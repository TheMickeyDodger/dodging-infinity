"""Query contract VERSION 2 through the catalog owner, the HTTP adapter, the
capture's chain passes and the Mission State composer.

Built on the accepted fixtures (``test_catalog_http.HttpFixture``: real
private registries and Mission Core stores, synthetic herd roots, the FAKE
listing behind ``test_runtime_snapshot``'s structural engine fence). Each
case discriminates against its near neighbour:

1. SELECTION: no flag serves version 1 exactly (eleven keys, no
   ``chain_observation``); ``contract_version=2`` serves version 2, which a
   version 1 consumer reads ``incompatible`` (the designed refusal), NOT
   ``malformed``; a ``contract_version`` query field is refused (400) and
   never reaches the owner; serve-time freshness is the served version's.
2. THE CAPTURE'S CHAIN PASSES over REAL files: the four children-record
   outcomes, the 32-record bound, depth 1 with tolerant child roots and
   their capacity, and the two herdr rules computed from real statuses.
4. THE SHARED REFRESH READ BUDGET over REAL stores (round 3, B2): the
   BACKWARD reproduction (through interfaces both the rejected and the
   corrected candidate have) measures every store byte one refresh takes
   off disk against its stated cap; then the new surface: a read that
   FAILS keeps its hold (the cap is charged, not just the successes), a
   read that cannot be held is never made, and every stage of one owner
   refresh holds against the one UNCHANGED stated cap, exhaustion reported
   explicitly, never as an empty success.
3. COMPOSITION over a REAL Mission Core store: the cursor sampled BEFORE
   collection stays ``moved`` when the head moves mid-collection, against a
   re-sampled cursor that would read ``current``; no kind is ever supplied
   as ``unknown``; unmapped task literals refuse; the baseline is null and
   ``unobserved``; candidate applicability is ``unproven`` even beside an
   APPROVE; an unparsed review gate is never a rejection; the read-only
   store refuses ``lock`` and ``save``.
"""

import json
import os
import sys
import threading
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "tests"))

from mission import service as mission_service  # noqa: E402
from mission import store as mission_store  # noqa: E402
from mission_sources import contract  # noqa: E402
from mission_sources import contract_v2 as v2  # noqa: E402
from mission_sources import fileread  # noqa: E402
from mission_sources import query_v2  # noqa: E402
from mission_sources import sources as sources_module  # noqa: E402
from target_runtime import catalog  # noqa: E402
from target_runtime import catalog_http  # noqa: E402
from target_runtime import mission_state  # noqa: E402
from target_runtime import runtime_snapshot as service  # noqa: E402
import test_catalog_http as http_tests  # noqa: E402
import test_contract_v2 as pure  # noqa: E402
from test_mission_sources import AUTHENTICATED  # noqa: E402
from test_runtime_snapshot import TASK, engine_row, listing_stdout  # noqa: E402


class V2Fixture(http_tests.HttpFixture):

    def owner(self, version=None, discover=True, roots=None):
        options = {} if version is None else {"contract_version": version}
        owner = catalog.CatalogService(
            self.registry.directory, self.clock, roots,
            self.plan(stdout=listing_stdout([engine_row()])),
            monotonic=self.ticks, discover=discover, **options)

        def end():
            owner.shutdown()
            state = owner.settle(10)
            self.assertNotEqual(state["query_work"], catalog.WORK_PENDING)
        self.addCleanup(end)
        return owner

    def running(self):
        """A REAL running Mission bound to TASK at a synthetic herd root."""
        state_dir, source_id = self.fresh_store()
        mission_id, workspace = self.running_mission(state_dir, task_id=TASK)
        self.herd(root=workspace, task_id=TASK, status="ACTIVE")
        self.register(source_id, store=state_dir)
        return state_dir, source_id, mission_id, workspace


class SelectionTests(V2Fixture):

    def test_no_flag_serves_v1_and_the_flag_serves_v2(self):
        self.running()
        v1 = self.owner().request(catalog.MAX_WAIT_SECONDS)["response"]
        self.assertEqual(v1["contract_version"], 1)
        self.assertEqual(set(v1), set(contract.RESPONSE_KEYS))
        self.assertNotIn("chain_observation", v1)
        self.assertNotIn("contract_version", self.owner().diagnostics())
        owner = self.owner(version=2)       # a second process, in effect
        answer = owner.request(catalog.MAX_WAIT_SECONDS)
        response = answer["response"]
        self.assertIsNotNone(response, answer)
        self.assertEqual(response["contract_version"], 2)
        self.assertEqual(response["chain_observation"]["health"],
                         contract.HEALTH_FRESH)
        v2.validate_response_v2(response)
        now = self.clock()
        verdict = contract.response_freshness(response, now)
        self.assertEqual(verdict, (contract.HEALTH_INCOMPATIBLE,
                                   contract.PROBLEM_RESPONSE_CONTRACT))
        self.assertNotEqual(verdict, (contract.HEALTH_MALFORMED,
                                      contract.PROBLEM_RESPONSE_SHAPE))
        self.assertEqual(v2.response_freshness_v2(response, now),
                         (contract.HEALTH_FRESH, None))
        self.assertEqual(owner.diagnostics()["contract_version"], 2)
        self.assertEqual(sorted(answer), sorted(catalog.ANSWER_KEYS))

    def test_a_query_field_is_never_a_selector(self):
        stub = http_tests.StubOwner(catalog.catalog_answer(
            self.clock(), None, catalog.PROBLEM_SLOT_OCCUPIED, "stub", None))
        server = self.serve(stub)
        for path in ("/catalog?contract_version=2",
                     "/catalog?wait_seconds=1&contract_version=2",
                     "/catalog?contract_version=1"):
            with self.subTest(path=path):
                status, _, _ = self.request(server, path)
                self.assertEqual(status, 400)
        self.assertEqual(stub.waits, [], "a refused query reaches no owner")
        self.assertEqual(catalog_http._parser().parse_args([
            "--registry", "/r", "--discovery-file", "/d"]).contract_version,
            1)
        self.assertEqual(catalog._parser().parse_args([
            "query", "--registry", "/r"]).contract_version, 1)
        for main in (lambda: catalog_http.main([
                "--registry", self.directory, "--discovery-file",
                os.path.join(self.root, "d.json"), "--discover-roots",
                "--delivery-store", self.root]),
                lambda: catalog.main(["query", "--registry", self.directory,
                                      "--delivery-store", self.root],
                                     clock=self.clock)):
            self.assertEqual(main(), catalog.EXIT_USAGE)
        self.assertFalse(os.path.exists(os.path.join(self.root, "d.json")))

    def test_serve_time_freshness_is_the_served_versions_own(self):
        self.running()
        owner = self.owner(version=2)
        answer = owner.request(catalog.MAX_WAIT_SECONDS)
        now = self.clock()
        served, health, _ = catalog_http.serve_time_answer(
            answer, now, owner.response_freshness)
        self.assertIs(served, answer)
        self.assertEqual(health, contract.HEALTH_FRESH)
        withheld, health, problem = catalog_http.serve_time_answer(
            answer, now)
        self.assertIsNone(withheld["response"])
        self.assertEqual((health, problem),
                         (contract.HEALTH_INCOMPATIBLE,
                          contract.PROBLEM_RESPONSE_CONTRACT))


class ChainPassTests(V2Fixture):
    """The capture's chain passes over REAL files, in-process (the same
    boundary functions a boundary child runs)."""

    def children_file(self, root, raw):
        path = self.state_path(root, v2.CHILDREN_FILE)
        if raw is None:
            if os.path.exists(path):
                os.unlink(path)
            return
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(raw)

    def projection(self, root):
        return service._children_projection(service._state_pass_v2([root])[0])

    def records(self, count, parent=TASK):
        return json.dumps({"version": 1, "children": [
            {"parent_task_id": parent, "task_id": "T%03d" % i,
             "repo": "/synthetic/%03d" % i, "dependency": True,
             "task_status": "ACTIVE", "workspace_id": "w",
             "agents": {"lead1": "a%03d" % i}} for i in range(count)]})

    def test_the_four_outcomes_are_never_collapsed(self):
        parent = self.herd()
        cases = (
            ('{"version": 1, "children": []}', v2.CHILDREN_OBSERVED_EMPTY,
             None, v2.CAVEAT_WRITER_RESET),
            (None, v2.CHILDREN_UNAVAILABLE, v2.PROBLEM_CHILDREN_ABSENT, None),
            ("", v2.CHILDREN_UNAVAILABLE, v2.PROBLEM_CHILDREN_ZERO_BYTES,
             None),
            ('{"children": [', v2.CHILDREN_UNAVAILABLE,
             v2.PROBLEM_CHILDREN_NOT_JSON, None),
            (self.records(1), v2.CHILDREN_OBSERVED, None, None))
        for raw, state, problem, caveat in cases:
            with self.subTest(raw=raw):
                self.children_file(parent, raw)
                got = self.projection(parent)
                self.assertEqual((got["state"], got["problem"],
                                  got["caveat"]), (state, problem, caveat))
                v2.validate_children(got, "children")

    def test_the_record_bound_and_the_other_parent_count(self):
        parent = self.herd()
        self.children_file(parent, self.records(v2.MAX_CHILD_RECORDS))
        self.assertEqual(self.projection(parent)["record_count"],
                         v2.MAX_CHILD_RECORDS)
        self.children_file(parent, self.records(v2.MAX_CHILD_RECORDS + 1))
        got = self.projection(parent)
        self.assertEqual((got["state"], got["problem"], got["record_count"]),
                         (v2.CHILDREN_UNAVAILABLE,
                          v2.PROBLEM_CHILDREN_OVER_LIMIT,
                          v2.MAX_CHILD_RECORDS + 1))
        self.children_file(parent, self.records(3, parent="ANOTHER-TASK"))
        got = self.projection(parent)
        self.assertEqual((got["state"], got["other_parent_count"]),
                         (v2.CHILDREN_OBSERVED_EMPTY, 3))

    def test_depth_one_tolerance_and_capacity(self):
        parent = self.herd()
        child = self.herd(task_id="T-CHILD")
        broken = self.herd(task_id="T-BROKEN")
        with open(self.state_path(broken, "task.json"), "w") as handle:
            handle.write("{not json")
        grandchild = self.herd(task_id="T-GRAND")
        missing = os.path.join(self.root, "no-such-child")
        self.children_file(child, json.dumps({"children": [
            {"parent_task_id": "T-CHILD", "task_id": "T-GRAND",
             "repo": grandchild}]}))
        self.children_file(parent, json.dumps({"children": [
            {"parent_task_id": TASK, "task_id": tid, "repo": repo}
            for tid, repo in (("T-CHILD", child), ("T-BROKEN", broken),
                              ("T-MISSING", missing))]}))
        value = service._first_pass_chains({
            "roots": [], "discovered": [parent], "chains": True,
            "capacity": 15})
        accounts = dict((a["root"], a.get("problem"))
                        for a in value["children"])
        self.assertEqual(accounts, {
            child: None, broken: service.PROBLEM_STATE_MALFORMED,
            missing: service.PROBLEM_ROOTS_INVALID})
        self.assertNotIn(grandchild, accounts, "depth 1: never followed")
        capped = service._first_pass_chains({
            "roots": [], "discovered": [parent], "chains": True,
            "capacity": 1})
        self.assertEqual(len(capped["children"]), 1)
        self.assertEqual(len(capped["child_over_capacity"]), 2)

    def test_real_statuses_drive_the_two_separate_rules(self):
        for status, ended, complete in (
                ("ACTIVE", False, False), ("FAILED", True, False),
                ("ABANDONED", True, False), ("COMPLETE", True, True),
                ("COMPLETED", True, False), ("complete", True, True),
                (None, False, False)):
            with self.subTest(status=status):
                block = service._root_task(0, {"id": "T", "status": status})
                self.assertEqual((block["membership_ended"],
                                  block["dependency_complete"]),
                                 (ended, complete))

    def test_a_request_without_chains_keeps_the_v1_shapes(self):
        root = self.herd()
        self.children_file(root, self.records(2))
        first = service._first_pass({"roots": [root]})
        self.assertEqual([len(d) for d in first], [3])
        snapshot = service._project(self.payload([root]))
        self.assertEqual(set(snapshot), set(contract.SNAPSHOT_KEYS))
        got = self.owner_snapshot([root])
        self.assertIsNone(got.chain_observation)

    def owner_snapshot(self, roots):
        owner = service.SnapshotService(roots, self.clock, self.plan())
        self.addCleanup(lambda: owner.settle(10))
        self.addCleanup(owner.shutdown)
        return owner.request(service.MAX_WAIT_SECONDS)


class CompositionTests(V2Fixture):
    """The Mission State composer over a REAL Mission Core store."""

    def setUp(self):
        super(CompositionTests, self).setUp()
        (self.state_dir, self.source_id, self.mission_id,
         self.workspace) = self.running()
        self.source = self.registry.sources()[0]
        self.composer = mission_state.MissionStateComposer(self.clock)

    def views(self):
        return sources_module.read_mission_core(
            self.source, self.clock(), sources_module.QueryBudget())[
                "missions"]

    def chains(self, status="ACTIVE", review=None):
        return v2.read_chain_observation(pure.chain_value(
            [pure.root(self.workspace, TASK, status,
                       review=review or pure.review_admitted("APPROVE"))],
            observed_at=self.clock()), self.clock())

    def compose(self, samples, chains=None):
        blocks = self.composer.compose(samples, self.source, self.views(),
                                       chains or self.chains())
        return blocks["%s:%s" % (self.source_id, self.mission_id)]

    def move_the_head(self):
        missions = mission_service.MissionService(
            mission_store.MissionStore(self.state_dir), self.clock)
        missions.open_blocker(
            self.mission_id, missions.mint_state_operation_id(AUTHENTICATED),
            missions.get_state(self.mission_id)["sequence"],
            "synthetic.blocker", "SYNTHETIC", AUTHENTICATED)

    def test_the_sampled_cursor_stays_moved_and_resampling_would_hide_it(
            self):
        samples = self.composer.sample([self.source])
        self.move_the_head()           # the head moves mid-collection
        moved = self.compose(samples)["report"]
        self.assertEqual(moved["provenance"]["collection"]["status"],
                         "moved")
        self.assertIsNotNone(moved["provenance"]["moved_during_collection"])
        # Reported, but it never resolves: the APPROVE affirms nothing.
        self.assertEqual(moved["review"]["value"], "APPROVE")
        self.assertIn("review_inapplicable", moved["completion"]["holds"])
        # The wrong implementation (sample after collection) reads current.
        resampled = self.compose(self.composer.sample([self.source]))[
            "report"]
        self.assertEqual(resampled["provenance"]["collection"]["status"],
                         "current")
        self.assertNotIn("review_inapplicable",
                         resampled["completion"]["holds"])

    def test_no_kind_is_ever_supplied_as_unknown(self):
        block = self.compose(self.composer.sample([self.source]))
        supplied = dict((kind, entry["supplied"])
                        for kind, entry in block["inputs"]["kinds"].items())
        self.assertEqual(supplied["delivery"], v2.SUPPLIED_UNAVAILABLE)
        self.assertNotIn(v2.SUPPLIED_UNKNOWN, supplied.values())
        self.assertEqual(block["report"]["delivery"]["standing"],
                         "unavailable")
        self.assertIn("no attested delivery receipt",
                      block["report"]["delivery"]["detail"])

    def test_unmapped_task_literals_refuse_and_mapped_ones_report(self):
        samples = self.composer.sample([self.source])
        for status in ("COMPLETED", "ABANDONED", "IDLE", "ERROR",
                       "MALFORMED", "Active", "RUNNING"):
            with self.subTest(status=status):
                task = self.compose(samples, self.chains(status))["report"][
                    "task"]
                self.assertEqual(task["standing"], "unavailable")
                self.assertIn("never coerced", task["detail"])
        for status in ("ACTIVE", "BLOCKED", "COMPLETE", "FAILED",
                       "NOT_STARTED"):
            with self.subTest(status=status):
                task = self.compose(samples, self.chains(status))["report"][
                    "task"]
                self.assertEqual((task["standing"], task["value"]),
                                 ("reported", status))

    def test_baseline_unobserved_and_applicability_unproven(self):
        block = self.compose(self.composer.sample([self.source]))
        candidate = block["report"]["candidate"]
        self.assertEqual(candidate["standing"], "reported")
        self.assertIn("baseline_digest_sha256", candidate["value"])
        self.assertIsNone(candidate["value"]["baseline_digest_sha256"])
        self.assertEqual(block["inputs"]["baseline"],
                         v2.BASELINE_UNOBSERVED)
        # Beside an APPROVE review, applicability is still unproven.
        self.assertEqual(block["report"]["review"]["value"], "APPROVE")
        self.assertEqual(block["inputs"]["candidate_applicability"],
                         v2.CANDIDATE_APPLICABILITY_UNPROVEN)

    def test_an_unparsed_gate_is_never_a_rejection(self):
        report = self.compose(self.composer.sample([self.source]),
                              self.chains(review=pure.review_admitted(
                                  "MISSING")))["report"]
        self.assertEqual(report["review"]["standing"], "unavailable")
        self.assertIn("not a rejection", report["review"]["detail"])
        holds = report["completion"]["holds"]
        self.assertIn("review_unavailable", holds)
        self.assertNotIn("review_not_approved", holds)
        stale = self.compose(self.composer.sample([self.source]),
                             self.chains(status="COMPLETE",
                                         review=pure.review_admitted(
                                             "APPROVE", "COMPLETE")))
        self.assertEqual(stale["report"]["review"]["standing"],
                         "unavailable")
        self.assertIn("stale", stale["report"]["review"]["detail"])

    def test_the_read_only_store_refuses_every_write(self):
        document = mission_state.load_store_document(self.state_dir)
        store = mission_state.ReadOnlyMissionStore(document)
        with self.assertRaises(mission_state.ReadOnlyStoreError):
            store.lock()
        with self.assertRaises(mission_state.ReadOnlyStoreError):
            store.save(document)
        store.load()["missions"].clear()
        self.assertTrue(store.load()["missions"])


class RefreshReadBudgetTests(V2Fixture):
    """Round 3, B2: ONE shared read budget per version 2 refresh, enforced
    against every read ATTEMPT, over REAL Mission Core stores."""

    def stores(self, count, herd=True):
        sizes = {}
        for index in range(count):
            source_id = "core%d" % index if count <= 10 else (
                "core%02d" % index)
            state_dir = self.mission_state(source_id)
            task_id = "task-%04d" % (index + 1)
            _, workspace = self.running_mission(state_dir, task_id=task_id)
            if herd:
                self.herd(root=workspace, task_id=task_id, status="ACTIVE")
            self.register(source_id=source_id, store=state_dir)
            sizes[source_id] = os.path.getsize(os.path.join(
                state_dir, mission_store.MISSIONS_FILE_NAME))
        return sizes

    def test_backward_one_refresh_never_pulls_past_its_stated_cap(self):
        """THE BACKWARD REPRODUCTION of the round 3 B2 finding, through
        interfaces BOTH candidates have: the version 2 owner, its stated
        ``refresh_budget``, ``fileread`` and the ``sources`` bound seam. The
        finding (17 stores x 16 MiB against 4 x 64 MiB) scaled through that
        seam: 17 REAL stores, per-read bound S = the largest store, per-pass
        bound 4S, so the stated cap is 16S while load 1 alone reads ~17S.
        EVERY byte a store read takes off disk is measured - success or
        failure, detection byte and partial reads included. Behaviour
        FIRST; the new diagnostic block is asserted only afterwards."""
        sizes = self.stores(17, herd=False)
        bound = max(sizes.values())
        for name, value in (("MAX_STORE_READ_BYTES", bound),
                            ("MAX_QUERY_INPUT_BYTES", 4 * bound)):
            patcher = mock.patch.object(sources_module, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        pulled = []
        local = threading.local()
        real_read = fileread.read_private_file

        class Measured(object):
            def __getattr__(self, name):
                return getattr(os, name)

            def read(self, descriptor, count):
                data = os.read(descriptor, count)
                if getattr(local, "store", False):
                    pulled.append(len(data))
                return data

        def measured(path, max_bytes):
            local.store = os.path.basename(path) == (
                mission_store.MISSIONS_FILE_NAME)
            try:
                return real_read(path, max_bytes)
            finally:
                local.store = False
        for name, value in (("os", Measured()),
                            ("read_private_file", measured)):
            patcher = mock.patch.object(fileread, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        answer = self.owner(version=2).request(catalog.MAX_WAIT_SECONDS)
        cap = answer["runtime_capture"]["refresh_budget"][
            "sum_max_query_input_bytes"]
        self.assertEqual(cap, 16 * bound)
        self.assertGreater(sum(sizes.values()), cap)
        # BEHAVIOUR: what one refresh took off disk stays within its cap.
        self.assertLessEqual(
            sum(pulled), cap, "one refresh pulled %d store bytes in %d"
            " reads against its stated cap of %d" % (
                sum(pulled), len(pulled), cap))
        # BEHAVIOUR: what it could not read it says so, source by source.
        response = answer["response"]
        for source in response["sources"]:
            self.assertIn((source["health"], source["problem"]), (
                (contract.HEALTH_FRESH, None),
                (contract.HEALTH_UNAVAILABLE,
                 sources_module.PROBLEM_QUERY_INPUT_BUDGET)))
        # Only now the new diagnostic block, which must agree.
        shared = answer["runtime_capture"]["refresh_budget"].get(
            "shared_input")
        self.assertIsNotNone(shared)
        self.assertLessEqual(sum(pulled), shared["charged_bytes"])
        self.assertLessEqual(shared["charged_bytes"], cap)
        self.assertIs(shared["exhausted"], True)

    def test_a_failed_read_is_charged_and_no_further_read_is_made(self):
        sizes = self.stores(3)
        registered = self.registry.sources()
        ids = [source["source_id"] for source in registered]
        cap = sum(sizes.values())
        budget = query_v2.RefreshReadBudget(cap)
        composer = mission_state.MissionStateComposer(self.clock)
        made = []
        real = mission_state.fileread.read_private_file

        def spy(path, max_bytes):
            made.append(max_bytes)
            return real(path, max_bytes)
        with mock.patch.object(mission_state.fileread, "read_private_file",
                               spy):
            samples = composer.sample(registered, budget)
            # Each load holds all that remains less the detection byte; the
            # third can no longer cover its file, FAILS, and KEEPS its hold.
            self.assertEqual(made, [cap - 1, cap - sizes[ids[0]] - 1,
                                    sizes[ids[2]] - 1])
            self.assertIsNotNone(samples[ids[1]].document)
            failed = samples[ids[2]]
            self.assertTrue(failed.budget_exhausted)
            self.assertIn(mission_state.BUDGET_EXHAUSTED, failed.reason)
            # The FAILED attempt is charged: the cap, not the successes.
            self.assertEqual(budget.summary(), {
                "max_bytes": cap, "charged_bytes": cap, "reads_refused": 1,
                "exhausted": True})
            # Repeated: nothing can be held, so NO read is made at all.
            again = composer.sample(registered, budget)
            self.assertEqual(len(made), 3)
            self.assertTrue(all(sample.budget_exhausted
                                for sample in again.values()))
            self.assertEqual((budget.charged, budget.refused_reads),
                             (cap, 4))
        # Composed, the exhausted source says so: never observed, never an
        # empty report, never an empty map.
        source = registered[2]
        views = sources_module.read_mission_core(
            source, self.clock(), sources_module.QueryBudget())["missions"]
        chains = v2.read_chain_observation(pure.chain_value(
            [], observed_at=self.clock()), self.clock())
        blocks = composer.compose(samples, source, views, chains, budget)
        self.assertEqual(len(blocks), len(views))
        for block in blocks.values():
            self.assertEqual(
                (block["state"], block["problem"], block["report"]),
                (v2.MISSION_STATE_UNAVAILABLE,
                 v2.PROBLEM_MISSION_STATE_BUDGET, None))

    def test_every_stage_holds_against_the_one_stated_cap(self):
        sizes = self.stores(2)
        total = sum(sizes.values())
        # The stated cap is UNCHANGED (four passes with discovery); every
        # stage is charged against it: discovery, load 1, query, load 2.
        answer = self.owner(version=2).request(catalog.MAX_WAIT_SECONDS)
        stated = answer["runtime_capture"]["refresh_budget"]
        self.assertEqual(stated["sum_max_query_input_bytes"],
                         4 * sources_module.MAX_QUERY_INPUT_BYTES)
        self.assertEqual(stated.get("shared_input"), {
            "max_bytes": stated["sum_max_query_input_bytes"],
            "charged_bytes": 4 * total, "reads_refused": 0,
            "exhausted": False})
        self.assertEqual([s["health"] for s in answer["response"]["sources"]],
                         [contract.HEALTH_FRESH] * 2)
        real = query_v2.RefreshReadBudget
        query_spent = 2 * total + sizes["core0"]
        # Three passes, plus the one detection byte the last query read
        # holds; load 2's first read then holds that byte and FAILS.
        load_two = 3 * total + 1
        for cap in (query_spent, load_two):
            with self.subTest(cap=cap):
                with mock.patch.object(query_v2, "RefreshReadBudget",
                                       lambda _, cap=cap: real(cap)):
                    answer = self.owner(version=2).request(
                        catalog.MAX_WAIT_SECONDS)
                self.assertEqual(
                    answer["runtime_capture"]["refresh_budget"].get(
                        "shared_input"),
                    {"max_bytes": cap, "charged_bytes": cap,
                     "reads_refused": 2, "exhausted": True})
                response = v2.validate_response_v2(answer["response"])
                if cap == query_spent:
                    # The query pass ran out: core0's read FAILED holding
                    # all that remained, core1's was never made. Both
                    # sources say so; neither is a fresh, empty source.
                    self.assertEqual(
                        [(s["health"], s["problem"])
                         for s in response["sources"]],
                        [(contract.HEALTH_UNAVAILABLE,
                          sources_module.PROBLEM_QUERY_INPUT_BUDGET)] * 2)
                    self.assertEqual(response["missions"], [])
                else:
                    # Load 2 ran out: every Mission State says so.
                    self.assertEqual(len(response["missions"]), 2)
                    for mission in response["missions"]:
                        block = mission["mission_state"]
                        self.assertEqual(
                            (block["state"], block["problem"]),
                            (v2.MISSION_STATE_UNAVAILABLE,
                             v2.PROBLEM_MISSION_STATE_BUDGET))


if __name__ == "__main__":
    unittest.main()
