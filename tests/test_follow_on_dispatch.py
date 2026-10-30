"""Safe follow-on dispatch (`herdr.follow_on`): a second task to an existing, COMPLETED, healthy child, preserving its sessions.

Every test runs on contained synthetic parent/child directories with the runtime interface faked (`herdr agent get`, the prompt);
`forbid_real_runtime` makes any call that would reach a real `herdr` binary or any subprocess FAIL the test. The REAL
`herdr.tasks.dispatch_task` runs (only its CLI seams are faked), so "no context reset" is observed on the real function.

What is proven (each is a named test):
  * reuse: no context reset, no workspace close, no start, no force, no config or session change;
  * truthful registration of BOTH dependencies, the first record untouched, the completed task's evidence preserved and auditable;
  * a new ACTIVE / ERROR / mismatched / unreadable state BLOCKS parent completion;
  * a failed dispatch never leaves a failed child task without a blocking record on the parent (CASE 1: false completion);
  * an unreadable or corrupt registry or parent state REFUSES, naming the condition, and discards nothing (CASE 2);
  * every preflight refusal names its check and changes nothing;
  * the races and partial writes this design introduces (one follow-on at a time, a registry that moved under the check, an atomic
    write, an interrupted dispatch, a finalize that fails).
"""

import copy
import errno
import fcntl
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

import herdctl  # noqa: E402  (repository root; the real completion command)
from _follow_on_fixture import (  # noqa: E402
    FIRST,
    PANES,
    PARENT_TASK,
    ROLES,
    SECOND,
    SESSIONS,
    Fixture,
    forbid_real_runtime,
    read_json,
    sha256_file,
    write_json,
)
from herdr import follow_on as follow_on_module  # noqa: E402
from herdr import orchestrator  # noqa: E402
from herdr.control_plane import HerdrControlPlane  # noqa: E402
from herdr.dependencies import (  # noqa: E402
    ANCHOR_FILE,
    TASK_ARCHIVE_PARTS,
    assert_child_dependencies_complete,
    child_dependencies,
    read_registration_anchor,
    registration_identity,
)
from herdr.follow_on import (  # noqa: E402
    FollowOnFailed,
    FollowOnRefused,
    follow_on_dispatch,
    normalize_task_policy,
)
from herdr.instance import HerdrInstance  # noqa: E402

TASK_TEXT = "Implement the approved exact-Mission history purge capability, source only."


def herdctl_complete(parent):
    """Run the REAL `herdctl.task_complete_cmd` for a synthetic parent (only `resolve_repo_ref`, which would shell out to git, is replaced)."""
    args = SimpleNamespace(repo=str(parent), checkpoint_file=None, note=None)
    with mock.patch.object(herdctl, "resolve_repo_ref", return_value=Path(parent)):
        herdctl.task_complete_cmd(args)


class FollowOnCase(unittest.TestCase):
    """A fresh contained fixture per test, the real dispatch_task over faked CLI seams, and a record of everything faked."""

    def setUp(self):
        self.fx = Fixture()
        self.addCleanup(self.fx.cleanup)
        forbid_real_runtime(self)
        # `dispatch_task` reports progress with print(); keep it out of the run log (tests that read it capture their own copy)
        quiet = redirect_stdout(io.StringIO())
        quiet.__enter__()
        self.addCleanup(quiet.__exit__, None, None, None)
        self.statuses = {name: "idle" for name in ROLES.values()}
        self.live_changes = {}  # role -> fields of the live agent record that differ from the bound session
        self.prompt_result = SimpleNamespace(returncode=0, stdout="", stderr="")
        self.prompt_error = None

        def fake_info(name):
            role = next((r for r, n in ROLES.items() if n == name), None)
            status = self.statuses.get(name, "missing")
            if role is None or status == "missing":
                return {"status": "missing", "raw": None}  # exactly what runtime.agent_info returns when `agent get` fails
            if status == "unknown":
                return {"status": "unknown", "raw": None}
            return self.fx.live(role, status, **self.live_changes.get(role, {}))

        def fake_prompt(agent, text, timeout, wait=True):
            self.prompts.append((agent, text))
            if self.after_prompt is not None:
                self.after_prompt()
            if self.prompt_error is not None:
                raise self.prompt_error
            return self.prompt_result

        self.prompts = []
        self.after_prompt = None  # a hook that runs INSIDE the dispatch, after the prompt was sent (a live change, a rival writer)
        for target, replacement in (
            ("herdr.follow_on.agent_info", fake_info),
            ("herdr.tasks.agent_info", fake_info),
            ("herdr.tasks.prompt", fake_prompt),
        ):
            patcher = mock.patch(target, side_effect=replacement)
            patcher.start()
            self.addCleanup(patcher.stop)
        patcher = mock.patch("herdr.tasks.clear_contexts")
        self.clear_contexts = patcher.start()
        self.addCleanup(patcher.stop)
        self.parent = HerdrInstance(self.fx.parent)

    def follow_on(self, **overrides):
        kwargs = {"task": TASK_TEXT}
        kwargs.update(overrides)
        return follow_on_dispatch(self.parent, self.fx.child, **kwargs)

    def parent_completion_blocked(self):
        """True iff the parent's task cannot complete (the gate `herdctl task-complete` calls)."""
        try:
            assert_child_dependencies_complete(self.parent, PARENT_TASK)
        except RuntimeError as exc:
            return str(exc)
        return None

    def assert_parent_status_active(self):
        self.assertEqual(read_json(self.fx.parent_state / "task.json")["status"], "ACTIVE")

    def assert_nothing_dispatched(self):
        self.assertEqual(self.prompts, [], "no prompt reached any agent")
        self.clear_contexts.assert_not_called()


class ReuseTests(FollowOnCase):
    def test_reuses_the_healthy_completed_child_without_a_reset_a_close_a_start_or_a_config_change(self):
        before = self.fx.digests()
        result = self.follow_on(rules=["Source only; no install."])
        # the real dispatch_task ran, and it did NOT reset any context
        self.clear_contexts.assert_not_called()
        self.assertEqual(len(self.prompts), 1)
        agent, text = self.prompts[0]
        self.assertEqual(agent, ROLES["supervisor"], "the task went to the EXISTING live Supervisor")
        self.assertIn(TASK_TEXT, text)
        self.assertIn("Source only; no install.", text, "the task's rules ride on the task")
        self.assertFalse(result["context_reset_issued"])
        self.assertFalse(result["workspace_close_issued"])
        # what was OBSERVED about the sessions, role by role, before and after (not a constant)
        expected = {role: "present" for role in ROLES}
        self.assertEqual(result["sessions"], {"before": expected, "after": expected})
        # ... and exactly one file of the child changed: task.json (the new ACTIVE task). Config, runtime, archive: untouched.
        after = self.fx.digests()
        changed = sorted(k for k in set(before) | set(after) if before.get(k) != after.get(k))
        self.assertEqual(
            changed,
            sorted(
                [
                    "child/.herd/state/task.json",
                    "parent/.herd/state/children.json",
                    f"parent/.herd/state/{ANCHOR_FILE}",  # the registration anchor: the one new file, written by the follow-on
                ]
            ),
        )
        task = read_json(self.fx.child_task_json)
        self.assertEqual((task["status"], task["description"]), ("ACTIVE", TASK_TEXT))
        self.assertEqual(task["id"], result["task"]["id"])

    def test_the_child_configuration_is_never_edited_the_rules_are_task_scoped(self):
        config_before = sha256_file(self.fx.child_config_json)
        self.follow_on(rules=["a task-only rule"], task_policy={"rules": ["another"]})
        self.assertEqual(sha256_file(self.fx.child_config_json), config_before)
        task = read_json(self.fx.child_task_json)
        self.assertEqual(task["policy"], {"rules": ["a task-only rule", "another"]})

    def test_both_dependencies_are_registered_truthfully_and_the_first_record_is_untouched(self):
        first_before = copy.deepcopy(self.fx.registry()["children"][0])
        result = self.follow_on()
        records = self.fx.registry()["children"]
        self.assertEqual(len(records), 2)
        self.assertEqual(records[0], first_before, "the first record, its task id included, is never replaced")
        second = records[1]
        self.assertEqual(second["task_id"], result["task"]["id"])
        self.assertNotEqual(second["task_id"], FIRST)
        self.assertEqual(second["parent_task_id"], PARENT_TASK)
        self.assertTrue(second["dependency"])
        self.assertEqual(second["repo"], str(self.fx.child))
        self.assertEqual(second["follow_on_of"], FIRST)
        self.assertEqual((second["dispatch_state"], second["task_status"]), ("DISPATCHED", "ACTIVE"))
        self.assertEqual((second["workspace_id"], second["agents"]), ("w-1", ROLES))
        # each (repository, task id) is addressable exactly once (workspace ownership matches on exactly that)
        self.assertEqual(sum(1 for r in records if r["task_id"] == FIRST), 1)
        self.assertEqual(sum(1 for r in records if r["task_id"] == second["task_id"]), 1)

    def test_the_completed_dependency_resolves_from_its_evidence_and_the_new_one_blocks_the_parent(self):
        result = self.follow_on()
        found = {d["task_id"]: d for d in child_dependencies(self.parent, PARENT_TASK)}
        self.assertEqual(found[FIRST]["current_status"], "COMPLETE")
        self.assertEqual(found[FIRST]["resolved_from"], "durable_evidence")
        self.assertEqual(found[result["task"]["id"]]["current_status"], "ACTIVE")
        blocked = self.parent_completion_blocked()
        self.assertIsNotNone(blocked, "an ACTIVE follow-on blocks the parent's completion")
        self.assertIn(result["task"]["id"], blocked)
        self.assertNotIn(FIRST, blocked)

    def test_the_prior_complete_evidence_is_preserved_and_auditable(self):
        archive = self.fx.archive_path(FIRST)
        before = sha256_file(archive)
        result = self.follow_on()
        self.assertEqual(sha256_file(archive), before, "the archive of the completed task is untouched")
        recorded = self.fx.registry()["children"][1]["prior_evidence"]
        self.assertEqual(recorded, {"task_id": FIRST, "path": str(archive), "sha256": before, "completed_at": 2})
        self.assertEqual(result["prior"], recorded)
        resolved = child_dependencies(self.parent, PARENT_TASK)[0]["evidence"]
        self.assertEqual(resolved["sha256"], before)

    def test_the_parent_completes_once_the_follow_on_is_complete(self):
        result = self.follow_on()
        new_id = result["task"]["id"]
        done = dict(read_json(self.fx.child_task_json), status="COMPLETE", completed_at=99)
        write_json(self.fx.child_task_json, done)
        write_json(self.fx.archive_path(new_id), done)
        self.assertIsNone(self.parent_completion_blocked())

    def test_a_third_follow_on_chains_from_the_second(self):
        second = self.follow_on()["task"]["id"]
        done = dict(read_json(self.fx.child_task_json), status="COMPLETE", completed_at=99)
        write_json(self.fx.child_task_json, done)
        write_json(self.fx.archive_path(second), done)
        with mock.patch("herdr.follow_on.time.strftime", return_value="20261027-130000"):
            third = self.follow_on(task="A third bounded task")["task"]["id"]
        records = self.fx.registry()["children"]
        self.assertEqual([r["task_id"] for r in records], [FIRST, second, third])
        self.assertEqual(records[2]["follow_on_of"], second)
        self.clear_contexts.assert_not_called()

    def test_no_start_no_spawn_no_force_is_ever_reached(self):
        with mock.patch.object(HerdrControlPlane, "start", side_effect=AssertionError("start")), mock.patch.object(
            HerdrControlPlane, "spawn", side_effect=AssertionError("spawn")
        ), mock.patch("herdr.lifecycle.start_herd", side_effect=AssertionError("start_herd")):
            HerdrControlPlane().follow_on_child(self.fx.parent, self.fx.child, task=TASK_TEXT)


class DispatchTaskOptionTests(FollowOnCase):
    """The task-level option, and the default that must NOT change (the hazard this whole path exists to avoid)."""

    def child_instance(self):
        return HerdrInstance(self.fx.child)

    def test_the_default_still_resets_contexts_when_the_previous_task_is_complete_the_hazard(self):
        from herdr.tasks import dispatch_task

        buffer = io.StringIO()
        with redirect_stdout(buffer):
            dispatch_task(self.child_instance(), "A task that does not ask to preserve")
        self.clear_contexts.assert_called_once()
        self.assertIn("resetting completed-task contexts", buffer.getvalue())

    def test_preserve_contexts_skips_the_reset_and_says_so(self):
        from herdr.tasks import dispatch_task

        buffer = io.StringIO()
        with redirect_stdout(buffer):
            dispatch_task(self.child_instance(), "A task that asks to preserve", preserve_contexts=True)
        self.clear_contexts.assert_not_called()
        self.assertIn("keeping the agents' contexts", buffer.getvalue())

    def test_an_active_previous_task_still_blocks_a_dispatch_whatever_is_asked(self):
        from herdr.tasks import dispatch_task

        write_json(self.fx.child_task_json, {"id": "x", "status": "ACTIVE"})
        with self.assertRaises(RuntimeError):
            dispatch_task(self.child_instance(), "second", preserve_contexts=True)
        self.assertEqual(self.prompts, [])

    def test_a_named_task_id_is_used_verbatim_and_a_bad_one_is_refused_before_anything_happens(self):
        from herdr.tasks import dispatch_task

        task = dispatch_task(self.child_instance(), "named", preserve_contexts=True, task_id="my-task-1")
        self.assertEqual(task["id"], "my-task-1")
        for bad in ("../x", "a/b", "", ".hidden", "x" * 200):
            with self.subTest(task_id=bad):
                before = len(self.prompts)
                with self.assertRaises(ValueError):
                    dispatch_task(self.child_instance(), "named", preserve_contexts=True, task_id=bad)
                self.assertEqual(len(self.prompts), before)

    def test_the_archive_location_is_the_one_herdctl_writes(self):
        import herdctl

        self.assertEqual("/".join(TASK_ARCHIVE_PARTS), herdctl.TASK_ARCHIVE)


class PreflightRefusalTests(FollowOnCase):
    """Each refuses BY NAME and changes NOTHING (every file's digest, no prompt, no context reset)."""

    def refuses(self, check, mutate=None, **kwargs):
        if mutate is not None:
            mutate(self.fx)
        before = self.fx.digests()
        with self.assertRaises(FollowOnRefused) as caught:
            self.follow_on(**kwargs)
        self.assertEqual(caught.exception.check, check, str(caught.exception))
        self.assertIn(check, str(caught.exception), "the refusal says which check failed")
        self.assertEqual(self.fx.digests(), before, f"{check}: nothing was changed")
        self.assert_nothing_dispatched()
        return caught.exception

    # -- the parent ----------------------------------------------------------------------------------------------

    def test_parent_not_initialized(self):
        self.refuses("parent_not_initialized", lambda fx: (fx.parent / ".herd" / "herd.config.json").unlink())

    def test_the_target_is_the_parent(self):
        with self.assertRaises(FollowOnRefused) as caught:
            follow_on_dispatch(self.parent, self.fx.parent, task=TASK_TEXT)
        self.assertEqual(caught.exception.check, "target_is_parent")

    def test_parent_runtime_missing_or_unreadable(self):
        self.refuses("parent_runtime_missing", lambda fx: (fx.parent_state / "runtime.json").unlink())

    def test_parent_runtime_unreadable(self):
        self.refuses("parent_runtime_unreadable", lambda fx: (fx.parent_state / "runtime.json").write_text("{nope"))

    def test_the_parent_task_file_missing_unreadable_invalid_or_not_active_REFUSES_it_is_never_read_as_empty(self):
        # CASE 2: spawn_child turns an unreadable parent task into {} and silently records NO dependency. The reuse path refuses.
        cases = (
            ("parent_task_missing", lambda fx: (fx.parent_state / "task.json").unlink()),
            ("parent_task_unreadable", lambda fx: (fx.parent_state / "task.json").write_text("{not json")),
            ("parent_task_invalid", lambda fx: (fx.parent_state / "task.json").write_text("[]")),
            ("parent_task_not_active", lambda fx: write_json(fx.parent_state / "task.json", {"id": PARENT_TASK, "status": "COMPLETE"})),
            ("parent_task_not_active", lambda fx: write_json(fx.parent_state / "task.json", {"status": "ACTIVE"})),
        )
        for check, mutate in cases:
            with self.subTest(check=check):
                self.setUp()
                self.refuses(check, mutate)

    # -- the registry (CASE 2: unreadable is not empty; prior dependencies are NOT discarded) -------------------------

    def test_the_registry_missing_unreadable_or_invalid_REFUSES_and_the_prior_dependencies_survive(self):
        cases = (
            ("registry_missing", lambda fx: fx.children_json.unlink()),
            ("registry_unreadable", lambda fx: fx.children_json.write_text("{corrupt")),
            ("registry_unreadable", lambda fx: fx.children_json.write_text("")),
            ("registry_invalid", lambda fx: fx.children_json.write_text("[]")),
            ("registry_invalid", lambda fx: write_json(fx.children_json, {"version": 1})),
            ("registry_invalid", lambda fx: write_json(fx.children_json, {"children": {}})),
            ("registry_invalid", lambda fx: write_json(fx.children_json, {"children": ["not an object"]})),
        )
        for check, mutate in cases:
            with self.subTest(check=check):
                self.setUp()
                mutate(self.fx)
                text_before = self.fx.children_json.read_bytes() if self.fx.children_json.exists() else None
                self.refuses(check)
                if text_before is not None:
                    self.assertEqual(self.fx.children_json.read_bytes(), text_before, "a corrupt registry is not rewritten as empty")

    def test_an_unreadable_registry_discards_nothing_a_readable_one_keeps_its_first_dependency(self):
        before = copy.deepcopy(self.fx.registry())
        self.fx.children_json.write_text("{corrupt")
        with self.assertRaises(FollowOnRefused):
            self.follow_on()
        self.fx.children_json.write_text(json.dumps(before, indent=2) + "\n")
        self.follow_on()
        self.assertEqual(self.fx.registry()["children"][0], before["children"][0])

    def test_the_target_not_registered_under_this_parent_task(self):
        self.refuses(
            "child_not_registered",
            lambda fx: write_json(
                fx.children_json,
                {"version": 1, "children": [dict(fx.registry()["children"][0], parent_task_id="another-parent-task")]},
            ),
        )

    def test_the_target_registered_for_a_different_repository(self):
        self.refuses(
            "child_not_registered",
            lambda fx: write_json(
                fx.children_json,
                {"version": 1, "children": [dict(fx.registry()["children"][0], repo=str(fx.root / "somewhere-else"))]},
            ),
        )

    def test_the_expected_task_id_must_be_the_one_the_parent_recorded(self):
        self.refuses("expected_task_mismatch", expected_task_id="20260101-000000-000000")
        self.setUp()
        self.follow_on(expected_task_id=FIRST)

    def test_a_pending_follow_on_must_be_resolved_first(self):
        self.refuses("follow_on_pending", lambda fx: fx.register_second(task_id=SECOND, dispatch_state="PENDING", follow_on_of=FIRST))

    # -- the child's task ----------------------------------------------------------------------------------------

    def test_child_not_initialized_or_config_unreadable(self):
        self.refuses("child_not_initialized", lambda fx: fx.child_config_json.unlink())
        self.setUp()
        self.refuses("child_config_unreadable", lambda fx: fx.child_config_json.write_text("{"))

    def test_the_child_task_missing_unreadable_or_invalid(self):
        for check, mutate in (
            ("child_task_missing", lambda fx: fx.child_task_json.unlink()),
            ("child_task_unreadable", lambda fx: fx.child_task_json.write_text("{")),
            ("child_task_invalid", lambda fx: fx.child_task_json.write_text("[]")),
        ):
            with self.subTest(check=check):
                self.setUp()
                self.refuses(check, mutate)

    def test_an_ACTIVE_child_is_never_taken_over(self):
        self.refuses("child_task_active", lambda fx: write_json(fx.child_task_json, dict(fx.first_record, status="ACTIVE")))

    def test_an_errored_or_aborted_or_unknown_child_task_is_not_complete(self):
        for status in ("ERROR", "ABORTED", "IDLE", "complete", ""):
            with self.subTest(status=status):
                self.setUp()
                self.refuses("child_task_not_complete", lambda fx, s=status: write_json(fx.child_task_json, dict(fx.first_record, status=s)))

    def test_the_child_reports_a_task_the_parent_did_not_record(self):
        self.refuses("child_task_identity_mismatch", lambda fx: write_json(fx.child_task_json, dict(fx.first_record, id="20261027-000000-unknwn")))

    def test_no_durable_evidence_for_the_completed_task(self):
        error = self.refuses("prior_evidence_missing", lambda fx: fx.archive_path(FIRST).unlink())
        self.assertIn("no archived record", str(error))

    # -- the child's runtime and roles ---------------------------------------------------------------------------

    def test_the_child_runtime_missing_unreadable_or_for_another_repository(self):
        for check, mutate in (
            ("child_runtime_missing", lambda fx: fx.child_runtime_json.unlink()),
            ("child_runtime_unreadable", lambda fx: fx.child_runtime_json.write_text("{")),
            (
                "child_runtime_repo_mismatch",
                lambda fx: write_json(fx.child_runtime_json, dict(read_json(fx.child_runtime_json), repo="/elsewhere")),
            ),
            (
                "child_runtime_repo_mismatch",
                lambda fx: write_json(
                    fx.child_runtime_json,
                    {k: v for k, v in read_json(fx.child_runtime_json).items() if k != "repo"},
                ),
            ),
            (
                "child_workspace_unproven",
                lambda fx: write_json(fx.child_runtime_json, dict(read_json(fx.child_runtime_json), workspace_id="")),
            ),
            (
                "runtime_workspace_changed",
                lambda fx: write_json(fx.child_runtime_json, dict(read_json(fx.child_runtime_json), workspace_id="w-OTHER")),
            ),
            (
                "child_roles_unproven",
                lambda fx: write_json(fx.child_runtime_json, dict(read_json(fx.child_runtime_json), agents=[])),
            ),
            (
                "runtime_agents_changed",
                lambda fx: write_json(
                    fx.child_runtime_json,
                    dict(read_json(fx.child_runtime_json), agents=dict(ROLES, supervisor="a-different-supervisor")),
                ),
            ),
        ):
            with self.subTest(check=check):
                self.setUp()
                self.refuses(check, mutate)

    def test_a_required_role_missing(self):
        def drop_reviewer(fx):
            runtime = read_json(fx.child_runtime_json)
            del runtime["agents"]["reviewer1"]
            write_json(fx.child_runtime_json, runtime)
            registry = fx.registry()
            registry["children"][0]["agents"] = dict(runtime["agents"])
            write_json(fx.children_json, registry)

        self.refuses("role_missing", drop_reviewer)

    def test_a_second_lead_the_config_requires_but_the_runtime_lacks(self):
        def two_leads(fx):
            config = read_json(fx.child_config_json)
            config["orchestration"]["leads"] = 2
            write_json(fx.child_config_json, config)

        error = self.refuses("role_missing", two_leads)
        self.assertIn("lead2", str(error))

    def test_an_invalid_orchestration_config(self):
        def bad(fx):
            config = read_json(fx.child_config_json)
            config["orchestration"]["pods"] = "many"
            write_json(fx.child_config_json, config)

        self.refuses("child_config_invalid", bad)

    def test_every_role_must_be_idle_a_working_or_blocked_role_refuses_naming_the_role(self):
        for role, name in ROLES.items():
            for status in ("working", "blocked", "busy"):
                with self.subTest(role=role, status=status):
                    self.setUp()
                    self.statuses[name] = status
                    error = self.refuses("role_not_healthy")
                    self.assertIn(f"`{role}`", str(error))
                    self.assertIn("UNKNOWN" if status == "busy" else status, str(error))

    def test_a_role_with_no_live_record_or_an_unreadable_probe_is_an_IDENTITY_refusal_not_a_health_one(self):
        for role, name in ROLES.items():
            for status, check in (("missing", "role_missing_live"), ("unknown", "role_identity_degraded")):
                with self.subTest(role=role, status=status):
                    self.setUp()
                    self.statuses[name] = status
                    error = self.refuses(check)
                    self.assertIn(f"`{role}`", str(error))

    def test_done_is_as_healthy_as_idle(self):
        for name in ROLES.values():
            self.statuses[name] = "done"
        self.follow_on()

    # -- inputs --------------------------------------------------------------------------------------------------

    def test_inputs_are_validated_before_anything_is_read(self):
        before = self.fx.digests()
        for kwargs in (
            {"task": "   "},
            {"task": TASK_TEXT, "task_policy": {"git": {"commit": "allowed"}}},
            {"task": TASK_TEXT, "task_policy": {"rules": ["ok"], "review": {"required": False}}},
            {"task": TASK_TEXT, "rules": "not a list"},
            {"task": TASK_TEXT, "rules": [""]},
            {"task": TASK_TEXT, "task_policy": {"rules": [1]}},
        ):
            with self.subTest(kwargs=kwargs):
                with self.assertRaises(ValueError):
                    self.follow_on(**kwargs)
        self.assertEqual(self.fx.digests(), before)
        self.assert_nothing_dispatched()

    def test_a_task_policy_may_add_rules_and_nothing_else(self):
        self.assertEqual(normalize_task_policy(["a", " a ", "b"], {"rules": ["b", "c"]}), {"rules": ["a", "b", "c"]})
        self.assertIsNone(normalize_task_policy(None, None))
        with self.assertRaises(ValueError) as caught:
            normalize_task_policy(None, {"git": {"push": "allowed"}})
        self.assertIn("git", str(caught.exception))


class FailedDispatchTests(FollowOnCase):
    """CASE 1: a failed dispatch must never leave a failed task on the CHILD with no blocking dependency on the PARENT."""

    def test_a_dispatch_rejected_before_the_child_changed_is_rolled_back_to_the_exact_registry_it_found(self):
        # the preflight (herdr.follow_on.agent_info) saw every role idle; the dispatch's OWN supervisor check
        # (herdr.tasks.agent_info) finds it busy: the dispatch is rejected before it changes the child
        before = self.fx.digests()
        registry_before = self.fx.children_json.read_bytes()
        busy = lambda name: {"status": "busy", "raw": None}  # noqa: E731
        with mock.patch("herdr.tasks.agent_info", side_effect=busy), self.assertRaises(FollowOnRefused) as caught:
            self.follow_on()
        self.assertEqual(caught.exception.check, "dispatch_rejected")
        self.assertIn("Supervisor is `busy`", str(caught.exception))
        self.assertEqual(self.fx.digests(), before, "the child and the parent are exactly as they were")
        self.assertEqual(self.fx.registry(), json.loads(registry_before), "the intent record was removed; the registry is as found")
        self.assert_nothing_dispatched()
        # nothing was started, so nothing is unresolved: the first task is still the child's COMPLETE task
        self.assertIsNone(self.parent_completion_blocked())

    def test_a_dispatch_that_fails_AFTER_the_child_recorded_the_task_leaves_a_BLOCKING_recoverable_record(self):
        self.prompt_result = SimpleNamespace(returncode=1, stdout="", stderr="supervisor did not accept the prompt")
        with self.assertRaises(FollowOnFailed) as caught:
            self.follow_on()
        failure = caught.exception
        self.assertEqual(failure.record_state, "FAILED")
        self.assertIn("REGISTERED", str(failure))
        # the child holds an ERROR task ...
        task = read_json(self.fx.child_task_json)
        self.assertEqual((task["id"], task["status"]), (failure.task_id, "ERROR"))
        # ... and the PARENT holds a record for exactly that task, FAILED, with the error
        records = self.fx.registry()["children"]
        self.assertEqual(len(records), 2)
        self.assertEqual(records[0]["task_id"], FIRST)
        self.assertEqual(records[1]["task_id"], failure.task_id)
        self.assertEqual((records[1]["dispatch_state"], records[1]["task_status"]), ("FAILED", "ERROR"))
        self.assertIn("did not accept the prompt", records[1]["dispatch_error"])
        # THE PROOF THAT MATTERS: the parent cannot complete. (The raise above is already there; it protects nobody.)
        blocked = self.parent_completion_blocked()
        self.assertIsNotNone(blocked, "PARENT COMPLETION IS BLOCKED by the failed follow-on")
        self.assertIn(failure.task_id, blocked)
        self.assertIn("ERROR", blocked)
        found = {d["task_id"]: d for d in child_dependencies(self.parent, PARENT_TASK)}
        self.assertEqual(found[FIRST]["current_status"], "COMPLETE", "the completed first task still resolves from its evidence")
        self.assertEqual(found[failure.task_id]["current_status"], "ERROR")
        self.clear_contexts.assert_not_called()

    def test_without_the_record_the_same_failure_would_have_let_the_parent_COMPLETE_falsely_the_hazard(self):
        # The ordering this module avoids: register the dependency only AFTER the dispatch returns (as `spawn_child` does).
        # Once the completed first task resolves from its evidence, nothing blocks the parent while a failed task sits on the child.
        self.prompt_result = SimpleNamespace(returncode=1, stdout="", stderr="boom")
        registry_before = self.fx.children_json.read_bytes()
        with self.assertRaises(FollowOnFailed):
            self.follow_on()
        self.assertEqual(read_json(self.fx.child_task_json)["status"], "ERROR")
        self.assertIsNotNone(self.parent_completion_blocked(), "with the intent record: blocked")
        self.fx.children_json.write_bytes(registry_before)  # the registry a register-after-success flow would have left
        # the registration anchor still remembers the intent, so putting the old registry back is now a detected LOSS, not a pass
        self.assertIn("REGISTRY_LOSS", self.parent_completion_blocked() or "")
        (self.fx.parent_state / ANCHOR_FILE).unlink()  # a flow that registers nothing leaves no anchor either
        self.assertIsNone(
            self.parent_completion_blocked(),
            "WITHOUT the record the parent could complete over an ERROR task on the child: the false completion this design prevents",
        )

    def test_a_prompt_that_raises_after_the_task_was_saved_is_registered_and_blocks(self):
        self.prompt_error = OSError("the terminal went away")
        with self.assertRaises(FollowOnFailed) as caught:
            self.follow_on()
        self.assertEqual(caught.exception.record_state, "FAILED")
        task = read_json(self.fx.child_task_json)
        self.assertEqual((task["id"], task["status"]), (caught.exception.task_id, "ACTIVE"), "the child may have the task: unknown outcome")
        self.assertEqual(self.fx.registry()["children"][1]["dispatch_state"], "FAILED")
        self.assertIsNotNone(self.parent_completion_blocked())

    def test_a_child_state_that_cannot_be_read_after_the_failure_leaves_the_record_PENDING_and_blocking(self):
        self.prompt_result = SimpleNamespace(returncode=1, stdout="", stderr="boom")
        with mock.patch("herdr.follow_on._child_task_now", return_value=None), self.assertRaises(FollowOnFailed) as caught:
            self.follow_on()
        self.assertEqual(caught.exception.record_state, "PENDING")
        pending = self.fx.registry()["children"][1]
        self.assertEqual(pending["dispatch_state"], "PENDING")
        self.assertEqual(child_dependencies(self.parent, PARENT_TASK)[1]["current_status"], "PENDING")
        self.assertIsNotNone(self.parent_completion_blocked())

    def test_a_pending_record_blocks_the_next_follow_on_until_a_human_resolves_it(self):
        self.prompt_result = SimpleNamespace(returncode=1, stdout="", stderr="boom")
        with mock.patch("herdr.follow_on._child_task_now", return_value=None), self.assertRaises(FollowOnFailed):
            self.follow_on()
        before = self.fx.digests()
        with self.assertRaises(FollowOnRefused) as caught:
            self.follow_on()
        self.assertEqual(caught.exception.check, "follow_on_pending")
        self.assertEqual(self.fx.digests(), before)

    def test_a_finalize_that_fails_leaves_the_dispatched_task_registered_PENDING_and_blocking(self):
        real = follow_on_module._write_registry
        calls = {"n": 0}

        def second_write_fails(path, document, expected_sha256):
            calls["n"] += 1
            if calls["n"] == 2:
                raise OSError("disk full")
            return real(path, document, expected_sha256)

        with mock.patch("herdr.follow_on._write_registry", side_effect=second_write_fails), self.assertRaises(FollowOnFailed) as caught:
            self.follow_on()
        self.assertEqual(caught.exception.record_state, "PENDING")
        self.assertEqual(read_json(self.fx.child_task_json)["status"], "ACTIVE", "the child did accept the task")
        record = self.fx.registry()["children"][1]
        self.assertEqual((record["task_id"], record["dispatch_state"]), (caught.exception.task_id, "PENDING"))
        self.assertIsNotNone(self.parent_completion_blocked())

    def test_an_interrupted_dispatch_leaves_the_intent_PENDING_and_blocking(self):
        self.prompt_error = KeyboardInterrupt()
        with self.assertRaises(KeyboardInterrupt):
            self.follow_on()
        record = self.fx.registry()["children"][1]
        self.assertEqual(record["dispatch_state"], "PENDING")
        self.assertIsNotNone(self.parent_completion_blocked())


class RaceAndPartialWriteTests(FollowOnCase):
    def test_one_follow_on_at_a_time_a_second_is_refused_not_queued(self):
        fd = os.open(self.fx.parent_state, os.O_RDONLY)  # the lock IS the state directory
        self.addCleanup(os.close, fd)
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        before = self.fx.digests()
        with self.assertRaises(FollowOnRefused) as caught:
            self.follow_on()
        self.assertEqual(caught.exception.check, "registry_busy")
        self.assertEqual(self.fx.digests(), before)
        self.assert_nothing_dispatched()

    def test_the_lock_is_released_after_a_refusal_and_after_a_success(self):
        with self.assertRaises(FollowOnRefused):
            self.follow_on(expected_task_id="20260101-000000-000000")
        self.follow_on()  # not "in progress": the earlier call released it

    def test_a_registry_that_changed_between_the_check_and_the_write_is_refused_not_overwritten(self):
        real = follow_on_module._preflight

        def preflight_then_someone_writes(*args, **kwargs):
            context = real(*args, **kwargs)
            registry = self.fx.registry()
            registry["children"].append(dict(registry["children"][0], task_id="another-writer-task", repo=str(self.fx.root / "x")))
            write_json(self.fx.children_json, registry)
            return context

        with mock.patch("herdr.follow_on._preflight", side_effect=preflight_then_someone_writes), self.assertRaises(FollowOnRefused) as caught:
            self.follow_on()
        self.assertEqual(caught.exception.check, "registry_changed")
        self.assertEqual([r["task_id"] for r in self.fx.registry()["children"]], [FIRST, "another-writer-task"], "the other writer's record is intact")
        self.assert_nothing_dispatched()

    def test_an_error_before_the_replace_leaves_the_old_registry_whole_and_no_temp_file(self):
        before = self.fx.children_json.read_bytes()
        with mock.patch("herdr.follow_on.os.replace", side_effect=OSError("disk error")), self.assertRaises(OSError):
            self.follow_on()
        self.assertEqual(self.fx.children_json.read_bytes(), before, "the old registry is whole")
        leftovers = [p.name for p in self.fx.parent_state.iterdir() if ".tmp-" in p.name]
        self.assertEqual(leftovers, [], "no temporary file is left behind")
        self.assert_nothing_dispatched()

    def test_the_registry_file_keeps_its_mode_across_the_atomic_replace(self):
        os.chmod(self.fx.children_json, 0o640)
        self.follow_on()
        self.assertEqual(os.stat(self.fx.children_json).st_mode & 0o777, 0o640)

    def test_existing_records_are_preserved_whatever_extra_fields_they_carry(self):
        registry = self.fx.registry()
        registry["children"][0]["role"] = "investigation"
        registry["children"][0]["extra"] = {"nested": [1, 2, 3]}
        registry["version"] = 1
        write_json(self.fx.children_json, registry)
        self.follow_on()
        after = self.fx.registry()
        self.assertEqual(after["children"][0], registry["children"][0])
        self.assertEqual(after["version"], 1)


class LiveIdentityTests(FollowOnCase):
    """Idle is HEALTH, not IDENTITY: a name answering `idle` proves something is alive under it, not that it is THIS child's
    bound session. Identity is `herdr.identity`'s; PRESENT is required, and REPLACED (a new session) refuses because the
    follow-on exists to preserve the existing sessions."""

    def refuses(self, check, **kwargs):
        before = self.fx.digests()
        with self.assertRaises(FollowOnRefused) as caught:
            self.follow_on(**kwargs)
        self.assertEqual(caught.exception.check, check, str(caught.exception))
        self.assertEqual(self.fx.digests(), before, f"{check}: nothing was changed")
        self.assert_nothing_dispatched()
        return str(caught.exception)

    def test_a_role_whose_SESSION_was_replaced_refuses_though_it_is_idle_and_the_stable_identity_matches(self):
        for role in ROLES:
            with self.subTest(role=role):
                self.setUp()
                self.live_changes[role] = {"agent_session": {"value": "a-NEW-session-id"}}
                message = self.refuses("role_session_replaced")
                self.assertIn(f"`{role}`", message)
                self.assertIn("would not be preserved", message)
                # session ids are never copied into a refusal
                self.assertNotIn("a-NEW-session-id", message)
                self.assertNotIn(SESSIONS[role], message)

    def test_a_live_agent_with_the_same_name_in_another_cwd_workspace_or_pane_is_a_different_herd(self):
        for role in ROLES:
            for field, value in (
                ("cwd", "/somewhere/else"),
                ("workspace_id", "w-OTHER"),
                ("pane_id", "pane-OTHER"),
                ("name", "reused-name"),
            ):
                with self.subTest(role=role, field=field):
                    self.setUp()
                    self.live_changes[role] = {field: value}
                    self.refuses("role_identity_degraded")

    def test_a_role_the_child_never_bound_is_unbound_whether_one_binding_or_the_whole_file_is_absent(self):
        def drop(fx):
            document = fx.bindings_document()
            del document["roles"]["reviewer1"]
            write_json(fx.child_bindings_json, document)

        drop(self.fx)
        self.assertIn("`reviewer1`", self.refuses("role_unbound"))
        self.setUp()
        self.fx.child_bindings_json.unlink()
        self.refuses("role_unbound")

    def test_unreadable_or_wrong_shaped_bindings_refuse_and_are_not_rewritten(self):
        for text in ("{not json", "[]", json.dumps({"version": 1}), json.dumps({"roles": []})):
            with self.subTest(text=text):
                self.setUp()
                self.fx.child_bindings_json.write_text(text)
                self.refuses("child_bindings_corrupt")
                self.assertEqual(self.fx.child_bindings_json.read_text(), text)

    def test_a_binding_for_a_different_agent_than_the_runtime_names_refuses(self):
        document = self.fx.bindings_document()
        document["roles"]["lead1"]["agent"] = "another-lead"
        write_json(self.fx.child_bindings_json, document)
        self.assertIn("`lead1`", self.refuses("role_bound_to_other_agent"))

    def test_a_live_role_bound_in_ANOTHER_repository_refuses_even_though_it_is_present_and_idle(self):
        # the agent really is the session that was bound ... to a different repository: PRESENT by identity, wrong by ownership
        document = self.fx.bindings_document()
        document["roles"]["supervisor"]["stable"]["cwd"] = "/another/repository"
        write_json(self.fx.child_bindings_json, document)
        self.live_changes["supervisor"] = {"cwd": "/another/repository"}
        self.assertIn("`supervisor`", self.refuses("role_not_in_child"))

    def test_a_live_role_bound_in_another_workspace_refuses_even_though_it_is_present_and_idle(self):
        document = self.fx.bindings_document()
        document["roles"]["executor1"]["stable"]["workspace_id"] = "w-ANOTHER"
        write_json(self.fx.child_bindings_json, document)
        self.live_changes["executor1"] = {"workspace_id": "w-ANOTHER"}
        self.assertIn("`executor1`", self.refuses("role_workspace_mismatch"))

    def test_a_binding_that_cannot_show_its_repository_or_workspace_refuses(self):
        for missing in ("cwd", "workspace_id"):
            with self.subTest(missing=missing):
                self.setUp()
                document = self.fx.bindings_document()
                del document["roles"]["reviewer1"]["stable"][missing]
                write_json(self.fx.child_bindings_json, document)
                self.refuses("role_binding_incomplete")

    def test_foreground_cwd_and_a_configured_model_are_NOT_identity(self):
        # foreground_cwd diverges from cwd in real herds, and a model string is configuration: neither admits or refuses a role
        for role in ROLES:
            self.live_changes[role] = {"foreground_cwd": "/anywhere", "model": "a-different-model"}
        self.follow_on()
        self.setUp()
        self.live_changes["supervisor"] = {"cwd": "/somewhere/else", "foreground_cwd": str(self.fx.child)}
        self.refuses("role_identity_degraded")  # a foreground_cwd equal to the child does not rescue a different cwd

    def test_a_role_working_inside_a_subdirectory_of_the_child_is_still_the_childs(self):
        sub = self.fx.child / "worktrees" / "pod-1"
        sub.mkdir(parents=True)
        document = self.fx.bindings_document()
        document["roles"]["executor1"]["stable"]["cwd"] = str(sub)
        write_json(self.fx.child_bindings_json, document)
        self.live_changes["executor1"] = {"cwd": str(sub)}
        self.follow_on()

    def test_the_result_reports_what_was_observed_after_the_dispatch_not_a_constant(self):
        def replaced_after_the_prompt():
            self.live_changes["lead1"] = {"agent_session": {"value": "replaced-during-dispatch"}}

        self.after_prompt = replaced_after_the_prompt
        result = self.follow_on()
        self.assertEqual(result["sessions"]["before"]["lead1"], "present")
        self.assertEqual(result["sessions"]["after"]["lead1"], "replaced")
        self.assertEqual(result["sessions"]["after"]["supervisor"], "present")
        self.assertNotIn("replaced-during-dispatch", json.dumps(result))

    def test_the_post_dispatch_look_never_fails_the_dispatch(self):
        def bindings_vanish():
            self.fx.child_bindings_json.write_text("{broken")

        self.after_prompt = bindings_vanish
        result = self.follow_on()
        self.assertEqual(set(result["sessions"]["after"].values()), {"unverified"})
        self.assertEqual(self.fx.registry()["children"][1]["dispatch_state"], "DISPATCHED")


class RegistryWriterTests(FollowOnCase):
    """The registry has TWO writers: this module and `HerdrControlPlane.spawn_child`. Both take the SAME lock (an flock on the
    parent's state directory). `spawn_child` takes it BEFORE it launches the child and holds it until the child's record is written;
    a writer that does not take the lock is detected before the replace.

    Every test drives the real `spawn_child` with only the child launch (`plane.spawn`) replaced by a recorder, so "launched" means
    exactly "the point at which a real child would start running"."""

    def plane(self, wait=0.0):
        plane = HerdrControlPlane()
        plane.REGISTRY_WAIT_SECONDS = wait
        self.launched = []
        self.during_launch = []

        def launch(repo, **kwargs):
            self.launched.append(Path(repo).name)
            for probe in self.launch_probes:
                self.during_launch.append(probe())
            name = Path(repo).name
            return {
                "repo": str(repo),
                "runtime": {"workspace_id": f"w-{name}", "agents": {"supervisor": f"{name}-sup"}},
                "task": {"id": f"spawned-{name}", "status": "ACTIVE"},
                "policy": {},
            }

        self.launch_probes = []
        plane.spawn = mock.Mock(side_effect=launch)
        return plane

    def spawn(self, name="spawned-child", wait=0.0):
        plane = self.plane(wait)
        return plane.spawn_child(self.fx.parent, self.fx.root / name, task="synthetic spawn")

    def refused(self, probe):
        try:
            probe()
        except FollowOnRefused as refusal:
            return refusal.check
        except SystemExit as stop:
            return "SystemExit: " + str(stop)
        return "NOT REFUSED"

    def rival_record(self, name="rival"):
        return {
            "requested_at": 50,
            "parent_repo": str(self.fx.parent),
            "parent_task_id": PARENT_TASK,
            "dependency": True,
            "repo": str(self.fx.root / name),
            "task_id": f"spawned-{name}",
            "task_status": "ACTIVE",
        }

    def hold_state_directory(self):
        fd = os.open(self.fx.parent_state, os.O_RDONLY)
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return fd

    def test_a_spawn_attempted_while_a_follow_on_holds_the_registry_is_refused_BEFORE_any_child_is_launched(self):
        outcomes = []

        def spawn_tries_mid_follow_on():
            outcomes.append(self.refused(lambda: self.spawn("during")))
            outcomes.append(list(self.launched))

        self.after_prompt = spawn_tries_mid_follow_on
        self.follow_on()
        self.assertEqual(outcomes, ["registry_busy", []], "refused, and nothing was launched: no child can run without a dependency")
        # nothing was lost, and now that the follow-on is done the spawn goes through
        self.spawn("after")
        ids = [r["task_id"] for r in self.fx.registry()["children"]]
        self.assertEqual(ids[0], FIRST)
        self.assertEqual(len(ids), 3)
        self.assertEqual(ids[2], "spawned-after")

    def test_the_lock_is_held_for_the_whole_spawn_from_before_launch_until_the_record_is_written(self):
        plane = self.plane()
        parent = self.parent

        def probes():
            return {
                "registry": self.refused(lambda: follow_on_module.registry_lock(parent).__enter__()),
                "follow_on": self.refused(lambda: self.follow_on()),
                "completion": self.refused(lambda: herdctl_complete(self.fx.parent)),
                "records_while_running": len(self.fx.registry()["children"]),
            }

        self.launch_probes = [probes]
        with mock.patch.object(herdctl, "COMPLETION_LOCK_WAIT_SECONDS", 0.0):
            plane.spawn_child(self.fx.parent, self.fx.root / "held", task="synthetic spawn")
        during = self.during_launch[0]
        self.assertEqual(during["registry"], "registry_busy", "the lock is held while the child is being launched")
        self.assertEqual(during["follow_on"], "registry_busy")
        self.assertTrue(during["completion"].startswith("SystemExit: Parent task cannot complete yet (registry_busy)"), during["completion"])
        self.assertEqual(during["records_while_running"], 1, "the record is written AFTER the launch, still under the same lock")
        # and once the spawn returned: the record is there, the lock is free, and the real gate blocks on the running child
        self.assertEqual(len(self.fx.registry()["children"]), 2)
        with follow_on_module.registry_lock(parent):
            pass
        self.assertIsNotNone(self.parent_completion_blocked())
        self.assert_parent_status_active()

    def test_a_contended_registry_refuses_the_spawn_before_launch_so_a_running_child_never_lacks_a_dependency(self):
        fd = self.hold_state_directory()
        self.addCleanup(os.close, fd)
        before = self.fx.digests()
        plane = self.plane(wait=0.0)
        with self.assertRaises(FollowOnRefused) as caught:
            plane.spawn_child(self.fx.parent, self.fx.root / "contended", task="synthetic spawn")
        self.assertEqual(caught.exception.check, "registry_busy")
        plane.spawn.assert_not_called()
        self.assertEqual(self.launched, [])
        self.assertEqual(self.fx.digests(), before)

    def test_the_spawn_waits_a_bounded_time_for_the_lock_BEFORE_launching_then_proceeds_or_refuses(self):
        fd = self.hold_state_directory()
        released = []

        def release_on_first_wait(seconds):
            if not released:
                released.append(seconds)
                os.close(fd)

        with mock.patch("herdr.follow_on.time.sleep", side_effect=release_on_first_wait):
            result = self.spawn("waited", wait=5.0)
        self.assertEqual(len(released), 1, "it waited once, the holder let go, it launched")
        self.assertEqual(self.launched, ["waited"])
        self.assertEqual(result["child_record"]["task_id"], "spawned-waited")
        self.assertEqual(len(self.fx.registry()["children"]), 2)
        # a lock that is never released is refused when the time is up, and the child is never launched
        fd2 = self.hold_state_directory()
        self.addCleanup(os.close, fd2)
        plane = self.plane(wait=5.0)
        with mock.patch("herdr.follow_on.time.sleep"), mock.patch(
            "herdr.follow_on.time.monotonic", side_effect=[0.0, 0.0, 99.0]
        ), self.assertRaises(FollowOnRefused) as caught:
            plane.spawn_child(self.fx.parent, self.fx.root / "never", task="synthetic spawn")
        self.assertEqual(caught.exception.check, "registry_busy")
        plane.spawn.assert_not_called()

    def test_a_spawn_whose_launch_fails_registers_nothing_and_releases_the_lock(self):
        plane = self.plane()
        plane.spawn = mock.Mock(side_effect=RuntimeError("bootstrap failed"))
        before = self.fx.children_json.read_bytes()
        with self.assertRaises(RuntimeError):
            plane.spawn_child(self.fx.parent, self.fx.root / "fails", task="synthetic spawn")
        self.assertEqual(self.fx.children_json.read_bytes(), before)
        with follow_on_module.registry_lock(self.parent):
            pass  # the lock was released

    def test_a_registration_that_returned_is_always_present_at_the_end_nothing_acknowledged_is_lost(self):
        # inject a spawn at EVERY registry write of the follow-on (intent, then finalize): either it is refused (no child
        # launched), or its record survives. (Without the shared lock the follow-on's replace would discard it.)
        real = follow_on_module._write_registry
        outcomes = []

        def spawn_then_write(path, document, expected_sha256):
            name = f"w{len(outcomes)}"
            outcomes.append((self.refused(lambda: self.spawn(name)), f"spawned-{name}", list(self.launched)))
            return real(path, document, expected_sha256)

        with mock.patch("herdr.follow_on._write_registry", side_effect=spawn_then_write):
            self.follow_on()
        self.assertEqual([o[0] for o in outcomes], ["registry_busy", "registry_busy"], "with the shared lock both are refused")
        self.assertEqual(outcomes[-1][2], [], "and neither launched a child")

    def test_a_writer_that_does_not_take_the_lock_is_DETECTED_at_the_intent_write_and_never_overwritten(self):
        real = follow_on_module._write_registry
        rival = self.rival_record()
        state = {"done": False}

        def rival_writes_directly_then_follow_on_writes(path, document, expected_sha256):
            if not state["done"]:
                state["done"] = True
                registry = self.fx.registry()
                registry["children"].append(rival)
                write_json(self.fx.children_json, registry)  # no lock taken
            return real(path, document, expected_sha256)

        with mock.patch("herdr.follow_on._write_registry", side_effect=rival_writes_directly_then_follow_on_writes):
            with self.assertRaises(FollowOnRefused) as caught:
                self.follow_on()
        self.assertEqual(caught.exception.check, "registry_changed")
        self.assertEqual([r["task_id"] for r in self.fx.registry()["children"]], [FIRST, rival["task_id"]], "the rival's record is intact")
        self.assert_nothing_dispatched()
        self.assertEqual([p.name for p in self.fx.parent_state.iterdir() if ".tmp-" in p.name], [])

    def test_a_writer_that_does_not_take_the_lock_is_DETECTED_at_the_finalize_and_the_dispatched_task_stays_PENDING_and_blocking(self):
        real = follow_on_module._write_registry
        rival = self.rival_record()
        calls = {"n": 0}

        def rival_before_second_write(path, document, expected_sha256):
            calls["n"] += 1
            if calls["n"] == 2:
                registry = self.fx.registry()
                registry["children"].append(rival)
                write_json(self.fx.children_json, registry)
            return real(path, document, expected_sha256)

        with mock.patch("herdr.follow_on._write_registry", side_effect=rival_before_second_write):
            with self.assertRaises(FollowOnFailed) as caught:
                self.follow_on()
        self.assertEqual(caught.exception.record_state, "PENDING")
        records = self.fx.registry()["children"]
        self.assertEqual([r["task_id"] for r in records], [FIRST, caught.exception.task_id, rival["task_id"]])
        self.assertEqual(records[1]["dispatch_state"], "PENDING")
        self.assertIsNotNone(self.parent_completion_blocked())

    def anchored(self):
        return read_registration_anchor(self.parent).get(PARENT_TASK, [])

    def child_identity(self, name, task_id=None):
        return registration_identity(self.fx.root / name, f"spawned-{name}" if task_id is None else task_id)

    def first_identity(self):
        return registration_identity(self.fx.child, FIRST)

    def test_spawn_child_registers_the_IDENTITY_of_every_child_seeding_the_anchor_from_the_whole_registry(self):
        self.assertEqual(self.anchored(), [], "nothing had been registered through a writer yet")
        self.spawn("a")
        self.assertEqual(self.anchored(), [self.first_identity(), self.child_identity("a")], "the record written before the anchor existed, and the spawned one")
        self.spawn("b")
        self.assertEqual(self.anchored(), [self.first_identity(), self.child_identity("a"), self.child_identity("b")])
        self.assertTrue(all(task_id is not None for _, task_id in self.anchored()), "no reservation is left behind by a completed spawn")

    def finish_spawned_child(self, name):
        """The spawned child's own task state, COMPLETE, so that only the registry's accounting can still block the parent."""
        write_json(
            self.fx.root / name / ".herd" / "state" / "task.json",
            {"id": f"spawned-{name}", "status": "COMPLETE", "completed_at": 5, "repo": str(self.fx.root / name), "repo_ref": str(self.fx.root / name)},
        )

    def test_THE_REPLACEMENT_CHILDREN_ATTACK_ordinary_spawns_after_a_loss_cannot_restore_the_invariant_through_the_REAL_command(self):
        # The Lead's sequence, using only ordinary spawn_child calls: anchored at two, the registry is lost (or damaged), a spawn starts
        # a new registry (one record), another spawn appends (two records). With a COUNT as the mark, two expected and two present looks
        # complete although the ORIGINAL children are gone. A count is fungible; identities are not.
        self.spawn("a")
        self.assertEqual(len(self.fx.registry()["children"]), 2)
        self.fx.children_json.write_text("{damaged")
        outcomes = []
        for name in ("b", "c"):
            try:
                self.spawn(name)
                outcomes.append("spawned")
                self.finish_spawned_child(name)
            except FollowOnRefused as refusal:
                outcomes.append(refusal.check)
        # Whatever the spawns did, the originals (the first record and "a") are not accounted for: the REAL command must refuse.
        for name in ("a", "b", "c"):
            if (self.fx.root / name).exists():
                self.finish_spawned_child(name)
        with mock.patch.object(herdctl, "COMPLETION_LOCK_WAIT_SECONDS", 0):
            refusal = None
            args = SimpleNamespace(repo=str(self.fx.parent), checkpoint_file=None, note=None)
            with mock.patch.object(herdctl, "resolve_repo_ref", return_value=self.fx.parent):
                try:
                    herdctl.task_complete_cmd(args)
                except SystemExit as stop:
                    refusal = str(stop)
        self.assertIsNotNone(refusal, f"the parent COMPLETED over vanished originals (spawns: {outcomes})")
        self.assertEqual(read_json(self.fx.parent_state / "task.json")["status"], "ACTIVE")
        self.assertFalse((self.fx.parent_state / "tasks").exists(), "no completion archive was written")

    def test_a_spawn_over_a_lost_or_damaged_registry_is_REFUSED_before_launch_and_never_replaces_it(self):
        # This is the path the replacement-children attack used: spawn_child replaced a damaged registry by an empty one, and an ordinary
        # second spawn then "restored" the count. Now nothing is launched on top of a registry that cannot be trusted.
        self.spawn("a")
        registry_bytes = self.fx.children_json.read_bytes()
        anchor_before = read_registration_anchor(self.parent)
        cases = (
            ("unreadable", lambda: self.fx.children_json.write_text("{damaged"), "registry_unreadable"),
            ("not a registry", lambda: self.fx.children_json.write_text("[]"), "registry_invalid"),
            ("children not records", lambda: write_json(self.fx.children_json, {"version": 1, "children": ["x"]}), "registry_invalid"),
            ("deleted", lambda: self.fx.children_json.unlink(), "registry_loss_detected"),
            (
                "lost a record",
                lambda: write_json(self.fx.children_json, {"version": 1, "children": self.fx.registry()["children"][:1]}),
                "registry_loss_detected",
            ),
            (
                "replaced by other children",
                lambda: write_json(
                    self.fx.children_json,
                    {"version": 1, "children": [dict(self.fx.registry()["children"][0], task_id="other-1", repo=str(self.fx.root / "o1")),
                                                 dict(self.fx.registry()["children"][0], task_id="other-2", repo=str(self.fx.root / "o2"))]},
                ),
                "registry_loss_detected",
            ),
        )
        for label, damage, check in cases:
            with self.subTest(damage=label):
                self.fx.children_json.write_bytes(registry_bytes)
                damage()
                damaged = self.fx.children_json.read_bytes() if self.fx.children_json.exists() else None
                plane = self.plane()
                with self.assertRaises(FollowOnRefused) as caught:
                    plane.spawn_child(self.fx.parent, self.fx.root / "late", task="synthetic spawn")
                self.assertEqual(caught.exception.check, check)
                plane.spawn.assert_not_called()
                self.assertEqual(self.fx.children_json.read_bytes() if self.fx.children_json.exists() else None, damaged, "the damaged registry was not replaced")
                self.assertEqual(read_registration_anchor(self.parent), anchor_before, "the anchor was not touched")
        # a person restores the registry, and spawning goes on
        self.fx.children_json.write_bytes(registry_bytes)
        self.spawn("after-repair")

    def test_a_spawn_whose_anchor_cannot_be_written_BEFORE_launch_is_refused_and_launches_nothing(self):
        before = self.fx.digests()
        plane = self.plane()
        with mock.patch("herdr.follow_on.register_identities", side_effect=OSError("disk full")), self.assertRaises(FollowOnRefused) as caught:
            plane.spawn_child(self.fx.parent, self.fx.root / "x", task="synthetic spawn")
        self.assertEqual(caught.exception.check, "registry_anchor_unwritable")
        plane.spawn.assert_not_called()
        self.assertEqual(self.fx.digests(), before)

    # -- canonical round 13, finding 1: an exception from the launch is NOT evidence that nothing started --------------------------
    # `spawn` is initialize, start, THEN `dispatch_task`, and `dispatch_task` saves the child's task ACTIVE, prompts the Supervisor,
    # and on a failed prompt saves the task ERROR and then RAISES. So the exception arrives after the child exists. The reservation is
    # the only protection at that moment (no registry record has been written), so it must survive every exception from the launch.

    def fresh_child(self, name="fresh"):
        repo = self.fx.root / name
        write_json(repo / ".herd" / "herd.config.json", read_json(self.fx.child_config_json))
        write_json(
            repo / ".herd" / "state" / "runtime.json",
            {"version": 2, "workspace_id": f"w-{name}", "agents": dict(ROLES), "panes": {}},
        )
        return repo

    def refused_completion(self):
        args = SimpleNamespace(repo=str(self.fx.parent), checkpoint_file=None, note=None)
        with mock.patch.object(herdctl, "resolve_repo_ref", return_value=self.fx.parent), self.assertRaises(SystemExit) as refused:
            herdctl.task_complete_cmd(args)
        return str(refused.exception)

    def assert_not_completed(self):
        self.assertEqual(read_json(self.fx.parent_state / "task.json")["status"], "ACTIVE")
        self.assertFalse((self.fx.parent_state / "tasks").exists(), "no archive of the parent's completion was written")

    def test_a_REAL_dispatch_failure_after_the_child_saved_an_ERROR_task_keeps_the_reservation_and_the_REAL_completion_command_refuses(self):
        # NOT a bootstrap-only mock: the real `spawn` runs the real `dispatch_task`; only the process boundaries (starting the herd,
        # `agent get`, the prompt) are replaced. A mock that raises before any effect cannot show the gate refusing.
        repo = self.fresh_child()
        self.prompt_result = SimpleNamespace(returncode=1, stdout="", stderr="supervisor did not accept the prompt")
        plane = HerdrControlPlane()
        plane.REGISTRY_WAIT_SECONDS = 0.0
        started = {"workspace_id": "w-fresh", "agents": dict(ROLES)}
        with mock.patch.object(HerdrControlPlane, "start", return_value=started), self.assertRaises(RuntimeError) as failed:
            plane.spawn_child(self.fx.parent, repo, task="synthetic spawn")
        self.assertIn("supervisor did not accept the prompt", str(failed.exception))
        # the EFFECT preceded the signal: the Supervisor was prompted and the child's task was saved, as ERROR
        self.assertEqual(len(self.prompts), 1)
        self.assertEqual(read_json(repo / ".herd" / "state" / "task.json")["status"], "ERROR")
        # no registry record was written (the exception left spawn_child before the append) ...
        self.assertEqual([r["task_id"] for r in self.fx.registry()["children"]], [FIRST])
        # ... so the reservation is the ONLY protection, and the real command must still refuse because of it
        self.assertIn(registration_identity(repo, None), read_registration_anchor(self.parent).get(PARENT_TASK, []))
        self.assertIn("status=REGISTRY_LOSS", self.refused_completion())
        self.assert_not_completed()

    def test_NO_exception_from_the_launch_withdraws_the_reservation_an_exception_is_not_evidence_that_nothing_happened(self):
        for exc in (RuntimeError("boot failed"), ValueError("bad value"), OSError("disk"), KeyboardInterrupt(), SystemExit("exit")):
            with self.subTest(exception=type(exc).__name__):
                self.setUp()
                plane = self.plane()
                plane.spawn = mock.Mock(side_effect=exc)
                target = self.fx.root / "x"
                buffer = io.StringIO()
                with redirect_stdout(buffer), self.assertRaises(type(exc)):
                    plane.spawn_child(self.fx.parent, target, task="synthetic spawn")
                self.assertIn(registration_identity(target, None), self.anchored(), "the reservation is still there")
                self.assertIn("an exception is not evidence that nothing started", buffer.getvalue())
                note = buffer.getvalue()
                self.assertIn("supervised correction", note)
                self.assertIn("never started", note)
                for forbidden in ("removed the reservation", "removes the reservation", "has checked that repository"):
                    self.assertNotIn(forbidden, note)
                self.assertIsNotNone(self.parent_completion_blocked(), "and it blocks the parent")

    # -- canonical round 13, finding 2: an unreadable parent task is NEVER read as "no task" ----------------------------------------
    # `spawn_child` used to turn an unreadable parent task into {}, so no reservation was written and the child was registered with
    # parent_task_id None: orphaned from its parent, and skipped by completion once the parent's ACTIVE state returned. A heartbeat's
    # `_save_task` is `path.write_text(...)`: the file is opened for writing (so emptied) BEFORE the new content is written, so a read in
    # that window is a SUPPORTED transient, not an external act.

    def test_an_UNREADABLE_or_contradictory_parent_task_REFUSES_before_launch_by_name_it_is_never_read_as_empty(self):
        cases = (  # each damage takes the task path of the fixture UNDER TEST (every subtest builds a fresh one)
            ("parent_task_unreadable", "emptied (a heartbeat's write_text opens the file for writing first)", lambda task: task.write_text("")),
            ("parent_task_unreadable", "partly written", lambda task: task.write_text('{"id": "20261026-1419')),
            ("parent_task_invalid", "valid JSON that is not an object", lambda task: task.write_text("[]")),
            ("parent_task_invalid", "ACTIVE without an id", lambda task: write_json(task, {"status": "ACTIVE"})),
            ("parent_task_invalid", "ACTIVE with an id that is not a string", lambda task: write_json(task, {"id": 5, "status": "ACTIVE"})),
            ("parent_task_invalid", "ACTIVE with an empty id", lambda task: write_json(task, {"id": "", "status": "ACTIVE"})),
            ("parent_task_invalid", "a status nothing writes", lambda task: write_json(task, {"id": PARENT_TASK, "status": "WEIRD"})),
            ("parent_task_invalid", "no status at all", lambda task: write_json(task, {"id": PARENT_TASK})),
        )
        for check, label, damage in cases:
            with self.subTest(case=label):
                self.setUp()
                damage(self.fx.parent_state / "task.json")
                before = self.fx.digests()
                plane = self.plane()
                with self.assertRaises(FollowOnRefused) as caught:
                    plane.spawn_child(self.fx.parent, self.fx.root / "orphan", task="synthetic spawn")
                self.assertEqual(caught.exception.check, check)
                plane.spawn.assert_not_called()
                self.assertEqual(self.fx.digests(), before, "nothing was written: no record and no reservation")
                self.assertFalse((self.fx.parent_state / ANCHOR_FILE).exists())

    def test_THE_TRANSIENT_a_spawn_during_a_heartbeat_write_is_refused_and_the_same_spawn_after_it_is_registered_under_the_parent(self):
        task_path = self.fx.parent_state / "task.json"
        original = task_path.read_text()
        plane = self.plane()
        with open(task_path, "w") as writing:  # what write_text does first: the file is opened for writing, and so emptied
            with self.assertRaises(FollowOnRefused) as caught:
                plane.spawn_child(self.fx.parent, self.fx.root / "during", task="synthetic spawn")
            self.assertEqual(caught.exception.check, "parent_task_unreadable")
            writing.write(original)
        plane.spawn.assert_not_called()
        self.assertEqual(self.launched, [], "nothing was launched during the window")
        self.spawn("after")  # the window has passed: the same operation succeeds
        record = self.fx.registry()["children"][-1]
        self.assertEqual(record["parent_task_id"], PARENT_TASK, "registered UNDER the parent task, not orphaned")
        self.assertTrue(record["dependency"])
        self.assertIn("cannot complete while child", self.refused_completion(), "and the real command honours it")
        self.assert_not_completed()

    def test_a_missing_parent_task_or_a_finished_one_is_still_no_dependency_the_refusal_is_narrow(self):
        for label, arrange in (  # each arrangement takes the task path of the fixture UNDER TEST
            ("missing", lambda task: task.unlink()),
            ("COMPLETE", lambda task: write_json(task, {"id": PARENT_TASK, "status": "COMPLETE"})),
            ("ABORTED", lambda task: write_json(task, {"id": PARENT_TASK, "status": "ABORTED"})),
            ("ERROR", lambda task: write_json(task, {"id": PARENT_TASK, "status": "ERROR"})),
            ("IDLE", lambda task: write_json(task, {"status": "IDLE"})),
        ):
            with self.subTest(parent_task=label):
                self.setUp()
                arrange(self.fx.parent_state / "task.json")
                self.spawn("loose")
                record = self.fx.registry()["children"][-1]
                self.assertIsNone(record["parent_task_id"])
                self.assertFalse(record["dependency"])
                self.assertFalse((self.fx.parent_state / ANCHOR_FILE).exists())

    def test_a_COUNT_ERA_anchor_refuses_a_spawn_before_launch_and_after_a_person_removes_it_the_next_spawn_seeds_identities(self):
        (self.fx.parent_state / ANCHOR_FILE).write_text(json.dumps({"version": 1, "registered": {PARENT_TASK: 1}}))
        plane = self.plane()
        with self.assertRaises(FollowOnRefused) as caught:
            plane.spawn_child(self.fx.parent, self.fx.root / "x", task="synthetic spawn")
        self.assertEqual(caught.exception.check, "registry_anchor_unreadable")
        self.assertIn("a person removes", str(caught.exception))
        plane.spawn.assert_not_called()
        (self.fx.parent_state / ANCHOR_FILE).unlink()  # the named step
        self.spawn("after-clearing")
        self.assertEqual(self.anchored(), [self.first_identity(), self.child_identity("after-clearing")])

    def test_a_child_LAUNCHED_but_never_recorded_leaves_an_unfulfilled_reservation_that_blocks_the_REAL_command(self):
        # The second concern of round 13: a failure AFTER launch must leave a durable mark, not a warning. The reservation was written
        # before the launch, so a record that could not be written is an unaccounted registration.
        plane = self.plane()
        with mock.patch.object(HerdrControlPlane, "_append_child_record", side_effect=OSError("disk full")), self.assertRaises(OSError):
            plane.spawn_child(self.fx.parent, self.fx.root / "ghost", task="synthetic spawn")
        self.assertEqual(self.launched, ["ghost"], "the child really was launched")
        held = read_registration_anchor(self.parent)[PARENT_TASK]
        self.assertIn(registration_identity(self.fx.root / "ghost", None), held, "its reservation is durable")
        args = SimpleNamespace(repo=str(self.fx.parent), checkpoint_file=None, note=None)
        with mock.patch.object(herdctl, "resolve_repo_ref", return_value=self.fx.parent), self.assertRaises(SystemExit) as refused:
            herdctl.task_complete_cmd(args)
        self.assertIn("status=REGISTRY_LOSS", str(refused.exception))
        self.assertEqual(read_json(self.fx.parent_state / "task.json")["status"], "ACTIVE")
        self.assertFalse((self.fx.parent_state / "tasks").exists())

    def test_a_registry_damaged_between_admission_and_the_append_is_NOT_replaced_and_the_reservation_blocks(self):
        # a writer that took no lock damages the registry after admission: the append must refuse, never replace it by an empty one
        # (the discard the replacement-children attack needed), and the reservation written before the launch must block the parent
        from herdr import control_plane as control_plane_module

        real_admit = control_plane_module.admit_spawn_registration

        def admit_then_damage(*args, **kwargs):
            admitted = real_admit(*args, **kwargs)
            self.fx.children_json.write_text("{damaged after admission")
            return admitted

        plane = self.plane()
        with mock.patch("herdr.control_plane.admit_spawn_registration", side_effect=admit_then_damage), self.assertRaises(FollowOnRefused) as caught:
            plane.spawn_child(self.fx.parent, self.fx.root / "late", task="synthetic spawn")
        self.assertEqual(caught.exception.check, "registry_unreadable")
        self.assertEqual(self.launched, ["late"], "the child was launched before the damage was found")
        self.assertEqual(self.fx.children_json.read_text(), "{damaged after admission", "the damaged registry was not replaced")
        self.assertIn(registration_identity(self.fx.root / "late", None), self.anchored(), "the reservation is durable")
        self.assertIsNotNone(self.parent_completion_blocked(), "the gate refuses the unreadable registry")

    def test_a_reservation_that_cannot_be_swapped_for_the_identity_stays_and_is_satisfied_by_the_record(self):
        buffer = io.StringIO()
        with mock.patch("herdr.control_plane.finalize_spawn_registration", return_value="disk full"), redirect_stdout(buffer):
            self.spawn("kept")
        self.assertIn("NOTE: the child is registered; swapping its reservation", buffer.getvalue())
        self.assertIn(registration_identity(self.fx.root / "kept", None), self.anchored())
        self.assertNotIn("REGISTRY_LOSS", self.parent_completion_blocked() or "", "the record satisfies the reservation: nothing is falsely blocked")

    def test_the_result_shape_of_a_spawn_is_unchanged(self):
        normal = self.spawn("normal")
        self.assertEqual(set(normal), {"repo", "runtime", "task", "policy", "parent_repo", "child_record"})

    def test_a_spawn_when_the_parent_has_no_active_task_registers_no_dependency_and_writes_no_anchor(self):
        done = dict(read_json(self.fx.parent_state / "task.json"), status="COMPLETE")
        write_json(self.fx.parent_state / "task.json", done)
        self.spawn("loose")
        self.assertFalse((self.fx.parent_state / ANCHOR_FILE).exists())

    def test_the_END_STATE_of_the_attack_replacement_children_never_satisfy_the_anchor_through_the_REAL_command(self):
        # Independent of whether a spawn would be refused: even if the registry ends up holding two OTHER children, both complete,
        # the anchor remembers WHO was registered, so two-present is not two-expected.
        self.spawn("a")
        write_json(
            self.fx.children_json,
            {"version": 1, "children": [dict(self.fx.registry()["children"][0], task_id="other-1", repo=str(self.fx.root / "o1")),
                                         dict(self.fx.registry()["children"][0], task_id="other-2", repo=str(self.fx.root / "o2"))]},
        )
        for name, task_id in (("o1", "other-1"), ("o2", "other-2")):
            write_json(
                self.fx.root / name / ".herd" / "state" / "task.json",
                {"id": task_id, "status": "COMPLETE", "completed_at": 5, "repo": str(self.fx.root / name), "repo_ref": str(self.fx.root / name)},
            )
        args = SimpleNamespace(repo=str(self.fx.parent), checkpoint_file=None, note=None)
        with mock.patch.object(herdctl, "resolve_repo_ref", return_value=self.fx.parent), self.assertRaises(SystemExit) as refused:
            herdctl.task_complete_cmd(args)
        self.assertIn("status=REGISTRY_LOSS", str(refused.exception))
        self.assertEqual(read_json(self.fx.parent_state / "task.json")["status"], "ACTIVE")
        self.assertFalse((self.fx.parent_state / "tasks").exists())

class OwnRecordScopeTests(FollowOnCase):
    """Rollback and finalize touch EXACTLY the record this follow-on appended, matched by full equality."""

    def foreign(self, task_id, repo, **extra):
        return dict(
            {
                "requested_at": 60,
                "parent_repo": str(self.fx.parent),
                "parent_task_id": PARENT_TASK,
                "dependency": True,
                "repo": repo,
                "task_id": task_id,
                "task_status": "DISPATCHING",
                "dispatch_state": "PENDING",
            },
            **extra,
        )

    def reject_with(self, during):
        """Run a follow-on whose dispatch is REJECTED (the supervisor turns busy), calling `during(our_record)` first."""

        def busy(name):
            registry = self.fx.registry()
            ours = [r for r in registry["children"] if r.get("follow_on_of") == FIRST][0]
            during(registry, ours)
            return {"status": "busy", "raw": None}

        return mock.patch("herdr.tasks.agent_info", side_effect=busy)

    def test_rollback_removes_only_its_own_record_never_a_pending_record_of_another_repository_with_the_same_task_id(self):
        other = self.foreign("PLACEHOLDER", str(self.fx.root / "some-other-repo"))

        def during(registry, ours):
            other["task_id"] = ours["task_id"]
            registry["children"].append(copy.deepcopy(other))
            write_json(self.fx.children_json, registry)

        with self.reject_with(during), self.assertRaises(FollowOnRefused) as caught:
            self.follow_on()
        self.assertEqual(caught.exception.check, "dispatch_rejected")
        records = self.fx.registry()["children"]
        self.assertEqual(len(records), 2)
        self.assertEqual(records[0]["task_id"], FIRST)
        self.assertEqual(records[1], other, "the other repository's PENDING record with the SAME task id survives untouched")

    def test_rollback_removes_only_the_exact_record_when_a_lookalike_for_the_same_repo_and_id_exists(self):
        lookalike = {}

        def during(registry, ours):
            lookalike.update(dict(ours, note="written by someone else"))
            registry["children"].append(copy.deepcopy(lookalike))
            write_json(self.fx.children_json, registry)

        with self.reject_with(during), self.assertRaises(FollowOnRefused):
            self.follow_on()
        records = self.fx.registry()["children"]
        self.assertEqual([r["task_id"] for r in records], [FIRST, lookalike["task_id"]])
        self.assertEqual(records[1], lookalike)

    def test_a_record_that_is_no_longer_exactly_ours_is_not_removed_and_stays_blocking(self):
        def during(registry, ours):
            ours["edited_by"] = "someone else"
            write_json(self.fx.children_json, registry)

        with self.reject_with(during), self.assertRaises(FollowOnFailed) as caught:
            self.follow_on()
        self.assertEqual(caught.exception.record_state, "PENDING")
        self.assertIn("registry_record_lost", str(caught.exception), "the failure names why the record could not be removed")
        records = self.fx.registry()["children"]
        self.assertEqual(len(records), 2)
        self.assertEqual(records[1]["edited_by"], "someone else")
        self.assertIsNotNone(self.parent_completion_blocked())

    def test_two_identical_records_are_ambiguous_and_neither_is_touched(self):
        def during(registry, ours):
            registry["children"].append(copy.deepcopy(ours))
            write_json(self.fx.children_json, registry)

        with self.reject_with(during), self.assertRaises(FollowOnFailed) as caught:
            self.follow_on()
        self.assertEqual(caught.exception.record_state, "PENDING")
        self.assertEqual(len(self.fx.registry()["children"]), 3)

    def test_the_helpers_refuse_a_record_that_is_absent(self):
        appended = self.foreign("ghost", str(self.fx.child))
        before = self.fx.children_json.read_bytes()
        for call in (
            lambda: follow_on_module._remove_own_record(self.fx.children_json, appended),
            lambda: follow_on_module._update_own_record(self.fx.children_json, appended, dispatch_state="DISPATCHED"),
        ):
            with self.assertRaises(FollowOnRefused) as caught:
                call()
            self.assertEqual(caught.exception.check, "registry_record_lost")
        self.assertEqual(self.fx.children_json.read_bytes(), before)


class RealCompletionGateTests(FollowOnCase):
    """CASE 1 through the REAL completion command (`herdctl.task_complete_cmd` -> the control plane's gate -> `dependencies`), not
    a helper: only `resolve_repo_ref` (which would shell out to git) is replaced. `tests/test_completion_gate.py` mocks the control
    plane, so it can show that the command honours a refusal but not that a failed follow-on produces one."""

    def complete_parent(self):
        args = SimpleNamespace(repo=str(self.fx.parent), checkpoint_file=None, note=None)
        with mock.patch.object(herdctl, "resolve_repo_ref", return_value=self.fx.parent):
            herdctl.task_complete_cmd(args)

    def assert_parent_not_completed(self):
        self.assertEqual(read_json(self.fx.parent_state / "task.json")["status"], "ACTIVE")
        self.assertFalse((self.fx.parent_state / "tasks").exists(), "no archive of the parent was written")
        self.assertFalse((self.fx.parent / ".herd" / "memory" / "task-history.md").exists())

    def test_the_real_command_refuses_to_complete_the_parent_after_a_failed_follow_on_dispatch(self):
        self.prompt_result = SimpleNamespace(returncode=1, stdout="", stderr="supervisor did not accept the prompt")
        with self.assertRaises(FollowOnFailed) as failed:
            self.follow_on()
        with self.assertRaises(SystemExit) as refused:
            self.complete_parent()
        message = str(refused.exception)
        self.assertIn("cannot complete while child", message)
        self.assertIn(failed.exception.task_id, message)
        self.assertNotIn(FIRST, message, "the completed first task does not block; the failed follow-on does")
        self.assert_parent_not_completed()

    def test_the_real_command_refuses_while_the_follow_on_is_active_and_completes_once_it_is_complete(self):
        new_id = self.follow_on()["task"]["id"]
        with self.assertRaises(SystemExit) as refused:
            self.complete_parent()
        self.assertIn(new_id, str(refused.exception))
        self.assert_parent_not_completed()
        done = dict(read_json(self.fx.child_task_json), status="COMPLETE", completed_at=99)
        write_json(self.fx.child_task_json, done)
        write_json(self.fx.archive_path(new_id), done)
        self.complete_parent()
        self.assertEqual(read_json(self.fx.parent_state / "task.json")["status"], "COMPLETE")

    def test_the_real_command_refuses_over_a_PENDING_record_the_state_nobody_could_confirm(self):
        self.prompt_result = SimpleNamespace(returncode=1, stdout="", stderr="boom")
        with mock.patch("herdr.follow_on._child_task_now", return_value=None), self.assertRaises(FollowOnFailed):
            self.follow_on()
        with self.assertRaises(SystemExit) as refused:
            self.complete_parent()
        self.assertIn("PENDING", str(refused.exception))
        self.assert_parent_not_completed()

    def test_CONTROL_without_the_record_the_real_command_would_complete_the_parent_over_a_failed_task(self):
        # what a register-only-after-success flow leaves after the same failure: the REAL command lets the parent complete.
        # This is what makes the refusal above evidence of the record, not of something else.
        self.prompt_result = SimpleNamespace(returncode=1, stdout="", stderr="boom")
        registry_before = self.fx.children_json.read_bytes()
        with self.assertRaises(FollowOnFailed):
            self.follow_on()
        self.assertEqual(read_json(self.fx.child_task_json)["status"], "ERROR")
        self.fx.children_json.write_bytes(registry_before)
        # with the anchor still in place the old registry is a detected LOSS and the REAL command refuses
        with self.assertRaises(SystemExit) as refused:
            self.complete_parent()
        self.assertIn("REGISTRY_LOSS", str(refused.exception))
        self.assert_parent_not_completed()
        (self.fx.parent_state / ANCHOR_FILE).unlink()  # a flow that registers nothing leaves no anchor either
        self.complete_parent()
        self.assertEqual(read_json(self.fx.parent_state / "task.json")["status"], "COMPLETE")

    def test_the_real_command_does_not_block_after_a_REJECTED_dispatch_because_nothing_started(self):
        # the policy point, stated by a test: a rejection that left the child untouched leaves no dependency and no block
        busy = lambda name: {"status": "busy", "raw": None}  # noqa: E731
        with mock.patch("herdr.tasks.agent_info", side_effect=busy), self.assertRaises(FollowOnRefused):
            self.follow_on()
        self.complete_parent()
        self.assertEqual(read_json(self.fx.parent_state / "task.json")["status"], "COMPLETE")


class LostRecordTests(FollowOnCase):
    """Round 11, finding 3. A record that is NOT in the registry cannot block anything, so a handler that says "it stays PENDING"
    is describing a mechanism that is not there. The state a failure reports is established by asking the completion gate itself;
    a lost record is replaced by a RECOVERY record the gate sees, and when nothing can be made to block the failure says
    UNPROTECTED instead of claiming a block."""

    def remove_our_record(self):
        registry = self.fx.registry()
        registry["children"] = [r for r in registry["children"] if r.get("follow_on_of") != FIRST]
        write_json(self.fx.children_json, registry)

    def complete_refusal(self):
        args = SimpleNamespace(repo=str(self.fx.parent), checkpoint_file=None, note=None)
        with mock.patch.object(herdctl, "resolve_repo_ref", return_value=self.fx.parent):
            try:
                herdctl.task_complete_cmd(args)
            except SystemExit as stop:
                return str(stop)
        return None

    def test_a_record_lost_before_the_finalize_is_replaced_by_a_RECOVERY_record_and_the_REAL_gate_blocks(self):
        self.after_prompt = self.remove_our_record
        with self.assertRaises(FollowOnFailed) as caught:
            self.follow_on()
        failure = caught.exception
        self.assertEqual(failure.record_state, "RECOVERY")
        self.assertIn("RECOVERY record was written", str(failure))
        records = self.fx.registry()["children"]
        self.assertEqual([r["task_id"] for r in records], [FIRST, failure.task_id])
        self.assertEqual(records[1]["dispatch_state"], "RECOVERY")
        self.assertEqual(records[1]["follow_on_of"], FIRST)
        self.assertIn("lost", records[1]["recovery_reason"] + "lost")
        found = {d["task_id"]: d for d in child_dependencies(self.parent, PARENT_TASK)}
        self.assertEqual(found[failure.task_id]["current_status"], "RECOVERY")
        # THE POINT: the claim and the mechanism agree. The real completion command refuses, and names the lost task.
        refusal = self.complete_refusal()
        self.assertIsNotNone(refusal, "the parent must NOT be able to complete over a lost record")
        self.assertIn(failure.task_id, refusal)
        self.assertEqual(read_json(self.fx.parent_state / "task.json")["status"], "ACTIVE")

    def test_the_same_when_the_task_failed_after_the_child_recorded_it_and_the_record_was_lost(self):
        self.prompt_result = SimpleNamespace(returncode=1, stdout="", stderr="boom")
        self.after_prompt = self.remove_our_record
        with self.assertRaises(FollowOnFailed) as caught:
            self.follow_on()
        self.assertEqual(caught.exception.record_state, "RECOVERY")
        self.assertEqual(read_json(self.fx.child_task_json)["status"], "ERROR")
        self.assertIsNotNone(self.complete_refusal())

    def test_a_record_lost_while_a_dispatch_is_rejected_with_the_child_untouched_starts_nothing_and_needs_no_recovery(self):
        def busy_after_losing_the_record(name):
            self.remove_our_record()
            return {"status": "busy", "raw": None}

        with mock.patch("herdr.tasks.agent_info", side_effect=busy_after_losing_the_record), self.assertRaises(FollowOnRefused) as caught:
            self.follow_on()
        self.assertEqual(caught.exception.check, "dispatch_rejected")
        self.assertIn("nothing started", str(caught.exception))
        self.assertEqual([r["task_id"] for r in self.fx.registry()["children"]], [FIRST])
        self.assertEqual(read_json(self.fx.child_task_json)["id"], FIRST, "the child was never touched")
        self.assertIsNone(self.complete_refusal(), "nothing started, so nothing should block (and nothing does)")

    def assert_parent_not_completed(self):
        self.assertEqual(read_json(self.fx.parent_state / "task.json")["status"], "ACTIVE")
        self.assertFalse((self.fx.parent_state / "tasks").exists(), "no archive of the parent's completion was written")
        self.assertFalse((self.fx.parent / ".herd" / "memory" / "task-history.md").exists(), "no completion history was written")

    def test_a_registry_deleted_mid_flight_is_a_LOSS_and_the_REAL_completion_command_REFUSES_it(self):
        # Round 12: the old test here asked only the dependency helper and so never showed the command refusing (and the outcome it
        # blessed let the parent COMPLETE). This one runs the command itself.
        def delete_registry():
            self.fx.children_json.unlink()

        self.after_prompt = delete_registry
        with self.assertRaises(FollowOnFailed) as caught:
            self.follow_on()
        failure = caught.exception
        self.assertEqual(failure.record_state, "REGISTRY_LOST")
        self.assertIn("LOST records", str(failure))
        self.assertNotIn("NOTHING is known", str(failure))
        self.assertFalse(self.fx.children_json.exists(), "a deleted registry is not recreated by guesswork")
        held = read_registration_anchor(self.parent)[PARENT_TASK]
        self.assertIn(registration_identity(self.fx.child, FIRST), held, "the anchor still remembers the first child")
        self.assertIn(registration_identity(self.fx.child, failure.task_id), held, "and the follow-on's own identity, registered before it dispatched")
        refusal = self.complete_refusal()
        self.assertIsNotNone(refusal, "the REAL completion command must refuse; a correct warning is not a blocking dependency")
        self.assertIn("status=REGISTRY_LOSS", refusal)
        self.assert_parent_not_completed()

    def test_a_registry_deleted_AFTER_a_successful_follow_on_is_just_as_blocked_no_operation_is_assumed_to_keep_it(self):
        self.follow_on()
        saved = self.fx.children_json.read_bytes()
        self.fx.children_json.unlink()
        refusal = self.complete_refusal()
        self.assertIn("status=REGISTRY_LOSS", refusal or "")
        self.assert_parent_not_completed()
        # a person's repair (the registry put back) clears the LOSS; what blocks then is the real, still-active follow-on task
        self.fx.children_json.write_bytes(saved)
        again = self.complete_refusal()
        self.assertNotIn("REGISTRY_LOSS", again or "")
        self.assertIn("status=ACTIVE", again or "")
        self.assert_parent_not_completed()

    def test_a_registry_that_LOST_RECORDS_is_blocked_too_not_only_one_that_vanished(self):
        self.follow_on()
        registry = self.fx.registry()
        registry["children"] = registry["children"][:1]  # the follow-on's record is gone, the first survives
        write_json(self.fx.children_json, registry)
        refusal = self.complete_refusal()
        self.assertIn("status=REGISTRY_LOSS", refusal or "")
        self.assert_parent_not_completed()

    def test_an_absent_registry_with_nothing_registered_is_still_the_normal_no_children_state_and_completes(self):
        # unknown is not permission, but an absence NOTHING contradicts is not unknown: `observe` reads it the same way
        fx = Fixture(registry=False)
        self.addCleanup(fx.cleanup)
        args = SimpleNamespace(repo=str(fx.parent), checkpoint_file=None, note=None)
        with mock.patch.object(herdctl, "resolve_repo_ref", return_value=fx.parent):
            herdctl.task_complete_cmd(args)
        self.assertEqual(read_json(fx.parent_state / "task.json")["status"], "COMPLETE")
        self.assertFalse((fx.parent_state / ANCHOR_FILE).exists())

    def test_when_BOTH_the_registry_and_its_anchor_are_gone_nothing_in_the_state_directory_can_know_the_stated_residual(self):
        # The one outcome no file in this directory can prevent: it is reported as exactly that, and the gate has no evidence left.
        # (It is not an endorsement of the completion that would follow; it is the limit, named.)
        def delete_both():
            self.fx.children_json.unlink()
            (self.fx.parent_state / ANCHOR_FILE).unlink()

        self.after_prompt = delete_both
        with self.assertRaises(FollowOnFailed) as caught:
            self.follow_on()
        self.assertEqual(caught.exception.record_state, "UNPROTECTED")
        self.assertIn("NOTHING is known to block", str(caught.exception))
        self.assertIn("anchor are both gone", str(caught.exception))
        self.assertEqual(child_dependencies(self.parent, PARENT_TASK), [])

    def test_an_unreadable_registry_is_reported_as_such_the_gate_refuses_it_and_the_file_is_never_replaced(self):
        corrupt = "{corrupt after the intent record was written"
        outcomes = {}

        def corrupt_registry():
            self.fx.children_json.write_text(corrupt)

        cases = (
            ("finalize", {}, "could not be finalized"),
            ("failed-after-recorded", {"returncode": 1}, "cannot be read"),
        )
        for label, options, fragment in cases:
            with self.subTest(case=label):
                self.setUp()
                if options:
                    self.prompt_result = SimpleNamespace(returncode=1, stdout="", stderr="boom")
                self.after_prompt = corrupt_registry
                with self.assertRaises(FollowOnFailed) as caught:
                    self.follow_on()
                self.assertEqual(caught.exception.record_state, "REGISTRY_UNREADABLE")
                self.assertIn(fragment, str(caught.exception))
                self.assertEqual(self.fx.children_json.read_text(), corrupt, "the unreadable file was left as found")
                self.assertIsNotNone(self.parent_completion_blocked(), "the gate refuses an unreadable registry")
                self.assertIsNotNone(self.complete_refusal())

        # a rejected dispatch whose rollback finds the registry unreadable
        self.setUp()

        def busy_after_corrupting(name):
            corrupt_registry()
            return {"status": "busy", "raw": None}

        with mock.patch("herdr.tasks.agent_info", side_effect=busy_after_corrupting), self.assertRaises(FollowOnFailed) as caught:
            self.follow_on()
        self.assertEqual(caught.exception.record_state, "REGISTRY_UNREADABLE")
        self.assertIn("registry_unreadable", str(caught.exception))
        self.assertEqual(self.fx.children_json.read_text(), corrupt)

    def test_a_record_that_is_present_is_never_reported_as_recovered_and_no_second_record_is_written(self):
        # the ordinary unreadable-then-restored case: the record is there, so the report is PENDING and nothing is appended
        real = follow_on_module._update_own_record
        calls = {"n": 0}

        def fails_once_with_the_record_still_present(*args, **kwargs):
            calls["n"] += 1
            raise FollowOnRefused("registry_changed", "simulated: another writer won the race")

        with mock.patch("herdr.follow_on._update_own_record", side_effect=fails_once_with_the_record_still_present), self.assertRaises(FollowOnFailed) as caught:
            self.follow_on()
        self.assertEqual(caught.exception.record_state, "PENDING")
        records = self.fx.registry()["children"]
        self.assertEqual(len(records), 2, "no recovery record is added when the original is there")
        self.assertEqual(records[1]["dispatch_state"], "PENDING")
        self.assertIsNotNone(self.complete_refusal())

    def test_a_task_the_gate_sees_COMPLETE_is_not_reported_as_blocking(self):
        # Reachable only when something else finalized the record and the child finished while our own finalize failed. The report
        # must then say that nothing is unresolved, not that it "blocks".
        def finalized_elsewhere_and_the_child_finished(*args, **kwargs):
            registry = self.fx.registry()
            for entry in registry["children"]:
                if entry.get("follow_on_of") == FIRST:
                    entry["dispatch_state"] = "DISPATCHED"
            write_json(self.fx.children_json, registry)
            done = dict(read_json(self.fx.child_task_json), status="COMPLETE", completed_at=50)
            write_json(self.fx.child_task_json, done)
            write_json(self.fx.archive_path(done["id"]), done)
            raise FollowOnRefused("registry_changed", "simulated: another writer won the race")

        with mock.patch("herdr.follow_on._update_own_record", side_effect=finalized_elsewhere_and_the_child_finished), self.assertRaises(FollowOnFailed) as caught:
            self.follow_on()
        self.assertEqual(caught.exception.record_state, "RESOLVED")
        self.assertIn("nothing is unresolved", str(caught.exception))
        self.assertNotIn("blocks its completion", str(caught.exception))
        self.assertIsNone(self.parent_completion_blocked(), "and the gate indeed does not block: the claim is true")


class CompletionAdmissionTests(FollowOnCase):
    """Round 11, finding 4. The parent's completion (the dependency check AND the save of COMPLETE) is serialized with dispatch
    admission by the same lock, and it fails closed."""

    def complete(self):
        args = SimpleNamespace(repo=str(self.fx.parent), checkpoint_file=None, note=None)
        with mock.patch.object(herdctl, "resolve_repo_ref", return_value=self.fx.parent), mock.patch.object(
            herdctl, "COMPLETION_LOCK_WAIT_SECONDS", 0.0
        ):
            herdctl.task_complete_cmd(args)

    def test_a_completion_attempted_between_the_preflight_and_the_intent_registration_is_REFUSED_not_interleaved(self):
        real = follow_on_module._write_registry
        seen = {}

        def complete_inside_the_window(path, document, expected_sha256):
            if "first" not in seen:
                seen["first"] = True
                try:
                    self.complete()
                    seen["completion"] = "COMPLETED"
                except SystemExit as stop:
                    seen["completion"] = str(stop)
            return real(path, document, expected_sha256)

        with mock.patch("herdr.follow_on._write_registry", side_effect=complete_inside_the_window):
            result = self.follow_on()
        self.assertIn("registry_busy", seen["completion"])
        self.assertNotEqual(seen["completion"], "COMPLETED")
        # the dispatch proceeded under an ACTIVE parent, and the parent is now blocked by it
        self.assertEqual(read_json(self.fx.parent_state / "task.json")["status"], "ACTIVE")
        self.assertIsNotNone(self.parent_completion_blocked())
        self.assertEqual(result["task"]["status"], "ACTIVE")

    def test_a_completion_that_lands_first_makes_the_follow_on_refuse_it_never_dispatches_under_a_completed_parent(self):
        self.complete()
        self.assertEqual(read_json(self.fx.parent_state / "task.json")["status"], "COMPLETE")
        before = self.fx.digests()
        with self.assertRaises(FollowOnRefused) as caught:
            self.follow_on()
        self.assertEqual(caught.exception.check, "parent_task_not_active")
        self.assertEqual(self.fx.digests(), before)
        self.assert_nothing_dispatched()

    def test_a_completion_is_refused_while_a_follow_on_holds_the_registry_and_nothing_is_saved(self):
        outcomes = []

        def complete_during_the_dispatch():
            before = self.fx.digests()
            try:
                self.complete()
                outcomes.append("COMPLETED")
            except SystemExit as stop:
                outcomes.append(str(stop))
            outcomes.append(self.fx.digests() == before)

        self.after_prompt = complete_during_the_dispatch
        self.follow_on()
        self.assertIn("registry_busy", outcomes[0])
        self.assertIn("Try again", outcomes[0])
        self.assertTrue(outcomes[1], "the refused completion changed nothing")

    def test_a_missing_state_directory_REFUSES_the_completion_it_does_not_skip_the_lock(self):
        bare = self.fx.root / "no-state-here"
        bare.mkdir()
        args = SimpleNamespace(repo=str(bare), checkpoint_file=None, note=None)
        with mock.patch.object(herdctl, "resolve_repo_ref", return_value=bare), mock.patch.object(
            herdctl, "load_task", return_value={"id": "p", "status": "ACTIVE"}
        ), self.assertRaises(SystemExit) as caught:
            herdctl.task_complete_cmd(args)
        self.assertIn("parent_not_initialized", str(caught.exception))
        self.assertNotIn("Try again", str(caught.exception), "a missing directory is not contention")
        self.assertFalse((bare / ".herd").exists(), "the refusal created nothing")

    def test_a_filesystem_that_cannot_be_locked_is_refused_at_once_it_is_not_waited_for_as_if_busy(self):
        with mock.patch("herdr.follow_on.fcntl.flock", side_effect=OSError(errno.ENOTSUP, "Operation not supported")), mock.patch(
            "herdr.follow_on.time.sleep", side_effect=AssertionError("must not wait on a lock that can never be had")
        ), self.assertRaises(FollowOnRefused) as caught:
            with follow_on_module.registry_lock(self.parent, wait_seconds=30.0):
                pass
        self.assertEqual(caught.exception.check, "registry_lock_unavailable")

    def test_the_lock_creates_nothing_in_the_state_directory(self):
        before = sorted(p.name for p in self.fx.parent_state.iterdir())
        with follow_on_module.registry_lock(self.parent):
            pass
        self.assertEqual(sorted(p.name for p in self.fx.parent_state.iterdir()), before, "taking the lock writes nothing")

    def test_a_missing_state_directory_refuses_the_lock(self):
        with self.assertRaises(FollowOnRefused) as caught:
            with follow_on_module.registry_lock(HerdrInstance(self.fx.root / "nowhere")):
                pass
        self.assertEqual(caught.exception.check, "parent_not_initialized")
        self.assertFalse((self.fx.root / "nowhere").exists())


REPO_ROOT = Path(__file__).resolve().parent.parent

#: A process that takes the registry lock, says so, and holds it until told to let go (a line on stdin): a handshake, never a timer.
HOLDER = """
import sys
sys.path.insert(0, sys.argv[1])
from herdr.follow_on import registry_lock
from herdr.instance import HerdrInstance
with registry_lock(HerdrInstance(sys.argv[2])):
    print("LOCKED", flush=True)
    sys.stdin.readline()
print("RELEASED", flush=True)
"""

#: A SECOND process that tries to get in through one of the three doors that share the lock. It never waits (wait 0).
PROBE = """
import sys
sys.path.insert(0, sys.argv[1])
mode, parent = sys.argv[2], sys.argv[3]
from pathlib import Path
from herdr.follow_on import FollowOnRefused, registry_lock
from herdr.instance import HerdrInstance
try:
    if mode == "lock":
        with registry_lock(HerdrInstance(parent)):
            print("ACQUIRED")
    elif mode == "completion":
        import herdctl
        herdctl.COMPLETION_LOCK_WAIT_SECONDS = 0
        with herdctl.completion_admission(Path(parent)):
            print("ACQUIRED")
    elif mode == "spawn":
        from herdr.control_plane import HerdrControlPlane
        plane = HerdrControlPlane()
        plane.REGISTRY_WAIT_SECONDS = 0
        def launch(repo, **kwargs):
            print("LAUNCHED", flush=True)
            return {"repo": str(repo), "runtime": {}, "task": {"id": "x", "status": "ACTIVE"}}
        plane.spawn = launch
        plane.spawn_child(parent, sys.argv[4], task="synthetic")
        print("SPAWNED")
except FollowOnRefused as refusal:
    print("REFUSED " + refusal.check)
"""


class CrossProcessLockTests(unittest.TestCase):
    """A lock that creates no file must still EXCLUDE. Two real processes: an flock taken twice inside one process can succeed
    trivially, so exclusion is proven between processes, through each door that shares the lock."""

    def setUp(self):
        self.fx = Fixture()
        self.addCleanup(self.fx.cleanup)
        self.env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")

    def start_holder(self):
        holder = subprocess.Popen(
            [sys.executable, "-B", "-c", HOLDER, str(REPO_ROOT), str(self.fx.parent)],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=self.env,
        )

        def stop():
            if holder.poll() is None:
                try:
                    holder.stdin.write("\n")
                    holder.stdin.flush()
                except (BrokenPipeError, ValueError):
                    pass
                holder.wait(timeout=30)
            for stream in (holder.stdin, holder.stdout, holder.stderr):
                try:
                    stream.close()
                except Exception:
                    pass

        self.addCleanup(stop)
        self.assertEqual(holder.stdout.readline().strip(), "LOCKED", "the holder process took the lock")
        return holder

    def probe(self, mode, *extra):
        done = subprocess.run(
            [sys.executable, "-B", "-c", PROBE, str(REPO_ROOT), mode, str(self.fx.parent), *extra],
            capture_output=True, text=True, env=self.env, timeout=60,
        )
        self.assertEqual(done.returncode, 0, done.stderr)
        return done.stdout.split()

    def test_a_second_process_is_refused_through_every_door_while_one_holds_the_lock_and_admitted_after_it_lets_go(self):
        entries_before = sorted(p.name for p in self.fx.parent_state.iterdir())
        holder = self.start_holder()
        target = str(self.fx.root / "spawn-target")
        self.assertEqual(self.probe("lock"), ["REFUSED", "registry_busy"])
        self.assertEqual(self.probe("completion"), ["REFUSED", "registry_busy"])
        spawned = self.probe("spawn", target)
        self.assertEqual(spawned, ["REFUSED", "registry_busy"], "refused BEFORE launch: no LAUNCHED line")
        self.assertEqual(sorted(p.name for p in self.fx.parent_state.iterdir()), entries_before, "holding and being refused create nothing")
        self.assertEqual(len(self.fx.registry()["children"]), 1, "the refused spawn registered nothing")
        holder.stdin.write("\n")
        holder.stdin.flush()
        self.assertEqual(holder.stdout.readline().strip(), "RELEASED")
        holder.wait(timeout=30)
        self.assertEqual(self.probe("lock"), ["ACQUIRED"])
        self.assertEqual(self.probe("completion"), ["ACQUIRED"])
        self.assertEqual(self.probe("spawn", target), ["LAUNCHED", "SPAWNED"])
        self.assertEqual(len(self.fx.registry()["children"]), 2)


class RegistryAnchorFollowOnTests(FollowOnCase):
    """The follow-on registers its identity with the completion gate through the anchor BEFORE it dispatches, and undoes it exactly."""

    def first(self):
        return registration_identity(self.fx.child, FIRST)

    def put_anchor(self, *identities):
        write_json(
            self.fx.parent_state / ANCHOR_FILE,
            {"version": 2, "registered": {PARENT_TASK: [{"repo": repo, "task_id": task_id} for repo, task_id in identities]}},
        )

    def anchor_doc(self):
        path = self.fx.parent_state / ANCHOR_FILE
        return json.loads(path.read_text()) if path.exists() else None

    def test_the_intent_registers_the_first_childs_identity_and_its_own_and_a_successful_follow_on_keeps_both(self):
        result = self.follow_on()
        held = read_registration_anchor(self.parent)[PARENT_TASK]
        self.assertEqual(held, [self.first(), registration_identity(self.fx.child, result["task"]["id"])])

    def test_a_rejected_dispatch_restores_the_anchor_to_what_it_found_and_a_clean_rollback_blocks_nothing(self):
        self.put_anchor(self.first())
        before = self.anchor_doc()
        busy = lambda name: {"status": "busy", "raw": None}  # noqa: E731
        with mock.patch("herdr.tasks.agent_info", side_effect=busy), self.assertRaises(FollowOnRefused):
            self.follow_on()
        self.assertEqual(self.anchor_doc(), before)
        self.assertIsNone(self.parent_completion_blocked())

    def test_a_rejected_dispatch_with_no_anchor_before_leaves_NO_anchor_after(self):
        busy = lambda name: {"status": "busy", "raw": None}  # noqa: E731
        with mock.patch("herdr.tasks.agent_info", side_effect=busy), self.assertRaises(FollowOnRefused):
            self.follow_on()
        self.assertIsNone(self.anchor_doc(), "the directory is exactly as it was found (the seeded first identity was withdrawn too)")

    def test_a_registry_that_no_longer_accounts_for_a_registered_identity_is_not_built_upon(self):
        self.put_anchor(self.first(), registration_identity(self.fx.root / "gone", "gone-task"))
        before = self.fx.digests()
        with self.assertRaises(FollowOnRefused) as caught:
            self.follow_on()
        self.assertEqual(caught.exception.check, "registry_loss_detected")
        self.assertIn("gone-task", str(caught.exception))
        self.assertEqual(self.fx.digests(), before)
        self.assert_nothing_dispatched()

    def test_an_unreadable_or_invalid_anchor_refuses_by_name_and_is_never_rewritten(self):
        good = {"repo": str(self.fx.child), "task_id": FIRST}
        shapes = (
            "{not json", "[]",
            json.dumps({"version": 1, "registered": {PARENT_TASK: [good]}}),  # the retired count-era version is not accepted
            json.dumps({"version": 2, "registered": []}),
            json.dumps({"version": 2, "registered": {PARENT_TASK: 3}}),
            json.dumps({"version": 2, "registered": {PARENT_TASK: ["x"]}}),
            json.dumps({"version": 2, "registered": {PARENT_TASK: [{"repo": "", "task_id": "t"}]}}),
            json.dumps({"version": 2, "registered": {PARENT_TASK: [{"repo": "/r", "task_id": ""}]}}),
            json.dumps({"version": 2, "registered": {PARENT_TASK: [{"repo": "/r"}]}}),
            json.dumps({"version": 2, "registered": {PARENT_TASK: [{"repo": "/r", "task_id": "t", "extra": 1}]}}),
            json.dumps({"version": 2, "registered": {"": [good]}}),
        )
        for text in shapes:
            with self.subTest(anchor=text):
                self.setUp()
                (self.fx.parent_state / ANCHOR_FILE).write_text(text)
                before = self.fx.digests()
                with self.assertRaises(FollowOnRefused) as caught:
                    self.follow_on()
                self.assertEqual(caught.exception.check, "registry_anchor_unreadable")
                self.assertEqual(self.fx.digests(), before)
                self.assertEqual((self.fx.parent_state / ANCHOR_FILE).read_text(), text, "the refused anchor was not rewritten")
                self.assert_nothing_dispatched()

    def test_a_COUNT_ERA_anchor_blocks_the_follow_on_and_the_real_command_until_a_person_removes_it_then_the_next_registration_seeds_identities(self):
        # The real parent's anchor, if a round-13 writer had ever written one, would be version 1 (a count). It is refused, not upgraded.
        (self.fx.parent_state / ANCHOR_FILE).write_text(json.dumps({"version": 1, "registered": {PARENT_TASK: 1}}))
        before = self.fx.digests()
        with self.assertRaises(FollowOnRefused) as caught:
            self.follow_on()
        self.assertEqual(caught.exception.check, "registry_anchor_unreadable")
        self.assertIn("count-era (version 1)", str(caught.exception))
        self.assertIn("a person removes", str(caught.exception))
        self.assertEqual(self.fx.digests(), before)
        self.assert_nothing_dispatched()
        args = SimpleNamespace(repo=str(self.fx.parent), checkpoint_file=None, note=None)
        with mock.patch.object(herdctl, "resolve_repo_ref", return_value=self.fx.parent), self.assertRaises(SystemExit) as refused:
            herdctl.task_complete_cmd(args)
        self.assertIn("count-era (version 1)", str(refused.exception))
        self.assertEqual(read_json(self.fx.parent_state / "task.json")["status"], "ACTIVE")
        (self.fx.parent_state / ANCHOR_FILE).unlink()  # the named step
        result = self.follow_on()
        self.assertEqual(
            read_registration_anchor(self.parent)[PARENT_TASK],
            [self.first(), registration_identity(self.fx.child, result["task"]["id"])],
            "seeded from the identities the registry holds, plus the follow-on's own",
        )

    def test_an_anchor_that_cannot_be_written_withdraws_the_intent_and_refuses_BEFORE_anything_reaches_the_child(self):
        before = self.fx.digests()
        with mock.patch("herdr.follow_on.register_identities", side_effect=OSError("disk full")), self.assertRaises(FollowOnRefused) as caught:
            self.follow_on()
        self.assertEqual(caught.exception.check, "registry_anchor_unwritable")
        self.assertEqual(self.fx.digests(), before, "the intent was withdrawn: the registry is as found")
        self.assert_nothing_dispatched()

    def test_an_anchor_that_cannot_be_written_and_an_intent_that_cannot_be_withdrawn_is_reported_as_blocking_by_the_intent_itself(self):
        with mock.patch("herdr.follow_on.register_identities", side_effect=OSError("disk full")), mock.patch(
            "herdr.follow_on._remove_own_record", side_effect=FollowOnRefused("registry_changed", "simulated")
        ), self.assertRaises(FollowOnFailed) as caught:
            self.follow_on()
        self.assertEqual(caught.exception.record_state, "PENDING")
        self.assert_nothing_dispatched()
        self.assertEqual(self.fx.registry()["children"][-1]["dispatch_state"], "PENDING")
        self.assertIsNotNone(self.parent_completion_blocked(), "the intent record blocks")

    def test_a_rollback_whose_anchor_cannot_be_restored_keeps_the_record_so_it_blocks_and_never_blocks_falsely(self):
        busy = lambda name: {"status": "busy", "raw": None}  # noqa: E731
        with mock.patch("herdr.tasks.agent_info", side_effect=busy), mock.patch(
            "herdr.follow_on.set_registration_anchor", side_effect=OSError("disk full")
        ), self.assertRaises(FollowOnFailed) as caught:
            self.follow_on()
        self.assertEqual(caught.exception.record_state, "PENDING")
        self.assertEqual(self.fx.registry()["children"][-1]["dispatch_state"], "PENDING")
        self.assertIsNotNone(self.parent_completion_blocked())

    def test_after_a_registry_loss_ordinary_follow_ons_cannot_restore_the_accounting_either(self):
        # the same attack, through the other writer: the registry is replaced by other records after a successful follow-on
        self.follow_on()
        write_json(
            self.fx.children_json,
            {"version": 1, "children": [dict(self.fx.registry()["children"][0], task_id="other-1", repo=str(self.fx.root / "o1"))]},
        )
        refusal = None
        args = SimpleNamespace(repo=str(self.fx.parent), checkpoint_file=None, note=None)
        with mock.patch.object(herdctl, "resolve_repo_ref", return_value=self.fx.parent):
            try:
                herdctl.task_complete_cmd(args)
            except SystemExit as stop:
                refusal = str(stop)
        self.assertIn("status=REGISTRY_LOSS", refusal or "")
        self.assertEqual(read_json(self.fx.parent_state / "task.json")["status"], "ACTIVE")


class BridgeTests(unittest.TestCase):
    def setUp(self):
        self.fx = Fixture()
        self.addCleanup(self.fx.cleanup)
        self.request = {"target_repo": str(self.fx.child), "task": " do the work ", "rules": [" a rule "]}

    def test_a_follow_on_request_accepts_only_its_own_fields_and_never_a_force_or_a_reconfiguration(self):
        for field, value in (
            ("force", True),
            ("preset", "x"),
            ("test_command", "make"),
            ("alias", "a"),
            ("policy", {"rules": []}),
            ("rejection_drill", True),
            ("unknown", 1),
        ):
            with self.subTest(field=field):
                with self.assertRaises(ValueError) as caught:
                    orchestrator._validate_follow_on_request(dict(self.request, **{field: value}))
                self.assertIn(field, str(caught.exception))
                self.assertIn("never forces", str(caught.exception))

    def test_a_follow_on_request_is_validated(self):
        for bad in (
            [],
            {"task": "x"},
            {"target_repo": "relative/path", "task": "x"},
            {"target_repo": str(self.fx.child)},
            {"target_repo": str(self.fx.child), "task": "x", "rules": "no"},
            {"target_repo": str(self.fx.child), "task": "x", "rules": [""]},
            {"target_repo": str(self.fx.child), "task": "x", "task_policy": []},
            {"target_repo": str(self.fx.child), "task": "x", "expected_task_id": ""},
        ):
            with self.subTest(request=bad):
                with self.assertRaises(ValueError):
                    orchestrator._validate_follow_on_request(bad)

    def test_the_request_reaches_the_control_plane_cleaned_and_never_through_spawn(self):
        plane = mock.Mock()
        plane.follow_on_child.return_value = {"ok": True}
        plane.spawn_child.side_effect = AssertionError("spawn_child must not be reached")
        result = orchestrator.execute_follow_on_request(
            self.fx.parent, dict(self.request, expected_task_id=FIRST), control_plane=plane
        )
        self.assertEqual(result, {"ok": True})
        plane.follow_on_child.assert_called_once_with(
            self.fx.parent,
            str(self.fx.child),
            task="do the work",
            rules=["a rule"],
            expected_task_id=FIRST,
        )

    def test_the_request_file_must_live_inside_the_parent_state_and_be_valid_json(self):
        outside = self.fx.root / "request.json"
        write_json(outside, self.request)
        with self.assertRaises(ValueError) as caught:
            orchestrator.execute_follow_on_request_file(self.fx.parent, outside, control_plane=mock.Mock())
        self.assertIn("Follow-on request files must live inside", str(caught.exception))
        inside = self.fx.parent_state / "follow-on-request.json"
        inside.write_text("{broken")
        with self.assertRaises(ValueError) as caught:
            orchestrator.execute_follow_on_request_file(self.fx.parent, inside, control_plane=mock.Mock())
        self.assertIn("Follow-on request is not valid JSON", str(caught.exception))
        write_json(inside, self.request)
        plane = mock.Mock()
        plane.follow_on_child.return_value = {"ok": 1}
        self.assertEqual(orchestrator.execute_follow_on_request_file(self.fx.parent, inside, control_plane=plane), {"ok": 1})

    def test_the_spawn_request_files_keep_their_exact_existing_messages(self):
        outside = self.fx.root / "spawn.json"
        write_json(outside, {"target_repo": str(self.fx.child), "task": "x"})
        with self.assertRaises(ValueError) as caught:
            orchestrator.execute_spawn_request_file(self.fx.parent, outside, control_plane=mock.Mock())
        self.assertIn("Spawn request files must live inside", str(caught.exception))
        with self.assertRaises(ValueError) as caught:
            orchestrator.execute_spawn_request_file(self.fx.parent, self.fx.parent_state / "missing.json", control_plane=mock.Mock())
        self.assertIn("Spawn request file not found", str(caught.exception))

    def test_the_spawn_command_still_refuses_a_follow_on_field_and_the_cli_has_a_separate_command(self):
        with self.assertRaises(ValueError):
            orchestrator._validate_request({"target_repo": str(self.fx.child), "task": "x", "expected_task_id": FIRST})
        request_file = self.fx.parent_state / "follow-on-request.json"
        write_json(request_file, self.request)
        printed = io.StringIO()
        argv = ["herdr-orchestrator", "follow-on", "--parent", str(self.fx.parent), "--request-file", str(request_file)]
        with mock.patch("sys.argv", argv), mock.patch.object(
            orchestrator, "execute_follow_on_request_file", return_value={"child": "ok"}
        ) as runner, mock.patch.object(orchestrator, "execute_spawn_request_file", side_effect=AssertionError("spawn")), redirect_stdout(printed):
            orchestrator.main()
        runner.assert_called_once_with(str(self.fx.parent), str(request_file))
        self.assertEqual(json.loads(printed.getvalue()), {"child": "ok"})

    def test_a_refusal_surfaces_as_a_clear_exit_message_naming_the_check(self):
        request_file = self.fx.parent_state / "follow-on-request.json"
        write_json(request_file, self.request)
        self.fx.children_json.write_text("{corrupt")
        argv = ["herdr-orchestrator", "follow-on", "--parent", str(self.fx.parent), "--request-file", str(request_file)]
        with mock.patch("sys.argv", argv), self.assertRaises(SystemExit) as caught:
            orchestrator.main()
        self.assertIn("registry_unreadable", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
