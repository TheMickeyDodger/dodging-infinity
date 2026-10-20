"""The git transport seam: the Runtime's ONLY subprocess boundary.

The transport is a CONSTRUCTOR-INJECTED boundary object (plan D-6):
production entry points construct ``GitTransport()`` unconditionally
and expose NO override — no environment variable, no CLI flag, no
config key. An env value is adversarial input under the mission
rules, and a seam reachable in production would be a remote-code
path; the static suite proves no override exists.

Every argv is a fixed literal command plus values resolved from the
protected workflow record — never a caller-typed string, never a
shell. The verb set is READ/CLONE/CHECKOUT only: nothing here can
stage, create a revision, move a ref, or contact a remote to write.
``add_worktree`` (automatic Mission workspaces) is a CHECKOUT into a
new directory: detached at an existing commit and locked, so it
creates no branch and no revision, moves no shared ref and contacts
no remote; no verb here removes, moves or prunes a worktree.
"""

import contextlib
import hashlib
import os
import re
import select
import stat
import subprocess
import tempfile
import time

# Hard bounds for the streamed read captures (I1 of task
# 20260826-113247), module constants NEVER derived from input and
# never configurable (the static suite forbids env reads here):
#
# - MAX_DIFF_RETAINED_BYTES: how much diff TEXT is retained for
#   display. Retention truncation is honest: the result carries an
#   explicit ``truncated`` flag while ``total_bytes`` and ``digest``
#   stay EXACT over the whole stream (every byte is hashed as it
#   passes).
# - MAX_DIFF_TOTAL_BYTES: the hard ceiling on how much diff is read
#   AT ALL. Past it the capture REFUSES: no ``total_bytes``, no
#   ``digest`` — a partial digest or a lower bound under an
#   exact-count name is the recorded "silent truncation presented as
#   fact" class, so the refusal shape carries only
#   ``total_bytes_lower_bound``.
# - MAX_PORCELAIN_CAPTURE_BYTES: single bound for porcelain capture.
#   Exact per-entry counts require the WHOLE output, so any
#   truncation here is a refusal, never a partial parse.
MAX_DIFF_RETAINED_BYTES = 65536
MAX_DIFF_TOTAL_BYTES = 33554432
MAX_PORCELAIN_CAPTURE_BYTES = 1048576

_STREAM_CHUNK_BYTES = 65536

# Item 3 (original 20, live code review): bounds for the reads that point at
# a LIVE agent workspace (see "NON-MUTATING reads of a LIVE workspace"
# below). Module constants, never derived from input, never configurable.
#
# - MAX_REVIEW_GIT_SECONDS: the wall-clock deadline of ONE live read. Past it
#   the process is killed and the capture REFUSES (``refused_deadline``);
#   the older streamed captures have no deadline by design, a live read does.
# - MAX_REVIEW_INVENTORY_BYTES: the whole output of one inventory capture (the
#   status listing, the per-file counts). Exact counts need the WHOLE
#   output, so an over-bound capture is a refusal, never a partial parse.
# - MAX_REVIEW_FILE_DIFF_BYTES: the most diff bytes read for ONE file. Past
#   it the capture refuses with ``total_bytes_lower_bound`` only.
MAX_REVIEW_GIT_SECONDS = 20
MAX_REVIEW_INVENTORY_BYTES = 1048576
MAX_REVIEW_FILE_DIFF_BYTES = 4194304
# - MAX_REVIEW_FILTER_DRIVERS: how many content-filter drivers a repository's
#   configuration may name for a live read to proceed. They are listed so the
#   review can SAY they were not run (no read ever runs one: see "LIVE
#   reads" below); past the bound the read refuses rather than list an
#   unbounded set.
# - MAX_REVIEW_INDEX_BYTES: the most of the target's index a live read will
#   COPY into the private git directory it reads through. Past it the read
#   REFUSES (``GitViewRefused``, carrying the exact size).
# - MAX_REVIEW_ATTRIBUTE_FILE_BYTES: the same bound for each of the
#   repository's ``info/exclude`` and ``info/attributes`` that are copied.
MAX_REVIEW_FILTER_DRIVERS = 16
MAX_REVIEW_INDEX_BYTES = 67108864
MAX_REVIEW_ATTRIBUTE_FILE_BYTES = 1048576
# - MAX_REVIEW_NAMES_BYTES: the whole output of the listing of every tracked
#   NAME (``ls-files -z``), which is what decides which locations are private
#   BEFORE any read that could hash a file. Past it the read refuses.
# - MAX_REVIEW_EXCLUSIONS / MAX_REVIEW_EXCLUSION_BYTES: how many private
#   locations, and how many bytes of them, one read is told to leave out
#   (each becomes one pathspec on git's command line, so both are finite).
#   Past either the read refuses rather than run without the exclusion.
#   MAX_REVIEW_INDEX_RECORD_BYTES: the whole output of the index's own record of
#   the private locations' entries (``ls-files --debug``: each entry carries its
#   recorded times and size, so it is far larger than its name). It is not the
#   changed-listing's 1 MiB: a private location may hold many UNCHANGED tracked
#   files, and refusing the review for that would be an availability loss the
#   old read (which listed only what had changed) did not have.
MAX_REVIEW_NAMES_BYTES = 16777216
MAX_REVIEW_INDEX_RECORD_BYTES = 16777216
MAX_REVIEW_EXCLUSIONS = 2048
MAX_REVIEW_EXCLUSION_BYTES = 262144

CAPTURE_CAPTURED = "captured"
CAPTURE_REFUSED_OVER_BOUND = "refused_over_bound"
CAPTURE_REFUSED_DEADLINE = "refused_deadline"

# The environment of a live read, EXPLICIT: nothing is inherited, so no
# ``GIT_*`` variable of the parent (a directory, an index file, an external
# diff program, a config source) can steer a read of someone else's
# repository, and no user or system configuration changes what it prints.
# The PATH is fixed: a driver the repository configures is found there or not
# at all.
_LIVE_READ_ENV = {
    "PATH": "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin",
    "LC_ALL": "C",
    "GIT_TERMINAL_PROMPT": "0",
    "GIT_OPTIONAL_LOCKS": "0",
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_CONFIG_GLOBAL": "/dev/null",
}
# Pinned in the argv of every live read, before the verb (the same idea as
# ``--no-ext-diff``/``--no-textconv``: the repository's own configuration
# must not decide how it is read): no index refresh through an optional
# lock, pathspecs are literal text, no filesystem-monitor program runs,
# paths are quoted the same way whatever the operator's git config says, and
# no colour or diff-prefix setting changes the shape of the output.
_LIVE_READ_PINS = (
    "--no-optional-locks", "--literal-pathspecs",
    "-c", "core.fsmonitor=false", "-c", "core.quotePath=true",
    "-c", "color.ui=false", "-c", "diff.noprefix=false",
    "-c", "diff.mnemonicPrefix=false",
)

# The same pins WITHOUT ``--literal-pathspecs``: the reads that take pathspec
# MAGIC (leave these locations out) need it on, so every pathspec such a read
# carries spells its own magic out (``:(top,literal,...)``) or is ``.``.
_LIVE_MAGIC_PINS = tuple(pin for pin in _LIVE_READ_PINS
                         if pin != "--literal-pathspecs")


class GitTransportError(Exception):
    """A git operation failed or returned unusable output."""


class GitViewRefused(GitTransportError):
    """A live read's private git directory could not be prepared inside a
    bound: ``what`` (the file), its exact size ``observed`` and the ``bound``."""

    def __init__(self, what, observed, bound):
        GitTransportError.__init__(
            self, "%s is %d bytes, more than the %d a read will copy"
            % (what, observed, bound))
        self.what, self.observed, self.bound = what, observed, bound


class GitBoundRefused(GitViewRefused):
    """A read was asked to leave out (or take) more than a bound allows."""

    def __init__(self, what, observed, bound):
        GitTransportError.__init__(
            self, "%s is %d, more than the %d a read will take"
            % (what, observed, bound))
        self.what, self.observed, self.bound = what, observed, bound


def _location(prefix):
    """``prefix`` as a contained, relative, non-empty path, or an error: it
    becomes a pathspec, and ``.`` or an empty one would mean EVERYTHING."""
    if type(prefix) is not str or not prefix or "\x00" in prefix or (
            prefix.startswith("/")) or any(
                part in ("", ".", "..") for part in prefix.split("/")):
        raise GitTransportError("a location is not a contained relative path")
    return prefix


def _locations(prefixes):
    """The bounded, validated tuple of ``prefixes``."""
    prefixes = tuple(_location(p) for p in prefixes)
    if len(prefixes) > MAX_REVIEW_EXCLUSIONS:
        raise GitBoundRefused("the private locations", len(prefixes),
                              MAX_REVIEW_EXCLUSIONS)
    size = sum(len(os.fsencode(p)) for p in prefixes)
    if size > MAX_REVIEW_EXCLUSION_BYTES:
        raise GitBoundRefused("the private locations' bytes", size,
                              MAX_REVIEW_EXCLUSION_BYTES)
    return prefixes


def _exclusion_tail(exclude):
    """The pathspecs that make a read LEAVE OUT every ``exclude`` location:
    git then never visits, stats or hashes anything under one (``.`` is
    everything else). Each is spelled ``:(top,literal,exclude)`` so a name is
    text, never a pattern. Nothing to leave out: no pathspec at all."""
    exclude = _locations(exclude)
    if not exclude:
        return ()
    return ("--", ".") + tuple(":(top,literal,exclude)" + p for p in exclude)


def _inside(path, prefixes):
    """Whether ``path`` is one of ``prefixes`` or below one (any letter case:
    a case-insensitive volume names one file two ways)."""
    folded = path.casefold()
    return any(folded == p.casefold() or folded.startswith(p.casefold() + "/")
               for p in prefixes)


_BOOLEAN = re.compile(r"\A(?:true|false|yes|no|on|off|0|1)\Z", re.IGNORECASE)

#: The ONLY settings a live read carries over from the repository's own
#: configuration into the private one it reads through: scalars that change
#: what a listing or a diff SAYS (a file mode, a line ending, a stat check),
#: each with the exact shape it must have. A value of another shape refuses
#: the read (it is not guessed at); no other key is read, so nothing that
#: names a PROGRAM (a filter, a textconv, an external diff, a monitor, a
#: pager, an editor, a hook directory) can come with it.
_VIEW_CORE_KEYS = {
    "filemode": _BOOLEAN, "symlinks": _BOOLEAN, "ignorecase": _BOOLEAN,
    "precomposeunicode": _BOOLEAN, "trustctime": _BOOLEAN,
    "sparsecheckout": _BOOLEAN,
    "autocrlf": re.compile(r"\A(?:true|false|yes|no|on|off|0|1|input)\Z",
                           re.IGNORECASE),
    "eol": re.compile(r"\A(?:lf|crlf|native)\Z", re.IGNORECASE),
    "checkstat": re.compile(r"\A(?:default|minimal)\Z", re.IGNORECASE),
    "bigfilethreshold": re.compile(r"\A[0-9]{1,12}[kmg]?\Z", re.IGNORECASE),
}
_VIEW_CORE_PATTERN = "^core[.](%s)$" % "|".join(sorted(_VIEW_CORE_KEYS))


def _copy_regular(source, target, bound, what, strict):
    """Copy ``source`` (a regular file, never followed through a symlink) to
    ``target``, at most ``bound`` bytes: the sha256 of what was copied (a
    truthy string). An ABSENT file
    is ``False``; so is one that is not a regular file (a FIFO, a device, a
    directory: never waited for, never read) unless ``strict`` (then it is a
    transport error). Past the bound: ``GitViewRefused``."""
    try:
        # O_NONBLOCK is what lets the regular-file guard below RUN: opening a
        # FIFO for reading otherwise waits for a writer for as long as it
        # takes, in Python, outside the git deadline (O_NOFOLLOW refuses a
        # symlink, not a FIFO). It changes nothing for a regular file.
        fd = os.open(source, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        return False
    except OSError:
        if strict:
            raise GitTransportError("%s cannot be read as a regular file" % what)
        return False
    with os.fdopen(fd, "rb") as handle:
        info = os.fstat(handle.fileno())
        if not stat.S_ISREG(info.st_mode):
            if strict:
                raise GitTransportError("%s is not a regular file" % what)
            return False
        if info.st_size > bound:
            raise GitViewRefused(what, info.st_size, bound)
        data = handle.read(bound + 1)
    if len(data) > bound:
        raise GitViewRefused(what, len(data), bound)
    with open(target, "wb") as out:
        out.write(data)
    # The copy keeps the original's modification time. Git decides whether a
    # file's recorded stat can be trusted by comparing it with the INDEX
    # FILE'S OWN mtime (a change made in the same instant leaves the stat
    # unchanged, so such an entry is "racily clean" and its content is
    # compared instead): a copy stamped with the time it was made would make
    # every entry look old enough to trust, and a same-size edit made just
    # after a commit would be missed.
    os.utime(target, ns=(info.st_atime_ns, info.st_mtime_ns))
    return hashlib.sha256(data).hexdigest()


def _digest_of(path):
    """sha256 of the file at ``path``, or None when there is none."""
    try:
        with open(path, "rb") as handle:
            return hashlib.sha256(handle.read()).hexdigest()
    except FileNotFoundError:
        return None


class LiveView(object):
    """One private git directory's handle: the revision it compares against
    (``oid``), the environment that points git at it (``env``) and the digest of
    the private INDEX it was made with (``index_digest``). The index is the
    single thing every decision of an observation rests on: which locations
    are private, where a private path came from, which files a diff may name,
    and what every read that could hash a file is told to leave out. It is a
    copy, made once, read-only, and ``verify`` says whether it is still exactly
    that copy: a read made through it can only ever have seen THAT index."""

    def __init__(self, oid, env):
        self.oid, self.env = oid, env
        self.index_digest = _digest_of(os.path.join(env["GIT_DIR"], "index"))

    def verify(self):
        if _digest_of(os.path.join(self.env["GIT_DIR"], "index")) != (
                self.index_digest):
            raise GitTransportError(
                "the private index changed while it was read through")


def _terminate_and_reap(process):
    """Terminate and reap a LIVE read's child, within a bound, and NEVER raise.

    It runs because something else is already failing, so what was already on
    its way out must be what propagates: nothing here may replace it. Each
    attempt signals the child and waits a quarter of a second for it; a signal
    that cannot be sent, a child that is not gone yet, and a further
    interruption (a watchdog that re-arms) do not end the effort. The attempts
    are COUNTED, not timed (so nothing in here depends on a clock), enough of
    them to cover one more ``MAX_REVIEW_GIT_SECONDS``. Returns whether the
    child was reaped."""
    reaped = False
    for _attempt in range(int(MAX_REVIEW_GIT_SECONDS * 4) + 1):
        try:
            try:
                process.kill()
            except Exception:
                pass        # the wait below decides whether the child is gone
            process.wait(timeout=0.25)
            reaped = True
            break
        except BaseException:
            pass
    try:
        process.stdout.close()
    except BaseException:
        pass
    return reaped


def _read_live(argv, spawn, retain_bytes, ceiling_bytes):
    """The WHOLE life of a LIVE streamed read's child, from the moment
    ``Popen`` has succeeded: the ``Popen`` itself is the first statement of
    the protected block, so no statement lies between the child's existence
    and its ownership.

    The child is OWNED from there: every statement that follows is inside that
    ONE protected block, so a failure ANYWHERE (the pipe cannot be waited on
    or read, the digest cannot be created, the read is interrupted, the wait
    after the read is interrupted, the ending itself fails) terminates and
    reaps the child (``_terminate_and_reap``) and then propagates UNCHANGED,
    whatever was already propagating staying what propagates. On every path
    the child is terminated and reaped within a bound and nothing waits
    without one. There is nothing to cancel or close AFTER the handler: the
    deadline is the read's own, not a timer's, and the poller holds no
    descriptor, so no cleanup statement can replace the exception in flight.

    THE CAPTURE'S I/O IS BOUNDED BY THE CLOCK, NOT BY THE END OF THE STREAM.
    The pipe is waited on (``poll``) for at most the time left to the
    deadline, and read only when it is readable, so a read can never block
    past the deadline: not for a child that is silent, and not for a pipe a
    DESCENDANT of the child keeps open (killing the child does not close it,
    and git is not assumed to have no helpers). Past the deadline the read
    stops AT ONCE, whatever the pipe holds: the child is killed and reaped,
    this end of the pipe is closed, and the capture REFUSES as past its
    deadline. A pipe's end is not the process's either: when the stream ends
    the child is waited for only until the same deadline, and a child that
    closed its output and did not exit is killed there and refused alike.
    Past the ceiling the child is killed at once. A killed child is reaped in
    moments; the bound on that is one more ``MAX_REVIEW_GIT_SECONDS``.

    Returns ``(total, retained, hasher, over, expired, returncode)``."""
    process = None
    expired = []
    try:
        process = subprocess.Popen(
            argv, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, **spawn
        )
        deadline = time.monotonic() + MAX_REVIEW_GIT_SECONDS
        descriptor = process.stdout.fileno()
        poller = select.poll()
        poller.register(descriptor, select.POLLIN)
        hasher = hashlib.sha256()
        retained = bytearray()
        total = 0
        over = False
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                expired.append(True)
                break
            if not poller.poll(remaining * 1000.0):
                continue
            chunk = os.read(descriptor, _STREAM_CHUNK_BYTES)
            if not chunk:
                break
            total += len(chunk)
            if total > ceiling_bytes:
                over = True
                break
            hasher.update(chunk)
            if len(retained) < retain_bytes:
                room = retain_bytes - len(retained)
                retained.extend(chunk[:room])
        if over or expired:
            process.kill()
        process.stdout.close()
        if over or expired:
            patience = MAX_REVIEW_GIT_SECONDS
        else:
            patience = max(0.0, deadline - time.monotonic())
        try:
            returncode = process.wait(timeout=patience)
        except subprocess.TimeoutExpired:
            if over or expired:
                raise GitTransportError(
                    "a killed git process could not be reaped")
            expired.append(True)
            process.kill()
            try:
                returncode = process.wait(timeout=MAX_REVIEW_GIT_SECONDS)
            except subprocess.TimeoutExpired:
                raise GitTransportError(
                    "a killed git process could not be reaped")
    except BaseException:
        if process is not None:
            _terminate_and_reap(process)
        raise
    return total, retained, hasher, over, expired, returncode


class GitTransport(object):
    """Real git transport; hermetic tests inject a fake instead."""

    def _run(self, argv):
        completed = subprocess.run(argv, capture_output=True)
        if completed.returncode != 0:
            raise GitTransportError(
                "git operation failed (%d): %s"
                % (
                    completed.returncode,
                    (completed.stderr or b"").decode(
                        "utf-8", "replace"
                    ).strip()[:500],
                )
            )
        return (completed.stdout or b"").decode("utf-8", "replace")

    def clone(self, url, path):
        """Clone the canonical URL into the leased directory."""
        self._run(["git", "clone", "--quiet", "--", str(url),
                   str(path)])

    def remote_url(self, path):
        return self._run(
            ["git", "-C", str(path), "remote", "get-url", "origin"]
        ).strip()

    def head_commit(self, path):
        return self._run(
            ["git", "-C", str(path), "rev-parse", "HEAD"]
        ).strip()

    def checkout_detached(self, path, commit_sha):
        self._run(
            ["git", "-C", str(path), "checkout", "--quiet",
             "--detach", str(commit_sha)]
        )

    def status_porcelain(self, path):
        return self._run(
            ["git", "-C", str(path), "status", "--porcelain"]
        )

    # -- automatic Mission workspaces (local repository only) ----------

    def toplevel(self, path):
        return self._run(
            ["git", "-C", str(path), "rev-parse", "--show-toplevel"]
        ).strip()

    def common_dir(self, path):
        """The repository's shared Git directory, absolute: the same for
        every worktree of one repository."""
        return self._run(
            ["git", "-C", str(path), "rev-parse", "--path-format=absolute",
             "--git-common-dir"]
        ).strip()

    def worktree_list(self, repository):
        """``git worktree list --porcelain -z``, unparsed: NUL-separated
        fields, so no path or lock reason can forge a record."""
        return self._run(
            ["git", "-C", str(repository), "worktree", "list",
             "--porcelain", "-z"]
        )

    def add_worktree(self, repository, path, commit_sha, lock_reason):
        """Check ``commit_sha`` out, detached, into the NEW directory
        ``path`` as a worktree of ``repository``, locked with
        ``lock_reason`` from its first moment (so ``git worktree prune``
        never removes it). Repository hooks are disabled for this one
        invocation: an unattended preparation runs no hook."""
        self._run(
            ["git", "-c", "core.hooksPath=/dev/null", "-C", str(repository),
             "worktree", "add", "--quiet", "--detach", "--lock", "--reason",
             str(lock_reason), "--", str(path), str(commit_sha)]
        )

    # -- streamed, bounded READ captures (evidence layer) --------------

    def _stream(self, argv, retain_bytes, ceiling_bytes, live=False,
                accept=(0,), env=None):
        """Run one read-only git command, streaming its stdout.

        Every byte that arrives is fed to a running sha256 and
        counted; only the first ``retain_bytes`` are kept in memory.
        If the process emits more than ``ceiling_bytes`` the capture
        REFUSES: the process is killed, and the result carries ONLY
        the refusal status and ``total_bytes_lower_bound`` (the bytes
        actually observed — more may exist). No digest and no
        exact-named total are ever emitted for a refused capture: a
        partial digest is meaningless and a lower bound under an
        exact-count field name would be a lie.

        stderr goes to DEVNULL deliberately: this path has no
        deadline by design, and reading two pipes without one risks a
        blocked-writer deadlock on hostile unbounded stderr. A failed
        command therefore reports its exit status, not its message.

        ``live`` is the read of a LIVE observed workspace (item 3): the
        child gets the explicit ``_LIVE_READ_ENV`` (or ``env``, which a
        caller builds FROM it) and no stdin, and runs
        under a wall-clock deadline of ``MAX_REVIEW_GIT_SECONDS`` — past it
        the process is killed and the capture refuses with
        ``refused_deadline`` (and ``total_bytes_lower_bound`` only, for the
        same reason as over the ceiling). Every other caller is unchanged.

        A LIVE read OWNS its child from the moment ``Popen`` has succeeded
        (``_read_live``: the ``Popen`` is the first statement of its
        protected block): on EVERY exit path, a normal return, a ceiling
        refusal, a deadline, a pipe that ends before the process does, and a
        failure anywhere (the capture's I/O, the read, the wait after it, the
        cleanup itself) with a ``BaseException`` included, the child is
        terminated and reaped within a bound, and the exception already on
        its way out is the one that propagates. Its I/O is bounded by the
        deadline itself, not by the end of the stream: a descendant that keeps
        the output open cannot hold the read past it. The non-live path is
        exactly as it was.
        """
        spawn = {}
        if live:
            spawn = {"stdin": subprocess.DEVNULL,
                     "env": _LIVE_READ_ENV if env is None else env}
            total, retained, hasher, over, expired, returncode = _read_live(
                argv, spawn, retain_bytes, ceiling_bytes)
        else:
            process = subprocess.Popen(
                argv, stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL, **spawn
            )
            expired = []
            hasher = hashlib.sha256()
            retained = bytearray()
            total = 0
            over = False
            try:
                while True:
                    chunk = process.stdout.read(_STREAM_CHUNK_BYTES)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > ceiling_bytes:
                        over = True
                        break
                    hasher.update(chunk)
                    if len(retained) < retain_bytes:
                        room = retain_bytes - len(retained)
                        retained.extend(chunk[:room])
            finally:
                if over:
                    process.kill()
                process.stdout.close()
                returncode = process.wait()
        if expired:
            return {
                "status": CAPTURE_REFUSED_DEADLINE,
                "total_bytes_lower_bound": total,
            }
        if over:
            return {
                "status": CAPTURE_REFUSED_OVER_BOUND,
                "total_bytes_lower_bound": total,
            }
        if returncode not in accept:
            raise GitTransportError(
                "git operation failed (%d) during a streamed read"
                " capture" % returncode
            )
        return {
            "status": CAPTURE_CAPTURED,
            "total_bytes": total,
            "digest": hasher.hexdigest(),
            "retained": bytes(retained),
            "truncated": total > retain_bytes,
        }

    def diff_head(self, path):
        """Streamed, bounded capture of the tracked diff against HEAD
        (staged + unstaged; untracked files are the porcelain
        inventory's job).

        Returns, on capture: ``status`` ``captured``,
        ``retained_bytes`` (exact, of what is retained),
        ``retained_text`` (display rendering of the retained bytes;
        ``retained_text_lossy`` is True when they did not decode as
        strict UTF-8 — a flagged display artifact, never presented as
        the diff), ``truncated`` (retention truncation flag),
        ``total_bytes`` (EXACT, whole diff) and ``digest`` (sha256 of
        the WHOLE diff, exact). Over the hard ceiling: ``status``
        ``refused_over_bound`` with ``total_bytes_lower_bound`` only.

        ``--no-ext-diff``/``--no-textconv`` pin git to its internal
        diff machinery: repository-authored attributes must never
        select a driver for this read. Mutation posture, MEASURED
        (git 2.39): ``git diff`` refreshes the index stat cache even
        under ``--no-optional-locks``, so this verb can rewrite
        ``.git/index`` metadata of the repository it reads — it is
        therefore only ever pointed at the DISPOSABLE leased
        workspace, never the control repository (the evidence layer
        pins that by construction and by test). Worktree content,
        refs, and objects are untouched.
        """
        result = self._stream(
            ["git", "--no-optional-locks", "-C", str(path), "diff",
             "--no-color", "--no-ext-diff", "--no-textconv", "HEAD"],
            MAX_DIFF_RETAINED_BYTES, MAX_DIFF_TOTAL_BYTES,
        )
        if result["status"] != CAPTURE_CAPTURED:
            return result
        retained = result["retained"]
        try:
            text = retained.decode("utf-8")
            lossy = False
        except UnicodeDecodeError:
            text = retained.decode("utf-8", "replace")
            lossy = True
        return {
            "status": CAPTURE_CAPTURED,
            "retained_bytes": len(retained),
            "retained_text": text,
            "retained_text_lossy": lossy,
            "truncated": result["truncated"],
            "total_bytes": result["total_bytes"],
            "digest": result["digest"],
        }

    def status_porcelain_readonly(self, path):
        """Bounded porcelain capture, provably non-mutating.

        Unlike ``status_porcelain`` this variant takes
        ``--no-optional-locks`` (no ``.git/index`` refresh — required
        because evidence collection also reads the CONTROL worktree)
        and refuses instead of truncating: exact per-entry counts
        need the WHOLE output, so an over-bound capture returns
        ``status`` ``refused_over_bound`` with
        ``total_bytes_lower_bound`` and NO text. On capture:
        ``status`` ``captured``, ``text`` (strict UTF-8) and
        ``total_bytes`` (exact). ``-c core.quotePath=true`` PINS the
        quoting behaviour in the argv (round-01 F-2) exactly as
        ``--no-ext-diff``/``--no-textconv``/``--no-optional-locks``
        pin theirs: unusual path bytes — non-ASCII line separators
        like U+2028/U+0085 included — are always emitted
        quoted-and-escaped to ASCII, REGARDLESS of the operator's
        git config, so per-line entry counting over this output is
        exact and the strict-UTF-8 decode is guaranteed rather than
        merely usual (a non-decodable capture is still refused as a
        transport error rather than lossily parsed).
        """
        result = self._stream(
            ["git", "--no-optional-locks", "-c",
             "core.quotePath=true", "-C", str(path), "status",
             "--porcelain"],
            MAX_PORCELAIN_CAPTURE_BYTES, MAX_PORCELAIN_CAPTURE_BYTES,
        )
        if result["status"] != CAPTURE_CAPTURED:
            return {
                "status": CAPTURE_REFUSED_OVER_BOUND,
                "total_bytes_lower_bound": result[
                    "total_bytes_lower_bound"
                ],
            }
        try:
            text = result["retained"].decode("utf-8")
        except UnicodeDecodeError:
            raise GitTransportError(
                "porcelain capture is not valid UTF-8; refusing to"
                " parse it lossily"
            )
        return {
            "status": CAPTURE_CAPTURED,
            "text": text,
            "total_bytes": result["total_bytes"],
        }

    # -- NON-MUTATING reads of a LIVE workspace (item 3, original 20) ----
    #
    # The target of a code review is an agent's REAL workspace, not a
    # disposable lease: nothing here may write to it, and nothing here may
    # RUN anything the target's own configuration names. Both are MEASURED
    # bars, not arguments (git 2.39.5, a stat-dirty index, the whole ``.git``
    # digested before and after: tests/test_code_review_git.py; the
    # measurements themselves are kept in the item-3 evidence directory):
    #
    # - ``git diff`` REWRITES ``.git/index`` — with or without
    #   ``--no-optional-locks`` (see ``diff_head``) — so it is never used
    #   here;
    # - ``git status`` rewrites it too unless ``--no-optional-locks`` is
    #   given, which every live read pins;
    # - the PLUMBING verbs below (``diff-index``, ``rev-parse``) leave the
    #   whole ``.git`` byte-identical, ``diff-index -p HEAD`` printing the
    #   same staged-plus-unstaged diff against HEAD that ``git diff HEAD``
    #   does (a file only touched, its content unchanged, is omitted).
    #
    # LIVE reads. Git runs a content filter's CLEAN program whenever it
    # re-hashes a file whose stat no longer matches the index (a touched
    # file, a checkout), and has no switch that turns every filter off —
    # only an override per NAME, and a name that appears in the repository's
    # configuration after it was enumerated is not covered. So a read that
    # can hash a working file (the listing, the counts, a file's diff) never
    # reads the repository's configuration at all: it runs in a git
    # directory of ITS OWN making (``_live_view``) holding a configuration
    # the read wrote, a detached HEAD, a COPY of the index, and the target's
    # object store BORROWED through ``objects/info/alternates`` (so even an
    # object git were to write lands in the private directory). A filter
    # driver is a name in a configuration; there is none to find. The few
    # settings that change what a listing says (file modes, line endings)
    # are carried over from an allow-list with exact shapes
    # (``_VIEW_CORE_KEYS``). The repository's ``info/exclude`` and
    # ``info/attributes`` are copied as DATA (patterns; no program). The
    # verbs that still read the real configuration (``rev-parse``,
    # ``config --get-regexp``) execute nothing it names: measured, with a
    # marker program behind every program-valued key.
    # The target's ``.git`` is only READ (the index and the two info files
    # are opened without following a symlink); the copy is the only write,
    # and it is to a scratch directory removed when the read ends.
    # Every argv is a fixed literal verb plus values the caller resolved from
    # the canonical record (the workspace) or from git's own listing (a path
    # after ``--`` under ``--literal-pathspecs``), never a shell.

    def _live_capture(self, path, verb, ceiling, accept=(0,), env=None,
                      magic=False):
        """One live read, the WHOLE output held (``ceiling`` bytes at most):
        ``status`` ``captured`` with ``data`` (bytes), ``total_bytes`` and
        ``digest``; or a refusal carrying ``total_bytes_lower_bound`` only.
        ``env`` is the environment of a read through a private git
        directory (``_live_view``); without it the read uses the
        repository's own. ``magic`` is for a verb whose pathspecs spell their
        own magic out (the exclusion tail): the literal-pathspecs pin is
        then left off, and ONLY then."""
        pins = _LIVE_MAGIC_PINS if magic else _LIVE_READ_PINS
        result = self._stream(
            ["git"] + list(pins) + ["-C", str(path)] + list(verb),
            ceiling, ceiling, live=True, accept=accept, env=env)
        if result["status"] != CAPTURE_CAPTURED:
            return result
        return {
            "status": CAPTURE_CAPTURED,
            "data": result["retained"],
            "total_bytes": result["total_bytes"],
            "digest": result["digest"],
        }

    def _live_text(self, path, verb):
        """A short live answer as text; a refusal is a transport error."""
        result = self._live_capture(path, verb, 4096)
        if result["status"] != CAPTURE_CAPTURED:
            raise GitTransportError(
                "a live read was refused (%s)" % result["status"])
        try:
            return result["data"].decode("utf-8").rstrip("\n")
        except UnicodeDecodeError:
            raise GitTransportError("a live read is not valid UTF-8")

    @contextlib.contextmanager
    def _live_view(self, path):
        """The git directory a content-reading live read goes through:
        yields ``(oid, env)`` — the commit HEAD names in ``path`` and the
        environment that points git at a private directory (see "LIVE
        reads" above). The directory is removed when the read ends."""
        oid = self._live_text(path, ["rev-parse", "--verify", "HEAD^{commit}"])
        where = self._live_text(path, [
            "rev-parse", "--path-format=absolute", "--absolute-git-dir",
            "--git-common-dir", "--show-object-format"]).split("\n")
        if len(where) != 3 or not all(where):
            raise GitTransportError("git does not say where its directories are")
        git_dir, common_dir, object_format = where
        if object_format != "sha1" or not os.path.isabs(git_dir) or (
                not os.path.isabs(common_dir)):
            raise GitTransportError(
                "only an ordinary (sha1) repository can be read")
        kept = self._live_capture(
            path, ["config", "-z", "--get-regexp", _VIEW_CORE_PATTERN], 65536,
            accept=(0, 1))
        if kept["status"] != CAPTURE_CAPTURED:
            raise GitTransportError("the core configuration is too large")
        lines = ["[core]", "\trepositoryformatversion = 0"]
        for record in kept["data"].split(b"\x00"):
            if not record:
                continue
            try:
                text = record.decode("utf-8")
            except UnicodeDecodeError:
                raise GitTransportError("a core setting is not valid UTF-8")
            key, separator, value = text.partition("\n")
            variable = key.split(".", 1)[1] if key.startswith("core.") else ""
            shape = _VIEW_CORE_KEYS.get(variable)
            if shape is None:
                raise GitTransportError("git returned a setting that was not asked for")
            if not separator:
                value = "true"          # a bare ``[core] filemode`` means true
            if not shape.match(value):
                raise GitTransportError(
                    "core.%s has a value a read cannot reproduce" % variable)
            lines.append("\t%s = %s" % (variable, value))
        with tempfile.TemporaryDirectory(prefix="di-review-") as scratch:
            for directory in ("refs/heads", "refs/tags", "objects/info", "info"):
                os.makedirs(os.path.join(scratch, *directory.split("/")))
            with open(os.path.join(scratch, "config"), "w") as handle:
                handle.write("\n".join(lines) + "\n")
            with open(os.path.join(scratch, "HEAD"), "w") as handle:
                handle.write(oid + "\n")
            with open(os.path.join(scratch, "objects", "info", "alternates"),
                      "w") as handle:
                handle.write(os.path.join(common_dir, "objects") + "\n")
            for name in ("exclude", "attributes"):
                _copy_regular(
                    os.path.join(common_dir, "info", name),
                    os.path.join(scratch, "info", name),
                    MAX_REVIEW_ATTRIBUTE_FILE_BYTES, "info/" + name, False)
            if _copy_regular(
                    os.path.join(git_dir, "index"),
                    os.path.join(scratch, "index"), MAX_REVIEW_INDEX_BYTES,
                    "the index", True):
                os.chmod(os.path.join(scratch, "index"), 0o444)
            env = dict(_LIVE_READ_ENV)
            env["GIT_DIR"] = scratch
            env["GIT_WORK_TREE"] = str(path)
            yield oid, env

    def live_toplevel(self, path):
        """The worktree root git itself resolves for ``path``."""
        return self._live_text(path, ["rev-parse", "--show-toplevel"])

    def live_head(self, path):
        """``{"oid": <HEAD commit>, "ref": <full branch ref or None>}``:
        ``ref`` is None when HEAD is detached. An unborn branch has no
        commit to compare against and raises."""
        oid = self._live_text(
            path, ["rev-parse", "--verify", "HEAD^{commit}"])
        ref = self._live_text(path, ["rev-parse", "--symbolic-full-name",
                                     "HEAD"])
        return {"oid": oid, "ref": None if ref == "HEAD" else ref}

    def live_filter_drivers(self, path):
        """The content-filter drivers the repository's OWN configuration
        names, sorted: the names in ``filter.<name>.clean|smudge|process|
        required``. System and global configuration are excluded by the
        live environment, so these are the repository's (and its included
        files'). They are listed for DISCLOSURE only — the review says that
        a file such a driver manages is compared as stored. No live read
        runs one: the reads that could are made through a private git
        directory that holds no configuration of the repository's, so a
        driver added to it after this read is not a driver any read finds."""
        result = self._live_capture(
            path, ["config", "--name-only", "-z", "--get-regexp",
                   "^filter[.]"], 65536, accept=(0, 1))
        if result["status"] != CAPTURE_CAPTURED:
            raise GitTransportError("the filter configuration is too large")
        try:
            keys = [k.decode("utf-8") for k in result["data"].split(b"\x00")
                    if k]
        except UnicodeDecodeError:
            raise GitTransportError("a filter key is not valid UTF-8")
        names = set()
        for key in keys:
            body = key[len("filter."):]
            if "." in body and body.rsplit(".", 1)[1].lower() in (
                    "clean", "smudge", "process", "required"):
                names.add(body.rsplit(".", 1)[0])
        if len(names) > MAX_REVIEW_FILTER_DRIVERS:
            raise GitTransportError(
                "the repository defines more than %d content-filter drivers"
                % MAX_REVIEW_FILTER_DRIVERS)
        return sorted(names)

    @contextlib.contextmanager
    def live_view(self, path):
        """ONE private git directory for a whole observation: every read made
        through the yielded ``LiveView`` sees the SAME copy of the index, so
        what is decided private from its names is exactly what the reads that
        could hash a file are then told to leave out (not a later index, in
        which an agent may have staged one more). Removed when the block
        ends."""
        with self._live_view(path) as (oid, env):
            yield LiveView(oid, env)

    def _live_read(self, path, verb, ceiling, view=None, magic=False):
        """One content-reading live read through a private git directory (the
        ``view`` given, or one of its own). A captured result carries
        ``head``: the revision the read compared against, so callers can tell
        whether HEAD moved between two reads."""
        if view is None:
            with self.live_view(path) as own:
                return self._live_read(path, verb, ceiling, own, magic)
        result = self._live_capture(path, verb, ceiling, env=view.env,
                                    magic=magic)
        if result["status"] == CAPTURE_CAPTURED:
            result["head"] = view.oid
        return result

    def live_names(self, path, view=None):
        """Every tracked NAME, and what is staged against HEAD, from the
        index and the tree alone: ``ls-files -z`` and ``diff-index --cached
        --raw -z HEAD``. Neither opens a working file (``--cached`` never
        looks at the working tree; ``ls-files`` without ``-m``/``-o`` only
        lists the index), so this is the read that may precede the decision
        of which locations are private. Captured: ``names`` and ``staged``
        (bytes), ``total_bytes``, ``digest``, ``head``."""
        if view is None:
            with self.live_view(path) as own:
                return self.live_names(path, own)
        names = self._live_capture(path, ["ls-files", "-z"],
                                   MAX_REVIEW_NAMES_BYTES, env=view.env)
        if names["status"] != CAPTURE_CAPTURED:
            return names
        staged = self._live_capture(
            path, ["diff-index", "--cached", "--raw", "-z", "HEAD"],
            MAX_REVIEW_INVENTORY_BYTES, env=view.env)
        if staged["status"] != CAPTURE_CAPTURED:
            return staged
        return {
            "status": CAPTURE_CAPTURED, "names": names["data"],
            "staged": staged["data"],
            "total_bytes": names["total_bytes"] + staged["total_bytes"],
            "digest": hashlib.sha256(
                (names["digest"] + staged["digest"]).encode()).hexdigest(),
            "head": view.oid,
        }

    def live_index_stat(self, path, locations, view=None):
        """The index's own record of every tracked entry at the given
        ``locations`` (private ones): ``ls-files -z -s --debug`` (mode, blob,
        stage, and the time, inode and size the index recorded). It reads the
        INDEX and nothing else: git never looks at a working file for it, so it
        cannot hash one, however the recorded times compare with the index's
        own (the "racily clean" fallback is git's, and is not reached). The
        caller compares the record with the working tree's stat itself. Nothing
        to look at: nothing is run."""
        locations = _locations(locations)
        if view is None:
            with self.live_view(path) as own:
                return self.live_index_stat(path, locations, own)
        if not locations:
            return {"status": CAPTURE_CAPTURED, "data": b"", "total_bytes": 0,
                    "digest": hashlib.sha256(b"").hexdigest(),
                    "head": view.oid}
        result = self._live_capture(
            path, ["ls-files", "-z", "-s", "--debug", "--"]
            + [":(top,literal)" + p for p in locations],
            MAX_REVIEW_INDEX_RECORD_BYTES, env=view.env, magic=True)
        if result["status"] == CAPTURE_CAPTURED:
            result["head"] = view.oid
        return result

    # The four reads below can HASH or DIFF a working file (a stat-dirty
    # file is re-hashed by ``status``; ``diff-index`` hashes it to count or
    # print it). Each takes ``exclude``, REQUIRED (no default that would
    # quietly read everything): the private locations, which git is told to
    # leave out in its own pathspec, so it never visits or hashes anything
    # under one. ``()`` is "nothing private", stated, not assumed.

    def live_inventory(self, path, exclude, view=None):
        """Every changed path against the index and HEAD, plus every
        untracked file (not ignored ones): ``status --porcelain=v1 -z``.
        Renames are NOT paired here (``--no-renames``). A submodule whose
        recorded revision moved is listed; what is INSIDE a submodule is not
        looked at (``--ignore-submodules=dirty``: git does not enter it, so
        no program its configuration names can run — measured). Nothing
        under ``exclude`` is visited."""
        tail = _exclusion_tail(exclude)
        return self._live_read(
            path, ["status", "--porcelain=v1", "-z", "--no-renames",
                   "--untracked-files=all", "--ignore-submodules=dirty"]
            + list(tail), MAX_REVIEW_INVENTORY_BYTES, view, magic=bool(tail))

    def live_counts(self, path, exclude, view=None):
        """Per-file additions and deletions of the working tree against
        HEAD (staged and unstaged together), renames paired by git:
        ``diff-index --numstat -z -M HEAD``. Untracked files are not here.
        Nothing under ``exclude`` is hashed or diffed."""
        tail = _exclusion_tail(exclude)
        return self._live_read(
            path, ["diff-index", "--numstat", "-z", "-M",
                   "--ignore-submodules=dirty", "HEAD"] + list(tail),
            MAX_REVIEW_INVENTORY_BYTES, view, magic=bool(tail))

    def live_modes(self, path, exclude, view=None):
        """The file modes and change letters of every tracked change against
        HEAD: ``diff-index --raw -z -M HEAD``. Mode ``160000`` is a submodule
        (a recorded revision, not a file), which the listing says so about.
        Nothing under ``exclude`` is hashed."""
        tail = _exclusion_tail(exclude)
        return self._live_read(
            path, ["diff-index", "--raw", "-z", "-M",
                   "--ignore-submodules=dirty", "HEAD"] + list(tail),
            MAX_REVIEW_INVENTORY_BYTES, view, magic=bool(tail))

    def live_file_diff(self, path, relative_paths, exclude, view=None,
                       files_only=False):
        """The unified diff of ``relative_paths`` (one path, or a renamed
        file's old and new path) against HEAD: ``diff-index -p -M HEAD --``.
        ``--no-ext-diff``/``--no-textconv`` pin git to its internal diff
        machinery, as for ``diff_head``. A path under ``exclude`` is REFUSED
        before any git process starts: a private file is not diffed.

        ``files_only`` selects each path as a FILE and nothing else: the
        pathspec is that exact path (``:(top,literal)``) and every path below
        it is left out (``:(top,literal,exclude)<path>/``), so a path that is a
        directory in the index or in the base selects nothing, and a file that
        has become one is not expanded into what is under it. With ``view``
        the diff is made against THAT view's index and base, the ones the
        caller's decisions were made from."""
        exclude = _locations(exclude)
        relative_paths = [str(p) for p in relative_paths]
        if any(_inside(p, exclude) for p in relative_paths):
            raise GitTransportError(
                "a private location was asked for; it is not read")
        if files_only:
            relative_paths = _locations(relative_paths)
            spec = [":(top,literal)" + p for p in relative_paths] + [
                ":(top,literal,exclude)" + p + "/" for p in relative_paths]
        else:
            spec = relative_paths
        return self._live_read(
            path, ["diff-index", "-p", "-M", "--unified=3", "--no-color",
                   "--no-ext-diff", "--no-textconv",
                   "--ignore-submodules=dirty", "HEAD", "--"] + spec,
            MAX_REVIEW_FILE_DIFF_BYTES, view, magic=files_only)
