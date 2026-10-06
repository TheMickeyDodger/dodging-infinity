"""The max-quality child Supervisor's Codex launch argv (Task 8, v3 w39).

codex-cli 0.160.0 rejects ``--sandbox workspace-write`` together with
``--approve-for-me`` ("the argument '--sandbox <SANDBOX_MODE>' cannot be
used with '--approve-for-me'"), which is the exact startup failure the
reviewed diagnostic captured in workspace w39. The Supervisor argv now
drops the explicit sandbox pair and keeps ``--approve-for-me``, whose
installed help text says it uses the workspace-write sandbox (help-text
evidence only, not observed runtime behaviour).

No agent is launched and no Mission or spawn is involved. The parser probe
runs ``codex <args> completion zsh``, which on 0.160.0 terminates at
argument parsing or completion generation without starting a session.
That is observed behaviour, not a documented guarantee. It is skipped
when ``codex`` is not on PATH, runs in a temporary cwd under an
independent timeout, and never passes a prompt.
"""

import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from herdr import config as herdr_config
from herdr import runtime as runtime_module
from herdr.initialize import initialize_herd
from herdr.instance import HerdrInstance

import test_initialize  # module import: its test class is not re-collected

REPO_ROOT = Path(__file__).resolve().parent.parent

OLD_SUPERVISOR_ARGS = [
    "-m", "gpt-6-astra", "-c", 'model_reasoning_effort="xhigh"',
    "--sandbox", "workspace-write", "--approve-for-me",
]
NEW_SUPERVISOR_ARGS = [
    "-m", "gpt-6-astra", "-c", 'model_reasoning_effort="xhigh"',
    "--approve-for-me",
]
# The other three max-quality roles, byte-identical to before this fix.
UNCHANGED_ROLES = {
    "lead": {"kind": "claude", "args": [
        "--model", "claude-opus-5", "--effort", "high",
        "--permission-mode", "auto"]},
    "executor": {"kind": "claude", "args": [
        "--model", "claude-opus-5-5", "--effort", "xhigh",
        "--permission-mode", "auto"]},
    "reviewer": {"kind": "codex", "args": [
        "-m", "gpt-6-astra", "-c", 'model_reasoning_effort="xhigh"',
        "-c", 'sandbox_mode="read-only"', "-c", 'approval_policy="never"']},
}
CONFLICT = ("error: the argument '--sandbox <SANDBOX_MODE>' cannot be used "
            "with '--approve-for-me'")
PROBE_TIMEOUT_SECONDS = 30


def agent_start_command(role_cfg):
    """The exact `herdr agent start` argv runtime.start_agent builds, with
    the runner faked (nothing is executed)."""
    sent = []

    def fake_run(cmd, cwd=None, check=False):
        sent.append(list(cmd))
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    with patch.object(runtime_module, "run", side_effect=fake_run):
        runtime_module.start_agent("di-sup", "pane-1", role_cfg, 60000)
    assert len(sent) == 1, sent
    return sent[0]


class SupervisorArgvTests(unittest.TestCase):
    def test_the_generated_child_supervisor_argv_is_exact(self):
        """Through the real initialization path into a temporary repository,
        then the argv runtime.start_agent builds for `herdr agent start`."""
        temp, repo = test_initialize.HerdrInitializeTests.make_repo(self)
        self.addCleanup(temp.cleanup)
        with patch("herdr.registry.REGISTRY", repo / "test-registry.json"):
            initialize_herd(repo, preset="max-quality",
                            test_command="python -m pytest",
                            alias="launch-flags")
        roles = HerdrInstance(repo).load_config()["roles"]
        supervisor = roles["supervisor"]
        self.assertEqual(supervisor, {"kind": "codex",
                                      "args": NEW_SUPERVISOR_ARGS})
        self.assertEqual(agent_start_command(supervisor), [
            "herdr", "agent", "start", "di-sup", "--kind", "codex",
            "--pane", "pane-1", "--timeout", "60000", "--",
            "-m", "gpt-6-astra", "-c", 'model_reasoning_effort="xhigh"',
            "--approve-for-me",
        ])
        args = supervisor["args"]
        for flag in ("--sandbox", "-s"):
            self.assertNotIn(flag, args)
        self.assertFalse([a for a in args if a.startswith(("--sandbox=", "-s"))
                          and a != "-s"])
        self.assertEqual(args.count("--approve-for-me"), 1)
        # Model and effort are unchanged.
        self.assertEqual(args[:4], ["-m", "gpt-6-astra", "-c",
                                    'model_reasoning_effort="xhigh"'])
        for name, expected in UNCHANGED_ROLES.items():
            self.assertEqual(roles[name], expected, name)
            self.assertEqual(agent_start_command(roles[name])[11:],
                             expected["args"], name)

    def test_the_preset_and_the_example_config_agree(self):
        preset = herdr_config.PRESETS["max-quality"]["roles"]
        self.assertEqual(preset["supervisor"]["args"], NEW_SUPERVISOR_ARGS)
        example = json.loads(
            (REPO_ROOT / "herd.config.example.json").read_text())
        self.assertEqual(example["preset"], "max-quality")
        self.assertEqual(example["roles"], preset)

    def test_the_default_roster_supervisor_is_untouched(self):
        self.assertEqual(herdr_config.DEFAULT["roles"]["supervisor"]["args"], [
            "-m", "gpt-6-astra", "-c", 'model_reasoning_effort="xhigh"',
            "--sandbox", "workspace-write", "--ask-for-approval", "on-request",
        ])


@unittest.skipUnless(shutil.which("codex"), "codex is not on PATH")
class InstalledCodexParserTests(unittest.TestCase):
    """Parser-level proof against the INSTALLED CLI (`--help`/`--version`
    can bypass clap's conflict validation, so they do not count)."""

    def probe(self, args):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        result = subprocess.run(
            ["codex"] + args + ["completion", "zsh"],
            cwd=temp.name, stdin=subprocess.DEVNULL, capture_output=True,
            text=True, timeout=PROBE_TIMEOUT_SECONDS)
        return result.returncode, bool(result.stdout.strip()), result.stderr

    def test_the_old_argv_is_rejected_with_the_observed_conflict(self):
        rc, has_stdout, stderr = self.probe(OLD_SUPERVISOR_ARGS)
        self.assertEqual(rc, 2)
        self.assertFalse(has_stdout)
        self.assertEqual(stderr.splitlines()[0], CONFLICT)

    def test_the_new_argv_parses(self):
        rc, has_stdout, stderr = self.probe(
            herdr_config.PRESETS["max-quality"]["roles"]["supervisor"]["args"])
        self.assertEqual(rc, 0, stderr)
        self.assertTrue(has_stdout)


if __name__ == "__main__":
    unittest.main(verbosity=1)
