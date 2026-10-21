"""Regression coverage for tests/_ci_runner.py and the CI workflow shape.

Two halves:

1. Runner behaviour, executed: each test writes small fixture scripts
   into a private temporary "repository" and runs them through the
   runner. Fixtures only touch their own temporary directory, and every
   wait in a fixture or a test has its own deadline, so a broken runner
   fails these tests instead of hanging them.

2. Workflow shape: .github/workflows/ci.yml is read as text and parsed
   by the small YAML-subset parser below (standard library only;
   PyYAML is not available on every CI interpreter). The parser refuses
   constructs it does not understand rather than guessing.

Standalone: PYTHONPATH=$PWD python3 tests/test_ci_runner.py
"""

import io
import json
import os
import re
import shlex
import signal
import subprocess
import sys
import tempfile
import textwrap
import threading
import time
import unittest
from unittest import mock

import _ci_runner

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(TESTS_DIR)
RUNNER_PATH = os.path.join(TESTS_DIR, "_ci_runner.py")
WORKFLOW_PATH = os.path.join(REPO_ROOT, ".github", "workflows", "ci.yml")

# Upper bound on any single wait in this module. Generous for loaded CI
# machines; a broken runner hits it and fails rather than hanging.
WAIT_LIMIT = 60.0


# ---------------------------------------------------------------------
# Helpers for the runner half.

class Recorder(object):
    """A thread-safe binary output stream that keeps everything."""

    def __init__(self):
        self._lock = threading.Lock()
        self._chunks = []

    def write(self, data):
        with self._lock:
            self._chunks.append(bytes(data))
        return len(data)

    def flush(self):
        pass

    def getvalue(self):
        with self._lock:
            return b"".join(self._chunks)

    def text(self):
        return self.getvalue().decode("utf-8")


def wait_until(predicate, what, limit=WAIT_LIMIT):
    deadline = time.monotonic() + limit
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.02)
    raise AssertionError("timed out after %.0fs waiting for %s" % (limit, what))


class FixtureRepo(object):
    """A temporary directory laid out like the repository: test files
    live in ``<root>/tests``, and ``<root>/state`` is scratch space the
    fixtures write their evidence into."""

    def __init__(self, testcase):
        handle = tempfile.TemporaryDirectory(prefix="ci-runner-test-")
        testcase.addCleanup(handle.cleanup)
        # realpath: on macOS the temp dir is reached through a symlink.
        self.root = os.path.realpath(handle.name)
        self.tests = os.path.join(self.root, "tests")
        self.state = os.path.join(self.root, "state")
        os.makedirs(self.tests)
        os.makedirs(self.state)

    def add(self, name, body):
        """Write ``tests/<name>``; ``STATE`` in the body is bound to the
        state directory."""
        source = "import os, sys, time\nSTATE = %r\n" % self.state
        source += textwrap.dedent(body)
        with open(os.path.join(self.tests, name), "w") as handle:
            handle.write(source)
        return "tests/" + name

    def state_path(self, name):
        return os.path.join(self.state, name)

    def exists(self, name):
        return os.path.exists(self.state_path(name))

    def read_json(self, name):
        with open(self.state_path(name)) as handle:
            return json.load(handle)

    def run(self, jobs, should_stop=None, relpaths=None):
        out = Recorder()
        if relpaths is None:
            relpaths = _ci_runner.select_files(self.root)
        results, stopped = _ci_runner.run_files(
            self.root, relpaths, jobs, out, should_stop=should_stop)
        return results, stopped, out

    def main(self, *args):
        out = Recorder()
        status = _ci_runner.main(["--repo", self.root] + list(args), out=out)
        return status, out


PASS_BODY = """
open(os.path.join(STATE, "ran-" + os.path.basename(__file__)), "w").close()
"""

FAIL_BODY = """
open(os.path.join(STATE, "ran-" + os.path.basename(__file__)), "w").close()
sys.exit(1)
"""

# Records how many fixtures were running at once, as seen by each of
# them: a marker per live process in STATE/active, sampled for a while.
# BARRIER > 0 makes the fixture wait until that many are live (with a
# deadline) before sampling, so the cap must actually be reached.
SAMPLER_BODY = """
BARRIER = %(barrier)d
HOLD = %(hold)r
active = os.path.join(STATE, "active")
os.makedirs(active, exist_ok=True)
marker = os.path.join(active, str(os.getpid()))
open(marker, "w").close()
peak = 0
reached = BARRIER == 0
try:
    deadline = time.monotonic() + 20.0
    while BARRIER and time.monotonic() < deadline:
        count = len(os.listdir(active))
        peak = max(peak, count)
        if count >= BARRIER:
            reached = True
            break
        time.sleep(0.005)
    end = time.monotonic() + HOLD
    while time.monotonic() < end:
        peak = max(peak, len(os.listdir(active)))
        time.sleep(0.005)
finally:
    os.remove(marker)
with open(os.path.join(STATE, "peak-" + os.path.basename(__file__)), "w") as f:
    f.write(str(peak))
sys.exit(0 if reached else 3)
"""

# Waits until STATE/<release> exists (deadline 60s, then exits 3), after
# leaving STATE/<ready> behind. Prints evidence first.
HOLDER_BODY = """
print("holder-evidence " + os.path.basename(__file__), flush=True)
with open(os.path.join(STATE, %(ready)r), "w") as f:
    f.write(str(os.getpid()))
deadline = time.monotonic() + 60.0
while not os.path.exists(os.path.join(STATE, %(release)r)):
    if time.monotonic() > deadline:
        sys.exit(3)
    time.sleep(0.01)
open(os.path.join(STATE, "finished-" + os.path.basename(__file__)), "w").close()
"""


# Waits until the fixture that wrote STATE/<after> has exited AND been
# reaped (its pid no longer exists), then records its own pid in
# STATE/<mine> and exits with <code>. Deadline 30s.
ORDERED_BODY = """
AFTER = %(after)r
if AFTER:
    path = os.path.join(STATE, AFTER)
    deadline = time.monotonic() + 30.0
    while not os.path.exists(path):
        assert time.monotonic() < deadline, "no " + AFTER
        time.sleep(0.005)
    with open(path) as f:
        other = int(f.read())
    while True:
        try:
            os.kill(other, 0)
        except ProcessLookupError:
            break
        assert time.monotonic() < deadline, "still alive: %%d" %% other
        time.sleep(0.005)
partial = os.path.join(STATE, %(mine)r + ".partial")
with open(partial, "w") as f:
    f.write(str(os.getpid()))
os.rename(partial, os.path.join(STATE, %(mine)r))
sys.exit(%(code)d)
"""


def sampler(barrier=0, hold=0.0):
    return SAMPLER_BODY % {"barrier": barrier, "hold": hold}


def holder(ready, release):
    return HOLDER_BODY % {"ready": ready, "release": release}


def github_view(raw):
    """Model how GitHub Actions shows a log: returns the displayed lines
    as ``(group_depth, text)`` pairs.

    ``::group::`` and ``::endgroup::`` open and close a collapsed group,
    any other ``::`` line is a hidden command, and between
    ``::stop-commands::TOKEN`` and ``::TOKEN::`` every line is shown as
    plain text.
    """
    shown = []
    depth = 0
    stop_token = None
    lines = raw.decode("utf-8").split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    for line in lines:
        if stop_token is not None:
            if line == "::%s::" % stop_token:
                stop_token = None
            else:
                shown.append((depth, line))
            continue
        if line.startswith("::stop-commands::"):
            stop_token = line[len("::stop-commands::"):]
        elif line.startswith("::group::"):
            shown.append((depth, line[len("::group::"):]))
            depth += 1
        elif line == "::endgroup::":
            depth -= 1
            if depth < 0:
                raise AssertionError("::endgroup:: without a group")
        elif not line.startswith("::"):
            shown.append((depth, line))
    if depth != 0 or stop_token is not None:
        raise AssertionError("log ends inside a group or a stop-commands "
                             "span (depth %d, token %r)" % (depth, stop_token))
    return shown


def block_body(raw, relpath):
    """The captured lines of ``relpath``'s block, located by its header
    and the stop-commands span that follows it. Asserts there is exactly
    one block for the file."""
    lines = raw.decode("utf-8").split("\n")
    heads = [
        index for index, line in enumerate(lines)
        if (line.startswith("::group::") or line.startswith("[ci-runner] ===="))
        and (" %s " % relpath) in line and " end " not in line
    ]
    if len(heads) != 1:
        raise AssertionError("expected one block for %s, found %d"
                             % (relpath, len(heads)))
    start = heads[0] + 1
    token = lines[start][len("::stop-commands::"):]
    if not lines[start].startswith("::stop-commands::") or not token:
        raise AssertionError("block for %s does not quote its output" % relpath)
    end = lines.index("::%s::" % token, start + 1)
    return heads[0], lines[start + 1:end], end


# ---------------------------------------------------------------------
# Runner behaviour.

class SelectionTests(unittest.TestCase):

    def test_selection_is_the_sorted_test_glob(self):
        repo = FixtureRepo(self)
        for name in ("test_b.py", "test_a.py", "test_c.py", "helper.py",
                     "_test_private.py", "test_notes.txt"):
            repo.add(name, PASS_BODY)
        os.makedirs(os.path.join(repo.tests, "test_dir.py"))
        self.assertEqual(
            _ci_runner.select_files(repo.root),
            ["tests/test_a.py", "tests/test_b.py", "tests/test_c.py"])

    def test_an_excluded_file_is_never_executed(self):
        # Requirement 10.
        repo = FixtureRepo(self)
        repo.add("test_kept.py", PASS_BODY)
        repo.add("test_left_out.py", PASS_BODY)
        status, out = repo.main("--exclude", "test_left_out.py")
        self.assertEqual(status, 0, out.text())
        self.assertTrue(repo.exists("ran-test_kept.py"))
        self.assertFalse(repo.exists("ran-test_left_out.py"))
        self.assertNotIn("tests/test_left_out.py", out.text().split())
        self.assertIn("excluded: test_left_out.py", out.text())

    def test_a_misspelt_exclusion_is_refused_before_anything_runs(self):
        repo = FixtureRepo(self)
        repo.add("test_kept.py", PASS_BODY)
        with mock.patch("sys.stderr", new_callable=io.StringIO) as err:
            status, out = repo.main("--exclude", "test_no_such_file.py")
        self.assertEqual(status, 2)
        self.assertIn("test_no_such_file.py", err.getvalue())
        self.assertFalse(repo.exists("ran-test_kept.py"))
        self.assertEqual(out.getvalue(), b"")

    def test_an_exclusion_must_be_a_bare_file_name(self):
        repo = FixtureRepo(self)
        repo.add("test_kept.py", PASS_BODY)
        for bad in ("tests/test_kept.py", "", "..", "sub/test_kept.py"):
            with self.assertRaises(_ci_runner.SelectionError, msg=bad):
                _ci_runner.select_files(repo.root, [bad])

    def test_an_empty_selection_is_refused(self):
        repo = FixtureRepo(self)
        with self.assertRaises(_ci_runner.SelectionError):
            _ci_runner.select_files(repo.root)
        repo.add("test_only.py", PASS_BODY)
        with self.assertRaises(_ci_runner.SelectionError):
            _ci_runner.select_files(repo.root, ["test_only.py"])


class JobCapTests(unittest.TestCase):
    # Requirement 4.

    def test_default_is_core_aware_and_bounded_at_four(self):
        for cores, expected in ((1, 1), (2, 2), (3, 3), (4, 4), (8, 4),
                                (64, 4), (None, 1), (0, 1)):
            with mock.patch("os.cpu_count", return_value=cores):
                self.assertEqual(_ci_runner.default_jobs(), expected, cores)

    def test_jobs_argument_overrides_the_default(self):
        parser = _ci_runner.build_parser()
        self.assertEqual(parser.parse_args(["--jobs", "7"]).jobs, 7)
        self.assertIsNone(parser.parse_args([]).jobs)
        for bad in ("0", "-1", "two"):
            with mock.patch("sys.stderr", new_callable=io.StringIO):
                with self.assertRaises(SystemExit, msg=bad):
                    parser.parse_args(["--jobs", bad])

    def test_main_uses_the_default_when_no_jobs_argument_is_given(self):
        repo = FixtureRepo(self)
        repo.add("test_a.py", PASS_BODY)
        with mock.patch("os.cpu_count", return_value=2):
            status, out = repo.main()
        self.assertEqual(status, 0, out.text())
        self.assertIn("[ci-runner] jobs: 2 (default", out.text())


class ProcessFormTests(unittest.TestCase):
    # Requirement 5.

    def test_command_is_the_ci_per_file_form(self):
        command = _ci_runner.build_command("/r", "tests/test_x.py")
        self.assertEqual(command, [sys.executable, "/r/tests/test_x.py"])
        self.assertNotIn("-m", command)

    def test_child_environment_replaces_pythonpath(self):
        with mock.patch.dict(os.environ, {"PYTHONPATH": "/elsewhere",
                                          "CI_RUNNER_PROBE": "kept"}):
            env = _ci_runner.child_environment("/r")
        self.assertEqual(env["PYTHONPATH"], "/r")
        self.assertEqual(env["CI_RUNNER_PROBE"], "kept")

    def test_each_file_runs_in_its_own_interpreter_in_ci_form(self):
        repo = FixtureRepo(self)
        # If anything imported the tests package, this would record it.
        with open(os.path.join(repo.tests, "__init__.py"), "w") as handle:
            handle.write("open(%r, 'w').close()\n"
                         % repo.state_path("package-init-ran"))
        probe = """
        import json
        with open(os.path.join(STATE, "probe-" + os.path.basename(__file__)),
                  "w") as f:
            json.dump({"argv": sys.argv, "executable": sys.executable,
                       "cwd": os.getcwd(),
                       "pythonpath": os.environ.get("PYTHONPATH"),
                       "pid": os.getpid(), "ppid": os.getppid(),
                       "main_file": __file__,
                       "tests_package": "tests" in sys.modules}, f)
        """
        names = ("test_one.py", "test_two.py", "test_three.py")
        for name in names:
            repo.add(name, probe)
        results, stopped, out = repo.run(jobs=3)
        self.assertFalse(stopped)
        self.assertEqual([r.status for r in results], ["passed"] * 3)
        pids = set()
        for name in names:
            seen = repo.read_json("probe-" + name)
            path = os.path.join(repo.root, "tests", name)
            self.assertEqual(seen["argv"], [path])
            self.assertEqual(os.path.realpath(seen["executable"]),
                             os.path.realpath(sys.executable))
            self.assertEqual(os.path.realpath(seen["cwd"]), repo.root)
            self.assertEqual(seen["pythonpath"], repo.root)
            self.assertEqual(seen["ppid"], os.getpid())
            self.assertFalse(seen["tests_package"])
            pids.add(seen["pid"])
        self.assertEqual(len(pids), 3)
        self.assertNotIn(os.getpid(), pids)
        self.assertFalse(repo.exists("package-init-ran"))


class OutcomeTests(unittest.TestCase):
    # Requirements 1 and 2.

    def test_every_file_runs_when_a_middle_file_fails(self):
        for jobs in (1, 3):
            with self.subTest(jobs=jobs):
                repo = FixtureRepo(self)
                repo.add("test_a.py", PASS_BODY)
                repo.add("test_b.py", FAIL_BODY)
                repo.add("test_c.py", PASS_BODY + "raise RuntimeError('boom')\n")
                repo.add("test_d.py", PASS_BODY)
                repo.add("test_e.py", PASS_BODY)
                status, out = repo.main("--jobs", str(jobs))
                self.assertEqual(status, 1, out.text())
                for name in ("a", "b", "c", "d", "e"):
                    self.assertTrue(repo.exists("ran-test_%s.py" % name),
                                    (jobs, name, out.text()))
                summary = out.text().split("summary, sorted by file:")[1]
                for name, label in (("a", "PASS"), ("b", "FAIL"),
                                    ("c", "FAIL"), ("d", "PASS"),
                                    ("e", "PASS")):
                    self.assertRegex(
                        summary, r"\[ci-runner\]   %s .* tests/test_%s\.py\n"
                        % (label, name))
                self.assertIn("[ci-runner] failures: 2\n", summary)
                self.assertIn("[ci-runner] verdict: FAIL\n", summary)

    def test_all_passing_exits_zero(self):
        repo = FixtureRepo(self)
        for name in ("test_a.py", "test_b.py", "test_c.py"):
            repo.add(name, PASS_BODY)
        status, out = repo.main("--jobs", "2")
        self.assertEqual(status, 0, out.text())
        self.assertIn("[ci-runner] failures: 0\n", out.text())
        self.assertIn("[ci-runner] verdict: PASS\n", out.text())

    def test_one_failure_among_passes_exits_nonzero(self):
        repo = FixtureRepo(self)
        for name in ("test_a.py", "test_b.py", "test_d.py"):
            repo.add(name, PASS_BODY)
        repo.add("test_c.py", "sys.exit(5)\n")
        status, out = repo.main("--jobs", "4")
        self.assertEqual(status, 1, out.text())
        self.assertRegex(out.text(), r"FAIL +exit 5 +\S+ +tests/test_c\.py")

    def test_a_file_killed_by_a_signal_is_a_failure(self):
        repo = FixtureRepo(self)
        repo.add("test_a.py", PASS_BODY)
        repo.add("test_b.py", "import signal\nos.kill(os.getpid(), signal.SIGKILL)\n")
        status, out = repo.main("--jobs", "2")
        self.assertEqual(status, 1, out.text())
        self.assertRegex(out.text(),
                         r"FAIL +signal 9 \(SIGKILL\) +\S+ +tests/test_b\.py")


class ConcurrencyTests(unittest.TestCase):
    # Requirement 3: the fixtures observe how many of them are alive at
    # once. A serial runner would make every observed peak 1.

    def peaks(self, repo, names):
        found = {}
        for name in names:
            with open(repo.state_path("peak-" + name)) as handle:
                found[name] = int(handle.read())
        return found

    def test_observed_simultaneity_never_exceeds_the_cap_and_exceeds_one(self):
        repo = FixtureRepo(self)
        names = ["test_s%d.py" % index for index in range(8)]
        for name in names:
            repo.add(name, sampler(hold=0.6))
        results, stopped, out = repo.run(jobs=3)
        self.assertEqual([r.status for r in results], ["passed"] * 8,
                         out.text())
        peaks = self.peaks(repo, names)
        self.assertLessEqual(max(peaks.values()), 3, peaks)
        self.assertGreater(max(peaks.values()), 1, peaks)

    def test_the_cap_is_actually_reached(self):
        # Each fixture waits until three are alive at once and exits 3
        # if that never happens, so this passes only with real
        # three-way parallelism. The fourth file proves the queue still
        # drains after the barrier group.
        repo = FixtureRepo(self)
        names = ["test_b%d.py" % index for index in range(3)]
        for name in names:
            repo.add(name, sampler(barrier=3, hold=0.2))
        repo.add("test_z.py", PASS_BODY)
        results, stopped, out = repo.run(jobs=3)
        self.assertEqual([r.status for r in results], ["passed"] * 4,
                         out.text())
        self.assertEqual(set(self.peaks(repo, names).values()), {3})

    def test_a_cap_of_one_runs_serially(self):
        repo = FixtureRepo(self)
        names = ["test_s%d.py" % index for index in range(4)]
        for name in names:
            repo.add(name, sampler(hold=0.2))
        results, stopped, out = repo.run(jobs=1)
        self.assertEqual([r.status for r in results], ["passed"] * 4)
        self.assertEqual(set(self.peaks(repo, names).values()), {1})


class OutputContractTests(unittest.TestCase):
    # Requirements 6, 7, 8 and 9, plus the log-structure guarantees.

    def test_failing_output_is_complete_and_not_collapsed(self):
        # Requirement 6.
        repo = FixtureRepo(self)
        repo.add("test_a.py", 'print("pass-line")\n')
        repo.add("test_b.py", """
        for index in range(3000):
            print("fail-line-%05d" % index)
        sys.stdout.flush()
        sys.stderr.write("fail-stderr-last\\n")
        sys.exit(1)
        """)
        results, stopped, out = repo.run(jobs=2)
        expected = ["fail-line-%05d" % index for index in range(3000)]
        expected.append("fail-stderr-last")
        _, body, _ = block_body(out.getvalue(), "tests/test_b.py")
        self.assertEqual(body, expected)
        shown = github_view(out.getvalue())
        failing = [(d, t) for d, t in shown if t.startswith("fail-")]
        self.assertEqual([t for _, t in failing], expected)
        self.assertEqual({d for d, _ in failing}, {0})
        self.assertIn((1, "pass-line"), shown)
        self.assertIn((0, "PASS tests/test_a.py (exit 0, %s)"
                       % _ci_runner._seconds(results[0].duration)), shown)

    def test_each_block_is_contiguous_under_interleaved_writes(self):
        # Requirement 7: four files write at the same time, line by line,
        # alternating stdout and stderr.
        repo = FixtureRepo(self)
        body = """
        tag = os.path.basename(__file__)[:-3]
        for index in range(200):
            stream = sys.stdout if index % 2 else sys.stderr
            stream.write("%s line %03d\\n" % (tag, index))
            stream.flush()
            time.sleep(0.001)
        """
        names = ["test_w%d" % index for index in range(4)]
        for name in names:
            repo.add(name + ".py", body)
        results, stopped, out = repo.run(jobs=4)
        raw = out.getvalue()
        for name in names:
            _, lines, _ = block_body(raw, "tests/%s.py" % name)
            self.assertEqual(
                lines, ["%s line %03d" % (name, index) for index in range(200)])
        shown = [text for _, text in github_view(raw)]
        for name in names:
            positions = [i for i, text in enumerate(shown)
                         if text.startswith(name + " line ")]
            self.assertEqual(len(positions), 200)
            self.assertEqual(positions[-1] - positions[0], 199, name)

    def test_a_block_is_emitted_when_its_file_completes(self):
        # Requirement 8: the fast file's block must be in the stream
        # while the slow file is provably still running.
        repo = FixtureRepo(self)
        repo.add("test_a_slow.py", holder("slow-ready", "release"))
        repo.add("test_b_fast.py", 'print("fast-evidence")\n')
        out = Recorder()
        outcome = {}

        def target():
            outcome["value"] = _ci_runner.run_files(
                repo.root, _ci_runner.select_files(repo.root), 2, out)

        thread = threading.Thread(target=target)
        thread.start()
        try:
            wait_until(lambda: b"::endgroup::" in out.getvalue()
                       and repo.exists("slow-ready"), "the fast block")
            during = out.text()
            self.assertIn("fast-evidence", during)
            self.assertIn("PASS tests/test_b_fast.py", during)
            self.assertIn("[ci-runner] start tests/test_a_slow.py", during)
            self.assertNotIn("holder-evidence", during)
            self.assertFalse(repo.exists("finished-test_a_slow.py"))
        finally:
            touch(repo.state_path("release"))
            thread.join(WAIT_LIMIT)
        self.assertFalse(thread.is_alive())
        results, stopped = outcome["value"]
        self.assertEqual([r.status for r in results], ["passed", "passed"])
        after = out.text()
        self.assertLess(after.index("PASS tests/test_b_fast.py"),
                        after.index("PASS tests/test_a_slow.py"))

    def test_summary_is_sorted_and_reports_exit_status(self):
        # Requirement 9. test_c finishes first and test_a last, so the
        # blocks come out c, b, a while the summary stays a, b, c. Each
        # fixture waits until the previous one has been REAPED by the
        # runner (its pid is gone), which the runner does in the same
        # step that prints its block, so the order is deterministic.
        repo = FixtureRepo(self)
        repo.add("test_c.py", ORDERED_BODY % {"after": None, "mine": "c-done",
                                              "code": 0})
        repo.add("test_b.py", ORDERED_BODY % {"after": "c-done",
                                              "mine": "b-done", "code": 0})
        repo.add("test_a.py", ORDERED_BODY % {"after": "b-done",
                                              "mine": "a-done", "code": 2})
        summaries = []
        for _ in range(2):
            for name in ("c-done", "b-done"):
                if repo.exists(name):
                    os.remove(repo.state_path(name))
            status, out = repo.main("--jobs", "3")
            self.assertEqual(status, 1, out.text())
            text = out.text()
            self.assertLess(text.index("PASS tests/test_c.py"),
                            text.index("PASS tests/test_b.py"))
            self.assertLess(text.index("PASS tests/test_b.py"),
                            text.index("FAIL tests/test_a.py"))
            summary = text.split("[ci-runner] summary, sorted by file:\n")[1]
            # Durations differ between runs; everything else must not.
            summaries.append(re.sub(r"\s+\d+\.\d\ds\s+", " <t> ", summary))
        self.assertEqual(summaries[0], summaries[1])
        rows = summaries[0].splitlines()
        self.assertEqual([row.split()[-1] for row in rows[:3]],
                         ["tests/test_a.py", "tests/test_b.py",
                          "tests/test_c.py"])
        self.assertRegex(rows[0], r"^\[ci-runner\]   FAIL +exit 2 ")
        self.assertRegex(rows[1], r"^\[ci-runner\]   PASS +exit 0 ")
        self.assertRegex(rows[2], r"^\[ci-runner\]   PASS +exit 0 ")
        self.assertEqual(rows[3:], [
            "[ci-runner] files: 3 (2 passed, 1 failed)",
            "[ci-runner] failures: 1",
            "[ci-runner] verdict: FAIL",
        ])

    def test_printed_workflow_commands_cannot_regroup_the_log(self):
        repo = FixtureRepo(self)
        repo.add("test_a.py", """
        print("::endgroup::")
        print("::group::forged")
        print("::error::forged annotation")
        """)
        repo.add("test_b.py", 'print("real-failure")\nsys.exit(1)\n')
        results, stopped, out = repo.run(jobs=1)
        shown = github_view(out.getvalue())
        self.assertIn((1, "::endgroup::"), shown)
        self.assertIn((1, "::group::forged"), shown)
        self.assertIn((1, "::error::forged annotation"), shown)
        self.assertIn((0, "real-failure"), shown)

    def test_output_without_a_final_newline_still_closes_its_block(self):
        repo = FixtureRepo(self)
        repo.add("test_a.py", 'sys.stdout.write("no-newline")\n')
        repo.add("test_b.py", 'sys.stdout.write("also-no-newline")\nsys.exit(1)\n')
        results, stopped, out = repo.run(jobs=1)
        shown = github_view(out.getvalue())
        self.assertIn((1, "no-newline"), shown)
        self.assertIn((0, "also-no-newline"), shown)


class InterruptTests(unittest.TestCase):
    """A cancelled CI job must not lose the evidence of files that were
    still running."""

    def test_a_stop_request_reports_partial_output_and_starts_nothing_new(self):
        repo = FixtureRepo(self)
        repo.add("test_a.py", holder("a-ready", "never-released"))
        repo.add("test_b.py", PASS_BODY)
        self.addCleanup(touch, repo.state_path("never-released"))
        results, stopped, out = repo.run(
            jobs=1, should_stop=lambda: repo.exists("a-ready"))
        self.assertTrue(stopped)
        by_path = {r.relpath: r for r in results}
        self.assertEqual(by_path["tests/test_a.py"].status, "interrupted")
        self.assertEqual(by_path["tests/test_b.py"].status, "not started")
        self.assertIsNotNone(by_path["tests/test_a.py"].returncode)
        self.assertFalse(repo.exists("ran-test_b.py"))
        self.assertFalse(repo.exists("finished-test_a.py"))
        shown = github_view(out.getvalue())
        self.assertIn((0, "holder-evidence test_a.py"), shown)
        self.assertTrue(any(
            depth == 0 and text.startswith(
                "[ci-runner] ======== INTERRUPTED tests/test_a.py")
            for depth, text in shown), shown)

    def test_a_stop_request_during_a_launch_batch_starts_nothing_more(self):
        # Three free slots, three files. The stop request turns true the
        # moment the first file has been started, in the middle of the
        # batch; the other two must not start.
        repo = FixtureRepo(self)
        repo.add("test_a.py", holder("a-ready", "never-released"))
        repo.add("test_b.py", PASS_BODY)
        repo.add("test_c.py", PASS_BODY)
        self.addCleanup(touch, repo.state_path("never-released"))
        out = Recorder()
        results, stopped = _ci_runner.run_files(
            repo.root, _ci_runner.select_files(repo.root), 3, out,
            should_stop=lambda: (b"[ci-runner] start tests/test_a.py\n"
                                 in out.getvalue()))
        self.assertTrue(stopped)
        text = out.text()
        self.assertEqual(re.findall(r"^\[ci-runner\] start (\S+)$", text, re.M),
                         ["tests/test_a.py"], text)
        self.assertEqual({r.relpath: r.status for r in results}, {
            "tests/test_a.py": "interrupted",
            "tests/test_b.py": "not started",
            "tests/test_c.py": "not started",
        })
        self.assertFalse(repo.exists("ran-test_b.py"))
        self.assertFalse(repo.exists("ran-test_c.py"))

    def test_sigterm_to_the_cli_reports_partial_output_and_exits_nonzero(self):
        repo = FixtureRepo(self)
        repo.add("test_a.py", holder("a-ready", "never-released"))
        repo.add("test_b.py", PASS_BODY)
        log_path = repo.state_path("runner.log")
        with open(log_path, "wb") as log:
            process = subprocess.Popen(
                [sys.executable, RUNNER_PATH, "--repo", repo.root,
                 "--jobs", "1"],
                stdout=log, stderr=subprocess.STDOUT, cwd=repo.root)
        self.addCleanup(self._reap, process)
        self.addCleanup(touch, repo.state_path("never-released"))
        wait_until(lambda: repo.exists("a-ready"), "the fixture to start")
        process.send_signal(signal.SIGTERM)
        status = process.wait(WAIT_LIMIT)
        with open(log_path, "rb") as log:
            raw = log.read()
        text = raw.decode("utf-8")
        self.assertEqual(status, 128 + signal.SIGTERM, text)
        shown = github_view(raw)
        self.assertIn((0, "holder-evidence test_a.py"), shown)
        self.assertIn("stopped by signal %d" % signal.SIGTERM, text)
        self.assertRegex(text, r"INTERRUPTED +signal 15 \(SIGTERM\) .*tests/test_a\.py")
        self.assertRegex(text, r"NOT STARTED .*tests/test_b\.py")
        self.assertIn("[ci-runner] verdict: FAIL\n", text)
        self.assertFalse(repo.exists("ran-test_b.py"))
        with open(repo.state_path("a-ready")) as handle:
            fixture_pid = int(handle.read())
        wait_until(lambda: not _alive(fixture_pid), "the fixture to stop", 10)

    @staticmethod
    def _reap(process):
        if process.poll() is None:
            process.kill()
            process.wait()


def touch(path):
    with open(path, "w"):
        pass


def _alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


# ---------------------------------------------------------------------
# Workflow shape.

class WorkflowParseError(ValueError):
    pass


_KEY = re.compile(r"^([A-Za-z0-9_-]+):(?: (.*))?$")


class _Reader(object):
    def __init__(self, text):
        self.lines = text.split("\n")
        self.index = 0

    def skip_blank(self):
        while self.index < len(self.lines):
            stripped = self.lines[self.index].strip()
            if stripped and not stripped.startswith("#"):
                return
            self.index += 1

    def peek(self):
        self.skip_blank()
        if self.index >= len(self.lines):
            return None, None
        line = self.lines[self.index]
        if line.startswith("\t"):
            raise WorkflowParseError("tab indentation at line %d"
                                     % (self.index + 1))
        indent = len(line) - len(line.lstrip(" "))
        return indent, line[indent:]


def _scalar(text, where):
    if text.startswith(("'", '"')):
        quote = text[0]
        if len(text) < 2 or not text.endswith(quote):
            raise WorkflowParseError("unsupported quoted scalar at %s" % where)
        inner = text[1:-1]
        if quote == "'":
            return inner.replace("''", "'")
        if "\\" in inner:
            raise WorkflowParseError("escapes unsupported at %s" % where)
        return inner
    if " #" in text:
        text = text[:text.index(" #")].rstrip()
    if text.startswith(("&", "*", "!", "{", ">", "|", "%", "@", "`")):
        raise WorkflowParseError("unsupported scalar %r at %s" % (text, where))
    return text


def _parse_node(reader, indent):
    found, content = reader.peek()
    if found is None or found < indent:
        return None
    if found != indent:
        raise WorkflowParseError("unexpected indentation at line %d"
                                 % (reader.index + 1))
    if content == "-" or content.startswith("- "):
        return _parse_sequence(reader, indent)
    return _parse_mapping(reader, indent)


def _parse_value(reader, indent, rest, where):
    """The value after ``key:`` on a line indented ``indent``."""
    if rest is None or rest == "" or rest.startswith("#"):
        found, _ = reader.peek()
        if found is None or found <= indent:
            return None
        return _parse_node(reader, found)
    if rest == "|":
        return _block_scalar(reader, indent, where)
    return _scalar(rest, where)


def _block_scalar(reader, indent, where):
    collected = []
    while reader.index < len(reader.lines):
        line = reader.lines[reader.index]
        if line.strip() and len(line) - len(line.lstrip(" ")) <= indent:
            break
        collected.append(line)
        reader.index += 1
    while collected and not collected[-1].strip():
        collected.pop()
    if not collected:
        raise WorkflowParseError("empty block scalar at %s" % where)
    block_indent = min(len(l) - len(l.lstrip(" ")) for l in collected if l.strip())
    return "\n".join(l[block_indent:] for l in collected) + "\n"


def _parse_mapping(reader, indent):
    result = {}
    while True:
        found, content = reader.peek()
        if found is None or found < indent:
            return result
        if found > indent:
            raise WorkflowParseError("unexpected indentation at line %d"
                                     % (reader.index + 1))
        if content.startswith("-"):
            raise WorkflowParseError("sequence inside a mapping at line %d"
                                     % (reader.index + 1))
        match = _KEY.match(content)
        if not match:
            raise WorkflowParseError("not a key at line %d: %r"
                                     % (reader.index + 1, content))
        key, rest = match.group(1), match.group(2)
        if key in result:
            raise WorkflowParseError("duplicate key %r at line %d"
                                     % (key, reader.index + 1))
        where = "line %d" % (reader.index + 1)
        reader.index += 1
        result[key] = _parse_value(reader, indent, rest, where)


def _parse_sequence(reader, indent):
    items = []
    while True:
        found, content = reader.peek()
        if found is None or found < indent:
            return items
        if found > indent or not (content == "-" or content.startswith("- ")):
            if found == indent:
                return items
            raise WorkflowParseError("unexpected indentation at line %d"
                                     % (reader.index + 1))
        rest = content[1:]
        if not rest.strip():
            raise WorkflowParseError("bare '-' item at line %d"
                                     % (reader.index + 1))
        item_indent = indent + 1 + len(rest) - len(rest.lstrip(" "))
        text = rest.lstrip(" ")
        if _KEY.match(text):
            # "- key: value": the item is a mapping whose first key sits
            # on the dash line.
            reader.lines[reader.index] = " " * item_indent + text
            items.append(_parse_mapping(reader, item_indent))
        else:
            reader.index += 1
            items.append(_scalar(text, "line %d" % reader.index))


def parse_workflow(text):
    """Parse the YAML subset ci.yml uses: block mappings, block
    sequences, plain and quoted scalars, ``|`` block scalars, comments.
    Flow collections such as ``[main]`` stay as their raw text.
    Anything else raises WorkflowParseError."""
    reader = _Reader(text)
    document = _parse_node(reader, 0)
    found, _ = reader.peek()
    if found is not None:
        raise WorkflowParseError("trailing content at line %d"
                                 % (reader.index + 1))
    if not isinstance(document, dict):
        raise WorkflowParseError("the document is not a mapping")
    return document


def read_workflow_text():
    with open(WORKFLOW_PATH) as handle:
        return handle.read()


MATRIX_EXPRESSION = re.compile(
    r"^\$\{\{ fromJSON\(github\.event_name == 'pull_request' && "
    r"'([^']*)' \|\| '([^']*)'\) \}\}$")

PR_LEGS = {("ubuntu-latest", "3.9"), ("ubuntu-latest", "3.13"),
           ("macos-latest", "3.13")}
FULL_LEGS = PR_LEGS | {("macos-latest", "3.9")}

NOT_CANCELLED = "${{ !cancelled() }}"

# Requirement 20: the steps that now run after a unit-test failure.
COMPILE_STEP = "Compile Python sources"
CONDITIONED_STEPS = ("Run static integration suite", COMPILE_STEP,
                     "Check for whitespace errors")

# Requirement 21. These two commands are byte-identical on the pinned
# baseline (306c1a6) and on current main (feb2ea3), so exact text is the
# invariant: a rewrite of either must fail here.
EXACT_COMMANDS = {
    "Run static integration suite":
        'PYTHONPATH="$PWD" python tests/test_static.py',
    "Check for whitespace errors":
        'git diff --check "$(git hash-object -t tree /dev/null)"',
}

# Requirement 21, compile step. Its target list legitimately GROWS (main
# adds three targets), so the invariant is: still one single-line
# `python -m py_compile` command, and no baseline target removed.
BASELINE_COMPILE_TARGETS = (
    "herdctl.py", "herdr/*.py", "tests/*.py", "codexgw.py",
    "codex_gateway/*.py", "tgop.py", "telegram_operator/*.py",
    "workflow_authority/*.py", "target_runtime/*.py", "dirun.py",
    "operator_session/*.py", "human_interaction/*.py",
    "durable_execution/*.py", "capability/*.py", "worker/*.py",
    "pr_delivery/*.py", "mission/*.py", "coordination/*.py",
    "local_request/*.py", "direquest.py", "grok_bot/*.py", "grokbot.py",
    "tunnel_control/*.py", "ditunnel.py",
)

# The job timeout may be raised (main uses 45) but never lowered below
# the baseline's value, and both jobs must agree.
BASELINE_TIMEOUT_MINUTES = 30

# Current main (feb2ea3) differs from the pinned baseline's ci.yml in
# exactly two ways: timeout-minutes 45, and this compile command (three
# more targets). Inline on purpose: the copy of main's file under .herd/
# is not part of the repository and does not exist on a CI runner.
MAIN_TIMEOUT_MINUTES = 45
MAIN_COMPILE_COMMAND = (
    "python -m py_compile herdctl.py herdr/*.py tests/*.py codexgw.py "
    "codex_gateway/*.py tgop.py telegram_operator/*.py "
    "workflow_authority/*.py target_runtime/*.py dirun.py "
    "operator_session/*.py human_interaction/*.py durable_execution/*.py "
    "capability/*.py worker/*.py pr_delivery/*.py mission/*.py "
    "coordination/*.py mission_sources/*.py local_request/*.py "
    "direquest.py grok_bot/*.py grokbot.py tunnel_control/*.py "
    "ditunnel.py dicatalog.py dicatalog_http.py"
)

SHOW_ENVIRONMENT = "python --version\ngit --version\n"
RUNNER_STEP_COMMAND = ("exec python tests/_ci_runner.py "
                       "--exclude test_static.py --exclude test_hermetic_git.py")
HERMETIC_COMMAND = 'PYTHONPATH="$PWD" python tests/test_hermetic_git.py'
_TEST_FILE = re.compile(r"(?<![\w/])tests/(test_\w+\.py)\b")


def steps_running(job, needle):
    return [step for step in job.get("steps") or []
            if needle in (step.get("run") or "")]


def jobs_running(workflow, needle):
    return sorted(name for name, job in workflow["jobs"].items()
                  if steps_running(job, needle))


def runner_selection(run_text, repo=REPO_ROOT):
    """The files the runner step's own arguments select, decided by the
    runner's own parser and selection code."""
    words = shlex.split(run_text)
    if words and words[0] == "exec":
        words = words[1:]
    if words[:2] != ["python", "tests/_ci_runner.py"]:
        raise AssertionError("unexpected runner invocation: %r" % run_text)
    args = _ci_runner.build_parser().parse_args(words[2:])
    if args.repo != _ci_runner.REPO_ROOT:
        raise AssertionError("the CI invocation must not move --repo")
    return {os.path.basename(path)
            for path in _ci_runner.select_files(repo, args.exclude)}


def coverage_accounting(workflow, repo=REPO_ROOT):
    """Where every tests/test_*.py file runs, derived from the workflow.

    Returns (on_disk, by_runner, by_direct_steps): the files on disk,
    the files the runner step selects, and the files run directly by
    name in any other step, each a set of basenames."""
    on_disk = {name for name in os.listdir(os.path.join(repo, "tests"))
               if re.match(r"^test_.*\.py$", name)
               and os.path.isfile(os.path.join(repo, "tests", name))}
    (main_name,) = jobs_running(workflow, "tests/_ci_runner.py")
    (runner_step,) = steps_running(workflow["jobs"][main_name],
                                   "tests/_ci_runner.py")
    by_runner = runner_selection(runner_step["run"], repo)
    by_direct = set()
    for job in workflow["jobs"].values():
        for step in job.get("steps") or []:
            run = step.get("run") or ""
            if "tests/_ci_runner.py" not in run:
                by_direct.update(_TEST_FILE.findall(run))
    return on_disk, by_runner, by_direct


def compile_problems(job, text):
    """Why the compile step breaks its invariant; empty when it holds."""
    steps = [s for s in job["steps"] if s.get("name") == COMPILE_STEP]
    if len(steps) != 1:
        return ["expected one %r step, found %d" % (COMPILE_STEP, len(steps))]
    run = (steps[0].get("run") or "").strip()
    problems = []
    if "\n" in run:
        problems.append("the compile command spans more than one line")
    words = run.split()
    if words[:3] != ["python", "-m", "py_compile"]:
        problems.append("not a 'python -m py_compile' command: %r" % words[:3])
    targets = words[3:]
    chained = [w for w in targets
               if "py_compile" in w or any(c in w for c in ";&|<>`$")]
    if chained:
        problems.append("more than one command in the compile step: %r"
                        % chained)
    removed = [t for t in BASELINE_COMPILE_TARGETS if t not in targets]
    if removed:
        problems.append("baseline compile targets removed: %r" % removed)
    lines = [line for line in text.splitlines() if "py_compile" in line]
    if len(lines) != 1:
        problems.append("expected one line naming py_compile, found %d"
                        % len(lines))
    elif lines[0].strip() != "run: " + run:
        problems.append("the only py_compile line is not the compile "
                        "step's own one-line command: %r" % lines[0])
    return problems


def timeout_problems(jobs):
    """Why the job timeouts break their invariant; empty when it holds."""
    problems = []
    values = {}
    for name in sorted(jobs):
        value = jobs[name].get("timeout-minutes")
        if value is None:
            problems.append("%s has no timeout-minutes" % name)
        elif not re.match(r"^[0-9]+$", value):
            problems.append("%s timeout-minutes is not an integer: %r"
                            % (name, value))
        else:
            values[name] = int(value)
    if len(set(values.values())) > 1:
        problems.append("the jobs disagree on timeout-minutes: %r" % values)
    lowered = {name: value for name, value in values.items()
               if value < BASELINE_TIMEOUT_MINUTES}
    if lowered:
        problems.append("timeout-minutes lowered below %d: %r"
                        % (BASELINE_TIMEOUT_MINUTES, lowered))
    return problems


# Structural edits of a workflow TEXT. They locate what they change in
# the text they are given (its timeout lines, its one py_compile line),
# never by matching baseline literals, so they work on any revision of
# ci.yml, including one already reconciled onto main.
_TIMEOUT_LINE = re.compile(r"^([ \t]*timeout-minutes: )(\S+)$", re.M)
_COMPILE_RUN = re.compile(r"^([ \t]*run: )(.*)$")


def observed_timeouts(text):
    """The timeout-minutes values in ``text``, in file order."""
    return [match.group(2) for match in _TIMEOUT_LINE.finditer(text)]


def with_timeouts(text, values):
    """``text`` with its timeout-minutes values replaced in file order.
    ``values`` holds one entry per timeout line; None removes that line."""
    matches = list(_TIMEOUT_LINE.finditer(text))
    if len(matches) != len(values):
        raise AssertionError("%d timeout lines but %d values"
                             % (len(matches), len(values)))
    pieces = []
    position = 0
    for match, value in zip(matches, values):
        pieces.append(text[position:match.start()])
        if value is None:
            position = match.end() + 1  # the line and its newline
        else:
            pieces.append(match.group(1) + str(value))
            position = match.end()
    pieces.append(text[position:])
    return "".join(pieces)


def compile_line(text):
    """``(prefix, command)`` of the single line naming py_compile, where
    ``prefix`` is its indentation plus ``run: ``."""
    lines = [line for line in text.splitlines() if "py_compile" in line]
    if len(lines) != 1:
        raise AssertionError("expected one line naming py_compile, found %d"
                             % len(lines))
    match = _COMPILE_RUN.match(lines[0])
    if not match:
        raise AssertionError("the py_compile line is not a run line: %r"
                             % lines[0])
    return match.group(1), match.group(2)


def with_compile_command(text, command):
    """``text`` with its one py_compile command replaced by ``command``."""
    prefix, old = compile_line(text)
    old_line = prefix + old + "\n"
    if text.count(old_line) != 1:
        raise AssertionError("the py_compile line is not unique")
    return text.replace(old_line, prefix + command + "\n")


def project_current_main(text):
    """``text`` with current main's two ci.yml differences applied where
    they are not already present, derived from ``text`` itself:

    - each job timeout below main's 45 is raised to 45; an equal or
      higher value is kept;
    - each of main's compile targets that is absent is added, after the
      target that precedes it on main when that one is present (so the
      baseline projects to main's exact command), else at the end.

    A workflow that already carries both differences projects to itself.
    """
    values = []
    for value in observed_timeouts(text):
        if re.match(r"^[0-9]+$", value) and int(value) < MAIN_TIMEOUT_MINUTES:
            value = str(MAIN_TIMEOUT_MINUTES)
        values.append(value)
    text = with_timeouts(text, values)
    words = compile_line(text)[1].split()
    main_words = MAIN_COMPILE_COMMAND.split()
    added = False
    for target in main_words[3:]:
        if target in words:
            continue
        before = main_words[main_words.index(target) - 1]
        if before in words[3:]:
            words.insert(words.index(before) + 1, target)
        else:
            words.append(target)
        added = True
    if added:
        text = with_compile_command(text, " ".join(words))
    return text


class WorkflowParserTests(unittest.TestCase):
    """The parser the shape tests rely on, checked on its own."""

    def test_it_parses_the_constructs_ci_yml_uses(self):
        text = textwrap.dedent("""\
            name: CI
            # comment
            on:
              push:
                branches: [main]
              pull_request:
            jobs:
              a:
                name: x / ${{ matrix.os }}
                steps:
                  - name: one
                    uses: actions/checkout@v6
                    with:
                      python-version: "3.13"

                  - name: two
                    run: |
                      first
                        indented

                      last
                  - name: three
                    run: echo 'it''s' # trailing comment
            """)
        self.assertEqual(parse_workflow(text), {
            "name": "CI",
            "on": {"push": {"branches": "[main]"}, "pull_request": None},
            "jobs": {"a": {
                "name": "x / ${{ matrix.os }}",
                "steps": [
                    {"name": "one", "uses": "actions/checkout@v6",
                     "with": {"python-version": "3.13"}},
                    {"name": "two", "run": "first\n  indented\n\nlast\n"},
                    {"name": "three", "run": "echo 'it''s'"},
                ],
            }},
        })

    def test_it_refuses_what_it_does_not_understand(self):
        for text in ("a: 1\na: 2\n", "a:\n\tb: 1\n", "a: &anchor 1\n",
                     "a: {b: 1}\n", "a: >\n  folded\n", "a: 1\n  b: 2\n",
                     "- top-level list\n", "a:\n- same-indent item\n"):
            with self.assertRaises(WorkflowParseError, msg=text):
                parse_workflow(text)


class WorkflowShapeTests(unittest.TestCase):
    """Requirements 11-21, against the real .github/workflows/ci.yml."""

    @classmethod
    def workflow_text(cls):
        return read_workflow_text()

    @classmethod
    def setUpClass(cls):
        cls.text = cls.workflow_text()
        cls.workflow = parse_workflow(cls.text)
        cls.jobs = cls.workflow["jobs"]
        (cls.main_name,) = jobs_running(cls.workflow, "tests/_ci_runner.py")
        (cls.hermetic_name,) = jobs_running(cls.workflow,
                                            "tests/test_hermetic_git.py")
        cls.main = cls.jobs[cls.main_name]
        cls.hermetic = cls.jobs[cls.hermetic_name]

    def step(self, job, name):
        (found,) = [s for s in job["steps"] if s.get("name") == name]
        return found

    def test_11_every_test_file_runs_somewhere(self):
        on_disk, by_runner, by_direct = coverage_accounting(self.workflow)
        self.assertGreater(len(on_disk), 20)
        self.assertEqual(by_direct, {"test_static.py", "test_hermetic_git.py"})
        self.assertEqual(by_runner | by_direct, on_disk,
                         "files that run nowhere: %s"
                         % sorted(on_disk - by_runner - by_direct))

    def test_11_the_accounting_notices_a_dropped_file(self):
        # Negative control: excluding one more file from the runner, with
        # no step that runs it, must show up as a gap.
        mutated = self.text.replace(
            "--exclude test_hermetic_git.py",
            "--exclude test_hermetic_git.py --exclude test_ci_runner.py")
        self.assertNotEqual(mutated, self.text)
        on_disk, by_runner, by_direct = coverage_accounting(
            parse_workflow(mutated))
        self.assertEqual(on_disk - by_runner - by_direct, {"test_ci_runner.py"})

    def test_12_the_main_job_excludes_static_and_hermetic(self):
        (runner_step,) = steps_running(self.main, "tests/_ci_runner.py")
        selected = runner_selection(runner_step["run"])
        self.assertNotIn("test_static.py", selected)
        self.assertNotIn("test_hermetic_git.py", selected)
        self.assertEqual(runner_step["run"], RUNNER_STEP_COMMAND)
        self.assertEqual(runner_step.get("shell"), "bash")

    def test_13_a_separate_job_runs_hermetic_git_without_needs(self):
        self.assertNotEqual(self.hermetic_name, self.main_name)
        self.assertNotIn("needs", self.hermetic)
        self.assertNotIn("needs", self.main)
        runs = [s["run"] for s in self.hermetic["steps"] if "run" in s]
        self.assertEqual(runs, [SHOW_ENVIRONMENT, HERMETIC_COMMAND])
        self.assertEqual(set(self.jobs), {self.main_name, self.hermetic_name})

    def test_14_both_jobs_use_byte_identical_matrix_expressions(self):
        lines = [line for line in self.text.splitlines()
                 if line.strip().startswith("include:")]
        self.assertEqual(len(lines), 2)
        self.assertEqual(lines[0], lines[1])
        self.assertEqual(self.main["strategy"], self.hermetic["strategy"])
        self.assertEqual(self.main["strategy"]["fail-fast"], "false")
        self.assertEqual(set(self.main["strategy"]["matrix"]), {"include"})

    def test_15_the_event_matrices_decode_to_the_required_legs(self):
        for job in (self.main, self.hermetic):
            match = MATRIX_EXPRESSION.match(job["strategy"]["matrix"]["include"])
            self.assertIsNotNone(match)
            decoded = []
            for raw in match.groups():
                entries = json.loads(raw)
                for entry in entries:
                    self.assertEqual(set(entry), {"os", "python-version"})
                legs = [(e["os"], e["python-version"]) for e in entries]
                self.assertEqual(len(legs), len(set(legs)))
                decoded.append(set(legs))
            self.assertEqual(decoded[0], PR_LEGS)
            self.assertEqual(decoded[1], FULL_LEGS)
        # Only pull_request takes the reduced matrix; every other trigger
        # gets the full one, so the trigger set itself is pinned.
        self.assertEqual(self.workflow["on"], {
            "push": {"branches": "[main]"},
            "pull_request": None,
            "workflow_dispatch": None,
        })

    def test_16_permissions_stay_read_only(self):
        self.assertEqual(self.workflow["permissions"], {"contents": "read"})
        for name, job in self.jobs.items():
            self.assertNotIn("permissions", job, name)

    def test_17_action_references_are_unchanged(self):
        for name, job in self.jobs.items():
            uses = [s["uses"] for s in job["steps"] if "uses" in s]
            self.assertEqual(uses, ["actions/checkout@v6",
                                    "actions/setup-python@v6"], name)

    def test_18_static_compile_and_whitespace_steps_still_exist(self):
        names = [s.get("name") for s in self.main["steps"]]
        self.assertEqual(names, [
            "Check out repository", "Set up Python", "Show environment",
            "Run unit tests", "Run static integration suite",
            "Compile Python sources", "Check for whitespace errors",
        ])
        self.assertEqual(self.step(self.main, "Show environment")["run"],
                         SHOW_ENVIRONMENT)
        self.assertEqual(timeout_problems(self.jobs), [])

    def test_19_only_standard_hosted_runners(self):
        for name, job in self.jobs.items():
            self.assertEqual(job["runs-on"], "${{ matrix.os }}", name)
        self.assertEqual({os_name for os_name, _ in FULL_LEGS},
                         {"ubuntu-latest", "macos-latest"})

    def test_20_later_steps_run_after_a_failure_but_not_after_cancel(self):
        for name in CONDITIONED_STEPS:
            self.assertEqual(self.step(self.main, name).get("if"),
                             NOT_CANCELLED, name)
        self.assertNotIn("if", self.step(self.main, "Run unit tests"))
        for job_name, job in self.jobs.items():
            self.assertNotIn("continue-on-error", job, job_name)
            self.assertNotIn("if", job, job_name)
            for step in job["steps"]:
                self.assertNotIn("continue-on-error", step)
                self.assertNotIn("always()", step.get("if") or "")
        conditioned = [s.get("name") for s in self.main["steps"] if "if" in s]
        self.assertEqual(sorted(conditioned), sorted(CONDITIONED_STEPS))
        self.assertEqual([s for s in self.hermetic["steps"] if "if" in s], [])

    def test_21_conditioned_commands_keep_their_baseline_text(self):
        for name, command in EXACT_COMMANDS.items():
            self.assertEqual(self.step(self.main, name)["run"], command, name)
        # One single-line py_compile command that keeps every baseline
        # target (the release-narrative guard reads the LAST py_compile
        # line, so there must be exactly one).
        self.assertEqual(compile_problems(self.main, self.text), [])


class ProjectedMainWorkflowShapeTests(WorkflowShapeTests):
    """Every shape test above, re-run over this workflow with current
    main's two differences applied (timeout 45, three more compile
    targets): a reconciliation that keeps main's compile coverage and
    timeout must pass this suite unchanged."""

    @classmethod
    def workflow_text(cls):
        return project_current_main(read_workflow_text())

    def test_the_projection_carries_mains_differences(self):
        source = read_workflow_text()
        projected_targets = compile_line(self.text)[1].split()[3:]
        self.assertLessEqual(set(MAIN_COMPILE_COMMAND.split()[3:]),
                             set(projected_targets))
        self.assertLessEqual(set(compile_line(source)[1].split()[3:]),
                             set(projected_targets))
        before = observed_timeouts(source)
        after = observed_timeouts(self.text)
        self.assertEqual(len(before), len(after))
        self.assertGreaterEqual(len(after), 2)
        for old, new in zip(before, after):
            self.assertEqual(int(new), max(int(old), MAIN_TIMEOUT_MINUTES))
        # A workflow that already carries main's differences is a valid
        # input that projects to itself.
        self.assertEqual(project_current_main(self.text), self.text)


class ProjectionTests(unittest.TestCase):
    """project_current_main on inputs derived from the workflow under
    test, whatever revision of ci.yml that is."""

    def baseline_variant(self):
        """The workflow under test with the pinned baseline's two values
        put back: every timeout 30, and the baseline compile command."""
        text = read_workflow_text()
        text = with_timeouts(
            text, [BASELINE_TIMEOUT_MINUTES] * len(observed_timeouts(text)))
        return with_compile_command(
            text, "python -m py_compile " + " ".join(BASELINE_COMPILE_TARGETS))

    def test_the_baseline_values_project_to_mains_exact_values(self):
        baseline = self.baseline_variant()
        projected = project_current_main(baseline)
        self.assertNotEqual(projected, baseline)
        self.assertEqual(compile_line(projected)[1], MAIN_COMPILE_COMMAND)
        self.assertEqual(set(observed_timeouts(projected)),
                         {str(MAIN_TIMEOUT_MINUTES)})

    def test_an_already_reconciled_workflow_projects_to_itself(self):
        reconciled = project_current_main(self.baseline_variant())
        self.assertEqual(project_current_main(reconciled), reconciled)

    def test_a_timeout_above_mains_is_kept(self):
        text = read_workflow_text()
        count = len(observed_timeouts(text))
        higher = with_timeouts(text, [MAIN_TIMEOUT_MINUTES + 15] * count)
        self.assertEqual(observed_timeouts(project_current_main(higher)),
                         [str(MAIN_TIMEOUT_MINUTES + 15)] * count)


class ShapeInvariantControlTests(unittest.TestCase):
    """Negative controls: the compile and timeout checks still reject the
    changes they exist to catch. Every mutation is derived from the
    workflow under test -- its observed timeouts and compile targets --
    so the controls hold on any revision of ci.yml. The subclass below
    runs them all again on the main-projected workflow.

    Scope: the removal check protects the baseline's compile targets.
    Targets added later (main's three included) are accepted additions
    and are not protected by this module."""

    @classmethod
    def source_text(cls):
        return read_workflow_text()

    @classmethod
    def setUpClass(cls):
        cls.text = cls.source_text()
        cls.timeouts = [int(value) for value in observed_timeouts(cls.text)]
        cls.command = compile_line(cls.text)[1]

    def problems(self, text):
        workflow = parse_workflow(text)
        (main_name,) = jobs_running(workflow, "tests/_ci_runner.py")
        return (compile_problems(workflow["jobs"][main_name], text),
                timeout_problems(workflow["jobs"]))

    def test_the_unmodified_workflow_has_no_problems(self):
        self.assertEqual(self.problems(self.text), ([], []))
        self.assertGreaterEqual(len(self.timeouts), 2)

    def test_lowering_every_timeout_below_the_floor_is_rejected(self):
        for lowered in (BASELINE_TIMEOUT_MINUTES - 1, 10):
            self.assertLess(lowered, min(self.timeouts))
            text = with_timeouts(self.text, [lowered] * len(self.timeouts))
            problems = self.problems(text)[1]
            self.assertEqual(len(problems), 1, problems)
            self.assertTrue(problems[0].startswith(
                "timeout-minutes lowered below %d: " % BASELINE_TIMEOUT_MINUTES),
                problems)

    def test_lowering_one_job_timeout_is_rejected(self):
        for index in range(len(self.timeouts)):
            values = list(self.timeouts)
            values[index] -= 1
            problems = self.problems(with_timeouts(self.text, values))[1]
            self.assertTrue(problems, values)
            self.assertIn("disagree", problems[0])

    def test_a_raised_timeout_is_accepted_only_when_both_jobs_agree(self):
        raised = [value + 15 for value in self.timeouts]
        self.assertEqual(self.problems(with_timeouts(self.text, raised))[1], [])
        one = list(self.timeouts)
        one[0] += 15
        self.assertIn("disagree",
                      self.problems(with_timeouts(self.text, one))[1][0])

    def test_a_missing_or_non_integer_timeout_is_rejected(self):
        for index in range(len(self.timeouts)):
            dropped = list(self.timeouts)
            dropped[index] = None
            self.assertIn("has no timeout-minutes",
                          self.problems(with_timeouts(self.text, dropped))[1][0])
            odd = list(self.timeouts)
            odd[index] = "%d.5" % self.timeouts[index]
            self.assertIn("not an integer",
                          self.problems(with_timeouts(self.text, odd))[1][0])

    def test_removing_any_observed_baseline_target_is_rejected(self):
        words = self.command.split()
        removed = 0
        for target in words[3:]:
            if target not in BASELINE_COMPILE_TARGETS:
                continue
            kept = [word for word in words if word != target]
            text = with_compile_command(self.text, " ".join(kept))
            self.assertEqual(self.problems(text)[0], [
                "baseline compile targets removed: %r" % [target]])
            removed += 1
        self.assertEqual(removed, len(BASELINE_COMPILE_TARGETS))

    def test_added_compile_targets_are_accepted(self):
        text = with_compile_command(self.text, self.command + " extra/*.py")
        self.assertEqual(self.problems(text), ([], []))

    def test_a_second_py_compile_line_is_rejected(self):
        text = self.text + "# python -m py_compile later.py\n"
        self.assertEqual(self.problems(text)[0], [
            "expected one line naming py_compile, found 2"])

    def test_a_chained_compile_command_is_rejected(self):
        text = with_compile_command(self.text, self.command + " && echo done")
        problems = self.problems(text)[0]
        self.assertEqual(len(problems), 1, problems)
        self.assertIn("more than one command", problems[0])

    def test_a_different_compile_command_is_rejected(self):
        words = self.command.split()
        text = with_compile_command(
            self.text, " ".join(["python3", "-m", "compileall", "-q"] + words[3:]))
        problems = self.problems(text)[0]
        self.assertIn("not a 'python -m py_compile' command", problems[0])


class ProjectedShapeInvariantControlTests(ShapeInvariantControlTests):
    """The same controls on the main-projected workflow (timeouts 45 and
    main's compile targets), so they are shown to hold there too."""

    @classmethod
    def source_text(cls):
        return project_current_main(read_workflow_text())


if __name__ == "__main__":
    unittest.main(verbosity=2)
