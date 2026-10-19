"""Item 3 (original 20) — the git side of the live code review.

A review target is an agent's REAL workspace, not a disposable lease, so the
bar for every git verb pointed at it is MEASURED, not argued: the target's
whole git state (the worktree's git directory AND the repository's common
directory) is byte-identical before and after, by digest. These tests build
real repositories and real linked worktrees, make the index STAT-DIRTY (so a
refresh WOULD rewrite it), and prove:

1. the CONTROLS: ``git diff HEAD`` (with or without ``--no-optional-locks``)
   and ``git status`` with default locks DO rewrite the index of this very
   fixture — the trap is real, and the fixture would show a mutation;
2. every live verb leaves the whole git state untouched;
3. repository-authored configuration cannot make a live read execute a
   program (external diff, textconv, fsmonitor), and an inherited ``GIT_*``
   variable of the parent cannot steer it;
4. the transport's own bounds: a deadline, an output ceiling.

The cases run against the real ``git`` of the machine; the version is part of
the evidence (``test_the_git_version_is_recorded``).
"""

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "tests"))

from target_runtime import git_transport  # noqa: E402
from test_mission_sources import Bounded  # noqa: E402
from _hermetic_git import run_git_completed  # noqa: E402

#: The ordinary untracked directory of the fixture workspace. Named, because the string "notes" is also a git verb and
#: the identity guard (tests/test_hermetic_git.py) reads a literal "notes" among a call's arguments as `git notes`.
NOTES_DIR = "notes"

#: The test's own git environment (the product's is git_transport's).
CLEAN_ENV = {
    "PATH": "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin",
    "LC_ALL": "C", "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": "/dev/null",
    "GIT_TERMINAL_PROMPT": "0",
}


def fixture_git_env():
    """The fixture's git environment: ``CLEAN_ENV`` exactly (the identity scrub stays: no GIT_AUTHOR_*/GIT_COMMITTER_*,
    GIT_CONFIG_NOSYSTEM, GIT_CONFIG_GLOBAL=/dev/null), except that PATH is the INHERITED one. A hardcoded PATH hides the
    executed sweep's logging shim (tests/test_hermetic_git.py prepends it to the inherited PATH) and the sweep would observe
    no git call of this module. The deliberate CONTROL environments below keep ``CLEAN_ENV`` itself."""
    env = dict(CLEAN_ENV)
    env["PATH"] = os.environ.get("PATH", CLEAN_ENV["PATH"])
    return env


def run_git(cwd, *args, check=True, env=None):
    """``git -C cwd ARGS`` through the hermetic helper (the identity rides the invocation as -c options). Returns the
    CompletedProcess with BYTES output, as it always did: callers decode it or compare it byte for byte."""
    return run_git_completed(["-C", cwd] + list(args), check=check,
                             env=env or fixture_git_env(), text=False)


def tree_state(directory):
    """Every file under ``directory``: path, size, mtime, inode, content."""
    entries = []
    for dirpath, dirnames, filenames in os.walk(directory):
        dirnames.sort()
        for name in sorted(filenames):
            path = os.path.join(dirpath, name)
            st = os.lstat(path)
            with open(path, "rb") as handle:
                body = hashlib.sha256(handle.read()).hexdigest()
            entries.append((os.path.relpath(path, directory), st.st_size,
                            st.st_mtime_ns, st.st_ino, body))
    return entries


def git_state(workspace):
    """The target's WHOLE git state: the worktree's git directory and the
    repository's common directory (the same one for a plain repository)."""
    directories = set()
    for flag in ("--absolute-git-dir", "--git-common-dir"):
        out = run_git(workspace, "rev-parse", "--path-format=absolute",
                      flag).stdout.decode().strip()
        directories.add(os.path.realpath(out))
    return dict((d, tree_state(d)) for d in sorted(directories))


def digest(state):
    return hashlib.sha256(repr(sorted(state.items())).encode()).hexdigest()


def future_mtime(path, seconds=30):
    """Move ``path``'s mtime so its stat no longer matches the index's entry
    for it, WITHOUT changing its content (what a touch, a checkout or a
    backup tool does to a clean file)."""
    when = time.time_ns() + seconds * 1_000_000_000
    os.utime(path, ns=(when, when))


class Repo(object):
    """A real repository (or linked worktree) in a temporary directory."""

    @classmethod
    def adopt(cls, path):
        """A repository made INSIDE an existing directory (a Mission's
        workspace, which already holds the herd's private state)."""
        self = cls.__new__(cls)
        self.path = os.path.realpath(path)
        self.git("init", "-q", "-b", "main")
        self.git("config", "user.email", "t@example.com")
        self.git("config", "user.name", "T")
        return self

    def __init__(self, parent, name, main=None):
        self.path = os.path.realpath(os.path.join(parent, name))
        if main is None:
            os.makedirs(self.path)
            self.git("init", "-q", "-b", "main")
            self.git("config", "user.email", "t@example.com")
            self.git("config", "user.name", "T")
        else:
            self.git_in(main.path, "worktree", "add", "-q", "--detach",
                        self.path, "HEAD")
            self.path = os.path.realpath(self.path)

    def git(self, *args, **kwargs):
        return run_git(self.path, *args, **kwargs)

    @staticmethod
    def git_in(cwd, *args):
        return run_git(cwd, *args)

    def write(self, relative, data):
        path = os.path.join(self.path, *relative.split("/"))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as handle:
            handle.write(data if isinstance(data, bytes) else data.encode())
        return path

    def stage(self, *relatives):
        self.git("add", "--", *relatives)

    def save(self, message="fixture"):
        run_git(self.path, "-c", "commit.gpgsign=false", "commit", "-q", "-m", message)


def busy_workspace(repo, extras=None):
    """The shape of a workspace an agent is working in: staged, unstaged,
    both, deleted, renamed, binary, empty, untracked, private, stat-dirty.
    ``extras`` ({path: (committed, changed)}) are further TRACKED files,
    committed with the base and changed after it (unstaged)."""
    extras = extras or {}
    for relative, (committed, _changed) in extras.items():
        repo.write(relative, committed)
        repo.stage(relative)
    repo.write("src/app.py", "def main():\n    return 1\n")
    repo.write("src/util.py", "A = 1\nB = 2\nC = 3\n")
    repo.write("src/mover.py", "".join("line %d\n" % n for n in range(20)))
    repo.write("docs/gone.md", "to be deleted\n")
    repo.write("bin/blob.bin", b"\x00\x01\x02binary\x00")
    repo.write("touched.txt", "unchanged\n")
    repo.write("crlf.txt", "a\r\nb\r\n")
    repo.stage("src", "docs", "bin", "touched.txt", "crlf.txt")
    repo.save("base")
    # staged, then changed again after staging: both
    repo.write("src/app.py", "def main():\n    return 2\n")
    repo.stage("src/app.py")
    repo.write("src/app.py", "def main():\n    return 3\n")
    # unstaged only
    repo.write("src/util.py", "A = 1\nB = 20\nC = 3\nD = 4\n")
    # staged deletion
    repo.git("rm", "-q", "--", "docs/gone.md")
    # staged rename with a small unstaged edit on top
    repo.git("mv", "src/mover.py", "src/moved.py")
    repo.write("src/moved.py",
               "".join("line %d\n" % n for n in range(20)) + "extra\n")
    # binary modified
    repo.write("bin/blob.bin", b"\x00\x01\x02binary-changed\x00")
    # a new empty file, staged
    repo.write("empty.txt", b"")
    repo.stage("empty.txt")
    # crlf edit
    repo.write("crlf.txt", "a\r\nb\r\nc\r\n")
    # untracked: ordinary, private, secret-like
    repo.write("notes/new.txt", "first\nsecond\n")
    repo.write(".env", "TOKEN=hunter2\n")
    repo.write(".herd/state.json", "{}\n")
    for relative, (_committed, changed) in extras.items():
        repo.write(relative, changed)
    # stat-dirty but identical
    future_mtime(os.path.join(repo.path, "touched.txt"))
    future_mtime(os.path.join(repo.path, "src", "util.py"))


class FixtureCase(Bounded):
    """CONTRIBUTING.md's termination rule: no test may depend on the code path
    under test for its own termination. Every test built on this fixture
    constructs the real ``GitTransport`` and drives real git reads, and the
    transport's own 20-second deadline IS the code path under test, so each
    one carries the repository's own independent bound
    (``test_mission_sources.Bounded``: a SIGALRM watchdog whose expiry is a
    ``BaseException`` that the backend's ``except OSError`` cannot swallow,
    recorded and failed at cleanup), ARMED FIRST, before the fixture is built.
    ``WatchdogBoundsTheRealReadTests`` (``test_code_review_read.py``) proves it
    fires and that every class of the review suites carries it. ``FifoTests``
    keeps its own subprocess bound as well."""

    def setUp(self):
        Bounded.setUp(self)                    # the bound, FIRST
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = os.path.realpath(self._tmp.name)

    def repo(self, name="ws"):
        return Repo(self.root, name)

    def busy(self, name="ws", linked=False):
        main = self.repo(name)
        busy_workspace(main)
        if not linked:
            return main
        # The linked worktree is where the Mission works in production: its
        # git directory is under the main repository's, and ITS index lives
        # there. Build the same busy shape inside it.
        base = Repo(self.root, name + "-main")
        base.write("README.md", "main\n")
        base.stage("README.md")
        base.save("main base")
        linked = Repo(self.root, name + "-linked", main=base)
        busy_workspace(linked)
        return linked


#: Run in a CHILD process so that a regression BLOCKS the child, not the suite:
#: the parent's own timeout kills it (``subprocess.run(timeout=)``).
_FIFO_CHILD = r"""
import json, os, sys
sys.path.insert(0, sys.argv[1])
from target_runtime import git_transport
mode = sys.argv[2]
try:
    if mode in ("lax", "strict"):
        copied = git_transport._copy_regular(
            sys.argv[3], sys.argv[4], 1024, "the file", mode == "strict")
        print(json.dumps({"copied": copied}))
    else:
        with git_transport.GitTransport()._live_view(sys.argv[3]) as (oid, env):
            print(json.dumps({"viewed": os.path.isdir(env["GIT_DIR"])}))
except git_transport.GitTransportError as exc:
    print(json.dumps({"refused": str(exc)}))
"""
FIFO_SECONDS = 8


class FifoTests(FixtureCase):
    """ROUND 20 F1. ``O_NOFOLLOW`` refuses a symlink, not a FIFO, and
    ``open(O_RDONLY)`` on a FIFO BLOCKS until a writer appears. The copy is in
    Python, outside the git deadline: a FIFO planted where git keeps a file
    would hold the review slot for good. Each case is itself time-bounded."""

    def child(self, *args):
        try:
            done = subprocess.run(
                [sys.executable, "-I", "-c", _FIFO_CHILD, str(REPO_ROOT)]
                + list(args), capture_output=True, timeout=FIFO_SECONDS)
        except subprocess.TimeoutExpired:
            self.fail("BLOCKED: the read waited on a FIFO for more than %d s"
                      % FIFO_SECONDS)
        self.assertEqual(done.returncode, 0, done.stderr.decode()[-400:])
        import json
        return json.loads(done.stdout.decode())

    def fifo(self, name="pipe"):
        path = os.path.join(self.root, name)
        os.mkfifo(path)
        return path

    def test_a_fifo_is_skipped_not_waited_for(self):
        out = os.path.join(self.root, "copy")
        self.assertEqual(self.child("lax", self.fifo(), out), {"copied": False})
        self.assertFalse(os.path.exists(out))

    def test_a_fifo_where_a_regular_file_is_required_is_refused(self):
        answer = self.child("strict", self.fifo(), os.path.join(self.root, "c"))
        self.assertIn("not a regular file", answer["refused"])

    def test_a_fifo_in_place_of_the_info_files_does_not_hold_the_view(self):
        repo = self.repo("fifo-info")
        repo.write("a.txt", "a\n")
        repo.stage("a.txt")
        repo.save("base")
        info = os.path.join(repo.path, ".git", "info")
        os.makedirs(info, exist_ok=True)
        for name in ("exclude", "attributes"):
            target = os.path.join(info, name)
            if os.path.lexists(target):
                os.unlink(target)
            os.mkfifo(target)
        self.assertEqual(self.child("view", repo.path), {"viewed": True})

    def test_a_fifo_in_place_of_the_index_is_refused_not_waited_for(self):
        repo = self.repo("fifo-index")
        repo.write("a.txt", "a\n")
        repo.stage("a.txt")
        repo.save("base")
        index = os.path.join(repo.path, ".git", "index")
        os.unlink(index)
        os.mkfifo(index)
        self.assertIn("not a regular file",
                      self.child("view", repo.path)["refused"])


def private_workspace(case, name="priv"):
    """A workspace whose PRIVATE tracked files (the credential-name rule:
    ``secrets/``) are in every state git would HASH or DIFF them in, and
    UNREADABLE (mode 000): an attempt to open one is then visible, as a
    failure or as the file listed changed. A public change sits beside them."""
    repo = case.repo(name)
    repo.write("src/app.py", "def main():\n    return 1\n")
    repo.write("docs/readme.md", "hello\n")
    repo.write("secrets/key.txt", "BODY-ONE-" + "0" * 10 + "\n")      # a placeholder, built at run time
    repo.write("secrets/touched.txt", "same\n")
    repo.write("secrets/staged.txt", "BODY-OLD-XXXXXXXXXXXX\n")
    repo.write("secrets/gone.txt", "BODY-GONE-0123\n")
    repo.stage("src", "docs", "secrets")
    repo.save("base")
    repo.write("src/app.py", "def main():\n    return 2\n")
    repo.write("secrets/key.txt", "BODY-TWO-ABCDEFGHIJ\n")    # same size
    future_mtime(os.path.join(repo.path, "secrets", "key.txt"))
    future_mtime(os.path.join(repo.path, "secrets", "touched.txt"))
    repo.write("secrets/staged.txt", "BODY-NEW-YYYYYYYYYYYY\n")
    repo.stage("secrets/staged.txt")
    repo.git("rm", "-q", "--", "secrets/gone.txt")
    for relative in ("key.txt", "touched.txt", "staged.txt"):
        target = os.path.join(repo.path, "secrets", relative)
        os.chmod(target, 0)
        case.addCleanup(os.chmod, target, 0o600)
    return repo


class ReadWitness(object):
    """A witness of git's reads of WORKING files through the filter path, with no
    privileged tracing. Git runs a content filter's CLEAN program when it reads a
    working file's content through its conversion path to hash or compare it. A
    clean filter that appends the file's name to a log therefore PROVES, for every
    name in the log, that git physically read THAT WORKING FILE's content through
    that path. Absence from the log means only that no such FILTERED working-file
    content read occurred.

    What it is NOT: it is not syscall tracing. It does not see an object-store
    (blob) read, which runs no filter (a test that needs one makes the blob
    unreadable, so the read fails visibly), nor any read path that bypasses
    conversion. For the verbs these tests use, on git 2.39.5, the working-file
    read of a stat-clean tracked entry IS seen (tools/witness_limits_probe.py, A
    and B); that is a measurement, not a guarantee about other git versions.

    SEPARATION, never to be cited as one proof: the filter is INJECTED into the
    environment of the PRIVATE view's git processes by a TEST-ONLY patch of the
    transport's ``_stream`` (GIT_CONFIG_COUNT/KEY/VALUE, plus ``* filter=witness``
    in the repository's info/attributes, which the view copies as data). This
    harness runs a program ON PURPOSE. The product's no-exec property is the
    opposite claim, proved without it: unmodified, the private view carries no
    program-valued configuration, so no repository-configured program can run
    (the filter-race probe, the allow-listed core settings and the argv and
    environment pins)."""

    def __init__(self, case, repo):
        case.witnesses = getattr(case, "witnesses", 0) + 1
        self.log = os.path.join(case.root, "reads-%d.log" % case.witnesses)
        self.command = ("sh -c 'printf \"%%s\\n\" \"$0\" >> %s; cat' %%f"
                        % self.log)
        with open(os.path.join(repo.path, ".git", "info", "attributes"),
                  "a") as handle:
            handle.write("* filter=witness\n")
        self._patch = None

    def __enter__(self):
        real = git_transport.GitTransport._stream
        command = self.command

        def spy(transport, argv, *args, **kwargs):
            env = kwargs.get("env")
            if env is not None and "GIT_DIR" in env:
                kwargs["env"] = dict(
                    env, GIT_CONFIG_COUNT="1",
                    GIT_CONFIG_KEY_0="filter.witness.clean",
                    GIT_CONFIG_VALUE_0=command)
            return real(transport, argv, *args, **kwargs)
        self._patch = mock.patch.object(git_transport.GitTransport,
                                        "_stream", spy)
        self._patch.start()
        return self

    def __exit__(self, *exc):
        self._patch.stop()
        return False

    def reads(self):
        if not os.path.exists(self.log):
            return set()
        with open(self.log) as handle:
            return set(handle.read().split("\n")) - {""}

    def reset(self):
        if os.path.exists(self.log):
            os.unlink(self.log)


def blob_id(body):
    return hashlib.sha1(b"blob %d\x00" % len(body) + body).hexdigest().encode()


class PrivateLocationBoundaryTests(FixtureCase):
    """ROUND 20 F2. The reads that can hash or diff a working file are told
    which locations are private and git itself LEAVES THEM OUT; the names and
    kinds of change of the private tracked files come from reads that open no
    file. Every case runs over files that CANNOT be opened."""

    LOCATIONS = ("secrets",)
    #: every public verb of the live seam, classified deliberately
    OPENS_NO_FILE = {"live_toplevel", "live_head", "live_filter_drivers",
                     "live_view", "live_names", "live_index_stat"}
    CAN_HASH = {"live_inventory", "live_counts", "live_modes",
                "live_file_diff"}

    def setUp(self):
        super(PrivateLocationBoundaryTests, self).setUp()
        self.repo_ = private_workspace(self)
        self.path = self.repo_.path
        self.transport = git_transport.GitTransport()

    def test_the_fixture_is_sensitive_git_does_try_to_open_them(self):
        # the CONTROLS: with nothing left out, git fails to hash one body and
        # lists a file that was only touched (it tried, and could not read it)
        with self.assertRaises(git_transport.GitTransportError):
            self.transport.live_counts(self.path, ())
        self.assertIn(b"secrets/touched.txt",
                      self.transport.live_inventory(self.path, ())["data"])

    def test_no_read_that_can_hash_reaches_a_private_body(self):
        reads = {
            "inventory": self.transport.live_inventory(self.path, self.LOCATIONS),
            "counts": self.transport.live_counts(self.path, self.LOCATIONS),
            "modes": self.transport.live_modes(self.path, self.LOCATIONS),
        }
        for name, capture in reads.items():
            with self.subTest(name):
                self.assertEqual(capture["status"], "captured")
                self.assertNotIn(b"secrets", capture["data"])
                self.assertIn(b"src/app.py", capture["data"])
                # the blob ids git computes from a private BODY appear nowhere
                for body in (b"BODY-TWO-ABCDEFGHIJ\n", b"BODY-NEW-YYYYYYYYYYYY\n"):
                    self.assertNotIn(blob_id(body), capture["data"])

    def test_the_private_names_and_kinds_come_from_reads_that_open_no_file(self):
        named = self.transport.live_names(self.path)
        names = set(named["names"].split(b"\x00")) - {b""}
        self.assertEqual(names, {b"src/app.py", b"docs/readme.md",
                                 b"secrets/key.txt", b"secrets/touched.txt",
                                 b"secrets/staged.txt"})
        staged = named["staged"].split(b"\x00")
        self.assertIn(b"secrets/staged.txt", staged)
        self.assertIn(b"secrets/gone.txt", staged)
        self.assertNotIn(b"secrets/key.txt", staged)
        recorded = self.transport.live_index_stat(self.path, self.LOCATIONS)
        self.assertEqual(recorded["status"], "captured")
        for name in (b"secrets/key.txt", b"secrets/touched.txt",
                     b"secrets/staged.txt"):
            self.assertIn(name, recorded["data"], "the index's record of it")
        self.assertNotIn(b"src/app.py", recorded["data"],
                         "only the locations asked for")

    def test_the_index_record_is_read_without_opening_a_working_file(self):
        # The PHYSICAL attempt is observed (ReadWitness: a clean filter that logs
        # the working files git opens), over private files whose recorded time
        # is later than the index's own at ANY distance: git's own comparison
        # opens each of them; the read of the index's record opens none
        repo = self.repo("racy")
        recorded = {"secrets/near.txt": time.time() + 72 * 3600,
                    "secrets/year.txt": time.time() + 3650 * 86400,
                    "secrets/far.txt": 4294967295}
        for relative, when in recorded.items():
            os.utime(repo.write(relative, "BODY-ONE-0123456789\n"), (when, when))
        repo.write("src/a.py", "a\n")
        repo.stage("secrets", "src")
        repo.save("base")
        witness = ReadWitness(self, repo)
        subprocess.run(
            ["git", "-C", repo.path, "--no-optional-locks", "-c",
             "filter.witness.clean=" + witness.command, "diff-files", "--raw",
             "-z"], capture_output=True, env=CLEAN_ENV)
        self.assertLessEqual(set(recorded), witness.reads(),
                             "the control: git's own diff-files opens each entry")
        witness.reset()
        with witness:
            result = self.transport.live_index_stat(repo.path, ("secrets",))
        for relative in recorded:
            self.assertIn(relative.encode(), result["data"])
        self.assertEqual(witness.reads(), set(), "the read opened no file")

    def test_a_selected_file_is_selected_as_a_file_and_nothing_under_it(self):
        repo = self.repo("selected")
        repo.write("src/app.py", "def main():\n    return 1\n")
        repo.write("lib/a.txt", "AAAA\n")
        repo.write("lib/b.txt", "BBBB\n")
        repo.stage("src", "lib")
        repo.save("base")
        repo.write("src/app.py", "def main():\n    return 2\n")
        repo.write("lib/a.txt", "AAAB\n")
        repo.write("lib/b.txt", "BBBC\n")
        for relative in ("src/app.py", "lib/a.txt", "lib/b.txt"):
            future_mtime(os.path.join(repo.path, *relative.split("/")))
        witness = ReadWitness(self, repo)
        t = self.transport
        with witness:
            expanded = t.live_file_diff(repo.path, ["lib"], ())
            self.assertIn(b"lib/a.txt", expanded["data"],
                          "the control: a directory as a pathspec expands")
            self.assertTrue(witness.reads() >= {"lib/a.txt", "lib/b.txt"})
            witness.reset()
            nothing = t.live_file_diff(repo.path, ["lib"], (), None, True)
            self.assertEqual(nothing["data"], b"",
                             "as a FILE, a directory selects nothing")
            self.assertEqual(witness.reads(), set(),
                             "and nothing under it is opened")
            one = t.live_file_diff(repo.path, ["src/app.py"], (), None, True)
            self.assertIn(b"+    return 2", one["data"])
            self.assertEqual(witness.reads(), {"src/app.py"})

    def test_the_private_index_is_one_immutable_copy_and_says_when_it_is_not(self):
        with self.transport.live_view(self.path) as view:
            index = os.path.join(view.env["GIT_DIR"], "index")
            self.assertEqual(os.stat(index).st_mode & 0o222, 0, "read-only")
            view.verify()
            with open(index, "rb") as handle:
                original = handle.read()
            os.chmod(index, 0o644)
            with open(index, "wb") as handle:
                handle.write(original[:-1] + bytes([original[-1] ^ 1]))
            with self.assertRaises(git_transport.GitTransportError):
                view.verify()

    def test_a_private_path_is_refused_before_git_runs(self):
        with mock.patch.object(git_transport.GitTransport, "_stream",
                               side_effect=AssertionError("git ran")):
            for asked in (["secrets/key.txt"], ["Secrets/KEY.txt"],
                          ["docs/readme.md", "secrets/sub/deep.txt"]):
                with self.subTest(asked=asked):
                    with self.assertRaises(git_transport.GitTransportError):
                        self.transport.live_file_diff(
                            self.path, asked, self.LOCATIONS)

    def test_the_exclusion_is_finite_and_a_location_is_a_contained_path(self):
        clean = self.repo("clean")
        clean.write("a.txt", "a\n")
        clean.stage("a.txt")
        clean.save("base")
        t, p = self.transport, clean.path
        with mock.patch.object(git_transport, "MAX_REVIEW_EXCLUSIONS", 2):
            self.assertEqual(t.live_counts(p, ("a", "b"))["status"], "captured")
            with self.assertRaises(git_transport.GitBoundRefused) as caught:
                t.live_counts(p, ("a", "b", "c"))
            self.assertEqual((caught.exception.observed,
                              caught.exception.bound), (3, 2))
        with mock.patch.object(git_transport, "MAX_REVIEW_EXCLUSION_BYTES", 5):
            self.assertEqual(t.live_inventory(p, ("abcde",))["status"],
                             "captured")
            with self.assertRaises(git_transport.GitBoundRefused):
                t.live_inventory(p, ("abcdef",))
            with self.assertRaises(git_transport.GitBoundRefused):
                t.live_index_stat(p, ("abc", "def"))
        for bad in ("", ".", "..", "a/./b", "a/../b", "/abs", "a//b", "a/",
                    "a\x00b"):
            with self.subTest(bad=bad):
                with self.assertRaises(git_transport.GitTransportError):
                    t.live_inventory(p, (bad,))
        with mock.patch.object(git_transport, "MAX_REVIEW_NAMES_BYTES", 3):
            self.assertEqual(t.live_names(p)["status"], "refused_over_bound")
        with mock.patch.object(git_transport, "MAX_REVIEW_INDEX_RECORD_BYTES", 10):
            self.assertEqual(t.live_index_stat(p, ("a.txt",))["status"],
                             "refused_over_bound")
        self.assertEqual(t.live_index_stat(p, ("a.txt",))["status"], "captured")

    def test_the_exclusion_is_in_gits_own_pathspec_spelled_literally(self):
        recorded = []
        real = git_transport.GitTransport._stream

        def spy(transport, argv, *args, **kwargs):
            recorded.append((list(argv), kwargs.get("env")))
            return real(transport, argv, *args, **kwargs)

        odd = ":(top)secrets.txt"       # a name that LOOKS like pathspec magic
        with mock.patch.object(git_transport.GitTransport, "_stream", spy):
            with self.transport.live_view(self.path) as view:
                self.transport.live_inventory(self.path, ("secrets", odd), view)
                self.transport.live_counts(self.path, ("secrets",), view)
                self.transport.live_modes(self.path, (), view)
                self.transport.live_index_stat(self.path, ("secrets",), view)
        by_verb = dict((argv[argv.index("-C") + 2], (argv, env))
                       for argv, env in recorded if "-C" in argv)
        status, _env = by_verb["status"]
        self.assertEqual(status[status.index("--"):], [
            "--", ".", ":(top,literal,exclude)secrets",
            ":(top,literal,exclude):(top)secrets.txt"])
        self.assertNotIn("--literal-pathspecs", status,
                         "magic is on only where every pathspec spells it out")
        self.assertIn("--no-optional-locks", status)
        counts = [a for a, _e in recorded if "--numstat" in a][0]
        self.assertEqual(counts[counts.index("--"):],
                         ["--", ".", ":(top,literal,exclude)secrets"])
        modes = [a for a, _e in recorded if "--raw" in a
                 and "--cached" not in a and "diff-index" in a][0]
        self.assertNotIn("--", modes, "nothing left out: no pathspec at all")
        self.assertIn("--literal-pathspecs", modes)
        stat, env = [(a, e) for a, e in recorded if "--debug" in a][0]
        self.assertEqual(stat[stat.index("ls-files"):],
                         ["ls-files", "-z", "-s", "--debug", "--",
                          ":(top,literal)secrets"])
        self.assertNotIn("diff-files", [a for argv, _e in recorded
                                        for a in argv],
                         "git is never asked to compare a working file for it")
        self.assertEqual(set(env) - set(git_transport._LIVE_READ_ENV),
                         {"GIT_DIR", "GIT_WORK_TREE"},
                         "the very index every other read of the view uses")

    def test_every_verb_that_can_hash_requires_the_locations_to_leave_out(self):
        import inspect
        live = {name for name in dir(git_transport.GitTransport)
                if name.startswith("live_")
                and callable(getattr(git_transport.GitTransport, name))}
        self.assertEqual(live, self.OPENS_NO_FILE | self.CAN_HASH,
                         "a new live verb must be classified here, deliberately")
        for name in sorted(self.CAN_HASH):
            parameter = inspect.signature(getattr(
                git_transport.GitTransport, name)).parameters.get("exclude")
            self.assertIsNotNone(parameter, name)
            self.assertIs(parameter.default, inspect.Parameter.empty,
                          name + ": no default that would read everything")

    def test_hostile_names_are_text_never_patterns(self):
        names = (":(top)secrets.txt", "-secret.txt", "weird*/secret?.txt",
                 "new\nline/secret.txt", "caf\u00e9/secret.txt")
        repo = self.repo("hostile")
        repo.write("docs/ok.txt", "a\n")
        for name in names:
            repo.write(name, "BODY-A-%d\n" % len(name))
        repo.git("add", "-A")
        repo.save("base")
        repo.write("docs/ok.txt", "b\n")
        for name in names:
            repo.write(name, "BODY-B-%d\n" % len(name))
            future_mtime(os.path.join(repo.path, name))
            os.chmod(os.path.join(repo.path, name), 0)
            self.addCleanup(os.chmod, os.path.join(repo.path, name), 0o600)
        t = self.transport
        for label, capture in (
                ("inventory", t.live_inventory(repo.path, names)),
                ("counts", t.live_counts(repo.path, names)),
                ("modes", t.live_modes(repo.path, names))):
            with self.subTest(label):
                self.assertEqual(capture["status"], "captured")
                self.assertIn(b"docs/ok.txt", capture["data"])
                for name in names:
                    self.assertNotIn(name.encode(), capture["data"])
        stat = t.live_index_stat(repo.path, names)["data"]
        for name in names:
            self.assertIn(name.encode(), stat)


class ControlTests(FixtureCase):
    """The controls: the same fixture, and the verbs the product must NOT
    use. If these stopped mutating, the fixture would no longer prove
    anything about the verbs that must not."""

    def mutates(self, *args):
        repo = self.busy(name="control-%d" % abs(hash(args)))
        before = git_state(repo.path)
        repo.git(*args)
        return digest(git_state(repo.path)) != digest(before)

    def test_the_git_version_is_recorded(self):
        version = subprocess.run(["git", "--version"], capture_output=True,
                                 env=CLEAN_ENV).stdout.decode().strip()
        print("\ngit under test: %s" % version)
        self.assertTrue(version.startswith("git version "))

    def test_diff_rewrites_the_index_even_without_optional_locks(self):
        self.assertTrue(self.mutates("diff", "HEAD"))
        self.assertTrue(self.mutates("--no-optional-locks", "diff", "HEAD"))

    def test_status_rewrites_the_index_unless_optional_locks_are_off(self):
        self.assertTrue(self.mutates("status", "--porcelain"))
        self.assertFalse(self.mutates("--no-optional-locks", "status",
                                      "--porcelain"))


class LiveVerbsLeaveTheTargetUntouchedTests(FixtureCase):
    """THE acceptance bar: every verb the live review points at a target."""

    def verbs(self, transport, path):
        def view_only():
            with transport.live_view(path) as view:
                return view.oid
        return (
            ("live_view", view_only),
            ("live_toplevel", lambda: transport.live_toplevel(path)),
            ("live_head", lambda: transport.live_head(path)),
            ("live_inventory", lambda: transport.live_inventory(path, ())),
            ("live_counts", lambda: transport.live_counts(path, ())),
            ("live_modes", lambda: transport.live_modes(path, ())),
            # round 20 F2: the same four with a location left out, and the two
            # reads that decide and list the private ones from names and stat
            ("live_inventory (a location left out)",
             lambda: transport.live_inventory(path, (NOTES_DIR, "src/util.py"))),
            ("live_counts (a location left out)",
             lambda: transport.live_counts(path, (NOTES_DIR, "src/util.py"))),
            ("live_modes (a location left out)",
             lambda: transport.live_modes(path, (NOTES_DIR, "src/util.py"))),
            ("live_names", lambda: transport.live_names(path)),
            ("live_index_stat", lambda: transport.live_index_stat(
                path, ["src", NOTES_DIR])),
            ("live_file_diff (files only)", lambda: transport.live_file_diff(
                path, ["src/app.py"], (), None, True)),
            ("live_file_diff", lambda: transport.live_file_diff(
                path, ["src/app.py"], ())),
            ("live_file_diff (rename pair)", lambda: transport.live_file_diff(
                path, ["src/mover.py", "src/moved.py"], ())),
            ("live_file_diff (everything)", lambda: transport.live_file_diff(
                path, ["src", "docs", "bin", "touched.txt", "crlf.txt"], ())),
        )

    def check(self, repo):
        transport = git_transport.GitTransport()
        before = git_state(repo.path)
        self.assertTrue(before, "the git state is not empty")
        # The fixture IS stat-dirty: a refreshing verb would rewrite it.
        probe = run_git(repo.path, "diff-index", "--quiet", "HEAD", "--",
                        "touched.txt", check=False)
        self.assertEqual(probe.returncode, 1, "touched.txt is stat-dirty")
        for name, verb in self.verbs(transport, repo.path):
            with self.subTest(verb=name):
                result = verb()
                self.assertIsNotNone(result)
                self.assertEqual(digest(git_state(repo.path)), digest(before),
                                 "%s changed the target's git state" % name)

    def test_a_plain_repository(self):
        self.check(self.busy())

    def test_a_linked_worktree_the_shape_a_mission_works_in(self):
        repo = self.busy(linked=True)
        states = git_state(repo.path)
        self.assertEqual(len(states), 2, "worktree git dir AND common dir")
        self.check(repo)


class WhatTheVerbsReadTests(FixtureCase):

    def setUp(self):
        super(WhatTheVerbsReadTests, self).setUp()
        self.repo_ = self.busy()
        self.transport = git_transport.GitTransport()

    def test_toplevel_and_head(self):
        self.assertEqual(self.transport.live_toplevel(self.repo_.path),
                         self.repo_.path)
        head = self.transport.live_head(self.repo_.path)
        self.assertEqual(head["ref"], "refs/heads/main")
        self.assertEqual(head["oid"], self.repo_.git(
            "rev-parse", "HEAD").stdout.decode().strip())

    def test_a_detached_head_has_no_ref(self):
        self.repo_.git("checkout", "-q", "--detach")
        head = self.transport.live_head(self.repo_.path)
        self.assertIsNone(head["ref"])

    def test_an_unborn_branch_has_no_commit_to_compare(self):
        empty = self.repo("empty")
        with self.assertRaises(git_transport.GitTransportError):
            self.transport.live_head(empty.path)

    def test_the_inventory_is_status_with_no_rename_pairing(self):
        capture = self.transport.live_inventory(self.repo_.path, ())
        self.assertEqual(capture["status"], git_transport.CAPTURE_CAPTURED)
        entries = [e for e in capture["data"].split(b"\x00") if e]
        by_path = dict((e[3:].decode(), e[:2].decode()) for e in entries)
        self.assertEqual(by_path["src/app.py"], "MM")
        self.assertEqual(by_path["src/util.py"], " M")
        self.assertEqual(by_path["docs/gone.md"], "D ")
        self.assertEqual(by_path["src/mover.py"], "D ")
        self.assertEqual(by_path["src/moved.py"], "AM")
        self.assertEqual(by_path["notes/new.txt"], "??")
        self.assertEqual(by_path[".env"], "??")
        self.assertNotIn("touched.txt", by_path,
                         "touched, not changed: git lists nothing")

    def test_the_counts_pair_the_rename_and_mark_the_binary(self):
        capture = self.transport.live_counts(self.repo_.path, ())
        self.assertEqual(capture["status"], git_transport.CAPTURE_CAPTURED)
        text = capture["data"].decode()
        self.assertIn("-\t-\tbin/blob.bin\x00", text)
        self.assertIn("\x00src/mover.py\x00src/moved.py\x00", text)

    def test_the_file_diff_is_head_against_the_working_tree(self):
        capture = self.transport.live_file_diff(self.repo_.path, ["src/app.py"], ())
        text = capture["data"].decode()
        self.assertIn("-    return 1\n+    return 3\n", text)
        self.assertNotIn("return 2", text, "staged and unstaged, net")

    def test_a_pathspec_is_literal_text(self):
        # a glob and a magic pathspec name nothing: they match no file
        for spec in ("src/*.py", ":(glob)**/*.py", ":/src/app.py"):
            with self.subTest(spec=spec):
                capture = self.transport.live_file_diff(self.repo_.path, [spec], ())
                self.assertEqual(capture["data"], b"")


class RepositoryAuthoredConfigurationTests(FixtureCase):
    """Config the REPOSITORY (or the agent) authored must not run a program
    for a live read, and the parent's environment must not steer it."""

    def marker(self, name):
        return os.path.join(self.root, name)

    def arm(self, repo, key, command):
        repo.git("config", "--local", key, command)

    def test_an_external_diff_program_never_runs(self):
        repo = self.busy()
        script = os.path.join(self.root, "extdiff.sh")
        with open(script, "w") as handle:
            handle.write("#!/bin/sh\ntouch %s\n" % self.marker("ext-ran"))
        os.chmod(script, 0o755)
        self.arm(repo, "diff.external", script)
        git_transport.GitTransport().live_file_diff(repo.path, ["src/app.py"], ())
        self.assertFalse(os.path.exists(self.marker("ext-ran")))
        # control: the unpinned verb DOES run it, so the marker can appear
        run_git(repo.path, "diff", "HEAD", "--", "src/app.py")
        self.assertTrue(os.path.exists(self.marker("ext-ran")))

    def test_a_textconv_program_never_runs(self):
        repo = self.busy()
        script = os.path.join(self.root, "textconv.sh")
        with open(script, "w") as handle:
            handle.write("#!/bin/sh\ntouch %s\ncat \"$1\"\n"
                         % self.marker("textconv-ran"))
        os.chmod(script, 0o755)
        repo.write(".gitattributes", "*.py diff=probe\n")
        self.arm(repo, "diff.probe.textconv", script)
        git_transport.GitTransport().live_file_diff(repo.path, ["src/app.py"], ())
        self.assertFalse(os.path.exists(self.marker("textconv-ran")))
        run_git(repo.path, "diff", "HEAD", "--", "src/app.py")
        self.assertTrue(os.path.exists(self.marker("textconv-ran")))

    def test_a_filesystem_monitor_program_never_runs(self):
        repo = self.busy()
        script = os.path.join(self.root, "fsmonitor.sh")
        with open(script, "w") as handle:
            handle.write("#!/bin/sh\ntouch %s\nprintf '\\0'\n"
                         % self.marker("fsmonitor-ran"))
        os.chmod(script, 0o755)
        self.arm(repo, "core.fsmonitor", script)
        transport = git_transport.GitTransport()
        transport.live_inventory(repo.path, ())
        transport.live_counts(repo.path, ())
        transport.live_file_diff(repo.path, ["src/app.py"], ())
        self.assertFalse(os.path.exists(self.marker("fsmonitor-ran")))

    def arm_clean_filter(self, repo, name="probe", required=False,
                         key="clean"):
        script = os.path.join(self.root, "clean.sh")
        with open(script, "w") as handle:
            handle.write("#!/bin/sh\ntouch %s\ncat\n"
                         % self.marker("clean-ran"))
        os.chmod(script, 0o755)
        repo.write(".gitattributes",
                   "*.py filter=%s\n*.txt filter=%s\n" % (name, name))
        self.arm(repo, "filter.%s.%s" % (name, key), script)
        if required:
            self.arm(repo, "filter.%s.required" % name, "true")

    def read_everything(self, transport, repo):
        # tracked files are stat-dirty (touched.txt, util.py): git must
        # re-hash their content to know they are unchanged, and a clean
        # filter is the program it would run to do so
        transport.live_inventory(repo.path, ())
        transport.live_counts(repo.path, ())
        transport.live_file_diff(repo.path, ["src/util.py"], ())
        transport.live_file_diff(repo.path, ["touched.txt"], ())

    def test_a_clean_filter_runs_in_a_read_under_the_repositorys_own_config(
            self):
        # THE CONTROL (a real finding): the pins that stop an external diff,
        # a textconv and an fsmonitor do NOT stop a clean filter. The same
        # verb under the same pins, in the repository's own configuration,
        # runs the marker program — so the hostile fixture is effective and
        # its silence in the tests below means something.
        repo = self.busy()
        self.arm_clean_filter(repo)
        run_git_completed(
            list(git_transport._LIVE_READ_PINS)
            + ["-C", repo.path, "diff-index", "--numstat", "-z", "-M", "HEAD"],
            env=CLEAN_ENV, check=True, text=False)
        self.assertTrue(os.path.exists(self.marker("clean-ran")))

    def test_a_clean_filter_never_runs_in_a_live_read(self):
        for name, required in (("probe", False), ("probe", True),
                               ("lfs.like", True)):
            with self.subTest(name=name, required=required):
                repo = self.busy(name="ws-%s-%s" % (name, required))
                self.arm_clean_filter(repo, name, required)
                transport = git_transport.GitTransport()
                self.assertEqual(transport.live_filter_drivers(repo.path),
                                 [name])
                self.read_everything(transport, repo)
                self.assertFalse(os.path.exists(self.marker("clean-ran")))

    def test_a_driver_that_appears_after_it_was_enumerated_never_runs(self):
        # THE RACE the lead and root rejected the rationale for: the drivers
        # are listed, and ONLY THEN does the repository's configuration gain
        # one. A per-name override cannot cover a name it never saw; a read
        # that never reads the repository's configuration has no driver to
        # find. Plain repository and linked worktree (where a Mission works),
        # a clean driver and a long-running process driver, required or not.
        for linked in (False, True):
            for key, required in (("clean", False), ("clean", True),
                                  ("process", False)):
                with self.subTest(linked=linked, key=key, required=required):
                    repo = self.busy(name="race-%s-%s-%s" % (
                        linked, key, required), linked=linked)
                    transport = git_transport.GitTransport()
                    self.assertEqual(
                        transport.live_filter_drivers(repo.path), [])
                    self.arm_clean_filter(repo, "late", required, key)
                    self.read_everything(transport, repo)
                    self.assertFalse(
                        os.path.exists(self.marker("clean-ran")))

    def test_attributes_from_the_repositorys_info_directory_select_nothing(
            self):
        repo = self.busy()
        script = os.path.join(self.root, "clean.sh")
        with open(script, "w") as handle:
            handle.write("#!/bin/sh\ntouch %s\ncat\n"
                         % self.marker("clean-ran"))
        os.chmod(script, 0o755)
        git_dir = repo.git("rev-parse", "--path-format=absolute",
                           "--git-common-dir").stdout.decode().strip()
        os.makedirs(os.path.join(git_dir, "info"), exist_ok=True)
        with open(os.path.join(git_dir, "info", "attributes"), "w") as handle:
            handle.write("* filter=late\n")
        self.arm(repo, "filter.late.clean", script)
        self.read_everything(git_transport.GitTransport(), repo)
        self.assertFalse(os.path.exists(self.marker("clean-ran")))

    def test_the_verbs_that_read_the_real_configuration_run_nothing_it_names(
            self):
        # rev-parse and config --get-regexp still read the repository's own
        # configuration (the private directory needs to be told where its
        # target is). MEASURED, with a marker program behind every key that
        # names a program: none of them runs one.
        repo = self.busy()
        directory = os.path.join(self.root, "markers")
        os.makedirs(directory)

        def program(name):
            path = os.path.join(directory, name + ".sh")
            with open(path, "w") as handle:
                handle.write("#!/bin/sh\ntouch '%s/%s.ran'\ncat\n"
                             % (directory, name))
            os.chmod(path, 0o755)
            return path
        for key in ("core.pager", "core.editor", "core.askPass",
                    "core.sshCommand", "credential.helper", "gpg.program",
                    "core.alternateRefsCommand", "uploadpack.packObjectsHook",
                    "diff.external", "core.fsmonitor", "diff.late.textconv",
                    "diff.late.command", "filter.late.clean",
                    "filter.late.process"):
            self.arm(repo, key, program(key.replace(".", "-")))
        self.arm(repo, "alias.rp", "!" + program("alias"))
        hooks = os.path.join(self.root, "hooks")
        os.makedirs(hooks)
        for hook in ("post-checkout", "post-index-change", "pre-commit",
                     "reference-transaction", "fsmonitor-watchman"):
            os.symlink(program("hook-" + hook), os.path.join(hooks, hook))
        self.arm(repo, "core.hooksPath", hooks)
        repo.write(".gitattributes", "*.py diff=late filter=late\n")
        transport = git_transport.GitTransport()
        transport.live_toplevel(repo.path)
        transport.live_head(repo.path)
        transport.live_filter_drivers(repo.path)
        self.read_everything(transport, repo)
        self.assertEqual(
            [name for name in os.listdir(directory) if name.endswith(".ran")],
            [])

    def test_the_drivers_are_what_the_repositorys_own_config_defines(self):
        repo = self.busy()
        transport = git_transport.GitTransport()
        self.assertEqual(transport.live_filter_drivers(repo.path), [])
        self.arm(repo, "filter.b.smudge", "true")
        self.arm(repo, "filter.a.process", "true")
        self.arm(repo, "filter.a.required", "true")
        self.arm(repo, "filter.c.unrelated", "true")
        self.assertEqual(transport.live_filter_drivers(repo.path), ["a", "b"])

    def test_more_drivers_than_the_bound_refuse_the_read(self):
        repo = self.busy()
        for number in range(git_transport.MAX_REVIEW_FILTER_DRIVERS + 1):
            self.arm(repo, "filter.d%d.clean" % number, "true")
        with self.assertRaises(git_transport.GitTransportError):
            git_transport.GitTransport().live_filter_drivers(repo.path)

    def test_the_parents_git_environment_cannot_steer_a_live_read(self):
        repo = self.busy()
        other = self.repo("elsewhere")
        other.write("x.txt", "x\n")
        other.stage("x.txt")
        other.save("other base")
        hostile = {
            "GIT_DIR": os.path.join(other.path, ".git"),
            "GIT_WORK_TREE": other.path,
            "GIT_INDEX_FILE": os.path.join(self.root, "nowhere", "index"),
            "GIT_EXTERNAL_DIFF": "/bin/false",
            "GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "diff.external",
            "GIT_CONFIG_VALUE_0": "/bin/false",
        }
        transport = git_transport.GitTransport()
        with mock.patch.dict(os.environ, hostile):
            self.assertEqual(transport.live_toplevel(repo.path), repo.path)
            inventory = transport.live_inventory(repo.path, ())
            diff = transport.live_file_diff(repo.path, ["src/app.py"], ())
        self.assertIn(b" src/app.py", b" " + inventory["data"])
        self.assertIn(b"+    return 3", diff["data"])


class PrivateViewTests(FixtureCase):
    """The git directory a content-reading live read goes through: what it
    holds, what it carries over, what it refuses, and that it says what the
    repository's own directory would have said."""

    STATUS = ["status", "--porcelain=v1", "-z", "--no-renames",
              "--untracked-files=all", "--ignore-submodules=all"]
    COUNTS = ["diff-index", "--numstat", "-z", "-M",
              "--ignore-submodules=all", "HEAD"]
    PATCH = ["diff-index", "-p", "-M", "--unified=3", "--no-color",
             "--no-ext-diff", "--no-textconv", "--ignore-submodules=all",
             "HEAD", "--"]

    def real(self, repo, *verb):
        """The same verb under the same pins, in the repository's OWN git
        directory (a fixture with no filter configured, so it is safe)."""
        return run_git_completed(
            list(git_transport._LIVE_READ_PINS)
            + ["-C", repo.path] + list(verb),
            env=CLEAN_ENV, check=True, text=False).stdout

    def assert_same_reads(self, repo):
        transport = git_transport.GitTransport()
        paths = ["src/app.py", "src/util.py", "crlf.txt"]
        self.assertEqual(transport.live_inventory(repo.path, ())["data"],
                         self.real(repo, *self.STATUS))
        self.assertEqual(transport.live_counts(repo.path, ())["data"],
                         self.real(repo, *self.COUNTS))
        self.assertEqual(transport.live_file_diff(repo.path, paths, ())["data"],
                         self.real(repo, *(self.PATCH + paths)))

    def test_a_private_read_says_what_the_repositorys_own_read_says(self):
        for linked in (False, True):
            with self.subTest(linked=linked):
                self.assert_same_reads(
                    self.busy(name="same-%s" % linked, linked=linked))

    def test_the_settings_that_change_what_a_listing_says_come_along(self):
        repo = self.busy()
        os.chmod(os.path.join(repo.path, "src", "util.py"), 0o755)
        transport = git_transport.GitTransport()
        for key, value in (("core.filemode", "false"), ("core.autocrlf", "true"),
                           ("core.autocrlf", "input"), ("core.eol", "crlf"),
                           ("core.symlinks", "false"),
                           ("core.bigfilethreshold", "1k")):
            with self.subTest(key=key, value=value):
                repo.git("config", "--local", key, value)
                self.assert_same_reads(repo)
                repo.git("config", "--local", "--unset", key)
        # the setting is not decoration: it decides whether a mode change shows
        shown = transport.live_file_diff(repo.path, ["src/util.py"], ())["data"]
        self.assertIn(b"old mode 100644", shown)
        repo.git("config", "--local", "core.filemode", "false")
        hidden = transport.live_file_diff(repo.path, ["src/util.py"], ())["data"]
        self.assertNotIn(b"old mode", hidden)

    def test_a_racily_clean_file_is_still_seen_as_changed(self):
        # git cannot trust a file's stat when it is not older than the index
        # file it was recorded in (a change made in the same instant leaves
        # the stat unchanged), so it compares the CONTENT. That rule reads
        # the index file's own mtime — so a copy of the index must carry the
        # original's, or a same-size edit made just after a commit is not
        # seen. Built exactly: an entry, the index file and the working file
        # all at one instant, ctime ignored, the content changed in place at
        # the same size.
        repo = self.repo()
        repo.git("config", "core.trustctime", "false")
        repo.write("f.txt", "return 1\n")
        # older than the index it is added to, so git does not smudge the
        # entry when it writes it (a smudged entry is found by its size, which
        # would hide whether the index file's mtime is carried over)
        long_ago = time.time_ns() - 60 * 1_000_000_000
        os.utime(os.path.join(repo.path, "f.txt"), ns=(long_ago, long_ago))
        repo.stage("f.txt")
        repo.save("base")
        git_dir = repo.git("rev-parse", "--path-format=absolute",
                           "--absolute-git-dir").stdout.decode().strip()
        index = os.path.join(git_dir, "index")
        path = os.path.join(repo.path, "f.txt")
        instant = os.stat(path).st_mtime_ns
        os.utime(index, ns=(instant, instant))
        repo.write("f.txt", "return 2\n")            # the same size
        os.utime(path, ns=(instant, instant))
        os.utime(index, ns=(instant, instant))
        transport = git_transport.GitTransport()
        real = self.real(repo, *self.STATUS)
        self.assertIn(b" M f.txt", b" " + real,
                      "the fixture is racy: git itself sees the change")
        self.assertEqual(transport.live_inventory(repo.path, ())["data"], real)
        self.assertEqual(transport.live_counts(repo.path, ())["data"],
                         self.real(repo, *self.COUNTS))
        self.assertIn(b"+return 2",
                      transport.live_file_diff(repo.path, ["f.txt"], ())["data"])

    def test_a_read_reports_the_commit_it_compared_against(self):
        repo = self.busy()
        head = repo.git("rev-parse", "HEAD").stdout.decode().strip()
        transport = git_transport.GitTransport()
        for capture in (transport.live_inventory(repo.path, ()),
                        transport.live_counts(repo.path, ()),
                        transport.live_file_diff(repo.path, ["src/app.py"], ())):
            self.assertEqual(capture["head"], head)

    def test_the_private_configuration_holds_only_the_allow_listed_settings(
            self):
        repo = self.busy()
        for key, value in (
                ("core.filemode", "false"), ("core.autocrlf", "input"),
                ("filter.x.clean", "touch /x"), ("diff.external", "/bin/false"),
                ("core.fsmonitor", "/x"), ("core.hooksPath", "/x"),
                ("core.editor", "vi"), ("core.pager", "less"),
                ("include.path", "/x"), ("url.http://h/.insteadOf", "y"),
                ("remote.origin.url", "http://h/r"),
                ("remote.origin.promisor", "true"),
                ("extensions.partialclone", "origin"),
                ("submodule.s.update", "!x")):
            repo.git("config", "--local", key, value)
        transport = git_transport.GitTransport()
        with transport._live_view(repo.path) as (oid, env):
            scratch = env["GIT_DIR"]
            with open(os.path.join(scratch, "config")) as handle:
                lines = [line.strip() for line in handle.read().splitlines()]
            self.assertEqual(env["GIT_WORK_TREE"], repo.path)
            self.assertTrue(os.path.isdir(scratch))
        carried = dict(line.split(" = ", 1) for line in lines if " = " in line)
        # what the repository set is carried; what git itself wrote at init on
        # this machine (case folding, unicode composition) is too; NOTHING ELSE
        self.assertEqual(carried["filemode"], "false")
        self.assertEqual(carried["autocrlf"], "input")
        self.assertEqual(carried["repositoryformatversion"], "0")
        self.assertLessEqual(
            set(carried),
            set(git_transport._VIEW_CORE_KEYS) | {"repositoryformatversion"})
        self.assertEqual(lines[0], "[core]")
        self.assertEqual(len(lines), 1 + len(carried), "no other section")
        for hostile in ("filter", "fsmonitor", "hookspath", "editor", "pager",
                        "include", "insteadof", "remote", "promisor",
                        "partialclone", "submodule", "external"):
            self.assertFalse([l for l in lines if hostile in l.lower()], hostile)
        self.assertFalse(os.path.exists(scratch), "removed when the read ends")

    def test_the_private_directory_borrows_objects_and_writes_none(self):
        repo = self.busy()
        before = digest(git_state(repo.path))
        transport = git_transport.GitTransport()
        with transport._live_view(repo.path) as (_oid, env):
            scratch = env["GIT_DIR"]
            for verb in (self.STATUS, self.COUNTS, self.PATCH + ["src/util.py"]):
                result = transport._live_capture(repo.path, verb, 1 << 20,
                                                 env=env)
                self.assertEqual(result["status"],
                                 git_transport.CAPTURE_CAPTURED)
            self.assertEqual(os.listdir(os.path.join(scratch, "objects")),
                             ["info"])
            self.assertEqual(
                os.listdir(os.path.join(scratch, "objects", "info")),
                ["alternates"])
        self.assertEqual(digest(git_state(repo.path)), before)

    def test_a_setting_of_a_shape_a_read_cannot_reproduce_refuses_the_read(
            self):
        repo = self.busy()
        repo.git("config", "--local", "core.eol", "sideways")
        with self.assertRaises(git_transport.GitTransportError):
            git_transport.GitTransport().live_inventory(repo.path, ())

    def test_an_index_past_its_bound_refuses_with_its_exact_size(self):
        repo = self.busy()
        git_dir = repo.git("rev-parse", "--path-format=absolute",
                           "--absolute-git-dir").stdout.decode().strip()
        size = os.path.getsize(os.path.join(git_dir, "index"))
        with mock.patch.object(git_transport, "MAX_REVIEW_INDEX_BYTES",
                               size - 1):
            with self.assertRaises(git_transport.GitViewRefused) as caught:
                git_transport.GitTransport().live_inventory(repo.path, ())
        self.assertEqual(caught.exception.observed, size)
        self.assertEqual(caught.exception.bound, size - 1)

    def test_an_index_that_is_not_a_regular_file_refuses_the_read(self):
        repo = self.busy()
        git_dir = repo.git("rev-parse", "--path-format=absolute",
                           "--absolute-git-dir").stdout.decode().strip()
        index = os.path.join(git_dir, "index")
        os.rename(index, index + ".real")
        os.symlink(index + ".real", index)
        with self.assertRaises(git_transport.GitTransportError):
            git_transport.GitTransport().live_inventory(repo.path, ())

    def test_an_info_file_that_is_a_symlink_is_not_followed(self):
        repo = self.busy()
        git_dir = repo.git("rev-parse", "--path-format=absolute",
                           "--git-common-dir").stdout.decode().strip()
        elsewhere = os.path.join(self.root, "elsewhere-exclude")
        with open(elsewhere, "w") as handle:
            handle.write("notes/\n")
        os.makedirs(os.path.join(git_dir, "info"), exist_ok=True)
        exclude = os.path.join(git_dir, "info", "exclude")
        if os.path.lexists(exclude):
            os.rename(exclude, exclude + ".real")
        os.symlink(elsewhere, exclude)
        transport = git_transport.GitTransport()
        with transport._live_view(repo.path) as (_oid, env):
            self.assertFalse(os.path.exists(
                os.path.join(env["GIT_DIR"], "info", "exclude")))

    def test_a_split_index_is_refused_not_misread(self):
        # A split index keeps most entries in a shared file in the git
        # directory, which the private directory does not carry: git cannot
        # read the copy, and the read refuses — it never lists a partial index.
        repo = self.busy()
        repo.git("update-index", "--split-index")
        with self.assertRaises(git_transport.GitTransportError):
            git_transport.GitTransport().live_inventory(repo.path, ())

    def test_a_repository_of_another_object_format_refuses_the_read(self):
        repo = Repo.__new__(Repo)
        repo.path = os.path.join(self.root, "sha256")
        os.makedirs(repo.path)
        made = run_git(repo.path, "init", "-q", "--object-format=sha256",
                       check=False)
        if made.returncode != 0:
            self.skipTest("this git has no sha256 repositories")
        repo.git("config", "user.email", "t@example.com")
        repo.git("config", "user.name", "T")
        repo.write("a.txt", "a\n")
        repo.stage("a.txt")
        repo.save("base")
        with self.assertRaises(git_transport.GitTransportError):
            git_transport.GitTransport().live_inventory(repo.path, ())


def submodule_workspace(root, name="ws"):
    """A workspace with a TRACKED submodule whose recorded commit has moved
    (the submodule committed since), uncommitted work INSIDE the submodule, a
    hostile configuration inside the submodule's own git directory, and an
    UNTRACKED nested repository."""
    lib = Repo(root, name + "-lib")
    lib.write("a.txt", "a\n")
    lib.stage("a.txt")
    lib.save("lib base")
    main = Repo(root, name)
    main.write("x.txt", "x\n")
    main.stage("x.txt")
    main.save("main base")
    main.git("-c", "protocol.file.allow=always", "submodule", "add", "-q",
             lib.path, "vendor/lib")
    main.save("add the submodule")
    inner = Repo.__new__(Repo)
    inner.path = os.path.join(main.path, "vendor", "lib")
    inner.git("config", "user.email", "t@example.com")
    inner.git("config", "user.name", "T")
    inner.write("b.txt", "b\n")
    inner.stage("b.txt")
    inner.save("the submodule moved")            # the pointer now differs
    inner.write("dirty.txt", "uncommitted inside the submodule\n")
    inner.write("a.txt", "a changed inside the submodule\n")
    nested = os.path.join(main.path, "vendor", "clone")
    os.makedirs(nested)
    clone = Repo.adopt(nested)
    clone.write("c.txt", "c\n")
    clone.stage("c.txt")
    clone.save("nested base")
    return main


class SubmoduleTests(FixtureCase):
    """A moved submodule pointer is listed; the submodule is never entered
    (so nothing its own configuration names can run); an untracked nested
    repository is one directory entry; and every verb leaves the superproject
    AND the submodule byte-identical."""

    def test_a_moved_pointer_is_listed_and_nothing_inside_it_is(self):
        main = submodule_workspace(self.root)
        transport = git_transport.GitTransport()
        status = transport.live_inventory(main.path, ())["data"]
        self.assertEqual(sorted(r for r in status.split(b"\x00") if r),
                         [b" M vendor/lib", b"?? vendor/clone/"])
        self.assertNotIn(b"dirty.txt", status)
        counts = transport.live_counts(main.path, ())["data"]
        self.assertEqual(counts, b"1\t1\tvendor/lib\x00")
        modes = transport.live_modes(main.path, ())["data"].split(b"\x00")
        self.assertRegex(modes[0].decode(), r"^:160000 160000 [0-9a-f]{40} "
                                            r"[0-9a-f]{40} M$")
        self.assertEqual(modes[1], b"vendor/lib")
        patch = transport.live_file_diff(main.path, ["vendor/lib"], ())["data"]
        self.assertIn(b"-Subproject commit ", patch)
        self.assertIn(b"+Subproject commit ", patch)
        self.assertNotIn(b"-dirty", patch, "what is inside is not looked at")

    def test_no_live_read_runs_a_program_the_submodule_configures(self):
        main = submodule_workspace(self.root)
        sub = os.path.join(main.path, "vendor", "lib")
        script = os.path.join(self.root, "clean.sh")
        with open(script, "w") as handle:
            handle.write("#!/bin/sh\ntouch %s\ncat\n"
                         % os.path.join(self.root, "sub-ran"))
        os.chmod(script, 0o755)
        run_git(sub, "config", "filter.late.clean", script)
        with open(os.path.join(sub, ".gitattributes"), "w") as handle:
            handle.write("*.txt filter=late\n")
        future_mtime(os.path.join(sub, "a.txt"))
        future_mtime(os.path.join(sub, "b.txt"))
        # CONTROL: a read that DOES enter the submodule (the setting this
        # review does not use) runs the submodule's filter, so the fixture
        # is hostile and the silence below means something
        run_git_completed(
            list(git_transport._LIVE_READ_PINS)
            + ["-C", main.path, "status", "--porcelain=v1",
               "--ignore-submodules=none"],
            env=CLEAN_ENV, check=True, text=False)
        self.assertTrue(os.path.exists(os.path.join(self.root, "sub-ran")),
                        "the control read ran the submodule's filter")
        os.rename(os.path.join(self.root, "sub-ran"),
                  os.path.join(self.root, "sub-ran.control"))
        transport = git_transport.GitTransport()
        transport.live_inventory(main.path, ())
        transport.live_counts(main.path, ())
        transport.live_modes(main.path, ())
        transport.live_file_diff(main.path, ["vendor/lib"], ())
        transport.live_file_diff(main.path, ["x.txt"], ())
        self.assertFalse(os.path.exists(os.path.join(self.root, "sub-ran")))

    def test_every_live_read_leaves_the_superproject_and_the_submodule_alone(
            self):
        main = submodule_workspace(self.root)
        sub = os.path.join(main.path, "vendor", "lib")
        transport = git_transport.GitTransport()
        before = (digest(git_state(main.path)), digest(git_state(sub)),
                  tree_state(sub))
        for read in (lambda: transport.live_inventory(main.path, ()),
                     lambda: transport.live_counts(main.path, ()),
                     lambda: transport.live_modes(main.path, ()),
                     lambda: transport.live_file_diff(main.path, ["vendor/lib"], ())):
            with self.subTest(read=read):
                read()
                self.assertEqual(
                    (digest(git_state(main.path)), digest(git_state(sub)),
                     tree_state(sub)), before)

    def test_an_untracked_nested_repository_is_one_directory_entry(self):
        main = submodule_workspace(self.root)
        status = git_transport.GitTransport().live_inventory(main.path, ())["data"]
        self.assertIn(b"?? vendor/clone/\x00", status)
        self.assertNotIn(b"c.txt", status, "git does not enter a nested repo")

    def test_a_pointer_that_moved_in_the_index_is_staged_and_listed(self):
        main = submodule_workspace(self.root)
        main.git("add", "vendor/lib")
        transport = git_transport.GitTransport()
        status = transport.live_inventory(main.path, ())["data"]
        self.assertIn(b"M  vendor/lib\x00", status)
        self.assertIn(b"1\t1\tvendor/lib", transport.live_counts(main.path, ())["data"])


class TransportBoundsTests(FixtureCase):

    def test_a_live_read_past_its_deadline_is_killed_and_refused(self):
        transport = git_transport.GitTransport()
        with mock.patch.object(git_transport, "MAX_REVIEW_GIT_SECONDS", 0.3):
            started = time.monotonic()
            result = transport._stream(["sleep", "30"], 10, 10, live=True)
            elapsed = time.monotonic() - started
        self.assertEqual(result["status"],
                         git_transport.CAPTURE_REFUSED_DEADLINE)
        self.assertIn("total_bytes_lower_bound", result)
        self.assertNotIn("digest", result)
        self.assertLess(elapsed, 10, "the process was killed, not awaited")

    def test_an_output_past_the_ceiling_is_a_refusal_with_a_lower_bound(self):
        repo = self.repo()
        repo.write("big.txt", "x" * 200)
        repo.stage("big.txt")
        repo.save("base")
        repo.write("big.txt", "y" * 5000)
        transport = git_transport.GitTransport()
        with mock.patch.object(git_transport, "MAX_REVIEW_FILE_DIFF_BYTES",
                               300):
            result = transport.live_file_diff(repo.path, ["big.txt"], ())
        self.assertEqual(result["status"],
                         git_transport.CAPTURE_REFUSED_OVER_BOUND)
        self.assertGreater(result["total_bytes_lower_bound"], 300)
        self.assertNotIn("digest", result)
        self.assertNotIn("data", result)

    def test_the_older_streamed_reads_are_unchanged(self):
        # the live flag is opt-in: a caller that does not pass it gets the
        # inherited environment and no deadline, exactly as before
        repo = self.busy()
        transport = git_transport.GitTransport()
        result = transport.status_porcelain_readonly(repo.path)
        self.assertEqual(result["status"], git_transport.CAPTURE_CAPTURED)
        self.assertIn("MM src/app.py", result["text"])


#: ROUND 24-25. The child of the live-read lifecycle regressions. It runs ``GitTransport._stream`` over a STAND-IN child
#: process (a real process the test owns, whose pid it writes to a file), in a process of its own so that a read that does
#: not end its child could never hang THIS suite, injects ONE fault at ONE point of the child's life (or none), and then
#: OBSERVES THE CHILD BY PID: ``os.kill(pid, 0)`` succeeds for a live child AND for a killed one nobody has reaped (a
#: zombie), and fails only once it is reaped, so "gone" here means terminated AND reaped. The process bounds itself too
#: (a re-arming alarm), so it always ends. The points of the child's life, from the moment ``Popen`` has succeeded:
#:   deadline setup (the Timer's construction, its start), the digest set-up, the read loop, the post-EOF wait, the ending
#:   (the kill, the reap), and a failure WITHIN the cleanup that is already running because of another failure.
LIFECYCLE_CHILD = r"""
import hashlib, json, os, select, signal, subprocess, sys, threading, time
sys.path.insert(0, sys.argv[1])
from target_runtime import git_transport
mode, pidfile = sys.argv[2], sys.argv[3]


class Interrupted(BaseException):
    pass


class InterruptedAgain(BaseException):
    pass


CLOCK = {"armed": False, "fired": False}
RealMonotonic = time.monotonic


def interrupted(signum, frame):
    CLOCK["armed"] = True               # (only the mode that fails the clock looks at this)
    raise Interrupted()


def faulty_monotonic():
    # the FIRST reading of the clock after the read was interrupted, which is the cleanup's own first statement, fails
    if CLOCK["armed"] and not CLOCK["fired"]:
        CLOCK["fired"] = True
        raise InterruptedAgain()
    return RealMonotonic()


QUIET = "time.sleep(30)"                       # alive and silent: the read blocks on its output
# A REAL DESCENDANT that keeps the output pipe open: the stand-in starts a process of its own (which inherits the pipe's
# write end and sleeps) and records that process's pid. Killing the stand-in does not close the pipe.
HOLD = ("import subprocess; d = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)']); "
        "open(@DESC@, 'w').write(str(d.pid))")
BODY = {
    "held": HOLD + "; time.sleep(30)",         # the child is alive and silent, a descendant holds the pipe
    "held_exit": HOLD,                         # the child EXITS; the descendant still holds the pipe
    "interrupt": QUIET, "deadline": QUIET, "timerstart": QUIET, "timerctor": QUIET, "digest": QUIET,
    "cancel_fails": QUIET, "poll_fails": QUIET,
    "read_fails": "sys.stdout.write('x'); sys.stdout.flush(); time.sleep(30)",      # something to READ: the read is what fails
    "cleanup_kill_fails": QUIET, "cleanup_interrupted": QUIET, "cleanup_clock_fails": QUIET,
    # closes its output (the reader sees EOF) and keeps running
    "eof": "os.close(1); time.sleep(30)", "postwait": "os.close(1); time.sleep(30)",
    "end_close_fails": "os.close(1); time.sleep(30)", "deadline_kill_fails": "os.close(1); time.sleep(30)",
    # exits by itself
    "normal": "sys.stdout.write('ok\\n')", "nonlive_normal": "sys.stdout.write('ok\\n')",
    "cancel_fails_healthy": "sys.stdout.write('ok\\n')",
    # writes past the ceiling and keeps running
    "ceiling": "sys.stdout.write('x' * 100000); sys.stdout.flush(); time.sleep(30)",
    "nonlive_ceiling": "sys.stdout.write('x' * 100000); sys.stdout.flush(); time.sleep(30)",
    "end_kill_fails": "sys.stdout.write('x' * 100000); sys.stdout.flush(); time.sleep(30)",
}
code = "import os, sys, time\n" + BODY[mode].replace("@DESC@", repr(pidfile + ".descendant"))
argv = [sys.executable, "-c", code]
ceiling = 1000 if mode in ("ceiling", "nonlive_ceiling", "end_kill_fails") else 1 << 20
live = not mode.startswith("nonlive_")
if mode in ("eof", "deadline", "deadline_kill_fails", "held", "held_exit", "timerstart", "timerctor"):
    git_transport.MAX_REVIEW_GIT_SECONDS = 2.0

# the faults: one point each. Popen's kill is the ONE call whose failure is injected on the child itself. Every mode
# records the child's pid THE INSTANT it exists (a fault can come before the child has even started to run).
RealPopen = subprocess.Popen


class FaultyStdout(object):
    # the child's output pipe, whose close fails once (after really closing)
    def __init__(self, real):
        self.real = real
        self.fired = False

    def close(self):
        self.real.close()
        if not self.fired:
            self.fired = True
            raise OSError("injected: close failed")

    def __getattr__(self, name):
        return getattr(self.real, name)


class FaultyPopen(RealPopen):
    fired = False

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        open(pidfile, "w").write(str(self.pid))
        if mode == "end_close_fails":
            self.stdout = FaultyStdout(self.stdout)
        if mode == "read_fails":
            os.read = faulty_read          # after Popen has used it itself: the READ of the capture fails

    def kill(self):
        # (only a call from the MAIN thread is failed: the deadline timer's own kill is a different caller)
        if threading.current_thread() is threading.main_thread() and not FaultyPopen.fired:
            if mode in ("cleanup_kill_fails", "end_kill_fails", "deadline_kill_fails"):
                FaultyPopen.fired = True
                raise OSError("injected: the signal could not be sent")
            if mode == "cleanup_interrupted":
                FaultyPopen.fired = True
                raise InterruptedAgain()
        return super().kill()


def boom(*args, **kwargs):
    raise RuntimeError("injected: can't start new thread")


RealRead = os.read
RealPoll = select.poll


def faulty_read(*args, **kwargs):
    raise OSError("injected: the read failed")


class FaultyPoller(object):
    def register(self, *args):
        pass

    def poll(self, *args):
        raise OSError("injected: poll failed")


subprocess.Popen = FaultyPopen
if mode == "cleanup_clock_fails":
    time.monotonic = faulty_monotonic
class InertTimer(object):
    # a deadline timer that never fires: only the MAIN thread's own deadline wait is then in play (a race otherwise: at the
    # deadline the timer thread and the main thread both kill the child, and which goes first decides what is reached)
    def __init__(self, *args, **kwargs):
        self.daemon = False

    def start(self):
        pass

    def cancel(self):
        pass


if mode == "deadline_kill_fails":
    threading.Timer = InertTimer
if mode == "timerstart":
    threading.Timer.start = boom
if mode == "timerctor":
    threading.Timer = boom
if mode == "digest":
    hashlib.sha256 = boom
# the CANCELLATION path: whatever a deadline timer is cancelled by fails (a read that has no timer has no such path)
if mode in ("cancel_fails", "cancel_fails_healthy"):
    threading.Timer.cancel = boom
if mode == "poll_fails":
    select.poll = FaultyPoller

# The caller's interruption is a BaseException raised inside the read (or inside the wait after it), as the suite's watchdog
# raises one; in the modes that expect no interruption the alarm is only this process's OWN outer bound.
first = 1.0 if mode in ("interrupt", "postwait", "cancel_fails", "cleanup_kill_fails", "cleanup_interrupted",
                       "cleanup_clock_fails") else 7.0
signal.signal(signal.SIGALRM, interrupted)
signal.setitimer(signal.ITIMER_REAL, first, 1.0)
outcome = {}
started = time.monotonic()
try:
    result = git_transport.GitTransport()._stream(argv, 100, ceiling, live=live)
    outcome["result"] = {"status": result["status"]}      # (a captured result also holds raw bytes)
except BaseException as error:
    outcome["raised"] = type(error).__name__
signal.setitimer(signal.ITIMER_REAL, 0)
subprocess.Popen = RealPopen
time.monotonic = RealMonotonic
os.read = RealRead
select.poll = RealPoll
outcome["elapsed"] = time.monotonic() - started
pid = int(open(pidfile).read())
try:
    os.kill(pid, 0)
    outcome["child_gone"] = False
except ProcessLookupError:
    outcome["child_gone"] = True
outcome["pid"] = pid
if os.path.exists(pidfile + ".descendant"):
    descendant = int(open(pidfile + ".descendant").read())
    outcome["descendant_pid"] = descendant
    try:
        os.kill(descendant, 0)
        outcome["descendant_alive"] = True
    except ProcessLookupError:
        outcome["descendant_alive"] = False
outcome["ps"] = subprocess.run(["ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True).stdout.strip()
print(json.dumps(outcome))
"""


class LiveChildLifecycleTests(FixtureCase):
    """ROUND 24-25. A LIVE child is OWNED from the moment ``Popen`` has succeeded, through deadline setup, the read, the wait
    and the cleanup, INCLUDING a failure within the cleanup itself: on every exit path it is terminated AND REAPED within a
    bounded time, the exception already on its way out (if any) is the one that propagates, and nothing waits without a
    bound. Observed on the child itself, by pid, not on the test body's return."""

    def observe(self, mode):
        pidfile = os.path.join(self.root, "child-%s.pid" % mode)
        outcome = None
        try:
            done = subprocess.run([sys.executable, "-c", LIFECYCLE_CHILD, str(REPO_ROOT), mode, pidfile],
                                  capture_output=True, text=True, timeout=30)
            self.assertEqual(done.returncode, 0, done.stderr[-600:])
            outcome = json.loads(done.stdout)
            return outcome
        finally:
            # whatever a broken read left alive, and the DESCENDANT the test itself started, is not left alive by the TEST
            for path, gone in ((pidfile, (outcome or {}).get("child_gone")), (pidfile + ".descendant", False)):
                if os.path.exists(path) and not gone:
                    try:
                        os.kill(int(open(path).read()), 9)
                    except (ProcessLookupError, ValueError):
                        pass

    def assert_ends_with(self, mode, raised, within=6):
        outcome = self.observe(mode)
        self.assertEqual(outcome.get("raised"), raised, (mode, "the exception that propagates", outcome))
        self.assertTrue(outcome["child_gone"], (mode, "the child is terminated and reaped, not left alive or a zombie",
                                                outcome["ps"]))
        self.assertLess(outcome["elapsed"], within, mode)

    def test_an_interruption_during_the_read_leaves_no_child(self):
        self.assert_ends_with("interrupt", "Interrupted")

    def test_an_interruption_during_the_wait_after_the_pipe_ended_leaves_no_child(self):
        # ROUND 25 F1: the pipe ended, the child did not exit, and the caller is interrupted WHILE the read waits for it
        self.assert_ends_with("postwait", "Interrupted")

    def test_a_pipe_that_ends_before_the_process_does_is_bounded_by_the_deadline(self):
        outcome = self.observe("eof")
        self.assertNotIn("raised", outcome, "the read ended by itself, not by the harness's own outer bound")
        self.assertEqual(outcome["result"]["status"], git_transport.CAPTURE_REFUSED_DEADLINE)
        self.assertTrue(outcome["child_gone"], ("terminated and reaped", outcome["ps"]))
        self.assertLess(outcome["elapsed"], 6, "within the (2 s) deadline, not the child's 30")

    def test_the_deadline_needs_no_thread_and_a_setup_failure_leaves_no_child(self):
        # ROUND 25 F2 asked that the child survive a Timer that cannot start; round 26 asks more: the deadline is the read's
        # OWN, so a Timer that cannot start a thread or cannot even be built changes NOTHING: the read still refuses at its
        # deadline and its child is gone. And the digest cannot be created: that failure propagates, the child still ended.
        for mode in ("timerstart", "timerctor"):
            with self.subTest(mode=mode):
                outcome = self.observe(mode)
                self.assertNotIn("raised", outcome, outcome)
                self.assertEqual(outcome["result"]["status"], git_transport.CAPTURE_REFUSED_DEADLINE)
                self.assertTrue(outcome["child_gone"], (mode, outcome["ps"]))
                self.assertLess(outcome["elapsed"], 5)
        self.assert_ends_with("digest", "RuntimeError", within=5)

    def test_a_descendant_that_keeps_the_pipe_open_does_not_hold_the_read_past_its_deadline(self):
        # ROUND 26 F1. A REAL descendant, started by the child, keeps the output pipe's write end open and sleeps. The
        # child is killed at the deadline and the pipe does NOT reach EOF; the read must stop at its deadline anyway: the
        # capture is bounded by the clock, not by the end of the stream. Observed on the real processes by pid.
        for mode in ("held", "held_exit"):
            with self.subTest(mode=mode):
                outcome = self.observe(mode)
                self.assertNotIn("raised", outcome, (mode, "the read ended by itself, not by the harness's own bound", outcome))
                self.assertEqual(outcome["result"]["status"], git_transport.CAPTURE_REFUSED_DEADLINE)
                self.assertTrue(outcome["child_gone"], (mode, "the child is terminated and reaped", outcome["ps"]))
                self.assertTrue(outcome["descendant_alive"], (mode, "the descendant really was still holding the pipe"))
                self.assertLess(outcome["elapsed"], 5, (mode, "at the 2 s deadline, not at the descendant's 30 s"))

    def test_a_failure_in_the_cancellation_path_does_not_replace_the_exception_in_flight(self):
        # ROUND 26 F2. Whatever cancels a deadline timer fails, WHILE the read is already being abandoned: the exception in
        # flight is the one that propagates (it was lost to the cancellation's), and the child is ended. A read with no
        # timer has no such path to fail, and a HEALTHY read is not turned into a failure by it either.
        self.assert_ends_with("cancel_fails", "Interrupted", within=5)
        outcome = self.observe("cancel_fails_healthy")
        self.assertNotIn("raised", outcome, ("a healthy read is not failed by a cancellation", outcome))
        self.assertEqual(outcome["result"]["status"], git_transport.CAPTURE_CAPTURED)
        self.assertTrue(outcome["child_gone"])

    def test_a_failure_of_the_capture_io_itself_leaves_no_child(self):
        # the I/O phase's own fault points: waiting for the pipe, and reading it
        for mode in ("poll_fails", "read_fails"):
            with self.subTest(mode=mode):
                self.assert_ends_with(mode, "OSError", within=5)

    def test_a_failure_within_the_cleanup_does_not_mask_the_original_or_leave_the_child(self):
        # the cleanup that runs because the read was interrupted is itself hit: the signal cannot be sent, or the caller is
        # interrupted AGAIN. The exception already on its way out is the one that propagates, and the child is still ended.
        for mode in ("cleanup_kill_fails", "cleanup_interrupted", "cleanup_clock_fails"):
            with self.subTest(mode=mode):
                self.assert_ends_with(mode, "Interrupted", within=5)
        # and with NO exception on its way out, the ending's own failure is the one that propagates, the child still ended:
        # the kill past the ceiling, closing the pipe after the read, and the kill at the deadline of a child that closed its
        # output and did not exit
        for mode in ("end_kill_fails", "end_close_fails", "deadline_kill_fails"):
            with self.subTest(mode=mode):
                self.assert_ends_with(mode, "OSError", within=6)

    def test_every_healthy_way_a_read_ends_still_ends_its_child_and_answers_as_before(self):
        # CONTROLS: the normal live read, the ceiling, the deadline, and the non-live reads (which have no timer and are
        # not changed) all end exactly as they did
        for mode, status in (("normal", git_transport.CAPTURE_CAPTURED),
                             ("ceiling", git_transport.CAPTURE_REFUSED_OVER_BOUND),
                             ("deadline", git_transport.CAPTURE_REFUSED_DEADLINE),
                             ("nonlive_normal", git_transport.CAPTURE_CAPTURED),
                             ("nonlive_ceiling", git_transport.CAPTURE_REFUSED_OVER_BOUND)):
            with self.subTest(mode=mode):
                outcome = self.observe(mode)
                self.assertNotIn("raised", outcome, outcome)
                self.assertEqual(outcome["result"]["status"], status)
                self.assertTrue(outcome["child_gone"], ("terminated and reaped", outcome["ps"]))
                self.assertLess(outcome["elapsed"], 6)


class LiveReadSurfaceTests(Bounded):

    def test_the_live_environment_inherits_nothing_and_pins_the_config(self):
        env = git_transport._LIVE_READ_ENV
        self.assertEqual(sorted(env), sorted((
            "PATH", "LC_ALL", "GIT_TERMINAL_PROMPT", "GIT_OPTIONAL_LOCKS",
            "GIT_CONFIG_NOSYSTEM", "GIT_CONFIG_GLOBAL")))
        self.assertEqual(env["GIT_CONFIG_GLOBAL"], "/dev/null")
        pins = git_transport._LIVE_READ_PINS
        for pin in ("--no-optional-locks", "--literal-pathspecs",
                    "core.fsmonitor=false", "core.quotePath=true"):
            self.assertIn(pin, pins)

    def test_every_live_read_runs_only_a_read_verb_under_the_pins(self):
        argvs = []
        head = "a" * 40

        def record(argv, retain, ceiling, live=False, accept=(0,), env=None):
            argvs.append((list(argv), live, env))
            if "--show-object-format" in argv:
                data = b"/g\n/g\nsha1\n"
            elif "--verify" in argv:
                data = head.encode() + b"\n"
            else:
                data = b""
            return {"status": git_transport.CAPTURE_CAPTURED, "total_bytes": 0,
                    "digest": "0" * 64, "retained": data, "truncated": False}
        transport = git_transport.GitTransport()
        with mock.patch.object(transport, "_stream", record):
            transport.live_toplevel("/w")
            transport.live_head("/w")
            transport.live_filter_drivers("/w")
            transport.live_inventory("/w", ())
            transport.live_counts("/w", ())
            transport.live_file_diff("/w", ["p", "q"], ())
        verbs = set()
        self.assertGreaterEqual(len(argvs), 9)
        for argv, live, env in argvs:
            self.assertTrue(live, "a live read is always a live stream")
            self.assertEqual(argv[0], "git")
            for pin in git_transport._LIVE_READ_PINS:
                self.assertIn(pin, argv)
            verb = argv[argv.index("-C") + 2]
            verbs.add(verb)
            for write in ("add", "update-index", "write-tree", "checkout",
                          "reset", "stash", "gc", "prune", "fetch", "clean",
                          "rm", "mv", "apply", "restore", "switch"):
                self.assertNotIn(write, argv[argv.index("-C") + 2:argv.index(
                    "--") if "--" in argv else len(argv)])
            # THE line between the two kinds of read: what can hash a working
            # file goes through a private git directory, always; what only
            # asks git where things are does not, and never carries one
            if verb in ("status", "diff-index"):
                self.assertIsNotNone(env, "a content read has its own git dir")
                self.assertEqual(env["GIT_WORK_TREE"], "/w")
                self.assertNotIn(env["GIT_DIR"], ("/g", "/w/.git"))
                self.assertEqual(
                    set(env) - set(git_transport._LIVE_READ_ENV),
                    {"GIT_DIR", "GIT_WORK_TREE"})
            else:
                self.assertIsNone(env)
            # no per-driver override is ever asked for: there is no driver
            self.assertFalse([a for a in argv if a.startswith("filter.")])
        self.assertEqual(verbs, {"rev-parse", "config", "status", "diff-index"})
        # no refresh of the index is ever asked for
        for argv, _live, _env in argvs:
            self.assertNotIn("--refresh", argv)


if __name__ == "__main__":
    unittest.main()
