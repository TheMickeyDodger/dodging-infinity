"""Tests for local retention (Deliverable 2): ``mission_retention``.

Every test runs the REAL stores over a real temporary protected directory it creates and owns (mode
700, files 600, through each store's own writer); none reads, writes or names the user's state
directory. Missions are created through the REAL stack (the panel fixture: conversation -> journal ->
local request surface -> Mission Core -> the real bridge with the bridge suite's scripted fakes), so
what is reclaimed is a record that EXISTED before reclamation ran, written by the production writers,
never a record built in the reduced shape. Each assertion is at the ENFORCEMENT point: the bytes on
disk, the store's own validation, and the refusal the action itself raises.

Termination: every test inherits the suite's SIGALRM watchdog (``Bounded``), so no test depends on
the code under test to return.
"""

import json
import os
import re
import stat
import sys
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "tests"))

from local_request import store as store_module  # noqa: E402
from mission import record as mission_record  # noqa: E402
from mission import state as mission_state  # noqa: E402
from mission_panel import journal as journal_module  # noqa: E402
from mission_panel import service as service_module  # noqa: E402
from mission_retention import eligibility  # noqa: E402
from mission_retention import reclaim  # noqa: E402
from mission_retention import store as retention_store  # noqa: E402
from target_runtime import dispatch as dispatch_module  # noqa: E402
from target_runtime import mission_run_guard as run_guard_module  # noqa: E402
from test_mission_bridge import raw_observation  # noqa: E402
from test_mission_panel_conversation import GOOD  # noqa: E402
from test_mission_panel_service import PanelFixture  # noqa: E402

DAY = 24 * 60 * 60
USER_WORDS = "make the probe stable"
OBJECTIVE = GOOD["objective"]
TS_STATUS = (REPO_ROOT / "integrations" / "pixel-agents" / "webview-ui" / "src" / "components"
             / "diMissionStatus.ts")
CAPACITY_CASES = (REPO_ROOT / "integrations" / "pixel-agents" / "webview-ui" / "test" / "fixtures"
                  / "di-capacity-basis-cases.FIXTURE.json")


class RetentionFixture(PanelFixture):

    def setUp(self):
        super(RetentionFixture, self).setUp()
        self.guard = run_guard_module.RunGuard(self.state)
        self.retention = reclaim.MissionCoreRetention(self.state, self.clock, self.guard)

    # -- Missions through the real stack --------------------------------------------------

    def completed(self):
        view = self.of_record()
        self.approve(view)
        self.service.run(view["request_ref"], "dispatch")
        self.observer.raw = raw_observation()
        self.service.run(view["request_ref"], "observe")
        self.finish_team(view["mission_id"])
        return self.service.run(view["request_ref"], "verify")["request"]

    def rejected(self):
        view = self.of_record()
        args = self.decide_args(view)
        args.pop("reply")
        return self.service.reject(view["request_ref"], **args)["request"]

    def abandoned_unstarted(self):
        """The shape of the accepted ``di-office-state-progress-abandoned-v2`` record, through the
        real writers: of record (AWAITING_DECISION), no start, Mission State progress ABANDONED."""
        view = self.of_record()
        mission_id = view["mission_id"]
        operation = self.missions.mint_state_operation_id(self.context)
        sequence = self.missions.get_state(mission_id)["sequence"]
        self.missions.abandon(mission_id, operation, sequence, "the person moved on",
                              self.context)
        return view

    # -- the disk ---------------------------------------------------------------------------

    def bytes_of(self, name):
        path = os.path.join(self.state, name)
        if not os.path.exists(path):
            return b""
        with open(path, "rb") as handle:
            return handle.read()

    def document(self, name):
        return json.loads(self.bytes_of(name).decode("utf-8"))

    def record(self, mission_id):
        return self.retention.retention.load()["records"].get(
            retention_store.retention_key(retention_store.FAMILY_MISSION_CORE, mission_id))

    def terminal_at(self, mission_id):
        mission = self.document("missions.json")["missions"][mission_id]
        return eligibility.mission_core(
            mission, self.document("missions.json")["mission_state"].get(mission_id)).terminal_at

    def authority(self):
        doc = self.document("missions.json")
        return json.dumps({key: doc[key] for key in (
            "authority_ledger", "reservations", "authorizations")}, sort_keys=True)

    def assert_payload_gone(self, mission_id):
        journal = self.bytes_of("mission_panel_journal.json").decode("utf-8")
        requests = self.bytes_of("local_requests.json").decode("utf-8")
        self.assertNotIn(USER_WORDS, journal)
        self.assertNotIn(OBJECTIVE, journal)
        self.assertNotIn("Here is my proposal", journal)
        self.assertNotIn(OBJECTIVE, requests)
        # Mission Core's own record persists beyond reclamation (load-bearing for replay)
        self.assertIn(OBJECTIVE, self.bytes_of("missions.json").decode("utf-8"))
        # and every store still loads through its own fail-closed boundary
        journal_module.PanelJournal(self.state).load()
        store_module.LocalRequestStore(self.state).load()
        retention_store.RetentionStore(self.state).load()

    def retention_records(self):
        """The coordinator store's records as on disk ({} when it was never written)."""
        return retention_store.RetentionStore(self.state).load()["records"]

    def assert_payload_intact(self):
        self.assertIn(USER_WORDS, self.bytes_of("mission_panel_journal.json").decode("utf-8"))
        self.assertIn(OBJECTIVE, self.bytes_of("local_requests.json").decode("utf-8"))


# == the coordinator store =====================================================================


class RetentionStoreTests(RetentionFixture):

    def sample(self, record_id="mn-" + "1" * 32, phase=retention_store.PHASE_OBSERVED):
        return {"key": retention_store.retention_key(retention_store.FAMILY_MISSION_CORE, record_id),
                "family": retention_store.FAMILY_MISSION_CORE, "record_id": record_id,
                "first_eligible_observed_at": 10, "terminal_at": None,
                "terminal_at_source": None, "basis": "no_start_recorded",
                "basis_source": "run.intent = null", "phase": phase, "phase_at": 10,
                "summary": None, "retained_for": []}

    def test_RS1_closed_schema_refused_on_save_and_on_load(self):
        store = retention_store.RetentionStore(self.state)
        document = retention_store.default_document()
        retention_store.put_record(document, self.sample())
        store.save(document)
        self.assertEqual(stat.S_IMODE(os.stat(store.path).st_mode), 0o600)
        bad = json.loads(open(store.path).read())
        bad["records"][self.sample()["key"]]["extra"] = 1
        with self.assertRaises(retention_store.RetentionStoreError):
            store.save(bad)
        with open(store.path, "w") as handle:
            json.dump(bad, handle)
        os.chmod(store.path, 0o600)
        with self.assertRaises(retention_store.RetentionStoreError):
            store.load()

    def test_RS2_a_reclaimed_record_must_keep_its_summary_and_a_deleted_one_none(self):
        document = retention_store.default_document()
        with self.assertRaises(retention_store.RetentionStoreError):
            retention_store.put_record(document, self.sample(phase=retention_store.PHASE_RECLAIMED))
        deleted = self.sample(phase=retention_store.PHASE_DELETED)
        deleted["summary"] = {"final_state": "x", "final_state_source": "y",
                              "verdict": {"state": "recorded", "detail": "v", "source": "s"},
                              "ci": {"state": "not_recorded_locally", "detail": None, "source": "s"},
                              "comments": {"state": "not_recorded_locally", "detail": None,
                                           "source": "s"}}
        with self.assertRaises(retention_store.RetentionStoreError):
            retention_store.put_record(document, deleted)

    def test_RS3_open_permissions_refuse_before_anything_is_read(self):
        store = retention_store.RetentionStore(self.state)
        store.save(retention_store.default_document())
        os.chmod(store.path, 0o644)
        with self.assertRaises(retention_store.RetentionStoreError):
            store.load()

    def test_RS5_the_shared_and_derived_bounds_are_pinned_to_what_they_must_admit(self):
        from target_runtime import evidence_preservation
        from workflow_authority import record as workflow_record
        from workflow_authority import store as workflow_store
        # deliberate, not a coincidence: a retention record names any workflow id the workflow
        # store admits, and any Mission Core id ("mn-" and 32 hex)
        self.assertEqual(retention_store.MAX_ID_CHARS, workflow_record.MAX_ID_CHARS)
        self.assertGreaterEqual(retention_store.MAX_ID_CHARS,
                                len(mission_record.MISSION_ID_PREFIX) + 1 + mission_record.ID_HEX_CHARS)
        # one preserved document is read whole: its contents are capped by its owner, and JSON
        # escaping can grow a byte at most six times (\u00XX); a widened owner cap fails here
        self.assertGreaterEqual(reclaim.MAX_PRESERVED_BYTES,
                                6 * evidence_preservation.MAX_TOTAL_BYTES + 65536)
        # the removal hand-off's schema version, by value (the bound-constant registry derives only
        # bound names)
        self.assertEqual(workflow_store.WORKFLOW_REMOVALS_SCHEMA_VERSION, 1)
        self.assertEqual(workflow_store.MAX_WORKFLOW_REMOVALS, 4 * workflow_store.MAX_WORKFLOW_RECORDS)

    def test_RS4_at_the_cap_it_refuses_and_never_evicts(self):
        document = retention_store.default_document()
        with mock.patch.object(retention_store, "MAX_RETENTION_RECORDS", 2):
            retention_store.put_record(document, self.sample("mn-" + "1" * 32))
            retention_store.put_record(document, self.sample("mn-" + "2" * 32))
            with self.assertRaises(retention_store.RetentionStoreError) as caught:
                retention_store.put_record(document, self.sample("mn-" + "3" * 32))
        self.assertEqual(caught.exception.problem, retention_store.PROBLEM_FULL)
        self.assertEqual(len(document["records"]), 2)


# == eligibility, and the pins that keep it in step ============================================


def mission(state, **fields):
    value = {"mission_id": "mn-" + "a" * 32, "state": state, "current_revision": 1,
             "lifecycle": [], "decisions": []}
    value.update(fields)
    return value


def progress_record(progress, activations=(), closed_at=50):
    return {"progress": progress, "contract_activations": list(activations),
            "closure": {"progress": progress, "closed_at": closed_at}}


class EligibilityTests(unittest.TestCase):

    def test_E1_each_mission_core_basis_names_its_source_field(self):
        done = eligibility.mission_core(mission(
            "COMPLETED", run={"intent": {"x": 1}},
            lifecycle=[{"to_state": "COMPLETED", "recorded_at": 70}]))
        self.assertEqual((done.basis, done.basis_source, done.terminal_at),
                         ("verified_target_stopped", 'state = "COMPLETED"', 70))
        for quiescence in eligibility.QUIESCENT_CANCELS:
            cancelled = eligibility.mission_core(mission(
                "CANCELLED", run={"intent": {"x": 1}, "cancel": {
                    "quiescence": quiescence, "completed_at": 90}},
                lifecycle=[{"to_state": "CANCELLED", "recorded_at": 80}]))
            self.assertEqual(cancelled.basis, "cancel_quiescent", quiescence)
            self.assertEqual(cancelled.terminal_at, 90, "dated by the observation that proved it")
        unproven = eligibility.mission_core(mission(
            "CANCELLED", run={"intent": {"x": 1}, "cancel": {
                "quiescence": mission_record.CANCEL_QUIESCENCE_UNPROVEN, "completed_at": 90}}))
        self.assertEqual(unproven.reason, eligibility.REASON_NO_POSITIVE_BASIS)
        rejected = eligibility.mission_core(mission(
            "REJECTED", decisions=[{"outcome": {"resulting_state": "REJECTED"}, "decided_at": 60}]))
        self.assertEqual((rejected.basis, rejected.terminal_at), ("no_start_recorded", 60))

    def cancelled(self, quiescence, control="not_applicable"):
        return eligibility.mission_core(mission(
            "CANCELLED", run={"intent": {"x": 1}, "cancel": {
                "requested_at": 70, "achieved": "cancelled_after_observed_running",
                "control": control, "quiescence": quiescence, "completed_at": 90}},
            lifecycle=[{"to_state": "CANCELLED", "recorded_at": 70}]))

    def test_EQ1_quiescence_not_applicable_is_no_positive_basis(self):
        value = self.cancelled("not_applicable")
        self.assertFalse(value.eligible)
        self.assertEqual(value.reason, eligibility.REASON_NO_POSITIVE_BASIS)

    def test_EQ2_quiescence_unproven_is_no_positive_basis(self):
        value = self.cancelled(mission_record.CANCEL_QUIESCENCE_UNPROVEN)
        self.assertFalse(value.eligible)
        self.assertEqual(value.reason, eligibility.REASON_NO_POSITIVE_BASIS)

    def test_EQ3_the_control_value_is_never_read_owned_group_already_gone_with_unproven_quiescence(self):
        value = self.cancelled(mission_record.CANCEL_QUIESCENCE_UNPROVEN,
                               control="owned_group_already_gone")
        self.assertFalse(value.eligible)
        self.assertEqual(value.reason, eligibility.REASON_NO_POSITIVE_BASIS)

    def test_EQ4_the_documented_pairing_owned_group_already_gone_with_owned_group_reaped_is_eligible(self):
        value = self.cancelled(mission_record.CANCEL_QUIESCENCE_OWNED_GROUP_REAPED,
                               control="owned_group_already_gone")
        self.assertTrue(value.eligible)
        self.assertEqual((value.basis, value.basis_source),
                         ("cancel_quiescent", 'run.cancel.quiescence = "owned_group_reaped"'))

    def test_EQ5_the_quiescent_set_is_exactly_the_three_approved_values(self):
        self.assertEqual(set(eligibility.QUIESCENT_CANCELS),
                         {"nothing_started", "owned_group_reaped", "task_observed_stopped"})
        self.assertEqual(set(eligibility.QUIESCENT_CANCELS),
                         set(mission_record.CANCEL_QUIESCENCE) - {
                             mission_record.CANCEL_QUIESCENCE_UNPROVEN})
        source = (REPO_ROOT / "mission_retention" / "eligibility.py").read_text()
        self.assertNotRegex(source, r'\[\s*"control"\s*\]|\.get\(\s*"control"',
                            "eligibility never reads the cancel's control value")

    def test_E2_terminal_standing_after_a_start_with_no_stopped_proof_is_refused(self):
        # BLOCKED after a failed verification (verify_target_not_stopped): the target can still run
        blocked = eligibility.mission_core(mission("BLOCKED", run={"intent": {"x": 1}}))
        self.assertFalse(blocked.eligible)
        self.assertEqual(blocked.reason, eligibility.REASON_NO_POSITIVE_BASIS)

    def test_E3_progress_closure_is_terminal_eligibility_when_nothing_can_dispatch(self):
        # the accepted D1 fixture's shape: AWAITING_DECISION, no start, progress ABANDONED
        value = eligibility.mission_core(
            mission("AWAITING_DECISION"), progress_record(mission_state.PROGRESS_ABANDONED))
        self.assertTrue(value.eligible)
        self.assertEqual((value.basis, value.basis_source, value.terminal_at,
                          value.terminal_at_source),
                         ("no_start_recorded", "run.intent = null", 50,
                          "mission_state.closure.closed_at"))

    def test_E4_progress_closure_with_a_contract_active_at_the_current_revision_is_refused(self):
        # the _activate_contract early return: a dispatch would never reach the refused activation
        value = eligibility.mission_core(
            mission("AUTHORIZED", current_revision=2),
            progress_record(mission_state.PROGRESS_ABANDONED, [{"revision": 2}]))
        self.assertFalse(value.eligible)
        self.assertEqual(value.reason, eligibility.REASON_DISPATCH_POSSIBLE)
        # an activation at an EARLIER revision cannot satisfy that early return
        earlier = eligibility.mission_core(
            mission("AUTHORIZED", current_revision=2),
            progress_record(mission_state.PROGRESS_ABANDONED, [{"revision": 1}]))
        self.assertTrue(earlier.eligible)

    def test_E5_progress_closure_beside_a_live_or_started_run_is_refused(self):
        running = eligibility.mission_core(
            mission("RUNNING", run={"intent": {"x": 1}}),
            progress_record(mission_state.PROGRESS_ABANDONED))
        self.assertEqual(running.reason, eligibility.REASON_RUNNING)
        started = eligibility.mission_core(
            mission("AUTHORIZED", run={"intent": {"x": 1}}),
            progress_record(mission_state.PROGRESS_ABANDONED))
        self.assertEqual(started.reason, eligibility.REASON_NO_POSITIVE_BASIS)
        open_progress = eligibility.mission_core(
            mission("AWAITING_DECISION"), progress_record(mission_state.PROGRESS_IN_PROGRESS))
        self.assertEqual(open_progress.reason, eligibility.REASON_NOT_TERMINAL)

    def test_E6_workflow_bases(self):
        done = eligibility.workflow({"phase": "COMPLETED", "receipts": []})
        self.assertEqual(done.basis, "workflow_target_stopped")
        never = eligibility.workflow({"phase": "BLOCKED", "receipts": [
            {"kind": "preparation", "bounded_summary": "prepared"}]})
        self.assertEqual(never.basis, "workflow_not_dispatched")
        dispatched = eligibility.workflow({"phase": "BLOCKED", "receipts": [
            {"kind": "evidence", "bounded_summary":
             dispatch_module.DISPATCH_RECEIPT_MARKER + " 1 to o/r (dispatch 1, exact)"}]})
        self.assertEqual(dispatched.reason, eligibility.REASON_NO_POSITIVE_BASIS)
        self.assertEqual(eligibility.workflow({"phase": "DISPATCHED"}).reason,
                         eligibility.REASON_RUNNING)

    def test_E9_the_re_declared_standing_tables_and_literals_equal_their_owners(self):
        from mission_sources import contract as sources_contract
        from mission_sources import sources as sources_module
        from workflow_authority import record as workflow_record
        self.assertEqual(eligibility.MISSION_CORE_STANDING, sources_module.MISSION_CORE_STANDING)
        self.assertEqual(eligibility.WORKFLOW_STORE_STANDING,
                         sources_module.WORKFLOW_STORE_STANDING)
        self.assertEqual((eligibility.STANDING_ACTIVE, eligibility.STANDING_INACTIVE,
                          eligibility.STANDING_TERMINAL),
                         (sources_contract.STANDING_ACTIVE, sources_contract.STANDING_INACTIVE,
                          sources_contract.STANDING_TERMINAL))
        self.assertEqual((eligibility.WORKFLOW_COMPLETED, eligibility.WORKFLOW_BLOCKED,
                          eligibility.WORKFLOW_DISPATCHED, eligibility.RECEIPT_KIND_EVIDENCE),
                         (workflow_record.PHASE_COMPLETED, workflow_record.PHASE_BLOCKED,
                          workflow_record.PHASE_DISPATCHED, workflow_record.RECEIPT_KIND_EVIDENCE))

    def test_E7_pins_the_re_declared_dispatch_marker_and_count_to_the_runtime(self):
        self.assertEqual(eligibility.DISPATCH_RECEIPT_MARKER,
                         dispatch_module.DISPATCH_RECEIPT_MARKER)
        entry = {"receipts": [
            {"kind": "evidence", "bounded_summary": dispatch_module.DISPATCH_RECEIPT_MARKER + " x"},
            {"kind": "preparation", "bounded_summary": dispatch_module.DISPATCH_RECEIPT_MARKER},
            {"kind": "evidence", "bounded_summary": "other"}]}
        self.assertEqual(eligibility.dispatch_count(entry), dispatch_module.dispatch_count(entry))

    def test_E8_the_bases_agree_case_by_case_with_the_office_on_one_shared_table(self):
        # Round 8 seam (lead): name equality is not semantic equality. ONE committed table of shaped
        # records (webview-ui/test/fixtures/di-capacity-basis-cases.FIXTURE.json) is run through
        # the backend here and through the office in webview-ui/test/diCapacityBasisCases.test.ts.
        # A basis shared by name must decide the same case from the same record fact on both sides;
        # anything office-only is declared, with its reason, or this fails.
        table = json.loads(CAPACITY_CASES.read_text(encoding="utf-8"))

        def decide(shape):
            shape = dict(shape)
            if shape.pop("family") == "mission_core":
                return eligibility.mission_core(mission(shape.pop("state"), **shape), None)
            return eligibility.workflow(shape)
        for case in table["shared"]:
            decision = decide(case["backend"])
            self.assertEqual((decision.eligible, decision.basis, decision.basis_source),
                             (True, case["basis"], case["backend_source"]), case["case"])
        for case in table["refused"]:
            if case["backend"] is not None:
                decision = decide(case["backend"])
                self.assertEqual((decision.eligible, decision.reason),
                                 (False, eligibility.REASON_NO_POSITIVE_BASIS), case["case"])
        for case in table["asymmetric"]["cases"]:
            decision = decide(case["backend"])
            self.assertEqual((decision.eligible, decision.reason), (False, case["backend_reason"]),
                             case["case"])
        shared = {case["basis"] for case in table["shared"]}
        backend = set(eligibility.MISSION_CORE_BASES) | set(eligibility.WORKFLOW_BASES)
        self.assertEqual(backend, shared, "every backend basis is mirrored case by case")
        source = TS_STATUS.read_text(encoding="utf-8")
        office = set(re.findall(r"\|\s*'([a-z_]+)'", source.split(
            "export type DiCapacityBasis =", 1)[1].split(";", 1)[0]))
        self.assertEqual(office - shared, set(table["asymmetric"]["bases"]),
                         "an office-only basis must be declared asymmetric, with its reason")
        declared = source.split("export const DI_CAPACITY_ASYMMETRIC", 1)[1].split("};", 1)[0]
        self.assertEqual(set(re.findall(r"^\s+([a-z_]+):", declared, re.M)),
                         set(table["asymmetric"]["bases"]), "the office declares the same asymmetry")
        # each shared basis rests on ONE record fact: every case of it names the same evidence
        for basis in shared:
            evidence = {case["evidence"] for case in table["shared"] if case["basis"] == basis}
            self.assertEqual(len(evidence), 1, (basis, evidence))
        stopped = re.search(r"herdrStoppedStatuses: \[([^\]]*)\]", source).group(1)
        self.assertEqual(set(re.findall(r"'([A-Z]+)'", stopped)), {"COMPLETE", "ABORTED", "ERROR"})
        quiescent = re.search(r"cancelQuiescent: \[([^\]]*)\]", source).group(1)
        self.assertEqual(set(re.findall(r"'([a-z_]+)'", quiescent)),
                         set(eligibility.QUIESCENT_CANCELS))

    def test_E11_receipts_that_cannot_be_read_establish_nothing_never_zero_dispatches(self):
        # round 10: an absent or malformed receipts list is NOT "never dispatched"
        from mission_sources import sources as sources_module
        for record in ({"phase": "BLOCKED"}, {"phase": "BLOCKED", "receipts": None},
                       {"phase": "BLOCKED", "receipts": ["not a receipt"]}):
            self.assertIsNone(eligibility.dispatch_count(record), record)
            self.assertIsNone(sources_module.sent_handoff_receipts(record), record)
            decision = eligibility.workflow(record)
            self.assertEqual((decision.eligible, decision.reason),
                             (False, eligibility.REASON_NO_POSITIVE_BASIS), record)

    def test_E10_the_catalog_reader_counts_the_same_receipts_as_the_runtime_and_retention(self):
        from mission_sources import sources as sources_module
        self.assertEqual(sources_module.SENT_HANDOFF_RECEIPT_MARKER,
                         dispatch_module.DISPATCH_RECEIPT_MARKER)
        for receipts in ([], [{"kind": "preparation", "bounded_summary": "dispatched handoff revision"}],
                         [{"kind": "evidence", "bounded_summary": "dispatched handoff revision 1"},
                          {"kind": "evidence", "bounded_summary": "dispatched handoff revision 2"},
                          {"kind": "evidence", "bounded_summary": "verified"}]):
            entry = {"receipts": receipts}
            self.assertEqual(sources_module.sent_handoff_receipts(entry),
                             dispatch_module.dispatch_count(entry))
            self.assertEqual(sources_module.sent_handoff_receipts(entry),
                             eligibility.dispatch_count(entry))

# == reclamation, end to end, on records that already existed ==================================



class SummaryShapeTests(unittest.TestCase):
    """The final-state shapes the Mission Core summary producer really emits, generated THROUGH it
    (``reclaim._mission_core_summary``) and exported for the office's retained-only status reading
    (webview-ui/test/diRetainedArchive.test.ts), so a change of the producer's format breaks both."""

    SUMMARIES = (REPO_ROOT / "integrations" / "pixel-agents" / "webview-ui" / "test" / "fixtures"
                 / "di-retained-summaries.FIXTURE.json")

    def test_SS1_every_producer_shape_is_exported_from_the_producer_itself(self):
        def summary(record, state_record=None):
            return reclaim._mission_core_summary(record, state_record, None)
        verified = {"intent": {"x": 1}, "verification": {"verified": True}}
        shapes = {
            "bare_completed": summary(mission("COMPLETED", run=verified)),
            "bare_rejected": summary(mission("REJECTED")),
            "bare_cancelled": summary(mission("CANCELLED", run={"intent": {"x": 1}, "cancel": {
                "quiescence": "nothing_started", "completed_at": 90}})),
            "withdrawn": summary(mission("AWAITING_DECISION", withdrawal={"withdrawn_at": 5})),
        }
        for progress in mission_state.TERMINAL_PROGRESS_STATES:
            shapes["progress_%s" % progress.lower()] = summary(
                mission("AWAITING_DECISION"), {"progress": progress})
        self.assertEqual(shapes["withdrawn"]["final_state"], "AWAITING_DECISION (withdrawn)")
        self.assertEqual(shapes["progress_completed"]["final_state"],
                         "COMPLETED (Mission State progress; record state AWAITING_DECISION)")
        self.assertEqual(set(shapes), {"bare_completed", "bare_rejected", "bare_cancelled", "withdrawn",
                                       "progress_completed", "progress_closed_unsuccessful",
                                       "progress_abandoned"})
        text = json.dumps({"fixture": True, "generator": "tests/test_mission_retention.py SS1",
                           "label": "FIXTURE (generated, not live): Mission Core retained summaries,"
                                    " produced by mission_retention.reclaim._mission_core_summary for"
                                    " each of its final-state shapes", "summaries": shapes},
                          indent=2, sort_keys=True) + "\n"
        if os.environ.get("DI_EXPORT_PANEL_ANSWERS") == "1":
            self.SUMMARIES.write_text(text, encoding="utf-8")
        self.assertEqual(self.SUMMARIES.read_text(encoding="utf-8"), text)


class FactStateTests(unittest.TestCase):
    """Round 10: not configured, unreadable and confirmed absence are DIFFERENT claims, kept apart from
    the producer to the store, and a version-1 store loads without any of them becoming a confident
    absence."""

    def setUp(self):
        import tempfile
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def store_dir(self, name):
        path = os.path.join(self.tmp.name, name)
        os.mkdir(path, 0o700)
        return path

    def test_FS1_the_ci_producer_says_not_read_unreadable_and_confirmed_absence_apart(self):
        not_read = reclaim._delivery_ci(None, "mn-" + "1" * 32)
        self.assertEqual((not_read["state"], not_read["detail"]), (retention_store.SUMMARY_NOT_READ, None))
        unreadable_dir = self.store_dir("unreadable")
        with open(os.path.join(unreadable_dir, reclaim.DELIVERY_STORE_FILE), "w") as handle:
            handle.write("{not json")
        os.chmod(os.path.join(unreadable_dir, reclaim.DELIVERY_STORE_FILE), 0o600)
        unreadable = reclaim._delivery_ci(unreadable_dir, "mn-" + "1" * 32)
        self.assertEqual(unreadable["state"], retention_store.SUMMARY_UNREADABLE)
        self.assertIn("could not be read", unreadable["source"])
        absent_dir = self.store_dir("absent")                     # configured, nothing ever delivered
        empty_dir = self.store_dir("empty")
        with open(os.path.join(empty_dir, reclaim.DELIVERY_STORE_FILE), "w") as handle:
            json.dump({"pr_delivery_store_schema_version": reclaim.DELIVERY_STORE_SCHEMA_VERSION,
                       "deliveries": {}}, handle)
        os.chmod(os.path.join(empty_dir, reclaim.DELIVERY_STORE_FILE), 0o600)
        # ruling 3: a MISSING file and a MISSING configured directory are both the delivery store's own reading
        # of no deliveries (pr_delivery/store.py load: a path that does not exist is the default document)
        missing_dir = os.path.join(self.tmp.name, "never-created")
        for directory in (absent_dir, empty_dir, missing_dir):
            absent = reclaim._delivery_ci(directory, "mn-" + "1" * 32)
            self.assertEqual(absent["state"], retention_store.SUMMARY_NOT_RECORDED, directory)
        self.assertFalse(os.path.exists(missing_dir), "reading never creates the store")
        # and a store that is there but refused is UNREADABLE, never absence: a file others may read
        loose_dir = self.store_dir("loose")
        with open(os.path.join(loose_dir, reclaim.DELIVERY_STORE_FILE), "w") as handle:
            json.dump({"pr_delivery_store_schema_version": reclaim.DELIVERY_STORE_SCHEMA_VERSION,
                       "deliveries": {}}, handle)
        os.chmod(os.path.join(loose_dir, reclaim.DELIVERY_STORE_FILE), 0o644)
        self.assertEqual(reclaim._delivery_ci(loose_dir, "mn-" + "1" * 32)["state"],
                         retention_store.SUMMARY_UNREADABLE)
        # and only a recorded fact may carry a detail, in the store and in the contract alike
        for state in (retention_store.SUMMARY_NOT_RECORDED, retention_store.SUMMARY_NOT_READ,
                      retention_store.SUMMARY_UNREADABLE, retention_store.SUMMARY_NOT_KNOWN):
            with self.assertRaises(retention_store.RetentionStoreError):
                retention_store._validate_fact({"state": state, "detail": "x", "source": "s"}, "f")

    FACTS = (REPO_ROOT / "integrations" / "pixel-agents" / "webview-ui" / "test" / "fixtures"
             / "di-retained-fact-states.FIXTURE.json")

    def test_FS4_exports_the_real_producers_facts_and_an_upgraded_version_1_summary(self):
        unreadable_dir = self.store_dir("unreadable-delivery")
        with open(os.path.join(unreadable_dir, reclaim.DELIVERY_STORE_FILE), "w") as handle:
            handle.write("{not json")
        os.chmod(os.path.join(unreadable_dir, reclaim.DELIVERY_STORE_FILE), 0o600)
        mission_id = "mn-" + "1" * 32
        facts = {
            "ci_not_read": reclaim._delivery_ci(None, mission_id),
            "ci_unreadable": reclaim._delivery_ci(unreadable_dir, mission_id),
            "ci_confirmed_absent": reclaim._delivery_ci(self.store_dir("no-deliveries"), mission_id),
        }
        legacy_dir = self.store_dir("legacy-store")
        record_id = "mn-" + "c" * 32
        legacy = {"schema_version": 1, "records": {"mission_core:" + record_id: {
            "key": "mission_core:" + record_id, "family": "mission_core", "record_id": record_id,
            "first_eligible_observed_at": 1, "terminal_at": None, "terminal_at_source": None,
            "basis": "verified_target_stopped", "basis_source": 'state = "COMPLETED"',
            "phase": "reclaimed", "phase_at": 2, "retained_for": [], "summary": {
                "final_state": "COMPLETED", "final_state_source": "missions.json missions[x].state",
                "verdict": {"state": "not_recorded_locally", "detail": None,
                            "source": "missions.json missions[x].run.verification"},
                "ci": {"state": "not_recorded_locally", "detail": None,
                       "source": "not read: pr_delivery.json could not be read as the delivery store"},
                "comments": {"state": "not_recorded_locally", "detail": None,
                             "source": "missions.json missions[x] (Mission Core records no comments)"}}}}}
        path = os.path.join(legacy_dir, retention_store.RETENTION_FILE_NAME)
        with open(path, "w") as handle:
            json.dump(legacy, handle)
        os.chmod(path, 0o600)
        upgraded = retention_store.RetentionStore(legacy_dir).load()["records"][
            "mission_core:" + record_id]["summary"]
        self.assertEqual([upgraded[n]["state"] for n in ("verdict", "ci", "comments")],
                         ["not_known", "unreadable", "not_known"])
        text = json.dumps({"fixture": True, "generator": "tests/test_mission_retention.py FS4",
                           "label": "FIXTURE (generated, not live): retained facts as the real"
                                    " producers make them, and a version-1 summary as the store"
                                    " upgrades it", "facts": facts, "upgraded_v1_summary": upgraded},
                          indent=2, sort_keys=True) + "\n"
        if os.environ.get("DI_EXPORT_PANEL_ANSWERS") == "1":
            self.FACTS.write_text(text, encoding="utf-8")
        self.assertEqual(self.FACTS.read_text(encoding="utf-8"), text)

    def test_FS2_the_contract_and_the_store_name_the_same_five_states(self):
        from mission_sources import contract_v2
        self.assertEqual(contract_v2.RETENTION_FACT_STATES, retention_store.SUMMARY_STATES)
        fact = lambda state, detail: {"state": state, "detail": detail, "source": "s"}
        summary = lambda ci: {"final_state": "COMPLETED", "final_state_source": "x",
                              "verdict": fact("recorded", "d"), "ci": ci,
                              "comments": fact("not_recorded_locally", None)}
        for state in retention_store.SUMMARY_STATES:          # every claim, in its own shape, passes
            contract_v2._validate_retention_summary(
                summary(fact(state, "d" if state == "recorded" else None)), "r")
        for bad in (fact("unreadable", "a record may say anything"), fact("recorded", None),
                    fact("absent", None)):                     # and nothing else does
            with self.assertRaises(contract_v2.contract.ContractError, msg=bad):
                contract_v2._validate_retention_summary(summary(bad), "r")

    def test_FS3_a_version_1_store_loads_and_never_turns_an_unestablished_absence_confident(self):
        directory = self.store_dir("legacy")
        fact = lambda state, source, detail=None: {"state": state, "detail": detail, "source": source}
        summary = {
            "final_state": "COMPLETED", "final_state_source": "missions.json missions[x].state",
            "verdict": fact("not_recorded_locally", "missions.json missions[x].run.verification"),
            "ci": fact("not_recorded_locally", "not read: no PR delivery store is configured for this"
                       " process, so its CI results were not consulted"),
            "comments": fact("recorded", "x comments", "a bounded excerpt"),
        }
        other = dict(summary, ci=fact("not_recorded_locally",
                                      "not read: pr_delivery.json could not be read as the delivery store"))
        record = lambda record_id, value: {
            "key": "mission_core:" + record_id, "family": "mission_core", "record_id": record_id,
            "first_eligible_observed_at": 1, "terminal_at": None, "terminal_at_source": None,
            "basis": "verified_target_stopped", "basis_source": 'state = "COMPLETED"',
            "phase": "reclaimed", "phase_at": 2, "summary": value, "retained_for": []}
        a, b = "mn-" + "a" * 32, "mn-" + "b" * 32
        version_1 = {"schema_version": 1, "records": {"mission_core:" + a: record(a, summary),
                                                       "mission_core:" + b: record(b, other)}}
        path = os.path.join(directory, retention_store.RETENTION_FILE_NAME)
        with open(path, "w") as handle:
            json.dump(version_1, handle)
        os.chmod(path, 0o600)
        loaded = retention_store.RetentionStore(directory).load()
        self.assertEqual(loaded["schema_version"], retention_store.SCHEMA_VERSION)
        first = loaded["records"]["mission_core:" + a]["summary"]
        self.assertEqual(first["verdict"]["state"], retention_store.SUMMARY_NOT_KNOWN,
                         "absence was never established by version 1")
        self.assertEqual(first["ci"]["state"], retention_store.SUMMARY_NOT_READ)
        self.assertEqual(first["comments"], summary["comments"], "a recorded fact is kept exactly")
        self.assertEqual(loaded["records"]["mission_core:" + b]["summary"]["ci"]["state"],
                         retention_store.SUMMARY_UNREADABLE)
        # the next save writes version 2, and it loads as such
        store = retention_store.RetentionStore(directory)
        with store.lock():
            store.save(loaded)
        self.assertEqual(json.load(open(path))["schema_version"], retention_store.SCHEMA_VERSION)
        self.assertEqual(store.load()["records"]["mission_core:" + a]["summary"]["verdict"]["state"],
                         retention_store.SUMMARY_NOT_KNOWN)
        # and an unknown version still fails closed
        with open(path, "w") as handle:
            json.dump(dict(version_1, schema_version=7), handle)
        with self.assertRaises(retention_store.RetentionStoreError):
            retention_store.RetentionStore(directory).load()

class ReclamationTests(RetentionFixture):

    def test_RC1_before_the_deadline_nothing_is_reclaimed_and_the_mission_is_observed(self):
        view = self.completed()
        terminal = self.terminal_at(view["mission_id"])
        self.clock.now = terminal + reclaim.RETENTION_SECONDS - 1
        report = self.retention.sweep()
        self.assertEqual((report.observed, report.reclaimed), (1, 0))
        self.assertEqual(self.record(view["mission_id"])["phase"], retention_store.PHASE_OBSERVED)
        self.assert_payload_intact()

    def test_RC2_exactly_at_the_deadline_the_payload_is_reclaimed_and_the_summary_kept(self):
        view = self.completed()
        mission_id = view["mission_id"]
        authority = self.authority()
        terminal = self.terminal_at(mission_id)
        self.clock.now = terminal + reclaim.RETENTION_SECONDS
        report = self.retention.sweep()
        self.assertEqual(report.reclaimed, 1, report)
        self.assert_payload_gone(mission_id)
        self.assertEqual(self.authority(), authority, "ledger, reservations, authorizations")
        record = self.record(mission_id)
        self.assertEqual(record["phase"], retention_store.PHASE_RECLAIMED)
        self.assertEqual(record["terminal_at"], terminal)
        self.assertEqual(record["basis"], "verified_target_stopped")
        summary = record["summary"]
        self.assertEqual(summary["final_state"], "COMPLETED")
        self.assertEqual(summary["verdict"]["state"], "recorded")
        self.assertIn("verified", summary["verdict"]["detail"])
        self.assertEqual(summary["comments"]["state"], "not_recorded_locally")
        self.assertIn("task.json", summary["comments"]["source"])
        # CHANGED IN round 10: the state itself says the delivery store was not consulted (never a
        # confident "not recorded"), and its source says why
        self.assertEqual((summary["ci"]["state"], summary["ci"]["detail"]), ("not_read", None))
        self.assertIn("not consulted", summary["ci"]["source"])

    def test_RC3_well_after_the_deadline_it_is_reclaimed(self):
        view = self.completed()
        self.clock.now = self.terminal_at(view["mission_id"]) + 400 * DAY
        self.assertEqual(self.retention.sweep().reclaimed, 1)
        self.assert_payload_gone(view["mission_id"])

    def test_RC4_a_clock_that_goes_backwards_or_is_invalid_never_reclaims_early(self):
        view = self.completed()
        terminal = self.terminal_at(view["mission_id"])
        self.clock.now = terminal + 10 * DAY
        self.retention.sweep()
        for reading in (terminal - 5 * DAY, 0, terminal + 29 * DAY):
            self.clock.now = reading
            self.assertEqual(self.retention.sweep().reclaimed, 0, reading)
        record = self.record(view["mission_id"])
        self.assertEqual(record["first_eligible_observed_at"], terminal + 10 * DAY,
                         "the first observation is never moved by a later reading")
        self.assertFalse(reclaim.is_due(record, "soon"))
        self.assertFalse(reclaim.is_due(record, -1))
        self.assert_payload_intact()

    def test_RC5_the_running_path_reclaims_without_a_restart_when_the_deadline_passes(self):
        view = self.completed()
        terminal = self.terminal_at(view["mission_id"])
        self.clock.now = terminal + DAY
        self.assertEqual(self.retention.sweep().reclaimed, 0)
        self.assert_payload_intact()
        # the same objects, the same process: only the injected clock moves
        self.clock.now = terminal + reclaim.RETENTION_SECONDS
        self.assertEqual(self.retention.sweep().reclaimed, 1)
        self.assert_payload_gone(view["mission_id"])

    def test_RC6_idempotent_a_second_pass_changes_no_byte(self):
        view = self.completed()
        self.clock.now = self.terminal_at(view["mission_id"]) + reclaim.RETENTION_SECONDS
        self.retention.sweep()
        before = [self.bytes_of(name) for name in (
            "mission_panel_journal.json", "local_requests.json", "missions.json",
            "mission_retention.json")]
        self.clock.now += DAY
        self.assertEqual(self.retention.sweep().reclaimed, 0)
        after = [self.bytes_of(name) for name in (
            "mission_panel_journal.json", "local_requests.json", "missions.json",
            "mission_retention.json")]
        self.assertEqual(before, after)

    def test_RC7_a_pass_that_dies_mid_reduction_leaves_loadable_stores_and_resumes(self):
        view = self.completed()
        mission_id = view["mission_id"]
        self.clock.now = self.terminal_at(mission_id) + reclaim.RETENTION_SECONDS
        with mock.patch.object(store_module, "reclaim_requests",
                               side_effect=store_module.LocalRequestStoreError("disk")):
            report = self.retention.sweep()
        self.assertEqual(report.reclaimed, 0)
        self.assertTrue(report.problems)
        # every store still loads; the record says reclamation is under way, never intact
        journal_module.PanelJournal(self.state).load()
        store_module.LocalRequestStore(self.state).load()
        self.assertEqual(self.record(mission_id)["phase"], retention_store.PHASE_RECLAIMING)
        self.assertIsNotNone(self.record(mission_id)["summary"])
        # the next pass finishes the same Mission
        self.assertEqual(self.retention.sweep().reclaimed, 1)
        self.assertEqual(self.record(mission_id)["phase"], retention_store.PHASE_RECLAIMED)
        self.assert_payload_gone(mission_id)

    def test_RC8_a_reclaimed_conversation_is_refused_a_new_turn_and_a_submission(self):
        view = self.completed()
        self.clock.now = self.terminal_at(view["mission_id"]) + reclaim.RETENTION_SECONDS
        self.retention.sweep()
        turns = journal_module.PanelJournal(self.state).load()["turns"]
        conversation_id = next(iter(turns.values()))["conversation_id"]
        with self.assertRaises(journal_module.JournalError) as caught:
            self.service.send_turn(conversation_id, "one more thing")
        self.assertEqual(caught.exception.problem, journal_module.PROBLEM_CONVERSATION_RECLAIMED)
        turn = next(t for t in turns.values() if t["draft"] is not None)
        with self.assertRaises(journal_module.JournalError) as caught:
            journal_module.PanelJournal(self.state).reserve_submission(
                turn["turn_id"], turn["draft"]["proposal_digest_sha256"], None, self.clock())
        self.assertEqual(caught.exception.problem, journal_module.PROBLEM_CONVERSATION_RECLAIMED)

    def test_RC9_a_reclaimed_mission_reads_as_reclaimed_never_as_empty(self):
        view = self.completed()
        self.clock.now = self.terminal_at(view["mission_id"]) + reclaim.RETENTION_SECONDS
        self.retention.sweep()
        conversation_id = next(iter(journal_module.PanelJournal(self.state).load()["turns"]
                                    .values()))["conversation_id"]
        shown = self.service.conversation_view(conversation_id)["turns"]
        self.assertTrue(shown)
        for turn in shown:
            self.assertEqual(turn["reclaimed"]["outcome"], "reclaimed")
            self.assertIsNone(turn["user_text"])
        request = self.service.request_view(view["request_ref"])
        self.assertEqual(request["reclaimed"]["outcome"], "reclaimed")
        overview = self.service.overview()
        self.assertTrue(all(item["reclaimed"] for item in overview["conversations"]))


class MissionsReductionTests(RetentionFixture):
    """The ONE missions.json reduction: a terminal run's two diagnostic texts, nothing else."""

    def completed_with_a_workspace_refusal(self):
        view = self.of_record()
        self.approve(view)
        self.missions.record_workspace_refusal(
            view["mission_id"], "workspace_not_ready", "the private diagnostic text", self.context)
        self.service.run(view["request_ref"], "dispatch")
        self.observer.raw = raw_observation()
        self.service.run(view["request_ref"], "observe")
        self.finish_team(view["mission_id"])
        return self.service.run(view["request_ref"], "verify")["request"]

    def test_MR1_the_reclaimed_run_validates_replays_exactly_and_round_trips(self):
        from mission import store as mission_store
        view = self.completed_with_a_workspace_refusal()
        mission_id = view["mission_id"]
        before = self.document("missions.json")
        self.assertEqual(before["missions"][mission_id]["state"], "COMPLETED")
        self.assertEqual(before["missions"][mission_id]["run"]["workspace_refusal"]["detail"],
                         "the private diagnostic text")
        self.clock.now = self.terminal_at(mission_id) + reclaim.RETENTION_SECONDS
        self.assertEqual(self.retention.sweep().reclaimed, 1)
        after = self.document("missions.json")
        run = after["missions"][mission_id]["run"]
        # (a) the closed-schema validator accepts the marker, (c) through the real load boundary,
        # which (b) re-runs the full history replay (reconcile_mission_history) on every load
        mission_store.MissionStore(self.state).load()
        self.assertEqual(run["reclaimed"]["outcome"], "reclaimed")
        self.assertIsNone(run["workspace_refusal"]["detail"])
        self.assertNotIn("the private diagnostic text", self.bytes_of("missions.json").decode())
        # every other byte of the Mission, and all authority, is exactly as it was
        for key in ("authority_ledger", "reservations", "authorizations", "mission_state"):
            self.assertEqual(after[key], before[key], key)
        expected = json.loads(json.dumps(before["missions"][mission_id]))
        expected["run"]["workspace_refusal"]["detail"] = None
        expected["run"]["reclaimed"] = run["reclaimed"]
        self.assertEqual(after["missions"][mission_id], expected)

    def test_MR2_the_marker_is_refused_on_a_run_that_can_still_change_and_unknown_keys_still_fail(self):
        from mission import manifest
        from mission import store as mission_store
        view = self.of_record()
        self.approve(view)
        self.service.run(view["request_ref"], "dispatch")
        self.observer.raw = raw_observation()
        self.service.run(view["request_ref"], "observe")
        document = self.document("missions.json")
        mission = document["missions"][view["mission_id"]]
        self.assertEqual(mission["state"], "RUNNING")
        mission["run"] = manifest.reclaimed_run(mission["run"], 1, "reclaimed")
        with self.assertRaises(mission_store.MissionStoreError) as caught:
            mission_store.validate_document(document)
        self.assertIn("valid only on a terminal run", str(caught.exception))
        mission["run"].pop("reclaimed")
        mission["run"]["not_a_key"] = 1
        with self.assertRaises(mission_store.MissionStoreError):
            mission_store.validate_document(document)


class ProgressClosureTests(RetentionFixture):

    def test_PC1_the_production_shaped_abandoned_unstarted_record_is_eligible_and_reclaimed(self):
        view = self.abandoned_unstarted()
        mission_id = view["mission_id"]
        doc = self.document("missions.json")
        self.assertEqual(doc["missions"][mission_id]["state"], "AWAITING_DECISION")
        self.assertEqual(doc["mission_state"][mission_id]["progress"], "ABANDONED")
        decision = eligibility.mission_core(doc["missions"][mission_id],
                                            doc["mission_state"][mission_id])
        self.assertTrue(decision.eligible, decision)
        self.assertEqual(decision.basis, "no_start_recorded")
        self.clock.now = decision.terminal_at + reclaim.RETENTION_SECONDS
        self.assertEqual(self.retention.sweep().reclaimed, 1)
        self.assert_payload_gone(mission_id)
        # Addition 1: the retained summary states the SAME closure the office files it by
        # (Deliverable 1: Stopped, record closed), with the record's own state beside it
        summary = self.record(mission_id)["summary"]
        self.assertEqual(summary["final_state"],
                         "ABANDONED (Mission State progress; record state AWAITING_DECISION)")
        self.assertIn("mission_state[%s].progress" % mission_id, summary["final_state_source"])

    def test_PC2_an_active_contract_at_the_current_revision_refuses_and_a_dispatch_really_follows(self):
        view = self.of_record()
        mission_id = view["mission_id"]
        self.approve(view)
        state = self.missions.get_state(mission_id)
        self.missions.activate_proof_contract(
            mission_id, self.missions.mint_state_operation_id(self.context), state["sequence"],
            self.context)
        state = self.missions.get_state(mission_id)
        self.missions.abandon(mission_id, self.missions.mint_state_operation_id(self.context),
                              state["sequence"], "abandoned while authorized", self.context)
        doc = self.document("missions.json")
        decision = eligibility.mission_core(doc["missions"][mission_id],
                                            doc["mission_state"][mission_id])
        self.assertEqual(decision.reason, eligibility.REASON_DISPATCH_POSSIBLE, decision)
        report = self.retention.sweep()
        self.assertEqual((report.observed, report.reclaimed), (0, 0), "never even observed")
        with self.assertRaises(reclaim.DeleteRefused) as caught:
            self.retention.delete(mission_id, "AUTHORIZED", self.retention.store_identity)
        self.assertEqual(caught.exception.reason, "dispatch_still_possible")
        self.assertIsNone(self.record(mission_id))
        # why it must be refused: the bridge really still dispatches it (the early return skips
        # the activation that terminal progress would refuse)
        self.assertEqual(self.spawn.calls, [])
        self.bridge.dispatch(mission_id)
        self.assertEqual(len(self.spawn.calls), 1)

    def test_PC3_terminal_progress_beside_a_running_team_is_refused(self):
        view = self.of_record()
        mission_id = view["mission_id"]
        self.approve(view)
        self.service.run(view["request_ref"], "dispatch")
        self.observer.raw = raw_observation()
        self.service.run(view["request_ref"], "observe")
        state = self.missions.get_state(mission_id)
        self.missions.abandon(mission_id, self.missions.mint_state_operation_id(self.context),
                              state["sequence"], "descriptive only", self.context)
        with self.assertRaises(reclaim.DeleteRefused) as caught:
            self.retention.delete(mission_id, "RUNNING", self.retention.store_identity)
        self.assertEqual(caught.exception.reason, reclaim.REFUSE_RUNNING)
        self.clock.now += 400 * DAY
        self.assertEqual(self.retention.sweep().reclaimed, 0)
        self.assert_payload_intact()

    def test_PC4_the_archived_record_refuses_execution_server_side(self):
        view = self.abandoned_unstarted()
        with self.assertRaises(service_module.PanelRefusal) as caught:
            self.service.run(view["request_ref"], "dispatch")
        self.assertEqual(caught.exception.problem, service_module.PROBLEM_ACTION_NOT_OFFERED)
        with self.assertRaises(Exception):
            self.bridge.dispatch(view["mission_id"])
        self.assertEqual(self.spawn.calls, [])


class SharedConversationTests(RetentionFixture):
    """A conversation that produced TWO Missions: its words are reasoning for both."""

    def two_in_one_conversation(self, gap):
        first = self.rejected()
        turns = journal_module.PanelJournal(self.state).load()["turns"]
        conversation_id = next(t["conversation_id"] for t in turns.values()
                               if t["submission"] is not None)
        self.clock.now += gap
        turn = self.drafted(objective="Second objective, same conversation",
                            conversation_id=conversation_id)
        second = self.service.submit_draft(
            turn["turn_id"], turn["draft"]["proposal_digest_sha256"])["request"]
        args = self.decide_args(second)
        args.pop("reply")
        second = self.service.reject(second["request_ref"], **args)["request"]
        return first, second, conversation_id

    def request_body(self, request_ref):
        return self.document("local_requests.json")["requests"][request_ref]["proposal"]

    def test_SC1_two_due_eligible_missions_sharing_a_conversation_reclaim_on_one_sweep(self):
        first, second, _ = self.two_in_one_conversation(gap=DAY)
        latest = max(self.terminal_at(first["mission_id"]), self.terminal_at(second["mission_id"]))
        self.clock.now = latest + reclaim.RETENTION_SECONDS
        report = self.retention.sweep()
        self.assertEqual(report.reclaimed, 2, report)
        for view in (first, second):
            record = self.record(view["mission_id"])
            self.assertEqual((record["phase"], record["retained_for"]),
                             (retention_store.PHASE_RECLAIMED, []))
            self.assertIsNone(self.request_body(view["request_ref"]))
        self.assertNotIn(USER_WORDS, self.bytes_of("mission_panel_journal.json").decode("utf-8"))

    def test_SC2_one_due_and_one_not_yet_due_keep_the_conversation_until_both_are_safe(self):
        first, second, _ = self.two_in_one_conversation(gap=10 * DAY)
        self.clock.now = self.terminal_at(first["mission_id"]) + reclaim.RETENTION_SECONDS
        self.assertEqual(self.retention.sweep().reclaimed, 1)
        record = self.record(first["mission_id"])
        self.assertEqual(record["phase"], retention_store.PHASE_RECLAIMED)
        self.assertEqual(record["retained_for"], [second["mission_id"]])
        # the shared words stay for the Mission that still needs them ...
        self.assertIn(USER_WORDS, self.bytes_of("mission_panel_journal.json").decode("utf-8"))
        # ... while the due Mission's OWN payload is reclaimed, and the other's is untouched
        self.assertIsNone(self.request_body(first["request_ref"]))
        self.assertIsNotNone(self.request_body(second["request_ref"]))
        self.assertEqual(self.record(second["mission_id"])["phase"], retention_store.PHASE_OBSERVED)
        # once the second is due, one more pass releases the conversation for both
        self.clock.now = self.terminal_at(second["mission_id"]) + reclaim.RETENTION_SECONDS
        self.retention.sweep()
        self.assertNotIn(USER_WORDS, self.bytes_of("mission_panel_journal.json").decode("utf-8"))
        self.assertEqual(self.record(second["mission_id"])["phase"], retention_store.PHASE_RECLAIMED)
        self.retention.sweep()
        self.assertEqual(self.record(first["mission_id"])["retained_for"], [])

    def test_SC3_owner_delete_of_one_mission_of_a_shared_conversation_is_refused(self):
        first, _, _ = self.two_in_one_conversation(gap=DAY)
        with self.assertRaises(reclaim.DeleteRefused) as caught:
            self.retention.delete(first["mission_id"], "REJECTED", self.retention.store_identity)
        self.assertEqual(caught.exception.reason, reclaim.REFUSE_SHARED)
        self.assert_payload_intact()



class ConversationRaceTests(RetentionFixture):
    """Round 8 finding 1: the linkage check and the reduction are ONE act, atomic inside the
    journal's own write lock. Each test lands a second Mission in the conversation, through the
    production methods (a real turn, a real draft, a real submission into Mission Core), in the
    window between retention reading the journal and retention reducing it."""

    LIVE_WORDS = "the second Mission's live words, still needed"
    LIVE_OBJECTIVE = "A second objective, still awaiting its decision in the same conversation"

    def conversation_of(self, request_ref):
        turns = self.document("mission_panel_journal.json")["turns"]
        return next(t["conversation_id"] for t in turns.values()
                    if t["submission"] is not None and t["submission"]["request_ref"] == request_ref)

    def submit_into(self, conversation_id):
        reply = "```mission-proposal\n%s\n```" % json.dumps(dict(GOOD, objective=self.LIVE_OBJECTIVE))
        turn = self.say(self.LIVE_WORDS, reply, conversation_id)
        return self.service.submit_draft(
            turn["turn_id"], turn["draft"]["proposal_digest_sha256"])["request"]

    def interleave(self, conversation_id):
        """Retention's NEXT journal read takes its snapshot, and only THEN does the second Mission
        land: exactly the window between the check and the reduction."""
        original = self.retention.journal.load
        landed = []

        def load():
            snapshot = original()
            if not landed:
                landed.append(self.submit_into(conversation_id))
            return snapshot
        return mock.patch.object(self.retention.journal, "load", side_effect=load), landed

    def request_body(self, request_ref):
        return self.document("local_requests.json")["requests"][request_ref]["proposal"]

    def assert_live_intact(self):
        journal = self.bytes_of("mission_panel_journal.json").decode("utf-8")
        self.assertIn(self.LIVE_WORDS, journal, "the live Mission's turn text")
        self.assertIn(self.LIVE_OBJECTIVE, journal, "the live Mission's draft body")
        self.assertIn(self.LIVE_OBJECTIVE, self.bytes_of("local_requests.json").decode("utf-8"))

    def test_RA0_the_interleaving_is_real_without_the_snapshot_check_the_live_words_would_go(self):
        # Control: with the judged snapshot made meaningless, the SAME interleaving reaches the
        # reduction and erases the live Mission's words. So RA1 and RA2 exercise the race itself.
        first = self.rejected()
        self.clock.now = self.terminal_at(first["mission_id"]) + reclaim.RETENTION_SECONDS
        patcher, landed = self.interleave(self.conversation_of(first["request_ref"]))
        with patcher, mock.patch.object(journal_module, "conversation_snapshot",
                                        return_value="unchecked"):
            self.retention.sweep()
        self.assertEqual(len(landed), 1)
        self.assertNotIn(self.LIVE_WORDS,
                         self.bytes_of("mission_panel_journal.json").decode("utf-8"))

    def test_RA1_a_submission_landing_between_the_check_and_the_reduction_is_never_reclaimed(self):
        first = self.rejected()
        self.clock.now = self.terminal_at(first["mission_id"]) + reclaim.RETENTION_SECONDS
        patcher, landed = self.interleave(self.conversation_of(first["request_ref"]))
        with patcher:
            report = self.retention.sweep()
        self.assertEqual(len(landed), 1)
        second = landed[0]
        self.assertEqual(report.reclaimed, 0, report)
        self.assertIn((first["mission_id"], journal_module.PROBLEM_CONVERSATION_CHANGED),
                      report.problems)
        self.assert_live_intact()
        # nothing of the first Mission was reduced either, and it is not recorded as reclaimed
        self.assertIsNotNone(self.request_body(first["request_ref"]))
        self.assertEqual(self.record(first["mission_id"])["phase"], retention_store.PHASE_RECLAIMING)
        # the next pass judges the CURRENT journal: the conversation is kept for the live Mission,
        # and only the first Mission's own payload goes
        report = self.retention.sweep()
        self.assertEqual(report.reclaimed, 1, report)
        record = self.record(first["mission_id"])
        self.assertEqual((record["phase"], record["retained_for"]),
                         (retention_store.PHASE_RECLAIMED, [second["mission_id"]]))
        self.assertIsNone(self.request_body(first["request_ref"]))
        self.assertIsNotNone(self.request_body(second["request_ref"]))
        self.assert_live_intact()

    def test_RA2_an_interleaved_owner_delete_is_refused_with_nothing_durable_and_stays_refused(self):
        first = self.rejected()
        before = self.retention_records()
        patcher, landed = self.interleave(self.conversation_of(first["request_ref"]))
        with patcher, self.assertRaises(reclaim.DeleteRefused) as caught:
            self.retention.delete(first["mission_id"], "REJECTED", self.retention.store_identity)
        self.assertEqual(caught.exception.reason, reclaim.REFUSE_CHANGED)
        self.assert_live_intact()
        self.assertIsNotNone(self.request_body(first["request_ref"]), "nothing reduced")
        # refused means refused: no deletion recorded or queued, the coordinator store as it was
        self.assertIsNone(self.record(first["mission_id"]))
        self.assertEqual(self.retention_records(), before)
        # a restarted process's pass does what the caller was told: it deletes nothing
        restarted = reclaim.MissionCoreRetention(self.state, self.clock, self.guard)
        restarted.sweep()
        record = self.record(first["mission_id"])
        self.assertEqual(record["phase"], retention_store.PHASE_OBSERVED)
        self.assertIsNotNone(self.request_body(first["request_ref"]))
        self.assert_live_intact()
        # and asked again, the owner is told the truth about the conversation now: it is shared
        with self.assertRaises(reclaim.DeleteRefused) as again:
            self.retention.delete(first["mission_id"], "REJECTED", self.retention.store_identity)
        self.assertEqual(again.exception.reason, reclaim.REFUSE_SHARED)
        self.assertIn(landed[0]["mission_id"], again.exception.detail)

    def test_RA3_the_journal_refuses_a_reduction_whose_snapshot_moved_and_writes_nothing(self):
        first = self.rejected()
        conversation_id = self.conversation_of(first["request_ref"])
        journal = journal_module.PanelJournal(self.state)
        judged = journal_module.conversation_snapshot(journal.load(), conversation_id)
        self.submit_into(conversation_id)
        before = self.bytes_of("mission_panel_journal.json")
        with self.assertRaises(journal_module.JournalError) as caught:
            journal.reclaim_conversations([conversation_id], self.clock.now,
                                          reclaim.OUTCOME_RECLAIMED, {conversation_id: judged})
        self.assertEqual(caught.exception.problem, journal_module.PROBLEM_CONVERSATION_CHANGED)
        self.assertEqual(self.bytes_of("mission_panel_journal.json"), before, "nothing written")
        # and the judged set must name exactly the conversations reduced
        with self.assertRaises(ValueError):
            journal.reclaim_conversations([conversation_id], self.clock.now,
                                          reclaim.OUTCOME_RECLAIMED, {})

class ReviewerSummaryTests(unittest.TestCase):

    def test_RV1_only_the_canonical_decision_and_a_bounded_excerpt_are_kept(self):
        text = ("# reviewer evidence\n" + "long raw log line\n" * 4000
                + "HERD_DECISION: REJECT\n1. fix the race in store.py\n" + "x" * 5000)
        decision, comments = reclaim.reviewer_summary(text)
        self.assertEqual(decision, "REJECT")
        self.assertTrue(comments.startswith("1. fix the race in store.py"))
        self.assertLessEqual(len(comments), reclaim.MAX_COMMENT_CHARS)
        self.assertNotIn("long raw log line", comments)

    def test_RV2_a_non_canonical_word_is_never_a_verdict(self):
        for text in ("LGTM, approved", "HERD_DECISION: APPROVED", "decision: APPROVE", None, ""):
            self.assertEqual(reclaim.reviewer_summary(text), (None, None), text)


class DeliveryReadPinTests(unittest.TestCase):

    def test_DP1_the_re_declared_delivery_store_constants_equal_their_owner(self):
        from pr_delivery import store as delivery_store
        from target_runtime import mission_state as state_reader
        self.assertEqual(reclaim.DELIVERY_STORE_FILE, delivery_store.STORE_FILE_NAME)
        self.assertEqual(reclaim.DELIVERY_STORE_SCHEMA_VERSION, delivery_store.STORE_SCHEMA_VERSION)
        self.assertEqual(reclaim.MAX_DELIVERY_STORE_BYTES, state_reader.MAX_DELIVERY_STORE_BYTES)
        self.assertEqual(reclaim.MAX_DELIVERY_RECORDS, delivery_store.MAX_PR_DELIVERY_RECORDS)
        self.assertEqual(reclaim.MAX_DELIVERY_RECORDS, state_reader.MAX_DELIVERY_RECORDS)
        # and retention never reaches the delivery package itself
        self.assertNotIn("pr_delivery", (REPO_ROOT / "mission_retention" / "reclaim.py").read_text()
                         .split('"""', 2)[2].split("\nimport ", 1)[0] + "".join(
            line for line in (REPO_ROOT / "mission_retention" / "reclaim.py").read_text()
            .splitlines() if line.startswith(("import ", "from "))))


class WorkflowRetentionTests(RetentionFixture):

    def setUp(self):
        super(WorkflowRetentionTests, self).setUp()
        from test_workflow_authority import make_record
        from workflow_authority import record as workflow_record
        from workflow_authority import store as workflow_store
        self.make_record = make_record
        self.wrecord = workflow_record
        self.wstore_module = workflow_store
        self.wdir = os.path.join(self.tmp.name, "workflow-store")
        self.wstore = workflow_store.WorkflowStore(self.wdir)
        done = make_record("wf-done", human_intent="the private human intent")
        done["phase"] = workflow_record.PHASE_COMPLETED
        never = make_record("wf-never", human_intent="blocked before any dispatch")
        never["phase"] = workflow_record.PHASE_BLOCKED
        dispatched = make_record("wf-dispatched", human_intent="blocked after a dispatch")
        dispatched["phase"] = workflow_record.PHASE_BLOCKED
        dispatched["receipts"] = [dispatch_module.dispatch_receipt(
            dispatched, now=150, sequence_number=1,
            turn_id_factory=lambda: "disp-0000000000000001")]
        document = workflow_store.default_document()
        for entry in (done, never, dispatched):
            ok, problem, _ = workflow_store.add_workflow(document, entry)
            self.assertTrue(ok, problem)
        with workflow_store.exclusive_store_lock(self.wdir):
            self.wstore.save(document)
        # preserved evidence through its REAL writer, from a real lease directory
        from target_runtime import evidence_preservation
        lease = os.path.join(self.tmp.name, "lease")
        state = os.path.join(lease, *evidence_preservation.STATE_SUBDIRS)
        os.makedirs(state)
        contents = {name: "RAW %s live reasoning\n" % name * 50
                    for name in evidence_preservation.REQUIRED_ARTIFACTS}
        contents[reclaim.REVIEWER_EVIDENCE] += ("HERD_DECISION: APPROVE\nInspected the store"
                                                " and the tests; no blocker remains.\n")
        for name, text in contents.items():
            with open(os.path.join(state, name), "w") as handle:
                handle.write(text)
        ok, problem, detail, _ = evidence_preservation.preserve(
            done, lease, self.wdir, 160, "ws-1",
            required_names=evidence_preservation.REQUIRED_ARTIFACTS)
        self.assertTrue(ok, (problem, detail))
        self.workflow_retention = reclaim.WorkflowRetention(self.wdir, self.clock)

    def wbytes(self, *parts):
        with open(os.path.join(self.wdir, *parts), "rb") as handle:
            return handle.read().decode("utf-8")

    def test_WF1_terminal_workflows_with_a_basis_are_removed_and_summarised_after_30_days(self):
        self.clock.now = 1000
        self.assertEqual(self.workflow_retention.sweep().observed, 2)
        self.clock.now = 1000 + reclaim.RETENTION_SECONDS - 1
        self.assertEqual(self.workflow_retention.sweep().reclaimed, 0)
        self.clock.now = 1000 + reclaim.RETENTION_SECONDS
        self.assertEqual(self.workflow_retention.sweep().reclaimed, 2)
        workflows = self.wbytes("workflows.json")
        self.assertNotIn("the private human intent", workflows)
        self.assertNotIn("blocked before any dispatch", workflows)
        self.assertIn("blocked after a dispatch", workflows, "no basis: kept intact")
        self.wstore.load()                                   # still loads through its boundary
        preserved = json.loads(self.wbytes("preserved-evidence", "wf-done.json"))
        self.assertTrue(all(entry["content"] is None for entry in preserved["files"]))
        self.assertTrue(all(entry["digest"] for entry in preserved["files"]))
        self.assertNotIn("live reasoning", self.wbytes("preserved-evidence", "wf-done.json"))
        records = retention_store.RetentionStore(self.wdir).load()["records"]
        summary = records["workflow_store:wf-done"]["summary"]
        self.assertIn("HERD_DECISION: APPROVE", summary["verdict"]["detail"])
        self.assertIn("no blocker remains", summary["comments"]["detail"])
        self.assertLessEqual(len(summary["comments"]["detail"]), reclaim.MAX_COMMENT_CHARS)
        self.assertNotIn("live reasoning", json.dumps(summary))
        self.assertEqual(records["workflow_store:wf-never"]["basis"], "workflow_not_dispatched")
        self.assertNotIn("workflow_store:wf-dispatched", records)

    def test_WF2_a_removed_workflow_cannot_be_replayed_or_resurrected(self):
        from telegram_operator import mission as telegram_mission
        before = self.wstore.load()
        # even BEFORE removal, a terminal record's approval cannot be consumed (it needs PLANNED)
        self.assertFalse(telegram_mission.consume_mission(
            before, "wf-done", self.wrecord.DECISION_APPROVE, 99, 2000))
        self.clock.now = 1000
        self.workflow_retention.sweep()
        self.clock.now = 1000 + reclaim.RETENTION_SECONDS
        self.workflow_retention.sweep()
        after = self.wstore.load()
        self.assertNotIn("wf-done", after["workflows"])
        # every approval callback resolves the record by id, and refuses an absent one
        entry, problem = telegram_mission.evaluate_mission_callback(
            after, "wf-done", 1001, 1001, None, 1, 2000)
        self.assertEqual((entry, problem), (None, telegram_mission.PROBLEM_UNKNOWN_WORKFLOW))
        self.assertFalse(telegram_mission.consume_mission(
            after, "wf-done", self.wrecord.DECISION_APPROVE, 99, 2000))
        # the only two writers that add a record mint a fresh random 96-bit id; none takes one in
        import inspect
        from grok_bot import conforming
        self.assertIn("secrets.token_hex(12)",
                      inspect.getsource(telegram_mission._default_workflow_id_factory))
        self.assertIn("secrets.token_hex(12)", inspect.getsource(conforming.new_workflow_id))
        fresh = {telegram_mission._default_workflow_id_factory() for _ in range(64)}
        self.assertEqual(len(fresh), 64)
        self.assertNotIn("wf-done", fresh)
        # and the migration only retires: it never writes a record into the store
        migrate_source = (REPO_ROOT / "workflow_authority" / "migrate.py").read_text()
        self.assertNotIn("add_workflow(", migrate_source)
        self.assertNotRegex(migrate_source, r"\[\"workflows\"\]\[[^\]]+\]\s*=")
        # the tombstone that names it stays in the retention store
        records = retention_store.RetentionStore(self.wdir).load()["records"]
        self.assertEqual(records["workflow_store:wf-done"]["phase"], retention_store.PHASE_RECLAIMED)


    # -- round 8 finding 3: unreadable is never absent, and an unproven reduction is never reclaimed

    PRIVATE = "PRIVATE raw log text that must never be certified deleted while it is on disk"

    def preserved_file(self, workflow_id):
        return os.path.join(self.wdir, "preserved-evidence", "%s.json" % workflow_id)

    def corrupt(self, workflow_id):
        """A preserved document that EXISTS and is malformed, holding private raw log text."""
        path = self.preserved_file(workflow_id)
        with open(path, "w") as handle:
            handle.write('{"files": [{"name": "transcript", "content": "%s"' % self.PRIVATE)
        os.chmod(path, 0o600)

    def repair(self, workflow_id):
        path = self.preserved_file(workflow_id)
        with open(path, "w") as handle:
            json.dump({"files": [{"name": "transcript", "digest": "d" * 64,
                                  "content": self.PRIVATE}]}, handle)
        os.chmod(path, 0o600)

    def wrecords(self):
        return retention_store.RetentionStore(self.wdir).load()["records"]

    def test_WF3_a_malformed_preserved_document_is_reported_never_recorded_as_reclaimed(self):
        self.corrupt("wf-done")
        self.clock.now = 1000
        report = self.workflow_retention.sweep()
        self.assertIn(("wf-done", reclaim.PROBLEM_PRESERVED_UNREADABLE), report.problems)
        self.assertIsNone(self.wrecords()["workflow_store:wf-done"]["summary"],
                          "no verdict is said 'not recorded' over a document that is there")
        self.clock.now = 1000 + reclaim.RETENTION_SECONDS
        report = self.workflow_retention.sweep()
        self.assertIn(("wf-done", reclaim.PROBLEM_PRESERVED_UNREADABLE), report.problems)
        # NOT reclaimed, and nothing pretends otherwise: the record is still in its store, the
        # retention record is not reclaimed, and the private text is still (honestly) on disk
        self.assertNotEqual(self.wrecords()["workflow_store:wf-done"]["phase"],
                            retention_store.PHASE_RECLAIMED)
        self.assertIn("wf-done", self.wstore.load()["workflows"])
        self.assertIn(self.PRIVATE, self.wbytes("preserved-evidence", "wf-done.json"))
        # the workflow with NO preserved evidence is still reclaimed (absent is absent)
        self.assertEqual(self.wrecords()["workflow_store:wf-never"]["phase"],
                         retention_store.PHASE_RECLAIMED)
        # once the document reads as one, the next pass reclaims it, PROVEN
        self.repair("wf-done")
        report = self.workflow_retention.sweep()
        self.assertEqual(report.problems, [])
        self.assertEqual(self.wrecords()["workflow_store:wf-done"]["phase"],
                         retention_store.PHASE_RECLAIMED)
        self.assertNotIn(self.PRIVATE, self.wbytes("preserved-evidence", "wf-done.json"))
        self.assertNotIn("wf-done", self.wstore.load()["workflows"])

    def test_WF4_corruption_between_the_summary_and_the_reduction_leaves_it_reclaiming_and_reported(self):
        self.clock.now = 1000
        self.workflow_retention.sweep()
        self.clock.now = 1000 + reclaim.RETENTION_SECONDS
        original = self.workflow_retention._summary

        def summary_then_corrupt(record):
            captured = original(record)
            if record["workflow_id"] == "wf-done":
                self.corrupt("wf-done")
            return captured
        with mock.patch.object(self.workflow_retention, "_summary", side_effect=summary_then_corrupt):
            report = self.workflow_retention.sweep()
        self.assertIn(("wf-done", reclaim.PROBLEM_PRESERVED_UNREADABLE), report.problems)
        record = self.wrecords()["workflow_store:wf-done"]
        self.assertEqual(record["phase"], retention_store.PHASE_RECLAIMING, "retry pending")
        self.assertIn("wf-done", self.wstore.load()["workflows"], "the record is not removed first")
        self.assertIn(self.PRIVATE, self.wbytes("preserved-evidence", "wf-done.json"))
        # a restarted process's pass still refuses to certify it, and says so again
        again = reclaim.WorkflowRetention(self.wdir, self.clock).sweep()
        self.assertIn(("wf-done", reclaim.PROBLEM_PRESERVED_UNREADABLE), again.problems)
        self.assertEqual(self.wrecords()["workflow_store:wf-done"]["phase"],
                         retention_store.PHASE_RECLAIMING)

    def test_WF5_a_dangling_link_at_the_preserved_path_is_unreadable_never_absent(self):
        path = self.preserved_file("wf-done")
        os.remove(path)
        os.symlink(os.path.join(self.tmp.name, "nowhere.json"), path)
        self.assertFalse(os.path.exists(path), "os.path.exists calls this absent")
        with self.assertRaises(reclaim.RetentionDeferred) as caught:
            reclaim.read_preserved(self.wdir, "wf-done")
        self.assertEqual(caught.exception.problem, reclaim.PROBLEM_PRESERVED_UNREADABLE)
        self.assertIsNone(reclaim.read_preserved(self.wdir, "wf-never"), "absent is absent")
        self.clock.now = 1000
        self.workflow_retention.sweep()
        self.clock.now = 1000 + reclaim.RETENTION_SECONDS
        report = self.workflow_retention.sweep()
        self.assertIn(("wf-done", reclaim.PROBLEM_PRESERVED_UNREADABLE), report.problems)
        self.assertNotEqual(self.wrecords()["workflow_store:wf-done"]["phase"],
                            retention_store.PHASE_RECLAIMED)

    def test_WF6_a_reduction_that_does_not_read_back_reduced_is_never_recorded_as_reclaimed(self):
        self.clock.now = 1000
        self.workflow_retention.sweep()
        self.clock.now = 1000 + reclaim.RETENTION_SECONDS
        # a write that reports success and changed nothing (a lost write): the proof catches it
        with mock.patch.object(reclaim.workflow_store_module, "atomic_write_json",
                               side_effect=lambda *args, **kwargs: None):
            report = self.workflow_retention.sweep()
        self.assertIn(("wf-done", reclaim.PROBLEM_PRESERVED_NOT_REDUCED), report.problems)
        self.assertEqual(self.wrecords()["workflow_store:wf-done"]["phase"],
                         retention_store.PHASE_RECLAIMING)
        self.assertIn("live reasoning", self.wbytes("preserved-evidence", "wf-done.json"))
        self.assertIn("wf-done", self.wstore.load()["workflows"])

    def test_WF7_failure_then_prune_then_repair_recovers_the_summary_retried_and_reported(self):
        # Round 9 finding 2, end to end on the real path: the first observation cannot read the
        # evidence (a record with NO summary), the store's real cap then prunes the record, and the
        # hand-off must keep its ending until the summary is durably captured.
        self.corrupt("wf-done")
        self.clock.now = 1000
        report = self.workflow_retention.sweep()
        self.assertIn(("wf-done", reclaim.PROBLEM_PRESERVED_UNREADABLE), report.problems)
        self.assertIsNone(self.wrecords()["workflow_store:wf-done"]["summary"])
        # the real prune: fill the store with Missions still awaiting approval, then two adds in ONE
        # locked transaction evict the two oldest terminal records (wf-dispatched, then wf-done)
        with self.wstore_module.exclusive_store_lock(self.wdir):
            document = self.wstore.load()
            for index in range(self.wstore_module.MAX_WORKFLOW_RECORDS - len(document["workflows"])):
                ok, problem, _ = self.wstore_module.add_workflow(
                    document, self.make_record("wf-wait-%02d" % index))
                self.assertTrue(ok, problem)
            for name in ("wf-new-1", "wf-new-2"):
                ok, problem, pruned = self.wstore_module.add_workflow(document, self.make_record(name))
                self.assertEqual((ok, pruned), (True, 1), problem)
            self.wstore.save(document)
        self.assertNotIn("wf-done", self.wstore.load()["workflows"])
        self.assertIn("wf-done", self.wstore_module.load_removals(self.wdir)["removed"])
        # while the evidence stays unreadable: retried EVERY pass, said every pass, the ending kept
        reads = []
        original = reclaim.read_preserved

        def counted(directory, workflow_id):
            reads.append(workflow_id)
            return original(directory, workflow_id)
        for _ in range(2):
            with mock.patch.object(reclaim, "read_preserved", side_effect=counted):
                report = self.workflow_retention.sweep()
            self.assertIn(("wf-done", reclaim.PROBLEM_PRESERVED_UNREADABLE), report.problems)
            self.assertIn("wf-done", self.wstore_module.load_removals(self.wdir)["removed"],
                          "the ending is kept until the summary is captured")
            self.assertIsNone(self.wrecords()["workflow_store:wf-done"]["summary"])
        self.assertGreaterEqual(reads.count("wf-done"), 2, "an evidence read is attempted each pass")
        # repaired: the next pass captures the summary, and only then releases the ending
        self.repair("wf-done")
        report = self.workflow_retention.sweep()
        self.assertNotIn("wf-done", [workflow_id for workflow_id, _ in report.problems])
        summary = self.wrecords()["workflow_store:wf-done"]["summary"]
        self.assertIsNotNone(summary)
        self.assertEqual(summary["final_state"], "COMPLETED")
        self.assertIn("cap pruned the record", summary["final_state_source"])
        self.assertNotIn("wf-done", self.wstore_module.load_removals(self.wdir)["removed"])
        # and at its deadline it is reclaimed, proven, its summary kept
        self.clock.now = 1000 + reclaim.RETENTION_SECONDS
        self.workflow_retention.sweep()
        record = self.wrecords()["workflow_store:wf-done"]
        self.assertEqual(record["phase"], retention_store.PHASE_RECLAIMED)
        self.assertIsNotNone(record["summary"])
        self.assertNotIn(self.PRIVATE, self.wbytes("preserved-evidence", "wf-done.json"))

    def test_WF8_a_pruned_record_with_no_summary_and_no_ending_is_reported_never_silently_deferred(self):
        self.corrupt("wf-done")
        self.clock.now = 1000
        self.workflow_retention.sweep()
        # its ending lost by some other path (not the cap's hand-off): it is said every pass
        with self.wstore_module.exclusive_store_lock(self.wdir):
            document = self.wstore.load()
            del document["workflows"]["wf-done"]
            self.wstore.save(document)
        self.clock.now = 1000 + reclaim.RETENTION_SECONDS
        report = self.workflow_retention.sweep()
        self.assertIn(("wf-done", reclaim.PROBLEM_SUMMARY_NOT_CAPTURED), report.problems)
        self.assertEqual(self.wrecords()["workflow_store:wf-done"]["phase"],
                         retention_store.PHASE_OBSERVED)

    # -- archive counts (round 9): ``awaiting`` is disjoint from ``total``, owed and absent only

    def prune_two(self, fail_workflows_write=False):
        """The store's REAL cap: fill it with Missions awaiting approval, then two adds in ONE locked
        transaction evict wf-dispatched (no positive basis) and wf-done (owed a summary). With
        ``fail_workflows_write``, the hand-off is written and the workflows.json replacement fails."""
        original = self.wstore_module.atomic_write_json

        def write(directory, path, document, **kwargs):
            if fail_workflows_write and path.endswith(self.wstore_module.WORKFLOWS_FILE_NAME):
                raise OSError("disk full")
            return original(directory, path, document, **kwargs)
        with self.wstore_module.exclusive_store_lock(self.wdir):
            document = self.wstore.load()
            for index in range(self.wstore_module.MAX_WORKFLOW_RECORDS - len(document["workflows"])):
                self.wstore_module.add_workflow(document, self.make_record("wf-wait-%02d" % index))
            for name in ("wf-new-1", "wf-new-2"):
                ok, problem, pruned = self.wstore_module.add_workflow(document, self.make_record(name))
                self.assertEqual((ok, pruned), (True, 1), problem)
            with mock.patch.object(self.wstore_module, "atomic_write_json", side_effect=write):
                self.wstore.save(document)

    def archive(self):
        from mission_retention import view as retention_view
        return retention_view.RetentionReader().retained("workflow_store", "wf", self.wdir)

    def test_AC1_a_handoff_whose_store_replacement_failed_owes_nothing_and_recovery_still_works(self):
        with self.assertRaises(OSError):
            self.prune_two(fail_workflows_write=True)
        self.assertIn("wf-done", self.wstore.load()["workflows"], "the record is still present")
        self.assertIn("wf-done", self.wstore_module.load_removals(self.wdir)["removed"],
                      "the hand-off was written first")
        reading = self.archive()
        self.assertEqual((reading["state"], reading["total"], reading["awaiting"]), ("observed", 0, 0),
                         "a record still in its store is never counted as removed")
        # recovery: the next pass adopts nothing owed (the record is present) and observes it live
        self.clock.now = 1000
        self.workflow_retention.sweep()
        self.assertNotIn("wf-done", self.wstore_module.load_removals(self.wdir)["removed"])
        self.assertIsNotNone(self.wrecords()["workflow_store:wf-done"]["summary"])
        # and a later prune that succeeds is counted once, in total, captured
        self.prune_two()
        reading = self.archive()
        self.assertEqual((reading["total"], reading["awaiting"]), (1, 0))
        self.assertEqual([m["native_id"] for m in reading["missions"]], ["wf-done"])

    def test_AC2_an_ending_already_represented_by_a_retention_record_is_counted_once(self):
        self.corrupt("wf-done")
        self.clock.now = 1000
        self.workflow_retention.sweep()                 # a record with NO summary (unreadable evidence)
        self.prune_two()
        self.assertIn("wf-done", self.wstore_module.load_removals(self.wdir)["removed"])
        reading = self.archive()
        self.assertEqual((reading["total"], reading["awaiting"]), (1, 0), "in total, not again in awaiting")
        (listed,) = reading["missions"]
        self.assertEqual((listed["native_id"], listed["phase"], listed["summary"]),
                         ("wf-done", retention_store.PHASE_OBSERVED, None))

    def test_AC3_an_absent_ending_with_no_retention_record_is_counted_as_awaiting_and_only_if_owed(self):
        self.prune_two()                                # before ANY sweep: nothing observed them
        removed = self.wstore_module.load_removals(self.wdir)["removed"]
        self.assertEqual(sorted(removed), ["wf-dispatched", "wf-done"])
        reading = self.archive()
        self.assertEqual((reading["total"], reading["awaiting"]), (0, 1),
                         "wf-done is owed a summary; wf-dispatched (no positive basis) is not")
        # the next pass captures it: then it is in total, and no longer awaiting
        self.clock.now = 1000
        self.workflow_retention.sweep()
        reading = self.archive()
        self.assertEqual((reading["total"], reading["awaiting"]), (1, 0))

    OBSERVED_ARCHIVE = (REPO_ROOT / "integrations" / "pixel-agents" / "server" / "__tests__"
                        / "fixtures" / "di-backend" / "retention-archive-observed.FIXTURE.json")

    def test_AC5_exports_a_real_observed_archive_reading_with_its_summaries(self):
        # two workflows reclaimed whole (their records gone), each summary as retention captured it:
        # a reviewer's canonical decision and a bounded comment excerpt, and a never-dispatched one
        self.clock.now = 1000
        self.workflow_retention.sweep()
        self.clock.now = 1000 + reclaim.RETENTION_SECONDS
        self.workflow_retention.sweep()
        reading = self.archive()
        self.assertEqual((reading["state"], reading["total"], reading["awaiting"]), ("observed", 2, 0))
        done = next(m for m in reading["missions"] if m["native_id"] == "wf-done")
        self.assertIn("HERD_DECISION: APPROVE", done["summary"]["verdict"]["detail"])
        self.assertEqual(done["summary"]["comments"]["state"], "recorded")
        text = json.dumps({"fixture": True, "generator": "tests/test_mission_retention.py AC5",
                           "label": "FIXTURE (generated, not live): the real retention reader's archive"
                                    " reading of a workflow store whose two terminal records were"
                                    " reclaimed whole", "reading": reading},
                          indent=2, sort_keys=True) + "\n"
        if os.environ.get("DI_EXPORT_PANEL_ANSWERS") == "1":
            self.OBSERVED_ARCHIVE.write_text(text, encoding="utf-8")
        self.assertEqual(self.OBSERVED_ARCHIVE.read_text(encoding="utf-8"), text)

    def test_AC4_an_unreadable_handoff_reads_unavailable_never_a_confident_zero(self):
        path = os.path.join(self.wdir, self.wstore_module.WORKFLOW_REMOVALS_FILE_NAME)
        with open(path, "w") as handle:
            handle.write("{not json")
        os.chmod(path, 0o600)
        reading = self.archive()
        self.assertEqual((reading["state"], reading["total"], reading["awaiting"], reading["missions"]),
                         ("unavailable", None, None, []))
        self.assertTrue(reading["problem"])

class WorkflowPruneTests(RetentionFixture):
    """Addition 2: the workflow store's OWN cap can prune a terminal record before its deadline."""

    def test_WP1_a_prune_before_the_deadline_leaves_the_captured_summary_and_an_honest_status(self):
        from test_workflow_authority import make_record
        from workflow_authority import record as workflow_record
        from workflow_authority import store as workflow_store
        directory = os.path.join(self.tmp.name, "capped-store")
        wstore = workflow_store.WorkflowStore(directory)
        done = make_record("wf-a-done", human_intent="a private intent the cap will evict")
        done["phase"] = workflow_record.PHASE_COMPLETED
        done["verified_result"] = None
        document = workflow_store.default_document()
        workflow_store.add_workflow(document, done)
        for index in range(workflow_store.MAX_WORKFLOW_RECORDS - 1):
            ok, problem, _ = workflow_store.add_workflow(
                document, make_record("wf-z-active-%04d" % index))
            self.assertTrue(ok, problem)
        with workflow_store.exclusive_store_lock(directory):
            wstore.save(document)
        retention = reclaim.WorkflowRetention(directory, self.clock)
        self.clock.now = 1000
        self.assertEqual(retention.sweep().observed, 1)
        records = retention_store.RetentionStore(directory).load()["records"]
        captured = records["workflow_store:wf-a-done"]["summary"]
        self.assertEqual(captured["final_state"], "COMPLETED", "captured at first observation")
        # the store's REAL prune fires on the next add, well before the deadline
        with workflow_store.exclusive_store_lock(directory):
            document = wstore.load()
            ok, problem, pruned = workflow_store.add_workflow(document, make_record("wf-new"))
            self.assertTrue(ok, problem)
            self.assertEqual(pruned, 1)
            wstore.save(document)
        self.assertNotIn("wf-a-done", wstore.load()["workflows"])
        self.clock.now = 1000 + 5 * DAY
        retention.sweep()
        record = retention_store.RetentionStore(directory).load()["records"]["workflow_store:wf-a-done"]
        self.assertEqual(record["phase"], retention_store.PHASE_OBSERVED, "not due: not reclaimed")
        self.assertEqual(record["summary"]["final_state"], "COMPLETED", "the summary survives")
        # at the deadline it is finished honestly: reclaimed, the captured summary, and why
        self.clock.now = 1000 + reclaim.RETENTION_SECONDS
        self.assertEqual(retention.sweep().reclaimed, 1)
        record = retention_store.RetentionStore(directory).load()["records"]["workflow_store:wf-a-done"]
        self.assertEqual(record["phase"], retention_store.PHASE_RECLAIMED)
        self.assertEqual(record["summary"]["final_state"], "COMPLETED")
        self.assertIn("cap pruned the record before its retention deadline",
                      record["summary"]["final_state_source"])
        self.assertIn(record["summary"]["verdict"]["state"], retention_store.SUMMARY_STATES)


    def test_WP2_the_real_telegram_transaction_that_supersedes_and_prunes_at_once_keeps_the_summary(self):
        # Round 8 finding 2, on the REAL single-transaction path: the Telegram adapter's own
        # _offer_mission, under ONE workflow-store lock, supersedes the chat's PLANNED Mission
        # (-> BLOCKED, never dispatched) and adds the new one, and the store's cap prunes the
        # record it has just made terminal. No sweep can observe it in between.
        from test_mission import MissionHarness
        from test_workflow_authority import make_record
        from workflow_authority import store as workflow_store
        directory = os.path.join(self.tmp.name, "telegram-store")
        os.mkdir(directory, 0o700)
        harness = MissionHarness(directory)
        # 63 Missions still awaiting approval in ANOTHER chat: active, so the cap can never prune them
        document = workflow_store.default_document()
        for index in range(workflow_store.MAX_WORKFLOW_RECORDS - 1):
            ok, problem, _ = workflow_store.add_workflow(
                document, make_record("wf-other-%02d" % index))
            self.assertTrue(ok, problem)
        with workflow_store.exclusive_store_lock(directory):
            workflow_store.WorkflowStore(directory).save(document)
        harness.offer_mission(uid=1)                         # wf-0001, PLANNED: the 64th record
        self.assertEqual(harness.fresh_workflows()["workflows"]["wf-0001"]["phase"], "PLANNED")
        # the second offer: supersede wf-0001 AND prune it AND add wf-0002, in one locked save.
        # The eviction path never takes the retention lock (one lock only; nothing can invert).
        with mock.patch.object(retention_store.RetentionStore, "lock",
                               side_effect=AssertionError("the eviction path took the retention lock")):
            harness.offer_mission(uid=2)
        workflows = harness.fresh_workflows()["workflows"]
        self.assertNotIn("wf-0001", workflows, "pruned in the same transaction")
        self.assertIn("wf-0002", workflows)
        with open(os.path.join(directory, workflow_store.WORKFLOWS_FILE_NAME)) as handle:
            self.assertNotIn(workflow_store._PRUNED_ENDINGS_KEY, handle.read(),
                             "the in-memory carrier is never persisted")
        # the hand-off holds how it ENDED, never its intent or authorization text
        with open(os.path.join(directory, workflow_store.WORKFLOW_REMOVALS_FILE_NAME)) as handle:
            handoff = handle.read()
        self.assertEqual(sorted(json.loads(handoff)["removed"]), ["wf-0001"])
        self.assertNotIn("do the mission", handoff)
        self.assertNotIn("MISSION AUTHORIZATION", handoff.upper().replace("_", " "))
        # the next sweep adopts it: a retention record WITH its summary, though never observed live
        retention = reclaim.WorkflowRetention(directory, self.clock)
        self.clock.now = 5000
        report = retention.sweep()
        self.assertEqual(report.problems, [])
        record = retention_store.RetentionStore(directory).load()["records"]["workflow_store:wf-0001"]
        self.assertEqual((record["basis"], record["phase"]),
                         ("workflow_not_dispatched", retention_store.PHASE_OBSERVED))
        self.assertEqual(record["summary"]["final_state"], "BLOCKED")
        self.assertIn("cap pruned the record before its retention deadline",
                      record["summary"]["final_state_source"])
        self.assertEqual(workflow_store.load_removals(directory)["removed"], {}, "adopted")
        # the office still reports it, from retention, with its summary
        from mission_retention import view as view_module
        reading = view_module.RetentionReader().retained("workflow_store", "telegram", directory)
        self.assertEqual((reading["state"], reading["total"], reading["awaiting"]), ("observed", 1, 0))
        self.assertEqual([(item["native_id"], item["summary"]["final_state"])
                          for item in reading["missions"]], [("wf-0001", "BLOCKED")])
        # and at its deadline it is finished honestly, its summary kept
        self.clock.now = 5000 + reclaim.RETENTION_SECONDS
        retention.sweep()
        record = retention_store.RetentionStore(directory).load()["records"]["workflow_store:wf-0001"]
        self.assertEqual(record["phase"], retention_store.PHASE_RECLAIMED)
        self.assertEqual(record["summary"]["final_state"], "BLOCKED")

    def test_WP3_the_hand_off_is_bounded_and_refuses_never_drops(self):
        from test_workflow_authority import make_record
        from workflow_authority import record as workflow_record
        from workflow_authority import store as workflow_store
        directory = os.path.join(self.tmp.name, "full-handoff")
        os.mkdir(directory, 0o700)
        wstore = workflow_store.WorkflowStore(directory)
        handoff = workflow_store.default_removals()
        for index in range(workflow_store.MAX_WORKFLOW_REMOVALS):
            ending = workflow_store.removal_ending(dict(make_record("wf-gone-%03d" % index),
                                                        phase=workflow_record.PHASE_BLOCKED))
            handoff["removed"][ending["workflow_id"]] = ending
        with workflow_store.exclusive_store_lock(directory):
            workflow_store.save_removals(directory, handoff)
            document = workflow_store.default_document()
            done = make_record("wf-terminal")
            done["phase"] = workflow_record.PHASE_COMPLETED
            workflow_store.add_workflow(document, done)
            for index in range(workflow_store.MAX_WORKFLOW_RECORDS - 1):
                workflow_store.add_workflow(document, make_record("wf-active-%02d" % index))
            wstore.save(document)
            before = open(os.path.join(directory, "workflows.json"), "rb").read()
            ok, problem, pruned = workflow_store.add_workflow(document, make_record("wf-new"))
            self.assertEqual((ok, pruned), (True, 1), problem)       # the cap prunes wf-terminal
            with self.assertRaises(workflow_store.StoreError):
                wstore.save(document)
        self.assertEqual(open(os.path.join(directory, "workflows.json"), "rb").read(), before,
                         "nothing was written: the pruned record is still in its store")
        self.assertEqual(len(workflow_store.load_removals(directory)["removed"]),
                         workflow_store.MAX_WORKFLOW_REMOVALS, "nothing dropped")

    def test_WP5_the_caps_prune_and_retention_itself_are_the_only_removals_of_a_workflow_record(self):
        # Every product deletion of a workflow record, found by AST over the derived product set:
        # the prune (which hands its ending off) and retention's own removal (which reduced it
        # first). A new removal path fails here before it can evict without a hand-off.
        import ast
        from test_workflow_authority import derive_product_python_files
        found = set()
        for path in derive_product_python_files(REPO_ROOT):
            for node in ast.walk(ast.parse(path.read_text())):
                targets = []
                if isinstance(node, ast.Delete):
                    targets = [ast.unparse(t) for t in node.targets]
                elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and (
                    node.func.attr in ("pop", "popitem", "clear")
                ):
                    targets = [ast.unparse(node.func.value)]
                for target in targets:
                    if "workflows" in target:
                        found.add((path.relative_to(REPO_ROOT).as_posix(), target))
        self.assertEqual(found, {
            ("workflow_authority/store.py", "workflows[workflow_id]"),
            ("mission_retention/reclaim.py", "document['workflows'][workflow_id]"),
        })

    def test_WP4_the_hand_off_projection_decides_and_summarises_exactly_as_the_full_record(self):
        # The hand-off keeps only REMOVAL_ENDING_KEYS; if eligibility or the summary ever read
        # another field, this fails rather than letting the two silently diverge.
        from test_workflow_authority import make_record
        from workflow_authority import record as workflow_record
        from workflow_authority import store as workflow_store
        directory = os.path.join(self.tmp.name, "projection")
        os.mkdir(directory, 0o700)
        retention = reclaim.WorkflowRetention(directory, self.clock)
        shapes = []
        for phase in (workflow_record.PHASE_COMPLETED, workflow_record.PHASE_BLOCKED,
                      workflow_record.PHASE_PLANNED, "DISPATCHED"):
            entry = make_record("wf-%s" % phase.lower(), human_intent="private intent")
            entry["phase"] = phase
            shapes.append(entry)
        dispatched = make_record("wf-blocked-after", human_intent="private intent")
        dispatched["phase"] = workflow_record.PHASE_BLOCKED
        dispatched["receipts"] = [dispatch_module.dispatch_receipt(
            dispatched, now=150, sequence_number=1, turn_id_factory=lambda: "disp-0000000000000001")]
        shapes.append(dispatched)
        for full in shapes:
            ending = workflow_store.removal_ending(full)
            self.assertEqual(sorted(ending), sorted(workflow_store.REMOVAL_ENDING_KEYS))
            self.assertEqual(eligibility.workflow(ending), eligibility.workflow(full), full["phase"])
            if eligibility.workflow(full).eligible:
                self.assertEqual(retention._summary(ending), retention._summary(full))
            self.assertNotIn("private intent", json.dumps(ending))

class CatalogRetentionTests(RetentionFixture):
    """The backend's retention reading reaches the office through the v2 catalog, by ONE rule."""

    def query(self, sources, now):
        from mission_retention import view as retention_view
        from mission_sources import contract
        from mission_sources import contract_v2
        from mission_sources import query_v2
        registered = [{"source_id": source_id, "kind": kind, "store_dir": store_dir,
                       "producer": "local_request", "max_age_seconds": 60,
                       "registered_at": now} for source_id, kind, store_dir in sources]
        document = {"mission_sources_registry_schema_version": 1, "sources": registered}

        class Registry(object):                    # the one method query_v2 calls
            directory = os.path.join(self.tmp.name, "registry")

            def load_measured(self, absent_is_empty=True):
                return document, len(json.dumps(document))
        snapshot = {"schema": contract.SNAPSHOT_SCHEMA, "version": contract.SNAPSHOT_VERSION,
                    "observed_at": now, "producer": "synthetic", "tasks": [], "agents": []}
        chains = {"schema": contract_v2.CHAIN_OBSERVATION_SCHEMA,
                  "version": contract_v2.CHAIN_OBSERVATION_VERSION, "observed_at": now,
                  "producer": "synthetic", "roots": [], "unavailable": [], "over_capacity": []}
        reader = retention_view.RetentionReader()
        return query_v2.query_v2(Registry(), snapshot, chains, now,
                                 retention_of=reader.block, retained_of=reader.retained,
                                 max_age_seconds=contract.DEFAULT_MAX_AGE_SECONDS)

    def retention_of(self, response, mission_id):
        return next(m["retention"] for m in response["missions"]
                    if m["native_id"] == mission_id)

    def test_CR1_the_vocabularies_are_pinned_between_the_contract_and_the_reader(self):
        from mission_retention import view as retention_view
        from mission_sources import contract_v2
        self.assertEqual(contract_v2.RETENTION_KEYS, retention_view.BLOCK_KEYS)
        self.assertEqual(contract_v2.RETAINED_MISSION_KEYS, retention_view.RETAINED_KEYS)
        self.assertEqual(set(contract_v2.RETENTION_PHASES),
                         {retention_view.PHASE_INTACT} | set(retention_store.PHASES))
        self.assertEqual(contract_v2.RETENTION_SUMMARY_KEYS, retention_store.SUMMARY_KEYS)
        self.assertEqual(contract_v2.RETENTION_FACT_KEYS, retention_store.SUMMARY_FACT_KEYS)
        self.assertEqual(set(contract_v2.RETENTION_FACT_STATES), set(retention_store.SUMMARY_STATES))

    def test_CR2_eligible_ineligible_and_reclaimed_missions_read_through_the_real_query(self):
        done = self.completed()
        abandoned = self.abandoned_unstarted()
        pending = self.of_record()
        now = self.clock.now
        response = self.query([("mc", "mission_core", self.state)], now)
        block = self.retention_of(response, done["mission_id"])
        self.assertEqual((block["state"], block["eligible"], block["basis"], block["phase"]),
                         ("observed", True, "verified_target_stopped", "intact"))
        self.assertEqual(self.retention_of(response, abandoned["mission_id"])["basis"],
                         "no_start_recorded")
        open_block = self.retention_of(response, pending["mission_id"])
        self.assertEqual((open_block["eligible"], open_block["reason"]), (False, "not_terminal"))
        # after reclamation: the phase and the bounded summary, never "intact"
        self.clock.now = self.terminal_at(done["mission_id"]) + reclaim.RETENTION_SECONDS
        self.retention.sweep()
        response = self.query([("mc", "mission_core", self.state)], self.clock.now)
        block = self.retention_of(response, done["mission_id"])
        self.assertEqual(block["phase"], "reclaimed")
        self.assertEqual(block["summary"]["final_state"], "COMPLETED")

    def test_CR3_a_workflow_gone_from_its_store_is_still_reported_with_its_summary(self):
        from test_workflow_authority import make_record
        from workflow_authority import record as workflow_record
        from workflow_authority import store as workflow_store
        directory = os.path.join(self.tmp.name, "wf-store")
        done = make_record("wf-done", human_intent="intent")
        done["phase"] = workflow_record.PHASE_COMPLETED
        document = workflow_store.default_document()
        workflow_store.add_workflow(document, done)
        with workflow_store.exclusive_store_lock(directory):
            workflow_store.WorkflowStore(directory).save(document)
        retention = reclaim.WorkflowRetention(directory, self.clock)
        self.clock.now = 1000
        retention.sweep()
        self.clock.now = 1000 + reclaim.RETENTION_SECONDS
        retention.sweep()
        response = self.query([("wf", "workflow_store", directory)], self.clock.now)
        self.assertEqual([m["native_id"] for m in response["missions"]], [])
        (reading,) = response["retained_by_source"]
        self.assertEqual((reading["source_id"], reading["state"], reading["total"], reading["awaiting"]),
                         ("wf", "observed", 1, 0))
        self.assertEqual([(r["mission_ref"], r["phase"]) for r in reading["missions"]],
                         [("wf:wf-done", "reclaimed")])
        self.assertEqual(reading["missions"][0]["summary"]["final_state"], "COMPLETED")

    # -- archive health (round 9): unreadable is never a known-empty archive, on the real query

    ARCHIVE = (REPO_ROOT / "integrations" / "pixel-agents" / "server" / "__tests__" / "fixtures"
               / "di-backend" / "retention-archive-unavailable.FIXTURE.json")

    def test_CR4_an_empty_store_with_an_unreadable_retention_store_reads_unavailable_never_empty(self):
        from workflow_authority import store as workflow_store
        directory = os.path.join(self.tmp.name, "emptied-store")
        with workflow_store.exclusive_store_lock(directory):
            workflow_store.WorkflowStore(directory).save(workflow_store.default_document())
        retention_path = os.path.join(directory, retention_store.RETENTION_FILE_NAME)
        with open(retention_path, "w") as handle:
            handle.write('{"schema_version": 1, "records": {')        # malformed
        os.chmod(retention_path, 0o600)
        response = self.query([("wf", "workflow_store", directory)], self.clock.now)
        self.assertEqual(response["missions"], [], "no original record is left to carry a block")
        (reading,) = response["retained_by_source"]
        self.assertEqual((reading["state"], reading["total"], reading["awaiting"], reading["missions"]),
                         ("unavailable", None, None, []))
        self.assertEqual(reading["problem"], retention_store.PROBLEM_UNREADABLE)
        # the bytes the office's server is tested on (server/__tests__/diRetentionArchiveWire.test.ts)
        text = json.dumps({"fixture": True, "generator": "tests/test_mission_retention.py CR4",
                           "label": "FIXTURE (generated, not live): the real catalog's retained_by_source"
                                    " for an EMPTY workflow store whose mission_retention.json is"
                                    " malformed", "retained_by_source": response["retained_by_source"]},
                          indent=2, sort_keys=True) + "\n"
        if os.environ.get("DI_EXPORT_PANEL_ANSWERS") == "1":
            self.ARCHIVE.write_text(text, encoding="utf-8")
        self.assertEqual(self.ARCHIVE.read_text(encoding="utf-8"), text)
        # and, readable again, the same empty store is an OBSERVED empty archive: a different value
        os.remove(retention_path)
        (reading,) = self.query([("wf", "workflow_store", directory)], self.clock.now)["retained_by_source"]
        self.assertEqual((reading["state"], reading["total"], reading["missions"]), ("observed", 0, []))

    def test_CR5_a_catalog_that_does_not_read_retention_reports_no_archive_at_all_never_an_empty_one(self):
        from mission_sources import contract, contract_v2, query_v2
        registered = [{"source_id": "mc", "kind": "mission_core", "store_dir": self.state,
                       "producer": "local_request", "max_age_seconds": 60,
                       "registered_at": self.clock.now}]
        document = {"mission_sources_registry_schema_version": 1, "sources": registered}

        class Registry(object):
            directory = os.path.join(self.tmp.name, "registry")

            def load_measured(self, absent_is_empty=True):
                return document, len(json.dumps(document))
        snapshot = {"schema": contract.SNAPSHOT_SCHEMA, "version": contract.SNAPSHOT_VERSION,
                    "observed_at": self.clock.now, "producer": "synthetic", "tasks": [], "agents": []}
        chains = {"schema": contract_v2.CHAIN_OBSERVATION_SCHEMA,
                  "version": contract_v2.CHAIN_OBSERVATION_VERSION, "observed_at": self.clock.now,
                  "producer": "synthetic", "roots": [], "unavailable": [], "over_capacity": []}
        response = query_v2.query_v2(Registry(), snapshot, chains, self.clock.now,
                                     max_age_seconds=contract.DEFAULT_MAX_AGE_SECONDS)
        self.assertIsNone(response["retained_by_source"])

    def test_CR6_the_listed_cap_never_hides_the_exact_total(self):
        from mission_retention import view as retention_view
        from test_workflow_authority import make_record
        from workflow_authority import record as workflow_record
        from workflow_authority import store as workflow_store
        directory = os.path.join(self.tmp.name, "two-gone")
        document = workflow_store.default_document()
        for name in ("wf-a", "wf-b"):
            entry = make_record(name)
            entry["phase"] = workflow_record.PHASE_COMPLETED
            workflow_store.add_workflow(document, entry)
        with workflow_store.exclusive_store_lock(directory):
            workflow_store.WorkflowStore(directory).save(document)
        retention = reclaim.WorkflowRetention(directory, self.clock)
        self.clock.now = 1000
        retention.sweep()
        self.clock.now = 1000 + reclaim.RETENTION_SECONDS
        retention.sweep()
        with mock.patch.object(retention_view, "MAX_LISTED", 1):
            (reading,) = self.query([("wf", "workflow_store", directory)], self.clock.now)[
                "retained_by_source"]
        self.assertEqual((reading["state"], reading["total"], len(reading["missions"])),
                         ("observed", 2, 1), "one listed, and the exact two said")

    def test_CR7_the_archive_shape_and_bounds_are_pinned_between_the_reader_and_the_contract(self):
        from mission_retention import view as retention_view
        from mission_sources import contract_v2
        from mission_sources import registry as registry_module
        self.assertEqual(contract_v2.RETAINED_SOURCE_KEYS, retention_view.ARCHIVE_KEYS)
        self.assertEqual(retention_view.MAX_LISTED, contract_v2.MAX_RETAINED_MISSIONS)
        self.assertEqual(contract_v2.MAX_RETAINED_SOURCES, registry_module.MAX_REGISTERED_SOURCES)
        self.assertEqual(sorted(contract_v2.retained_source_unavailable("s", "p")),
                         sorted(contract_v2.RETAINED_SOURCE_KEYS))


class StartupAndRunningPathTests(RetentionFixture):
    """BOTH paths: overdue on the next start, and a deadline crossed while already running."""

    def flags(self, command):
        return ["--mission-state-dir", self.state, "--mission-control-repo", self.control,
                "--mission-workspace-repository", self.repository,
                "--mission-workspaces-root", self.root, command]

    def fake_bridge(self, missions, control, clock, run_guard):
        from target_runtime import mission_bridge as bridge_module
        return bridge_module.MissionBridge(
            missions, "/control-repo", clock, spawn_fn=self.spawn,
            observer_fn=self.observer, spawn_records_fn=self.records,
            transport=self.transport,
            surface_digest_fn=lambda repo: dict(self.surface_digest),
            ownership=self.ownership, owner_directory="/owner-scope",
            workspace_repository=self.repository, workspaces_root=self.root,
            worker=self.trust, run_guard=run_guard)

    def composition(self, state, control, repository, root):
        from mission_panel import composition as composition_module
        return composition_module.compose(state, control, repository, root, clock=self.clock,
                                          bridge_factory=self.fake_bridge)

    def started_panel(self):
        """A fresh panel over the SAME state directory, as a restart builds it, then started."""
        service = service_module.PanelService(
            self.panel_surface, journal_module.PanelJournal(self.state), self.convo,
            self.registry, self.clock, bridge=self.bridge, retention=self.retention)
        service.start()
        return service

    def test_SP1_startup_path_before_exactly_at_and_after_the_deadline(self):
        view = self.completed()
        terminal = self.terminal_at(view["mission_id"])
        self.clock.now = terminal + reclaim.RETENTION_SECONDS - 1
        self.started_panel()
        self.assert_payload_intact()
        self.clock.now = terminal + reclaim.RETENTION_SECONDS
        self.started_panel()
        self.assert_payload_gone(view["mission_id"])
        self.assertIsNotNone(self.record(view["mission_id"])["summary"])

    def test_SP2_startup_path_long_overdue_seeded_before_any_retention_ran(self):
        view = self.completed()
        self.assertIsNone(self.record(view["mission_id"]))      # nothing ever observed it
        self.clock.now = self.terminal_at(view["mission_id"]) + 90 * DAY
        self.started_panel()
        self.assert_payload_gone(view["mission_id"])

    def test_SP3_running_path_through_the_runtimes_own_tick_without_a_restart(self):
        import contextlib
        import io
        from target_runtime import cli as cli_module
        view = self.completed()
        terminal = self.terminal_at(view["mission_id"])
        self.clock.now = terminal + DAY                      # not due at startup, nor at pass 1
        moved = []

        def sleeper(seconds):                                # between pass 1 and pass 2
            moved.append(self.clock.now)
            self.clock.now = terminal + reclaim.RETENTION_SECONDS
        with contextlib.redirect_stderr(io.StringIO()):
            code = cli_module.main(self.flags("run"), sleeper=sleeper, passes=2,
                                   mission_composition=self.composition,
                                   retention_clock=self.clock)
        self.assertEqual(code, cli_module.EXIT_OK)
        self.assertEqual(moved, [terminal + DAY], "one pause: the same process throughout")
        self.assert_payload_gone(view["mission_id"])
        self.assertEqual(self.record(view["mission_id"])["phase"], retention_store.PHASE_RECLAIMED)

    def test_SP4_running_path_before_and_exactly_at_on_the_tick(self):
        import contextlib
        import io
        from target_runtime import cli as cli_module
        view = self.completed()
        terminal = self.terminal_at(view["mission_id"])
        self.clock.now = terminal + reclaim.RETENTION_SECONDS - 1
        with contextlib.redirect_stderr(io.StringIO()):
            cli_module.main(self.flags("run"), sleeper=lambda seconds: None, passes=1,
                            mission_composition=self.composition, retention_clock=self.clock)
        self.assert_payload_intact()
        self.clock.now = terminal + reclaim.RETENTION_SECONDS
        with contextlib.redirect_stderr(io.StringIO()):
            cli_module.main(self.flags("run"), sleeper=lambda seconds: None, passes=1,
                            mission_composition=self.composition, retention_clock=self.clock)
        self.assert_payload_gone(view["mission_id"])

    def test_SP5_a_failing_sweep_never_wedges_the_tick(self):
        import contextlib
        import io
        from target_runtime import cli as cli_module
        stderr = io.StringIO()
        with mock.patch.object(reclaim.MissionCoreRetention, "sweep",
                               side_effect=retention_store.RetentionStoreError("unreadable")):
            with contextlib.redirect_stderr(stderr):
                code = cli_module.main(self.flags("run"), sleeper=lambda seconds: None, passes=3,
                                       mission_composition=self.composition,
                                       retention_clock=self.clock)
        self.assertEqual(code, cli_module.EXIT_OK)
        self.assertEqual(stderr.getvalue().count("local retention could not finish"), 1,
                         "said once, the loop went on")


class OwnerDeleteHelpers(object):
    """The owner's delete through the service, which is the gate (no tests of its own)."""

    def delete(self, mission_id, displayed):
        return self.retention_service().delete_local_data(
            mission_id, displayed, "mc:" + mission_id, self.retention.store_identity)

    def retention_service(self):
        return service_module.PanelService(
            self.panel_surface, journal_module.PanelJournal(self.state), self.convo,
            self.registry, self.clock, bridge=self.bridge, retention=self.retention)

    def refused(self, reason, mission_id, displayed):
        with self.assertRaises(service_module.PanelRefusal) as caught:
            self.delete(mission_id, displayed)
        self.assertEqual(caught.exception.problem, service_module.PROBLEM_DELETE_PREFIX + reason,
                         caught.exception.reason)
        return caught.exception


class OwnerDeleteTests(OwnerDeleteHelpers, RetentionFixture):
    """The fail-closed refusal matrix and the deleted outcome, at the service, which is the gate."""

    def test_OD1_target_unresolved(self):
        self.refused(reclaim.REFUSE_UNRESOLVED, "mn-" + "0" * 32, "COMPLETED")

    def test_OD2_not_owned_a_mission_this_panel_did_not_submit(self):
        mission_id = self.running()                          # through the surface, not the panel
        self.refused(reclaim.REFUSE_NOT_OWNED, mission_id, "RUNNING")

    def test_OD3_stale_the_display_is_not_what_the_record_says_now(self):
        view = self.completed()
        self.refused(reclaim.REFUSE_STALE, view["mission_id"], "RUNNING")

    def test_OD4_running_live_workers(self):
        view = self.of_record()
        self.approve(view)
        self.service.run(view["request_ref"], "dispatch")
        self.observer.raw = raw_observation()
        self.service.run(view["request_ref"], "observe")
        self.refused(reclaim.REFUSE_RUNNING, view["mission_id"], "RUNNING")

    def test_OD5_not_finished(self):
        view = self.of_record()
        self.refused(reclaim.REFUSE_NOT_TERMINAL, view["mission_id"], "AWAITING_DECISION")

    def test_OD6_no_positive_basis_terminal_after_a_start_with_no_stopped_proof(self):
        view = self.of_record()
        self.approve(view)
        self.service.run(view["request_ref"], "dispatch")
        self.observer.raw = raw_observation()
        self.service.run(view["request_ref"], "observe")
        self.finish_team(view["mission_id"], status="ERROR")
        self.service.run(view["request_ref"], "verify")
        state = self.document("missions.json")["missions"][view["mission_id"]]["state"]
        self.assertEqual(state, "BLOCKED")
        self.refused(reclaim.REFUSE_NO_BASIS, view["mission_id"], "BLOCKED")
        self.assert_payload_intact()

    def test_OD7_busy_a_progression_pass_holds_the_run_guard(self):
        view = self.completed()
        with self.guard.hold(view["mission_id"]):
            self.refused(reclaim.REFUSE_BUSY, view["mission_id"], "COMPLETED")
        self.assert_payload_intact()

    def test_OD8_deleted_differs_from_reclaimed_and_cannot_resurrect(self):
        reclaimed = self.completed()
        deleted = self.completed()
        self.clock.now = self.terminal_at(reclaimed["mission_id"]) + reclaim.RETENTION_SECONDS
        self.retention.sweep()                             # automatic: both are due
        # the owner deletes one: its summary goes too, the other keeps it
        self.delete(deleted["mission_id"], "COMPLETED")
        kept, gone = self.record(reclaimed["mission_id"]), self.record(deleted["mission_id"])
        self.assertEqual((kept["phase"], gone["phase"]), ("reclaimed", "deleted"))
        self.assertIsNotNone(kept["summary"])
        self.assertIsNone(gone["summary"])
        self.assertEqual(sorted(gone), sorted(retention_store.RECORD_KEYS))
        self.refused(reclaim.REFUSE_ALREADY_DELETED, deleted["mission_id"], "COMPLETED")
        # a restart, a re-read and another sweep: still deleted, never intact, never summarised
        reread = reclaim.MissionCoreRetention(self.state, self.clock, self.guard)
        self.clock.now += 400 * DAY
        reread.sweep()
        self.assertEqual(self.record(deleted["mission_id"])["phase"], "deleted")
        self.assertIsNone(self.record(deleted["mission_id"])["summary"])
        view = self.retention_service().request_view(deleted["request_ref"])
        self.assertEqual(view["reclaimed"]["outcome"], "deleted")
        self.assertIsNone(view["proposal"])

    def test_OD9_owner_delete_of_an_eligible_mission_before_its_deadline(self):
        view = self.rejected()                              # terminal, nothing ever started
        result = self.delete(view["mission_id"], "REJECTED")
        self.assertEqual((result["ok"], result["deletion"]), (True, service_module.DELETION_COMPLETED))
        self.assertEqual(result["deleted"]["phase"], "deleted")
        self.assertIsNone(result["deleted"]["summary"])
        # what it was, and what each store changed: nothing retained a summary before this
        self.assertEqual(result["deleted"]["previously"], "intact")
        self.assertEqual(result["deleted"]["removed"]["summary"], False)
        self.assertGreater(result["deleted"]["removed"]["conversation_turns"], 0)
        self.assertEqual(result["deleted"]["removed"]["requests"], 1)
        self.assert_payload_gone(view["mission_id"])



class DeleteContractTests(OwnerDeleteHelpers, RetentionFixture):
    """The owner-delete result contract (lead ruling after round 8): a REFUSAL writes nothing
    durable (no deletion queued, a retained summary still retained); a deletion that started and
    could not finish is said as started, never as refused; and the next pass, in a restarted
    process, does exactly what the caller was told."""

    def test_DC1_the_held_submit_race_refuses_with_nothing_written_and_a_restart_agrees(self):
        view = self.rejected()
        conversation_id = next(
            t["conversation_id"] for t in self.document("mission_panel_journal.json")["turns"].values()
            if t["submission"] is not None)
        # a second draft in the SAME conversation, its submission held in flight by the panel's
        # own first step (the journal reservation submit_draft makes before Mission Core)
        turn = self.drafted(objective="A second objective, being submitted right now",
                            conversation_id=conversation_id)
        journal_module.PanelJournal(self.state).reserve_submission(
            turn["turn_id"], turn["draft"]["proposal_digest_sha256"], None, self.clock.now)
        retention_before = self.retention_records()
        refusal = self.refused(reclaim.REFUSE_IN_FLIGHT, view["mission_id"], "REJECTED")
        self.assertEqual(refusal.details["refusal"], reclaim.REFUSE_IN_FLIGHT)
        # the caller was told refused, and the disk agrees: nothing queued, nothing reduced
        self.assertEqual(self.retention_records(), retention_before)
        self.assertIsNone(self.record(view["mission_id"]))
        self.assert_payload_intact()
        self.assertIn("being submitted right now",
                      self.bytes_of("mission_panel_journal.json").decode("utf-8"))
        # a restarted process's pass deletes nothing and never reads the Mission as deleting
        restarted = reclaim.MissionCoreRetention(self.state, self.clock, self.guard)
        restarted.sweep()
        self.assertEqual(self.record(view["mission_id"])["phase"], retention_store.PHASE_OBSERVED)
        self.assert_payload_intact()

    def test_DC2_an_automatically_reclaimed_missions_summary_survives_a_refused_delete(self):
        view = self.completed()
        self.clock.now = self.terminal_at(view["mission_id"]) + reclaim.RETENTION_SECONDS
        self.assertEqual(self.retention.sweep().reclaimed, 1)
        reclaimed = self.record(view["mission_id"])
        self.assertEqual(reclaimed["phase"], retention_store.PHASE_RECLAIMED)
        self.assertIsNotNone(reclaimed["summary"], "automatic reclamation KEEPS the summary")
        retention_before = self.bytes_of("mission_retention.json")
        # A reclaimed conversation takes no new turn, so no production path can race this delete;
        # the journal's OWN refusal is injected at its boundary to prove the withdrawal.
        busy = journal_module.JournalError("a turn of these conversations is still in flight",
                                           journal_module.PROBLEM_CONVERSATION_BUSY)
        with mock.patch.object(self.retention.journal, "reclaim_conversations", side_effect=busy):
            self.refused(reclaim.REFUSE_IN_FLIGHT, view["mission_id"], "COMPLETED")
        # refused: the summary they still had, they still have, byte for byte on disk
        self.assertEqual(self.bytes_of("mission_retention.json"), retention_before)
        self.assertEqual(self.record(view["mission_id"]), reclaimed)
        restarted = reclaim.MissionCoreRetention(self.state, self.clock, self.guard)
        restarted.sweep()
        self.assertEqual(self.record(view["mission_id"]), reclaimed, "nothing was queued")
        # and the real deletion then REMOVES that summary, and says so
        result = self.delete(view["mission_id"], "COMPLETED")["deleted"]
        self.assertEqual((result["phase"], result["previously"]), ("deleted", "reclaimed"))
        self.assertTrue(result["removed"]["summary"], "a real summary was removed")
        self.assertIsNone(self.record(view["mission_id"])["summary"])

    def test_DC3_a_deletion_that_started_and_failed_is_said_as_started_and_finished_by_the_next_pass(self):
        view = self.rejected()
        failure = store_module.LocalRequestStoreError("the request store could not be written")
        with mock.patch.object(reclaim.request_store, "reclaim_requests", side_effect=failure):
            answer = self.delete(view["mission_id"], "REJECTED")
        # round 9 finding 1: an ACCEPTED, pending answer (ok true), never a refusal of any kind
        self.assertEqual((answer["ok"], answer["deletion"]), (True, service_module.DELETION_PENDING))
        self.assertEqual(answer["deleted"]["phase"], retention_store.PHASE_DELETING)
        self.assertIsNone(answer["deleted"]["summary"])
        self.assertGreater(answer["deleted"]["removed"]["conversation_turns"], 0)
        # the disk agrees with "started": deleting, summary removed, never readable as intact
        record = self.record(view["mission_id"])
        self.assertEqual((record["phase"], record["summary"]), (retention_store.PHASE_DELETING, None))
        self.assertNotIn(USER_WORDS, self.bytes_of("mission_panel_journal.json").decode("utf-8"))
        # the next pass, in a restarted process, finishes it
        restarted = reclaim.MissionCoreRetention(self.state, self.clock, self.guard)
        restarted.sweep()
        self.assertEqual(self.record(view["mission_id"])["phase"], retention_store.PHASE_DELETED)
        self.assert_payload_gone(view["mission_id"])

    def test_DC4_a_refusal_whose_withdrawal_cannot_be_saved_is_reported_as_started_not_refused(self):
        view = self.rejected()
        busy = journal_module.JournalError("a turn of these conversations is still in flight",
                                           journal_module.PROBLEM_CONVERSATION_BUSY)
        original = self.retention.retention.save
        calls = []

        def save(document):
            calls.append(1)
            if len(calls) == 2:                     # the withdrawal
                raise retention_store.RetentionStoreError("disk full")
            return original(document)
        with mock.patch.object(self.retention.journal, "reclaim_conversations", side_effect=busy), \
                mock.patch.object(self.retention.retention, "save", side_effect=save):
            answer = self.delete(view["mission_id"], "REJECTED")
        self.assertEqual((answer["ok"], answer["deletion"]), (True, service_module.DELETION_PENDING),
                         "a deletion that could not be withdrawn is said as started, never refused")
        self.assertEqual(self.record(view["mission_id"])["phase"], retention_store.PHASE_DELETING)
        self.assert_payload_intact()                # nothing reduced yet; the next pass finishes it
        reclaim.MissionCoreRetention(self.state, self.clock, self.guard).sweep()
        self.assertEqual(self.record(view["mission_id"])["phase"], retention_store.PHASE_DELETED)
        self.assert_payload_gone(view["mission_id"])

    def test_DC5_a_resumed_deletion_never_takes_the_words_of_a_mission_that_joined_its_conversation(self):
        # The one way a deletion resumes with its conversation unreduced: the withdrawal could not be
        # saved (DC4). A live Mission then lands in that conversation, through the production
        # methods, before the next pass. The resumed pass judges the conversation again.
        view = self.rejected()
        conversation_id = next(
            t["conversation_id"] for t in self.document("mission_panel_journal.json")["turns"].values()
            if t["submission"] is not None)
        busy = journal_module.JournalError("a turn of these conversations is still in flight",
                                           journal_module.PROBLEM_CONVERSATION_BUSY)
        original = self.retention.retention.save
        calls = []

        def save(document):
            calls.append(1)
            if len(calls) == 2:
                raise retention_store.RetentionStoreError("disk full")
            return original(document)
        with mock.patch.object(self.retention.journal, "reclaim_conversations", side_effect=busy), \
                mock.patch.object(self.retention.retention, "save", side_effect=save):
            self.assertEqual(self.delete(view["mission_id"], "REJECTED")["deletion"],
                             service_module.DELETION_PENDING)
        live_words = "a live Mission's words, after the deletion was recorded"
        reply = "```mission-proposal\n%s\n```" % json.dumps(
            dict(GOOD, objective="A live objective in the same conversation"))
        turn = self.say(live_words, reply, conversation_id)
        self.service.submit_draft(turn["turn_id"], turn["draft"]["proposal_digest_sha256"])
        report = reclaim.MissionCoreRetention(self.state, self.clock, self.guard).sweep()
        self.assertIn((view["mission_id"], reclaim.PROBLEM_DELETE_WAITS_FOR_SHARED), report.problems)
        self.assertIn(live_words, self.bytes_of("mission_panel_journal.json").decode("utf-8"))
        self.assertIn("A live objective in the same conversation",
                      self.bytes_of("local_requests.json").decode("utf-8"))
        self.assertEqual(self.record(view["mission_id"])["phase"], retention_store.PHASE_DELETING)


if __name__ == "__main__":
    unittest.main()
