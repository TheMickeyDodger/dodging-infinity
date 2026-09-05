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
"""

import hashlib
import json
import subprocess
import tempfile

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

_PR_JSON_FIELDS = "number,url,headRefOid,headRefName,baseRefName,state"

__all__ = ("DeliveryTransport", "DeliveryTransportError")


class DeliveryTransport(object):
    """The real transport. Hermetic tests inject a fake instead."""

    def _run(self, argv, cwd=None, stdin_bytes=None):
        """Run one argv; return ``(returncode, stdout_bytes, stderr_text)``.

        stdout is streamed and bounded; over the bound the child is
        killed and the call refuses. stderr is captured through a
        temporary file and only its head is retained for messages.
        """
        with tempfile.TemporaryFile() as stderr_file:
            process = subprocess.Popen(
                argv, cwd=cwd, stdout=subprocess.PIPE, stderr=stderr_file,
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
        returncode, stdout, stderr = self._run(full)
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
        _, text = self._git_text(path, ["ls-remote", "--exit-code",
                                        remote_name, ref],
                                 allow_fail=True)
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
            process = subprocess.Popen(
                list(argv), cwd=cwd, stdout=subprocess.PIPE,
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
        return returncode, bytes(collected), truncated

    # -- gh verbs -----------------------------------------------------

    def _gh(self, argv, stdin_bytes=None):
        prefix = tuple(argv[:3]) if argv[:1] == ["api"] else tuple(argv[:2])
        if prefix not in ALLOWED_GH_ARGV:
            raise DeliveryTransportError(
                "gh verb %r is outside the closed verb set" % (prefix,)
            )
        returncode, stdout, stderr = self._run(["gh"] + list(argv),
                                               stdin_bytes=stdin_bytes)
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
