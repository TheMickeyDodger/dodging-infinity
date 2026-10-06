"""The write-capable delivery transport: git and gh, argv arrays only.

This is the ONLY module in ``pr_delivery`` that starts a process, and the
static suite pins that. It mirrors ``target_runtime/git_transport.py``'s
discipline: the real transport is a CONSTRUCTOR-INJECTED boundary object
built with zero arguments in exactly one production place (``cli.py``);
there is no environment variable, CLI flag, or config key that selects
an implementation; every argv is a fixed literal command plus values
resolved from the validated authority record; there is never a shell.
``argv[0]`` is the bare ``git``/``gh`` resolved through PATH exactly as
the read-only transport resolves ``git`` — no absolute binary path is
hard-coded.

The verb set is CLOSED (``ALLOWED_GIT_VERBS`` / ``ALLOWED_GH_ARGV``,
pinned by value): read verbs, ``fetch`` of one ref, the two-way
``read-tree``, a compare-and-swap ``update-ref`` (the only ref move —
no reset, rebase, checkout, or branch force exists here), one ``commit``
whose identity and ``gpgsign=false`` ride the argv, one ``push`` of
``src:dst`` with no force parameter, and ``gh pr list/create/view`` plus
``gh api --method GET`` against two literal endpoint templates. No
merge, review, ready, close, label, release, or delete verb exists.

Credentials: none are read, stored, printed, or logged. ``gh`` uses its
own keychain-backed login; this module never sees a token and never
passes one. Authorization decisions live entirely outside this module.

Deadline posture: like the read-only transport, every child runs with
NO deadline (an engineering verification can legitimately take a long
time and a timeout would be a silent truncation of evidence). stdout is
streamed and bounded by ``MAX_TRANSPORT_OUTPUT_BYTES`` (over the bound
the child is killed and the call REFUSES rather than parsing a partial
output); stderr goes to a temporary FILE, never a second pipe, so a
hostile unbounded stderr cannot deadlock a deadline-free read.

``run_reverification`` (Lead M4) executes the human-bound argv recorded
in the immutable authority half. It is the widest verb here and it is
handled with the same rules: an argv list of strings, no shell, no
interpolation, cwd is the authorized repository root, output bounded.
A non-zero exit is reported to the machine, which records the named
problem ``pr_delivery_reverification_failed`` and blocks durably.

OWNED EFFECT CHILDREN (Task 8 S-VI, R2-8). Every EFFECT child — the git
effect verbs (``EFFECT_GIT_VERBS``), ``gh pr create`` and the
reverification argv — started while the machine has named its owner
(``effect_owner``) and bound a child ledger (``bind_child_ledger``) runs in
its OWN session (one process group: its whole tree is reachable through
one id) and is recorded in an fsynced per-delivery ledger: an INTENT row
before the spawn, the group id right after it, and a SETTLED row only when
``killpg(group, 0)`` proves the group empty after the leader exited.
``unsettled_children`` is what the machine consults before every effect:
a group still alive, or an intent whose group id was never recorded (a
crash inside the spawn window), refuses the next effect — settlement is
PROVEN before any retry and nothing here ever kills a process, so an
unattributed one is never touched. The limits, stated: a descendant that
leaves its group (its own ``setsid``) is outside what the group proves;
and an intent-only row (crash between the intent and the group id) stays
unresolved until a human resolves it — refusal, never a guess.
"""

import hashlib
import json
import os
import secrets
import subprocess
import tempfile
import time

from pr_delivery.errors import DeliveryTransportError

# Hard bound on captured child output, never derived from input.
MAX_TRANSPORT_OUTPUT_BYTES = 1048576
_STREAM_CHUNK_BYTES = 65536
_STDERR_RETAINED_BYTES = 4000

ALLOWED_GIT_VERBS = (
    "rev-parse", "symbolic-ref", "config", "remote", "status", "diff",
    "diff-index", "diff-tree", "write-tree", "ls-remote", "fetch",
    "merge-base", "update-index", "read-tree", "update-ref", "commit",
    "push",
)
ALLOWED_GH_ARGV = (
    ("pr", "list"), ("pr", "create"), ("pr", "view"),
    ("api", "--method", "GET"),
)
CHECK_RUNS_ENDPOINT = "repos/%s/%s/commits/%s/check-runs"

# Task 8 S-VI: the git verbs that CHANGE something (objects, the index, a
# ref, a remote); each child running one is an OWNED effect child. The
# MUTATING form of ``symbolic-ref`` (``symbolic-ref HEAD <ref>``: the
# Mission-bound preparation's HEAD move) is an effect form of its own; the
# READ-ONLY ``symbolic-ref -q HEAD`` observation never is (``_effect_form``).
SYMBOLIC_REF_SET = "symbolic-ref-set"
EFFECT_GIT_VERBS = ("fetch", "update-index", "read-tree", "update-ref",
                    "commit", "push", "write-tree", SYMBOLIC_REF_SET)
CHILD_LEDGER_DIR_NAME = "pr_delivery-children"
CHILD_UNSETTLED = "pr_delivery_effect_child_unsettled"
CHILD_UNRESOLVED = "pr_delivery_effect_owner_unresolved"

_PR_JSON_FIELDS = "number,url,headRefOid,headRefName,baseRefName,state"

__all__ = ("DeliveryTransport", "DeliveryTransportError")


def _effect_form(verb, argv):
    """The effect form of one git call: ``symbolic-ref`` with a ref to SET
    (two positional arguments) is ``SYMBOLIC_REF_SET``; its read-only query
    form stays ``symbolic-ref``; every other verb is itself."""
    if verb != "symbolic-ref":
        return verb
    rest = argv[argv.index(verb) + 1:]
    positional = [item for item in rest if not item.startswith("-")]
    return SYMBOLIC_REF_SET if len(positional) >= 2 else verb


def _group_gone(pgid):
    """True only when ``killpg(pgid, 0)`` proves no process remains in the
    group; alive, or not ours to probe, is NOT gone."""
    try:
        os.killpg(pgid, 0)
    except ProcessLookupError:
        return True
    except OSError:
        return False
    return False


class DeliveryTransport(object):
    """The real transport. Hermetic tests inject a fake instead."""

    # Bound by the machine (never by construction: the transport is built
    # with zero arguments in exactly one place, ``cli.build_machine``).
    child_ledger_directory = None
    effect_owner = None
    # The ledger's problem codes, for callers that must not import this
    # module (the Mission-bound preparation reads them off the instance).
    child_unsettled_problem = CHILD_UNSETTLED
    child_unresolved_problem = CHILD_UNRESOLVED

    def bind_child_ledger(self, directory):
        """Record owned effect children under ``directory`` (the delivery
        store's protected directory)."""
        self.child_ledger_directory = os.path.join(directory,
                                                   CHILD_LEDGER_DIR_NAME)

    def _ledger_path(self, delivery_id):
        return os.path.join(self.child_ledger_directory,
                            "%s.jsonl" % delivery_id)

    def _ledger_append(self, delivery_id, row):
        os.makedirs(self.child_ledger_directory, mode=0o700, exist_ok=True)
        descriptor = os.open(self._ledger_path(delivery_id),
                             os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        try:
            os.write(descriptor, (json.dumps(row, sort_keys=True) + "\n")
                     .encode("utf-8"))
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    def _owner(self, owned):
        """The effect owner to record this child under, or None (a read,
        or no ledger bound / no owner named)."""
        owner = self.effect_owner
        if not owned or self.child_ledger_directory is None or not isinstance(
            owner, dict
        ):
            return None
        return owner

    def unsettled_children(self, delivery_id):
        """``[(problem, detail)]`` for every owned child of ``delivery_id``
        whose settlement is not proven: a group still alive
        (``CHILD_UNSETTLED``) or an intent whose group id was never recorded
        (``CHILD_UNRESOLVED``). A group found gone now gets its settled row
        (the proof). Read-mostly; never signals a process."""
        if self.child_ledger_directory is None:
            return []
        path = self._ledger_path(delivery_id)
        if not os.path.exists(path):
            return []
        intents, groups, settled = {}, {}, set()
        with open(path, "r", encoding="utf-8") as handle:
            for line in handle:
                try:
                    row = json.loads(line)
                except ValueError:
                    return [(CHILD_UNRESOLVED,
                             "the owned-child ledger of %s is unreadable"
                             % delivery_id)]
                if not isinstance(row, dict):
                    continue
                if isinstance(row.get("intent"), str):
                    intents[row["intent"]] = row
                elif isinstance(row.get("nonce"), str):
                    if isinstance(row.get("pgid"), int):
                        groups[row["nonce"]] = row["pgid"]
                    if row.get("settled") is True:
                        settled.add(row["nonce"])
        problems = []
        for nonce, intent in sorted(intents.items()):
            if nonce in settled:
                continue
            pgid = groups.get(nonce)
            if pgid is None:
                problems.append((CHILD_UNRESOLVED,
                                 "effect child %s (%s %s) was intended but its"
                                 " process group was never recorded; ownership"
                                 " and settlement cannot be proven" % (
                                     nonce, intent.get("step"),
                                     intent.get("effect"))))
            elif _group_gone(pgid):
                self._ledger_append(delivery_id, {"nonce": nonce,
                                                  "settled": True,
                                                  "proven_at": time.time()})
            else:
                problems.append((CHILD_UNSETTLED,
                                 "effect child %s (%s %s) process group %d is"
                                 " still alive; no retry until it settles" % (
                                     nonce, intent.get("step"),
                                     intent.get("effect"), pgid)))
        return problems

    def child_intents(self, delivery_id):
        """The effect names of every owned child recorded under
        ``delivery_id`` (its fsynced intent rows, in order), or None when
        the ledger cannot be read. Read-only; the settlement of each is
        ``unsettled_children``'s to prove."""
        if self.child_ledger_directory is None:
            return None
        path = self._ledger_path(delivery_id)
        if not os.path.exists(path):
            return []
        effects = []
        with open(path, "r", encoding="utf-8") as handle:
            for line in handle:
                try:
                    row = json.loads(line)
                except ValueError:
                    return None
                if isinstance(row, dict) and isinstance(row.get("intent"), str):
                    effects.append(row.get("effect"))
        return effects

    def _start_child(self, argv, owned, **popen_kwargs):
        """Start one child. An OWNED child is recorded (intent before, group
        after) and runs in its own session; returns ``(process, owner,
        nonce)``."""
        owner = self._owner(owned)
        if owner is None:
            return subprocess.Popen(argv, **popen_kwargs), None, None
        nonce = "chd-" + secrets.token_hex(8)
        self._ledger_append(owner["delivery_id"], {
            "intent": nonce, "step": owner.get("step"),
            "effect": owner.get("effect"), "program": os.path.basename(argv[0]),
            "at": time.time()})
        process = subprocess.Popen(argv, start_new_session=True, **popen_kwargs)
        self._ledger_append(owner["delivery_id"], {"nonce": nonce,
                                                   "pgid": process.pid})
        return process, owner, nonce

    def _settle_child(self, process, owner, nonce, returncode):
        if owner is None:
            return
        self._ledger_append(owner["delivery_id"], {
            "nonce": nonce, "returncode": returncode,
            "settled": _group_gone(process.pid), "at": time.time()})

    def _run(self, argv, cwd=None, stdin_bytes=None, owned=False):
        """Run one argv; return ``(returncode, stdout_bytes, stderr_text)``.

        stdout is streamed and bounded; over the bound the child is
        killed and the call refuses. stderr is captured through a
        temporary file and only its head is retained for messages.
        ``owned`` marks an EFFECT child (see the module docstring).
        """
        with tempfile.TemporaryFile() as stderr_file:
            process, owner, nonce = self._start_child(
                argv, owned, cwd=cwd, stdout=subprocess.PIPE,
                stderr=stderr_file,
                stdin=subprocess.PIPE if stdin_bytes is not None else (
                    subprocess.DEVNULL
                ),
            )
            if stdin_bytes is not None:
                try:
                    process.stdin.write(stdin_bytes)
                finally:
                    process.stdin.close()
            collected = bytearray()
            over = False
            try:
                while True:
                    chunk = process.stdout.read(_STREAM_CHUNK_BYTES)
                    if not chunk:
                        break
                    collected.extend(chunk)
                    if len(collected) > MAX_TRANSPORT_OUTPUT_BYTES:
                        over = True
                        break
            finally:
                if over:
                    process.kill()
                process.stdout.close()
                returncode = process.wait()
                self._settle_child(process, owner, nonce, returncode)
            stderr_file.seek(0)
            stderr_text = stderr_file.read(_STDERR_RETAINED_BYTES).decode(
                "utf-8", "replace"
            )
        if over:
            raise DeliveryTransportError(
                "%s produced more than %d bytes; refusing to parse a"
                " partial output" % (argv[0], MAX_TRANSPORT_OUTPUT_BYTES)
            )
        return returncode, bytes(collected), stderr_text

    def _git(self, path, argv, allow_fail=False, config=()):
        # The closed verb set is enforced HERE at call time, not only by
        # the static pin (round-01 N1): the first non-option element of
        # the caller's argv must be an allowed verb.
        verb = next((item for item in argv if not item.startswith("-")),
                    None)
        if verb not in ALLOWED_GIT_VERBS:
            raise DeliveryTransportError(
                "git verb %r is outside the closed verb set" % (verb,)
            )
        full = ["git"]
        for item in config:
            full.extend(["-c", item])
        full.extend(["-C", str(path)])
        full.extend(argv)
        returncode, stdout, stderr = self._run(
            full, owned=_effect_form(verb, argv) in EFFECT_GIT_VERBS)
        if returncode != 0 and not allow_fail:
            raise DeliveryTransportError(
                "git %s failed (%d): %s"
                % (argv[0], returncode, stderr.strip()[:500])
            )
        return returncode, stdout, stderr

    def _git_text(self, path, argv, allow_fail=False, config=()):
        returncode, stdout, _ = self._git(path, argv, allow_fail=allow_fail,
                                          config=config)
        return returncode, stdout.decode("utf-8", "replace").strip()

    # -- read verbs ---------------------------------------------------

    def toplevel(self, path):
        return self._git_text(path, ["rev-parse", "--show-toplevel"])[1]

    def git_dir(self, path):
        return self._git_text(
            path, ["rev-parse", "--path-format=absolute", "--git-dir"]
        )[1]

    def rev_parse(self, path, spec):
        code, text = self._git_text(path, ["rev-parse", "--verify",
                                           "--quiet", spec + "^{}"],
                                    allow_fail=True)
        return text if code == 0 and text else None

    def head_oid(self, path):
        return self.rev_parse(path, "HEAD")

    def symbolic_ref_head(self, path):
        code, text = self._git_text(path, ["symbolic-ref", "-q", "HEAD"],
                                    allow_fail=True)
        return text if code == 0 and text else None

    def config_get(self, path, key):
        code, text = self._git_text(path, ["config", "--get", key],
                                    allow_fail=True)
        return text if code == 0 and text else None

    def remote_url(self, path, name):
        """The CONFIGURED value (``remote.<name>.url``)."""
        return self.config_get(path, "remote.%s.url" % name)

    def remote_fetch_url(self, path, name):
        """The EXPANDED fetch URL git resolves for ``name``."""
        code, text = self._git_text(path, ["remote", "get-url", name],
                                    allow_fail=True)
        return text if code == 0 and text else None

    def remote_push_url(self, path, name):
        """The EXPANDED push URL git resolves for ``name`` — what the
        pre-push hook is handed."""
        code, text = self._git_text(path, ["remote", "get-url", "--push",
                                           name], allow_fail=True)
        return text if code == 0 and text else None

    def status_porcelain(self, path):
        """quotePath-pinned porcelain (see git_transport)."""
        _, stdout, _ = self._git(
            path, ["--no-optional-locks", "status", "--porcelain"],
            config=("core.quotePath=true",),
        )
        try:
            return stdout.decode("utf-8")
        except UnicodeDecodeError:
            raise DeliveryTransportError(
                "porcelain capture is not valid UTF-8; refusing to parse"
                " it lossily"
            )

    def diff_index_raw(self, path, base_oid):
        """Staged index vs ``base_oid``: ``--raw -z --no-renames``."""
        _, stdout, _ = self._git(
            path, ["diff-index", "--cached", "--raw", "--abbrev=40",
                   "--no-renames", "-z", base_oid],
        )
        return stdout

    def diff_tree_raw(self, path, old_oid, new_oid):
        _, stdout, _ = self._git(
            path, ["diff-tree", "-r", "--raw", "--abbrev=40",
                   "--no-renames", "-z", old_oid, new_oid],
        )
        return stdout

    def staged_diff_sha256(self, path):
        """sha256 of ``diff --cached --binary`` exactly as the legacy
        guard computes it (strict UTF-8 text round trip)."""
        _, stdout, _ = self._git(path, ["diff", "--cached", "--binary"])
        try:
            text = stdout.decode("utf-8")
        except UnicodeDecodeError:
            raise DeliveryTransportError(
                "staged diff is not valid UTF-8; the legacy guard could"
                " not bind it either"
            )
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    def write_tree(self, path):
        return self._git_text(path, ["write-tree"])[1]

    def commit_parent_and_tree(self, path, oid):
        parent = self.rev_parse(path, oid + "^1")
        tree = self.rev_parse(path, oid + "^{tree}")
        return parent, tree

    def ls_remote(self, path, remote_name, ref):
        """The remote OID of ``ref``, or None when the remote PROVABLY has no
        such ref (``--exit-code`` exit 2). Any other failure RAISES: a remote
        lookup that could not be made is never read as absence (Task 8
        S-VI, prep Q7)."""
        code, text = self._git_text(path, ["ls-remote", "--exit-code",
                                           remote_name, ref],
                                    allow_fail=True)
        if code == 2:
            return None
        if code != 0:
            raise DeliveryTransportError(
                "git ls-remote %s %s failed (%d); the remote could not be"
                " queried, which is never absence" % (remote_name, ref, code))
        for line in text.splitlines():
            parts = line.split("\t")
            if len(parts) == 2 and parts[1] == ref:
                return parts[0]
        return None

    def is_ancestor(self, path, old_oid, new_oid):
        code, _, _ = self._git(
            path, ["merge-base", "--is-ancestor", old_oid, new_oid],
            allow_fail=True,
        )
        return code == 0

    # -- write verbs --------------------------------------------------

    def fetch_ref(self, path, remote_name, ref):
        self._git(path, ["fetch", "--quiet", "--no-tags", remote_name,
                         ref])

    def read_tree_two_way(self, path, old_oid, new_oid):
        """Two-way merge read. ``update-index -q --refresh`` first: the
        merge refuses any entry whose cached stat data is stale ("not
        uptodate"), which a touched or copied file produces even when its
        content is unchanged. The refresh rewrites stat data only; it
        never changes what is staged (the only ``update-index`` form this
        transport issues)."""
        self._git(path, ["update-index", "-q", "--refresh"])
        self._git(path, ["read-tree", "-m", "-u", old_oid, new_oid])

    def update_ref(self, path, ref, new_oid, old_oid):
        """Compare-and-swap ref move: refuses unless the ref is exactly
        ``old_oid`` at the moment of the update."""
        self._git(path, ["update-ref", ref, new_oid, old_oid])

    def source_branch_state(self, path, ref, head_oid):
        """Task 8 S-VI, READ-ONLY: where the Mission-bound preparation of the
        local source branch ``ref`` at ``head_oid`` stands in ``path``, read
        from the actual refs and HEAD. Returns ``(state, detail)``:

        - ``not_started``: ``ref`` absent, HEAD detached at ``head_oid``;
        - ``partial``: ``ref`` at ``head_oid``, HEAD still detached there
          (the window between the ref creation and the HEAD move);
        - ``done``: HEAD names ``ref`` and ``ref`` is at ``head_oid``;
        - ``foreign``: anything else (HEAD elsewhere, the ref at another
          commit, HEAD naming another branch) — never adopted."""
        symbolic = self.symbolic_ref_head(path)
        at = self.rev_parse(path, ref)
        head = self.head_oid(path)
        if symbolic == ref and at == head_oid:
            return "done", None
        if symbolic is None and head == head_oid:
            if at is None:
                return "not_started", None
            if at == head_oid:
                return "partial", None
        return "foreign", "HEAD %s (symbolic %s), %s at %s, bound %s" % (
            head, symbolic, ref, at, head_oid)

    def attach_head(self, path, ref):
        """Task 8 S-VI, the second (atomic) half of the preparation: point
        HEAD at the existing local ``ref`` (``symbolic-ref``: one lockfile
        rename; index and working tree untouched, no remote reached). The
        caller has classified the state (``source_branch_state``) first."""
        self._git(path, ["symbolic-ref", "HEAD", ref])

    def commit(self, path, name, email, message):
        """One commit with identity and ``gpgsign=false`` on the argv."""
        self._git(
            path, ["commit", "--quiet", "-m", message],
            config=(
                "user.name=" + name, "user.email=" + email,
                "commit.gpgsign=false",
            ),
        )

    def push(self, path, remote_name, source_ref, destination_ref):
        """One push of ``source_ref:destination_ref``; no force parameter
        exists on this verb."""
        self._git(path, ["push", "--quiet", remote_name,
                         "%s:%s" % (source_ref, destination_ref)])

    def run_reverification(self, argv, cwd):
        """Run the human-bound verification argv (see module docstring).
        Returns ``(returncode, log_bytes, log_truncated)``."""
        if not isinstance(argv, list) or not all(
            isinstance(item, str) for item in argv
        ) or not argv:
            raise DeliveryTransportError(
                "reverification argv must be a non-empty list of strings"
            )
        with tempfile.TemporaryFile() as stderr_file:
            process, owner, nonce = self._start_child(
                list(argv), True, cwd=cwd, stdout=subprocess.PIPE,
                stderr=stderr_file, stdin=subprocess.DEVNULL,
            )
            collected = bytearray()
            truncated = False
            try:
                while True:
                    chunk = process.stdout.read(_STREAM_CHUNK_BYTES)
                    if not chunk:
                        break
                    if len(collected) < MAX_TRANSPORT_OUTPUT_BYTES:
                        room = MAX_TRANSPORT_OUTPUT_BYTES - len(collected)
                        collected.extend(chunk[:room])
                        if len(chunk) > room:
                            truncated = True
                    else:
                        truncated = True
            finally:
                process.stdout.close()
                returncode = process.wait()
                self._settle_child(process, owner, nonce, returncode)
        return returncode, bytes(collected), truncated

    # -- gh verbs -----------------------------------------------------

    def _gh(self, argv, stdin_bytes=None):
        prefix = tuple(argv[:3]) if argv[:1] == ["api"] else tuple(argv[:2])
        if prefix not in ALLOWED_GH_ARGV:
            raise DeliveryTransportError(
                "gh verb %r is outside the closed verb set" % (prefix,)
            )
        # Only ``pr create`` changes anything remotely: an OWNED effect child.
        returncode, stdout, stderr = self._run(
            ["gh"] + list(argv), stdin_bytes=stdin_bytes,
            owned=prefix == ("pr", "create"))
        if returncode != 0:
            raise DeliveryTransportError(
                "gh %s failed (%d): %s"
                % (" ".join(prefix), returncode, stderr.strip()[:500])
            )
        return stdout

    def _gh_json(self, argv):
        stdout = self._gh(argv)
        try:
            return json.loads(stdout.decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as exc:
            raise DeliveryTransportError(
                "gh %s returned unparsable JSON (%s)" % (argv[0], exc)
            )

    def gh_pr_list(self, owner, repo, head_branch, base_branch):
        """Every pull request for this head/base in EVERY state, so a
        closed or merged exact pull request is seen by reconciliation
        rather than duplicated (round-01 B3)."""
        return self._gh_json([
            "pr", "list", "--repo", "%s/%s" % (owner, repo),
            "--head", head_branch, "--base", base_branch,
            "--state", "all", "--json", _PR_JSON_FIELDS,
        ])

    def gh_pr_create(self, owner, repo, head_branch, base_branch, title,
                     body_text):
        stdout = self._gh(
            ["pr", "create", "--repo", "%s/%s" % (owner, repo),
             "--head", head_branch, "--base", base_branch,
             "--title", title, "--body-file", "-"],
            stdin_bytes=body_text.encode("utf-8"),
        )
        return stdout.decode("utf-8", "replace").strip()

    def gh_pr_view(self, owner, repo, number):
        return self._gh_json([
            "pr", "view", str(int(number)), "--repo",
            "%s/%s" % (owner, repo), "--json", _PR_JSON_FIELDS,
        ])

    def gh_check_runs(self, owner, repo, sha):
        document = self._gh_json([
            "api", "--method", "GET", CHECK_RUNS_ENDPOINT % (owner, repo, sha),
        ])
        runs = document.get("check_runs") if isinstance(document, dict) else (
            None
        )
        if not isinstance(runs, list):
            raise DeliveryTransportError(
                "check-runs response carries no check_runs list"
            )
        return [
            {
                "name": str(run.get("name")),
                "status": str(run.get("status")),
                "conclusion": run.get("conclusion"),
            }
            for run in runs if isinstance(run, dict)
        ]
