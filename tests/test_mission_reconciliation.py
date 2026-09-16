"""Behavioral tests for Task 7 Stage 2, roadmap item 16: deterministic,
idempotent Mission Reconciliation that detects drift and repairs only
supported derived state.

Everything here drives the REAL store and the REAL service in a
temporary protected directory with an injected clock. Source reports are
materialized by the TEST HARNESS from its controlled adapters, outside
the package, and handed in as plain data bound to the head cursor they
were collected at (``materialize`` in test_mission_observation). Nothing
is launched, messaged or delivered.

Sections:
  R1  detection: stale and unavailable sources, task and review changes,
      candidate and baseline drift, missing and contradicted proof,
      receipts bound to recorded receipt references, ambiguous
      outcomes, revision drift
  R2  repairs only supported derived state: the ledger entry, the
      reconciliation record and the re-bound snapshot; nothing else moves
      and authority is byte-identical
  R3  no speculative effects, structurally (AST of the module and of the
      service method) and behaviorally (missing, invalid, ambiguous,
      unbound and stale receipt reports cause nothing)
  R4  idempotent on unchanged meaning; no event storms; a timestamp-only
      refresh writes literally nothing (byte equality of the store);
      one pass converges after accepted evidence crosses its age bound
  R5  stale concurrent writes reject without mutation on the write path
      AND the no-op path; reports collected at a cursor the document has
      left refuse; an EDIT makes the position not current; conflicts are
      refused, never guessed; terminal is terminal
  R6  duplicates reuse the existing reservation / idempotency discipline
  R7  reload preserves provenance, freshness semantics, blockers,
      reconciliation position, journal cursor and snapshot bindings
  R8  every new shape fails closed on load AND save, with its own code;
      every bound is a module constant and refuses at the bound
  R9  compatibility: a record without ``reconciliations`` still loads
  RA  Task 7 Stage 2 receipt attestation: positive findings only for the
      attested form (real validator, real consumer/service path);
      structural validity is not success; unknown / moved / stale
      provenance stays conservative; bounds; convergence; reconciliation
      manufactures nothing; reload and replay preserve the form and
      validate nothing
"""

import ast
import copy
import json
import os
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "tests"))

from mission import record as mission_record  # noqa: E402
from mission import journal as mj  # noqa: E402
from mission import observation as ob  # noqa: E402
from mission import reconciliation as rc  # noqa: E402
from mission import state as ms  # noqa: E402
from mission import state_service as mss  # noqa: E402
from mission import store as mst  # noqa: E402
import _non_invoking as ni  # noqa: E402
from pr_delivery import authorization as delivery_contract  # noqa: E402
from test_mission_observation import (  # noqa: E402
    FORBIDDEN_CALLS, FORBIDDEN_NAMES, Counting, ObservationFixture,
    _function_calls, _module_facts, materialize,
)
from test_mission_state import HEX_A, contract, hexid  # noqa: E402

DERIVED_KEYS = {"applied_operations", "sequence", "updated_at", "snapshot",
                "reconciliations"}


class ReconciliationFixture(ObservationFixture):

    def adapters(self, task=rc.TASK_REPORT_ACTIVE, review=None, candidate=None,
                 delivery=None, age=0):
        result = {}
        if task is not None:
            result["task"] = Counting(self.answer(task, age))
        if review is not None:
            result["review"] = Counting(self.answer(review, age))
        if candidate is not None:
            result["candidate"] = Counting(self.answer(candidate, age))
        if delivery is not None:
            result["delivery"] = Counting(self.answer(delivery, age))
        return result

    def reconcile(self, adapters, mission_id=None, expected_sequence=None,
                  operation_id=None, context=None, inputs=None):
        """Materialize at the current head, mint, snapshot the bytes, then
        reconcile: the bytes before are what a no-op must leave untouched."""
        mission_id = mission_id or self.mission_id
        context = context or self.context
        if inputs is None:
            inputs = materialize(adapters, mission_id, self.head(mission_id))
        operation_id = operation_id or self.oid(context)
        if expected_sequence is None:
            expected_sequence = self.seq(mission_id)
        self.before = self.read_bytes()
        return self.service.reconcile(mission_id, operation_id, expected_sequence,
                                      inputs, context)

    def kinds(self, result):
        return [f["kind"] for f in result["findings"]]

    def state(self, mission_id=None):
        return self.document()["mission_state"][mission_id or self.mission_id]

    def ledger_kinds(self, mission_id=None):
        return [op["kind"] for op in self.state(mission_id)["applied_operations"]]

    def record_receipt(self, mission_id=None, locator="receipt:42", digest="d" * 64):
        return self.call("record_artifact", mission_id or self.mission_id, "pr_receipt",
                         mission_record.ARTIFACT_ROLE_PRODUCED,
                         ms.LOCATOR_KIND_DELIVERY_RECEIPT_REFERENCE, locator, digest,
                         True, [])["artifact_id"]

    # -- the OUTER receipt fixture: the EXISTING delivery receipt contract --

    DELIVERY_ID = "dlv-task7-fixture"
    AUTHORITY_DIGEST = "1" * 64

    def existing_receipt(self, head_sha="c" * 40):
        """A receipt in the shape the delivery package's contract defines
        (``pr_delivery.authorization.RECEIPT_KEYS``), digested by THAT
        contract's ``receipt_digest`` and accepted by THAT contract's
        ``validate_receipt``. The mission package never sees this
        object; only the materialized judgement crosses the seam."""
        receipt = {
            "receipt_id": "rcpt-" + "a" * 24,
            "step": delivery_contract.STEP_PR_CREATE,
            "delivery_id": self.DELIVERY_ID,
            "parent_authority_digest_sha256": self.AUTHORITY_DIGEST,
            "derived_at": 5,
            "attempt": 1,
            "state": delivery_contract.RECEIPT_SUCCEEDED,
            "binding": {
                "owner": "Example", "repo": "Repo",
                "remote_url_exact": "https://github.com/Example/Repo.git",
                "head_branch": "phase1/mission-truth", "head_sha": head_sha,
                "base_branch": "main", "title_sha256": "2" * 64,
                "body_sha256": "3" * 64, "candidate_identity_digest": "4" * 64,
            },
            "observed": None,
            "receipt_digest_sha256": "0" * 64,
        }
        receipt["receipt_digest_sha256"] = delivery_contract.receipt_digest(receipt)
        return receipt

    def validator_report(self, receipt, artifact_id, locator=None):
        """Run the EXISTING receipt validator over ``receipt`` and
        materialize its judgement, bound to the receipt it judged: VALID
        when the contract accepts, INVALID when it refuses; either way
        the report names the artifact, the reference and the receipt's
        own content digest."""
        try:
            delivery_contract.validate_receipt(
                receipt, receipt["step"], self.DELIVERY_ID, self.AUTHORITY_DIGEST,
                "receipt")
            status = rc.DELIVERY_REPORT_VALID
        except Exception:  # noqa: BLE001 - the contract's own refusal
            status = rc.DELIVERY_REPORT_INVALID
        return {"status": status, "receipt_artifact_id": artifact_id,
                "locator": locator or "receipt:" + receipt["receipt_id"],
                "receipt_digest_sha256": receipt["receipt_digest_sha256"]}

    def record_validated_receipt(self, receipt, mission_id=None):
        return self.record_receipt(mission_id, "receipt:" + receipt["receipt_id"],
                                   receipt["receipt_digest_sha256"])

    def receipts(self, mission_id=None):
        return [a for a in self.state(mission_id)["artifacts"]
                if a["locator_kind"] == ms.LOCATOR_KIND_DELIVERY_RECEIPT_REFERENCE]

    def receipts_by_attestation(self, mission_id=None):
        split = {"attested": [], "unattested": []}
        for artifact in self.receipts(mission_id):
            key = "attested" if ms.receipt_attestation_of(artifact) else "unattested"
            split[key].append(artifact["artifact_id"])
        return split


# ====================================================================
# R1. Detection
# ====================================================================


class R1DetectionTests(ReconciliationFixture):

    def test_R1_first_pass_records_sources_provenance_and_findings_as_an_event(self):
        result = self.reconcile(self.adapters())
        self.assertTrue(result["changed"])
        self.assertFalse(result["idempotent"])
        self.assertEqual(result["sequence"], 4)
        self.assertEqual(result["observed_position"], 3)
        self.assertEqual(result["observed_revision"], 1)
        self.assertEqual(result["finding_count"], len(result["findings"]))
        self.assertEqual(self.kinds(result), [
            rc.FINDING_PROOF_MISSING, rc.FINDING_SOURCE_UNAVAILABLE,
            rc.FINDING_SOURCE_UNAVAILABLE, rc.FINDING_SOURCE_UNAVAILABLE])
        self.assertEqual(self.ledger_kinds()[-1], ms.OPERATION_RECONCILE)
        record = self.state()["reconciliations"][-1]
        self.assertEqual(set(record), set(rc.RECONCILIATION_KEYS))
        self.assertEqual(record["sources"]["task"],
                         {"standing": rc.STANDING_REPORTED, "value": "ACTIVE"})
        self.assertEqual(record["sources"]["review"],
                         {"standing": rc.STANDING_UNAVAILABLE, "value": None})
        # Provenance is stored beside the sources, never inside them.
        self.assertEqual(set(record["sources"]["task"]), set(rc.SOURCE_KEYS))
        self.assertEqual(record["source_provenance"]["task"],
                         {"observed_at": self.clock()})
        self.assertEqual(record["source_provenance"]["review"], {"observed_at": None})
        self.assertEqual(record["observed_journal_digest_sha256"],
                         mj.journal_digest_at(self.state(), 3))
        self.assertEqual(record["reconciled_at"], self.clock())
        for finding in record["findings"]:
            self.assertEqual(set(finding), set(rc.FINDING_KEYS))
            self.assertIn(finding["kind"], rc.FINDING_KINDS)

    def test_R1_unknown_and_unavailable_sources_are_detected_apart_and_stale_is_read_time(self):
        adapters = {"task": Counting(self.answer("ACTIVE", age=10 ** 6)),
                    "review": Counting(None),
                    "candidate": Counting(raise_=RuntimeError("down"))}
        result = self.reconcile(adapters)
        findings = dict((f["subject"], f["kind"]) for f in result["findings"]
                        if f["kind"] in (rc.FINDING_SOURCE_UNKNOWN,
                                         rc.FINDING_SOURCE_UNAVAILABLE))
        self.assertEqual(findings, {"review": rc.FINDING_SOURCE_UNKNOWN,
                                    "candidate": rc.FINDING_SOURCE_UNAVAILABLE,
                                    "delivery": rc.FINDING_SOURCE_UNAVAILABLE})
        record = self.state()["reconciliations"][-1]
        self.assertEqual(record["sources"]["task"]["standing"], rc.STANDING_REPORTED)
        self.assertNotIn("stale", json.dumps(record["sources"]))
        self.assertEqual(record["source_provenance"]["task"]["observed_at"],
                         self.clock() - 10 ** 6)
        report = self.observe(adapters)
        self.assertEqual(report["task"]["freshness"], ob.FRESHNESS_STALE)
        self.assertEqual(report["reconciliation"]["findings"], result["findings"])

    def test_R1_task_and_review_changes_are_detected_against_the_previous_pass(self):
        self.reconcile(self.adapters(review=rc.REVIEW_REPORT_PENDING))
        result = self.reconcile(self.adapters(task=rc.TASK_REPORT_BLOCKED,
                                              review=rc.REVIEW_REPORT_REJECT))
        self.assertTrue(result["changed"])
        changed = dict((f["kind"], f["detail"]) for f in result["findings"])
        self.assertEqual(changed[rc.FINDING_TASK_CHANGED],
                         "reported ACTIVE -> reported BLOCKED")
        self.assertEqual(changed[rc.FINDING_REVIEW_CHANGED],
                         "reported PENDING -> reported REJECT")
        gone = self.reconcile(self.adapters(task=rc.TASK_REPORT_BLOCKED))
        self.assertEqual(dict((f["kind"], f["detail"]) for f in gone["findings"])[
            rc.FINDING_REVIEW_CHANGED], "reported REJECT -> unavailable")

    def test_R1_candidate_drift_invalidates_derived_claims_without_touching_evidence(self):
        evidence_id = self.make_local_complete(self.mission_id)
        clean = self.reconcile(self.adapters(candidate=self.candidate(test_log=HEX_A)))
        self.assertNotIn(rc.FINDING_CANDIDATE_DRIFT, self.kinds(clean))
        before = self.state()
        authority = self.authority_bytes()
        drifted = self.reconcile(self.adapters(candidate=self.candidate(test_log="d" * 64)))
        finding = [f for f in drifted["findings"]
                   if f["kind"] == rc.FINDING_CANDIDATE_DRIFT]
        self.assertEqual(len(finding), 1)
        self.assertEqual(finding[0]["subject"], "test_log")
        self.assertIn("d" * 64, finding[0]["detail"])
        after = self.state()
        self.assertEqual(after["evidence"], before["evidence"])
        self.assertEqual(after["artifacts"], before["artifacts"])
        self.assertIsNone(after["evidence"][0]["invalidation"])
        self.assertEqual(self.authority_bytes(), authority)
        changed = sorted(k for k in after if after[k] != before.get(k))
        self.assertTrue(set(changed) <= DERIVED_KEYS, changed)
        report = self.observe(self.adapters(candidate=self.candidate(test_log="d" * 64)))
        item = [e for e in report["evidence"]["value"]
                if e["evidence_id"] == evidence_id][0]
        self.assertEqual(item["standing"], ob.STANDING_VERIFIED)
        self.assertTrue(item["drifted"])
        self.assertIn(ob.HOLD_CANDIDATE_DRIFTED, report["completion"]["holds"])
        self.assertFalse(report["completion"]["verified_success"])
        self.assertEqual(self.service.get_state(self.mission_id)["proof"]["satisfied"],
                         True)
        # An unavailable candidate on the next pass PRESERVES the drift
        # rather than erasing it (finding 4).
        unavailable = self.reconcile(self.adapters())
        self.assertIn(rc.FINDING_CANDIDATE_DRIFT, self.kinds(unavailable))
        self.assertEqual([f for f in unavailable["findings"]
                          if f["kind"] == rc.FINDING_CANDIDATE_DRIFT], finding)
        # Round-2 finding 5: a MATCHING report that is stale at the pass's
        # time cannot resolve it either, and the pass persists the drift.
        stale = self.reconcile(self.adapters(candidate=self.candidate(test_log=HEX_A),
                                             age=999999))
        self.assertIn(rc.FINDING_CANDIDATE_DRIFT, self.kinds(stale))
        self.assertIn(rc.FINDING_CANDIDATE_DRIFT,
                      [f["kind"] for f in self.state()["reconciliations"][-1]["findings"]])
        self.assertFalse(self.observe(self.adapters(
            candidate=self.candidate(test_log=HEX_A), age=999999))["completion"][
                "verified_success"])
        # Only a fresh report that names the key resolves it.
        resolved = self.reconcile(self.adapters(candidate=self.candidate(test_log=HEX_A)))
        self.assertNotIn(rc.FINDING_CANDIDATE_DRIFT, self.kinds(resolved))

    def test_R1_baseline_drift_is_measured_against_the_anchor_and_preserved(self):
        self.reconcile(self.adapters(candidate=self.candidate(baseline="1" * 64)))
        same = self.reconcile(self.adapters(candidate=self.candidate(baseline="1" * 64)))
        self.assertFalse(same["changed"])
        moved = self.reconcile(self.adapters(candidate=self.candidate(baseline="2" * 64)))
        self.assertIn(rc.FINDING_BASELINE_DRIFT, self.kinds(moved))
        detail = [f["detail"] for f in moved["findings"]
                  if f["kind"] == rc.FINDING_BASELINE_DRIFT][0]
        self.assertIn("1" * 64, detail)
        self.assertIn("2" * 64, detail)
        still = self.reconcile(self.adapters(candidate=self.candidate(baseline="2" * 64)))
        self.assertFalse(still["changed"])
        self.assertIn(rc.FINDING_BASELINE_DRIFT, self.kinds(still))
        unavailable = self.reconcile(self.adapters())
        self.assertIn(rc.FINDING_BASELINE_DRIFT, self.kinds(unavailable))
        unknown_baseline = self.reconcile(self.adapters(candidate=self.candidate(None)))
        self.assertIn(rc.FINDING_BASELINE_DRIFT, self.kinds(unknown_baseline))
        back = self.reconcile(self.adapters(candidate=self.candidate(baseline="1" * 64)))
        self.assertNotIn(rc.FINDING_BASELINE_DRIFT, self.kinds(back))

    def test_R1_missing_and_contradicted_proof_and_hard_blockers_are_detected(self):
        missing = self.reconcile(self.adapters())
        self.assertEqual([f for f in missing["findings"]
                          if f["kind"] == rc.FINDING_PROOF_MISSING],
                         [{"kind": rc.FINDING_PROOF_MISSING, "subject": "tests_pass",
                           "detail": self.mp.REQUIREMENT_MISSING}])
        first = self.call("submit_evidence", self.mission_id, "tests_pass",
                          "VERIFICATION_RECORD", "e" * 64, [self.artifact["artifact_id"]])
        second = self.call("submit_evidence", self.mission_id, "tests_pass",
                           "VERIFICATION_RECORD", "f" * 64, [self.artifact["artifact_id"]])
        self.call("accept_evidence", self.mission_id, first["evidence_id"], "e" * 64,
                  context=self.other)
        self.call("accept_evidence", self.mission_id, second["evidence_id"], "f" * 64,
                  context=self.other)
        blocker = self.call("open_blocker", self.mission_id, "disk_full", "no space")
        result = self.reconcile(self.adapters())
        kinds = dict((f["kind"], f) for f in result["findings"])
        self.assertEqual(kinds[rc.FINDING_PROOF_CONTRADICTED]["subject"], "tests_pass")
        self.assertEqual(kinds[rc.FINDING_PROOF_CONTRADICTED]["detail"],
                         self.mp.REQUIREMENT_CONTRADICTED)
        self.assertEqual(kinds[rc.FINDING_HARD_BLOCKER]["subject"], blocker["blocker_id"])
        self.assertNotIn(rc.FINDING_PROOF_MISSING, kinds)

    def test_R1_reported_completion_and_review_are_findings_never_closures(self):
        loud = self.adapters(task=rc.TASK_REPORT_COMPLETE, review=rc.REVIEW_REPORT_APPROVE)
        result = self.reconcile(loud)
        self.assertIn(rc.FINDING_REPORTED_COMPLETE_UNVERIFIED, self.kinds(result))
        self.assertNotIn(rc.FINDING_REVIEW_NOT_APPROVED, self.kinds(result))
        self.assertIsNone(self.state()["closure"])
        self.assertEqual(self.state()["progress"], ms.PROGRESS_IN_PROGRESS)
        unreviewed = self.reconcile(self.adapters(task=rc.TASK_REPORT_COMPLETE,
                                                  review=rc.REVIEW_REPORT_PENDING))
        self.assertIn(rc.FINDING_REVIEW_NOT_APPROVED, self.kinds(unreviewed))
        detail = [f["detail"] for f in unreviewed["findings"]
                  if f["kind"] == rc.FINDING_REVIEW_NOT_APPROVED][0]
        self.assertIn("reported PENDING", detail)
        self.assertFalse(self.observe(loud)["completion"]["verified_success"])

    def test_R1_receipt_reports_are_reflected_as_the_callers_report_bound_to_the_receipt(self):
        # Round-2 finding 8 / round-4 finding 4. What the package CAN do: bind
        # a delivery report to a recorded receipt-reference artifact by id,
        # reference and content digest, and reflect the caller's judgement
        # as a REPORT. An arbitrary reference recorded without a digest and
        # a matching VALID report is UNBOUND.
        arbitrary = self.call("record_artifact", self.mission_id, "pr_receipt",
                              mission_record.ARTIFACT_ROLE_PRODUCED,
                              ms.LOCATOR_KIND_DELIVERY_RECEIPT_REFERENCE,
                              "not-a-validated-receipt", None, True, [])["artifact_id"]
        fabricated = self.reconcile(self.adapters(delivery={
            "status": rc.DELIVERY_REPORT_VALID, "receipt_artifact_id": arbitrary,
            "locator": "not-a-validated-receipt", "receipt_digest_sha256": "9" * 64}))
        self.assertIn(rc.FINDING_DELIVERY_UNBOUND, self.kinds(fabricated))
        self.assertNotIn(rc.FINDING_DELIVERY_VALID, self.kinds(fabricated))
        # The finding names say what they are: the CALLER's report, never a
        # receipt this package verified.
        self.assertEqual(rc.FINDING_DELIVERY_VALID, "delivery_reported_valid")
        self.assertEqual(rc.FINDING_DELIVERY_INVALID, "delivery_reported_invalid")
        for kind in rc.FINDING_KINDS:
            if kind.startswith("delivery"):
                self.assertNotIn("verified", kind)
                self.assertNotIn("receipt_valid", kind)
        self.assertIn("stated limit", rc.__doc__.lower())
        self.assertIn("CANNOT do is establish", rc.__doc__)
        # A receipt the EXISTING contract accepts, recorded by its own
        # content digest THROUGH THE GENERIC record_artifact path: the
        # caller's VALID report binds to that reference, but (Task 7,
        # Stage 2, condition 5) the reference was never attested by the
        # validating path, so the report is reflected as an UNVERIFIED
        # claim — ``delivery_report_unattested`` — never as the positive
        # finding. The positive finding exists only for an attested
        # reference (the next test).
        receipt = self.existing_receipt()
        delivery_contract.validate_receipt(receipt, receipt["step"], self.DELIVERY_ID,
                                           self.AUTHORITY_DIGEST, "receipt")
        recorded = self.record_validated_receipt(receipt)
        judged = self.validator_report(receipt, recorded)
        self.assertEqual(judged["status"], rc.DELIVERY_REPORT_VALID)
        before = self.state()
        valid = self.reconcile(self.adapters(delivery=judged))
        self.assertNotIn(rc.FINDING_DELIVERY_VALID, self.kinds(valid))
        self.assertNotIn(rc.FINDING_DELIVERY_ATTESTED, self.kinds(valid))
        bound = [f for f in valid["findings"]
                 if f["kind"] == rc.FINDING_DELIVERY_UNATTESTED]
        self.assertEqual(len(bound), 1)
        self.assertEqual(bound[0]["subject"], recorded)
        self.assertIn("no attestation", bound[0]["detail"])
        # The detail names the artifact and the receipt digest, never the
        # locator (round-4 finding 6).
        self.assertIn(receipt["receipt_digest_sha256"], bound[0]["detail"])
        self.assertNotIn(receipt["receipt_id"], bound[0]["detail"])
        after = self.state()
        self.assertEqual(after["artifacts"], before["artifacts"])
        self.assertEqual(after["evidence"], before["evidence"])
        self.assertIsNone(after["closure"])
        # The same receipt tampered after recording: the existing contract
        # refuses it, the materialized judgement is INVALID, and the
        # report still binds to the receipt it judged.
        tampered = copy.deepcopy(receipt)
        tampered["binding"]["head_sha"] = "e" * 40
        with self.assertRaises(Exception):
            delivery_contract.validate_receipt(tampered, tampered["step"],
                                               self.DELIVERY_ID, self.AUTHORITY_DIGEST,
                                               "receipt")
        invalid = self.reconcile(self.adapters(delivery=self.validator_report(
            tampered, recorded)))
        bound = [f for f in invalid["findings"] if f["kind"] == rc.FINDING_DELIVERY_INVALID]
        self.assertEqual([f["subject"] for f in bound], [recorded])
        # The Reviewer's exact bypass (round-4 finding 4), now CLOSED: a
        # fabricated pair — an arbitrary reference recorded WITH an
        # arbitrary digest through the generic path, and a VALID report
        # naming that same digest — still binds, but is reflected as an
        # unverified claim (``delivery_report_unattested``), never as
        # ``delivery_reported_valid``: the record holds no attestation of
        # that reference by the validating path, and no caller-supplied
        # digest or report can create one. Observation keeps the delivery
        # fact ``reported`` and says the bound reference is not attested.
        forged = self.call("record_artifact", self.mission_id, "forged_receipt",
                           mission_record.ARTIFACT_ROLE_PRODUCED,
                           ms.LOCATOR_KIND_DELIVERY_RECEIPT_REFERENCE,
                           "not-a-validated-receipt", "9" * 64, True, [])["artifact_id"]
        bypass = self.reconcile(self.adapters(delivery={
            "status": rc.DELIVERY_REPORT_VALID, "receipt_artifact_id": forged,
            "locator": "not-a-validated-receipt", "receipt_digest_sha256": "9" * 64}))
        self.assertNotIn(rc.FINDING_DELIVERY_VALID, self.kinds(bypass))
        self.assertNotIn(rc.FINDING_DELIVERY_ATTESTED, self.kinds(bypass))
        reflected = [f for f in bypass["findings"]
                     if f["kind"] == rc.FINDING_DELIVERY_UNATTESTED]
        self.assertEqual([f["subject"] for f in reflected], [forged])
        self.assertIn("unverified source claim", reflected[0]["detail"])
        self.assertEqual(reflected[0]["kind"], "delivery_report_unattested")
        report = self.observe(self.adapters(delivery={
            "status": rc.DELIVERY_REPORT_VALID, "receipt_artifact_id": forged,
            "locator": "not-a-validated-receipt", "receipt_digest_sha256": "9" * 64}))
        self.assertEqual(report["delivery"]["standing"], ob.STANDING_REPORTED)
        self.assertNotEqual(report["delivery"]["standing"], ob.STANDING_VERIFIED)
        self.assertEqual(report["delivery_receipts"]["value"]["report_bound_to"], forged)
        self.assertIs(report["delivery_receipts"]["value"]["report_bound_attested"], False)
        self.assertEqual(report["delivery_receipts"]["value"]["attested"], [])
        self.assertEqual(report["delivery_receipts"]["value"]["effects_completed"], [])
        self.assertEqual(self.receipts_by_attestation()["attested"], [])
        # A DIFFERENT accepted receipt whose digest is not the recorded one
        # is unbound: the binding is by the receipt actually named.
        other = self.existing_receipt(head_sha="f" * 40)
        unbound = self.reconcile(self.adapters(delivery=self.validator_report(
            other, recorded, locator="receipt:" + receipt["receipt_id"])))
        self.assertIn(rc.FINDING_DELIVERY_UNBOUND, self.kinds(unbound))
        # Wrong locator, or another artifact id, is unbound too.
        wrong = self.reconcile(self.adapters(delivery=dict(judged, locator="receipt:43")))
        self.assertIn(rc.FINDING_DELIVERY_UNBOUND, self.kinds(wrong))
        another = self.reconcile(self.adapters(delivery=dict(
            judged, receipt_artifact_id=self.artifact["artifact_id"],
            locator="opaque:log")))
        self.assertIn(rc.FINDING_DELIVERY_UNBOUND, self.kinds(another))
        # AMBIGUOUS and ABSENT as the validator would report them.
        ambiguous = self.reconcile(self.adapters(delivery=dict(
            judged, status=rc.DELIVERY_REPORT_AMBIGUOUS)))
        self.assertEqual([f["subject"] for f in ambiguous["findings"]
                          if f["kind"] == rc.FINDING_DELIVERY_AMBIGUOUS], [recorded])
        absent = self.reconcile(self.adapters(delivery=self.delivery("ABSENT")))
        self.assertIn(rc.FINDING_DELIVERY_ABSENT, self.kinds(absent))
        # A bound report that is stale at the pass's own time is stale, and
        # its detail carries no timestamp.
        aged = self.reconcile(self.adapters(delivery=judged,
                                            age=rc.REPORTED_FRESHNESS_BOUND_SECONDS + 1))
        stale = [f for f in aged["findings"] if f["kind"] == rc.FINDING_DELIVERY_STALE]
        self.assertEqual(len(stale), 1)
        self.assertNotIn(str(self.clock()), stale[0]["detail"])
        # The observation binds and ages the live report the same way.
        report = self.observe(self.adapters(delivery=judged))
        self.assertEqual(report["delivery_receipts"]["value"],
                         {"recorded": [arbitrary, recorded, forged],
                          "attested": [], "unattested": [arbitrary, recorded, forged],
                          "effects_completed": [],
                          "report_bound_to": recorded, "report_bound_attested": False,
                          "report_fresh": True})
        report = self.observe(self.adapters(delivery=judged, age=999999))
        self.assertEqual(report["delivery_receipts"]["value"]["report_fresh"], False)
        self.assertEqual(report["delivery"]["freshness"], ob.FRESHNESS_STALE)
        self.assertEqual(len(self.receipts()), 3)

    def test_R1_findings_are_constructible_for_every_locator_the_schema_permits(self):
        # Round-4 finding 6: a receipt reference at MAX_LOCATOR_CHARS (2048)
        # must never produce a finding detail over MAX_FINDING_DETAIL_CHARS
        # (500). No finding embeds a locator; every detail is bounded for
        # every schema-valid input, and the record persists and reloads.
        self.assertGreater(ms.MAX_LOCATOR_CHARS, rc.MAX_FINDING_DETAIL_CHARS)
        existing = self.existing_receipt()
        longest = "r" * ms.MAX_LOCATOR_CHARS
        recorded = self.record_receipt(locator=longest,
                                       digest=existing["receipt_digest_sha256"])
        for label, report, expected in (
            # Task 7, Stage 2: a VALID report bound to a reference the record
            # never attested is an unverified claim, not a positive finding.
            ("bound valid, unattested",
             self.validator_report(existing, recorded, locator=longest),
             rc.FINDING_DELIVERY_UNATTESTED),
            ("bound invalid", dict(self.validator_report(existing, recorded,
                                                         locator=longest),
                                   status="INVALID"), rc.FINDING_DELIVERY_INVALID),
            ("bound ambiguous", dict(self.validator_report(existing, recorded,
                                                           locator=longest),
                                     status="AMBIGUOUS"), rc.FINDING_DELIVERY_AMBIGUOUS),
            ("unbound long locator", {"status": "VALID", "receipt_artifact_id": recorded,
                                      "locator": longest[:-1] + "x",
                                      "receipt_digest_sha256": "9" * 64},
             rc.FINDING_DELIVERY_UNBOUND),
            ("stale long locator", None, rc.FINDING_DELIVERY_STALE),
        ):
            with self.subTest(label):
                if report is None:
                    adapters = self.adapters(delivery=self.validator_report(
                        existing, recorded, locator=longest), age=999999)
                else:
                    adapters = self.adapters(delivery=report)
                result = self.reconcile(adapters)
                self.assertTrue(result["changed"])
                # The expected delivery finding EXISTS, exactly once, on the
                # recorded artifact; only then are its bounds checked.
                delivery = [f for f in result["findings"]
                            if f["kind"].startswith("delivery")]
                self.assertEqual([f["kind"] for f in delivery], [expected])
                self.assertEqual(delivery[0]["subject"], recorded)
                self.assertTrue(result["findings"])
                for finding in result["findings"]:
                    self.assertLessEqual(len(finding["detail"]), rc.MAX_FINDING_DETAIL_CHARS)
                    self.assertNotIn(longest[:100], finding["detail"])
                self.assertEqual(self.stable(), self.document())
        restarted = self.service.__class__(mst.MissionStore(self.directory), self.clock)
        self.assertEqual(restarted.reload_supported_state(self.mission_id)["source"],
                         mj.SOURCE_SNAPSHOT)

    def test_R1_revision_drift_is_detected_and_the_snapshot_is_repaired(self):
        self.reconcile(self.adapters())
        self.edit(self.mission_id, 1, proof_contract=contract(required_dependencies=[]))
        stale = self.service.reload_supported_state(self.mission_id)
        self.assertEqual(stale["source"], mj.SOURCE_REPLAY)
        self.assertEqual(stale["snapshot_problem"], mj.PROBLEM_SNAPSHOT_STALE)
        # The position is no longer current: the revision moved.
        self.assertFalse(rc.position_view(self.state(), 2)["current"])
        self.assertFalse(self.observe()["reconciliation"]["current"])
        result = self.reconcile(self.adapters())
        self.assertTrue(result["changed"])
        self.assertEqual(result["observed_revision"], 2)
        drift = [f for f in result["findings"] if f["kind"] == rc.FINDING_REVISION_DRIFT]
        self.assertEqual(drift, [{"kind": rc.FINDING_REVISION_DRIFT, "subject": None,
                                  "detail": "the active contract binds revision 1 but"
                                            " the mission is at revision 2"}])
        repaired = self.service.reload_supported_state(self.mission_id)
        self.assertEqual(repaired["source"], mj.SOURCE_SNAPSHOT)
        self.assertEqual(repaired["cursor"]["revision"], 2)
        self.assertEqual(self.state()["snapshot"]["revision"], 2)
        self.assertEqual(self.state()["applied_operations"][-1]["provenance"]["revision"], 2)
        self.assertTrue(self.observe()["reconciliation"]["current"])
        self.assertEqual(self.state()["contract_activations"][-1]["revision"], 1)
        self.assertEqual(self.service.get(self.mission_id)["record"]["state"],
                         mission_record.STATE_AWAITING_DECISION)
        self.approve(self.mission_id, 2)
        self.call("activate_proof_contract", self.mission_id)
        cleared = self.reconcile(self.adapters())
        self.assertTrue(cleared["changed"])
        self.assertNotIn(rc.FINDING_REVISION_DRIFT, self.kinds(cleared))


# ====================================================================
# R2. Repairs only supported derived state
# ====================================================================


class R2DerivedOnlyTests(ReconciliationFixture):

    def test_R2_a_meaningful_pass_moves_only_the_derived_keys(self):
        self.make_local_complete(self.mission_id)
        receipt = self.existing_receipt()
        recorded = self.record_validated_receipt(receipt)
        self.clock.advance(1)
        authority = self.authority_bytes()
        result = self.reconcile(self.adapters(
            review=rc.REVIEW_REPORT_APPROVE,
            delivery=self.validator_report(receipt, recorded)))
        before_document = json.loads(self.before)  # after the mint, before the pass
        before = before_document["mission_state"][self.mission_id]
        after_document = self.document()
        after = after_document["mission_state"][self.mission_id]
        changed = sorted(k for k in after if after[k] != before.get(k))
        self.assertEqual(set(changed), DERIVED_KEYS)
        self.assertEqual(after["sequence"], before["sequence"] + 1)
        self.assertEqual(len(after["applied_operations"]),
                         len(before["applied_operations"]) + 1)
        self.assertEqual(after["reconciliations"], [after["reconciliations"][-1]])
        self.assertEqual(after["snapshot"]["position"], before["snapshot"]["position"] + 1)
        self.assertEqual(after["snapshot"]["supported_state"],
                         before["snapshot"]["supported_state"])
        self.assertEqual(after_document["missions"], before_document["missions"])
        self.assertEqual(self.authority_bytes(), authority)
        reservations_before = before_document["reservations"]
        reservations_after = after_document["reservations"]
        self.assertEqual(set(reservations_after), set(reservations_before))
        moved = [k for k in reservations_after
                 if reservations_after[k] != reservations_before[k]]
        self.assertEqual(moved, [result["operation_id"]])
        entry = after["applied_operations"][-1]
        self.assertEqual(sorted(entry["outcome"]), sorted(
            ms.OUTCOME_COMMON_KEYS + ms.OUTCOME_KEYS_BY_KIND[ms.OPERATION_RECONCILE]))
        self.assertNotIn(ms.OPERATION_RECONCILE, ms.CONTRACT_DEPENDENT_KINDS)
        self.assertEqual(entry["outcome"]["progress"], ms.PROGRESS_IN_PROGRESS)
        record = after["reconciliations"][-1]
        # The invocation digest binds the sources AND their provenance.
        self.assertEqual(entry["content_digest_sha256"], ms.invocation_digest(
            ms.OPERATION_RECONCILE, self.mission_id, before["sequence"],
            {"sources": record["sources"],
             "source_provenance": record["source_provenance"]}))
        self.assertIn(rc.FINDING_DELIVERY_UNATTESTED,
                      [f["kind"] for f in record["findings"]])
        self.assertNotIn(rc.FINDING_DELIVERY_VALID, [f["kind"] for f in record["findings"]])
        self.assertEqual(self.stable(), after_document)

    def test_R2_authority_budget_proof_requirements_and_identity_never_move(self):
        mission_before = self.service.get(self.mission_id)
        for adapters in (self.adapters(task=rc.TASK_REPORT_COMPLETE,
                                       review=rc.REVIEW_REPORT_APPROVE,
                                       delivery=self.delivery("VALID")),
                         self.adapters(task=rc.TASK_REPORT_FAILED),
                         self.adapters(candidate=self.candidate(test_log="d" * 64))):
            self.reconcile(adapters)
        mission_after = self.service.get(self.mission_id)
        self.assertEqual(mission_after, mission_before)
        state = self.state()
        self.assertEqual(state["progress"], ms.PROGRESS_IN_PROGRESS)
        self.assertIsNone(state["closure"])
        self.assertEqual(len(state["contract_activations"]), 1)
        self.assertEqual(len(state["continuations"]), 0)
        self.assertEqual(self.service.get_state(self.mission_id)["budget"],
                         {"attempts_consumed": 0, "attempts_remaining": 3,
                          "checkpoints_consumed": 0, "checkpoints_remaining": 8})
        self.assertEqual(len(state["reconciliations"]), 3)
        self.assertIsNone(self.ma.reconcile_registry(self.store.load()))


# ====================================================================
# R3. No speculative effects
# ====================================================================


class R3EffectFreedomTests(ReconciliationFixture):

    def test_R3_module_is_pure_and_non_invoking_by_construction(self):
        source, tree, roots, imported, calls, identifiers = _module_facts(
            "mission/reconciliation.py")
        self.assertEqual(roots, {"mission"})
        self.assertEqual(imported, {"journal", "progress", "record", "state"})
        for forbidden in FORBIDDEN_CALLS:
            self.assertNotIn(forbidden, calls, forbidden)
        for word in FORBIDDEN_NAMES + ("observation", "inputs", "reports"):
            self.assertNotIn(word, identifiers, word)
        for word in ("issue_mission_authorization", "apply_human_decision",
                     "atomic_write_json", "exclusive_store_lock"):
            self.assertNotIn(word, source, word)
        self.assertGreaterEqual(ni.check_module(tree, "mission/reconciliation.py"), 25)

    def test_R3_service_method_writes_only_through_apply(self):
        tree = ast.parse((REPO_ROOT / "mission" / "state_service.py").read_text())
        calls = {getattr(n.func, "id", getattr(n.func, "attr", None))
                 for n in _function_calls(tree, "reconcile")}
        for forbidden in ("lock", "save", "open", "apply_human_decision",
                          "issue_mission_authorization", "atomic_write_json",
                          "_reserve", "mint_state_operation_id", "sleep", "collect",
                          "callable"):
            self.assertNotIn(forbidden, calls, forbidden)
        self.assertIn("_apply", calls)
        self.assertIn("normalize_inputs", calls)
        self.assertIn("load", calls)
        source = (REPO_ROOT / "mission" / "state_service.py").read_text()
        for word in ("dispatch", "route", "schedule", "spawn", "subprocess", "fetch",
                     "publish", "deploy", "merge", "release"):
            self.assertNotIn(word, source.lower(), word)

    def test_R3_missing_invalid_ambiguous_unbound_and_stale_receipts_cause_nothing(self):
        minted = []

        def counting_mint(prefix):
            minted.append(prefix)
            return mission_record.mint_id(prefix)

        service = self.service.__class__(mst.MissionStore(self.directory), self.clock,
                                         counting_mint)
        existing = self.existing_receipt()
        receipt = self.record_validated_receipt(existing)
        judged = self.validator_report(existing, receipt)
        listing = sorted(os.listdir(self.directory))
        authority = self.authority_bytes()
        cases = (
            ("invalid", dict(judged, status="INVALID"), 0),
            ("ambiguous", dict(judged, status="AMBIGUOUS"), 0),
            ("absent", self.delivery("ABSENT"), 0),
            ("unbound", self.delivery("VALID"), 0),
            ("stale valid", judged, 999999),
            ("none", None, 0),
        )
        for label, report, age in cases:
            with self.subTest(label):
                adapters = self.adapters(delivery=report, age=age)
                inputs = materialize(adapters, self.mission_id, self.head())
                operation_id = self.service.mint_state_operation_id(self.context)
                result = service.reconcile(self.mission_id, operation_id,
                                           self.seq(self.mission_id), inputs,
                                           self.context)
                for adapter in adapters.values():
                    self.assertEqual(len(adapter.calls), 1)
                self.assertEqual(minted, [])
                self.assertEqual(sorted(os.listdir(self.directory)), listing)
                self.assertEqual(self.authority_bytes(), authority)
                self.assertNotIn(rc.FINDING_DELIVERY_VALID, self.kinds(result))
                if result["changed"]:
                    self.assertEqual(self.ledger_kinds()[-1], ms.OPERATION_RECONCILE)
        self.assertIsNone(self.state()["closure"])
        self.assertEqual(len(self.receipts()), 1)


# ====================================================================
# R4. Idempotent on unchanged meaning; no storms
# ====================================================================


class R4IdempotencyTests(ReconciliationFixture):

    def test_R4_unchanged_meaning_writes_literally_nothing(self):
        first = self.reconcile(self.adapters(review=rc.REVIEW_REPORT_PENDING,
                                             candidate=self.candidate(test_log=HEX_A)))
        self.assertTrue(first["changed"])
        events = len(self.ledger_kinds())
        for _ in range(5):
            again = self.reconcile(self.adapters(review=rc.REVIEW_REPORT_PENDING,
                                                 candidate=self.candidate(test_log=HEX_A)))
            self.assertFalse(again["changed"])
            self.assertFalse(again["idempotent"])
            self.assertEqual(self.read_bytes(), self.before)
            self.assertEqual(again["findings"], first["findings"])
            self.assertEqual(again["sequence"], first["sequence"])
            self.assertEqual(again["cursor"], self.head())
        self.assertEqual(len(self.ledger_kinds()), events)
        self.assertEqual(len(self.state()["reconciliations"]), 1)
        reservations = self.document()["reservations"]
        self.assertIsNone(reservations[again["operation_id"]]["consumed_by"])
        record_ = self.state()
        self.assertEqual(record_["updated_at"],
                         record_["applied_operations"][-1]["applied_at"])
        self.assertEqual(record_["snapshot"]["position"], first["sequence"])

    def test_R4_timestamp_only_refresh_is_not_a_meaningful_change(self):
        first = self.reconcile(self.adapters(review=rc.REVIEW_REPORT_PENDING))
        self.clock.advance(ob.REPORTED_FRESHNESS_BOUND_SECONDS * 10)
        old = self.adapters(review=rc.REVIEW_REPORT_PENDING, age=10 ** 6)
        refreshed = self.adapters(review=rc.REVIEW_REPORT_PENDING)
        self.assertEqual(self.observe(old)["task"]["freshness"], ob.FRESHNESS_STALE)
        self.assertEqual(self.observe(refreshed)["task"]["freshness"], ob.FRESHNESS_FRESH)
        for adapters in (old, refreshed):
            result = self.reconcile(adapters)
            self.assertFalse(result["changed"])
            self.assertEqual(self.read_bytes(), self.before)
        self.assertEqual(len(self.state()["reconciliations"]), 1)
        self.assertEqual(self.state()["reconciliations"][0]["sequence"], first["sequence"])
        # Provenance travels beside the sources and never decides.
        self.assertEqual(set(self.state()["reconciliations"][0]["sources"]["review"]),
                         {"standing", "value"})
        self.assertEqual(set(self.state()["reconciliations"][0]["source_provenance"][
            "review"]), {"observed_at"})

    def test_R4_meaning_changes_produce_exactly_one_event_each(self):
        self.reconcile(self.adapters())
        for task in (rc.TASK_REPORT_BLOCKED, rc.TASK_REPORT_BLOCKED, rc.TASK_REPORT_ACTIVE,
                     rc.TASK_REPORT_ACTIVE):
            self.reconcile(self.adapters(task=task))
        self.assertEqual(self.ledger_kinds().count(ms.OPERATION_RECONCILE), 3)
        self.assertEqual([r["sources"]["task"]["value"]
                          for r in self.state()["reconciliations"]],
                         ["ACTIVE", "BLOCKED", "ACTIVE"])
        self.reconcile({})
        self.assertEqual(self.state()["reconciliations"][-1]["sources"]["task"],
                         {"standing": rc.STANDING_UNAVAILABLE, "value": None})

    def test_R4_stale_delivery_reports_do_not_storm_and_finding_identity_is_semantic(self):
        # Round-2 finding 7, exactly: an unchanged, already-stale delivery
        # report with one-second clock advances. One event, then no-ops.
        existing = self.existing_receipt()
        receipt = self.record_validated_receipt(existing)
        stale = self.adapters(delivery=self.validator_report(existing, receipt),
                              age=999999)
        outcomes = []
        for _ in range(3):
            outcomes.append(self.reconcile(stale)["changed"])
            self.assertEqual(self.read_bytes() == self.before, not outcomes[-1])
            self.clock.advance(1)
        self.assertEqual(outcomes, [True, False, False])
        record = self.state()["reconciliations"][-1]
        kinds = [f["kind"] for f in record["findings"]]
        self.assertIn(rc.FINDING_DELIVERY_STALE, kinds)
        # Semantic identity carries no timestamp; the pass's time lives on
        # the record and in the provenance, both stored, both outside the
        # change comparison, and the record recomputes to itself on
        # reload (its own stored time is the evaluation time).
        self.assertTrue(record["findings"])
        for finding in record["findings"]:
            for token in (str(record["reconciled_at"]), str(self.clock()),
                          str(record["source_provenance"]["delivery"]["observed_at"])):
                self.assertNotIn(token, finding["detail"])
        self.assertEqual(record["source_provenance"]["delivery"]["observed_at"],
                         stale["delivery"].answer["observed_at"])
        self.assertEqual(self.stable(), self.document())
        restarted = self.service.__class__(mst.MissionStore(self.directory), self.clock)
        self.assertEqual(restarted.observe(self.mission_id, self.inputs(stale))[
            "reconciliation"]["findings"], record["findings"])
        # A timestamp-only refresh of the same stale report is a no-op too,
        # and so is a fresh report of the same judgement after it has
        # been recorded once.
        refreshed = self.adapters(delivery=self.validator_report(existing, receipt),
                                  age=999998)
        self.assertFalse(self.reconcile(refreshed)["changed"])
        self.assertEqual(self.read_bytes(), self.before)
        fresh = self.adapters(delivery=self.validator_report(existing, receipt))
        self.assertTrue(self.reconcile(fresh)["changed"])
        for _ in range(3):
            self.clock.advance(1)
            self.assertFalse(self.reconcile(self.adapters(
                delivery=self.validator_report(existing, receipt)))["changed"])
            self.assertEqual(self.read_bytes(), self.before)
        self.assertEqual(self.ledger_kinds().count(ms.OPERATION_RECONCILE), 2)

    def test_R4_one_pass_converges_after_accepted_evidence_crosses_its_age_bound(self):
        # Finding 6's reproduction: the first-ever pass after expiry.
        self.make_local_complete(self.mission_id)
        self.clock.advance(3601)
        outcomes = [self.reconcile(self.adapters()) for _ in range(3)]
        self.assertEqual([r["changed"] for r in outcomes], [True, False, False])
        self.assertEqual([f["detail"] for f in outcomes[0]["findings"]
                          if f["kind"] == rc.FINDING_PROOF_MISSING],
                         [self.mp.REQUIREMENT_STALE])
        self.assertEqual(self.ledger_kinds().count(ms.OPERATION_RECONCILE), 1)
        record = self.state()["reconciliations"][-1]
        self.assertEqual(record["reconciled_at"], self.clock())
        self.assertEqual(self.stable(), self.document())
        # Crossing the bound BETWEEN passes is exactly one event; before it,
        # a pass at the same meaning is a no-op.
        other = self.ready_mission(required_dependencies=[])
        self.make_local_complete(other)
        first = self.reconcile(self.adapters(), mission_id=other)
        self.assertNotIn(rc.FINDING_PROOF_MISSING, self.kinds(first))
        self.clock.advance(3000)
        self.assertFalse(self.reconcile(self.adapters(), mission_id=other)["changed"])
        self.clock.advance(601)
        crossed = self.reconcile(self.adapters(), mission_id=other)
        self.assertTrue(crossed["changed"])
        self.assertIn(rc.FINDING_PROOF_MISSING, self.kinds(crossed))
        for _ in range(3):
            self.assertFalse(self.reconcile(self.adapters(), mission_id=other)["changed"])
            self.assertEqual(self.read_bytes(), self.before)
        self.assertEqual(self.ledger_kinds(other).count(ms.OPERATION_RECONCILE), 2)


# ====================================================================
# R5. Stale writes and conflicts
# ====================================================================


class R5ConflictTests(ReconciliationFixture):

    def test_R5_stale_sequence_refuses_without_mutation_on_both_paths(self):
        self.reconcile(self.adapters())
        sequence = self.seq(self.mission_id)
        for stale in (sequence - 1, sequence + 1, 0):
            with self.subTest(("changed", stale)):
                self.assertRefuses(mss.PROBLEM_STALE_SEQUENCE, self.reconcile,
                                   self.adapters(task=rc.TASK_REPORT_BLOCKED),
                                   expected_sequence=stale)
                self.assertEqual(self.read_bytes(), self.before)
            with self.subTest(("unchanged", stale)):
                # Finding 2: the no-op path validates the precondition too.
                self.assertRefuses(mss.PROBLEM_STALE_SEQUENCE, self.reconcile,
                                   self.adapters(), expected_sequence=stale)
                self.assertEqual(self.read_bytes(), self.before)
        self.assertFalse(self.reconcile(self.adapters(),
                                        expected_sequence=sequence)["changed"])
        a_id = self.oid()
        b_id = self.oid()
        winner = self.service.reconcile(
            self.mission_id, a_id, sequence,
            self.inputs(self.adapters(task=rc.TASK_REPORT_BLOCKED)), self.context)
        self.assertTrue(winner["changed"])
        before = self.read_bytes()
        self.assertRefuses(mss.PROBLEM_STALE_SEQUENCE, self.service.reconcile,
                           self.mission_id, b_id, sequence,
                           self.inputs(self.adapters(task=rc.TASK_REPORT_FAILED)),
                           self.context)
        self.assertEqual(self.read_bytes(), before)
        self.assertIsNone(self.document()["reservations"][b_id]["consumed_by"])

    def test_R5_reports_collected_at_a_cursor_the_document_left_refuse(self):
        self.reconcile(self.adapters())
        sequence = self.seq(self.mission_id)
        # An event interleaved during collection: the sequence refuses
        # first, and with a matching sequence the cursor still refuses.
        collected = self.inputs(self.adapters(task=rc.TASK_REPORT_FAILED))
        self.call("record_claim", self.mission_id, "tests_pass", "meanwhile")
        self.assertRefuses(mss.PROBLEM_STALE_SEQUENCE, self.reconcile, None,
                           inputs=collected, expected_sequence=sequence)
        exc = self.assertRefuses(rc.PROBLEM_RECONCILIATION_MOVED, self.reconcile, None,
                                 inputs=collected, expected_sequence=sequence + 1)
        self.assertIn("position", str(exc))
        self.assertEqual(self.read_bytes(), self.before)
        # Finding 3: an EDIT interleaved between collection and the pass.
        collected = self.inputs(self.adapters(task=rc.TASK_REPORT_BLOCKED))
        self.assertEqual(collected["cursor"]["revision"], 1)
        self.edit(self.mission_id, 1, proof_contract=contract(required_dependencies=[]))
        sequence = self.seq(self.mission_id)
        exc = self.assertRefuses(rc.PROBLEM_RECONCILIATION_MOVED, self.reconcile,
                                 None, inputs=collected, expected_sequence=sequence)
        self.assertIn("revision", str(exc))
        self.assertEqual(self.read_bytes(), self.before)
        # The same on the no-op path: unchanged sources, moved cursor.
        stale_noop = self.inputs(self.adapters())
        stale_noop["cursor"] = dict(stale_noop["cursor"], revision=1)
        self.assertRefuses(rc.PROBLEM_RECONCILIATION_MOVED, self.reconcile, None,
                           inputs=stale_noop, expected_sequence=sequence)
        self.assertEqual(self.read_bytes(), self.before)
        # Nothing was persisted against revision 2 from reports that saw 1.
        self.assertEqual([r["observed_revision"] for r in self.state()["reconciliations"]],
                         [1])
        # Re-observed at the real head, the pass records under revision 2.
        result = self.reconcile(self.adapters(task=rc.TASK_REPORT_FAILED))
        self.assertTrue(result["changed"])
        self.assertEqual(result["observed_revision"], 2)
        self.assertIn(rc.FINDING_REVISION_DRIFT, self.kinds(result))

    def test_R5_unchanged_inside_the_lock_refuses_without_mutation(self):
        self.reconcile(self.adapters())
        real_plan = rc.plan
        outcomes = iter([True, False])

        def doctored(*args):
            return dict(real_plan(*args), changed=next(outcomes))

        mss.reconciliation.plan = doctored
        try:
            self.assertRefuses(rc.PROBLEM_RECONCILIATION_UNCHANGED, self.reconcile,
                               self.adapters())
        finally:
            mss.reconciliation.plan = real_plan
        self.assertEqual(self.read_bytes(), self.before)
        self.assertEqual(len(self.state()["reconciliations"]), 1)

    def test_R5_terminal_is_terminal_and_still_observable(self):
        self.make_local_complete(self.mission_id)
        self.call("complete_successfully", self.mission_id, "done")
        self.assertRefuses(ms.PROBLEM_PROGRESS_TERMINAL, self.reconcile,
                           self.adapters(task=rc.TASK_REPORT_FAILED))
        self.assertEqual(self.read_bytes(), self.before)
        report = self.observe(self.adapters(task=rc.TASK_REPORT_FAILED))
        self.assertTrue(report["completion"]["closure_verified"])
        self.assertTrue(report["completion"]["contradicted"])

    def test_R5_bad_input_refuses_before_anything_is_read(self):
        inputs = self.inputs(self.adapters())
        self.assertRefuses(mission_record.PROBLEM_ID_GRAMMAR, self.service.reconcile,
                           self.mission_id, "mo-x", 0, inputs, self.context)
        # Exact types first: a bool sequence and a dict context refuse at
        # the seam before any validator that could dispatch reads them.
        self.assertRefuses(ob.PROBLEM_OBSERVATION_INPUT, self.service.reconcile,
                           self.mission_id, self.oid(), True, inputs, self.context)
        self.assertRefuses(ob.PROBLEM_OBSERVATION_INPUT, self.service.reconcile,
                           self.mission_id, self.oid(), 0, inputs, {"transport": "x"})
        self.assertRefuses(ob.PROBLEM_OBSERVATION_INPUT, self.service.reconcile,
                           self.mission_id, self.oid(), 3,
                           {"cursor": self.head(), "reports": {"herd": None}},
                           self.context)


# ====================================================================
# R6. Duplicates reuse the existing discipline
# ====================================================================


class R6DuplicateTests(ReconciliationFixture):

    def test_R6_replay_same_content_conflict_foreign_and_caller_chosen(self):
        inputs = self.inputs(self.adapters(review=rc.REVIEW_REPORT_PENDING))
        first = self.reconcile(None, inputs=inputs)
        operation_id = first["operation_id"]
        expected = first["sequence"] - 1
        before = self.read_bytes()
        # The same invocation (same sources, same provenance): the recorded
        # outcome, nothing written.
        replay = self.service.reconcile(self.mission_id, operation_id, expected,
                                        copy.deepcopy(inputs), self.context)
        self.assertTrue(replay["idempotent"])
        self.assertTrue(replay["changed"])
        self.assertIsNone(replay["findings"])
        self.assertEqual(dict((k, v) for k, v in replay.items()
                              if k not in ("idempotent", "changed", "findings")),
                         self.state()["applied_operations"][-1]["outcome"])
        self.assertEqual(self.read_bytes(), before)
        # Same meaning but a different observed-at time is a different
        # invocation: it conflicts rather than being folded in (the
        # provenance is digest-bound), and a fresh id then no-ops.
        refreshed = copy.deepcopy(inputs)
        refreshed["reports"]["review"]["observed_at"] += 1
        self.assertRefuses(mss.PROBLEM_STATE_OPERATION_CONFLICT, self.service.reconcile,
                           self.mission_id, operation_id, expected, refreshed,
                           self.context)
        self.assertEqual(self.read_bytes(), before)
        at_head = self.inputs(self.adapters(review=rc.REVIEW_REPORT_PENDING))
        at_head["reports"]["review"]["observed_at"] += 1
        self.assertFalse(self.reconcile(None, inputs=at_head)["changed"])
        self.assertEqual(self.read_bytes(), self.before)
        self.assertRefuses(mss.PROBLEM_STATE_OPERATION_CONFLICT, self.service.reconcile,
                           self.mission_id, operation_id, expected,
                           self.inputs(self.adapters(review=rc.REVIEW_REPORT_REJECT)),
                           self.context)
        self.assertRefuses(mss.PROBLEM_STATE_OPERATION_CONTEXT_CONFLICT,
                           self.service.reconcile, self.mission_id, operation_id,
                           expected, inputs, self.other)
        self.assertRefuses(mss.PROBLEM_UNKNOWN_STATE_OPERATION_ID, self.service.reconcile,
                           self.mission_id, hexid("mo", 0x4242), expected, inputs,
                           self.context)
        self.assertRefuses(mss.PROBLEM_STATE_OPERATION_CONTEXT_CONFLICT,
                           self.service.reconcile, self.mission_id, operation_id,
                           expected + 1, inputs, self.other)


# ====================================================================
# R7. Reload preserves
# ====================================================================


class R7ReloadTests(ReconciliationFixture):

    def test_R7_restart_and_snapshot_absence_preserve_everything(self):
        self.make_local_complete(self.mission_id)
        self.call("open_blocker", self.mission_id, "flaky_network", "degraded")
        adapters = self.adapters(review=rc.REVIEW_REPORT_PENDING,
                                 candidate=self.candidate(test_log="d" * 64))
        self.reconcile(adapters)
        self.reconcile(self.adapters(task=rc.TASK_REPORT_BLOCKED))
        self.clock.advance(11)
        good = self.stable()
        inputs = self.inputs(adapters)
        before = self.observe_raw(inputs)
        position = rc.position_view(self.state(), 1)
        cursor = self.head()
        restarted = self.service.__class__(mst.MissionStore(self.directory), self.clock)
        self.assertEqual(restarted.observe(self.mission_id, inputs), before)
        self.assertEqual(rc.position_view(self.state(), 1), position)
        self.assertEqual(restarted.get_journal(self.mission_id)["cursor"], cursor)
        self.assertEqual(restarted.reload_supported_state(self.mission_id)["source"],
                         mj.SOURCE_SNAPSHOT)
        without = copy.deepcopy(good)
        without["mission_state"][self.mission_id]["snapshot"] = None
        self.write_raw(json.dumps(without))
        self.assertEqual(restarted.observe(self.mission_id, inputs), before)
        self.assertEqual(restarted.reload_supported_state(self.mission_id)["source"],
                         mj.SOURCE_REPLAY)
        self.assertEqual(restarted.reload_supported_state(self.mission_id)["cursor"],
                         cursor)
        self.assertEqual(rc.position_view(self.state(), 1), position)
        self.assertEqual(before["reconciliation"]["findings"], position["findings"])
        self.assertTrue(before["reconciliation"]["current"])
        self.assertEqual(before["reconciliation"]["source_provenance"]["task"][
            "observed_at"], self.clock() - 11)
        result = self.reconcile(self.adapters(task=rc.TASK_REPORT_BLOCKED))
        self.assertFalse(result["changed"])
        self.assertEqual(self.read_bytes(), self.before)


# ====================================================================
# R8. Fail-closed validation of every new shape, bounds
# ====================================================================


class R8FailClosedTests(ReconciliationFixture):

    def setUp(self):
        super(R8FailClosedTests, self).setUp()
        self.make_local_complete(self.mission_id)
        self.existing = self.existing_receipt()
        self.receipt = self.record_validated_receipt(self.existing)
        self.reconcile(self.adapters(review=rc.REVIEW_REPORT_PENDING,
                                     candidate=self.candidate(test_log="d" * 64),
                                     delivery=self.validator_report(self.existing,
                                                                    self.receipt)))
        self.reconcile(self.adapters(task=rc.TASK_REPORT_BLOCKED))
        self.good = self.stable()

    def tamper(self, index, mutate):
        document = copy.deepcopy(self.good)
        record_ = document["mission_state"][self.mission_id]["reconciliations"][index]
        mutate(document, record_)
        return document

    def test_R8_every_shape_refuses_on_load_and_save_with_its_own_code(self):
        def set_(key, value):
            def mutate(document, record_):
                record_[key] = value
            return mutate

        cases = {
            "unknown key": (set_("extra", 1), mission_record.PROBLEM_UNKNOWN_KEY),
            "missing key": (lambda d, r: r.pop("findings"),
                            mission_record.PROBLEM_MISSING_KEY),
            "missing provenance key": (lambda d, r: r.pop("source_provenance"),
                                       mission_record.PROBLEM_MISSING_KEY),
            "position not before its event": (set_("observed_position", 5),
                                              rc.PROBLEM_RECONCILIATION_MALFORMED),
            "bool position": (set_("observed_position", True),
                              mission_record.PROBLEM_BAD_TYPE),
            "revision not provenance": (set_("observed_revision", 2),
                                        rc.PROBLEM_RECONCILIATION_MALFORMED),
            "chain digest wrong": (set_("observed_journal_digest_sha256", "0" * 64),
                                   rc.PROBLEM_RECONCILIATION_BINDING),
            "chain digest malformed": (set_("observed_journal_digest_sha256", "zz"),
                                       mission_record.PROBLEM_BAD_VALUE),
            "time not the operation's": (set_("reconciled_at", 1),
                                         ms.PROBLEM_TIME_INCONSISTENT),
            "operation is a claim": (
                lambda d, r: r.__setitem__("operation_id", self.claim["operation_id"]),
                ms.PROBLEM_OPERATION_BINDING),
            "source standing verified": (
                lambda d, r: r["sources"]["task"].__setitem__("standing", "verified"),
                rc.PROBLEM_SOURCE_MALFORMED),
            "source value on unavailable": (
                lambda d, r: r["sources"]["delivery"].__setitem__(
                    "value", self.delivery("ABSENT")),
                rc.PROBLEM_SOURCE_MALFORMED),
            "source kind missing": (lambda d, r: r["sources"].pop("review"),
                                    rc.PROBLEM_SOURCE_MALFORMED),
            "source value changed": (
                lambda d, r: r["sources"]["task"].__setitem__("value", "FAILED"),
                ms.PROBLEM_INVOCATION_MISMATCH),
            "provenance time changed": (
                lambda d, r: r["source_provenance"]["task"].__setitem__("observed_at", 5),
                ms.PROBLEM_INVOCATION_MISMATCH),
            "provenance missing for reported": (
                lambda d, r: r["source_provenance"]["task"].__setitem__("observed_at", None),
                rc.PROBLEM_SOURCE_MALFORMED),
            "provenance time on unavailable": (
                lambda d, r: r["source_provenance"]["delivery"].__setitem__(
                    "observed_at", 5),
                rc.PROBLEM_SOURCE_MALFORMED),
            "provenance bool time": (
                lambda d, r: r["source_provenance"]["task"].__setitem__("observed_at", True),
                rc.PROBLEM_SOURCE_MALFORMED),
            "provenance extra key": (
                lambda d, r: r["source_provenance"]["task"].__setitem__("detail", "x"),
                rc.PROBLEM_SOURCE_MALFORMED),
            "provenance kind missing": (lambda d, r: r["source_provenance"].pop("review"),
                                        rc.PROBLEM_SOURCE_MALFORMED),
            "finding dropped": (lambda d, r: r["findings"].pop(),
                                ms.PROBLEM_EFFECT_INCONSISTENT),
            "finding rewritten": (
                lambda d, r: r["findings"][0].__setitem__("detail", "other"),
                rc.PROBLEM_RECONCILIATION_DISAGREES),
            "finding unknown kind": (
                lambda d, r: r["findings"][0].__setitem__("kind", "drifted"),
                mission_record.PROBLEM_BAD_VALUE),
            "findings unsorted": (lambda d, r: r["findings"].reverse(),
                                  rc.PROBLEM_RECONCILIATION_MALFORMED),
            "finding detail over bound": (
                lambda d, r: r["findings"][0].__setitem__(
                    "detail", "x" * (rc.MAX_FINDING_DETAIL_CHARS + 1)),
                mission_record.PROBLEM_TOO_LARGE),
            "outcome count": (
                lambda d, r: d["mission_state"][self.mission_id]["applied_operations"][
                    -1]["outcome"].__setitem__("finding_count", 0),
                ms.PROBLEM_EFFECT_INCONSISTENT),
            "outcome position bool": (
                lambda d, r: d["mission_state"][self.mission_id]["applied_operations"][
                    -1]["outcome"].__setitem__("observed_position", True),
                ms.PROBLEM_OUTCOME_MALFORMED),
        }
        for label, (mutate, code) in cases.items():
            with self.subTest(label):
                self.refuse_raw(self.tamper(-1, mutate), code)
        orphaned = copy.deepcopy(self.good)
        orphaned["mission_state"][self.mission_id]["reconciliations"].pop()
        self.refuse_raw(orphaned, ms.PROBLEM_EFFECT_INCONSISTENT)
        duplicated = copy.deepcopy(self.good)
        records = duplicated["mission_state"][self.mission_id]["reconciliations"]
        records.append(copy.deepcopy(records[-1]))
        self.refuse_raw(duplicated, ms.PROBLEM_SEQUENCE)
        # The first record's provenance or sources rewritten: the digest
        # refuses first.
        earlier = self.tamper(0, lambda d, r: r["source_provenance"]["delivery"].__setitem__(
            "observed_at", 1))
        self.refuse_raw(earlier, ms.PROBLEM_INVOCATION_MISMATCH)
        earlier = self.tamper(0, lambda d, r: r["sources"]["candidate"].__setitem__(
            "value", self.candidate(test_log=HEX_A)))
        self.refuse_raw(earlier, ms.PROBLEM_INVOCATION_MISMATCH)
        # The second record carries the first's drift forward; dropping
        # that carried finding is an inconsistent effect.
        carried = self.tamper(-1, lambda d, r: r["findings"].__setitem__(
            slice(None), [f for f in r["findings"]
                          if f["kind"] != rc.FINDING_CANDIDATE_DRIFT]))
        self.refuse_raw(carried, ms.PROBLEM_EFFECT_INCONSISTENT)
        self.assertEqual(self.stable(), self.good)

    def test_R8_bounds_are_module_constants_and_refuse_at_the_bound(self):
        self.assertEqual(rc.MAX_RECONCILIATION_RECORDS, 256)
        self.assertEqual(rc.MAX_RECONCILIATION_FINDINGS, 512)
        self.assertEqual(rc.MAX_FINDING_DETAIL_CHARS, 500)
        self.assertEqual(rc.MAX_OBSERVED_CANDIDATE_KEYS, 64)
        self.assertEqual(rc.REPORTED_FRESHNESS_BOUND_SECONDS, 600)
        self.assertEqual(ob.REPORTED_FRESHNESS_BOUND_SECONDS, 600)
        self.assertEqual(ob.MAX_REPORT_DETAIL_CHARS, 500)
        real = rc.MAX_RECONCILIATION_RECORDS
        rc.MAX_RECONCILIATION_RECORDS = 2
        try:
            exc = self.assertRefuses(rc.PROBLEM_RECONCILIATION_FULL, self.reconcile,
                                     self.adapters(task=rc.TASK_REPORT_FAILED))
            self.assertIn("2", str(exc))
            self.assertEqual(self.read_bytes(), self.before)
            self.assertEqual(self.stable(), json.loads(self.before))
            over = copy.deepcopy(self.good)
            records = over["mission_state"][self.mission_id]["reconciliations"]
            records.append(copy.deepcopy(records[-1]))
            self.write_raw(json.dumps(over))
            with self.assertRaises(mst.MissionStoreError) as ctx:
                self.store.load()
            self.assertEqual(ctx.exception.problem, mst.PROBLEM_STORE_FULL)
            self.assertIn("hard bound is 2", str(ctx.exception))
            self.write_raw(json.dumps(json.loads(self.before)))
            with self.assertRaises(mst.MissionStoreError) as ctx:
                self.store.save(over)
            self.assertEqual(ctx.exception.problem, mst.PROBLEM_STORE_FULL)
        finally:
            rc.MAX_RECONCILIATION_RECORDS = real
        document = self.store.load()
        state = document["mission_state"][self.mission_id]
        mission = document["missions"][self.mission_id]
        contract_ = mst.activation_contract(document, mission,
                                            ms.latest_activation(state), "activation")
        _, answers = ob.normalize_inputs(self.inputs(self.adapters(
            task=rc.TASK_REPORT_FAILED)))
        sources = ob.sources_of(answers)
        provenance = ob.provenance_of(answers)
        previous = rc.records_as_of(state, state["sequence"])
        derived = rc.derive_findings(state, contract_, state["sequence"], 1, sources,
                                     provenance, previous, self.clock())
        self.assertGreater(len(derived), 1)
        real = rc.MAX_RECONCILIATION_FINDINGS
        rc.MAX_RECONCILIATION_FINDINGS = 1
        try:
            exc = self.assertRefuses(rc.PROBLEM_RECONCILIATION_FULL, rc.derive_findings,
                                     state, contract_, state["sequence"], 1, sources,
                                     provenance, previous, self.clock())
            self.assertIn("the hard bound is 1", str(exc))
        finally:
            rc.MAX_RECONCILIATION_FINDINGS = real
        self.assertEqual(self.stable(), json.loads(self.before))

    def test_R8_pure_validators_refuse_hostile_sources_provenance_and_findings(self):
        good = {kind: {"standing": rc.STANDING_UNAVAILABLE, "value": None}
                for kind in rc.SOURCE_KINDS}
        self.assertIs(rc.validate_sources(good, "s"), good)
        hostile = {
            "task 1.0": ("task", {"standing": "reported", "value": 1.0}),
            "review bool": ("review", {"standing": "reported", "value": True}),
            "candidate list": ("candidate", {"standing": "reported", "value": []}),
            "candidate bad key": ("candidate", {"standing": "reported", "value": {
                "baseline_digest_sha256": None, "artifact_digests": {"Bad Key": "c" * 64}}}),
            "candidate bad digest": ("candidate", {"standing": "reported", "value": {
                "baseline_digest_sha256": None, "artifact_digests": {"k": "c" * 63}}}),
            "delivery enum": ("delivery", {"standing": "reported", "value": "VALID"}),
            "delivery bad status": ("delivery", {"standing": "reported", "value": {
                "status": "DONE", "receipt_artifact_id": hexid("mf", 1), "locator": "r",
                "receipt_digest_sha256": "d" * 64}}),
            "delivery bad id": ("delivery", {"standing": "reported", "value": {
                "status": "VALID", "receipt_artifact_id": "mf-x", "locator": "r",
                "receipt_digest_sha256": "d" * 64}}),
            "delivery locator over bound": ("delivery", {"standing": "reported", "value": {
                "status": "VALID", "receipt_artifact_id": hexid("mf", 1),
                "locator": "r" * (ms.MAX_LOCATOR_CHARS + 1),
                "receipt_digest_sha256": "d" * 64}}),
            "delivery digest missing": ("delivery", {"standing": "reported", "value": {
                "status": "VALID", "receipt_artifact_id": hexid("mf", 1), "locator": "r",
                "receipt_digest_sha256": None}}),
            "delivery digest malformed": ("delivery", {"standing": "reported", "value": {
                "status": "VALID", "receipt_artifact_id": hexid("mf", 1), "locator": "r",
                "receipt_digest_sha256": "zz"}}),
            "delivery absent with digest": ("delivery", {"standing": "reported", "value": {
                "status": "ABSENT", "receipt_artifact_id": None, "locator": None,
                "receipt_digest_sha256": "d" * 64}}),
            "standing fresh": ("task", {"standing": "fresh", "value": None}),
            "extra key": ("task", {"standing": "unknown", "value": None, "at": 1}),
        }
        for label, (kind, entry) in hostile.items():
            with self.subTest(label):
                self.assertRefuses(rc.PROBLEM_SOURCE_MALFORMED, rc.validate_sources,
                                   dict(good, **{kind: entry}), "s")
        provenance = {kind: {"observed_at": None} for kind in rc.SOURCE_KINDS}
        self.assertIs(rc.validate_source_provenance(provenance, good, "p"), provenance)
        reported = dict(good, task={"standing": "reported", "value": "ACTIVE"})
        for label, entry in (("missing for reported", {"observed_at": None}),
                             ("bool", {"observed_at": True}),
                             ("negative", {"observed_at": -1}),
                             ("extra", {"observed_at": 1, "source": "x"})):
            with self.subTest(label):
                self.assertRefuses(rc.PROBLEM_SOURCE_MALFORMED,
                                   rc.validate_source_provenance,
                                   dict(provenance, task=entry), reported, "p")
        self.assertRefuses(mission_record.PROBLEM_BAD_VALUE, rc.validate_finding,
                           {"kind": "nope", "subject": None, "detail": "d"}, "f")
        self.assertRefuses(mission_record.PROBLEM_TOO_LARGE, rc.validate_finding,
                           {"kind": rc.FINDING_PROOF_MISSING, "subject": "k" * 129,
                            "detail": "d"}, "f")
        self.assertRefuses(mission_record.PROBLEM_UNKNOWN_KEY, rc.validate_finding,
                           {"kind": rc.FINDING_PROOF_MISSING, "subject": None,
                            "detail": "d", "at": 1}, "f")


# ====================================================================
# R9. Compatibility
# ====================================================================


class R9CompatibilityTests(ReconciliationFixture):

    def test_R9_a_record_without_the_key_loads_observes_and_gains_it_only_by_reconciling(self):
        self.assertIn("reconciliations", ms.STATE_RECORD_OPTIONAL_KEYS)
        self.assertEqual(ms.new_state_record(hexid("mn", 1), 5)["reconciliations"], [])
        legacy = self.document()
        del legacy["mission_state"][self.mission_id]["reconciliations"]
        self.write_raw(json.dumps(legacy))
        before = self.read_bytes()
        loaded = self.store.load()
        self.assertNotIn("reconciliations", loaded["mission_state"][self.mission_id])
        self.assertEqual(loaded, legacy)
        self.assertEqual(self.read_bytes(), before)
        self.store.save(loaded)
        self.assertNotIn("reconciliations",
                         json.loads(self.read_bytes())["mission_state"][self.mission_id])
        self.assertEqual(json.loads(self.read_bytes()), legacy)
        report = self.observe(self.adapters())
        self.assertIsNone(report["reconciliation"])
        self.assertEqual(report["sequence"], 3)
        self.call("record_claim", self.mission_id, "tests_pass", "after legacy")
        self.assertNotIn("reconciliations", self.state())
        self.assertEqual(self.service.reload_supported_state(self.mission_id)["source"],
                         mj.SOURCE_SNAPSHOT)
        result = self.reconcile(self.adapters())
        self.assertTrue(result["changed"])
        self.assertEqual(len(self.state()["reconciliations"]), 1)
        self.assertEqual(self.state()["reconciliations"][0]["observed_position"], 4)
        self.stable()
        for text in (ms.__doc__, mst.__doc__, rc.__doc__, mss.__doc__):
            self.assertIn("reconcil", text)
        self.assertIn("additive-optional", ms.__doc__)
        self.assertIn("NOTHING for it", rc.__doc__)

    def test_R9_a_mission_with_no_state_record_reconciles_at_the_origin(self):
        other, _ = self.approved_mission()
        self.assertNotIn(other, self.document()["mission_state"])
        result = self.reconcile(self.adapters(task=rc.TASK_REPORT_NOT_STARTED),
                                mission_id=other, expected_sequence=0)
        self.assertTrue(result["changed"])
        self.assertEqual(result["sequence"], 1)
        self.assertEqual(result["observed_position"], 0)
        self.assertEqual(result["progress"], ms.PROGRESS_NOT_STARTED)
        self.assertEqual(self.kinds(result), [rc.FINDING_SOURCE_UNAVAILABLE] * 3)
        state = self.state(other)
        self.assertEqual(state["progress"], ms.PROGRESS_NOT_STARTED)
        self.assertEqual(state["contract_activations"], [])
        self.assertEqual(state["reconciliations"][0]["observed_journal_digest_sha256"],
                         mj.genesis_digest(other))
        self.assertIsNone(state["snapshot"]["supported_state"]["activation_id"])
        self.assertEqual(self.service.reload_supported_state(other)["source"],
                         mj.SOURCE_SNAPSHOT)
        again = self.reconcile(self.adapters(task=rc.TASK_REPORT_NOT_STARTED),
                               mission_id=other)
        self.assertFalse(again["changed"])
        self.assertEqual(self.read_bytes(), self.before)
        self.call("activate_proof_contract", other)
        self.assertEqual(self.state(other)["progress"], ms.PROGRESS_IN_PROGRESS)
        self.stable()


# ====================================================================
# RA. Task 7, Stage 2: attested receipts (the receipt criterion)
# ====================================================================


class RAAttestedReceiptTests(ReconciliationFixture):
    """Condition 5 in reconciliation and observation: only the attested
    form (recorded by the delivery layer's validating caller through the
    real, unchanged validator and the real consumer/service path) yields
    a positive receipt finding or completed-effect evidence; structural
    validity is separated from success; unknown, moved and stale
    provenance stays conservative; findings are bounded at maximum field
    lengths; repeats converge; reconciliation manufactures nothing; reload
    and replay preserve the attested form and validate nothing."""

    def setUp(self):
        super(RAAttestedReceiptTests, self).setUp()
        from pr_delivery import authorization as delivery_authorization
        from pr_delivery import mission_parent
        from test_mission_core import delivery_record, with_receipt
        self.auth = delivery_authorization
        self.seam = mission_parent
        self.delivery_record = delivery_record
        self.with_receipt = with_receipt
        self.record = delivery_record(self.mission_id,
                                      self.authorization_digest(self.mission_id),
                                      self.clock())

    def attest_record(self, record, step):
        """The REAL consumer path over a prepared delivery record."""
        return self.seam.attest_validated_receipt(
            record, step, self.service, self.oid(), self.seq(self.mission_id),
            self.context)

    def attest(self, step, state):
        """A receipt the delivery layer derived in ``state`` and the
        UNCHANGED validator accepted, attested through the real consumer
        path; returns the attested artifact id and the receipt."""
        record = self.with_receipt(self.record, step, state, self.clock())
        result = self.attest_record(record, step)
        self.assertTrue(result["valid"], result)
        self.prepared = record
        return result["outcome"]["artifact_id"], record["steps"][step]["receipt"]

    def report_for(self, artifact_id, receipt, status=rc.DELIVERY_REPORT_VALID):
        return {"status": status, "receipt_artifact_id": artifact_id,
                "locator": receipt["receipt_id"],
                "receipt_digest_sha256": receipt["receipt_digest_sha256"]}

    def test_RA_positive_findings_exist_only_for_the_attested_form(self):
        attested, receipt = self.attest(self.auth.STEP_PR_CREATE,
                                        self.auth.RECEIPT_SUCCEEDED)
        # A generic reference recorded with the SAME digest and reference
        # through record_artifact: a matching arbitrary digest through the
        # generic path stays unattested.
        generic = self.record_receipt(locator=receipt["receipt_id"],
                                      digest=receipt["receipt_digest_sha256"])
        self.assertEqual(self.receipts_by_attestation(),
                         {"attested": [attested], "unattested": [generic]})
        # No delivery report at all: the RECORD yields the attested finding.
        result = self.reconcile(self.adapters())
        found = [f for f in result["findings"] if f["kind"] == rc.FINDING_DELIVERY_ATTESTED]
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]["subject"], attested)
        self.assertIn("effect completed: yes", found[0]["detail"])
        self.assertIn(self.auth.STEP_PR_CREATE, found[0]["detail"])
        self.assertIn(self.auth.RECEIPT_SUCCEEDED, found[0]["detail"])
        self.assertNotIn(receipt["receipt_id"], found[0]["detail"])
        # A VALID report bound to the ATTESTED reference: the positive
        # report finding exists, exactly once, beside the record finding.
        valid = self.reconcile(self.adapters(delivery=self.report_for(attested, receipt)))
        kinds = self.kinds(valid)
        self.assertEqual(kinds.count(rc.FINDING_DELIVERY_VALID), 1)
        self.assertEqual(kinds.count(rc.FINDING_DELIVERY_ATTESTED), 1)
        self.assertNotIn(rc.FINDING_DELIVERY_UNATTESTED, kinds)
        bound = [f for f in valid["findings"] if f["kind"] == rc.FINDING_DELIVERY_VALID]
        self.assertEqual(bound[0]["subject"], attested)
        # The SAME report bound to the generic twin: unattested, never valid.
        twin = self.reconcile(self.adapters(delivery=self.report_for(generic, receipt)))
        self.assertIn(rc.FINDING_DELIVERY_UNATTESTED, self.kinds(twin))
        self.assertNotIn(rc.FINDING_DELIVERY_VALID, self.kinds(twin))
        self.assertEqual([f["subject"] for f in twin["findings"]
                          if f["kind"] == rc.FINDING_DELIVERY_UNATTESTED], [generic])
        # An INVALID report against the attested reference is reflected as
        # the caller's contradiction, conservatively, beside the record
        # finding; the record finding never disappears.
        invalid = self.reconcile(self.adapters(delivery=self.report_for(
            attested, receipt, rc.DELIVERY_REPORT_INVALID)))
        self.assertIn(rc.FINDING_DELIVERY_INVALID, self.kinds(invalid))
        self.assertIn(rc.FINDING_DELIVERY_ATTESTED, self.kinds(invalid))
        # Observation: attested and unattested are listed apart; the bound
        # report says whether its reference is attested; standing stays
        # verified for the record and reported for the source.
        report = self.observe(self.adapters(delivery=self.report_for(attested, receipt)))
        value = report["delivery_receipts"]["value"]
        self.assertEqual(report["delivery_receipts"]["standing"], ob.STANDING_VERIFIED)
        self.assertEqual(report["delivery"]["standing"], ob.STANDING_REPORTED)
        self.assertEqual(value["recorded"], [attested, generic])
        self.assertEqual([a["artifact_id"] for a in value["attested"]], [attested])
        self.assertEqual(value["attested"][0]["effect_completed"], True)
        self.assertEqual(value["attested"][0]["step"], self.auth.STEP_PR_CREATE)
        self.assertEqual(value["attested"][0]["receipt_state"], self.auth.RECEIPT_SUCCEEDED)
        self.assertEqual(value["unattested"], [generic])
        self.assertEqual(value["effects_completed"], [attested])
        self.assertEqual(value["report_bound_to"], attested)
        self.assertIs(value["report_bound_attested"], True)
        items = {a["artifact_id"]: a for a in report["artifacts"]["value"]}
        self.assertTrue(items[attested]["attested"])
        self.assertFalse(items[generic]["attested"])
        self.assertIsNone(items[generic]["receipt_attestation"])
        # An attestation is evidence, not completion: no completion term
        # moves and no hold is removed by it.
        completion = report["completion"]
        self.assertFalse(completion["closure_verified"])
        self.assertFalse(completion["verified_success"])
        self.assertIn(ob.HOLD_NO_CANONICAL_CLOSURE, completion["holds"])
        self.assertEqual(self.stable(), self.document())

    def test_RA_structural_validity_is_not_success(self):
        # A validator-accepted receipt in each non-success state is
        # attested as exactly that state: attested, listed, and NOT a
        # completed effect, in findings and in observation.
        expected = {}
        for step, state in (
            (self.auth.STEP_BASE_REFRESH, self.auth.RECEIPT_DERIVED),
            (self.auth.STEP_COMMIT, self.auth.RECEIPT_EXECUTING),
            (self.auth.STEP_PUSH, self.auth.RECEIPT_FAILED_RETRYABLE),
            (self.auth.STEP_PR_CREATE, self.auth.RECEIPT_VOID),
        ):
            artifact_id, _ = self.attest(step, state)
            expected[artifact_id] = (step, state)
        result = self.reconcile(self.adapters())
        found = {f["subject"]: f for f in result["findings"]
                 if f["kind"] == rc.FINDING_DELIVERY_ATTESTED}
        self.assertEqual(set(found), set(expected))
        for artifact_id, (step, state) in expected.items():
            self.assertIn("effect completed: no", found[artifact_id]["detail"])
            self.assertIn(state, found[artifact_id]["detail"])
        report = self.observe()
        value = report["delivery_receipts"]["value"]
        self.assertEqual(sorted(a["artifact_id"] for a in value["attested"]),
                         sorted(expected))
        self.assertEqual(value["effects_completed"], [])
        for item in value["attested"]:
            self.assertFalse(item["effect_completed"])
            self.assertEqual(item["receipt_state"], expected[item["artifact_id"]][1])
        # Then the succeeded state, for one of them: exactly that one is a
        # completed effect.
        done, _ = self.attest(self.auth.STEP_COMMIT, self.auth.RECEIPT_SUCCEEDED)
        value = self.observe()["delivery_receipts"]["value"]
        self.assertEqual(value["effects_completed"], [done])
        self.assertEqual(len(value["attested"]), len(expected) + 1)
        self.assertEqual(ms.RECEIPT_STATE_SUCCEEDED, self.auth.RECEIPT_SUCCEEDED)

    def test_RA_unknown_moved_and_stale_provenance_stay_conservative(self):
        attested, receipt = self.attest(self.auth.STEP_PUSH, self.auth.RECEIPT_SUCCEEDED)
        judged = self.report_for(attested, receipt)
        at_collection = self.head()
        # Moved: the document advanced after collection. The report binds
        # nothing and says nothing about attestation; the RECORD facts stay
        # verified at the head.
        self.call("record_claim", self.mission_id, "tests_pass", "later")
        moved = self.observe_raw(materialize(self.adapters(delivery=judged),
                                             self.mission_id, at_collection))
        self.assertEqual(moved["provenance"]["collection"]["status"], ob.COLLECTION_MOVED)
        self.assertIsNone(moved["delivery_receipts"]["value"]["report_bound_to"])
        self.assertIsNone(moved["delivery_receipts"]["value"]["report_bound_attested"])
        self.assertEqual([a["artifact_id"] for a in
                          moved["delivery_receipts"]["value"]["attested"]], [attested])
        # Unknown cursor (no cursor supplied): likewise.
        unknown = self.observe_raw({"cursor": None, "reports": {
            "delivery": self.answer(judged)}})
        self.assertIsNone(unknown["delivery_receipts"]["value"]["report_bound_to"])
        self.assertIsNone(unknown["delivery_receipts"]["value"]["report_bound_attested"])
        # Stale report: the report finding is stale; the record finding
        # stands; nothing is re-attested, retried or written beyond the
        # one reconciliation event.
        aged = self.reconcile(self.adapters(delivery=judged,
                                            age=rc.REPORTED_FRESHNESS_BOUND_SECONDS + 1))
        self.assertIn(rc.FINDING_DELIVERY_STALE, self.kinds(aged))
        self.assertIn(rc.FINDING_DELIVERY_ATTESTED, self.kinds(aged))
        self.assertNotIn(rc.FINDING_DELIVERY_VALID, self.kinds(aged))
        self.assertEqual(len(self.receipts_by_attestation()["attested"]), 1)
        # Unavailable source: the record finding stands, the source is
        # reported unavailable.
        busy = self.reconcile({"delivery": Counting(raise_=RuntimeError("busy"))})
        self.assertIn(rc.FINDING_SOURCE_UNAVAILABLE, self.kinds(busy))
        self.assertIn(rc.FINDING_DELIVERY_ATTESTED, self.kinds(busy))

    def test_RA_maximum_lengths_are_bounded_before_work_and_findings_stay_bounded(self):
        longest = "x" * ms.MAX_RECEIPT_ATTESTATION_FIELD_CHARS
        attestation = self.attestation(self.mission_id, receipt_id=longest,
                                       step="s" * ms.MAX_RECEIPT_ATTESTATION_FIELD_CHARS,
                                       receipt_state="t" * ms.MAX_RECEIPT_ATTESTATION_FIELD_CHARS,
                                       delivery_id="d" * ms.MAX_RECEIPT_ATTESTATION_FIELD_CHARS)
        outcome = self.call("attest_delivery_receipt", self.mission_id, attestation)
        result = self.reconcile(self.adapters(delivery={
            "status": rc.DELIVERY_REPORT_VALID,
            "receipt_artifact_id": outcome["artifact_id"], "locator": longest,
            "receipt_digest_sha256": attestation["receipt_digest_sha256"]}))
        kinds = self.kinds(result)
        self.assertIn(rc.FINDING_DELIVERY_ATTESTED, kinds)
        self.assertIn(rc.FINDING_DELIVERY_VALID, kinds)
        for finding in result["findings"]:
            self.assertLessEqual(len(finding["detail"]), rc.MAX_FINDING_DETAIL_CHARS)
        self.assertEqual(self.stable(), self.document())
        # One over the bound, for every string field, refuses with the
        # attestation's own code before anything is read or written.
        over = "x" * (ms.MAX_RECEIPT_ATTESTATION_FIELD_CHARS + 1)
        for field in ("receipt_id", "step", "receipt_state", "delivery_id"):
            with self.subTest(field):
                operation_id = self.oid()
                before = self.read_bytes()
                self.assertRefuses(ms.PROBLEM_RECEIPT_ATTESTATION,
                                   self.service.attest_delivery_receipt,
                                   self.mission_id, operation_id, self.seq(self.mission_id),
                                   self.attestation(self.mission_id, **{field: over}),
                                   self.context)
                self.assertEqual(self.read_bytes(), before)
        # Hostile shapes: not a dict, a subclass, non-string keys, an
        # extra key, a missing key, a non-hex digest, a bool where a string
        # is expected — each refused with the same code, nothing written.
        class Sub(dict):
            pass
        hostile = [
            None, [], Sub(self.attestation(self.mission_id)),
            dict(self.attestation(self.mission_id), extra=1),
            {k: v for k, v in self.attestation(self.mission_id).items() if k != "step"},
            self.attestation(self.mission_id, receipt_digest_sha256="zz"),
            self.attestation(self.mission_id, step=True),
            dict(self.attestation(self.mission_id), **{"k" * 200: "v"}),
        ]
        broken = self.attestation(self.mission_id)
        broken[7] = "v"
        hostile.append(broken)
        for value in hostile:
            operation_id = self.oid()
            before = self.read_bytes()
            self.assertRefuses(ms.PROBLEM_RECEIPT_ATTESTATION,
                               self.service.attest_delivery_receipt,
                               self.mission_id, operation_id, self.seq(self.mission_id),
                               value, self.context)
            self.assertEqual(self.read_bytes(), before)

    def test_RA_repeats_converge_and_reconciliation_manufactures_nothing(self):
        attested, receipt = self.attest(self.auth.STEP_PR_CREATE,
                                        self.auth.RECEIPT_SUCCEEDED)
        judged = self.report_for(attested, receipt)
        first = self.reconcile(self.adapters(delivery=judged))
        self.assertTrue(first["changed"])
        artifacts = self.state()["artifacts"]
        # A second pass with the same meaning writes literally nothing; a
        # repeated receipt observation refuses; three more reports, three
        # no-ops: no storm.
        for _ in range(3):
            again = self.reconcile(self.adapters(delivery=judged))
            self.assertFalse(again["changed"])
            self.assertEqual(self.read_bytes(), self.before)
        self.assertRefuses(mss.PROBLEM_RECEIPT_ALREADY_ATTESTED, self.attest_record,
                           self.prepared, self.auth.STEP_PR_CREATE)
        # Reconciliation over ANY report never adds, promotes or marks an
        # artifact: the artifacts list is byte-identical across every
        # pass, including a VALID report about a generic reference and an
        # INVALID report about the attested one.
        generic = self.record_receipt(locator="r", digest="9" * 64)
        artifacts = self.state()["artifacts"]
        for report in (self.report_for(generic, {"receipt_id": "r",
                                                 "receipt_digest_sha256": "9" * 64}),
                       self.report_for(attested, receipt, rc.DELIVERY_REPORT_INVALID),
                       self.report_for(attested, receipt, rc.DELIVERY_REPORT_AMBIGUOUS),
                       self.delivery("ABSENT")):
            self.reconcile(self.adapters(delivery=report))
            self.assertEqual(self.state()["artifacts"], artifacts)
        self.assertEqual(self.receipts_by_attestation(),
                         {"attested": [attested], "unattested": [generic]})
        self.assertEqual(self.ledger_kinds().count(ms.OPERATION_ATTEST_DELIVERY_RECEIPT), 1)

    def test_RA_reload_snapshot_and_replay_preserve_the_attested_form_and_validate_nothing(self):
        attested, receipt = self.attest(self.auth.STEP_PUSH, self.auth.RECEIPT_SUCCEEDED)
        self.reconcile(self.adapters(delivery=self.report_for(attested, receipt)))
        stored = self.document()
        restarted = self.service.__class__(mst.MissionStore(self.directory), self.clock)
        self.assertEqual(restarted.reload_supported_state(self.mission_id)["source"],
                         mj.SOURCE_SNAPSHOT)
        with_snapshot = restarted.reload_supported_state(self.mission_id)
        with_snapshot_report = restarted.observe(self.mission_id, self.inputs())
        # Drop the snapshot: replay reconstructs the same supported state
        # and the same observation from the ledger alone; the marker is
        # untouched by replay and no validation adapter is reachable from
        # the journal, observation, reconciliation or service modules.
        without = copy.deepcopy(stored)
        without["mission_state"][self.mission_id]["snapshot"] = None
        self.write_raw(json.dumps(without))
        replayed = self.service.__class__(mst.MissionStore(self.directory), self.clock)
        self.assertEqual(replayed.reload_supported_state(self.mission_id)["source"],
                         mj.SOURCE_REPLAY)
        self.assertEqual(replayed.reload_supported_state(self.mission_id)["supported_state"],
                         with_snapshot["supported_state"])
        self.assertEqual(replayed.observe(self.mission_id, self.inputs())["delivery_receipts"],
                         with_snapshot_report["delivery_receipts"])
        artifact = [a for a in replayed.get_state(self.mission_id)["record"]["artifacts"]
                    if a["artifact_id"] == attested][0]
        self.assertEqual(artifact, [a for a in stored["mission_state"][self.mission_id][
            "artifacts"] if a["artifact_id"] == attested][0])
        events = replayed.get_journal(self.mission_id)["events"]
        self.assertIn(ms.OPERATION_ATTEST_DELIVERY_RECEIPT, [e["kind"] for e in events])
        for relpath in ("mission/journal.py", "mission/observation.py",
                        "mission/reconciliation.py", "mission/state_service.py",
                        "mission/store.py", "mission/state_validation.py",
                        "mission/state_reconcile.py"):
            source = (REPO_ROOT / relpath).read_text()
            for forbidden in ("validate_receipt(", "validate_authorization(",
                              "receipts.derive", "pr_delivery", "authority_digest("):
                self.assertNotIn(forbidden, source, (relpath, forbidden))


# ====================================================================
# RB. Round 11: unobserved artifacts are recorded, carried and resolved
# ====================================================================


class RBUnobservedArtifactTests(ReconciliationFixture):

    def test_RB_omissions_are_findings_carried_until_named_with_no_storm(self):
        self.make_local_complete(self.mission_id)
        authority = self.authority_bytes()
        evidence_before = self.state()["evidence"]
        # The Reviewer's probe through reconciliation: an empty
        # artifact_digests is valid at the boundary and yields exactly one
        # unobserved finding per recorded keyed artifact with a digest.
        empty = self.candidate()
        self.assertIs(rc.validate_candidate_value(empty, "c"), empty)
        first = self.reconcile(self.adapters(candidate=empty))
        self.assertTrue(first["changed"])
        unobserved = [f for f in first["findings"]
                      if f["kind"] == rc.FINDING_CANDIDATE_UNOBSERVED]
        self.assertEqual([f["subject"] for f in unobserved], ["test_log"])
        self.assertIn(rc.FINDING_CANDIDATE_UNOBSERVED, rc.FINDING_KINDS)
        self.assertIn(rc.FINDING_CANDIDATE_UNOBSERVED, rc.DRIFT_FINDINGS)
        for finding in first["findings"]:
            self.assertLessEqual(len(finding["detail"]), rc.MAX_FINDING_DETAIL_CHARS)
        # No storm: the same empty report again is not a meaningful change
        # and writes literally nothing.
        for _ in range(3):
            again = self.reconcile(self.adapters(candidate=empty))
            self.assertFalse(again["changed"])
            self.assertEqual(self.read_bytes(), self.before)
        # A stale report cannot resolve it: carried, and it creates none.
        stale = self.reconcile(self.adapters(candidate=self.candidate(test_log=HEX_A),
                                             age=rc.REPORTED_FRESHNESS_BOUND_SECONDS + 1))
        self.assertIn(rc.FINDING_CANDIDATE_UNOBSERVED, self.kinds(stale))
        # An applicable report that names the artifact resolves it.
        resolved = self.reconcile(self.adapters(candidate=self.candidate(test_log=HEX_A)))
        self.assertTrue(resolved["changed"])
        self.assertNotIn(rc.FINDING_CANDIDATE_UNOBSERVED, self.kinds(resolved))
        self.assertNotIn(rc.FINDING_CANDIDATE_DRIFT, self.kinds(resolved))
        # Then omitted again: unobserved again, one event; and the
        # observation agrees at each step, withholding present success
        # only while unconfirmed.
        omitted = self.reconcile(self.adapters(candidate=empty))
        self.assertTrue(omitted["changed"])
        self.assertIn(rc.FINDING_CANDIDATE_UNOBSERVED, self.kinds(omitted))
        report = self.observe(self.adapters(candidate=empty))
        self.assertTrue([e for e in report["evidence"]["value"] if e["accepted"]][0][
            "unconfirmed"])
        # Source evidence and authority never moved; every record reloads.
        self.assertEqual(self.state()["evidence"], evidence_before)
        self.assertEqual(self.authority_bytes(), authority)
        self.assertEqual(self.stable(), self.document())
        self.assertEqual(self.ledger_kinds().count(ms.OPERATION_RECONCILE), 4)


# ====================================================================
# RC. Round 12: drift over every recorded artifact a key holds that matters
# ====================================================================


class RCDuplicateKeyTests(ReconciliationFixture):

    def test_RC_drift_and_omission_cover_the_referenced_artifact_not_only_the_latest(self):
        older = self.call("record_artifact", self.mission_id, "extra_log",
                          mission_record.ARTIFACT_ROLE_PRODUCED,
                          ms.LOCATOR_KIND_OPAQUE_REFERENCE, "opaque:x", "1" * 64, True, [])
        evidence = self.call("submit_evidence", self.mission_id, "tests_pass",
                             mission_record.EVIDENCE_KIND_VERIFICATION_RECORD, "e" * 64,
                             [self.artifact["artifact_id"], older["artifact_id"]])
        self.clock.advance(1)
        self.call("accept_evidence", self.mission_id, evidence["evidence_id"], "e" * 64,
                  context=self.other)
        self.call("record_artifact", self.mission_id, "extra_log",
                  mission_record.ARTIFACT_ROLE_PRODUCED,
                  ms.LOCATOR_KIND_OPAQUE_REFERENCE, "opaque:y", "2" * 64, True, [])
        authority = self.authority_bytes()
        # A report confirming the LATEST only: drift for the key, naming
        # the disagreement; bounded detail.
        latest_only = self.reconcile(self.adapters(candidate=self.candidate(
            test_log=HEX_A, extra_log="2" * 64)))
        drift = [f for f in latest_only["findings"] if f["kind"] == rc.FINDING_CANDIDATE_DRIFT]
        self.assertEqual([f["subject"] for f in drift], ["extra_log"])
        self.assertIn("disagrees with 1 of the 2 recorded artifact(s)", drift[0]["detail"])
        self.assertLessEqual(len(drift[0]["detail"]), rc.MAX_FINDING_DETAIL_CHARS)
        # Repeating it is not an event.
        again = self.reconcile(self.adapters(candidate=self.candidate(
            test_log=HEX_A, extra_log="2" * 64)))
        self.assertFalse(again["changed"])
        self.assertEqual(self.read_bytes(), self.before)
        # Omitting the key: unobserved, once, whichever artifact matters —
        # and the drift the previous pass recorded for that key is CARRIED,
        # because a report that does not name the key cannot resolve it
        # (the accepted carry rule); an omission resolves nothing.
        omitted = self.reconcile(self.adapters(candidate=self.candidate(test_log=HEX_A)))
        self.assertEqual([f["subject"] for f in omitted["findings"]
                          if f["kind"] == rc.FINDING_CANDIDATE_UNOBSERVED], ["extra_log"])
        self.assertEqual([f["subject"] for f in omitted["findings"]
                          if f["kind"] == rc.FINDING_CANDIDATE_DRIFT], ["extra_log"])
        # The same-digest shape (the Reviewer's): a re-recorded test_log
        # with the approved digest, and a report naming it, is confirmed
        # for both; omitting it is unobserved.
        self.call("record_artifact", self.mission_id, "test_log",
                  mission_record.ARTIFACT_ROLE_VERIFICATION,
                  ms.LOCATOR_KIND_OPAQUE_REFERENCE, "opaque:log2", HEX_A, True, [])
        named = self.reconcile(self.adapters(candidate=self.candidate(
            test_log=HEX_A, extra_log="1" * 64)))
        self.assertNotIn(rc.FINDING_CANDIDATE_UNOBSERVED, self.kinds(named))
        self.assertEqual([f["subject"] for f in named["findings"]
                          if f["kind"] == rc.FINDING_CANDIDATE_DRIFT], ["extra_log"])
        self.assertEqual(self.authority_bytes(), authority)
        self.assertEqual(self.stable(), self.document())


if __name__ == "__main__":
    unittest.main()
