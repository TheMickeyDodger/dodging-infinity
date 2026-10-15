"""Query contract VERSION 2, the pure layer (mission_sources.contract_v2,
.chains, .query_v2).

Pure: injected snapshot and chain observation VALUES, hand-built mission
views (inputs, never outputs), and a real private registry. Covered: the
chain observation reader's refusals; the version 1 / version 2 consumer
operations reading each other ``incompatible``; the chain decision order
(membership END by the herdr task rule kept apart from dependency
SATISFACTION by the herdr dependency rule, in one response); the four
children-record outcomes; unobserved, stale, conflicting, cyclic and
incomplete child records; task-qualified member refs with claims counted
across every chain; the Mission State and canonical review projections and
their bounds; the deep validator; and parity of every re-declared constant
with its owner.
"""

import copy
import os
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "tests"))

from mission import observation as mission_observation  # noqa: E402
from mission import reconciliation  # noqa: E402
from mission_sources import chains as chains_module  # noqa: E402
from mission_sources import contract  # noqa: E402
from mission_sources import contract_v2 as v2  # noqa: E402
from mission_sources import query_v2  # noqa: E402
from mission_sources import roster  # noqa: E402
from mission_sources import sources as sources_module  # noqa: E402
from pr_delivery import store as delivery_store  # noqa: E402
from target_runtime import mission_state  # noqa: E402
from target_runtime import runtime_snapshot  # noqa: E402
import test_mission_sources as base  # noqa: E402

NOW = 1_000_000
TERMINAL = ("COMPLETE", "COMPLETED", "ABANDONED", "FAILED")
P = "/synthetic/parent"
C1, C2, C3, C4 = ("/synthetic/child-1", "/synthetic/child-2",
                  "/synthetic/child-3", "/synthetic/child-4")


def task_block(task_id, status="ACTIVE"):
    return {"task_id": task_id, "status": status,
            "membership_ended": status in TERMINAL,
            "dependency_complete": status == "COMPLETE"}


def review_na():
    return {"state": v2.REVIEW_NOT_APPLICABLE,
            "problem": v2.PROBLEM_REVIEW_NO_BINDING, "record": None,
            "gate": None, "verdict": None, "currency": None,
            "task_status": None, "integrity": None}


def review_admitted(token="APPROVE", status="ACTIVE"):
    parsed = token in ("APPROVE", "REJECT")
    return {"state": v2.REVIEW_ADMITTED, "problem": None,
            "record": {"round": 1, "reviewer_logical": "reviewer1",
                       "reviewer_agent": "agent-reviewer1",
                       "protocol_token": token},
            "gate": v2.GATE_PARSED if parsed else v2.GATE_UNPARSED,
            "verdict": token if parsed else None,
            "currency": (v2.CURRENCY_CURRENT if status == "ACTIVE"
                         else v2.CURRENCY_POSSIBLY_STALE),
            "task_status": status,
            "integrity": v2.INTEGRITY_LINKAGE_CORROBORATED}


def child_record(parent, child, repo, dependency=True, agents=None):
    return {"parent_task_id": parent, "child_task_id": child, "repo": repo,
            "workspace_id": "w-" + (child or "x"), "dependency": dependency,
            "declared_agents": agents}


def children(records=None, state=None, problem=None, other=0):
    records = list(records or [])
    if state is None:
        state = v2.CHILDREN_OBSERVED if records else (
            v2.CHILDREN_OBSERVED_EMPTY)
    if state == v2.CHILDREN_UNAVAILABLE:
        return {"state": state, "problem": problem, "caveat": None,
                "record_count": None, "other_parent_count": None,
                "records": []}
    return {"state": state, "problem": None,
            "caveat": (v2.CAVEAT_WRITER_RESET
                       if state == v2.CHILDREN_OBSERVED_EMPTY else None),
            "record_count": len(records), "other_parent_count": other,
            "records": records}


def root(path, task_id, status="ACTIVE", tier=v2.TIER_DISCOVERED,
         kids=None, review=None):
    return {"root": path, "tier": tier,
            "task": None if task_id is None else task_block(task_id, status),
            "children": kids if kids is not None else children(
                state=v2.CHILDREN_UNAVAILABLE,
                problem=v2.PROBLEM_CHILDREN_ABSENT),
            "review": review or review_na()}


def chain_value(roots, unavailable=(), over=(), observed_at=NOW):
    return {"schema": v2.CHAIN_OBSERVATION_SCHEMA,
            "version": v2.CHAIN_OBSERVATION_VERSION,
            "observed_at": observed_at, "producer": "synthetic",
            "roots": list(roots), "unavailable": list(unavailable),
            "over_capacity": list(over)}


def binding(logical, agent, path):
    role = "".join(ch for ch in logical if ch.isalpha())
    return {"logical": logical, "role": role, "agent": agent,
            "session": "s-" + agent, "pane_id": "p-" + agent,
            "workspace_id": "w-" + path}


def live(agent, path, status="working"):
    return {"agent": agent, "provider": "claude", "status": status,
            "session": "s-" + agent, "pane_id": "p-" + agent,
            "workspace_id": "w-" + path}


def snapshot(tasks, agents, observed_at=NOW):
    return {"schema": contract.SNAPSHOT_SCHEMA,
            "version": contract.SNAPSHOT_VERSION, "observed_at": observed_at,
            "producer": "synthetic", "tasks": list(tasks),
            "agents": list(agents)}


def snap_task(task_id, path, bindings):
    return {"task_id": task_id, "workspace_realpath": path,
            "bindings": list(bindings)}


def view(native, task_id, workspace, standing=contract.STANDING_ACTIVE,
         source_id="mc"):
    return {
        "mission_ref": "%s:%s" % (source_id, native), "source_id": source_id,
        "native_id": native,
        "lifecycle": {"model": contract.LIFECYCLE_MODEL_MISSION_CORE,
                      "state": "RUNNING", "standing": standing},
        "producer": {"declared": "synthetic", "recorded": {
            "transport": "t", "principal_kind": "k"}},
        "revision": 1, "objective": "SYNTHETIC", "updated_at": NOW,
        "execution": {"task_id": task_id, "identity_source": "start_result",
                      "workspace_realpath": workspace,
                      "run_started": True, "receipt_recorded": True,
                      "consistent": True, "record_ambiguous": False},
        "native": {"paused": False, "cancel_requested": False},
    }


def classify(views, snap, chains):
    reading = contract.read_runtime_snapshot(snap, NOW)
    chain_reading = v2.read_chain_observation(chains, NOW)
    return chains_module.classify_v2(views, reading, chain_reading)


def by_task(mission):
    return dict((c["chain_task_id"], c) for c in mission["task_chains"])


class ChainObservationReaderTests(unittest.TestCase):

    def test_absent_incompatible_malformed_future_and_stale(self):
        good = chain_value([root(P, "T0")])
        self.assertEqual(v2.read_chain_observation(None, NOW).health,
                         contract.HEALTH_UNAVAILABLE)
        self.assertEqual(v2.read_chain_observation(
            dict(good, version=2), NOW).health, contract.HEALTH_INCOMPATIBLE)
        self.assertEqual(v2.read_chain_observation(
            dict(good, extra=1), NOW).health, contract.HEALTH_MALFORMED)
        self.assertEqual(v2.read_chain_observation(
            dict(good, observed_at=NOW + 1), NOW).problem,
            contract.PROBLEM_OBSERVED_IN_FUTURE)
        self.assertEqual(v2.read_chain_observation(
            dict(good, observed_at=NOW - 61), NOW).health,
            contract.HEALTH_STALE)
        self.assertTrue(v2.read_chain_observation(good, NOW).is_live())

    def test_shape_violations_are_malformed_never_repaired(self):
        bad = []
        twice = chain_value([root(P, "T0"), root(P, "T0")])
        bad.append(twice)
        empty_no_caveat = chain_value([root(P, "T0", kids=dict(
            children(), caveat=None))])
        bad.append(empty_no_caveat)
        verdict_on_unparsed = review_admitted("MISSING")
        verdict_on_unparsed["verdict"] = "REJECT"
        bad.append(chain_value([root(P, "T0", review=verdict_on_unparsed)]))
        wrong_currency = review_admitted("APPROVE", status="COMPLETE")
        wrong_currency["currency"] = v2.CURRENCY_CURRENT
        bad.append(chain_value([root(P, "T0", review=wrong_currency)]))
        raw = review_admitted("APPROVE")
        raw["record"]["protocol_token"] = "LGTM"
        bad.append(chain_value([root(P, "T0", review=raw)]))
        no_integrity = review_admitted()
        no_integrity["integrity"] = None
        bad.append(chain_value([root(P, "T0", review=no_integrity)]))
        records_while_empty = children()
        records_while_empty["records"] = [child_record("T0", "T1", C1)]
        bad.append(chain_value([root(P, "T0", kids=records_while_empty)]))
        for value in bad:
            with self.subTest(value=value["roots"][-1]):
                self.assertEqual(v2.read_chain_observation(value, NOW).health,
                                 contract.HEALTH_MALFORMED)


class ConsumerOperationTests(unittest.TestCase):

    def response(self, version=2):
        keys = (v2.RESPONSE_KEYS_V2 if version == 2
                else contract.RESPONSE_KEYS)
        value = dict((key, None) for key in keys)
        value.update(contract=contract.QUERY_CONTRACT,
                     contract_version=version, generated_at=NOW,
                     max_age_seconds=60, valid_until=NOW + 30,
                     registry={"health": contract.HEALTH_FRESH})
        return value

    def test_each_version_reads_the_other_as_incompatible(self):
        self.assertEqual(v2.response_freshness_v2(self.response(2), NOW),
                         (contract.HEALTH_FRESH, None))
        self.assertEqual(contract.response_freshness(self.response(2), NOW),
                         (contract.HEALTH_INCOMPATIBLE,
                          contract.PROBLEM_RESPONSE_CONTRACT))
        self.assertEqual(v2.response_freshness_v2(self.response(1), NOW),
                         (contract.HEALTH_INCOMPATIBLE,
                          contract.PROBLEM_RESPONSE_CONTRACT))
        self.assertEqual(contract.response_freshness(self.response(1), NOW),
                         (contract.HEALTH_FRESH, None))

    def test_the_v2_rule_keeps_every_v1_rule(self):
        added = dict(self.response(), extra=1)
        self.assertEqual(v2.response_freshness_v2(added, NOW)[1],
                         contract.PROBLEM_RESPONSE_SHAPE)
        self.assertEqual(v2.response_freshness_v2(self.response(),
                                                  NOW + 31)[1],
                         contract.PROBLEM_RESPONSE_EXPIRED)
        self.assertEqual(v2.response_freshness_v2(self.response(),
                                                  NOW - 1)[1],
                         contract.PROBLEM_OBSERVED_IN_FUTURE)
        degraded = dict(self.response(), registry={
            "health": contract.HEALTH_UNAVAILABLE})
        self.assertEqual(v2.response_freshness_v2(degraded, NOW)[0],
                         contract.HEALTH_UNAVAILABLE)
        self.assertEqual(v2.FRESHNESS_BY_VERSION, {
            1: contract.response_freshness, 2: v2.response_freshness_v2})


class ChainDecisionTests(unittest.TestCase):

    def setUp(self):
        self.mission = view("m1", "T0", P)
        self.direct = [binding("lead1", "a-lead", P)]
        self.agents = [live("a-lead", P)]

    def run_chains(self, records, roots, tasks=(), agents=(), views=None,
                   unavailable=(), over=()):
        snap = snapshot([snap_task("T0", P, self.direct)] + list(tasks),
                        self.agents + list(agents))
        value = chain_value([root(P, "T0", kids=children(records))]
                            + list(roots), unavailable, over)
        missions, unmatched = classify(views or [self.mission], snap, value)
        return missions, unmatched

    def test_failed_and_abandoned_end_membership_and_stay_unsatisfied(self):
        records = [child_record("T0", "T-FAILED", C1),
                   child_record("T0", "T-ABANDONED", C2),
                   child_record("T0", "T-COMPLETE", C3),
                   child_record("T0", "T-COMPLETED", C4)]
        roots = [root(C1, "T-FAILED", "FAILED"),
                 root(C2, "T-ABANDONED", "ABANDONED"),
                 root(C3, "T-COMPLETE", "COMPLETE"),
                 root(C4, "T-COMPLETED", "COMPLETED")]
        missions, _ = self.run_chains(records, roots)
        chains = by_task(missions[0])
        for task_id, resolution in (
                ("T-FAILED", v2.DEPENDENCY_UNSATISFIED),
                ("T-ABANDONED", v2.DEPENDENCY_UNSATISFIED),
                ("T-COMPLETE", v2.DEPENDENCY_SATISFIED),
                ("T-COMPLETED", v2.DEPENDENCY_UNSATISFIED)):
            with self.subTest(task=task_id):
                chain = chains[task_id]
                # Membership END (the task rule) ...
                self.assertIs(chain["membership_end"]["ended"], True)
                self.assertEqual(chain["binding"]["state"],
                                 contract.BINDING_NOT_APPLICABLE)
                self.assertEqual(chain["binding"]["reason"],
                                 v2.REASON_MEMBERSHIP_ENDED)
                # ... and dependency SATISFACTION (the dependency rule),
                # answered separately in the same response.
                self.assertEqual(chain["dependency"]["resolution"],
                                 resolution)
        self.assertEqual([m["chain"] for m in missions[0]["members"]],
                         [v2.CHAIN_DIRECT])

    def test_a_live_child_binds_with_task_qualified_members(self):
        child_bindings = [binding("lead1", "c-lead", C1)]
        missions, unmatched = self.run_chains(
            [child_record("T0", "T1", C1)], [root(C1, "T1")],
            tasks=[snap_task("T1", C1, child_bindings)],
            agents=[live("c-lead", C1, "idle")])
        mission = missions[0]
        self.assertEqual(by_task(mission)["T1"]["binding"]["state"],
                         contract.BINDING_BOUND)
        self.assertEqual(by_task(mission)["T1"]["dependency"]["resolution"],
                         v2.DEPENDENCY_UNSATISFIED)
        refs = sorted(m["member_ref"] for m in mission["members"])
        # The SAME logical name in both chains, two distinct members.
        self.assertEqual(refs, ["mc:m1#T0#lead1", "mc:m1#T1#lead1"])
        chains = dict((m["member_ref"], (m["chain"], m["chain_task_id"]))
                      for m in mission["members"])
        self.assertEqual(chains["mc:m1#T1#lead1"], (v2.CHAIN_CHILD, "T1"))
        self.assertEqual(unmatched, 0)

    def test_one_agent_in_two_chains_is_never_represented_twice(self):
        missions, _ = self.run_chains(
            [child_record("T0", "T1", C1)], [root(C1, "T1")],
            tasks=[snap_task("T1", C1, [binding("lead1", "a-lead", C1)])])
        self.assertEqual(
            sorted((m["classification"], m["reason"])
                   for m in missions[0]["members"]),
            [(contract.MEMBER_AMBIGUOUS,
              roster.MEMBER_REASON_AGENT_MULTIPLY_CLAIMED)] * 2)

    def test_an_unobserved_child_root_is_never_a_live_member(self):
        cases = (
            ([], [], v2.ROOT_NOT_CAPTURED),
            ([{"root": C1, "tier": v2.TIER_CHILD,
               "problem": "herd_roots_invalid", "detail": None}], [],
             v2.ROOT_UNAVAILABLE),
            ([], [{"root": C1, "tier": v2.TIER_CHILD}],
             v2.ROOT_OVER_CAPACITY))
        for unavailable, over, observation in cases:
            with self.subTest(observation=observation):
                missions, _ = self.run_chains(
                    [child_record("T0", "T1", C1)], [],
                    unavailable=unavailable, over=over)
                chain = by_task(missions[0])["T1"]
                self.assertEqual(
                    (chain["binding"]["state"], chain["binding"]["reason"]),
                    (contract.BINDING_UNVERIFIED,
                     roster.REASON_WORKSPACE_NOT_OBSERVED))
                self.assertEqual(chain["root"]["observation"], observation)
                self.assertEqual(chain["dependency"]["resolution"],
                                 v2.DEPENDENCY_UNKNOWN)
                self.assertEqual([m["chain"] for m in missions[0]["members"]],
                                 [v2.CHAIN_DIRECT])

    def test_a_stale_record_is_a_reported_conflict_not_resolved(self):
        missions, _ = self.run_chains(
            [child_record("T0", "T1", C1)], [root(C1, "T-NEWER")],
            tasks=[snap_task("T-NEWER", C1, [binding("lead1", "n", C1)])])
        chain = by_task(missions[0])["T1"]
        self.assertEqual((chain["binding"]["state"],
                          chain["binding"]["reason"]),
                         (contract.BINDING_AMBIGUOUS,
                          v2.REASON_CHILD_RECORD_STALE))
        self.assertEqual(chain["dependency"]["resolution"],
                         v2.DEPENDENCY_UNSATISFIED)
        self.assertIsNone(chain["membership_end"]["ended"])

    def test_a_child_root_with_no_current_task_has_disappeared(self):
        missions, _ = self.run_chains([child_record("T0", "T1", C1)],
                                      [root(C1, None)])
        chain = by_task(missions[0])["T1"]
        self.assertEqual(chain["binding"]["state"],
                         contract.BINDING_DISAPPEARED)

    def test_cycles_conflicts_and_incomplete_records_are_explicit(self):
        cases = (
            ([child_record("T0", "T1", P)], [],
             v2.REASON_CHILD_RECORD_CYCLE),
            ([child_record("T0", "T1", C1)],
             [root(C1, "T1", kids=children([child_record("T1", "T9", P)]))],
             v2.REASON_CHILD_RECORD_CYCLE),
            ([child_record("T0", "T1", C1), child_record("T0", "T1", C2)],
             [root(C1, "T1"), root(C2, "T1")],
             roster.REASON_TASK_MULTIPLY_CLAIMED),
            ([child_record("T0", "T1", C1), child_record("T0", "T2", C1)],
             [root(C1, "T1")], roster.REASON_WORKSPACE_MULTIPLY_CLAIMED),
            ([child_record("T0", None, C1)], [],
             v2.REASON_CHILD_RECORD_INCOMPLETE),
            ([child_record("T0", "T1", None)], [],
             v2.REASON_CHILD_RECORD_INCOMPLETE))
        for records, roots, reason in cases:
            with self.subTest(reason=reason, records=records):
                missions, _ = self.run_chains(records, roots)
                for chain in missions[0]["task_chains"][1:]:
                    self.assertEqual((chain["binding"]["state"],
                                      chain["binding"]["reason"]),
                                     (contract.BINDING_AMBIGUOUS, reason))

    def test_a_child_claimed_by_two_missions_is_ambiguous_for_both(self):
        other = view("m2", "T5", C2)
        snap = snapshot([snap_task("T0", P, self.direct),
                         snap_task("T5", C2, [])], self.agents)
        value = chain_value([
            root(P, "T0", kids=children([child_record("T0", "T1", C1)])),
            root(C2, "T5", kids=children([child_record("T5", "T1", C1)])),
            root(C1, "T1")])
        missions, _ = classify([self.mission, other], snap, value)
        for mission in missions:
            self.assertEqual(by_task(mission)["T1"]["binding"]["reason"],
                             roster.REASON_TASK_MULTIPLY_CLAIMED)

    def test_descendants_are_counted_and_never_followed(self):
        grand = children([child_record("T1", "T2", C2),
                          child_record("T1", "T3", C3)])
        missions, _ = self.run_chains([child_record("T0", "T1", C1)],
                                      [root(C1, "T1", kids=grand)])
        chain = by_task(missions[0])["T1"]
        self.assertEqual(chain["descendants_not_followed"], 2)
        self.assertEqual(sorted(by_task(missions[0])), ["T0", "T1"])

    def test_the_four_children_outcomes_stay_apart(self):
        for kids, state, problem, caveat in (
                (children(), v2.CHILDREN_OBSERVED_EMPTY, None,
                 v2.CAVEAT_WRITER_RESET),
                (children(state=v2.CHILDREN_UNAVAILABLE,
                          problem=v2.PROBLEM_CHILDREN_ABSENT),
                 v2.CHILDREN_UNAVAILABLE, v2.PROBLEM_CHILDREN_ABSENT, None),
                (children(state=v2.CHILDREN_UNAVAILABLE,
                          problem=v2.PROBLEM_CHILDREN_ZERO_BYTES),
                 v2.CHILDREN_UNAVAILABLE, v2.PROBLEM_CHILDREN_ZERO_BYTES,
                 None),
                (children(state=v2.CHILDREN_UNAVAILABLE,
                          problem=v2.PROBLEM_CHILDREN_NOT_JSON),
                 v2.CHILDREN_UNAVAILABLE, v2.PROBLEM_CHILDREN_NOT_JSON,
                 None)):
            with self.subTest(problem=problem, state=state):
                snap = snapshot([snap_task("T0", P, self.direct)],
                                self.agents)
                missions, _ = classify([self.mission], snap,
                                       chain_value([root(P, "T0",
                                                         kids=kids)]))
                block = missions[0]["children_record"]
                self.assertEqual((block["state"], block["problem"],
                                  block["caveat"]), (state, problem, caveat))
                # An observed empty is never unavailable; no chain either way.
                self.assertEqual(len(missions[0]["task_chains"]), 1)

    def test_a_parent_whose_root_moved_to_another_task_has_no_children(self):
        snap = snapshot([], self.agents)
        missions, _ = classify([self.mission], snap, chain_value([root(
            P, "T-OTHER", kids=children([child_record("T-OTHER", "T1",
                                                      C1)]))]))
        self.assertEqual(missions[0]["children_record"]["problem"],
                         v2.PROBLEM_PARENT_TASK_MISMATCH)
        self.assertEqual(len(missions[0]["task_chains"]), 1)

    def test_a_stale_observation_presents_nothing_live(self):
        snap = snapshot([snap_task("T0", P, self.direct)], self.agents,
                        observed_at=NOW - 61)
        missions, unmatched = classify([self.mission], snap, chain_value(
            [root(P, "T0", kids=children([child_record("T0", "T1", C1)]))],
            observed_at=NOW - 61))
        self.assertEqual(missions[0]["members"], [])
        self.assertEqual(missions[0]["children_record"]["problem"],
                         v2.PROBLEM_CHAIN_OBSERVATION_NOT_FRESH)
        self.assertIsNone(unmatched)

    def test_an_ended_direct_task_says_so_and_the_mission_binding_is_v1(self):
        snap = snapshot([], self.agents)
        missions, _ = classify([self.mission], snap, chain_value(
            [root(P, "T0", "FAILED")]))
        direct = missions[0]["task_chains"][0]
        self.assertEqual(missions[0]["binding"]["reason"],
                         roster.REASON_TASK_NOT_OBSERVED)
        self.assertEqual((direct["binding"]["state"],
                          direct["binding"]["reason"]),
                         (contract.BINDING_NOT_APPLICABLE,
                          v2.REASON_MEMBERSHIP_ENDED))
        self.assertEqual(direct["membership_end"]["status"], "FAILED")

    def test_the_input_views_are_never_mutated(self):
        views = [self.mission]
        before = copy.deepcopy(views)
        self.run_chains([child_record("T0", "T1", C1)], [root(C1, "T1")],
                        views=views)
        self.assertEqual(views, before)

    def test_no_live_child_without_a_confirmed_unique_parent_binding(self):
        # Round 3, B1: a recorded, observed child with a live agent, under a
        # parent whose OWN binding the two passes never agreed on.
        snap = snapshot([snap_task("T0", P, self.direct),
                         snap_task("T1", C1, [binding("lead1", "c-lead",
                                                      C1)])],
                        self.agents + [live("c-lead", C1)])
        value = chain_value([
            root(P, "T0", kids=children([child_record("T0", "T1", C1)])),
            root(C1, "T1")])
        reading = contract.read_runtime_snapshot(snap, NOW)
        chain_reading = v2.read_chain_observation(value, NOW)
        ref = self.mission["mission_ref"]
        agreed = {ref: roster.binding_identity(self.mission)}
        cases = (
            # Pass two only: the discovery pass never saw this mission.
            ("absent_from_discovery", [self.mission], {}),
            ("identity_disagrees", [self.mission],
             {ref: dict(agreed[ref], task_id="T-OTHER")}),
            # Not unique: a second active mission claims the parent root.
            ("parent_not_unique", [self.mission, view("m2", "T9", P)], None),
        )
        for name, views, confirmed in cases:
            with self.subTest(case=name):
                missions, _ = chains_module.classify_v2(
                    views, reading, chain_reading,
                    confirmed_bindings=confirmed)
                mission = [m for m in missions if m["mission_ref"] == ref][0]
                chain = by_task(mission)["T1"]
                self.assertEqual(
                    (chain["binding"]["state"], chain["binding"]["reason"]),
                    (contract.BINDING_UNVERIFIED,
                     v2.REASON_PARENT_BINDING_UNCONFIRMED))
                self.assertNotEqual(v2.REASON_PARENT_BINDING_UNCONFIRMED,
                                    roster.REASON_BINDING_UNCONFIRMED)
                # No live member at all, child or direct ...
                self.assertEqual(mission["members"], [])
                self.assertNotEqual(mission["binding"]["state"],
                                    contract.BINDING_BOUND)
                # ... and NOT observed-empty: the child is still reported.
                self.assertEqual(mission["children_record"]["state"],
                                 v2.CHILDREN_OBSERVED)
                self.assertEqual(mission["children_record"]["record_count"],
                                 1)
                self.assertEqual(chain["record"]["child_task_id"], "T1")
        # The same scene with the two passes AGREEING binds the child live.
        missions, _ = chains_module.classify_v2(
            [self.mission], reading, chain_reading, confirmed_bindings=agreed)
        self.assertEqual(by_task(missions[0])["T1"]["binding"]["state"],
                         contract.BINDING_BOUND)
        self.assertEqual(sorted(m["member_ref"]
                                for m in missions[0]["members"]),
                         ["mc:m1#T0#lead1", "mc:m1#T1#lead1"])


class QueryV2Tests(base.RegistryFixture):

    def setUp(self):
        super(QueryV2Tests, self).setUp()
        self.register("mc", store=self.store_dir("mc"))
        self.register("wf", kind=contract.KIND_WORKFLOW_STORE,
                      store=self.store_dir("wf"))
        self.views = {"mc": [view("m1", "T0", P)], "wf": [dict(
            view("w1", None, None, source_id="wf"),
            lifecycle={"model": contract.LIFECYCLE_MODEL_WORKFLOW_STORE,
                       "state": "DISPATCHED",
                       "standing": contract.STANDING_ACTIVE})]}

    def reader(self, source, now, budget):
        return sources_module._reading(source, now, contract.HEALTH_FRESH,
                                       None, self.views[source["source_id"]])

    def query(self, compose=None, chains=None, snap=None):
        readers = {contract.KIND_MISSION_CORE: self.reader,
                   contract.KIND_WORKFLOW_STORE: self.reader}
        return query_v2.query_v2(
            self.registry, snap or snapshot(
                [snap_task("T0", P, [binding("lead1", "a", P)])],
                [live("a", P)]),
            chains if chains is not None else chain_value(
                [root(P, "T0", kids=children(),
                      review=review_admitted("MISSING"))]),
            NOW, readers=readers, compose=compose)

    def mission(self, response, ref):
        return [m for m in response["missions"] if m["mission_ref"] == ref][0]

    def observed_block(self, view_ref):
        report = dict((key, None) for key in v2.OBSERVATION_REPORT_KEYS)
        kinds = dict((kind, {"supplied": v2.SUPPLIED_UNAVAILABLE,
                             "reason": "synthetic"})
                     for kind in v2.REPORT_KINDS)
        return {"state": v2.MISSION_STATE_OBSERVED, "problem": None,
                "detail": None, "report": report, "inputs": {
                    "cursor": v2.CURSOR_SAMPLED, "kinds": kinds,
                    "candidate_applicability":
                        v2.CANDIDATE_APPLICABILITY_UNPROVEN,
                    "baseline": v2.BASELINE_UNOBSERVED}}

    def test_the_response_is_version_2_and_validated(self):
        response = self.query()
        self.assertEqual(response["contract_version"], 2)
        self.assertEqual(set(response), set(v2.RESPONSE_KEYS_V2))
        self.assertEqual(v2.response_freshness_v2(response, NOW),
                         (contract.HEALTH_FRESH, None))
        self.assertEqual(response["limits"]["max_child_records"],
                         v2.MAX_CHILD_RECORDS)
        v2.validate_response_v2(response)

    def test_mission_state_kinds_and_compose_failures_are_explicit(self):
        response = self.query()
        self.assertEqual(self.mission(response, "wf:w1")["mission_state"][
            "problem"], v2.PROBLEM_MISSION_STATE_KIND)
        self.assertEqual(self.mission(response, "mc:m1")["mission_state"][
            "problem"], v2.PROBLEM_MISSION_STATE_NOT_CONFIGURED)

        def raising(source, views, chain_reading):
            raise RuntimeError("synthetic")

        def malformed(source, views, chain_reading):
            return dict((v["mission_ref"], {"state": "observed"})
                        for v in views)
        for compose in (raising, malformed):
            with self.subTest(compose=compose.__name__):
                block = self.mission(self.query(compose), "mc:m1")[
                    "mission_state"]
                self.assertEqual((block["state"], block["problem"]),
                                 (v2.MISSION_STATE_UNAVAILABLE,
                                  v2.PROBLEM_MISSION_STATE_RETURN))

        def good(source, views, chain_reading):
            self.assertEqual(source["kind"], contract.KIND_MISSION_CORE)
            self.assertTrue(chain_reading.is_live())
            return dict((v["mission_ref"], self.observed_block(
                v["mission_ref"])) for v in views)
        block = self.mission(self.query(good), "mc:m1")["mission_state"]
        self.assertEqual(block["state"], v2.MISSION_STATE_OBSERVED)
        self.assertEqual(block["inputs"]["candidate_applicability"],
                         v2.CANDIDATE_APPLICABILITY_UNPROVEN)

    def test_at_most_the_bound_is_offered_active_first(self):
        self.views["mc"] = [view("m%02d" % i, "T%02d" % i, "/r/%02d" % i)
                            for i in range(v2.MAX_OBSERVED_MISSIONS)] + [
            view("inactive", None, None,
                 standing=contract.STANDING_INACTIVE)]
        offered = []

        def compose(source, views, chain_reading):
            offered.extend(v["mission_ref"] for v in views)
            return dict((v["mission_ref"], self.observed_block(
                v["mission_ref"])) for v in views)
        response = self.query(compose)
        self.assertEqual(len(offered), v2.MAX_OBSERVED_MISSIONS)
        self.assertNotIn("mc:inactive", offered)
        self.assertEqual(self.mission(response, "mc:inactive")[
            "mission_state"]["problem"], v2.PROBLEM_MISSION_STATE_OVER_LIMIT)

    def test_the_canonical_review_projects_only_through_agreement(self):
        review = self.mission(self.query(), "mc:m1")["admitted_artifacts"][
            "canonical_review"]
        self.assertEqual((review["state"], review["gate"], review["verdict"],
                          review["candidate_applicability"]),
                         (v2.REVIEW_ADMITTED, v2.GATE_UNPARSED, None,
                          v2.CANDIDATE_APPLICABILITY_UNPROVEN))
        for chains, state, problem in (
                (chain_value([], observed_at=NOW - 61),
                 v2.REVIEW_UNAVAILABLE,
                 v2.PROBLEM_CHAIN_OBSERVATION_NOT_FRESH),
                (chain_value([]), v2.REVIEW_UNAVAILABLE,
                 v2.PROBLEM_REVIEW_ROOT_NOT_OBSERVED),
                (chain_value([root(P, "T-OTHER", review=review_admitted())]),
                 v2.REVIEW_REFUSED, v2.PROBLEM_REVIEW_TASK_MISMATCH)):
            with self.subTest(problem=problem):
                review = self.mission(self.query(chains=chains), "mc:m1")[
                    "admitted_artifacts"]["canonical_review"]
                self.assertEqual((review["state"], review["problem"]),
                                 (state, problem))
                self.assertIsNone(review["record"])
        self.assertEqual(self.mission(self.query(), "wf:w1")[
            "admitted_artifacts"]["canonical_review"]["problem"],
            v2.PROBLEM_REVIEW_KIND_NOT_APPLICABLE)

    def test_valid_until_is_shortened_by_the_chain_observation(self):
        response = self.query(chains=chain_value(
            [root(P, "T0")], observed_at=NOW - 50))
        self.assertEqual(response["valid_until"], NOW - 50 + 60)

    def test_a_degraded_registry_is_still_a_version_2_response(self):
        self.registry = type(self.registry)(os.path.join(self.root, "none"))
        response = self.query()
        self.assertEqual(response["contract_version"], 2)
        self.assertEqual(response["registry"]["health"],
                         contract.HEALTH_UNAVAILABLE)
        v2.validate_response_v2(response)

    def test_the_deep_validator_refuses_what_v2_must_never_emit(self):
        good = self.query()
        mutations = []
        member = copy.deepcopy(good)
        self.mission(member, "mc:m1")["members"][0]["member_ref"] = (
            "mc:m1#lead1")
        mutations.append(("member_ref not task-qualified", member))
        unproven = copy.deepcopy(good)
        self.mission(unproven, "mc:m1")["admitted_artifacts"][
            "canonical_review"]["candidate_applicability"] = "proven"
        mutations.append(("candidate applicability", unproven))
        order = copy.deepcopy(good)
        self.mission(order, "mc:m1")["task_chains"][0]["chain"] = "child"
        mutations.append(("direct chain first", order))
        unknown = copy.deepcopy(good)
        self.mission(unknown, "mc:m1")["members"][0]["model"] = "gpt"
        mutations.append(("unreported facts stay unknown", unknown))
        for label, value in mutations:
            with self.subTest(label=label):
                with self.assertRaises(contract.ContractError):
                    v2.validate_response_v2(value)


class ParityTests(unittest.TestCase):
    """Every constant contract_v2 or the composer RE-DECLARES equals its
    owner's, so the two can never drift silently."""

    def test_redeclared_constants_equal_their_owners(self):
        self.assertEqual(v2.OBSERVATION_REPORT_KEYS,
                         mission_observation.OBSERVATION_KEYS)
        self.assertEqual(tuple(sorted(v2.REPORT_KINDS)),
                         tuple(sorted(reconciliation.SOURCE_KINDS)))
        self.assertEqual(v2.REPORTED_FRESHNESS_BOUND_SECONDS,
                         reconciliation.REPORTED_FRESHNESS_BOUND_SECONDS)
        self.assertEqual(v2.MAX_HERD_ROOTS_V2,
                         runtime_snapshot.MAX_HERD_ROOTS)
        self.assertEqual(v2.MAX_RESPONSE_MISSIONS,
                         sources_module.MAX_QUERY_MISSIONS)
        self.assertEqual(runtime_snapshot.CHILDREN_FILE, v2.CHILDREN_FILE)
        self.assertEqual(runtime_snapshot.STATE_FILES_V2[:3],
                         runtime_snapshot.STATE_FILES)
        self.assertEqual(mission_state.DELIVERY_STORE_FILE,
                         delivery_store.STORE_FILE_NAME)
        self.assertEqual(mission_state.DELIVERY_STORE_SCHEMA_VERSION,
                         delivery_store.STORE_SCHEMA_VERSION)
        self.assertEqual(mission_state.TASK_REPORTS,
                         reconciliation.TASK_REPORTS)
        self.assertEqual(v2.CONTRACT_VERSIONS, (1, 2))
        self.assertEqual(v2.DEFAULT_CONTRACT_VERSION, 1)

    def test_the_membership_rule_is_the_herdr_task_rule(self):
        from herdr import vintage
        self.assertEqual(set(TERMINAL), set(vintage.TERMINAL_TASK_STATUSES))
        for status in TERMINAL:
            self.assertIn(status, v2.MEMBERSHIP_RULE)


if __name__ == "__main__":
    unittest.main()
