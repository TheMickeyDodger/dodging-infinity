"""Tests for the local operator request surface (``local_request``).

Every test drives the REAL surface over the REAL Mission Core in a
temporary protected directory. What is SYNTHETIC is labelled so:

- ``SYNTHETIC_AUTHENTICATED`` is a connector-credential context used
  ONLY to stand in for a future authenticated decision route acting
  directly on Mission Core (to make a Mission authorized, edited or
  otherwise not the surface caller's own pending proposal). It never
  passes through the surface, and it is not, and is not evidence of, any
  live transport, Dots or otherwise.
- "Caller A" and "caller B" are two surface instances over one state
  directory: two local processes. Nothing here simulates a phone, a
  vendor session or an authenticated principal.

No test here is evidence of live Dots behaviour.

Termination rule (CONTRIBUTING.md): every child interpreter carries an
independent ``timeout``; nothing else blocks.
"""

import ast
import contextlib
import io
import json
import os
import signal
import subprocess
import sys
import tempfile
import tokenize
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from capability import contract as capability_contract  # noqa: E402
from mission import decision as mission_decision  # noqa: E402
from mission import record as mission_record  # noqa: E402
from mission import service as mission_service  # noqa: E402
from mission import store as mission_store  # noqa: E402

from local_request import cli as cli_module  # noqa: E402
from local_request import store as store_module  # noqa: E402
from local_request import surface as surface_module  # noqa: E402

PACKAGE_DIR = REPO_ROOT / "local_request"
ENTRY_SCRIPT = REPO_ROOT / "direquest.py"
CHILD_TIMEOUT_SECONDS = 120
NOW = 1_000_000

SYNTHETIC_AUTHENTICATED = mission_record.AuthenticatedContext(
    transport="synthetic_transport",
    principal_kind=mission_record.PRINCIPAL_KIND_CONNECTOR_CREDENTIAL,
    principal_ref="1",
)

# Roots no module of the surface may import: every execution, dispatch,
# transport, delivery and orchestration package, and process / network
# machinery. Shared by the static pin and the fresh-interpreter probe.
NO_DISPATCH_ROOTS = (
    "herdr", "herdctl", "target_runtime", "codex_gateway", "telegram_operator",
    "operator_session", "human_interaction", "pr_delivery", "worker",
    "durable_execution", "coordination", "dirun", "tgop", "codexgw",
    "subprocess", "socket", "http", "urllib", "ssl", "multiprocessing",
    "asyncio", "concurrent", "threading", "signal", "ctypes",
)
ALLOWED_IMPORT_ROOTS = frozenset({
    "argparse", "copy", "hashlib", "hmac", "json", "os", "secrets", "stat",
    "sys", "time", "capability", "mission", "workflow_authority",
    "local_request",
})
ALLOWED_WORKFLOW_AUTHORITY_MODULES = frozenset({
    "workflow_authority.atomic", "workflow_authority.digest",
})


def contract():
    return {
        "requirements": [{
            "key": "tests_pass", "description": "the focused suite passes",
            "evidence_kinds": [mission_record.EVIDENCE_KIND_VERIFICATION_RECORD],
            "required_artifact_keys": [], "max_evidence_age_seconds": 3600,
        }],
        "required_artifacts": [], "required_dependencies": [],
        "required_resource_readiness": [],
        "degradation_policy": {"permitted_blocker_keys": []},
        "continuation_budget": {"max_attempts": 1, "max_checkpoints": 2},
    }


def request(**overrides):
    base = {
        "objective": "Investigate and resolve the flaky readiness probe",
        "target_context": "control repository, readiness subsystem",
        "repository_url": "https://github.com/Example/Repo",
        "requested_scope": "readiness probe and its tests; no schema change",
        "requested_action_scope": [
            mission_record.ACTION_SCOPE_ENGINEERING_CHANGE,
            mission_record.ACTION_SCOPE_REPOSITORY_READ,
        ],
        "requested_delivery_target": None,
        "proof_contract": contract(),
    }
    base.update(overrides)
    return base


class Clock(object):
    def __init__(self, now=NOW):
        self.now = now

    def __call__(self):
        return self.now


class LostResponseService(object):
    """Mission Core whose ``propose`` COMMITS and then raises, once: the
    response of a committed proposal is lost. Synthetic fault injection
    around the real service."""

    def __init__(self, real):
        self._real = real
        self.armed = True

    def __getattr__(self, name):
        return getattr(self._real, name)

    def propose(self, *args):
        outcome = self._real.propose(*args)
        if self.armed:
            self.armed = False
            raise OSError("synthetic: response lost after the commit")
        return outcome


WATCHDOG_SECONDS = 60


class Bounded(unittest.TestCase):
    """Independent termination bound (CONTRIBUTING.md termination rule):
    a SIGALRM watchdog that raises inside a hung lock wait or loop, so a
    broken operation fails the test fast instead of hanging the suite. It
    does not depend on the code under test to return."""

    def setUp(self):
        def expired(signum, frame):
            raise TimeoutError("watchdog: test exceeded %d s" % WATCHDOG_SECONDS)
        previous = signal.signal(signal.SIGALRM, expired)
        signal.alarm(WATCHDOG_SECONDS)
        self.addCleanup(signal.signal, signal.SIGALRM, previous)
        self.addCleanup(signal.alarm, 0)


class Fixture(Bounded):

    def setUp(self):
        super(Fixture, self).setUp()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.state = os.path.join(self.tmp.name, "state")
        self.clock = Clock()
        self.missions = mission_service.MissionService(
            mission_store.MissionStore(self.state), self.clock)
        self.surface = self.surface_for(self.missions)

    def surface_for(self, missions):
        return surface_module.LocalRequestSurface(
            missions, store_module.LocalRequestStore(self.state), self.clock)

    def fresh_surface(self):
        """A new process with no prior context: built from the state
        directory alone."""
        return cli_module.build_surface(self.state, self.clock)

    def file_bytes(self, name):
        path = os.path.join(self.state, name)
        if not os.path.exists(path):
            return None
        with open(path, "rb") as handle:
            return handle.read()

    def mission_bytes(self):
        return self.file_bytes("missions.json")

    def surface_bytes(self):
        return self.file_bytes("local_requests.json")

    def refused(self, problem, callable_, *args):
        with self.assertRaises(surface_module.LocalRequestRefusal) as caught:
            callable_(*args)
        self.assertEqual(caught.exception.problem, problem, caught.exception.reason)
        return caught.exception

    def authenticated_approve(self, mission_id, revision):
        """SYNTHETIC stand-in for a future authenticated decision route,
        acting directly on Mission Core (never through the surface)."""
        current = self.missions.get(mission_id)["record"]["revisions"][-1]
        return self.missions.apply_human_decision(
            mission_decision.HumanDecisionEnvelope(
                context=SYNTHETIC_AUTHENTICATED,
                decision_id=self.missions.mint_decision_id(SYNTHETIC_AUTHENTICATED),
                mission_id=mission_id, revision=revision,
                decision=mission_decision.DECISION_APPROVE,
                received_at=self.clock(),
                approved_action_scope=current["proposal"]["requested_action_scope"],
                approved_delivery_targets=[]))

    def authenticated_edit(self, mission_id, revision, **overrides):
        """SYNTHETIC stand-in, as above, for an authenticated EDIT."""
        return self.missions.edit(
            mission_id, revision, request(**overrides),
            self.missions.mint_decision_id(SYNTHETIC_AUTHENTICATED),
            SYNTHETIC_AUTHENTICATED)


# ====================================================================
# A. Request -> bounded proposal -> durable status, end to end
# ====================================================================


class ANormalFlowTests(Fixture):

    def test_A1_request_becomes_a_bounded_revisioned_proposal(self):
        out = self.surface.submit(request())
        self.assertTrue(out["ok"])
        self.assertEqual(out["revision"], 1)
        self.assertEqual(out["mission_state"], "AWAITING_DECISION")
        self.assertIsNone(store_module.request_ref_problem(out["request_ref"]))
        self.assertIsNone(mission_record.id_problem(out["mission_id"], "mn"))
        # The proposal is Mission Core's own, with its own digest: scope,
        # constraints, evidence requirements, budget and allowed effects.
        clean = mission_record.validate_proposal(request())
        self.assertEqual(out["proposal"], clean)
        self.assertEqual(out["proposal_digest_sha256"],
                         mission_record.proposal_digest(clean))
        self.assertEqual(out["proposal"]["proof_contract"]["continuation_budget"],
                         {"max_attempts": 1, "max_checkpoints": 2})
        self.assertEqual(out["delivery_authority"], "none")
        self.assertEqual(out["dispatch"], "none")
        self.assertEqual(out["approval"]["problem"],
                         "local_request_approval_unauthenticated")
        self.assertTrue(out["control_capability"].startswith("lc-"))
        # Recorded truthfully in Mission Core: unauthenticated, no proof.
        stored = self.missions.get(out["mission_id"])
        provenance = stored["record"]["revisions"][0]["provenance"]
        self.assertEqual(provenance["transport"], "local_request")
        self.assertEqual(provenance["principal_kind"],
                         "unauthenticated_local_caller")
        self.assertEqual(provenance["proof"], "none_unauthenticated")
        self.assertEqual(stored["authorizations"], [])

    def test_A2_status_needs_no_prior_context_and_is_read_only(self):
        out = self.surface.submit(request())
        before = (self.mission_bytes(), self.surface_bytes())
        status = self.fresh_surface().status(out["request_ref"])
        self.assertEqual((self.mission_bytes(), self.surface_bytes()), before)
        self.assertEqual(status["surface_state"], "OPEN")
        self.assertEqual(status["mission_id"], out["mission_id"])
        self.assertEqual(status["proposal_digest_sha256"],
                         out["proposal_digest_sha256"])
        self.assertEqual(status["mission_observation"],
                         self.missions.observe(out["mission_id"]))
        self.assertEqual(status["mission_observation"]["phase"]["value"]["state"],
                         "AWAITING_DECISION")
        self.assertNotIn("control_capability", json.dumps(status))
        self.assertNotIn(out["control_capability"], json.dumps(status))

    def test_A3_the_control_capability_is_never_stored(self):
        out = self.surface.submit(request())
        self.assertNotIn(out["control_capability"].encode(), self.surface_bytes())
        self.assertNotIn(out["control_capability"].encode(), self.mission_bytes())
        self.assertIn(store_module.token_digest(out["control_capability"]).encode(),
                      self.surface_bytes())


# ====================================================================
# B. Approval FAILS CLOSED (the key test)
# ====================================================================


class BApprovalFailsClosedTests(Fixture):

    def assertApprovalRefusal(self, refusal):
        self.assertFalse(refusal["ok"])
        self.assertEqual(refusal["status"], "refused")
        self.assertEqual(refusal["problem"], "local_request_approval_unauthenticated")
        self.assertEqual(refusal["mission_problem"],
                         "mission_unauthenticated_principal")
        self.assertEqual(refusal["missing"], [
            "authenticated_per_message_principal_assertion",
            "exact_human_approval_event_not_mintable_by_a_model",
        ])
        self.assertEqual(refusal["reason"], "approval refused: "
                         + mission_record.UNAUTHENTICATED_REFUSAL_DETAIL)
        self.assertIn("whose account, not whose intent", refusal["reason"])
        self.assertEqual(refusal["delivery_authority"], "none")

    def test_B1_an_exactly_bound_approval_is_refused_and_recorded(self):
        out = self.surface.submit(request())
        before = self.mission_bytes()
        refusal = self.surface.approve(out["request_ref"], 1,
                                       out["proposal_digest_sha256"])
        self.assertApprovalRefusal(refusal)
        self.assertEqual(refusal["binding"], "matches_current_revision")
        self.assertTrue(refusal["recorded"])
        # Mission Core is untouched: no authorization, no ledger, no state.
        self.assertEqual(self.mission_bytes(), before)
        stored = self.missions.get(out["mission_id"])
        self.assertEqual(stored["record"]["state"], "AWAITING_DECISION")
        self.assertEqual(stored["authorizations"], [])
        # Durable: a new process reads the refusal back.
        status = self.fresh_surface().status(out["request_ref"])
        self.assertEqual(len(status["approval_refusals"]), 1)
        self.assertEqual(status["approval_refusals"][0]["problem"],
                         "local_request_approval_unauthenticated")

    def test_B2_every_approval_input_is_refused_and_mission_core_never_moves(self):
        out = self.surface.submit(request())
        before = self.mission_bytes()
        digest = out["proposal_digest_sha256"]
        for revision, attempt_digest, expires_at in (
            (1, digest, None), (1, digest, NOW + 3600), (1, digest, NOW),
            (2, digest, None), (1, "0" * 64, None), (99, "f" * 64, NOW + 1),
        ):
            with self.subTest(revision=revision, expires_at=expires_at):
                self.assertApprovalRefusal(self.surface.approve(
                    out["request_ref"], revision, attempt_digest, expires_at))
        self.assertEqual(self.mission_bytes(), before)
        self.assertEqual(self.missions.get(out["mission_id"])["authorizations"], [])

    def test_B3_duplicate_decision_is_recorded_once(self):
        out = self.surface.submit(request())
        first = self.surface.approve(out["request_ref"], 1,
                                     out["proposal_digest_sha256"])
        after_first = self.surface_bytes()
        second = self.surface.approve(out["request_ref"], 1,
                                      out["proposal_digest_sha256"])
        self.assertApprovalRefusal(second)
        self.assertFalse(first["duplicate"])
        self.assertTrue(second["duplicate"])
        self.assertEqual(self.surface_bytes(), after_first)

    def test_B4_stale_decision_against_a_superseded_revision(self):
        out = self.surface.submit(request())
        self.authenticated_edit(out["mission_id"], 1, objective="Narrower")
        refusal = self.surface.approve(out["request_ref"], 1,
                                       out["proposal_digest_sha256"])
        self.assertApprovalRefusal(refusal)
        self.assertEqual(refusal["binding"], "stale_revision")

    def test_B5_altered_proposal_digest(self):
        out = self.surface.submit(request())
        refusal = self.surface.approve(out["request_ref"], 1, "a" * 64)
        self.assertApprovalRefusal(refusal)
        self.assertEqual(refusal["binding"], "proposal_digest_mismatch")

    def test_B6_expired_decision(self):
        out = self.surface.submit(request())
        refusal = self.surface.approve(out["request_ref"], 1,
                                       out["proposal_digest_sha256"], NOW - 1)
        self.assertApprovalRefusal(refusal)
        self.assertEqual(refusal["binding"], "expired")

    def test_B7_unauthorized_caller_cannot_assert_identity_or_approval(self):
        # Closed request: no field can name a principal, an approval, a
        # control action or a dispatch. Nothing is written for any of them.
        for field, value in (
            ("principal", "me"), ("principal_kind", "local_process_user"),
            ("transport", "telegram"), ("context", {"principal_ref": "1"}),
            ("approved", True), ("approval", {"revision": 1}),
            ("authorization_id", "ma-" + "0" * 32), ("human", True),
            ("dispatch", True), ("herdr", {"task": "x"}), ("cancel", "mn-x"),
        ):
            with self.subTest(field=field):
                self.refused("local_request_unknown_field", self.surface.submit,
                             request(**{field: value}))
        self.assertIsNone(self.mission_bytes())
        self.assertIsNone(self.surface_bytes())
        self.assertFalse(os.path.exists(self.state))

    def test_B8_the_approval_path_names_no_mission_decision_api(self):
        # A bypass would have to call one of these; none appears anywhere
        # in the surface, so adding one fails this pin (the core refuses
        # the surface's context regardless: test_mission_core U*).
        # Task 8 (user decision): the operator-attested path now reserves a
        # decision id, under its OperatorAttestedContext only, and applies it
        # through apply_operator_attested_approval. Everything else that could
        # decide or authorize stays banned here.
        banned = (
            "apply_human_decision", "mint_state_operation_id",
            "HumanDecisionEnvelope", "DECISION_APPROVE", "DECISION_EDIT",
            "DECISION_DENY", "issue_mission_authorization", "edit",
            "PRINCIPAL_KIND_LOCAL_PROCESS_USER",
            "PRINCIPAL_KIND_CONNECTOR_CREDENTIAL", "AUTHENTICATED_PRINCIPAL_KINDS",
            "environ", "getenv", "putenv",
        )
        for path in sorted(PACKAGE_DIR.glob("*.py")) + [ENTRY_SCRIPT]:
            source = path.read_text()
            for token in tokenize.generate_tokens(io.StringIO(source).readline):
                if token.type == tokenize.NAME:
                    self.assertNotIn(token.string, banned, (path.name, token.start))

    def test_B9_the_one_context_is_built_from_constants_only(self):
        constructions = []
        for path in sorted(PACKAGE_DIR.glob("*.py")) + [ENTRY_SCRIPT]:
            for node in ast.walk(ast.parse(path.read_text())):
                if isinstance(node, ast.Call) and getattr(
                    node.func, "attr", getattr(node.func, "id", None)
                ) == "AuthenticatedContext":
                    constructions.append((path.name, node))
        self.assertEqual([name for name, _ in constructions], ["surface.py"])
        call = constructions[0][1]
        self.assertEqual(call.args, [])
        values = {k.arg: k.value for k in call.keywords}
        self.assertEqual(sorted(values), ["principal_kind", "principal_ref",
                                          "transport"])
        self.assertEqual(ast.dump(values["principal_kind"]), ast.dump(ast.parse(
            "mission_record.PRINCIPAL_KIND_UNAUTHENTICATED_LOCAL_CALLER",
            mode="eval").body))
        for name in ("transport", "principal_ref"):
            self.assertIsInstance(values[name], ast.Name)
        self.assertEqual(surface_module.LOCAL_CALLER_CONTEXT.as_dict(), {
            "transport": "local_request",
            "principal_kind": "unauthenticated_local_caller",
            "principal_ref": "unauthenticated", "configured_subject": None})
        # Task 8: exactly one OperatorAttestedContext, from constants, and it
        # is NOT an AuthenticatedContext.
        attested = []
        for path in sorted(PACKAGE_DIR.glob("*.py")) + [ENTRY_SCRIPT]:
            for node in ast.walk(ast.parse(path.read_text())):
                if isinstance(node, ast.Call) and getattr(
                    node.func, "attr", getattr(node.func, "id", None)
                ) == "OperatorAttestedContext":
                    attested.append((path.name, node))
        self.assertEqual([name for name, _ in attested], ["surface.py"])
        self.assertEqual(sorted(k.arg for k in attested[0][1].keywords),
                         ["principal_ref", "transport"])
        for keyword in attested[0][1].keywords:
            self.assertIsInstance(keyword.value, ast.Name)
        self.assertNotIsInstance(surface_module.OPERATOR_ATTESTED_CONTEXT,
                                 mission_record.AuthenticatedContext)
        self.assertEqual(surface_module.OPERATOR_ATTESTED_CONTEXT.as_dict(), {
            "transport": "local_request",
            "principal_kind": "operator_attested_relay",
            "principal_ref": "outer_operator_relay", "configured_subject": None})

    def test_B10_no_flag_or_environment_grants_anything(self):
        out = self.surface.submit(request())
        for argv in (["approve", out["request_ref"], "--revision", "1",
                      "--proposal-digest", out["proposal_digest_sha256"],
                      "--as-human"],
                     ["approve", out["request_ref"], "--revision", "1",
                      "--proposal-digest", out["proposal_digest_sha256"],
                      "--principal", "me"],
                     ["--approve", "propose"]):
            with self.subTest(argv=argv):
                with contextlib.redirect_stderr(io.StringIO()) as usage:
                    code = cli_module.main(["--state-dir", self.state] + argv,
                                           io.StringIO(""), io.StringIO(),
                                           clock=self.clock)
                self.assertEqual(code, cli_module.EXIT_USAGE)
                self.assertIn("unrecognized arguments", usage.getvalue())
        saved = dict(os.environ)
        try:
            for key in ("DI_APPROVE", "DI_PRINCIPAL", "DI_AUTHENTICATED",
                        "HUMAN_APPROVER", "DEBUG"):
                os.environ[key] = "1"
            stdout = io.StringIO()
            code = cli_module.main(
                ["--state-dir", self.state, "approve", out["request_ref"],
                 "--revision", "1", "--proposal-digest",
                 out["proposal_digest_sha256"]],
                io.StringIO(""), stdout, clock=self.clock)
        finally:
            os.environ.clear()
            os.environ.update(saved)
        self.assertEqual(code, cli_module.EXIT_REFUSED)
        self.assertEqual(json.loads(stdout.getvalue())["problem"],
                         "local_request_approval_unauthenticated")


# ====================================================================
# C. Uncertainty and recovery; missing machine or session
# ====================================================================


class CRecoveryTests(Fixture):

    def test_C1_a_lost_proposal_response_is_recovered_exactly_once(self):
        lossy = LostResponseService(self.missions)
        surface = self.surface_for(lossy)
        with self.assertRaises(OSError):
            surface.submit(request())
        document = json.loads(self.surface_bytes())
        (request_ref,) = document["requests"]
        status = self.fresh_surface().status(request_ref)
        self.assertIsNone(status["mission_id"])
        self.assertEqual(status["mission_observation_problem"],
                         "local_request_proposal_unconfirmed")
        # Uncertain approval: still refused, classified as unconfirmed.
        refusal = self.surface.approve(request_ref, 1, "0" * 64)
        self.assertEqual(refusal["problem"], "local_request_approval_unauthenticated")
        self.assertEqual(refusal["binding"], "proposal_unconfirmed")
        self.refused("local_request_proposal_unconfirmed", self.surface.cancel,
                     request_ref, "lc-" + "0" * 64)
        recovered = self.fresh_surface().recover(request_ref)
        again = self.fresh_surface().recover(request_ref)
        self.assertEqual(recovered["mission_id"], again["mission_id"])
        self.assertEqual(len(self.missions._store.load()["missions"]), 1)
        self.assertEqual(self.fresh_surface().status(request_ref)["mission_id"],
                         recovered["mission_id"])

    def test_C2_missing_machine_or_session_refuses_without_creating_state(self):
        for unknown in ("lr-" + "0" * 32, "mn-" + "0" * 32, "", "../etc"):
            with self.subTest(unknown=unknown):
                self.refused("local_request_unknown_request",
                             self.fresh_surface().status, unknown)
        self.assertFalse(os.path.exists(self.state))
        out = self.surface.submit(request())
        # The Mission store is gone (a different machine, or a reset):
        # the surface reports it unavailable, it does not invent status.
        os.remove(os.path.join(self.state, "missions.json"))
        status = self.fresh_surface().status(out["request_ref"])
        self.assertIsNone(status["mission_observation"])
        self.assertEqual(status["mission_observation_problem"],
                         "local_request_mission_unavailable")
        refusal = self.surface.approve(out["request_ref"], 1,
                                       out["proposal_digest_sha256"])
        self.assertEqual(refusal["binding"], "mission_unavailable")
        self.refused("local_request_mission_unavailable", self.surface.cancel,
                     out["request_ref"], out["control_capability"])

    def test_C3_a_tampered_surface_document_fails_closed(self):
        out = self.surface.submit(request())
        document = json.loads(self.surface_bytes())
        document["requests"][out["request_ref"]]["state"] = "CANCELLED"
        with open(os.path.join(self.state, "local_requests.json"), "w") as handle:
            json.dump(document, handle)
        with self.assertRaises(store_module.LocalRequestStoreError):
            self.fresh_surface().status(out["request_ref"])


# ====================================================================
# D. Cancel: a durable terminal state, and a late write prevented
# ====================================================================


class DCancelTests(Fixture):

    def test_D1_late_writes_after_cancel_are_prevented(self):
        out = self.surface.submit(request())
        ref = out["request_ref"]
        done = self.surface.cancel(ref, out["control_capability"])
        self.assertEqual(done["surface_state"], "CANCELLED")
        missions_after, surface_after = self.mission_bytes(), self.surface_bytes()
        # Another process attempts every write path this surface has.
        late = self.fresh_surface()
        self.refused("local_request_cancelled", late.approve, ref, 1,
                     out["proposal_digest_sha256"])
        self.refused("local_request_cancelled", late.recover, ref)
        self.refused("local_request_cancelled", late.cancel, ref,
                     out["control_capability"])
        self.assertEqual(self.surface_bytes(), surface_after)
        self.assertEqual(self.mission_bytes(), missions_after)
        status = late.status(ref)
        self.assertEqual(status["surface_state"], "CANCELLED")
        self.assertEqual(status["cancellation"]["mission_id"], out["mission_id"])

    def test_D2_cancel_text_names_the_local_request_and_claims_nothing_more(self):
        out = self.surface.submit(request())
        result = self.surface.cancel(out["request_ref"], out["control_capability"])
        self.assertEqual(result["cancelled"], "local request %s" % out["request_ref"])
        self.assertEqual(result["mission_state"], "AWAITING_DECISION")
        effect = result["effect"]
        self.assertIn("local request %s is CANCELLED" % out["request_ref"], effect)
        self.assertIn("withdrawal marker", effect)
        self.assertIn("still AWAITING_DECISION", effect)
        self.assertIn("stopped and changed no running work", effect)
        for overclaim in ("Mission was cancelled", "Mission is cancelled",
                          "invalidated the Mission", "Herdr work stopped",
                          "stopped the work"):
            self.assertNotIn(overclaim, effect)
        status = self.fresh_surface().status(out["request_ref"])
        self.assertEqual(status["effect"], effect)
        self.assertTrue(status["mission_decisions_refused_by_withdrawal"])

    def test_D3_a_crash_between_the_two_writes_completes_on_retry(self):
        out = self.surface.submit(request())
        real = self.missions.withdraw_proposal
        calls = []

        def commit_then_crash(*args):
            calls.append(real(*args))
            raise OSError("synthetic: crashed after Mission Core committed")

        self.missions.withdraw_proposal = commit_then_crash
        with self.assertRaises(OSError):
            self.surface.cancel(out["request_ref"], out["control_capability"])
        del self.missions.withdraw_proposal
        # Mission Core holds the marker; the local request is still OPEN and
        # its capability unspent (nothing was saved on the surface side).
        self.assertIsNotNone(self.missions.get(out["mission_id"])["record"][
            "withdrawal"])
        self.assertEqual(self.surface.status(out["request_ref"])["surface_state"],
                         "OPEN")
        done = self.surface.cancel(out["request_ref"], out["control_capability"])
        self.assertEqual(done["surface_state"], "CANCELLED")
        self.assertEqual(done["mission_withdrawal"], calls[0]["withdrawal"])


class R4ReviewFindingTests(Fixture):
    """Round-4 findings 1, 3, 4 and 5 (finding 2 is direct-core:
    test_mission_core U16)."""

    def test_R4_1_interrupted_cancel_is_reported_and_completes_after_expiry(self):
        out = self.surface.submit(request())
        real = self.missions.withdraw_proposal

        def commit_then_crash(*args):
            real(*args)
            raise OSError("synthetic: crashed after Mission Core committed")

        self.missions.withdraw_proposal = commit_then_crash
        with self.assertRaises(OSError):
            self.surface.cancel(out["request_ref"], out["control_capability"])
        del self.missions.withdraw_proposal
        self.clock.now += store_module.CONTROL_CAPABILITY_VALIDITY_SECONDS + 1
        status = self.fresh_surface().status(out["request_ref"])
        self.assertEqual(status["surface_state"], "OPEN")
        self.assertEqual(status["local_cancellation"], "INCOMPLETE")
        self.assertIn("local cancellation INCOMPLETE", status["effect"])
        self.assertNotIn("is CANCELLED on this surface", status["effect"])
        before = self.surface_bytes()
        self.refused("local_request_cancellation_incomplete", self.surface.approve,
                     out["request_ref"], 1, out["proposal_digest_sha256"])
        self.assertEqual(self.surface_bytes(), before)
        # Completion after expiry with the same capability; a wrong one fails.
        self.refused("local_request_control_capability", self.surface.cancel,
                     out["request_ref"], "lc-" + "2" * 64)
        done = self.surface.cancel(out["request_ref"], out["control_capability"])
        self.assertEqual(done["surface_state"], "CANCELLED")
        status = self.fresh_surface().status(out["request_ref"])
        self.assertEqual(status["local_cancellation"], "COMPLETE")
        self.assertIn("is CANCELLED on this surface", status["effect"])

    def test_R4_3_absent_or_null_proof_contract_is_refused(self):
        absent = request()
        del absent["proof_contract"]
        for label, value in (("absent", absent),
                             ("null", request(proof_contract=None))):
            with self.subTest(label):
                self.refused("local_request_proof_contract_required",
                             self.surface.submit, value)
        self.assertIsNone(self.surface_bytes())
        self.assertIsNone(self.mission_bytes())
        # Mission Core's legacy compatibility is unchanged.
        legacy = self.missions.propose(
            self.missions.mint_request_id(SYNTHETIC_AUTHENTICATED), absent,
            SYNTHETIC_AUTHENTICATED)
        self.assertNotIn("proof_contract", legacy["proposal"])

    def test_R4_4_a_colliding_reference_minter_fails_fast(self):
        surface = surface_module.LocalRequestSurface(
            self.missions, store_module.LocalRequestStore(self.state), self.clock,
            mint_ref=lambda: "lr-" + "0" * 32)
        surface.submit(request())
        with self.assertRaises(store_module.LocalRequestStoreError):
            surface.submit(request())

    def test_R4_5_a_request_without_its_capability_is_malformed(self):
        out = self.surface.submit(request())
        document = json.loads(self.surface_bytes())
        document["control_capabilities"] = {}
        with self.assertRaises(store_module.LocalRequestStoreError):
            store_module.validate_document(document)
        with open(os.path.join(self.state, "local_requests.json"), "w") as handle:
            json.dump(document, handle)
        with self.assertRaises(store_module.LocalRequestStoreError):
            self.fresh_surface().recover(out["request_ref"])


class DelayedPropose(object):
    """SYNTHETIC delay around the real Mission Core ``propose``: the clock
    moves (and optionally the first call is interrupted BEFORE creation)
    between the surface minting the capability and the Mission's creation,
    so the two clocks never coincide."""

    def __init__(self, real, clock, delay, interrupt_first=False):
        self._real = real
        self._clock = clock
        self._delay = delay
        self._interrupt = interrupt_first

    def __getattr__(self, name):
        return getattr(self._real, name)

    def propose(self, *args):
        if self._interrupt:
            self._interrupt = False
            raise OSError("synthetic: interrupted before the Mission existed")
        self._clock.now += self._delay
        return self._real.propose(*args)


class R5OriginalExpiryTests(Fixture):
    """Round 5: Mission Core binds the capability's ORIGINAL expiry (mint
    time + validity), through delayed creation and recovery, never
    creation time + validity. Clocks deliberately do NOT coincide."""

    def assertBothPathsRefuse(self, out_ref, token, mission_id):
        before = json.loads(self.mission_bytes())["missions"][mission_id]
        error = self.refused("local_request_control_capability",
                             self.fresh_surface().cancel, out_ref, token)
        self.assertEqual(error.details["capability_problem"],
                         capability_contract.PROBLEM_CAPABILITY_EXPIRED)
        entry = json.loads(self.surface_bytes())["requests"][out_ref]
        with self.assertRaises(mission_record.MissionError) as caught:
            self.missions.withdraw_proposal(mission_id, entry["mission_request_id"],
                                            token, surface_module.LOCAL_CALLER_CONTEXT)
        self.assertEqual(caught.exception.problem, "mission_withdrawal_key_expired")
        after = json.loads(self.mission_bytes())["missions"][mission_id]
        self.assertEqual(after, before)
        self.assertNotIn("withdrawal", after)

    def test_R5_delayed_submit_binds_mint_time_expiry(self):
        minted_at = self.clock()
        surface = self.surface_for(DelayedPropose(self.missions, self.clock, 100))
        out = surface.submit(request())
        record_ = self.missions.get(out["mission_id"])["record"]
        original = minted_at + store_module.CONTROL_CAPABILITY_VALIDITY_SECONDS
        self.assertEqual(record_["created_at"], minted_at + 100)
        self.assertEqual(record_["withdrawal_key_expires_at"], original)
        self.clock.now = original
        self.assertBothPathsRefuse(out["request_ref"], out["control_capability"],
                                   out["mission_id"])

    def test_R5_delayed_recovery_refuses_the_true_key_after_original_expiry(self):
        # Same shape with the true key captured: interrupt AFTER the
        # capability is minted but before creation, by failing propose once,
        # and read the key from the surface's mint (synthetic capture).
        minted = []
        real_mint = store_module.ProposalControlAuthority.mint

        def capture(self_, *args):
            token = real_mint(self_, *args)
            minted.append(token)
            return token

        store_module.ProposalControlAuthority.mint = capture
        try:
            minted_at = self.clock()
            surface = self.surface_for(DelayedPropose(self.missions, self.clock, 0,
                                                      interrupt_first=True))
            with self.assertRaises(OSError):
                surface.submit(request())
        finally:
            store_module.ProposalControlAuthority.mint = real_mint
        (request_ref,) = json.loads(self.surface_bytes())["requests"]
        original = minted_at + store_module.CONTROL_CAPABILITY_VALIDITY_SECONDS
        self.clock.now = original + 1
        recovered = self.fresh_surface().recover(request_ref)
        record_ = self.missions.get(recovered["mission_id"])["record"]
        self.assertEqual(record_["created_at"], original + 1)
        self.assertEqual(record_["withdrawal_key_expires_at"], original)
        self.assertBothPathsRefuse(request_ref, minted[0], recovered["mission_id"])

    def test_R5_committed_before_original_expiry_completes_afterwards(self):
        minted_at = self.clock()
        surface = self.surface_for(DelayedPropose(self.missions, self.clock, 100))
        out = surface.submit(request())
        original = minted_at + store_module.CONTROL_CAPABILITY_VALIDITY_SECONDS
        self.clock.now = original - 1
        entry = json.loads(self.surface_bytes())["requests"][out["request_ref"]]
        first = self.missions.withdraw_proposal(
            out["mission_id"], entry["mission_request_id"],
            out["control_capability"], surface_module.LOCAL_CALLER_CONTEXT)
        self.assertFalse(first["idempotent"])
        self.clock.now = original + 1000
        done = self.fresh_surface().cancel(out["request_ref"],
                                           out["control_capability"])
        self.assertEqual(done["surface_state"], "CANCELLED")
        self.assertEqual(done["mission_withdrawal"], first["withdrawal"])


class PAttestedApprovalTests(Fixture):
    """Task 8, user decision: the Outer Operator relays the human's explicit
    "approved" reply as an OPERATOR ATTESTATION. Nothing here asserts that a
    human sent it; the tests pin the honest label, the residual same-user
    fabrication risk, exact binding, ambiguity refusal and once-only
    application. Synthetic throughout; not evidence of live Dots behaviour."""

    def binding(self, out, **changes):
        presented = self.surface.present(out["request_ref"])
        args = dict(
            request_ref=out["request_ref"], mission_id=presented["mission_id"],
            revision=presented["revision"],
            proposal_digest_sha256=presented["proposal_digest_sha256"],
            approved_action_scope=presented["approved_action_scope"],
            approved_delivery_targets=presented["approved_delivery_targets"],
            expires_at=self.clock() + 600, relayed_reply="approved",
            relay_ref="synthetic-session-ref",
        )
        args.update(changes)
        return args

    def authorizations(self):
        return json.loads(self.mission_bytes())["authorizations"]

    def test_P1_attested_approval_records_the_honest_label_and_risk(self):
        out = self.surface.submit(request())
        result = self.surface.attest_approval(**self.binding(out))
        self.assertEqual(result["status"], "approved_by_operator_attestation")
        self.assertEqual(result["provenance_label"], {
            "principal_kind": "operator_attested_relay",
            "proof": "operator_attested_not_independently_verified"})
        self.assertIn("could fabricate it", result["residual_risk"])
        principal = self.missions.get(out["mission_id"])["authorizations"][0][
            "human_principal"]
        self.assertEqual(principal["proof"],
                         "operator_attested_not_independently_verified")
        self.assertIsNone(principal["human_identity_proof"])
        status = self.fresh_surface().status(out["request_ref"])
        self.assertEqual(status["attested_approval"]["state"], "APPLIED")
        self.assertEqual(status["attested_approval"]["relayed_reply"], "approved")
        self.assertEqual(status["attested_approval"]["residual_risk"],
                         mission_record.OPERATOR_ATTESTED_RESIDUAL_RISK)
        self.assertEqual(len(self.authorizations()), 1)

    def test_P2_ordinary_approve_is_still_refused(self):
        out = self.surface.submit(request())
        refusal = self.surface.approve(out["request_ref"], 1,
                                       out["proposal_digest_sha256"])
        self.assertEqual(refusal["problem"], "local_request_approval_unauthenticated")
        self.assertEqual(self.authorizations(), {})

    def test_P3_stale_altered_scope_expired_refused_before_any_write(self):
        out = self.surface.submit(request())
        for changes in (dict(revision=2), dict(proposal_digest_sha256="a" * 64),
                        dict(approved_action_scope=["repository_read"]),
                        dict(expires_at=self.clock()),
                        dict(expires_at=self.clock() + 901)):
            with self.subTest(changes=changes):
                before = (self.mission_bytes(), self.surface_bytes())
                self.refused("local_request_binding_mismatch",
                             lambda: self.surface.attest_approval(
                                 **self.binding(out, **changes)))
                self.assertEqual((self.mission_bytes(), self.surface_bytes()),
                                 before)
        self.assertEqual(self.authorizations(), {})

    def test_P4_misattributed_and_cross_mission_are_refused(self):
        a = self.surface.submit(request(objective="A"))
        b = self.surface.submit(request(objective="B"))
        self.refused("local_request_misattributed",
                     lambda: self.surface.attest_approval(
                         **self.binding(a, mission_id=b["mission_id"])))
        self.refused("local_request_binding_mismatch",
                     lambda: self.surface.attest_approval(**self.binding(
                         a, proposal_digest_sha256=b["proposal_digest_sha256"])))
        self.assertEqual(self.authorizations(), {})

    def test_P5_ambiguity_requires_an_explicit_reference(self):
        self.refused("local_request_nothing_pending", self.surface.present)
        one = self.surface.submit(request(objective="one"))
        self.assertEqual(self.surface.present()["request_ref"], one["request_ref"])
        self.surface.submit(request(objective="two"))
        error = self.refused("local_request_ambiguous_reference",
                             self.surface.present)
        self.assertEqual(len(error.details["candidates"]), 2)

    def test_P6_duplicates_replay_and_restart_apply_once(self):
        out = self.surface.submit(request())
        args = self.binding(out)
        first = self.surface.attest_approval(**args)
        again = self.fresh_surface().attest_approval(**args)
        self.assertTrue(again["idempotent"])
        self.assertEqual(again["authorization_id"], first["authorization_id"])
        self.refused("local_request_attestation_conflict",
                     lambda: self.surface.attest_approval(
                         **dict(args, expires_at=args["expires_at"] - 1)))
        self.assertEqual(len(self.authorizations()), 1)
        ledger = json.loads(self.mission_bytes())["authority_ledger"]
        self.assertEqual([e["kind"] for e in ledger], ["ISSUED"])

    # P7 (round 12): an UNKNOWN outcome is a HOLD. A later call only
    # reconciles an EXISTING durable Mission Core outcome; absent or
    # unreadable keeps the HOLD, applies nothing and mints nothing.

    def attestation_state(self, out):
        return json.loads(self.surface_bytes())["requests"][out["request_ref"]][
            "attested_approval"]["state"]

    def assert_hold_refusal(self, surface, args):
        error = self.refused("local_request_attestation_hold",
                             lambda: surface.attest_approval(**args))
        self.assertIn("outcome is unknown", error.reason)
        self.assertIn("reconciliation", error.reason)
        for invitation in ("retry", "try again", "safe"):
            self.assertNotIn(invitation, error.reason.lower())
        return error

    def guarded_restart(self):
        """A fresh process whose Mission Core entry points record any call
        that would apply or mint, so a test can assert none happened."""
        fresh = self.fresh_surface()
        calls = []
        for name in ("apply_operator_attested_approval", "mint_decision_id"):
            real = getattr(fresh._missions, name)

            def recorder(*a, _name=name, _real=real):
                calls.append(_name)
                return _real(*a)
            setattr(fresh._missions, name, recorder)
        return fresh, calls

    def test_P7_failure_AFTER_commit_holds_then_reconciles_on_restart(self):
        out = self.surface.submit(request())
        real = self.missions.apply_operator_attested_approval

        def commit_then_fail(*a):
            real(*a)
            raise OSError("synthetic: I/O failed after Mission Core committed")

        self.missions.apply_operator_attested_approval = commit_then_fail
        args = self.binding(out)
        error = self.assert_hold_refusal(self.surface, args)
        del self.missions.apply_operator_attested_approval
        self.assertIn("does not mean no authorization exists", error.reason)
        self.assertEqual(self.attestation_state(out), "HOLD")
        self.assertEqual(len(self.authorizations()), 1)
        # Restart with Mission Core UNREADABLE: the HOLD stays, nothing is
        # written, even though an authorization does exist.
        before = (self.mission_bytes(), self.surface_bytes())
        blind, calls = self.guarded_restart()

        def unreadable(*a):
            raise mission_store.MissionStoreError("synthetic: unreadable")
        blind._missions.get = unreadable
        self.assert_hold_refusal(blind, args)
        self.assertEqual((self.mission_bytes(), self.surface_bytes()), before)
        self.assertEqual(calls, [])
        # Restart with Mission Core readable: reconciled from the durable
        # record, never re-applied.
        fresh, calls = self.guarded_restart()
        done = fresh.attest_approval(**args)
        self.assertEqual(calls, [])
        self.assertEqual(done["status"], "approved_by_operator_attestation")
        self.assertTrue(done["reconciled_from_durable_record"])
        self.assertEqual(done["authorization_id"], list(self.authorizations())[0])
        self.assertEqual(self.attestation_state(out), "APPLIED")
        self.assertEqual(len(self.authorizations()), 1)
        ledger = json.loads(self.mission_bytes())["authority_ledger"]
        self.assertEqual([e["kind"] for e in ledger], ["ISSUED"])

    def test_P7b_core_save_failure_BEFORE_commit_never_authorizes(self):
        # The reproduced round-12 defect: Mission Core's save fails before its
        # commit, then an identical call after restart must NOT authorize.
        out = self.surface.submit(request())
        real = self.missions.apply_operator_attested_approval
        core_store = self.missions._store

        def core_save_fails_before_commit(*a):
            def refuse_save(document):
                raise OSError("synthetic: Mission Core save failed before commit")
            core_store.save = refuse_save
            try:
                return real(*a)
            finally:
                del core_store.save

        self.missions.apply_operator_attested_approval = core_save_fails_before_commit
        args = self.binding(out)
        self.assert_hold_refusal(self.surface, args)
        del self.missions.apply_operator_attested_approval
        self.assertEqual(self.attestation_state(out), "HOLD")
        self.assertEqual(self.authorizations(), {})
        self.assertEqual(self.missions.get(out["mission_id"])["record"]["decisions"], [])
        before = (self.mission_bytes(), self.surface_bytes())
        for _ in range(3):
            fresh, calls = self.guarded_restart()
            self.assert_hold_refusal(fresh, args)
            self.assertEqual(calls, [])
            self.assertEqual((self.mission_bytes(), self.surface_bytes()), before)
        self.refused("local_request_attestation_conflict",
                     lambda: self.fresh_surface().attest_approval(
                         **dict(args, expires_at=args["expires_at"] - 1)))
        self.assertEqual((self.mission_bytes(), self.surface_bytes()), before)
        self.assertEqual(self.authorizations(), {})
        self.assertEqual(self.attestation_state(out), "HOLD")
        # The abandonment the HOLD reason names really works: cancel withdraws
        # the proposal, after which nothing can approve it.
        self.fresh_surface().cancel(out["request_ref"], out["control_capability"])
        self.refused("local_request_cancelled",
                     lambda: self.fresh_surface().attest_approval(**args))
        self.assertEqual(self.authorizations(), {})

    def test_P7c_crash_after_reservation_before_apply_holds_on_restart(self):
        out = self.surface.submit(request())

        class SimulatedCrash(BaseException):
            pass

        def crash(*a):
            raise SimulatedCrash()

        self.missions.apply_operator_attested_approval = crash
        args = self.binding(out)
        with self.assertRaises(SimulatedCrash):
            self.surface.attest_approval(**args)
        self.assertEqual(self.attestation_state(out), "RESERVED")
        mission_before = self.mission_bytes()
        fresh, calls = self.guarded_restart()
        self.assert_hold_refusal(fresh, args)
        self.assertEqual(calls, [])
        self.assertEqual(self.mission_bytes(), mission_before)
        self.assertEqual(self.attestation_state(out), "HOLD")
        before = (self.mission_bytes(), self.surface_bytes())
        again, calls = self.guarded_restart()
        self.assert_hold_refusal(again, args)
        self.assertEqual(calls, [])
        self.assertEqual((self.mission_bytes(), self.surface_bytes()), before)
        self.assertEqual(self.authorizations(), {})

    def test_P8_crash_after_reservation_before_record_is_harmless(self):
        out = self.surface.submit(request())
        real_save = self.surface._store.save
        calls = []

        def fail_first_save(document):
            if not calls:
                calls.append(1)
                raise OSError("synthetic: crashed before the attestation saved")
            return real_save(document)

        self.surface._store.save = fail_first_save
        with self.assertRaises(OSError):
            self.surface.attest_approval(**self.binding(out))
        del self.surface._store.save
        self.assertNotIn("attested_approval",
                         json.loads(self.surface_bytes())["requests"][out["request_ref"]])
        self.assertEqual(self.authorizations(), {})
        done = self.surface.attest_approval(**self.binding(out))
        self.assertEqual(done["status"], "approved_by_operator_attestation")
        self.assertEqual(len(self.authorizations()), 1)

    def test_P9_cancelled_or_withdrawn_request_cannot_be_approved(self):
        out = self.surface.submit(request())
        args = self.binding(out)
        self.surface.cancel(out["request_ref"], out["control_capability"])
        self.refused("local_request_cancelled",
                     lambda: self.surface.attest_approval(**args))
        self.assertEqual(self.authorizations(), {})


class QAffirmativeGrammarTests(Fixture):
    """Only the WHOLE reply "approved" or "approve" (trimmed, case-folded)
    counts. Never a substring, never quoted or reported speech, never
    interpreted. Every refusal writes nothing."""

    binding = PAttestedApprovalTests.binding
    authorizations = PAttestedApprovalTests.authorizations

    NEGATIVE = (
        "no", "not approved", "disapproved", "approved, thanks",
        "yes approved", "approved by me", "\"approved\"", "'approved'",
        "he said approved", "she said \"approved\"", "approved.", "approve?",
        "unrelated text", "", "   ", "\n\t",
    )

    def test_Q1_every_non_exact_reply_is_refused_and_writes_nothing(self):
        out = self.surface.submit(request())
        for reply in self.NEGATIVE:
            with self.subTest(reply=reply):
                before = (self.mission_bytes(), self.surface_bytes())
                error = self.refused("local_request_reply_not_affirmative",
                                     lambda: self.surface.attest_approval(
                                         **self.binding(out, relayed_reply=reply)))
                self.assertIn("not an exact affirmative", error.reason)
                self.assertEqual((self.mission_bytes(), self.surface_bytes()),
                                 before)
        self.assertEqual(self.authorizations(), {})
        self.assertNotIn("attested_approval",
                         json.loads(self.surface_bytes())["requests"][out["request_ref"]])

    def test_Q2_exact_affirmatives_are_accepted(self):
        for reply in ("approved", "  Approved  ", "APPROVE"):
            with self.subTest(reply=reply):
                out = self.surface.submit(request(objective="q2 %r" % reply))
                result = self.surface.attest_approval(
                    **self.binding(out, relayed_reply=reply))
                self.assertEqual(result["status"],
                                 "approved_by_operator_attestation")
                self.assertEqual(result["evidence_status"],
                                 "operator_attestation_only_not_sender_evidence")

    def test_Q3_ambiguous_reply_without_a_mission_reference_is_refused(self):
        a = self.surface.submit(request(objective="A"))
        self.surface.submit(request(objective="B"))
        before = (self.mission_bytes(), self.surface_bytes())
        self.refused("local_request_ambiguous_reference", self.surface.present)
        for changes in (dict(mission_id=None), dict(request_ref=None),
                        dict(mission_id="")):
            with self.subTest(changes=changes):
                self.refused("local_request_ambiguous_reference",
                             lambda: self.surface.attest_approval(
                                 **self.binding(a, **changes)))
        self.assertEqual((self.mission_bytes(), self.surface_bytes()), before)
        self.assertEqual(self.authorizations(), {})

    def test_Q4_relay_ref_is_attestation_only_never_sender_evidence(self):
        out = self.surface.submit(request())
        self.surface.attest_approval(**self.binding(out, relay_ref="anything"))
        record_ = self.fresh_surface().status(out["request_ref"])["attested_approval"]
        self.assertEqual(record_["evidence_status"],
                         "operator_attestation_only_not_sender_evidence")
        self.assertEqual(record_["provenance_label"]["proof"],
                         "operator_attested_not_independently_verified")
        self.assertIn("could fabricate it", record_["residual_risk"])
        # A tampered record that stores a non-affirmative reply fails closed.
        document = json.loads(self.surface_bytes())
        document["requests"][out["request_ref"]]["attested_approval"][
            "relayed_reply"] = "not approved"
        with self.assertRaises(store_module.LocalRequestStoreError):
            store_module.validate_document(document)


class S3aDirectCoreWithdrawalIsolationTests(Fixture):
    """S3a: MissionService.withdraw_proposal called DIRECTLY on the core,
    never through surface.cancel, with A's real ids (learned through the
    allowed status read) and the fixed shared LOCAL_CALLER_CONTEXT."""

    def test_S3a_direct_core_withdrawal_needs_the_exact_key(self):
        a = self.fresh_surface().submit(request(objective="caller A"))
        b = self.fresh_surface().submit(request(objective="caller B"))
        learned = self.fresh_surface().status(a["request_ref"])
        mission_a, request_a = learned["mission_id"], learned["mission_request_id"]
        context = surface_module.LOCAL_CALLER_CONTEXT
        before = json.loads(self.mission_bytes())["missions"][mission_a]
        for label, key in (("no token", None), ("empty token", ""),
                           ("wrong token", "lc-" + "1" * 64),
                           ("B's own valid token", b["control_capability"])):
            with self.subTest(label):
                with self.assertRaises(mission_record.MissionError) as caught:
                    self.missions.withdraw_proposal(mission_a, request_a, key,
                                                    context)
                self.assertEqual(caught.exception.problem,
                                 "mission_withdrawal_key_mismatch")
                after = json.loads(self.mission_bytes())["missions"][mission_a]
                self.assertEqual(after, before)
                self.assertNotIn("withdrawal", after)
                self.assertEqual(after["state"], "AWAITING_DECISION")
        own = self.missions.withdraw_proposal(mission_a, request_a,
                                              a["control_capability"], context)
        self.assertFalse(own["idempotent"])
        self.assertEqual(own["state"], "AWAITING_DECISION")
        self.assertIsNotNone(self.missions.get(mission_a)["record"]["withdrawal"])
        # B's proposal was untouched by all of it.
        self.assertNotIn("withdrawal",
                         self.missions.get(b["mission_id"])["record"])


class E2CrossPathTests(Fixture):
    """E2, the cross-path question, asked factually: after this surface
    cancels a local request, can an AUTHENTICATED decision path still
    decide that SAME Mission? The authenticated path is the core decision
    path called directly with the SYNTHETIC authenticated context (the only
    product path that can decide a Mission Core Mission is
    ``MissionService.apply_human_decision``; the Telegram reference adapter
    decides workflow_authority workflows, not Mission Core Missions)."""

    def test_E2_authenticated_decisions_after_a_surface_cancel(self):
        out = self.surface.submit(request())
        self.surface.cancel(out["request_ref"], out["control_capability"])
        mission_id = out["mission_id"]
        self.assertEqual(self.missions.get(mission_id)["record"]["state"],
                         "AWAITING_DECISION")
        before = self.mission_bytes()
        observed = {}
        for label, act in (
            ("APPROVE", lambda: self.authenticated_approve(mission_id, 1)),
            ("EDIT", lambda: self.authenticated_edit(mission_id, 1, objective="n")),
            ("DENY", lambda: self.missions.apply_human_decision(
                mission_decision.HumanDecisionEnvelope(
                    context=SYNTHETIC_AUTHENTICATED,
                    decision_id=self.missions.mint_decision_id(
                        SYNTHETIC_AUTHENTICATED),
                    mission_id=mission_id, revision=1,
                    decision=mission_decision.DECISION_DENY,
                    received_at=self.clock()))),
        ):
            try:
                act()
                observed[label] = "SUCCEEDED"
            except mission_record.MissionError as exc:
                observed[label] = exc.problem
        # The factual outcome, recorded as observed.
        self.assertEqual(observed, {
            "APPROVE": "mission_proposal_withdrawn",
            "EDIT": "mission_proposal_withdrawn",
            "DENY": "mission_proposal_withdrawn",
        })
        stored = self.missions.get(mission_id)
        self.assertEqual(stored["record"]["state"], "AWAITING_DECISION")
        self.assertEqual(stored["authorizations"], [])
        self.assertIsNone(stored["live_authorization_id"])
        self.assertEqual(stored["record"]["decisions"], [])
        # Only decision-id reservations were added; the Mission is unchanged.
        after = json.loads(self.mission_bytes())
        self.assertEqual(after["missions"][mission_id],
                         json.loads(before)["missions"][mission_id])


# ====================================================================
# E. Isolation between pending proposals (E1) and unreachable Missions
# ====================================================================


class EIsolationTests(Fixture):

    def test_E1_cross_proposal_cancel_by_another_caller_is_refused(self):
        caller_a, caller_b = self.fresh_surface(), self.fresh_surface()
        a = caller_a.submit(request(objective="caller A's proposal"))
        b = caller_b.submit(request(objective="caller B's proposal"))
        surface_before = self.surface_bytes()
        # B holds A's valid pending request_ref and Mission id, which may
        # legitimately appear in logs, status output or a transcript.
        for problem, token in (
            (capability_contract.PROBLEM_CAPABILITY_MISMATCH, b["control_capability"]),
            (capability_contract.PROBLEM_CAPABILITY_MISSING, ""),
            (capability_contract.PROBLEM_CAPABILITY_UNKNOWN, "lc-" + "1" * 64),
            (capability_contract.PROBLEM_CAPABILITY_UNKNOWN, a["mission_id"]),
            (capability_contract.PROBLEM_CAPABILITY_UNKNOWN, a["request_ref"]),
            (capability_contract.PROBLEM_CAPABILITY_UNKNOWN,
             store_module.token_digest(a["control_capability"])),
        ):
            with self.subTest(problem=problem, token=token[:12]):
                error = self.refused("local_request_control_capability",
                                     caller_b.cancel, a["request_ref"], token)
                self.assertEqual(error.details["capability_problem"], problem)
        self.assertEqual(self.surface_bytes(), surface_before)
        self.assertEqual(caller_b.status(a["request_ref"])["surface_state"], "OPEN")
        # Refusals spent nothing: each caller still controls its own.
        self.assertTrue(caller_b.cancel(b["request_ref"], b["control_capability"])["ok"])
        self.assertTrue(caller_a.cancel(a["request_ref"], a["control_capability"])["ok"])

    def test_E2_own_pending_proposal_control_succeeds(self):
        caller_a = self.fresh_surface()
        a = caller_a.submit(request())
        result = caller_a.cancel(a["request_ref"], a["control_capability"])
        self.assertTrue(result["ok"])
        self.assertEqual(result["surface_state"], "CANCELLED")
        self.assertEqual(result["delivery_authority"], "none")
        self.assertEqual(result["dispatch"], "none")
        # Control conferred no authority: still no authorization anywhere.
        self.assertEqual(self.missions.get(a["mission_id"])["authorizations"], [])

    def test_E3_authenticated_missions_are_unreachable_even_by_correct_id(self):
        # A Mission this surface never created, authorized through the
        # SYNTHETIC authenticated stand-in directly on Mission Core.
        request_id = self.missions.mint_request_id(SYNTHETIC_AUTHENTICATED)
        foreign = self.missions.propose(request_id, request(),
                                        SYNTHETIC_AUTHENTICATED)
        self.authenticated_approve(foreign["mission_id"], 1)
        own = self.surface.submit(request())
        before = self.mission_bytes()
        for identifier in (foreign["mission_id"], request_id):
            with self.subTest(identifier=identifier):
                for operation, args in (
                    (self.surface.cancel, (identifier, own["control_capability"])),
                    (self.surface.approve, (identifier, 1, "0" * 64)),
                    (self.surface.recover, (identifier,)),
                    (self.surface.status, (identifier,)),
                ):
                    self.refused("local_request_unknown_request", operation, *args)
        self.assertEqual(self.mission_bytes(), before)
        self.assertTrue(self.missions.get(foreign["mission_id"])["live_authorization_id"])

    def test_E4_own_proposal_out_of_scope_once_authenticated_or_edited(self):
        for label, act in (
            ("authorized", lambda m: self.authenticated_approve(m, 1)),
            ("edited", lambda m: self.authenticated_edit(m, 1, objective="new")),
        ):
            with self.subTest(label):
                out = self.surface.submit(request())
                act(out["mission_id"])
                surface_before = self.surface_bytes()
                error = self.refused("local_request_control_out_of_scope",
                                     self.surface.cancel, out["request_ref"],
                                     out["control_capability"])
                self.assertIn("UNPROVEN", error.reason)
                # Refused BEFORE the capability was looked at: nothing spent.
                self.assertEqual(self.surface_bytes(), surface_before)
                entry = json.loads(self.surface_bytes())["control_capabilities"][
                    store_module.token_digest(out["control_capability"])]
                self.assertIsNone(entry["consumed_at"])

    def test_E5_an_expired_or_consumed_capability_controls_nothing(self):
        out = self.surface.submit(request())
        self.clock.now += store_module.CONTROL_CAPABILITY_VALIDITY_SECONDS
        error = self.refused("local_request_control_capability",
                             self.surface.cancel, out["request_ref"],
                             out["control_capability"])
        self.assertEqual(error.details["capability_problem"],
                         capability_contract.PROBLEM_CAPABILITY_EXPIRED)
        self.assertEqual(self.surface.status(out["request_ref"])["surface_state"],
                         "OPEN")


# ====================================================================
# F. No dispatch from this surface, independently of approval
# ====================================================================


def import_roots(path):
    roots = set()
    modules = set()
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            for alias in node.names:
                roots.add(alias.name.split(".")[0])
                modules.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            roots.add((node.module or "").split(".")[0])
            modules.add(node.module or "")
    return roots, modules


DISPATCH_PROBE = r"""
import io, json, os, socket, subprocess, sys
sys.path.insert(0, sys.argv[1])
def forbid(*args, **kwargs):
    raise AssertionError("the surface tried to start a process or connect")
subprocess.Popen = forbid
os.system = forbid
for name in ("fork", "execv", "execve", "execvp", "spawnv", "posix_spawn"):
    if hasattr(os, name):
        setattr(os, name, forbid)
socket.socket = forbid
socket.create_connection = forbid
before = set(sys.modules)
from local_request import cli
state = sys.argv[2]
def run(argv, text=""):
    out = io.StringIO()
    code = cli.main(["--state-dir", state] + argv, io.StringIO(text), out,
                    clock=lambda: 1000000)
    return code, (json.loads(out.getvalue()) if out.getvalue() else None)
req = {"objective": "o", "target_context": "t",
       "repository_url": "https://github.com/Example/Repo",
       "requested_scope": "s", "requested_action_scope": ["repository_read"],
       "requested_delivery_target": None,
       "proof_contract": {"requirements": [{"key": "k", "description": "d",
           "evidence_kinds": ["VERIFICATION_RECORD"],
           "required_artifact_keys": [], "max_evidence_age_seconds": 60}],
           "required_artifacts": [], "required_dependencies": [],
           "required_resource_readiness": [],
           "degradation_policy": {"permitted_blocker_keys": []},
           "continuation_budget": {"max_attempts": 1, "max_checkpoints": 1}}}
codes = []
code, out = run(["propose"], json.dumps(req)); codes.append(code)
ref = out["request_ref"]
for extra in ({"dispatch": True}, {"herdr": "run"}, {"execute": "now"},
              {"approved": True}, {"authorization": "ma-0"}):
    codes.append(run(["propose"], json.dumps(dict(req, **extra)))[0])
codes.append(run(["status", ref])[0])
codes.append(run(["approve", ref, "--revision", "1", "--proposal-digest",
                  out["proposal_digest_sha256"]])[0])
codes.append(run(["recover", ref])[0])
codes.append(run(["cancel", ref], "lc-" + "0" * 64)[0])
codes.append(run(["cancel", ref], out["control_capability"])[0])
codes.append(run(["approve", ref, "--revision", "1", "--proposal-digest",
                  out["proposal_digest_sha256"]])[0])
loaded = sorted(n for n in set(sys.modules) - before
                if n.split(".")[0] in json.loads(sys.argv[3]))
print(json.dumps({"codes": codes, "loaded": loaded}))
"""


class FNoDispatchTests(Bounded):

    def test_F1_static_no_dispatch_import_closure(self):
        files = sorted(PACKAGE_DIR.glob("*.py")) + [ENTRY_SCRIPT]
        self.assertEqual(sorted(p.name for p in files),
                         ["__init__.py", "cli.py", "direquest.py", "store.py",
                          "surface.py"])
        # INTENTIONAL PIN CHANGE (Task 8 increment 2b, Lead brief section A):
        # the run route needs the real bridge, so cli.py holds EXACTLY ONE
        # import outside the allowed roots: ``from target_runtime import
        # mission_bridge``, nested inside ``build_bridge`` (lazy: only the
        # run commands execute it; F2 proves every other command still
        # loads none of NO_DISPATCH_ROOTS). surface.py imports no runtime:
        # it reaches the bridge only as an object its builder hands in.
        cli_tree = ast.parse((PACKAGE_DIR / "cli.py").read_text())
        runtime_imports = [
            node for node in ast.walk(cli_tree)
            if isinstance(node, (ast.Import, ast.ImportFrom))
            and ((getattr(node, "module", None) or "").split(".")[0]
                 in NO_DISPATCH_ROOTS
                 or any(a.name.split(".")[0] in NO_DISPATCH_ROOTS
                        for a in node.names))]
        self.assertEqual(len(runtime_imports), 1)
        self.assertEqual((runtime_imports[0].module,
                          [a.name for a in runtime_imports[0].names]),
                         ("target_runtime", ["mission_bridge"]))
        builder = [node for node in cli_tree.body
                   if isinstance(node, ast.FunctionDef)
                   and node.name == "build_bridge"][0]
        self.assertIn(runtime_imports[0], list(ast.walk(builder)))
        for path in files:
            roots, modules = import_roots(path)
            if path.name == "cli.py":
                roots = roots - {"target_runtime"}
                modules = modules - {"target_runtime"}
            self.assertLessEqual(roots, ALLOWED_IMPORT_ROOTS, path.name)
            self.assertFalse(roots & set(NO_DISPATCH_ROOTS), path.name)
            for module in modules:
                if module.split(".")[0] == "workflow_authority":
                    self.assertIn(module, ALLOWED_WORKFLOW_AUTHORITY_MODULES,
                                  path.name)
            source = path.read_text()
            for word in ("dispatch_", "start_agent", "HerdrControlPlane",
                         "subprocess", "Popen", "shell=True"):
                self.assertNotIn(word, source.replace("dispatch_roots", ""),
                                 (path.name, word))

    def test_F2_no_input_starts_a_process_or_loads_an_execution_module(self):
        with tempfile.TemporaryDirectory() as temp:
            state = os.path.join(temp, "state")
            result = subprocess.run(
                [sys.executable, "-c", DISPATCH_PROBE, str(REPO_ROOT), state,
                 json.dumps(list(NO_DISPATCH_ROOTS))],
                cwd=str(REPO_ROOT), capture_output=True, text=True,
                timeout=CHILD_TIMEOUT_SECONDS)
        self.assertEqual(result.returncode, 0, result.stderr[-3000:])
        report = json.loads(result.stdout.strip().splitlines()[-1])
        self.assertEqual(report["loaded"], [])
        # propose ok; five refused fields; status ok; approve refused;
        # recover ok; wrong token refused; own cancel ok; late approve refused.
        self.assertEqual(report["codes"], [0, 3, 3, 3, 3, 3, 0, 3, 0, 3, 0, 3])

    def test_F3_entry_script_starts_without_grok_and_answers_help(self):
        result = subprocess.run(
            [sys.executable, str(ENTRY_SCRIPT), "--help"], cwd=str(REPO_ROOT),
            capture_output=True, text=True, timeout=CHILD_TIMEOUT_SECONDS,
            env=dict(os.environ, PYTHONPATH=str(REPO_ROOT)))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("usage", result.stdout.lower())
        flat = " ".join(result.stdout.split())
        self.assertIn("Ordinary approval is always refused", flat)
        self.assertIn("operator-attested approval (not independently verified)",
                      flat)


# ====================================================================
# G. Evidence level and delivery boundary
# ====================================================================


class GEvidenceAndDeliveryTests(Fixture):

    def test_G1_a_recorded_result_is_never_upgraded_by_reading_it_back(self):
        out = self.surface.submit(request())
        mission_id = out["mission_id"]
        # SYNTHETIC authenticated route on Mission Core: approve, activate,
        # and SUBMIT (not accept) one evidence record.
        self.authenticated_approve(mission_id, 1)
        self.missions.activate_proof_contract(
            mission_id, self.missions.mint_state_operation_id(SYNTHETIC_AUTHENTICATED),
            0, SYNTHETIC_AUTHENTICATED)
        self.missions.submit_evidence(
            mission_id, self.missions.mint_state_operation_id(SYNTHETIC_AUTHENTICATED),
            1, "tests_pass", mission_record.EVIDENCE_KIND_VERIFICATION_RECORD,
            "a" * 64, [], SYNTHETIC_AUTHENTICATED)
        status = self.fresh_surface().status(out["request_ref"])
        observation = status["mission_observation"]
        self.assertEqual(observation, self.missions.observe(mission_id))
        (evidence,) = observation["evidence"]["value"]
        self.assertFalse(evidence["accepted"])
        self.assertEqual(evidence["standing"], "reported")
        self.assertEqual(observation["proof"]["value"]["requirements"],
                         {"tests_pass": "SUBMITTED_NOT_ACCEPTED"})
        self.assertFalse(observation["proof"]["value"]["satisfied"])
        self.assertFalse(observation["completion"]["verified_success"])
        self.assertFalse(observation["completion"]["closure_verified"])
        self.assertEqual(observation["delivery_receipts"]["value"]["effects_completed"],
                         [])
        self.assertEqual(status["delivery_authority"], "none")

    def test_G2_delivery_authority_is_structurally_none(self):
        out = self.surface.submit(request(
            requested_delivery_target=mission_record.DELIVERY_TARGET_GITHUB_PR))
        self.assertEqual(out["proposal"]["requested_delivery_target"], "github_pr")
        refusal = self.surface.approve(out["request_ref"], 1,
                                       out["proposal_digest_sha256"])
        for response in (out, refusal, self.surface.status(out["request_ref"])):
            self.assertEqual(response["delivery_authority"], "none")
            self.assertEqual(response["dispatch"], "none")
        self.assertEqual(self.missions.get(out["mission_id"])["authorizations"], [])
        self.assertEqual(self.missions._store.load()["authority_ledger"], [])


if __name__ == "__main__":
    unittest.main()
