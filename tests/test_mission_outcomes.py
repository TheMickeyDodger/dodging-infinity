"""(c): the three human-requested NON-APPROVAL outcomes on a local-surface proposal, carried by the
EXISTING operator-attestation policy (owner decision (c), reports/owner-request-outcomes-authority-20261029.md):

- DECLINE is the existing DENY: durable, NON-terminal (DENIED may return to AWAITING_DECISION), and
  distinct from the proposer's withdrawal.
- REJECT is a DISTINCT TERMINAL outcome, the new state REJECTED, reachable only from AWAITING_DECISION.
  DENY stays non-terminal, and its historical records keep their meaning.
- REQUEST CHANGES records the person's READABLE reason verbatim (new state CHANGES_REQUESTED,
  non-terminal). The unchanged proposal can no longer be approved. A revised proposal goes through the
  EXISTING approval flow: the proposer submits it as a new request and it awaits its own decision; in
  Mission Core only a new revision (the existing EDIT) returns the same Mission to AWAITING_DECISION.
  A change request implies no revision submitted or approved.
- PROPOSER WITHDRAWAL IS UNTOUCHED: it is still possible only before any decision, so none of the three
  outcomes can be withdrawn.

THE AUTHORITY SHAPE IS STRUCTURAL: each attested outcome has its OWN narrow Mission Core entry point, as
the attested approval has, so ``apply_human_decision`` keeps refusing attested DENY and EDIT
(test_mission_core ``test_O4``) BY CONSTRUCTION. The new kinds are not added to the envelope's
DECISIONS, so no authenticated path is broadened either. Each outcome binds the exact revision and
digest the person saw, replays idempotently by decision id ONLY for the same content AND the same digest
(round 61), refuses anything else under that id, applies only to a proposal the local request surface
made, issues no authorization, and records an
operator-attested, not-independently-verified provenance. Approve is unchanged.

The names this file expects (an implementation contract, read by the red):
  mission.service.MissionService.apply_operator_attested_decline(context, decision_id, mission_id,
      revision, proposal_digest_sha256)
  … .apply_operator_attested_rejection(same)
  … .apply_operator_attested_change_request(same, reason)
  mission.decision.DECISION_REJECT = "REJECT"; DECISION_REQUEST_CHANGES = "REQUEST_CHANGES"
  mission.record.STATE_REJECTED = "REJECTED"; STATE_CHANGES_REQUESTED = "CHANGES_REQUESTED";
      MAX_CHANGE_REQUEST_REASON_CHARS = 2000; PROBLEM_ATTESTED_OUTCOME = "mission_attested_outcome";
      PROBLEM_CHANGE_REQUEST_REASON = "mission_change_request_reason"
  local_request.surface.LocalRequestSurface.attest_decline(request_ref, mission_id, revision,
      proposal_digest_sha256, relay_ref); .attest_rejection(same);
      .attest_change_request(same before relay_ref, reason, relay_ref);
      PROBLEM_CHANGE_REQUEST_REASON = "local_request_change_request_reason";
      status(request_ref)["attested_outcome"] = {"state": "APPLIED", "kind": "decline"|"reject"|"request_changes", …}
  mission_panel.service.PanelService.decline(request_ref, mission_id, revision, proposal_digest_sha256,
      offer_digest_sha256, expires_at, action_scope, delivery_targets, repository_url); .reject(same);
      .request_changes(same, reason)
A READABLE REASON: a string of at most 2000 characters, not blank, with no control character other
than newline and tab; recorded exactly as given.

What is REAL: Mission Core (store, service, records), the local request surface and its store, and the
panel service with its journal, all in-process, over in-pin temporary directories; and the state tables
of two consumers that re-declare every Mission state (mission_sources.sources, coordination.record).
The run bridge's terminal list is PARSED, not imported (the bridge imports the run machinery). What is SYNTHETIC:
the proposals, the contexts' labels and the clock. Nothing here is a live pending Mission or a real
panel operation, and nothing starts a process, opens a socket, touches Git or a transport.

CONTAINMENT is this test's own obligation (its runner is not a sandbox). BEFORE any product import, an
audit hook that raises on socket creation, process start and signals to another process is installed,
and socket.socket, subprocess.Popen, os.fork, os.posix_spawn and the os.exec* family are replaced with
raisers. Every test asserts in its cleanup that none was reached, and one test asserts no other test
module was imported. Every per-item loop asserts the collection it iterates first (round 59). Every
refusal is asserted by its exact problem code.
"""

import os
import socket
import subprocess
import sys

BLOCKED_EVENTS = frozenset((
    "socket.__new__", "subprocess.Popen", "os.system", "os.exec",
    "os.posix_spawn", "os.spawn", "os.fork", "os.forkpty", "os.kill",
    "os.killpg",
))
REACHED = []


def _audit(event, args):
    if event in BLOCKED_EVENTS:
        REACHED.append(event)
        raise RuntimeError("test_mission_outcomes: blocked %s" % event)


sys.addaudithook(_audit)


class _RefusedSocket(socket.socket):
    def __init__(self, *args, **kwargs):
        REACHED.append("socket.socket")
        raise RuntimeError("test_mission_outcomes: socket is refused")


class _RefusedPopen(subprocess.Popen):
    def __init__(self, *args, **kwargs):
        REACHED.append("subprocess.Popen")
        raise RuntimeError("test_mission_outcomes: Popen is refused")


def _raiser(name):
    def refuse(*args, **kwargs):
        REACHED.append(name)
        raise RuntimeError("test_mission_outcomes: %s is refused" % name)
    refuse.refused_name = name
    return refuse


EXEC_NAMES = ("execv", "execve", "execl", "execle", "execlp", "execlpe",
              "execvp", "execvpe")
socket.socket = _RefusedSocket
subprocess.Popen = _RefusedPopen
os.fork = _raiser("os.fork")
os.posix_spawn = _raiser("os.posix_spawn")
for _name in EXEC_NAMES:
    setattr(os, _name, _raiser("os." + _name))

import ast  # noqa: E402
import copy  # noqa: E402
import signal  # noqa: E402
import tempfile  # noqa: E402
import unittest  # noqa: E402
from pathlib import Path  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[1]
TESTS_DIR = os.path.realpath(str(REPO_ROOT / "tests"))
sys.path.insert(0, str(REPO_ROOT))

from coordination import record as coordination_record  # noqa: E402
from local_request import store as request_store  # noqa: E402
from local_request import surface as surface_module  # noqa: E402
from mission import decision as mission_decision  # noqa: E402
from mission import record as mission_record  # noqa: E402
from mission import service as mission_service  # noqa: E402
from mission import store as mission_store  # noqa: E402
from mission_panel import journal as journal_module  # noqa: E402
from mission_panel import service as panel_module  # noqa: E402
from mission_sources import contract as sources_contract  # noqa: E402
from mission_sources import sources as sources_module  # noqa: E402

WATCHDOG_SECONDS = 60
SYNTHETIC_TRANSPORT = "synthetic_transport"
KEY = "k" * 64
# The new names' expected values, stated here and never read from the product under test.
ATTESTED_OUTCOME = "mission_attested_outcome"
CORE_REASON_PROBLEM = "mission_change_request_reason"
SURFACE_REASON_PROBLEM = "local_request_change_request_reason"
ENTRY_POINTS = ("apply_operator_attested_decline", "apply_operator_attested_rejection",
                "apply_operator_attested_change_request")
SURFACE_OPERATIONS = ("attest_decline", "attest_rejection", "attest_change_request")
PANEL_OPERATIONS = ("decline", "reject", "request_changes")
OUTCOMES = (  # entry point, recorded decision, resulting state, surface operation, surface kind, panel operation
    ("apply_operator_attested_decline", "DENY", "DENIED", "attest_decline", "decline", "decline"),
    ("apply_operator_attested_rejection", "REJECT", "REJECTED", "attest_rejection", "reject", "reject"),
    ("apply_operator_attested_change_request", "REQUEST_CHANGES", "CHANGES_REQUESTED",
     "attest_change_request", "request_changes", "request_changes"),
)
REASON = "Narrow the scope to the readiness probe.\nAdd a regression test for the flake."
BAD_REASONS = ("", "   ", "\n\t", None, 5, ["a reason"], "x" * 2001, "a\x00b")
RUN_TRANSITIONS_BEFORE = frozenset((
    ("AUTHORIZED", "RUNNING"), ("AUTHORIZED", "BLOCKED"), ("AUTHORIZED", "CANCELLED"),
    ("RUNNING", "COMPLETED"), ("RUNNING", "BLOCKED"), ("RUNNING", "CANCELLED"),
))


class Clock(object):
    def __init__(self, start=1_000_000):
        self.now = start

    def __call__(self):
        return self.now


def core_proposal(**overrides):
    """Mission Core's proposal keys (the core accepts the absence of a proof contract)."""
    base = {
        "objective": "Investigate and resolve the flaky readiness probe",
        "target_context": "control repository, readiness subsystem",
        "repository_url": "https://github.com/Example/Repo",
        "requested_scope": "readiness probe and its tests",
        "requested_action_scope": [
            mission_record.ACTION_SCOPE_ENGINEERING_CHANGE,
            mission_record.ACTION_SCOPE_REPOSITORY_READ,
        ],
        "requested_delivery_target": None,
    }
    base.update(overrides)
    return base


def surface_request(**overrides):
    """The local request surface's closed request: Mission Core's keys and a complete proof contract."""
    base = core_proposal(proof_contract={
        "requirements": [{
            "key": "tests_pass", "description": "the focused suite passes",
            "evidence_kinds": [mission_record.EVIDENCE_KIND_VERIFICATION_RECORD],
            "required_artifact_keys": [], "max_evidence_age_seconds": 3600,
        }],
        "required_artifacts": [], "required_dependencies": [],
        "required_resource_readiness": [],
        "degradation_policy": {"permitted_blocker_keys": []},
        "continuation_budget": {"max_attempts": 1, "max_checkpoints": 2},
    })
    base.update(overrides)
    return base


def read_bytes(path):
    return open(path, "rb").read() if os.path.exists(path) else None


class Contained(unittest.TestCase):
    """A SIGALRM watchdog (the termination rule); in cleanup, no blocked event or raiser was reached."""

    def setUp(self):
        def expired(signum, frame):
            raise AssertionError("watchdog: test exceeded %d s" % WATCHDOG_SECONDS)
        previous = signal.signal(signal.SIGALRM, expired)
        signal.alarm(WATCHDOG_SECONDS)
        self.addCleanup(signal.signal, signal.SIGALRM, previous)
        self.addCleanup(signal.alarm, 0)
        self.addCleanup(self.assert_contained)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = os.path.realpath(self.tmp.name)
        self.clock = Clock()
        self.store = mission_store.MissionStore(os.path.join(self.root, "mission"))
        self.missions = mission_service.MissionService(self.store, self.clock)
        self.attested = mission_record.OperatorAttestedContext(
            transport=SYNTHETIC_TRANSPORT, principal_ref="outer_operator_relay")
        self.unauth = mission_record.AuthenticatedContext(
            transport=SYNTHETIC_TRANSPORT,
            principal_kind=mission_record.PRINCIPAL_KIND_UNAUTHENTICATED_LOCAL_CALLER,
            principal_ref="unauthenticated")
        self.authenticated = mission_record.AuthenticatedContext(
            transport=SYNTHETIC_TRANSPORT,
            principal_kind=mission_record.PRINCIPAL_KIND_CONNECTOR_CREDENTIAL,
            principal_ref="1", configured_subject=None)

    def assert_contained(self):
        self.assertEqual(REACHED, [], "a blocked event or raiser was reached")

    def need(self, owner, name):
        """The named entry point, as a callable; its absence FAILS (an assertion), never errors."""
        value = getattr(owner, name, None)
        self.assertTrue(callable(value), "%s.%s does not exist" % (type(owner).__name__, name))
        return value

    def mission_bytes(self):
        return read_bytes(self.store.path)

    def record_of(self, mission_id):
        return self.missions.get(mission_id)["record"]

    def surface_proposal(self):
        """A proposal the LOCAL REQUEST SURFACE made (the unauthenticated local caller kind), with a withdrawal key."""
        request_id = self.missions.mint_request_id(self.unauth)
        created = self.missions.propose(
            request_id, core_proposal(), self.unauth, mission_record.withdrawal_key_digest(KEY),
            self.clock() + mission_record.WITHDRAWAL_KEY_VALIDITY_SECONDS)
        entry = self.record_of(created["mission_id"])["revisions"][-1]
        return created["mission_id"], request_id, entry

    def outcome(self, name, mission_id, entry, decision_id=None, reason=REASON, **changes):
        call = self.need(self.missions, name)
        decision_id = decision_id or self.missions.mint_decision_id(self.attested)
        args = dict(context=self.attested, decision_id=decision_id, mission_id=mission_id,
                    revision=entry["revision"], proposal_digest_sha256=entry["proposal_digest_sha256"])
        if name == "apply_operator_attested_change_request":
            args["reason"] = reason
        args.update(changes)
        return call(**args), decision_id

    def attested_approval(self, mission_id, entry, decision_id=None):
        return self.missions.apply_operator_attested_approval(
            self.attested, decision_id or self.missions.mint_decision_id(self.attested), mission_id,
            entry["revision"], entry["proposal_digest_sha256"],
            list(entry["proposal"]["requested_action_scope"]), [], self.clock() + 600)

    def refuses(self, problem, call, *args, **kwargs):
        with self.assertRaises(mission_record.MissionError) as caught:
            call(*args, **kwargs)
        self.assertEqual(caught.exception.problem, problem)
        return caught.exception

    def refused_unchanged(self, problem, call):
        """Refused with exactly ``problem``, and the WHOLE Mission store's bytes are what they were."""
        before = self.mission_bytes()
        self.assertIsNotNone(before)
        self.refuses(problem, call)
        self.assertEqual(self.mission_bytes(), before)


class VocabularyTests(Contained):

    def test_the_vocabulary_grows_by_exactly_the_named_outcomes_and_the_envelope_does_not(self):
        self.assertEqual(mission_decision.DECISIONS, ("APPROVE", "EDIT", "DENY"))
        self.assertEqual(getattr(mission_decision, "DECISION_REJECT", None), "REJECT")
        self.assertEqual(getattr(mission_decision, "DECISION_REQUEST_CHANGES", None), "REQUEST_CHANGES")
        self.assertEqual(getattr(mission_record, "STATE_REJECTED", None), "REJECTED")
        self.assertEqual(getattr(mission_record, "STATE_CHANGES_REQUESTED", None), "CHANGES_REQUESTED")
        self.assertEqual(getattr(mission_record, "MAX_CHANGE_REQUEST_REASON_CHARS", None), 2000)
        self.assertEqual(getattr(mission_record, "PROBLEM_ATTESTED_OUTCOME", None), ATTESTED_OUTCOME)
        self.assertEqual(getattr(mission_record, "PROBLEM_CHANGE_REQUEST_REASON", None), CORE_REASON_PROBLEM)
        self.assertEqual(getattr(surface_module, "PROBLEM_CHANGE_REQUEST_REASON", None), SURFACE_REASON_PROBLEM)

    def test_reject_is_terminal_and_deny_and_request_changes_are_not(self):
        self.assertEqual(mission_record.TERMINAL_STATES, ("BLOCKED", "COMPLETED", "CANCELLED", "REJECTED"))
        for state in ("REJECTED", "CHANGES_REQUESTED"):
            self.assertIn(state, mission_record.MISSION_STATES)
            self.assertIn(state, mission_record.REACHABLE_STATES)
        transitions = mission_record.ALLOWED_TRANSITIONS
        self.assertEqual(transitions["AWAITING_DECISION"],
                         frozenset(("AUTHORIZED", "DENIED", "REJECTED", "CHANGES_REQUESTED")))
        self.assertEqual(transitions.get("REJECTED"), frozenset())
        self.assertEqual(transitions.get("CHANGES_REQUESTED"), frozenset(("AWAITING_DECISION",)))
        self.assertEqual(transitions["DENIED"], frozenset(("AWAITING_DECISION",)))
        self.assertEqual({source for source, targets in transitions.items() if "REJECTED" in targets},
                         {"AWAITING_DECISION"})
        self.assertEqual({source for source, targets in transitions.items() if "CHANGES_REQUESTED" in targets},
                         {"AWAITING_DECISION"})
        self.assertEqual(mission_record.RUN_TRANSITIONS, RUN_TRANSITIONS_BEFORE)


class CoreOutcomeTests(Contained):

    def test_each_outcome_records_its_kind_and_state_with_attested_provenance_and_issues_no_authority(self):
        self.assertEqual(len(OUTCOMES), 3)
        for name, decision, state, _, _, _ in OUTCOMES:
            mission_id, _, entry = self.surface_proposal()
            out, decision_id = self.outcome(name, mission_id, entry)
            self.assertEqual((out["decision"], out["state"], out["idempotent"]), (decision, state, False), name)
            got = self.missions.get(mission_id)
            self.assertEqual(got["record"]["state"], state, name)
            self.assertEqual((got["authorizations"], got["live_authorization_id"]), ([], None), name)
            self.assertIsNone(got["record"].get("withdrawal"), name)
            decisions = got["record"]["decisions"]
            self.assertEqual([d["decision_id"] for d in decisions], [decision_id], name)
            expected_keys = set(mission_decision.DECISION_KEYS) | (
                {"reason"} if decision == "REQUEST_CHANGES" else set())
            self.assertEqual(set(decisions[0]), expected_keys, name)
            self.assertEqual((decisions[0]["decision"], decisions[0]["revision"]), (decision, 1), name)
            provenance = decisions[0]["provenance"]
            self.assertEqual(provenance["principal_kind"], mission_record.PRINCIPAL_KIND_OPERATOR_ATTESTED, name)
            self.assertEqual(provenance["proof"], mission_record.PROOF_OPERATOR_ATTESTED, name)
            # the authority ledger: a decline records the existing DENIED entry; the two new outcomes record none
            ledger = [(e["kind"], e["decision_id"], e["authorization_id"])
                      for e in self.store.load()["authority_ledger"] if e["mission_id"] == mission_id]
            self.assertEqual(ledger, [("DENIED", decision_id, None)] if decision == "DENY" else [], name)
            # durable: a fresh service over the same store reads it back through the store's validation
            reopened = mission_service.MissionService(mission_store.MissionStore(self.store.directory), self.clock)
            self.assertEqual(reopened.get(mission_id)["record"]["decisions"], decisions, name)

    def test_decline_is_the_existing_non_terminal_deny_and_reject_is_reachable_only_from_awaiting(self):
        mission_id, request_id, entry = self.surface_proposal()
        self.outcome("apply_operator_attested_decline", mission_id, entry)
        self.assertNotIn("DENIED", mission_record.TERMINAL_STATES)
        self.assertIn("AWAITING_DECISION", mission_record.ALLOWED_TRANSITIONS["DENIED"])
        # nothing but a new revision leaves DENIED: no outcome, no approval, no withdrawal
        self.assertEqual(len(ENTRY_POINTS), 3)
        for name in ENTRY_POINTS:
            decision_id = self.missions.mint_decision_id(self.attested)
            self.refused_unchanged(mission_record.PROBLEM_INVALID_TRANSITION,
                                   lambda n=name: self.outcome(n, mission_id, entry, decision_id=decision_id))
        decision_id = self.missions.mint_decision_id(self.attested)
        self.refused_unchanged(mission_record.PROBLEM_INVALID_TRANSITION,
                               lambda: self.attested_approval(mission_id, entry, decision_id))
        self.refused_unchanged(mission_service.PROBLEM_NOT_WITHDRAWABLE,
                               lambda: self.missions.withdraw_proposal(mission_id, request_id, KEY, self.unauth))
        self.assertEqual(self.record_of(mission_id)["state"], "DENIED")

    def test_reject_is_terminal_and_takes_nothing_after_it(self):
        mission_id, request_id, entry = self.surface_proposal()
        self.outcome("apply_operator_attested_rejection", mission_id, entry)
        self.assertEqual(len(ENTRY_POINTS), 3)
        for name in ENTRY_POINTS:
            decision_id = self.missions.mint_decision_id(self.attested)
            self.refused_unchanged(mission_record.PROBLEM_MISSION_TERMINAL,
                                   lambda n=name: self.outcome(n, mission_id, entry, decision_id=decision_id))
        decision_id = self.missions.mint_decision_id(self.attested)
        self.refused_unchanged(mission_record.PROBLEM_MISSION_TERMINAL,
                               lambda: self.attested_approval(mission_id, entry, decision_id))
        decision_id = self.missions.mint_decision_id(self.authenticated)
        self.refused_unchanged(mission_record.PROBLEM_MISSION_TERMINAL,
                               lambda: self.missions.edit(mission_id, 1, core_proposal(), decision_id,
                                                          self.authenticated))
        self.refused_unchanged(mission_service.PROBLEM_NOT_WITHDRAWABLE,
                               lambda: self.missions.withdraw_proposal(mission_id, request_id, KEY, self.unauth))
        self.assertEqual(self.record_of(mission_id)["state"], "REJECTED")

    def test_request_changes_keeps_the_reason_verbatim_and_only_a_revision_reopens_the_mission(self):
        mission_id, request_id, entry = self.surface_proposal()
        spaced = "  " + REASON + "  "
        self.outcome("apply_operator_attested_change_request", mission_id, entry, reason=spaced)
        self.assertEqual(self.record_of(mission_id)["decisions"][0]["reason"], spaced)
        self.assertNotIn("CHANGES_REQUESTED", mission_record.TERMINAL_STATES)
        # the unchanged proposal takes no approval and no further outcome
        decision_id = self.missions.mint_decision_id(self.attested)
        self.refused_unchanged(mission_record.PROBLEM_INVALID_TRANSITION,
                               lambda: self.attested_approval(mission_id, entry, decision_id))
        self.assertEqual(len(ENTRY_POINTS), 3)
        for name in ENTRY_POINTS:
            decision_id = self.missions.mint_decision_id(self.attested)
            self.refused_unchanged(mission_record.PROBLEM_INVALID_TRANSITION,
                                   lambda n=name: self.outcome(n, mission_id, entry, decision_id=decision_id))
        # withdrawal is untouched: a proposal with a decision cannot be withdrawn
        self.refused_unchanged(mission_service.PROBLEM_NOT_WITHDRAWABLE,
                               lambda: self.missions.withdraw_proposal(mission_id, request_id, KEY, self.unauth))
        # only a new revision returns it to AWAITING_DECISION: the existing EDIT, by an authenticated principal
        decision_id = self.missions.mint_decision_id(self.authenticated)
        edited = self.missions.edit(mission_id, 1, core_proposal(requested_scope="the probe only"),
                                    decision_id, self.authenticated)
        self.assertEqual((edited["state"], edited["revision"]), ("AWAITING_DECISION", 2))
        record = self.record_of(mission_id)
        self.assertEqual([d["decision"] for d in record["decisions"]], ["REQUEST_CHANGES", "EDIT"])
        self.assertEqual(record["decisions"][0]["reason"], spaced)

    def test_a_change_request_needs_a_readable_reason_and_nothing_is_written_without_one(self):
        self.assertEqual(len(BAD_REASONS), 8)
        for bad in BAD_REASONS:
            mission_id, _, entry = self.surface_proposal()
            decision_id = self.missions.mint_decision_id(self.attested)
            self.refused_unchanged(CORE_REASON_PROBLEM,
                                   lambda: self.outcome("apply_operator_attested_change_request", mission_id,
                                                        entry, decision_id=decision_id, reason=bad))
            self.assertEqual(self.record_of(mission_id)["state"], "AWAITING_DECISION", repr(bad))
        mission_id, _, entry = self.surface_proposal()
        longest = "y" * 2000
        out, _ = self.outcome("apply_operator_attested_change_request", mission_id, entry, reason=longest)
        self.assertEqual(out["state"], "CHANGES_REQUESTED")
        self.assertEqual(self.record_of(mission_id)["decisions"][0]["reason"], longest)

    def test_each_outcome_binds_the_exact_revision_and_digest_the_person_saw(self):
        self.assertEqual(len(ENTRY_POINTS), 3)
        for name in ENTRY_POINTS:
            mission_id, _, entry = self.surface_proposal()
            decision_id = self.missions.mint_decision_id(self.attested)
            self.refused_unchanged(mission_service.PROBLEM_PROPOSAL_DIGEST_MISMATCH,
                                   lambda n=name: self.outcome(n, mission_id, entry, decision_id=decision_id,
                                                               proposal_digest_sha256="e" * 64))
            self.refused_unchanged(mission_service.PROBLEM_STALE_REVISION,
                                   lambda n=name: self.outcome(n, mission_id, entry, decision_id=decision_id,
                                                               revision=2))
            self.assertEqual(self.record_of(mission_id)["decisions"], [], name)

    def test_a_replay_returns_the_recorded_outcome_and_any_other_use_of_the_id_refuses(self):
        self.assertEqual(len(ENTRY_POINTS), 3)
        for name in ENTRY_POINTS:
            mission_id, _, entry = self.surface_proposal()
            first, decision_id = self.outcome(name, mission_id, entry)
            before = self.mission_bytes()
            again, _ = self.outcome(name, mission_id, entry, decision_id=decision_id)
            self.assertEqual((again["decision"], again["state"], again["idempotent"]),
                             (first["decision"], first["state"], True), name)
            self.assertEqual(self.mission_bytes(), before, name)
        # the same id with different content: another reason, or another outcome
        mission_id, _, entry = self.surface_proposal()
        _, decision_id = self.outcome("apply_operator_attested_change_request", mission_id, entry)
        self.refused_unchanged(mission_service.PROBLEM_DECISION_ID_CONFLICT,
                               lambda: self.outcome("apply_operator_attested_change_request", mission_id, entry,
                                                    decision_id=decision_id, reason="A different reason."))
        self.refused_unchanged(mission_service.PROBLEM_DECISION_ID_CONFLICT,
                               lambda: self.outcome("apply_operator_attested_decline", mission_id, entry,
                                                    decision_id=decision_id))
        # an id reserved for another principal, and an id this layer never issued
        mission_id, _, entry = self.surface_proposal()
        other = self.missions.mint_decision_id(self.authenticated)
        self.refused_unchanged(mission_service.PROBLEM_DECISION_CONTEXT_CONFLICT,
                               lambda: self.outcome("apply_operator_attested_rejection", mission_id, entry,
                                                    decision_id=other))
        forged = other[:-1] + ("0" if other[-1] != "0" else "1")
        self.refused_unchanged(mission_service.PROBLEM_UNKNOWN_DECISION_ID,
                               lambda: self.outcome("apply_operator_attested_rejection", mission_id, entry,
                                                    decision_id=forged))
        self.assertEqual(self.record_of(mission_id)["decisions"], [])

    def test_an_applied_decision_id_reused_with_another_digest_refuses_for_each_outcome(self):
        # Round 61, blocker 1: the digest the person saw is part of what a decision id binds, on a REPLAY as on first use.
        # Two other digests: one no proposal has, and the real digest of a different proposal.
        others = ("e" * 64, mission_record.proposal_digest(
            mission_record.validate_proposal(core_proposal(requested_scope="a different scope"))))
        self.assertEqual(len(others), 2)
        self.assertEqual(len(ENTRY_POINTS), 3)
        for name in ENTRY_POINTS:
            mission_id, _, entry = self.surface_proposal()
            first, decision_id = self.outcome(name, mission_id, entry)
            for digest in others:
                self.assertNotEqual(digest, entry["proposal_digest_sha256"], name)
                self.refused_unchanged(mission_service.PROBLEM_DECISION_ID_CONFLICT,
                                       lambda n=name, d=digest: self.outcome(n, mission_id, entry,
                                                                             decision_id=decision_id,
                                                                             proposal_digest_sha256=d))
            # the exact replay still returns the recorded outcome, and the record still holds one decision
            again, _ = self.outcome(name, mission_id, entry, decision_id=decision_id)
            self.assertEqual((again["state"], again["idempotent"]), (first["state"], True), name)
            self.assertEqual(len(self.record_of(mission_id)["decisions"]), 1, name)

    def test_only_a_local_surface_proposal_and_only_an_operator_attested_context(self):
        request_id = self.missions.mint_request_id(self.authenticated)
        created = self.missions.propose(request_id, core_proposal(), self.authenticated)
        entry = self.record_of(created["mission_id"])["revisions"][-1]
        self.assertEqual(len(ENTRY_POINTS), 3)
        for name in ENTRY_POINTS:
            decision_id = self.missions.mint_decision_id(self.attested)
            self.refused_unchanged(ATTESTED_OUTCOME,
                                   lambda n=name: self.outcome(n, created["mission_id"], entry,
                                                               decision_id=decision_id))
        mission_id, _, entry = self.surface_proposal()
        contexts = (self.unauth, self.authenticated)
        self.assertEqual(len(contexts), 2)
        for name in ENTRY_POINTS:
            for context in contexts:
                decision_id = self.missions.mint_decision_id(self.attested)
                # the existing attestation check, reused unchanged, refuses any other context type
                self.refused_unchanged(mission_record.PROBLEM_ATTESTED_APPROVAL,
                                       lambda n=name, c=context: self.outcome(n, mission_id, entry,
                                                                              decision_id=decision_id, context=c))
        self.assertEqual(self.record_of(mission_id)["decisions"], [])

    def test_apply_human_decision_is_not_broadened_and_attested_approve_is_unchanged(self):
        mission_id, _, entry = self.surface_proposal()
        # test_O4, kept: attested DENY and EDIT through the general decision path still refuse
        cases = ((mission_decision.DECISION_DENY, {}),
                 (mission_decision.DECISION_EDIT, dict(proposal=core_proposal())))
        self.assertEqual(len(cases), 2)
        for decision, extra in cases:
            decision_id = self.missions.mint_decision_id(self.attested)
            self.refused_unchanged(
                mission_record.PROBLEM_ATTESTED_APPROVAL,
                lambda d=decision, x=extra: self.missions.apply_human_decision(
                    mission_decision.HumanDecisionEnvelope(
                        context=self.attested, decision_id=decision_id, mission_id=mission_id,
                        revision=1, decision=d, received_at=self.clock(), **x)))
        # the new kinds are not decisions of the general path
        kinds = ("REJECT", "REQUEST_CHANGES")
        self.assertEqual(len(kinds), 2)
        for kind in kinds:
            decision_id = self.missions.mint_decision_id(self.authenticated)
            self.refused_unchanged(
                mission_decision.PROBLEM_DECISION,
                lambda k=kind: self.missions.apply_human_decision(
                    mission_decision.HumanDecisionEnvelope(
                        context=self.authenticated, decision_id=decision_id, mission_id=mission_id,
                        revision=1, decision=k, received_at=self.clock())))
        self.assertEqual(self.record_of(mission_id)["decisions"], [])
        out = self.attested_approval(mission_id, entry)
        self.assertEqual((out["decision"], out["state"]), ("APPROVE", "AUTHORIZED"))

    def test_a_historical_deny_keeps_its_meaning_and_its_record(self):
        request_id = self.missions.mint_request_id(self.authenticated)
        created = self.missions.propose(request_id, core_proposal(), self.authenticated)
        mission_id = created["mission_id"]
        self.missions.apply_human_decision(mission_decision.HumanDecisionEnvelope(
            context=self.authenticated, decision_id=self.missions.mint_decision_id(self.authenticated),
            mission_id=mission_id, revision=1, decision=mission_decision.DECISION_DENY,
            received_at=self.clock()))
        reopened = mission_service.MissionService(mission_store.MissionStore(self.store.directory), self.clock)
        record = reopened.get(mission_id)["record"]
        self.assertEqual(record["state"], "DENIED")
        self.assertEqual([d["decision"] for d in record["decisions"]], ["DENY"])
        self.assertEqual(set(record["decisions"][0]), set(mission_decision.DECISION_KEYS))
        self.assertEqual(record["decisions"][0]["provenance"]["principal_kind"],
                         mission_record.PRINCIPAL_KIND_CONNECTOR_CREDENTIAL)
        # and it still reopens through a new revision
        edited = reopened.edit(mission_id, 1, core_proposal(requested_scope="the probe only"),
                               reopened.mint_decision_id(self.authenticated), self.authenticated)
        self.assertEqual(edited["state"], "AWAITING_DECISION")

    def test_the_stored_record_validator_admits_exactly_these_outcomes(self):
        validate = mission_decision.validate_decision_record
        stored = {}
        self.assertEqual(len(OUTCOMES), 3)
        for name, decision, _, _, _, _ in OUTCOMES:
            mission_id, _, entry = self.surface_proposal()
            self.outcome(name, mission_id, entry)
            stored[decision] = self.record_of(mission_id)["decisions"][0]
        self.assertEqual(sorted(stored), ["DENY", "REJECT", "REQUEST_CHANGES"])
        for decision, value in sorted(stored.items()):
            self.assertEqual(validate(copy.deepcopy(value)), value, decision)
        # a change request without a readable reason is not a change request
        mutations = (lambda v: v.pop("reason"), lambda v: v.update(reason=""), lambda v: v.update(reason=7))
        self.assertEqual(len(mutations), 3)
        for mutate in mutations:
            value = copy.deepcopy(stored["REQUEST_CHANGES"])
            mutate(value)
            self.refuses(CORE_REASON_PROBLEM, validate, value)
        # no other decision carries a reason
        for decision in ("DENY", "REJECT"):
            value = copy.deepcopy(stored[decision])
            value["reason"] = REASON
            self.refuses(mission_record.PROBLEM_UNKNOWN_KEY, validate, value)
        # the attestation still never carries an EDIT
        value = copy.deepcopy(stored["DENY"])
        value["decision"] = "EDIT"
        value["proposal_digest_sha256"] = "e" * 64
        self.refuses(mission_record.PROBLEM_ATTESTED_APPROVAL, validate, value)

    def test_each_entry_point_has_one_production_caller_and_the_panel_names_none(self):
        counts = {}
        roots = ("mission", "local_request", "mission_panel", "mission_sources", "target_runtime")
        files = sorted(p for root in roots for p in (REPO_ROOT / root).glob("*.py"))
        self.assertGreater(len(files), 20)
        for path in files:
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                names = []
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    names.append(node.name)
                elif isinstance(node, ast.Attribute):
                    names.append(node.attr)
                elif isinstance(node, ast.Name):
                    names.append(node.id)
                elif isinstance(node, ast.Constant) and isinstance(node.value, str):
                    names.append(node.value)
                for name in names:
                    if name in ENTRY_POINTS:
                        key = (name, path.relative_to(REPO_ROOT).as_posix())
                        counts[key] = counts.get(key, 0) + 1
        expected = {}
        for name in ENTRY_POINTS:
            expected[(name, "mission/service.py")] = 1
            expected[(name, "local_request/surface.py")] = 1
        self.assertEqual(counts, expected)


class SurfaceOutcomeTests(Contained):

    def setUp(self):
        super(SurfaceOutcomeTests, self).setUp()
        self.requests = request_store.LocalRequestStore(os.path.join(self.root, "requests"))
        self.surface = surface_module.LocalRequestSurface(self.missions, self.requests, self.clock)

    def submitted(self, **overrides):
        return self.surface.submit(surface_request(**overrides))

    def both_bytes(self):
        return self.mission_bytes(), read_bytes(self.requests.path)

    def attest(self, name, out, reason=REASON, **changes):
        call = self.need(self.surface, name)
        args = dict(request_ref=out["request_ref"], mission_id=out["mission_id"], revision=out["revision"],
                    proposal_digest_sha256=out["proposal_digest_sha256"])
        if name == "attest_change_request":
            args["reason"] = reason
        args["relay_ref"] = "relay-1"
        args.update(changes)
        return call(**args)

    def surface_refuses(self, problem, call):
        """Refused with exactly ``problem``, and neither store's bytes changed."""
        before = self.both_bytes()
        with self.assertRaises(surface_module.LocalRequestRefusal) as caught:
            call()
        self.assertEqual(caught.exception.problem, problem)
        self.assertEqual(self.both_bytes(), before)

    def state_of(self, out):
        return self.record_of(out["mission_id"])["state"]

    def approve(self, out):
        return self.surface.attest_approval(
            out["request_ref"], out["mission_id"], out["revision"], out["proposal_digest_sha256"],
            sorted(out["proposal"]["requested_action_scope"]), [], self.clock() + 600, "approved", "relay-1")

    def test_each_outcome_is_applied_through_mission_core_and_recorded_on_the_request(self):
        self.assertEqual(len(OUTCOMES), 3)
        for _, decision, state, operation, kind, _ in OUTCOMES:
            out = self.submitted()
            first = self.attest(operation, out)
            self.assertEqual((first["ok"], first["idempotent"]), (True, False), operation)
            self.assertEqual(self.state_of(out), state, operation)
            decisions = self.record_of(out["mission_id"])["decisions"]
            self.assertEqual([d["decision"] for d in decisions], [decision], operation)
            recorded = self.surface.status(out["request_ref"]).get("attested_outcome")
            self.assertIsNotNone(recorded, operation)
            self.assertEqual((recorded["state"], recorded["kind"], recorded["decision_id"]),
                             (request_store.ATTESTED_APPLIED, kind, decisions[0]["decision_id"]), operation)
            if kind == "request_changes":
                self.assertEqual((recorded["reason"], decisions[0]["reason"]), (REASON, REASON))
            again = self.attest(operation, out)
            self.assertEqual(again["idempotent"], True, operation)
            self.assertEqual(len(self.record_of(out["mission_id"])["decisions"]), 1, operation)

    def test_an_explicit_target_and_the_bound_mission_and_proposal_are_required(self):
        self.assertEqual(len(SURFACE_OPERATIONS), 3)
        for name in SURFACE_OPERATIONS:
            out, other = self.submitted(), self.submitted()
            self.surface_refuses(surface_module.PROBLEM_AMBIGUOUS, lambda n=name: self.attest(n, out, request_ref=""))
            self.surface_refuses(surface_module.PROBLEM_MISATTRIBUTED,
                                 lambda n=name: self.attest(n, out, mission_id=other["mission_id"]))
            self.surface_refuses(surface_module.PROBLEM_BINDING_MISMATCH,
                                 lambda n=name: self.attest(n, out, proposal_digest_sha256="e" * 64))
            self.surface_refuses(surface_module.PROBLEM_BINDING_MISMATCH,
                                 lambda n=name: self.attest(n, out, revision=2))
            self.assertEqual(self.state_of(out), "AWAITING_DECISION", name)

    def test_a_repeat_of_an_applied_outcome_with_another_digest_refuses_for_each_outcome(self):
        # Round 61, blocker 1, at the surface: a repeat returns the recorded outcome only for the SAME binding.
        self.assertEqual(len(SURFACE_OPERATIONS), 3)
        for name in SURFACE_OPERATIONS:
            out = self.submitted()
            self.attest(name, out)
            self.surface_refuses(surface_module.PROBLEM_ATTESTATION_CONFLICT,
                                 lambda n=name: self.attest(n, out, proposal_digest_sha256="e" * 64))
            self.assertEqual(self.attest(name, out)["idempotent"], True, name)

    def test_one_outcome_per_request_and_approval_cannot_follow_one(self):
        out = self.submitted()
        self.attest("attest_decline", out)
        for name in ("attest_rejection", "attest_change_request"):
            self.surface_refuses(surface_module.PROBLEM_ATTESTATION_CONFLICT, lambda n=name: self.attest(n, out))
        # the attested approval, unchanged, finds the proposal no longer pending
        self.surface_refuses(surface_module.PROBLEM_BINDING_MISMATCH, lambda: self.approve(out))
        self.assertEqual(self.state_of(out), "DENIED")
        self.assertEqual(len(self.record_of(out["mission_id"])["decisions"]), 1)

    def test_an_unreadable_reason_is_refused_before_anything_is_written(self):
        self.assertEqual(len(BAD_REASONS), 8)
        for bad in BAD_REASONS:
            out = self.submitted()
            self.surface_refuses(SURFACE_REASON_PROBLEM,
                                 lambda: self.attest("attest_change_request", out, reason=bad))
            self.assertIsNone(self.surface.status(out["request_ref"]).get("attested_outcome"), repr(bad))

    def test_after_request_changes_a_revised_proposal_goes_through_the_existing_flow(self):
        out = self.submitted()
        self.attest("attest_change_request", out)
        # withdrawal is untouched: the proposer cannot withdraw a proposal that has a decision
        self.surface_refuses(surface_module.PROBLEM_OUT_OF_SCOPE,
                             lambda: self.surface.cancel(out["request_ref"], out["control_capability"]))
        revised = self.submitted(requested_scope="the readiness probe only")
        self.assertNotEqual(revised["mission_id"], out["mission_id"])
        self.assertEqual(self.state_of(revised), "AWAITING_DECISION")
        self.approve(revised)
        self.assertEqual(self.state_of(revised), "AUTHORIZED")
        old = self.record_of(out["mission_id"])
        self.assertEqual((old["state"], old.get("withdrawal")), ("CHANGES_REQUESTED", None))
        self.assertEqual(old["decisions"][0]["reason"], REASON)

    def test_a_decline_is_not_a_withdrawal(self):
        out = self.submitted()
        self.attest("attest_decline", out)
        self.surface_refuses(surface_module.PROBLEM_OUT_OF_SCOPE,
                             lambda: self.surface.cancel(out["request_ref"], out["control_capability"]))
        self.assertIsNone(self.record_of(out["mission_id"]).get("withdrawal"))
        self.assertEqual(self.surface.status(out["request_ref"])["surface_state"], request_store.STATE_OPEN)

    def test_approval_is_unchanged_and_no_outcome_follows_it(self):
        out = self.submitted()
        self.approve(out)
        self.assertEqual(self.state_of(out), "AUTHORIZED")
        self.assertEqual(len(SURFACE_OPERATIONS), 3)
        for name in SURFACE_OPERATIONS:
            self.surface_refuses(surface_module.PROBLEM_ATTESTATION_CONFLICT, lambda n=name: self.attest(n, out))
        self.assertEqual(self.state_of(out), "AUTHORIZED")


class PanelOutcomeTests(Contained):

    def setUp(self):
        super(PanelOutcomeTests, self).setUp()
        self.requests = request_store.LocalRequestStore(os.path.join(self.root, "requests"))
        self.surface = surface_module.LocalRequestSurface(self.missions, self.requests, self.clock)
        panel_dir = os.path.join(self.root, "panel")
        os.makedirs(panel_dir, mode=0o700)
        self.journal = journal_module.PanelJournal(panel_dir)
        # decisions use the surface, the journal and the clock only; no conversation, no registry
        self.panel = panel_module.PanelService(self.surface, self.journal, None, None, self.clock)

    def offered(self):
        out = self.surface.submit(surface_request())
        return out, self.panel.decision_offer(out["request_ref"])

    def all_bytes(self):
        return self.mission_bytes(), read_bytes(self.requests.path), read_bytes(self.journal.path)

    def relay(self, name, offer, reason=REASON, **changes):
        call = self.need(self.panel, name)
        args = dict(request_ref=offer["request_ref"], mission_id=offer["mission_id"], revision=offer["revision"],
                    proposal_digest_sha256=offer["proposal_digest_sha256"],
                    offer_digest_sha256=offer["offer_digest_sha256"], expires_at=offer["expires_at"],
                    action_scope=offer["approved_action_scope"], delivery_targets=offer["approved_delivery_targets"],
                    repository_url=offer["repository_url"])
        if name == "request_changes":
            args["reason"] = reason
        args.update(changes)
        return call(**args)

    def state_of(self, offer):
        return self.record_of(offer["mission_id"])["state"]

    def refused(self, exception, problem, call):
        before = self.all_bytes()
        with self.assertRaises(exception) as caught:
            call()
        self.assertEqual(caught.exception.problem, problem)
        self.assertEqual(self.all_bytes(), before)

    def test_each_outcome_is_relayed_on_exactly_the_offer_shown(self):
        self.assertEqual(len(OUTCOMES), 3)
        for _, decision, state, _, _, operation in OUTCOMES:
            _, offer = self.offered()
            result = self.relay(operation, offer)
            self.assertEqual(result["ok"], True, operation)
            self.assertEqual(self.state_of(offer), state, operation)
            self.assertEqual([d["decision"] for d in self.record_of(offer["mission_id"])["decisions"]],
                             [decision], operation)

    def test_a_tampered_or_stale_offer_refuses_and_decides_nothing(self):
        changes = (dict(repository_url="https://github.com/Example/Other"),
                   dict(proposal_digest_sha256="e" * 64), dict(revision=2))
        self.assertEqual((len(PANEL_OPERATIONS), len(changes)), (3, 3))
        for name in PANEL_OPERATIONS:
            _, offer = self.offered()
            for change in changes:
                self.refused(panel_module.PanelRefusal, panel_module.PROBLEM_STALE_DISPLAY,
                             lambda n=name, c=change: self.relay(n, offer, **c))
            self.assertEqual(self.state_of(offer), "AWAITING_DECISION", name)

    def test_request_changes_without_a_readable_reason_is_refused_before_any_write(self):
        self.assertEqual(len(BAD_REASONS), 8)
        for bad in BAD_REASONS:
            _, offer = self.offered()
            self.refused(surface_module.LocalRequestRefusal, SURFACE_REASON_PROBLEM,
                         lambda: self.relay("request_changes", offer, reason=bad))

    def test_after_a_rejection_the_approval_on_the_same_offer_cannot_decide(self):
        _, offer = self.offered()
        self.relay("reject", offer)
        self.refused(panel_module.PanelRefusal, panel_module.PROBLEM_STALE_DISPLAY,
                     lambda: self.panel.decide(offer["request_ref"], offer["mission_id"], offer["revision"],
                                               offer["proposal_digest_sha256"], "approve",
                                               offer["offer_digest_sha256"], offer["expires_at"],
                                               offer["approved_action_scope"], offer["approved_delivery_targets"],
                                               offer["repository_url"]))
        self.assertEqual(self.state_of(offer), "REJECTED")

    def test_each_relay_makes_exactly_its_one_named_surface_call(self):
        effect = {"attest_approval", "run_command", "cancel", "submit", "advance"} | set(SURFACE_OPERATIONS)
        expected = {"decline": {"attest_decline"}, "reject": {"attest_rejection"},
                    "request_changes": {"attest_change_request"}}
        tree = ast.parse((REPO_ROOT / "mission_panel" / "service.py").read_text(encoding="utf-8"))
        functions = {n.name: n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}
        self.assertEqual(sorted(set(expected) & set(functions)), sorted(expected))
        for function, calls in sorted(expected.items()):
            made = {getattr(c.func, "attr", getattr(c.func, "id", None)) for c in ast.walk(functions[function])
                    if isinstance(c, ast.Call)} & effect
            self.assertEqual(made, calls, function)
        # no other function of the package makes any of the three
        callers = set()
        files = sorted((REPO_ROOT / "mission_panel").glob("*.py"))
        self.assertGreater(len(files), 5)
        for path in files:
            for fn in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                if not isinstance(fn, ast.FunctionDef):
                    continue
                for c in ast.walk(fn):
                    if isinstance(c, ast.Call) and getattr(c.func, "attr", None) in SURFACE_OPERATIONS:
                        callers.add((path.name, fn.name, c.func.attr))
        self.assertEqual(callers, {("service.py", "decline", "attest_decline"),
                                   ("service.py", "reject", "attest_rejection"),
                                   ("service.py", "request_changes", "attest_change_request")})


class ConsumerTests(Contained):

    def test_every_consumer_that_re_declares_the_states_names_the_two_new_ones(self):
        standing = sources_module.MISSION_CORE_STANDING
        self.assertEqual(set(standing), set(mission_record.MISSION_STATES))
        self.assertEqual(standing.get("REJECTED"), sources_contract.STANDING_TERMINAL)
        self.assertEqual(standing.get("CHANGES_REQUESTED"), sources_contract.STANDING_INACTIVE)
        self.assertEqual(standing.get("DENIED"), sources_contract.STANDING_INACTIVE)
        self.assertEqual(coordination_record.LIFECYCLE_STATES, mission_record.MISSION_STATES)
        # the progression stops re-reading a Mission in these; parsed, because the bridge imports the run machinery
        tree = ast.parse((REPO_ROOT / "target_runtime" / "mission_bridge.py").read_text(encoding="utf-8"))
        assigned = [n for n in tree.body if isinstance(n, ast.Assign)
                    and [getattr(t, "id", None) for t in n.targets] == ["TERMINAL_MISSION_STATES"]]
        self.assertEqual(len(assigned), 1)
        self.assertIsInstance(assigned[0].value, ast.Tuple)
        named = [e.attr for e in assigned[0].value.elts if isinstance(e, ast.Attribute)]
        self.assertEqual(len(named), len(assigned[0].value.elts))
        self.assertEqual(sorted(named), sorted((
            "STATE_COMPLETED", "STATE_BLOCKED", "STATE_CLOSED", "STATE_CANCELLED", "STATE_DENIED",
            "STATE_REJECTED", "STATE_CHANGES_REQUESTED")))


class ApprovalReplayDigestTests(Contained):
    """Owed item 8: an applied APPROVE replayed under its decision id with a presented proposal digest is answered
    only when that digest is the one the approval RECORDED; any other refuses, and nothing is written. The decision
    digest's composition is unchanged (it never held the presented digest), the replay stays bound to the recorded
    Mission and revision through it, and a caller that presents no digest (the documented authenticated option) is
    answered exactly as before. The attested approval itself is unchanged in every other rule."""

    def attest(self, mission_id, entry, decision_id, digest):
        return self.missions.apply_operator_attested_approval(
            self.attested, decision_id, mission_id, entry["revision"], digest,
            list(entry["proposal"]["requested_action_scope"]), [], self.clock() + 600)

    def test_an_applied_attested_approval_id_reused_with_another_digest_refuses_and_writes_nothing(self):
        mission_id, _, entry = self.surface_proposal()
        other_id = self.missions.propose(
            self.missions.mint_request_id(self.unauth), core_proposal(requested_scope="a different scope"),
            self.unauth)["mission_id"]
        other = self.record_of(other_id)["revisions"][-1]["proposal_digest_sha256"]
        self.assertNotEqual(other, entry["proposal_digest_sha256"])
        decision_id = self.missions.mint_decision_id(self.attested)
        first = self.attest(mission_id, entry, decision_id, entry["proposal_digest_sha256"])
        self.assertFalse(first["idempotent"])
        others = ("e" * 64, other)  # one no proposal has, and the real digest of another stored proposal
        self.assertEqual(others, ("e" * 64, other))
        for digest in others:
            with self.subTest(digest=digest):
                self.refused_unchanged(mission_service.PROBLEM_DECISION_ID_CONFLICT,
                                       lambda d=digest: self.attest(mission_id, entry, decision_id, d))
        again = self.attest(mission_id, entry, decision_id, entry["proposal_digest_sha256"])
        self.assertTrue(again["idempotent"])
        self.assertEqual(again["authorization_id"], first["authorization_id"])
        document = self.store.load()
        self.assertEqual(list(document["authorizations"]), [first["authorization_id"]])
        self.assertEqual([e["kind"] for e in document["authority_ledger"] if e["mission_id"] == mission_id],
                         ["ISSUED"])

    def test_an_authenticated_approval_replay_may_omit_the_digest_and_a_presented_one_is_compared_with_the_recorded(self):
        mission_id = self.missions.propose(
            self.missions.mint_request_id(self.authenticated), core_proposal(), self.authenticated)["mission_id"]
        approved = self.record_of(mission_id)["revisions"][-1]
        decision_id = self.missions.mint_decision_id(self.authenticated)

        def envelope(**extra):
            return mission_decision.HumanDecisionEnvelope(
                context=self.authenticated, decision_id=decision_id, mission_id=mission_id, revision=1,
                decision=mission_decision.DECISION_APPROVE, received_at=self.clock(),
                approved_action_scope=sorted(approved["proposal"]["requested_action_scope"]),
                approved_delivery_targets=[], **extra)

        first = self.missions.apply_human_decision(envelope())
        self.assertFalse(first["idempotent"])
        # The Mission moves on: an EDIT makes revision 2, whose real digest differs from the approved one.
        self.missions.edit(mission_id, 1, core_proposal(requested_scope="a different scope"),
                           self.missions.mint_decision_id(self.authenticated), self.authenticated)
        current = self.record_of(mission_id)["revisions"][-1]
        self.assertEqual(current["revision"], 2)
        self.assertNotEqual(current["proposal_digest_sha256"], approved["proposal_digest_sha256"])
        # No digest presented (the documented option), and the digest the approval recorded: both are answered
        # with the recorded outcome, though the current revision is another one.
        answered = ({}, {"expected_proposal_digest_sha256": approved["proposal_digest_sha256"]})
        self.assertEqual(len(answered), 2)
        for extra in answered:
            with self.subTest(extra=extra):
                again = self.missions.apply_human_decision(envelope(**extra))
                self.assertTrue(again["idempotent"])
                self.assertEqual(again["authorization_id"], first["authorization_id"])
        # Any other presented digest refuses with nothing written: here the CURRENT revision's real digest.
        self.refused_unchanged(
            mission_service.PROBLEM_DECISION_ID_CONFLICT,
            lambda: self.missions.apply_human_decision(
                envelope(expected_proposal_digest_sha256=current["proposal_digest_sha256"])))


class ContainmentTests(Contained):

    def test_no_other_test_module_was_imported_and_the_raisers_are_in_place(self):
        here = sys.modules[__name__]
        others = []
        for name, module in list(sys.modules.items()):
            path = getattr(module, "__file__", None)
            if module is here or not path:
                continue
            if os.path.dirname(os.path.realpath(path)) == TESTS_DIR:
                others.append(name)
        self.assertEqual(others, [])
        self.assertIs(socket.socket, _RefusedSocket)
        self.assertIs(subprocess.Popen, _RefusedPopen)
        self.assertEqual(os.fork.refused_name, "os.fork")
        self.assertEqual(os.posix_spawn.refused_name, "os.posix_spawn")
        self.assertEqual(len(EXEC_NAMES), 8)
        for name in EXEC_NAMES:
            self.assertEqual(getattr(os, name).refused_name, "os." + name)

    def test_the_audit_hook_raises_on_a_blocked_event_without_doing_it(self):
        events = ("socket.__new__", "subprocess.Popen", "os.fork")
        self.assertEqual(len(events), 3)
        for event in events:
            with self.assertRaises(RuntimeError):
                sys.audit(event)
            self.assertEqual(REACHED.pop(), event)


if __name__ == "__main__":
    unittest.main()
