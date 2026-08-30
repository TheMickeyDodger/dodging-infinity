import contextlib
import io
import json
import os
import shutil
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import herdctl
from herdr.instance import HerdrInstance
from herdr.lifecycle import start_herd


CATEGORIES = [
    "Herd config",
    "Herdr server",
    "Runtime state",
    "Agents",
    "Task state",
]

FULL_ROLES = {
    "supervisor": {"kind": "claude"},
    "lead1": {"kind": "claude"},
    "executor1": {"kind": "claude"},
    "reviewer1": {"kind": "claude"},
}

FULL_CONFIG = {
    "roles": FULL_ROLES,
    "orchestration": {"leads": 1, "pods": 1},
}

FULL_AGENTS = {
    "supervisor": "h-demo-sup",
    "lead1": "h-demo-lead1",
    "executor1": "h-demo-exec1",
    "reviewer1": "h-demo-rev1",
}


def proc(returncode=0, stdout="", stderr=""):
    return SimpleNamespace(
        returncode=returncode,
        stdout=stdout,
        stderr=stderr,
    )


def healthy_doubles():
    """Patch values under which every health check passes."""
    return {
        "resolve_repo_ref": Mock(
            return_value=Path("/tmp/demo"),
        ),
        "cfg": Mock(
            return_value=json.loads(json.dumps(FULL_CONFIG)),
        ),
        "state": Mock(
            return_value={"agents": dict(FULL_AGENTS)},
        ),
        "load_task": Mock(
            return_value={"status": "IDLE"},
        ),
        "agent_info": Mock(
            return_value={"status": "idle", "raw": {}},
        ),
        "run": Mock(
            return_value=proc(0),
        ),
    }


class RowInvariantMixin:
    def assert_one_row_per_category(self, out):
        """Every category appears exactly once — the B2 invariant."""
        lines = out.splitlines()
        for cat in CATEGORIES:
            rows = [line for line in lines if line.startswith(f"{cat} ")]
            self.assertEqual(
                len(rows),
                1,
                f"expected exactly one `{cat}` row, got {rows!r} in:\n{out}",
            )


class HerdctlHealthTests(RowInvariantMixin, unittest.TestCase):
    def invoke(self, overrides=None, which="/usr/bin/herdr", repo=None):
        """Run herdctl.health hermetically; return (exit_code, output).

        exit_code is 0 when health returns without raising SystemExit.
        Any exception other than SystemExit propagates and fails the
        test — that is the no-traceback assertion.
        """
        doubles = healthy_doubles()
        doubles.update(overrides or {})
        out = io.StringIO()
        code = 0

        with contextlib.ExitStack() as stack:
            for name, value in doubles.items():
                stack.enter_context(
                    patch.object(herdctl, name, value),
                )
            stack.enter_context(
                patch.object(
                    herdctl.shutil,
                    "which",
                    Mock(return_value=which),
                ),
            )
            stack.enter_context(
                patch("sys.stdout", out),
            )
            try:
                herdctl.health(SimpleNamespace(repo=repo))
            except SystemExit as exc:
                code = exc.code if exc.code is not None else 0

        self.doubles = doubles
        return code, out.getvalue()

    # a. Healthy repo => exit 0.
    def test_healthy_repo_exits_zero(self):
        code, out = self.invoke()

        self.assertEqual(code, 0)
        self.assertIn("Health: READY", out)
        self.assertNotIn("FAIL", out)
        self.assert_one_row_per_category(out)

    # b. Uninitialized repo => nonzero + actionable message, no traceback.
    def test_uninitialized_repo_fails_actionably(self):
        code, out = self.invoke(
            overrides={
                "cfg": Mock(
                    side_effect=SystemExit(
                        "/tmp/demo is not initialized. Run `herdctl init` there first.",
                    ),
                ),
                "state": Mock(
                    side_effect=SystemExit(
                        "No runtime state for /tmp/demo. Run `herdctl bootstrap` first.",
                    ),
                ),
            },
        )

        self.assertNotEqual(code, 0)
        self.assertIn("not initialized", out)
        self.assertIn("herdctl init", out)
        self.assertIn("Health: NOT READY", out)
        self.assert_one_row_per_category(out)

    # c. Malformed config / malformed runtime state => nonzero + actionable.
    def test_malformed_config_and_runtime_fail_actionably(self):
        code, out = self.invoke(
            overrides={
                "cfg": Mock(
                    side_effect=ValueError("Expecting value"),
                ),
                "state": Mock(
                    side_effect=ValueError("Expecting value"),
                ),
            },
        )

        self.assertNotEqual(code, 0)
        self.assertIn("herd.config.json is not valid JSON", out)
        self.assertIn("runtime.json is not valid JSON", out)
        self.assertIn("remedy:", out)

    def test_malformed_runtime_alone_fails(self):
        code, out = self.invoke(
            overrides={
                "state": Mock(
                    side_effect=ValueError("Expecting value"),
                ),
            },
        )

        self.assertNotEqual(code, 0)
        self.assertIn("runtime.json is not valid JSON", out)
        self.assertIn("Herd config    OK", out)

    # d. Unbootstrapped repo => nonzero + actionable message.
    def test_unbootstrapped_repo_fails_actionably(self):
        code, out = self.invoke(
            overrides={
                "state": Mock(
                    side_effect=SystemExit(
                        "No runtime state for /tmp/demo. Run `herdctl bootstrap` first.",
                    ),
                ),
            },
        )

        self.assertNotEqual(code, 0)
        self.assertIn("No runtime state", out)
        self.assertIn("herdctl bootstrap", out)
        # S1: the Agents row itself must be FAIL, not merely annotated.
        self.assertRegex(
            out,
            r"(?m)^Agents\s+FAIL\s+not checked: runtime state unavailable",
        )

    # S3: with the config unavailable, the Agents row stays OK (liveness
    # ran and passed) but must name the skipped completeness sub-check.
    def test_agents_note_names_skipped_check_when_config_unavailable(self):
        code, out = self.invoke(
            overrides={
                "cfg": Mock(
                    side_effect=SystemExit(
                        "/tmp/demo is not initialized. Run `herdctl init` there first.",
                    ),
                ),
            },
        )

        self.assertNotEqual(code, 0)
        self.assertIn("Health: NOT READY", out)
        self.assertRegex(
            out,
            r"(?m)^Agents\s+OK\s+.*"
            r"\(expected roles not verified: config unavailable\)",
        )

    # e. Herdr binary absent / server unreachable => nonzero, no traceback.
    def test_missing_herdr_binary_fails_without_subprocess(self):
        code, out = self.invoke(which=None)

        self.assertNotEqual(code, 0)
        self.assertIn("`herdr` binary not found on PATH", out)
        self.doubles["run"].assert_not_called()

    def test_unreachable_herdr_server_fails(self):
        code, out = self.invoke(
            overrides={
                "run": Mock(return_value=proc(7)),
            },
        )

        self.assertNotEqual(code, 0)
        self.assertIn("`herdr agent list` exited 7", out)
        # S2: the Agents row itself must be FAIL, not merely annotated.
        self.assertRegex(
            out,
            r"(?m)^Agents\s+FAIL\s+not checked: Herdr server unreachable",
        )
        self.assert_one_row_per_category(out)

    def test_herdr_exec_oserror_fails_without_traceback(self):
        code, out = self.invoke(
            overrides={
                "run": Mock(
                    side_effect=OSError("exec format error"),
                ),
            },
        )

        self.assertNotEqual(code, 0)
        self.assertIn("could not execute `herdr`", out)

    # f. Blocked agent is state information, not a health failure.
    def test_blocked_agent_still_healthy(self):
        code, out = self.invoke(
            overrides={
                "agent_info": Mock(
                    return_value={"status": "blocked", "raw": {}},
                ),
            },
        )

        self.assertEqual(code, 0)
        self.assertIn("executor1=blocked", out)
        self.assertIn("Health: READY", out)

    # g. missing / unknown agents are infrastructure failures.
    def test_missing_agent_fails(self):
        code, out = self.invoke(
            overrides={
                "agent_info": Mock(
                    return_value={"status": "missing", "raw": None},
                ),
            },
        )

        self.assertNotEqual(code, 0)
        self.assertIn("executor1=missing", out)
        self.assertIn("herdctl bootstrap --force", out)

    def test_unknown_agent_fails(self):
        code, out = self.invoke(
            overrides={
                "agent_info": Mock(
                    return_value={"status": "unknown", "raw": None},
                ),
            },
        )

        self.assertNotEqual(code, 0)
        self.assertIn("executor1=unknown", out)

    # h. Malformed task.json (the load_task sentinel) => nonzero.
    def test_unreadable_task_state_fails(self):
        code, out = self.invoke(
            overrides={
                "load_task": Mock(
                    return_value={
                        "status": "ERROR",
                        "error": "unreadable task state",
                    },
                ),
            },
        )

        self.assertNotEqual(code, 0)
        self.assertIn("task.json is not valid JSON", out)

    def test_persisted_error_task_outcome_is_state_not_failure(self):
        code, out = self.invoke(
            overrides={
                "load_task": Mock(
                    return_value={
                        "status": "ERROR",
                        "error": "supervisor prompt failed",
                    },
                ),
            },
        )

        self.assertEqual(code, 0)
        self.assertIn("Task state", out)
        self.assertIn("ERROR", out)

    # --repo pointing at a bad target => actionable message, no traceback,
    # and (N1) the overall verdict line is printed like every other path.
    def test_bad_repo_ref_fails_actionably(self):
        code, out = self.invoke(
            overrides={
                "resolve_repo_ref": Mock(
                    side_effect=SystemExit(
                        "Unknown repo `nope`. Run `herdctl repos`.",
                    ),
                ),
            },
            repo="nope",
        )

        self.assertNotEqual(code, 0)
        self.assertIn("Unknown repo `nope`", out)
        self.assertIn("remedy:", out)
        self.assertIn("Health: NOT READY", out)

    # Probe bound — the number of live-agent probes (one `herdr agent
    # get` subprocess each) is a hard constant, asserted by counting
    # actual agent_info invocations, not by inspecting output alone.
    def test_probe_invocations_are_hard_capped(self):
        agents = {f"ghost{i}": f"h-ghost-{i}" for i in range(100_000)}
        agents.update(FULL_AGENTS)

        code, out = self.invoke(
            overrides={
                "state": Mock(return_value={"agents": agents}),
            },
        )

        self.assertLessEqual(
            self.doubles["agent_info"].call_count,
            herdctl._HEALTH_MAX_AGENT_PROBES,
        )
        # A truncated run fails closed: unprobed entries are unverified,
        # so they can never contribute to a READY verdict.
        self.assertNotEqual(code, 0)
        self.assertIn("Health: NOT READY", out)
        self.assertNotIn("Health: READY", out)
        self.assertIn("not probed", out)
        self.assertIn(str(herdctl._HEALTH_MAX_AGENT_PROBES), out)

    # Probe ordering — expected roles are probed FIRST, so a required
    # agent that is dead is still detected even when extras alone would
    # exhaust the cap ("aaa*" keys all sort before "supervisor").
    def test_dead_required_agent_detected_despite_cap(self):
        agents = dict(FULL_AGENTS)
        for i in range(herdctl._HEALTH_MAX_AGENT_PROBES + 50):
            agents[f"aaa{i}"] = f"h-a-{i}"

        def info(agent):
            if agent == "h-demo-sup":
                return {"status": "missing", "raw": None}
            return {"status": "idle", "raw": {}}

        code, out = self.invoke(
            overrides={
                "state": Mock(return_value={"agents": agents}),
                "agent_info": Mock(side_effect=info),
            },
        )

        self.assertNotEqual(code, 0)
        self.assertIn("supervisor (h-demo-sup) is missing", out)
        self.assertLessEqual(
            self.doubles["agent_info"].call_count,
            herdctl._HEALTH_MAX_AGENT_PROBES,
        )

    # An expected role absent from the runtime map is detected by set
    # difference, not probing, so the cap must not mask it either.
    def test_absent_required_role_detected_beyond_cap(self):
        agents = {
            f"obs{i}": f"h-obs-{i}"
            for i in range(herdctl._HEALTH_MAX_AGENT_PROBES + 10)
        }
        agents.update(FULL_AGENTS)
        del agents["reviewer1"]

        code, out = self.invoke(
            overrides={
                "state": Mock(return_value={"agents": agents}),
            },
        )

        self.assertNotEqual(code, 0)
        self.assertIn(
            "expected role(s) absent from runtime state: reviewer1",
            out,
        )

    # The corrupt-supervisor repair-first remedy must come from the raw
    # runtime map, NOT from the probed slice: with config unavailable
    # (no expected-first reordering) and an over-cap map whose `aaa*`
    # keys all sort before "supervisor", the supervisor entry is never
    # probed — and the remedy must still switch, because bootstrap
    # reads the map directly and tracebacks regardless of what health
    # probed. Kills the mutant that recomputes supervisor_name from a
    # probed-preview slice (round-2 review, mutant N9).
    def test_unprobed_corrupt_supervisor_still_routes_repair_remedy(self):
        agents = {f"aaa{i}": f"h-a-{i}" for i in range(612)}
        agents["supervisor"] = [1]

        code, out = self.invoke(
            overrides={
                "cfg": Mock(
                    side_effect=SystemExit(
                        "/tmp/demo is not initialized. Run `herdctl init` there first.",
                    ),
                ),
                "state": Mock(return_value={"agents": agents}),
            },
        )

        self.assertNotEqual(code, 0)
        self.assertIn("Health: NOT READY", out)
        self.assertIn("move the file aside", out)
        self.assertNotIn("--force", out)
        # Supervisor genuinely went unprobed: every probe hit an `aaa*`
        # value, the probe loop never flagged the supervisor entry, and
        # the truncation count matches the 101 entries past the cap.
        probed = [
            call.args[0]
            for call in self.doubles["agent_info"].call_args_list
        ]
        self.assertEqual(
            len(probed),
            herdctl._HEALTH_MAX_AGENT_PROBES,
        )
        self.assertTrue(
            all(
                isinstance(name, str) and name.startswith("h-a-")
                for name in probed
            ),
        )
        self.assertNotIn("supervisor has a non-string agent name", out)
        self.assertNotIn("supervisor=invalid", out)
        self.assertIn("101 agent(s) not probed", out)

    # Boundary — a map exactly at the cap is fully probed, untruncated,
    # and (extras being information, not failure) READY.
    def test_map_exactly_at_probe_cap_is_fully_probed(self):
        agents = dict(FULL_AGENTS)
        i = 0
        while len(agents) < herdctl._HEALTH_MAX_AGENT_PROBES:
            agents[f"obs{i}"] = f"h-obs-{i}"
            i += 1

        code, out = self.invoke(
            overrides={
                "state": Mock(return_value={"agents": agents}),
            },
        )

        self.assertEqual(code, 0)
        self.assertIn("Health: READY", out)
        self.assertNotIn("not probed", out)
        self.assertEqual(
            self.doubles["agent_info"].call_count,
            herdctl._HEALTH_MAX_AGENT_PROBES,
        )

    # i. CLI exposure through herdctl.main().
    def test_cli_wires_health_subcommand(self):
        handler = Mock()

        with patch.object(herdctl, "health", handler), patch.object(
            herdctl.sys,
            "argv",
            ["herdctl", "health", "--repo", "demo"],
        ):
            herdctl.main()

        handler.assert_called_once()
        args = handler.call_args[0][0]
        self.assertEqual(args.repo, "demo")


class HerdctlHealthRealTreeTests(RowInvariantMixin, unittest.TestCase):
    """Regression tests that hand health real (including malformed) files.

    Real cfg/state/load_task read a temp `.herd` tree; only repo
    resolution and Herdr access are doubled, so the tests stay hermetic
    (no `herdr` binary, no network, no unpatched subprocess).
    """

    def make_tree(
        self,
        config=None,
        runtime=None,
        task=None,
    ):
        """Write raw file contents (strings) into a temp `.herd` tree."""
        tmp = Path(tempfile.mkdtemp(prefix="herd-health-"))
        self.addCleanup(shutil.rmtree, tmp, True)
        herd = tmp / ".herd"
        (herd / "state").mkdir(parents=True)
        if config is not None:
            (herd / "herd.config.json").write_text(config)
        if runtime is not None:
            (herd / "state" / "runtime.json").write_text(runtime)
        if task is not None:
            (herd / "state" / "task.json").write_text(task)
        return tmp

    def full_tree(self, **overrides):
        defaults = {
            "config": json.dumps(FULL_CONFIG),
            "runtime": json.dumps({"agents": dict(FULL_AGENTS)}),
            "task": json.dumps({"status": "IDLE"}),
        }
        defaults.update(overrides)
        return self.make_tree(**defaults)

    def run_real(self, tmp, which="/usr/bin/herdr", agent_status="idle"):
        """Run health against the tree; return (exit_code, output).

        Anything other than SystemExit escaping health fails the test —
        that is the no-traceback assertion for real malformed inputs.
        """
        out = io.StringIO()
        code = 0

        with contextlib.ExitStack() as stack:
            stack.enter_context(
                patch.object(
                    herdctl,
                    "resolve_repo_ref",
                    Mock(return_value=tmp),
                ),
            )
            stack.enter_context(
                patch.object(
                    herdctl,
                    "run",
                    Mock(return_value=proc(0)),
                ),
            )
            stack.enter_context(
                patch.object(
                    herdctl,
                    "agent_info",
                    Mock(return_value={"status": agent_status, "raw": {}}),
                ),
            )
            stack.enter_context(
                patch.object(
                    herdctl.shutil,
                    "which",
                    Mock(return_value=which),
                ),
            )
            stack.enter_context(
                patch("sys.stdout", out),
            )
            try:
                herdctl.health(SimpleNamespace(repo=str(tmp)))
            except SystemExit as exc:
                code = exc.code if exc.code is not None else 0

        return code, out.getvalue()

    def assert_failed_actionably(self, code, out):
        self.assertNotEqual(code, 0)
        self.assertIn("remedy:", out)
        self.assertIn("Health: NOT READY", out)
        self.assert_one_row_per_category(out)

    def test_real_healthy_tree_is_ready(self):
        code, out = self.run_real(self.full_tree())

        self.assertEqual(code, 0)
        self.assertIn("Health: READY", out)
        self.assert_one_row_per_category(out)

    # B1.a/b + B1.2 — config parses to a non-dict.
    def test_config_json_array_fails(self):
        code, out = self.run_real(self.full_tree(config="[]"))

        self.assert_failed_actionably(code, out)
        self.assertIn("herd.config.json does not contain a JSON object", out)

    def test_config_json_string_fails(self):
        code, out = self.run_real(self.full_tree(config='"x"'))

        self.assert_failed_actionably(code, out)
        self.assertIn("herd.config.json does not contain a JSON object", out)

    # B2 — config `null` must emit a FAIL row, never a silent skip.
    def test_config_json_null_fails_with_config_row(self):
        code, out = self.run_real(self.full_tree(config="null"))

        self.assert_failed_actionably(code, out)
        self.assertIn("herd.config.json does not contain a JSON object", out)

    # B1.c/d — runtime state parses to a non-dict.
    def test_runtime_json_array_fails(self):
        code, out = self.run_real(self.full_tree(runtime="[]"))

        self.assert_failed_actionably(code, out)
        self.assertIn("runtime.json does not contain a JSON object", out)

    def test_runtime_json_null_fails(self):
        code, out = self.run_real(self.full_tree(runtime="null"))

        self.assert_failed_actionably(code, out)
        self.assertIn("runtime.json does not contain a JSON object", out)

    # B1.e + B1.4 — task.json parses to a non-dict.
    def test_task_json_array_fails(self):
        code, out = self.run_real(self.full_tree(task="[]"))

        self.assert_failed_actionably(code, out)
        self.assertIn("task.json is not valid JSON", out)

    def test_task_json_null_fails(self):
        code, out = self.run_real(self.full_tree(task="null"))

        self.assert_failed_actionably(code, out)
        self.assertIn("task.json is not valid JSON", out)

    # B1.f/g — unreadable files (permissions).
    @unittest.skipIf(
        os.geteuid() == 0,
        "chmod 000 is not enforced for root",
    )
    def test_unreadable_config_fails(self):
        tmp = self.full_tree()
        path = tmp / ".herd" / "herd.config.json"
        path.chmod(0)
        self.addCleanup(path.chmod, 0o644)

        code, out = self.run_real(tmp)

        self.assert_failed_actionably(code, out)
        self.assertIn("could not read", out)
        self.assertIn("herd.config.json", out)

    @unittest.skipIf(
        os.geteuid() == 0,
        "chmod 000 is not enforced for root",
    )
    def test_unreadable_runtime_fails(self):
        tmp = self.full_tree()
        path = tmp / ".herd" / "state" / "runtime.json"
        path.chmod(0)
        self.addCleanup(path.chmod, 0o644)

        code, out = self.run_real(tmp)

        self.assert_failed_actionably(code, out)
        self.assertIn("could not read", out)
        self.assertIn("runtime.json", out)

    # B1.h — config path is a directory.
    def test_config_is_directory_fails(self):
        tmp = self.full_tree(config=None)
        (tmp / ".herd" / "herd.config.json").mkdir()

        code, out = self.run_real(tmp)

        self.assert_failed_actionably(code, out)
        self.assertIn("could not read", out)
        self.assertIn("herd.config.json", out)

    # B1.i + B1.5 — non-string agent name in runtime state. The remedy
    # depends on WHICH role is corrupt: `start_herd` reads only
    # agents["supervisor"] from old state, so a truthy non-string
    # supervisor makes `bootstrap --force` traceback (repair-first
    # remedy), while a corrupt non-supervisor name leaves `--force`
    # genuinely accurate (see StartHerdCorruptStateContractTests).
    def test_non_string_agent_name_fails(self):
        agents = dict(FULL_AGENTS)
        agents["executor1"] = 5
        tmp = self.full_tree(runtime=json.dumps({"agents": agents}))

        code, out = self.run_real(tmp)

        self.assert_failed_actionably(code, out)
        self.assertIn("executor1 has a non-string agent name", out)
        self.assertIn("executor1=invalid", out)
        self.assertIn("re-run `herdctl bootstrap --force` to relaunch", out)

        agents = dict(FULL_AGENTS)
        agents["supervisor"] = 5
        tmp = self.full_tree(runtime=json.dumps({"agents": agents}))

        code, sup_out = self.run_real(tmp)

        self.assert_failed_actionably(code, sup_out)
        self.assertIn("supervisor has a non-string agent name", sup_out)
        self.assertIn("supervisor=invalid", sup_out)
        self.assertIn("move the file aside", sup_out)
        self.assertNotIn("--force", sup_out)

    # The corrupt-supervisor boundary is truthiness of a non-string
    # value: an empty-string supervisor short-circuits start_herd's
    # live probe and bootstraps fine, so `--force` stays accurate and
    # must not regress to the repair-first remedy.
    def test_empty_string_supervisor_keeps_force_remedy(self):
        agents = dict(FULL_AGENTS)
        agents["supervisor"] = ""
        tmp = self.full_tree(runtime=json.dumps({"agents": agents}))

        code, out = self.run_real(tmp)

        self.assert_failed_actionably(code, out)
        self.assertIn("supervisor=invalid", out)
        self.assertIn("re-run `herdctl bootstrap --force` to relaunch", out)

    # B3 — runtime state drifted from the config's expected roles.
    def test_expected_role_missing_from_runtime_fails(self):
        tmp = self.full_tree(
            runtime=json.dumps(
                {"agents": {"executor1": "h-demo-exec1"}},
            ),
        )

        code, out = self.run_real(tmp)

        self.assert_failed_actionably(code, out)
        self.assertIn(
            "expected role(s) absent from runtime state: lead1, reviewer1, supervisor",
            out,
        )
        self.assertIn("herdctl bootstrap --force", out)

    def test_extra_agents_are_information_not_failure(self):
        agents = dict(FULL_AGENTS)
        agents["scout"] = "h-demo-scout"
        tmp = self.full_tree(runtime=json.dumps({"agents": agents}))

        code, out = self.run_real(tmp)

        self.assertEqual(code, 0)
        self.assertIn("extra agent(s) beyond config: scout", out)
        self.assertIn("Health: READY", out)

    # B3 defensiveness — malformed orchestration must not traceback.
    def test_non_dict_orchestration_fails(self):
        config = {"roles": FULL_ROLES, "orchestration": []}
        tmp = self.full_tree(config=json.dumps(config))

        code, out = self.run_real(tmp)

        self.assert_failed_actionably(code, out)
        self.assertIn("cannot derive expected roles", out)
        self.assertIn("`orchestration` in herd.config.json is not a JSON object", out)

    def test_infinity_orchestration_count_fails(self):
        # Python's json.loads accepts `Infinity`; int(inf) raises
        # OverflowError, which must convert to a FAIL row, not escape.
        config = (
            '{"roles": {"supervisor": {}, "lead1": {}, "executor1": {}, '
            '"reviewer1": {}}, "orchestration": {"leads": 1, "pods": Infinity}}'
        )
        tmp = self.full_tree(config=config)

        code, out = self.run_real(tmp)

        self.assert_failed_actionably(code, out)
        self.assertIn("must be integers", out)

    def test_non_int_orchestration_counts_fail(self):
        for bad in ("x", None, []):
            config = {
                "roles": FULL_ROLES,
                "orchestration": {"leads": bad, "pods": 1},
            }
            tmp = self.full_tree(config=json.dumps(config))

            code, out = self.run_real(tmp)

            self.assert_failed_actionably(code, out)
            self.assertIn("must be integers", out)

    # C1.a — an out-of-range orchestration count must fail fast, never
    # expand the role set. The elapsed bound is generous (the failure it
    # guards against is a multi-second hang or worse, not milliseconds).
    def test_out_of_range_orchestration_count_fails_fast(self):
        config = {
            "roles": FULL_ROLES,
            "orchestration": {"leads": 1, "pods": 1e9},
        }
        tmp = self.full_tree(config=json.dumps(config))

        start = time.monotonic()
        code, out = self.run_real(tmp)
        elapsed = time.monotonic() - start

        self.assert_failed_actionably(code, out)
        self.assertIn("cannot derive expected roles", out)
        self.assertIn(
            "must be at most " + str(herdctl._HEALTH_MAX_ROLE_COUNT),
            out,
        )
        self.assertLess(elapsed, 2.0)

    # C1.b — enumerated diagnostics are truncated: an in-range count that
    # previously produced one unbounded remedy line stays bounded.
    def test_missing_role_diagnostic_lines_are_bounded(self):
        config = {
            "roles": FULL_ROLES,
            "orchestration": {"leads": 1, "pods": 1000},
        }
        tmp = self.full_tree(config=json.dumps(config))

        code, out = self.run_real(tmp)

        self.assert_failed_actionably(code, out)
        self.assertIn("expected role(s) absent from runtime state:", out)
        self.assertIn("... and", out)
        longest = max(len(line) for line in out.splitlines())
        self.assertLessEqual(longest, 512, f"longest line: {longest}")

    # C1.b — a large-but-legal runtime map must not produce unbounded
    # detail lines either (states and extras both truncate).
    def test_large_runtime_map_lines_are_bounded(self):
        agents = dict(FULL_AGENTS)
        for i in range(300):
            agents[f"obs{i}"] = f"h-obs-{i}"
        tmp = self.full_tree(runtime=json.dumps({"agents": agents}))

        code, out = self.run_real(tmp)

        self.assertEqual(code, 0)
        self.assertIn("Health: READY", out)
        self.assertIn("... and", out)
        longest = max(len(line) for line in out.splitlines())
        self.assertLessEqual(longest, 512, f"longest line: {longest}")

    # B4 mutant pins — the empty-payload FAIL branches.
    def test_config_without_roles_fails(self):
        tmp = self.full_tree(config=json.dumps({"roles": {}}))

        code, out = self.run_real(tmp)

        self.assert_failed_actionably(code, out)
        self.assertIn("no roles defined in", out)

    def test_runtime_without_agents_mapping_fails(self):
        tmp = self.full_tree(runtime=json.dumps({"agents": {}}))

        code, out = self.run_real(tmp)

        self.assert_failed_actionably(code, out)
        self.assertIn("no `agents` mapping in", out)

    # Remedy accuracy on the malformed-runtime branches. `start_herd`
    # re-reads runtime.json BEFORE its force check, so on these states
    # `herdctl bootstrap --force` alone tracebacks (pinned executable by
    # StartHerdCorruptStateContractTests below) — the remedy must name
    # the prerequisite repair first and must not advertise `--force` as
    # a standalone fix anywhere in the report.
    def assert_repair_first_remedy(self, code, out):
        self.assert_failed_actionably(code, out)
        self.assertIn("move the file aside", out)
        self.assertIn("then re-run `herdctl bootstrap`", out)
        self.assertNotIn("--force", out)

    def test_invalid_json_runtime_remedy_names_repair_first(self):
        code, out = self.run_real(self.full_tree(runtime="{not json"))

        self.assertIn("runtime.json is not valid JSON", out)
        self.assert_repair_first_remedy(code, out)

    def test_non_object_runtime_remedy_names_repair_first(self):
        for raw in ("[1, 2, 3]", '"hello"'):
            code, out = self.run_real(self.full_tree(runtime=raw))

            self.assertIn("runtime.json does not contain a JSON object", out)
            self.assert_repair_first_remedy(code, out)

    def test_non_object_agents_value_remedy_names_repair_first(self):
        for raw in ('{"agents": "nope"}', '{"agents": null}', '{"agents": [1]}'):
            code, out = self.run_real(self.full_tree(runtime=raw))

            self.assertIn("`agents` in", out)
            self.assertIn("is not a JSON object", out)
            self.assert_repair_first_remedy(code, out)

    @unittest.skipIf(
        os.geteuid() == 0,
        "chmod 000 is not enforced for root",
    )
    def test_unreadable_runtime_remedy_orders_permission_fix_first(self):
        tmp = self.full_tree()
        path = tmp / ".herd" / "state" / "runtime.json"
        path.chmod(0)
        self.addCleanup(path.chmod, 0o644)

        code, out = self.run_real(tmp)

        self.assert_failed_actionably(code, out)
        self.assertIn(
            "fix the file's permissions, then re-run `herdctl bootstrap`",
            out,
        )
        self.assertNotIn("--force", out)

    # Empty/absent `agents` mapping is the opposite case: `start_herd`
    # reads past it normally, so plain bootstrap IS the whole remedy.
    def test_empty_agents_mapping_remedy_is_plain_bootstrap(self):
        for raw in ('{"agents": {}}', "{}"):
            code, out = self.run_real(self.full_tree(runtime=raw))

            self.assert_failed_actionably(code, out)
            self.assertIn("no `agents` mapping in", out)
            self.assertIn(
                "re-run `herdctl bootstrap` to rebuild runtime state",
                out,
            )
            self.assertNotIn("--force", out)

    # j. Non-mutating: a real herd tree is byte-identical after health.
    def test_health_writes_nothing(self):
        tmp = self.full_tree()

        def snapshot(root):
            snap = {}
            for path in sorted(root.rglob("*")):
                rel = str(path.relative_to(root))
                if path.is_file():
                    snap[rel] = path.read_bytes()
                else:
                    snap[rel] = "<dir>"
            return snap

        before = snapshot(tmp)
        code, out = self.run_real(tmp)

        self.assertEqual(before, snapshot(tmp))
        self.assertEqual(code, 0)
        self.assertIn("Health: READY", out)

    def test_health_writes_nothing_on_malformed_tree(self):
        tmp = self.full_tree(config="null", runtime="[]", task="[]")

        def snapshot(root):
            return {
                str(path.relative_to(root)): (
                    path.read_bytes() if path.is_file() else "<dir>"
                )
                for path in sorted(root.rglob("*"))
            }

        before = snapshot(tmp)
        code, out = self.run_real(tmp)

        self.assertEqual(before, snapshot(tmp))
        self.assertNotEqual(code, 0)
        self.assertIn("Health: NOT READY", out)


class StartHerdCorruptStateContractTests(unittest.TestCase):
    """Executable contract behind the malformed-runtime health remedies.

    `_health_runtime_row` tells the operator that on corrupt runtime
    state `herdctl bootstrap` — forced or not — cannot succeed until
    runtime.json is repaired or moved aside: `start_herd` re-reads the
    old state BEFORE its force check, so it raises either way, while an
    empty/absent `agents` mapping (or an absent file) bootstraps
    normally. The same applies to the Agents-row remedy split: a truthy
    non-string agents["supervisor"] reaches subprocess through the
    live-supervisor probe before the force check (TypeError), while
    falsy values short-circuit it and corrupt NON-supervisor names are
    never read at all. These tests pin that lifecycle behavior so the
    remedy text stays truthful: if herdr/lifecycle.py is later changed
    to tolerate corrupt state, they fail and force the health remedies
    to be updated in lockstep. Everything herdr-side is mocked (the
    tests/test_lifecycle.py pattern); the supervisor-probe cases keep
    the real subprocess boundary via _hermetic_subprocess_run — still
    no herdr binary and no network.
    """

    def make_instance(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        repo = Path(temp.name)
        herd_root = repo / ".herd"
        (herd_root / "roles").mkdir(parents=True)
        for role in ["supervisor", "lead", "executor", "reviewer"]:
            (herd_root / "roles" / f"{role}.md").write_text(f"# {role}\n")
        config = {
            "version": 4,
            "project": {"name": "test", "test_command": "pytest"},
            "orchestration": {
                "leads": 1,
                "pods": 1,
                "agent_start_timeout_ms": 60000,
                "shell_ready_timeout_ms": 30000,
                "agent_task_timeout_ms": 600000,
                "heartbeat_autostart": True,
            },
            "roles": {
                role: {"kind": "claude", "args": []}
                for role in ["supervisor", "lead", "executor", "reviewer"]
            },
            "policy": {},
        }
        (herd_root / "herd.config.json").write_text(json.dumps(config))
        return HerdrInstance(repo)

    def write_runtime(self, herd, raw):
        state_path = herd.herd_root / "state" / "runtime.json"
        state_path.parent.mkdir(parents=True, exist_ok=True)
        state_path.write_text(raw)

    @staticmethod
    def _hermetic_subprocess_run(cmd):
        """Stand-in for herdr.lifecycle.run that keeps subprocess real.

        The supervisor-live probe must NOT be blanket-mocked in the
        corrupt-supervisor cases: a plain Mock swallows subprocess.run's
        argument-type checking, which is exactly what masked the
        corrupt-supervisor defect in review round 1. Swapping argv[0]
        for POSIX `true` (exits 0, ignores its arguments) keeps the
        test hermetic — no herdr binary, no network — while a
        non-string argument still raises TypeError exactly as it would
        against real herdr. Exit 0 also makes any probed supervisor
        string read as "live", which the force-required case relies on.
        """
        return subprocess.run(
            ["true"] + list(cmd[1:]),
            capture_output=True,
            text=True,
        )

    def start(self, herd, force, run_double=None):
        """Run start_herd with the herdr side fully mocked.

        run_double replaces herdr.lifecycle.run; the default Mock is
        fine wherever the corrupt state raises before any run() call.
        Pass _hermetic_subprocess_run to preserve the real subprocess
        argument-type boundary (see its docstring for why).
        """
        ok = SimpleNamespace(returncode=0, stdout="", stderr="")
        with contextlib.ExitStack() as stack:
            mock_jrun = stack.enter_context(patch("herdr.lifecycle.jrun"))
            mock_split = stack.enter_context(patch("herdr.lifecycle.split"))
            stack.enter_context(patch("herdr.lifecycle.start_agent"))
            # R-53 AQ-2: bootstrap now BINDS every role from live
            # evidence before reporting ready, so Herdr's answer has
            # to be modelled here too. Without it these contract
            # tests would fail on the binding step rather than on the
            # corrupt-state behaviour they exist to pin.
            stack.enter_context(patch(
                "herdr.lifecycle.agent_info",
                side_effect=lambda agent: {
                    "status": "idle",
                    "raw": {"result": {"agent": {
                        "name": agent, "cwd": "/repo",
                        "workspace_id": "ws1",
                        "pane_id": "pane-" + agent,
                        "agent_session": {"value": "sess-" + agent},
                    }}},
                },
            ))
            mock_prompt = stack.enter_context(patch("herdr.lifecycle.prompt"))
            stack.enter_context(
                patch(
                    "herdr.lifecycle.run",
                    run_double if run_double is not None else Mock(return_value=ok),
                ),
            )
            stack.enter_context(
                contextlib.redirect_stdout(io.StringIO()),
            )
            mock_jrun.return_value = {
                "result": {
                    "workspace": {"workspace_id": "ws1"},
                    "root_pane": {"pane_id": "pane-root"},
                }
            }
            mock_split.side_effect = [
                "pane-lead",
                "pane-executor",
                "pane-reviewer",
                "pane-controller",
            ]
            mock_prompt.return_value = ok
            return start_herd(herd, force=force)

    # `--force` does not help on any of these: the corrupt read happens
    # before the force check, so both force values must raise.
    def test_invalid_json_raises_for_both_force_values(self):
        for force in (False, True):
            herd = self.make_instance()
            self.write_runtime(herd, "{not json")

            with self.assertRaises(json.JSONDecodeError):
                self.start(herd, force)

    def test_non_object_payload_raises_for_both_force_values(self):
        for raw in ("[1, 2, 3]", '"hello"'):
            for force in (False, True):
                herd = self.make_instance()
                self.write_runtime(herd, raw)

                with self.assertRaises(AttributeError):
                    self.start(herd, force)

    def test_non_object_agents_value_raises_for_both_force_values(self):
        for raw in ('{"agents": "nope"}', '{"agents": null}'):
            for force in (False, True):
                herd = self.make_instance()
                self.write_runtime(herd, raw)

                with self.assertRaises(AttributeError):
                    self.start(herd, force)

    # The states whose remedy is plain bootstrap really do bootstrap.
    def test_empty_agents_mapping_and_absent_state_bootstrap_normally(self):
        for raw in ('{"agents": {}}', "{}", None):
            herd = self.make_instance()
            if raw is not None:
                self.write_runtime(herd, raw)

            state = self.start(herd, force=False)

            self.assertEqual(state["workspace_id"], "ws1")
            self.assertIn("supervisor", state["agents"])

    # Corrupt supervisor entry: start_herd hands agents["supervisor"]
    # straight to subprocess for the live probe, BEFORE the force
    # check, so a truthy non-string value tracebacks at both force
    # values — this is what routes the Agents-row remedy to the
    # repair-first text instead of `--force`.
    def test_truthy_non_string_supervisor_raises_for_both_force_values(self):
        for value in (5, [1], {"x": "y"}):
            for force in (False, True):
                herd = self.make_instance()
                self.write_runtime(
                    herd,
                    json.dumps({"agents": {"supervisor": value}}),
                )

                with self.assertRaises(TypeError):
                    self.start(
                        herd,
                        force,
                        run_double=self._hermetic_subprocess_run,
                    )

    # The boundary is a TRUTHY non-string, not any falsy/invalid value:
    # an empty-string supervisor short-circuits the live probe and
    # bootstraps normally, so health keeps `--force` for it.
    def test_empty_string_supervisor_bootstraps_for_both_force_values(self):
        for force in (False, True):
            herd = self.make_instance()
            self.write_runtime(
                herd,
                json.dumps({"agents": {"supervisor": ""}}),
            )

            state = self.start(
                herd,
                force,
                run_double=self._hermetic_subprocess_run,
            )

            self.assertEqual(state["workspace_id"], "ws1")

    # A corrupt NON-supervisor name never breaks bootstrap (start_herd
    # reads only the supervisor entry), so `--force` genuinely is the
    # right remedy there: live supervisor -> RuntimeError without
    # force, success with force. This also pins the ordinary
    # Agents-row `--force` path (valid runtime dict, live supervisor).
    def test_live_supervisor_with_corrupt_executor_requires_force_only(self):
        raw = json.dumps({
            "workspace_id": "ws-old",
            "agents": {"supervisor": "sup-old", "executor1": 5},
            "panes": {},
        })

        herd = self.make_instance()
        self.write_runtime(herd, raw)
        with self.assertRaises(RuntimeError):
            self.start(
                herd,
                force=False,
                run_double=self._hermetic_subprocess_run,
            )

        herd = self.make_instance()
        self.write_runtime(herd, raw)
        state = self.start(
            herd,
            force=True,
            run_double=self._hermetic_subprocess_run,
        )

        self.assertEqual(state["workspace_id"], "ws1")


if __name__ == "__main__":
    unittest.main()
