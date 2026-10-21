"""Item 07: the READ-ONLY task-native catalog source and runtime agents.

Every case runs the REAL catalog owner (``target_runtime.catalog``) with
root discovery, over REAL on-disk herd state written as the engine writes
it, a private source registry, a private DI repository registry
(``repos.json``, the locator ``herdctl init`` writes) and a FAKE agent
listing; nothing reaches the engine (the fixture's tripwire fails any test
that does). All identities are SYNTHETIC.

The launch-route matrix: what each production route leaves on disk is what
this source reads, so each route is exercised by its artifacts:

1. ``herdctl`` bootstrap: runtime and role bindings, NO ``task.json``;
2. ``herdctl task TEXT`` / ``task --mission``: ``task.json`` ACTIVE, a plain
   Herdr mission v1 (no ``mn-`` identity);
3. ``HerdrControlPlane.spawn`` / ``spawn_child``: the child's own root and
   task, recorded in the parent root's ``children.json``;
4. ``herdr.orchestrator`` ``execute_spawn_request``: converges on
   ``spawn_child`` (``herdr/orchestrator.py``), so the same artifacts;
5. Mission Core / local request: a Mission Core record bound to the task;
6. legacy WorkflowStore: a workflow record bound to the task.

The Supervisor's six required regressions (B1 runtime visibility decision)
are the classes ``Required1`` .. ``Required6``.
"""

import hashlib
import json
import os
import re
import shutil
import sys
import time
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "tests"))

from herdr import registry as herd_registry  # noqa: E402
from herdr import tasks as engine_tasks  # noqa: E402
from mission_sources import contract  # noqa: E402
from mission_sources import contract_v2 as v2  # noqa: E402
from mission_sources import sources as sources_module  # noqa: E402
from target_runtime import catalog  # noqa: E402
from target_runtime import herdr_task_source as hts  # noqa: E402
from target_runtime import runtime_snapshot  # noqa: E402
import test_catalog as catalog_tests  # noqa: E402
from test_mission_sources import NOW  # noqa: E402
from test_runtime_snapshot import engine_row, listing_stdout, stable  # noqa: E402

LOGICALS = ("lead1", "executor1", "reviewer1", "supervisor")
TASK_N = "20261017-150121-aaaaaa"      # SYNTHETIC: a direct herdctl task
TASK_K = "20261017-133442-bbbbbb"      # SYNTHETIC: a Mission Core task
TASK_CHILD = "20261017-160000-cccccc"  # SYNTHETIC: a spawned child task
VERIFIED = contract.IDENTITY_VERIFIED


def root_key(root):
    return hashlib.sha256(root.encode("utf-8")).hexdigest()[:12]


class Team(object):
    """One herd root as the engine leaves it, and its live listing rows."""

    def __init__(self, root, agents, panes, sessions, rows):
        self.root, self.agents, self.panes = root, agents, panes
        self.sessions, self.rows = sessions, rows


class HerdrFixture(catalog_tests.CatalogFixture):

    def setUp(self):
        super(HerdrFixture, self).setUp()
        self._pane = 3

    # -- the DI repository registry (the locator) -------------------------

    def herd_registry(self, roots, version=1, raw=None, name="di-registry"):
        directory = os.path.realpath(os.path.join(self.root, name))
        os.makedirs(directory, exist_ok=True)
        path = os.path.join(directory, "repos.json")
        if raw is None:
            raw = json.dumps({"version": version, "repos": {
                alias: {"path": path_, "registered_at": NOW}
                for alias, path_ in roots.items()}}, indent=2) + "\n"
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(raw)
        os.chmod(path, 0o644)
        return directory

    def register_native(self, directory, source_id="herd"):
        self.register(source_id=source_id, kind=contract.KIND_TASK_NATIVE,
                      store=directory, producer="herdctl")

    def fresh_registry(self, name):
        self.registry = type(self.registry)(os.path.join(self.root, name))

    # -- herd state, shaped like the engine's own ------------------------

    def team(self, prefix, task_id, status="ACTIVE", task=True,
             logicals=LOGICALS, mission=True, root=None, started_at=NOW,
             workspace="w1"):
        """A herd root whose roles are ``<prefix>-<logical>``, each on its
        own pane and session, so two teams never share an identity."""
        if root is None:
            root = os.path.realpath(os.path.join(self.root, "repo-" + prefix))
            os.makedirs(root, exist_ok=True)
        agents = {logical: "%s-%s" % (prefix, logical) for logical in logicals}
        panes, sessions = {}, {}
        for logical in sorted(logicals):
            panes[logical] = "%s:p%d" % (workspace, self._pane)
            sessions[logical] = "sess-%s-%s" % (prefix, logical)
            self._pane += 1
        roles = {logical: {
            "logical": logical, "agent": agents[logical],
            "session": sessions[logical],
            "stable": stable(agents[logical], panes[logical], workspace)}
            for logical in logicals}
        self.herd(root=root, task_id=task_id, status=status, agents=agents,
                  panes=panes, roles=roles, task=task, workspace_id=workspace)
        state = os.path.join(root, ".herd", "state")
        if task:
            self.write(state, "task.json", {
                "version": 1, "id": task_id, "status": status,
                "description": "SYNTHETIC task", "repo": root,
                "started_at": started_at})
        if mission and task:
            # A plain Herdr mission v1, as ``herdctl task --mission`` leaves
            # it: no Mission Core identity anywhere.
            self.write(state, "mission.json",
                       {"version": 1, "task_id": task_id,
                        "objective": "SYNTHETIC plain mission"})
        rows = [engine_row(name=agents[logical], session=sessions[logical],
                           pane=panes[logical], workspace=workspace)
                for logical in logicals]
        return Team(root, agents, panes, sessions, rows)

    def owner(self, *rows_lists, version=None, extra_rows=(), discover=True):
        rows = [row for rows in rows_lists for row in rows] + list(extra_rows)
        options = {} if version is None else {"contract_version": version}
        owner = catalog.CatalogService(
            self.registry.directory, self.clock, None,
            self.plan(stdout=listing_stdout(rows)), monotonic=self.ticks,
            discover=discover, **options)

        def end():
            owner.shutdown()
            owner.settle(10)
        self.addCleanup(end)
        return owner

    def answer(self, owner):
        self.ticks.t += catalog.MIN_REFRESH_INTERVAL_SECONDS
        answer = owner.request(catalog.MAX_WAIT_SECONDS)
        self.assertIsNotNone(answer["response"], answer)
        return answer

    def by_ref(self, answer):
        return {m["mission_ref"]: m for m in answer["response"]["missions"]}

    def ref(self, task_id, root, source_id="herd"):
        return "%s:%s@%s" % (source_id, task_id, root_key(root))

    def boot_ref(self, root, source_id="herd"):
        return "%s:root@%s" % (source_id, root_key(root))

    def members(self, answer):
        return [m for mission in answer["response"]["missions"]
                for m in mission["members"]]

    def runtime(self, answer):
        return answer["response"]["runtime_agents"]

    def entries(self, answer):
        return {e["agent"]: e for e in self.runtime(answer)["agents"]}

    def verified(self, answer):
        return sorted(e["agent"] for e in self.runtime(answer)["agents"]
                      if e["identity"]["state"] == VERIFIED)

    def characters(self, answer):
        """Every live agent the answer would draw: members a bound team
        classifies present/replaced, plus verified runtime agents."""
        drawn = [m["agent"] for m in self.members(answer)
                 if m["classification"] in (contract.MEMBER_PRESENT,
                                            contract.MEMBER_REPLACED)]
        return sorted(drawn + self.verified(answer))

    def source(self, answer, source_id="herd"):
        return {s["source_id"]: s for s in answer["response"]["sources"]}[
            source_id]

    def between_passes(self, change):
        """A patch running ``change`` after the first source pass and before
        the capture (``Observation.stable_names`` is called exactly there)."""
        real = hts.Observation.stable_names

        def wrapped(observation):
            change()
            return real(observation)
        return mock.patch.object(hts.Observation, "stable_names", wrapped)


class MixedRegistry(object):
    """Operator-supplied SHAPE (fixture realism only; never observed here):
    98 aliases over 92 distinct paths, 4 shared-path groups, 19 missing
    paths; per distinct path 15 ACTIVE, 29 COMPLETE, 9 ABORTED and 39
    no_task records; 4 live native workspaces. Registry totals are NOT
    live team counts."""

    def build(self):
        roots, live_rows, live = {}, [], {}
        store = self.mission_state("mc")
        self.register(source_id="mc", store=store)
        _, workspace = self.running_mission(store, task_id=TASK_K)
        k = self.team("w5k", TASK_K, root=workspace, mission=False)
        n = self.team("w5n", TASK_N, started_at=NOW - 5)
        roots.update({"w5k": k.root, "w5n": n.root})
        live_rows += k.rows + n.rows
        live.update(k=k, n=n)
        # 13 more ACTIVE records, none live; two reuse w5n's agent NAMES on
        # dead sessions (an uncorroborated record must not disturb w5n).
        for index in range(13):
            prefix = "w5n" if index < 2 else "act%02d" % index
            root = os.path.realpath(os.path.join(self.root,
                                                 "act-%02d" % index))
            os.makedirs(root)
            team = self.team(prefix, "20261001-0000%02d-dddddd" % index,
                             root=root, started_at=NOW - 100 - index)
            if index < 2:
                state = os.path.join(root, ".herd", "state")
                roles = json.loads(Path(state, "role-bindings.json")
                                   .read_text())
                for role in roles["roles"].values():
                    role["session"] = "sess-dead-%02d-%s" % (
                        index, role["logical"])
                self.write(state, "role-bindings.json", roles)
            roots["act%02d" % index] = team.root
        # 29 COMPLETE (one still holding live sessions) and 9 ABORTED.
        for index in range(29):
            team = self.team("done%02d" % index, "20260901-0000%02d-eeeeee"
                             % index, status="COMPLETE",
                             started_at=NOW - 1000 - index)
            roots["done%02d" % index] = team.root
            if index == 0:
                live_rows += team.rows
                live["done"] = team
        for index in range(9):
            team = self.team("abrt%02d" % index, "20260801-0000%02d-ffffff"
                             % index, status="ABORTED")
            roots["abrt%02d" % index] = team.root
        # 39 no_task: 10 bootstrapped (one live), 10 never started, 19
        # missing paths.
        for index in range(10):
            team = self.team("boot%02d" % index, None, task=False)
            roots["boot%02d" % index] = team.root
            if index == 0:
                live_rows += team.rows
                live["boot"] = team
        for index in range(10):
            root = os.path.realpath(os.path.join(self.root,
                                                 "never-%02d" % index))
            os.makedirs(root)
            roots["never%02d" % index] = root
        for index in range(19):
            roots["gone%02d" % index] = os.path.join(self.root,
                                                     "removed-%02d" % index)
        self.assertEqual(len(roots), 92)
        # 4 shared-path groups (6 extra aliases): 98 aliases, 92 paths. The
        # two live task roots stay registered exactly once.
        for alias, target in (("done00-b", "done00"), ("boot00-b", "boot00"),
                              ("abrt00-b", "abrt00"), ("abrt00-c", "abrt00"),
                              ("never00-b", "never00"),
                              ("never00-c", "never00")):
            roots[alias] = roots[target]
        self.assertEqual(len(roots), 98)
        self.assertEqual(len(set(roots.values())), 92)
        self.register_native(self.herd_registry(roots))
        extra = [engine_row(name="native-%d" % i, session="sess-native-%d" % i,
                            pane="w9:p%d" % i, workspace="w9")
                 for i in range(6)]
        return live, live_rows, extra


# ---------------------------------------------------------------------------
# Required 6 (and the original route matrix): every launch route.
# ---------------------------------------------------------------------------

class Required6RouteMatrix(HerdrFixture):

    def test_route_2_a_direct_herdctl_task_is_a_herdr_task_with_its_team(self):
        team = self.team("w5n", TASK_N)
        self.register_native(self.herd_registry({"w5n": team.root}))
        answer = self.answer(self.owner(team.rows))
        mission = self.by_ref(answer)[self.ref(TASK_N, team.root)]
        self.assertEqual(mission["lifecycle"], {
            "model": hts.LIFECYCLE_MODEL, "state": "ACTIVE",
            "standing": contract.STANDING_ACTIVE})
        self.assertEqual(mission["binding"]["state"], contract.BINDING_BOUND)
        self.assertEqual(sorted(m["logical"] for m in mission["members"]),
                         sorted(LOGICALS))
        self.assertTrue(all(m["classification"] == contract.MEMBER_PRESENT
                            for m in mission["members"]))
        # No Mission Core identity is synthesized for a plain Herdr task.
        self.assertIsNone(mission["revision"])
        self.assertFalse(mission["native_id"].startswith("mn-"))
        self.assertEqual(mission["execution"]["identity_source"],
                         hts.IDENTITY_SOURCE)
        self.assertEqual(mission["producer"]["recorded"], {
            "schema": hts.SCHEMA, "schema_version": hts.SCHEMA_VERSION,
            "root_aliases": ["w5n"], "membership": hts.MEMBERSHIP_DERIVED})
        # Its four agents are drawn ONCE, as members.
        runtime = self.runtime(answer)
        self.assertEqual(runtime["state"], contract.RUNTIME_AGENTS_OBSERVED)
        self.assertEqual(runtime["agents"], [])
        self.assertEqual(runtime["counts"]["represented_by_teams"], 4)
        self.assertEqual(len(self.characters(answer)), 4)

    def test_route_2_under_contract_v2_mission_core_facts_stay_unsupported(self):
        team = self.team("w5n", TASK_N)
        self.register_native(self.herd_registry({"w5n": team.root}))
        answer = self.answer(self.owner(team.rows, version=2))
        mission = self.by_ref(answer)[self.ref(TASK_N, team.root)]
        self.assertEqual(mission["mission_state"]["state"],
                         v2.MISSION_STATE_NOT_APPLICABLE)
        self.assertEqual(mission["admitted_artifacts"]["canonical_review"][
            "state"], v2.REVIEW_NOT_APPLICABLE)
        self.assertEqual(mission["workflow_phase"]["state"],
                         v2.WORKFLOW_PHASE_NOT_APPLICABLE)
        self.assertEqual(len(mission["members"]), 4)
        self.assertEqual(self.runtime(answer)["counts"][
            "represented_by_teams"], 4)
        v2.validate_response_v2(answer["response"])

    def test_route_1_bootstrap_only_forms_no_team(self):
        boot = self.team("boot", None, task=False)
        self.register_native(self.herd_registry({"boot": boot.root}))
        answer = self.answer(self.owner(boot.rows))
        mission = self.by_ref(answer)[self.boot_ref(boot.root)]
        self.assertEqual(mission["lifecycle"]["state"], hts.NO_CURRENT_TASK)
        self.assertEqual(mission["lifecycle"]["standing"],
                         contract.STANDING_INACTIVE)
        self.assertIsNone(mission["execution"]["task_id"])
        self.assertIsNone(mission["objective"])
        self.assertEqual(mission["binding"]["state"],
                         contract.BINDING_NOT_APPLICABLE)
        self.assertEqual(mission["members"], [])
        self.assertEqual(self.members(answer), [])

    def test_route_5_a_mission_core_task_is_covered_never_duplicated(self):
        store = self.mission_state("mc")
        self.register(source_id="mc", store=store)
        mission_id, workspace = self.running_mission(store, task_id=TASK_K)
        team = self.team("w5k", TASK_K, root=workspace, mission=False)
        self.register_native(self.herd_registry({"w5k": workspace}))
        answer = self.answer(self.owner(team.rows))
        missions = self.by_ref(answer)
        core = missions["mc:" + mission_id]
        alias = missions[self.ref(TASK_K, workspace)]
        self.assertEqual(core["binding"]["state"], contract.BINDING_BOUND)
        self.assertEqual(len(core["members"]), 4)
        self.assertEqual(alias["binding"], {
            "state": contract.BINDING_NOT_APPLICABLE,
            "reason": "covered_by_canonical_record", "task_id": TASK_K})
        self.assertEqual(alias["members"], [])
        self.assertEqual(len(self.characters(answer)), 4)
        self.assertEqual(self.runtime(answer)["counts"][
            "represented_by_teams"], 4)

    def test_route_6_a_workflow_store_task_is_covered_never_conflated(self):
        legacy = self.store_dir("legacy")
        team = self.team("wf", TASK_K)
        self.workflow(legacy, workflow_id="wf-0009", task_id=TASK_K,
                      workspace=team.root)
        self.register(source_id="legacy", kind=contract.KIND_WORKFLOW_STORE,
                      store=legacy)
        self.register_native(self.herd_registry({"wf": team.root}))
        answer = self.answer(self.owner(team.rows))
        missions = self.by_ref(answer)
        workflow = missions["legacy:wf-0009"]
        alias = missions[self.ref(TASK_K, team.root)]
        self.assertEqual(workflow["lifecycle"]["model"],
                         contract.LIFECYCLE_MODEL_WORKFLOW_STORE)
        self.assertEqual(workflow["execution"]["task_id"], TASK_K)
        self.assertEqual(alias["binding"]["reason"],
                         "covered_by_canonical_record")
        self.assertEqual(alias["members"], [])
        self.assertEqual(len(self.characters(answer)),
                         len(workflow["members"]))

    def child_link(self, parent_root, child_root):
        self.write(os.path.join(parent_root, ".herd", "state"),
                   v2.CHILDREN_FILE, {"version": 1, "children": [{
                       "parent_task_id": TASK_N, "task_id": TASK_CHILD,
                       "repo": child_root, "dependency": True,
                       "task_status": "ACTIVE", "workspace_id": "w1",
                       "agents": {logical: "kid-" + logical
                                  for logical in LOGICALS}}]})

    def test_routes_3_and_4_a_spawned_child_is_one_representation_v2(self):
        parent = self.team("w5n", TASK_N)
        child = self.team("kid", TASK_CHILD)
        self.child_link(parent.root, child.root)
        self.register_native(self.herd_registry({"w5n": parent.root,
                                                 "kid": child.root}))
        answer = self.answer(self.owner(parent.rows, child.rows, version=2))
        missions = self.by_ref(answer)
        parent_mission = missions[self.ref(TASK_N, parent.root)]
        child_alias = missions[self.ref(TASK_CHILD, child.root)]
        chains = parent_mission["task_chains"]
        self.assertEqual([c["chain"] for c in chains], ["direct", "child"])
        self.assertEqual(chains[1]["binding"]["state"], contract.BINDING_BOUND)
        self.assertEqual(child_alias["binding"]["reason"],
                         "covered_by_parent_chain")
        self.assertEqual(child_alias["members"], [])
        self.assertEqual(len(self.characters(answer)), 8)
        self.assertEqual(len(set(self.characters(answer))), 8)
        self.assertEqual(self.runtime(answer)["agents"], [])
        v2.validate_response_v2(answer["response"])

    def test_routes_3_and_4_under_v1_the_child_is_its_own_herdr_task(self):
        parent = self.team("w5n", TASK_N)
        child = self.team("kid", TASK_CHILD)
        self.child_link(parent.root, child.root)
        self.register_native(self.herd_registry({"w5n": parent.root,
                                                 "kid": child.root}))
        answer = self.answer(self.owner(parent.rows, child.rows))
        missions = self.by_ref(answer)
        self.assertEqual(len(missions[self.ref(TASK_N, parent.root)][
            "members"]), 4)
        self.assertEqual(len(missions[self.ref(TASK_CHILD, child.root)][
            "members"]), 4)
        self.assertEqual(len(set(self.characters(answer))), 8)

    def test_the_two_team_acceptance_shape_is_eight_distinct_characters(self):
        store = self.mission_state("mc")
        self.register(source_id="mc", store=store)
        _, workspace = self.running_mission(store, task_id=TASK_K)
        k = self.team("w5k", TASK_K, root=workspace, mission=False)
        n = self.team("w5n", TASK_N)
        self.register_native(self.herd_registry({"w5k": workspace,
                                                 "w5n": n.root}))
        answer = self.answer(self.owner(k.rows, n.rows, extra_rows=[
            engine_row(name="unrelated-shell", session="sess-x",
                       pane="w9:p1", workspace="w9")]))
        self.assertEqual(len(self.characters(answer)), 8)
        self.assertEqual(len(set(self.characters(answer))), 8)
        self.assertEqual(answer["response"]["unmatched_runtime_agent_count"], 1)
        self.assertNotIn("unrelated-shell", self.entries(answer))


# ---------------------------------------------------------------------------
# Required 1: bootstrap / no-task roles.
# ---------------------------------------------------------------------------

class Required1BootstrapRoles(HerdrFixture):

    def test_a_bootstrapped_role_with_exact_two_pass_identity_is_unassigned(
            self):
        boot = self.team("boot", None, task=False)
        self.register_native(self.herd_registry({"boot": boot.root}))
        answer = self.answer(self.owner(boot.rows))
        # No team, no member, no Mission (Required6 route 1) AND four real,
        # individually verified runtime characters.
        self.assertEqual(self.members(answer), [])
        self.assertEqual(self.verified(answer), sorted(boot.agents.values()))
        for entry in self.runtime(answer)["agents"]:
            self.assertEqual(entry["identity"],
                             {"state": VERIFIED, "reason": hts.ID_PRESENT})
            self.assertEqual(entry["assignment"], {
                "state": contract.ASSIGNMENT_UNASSIGNED,
                "reason": hts.AS_NO_TASK_RECORD, "task_ref": None,
                "task_id": None, "task_status": None})
            self.assertEqual(entry["root_ref"], self.boot_ref(boot.root))
            self.assertEqual(entry["session"],
                             boot.sessions[entry["logical"]])
            self.assertNotIn("mn-", entry["root_ref"])
        runtime = self.runtime(answer)
        self.assertEqual(runtime["caveat"], contract.RUNTIME_AGENTS_CAVEAT)
        self.assertIsNotNone(runtime["first_pass_at"])
        self.assertIsNotNone(runtime["observed_at"])
        # With no team to observe, ONE root anchored the listing: disclosed.
        self.assertEqual(answer["runtime_capture"]["discovery"][
            "listing_anchor"], boot.root)

    def test_identical_records_without_a_native_identity_yield_zero(self):
        boot = self.team("boot", None, task=False)
        self.register_native(self.herd_registry({"boot": boot.root}))
        answer = self.answer(self.owner(extra_rows=[engine_row(
            name="someone-else", session="s9", pane="w9:p1",
            workspace="w9")]))
        self.assertEqual(self.verified(answer), [])
        self.assertEqual(self.runtime(answer)["agents"], [])
        self.assertEqual(self.runtime(answer)["counts"]["roles_live"], 0)
        self.assertEqual(self.runtime(answer)["counts"]["roles_recorded"], 4)

    def test_identical_records_with_other_sessions_yield_zero_verified(self):
        boot = self.team("boot", None, task=False)
        for row in boot.rows:
            row["agent_session"]["value"] += "-new"
        self.register_native(self.herd_registry({"boot": boot.root}))
        answer = self.answer(self.owner(boot.rows))
        self.assertEqual(self.verified(answer), [])
        self.assertEqual(
            {e["identity"]["reason"] for e in self.runtime(answer)["agents"]},
            {hts.ID_SESSION_REPLACED})

    def test_without_the_discovery_pass_nothing_is_verified(self):
        boot = self.team("boot", None, task=False)
        self.register_native(self.herd_registry({"boot": boot.root}))
        owner = catalog.CatalogService(
            self.registry.directory, self.clock, [boot.root],
            self.plan(stdout=listing_stdout(boot.rows)), monotonic=self.ticks)

        def end():
            owner.shutdown()
            owner.settle(10)
        self.addCleanup(end)
        runtime = self.runtime(self.answer(owner))
        self.assertEqual((runtime["state"], runtime["problem"]),
                         (contract.RUNTIME_AGENTS_UNAVAILABLE,
                          hts.PROBLEM_SINGLE_PASS))
        self.assertEqual(runtime["agents"], [])


# ---------------------------------------------------------------------------
# Required 2: captured teams vs over-capacity roots.
# ---------------------------------------------------------------------------

class Required2CapturedAndOverCapacity(HerdrFixture):

    def test_a_captured_active_team_keeps_idle_and_done_members(self):
        team = self.team("w5n", TASK_N)
        for row, status in zip(team.rows, ("idle", "done", "idle", "done")):
            row["agent_status"] = status
        self.register_native(self.herd_registry({"w5n": team.root}))
        answer = self.answer(self.owner(team.rows))
        mission = self.by_ref(answer)[self.ref(TASK_N, team.root)]
        self.assertEqual(mission["lifecycle"]["state"], "ACTIVE")
        self.assertEqual(mission["binding"]["state"], contract.BINDING_BOUND)
        self.assertEqual(sorted(m["activity"] for m in mission["members"]),
                         ["done", "done", "idle", "idle"])
        self.assertEqual(self.runtime(answer)["agents"], [])

    def test_more_live_roots_than_capture_capacity_with_no_cap_change(self):
        self.assertEqual(runtime_snapshot.MAX_HERD_ROOTS, 16)
        count = runtime_snapshot.MAX_HERD_ROOTS + 2
        teams, roots, tasks = [], {}, []
        for index in range(count):
            task_id = "20261017-1000%02d-abcdef" % index
            team = self.team("t%02d" % index, task_id,
                             logicals=("lead1", "executor1"),
                             started_at=NOW - index, mission=False)
            teams.append(team)
            tasks.append(task_id)
            roots["t%02d" % index] = team.root
        # The OLDEST over-capacity root: one role on its recorded session,
        # one held by ANOTHER session (one role never validates another).
        oldest = teams[-1]
        oldest.rows[1]["agent_session"]["value"] = "sess-somebody-else"
        self.register_native(self.herd_registry(roots))
        answer = self.answer(self.owner(*[t.rows for t in teams]))
        self.assertEqual(runtime_snapshot.MAX_HERD_ROOTS, 16)
        account = answer["runtime_capture"]["roots"]
        self.assertEqual(len(account["captured"]), 16)
        self.assertEqual(sorted(account["over_capacity"]),
                         sorted(t.root for t in teams[16:]))
        runtime = self.runtime(answer)
        self.assertEqual(runtime["counts"]["roots_over_capacity"], 2)
        missions = self.by_ref(answer)
        for index, team in enumerate(teams):
            mission = missions[self.ref(tasks[index], team.root)]
            if index < 16:
                self.assertEqual(mission["binding"]["state"],
                                 contract.BINDING_BOUND)
                continue
            # Explicitly UNBOUND task: never promoted, never members.
            self.assertEqual(mission["binding"]["state"],
                             contract.BINDING_UNVERIFIED)
            self.assertEqual(mission["binding"]["reason"],
                             "workspace_not_observed")
            self.assertEqual(mission["members"], [])
        entries = self.entries(answer)
        lead = entries[oldest.agents["lead1"]]
        self.assertEqual(lead["identity"]["state"], VERIFIED)
        self.assertEqual(lead["assignment"], {
            "state": contract.ASSIGNMENT_UNVERIFIED_REFERENCE,
            "reason": hts.AS_OVER_CAPACITY,
            "task_ref": self.ref(tasks[-1], oldest.root),
            "task_id": tasks[-1], "task_status": "ACTIVE"})
        other = entries[oldest.agents["executor1"]]
        self.assertEqual(other["identity"], {
            "state": contract.IDENTITY_UNVERIFIED,
            "reason": hts.ID_SESSION_REPLACED})
        # Members of the 16 captured teams + ONLY independently verified
        # runtime characters for the two uncaptured roots.
        self.assertEqual(len(self.members(answer)), 32)
        self.assertEqual(self.verified(answer), sorted(
            [teams[16].agents["lead1"], teams[16].agents["executor1"],
             oldest.agents["lead1"]]))

    def test_an_active_record_without_live_corroboration_draws_nothing(self):
        stale = self.team("stale", TASK_N)
        live = self.team("live", TASK_CHILD)
        self.register_native(self.herd_registry({"stale": stale.root,
                                                 "live": live.root}))
        answer = self.answer(self.owner(live.rows))
        mission = self.by_ref(answer)[self.ref(TASK_N, stale.root)]
        self.assertEqual(mission["binding"], {
            "state": contract.BINDING_UNVERIFIED,
            "reason": "task_record_not_corroborated_by_a_live_session",
            "task_id": TASK_N})
        self.assertEqual(mission["members"], [])
        self.assertEqual(self.characters(answer), sorted(live.agents.values()))


# ---------------------------------------------------------------------------
# Required 3: the realistic mixed registry.
# ---------------------------------------------------------------------------

class Required3MixedRegistry(MixedRegistry, HerdrFixture):

    def test_registry_totals_are_not_live_team_counts(self):
        live, live_rows, extra = self.build()
        for version in (1, 2):
            with self.subTest(version=version):
                answer = self.answer(self.owner(live_rows, extra_rows=extra,
                                                version=version))
                locators = self.source(answer)["locators"]
                self.assertEqual(locators["registered_aliases"], 98)
                self.assertEqual(locators["physical_roots"], 73)
                self.assertEqual(locators["shared_root_groups"], 4)
                self.assertEqual(locators["aliases_without_herd_state"], 12)
                self.assertEqual(sorted(e["reason"]
                                        for e in locators["excluded"]),
                                 [hts.EXCLUDED_ROOT_ABSENT] * 19)
                self.assertEqual(locators["roots_with_current_task"], 53)
                self.assertEqual(locators["roots_without_current_task"], 10)
                # Teams: ONLY w5K (Mission Core) and w5N bind; 13 stale
                # ACTIVE records never bind and never disturb w5N.
                bound = [m for m in answer["response"]["missions"]
                         if m["binding"]["state"] == contract.BINDING_BOUND]
                self.assertEqual(len(bound), 2)
                members = self.members(answer)
                self.assertEqual(len(members), 8)
                self.assertTrue(all(
                    m["classification"] == contract.MEMBER_PRESENT
                    for m in members))
                # Runtime-only: the bootstrapped and the COMPLETE root's
                # live roles, individually verified, unassigned.
                self.assertEqual(self.verified(answer), sorted(
                    list(live["boot"].agents.values())
                    + list(live["done"].agents.values())))
                entries = self.entries(answer)
                done_lead = entries[live["done"].agents["lead1"]]
                self.assertEqual(done_lead["assignment"]["task_status"],
                                 "COMPLETE")
                self.assertEqual(done_lead["assignment"]["reason"],
                                 hts.AS_NOT_CURRENT)
                self.assertEqual(
                    entries[live["boot"].agents["lead1"]]["root_aliases"],
                    ["boot00", "boot00-b"])
                characters = self.characters(answer)
                self.assertEqual(len(characters), 16)
                self.assertEqual(len(set(characters)), 16)
                runtime = self.runtime(answer)
                self.assertEqual(runtime["counts"]["roots_requested"], 98)
                self.assertEqual(runtime["counts"]["roots_read"], 63)
                self.assertEqual(runtime["counts"]["roots_over_capacity"], 0)
                self.assertEqual(
                    answer["response"]["unmatched_runtime_agent_count"], 14)


# ---------------------------------------------------------------------------
# Required 4: per-role evidence failures stay fail-closed.
# ---------------------------------------------------------------------------

class Required4FailClosed(HerdrFixture):

    def run_boot(self, boot, rows=None, **owner):
        self.register_native(self.herd_registry({"boot": boot.root}))
        return self.answer(self.owner(boot.rows if rows is None else rows,
                                      **owner))

    def test_wrong_session_pane_workspace_or_cwd_is_never_verified(self):
        cases = (("session", hts.ID_SESSION_REPLACED),
                 ("pane_id", hts.ID_DEGRADED),
                 ("workspace_id", hts.ID_DEGRADED),
                 ("cwd", hts.ID_DEGRADED))
        for index, (field, reason) in enumerate(cases):
            with self.subTest(field=field):
                self.fresh_registry("sources-%d" % index)
                boot = self.team("b%d" % index, None, task=False)
                bad = boot.rows[0]
                if field == "session":
                    bad["agent_session"]["value"] = "sess-other"
                else:
                    bad[field] = "/elsewhere" if field == "cwd" else "zz"
                answer = self.run_boot(boot)
                entry = self.entries(answer)[bad["name"]]
                self.assertEqual(entry["identity"]["state"],
                                 contract.IDENTITY_UNVERIFIED)
                self.assertTrue(entry["identity"]["reason"].startswith(reason),
                                entry["identity"])
                # The other roles are decided on their OWN evidence.
                self.assertEqual(len(self.verified(answer)), 3)

    def test_a_duplicated_agent_or_shared_session_is_ambiguous(self):
        boot = self.team("boot", None, task=False)
        twin = dict(boot.rows[0], pane_id="w1:p90")
        answer = self.run_boot(boot, boot.rows + [twin])
        self.assertEqual(self.entries(answer)[twin["name"]]["identity"], {
            "state": contract.IDENTITY_AMBIGUOUS,
            "reason": hts.ID_NAME_DUPLICATED})
        self.fresh_registry("sources-shared")
        boot2 = self.team("b2", None, task=False)
        sharer = engine_row(name="impostor", session=boot2.rows[1][
            "agent_session"]["value"], pane="w9:p1", workspace="w9")
        answer = self.run_boot(boot2, boot2.rows + [sharer])
        self.assertEqual(self.entries(answer)[boot2.rows[1]["name"]][
            "identity"], {"state": contract.IDENTITY_AMBIGUOUS,
                          "reason": hts.ID_SESSION_SHARED})

    def test_one_live_identity_recorded_by_two_roots_is_ambiguous(self):
        a = self.team("same", None, task=False)
        other = os.path.realpath(os.path.join(self.root, "copy"))
        os.makedirs(os.path.join(other, ".herd"))
        shutil.copytree(os.path.join(a.root, ".herd", "state"),
                        os.path.join(other, ".herd", "state"))
        state = os.path.join(other, ".herd", "state")
        runtime = json.loads(Path(state, "runtime.json").read_text())
        runtime["repo"] = other
        self.write(state, "runtime.json", runtime)
        self.register_native(self.herd_registry({"a": a.root, "b": other}))
        answer = self.answer(self.owner(a.rows))
        self.assertEqual(self.verified(answer), [])
        self.assertEqual({e["identity"]["reason"]
                          for e in self.runtime(answer)["agents"]},
                         {hts.ID_MULTIPLE_ROOTS})

    def test_a_stale_listing_verifies_nothing(self):
        boot = self.team("boot", None, task=False)
        real = catalog.CatalogService._capture_runtime

        def aged(service, *args, **kwargs):
            out = real(service, *args, **kwargs)
            self.clock.now += 3600
            return out
        with mock.patch.object(catalog.CatalogService, "_capture_runtime",
                               aged):
            answer = self.run_boot(boot)
        runtime = self.runtime(answer)
        self.assertEqual((runtime["state"], runtime["problem"]),
                         (contract.RUNTIME_AGENTS_UNAVAILABLE,
                          hts.PROBLEM_SNAPSHOT_NOT_FRESH))
        self.assertEqual(runtime["agents"], [])

    def test_a_binding_or_task_changed_between_passes_is_unverified(self):
        for which in ("role-bindings.json", "task.json"):
            with self.subTest(changed=which):
                self.fresh_registry("sources-" + which)
                team = self.team("c-" + which[:4], TASK_N, status="COMPLETE")
                state = os.path.join(team.root, ".herd", "state")

                def change():
                    value = json.loads(Path(state, which).read_text())
                    value["note"] = "changed between the source passes"
                    self.write(state, which, value)
                self.register_native(self.herd_registry({"c": team.root}))
                with self.between_passes(change):
                    answer = self.answer(self.owner(team.rows))
                self.assertEqual(self.verified(answer), [])
                self.assertEqual(
                    {e["identity"]["reason"]
                     for e in self.runtime(answer)["agents"]},
                    {hts.ID_CHANGED})

    def test_unreadable_symlinked_or_over_budget_input_fails_closed(self):
        boot = self.team("boot", None, task=False)
        bindings = os.path.join(boot.root, ".herd", "state",
                                "role-bindings.json")
        os.rename(bindings, bindings + ".real")
        os.symlink(bindings + ".real", bindings)
        answer = self.run_boot(boot)
        self.assertEqual(self.source(answer)["locators"]["excluded"],
                         [{"alias": "boot",
                           "reason": hts.EXCLUDED_STATE_REFUSED}])
        self.assertEqual(self.verified(answer), [])
        self.fresh_registry("sources-budget")
        boot2 = self.team("b2", None, task=False)
        with mock.patch.object(sources_module, "MAX_QUERY_INPUT_BYTES", 600):
            answer = self.run_boot(boot2)
        source = self.source(answer)
        self.assertEqual((source["health"], source["problem"]),
                         (contract.HEALTH_UNAVAILABLE,
                          sources_module.PROBLEM_QUERY_INPUT_BUDGET))
        runtime = self.runtime(answer)
        self.assertEqual(runtime["state"], contract.RUNTIME_AGENTS_UNAVAILABLE)
        self.assertTrue(runtime["problem"].startswith(
            hts.PROBLEM_SOURCE_NOT_FRESH))
        self.assertEqual(runtime["agents"], [])

    def test_unparseable_role_records_are_counted_never_read_as_no_roles(self):
        for which in ("runtime.json", "role-bindings.json"):
            with self.subTest(file=which):
                self.fresh_registry("sources-" + which)
                boot = self.team("bad-" + which[:4], None, task=False)
                self.write(os.path.join(boot.root, ".herd", "state"), which,
                           None, raw="[not, an object")
                answer = self.run_boot(boot)
                runtime = self.runtime(answer)
                self.assertEqual(runtime["counts"][
                    "roots_role_records_unreadable"], 1)
                self.assertEqual(self.verified(answer), [])

    def test_a_canonical_conflict_is_never_bypassed_by_a_matching_session(
            self):
        store = self.mission_state("mc")
        self.register(source_id="mc", store=store)
        _, workspace = self.running_mission(store, task_id=TASK_K)
        # The root now runs ANOTHER task than the Mission Core record names.
        team = self.team("w5k", TASK_N, root=workspace, mission=False)
        self.register_native(self.herd_registry({"w5k": workspace}))
        answer = self.answer(self.owner(team.rows))
        alias = self.by_ref(answer)[self.ref(TASK_N, workspace)]
        self.assertEqual(alias["binding"]["state"], contract.BINDING_AMBIGUOUS)
        self.assertEqual(alias["binding"]["reason"], "canonical_claim_conflict")
        self.assertEqual(alias["members"], [])
        self.assertEqual(self.verified(answer), [])
        self.assertEqual(
            {e["identity"]["state"] for e in self.runtime(answer)["agents"]},
            {contract.IDENTITY_AMBIGUOUS})

    def test_an_unreadable_canonical_source_leaves_membership_unverified(self):
        self.register(source_id="mc", store=os.path.join(self.root, "gone"))
        team = self.team("w5n", TASK_N)
        self.register_native(self.herd_registry({"w5n": team.root}))
        answer = self.answer(self.owner(team.rows))
        mission = self.by_ref(answer)[self.ref(TASK_N, team.root)]
        self.assertEqual(mission["binding"], {
            "state": contract.BINDING_UNVERIFIED,
            "reason": "canonical_source_not_fresh", "task_id": TASK_N})
        self.assertEqual(mission["members"], [])
        # Affirmative visibility: verified runtime identities with a
        # RECORDED, unverified assignment reference.
        entries = self.runtime(answer)["agents"]
        self.assertEqual(len(entries), 4)
        for entry in entries:
            self.assertEqual(entry["identity"]["state"], VERIFIED)
            self.assertEqual(entry["assignment"]["state"],
                             contract.ASSIGNMENT_UNVERIFIED_REFERENCE)
            self.assertEqual(entry["assignment"]["reason"],
                             "canonical_source_not_fresh")
            self.assertEqual(entry["assignment"]["task_id"], TASK_N)

    def test_an_unknown_status_is_never_active(self):
        team = self.team("w5n", TASK_N, status="PAUSED")
        self.register_native(self.herd_registry({"w5n": team.root}))
        answer = self.answer(self.owner(team.rows))
        mission = self.by_ref(answer)[self.ref(TASK_N, team.root)]
        self.assertEqual(mission["lifecycle"]["standing"],
                         contract.STANDING_UNKNOWN_FUTURE)
        self.assertEqual(mission["members"], [])
        entries = self.runtime(answer)["agents"]
        self.assertEqual(len(entries), 4)
        for entry in entries:
            self.assertEqual(entry["assignment"]["reason"],
                             hts.AS_STATUS_UNRECOGNIZED)


# ---------------------------------------------------------------------------
# Required 5: one rendering per agent; no laundering; read-only; schema.
# ---------------------------------------------------------------------------

class Required5Dedup(HerdrFixture):

    def test_a_team_member_recorded_again_elsewhere_renders_once(self):
        team = self.team("w5n", TASK_N)
        boot = os.path.realpath(os.path.join(self.root, "boot-copy"))
        os.makedirs(boot)
        # A bootstrap-only root whose records name the SAME live identities.
        self.herd(root=boot, task=False, agents=team.agents, panes=team.panes,
                  roles=json.loads(Path(team.root, ".herd", "state",
                                        "role-bindings.json").read_text())[
                      "roles"])
        self.register_native(self.herd_registry({"w5n": team.root,
                                                 "boot": boot}))
        answer = self.answer(self.owner(team.rows))
        self.assertEqual(len(self.members(answer)), 4)
        self.assertEqual(self.runtime(answer)["agents"], [])
        self.assertEqual(self.runtime(answer)["counts"][
            "represented_by_teams"], 8)
        self.assertEqual(len(self.characters(answer)), 4)

    def test_shared_path_aliases_make_one_team_with_every_alias_listed(self):
        team = self.team("w5n", TASK_N)
        self.register_native(self.herd_registry(
            {"w5n": team.root, "w5n-again": team.root, "zz": team.root}))
        answer = self.answer(self.owner(team.rows))
        missions = self.by_ref(answer)
        self.assertEqual(list(missions), [self.ref(TASK_N, team.root)])
        mission = missions[self.ref(TASK_N, team.root)]
        self.assertEqual(mission["producer"]["recorded"]["root_aliases"],
                         ["w5n", "w5n-again", "zz"])
        self.assertEqual(len(self.characters(answer)), 4)
        self.assertEqual(self.source(answer)["locators"][
            "shared_root_groups"], 1)

    def test_a_terminal_task_never_regains_active_membership(self):
        for status in ("COMPLETE", "ABORTED"):
            with self.subTest(status=status):
                self.fresh_registry("sources-" + status)
                team = self.team("t-" + status.lower(), TASK_N, status=status)
                self.register_native(self.herd_registry(
                    {"t": team.root}, name="reg-" + status))
                answer = self.answer(self.owner(team.rows))
                mission = self.by_ref(answer)[self.ref(TASK_N, team.root)]
                self.assertEqual(mission["lifecycle"]["standing"],
                                 contract.STANDING_TERMINAL)
                self.assertEqual(mission["members"], [])
                self.assertEqual(self.members(answer), [])
                entries = self.runtime(answer)["agents"]
                self.assertEqual(len(entries), 4)
                for entry in entries:
                    self.assertEqual(entry["identity"]["state"], VERIFIED)
                    self.assertEqual(entry["assignment"], {
                        "state": contract.ASSIGNMENT_UNASSIGNED,
                        "reason": hts.AS_NOT_CURRENT,
                        "task_ref": self.ref(TASK_N, team.root),
                        "task_id": TASK_N, "task_status": status})

    def test_the_registry_is_read_only_and_no_mutating_helper_runs(self):
        team = self.team("w5n", TASK_N)
        boot = self.team("boot", None, task=False)
        directory = self.herd_registry({"w5n": team.root, "boot": boot.root,
                                        "gone": "/nonexistent/removed"})
        self.register_native(directory)
        registry_file = os.path.join(directory, "repos.json")
        before = (Path(registry_file).read_bytes(),
                  os.stat(registry_file).st_mtime_ns,
                  sorted(os.listdir(directory)))
        states = {(root, name): Path(root, ".herd", "state", name).read_bytes()
                  for root in (team.root, boot.root)
                  for name in os.listdir(os.path.join(root, ".herd", "state"))}
        tripped = mock.Mock(side_effect=AssertionError("mutating helper"))
        with mock.patch.object(herd_registry, "registry_load", tripped), \
                mock.patch.object(herd_registry, "registry_save", tripped), \
                mock.patch.object(herd_registry, "register_repo", tripped):
            for version in (1, 2):
                self.answer(self.owner(team.rows, boot.rows, version=version))
        tripped.assert_not_called()
        self.assertEqual(before, (Path(registry_file).read_bytes(),
                                  os.stat(registry_file).st_mtime_ns,
                                  sorted(os.listdir(directory))))
        self.assertEqual(states, {
            key: Path(key[0], ".herd", "state", key[1]).read_bytes()
            for key in states})

    def test_both_contracts_carry_the_closed_runtime_agents_block(self):
        boot = self.team("boot", None, task=False)
        self.register_native(self.herd_registry({"boot": boot.root}))
        for version in (1, 2):
            with self.subTest(version=version):
                answer = self.answer(self.owner(boot.rows, version=version))
                response = answer["response"]
                keys = (contract.RESPONSE_KEYS if version == 1
                        else v2.RESPONSE_KEYS_V2)
                self.assertEqual(set(response), set(keys))
                self.assertIn("runtime_agents", keys)
                contract.validate_runtime_agents(response["runtime_agents"])
                if version == 2:
                    v2.validate_response_v2(response)
                self.assertEqual(len(self.verified(answer)), 4)
        block = answer["response"]["runtime_agents"]
        with self.assertRaises(contract.ContractError):
            contract.validate_runtime_agents(dict(block, extra=1))
        entry = dict(block["agents"][0])
        entry["assignment"] = dict(entry["assignment"], state="member")
        with self.assertRaises(contract.ContractError):
            contract.validate_runtime_agents(dict(block, agents=[entry]))
        with self.assertRaises(contract.ContractError):
            contract.validate_runtime_agents(dict(block, caveat=None))

    def test_no_task_native_source_means_not_applicable(self):
        store = self.mission_state("mc")
        self.register(source_id="mc", store=store)
        runtime = self.runtime(self.answer(self.owner()))
        self.assertEqual((runtime["state"], runtime["problem"]),
                         (contract.RUNTIME_AGENTS_NOT_APPLICABLE,
                          contract.PROBLEM_NO_TASK_NATIVE_SOURCE))


# ---------------------------------------------------------------------------
# Review round 7 (REJECT): F1 refused reads uncharged; F2 a bound team hides a
# role that is not in it.
# ---------------------------------------------------------------------------

class Round7F1RefusedReadsAreCharged(HerdrFixture):
    """Every hardened read is charged the bytes it ACTUALLY consumed, on every
    outcome, once: a refusal does real I/O too."""

    BOUND = hts.prepare_module.MAX_INSTRUCTION_FILE_BYTES

    def read(self, directory, allowance=10_000_000):
        source = {"source_id": "herd", "kind": contract.KIND_TASK_NATIVE,
                  "store_dir": directory, "producer": "herdctl",
                  "max_age_seconds": 60, "registered_at": NOW}
        budget = sources_module.QueryBudget(bytes_remaining=allowance)
        reading = hts.read_herdr_task(source, NOW, budget)
        return reading, allowance - budget.bytes_remaining

    def registry_bytes(self, directory):
        return os.path.getsize(os.path.join(directory, "repos.json"))

    def task_file(self, root, raw):
        path = os.path.join(root, ".herd", "state", "task.json")
        with open(path, "wb") as handle:
            handle.write(raw)

    def test_a_non_utf8_refusal_is_charged_its_exact_bytes(self):
        team = self.team("w5n", TASK_N)
        self.task_file(team.root, b"\xff" * 445)
        directory = self.herd_registry({"w5n": team.root})
        reading, spent = self.read(directory)
        self.assertEqual(reading["health"], contract.HEALTH_FRESH)
        self.assertEqual(reading["locators"]["excluded"], [
            {"alias": "w5n", "reason": hts.EXCLUDED_STATE_REFUSED}])
        self.assertEqual(spent, self.registry_bytes(directory) + 445)

    def test_an_over_bound_refusal_is_charged_the_bytes_read_not_the_size(self):
        team = self.team("w5n", TASK_N)
        self.task_file(team.root, b"{" + b" " * 200_000 + b"}")
        directory = self.herd_registry({"w5n": team.root})
        reading, spent = self.read(directory)
        # The primitive reads bound + 1 bytes, then refuses; the count it
        # returns is the file's SIZE (informational), never charged.
        self.assertEqual(spent, self.registry_bytes(directory) + self.BOUND + 1)

    def test_a_refused_chain_is_charged_the_upper_bound_a_read_could_take(self):
        team = self.team("w5n", TASK_N)
        state = os.path.join(team.root, ".herd", "state")
        moved = os.path.join(self.root, "state-elsewhere")
        os.rename(state, moved)
        os.symlink(moved, state)
        directory = self.herd_registry({"w5n": team.root})
        reading, spent = self.read(directory)
        self.assertEqual(reading["locators"]["excluded"], [
            {"alias": "w5n", "reason": hts.EXCLUDED_STATE_REFUSED}])
        self.assertEqual(spent, self.registry_bytes(directory) + self.BOUND + 1)

    def test_refused_reads_exhaust_the_shared_budget_explicitly(self):
        team = self.team("w5n", TASK_N)
        self.task_file(team.root, b"\xff" * 445)
        directory = self.herd_registry({"w5n": team.root})
        reading, _ = self.read(directory,
                               allowance=self.registry_bytes(directory) + 100)
        self.assertEqual((reading["health"], reading["problem"]),
                         (contract.HEALTH_UNAVAILABLE,
                          sources_module.PROBLEM_QUERY_INPUT_BUDGET))

    def test_an_absent_file_charges_nothing_and_a_read_once(self):
        boot = self.team("boot", None, task=False)
        directory = self.herd_registry({"boot": boot.root})
        _, spent = self.read(directory)
        state = os.path.join(boot.root, ".herd", "state")
        self.assertEqual(spent, self.registry_bytes(directory)
                         + os.path.getsize(os.path.join(state, "runtime.json"))
                         + os.path.getsize(os.path.join(state,
                                                        "role-bindings.json")))


class Round7F2BoundTeamNeverHidesANonMember(unittest.TestCase):
    """At a BOUND root a live role is skipped only when that role ITSELF is a
    member (exact agent and recorded session); any other live role at the
    same root is decided on its own evidence."""

    ROOT = "/synthetic/root-w5n"
    VIEW_REF = "herd:%s@%s" % (TASK_N, root_key(ROOT))

    def role(self, logical, agent, session, pane):
        entry = {"logical": logical, "agent": agent, "session": session,
                 "stable": stable(agent, pane, "w1")}
        return {"logical": logical, "role": logical.rstrip("1"),
                "agent": agent, "session": session, "pane_id": pane,
                "workspace_id": "w1", "entry": entry}

    def build(self):
        member = self.role("lead1", "w5n-lead1", "sess-lead", "w1:p3")
        outsider = self.role("helper1", "w5n-helper1", "sess-helper", "w1:p4")
        view = {"mission_ref": self.VIEW_REF, "source_id": "herd",
                "lifecycle": {"model": hts.LIFECYCLE_MODEL, "state": "ACTIVE",
                              "standing": contract.STANDING_ACTIVE},
                "execution": {"task_id": TASK_N,
                              "workspace_realpath": self.ROOT}}
        record = {"view": view, "root": self.ROOT, "aliases": ["w5n"],
                  "digests": ("t", "r", "b"),
                  "runtime_roles": {"lead1": "w5n-lead1",
                                    "helper1": "w5n-helper1"},
                  "bound": [member, outsider], "problem": None}
        observation = hts.Observation()
        for _ in range(2):
            observation.passes.append({"readings": [{
                "source_id": "herd", "at": NOW, "health": contract.HEALTH_FRESH,
                "problem": None, "requested": 1, "roots": [record]}]})
        # The BOUND team's roster holds the member only.
        mission = dict(view, binding={"state": contract.BINDING_BOUND,
                                      "reason": "canonical_chain_complete",
                                      "task_id": TASK_N},
                       members=[{"agent": "w5n-lead1",
                                 "session": {"recorded": "sess-lead",
                                             "live": "sess-lead"}}])
        rows = [{"agent": a, "provider": "claude", "status": "idle",
                 "session": s, "pane_id": p, "workspace_id": "w1"}
                for a, s, p in (("w5n-lead1", "sess-lead", "w1:p3"),
                                ("w5n-helper1", "sess-helper", "w1:p4"))]
        reading = contract.read_runtime_snapshot({
            "schema": contract.SNAPSHOT_SCHEMA,
            "version": contract.SNAPSHOT_VERSION, "observed_at": NOW,
            "producer": "test", "tasks": [], "agents": rows}, NOW)
        capture = {"native_cwd": {"w5n-lead1": ["/herd"],
                                  "w5n-helper1": ["/herd"]},
                   "requested": ["w5n-helper1", "w5n-lead1"],
                   "names_over_bound": 0, "over_capacity": []}
        return observation, [mission], reading, capture

    def test_a_live_non_member_at_a_bound_root_is_a_runtime_agent(self):
        observation, missions, reading, capture = self.build()
        block = hts.runtime_agents(observation, missions, reading, NOW, capture)
        entries = {e["agent"]: e for e in block["agents"]}
        # The genuine member renders once, as a member: never also runtime.
        self.assertNotIn("w5n-lead1", entries)
        self.assertEqual(block["counts"]["represented_by_teams"], 1)
        # The non-member is neither hidden nor counted as represented: it is
        # decided on its own evidence, with a truthful assignment.
        helper = entries["w5n-helper1"]
        self.assertEqual(helper["identity"], {"state": VERIFIED,
                                              "reason": hts.ID_PRESENT})
        self.assertEqual(helper["assignment"], {
            "state": contract.ASSIGNMENT_UNVERIFIED_REFERENCE,
            "reason": hts.AS_NOT_IN_TEAM_ROSTER, "task_ref": self.VIEW_REF,
            "task_id": TASK_N, "task_status": "ACTIVE"})
        self.assertEqual(block["counts"]["verified"], 1)
        contract.validate_runtime_agents(block)

    def test_a_non_member_with_a_mismatched_identity_is_unverified_not_hidden(
            self):
        observation, missions, reading, capture = self.build()
        capture["native_cwd"]["w5n-helper1"] = ["/elsewhere"]
        block = hts.runtime_agents(observation, missions, reading, NOW, capture)
        helper = {e["agent"]: e for e in block["agents"]}["w5n-helper1"]
        self.assertEqual(helper["identity"]["state"],
                         contract.IDENTITY_UNVERIFIED)
        self.assertEqual(block["counts"]["represented_by_teams"], 1)


# ---------------------------------------------------------------------------
# Review round 8 (REJECT): a degraded read must still settle the SHARED pass
# budget with what it consumed, exactly once, on every exit path.
# ---------------------------------------------------------------------------

class _BudgetHelpers(object):

    def source(self, directory, source_id="herd"):
        return {"source_id": source_id, "kind": contract.KIND_TASK_NATIVE,
                "store_dir": directory, "producer": "herdctl",
                "max_age_seconds": 60, "registered_at": NOW}

    def size(self, root, name):
        return os.path.getsize(os.path.join(root, ".herd", "state", name))

    def root_bytes(self, root):
        return sum(self.size(root, name) for name in (
            "task.json", "runtime.json", "role-bindings.json"))

    def registry_bytes(self, directory):
        return os.path.getsize(os.path.join(directory, "repos.json"))


class Round8SharedBudgetSettledOnEveryExit(_BudgetHelpers, HerdrFixture):

    def test_exhaustion_mid_source_debits_what_it_read_and_the_next_source_sees_it(
            self):
        one = self.team("x1", TASK_N)
        two = self.team("x2", TASK_CHILD)
        first = self.herd_registry({"a1": one.root, "a2": two.root},
                                   name="reg-first")
        other = self.team("y1", TASK_K)
        second = self.herd_registry({"b1": other.root}, name="reg-second")
        # Round 9 (HARD bound): room for the registry and root a1, then a2's
        # first read is REFUSED BEFORE it starts (the round-8 expectation of
        # an overdrawn -1 asserted the overrun the Supervisor ruled out).
        consumed = self.registry_bytes(first) + self.root_bytes(one.root)
        allowance = consumed + hts.READ_MAX - 1
        budget = sources_module.QueryBudget(bytes_remaining=allowance)
        missions_before = budget.missions_remaining
        reading = hts.read_herdr_task(self.source(first), NOW, budget)
        self.assertEqual((reading["health"], reading["problem"]),
                         (contract.HEALTH_UNAVAILABLE,
                          sources_module.PROBLEM_QUERY_INPUT_BUDGET))
        # The shared budget is debited by EVERYTHING this source read; no
        # view was accepted; the counter is never negative.
        self.assertEqual(budget.bytes_remaining, allowance - consumed)
        self.assertEqual(budget.bytes_remaining, hts.READ_MAX - 1)
        self.assertEqual(budget.missions_remaining, missions_before)
        # A following source in the SAME pass sees the reduced allowance:
        # with no room left it reads nothing and degrades explicitly.
        after = budget.bytes_remaining
        reading = hts.read_herdr_task(self.source(second, "herd2"), NOW,
                                      budget)
        self.assertEqual((reading["health"], reading["problem"]),
                         (contract.HEALTH_UNAVAILABLE,
                          sources_module.PROBLEM_QUERY_INPUT_BUDGET))
        self.assertEqual(budget.bytes_remaining, after)

    def test_a_non_budget_degradation_still_debits_what_it_read(self):
        directory = self.herd_registry({}, version=2)
        budget = sources_module.QueryBudget(bytes_remaining=1_000_000)
        reading = hts.read_herdr_task(self.source(directory), NOW, budget)
        self.assertEqual(reading["health"], contract.HEALTH_INCOMPATIBLE)
        self.assertEqual(budget.bytes_remaining,
                         1_000_000 - self.registry_bytes(directory))

    def test_a_v2_pass_settles_exactly_what_a_degraded_source_read(self):
        from mission_sources import query_v2
        directory = self.herd_registry({}, version=2)
        shared = query_v2.RefreshReadBudget(10_000_000)
        result = shared.pass_budget().read_source(
            self.source(directory), NOW, hts.READERS)
        self.assertEqual(result["health"], contract.HEALTH_INCOMPATIBLE)
        # Settled to the bytes actually read: never the whole hold.
        self.assertEqual(shared.charged, self.registry_bytes(directory))

    def test_success_settles_once_and_deducts_its_views(self):
        team = self.team("x1", TASK_N)
        directory = self.herd_registry({"a1": team.root})
        consumed = self.registry_bytes(directory) + self.root_bytes(team.root)
        budget = sources_module.QueryBudget(bytes_remaining=1_000_000)
        missions_before = budget.missions_remaining
        reading = hts.read_herdr_task(self.source(directory), NOW, budget)
        self.assertEqual(reading["health"], contract.HEALTH_FRESH)
        self.assertEqual(budget.bytes_remaining, 1_000_000 - consumed)
        self.assertEqual(budget.missions_remaining, missions_before - 1)
        from mission_sources import query_v2
        shared = query_v2.RefreshReadBudget(10_000_000)
        shared.pass_budget().read_source(self.source(directory), NOW,
                                         hts.READERS)
        self.assertEqual(shared.charged, consumed)

    def test_one_source_never_takes_more_than_a_store_read_bound(self):
        team = self.team("x1", TASK_N)
        directory = self.herd_registry({"a1": team.root})
        bound = self.registry_bytes(directory) + 10
        budget = sources_module.QueryBudget(bytes_remaining=1_000_000)
        with mock.patch.object(sources_module, "MAX_STORE_READ_BYTES", bound):
            reading = hts.read_herdr_task(self.source(directory), NOW, budget)
        self.assertEqual((reading["health"], reading["problem"]),
                         (contract.HEALTH_UNAVAILABLE,
                          sources_module.PROBLEM_QUERY_INPUT_BUDGET))

    def test_a_degraded_record_reports_what_was_requested(self):
        team = self.team("x1", TASK_N)
        directory = self.herd_registry({"a1": team.root, "a2": "/gone/a2"})
        absent = os.path.join(self.root, "no-registry")
        os.makedirs(absent)
        observation = hts.Observation()
        readers = observation.pass_readers()          # ONE pass, two sources
        # Room for the registry read only (round 9: a read needs READ_MAX).
        readers[contract.KIND_TASK_NATIVE](
            self.source(directory), NOW, sources_module.QueryBudget(
                bytes_remaining=self.registry_bytes(directory)
                + hts.READ_MAX - 1))
        readers[contract.KIND_TASK_NATIVE](
            self.source(absent, "herd2"), NOW, sources_module.QueryBudget())
        degraded, unread = observation.passes[0]["readings"]
        self.assertEqual(degraded["health"], contract.HEALTH_UNAVAILABLE)
        self.assertEqual(degraded["requested"], 2)  # registered, not accepted
        self.assertIsNone(unread["requested"])      # the registry was unread
        # The all-degraded pass says what was requested (2 known, 1 source
        # unknown) and that nothing was read into the answer.
        block = hts.runtime_agents(observation, [], None, NOW, {})
        self.assertEqual(block["state"], contract.RUNTIME_AGENTS_UNAVAILABLE)
        self.assertEqual(block["counts"]["roots_requested"], 2)
        self.assertEqual(block["counts"]["roots_read"], 0)


class Round8UnexpectedErrorStillSettlesAndRecords(_BudgetHelpers, HerdrFixture):

    def test_an_unexpected_reader_error_settles_and_is_never_not_applicable(
            self):
        team = self.team("x1", TASK_N)
        directory = self.herd_registry({"a1": team.root})
        observation = hts.Observation()
        readers = observation.pass_readers()
        budget = sources_module.QueryBudget(bytes_remaining=1_000_000)
        with mock.patch.object(hts, "_physical",
                               side_effect=RuntimeError("unexpected")):
            reading = sources_module.read_source(self.source(directory), NOW,
                                                 budget, readers)
        self.assertEqual((reading["health"], reading["problem"]),
                         (contract.HEALTH_MALFORMED,
                          sources_module.PROBLEM_READER_FAILED))
        self.assertEqual(budget.bytes_remaining,
                         1_000_000 - self.registry_bytes(directory))
        block = hts.runtime_agents(observation, [], None, NOW, {})
        self.assertEqual(block["state"], contract.RUNTIME_AGENTS_UNAVAILABLE)
        self.assertTrue(block["problem"].endswith(
            sources_module.PROBLEM_READER_FAILED))
        self.assertEqual(block["counts"]["roots_requested"], 1)


# ---------------------------------------------------------------------------
# Review round 9 (REJECT): the reader's budget is ONE HARD reservation in
# every applicable ledger. Proven with INDEPENDENT interception of the
# primitive (calls, every os.read requested/returned) and an independent
# re-statement of the charge rule, against the PRODUCTION v1 QueryBudget and
# v2 RefreshReadBudget / StageBudget / PassBudget / ReadHold.
# ---------------------------------------------------------------------------

from mission_sources import query_v2 as query_v2_module  # noqa: E402
from target_runtime import prepare as prepare_module  # noqa: E402
from target_runtime import state_artifact as state_artifact_module  # noqa: E402

READ_MAX = 65537                # stated here, independently of the code
MEASURED_STATUSES = ("read", "refused_non_utf8")
ZERO_STATUSES = ("absent", "refused_not_a_regular_file", "refused_hardlink",
                 "refused_unreadable")


class PhysicalReads(object):
    """Every primitive call and every os.read inside it, recorded at the
    primitive (never through the reader's accounting). ``allowance`` is the
    source's hold, computed by the TEST from the ledger before the source
    starts; ``violations`` lists any call that started without room."""

    def __init__(self, test):
        self.calls = []
        self.allowance = None
        self.violations = []
        self.inject = None          # (name, "before" | "after")
        real = prepare_module.read_workspace_instruction
        real_read = os.read
        spy = self

        def primitive(root, name):
            before = spy.expected_charge()
            if spy.allowance is not None and before + READ_MAX > spy.allowance:
                spy.violations.append((name, before, spy.allowance))
            call = {"name": name, "os_reads": [], "status": None,
                    "count": None, "raised": False}
            spy.calls.append(call)

            def counting(fd, n):
                data = real_read(fd, n)
                call["os_reads"].append((n, len(data)))
                return data
            if spy.inject == (name, "before"):
                call["raised"] = True
                raise RuntimeError("injected before I/O")
            with mock.patch.object(prepare_module.os, "read", counting):
                result = real(root, name)
            if spy.inject == (name, "after"):
                call["raised"] = True
                raise RuntimeError("injected after I/O")
            call["status"], call["count"] = result[0], result[1]
            return result
        patcher = mock.patch.object(prepare_module,
                                    "read_workspace_instruction", primitive)
        patcher.start()
        test.addCleanup(patcher.stop)

    @staticmethod
    def charge(call):
        """The DECLARED rule, restated independently of the code."""
        if call["raised"]:
            return READ_MAX                      # unknown: conservative
        if call["status"] in MEASURED_STATUSES:
            return call["count"]
        if call["status"] == "refused_over_bound":
            return READ_MAX                      # bound + 1, read exactly
        if call["status"] in ZERO_STATUSES:
            return 0
        return READ_MAX                          # escapes / unknown

    def conservative(self):
        return sum(READ_MAX for c in self.calls
                   if c["raised"] or c["status"] not in MEASURED_STATUSES
                   + ZERO_STATUSES + ("refused_over_bound",))

    def expected_charge(self):
        return sum(self.charge(c) for c in self.calls)

    def physical(self):
        return sum(n for c in self.calls for _, n in c["os_reads"])


class Round9BudgetInvariant(HerdrFixture):

    def setUp(self):
        super(Round9BudgetInvariant, self).setUp()
        self.spy = PhysicalReads(self)

    def source(self, directory, source_id="herd"):
        return {"source_id": source_id, "kind": contract.KIND_TASK_NATIVE,
                "store_dir": directory, "producer": "herdctl",
                "max_age_seconds": 60, "registered_at": NOW}

    def one_root(self, name="reg"):
        team = self.team("w5n", TASK_N)
        return team, self.herd_registry({"w5n": team.root}, name=name)

    def task_file(self, root, raw):
        with open(os.path.join(root, ".herd", "state", "task.json"),
                  "wb") as handle:
            handle.write(raw)

    # -- running one source against a production ledger ------------------

    def run_v1(self, directory, start, source_id="herd"):
        budget = sources_module.QueryBudget(bytes_remaining=start)
        missions = budget.missions_remaining
        writes = []
        real_setattr = object.__setattr__

        def counting(obj, name, value):
            if name == "bytes_remaining":
                writes.append(value)
            real_setattr(obj, name, value)
        self.spy.allowance = min(start, sources_module.MAX_STORE_READ_BYTES)
        with mock.patch.object(sources_module.QueryBudget, "__setattr__",
                               counting):
            reading = sources_module.read_source(
                self.source(directory, source_id), NOW, budget, hts.READERS)
        return {"reading": reading, "debit": start - budget.bytes_remaining,
                "missions": missions - budget.missions_remaining,
                "writes": writes, "budget": budget}

    def run_v2(self, directory, shared, source_id="herd"):
        before = shared.charged
        room = min(shared.remaining, sources_module.MAX_QUERY_INPUT_BYTES)
        self.spy.allowance = min(sources_module.MAX_STORE_READ_BYTES,
                                 room - 1)
        settles, fails = [], []
        real_settle = query_v2_module.ReadHold.settle
        real_failed = query_v2_module.ReadHold.failed

        def settle(hold, pulled):
            settles.append(pulled)
            return real_settle(hold, pulled)

        def failed(hold, before_any_byte=False):
            fails.append(before_any_byte)
            return real_failed(hold, before_any_byte)
        pass_budget = shared.pass_budget()
        missions = pass_budget.missions_remaining
        with mock.patch.object(query_v2_module.ReadHold, "settle", settle), \
                mock.patch.object(query_v2_module.ReadHold, "failed", failed):
            reading = pass_budget.read_source(
                self.source(directory, source_id), NOW, hts.READERS)
        return {"reading": reading, "debit": shared.charged - before,
                "missions": missions - pass_budget.missions_remaining,
                "settles": settles, "fails": fails}

    def assert_invariant(self, run, version):
        spy = self.spy
        self.assertEqual(spy.violations, [], "a read started without room")
        for call in spy.calls:
            self.assertLessEqual(sum(n for _, n in call["os_reads"]),
                                 READ_MAX)
            for requested, _ in call["os_reads"]:
                self.assertLessEqual(requested, READ_MAX)
        expected = spy.expected_charge()
        self.assertLessEqual(spy.physical(), expected)
        if spy.allowance is not None:
            self.assertLessEqual(expected, max(spy.allowance, 0))
        self.assertEqual(run["debit"], expected)
        if version == 1:
            # Reserve, then ONE settle: never more, never a negative counter.
            self.assertEqual(len(run["writes"]), 2, run["writes"])
            self.assertTrue(all(v >= 0 for v in run["writes"]))
        else:
            self.assertEqual(len(run["settles"]), 1, run["settles"])
            self.assertEqual(run["settles"][0], expected)  # never clipped
            self.assertEqual(run["fails"], [])
        account = run["reading"].get("read_account")
        if account is not None:
            self.assertEqual(account["measured_bytes"]
                             + account["conservative_bytes"], expected)
            self.assertEqual(account["conservative_bytes"],
                             spy.conservative())
            self.assertEqual(account["settled_bytes"], expected)
            self.assertEqual(account["reserve_per_read"], READ_MAX)
        fresh = run["reading"]["health"] == contract.HEALTH_FRESH
        self.assertEqual(run["missions"],
                         len(run["reading"]["missions"]) if fresh else 0)
        if not fresh:
            self.assertEqual(run["reading"]["missions"], [])

    def budget_refused(self, run):
        self.assertEqual((run["reading"]["health"], run["reading"]["problem"]),
                         (contract.HEALTH_UNAVAILABLE,
                          sources_module.PROBLEM_QUERY_INPUT_BUDGET))

    # -- the AVAILABLE ROOM column --------------------------------------

    def test_room_matrix_v1_and_v2(self):
        team, directory = self.one_root()
        registry = os.path.getsize(os.path.join(directory, "repos.json"))
        files = [os.path.getsize(os.path.join(team.root, ".herd", "state", n))
                 for n in ("task.json", "runtime.json", "role-bindings.json")]
        exact = registry + files[0] + files[1] + READ_MAX   # last read fits
        rows = [(0, 0, False), (1, 0, False), (READ_MAX - 1, 0, False),
                (READ_MAX, 1, False), (READ_MAX + 1, 1, False),
                (exact - 1, 3, False), (exact, 4, True)]
        for allowance, calls, fresh in rows:
            for version in (1, 2):
                with self.subTest(allowance=allowance, version=version):
                    self.spy.calls[:] = []
                    if version == 1:
                        run = self.run_v1(directory, allowance)
                    else:
                        run = self.run_v2(
                            directory,
                            query_v2_module.RefreshReadBudget(allowance + 1))
                    self.assertEqual(len(self.spy.calls), calls)
                    if fresh:
                        self.assertEqual(run["reading"]["health"],
                                         contract.HEALTH_FRESH)
                    else:
                        self.budget_refused(run)
                    self.assert_invariant(run, version)

    # -- the CONTENT / OUTCOME column (ample room) ------------------------

    def content_cases(self):
        def empty(root):
            self.task_file(root, b"")

        def exact_cap(root):
            head = json.dumps({"version": 1, "id": TASK_N, "status": "ACTIVE",
                               "repo": root, "started_at": NOW,
                               "description": ""})[:-2]
            pad = 65536 - len(head) - 2
            self.task_file(root, (head + "x" * pad + '"}').encode())

        def oversized(root):
            self.task_file(root, b"{" + b" " * 70000 + b"}")

        def absent(root):
            os.rename(os.path.join(root, ".herd", "state", "task.json"),
                      os.path.join(root, "task-moved-aside.json"))

        def symlinked(root):
            task = os.path.join(root, ".herd", "state", "task.json")
            os.rename(task, task + ".real")
            os.symlink(task + ".real", task)

        def non_utf8(root):
            self.task_file(root, b"\xff" * 445)

        def malformed(root):
            self.task_file(root, b"{not json")
        return {"empty": empty, "small": None, "exact_cap": exact_cap,
                "oversized": oversized, "absent": absent,
                "pre_read_refusal": symlinked, "non_utf8": non_utf8,
                "malformed_json": malformed}

    def test_content_matrix_v1_and_v2(self):
        expected_status = {
            "empty": "read", "small": "read", "exact_cap": "read",
            "oversized": "refused_over_bound", "absent": "absent",
            "pre_read_refusal": "refused_not_a_regular_file",
            "non_utf8": "refused_non_utf8", "malformed_json": "read"}
        for index, (name, edit) in enumerate(self.content_cases().items()):
            for version in (1, 2):
                with self.subTest(content=name, version=version):
                    self.spy.calls[:] = []
                    team = self.team("c%d%d" % (index, version), TASK_N)
                    if edit is not None:
                        edit(team.root)
                    directory = self.herd_registry(
                        {"r": team.root}, name="reg-%s-%d" % (name, version))
                    run = (self.run_v1(directory, 10_000_000) if version == 1
                           else self.run_v2(directory,
                                            query_v2_module.RefreshReadBudget(
                                                10_000_000)))
                    task_calls = [c for c in self.spy.calls
                                  if c["name"] == "task.json"]
                    self.assertEqual(task_calls[0]["status"],
                                     expected_status[name])
                    if name == "exact_cap":
                        self.assertEqual(task_calls[0]["os_reads"],
                                         [(READ_MAX, 65536)])
                    if name == "oversized":
                        self.assertEqual(task_calls[0]["os_reads"],
                                         [(READ_MAX, READ_MAX)])
                    if name == "pre_read_refusal":
                        self.assertEqual(task_calls[0]["os_reads"], [])
                    self.assert_invariant(run, version)

    def test_a_state_chain_change_after_a_real_read_keeps_a_labelled_hold(
            self):
        real = state_artifact_module._resolved_chain
        for version in (1, 2):
            with self.subTest(version=version):
                self.spy.calls[:] = []
                team = self.team("chain%d" % version, TASK_N)
                directory = self.herd_registry({"r": team.root},
                                               name="reg-chain-%d" % version)
                seen = {"n": 0}

                def changing(root, subdirs):
                    if subdirs == (".herd", "state") and root == team.root:
                        seen["n"] += 1
                        if seen["n"] == 2:          # after task.json's read
                            return "/changed"
                    return real(root, subdirs)
                with mock.patch.object(state_artifact_module,
                                       "_resolved_chain", changing):
                    run = (self.run_v1(directory, 10_000_000) if version == 1
                           else self.run_v2(directory,
                                            query_v2_module.RefreshReadBudget(
                                                10_000_000)))
                task = [c for c in self.spy.calls
                        if c["name"] == "task.json"][0]
                self.assertGreater(sum(n for _, n in task["os_reads"]), 0)
                self.assertEqual(task["status"], "read")  # primitive's own
                # The wrapper discarded it: the reader charged a LABELLED
                # conservative hold that covers the real read.
                account = run["reading"]["read_account"]
                self.assertEqual(account["conservative_bytes"], READ_MAX)
                self.assertEqual(
                    run["debit"],
                    self.spy.expected_charge() - task["count"] + READ_MAX)
                self.assertLessEqual(self.spy.physical(), run["debit"])

    def test_a_per_root_refusal_reads_no_content(self):
        team = self.team("w5n", TASK_N)
        link = os.path.join(self.root, "link-root")
        os.symlink(team.root, link)
        directory = self.herd_registry({"link": link})
        for version in (1, 2):
            with self.subTest(version=version):
                self.spy.calls[:] = []
                run = (self.run_v1(directory, 10_000_000) if version == 1
                       else self.run_v2(directory,
                                        query_v2_module.RefreshReadBudget(
                                            10_000_000)))
                self.assertEqual([c["name"] for c in self.spy.calls],
                                 ["repos.json"])
                self.assert_invariant(run, version)

    def test_an_injected_exception_before_or_after_io_settles_once(self):
        for when in ("before", "after"):
            for version in (1, 2):
                with self.subTest(when=when, version=version):
                    self.spy.calls[:] = []
                    team = self.team("x%s%d" % (when, version), TASK_N)
                    directory = self.herd_registry(
                        {"r": team.root}, name="reg-%s-%d" % (when, version))
                    self.spy.inject = ("task.json", when)
                    try:
                        run = (self.run_v1(directory, 10_000_000)
                               if version == 1 else
                               self.run_v2(directory,
                                           query_v2_module.RefreshReadBudget(
                                               10_000_000)))
                    finally:
                        self.spy.inject = None
                    self.assertEqual(
                        (run["reading"]["health"], run["reading"]["problem"]),
                        (contract.HEALTH_MALFORMED,
                         sources_module.PROBLEM_READER_FAILED))
                    if when == "before":
                        self.assertEqual(self.spy.calls[-1]["os_reads"], [])
                    else:
                        self.assertGreater(len(self.spy.calls[-1]["os_reads"]),
                                           0)
                    self.assert_invariant(run, version)

    # -- the SEQUENCE column ----------------------------------------------

    def test_v1_two_sources_share_a_pass_and_never_reclaim_room(self):
        one = self.team("x1", TASK_N)
        two = self.team("x2", TASK_CHILD)
        first = self.herd_registry({"a1": one.root, "a2": two.root},
                                   name="reg-first")
        other = self.team("y1", TASK_K)
        second = self.herd_registry({"b1": other.root}, name="reg-second")
        size = lambda root, n: os.path.getsize(
            os.path.join(root, ".herd", "state", n))
        registry = os.path.getsize(os.path.join(first, "repos.json"))
        root_one = sum(size(one.root, n) for n in (
            "task.json", "runtime.json", "role-bindings.json"))
        # Room for the registry and root a1, then a2's first read refuses.
        start = registry + root_one + READ_MAX - 1
        budget = sources_module.QueryBudget(bytes_remaining=start)
        self.spy.allowance = start
        first_reading = sources_module.read_source(
            self.source(first), NOW, budget, hts.READERS)
        self.assertEqual(first_reading["problem"],
                         sources_module.PROBLEM_QUERY_INPUT_BUDGET)
        self.assertEqual(start - budget.bytes_remaining,
                         self.spy.expected_charge())
        self.assertEqual(self.spy.expected_charge(), registry + root_one)
        calls_before = len(self.spy.calls)
        remaining = budget.bytes_remaining
        self.assertEqual(remaining, READ_MAX - 1)
        # The LATER source sees only what is left: less than one read.
        self.spy.allowance = remaining
        second_reading = sources_module.read_source(
            self.source(second, "herd2"), NOW, budget, hts.READERS)
        self.assertEqual(second_reading["problem"],
                         sources_module.PROBLEM_QUERY_INPUT_BUDGET)
        self.assertEqual(len(self.spy.calls), calls_before)    # no read
        self.assertEqual(budget.bytes_remaining, remaining)     # no reclaim
        self.assertEqual(self.spy.violations, [])

    def test_v2_two_passes_and_a_stage_consumer_share_one_refresh(self):
        team = self.team("x1", TASK_N)
        directory = self.herd_registry({"a1": team.root})
        self.register_native(directory)
        size = lambda n: os.path.getsize(
            os.path.join(team.root, ".herd", "state", n))
        registry = os.path.getsize(os.path.join(directory, "repos.json"))
        one_pass = registry + size("task.json") + size("runtime.json") \
            + size("role-bindings.json")
        composer = 1000
        # Pass 1 reads fully; a composer stage settles 1,000 bytes; pass 2
        # is then left LESS than one read's reservation: it reads nothing.
        # (pass 2's hold is then room - 1 = READ_MAX - 1: below one read).
        total = one_pass + composer + READ_MAX
        shared = query_v2_module.RefreshReadBudget(total)
        self.spy.allowance = min(sources_module.MAX_STORE_READ_BYTES,
                                 total - 1)
        views, _ = query_v2_module.discovery_input(
            self.registry, NOW, shared.pass_budget(), hts.READERS)
        self.assertEqual(len(views), 1)
        self.assertEqual(shared.charged, one_pass)
        self.assertEqual(self.spy.expected_charge(), one_pass)
        hold = shared.stage().hold(4096)
        hold.settle(composer)
        self.assertEqual(shared.charged, one_pass + composer)
        calls_before = len(self.spy.calls)
        self.spy.allowance = min(sources_module.MAX_STORE_READ_BYTES,
                                 shared.remaining - 1)
        response = query_v2_module.query_v2(
            self.registry, None, None, NOW, readers=hts.READERS,
            budget=shared.pass_budget())
        source = {s["source_id"]: s for s in response["sources"]}["herd"]
        self.assertEqual(source["problem"],
                         sources_module.PROBLEM_QUERY_INPUT_BUDGET)
        self.assertEqual(len(self.spy.calls), calls_before)   # no read
        self.assertEqual(shared.charged, one_pass + composer)  # no reclaim
        self.assertGreaterEqual(shared.summary()["reads_refused"], 1)
        self.assertEqual(self.spy.violations, [])

    def test_v2_near_empty_reservation_reads_nothing(self):
        team, directory = self.one_root()
        for room in (2, READ_MAX - 1, READ_MAX):
            with self.subTest(room=room):
                self.spy.calls[:] = []
                shared = query_v2_module.RefreshReadBudget(room)
                run = self.run_v2(directory, shared)
                self.budget_refused(run)
                self.assertEqual(self.spy.calls, [])
                self.assertEqual(shared.charged, 0)
                self.assert_invariant(run, 2)

    # -- callbacks, double close, labels, unknown counts ------------------

    def test_a_failing_record_callback_commits_bytes_and_rolls_back_views(
            self):
        team, directory = self.one_root()
        budget = sources_module.QueryBudget(bytes_remaining=10_000_000)
        missions = budget.missions_remaining
        self.spy.allowance = 10_000_000

        def failing(entry):
            raise RuntimeError("record callback failed")
        with self.assertRaises(RuntimeError):
            hts._read(self.source(directory), NOW, budget, failing)
        self.assertEqual(10_000_000 - budget.bytes_remaining,
                         self.spy.expected_charge())   # bytes stay debited
        self.assertEqual(budget.missions_remaining, missions)  # rolled back
        # Through the production path the source then publishes NO facts.
        reading = sources_module.read_source(
            self.source(directory), NOW,
            sources_module.QueryBudget(bytes_remaining=10_000_000),
            {contract.KIND_TASK_NATIVE: lambda s, n, b: hts._read(
                s, n, b, failing)})
        self.assertEqual((reading["health"], reading["missions"]),
                         (contract.HEALTH_MALFORMED, []))

    def test_double_close_is_a_no_op_and_close_is_never_missed(self):
        budget = sources_module.QueryBudget(bytes_remaining=500_000)
        reservation = hts._Reservation(budget)
        self.assertEqual(budget.bytes_remaining,
                         500_000 - reservation.allowance)
        reservation.settle()
        reservation.settle()
        self.assertEqual(budget.bytes_remaining, 500_000)
        shared = query_v2_module.RefreshReadBudget(500_000)
        settles = []
        real = query_v2_module.ReadHold.settle

        def settle(hold, pulled):
            settles.append(pulled)
            return real(hold, pulled)
        with mock.patch.object(query_v2_module.ReadHold, "settle", settle):
            shared.pass_budget().read_source(
                self.source(self.herd_registry({}, name="reg-empty")), NOW,
                {contract.KIND_TASK_NATIVE: self.close_twice})
        self.assertEqual(len(settles), 1)          # one settle, not two
        self.assertEqual(shared.charged, 0)        # nothing read, all released

    @staticmethod
    def close_twice(source, now, budget):
        reservation = hts._Reservation(budget)    # inside the real ReadHold
        reservation.settle()
        reservation.settle()
        return sources_module._reading(source, now, contract.HEALTH_FRESH,
                                       None, [])

    def test_a_v2_hold_refused_before_the_reader_is_unknown_not_absent(self):
        """The production v2 pass refuses the hold BEFORE the reader runs:
        the runtime block must still know a task-native source was there
        (unavailable, its registry unread), never "no task-native source"."""
        from target_runtime import catalog as catalog_module
        team, directory = self.one_root()
        self.register_native(directory)
        # Room 0: the pass refuses the hold and the reader NEVER runs; 1, 2
        # and READ_MAX: the reader runs and refuses before any read.
        for room in (0, 1, 2, READ_MAX):
            with self.subTest(room=room):
                self.spy.calls[:] = []
                observation = hts.Observation()
                shared = query_v2_module.RefreshReadBudget(room)
                response = query_v2_module.query_v2(
                    self.registry, None, None, NOW,
                    readers=observation.pass_readers(),
                    budget=shared.pass_budget(),
                    runtime_agents=catalog_module._runtime_agents_hook(
                        observation, {}))
                source = {s["source_id"]: s
                          for s in response["sources"]}["herd"]
                self.assertEqual(source["problem"],
                                 sources_module.PROBLEM_QUERY_INPUT_BUDGET)
                self.assertEqual(self.spy.calls, [])          # nothing read
                block = response["runtime_agents"]
                self.assertEqual(block["state"],
                                 contract.RUNTIME_AGENTS_UNAVAILABLE)
                self.assertNotEqual(block["problem"],
                                    contract.PROBLEM_NO_TASK_NATIVE_SOURCE)
                self.assertEqual(block["counts"]["registries_unread"], 1)
                self.assertEqual(block["counts"]["roots_requested"], 0)

    def test_an_unread_registry_is_counted_unknown_never_zero(self):
        team, directory = self.one_root()
        absent = os.path.join(self.root, "no-registry")
        os.makedirs(absent)
        observation = hts.Observation()
        readers = observation.pass_readers()
        readers[contract.KIND_TASK_NATIVE](self.source(directory), NOW,
                                           sources_module.QueryBudget())
        readers[contract.KIND_TASK_NATIVE](self.source(absent, "herd2"), NOW,
                                           sources_module.QueryBudget())
        block = hts.runtime_agents(observation, [], None, NOW, {})
        self.assertEqual(block["counts"]["roots_requested"], 1)
        self.assertEqual(block["counts"]["registries_unread"], 1)


# ---------------------------------------------------------------------------
# Locator, discovery and registry regressions.
# ---------------------------------------------------------------------------

class LocatorRegressions(HerdrFixture):

    def test_a_new_root_is_discovered_automatically_on_the_next_refresh(self):
        first = self.team("one", TASK_N)
        second = self.team("two", TASK_CHILD)
        self.register_native(self.herd_registry({"one": first.root}))
        owner = self.owner(first.rows, second.rows)
        before = self.answer(owner)
        self.assertEqual(set(self.by_ref(before)),
                         {self.ref(TASK_N, first.root)})
        # ``herdctl init`` registers the new repository; nobody registers a
        # source or a root by hand.
        self.herd_registry({"one": first.root, "two": second.root})
        after = self.answer(owner)
        missions = self.by_ref(after)
        self.assertEqual(set(missions), {self.ref(TASK_N, first.root),
                                         self.ref(TASK_CHILD, second.root)})
        self.assertIn(second.root, after["runtime_capture"]["discovery"][
            "roots"])
        self.assertEqual(len(missions[self.ref(TASK_CHILD, second.root)][
            "members"]), 4)

    def test_a_replaced_task_is_a_new_record_and_the_old_one_is_gone(self):
        team = self.team("w5n", TASK_N)
        self.register_native(self.herd_registry({"w5n": team.root}))
        owner = self.owner(team.rows)
        self.assertIn(self.ref(TASK_N, team.root), self.by_ref(
            self.answer(owner)))
        self.write(os.path.join(team.root, ".herd", "state"), "task.json", {
            "version": 1, "id": TASK_CHILD, "status": "ACTIVE",
            "description": "SYNTHETIC", "repo": team.root, "started_at": NOW})
        after = self.by_ref(self.answer(owner))
        self.assertNotIn(self.ref(TASK_N, team.root), after)
        self.assertIn(self.ref(TASK_CHILD, team.root), after)

    def test_a_replaced_session_is_replaced_never_present(self):
        team = self.team("w5n", TASK_N)
        team.rows[0]["agent_session"]["value"] = "sess-new"
        self.register_native(self.herd_registry({"w5n": team.root}))
        mission = self.by_ref(self.answer(self.owner(team.rows)))[
            self.ref(TASK_N, team.root)]
        classes = sorted(m["classification"] for m in mission["members"])
        self.assertEqual(classes.count(contract.MEMBER_REPLACED), 1)

    def test_two_roots_claiming_one_task_are_ambiguous_never_resolved(self):
        a = self.team("a", TASK_N)
        b = self.team("b", TASK_N)
        c = self.team("c", TASK_CHILD)
        self.register_native(self.herd_registry({"a": a.root, "b": b.root,
                                                 "c": c.root}))
        answer = self.answer(self.owner(a.rows, b.rows, c.rows))
        missions = self.by_ref(answer)
        for root in (a.root, b.root):
            mission = missions[self.ref(TASK_N, root)]
            self.assertEqual(mission["binding"], {
                "state": contract.BINDING_AMBIGUOUS,
                "reason": "task_claimed_by_multiple_missions",
                "task_id": TASK_N})
            self.assertEqual(mission["members"], [])
        self.assertEqual(len(missions[self.ref(TASK_CHILD, c.root)][
            "members"]), 4)
        # Their live roles are never verified characters either.
        self.assertEqual(self.verified(answer), [])

    def test_unrelated_native_agents_never_become_anything(self):
        team = self.team("w5n", TASK_N)
        self.register_native(self.herd_registry({"w5n": team.root}))
        extra = [engine_row(name="someone-else", session="s2", pane="w8:p2",
                            workspace="w8")]
        answer = self.answer(self.owner(team.rows, extra_rows=extra))
        self.assertEqual(len(self.characters(answer)), 4)
        self.assertEqual(answer["response"]["unmatched_runtime_agent_count"], 1)
        self.assertEqual(self.runtime(answer)["agents"], [])

    def test_an_absent_registry_is_unavailable_never_empty(self):
        directory = os.path.join(self.root, "no-registry")
        os.makedirs(directory)
        self.register_native(directory)
        source = self.source(self.answer(self.owner()))
        self.assertEqual((source["health"], source["problem"]),
                         (contract.HEALTH_UNAVAILABLE,
                          hts.PROBLEM_REGISTRY_ABSENT))

    def test_symlinked_traversing_relative_or_absent_roots_are_accounted(self):
        team = self.team("w5n", TASK_N)
        link = os.path.join(self.root, "link-to-repo")
        os.symlink(team.root, link)
        self.register_native(self.herd_registry({
            "good": team.root, "link": link,
            "trav": team.root + "/../repo-w5n", "rel": "repo-w5n",
            "gone": os.path.join(self.root, "deleted")}))
        answer = self.answer(self.owner(team.rows))
        source = self.source(answer)
        self.assertEqual(source["health"], contract.HEALTH_FRESH)
        self.assertEqual(source["locators"]["excluded"], [
            {"alias": "gone", "reason": hts.EXCLUDED_ROOT_ABSENT},
            {"alias": "link", "reason": hts.EXCLUDED_ROOT_REFUSED},
            {"alias": "rel", "reason": hts.EXCLUDED_ENTRY_INVALID},
            {"alias": "trav", "reason": hts.EXCLUDED_ENTRY_INVALID}])
        self.assertEqual(set(self.by_ref(answer)),
                         {self.ref(TASK_N, team.root)})
        self.assertEqual(len(self.characters(answer)), 4)

    def test_a_symlinked_task_file_is_excluded_with_its_reason(self):
        team = self.team("w5n", TASK_N)
        task = os.path.join(team.root, ".herd", "state", "task.json")
        os.rename(task, task + ".real")
        os.symlink(task + ".real", task)
        self.register_native(self.herd_registry({"w5n": team.root}))
        answer = self.answer(self.owner(team.rows))
        self.assertEqual(self.source(answer)["locators"]["excluded"], [
            {"alias": "w5n", "reason": hts.EXCLUDED_STATE_REFUSED}])
        self.assertEqual(self.characters(answer), [])

    def test_too_many_registered_roots_is_unavailable_never_truncated(self):
        roots = {"r%04d" % i: "/synthetic/r%04d" % i
                 for i in range(hts.MAX_REGISTERED_ROOTS + 1)}
        self.register_native(self.herd_registry(roots))
        source = self.source(self.answer(self.owner()))
        self.assertEqual(source["problem"], hts.PROBLEM_REGISTRY_OVER_BOUND)

    def test_an_unsupported_registry_version_is_incompatible(self):
        self.register_native(self.herd_registry({}, version=2))
        source = self.source(self.answer(self.owner()))
        self.assertEqual((source["health"], source["problem"]),
                         (contract.HEALTH_INCOMPATIBLE,
                          hts.PROBLEM_REGISTRY_VERSION))

    def test_the_reader_never_imports_the_mutating_registry_module(self):
        text = Path(hts.__file__).read_text()
        self.assertNotIn("from herdr import registry", text)
        self.assertNotIn("registry_load(", text)


class VocabularyTests(unittest.TestCase):

    def test_terminal_statuses_are_the_engines_own(self):
        for status in ("COMPLETE", "COMPLETED", "ABANDONED", "FAILED",
                       "ABORTED"):
            self.assertEqual(hts.standing(status), contract.STANDING_TERMINAL)
        self.assertEqual(hts.standing("ACTIVE"), contract.STANDING_ACTIVE)
        self.assertEqual(hts.standing("ERROR"), contract.STANDING_INACTIVE)
        self.assertEqual(hts.standing("PAUSED"),
                         contract.STANDING_UNKNOWN_FUTURE)

    def test_the_no_current_task_literal_is_the_engines_own(self):
        class Herd(object):
            herd_root = Path(os.path.join(os.sep, "nonexistent-herd-root"))
        self.assertEqual(engine_tasks.load_task(Herd())["status"],
                         hts.NO_CURRENT_TASK)

    def test_the_schema_is_explicit_and_mission_sources_stays_neutral(self):
        self.assertEqual((hts.SCHEMA, hts.SCHEMA_VERSION),
                         ("di.catalog.herdr_task", 1))
        self.assertEqual(hts.KIND, "task_native")
        self.assertIn(hts.KIND, contract.SOURCE_KINDS)
        self.assertEqual(hts.LIFECYCLE_MODEL, "herdr.task/1")
        self.assertNotIn(hts.LIFECYCLE_MODEL,
                         contract.CANONICAL_LIFECYCLE_MODELS)

    def test_the_capture_capacity_is_unchanged(self):
        self.assertEqual(runtime_snapshot.MAX_HERD_ROOTS, 16)


@unittest.skipUnless(os.environ.get("DI_MEASURE_HERDR_TASK"),
                     "measures refresh cost only when asked")
class MeasureRefreshCost(MixedRegistry, HerdrFixture):
    """Measured on the machine that runs it: wall-clock of a full refresh
    (two source passes, one capture, the answer) for an ordinary registry
    and the crowded mixed registry, contracts 1 and 2."""

    def measure(self, rows, extra, version):
        samples, answer = [], None
        for _ in range(5):
            owner = self.owner(rows, extra_rows=extra, version=version)
            started = time.perf_counter()
            answer = self.answer(owner)
            samples.append(round(time.perf_counter() - started, 4))
        return samples, answer

    def test_measure(self):
        out = os.environ["DI_MEASURE_HERDR_TASK"]
        results = {}
        ordinary = self.team("ord", TASK_N)
        self.register_native(self.herd_registry({"ord": ordinary.root},
                                                name="ordinary"))
        for version in (1, 2):
            samples, answer = self.measure(ordinary.rows, (), version)
            results["ordinary_v%d" % version] = {
                "seconds": samples,
                "answer_bytes": len(json.dumps(answer["response"])),
                "registered_aliases": 1,
                "characters": len(self.characters(answer))}
        self.fresh_registry("crowded-sources")
        live, live_rows, extra = self.build()
        for version in (1, 2):
            samples, answer = self.measure(live_rows, extra, version)
            results["crowded_v%d" % version] = {
                "seconds": samples,
                "answer_bytes": len(json.dumps(answer["response"])),
                "registered_aliases": 98,
                "roots_read": self.runtime(answer)["counts"]["roots_read"],
                "characters": len(self.characters(answer)),
                "members": len(self.members(answer)),
                "verified_runtime": len(self.verified(answer))}
        with open(os.path.join(out, "refresh-cost.json"), "w") as handle:
            json.dump(results, handle, indent=2, sort_keys=True)
            handle.write("\n")


# ---------------------------------------------------------------------------
# Publication cleanup (D1): the exported answers carry no machine-specific
# temporary root. The REAL serializer emission is taken from a fixture tree
# under this run's temp root and then mapped, deterministically, onto ONE
# synthetic root; every identity derived from a path is recomputed with the
# product's own ``hts.root_key``, and every list the product orders by such
# an identity is re-ordered by the same rule.
# ---------------------------------------------------------------------------

#: Clearly synthetic and portable: no machine has it, nothing may treat it as
#: a registry or a filesystem.
SYNTHETIC_FIXTURE_ROOT = "/__synthetic__/di-fixture-root"
_HEX12 = re.compile(r"(?<![0-9a-f])[0-9a-f]{12}(?![0-9a-f])")
_HEX64 = re.compile(r"(?<![0-9a-f])[0-9a-f]{64}(?![0-9a-f])")


def _map_strings(value, fn):
    if isinstance(value, dict):
        return dict((fn(k), _map_strings(v, fn)) for k, v in value.items())
    if isinstance(value, list):
        return [_map_strings(v, fn) for v in value]
    return fn(value) if isinstance(value, str) else value


def journal_digests(store_dir, temp_root, synthetic=SYNTHETIC_FIXTURE_ROOT):
    """``{exported digest: normalized digest}`` for every Mission Core
    Mission in the fixture's store: its head ``journal_digest_sha256`` is
    computed by the PRODUCT (``mission.journal.head_cursor``) over the
    store's records, which carry the temp root; the normalized digest is
    the same derivation over the same records with the root mapped."""
    from mission import journal
    with open(os.path.join(store_dir, "missions.json")) as handle:
        raw = json.load(handle)
    mapped = _map_strings(raw, lambda v: v.replace(temp_root, synthetic))
    digests = {}
    for mission_id in raw["missions"]:
        state = raw["mission_state"].get(mission_id)
        old = journal.head_cursor(raw["missions"][mission_id], state)[
            "journal_digest_sha256"]
        new = journal.head_cursor(
            mapped["missions"][mission_id],
            mapped["mission_state"].get(mission_id))["journal_digest_sha256"]
        digests[old] = new
    return digests
_ORDERED_BY = ("mission_ref", "agent_ref", "member_ref")


def normalize_export(answer, temp_root, synthetic=SYNTHETIC_FIXTURE_ROOT,
                     digests=None):
    """``(normalized, report)``: ``answer`` with ``temp_root`` (this run's
    realpath'd fixture root) replaced by ``synthetic`` — a prefix-only
    mapping, so distinct roots stay distinct — and every 12-hex root key of
    a path under it recomputed (``hts.root_key`` of the mapped path), then
    every list of records the product ordered by ``mission_ref`` /
    ``agent_ref`` / ``member_ref`` re-ordered by the recomputed value.
    ``digests`` (``journal_digests``): every exported Mission Core journal
    digest MUST be one of its keys — it is replaced by the product's own
    digest of the normalized records; an unknown one fails."""
    paths = set()

    def collect(value):
        if isinstance(value, dict):
            for key, item in value.items():
                collect(key)
                collect(item)
        elif isinstance(value, list):
            for item in value:
                collect(item)
        elif isinstance(value, str) and value.startswith(temp_root):
            paths.add(value)
    collect(answer)
    keys = {}
    for path in paths:
        old = hts.root_key(path)
        new = hts.root_key(synthetic + path[len(temp_root):])
        if keys.get(old, new) != new:
            raise AssertionError("one root key for two paths: %s" % old)
        keys[old] = new
    if len(set(keys.values())) != len(keys) or set(keys) & set(keys.values()):
        raise AssertionError("recomputed root keys collide")

    digests = digests or {}
    exported = set(re.findall(r'"journal_digest_sha256": "([0-9a-f]{64})"',
                              json.dumps(answer)))
    if exported - set(digests):
        raise AssertionError("journal digests not recomputed: %s"
                             % sorted(exported - set(digests)))

    def text(value):
        value = value.replace(temp_root, synthetic)
        value = _HEX64.sub(lambda m: digests.get(m.group(0), m.group(0)),
                           value)
        return _HEX12.sub(lambda m: keys.get(m.group(0), m.group(0)), value)

    reordered = []

    def walk(value):
        if isinstance(value, dict):
            return dict((text(k), walk(v)) for k, v in value.items())
        if isinstance(value, list):
            items = [walk(v) for v in value]
            for field in _ORDERED_BY:
                if value and all(isinstance(v, dict) and field in v
                                 for v in value):
                    original = [v[field] for v in value]
                    if original == sorted(original):
                        items.sort(key=lambda v: v[field])
                        reordered.append(field)
                    break
            return items
        if isinstance(value, str):
            return text(value)
        return value
    normalized = walk(answer)
    if temp_root in json.dumps(normalized):
        raise AssertionError("the temp root survived normalization")
    return normalized, {"paths": len(paths), "root_keys": len(keys),
                        "journal_digests": len(exported),
                        "lists_reordered": len(reordered)}


def assert_root_keys_coherent(test, answer):
    """Every herd identity names the root key of its OWN recorded path:
    missions (``…@<key>``) and runtime agents (``<source>:<key>#…``)."""
    response = answer["response"]
    checked = 0
    for mission in response["missions"]:
        path = mission["execution"]["workspace_realpath"]
        if path and mission["source_id"] == "herd":
            test.assertTrue(mission["mission_ref"].endswith(
                "@" + hts.root_key(path)), mission["mission_ref"])
            checked += 1
    for agent in (response.get("runtime_agents") or {}).get("agents", []):
        test.assertEqual(agent["agent_ref"].split(":")[1].split("#")[0],
                         hts.root_key(agent["workspace_realpath"]))
        checked += 1
    for listing, field in ((response["missions"], "mission_ref"),
                           ((response.get("runtime_agents") or {}).get(
                               "agents", []), "agent_ref")):
        values = [v[field] for v in listing]
        test.assertEqual(values, sorted(values))
    return checked


class NormalizedExportIsCoherent(MixedRegistry, HerdrFixture):
    """D1 regression: the normalized export keeps no temp root, every herd
    identity names the root key of its own (synthetic) path, the product's
    ordering holds, and distinct roots and records stay distinct."""

    def test_normalized_answers_are_coherent(self):
        live, live_rows, extra = self.build()
        temp_root = os.path.realpath(self.root)
        for version in (1, 2):
            answer = self.answer(self.owner(live_rows, extra_rows=extra,
                                            version=version))
            digests = journal_digests(os.path.join(self.root, "stores", "mc"),
                                      temp_root)
            normalized, report = normalize_export(answer, temp_root,
                                                  digests=digests)
            # Repeatable: the SAME input bytes always give the SAME output.
            again, _ = normalize_export(json.loads(json.dumps(answer)),
                                        temp_root, digests=digests)
            self.assertEqual(json.dumps(again, sort_keys=True),
                             json.dumps(normalized, sort_keys=True))
            text = json.dumps(normalized)
            self.assertNotIn(temp_root, text)
            self.assertNotIn("/var/folders/", text)
            self.assertIn(SYNTHETIC_FIXTURE_ROOT, text)
            self.assertGreater(report["root_keys"], 0)
            # The product's digest of the RAW records is what was exported
            # (the derivation is the right one), and it was replaced.
            exported = json.dumps(answer)
            for old, new in digests.items():
                if old in exported:
                    self.assertIn(new, text)
                    if new != old:            # path-dependent: replaced
                        self.assertNotIn(old, text)
            if version == 2:
                self.assertEqual(report["journal_digests"], 1)
            self.assertGreater(assert_root_keys_coherent(self, normalized), 0)
            # Distinct records stay distinct: as many missions and agents,
            # each with its own identity, as the raw emission had.
            for listing, field in (("missions", "mission_ref"),):
                before = [v[field] for v in answer["response"][listing]]
                after = [v[field] for v in normalized["response"][listing]]
                self.assertEqual(len(set(after)), len(set(before)))
            agents = normalized["response"]["runtime_agents"]["agents"]
            self.assertEqual(len(set(a["agent_ref"] for a in agents)),
                             len(answer["response"]["runtime_agents"]
                                 ["agents"]))
            if version == 2:
                v2.validate_response_v2(normalized["response"])


@unittest.skipUnless(os.environ.get("DI_EXPORT_HERDR_TASK_ANSWERS"),
                     "exports the consumer fixtures only when asked")
class ExportForConsumerTests(MixedRegistry, HerdrFixture):
    """Writes REAL serializer-emitted answers (v1 and v2) of the mixed
    registry for the office's consumer tests, into the named directory,
    NORMALIZED (``normalize_export``): no machine temp root, every path
    derived identity recomputed with the product's ``hts.root_key``."""

    def test_export(self):
        out = os.environ["DI_EXPORT_HERDR_TASK_ANSWERS"]
        live, live_rows, extra = self.build()
        temp_root = os.path.realpath(self.root)
        for version in (1, 2):
            answer = self.answer(self.owner(live_rows, extra_rows=extra,
                                            version=version))
            answer, _ = normalize_export(
                answer, temp_root,
                digests=journal_digests(
                    os.path.join(self.root, "stores", "mc"), temp_root))
            assert_root_keys_coherent(self, answer)
            with open(os.path.join(out, "herdr-task-answer-v%d.json"
                                   % version), "w") as handle:
                json.dump(answer, handle, indent=2, sort_keys=True)
                handle.write("\n")


class ObjectiveExtractionTests(unittest.TestCase):
    """feature13: the display objective of a formatted ``herdctl task --mission``.

    The formatter writes the description as ``OBJECTIVE\n<goal>`` followed by
    optional ``CONSTRAINTS`` / ``RULES`` / ``ACCEPTANCE CRITERIA`` /
    ``VERIFICATION`` sections, so the first non-empty line is the HEADING. These
    cases drive the ACTUAL formatter (``herdctl.task`` with ``--mission``, its
    dispatch seam captured: nothing is dispatched, no Mission or agent is
    started) and feed the exact text it produced through ``hts._objective``, so
    producer and consumer cannot drift apart silently. All content is SYNTHETIC.
    """

    @staticmethod
    def formatted(mission):
        """The description ``herdctl task --mission`` would dispatch for ``mission``."""
        import herdctl
        from types import SimpleNamespace
        control_plane = mock.Mock()
        with mock.patch.object(herdctl, "load_mission", return_value=mission), \
                mock.patch.object(herdctl, "resolve_repo_ref",
                                  return_value=Path("/tmp/synthetic-demo")), \
                mock.patch.object(herdctl, "HerdrControlPlane",
                                  return_value=control_plane):
            herdctl.task(SimpleNamespace(
                repo=None, text=None, mission=True, rejection_drill=False,
                rule=[]))
        control_plane.dispatch_task.assert_called_once()
        return control_plane.dispatch_task.call_args[0][1]

    @staticmethod
    def first_line_before(description):
        """The rule BEFORE this change (kept here to state what the defect was)."""
        for line in description.splitlines():
            if line.strip():
                return line.strip()
        return None

    def test_a_real_formatted_row_yields_its_goal_not_its_heading(self):
        text = self.formatted({
            "objective": "  Migrate the SYNTHETIC ingestion service.  ",
            "constraints": ["Do not change the schema"],
            "rules": ["Preserve compatibility"],
            "acceptance_criteria": ["Tests pass"],
            "verification": ["python3 -m unittest"],
        })
        self.assertEqual(text.splitlines()[0], "OBJECTIVE")
        # BEFORE: the heading (the defect). AFTER: the goal.
        self.assertEqual(self.first_line_before(text), "OBJECTIVE")
        self.assertEqual(hts._objective(text),
                         "Migrate the SYNTHETIC ingestion service.")

    def test_every_optional_section_leaves_the_goal_unchanged(self):
        goal = "Fix the SYNTHETIC authentication bug"
        for keys in ((), ("constraints",), ("rules",), ("acceptance_criteria",),
                     ("verification",),
                     ("constraints", "rules", "acceptance_criteria",
                      "verification")):
            mission = {"objective": goal}
            for key in keys:
                mission[key] = ["a bullet"]
            self.assertEqual(hts._objective(self.formatted(mission)), goal,
                             keys)

    def test_an_empty_objective_falls_to_unknown_never_the_next_heading(self):
        for objective in ("", "   ", None):
            text = self.formatted({"objective": objective,
                                   "constraints": ["Do not change the schema"],
                                   "rules": ["Preserve compatibility"]})
            self.assertIn("\nCONSTRAINTS\n", text)
            self.assertIsNone(hts._objective(text), repr(objective))

    def test_a_heading_only_description_is_unknown(self):
        self.assertIsNone(hts._objective("OBJECTIVE"))
        self.assertIsNone(hts._objective("OBJECTIVE\n"))
        self.assertIsNone(hts._objective("\n  OBJECTIVE  \n\n   \n"))
        # Formatted from a record that has only a heading's worth of content.
        self.assertIsNone(hts._objective(self.formatted({"objective": ""})))

    def test_ordinary_free_text_is_preserved_exactly(self):
        for text, want in (
                ("Fix auth\nmore detail", "Fix auth"),
                ("\n\n   Fix auth   \nmore", "Fix auth"),
                ("Fix auth", "Fix auth"),
                ("Objective\nFix auth", "Objective"),       # not the exact heading
                ("OBJECTIVES\nFix auth", "OBJECTIVES"),
                ("OBJECTIVE: Fix auth", "OBJECTIVE: Fix auth"),
                ("CONSTRAINTS\n- a", "CONSTRAINTS"),        # no OBJECTIVE heading: as before
                ("- a bullet first\nOBJECTIVE\nFix auth", "- a bullet first")):
            self.assertEqual(hts._objective(text), want, text)
            self.assertEqual(hts._objective(text), self.first_line_before(text))
        self.assertIsNone(hts._objective(""))
        self.assertIsNone(hts._objective("  \n \n"))

    def test_a_description_that_is_not_a_string_is_unknown(self):
        for value in (None, 7, 1.5, True, ["OBJECTIVE", "goal"], {"a": 1},
                      b"OBJECTIVE\ngoal"):
            self.assertIsNone(hts._objective(value), repr(value))

    def test_the_bound_and_its_ellipsis_are_unchanged_for_both_forms(self):
        self.assertEqual(hts.MAX_OBJECTIVE_CHARS, 160)
        long_goal = "g" * (hts.MAX_OBJECTIVE_CHARS + 40)
        for text in (long_goal, self.formatted({"objective": long_goal})):
            got = hts._objective(text)
            self.assertEqual(len(got), hts.MAX_OBJECTIVE_CHARS)
            self.assertEqual(got, "g" * (hts.MAX_OBJECTIVE_CHARS - 1) + "…")
        exact = "e" * hts.MAX_OBJECTIVE_CHARS
        self.assertEqual(hts._objective(self.formatted({"objective": exact})),
                         exact)

    def test_ambiguous_forms_fall_to_unknown_never_a_guess(self):
        # An objective that IS one of the section words cannot be told from the
        # heading that follows an empty objective: unknown, not a guess.
        for word in ("CONSTRAINTS", "RULES", "ACCEPTANCE CRITERIA",
                     "VERIFICATION"):
            self.assertIsNone(
                hts._objective(self.formatted({"objective": word})), word)
        # A bullet BELONGING TO A SECTION, or a later section, is never promoted to the goal.
        self.assertIsNone(hts._objective("OBJECTIVE\n\nCONSTRAINTS\n- a goal?"))
        self.assertIsNone(hts._objective("OBJECTIVE\nRULES\n- a goal?"))
        # A REPEATED heading is indistinguishable from a malformed record, so the
        # conservative answer is unknown: returning it would recreate the very
        # unreadable-heading output this extraction exists to remove. (The UI then says,
        # truthfully, that this source reported no goal.) An objective that is literally
        # the word OBJECTIVE is not worth recreating that defect for.
        self.assertIsNone(hts._objective("OBJECTIVE\nOBJECTIVE"))
        self.assertIsNone(hts._objective(self.formatted({"objective": "OBJECTIVE"})))
        self.assertIsNone(hts._objective("OBJECTIVE\n\n  OBJECTIVE  \nA goal?"))

    def test_a_dash_line_after_the_heading_is_the_objectives_own_text(self):
        # No bullet rejection (that would be a heuristic: "- do X" is a legitimate objective).
        # The formatter's bullets only follow a section heading, and a heading right after
        # OBJECTIVE is refused before its bullets are reached; so from the formatter a "- "
        # line in that position is the objective's own text, returned as written.
        text = self.formatted({"objective": "- do X first\n- then Y",
                               "rules": ["a rule"]})
        self.assertEqual(hts._objective(text), "- do X first")
        self.assertEqual(hts._objective("OBJECTIVE\n- something"), "- something")
        # A bullet BELONGING to a section is never reached: the heading is refused first.
        self.assertIsNone(hts._objective(self.formatted({"objective": "",
                                                         "rules": ["- a rule"]})))

    def test_only_the_first_goal_line_is_used_and_the_record_is_not_changed(self):
        mission = {"objective": "First line of the goal\nsecond line",
                   "rules": ["r"]}
        before = json.dumps(mission, sort_keys=True)
        text = self.formatted(mission)
        self.assertEqual(hts._objective(text), "First line of the goal")
        self.assertEqual(json.dumps(mission, sort_keys=True), before)

    def test_the_known_headings_are_exactly_the_formatters_five(self):
        # Producer and consumer stay locked: format a FULL mission with the real formatter and
        # take every non-bullet, non-blank line it wrote. They are the five headings this
        # module knows, in the formatter's order; a heading added to (or renamed in) herdctl.py
        # fails here instead of silently leaking into the office as a "goal".
        text = self.formatted({
            "objective": "A SYNTHETIC goal",
            "constraints": ["c"], "rules": ["r"],
            "acceptance_criteria": ["a"], "verification": ["v"]})
        headings = [line for line in text.splitlines()
                    if line and not line.startswith("- ")
                    and line != "A SYNTHETIC goal"]
        self.assertEqual(
            headings,
            ["OBJECTIVE", "CONSTRAINTS", "RULES", "ACCEPTANCE CRITERIA",
             "VERIFICATION"])
        self.assertEqual(headings[0], hts._MISSION_OBJECTIVE_HEADING)
        self.assertEqual(set(headings), set(hts._MISSION_SECTION_HEADINGS))
        self.assertEqual(len(hts._MISSION_SECTION_HEADINGS), 5)


if __name__ == "__main__":
    unittest.main()
