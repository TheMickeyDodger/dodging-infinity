"""Failed agent-start diagnostic (observability only).

When ``runtime.start_agent`` is given a ``diagnostic_dir`` and the start
fails terminally, a bounded, redacted diagnostic of the failed pane and its
processes is written BEFORE the error is raised, so it survives lifecycle's
workspace cleanup. These tests use no real herdr, agent, spawn or
transport: the herdr command is either a recording fake function or a
self-terminating fake executable placed first on PATH. Every test that can
reach a loop or a blocking read carries an independent bound
(CONTRIBUTING.md test termination rule): a fake clock with an exhaustion
stop, or a fake executable that exits on its own.
"""

import json
import os
import stat
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from herdr import runtime as runtime_module
from herdr.lifecycle import start_herd

from test_lifecycle import HerdrLifecycleTests, _bound_probe

LC = "lc-" + "ab12" * 16
SECRETS = (
    LC,
    "sk-live0123456789abcdefXYZ",
    "ghp_0123456789abcdefghijABCDEFGHIJ",
    "github_pat_11ABCDEFG0123456789_abcdefghijklmnop",
    "xoxb-1234567890-abcdefghij",
    "AKIAABCDEFGHIJKLMNOP",
    "hunter2pass",
    "zzapikeyvalue",
    "s3cr3t-next-argv",
    "tok3n-inline",
    "opaqueBearerValue.part2",
    "envsecretvalue",
    # Short values in forms with no "=" and no unquoted "key:" (pinned).
    "pwShortA",
    "akShortB",
    "pwShortC",
    "akShortD",
)
PANE_TEXT = (
    "Welcome to the agent\n"
    "export TOKEN=" + LC + "\n"
    "password=hunter2pass and api_key: zzapikeyvalue\n"
    "Authorization: Bearer opaqueBearerValue.part2\n"
    "keys sk-live0123456789abcdefXYZ ghp_0123456789abcdefghijABCDEFGHIJ\n"
    "slack xoxb-1234567890-abcdefghij aws AKIAABCDEFGHIJKLMNOP\n"
    "PANE-SCREEN-MARKER trust this folder? [y/N]\n"
    "$ login --password pwShortC\n"
    'config {"api_key":"akShortD","model":"m"}\n'
)
PROCESS_INFO = {
    "result": {"processes": [{
        "pid": 4242, "name": "codex", "argv0": "codex", "cwd": "/abs/ws",
        "argv": ["codex", "--api-key", "s3cr3t-next-argv",
                 "--token=tok3n-inline", "-m", "gpt-6-astra",
                 "github_pat_11ABCDEFG0123456789_abcdefghijklmnop"],
        "cmdline": ("codex --api-key s3cr3t-next-argv " + LC
                    + ' --password pwShortA --config {"api_key":"akShortB"}'),
        "environ": {"AWS_SECRET_ACCESS_KEY": "envsecretvalue"},
        "env": "HOME=/x SECRET=envsecretvalue",
    }]},
}
FAILED_START = SimpleNamespace(
    returncode=1, stdout="",
    stderr='{"error":{"code":"agent_start_timeout"}}')
BUSY_START = SimpleNamespace(
    returncode=1, stdout="",
    stderr='{"error":{"code":"agent_pane_busy"}}')
ROLE = {"kind": "codex", "args": ["-m", "gpt-6-astra"]}


class FakeHerdr(object):
    """A recording fake for ``_bounded_command``: never runs anything."""

    def __init__(self, log, pane_text=PANE_TEXT, info=None):
        self.log = log
        self.pane_text = pane_text
        self.info = PROCESS_INFO if info is None else info

    def __call__(self, cmd):
        self.log.append(("diagnostic", tuple(cmd)))
        base = {"started": True, "returncode": 0, "timed_out": False,
                "elapsed_seconds": 0.0, "stderr": "",
                "stdout_truncated_at_read": False,
                "stderr_truncated_at_read": False}
        if cmd[:3] == ["herdr", "pane", "process-info"]:
            return dict(base, stdout=json.dumps(self.info))
        return dict(base, stdout=self.pane_text)


FAKE_EXECUTABLE = textwrap.dedent("""\
    #!%(python)s
    # A self-terminating fake `herdr` for tests: it never talks to a
    # server and always exits on its own.
    import json, os, subprocess, sys, time
    argv = sys.argv[1:]
    pidfile = os.environ.get("FAKE_HERDR_PIDFILE")
    if pidfile:
        with open(pidfile + ".tmp", "w") as handle:
            handle.write(str(os.getpid()))
        os.replace(pidfile + ".tmp", pidfile)
    if argv[:2] == ["pane", "read"] and "descendant" in argv:
        # The leader exits at once; a descendant in the same process group
        # keeps the inherited output pipe open (self-bound 20 s).
        subprocess.Popen([sys.executable, "-c", (
            "import os, sys, time\\n"
            "open(sys.argv[1] + '.tmp', 'w').write(str(os.getpid()))\\n"
            "os.replace(sys.argv[1] + '.tmp', sys.argv[1])\\n"
            "time.sleep(20)\\n"), pidfile + ".child"])
        sys.exit(0)
    elif argv[:2] == ["pane", "process-info"]:
        sys.stdout.write(%(info)r)
    elif argv[:2] == ["pane", "read"] and "detection" in argv:
        time.sleep(20)          # hangs; self-bound 20 s
    elif argv[:2] == ["pane", "read"] and "visible" in argv:
        block = b"x" * 65536
        for _ in range(1024):   # 64 MiB at most, then exits
            sys.stdout.buffer.write(block)
            sys.stdout.buffer.flush()
    else:
        sys.stderr.write("fake herdr: unexpected %%r" %% (argv,))
        sys.exit(2)
""")


def fake_herdr_on_path(case):
    temp = tempfile.TemporaryDirectory()
    case.addCleanup(temp.cleanup)
    path = Path(temp.name) / "herdr"
    path.write_text(FAKE_EXECUTABLE % {
        "python": sys.executable, "info": json.dumps(PROCESS_INFO)})
    path.chmod(0o700)
    patcher = patch.dict(os.environ, {
        "PATH": temp.name + os.pathsep + os.environ.get("PATH", "")})
    patcher.start()
    case.addCleanup(patcher.stop)


def only_file(directory):
    files = sorted(Path(directory).glob("agent-start-failure-*.json"))
    assert len(files) == 1, files
    return files[0]


class CaptureOrderingAndContentTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.dir = Path(temp.name) / "state" / "diagnostics"
        self.log = []

    def start(self, result=FAILED_START, **kwargs):
        def fake_run(cmd, cwd=None, check=False):
            self.log.append(("run", tuple(cmd)))
            return result
        with patch.object(runtime_module, "run", side_effect=fake_run), \
                patch.object(runtime_module, "_bounded_command",
                             new=FakeHerdr(self.log)):
            with self.assertRaises(RuntimeError) as caught:
                runtime_module.start_agent(
                    "di-sup", "pane-98", ROLE, 60000,
                    diagnostic_dir=self.dir, **kwargs)
        return str(caught.exception)

    def test_capture_runs_the_documented_bounded_commands_before_the_raise(self):
        message = self.start()
        self.assertEqual([entry[0] for entry in self.log],
                         ["run", "diagnostic", "diagnostic", "diagnostic"])
        self.assertEqual(self.log[1][1], (
            "herdr", "pane", "process-info", "--pane", "pane-98"))
        self.assertEqual(self.log[2][1], (
            "herdr", "pane", "read", "pane-98", "--source", "detection",
            "--lines", str(runtime_module.DIAGNOSTIC_PANE_LINES)))
        self.assertEqual(self.log[3][1], (
            "herdr", "pane", "read", "pane-98", "--source", "visible",
            "--lines", str(runtime_module.DIAGNOSTIC_PANE_LINES)))
        path = only_file(self.dir)
        self.assertIn("Start-failure diagnostic: %s" % path, message)
        self.assertTrue(message.startswith(
            "start di-sup failed after 1 attempt(s):\n"
            '{"error":{"code":"agent_start_timeout"}}\n'))
        # A pointer only: no pane or process content in the error.
        self.assertNotIn("PANE-SCREEN-MARKER", message)
        self.assertNotIn("codex --api-key", message)
        record = json.loads(path.read_text())
        self.assertEqual(record["agent"], "di-sup")
        self.assertEqual(record["kind"], "codex")
        self.assertEqual(record["pane"], "pane-98")
        self.assertEqual(record["start_timeout_ms"], 60000)
        self.assertEqual(record["attempts"], 1)
        self.assertEqual(record["agent_start"]["returncode"], 1)
        self.assertIn("agent_start_timeout", record["agent_start"]["stderr"])
        self.assertIn("PANE-SCREEN-MARKER",
                      record["pane_read_detection"]["stdout"])
        self.assertTrue(record["captured_at"].endswith("Z"))
        self.assertIn("best-effort", record["redaction"])

    def test_planted_secrets_are_absent_and_no_environment_is_stored(self):
        self.start()
        text = only_file(self.dir).read_text()
        for secret in SECRETS:
            self.assertNotIn(secret, text, secret)
        self.assertIn(runtime_module.REDACTED, text)
        record = json.loads(text)
        process = record["processes"][0]
        self.assertEqual(sorted(process),
                         ["argv", "argv0", "cmdline", "cwd", "name", "pid"])
        self.assertEqual(process["argv"][:3],
                         ["codex", "--api-key", runtime_module.REDACTED])
        self.assertEqual(process["argv"][3], "--token=" + runtime_module.REDACTED)
        self.assertEqual(process["argv"][4:6], ["-m", "gpt-6-astra"])
        self.assertNotIn("environ", text)
        self.assertNotIn("HOME=/x", text)
        # process-info's raw stdout is never stored, only its parsed fields.
        self.assertNotIn("stdout", record["process_info"])

    def test_private_storage_and_atomic_write(self):
        self.start()
        path = only_file(self.dir)
        self.assertEqual(stat.S_IMODE(os.stat(self.dir).st_mode), 0o700)
        self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o600)
        self.assertEqual(sorted(p.name for p in self.dir.iterdir()),
                         [path.name])
        self.assertRegex(path.name,
                         r"^agent-start-failure-di-sup-\d{8}T\d{6}Z-[0-9a-f]{8}\.json$")

    def test_enabled_busy_terminal_failure_never_uses_the_legacy_capture(self):
        clock = [0.0]

        def fake_monotonic():
            clock[0] += 0.6
            if clock[0] > 60:       # exhaustion stop, independent bound
                raise AssertionError("busy loop did not terminate")
            return clock[0]

        with patch.object(runtime_module.time, "monotonic", fake_monotonic), \
                patch.object(runtime_module.time, "sleep", lambda s: None), \
                patch("builtins.print"):
            message = self.start(result=BUSY_START, shell_ready_timeout_ms=0)
        runs = [entry for entry in self.log if entry[0] == "run"]
        # Only the agent start itself went through the unbounded runner.
        self.assertTrue(runs)
        for _, cmd in runs:
            self.assertEqual(cmd[:3], ("herdr", "agent", "start"))
        self.assertNotIn("Pane diagnostics", message)
        self.assertNotIn("PANE-SCREEN-MARKER", message)
        self.assertNotIn("recent output", message)
        self.assertIn("Start-failure diagnostic: ", message)
        record = json.loads(only_file(self.dir).read_text())
        self.assertTrue(record["busy_terminal_failure"])
        self.assertEqual(record["attempts"], len(runs))


class RedactionTests(unittest.TestCase):
    def test_space_separated_flags_and_quoted_json_pairs_are_redacted(self):
        """Pinned formats: a short value survives neither a space-separated
        secret flag nor a quoted JSON pair, in cmdline-style text and in
        free pane text alike."""
        cases = {
            "--password pwShortA": "--password <REDACTED>",
            '{"api_key":"akShortB"}': '{"api_key":<REDACTED>}',
            # Round 22: a later pair in the same word, and a spaced
            # separator.
            '{"name":"worker","api_key":"shortSecretA"}':
                '{"name":"worker","api_key":<REDACTED>}',
            '{"api_key" : "shortSecretD"}': '{"api_key" : <REDACTED>',
            '{"api_key" :"shortSecretE"}': '{"api_key" :<REDACTED>',
            "--token=abc,def": "--token=<REDACTED>,<REDACTED>",
            "a=1&token=zz&b=2": "a=1&token=<REDACTED>&b=2",
            '{"model":"m","effort":"high"}': '{"model":"m","effort":"high"}',
            '"api_key": "akShortB"': '"api_key": <REDACTED>',
            "--api-key=short1": "--api-key=<REDACTED>",
            "token: short2": "token: <REDACTED>",
            "Authorization: Bearer short3": "Authorization: <REDACTED> <REDACTED>",
            "-m gpt-6-astra --permission-mode auto":
                "-m gpt-6-astra --permission-mode auto",
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                self.assertEqual(runtime_module.redact_text(text), expected)
        # In a process cmdline and in pane text, through the written file:
        # covered by the planted pwShortA/akShortB (cmdline) and
        # pwShortC/akShortD (pane) values in SECRETS.
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        directory = Path(temp.name) / "d"
        with patch.object(runtime_module, "_bounded_command",
                          new=FakeHerdr([])):
            path = runtime_module.capture_start_failure(
                directory, "a", "p1", ROLE, 60000, 1, FAILED_START, False)
        text = path.read_text()
        for value in ("pwShortA", "akShortB", "pwShortC", "akShortD"):
            self.assertNotIn(value, text)
        record = json.loads(text)
        self.assertIn("--password <REDACTED>", record["processes"][0]["cmdline"])
        self.assertIn("--password <REDACTED>",
                      record["pane_read_visible"]["stdout"])

    def test_later_pairs_and_spaced_separators_never_reach_the_file(self):
        """Round 22, finding 1: a secret pair AFTER another pair in the same
        word, and a pair with whitespace around its separator, are redacted
        in the serialized cmdline AND pane text."""
        info = {"result": {"processes": [{
            "pid": 7, "name": "codex", "argv0": "codex", "cwd": "/w",
            "argv": ["codex"],
            "cmdline": ('codex --config {"name":"worker","api_key":'
                        '"shortSecretA"} --x {"api_key" : "shortSecretD"}'),
        }]}}
        pane = ('cfg {"name":"worker","api_key":"shortSecretB"}\n'
                'also {"api_key" : "shortSecretE"} end\n')
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        with patch.object(runtime_module, "_bounded_command",
                          new=FakeHerdr([], pane_text=pane, info=info)):
            path = runtime_module.capture_start_failure(
                Path(temp.name) / "d", "a", "p1", ROLE, 60000, 1,
                FAILED_START, False)
        text = path.read_text()
        for value in ("shortSecretA", "shortSecretB", "shortSecretD",
                      "shortSecretE"):
            self.assertNotIn(value, text)
        record = json.loads(text)
        cmdline = record["processes"][0]["cmdline"]
        self.assertIn('"name":"worker","api_key":<REDACTED>', cmdline)
        self.assertIn('{"api_key" : <REDACTED>', cmdline)
        for field in ("pane_read_detection", "pane_read_visible"):
            self.assertIn('"name":"worker","api_key":<REDACTED>',
                          record[field]["stdout"])
            self.assertIn('{"api_key" : <REDACTED>', record[field]["stdout"])

    def test_redaction_time_is_bounded_on_homogeneous_capped_input(self):
        """Linear-time redaction: maximally long homogeneous and near-miss
        inputs, each at the cap (and one 10x over it, which the pre-cap
        bounds), are redacted in a child process under its own timeout."""
        code = textwrap.dedent("""\
            import time
            from herdr import runtime as r
            n = r.DIAGNOSTIC_READ_MAX_BYTES
            inputs = [
                "x" * n, "a" * n, "0" * n, "A" * n, ":" * n, "=" * n,
                "-" * n, " " * n, "ab12" * (n // 4), "sk-" * (n // 3),
                "lc-" * (n // 3), "AKIA" * (n // 4), "ghp" * (n // 3),
                "--password " * (n // 11), '"api_key":' * (n // 10),
                "token=" * (n // 6), "x" * (10 * n),
            ]
            worst = 0.0
            for text in inputs:
                started = time.monotonic()
                r._clean(text)
                r._redact_argv([text, text])
                worst = max(worst, time.monotonic() - started)
            print("WORST_SECONDS=%.3f" % worst)
        """)
        env = dict(os.environ)
        env["PYTHONPATH"] = str(Path(__file__).resolve().parent.parent)
        started = time.monotonic()
        result = subprocess.run([sys.executable, "-c", code], env=env,
                                capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr)
        worst = float(result.stdout.strip().split("=")[1])
        self.assertLess(worst, 2.0, result.stdout)
        self.assertLess(time.monotonic() - started, 60)


class OptInTests(unittest.TestCase):
    def test_none_keeps_the_legacy_behaviour_byte_for_byte(self):
        def fake_run(cmd, cwd=None, check=False):
            calls.append(tuple(cmd))
            if cmd[:3] == ["herdr", "agent", "start"]:
                return BUSY_START
            return SimpleNamespace(returncode=0, stdout="OUT", stderr="")

        def no_capture(*args, **kwargs):
            raise AssertionError("capture must not run without diagnostic_dir")

        calls = []
        clock = [0.0]

        def fake_monotonic():
            clock[0] += 0.6
            if clock[0] > 60:
                raise AssertionError("busy loop did not terminate")
            return clock[0]

        with patch.object(runtime_module, "run", side_effect=fake_run), \
                patch.object(runtime_module, "_bounded_command", no_capture), \
                patch.object(runtime_module, "capture_start_failure",
                             no_capture), \
                patch.object(runtime_module.time, "monotonic", fake_monotonic), \
                patch.object(runtime_module.time, "sleep", lambda s: None), \
                patch("builtins.print"):
            with self.assertRaises(RuntimeError) as caught:
                runtime_module.start_agent("a", "p1", ROLE, 60000, 0)
        # The legacy busy block, unchanged: positional process-info.
        self.assertEqual(calls[-2:], [
            ("herdr", "pane", "process-info", "p1"),
            ("herdr", "pane", "read", "p1", "--source", "recent-unwrapped",
             "--lines", "40"),
        ])
        attempts = len(calls) - 2
        self.assertEqual(str(caught.exception), (
            "start a failed after %d attempt(s):\n" % attempts
            + BUSY_START.stderr + "\n"
            + "\nPane diagnostics (p1):\nprocess-info:\nOUT\n"
            + "recent output:\nOUT"))

    def test_none_non_busy_failure_runs_nothing_extra(self):
        calls = []

        def fake_run(cmd, cwd=None, check=False):
            calls.append(tuple(cmd))
            return FAILED_START

        with patch.object(runtime_module, "run", side_effect=fake_run):
            with self.assertRaises(RuntimeError) as caught:
                runtime_module.start_agent("a", "p1", ROLE, 60000)
        self.assertEqual(len(calls), 1)
        self.assertEqual(str(caught.exception),
                         "start a failed after 1 attempt(s):\n"
                         + FAILED_START.stderr + "\n")


class FailureIsolationTests(unittest.TestCase):
    def run_failing(self, diagnostic_dir):
        with patch.object(runtime_module, "run", return_value=FAILED_START):
            with self.assertRaises(RuntimeError) as caught:
                runtime_module.start_agent("a", "p1", ROLE, 60000,
                                           diagnostic_dir=diagnostic_dir)
        return str(caught.exception)

    def test_a_capture_that_raises_still_raises_the_original_error(self):
        def boom(*args, **kwargs):
            raise ValueError("capture exploded")

        with patch.object(runtime_module, "capture_start_failure", boom):
            message = self.run_failing("/nonexistent/never/used")
        self.assertEqual(message, "start a failed after 1 attempt(s):\n"
                         + FAILED_START.stderr + "\n")

    def test_an_unwritable_directory_still_raises_the_original_error(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        blocker = Path(temp.name) / "state"
        blocker.write_text("a file where the directory should be")
        log = []
        with patch.object(runtime_module, "_bounded_command",
                          new=FakeHerdr(log)):
            message = self.run_failing(blocker / "diagnostics")
        self.assertEqual(message, "start a failed after 1 attempt(s):\n"
                         + FAILED_START.stderr + "\n")
        self.assertEqual(len(log), 3)

    def test_keyboard_interrupt_is_not_swallowed(self):
        def interrupt(*args, **kwargs):
            raise KeyboardInterrupt

        with patch.object(runtime_module, "run", return_value=FAILED_START), \
                patch.object(runtime_module, "capture_start_failure",
                             interrupt):
            with self.assertRaises(KeyboardInterrupt):
                runtime_module.start_agent("a", "p1", ROLE, 60000,
                                           diagnostic_dir="/unused")


class BoundsTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.dir = Path(temp.name) / "diagnostics"

    def capture(self, fake):
        with patch.object(runtime_module, "_bounded_command", new=fake):
            return runtime_module.capture_start_failure(
                self.dir, "a", "p1", ROLE, 60000, 1, FAILED_START, False)

    def test_oversized_fields_are_capped_and_marked(self):
        huge = "word " * 40000
        many = {"result": {"processes": [
            {"pid": n, "name": "p", "argv": ["x" * 5000] * 200,
             "cmdline": "y " * 5000, "cwd": "/w"} for n in range(100)]}}
        path = self.capture(FakeHerdr([], pane_text=huge, info=many))
        text = path.read_text()
        self.assertLessEqual(len(text), runtime_module.DIAGNOSTIC_FILE_MAX_CHARS)
        record = json.loads(text)
        visible = record["pane_read_visible"]["stdout"]
        self.assertTrue(visible.endswith(runtime_module.TRUNCATED))
        self.assertLessEqual(len(visible),
                             runtime_module.DIAGNOSTIC_FIELD_MAX_CHARS + 20)
        self.assertLessEqual(len(record["processes"]),
                             runtime_module.DIAGNOSTIC_MAX_PROCESSES)
        self.assertLessEqual(len(record["processes"][0]["argv"]),
                             runtime_module.DIAGNOSTIC_MAX_ARGV)

    def test_the_file_cap_holds_even_when_fields_alone_would_exceed_it(self):
        many = {"result": {"processes": [
            {"pid": n, "name": "p", "argv": ["x" * 900] * 64,
             "cmdline": "y " * 450, "cwd": "/w"} for n in range(32)]}}
        with patch.object(runtime_module, "DIAGNOSTIC_FILE_MAX_CHARS", 20000):
            path = self.capture(FakeHerdr([], pane_text="z " * 9000, info=many))
            self.assertLessEqual(len(path.read_text()), 20000)
        record = json.loads(path.read_text())
        self.assertTrue(record.get("processes_truncated"))

    def test_retention_prunes_to_the_maximum_count(self):
        self.dir.mkdir(mode=0o700, parents=True)
        now = time.time()
        for index in range(25):
            old = self.dir / ("agent-start-failure-old-%02d.json" % index)
            old.write_text("{}")
            os.utime(old, (now - 1000 + index, now - 1000 + index))
        unrelated = self.dir / "keep-me.txt"
        unrelated.write_text("not a diagnostic")
        newest = self.capture(FakeHerdr([]))
        retained = sorted(self.dir.glob("agent-start-failure-*.json"))
        self.assertEqual(len(retained), runtime_module.DIAGNOSTIC_MAX_FILES)
        self.assertIn(newest, retained)
        self.assertNotIn(self.dir / "agent-start-failure-old-00.json", retained)
        self.assertTrue(unrelated.exists())

    def test_undeletable_old_files_block_publication_across_captures(self):
        """Round 22, finding 3: pruning runs BEFORE publishing; when the
        oldest files cannot be deleted, nothing new is published, the count
        never exceeds the cap, and the ORIGINAL start error is raised."""
        self.dir.mkdir(mode=0o700, parents=True)
        cap = runtime_module.DIAGNOSTIC_MAX_FILES
        now = time.time()
        for index in range(cap):
            old = self.dir / ("agent-start-failure-old-%02d.json" % index)
            old.write_text("{}")
            os.utime(old, (now - 1000 + index, now - 1000 + index))
        real_unlink = Path.unlink
        refused = []

        def refuse_old(path, *args, **kwargs):
            if path.name.startswith("agent-start-failure-old-"):
                refused.append(path.name)
                raise PermissionError("cannot delete %s" % path.name)
            return real_unlink(path, *args, **kwargs)

        original = ("start a failed after 1 attempt(s):\n"
                    + FAILED_START.stderr + "\n")
        with patch.object(Path, "unlink", refuse_old), \
                patch.object(runtime_module, "run",
                             return_value=FAILED_START), \
                patch.object(runtime_module, "_bounded_command",
                             new=FakeHerdr([])):
            for attempt in range(3):
                with self.assertRaises(RuntimeError) as caught:
                    runtime_module.start_agent("a", "p1", ROLE, 60000,
                                               diagnostic_dir=self.dir)
                self.assertEqual(str(caught.exception), original)
                self.assertEqual(
                    len(list(self.dir.glob("agent-start-failure-*.json"))),
                    cap)
            with self.assertRaises(OSError):
                runtime_module.capture_start_failure(
                    self.dir, "a", "p1", ROLE, 60000, 1, FAILED_START, False)
        self.assertEqual(len(refused), 4)       # one oldest file per attempt
        self.assertEqual(sorted(p.name for p in self.dir.iterdir()), sorted(
            "agent-start-failure-old-%02d.json" % i for i in range(cap)))


class BoundedCommandTests(unittest.TestCase):
    """Real subprocesses of a self-terminating fake `herdr` (never the real
    one), to prove the time and read bounds on the actual reader."""

    def setUp(self):
        fake_herdr_on_path(self)

    def pidfile(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        path = os.path.join(temp.name, "pid")
        patcher = patch.dict(os.environ, {"FAKE_HERDR_PIDFILE": path})
        patcher.start()
        self.addCleanup(patcher.stop)
        return path

    def assert_gone(self, pid):
        """Independent bound: poll for at most 3 s (the fakes self-exit
        after 20 s regardless)."""
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline:
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                return
            time.sleep(0.05)
        self.fail("process %d survived the diagnostic cleanup" % pid)

    def test_a_descendant_holding_the_pipe_dies_after_the_leader_exits(self):
        """Round 22, finding 2: the leader exits at once while a descendant
        keeps the pipe; on timeout the OWNED group is killed anyway, and the
        group kill comes before any poll or wait on the leader."""
        pidfile = self.pidfile()
        calls = []
        real_killpg = os.killpg
        real_wait = subprocess.Popen.wait
        real_poll = subprocess.Popen.poll

        def killpg(pgid, sig):
            calls.append(("killpg", pgid))
            return real_killpg(pgid, sig)

        def wait(proc, timeout=None):
            calls.append(("wait", proc.pid))
            return real_wait(proc, timeout=timeout)

        def poll(proc):
            calls.append(("poll", proc.pid))
            return real_poll(proc)

        with patch.object(runtime_module.os, "killpg", killpg), \
                patch.object(subprocess.Popen, "wait", wait), \
                patch.object(subprocess.Popen, "poll", poll), \
                patch.object(runtime_module,
                             "DIAGNOSTIC_COMMAND_TIMEOUT_SECONDS", 1.0):
            started = time.monotonic()
            result = runtime_module._bounded_command(
                ["herdr", "pane", "read", "p1", "--source", "descendant"])
            elapsed = time.monotonic() - started
        self.assertTrue(result["timed_out"])
        self.assertLess(elapsed, 5.0)
        with open(pidfile) as handle:
            leader = int(handle.read())
        with open(pidfile + ".child") as handle:
            child = int(handle.read())
        self.assertEqual(calls[0], ("killpg", leader))
        self.assertEqual(calls[1:], [("wait", leader)])
        self.assert_gone(child)

    def test_a_setup_failure_after_creation_still_kills_the_owned_group(self):
        """Round 22, finding 2: selector construction now sits under the
        cleanup, so a failure there still kills the created process."""
        killed = []
        real_killpg = os.killpg

        def killpg(pgid, sig):
            killed.append(pgid)
            return real_killpg(pgid, sig)

        def broken_selector():
            raise RuntimeError("selector construction failed")

        with patch.object(runtime_module.os, "killpg", killpg), \
                patch.object(runtime_module.selectors, "DefaultSelector",
                             broken_selector):
            started = time.monotonic()
            result = runtime_module._bounded_command(
                ["herdr", "pane", "read", "p1", "--source", "detection",
                 "--lines", "80"])          # the fake would sleep 20 s
            elapsed = time.monotonic() - started
        self.assertEqual(result["error"], "RuntimeError")
        self.assertLess(elapsed, 3.0)
        self.assertEqual(len(killed), 1)
        self.assert_gone(killed[0])

    def test_a_hung_command_is_cut_off_by_its_own_timeout(self):
        with patch.object(runtime_module,
                          "DIAGNOSTIC_COMMAND_TIMEOUT_SECONDS", 0.5):
            started = time.monotonic()
            result = runtime_module._bounded_command(
                ["herdr", "pane", "read", "p1", "--source", "detection",
                 "--lines", "80"])
            elapsed = time.monotonic() - started
        self.assertTrue(result["timed_out"])
        self.assertLess(elapsed, 5.0)       # the fake would sleep 20 s

    def test_oversized_output_is_cut_at_the_read_limit(self):
        real_read = os.read
        chunk = runtime_module.DIAGNOSTIC_READ_CHUNK_BYTES
        consumed = []

        def counting_read(fd, size):
            data = real_read(fd, size)
            if size == chunk:
                consumed.append(len(data))
            return data

        with patch.object(runtime_module.os, "read", counting_read):
            started = time.monotonic()
            result = runtime_module._bounded_command(
                ["herdr", "pane", "read", "p1", "--source", "visible",
                 "--lines", "80"])
            elapsed = time.monotonic() - started
        cap = runtime_module.DIAGNOSTIC_READ_MAX_BYTES
        self.assertTrue(result["stdout_truncated_at_read"])
        self.assertFalse(result["timed_out"])
        # The fake writes up to 64 MiB; the reader consumed only about the
        # cap (plus at most one chunk) before stopping and killing it.
        self.assertLessEqual(sum(consumed), cap + chunk)
        self.assertLessEqual(len(result["stdout"]), cap + 20)
        self.assertTrue(result["stdout"].endswith(runtime_module.TRUNCATED))
        self.assertLess(elapsed, 5.0)

    def test_end_to_end_capture_with_a_hanging_read_still_raises_promptly(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        directory = Path(temp.name) / "diagnostics"
        with patch.object(runtime_module, "run", return_value=FAILED_START), \
                patch.object(runtime_module,
                             "DIAGNOSTIC_COMMAND_TIMEOUT_SECONDS", 0.5):
            started = time.monotonic()
            with self.assertRaises(RuntimeError) as caught:
                runtime_module.start_agent("a", "p1", ROLE, 60000,
                                           diagnostic_dir=directory)
            elapsed = time.monotonic() - started
        self.assertLess(elapsed, 8.0)
        self.assertIn("Start-failure diagnostic: ", str(caught.exception))
        record = json.loads(only_file(directory).read_text())
        self.assertTrue(record["pane_read_detection"]["timed_out"])
        self.assertTrue(record["pane_read_visible"]["stdout_truncated_at_read"])
        self.assertEqual(record["processes"][0]["pid"], 4242)
        for secret in SECRETS:
            self.assertNotIn(secret, json.dumps(record))


class LifecycleCleanupTests(unittest.TestCase):
    """The diagnostic exists BEFORE lifecycle issues `workspace close`, and
    survives the cleanup that removes runtime.json."""

    @patch("herdr.lifecycle.agent_info", new=_bound_probe)
    @patch("herdr.lifecycle.prompt")
    @patch("herdr.lifecycle.split")
    @patch("herdr.lifecycle.jrun")
    def run_failing_start(self, mock_jrun, mock_split, mock_prompt,
                          capture_override=None):
        temp, herd = HerdrLifecycleTests.make_instance(self)
        self.addCleanup(temp.cleanup)
        mock_jrun.return_value = {"result": {
            "workspace": {"workspace_id": "ws1"},
            "root_pane": {"pane_id": "pane-root"}}}
        mock_split.side_effect = ["pane-lead", "pane-executor",
                                  "pane-reviewer", "pane-controller"]
        state = herd.herd_root / "state"
        log = []

        def lifecycle_run(cmd, *args, **kwargs):
            log.append(("lifecycle", tuple(cmd),
                        sorted(p.name for p in (state / "diagnostics").glob(
                            "agent-start-failure-*.json"))
                        if (state / "diagnostics").exists() else [],
                        (state / "runtime.json").exists()))
            return SimpleNamespace(returncode=0, stdout="", stderr="")

        def runtime_run(cmd, cwd=None, check=False):
            log.append(("run", tuple(cmd)))
            return FAILED_START

        patches = [
            patch("herdr.lifecycle.run", side_effect=lifecycle_run),
            patch.object(runtime_module, "run", side_effect=runtime_run),
            patch.object(runtime_module, "_bounded_command",
                         new=FakeHerdr(log)),
            patch("builtins.print"),
        ]
        if capture_override is not None:
            patches.append(patch.object(runtime_module,
                                        "capture_start_failure",
                                        capture_override))
        for item in patches:
            item.start()
            self.addCleanup(item.stop)
        with self.assertRaises(RuntimeError) as caught:
            start_herd(herd)
        return herd, state, log, str(caught.exception)

    def test_the_diagnostic_is_written_before_close_and_survives_cleanup(self):
        herd, state, log, message = self.run_failing_start()
        kinds = [entry[0] for entry in log]
        self.assertEqual(kinds, ["run", "diagnostic", "diagnostic",
                                 "diagnostic", "lifecycle"])
        close = log[-1]
        self.assertEqual(close[1], ("herdr", "workspace", "close", "ws1"))
        self.assertEqual(len(close[2]), 1)      # file existed at close time
        self.assertTrue(close[3])               # runtime.json still there
        self.assertFalse((state / "runtime.json").exists())
        survivor = only_file(state / "diagnostics")
        self.assertEqual(survivor.name, close[2][0])
        self.assertIn(str(survivor), message)
        self.assertTrue(message.startswith("start "))

    def test_a_failing_capture_still_runs_cleanup_and_raises_the_original(self):
        def boom(*args, **kwargs):
            raise OSError("disk full")

        herd, state, log, message = self.run_failing_start(
            capture_override=boom)
        self.assertEqual(log[-1][1], ("herdr", "workspace", "close", "ws1"))
        self.assertFalse((state / "runtime.json").exists())
        self.assertNotIn("Start-failure diagnostic", message)
        self.assertIn("agent_start_timeout", message)


if __name__ == "__main__":
    unittest.main(verbosity=1)
