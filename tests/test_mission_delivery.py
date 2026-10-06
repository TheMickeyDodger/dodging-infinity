"""Task 8, slice S-VI: Mission-bound P1-A6 delivery, hermetic.

Production services with controlled adapters only: the real Mission
store and service, the real effect gate, the real ``TargetBroker`` and
Runtime pass over a REAL git lease (cloned from the target fixture by the
recording engine transport), the Runtime's REAL verification producer
(owned processes), the real ``DeliveryMachine`` and delivery store over a
hermetic bare "remote" (the lease's ``origin`` rewritten to it through a
repository-local ``insteadOf``), and a recording ``gh`` half
(``test_pr_delivery.TestTransport``: ``gh`` itself can never run). No
network, no real remote, no model, no Herdr, no live Mission or message.
"""

import contextlib
import copy
import errno
import hashlib
import json
import os
import shutil
import signal
import subprocess
import sys
import unittest
from unittest import mock

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from _hermetic_git import run_git, run_git_completed               # noqa: E402
import _stamp_faults as stamp_faults                               # noqa: E402
import test_mission_controls                                      # noqa: E402
from test_mission_engagement import EngagementCase                 # noqa: E402
from test_pr_delivery import TestTransport                         # noqa: E402
from test_target_runtime import CANONICAL_URL                      # noqa: E402

from mission import record as mission_record                       # noqa: E402
from mission import service as mission_service_module              # noqa: E402
from mission import state as mission_state                         # noqa: E402
from mission_control import delivery as delivery_module            # noqa: E402
from mission_control import delivery_artifacts as artifacts        # noqa: E402
from mission_control import gate as gate_module                    # noqa: E402
from mission_control import reconciliation_bridge as bridge         # noqa: E402
from grok_mcp import controller as controller_module               # noqa: E402
from grok_mcp import decision_tools                                # noqa: E402
from grok_mcp import elicitation                                   # noqa: E402
from grok_mcp import protocol                                      # noqa: E402
from pr_delivery import authorization as delivery_auth             # noqa: E402
from pr_delivery import cli as delivery_cli                        # noqa: E402
from pr_delivery import machine as machine_module                  # noqa: E402
from pr_delivery import store as delivery_store_module             # noqa: E402
from pr_delivery import transport as transport_module              # noqa: E402
from target_runtime import broker as broker_module                 # noqa: E402
from target_runtime import process_ownership                       # noqa: E402
from target_runtime import runtime as runtime_module               # noqa: E402
from target_runtime import verification as verification_module     # noqa: E402
from target_runtime import workspace as workspace_module           # noqa: E402
from workflow_authority import record as wa_record                 # noqa: E402
from workflow_authority import store as wa_store                   # noqa: E402

# The Mission's APPROVED verification argv: a real program (never a
# shell) the Runtime runs in the leased workspace; it reads the staged
# candidate file and exits by what it finds.
VERIFY_ARGV = [
    sys.executable, "-c",
    "import pathlib, sys; text = pathlib.Path('fix.txt').read_text();"
    " print('verified candidate:', text.strip());"
    " sys.exit(0 if text.startswith('fixed') else 3)",
]
CLIENT = mission_record.AuthenticatedContext(
    transport="grok_mcp",
    principal_kind=mission_record.PRINCIPAL_KIND_CLIENT_CONFIRMATION,
    principal_ref="1")
CONNECTOR = mission_record.AuthenticatedContext(
    transport="grok_mcp",
    principal_kind=mission_record.PRINCIPAL_KIND_CONNECTOR_CREDENTIAL,
    principal_ref="1")


class Crash(BaseException):
    """A process death at an exact point. A ``BaseException``, so no
    refusal handler in the delivery path (``except Exception``) can absorb
    it: the test observes exactly what was durable at that point."""


# The transport verbs that PERFORM a P1-A6 delivery effect, as counter
# labels (the P1-A6 COMMIT effect is labelled ``commit_step``: a label, not
# a git invocation).
EFFECT_VERBS = ("fetch_ref", "read_tree_two_way", "update_ref", "write_tree",
                "commit_step", "push", "run_reverification", "gh_pr_create")
# The workspace-preparation mutations (counted apart): the create-only ref
# (an ``update_ref`` against the zero id) and the HEAD move.
PREPARATION_VERBS = ("prepare_ref", "attach_head")
ZERO = "0" * 40


class MissionTransport(TestTransport):
    """The P1-A6 test transport (real git; ``gh`` structurally unable to
    run) answering the recording ``gh`` half for the Mission's repository.
    Every effect verb is COUNTED when performed, and a test may hook one
    to run code (a Mission control, a crash) just before or just after
    the real effect."""

    def __init__(self, repo_path):
        super(MissionTransport, self).__init__(repo_path)
        self.performed = dict((verb, 0) for verb in EFFECT_VERBS + PREPARATION_VERBS)
        self.hooks = {}
        self.remote_reads = 0

    def ls_remote(self, path, remote_name, ref):
        self.remote_reads += 1
        return TestTransport.ls_remote(self, path, remote_name, ref)

    def _effect(self, verb, perform):
        hook = self.hooks.get(verb)
        if hook is not None:
            hook("before")
        result = perform()
        self.performed[verb] += 1
        if hook is not None:
            hook("after")
        return result

    def fetch_ref(self, path, remote_name, ref):
        return self._effect("fetch_ref", lambda: TestTransport.fetch_ref(
            self, path, remote_name, ref))

    def read_tree_two_way(self, path, old_oid, new_oid):
        return self._effect("read_tree_two_way", lambda: TestTransport.read_tree_two_way(
            self, path, old_oid, new_oid))

    def update_ref(self, path, ref, new_oid, old_oid):
        return self._effect("prepare_ref" if old_oid == ZERO else "update_ref",
                            lambda: TestTransport.update_ref(
                                self, path, ref, new_oid, old_oid))

    def write_tree(self, path):
        return self._effect("write_tree", lambda: TestTransport.write_tree(self, path))

    def attach_head(self, path, ref):
        return self._effect("attach_head", lambda: TestTransport.attach_head(
            self, path, ref))

    def commit(self, path, name, email, message):
        return self._effect("commit_step", lambda: TestTransport.commit(
            self, path, name, email, message))

    def push(self, path, remote_name, source_ref, destination_ref):
        return self._effect("push", lambda: TestTransport.push(
            self, path, remote_name, source_ref, destination_ref))

    def run_reverification(self, argv, cwd):
        return self._effect("run_reverification", lambda: TestTransport.run_reverification(
            self, argv, cwd))

    def gh_pr_create(self, owner, repo, head_branch, base_branch, title,
                     body_text):
        def perform():
            if self.create_error:
                raise transport_module.DeliveryTransportError("gh pr create 502")
            head_oid = self.ls_remote(self.repo_path, "origin",
                                      "refs/heads/" + head_branch)
            number = self.next_number
            self.next_number += 1
            item = {"number": number,
                    "url": "%s/pull/%d" % (CANONICAL_URL, number),
                    "headRefOid": head_oid, "headRefName": head_branch,
                    "baseRefName": base_branch, "state": "OPEN"}
            self.open_prs.append(item)
            self.created.append((title, body_text))
            return item["url"]
        return self._effect("gh_pr_create", perform)


class DeliveryCase(EngagementCase):
    """``EngagementCase`` plus a hermetic bare remote holding the baseline
    on ``main`` and a delivery store; ``wire`` hands the Broker the REAL
    Mission delivery driver over a real machine."""

    # The S-V integration fixture's lease helpers (a VERIFIED record with
    # one APPROVE round, real git in the lease), borrowed without its tests.
    _S5 = test_mission_controls.RIntegrationTests
    lease_path = _S5.lease_path
    write_round = _S5.write_round
    listing_with_rounds = _S5.listing_with_rounds
    clean_herd_state = _S5.clean_herd_state
    verified = _S5.verified
    git = _S5.git
    write = _S5.write
    stage = _S5.stage
    base_advance = _S5.base_advance
    _hash = _S5._hash

    def setUp(self):
        super(DeliveryCase, self).setUp()
        self.bare = os.path.join(self.base, "remote.git")
        run_git("init", "-q", "--bare", "-b", "main", self.bare)
        run_git("-C", self.target_fixture, "push", "-q", self.bare,
                "%s:refs/heads/main" % self.baseline)
        self.delivery_dir = os.path.join(self.base, "delivery")
        os.makedirs(self.delivery_dir, mode=0o700)
        self.driver = None
        self.delivery_transport = None
        # The Grok process's OWN plain service over the same Mission store
        # (the S-V fixture service shadows the control operations with
        # test conveniences).
        self.grok_service = mission_service_module.MissionService(
            self.mstore, lambda: self.clock())
        self.desk = delivery_module.DeliveryDesk(self.grok_service, self.store_dir,
                                                 self.delivery_dir)

    # -- fixtures -----------------------------------------------------------------

    def delivery_mission(self, argv=None, **overrides):
        overrides.setdefault("verification", {"argv": list(argv or VERIFY_ARGV)})
        return self.ready_mission(**overrides)

    def completed(self, mission_id, content="fixed\n"):
        """A COMPLETED Mission-origin record whose lease holds the staged
        candidate ``fix.txt`` (exact), its origin rewritten to the bare
        remote and a committer configured, observed by the pass."""
        workflow_id = self.verified(mission_id)
        done = self.act(workflow_id, broker_module.ACTION_COMPLETE)
        self.assertTrue(done.ok, (done.problem, done.detail))
        self.git(workflow_id, "config", "url.%s.insteadOf" % self.bare, CANONICAL_URL)
        self.git(workflow_id, "config", "user.name", "Delivery Runtime")
        self.git(workflow_id, "config", "user.email", "runtime@example.com")
        if content is not None:
            self.stage(workflow_id, "fix.txt", content)
        runtime_module.observe_mission_candidates(self.broker)
        return workflow_id

    def wire(self, workflow_id, broker=None):
        broker = broker or self.broker
        transport = MissionTransport(self.lease_path(workflow_id))
        machine = machine_module.DeliveryMachine(
            delivery_store_module.DeliveryStore(self.delivery_dir), transport,
            lambda: self.clock())
        self.driver = delivery_module.MissionDelivery(
            self.gate, self.store_dir, self.delivery_dir, machine,
            lambda repo, remote, base: delivery_cli.live_bindings(
                transport, repo, remote, base),
            delivery_cli.authorize_client_confirmed, lambda: self.clock())
        broker.mission_delivery = self.driver
        broker.delivery_store_directory = self.delivery_dir
        self.delivery_transport = transport
        return self.driver

    def delivery_pass(self, broker=None):
        return runtime_module.drive_mission_deliveries(broker or self.broker)

    def card(self, mission_id):
        revision = self.service.get(mission_id)["record"]["current_revision"]
        return self.desk.card(mission_id, revision)

    def delivery_decide(self, mission_id, accept=True, context=CLIENT):
        card = self.card(mission_id)
        self.assertTrue(card["ok"], card)
        decision_id = self.service.mint_state_operation_id(context)
        record = self.desk.accept if accept else self.desk.decline
        return record(card["binding"], decision_id, context)

    # -- readers ------------------------------------------------------------------

    def deliveries(self):
        read = delivery_store_module.DeliveryStore(self.delivery_dir).read()
        return {} if read.document is None else read.document["deliveries"]

    def remote_refs(self):
        text = run_git("--git-dir", self.bare, "for-each-ref",
                       "--format=%(refname) %(objectname)")
        return dict(line.split(" ", 1) for line in text.splitlines() if line)

    def evidence(self, mission_id, key):
        state = self.service.get_state(mission_id)
        return [e for e in (state["record"] or {}).get("evidence") or []
                if e["requirement_key"] == key]

    def wf_receipts(self, workflow_id, prefix):
        return artifacts.workflow_receipts(self.record(workflow_id), prefix)

    def effects(self):
        """(remote refs other than main, PRs created)."""
        refs = self.remote_refs()
        return (sorted(ref for ref in refs if ref != "refs/heads/main"),
                len(self.delivery_transport.created)
                if self.delivery_transport is not None else 0)

    def performed(self):
        """The P1-A6 delivery effects performed (preparation apart)."""
        return dict((verb, self.delivery_transport.performed[verb])
                    for verb in EFFECT_VERBS)

    def preparation(self):
        """The workspace-preparation mutations performed."""
        return dict((verb, self.delivery_transport.performed[verb])
                    for verb in PREPARATION_VERBS)

    @staticmethod
    def no_effects():
        return dict((verb, 0) for verb in EFFECT_VERBS)

    def prepared(self, argv=None, **overrides):
        """Pass 1 done: a Mission with a prepared proposal awaiting the
        human's delivery decision."""
        mission_id = self.delivery_mission(argv=argv, **overrides)
        workflow_id = self.completed(mission_id)
        self.wire(workflow_id)
        outcome = self.delivery_pass()[workflow_id]
        self.assertEqual(outcome.outcome, "delivery_awaiting_decision",
                         (outcome.problem, outcome.detail))
        return mission_id, workflow_id

    def decided(self, **overrides):
        mission_id, workflow_id = self.prepared(**overrides)
        decided = self.delivery_decide(mission_id)
        self.assertTrue(decided["ok"], decided)
        return mission_id, workflow_id

    def the_delivery(self):
        deliveries = self.deliveries()
        self.assertEqual(len(deliveries), 1, sorted(deliveries))
        return list(deliveries.values())[0]

    def pass_outcome(self, workflow_id):
        return self.delivery_pass()[workflow_id]

    def hook(self, verb, when, action):
        """Run ``action`` once, ``when`` ('before'/'after') the real ``verb``."""
        fired = []

        def hook(phase):
            if phase == when and not fired:
                fired.append(phase)
                action()
        self.delivery_transport.hooks[verb] = hook

    def crash(self):
        raise Crash("the process died here")

    def submit_decision(self, mission_id, document, accept=True, context=CLIENT):
        """A decision recorded DIRECTLY through the service (no desk): the
        document stored, its evidence submitted and optionally accepted
        under ``context``."""
        directory = artifacts.artifact_directory(self.store_dir)
        digest = artifacts.store_document(directory, document)
        state = self.service.get_state(mission_id)
        submitted = self.service.submit_evidence(
            mission_id, document["decision_id"], state["sequence"], "delivery_decision",
            mission_record.EVIDENCE_KIND_VERIFICATION_RECORD, digest, [], context)
        if accept:
            self.service.accept_evidence(
                mission_id, self.service.mint_state_operation_id(context),
                self.service.get_state(mission_id)["sequence"],
                submitted["evidence_id"], digest, context)
        return submitted["evidence_id"], digest

    def decision_document(self, mission_id, context=CLIENT, **overrides):
        card = self.card(mission_id)
        binding = card["binding"]
        document = artifacts.decision_document(
            mission_id, binding["revision"], binding["proposal_digest_sha256"],
            binding["candidate_identity_digest_sha256"],
            self.service.mint_state_operation_id(context), self.clock(),
            artifacts.DECISION_ACCEPT)
        document.update(overrides)
        return document

    def snapshot(self):
        """Every byte the delivery path could write: the Mission store, the
        workflow store, the artifact directory and the delivery store."""
        files = {}
        for root in (self.mission_dir, self.store_dir, self.delivery_dir):
            for directory, _dirs, names in os.walk(root):
                for name in names:
                    path = os.path.join(directory, name)
                    if name.endswith(".lock"):
                        continue
                    with open(path, "rb") as handle:
                        files[path] = handle.read()
        return files


class HappyPathTests(DeliveryCase):

    def test_H1_proposal_decision_verification_authorization_effects_attestation_pr(self):
        mission_id = self.delivery_mission()
        workflow_id = self.completed(mission_id)
        self.wire(workflow_id)
        observation = delivery_module.exact_candidate(self.record(workflow_id))
        self.assertIsNotNone(observation)
        # Pass 1: the Runtime verifies, accepts its own observations as
        # evidence and PREPARES one proposal; nothing is minted or effected.
        outcome = self.delivery_pass()[workflow_id]
        self.assertTrue(outcome.ok, (outcome.problem, outcome.detail))
        self.assertEqual(outcome.outcome, "delivery_awaiting_decision")
        verifications = self.wf_receipts(workflow_id, artifacts.VERIFICATION_TURN_PREFIX)
        self.assertEqual(len(verifications), 1)
        record = artifacts.load_verification(artifacts.artifact_directory(self.store_dir),
                                             verifications[0]["digest"])
        self.assertEqual(record["command_argv"], VERIFY_ARGV)
        self.assertEqual(record["exit_status"], 0)
        self.assertEqual(record["settlement"], artifacts.SETTLEMENT_SETTLED)
        self.assertEqual(record["candidate_identity_digest_sha256"], observation["identity"])
        self.assertEqual(record["base_oid"], self.baseline)
        self.assertEqual(record["repository_realpath"], self.lease_path(workflow_id))
        log = artifacts.load_bytes(artifacts.artifact_directory(self.store_dir),
                                   record["log_sha256"], artifacts.LOG_SUFFIX)
        self.assertIn(b"verified candidate: fixed", log)
        self.assertGreaterEqual(record["finished_at"], record["ran_at"])
        for key in ("engineering_verified", "reviewer_approve", "candidate_identity"):
            accepted = [e for e in self.evidence(mission_id, key) if e["acceptance"]]
            self.assertEqual(len(accepted), 1, key)
        proposals = self.wf_receipts(workflow_id, artifacts.PROPOSAL_TURN_PREFIX)
        self.assertEqual(len(proposals), 1)
        self.assertEqual(self.deliveries(), {})
        self.assertEqual(self.effects(), ([], 0))
        # The card is the FULL proposal; the confirm value is the candidate
        # identity prefix.
        card = self.card(mission_id)
        self.assertTrue(card["ok"], card)
        self.assertEqual(card["confirm_value"], observation["identity"][:12])
        for fragment in ("DELIVERY DECISION REQUEST", self.lease_path(workflow_id),
                         CANONICAL_URL, "di-mission/%s-r1" % mission_id,
                         "target base: main", self.baseline, observation["identity"],
                         "fix.txt", "exit 0", "no merge, auto-merge, tag",
                         proposals[0]["digest"]):
            self.assertIn(fragment, card["card"])
        # The human accepts: delivery_decision evidence, client-confirmed.
        decided = self.delivery_decide(mission_id)
        self.assertTrue(decided["ok"], decided)
        decision = self.evidence(mission_id, "delivery_decision")
        self.assertEqual(len(decision), 1)
        self.assertEqual(decision[0]["provenance"]["principal_kind"],
                         mission_record.PRINCIPAL_KIND_CLIENT_CONFIRMATION)
        self.assertEqual(self.deliveries(), {})
        # Pass 2 (a full Runtime pass): bind, mint, drive through the gate,
        # attest every receipt, record the delivery evidence, close.
        runtime_module.process_once(self.broker)
        deliveries = self.deliveries()
        self.assertEqual(len(deliveries), 1)
        delivery = list(deliveries.values())[0]
        self.assertEqual(delivery["blocker"], None)
        self.assertEqual(delivery["human_authorization"]["source"],
                         delivery_auth.AUTHORIZATION_SOURCE_CLIENT_CONFIRMATION)
        self.assertEqual(delivery["phase"], delivery_auth.PHASE_COMPLETE)
        self.assertEqual(delivery["mission"]["workflow_id"], mission_id)
        refs, prs = self.effects()
        self.assertEqual(refs, ["refs/heads/di-mission/%s-r1" % mission_id])
        self.assertEqual(prs, 1)
        self.assertEqual(delivery["pull_request"]["url"],
                         self.delivery_transport.open_prs[0]["url"])
        state = self.service.get_state(mission_id)
        attested = mission_state.attested_artifacts(state["record"])
        self.assertEqual(
            sorted(mission_state.receipt_attestation_of(a)["step"] for a in attested),
            sorted(step for step in delivery_auth.STEPS
                   if delivery["steps"][step]["receipt"] is not None))
        self.assertEqual(len(self.evidence(mission_id, "delivery_recorded")), 1)
        self.assertEqual(state["progress"], mission_state.PROGRESS_COMPLETED)
        retention = self.record(workflow_id)[wa_record.RETENTION_KEY]
        self.assertEqual(retention["release_reason"], wa_record.RETENTION_RELEASE_PR_CREATED)
        status = self.desk.status(mission_id)
        self.assertEqual(status["delivery"]["pr_url"], delivery["pull_request"]["url"])
        self.assertEqual(status["uncertainty"], [])
        # Another pass repeats nothing.
        runtime_module.process_once(self.broker)
        self.assertEqual(self.effects(), (refs, 1))
        self.assertEqual(len(self.deliveries()), 1)


class T1AuthorityRefusalTests(DeliveryCase):
    """Required test 1: wrong confirmation, source or provenance, a missing
    parent, an unaccepted or mismatched decision -> no delivery authority
    (records 0, effects 0)."""

    def assert_nothing_minted(self, workflow_id, problem):
        outcome = self.pass_outcome(workflow_id)
        self.assertEqual(outcome.problem, problem, (outcome.outcome, outcome.detail))
        self.assertEqual(self.deliveries(), {})
        self.assertEqual(self.performed(), self.no_effects())
        self.assertEqual(self.effects(), ([], 0))
        self.assertEqual(self.wf_receipts(workflow_id, artifacts.BINDING_TURN_PREFIX), [])

    def test_T1a_a_connector_credential_decision_mints_nothing(self):
        mission_id, workflow_id = self.prepared()
        card = self.card(mission_id)
        decided = self.desk.accept(card["binding"],
                                   self.service.mint_state_operation_id(CONNECTOR),
                                   CONNECTOR)
        self.assertTrue(decided["ok"])  # the core records it; the driver refuses it
        self.assert_nothing_minted(workflow_id, delivery_module.PROBLEM_DECISION_PROVENANCE)

    def test_T1b_a_submitted_but_unaccepted_decision_mints_nothing(self):
        mission_id, workflow_id = self.prepared()
        self.submit_decision(mission_id, self.decision_document(mission_id), accept=False)
        outcome = self.pass_outcome(workflow_id)
        self.assertEqual(outcome.outcome, "delivery_awaiting_decision")
        self.assertEqual(self.deliveries(), {})
        self.assertEqual(self.performed(), self.no_effects())

    def test_T1c_a_decision_about_another_proposal_mints_nothing(self):
        mission_id, workflow_id = self.prepared()
        self.submit_decision(mission_id, self.decision_document(
            mission_id, proposal_digest_sha256="f" * 64))
        self.assert_nothing_minted(workflow_id, delivery_module.PROBLEM_DECISION_MISMATCH)

    def test_T1d_a_decline_document_or_a_foreign_decision_id_mints_nothing(self):
        mission_id, workflow_id = self.prepared()
        document = self.decision_document(mission_id, action=artifacts.DECISION_DECLINE)
        self.submit_decision(mission_id, document)
        self.assert_nothing_minted(workflow_id, delivery_module.PROBLEM_DECISION_MISMATCH)

    def test_T1e_an_absent_decision_document_mints_nothing(self):
        mission_id, workflow_id = self.prepared()
        state = self.service.get_state(mission_id)
        decision_id = self.service.mint_state_operation_id(CLIENT)
        submitted = self.service.submit_evidence(
            mission_id, decision_id, state["sequence"], "delivery_decision",
            mission_record.EVIDENCE_KIND_VERIFICATION_RECORD, "e" * 64, [], CLIENT)
        self.service.accept_evidence(
            mission_id, self.service.mint_state_operation_id(CLIENT),
            self.service.get_state(mission_id)["sequence"], submitted["evidence_id"],
            "e" * 64, CLIENT)
        self.assert_nothing_minted(workflow_id, artifacts.PROBLEM_ARTIFACT_ABSENT)

    def test_T1f_a_client_source_without_its_mission_parent_is_never_minted(self):
        mission_id, workflow_id = self.prepared()
        directory = artifacts.artifact_directory(self.store_dir)
        proposal = artifacts.load_document(
            directory, self.card(mission_id)["binding"]["proposal_digest_sha256"],
            artifacts.PROPOSAL_SCHEMA, artifacts.PROPOSAL_KEYS)
        authority = copy.deepcopy(proposal["authority_template"])
        authority["mission"] = None
        authority["human_authorization"] = {
            "identity": "grok_mcp client confirmation (principal 1)",
            "source": delivery_auth.AUTHORIZATION_SOURCE_CLIENT_CONFIRMATION,
            "authorized_at": self.clock(), "confirmation_digest_sha256": "a" * 64,
            "client_confirmation": {
                "decision_id": "mo-" + "1" * 32,
                "decision_document_digest_sha256": "b" * 64,
                "proposal_digest_sha256": "c" * 64, "mission_id": mission_id,
                "mission_revision": 1, "evidence_id": "mv-" + "2" * 32}}
        authority["expiration"] = {"policy": delivery_auth.EXPIRATION_POLICY_ABSOLUTE,
                                   "expires_at": self.clock() + 60}
        with self.assertRaises(delivery_auth.AuthorizationError):
            delivery_cli.authorize_client_confirmed(
                self.delivery_dir, "prd-" + "3" * 24, authority, self.clock())
        self.assertEqual(self.deliveries(), {})
        # And the legacy terminal source cannot be minted through the
        # client-confirmed path at all.
        authority["human_authorization"]["source"] = (
            delivery_auth.AUTHORIZATION_SOURCE_LOCAL_TERMINAL)
        with self.assertRaises(delivery_cli.CeremonyError):
            delivery_cli.authorize_client_confirmed(
                self.delivery_dir, "prd-" + "3" * 24, authority, self.clock())
        self.assertEqual(self.deliveries(), {})

    def test_T1g_mission_approval_and_delivery_decision_are_distinct(self):
        # A Mission approval alone prepares a proposal and authorizes no
        # delivery; the delivery decision changes no Mission decision or
        # authorization; neither stands in for the other.
        mission_id, workflow_id = self.prepared()
        before = self.service.get(mission_id)
        self.assertEqual(self.deliveries(), {})
        self.assertEqual(self.evidence(mission_id, "delivery_decision"), [])
        self.assertTrue(self.delivery_decide(mission_id)["ok"])
        after = self.service.get(mission_id)
        self.assertEqual(after["record"]["decisions"], before["record"]["decisions"])
        self.assertEqual(after["record"]["authorization_ids"],
                         before["record"]["authorization_ids"])
        self.assertEqual(after["record"]["state"], before["record"]["state"])
        self.assertEqual(self.deliveries(), {})

    def test_T1h_a_decline_records_the_sticky_cancel_and_releases_retention(self):
        mission_id, workflow_id = self.prepared()
        declined = self.delivery_decide(mission_id, accept=False)
        self.assertTrue(declined["ok"], declined)
        self.assertTrue(declined["cancel_requested"])
        self.assertEqual(self.evidence(mission_id, "delivery_decision"), [])
        outcome = self.pass_outcome(workflow_id)
        self.assertEqual(outcome.problem, gate_module.PROBLEM_CANCEL_REQUESTED)
        self.assertEqual(self.deliveries(), {})
        self.assertEqual(self.performed(), self.no_effects())
        runtime_module.release_mission_retentions(self.broker)
        retention = self.record(workflow_id)[wa_record.RETENTION_KEY]
        self.assertEqual(retention["release_reason"], wa_record.RETENTION_RELEASE_DECLINED)
        # A cancel that is NOT the client-confirmed decline of this proposal
        # releases nothing (S-V keeps confirming a cancel a control's act).
        self.assertFalse(delivery_module.declined_delivery(
            {"cancel_request": {"provenance": {"principal_kind":
                                               mission_record.PRINCIPAL_KIND_LOCAL_PROCESS_USER},
                                "reason": "operator cancel"}},
            artifacts.artifact_directory(self.store_dir), self.record(workflow_id)))


class T2OneDecisionOneDeliveryTests(DeliveryCase):
    """Required test 2: crashes across decision acceptance, binding and
    delivery insertion; concurrent retries -> at most one delivery."""

    def binding(self, workflow_id):
        receipts = self.wf_receipts(workflow_id, artifacts.BINDING_TURN_PREFIX)
        self.assertEqual(len(receipts), 1)
        return artifacts.load_document(
            artifacts.artifact_directory(self.store_dir), receipts[0]["digest"],
            artifacts.BINDING_SCHEMA, artifacts.BINDING_KEYS)

    def test_T2a_a_crash_after_binding_before_insertion_repeats_the_same_id(self):
        mission_id, workflow_id = self.decided()
        mint = self.driver._mint

        def crashing(*args):
            self.driver._mint = mint
            raise Crash("died before the insertion")
        self.driver._mint = crashing
        with self.assertRaises(Crash):
            self.delivery_pass()
        binding = self.binding(workflow_id)
        self.assertEqual(self.deliveries(), {})
        self.assertEqual(binding["delivery_id"], delivery_module.delivery_id_for(
            binding["decision_id"], binding["proposal_digest_sha256"]))
        runtime_module.process_once(self.broker)
        self.assertEqual(sorted(self.deliveries()), [binding["delivery_id"]])
        self.assertEqual(self.effects()[1], 1)

    def test_T2b_a_crash_after_insertion_reuses_the_record(self):
        mission_id, workflow_id = self.decided()
        mint = self.driver._mint

        def inserted_then_crash(*args):
            self.driver._mint = mint
            mint(*args)
            raise Crash("died after the insertion")
        self.driver._mint = inserted_then_crash
        with self.assertRaises(Crash):
            self.delivery_pass()
        self.assertEqual(len(self.deliveries()), 1)
        self.assertEqual(self.performed(), self.no_effects())
        runtime_module.process_once(self.broker)
        self.assertEqual(len(self.deliveries()), 1)
        self.assertEqual(self.the_delivery()["phase"], delivery_auth.PHASE_COMPLETE)
        self.assertEqual(self.effects()[1], 1)

    def test_T2c_a_concurrent_second_driver_and_a_retried_mint_are_one_record(self):
        mission_id, workflow_id = self.decided()
        self.hook("write_tree", "before", self.crash)
        with self.assertRaises(Crash):
            self.delivery_pass()
        record = self.the_delivery()
        authority = dict((key, record[key]) for key in delivery_auth.AUTHORITY_KEYS
                         if key not in ("schema_version", "delivery_id"))
        again, inserted = delivery_cli.authorize_client_confirmed(
            self.delivery_dir, record["delivery_id"], authority, self.clock())
        self.assertFalse(inserted)
        self.assertEqual(again["delivery_id"], record["delivery_id"])
        # A second Runtime driver over the same stores binds the same
        # decision to the same deterministic id.
        second = self.gated_broker()
        self.wire(workflow_id, broker=second)
        runtime_module.drive_mission_deliveries(second)
        self.assertEqual(sorted(self.deliveries()), [record["delivery_id"]])

    def test_T2d_two_accepted_decisions_mint_nothing(self):
        mission_id, workflow_id = self.prepared()
        self.assertTrue(self.delivery_decide(mission_id)["ok"])
        document = artifacts.decision_document(
            mission_id, 1, self.driver_proposal_digest(workflow_id),
            delivery_module.exact_candidate(self.record(workflow_id))["identity"],
            self.service.mint_state_operation_id(CLIENT), self.clock() + 1,
            artifacts.DECISION_ACCEPT)
        self.submit_decision(mission_id, document)
        outcome = self.pass_outcome(workflow_id)
        self.assertEqual(outcome.problem, delivery_module.PROBLEM_DECISION_AMBIGUOUS)
        self.assertEqual(self.deliveries(), {})
        self.assertEqual(self.performed(), self.no_effects())

    def driver_proposal_digest(self, workflow_id):
        return self.wf_receipts(workflow_id, artifacts.PROPOSAL_TURN_PREFIX)[-1]["digest"]


class T3MutationTests(DeliveryCase):
    """Required test 3: a candidate, branch, remote URL, baseline, evidence,
    command or expiry change between proposal, confirmation and effect
    admission refuses at the first affected phase."""

    def test_T3a_a_candidate_change_before_the_answer_refuses_the_card_and_the_record(self):
        mission_id, workflow_id = self.prepared()
        old = self.card(mission_id)
        self.stage(workflow_id, "fix.txt", "fixed differently\n")
        runtime_module.observe_mission_candidates(self.broker)
        stale = self.card(mission_id)
        self.assertEqual(stale["problem"], delivery_module.PROBLEM_PROPOSAL_STALE)
        recorded = self.desk.accept(old["binding"],
                                    self.service.mint_state_operation_id(CLIENT), CLIENT)
        self.assertFalse(recorded["ok"])
        self.assertFalse(recorded["recorded"])
        self.assertEqual(self.evidence(mission_id, "delivery_decision"), [])
        # The Runtime verifies the new candidate and refuses at the
        # evidence phase: the accepted candidate evidence is never
        # overwritten.
        outcome = self.pass_outcome(workflow_id)
        self.assertEqual(outcome.problem, delivery_module.PROBLEM_EVIDENCE_CONTRADICTED)
        self.assertEqual(len(self.wf_receipts(workflow_id,
                                              artifacts.VERIFICATION_TURN_PREFIX)), 2)
        self.assertEqual(self.deliveries(), {})
        self.assertEqual(self.performed(), self.no_effects())

    def test_T3b_a_candidate_change_after_the_answer_refuses_before_minting(self):
        mission_id, workflow_id = self.decided()
        self.stage(workflow_id, "fix.txt", "fixed differently\n")
        runtime_module.observe_mission_candidates(self.broker)
        outcome = self.pass_outcome(workflow_id)
        self.assertEqual(outcome.problem, delivery_module.PROBLEM_EVIDENCE_CONTRADICTED)
        self.assertEqual(self.deliveries(), {})
        self.assertEqual(self.performed(), self.no_effects())

    def test_T3c_a_remote_url_change_after_minting_refuses_the_first_effect(self):
        mission_id, workflow_id = self.decided()
        other = os.path.join(self.base, "other.git")
        run_git("clone", "-q", "--bare", self.bare, other)
        # The expanded URL moves after the human's answer: the machine's own
        # repository check refuses before any effect and nothing is pushed.
        self.git(workflow_id, "config", "--unset", "url.%s.insteadOf" % self.bare)
        self.git(workflow_id, "config", "url.%s.insteadOf" % other, CANONICAL_URL)
        outcome = self.pass_outcome(workflow_id)
        self.assertFalse(outcome.ok)
        record = self.the_delivery()
        self.assertEqual(record["phase"], delivery_auth.PHASE_BLOCKED)
        self.assertEqual(self.performed()["push"], 0)
        self.assertEqual(self.performed()["gh_pr_create"], 0)
        self.assertEqual(self.effects(), ([], 0))

    def test_T3d_a_moved_source_branch_refuses_the_first_effect(self):
        mission_id, workflow_id = self.decided()
        self.git(workflow_id, "symbolic-ref", "HEAD", "refs/heads/elsewhere")
        outcome = self.pass_outcome(workflow_id)
        self.assertFalse(outcome.ok)
        self.assertEqual(self.the_delivery()["phase"], delivery_auth.PHASE_BLOCKED)
        self.assertEqual(self.performed()["commit_step"], 0)
        self.assertEqual(self.effects(), ([], 0))

    def test_T3e_expiry_between_the_answer_and_minting_mints_nothing(self):
        mission_id, workflow_id = self.decided()
        self.clock.advance(delivery_module.DELIVERY_PROPOSAL_VALIDITY_SECONDS + 1)
        outcome = self.pass_outcome(workflow_id)
        self.assertEqual(outcome.problem, delivery_module.PROBLEM_PROPOSAL_EXPIRED)
        self.assertEqual(self.deliveries(), {})
        self.assertEqual(self.performed(), self.no_effects())

    def test_T3f_expiry_after_the_commit_refuses_the_push(self):
        mission_id, workflow_id = self.decided()
        self.hook("commit_step", "after", lambda: self.clock.advance(
            delivery_module.DELIVERY_PROPOSAL_VALIDITY_SECONDS + 1))
        self.pass_outcome(workflow_id)
        record = self.the_delivery()
        self.assertEqual(self.performed()["commit_step"], 1)
        self.assertEqual(self.performed()["push"], 0)
        self.assertEqual(record["phase"], delivery_auth.PHASE_BLOCKED)
        self.assertEqual(self.effects(), ([], 0))

    def test_T3g_a_tampered_proposal_document_refuses_minting(self):
        mission_id, workflow_id = self.decided()
        digest = self.wf_receipts(workflow_id, artifacts.PROPOSAL_TURN_PREFIX)[-1]["digest"]
        path = os.path.join(artifacts.artifact_directory(self.store_dir),
                            digest + artifacts.DOCUMENT_SUFFIX)
        with open(path, "rb") as handle:
            data = handle.read()
        with open(path, "wb") as handle:
            handle.write(data.replace(b"Dodging Infinity Mission", b"Dodging Infinity Missio!"))
        outcome = self.pass_outcome(workflow_id)
        self.assertFalse(outcome.ok)
        self.assertEqual(self.deliveries(), {})
        self.assertEqual(self.performed(), self.no_effects())

    def test_T3h_an_edit_after_the_answer_refuses_everything(self):
        mission_id, workflow_id = self.decided()
        self.edit(mission_id, verification={"argv": [sys.executable, "-c", "pass"]})
        outcome = self.pass_outcome(workflow_id)
        self.assertFalse(outcome.ok)
        self.assertEqual(self.deliveries(), {})
        self.assertEqual(self.performed(), self.no_effects())


class T4VerificationTests(DeliveryCase):
    """Required test 4: the production verification producer is the happy
    path (H1); missing, unconfigured, fabricated, stale or nonzero-exit
    inputs refuse — refusal never substitutes for the producer."""

    def test_T4a_an_undeclared_verification_prepares_nothing(self):
        mission_id = self.ready_mission()
        workflow_id = self.completed(mission_id)
        self.wire(workflow_id)
        outcome = self.pass_outcome(workflow_id)
        self.assertEqual(outcome.problem, delivery_module.PROBLEM_VERIFICATION_UNDECLARED)
        self.assertEqual(self.wf_receipts(workflow_id, artifacts.VERIFICATION_TURN_PREFIX), [])
        # A COMPLETED engagement and a PROVEN APPROVE review are not
        # verification: without a real run nothing is accepted for it.
        self.assertEqual(self.evidence(mission_id, "engineering_verified"), [])
        self.assertEqual(self.wf_receipts(workflow_id, artifacts.PROPOSAL_TURN_PREFIX), [])
        self.assertEqual(self.card(mission_id)["problem"],
                         delivery_module.PROBLEM_PROPOSAL_ABSENT)

    def test_T4b_a_nonzero_exit_is_recorded_truthfully_and_refuses_once(self):
        mission_id = self.delivery_mission()
        workflow_id = self.completed(mission_id, content="broken\n")
        self.wire(workflow_id)
        outcome = self.pass_outcome(workflow_id)
        self.assertEqual(outcome.problem, delivery_module.PROBLEM_VERIFICATION_FAILED)
        receipts = self.wf_receipts(workflow_id, artifacts.VERIFICATION_TURN_PREFIX)
        self.assertEqual(len(receipts), 1)
        record = artifacts.load_verification(artifacts.artifact_directory(self.store_dir),
                                             receipts[0]["digest"])
        self.assertEqual(record["exit_status"], 3)
        self.assertEqual(self.wf_receipts(workflow_id, artifacts.PROPOSAL_TURN_PREFIX), [])
        # The verdict stands for this exact candidate: no silent re-run.
        self.pass_outcome(workflow_id)
        self.assertEqual(len(self.wf_receipts(workflow_id,
                                              artifacts.VERIFICATION_TURN_PREFIX)), 1)
        self.assertEqual(self.evidence(mission_id, "engineering_verified"), [])

    def test_T4c_a_fabricated_record_is_never_used(self):
        mission_id = self.delivery_mission()
        workflow_id = self.completed(mission_id)
        self.wire(workflow_id)
        observation = delivery_module.exact_candidate(self.record(workflow_id))
        directory = artifacts.artifact_directory(self.store_dir)
        # A green-looking record whose log was never captured.
        fake = artifacts.verification_record(
            workflow_id, mission_id, 1, self.lease_path(workflow_id), VERIFY_ARGV, 0,
            "d" * 64, 10, self.clock(), self.clock(), 0.0, observation["identity"],
            self.baseline, artifacts.SETTLEMENT_SETTLED)
        digest = artifacts.store_document(directory, fake)
        entry = self.record(workflow_id)
        from workflow_authority import store as wa_store
        workflows = wa_store.WorkflowStore(self.store_dir)
        with wa_store.exclusive_store_lock(self.store_dir):
            document = workflows.load()
            document["workflows"][workflow_id]["receipts"].append(
                artifacts.verification_receipt(digest, fake, self.clock()))
            workflows.save(document)
        del entry
        outcome = self.pass_outcome(workflow_id)
        self.assertEqual(outcome.outcome, "delivery_awaiting_decision")
        receipts = self.wf_receipts(workflow_id, artifacts.VERIFICATION_TURN_PREFIX)
        self.assertEqual(len(receipts), 2)  # the producer really ran
        proposal = artifacts.load_document(
            directory, self.wf_receipts(workflow_id, artifacts.PROPOSAL_TURN_PREFIX)[0]["digest"],
            artifacts.PROPOSAL_SCHEMA, artifacts.PROPOSAL_KEYS)
        self.assertEqual(proposal["verification_record_digest_sha256"], receipts[1]["digest"])
        self.assertNotEqual(proposal["verification_record_digest_sha256"], digest)

    def test_T4d_a_missing_record_after_acceptance_refuses(self):
        mission_id, workflow_id = self.prepared()
        receipt = self.wf_receipts(workflow_id, artifacts.VERIFICATION_TURN_PREFIX)[0]
        os.unlink(os.path.join(artifacts.artifact_directory(self.store_dir),
                               receipt["digest"] + artifacts.DOCUMENT_SUFFIX))
        self.assertTrue(self.delivery_decide(mission_id)["ok"])
        outcome = self.pass_outcome(workflow_id)
        # A re-run cannot stand in for the accepted record.
        self.assertEqual(outcome.problem, delivery_module.PROBLEM_EVIDENCE_CONTRADICTED)
        self.assertEqual(self.deliveries(), {})
        self.assertEqual(self.performed(), self.no_effects())

    def test_T4e_the_verification_start_failure_records_nothing(self):
        mission_id = self.delivery_mission()
        workflow_id = self.completed(mission_id)
        self.wire(workflow_id)
        from target_runtime import verification as verification_module
        with mock.patch.object(verification_module, "produce",
                               side_effect=OSError("exec failed")):
            outcome = self.pass_outcome(workflow_id)
        self.assertEqual(outcome.problem, broker_module.PROBLEM_VERIFICATION_START)
        self.assertEqual(self.wf_receipts(workflow_id, artifacts.VERIFICATION_TURN_PREFIX), [])
        self.assertEqual(self.pass_outcome(workflow_id).outcome, "delivery_awaiting_decision")

    def test_T4f_a_candidate_that_moves_during_the_run_is_never_verified(self):
        mission_id = self.delivery_mission()
        workflow_id = self.completed(mission_id)
        self.wire(workflow_id)
        from target_runtime import verification as verification_module
        real = verification_module.produce

        def moving(*args, **kwargs):
            result = real(*args, **kwargs)
            self.stage(workflow_id, "fix.txt", "fixed, then moved\n")
            return result
        with mock.patch.object(verification_module, "produce", side_effect=moving):
            outcome = self.pass_outcome(workflow_id)
        self.assertEqual(outcome.problem, broker_module.PROBLEM_VERIFICATION_CANDIDATE_MOVED)
        self.assertEqual(self.wf_receipts(workflow_id, artifacts.VERIFICATION_TURN_PREFIX), [])


# -- Task 8 R28-1: the OUTER SETTLEMENT of a leader its consumer never decided ------
#
# A verification or role-turn spawn is HELD (``spawn_owned(hold_leader=True)``): its
# handle's waits only OBSERVE until its consumer DECIDES the reap
# (``process_ownership.disarm_hold``). A product that never decides leaves the leader
# uncollected — the test's own collection only observes, and the leader outlives the
# test, retained (correctly) by the hygiene module. The settlement below is the
# FIXTURE settling its own spawn; it is never the product's settlement, and says so.
# Module-level, so every class that borrows R19's ``seams`` or ``captured_children``
# reaches it with no further binding.

#: The real ``Popen`` class, bound at import (tests patch ``subprocess.Popen``).
_R28_POPEN = subprocess.Popen


def r28_undecided_leader(case, process):
    """Remember ``process`` — a spawn whose CONSUMER is to decide its reap — for
    ``case``'s ONE outer settlement. That settlement is registered at the FIRST such
    spawn and BEFORE that spawn's own collection, so it runs after every cleanup
    registered later — every collection of every spawn of ``case`` — and no wait of
    ``case`` follows it."""
    held = case.__dict__.get("_r28_undecided_leaders")
    if held is None:
        held = case._r28_undecided_leaders = []
        case.addCleanup(r28_settle_undecided_leaders, case, held)
    if not any(process is known for known in held):
        held.append(process)


def r28_leader_found(answer):
    """What the leader WAS, read from ``disarm_hold``'s own answer for a leader found
    with its hold ARMED and uncollected — never assumed, and never chosen when the
    answer cannot tell."""
    if answer == "collected":
        return ("EXITED and UNCOLLECTED, a ZOMBIE leader with no live member left in its"
                " group, collected now through its own handle")
    if answer == "kept held: it is still running":
        return "still RUNNING, a running child, kept held"
    if answer and "live member(s) remain in its group" in answer:
        return ("EXITED and UNCOLLECTED, a ZOMBIE leader with live member(s) in its group,"
                " kept held")
    if answer and "whether anything lives in its group is UNAVAILABLE" in answer:
        return ("EXITED and UNCOLLECTED, a ZOMBIE leader whose group membership could not"
                " be read, kept held")
    if answer == "kept held: it could not yet be collected":
        return "EXITED with no live member left but NOT collected, kept held"
    return "NOT TOLD, its state could not be read, kept held"


def r28_settle_undecided_leaders(case, held):
    """The OUTER SETTLEMENT of each leader whose CONSUMER NEVER DECIDED its reap: its
    hold still ARMED and the leader still uncollected after every collection of
    ``case``. A consumer that decided disarmed its hold, and then nothing here runs —
    an unmutated run records nothing.

    The settlement is the PRODUCT's own decision, ``disarm_hold``, through ``case``'s
    own handle: it collects an EXITED leader whose group has no live member left (its
    hold, current-group and descendant checks unchanged) and keeps everything else
    held. A wait follows ONLY its "collected"; any "kept held: …" is recorded verbatim,
    never waited on, never signalled. What the leader WAS — a zombie leader, a running
    child, a zombie with live members or an unreadable membership — is read from that
    answer (``r28_leader_found``), and recorded as NOT TOLD when the answer cannot
    tell. OBSERVED ENDED afterwards is signal 0 to its pid and its group: nothing is
    delivered.

    RECORDED, NEVER RAISED, on the R26 OUTER SETTLEMENT pattern — one stderr line per
    settled leader, an observation, UNPROVEN by assertion. The final driver judges
    these lines TWO-SIDED (its failing-path contract, declared for exactly ZM11 x ZV6b
    and ZM12 x ZV6e), and reads one in any stage it does not judge that way as a
    residue: no green row comes from the fixture tidying up after a product that
    failed to."""
    for process in held:
        try:
            if not isinstance(process, _R28_POPEN):
                continue                         # a stand-in: no hold to settle
            hold = getattr(process, "_di_hold", None)
            if (hold is None or not hold.armed
                    or process_ownership._is_collected(process)):
                continue                         # its consumer decided, or it is collected
            answer = process_ownership.disarm_hold(process)
            if answer == "collected":
                _R28_POPEN.wait(process, timeout=1)       # already collected: returns at once
                waited = "waited on its own handle only because it answered collected"
            else:
                waited = "NOT waited on and NOT signalled because it is kept held"
            gone = (process_ownership._process_exists(process.pid) is False
                    and process_ownership._group_alive(process.pid) is False)
            reading = ("leader %d: found %s, disarm_hold answered %r, %s, exit status observed"
                       " by its hold %r, return code %r, %s" % (
                           process.pid, r28_leader_found(answer), answer, waited,
                           getattr(hold, "observed", None), process.returncode,
                           "observed ENDED" if gone else "NOT observed ended"))
        except Exception as exc:                     # noqa: BLE001 - recorded, never raised
            reading = ("leader %s: the settlement itself failed (%s), NOT observed ended"
                       % (getattr(process, "pid", "?"), exc.__class__.__name__))
        sys.stderr.write(
            "R28 fixture: OUTER SETTLEMENT of %s on its FAILING path: its consumer NEVER"
            " DECIDED this leader's reap (its hold still ARMED and the leader uncollected"
            " after every collection of this test), a product failure, so this is the"
            " failing path whatever this test's own assertions found; the FIXTURE settles"
            " its own spawn with the product's own disarm_hold through its own handle,"
            " waiting only on collected and signalling nothing (UNPROVEN by assertion, an"
            " observation); recorded in its cleanup, never raised: %s\n"
            % (case._testMethodName, reading))


class R19VerificationLifecycleTests(DeliveryCase):
    """Task 8 R19-2 + R19-3, the verification lifecycle through the REAL
    delivery pass (``drive_mission_deliveries``) and the REAL producer:
    counts are taken on the real PRODUCER seam (``verification.produce``)
    and the real SPAWN seam (``process_ownership.spawn_owned``), each
    delegating to the production function.

    R19-2 — the launch is admitted AFRESH after the blocking capture and
    the prior-ownership reads: a hold, cancel or source outage landing
    in them reaches neither seam. R19-3 — ownership and settlement: the
    verification scope is a recovery owner (startup recovery reaps an
    owner-dead group), a prior attempt whose ownership or settlement is
    unresolved bars every further attempt, and an interrupted attempt is
    never replayed; unreadable ownership evidence is never read as clear."""

    ATTEMPT = broker_module.VERIFICATION_ATTEMPT_RECEIPT_MARKER

    def ready(self, argv=None):
        mission_id = self.delivery_mission(argv=argv)
        workflow_id = self.completed(mission_id)
        self.wire(workflow_id)
        return mission_id, workflow_id

    @contextlib.contextmanager
    def seams(self, on_spawn=None):
        """``{"produce": n, "spawn": n}`` over the pass(es) run inside;
        ``on_spawn(process)`` runs right after a REAL spawn returned."""
        calls = {"produce": 0, "spawn": 0}
        real_produce, real_spawn = verification_module.produce, process_ownership.spawn_owned

        def produce(*args, **kwargs):
            calls["produce"] += 1
            return real_produce(*args, **kwargs)

        def spawn(*args, **kwargs):
            calls["spawn"] += 1
            process = real_spawn(*args, **kwargs)
            # Task 8 R28-1: remembered for the ONE outer settlement, registered
            # BEFORE this collection so it runs after it (``r28_undecided_leader``).
            r28_undecided_leader(self, process)
            # Collected through its ORIGINAL wait at cleanup (after any
            # group kill below), whatever ``on_spawn`` replaces.
            self.addCleanup(self.collect, process.wait)
            if on_spawn is not None:
                on_spawn(process)
            return process
        with mock.patch.object(verification_module, "produce", produce), \
                mock.patch.object(process_ownership, "spawn_owned", spawn):
            yield calls

    @staticmethod
    def collect(wait):
        try:
            wait(timeout=10)
        except Exception:                               # noqa: BLE001
            pass

    @contextlib.contextmanager
    def during(self, owner, name, event):
        """``event`` runs once, INSIDE ``owner.name`` (before it reads),
        on its first call AFTER the pass's first admission at the delivery-
        effect boundary admitted it — i.e. between that admission and the
        launch."""
        gate = self.broker.mission_gate
        real_admit, real = gate.admit, getattr(owner, name)
        state = {"armed": False, "fired": False}

        def admit(entry, boundary, *args, **kwargs):
            admission = real_admit(entry, boundary, *args, **kwargs)
            if boundary == gate_module.BOUNDARY_DELIVERY_EFFECT and admission.ok:
                state["armed"] = True
            return admission

        def wrapped(*args, **kwargs):
            if state["armed"] and not state["fired"]:
                state["fired"] = True
                event()
            return real(*args, **kwargs)
        with mock.patch.object(gate, "admit", admit), \
                mock.patch.object(owner, name, wrapped):
            yield state

    def attempts(self, workflow_id):
        return [r["bounded_summary"] for r in self.record(workflow_id)["receipts"]
                if r["bounded_summary"].startswith(self.ATTEMPT + " ")]

    def verification_receipts(self, workflow_id):
        return self.wf_receipts(workflow_id, artifacts.VERIFICATION_TURN_PREFIX)

    def assert_one_settled_attempt(self, workflow_id, number=1, before=(), roots=0):
        receipts = self.verification_receipts(workflow_id)
        self.assertEqual(len(receipts), 1)
        attempts = self.attempts(workflow_id)
        self.assertEqual(attempts[:len(before)], list(before))
        self.assertEqual(len(attempts), len(before) + 2, attempts)   # a claim, a settlement
        claim, settlement = attempts[len(before):]
        self.assertTrue(claim.startswith("%s %d claimed roots=%d (candidate "
                                         % (self.ATTEMPT, number, roots)), claim)
        self.assertTrue(claim.endswith("; claimed before the producer call, not proof of"
                                       " it)"), claim)
        self.assertEqual(settlement, "%s %d settled: returned — record %s (its process"
                         " settled)" % (self.ATTEMPT, number, receipts[0]["digest"]))

    def blocked_not_replayable(self, workflow_id, kind):
        """Every pass blocks — neither seam reached; the second adds no
        receipt. Returns the attempt records after the first."""
        seen = []
        for _ in range(2):
            with self.seams() as calls:
                outcome = self.pass_outcome(workflow_id)
            self.assertEqual(calls, {"produce": 0, "spawn": 0})
            self.assertEqual((outcome.outcome, outcome.problem, outcome.detail), (
                "delivery_blocked", broker_module.PROBLEM_VERIFICATION_NOT_REPLAYABLE,
                "verification attempt(s) 1 (%s) ran or may have run in the lease and their"
                " result is not recorded; they are never replayed, and no further attempt"
                " starts" % kind))
            seen.append(self.attempts(workflow_id))
        self.assertEqual(seen[1], seen[0])
        self.assertEqual(self.verification_receipts(workflow_id), [])
        return seen[0]

    def scope(self, workflow_id):
        return process_ownership.owner_scope(
            process_ownership.OWNER_TYPE_WORKFLOW, self.control_of(workflow_id),
            workflow_id, verification_module.VERIFICATION_OWNER_UNIT)

    def control_of(self, workflow_id):
        return self.record(workflow_id)["control_identity"]["repository_realpath"]

    # -- R19-2: a fresh admission at the launch, after every blocking read ---------

    def refused_before_the_launch(self, event, owner=None, name="capture_candidate"):
        mission_id, workflow_id = self.ready()
        with self.seams() as calls, \
                self.during(owner or broker_module, name, lambda: event(mission_id)) as state:
            outcome = self.pass_outcome(workflow_id)
        self.assertTrue(state["fired"])
        self.assertEqual(calls, {"produce": 0, "spawn": 0})
        self.assertEqual(self.attempts(workflow_id), [])       # no claim either
        self.assertEqual(self.verification_receipts(workflow_id), [])
        return mission_id, workflow_id, outcome

    def test_VL1a_a_hold_during_the_capture_reaches_no_producer(self):
        mission_id, workflow_id, outcome = self.refused_before_the_launch(
            lambda m: self.service.request_hold(m))
        self.assertEqual((outcome.outcome, outcome.problem),
                         (broker_module.OUTCOME_MISSION_HELD, gate_module.PROBLEM_HOLD_ACTIVE))
        # Released: ONE attempt is claimed and the producer runs ONCE.
        self.service.release_hold(mission_id)
        with self.seams() as calls:
            outcome = self.pass_outcome(workflow_id)
        self.assertEqual(outcome.outcome, "delivery_awaiting_decision",
                         (outcome.problem, outcome.detail))
        self.assertEqual(calls, {"produce": 1, "spawn": 1})
        self.assert_one_settled_attempt(workflow_id)

    def test_VL1b_a_cancel_during_the_capture_reaches_no_producer_ever(self):
        mission_id, workflow_id, outcome = self.refused_before_the_launch(
            lambda m: self.service.request_cancel(m))
        self.assertEqual((outcome.outcome, outcome.problem),
                         (broker_module.OUTCOME_MISSION_HELD, gate_module.PROBLEM_CANCEL_REQUESTED))
        with self.seams() as calls:
            self.pass_outcome(workflow_id)
        self.assertEqual(calls, {"produce": 0, "spawn": 0})
        self.assertEqual(self.attempts(workflow_id), [])

    def test_VL1c_a_source_outage_during_the_capture_reaches_no_producer(self):
        good = []

        def outage(mission_id):
            good.append(self.mission_bytes())
            with open(self.mstore.path, "w", encoding="utf-8") as handle:
                handle.write("{oops")
        mission_id, workflow_id, outcome = self.refused_before_the_launch(outage)
        self.assertEqual((outcome.outcome, outcome.problem),
                         (broker_module.OUTCOME_MISSION_HELD,
                          gate_module.PROBLEM_SOURCE_UNAVAILABLE))
        with open(self.mstore.path, "wb") as handle:
            handle.write(good[0])
        with self.seams() as calls:
            outcome = self.pass_outcome(workflow_id)
        self.assertEqual(outcome.outcome, "delivery_awaiting_decision",
                         (outcome.problem, outcome.detail))
        self.assertEqual(calls, {"produce": 1, "spawn": 1})
        self.assert_one_settled_attempt(workflow_id)

    def test_VL1d_a_hold_during_the_prior_ownership_read_reaches_no_producer(self):
        """The R19-3 barrier's reads are blocking reads too: the fresh
        admission comes AFTER them."""
        mission_id, workflow_id, outcome = self.refused_before_the_launch(
            lambda m: self.service.request_hold(m), owner=verification_module,
            name="prior_ownership")
        self.assertEqual((outcome.outcome, outcome.problem),
                         (broker_module.OUTCOME_MISSION_HELD, gate_module.PROBLEM_HOLD_ACTIVE))

    # -- R19-3: ownership, settlement, no replay --------------------------------------

    def group_members(self, pgid):
        listed = subprocess.run(["pgrep", "-g", str(pgid)], capture_output=True, text=True)
        return [line for line in listed.stdout.split() if line]

    def the_group(self, workflow_id):
        """The one owned root the attempt left, and its recorded group; the
        group is killed at cleanup whatever the test's outcome (it is this
        test's own process)."""
        prefix = process_ownership.owned_root_base(self.scope(workflow_id))
        roots = [n for n in sorted(os.listdir(prefix)) if os.path.isdir(os.path.join(prefix, n))]
        self.assertEqual(len(roots), 1, roots)
        with open(os.path.join(prefix, roots[0], process_ownership.OWNED_ROOT_PGID_FILE)) as handle:
            pgid = int(handle.read())

        def kill():
            try:
                os.killpg(pgid, signal.SIGKILL)
            except OSError:
                pass
        self.addCleanup(kill)
        return pgid

    def members_after(self, pgid, count):
        """Wait (bounded) until the group has ``count`` live members."""
        import time
        for _ in range(100):
            if len(self.group_members(pgid)) == count:
                break
            time.sleep(0.05)
        return self.group_members(pgid)

    def owner_died_mid_run(self, workflow_id, leader_exits_first=False):
        """The pass that dies: the Runtime is gone while its verification
        runs — ``wait`` never returns to it and the reap never runs (when
        ``leader_exits_first``, the leader ends and is collected first, so
        only its descendant survives)."""
        def owner_dies(process):
            real_wait = process.wait

            def wait(*args, **kwargs):
                if leader_exits_first:
                    real_wait()
                    # Task 8 R28-1: the production spawn is HELD — its wait
                    # observes the exit WITHOUT collecting it — so the
                    # collection this premise names (a dead Runtime's child is
                    # collected by init) is made explicitly, through its handle.
                    process_ownership._collect_leader(process)
                raise Crash("the Runtime died while the verification ran")
            process.wait = wait
        with self.seams(on_spawn=owner_dies) as calls, \
                mock.patch.object(process_ownership, "reap_owned",
                                  side_effect=Crash("the Runtime died before its reap")), \
                self.assertRaises(Crash):
            self.pass_outcome(workflow_id)
        self.assertEqual(calls, {"produce": 1, "spawn": 1})
        attempts = self.attempts(workflow_id)
        self.assertEqual(len(attempts), 1)
        self.assertTrue(attempts[0].startswith("%s 1 claimed" % self.ATTEMPT))
        return attempts

    def held_on_ownership(self, workflow_id, detail):
        with self.seams() as calls:
            try:
                outcome = self.pass_outcome(workflow_id)
            except Exception as exc:                    # noqa: BLE001
                self.fail("the pass raised %r instead of holding" % (exc,))
        self.assertEqual(calls, {"produce": 0, "spawn": 0})
        self.assertEqual((outcome.outcome, outcome.problem, outcome.detail), (
            "delivery_held", broker_module.PROBLEM_VERIFICATION_OWNERSHIP_UNRESOLVED,
            "an earlier verification process's ownership is %s; no attempt starts" % detail))

    def blocked_as_interrupted(self, workflow_id, claim, observed):
        """Re-entry after the prior attempt's ownership resolved: it is
        settled INTERRUPTED (once) and never replayed — every later pass
        blocks with neither seam reached."""
        self.blocked_not_replayable(workflow_id, broker_module.VERIFICATION_ATTEMPT_INTERRUPTED)
        self.assertEqual(self.attempts(workflow_id), [claim, (
            "%s 1 settled: interrupted — claimed and never settled by its own pass; no"
            " verification process of this workflow can be alive now (%s); its outcome"
            " is unknown" % (self.ATTEMPT, observed))])

    def test_VL2_an_interrupted_attempt_reaches_the_spawn_seam_exactly_once(self):
        """The pass dies after the spawn (its own reap still ran, so the group
        is gone); re-entry settles the attempt INTERRUPTED and never spawns
        again."""
        mission_id, workflow_id = self.ready()

        def interrupted(process):
            def wait(*args, **kwargs):
                raise Crash("the pass died while the verification ran")
            process.wait = wait
        with self.seams(on_spawn=interrupted) as calls, self.assertRaises(Crash):
            self.pass_outcome(workflow_id)
        self.assertEqual(calls, {"produce": 1, "spawn": 1})
        attempts = self.attempts(workflow_id)
        self.assertEqual(len(attempts), 1, attempts)                 # the claim alone
        claim = attempts[0]
        self.blocked_as_interrupted(workflow_id, claim,
                                    "1 owned root(s), every recorded group gone")

    def test_VL3_owner_death_is_recovered_through_the_verification_scope(self):
        """The Runtime dies mid-run with a DESCENDANT in the group: re-entry
        is HELD (the group is alive and ours) — no second spawn; startup
        recovery (``recover_inherited_processes``, the existing contract)
        now attributes the verification scope and reaps the whole group,
        leader and descendant; the attempt then settles INTERRUPTED."""
        mission_id, workflow_id = self.ready(argv=[
            sys.executable, "-c",
            "import subprocess, time; subprocess.Popen(['sleep', '120']); time.sleep(30)"])
        [claim] = self.owner_died_mid_run(workflow_id)
        pgid = self.the_group(workflow_id)
        self.assertEqual(len(self.members_after(pgid, 2)), 2)   # leader + descendant
        self.held_on_ownership(workflow_id, "unresolved: group %d of owned root %s is alive"
                               " and corroborated as this verification's" % (
                                   pgid, self.root_name(workflow_id)))
        owner = (process_ownership.OWNER_TYPE_WORKFLOW,
                 process_ownership.control_digest(self.control_of(workflow_id)),
                 workflow_id, verification_module.VERIFICATION_OWNER_UNIT)
        results, unattributed = runtime_module.recover_inherited_processes(self.store_dir)
        self.assertEqual(self.group_members(pgid), [])          # leader AND descendant
        self.assertEqual([(tuple(identity), recovered) for identity, recovered, *_ in results
                          if identity.owner_id == workflow_id], [(owner, [pgid])])
        self.assertNotIn(self.scope(workflow_id), [d for d, _ in unattributed])
        self.assertIn(owner, runtime_module.current_scope_owners(self.store_dir))
        self.blocked_as_interrupted(workflow_id, claim,
                                    "1 owned root(s), every recorded group gone")

    def root_name(self, workflow_id):
        prefix = process_ownership.owned_root_base(self.scope(workflow_id))
        [name] = [n for n in sorted(os.listdir(prefix))
                  if os.path.isdir(os.path.join(prefix, n))]
        return name

    def test_VL4_leaderless_survivors_hold_every_attempt_and_are_never_signalled(self):
        """The leader ended and was collected, the Runtime died before its
        reap, a DESCENDANT survives: its ownership cannot be corroborated (no
        leader to match), so startup recovery leaves it alone (the existing
        contract never signals an uncorroborated group) — and the barrier
        HOLDS every attempt rather than reading it as gone."""
        mission_id, workflow_id = self.ready(argv=[
            sys.executable, "-c", "import subprocess; subprocess.Popen(['sleep', '120'])"])
        self.owner_died_mid_run(workflow_id, leader_exits_first=True)
        pgid = self.the_group(workflow_id)
        self.assertEqual(len(self.members_after(pgid, 1)), 1)   # the descendant alone
        # Task 8 R22-1: the reason is NAMED (a live group whose leader is gone
        # is unresolved, never "gone") — the value moved, the hold did not.
        detail = ("unresolved: group %d of owned root %s is alive and its ownership cannot"
                  " be corroborated (%s)" % (pgid, self.root_name(workflow_id),
                                             process_ownership.UNCORROBORATED_LEADERLESS))
        self.held_on_ownership(workflow_id, detail)
        report = runtime_module.recover_inherited_processes(self.store_dir)
        self.assertEqual(len(self.group_members(pgid)), 1)      # not signalled
        # ... and recovery REPORTS it (R22-1: it was silently omitted), with
        # the CLI naming it — an observation made, so none unavailable.
        root = os.path.join(process_ownership.owned_root_base(self.scope(workflow_id)),
                            self.root_name(workflow_id))
        self.assertEqual(
            [(tuple(identity), recovered, stuck, unstamped, uncorroborated)
             for identity, recovered, stuck, unstamped, uncorroborated in report[0]
             if identity.owner_id == workflow_id],
            [((process_ownership.OWNER_TYPE_WORKFLOW,
               process_ownership.control_digest(self.control_of(workflow_id)),
               workflow_id, verification_module.VERIFICATION_OWNER_UNIT), [], [], [],
              [(root, pgid, process_ownership.UNCORROBORATED_LEADERLESS)])])
        self.assertEqual(report.unavailable, [])
        from target_runtime import cli as cli_module
        import io
        stream = io.StringIO()
        cli_module.report_inherited_recovery(report, stream=stream)
        self.assertIn("dirun: group %d under %s is REPORTED and left alone (%s)"
                      % (pgid, root, process_ownership.UNCORROBORATED_LEADERLESS),
                      stream.getvalue().splitlines())
        self.held_on_ownership(workflow_id, detail)

    def unavailable(self, damage, repair, detail):
        """The verification scope ESTABLISHED as the producer establishes it
        (``assign_scope``), then made unreadable AS THE OS PRESENTS IT
        (``damage``): no attempt starts — neither seam — until repaired."""
        mission_id, workflow_id = self.ready()
        process_ownership.assign_scope(
            process_ownership.OWNER_TYPE_WORKFLOW, self.control_of(workflow_id),
            workflow_id, verification_module.VERIFICATION_OWNER_UNIT)
        scope = self.scope(workflow_id)
        damage(scope)
        self.addCleanup(repair, scope)
        try:
            self.held_on_ownership(workflow_id, "unavailable: " + detail)
            self.assertEqual(self.attempts(workflow_id), [])
        finally:
            repair(scope)             # before the next case, whatever this one found
        with self.seams() as calls:
            outcome = self.pass_outcome(workflow_id)
        self.assertEqual(outcome.outcome, "delivery_awaiting_decision",
                         (outcome.problem, outcome.detail))
        self.assertEqual(calls, {"produce": 1, "spawn": 1})
        self.assert_one_settled_attempt(workflow_id)

    def test_VL5_unavailable_recovery_evidence_is_never_read_as_clear(self):
        if os.geteuid() == 0:
            self.skipTest("permission bits do not bind root")
        prefix_of = process_ownership.owned_root_base
        cases = (
            ("the scope cannot be searched",
             lambda scope: os.chmod(scope, 0), lambda scope: os.chmod(scope, 0o755),
             "its owned-root prefix cannot be examined (PermissionError)"),
            ("the scope's parent cannot be searched",
             lambda scope: os.chmod(os.path.dirname(scope), 0),
             lambda scope: os.chmod(os.path.dirname(scope), 0o755),
             "its scope cannot be examined (PermissionError)"),
            ("the owned-root prefix cannot be listed",
             lambda scope: (os.makedirs(prefix_of(scope)), os.chmod(prefix_of(scope), 0)),
             lambda scope: os.path.isdir(prefix_of(scope)) and os.chmod(prefix_of(scope), 0o755),
             "its owned roots cannot be listed (PermissionError)"),
            ("a root's group record cannot be read",
             self.unreadable_group_record, self.readable_group_record,
             "owned root own-0000000000000000's group record cannot be read"
             " (PermissionError)"),
            ("the assignment is malformed",
             self.malformed_assignment, self.valid_assignment,
             "its assignment does not validate (%s)"
             % process_ownership.UNATTRIBUTED_MALFORMED),
        )
        for label, damage, repair, detail in cases:
            with self.subTest(label):
                self.unavailable(damage, repair, detail)

    def test_VL7_a_live_verification_group_with_no_claim_holds_every_attempt(self):
        """A verification process of this workflow is ALIVE in its scope with
        no attempt claimed for it — as a verification started before this
        correction left it, spawned through the same owned-process contract:
        the ownership records ALONE hold every attempt (neither seam
        reached), never a second, concurrent run."""
        mission_id, workflow_id = self.ready()
        scope = process_ownership.assign_scope(
            process_ownership.OWNER_TYPE_WORKFLOW, self.control_of(workflow_id),
            workflow_id, verification_module.VERIFICATION_OWNER_UNIT)
        process = process_ownership.spawn_owned(
            ["sleep", "60"], verification_module.VERIFICATION_LABEL, directory=scope,
            owned_root_base_dir=scope)
        self.addCleanup(self.collect, process.wait)
        self.addCleanup(self.kill_group, process.pid)
        self.assertEqual(len(self.members_after(process.pid, 1)), 1)
        detail = ("unresolved: group %d of owned root %s is alive and corroborated as this"
                  " verification's" % (process.pid, self.root_name(workflow_id)))
        for _ in range(2):
            self.held_on_ownership(workflow_id, detail)
        self.assertEqual(self.attempts(workflow_id), [])

    @staticmethod
    def kill_group(pgid):
        try:
            os.killpg(pgid, signal.SIGKILL)
        except OSError:
            pass

    # -- the producer's failures, settled by the PHASE reached ----------------------

    def test_VL6a_a_run_whose_result_cannot_be_stored_is_never_re_run(self):
        """POST-START: the verification RAN to its exit status and was
        reaped; storing its record failed (ENOSPC, injected at the artifact
        store). Settled ``ran-unrecorded`` — never "could not start" — and
        never re-run."""
        mission_id, workflow_id = self.ready()
        real = artifacts.store_document
        failed = []

        def store_document(directory, document):
            if not failed:
                failed.append(1)
                raise OSError(28, "No space left on device")
            return real(directory, document)
        with self.seams() as calls, \
                mock.patch.object(artifacts, "store_document", store_document):
            outcome = self.pass_outcome(workflow_id)
        self.assertEqual(calls, {"produce": 1, "spawn": 1})
        ran = ("the verification RAN (exit status 0, its process settled) and storing its"
               " result failed (OSError)")
        self.assertEqual((outcome.outcome, outcome.problem, outcome.detail), (
            "delivery_blocked", broker_module.PROBLEM_VERIFICATION_NOT_REPLAYABLE,
            "%s; it is never re-run, and no further attempt starts" % ran))
        claim, settlement = self.attempts(workflow_id)
        self.assertTrue(claim.startswith("%s 1 claimed roots=0 (" % self.ATTEMPT), claim)
        self.assertEqual(settlement, "%s 1 settled: ran-unrecorded — %s" % (self.ATTEMPT, ran))
        self.blocked_not_replayable(workflow_id, broker_module.VERIFICATION_ATTEMPT_UNRECORDED)
        self.assertEqual(self.attempts(workflow_id), [claim, settlement])

    def test_VL6e_a_failed_wait_is_post_start_and_its_cause_is_never_masked(self):
        """POST-START: the spawn returned, then waiting for the run failed
        (``OSError``, injected on the real process). Settled
        ``outcome-unknown`` — never "could not start" — and never re-run; the
        reap still ran. When the reap fails too (``ValueError``, injected),
        the WAIT's cause is kept and the reap's is named beside it."""
        for label, reap, text in (
                ("the reap succeeds", None,
                 "the verification STARTED and waiting for it failed (OSError): its exit"
                 " status is unknown (its process settled)"),
                ("the reap fails too", ValueError("reap failed"),
                 "the verification STARTED and waiting for it failed (OSError): its exit"
                 " status is unknown (its process unsettled; its reap failed too"
                 " (ValueError))")):
            with self.subTest(label):
                mission_id, workflow_id = self.ready()

                def wait_fails(process):
                    real_wait = process.wait

                    def wait(*args, **kwargs):
                        if reap is not None:     # the reap cannot kill it: end it here
                            real_wait()
                        raise OSError(5, "wait failed")
                    process.wait = wait
                patches = [mock.patch.object(process_ownership, "reap_owned",
                                             side_effect=reap)] if reap else []
                with self.seams(on_spawn=wait_fails) as calls, contextlib.ExitStack() as stack:
                    for patch in patches:
                        stack.enter_context(patch)
                    outcome = self.pass_outcome(workflow_id)
                self.assertEqual(calls, {"produce": 1, "spawn": 1})
                self.assertEqual((outcome.outcome, outcome.problem, outcome.detail), (
                    "delivery_blocked", broker_module.PROBLEM_VERIFICATION_NOT_REPLAYABLE,
                    "%s; it is never re-run, and no further attempt starts" % text))
                claim, settlement = self.attempts(workflow_id)
                self.assertEqual(settlement, "%s 1 settled: outcome-unknown — %s"
                                 % (self.ATTEMPT, text))
                self.blocked_not_replayable(
                    workflow_id, broker_module.VERIFICATION_ATTEMPT_OUTCOME_UNKNOWN)

    @contextlib.contextmanager
    def captured_children(self):
        """Every OWNED spawn's ``Popen`` (its argv runs the stamp wrapper),
        kept so the test can collect it (``spawn_owned`` loses it when it
        raises after ``Popen``); any other subprocess passes through."""
        real, created = subprocess.Popen, []

        def popen(argv, *args, **kwargs):
            process = real(argv, *args, **kwargs)
            if isinstance(argv, (list, tuple)) and any(
                    str(part).endswith("spawn_stamp.py") for part in argv):
                created.append(process)
                # Task 8 R28-1: remembered for the ONE outer settlement, before
                # this collection (``r28_undecided_leader``).
                r28_undecided_leader(self, process)
                self.addCleanup(self.collect, process.wait)
            return process
        with mock.patch.object(subprocess, "Popen", popen):
            yield created

    def test_VL6b_a_spawn_that_raised_after_its_child_started_is_never_replayed(self):
        """START UNKNOWN: the spawn raised AFTER ``Popen`` created the child
        (the parent-side group record failed — injected); the producer cannot
        know whether a process started and says so (held). The child stamped
        its OWN root before exec, so at re-entry the owned roots show one was
        created: settled ``outcome-unknown`` and never replayed."""
        mission_id, workflow_id = self.ready()
        with self.seams() as calls, self.captured_children() as children, \
                mock.patch.object(process_ownership, "record_owned_group",
                                  side_effect=OSError(5, "ledger write failed")):
            outcome = self.pass_outcome(workflow_id)
        self.assertEqual(calls, {"produce": 1, "spawn": 1})
        self.assertEqual(len(children), 1)            # a real child WAS started
        self.assertEqual((outcome.outcome, outcome.problem, outcome.detail), (
            "delivery_held", broker_module.PROBLEM_VERIFICATION_START_UNKNOWN,
            "the verification's spawn raised OSError: whether a process started is not"
            " known; the next pass decides from its owned roots, and nothing is re-run"
            " meanwhile"))
        claim, unknown = self.attempts(workflow_id)
        self.assertEqual(unknown, "%s 1 settled: start-unknown — the spawn raised OSError;"
                                  " whether a process started is not known on this pass"
                         % self.ATTEMPT)
        children[0].wait(timeout=30)                  # the child's run ends
        before = self.blocked_not_replayable(
            workflow_id, broker_module.VERIFICATION_ATTEMPT_OUTCOME_UNKNOWN)
        self.assertEqual(before, [claim, unknown, (
            "%s 1 settled: outcome-unknown — resolved: an owned root was created for it"
            " (0 before it, 1 now), so a process started; 1 owned root(s), every recorded"
            " group gone; its outcome is unknown" % self.ATTEMPT)])

    def test_VL6c_a_proven_pre_start_refusal_is_retried(self):
        """PROVEN PRE-START: ``SpawnGated`` (the scope's spawning frozen) is
        raised before any record or process — settled ``not-started``, held;
        once thawed the NEXT pass claims a new attempt and runs it once."""
        mission_id, workflow_id = self.ready()
        process_ownership.assign_scope(
            process_ownership.OWNER_TYPE_WORKFLOW, self.control_of(workflow_id),
            workflow_id, verification_module.VERIFICATION_OWNER_UNIT)
        scope = self.scope(workflow_id)
        process_ownership.freeze_spawning("frozen by the test", base=scope)
        with self.seams() as calls:
            outcome = self.pass_outcome(workflow_id)
        self.assertEqual(calls, {"produce": 1, "spawn": 1})
        self.assertEqual((outcome.outcome, outcome.problem, outcome.detail), (
            "delivery_held", broker_module.PROBLEM_VERIFICATION_START,
            "the verification could not start: SpawnGated"))
        claim, refused = self.attempts(workflow_id)
        self.assertEqual(refused, "%s 1 settled: not-started — refused before any process"
                                  " was started (SpawnGated)" % self.ATTEMPT)
        os.unlink(process_ownership.freeze_path(scope))
        with self.seams() as calls:
            outcome = self.pass_outcome(workflow_id)
        self.assertEqual(outcome.outcome, "delivery_awaiting_decision",
                         (outcome.problem, outcome.detail))
        self.assertEqual(calls, {"produce": 1, "spawn": 1})
        self.assert_one_settled_attempt(workflow_id, number=2, before=[claim, refused])

    def test_VL6d_a_spawn_that_raised_before_creating_its_root_resolves_as_not_started(self):
        """START UNKNOWN at the pass (the spawn raised — here before it
        created its owned root: the pending ledger record failed, injected);
        at re-entry the owned roots show none was created since the claim,
        so it resolves ``not-started`` and a new attempt runs once."""
        mission_id, workflow_id = self.ready()
        with self.seams() as calls, self.captured_children() as children, \
                mock.patch.object(process_ownership, "record_pending",
                                  side_effect=OSError(5, "ledger write failed")):
            outcome = self.pass_outcome(workflow_id)
        self.assertEqual((calls, children), ({"produce": 1, "spawn": 1}, []))
        self.assertEqual(outcome.problem, broker_module.PROBLEM_VERIFICATION_START_UNKNOWN)
        claim, unknown = self.attempts(workflow_id)
        with self.seams() as calls:
            outcome = self.pass_outcome(workflow_id)
        self.assertEqual(outcome.outcome, "delivery_awaiting_decision",
                         (outcome.problem, outcome.detail))
        self.assertEqual(calls, {"produce": 1, "spawn": 1})
        self.assert_one_settled_attempt(workflow_id, number=2, before=[claim, unknown, (
            "%s 1 settled: not-started — resolved: its spawn created no owned root (0"
            " before it, 0 now), so no process started" % self.ATTEMPT)])

    def test_VL5b_a_group_record_that_names_no_group_is_unavailable_never_a_crash(self):
        """A root's group record whose CONTENT names no process group —
        undecodable bytes, a non-ASCII digit ``str.isdigit`` admits and
        ``int`` rejects, an id too large for any group, the reserved id 1 —
        is UNAVAILABLE evidence: a truthful hold, never an exception out of
        the pass, and neither seam reached."""
        mission_id, workflow_id = self.ready()
        process_ownership.assign_scope(
            process_ownership.OWNER_TYPE_WORKFLOW, self.control_of(workflow_id),
            workflow_id, verification_module.VERIFICATION_OWNER_UNIT)
        root = os.path.join(process_ownership.owned_root_base(self.scope(workflow_id)),
                            "own-0000000000000000")
        os.makedirs(root)
        record = os.path.join(root, process_ownership.OWNED_ROOT_PGID_FILE)
        for content in (b"\xff\xfe", "²".encode("utf-8"), b"9" * 40, b"1", b""):
            with self.subTest(content=content):
                with open(record, "wb") as handle:
                    handle.write(content)
                self.held_on_ownership(
                    workflow_id, "unavailable: owned root own-0000000000000000's group"
                                 " record is not a group id")
        self.assertEqual(self.attempts(workflow_id), [])
        shutil.rmtree(root)
        with self.seams() as calls:
            outcome = self.pass_outcome(workflow_id)
        self.assertEqual(outcome.outcome, "delivery_awaiting_decision",
                         (outcome.problem, outcome.detail))
        self.assertEqual(calls, {"produce": 1, "spawn": 1})
        self.assert_one_settled_attempt(workflow_id)

    def unreadable_group_record(self, scope):
        root = os.path.join(process_ownership.owned_root_base(scope), "own-0000000000000000")
        os.makedirs(root)
        path = os.path.join(root, process_ownership.OWNED_ROOT_PGID_FILE)
        with open(path, "w") as handle:
            handle.write("999999")
        os.chmod(path, 0)

    def readable_group_record(self, scope):
        root = os.path.join(process_ownership.owned_root_base(scope), "own-0000000000000000")
        path = os.path.join(root, process_ownership.OWNED_ROOT_PGID_FILE)
        if os.path.exists(root):
            os.chmod(path, 0o644)
            shutil.rmtree(root)

    def malformed_assignment(self, scope):
        path = process_ownership.assignment_path(os.path.basename(scope))
        with open(path, "rb") as handle:
            self._assignment = handle.read()
        with open(path, "w") as handle:
            handle.write("{oops")

    def valid_assignment(self, scope):
        path = process_ownership.assignment_path(os.path.basename(scope))
        if getattr(self, "_assignment", None) is not None:
            with open(path, "wb") as handle:
                handle.write(self._assignment)
            self._assignment = None


class R20VerificationRetentionTests(DeliveryCase):
    """Task 8 R20-1: an UNRESOLVED verification is carried through cleanup
    eligibility, pruning, release and scope retirement — the lease, the
    workflow record and the ownership records are kept until settlement or
    absence is ESTABLISHED, and unreadable or ambiguous evidence keeps them —
    while a settled verification is cleaned up and pruned as before.

    Every case runs the PRODUCTION routes: the Runtime pass
    (``process_once``: cleanup candidates, then ``ACTION_RELEASE``), the
    Broker's own release action, the Mission bootstrap's insertion
    (``MissionControl.dispatch``: ``has_room``, then ``add_workflow`` pruning
    with the canonical-obligation predicate) and startup recovery. Counts are
    taken on the real seams, each delegating to the production function:
    the producer and the spawn (a replay), the workspace relinquish and the
    scope retirement (a deletion), and pruning."""

    # R19's fixtures (the real producer and spawn, an owner that dies
    # mid-run, the verification scope), borrowed without its tests.
    _R19 = R19VerificationLifecycleTests
    ATTEMPT = _R19.ATTEMPT
    seams = _R19.seams
    collect = staticmethod(_R19.collect)
    kill_group = staticmethod(_R19.kill_group)
    attempts = _R19.attempts
    scope = _R19.scope
    control_of = _R19.control_of
    group_members = _R19.group_members
    the_group = _R19.the_group
    members_after = _R19.members_after
    owner_died_mid_run = _R19.owner_died_mid_run
    root_name = _R19.root_name
    unreadable_group_record = _R19.unreadable_group_record

    INTERRUPTED = ("%s 1 settled: interrupted — claimed and never settled by its own pass;"
                   " no verification process of this workflow can be alive now (1 owned"
                   " root(s), every recorded group gone); its outcome is unknown"
                   % broker_module.VERIFICATION_ATTEMPT_RECEIPT_MARKER)

    @contextlib.contextmanager
    def destruction(self):
        """``{"relinquish": n, "revoke": n, "close": n, "retired": [...],
        "refused": [...], "pruned": [...]}`` over the calls run inside: each
        workspace relinquish (the directory removal and the lease release),
        each trust revocation, each session close the engine was asked for,
        each scope the retirement removed or refused, and the EXACT count
        every pruning returned (``has_room`` prunes a copy, ``add_workflow``
        the store)."""
        counts = {"relinquish": 0, "revoke": 0, "close": 0, "retired": [], "refused": [],
                  "pruned": []}
        worker = self.broker.worker
        real_relinquish = worker.relinquish_workspace
        real_revoke = worker.revoke_workspace_trust
        real_retire = process_ownership.retire_workflow_scopes
        real_prune = wa_store._prune_inactive
        closes = len(self.engine.close_calls)

        def relinquish(*args, **kwargs):
            counts["relinquish"] += 1
            return real_relinquish(*args, **kwargs)

        def revoke(*args, **kwargs):
            counts["revoke"] += 1
            return real_revoke(*args, **kwargs)

        def retire(*args, **kwargs):
            retired, refused = real_retire(*args, **kwargs)
            counts["retired"].extend(retired)
            counts["refused"].extend(refused)
            return retired, refused

        def prune(*args, **kwargs):
            pruned = real_prune(*args, **kwargs)
            counts["pruned"].append(pruned)
            return pruned
        try:
            with mock.patch.object(worker, "relinquish_workspace", relinquish), \
                    mock.patch.object(worker, "revoke_workspace_trust", revoke), \
                    mock.patch.object(process_ownership, "retire_workflow_scopes", retire), \
                    mock.patch.object(wa_store, "_prune_inactive", prune):
                yield counts
        finally:
            counts["close"] = len(self.engine.close_calls) - closes

    def expire_retention(self, workflow_id):
        deadline = self.record(workflow_id)[wa_record.RETENTION_KEY]["deadline_at"]
        self.clock.now = max(self.clock.now, deadline + 1)

    def cleanup_candidates(self):
        return runtime_module.terminal_cleanup_candidates(
            self.store_dir, self.gate, self.clock())

    def verification_owner(self, workflow_id):
        return (process_ownership.OWNER_TYPE_WORKFLOW,
                process_ownership.control_digest(self.control_of(workflow_id)),
                workflow_id, verification_module.VERIFICATION_OWNER_UNIT)

    def ready(self, argv=None):
        """R19's ready workflow, plus the control-side child record its
        spawn wrote (the lease and the bound task — test_mission_controls'
        idiom), so that a release can PROVE no session remains and reach the
        destructive boundary: without it every release here retains the
        directory for an unproven session close, whatever R20 decides."""
        from target_runtime import ownership as ownership_module
        from test_target_runtime import real_shaped_spawn_result
        from test_mission_engagement import AGENTS, WORKSPACE_ID
        mission_id, workflow_id = self._R19.ready(self, argv=argv)
        record = self.record(workflow_id)
        child = real_shaped_spawn_result(
            record["workspace_lease"]["path_realpath"],
            ownership_module.recorded_task_id(record), self.control)["child_record"]
        child.update(workspace_id=WORKSPACE_ID, agents=dict(AGENTS))
        self.spawn_record_overrides = {"records": [child]}
        return mission_id, workflow_id

    def verified_and_settled(self):
        """One verification run to its end: claimed, settled ``returned``,
        its group reaped — the settled case."""
        mission_id, workflow_id = self.ready()
        with self.seams() as calls:
            outcome = self.pass_outcome(workflow_id)
        self.assertEqual(outcome.outcome, "delivery_awaiting_decision",
                         (outcome.problem, outcome.detail))
        self.assertEqual(calls, {"produce": 1, "spawn": 1})
        attempts = self.attempts(workflow_id)
        self.assertEqual(len(attempts), 2, attempts)
        self.assertIn(" settled: returned — ", attempts[1])
        return mission_id, workflow_id

    def retained(self, workflow_id, detail, passes=2, problem=None, scopes=None):
        """Retained through the Runtime pass AND the release action: no
        cleanup candidate, the release refused with ``problem`` (default the
        verification hold's) and ``detail`` — no relinquish, no scope
        retired, no producer or spawn — the lease unreleased, its directory,
        ``scopes`` (default: the verification scope) and the record all
        kept, the attempt records unchanged."""
        problem = problem or broker_module.PROBLEM_VERIFICATION_RETAINED
        scopes = [self.scope(workflow_id)] if scopes is None else scopes
        lease = self.lease_path(workflow_id)
        before = self.attempts(workflow_id)
        for _ in range(passes):
            with self.seams() as calls, self.destruction() as gone:
                runtime_module.process_once(self.broker)
            self.assertEqual(calls, {"produce": 0, "spawn": 0})
            self.assertEqual((gone["relinquish"], gone["retired"]), (0, []))
            self.assertEqual(self.cleanup_candidates(), [])
        with self.seams() as calls, self.destruction() as gone:
            released = self.act(workflow_id, broker_module.ACTION_RELEASE)
        self.assertEqual(calls, {"produce": 0, "spawn": 0})
        self.assertEqual((released.ok, released.problem, released.detail),
                         (False, problem, detail))
        # Refused BEFORE anything: no trust revoked, no session closed, no
        # directory or lease released, no scope retired.
        self.assertEqual((gone["revoke"], gone["close"], gone["relinquish"], gone["retired"]),
                         (0, 0, 0, []))
        self.assertIsNone(self.record(workflow_id)["workspace_lease"]["released_at"])
        self.assertTrue(os.path.isdir(lease))
        for scope in scopes:
            self.assertTrue(os.path.isdir(scope), scope)
        self.assertEqual(self.attempts(workflow_id), before)

    def released_cleanly(self, workflow_id, scopes=None):
        """The next Runtime pass releases it: ONE relinquish, EXACTLY
        ``scopes`` retired (default: the verification scope, this workflow's
        only scope when its engineering ran through the engine double),
        nothing refused, nothing replayed."""
        scopes = [self.scope(workflow_id)] if scopes is None else scopes
        lease = self.lease_path(workflow_id)
        with self.seams() as calls, self.destruction() as gone:
            runtime_module.process_once(self.broker)
        self.assertEqual(calls, {"produce": 0, "spawn": 0})
        self.assertEqual(gone["relinquish"], 1)
        self.assertEqual(gone["retired"], scopes)
        self.assertEqual(gone["refused"], [])
        self.assertIsNotNone(self.record(workflow_id)["workspace_lease"]["released_at"])
        self.assertFalse(os.path.exists(lease))
        for scope in scopes:
            self.assertFalse(os.path.exists(scope), scope)

    def insert_under_pressure(self):
        """A new Mission's row inserted through the production bootstrap
        into a store already at its cap: ``(result, pruned counts, rows
        before, rows after)``."""
        other = self.ready_mission()
        before = sorted(self.rows())
        with mock.patch.object(wa_store, "MAX_WORKFLOW_RECORDS", len(before)), \
                self.destruction() as gone:
            result = self.bootstrap(other)
        return result, gone["pruned"], before, sorted(self.rows())

    def never_pruned(self, workflow_id):
        result, pruned, before, after = self.insert_under_pressure()
        self.assertFalse(result["ok"], result)
        self.assertIn(wa_store.PROBLEM_STORE_FULL, result["detail"])
        self.assertEqual(pruned, [0])                  # has_room's copy: nothing prunable
        self.assertEqual(after, before)
        self.assertIn(workflow_id, after)

    def pruned_once(self, workflow_id):
        """EXACTLY this record pruned (and only it), the new row inserted."""
        result, pruned, before, after = self.insert_under_pressure()
        self.assertTrue(result["ok"], result)
        self.assertEqual(pruned, [1, 1])               # has_room's copy, then the store
        self.assertEqual(sorted(set(before) - set(after)), [workflow_id])
        self.assertEqual(len(set(after) - set(before)), 1)
        self.assertEqual(len(after), len(before))

    # -- the settled positive ----------------------------------------------------------

    def test_RV0_a_settled_verification_is_cleaned_up_retired_and_pruned(self):
        """The valid SETTLED cleanup still proceeds: a candidate once retention
        expired, released by the pass with ONE relinquish and its
        verification scope retired, then pruned EXACTLY once under capacity
        pressure — and kept from pruning only while its lease was held."""
        mission_id, workflow_id = self.verified_and_settled()
        attempts = self.attempts(workflow_id)
        self.expire_retention(workflow_id)
        self.never_pruned(workflow_id)                 # its lease is still held
        self.assertEqual(self.cleanup_candidates(),
                         [(workflow_id, self.record(workflow_id)["handoff"]["revision"])])
        self.released_cleanly(workflow_id)
        self.assertEqual(self.attempts(workflow_id), attempts)
        self.pruned_once(workflow_id)

    # -- retention expiry: a live group whose owner died ----------------------------

    def test_RV1_retention_expiry_keeps_an_unresolved_verification_until_recovery_settles_it(self):
        mission_id, workflow_id = self.ready(argv=[
            sys.executable, "-c",
            "import subprocess, time; subprocess.Popen(['sleep', '120']); time.sleep(30)"])
        [claim] = self.owner_died_mid_run(workflow_id)
        pgid = self.the_group(workflow_id)
        self.assertEqual(len(self.members_after(pgid, 2)), 2)   # leader + descendant
        self.expire_retention(workflow_id)
        self.retained(workflow_id, (
            "the verification scope's ownership is unresolved: group %d of owned root %s is"
            " alive and corroborated as this verification's; nothing is released"
            % (pgid, self.root_name(workflow_id))))
        self.never_pruned(workflow_id)
        # Restart recovery still attributes the verification scope and reaps
        # the whole group; absence established, the next pass settles the
        # attempt INTERRUPTED once and releases.
        self.assertIn(self.verification_owner(workflow_id),
                      runtime_module.current_scope_owners(self.store_dir))
        runtime_module.recover_inherited_processes(self.store_dir)
        self.assertEqual(self.group_members(pgid), [])
        self.released_cleanly(workflow_id)
        self.assertEqual(self.attempts(workflow_id), [claim, self.INTERRUPTED])
        self.pruned_once(workflow_id)

    # -- cancellation and surviving descendants ------------------------------------

    def test_RV2_a_cancel_never_releases_a_verification_whose_descendants_survive(self):
        """The leader ended, the Runtime died before its reap, a DESCENDANT
        survives (its ownership cannot be corroborated). A confirmed cancel
        releases the retention — and the workflow is still retained: no
        candidate, no release, the scope's retirement refused, recovery never
        signals it. Once the fixture has TERMINATED the descendant (a
        controlled kill of its own process, never an observed spontaneous
        end), absence is established and the next pass releases."""
        mission_id, workflow_id = self.ready(argv=[
            sys.executable, "-c", "import subprocess; subprocess.Popen(['sleep', '120'])"])
        [claim] = self.owner_died_mid_run(workflow_id, leader_exits_first=True)
        pgid = self.the_group(workflow_id)
        self.assertEqual(len(self.members_after(pgid, 1)), 1)
        self.service.request_cancel(mission_id)
        runtime_module.process_once(self.broker)
        self.service.confirm_cancel(mission_id)
        runtime_module.process_once(self.broker)
        retention = self.record(workflow_id)[wa_record.RETENTION_KEY]
        self.assertEqual(retention["release_reason"], wa_record.RETENTION_RELEASE_CANCEL_CONFIRMED)
        self.assertFalse(wa_store.retention_protects(self.record(workflow_id), self.clock()))
        # Task 8 R22-1: the reason is NAMED — the value moved, the hold did not.
        leaderless = ("the verification scope's ownership is unresolved: group %d of owned"
                      " root %s is alive and its ownership cannot be corroborated (%s);"
                      " nothing is released"
                      % (pgid, self.root_name(workflow_id),
                         process_ownership.UNCORROBORATED_LEADERLESS))
        self.retained(workflow_id, leaderless)
        retired, refused = process_ownership.retire_workflow_scopes(
            self.control_of(workflow_id), workflow_id)
        self.assertEqual((retired, refused), (
            [], [(self.scope(workflow_id), process_ownership.RETIRE_REFUSED_LEADERLESS)]))
        self.assertTrue(os.path.isdir(self.scope(workflow_id)))
        runtime_module.recover_inherited_processes(self.store_dir)
        self.assertEqual(len(self.group_members(pgid)), 1)      # never signalled
        self.never_pruned(workflow_id)
        self.retained(workflow_id, leaderless, passes=1)
        # CONTROLLED termination by the fixture (its own process), to reach
        # the absent-group state; nothing in the Runtime signalled it.
        self.kill_group(pgid)
        self.assertEqual(self.members_after(pgid, 0), [])
        self.released_cleanly(workflow_id)
        self.assertEqual(self.attempts(workflow_id), [claim, self.INTERRUPTED])

    # -- capacity pressure -----------------------------------------------------------

    def test_RV3_capacity_pressure_never_prunes_an_unsettled_attempt(self):
        """The pass died after the spawn, its own reap ran (nothing is
        alive): the claim is UNSETTLED. Under capacity pressure the record is
        never pruned; the release settles it INTERRUPTED once (absence is
        established) and releases; only then is it pruned, once."""
        mission_id, workflow_id = self.ready()

        def interrupted(process):
            def wait(*args, **kwargs):
                raise Crash("the pass died while the verification ran")
            process.wait = wait
        with self.seams(on_spawn=interrupted) as calls, self.assertRaises(Crash):
            self.pass_outcome(workflow_id)
        self.assertEqual(calls, {"produce": 1, "spawn": 1})
        [claim] = self.attempts(workflow_id)
        self.expire_retention(workflow_id)
        self.never_pruned(workflow_id)
        self.released_cleanly(workflow_id)
        self.assertEqual(self.attempts(workflow_id), [claim, self.INTERRUPTED])
        self.pruned_once(workflow_id)

    def test_RV3b_capacity_pressure_never_prunes_a_settled_attempt_whose_group_lives(self):
        """Every attempt SETTLED, yet a verification group of the workflow is
        alive in its scope: the held lease keeps the record from pruning, and
        the ownership records keep it from cleanup — until the fixture
        terminates the group (a controlled kill of its own process)."""
        mission_id, workflow_id = self.verified_and_settled()
        process = process_ownership.spawn_owned(
            ["sleep", "60"], verification_module.VERIFICATION_LABEL,
            directory=self.scope(workflow_id), owned_root_base_dir=self.scope(workflow_id))
        self.addCleanup(self.collect, process.wait)
        self.addCleanup(self.kill_group, process.pid)
        self.assertEqual(len(self.members_after(process.pid, 1)), 1)
        self.expire_retention(workflow_id)
        prefix = process_ownership.owned_root_base(self.scope(workflow_id))
        [live] = [name for name in sorted(os.listdir(prefix))
                  if os.path.isdir(os.path.join(prefix, name))
                  and self.group_of(os.path.join(prefix, name)) == process.pid]
        self.never_pruned(workflow_id)
        self.retained(workflow_id, (
            "the verification scope's ownership is unresolved: group %d of owned root %s is"
            " alive and corroborated as this verification's; nothing is released"
            % (process.pid, live)))
        self.kill_group(process.pid)
        process.wait(timeout=10)
        self.released_cleanly(workflow_id)
        self.pruned_once(workflow_id)

    def released_yet_unresolved(self, summary):
        """Fail closed on a record no release produces: its lease released,
        yet carrying ``summary`` — ambiguity is not settlement, so it is
        never pruned; without it the same record is pruned once."""
        mission_id, workflow_id = self.verified_and_settled()
        self.expire_retention(workflow_id)
        self.released_cleanly(workflow_id)
        self.attempt_record(workflow_id, summary, add=True)
        self.never_pruned(workflow_id)
        self.attempt_record(workflow_id, summary, add=False)
        self.pruned_once(workflow_id)

    def test_RV7a_a_released_record_with_an_unsettled_claim_is_never_pruned(self):
        self.released_yet_unresolved("%s 2 claimed roots=1 (crafted)" % self.ATTEMPT)

    def test_RV7b_a_released_record_with_a_start_unknown_attempt_is_never_pruned(self):
        self.released_yet_unresolved("%s 2 settled: start-unknown — crafted" % self.ATTEMPT)

    def test_RV7c_a_released_record_with_an_undecodable_attempt_is_never_pruned(self):
        self.released_yet_unresolved("%s 2x settled: returned — crafted" % self.ATTEMPT)

    def attempt_record(self, workflow_id, summary, add):
        workflows = self.broker.store.load()
        receipts = workflows["workflows"][workflow_id]["receipts"]
        if add:
            receipts.append({
                "kind": wa_record.RECEIPT_KIND_EVIDENCE, "turn_id": "mverify-00000000000000ff",
                "recorded_at": self.clock(), "bounded_summary": summary,
                "digest": hashlib.sha256(summary.encode("utf-8")).hexdigest()})
        else:
            receipts[:] = [r for r in receipts if r["bounded_summary"] != summary]
        self.broker.store.save(workflows)

    @staticmethod
    def group_of(root):
        with open(os.path.join(root, process_ownership.OWNED_ROOT_PGID_FILE)) as handle:
            return int(handle.read())

    # -- unreadable or ambiguous evidence retains ------------------------------------

    def test_RV4_unreadable_ownership_evidence_retains_everything(self):
        if os.geteuid() == 0:
            self.skipTest("permission bits do not bind root")
        mission_id, workflow_id = self.verified_and_settled()
        self.expire_retention(workflow_id)
        scope = self.scope(workflow_id)
        self.unreadable_group_record(scope)
        self.addCleanup(self.clear_damage, scope)
        try:
            self.retained(workflow_id, (
                "the verification scope's ownership is unavailable: owned root"
                " own-0000000000000000's group record cannot be read (PermissionError);"
                " nothing is released"))
            self.never_pruned(workflow_id)
            retired, refused = process_ownership.retire_workflow_scopes(
                self.control_of(workflow_id), workflow_id)
            self.assertEqual((retired, refused),
                             ([], [(scope, process_ownership.RETIRE_REFUSED_UNREADABLE)]))
        finally:
            self.clear_damage(scope)
        self.released_cleanly(workflow_id)

    def test_RV5_ambiguous_evidence_retains_everything(self):
        """A root never stamped (whether its process started cannot be known)
        and an attempt record that does not decode are each AMBIGUOUS: never
        read as absence, never as settlement."""
        mission_id, workflow_id = self.verified_and_settled()
        self.expire_retention(workflow_id)
        scope = self.scope(workflow_id)
        root = process_ownership.create_owned_root("own-0000000000000000", scope)
        self.retained(workflow_id, (
            "the verification scope's ownership is unresolved: owned root"
            " own-0000000000000000 was never stamped with a process group; nothing is"
            " released"))
        self.never_pruned(workflow_id)
        retired, refused = process_ownership.retire_workflow_scopes(
            self.control_of(workflow_id), workflow_id)
        self.assertEqual((retired, refused),
                         ([], [(scope, process_ownership.RETIRE_REFUSED_UNSTAMPED)]))
        shutil.rmtree(root)
        # An attempt record that does not decode.
        self.attempt_record(workflow_id, "%s 1x settled: returned — not a number"
                            % self.ATTEMPT, add=True)
        self.retained(workflow_id, (
            "1 verification attempt record(s) do not decode, so no attempt can be read as"
            " settled; nothing is released"))
        self.never_pruned(workflow_id)

    # -- the destructive boundary --------------------------------------------------------

    def turns_unclear_during_the_release(self, damage, detail):
        """The hold at the top of the release reads CLEAR; DURING the
        preservation window (``damage``, run as the real preservation
        returns) the verification scope's evidence turns unclear. The hold
        re-established at the destructive boundary reads it: the lease, its
        directory, the record and the ownership records are kept and no
        scope is retired — and once the evidence is clear again the next
        pass releases."""
        from target_runtime import evidence_preservation
        mission_id, workflow_id = self.verified_and_settled()
        self.expire_retention(workflow_id)
        scope = self.scope(workflow_id)
        lease = self.lease_path(workflow_id)
        real_preserve = evidence_preservation.preserve
        windows = []

        def preserve(*args, **kwargs):
            result = real_preserve(*args, **kwargs)
            windows.append(result[0])
            damage(scope)                              # inside the preservation window
            return result
        self.addCleanup(self.clear_damage, scope)
        try:
            with self.seams() as calls, self.destruction() as gone, \
                    mock.patch.object(evidence_preservation, "preserve", preserve):
                released = self.act(workflow_id, broker_module.ACTION_RELEASE)
            self.assertEqual(windows, [True])         # the preservation itself succeeded
            self.assertEqual(calls, {"produce": 0, "spawn": 0})
            self.assertEqual((released.ok, released.problem),
                             (False, broker_module.PROBLEM_VERIFICATION_RETAINED))
            self.assertTrue(released.detail.startswith(
                detail + " (at the destructive boundary; "), released.detail)
            # What ran before the boundary ran (the trust revocation, the
            # session close); the boundary itself released nothing.
            self.assertEqual((gone["revoke"], gone["close"], gone["relinquish"],
                              gone["retired"]), (1, 1, 0, []))
            self.assertIsNone(self.record(workflow_id)["workspace_lease"]["released_at"])
            self.assertTrue(os.path.isdir(lease))
            self.assertTrue(os.path.isdir(scope))
            self.never_pruned(workflow_id)
        finally:
            self.clear_damage(scope)
        self.released_cleanly(workflow_id)

    @staticmethod
    def clear_damage(scope):
        """Remove the damaged root ``own-0000000000000000``, whatever was done
        to it (idempotent)."""
        root = os.path.join(process_ownership.owned_root_base(scope), "own-0000000000000000")
        record = os.path.join(root, process_ownership.OWNED_ROOT_PGID_FILE)
        if os.path.exists(record):
            os.chmod(record, 0o644)
        if os.path.isdir(root):
            shutil.rmtree(root)

    def test_RV6a_evidence_unreadable_during_the_release_is_read_at_its_boundary(self):
        if os.geteuid() == 0:
            self.skipTest("permission bits do not bind root")
        self.turns_unclear_during_the_release(
            self.unreadable_group_record,
            "the verification scope's ownership is unavailable: owned root"
            " own-0000000000000000's group record cannot be read (PermissionError);"
            " nothing is released")

    def test_RV6b_a_never_stamped_root_appearing_during_the_release_is_read_at_its_boundary(self):
        self.turns_unclear_during_the_release(
            lambda scope: process_ownership.create_owned_root("own-0000000000000000", scope),
            "the verification scope's ownership is unresolved: owned root"
            " own-0000000000000000 was never stamped with a process group; nothing is"
            " released")


class R20BTaskScopeRetentionTests(DeliveryCase):
    """Task 8 R20-2: a workflow's TASK scope (and every other process scope
    it owns besides the verification scope) is carried like R20-1's
    verification scope. A retirement it would refuse keeps the lease, the
    workflow record and with it the recovery owner — never released, then
    pruned, leaving a live scope that ``current_scope_owners`` no longer
    names — while a settled, absent scope still releases, retires and
    prunes exactly.

    The same PRODUCTION routes and seams as R20 (``process_once``,
    ``ACTION_RELEASE``, the bootstrap's capacity-pressure insertion,
    startup recovery). The task scope is ASSIGNED by the production
    role-turn assignment (``role_turn._owner_scope_for``) and its processes
    are spawned through the owned path exactly as a role turn spawns
    them."""

    _R19 = R19VerificationLifecycleTests
    _R20 = R20VerificationRetentionTests
    ATTEMPT = _R19.ATTEMPT
    seams = _R19.seams
    collect = staticmethod(_R19.collect)
    kill_group = staticmethod(_R19.kill_group)
    attempts = _R19.attempts
    scope = _R19.scope
    control_of = _R19.control_of
    group_members = _R19.group_members
    members_after = _R19.members_after
    unreadable_group_record = _R19.unreadable_group_record
    destruction = _R20.destruction
    expire_retention = _R20.expire_retention
    cleanup_candidates = _R20.cleanup_candidates
    ready = _R20.ready
    verified_and_settled = _R20.verified_and_settled
    retained = _R20.retained
    released_cleanly = _R20.released_cleanly
    insert_under_pressure = _R20.insert_under_pressure
    never_pruned = _R20.never_pruned
    pruned_once = _R20.pruned_once
    clear_damage = staticmethod(_R20.clear_damage)

    LABEL = "codex-role-turn"
    RETAINED = broker_module.PROBLEM_PROCESS_SCOPE_RETAINED

    def task_scope(self, workflow_id):
        """The workflow's task scope, ASSIGNED as a role turn assigns it."""
        from codex_gateway import role_turn
        record = self.record(workflow_id)
        self.assertNotIn(record["target_engine"]["task_id"], (None, "", "pre-dispatch"))
        return role_turn._owner_scope_for(record)

    def task_owner(self, workflow_id):
        return (process_ownership.OWNER_TYPE_WORKFLOW,
                process_ownership.control_digest(self.control_of(workflow_id)),
                workflow_id, self.record(workflow_id)["target_engine"]["task_id"])

    def spawn_in(self, scope, argv, spawn=None):
        """A process spawned into ``scope`` through the owned path (``spawn``:
        the real ``spawn_owned`` captured before a seam counts it), as a role
        turn spawns it; its group is killed and the leader collected at
        cleanup whatever the test's outcome (this test's own process)."""
        process = (spawn or process_ownership.spawn_owned)(
            argv, self.LABEL, directory=scope, owned_root_base_dir=scope)
        self.addCleanup(self.collect, process.wait)
        self.addCleanup(self.kill_group, process.pid)
        return process

    def dead_stamped_root(self, scope):
        """A role turn that ran to its end in ``scope``: its root stamped,
        its group gone."""
        process = self.spawn_in(scope, ["true"])
        process.wait(timeout=10)
        self.assertEqual(self.members_after(process.pid, 0), [])

    @staticmethod
    def held(scope, reason, count=1):
        return ("%d process scope(s) of this workflow cannot be retired — scope %s: %s;"
                " nothing is released" % (count, os.path.basename(scope), reason))

    def both(self, workflow_id, task):
        """The two scopes, in the order retirement enumerates them."""
        return sorted([self.scope(workflow_id), task])

    def scope_refusals(self, workflow_id):
        return process_ownership.owned_scope_refusals(
            self.control_of(workflow_id), workflow_id,
            skip_units=(verification_module.VERIFICATION_OWNER_UNIT,))

    # -- the settled / absent positive -------------------------------------------------

    def test_RS5_a_settled_absent_task_scope_is_released_retired_and_pruned_exactly(self):
        """The task scope's role turn ran to its end (its root stamped, its
        group gone) beside a settled verification: a candidate once retention
        expired, released by the pass with ONE relinquish and EXACTLY both
        scopes retired, then pruned EXACTLY once — kept from pruning only
        while its lease was held."""
        mission_id, workflow_id = self.verified_and_settled()
        task = self.task_scope(workflow_id)
        self.dead_stamped_root(task)
        self.assertEqual(self.scope_refusals(workflow_id), ([], None))
        self.expire_retention(workflow_id)
        self.never_pruned(workflow_id)                 # its lease is still held
        self.assertEqual(self.cleanup_candidates(),
                         [(workflow_id, self.record(workflow_id)["handoff"]["revision"])])
        self.released_cleanly(workflow_id, scopes=self.both(workflow_id, task))
        self.assertEqual([r for r in self.record(workflow_id)["receipts"]
                          if r["bounded_summary"].startswith(
                              wa_record.PROCESS_SCOPE_RETAINED_RECEIPT_MARKER)], [])
        self.pruned_once(workflow_id)

    # -- the refused task scope: the loss-of-owner sequence ---------------------------

    def test_RS1_a_live_task_group_keeps_the_lease_record_and_recovery_owner(self):
        """A corroborated group of the task scope is alive when retention
        expires. Before R20-2 the release checked the verification scope
        alone, released the lease, had the task scope's retirement REFUSED
        (reported degraded) and the record was then pruned — leaving the
        live scope with no owner ``current_scope_owners`` names. Now: no
        candidate, the release refused before anything, never pruned, the
        owner still listed; startup recovery reaps the group through that
        owner, and only then is it released, retired and pruned exactly."""
        mission_id, workflow_id = self.verified_and_settled()
        task = self.task_scope(workflow_id)
        process = self.spawn_in(task, ["sleep", "60"])
        self.assertEqual(len(self.members_after(process.pid, 1)), 1)
        self.expire_retention(workflow_id)
        self.assertEqual(self.scope_refusals(workflow_id), (
            [(task, process_ownership.RETIRE_REFUSED_LIVE_GROUP)], None))
        self.retained(workflow_id, self.held(task, process_ownership.RETIRE_REFUSED_LIVE_GROUP),
                      problem=self.RETAINED, scopes=self.both(workflow_id, task))
        self.never_pruned(workflow_id)
        owner = self.task_owner(workflow_id)
        self.assertIn(owner, runtime_module.current_scope_owners(self.store_dir))
        results, unattributed = runtime_module.recover_inherited_processes(self.store_dir)
        self.assertEqual([(tuple(identity), recovered) for identity, recovered, *_ in results
                          if identity.owner_id == workflow_id], [(owner, [process.pid])])
        self.assertNotIn(task, [d for d, _ in unattributed])
        process.wait(timeout=10)
        self.assertEqual(self.group_members(process.pid), [])
        self.released_cleanly(workflow_id, scopes=self.both(workflow_id, task))
        self.pruned_once(workflow_id)

    # -- a surviving descendant ----------------------------------------------------------

    def test_RS2_a_cancel_never_releases_a_task_scope_whose_descendant_survives(self):
        """The task scope's leader ended and was collected; its DESCENDANT
        survives (ownership cannot be corroborated). A confirmed cancel
        releases the retention — and the workflow is still retained: no
        candidate, no release, never pruned; recovery never signals the
        group. Once the fixture TERMINATES the descendant (a controlled kill
        of its own process), the next pass releases and prunes exactly."""
        mission_id, workflow_id = self.verified_and_settled()
        task = self.task_scope(workflow_id)
        process = self.spawn_in(task, [
            sys.executable, "-c", "import subprocess; subprocess.Popen(['sleep', '120'])"])
        process.wait(timeout=10)                       # the leader ended and is collected
        self.assertEqual(len(self.members_after(process.pid, 1)), 1)
        self.service.request_cancel(mission_id)
        runtime_module.process_once(self.broker)
        self.service.confirm_cancel(mission_id)
        runtime_module.process_once(self.broker)
        retention = self.record(workflow_id)[wa_record.RETENTION_KEY]
        self.assertEqual(retention["release_reason"], wa_record.RETENTION_RELEASE_CANCEL_CONFIRMED)
        self.assertFalse(wa_store.retention_protects(self.record(workflow_id), self.clock()))
        leaderless = self.held(task, process_ownership.RETIRE_REFUSED_LEADERLESS)
        self.retained(workflow_id, leaderless, problem=self.RETAINED,
                      scopes=self.both(workflow_id, task))
        self.assertEqual(self.scope_refusals(workflow_id), (
            [(task, process_ownership.RETIRE_REFUSED_LEADERLESS)], None))
        runtime_module.recover_inherited_processes(self.store_dir)
        self.assertEqual(len(self.group_members(process.pid)), 1)   # never signalled
        self.never_pruned(workflow_id)
        self.retained(workflow_id, leaderless, passes=1, problem=self.RETAINED,
                      scopes=self.both(workflow_id, task))
        # CONTROLLED termination by the fixture (its own process), to reach
        # the absent-group state; nothing in the Runtime signalled it.
        self.kill_group(process.pid)
        self.assertEqual(self.members_after(process.pid, 0), [])
        self.released_cleanly(workflow_id, scopes=self.both(workflow_id, task))
        self.pruned_once(workflow_id)

    # -- unreadable or ambiguous evidence retains -------------------------------------

    def test_RS3a_an_unreadable_task_scope_record_retains_everything(self):
        if os.geteuid() == 0:
            self.skipTest("permission bits do not bind root")
        mission_id, workflow_id = self.verified_and_settled()
        task = self.task_scope(workflow_id)
        self.expire_retention(workflow_id)
        self.unreadable_group_record(task)
        self.addCleanup(self.clear_damage, task)
        try:
            self.retained(workflow_id, self.held(task, process_ownership.RETIRE_REFUSED_UNREADABLE),
                          problem=self.RETAINED, scopes=self.both(workflow_id, task))
            self.never_pruned(workflow_id)
        finally:
            self.clear_damage(task)
        self.released_cleanly(workflow_id, scopes=self.both(workflow_id, task))
        self.pruned_once(workflow_id)

    def test_RS3b_a_name_claiming_this_workflow_without_an_assignment_retains(self):
        """A directory whose NAME claims this workflow and control repository
        with no assignment: ambiguous ownership, never read as "not ours".
        Recovery leaves it unattributed; nothing releases until it is gone."""
        mission_id, workflow_id = self.verified_and_settled()
        self.expire_retention(workflow_id)
        forged = os.path.join(process_ownership.owned_root_base(), process_ownership.scope_name(
            process_ownership.OWNER_TYPE_WORKFLOW, self.control_of(workflow_id),
            workflow_id, "unassigned-unit"))
        os.makedirs(forged)
        reason = "%s (%s)" % (process_ownership.RETIRE_REFUSED_UNATTRIBUTED,
                              process_ownership.UNATTRIBUTED_NO_ASSIGNMENT)
        self.assertEqual(self.scope_refusals(workflow_id), ([(forged, reason)], None))
        self.retained(workflow_id, self.held(forged, reason), problem=self.RETAINED,
                      scopes=[self.scope(workflow_id), forged])
        self.never_pruned(workflow_id)
        results, unattributed = runtime_module.recover_inherited_processes(self.store_dir)
        self.assertIn((forged, process_ownership.UNATTRIBUTED_NO_ASSIGNMENT), unattributed)
        self.assertTrue(os.path.isdir(forged))
        shutil.rmtree(forged)                          # the fixture's own directory
        self.released_cleanly(workflow_id)
        self.pruned_once(workflow_id)

    def test_RS6_a_matching_entry_that_is_not_a_directory_retains(self):
        """Addendum A. Entries whose NAMES claim exactly this workflow and
        control repository but which are not directories: the task scope's
        own name as a SYMBOLIC LINK to the relocated, LIVE task scope (its
        assignment valid), and the pre-dispatch scope's name as a REGULAR
        FILE. Neither is read as absent — both refused UNREADABLE, naming
        what each is — and the workflow is retained; once the fixture has
        restored the real directory and ended its group, it releases,
        retires and prunes exactly."""
        mission_id, workflow_id = self.verified_and_settled()
        task = self.task_scope(workflow_id)
        relocated = os.path.join(process_ownership.default_base(), "relocated-task-scope")
        os.rename(task, relocated)
        os.symlink(relocated, task)
        self.addCleanup(lambda: os.path.islink(task) and os.unlink(task))
        process = self.spawn_in(relocated, ["sleep", "60"])
        self.assertEqual(len(self.members_after(process.pid, 1)), 1)
        self.assertEqual(process_ownership.retirement_refusal(task),
                         process_ownership.RETIRE_REFUSED_LIVE_GROUP)   # what the link reaches
        planning = os.path.join(process_ownership.owned_root_base(), process_ownership.scope_name(
            process_ownership.OWNER_TYPE_WORKFLOW, self.control_of(workflow_id),
            workflow_id, "pre-dispatch"))
        with open(planning, "w") as handle:
            handle.write("not a scope")
        self.addCleanup(lambda: os.path.isfile(planning) and os.unlink(planning))
        self.expire_retention(workflow_id)
        refusals = sorted([
            (task, "%s (a symbolic link, not a directory)"
             % process_ownership.RETIRE_REFUSED_UNREADABLE),
            (planning, "%s (a regular file, not a directory)"
             % process_ownership.RETIRE_REFUSED_UNREADABLE)])
        self.assertEqual(self.scope_refusals(workflow_id), (refusals, None))
        self.retained(workflow_id, self.held(refusals[0][0], refusals[0][1], count=2),
                      problem=self.RETAINED, scopes=[self.scope(workflow_id)])
        self.never_pruned(workflow_id)
        self.assertTrue(os.path.islink(task))
        self.assertTrue(os.path.isfile(planning))
        self.assertEqual(len(self.group_members(process.pid)), 1)
        # CONTROLLED by the fixture: its group ended, the real directory
        # restored, the file removed.
        self.kill_group(process.pid)
        process.wait(timeout=10)
        self.assertEqual(self.members_after(process.pid, 0), [])
        os.unlink(task)
        os.rename(relocated, task)
        os.unlink(planning)
        self.released_cleanly(workflow_id, scopes=self.both(workflow_id, task))
        self.pruned_once(workflow_id)

    # -- the destructive boundary, and after it ----------------------------------------

    def test_RS4a_a_task_scope_turning_unclear_during_the_release_is_read_at_its_boundary(self):
        """The holds at the top of the release read CLEAR; DURING the
        preservation window a never-stamped root appears in the task scope.
        The scope hold RE-ESTABLISHED at the destructive boundary reads it:
        the effects already made are recorded (the trust revocation, the
        session close), the lease, its directory, the record and both scopes
        are kept, nothing is retired — and once it is clear the next pass
        releases and the record is pruned exactly."""
        from target_runtime import evidence_preservation
        mission_id, workflow_id = self.verified_and_settled()
        task = self.task_scope(workflow_id)
        self.expire_retention(workflow_id)
        lease = self.lease_path(workflow_id)
        real_preserve = evidence_preservation.preserve
        windows = []

        def preserve(*args, **kwargs):
            result = real_preserve(*args, **kwargs)
            windows.append(result[0])
            process_ownership.create_owned_root("own-0000000000000000", task)
            return result
        self.addCleanup(self.clear_damage, task)
        try:
            with self.seams() as calls, self.destruction() as gone, \
                    mock.patch.object(evidence_preservation, "preserve", preserve):
                released = self.act(workflow_id, broker_module.ACTION_RELEASE)
            self.assertEqual(windows, [True])
            self.assertEqual(calls, {"produce": 0, "spawn": 0})
            self.assertEqual((released.ok, released.problem), (False, self.RETAINED))
            self.assertTrue(released.detail.startswith(
                self.held(task, process_ownership.RETIRE_REFUSED_UNSTAMPED)
                + " (at the destructive boundary; "), released.detail)
            self.assertEqual((gone["revoke"], gone["close"], gone["relinquish"],
                              gone["retired"]), (1, 1, 0, []))
            self.assertIsNone(self.record(workflow_id)["workspace_lease"]["released_at"])
            self.assertTrue(os.path.isdir(lease))
            self.assertTrue(os.path.isdir(task))
            self.assertTrue(os.path.isdir(self.scope(workflow_id)))
            self.assertIn(self.task_owner(workflow_id),
                          runtime_module.current_scope_owners(self.store_dir))
            self.never_pruned(workflow_id)
        finally:
            self.clear_damage(task)
        self.released_cleanly(workflow_id, scopes=self.both(workflow_id, task))
        self.pruned_once(workflow_id)

    # -- a retirement refused AFTER the lease released (Addendum B) ---------------------

    def scope_receipts(self, workflow_id):
        """The ``process scope retained`` / ``settled`` receipts, in order."""
        return [r["bounded_summary"] for r in self.record(workflow_id)["receipts"]
                if r["bounded_summary"].startswith((
                    wa_record.PROCESS_SCOPE_RETAINED_RECEIPT_MARKER + ":",
                    wa_record.PROCESS_SCOPE_SETTLED_RECEIPT_MARKER + ":"))]

    def refused_late(self, workflow_id, task, damage, reason):
        """The release's boundary re-check reads CLEAR; INSIDE the relinquish
        ``damage(task)`` changes the task scope, so its retirement is REFUSED
        after the lease went: the lease is released (truthfully — it was),
        the verification scope retired, the task scope KEPT, and the durable
        ``process scope retained`` receipt written."""
        worker = self.broker.worker
        real_relinquish = worker.relinquish_workspace

        def relinquish(*args, **kwargs):
            result = real_relinquish(*args, **kwargs)
            damage(task)
            return result
        with mock.patch.object(worker, "relinquish_workspace", relinquish), \
                self.seams() as calls, self.destruction() as gone:
            released = self.act(workflow_id, broker_module.ACTION_RELEASE)
        self.assertEqual(calls, {"produce": 0, "spawn": 0})
        self.assertEqual((released.ok, released.outcome, released.problem), (
            True, broker_module.OUTCOME_RELEASED_DEGRADED,
            broker_module.ownership_module.PROBLEM_CLEANUP_DEGRADED))
        self.assertEqual((gone["revoke"], gone["close"], gone["relinquish"], gone["retired"],
                          gone["refused"]),
                         (1, 1, 1, [self.scope(workflow_id)], [(task, reason)]))
        self.assertIsNotNone(self.record(workflow_id)["workspace_lease"]["released_at"])
        self.assertFalse(os.path.exists(self.lease_path(workflow_id)))
        self.assertTrue(os.path.isdir(task))
        self.assertEqual(self.scope_receipts(workflow_id), [
            "%s: 1 scope(s) kept after the workspace lease was released — %s: %s" % (
                wa_record.PROCESS_SCOPE_RETAINED_RECEIPT_MARKER, os.path.basename(task), reason)])
        self.assertTrue(wa_record.cleanup_evidence_outstanding(self.record(workflow_id)))

    def still_outstanding(self, workflow_id, task, reason):
        """The negative: while the scope is still refused, no pass retries
        it and the release action refuses with NOTHING done — the scope
        kept, no receipt added, never pruned, the owner still listed."""
        before = self.scope_receipts(workflow_id)
        with self.seams() as calls, self.destruction() as gone:
            runtime_module.process_once(self.broker)
        self.assertEqual(calls, {"produce": 0, "spawn": 0})
        self.assertEqual((gone["revoke"], gone["close"], gone["relinquish"], gone["retired"],
                          gone["refused"]), (0, 0, 0, [], []))
        self.assertEqual(self.cleanup_candidates(), [])
        with self.seams() as calls, self.destruction() as gone:
            released = self.act(workflow_id, broker_module.ACTION_RELEASE)
        self.assertEqual(calls, {"produce": 0, "spawn": 0})
        self.assertEqual((released.ok, released.problem, released.detail),
                         (False, self.RETAINED, self.held(task, reason)))
        self.assertEqual((gone["revoke"], gone["close"], gone["relinquish"], gone["retired"],
                          gone["refused"]), (0, 0, 0, [], []))
        self.assertTrue(os.path.isdir(task))
        self.assertEqual(self.scope_receipts(workflow_id), before)
        self.assertTrue(wa_record.cleanup_evidence_outstanding(self.record(workflow_id)))
        self.never_pruned(workflow_id)
        self.assertIn(self.task_owner(workflow_id),
                      runtime_module.current_scope_owners(self.store_dir))

    def settled_by_the_retry(self, workflow_id, task, retired=None):
        """Absence established: the workflow is a candidate again, and the
        pass's RETIREMENT-ONLY re-entry retires EXACTLY ``retired`` (default:
        the refused scope) — no producer, spawn, revocation, close or
        relinquish — and settles the receipt; a further pass replays
        nothing; pruned exactly once."""
        retired = [task] if retired is None else retired
        self.assertEqual(self.cleanup_candidates(),
                         [(workflow_id, self.record(workflow_id)["handoff"]["revision"])])
        before = self.scope_receipts(workflow_id)
        with self.seams() as calls, self.destruction() as gone:
            runtime_module.process_once(self.broker)
        self.assertEqual(calls, {"produce": 0, "spawn": 0})
        self.assertEqual((gone["revoke"], gone["close"], gone["relinquish"], gone["retired"],
                          gone["refused"]), (0, 0, 0, retired, []))
        self.assertFalse(os.path.exists(task))
        self.assertEqual(self.scope_receipts(workflow_id), before + [
            "%s: absence established for every process scope; %d retired — %s" % (
                wa_record.PROCESS_SCOPE_SETTLED_RECEIPT_MARKER, len(retired),
                ", ".join(os.path.basename(path) for path in retired) or "none remained")])
        self.assertFalse(wa_record.process_scope_retention_outstanding(self.record(workflow_id)))
        self.assertFalse(wa_record.cleanup_evidence_outstanding(self.record(workflow_id)))
        settled = self.scope_receipts(workflow_id)
        with self.seams() as calls, self.destruction() as gone:
            runtime_module.process_once(self.broker)
        self.assertEqual(calls, {"produce": 0, "spawn": 0})
        self.assertEqual((gone["revoke"], gone["close"], gone["relinquish"], gone["retired"],
                          gone["refused"]), (0, 0, 0, [], []))
        self.assertEqual(self.cleanup_candidates(), [])
        self.assertEqual(self.scope_receipts(workflow_id), settled)
        self.pruned_once(workflow_id)

    def test_RS4b_late_refusal_then_recovery_then_a_settled_retirement_and_prune(self):
        """A role-turn group of the task scope starts AFTER the boundary
        re-check (inside the relinquish): its retirement is refused with the
        lease already released. While outstanding the record is never pruned
        and recovery still attributes the scope through it — and reaps the
        group; absence then established, the retirement-only retry retires
        exactly that scope, settles the receipt, and the record prunes once."""
        mission_id, workflow_id = self.verified_and_settled()
        task = self.task_scope(workflow_id)
        self.expire_retention(workflow_id)
        real_spawn = process_ownership.spawn_owned
        spawned = []
        self.refused_late(
            workflow_id, task,
            lambda scope: spawned.append(self.spawn_in(scope, ["sleep", "60"], spawn=real_spawn)),
            process_ownership.RETIRE_REFUSED_LIVE_GROUP)
        [process] = spawned
        self.still_outstanding(workflow_id, task, process_ownership.RETIRE_REFUSED_LIVE_GROUP)
        results, unattributed = runtime_module.recover_inherited_processes(self.store_dir)
        self.assertEqual([(tuple(identity), recovered) for identity, recovered, *_ in results
                          if identity.owner_id == workflow_id],
                         [(self.task_owner(workflow_id), [process.pid])])
        self.assertNotIn(task, [d for d, _ in unattributed])
        process.wait(timeout=10)
        self.assertEqual(self.group_members(process.pid), [])
        self.settled_by_the_retry(workflow_id, task)

    def test_RS4c_unreadable_after_the_lease_released_retains_until_it_reads(self):
        """The same late refusal on an UNREADABLE group record: the retry
        never reads a failed read as absence — no pass retries, the release
        action refuses with nothing done, never pruned — until the record
        reads again; then it settles exactly as above."""
        if os.geteuid() == 0:
            self.skipTest("permission bits do not bind root")
        mission_id, workflow_id = self.verified_and_settled()
        task = self.task_scope(workflow_id)
        self.expire_retention(workflow_id)
        self.addCleanup(self.clear_damage, task)
        self.refused_late(workflow_id, task, self.unreadable_group_record,
                          process_ownership.RETIRE_REFUSED_UNREADABLE)
        self.still_outstanding(workflow_id, task, process_ownership.RETIRE_REFUSED_UNREADABLE)
        self.clear_damage(task)
        self.settled_by_the_retry(workflow_id, task)

    # -- Addendum C: the deletion boundary, and observed removal -----------------------

    def refused_late_then_cleared(self, workflow_id, task):
        """A late refusal on a never-stamped root, which the fixture then
        removes: absence established, the retained receipt outstanding."""
        self.addCleanup(self.clear_damage, task)
        self.refused_late(
            workflow_id, task,
            lambda scope: process_ownership.create_owned_root("own-0000000000000000", scope),
            process_ownership.RETIRE_REFUSED_UNSTAMPED)
        self.clear_damage(task)

    def retained_receipt(self, path, reason):
        return "%s: 1 scope(s) kept after the workspace lease was released — %s: %s" % (
            wa_record.PROCESS_SCOPE_RETAINED_RECEIPT_MARKER, os.path.basename(path), reason)

    def test_RS4d_a_hold_landing_during_the_retry_reads_retires_and_settles_nothing(self):
        """C-1: a HOLD lands while the retirement-only re-entry reads its holds
        (after the action's entry admission). The fresh cleanup admission
        refuses: nothing retired, nothing settled, the record unchanged. The
        hold released, the next pass settles exactly."""
        mission_id, workflow_id = self.verified_and_settled()
        task = self.task_scope(workflow_id)
        self.expire_retention(workflow_id)
        self.refused_late_then_cleared(workflow_id, task)
        real_hold = broker_module.scope_release_hold

        def hold_lands(entry):
            result = real_hold(entry)
            self.service.request_hold(mission_id)
            return result
        before = self.record(workflow_id)
        with mock.patch.object(broker_module, "scope_release_hold", hold_lands), \
                self.seams() as calls, self.destruction() as gone:
            released = self.act(workflow_id, broker_module.ACTION_RELEASE)
        self.assertEqual(calls, {"produce": 0, "spawn": 0})
        self.assertEqual((released.ok, released.problem), (False, gate_module.PROBLEM_HOLD_ACTIVE))
        self.assertTrue(released.detail.startswith(
            "the cleanup admission refused before the scope retirement ("), released.detail)
        self.assertTrue(released.detail.endswith("); nothing is retired or settled"),
                        released.detail)
        self.assertEqual((gone["revoke"], gone["close"], gone["relinquish"], gone["retired"],
                          gone["refused"]), (0, 0, 0, [], []))
        self.assertTrue(os.path.isdir(task))
        self.assertEqual(self.record(workflow_id), before)
        self.service.release_hold(mission_id)
        self.settled_by_the_retry(workflow_id, task)

    def test_RS4e_a_scope_whose_removal_is_not_observed_never_settles(self):
        """C-2: the retry's removal of the task scope silently fails (its
        owned-root prefix cannot be written, so ``rmtree``'s ignored error
        leaves it). REFUSED, never reported retired: a fresh retained receipt
        saying its credential WAS removed (a truthful partial effect), no
        settlement, never pruned. Its credential gone, the surviving directory
        is UNATTRIBUTED and holds every pass; only once it is gone does the
        retry settle, with nothing left to retire."""
        if os.geteuid() == 0:
            self.skipTest("permission bits do not bind root")
        mission_id, workflow_id = self.verified_and_settled()
        task = self.task_scope(workflow_id)
        self.expire_retention(workflow_id)
        self.refused_late_then_cleared(workflow_id, task)
        prefix = process_ownership.owned_root_base(task)
        with open(os.path.join(prefix, "keep"), "w") as handle:
            handle.write("an entry the prefix cannot lose")
        os.chmod(prefix, 0o500)
        self.addCleanup(lambda: os.path.isdir(prefix) and os.chmod(prefix, 0o755))
        before = self.scope_receipts(workflow_id)
        with self.seams() as calls, self.destruction() as gone:
            runtime_module.process_once(self.broker)
        undeleted = ("%s (its assignment credential was removed)"
                     % process_ownership.RETIRE_REFUSED_UNDELETED)
        self.assertEqual(calls, {"produce": 0, "spawn": 0})
        self.assertEqual((gone["revoke"], gone["close"], gone["relinquish"], gone["retired"],
                          gone["refused"]), (0, 0, 0, [], [(task, undeleted)]))
        self.assertTrue(os.path.isdir(task))
        self.assertFalse(os.path.exists(
            process_ownership.assignment_path(os.path.basename(task))))
        self.assertEqual(self.scope_receipts(workflow_id),
                         before + [self.retained_receipt(task, undeleted)])
        self.assertTrue(wa_record.cleanup_evidence_outstanding(self.record(workflow_id)))
        self.never_pruned(workflow_id)
        self.still_outstanding(workflow_id, task, "%s (%s)" % (
            process_ownership.RETIRE_REFUSED_UNATTRIBUTED,
            process_ownership.UNATTRIBUTED_NO_ASSIGNMENT))
        # The fixture removes the residue (its own directory).
        os.chmod(prefix, 0o755)
        shutil.rmtree(task)
        self.settled_by_the_retry(workflow_id, task, retired=[])

    def test_RS4f_a_hold_landing_between_two_removals_stops_the_retirement(self):
        """C-1b: the admission is taken at the ACTUAL deletion boundary, per
        scope. In the FIRST-PASS release a hold lands after the first scope's
        removal: the second is refused unread and untouched, the first stays
        removed and reported (a truthful partial effect, never replayed),
        nothing settles, the record is kept. The hold released, the retry
        removes exactly the second and settles."""
        mission_id, workflow_id = self.verified_and_settled()
        task = self.task_scope(workflow_id)
        self.dead_stamped_root(task)
        self.expire_retention(workflow_id)
        first, second = self.both(workflow_id, task)
        # Task 8 R25-1: a scope's removal HOLDS its admission across the effect.
        real_admission = broker_module._cleanup_admission_held
        removals = []

        def admission(broker, entry, where, effect):
            if where == "before a scope's removal":
                removals.append(where)
                if len(removals) == 2:
                    self.service.request_hold(mission_id)    # after the first removal
            return real_admission(broker, entry, where, effect)
        with mock.patch.object(broker_module, "_cleanup_admission_held", admission), \
                self.seams() as calls, self.destruction() as gone:
            runtime_module.process_once(self.broker)
        self.assertEqual(calls, {"produce": 0, "spawn": 0})
        self.assertEqual(len(removals), 2)
        self.assertEqual((gone["relinquish"], gone["retired"]), (1, [first]))
        [(path, reason)] = gone["refused"]
        self.assertEqual(path, second)
        self.assertTrue(reason.startswith("the cleanup admission refused before a scope's"
                                          " removal (%s: " % gate_module.PROBLEM_HOLD_ACTIVE),
                        reason)
        self.assertFalse(os.path.exists(first))
        self.assertTrue(os.path.isdir(second))
        self.assertIsNotNone(self.record(workflow_id)["workspace_lease"]["released_at"])
        self.assertEqual(self.scope_receipts(workflow_id), [self.retained_receipt(second, reason)])
        self.never_pruned(workflow_id)
        self.service.release_hold(mission_id)
        self.settled_by_the_retry(workflow_id, second)

    def kept_by_a_pass(self, workflow_id, refused, written, credential):
        """One Runtime pass through the retry: exactly ``refused`` refused,
        nothing retired, revoked, closed or relinquished; a retained receipt
        only when ``written`` (never one repeating the latest); no
        settlement; the credential in place."""
        before = self.scope_receipts(workflow_id)
        with self.seams() as calls, self.destruction() as gone:
            runtime_module.process_once(self.broker)
        self.assertEqual(calls, {"produce": 0, "spawn": 0})
        self.assertEqual((gone["revoke"], gone["close"], gone["relinquish"], gone["retired"],
                          gone["refused"]), (0, 0, 0, [], [refused]))
        self.assertEqual(self.scope_receipts(workflow_id),
                         before + ([self.retained_receipt(*refused)] if written else []))
        self.assertTrue(wa_record.cleanup_evidence_outstanding(self.record(workflow_id)))
        self.assertTrue(os.path.isfile(credential))

    def test_RS4g_a_credential_that_cannot_be_removed_never_settles_and_never_grows(self):
        """C-2: the retry removes the task scope's directory but NOT its
        credential (the credential store cannot be written): refused, a
        truthful partial effect. Every later pass re-attempts the credential
        — refused again, one receipt for the new state, none for a repeat —
        and never settles while it survives. C-2b: the credential then
        UNREADABLE, then FORGED — each ambiguous, never absent: refused, left
        in place, no settlement, never pruned. Once it reads and can be
        removed, the retry retires exactly it and settles."""
        if os.geteuid() == 0:
            self.skipTest("permission bits do not bind root")
        mission_id, workflow_id = self.verified_and_settled()
        task = self.task_scope(workflow_id)
        self.expire_retention(workflow_id)
        self.refused_late_then_cleared(workflow_id, task)
        store = process_ownership.assignment_base()
        credential = process_ownership.assignment_path(os.path.basename(task))
        os.chmod(store, 0o500)
        self.addCleanup(lambda: os.path.isdir(store) and os.chmod(store, 0o700))
        kept = process_ownership.RETIRE_REFUSED_CREDENTIAL_KEPT
        for refused, written in (
                ((task, "%s (PermissionError; its directory was removed)" % kept), True),
                ((credential, "%s (PermissionError; its directory is already gone)" % kept), True),
                ((credential, "%s (PermissionError; its directory is already gone)" % kept), False)):
            self.kept_by_a_pass(workflow_id, refused, written, credential)
        self.assertFalse(os.path.exists(task))
        self.never_pruned(workflow_id)
        os.chmod(store, 0o700)
        unread = process_ownership.RETIRE_REFUSED_CREDENTIAL_UNREAD
        # UNREADABLE (an OSError on its read): ambiguous, never absent — and
        # (Task 8 R22-2) reported as UNAVAILABLE, never "malformed": the value
        # moved, the refusal did not.
        with open(credential, "rb") as handle:
            original = handle.read()
        os.chmod(credential, 0)
        self.addCleanup(lambda: os.path.isfile(credential) and os.chmod(credential, 0o600))
        self.kept_by_a_pass(workflow_id, (credential, "%s (%s (the assignment record:"
                                          " PermissionError))" % (
            unread, process_ownership.UNATTRIBUTED_CREDENTIAL_UNAVAILABLE)), True, credential)
        self.never_pruned(workflow_id)
        os.chmod(credential, 0o600)
        # FORGED (its integrity binding does not verify): ambiguous, never absent.
        forged = json.loads(original)
        forged["binding"] = "0" * len(forged["binding"])
        with open(credential, "w") as handle:
            json.dump(forged, handle)
        self.kept_by_a_pass(workflow_id, (credential, "%s (%s)" % (
            unread, process_ownership.UNATTRIBUTED_FORGED)), True, credential)
        self.never_pruned(workflow_id)
        with open(credential, "wb") as handle:
            handle.write(original)
        self.settled_by_the_retry(workflow_id, task, retired=[credential])

    def test_RS4h_a_credential_gone_bad_after_the_holds_is_refused_never_omitted(self):
        """C-2c: the task scope's credential becomes UNREADABLE after the holds
        passed, inside the relinquish. The retirement's strict enumeration
        still sees the scope by its NAME — refused with the reason, never
        retired and never omitted from both lists: the directory and its
        credential kept, no settlement, never pruned, every pass holding.
        Once the credential reads again, the retry retires exactly the scope
        and settles."""
        if os.geteuid() == 0:
            self.skipTest("permission bits do not bind root")
        mission_id, workflow_id = self.verified_and_settled()
        task = self.task_scope(workflow_id)
        self.expire_retention(workflow_id)
        credential = process_ownership.assignment_path(os.path.basename(task))
        self.addCleanup(lambda: os.path.isfile(credential) and os.chmod(credential, 0o600))
        # Task 8 R22-2: an UNREADABLE credential is UNAVAILABLE, never
        # "malformed" — the value moved; the refusal and the retention did not.
        unread = "%s (%s (the assignment record: PermissionError))" % (
            process_ownership.RETIRE_REFUSED_UNATTRIBUTED,
            process_ownership.UNATTRIBUTED_CREDENTIAL_UNAVAILABLE)
        self.refused_late(workflow_id, task, lambda scope: os.chmod(credential, 0), unread)
        self.assertTrue(os.path.isfile(credential))
        self.still_outstanding(workflow_id, task, unread)
        os.chmod(credential, 0o600)
        self.settled_by_the_retry(workflow_id, task)


class R21RemovalRetryTests(DeliveryCase):
    """Task 8 R21-1 / R21-3 / R21-A — the release's DESTRUCTIVE BOUNDARY and
    its removal-only retry, through the PRODUCTION routes: the Runtime pass
    (``process_once``: cleanup candidates, then ``ACTION_RELEASE``), the
    Broker's release action and the bootstrap's capacity-pressure pruning,
    over the REAL lease directory and ``workspace.release``, the real Mission
    gate, and the engine double's live listing and child records.

    R21-1: a hold or a Mission source outage landing DURING the boundary
    reads refuses at the FRESH admission with ZERO relinquishment — the trust
    revocation, the preservation and the session close already made are
    recorded, never replayed — and the admitted release still releases.
    R21-3: a removal that FAILS, cannot be OBSERVED, or is PARTIAL keeps the
    lease and a retryable ``workspace removal pending`` receipt (never
    pruned), and the retry completes it without replay. R21-A: the retry
    re-proves the sessions' ABSENCE NOW — a workspace listed live again, a
    contradictory same-lease child record or an unavailable listing removes
    nothing; restored absence removes exactly once.

    Counts are taken on the real seams, each delegating to the production
    function: producer and spawn, preservation, trust revocation, the
    engine's session close, the workspace relinquish, the scope retirement
    and pruning."""

    _R19 = R19VerificationLifecycleTests
    _R20 = R20VerificationRetentionTests
    ATTEMPT = _R19.ATTEMPT
    seams = _R19.seams
    collect = staticmethod(_R19.collect)
    kill_group = staticmethod(_R19.kill_group)
    attempts = _R19.attempts
    scope = _R19.scope
    control_of = _R19.control_of
    group_members = _R19.group_members
    members_after = _R19.members_after
    destruction = _R20.destruction
    expire_retention = _R20.expire_retention
    cleanup_candidates = _R20.cleanup_candidates
    ready = _R20.ready
    verified_and_settled = _R20.verified_and_settled
    insert_under_pressure = _R20.insert_under_pressure
    never_pruned = _R20.never_pruned
    pruned_once = _R20.pruned_once

    PENDING = wa_record.WORKSPACE_REMOVAL_PENDING_RECEIPT_MARKER
    COMPLETED = wa_record.WORKSPACE_REMOVAL_COMPLETED_RECEIPT_MARKER
    BOUNDARY = ("%s: the release reached its destructive boundary with the sessions proven"
                " closed; the removal was not made or not observed — "
                % wa_record.WORKSPACE_REMOVAL_PENDING_RECEIPT_MARKER)
    ADMISSION = "the cleanup admission refused before the workspace relinquish ("
    NOTHING = dict(produce=0, spawn=0, preserve=0, revoke=0, close=0, relinquish=0, retired=[])

    # -- helpers ---------------------------------------------------------------------

    @contextlib.contextmanager
    def counted(self):
        """``destruction()``'s counts plus the producer and the spawn
        (``seams()``) and each evidence preservation."""
        from target_runtime import evidence_preservation
        real_preserve = evidence_preservation.preserve
        preserved = []

        def preserve(*args, **kwargs):
            preserved.append(args[1])
            return real_preserve(*args, **kwargs)
        with self.seams() as calls, self.destruction() as gone, \
                mock.patch.object(evidence_preservation, "preserve", preserve):
            yield gone
        gone.update(calls)
        gone["preserve"] = len(preserved)

    @staticmethod
    def tally(gone):
        return dict((key, gone[key]) for key in (
            "produce", "spawn", "preserve", "revoke", "close", "relinquish", "retired"))

    def lease_of(self, workflow_id):
        return self.record(workflow_id)["workspace_lease"]["path_realpath"]

    def removal_receipts(self, workflow_id):
        return [r["bounded_summary"] for r in self.record(workflow_id)["receipts"]
                if r["bounded_summary"].startswith((self.PENDING + ":", self.COMPLETED + ":"))]

    def kept(self, workflow_id, directory=True):
        """The lease unreleased (and its directory present), the
        verification scope kept, the removal outstanding."""
        record = self.record(workflow_id)
        self.assertIsNone(record["workspace_lease"]["released_at"])
        self.assertEqual(os.path.isdir(self.lease_of(workflow_id)), directory)
        self.assertTrue(os.path.isdir(self.scope(workflow_id)))
        self.assertTrue(wa_record.workspace_removal_outstanding(record))

    def first_pass(self, workflow_id, problem, detail, relinquish, patches=(), after=None):
        """The release action reaches its destructive boundary: the trust
        revoked, the evidence preserved and the session closed ONCE (the
        effects recorded), then RETAINED there with ``problem`` and
        ``detail`` (or ``detail()``, read after the action) — ``relinquish``
        0 (refused before) or 1 (made, not observed) — no scope retired, the
        pending receipt naming why, never pruned. ``patches`` are entered
        around the action; ``after()`` runs as soon as it returns."""
        with contextlib.ExitStack() as stack:
            for patch in patches:
                stack.enter_context(patch)
            gone = stack.enter_context(self.counted())
            released = self.act(workflow_id, broker_module.ACTION_RELEASE)
        if after is not None:
            after()
        detail = detail() if callable(detail) else detail
        self.assertEqual((released.ok, released.problem), (False, problem))
        self.assertTrue(released.detail.startswith(
            detail + " (at the destructive boundary; cleanup DEGRADED: "), released.detail)
        self.assertEqual(self.tally(gone), dict(
            self.NOTHING, preserve=1, revoke=1, close=1, relinquish=relinquish))
        self.assertEqual(self.removal_receipts(workflow_id), [self.BOUNDARY + detail])
        self.never_pruned(workflow_id)

    def boundary_hold(self, at_boundary):
        """``(patch, reads)``: ``scope_release_hold`` with ``at_boundary()`` run
        right after its SECOND read in the action — the destructive
        boundary's, after the top-of-release read, the preservation and the
        close."""
        real_hold = broker_module.scope_release_hold
        reads = []

        def hold(entry):
            result = real_hold(entry)
            reads.append(result)
            if len(reads) == 2:
                at_boundary()
            return result
        return mock.patch.object(broker_module, "scope_release_hold", hold), reads

    def after_the_session_proof(self, at_boundary):
        """``(patch, reads)``: the boundary's session-absence proof
        (``_sessions_absent_now``, R21-C) with ``at_boundary()`` run right
        after its FIRST read in the action — the LAST read before the
        admission and the relinquish."""
        real_proof = broker_module.TargetBroker._sessions_absent_now
        reads = []

        def proof(broker, entry):
            result = real_proof(broker, entry)
            reads.append(result)
            if len(reads) == 1:
                at_boundary()
            return result
        return mock.patch.object(broker_module.TargetBroker, "_sessions_absent_now",
                                 proof), reads

    def admission_refusals(self):
        """``(patch, refusals)``: every cleanup admission the gate REFUSES,
        as the gate answered it (its exact detail, never restated)."""
        real_admit = self.gate.admit_cleanup
        refusals = []

        def admit(*args, **kwargs):
            admission = real_admit(*args, **kwargs)
            if not admission.ok:
                refusals.append(admission)
            return admission
        return mock.patch.object(self.gate, "admit_cleanup", admit), refusals

    def refused_at_the_boundary(self, workflow_id, at_boundary, problem, after=None,
                                last_read=False):
        """R21-1: ``at_boundary()`` lands during the boundary reads — during
        the hold reads, or (``last_read``) after the session proof, the last
        of them; the FRESH admission immediately before the relinquish
        refuses with ``problem`` — the ONE refusal of the action — and
        nothing is relinquished."""
        spy, refusals = self.admission_refusals()
        hook, reads = (self.after_the_session_proof if last_read
                       else self.boundary_hold)(at_boundary)
        self.first_pass(
            workflow_id, problem,
            lambda: self.ADMISSION + "%s); nothing is released" % refusals[-1].detail,
            relinquish=0, patches=[spy, hook], after=after)
        self.assertEqual(reads, [None] if last_read else [None, None])
        self.assertEqual([admission.problem for admission in refusals], [problem])
        self.kept(workflow_id)

    def held_at_the_boundary(self, mission_id, workflow_id):
        """R21-1's hold landing during the boundary reads: ZERO
        relinquishment. While held, no pass removes anything."""
        self.refused_at_the_boundary(
            workflow_id, lambda: self.service.request_hold(mission_id),
            gate_module.PROBLEM_HOLD_ACTIVE)
        with self.counted() as gone:
            runtime_module.process_once(self.broker)
        self.assertEqual(self.tally(gone), self.NOTHING)
        self.kept(workflow_id)

    def retained_between_passes(self, workflow_id, detail):
        """R21-A: the removal-only retry finds the sessions' absence NOT
        established now — nothing removed, closed, revoked or preserved, the
        state recorded once (a second identical retry writes NOTHING), and
        no Runtime pass removes anything either."""
        before = self.removal_receipts(workflow_id)
        problem = broker_module.PROBLEM_WORKSPACE_SESSIONS_RETAINED
        with self.counted() as gone:
            released = self.act(workflow_id, broker_module.ACTION_RELEASE)
        self.assertEqual((released.ok, released.problem, released.detail),
                         (False, problem, detail))
        self.assertEqual(self.tally(gone), self.NOTHING)
        self.kept(workflow_id)
        self.assertEqual(self.removal_receipts(workflow_id), before + [
            "%s: the retried removal was not made: %s" % (self.PENDING, detail)])
        record = self.record(workflow_id)
        with self.counted() as gone:
            released = self.act(workflow_id, broker_module.ACTION_RELEASE)
        self.assertEqual((released.ok, released.problem, released.detail),
                         (False, problem, detail))
        self.assertEqual(self.tally(gone), self.NOTHING)
        self.assertEqual(self.record(workflow_id), record)
        with self.counted() as gone:
            runtime_module.process_once(self.broker)
        self.assertEqual(self.tally(gone), self.NOTHING)
        self.kept(workflow_id)
        self.never_pruned(workflow_id)

    def still_incomplete(self, workflow_id, detail):
        """R21-3: while the removal still cannot complete, the retry attempts
        the REMOVAL alone (one relinquish, nothing else) and records the new
        state once; an identical retry writes NOTHING."""
        before = self.removal_receipts(workflow_id)
        problem = workspace_module.PROBLEM_RELEASE_INCOMPLETE
        with self.counted() as gone:
            released = self.act(workflow_id, broker_module.ACTION_RELEASE)
        self.assertEqual((released.ok, released.problem, released.detail),
                         (False, problem, detail))
        self.assertEqual(self.tally(gone), dict(self.NOTHING, relinquish=1))
        self.assertEqual(self.removal_receipts(workflow_id), before + [
            "%s: the retried removal was not observed complete — %s" % (self.PENDING, detail)])
        record = self.record(workflow_id)
        with self.counted() as gone:
            released = self.act(workflow_id, broker_module.ACTION_RELEASE)
        self.assertEqual((released.ok, released.problem, released.detail),
                         (False, problem, detail))
        self.assertEqual(self.tally(gone), dict(self.NOTHING, relinquish=1))
        self.assertEqual(self.record(workflow_id), record)
        self.never_pruned(workflow_id)

    def removed_by_the_retry(self, workflow_id):
        """Absence restored: still a cleanup candidate, and the pass's
        REMOVAL-ONLY re-entry removes EXACTLY once — no producer, spawn,
        preservation, revocation or close — retires the verification scope
        and settles the receipt; a further pass repeats nothing; pruned
        exactly once."""
        lease = self.lease_of(workflow_id)
        self.assertEqual(self.cleanup_candidates(),
                         [(workflow_id, self.record(workflow_id)["handoff"]["revision"])])
        before = self.removal_receipts(workflow_id)
        with self.counted() as gone:
            runtime_module.process_once(self.broker)
        self.assertEqual(self.tally(gone), dict(self.NOTHING, relinquish=1,
                                                retired=[self.scope(workflow_id)]))
        self.assertEqual(gone["refused"], [])
        record = self.record(workflow_id)
        self.assertIsNotNone(record["workspace_lease"]["released_at"])
        self.assertFalse(os.path.exists(lease))
        self.assertEqual(self.removal_receipts(workflow_id), before + [
            "%s: the workspace directory is observed removed and its lease released"
            % self.COMPLETED])
        self.assertFalse(wa_record.workspace_removal_outstanding(record))
        with self.counted() as gone:
            runtime_module.process_once(self.broker)
        self.assertEqual(self.tally(gone), self.NOTHING)
        self.assertEqual(self.cleanup_candidates(), [])
        self.pruned_once(workflow_id)

    def damaged_relinquish(self, damage):
        """The REAL relinquish, with ``damage()`` run immediately before it."""
        worker = self.broker.worker
        real = worker.relinquish_workspace

        def relinquish(*args, **kwargs):
            damage()
            return real(*args, **kwargs)
        return mock.patch.object(worker, "relinquish_workspace", relinquish)

    def settled_workflow(self):
        mission_id, workflow_id = self.verified_and_settled()
        self.expire_retention(workflow_id)
        return mission_id, workflow_id

    # -- R21-1: a fresh admission immediately before the relinquish -----------------

    def test_R21_1a_a_hold_during_the_boundary_reads_relinquishes_nothing(self):
        mission_id, workflow_id = self.settled_workflow()
        self.held_at_the_boundary(mission_id, workflow_id)
        self.service.release_hold(mission_id)
        self.removed_by_the_retry(workflow_id)

    def test_R21_1b_a_source_outage_during_the_boundary_reads_relinquishes_nothing(self):
        """The outage lands after the LAST boundary read (R21-C's session
        proof, which itself reads the canonical starts and so would refuse an
        outage landing before it): the fresh admission is what refuses."""
        mission_id, workflow_id = self.settled_workflow()
        good = []

        def outage():
            good.append(self.mission_bytes())
            with open(self.mstore.path, "w", encoding="utf-8") as handle:
                handle.write("{oops")

        def restored():
            with open(self.mstore.path, "wb") as handle:
                handle.write(good[0])
        self.refused_at_the_boundary(workflow_id, outage,
                                     gate_module.PROBLEM_SOURCE_UNAVAILABLE, after=restored,
                                     last_read=True)
        self.assertEqual(len(good), 1)
        self.removed_by_the_retry(workflow_id)

    def test_R21_1c_the_admitted_release_relinquishes_exactly_once_after_a_fresh_admission(self):
        """The positive: admitted, the release relinquishes ONCE — and the
        admission asked immediately before that relinquish is a fresh one,
        with NO read of the lease directory between it and the removal
        (R21-C, C-2: no diagnostic listing on the destructive path) — retires
        the verification scope, writes no removal receipt (nothing is
        pending) and the record prunes exactly once."""
        import shutil as shutil_module
        mission_id, workflow_id = self.settled_workflow()
        lease = self.lease_of(workflow_id)
        events = []
        real_admit = self.gate.admit_cleanup
        worker = self.broker.worker
        real_relinquish = worker.relinquish_workspace
        real_listdir, real_rmtree = os.listdir, shutil_module.rmtree

        def admit(*args, **kwargs):
            events.append("admit")
            return real_admit(*args, **kwargs)

        def relinquish(*args, **kwargs):
            events.append("relinquish")
            return real_relinquish(*args, **kwargs)

        def listdir(path=".", *args, **kwargs):
            if path == lease:
                events.append("listdir")
            return real_listdir(path, *args, **kwargs)

        def rmtree(path, *args, **kwargs):
            if path == lease:
                events.append("rmtree")
            return real_rmtree(path, *args, **kwargs)
        with mock.patch.object(self.gate, "admit_cleanup", admit), \
                mock.patch.object(worker, "relinquish_workspace", relinquish), \
                mock.patch.object(workspace_module.os, "listdir", listdir), \
                mock.patch.object(workspace_module.shutil, "rmtree", rmtree), \
                self.counted() as gone:
            runtime_module.process_once(self.broker)
        self.assertEqual(self.tally(gone), dict(self.NOTHING, preserve=1, revoke=1, close=1,
                                                relinquish=1, retired=[self.scope(workflow_id)]))
        self.assertEqual(events.count("relinquish"), 1)
        at = events.index("relinquish")
        self.assertEqual(events[at - 1:at + 2], ["admit", "relinquish", "rmtree"], events)
        self.assertIsNotNone(self.record(workflow_id)["workspace_lease"]["released_at"])
        self.assertEqual(self.removal_receipts(workflow_id), [])
        self.pruned_once(workflow_id)

    def test_R21_1d_a_hold_during_the_removal_retry_reads_removes_and_writes_nothing(self):
        """The REMOVAL-ONLY retry's own fresh admission: a hold landing while
        the retry reads its holds (after the action's entry admission)
        refuses immediately before the relinquish — nothing removed, closed,
        revoked or preserved, the record unchanged. Released, the next pass
        removes exactly once."""
        mission_id, workflow_id = self.settled_workflow()
        self.held_at_the_boundary(mission_id, workflow_id)
        self.service.release_hold(mission_id)
        real_hold = broker_module.scope_release_hold

        def hold_lands(entry):
            result = real_hold(entry)
            self.service.request_hold(mission_id)
            return result
        before = self.record(workflow_id)
        spy, refusals = self.admission_refusals()
        with mock.patch.object(broker_module, "scope_release_hold", hold_lands), spy, \
                self.counted() as gone:
            released = self.act(workflow_id, broker_module.ACTION_RELEASE)
        self.assertEqual([admission.problem for admission in refusals],
                         [gate_module.PROBLEM_HOLD_ACTIVE])
        self.assertEqual((released.ok, released.problem, released.detail), (
            False, gate_module.PROBLEM_HOLD_ACTIVE,
            self.ADMISSION + "%s); nothing is released" % refusals[0].detail))
        self.assertEqual(self.tally(gone), self.NOTHING)
        self.assertEqual(self.record(workflow_id), before)
        self.service.release_hold(mission_id)
        self.removed_by_the_retry(workflow_id)

    # -- R21-3: the lease is released only on OBSERVED absence ----------------------

    def test_R21_3a_a_failing_removal_keeps_the_lease_and_the_retry_completes_it(self):
        if os.geteuid() == 0:
            self.skipTest("permission bits do not bind root")
        mission_id, workflow_id = self.settled_workflow()
        lease = self.lease_of(workflow_id)
        mode = os.stat(lease).st_mode & 0o7777
        self.addCleanup(lambda: os.path.isdir(lease) and os.chmod(lease, mode))
        detail = ("%s survived its removal (its remaining entries could not be counted); the"
                  " lease is kept and the removal is retried" % lease)
        self.first_pass(workflow_id, workspace_module.PROBLEM_RELEASE_INCOMPLETE, detail,
                        relinquish=1,
                        patches=[self.damaged_relinquish(lambda: os.chmod(lease, 0))])
        self.kept(workflow_id)
        self.still_incomplete(workflow_id, detail)
        os.chmod(lease, mode)
        self.assertTrue(os.path.isfile(os.path.join(lease, "fix.txt")))  # nothing was removed
        self.removed_by_the_retry(workflow_id)

    def test_R21_3b_a_partial_removal_keeps_the_lease_and_the_retry_completes_it(self):
        if os.geteuid() == 0:
            self.skipTest("permission bits do not bind root")
        mission_id, workflow_id = self.settled_workflow()
        lease = self.lease_of(workflow_id)
        kept_dir = os.path.join(lease, "r21-kept")
        self.assertGreater(len(os.listdir(lease)), 1)
        self.addCleanup(lambda: os.path.isdir(kept_dir) and os.chmod(kept_dir, 0o700))

        def partial():
            os.mkdir(kept_dir)
            with open(os.path.join(kept_dir, "held.txt"), "w") as handle:
                handle.write("held\n")
            os.chmod(kept_dir, 0o500)              # its file cannot be unlinked
        # R21-C (C-2): only what SURVIVED is reported — no entry count is
        # taken before the removal.
        detail = ("%s survived its removal (entries remaining: 1); the lease is kept and the"
                  " removal is retried" % lease)
        self.first_pass(workflow_id, workspace_module.PROBLEM_RELEASE_INCOMPLETE, detail,
                        relinquish=1, patches=[self.damaged_relinquish(partial)])
        self.kept(workflow_id)
        self.assertEqual(os.listdir(lease), ["r21-kept"])       # partial: the rest went
        self.still_incomplete(workflow_id, detail)
        os.chmod(kept_dir, 0o700)
        self.removed_by_the_retry(workflow_id)

    def test_R21_3c_an_unobservable_removal_keeps_the_lease_and_the_retry_completes_it(self):
        """The removal happened, and its absence could not be OBSERVED (the
        ``lstat`` after it is refused): the lease is kept all the same, and
        the retry — finding the directory absent — completes the release."""
        import shutil as shutil_module
        mission_id, workflow_id = self.settled_workflow()
        lease = self.lease_of(workflow_id)
        real_rmtree, real_lstat = shutil_module.rmtree, os.lstat
        armed = []

        def rmtree(path, *args, **kwargs):
            result = real_rmtree(path, *args, **kwargs)
            if path == lease:
                armed.append(path)
            return result

        def lstat(path, *args, **kwargs):
            if armed and path == lease:
                armed.pop()
                raise PermissionError("the removal's observation is refused")
            return real_lstat(path, *args, **kwargs)
        detail = ("the removal of %s cannot be observed (PermissionError); the lease is kept"
                  " and the removal is retried" % lease)
        self.first_pass(workflow_id, workspace_module.PROBLEM_RELEASE_INCOMPLETE, detail,
                        relinquish=1,
                        patches=[mock.patch.object(workspace_module.shutil, "rmtree", rmtree),
                                 mock.patch.object(workspace_module.os, "lstat", lstat)])
        self.assertEqual(armed, [])
        self.kept(workflow_id, directory=False)                  # removed, not observed
        self.removed_by_the_retry(workflow_id)

    # -- R21-C (C-1): the first pass re-proves the sessions' absence AT its boundary --

    def changed_during_the_hold_reads(self, workflow_id, change, detail):
        """The first pass closed the sessions and observed them absent; DURING
        its boundary hold reads ``change()`` lands. The boundary's own session
        proof reads it: ZERO relinquishment, the effects already made
        (revocation, preservation, the one close) recorded and never replayed,
        the lease and the pending receipt kept, never pruned."""
        hold, reads = self.boundary_hold(change)
        self.first_pass(workflow_id, broker_module.PROBLEM_WORKSPACE_SESSIONS_RETAINED, detail,
                        relinquish=0, patches=[hold])
        self.assertEqual(reads, [None, None])
        self.kept(workflow_id)

    def test_R21C_a_a_workspace_live_again_during_the_hold_reads_removes_nothing(self):
        from test_mission_engagement import AGENT_NAMES, WORKSPACE_ID
        mission_id, workflow_id = self.settled_workflow()
        self.changed_during_the_hold_reads(
            workflow_id,
            lambda: self.engine.live.append({"workspace_id": WORKSPACE_ID,
                                             "agent_names": list(AGENT_NAMES)}),
            "workspace(s) %s of this workflow are listed live again after its sessions were"
            " proven closed; nothing is closed or released" % WORKSPACE_ID)
        self.engine.live = []
        self.removed_by_the_retry(workflow_id)

    def test_R21C_b_a_contradictory_same_lease_child_during_the_hold_reads_removes_nothing(self):
        mission_id, workflow_id = self.settled_workflow()
        records = list(self.spawn_record_overrides["records"])
        intruder = dict(records[0], task_id="task-intruder-1", workspace_id="ws-intruder-1")
        self.changed_during_the_hold_reads(
            workflow_id,
            lambda: self.spawn_record_overrides.__setitem__("records", records + [intruder]),
            "the sessions' absence is not established now (%s: child evidence names this"
            " workflow's lease with 1 record(s) no canonical start of it establishes exactly"
            " (task, workspace: 'task-intruder-1' 'ws-intruder-1'); it is never overridden);"
            " nothing is released" % broker_module.PROBLEM_RELEASE_BINDING_UNPROVEN)
        self.spawn_record_overrides["records"] = records
        self.removed_by_the_retry(workflow_id)

    # -- R21-A: the retry re-proves the sessions' absence NOW ----------------------

    def test_R21A_a_a_workspace_listed_live_again_removes_nothing(self):
        from test_mission_engagement import AGENT_NAMES, WORKSPACE_ID
        mission_id, workflow_id = self.settled_workflow()
        self.held_at_the_boundary(mission_id, workflow_id)
        self.assertEqual(self.engine.live, [])                   # closed by the first pass
        self.engine.live = [{"workspace_id": WORKSPACE_ID, "agent_names": list(AGENT_NAMES)}]
        self.service.release_hold(mission_id)
        self.retained_between_passes(workflow_id, (
            "workspace(s) %s of this workflow are listed live again after its sessions were"
            " proven closed; nothing is closed or released" % WORKSPACE_ID))
        self.engine.live = []
        self.removed_by_the_retry(workflow_id)

    def test_R21A_b_a_contradictory_same_lease_child_removes_nothing(self):
        mission_id, workflow_id = self.settled_workflow()
        self.held_at_the_boundary(mission_id, workflow_id)
        records = list(self.spawn_record_overrides["records"])
        intruder = dict(records[0], task_id="task-intruder-1", workspace_id="ws-intruder-1")
        self.spawn_record_overrides["records"] = records + [intruder]
        self.service.release_hold(mission_id)
        self.retained_between_passes(workflow_id, (
            "the sessions' absence is not established now (%s: child evidence names this"
            " workflow's lease with 1 record(s) no canonical start of it establishes exactly"
            " (task, workspace: 'task-intruder-1' 'ws-intruder-1'); it is never overridden);"
            " nothing is released" % broker_module.PROBLEM_RELEASE_BINDING_UNPROVEN))
        self.spawn_record_overrides["records"] = records
        self.removed_by_the_retry(workflow_id)

    def test_R21A_c_an_unavailable_live_listing_removes_nothing(self):
        mission_id, workflow_id = self.settled_workflow()
        self.held_at_the_boundary(mission_id, workflow_id)
        self.engine.live_error = RuntimeError("listing down")
        self.service.release_hold(mission_id)
        self.retained_between_passes(workflow_id, (
            "the sessions' absence is not established now (%s: the workspace evidence is"
            " unreadable (RuntimeError)); nothing is released"
            % broker_module.PROBLEM_RELEASE_BINDING_UNPROVEN))
        self.engine.live_error = None
        self.removed_by_the_retry(workflow_id)


class R22RecoveryRouteTests(DeliveryCase):
    """Task 8 R22-1 / R22-2 through the PRODUCTION entry the CLI runs —
    ``runtime.recover_inherited_processes(store)`` (``current_scope_owners`` →
    ``recover_attributed``), ``cli.report_inherited_recovery``, and the
    verification barrier — over a REAL workflow: its task scope ASSIGNED by
    the production role-turn assignment, a live group spawned into it through
    the owned path (R20-B's fixture). For each source that cannot be read:
    the group never signalled and no reap called, nothing deleted, no
    ownership inferred, the CLI saying UNAVAILABLE; restored, recovery reaps
    exactly once."""

    _R19 = R19VerificationLifecycleTests
    _R20 = R20VerificationRetentionTests
    _R20B = R20BTaskScopeRetentionTests
    ATTEMPT = _R19.ATTEMPT
    seams = _R19.seams
    collect = staticmethod(_R19.collect)
    kill_group = staticmethod(_R19.kill_group)
    attempts = _R19.attempts
    scope = _R19.scope
    control_of = _R19.control_of
    group_members = _R19.group_members
    members_after = _R19.members_after
    the_group = _R19.the_group
    root_name = _R19.root_name
    owner_died_mid_run = _R19.owner_died_mid_run
    held_on_ownership = _R19.held_on_ownership
    ready = _R20.ready
    verified_and_settled = _R20.verified_and_settled
    LABEL = _R20B.LABEL
    task_scope = _R20B.task_scope
    task_owner = _R20B.task_owner
    spawn_in = _R20B.spawn_in

    # -- helpers -------------------------------------------------------------------

    @contextlib.contextmanager
    def no_reap(self):
        """Every recorded-root reap COUNTED; it must stay zero."""
        reaps = []
        real = process_ownership.reap_group_by_recorded_root

        def reap(*args, **kwargs):
            reaps.append(args)
            return real(*args, **kwargs)
        with mock.patch.object(process_ownership, "reap_group_by_recorded_root", reap):
            yield
        self.assertEqual(reaps, [], "an unavailable source was acted on")

    @staticmethod
    def leader_query_fails():
        """The leader's start-time query FAILS (its `ps` cannot be started)
        while the leader lives; every other subprocess runs."""
        from target_runtime import spawn_stamp
        real_run = spawn_stamp.subprocess.run

        def run(argv, *args, **kwargs):
            if isinstance(argv, (list, tuple)) and argv and argv[0] == "ps":
                raise OSError("the leader query is refused")
            return real_run(argv, *args, **kwargs)
        return mock.patch.object(spawn_stamp.subprocess, "run", run)

    def report_lines(self, report):
        import io
        from target_runtime import cli as cli_module
        stream = io.StringIO()
        cli_module.report_inherited_recovery(report, stream=stream)
        return stream.getvalue().splitlines()

    @staticmethod
    def unavailable_count(count):
        return ("dirun: recovery observations UNAVAILABLE: %d (reported, never read as"
                " absent; nothing they cover was acted on)" % count)

    def live_task_group(self):
        mission_id, workflow_id = self.verified_and_settled()
        task = self.task_scope(workflow_id)
        process = self.spawn_in(task, ["sleep", "60"])
        self.assertEqual(len(self.members_after(process.pid, 1)), 1)
        [(root, pgid)] = process_ownership.owned_roots(task)
        self.assertEqual(pgid, process.pid)
        return workflow_id, task, root, process

    def reaped_once(self, workflow_id, process):
        """Restored: recovery acts on the REAL records — the task group
        reaped exactly once, every observation made."""
        report = runtime_module.recover_inherited_processes(self.store_dir)
        self.assertEqual([(tuple(identity), recovered)
                          for identity, recovered, *_ in report[0]
                          if identity.owner_id == workflow_id],
                         [(self.task_owner(workflow_id), [process.pid])])
        self.assertEqual(report.unavailable, [])
        self.assertIn(self.unavailable_count(0), self.report_lines(report))
        process.wait(timeout=10)
        self.assertEqual(self.group_members(process.pid), [])

    # -- R22-1 ---------------------------------------------------------------------

    def test_R22_1_a_failed_leader_query_through_recovery_and_the_cli(self):
        workflow_id, task, root, process = self.live_task_group()
        reason = process_ownership.UNCORROBORATED_LEADER_UNAVAILABLE
        with self.leader_query_fails(), self.no_reap():
            report = runtime_module.recover_inherited_processes(self.store_dir)
        self.assertEqual(
            [(tuple(identity), recovered, stuck, unstamped, uncorroborated)
             for identity, recovered, stuck, unstamped, uncorroborated in report[0]
             if identity.owner_id == workflow_id],
            [(self.task_owner(workflow_id), [], [], [], [(root, process.pid, reason)])])
        self.assertEqual(report[1], [])
        self.assertEqual(report.unavailable, [(root, reason)])
        lines = self.report_lines(report)
        self.assertIn("dirun: group %d under %s is REPORTED and left alone (%s)"
                      % (process.pid, root, reason), lines)
        self.assertIn(self.unavailable_count(1), lines)
        self.assertEqual(len(self.group_members(process.pid)), 1)  # never signalled
        self.reaped_once(workflow_id, process)

    def test_R22_1_a_failed_leader_query_holds_the_verification_as_unavailable(self):
        """The verification barrier's reader (``prior_ownership``): a live
        verification group whose leader query FAILS is UNAVAILABLE — not a
        gone leader — and no attempt starts; restored, the corroborated
        live group holds it as unresolved."""
        mission_id, workflow_id = self._R19.ready(self, argv=[
            sys.executable, "-c", "import time; time.sleep(120)"])
        self.owner_died_mid_run(workflow_id)
        pgid = self.the_group(workflow_id)
        name = self.root_name(workflow_id)
        with self.leader_query_fails():
            self.held_on_ownership(workflow_id, (
                "unavailable: group %d of owned root %s is alive and its leader's start time"
                " cannot be read" % (pgid, name)))
        self.assertEqual(len(self.group_members(pgid)), 1)
        self.held_on_ownership(workflow_id, (
            "unresolved: group %d of owned root %s is alive and corroborated as this"
            " verification's" % (pgid, name)))

    # -- R22-2 ---------------------------------------------------------------------

    def test_R22_2_an_unreadable_workflow_store_through_recovery_and_the_cli(self):
        workflow_id, task, root, process = self.live_task_group()
        verification = self.scope(workflow_id)
        path = os.path.join(self.store_dir, wa_store.WORKFLOWS_FILE_NAME)
        mode = os.stat(path).st_mode & 0o7777
        self.addCleanup(os.chmod, path, mode)
        os.chmod(path, 0)
        owners = runtime_module.current_scope_owners(self.store_dir)
        self.assertIsInstance(owners, process_ownership.UnavailableOwners)
        with self.no_reap():
            report = runtime_module.recover_inherited_processes(self.store_dir)
        gap = process_ownership.UNATTRIBUTED_OWNERS_UNAVAILABLE
        self.assertEqual(report[0], [])                 # no ownership inferred
        self.assertEqual(sorted(report[1]), sorted([(task, gap), (verification, gap)]))
        self.assertEqual(report.unavailable[0], (path, owners.reason))
        self.assertEqual(sorted(report.unavailable[1:]),
                         sorted([(task, gap), (verification, gap)]))
        lines = self.report_lines(report)
        self.assertIn(self.unavailable_count(3), lines)
        for scope in (task, verification):
            self.assertIn("dirun: unattributed process record directory REPORTED and left"
                          " alone (%s): %s" % (gap, scope), lines)
            self.assertTrue(os.path.isdir(scope))
        self.assertEqual(len(self.group_members(process.pid)), 1)  # never signalled
        os.chmod(path, mode)                            # restored
        self.reaped_once(workflow_id, process)


class R23RecoveryRouteTests(DeliveryCase):
    """Task 8 R23-1 / R23-2 through the PRODUCTION routes, over a REAL workflow
    whose task scope the production role-turn assignment wrote:

    - recovery and the CLI: ``runtime.recover_inherited_processes(store)``,
      ``cli.report_inherited_recovery``;
    - the release: ``ACTION_RELEASE`` and the Runtime pass.

    R23-1: the workflow store's METADATA cannot be read (EACCES, EIO). It is
    unavailable, never a fresh default document: no ownership inferred, no
    STALE, the CLI never saying ``UNAVAILABLE: 0``, nothing signalled or
    written. Restored, recovery reaps exactly once.

    R23-2: a task credential whose binding is MALFORMED (non-ASCII), before or
    during the release. It is refused and reported, never raised: nothing
    released or retired that it covers, the credential untouched. Restored,
    the release and the retirement complete exactly once."""

    _R19 = R19VerificationLifecycleTests
    _R20 = R20VerificationRetentionTests
    _R20B = R20BTaskScopeRetentionTests
    _R22 = R22RecoveryRouteTests
    ATTEMPT = _R19.ATTEMPT
    seams = _R19.seams
    collect = staticmethod(_R19.collect)
    kill_group = staticmethod(_R19.kill_group)
    attempts = _R19.attempts
    scope = _R19.scope
    control_of = _R19.control_of
    group_members = _R19.group_members
    members_after = _R19.members_after
    unreadable_group_record = _R19.unreadable_group_record
    destruction = _R20.destruction
    expire_retention = _R20.expire_retention
    cleanup_candidates = _R20.cleanup_candidates
    ready = _R20.ready
    verified_and_settled = _R20.verified_and_settled
    retained = _R20.retained
    released_cleanly = _R20.released_cleanly
    insert_under_pressure = _R20.insert_under_pressure
    never_pruned = _R20.never_pruned
    pruned_once = _R20.pruned_once
    clear_damage = staticmethod(_R20.clear_damage)
    LABEL = _R20B.LABEL
    RETAINED = _R20B.RETAINED
    task_scope = _R20B.task_scope
    task_owner = _R20B.task_owner
    spawn_in = _R20B.spawn_in
    held = staticmethod(_R20B.held)
    both = _R20B.both
    scope_refusals = _R20B.scope_refusals
    scope_receipts = _R20B.scope_receipts
    refused_late = _R20B.refused_late
    still_outstanding = _R20B.still_outstanding
    settled_by_the_retry = _R20B.settled_by_the_retry
    no_reap = _R22.no_reap
    report_lines = _R22.report_lines
    unavailable_count = staticmethod(_R22.unavailable_count)
    live_task_group = _R22.live_task_group
    reaped_once = _R22.reaped_once

    # -- helpers -------------------------------------------------------------------

    def never_raised(self, call, *args, **kwargs):
        """``call(*args, **kwargs)``, which must CLASSIFY and never raise on
        content: an escaping ``TypeError`` or ``RecursionError`` is reported as
        the assertion failure it is."""
        try:
            return call(*args, **kwargs)
        except (TypeError, RecursionError) as exc:
            self.fail("%s RAISED %s on content instead of classifying it"
                      % (getattr(call, "__name__", call), type(exc).__name__))

    @staticmethod
    def stat_refused(path, error):
        """``os.stat`` of exactly ``path`` fails with errno ``error``; every other
        path is observed for real. A context manager."""
        real_stat = os.stat

        def stat(target, *args, **kwargs):
            if not isinstance(target, int) and os.fspath(target) == path:
                raise OSError(error, os.strerror(error), path)
            return real_stat(target, *args, **kwargs)
        return mock.patch.object(os, "stat", stat)

    @staticmethod
    def malformed(credential):
        """``credential`` rewritten with a NON-ASCII binding (still valid JSON);
        returns the function that writes the real bytes back."""
        with open(credential, "rb") as handle:
            original = handle.read()
        record = json.loads(original.decode("utf-8"))
        record["binding"] = "é" * 64

        def put(data):
            with open(credential, "wb") as handle:
                handle.write(data)
        put(json.dumps(record, ensure_ascii=False).encode("utf-8"))
        return lambda: put(original)

    # -- R23-1 ---------------------------------------------------------------------

    def store_metadata_unavailable(self, error):
        workflow_id, task, root, process = self.live_task_group()
        verification = self.scope(workflow_id)
        path = os.path.join(self.store_dir, wa_store.WORKFLOWS_FILE_NAME)
        with open(path, "rb") as handle:
            stored = handle.read()
        with self.stat_refused(path, error):
            owners = runtime_module.current_scope_owners(self.store_dir)
            self.assertIsInstance(owners, process_ownership.UnavailableOwners)
            with self.no_reap():
                report = runtime_module.recover_inherited_processes(self.store_dir)
        gap = process_ownership.UNATTRIBUTED_OWNERS_UNAVAILABLE
        self.assertEqual(report[0], [])                 # no ownership inferred
        self.assertEqual(sorted(report[1]), sorted([(task, gap), (verification, gap)]))
        self.assertEqual(report.unavailable[0], (path, owners.reason))
        self.assertEqual(sorted(report.unavailable[1:]),
                         sorted([(task, gap), (verification, gap)]))
        lines = self.report_lines(report)
        self.assertIn(self.unavailable_count(3), lines)
        self.assertNotIn(self.unavailable_count(0), lines)
        for scope in (task, verification):
            self.assertIn("dirun: unattributed process record directory REPORTED and left"
                          " alone (%s): %s" % (gap, scope), lines)
            self.assertTrue(os.path.isdir(scope))
        self.assertEqual(len(self.group_members(process.pid)), 1)  # never signalled
        with open(path, "rb") as handle:
            self.assertEqual(handle.read(), stored)     # never reinitialized
        self.reaped_once(workflow_id, process)          # restored

    def test_R23_1_store_metadata_EACCES_through_recovery_and_the_cli(self):
        import errno
        self.store_metadata_unavailable(errno.EACCES)

    def test_R23_1_store_metadata_EIO_through_recovery_and_the_cli(self):
        import errno
        self.store_metadata_unavailable(errno.EIO)

    # -- R23-2 ---------------------------------------------------------------------

    def test_R23_2_a_malformed_task_credential_holds_the_release_and_is_reported(self):
        """Malformed BEFORE the release: the release refuses with nothing
        released or retired, the Runtime never prunes it, recovery reports
        the scope as MALFORMED. The credential is never rewritten or
        removed. Restored, the release completes and prunes exactly once."""
        mission_id, workflow_id = self.verified_and_settled()
        task = self.task_scope(workflow_id)
        self.expire_retention(workflow_id)
        credential = process_ownership.assignment_path(os.path.basename(task))
        restore = self.malformed(credential)
        self.addCleanup(restore)
        with open(credential, "rb") as handle:
            damaged = handle.read()
        malformed = process_ownership.UNATTRIBUTED_MALFORMED
        reason = "%s (%s)" % (process_ownership.RETIRE_REFUSED_UNATTRIBUTED, malformed)
        self.assertEqual(self.never_raised(self.scope_refusals, workflow_id),
                         ([(task, reason)], None))
        self.never_raised(self.retained, workflow_id, self.held(task, reason),
                          problem=self.RETAINED, scopes=self.both(workflow_id, task))
        self.never_pruned(workflow_id)
        with self.no_reap():
            report = self.never_raised(runtime_module.recover_inherited_processes,
                                       self.store_dir)
        self.assertIn((task, malformed), report[1])
        self.assertIn("dirun: unattributed process record directory REPORTED and left alone"
                      " (%s): %s" % (malformed, task), self.report_lines(report))
        self.assertTrue(os.path.isdir(task))
        with open(credential, "rb") as handle:
            self.assertEqual(handle.read(), damaged)    # never rewritten or removed
        restore()
        self.released_cleanly(workflow_id, scopes=self.both(workflow_id, task))
        self.pruned_once(workflow_id)

    def test_R23_2_a_credential_turning_malformed_inside_the_release_is_refused(self):
        """Malformed INSIDE the relinquish, after the holds read clear: the
        retirement refuses the scope with the reason, never raises and never
        omits it, and keeps the scope. Every later pass holds. Restored, the
        retirement-only retry retires exactly that scope and settles."""
        mission_id, workflow_id = self.verified_and_settled()
        task = self.task_scope(workflow_id)
        self.expire_retention(workflow_id)
        credential = process_ownership.assignment_path(os.path.basename(task))
        restores = []
        self.addCleanup(lambda: [restore() for restore in restores])
        reason = "%s (%s)" % (process_ownership.RETIRE_REFUSED_UNATTRIBUTED,
                              process_ownership.UNATTRIBUTED_MALFORMED)
        self.never_raised(self.refused_late, workflow_id, task,
                          lambda scope: restores.append(self.malformed(credential)), reason)
        self.assertTrue(os.path.isfile(credential))
        self.never_raised(self.still_outstanding, workflow_id, task, reason)
        restores.pop()()
        self.settled_by_the_retry(workflow_id, task)


class R24RecoveryRouteTests(DeliveryCase):
    """Task 8 R24-1 / R24-2 through the PRODUCTION routes, over a REAL workflow
    whose task scope the production role-turn assignment wrote:

    - recovery and the CLI: ``runtime.recover_inherited_processes(store)``,
      ``cli.report_inherited_recovery``;
    - the release: ``ACTION_RELEASE`` and the Runtime pass.

    ``stat`` and ``open`` raise ``FileNotFoundError`` for an existing link
    whose target is missing.

    R24-1: the workflow store FILE is such a link. It is unavailable, never an
    empty store: no ownership inferred, no STALE, the CLI never saying
    ``UNAVAILABLE: 0``, nothing signalled, nothing written at the link or its
    target. The target restored, recovery reaps exactly once.

    R24-2: the task CREDENTIAL is such a link. It is unavailable, never "no
    assignment": the release holds, the Runtime never prunes, recovery reports
    it, and the link is untouched. The target restored, the release and the
    retirement complete exactly once."""

    _R19 = R19VerificationLifecycleTests
    _R20 = R20VerificationRetentionTests
    _R20B = R20BTaskScopeRetentionTests
    _R22 = R22RecoveryRouteTests
    _R23 = R23RecoveryRouteTests
    ATTEMPT = _R23.ATTEMPT
    seams = _R23.seams
    collect = staticmethod(_R23.collect)
    kill_group = staticmethod(_R23.kill_group)
    attempts = _R23.attempts
    scope = _R23.scope
    control_of = _R23.control_of
    group_members = _R23.group_members
    members_after = _R23.members_after
    destruction = _R23.destruction
    expire_retention = _R23.expire_retention
    cleanup_candidates = _R23.cleanup_candidates
    ready = _R23.ready
    verified_and_settled = _R23.verified_and_settled
    retained = _R23.retained
    released_cleanly = _R23.released_cleanly
    insert_under_pressure = _R23.insert_under_pressure
    never_pruned = _R23.never_pruned
    pruned_once = _R23.pruned_once
    clear_damage = staticmethod(_R23.clear_damage)
    unreadable_group_record = _R23.unreadable_group_record
    LABEL = _R23.LABEL
    RETAINED = _R23.RETAINED
    task_scope = _R23.task_scope
    task_owner = _R23.task_owner
    spawn_in = _R23.spawn_in
    held = staticmethod(_R23.held)
    both = _R23.both
    scope_refusals = _R23.scope_refusals
    scope_receipts = _R23.scope_receipts
    refused_late = _R23.refused_late
    still_outstanding = _R23.still_outstanding
    settled_by_the_retry = _R23.settled_by_the_retry
    no_reap = _R23.no_reap
    report_lines = _R23.report_lines
    unavailable_count = staticmethod(_R23.unavailable_count)
    live_task_group = _R23.live_task_group
    reaped_once = _R23.reaped_once

    DANGLING = "FileNotFoundError (symlink target)"

    def dangle(self, path):
        """``path`` becomes a link to a MISSING target; the real object waits
        aside. Returns ``(restore, target, link)``: ``restore`` makes the target
        return, so the link resolves. The cleanup puts the object back — unless
        a production route legitimately removed the link meanwhile."""
        import tempfile
        holding = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, holding, True)
        aside = os.path.join(holding, "aside")
        target = os.path.join(holding, "target")
        os.rename(path, aside)
        os.symlink(target, path)

        def put_back():
            if os.path.islink(path):
                os.unlink(path)
                os.rename(target if os.path.lexists(target) else aside, path)
        self.addCleanup(put_back)
        return (lambda: os.rename(aside, target)), target, os.lstat(path)

    def link_unchanged(self, path, target, link):
        self.assertTrue(os.path.islink(path), path)
        self.assertEqual((os.lstat(path).st_ino, os.readlink(path)), (link.st_ino, target))
        self.assertFalse(os.path.lexists(target), "something was initialized at the target")

    def test_R24_1_a_dangling_workflow_store_link_through_recovery_and_the_cli(self):
        workflow_id, task, root, process = self.live_task_group()
        verification = self.scope(workflow_id)
        path = os.path.join(self.store_dir, wa_store.WORKFLOWS_FILE_NAME)
        with open(path, "rb") as handle:
            stored = handle.read()
        restore, target, link = self.dangle(path)
        owners = runtime_module.current_scope_owners(self.store_dir)
        self.assertIsInstance(owners, process_ownership.UnavailableOwners)
        with self.no_reap():
            report = runtime_module.recover_inherited_processes(self.store_dir)
        gap = process_ownership.UNATTRIBUTED_OWNERS_UNAVAILABLE
        self.assertEqual(report[0], [])                 # no ownership inferred
        self.assertEqual(sorted(report[1]), sorted([(task, gap), (verification, gap)]))
        self.assertEqual(report.unavailable[0], (path, owners.reason))
        self.assertEqual(sorted(report.unavailable[1:]),
                         sorted([(task, gap), (verification, gap)]))
        lines = self.report_lines(report)
        self.assertIn(self.unavailable_count(3), lines)
        self.assertNotIn(self.unavailable_count(0), lines)
        for scope in (task, verification):
            self.assertTrue(os.path.isdir(scope))
        self.assertEqual(len(self.group_members(process.pid)), 1)  # never signalled
        self.link_unchanged(path, target, link)         # never reinitialized
        restore()
        with open(path, "rb") as handle:
            self.assertEqual(handle.read(), stored)
        self.reaped_once(workflow_id, process)          # restored

    def test_R24_2_a_dangling_task_credential_holds_the_release_and_is_reported(self):
        """The credential a link to a missing target BEFORE the release: the
        release refuses with nothing released or retired, the Runtime never
        prunes it, and recovery reports the scope's credential UNAVAILABLE
        (never "no assignment"). The link is never rewritten or removed.
        Restored, the release completes and prunes exactly once."""
        mission_id, workflow_id = self.verified_and_settled()
        task = self.task_scope(workflow_id)
        self.expire_retention(workflow_id)
        credential = process_ownership.assignment_path(os.path.basename(task))
        restore, target, link = self.dangle(credential)
        unavailable = "%s (the assignment record: %s)" % (
            process_ownership.UNATTRIBUTED_CREDENTIAL_UNAVAILABLE, self.DANGLING)
        reason = "%s (%s)" % (process_ownership.RETIRE_REFUSED_UNATTRIBUTED, unavailable)
        self.assertEqual(self.scope_refusals(workflow_id), ([(task, reason)], None))
        self.retained(workflow_id, self.held(task, reason), problem=self.RETAINED,
                      scopes=self.both(workflow_id, task))
        self.never_pruned(workflow_id)
        with self.no_reap():
            report = runtime_module.recover_inherited_processes(self.store_dir)
        self.assertIn((task, unavailable), report[1])
        self.assertIn((task, unavailable), report.unavailable)
        lines = self.report_lines(report)
        self.assertIn("dirun: unattributed process record directory REPORTED and left alone"
                      " (%s): %s" % (unavailable, task), lines)
        self.assertNotIn(self.unavailable_count(0), lines)
        self.assertTrue(os.path.isdir(task))
        self.link_unchanged(credential, target, link)   # never rewritten or removed
        restore()
        self.released_cleanly(workflow_id, scopes=self.both(workflow_id, task))
        self.pruned_once(workflow_id)


class R25HeldAdmissionRouteTests(DeliveryCase):
    """Task 8 R25-1 / R25-2 through the PRODUCTION routes: the Broker's
    ``ACTION_RELEASE``, the Runtime pass (``process_once``) and the delivery
    pass, over a REAL workflow (its verification scope, and its task scope
    assigned as a role turn assigns it, with a stamped root whose group is
    gone), the real Mission gate and store, and ``workspace.release``.

    R25-1: a scope's removal needs BOTH proofs at the moment of effect. The
    ownership readers run before the admission, bracketed by their evidence.
    The admission is HELD across the effect (``admit_cleanup_held``, under
    the Mission store lock), and the evidence is re-read inside it.
    - A hold, or a Mission source failure, landing DURING the ownership
      reads is seen by the admission taken after them.
    - The scope changing DURING the admission is seen by the in-section
      re-read.
    - A hold requested while the section is held lands AFTER the effect,
      and the NEXT object's fresh admission refuses.
    Each refusal means ZERO credential removals and ZERO scope deletions for
    the refused object, counted separately; the bytes are kept, the
    obligation stays outstanding, and once restored, the retirement-only
    retry retires exactly once.

    R25-2: an absence observed THROUGH a dangling ancestor is not absence:
    - a start-unknown verification attempt stays unresolved; no new claim;
    - a workspace removal keeps its lease (``released_at`` None);
    - a credential whose unlink cannot be observed is kept and reported.
    Restored, each settles exactly once, through a VALID path."""

    _R19 = R19VerificationLifecycleTests
    _R20B = R20BTaskScopeRetentionTests
    _R21 = R21RemovalRetryTests
    _R23 = R23RecoveryRouteTests
    ATTEMPT = _R23.ATTEMPT
    seams = _R23.seams
    collect = staticmethod(_R23.collect)
    kill_group = staticmethod(_R23.kill_group)
    attempts = _R23.attempts
    scope = _R23.scope
    control_of = _R23.control_of
    group_members = _R23.group_members
    members_after = _R23.members_after
    destruction = _R23.destruction
    expire_retention = _R23.expire_retention
    cleanup_candidates = _R23.cleanup_candidates
    ready = _R23.ready
    verified_and_settled = _R23.verified_and_settled
    insert_under_pressure = _R23.insert_under_pressure
    never_pruned = _R23.never_pruned
    pruned_once = _R23.pruned_once
    LABEL = _R23.LABEL
    task_scope = _R23.task_scope
    spawn_in = _R23.spawn_in
    both = _R23.both
    scope_receipts = _R23.scope_receipts
    settled_by_the_retry = _R23.settled_by_the_retry
    dead_stamped_root = _R20B.dead_stamped_root
    retained_receipt = _R20B.retained_receipt
    held_on_ownership = _R19.held_on_ownership
    captured_children = _R19.captured_children
    verification_receipts = _R19.verification_receipts
    assert_one_settled_attempt = _R19.assert_one_settled_attempt
    PENDING = _R21.PENDING
    COMPLETED = _R21.COMPLETED
    BOUNDARY = _R21.BOUNDARY
    NOTHING = _R21.NOTHING
    counted = _R21.counted
    tally = staticmethod(_R21.tally)
    lease_of = _R21.lease_of
    removal_receipts = _R21.removal_receipts
    kept = _R21.kept
    first_pass = _R21.first_pass
    removed_by_the_retry = _R21.removed_by_the_retry
    settled_workflow = _R21.settled_workflow
    admission_refusals = _R21.admission_refusals

    DANGLING = "FileNotFoundError (symlink target)"
    REMOVAL = "the cleanup admission refused before a scope's removal (%s: "
    CHANGED = ("%s (its ownership evidence changed before the removal)"
               % process_ownership.RETIRE_REFUSED_UNREADABLE)

    # -- helpers ---------------------------------------------------------------------

    def swap_aside(self, path):
        """``path`` becomes a link to a MISSING target; the real object waits
        aside. Returns ``(put_back, link)``: ``put_back()`` restores the REAL
        object at ``path``, as it was. The cleanup puts it back if the test
        did not."""
        import tempfile
        holding = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, holding, True)
        aside = os.path.join(holding, "aside")
        os.rename(path, aside)
        os.symlink(os.path.join(holding, "missing"), path)
        link = os.lstat(path)

        def put_back():
            if os.path.islink(path):
                os.unlink(path)
                os.rename(aside, path)
        self.addCleanup(put_back)
        return put_back, link

    def dangle(self, path):
        """As R24: ``(restore, target, link)``, where ``restore`` makes the
        missing TARGET return, so the link resolves (a VALID path)."""
        return R24RecoveryRouteTests.dangle(self, path)

    def link_unchanged(self, path, target, link):
        R24RecoveryRouteTests.link_unchanged(self, path, target, link)

    @staticmethod
    def tree_bytes(path):
        """Every entry under ``path`` (followed when ``path`` is a link) with
        each regular file's bytes: what a removal would destroy."""
        seen = []
        for root, directories, files in os.walk(path):
            for name in sorted(directories):
                seen.append((os.path.relpath(os.path.join(root, name), path), None))
            for name in sorted(files):
                full = os.path.join(root, name)
                with open(full, "rb") as handle:
                    seen.append((os.path.relpath(full, path), handle.read()))
        return sorted(seen, key=lambda item: item[0])

    def credential_bytes(self, scope):
        with open(process_ownership.assignment_path(os.path.basename(scope)), "rb") as handle:
            return handle.read()

    @contextlib.contextmanager
    def effects(self, on_removal=None):
        """``{"removals": [credential...], "deletions": [scope...]}``: every
        credential removal the retirement INVOKED and every ``rmtree`` of a
        path under the owned-root base, each counted at its call — the
        EFFECTS, separately from any report. ``on_removal(path)`` runs at a
        credential removal, before it."""
        real_remove, real_rmtree = process_ownership._remove_credential, shutil.rmtree
        base = process_ownership.owned_root_base(None)
        counts = {"removals": [], "deletions": []}

        def remove(path):
            counts["removals"].append(path)
            if on_removal is not None:
                on_removal(path)
            return real_remove(path)

        def rmtree(path, *args, **kwargs):
            if str(path).startswith(base + os.sep):
                counts["deletions"].append(path)
            return real_rmtree(path, *args, **kwargs)
        with mock.patch.object(process_ownership, "_remove_credential", remove), \
                mock.patch.object(shutil, "rmtree", rmtree):
            yield counts

    @contextlib.contextmanager
    def in_retirement(self):
        """``state``: ``state["in"]`` is True exactly while the retirement
        runs (not while the holds read the same records)."""
        real_retire = process_ownership.retire_workflow_scopes
        state = {"in": False}

        def retire(*args, **kwargs):
            state["in"] = True
            try:
                return real_retire(*args, **kwargs)
            finally:
                state["in"] = False
        with mock.patch.object(process_ownership, "retire_workflow_scopes", retire):
            yield state

    @contextlib.contextmanager
    def during_the_ownership_reads(self, scope, event):
        """``event()`` runs ONCE, inside the retirement's ownership reads of
        ``scope`` — right after ``retirement_refusal`` read it, BEFORE the
        admission is taken. ``fired`` lists it."""
        real_refusal = process_ownership.retirement_refusal
        fired = []
        with self.in_retirement() as state:
            def refusal(directory):
                result = real_refusal(directory)
                if state["in"] and directory == scope and not fired:
                    fired.append(directory)
                    event()
                return result
            with mock.patch.object(process_ownership, "retirement_refusal", refusal):
                yield fired

    @contextlib.contextmanager
    def during_the_admission(self, event):
        """``event()`` runs ONCE, INSIDE the retirement's first cleanup
        admission — under the Mission store lock the held section takes,
        before the gate reads. ``fired`` lists it."""
        real_admit = self.gate.admit_cleanup
        fired = []
        with self.in_retirement() as state:
            def admit(*args, **kwargs):
                if state["in"] and not fired:
                    fired.append(True)
                    event()
                return real_admit(*args, **kwargs)
            with mock.patch.object(self.gate, "admit_cleanup", admit):
                yield fired

    def task_with_a_spent_root(self):
        """A verified, settled workflow past retention, whose task scope holds
        a stamped root whose group is gone: ``(mission_id, workflow_id,
        first, second)`` — its two scopes in retirement order."""
        mission_id, workflow_id = self.verified_and_settled()
        task = self.task_scope(workflow_id)
        self.dead_stamped_root(task)
        self.expire_retention(workflow_id)
        first, second = self.both(workflow_id, task)
        return mission_id, workflow_id, first, second

    def released_with_both_refused(self, workflow_id, first, second, problem, run):
        """The release action relinquishes ONCE, then the retirement is
        REFUSED at the admission for ``first`` — and, stopped, for ``second``
        — with ZERO credential removals and ZERO scope deletions, each counted
        separately. Both scopes' bytes and credentials are unchanged, the
        retained receipt keeps the record, never pruned. ``run()`` performs
        the action inside the hooks and returns its result."""
        kept = dict((scope, (self.tree_bytes(scope), self.credential_bytes(scope)))
                    for scope in (first, second))
        spy, refusals = self.admission_refusals()
        with spy, self.seams() as calls, self.destruction() as gone, \
                self.effects() as made:
            released = run()
        self.assertEqual(calls, {"produce": 0, "spawn": 0})
        self.assertEqual([admission.problem for admission in refusals], [problem])
        reason = self.REMOVAL % problem + "%s)" % refusals[0].detail
        self.assertEqual((released.ok, released.outcome), (
            True, broker_module.OUTCOME_RELEASED_DEGRADED))
        self.assertEqual(made["removals"], [])               # ZERO credential removals
        self.assertEqual(made["deletions"], [])              # ZERO scope deletions
        self.assertEqual((gone["relinquish"], gone["retired"], gone["refused"]),
                         (1, [], [(first, reason), (second, reason)]))
        for scope in (first, second):
            self.assertEqual((self.tree_bytes(scope), self.credential_bytes(scope)),
                             kept[scope])
        self.assertIsNotNone(self.record(workflow_id)["workspace_lease"]["released_at"])
        self.assertTrue(wa_record.cleanup_evidence_outstanding(self.record(workflow_id)))
        self.never_pruned(workflow_id)
        return reason

    # -- R25-1: a Mission transition DURING the ownership reads ------------------------

    def test_R25_1a_a_hold_landing_during_the_ownership_reads_removes_nothing(self):
        """The hold lands after ``retirement_refusal`` read the first scope,
        BEFORE its admission: the admission taken after the reads sees it.
        Released, the retry retires both scopes exactly once."""
        mission_id, workflow_id, first, second = self.task_with_a_spent_root()

        def run():
            with self.during_the_ownership_reads(
                    first, lambda: self.service.request_hold(mission_id)) as fired:
                released = self.act(workflow_id, broker_module.ACTION_RELEASE)
            self.assertEqual(fired, [first])
            return released
        self.released_with_both_refused(workflow_id, first, second,
                                        gate_module.PROBLEM_HOLD_ACTIVE, run)
        self.service.release_hold(mission_id)
        self.settled_by_the_retry(workflow_id, first, retired=[first, second])

    def test_R25_1b_a_source_failure_during_the_ownership_reads_removes_nothing(self):
        """The Mission source stops answering during the first scope's
        ownership reads: the held admission refuses as unavailable. Restored,
        the retry retires both exactly once."""
        mission_id, workflow_id, first, second = self.task_with_a_spent_root()
        good = []

        def outage():
            good.append(self.mission_bytes())
            with open(self.mstore.path, "w", encoding="utf-8") as handle:
                handle.write("{oops")

        def run():
            with self.during_the_ownership_reads(first, outage) as fired:
                released = self.act(workflow_id, broker_module.ACTION_RELEASE)
            self.assertEqual(fired, [first])
            with open(self.mstore.path, "wb") as handle:
                handle.write(good[0])
            return released
        self.released_with_both_refused(workflow_id, first, second,
                                        gate_module.PROBLEM_SOURCE_UNAVAILABLE, run)
        self.settled_by_the_retry(workflow_id, first, retired=[first, second])

    def test_R25_1c_the_ownership_reads_with_no_transition_retire_each_exactly_once(self):
        """The allowed control, through the same hooks: nothing lands during
        the reads, so each scope is admitted, HELD, re-read unchanged and
        removed — one credential removal and one deletion per scope."""
        mission_id, workflow_id, first, second = self.task_with_a_spent_root()
        with self.during_the_ownership_reads(first, lambda: None) as fired, \
                self.seams() as calls, self.destruction() as gone, self.effects() as made:
            released = self.act(workflow_id, broker_module.ACTION_RELEASE)
        self.assertEqual(fired, [first])
        self.assertEqual(calls, {"produce": 0, "spawn": 0})
        self.assertEqual((released.ok, released.problem), (True, None))
        self.assertEqual((gone["relinquish"], gone["retired"], gone["refused"]),
                         (1, [first, second], []))
        self.assertEqual(made["removals"], [
            process_ownership.assignment_path(os.path.basename(scope))
            for scope in (first, second)])
        self.assertEqual(made["deletions"], [first, second])
        for scope in (first, second):
            self.assertFalse(os.path.lexists(scope))
        self.pruned_once(workflow_id)

    # -- R25-1: the scope changing DURING the held admission ----------------------------

    def test_R25_1d_a_scope_dangled_during_the_admission_is_never_removed(self):
        """The Reviewer's probe through the production Broker: INSIDE the first
        scope's admission (under the lock), its entry becomes a link to a
        missing target. The admission grants; the in-section re-read sees the
        change: ZERO credential removals and ZERO deletions for it, its bytes
        kept. Per object: the second scope's own fresh admission retires it
        (one removal, one deletion). The real directory put back, the retry
        retires the first exactly once."""
        mission_id, workflow_id, first, second = self.task_with_a_spent_root()
        kept = (self.tree_bytes(first), self.credential_bytes(first))
        swapped = []
        with self.during_the_admission(
                lambda: swapped.append(self.swap_aside(first))) as fired, \
                self.seams() as calls, self.destruction() as gone, self.effects() as made:
            released = self.act(workflow_id, broker_module.ACTION_RELEASE)
        self.assertEqual(fired, [True])
        [(put_back, link)] = swapped
        self.assertEqual(calls, {"produce": 0, "spawn": 0})
        self.assertEqual((released.ok, released.outcome), (
            True, broker_module.OUTCOME_RELEASED_DEGRADED))
        self.assertEqual((gone["relinquish"], gone["retired"], gone["refused"]),
                         (1, [second], [(first, self.CHANGED)]))
        credential = process_ownership.assignment_path(os.path.basename(first))
        self.assertNotIn(credential, made["removals"])       # ZERO for the refused scope
        self.assertNotIn(first, made["deletions"])
        self.assertEqual(made["removals"], [
            process_ownership.assignment_path(os.path.basename(second))])
        self.assertEqual(made["deletions"], [second])
        self.assertEqual((os.lstat(first).st_ino, os.path.islink(first)), (link.st_ino, True))
        self.assertEqual(self.scope_receipts(workflow_id),
                         [self.retained_receipt(first, self.CHANGED)])
        self.never_pruned(workflow_id)
        put_back()
        self.assertEqual((self.tree_bytes(first), self.credential_bytes(first)), kept)
        self.settled_by_the_retry(workflow_id, first)

    def test_R25_1e_a_hold_requested_inside_the_held_section_lands_after_the_effect(self):
        """The admission is HELD across the effect: a hold requested from
        another thread while the first scope's removal runs cannot commit
        until the section ends (it is still waiting when the credential is
        removed), so it is truthfully AFTER that effect. The second scope's
        FRESH admission then refuses: zero removals and deletions for it.
        Released, the retry retires it exactly once."""
        import threading
        mission_id, workflow_id, first, second = self.task_with_a_spent_root()
        threads, waiting, errors = [], [], []

        def request():
            try:
                self.service.request_hold(mission_id)
            except Exception as exc:                     # noqa: BLE001
                errors.append(exc)

        def on_removal(path):
            if not threads:
                thread = threading.Thread(target=request)
                threads.append(thread)
                thread.start()
                thread.join(0.5)
                waiting.append(thread.is_alive())        # blocked on the Mission store lock
        real_refusal = process_ownership.retirement_refusal

        def refusal(directory):
            if directory == second and threads:
                threads[0].join(30)                      # the hold commits, after the effect
                waiting.append(threads[0].is_alive())
            return real_refusal(directory)
        spy, refusals = self.admission_refusals()
        with mock.patch.object(process_ownership, "retirement_refusal", refusal), spy, \
                self.seams() as calls, self.destruction() as gone, \
                self.effects(on_removal) as made:
            released = self.act(workflow_id, broker_module.ACTION_RELEASE)
        self.assertEqual((waiting, errors), ([True, False], []))
        self.assertEqual(calls, {"produce": 0, "spawn": 0})
        self.assertEqual([admission.problem for admission in refusals],
                         [gate_module.PROBLEM_HOLD_ACTIVE])
        reason = self.REMOVAL % gate_module.PROBLEM_HOLD_ACTIVE + "%s)" % refusals[0].detail
        self.assertEqual((released.ok, gone["relinquish"], gone["retired"], gone["refused"]),
                         (True, 1, [first], [(second, reason)]))
        self.assertEqual(made["removals"], [
            process_ownership.assignment_path(os.path.basename(first))])
        self.assertEqual(made["deletions"], [first])
        self.assertTrue(os.path.isdir(second))
        self.assertTrue(self.service.mission_controls(mission_id)["hold_active"])
        self.never_pruned(workflow_id)
        self.service.release_hold(mission_id)
        self.settled_by_the_retry(workflow_id, second)

    # -- R25-2: absence observed through a dangling ancestor -----------------------------

    def test_R25_2a_a_start_unknown_attempt_stays_unresolved_under_a_dangling_ancestor(self):
        """VL6d's start-unknown attempt (its spawn raised before creating its
        owned root). At re-entry the verification scope's ANCESTOR — the
        owned-root base — is a link to a missing target, so ``lstat`` of the
        scope raises FileNotFoundError through it. Before R25 that read as
        "no verification scope exists": resolved NOT-STARTED, and another
        attempt claimed and spawned. Now it holds: no settlement receipt, no
        claim, neither seam reached, on every pass. The target restored —
        the base a VALID link — it resolves not-started and exactly one new
        attempt runs."""
        mission_id, workflow_id = self.ready()
        with self.seams() as calls, self.captured_children() as children, \
                mock.patch.object(process_ownership, "record_pending",
                                  side_effect=OSError(5, "ledger write failed")):
            outcome = self.pass_outcome(workflow_id)
        self.assertEqual((calls, children), ({"produce": 1, "spawn": 1}, []))
        self.assertEqual(outcome.problem, broker_module.PROBLEM_VERIFICATION_START_UNKNOWN)
        claim, unknown = self.attempts(workflow_id)
        base = os.path.dirname(self.scope(workflow_id))
        restore, target, link = self.dangle(base)
        for _ in range(2):
            self.held_on_ownership(workflow_id, "unavailable: its scope cannot be examined"
                                                " (%s)" % self.DANGLING)
            self.assertEqual(self.attempts(workflow_id), [claim, unknown])
        self.link_unchanged(base, target, link)
        restore()
        with self.seams() as calls:
            outcome = self.pass_outcome(workflow_id)
        self.assertEqual(outcome.outcome, "delivery_awaiting_decision",
                         (outcome.problem, outcome.detail))
        self.assertEqual(calls, {"produce": 1, "spawn": 1})
        self.assert_one_settled_attempt(workflow_id, number=2, before=[claim, unknown, (
            "%s 1 settled: not-started — resolved: its spawn created no owned root (0"
            " before it, 0 now), so no process started" % self.ATTEMPT)])

    def test_R25_2d_a_dangling_verification_group_record_is_unavailable_never_unstamped(self):
        """The same reader's group record (the bounded audit): a link to a
        missing target is a record that cannot be read — UNAVAILABLE, named —
        never "never stamped". The pass holds, neither seam reached, no
        attempt claimed. The record restored (a VALID link to it), the root's
        group is gone and exactly one attempt runs."""
        mission_id, workflow_id = self.ready()
        process_ownership.assign_scope(
            process_ownership.OWNER_TYPE_WORKFLOW, self.control_of(workflow_id),
            workflow_id, verification_module.VERIFICATION_OWNER_UNIT)
        root = os.path.join(process_ownership.owned_root_base(self.scope(workflow_id)),
                            "own-0000000000000000")
        os.makedirs(root)
        record = os.path.join(root, process_ownership.OWNED_ROOT_PGID_FILE)
        process = subprocess.Popen(["true"])
        process.wait(timeout=30)                              # a group id that is gone
        with open(record, "w") as handle:
            handle.write(str(process.pid))
        restore, target, link = self.dangle(record)
        for _ in range(2):
            self.held_on_ownership(
                workflow_id, "unavailable: owned root own-0000000000000000's group record"
                             " cannot be read (%s)" % self.DANGLING)
        self.assertEqual(self.attempts(workflow_id), [])
        self.link_unchanged(record, target, link)
        restore()
        with self.seams() as calls:
            outcome = self.pass_outcome(workflow_id)
        self.assertEqual(outcome.outcome, "delivery_awaiting_decision",
                         (outcome.problem, outcome.detail))
        self.assertEqual(calls, {"produce": 1, "spawn": 1})
        self.assert_one_settled_attempt(workflow_id, roots=1)

    def test_R25_2e_a_restored_VALID_credential_link_retires_with_exact_effects(self):
        """R24's restoration, under the R25 evidence reader (the Lead's item 2).
        The task credential is a link to a MISSING target: the release holds,
        with ZERO credential removals and ZERO scope deletions, and the link is
        untouched. The target restored, the link is VALID. A record that is a
        link is followed as its reader follows it, so the next pass releases
        and retires BOTH scopes exactly once:
        - exactly two credential removals — the verification credential, and
          the task credential's LINK;
        - exactly two scope deletions.
        The link's target is never touched, and the record prunes once."""
        mission_id, workflow_id = self.verified_and_settled()
        task = self.task_scope(workflow_id)
        self.expire_retention(workflow_id)
        first, second = self.both(workflow_id, task)
        credential = process_ownership.assignment_path(os.path.basename(task))
        restore, target, link = self.dangle(credential)
        with self.seams() as calls, self.destruction() as gone, self.effects() as made:
            released = self.act(workflow_id, broker_module.ACTION_RELEASE)
        self.assertEqual(calls, {"produce": 0, "spawn": 0})
        self.assertFalse(released.ok, (released.problem, released.detail))
        self.assertEqual(made["removals"], [])                  # ZERO credential removals
        self.assertEqual(made["deletions"], [])                 # ZERO scope deletions
        self.assertEqual((gone["relinquish"], gone["retired"]), (0, []))
        self.assertIsNone(self.record(workflow_id)["workspace_lease"]["released_at"])
        self.link_unchanged(credential, target, link)
        restore()
        with open(target, "rb") as handle:
            stored = handle.read()
        with self.seams() as calls, self.destruction() as gone, self.effects() as made:
            runtime_module.process_once(self.broker)
        self.assertEqual(calls, {"produce": 0, "spawn": 0})
        self.assertEqual((gone["relinquish"], gone["retired"], gone["refused"]),
                         (1, [first, second], []))
        self.assertEqual(made["removals"], [
            process_ownership.assignment_path(os.path.basename(scope))
            for scope in (first, second)])
        self.assertEqual(made["deletions"], [first, second])
        self.assertIsNotNone(self.record(workflow_id)["workspace_lease"]["released_at"])
        self.assertFalse(os.path.lexists(credential))
        with open(target, "rb") as handle:
            self.assertEqual(handle.read(), stored)             # the target never touched
        self.pruned_once(workflow_id)

    def test_R25_2b_a_removal_observed_through_a_dangling_ancestor_keeps_the_lease(self):
        """``workspace.release`` removes the lease directory; then its parent —
        the workspaces root — becomes a link to a missing target, so the
        observing ``lstat`` raises FileNotFoundError THROUGH it. Before R25
        the lease was released. Now: PROBLEM_RELEASE_INCOMPLETE, ``released_at``
        None, one relinquish, the pending receipt, never pruned. The real
        root put back, the removal-only retry observes GENUINE absence and
        releases exactly once."""
        mission_id, workflow_id = self.settled_workflow()
        lease = self.lease_of(workflow_id)
        root = os.path.dirname(lease)
        real_rmtree = workspace_module.shutil.rmtree
        swapped = []

        def rmtree(path, *args, **kwargs):
            result = real_rmtree(path, *args, **kwargs)
            if path == lease and not swapped:
                swapped.append(self.swap_aside(root))
            return result
        detail = ("the removal of %s cannot be observed (%s); the lease is kept and the"
                  " removal is retried" % (lease, self.DANGLING))
        self.first_pass(workflow_id, workspace_module.PROBLEM_RELEASE_INCOMPLETE, detail,
                        relinquish=1,
                        patches=[mock.patch.object(workspace_module.shutil, "rmtree", rmtree)])
        [(put_back, link)] = swapped
        self.assertIsNone(self.record(workflow_id)["workspace_lease"]["released_at"])
        self.kept(workflow_id, directory=False)
        self.assertEqual(os.lstat(root).st_ino, link.st_ino)     # the link untouched
        put_back()
        self.assertFalse(os.path.lexists(lease))                 # genuinely removed
        self.removed_by_the_retry(workflow_id)

    def test_R25_2c_a_credential_unlink_through_a_dangling_ancestor_is_kept(self):
        """INSIDE the first scope's held section, between its evidence re-read
        and its credential's unlink, the credential STORE becomes a link to a
        missing target: ``unlink`` and the observing ``lstat`` both raise
        FileNotFoundError through it. Before R25 that was "removed cleanly".
        Now the credential — the sole deletion proof — is KEPT and reported
        (ONE removal invoked, its bytes unchanged at its real location), the
        record outstanding, never pruned; the second scope, whose credential
        cannot be read, and the store, which cannot be listed, are refused
        with zero effects. The store put back, the retry removes that
        credential and retires the second scope exactly once.

        (Task 8 R26-1: the second scope's FIRST evidence read cannot observe
        its credential through the dangling store, so the gate refuses it
        before any reader runs, as unreadable. Before R26 its attribution
        reader ran first and reported it unattributed, credential
        unavailable. Either way it is refused as UNAVAILABLE, never as
        absent. WHY is asserted on the evidence itself.)"""
        mission_id, workflow_id, first, second = self.task_with_a_spent_root()
        store = process_ownership.assignment_base(None)
        credential = process_ownership.assignment_path(os.path.basename(first))
        with open(credential, "rb") as handle:
            stored = handle.read()
        swapped = []

        def on_removal(path):
            if not swapped:
                swapped.append(self.swap_aside(store))
        with self.seams() as calls, self.destruction() as gone, \
                self.effects(on_removal) as made:
            released = self.act(workflow_id, broker_module.ACTION_RELEASE)
        [(put_back, link)] = swapped
        self.assertEqual(calls, {"produce": 0, "spawn": 0})
        kept = "%s (its absence cannot be observed (%s); its directory was removed)" % (
            process_ownership.RETIRE_REFUSED_CREDENTIAL_KEPT, self.DANGLING)
        self.assertEqual((released.ok, gone["relinquish"], gone["retired"]), (True, 1, []))
        self.assertEqual(gone["refused"], [
            (first, kept), (second, process_ownership.RETIRE_REFUSED_UNREADABLE),
            (store, process_ownership.RETIRE_REFUSED_UNREADABLE)])
        second_credential = process_ownership.assignment_path(os.path.basename(second))
        self.assertEqual(process_ownership._unbound_reason(process_ownership._ownership_evidence(
            second, second_credential, None)), "%s: its observation is unavailable (%s)" % (
                second_credential, self.DANGLING))              # unavailable, never absent
        self.assertEqual(made["removals"], [credential])     # invoked once, never "removed"
        self.assertEqual(made["deletions"], [first])
        self.assertTrue(os.path.isdir(second))
        self.assertEqual(os.lstat(store).st_ino, link.st_ino)
        self.assertTrue(wa_record.cleanup_evidence_outstanding(self.record(workflow_id)))
        self.never_pruned(workflow_id)
        put_back()
        with open(credential, "rb") as handle:
            self.assertEqual(handle.read(), stored)          # RETAINED, byte for byte
        self.settled_by_the_retry(workflow_id, second, retired=[second, credential])
        self.assertFalse(os.path.lexists(credential))


class R26NonWaitingRouteTests(DeliveryCase):
    """Task 8 R26-1 through the PRODUCTION routes — the delivery pass (the
    verification barrier), the Broker's ``ACTION_RELEASE`` (the cleanup hold,
    then the retirement), the Runtime pass, and recovery's production entry
    (``runtime.recover_inherited_processes``) — over a REAL workflow. An
    ownership record that is a FIFO never makes a route WAIT. Each route is
    TIMEOUT-GUARDED (``bounded``): a route that waits fails the test, and only
    this fixture's own FIFO is then opened for writing so the wait ends. NO
    TEARDOWN runs beneath a live route: the inherited ``tearDown`` and every
    cleanup of this case, its fixtures' included, first require each route's
    thread OBSERVED terminated, and are otherwise WITHHELD, each failing the
    case by name (``routes_settled``, ``guarded_cleanup``). Covered: a
    PRE-EXISTING FIFO, and one SUBSTITUTED during the retirement after its
    first evidence read. And the SHARED STAMP WRITER on the verification
    spawn (2i, 2j, 2k): the pass never waits, a refused stamp after ``Popen``
    is START-UNKNOWN (never "not started"), and nothing is replayed. Every
    verification CHILD those cases start is BOOKED at its creation
    (``booking``: fixture bookkeeping, never ownership evidence) and settled
    at cleanup SAFELY (``settle_child``): signalled only while freshly proven
    this process's own uncollected child, then collected through its own
    handle, then judged by signal 0 alone. A child NOT observed ended
    withholds every DESTRUCTIVE cleanup, while the SAFE step still runs.

    Asserted separately, each by its own assertion:
    - the truthful unavailable classification;
    - the unchanged durable obligations (the verification attempts, the
      lease, the credential, the retained receipt);
    - ZERO producer, spawn, revocation, close, relinquish, retirement,
      removal, deletion, reap and pruning effects, counted at their calls;
    - exact restored progress."""

    _R25 = R25HeldAdmissionRouteTests
    _R22 = R22RecoveryRouteTests
    _R19 = _R25._R19
    _R20B = _R25._R20B
    _R21 = _R25._R21
    _R23 = _R25._R23
    ATTEMPT = _R25.ATTEMPT
    seams = _R25.seams
    collect = staticmethod(_R25.collect)
    kill_group = staticmethod(_R25.kill_group)
    attempts = _R25.attempts
    scope = _R25.scope
    control_of = _R25.control_of
    group_members = _R25.group_members
    members_after = _R25.members_after
    destruction = _R25.destruction
    expire_retention = _R25.expire_retention
    cleanup_candidates = _R25.cleanup_candidates
    ready = _R25.ready
    verified_and_settled = _R25.verified_and_settled
    insert_under_pressure = _R25.insert_under_pressure
    never_pruned = _R25.never_pruned
    pruned_once = _R25.pruned_once
    LABEL = _R25.LABEL
    task_scope = _R25.task_scope
    spawn_in = _R25.spawn_in
    both = _R25.both
    scope_receipts = _R25.scope_receipts
    settled_by_the_retry = _R25.settled_by_the_retry
    dead_stamped_root = _R25.dead_stamped_root
    retained_receipt = _R25.retained_receipt
    held_on_ownership = _R25.held_on_ownership
    captured_children = _R25.captured_children
    verification_receipts = _R25.verification_receipts
    assert_one_settled_attempt = _R25.assert_one_settled_attempt
    released_cleanly = _R20B.released_cleanly
    retained = _R20B.retained
    held = staticmethod(_R20B.held)
    effects = _R25.effects
    in_retirement = _R25.in_retirement
    tree_bytes = staticmethod(_R25.tree_bytes)
    credential_bytes = _R25.credential_bytes
    task_owner = _R22.task_owner
    no_reap = _R22.no_reap
    report_lines = _R22.report_lines
    unavailable_count = staticmethod(_R22.unavailable_count)
    live_task_group = _R22.live_task_group
    reaped_once = _R22.reaped_once

    BOUND = 15.0
    #: How many times a waiting route's own FIFOs are released (one second
    #: of joining each) before its thread is judged NOT observed terminated.
    RELEASE_PASSES = 30
    NOT_REGULAR = "NotARegularRecord"
    #: The SAFE cleanup step: settling this fixture's own BOOKED children. It
    #: runs even while a child is NOT observed ended; every other cleanup — a
    #: record put back, a store closed, a tree removed, a seam stopped — is
    #: DESTRUCTIVE, and is then withheld.
    SAFE_STEPS = ("settle_scope",)

    # -- helpers -------------------------------------------------------------------

    def make_fifo(self, path):
        """``path`` — a real record — becomes a FIFO with NO writer; the real
        record waits aside. ``self.restore()`` puts it back (also at
        cleanup). Returns ``path``."""
        import stat
        import tempfile
        holding = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, holding, True)
        aside = os.path.join(holding, "aside")
        os.rename(path, aside)
        os.mkfifo(path, 0o600)

        def put_back():
            if os.path.lexists(path) and stat.S_ISFIFO(os.lstat(path).st_mode):
                os.unlink(path)
                os.rename(aside, path)
        self.addCleanup(put_back)
        self.restore = put_back
        return path

    def bounded(self, call, fifos, *args, **kwargs):
        """``call(*args, **kwargs)`` on a thread, TIMEOUT-GUARDED: it must return
        within ``BOUND``. The thread is REGISTERED before it starts, so no
        cleanup of this case runs beneath it while it lives
        (``guarded_cleanup``). If it waits, this fixture's own ``fifos`` (a
        list, read when needed) are released (``release``) and the test
        FAILS, saying whether the thread was then OBSERVED terminated.
        Returns the result, or raises its exception."""
        import threading
        box = {}

        def run():
            try:
                box["result"] = call(*args, **kwargs)
            except BaseException as exc:                 # noqa: BLE001 - re-raised below
                box["error"] = exc
        name = getattr(call, "__name__", repr(call))
        thread = threading.Thread(target=run, name="bounded-route-%s" % name, daemon=True)
        self.__dict__.setdefault("routes", []).append((thread, fifos))
        thread.start()
        thread.join(self.BOUND)
        waited = thread.is_alive()
        self.release(thread, fifos)
        alive = thread.is_alive()
        self.assertFalse(waited or alive, "the route WAITED on an ownership record (%s); its"
                         " thread %s" % (name, "is STILL ALIVE: every cleanup is withheld"
                                         if alive else "was then observed terminated"))
        if "error" in box:
            raise box["error"]
        return box["result"]

    def release(self, thread, fifos):
        """End a route waiting on this fixture's OWN FIFOs: while ``thread``
        lives, each of ``fifos`` is opened for writing and then for reading,
        each without waiting, and closed — a waiting READ open then returns
        and sees end of file; a waiting WRITE open (a stamp) then returns and
        its write finds no reader — and the thread is joined again, at most
        ``RELEASE_PASSES`` times. Nothing is killed. Whether it ended is read
        from ``is_alive`` afterwards, never assumed from a join."""
        for _ in range(self.RELEASE_PASSES):
            if not thread.is_alive():
                return
            for fifo in list(fifos):
                for mode in (os.O_WRONLY, os.O_RDONLY):
                    try:
                        os.close(os.open(fifo, mode | os.O_NONBLOCK))
                    except OSError:
                        pass
            thread.join(1.0)

    def routes_settled(self):
        """True once every route thread ``bounded`` started is OBSERVED
        terminated: each still alive has its own FIFOs released and is
        joined again, and then ``is_alive`` decides. Once False, it stays
        False for the rest of this case."""
        if self.__dict__.get("unsettled"):
            return False
        routes = self.__dict__.get("routes", ())
        for thread, fifos in routes:
            self.release(thread, fifos)
        self.unsettled = ["route thread %s" % thread.name for thread, _fifos in routes
                          if thread.is_alive()]
        return not self.unsettled

    def withhold(self, step, args=()):
        """Record ``step`` as WITHHELD and fail the case naming it, and what
        is retained. The base is KEPT on this path (``keep_base``), and the
        message says whether it is."""
        self.__dict__.setdefault("withheld", []).append(step)
        routes = list(self.__dict__.get("unsettled") or ())
        not_kept = self.keep_base()
        raise AssertionError(
            "%s WITHHELD%s: not observed ended — %s; %s (base %s %s)"
            % ("cleanup" if args is not None else step,
               " (%s%r)" % (step, args) if args is not None else "",
               ", ".join(routes + list(self.__dict__.get("unsettled_children") or ())),
               "nothing is removed, restored, reaped or unpatched beneath them" if routes else
               "nothing is removed, restored or unpatched beneath them; only this fixture's"
               " own booked children are settled, each signalled only while freshly proven"
               " its uncollected child", getattr(self, "base", None),
               "retained" if not_kept is None else "NOT KEPT: %s" % not_kept))

    def keep_base(self):
        """Task 8 R27 (the Lead's §9.2): on the WITHHELD path, KEEP this case's
        base. ``RuntimeCase.setUp`` makes it a ``tempfile.TemporaryDirectory``
        (``self.tmp``), whose own FINALIZER deletes it at garbage collection or
        interpreter exit, independently of the withheld ``self.tmp.cleanup``.
        R27-dev4's raw records that finalizer EXECUTING for a base whose
        cleanup was withheld (``tempfile.py:817 … Implicitly cleaning up
        <TemporaryDirectory '…tmpede_39x0'>``); the deletion's timing and
        actor are inferred from the source (``_cleanup`` calls ``_rmtree`` at
        ``:816``, then warns), not observed. So the finalizer is DETACHED here
        — on this path only. A case whose cleanup runs still deletes its base
        through ``self.tmp.cleanup``, exactly as before.

        VERSION DEPENDENCY, stated: ``_finalizer`` is a PRIVATE CPython
        attribute of ``tempfile.TemporaryDirectory`` — the ``weakref.finalize``
        its ``__init__`` sets and its ``cleanup`` detaches (CPython 3.9.6, the
        interpreter these runs use: ``tempfile.py:780`` and ``:829``). Where it
        is absent nothing is guessed: this returns why the base is NOT kept,
        and the withheld case says so. Returns None when the base is kept, or
        when no ``TemporaryDirectory`` holds it."""
        holder = self.__dict__.get("tmp")
        if holder is None:
            return None                                      # no finalizer holds the base
        finalizer = getattr(holder, "_finalizer", None)
        if finalizer is None or not hasattr(finalizer, "detach"):
            return ("its TemporaryDirectory has no detachable _finalizer (a private CPython"
                    " attribute this Python does not provide)")
        finalizer.detach()                                   # idempotent: None once detached
        return None

    def tearDown(self):
        """The inherited ``tearDown`` runs only once every route is OBSERVED
        terminated — it runs BEFORE any registered cleanup, so it is gated
        here, not there. Otherwise it is WITHHELD and the case fails."""
        if not self.routes_settled():
            self.withhold("tearDown", None)
        super(R26NonWaitingRouteTests, self).tearDown()

    def addCleanup(self, function, *args, **kwargs):
        """EVERY cleanup of this case — its own and its fixtures': a record
        put back, the private scope store closed, the temporary tree
        removed, a seam patch stopped — runs through ``guarded_cleanup``."""
        super(R26NonWaitingRouteTests, self).addCleanup(self.guarded_cleanup, function,
                                                        args, kwargs)

    def safe_step(self, function):
        """Whether ``function`` is a SAFE step (``SAFE_STEPS``)."""
        return getattr(function, "__name__", None) in self.SAFE_STEPS

    def guarded_cleanup(self, function, args, kwargs):
        """Run ``function`` only once every route is OBSERVED terminated
        (``routes_settled``). Otherwise it is WITHHELD — not run — and the
        case fails naming it; so is every cleanup after it. Nothing is
        removed, restored, reaped or unpatched beneath a route that may
        still be running, and the retained tree is named. Once a CHILD is
        NOT observed ended (``unsettled_children``), every DESTRUCTIVE cleanup
        is withheld the same way, while the SAFE step still runs: unknown
        outcomes stay reported, and the evidence stays retained."""
        if not self.routes_settled():
            self.withhold(getattr(function, "__name__", repr(function)), args)
        if self.__dict__.get("unsettled_children") and not self.safe_step(function):
            self.withhold(getattr(function, "__name__", repr(function)), args)
        return function(*args, **kwargs)

    def verification_record(self, workflow_id):
        """The verification scope, assigned as the producer assigns it, with
        one owned root stamped with a group that is gone: its group record."""
        process_ownership.assign_scope(
            process_ownership.OWNER_TYPE_WORKFLOW, self.control_of(workflow_id),
            workflow_id, verification_module.VERIFICATION_OWNER_UNIT)
        root = os.path.join(process_ownership.owned_root_base(self.scope(workflow_id)),
                            "own-0000000000000000")
        os.makedirs(root)
        record = os.path.join(root, process_ownership.OWNED_ROOT_PGID_FILE)
        process = subprocess.Popen(["true"])
        process.wait(timeout=30)                              # a group id that is gone
        with open(record, "w") as handle:
            handle.write(str(process.pid))
        return record

    def task_record(self, workflow_id):
        """The task scope with a stamped root whose group is gone: its group
        record."""
        task = self.task_scope(workflow_id)
        self.dead_stamped_root(task)
        [(root, _pgid)] = process_ownership.owned_roots(task)
        return task, os.path.join(root, process_ownership.OWNED_ROOT_PGID_FILE)

    # -- the verification barrier -------------------------------------------------------

    def test_R26_2a_the_delivery_pass_never_waits_on_a_FIFO_verification_record(self):
        """The verification scope's group record is a FIFO. Every pass RETURNS
        held, with the record UNAVAILABLE ("cannot be read
        (NotARegularRecord)"). Neither the producer nor the spawn is reached,
        on either of two passes (no replay), and the attempts stand unchanged.
        Restored, exactly one attempt runs and settles."""
        mission_id, workflow_id = self.ready()
        record = self.verification_record(workflow_id)
        fifo = self.make_fifo(record)
        detail = ("unavailable: owned root own-0000000000000000's group record cannot be read"
                  " (%s)" % self.NOT_REGULAR)
        before = self.attempts(workflow_id)
        for _ in range(2):
            with self.seams() as calls:
                outcome = self.bounded(self.pass_outcome, [fifo], workflow_id)
            self.assertEqual(calls, {"produce": 0, "spawn": 0})     # zero effects, no replay
            self.assertEqual((outcome.outcome, outcome.problem, outcome.detail), (
                "delivery_held", broker_module.PROBLEM_VERIFICATION_OWNERSHIP_UNRESOLVED,
                "an earlier verification process's ownership is %s; no attempt starts"
                % detail))
            self.assertEqual(self.attempts(workflow_id), before)    # durable: unchanged
        self.assertTrue(stat_is_fifo(record))
        self.restore()
        with self.seams() as calls:
            outcome = self.pass_outcome(workflow_id)
        self.assertEqual(outcome.outcome, "delivery_awaiting_decision",
                         (outcome.problem, outcome.detail))
        self.assertEqual(calls, {"produce": 1, "spawn": 1})
        self.assert_one_settled_attempt(workflow_id, roots=1)

    # -- the release: the cleanup hold, and the retirement ---------------------------

    def test_R26_2b_the_release_never_waits_on_a_FIFO_task_group_record(self):
        """The task scope's group record is a FIFO. The release action RETURNS,
        refused by the cleanup hold ("cannot be retired —
        records cannot be read"). Nothing is revoked, closed, relinquished,
        retired, removed or deleted. The lease stays unreleased, its directory
        and both scopes kept, the credentials unchanged. The Runtime pass
        returns and removes nothing, and the record is never pruned. Restored,
        the release completes and the record prunes exactly once."""
        mission_id, workflow_id = self.verified_and_settled()
        task, record = self.task_record(workflow_id)
        self.expire_retention(workflow_id)
        credentials = [self.credential_bytes(scope) for scope in self.both(workflow_id, task)]
        fifo = self.make_fifo(record)
        with self.seams() as calls, self.destruction() as gone, self.effects() as made:
            released = self.bounded(self.act, [fifo], workflow_id, broker_module.ACTION_RELEASE)
            self.bounded(runtime_module.process_once, [fifo], self.broker)
        self.assertEqual(calls, {"produce": 0, "spawn": 0})
        self.assertEqual((released.ok, released.problem, released.detail), (
            False, broker_module.PROBLEM_PROCESS_SCOPE_RETAINED,
            self.held(task, process_ownership.RETIRE_REFUSED_UNREADABLE)))
        self.assertEqual((gone["revoke"], gone["close"], gone["relinquish"], gone["retired"],
                          gone["refused"]), (0, 0, 0, [], []))
        self.assertEqual((made["removals"], made["deletions"]), ([], []))
        self.assertIsNone(self.record(workflow_id)["workspace_lease"]["released_at"])
        self.assertTrue(os.path.isdir(self.lease_path(workflow_id)))
        for scope, stored in zip(self.both(workflow_id, task), credentials):
            self.assertTrue(os.path.isdir(scope))
            self.assertEqual(self.credential_bytes(scope), stored)
        self.assertEqual(self.cleanup_candidates(), [])
        self.never_pruned(workflow_id)
        self.assertTrue(stat_is_fifo(record))
        self.restore()
        self.released_cleanly(workflow_id, scopes=self.both(workflow_id, task))
        self.pruned_once(workflow_id)

    def test_R26_2c_a_task_record_substituted_during_the_retirement_never_waits(self):
        """The hold reads the task record REGULAR, and the lease is released.
        Then, inside the retirement, the task scope's record becomes a FIFO
        after its first evidence read. ``retirement_refusal``'s open does not
        wait: the task scope is refused (unreadable), with ZERO credential
        removals and ZERO deletions for it. The verification scope is retired,
        a truthful partial effect. The retained receipt keeps the record, and
        it is never pruned. Restored, the retirement-only retry retires the
        task scope exactly once and settles."""
        mission_id, workflow_id = self.verified_and_settled()
        task, record = self.task_record(workflow_id)
        self.expire_retention(workflow_id)
        verification = self.scope(workflow_id)
        real_evidence = process_ownership._ownership_evidence
        swapped = []

        def evidence(directory, credential, base=None):
            result = real_evidence(directory, credential, base)
            if directory == task and not swapped:
                swapped.append(self.make_fifo(record))    # AFTER it was observed regular
            return result
        with mock.patch.object(process_ownership, "_ownership_evidence", evidence), \
                self.seams() as calls, self.destruction() as gone, self.effects() as made:
            released = self.bounded(self.act, swapped, workflow_id,
                                    broker_module.ACTION_RELEASE)
        self.assertEqual(swapped, [record])
        self.assertEqual(calls, {"produce": 0, "spawn": 0})
        self.assertEqual((released.ok, released.outcome), (
            True, broker_module.OUTCOME_RELEASED_DEGRADED))
        reason = process_ownership.RETIRE_REFUSED_UNREADABLE
        self.assertEqual((gone["relinquish"], gone["retired"], gone["refused"]),
                         (1, [verification], [(task, reason)]))
        self.assertNotIn(process_ownership.assignment_path(os.path.basename(task)),
                         made["removals"])                       # ZERO for the task scope
        self.assertNotIn(task, made["deletions"])
        self.assertTrue(os.path.isdir(task))
        self.assertEqual(self.scope_receipts(workflow_id), [self.retained_receipt(task, reason)])
        self.never_pruned(workflow_id)
        self.restore()
        self.settled_by_the_retry(workflow_id, task)

    # -- recovery's production entry ------------------------------------------------------

    def test_R26_2d_recovery_never_waits_on_a_FIFO_task_credential(self):
        """The live task group's CREDENTIAL is a FIFO. Recovery's production
        entry RETURNS: the scope is reported unattributed and UNAVAILABLE
        ("the assignment record: NotARegularRecord"), and the CLI says so. No
        reap is called and the group is never signalled. The FIFO is never
        removed or replaced. Restored, recovery reaps exactly once."""
        workflow_id, task, root, process = self.live_task_group()
        credential = process_ownership.assignment_path(os.path.basename(task))
        fifo = self.make_fifo(credential)
        unavailable = "%s (the assignment record: %s)" % (
            process_ownership.UNATTRIBUTED_CREDENTIAL_UNAVAILABLE, self.NOT_REGULAR)
        with self.no_reap():
            report = self.bounded(runtime_module.recover_inherited_processes, [fifo],
                                  self.store_dir)
        self.assertIn((task, unavailable), report[1])
        self.assertIn((task, unavailable), report.unavailable)
        lines = self.report_lines(report)
        self.assertIn("dirun: unattributed process record directory REPORTED and left alone"
                      " (%s): %s" % (unavailable, task), lines)
        self.assertNotIn(self.unavailable_count(0), lines)
        self.assertEqual(len(self.group_members(process.pid)), 1)   # never signalled
        self.assertTrue(stat_is_fifo(credential))
        self.restore()
        self.reaped_once(workflow_id, process)

    def test_R26_2e_the_release_never_waits_on_a_FIFO_binding_key(self):
        """The store's BINDING KEY is a FIFO. The release action RETURNS,
        refused by the cleanup hold. Every scope's credential is unattributed
        and unavailable ("the store's binding key: NotARegularRecord"), never
        forged. Nothing is relinquished, retired, removed or deleted, the lease
        is unreleased, and the key is never replaced. Restored, the release
        completes and the record prunes exactly once."""
        mission_id, workflow_id = self.verified_and_settled()
        task, _record = self.task_record(workflow_id)
        self.expire_retention(workflow_id)
        key = os.path.join(process_ownership.assignment_base(None),
                           process_ownership.ASSIGNMENT_KEY_FILE)
        fifo = self.make_fifo(key)
        with self.seams() as calls, self.destruction() as gone, self.effects() as made:
            released = self.bounded(self.act, [fifo], workflow_id, broker_module.ACTION_RELEASE)
        self.assertEqual(calls, {"produce": 0, "spawn": 0})
        self.assertFalse(released.ok, (released.problem, released.detail))
        self.assertIn("the store's binding key: %s" % self.NOT_REGULAR, released.detail)
        self.assertEqual((gone["relinquish"], gone["retired"]), (0, []))
        self.assertEqual((made["removals"], made["deletions"]), ([], []))
        self.assertIsNone(self.record(workflow_id)["workspace_lease"]["released_at"])
        self.assertTrue(stat_is_fifo(key))
        self.never_pruned(workflow_id)
        self.restore()
        self.released_cleanly(workflow_id, scopes=self.both(workflow_id, task))
        self.pruned_once(workflow_id)

    # -- Task 8 R26, the shared stamp WRITER, through the delivery pass ---------------------

    #: The counted command: ONE byte per run, written by one unbuffered
    #: ``os.write`` and its descriptor CLOSED before the optional sleep — an
    #: explicit lifetime on ONE line, because an approved verification argv
    #: admits no control character (``mission.record``).
    WRITER_ARGV_CODE = ("import os, sys, time; descriptor = os.open(sys.argv[1],"
                        " os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600);"
                        " os.write(descriptor, b'x'); os.close(descriptor);"
                        " time.sleep(float(sys.argv[2]))")
    #: How long a started child's wait may take before it is judged NOT
    #: observed ended.
    WAIT_SECONDS = 10.0

    def counting_ready(self, sleep=0.0):
        """A ready workflow whose APPROVED verification argv appends ONE byte to
        a marker per run (then sleeps ``sleep`` seconds): the actual-command
        invocation count, read from bytes apart from every report. Its
        verification scope's BOOKED children are settled at cleanup
        (``settle_at_cleanup``)."""
        self.writer_marker = os.path.join(self.base, "writer-marker")
        mission_id, workflow_id = self.ready(argv=[sys.executable, "-c", self.WRITER_ARGV_CODE,
                                                   self.writer_marker, str(sleep)])
        self.settle_at_cleanup(self.scope(workflow_id))
        return mission_id, workflow_id

    def settle_at_cleanup(self, scope):
        """Register the settlement of ``scope``'s BOOKED children — every child a
        spawn under ``booking`` created there (``capturing`` books them) — to
        run BEFORE every earlier-registered cleanup (the tree's removal among
        them)."""
        self.__dict__.setdefault("owned_children", [])
        self.addCleanup(self.settle_scope, scope)

    def booking(self, book):
        """FIXTURE BOOKKEEPING, never ownership evidence: while active, every
        child the production spawn starts through the stamping wrapper is
        BOOKED in ``book`` at the point it is CREATED — ``(process, started,
        root)``: its process object, its start time read right then, and its
        owned root — whatever the spawn later returns or raises. A mutant that
        discards ``SpawnUnconfirmed``'s process carrier (WM9, the pre-writer
        code) therefore cannot take away this fixture's access to the child it
        created, and the production contract stays exactly as the mutant made
        it. Nothing about ownership is inferred from a booking: it serves this
        fixture's own cleanup only."""
        real, fixture = subprocess.Popen, self

        class Booked(real):
            def __init__(created, args, *rest, **kwargs):
                super(Booked, created).__init__(args, *rest, **kwargs)
                if (isinstance(args, (list, tuple)) and len(args) > 2
                        and args[1] == process_ownership._STAMP_WRAPPER):
                    book.append((created, fixture.start_of(created.pid), str(args[2])))
        return mock.patch.object(subprocess, "Popen", Booked)

    #: The ``Popen`` class as this module found it at IMPORT — before any case's
    #: patch, ``booking``'s own included. ``start_of`` reads through it alone.
    FIXTURE_POPEN = subprocess.Popen

    @classmethod
    def start_of(cls, pid):
        """``pid``'s live start time as ``ps -o lstart=`` reports it NOW, or
        None (no such process, or the query failed). FIXTURE BOOKKEEPING on the
        fixture's OWN path — ``ps`` through ``FIXTURE_POPEN`` — so no case's own
        patch reaches it: not one of the product's ``leader_start_time``, and
        not one of ``subprocess`` (R26NonWaitingRecordTests' W1s showed the
        first: a booking read through the product's one definition recorded a
        case's controlled literal, and the still-pinned child was then read as
        "another child" and never settled). The booked read and every fresh one
        come from here, so they compare like with like; the product's own
        definition is still what its assertions read."""
        if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 1:
            return None
        try:
            with cls.FIXTURE_POPEN(["ps", "-o", "lstart=", "-p", str(pid)],
                                   stdout=subprocess.PIPE,
                                   stderr=subprocess.DEVNULL) as query:
                output = query.communicate()[0]
        except (OSError, subprocess.SubprocessError):
            return None
        if query.returncode != 0:
            return None
        return output.decode("utf-8", "replace").strip() or None

    @staticmethod
    def parent_of(pid):
        """What ``ps`` reports NOW as ``pid``'s parent, read WITH its status —
        nothing is signalled — as ``(kind, value)``: ``("listed", ppid)``;
        ``("unlisted", None)`` when ``ps`` ran cleanly and listed no such
        process (status 1, no output, nothing on stderr); otherwise
        ``("unavailable", why)`` — an instrument failure, never read as gone
        and never as a parent."""
        try:
            listed = subprocess.run(["ps", "-o", "ppid=", "-p", str(pid)],
                                    capture_output=True, text=True)
        except OSError as exc:
            return "unavailable", "ps could not run (%s)" % exc.__class__.__name__
        text = listed.stdout.strip()
        if listed.returncode == 0 and text.isdigit():
            return "listed", int(text)
        if listed.returncode == 1 and not text and not listed.stderr.strip():
            return "unlisted", None
        return "unavailable", "ps answered status %d, %r, %r" % (
            listed.returncode, text, listed.stderr.strip()[:80])

    def pid_absent(self, pid):
        """Whether NO process holds ``pid`` — OBSERVED by two reads that agree:
        ``ps`` ran cleanly and lists none, and signal 0 (nothing delivered)
        answers ``ESRCH``. Anything else, an unreadable answer included, is not
        observed absent."""
        kind, _parent = self.parent_of(pid)
        return kind == "unlisted" and process_ownership._process_exists(pid) is False

    def pinned(self, process, started):
        """What ``process``'s pid names NOW, read fresh — nothing is signalled —
        as ``(state, why)``: ``collected`` (its own handle holds a return code —
        which may be FABRICATED: CPython's wait and poll set 0 on ``ECHILD``
        after a collection elsewhere — so it is never read as an exit status
        here); ``absent`` (no process holds the pid,
        ``pid_absent``); ``pinned`` (``ps`` names THIS process its parent AND
        its start time is the one BOOKED at its creation — still this
        process's own UNCOLLECTED child, so neither its pid nor its group can
        have been released for reuse); ``another parent`` / ``another child``
        (MISMATCHED identity: a process holds the pid, but not this child);
        ``unavailable`` (an answer cannot be read). The booking decides this
        fixture's own cleanup only, never ownership."""
        if process.returncode is not None:
            return "collected", "its own handle holds return code %r" % (process.returncode,)
        kind, parent = self.parent_of(process.pid)
        if kind == "unlisted":
            if process_ownership._process_exists(process.pid) is False:
                return "absent", "no process holds its pid (ps lists none; signal 0: ESRCH)"
            return "unavailable", "ps lists no such process, but signal 0 does not answer ESRCH"
        if kind != "listed":
            return "unavailable", "its parent cannot be read: %s" % parent
        if parent != os.getpid():
            return "another parent", "ps names %d its parent, not this process" % parent
        start = self.start_of(process.pid)
        if started is None or start is None:
            return "unavailable", "its start time cannot be compared (booked %r, now %r)" % (
                started, start)
        if start != started:
            return "another child", "the pid names another child of this process (started" \
                " %s, not the booked %s)" % (start, started)
        return "pinned", "this process's own uncollected child, started %s" % started

    def account(self, process, reason):
        """An UNFINALIZED handle, ACCOUNTED for and left so: ``reason`` says why
        its exit status cannot be recovered — its child was collected OUT OF
        BAND (the production reaper's ``waitpid`` discards the status, and a
        later wait or poll here would FABRICATE 0), or its child is
        unattributable. It is never waited or polled to look finalized. So a
        ``ResourceWarning: subprocess N is still running`` for it, when the
        handle is collected, is EXPECTED and ATTRIBUTABLE: never evidence of a
        survivor — nor is a missing warning evidence of none."""
        self.__dict__.setdefault("unfinalized", []).append((process.pid, reason))
        sys.stderr.write("R26 fixture: handle %d is left UNFINALIZED (%s); a ResourceWarning"
                         " for it is expected and attributable\n" % (process.pid, reason))

    def settle_child(self, process, started):
        """ONE booked child, settled SAFELY (``settle_owned``). Returns what is
        NOT observed ended about it — empty ONLY when it is OBSERVED ENDED.
        Three outcomes, kept distinct: PROVEN OWN-CHILD COLLECTION (``pinned``:
        collected through its OWN handle first — an exited child at once
        (``poll``), a live one after ``kill_group`` signals its own group
        (``wait``) — so its handle holds its TRUE exit status, and nothing
        collects it out of band); an ALREADY-COLLECTED handle (``collected``)
        or a pid no process holds (``absent``: collected out of band,
        ``account``): never signalled, never waited or polled; UNAVAILABLE or
        MISMATCHED identity: NOT observed ended, never signalled, never waited
        or polled (that could only collect another process, or FABRICATE a
        return code), and ACCOUNTED for. OBSERVED ENDED means observed ABSENT:
        no process holds its pid (``pid_absent``) and its group's signal-0
        answer is the observed-absent one (``_group_alive(...) is False``). A
        return code alone — possibly fabricated — is never taken for it."""
        state, why = self.pinned(process, started)
        signalled = "not signalled"
        if state == "pinned":
            if process.poll() is None:                       # alive: signalled while pinned
                self.kill_group(process.pid)                 # its OWN group: pid == pgid
                signalled = "SIGKILL attempted on its group while pinned"
                try:
                    process.wait(timeout=self.WAIT_SECONDS)
                except subprocess.TimeoutExpired:
                    return ["child %d (running after its wait; %s)" % (process.pid, signalled)]
            state, why = "collected", "collected by its own handle while pinned (exit status" \
                " %r)" % (process.returncode,)
        elif state != "collected":
            self.account(process, "%s: %s" % (state, why))
        if state not in ("collected", "absent"):
            return ["child %d (%s; %s: %s — NOT observed ended, not waited)" % (
                process.pid, signalled, state, why)]
        if not self.pid_absent(process.pid):
            return ["child %d (%s; %s, but a process holds its pid, or that cannot be read:"
                    " NOT observed ended)" % (process.pid, signalled, why)]
        if process_ownership._group_alive(process.pid) is not False:
            return ["child %d (%s; %s, but its group is not observed gone: NOT signalled"
                    " again)" % (process.pid, signalled, why)]
        return []

    def settle_owned(self, scope, children):
        """SAFE, FRESH-PROVEN settlement of ``children`` — this fixture's own
        BOOKED children whose owned root is under ``scope`` — and a REPORT of
        every group ``scope``'s ledger names that is not one of them. Returns
        what is NOT observed ended (empty when all are): a child freshly proven
        still this process's own uncollected child (``pinned``) is COLLECTED
        through its OWN handle — at once if it has exited, otherwise after the
        existing test-cleanup signal (``kill_group``) to its own group — so its
        handle holds its TRUE exit status; any other handle is never
        signalled, waited or polled, and is ACCOUNTED for as unfinalized
        (``settle_child``, ``account``); it is never signalled again, and is
        OBSERVED ENDED only when observed ABSENT (no process holds its pid, and
        its group's signal-0 answer is the observed-absent one); a ledger group
        that is not a booked child is NOT signalled, only reported (the ledger
        is read for this report only). Nothing here depends on why a signal
        was refused. (An UNCONFIRMED observation, not a mechanism: in
        dev26 the pinned ledger reaper reported "could not signal … EPERM" for
        groups whose leader had not yet been waited.)"""
        prefix = os.path.join(os.path.realpath(scope), "")
        mine = [(process, started) for process, started, root in children
                if os.path.realpath(root).startswith(prefix)]
        unsettled = []
        for process, started in mine:
            unsettled.extend(self.settle_child(process, started))
        booked = set(process.pid for process, _started in mine)
        for pgid in sorted(process_ownership.owned_groups(scope)):
            if pgid not in booked:
                unsettled.append("ledger group %d (not a child this fixture booked: not"
                                 " signalled)" % pgid)
        return unsettled

    def settle_scope(self, scope):
        """Settle ``scope``'s BOOKED children (``settle_owned``) — a SAFE step,
        run even while another child is unsettled. Anything not observed ended
        is recorded in the CHILD gate (``unsettled_children``), so every later
        DESTRUCTIVE cleanup is WITHHELD while the safe step still runs, and
        this cleanup FAILS naming what is retained. An error while settling is
        recorded the same way, its cause chained — never swallowed."""
        try:
            unsettled = self.settle_owned(scope, self.__dict__.get("owned_children", ()))
        except Exception as exc:                             # noqa: BLE001 - re-raised below
            unsettled, cause = ["the settlement itself raised %r" % (exc,)], exc
        else:
            cause = None
        if not unsettled:
            return
        self.unsettled_children = list(self.__dict__.get("unsettled_children") or ()) + unsettled
        try:
            roots = sorted(os.listdir(process_ownership.owned_root_base(scope)))
        except OSError as exc:
            roots = "unlisted (%s)" % exc.__class__.__name__
        raise AssertionError(
            "owned process(es) NOT observed ended: %s — retained: the scope %s, its ledger"
            " %s, its owned roots %s" % (", ".join(unsettled), scope,
                                         process_ownership.ledger_path(scope), roots)
        ) from cause

    def invocations(self):
        try:
            with open(self.writer_marker, "rb") as handle:
                return len(handle.read())
        except FileNotFoundError:
            return 0

    def capturing(self):
        """A patch around the production spawn: every child it creates is
        BOOKED (``booking``) for ``settle_scope``, whatever it returns or
        raises, and WHATEVER it raises is KEPT, then re-raised unchanged. Enter
        it BEFORE ``seams``, which wraps what it finds. Each case judges what
        was raised by assertion."""
        real, captured = process_ownership.spawn_owned, []

        def spawn(*args, **kwargs):
            try:
                with self.booking(self.__dict__.setdefault("owned_children", [])):
                    return real(*args, **kwargs)
            except Exception as exc:                         # noqa: BLE001 - re-raised
                captured.append(exc)
                raise
        return mock.patch.object(process_ownership, "spawn_owned", spawn), captured

    def the_unconfirmed(self, captured):
        """The ONE exception the production spawn raised, asserted to be the
        parent's ``SpawnUnconfirmed`` — a failure, never an error, otherwise.
        Each case asserts it FIRST, ahead of every effect and of the pass's
        report (``start_unknown``), so the spawn boundary is judged before
        anything that reads its outcome."""
        self.assertEqual(len(captured), 1, captured)
        self.assertIsInstance(captured[0], process_ownership.SpawnUnconfirmed)
        return captured[0]

    def exit_of(self, process):
        try:
            return process.wait(timeout=self.BOUND)
        except subprocess.TimeoutExpired:
            self.fail("the started child never ended")

    def wait_for(self, condition, what):
        import time
        deadline = time.monotonic() + self.BOUND - 2
        while time.monotonic() < deadline:
            if condition():
                return
            time.sleep(0.02)
        self.fail(what)

    def nothing_written(self, fifo):
        descriptor = os.open(fifo, os.O_RDONLY | os.O_NONBLOCK)
        try:
            try:
                data = os.read(descriptor, 64)
            except BlockingIOError:
                data = b""
        finally:
            os.close(descriptor)
        self.assertEqual(data, b"")

    def plant(self, planted, fifo):
        """Record the FIFO this case just PLANTED at ``fifo`` — its identity,
        read without following — for ``unplant``."""
        info = os.lstat(fifo)
        planted.append((fifo, info.st_dev, info.st_ino))

    def unplant(self, planted):
        """The fixture's OWN planted FIFOs, removed at cleanup however the case
        ended: the inline ``os.unlink`` runs only when the case gets that far,
        and a FIFO left in an owned root keeps its private base (the product
        reads an unreadable group record as possibly live — correctly). Only
        the very FIFO this case made is removed: a path that is gone, or that
        holds anything else (the child's own record put back), is left as it
        is. A DESTRUCTIVE step, registered BEFORE ``counting_ready``: it runs
        after ``settle_scope`` and is WITHHELD like every other while a route
        thread or a child is unsettled."""
        import stat
        for fifo, device, inode in planted:
            try:
                info = os.lstat(fifo)
            except FileNotFoundError:
                continue                                     # removed inline
            if stat.S_ISFIFO(info.st_mode) and (info.st_dev, info.st_ino) == (device, inode):
                os.unlink(fifo)

    def start_unknown(self, workflow_id, before, outcome):
        """The pass RETURNED held as START-UNKNOWN, its cause the parent's
        ``SpawnUnconfirmed`` — never "not started", never "could not start" —
        and exactly one settlement was added. Returns the attempts."""
        cause = "the spawn raised SpawnUnconfirmed"
        self.assertEqual((outcome.outcome, outcome.problem, outcome.detail), (
            "delivery_held", broker_module.PROBLEM_VERIFICATION_START_UNKNOWN,
            "the verification's spawn raised SpawnUnconfirmed: whether a process started"
            " is not known; the next pass decides from its owned roots, and nothing is"
            " re-run meanwhile"))
        attempts = self.attempts(workflow_id)
        self.assertEqual(len(attempts), len(before) + 2, attempts)  # a claim, a settlement
        self.assertEqual(attempts[-1], "%s 1 %s: %s — %s; whether a process started is not"
                         " known on this pass" % (
                             self.ATTEMPT, broker_module.VERIFICATION_ATTEMPT_SETTLED,
                             broker_module.VERIFICATION_ATTEMPT_START_UNKNOWN, cause))
        return attempts

    def held_without_replay(self, workflow_id, attempts, state, detail, passes=2):
        """Every pass RETURNS held on the verification's ownership, with no
        producer, no spawn and no reap; the attempts stand unchanged."""
        for _ in range(passes):
            with self.seams() as calls, self.no_reap():
                outcome = self.bounded(self.pass_outcome, [], workflow_id)
            self.assertEqual(calls, {"produce": 0, "spawn": 0})
            self.assertEqual((outcome.outcome, outcome.problem, outcome.detail), (
                "delivery_held", broker_module.PROBLEM_VERIFICATION_OWNERSHIP_UNRESOLVED,
                "an earlier verification process's ownership is %s: %s; no attempt starts"
                % (state, detail)))
            self.assertEqual(self.attempts(workflow_id), attempts)

    def test_R26_2i_a_FIFO_verification_stamp_record_never_waits_and_never_reads_no_spawn(
            self):
        """The verification spawn's new owned root has a FIFO at its GROUP record
        path before the spawn. The pass RETURNS: the child's stamp and the
        parent's both refuse at once; the child refuses to exec, so the actual
        verification command NEVER runs; ``spawn_owned`` raises
        ``SpawnUnconfirmed`` and the attempt settles START-UNKNOWN. Nothing is
        written into the FIFO; the ledger names the group with no pending
        record; the lease and the scope's credential stand. Next passes: held
        on the UNAVAILABLE record — no producer, no spawn, no reap (no replay),
        attempts unchanged. Restored (the FIFO removed), the root reads as
        never stamped: held UNRESOLVED, still nothing run."""
        planted = []
        self.addCleanup(self.unplant, planted)                    # after settle_scope
        mission_id, workflow_id = self.counting_ready()
        fifos = []
        real_create = process_ownership.create_owned_root

        def create(nonce, base=None):
            root = real_create(nonce, base)
            fifos.append(os.path.join(root, process_ownership.OWNED_ROOT_PGID_FILE))
            os.mkfifo(fifos[-1], 0o600)
            self.plant(planted, fifos[-1])
            return root
        capture, captured = self.capturing()
        before = self.attempts(workflow_id)
        with mock.patch.object(process_ownership, "create_owned_root", create), capture, \
                self.seams() as calls, self.no_reap():
            outcome = self.bounded(self.pass_outcome, fifos, workflow_id)
        unconfirmed = self.the_unconfirmed(captured)              # the spawn boundary, FIRST
        [fifo] = fifos
        scope = self.scope(workflow_id)
        self.assertEqual(calls, {"produce": 1, "spawn": 1})
        attempts = self.start_unknown(workflow_id, before, outcome)
        from target_runtime import spawn_stamp
        self.assertEqual(self.exit_of(unconfirmed.process), spawn_stamp.EXIT_UNSTAMPABLE)
        self.assertEqual(self.invocations(), 0)                   # the command never ran
        self.assertTrue(stat_is_fifo(fifo))
        self.nothing_written(fifo)
        self.assertEqual(process_ownership.owned_groups(scope), {unconfirmed.process.pid})
        self.assertEqual(process_ownership.pending_nonces(scope), [])
        self.assertIsNone(self.record(workflow_id)["workspace_lease"]["released_at"])
        credential = self.credential_bytes(scope)
        name = os.path.basename(os.path.dirname(fifo))
        self.held_without_replay(workflow_id, attempts, verification_module.PRIOR_UNAVAILABLE,
                                 "owned root %s's group record cannot be read (%s)"
                                 % (name, self.NOT_REGULAR))
        os.unlink(fifo)
        self.held_without_replay(workflow_id, attempts, verification_module.PRIOR_UNRESOLVED,
                                 "owned root %s was never stamped with a process group" % name,
                                 passes=1)
        self.assertEqual(self.invocations(), 0)
        self.assertEqual(self.credential_bytes(scope), credential)
        self.assertIsNone(self.record(workflow_id)["workspace_lease"]["released_at"])

    def test_R26_2j_a_FIFO_after_the_childs_stamp_reports_a_STARTED_run_never_replayed(self):
        """The verification child stamps its root and runs the actual command
        ONCE; then, before the PARENT's confirming stamp, the group record
        becomes a FIFO (the child's own record set aside). The pass RETURNS: the
        parent's stamp refuses at once, and the attempt settles START-UNKNOWN.
        Next passes: held on the UNAVAILABLE record, no replay. Restored (the
        child's record put back; its group gone): the next pass RESOLVES the
        attempt from the evidence — an owned root was created, so a process
        STARTED, its outcome unknown — and blocks it as never replayable. The
        command ran exactly once throughout."""
        planted = []
        self.addCleanup(self.unplant, planted)                    # after settle_scope
        mission_id, workflow_id = self.counting_ready()
        fifos, swapped = [], []
        real_group = process_ownership.record_owned_group

        def group_then_substitute(pgid, label, directory=None, nonce=None):
            result = real_group(pgid, label, directory, nonce=nonce)
            self.wait_for(lambda: self.invocations() == 1, "the command never ran")
            record = os.path.join(process_ownership.owned_root_base(directory), nonce,
                                  process_ownership.OWNED_ROOT_PGID_FILE)
            aside = os.path.join(self.base, "child-record")
            os.rename(record, aside)
            os.mkfifo(record, 0o600)
            self.plant(planted, record)
            fifos.append(record)
            swapped.append(aside)
            return result
        capture, captured = self.capturing()
        before = self.attempts(workflow_id)
        with mock.patch.object(process_ownership, "record_owned_group", group_then_substitute), \
                capture, self.seams() as calls, self.no_reap():
            outcome = self.bounded(self.pass_outcome, fifos, workflow_id)
        unconfirmed = self.the_unconfirmed(captured)              # the spawn boundary, FIRST
        [fifo], [aside] = fifos, swapped
        self.assertEqual(calls, {"produce": 1, "spawn": 1})
        attempts = self.start_unknown(workflow_id, before, outcome)
        self.assertEqual(self.exit_of(unconfirmed.process), 0)
        self.assertEqual(self.invocations(), 1)                   # it RAN, exactly once
        self.assertTrue(stat_is_fifo(fifo))
        self.nothing_written(fifo)
        with open(aside, encoding="utf-8") as handle:
            self.assertEqual(handle.read(), str(unconfirmed.process.pid))
        name = os.path.basename(os.path.dirname(fifo))
        self.held_without_replay(workflow_id, attempts, verification_module.PRIOR_UNAVAILABLE,
                                 "owned root %s's group record cannot be read (%s)"
                                 % (name, self.NOT_REGULAR))
        os.unlink(fifo)
        os.rename(aside, fifo)                                    # the child's own record
        resolved = "%s 1 %s: %s — resolved: an owned root was created for it (0 before it, 1" \
            " now), so a process started; 1 owned root(s), every recorded group gone; its" \
            " outcome is unknown" % (self.ATTEMPT, broker_module.VERIFICATION_ATTEMPT_SETTLED,
                                     broker_module.VERIFICATION_ATTEMPT_OUTCOME_UNKNOWN)
        for _ in range(2):
            with self.seams() as calls, self.no_reap():
                outcome = self.bounded(self.pass_outcome, [], workflow_id)
            self.assertEqual(calls, {"produce": 0, "spawn": 0})
            self.assertEqual((outcome.outcome, outcome.problem, outcome.detail), (
                "delivery_blocked", broker_module.PROBLEM_VERIFICATION_NOT_REPLAYABLE,
                "verification attempt(s) 1 (%s) ran or may have run in the lease and their"
                " result is not recorded; they are never replayed, and no further attempt"
                " starts" % broker_module.VERIFICATION_ATTEMPT_OUTCOME_UNKNOWN))
            self.assertEqual(self.attempts(workflow_id), attempts + [resolved])
        self.assertEqual(self.invocations(), 1)                   # never replayed

    def test_R26_2k_a_STARTED_child_still_running_is_held_then_settled_by_recovery(self):
        """The production composition at ``verification.produce``: the
        verification child stamps its root, runs the actual command ONCE and is
        STILL RUNNING when, before the PARENT's confirming stamp, the group
        record becomes a FIFO. The pass RETURNS start-unknown: the producer
        neither waits for nor reaps the started child — observed ALIVE, its
        group never signalled. Held on the UNAVAILABLE record, no replay.
        Restored (the child's own record back): held UNRESOLVED — alive and
        corroborated as this verification's — still never signalled. Startup
        recovery (``recover_inherited_processes``) then attributes the scope
        and REAPS the group; the next pass resolves the attempt from the
        evidence (a process STARTED, its outcome unknown) and blocks it as
        never replayable. The command ran exactly once."""
        planted = []
        self.addCleanup(self.unplant, planted)                    # after settle_scope
        mission_id, workflow_id = self.counting_ready(sleep=120.0)
        fifos, swapped = [], []
        real_group = process_ownership.record_owned_group

        def group_then_substitute(pgid, label, directory=None, nonce=None):
            result = real_group(pgid, label, directory, nonce=nonce)
            self.wait_for(lambda: self.invocations() == 1, "the command never ran")
            record = os.path.join(process_ownership.owned_root_base(directory), nonce,
                                  process_ownership.OWNED_ROOT_PGID_FILE)
            aside = os.path.join(self.base, "child-record")
            os.rename(record, aside)
            os.mkfifo(record, 0o600)
            self.plant(planted, record)
            fifos.append(record)
            swapped.append(aside)
            return result
        capture, captured = self.capturing()
        before = self.attempts(workflow_id)
        with mock.patch.object(process_ownership, "record_owned_group", group_then_substitute), \
                capture, self.seams() as calls, self.no_reap():
            outcome = self.bounded(self.pass_outcome, fifos, workflow_id)
        process = self.the_unconfirmed(captured).process          # the spawn boundary, FIRST
        [fifo], [aside] = fifos, swapped
        self.assertEqual(calls, {"produce": 1, "spawn": 1})
        attempts = self.start_unknown(workflow_id, before, outcome)
        self.assertIsNone(process.poll())                         # STILL RUNNING
        self.assertEqual(len(self.group_members(process.pid)), 1)  # never signalled
        self.assertEqual(self.invocations(), 1)
        self.nothing_written(fifo)
        name = os.path.basename(os.path.dirname(fifo))
        self.held_without_replay(workflow_id, attempts, verification_module.PRIOR_UNAVAILABLE,
                                 "owned root %s's group record cannot be read (%s)"
                                 % (name, self.NOT_REGULAR))
        os.unlink(fifo)
        os.rename(aside, fifo)                                    # the child's own record
        self.held_without_replay(workflow_id, attempts, verification_module.PRIOR_UNRESOLVED,
                                 "group %d of owned root %s is alive and corroborated as this"
                                 " verification's" % (process.pid, name), passes=1)
        self.assertIsNone(process.poll())                         # still never signalled
        owner = (process_ownership.OWNER_TYPE_WORKFLOW,
                 process_ownership.control_digest(self.control_of(workflow_id)),
                 workflow_id, verification_module.VERIFICATION_OWNER_UNIT)
        results, _unattributed = runtime_module.recover_inherited_processes(self.store_dir)
        self.assertEqual([(tuple(identity), recovered) for identity, recovered, *_ in results
                          if identity.owner_id == workflow_id], [(owner, [process.pid])])
        self.assertIsNotNone(self.exit_of(process))               # settled BY RECOVERY
        self.assertEqual(self.group_members(process.pid), [])
        resolved = "%s 1 %s: %s — resolved: an owned root was created for it (0 before it, 1" \
            " now), so a process started; 1 owned root(s), every recorded group gone; its" \
            " outcome is unknown" % (self.ATTEMPT, broker_module.VERIFICATION_ATTEMPT_SETTLED,
                                     broker_module.VERIFICATION_ATTEMPT_OUTCOME_UNKNOWN)
        with self.seams() as calls, self.no_reap():
            outcome = self.bounded(self.pass_outcome, [], workflow_id)
        self.assertEqual(calls, {"produce": 0, "spawn": 0})
        self.assertEqual((outcome.outcome, outcome.problem), (
            "delivery_blocked", broker_module.PROBLEM_VERIFICATION_NOT_REPLAYABLE))
        self.assertEqual(self.attempts(workflow_id), attempts + [resolved])
        self.assertEqual(self.invocations(), 1)                   # never replayed

    # -- Task 8 R27-1: the PARENT's confirmation FAILS after the verification child's stamp --
    #
    # 2k's production composition at ``verification.produce``, with the PARENT's
    # confirming stamp of the started child's root FAULTED inside the writer
    # (``stamp_faults``: this process's writer only; the child stamps itself with its
    # own interpreter). The child has stamped its root, run the actual command ONCE,
    # and is still running. Asserted, each separately: the spawn boundary FIRST; the
    # child's records as they stand; every pass HELD on an UNRESOLVED ownership, never
    # "clear" — no producer, spawn or reap, the attempts, the lease, the credential,
    # the scope and the root all RETAINED, the group never signalled; then startup
    # recovery settles exactly that group, and the attempt resolves once as never
    # replayable. The command ran exactly once.

    def faulted_confirmation_pass(self, workflow_id, faults):
        """The pass, with the PARENT's confirmation of the started child's root
        run with ``faults`` armed, once the child's own stamp is done. Returns
        ``(unconfirmed, root, attempts)``.

        A faulted confirmation must RAISE. One that RETURNS raises an
        ``AssertionError`` in its place, inside the pass — so the spawn does
        not return a process the producer would then WAIT for (the child runs
        long); the case fails at the spawn boundary, the pass returns, and the
        booked child is settled at cleanup as every other."""
        real_group = process_ownership.record_owned_group
        real_confirm, roots = process_ownership.record_owned_root_group, []

        def group_then_wait(pgid, label, directory=None, nonce=None):
            result = real_group(pgid, label, directory, nonce=nonce)
            self.wait_for(lambda: self.invocations() == 1, "the command never ran")
            return result

        def faulted_confirm(root, pgid):
            roots.append(root)
            with faults.active():
                result = real_confirm(root, pgid)
            raise AssertionError("the faulted parent confirmation RETURNED (%r)" % (result,))
        capture, captured = self.capturing()
        before = self.attempts(workflow_id)
        with mock.patch.object(process_ownership, "record_owned_group", group_then_wait), \
                mock.patch.object(process_ownership, "record_owned_root_group",
                                  faulted_confirm), \
                capture, self.seams() as calls, self.no_reap():
            outcome = self.bounded(self.pass_outcome, [], workflow_id)
        unconfirmed = self.the_unconfirmed(captured)              # the spawn boundary, FIRST
        self.assertEqual(calls, {"produce": 1, "spawn": 1})
        [root] = roots
        self.assertEqual(unconfirmed.root, root)
        attempts = self.start_unknown(workflow_id, before, outcome)
        return unconfirmed, root, attempts

    def child_records(self, root, process):
        """The started child's records as they stand: its group record names
        its pid, its START its live start time; no replacement remains."""
        with open(os.path.join(root, process_ownership.OWNED_ROOT_PGID_FILE),
                  encoding="utf-8") as handle:
            self.assertEqual(handle.read(), str(process.pid))
        with open(os.path.join(root, process_ownership.OWNED_ROOT_START_FILE),
                  encoding="utf-8") as handle:
            self.assertEqual(handle.read(), process_ownership.leader_start_time(process.pid))
        self.assertEqual(stamp_faults.replacements_in(root), [])

    def held_then_settled_without_replay(self, workflow_id, process, root, attempts):
        """Held UNRESOLVED — never clear — with every linkage RETAINED; then
        recovery settles exactly that group, and the attempt resolves ONCE as
        never replayable. The command ran exactly once."""
        scope, name = self.scope(workflow_id), os.path.basename(root)
        credential = self.credential_bytes(scope)
        self.assertIsNone(process.poll())                         # STILL RUNNING
        self.assertEqual(len(self.group_members(process.pid)), 1)  # never signalled
        self.held_without_replay(workflow_id, attempts, verification_module.PRIOR_UNRESOLVED,
                                 "group %d of owned root %s is alive and corroborated as this"
                                 " verification's" % (process.pid, name))
        self.assertIsNone(process.poll())                         # still never signalled
        for path in (scope, root):
            self.assertTrue(os.path.isdir(path), path)            # RETAINED
        self.assertEqual(self.credential_bytes(scope), credential)
        self.assertIsNone(self.record(workflow_id)["workspace_lease"]["released_at"])
        owner = (process_ownership.OWNER_TYPE_WORKFLOW,
                 process_ownership.control_digest(self.control_of(workflow_id)),
                 workflow_id, verification_module.VERIFICATION_OWNER_UNIT)
        results, _unattributed = runtime_module.recover_inherited_processes(self.store_dir)
        self.assertEqual([(tuple(identity), recovered) for identity, recovered, *_ in results
                          if identity.owner_id == workflow_id], [(owner, [process.pid])])
        self.assertIsNotNone(self.exit_of(process))               # settled BY RECOVERY
        self.assertEqual(self.group_members(process.pid), [])
        resolved = "%s 1 %s: %s — resolved: an owned root was created for it (0 before it, 1" \
            " now), so a process started; 1 owned root(s), every recorded group gone; its" \
            " outcome is unknown" % (self.ATTEMPT, broker_module.VERIFICATION_ATTEMPT_SETTLED,
                                     broker_module.VERIFICATION_ATTEMPT_OUTCOME_UNKNOWN)
        for _ in range(2):
            with self.seams() as calls, self.no_reap():
                outcome = self.bounded(self.pass_outcome, [], workflow_id)
            self.assertEqual(calls, {"produce": 0, "spawn": 0})
            self.assertEqual((outcome.outcome, outcome.problem), (
                "delivery_blocked", broker_module.PROBLEM_VERIFICATION_NOT_REPLAYABLE))
            self.assertEqual(self.attempts(workflow_id), attempts + [resolved])
        self.assertEqual(self.invocations(), 1)                   # never replayed

    def test_R27_2a_a_SHORT_parent_write_of_the_GROUP_record_is_held_never_clear(self):
        """The parent's group-record replacement takes a SHORT write — the
        first digits of the pid, a valid-looking DIFFERENT group — and an I/O
        failure. ``SpawnUnconfirmed`` (the failure chained); the child's group
        record is exactly its pid; held UNRESOLVED, every linkage retained;
        recovery settles it, the attempt resolves once, never replayed."""
        mission_id, workflow_id = self.counting_ready(sleep=120.0)
        unconfirmed, root, attempts = self.faulted_confirmation_pass(
            workflow_id, stamp_faults.StampFaults().short_write(
                process_ownership.OWNED_ROOT_PGID_FILE, -2))
        self.assertIsInstance(unconfirmed.__cause__, OSError)
        self.assertEqual(unconfirmed.__cause__.errno, errno.EIO)
        self.child_records(root, unconfirmed.process)
        self.held_then_settled_without_replay(workflow_id, unconfirmed.process, root, attempts)

    def test_R27_2b_a_SHORT_parent_write_of_the_START_record_is_held_never_clear(self):
        """The parent's START replacement takes a SHORT write — a cut start
        time — and an I/O failure (the group record is never reached).
        ``SpawnUnconfirmed``; both of the child's records stand; held
        UNRESOLVED, every linkage retained; recovery settles it, the attempt
        resolves once, never replayed."""
        mission_id, workflow_id = self.counting_ready(sleep=120.0)
        unconfirmed, root, attempts = self.faulted_confirmation_pass(
            workflow_id, stamp_faults.StampFaults().short_write(
                process_ownership.OWNED_ROOT_START_FILE, 12))
        self.assertIsInstance(unconfirmed.__cause__, OSError)
        self.assertEqual(unconfirmed.__cause__.errno, errno.EIO)
        self.child_records(root, unconfirmed.process)
        self.held_then_settled_without_replay(workflow_id, unconfirmed.process, root, attempts)

    def test_R27_2c_a_PUBLISHED_but_UNPROVEN_group_record_is_held_never_undone(self):
        """The parent's group-record replacement is PUBLISHED and the
        directory's ``fsync`` then fails: ``SpawnUnconfirmed`` with
        ``PublicationUnproven`` chained — published, never rolled back: the
        record is the child's pid, valid. Held UNRESOLVED, every linkage
        retained; recovery settles it, the attempt resolves once, never
        replayed."""
        from target_runtime import spawn_stamp
        mission_id, workflow_id = self.counting_ready(sleep=120.0)
        unconfirmed, root, attempts = self.faulted_confirmation_pass(
            workflow_id, stamp_faults.StampFaults().failed_directory_fsync(after=1))
        self.assertIsInstance(unconfirmed.__cause__, spawn_stamp.PublicationUnproven)
        self.assertEqual(unconfirmed.__cause__.errno, errno.EIO)
        self.child_records(root, unconfirmed.process)
        self.held_then_settled_without_replay(workflow_id, unconfirmed.process, root, attempts)

    def test_R27_2d_a_LIVE_root_under_an_UNOBSERVED_owner_ledger_is_held_then_settled_once(
            self):
        """2c's start-unknown run — the child's root VALID, its group LIVE and
        corroborated — while the verification scope's owner ledger is PRESENT
        but cannot be observed (a FIFO stands in its place). Never fail-open:
        - every pass is HELD ``PRIOR_UNAVAILABLE`` on the ledger — no producer,
          no spawn, no reap; the attempts, the lease, the credential, the scope
          and the root all RETAINED;
        - startup recovery REPORTS the ledger unavailable and acts on NOTHING —
          zero reaps; the child still running, never signalled.
        The ledger then READS: held UNRESOLVED (alive and corroborated), then
        recovery settles exactly that group ONCE, the attempt resolves ONCE as
        never replayable, and the command ran exactly once."""
        mission_id, workflow_id = self.counting_ready(sleep=120.0)
        unconfirmed, root, attempts = self.faulted_confirmation_pass(
            workflow_id, stamp_faults.StampFaults().failed_directory_fsync(after=1))
        process = unconfirmed.process
        scope, name = self.scope(workflow_id), os.path.basename(root)
        ledger = process_ownership.ledger_path(scope)
        planted, aside = [], os.path.join(self.base, "owner-ledger-aside")
        self.addCleanup(self.unplant, planted)                   # after settle_scope
        os.rename(ledger, aside)
        os.mkfifo(ledger, 0o600)
        self.plant(planted, ledger)
        gap = (ledger, "%s: the owner ledger cannot be read (%s)" % (
            process_ownership.OBSERVATION_UNAVAILABLE, self.NOT_REGULAR))
        credential = self.credential_bytes(scope)
        self.held_without_replay(workflow_id, attempts, verification_module.PRIOR_UNAVAILABLE,
                                 "owned root %s cannot be checked against its owner ledger (%s)"
                                 % (name, gap[1]))
        with self.no_reap():                                      # ZERO reaps
            report = runtime_module.recover_inherited_processes(self.store_dir)
        self.assertIn(gap, report.unavailable)
        self.assertEqual([identity for identity, *_rest in report[0]
                          if identity.owner_id == workflow_id], [])
        self.assertIsNone(process.poll())                         # still running
        self.assertEqual(len(self.group_members(process.pid)), 1)  # never signalled
        for path in (scope, root):
            self.assertTrue(os.path.isdir(path), path)            # RETAINED
        self.assertEqual(self.credential_bytes(scope), credential)
        self.assertIsNone(self.record(workflow_id)["workspace_lease"]["released_at"])
        self.assertEqual(self.attempts(workflow_id), attempts)
        os.unlink(ledger)
        os.rename(aside, ledger)                                  # the ledger READS again
        self.held_then_settled_without_replay(workflow_id, process, root, attempts)

    def test_R27_2e_a_ledger_SUBSTITUTED_after_the_commands_wait_RETURNS_UNSETTLED_never_signals(
            self):
        """``verification.run`` reaps its command's group right after the
        command's WAIT, through ``reap_owned``, whose gate READS the
        verification scope's owner ledger (``owned_groups``). The command
        appends its marker byte, leaves a same-group DESCENDANT running and
        exits; right after the wait returns, a FIFO is SUBSTITUTED for the
        ledger (the real one moved aside). The pass RETURNS — it never waits:
        - the verification is recorded with its exit status and its process
          UNSETTLED, and the attempt says so — never "settled";
        - ZERO signals during the pass, counted at their calls; the
          descendant is still running; the command ran exactly once.
        The ledger then READS (put back): the same ledger-gated reap proceeds
        EXACTLY ONCE — one SIGKILL, to exactly that group, while its
        descendant still holds the group id — and the group is gone; a later
        pass runs no producer and no spawn, and the command is never re-run."""
        import signal as signal_module
        marker = os.path.join(self.base, "r27-2e-marker")
        mission_id, workflow_id = self.ready(argv=[
            sys.executable, "-c",
            "import os, subprocess, sys; descriptor = os.open(sys.argv[1], os.O_WRONLY"
            " | os.O_CREAT | os.O_APPEND, 0o600); os.write(descriptor, b'x');"
            " os.close(descriptor); subprocess.Popen(['sleep', '30'])", marker])
        scope = self.scope(workflow_id)
        ledger = process_ownership.ledger_path(scope)
        planted, aside = [], os.path.join(self.base, "r27-2e-owner-ledger-aside")
        groups = []
        self.addCleanup(self.unplant, planted)

        def settle_descendant():
            """FIXTURE settlement of this case's OWN recorded group, however
            the case ended: the ledger put back first; then the production
            ledger-gated reaper, ONLY while a member is freshly observed (the
            group id is then still held by this case's descendant)."""
            if os.path.lexists(aside) and stat_is_fifo(ledger):
                os.unlink(ledger)
                os.rename(aside, ledger)
            if groups and self.group_members(groups[0]):
                process_ownership.reap_owned(groups[0], directory=scope, settle_seconds=5.0)
        self.addCleanup(settle_descendant)                # runs BEFORE unplant

        def substitute_after_wait(process):
            real_wait = process.wait

            def wait(*args, **kwargs):
                status = real_wait(*args, **kwargs)
                if not planted:                           # once: right after the command's wait
                    groups.append(process.pid)            # a session leader: its pid, its group
                    os.rename(ledger, aside)
                    os.mkfifo(ledger, 0o600)
                    self.plant(planted, ledger)
                return status
            process.wait = wait
        sent, real_killpg, real_kill = [], os.killpg, os.kill

        def killpg(pgid, sig):
            if sig != 0:
                sent.append(("killpg", pgid, sig))
            return real_killpg(pgid, sig)

        def kill(pid, sig):
            if sig != 0:
                sent.append(("kill", pid, sig))
            return real_kill(pid, sig)
        def guarded(*patches):
            # entered now; UNDONE by a GUARDED cleanup — never beneath a route
            # thread not observed terminated — or by the case once each ended
            stack = contextlib.ExitStack()
            self.addCleanup(stack.close)
            return stack, [stack.enter_context(patch) for patch in patches]
        patched, (calls, _killpg, _kill) = guarded(
            self.seams(on_spawn=substitute_after_wait), mock.patch.object(os, "killpg", killpg),
            mock.patch.object(os, "kill", kill))
        self.bounded(self.pass_outcome, [ledger], workflow_id)
        patched.close()                                   # the route observed ended
        self.assertEqual(calls, {"produce": 1, "spawn": 1})
        self.assertEqual(len(planted), 1, "the ledger was never substituted after the wait")
        [pgid] = groups
        receipts = self.verification_receipts(workflow_id)
        self.assertEqual(len(receipts), 1)
        record = artifacts.load_verification(artifacts.artifact_directory(self.store_dir),
                                             receipts[0]["digest"])
        self.assertEqual((record["exit_status"], record["settlement"]),
                         (0, artifacts.SETTLEMENT_UNSETTLED))   # its process NEVER "settled"
        claim, settlement = self.attempts(workflow_id)
        self.assertEqual(settlement, "%s 1 settled: returned — record %s (its process %s)"
                         % (self.ATTEMPT, receipts[0]["digest"], artifacts.SETTLEMENT_UNSETTLED))
        self.assertEqual(sent, [])                        # ZERO signals
        self.assertEqual(len(self.members_after(pgid, 1)), 1)    # the descendant still runs
        with open(marker, "rb") as handle:
            self.assertEqual(handle.read(), b"x")        # the command ran ONCE
        os.unlink(ledger)
        os.rename(aside, ledger)                          # the ledger READS again
        self.assertEqual(len(self.group_members(pgid)), 1)      # still held by the descendant
        sent[:] = []
        patched, _patches = guarded(mock.patch.object(os, "killpg", killpg),
                                    mock.patch.object(os, "kill", kill))
        reaped = self.bounded(process_ownership.reap_owned, [], pgid, directory=scope,
                              settle_seconds=10.0)
        patched.close()
        self.assertEqual(reaped, (process_ownership.REAPED, None))
        self.assertEqual(sent, [("killpg", pgid, signal_module.SIGKILL)])   # EXACTLY one
        self.assertEqual(self.members_after(pgid, 0), [])
        patched, (calls,) = guarded(self.seams())
        self.bounded(self.pass_outcome, [], workflow_id)
        patched.close()
        self.assertEqual(calls, {"produce": 0, "spawn": 0})      # never re-run
        with open(marker, "rb") as handle:
            self.assertEqual(handle.read(), b"x")


def stat_is_fifo(path):
    """Whether ``path`` is (still) a FIFO, by ``lstat`` — never opened."""
    import stat
    return stat.S_ISFIFO(os.lstat(path).st_mode)


class R26BoundedRouteFixtureTests(unittest.TestCase):
    """Task 8 R26-1: the TIMEOUT fixture of ``R26NonWaitingRouteTests``
    (``bounded``, ``guarded_cleanup``). One case of that class runs on its
    own, with a minimal ``setUp`` that registers two recording cleanups, an
    INHERITED ``tearDown`` that records itself (below the whole
    DeliveryCase chain), and a route that RETURNS (the ``tearDown``, then
    every cleanup, last registered first), WAITS on its own FIFO (released
    and OBSERVED terminated before the ``tearDown`` or any cleanup), or
    WAITS on what no FIFO release can end (the ``tearDown`` and EVERY
    cleanup WITHHELD, none run, each failing the case by name; this test's
    own event then ends the thread, observed terminated). And the OWNED
    CHILDREN (2h2, 2h3): a child never observed ended withholds every
    destructive cleanup while a SAFE step still settles another; a collected
    child is never signalled again; a child whose spawn result is dropped is
    still settled through its booking."""

    CASE = R26NonWaitingRouteTests
    AFTER = ["inherited tearDown", "second registered", "first registered"]

    def case(self, route, ran):
        class Inherited(unittest.TestCase):
            def tearDown(inner):
                ran.append("inherited tearDown")

        class Case(self.CASE, Inherited):
            BOUND = 0.2
            RELEASE_PASSES = 2

            def setUp(inner):
                inner.addCleanup(ran.append, "first registered")
                inner.addCleanup(ran.append, "second registered")

            def test_route(inner):
                route(inner)
        case, result = Case("test_route"), unittest.TestResult()
        case.run(result)
        return case, result

    def test_R26_2f_a_route_that_returns_runs_every_cleanup(self):
        ran = []
        _case, result = self.case(
            lambda inner: inner.assertEqual(inner.bounded(lambda: 7, []), 7), ran)
        self.assertEqual((result.failures, result.errors), ([], []))
        self.assertEqual(ran, self.AFTER)

    def test_R26_2g_a_released_route_is_observed_terminated_before_any_cleanup(self):
        import tempfile
        holding = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, holding, True)
        fifo = os.path.join(holding, "fifo")
        os.mkfifo(fifo, 0o600)
        ran, alive = [], []

        def read():
            with open(fifo, "rb") as handle:              # waits: no writer
                return handle.read()

        def route(inner):
            try:
                inner.bounded(read, [fifo])
            finally:
                alive.extend(thread.is_alive() for thread, _fifos in inner.routes)
        _case, result = self.case(route, ran)
        try:
            texts = [text for _failed, text in result.failures]
            self.assertEqual(len(texts), 1, texts)
            self.assertIn("WAITED", texts[0])
            self.assertIn("was then observed terminated", texts[0])
            self.assertEqual(alive, [False])              # ended BEFORE any teardown
            self.assertEqual(result.errors, [])
            self.assertEqual(ran, self.AFTER)
        finally:
            self.assertEqual(self.ended("bounded-route-read", fifo), [])   # none outlives it

    @staticmethod
    def ended(name, fifo=None):
        """This test's OWN release, independent of the fixture code under
        test: while a thread named ``name`` lives, ``fifo`` (when given) is
        opened for writing and for reading, each without waiting, and closed,
        and the thread is joined again, at most 30 times. Returns the names
        still alive."""
        import threading
        for _ in range(30):
            live = [thread for thread in threading.enumerate() if thread.name == name]
            if not live:
                break
            if fifo is not None:
                for mode in (os.O_WRONLY, os.O_RDONLY):
                    try:
                        os.close(os.open(fifo, mode | os.O_NONBLOCK))
                    except OSError:
                        pass
            for thread in live:
                thread.join(1.0)
        return [thread.name for thread in threading.enumerate() if thread.name == name]

    def test_R26_2g2_a_released_WRITER_route_is_observed_terminated_before_any_cleanup(self):
        """A route WAITING to WRITE its own FIFO — as the pre-correction stamp
        writer did, inside ``open`` — is released by the fixture's read-side
        pulse, OBSERVED terminated, and only then do the ``tearDown`` and the
        cleanups run; the case fails (it waited)."""
        import tempfile
        holding = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, holding, True)
        fifo = os.path.join(holding, "fifo")
        os.mkfifo(fifo, 0o600)
        ran, alive = [], []

        def write():
            with open(fifo, "wb") as handle:             # waits: no reader
                handle.write(b"x")

        def route(inner):
            try:
                inner.bounded(write, [fifo])
            finally:
                alive.extend(thread.is_alive() for thread, _fifos in inner.routes)
        _case, result = self.case(route, ran)
        try:
            texts = [text for _failed, text in result.failures]
            self.assertEqual(len(texts), 1, texts)
            self.assertIn("WAITED", texts[0])
            self.assertIn("was then observed terminated", texts[0])
            self.assertEqual(alive, [False])              # ended BEFORE any teardown
            self.assertEqual(result.errors, [])
            self.assertEqual(ran, self.AFTER)
        finally:
            self.assertEqual(self.ended("bounded-route-write", fifo), [])   # none outlives it

    def test_R26_2h_a_route_never_observed_terminated_WITHHOLDS_every_cleanup(self):
        import threading
        never = threading.Event()
        ran = []
        case, result = self.case(lambda inner: inner.bounded(never.wait, []), ran)
        try:
            self.assertEqual(ran, [])                     # NOTHING ran beneath it
            texts = [text for _failed, text in result.failures]
            self.assertEqual(len(texts), 4, texts)        # the wait, tearDown, 2 cleanups
            self.assertIn("STILL ALIVE: every cleanup is withheld", texts[0])
            self.assertIn("tearDown WITHHELD: not observed ended — route thread"
                          " bounded-route-wait", texts[1])
            self.assertIn("cleanup WITHHELD (append('second registered',))", texts[2])
            self.assertIn("cleanup WITHHELD (append('first registered',))", texts[3])
            self.assertEqual(result.errors, [])
            self.assertEqual(case.__dict__.get("withheld"), ["tearDown", "append", "append"])
        finally:
            never.set()                                   # this test's own event
            self.assertEqual(self.ended("bounded-route-wait"), [])   # observed terminated

    # -- Task 8 R27 (the Lead's §9.2): a WITHHELD cleanup KEEPS the case's base ----------

    def case_with_base(self, route, ran):
        """``case``, with the base made as ``RuntimeCase.setUp`` makes it: a
        ``tempfile.TemporaryDirectory`` held as ``tmp``, its ``cleanup``
        registered FIRST (so it runs LAST). Returns ``(case, result, base)``."""
        import tempfile

        class Inherited(unittest.TestCase):
            def tearDown(inner):
                ran.append("inherited tearDown")

        class Case(self.CASE, Inherited):
            BOUND = 0.2
            RELEASE_PASSES = 2

            def setUp(inner):
                inner.tmp = tempfile.TemporaryDirectory()
                inner.base = inner.tmp.name
                inner.addCleanup(inner.tmp.cleanup)
                inner.addCleanup(ran.append, "first registered")
                inner.addCleanup(ran.append, "second registered")

            def test_route(inner):
                route(inner)
        case, result = Case("test_route"), unittest.TestResult()
        case.run(result)
        return case, result, case.base

    def test_R27_2w_a_WITHHELD_base_is_never_deleted_implicitly(self):
        """A route never observed terminated: the ``tearDown`` and EVERY
        cleanup are WITHHELD — the base's own ``cleanup`` among them — and the
        base is KEPT. Its ``TemporaryDirectory`` finalizer is DETACHED, so
        neither garbage collection nor interpreter exit deletes it: invoked
        now, the finalizer does nothing, and the base still stands. Each
        withheld message says the base is RETAINED. (This test's own cleanup
        removes the base afterwards — this test's own artifact.)"""
        import threading
        never = threading.Event()
        ran = []
        case, result, base = self.case_with_base(lambda inner: inner.bounded(never.wait, []),
                                                 ran)
        self.addCleanup(shutil.rmtree, base, True)
        try:
            self.assertEqual(ran, [])                     # NOTHING ran beneath it
            texts = [text for _failed, text in result.failures]
            self.assertEqual(len(texts), 5, texts)        # the wait, tearDown, 3 cleanups
            self.assertIn("cleanup WITHHELD (cleanup())", texts[4])
            for text in texts[1:]:
                self.assertIn("(base %s retained)" % base, text)
            self.assertFalse(case.tmp._finalizer.alive)   # DETACHED: no implicit deletion
            case.tmp._finalizer()                         # as exit or collection would
            self.assertTrue(os.path.isdir(base), "a WITHHELD base was deleted implicitly")
        finally:
            never.set()                                   # this test's own event
            self.assertEqual(self.ended("bounded-route-wait"), [])   # observed terminated

    def test_R27_2n_a_base_whose_cleanup_RUNS_is_still_deleted(self):
        """A route that RETURNS: nothing is withheld, every cleanup runs — the
        base's own ``cleanup`` last — and the base IS deleted, exactly as
        before; the finalizer is never detached by the fixture."""
        ran = []
        case, result, base = self.case_with_base(
            lambda inner: inner.assertEqual(inner.bounded(lambda: 7, []), 7), ran)
        self.addCleanup(shutil.rmtree, base, True)        # only if the case left it
        self.assertEqual((result.failures, result.errors), ([], []))
        self.assertEqual(ran, self.AFTER)
        self.assertIsNone(case.__dict__.get("withheld"))
        self.assertFalse(os.path.exists(base), "a base whose cleanup ran was kept")

    def outer_settlement(self, case, killed, children):
        """This test's OWN outer settlement on its FAILING path, where its
        settlement assertions never run (an earlier assertion already failed;
        the ``finally`` that calls this is what reaps). RECORDED, NEVER RAISED —
        a raise here would replace that first failure. For each ``(name,
        process)``: whether this test signalled it, its handle's return code
        (possibly FABRICATED, never read as its exit status), and whether it is
        OBSERVED ENDED — no process holds its pid (``pid_absent``) and its group
        answers signal 0 with the observed-absent answer. Written to stderr as
        UNPROVEN by assertion: an observation, never a passed check."""
        try:
            readings = []
            for name, process in children:
                try:
                    ended = (case.pid_absent(process.pid)
                             and process_ownership._group_alive(process.pid) is False)
                    state = "observed ENDED" if ended else "NOT observed ended"
                except Exception as exc:                     # noqa: BLE001 - recorded
                    state = "UNREADABLE (%s)" % exc.__class__.__name__
                readings.append("%s %d: signalled by this test %s, return code %r, %s" % (
                    name, process.pid, "yes" if process.pid in killed else "no",
                    process.returncode, state))
            text = "; ".join(readings)
        except Exception as exc:                             # noqa: BLE001 - recorded
            text = "the observation itself failed (%s)" % exc.__class__.__name__
        sys.stderr.write("R26 fixture: OUTER SETTLEMENT of %s on its FAILING path: its"
                         " settlement assertions did NOT run (UNPROVEN by assertion);"
                         " recorded in its finally, never raised: %s\n"
                         % (self._testMethodName, text))

    def test_R26_2h2_an_owned_child_never_observed_ended_WITHHOLDS_every_cleanup(self):
        """An owned CHILD is the same bar as a route thread for every
        DESTRUCTIVE cleanup. A case BOOKS two children in two scopes, each
        scope's settlement registered (a SAFE step): the SECOND scope's child
        keeps running and its signal is REFUSED (a controlled adapter that
        records it), so it is never observed ended — its settlement FAILS
        naming the child, the signal attempted while pinned and what is
        retained; the FIRST scope's settlement still runs and settles its child
        (signalled while pinned, collected through its own handle with its
        TRUE status, observed gone); and every DESTRUCTIVE cleanup is WITHHELD
        — none runs. Failures, never errors. This test's own mechanism then
        signals its own group while it is still UNCOLLECTED (pinned), collects
        it through its OWN handle (its true status), and observes it ended and
        its group gone."""
        import tempfile
        holding = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, holding, True)
        made, ran, refused = {}, [], []
        real_kill_group = self.CASE.kill_group

        def route(inner):
            for name in ("settled", "refused"):              # "refused" settles FIRST
                scope = os.path.join(holding, name)
                os.makedirs(scope)
                inner.settle_at_cleanup(scope)
                with inner.booking(inner.owned_children):
                    child = process_ownership.spawn_owned(
                        [sys.executable, "-c", inner.WRITER_ARGV_CODE,
                         os.path.join(holding, "marker-" + name), "60"], label="r26-writer",
                        directory=scope, owned_root_base_dir=scope,
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                # this TEST's own record of each child (scope, process, start time),
                # apart from the fixture's booking, for this test's own final reap
                made[name] = (scope, child, inner.start_of(child.pid))

        class Refusing(self.CASE):
            WAIT_SECONDS = 0.5

            @staticmethod
            def kill_group(pgid):
                if "refused" in made and pgid == made["refused"][1].pid:
                    refused.append(pgid)                     # REFUSED: nothing is delivered
                    return None
                return real_kill_group(pgid)

        real_case, self.CASE = self.CASE, Refusing
        try:
            case, result = self.case(route, ran)
        finally:
            self.CASE = real_case
        (scope, process, _start), (_scope, settled, _started) = made["refused"], made["settled"]
        killed, completed = [], []
        try:
            texts = [text for _failed, text in result.failures]
            self.assertEqual(len(texts), 3, texts)       # the settlement, two cleanups
            self.assertIn("owned process(es) NOT observed ended: child %d (running after its"
                          " wait; SIGKILL attempted on its group while pinned)" % process.pid,
                          texts[0])
            self.assertEqual(refused, [process.pid])          # its signal: REFUSED here
            self.assertIn("retained: the scope %s, its ledger %s, its owned roots"
                          % (scope, process_ownership.ledger_path(scope)), texts[0])
            self.assertIn("cleanup WITHHELD (append('second registered',)): not observed"
                          " ended — child %d" % process.pid, texts[1])
            self.assertIn("cleanup WITHHELD (append('first registered',))", texts[2])
            self.assertEqual(result.errors, [])
            self.assertEqual(ran, ["inherited tearDown"])     # NO destructive cleanup ran
            self.assertEqual(case.__dict__.get("withheld"), ["append", "append"])
            self.assertEqual(settled.returncode, -signal.SIGKILL)   # the SAFE step ran:
            self.assertTrue(case.pid_absent(settled.pid))     # its TRUE status, observed ended,
            self.assertFalse(process_ownership._group_alive(settled.pid))   # observed gone
            completed.append(True)                            # every assertion above HELD
        finally:
            for _owned_scope, child, start in (made["refused"], made["settled"]):
                if case.pinned(child, start)[0] == "pinned":  # still its UNCOLLECTED child
                    real_kill_group(child.pid)
                    child.wait(timeout=10)                    # through its OWN handle
                    killed.append(child.pid)
            if not completed:                                 # RECORDED, never raised
                self.outer_settlement(case, killed, (("refused", process), ("settled", settled)))
        self.assertEqual(killed, [process.pid])               # the refused one, by this test
        self.assertEqual(process.returncode, -signal.SIGKILL)   # its TRUE status
        self.assertTrue(case.pid_absent(process.pid))           # observed ended,
        self.assertFalse(process_ownership._group_alive(process.pid))   # observed gone

    def test_R26_2h3_a_COLLECTED_child_is_never_signalled_and_a_DROPPED_carrier_is_still_settled(
            self):
        """FIXTURE BOOKKEEPING decides the cleanup, never a bare id. A case BOOKS
        two children in one scope whose settlement it registers: one it
        COLLECTS itself before cleanup, and one whose spawn result it DROPS —
        as a mutant that discards ``SpawnUnconfirmed``'s process carrier would.
        The settlement observes both ended — the dropped one through its
        booking — and sends the collected one NO signal but signal 0: every
        ``os.kill`` / ``os.killpg`` aimed at its pid is RECORDED here and only
        signal 0 is delivered to it. Nothing is withheld."""
        import tempfile
        holding = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, holding, True)
        children, watched, signals, ran = [], [], [], []

        def recording(real):
            def send(pid, number):
                if number != 0 and abs(pid) in watched:
                    signals.append((pid, number))            # recorded, never delivered
                    return None
                return real(pid, number)
            return send

        def route(inner):
            scope = os.path.join(holding, "scope")
            os.makedirs(scope)
            inner.settle_at_cleanup(scope)
            argv = [sys.executable, "-c", inner.WRITER_ARGV_CODE, os.path.join(holding, "marker"),
                    "0"]
            with inner.booking(inner.owned_children):
                collected = process_ownership.spawn_owned(
                    argv, label="r26-writer", directory=scope, owned_root_base_dir=scope,
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            collected.wait(timeout=10)                       # COLLECTED by the case itself
            watched.append(collected.pid)
            with inner.booking(inner.owned_children):       # its result DROPPED
                process_ownership.spawn_owned(
                    argv, label="r26-writer", directory=scope, owned_root_base_dir=scope,
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            children.extend(process for process, _started, _root in inner.owned_children)

        with mock.patch.object(os, "kill", recording(os.kill)), \
                mock.patch.object(os, "killpg", recording(os.killpg)):
            case, result = self.case(route, ran)
        self.assertEqual(len(children), 2, children)
        self.assertEqual(signals, [])                        # the COLLECTED one: never signalled
        self.assertEqual((result.failures, result.errors), ([], []))
        self.assertEqual(ran, self.AFTER)                    # nothing withheld
        dropped = children[1]
        self.assertTrue(case.pid_absent(dropped.pid))        # settled through its BOOKING:
        self.assertFalse(process_ownership._group_alive(dropped.pid))   # observed ended, gone


class T4bPreparationTests(DeliveryCase):
    """Required test 4b: the preparation write is explicit and idempotent;
    status and the proposal construction write nothing."""

    def test_T4b1_preparation_is_idempotent(self):
        mission_id, workflow_id = self.prepared()
        state = self.service.get_state(mission_id)
        proposals = [a for a in state["record"]["artifacts"]
                     if a["key"] == delivery_module.ARTIFACT_KEY_PROPOSAL]
        before = self.snapshot()
        for _ in range(2):
            self.assertEqual(self.pass_outcome(workflow_id).outcome,
                             "delivery_awaiting_decision")
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(len(proposals), 1)
        self.assertEqual(len(self.wf_receipts(workflow_id, artifacts.PROPOSAL_TURN_PREFIX)), 1)

    def test_T4b2_status_card_and_construction_write_nothing(self):
        mission_id, workflow_id = self.prepared()
        before = self.snapshot()
        status = self.desk.status(mission_id)
        self.assertEqual(status["next_action"], "decide the proposal with di_delivery_decide")
        self.assertTrue(status["proposal"]["current"])
        card = self.card(mission_id)
        self.assertTrue(card["ok"])
        entry = self.record(workflow_id)
        observation = delivery_module.exact_candidate(entry)
        receipt = self.wf_receipts(workflow_id, artifacts.VERIFICATION_TURN_PREFIX)[0]
        directory = artifacts.artifact_directory(self.store_dir)
        record = artifacts.load_verification(directory, receipt["digest"])
        bindings = delivery_cli.live_bindings(
            self.delivery_transport, self.lease_path(workflow_id), "origin", "main")
        proposal = delivery_module.build_proposal(
            entry, self.service.get(mission_id), bindings, receipt["digest"], record,
            delivery_module.review_approval(entry), self.clock())
        self.assertEqual(proposal["authority_template"]["candidate"]["identity_digest_sha256"],
                         observation["identity"])
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.performed(), self.no_effects())


class T5EffectGateTests(DeliveryCase):
    """Required test 5: cancel, EDIT, expiry, revocation or stale readiness
    landing during the delivery's blocking work stops the NEXT effect."""

    def assert_stopped_after_commit(self, workflow_id, phase):
        self.pass_outcome(workflow_id)
        counts = self.performed()
        self.assertEqual(counts["commit_step"], 1)
        self.assertEqual(counts["push"], 0)
        self.assertEqual(counts["gh_pr_create"], 0)
        self.assertEqual(self.effects(), ([], 0))
        self.assertEqual(self.the_delivery()["phase"], phase)

    def test_T5a_a_cancel_after_the_commit_stops_the_push(self):
        mission_id, workflow_id = self.decided()
        self.hook("commit_step", "after", lambda: self.service.request_cancel(mission_id))
        self.assert_stopped_after_commit(workflow_id, delivery_auth.PHASE_REVOKED)

    def test_T5b_a_hold_after_the_commit_holds_then_resumes(self):
        mission_id, workflow_id = self.decided()
        self.hook("commit_step", "after", lambda: self.service.request_hold(mission_id))
        self.assert_stopped_after_commit(workflow_id, delivery_auth.PHASE_COMMITTED)
        self.service.release_hold(mission_id)
        runtime_module.process_once(self.broker)
        self.assertEqual(self.performed()["push"], 1)
        self.assertEqual(self.effects()[1], 1)
        self.assertEqual(self.the_delivery()["phase"], delivery_auth.PHASE_COMPLETE)

    def test_T5c_an_edit_after_the_commit_stops_the_push(self):
        mission_id, workflow_id = self.decided()
        self.hook("commit_step", "after", lambda: self.edit(mission_id))
        self.assert_stopped_after_commit(workflow_id, delivery_auth.PHASE_REVOKED)

    def test_T5d_stale_readiness_after_the_commit_holds_the_push(self):
        mission_id, workflow_id = self.decided(proof_contract=self.short_readiness())
        self.hook("commit_step", "after", lambda: self.clock.advance(61))
        self.assert_stopped_after_commit(workflow_id, delivery_auth.PHASE_COMMITTED)

    def test_T5e_a_revocation_after_the_commit_is_never_overwritten(self):
        mission_id, workflow_id = self.decided()
        self.hook("commit_step", "after", lambda: self.driver.machine.revoke(
            self.the_delivery()["delivery_id"], "human", "stop"))
        self.assert_stopped_after_commit(workflow_id, delivery_auth.PHASE_REVOKED)
        self.assertEqual(self.the_delivery()["revocation"]["revoked_by"], "human")

    @staticmethod
    def short_readiness():
        from test_mission_engagement import contract
        return contract(required_resource_readiness=[
            {"resource_key": "build_host", "max_age_seconds": 60}])


class MintedCase(DeliveryCase):

    def minted(self):
        """A minted Mission-bound record whose first effect never started
        (the process died just before ``write-tree``, which precedes the
        COMMIT receipt it binds): nothing was performed."""
        mission_id, workflow_id = self.decided()
        self.hook("write_tree", "before", self.crash)
        with self.assertRaises(Crash):
            self.delivery_pass()
        self.delivery_transport.hooks.clear()
        record = self.the_delivery()
        self.assertEqual(record["phase"], delivery_auth.PHASE_BASE_CURRENT)
        self.assertEqual(self.performed(), self.no_effects())
        return mission_id, workflow_id, record


class T6BypassTests(MintedCase):
    """Required test 6: the default CLI and a direct machine cannot drive a
    Mission-bound record; the legacy terminal and guard paths are the
    unchanged P1-A6 suites (tests.test_pr_delivery, test_pr_delivery_guards)."""

    def test_T6_the_default_cli_and_a_bare_machine_change_nothing(self):
        import contextlib
        import io
        from types import SimpleNamespace
        mission_id, workflow_id, record = self.minted()
        before = self.snapshot()
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            delivery_cli.advance_cmd(SimpleNamespace(delivery_id=record["delivery_id"]),
                                     store_dir=self.delivery_dir)
        self.assertEqual(json.loads(out.getvalue())["outcome"],
                         machine_module.OUTCOME_GATE_REQUIRED)
        bare = machine_module.DeliveryMachine(
            delivery_store_module.DeliveryStore(self.delivery_dir),
            self.delivery_transport, lambda: self.clock())
        self.assertEqual(bare.advance_once(record["delivery_id"]),
                         machine_module.OUTCOME_GATE_REQUIRED)
        self.assertEqual(bare.advance(record["delivery_id"]),
                         machine_module.OUTCOME_GATE_REQUIRED)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.performed(), self.no_effects())
        # Through the Runtime's gated driver it completes, exactly once.
        runtime_module.process_once(self.broker)
        self.assertEqual(self.performed()["commit_step"], 1)
        self.assertEqual(self.effects()[1], 1)


class T7OneOwnerTests(MintedCase):
    """Required test 7: one effect owner across Runtime, CLI and revoker; no
    stale overwrite; no lost revocation."""

    def test_T7a_a_second_driver_while_one_drives_performs_nothing(self):
        mission_id, workflow_id, record = self.minted()
        store = delivery_store_module.DeliveryStore(self.delivery_dir)
        before = self.snapshot()
        with store.drive_lock(record["delivery_id"]):
            outcome = self.pass_outcome(workflow_id)
        self.assertTrue(outcome.ok)
        self.assertEqual(outcome.outcome, "delivery_delivering")
        self.assertEqual(self.performed(), self.no_effects())
        self.assertEqual(self.snapshot(), before)
        runtime_module.process_once(self.broker)
        self.assertEqual(self.the_delivery()["phase"], delivery_auth.PHASE_COMPLETE)

    def test_T7b_a_revocation_during_the_drive_is_kept(self):
        mission_id, workflow_id, record = self.minted()
        self.hook("write_tree", "after", lambda: delivery_cli.revoke_cmd(
            _args(delivery_id=record["delivery_id"], reason="human stop"),
            store_dir=self.delivery_dir))
        self.pass_outcome(workflow_id)
        final = self.the_delivery()
        self.assertTrue(final["revocation"]["revoked"])
        self.assertEqual(final["phase"], delivery_auth.PHASE_REVOKED)
        self.assertEqual(self.performed()["commit_step"], 0)
        self.assertEqual(self.effects(), ([], 0))


def _args(**values):
    from types import SimpleNamespace
    return SimpleNamespace(**values)


class T8OwnedChildrenTests(MintedCase):
    """Required test 8: an effect child whose ownership or settlement is not
    proven blocks every retry; proof releases it."""

    def ledger(self, delivery_id):
        return os.path.join(self.delivery_dir, transport_module.CHILD_LEDGER_DIR_NAME,
                            "%s.jsonl" % delivery_id)

    def append(self, delivery_id, row):
        path = self.ledger(delivery_id)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(row) + "\n")

    def test_T8a_an_intent_without_a_group_blocks_until_settled(self):
        mission_id, workflow_id, record = self.minted()
        self.append(record["delivery_id"], {"intent": "chd-orphan", "step": "commit",
                                            "effect": "write_tree", "program": "git"})
        outcome = self.pass_outcome(workflow_id)
        self.assertEqual(outcome.problem, transport_module.CHILD_UNRESOLVED)
        self.assertEqual(self.performed(), self.no_effects())
        self.append(record["delivery_id"], {"nonce": "chd-orphan", "settled": True})
        runtime_module.process_once(self.broker)
        self.assertEqual(self.the_delivery()["phase"], delivery_auth.PHASE_COMPLETE)

    def test_T8b_a_live_group_blocks_and_is_never_signalled(self):
        mission_id, workflow_id, record = self.minted()
        self.append(record["delivery_id"], {"intent": "chd-live", "step": "commit",
                                            "effect": "write_tree", "program": "git"})
        self.append(record["delivery_id"], {"nonce": "chd-live", "pgid": os.getpgrp()})
        outcome = self.pass_outcome(workflow_id)
        self.assertEqual(outcome.problem, transport_module.CHILD_UNSETTLED)
        self.assertEqual(self.performed(), self.no_effects())

    def test_T8c_every_effect_child_of_a_delivery_is_ledgered_and_settled(self):
        mission_id, workflow_id = self.decided()
        runtime_module.process_once(self.broker)
        delivery_id = self.the_delivery()["delivery_id"]
        with open(self.ledger(delivery_id), encoding="utf-8") as handle:
            rows = [json.loads(line) for line in handle]
        intents = [row["intent"] for row in rows if "intent" in row]
        settled = set(row["nonce"] for row in rows if row.get("settled") is True)
        self.assertTrue(intents)
        self.assertEqual(set(intents), settled)
        self.assertTrue(all(isinstance(row.get("pgid"), int) for row in rows
                            if "pgid" in row))


class T9CrashAdoptionTests(DeliveryCase):
    """Required test 9: a crash around an effect is adopted by
    reconciliation — commit, push and PR totals exactly 1."""

    def crash_after(self, verb):
        mission_id, workflow_id = self.decided()
        self.hook(verb, "after", self.crash)
        with self.assertRaises(Crash):
            self.delivery_pass()
        self.delivery_transport.hooks.clear()
        runtime_module.process_once(self.broker)
        runtime_module.process_once(self.broker)
        record = self.the_delivery()
        self.assertEqual(record["phase"], delivery_auth.PHASE_COMPLETE, record["blocker"])
        return mission_id, workflow_id, record

    def test_T9a_after_the_commit(self):
        _m, workflow_id, record = self.crash_after("commit_step")
        self.assertEqual(self.performed()["commit_step"], 1)
        self.assertEqual(self.effects()[1], 1)

    def test_T9b_after_the_push(self):
        _m, workflow_id, record = self.crash_after("push")
        self.assertEqual(self.performed()["push"], 1)
        self.assertEqual(self.effects()[1], 1)

    def test_T9c_after_the_pr_create(self):
        _m, workflow_id, record = self.crash_after("gh_pr_create")
        self.assertEqual(self.performed()["gh_pr_create"], 1)
        self.assertEqual(len(self.delivery_transport.open_prs), 1)


class T10AmbiguityTests(DeliveryCase):
    """Required test 10: remote lookup failure, foreign PR state and
    exhausted attempts refuse a blind retry."""

    def test_T10a_a_remote_lookup_failure_never_voids_an_executing_push(self):
        mission_id, workflow_id = self.decided()
        self.hook("push", "after", self.crash)
        with self.assertRaises(Crash):
            self.delivery_pass()
        self.delivery_transport.hooks.clear()
        real = self.delivery_transport.ls_remote

        def failing(path, remote, ref):
            if ref.startswith("refs/heads/di-mission/"):
                raise transport_module.DeliveryTransportError("ls-remote origin failed")
            return real(path, remote, ref)
        self.delivery_transport.ls_remote = failing
        self.pass_outcome(workflow_id)
        record = self.the_delivery()
        push = record["steps"][delivery_auth.STEP_PUSH]
        self.assertIsNotNone(push["receipt"])
        self.assertNotEqual(push["receipt"]["state"], delivery_auth.RECEIPT_VOID)
        self.assertEqual(push["voided"], [])
        self.assertEqual(self.performed()["push"], 1)
        del self.delivery_transport.ls_remote
        runtime_module.process_once(self.broker)
        self.assertEqual(self.performed()["push"], 1)
        self.assertEqual(self.the_delivery()["phase"], delivery_auth.PHASE_COMPLETE)

    def foreign_pr(self, mission_id, state, same_head=True):
        """After the push, a pull request for the exact head branch (at the
        pushed commit, or at another one) appears on the forge."""
        branch = "di-mission/%s-r1" % mission_id

        def plant():
            head = self.delivery_transport.ls_remote(
                self.delivery_transport.repo_path, "origin", "refs/heads/" + branch)
            self.delivery_transport.open_prs.append({
                "number": 7, "url": "%s/pull/7" % CANONICAL_URL,
                "headRefOid": head if same_head else "0" * 40,
                "headRefName": branch, "baseRefName": "main", "state": state})
        self.hook("push", "after", plant)

    def test_T10b_a_closed_pull_request_for_the_exact_head_blocks(self):
        mission_id, workflow_id = self.decided()
        self.foreign_pr(mission_id, "CLOSED")
        runtime_module.process_once(self.broker)
        record = self.the_delivery()
        self.assertEqual(record["phase"], delivery_auth.PHASE_BLOCKED)
        self.assertEqual(record["blocker"]["problem"], machine_module.PROBLEM_PR_NOT_OPEN)
        self.assertEqual(self.performed()["gh_pr_create"], 0)

    def test_T10c_an_ambiguous_open_pull_request_blocks(self):
        mission_id, workflow_id = self.decided()
        self.foreign_pr(mission_id, "OPEN", same_head=False)
        runtime_module.process_once(self.broker)
        record = self.the_delivery()
        self.assertEqual(record["phase"], delivery_auth.PHASE_BLOCKED)
        self.assertEqual(record["blocker"]["problem"], machine_module.PROBLEM_PR_AMBIGUOUS)
        self.assertEqual(self.performed()["gh_pr_create"], 0)

    def test_T10d_exhausted_push_attempts_block_without_a_push(self):
        mission_id, workflow_id = self.decided()

        def refuse(phase):
            if phase == "before":
                raise transport_module.DeliveryTransportError("push rejected")
        self.delivery_transport.hooks["push"] = refuse
        for _ in range(delivery_auth.MAX_STEP_ATTEMPTS + 3):
            self.delivery_pass()
        record = self.the_delivery()
        self.assertEqual(record["phase"], delivery_auth.PHASE_BLOCKED)
        self.assertEqual(self.performed()["push"], 0)
        self.assertEqual(self.effects(), ([], 0))


class T11NotNeededAndRecoveryTests(DeliveryCase):
    """Required test 11: a NOT_NEEDED base refresh has no invented receipt;
    attestation and acceptance crashes recover without repeating effects."""

    def test_T11a_not_needed_base_refresh_has_no_receipt_or_attestation(self):
        mission_id, workflow_id = self.decided()
        runtime_module.process_once(self.broker)
        record = self.the_delivery()
        refresh = record["steps"][delivery_auth.STEP_BASE_REFRESH]
        self.assertEqual(refresh["state"], delivery_auth.STEP_NOT_NEEDED)
        self.assertIsNone(refresh["receipt"])
        attested = mission_state.attested_artifacts(
            self.service.get_state(mission_id)["record"])
        self.assertNotIn(delivery_auth.STEP_BASE_REFRESH,
                         [mission_state.receipt_attestation_of(a)["step"] for a in attested])
        self.assertEqual(self.desk.status(mission_id)["delivery"]["steps"][0]["receipt_id"],
                         None)

    def test_T11b_an_attestation_crash_recovers_without_repeating_effects(self):
        mission_id, workflow_id = self.decided()
        with mock.patch.object(delivery_module.delivery_parent, "attest_validated_receipt",
                               side_effect=Crash("died attesting")):
            with self.assertRaises(Crash):
                self.delivery_pass()
        effects = self.performed()
        self.assertEqual(self.the_delivery()["phase"], delivery_auth.PHASE_COMPLETE)
        runtime_module.process_once(self.broker)
        self.assertEqual(self.performed(), effects)
        self.assertEqual(self.service.get_state(mission_id)["progress"],
                         mission_state.PROGRESS_COMPLETED)

    def test_T11c_an_acceptance_crash_accepts_the_pending_evidence_once(self):
        mission_id, workflow_id = self.decided()
        real = self.service.accept_evidence
        calls = []

        def crash_on_delivery_recorded(mission, op, seq, evidence_id, digest, context):
            state = self.service.get_state(mission)
            key = [e for e in state["record"]["evidence"]
                   if e["evidence_id"] == evidence_id][0]["requirement_key"]
            if key == "delivery_recorded" and not calls:
                calls.append(evidence_id)
                raise Crash("died accepting")
            return real(mission, op, seq, evidence_id, digest, context)
        self.service.accept_evidence = crash_on_delivery_recorded
        with self.assertRaises(Crash):
            self.delivery_pass()
        effects = self.performed()
        runtime_module.process_once(self.broker)
        self.assertEqual(self.performed(), effects)
        recorded = self.evidence(mission_id, "delivery_recorded")
        self.assertEqual(len(recorded), 1)
        self.assertIsNotNone(recorded[0]["acceptance"])
        self.assertEqual(self.service.get_state(mission_id)["progress"],
                         mission_state.PROGRESS_COMPLETED)


class T12TruthfulUnattestedTests(DeliveryCase):
    """Required test 12: authority gone before attestation leaves truthful
    unattested results; the status read stays read-only."""

    def test_T12_expired_mission_authority_before_attestation_is_reported(self):
        mission_id = self.propose(verification={"argv": list(VERIFY_ARGV)})
        self.approve(mission_id, expires_at=self.clock() + 5000)
        self.op("activate_proof_contract", mission_id)
        self.op("observe_resource_readiness", mission_id, "build_host",
                mission_state.READINESS_READY, self.clock())
        workflow_id = self.completed(mission_id)
        self.wire(workflow_id)
        self.assertEqual(self.pass_outcome(workflow_id).outcome, "delivery_awaiting_decision")
        self.assertTrue(self.delivery_decide(mission_id)["ok"])
        with mock.patch.object(delivery_module.delivery_parent, "attest_validated_receipt",
                               side_effect=Crash("died before attesting")):
            with self.assertRaises(Crash):
                self.delivery_pass()
        effects = self.performed()
        self.assertEqual(self.the_delivery()["phase"], delivery_auth.PHASE_COMPLETE)
        self.clock.advance(5001)
        self.pass_outcome(workflow_id)
        self.assertEqual(self.performed(), effects)
        before = self.snapshot()
        status = self.desk.status(mission_id)
        self.assertEqual(self.snapshot(), before)
        receipts = [s for s in status["delivery"]["steps"] if s["receipt_id"]]
        self.assertTrue(receipts)
        self.assertTrue(all(not s["attested"] for s in receipts))
        self.assertEqual(len(status["uncertainty"]), len(receipts))
        self.assertNotEqual(self.service.get_state(mission_id)["progress"],
                            mission_state.PROGRESS_COMPLETED)


class L1ExpiredDecisionTests(DeliveryCase):
    """Lead gate L1: a decided proposal that expires before minting mints
    nothing, writes nothing, says so, recovers only through an EDIT, and
    its decision is never consumed again."""

    def test_L1a_an_expired_decided_proposal_mints_nothing_writes_nothing_and_says_so(self):
        mission_id, workflow_id = self.decided()
        self.clock.advance(delivery_module.DELIVERY_PROPOSAL_VALIDITY_SECONDS + 1)
        before = self.snapshot()
        outcome = self.pass_outcome(workflow_id)
        self.assertFalse(outcome.ok)
        self.assertEqual(outcome.outcome, "delivery_blocked")
        self.assertEqual(outcome.problem, delivery_module.PROBLEM_PROPOSAL_EXPIRED)
        self.assertEqual(self.deliveries(), {})
        self.assertEqual(self.performed(), self.no_effects())
        self.assertEqual(self.wf_receipts(workflow_id, artifacts.BINDING_TURN_PREFIX), [])
        self.assertEqual(self.snapshot(), before)
        status = self.desk.status(mission_id)
        self.assertEqual(self.snapshot(), before)
        self.assertFalse(status["proposal"]["current"])
        self.assertEqual(status["proposal"]["problem"], delivery_module.PROBLEM_PROPOSAL_EXPIRED)
        self.assertTrue(status["decision"]["accepted"])
        self.assertIsNone(status["delivery"])
        self.assertTrue(status["next_action"].startswith("none until an EDIT"), status)
        self.assertIn(delivery_module.PROBLEM_PROPOSAL_EXPIRED, status["next_action"])
        # the card refuses too, naming the expiry (never re-presented)
        self.assertEqual(self.card(mission_id)["problem"],
                         delivery_module.PROBLEM_PROPOSAL_EXPIRED)
        self.assertEqual(self.snapshot(), before)

    def test_L1b_an_edit_recovers_with_one_delivery_and_never_reuses_the_decision(self):
        mission_id, old_workflow = self.decided()
        old = self.evidence(mission_id, "delivery_decision")[0]
        self.clock.advance(delivery_module.DELIVERY_PROPOSAL_VALIDITY_SECONDS + 1)
        self.assertEqual(self.pass_outcome(old_workflow).problem,
                         delivery_module.PROBLEM_PROPOSAL_EXPIRED)
        # EDIT -> revision 2 -> a fresh approval, contract and readiness.
        self.edit(mission_id, verification={"argv": list(VERIFY_ARGV)})
        self.approve(mission_id)
        self.op("activate_proof_contract", mission_id)
        self.op("observe_resource_readiness", mission_id, "build_host",
                mission_state.READINESS_READY, self.clock())
        # The expired decision is not consumable: the desk refuses its old
        # revision; replaying its reserved submission records nothing new.
        refused = self.desk.accept(
            {"mission_id": mission_id, "revision": 1,
             "proposal_digest_sha256": old["content_digest_sha256"],
             "proposal_artifact_id": "mf-" + "0" * 32,
             "candidate_identity_digest_sha256": "0" * 64,
             "activation_id": old["activation_id"], "expires_at": 0},
            old["operation_id"], CLIENT)
        self.assertFalse(refused["ok"])
        self.assertFalse(refused["recorded"])
        before = len(self.evidence(mission_id, "delivery_decision"))
        try:
            self.grok_service.submit_evidence(
                mission_id, old["operation_id"],
                self.service.get_state(mission_id)["sequence"], "delivery_decision",
                old["kind"], old["content_digest_sha256"], [], CLIENT)
        except mission_record.MissionError:
            pass
        self.assertEqual(len(self.evidence(mission_id, "delivery_decision")), before)
        activation = self.service.get_state(mission_id)["contract"]["activation_id"]
        self.assertEqual([e for e in self.evidence(mission_id, "delivery_decision")
                          if e["activation_id"] == activation], [])
        # The new revision's engagement, proposal, decision and delivery.
        new_workflow = self.completed(mission_id)
        self.assertNotEqual(new_workflow, old_workflow)
        self.wire(new_workflow)
        self.assertEqual(self.pass_outcome(new_workflow).outcome,
                         "delivery_awaiting_decision")
        decided = self.delivery_decide(mission_id)
        self.assertTrue(decided["ok"], decided)
        runtime_module.process_once(self.broker)
        runtime_module.process_once(self.broker)
        delivery = self.the_delivery()
        confirmation = delivery["human_authorization"]["client_confirmation"]
        self.assertEqual(confirmation["mission_revision"], 2)
        self.assertNotEqual(confirmation["decision_id"], old["operation_id"])
        self.assertEqual(delivery["phase"], delivery_auth.PHASE_COMPLETE)
        self.assertEqual(self.wf_receipts(old_workflow, artifacts.BINDING_TURN_PREFIX), [])
        self.assertEqual(self.effects()[1], 1)


class L2WorkspacePreparationTests(DeliveryCase):
    """Lead gate L2 (option 2): the source-branch preparation is workspace
    preparation outside the P1-A6 delivery effect inventory, and every
    property is proven: confinement, create-only, zero remote effects, no
    index or worktree mutation, idempotence, admission at its own
    boundary, durable provenance, and the crash window between the ref
    creation and the HEAD move."""

    def lease_refs(self, workflow_id):
        text = self.git(workflow_id, "for-each-ref", "--format=%(refname) %(objectname)")
        return dict(line.split(" ", 1) for line in text.splitlines() if line)

    def repo_refs(self, path, bare=False):
        argv = (["--git-dir", path] if bare else ["-C", path]) + [
            "for-each-ref", "--format=%(refname) %(objectname)"]
        return run_git(*argv)

    def head(self, workflow_id):
        symbolic = run_git_completed(["-C", self.lease_path(workflow_id), "symbolic-ref",
                                      "-q", "HEAD"], check=False)
        return ((symbolic.stdout or "").strip() or None,
                self.git(workflow_id, "rev-parse", "HEAD"))

    def index_and_tree(self, workflow_id):
        import hashlib
        lease = self.lease_path(workflow_id)
        files = {}
        for directory, dirs, names in os.walk(lease):
            dirs[:] = [d for d in dirs if d != ".git"]
            for name in names:
                path = os.path.join(directory, name)
                with open(path, "rb") as handle:
                    files[os.path.relpath(path, lease)] = hashlib.sha256(
                        handle.read()).hexdigest()
        return self.git(workflow_id, "ls-files", "-s"), files

    def states(self, workflow_id):
        entry = self.record(workflow_id)
        return [artifacts.attach_states({"receipts": [r]}, r["digest"])[0]
                for r in artifacts.workflow_receipts(entry, artifacts.ATTACH_TURN_PREFIX)]

    def ready(self):
        mission_id = self.delivery_mission()
        workflow_id = self.completed(mission_id)
        self.wire(workflow_id)
        return mission_id, workflow_id, "refs/heads/di-mission/%s-r1" % mission_id

    def assert_unprepared(self, workflow_id):
        self.assertEqual(self.wf_receipts(workflow_id, artifacts.PROPOSAL_TURN_PREFIX), [])
        self.assertEqual(self.deliveries(), {})
        self.assertEqual(self.performed(), self.no_effects())

    def test_L2a_confined_create_only_no_index_worktree_or_remote_effect(self):
        mission_id, workflow_id, ref = self.ready()
        refs_before = self.lease_refs(workflow_id)
        self.assertEqual(self.head(workflow_id), (None, self.baseline))
        tree_before = self.index_and_tree(workflow_id)
        others_before = (self.repo_refs(self.bare, bare=True),
                         self.repo_refs(self.target_fixture), self.repo_refs(self.control))
        outcome = self.pass_outcome(workflow_id)
        self.assertEqual(outcome.outcome, "delivery_awaiting_decision")
        self.assertEqual(self.lease_refs(workflow_id),
                         dict(refs_before, **{ref: self.baseline}))
        self.assertEqual(self.head(workflow_id), (ref, self.baseline))
        self.assertEqual(self.index_and_tree(workflow_id), tree_before)
        self.assertEqual((self.repo_refs(self.bare, bare=True),
                          self.repo_refs(self.target_fixture),
                          self.repo_refs(self.control)), others_before)
        self.assertEqual(self.preparation(), {"prepare_ref": 1, "attach_head": 1})
        self.assertEqual(self.performed(), self.no_effects())
        self.assertEqual(self.effects(), ([], 0))  # zero remote effects
        self.assertGreaterEqual(self.delivery_transport.remote_reads, 1)  # reads only
        # each mutation recorded before it ran: intended (create), partial
        # (HEAD move), then the verified result
        self.assertEqual(self.states(workflow_id), [artifacts.ATTACH_INTENDED,
                                                    artifacts.ATTACH_PARTIAL,
                                                    artifacts.ATTACH_ATTACHED])
        status = self.desk.status(mission_id)
        self.assertEqual(status["preparation"]["state"], artifacts.ATTACH_ATTACHED)

    def test_L2b_re_entry_never_redoes_the_mutation(self):
        mission_id, workflow_id, ref = self.ready()
        self.pass_outcome(workflow_id)
        self.pass_outcome(workflow_id)
        # force a re-preparation (the proposal expires undecided): the
        # branch step is re-entered, found done, and nothing is re-done
        self.clock.advance(delivery_module.DELIVERY_PROPOSAL_VALIDITY_SECONDS + 1)
        self.op("observe_resource_readiness", mission_id, "build_host",
                mission_state.READINESS_READY, self.clock())
        self.assertEqual(self.pass_outcome(workflow_id).outcome, "delivery_awaiting_decision")
        self.assertEqual(len(self.wf_receipts(workflow_id, artifacts.PROPOSAL_TURN_PREFIX)), 2)
        self.assertEqual(self.preparation(), {"prepare_ref": 1, "attach_head": 1})
        self.assertEqual(self.states(workflow_id), [artifacts.ATTACH_INTENDED,
                                                    artifacts.ATTACH_PARTIAL,
                                                    artifacts.ATTACH_ATTACHED])

    def test_L2c_an_existing_branch_is_never_moved_or_deleted(self):
        mission_id, workflow_id, ref = self.ready()
        other = self.base_advance(workflow_id)
        self.git(workflow_id, "update-ref", ref, other, ZERO)
        refs_before = self.lease_refs(workflow_id)
        outcome = self.pass_outcome(workflow_id)
        self.assertEqual(outcome.problem, delivery_module.PROBLEM_PREPARATION_UNRESOLVED)
        self.assertEqual(self.lease_refs(workflow_id), refs_before)
        self.assertEqual(self.head(workflow_id), (None, self.baseline))
        self.assertEqual(self.preparation(), {"prepare_ref": 0, "attach_head": 0})
        self.assertEqual(self.states(workflow_id), [artifacts.ATTACH_UNRESOLVED])
        self.assert_unprepared(workflow_id)
        self.pass_outcome(workflow_id)
        self.assertEqual(self.states(workflow_id), [artifacts.ATTACH_UNRESOLVED])
        status = self.desk.status(mission_id)
        self.assertEqual(status["preparation"]["state"], artifacts.ATTACH_UNRESOLVED)
        self.assertTrue(status["next_action"].startswith("none until an EDIT"))

    def test_L2d_the_crash_window_between_the_ref_and_the_head_move(self):
        # Sub-window 1: the process dies right after the create, BEFORE the
        # HEAD move's own admission recorded anything.
        mission_id, workflow_id, ref = self.ready()
        self.hook("prepare_ref", "after", self.crash)
        with self.assertRaises(Crash):
            self.delivery_pass()
        self.delivery_transport.hooks.clear()
        # The exact resulting state: the ref created, HEAD not moved, the
        # attempt durably recorded, nothing else anywhere.
        self.assertEqual(self.preparation(), {"prepare_ref": 1, "attach_head": 0})
        self.assertEqual(self.lease_refs(workflow_id)[ref], self.baseline)
        self.assertEqual(self.head(workflow_id), (None, self.baseline))
        self.assertEqual(self.states(workflow_id), [artifacts.ATTACH_INTENDED])
        self.assert_unprepared(workflow_id)
        self.assertEqual(self.effects(), ([], 0))
        status = self.desk.status(mission_id)
        self.assertEqual(status["preparation"]["state"], artifacts.ATTACH_INTENDED)
        # Re-entry reconciles the refs and HEAD as found: the HEAD move gets
        # its own admission and record, runs ONCE, and no second ref exists.
        self.assertEqual(self.pass_outcome(workflow_id).outcome, "delivery_awaiting_decision")
        self.assertEqual(self.preparation(), {"prepare_ref": 1, "attach_head": 1})
        self.assertEqual(self.head(workflow_id), (ref, self.baseline))
        self.assertEqual(self.states(workflow_id), [
            artifacts.ATTACH_INTENDED, artifacts.ATTACH_PARTIAL, artifacts.ATTACH_ATTACHED])

    def test_L2d2_a_crash_after_the_head_move_was_admitted_is_never_retried(self):
        # Sub-window 2: the HEAD move was admitted and recorded (``partial``)
        # and the process died before ``symbolic-ref`` ran: the one HEAD move
        # of this preparation is spent, so re-entry refuses to advance.
        mission_id, workflow_id, ref = self.ready()
        self.hook("attach_head", "before", self.crash)
        with self.assertRaises(Crash):
            self.delivery_pass()
        self.delivery_transport.hooks.clear()
        self.assertEqual(self.preparation(), {"prepare_ref": 1, "attach_head": 0})
        self.assertEqual(self.head(workflow_id), (None, self.baseline))
        self.assertEqual(self.states(workflow_id), [artifacts.ATTACH_INTENDED,
                                                    artifacts.ATTACH_PARTIAL])
        for _ in range(2):
            outcome = self.pass_outcome(workflow_id)
            self.assertEqual(outcome.problem, delivery_module.PROBLEM_PREPARATION_UNRESOLVED)
        self.assertEqual(self.preparation(), {"prepare_ref": 1, "attach_head": 0})
        self.assertEqual(self.head(workflow_id), (None, self.baseline))
        self.assertEqual(self.states(workflow_id), [artifacts.ATTACH_INTENDED,
                                                    artifacts.ATTACH_PARTIAL,
                                                    artifacts.ATTACH_UNRESOLVED])
        self.assert_unprepared(workflow_id)
        self.assertEqual(self.desk.status(mission_id)["preparation"]["state"],
                         artifacts.ATTACH_UNRESOLVED)

    def test_L2e_an_unresolved_window_refuses_to_advance_and_is_never_retried(self):
        mission_id, workflow_id, ref = self.ready()
        self.hook("prepare_ref", "after", self.crash)
        with self.assertRaises(Crash):
            self.delivery_pass()
        self.delivery_transport.hooks.clear()
        # Something else moves the created ref before re-entry.
        other = self.base_advance(workflow_id)
        self.git(workflow_id, "update-ref", ref, other, self.baseline)
        for _ in range(3):
            outcome = self.pass_outcome(workflow_id)
            self.assertEqual(outcome.problem, delivery_module.PROBLEM_PREPARATION_UNRESOLVED)
        self.assertEqual(self.preparation(), {"prepare_ref": 1, "attach_head": 0})
        self.assertEqual(self.lease_refs(workflow_id)[ref], other)
        self.assertEqual(self.head(workflow_id), (None, self.baseline))
        self.assertEqual(self.states(workflow_id), [artifacts.ATTACH_INTENDED,
                                                    artifacts.ATTACH_UNRESOLVED])
        self.assert_unprepared(workflow_id)

    def lock(self, workflow_id, relative):
        """A stale git lock file in the lease: the REAL git child that needs
        it fails (an owned child that ran, exited nonzero and settled)."""
        path = os.path.join(self.lease_path(workflow_id), ".git", relative + ".lock")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write("held by the test\n")

    def test_L2f_a_failing_head_move_is_attempted_at_most_once(self):
        mission_id, workflow_id, ref = self.ready()
        self.lock(workflow_id, "HEAD")
        problems = [self.pass_outcome(workflow_id).problem for _ in range(4)]
        self.assertEqual(problems, [delivery_module.PROBLEM_PREPARATION_FAILED,
                                    delivery_module.PROBLEM_PREPARATION_UNRESOLVED,
                                    delivery_module.PROBLEM_PREPARATION_UNRESOLVED,
                                    delivery_module.PROBLEM_PREPARATION_UNRESOLVED])
        self.assertEqual(self.states(workflow_id), [
            artifacts.ATTACH_INTENDED, artifacts.ATTACH_PARTIAL, artifacts.ATTACH_FAILED,
            artifacts.ATTACH_UNRESOLVED])
        self.assertEqual(self.preparation(), {"prepare_ref": 1, "attach_head": 0})
        self.assertEqual(self.head(workflow_id), (None, self.baseline))
        self.assert_unprepared(workflow_id)

    def test_L2g_a_ref_creation_that_changed_nothing_is_retried_a_bounded_number_of_times(self):
        mission_id, workflow_id, ref = self.ready()
        self.lock(workflow_id, ref)
        problems = [self.pass_outcome(workflow_id).problem for _ in range(5)]
        self.assertEqual(problems[:3], [delivery_module.PROBLEM_PREPARATION_FAILED] * 3)
        self.assertEqual(problems[3:], [delivery_module.PROBLEM_PREPARATION_UNRESOLVED] * 2)
        states = self.states(workflow_id)
        self.assertEqual(states.count(artifacts.ATTACH_INTENDED),
                         delivery_module.MAX_PREPARATION_ATTEMPTS)
        self.assertEqual(states.count(artifacts.ATTACH_NO_EFFECT), 3)
        self.assertEqual(states[-1], artifacts.ATTACH_UNRESOLVED)
        self.assertNotIn(ref, self.lease_refs(workflow_id))
        self.assert_unprepared(workflow_id)

    def test_L2h_the_mutation_is_admitted_at_its_own_boundary(self):
        for control, problem, blocked in (
                ("hold", gate_module.PROBLEM_HOLD_ACTIVE, False),
                ("cancel", gate_module.PROBLEM_CANCEL_REQUESTED, True)):
            with self.subTest(control=control):
                mission_id, workflow_id, ref = self.ready()
                accept = self.driver._accept_runtime_evidence

                def then_control(*args, **kwargs):
                    changed = accept(*args, **kwargs)
                    getattr(self.service, "request_%s" % control)(mission_id)
                    return changed
                self.driver._accept_runtime_evidence = then_control
                outcome = self.pass_outcome(workflow_id)
                self.assertEqual(outcome.problem, problem)
                self.assertEqual(outcome.outcome,
                                 "delivery_blocked" if blocked else "delivery_held")
                self.assertEqual(self.preparation(), {"prepare_ref": 0, "attach_head": 0})
                self.assertEqual(self.states(workflow_id), [])
                self.assertNotIn(ref, self.lease_refs(workflow_id))
                self.assert_unprepared(workflow_id)

    # -- the gap BETWEEN the two mutations (Lead finding S6-1) ------------------

    def in_the_gap(self, event):
        """Run ``event`` once, right after the REAL create, before the HEAD
        move (the ``prepare_ref`` hook's 'after')."""
        self.hook("prepare_ref", "after", event)

    def assert_head_move_not_run(self, workflow_id, ref, states):
        self.assertEqual(self.preparation(), {"prepare_ref": 1, "attach_head": 0})
        self.assertEqual(self.lease_refs(workflow_id)[ref], self.baseline)
        self.assertEqual(self.head(workflow_id), (None, self.baseline))
        self.assertEqual(self.states(workflow_id), states)
        self.assert_unprepared(workflow_id)

    def test_L2i_X1_a_hold_in_the_gap_stops_the_head_move_then_resumes_once(self):
        mission_id, workflow_id, ref = self.ready()
        self.in_the_gap(lambda: self.service.request_hold(mission_id))
        outcome = self.pass_outcome(workflow_id)
        self.assertEqual((outcome.outcome, outcome.problem),
                         ("delivery_held", gate_module.PROBLEM_HOLD_ACTIVE))
        self.assert_head_move_not_run(workflow_id, ref, [artifacts.ATTACH_INTENDED,
                                                         artifacts.ATTACH_FAILED])
        # While held nothing moves; once released the HEAD move alone runs
        # (under its own admission), the create is never repeated.
        self.assertEqual(self.pass_outcome(workflow_id).problem,
                         gate_module.PROBLEM_HOLD_ACTIVE)
        self.assertEqual(self.preparation(), {"prepare_ref": 1, "attach_head": 0})
        self.service.release_hold(mission_id)
        self.assertEqual(self.pass_outcome(workflow_id).outcome, "delivery_awaiting_decision")
        self.assertEqual(self.preparation(), {"prepare_ref": 1, "attach_head": 1})
        self.assertEqual(self.head(workflow_id), (ref, self.baseline))
        self.assertEqual(self.states(workflow_id), [
            artifacts.ATTACH_INTENDED, artifacts.ATTACH_FAILED, artifacts.ATTACH_PARTIAL,
            artifacts.ATTACH_ATTACHED])

    def test_L2j_X1_a_cancel_in_the_gap_stops_the_head_move_for_good(self):
        mission_id, workflow_id, ref = self.ready()
        self.in_the_gap(lambda: self.service.request_cancel(mission_id))
        outcome = self.pass_outcome(workflow_id)
        self.assertEqual((outcome.outcome, outcome.problem),
                         ("delivery_blocked", gate_module.PROBLEM_CANCEL_REQUESTED))
        self.assert_head_move_not_run(workflow_id, ref, [artifacts.ATTACH_INTENDED,
                                                         artifacts.ATTACH_FAILED])
        self.assertEqual(self.pass_outcome(workflow_id).problem,
                         gate_module.PROBLEM_CANCEL_REQUESTED)
        self.assert_head_move_not_run(workflow_id, ref, [artifacts.ATTACH_INTENDED,
                                                         artifacts.ATTACH_FAILED])

    def test_L2k_X2_an_edit_in_the_gap_stops_the_head_move(self):
        mission_id, workflow_id, ref = self.ready()
        self.in_the_gap(lambda: self.edit(mission_id))
        outcome = self.pass_outcome(workflow_id)
        self.assertEqual((outcome.outcome, outcome.problem),
                         ("delivery_blocked", gate_module.PROBLEM_REVISION_SUPERSEDED))
        self.assert_head_move_not_run(workflow_id, ref, [artifacts.ATTACH_INTENDED,
                                                         artifacts.ATTACH_FAILED])

    def test_L2l_X3_a_foreign_ref_move_in_the_gap_is_never_attached(self):
        mission_id, workflow_id, ref = self.ready()
        moved = {}

        def move_it():
            moved["oid"] = self.base_advance(workflow_id)
            self.git(workflow_id, "update-ref", ref, moved["oid"], self.baseline)
        self.in_the_gap(move_it)
        outcome = self.pass_outcome(workflow_id)
        self.assertEqual((outcome.outcome, outcome.problem),
                         ("delivery_blocked", delivery_module.PROBLEM_PREPARATION_UNRESOLVED))
        self.assertEqual(self.preparation(), {"prepare_ref": 1, "attach_head": 0})
        self.assertEqual(self.lease_refs(workflow_id)[ref], moved["oid"])
        self.assertEqual(self.head(workflow_id), (None, self.baseline))
        self.assertEqual(self.states(workflow_id), [artifacts.ATTACH_INTENDED,
                                                    artifacts.ATTACH_PARTIAL,
                                                    artifacts.ATTACH_UNRESOLVED])
        self.assert_unprepared(workflow_id)
        # never retried: a later pass records nothing more and moves nothing
        self.pass_outcome(workflow_id)
        self.assertEqual(self.preparation(), {"prepare_ref": 1, "attach_head": 0})
        self.assertEqual(len(self.states(workflow_id)), 3)


class S63PreparationOwnershipTests(DeliveryCase):
    """Lead gate finding S6-3: both preparation MUTATIONS are owned children
    under the P1-A6 transport's existing owned-child contract, keyed by the
    preparation attempt (named owner, own session, fsynced intent before the
    spawn, group id after, settlement proof by the group check); read-only
    calls stay unowned; and re-entry proves every earlier child settled
    before it adopts, retries or advances. The owner deaths below are REAL:
    the owner is a separate Runtime process (``tests/_delivery_pass.py``,
    the production gate and delivery composition) that is killed; S63a and
    S63b re-enter in FRESH processes. S6-4 (S63e-S63g) runs its passes
    in-process, so ONE pass can be observed: a REAL owned child whose group
    outlives its leader (a same-group descendant), and a REAL orphan whose
    create completes while the re-entering pass is under way."""

    _L2 = L2WorkspacePreparationTests
    lease_refs = _L2.lease_refs
    head = _L2.head
    states = _L2.states
    ready = _L2.ready
    assert_unprepared = _L2.assert_unprepared
    HELPER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_delivery_pass.py")

    # -- fixtures ---------------------------------------------------------------

    def key(self, workflow_id, ref):
        return delivery_module.preparation_owner_key(artifacts.attach_digest(
            workflow_id, self.lease_path(workflow_id), ref, self.baseline))

    def ledger(self, key):
        path = os.path.join(self.delivery_dir, transport_module.CHILD_LEDGER_DIR_NAME,
                            key + ".jsonl")
        if not os.path.exists(path):
            return []
        with open(path, encoding="utf-8") as handle:
            return [json.loads(line) for line in handle if line.strip()]

    def intents(self, key, effect):
        return [row for row in self.ledger(key)
                if isinstance(row.get("intent"), str) and row.get("effect") == effect]

    def stopped_before_preparation(self):
        """Verification and evidence done by an in-process pass; the
        preparation itself never started."""
        mission_id, workflow_id, ref = self.ready()
        stop = delivery_module._Stop(delivery_module.STATUS_HELD, "test_stop",
                                     "stopped before the preparation")
        with mock.patch.object(delivery_module.MissionDelivery, "prepare_proposal",
                               side_effect=stop):
            self.assertEqual(self.pass_outcome(workflow_id).problem, "test_stop")
        self.assertEqual(self.states(workflow_id), [])
        return mission_id, workflow_id, ref

    def config(self, workflow_id, **fault):
        config = {"root": REPO_ROOT, "now": self.clock(), "principal": "dirun-test",
                  "mission_dir": self.mission_dir, "store_dir": self.store_dir,
                  "delivery_dir": self.delivery_dir, "control": self.control,
                  "workspaces": self.workspaces, "claude_config": self.claude_config,
                  "workflow_id": workflow_id}
        config.update(fault)
        return json.dumps(config)

    def fresh_pass(self, workflow_id, **fault):
        """One Runtime delivery pass in a FRESH process. Bounded: a pass that
        does not return (a child it must never have started, blocked by the
        test's hook) fails the test instead of hanging the suite."""
        try:
            return subprocess.run([sys.executable, "-B", self.HELPER,
                                   self.config(workflow_id, **fault)],
                                  capture_output=True, text=True, env=dict(os.environ),
                                  timeout=120)
        except subprocess.TimeoutExpired:
            self.fail("the fresh-process pass did not return: it is blocked in a"
                      " child it should never have started")

    def outcome_of(self, completed):
        self.assertEqual(completed.returncode, 0, completed.stderr[-3000:])
        return json.loads(completed.stdout.strip().splitlines()[-1])

    @staticmethod
    def group_alive(pgid):
        try:
            os.killpg(pgid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        return True

    def wait_until(self, predicate, what, seconds=60.0):
        import time
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            if predicate():
                return
            time.sleep(0.05)
        self.fail("timed out waiting for %s" % what)

    def blocking_hook(self, workflow_id):
        """A ``reference-transaction`` hook in the lease that holds a ref
        update in its 'prepared' state while the block file exists: the
        REAL ``update-ref`` child stays alive, uncommitted."""
        block = os.path.join(self.base, "block-reference-transaction")
        hook = os.path.join(self.lease_path(workflow_id), ".git", "hooks",
                            "reference-transaction")
        os.makedirs(os.path.dirname(hook), exist_ok=True)
        with open(hook, "w", encoding="utf-8") as handle:
            handle.write("#!/bin/sh\ncat >/dev/null\n"
                         "if [ \"$1\" = prepared ]; then\n"
                         "  while [ -f '%s' ]; do sleep 0.05; done\nfi\nexit 0\n" % block)
        os.chmod(hook, 0o755)
        with open(block, "w", encoding="utf-8") as handle:
            handle.write("block\n")
        return block

    def release_and_reap(self, block, key):
        if os.path.exists(block):
            os.unlink(block)
        for row in self.ledger(key):
            if isinstance(row.get("pgid"), int):
                self.wait_until(lambda pgid=row["pgid"]: not self.group_alive(pgid),
                                "group %d to end" % row["pgid"], seconds=30.0)

    def lingering_git(self, armed):
        """A ``git`` first on PATH for this test that, ONCE, for the armed
        owned mutation (``update-ref`` — the create — or
        ``symbolic-ref-set`` — ``symbolic-ref HEAD refs/heads/...``, the HEAD
        move; the read-only ``symbolic-ref -q HEAD`` never matches), leaves
        a background descendant in the child's OWN process group that lives
        while the returned block file exists, then ``exec``s the git this
        test resolved (so the owned child's pid and group are unchanged and
        the real git runs). The leader exits normally; its group does not
        end. Returns the block file; the cleanup releases it."""
        real_git = shutil.which("git")
        shim_dir = os.path.join(self.base, "lingering-git")
        os.makedirs(shim_dir, exist_ok=True)
        arm = os.path.join(shim_dir, "arm")
        block = os.path.join(shim_dir, "block")
        with open(os.path.join(shim_dir, "git"), "w", encoding="utf-8") as handle:
            handle.write(
                "#!/bin/sh\n"
                "if [ -f '%(arm)s' ]; then\n"
                "  armed=$(cat '%(arm)s'); hit=''\n"
                "  case \"$armed\" in\n"
                "    update-ref) case \" $* \" in *' update-ref '*) hit=1 ;; esac ;;\n"
                "    symbolic-ref-set) case \" $* \" in"
                " *' symbolic-ref HEAD refs/heads/'*) hit=1 ;; esac ;;\n"
                "  esac\n"
                "  if [ -n \"$hit\" ]; then\n"
                "    rm -f '%(arm)s'\n"
                "    ( while [ -f '%(block)s' ]; do sleep 0.05; done )"
                " >/dev/null 2>&1 </dev/null &\n"
                "  fi\n"
                "fi\n"
                "exec '%(git)s' \"$@\"\n" % {"arm": arm, "block": block, "git": real_git})
        os.chmod(os.path.join(shim_dir, "git"), 0o755)
        for path, text in ((block, "block\n"), (arm, armed)):
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(text)
        path_patch = mock.patch.dict(os.environ, {
            "PATH": shim_dir + os.pathsep + os.environ.get("PATH", "")})
        path_patch.start()
        self.addCleanup(path_patch.stop)
        self.addCleanup(lambda: os.path.exists(block) and os.unlink(block))
        return block

    def settle_rows(self, key, effect):
        """``(returncode, settled)`` of every settlement row the transport
        wrote when an owned ``effect`` child's leader exited."""
        nonces = {row["intent"] for row in self.intents(key, effect)}
        return [(row["returncode"], row["settled"]) for row in self.ledger(key)
                if row.get("nonce") in nonces and "returncode" in row]

    def group_of(self, key, effect):
        nonces = {row["intent"] for row in self.intents(key, effect)}
        groups = [row["pgid"] for row in self.ledger(key)
                  if row.get("nonce") in nonces and isinstance(row.get("pgid"), int)]
        self.assertEqual(len(groups), 1, (effect, groups))
        return groups[0]

    # -- regressions ----------------------------------------------------------------

    def test_S63a_a_real_child_outlives_its_owner_and_blocks_re_entry_until_settled(self):
        mission_id, workflow_id, ref = self.stopped_before_preparation()
        key = self.key(workflow_id, ref)
        block = self.blocking_hook(workflow_id)
        self.addCleanup(self.release_and_reap, block, key)
        owner = subprocess.Popen([sys.executable, "-B", self.HELPER, self.config(workflow_id)],
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self.wait_until(lambda: any(isinstance(r.get("pgid"), int) for r in self.ledger(key)),
                        "the owned create child's group row")
        pgid = [r["pgid"] for r in self.ledger(key) if isinstance(r.get("pgid"), int)][0]
        owner.kill()                                   # the OWNER dies ...
        owner.communicate()
        self.assertTrue(self.group_alive(pgid))       # ... its owned child lives on
        self.assertEqual([r["effect"] for r in self.ledger(key) if "intent" in r],
                         [delivery_module.PREPARATION_CREATE])
        self.assertNotIn(ref, self.lease_refs(workflow_id))   # create not committed
        self.assertEqual(self.states(workflow_id), [artifacts.ATTACH_INTENDED])
        # A FRESH-PROCESS re-entry is held: no second create, no HEAD move,
        # no signal to the live group, nothing adopted or advanced.
        for _ in range(2):
            outcome = self.outcome_of(self.fresh_pass(workflow_id))
            self.assertEqual((outcome["outcome"], outcome["problem"]),
                             ("delivery_held", transport_module.CHILD_UNSETTLED))
        self.assertTrue(self.group_alive(pgid))
        self.assertEqual(len(self.intents(key, delivery_module.PREPARATION_CREATE)), 1)
        self.assertEqual(self.intents(key, delivery_module.PREPARATION_HEAD_MOVE), [])
        self.assertEqual(self.states(workflow_id), [artifacts.ATTACH_INTENDED])
        self.assertNotIn(ref, self.lease_refs(workflow_id))
        self.assert_unprepared(workflow_id)
        # The surviving child finishes its create on its own and exits.
        os.unlink(block)
        self.wait_until(lambda: not self.group_alive(pgid), "the child's group to end")
        self.assertEqual(self.lease_refs(workflow_id)[ref], self.baseline)
        self.assertEqual(self.head(workflow_id), (None, self.baseline))
        # A fresh re-entry PROVES the settlement (the group check), ADOPTS
        # that create — never re-running it — and moves HEAD exactly once.
        outcome = self.outcome_of(self.fresh_pass(workflow_id))
        self.assertEqual(outcome["outcome"], "delivery_awaiting_decision")
        self.assertEqual(len(self.intents(key, delivery_module.PREPARATION_CREATE)), 1)
        self.assertEqual(len(self.intents(key, delivery_module.PREPARATION_HEAD_MOVE)), 1)
        rows = self.ledger(key)
        self.assertEqual({r["intent"] for r in rows if "intent" in r},
                         {r["nonce"] for r in rows if r.get("settled") is True})
        self.assertEqual(len([r for r in rows if r.get("proven_at")]), 1)
        self.assertEqual(self.head(workflow_id), (ref, self.baseline))
        self.assertEqual(self.states(workflow_id), [artifacts.ATTACH_INTENDED,
                                                    artifacts.ATTACH_PARTIAL,
                                                    artifacts.ATTACH_ATTACHED])
        self.assertEqual(len(self.wf_receipts(workflow_id, artifacts.PROPOSAL_TURN_PREFIX)), 1)
        self.assertEqual(self.deliveries(), {})

    def test_S63b_an_owner_dead_between_intent_and_spawn_stays_unresolved(self):
        mission_id, workflow_id, ref = self.stopped_before_preparation()
        key = self.key(workflow_id, ref)
        died = self.fresh_pass(workflow_id, die_after_intent=delivery_module.PREPARATION_CREATE)
        self.assertEqual(died.returncode, 137, died.stderr[-2000:])
        rows = self.ledger(key)
        self.assertEqual([r["effect"] for r in rows if "intent" in r],
                         [delivery_module.PREPARATION_CREATE])
        self.assertEqual([r for r in rows if "pgid" in r], [])   # the start is unknown
        self.assertEqual(self.states(workflow_id), [artifacts.ATTACH_INTENDED])
        for _ in range(2):
            outcome = self.outcome_of(self.fresh_pass(workflow_id))
            self.assertEqual((outcome["outcome"], outcome["problem"]),
                             ("delivery_blocked",
                              delivery_module.PROBLEM_PREPARATION_UNRESOLVED))
        self.assertEqual(self.states(workflow_id), [artifacts.ATTACH_INTENDED,
                                                    artifacts.ATTACH_UNRESOLVED])
        self.assertEqual(len(self.intents(key, delivery_module.PREPARATION_CREATE)), 1)
        self.assertNotIn(ref, self.lease_refs(workflow_id))
        self.assertEqual(self.head(workflow_id), (None, self.baseline))
        self.assert_unprepared(workflow_id)

    def test_S63c_an_injected_unknown_outcome_is_never_retried(self):
        # The Lead's Y4 shape: the step raised with NO owned child behind it
        # (an INJECTED unknown outcome — not a surviving-child crash, which
        # S63a/S63b cover).
        mission_id, workflow_id, ref = self.ready()
        transport = self.delivery_transport
        calls = []

        def unknown(lease, reference, oid, old):
            calls.append(reference)
            raise transport_module.DeliveryTransportError("outcome unknown")
        transport.update_ref = unknown
        first = self.pass_outcome(workflow_id)
        del transport.update_ref
        second = self.pass_outcome(workflow_id)
        self.assertEqual(first.problem, delivery_module.PROBLEM_PREPARATION_FAILED)
        self.assertEqual(second.problem, delivery_module.PROBLEM_PREPARATION_UNRESOLVED)
        self.assertEqual(len(calls), 1)
        self.assertEqual(self.preparation(), {"prepare_ref": 0, "attach_head": 0})
        self.assertEqual(self.states(workflow_id), [artifacts.ATTACH_INTENDED,
                                                    artifacts.ATTACH_FAILED,
                                                    artifacts.ATTACH_UNRESOLVED])
        self.assert_unprepared(workflow_id)

    def test_S63d_only_the_two_mutations_are_owned_and_each_in_its_own_session(self):
        mission_id, workflow_id, ref = self.ready()
        seen = []
        real = transport_module.DeliveryTransport._start_child

        def spy(self_t, argv, owned, **kwargs):
            process, owner, nonce = real(self_t, argv, owned, **kwargs)
            verb = next((a for a in argv if a in ("update-ref", "symbolic-ref")), None)
            if verb == "symbolic-ref" and len([a for a in argv[argv.index(verb) + 1:]
                                               if not a.startswith("-")]) >= 2:
                verb = "symbolic-ref-set"
            seen.append((verb, owner is not None))
            return process, owner, nonce
        with mock.patch.object(transport_module.DeliveryTransport, "_start_child", spy):
            self.assertEqual(self.pass_outcome(workflow_id).outcome,
                             "delivery_awaiting_decision")
        owned = [verb for verb, has_owner in seen if has_owner]
        self.assertEqual(owned, ["update-ref", "symbolic-ref-set"])
        self.assertTrue(all(not has_owner for verb, has_owner in seen
                            if verb == "symbolic-ref"))       # reads stay unowned
        self.assertIn(transport_module.SYMBOLIC_REF_SET, transport_module.EFFECT_GIT_VERBS)
        self.assertNotIn("symbolic-ref", transport_module.EFFECT_GIT_VERBS)
        key = self.key(workflow_id, ref)
        rows = self.ledger(key)
        self.assertEqual([r["effect"] for r in rows if "intent" in r],
                         [delivery_module.PREPARATION_CREATE,
                          delivery_module.PREPARATION_HEAD_MOVE])
        self.assertEqual(len([r for r in rows if isinstance(r.get("pgid"), int)]), 2)
        self.assertEqual({r["intent"] for r in rows if "intent" in r},
                         {r["nonce"] for r in rows if r.get("settled") is True})

    def test_S63e_a_create_group_outliving_its_leader_holds_the_head_move_in_the_same_pass(self):
        # S6-4: the REAL owned ``update-ref`` exits 0 with the ref created,
        # but a descendant keeps its process group alive, so the transport
        # records ``settled: false``. The SAME pass must not move HEAD,
        # record ``partial`` or prepare a proposal.
        CREATE, MOVE = delivery_module.PREPARATION_CREATE, delivery_module.PREPARATION_HEAD_MOVE
        mission_id, workflow_id, ref = self.ready()
        key = self.key(workflow_id, ref)
        block = self.lingering_git("update-ref")
        outcome = self.pass_outcome(workflow_id)
        self.assertEqual((outcome.outcome, outcome.problem),
                         ("delivery_held", transport_module.CHILD_UNSETTLED))
        pgid = self.group_of(key, CREATE)
        self.assertEqual(self.settle_rows(key, CREATE), [(0, False)])
        self.assertTrue(self.group_alive(pgid))
        self.assertEqual(len(self.intents(key, CREATE)), 1)
        self.assertEqual(self.intents(key, MOVE), [])
        self.assertEqual(self.preparation(), {"prepare_ref": 1, "attach_head": 0})
        self.assertEqual(self.lease_refs(workflow_id)[ref], self.baseline)   # took effect
        self.assertEqual(self.head(workflow_id), (None, self.baseline))
        self.assertEqual(self.states(workflow_id), [artifacts.ATTACH_INTENDED])
        self.assert_unprepared(workflow_id)
        # Re-entry while the group lives: held again, nothing more, and the
        # group is never signalled.
        again = self.pass_outcome(workflow_id)
        self.assertEqual((again.outcome, again.problem),
                         ("delivery_held", transport_module.CHILD_UNSETTLED))
        self.assertEqual(self.preparation(), {"prepare_ref": 1, "attach_head": 0})
        self.assertEqual((len(self.intents(key, CREATE)), len(self.intents(key, MOVE))),
                         (1, 0))
        self.assertEqual(self.states(workflow_id), [artifacts.ATTACH_INTENDED])
        self.assertTrue(self.group_alive(pgid))
        self.assert_unprepared(workflow_id)
        # Released externally: the next pass proves the settlement, adopts
        # the create (never re-run) and moves HEAD exactly once.
        os.unlink(block)
        self.wait_until(lambda: not self.group_alive(pgid), "the lingering group to end")
        self.assertEqual(self.pass_outcome(workflow_id).outcome, "delivery_awaiting_decision")
        self.assertEqual(self.preparation(), {"prepare_ref": 1, "attach_head": 1})
        self.assertEqual((len(self.intents(key, CREATE)), len(self.intents(key, MOVE))),
                         (1, 1))
        self.assertEqual(len([r for r in self.ledger(key) if r.get("proven_at")]), 1)
        self.assertEqual(self.head(workflow_id), (ref, self.baseline))
        self.assertEqual(self.states(workflow_id), [
            artifacts.ATTACH_INTENDED, artifacts.ATTACH_PARTIAL, artifacts.ATTACH_ATTACHED])
        self.assertEqual(len(self.wf_receipts(workflow_id, artifacts.PROPOSAL_TURN_PREFIX)), 1)
        self.assertEqual(self.deliveries(), {})

    def test_S63f_a_head_move_group_outliving_its_leader_holds_the_proposal_in_the_same_pass(self):
        # S6-4: the REAL owned ``symbolic-ref HEAD <ref>`` exits 0 with HEAD
        # moved, but a descendant keeps its group alive. Nothing is recorded
        # attached and no proposal is prepared until its settlement is proven.
        CREATE, MOVE = delivery_module.PREPARATION_CREATE, delivery_module.PREPARATION_HEAD_MOVE
        mission_id, workflow_id, ref = self.ready()
        key = self.key(workflow_id, ref)
        block = self.lingering_git("symbolic-ref-set")
        outcome = self.pass_outcome(workflow_id)
        self.assertEqual((outcome.outcome, outcome.problem),
                         ("delivery_held", transport_module.CHILD_UNSETTLED))
        pgid = self.group_of(key, MOVE)
        self.assertEqual(self.settle_rows(key, CREATE), [(0, True)])
        self.assertEqual(self.settle_rows(key, MOVE), [(0, False)])
        self.assertTrue(self.group_alive(pgid))
        self.assertEqual((len(self.intents(key, CREATE)), len(self.intents(key, MOVE))),
                         (1, 1))
        self.assertEqual(self.preparation(), {"prepare_ref": 1, "attach_head": 1})
        self.assertEqual(self.head(workflow_id), (ref, self.baseline))     # took effect
        self.assertEqual(self.states(workflow_id), [artifacts.ATTACH_INTENDED,
                                                    artifacts.ATTACH_PARTIAL])
        self.assert_unprepared(workflow_id)
        again = self.pass_outcome(workflow_id)
        self.assertEqual((again.outcome, again.problem),
                         ("delivery_held", transport_module.CHILD_UNSETTLED))
        self.assertEqual(self.preparation(), {"prepare_ref": 1, "attach_head": 1})
        self.assertEqual(self.states(workflow_id), [artifacts.ATTACH_INTENDED,
                                                    artifacts.ATTACH_PARTIAL])
        self.assertTrue(self.group_alive(pgid))
        self.assert_unprepared(workflow_id)
        # Released externally: the next pass proves the settlement and
        # adopts the moved HEAD — no second HEAD move — then prepares once.
        os.unlink(block)
        self.wait_until(lambda: not self.group_alive(pgid), "the lingering group to end")
        self.assertEqual(self.pass_outcome(workflow_id).outcome, "delivery_awaiting_decision")
        self.assertEqual(self.preparation(), {"prepare_ref": 1, "attach_head": 1})
        self.assertEqual((len(self.intents(key, CREATE)), len(self.intents(key, MOVE))),
                         (1, 1))
        self.assertEqual(len([r for r in self.ledger(key) if r.get("proven_at")]), 1)
        self.assertEqual(self.states(workflow_id), [
            artifacts.ATTACH_INTENDED, artifacts.ATTACH_PARTIAL, artifacts.ATTACH_ATTACHED])
        self.assertEqual(len(self.wf_receipts(workflow_id, artifacts.PROPOSAL_TURN_PREFIX)), 1)
        self.assertEqual(self.deliveries(), {})

    def test_S63g_an_orphan_completing_during_the_pass_is_never_decided_on_a_stale_state(self):
        # S6-4, the classify/settle window: a REAL orphan create (owner
        # killed, held uncommitted by the lease hook) is released the first
        # time the re-entering pass reads the refs while the orphan is still
        # alive, so it completes INSIDE the pass. Invariants, whatever the
        # order: the create runs at most once, no ``no_effect`` is recorded
        # for a create that took effect, and it is adopted exactly once. On
        # these bytes the first read DOES happen while the orphan lives
        # (asserted, so the window is really exercised).
        CREATE, MOVE = delivery_module.PREPARATION_CREATE, delivery_module.PREPARATION_HEAD_MOVE
        mission_id, workflow_id, ref = self.stopped_before_preparation()
        key = self.key(workflow_id, ref)
        block = self.blocking_hook(workflow_id)
        self.addCleanup(self.release_and_reap, block, key)
        owner = subprocess.Popen([sys.executable, "-B", self.HELPER, self.config(workflow_id)],
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self.wait_until(lambda: any(isinstance(r.get("pgid"), int) for r in self.ledger(key)),
                        "the owned create child's group row")
        pgid = self.group_of(key, CREATE)
        owner.kill()
        owner.communicate()
        self.assertTrue(self.group_alive(pgid))
        self.assertNotIn(ref, self.lease_refs(workflow_id))
        transport = self.driver.machine.transport
        real_state = transport.source_branch_state
        seen = []

        def classify(lease, reference, oid):
            found = real_state(lease, reference, oid)
            seen.append((found[0], self.group_alive(pgid)))
            if os.path.exists(block):
                self.release_and_reap(block, key)          # completes in-window
            return found
        transport.source_branch_state = classify
        self.addCleanup(setattr, transport, "source_branch_state", real_state)
        first = self.pass_outcome(workflow_id)
        self.release_and_reap(block, key)                  # (a no-op here)
        second = self.pass_outcome(workflow_id)
        self.assertEqual(seen[0], (delivery_module.SOURCE_NOT_STARTED, True),
                         "the window was not exercised")
        self.assertEqual(self.lease_refs(workflow_id)[ref], self.baseline)
        self.assertEqual(len(self.intents(key, CREATE)), 1,
                         "I1: the create was invoked more than once")
        self.assertNotIn(artifacts.ATTACH_NO_EFFECT, self.states(workflow_id),
                         "I2: no_effect recorded for a create that took effect")
        self.assertEqual(len(self.intents(key, MOVE)), 1,
                         "I3: the HEAD move did not run exactly once")
        self.assertEqual(self.head(workflow_id), (ref, self.baseline))
        self.assertEqual(self.states(workflow_id), [
            artifacts.ATTACH_INTENDED, artifacts.ATTACH_PARTIAL, artifacts.ATTACH_ATTACHED])
        self.assertEqual((first.outcome, second.outcome),
                         ("delivery_awaiting_decision", "delivery_awaiting_decision"))
        self.assertEqual(len(self.wf_receipts(workflow_id, artifacts.PROPOSAL_TURN_PREFIX)), 1)


class L3VerificationProvenanceTests(DeliveryCase):
    """Lead gate L3: the delivery's verification is a real, separately
    executed, Runtime-owned run accepted through the proof contract, and
    every surface says exactly that — never more."""

    def test_L3a_real_owned_run_canonical_acceptance_and_truthful_wording(self):
        mission_id, workflow_id = self.prepared()
        entry = self.record(workflow_id)
        receipt = self.wf_receipts(workflow_id, artifacts.VERIFICATION_TURN_PREFIX)[0]
        record = artifacts.load_verification(artifacts.artifact_directory(self.store_dir),
                                             receipt["digest"])
        self.assertEqual(record["settlement"], artifacts.SETTLEMENT_SETTLED)
        engineering = self.evidence(mission_id, "engineering_verified")
        self.assertEqual(len(engineering), 1)
        # the proof contract accepts the RUN, never the engineering result
        self.assertEqual(engineering[0]["content_digest_sha256"], receipt["digest"])
        self.assertNotEqual(engineering[0]["content_digest_sha256"],
                            entry["verified_result"]["digest"])
        self.assertEqual(engineering[0]["kind"],
                         mission_record.EVIDENCE_KIND_VERIFICATION_RECORD)
        self.assertEqual(engineering[0]["provenance"]["principal_kind"],
                         mission_record.PRINCIPAL_KIND_LOCAL_PROCESS_USER)
        card = self.card(mission_id)
        self.assertIn(delivery_module.VERIFICATION_PROVENANCE, card["card"])
        status = self.desk.status(mission_id)
        self.assertEqual(status["proposal"]["verification"]["provenance"],
                         delivery_module.VERIFICATION_PROVENANCE)
        self.assertTrue(self.delivery_decide(mission_id)["ok"])
        decision = self.evidence(mission_id, "delivery_decision")[0]
        self.assertEqual(decision["provenance"]["principal_kind"],
                         mission_record.PRINCIPAL_KIND_CLIENT_CONFIRMATION)
        runtime_module.process_once(self.broker)
        body = self.delivery_transport.created[0][1]
        self.assertIn("machine-observed", body)
        self.assertIn("no independent-party verification is claimed", body)
        self.assertNotIn("Independent verification", body)
        # attest each receipt, then accept the delivery evidence, then close
        state = self.service.get_state(mission_id)["record"]
        attested = [a["sequence"] for a in mission_state.attested_artifacts(state)]
        recorded = self.evidence(mission_id, "delivery_recorded")[0]
        self.assertLess(max(attested), recorded["acceptance"]["sequence"])
        self.assertLess(recorded["acceptance"]["sequence"], state["closure"]["sequence"])
        self.assertEqual(self.service.get_state(mission_id)["progress"],
                         mission_state.PROGRESS_COMPLETED)


class BridgeDeliveryReportTests(DeliveryCase):
    """S-VI owns the bridge's delivery report (S-V reported 'unavailable'
    for any bound delivery): judged by the delivery layer's own validator,
    reported only against the Mission's attested reference."""

    def test_B1_the_bound_delivery_is_reported_against_its_attested_reference(self):
        mission_id, workflow_id = self.decided()
        entry = self.record(workflow_id)
        self.assertEqual(bridge.delivery_report(entry)["value"]["status"], "ABSENT")
        with mock.patch.object(delivery_module.delivery_parent, "attest_validated_receipt",
                               side_effect=Crash("died before attesting")):
            with self.assertRaises(Crash):
                self.delivery_pass()
        record = self.the_delivery()
        state = self.service.get_state(mission_id)["record"]
        self.assertIn("unavailable", bridge.delivery_report(entry, record, state))
        runtime_module.process_once(self.broker)
        record = self.the_delivery()
        state = self.service.get_state(mission_id)["record"]
        report = bridge.delivery_report(entry, record, state)["value"]
        receipt = record["steps"][delivery_auth.STEP_PR_CREATE]["receipt"]
        self.assertEqual(report["status"], "VALID")
        self.assertEqual(report["locator"], receipt["receipt_id"])
        self.assertEqual(report["receipt_digest_sha256"], receipt["receipt_digest_sha256"])
        artifact = [a for a in mission_state.attested_artifacts(state)
                    if a["locator"] == receipt["receipt_id"]][-1]
        self.assertEqual(report["receipt_artifact_id"], artifact["artifact_id"])
        tampered = copy.deepcopy(record)
        tampered["steps"][delivery_auth.STEP_PR_CREATE]["receipt"]["binding"][
            "head_branch"] = "elsewhere"
        invalid = bridge.delivery_report(entry, tampered, state)["value"]
        self.assertEqual(invalid["status"], "INVALID")
        self.assertEqual(invalid["receipt_artifact_id"], artifact["artifact_id"])


class ScriptedChannel(elicitation.ElicitationChannel):
    """The server's elicitation channel with a scripted CLIENT: the form
    request is built by the real ``build_request`` and the client's form
    response classified by the real ``evaluate_response``."""

    def __init__(self, action, confirm=None):
        self.asked = []
        self.claims = []
        self.releases = []

        def elicit(request_id, message, confirm_value, channel):
            request = elicitation.build_request(request_id, "2025-06-18", message,
                                                confirm_value)
            self.asked.append((request_id, message, confirm_value, request))
            channel.consume()
            result = {"action": action}
            if action == "accept":
                result["content"] = {"confirm": confirm or confirm_value}
            answer = elicitation.evaluate_response({"result": result}, confirm_value)
            channel.admit(answer)
            return answer
        super(ScriptedChannel, self).__init__(
            elicit_fn=elicit, claim_fn=lambda: self.claims.append(1) or True,
            release_fn=lambda: self.releases.append(1))


class GrokDeliveryToolTests(DeliveryCase):
    """``di_delivery_decide`` / ``di_delivery_status`` through the real
    controller and relay: the S-I admission, reservation, single-exit and
    best-effort cleanup guarantees; a distinct decision kind and
    reservation from the Mission decision."""

    def controller(self, desk="default"):
        return controller_module.GrokMcpController(
            None, "/repo", mission_service=self.grok_service,
            delivery_desk=self.desk if desk == "default" else desk)

    def decide_tool(self, mission_id, channel, controller=None, client=CLIENT):
        result = (controller or self.controller()).call_tool(
            protocol.TOOL_DELIVERY_DECIDE, {"mission_id": mission_id, "revision": 1},
            ingress=CONNECTOR, elicitation=channel, client_ingress=client)
        return result.structured, result.is_error

    def reservation(self, reserved_id):
        return self.mstore.load()["reservations"].get(reserved_id)

    def test_G1_accept_records_the_decision_and_the_runtime_delivers(self):
        mission_id, workflow_id = self.prepared()
        card = self.card(mission_id)
        channel = ScriptedChannel("accept")
        structured, is_error = self.decide_tool(mission_id, channel)
        self.assertFalse(is_error, structured)
        self.assertEqual(structured["status"], "applied")
        self.assertEqual(structured["decision"], "accept")
        self.assertTrue(structured["decision_recorded"])
        self.assertFalse(structured["cancel_requested"])
        request_id, message, confirm, request = channel.asked[0]
        self.assertEqual(message, card["card"])
        self.assertEqual(confirm, card["confirm_value"])
        self.assertEqual(request["params"]["requestedSchema"]["properties"]["confirm"]["enum"],
                         [card["confirm_value"]])
        self.assertEqual(request_id, structured["delivery_decision_id"])
        evidence = self.evidence(mission_id, "delivery_decision")
        self.assertEqual([e["evidence_id"] for e in evidence], [structured["evidence_id"]])
        self.assertEqual(evidence[0]["operation_id"], structured["delivery_decision_id"])
        self.assertIsNotNone(self.reservation(structured["delivery_decision_id"])["consumed_by"])
        self.assertFalse(channel.holding)
        runtime_module.process_once(self.broker)
        self.assertEqual(self.the_delivery()["phase"], delivery_auth.PHASE_COMPLETE)
        status, is_error = self.status_tool(mission_id)
        self.assertFalse(is_error)
        self.assertEqual(status["delivery"]["pr_url"], self.the_delivery()["pull_request"]["url"])

    def status_tool(self, mission_id, controller=None):
        result = (controller or self.controller()).call_tool(
            protocol.TOOL_DELIVERY_STATUS, {"mission_id": mission_id}, ingress=CONNECTOR)
        return result.structured, result.is_error

    def test_G2_a_wrong_confirmation_records_nothing_and_keeps_the_reservation(self):
        mission_id, workflow_id = self.prepared()
        structured, is_error = self.decide_tool(mission_id,
                                                ScriptedChannel("accept", confirm="0" * 12))
        self.assertTrue(is_error)
        self.assertEqual(structured["problem"], "elicitation_binding_mismatch")
        self.assertFalse(structured["decision_recorded"])
        self.assertIsNone(self.reservation(structured["delivery_decision_id"])["consumed_by"])
        self.assertEqual(self.evidence(mission_id, "delivery_decision"), [])
        self.assertEqual(self.pass_outcome(workflow_id).outcome, "delivery_awaiting_decision")

    def test_G3_a_cancelled_form_records_nothing(self):
        mission_id, workflow_id = self.prepared()
        structured, is_error = self.decide_tool(mission_id, ScriptedChannel("cancel"))
        self.assertTrue(is_error)
        self.assertEqual(structured["status"], "not_recorded")
        self.assertEqual(self.evidence(mission_id, "delivery_decision"), [])
        self.assertFalse(self.service.mission_controls(mission_id)["cancel_requested"])

    def test_G4_a_decline_records_the_cancel_request(self):
        mission_id, workflow_id = self.prepared()
        structured, is_error = self.decide_tool(mission_id, ScriptedChannel("decline"))
        self.assertFalse(is_error, structured)
        self.assertEqual(structured["decision"], "decline")
        self.assertTrue(structured["cancel_requested"])
        self.assertTrue(self.service.mission_controls(mission_id)["cancel_requested"])
        self.assertEqual(self.evidence(mission_id, "delivery_decision"), [])

    def test_G5_unwired_or_unconfirmed_calls_reserve_nothing(self):
        mission_id, workflow_id = self.prepared()
        before = len(self.mstore.load()["reservations"])
        structured, is_error = self.decide_tool(mission_id, ScriptedChannel("accept"),
                                                controller=self.controller(desk=None))
        self.assertTrue(is_error)
        self.assertEqual(structured["reason"], decision_tools.REASON_DELIVERY_NOT_WIRED)
        structured, is_error = self.decide_tool(mission_id, ScriptedChannel("accept"),
                                                client=CONNECTOR)
        self.assertTrue(is_error)
        self.assertEqual(len(self.mstore.load()["reservations"]), before)
        # an oversized card is refused before anything is reserved
        with mock.patch.object(protocol, "MAX_ELICITATION_MESSAGE_CHARS", 100):
            channel = ScriptedChannel("accept")
            structured, is_error = self.decide_tool(mission_id, channel)
        self.assertEqual(structured["problem"], decision_tools.REASON_OVERSIZED)
        self.assertEqual(channel.asked, [])
        self.assertEqual(len(self.mstore.load()["reservations"]), before)

    def test_G6_a_raise_after_the_reservation_reports_the_id_and_releases_the_claim(self):
        mission_id, workflow_id = self.prepared()
        channel = ScriptedChannel("accept")
        with mock.patch.object(self.desk, "accept", side_effect=RuntimeError("boom")):
            structured, is_error = self.decide_tool(mission_id, channel)
        self.assertTrue(is_error)
        self.assertEqual(structured["status"], "store_outcome_uncertain")
        self.assertTrue(structured["delivery_decision_id"].startswith("mo-"))
        self.assertEqual(structured["elicitation_outcome"], "accept")
        self.assertFalse(channel.holding)
        self.assertIs(channel.last_result[0], structured)

    def test_G7_the_status_tool_is_a_pure_read(self):
        mission_id, workflow_id = self.prepared()
        before = self.snapshot()
        status, is_error = self.status_tool(mission_id)
        self.assertFalse(is_error)
        self.assertEqual(status["status"], "read")
        self.assertTrue(status["proposal"]["current"])
        self.assertEqual(self.snapshot(), before)
        refused, is_error = self.status_tool(mission_id,
                                             controller=self.controller(desk=None))
        self.assertTrue(is_error)

    def test_G8_the_mission_decision_never_records_a_delivery_decision(self):
        mission_id, workflow_id = self.prepared()
        before = self.evidence(mission_id, "delivery_decision")
        result = self.controller().call_tool(
            protocol.TOOL_MISSION_DECIDE, {"mission_id": mission_id, "revision": 1},
            ingress=CONNECTOR, elicitation=ScriptedChannel("accept"),
            client_ingress=CLIENT)
        self.assertEqual(self.evidence(mission_id, "delivery_decision"), before)
        self.assertEqual(self.deliveries(), {})
        del result


class VerificationProducerTests(unittest.TestCase):
    """The Runtime-owned verification producer, directly: owned process,
    complete log, real exit status and timing, content-addressed record."""

    def setUp(self):
        import tempfile
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = os.path.realpath(self.temp.name)
        self.lease = os.path.join(self.base, "lease")
        os.makedirs(self.lease)
        self.store = os.path.join(self.base, "store")
        os.makedirs(self.store)
        self.scopes = os.path.join(self.base, "scopes")
        self.now = [100.0]

    def produce(self, argv):
        from target_runtime import verification
        return verification.produce(
            argv, self.lease, "wf-m-" + "a" * 26, "mn-" + "b" * 32, 1, self.base,
            "c" * 64, "d" * 40, self.store, lambda: self.now[0], scope_base=self.scopes)

    def test_V1_a_green_run_is_captured_completely_and_owned(self):
        from target_runtime import process_ownership
        digest, record = self.produce([
            sys.executable, "-c",
            "import os, sys; print('out', os.getcwd()); print('err', file=sys.stderr)"])
        self.assertEqual(record["exit_status"], 0)
        self.assertEqual(record["settlement"], artifacts.SETTLEMENT_SETTLED)
        self.assertEqual(record["candidate_identity_digest_sha256"], "c" * 64)
        self.assertEqual(record["base_oid"], "d" * 40)
        directory = artifacts.artifact_directory(self.store)
        self.assertEqual(artifacts.load_verification(directory, digest), record)
        log = artifacts.load_bytes(directory, record["log_sha256"], artifacts.LOG_SUFFIX)
        self.assertIn(("out %s" % self.lease).encode("utf-8"), log)
        self.assertIn(b"err", log)
        self.assertEqual(len(log), record["log_bytes"])
        for name in os.listdir(directory):
            self.assertEqual(os.stat(os.path.join(directory, name)).st_mode & 0o777, 0o600)
        self.assertFalse([n for n in os.listdir(directory) if n.startswith(".partial")])
        name = process_ownership.scope_name(process_ownership.OWNER_TYPE_WORKFLOW,
                                            self.base, "wf-m-" + "a" * 26,
                                            "verification")
        assignment, reason = process_ownership.read_assignment(name, base=self.scopes)
        self.assertIsNone(reason)
        self.assertEqual(assignment["owner_id"], "wf-m-" + "a" * 26)

    def test_V2_a_failing_run_is_recorded_truthfully(self):
        digest, record = self.produce([sys.executable, "-c", "import sys; sys.exit(5)"])
        self.assertEqual(record["exit_status"], 5)
        self.assertEqual(record["log_bytes"], 0)

    def test_V3_a_tampered_log_or_record_is_refused_on_read(self):
        digest, record = self.produce([sys.executable, "-c", "print('x')"])
        directory = artifacts.artifact_directory(self.store)
        path = os.path.join(directory, record["log_sha256"] + artifacts.LOG_SUFFIX)
        with open(path, "wb") as handle:
            handle.write(b"y\n")
        with self.assertRaises(artifacts.ArtifactError) as caught:
            artifacts.load_verification(directory, digest)
        self.assertEqual(caught.exception.problem, artifacts.PROBLEM_ARTIFACT_TAMPERED)


class R28CurrentGroupRouteTests(DeliveryCase):
    """Task 8 R28-1 through the REAL delivery pass and the REAL producer
    (``verification.produce``): its command's WAIT, its REAP and its ARTIFACT.
    The producer's spawn is HELD — its wait observes the command's exit without
    collecting it — so the reap acts while the group's number is still this
    process's own; and a group whose CURRENT ownership is not proven is never
    signalled and never recorded settled. Where a case says MODELED, an OS answer
    is replaced IN MEMORY (CONTROL-FLOW evidence, not a real-process
    observation): no host pid or group is reused, and nothing unrelated is
    signalled, to reproduce it. Effects are counted at their calls."""

    # Borrowed from R19 — every helper these cases call, and every helper those
    # call in turn (``seams`` calls ``collect``).
    _R19 = R19VerificationLifecycleTests
    ATTEMPT = _R19.ATTEMPT
    ready = _R19.ready
    seams = _R19.seams
    collect = staticmethod(_R19.collect)
    attempts = _R19.attempts
    verification_receipts = _R19.verification_receipts
    group_members = _R19.group_members
    members_after = _R19.members_after
    MODELED_START = "Mon Jan  1 00:00:00 2099"

    def descendants(self, marker, count, status=0):
        """A verification argv that starts ``count`` same-group sleepers, writes
        their pids to ``marker``, and EXITS with ``status``."""
        return [sys.executable, "-c",
                "import subprocess, sys; pids = [subprocess.Popen(['sleep', '3600']).pid"
                " for _ in range(%d)]; open(sys.argv[1], 'w').write(' '.join(map(str, pids)));"
                " sys.exit(%d)" % (count, status), marker]

    def booked(self, marker):
        """The descendants' pids (from ``marker``) and their start times read NOW;
        a FIXTURE cleanup signals each ONLY while its pid AND start still match."""
        from target_runtime import spawn_stamp
        with open(marker) as handle:
            kids = sorted(int(value) for value in handle.read().split())
        starts = {kid: spawn_stamp.leader_start_time(kid) for kid in kids}

        def settle_kids():
            for kid, started in starts.items():
                if started is not None and spawn_stamp.leader_start_time(kid) == started:
                    os.kill(kid, signal.SIGKILL)
        self.addCleanup(settle_kids)
        return kids

    @staticmethod
    def zombie(pid):
        listed = subprocess.run(["ps", "-o", "stat=", "-p", str(pid)],
                                capture_output=True, text=True).stdout.strip()
        return listed.startswith("Z")

    def counted(self, model=None):
        """``(sent, patches)``: every signal but signal 0 is RECORDED at its call
        and DELIVERED, unless ``model(call, target, sig)`` answers in its place
        (a MODELED OS answer: it returns what the call returns, or raises)."""
        sent, real_killpg, real_kill = [], os.killpg, os.kill

        def killpg(pgid, sig):
            if sig != 0:
                sent.append(("killpg", pgid, sig))
            if model is not None:
                answer = model("killpg", pgid, sig)
                if answer is not None:
                    return answer()
            return real_killpg(pgid, sig)

        def kill(pid, sig):
            if sig != 0:
                sent.append(("kill", pid, sig))
            if model is not None:
                answer = model("kill", pid, sig)
                if answer is not None:
                    return answer()
            return real_kill(pid, sig)
        return sent, [mock.patch.object(os, "killpg", killpg), mock.patch.object(os, "kill", kill)]

    def the_record(self, workflow_id):
        [receipt] = self.verification_receipts(workflow_id)
        return receipt["digest"], artifacts.load_verification(
            artifacts.artifact_directory(self.store_dir), receipt["digest"])

    #: How long the stalled stand-in observer sleeps; it ends by itself.
    STALL = 6

    def failing_observers(self):
        """The production membership read's observer REPLACED by a stand-in that
        STALLS (sleeps ``STALL`` s; a Python child of THIS test) and whose OWN
        cleanup FAILS — MODELED (control-flow evidence): its handle's ``kill`` and
        ``wait`` and its stdout's ``close`` raise ``OSError`` without reaching the
        child. ``(observers, [argv patch, Popen patch])``; ``observers_ended``
        then waits for each (it ends by itself) and closes its pipes."""
        argv = (sys.executable, "-c", "import time; time.sleep(%d)" % self.STALL)
        observers, real = [], subprocess.Popen

        class Unclosable(object):
            def __init__(self, stream):
                self.stream = stream

            def fileno(self):
                return self.stream.fileno()

            @property
            def closed(self):
                return self.stream.closed

            def close(self):
                raise OSError(errno.EIO, "modeled: the observer's pipe does not close")

        class Failing(real):
            def kill(created):
                raise OSError(errno.EPERM, "modeled: the observer's kill fails")

            def wait(created, timeout=None):
                if not getattr(created, "released", False):
                    raise OSError(errno.EIO, "modeled: the observer's wait fails")
                return real.wait(created, timeout)

        def popen(args, *rest, **kwargs):
            if tuple(args) != argv:
                return real(args, *rest, **kwargs)
            created = Failing(args, *rest, **kwargs)
            created.stdout = Unclosable(created.stdout)
            observers.append(created)
            return created
        self.addCleanup(self.observers_ended, observers)
        return observers, [mock.patch.object(process_ownership, "_MEMBERSHIP_ARGV", argv),
                           mock.patch.object(subprocess, "Popen", popen)]

    def observers_ended(self, observers):
        """FIXTURE cleanup: each recorded observer is WAITED for, bounded — never
        signalled here (each ends by itself within ``STALL`` seconds; its MODELED
        failure stops answering first) — and its pipes are closed."""
        for observer in observers:
            observer.released = True
            if observer.returncode is None:
                observer.wait(timeout=self.STALL + 10)
            for stream in (observer.stdout, observer.stderr):
                stream = getattr(stream, "stream", stream)
                if stream is not None and not stream.closed:
                    stream.close()

    def test_R28_2a_a_MODELED_reuse_after_the_wait_signals_NOTHING_and_records_UNSETTLED(self):
        """THE R28-1 SHAPE, through the production wait -> reap -> artifact path.
        The command exits; right after its wait its leader is COLLECTED out of
        band (the number released), and the number is MODELED as held by a
        DIFFERENT live incarnation: signal 0 answers alive, and its leader's start
        time is another — the owned root's proof CONTRADICTS the ledger's number.
        ZERO signals (counted; the modeled group is never delivered one). The
        verification is recorded with its exit status and its process UNSETTLED
        — never settled — and the attempt says so; it is never re-run."""
        from target_runtime import spawn_stamp
        mission_id, workflow_id = self.ready()
        state = {}

        def collected_then_reused(process):
            real_wait = process.wait

            def wait(*args, **kwargs):
                status = real_wait(*args, **kwargs)       # OBSERVED: held, not collected
                process_ownership._collect_leader(process)  # collected out of band
                state["pgid"] = process.pid                 # ... and the number REUSED (modeled)
                return status
            process.wait = wait

        def model(call, target, sig):
            if state.get("pgid") == target:
                return lambda: None                       # alive / "delivered", never really
            return None
        real_start = spawn_stamp.leader_start_time
        sent, patches = self.counted(model)
        with self.seams(on_spawn=collected_then_reused) as calls, patches[0], patches[1], \
                mock.patch.object(spawn_stamp, "leader_start_time",
                                  lambda pid: (self.MODELED_START if pid == state.get("pgid")
                                               else real_start(pid))):
            self.pass_outcome(workflow_id)
        self.assertEqual(calls, {"produce": 1, "spawn": 1})
        self.assertIn("pgid", state, "the wait was never reached")
        self.assertEqual(sent, [])                        # ZERO signals
        digest, record = self.the_record(workflow_id)
        self.assertEqual((record["exit_status"], record["settlement"]),
                         (0, artifacts.SETTLEMENT_UNSETTLED))   # NEVER "settled"
        claim, settlement = self.attempts(workflow_id)
        self.assertEqual(settlement, "%s 1 settled: returned — record %s (its process %s)"
                         % (self.ATTEMPT, digest, artifacts.SETTLEMENT_UNSETTLED))
        with self.seams() as calls:                       # the model withdrawn
            self.pass_outcome(workflow_id)
        self.assertEqual(calls, {"produce": 0, "spawn": 0})   # never re-run

    def test_R28_2b_descendants_the_command_left_are_reaped_POSITIVELY_and_SETTLED(self):
        """The command leaves TWO same-group descendants and exits with status 3.
        Its wait OBSERVES the exit without collecting it; the reap signals its
        group EXACTLY ONCE (counted and delivered); both descendants are gone and
        the leader is collected AFTER the signal; the record carries the
        command's TRUE status (3 — never a fabricated 0) and its process
        SETTLED."""
        marker = os.path.join(self.base, "r28-2b-descendants")
        mission_id, workflow_id = self.ready(argv=self.descendants(marker, 2, status=3))
        groups = []
        sent, patches = self.counted()
        with self.seams(on_spawn=lambda process: groups.append(process.pid)) as calls, \
                patches[0], patches[1]:
            self.pass_outcome(workflow_id)
        self.assertEqual(calls, {"produce": 1, "spawn": 1})
        [pgid] = groups
        kids = self.booked(marker)
        self.assertEqual(len(kids), 2)
        self.assertEqual(sent, [("killpg", pgid, signal.SIGKILL)])   # EXACTLY one
        self.assertEqual(self.members_after(pgid, 0), [])
        self.assertFalse(self.zombie(pgid))
        digest, record = self.the_record(workflow_id)
        self.assertEqual((record["exit_status"], record["settlement"]),
                         (3, artifacts.SETTLEMENT_SETTLED))
        claim, settlement = self.attempts(workflow_id)
        self.assertEqual(settlement, "%s 1 settled: returned — record %s (its process %s)"
                         % (self.ATTEMPT, digest, artifacts.SETTLEMENT_SETTLED))

    def test_R28_2c_an_UNAVAILABLE_proof_at_the_reap_RETAINS_and_recovery_settles_WITHOUT_replay(
            self):
        """The CURRENT-group proof cannot be read when the pass reaps — MODELED
        (control-flow evidence): signal 0 to the held leader's pid answers EIO,
        for that one read. ZERO signals; the record is UNSETTLED; the leader stays
        UNCOLLECTED (a zombie: its number held) and its descendant runs —
        retained, never settled. The next pass BLOCKS (the group not proven
        settled: no producer, no spawn). Startup recovery then proves the group
        by its owned root (the held leader's start time is the recorded one) and
        settles it with ONE SIGKILL; every later pass runs neither seam, and the
        run's record is never rewritten as settled."""
        marker = os.path.join(self.base, "r28-2c-descendants")
        mission_id, workflow_id = self.ready(argv=self.descendants(marker, 1))
        state = {}

        def unreadable_once(process):
            real_wait = process.wait

            def wait(*args, **kwargs):
                status = real_wait(*args, **kwargs)
                state["pgid"] = process.pid
                state["armed"] = True
                return status
            process.wait = wait

        def model(call, target, sig):
            if call == "kill" and sig == 0 and state.get("armed") and target == state["pgid"]:
                state["armed"] = False

                def unreadable():
                    raise OSError(errno.EIO, "modeled: unreadable")
                return unreadable
            return None
        sent, patches = self.counted(model)
        with self.seams(on_spawn=unreadable_once) as calls, patches[0], patches[1]:
            self.pass_outcome(workflow_id)
        self.assertEqual(calls, {"produce": 1, "spawn": 1})
        pgid = state["pgid"]
        kids = self.booked(marker)
        self.assertEqual(sent, [])                        # ZERO signals
        self.assertFalse(state["armed"], "the proof was never read at the reap")
        digest, record = self.the_record(workflow_id)
        self.assertEqual((record["exit_status"], record["settlement"]),
                         (0, artifacts.SETTLEMENT_UNSETTLED))
        self.assertTrue(self.zombie(pgid))               # RETAINED: uncollected, held
        self.assertEqual(sorted(int(pid) for pid in self.group_members(pgid)), kids)
        unsettled = ("delivery_blocked", delivery_module.PROBLEM_VERIFICATION_UNSETTLED,
                     "the verification process group was not proven settled")
        with self.seams() as calls:
            outcome = self.pass_outcome(workflow_id)
        self.assertEqual(calls, {"produce": 0, "spawn": 0})
        self.assertEqual((outcome.outcome, outcome.problem, outcome.detail), unsettled)
        self.assertTrue(self.zombie(pgid))               # still retained
        sent, patches = self.counted()
        with patches[0], patches[1]:
            runtime_module.recover_inherited_processes(self.store_dir)
        self.assertEqual(sent, [("killpg", pgid, signal.SIGKILL)])   # EXACTLY one
        self.assertEqual(self.members_after(pgid, 0), [])
        self.assertFalse(self.zombie(pgid))
        for _ in range(2):
            with self.seams() as calls:
                outcome = self.pass_outcome(workflow_id)
            self.assertEqual(calls, {"produce": 0, "spawn": 0})   # never replayed
            # the run's record is never rewritten: it stays "not proven settled"
            self.assertEqual((outcome.outcome, outcome.problem, outcome.detail), unsettled)

    def test_R28_2d_an_observer_FAILURE_at_the_disarm_settles_POST_START_never_NOT_STARTED(self):
        """The one case VL6a and VL6e cannot reach: a failure at the OBSERVER /
        DISARM step, which runs in ``produce``'s ``finally`` OUTSIDE its wait and
        reap captures (an exception escaping there would fall to the Broker's
        "could not start" handler and settle the attempt NOT STARTED). As in 2c the
        proof at the reap is unreadable (MODELED: EIO once), so the reap refuses
        and the disarm reads the group's membership — and that observer STALLS and
        its OWN cleanup FAILS (``failing_observers``: kill, wait and pipe close
        raise). Nothing escapes ``produce``: the attempt settles POST-START —
        ``returned``, its record stored (exit 0, UNSETTLED) — never ``not-started``
        / "could not start"; ZERO signals; the disarm's reason UNAVAILABLE with the
        observer's end UNPROVEN; the leader retained (a zombie, held) and its
        descendant running. Recovery then settles it with ONE SIGKILL."""
        marker = os.path.join(self.base, "r28-2d-descendants")
        mission_id, workflow_id = self.ready(argv=self.descendants(marker, 1))
        state, decided = {}, []
        real_disarm = process_ownership.disarm_hold

        def unreadable_once(process):
            real_wait = process.wait

            def wait(*args, **kwargs):
                status = real_wait(*args, **kwargs)
                state["pgid"] = process.pid
                state["armed"] = True
                return status
            process.wait = wait

        def model(call, target, sig):
            if call == "kill" and sig == 0 and state.get("armed") and target == state["pgid"]:
                state["armed"] = False

                def unreadable():
                    raise OSError(errno.EIO, "modeled: unreadable")
                return unreadable
            return None

        def disarm(process):
            decided.append(real_disarm(process))
            return decided[-1]
        observers, observing = self.failing_observers()
        sent, patches = self.counted(model)
        with self.seams(on_spawn=unreadable_once) as calls, patches[0], patches[1], \
                observing[0], observing[1], \
                mock.patch.object(process_ownership, "_OBSERVER_SECONDS", 0.5), \
                mock.patch.object(process_ownership, "disarm_hold", disarm):
            self.pass_outcome(workflow_id)
        pgid = state["pgid"]
        kids = self.booked(marker)                       # its FIXTURE cleanup, booked first
        self.assertEqual(calls, {"produce": 1, "spawn": 1})
        attempts = self.attempts(workflow_id)
        self.assertEqual(len(attempts), 2, attempts)                 # a claim, a settlement
        returned = "%s 1 settled: %s — record " % (
            self.ATTEMPT, broker_module.VERIFICATION_ATTEMPT_RETURNED)
        self.assertTrue(attempts[1].startswith(returned), attempts[1])   # POST-START
        digest, record = self.the_record(workflow_id)
        self.assertEqual(attempts[1], "%s 1 settled: %s — record %s (its process %s)" % (
            self.ATTEMPT, broker_module.VERIFICATION_ATTEMPT_RETURNED, digest,
            artifacts.SETTLEMENT_UNSETTLED))
        self.assertEqual((record["exit_status"], record["settlement"]),
                         (0, artifacts.SETTLEMENT_UNSETTLED))
        self.assertEqual(sent, [])                                   # ZERO signals
        self.assertEqual((len(observers), len(decided)), (1, 1))
        self.assertTrue(decided[0].startswith("kept held: whether anything lives in its group"
                                              " is UNAVAILABLE"), decided[0])
        self.assertIn("its end could NOT be observed (OSError): UNPROVEN", decided[0])
        self.assertTrue(self.zombie(pgid))                           # RETAINED: held
        self.assertEqual(sorted(int(pid) for pid in self.group_members(pgid)), kids)
        sent, patches = self.counted()
        with patches[0], patches[1]:
            runtime_module.recover_inherited_processes(self.store_dir)
        self.assertEqual(sent, [("killpg", pgid, signal.SIGKILL)])   # EXACTLY one
        self.assertEqual(self.members_after(pgid, 0), [])
        self.assertFalse(self.zombie(pgid))


if __name__ == "__main__":
    unittest.main()
