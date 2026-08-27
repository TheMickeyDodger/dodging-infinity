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
"""

import hashlib
import subprocess

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

CAPTURE_CAPTURED = "captured"
CAPTURE_REFUSED_OVER_BOUND = "refused_over_bound"


class GitTransportError(Exception):
    """A git operation failed or returned unusable output."""


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

    # -- streamed, bounded READ captures (evidence layer) --------------

    def _stream(self, argv, retain_bytes, ceiling_bytes):
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
        """
        process = subprocess.Popen(
            argv, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
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
        if over:
            return {
                "status": CAPTURE_REFUSED_OVER_BOUND,
                "total_bytes_lower_bound": total,
            }
        if returncode != 0:
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
