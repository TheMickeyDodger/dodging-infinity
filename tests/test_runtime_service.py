"""Focused tests for Trusted Host Survival: the DI Runtime LaunchAgent
supervision adapter (telegram_operator.runtime_service), the local
break-glass readiness probes (telegram_operator.host_readiness), the
`tgop runtime-service` CLI adapter, and the preserved Runtime
invariants (single-instance lock, recovery ordering, ownership).

Every test is hermetic: a temp HOME, a temp state directory, an
injected launchctl/tailscale/systemsetup runner, and an injected
`which`. No test reaches the real launchd domain, the real
~/Library/LaunchAgents, the real state directory, or any real host
command. The production label is used as a VALUE only.

Run: PYTHONPATH="$PWD" python tests/test_runtime_service.py
"""

import ast
import contextlib
import io
import json
import os
import plistlib
import stat
import sys
import tempfile
import unittest
from unittest.mock import patch

from telegram_operator import host_readiness
from telegram_operator import runtime_service
from telegram_operator import state

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

SECRET_TOKEN = "987654321:ZZsecretZZ-qwerty-uiop-9f8e7d6c"

# The REAL production job on the engineering Mac (copied verbatim; the
# real file is never read at test time). It DIFFERS from what this
# checkout would install: a different root, and no ExitTimeOut.
PRODUCTION_PLIST = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>com.dodginginfinity.dirun</string>
  <key>ProgramArguments</key>
  <array>
    <string>/usr/bin/python3</string>
    <string>/Users/travissellers/tools/dodging-infinity/dirun.py</string>
    <string>run</string>
  </array>
  <key>EnvironmentVariables</key>
  <dict><key>PATH</key><string>/Users/travissellers/.local/bin:/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin</string></dict>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>ThrottleInterval</key><integer>10</integer>
  <key>StandardOutPath</key><string>/Users/travissellers/Library/Application Support/DodgingInfinity/telegram/dirun.out.log</string>
  <key>StandardErrorPath</key><string>/Users/travissellers/Library/Application Support/DodgingInfinity/telegram/dirun.err.log</string>
</dict>
</plist>
"""

# A real `launchctl list <label>` document, parameterized on the two
# fields the state machine reads.
LIST_TEMPLATE = (
    '{\n'
    '\t"StandardOutPath" = "/Users/x/Library/Application Support/'
    'DodgingInfinity/telegram/dirun.out.log";\n'
    '\t"LimitLoadToSessionType" = "Aqua";\n'
    '\t"StandardErrorPath" = "/Users/x/Library/Application Support/'
    'DodgingInfinity/telegram/dirun.err.log";\n'
    '\t"Label" = "com.dodginginfinity.dirun";\n'
    '\t"OnDemand" = false;\n'
    '\t"LastExitStatus" = %(exit)s;\n'
    '%(pid)s'
    '\t"Program" = "/usr/bin/python3";\n'
    '\t"ProgramArguments" = (\n'
    '\t\t"/usr/bin/python3";\n'
    '\t\t"/Users/x/tools/dodging-infinity/dirun.py";\n'
    '\t\t"run";\n'
    '\t);\n'
    '};\n'
)


def list_output(pid=None, last_exit=0):
    pid_line = '\t"PID" = %d;\n' % pid if pid is not None else ""
    return LIST_TEMPLATE % {"exit": last_exit, "pid": pid_line}


NOT_FOUND = (
    113, "",
    'Could not find service "com.dodginginfinity.dirun" in domain for'
    ' port\n',
)


class ScriptedRunner(object):
    """Hermetic launchctl/tailscale/systemsetup double. Records every
    argv; answers by verb; can raise."""

    def __init__(self, list_response=NOT_FOUND, responses=None,
                 raise_for=()):
        self.calls = []
        self.list_response = list_response
        self.responses = dict(responses or {})
        self.raise_for = tuple(raise_for)

    def __call__(self, argv):
        self.calls.append(list(argv))
        verb = tuple(argv[:2])
        if verb in self.raise_for:
            raise OSError("launchctl unavailable")
        if verb == ("launchctl", "list"):
            return self.list_response
        return self.responses.get(verb, (0, "", ""))

    def calls_with(self, verb):
        return [call for call in self.calls if call[:2] == ["launchctl", verb]]


def fake_which(name):
    return "/fake/tools/bin/%s" % name


def no_which(name):
    return None


class Fixture(unittest.TestCase):
    """Temp HOME, temp stable root, temp config directory."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        # realpath: on macOS the temp directory is reached through the
        # /var -> /private/var symlink and the predicate rejects that.
        self.base = os.path.realpath(self.tmp.name)
        self.home = os.path.join(self.base, "home")
        os.makedirs(self.home, mode=0o700)
        self.root = self.make_root("root")
        self.confdir = os.path.join(self.base, "conf")
        os.makedirs(self.confdir, mode=0o700)
        self.config_path = self.write_config()

    def make_root(self, name):
        root = os.path.join(self.base, name)
        os.makedirs(root, mode=0o755)
        with open(os.path.join(root, "dirun.py"), "w") as handle:
            handle.write("#!/usr/bin/env python3\n")
        os.makedirs(os.path.join(root, ".git"))
        return root

    def write_config(self, payload=None, path=None):
        path = path or os.path.join(self.confdir, "config.json")
        payload = payload or {
            "bot_token": SECRET_TOKEN,
            "allowed_user_ids": [42],
            "repository": self.root,
        }
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(payload, handle)
        os.chmod(path, 0o600)
        return path

    def install(self, runner=None, which=fake_which, root=None,
                config_path="default", **kwargs):
        runner = runner if runner is not None else ScriptedRunner()
        if config_path == "default":
            config_path = self.config_path
        return runtime_service.install_service(
            "/usr/bin/python3", root or self.root, home=self.home,
            runner=runner, which=which, config_path=config_path,
            temp_prefixes=(), **kwargs
        )

    def plist_path(self):
        return runtime_service.service_plist_path(self.home)

    def read_plist(self):
        with open(self.plist_path(), "rb") as handle:
            return plistlib.load(handle)

    def status(self, runner, config_path="default"):
        if config_path == "default":
            config_path = self.config_path
        return runtime_service.service_status(
            home=self.home, runner=runner, config_path=config_path,
        )


# -- 1-5, 26-27: the definition ------------------------------------------

class DefinitionTests(Fixture):
    def build(self, **kwargs):
        kwargs.setdefault("config_path", self.config_path)
        return runtime_service.build_service_plist(
            "/usr/bin/python3", self.root, home=self.home, **kwargs
        )

    def test_stable_label_and_exact_key_set(self):
        plist = self.build(codex_directory="/fake/tools/bin")
        self.assertEqual(
            runtime_service.SERVICE_LABEL, "com.dodginginfinity.dirun"
        )
        self.assertEqual(plist["Label"], runtime_service.SERVICE_LABEL)
        self.assertEqual(
            sorted(plist),
            sorted([
                "Label", "ProgramArguments", "EnvironmentVariables",
                "RunAtLoad", "KeepAlive", "ThrottleInterval",
                "ExitTimeOut", "StandardOutPath", "StandardErrorPath",
            ]),
        )
        self.assertEqual(
            plist["EnvironmentVariables"],
            {"PATH": "/fake/tools/bin:" + runtime_service.SERVICE_BASE_PATH},
        )

    def test_path_binding_is_absolute_with_no_working_directory(self):
        plist = self.build()
        arguments = plist["ProgramArguments"]
        self.assertEqual(arguments[0], "/usr/bin/python3")
        self.assertEqual(arguments[1], os.path.join(self.root, "dirun.py"))
        self.assertEqual(arguments[-1], "run")
        self.assertEqual(arguments[2:4], ["--config", self.config_path])
        for element in arguments[:-1]:
            if element == "--config":
                continue
            self.assertTrue(os.path.isabs(element), element)
        self.assertNotIn("WorkingDirectory", plist)
        self.assertNotIn("UserName", plist)
        self.assertNotIn("GroupName", plist)
        # A relative interpreter / root is normalized, never emitted.
        relative = runtime_service.build_service_plist(
            "python3", "root", home=self.home
        )
        for element in relative["ProgramArguments"][:2]:
            self.assertTrue(os.path.isabs(element), element)
        self.assertEqual(len(relative["ProgramArguments"]), 3)

    def test_startup_and_restart_semantics(self):
        plist = self.build()
        self.assertIs(plist["RunAtLoad"], True)
        self.assertIs(plist["KeepAlive"], True)
        self.assertEqual(plist["ThrottleInterval"], 10)
        self.assertEqual(plist["ExitTimeOut"], 30)
        self.assertEqual(runtime_service.THROTTLE_SECONDS, 10)
        self.assertEqual(runtime_service.EXIT_TIMEOUT_SECONDS, 30)

    def test_logs_live_in_state_dir_outside_git_tracked_paths(self):
        plist = self.build()
        for key in ("StandardOutPath", "StandardErrorPath"):
            self.assertEqual(os.path.dirname(plist[key]), self.confdir)
            self.assertFalse(plist[key].startswith(self.root + os.sep))
            self.assertFalse(plist[key].startswith(REPO_ROOT + os.sep))
        default = runtime_service.build_service_plist(
            "/usr/bin/python3", self.root, home=self.home
        )
        expected = state.default_state_dir(self.home)
        self.assertEqual(
            os.path.dirname(default["StandardOutPath"]), expected
        )
        self.assertEqual(
            os.path.dirname(default["StandardErrorPath"]), expected
        )
        self.assertEqual(
            runtime_service.service_state_dir(self.home), expected
        )

    def test_secret_absent_from_plist_argv_status_and_doctor(self):
        runner = ScriptedRunner(list_response=(0, list_output(pid=4242), ""))
        ok, message = self.install(runner=runner)
        self.assertTrue(ok, message)
        with open(self.plist_path(), "rb") as handle:
            raw = handle.read()
        self.assertNotIn(SECRET_TOKEN.encode(), raw)
        self.assertNotIn(b"bot_token", raw)
        parsed = self.read_plist()
        self.assertNotIn(SECRET_TOKEN, " ".join(parsed["ProgramArguments"]))
        self.assertNotIn(SECRET_TOKEN, parsed["EnvironmentVariables"]["PATH"])
        for call in runner.calls:
            self.assertNotIn(SECRET_TOKEN, " ".join(call))
        report = self.status(runner)
        self.assertNotIn(
            SECRET_TOKEN, runtime_service.render_status_text(report)
        )
        doctor = runtime_service.doctor(
            home=self.home, runner=runner, which=fake_which,
            config_path=self.config_path,
        )
        self.assertNotIn(
            SECRET_TOKEN, runtime_service.render_doctor_text(doctor)
        )
        self.assertNotIn(SECRET_TOKEN, json.dumps(doctor))
        self.assertNotIn(SECRET_TOKEN, message)

    def test_log_paths_carry_no_secret_component(self):
        plist = self.build()
        for key in ("StandardOutPath", "StandardErrorPath"):
            self.assertNotIn(SECRET_TOKEN, plist[key])
            self.assertIn(os.path.basename(plist[key]),
                          ("dirun.out.log", "dirun.err.log"))

    def test_state_dir_is_created_0700_and_open_config_dir_is_refused(self):
        # The state dir IS the validated config's own directory, so a
        # successful install can never reach a missing state dir; the
        # creation mode is exercised on the helper directly, and the
        # guarantee that an existing one carries no group/other bits
        # is load_config's refusal, exercised through install.
        fresh = os.path.join(self.base, "fresh", "state")
        self.assertFalse(os.path.exists(fresh))
        runtime_service._prepare_state_dir(fresh)
        self.assertEqual(stat.S_IMODE(os.stat(fresh).st_mode), 0o700)
        runtime_service._prepare_state_dir(fresh)  # idempotent
        self.assertEqual(stat.S_IMODE(os.stat(fresh).st_mode), 0o700)
        for mode in (0o750, 0o705, 0o770, 0o777):
            os.chmod(self.confdir, mode)
            runner = ScriptedRunner()
            ok, message = self.install(runner=runner)
            self.assertFalse(ok, oct(mode))
            self.assertIn("group/other", message)
            self.assertFalse(os.path.exists(self.plist_path()))
            self.assertEqual(runner.calls, [])
        os.chmod(self.confdir, 0o700)
        ok, message = self.install()
        self.assertTrue(ok, message)

    def test_generated_artifacts_are_never_group_or_other_writable(self):
        ok, message = self.install()
        self.assertTrue(ok, message)
        self.assertEqual(
            stat.S_IMODE(os.stat(self.plist_path()).st_mode), 0o600
        )
        state_dir = runtime_service.service_state_dir(
            self.home, self.config_path
        )
        self.assertEqual(stat.S_IMODE(os.stat(state_dir).st_mode), 0o700)
        default_state = runtime_service.service_state_dir(self.home)
        ok, message = self.install(config_path=None)
        # (install against the default config refuses — no config
        # there — so it must not have created anything either)
        self.assertFalse(ok)
        self.assertFalse(os.path.exists(default_state))
        for directory, _, files in os.walk(self.home):
            for entry in [directory] + [
                os.path.join(directory, name) for name in files
            ]:
                mode = os.stat(entry).st_mode
                self.assertEqual(
                    mode & (stat.S_IWGRP | stat.S_IWOTH), 0, entry
                )


# -- 28: the stable-code-path predicate ----------------------------------

class UnstableRootTests(Fixture):
    def test_engineering_worktree_under_private_tmp_is_refused(self):
        # This very worktree: /private/tmp/... AND a .git FILE. Both
        # rules refuse it independently.
        reason = runtime_service.unstable_root_reason(
            "/private/tmp/some-engineering-worktree"
        )
        self.assertIsNotNone(reason)
        if REPO_ROOT.startswith("/private/tmp/"):
            self.assertIsNotNone(
                runtime_service.unstable_root_reason(REPO_ROOT)
            )
        # A root that exists under a temp prefix is refused by the
        # prefix rule alone (every other rule passes).
        self.assertIsNone(
            runtime_service.unstable_root_reason(self.root, temp_prefixes=())
        )
        reason = runtime_service.unstable_root_reason(
            self.root, temp_prefixes=(self.base,)
        )
        self.assertIn("temporary prefix", reason)

    def test_git_file_root_is_refused(self):
        worktree = self.make_root("worktree")
        os.rmdir(os.path.join(worktree, ".git"))
        with open(os.path.join(worktree, ".git"), "w") as handle:
            handle.write("gitdir: /elsewhere/.git/worktrees/x\n")
        reason = runtime_service.unstable_root_reason(
            worktree, temp_prefixes=()
        )
        self.assertIsNotNone(reason)
        self.assertIn("worktree", reason)

    def test_symlinked_root_is_refused(self):
        link = os.path.join(self.base, "link")
        os.symlink(self.root, link)
        reason = runtime_service.unstable_root_reason(link, temp_prefixes=())
        self.assertIsNotNone(reason)
        self.assertIn("symlink", reason)

    def test_missing_entry_relative_and_world_writable_are_refused(self):
        self.assertIn(
            "not absolute",
            runtime_service.unstable_root_reason("relative/root"),
        )
        self.assertIn(
            "does not exist",
            runtime_service.unstable_root_reason(
                os.path.join(self.base, "nope"), temp_prefixes=()
            ),
        )
        bare = os.path.join(self.base, "bare")
        os.makedirs(bare)
        self.assertIn(
            "dirun.py",
            runtime_service.unstable_root_reason(bare, temp_prefixes=()),
        )
        os.chmod(self.root, 0o777)
        self.assertIn(
            "world-writable",
            runtime_service.unstable_root_reason(self.root, temp_prefixes=()),
        )
        os.chmod(self.root, 0o755)

    def test_stable_fixture_root_is_accepted(self):
        self.assertIsNone(
            runtime_service.unstable_root_reason(self.root, temp_prefixes=())
        )
        self.assertEqual(runtime_service.DEFAULT_TEMP_PREFIXES, (
            "/tmp", "/private/tmp", "/var/tmp", "/private/var/tmp",
            "/var/folders", "/private/var/folders",
        ))

    def test_install_refuses_unstable_root_before_writing(self):
        runner = ScriptedRunner()
        ok, message = runtime_service.install_service(
            "/usr/bin/python3", self.root, home=self.home, runner=runner,
            which=fake_which, config_path=self.config_path,
        )  # production prefixes: the temp fixture is refused
        self.assertFalse(ok)
        self.assertIn("refusing to install", message)
        self.assertIn("Nothing was written", message)
        self.assertFalse(os.path.exists(self.plist_path()))
        self.assertEqual(runner.calls, [])


# -- 6-11: install transitions and uninstall -----------------------------

class InstallTests(Fixture):
    def test_malformed_config_refused_before_writing(self):
        runner = ScriptedRunner()
        bad = os.path.join(self.confdir, "bad.json")
        with open(bad, "w") as handle:
            handle.write("{not json")
        os.chmod(bad, 0o600)
        ok, message = self.install(runner=runner, config_path=bad)
        self.assertFalse(ok)
        self.assertIn("config", message)
        self.assertFalse(os.path.exists(self.plist_path()))
        self.assertEqual(runner.calls, [])
        # An unsafe-permission config is refused the same way.
        os.chmod(self.config_path, 0o644)
        ok, message = self.install(runner=runner)
        self.assertFalse(ok)
        self.assertIn("group/other", message)
        self.assertFalse(os.path.exists(self.plist_path()))
        self.assertEqual(runner.calls, [])

    def test_unresolvable_codex_refused_before_writing(self):
        runner = ScriptedRunner()
        ok, message = self.install(runner=runner, which=no_which)
        self.assertFalse(ok)
        self.assertIn("Nothing was installed", message)
        self.assertFalse(os.path.exists(self.plist_path()))
        self.assertEqual(runner.calls, [])

    def test_initial_install_writes_0600_plist_and_bootstraps(self):
        runner = ScriptedRunner()
        ok, message = self.install(runner=runner)
        self.assertTrue(ok, message)
        self.assertIn("installed and bootstrapped", message)
        path = self.plist_path()
        self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o600)
        parsed = self.read_plist()
        self.assertEqual(parsed, runtime_service.build_service_plist(
            "/usr/bin/python3", self.root, home=self.home,
            config_path=self.config_path, codex_directory="/fake/tools/bin",
        ))
        uid = os.getuid()
        # Absent -> installed: no pre-observation is needed; exactly one
        # bootstrap of the owned plist into the user's own domain.
        self.assertEqual(runner.calls, [
            ["launchctl", "bootstrap", "gui/%d" % uid, path],
        ])

    def test_install_reports_bootstrap_failure(self):
        runner = ScriptedRunner(responses={
            ("launchctl", "bootstrap"): (5, "", "Bootstrap failed: 5"),
        })
        ok, message = self.install(runner=runner)
        self.assertFalse(ok)
        self.assertIn("exited 5", message)
        self.assertTrue(os.path.exists(self.plist_path()))

    def test_repeated_install_same_configuration_is_stable(self):
        first = ScriptedRunner()
        ok, _ = self.install(runner=first)
        self.assertTrue(ok)
        with open(self.plist_path(), "rb") as handle:
            first_bytes = handle.read()
        before = os.stat(self.plist_path())
        # Second install: the job is now bootstrapped.
        second = ScriptedRunner(list_response=(0, list_output(pid=4242), ""))
        ok, message = self.install(runner=second)
        self.assertTrue(ok, message)
        self.assertIn("unchanged", message)
        with open(self.plist_path(), "rb") as handle:
            self.assertEqual(handle.read(), first_bytes)
        self.assertEqual(os.stat(self.plist_path()).st_mtime_ns,
                         before.st_mtime_ns)
        # No second bootstrap, no second label, one log tree.
        self.assertEqual(second.calls, [
            ["launchctl", "list", "com.dodginginfinity.dirun"],
        ])
        agents = os.listdir(os.path.dirname(self.plist_path()))
        self.assertEqual(agents, ["com.dodginginfinity.dirun.plist"])
        state_dirs = [
            directory for directory, _, _ in os.walk(self.base)
            if os.path.basename(directory) == "conf"
        ]
        self.assertEqual(state_dirs, [self.confdir])
        # Unchanged but NOT bootstrapped (after a stop): bootstrapped.
        third = ScriptedRunner()
        ok, message = self.install(runner=third)
        self.assertTrue(ok, message)
        self.assertIn("unchanged", message)
        self.assertEqual(len(third.calls_with("bootstrap")), 1)

    def write_production_plist(self):
        os.makedirs(os.path.dirname(self.plist_path()), mode=0o755)
        with open(self.plist_path(), "w", encoding="utf-8") as handle:
            handle.write(PRODUCTION_PLIST)
        os.chmod(self.plist_path(), 0o600)
        return PRODUCTION_PLIST.encode("utf-8")

    def test_differing_existing_plist_is_refused_unchanged(self):
        original = self.write_production_plist()
        runner = ScriptedRunner()
        ok, message = self.install(runner=runner)
        self.assertFalse(ok)
        self.assertIn("DIFFERS", message)
        self.assertIn("--reconcile", message)
        for key in ("ProgramArguments", "ExitTimeOut", "EnvironmentVariables"):
            self.assertIn(key, message)
        with open(self.plist_path(), "rb") as handle:
            self.assertEqual(handle.read(), original)
        self.assertEqual(runner.calls, [])

    def test_unparseable_existing_plist_is_refused_unchanged(self):
        os.makedirs(os.path.dirname(self.plist_path()), mode=0o755)
        with open(self.plist_path(), "wb") as handle:
            handle.write(b"<plist><dict><key>Label</key")
        runner = ScriptedRunner()
        ok, message = self.install(runner=runner)
        self.assertFalse(ok)
        self.assertIn("does not parse", message)
        self.assertIn("--reconcile", message)
        with open(self.plist_path(), "rb") as handle:
            self.assertEqual(handle.read(), b"<plist><dict><key>Label</key")
        self.assertEqual(runner.calls, [])

    def test_non_dict_plist_root_is_refused_then_reconciled(self):
        os.makedirs(os.path.dirname(self.plist_path()), mode=0o755)
        for root in (["a", "b"], "just a string", 7, [{"Label": "x"}]):
            with open(self.plist_path(), "wb") as handle:
                plistlib.dump(root, handle)
            with open(self.plist_path(), "rb") as handle:
                original = handle.read()
            runner = ScriptedRunner()
            ok, message = self.install(runner=runner)
            self.assertFalse(ok, root)
            self.assertIn("does not parse", message)
            self.assertIn("not a dictionary", message)
            self.assertIn("--reconcile", message)
            with open(self.plist_path(), "rb") as handle:
                self.assertEqual(handle.read(), original)
            self.assertEqual(runner.calls, [])
            runner = ScriptedRunner()
            ok, message = self.install(runner=runner, reconcile=True)
            self.assertTrue(ok, (root, message))
            self.assertIn("replaced an unparseable definition", message)
            self.assertIn("not a dictionary", message)
            self.assertEqual(self.read_plist()["Label"],
                             runtime_service.SERVICE_LABEL)
            self.assertEqual(len(runner.calls_with("bootstrap")), 1)

    def test_reconcile_replaces_and_says_what_it_replaced(self):
        self.write_production_plist()
        runner = ScriptedRunner(list_response=(0, list_output(pid=759), ""))
        ok, message = self.install(runner=runner, reconcile=True)
        self.assertTrue(ok, message)
        self.assertIn("reconciled", message)
        self.assertIn("replaced a differing definition", message)
        self.assertIn("ExitTimeOut", message)
        parsed = self.read_plist()
        self.assertEqual(parsed["ExitTimeOut"], 30)
        self.assertEqual(
            parsed["ProgramArguments"][1], os.path.join(self.root, "dirun.py")
        )
        uid = os.getuid()
        target = "gui/%d/com.dodginginfinity.dirun" % uid
        self.assertEqual(runner.calls, [
            ["launchctl", "list", "com.dodginginfinity.dirun"],
            ["launchctl", "bootout", target],
            ["launchctl", "bootstrap", "gui/%d" % uid, self.plist_path()],
        ])
        # Reconciling an unparseable file says so too.
        with open(self.plist_path(), "wb") as handle:
            handle.write(b"garbage")
        runner = ScriptedRunner()
        ok, message = self.install(runner=runner, reconcile=True)
        self.assertTrue(ok, message)
        self.assertIn("replaced an unparseable definition", message)

    def test_uninstall_absent_is_idempotent_success(self):
        runner = ScriptedRunner()
        ok, message = runtime_service.uninstall_service(
            home=self.home, runner=runner
        )
        self.assertTrue(ok)
        self.assertIn("nothing installed", message)
        self.assertEqual(runner.calls, [])

    def test_uninstall_present_boots_out_and_removes_only_the_plist(self):
        self.install()
        state_dir = runtime_service.service_state_dir(
            self.home, self.config_path
        )
        out_log, err_log = runtime_service.service_log_paths(
            self.home, self.config_path
        )
        lock_path = os.path.join(state_dir, state.RUNTIME_LOCK_FILE_NAME)
        state_file = os.path.join(state_dir, state.STATE_FILE_NAME)
        workspace = os.path.join(state_dir, "workspaces", "w1")
        os.makedirs(workspace)
        for path in (out_log, err_log, lock_path, state_file,
                     os.path.join(workspace, "file.txt")):
            with open(path, "w") as handle:
                handle.write("keep\n")
        runner = ScriptedRunner()
        ok, message = runtime_service.uninstall_service(
            home=self.home, runner=runner
        )
        self.assertTrue(ok, message)
        self.assertFalse(os.path.exists(self.plist_path()))
        uid = os.getuid()
        self.assertEqual(runner.calls, [
            ["launchctl", "bootout", "gui/%d/com.dodginginfinity.dirun" % uid],
        ])
        # Owned-path containment: everything else survives.
        for path in (out_log, err_log, lock_path, state_file,
                     os.path.join(workspace, "file.txt"), self.config_path):
            self.assertTrue(os.path.exists(path), path)
        self.assertTrue(os.path.isdir(state_dir))
        # And a second uninstall is the idempotent absent case.
        ok, message = runtime_service.uninstall_service(
            home=self.home, runner=ScriptedRunner()
        )
        self.assertTrue(ok)
        self.assertIn("nothing installed", message)
        # Present but not bootstrapped: the "not loaded" refusal is
        # tolerated and the plist is still removed.
        self.install()
        runner = ScriptedRunner(responses={
            ("launchctl", "bootout"): (3, "", "Boot-out failed: 3: No such process"),
        })
        ok, message = runtime_service.uninstall_service(
            home=self.home, runner=runner
        )
        self.assertTrue(ok, message)
        self.assertIn("not bootstrapped", message)
        self.assertFalse(os.path.exists(self.plist_path()))

    def test_uninstall_keeps_plist_when_bootout_fails(self):
        self.install()
        for response in (
            (1, "", "nope"),
            # Exit 3 with an UNRELATED message is a failure, not
            # "not loaded": fail closed, keep the plist.
            (3, "", "Boot-out failed: 3: Operation not permitted"),
            (113, "", "some other refusal"),
        ):
            runner = ScriptedRunner(
                responses={("launchctl", "bootout"): response},
            )
            ok, message = runtime_service.uninstall_service(
                home=self.home, runner=runner
            )
            self.assertFalse(ok, response)
            self.assertIn("left in place", message)
            self.assertTrue(os.path.exists(self.plist_path()))
            ok, message = runtime_service.stop_service(
                home=self.home, runner=runner
            )
            self.assertFalse(ok, response)
        # Exit 3 with NO text at all is tolerated as not loaded.
        runner = ScriptedRunner(
            responses={("launchctl", "bootout"): (3, "", "")},
        )
        ok, message = runtime_service.uninstall_service(
            home=self.home, runner=runner
        )
        self.assertTrue(ok, message)
        self.assertFalse(os.path.exists(self.plist_path()))

    def test_uninstall_removes_a_foreign_plist_at_the_owned_path_only(self):
        # Intended: uninstall cleans up whatever sits at the owned label
        # path (for example a job written by scripts/dirun-agent.sh),
        # and touches nothing else in the directory.
        agents = os.path.dirname(self.plist_path())
        os.makedirs(agents, mode=0o755)
        with open(self.plist_path(), "w", encoding="utf-8") as handle:
            handle.write(PRODUCTION_PLIST)
        sibling = os.path.join(agents, "com.example.other.plist")
        with open(sibling, "wb") as handle:
            plistlib.dump({"Label": "com.example.other"}, handle)
        runner = ScriptedRunner()
        ok, message = runtime_service.uninstall_service(
            home=self.home, runner=runner
        )
        self.assertTrue(ok, message)
        self.assertFalse(os.path.exists(self.plist_path()))
        self.assertTrue(os.path.exists(sibling))
        self.assertEqual(sorted(os.listdir(agents)),
                         ["com.example.other.plist"])
        self.assertEqual(runner.calls, [
            ["launchctl", "bootout",
             "gui/%d/com.dodginginfinity.dirun" % os.getuid()],
        ])


# -- the launchctl list parser -------------------------------------------

class LaunchctlListParserTests(unittest.TestCase):
    def test_parses_real_output_scalars(self):
        parsed = runtime_service.parse_launchctl_list(
            list_output(pid=759, last_exit=0)
        )
        self.assertEqual(parsed["Label"], "com.dodginginfinity.dirun")
        self.assertEqual(parsed["PID"], 759)
        self.assertEqual(parsed["LastExitStatus"], 0)
        self.assertEqual(parsed["Program"], "/usr/bin/python3")
        self.assertIs(parsed["OnDemand"], False)
        self.assertNotIn("ProgramArguments", parsed)
        absent = runtime_service.parse_launchctl_list(list_output())
        self.assertNotIn("PID", absent)

    def test_garbage_does_not_parse(self):
        for text in ("", "Could not find service", "{\n\tnonsense\n};\n",
                     '{\n\t"Label" = ;\n};\n', "{\n", None):
            self.assertIsNone(runtime_service.parse_launchctl_list(text), text)

    def test_wait_status_is_decoded_to_an_exit_code(self):
        self.assertEqual(runtime_service._decode_exit_status(768), 3)
        self.assertEqual(runtime_service._decode_exit_status(3), 3)
        self.assertEqual(runtime_service._decode_exit_status(0), 0)
        self.assertIsNone(runtime_service._decode_exit_status(None))


# -- 12-18: the status state machine ---------------------------------------

class StatusTests(Fixture):
    def hold_lock(self):
        import fcntl
        state_dir = runtime_service.service_state_dir(
            self.home, self.config_path
        )
        os.makedirs(state_dir, mode=0o700, exist_ok=True)
        descriptor = os.open(
            os.path.join(state_dir, state.RUNTIME_LOCK_FILE_NAME),
            os.O_RDWR | os.O_CREAT, 0o600,
        )
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        self.addCleanup(os.close, descriptor)

    def test_not_installed(self):
        runner = ScriptedRunner()
        report = self.status(runner)
        self.assertEqual(report["state"], "not_installed")
        self.assertFalse(report["installed"])
        self.assertIsNone(report["pid"])
        self.assertEqual(runner.calls, [])
        text = runtime_service.render_status_text(report)
        self.assertIn("state=not_installed", text)

    def test_installed_stopped_not_bootstrapped_and_clean_exit(self):
        self.install()
        report = self.status(ScriptedRunner())
        self.assertEqual(report["state"], "installed_stopped")
        self.assertIn("not bootstrapped", report["detail"])
        self.assertEqual(report["code_path"], os.path.join(self.root, "dirun.py"))
        self.assertEqual(report["program"], "/usr/bin/python3")
        self.assertEqual(report["log_paths"], list(
            runtime_service.service_log_paths(self.home, self.config_path)
        ))
        self.assertFalse(report["runtime_lock_held"])
        report = self.status(ScriptedRunner(
            list_response=(0, list_output(pid=None, last_exit=0), "")
        ))
        self.assertEqual(report["state"], "installed_stopped")
        self.assertIsNone(report["pid"])

    def test_running_without_lock(self):
        self.install()
        report = self.status(ScriptedRunner(
            list_response=(0, list_output(pid=4242), "")
        ))
        self.assertEqual(report["state"], "running")
        self.assertEqual(report["pid"], 4242)
        self.assertFalse(report["runtime_lock_held"])

    def test_ready_with_pid_and_lock_held(self):
        self.install()
        self.hold_lock()
        report = self.status(ScriptedRunner(
            list_response=(0, list_output(pid=4242), "")
        ))
        self.assertEqual(report["state"], "ready")
        self.assertEqual(report["pid"], 4242)
        self.assertTrue(report["runtime_lock_held"])
        text = runtime_service.render_status_text(report)
        self.assertIn("pid: 4242", text)
        self.assertIn("runtime lock: held", text)
        self.assertIn(os.path.join(self.root, "dirun.py"), text)

    def test_degraded_lock_contention_names_the_manual_runtime(self):
        self.install()
        for raw in (3, 768):
            report = self.status(ScriptedRunner(
                list_response=(0, list_output(pid=None, last_exit=raw), "")
            ))
            self.assertEqual(report["state"], "degraded", raw)
            self.assertEqual(report["last_exit_status"], raw)
            self.assertEqual(report["last_exit_code"], 3)
            self.assertIn("lock", report["detail"])
            self.assertIn("dirun run", report["detail"])
        # The wait-status form (768) is unambiguous lock contention;
        # a bare raw 3 names BOTH readings (exit 3 or SIGQUIT).
        unambiguous = self.status(ScriptedRunner(
            list_response=(0, list_output(pid=None, last_exit=768), "")
        ))["detail"]
        self.assertIn("lock contention", unambiguous)
        self.assertNotIn("signal", unambiguous)
        ambiguous = self.status(ScriptedRunner(
            list_response=(0, list_output(pid=None, last_exit=3), "")
        ))["detail"]
        self.assertIn("either", ambiguous)
        self.assertIn("lock-contention exit code", ambiguous)
        self.assertIn("signal-3 (SIGQUIT)", ambiguous)
        self.assertIn("raw status 3", ambiguous)
        # Any other non-zero exit is degraded too, pointing at the log.
        report = self.status(ScriptedRunner(
            list_response=(0, list_output(pid=None, last_exit=512), "")
        ))
        self.assertEqual(report["state"], "degraded")
        self.assertIn(report["log_paths"][1], report["detail"])

    def test_degraded_when_lock_held_without_our_pid(self):
        self.install()
        self.hold_lock()
        report = self.status(ScriptedRunner(
            list_response=(0, list_output(pid=None, last_exit=0), "")
        ))
        self.assertEqual(report["state"], "degraded")
        self.assertIn("OUTSIDE this service", report["detail"])

    def test_unobservable_runner_raises_unexpected_exit_garbage(self):
        self.install()
        raising = ScriptedRunner(raise_for=(("launchctl", "list"),))
        report = self.status(raising)
        self.assertEqual(report["state"], "unobservable")
        self.assertIn("could not be run", report["detail"])
        weird = ScriptedRunner(list_response=(1, "", "boom"))
        report = self.status(weird)
        self.assertEqual(report["state"], "unobservable")
        self.assertIn("exited 1", report["detail"])
        garbage = ScriptedRunner(list_response=(0, "<<not a dict>>", ""))
        report = self.status(garbage)
        self.assertEqual(report["state"], "unobservable")
        self.assertIn("did not parse", report["detail"])
        # Still reports what IS observable: the bound code path.
        self.assertEqual(report["code_path"], os.path.join(self.root, "dirun.py"))

    def test_foreign_plist_under_our_path(self):
        os.makedirs(os.path.dirname(self.plist_path()), mode=0o755)
        foreign = {
            "Label": "com.example.other",
            "ProgramArguments": ["/usr/bin/true"],
        }
        with open(self.plist_path(), "wb") as handle:
            plistlib.dump(foreign, handle)
        runner = ScriptedRunner()
        report = self.status(runner)
        self.assertEqual(report["state"], "foreign")
        self.assertTrue(report["installed"])
        self.assertEqual(runner.calls, [])
        with open(self.plist_path(), "wb") as handle:
            handle.write(b"not a plist")
        report = self.status(runner)
        self.assertEqual(report["state"], "foreign")
        self.assertIn("does not parse", report["detail"])
        # A plist that PARSES but whose root is not a dictionary (an
        # array, a string, an integer, an array of dicts) is the same
        # ambiguous class: foreign, reported, never a traceback.
        for root in (["a", "b"], "just a string", 7, [{"Label": "x"}]):
            with open(self.plist_path(), "wb") as handle:
                plistlib.dump(root, handle)
            runner = ScriptedRunner()
            report = self.status(runner)
            self.assertEqual(report["state"], "foreign", root)
            self.assertIn("not a dictionary", report["detail"])
            self.assertEqual(runner.calls, [])
            text = runtime_service.render_status_text(report)
            self.assertIn("state=foreign", text)
            doctor = runtime_service.doctor(
                home=self.home, runner=runner, which=no_which,
                config_path=self.config_path,
            )
            self.assertEqual(doctor["state"], "foreign")
            runtime_service.render_doctor_text(doctor)
            # doctor issued only the read-only host probes, no launchctl
            # verb against the job.
            self.assertEqual(runner.calls_with("list"), [])
            self.assertEqual(
                [call for call in runner.calls if call[0] == "launchctl"],
                [["launchctl", "print-disabled", "system"]],
            )
        # Also without assuming a dict at the foreign-detail site itself.
        self.assertFalse(runtime_service._owned_definition(["x"]))
        self.assertFalse(runtime_service._owned_definition("x"))
        # The real production job IS ours (a dirun job under our label):
        # status reports its bound code path rather than calling it foreign.
        with open(self.plist_path(), "w", encoding="utf-8") as handle:
            handle.write(PRODUCTION_PLIST)
        report = self.status(ScriptedRunner())
        self.assertEqual(report["state"], "installed_stopped")
        self.assertEqual(
            report["code_path"],
            "/Users/travissellers/tools/dodging-infinity/dirun.py",
        )


# -- 20, 30: lifecycle verbs and their targets -------------------------------

class LifecycleTests(Fixture):
    def target(self):
        return "gui/%d/com.dodginginfinity.dirun" % os.getuid()

    def test_restart_issues_exactly_one_kickstart_k(self):
        runner = ScriptedRunner()
        ok, message = runtime_service.restart_service(
            home=self.home, runner=runner
        )
        self.assertTrue(ok, message)
        self.assertEqual(runner.calls, [
            ["launchctl", "kickstart", "-k", self.target()],
        ])
        failing = ScriptedRunner(responses={
            ("launchctl", "kickstart"): (3, "", "No such process"),
        })
        ok, message = runtime_service.restart_service(
            home=self.home, runner=failing
        )
        self.assertFalse(ok)
        self.assertIn("exited 3", message)

    def test_stop_is_exactly_one_bootout_and_never_disables(self):
        runner = ScriptedRunner()
        ok, message = runtime_service.stop_service(
            home=self.home, runner=runner
        )
        self.assertTrue(ok, message)
        self.assertEqual(runner.calls, [
            ["launchctl", "bootout", self.target()],
        ])
        self.assertIn("SIGTERM", message)
        self.assertIn("30 s", message)
        self.assertIn(runtime_service.STOPPED_UNINSTALL_NOTE, message)
        # Not loaded: tolerated, still exactly one argv.
        idle = ScriptedRunner(responses={
            ("launchctl", "bootout"): (3, "", "Boot-out failed: 3: No such process"),
        })
        ok, message = runtime_service.stop_service(
            home=self.home, runner=idle
        )
        self.assertTrue(ok)
        self.assertIn("nothing to stop", message)
        self.assertEqual(idle.calls, [["launchctl", "bootout", self.target()]])
        # Any other failure is a failure, and launchctl unavailable too.
        failing = ScriptedRunner(responses={
            ("launchctl", "bootout"): (1, "", "Boot-out failed: 1: Operation not permitted"),
        })
        ok, message = runtime_service.stop_service(
            home=self.home, runner=failing
        )
        self.assertFalse(ok)
        broken = ScriptedRunner(raise_for=(("launchctl", "bootout"),))
        ok, message = runtime_service.stop_service(
            home=self.home, runner=broken
        )
        self.assertFalse(ok)
        # "disable" appears in NO argv issued by any lifecycle verb.
        self.install()
        for list_response in (NOT_FOUND, (0, list_output(pid=4242), "")):
            sweep = ScriptedRunner(list_response=list_response)
            runtime_service.stop_service(home=self.home, runner=sweep)
            runtime_service.restart_service(home=self.home, runner=sweep)
            self.install(runner=sweep)
            self.install(runner=sweep, reconcile=True)
            runtime_service.uninstall_service(home=self.home, runner=sweep)
            self.install(runner=sweep)
            self.assertTrue(sweep.calls)
            for call in sweep.calls:
                self.assertNotIn("disable", call)
                self.assertNotIn("disable", " ".join(call))

    def test_stopped_transience_is_reported_in_status_and_doctor(self):
        self.install()
        for list_response in (NOT_FOUND,
                              (0, list_output(pid=None, last_exit=0), "")):
            runner = ScriptedRunner(list_response=list_response)
            report = self.status(runner)
            self.assertEqual(report["state"], "installed_stopped")
            status_text = runtime_service.render_status_text(report)
            doctor_text = runtime_service.render_doctor_text(
                runtime_service.doctor(
                    home=self.home, runner=runner, which=no_which,
                    config_path=self.config_path,
                )
            )
            for text in (status_text, doctor_text):
                self.assertIn(runtime_service.STOPPED_PLIST_INSTALLED_NOTE, text)
                self.assertIn(runtime_service.STOPPED_RELAUNCH_NOTE, text)
                self.assertIn(runtime_service.STOPPED_UNINSTALL_NOTE, text)

    def test_start_bootstraps_or_kickstarts_the_owned_target(self):
        runner = ScriptedRunner()
        ok, message = runtime_service.start_service(
            home=self.home, runner=runner
        )
        self.assertFalse(ok)
        self.assertIn("not installed", message)
        self.assertEqual(runner.calls, [])
        self.install()
        runner = ScriptedRunner()
        ok, message = runtime_service.start_service(
            home=self.home, runner=runner
        )
        self.assertTrue(ok, message)
        self.assertEqual(runner.calls, [
            ["launchctl", "list", "com.dodginginfinity.dirun"],
            ["launchctl", "enable", self.target()],
            ["launchctl", "bootstrap", "gui/%d" % os.getuid(),
             self.plist_path()],
        ])
        loaded = ScriptedRunner(list_response=(0, list_output(), ""))
        ok, message = runtime_service.start_service(
            home=self.home, runner=loaded
        )
        self.assertTrue(ok, message)
        self.assertEqual(loaded.calls[-1],
                         ["launchctl", "kickstart", self.target()])
        self.assertNotIn("enable", message)
        # A failed `enable` is never silent: reported on success of the
        # next verb, and included when the next verb fails too.
        enable_fails = ScriptedRunner(responses={
            ("launchctl", "enable"): (1, "", "Could not enable"),
        })
        ok, message = runtime_service.start_service(
            home=self.home, runner=enable_fails
        )
        self.assertTrue(ok, message)
        self.assertIn("launchctl enable", message)
        self.assertIn("exited 1", message)
        self.assertIn("Could not enable", message)
        both_fail = ScriptedRunner(responses={
            ("launchctl", "enable"): (1, "", "Could not enable"),
            ("launchctl", "bootstrap"): (5, "", "Bootstrap failed: 5"),
        })
        ok, message = runtime_service.start_service(
            home=self.home, runner=both_fail
        )
        self.assertFalse(ok)
        self.assertIn("bootstrap", message)
        self.assertIn("exited 5", message)
        self.assertIn("launchctl enable", message)
        self.assertIn("exited 1", message)

    def test_every_issued_launchctl_argv_is_fully_qualified_to_the_owned_target(self):
        uid = os.getuid()
        domain = "gui/%d" % uid
        target = "%s/com.dodginginfinity.dirun" % domain
        runners = []

        def run_all(list_response):
            runner = ScriptedRunner(list_response=list_response)
            runners.append(runner)
            self.install(runner=runner)
            self.install(runner=runner, reconcile=True)
            runtime_service.start_service(home=self.home, runner=runner)
            runtime_service.stop_service(home=self.home, runner=runner)
            runtime_service.restart_service(home=self.home, runner=runner)
            self.status(runner)
            runtime_service.doctor(
                home=self.home, runner=runner, which=fake_which,
                config_path=self.config_path,
            )
            runtime_service.uninstall_service(home=self.home, runner=runner)

        run_all(NOT_FOUND)
        run_all((0, list_output(pid=4242), ""))
        issued = [call for runner in runners for call in runner.calls
                  if call[0] == "launchctl"]
        self.assertTrue(issued)
        for call in issued:
            verb = call[1]
            if verb == "list":
                self.assertEqual(call, ["launchctl", "list",
                                        "com.dodginginfinity.dirun"])
            elif verb == "bootstrap":
                self.assertEqual(call, ["launchctl", "bootstrap", domain,
                                        self.plist_path()])
            elif verb in ("bootout", "enable"):
                self.assertEqual(call, ["launchctl", verb, target])
            elif verb == "kickstart":
                self.assertEqual(call[-1], target)
                self.assertIn(call, (
                    ["launchctl", "kickstart", target],
                    ["launchctl", "kickstart", "-k", target],
                ))
            elif verb == "print-disabled":
                # The read-only SSH probe (host_readiness allowlist).
                self.assertEqual(call, ["launchctl", "print-disabled",
                                        "system"])
            else:
                self.fail("unexpected launchctl verb: %r" % call)
            joined = " ".join(call)
            self.assertNotIn("telegram-operator", joined)
            self.assertNotIn("user/", joined)
            self.assertNotIn("gui/0/", joined)
            for forbidden in ("load", "unload", "disable", "kill",
                              "remove", "submit"):
                self.assertNotIn(forbidden, call[1:2])


# -- 19-22: preserved Runtime invariants ----------------------------------

class RuntimeInvariantTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = os.path.realpath(self.tmp.name)

    def runtime_cli_source(self):
        with open(os.path.join(REPO_ROOT, "target_runtime", "cli.py")) as handle:
            return handle.read()

    def test_single_instance_lock_is_still_load_bearing(self):
        from target_runtime import cli
        confdir = os.path.join(self.base, "conf")
        os.makedirs(confdir, mode=0o700)
        repo = os.path.join(self.base, "repo")
        os.makedirs(repo)
        config_path = os.path.join(confdir, "config.json")
        with open(config_path, "w") as handle:
            json.dump({
                "bot_token": "123:abc", "allowed_user_ids": [42],
                "repository": repo,
            }, handle)
        os.chmod(config_path, 0o600)
        first = cli.acquire_runtime_lock(confdir)
        self.assertIsNotNone(first)
        self.addCleanup(os.close, first)
        # A second acquire on the SAME file fails: real flock.
        self.assertIsNone(cli.acquire_runtime_lock(confdir))
        # The adapter's non-destructive probe sees it as held.
        held, _ = runtime_service.runtime_status(confdir)
        self.assertTrue(held)
        stream = io.StringIO()
        with contextlib.redirect_stderr(stream):
            code = cli.main(["--config", config_path, "once"])
        self.assertEqual(cli.EXIT_LOCKED, 3)
        self.assertEqual(code, cli.EXIT_LOCKED)
        self.assertIn("refusing to run twice", stream.getvalue())
        self.assertEqual(
            runtime_service.RUNTIME_LOCKED_EXIT_CODE, cli.EXIT_LOCKED
        )

    def _main_function(self):
        tree = ast.parse(self.runtime_cli_source())
        for node in tree.body:
            if isinstance(node, ast.FunctionDef) and node.name == "main":
                return node
        self.fail("target_runtime.cli.main not found")

    def test_lock_descriptor_released_in_main_finally(self):
        main = self._main_function()
        finally_calls = []
        for node in ast.walk(main):
            if isinstance(node, ast.Try) and node.finalbody:
                for statement in node.finalbody:
                    finally_calls.append(ast.dump(statement))
        self.assertTrue(any(
            "os" in dump and "close" in dump and "lock_descriptor" in dump
            for dump in finally_calls
        ), finally_calls)

    def test_recovery_ordering_lock_recover_readiness_process_once(self):
        main = self._main_function()

        def first_line(predicate):
            for node in ast.walk(main):
                if predicate(node):
                    return node.lineno
            return None

        def is_call(node, attr):
            return (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == attr
            )

        lock_line = first_line(
            lambda node: isinstance(node, ast.Call)
            and getattr(node.func, "id", None) == "acquire_runtime_lock"
        )
        refusal_line = first_line(
            lambda node: isinstance(node, ast.Return)
            and isinstance(node.value, ast.Name)
            and node.value.id == "EXIT_LOCKED"
        )
        recover_line = first_line(
            lambda node: is_call(node, "recover_inherited_processes")
        )
        readiness_line = first_line(
            lambda node: is_call(node, "readiness_attention")
        )
        process_line = first_line(lambda node: is_call(node, "process_once"))
        for name, line in (("lock", lock_line), ("refusal", refusal_line),
                           ("recover", recover_line),
                           ("readiness", readiness_line),
                           ("process_once", process_line)):
            self.assertIsNotNone(line, name)
        self.assertLess(lock_line, refusal_line)
        self.assertLess(refusal_line, recover_line)
        self.assertLess(recover_line, readiness_line)
        self.assertLess(readiness_line, process_line)
        # recover/readiness sit in main's TOP-LEVEL body (not inside the
        # try that wraps process_once, nor inside any branch).
        top_level_lines = set()
        for statement in main.body:
            for node in ast.walk(statement):
                if isinstance(node, ast.Try):
                    break
            else:
                top_level_lines.update(
                    range(statement.lineno, statement.end_lineno + 1)
                )
        self.assertIn(recover_line, top_level_lines)
        self.assertIn(readiness_line, top_level_lines)

    def test_ownership_semantics_unchanged(self):
        # RuntimeWorker is constructed in exactly one place: the
        # Broker constructor. The Runtime CLI never constructs one and
        # never passes worker=; the service layer never imports the
        # Runtime at all.
        construction_sites = []
        for name in sorted(os.listdir(os.path.join(REPO_ROOT, "target_runtime"))):
            if not name.endswith(".py"):
                continue
            path = os.path.join(REPO_ROOT, "target_runtime", name)
            with open(path) as handle:
                tree = ast.parse(handle.read())
            for node in ast.walk(tree):
                if isinstance(node, ast.Call) and (
                    getattr(node.func, "id", None) == "RuntimeWorker"
                ):
                    construction_sites.append((name, node.lineno))
        self.assertEqual(len(construction_sites), 1, construction_sites)
        self.assertEqual(construction_sites[0][0], "broker.py")
        with open(os.path.join(REPO_ROOT, "target_runtime", "broker.py")) as handle:
            broker_tree = ast.parse(handle.read())
        enclosing = None
        for node in ast.walk(broker_tree):
            if isinstance(node, ast.ClassDef) and node.name == "TargetBroker":
                for item in node.body:
                    if isinstance(item, ast.FunctionDef) and item.name == "__init__":
                        if any(
                            isinstance(sub, ast.Call)
                            and getattr(sub.func, "id", None) == "RuntimeWorker"
                            for sub in ast.walk(item)
                        ):
                            enclosing = "TargetBroker.__init__"
        self.assertEqual(enclosing, "TargetBroker.__init__")
        cli_source = self.runtime_cli_source()
        self.assertNotIn("RuntimeWorker(", cli_source)
        self.assertNotIn("worker=", cli_source)
        self.assertIn("workspace_close_fn=workspace_ownership.production_close",
                      cli_source)
        self.assertTrue(os.path.isfile(
            os.path.join(REPO_ROOT, "target_runtime", "process_ownership.py")
        ))
        for module in ("runtime_service", "host_readiness"):
            path = os.path.join(REPO_ROOT, "telegram_operator", module + ".py")
            with open(path) as handle:
                tree = ast.parse(handle.read())
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    names = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom):
                    names = [node.module or ""]
                else:
                    continue
                for name in names:
                    self.assertNotEqual(name.split(".")[0], "target_runtime",
                                        (module, name))


# -- 23-24, 29: host readiness --------------------------------------------

TAILSCALE_HEALTHY = json.dumps({
    "BackendState": "Running",
    "Self": {"Online": True, "sshHostKeys": ["ssh-ed25519 AAAA"],
             "HostName": "mac"},
})
TAILSCALE_NEEDS_LOGIN = json.dumps({
    "BackendState": "NeedsLogin", "Self": {"Online": False},
})


class HostReadinessTests(unittest.TestCase):
    def runner_for(self, responses, raise_for=()):
        calls = []

        def run(argv):
            calls.append(list(argv))
            if tuple(argv[:1]) in raise_for or tuple(argv) in raise_for:
                raise OSError("no such binary")
            return responses[tuple(argv)]

        run.calls = calls
        return run

    def test_read_only_argv_allowlist_has_no_mutating_verb(self):
        mutating = ("up", "down", "login", "logout", "set", "switch",
                    "serve", "funnel", "cert", "file", "bootstrap",
                    "bootout", "kickstart", "enable", "disable", "load",
                    "unload", "-setremotelogin")
        allowlist = host_readiness.READ_ONLY_ARGV_ALLOWLIST
        self.assertEqual(allowlist, (
            ("tailscale", "status", "--json"),
            ("systemsetup", "-getremotelogin"),
            ("launchctl", "print-disabled", "system"),
        ))
        for argv in allowlist:
            for element in argv:
                self.assertNotIn(element.lower(), mutating, argv)
        # And the module can issue NOTHING outside the allowlist: a
        # real exception (a ValueError subclass), not a strippable assert.
        self.assertTrue(issubclass(host_readiness.DisallowedArgvError,
                                   ValueError))
        for argv in (("tailscale", "up"), ("launchctl", "bootout", "x"),
                     ("systemsetup", "-setremotelogin", "on")):
            calls = []
            with self.assertRaises(host_readiness.DisallowedArgvError):
                host_readiness._run_allowlisted(
                    lambda argv: calls.append(argv) or (0, "", ""), argv
                )
            self.assertEqual(calls, [])
        with open(host_readiness.__file__) as handle:
            module_source = handle.read()
        self.assertNotIn("assert tuple(argv)", module_source)

    def test_tailscale_missing(self):
        run = self.runner_for({})
        report = host_readiness.tailscale_readiness(runner=run, which=no_which)
        self.assertEqual(report["state"], "missing")
        self.assertEqual(report["tailscale_ssh"], "unobservable")
        self.assertEqual(run.calls, [])
        self.assertIn("ACLs", report["policy"])

    def test_tailscale_disconnected(self):
        run = self.runner_for({
            ("tailscale", "status", "--json"): (0, TAILSCALE_NEEDS_LOGIN, ""),
        })
        report = host_readiness.tailscale_readiness(runner=run, which=fake_which)
        self.assertEqual(report["state"], "disconnected")
        self.assertEqual(report["backend_state"], "NeedsLogin")
        self.assertIs(report["online"], False)
        self.assertEqual(report["tailscale_ssh"], "unobservable")
        # Daemon unreachable: non-zero exit, no document.
        run = self.runner_for({
            ("tailscale", "status", "--json"): (
                1, "", "failed to connect to local Tailscale service"
            ),
        })
        report = host_readiness.tailscale_readiness(runner=run, which=fake_which)
        self.assertEqual(report["state"], "disconnected")
        self.assertIn("daemon unreachable", report["detail"])

    def test_tailscale_healthy_fixture(self):
        run = self.runner_for({
            ("tailscale", "status", "--json"): (0, TAILSCALE_HEALTHY, ""),
        })
        report = host_readiness.tailscale_readiness(runner=run, which=fake_which)
        self.assertEqual(report["state"], "connected")
        self.assertEqual(report["backend_state"], "Running")
        self.assertIs(report["online"], True)
        self.assertEqual(report["tailscale_ssh"], "enabled")
        self.assertEqual(run.calls, [["tailscale", "status", "--json"]])
        # Connected but no sshHostKeys: NOT inferred.
        run = self.runner_for({
            ("tailscale", "status", "--json"): (
                0, json.dumps({"BackendState": "Running",
                               "Self": {"Online": True}}), ""
            ),
        })
        report = host_readiness.tailscale_readiness(runner=run, which=fake_which)
        self.assertEqual(report["state"], "connected")
        self.assertEqual(report["tailscale_ssh"], "unobservable")

    def test_tailscale_unobservable(self):
        run = self.runner_for({
            ("tailscale", "status", "--json"): (0, "not json", ""),
        })
        report = host_readiness.tailscale_readiness(runner=run, which=fake_which)
        self.assertEqual(report["state"], "unobservable")
        run = self.runner_for({}, raise_for=(("tailscale",),))
        report = host_readiness.tailscale_readiness(runner=run, which=fake_which)
        self.assertEqual(report["state"], "unobservable")

    def test_ssh_unavailable(self):
        run = self.runner_for({
            ("systemsetup", "-getremotelogin"): (0, "Remote Login: Off\n", ""),
        })
        report = host_readiness.ssh_readiness(runner=run)
        self.assertEqual(report["state"], "off")
        self.assertEqual(len(report["probes"]), 1)
        self.assertEqual(run.calls, [["systemsetup", "-getremotelogin"]])
        run = self.runner_for({
            ("systemsetup", "-getremotelogin"): (0, "Remote Login: On\n", ""),
        })
        self.assertEqual(host_readiness.ssh_readiness(runner=run)["state"], "on")

    def test_ssh_unobservable_with_reason_never_off(self):
        run = self.runner_for({
            ("systemsetup", "-getremotelogin"): (
                1, "", "You need administrator access to run this tool..."
                " exiting!\n"
            ),
            ("launchctl", "print-disabled", "system"): (0, "{\n}\n", ""),
        })
        report = host_readiness.ssh_readiness(runner=run)
        self.assertEqual(report["state"], "unobservable")
        self.assertIn("administrator", report["detail"])
        self.assertIn("does not list", report["detail"])
        self.assertEqual(len(report["probes"]), 2)
        self.assertEqual(report["probes"][0]["result"], "unobservable")
        # The secondary probe can still answer.
        run = self.runner_for({
            ("systemsetup", "-getremotelogin"): (
                1, "", "You need administrator access\n"
            ),
            ("launchctl", "print-disabled", "system"): (
                0, 'disabled services = {\n\t"com.openssh.sshd" => disabled\n}\n', ""
            ),
        })
        report = host_readiness.ssh_readiness(runner=run)
        self.assertEqual(report["state"], "off")
        self.assertIn("primary probe", report["detail"])
        run = self.runner_for({
            ("systemsetup", "-getremotelogin"): (
                1, "", "You need administrator access\n"
            ),
            ("launchctl", "print-disabled", "system"): (
                0, '"com.openssh.sshd" => enabled\n', ""
            ),
        })
        self.assertEqual(host_readiness.ssh_readiness(runner=run)["state"], "on")
        # Both probes failing to run: unobservable with both reasons.
        run = self.runner_for({}, raise_for=(("systemsetup",), ("launchctl",)))
        report = host_readiness.ssh_readiness(runner=run)
        self.assertEqual(report["state"], "unobservable")
        self.assertIn("could not be run", report["detail"])


# -- 25: doctor ------------------------------------------------------------

class DoctorTests(Fixture):
    def test_doctor_text_names_paths_label_and_both_limitations(self):
        self.install()
        runner = ScriptedRunner(list_response=(0, list_output(pid=4242), ""))
        report = runtime_service.doctor(
            home=self.home, runner=runner, which=no_which,
            config_path=self.config_path,
        )
        text = runtime_service.render_doctor_text(report)
        self.assertIn("com.dodginginfinity.dirun", text)
        self.assertIn(os.path.join(self.root, "dirun.py"), text)
        out_log, err_log = runtime_service.service_log_paths(
            self.home, self.config_path
        )
        self.assertIn(out_log, text)
        self.assertIn(err_log, text)
        self.assertIn("state=running", text)
        self.assertIn("tailscale: state=missing", text)
        self.assertIn("ssh: state=", text)
        self.assertIn("after a reboot it starts once that user logs in", text)
        self.assertIn("NOT verified locally", text)
        self.assertEqual(report["limitations"], [
            runtime_service.GUI_LOGIN_LIMITATION,
            host_readiness.TAILNET_POLICY_STATEMENT,
        ])
        self.assertEqual(report["tailscale"]["state"], "missing")
        self.assertIn(report["ssh"]["state"], ("on", "off", "unobservable"))
        # Deterministic.
        again = runtime_service.doctor(
            home=self.home, runner=runner, which=no_which,
            config_path=self.config_path,
        )
        self.assertEqual(
            runtime_service.render_doctor_text(again), text
        )


# -- the CLI adapter -------------------------------------------------------

class CliTests(Fixture):
    def run_cli(self, argv):
        from telegram_operator import cli
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main(argv)
        return code, out.getvalue(), err.getvalue()

    def test_default_root_from_this_worktree_is_refused_and_writes_nothing(self):
        from telegram_operator import cli
        # HOME is a temp directory; a hermetic runner would record any
        # launchctl call. The default --root is THIS checkout, which
        # the predicate refuses (temp path and/or .git file), so
        # nothing is written and nothing is issued.
        runner = ScriptedRunner()
        with patch.dict(os.environ, {"HOME": self.home}), patch.object(
            cli.runtime_service, "_default_runner", runner
        ), patch.object(cli.runtime_service.shutil, "which", fake_which):
            code, _, err = self.run_cli(
                ["--config", self.config_path, "runtime-service", "install"]
            )
        self.assertEqual(code, cli.EXIT_AGENT)
        self.assertIn("refusing to install", err)
        self.assertIn(REPO_ROOT, err)
        self.assertFalse(os.path.exists(self.plist_path()))
        self.assertFalse(os.path.exists(
            os.path.join(self.home, "Library", "LaunchAgents")
        ))
        self.assertEqual(runner.calls, [])

    def test_install_validates_config_first_exit_2(self):
        from telegram_operator import cli
        missing = os.path.join(self.confdir, "missing.json")
        with patch.object(
            cli.runtime_service, "install_service"
        ) as installed:
            code, _, err = self.run_cli(
                ["--config", missing, "runtime-service", "install",
                 "--root", self.root]
            )
        self.assertEqual(code, cli.EXIT_CONFIG)
        self.assertIn("tgop: config:", err)
        installed.assert_not_called()

    def test_install_delegates_with_root_config_and_reconcile(self):
        from telegram_operator import cli
        with patch.object(
            cli.runtime_service, "install_service",
            return_value=(True, "installed and bootstrapped x"),
        ) as installed:
            code, _, err = self.run_cli(
                ["--config", self.config_path, "runtime-service", "install",
                 "--root", self.root, "--reconcile"]
            )
        self.assertEqual(code, 0)
        self.assertIn("installed and bootstrapped", err)
        args, kwargs = installed.call_args
        self.assertEqual(args[0], sys.executable)
        self.assertEqual(args[1], self.root)
        self.assertEqual(kwargs["config_path"], self.config_path)
        self.assertIs(kwargs["reconcile"], True)
        self.assertNotIn("temp_prefixes", kwargs)

    def test_lifecycle_verbs_delegate_and_map_exit_codes(self):
        from telegram_operator import cli
        for action, name in (("uninstall", "uninstall_service"),
                             ("start", "start_service"),
                             ("stop", "stop_service"),
                             ("restart", "restart_service")):
            with patch.object(
                cli.runtime_service, name, return_value=(True, "did %s" % action)
            ) as fn:
                code, _, err = self.run_cli(["runtime-service", action])
            self.assertEqual(code, 0, action)
            self.assertIn("did %s" % action, err)
            fn.assert_called_once_with()
            with patch.object(
                cli.runtime_service, name, return_value=(False, "failed")
            ):
                code, _, err = self.run_cli(["runtime-service", action])
            self.assertEqual(code, cli.EXIT_AGENT, action)

    def test_status_and_doctor_print_text_exit_0(self):
        from telegram_operator import cli
        self.install()
        runner = ScriptedRunner(list_response=(0, list_output(pid=4242), ""))
        with patch.dict(os.environ, {"HOME": self.home}), patch.object(
            cli.runtime_service, "_default_runner", runner
        ), patch.object(
            cli.runtime_service.host_readiness, "_default_runner", runner
        ), patch.object(cli.runtime_service.host_readiness.shutil, "which",
                        no_which):
            code, out, _ = self.run_cli(
                ["--config", self.config_path, "runtime-service", "status"]
            )
            self.assertEqual(code, 0)
            self.assertIn("state=running", out)
            self.assertIn(os.path.join(self.root, "dirun.py"), out)
            code, out, _ = self.run_cli(
                ["--config", self.config_path, "runtime-service", "doctor"]
            )
        self.assertEqual(code, 0)
        self.assertIn("limitations:", out)
        self.assertIn("tailscale: state=missing", out)
        self.assertNotIn(SECRET_TOKEN, out)
        # Observation only: every issued argv is in the read-only set.
        for call in runner.calls:
            self.assertIn(call[0], ("launchctl", "systemsetup"))
            if call[0] == "launchctl":
                self.assertIn(call[1], ("list", "print-disabled"))
            else:
                self.assertEqual(call, ["systemsetup", "-getremotelogin"])

    def test_unknown_action_is_usage_error(self):
        code, _, err = self.run_cli(["runtime-service", "explode"])
        self.assertEqual(code, 2)
        self.assertIn("invalid choice", err)


if __name__ == "__main__":
    unittest.main()
