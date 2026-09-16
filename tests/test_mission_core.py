"""Behavioral tests for the Dodging Infinity Mission Core (``mission``).

Sections:
  A  package boundary (fresh-subprocess import closure, vocabulary)
  B  record: identity grammar, proposal schema, content-only digest,
     lifecycle table (approved is not running)
  C  store: single atomic document, fail-closed load, caps
  D  service: propose / get / edit / decisions / authorization / ledger
     (success scenarios A-F and the fail-closed list)
  E  trust separation: single issuance point, truthful provenance,
     model / orchestration / worker / capability / delivery paths cannot
     mint human authority
  F  P1-A6 parent-authority seam in pr_delivery; Task 7 Stage 2 receipt
     attestation: the seam's validating consumer, its call-path
     confinement pin with planted probes, and the consumer behaviour
     (validate first, refusals leave nothing, evidence not authority,
     structural validity is not success)
"""

import ast
import copy
import io
import json
import os
import stat
import subprocess
import sys
import tempfile
import threading
import tokenize
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from mission import record as mission_record  # noqa: E402

PROVIDER_ROOTS = (
    "telegram_operator", "grok_mcp", "operator_session", "codex_gateway",
    "pr_delivery", "target_runtime", "capability", "worker",
    "durable_execution", "herdr", "herdctl", "subprocess",
)


def proposal(**overrides):
    base = {
        "objective": "Investigate and resolve the flaky readiness probe",
        "target_context": "control repository, readiness subsystem",
        "repository_url": "https://github.com/Example/Repo",
        "requested_scope": "readiness probe and its tests",
        "requested_action_scope": [
            mission_record.ACTION_SCOPE_ENGINEERING_CHANGE,
            mission_record.ACTION_SCOPE_REPOSITORY_READ,
        ],
        "requested_delivery_target": mission_record.DELIVERY_TARGET_GITHUB_PR,
    }
    base.update(overrides)
    return base


# ====================================================================
# A. Package boundary
# ====================================================================


class APackageBoundaryTests(unittest.TestCase):

    def test_A1_fresh_subprocess_import_loads_no_provider(self):
        code = (
            "import sys\n"
            "import mission\n"
            "import mission.record, mission.decision, mission.authorization\n"
            "import mission.store, mission.service\n"
            "roots = %r\n"
            "bad = sorted(name for name in sys.modules\n"
            "             if name.split('.')[0] in roots)\n"
            "print('\\n'.join(bad))\n"
            "print('LOADED', 'mission.service' in sys.modules)\n"
            "sys.exit(1 if bad else 0)\n"
        ) % (PROVIDER_ROOTS,)
        probe = subprocess.run(
            [sys.executable, "-c", code], cwd=str(REPO_ROOT),
            capture_output=True, text=True,
        )
        self.assertEqual(probe.returncode, 0, (probe.stdout, probe.stderr))
        self.assertIn("LOADED True", probe.stdout)

    def test_A2_workflow_authority_submodule_import_loads_no_provider(self):
        # Route A of amendment A-1: the package __init__ is lazy, so
        # importing one neutral submodule loads no provider.
        code = (
            "import sys\n"
            "import workflow_authority.atomic\n"
            "import workflow_authority.digest\n"
            "import workflow_authority.canonical\n"
            "bad = sorted(name for name in sys.modules\n"
            "             if name.split('.')[0] == 'telegram_operator'\n"
            "             or name in ('workflow_authority.store',\n"
            "                         'workflow_authority.record'))\n"
            "print('\\n'.join(bad))\n"
            "sys.exit(1 if bad else 0)\n"
        )
        probe = subprocess.run(
            [sys.executable, "-c", code], cwd=str(REPO_ROOT),
            capture_output=True, text=True,
        )
        self.assertEqual(probe.returncode, 0, (probe.stdout, probe.stderr))

    def test_A3_workflow_authority_public_names_still_resolve(self):
        code = (
            "import workflow_authority\n"
            "from workflow_authority import WorkflowStore, StoreError\n"
            "from workflow_authority import MAX_WORKFLOW_RECORDS\n"
            "from workflow_authority import validate_record\n"
            "from workflow_authority import record as r\n"
            "from workflow_authority.digest import json_digest\n"
            "assert workflow_authority.DigestError is not None\n"
            "assert workflow_authority.WORKFLOW_SCHEMA_VERSION\n"
            "assert workflow_authority.validate_transition\n"
            "assert workflow_authority.RecordError\n"
            "assert sorted(workflow_authority.__all__) == sorted(set(\n"
            "    dir(workflow_authority)) & set(workflow_authority.__all__))\n"
            "assert workflow_authority.WorkflowStore is r.__dict__.get(\n"
            "    'WorkflowStore', workflow_authority.WorkflowStore)\n"
            "try:\n"
            "    workflow_authority.no_such_name\n"
            "except AttributeError:\n"
            "    pass\n"
            "else:\n"
            "    raise SystemExit('missing AttributeError')\n"
            "print('OK')\n"
        )
        probe = subprocess.run(
            [sys.executable, "-c", code], cwd=str(REPO_ROOT),
            capture_output=True, text=True,
        )
        self.assertEqual(probe.returncode, 0, (probe.stdout, probe.stderr))
        self.assertIn("OK", probe.stdout)

    def test_A4_atomic_primitives_are_shared_not_copied(self):
        from workflow_authority import atomic, store
        self.assertIs(store.atomic_write_json, atomic.atomic_write_json)
        self.assertIs(store.exclusive_store_lock, atomic.exclusive_store_lock)

    def test_A5_mission_imports_only_the_allowed_roots(self):
        allowed = {
            "abc", "collections", "contextlib", "copy", "dataclasses",
            "json", "os", "re", "secrets", "stat", "threading", "typing",
            "workflow_authority", "mission",
        }
        allowed_wa = {
            "workflow_authority.atomic", "workflow_authority.digest",
            "workflow_authority.canonical",
        }
        files = sorted((REPO_ROOT / "mission").glob("*.py"))
        self.assertTrue(files)
        for path in files:
            for node in ast.walk(ast.parse(path.read_text())):
                if isinstance(node, ast.Import):
                    names = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom):
                    self.assertEqual(node.level, 0, path)
                    module = node.module or ""
                    if module == "workflow_authority":
                        names = [module + "." + a.name for a in node.names]
                    else:
                        names = [module]
                else:
                    continue
                for name in names:
                    root = name.split(".")[0]
                    self.assertIn(root, allowed, (path, name))
                    if root == "workflow_authority":
                        self.assertIn(name, allowed_wa, (path, name))
                    self.assertNotEqual(root, "pr_delivery", path)


# ====================================================================
# B. Record
# ====================================================================


class BRecordTests(unittest.TestCase):

    def test_B1_identity_grammar_is_prefix_plus_32_hex(self):
        good = "mn-" + "0" * 32
        self.assertIsNone(mission_record.id_problem(good, "mn"))
        for bad in ("mn-" + "0" * 31, "mn-" + "G" * 32, "di-" + "0" * 32,
                    "MN-" + "0" * 32, "", None, 7, "mq-" + "0" * 32):
            self.assertIsNotNone(mission_record.id_problem(bad, "mn"), bad)
        # Every id kind has its own distinct prefix, and none is the
        # Grok transport prefix.
        prefixes = (
            mission_record.MISSION_ID_PREFIX,
            mission_record.REQUEST_ID_PREFIX,
            mission_record.DECISION_ID_PREFIX,
            mission_record.AUTHORIZATION_ID_PREFIX,
            mission_record.LEDGER_ENTRY_ID_PREFIX,
        )
        self.assertEqual(len(set(prefixes)), len(prefixes))
        self.assertNotIn("di", prefixes)

    def test_B2_mission_id_is_accepted_by_the_delivery_id_grammar(self):
        # This is what lets a Mission id sit in pr_delivery's existing
        # optional ``mission.workflow_id`` slot with no schema change.
        from pr_delivery import authorization as pr_authorization
        minted = mission_record.mint_id(mission_record.MISSION_ID_PREFIX)
        self.assertEqual(len(minted), 35)
        pr_authorization._require_id(minted, "probe")

    def test_B3_proposal_validation_is_closed_and_refuses_not_repairs(self):
        clean = mission_record.validate_proposal(proposal())
        self.assertEqual(clean["repository_url"],
                         "https://github.com/Example/Repo")
        self.assertEqual(clean["requested_action_scope"], sorted(
            clean["requested_action_scope"]
        ))
        cases = {
            "unknown key": proposal(extra=1),
            "missing key": {k: v for k, v in proposal().items()
                            if k != "objective"},
            "empty objective": proposal(objective=""),
            "objective too long": proposal(
                objective="x" * (mission_record.MAX_OBJECTIVE_CHARS + 1)
            ),
            "non-canonical url": proposal(
                repository_url="https://github.com/Example/Repo/"
            ),
            "issue url where repository expected": proposal(
                repository_url="https://github.com/Example/Repo/issues/3"
            ),
            "unknown action scope": proposal(
                requested_action_scope=["deploy_everything"]
            ),
            "duplicate action scope": proposal(requested_action_scope=[
                mission_record.ACTION_SCOPE_REPOSITORY_READ,
                mission_record.ACTION_SCOPE_REPOSITORY_READ,
            ]),
            "empty action scope": proposal(requested_action_scope=[]),
            "unknown delivery target": proposal(
                requested_delivery_target="npm_publish"
            ),
            "bool where str": proposal(target_context=True),
        }
        for label, bad in cases.items():
            with self.assertRaises(mission_record.MissionError, msg=label) as ctx:
                mission_record.validate_proposal(bad)
            self.assertTrue(ctx.exception.problem.startswith("mission_"),
                            (label, ctx.exception.problem))
        # Repository identity may be absent (None) when not applicable.
        without = mission_record.validate_proposal(proposal(repository_url=None))
        self.assertIsNone(without["repository_url"])
        no_delivery = mission_record.validate_proposal(
            proposal(requested_delivery_target=None)
        )
        self.assertIsNone(no_delivery["requested_delivery_target"])

    def test_B4_proposal_digest_is_content_only_and_deterministic(self):
        first = mission_record.proposal_digest(proposal())
        second = mission_record.proposal_digest(dict(reversed(list(
            proposal().items()
        ))))
        self.assertEqual(first, second)
        self.assertEqual(len(first), 64)
        self.assertNotEqual(
            first, mission_record.proposal_digest(proposal(objective="other"))
        )
        # Order of the action-scope list does not change the digest.
        reordered = proposal(requested_action_scope=list(reversed(
            proposal()["requested_action_scope"]
        )))
        self.assertEqual(first, mission_record.proposal_digest(reordered))

    def test_B5_lifecycle_declares_future_states_but_wires_only_decisions(self):
        states = mission_record.MISSION_STATES
        for name in ("AWAITING_DECISION", "AUTHORIZED", "DENIED", "RUNNING",
                     "BLOCKED", "COMPLETED", "CLOSED", "CANCELLED"):
            self.assertIn(name, states)
        transitions = mission_record.ALLOWED_TRANSITIONS
        self.assertEqual(set(transitions), set(states))
        self.assertEqual(transitions["AWAITING_DECISION"],
                         frozenset(("AUTHORIZED", "DENIED")))
        self.assertEqual(transitions["AUTHORIZED"],
                         frozenset(("AWAITING_DECISION",)))
        self.assertEqual(transitions["DENIED"],
                         frozenset(("AWAITING_DECISION",)))
        targets = set().union(*transitions.values())
        for unwired in ("RUNNING", "BLOCKED", "COMPLETED", "CLOSED",
                        "CANCELLED"):
            self.assertNotIn(unwired, targets)
            self.assertEqual(transitions[unwired], frozenset())
        self.assertNotIn("RUNNING", transitions["AUTHORIZED"])
        with self.assertRaises(mission_record.MissionError) as ctx:
            mission_record.validate_transition("AUTHORIZED", "RUNNING")
        self.assertEqual(ctx.exception.problem,
                         mission_record.PROBLEM_INVALID_TRANSITION)

    def test_B6_no_product_code_path_sets_running(self):
        # Approved does not imply running: the RUNNING literal appears in
        # the state vocabulary declaration only, never as an assigned
        # state anywhere in the package.
        for path in sorted((REPO_ROOT / "mission").glob("*.py")):
            tree = ast.parse(path.read_text())
            for node in ast.walk(tree):
                if isinstance(node, ast.Assign) and isinstance(
                    node.value, ast.Constant
                ) and node.value.value == "RUNNING":
                    targets = [getattr(t, "id", None) for t in node.targets]
                    self.assertEqual(targets, ["STATE_RUNNING"], path)
                if isinstance(node, ast.Subscript) and isinstance(
                    node.value, ast.Name
                ):
                    continue


# ====================================================================
# C. Store
# ====================================================================


def make_context(principal_ref="1", transport="grok_mcp", subject=None):
    return mission_record.AuthenticatedContext(
        transport=transport,
        principal_kind=mission_record.PRINCIPAL_KIND_CONNECTOR_CREDENTIAL,
        principal_ref=principal_ref,
        configured_subject=subject,
    )


class StoreFixture(unittest.TestCase):

    def setUp(self):
        from mission import store as mission_store
        self.mission_store = mission_store
        self.tmp = tempfile.TemporaryDirectory()
        self.directory = os.path.join(self.tmp.name, "protected")
        self.store = mission_store.MissionStore(self.directory)

    def tearDown(self):
        self.tmp.cleanup()

    def read_bytes(self):
        with open(self.store.path, "rb") as handle:
            return handle.read()

    def write_raw(self, text):
        os.makedirs(self.directory, mode=0o700, exist_ok=True)
        with open(self.store.path, "w") as handle:
            handle.write(text)
        os.chmod(self.store.path, 0o600)


class CStoreTests(StoreFixture):

    def test_C1_fresh_store_is_one_document_written_mode_600(self):
        document = self.store.load()
        self.assertEqual(document, self.mission_store.default_document())
        self.assertEqual(set(document), set(self.mission_store.TOP_LEVEL_KEYS))
        self.assertEqual(document["missions"], {})
        self.assertEqual(document["authorizations"], {})
        self.assertEqual(document["authority_ledger"], [])
        self.assertEqual(document["reservations"], {})
        self.store.save(document)
        self.assertEqual(stat.S_IMODE(os.stat(self.store.path).st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(os.stat(self.directory).st_mode), 0o700)
        self.assertEqual(json.loads(self.read_bytes()), document)
        # Registry and ledger live in the SAME file: one os.replace.
        self.assertEqual(
            sorted(os.listdir(self.directory)), ["missions.json"]
        )

    def test_C2_load_fails_closed_and_never_reinitializes(self):
        cases = {
            "bad json": "{not json",
            "not an object": "[1, 2]",
            "unknown version": json.dumps({
                "mission_store_schema_version": 99, "missions": {},
                "authorizations": {}, "authority_ledger": [],
                "reservations": {},
            }),
            "unknown top key": json.dumps({
                "mission_store_schema_version": 1, "missions": {},
                "authorizations": {}, "authority_ledger": [],
                "reservations": {}, "extra": 1,
            }),
            "missing top key": json.dumps({
                "mission_store_schema_version": 1, "missions": {},
            }),
            "invalid mission record": json.dumps({
                "mission_store_schema_version": 1,
                "missions": {"mn-" + "0" * 32: {"garbage": True}},
                "authorizations": {}, "authority_ledger": [],
                "reservations": {},
            }),
            "invalid ledger entry": json.dumps({
                "mission_store_schema_version": 1, "missions": {},
                "authorizations": {}, "authority_ledger": [{"kind": "X"}],
                "reservations": {},
            }),
        }
        for label, text in cases.items():
            self.write_raw(text)
            before = self.read_bytes()
            with self.assertRaises(self.mission_store.MissionStoreError,
                                   msg=label) as ctx:
                self.store.load()
            self.assertEqual(ctx.exception.problem,
                             self.mission_store.PROBLEM_STORE_UNREADABLE, label)
            self.assertEqual(self.read_bytes(), before, label)
        # Group/other access is refused too.
        self.write_raw(json.dumps(self.mission_store.default_document()))
        os.chmod(self.store.path, 0o640)
        with self.assertRaises(self.mission_store.MissionStoreError):
            self.store.load()
        os.chmod(self.store.path, 0o600)
        self.assertEqual(self.store.load(),
                         self.mission_store.default_document())

    def test_C3_save_validates_before_touching_the_filesystem(self):
        good = self.mission_store.default_document()
        self.store.save(good)
        before = self.read_bytes()
        bad = self.mission_store.default_document()
        bad["missions"]["mn-" + "1" * 32] = {"garbage": True}
        with self.assertRaises(self.mission_store.MissionStoreError):
            self.store.save(bad)
        self.assertEqual(self.read_bytes(), before)
        self.assertEqual(sorted(os.listdir(self.directory)), ["missions.json"])

    def test_C4_caps_are_module_constants_and_refuse_not_evict(self):
        ms = self.mission_store
        for name in ("MAX_MISSION_RECORDS", "MAX_MISSION_REVISIONS",
                     "MAX_MISSION_DECISIONS", "MAX_AUTHORITY_LEDGER_ENTRIES",
                     "MAX_AUTHORIZATION_RECORDS", "MAX_RESERVED_REQUEST_IDS",
                     "MAX_RESERVED_DECISION_IDS"):
            self.assertIsInstance(getattr(ms, name), int)
            self.assertGreater(getattr(ms, name), 0)
        document = ms.default_document()
        entry = {
            "reserved_at": 5, "kind": "request",
            "context": make_context().as_dict(), "consumed_by": None,
        }
        for index in range(ms.MAX_RESERVED_REQUEST_IDS):
            document["reservations"]["mq-%032x" % index] = dict(entry)
        self.store.save(document)
        document["reservations"]["mq-%032x" % ms.MAX_RESERVED_REQUEST_IDS] = (
            dict(entry)
        )
        with self.assertRaises(ms.MissionStoreError) as ctx:
            self.store.save(document)
        self.assertEqual(ctx.exception.problem, ms.PROBLEM_STORE_FULL)
        self.assertEqual(
            len(json.loads(self.read_bytes())["reservations"]),
            ms.MAX_RESERVED_REQUEST_IDS,
        )


# ====================================================================
# D. Service: success scenarios A-F and the fail-closed list
# ====================================================================


class Clock(object):
    def __init__(self, start=1_000_000):
        self.now = start

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds
        return self.now


class ServiceFixture(StoreFixture):

    def setUp(self):
        super(ServiceFixture, self).setUp()
        from mission import authorization as mission_authorization
        from mission import decision as mission_decision
        from mission import service as mission_service
        self.mission_authorization = mission_authorization
        self.mission_decision = mission_decision
        self.mission_service = mission_service
        self.clock = Clock()
        self.context = make_context("1")
        self.other_context = make_context("2")
        self.service = mission_service.MissionService(self.store, self.clock)

    def propose(self, context=None, **overrides):
        context = context or self.context
        request_id = self.service.mint_request_id(context)
        return self.service.propose(request_id, proposal(**overrides), context)

    def approve(self, mission_id, revision, context=None, expires_at=None,
                actions=None, targets=None):
        context = context or self.context
        decision_id = self.service.mint_decision_id(context)
        current = self.service.get(mission_id)["record"]["revisions"][-1]
        envelope = self.mission_decision.HumanDecisionEnvelope(
            context=context, decision_id=decision_id, mission_id=mission_id,
            revision=revision, decision=self.mission_decision.DECISION_APPROVE,
            received_at=self.clock(),
            approved_action_scope=(
                current["proposal"]["requested_action_scope"]
                if actions is None else actions
            ),
            approved_delivery_targets=(
                [current["proposal"]["requested_delivery_target"]]
                if targets is None and current["proposal"][
                    "requested_delivery_target"] is not None
                else (targets or [])
            ),
            expires_at=expires_at,
        )
        return self.service.apply_human_decision(envelope)

    def deny(self, mission_id, revision, context=None):
        context = context or self.context
        decision_id = self.service.mint_decision_id(context)
        envelope = self.mission_decision.HumanDecisionEnvelope(
            context=context, decision_id=decision_id, mission_id=mission_id,
            revision=revision, decision=self.mission_decision.DECISION_DENY,
            received_at=self.clock(),
        )
        return self.service.apply_human_decision(envelope)

    def edit(self, mission_id, revision, context=None, **overrides):
        context = context or self.context
        decision_id = self.service.mint_decision_id(context)
        return self.service.edit(mission_id, revision, proposal(**overrides),
                                 decision_id, context)

    def ledger_kinds(self, mission_id=None):
        document = self.store.load()
        return [
            e["kind"] for e in document["authority_ledger"]
            if mission_id is None or e["mission_id"] == mission_id
        ]

    def assertRefuses(self, problem, callable_, *args, **kwargs):
        with self.assertRaises(mission_record.MissionError) as ctx:
            callable_(*args, **kwargs)
        self.assertEqual(ctx.exception.problem, problem, str(ctx.exception))
        return ctx.exception


class DSuccessScenarioTests(ServiceFixture):

    def test_D_A_propose_returns_stable_id_revision_1_awaiting_no_authority(self):
        request_id = self.service.mint_request_id(self.context)
        self.assertIsNone(mission_record.id_problem(request_id, "mq"))
        first = self.service.propose(request_id, proposal(), self.context)
        self.assertIsNone(mission_record.id_problem(first["mission_id"], "mn"))
        self.assertEqual(first["revision"], 1)
        self.assertEqual(first["state"], "AWAITING_DECISION")
        self.assertFalse(first["idempotent"])
        self.assertEqual(first["request_id"], request_id)
        stored = self.service.get(first["mission_id"])
        self.assertEqual(stored["record"]["revisions"][0]["proposal"],
                         mission_record.validate_proposal(proposal()))
        self.assertEqual(stored["authorizations"], [])
        self.assertEqual(self.store.load()["authority_ledger"], [])
        # Identical retry with the same DI-issued id: the same Mission.
        self.clock.advance(30)
        retry = self.service.propose(request_id, proposal(), self.context)
        self.assertEqual(retry["mission_id"], first["mission_id"])
        self.assertTrue(retry["idempotent"])
        self.assertEqual(len(self.store.load()["missions"]), 1)
        # A NEW reserved id with identical content creates a NEW Mission.
        second = self.propose()
        self.assertNotEqual(second["mission_id"], first["mission_id"])
        self.assertEqual(len(self.store.load()["missions"]), 2)

    def test_D_B_edit_keeps_id_appends_revision_and_stale_approval_refuses(self):
        created = self.propose()
        mission_id = created["mission_id"]
        edited = self.edit(mission_id, 1, objective="Narrower objective")
        self.assertEqual(edited["mission_id"], mission_id)
        self.assertEqual(edited["revision"], 2)
        self.assertEqual(edited["state"], "AWAITING_DECISION")
        stored = self.service.get(mission_id)["record"]
        self.assertEqual([r["revision"] for r in stored["revisions"]], [1, 2])
        self.assertEqual(stored["revisions"][0]["proposal"]["objective"],
                         proposal()["objective"])
        self.assertEqual(stored["revisions"][1]["proposal"]["objective"],
                         "Narrower objective")
        self.assertRefuses(self.mission_service.PROBLEM_STALE_REVISION,
                           self.approve, mission_id, 1)
        self.assertEqual(self.service.get(mission_id)["authorizations"], [])
        self.assertEqual(self.ledger_kinds(mission_id), [])

    def test_D_C_approval_issues_durable_authorization_and_ledger_issuance(self):
        created = self.propose()
        mission_id = created["mission_id"]
        outcome = self.approve(mission_id, 1)
        self.assertEqual(outcome["state"], "AUTHORIZED")
        self.assertIsNone(mission_record.id_problem(
            outcome["authorization_id"], "ma"
        ))
        self.assertEqual(len(outcome["authorization_digest_sha256"]), 64)
        self.assertEqual(self.ledger_kinds(mission_id), ["ISSUED"])
        stored = self.service.get(mission_id)
        self.assertEqual(stored["record"]["state"], "AUTHORIZED")
        self.assertEqual(len(stored["authorizations"]), 1)
        authorization = stored["authorizations"][0]
        self.assertEqual(authorization["revision"], 1)
        self.assertEqual(authorization["proposal_digest_sha256"],
                         stored["record"]["revisions"][0]["proposal_digest_sha256"])
        self.assertFalse(authorization["revocation"]["revoked"])
        # Durable: a second service over the same directory sees it.
        again = self.mission_service.MissionService(
            self.mission_store.MissionStore(self.directory), self.clock
        )
        self.assertEqual(again.get(mission_id)["authorizations"][0],
                         authorization)
        # Validates exact scope only.
        check = self.service.validate_authorization(
            outcome["authorization_id"], mission_id, 1,
            required_actions=[mission_record.ACTION_SCOPE_ENGINEERING_CHANGE],
            required_delivery_target=mission_record.DELIVERY_TARGET_GITHUB_PR,
        )
        self.assertTrue(check.valid, check)
        self.assertEqual(check.authorized_action_scope,
                         sorted(proposal()["requested_action_scope"]))
        outside = self.service.validate_authorization(
            outcome["authorization_id"], mission_id, 1,
            required_actions=[mission_record.ACTION_SCOPE_VERIFICATION_RUN],
        )
        self.assertFalse(outside.valid)
        self.assertEqual(outside.problem,
                         self.mission_authorization.PROBLEM_ACTION_OUTSIDE_SCOPE)

    def test_D_D_denial_issues_nothing_and_blocks_until_new_revision(self):
        created = self.propose()
        mission_id = created["mission_id"]
        outcome = self.deny(mission_id, 1)
        self.assertEqual(outcome["state"], "DENIED")
        self.assertIsNone(outcome["authorization_id"])
        self.assertEqual(self.service.get(mission_id)["authorizations"], [])
        self.assertEqual(self.ledger_kinds(mission_id), ["DENIED"])
        # Approval of a denied revision refuses: a new revision is required.
        self.assertRefuses(mission_record.PROBLEM_INVALID_TRANSITION,
                           self.approve, mission_id, 1)
        edited = self.edit(mission_id, 1, objective="Revised after denial")
        self.assertEqual(edited["state"], "AWAITING_DECISION")
        self.assertEqual(edited["revision"], 2)
        approved = self.approve(mission_id, 2)
        self.assertEqual(approved["state"], "AUTHORIZED")
        self.assertEqual(self.ledger_kinds(mission_id), ["DENIED", "ISSUED"])

    def test_D_E_edit_after_approval_invalidates_authority(self):
        mission_id = self.propose()["mission_id"]
        approved = self.approve(mission_id, 1)
        authorization_id = approved["authorization_id"]
        edited = self.edit(mission_id, 1, requested_scope="wider scope text")
        self.assertEqual(edited["revision"], 2)
        self.assertEqual(edited["state"], "AWAITING_DECISION")
        self.assertEqual(edited["invalidated_authorization_ids"],
                         [authorization_id])
        stored = self.service.get(mission_id)
        authorization = stored["authorizations"][0]
        self.assertTrue(authorization["revocation"]["revoked"])
        self.assertEqual(authorization["revocation"]["reason"],
                         "superseded_by_edit")
        self.assertEqual(self.ledger_kinds(mission_id),
                         ["ISSUED", "INVALIDATED_BY_EDIT"])
        # Authority never follows or widens across an edit.
        for revision in (1, 2):
            check = self.service.validate_authorization(
                authorization_id, mission_id, revision
            )
            self.assertFalse(check.valid)
        self.assertEqual(
            self.service.validate_authorization(
                authorization_id, mission_id, 1
            ).problem,
            self.mission_authorization.PROBLEM_REVOKED,
        )
        # Revision 2 has zero authorizations until a new approval.
        ids = [a["authorization_id"] for a in stored["authorizations"]
               if a["revision"] == 2]
        self.assertEqual(ids, [])
        reapproved = self.approve(mission_id, 2)
        self.assertNotEqual(reapproved["authorization_id"], authorization_id)
        self.assertTrue(self.service.validate_authorization(
            reapproved["authorization_id"], mission_id, 2
        ).valid)

    def test_D_F_github_pr_is_parent_authority_confirmable_without_git_effect(self):
        mission_id = self.propose()["mission_id"]
        approved = self.approve(mission_id, 1)
        self.assertEqual(approved["authorized_delivery_targets"], ["github_pr"])
        check = self.service.check_parent_authority(
            mission_id, approved["authorization_digest_sha256"],
            mission_record.DELIVERY_TARGET_GITHUB_PR,
        )
        self.assertTrue(check.valid, check)
        self.assertEqual(check.authorization_id, approved["authorization_id"])
        self.assertEqual(check.revision, 1)
        self.assertEqual(check.authorized_delivery_targets, ["github_pr"])
        # A Mission approved WITHOUT github_pr does not carry it.
        plain = self.propose(requested_delivery_target=None)["mission_id"]
        plain_approved = self.approve(plain, 1)
        self.assertEqual(plain_approved["authorized_delivery_targets"], [])
        denied = self.service.check_parent_authority(
            plain, plain_approved["authorization_digest_sha256"],
            mission_record.DELIVERY_TARGET_GITHUB_PR,
        )
        self.assertFalse(denied.valid)
        self.assertEqual(denied.problem,
                         self.mission_authorization.PROBLEM_TARGET_OUTSIDE_SCOPE)
        # No Git effect: the protected directory holds only the store.
        self.assertEqual(sorted(os.listdir(self.directory)),
                         ["missions.json", "missions.lock"])


class DFailClosedTests(ServiceFixture):

    def test_D1_unknown_or_malformed_mission(self):
        self.assertRefuses(self.mission_authorization.PROBLEM_UNKNOWN_MISSION,
                           self.service.get, "mn-" + "f" * 32)
        self.assertRefuses(self.mission_authorization.PROBLEM_UNKNOWN_MISSION,
                           self.service.get, "not-an-id")
        self.assertRefuses(self.mission_authorization.PROBLEM_UNKNOWN_MISSION,
                           self.approve, "mn-" + "f" * 32, 1)
        check = self.service.validate_authorization(
            "ma-" + "0" * 32, "mn-" + "f" * 32, 1
        )
        self.assertEqual(check.problem,
                         self.mission_authorization.PROBLEM_UNKNOWN_MISSION)
        # Malformed Mission state on disk fails the validation path closed.
        mission_id = self.propose()["mission_id"]
        approved = self.approve(mission_id, 1)
        document = json.loads(self.read_bytes())
        document["missions"][mission_id]["state"] = "RUNNING"
        self.write_raw(json.dumps(document))
        check = self.service.validate_authorization(
            approved["authorization_id"], mission_id, 1
        )
        self.assertFalse(check.valid)
        self.assertIn(check.problem, (
            self.mission_authorization.PROBLEM_STORE_UNREADABLE,
            self.mission_authorization.PROBLEM_MALFORMED_STATE,
        ))

    def test_D2_request_id_reservation_and_conflicts(self):
        # Caller-chosen id matching the grammar is refused.
        self.assertRefuses(self.mission_service.PROBLEM_UNKNOWN_REQUEST_ID,
                           self.service.propose, "mq-" + "a" * 32,
                           proposal(), self.context)
        self.assertRefuses(mission_record.PROBLEM_ID_GRAMMAR,
                           self.service.propose, "mq-short", proposal(),
                           self.context)
        request_id = self.service.mint_request_id(self.context)
        first = self.service.propose(request_id, proposal(), self.context)
        # Same reserved id + conflicting content -> refuse, no mutation.
        before = self.read_bytes()
        self.assertRefuses(self.mission_service.PROBLEM_REQUEST_ID_CONFLICT,
                           self.service.propose, request_id,
                           proposal(objective="different"), self.context)
        self.assertEqual(self.read_bytes(), before)
        # Same reserved id from a different principal -> refuse.
        self.assertRefuses(self.mission_service.PROBLEM_REQUEST_CONTEXT_CONFLICT,
                           self.service.propose, request_id, proposal(),
                           self.other_context)
        # A reserved-but-unused id from another principal also refuses.
        reserved = self.service.mint_request_id(self.context)
        self.assertRefuses(self.mission_service.PROBLEM_REQUEST_CONTEXT_CONFLICT,
                           self.service.propose, reserved, proposal(),
                           self.other_context)
        # Replay after edits: same Mission, current revision reported,
        # comparison against revision-1 content.
        self.edit(first["mission_id"], 1, objective="edited")
        replay = self.service.propose(request_id, proposal(), self.context)
        self.assertEqual(replay["mission_id"], first["mission_id"])
        self.assertEqual(replay["revision"], 2)
        self.assertTrue(replay["idempotent"])
        self.assertRefuses(self.mission_service.PROBLEM_REQUEST_ID_CONFLICT,
                           self.service.propose, request_id,
                           proposal(objective="edited"), self.context)
        # Reordered action scope is the same content.
        reordered = proposal(requested_action_scope=list(reversed(
            proposal()["requested_action_scope"]
        )))
        self.assertTrue(self.service.propose(
            request_id, reordered, self.context
        )["idempotent"])

    def test_D3_reservation_cap_refuses_and_never_evicts(self):
        ms = self.mission_store
        document = self.store.load()
        entry = {"reserved_at": 1, "kind": "request",
                 "context": self.context.as_dict(), "consumed_by": None}
        for index in range(ms.MAX_RESERVED_REQUEST_IDS):
            document["reservations"]["mq-%032x" % index] = dict(entry)
        self.store.save(document)
        before = self.read_bytes()
        with self.assertRaises(ms.MissionStoreError) as ctx:
            self.service.mint_request_id(self.context)
        self.assertEqual(ctx.exception.problem, ms.PROBLEM_STORE_FULL)
        self.assertEqual(self.read_bytes(), before)
        # Every earlier reservation is still honored.
        outcome = self.service.propose("mq-%032x" % 7, proposal(), self.context)
        self.assertEqual(outcome["revision"], 1)

    def test_D4_stale_edit_approval_and_denial_refuse(self):
        mission_id = self.propose()["mission_id"]
        self.edit(mission_id, 1, objective="v2")
        for action in (self.approve, self.deny):
            before = self.read_bytes()
            self.assertRefuses(self.mission_service.PROBLEM_STALE_REVISION,
                               action, mission_id, 1)
            self.assertRefuses(self.mission_service.PROBLEM_STALE_REVISION,
                               action, mission_id, 3)
            self.assertNotEqual(self.read_bytes(), before)  # reservation only
            self.assertEqual(self.service.get(mission_id)["authorizations"], [])
        self.assertRefuses(self.mission_service.PROBLEM_STALE_REVISION,
                           self.edit, mission_id, 1, objective="v3")
        self.assertEqual(self.service.get(mission_id)["record"][
            "current_revision"], 2)
        self.assertEqual(self.ledger_kinds(mission_id), [])

    def test_D5_concurrent_editors_only_one_wins_per_revision(self):
        mission_id = self.propose()["mission_id"]
        services = [
            self.mission_service.MissionService(
                self.mission_store.MissionStore(self.directory), self.clock
            ) for _ in range(6)
        ]
        results = []
        errors = []

        def worker(index):
            service = services[index]
            try:
                decision_id = service.mint_decision_id(self.context)
                results.append(service.edit(
                    mission_id, 1, proposal(objective="edit %d" % index),
                    decision_id, self.context,
                ))
            except mission_record.MissionError as exc:
                errors.append(exc.problem)

        threads = [threading.Thread(target=worker, args=(i,))
                   for i in range(len(services))]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(len(results), 1, (results, errors))
        self.assertEqual(len(errors), len(services) - 1)
        self.assertEqual(set(errors), {self.mission_service.PROBLEM_STALE_REVISION})
        stored = self.service.get(mission_id)["record"]
        self.assertEqual(stored["current_revision"], 2)
        self.assertEqual(len(stored["revisions"]), 2)
        self.assertEqual(len(stored["decisions"]), 1)
        self.store.load()  # the document is still whole

    def test_D6_decision_id_reuse_is_exact_replay_or_conflict(self):
        mission_id = self.propose()["mission_id"]
        decision_id = self.service.mint_decision_id(self.context)
        current = self.service.get(mission_id)["record"]["revisions"][-1]
        envelope = self.mission_decision.HumanDecisionEnvelope(
            context=self.context, decision_id=decision_id,
            mission_id=mission_id, revision=1,
            decision=self.mission_decision.DECISION_APPROVE,
            received_at=self.clock(),
            approved_action_scope=current["proposal"]["requested_action_scope"],
            approved_delivery_targets=["github_pr"],
        )
        first = self.service.apply_human_decision(envelope)
        self.assertFalse(first["idempotent"])
        # Exact repeat (fresh receive time): recorded outcome, nothing new.
        self.clock.advance(60)
        repeat = self.service.apply_human_decision(
            self.mission_decision.HumanDecisionEnvelope(
                context=self.context, decision_id=decision_id,
                mission_id=mission_id, revision=1,
                decision=self.mission_decision.DECISION_APPROVE,
                received_at=self.clock(),
                approved_action_scope=list(reversed(
                    current["proposal"]["requested_action_scope"]
                )),
                approved_delivery_targets=["github_pr"],
            )
        )
        self.assertTrue(repeat["idempotent"])
        self.assertEqual(repeat["authorization_id"], first["authorization_id"])
        self.assertEqual(self.ledger_kinds(mission_id), ["ISSUED"])
        self.assertEqual(len(self.service.get(mission_id)["authorizations"]), 1)
        # Same id, different content -> conflict, no mutation.
        before = self.read_bytes()
        self.assertRefuses(
            self.mission_service.PROBLEM_DECISION_ID_CONFLICT,
            self.service.apply_human_decision,
            self.mission_decision.HumanDecisionEnvelope(
                context=self.context, decision_id=decision_id,
                mission_id=mission_id, revision=1,
                decision=self.mission_decision.DECISION_DENY,
                received_at=self.clock(),
            ),
        )
        # Same id, different principal -> conflict.
        self.assertRefuses(
            self.mission_service.PROBLEM_DECISION_CONTEXT_CONFLICT,
            self.service.apply_human_decision,
            self.mission_decision.HumanDecisionEnvelope(
                context=self.other_context, decision_id=decision_id,
                mission_id=mission_id, revision=1,
                decision=self.mission_decision.DECISION_APPROVE,
                received_at=self.clock(),
                approved_action_scope=current["proposal"]["requested_action_scope"],
                approved_delivery_targets=["github_pr"],
            ),
        )
        # Caller-chosen decision id -> unknown.
        self.assertRefuses(
            self.mission_service.PROBLEM_UNKNOWN_DECISION_ID,
            self.service.apply_human_decision,
            self.mission_decision.HumanDecisionEnvelope(
                context=self.context, decision_id="md-" + "b" * 32,
                mission_id=mission_id, revision=1,
                decision=self.mission_decision.DECISION_DENY,
                received_at=self.clock(),
            ),
        )
        self.assertEqual(self.read_bytes(), before)
        # A second approval of an already authorized revision is refused
        # as an invalid transition, never a second authorization.
        self.assertRefuses(mission_record.PROBLEM_INVALID_TRANSITION,
                           self.approve, mission_id, 1)
        self.assertEqual(len(self.service.get(mission_id)["authorizations"]), 1)

    def test_D6b_requested_expiry_is_approval_content_in_the_digest(self):
        # Amendment A-5: a different requested expiry under the same
        # decision id is a DIFFERENT decision, never a silent replay.
        mission_id = self.propose()["mission_id"]
        decision_id = self.service.mint_decision_id(self.context)
        current = self.service.get(mission_id)["record"]["revisions"][-1]
        expiry = self.clock() + 3600

        def envelope(expires_at, received_at):
            return self.mission_decision.HumanDecisionEnvelope(
                context=self.context, decision_id=decision_id,
                mission_id=mission_id, revision=1,
                decision=self.mission_decision.DECISION_APPROVE,
                received_at=received_at,
                approved_action_scope=current["proposal"][
                    "requested_action_scope"],
                approved_delivery_targets=["github_pr"],
                expires_at=expires_at,
            )

        first = self.service.apply_human_decision(
            envelope(expiry, self.clock())
        )
        self.assertFalse(first["idempotent"])
        stored = self.service.get(mission_id)
        self.assertEqual(stored["authorizations"][0]["expires_at"], expiry)
        self.assertEqual(stored["record"]["decisions"][0]["expires_at"], expiry)
        ledger_before = self.read_bytes()
        # Different expiry, same everything else -> conflict, no mutation.
        for other in (expiry + 1, None):
            self.assertRefuses(
                self.mission_service.PROBLEM_DECISION_ID_CONFLICT,
                self.service.apply_human_decision,
                envelope(other, self.clock()),
            )
        self.assertEqual(self.read_bytes(), ledger_before)
        self.assertEqual(self.ledger_kinds(mission_id), ["ISSUED"])
        self.assertEqual(len(self.service.get(mission_id)["authorizations"]), 1)
        # Identical expiry with fresh processing timestamps still replays.
        self.clock.advance(90)
        replay = self.service.apply_human_decision(
            envelope(expiry, self.clock())
        )
        self.assertTrue(replay["idempotent"])
        self.assertEqual(replay["authorization_id"], first["authorization_id"])
        self.assertEqual(self.read_bytes(), ledger_before)
        # The None case replays too, and conflicts with a set expiry.
        other_mission = self.propose()["mission_id"]
        other_decision = self.service.mint_decision_id(self.context)
        none_envelope = self.mission_decision.HumanDecisionEnvelope(
            context=self.context, decision_id=other_decision,
            mission_id=other_mission, revision=1,
            decision=self.mission_decision.DECISION_APPROVE,
            received_at=self.clock(),
            approved_action_scope=current["proposal"]["requested_action_scope"],
            approved_delivery_targets=["github_pr"], expires_at=None,
        )
        issued = self.service.apply_human_decision(none_envelope)
        self.assertIsNone(
            self.service.get(other_mission)["authorizations"][0]["expires_at"]
        )
        self.assertTrue(self.service.apply_human_decision(
            none_envelope
        )["idempotent"])
        self.assertRefuses(
            self.mission_service.PROBLEM_DECISION_ID_CONFLICT,
            self.service.apply_human_decision,
            self.mission_decision.HumanDecisionEnvelope(
                context=self.context, decision_id=other_decision,
                mission_id=other_mission, revision=1,
                decision=self.mission_decision.DECISION_APPROVE,
                received_at=self.clock(),
                approved_action_scope=current["proposal"][
                    "requested_action_scope"],
                approved_delivery_targets=["github_pr"],
                expires_at=self.clock() + 5,
            ),
        )
        self.assertEqual(issued["authorization_id"],
                         self.service.get(other_mission)["authorizations"][0][
                             "authorization_id"])

    def test_D6c_every_semantic_envelope_field_is_digested(self):
        # Digest inputs: every envelope field except the two processing
        # timestamps, the DI-minted decision id, and the context (which
        # is checked as the reservation's principal binding instead).
        import dataclasses
        fields = {f.name for f in dataclasses.fields(
            self.mission_decision.HumanDecisionEnvelope
        )}
        digested = {"mission_id", "revision", "decision",
                    "approved_action_scope", "approved_delivery_targets",
                    "expires_at", "proposal"}
        excluded = {"context", "decision_id", "received_at"}
        self.assertEqual(fields, digested | excluded)
        base = dict(
            context=self.context, decision_id="md-" + "0" * 32,
            mission_id="mn-" + "0" * 32, revision=1,
            decision=self.mission_decision.DECISION_APPROVE, received_at=1,
            approved_action_scope=["repository_read"],
            approved_delivery_targets=[], expires_at=None, proposal=None,
        )
        reference = self.mission_decision.HumanDecisionEnvelope(**base).digest()
        variants = {
            "mission_id": "mn-" + "1" * 32, "revision": 2,
            "approved_action_scope": ["engineering_change"],
            "approved_delivery_targets": ["github_pr"], "expires_at": 99,
        }
        for name, value in variants.items():
            changed = dict(base, **{name: value})
            self.assertNotEqual(
                self.mission_decision.HumanDecisionEnvelope(**changed).digest(),
                reference, name,
            )
        same = dict(base, received_at=2, decision_id="md-" + "1" * 32,
                    context=self.other_context)
        self.assertEqual(
            self.mission_decision.HumanDecisionEnvelope(**same).digest(),
            reference,
        )
        edit_a = self.mission_decision.HumanDecisionEnvelope(
            context=self.context, decision_id="md-" + "0" * 32,
            mission_id="mn-" + "0" * 32, revision=1,
            decision=self.mission_decision.DECISION_EDIT, received_at=1,
            proposal=proposal(),
        ).digest()
        edit_b = self.mission_decision.HumanDecisionEnvelope(
            context=self.context, decision_id="md-" + "0" * 32,
            mission_id="mn-" + "0" * 32, revision=1,
            decision=self.mission_decision.DECISION_EDIT, received_at=1,
            proposal=proposal(objective="changed"),
        ).digest()
        self.assertNotEqual(edit_a, edit_b)

    def test_D7_absent_or_forged_provenance_refuses(self):
        mission_id = self.propose()["mission_id"]
        decision_id = self.service.mint_decision_id(self.context)
        for bad_context in (None, {"transport": "grok_mcp"}, "operator",
                            make_context("", ), make_context("1", transport="")):
            envelope = self.mission_decision.HumanDecisionEnvelope(
                context=bad_context, decision_id=decision_id,
                mission_id=mission_id, revision=1,
                decision=self.mission_decision.DECISION_DENY,
                received_at=self.clock(),
            )
            with self.assertRaises(mission_record.MissionError):
                self.service.apply_human_decision(envelope)
        self.assertEqual(self.ledger_kinds(mission_id), [])
        self.assertRefuses(mission_record.PROBLEM_PROVENANCE,
                           self.service.mint_request_id, None)
        self.assertRefuses(mission_record.PROBLEM_PROVENANCE,
                           self.service.mint_request_id, {"transport": "x"})

    def test_D8_wrong_bound_principal_cannot_replay_a_decision(self):
        mission_id = self.propose()["mission_id"]
        decision_id = self.service.mint_decision_id(self.context)
        envelope = self.mission_decision.HumanDecisionEnvelope(
            context=self.other_context, decision_id=decision_id,
            mission_id=mission_id, revision=1,
            decision=self.mission_decision.DECISION_DENY,
            received_at=self.clock(),
        )
        self.assertRefuses(self.mission_service.PROBLEM_DECISION_CONTEXT_CONFLICT,
                           self.service.apply_human_decision, envelope)
        self.assertEqual(self.service.get(mission_id)["record"]["state"],
                         "AWAITING_DECISION")

    def test_D9_validation_path_distinct_codes(self):
        auth = self.mission_authorization
        mission_id = self.propose()["mission_id"]
        approved = self.approve(mission_id, 1, expires_at=self.clock() + 100)
        authorization_id = approved["authorization_id"]
        other = self.propose()["mission_id"]
        self.approve(other, 1)
        ok = self.service.validate_authorization(authorization_id, mission_id, 1)
        self.assertTrue(ok.valid)
        cases = [
            (auth.PROBLEM_UNKNOWN_AUTHORIZATION,
             dict(authorization_id="ma-" + "e" * 32, mission_id=mission_id,
                  revision=1)),
            (auth.PROBLEM_WRONG_MISSION,
             dict(authorization_id=authorization_id, mission_id=other,
                  revision=1)),
            (auth.PROBLEM_WRONG_REVISION,
             dict(authorization_id=authorization_id, mission_id=mission_id,
                  revision=2)),
            (auth.PROBLEM_WRONG_MANIFEST_DIGEST,
             dict(authorization_id=authorization_id, mission_id=mission_id,
                  revision=1, expected_proposal_digest="0" * 64)),
            (auth.PROBLEM_ACTION_OUTSIDE_SCOPE,
             dict(authorization_id=authorization_id, mission_id=mission_id,
                  revision=1, required_actions=["verification_run"])),
            (auth.PROBLEM_TARGET_OUTSIDE_SCOPE,
             dict(authorization_id=authorization_id, mission_id=mission_id,
                  revision=1, required_delivery_target="npm")),
        ]
        for problem, kwargs in cases:
            check = self.service.validate_authorization(**kwargs)
            self.assertFalse(check.valid, problem)
            self.assertEqual(check.problem, problem, kwargs)
        # Expired.
        self.clock.advance(100)
        expired = self.service.validate_authorization(authorization_id,
                                                      mission_id, 1)
        self.assertEqual(expired.problem, auth.PROBLEM_EXPIRED)
        # Denied mission.
        denied_id = self.propose()["mission_id"]
        self.deny(denied_id, 1)
        forged = self.service.validate_authorization(authorization_id,
                                                     denied_id, 1)
        self.assertEqual(forged.problem, auth.PROBLEM_WRONG_MISSION)
        # Tampered authorization content fails closed.
        document = json.loads(self.read_bytes())
        document["authorizations"][authorization_id][
            "authorized_delivery_targets"] = []
        self.write_raw(json.dumps(document))
        tampered = self.service.validate_authorization(authorization_id,
                                                       mission_id, 1)
        self.assertFalse(tampered.valid)
        self.assertIn(tampered.problem, (
            auth.PROBLEM_STORE_UNREADABLE, auth.PROBLEM_AUTHORIZATION_TAMPERED,
        ))

    def test_D10_unreadable_store_or_ledger_fails_closed_everywhere(self):
        auth = self.mission_authorization
        mission_id = self.propose()["mission_id"]
        approved = self.approve(mission_id, 1)
        good = self.read_bytes()
        # Unreadable store.
        self.write_raw("{corrupt")
        check = self.service.validate_authorization(
            approved["authorization_id"], mission_id, 1
        )
        self.assertFalse(check.valid)
        self.assertEqual(check.problem, auth.PROBLEM_STORE_UNREADABLE)
        parent = self.service.check_parent_authority(
            mission_id, approved["authorization_digest_sha256"], "github_pr"
        )
        self.assertEqual(parent.problem, auth.PROBLEM_STORE_UNREADABLE)
        with self.assertRaises(self.mission_store.MissionStoreError):
            self.service.get(mission_id)
        with self.assertRaises(self.mission_store.MissionStoreError):
            self.approve(mission_id, 1)
        self.assertEqual(self.read_bytes(), b"{corrupt")
        # Inconsistent ledger: the ISSUED entry is gone. The validator
        # names the contradiction; the store refuses to load it at all.
        document = json.loads(good)
        document["authority_ledger"] = []
        direct = auth.validate_authorization_use(
            document, approved["authorization_id"], mission_id, 1, self.clock()
        )
        self.assertFalse(direct.valid)
        self.assertEqual(direct.problem, auth.PROBLEM_LEDGER_INCONSISTENT)
        self.write_raw(json.dumps(document))
        check = self.service.validate_authorization(
            approved["authorization_id"], mission_id, 1
        )
        self.assertFalse(check.valid)
        self.assertEqual(check.problem, auth.PROBLEM_STORE_UNREADABLE)
        # Malformed ledger entry makes the store unreadable.
        document = json.loads(good)
        document["authority_ledger"][0]["kind"] = "GRANTED"
        self.write_raw(json.dumps(document))
        check = self.service.validate_authorization(
            approved["authorization_id"], mission_id, 1
        )
        self.assertEqual(check.problem, auth.PROBLEM_STORE_UNREADABLE)

    def test_D11_approved_scope_cannot_exceed_requested(self):
        mission_id = self.propose(
            requested_action_scope=[mission_record.ACTION_SCOPE_REPOSITORY_READ],
            requested_delivery_target=None,
        )["mission_id"]
        self.assertRefuses(
            self.mission_authorization.PROBLEM_ACTION_OUTSIDE_SCOPE,
            self.approve, mission_id, 1,
            actions=[mission_record.ACTION_SCOPE_ENGINEERING_CHANGE],
        )
        self.assertRefuses(
            self.mission_authorization.PROBLEM_TARGET_OUTSIDE_SCOPE,
            self.approve, mission_id, 1,
            actions=[mission_record.ACTION_SCOPE_REPOSITORY_READ],
            targets=["github_pr"],
        )
        self.assertEqual(self.service.get(mission_id)["authorizations"], [])
        narrowed = self.approve(
            mission_id, 1, actions=[mission_record.ACTION_SCOPE_REPOSITORY_READ],
            targets=[],
        )
        self.assertEqual(narrowed["authorized_action_scope"],
                         ["repository_read"])

    def test_D12_expiry_is_recorded_in_the_ledger_on_the_next_mutation(self):
        mission_id = self.propose()["mission_id"]
        approved = self.approve(mission_id, 1, expires_at=self.clock() + 10)
        self.clock.advance(20)
        self.assertEqual(self.service.validate_authorization(
            approved["authorization_id"], mission_id, 1
        ).problem, self.mission_authorization.PROBLEM_EXPIRED)
        self.assertEqual(self.ledger_kinds(mission_id), ["ISSUED"])
        self.edit(mission_id, 1, objective="after expiry")
        self.assertEqual(self.ledger_kinds(mission_id),
                         ["ISSUED", "EXPIRED", "INVALIDATED_BY_EDIT"])

    def test_D13_approval_stops_before_execution(self):
        # Approval leaves exactly one file changed (the store), creates no
        # workspace, and the state is AUTHORIZED, never RUNNING.
        mission_id = self.propose()["mission_id"]
        outcome = self.approve(mission_id, 1)
        self.assertEqual(outcome["state"], "AUTHORIZED")
        self.assertNotEqual(outcome["state"], "RUNNING")
        self.assertEqual(sorted(os.listdir(self.directory)),
                         ["missions.json", "missions.lock"])
        self.assertEqual(sorted(os.listdir(self.tmp.name)), ["protected"])


class DReplayProjectionTests(ServiceFixture):
    """Amendment A-6: a replay preserves the exact historical outcome and
    never reads as live authority."""

    def approve_envelope(self, mission_id, revision, decision_id,
                         expires_at=None):
        current = self.service.get(mission_id)["record"]["revisions"][-1]
        return self.mission_decision.HumanDecisionEnvelope(
            context=self.context, decision_id=decision_id,
            mission_id=mission_id, revision=revision,
            decision=self.mission_decision.DECISION_APPROVE,
            received_at=self.clock(),
            approved_action_scope=current["proposal"]["requested_action_scope"],
            approved_delivery_targets=["github_pr"], expires_at=expires_at,
        )

    def test_D14_replay_after_edit_carries_the_r1_digest_and_shows_revocation(self):
        mission_id = self.propose()["mission_id"]
        r1_digest = self.service.get(mission_id)["record"]["revisions"][0][
            "proposal_digest_sha256"]
        decision_id = self.service.mint_decision_id(self.context)
        first = self.service.apply_human_decision(
            self.approve_envelope(mission_id, 1, decision_id)
        )
        self.assertEqual(first["proposal_digest_sha256"], r1_digest)
        self.assertTrue(first["authorization_live"])
        self.assertIsNone(first["authorization_problem"])
        self.assertEqual(first["current_revision"], 1)
        self.assertEqual(first["current_state"], "AUTHORIZED")
        edited = self.edit(mission_id, 1, objective="revision two")
        r2_digest = edited["proposal_digest_sha256"]
        self.assertNotEqual(r1_digest, r2_digest)
        self.clock.advance(5)
        replay = self.service.apply_human_decision(
            self.approve_envelope(mission_id, 1, decision_id)
        )
        self.assertTrue(replay["idempotent"])
        # Historical half: exactly what the decision did.
        self.assertEqual(replay["revision"], 1)
        self.assertEqual(replay["state"], "AUTHORIZED")
        self.assertEqual(replay["proposal_digest_sha256"], r1_digest)
        self.assertEqual(replay["authorization_id"], first["authorization_id"])
        # Present half: the authority is visibly gone.
        self.assertFalse(replay["authorization_live"])
        self.assertEqual(replay["authorization_problem"],
                         self.mission_authorization.PROBLEM_REVOKED)
        self.assertEqual(replay["current_revision"], 2)
        self.assertEqual(replay["current_state"], "AWAITING_DECISION")
        self.assertFalse(self.service.validate_authorization(
            replay["authorization_id"], mission_id, 1
        ).valid)
        self.assertEqual(self.ledger_kinds(mission_id),
                         ["ISSUED", "INVALIDATED_BY_EDIT"])
        # The approve envelope for revision 1 is otherwise stale.
        self.assertRefuses(self.mission_service.PROBLEM_STALE_REVISION,
                           self.approve, mission_id, 1)

    def test_D15_replay_after_denial_is_historical(self):
        mission_id = self.propose()["mission_id"]
        decision_id = self.service.mint_decision_id(self.context)
        envelope = self.mission_decision.HumanDecisionEnvelope(
            context=self.context, decision_id=decision_id,
            mission_id=mission_id, revision=1,
            decision=self.mission_decision.DECISION_DENY,
            received_at=self.clock(),
        )
        first = self.service.apply_human_decision(envelope)
        self.edit(mission_id, 1, objective="after denial")
        replay = self.service.apply_human_decision(envelope)
        self.assertTrue(replay["idempotent"])
        self.assertEqual(replay["state"], "DENIED")
        self.assertEqual(replay["revision"], 1)
        self.assertEqual(replay["proposal_digest_sha256"],
                         first["proposal_digest_sha256"])
        self.assertIsNone(replay["authorization_id"])
        self.assertIsNone(replay["authorization_live"])
        self.assertIsNone(replay["authorization_problem"])
        self.assertEqual(replay["current_revision"], 2)
        self.assertEqual(replay["current_state"], "AWAITING_DECISION")
        self.assertEqual(self.ledger_kinds(mission_id), ["DENIED"])

    def test_D16_replay_after_expiry_shows_expired_not_live(self):
        mission_id = self.propose()["mission_id"]
        decision_id = self.service.mint_decision_id(self.context)
        expires = self.clock() + 50
        first = self.service.apply_human_decision(
            self.approve_envelope(mission_id, 1, decision_id, expires_at=expires)
        )
        self.assertTrue(first["authorization_live"])
        self.clock.advance(60)
        replay = self.service.apply_human_decision(
            self.approve_envelope(mission_id, 1, decision_id, expires_at=expires)
        )
        self.assertTrue(replay["idempotent"])
        self.assertEqual(replay["state"], "AUTHORIZED")
        self.assertFalse(replay["authorization_live"])
        self.assertEqual(replay["authorization_problem"],
                         self.mission_authorization.PROBLEM_EXPIRED)
        self.assertEqual(self.service.validate_authorization(
            replay["authorization_id"], mission_id, 1
        ).problem, self.mission_authorization.PROBLEM_EXPIRED)
        # A fresh (non-replay) decision keeps its current behavior.
        edited = self.edit(mission_id, 1, objective="fresh")
        self.assertFalse(edited["idempotent"])
        self.assertEqual(edited["revision"], 2)
        self.assertEqual(edited["current_revision"], 2)
        self.assertEqual(edited["proposal_digest_sha256"],
                         self.service.get(mission_id)["record"]["revisions"][1][
                             "proposal_digest_sha256"])


class DLedgerReconciliationTests(ServiceFixture):
    """Amendment A-7: relational ledger reconciliation inside the one
    validator. Each test builds a real store through the real service,
    tampers ONE relation, and asserts the exact refusal code both when the
    validator is driven over the tampered document and when the tampered
    document is written back to disk and read through the service."""

    def setUp(self):
        super(DLedgerReconciliationTests, self).setUp()
        self.auth = self.mission_authorization
        self.mission_id = self.propose()["mission_id"]
        self.approved = self.approve(self.mission_id, 1,
                                     expires_at=self.clock() + 1000)
        self.authorization_id = self.approved["authorization_id"]
        self.good = json.loads(self.read_bytes())
        self.assertTrue(self.service.validate_authorization(
            self.authorization_id, self.mission_id, 1
        ).valid)

    def tampered(self):
        return json.loads(json.dumps(self.good))

    def assert_inconsistent(self, document, problem=None):
        problem = problem or self.auth.PROBLEM_LEDGER_INCONSISTENT
        direct = self.auth.validate_authorization_use(
            document, self.authorization_id, self.mission_id, 1, self.clock()
        )
        self.assertFalse(direct.valid)
        self.assertEqual(direct.problem, problem, direct.detail)
        # Through the store: fails closed either as the same inconsistency
        # or, when the store's own cross-checks catch it first, as an
        # unreadable store. Never valid.
        self.write_raw(json.dumps(document))
        via_service = self.service.validate_authorization(
            self.authorization_id, self.mission_id, 1
        )
        self.assertFalse(via_service.valid)
        self.assertIn(via_service.problem,
                      (problem, self.auth.PROBLEM_STORE_UNREADABLE))
        parent = self.service.check_parent_authority(
            self.mission_id, self.approved["authorization_digest_sha256"],
            "github_pr",
        )
        self.assertFalse(parent.valid)
        return direct

    def entry(self, document, kind="ISSUED", **overrides):
        base = dict(document["authority_ledger"][0])
        base.update({"entry_id": "ml-" + "9" * 32, "kind": kind,
                     "recorded_at": self.clock()})
        base.update(overrides)
        return base

    def test_D17_revoked_entry_while_boolean_says_unrevoked(self):
        document = self.tampered()
        document["authority_ledger"].append(self.entry(
            document, "REVOKED", reason="revoked by tamper"
        ))
        self.assert_inconsistent(document)

    def test_D18_invalidated_by_edit_entry_while_boolean_says_unrevoked(self):
        document = self.tampered()
        document["authority_ledger"].append(self.entry(
            document, "INVALIDATED_BY_EDIT", reason="superseded_by_edit"
        ))
        self.assert_inconsistent(document)

    def test_D19_expired_entry_while_record_looks_live(self):
        document = self.tampered()
        document["authority_ledger"].append(self.entry(
            document, "EXPIRED", decision_id=None, reason="expires_at passed"
        ))
        self.assert_inconsistent(document)

    def test_D20_issued_entry_names_another_mission(self):
        other = self.propose()["mission_id"]
        document = json.loads(self.read_bytes())
        self.good = document
        document = self.tampered()
        document["authority_ledger"][0]["mission_id"] = other
        self.assert_inconsistent(document)

    def test_D21_issued_entry_names_another_revision(self):
        document = self.tampered()
        document["authority_ledger"][0]["revision"] = 2
        self.assert_inconsistent(document)

    def test_D22_issued_entry_names_another_decision(self):
        document = self.tampered()
        other_decision = self.service.mint_decision_id(self.context)
        document = json.loads(self.read_bytes())
        self.good = document
        document = self.tampered()
        document["authority_ledger"][0]["decision_id"] = other_decision
        self.assert_inconsistent(document)

    def test_D23_boolean_says_revoked_without_any_ledger_event(self):
        document = self.tampered()
        document["authorizations"][self.authorization_id]["revocation"] = {
            "revoked": True, "revoked_at": self.clock(),
            "reason": "superseded_by_edit",
        }
        # The mission still says AUTHORIZED, so state and authority also
        # disagree; the validator reports the ledger contradiction.
        self.assert_inconsistent(document)

    def test_D24_denied_mission_without_denied_event(self):
        document = self.tampered()
        document["missions"][self.mission_id]["state"] = "DENIED"
        document["authorizations"][self.authorization_id]["revocation"] = {
            "revoked": True, "revoked_at": self.clock(),
            "reason": "superseded_by_edit",
        }
        document["authority_ledger"].append(self.entry(
            document, "INVALIDATED_BY_EDIT", reason="superseded_by_edit"
        ))
        self.assert_inconsistent(document,
                                 self.auth.PROBLEM_HISTORY_INCONSISTENT)

    def test_D25_edited_away_without_invalidation_event(self):
        # Real edit first, then drop the INVALIDATED_BY_EDIT event.
        self.edit(self.mission_id, 1, objective="edited")
        self.good = json.loads(self.read_bytes())
        document = self.tampered()
        document["authority_ledger"] = [
            e for e in document["authority_ledger"]
            if e["kind"] != "INVALIDATED_BY_EDIT"
        ]
        # Restore the cache to "unrevoked" too: the mission moved on but
        # the ledger and the record now both pretend nothing happened.
        document["authorizations"][self.authorization_id]["revocation"] = {
            "revoked": False, "revoked_at": None, "reason": None,
        }
        self.assert_inconsistent(document)

    def test_D26_unrevoked_authorization_while_mission_awaits_decision(self):
        document = self.tampered()
        document["missions"][self.mission_id]["state"] = "AWAITING_DECISION"
        self.assert_inconsistent(document,
                                 self.auth.PROBLEM_HISTORY_INCONSISTENT)

    def test_D27_consistent_history_still_validates_and_revoked_is_revoked(self):
        # A genuine edit produces agreeing ledger and cache: revoked.
        self.edit(self.mission_id, 1, objective="edited")
        check = self.service.validate_authorization(
            self.authorization_id, self.mission_id, 1
        )
        self.assertEqual(check.problem, self.auth.PROBLEM_REVOKED)
        again = self.approve(self.mission_id, 2)
        self.assertTrue(self.service.validate_authorization(
            again["authorization_id"], self.mission_id, 2
        ).valid)


# ====================================================================
# E. Trust separation: one issuance point, non-human paths cannot mint
# ====================================================================


def _name_and_string_counts(path, symbol):
    """NAME tokens equal to ``symbol`` plus non-docstring STRING tokens
    containing it (so getattr(module, "symbol") is counted too)."""
    source = path.read_text()
    positions = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                             ast.AsyncFunctionDef)):
            body = getattr(node, "body", [])
            if (body and isinstance(body[0], ast.Expr)
                    and isinstance(body[0].value, ast.Constant)
                    and isinstance(body[0].value.value, str)):
                positions.add((body[0].value.lineno, body[0].value.col_offset))
    count = 0
    for token in tokenize.generate_tokens(io.StringIO(source).readline):
        if token.type == tokenize.NAME and token.string == symbol:
            count += 1
        elif token.type == tokenize.STRING and token.start not in positions \
                and symbol in token.string:
            count += 1
    return count


def _product_files():
    sys.path.insert(0, str(REPO_ROOT / "tests"))
    from test_workflow_authority import derive_product_python_files
    files = derive_product_python_files(REPO_ROOT)
    files += sorted((REPO_ROOT / "herdr").glob("*.py"))
    return files


class ETrustSeparationTests(unittest.TestCase):
    """Static call-site pins SUPPLEMENT the behavioral proofs above
    (G3/G4 in the Grok suite, F5 below, D7/D8); they never substitute for
    them. In-process Python can construct any object; these pins close
    literal references in the product tree, nothing more."""

    def test_E1_authorization_constructor_has_one_definition_and_one_caller(self):
        counts = {}
        for path in _product_files():
            count = _name_and_string_counts(path, "issue_mission_authorization")
            if count:
                counts[path.relative_to(REPO_ROOT).as_posix()] = count
        self.assertEqual(counts, {
            "mission/authorization.py": 1,   # the definition
            "mission/service.py": 1,         # the one call, in apply_human_decision
        }, counts)
        service_tree = ast.parse((REPO_ROOT / "mission" / "service.py").read_text())
        callers = [
            node.name for node in ast.walk(service_tree)
            if isinstance(node, ast.FunctionDef) and any(
                isinstance(n, ast.Call) and getattr(
                    n.func, "attr", getattr(n.func, "id", None)
                ) == "issue_mission_authorization"
                for n in ast.walk(node)
            )
        ]
        self.assertEqual(callers, ["_approve"])
        # and _approve is reached only from apply_human_decision.
        approve_callers = [
            node.name for node in ast.walk(service_tree)
            if isinstance(node, ast.FunctionDef) and any(
                isinstance(n, ast.Call) and getattr(n.func, "attr", None)
                == "_approve" for n in ast.walk(node)
            )
        ]
        self.assertEqual(approve_callers, ["apply_human_decision"])

    def test_E2_only_the_service_and_the_grok_relay_apply_decisions(self):
        counts = {}
        for path in _product_files():
            count = _name_and_string_counts(path, "apply_human_decision")
            if count:
                counts[path.relative_to(REPO_ROOT).as_posix()] = count
        self.assertEqual(set(counts), {
            "mission/service.py", "grok_mcp/mission_tools.py",
        }, counts)
        self.assertEqual(counts["grok_mcp/mission_tools.py"], 1)

    def test_E3_no_execution_seam_or_orchestration_module_imports_mission(self):
        forbidden_roots = (
            "operator_session", "worker", "capability", "durable_execution",
            "target_runtime", "telegram_operator", "codex_gateway",
            "human_interaction", "herdr",
        )
        importers = {}
        for path in _product_files():
            relpath = path.relative_to(REPO_ROOT).as_posix()
            for node in ast.walk(ast.parse(path.read_text())):
                if isinstance(node, ast.Import):
                    names = [a.name for a in node.names]
                elif isinstance(node, ast.ImportFrom):
                    names = [node.module or ""]
                else:
                    continue
                if any(n.split(".")[0] == "mission" for n in names):
                    importers.setdefault(relpath, 0)
                    importers[relpath] += 1
        for relpath in importers:
            root = relpath.split("/")[0]
            self.assertNotIn(root, forbidden_roots, relpath)
            self.assertNotIn(relpath, ("herdctl.py", "dirun.py", "tgop.py",
                                       "codexgw.py"))
        self.assertEqual(
            {r for r in importers if r.startswith("pr_delivery/")},
            {"pr_delivery/mission_parent.py"},
        )
        self.assertTrue({r for r in importers if r.startswith("grok_mcp/")})

    def test_E4_mission_never_names_pr_delivery_or_a_provider(self):
        words = ("pr_delivery", "grok", "telegram", "codex", "claude", "pi_",
                 "operator_session", "target_runtime", "capability", "worker",
                 "durable_execution", "herd")
        for path in sorted((REPO_ROOT / "mission").glob("*.py")):
            source = path.read_text()
            for token in tokenize.generate_tokens(io.StringIO(source).readline):
                if token.type == tokenize.NAME:
                    for word in words:
                        self.assertNotIn(word, token.string.lower(),
                                         (path, token.start))
            lowered = source.lower()
            for word in ("pr_delivery", "grok", "telegram", "herd", "codex",
                         "claude"):
                self.assertNotIn(word, lowered, (path, word))

    def test_E5_no_claim_of_cryptographic_human_proof(self):
        # Truthfulness pin: the package states the limit and never claims
        # cryptographic proof of a human.
        for path in sorted((REPO_ROOT / "mission").glob("*.py")) + [
            REPO_ROOT / "grok_mcp" / "mission_tools.py",
            REPO_ROOT / "pr_delivery" / "mission_parent.py",
        ]:
            lowered = path.read_text().lower()
            self.assertNotIn("cryptographic human proof", lowered, path)
            self.assertNotIn("cryptographic proof", lowered, path)
            self.assertNotIn("forging is impossible", lowered, path)
            self.assertNotIn("cannot be forged", lowered, path)
        init = (REPO_ROOT / "mission" / "__init__.py").read_text()
        self.assertIn("not the human behind it", init)
        self.assertIn("it is NOT\ncryptographic security", init)
        self.assertIn("arbitrary in-process Python can construct any", init)


# ====================================================================
# F. P1-A6 parent-authority seam in pr_delivery
# ====================================================================


class FParentAuthoritySeamTests(ServiceFixture):

    def setUp(self):
        super(FParentAuthoritySeamTests, self).setUp()
        from pr_delivery import mission_parent
        self.mission_parent = mission_parent

    def delivery_document(self, mission_block):
        # Only the optional ``mission`` block is read; the rest of a
        # delivery record is irrelevant to the seam and stays untouched.
        return {"delivery_id": "dl-" + "0" * 32, "mission": mission_block}

    def test_F1_approved_github_pr_mission_validates_as_parent_authority(self):
        mission_id = self.propose()["mission_id"]
        approved = self.approve(mission_id, 1)
        projection = self.mission_parent.parent_mission_authority(
            self.delivery_document({
                "workflow_id": mission_id,
                "mission_authorization_digest_sha256":
                    approved["authorization_digest_sha256"],
            }),
            self.service,
        )
        self.assertTrue(projection["valid"], projection)
        self.assertIsNone(projection["problem"])
        self.assertEqual(projection["authorization_id"],
                         approved["authorization_id"])
        self.assertEqual(projection["mission_id"], mission_id)
        self.assertEqual(projection["revision"], 1)
        self.assertEqual(projection["proposal_digest_sha256"],
                         self.service.get(mission_id)["record"]["revisions"][0][
                             "proposal_digest_sha256"])
        self.assertEqual(projection["authorized_action_scope"],
                         ["engineering_change", "repository_read"])
        self.assertEqual(projection["authorized_delivery_targets"],
                         ["github_pr"])
        self.assertEqual(projection["delivery_target"], "github_pr")
        self.assertEqual(projection["workflow_id"], mission_id)

    def test_F2_legacy_and_absent_parent_shapes_refuse_without_error(self):
        # A legacy DI-REMOTE workflow id never resolves in the registry.
        legacy = self.mission_parent.parent_mission_authority(
            self.delivery_document({
                "workflow_id": "wf-0001",
                "mission_authorization_digest_sha256": "a" * 64,
            }),
            self.service,
        )
        self.assertFalse(legacy["valid"])
        self.assertEqual(legacy["problem"],
                         self.mission_authorization.PROBLEM_UNKNOWN_AUTHORIZATION)
        absent = self.mission_parent.parent_mission_authority(
            self.delivery_document(None), self.service
        )
        self.assertFalse(absent["valid"])
        self.assertEqual(absent["problem"],
                         self.mission_parent.PROBLEM_PARENT_ABSENT)
        malformed = self.mission_parent.parent_mission_authority(
            {"no": "mission key"}, self.service
        )
        self.assertFalse(malformed["valid"])
        self.assertEqual(malformed["problem"],
                         self.mission_parent.PROBLEM_PARENT_ABSENT)

    def test_F3_mission_id_disagreement_and_stale_authority_refuse(self):
        mission_id = self.propose()["mission_id"]
        approved = self.approve(mission_id, 1)
        other = self.propose()["mission_id"]
        disagree = self.mission_parent.parent_mission_authority(
            self.delivery_document({
                "workflow_id": other,
                "mission_authorization_digest_sha256":
                    approved["authorization_digest_sha256"],
            }),
            self.service,
        )
        self.assertFalse(disagree["valid"])
        self.assertEqual(disagree["problem"],
                         self.mission_authorization.PROBLEM_WRONG_MISSION)
        # Edit after approval: the parent authority is gone.
        self.edit(mission_id, 1, objective="changed")
        revoked = self.mission_parent.parent_mission_authority(
            self.delivery_document({
                "workflow_id": mission_id,
                "mission_authorization_digest_sha256":
                    approved["authorization_digest_sha256"],
            }),
            self.service,
        )
        self.assertFalse(revoked["valid"])
        self.assertEqual(revoked["problem"],
                         self.mission_authorization.PROBLEM_REVOKED)
        # The digest still RESOLVES the record, so the projection names it.
        self.assertEqual(revoked["authorization_id"],
                         approved["authorization_id"])

    def test_F4_no_scope_target_and_wrong_digest_refuse(self):
        plain = self.propose(requested_delivery_target=None)["mission_id"]
        approved = self.approve(plain, 1)
        no_target = self.mission_parent.parent_mission_authority(
            self.delivery_document({
                "workflow_id": plain,
                "mission_authorization_digest_sha256":
                    approved["authorization_digest_sha256"],
            }),
            self.service,
        )
        self.assertEqual(no_target["problem"],
                         self.mission_authorization.PROBLEM_TARGET_OUTSIDE_SCOPE)
        wrong = self.mission_parent.parent_mission_authority(
            self.delivery_document({
                "workflow_id": plain,
                "mission_authorization_digest_sha256": "f" * 64,
            }),
            self.service,
        )
        self.assertEqual(wrong["problem"],
                         self.mission_authorization.PROBLEM_UNKNOWN_AUTHORIZATION)

    def test_F5_seam_is_read_only_effect_free_and_unwired(self):
        mission_id = self.propose()["mission_id"]
        approved = self.approve(mission_id, 1)
        before = self.read_bytes()
        listing = sorted(os.listdir(self.directory))
        for _ in range(3):
            self.mission_parent.parent_mission_authority(
                self.delivery_document({
                    "workflow_id": mission_id,
                    "mission_authorization_digest_sha256":
                        approved["authorization_digest_sha256"],
                }),
                self.service,
            )
        self.assertEqual(self.read_bytes(), before)
        self.assertEqual(sorted(os.listdir(self.directory)), listing)
        # The delivery machine does not call the seam in this bundle.
        for path in sorted((REPO_ROOT / "pr_delivery").glob("*.py")):
            if path.name == "mission_parent.py":
                continue
            self.assertEqual(
                _name_and_string_counts(path, "parent_mission_authority"), 0,
                path,
            )
            self.assertEqual(_name_and_string_counts(path, "mission_parent"), 0,
                             path)
        # No pr_delivery schema change: the mission block is still closed
        # to exactly its two keys, and the seam never names the delivery
        # store.
        source = (REPO_ROOT / "pr_delivery" / "authorization.py").read_text()
        self.assertIn('("workflow_id", "mission_authorization_digest_sha256")',
                      source)
        seam = (REPO_ROOT / "pr_delivery" / "mission_parent.py").read_text()
        for word in ("DeliveryStore", "pr_delivery.json", "subprocess", "git"):
            self.assertNotIn(word, seam.replace("github_pr", ""), word)


# ====================================================================
# F (continued). Task 7, Stage 2: the receipt attestation path
# ====================================================================
#
# Supervisor scope decision AUTHORIZE_OPTION_1_WITH_CONDITIONS. The seam
# gained a SECOND function, ``attest_validated_receipt``, which validates
# a delivery record and one stored receipt through the UNCHANGED existing
# validator, checks the Mission parent through the read-only seam above,
# and only then calls the Mission Core's distinct, non-authorizing
# ``attest_delivery_receipt`` operation. The fixtures below build a
# delivery record in memory that ``validate_authorization`` accepts and
# derive its receipts with the delivery layer's own ``receipts.derive``,
# so every case runs the REAL validator and the REAL consumer/service path
# with no git process; a mocked VALID string proves nothing here.

ATTEST_ROOT = "/di-attest-fixture"
ATTEST_OPERATION = "attest_delivery_receipt"
ATTEST_SEAM_FUNCTION = "attest_validated_receipt"
ATTEST_SERVICE_FILE = "mission/state_service.py"
ATTEST_SEAM_FILE = "pr_delivery/mission_parent.py"
ATTEST_KIND_FILE = "mission/state.py"


def delivery_record(workflow_id, mission_digest, now,
                    delivery_id="prd-" + "a" * 12, mission=True, maximal=False):
    """A delivery record the UNCHANGED ``validate_authorization`` accepts,
    built in memory: real candidate identity digest, evidence bound to it,
    resolved absolute paths under a root that need not exist, and the
    optional Mission parent block naming ``workflow_id`` and the Mission
    Authorization digest. Returned through ``new_authorization``, which
    validates before it returns."""
    from pr_delivery import authorization as auth
    from pr_delivery import candidate as candidate_module
    entries = [
        {"status": "M", "mode": "100644", "blob": "2" * 40, "path": "keep.txt"},
        {"status": "A", "mode": "100644", "blob": "1" * 40, "path": "src/pkg.py"},
    ]
    if maximal:
        # A GENUINE maximum-size record under the delivery contract's own
        # limits: MAX_CANDIDATE_ENTRIES entries in strict path order, a
        # PR body at MAX_PR_BODY_CHARS, human texts at MAX_HUMAN_TEXT_CHARS,
        # a reverification argv of MAX_REVERIFICATION_ARGV strings at the
        # 4096-character bound.
        entries = [{"status": "A", "mode": "100644", "blob": "%040x" % index,
                    "path": "src/%06d.py" % index}
                   for index in range(auth.MAX_CANDIDATE_ENTRIES)]
    digest = candidate_module.identity_digest(entries)
    base = "6" * 40
    common = {"candidate_identity_digest_sha256": digest, "base_oid": base,
              "recorded_at": now - 1000}
    authority = {
        "revision": 1, "previous_delivery_id": None,
        "workflow_identity": {"workflow_id": "wf-p1a6",
                              "engineering_task_id": "20260904-150441-159120"},
        "mission": ({"workflow_id": workflow_id,
                     "mission_authorization_digest_sha256": mission_digest}
                    if mission else None),
        "repository": {"realpath": ATTEST_ROOT + "/work",
                       "git_dir_realpath": ATTEST_ROOT + "/work/.git",
                       "canonical_host": "github.com", "owner": "octo",
                       "repo": "repo",
                       "repository_url": "https://github.com/octo/repo"},
        "remote": {"name": "origin",
                   "url_exact": "https://github.com/octo/repo.git",
                   "url_fetch": ATTEST_ROOT + "/remote.git",
                   "url_push": ATTEST_ROOT + "/remote.git",
                   "repository_url": "https://github.com/octo/repo"},
        "mode": "pull_request",
        "source": {"branch": "feature/p1-a6", "ref": "refs/heads/feature/p1-a6"},
        "target_base": {"branch": "main", "ref": "refs/heads/main"},
        "original_baseline": {"ref": "refs/heads/main", "commit_sha": base},
        "candidate": {"entries": entries, "entry_count": len(entries),
                      "identity_digest_sha256": digest},
        "evidence": {
            "engineering_complete": dict(
                common, task_id="20260904-150441-159120", status="COMPLETE",
                task_state_sha256="a" * 64),
            "reviewer_approve": dict(
                common, task_id="20260904-150441-159120", round=2,
                review_file_name="round-02.md", review_file_sha256="b" * 64,
                decision="APPROVE"),
            "independent_verification": dict(
                common, command_argv=["python3", "-m", "nothing"], exit_status=0,
                log_sha256="c" * 64, log_bytes=10, ran_at=now - 500),
        },
        "allowed_actions": list(auth.STEPS),
        "committer": {"name": "Delivery Human", "email": "human@example.com"},
        "reverification": {"argv": (
            ["/usr/bin/true"] + ["x" * 4096] * (auth.MAX_REVERIFICATION_ARGV - 1)
            if maximal else ["/usr/bin/true"])},
        "pr_content": {"title": "P1-A6",
                       "objective": "o" * auth.MAX_HUMAN_TEXT_CHARS if maximal else "o",
                       "architecture_notes": "a" * auth.MAX_HUMAN_TEXT_CHARS if maximal else "a",
                       "nonblocking_risks": "n" * auth.MAX_HUMAN_TEXT_CHARS if maximal else "n"},
        "human_authorization": {"identity": "human", "source": "local_terminal",
                                "authorized_at": now,
                                "confirmation_digest_sha256": "d" * 64},
        "expiration": {"policy": "absolute_deadline", "expires_at": now + 3600},
    }
    return auth.new_authorization(delivery_id, authority, now)


def with_receipt(record, step, state, now, binding=None):
    """A copy of ``record`` holding ONE receipt for ``step`` in ``state``,
    derived by the delivery layer's own ``receipts.derive`` (which
    validates it), with the step state the delivery contract requires for
    that receipt state. Validated again before it is returned."""
    from pr_delivery import authorization as auth
    from pr_delivery import receipts
    record = copy.deepcopy(record)
    phase_for_step = dict((v, k) for k, v in auth.STEP_FOR_PHASE.items())
    record["phase"] = phase_for_step[step]
    for earlier in auth.STEPS:
        if earlier == step:
            break
        record["steps"][earlier]["state"] = auth.STEP_NOT_NEEDED
    if binding is None:
        binding = dict(
            (field, ("7" * 40 if field.endswith(("sha", "oid")) else "v"))
            for field in auth.RECEIPT_BINDING_FIELDS[step])
        if "fast_forward" in binding:
            binding["fast_forward"] = True
    receipt = receipts.derive(record, step, binding, now)
    receipt["state"] = state
    record["steps"][step]["receipt"] = receipt
    record["steps"][step]["state"] = {
        auth.RECEIPT_EXECUTING: auth.STEP_EXECUTING,
        auth.RECEIPT_SUCCEEDED: auth.STEP_SUCCEEDED,
        auth.RECEIPT_FAILED_RETRYABLE: auth.STEP_FAILED_RETRYABLE,
    }.get(state, auth.STEP_PENDING)
    auth.validate_authorization(record)
    return record


def _docstring_positions(tree):
    positions = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                             ast.AsyncFunctionDef)):
            body = getattr(node, "body", [])
            if (body and isinstance(body[0], ast.Expr)
                    and isinstance(body[0].value, ast.Constant)
                    and isinstance(body[0].value.value, str)):
                positions.add((body[0].value.lineno, body[0].value.col_offset))
    return positions


_REFERENCE_CACHE = {}


def _reference_counts(sources, symbol):
    """Per file: every DECODED reference to ``symbol`` in the AST — a
    Name, an Attribute, a def / class / parameter / keyword-argument /
    import name equal to it, and any non-docstring string or bytes
    CONSTANT whose decoded value contains it (so ``"\\x61ttest..."``,
    f-string parts and ``getattr(x, "...")`` count). Comments and
    docstrings do not. Spelling is never compared: the value is."""
    counts = {}
    encoded = symbol.encode("utf-8")
    for relpath, source in sources.items():
        cached = _REFERENCE_CACHE.get((symbol, source))
        if cached is not None:
            counts[relpath] = cached
            continue
        tree = ast.parse(source)
        positions = _docstring_positions(tree)
        count = 0
        for node in ast.walk(tree):
            if isinstance(node, ast.Name) and node.id == symbol:
                count += 1
            elif isinstance(node, ast.Attribute) and node.attr == symbol:
                count += 1
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef,
                                   ast.ClassDef)) and node.name == symbol:
                count += 1
            elif isinstance(node, ast.arg) and node.arg == symbol:
                count += 1
            elif isinstance(node, ast.keyword) and node.arg == symbol:
                count += 1
            elif isinstance(node, ast.alias) and symbol in (
                node.name, node.asname
            ):
                count += 1
            elif isinstance(node, ast.Constant):
                value = node.value
                if isinstance(value, str) and symbol in value and (
                    (node.lineno, node.col_offset) not in positions
                ):
                    count += 1
                elif isinstance(value, bytes) and encoded in value:
                    count += 1
        _REFERENCE_CACHE[(symbol, source)] = count
        counts[relpath] = count
    return counts


def _target_names(target):
    """Every Name bound by an assignment-like target (Tuple / List /
    Starred unpacking included). Subscript and Attribute targets bind no
    name but MUTATE their root; they are reported apart."""
    names = []
    for node in ast.walk(target):
        if isinstance(node, ast.Name):
            names.append(node.id)
    return names


def _bound_names(scope):
    """Every name bound anywhere inside ``scope`` (a function or module
    node, nested scopes included) by ANY binding form Python has:
    assignment, augmented and annotated assignment, walrus, ``for`` /
    ``async for`` targets, ``with ... as``, ``except ... as``,
    comprehension targets, ``global`` / ``nonlocal``, ``def`` / ``async
    def`` / ``class`` / ``lambda`` names and every parameter kind,
    ``import`` / ``from ... import ... as``, ``del``, and ``match``
    capture / star / rest patterns."""
    bound = []
    for node in ast.walk(scope):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                bound.extend(_target_names(target))
        elif isinstance(node, (ast.AugAssign, ast.AnnAssign)):
            bound.extend(_target_names(node.target))
        elif isinstance(node, ast.NamedExpr):
            bound.extend(_target_names(node.target))
        elif isinstance(node, (ast.For, ast.AsyncFor)):
            bound.extend(_target_names(node.target))
        elif isinstance(node, (ast.With, ast.AsyncWith)):
            for item in node.items:
                if item.optional_vars is not None:
                    bound.extend(_target_names(item.optional_vars))
        elif isinstance(node, ast.ExceptHandler):
            if node.name is not None:
                bound.append(node.name)
        elif isinstance(node, ast.comprehension):
            bound.extend(_target_names(node.target))
        elif isinstance(node, (ast.Global, ast.Nonlocal)):
            bound.extend(node.names)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if node is not scope:
                bound.append(node.name)
        elif isinstance(node, ast.Delete):
            for target in node.targets:
                bound.extend(_target_names(target))
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                bound.append(alias.asname or alias.name.split(".")[0])
        elif isinstance(node, ast.arg):
            bound.append(node.arg)
        else:
            for attribute in ("name", "rest"):
                if type(node).__name__ in ("MatchAs", "MatchStar", "MatchMapping"):
                    value = getattr(node, attribute, None)
                    if isinstance(value, str):
                        bound.append(value)
    return bound


def _mutated_roots(scope):
    """Names whose OBJECT may be mutated inside ``scope``: the root Name
    of any Subscript / Attribute store or delete target, and the
    receiver of any method call (``x.update(...)``, ``x.pop(...)``)."""
    roots = []

    def root_of(node):
        while isinstance(node, (ast.Subscript, ast.Attribute)):
            node = node.value
        return node.id if isinstance(node, ast.Name) else None

    for node in ast.walk(scope):
        targets = []
        if isinstance(node, ast.Assign):
            targets = node.targets
        elif isinstance(node, (ast.AugAssign, ast.AnnAssign)):
            targets = [node.target]
        elif isinstance(node, ast.Delete):
            targets = node.targets
        elif isinstance(node, (ast.For, ast.AsyncFor)):
            targets = [node.target]
        elif isinstance(node, ast.NamedExpr):
            targets = [node.target]
        for target in targets:
            for sub in ast.walk(target):
                if isinstance(sub, (ast.Subscript, ast.Attribute)):
                    name = root_of(sub)
                    if name is not None:
                        roots.append(name)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            name = root_of(node.func)
            if name is not None:
                roots.append(name)
    return roots


def _dump(node):
    return ast.dump(node)


def _expr(text):
    return ast.dump(ast.parse(text, mode="eval").body)


def _stmt(text):
    return ast.dump(ast.parse(text).body[0])


def _body_without_docstring(function):
    body = list(function.body)
    if (body and isinstance(body[0], ast.Expr)
            and isinstance(body[0].value, ast.Constant)
            and isinstance(body[0].value.value, str)):
        body = body[1:]
    return body


# The validating function, as an exact program: six statements, each
# pinned by AST equality (formatting, comments and the docstring are
# free; nothing else is). Every returned field is a read of ``record``
# or ``receipt`` — the frozen copy the unchanged validators accepted —
# or of the ``step`` parameter.
VALIDATED_RECEIPT_PROGRAM = (
    "record = _plain_copy(delivery_document)",
    "delivery_authorization.validate_authorization(record)",
    'receipt = record["steps"][step]["receipt"]',
    "if receipt is None:\n    return None",
    'delivery_authorization.validate_receipt(receipt, step, record["delivery_id"],'
    ' record["authority_digest_sha256"],'
    ' "delivery %s step %s receipt" % (record["delivery_id"], step))',
)
VALIDATED_RECEIPT_FIELDS = {
    "receipt_id": 'receipt["receipt_id"]',
    "receipt_digest_sha256": 'receipt["receipt_digest_sha256"]',
    "delivery_id": 'record["delivery_id"]',
    "step": "step",
    "receipt_state": 'receipt["state"]',
    "step_state": 'record["steps"][step]["state"]',
    "parent_authority_digest_sha256": 'record["authority_digest_sha256"]',
    "authorization_digest_sha256": (
        'None if record["mission"] is None'
        ' else record["mission"]["mission_authorization_digest_sha256"]'),
    "succeeded": (
        'receipt["state"] == delivery_authorization.RECEIPT_SUCCEEDED'
        ' and record["steps"][step]["state"] == delivery_authorization.STEP_SUCCEEDED'),
    "record": "record",
}
ATTESTATION_INPUT_FIELDS = (
    "receipt_id", "receipt_digest_sha256", "delivery_id", "step", "receipt_state",
    "step_state", "parent_authority_digest_sha256", "authorization_digest_sha256",
)
# The seam's attestation program, WHOLE and DERIVED: every definition in
# the module reachable from the recording function through the names it
# and they use (round 10: the compared set is computed from the source,
# never hand-listed — a hand list of four is exactly what round 10
# exploited via the preflight helper). The pin compares each reachable
# definition by AST equality after docstrings (decorators, bases,
# metaclass keywords, signatures and every statement included), and
# requires the golden set to EQUAL the reachable set, so a helper that
# joins the call path is compared the moment it is reached and a stale
# golden entry fails too. Round 08 findings 1, 2 and 4; round 10.
SEAM_ATTESTATION_PROGRAM = 'def _projection(check_dict, workflow_id, delivery_target):\n    projection = dict(check_dict)\n    projection["workflow_id"] = workflow_id\n    projection["delivery_target"] = delivery_target\n    assert tuple(sorted(projection)) == tuple(sorted(PROJECTION_KEYS))\n    return projection\n\n\ndef parent_mission_authority(delivery_document, mission_service):\n    """Validate the delivery record\'s Mission parent through the Mission\n    Core\'s one validation path, always for the ``github_pr`` delivery\n    target: the target is not a parameter, so no caller can disable or\n    redirect the check. Read-only; never raises for a malformed or absent\n    parent, it refuses with a problem code instead."""\n    delivery_target = mission_record.DELIVERY_TARGET_GITHUB_PR\n    mission = None\n    if isinstance(delivery_document, dict):\n        mission = delivery_document.get("mission")\n    if not isinstance(mission, dict):\n        return _projection({\n            "valid": False,\n            "problem": PROBLEM_PARENT_ABSENT,\n            "detail": ("the delivery record carries no Mission parent (the"\n                       " legacy/manual standalone shape); no parent Mission"\n                       " authority can be confirmed"),\n            "authorization_id": None, "mission_id": None, "revision": None,\n            "proposal_digest_sha256": None, "authorized_action_scope": None,\n            "authorized_delivery_targets": None,\n        }, None, delivery_target)\n    workflow_id = mission.get("workflow_id")\n    digest = mission.get("mission_authorization_digest_sha256")\n    check = mission_service.check_parent_authority(\n        workflow_id, digest, delivery_target\n    )\n    return _projection(check.as_dict(), workflow_id, delivery_target)\n\n\ndef _attestation(valid, problem, detail, receipt_id=None, step=None,\n                 receipt_state=None, succeeded=False, delivery_id=None,\n                 mission_id=None, authorization_id=None, revision=None,\n                 outcome=None):\n    projection = {\n        "valid": valid, "problem": problem, "detail": detail,\n        "receipt_id": receipt_id, "step": step, "receipt_state": receipt_state,\n        "succeeded": succeeded, "delivery_id": delivery_id,\n        "mission_id": mission_id, "authorization_id": authorization_id,\n        "revision": revision, "outcome": outcome,\n    }\n    assert tuple(sorted(projection)) == tuple(sorted(ATTESTATION_KEYS))\n    return projection\n\n\ndef _preflight_problem(document, step):\n    """Why the caller\'s document or step is outside the preflight bounds,\n    or None. Exact builtin types only (``type(x) is dict / list / str /\n    int / float / bool / NoneType``; no subclass, no other object); every\n    bound applied BEFORE the value is copied, formatted or compared; the\n    remaining item budget charged for a container\'s children BEFORE any\n    of them is enqueued, so the pending stack never holds more than the\n    budget and an oversized container costs one length check; and every\n    message built from constants — never from the input. Nothing here\n    judges string CONTENT: a string the unchanged store can hold is\n    carried to the unchanged validator exactly as it came."""\n    if type(step) is not str:\n        return "the step is not a string"\n    if len(step) > MAX_DELIVERY_DOCUMENT_KEY_CHARS:\n        return "the step is longer than %d characters" % MAX_DELIVERY_DOCUMENT_KEY_CHARS\n    if type(document) is not dict:\n        return "the delivery document is not a plain object"\n    remaining = MAX_DELIVERY_DOCUMENT_ITEMS - 1\n    pending = [(document, 1)]\n    while pending:\n        container, depth = pending.pop()\n        if depth > MAX_DELIVERY_DOCUMENT_DEPTH:\n            return ("the delivery document is nested deeper than %d levels"\n                    % MAX_DELIVERY_DOCUMENT_DEPTH)\n        size = len(container)\n        if size > remaining:\n            return ("the delivery document holds more than %d items"\n                    % MAX_DELIVERY_DOCUMENT_ITEMS)\n        remaining = remaining - size\n        if type(container) is dict:\n            for key in dict.keys(container):\n                if type(key) is not str:\n                    return "the delivery document has a key that is not a string"\n                if len(key) > MAX_DELIVERY_DOCUMENT_KEY_CHARS:\n                    return ("the delivery document has a key longer than %d"\n                            " characters" % MAX_DELIVERY_DOCUMENT_KEY_CHARS)\n            children = dict.values(container)\n        else:\n            children = container\n        for value in children:\n            kind = type(value)\n            if kind is dict or kind is list:\n                pending.append((value, depth + 1))\n            elif kind is str:\n                if len(value) > MAX_DELIVERY_DOCUMENT_STR_CHARS:\n                    return ("the delivery document holds a string longer than %d"\n                            " characters" % MAX_DELIVERY_DOCUMENT_STR_CHARS)\n            elif kind is int:\n                if value.bit_length() > MAX_DELIVERY_DOCUMENT_INT_BITS:\n                    return ("the delivery document holds an integer wider than %d"\n                            " bits" % MAX_DELIVERY_DOCUMENT_INT_BITS)\n            elif kind is not float and kind is not bool and value is not None:\n                return "the delivery document holds a value that is not plain data"\n    return None\n\n\ndef _plain_copy(value):\n    """A private structural copy of preflight-accepted plain data: every\n    dict and list is rebuilt, every string, number, boolean and null is\n    the same immutable value. No serialization is involved, so nothing\n    is re-encoded, no two distinct keys can collapse and no value can\n    change: the copy is faithful by construction."""\n    kind = type(value)\n    if kind is dict:\n        return dict((key, _plain_copy(item)) for key, item in dict.items(value))\n    if kind is list:\n        return [_plain_copy(item) for item in value]\n    return value\n\n\n@dataclass(frozen=True)\nclass ValidatedReceipt:\n    """The bound values of ONE receipt the unchanged validator accepted,\n    every field read from the same private copy it validated. Immutable,\n    with no base class, no metaclass and no method: nothing runs at or\n    after construction that could replace a field."""\n\n    receipt_id: str\n    receipt_digest_sha256: str\n    delivery_id: str\n    step: str\n    receipt_state: str\n    step_state: str\n    parent_authority_digest_sha256: str\n    authorization_digest_sha256: str\n    succeeded: bool\n    record: dict\n\n\ndef validated_receipt(delivery_document, step):\n    """Validate FIRST, through the unchanged contract, over a private\n    structural copy of the caller\'s document, and return the bound values\n    of the receipt that was validated — or None when the step holds no\n    receipt. Pinned by whole-program AST equality: ``record`` and\n    ``receipt`` are each bound exactly once, and every returned field is a\n    read of those two names (and of ``step``). Raises the contract\'s\n    ``AuthorizationError`` on any validation failure; the caller\n    preflights the document first."""\n    record = _plain_copy(delivery_document)\n    delivery_authorization.validate_authorization(record)\n    receipt = record["steps"][step]["receipt"]\n    if receipt is None:\n        return None\n    # Expected step, delivery identity and parent authority digest come\n    # from the VALIDATED copy, never from the receipt under check.\n    delivery_authorization.validate_receipt(\n        receipt, step, record["delivery_id"], record["authority_digest_sha256"],\n        "delivery %s step %s receipt" % (record["delivery_id"], step))\n    return ValidatedReceipt(\n        receipt_id=receipt["receipt_id"],\n        receipt_digest_sha256=receipt["receipt_digest_sha256"],\n        delivery_id=record["delivery_id"],\n        step=step,\n        receipt_state=receipt["state"],\n        step_state=record["steps"][step]["state"],\n        parent_authority_digest_sha256=record["authority_digest_sha256"],\n        authorization_digest_sha256=(\n            None if record["mission"] is None\n            else record["mission"]["mission_authorization_digest_sha256"]),\n        succeeded=(\n            receipt["state"] == delivery_authorization.RECEIPT_SUCCEEDED\n            and record["steps"][step]["state"] == delivery_authorization.STEP_SUCCEEDED),\n        record=record,\n    )\n\n\ndef attest_validated_receipt(delivery_document, step, mission_service,\n                             operation_id, expected_sequence, context):\n    """Preflight, validate through ``validated_receipt``, check the Mission\n    parent over the same private copy, then record through the Mission\n    Core\'s distinct operation exactly the values that were validated —\n    the receipt\'s verbatim state AND the step\'s verbatim state, so the\n    Mission side can derive the same completion answer as ``succeeded``\n    here and never a more positive one. The Mission call is the LAST\n    statement, reachable only after every step before it passed, and its\n    attestation is built solely from attribute reads of the\n    ``ValidatedReceipt`` and the parent projection.\n\n    Raises the contract\'s ``AuthorizationError`` when the delivery record\n    or the receipt is malformed, tampered or bound to another step,\n    delivery or authority; returns a refusal projection (no Mission call)\n    when the document or step is outside the preflight bounds, the step\n    is unknown, the step holds no receipt, or the Mission parent does not\n    validate; otherwise returns the attestation projection carrying the\n    Mission operation\'s outcome. A Mission-side refusal (stale sequence,\n    authority no longer valid, already attested) raises the Mission\n    Core\'s ``MissionError`` with nothing written."""\n    unbounded = _preflight_problem(delivery_document, step)\n    if unbounded is not None:\n        return _attestation(False, PROBLEM_DOCUMENT_UNBOUNDED,\n                            unbounded + "; nothing was validated or attested")\n    if step not in delivery_authorization.STEPS:\n        return _attestation(\n            False, PROBLEM_STEP_UNKNOWN,\n            "the attestation step is not one of the closed delivery steps;"\n            " nothing was attested", step=step)\n    validated = validated_receipt(delivery_document, step)\n    if validated is None:\n        return _attestation(\n            False, PROBLEM_RECEIPT_ABSENT,\n            "the delivery record holds no receipt for the step; there is nothing"\n            " to attest", step=step)\n    parent = parent_mission_authority(validated.record, mission_service)\n    if not parent["valid"]:\n        return _attestation(\n            False, PROBLEM_PARENT_INVALID,\n            "the delivery record\'s Mission parent does not validate (%s: %s);"\n            " the receipt is not attested" % (parent["problem"], parent["detail"]),\n            receipt_id=validated.receipt_id, step=validated.step,\n            receipt_state=validated.receipt_state,\n            delivery_id=validated.delivery_id, mission_id=parent["mission_id"],\n            authorization_id=parent["authorization_id"], revision=parent["revision"])\n    outcome = mission_service.attest_delivery_receipt(\n        parent["mission_id"], operation_id, expected_sequence, {\n            "receipt_id": validated.receipt_id,\n            "receipt_digest_sha256": validated.receipt_digest_sha256,\n            "delivery_id": validated.delivery_id,\n            "step": validated.step,\n            "receipt_state": validated.receipt_state,\n            "step_state": validated.step_state,\n            "parent_authority_digest_sha256": validated.parent_authority_digest_sha256,\n            "authorization_digest_sha256": validated.authorization_digest_sha256,\n        }, context)\n    return _attestation(\n        True, None,\n        "the existing receipt validator accepted receipt %s of delivery %s"\n        " for step %s in state %s and the Mission parent validates; the"\n        " receipt reference is attested as artifact %s (succeeded: %s)"\n        % (validated.receipt_id, validated.delivery_id, validated.step,\n           validated.receipt_state, outcome["artifact_id"], validated.succeeded),\n        receipt_id=validated.receipt_id, step=validated.step,\n        receipt_state=validated.receipt_state, succeeded=validated.succeeded,\n        delivery_id=validated.delivery_id, mission_id=parent["mission_id"],\n        authorization_id=parent["authorization_id"], revision=parent["revision"],\n        outcome=outcome)\n'
# Everything the seam module's top level may hold, by kind and name.
SEAM_MODULE_IMPORTS = (("dataclasses", "dataclass"), ("mission", "record"),
                       ("pr_delivery", "authorization"))
SEAM_MODULE_DEFINITIONS = (
    "_projection", "parent_mission_authority", "_attestation", "_preflight_problem",
    "_plain_copy", "ValidatedReceipt", "validated_receipt", "attest_validated_receipt",
)
SEAM_MODULE_CONSTANTS = (
    "PROBLEM_PARENT_ABSENT", "PROBLEM_RECEIPT_ABSENT", "PROBLEM_STEP_UNKNOWN",
    "PROBLEM_PARENT_INVALID", "PROBLEM_DOCUMENT_UNBOUNDED",
    "MAX_DELIVERY_DOCUMENT_ITEMS", "MAX_DELIVERY_DOCUMENT_DEPTH",
    "MAX_DELIVERY_DOCUMENT_STR_CHARS", "MAX_DELIVERY_DOCUMENT_KEY_CHARS",
    "MAX_DELIVERY_DOCUMENT_INT_BITS",
    "ATTESTATION_KEYS", "PROJECTION_KEYS",
)


def _pure_literal(node):
    """A module-level expression that cannot execute anything: a string,
    number, boolean or None constant, a tuple or list of pure literals,
    or a numeric negation. Nothing else — no name, call, attribute,
    subscript, comprehension, f-string, walrus, lambda or operator."""
    if isinstance(node, ast.Constant):
        return isinstance(node.value, (str, int, float, bool, bytes)) or node.value is None
    if isinstance(node, (ast.Tuple, ast.List)):
        return all(_pure_literal(item) for item in node.elts)
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
        return isinstance(node.operand, ast.Constant) and isinstance(
            node.operand.value, (int, float)) and not isinstance(node.operand.value, bool)
    return False


def _reachable_definitions(tree, entry):
    """The module-level definitions reachable from ``entry`` by following
    every Name a reached definition uses (calls, references, defaults,
    decorators, annotations and bases included, since ast.walk covers
    them all), transitively. DERIVED from the source: nothing is listed."""
    definitions = dict((node.name, node) for node in tree.body
                       if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef,
                                            ast.ClassDef)))
    reached, pending = set(), [entry]
    while pending:
        name = pending.pop()
        if name in reached or name not in definitions:
            continue
        reached.add(name)
        for sub in ast.walk(definitions[name]):
            if isinstance(sub, ast.Name) and sub.id in definitions:
                pending.append(sub.id)
    return reached


def _strip_docstrings(node):
    """A deep copy of a definition with every docstring (its own and any
    nested one) removed, for AST equality that is free of prose."""
    node = copy.deepcopy(node)
    for sub in ast.walk(node):
        body = getattr(sub, "body", None)
        if isinstance(body, list) and body and isinstance(body[0], ast.Expr) and (
            isinstance(body[0].value, ast.Constant)
            and isinstance(body[0].value.value, str)
        ):
            del body[0]
            if not body:
                body.append(ast.Pass())
    return node


def _function(tree, name):
    found = [node for node in ast.walk(tree)
             if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
             and node.name == name]
    return found[0] if len(found) == 1 else None


def _class(tree, name):
    found = [node for node in ast.walk(tree)
             if isinstance(node, ast.ClassDef) and node.name == name]
    return found[0] if len(found) == 1 else None


def attestation_confinement_problems(sources):
    """The receipt-attestation confinement and binding pin over a DERIVED
    product source map ``{relpath: source}`` (``product_sources`` walks
    the tree on disk). Returns the list of violations; empty when the
    tree is confined. What it proves, exactly:

    REFERENCES (decoded, not spelled). The Mission operation
    ``attest_delivery_receipt`` is defined once, in
    mission/state_service.py, and every DECODED reference to it in the
    product tree — Name, Attribute, def / class / parameter / keyword /
    import name, or a non-docstring string or bytes constant whose decoded
    value contains it — is one of exactly three: that definition, the
    kind literal in mission/state.py, and ONE attribute call in
    pr_delivery/mission_parent.py. Every other discovered file has zero.

    THE PROGRAM, whole, over a DERIVED set. Every definition in the seam
    reachable from ``attest_validated_receipt`` through the names it and
    they use, transitively (``_reachable_definitions``; today all eight,
    including the preflight, the refusal-projection helper, the parent
    check and its projection helper), is, by AST equality after
    docstrings, exactly the corresponding entry of
    ``SEAM_ATTESTATION_PROGRAM``, and the golden set must EQUAL the
    reachable set: decorators, bases, metaclass keywords, signatures and
    every statement are part of the comparison, and the set itself is
    computed from the source, so there is no whitelist that could be
    incomplete and no hand list a helper could sit outside of (round 08
    findings 1 and 2; round 10). A definition the recording path does not
    reach is not compared; the closed definitions list still forbids its
    existence.
    The module's top level is CLOSED BY EXPRESSION AND POSITION, not by
    name (round 09 finding 1): its docstring; then exactly the three
    named ``from`` imports (no ``import`` statement at all); then the
    named constants, each a name bound to a PURE LITERAL (a string,
    number, boolean, None, or a tuple / list of those — no call, name,
    attribute, subscript, f-string, lambda, walrus or operator, so an
    initializer cannot execute anything); then the named definitions, in
    order, with nothing after the last one; no definition decorated
    except the dataclass; every function default a pure literal and no
    annotation anywhere, so creating the function objects evaluates
    nothing; exactly one class. Import-time execution of the seam is
    therefore exactly: three imports, literal bindings, function-object
    creation, and one ``dataclass(frozen=True)`` call on a class with no
    base and no metaclass.

    DATA FLOW in the seam, not statement order (diagnostics inside the
    whole-program equality). (1) ``validated_receipt`` is the
    six-statement program ``VALIDATED_RECEIPT_PROGRAM`` + a ``return
    ValidatedReceipt(...)`` whose keyword expressions are exactly
    ``VALIDATED_RECEIPT_FIELDS``: ``record`` is bound once as a private
    STRUCTURAL copy (``_plain_copy``: dicts and lists rebuilt, immutable
    scalars carried over; no serialization, so no two keys can collapse
    and no value can change) of the parameter, the unchanged ``validate_authorization`` is called on it,
    ``receipt`` is bound once from it, the unchanged ``validate_receipt``
    is called on that receipt with the step PARAMETER and the frozen
    record's delivery id and authority digest, and every returned field is
    a read of ``record``, ``receipt`` or ``step``. AST equality leaves no
    room for a substitution, a rebinding, a mutation or an extra call
    between validation and return. (2) ``ValidatedReceipt`` is a
    ``@dataclass(frozen=True)`` with exactly the ten fields, no method,
    no base class and no metaclass keyword, so nothing runs at or after
    construction that could replace a field. (3) In
    ``attest_validated_receipt``: the only names bound in the body, by ANY
    binding form (assignment, augmented / annotated assignment, walrus,
    for / with / except targets, comprehensions, global / nonlocal, nested
    def / class / lambda and their parameters, import, del, match
    patterns), are ``unbounded``, ``validated``, ``parent`` and
    ``outcome``, each bound exactly once, ``validated`` from
    ``validated_receipt(delivery_document, step)`` and ``parent`` from
    ``parent_mission_authority(validated.record, mission_service)``; no
    object reachable from ``validated`` or ``parent`` is mutated (no
    subscript / attribute store, delete or method call on them) and
    neither is passed or aliased anywhere but those two places; the
    function has no try, no nested scope; every top-level statement before
    the Mission call is either one of those assignments or an ``if`` whose
    body ends in ``return``; the Mission call is the whole value of the
    ``outcome`` assignment, its first argument is ``parent["mission_id"]``
    and its attestation is a dict literal whose keys are exactly the seven
    attestation input fields, each mapped to ``validated.<same name>``;
    one ``return`` follows and nothing else. So every recorded value is an
    attribute read of the immutable result of the one validating call.
    (4) ``delivery_authorization`` and ``json`` are bound in the seam only
    by their import, in no function by any form, and nothing in the seam
    defines ``validate_receipt`` or ``validate_authorization``.

    STATED LIMIT. This is repository source and call-path confinement of
    decoded literal references, whole-program equality of every seam
    definition reachable from the recording function, and closure of the
    seam module's own import-time execution as listed above. Within the
    repository's own call path, what it does NOT prove is the content of
    the three imported modules (``dataclasses``, ``mission.record``,
    ``pr_delivery.authorization``): what they execute at their own import
    and what their functions do when called is pinned by their own
    suites, not by this pin. Outside the repository's source it proves
    nothing: what the builtins the seam names resolve to at run time, and
    code that runs in the process from anywhere else. The private structural copy
    is the runtime half of the binding (a caller object cannot change
    between validation and recording; a non-plain object is refused by
    the preflight before either, and no string the unchanged store can
    hold is refused). It is not protection against arbitrary code running
    in the process, runtime monkey-patching of the service or the
    validator, or a malicious rewrite of storage or source; a computed or
    concatenated name is beyond any static pin; and none of it is
    cryptographic authenticity."""
    problems = []
    counts = _reference_counts(sources, ATTEST_OPERATION)
    expected = {ATTEST_SERVICE_FILE: 1, ATTEST_SEAM_FILE: 1, ATTEST_KIND_FILE: 1}
    for relpath, count in sorted(counts.items()):
        if count != expected.get(relpath, 0):
            problems.append("%s references %s %d time(s); expected %d"
                            % (relpath, ATTEST_OPERATION, count,
                               expected.get(relpath, 0)))
    for relpath in expected:
        if relpath not in counts:
            problems.append("%s is missing from the scanned tree" % relpath)
    definitions = [relpath for relpath, source in sources.items()
                   if _reference_counts({relpath: source}, ATTEST_OPERATION)[relpath]
                   for node in ast.walk(ast.parse(source))
                   if isinstance(node, ast.FunctionDef) and node.name == ATTEST_OPERATION]
    if definitions != [ATTEST_SERVICE_FILE]:
        problems.append("definitions of %s: %r" % (ATTEST_OPERATION, definitions))
    seam_source = sources.get(ATTEST_SEAM_FILE)
    if seam_source is None:
        return problems
    tree = ast.parse(seam_source)
    # (0) WHOLE-PROGRAM equality of the attestation section, and a closed
    # module top level: the four definitions are, by AST equality after
    # docstrings, exactly SEAM_ATTESTATION_PROGRAM (decorators, bases,
    # metaclass keywords, signatures and every statement included), and
    # the module holds nothing at its top level but its docstring, the
    # three imports, the named constants and the named definitions.
    expected = dict((node.name, _dump(_strip_docstrings(node)))
                    for node in ast.parse(SEAM_ATTESTATION_PROGRAM).body)
    reachable = _reachable_definitions(tree, ATTEST_SEAM_FUNCTION)
    if reachable != set(expected):
        problems.append("the definitions reachable from %s are %r but the golden"
                        " program holds %r; every reachable definition is compared"
                        " and every golden entry must be reachable"
                        % (ATTEST_SEAM_FUNCTION, sorted(reachable), sorted(expected)))
    actual = dict((node.name, _dump(_strip_docstrings(node))) for node in tree.body
                  if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef,
                                       ast.ClassDef)) and node.name in reachable)
    for name in sorted(reachable):
        if actual.get(name) != expected.get(name):
            problems.append("%s is not the reviewed program (decorators, bases,"
                            " keywords, signature and body are all part of it)"
                            % name)
    kinds = []
    for index, node in enumerate(tree.body):
        if index == 0 and isinstance(node, ast.Expr) and isinstance(
            node.value, ast.Constant
        ) and isinstance(node.value.value, str):
            continue
        if isinstance(node, ast.ImportFrom) and len(node.names) == 1 and (
            node.level == 0
        ):
            kinds.append(("import", (node.module, node.names[0].name)))
        elif isinstance(node, ast.Assign) and len(node.targets) == 1 and (
            isinstance(node.targets[0], ast.Name)
        ):
            # Round 09 finding 1: a constant is a NAME bound to a PURE
            # LITERAL. An assignment whose expression could execute
            # anything (a call, a name, an attribute, a subscript, an
            # f-string, a lambda, a walrus, an operator) is not a
            # constant, whatever its target is called or its value is.
            if not _pure_literal(node.value):
                problems.append("the seam's top-level assignment to %s is not a"
                                " pure literal" % node.targets[0].id)
            kinds.append(("constant", node.targets[0].id))
        elif isinstance(node, (ast.FunctionDef, ast.ClassDef)):
            kinds.append(("definition", node.name))
            if isinstance(node, ast.FunctionDef):
                # A function object's creation evaluates its decorators,
                # its default values and its annotations at import time:
                # every default must be a pure literal and no annotation
                # may exist (the golden definitions carry none either).
                arguments = node.args
                for default in list(arguments.defaults) + [
                    d for d in arguments.kw_defaults if d is not None
                ]:
                    if not _pure_literal(default):
                        problems.append("%s has a default value that is not a pure"
                                        " literal" % node.name)
                annotated = [a for a in (arguments.posonlyargs + arguments.args
                                         + arguments.kwonlyargs
                                         + [arguments.vararg, arguments.kwarg])
                             if a is not None and a.annotation is not None]
                if annotated or node.returns is not None:
                    problems.append("%s carries an annotation" % node.name)
        else:
            problems.append("the seam's top level holds a statement that is not an"
                            " import, a constant assignment or a definition: %s"
                            % type(node).__name__)
    # Position: docstring, then imports, then constants, then definitions,
    # each group contiguous and in that order; nothing after the last
    # definition, so no assignment can run after a definition it could
    # wrap.
    order = [k[0] for k in kinds]
    grouped = ["import"] * order.count("import") + ["constant"] * order.count(
        "constant") + ["definition"] * order.count("definition")
    if order != grouped:
        problems.append("the seam's top level is not imports, then constants, then"
                        " definitions: %r" % (order,))
    if sum(1 for node in tree.body if isinstance(node, ast.ClassDef)) != 1:
        problems.append("the seam defines a class other than ValidatedReceipt")
    if [k[1] for k in kinds if k[0] == "import"] != list(SEAM_MODULE_IMPORTS):
        problems.append("the seam's imports are not exactly %r" % (SEAM_MODULE_IMPORTS,))
    if sorted(k[1] for k in kinds if k[0] == "constant") != sorted(SEAM_MODULE_CONSTANTS):
        problems.append("the seam's constants are not exactly the named set")
    if [k[1] for k in kinds if k[0] == "definition"] != list(SEAM_MODULE_DEFINITIONS):
        problems.append("the seam's definitions are not exactly %r"
                        % (SEAM_MODULE_DEFINITIONS,))
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.ClassDef)):
            if node.decorator_list and not (
                node.name == "ValidatedReceipt"
                and [_dump(d) for d in node.decorator_list]
                == [_expr("dataclass(frozen=True)")]
            ):
                problems.append("%s is decorated; the only permitted decorator is"
                                " dataclass(frozen=True) on ValidatedReceipt"
                                % node.name)
            if isinstance(node, ast.ClassDef) and (node.bases or node.keywords):
                problems.append("%s has a base class or a metaclass keyword"
                                % node.name)
    # (4) the validator module and dataclass are bound only by import.
    imports = {}
    for node in tree.body:
        if isinstance(node, ast.ImportFrom) and node.module == "pr_delivery":
            for alias in node.names:
                if alias.name == "authorization":
                    imports["validator"] = alias.asname or alias.name
        if isinstance(node, ast.Import):
            problems.append("the seam imports a module (no serialization, no os)")
    validator = imports.get("validator")
    if validator is None:
        problems.append("the seam must import pr_delivery.authorization")
        return problems
    module_bindings = []
    for node in tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            continue
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            module_bindings.append(node.name)
        module_bindings.extend(_bound_names(node))
    for name in (validator, "dataclass", "validated_receipt", "attest_validated_receipt",
                 "parent_mission_authority", "ValidatedReceipt", "_plain_copy"):
        found = module_bindings.count(name)
        wanted = 0 if name in (validator, "dataclass") else 1
        if found != wanted:
            problems.append("%s is bound %d time(s) in the seam; expected %d"
                            % (name, found, wanted))
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if node.name in ("validate_receipt", "validate_authorization"):
                problems.append("the seam defines %s" % node.name)
            for name in (validator, "dataclass", "_plain_copy", "ValidatedReceipt"):
                if name in _bound_names(node):
                    problems.append("%s binds %s" % (node.name, name))
    # (1) the validating function is exactly the program.
    validating = _function(tree, "validated_receipt")
    if validating is None:
        problems.append("validated_receipt is not defined exactly once")
        return problems
    if [a.arg for a in validating.args.args] != ["delivery_document", "step"] or (
        validating.args.vararg or validating.args.kwarg or validating.args.kwonlyargs
        or validating.args.posonlyargs or validating.args.defaults
    ):
        problems.append("validated_receipt has an unexpected signature")
    body = _body_without_docstring(validating)
    program = [text.replace("delivery_authorization", validator)
               for text in VALIDATED_RECEIPT_PROGRAM]
    if len(body) != len(program) + 1:
        problems.append("validated_receipt has %d statements; expected %d"
                        % (len(body), len(program) + 1))
        return problems
    for index, text in enumerate(program):
        if _dump(body[index]) != _stmt(text):
            problems.append("validated_receipt statement %d is not %r"
                            % (index + 1, text))
    final = body[-1]
    constructor = getattr(final, "value", None)
    if not (isinstance(final, ast.Return) and isinstance(constructor, ast.Call)
            and isinstance(constructor.func, ast.Name)
            and constructor.func.id == "ValidatedReceipt" and not constructor.args):
        problems.append("validated_receipt must end by returning ValidatedReceipt(...)")
        return problems
    keywords = dict((k.arg, k.value) for k in constructor.keywords)
    if set(keywords) != set(VALIDATED_RECEIPT_FIELDS) or len(constructor.keywords) != (
        len(VALIDATED_RECEIPT_FIELDS)
    ):
        problems.append("ValidatedReceipt is built with fields %r" % sorted(keywords))
    for field, text in VALIDATED_RECEIPT_FIELDS.items():
        if field in keywords and _dump(keywords[field]) != _expr(
            text.replace("delivery_authorization", validator)
        ):
            problems.append("ValidatedReceipt.%s is not read as %r" % (field, text))
    # (2) the frozen dataclass.
    klass = _class(tree, "ValidatedReceipt")
    if klass is None:
        problems.append("ValidatedReceipt is not defined exactly once")
        return problems
    decorators = [_dump(d) for d in klass.decorator_list]
    if decorators != [_expr("dataclass(frozen=True)")]:
        problems.append("ValidatedReceipt is not @dataclass(frozen=True)")
    if klass.bases or klass.keywords:
        problems.append("ValidatedReceipt has a base class or a metaclass keyword")
    if validating.decorator_list:
        problems.append("validated_receipt is decorated")
    fields = [n.target.id for n in klass.body
              if isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name)]
    if fields != list(VALIDATED_RECEIPT_FIELDS):
        problems.append("ValidatedReceipt fields are %r" % fields)
    if any(not isinstance(n, (ast.AnnAssign, ast.Expr)) for n in klass.body):
        problems.append("ValidatedReceipt carries a method or other statement")
    if not any(isinstance(n, ast.ImportFrom) and n.module == "dataclasses"
               and any(a.name == "dataclass" and a.asname is None for a in n.names)
               for n in tree.body):
        problems.append("dataclass is not imported from dataclasses")
    # (3) the caller's data flow.
    caller = _function(tree, ATTEST_SEAM_FUNCTION)
    if caller is None:
        problems.append("%s is not defined exactly once" % ATTEST_SEAM_FUNCTION)
        return problems
    parameters = ["delivery_document", "step", "mission_service", "operation_id",
                  "expected_sequence", "context"]
    if [a.arg for a in caller.args.args] != parameters or (
        caller.args.vararg or caller.args.kwarg or caller.args.kwonlyargs
        or caller.args.posonlyargs or caller.args.defaults
    ):
        problems.append("%s has an unexpected signature" % ATTEST_SEAM_FUNCTION)
    if caller.decorator_list:
        problems.append("%s is decorated" % ATTEST_SEAM_FUNCTION)
    if any(isinstance(n, ast.Try) for n in ast.walk(caller)):
        problems.append("%s contains a try statement" % ATTEST_SEAM_FUNCTION)
    if any(isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef,
                          ast.Lambda)) for n in ast.walk(caller) if n is not caller):
        problems.append("%s contains a nested scope" % ATTEST_SEAM_FUNCTION)
    bound = [n for n in _bound_names(caller) if n not in parameters]
    if sorted(bound) != ["outcome", "parent", "unbounded", "validated"]:
        problems.append("%s binds %r; expected exactly one binding each of"
                        " unbounded, validated, parent, outcome"
                        % (ATTEST_SEAM_FUNCTION, sorted(bound)))
    mutated = set(_mutated_roots(caller))
    for name in ("validated", "parent", "delivery_document"):
        if name in mutated:
            problems.append("%s mutates %s" % (ATTEST_SEAM_FUNCTION, name))
    body = _body_without_docstring(caller)
    assignments = {}
    call_index = None
    for index, statement in enumerate(body):
        if isinstance(statement, ast.Assign) and len(statement.targets) == 1 and (
            isinstance(statement.targets[0], ast.Name)
        ):
            assignments[statement.targets[0].id] = (index, statement.value)
            if statement.targets[0].id == "outcome":
                call_index = index
    for name, text in (
        ("unbounded", "_preflight_problem(delivery_document, step)"),
        ("validated", "validated_receipt(delivery_document, step)"),
        ("parent", "parent_mission_authority(validated.record, mission_service)"),
    ):
        if name not in assignments or _dump(assignments[name][1]) != _expr(text):
            problems.append("%s is not bound exactly as %r" % (name, text))
    if call_index is None:
        problems.append("the Mission call is not the value of the outcome assignment")
        return problems
    order = [assignments.get(n, (None,))[0] for n in ("unbounded", "validated", "parent")]
    if None in order or order != sorted(order) or order[-1] > call_index:
        problems.append("preflight, validation, parent check and the Mission call are"
                        " out of order: %r" % (order,))
    # The guards before the Mission call are exactly these four returning
    # ifs, by AST equality of their tests, in this order, and nothing
    # else stands between them but the three pinned assignments.
    guards = [_expr("unbounded is not None"),
              _expr("step not in %s.STEPS" % validator),
              _expr("validated is None"), _expr('not parent["valid"]')]
    seen_guards = []
    for index, statement in enumerate(body[:call_index]):
        if isinstance(statement, ast.Assign) and index in (
            assignments.get("unbounded", (None,))[0],
            assignments.get("validated", (None,))[0],
            assignments.get("parent", (None,))[0],
        ):
            continue
        if isinstance(statement, ast.If) and statement.body and (
            isinstance(statement.body[-1], ast.Return) and not statement.orelse
        ):
            seen_guards.append(_dump(statement.test))
            continue
        problems.append("statement %d of %s before the Mission call is neither a"
                        " pinned assignment nor a returning if"
                        % (index + 1, ATTEST_SEAM_FUNCTION))
    if seen_guards != guards:
        problems.append("the guards before the Mission call are not exactly the"
                        " four pinned tests in order")
    after = body[call_index + 1:]
    if len(after) != 1 or not isinstance(after[0], ast.Return):
        problems.append("exactly one return must follow the Mission call")
    for statement in body:
        if isinstance(statement, ast.If):
            for test_node in ast.walk(statement.test):
                if isinstance(test_node, ast.Call) and not (
                    isinstance(test_node.func, ast.Name)
                    and test_node.func.id in ("isinstance",)
                ):
                    problems.append("an if test in %s calls %s"
                                    % (ATTEST_SEAM_FUNCTION, _dump(test_node.func)))
    # The Mission call itself.
    call = assignments["outcome"][1]
    if not (isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute)
            and call.func.attr == ATTEST_OPERATION
            and isinstance(call.func.value, ast.Name)
            and call.func.value.id == "mission_service"):
        problems.append("the outcome is not mission_service.%s(...)" % ATTEST_OPERATION)
        return problems
    if call.keywords or len(call.args) != 5:
        problems.append("the Mission call must pass exactly five positional arguments")
        return problems
    wanted = ['parent["mission_id"]', "operation_id", "expected_sequence", None, "context"]
    for index, text in enumerate(wanted):
        if text is not None and _dump(call.args[index]) != _expr(text):
            problems.append("Mission call argument %d is not %r" % (index + 1, text))
    attestation = call.args[3]
    if not isinstance(attestation, ast.Dict) or not all(
        isinstance(k, ast.Constant) and isinstance(k.value, str) for k in attestation.keys
    ):
        problems.append("the attestation argument is not a dict literal with string keys")
        return problems
    keys = [k.value for k in attestation.keys]
    if sorted(keys) != sorted(ATTESTATION_INPUT_FIELDS) or len(keys) != len(set(keys)):
        problems.append("the attestation keys are %r" % keys)
    for key, value in zip(keys, attestation.values):
        if _dump(value) != _expr("validated.%s" % key):
            problems.append("attestation[%r] is not read from validated.%s" % (key, key))
    # ``validated`` and ``parent`` are never aliased, passed or subscripted
    # anywhere but their pinned positions.
    allowed = {
        _expr("validated.record"): 1, _expr("validated is None"): 1,
        _expr('not parent["valid"]'): 1,
    }
    for node in ast.walk(caller):
        if isinstance(node, ast.Call):
            for argument in list(node.args) + [k.value for k in node.keywords]:
                if isinstance(argument, ast.Name) and argument.id in ("validated", "parent"):
                    problems.append("%s passes %s bare" % (ATTEST_SEAM_FUNCTION,
                                                           argument.id))
        if isinstance(node, ast.Subscript) and isinstance(node.value, ast.Name) and (
            node.value.id == "validated"
        ):
            problems.append("validated is subscripted")
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and (
            node.value.id == "parent"
        ):
            problems.append("parent is read by attribute")
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and (
            node.value.id == "validated" and node.attr not in VALIDATED_RECEIPT_FIELDS
        ):
            problems.append("validated.%s is not a field" % node.attr)
    return problems


def product_sources(root):
    """Every non-test Python file under ``root``, DISCOVERED on disk by a
    recursive walk: nothing is enumerated, nothing but ``tests/``,
    ``__pycache__`` and dot-directories is excluded, so ``herdr/nested/x.py``,
    ``scripts/x.py`` or a new package is inside the scan the moment it
    exists."""
    excluded = {"tests", "__pycache__"}
    sources = {}
    for path in sorted(root.rglob("*.py")):
        parts = path.relative_to(root).parts
        if any(part in excluded or part.startswith(".") for part in parts):
            continue
        sources[path.relative_to(root).as_posix()] = path.read_text()
    return sources


def _product_sources():
    return product_sources(REPO_ROOT)


class FReceiptAttestationConfinementTests(unittest.TestCase):
    """Condition 4: the attesting operation is confined to its definition
    and its single validating caller, and what that caller records is
    bound by data flow to what it validated — proven on the tree
    DISCOVERED on disk, with planted probes for every class of evasion
    the Reviewer found (round 07): decoded rather than spelled
    references, nested and new files discovered on disk, every binding
    form, and substitution between validation and recording."""

    def test_F6_the_real_tree_is_confined_and_discovery_is_not_vacuous(self):
        sources = _product_sources()
        self.assertGreater(len(sources), 30)
        for required in (ATTEST_SERVICE_FILE, ATTEST_SEAM_FILE, ATTEST_KIND_FILE,
                         "pr_delivery/machine.py", "grok_mcp/mission_tools.py",
                         "herdr/guards.py", "herdctl.py", "dirun.py"):
            self.assertIn(required, sources)
        # Discovery is a recursive walk of the tree on disk: it holds every
        # non-test .py an independent walk finds, and is a superset of the
        # bound-pin derivation plus herdr recursively.
        independent = set()
        for directory, names, files in os.walk(REPO_ROOT):
            relative = Path(directory).relative_to(REPO_ROOT)
            names[:] = [n for n in names if n not in ("tests", "__pycache__")
                        and not n.startswith(".")]
            for name in files:
                if name.endswith(".py"):
                    independent.add((relative / name).as_posix())
        self.assertEqual(set(sources), independent)
        from test_workflow_authority import derive_product_python_files
        derived = {p.relative_to(REPO_ROOT).as_posix()
                   for p in derive_product_python_files(REPO_ROOT)}
        derived |= {p.relative_to(REPO_ROOT).as_posix()
                    for p in (REPO_ROOT / "herdr").rglob("*.py")
                    if "__pycache__" not in p.parts}
        self.assertTrue(derived <= set(sources), sorted(derived - set(sources)))
        self.assertEqual(attestation_confinement_problems(sources), [])
        counts = _reference_counts(sources, ATTEST_OPERATION)
        self.assertEqual({k: v for k, v in counts.items() if v},
                         {ATTEST_SERVICE_FILE: 1, ATTEST_SEAM_FILE: 1,
                          ATTEST_KIND_FILE: 1})
        importers = set()
        for relpath, source in sources.items():
            if relpath.startswith("mission/"):
                continue
            for node in ast.walk(ast.parse(source)):
                names = []
                if isinstance(node, ast.Import):
                    names = [a.name for a in node.names]
                elif isinstance(node, ast.ImportFrom):
                    names = [node.module or ""]
                if any(n.split(".")[0] == "mission" for n in names):
                    importers.add(relpath)
        self.assertEqual({r for r in importers if r.startswith("pr_delivery/")},
                         {ATTEST_SEAM_FILE})

    def test_F6_discovery_finds_files_planted_on_disk(self):
        # The scan itself is the thing under test: the product tree is
        # mirrored into a temporary directory, files are planted ON DISK
        # in a nested herdr package, a nested target_runtime package, a
        # new top-level package and scripts/, and the discovery must find
        # each and the pin must fail on it. Nothing is inserted into the
        # source map by hand.
        with tempfile.TemporaryDirectory() as temp:
            mirror = Path(temp) / "tree"
            for relpath, source in _product_sources().items():
                target = mirror / relpath
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(source)
            self.assertEqual(product_sources(mirror), _product_sources())
            self.assertEqual(attestation_confinement_problems(product_sources(mirror)), [])
            planted = {
                "herdr/nested/new.py": "def run(svc):\n    return svc.attest_delivery_receipt()\n",
                "target_runtime/nested/deeper/new.py":
                    "fn = getattr(svc, '\\x61ttest_delivery_receipt')\n",
                "brand_new_package/__init__.py": "NAME = b'attest_delivery_receipt'\n",
                "scripts/tool.py": "import mission.state_service as s\n"
                                   "s.MissionService.attest_delivery_receipt\n",
            }
            for relpath, text in planted.items():
                with self.subTest(relpath):
                    target = mirror / relpath
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_text(text)
                    discovered = product_sources(mirror)
                    self.assertIn(relpath, discovered)
                    self.assertEqual(discovered[relpath], text)
                    problems = attestation_confinement_problems(discovered)
                    self.assertTrue(any(relpath in p for p in problems), problems)
                    target.unlink()
            # A planted file under a dot-directory or tests/ is excluded by
            # design and stated so; a __pycache__ file likewise.
            for relpath in (".hidden/x.py", "tests/x.py", "herdr/__pycache__/x.py"):
                target = mirror / relpath
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text("svc.attest_delivery_receipt()\n")
                self.assertNotIn(relpath, product_sources(mirror))

    def test_F6_planted_probes_fail_the_pin(self):
        good = _product_sources()
        seam = good[ATTEST_SEAM_FILE]

        def doctored(**changes):
            sources = dict(good)
            sources.update(changes)
            return attestation_confinement_problems(sources)

        def doctored_seam(old, new, count=1):
            self.assertEqual(seam.count(old), count, old)
            mutant = seam.replace(old, new)
            self.assertNotEqual(mutant, seam)
            ast.parse(mutant)
            return doctored(**{ATTEST_SEAM_FILE: mutant})

        # (a) a second caller anywhere: direct, alias, getattr, a bare
        # string, the Reviewer's ESCAPED literal, a bytes literal, an
        # f-string part, a keyword name and an import alias.
        for label, text in (
            ("direct call", "\nresult = svc.attest_delivery_receipt(1, 2, 3, {}, c)\n"),
            ("alias", "\nfn = svc.attest_delivery_receipt\n"),
            ("getattr", "\nfn = getattr(svc, 'attest_delivery_receipt')\n"),
            ("string lookup", "\nname = 'attest_delivery_receipt'\n"),
            ("escaped literal", "\nfn = getattr(svc, \"\\x61ttest_delivery_receipt\")\n"),
            ("escaped octal", "\nfn = getattr(svc, \"\\141ttest_delivery_receipt\")\n"),
            ("bytes literal", "\nname = b'attest_delivery_receipt'\n"),
            ("f-string part", "\nname = f'x{1}attest_delivery_receipt'\n"),
            ("keyword name", "\nf(attest_delivery_receipt=1)\n"),
            ("import alias", "\nfrom mission.state_service import x as attest_delivery_receipt\n"),
        ):
            with self.subTest(label):
                for relpath in ("pr_delivery/machine.py", "grok_mcp/mission_tools.py",
                                "herdr/guards.py"):
                    self.assertTrue(doctored(**{relpath: good[relpath] + text}), label)
        self.assertTrue(doctored(**{
            "mission/service.py": good["mission/service.py"]
            + "\ndef attest_delivery_receipt(self):\n    pass\n"}))
        # (b) the validating function: every substitution, rebinding,
        # mutation and reordering between validation and return.
        for label, old, new in (
            ("substituted receipt after validation",
             "    return ValidatedReceipt(\n",
             "    receipt = dict(receipt, state='succeeded', receipt_digest_sha256='9' * 64)\n"
             "    return ValidatedReceipt(\n"),
            ("mutated receipt after validation",
             "    return ValidatedReceipt(\n",
             "    receipt['state'] = 'succeeded'\n    return ValidatedReceipt(\n"),
            ("mutated record after validation",
             "    return ValidatedReceipt(\n",
             "    record.update({})\n    return ValidatedReceipt(\n"),
            ("receipt rebound before validate_receipt",
             "    if receipt is None:\n        return None\n",
             "    if receipt is None:\n        return None\n    receipt = receipt\n"),
            ("record not a deep copy (shallow copy)",
             "    record = _plain_copy(delivery_document)\n",
             "    record = dict(delivery_document)\n"),
            ("record is the caller's object",
             "    record = _plain_copy(delivery_document)\n",
             "    record = delivery_document\n"),
            ("copy helper made lossy (re-encoding keys)",
             "        return dict((key, _plain_copy(item)) for key, item in dict.items(value))\n",
             "        return dict((key.encode('utf-16', 'surrogatepass').decode('utf-16'), _plain_copy(item)) for key, item in dict.items(value))\n"),
            ("validator run on the caller's object",
             "    delivery_authorization.validate_authorization(record)\n",
             "    delivery_authorization.validate_authorization(delivery_document)\n"),
            ("expected step from the receipt",
             '        receipt, step, record["delivery_id"], record["authority_digest_sha256"],\n',
             '        receipt, receipt["step"], record["delivery_id"], record["authority_digest_sha256"],\n'),
            ("expected delivery id from the receipt",
             '        receipt, step, record["delivery_id"], record["authority_digest_sha256"],\n',
             '        receipt, step, receipt["delivery_id"], record["authority_digest_sha256"],\n'),
            ("expected authority digest from the receipt",
             '        receipt, step, record["delivery_id"], record["authority_digest_sha256"],\n',
             '        receipt, step, record["delivery_id"], receipt["parent_authority_digest_sha256"],\n'),
            ("receipt state recorded from a literal",
             '        receipt_state=receipt["state"],\n',
             "        receipt_state='succeeded',\n"),
            ("receipt state recorded from the caller's document",
             '        receipt_state=receipt["state"],\n',
             '        receipt_state=delivery_document["steps"][step]["receipt"]["state"],\n'),
            ("digest recorded from another field",
             '        receipt_digest_sha256=receipt["receipt_digest_sha256"],\n',
             '        receipt_digest_sha256=receipt["parent_authority_digest_sha256"],\n'),
            ("authorization digest from the caller's document",
             '            else record["mission"]["mission_authorization_digest_sha256"]),\n',
             '            else delivery_document["mission"]["mission_authorization_digest_sha256"]),\n'),
            ("succeeded ignores the step state",
             '            and record["steps"][step]["state"] == delivery_authorization.STEP_SUCCEEDED),\n',
             "            and True),\n"),
            ("validate_receipt skipped",
             "    delivery_authorization.validate_receipt(\n",
             "    if False: delivery_authorization.validate_receipt(\n"),
            ("validate_authorization skipped",
             "    delivery_authorization.validate_authorization(record)\n",
             "    pass\n"),
            ("record passed to a helper",
             "    delivery_authorization.validate_authorization(record)\n",
             "    delivery_authorization.validate_authorization(record)\n    other(record)\n"),
            ("validators wrapped in try",
             "    delivery_authorization.validate_authorization(record)\n",
             "    try:\n        delivery_authorization.validate_authorization(record)\n"
             "    except Exception:\n        pass\n"),
        ):
            with self.subTest(label):
                self.assertTrue(doctored_seam(old, new), label)
        # (c) the frozen dataclass, its bases, keywords and construction
        # (round 08 findings 1 and 2, the Reviewer's exact shapes first).
        self.assertTrue(doctored_seam(
            "def validated_receipt(delivery_document, step):",
            "def _swap(fn):\n    def inner(document, step):\n"
            "        value = fn(document, step)\n        if value is None:\n"
            "            return None\n        fields = dict(value.__dict__)\n"
            "        fields['receipt_state'] = 'succeeded'\n"
            "        fields['receipt_digest_sha256'] = '9' * 64\n"
            "        return ValidatedReceipt(**fields)\n    return inner\n\n\n"
            "@_swap\ndef validated_receipt(delivery_document, step):"))
        self.assertTrue(doctored_seam(
            "@dataclass(frozen=True)\nclass ValidatedReceipt:",
            "class _Base:\n    def __post_init__(self):\n"
            "        object.__setattr__(self, 'receipt_state', 'succeeded')\n"
            "        object.__setattr__(self, 'receipt_digest_sha256', '9' * 64)\n\n\n"
            "@dataclass(frozen=True)\nclass ValidatedReceipt(_Base):"))
        self.assertTrue(doctored_seam(
            "@dataclass(frozen=True)\nclass ValidatedReceipt:",
            "class _Meta(type):\n    pass\n\n\n"
            "@dataclass(frozen=True)\nclass ValidatedReceipt(metaclass=_Meta):"))
        self.assertTrue(doctored_seam(
            "def attest_validated_receipt(delivery_document, step, mission_service,",
            "@staticmethod\ndef attest_validated_receipt(delivery_document, step, mission_service,"))
        self.assertTrue(doctored_seam(
            "def _plain_copy(value):", "@_swap\ndef _plain_copy(value):"))
        self.assertTrue(doctored_seam(
            "def _plain_copy(value):",
            "def _plain_copy(value):\n    return value\n\n\ndef _plain_copy_old(value):"))
        self.assertTrue(doctored(**{ATTEST_SEAM_FILE: seam
                                    + "\nValidatedReceipt.__post_init__ = lambda self: None\n"}))
        # Round 09 finding 1: the Reviewer's exact shape — a constant moved
        # to the END of the module, value preserved, whose INITIALIZER
        # wraps validated_receipt with no new definition — and every other
        # way a top-level assignment or a function's creation could
        # execute at import time.
        constant_line = 'PROBLEM_PARENT_ABSENT = "pr_delivery_mission_parent_absent"\n'
        self.assertEqual(seam.count(constant_line), 1)
        wrapping = (
            'PROBLEM_PARENT_ABSENT = (globals().__setitem__("validated_receipt", (lambda fn: '
            '(lambda document, step: (lambda value: value if value is None else '
            'ValidatedReceipt(**dict(value.__dict__, receipt_state="succeeded", '
            'receipt_digest_sha256="9" * 64)))(fn(document, step))))(validated_receipt)), '
            '"pr_delivery_mission_parent_absent")[1]\n')
        moved = seam.replace(constant_line, "") + "\n\n" + wrapping
        ast.parse(moved)
        self.assertTrue(doctored(**{ATTEST_SEAM_FILE: moved}))
        for label, new in (
            ("constant from a call", 'PROBLEM_PARENT_ABSENT = str("pr_delivery_mission_parent_absent")\n'),
            ("constant from a name", 'PROBLEM_PARENT_ABSENT = PROBLEM_RECEIPT_ABSENT\n'),
            ("constant from an f-string", 'PROBLEM_PARENT_ABSENT = f"pr_delivery_mission_parent_absent"\n'),
            ("constant with a walrus", 'PROBLEM_PARENT_ABSENT = (x := "pr_delivery_mission_parent_absent")\n'),
            ("constant from an attribute", 'PROBLEM_PARENT_ABSENT = mission_record.MISSION_ID_PREFIX\n'),
            ("tuple constant holding a call",
             'PROBLEM_PARENT_ABSENT = ("pr_delivery_mission_parent_absent", str())[0]\n'),
        ):
            with self.subTest(label):
                self.assertTrue(doctored_seam(constant_line, new), label)
        # Round 10: the same wrapper inside a REACHABLE helper the golden
        # set previously omitted — the Reviewer's exact relocation into
        # _preflight_problem — and into every other reachable helper; a new
        # helper reached from the path (not in the golden set); a helper
        # renamed away (golden entry no longer reachable).
        wrapper = (
            '    globals().__setitem__("validated_receipt", (lambda fn: (lambda document, step: '
            '(lambda value: value if value is None else ValidatedReceipt(**dict(value.__dict__, '
            'receipt_state="succeeded", receipt_digest_sha256="9" * 64)))(fn(document, step))))'
            '(globals()["validated_receipt"]))\n')
        for label, anchor in (
            ("preflight helper (Reviewer's exact relocation)",
             "def _preflight_problem(document, step):\n"),
            ("refusal projection helper",
             "def _attestation(valid, problem, detail, receipt_id=None, step=None,\n"
             "                 receipt_state=None, succeeded=False, delivery_id=None,\n"
             "                 mission_id=None, authorization_id=None, revision=None,\n"
             "                 outcome=None):\n"),
            ("parent check", "def parent_mission_authority(delivery_document, mission_service):\n"),
            ("projection helper", "def _projection(check_dict, workflow_id, delivery_target):\n"),
        ):
            with self.subTest(label):
                self.assertEqual(seam.count(anchor), 1, label)
                self.assertTrue(doctored(**{ATTEST_SEAM_FILE: seam.replace(
                    anchor, anchor + wrapper)}), label)
        self.assertTrue(doctored_seam(
            "def _preflight_problem(document, step):\n",
            "def _side(document):\n    return document\n\n\n"
            "def _preflight_problem(document, step):\n    _side(document)\n"))
        self.assertTrue(doctored(**{ATTEST_SEAM_FILE: seam.replace(
            "def _projection(check_dict, workflow_id, delivery_target):",
            "def _projection_(check_dict, workflow_id, delivery_target):")}))
        # A literal constant placed AFTER a definition fails on position.
        self.assertTrue(doctored(**{ATTEST_SEAM_FILE: seam.replace(constant_line, "")
                                    + "\n\n" + constant_line}))
        # A default value or an annotation that calls, on a non-golden
        # function, and a second class.
        self.assertTrue(doctored_seam(
            "def _attestation(valid, problem, detail, receipt_id=None, step=None,",
            "def _attestation(valid, problem, detail, receipt_id=globals().pop('x', None), step=None,"))
        self.assertTrue(doctored_seam(
            "def _preflight_problem(document, step):",
            "def _preflight_problem(document: str(), step):"))
        self.assertTrue(doctored_seam(
            "def _preflight_problem(document, step):",
            "def _preflight_problem(document, step) -> str():"))
        self.assertTrue(doctored_seam(
            "def _preflight_problem(document, step):",
            "class _Side:\n    pass\n\n\ndef _preflight_problem(document, step):"))
        self.assertTrue(doctored(**{ATTEST_SEAM_FILE: seam
                                    + "\nvalidated_receipt = _preflight_problem\n"}))
        self.assertTrue(doctored(**{ATTEST_SEAM_FILE: seam.replace(
            "from dataclasses import dataclass\n",
            "from dataclasses import dataclass\nimport json\n")}))
        self.assertTrue(doctored_seam(
            "    record = _plain_copy(delivery_document)\n",
            "    record = _plain_copy(delivery_document)\n    record = record\n"))
        self.assertTrue(doctored_seam("@dataclass(frozen=True)\nclass ValidatedReceipt:",
                                      "@dataclass\nclass ValidatedReceipt:"))
        self.assertTrue(doctored_seam("@dataclass(frozen=True)\nclass ValidatedReceipt:",
                                      "@dataclass(frozen=False)\nclass ValidatedReceipt:"))
        self.assertTrue(doctored_seam("    succeeded: bool\n    record: dict\n",
                                      "    succeeded: bool\n    record: dict\n\n"
                                      "    def __setattr__(self, k, v):\n"
                                      "        object.__setattr__(self, k, v)\n"))
        self.assertTrue(doctored_seam("    succeeded: bool\n    record: dict\n",
                                      "    succeeded: bool\n    record: dict\n    extra: int = 0\n"))
        self.assertTrue(doctored_seam(
            "        step_state=record[\"steps\"][step][\"state\"],\n",
            "        step_state=\"succeeded\",\n"))
        self.assertTrue(doctored_seam(
            '            "step_state": validated.step_state,\n',
            '            "step_state": "succeeded",\n'))
        # (d) the caller: every binding form that could rebind the
        # validator module or the validated names, plus mutation,
        # aliasing, reordering, nesting, a second call and swapped fields.
        anchor = "    validated = validated_receipt(delivery_document, step)\n"
        for label, new in (
            ("for-target rebinding of the validator (Reviewer's exact case)",
             "    for delivery_authorization in [mission_service]: pass\n" + anchor),
            ("with-as rebinding of the validator",
             "    with open('/dev/null') as delivery_authorization: pass\n" + anchor),
            ("except-as rebinding of the validator",
             "    try:\n        pass\n    except Exception as delivery_authorization:\n"
             "        pass\n" + anchor),
            ("comprehension rebinding of dataclass",
             "    _ = [dataclass for dataclass in [mission_service]]\n" + anchor),
            ("walrus rebinding of validated",
             anchor + "    if (validated := None) is None:\n        return None\n"),
            ("augmented assignment of validated",
             anchor + "    validated += 1\n"),
            ("annotated assignment of validated",
             anchor + "    validated: object = None\n"),
            ("global rebinding of validated",
             "    global validated\n" + anchor),
            ("nonlocal-style nested def capturing validated",
             anchor + "    def inner():\n        nonlocal validated\n        validated = None\n"),
            ("import-as rebinding of validated",
             anchor + "    import os as validated\n"),
            ("del of validated",
             anchor + "    del validated\n"),
            ("for-target rebinding of validated",
             anchor + "    for validated in [None]:\n        pass\n"),
            ("lambda parameter shadowing validated",
             anchor + "    _ = lambda validated: validated\n"),
            ("class definition named validated",
             anchor + "    class validated:\n        pass\n"),
            ("second binding of validated",
             anchor + anchor),
            ("mutation of validated.record",
             anchor + "    validated.record['steps'] = None\n"),
            ("method call on validated.record",
             anchor + "    validated.record.update({})\n"),
            ("mutation of the caller's document",
             anchor + "    delivery_document['x'] = 1\n"),
            ("validated passed bare",
             anchor + "    other(validated)\n"),
            ("validated aliased",
             anchor + "    alias = validated\n"),
            ("parent passed bare",
             anchor + "    other(parent)\n"),
            ("if test with a call",
             anchor + "    if other(validated):\n        return None\n"),
            ("if with an else branch",
             anchor + "    if validated is None:\n        return None\n    else:\n"
                      "        validated.record['x'] = 1\n"),
        ):
            with self.subTest(label):
                self.assertTrue(doctored_seam(anchor, new), label)
        self.assertTrue(doctored_seam(
            "    parent = parent_mission_authority(validated.record, mission_service)\n",
            "    parent = parent_mission_authority(delivery_document, mission_service)\n"))
        self.assertTrue(doctored_seam(
            '            "receipt_state": validated.receipt_state,\n',
            '            "receipt_state": validated.step,\n'))
        self.assertTrue(doctored_seam(
            '            "receipt_state": validated.receipt_state,\n',
            '            "receipt_state": "succeeded",\n'))
        self.assertTrue(doctored_seam(
            '            "receipt_digest_sha256": validated.receipt_digest_sha256,\n',
            '            "receipt_digest_sha256": delivery_document["steps"][step]["receipt"]["receipt_digest_sha256"],\n'))
        self.assertTrue(doctored_seam(
            '        parent["mission_id"], operation_id, expected_sequence, {\n',
            '        delivery_document["mission"]["workflow_id"], operation_id, expected_sequence, {\n'))
        self.assertTrue(doctored_seam(
            "    outcome = mission_service.attest_delivery_receipt(\n",
            "    if step:\n      outcome = mission_service.attest_delivery_receipt(\n"))
        self.assertTrue(doctored_seam(
            '    if not parent["valid"]:\n', '    if False and not parent["valid"]:\n'))
        self.assertTrue(doctored_seam(
            "    unbounded = _preflight_problem(delivery_document, step)\n",
            "    unbounded = None\n"))
        self.assertTrue(doctored(**{ATTEST_SEAM_FILE: seam + "\n\ndef again(svc):\n"
                                    "    return svc.attest_delivery_receipt()\n"}))
        # Reordering: the Mission call moved before the validation.
        lines = seam.split("\n")
        start = next(i for i, l in enumerate(lines)
                     if l.startswith("    outcome = mission_service."))
        end = next(i for i in range(start, len(lines)) if lines[i].endswith("}, context)"))
        block = lines[start:end + 1]
        del lines[start:end + 1]
        head = next(i for i, l in enumerate(lines)
                    if l.startswith("    validated = validated_receipt("))
        moved = "\n".join(lines[:head] + block + lines[head:])
        ast.parse(moved)
        self.assertTrue(doctored(**{ATTEST_SEAM_FILE: moved}))
        # And the real seam passes, so every probe discriminates.
        self.assertEqual(doctored(), [])


class FReceiptAttestationTests(ServiceFixture):
    """Conditions 1, 2, 5 and 7 at the consumer: validate first through the
    real, unchanged validator; a failure leaves no attestation and no
    partial operation; a success records evidence and no authority."""

    def setUp(self):
        super(FReceiptAttestationTests, self).setUp()
        from pr_delivery import authorization as delivery_authorization
        from pr_delivery import mission_parent
        from mission import state as mission_state
        from mission import state_service
        self.auth = delivery_authorization
        self.seam = mission_parent
        self.ms = mission_state
        self.mss = state_service
        from test_mission_state import contract
        self.contract = contract
        created = self.propose(proof_contract=contract())
        self.mission_id = created["mission_id"]
        self.approved = self.approve(self.mission_id, 1)
        self.digest = self.approved["authorization_digest_sha256"]
        self.clock.advance(1)
        self.service.activate_proof_contract(
            self.mission_id, self.service.mint_state_operation_id(self.context), 0,
            self.context)
        self.record = delivery_record(self.mission_id, self.digest, self.clock())

    def oid(self):
        return self.service.mint_state_operation_id(self.context)

    def seq(self):
        return self.service.get_state(self.mission_id)["sequence"]

    def attest(self, record, step, operation_id=None, expected_sequence=None,
               context=None):
        if expected_sequence is None:
            expected_sequence = self.seq()
        return self.seam.attest_validated_receipt(
            record, step, self.service, operation_id or self.oid(),
            expected_sequence, context or self.context)

    def state(self):
        return self.store.load()["mission_state"][self.mission_id]

    def attested(self):
        return [a for a in self.state()["artifacts"]
                if self.ms.receipt_attestation_of(a) is not None]

    def test_F7_validate_first_then_the_distinct_operation_records_evidence_only(self):
        record = with_receipt(self.record, self.auth.STEP_PR_CREATE,
                              self.auth.RECEIPT_SUCCEEDED, self.clock())
        receipt = record["steps"][self.auth.STEP_PR_CREATE]["receipt"]
        # The real validator accepts the record and the receipt.
        self.auth.validate_authorization(record)
        self.auth.validate_receipt(receipt, self.auth.STEP_PR_CREATE,
                                   record["delivery_id"],
                                   record["authority_digest_sha256"], "receipt")
        authority = json.dumps({"a": self.store.load()["authorizations"],
                                "l": self.store.load()["authority_ledger"]},
                               sort_keys=True)
        mission_before = self.store.load()["missions"][self.mission_id]
        before_sequence = self.seq()
        result = self.attest(record, self.auth.STEP_PR_CREATE)
        self.assertTrue(result["valid"], result)
        self.assertEqual(sorted(result), sorted(self.seam.ATTESTATION_KEYS))
        self.assertEqual(result["receipt_id"], receipt["receipt_id"])
        self.assertEqual(result["receipt_state"], self.auth.RECEIPT_SUCCEEDED)
        self.assertTrue(result["succeeded"])
        self.assertEqual(result["mission_id"], self.mission_id)
        self.assertEqual(result["authorization_id"],
                         self.approved["authorization_id"])
        self.assertEqual(result["revision"], 1)
        outcome = result["outcome"]
        self.assertFalse(outcome["idempotent"])
        self.assertEqual(outcome["sequence"], before_sequence + 1)
        self.assertEqual(sorted(outcome), sorted(
            self.ms.OUTCOME_COMMON_KEYS
            + self.ms.OUTCOME_KEYS_BY_KIND[self.ms.OPERATION_ATTEST_DELIVERY_RECEIPT]
            + ("idempotent",)))
        # The effect: exactly one attested artifact, bound field by field
        # to the receipt the validator accepted and to the Mission parent.
        artifacts = self.attested()
        self.assertEqual(len(artifacts), 1)
        artifact = artifacts[0]
        self.assertEqual(artifact["artifact_id"], outcome["artifact_id"])
        self.assertEqual(artifact["locator"], receipt["receipt_id"])
        self.assertEqual(artifact["locator_kind"],
                         self.ms.LOCATOR_KIND_DELIVERY_RECEIPT_REFERENCE)
        self.assertEqual(artifact["content_digest_sha256"],
                         receipt["receipt_digest_sha256"])
        self.assertEqual(artifact["content_digest_sha256"],
                         self.auth.receipt_digest(receipt))
        self.assertEqual(artifact["role"], mission_record.ARTIFACT_ROLE_VERIFICATION)
        self.assertIsNone(artifact["key"])
        self.assertTrue(artifact["available"])
        self.assertEqual(artifact["derived_from"], [])
        marker = artifact["receipt_attestation"]
        self.assertEqual(marker, {
            "delivery_id": record["delivery_id"],
            "step": self.auth.STEP_PR_CREATE,
            "receipt_state": self.auth.RECEIPT_SUCCEEDED,
            "step_state": self.auth.STEP_SUCCEEDED,
            "parent_authority_digest_sha256": record["authority_digest_sha256"],
            "authorization_id": self.approved["authorization_id"],
            "authorization_digest_sha256": self.digest,
        })
        self.assertEqual(marker["parent_authority_digest_sha256"],
                         receipt["parent_authority_digest_sha256"])
        ledger = self.state()["applied_operations"][-1]
        self.assertEqual(ledger["kind"], self.ms.OPERATION_ATTEST_DELIVERY_RECEIPT)
        self.assertEqual(ledger["operation_id"], outcome["operation_id"])
        self.assertEqual(self.state()["snapshot"]["position"], outcome["sequence"])
        # Evidence, not authority: the Mission's authority bytes, its
        # registry record, its progress, proof and closure are untouched,
        # and the read-only parent check is still read-only and still
        # answers the same.
        self.assertEqual(json.dumps({"a": self.store.load()["authorizations"],
                                     "l": self.store.load()["authority_ledger"]},
                                    sort_keys=True), authority)
        self.assertEqual(self.store.load()["missions"][self.mission_id],
                         mission_before)
        projection = self.service.get_state(self.mission_id)
        self.assertEqual(projection["progress"], self.ms.PROGRESS_IN_PROGRESS)
        self.assertIsNone(projection["record"]["closure"])
        self.assertEqual(projection["record"]["evidence"], [])
        self.assertEqual(projection["record"]["blockers"], [])
        self.assertFalse(projection["proof"]["satisfied"])
        self.assertFalse(projection["closure_eligibility"]["eligible"])
        before = self.read_bytes()
        self.assertTrue(self.seam.parent_mission_authority(record, self.service)["valid"])
        self.assertEqual(self.read_bytes(), before)
        # The record reloads through the validator with the attestation
        # intact and gains nothing.
        fresh = self.mission_service.MissionService(
            self.mission_store.MissionStore(self.directory), self.clock)
        self.assertEqual(fresh.get_state(self.mission_id)["record"], self.state())
        # The same request replayed with the same reserved id is
        # idempotent; a new id for the same receipt in the same state
        # refuses with nothing written (no duplicate effect).
        replay = self.attest(record, self.auth.STEP_PR_CREATE,
                             operation_id=outcome["operation_id"],
                             expected_sequence=before_sequence)
        self.assertTrue(replay["outcome"]["idempotent"])
        self.assertEqual(replay["outcome"]["artifact_id"], outcome["artifact_id"])
        duplicate_id = self.oid()
        bytes_before = self.read_bytes()
        with self.assertRaises(mission_record.MissionError) as ctx:
            self.attest(record, self.auth.STEP_PR_CREATE, operation_id=duplicate_id)
        self.assertEqual(ctx.exception.problem,
                         self.mss.PROBLEM_RECEIPT_ALREADY_ATTESTED)
        self.assertEqual(self.read_bytes(), bytes_before)
        self.assertEqual(len(self.attested()), 1)

    def test_F7_every_validation_failure_leaves_no_attestation_and_no_operation(self):
        record = with_receipt(self.record, self.auth.STEP_PUSH,
                              self.auth.RECEIPT_SUCCEEDED, self.clock())
        step = self.auth.STEP_PUSH
        def tampered(mutate):
            doctored = copy.deepcopy(record)
            mutate(doctored)
            return doctored

        def set_receipt(key, value):
            def mutate(document):
                document["steps"][step]["receipt"][key] = value
            return mutate

        def set_binding(key, value):
            def mutate(document):
                document["steps"][step]["receipt"]["binding"][key] = value
            return mutate

        other = delivery_record(self.mission_id, self.digest, self.clock(),
                                delivery_id="prd-" + "b" * 12)
        other_receipt = with_receipt(other, step, self.auth.RECEIPT_SUCCEEDED,
                                     self.clock())["steps"][step]["receipt"]
        wrong_step_receipt = with_receipt(
            self.record, self.auth.STEP_COMMIT, self.auth.RECEIPT_SUCCEEDED,
            self.clock())["steps"][self.auth.STEP_COMMIT]["receipt"]

        def swap_receipt(receipt):
            def mutate(document):
                document["steps"][step]["receipt"] = receipt
            return mutate

        def tamper_authority(document):
            document["pr_content"]["title"] = "changed after authorization"

        def wrong_authority_digest(document):
            document["steps"][step]["receipt"]["parent_authority_digest_sha256"] = (
                "9" * 64)

        def mission_block_gone(document):
            document["mission"] = None
            document["authority_digest_sha256"] = self.auth.authority_digest(document)
            document["steps"][step]["receipt"]["parent_authority_digest_sha256"] = (
                document["authority_digest_sha256"])
            receipt = document["steps"][step]["receipt"]
            receipt["receipt_digest_sha256"] = self.auth.receipt_digest(receipt)

        raising = {
            "tampered binding": tampered(set_binding("source_commit", "8" * 40)),
            "tampered receipt digest": tampered(set_receipt(
                "receipt_digest_sha256", "9" * 64)),
            "receipt of another delivery": tampered(swap_receipt(other_receipt)),
            "receipt of another step": tampered(swap_receipt(wrong_step_receipt)),
            "receipt bound to another authority": tampered(wrong_authority_digest),
            "authority half altered": tampered(tamper_authority),
            "unknown receipt state": tampered(set_receipt("state", "done")),
            "step state disagrees": tampered(
                lambda d: d["steps"].__setitem__(
                    step, dict(d["steps"][step], state=self.auth.STEP_EXECUTING))),
            "not a delivery record": {"delivery_id": record["delivery_id"]},
        }
        sequence = self.seq()
        for label, document in raising.items():
            with self.subTest(label):
                operation_id = self.oid()
                before = self.read_bytes()
                with self.assertRaises(self.auth.AuthorizationError):
                    self.attest(document, step, operation_id=operation_id)
                self.assertEqual(self.read_bytes(), before)
                self.assertEqual(self.seq(), sequence)
                self.assertEqual(self.attested(), [])
                self.assertIsNone(self.store.load()["reservations"][operation_id][
                    "consumed_by"])
        # Refusal projections: no receipt at the step, an unknown step, and
        # a Mission parent that does not validate (absent, wrong Mission,
        # unknown digest, revoked by EDIT). None reaches the Mission call.
        refusing = {
            "no receipt at step": (self.record, self.auth.STEP_COMMIT,
                                   self.seam.PROBLEM_RECEIPT_ABSENT),
            "unknown step": (record, "MERGE", self.seam.PROBLEM_STEP_UNKNOWN),
            "step not a string": (record, None, self.seam.PROBLEM_DOCUMENT_UNBOUNDED),
            "parent absent": (with_receipt(
                delivery_record(self.mission_id, self.digest, self.clock(),
                                mission=False), step, self.auth.RECEIPT_SUCCEEDED,
                self.clock()), step, self.seam.PROBLEM_PARENT_INVALID),
            "parent names another mission": (with_receipt(
                delivery_record(self.propose()["mission_id"], self.digest,
                                self.clock()), step, self.auth.RECEIPT_SUCCEEDED,
                self.clock()), step, self.seam.PROBLEM_PARENT_INVALID),
            "parent digest unknown": (with_receipt(
                delivery_record(self.mission_id, "f" * 64, self.clock()), step,
                self.auth.RECEIPT_SUCCEEDED, self.clock()), step,
                self.seam.PROBLEM_PARENT_INVALID),
        }
        for label, (document, at, problem) in refusing.items():
            with self.subTest(label):
                operation_id = self.oid()
                before = self.read_bytes()
                result = self.attest(document, at, operation_id=operation_id)
                self.assertFalse(result["valid"])
                self.assertEqual(result["problem"], problem)
                self.assertIsNone(result["outcome"])
                self.assertEqual(self.read_bytes(), before)
                self.assertEqual(self.seq(), sequence)
                self.assertIsNone(self.store.load()["reservations"][operation_id][
                    "consumed_by"])
        self.assertEqual(self.attested(), [])

    def test_F7_revision_or_authority_change_between_validation_and_application_refuses(self):
        record = with_receipt(self.record, self.auth.STEP_PR_CREATE,
                              self.auth.RECEIPT_SUCCEEDED, self.clock())
        # The caller validated against revision 1; an EDIT lands before the
        # write. The parent check refuses (revoked), so the projection
        # refuses; and the Mission-side operation itself, called with the
        # values the caller had validated, refuses inside the lock.
        parent = self.seam.parent_mission_authority(record, self.service)
        self.assertTrue(parent["valid"])
        attestation = {
            "receipt_id": record["steps"][self.auth.STEP_PR_CREATE]["receipt"][
                "receipt_id"],
            "receipt_digest_sha256": record["steps"][self.auth.STEP_PR_CREATE][
                "receipt"]["receipt_digest_sha256"],
            "delivery_id": record["delivery_id"], "step": self.auth.STEP_PR_CREATE,
            "receipt_state": self.auth.RECEIPT_SUCCEEDED,
            "step_state": self.auth.STEP_SUCCEEDED,
            "parent_authority_digest_sha256": record["authority_digest_sha256"],
            "authorization_digest_sha256": self.digest,
        }
        operation_id = self.oid()
        sequence = self.seq()
        self.edit(self.mission_id, 1, objective="changed",
                  proof_contract=self.contract())
        refused_id = self.oid()
        before = self.read_bytes()
        result = self.attest(record, self.auth.STEP_PR_CREATE, operation_id=refused_id)
        self.assertFalse(result["valid"])
        self.assertEqual(result["problem"], self.seam.PROBLEM_PARENT_INVALID)
        self.assertIn(self.mission_authorization.PROBLEM_REVOKED, result["detail"])
        with self.assertRaises(mission_record.MissionError) as ctx:
            self.service.attest_delivery_receipt(self.mission_id, operation_id,
                                                 sequence, attestation, self.context)
        # The contract is stale at the new revision: the shared _apply
        # discipline refuses before the attestation is even examined.
        self.assertEqual(ctx.exception.problem, self.mss.PROBLEM_CONTRACT_STALE)
        self.assertEqual(self.read_bytes(), before)
        self.assertEqual(self.attested(), [])
        # Re-approve and re-activate at revision 2: the OLD digest names a
        # revoked authorization and refuses with the attestation's own
        # code inside the lock; a stale expected_sequence refuses first.
        self.approve(self.mission_id, 2)
        self.service.activate_proof_contract(self.mission_id, self.oid(), self.seq(),
                                             self.context)
        first_id, second_id = self.oid(), self.oid()
        before = self.read_bytes()
        with self.assertRaises(mission_record.MissionError) as ctx:
            self.service.attest_delivery_receipt(self.mission_id, first_id,
                                                 self.seq(), attestation, self.context)
        self.assertEqual(ctx.exception.problem,
                         self.mss.PROBLEM_RECEIPT_ATTESTATION_AUTHORITY)
        self.assertEqual(self.read_bytes(), before)
        with self.assertRaises(mission_record.MissionError) as ctx:
            self.service.attest_delivery_receipt(self.mission_id, second_id,
                                                 self.seq() - 1, attestation,
                                                 self.context)
        self.assertEqual(ctx.exception.problem, self.mss.PROBLEM_STALE_SEQUENCE)
        self.assertEqual(self.read_bytes(), before)
        self.assertEqual(self.attested(), [])

    def test_F7_structural_validity_is_not_success(self):
        # Condition 5 at the consumer: a valid receipt in every non-success
        # state is attested as exactly that state, and ``succeeded`` is
        # False; the Mission side stores the verbatim state.
        cases = (
            (self.auth.RECEIPT_DERIVED, self.auth.STEP_COMMIT),
            (self.auth.RECEIPT_EXECUTING, self.auth.STEP_PUSH),
            (self.auth.RECEIPT_FAILED_RETRYABLE, self.auth.STEP_BASE_REFRESH),
            (self.auth.RECEIPT_VOID, self.auth.STEP_PR_CREATE),
        )
        for state, step in cases:
            with self.subTest(state):
                record = with_receipt(self.record, step, state, self.clock())
                result = self.attest(record, step)
                self.assertTrue(result["valid"], result)
                self.assertEqual(result["receipt_state"], state)
                self.assertFalse(result["succeeded"])
                marker = self.attested()[-1]["receipt_attestation"]
                self.assertEqual(marker["receipt_state"], state)
                self.assertEqual(marker["step"], step)
                self.assertFalse(self.ms.receipt_effect_completed(marker))
        self.assertEqual(len(self.attested()), len(cases))
        # The one succeeded state is a completed effect, and the constant
        # is the delivery layer's own.
        self.assertEqual(self.ms.RECEIPT_STATE_SUCCEEDED, self.auth.RECEIPT_SUCCEEDED)
        record = with_receipt(self.record, self.auth.STEP_COMMIT,
                              self.auth.RECEIPT_SUCCEEDED, self.clock())
        result = self.attest(record, self.auth.STEP_COMMIT)
        self.assertTrue(result["succeeded"])
        self.assertTrue(self.ms.receipt_effect_completed(
            self.attested()[-1]["receipt_attestation"]))
        # A receipt observed again in a LATER state is a new fact (same
        # reference and digest, different state); the same state repeats
        # nothing.
        executing = with_receipt(self.record, self.auth.STEP_PUSH,
                                 self.auth.RECEIPT_EXECUTING, self.clock())
        receipt = executing["steps"][self.auth.STEP_PUSH]["receipt"]
        succeeded = copy.deepcopy(executing)
        succeeded["steps"][self.auth.STEP_PUSH]["receipt"]["state"] = (
            self.auth.RECEIPT_SUCCEEDED)
        succeeded["steps"][self.auth.STEP_PUSH]["state"] = self.auth.STEP_SUCCEEDED
        succeeded["phase"] = executing["phase"]
        self.auth.validate_authorization(succeeded)
        first = self.attest(executing, self.auth.STEP_PUSH)
        self.assertFalse(first["succeeded"])
        count = len(self.attested())
        with self.assertRaises(mission_record.MissionError) as ctx:
            self.attest(executing, self.auth.STEP_PUSH)
        self.assertEqual(ctx.exception.problem, self.mss.PROBLEM_RECEIPT_ALREADY_ATTESTED)
        self.assertEqual(len(self.attested()), count)
        later = self.attest(succeeded, self.auth.STEP_PUSH)
        self.assertTrue(later["succeeded"])
        self.assertEqual(len(self.attested()), count + 1)
        self.assertEqual(self.attested()[-1]["locator"], receipt["receipt_id"])
        # Same reference, different content: a conflict, nothing written.
        conflicting = copy.deepcopy(succeeded)
        conflicting["steps"][self.auth.STEP_PUSH]["receipt"]["binding"][
            "source_commit"] = "8" * 40
        conflicting["steps"][self.auth.STEP_PUSH]["receipt"][
            "receipt_digest_sha256"] = self.auth.receipt_digest(
                conflicting["steps"][self.auth.STEP_PUSH]["receipt"])
        self.auth.validate_authorization(conflicting)
        conflict_id = self.oid()
        before = self.read_bytes()
        with self.assertRaises(mission_record.MissionError) as ctx:
            self.attest(conflicting, self.auth.STEP_PUSH, operation_id=conflict_id)
        self.assertEqual(ctx.exception.problem,
                         self.mss.PROBLEM_RECEIPT_ATTESTATION_CONFLICT)
        self.assertEqual(self.read_bytes(), before)


    def test_F7_recorded_values_are_the_validated_values_executably(self):
        # Finding 4 (round 07), executable, on the REAL path: the values
        # recorded are the values the unchanged validator accepted, even
        # when the caller's document is changed between validation and
        # recording. The only in-process hook that runs between the two
        # is the validator boundary itself, so the substitution is
        # performed there: the real validator runs (wraps), and its side
        # effect rewrites the CALLER's document to a succeeded receipt
        # with a different digest. The seam validated a frozen copy, so
        # what reaches the Mission store is what was validated.
        import unittest.mock as mock
        record = with_receipt(self.record, self.auth.STEP_PUSH,
                              self.auth.RECEIPT_EXECUTING, self.clock())
        step = self.auth.STEP_PUSH
        original_receipt = copy.deepcopy(record["steps"][step]["receipt"])
        seen = {}
        real_validate_receipt = self.auth.validate_receipt
        real_validate_authorization = self.auth.validate_authorization

        def spy_authorization(document, *args, **kwargs):
            seen["record"] = document
            seen["record_is_callers"] = document is record
            return real_validate_authorization(document, *args, **kwargs)

        def spy_receipt(receipt, *args, **kwargs):
            seen["receipt"] = copy.deepcopy(receipt)
            seen["receipt_is_callers"] = receipt is record["steps"][step]["receipt"]
            result = real_validate_receipt(receipt, *args, **kwargs)
            # The substitution: the caller's document changes now.
            record["steps"][step]["receipt"]["state"] = self.auth.RECEIPT_SUCCEEDED
            record["steps"][step]["receipt"]["receipt_digest_sha256"] = "9" * 64
            record["steps"][step]["state"] = self.auth.STEP_SUCCEEDED
            return result

        with mock.patch.object(self.auth, "validate_authorization",
                               side_effect=spy_authorization), \
                mock.patch.object(self.auth, "validate_receipt",
                                  side_effect=spy_receipt):
            result = self.attest(record, step)
        self.assertTrue(result["valid"], result)
        self.assertFalse(seen["record_is_callers"])
        self.assertFalse(seen["receipt_is_callers"])
        self.assertEqual(seen["receipt"], original_receipt)
        self.assertEqual(seen["record"]["steps"][step]["receipt"], original_receipt)
        marker = self.attested()[-1]
        self.assertEqual(marker["locator"], seen["receipt"]["receipt_id"])
        self.assertEqual(marker["content_digest_sha256"],
                         seen["receipt"]["receipt_digest_sha256"])
        self.assertEqual(marker["receipt_attestation"]["receipt_state"],
                         seen["receipt"]["state"])
        self.assertEqual(marker["receipt_attestation"]["receipt_state"],
                         self.auth.RECEIPT_EXECUTING)
        self.assertNotEqual(marker["content_digest_sha256"], "9" * 64)
        self.assertEqual(result["receipt_state"], self.auth.RECEIPT_EXECUTING)
        self.assertFalse(result["succeeded"])
        # The caller's document did change (the substitution happened);
        # nothing of it reached the record.
        self.assertEqual(record["steps"][step]["receipt"]["state"],
                         self.auth.RECEIPT_SUCCEEDED)
        # A validator that accepts and hands back a DIFFERENT receipt
        # object cannot change what is recorded either: the seam reads
        # the object it passed, never a return value.
        # The Reviewer's exact source mutant, EXECUTED on the real path:
        # the substituted receipt does reach the real recording boundary
        # when the seam is mutated, which is what the pin exists to stop,
        # and the pin fails that mutant.
        import types
        source = (REPO_ROOT / "pr_delivery" / "mission_parent.py").read_text()
        mutant = source.replace(
            "    return ValidatedReceipt(\n",
            "    receipt = dict(receipt, state='succeeded', receipt_digest_sha256='9' * 64)\n"
            "    return ValidatedReceipt(\n")
        self.assertNotEqual(mutant, source)
        self.assertTrue(attestation_confinement_problems(
            dict(_product_sources(), **{ATTEST_SEAM_FILE: mutant})))
        module = types.ModuleType("mutant_seam")
        exec(compile(mutant, "<mutant seam>", "exec"), module.__dict__)
        executing = with_receipt(self.record, self.auth.STEP_COMMIT,
                                 self.auth.RECEIPT_EXECUTING, self.clock())
        real_receipt = executing["steps"][self.auth.STEP_COMMIT]["receipt"]
        recorded = module.attest_validated_receipt(
            executing, self.auth.STEP_COMMIT, self.service, self.oid(), self.seq(),
            self.context)
        substituted = self.attested()[-1]
        self.assertEqual(substituted["receipt_attestation"]["receipt_state"], "succeeded")
        self.assertEqual(substituted["content_digest_sha256"], "9" * 64)
        self.assertNotEqual(substituted["content_digest_sha256"],
                            real_receipt["receipt_digest_sha256"])
        self.assertEqual(recorded["receipt_state"], "succeeded")
        # The genuine seam over the same receipt records the validated
        # state and digest.
        genuine = with_receipt(self.record, self.auth.STEP_PR_CREATE,
                               self.auth.RECEIPT_EXECUTING, self.clock())
        self.attest(genuine, self.auth.STEP_PR_CREATE)
        marker = self.attested()[-1]
        self.assertEqual(marker["receipt_attestation"]["receipt_state"],
                         self.auth.RECEIPT_EXECUTING)
        self.assertEqual(marker["content_digest_sha256"],
                         genuine["steps"][self.auth.STEP_PR_CREATE]["receipt"][
                             "receipt_digest_sha256"])

    def test_F7_the_seam_bounds_the_document_before_any_validator_runs(self):
        # Finding 5 (round 07): every new entry point bounds its input
        # before work. The seam's preflight refuses by size and type,
        # with a short constant message, before the unchanged validator
        # (which would otherwise format the input) is even called.
        import unittest.mock as mock
        record = with_receipt(self.record, self.auth.STEP_PUSH,
                              self.auth.RECEIPT_SUCCEEDED, self.clock())
        step = self.auth.STEP_PUSH

        class Sub(dict):
            pass

        def deep(levels):
            value = {"x": 1}
            for _ in range(levels):
                value = {"x": value}
            return value

        cases = {
            "million-character mode (Reviewer's case)": dict(record, mode="x" * 1_000_000),
            "string one over the bound": dict(
                record, mode="x" * (self.seam.MAX_DELIVERY_DOCUMENT_STR_CHARS + 1)),
            "too many items": dict(record, extra=[0] * self.seam.MAX_DELIVERY_DOCUMENT_ITEMS),
            "too deep": dict(record, extra=deep(self.seam.MAX_DELIVERY_DOCUMENT_DEPTH)),
            "key too long": dict(record, **{"k" * 129: 1}),
            "non-string key": dict(record, **{}) | {7: 1},
            "dict subclass": Sub(record),
            "tuple inside": dict(record, extra=(1, 2)),
            "object inside": dict(record, extra=object()),
            "huge int": dict(record, extra=1 << 70),
            "not an object": [record],
            "None": None,
        }
        sequence = self.seq()
        for label, document in cases.items():
            with self.subTest(label):
                operation_id = self.oid()
                before = self.read_bytes()
                with mock.patch.object(self.auth, "validate_authorization",
                                       wraps=self.auth.validate_authorization) as spy:
                    result = self.attest(document, step, operation_id=operation_id)
                self.assertEqual(spy.call_count, 0)
                self.assertFalse(result["valid"])
                self.assertEqual(result["problem"], self.seam.PROBLEM_DOCUMENT_UNBOUNDED)
                self.assertLess(len(result["detail"]), 200)
                self.assertIsNone(result["outcome"])
                self.assertEqual(self.read_bytes(), before)
                self.assertEqual(self.seq(), sequence)
        for label, bad_step in (("non-string step", None), ("int step", 7),
                                ("step over the bound", "S" * 129)):
            with self.subTest(label):
                with mock.patch.object(self.auth, "validate_authorization",
                                       wraps=self.auth.validate_authorization) as spy:
                    result = self.attest(record, bad_step)
                self.assertEqual(spy.call_count, 0)
                self.assertEqual(result["problem"], self.seam.PROBLEM_DOCUMENT_UNBOUNDED)
        # Round 08 finding 3, ordering: the remaining budget is charged for
        # a container BEFORE any child is enqueued, so an oversized
        # container costs one length check and no allocation proportional
        # to its size. Measured, not inferred: a list ten times the item
        # bound refuses with far less memory traced during the preflight
        # than one pointer per element would take (the Stage 2h walker
        # allocated tens of megabytes here).
        import tracemalloc
        huge = dict(record, extra=list(range(self.seam.MAX_DELIVERY_DOCUMENT_ITEMS * 10)))
        tracemalloc.start()
        baseline = tracemalloc.get_traced_memory()[0]
        problem = self.seam._preflight_problem(huge, step)
        peak = tracemalloc.get_traced_memory()[1] - baseline
        tracemalloc.stop()
        self.assertIsNotNone(problem)
        self.assertLess(peak, 64 * 1024)
        self.assertGreater(len(huge["extra"]) * 8, 64 * 1024 * 50)
        # A GENUINE maximum-size record under the delivery contract's own
        # limits (MAX_CANDIDATE_ENTRIES entries, MAX_HUMAN_TEXT_CHARS texts,
        # MAX_REVERIFICATION_ARGV argv strings at their bound) passes the
        # preflight, the unchanged validator, and the whole attestation
        # path; the same record with one more string over the bound or
        # one more item over the budget is refused before validation.
        maximal = with_receipt(
            delivery_record(self.mission_id, self.digest, self.clock(), maximal=True),
            step, self.auth.RECEIPT_SUCCEEDED, self.clock())
        self.assertEqual(len(maximal["candidate"]["entries"]),
                         self.auth.MAX_CANDIDATE_ENTRIES)
        self.assertEqual(len(maximal["reverification"]["argv"]),
                         self.auth.MAX_REVERIFICATION_ARGV)
        self.assertIsNone(self.seam._preflight_problem(maximal, step))
        self.auth.validate_authorization(maximal)
        counted = [0]

        def count(value):
            counted[0] += 1
            if type(value) is dict:
                for item in dict.values(value):
                    count(item)
            elif type(value) is list:
                for item in value:
                    count(item)

        count(maximal)
        self.assertGreater(counted[0], self.auth.MAX_CANDIDATE_ENTRIES * 5)
        self.assertLess(counted[0], self.seam.MAX_DELIVERY_DOCUMENT_ITEMS)
        result = self.attest(maximal, step)
        self.assertTrue(result["valid"], result["problem"])
        self.assertGreaterEqual(self.seam.MAX_DELIVERY_DOCUMENT_STR_CHARS,
                                self.auth.MAX_PR_BODY_CHARS)
        over_budget = dict(maximal, extra=[0] * (self.seam.MAX_DELIVERY_DOCUMENT_ITEMS
                                                  - counted[0] + 1))
        self.assertIsNotNone(self.seam._preflight_problem(over_budget, step))
        under_budget = dict(maximal, extra=[0] * (self.seam.MAX_DELIVERY_DOCUMENT_ITEMS
                                                   - counted[0] - 1))
        self.assertIsNone(self.seam._preflight_problem(under_budget, step))
        # The unbounded refusal formats nothing from the input: the
        # message is one of the module's constants.
        detail = self.attest(dict(record, mode="x" * 1_000_000), step)["detail"]
        self.assertNotIn("xxxx", detail)
        # Only the genuine maximal record was attested; every refusal above
        # left nothing.
        self.assertEqual(len(self.attested()), 1)


    def test_F7_reviewers_decorator_and_base_mutants_are_caught_and_would_substitute(self):
        # Round 08 findings 1 and 2, executable on the REAL path: the
        # Reviewer's decorator (no extra import) and the inherited
        # __post_init__ base, each executed as a fresh module against the
        # real service, DO record a substituted state and digest after
        # the real validators ran — and each is a different program under
        # the whole-program pin, so it fails.
        import types
        source = (REPO_ROOT / "pr_delivery" / "mission_parent.py").read_text()
        mutants = {
            "decorator": source.replace(
                "def validated_receipt(delivery_document, step):",
                "def _swap(fn):\n    def inner(document, step):\n"
                "        value = fn(document, step)\n        if value is None:\n"
                "            return None\n        fields = dict(value.__dict__)\n"
                "        fields['receipt_state'] = 'succeeded'\n"
                "        fields['receipt_digest_sha256'] = '9' * 64\n"
                "        return ValidatedReceipt(**fields)\n    return inner\n\n\n"
                "@_swap\ndef validated_receipt(delivery_document, step):"),
            "inherited __post_init__": source.replace(
                "@dataclass(frozen=True)\nclass ValidatedReceipt:",
                "class _Base:\n    def __post_init__(self):\n"
                "        object.__setattr__(self, 'receipt_state', 'succeeded')\n"
                "        object.__setattr__(self, 'receipt_digest_sha256', '9' * 64)\n\n\n"
                "@dataclass(frozen=True)\nclass ValidatedReceipt(_Base):"),
        }
        for label, mutant in mutants.items():
            with self.subTest(label):
                self.assertNotEqual(mutant, source)
                problems = attestation_confinement_problems(
                    dict(_product_sources(), **{ATTEST_SEAM_FILE: mutant}))
                self.assertTrue(problems)
                module = types.ModuleType("mutant_" + label.split()[0])
                exec(compile(mutant, "<mutant>", "exec"), module.__dict__)
                step = self.auth.STEP_PUSH if label == "decorator" else self.auth.STEP_COMMIT
                executing = with_receipt(self.record, step, self.auth.RECEIPT_EXECUTING,
                                         self.clock())
                real = executing["steps"][step]["receipt"]
                module.attest_validated_receipt(executing, step, self.service, self.oid(),
                                                self.seq(), self.context)
                substituted = self.attested()[-1]
                self.assertEqual(substituted["receipt_attestation"]["receipt_state"],
                                 "succeeded")
                self.assertEqual(substituted["content_digest_sha256"], "9" * 64)
                self.assertNotEqual(substituted["content_digest_sha256"],
                                    real["receipt_digest_sha256"])
        # The genuine program over the same receipts records the validated
        # values.
        genuine = with_receipt(self.record, self.auth.STEP_PR_CREATE,
                               self.auth.RECEIPT_EXECUTING, self.clock())
        self.attest(genuine, self.auth.STEP_PR_CREATE)
        marker = self.attested()[-1]
        self.assertEqual(marker["receipt_attestation"]["receipt_state"],
                         self.auth.RECEIPT_EXECUTING)
        self.assertEqual(marker["content_digest_sha256"],
                         genuine["steps"][self.auth.STEP_PR_CREATE]["receipt"][
                             "receipt_digest_sha256"])

    def test_F7_the_copy_is_faithful_and_lossy_input_is_refused_before_validation(self):
        # Round 08 finding 4, the Reviewer's EXACT keys: "\U0001f600" (the
        # scalar) and "\ud83d\ude00" (the surrogate-pair spelling) are two
        # distinct str keys; a JSON round-trip merges them and drops the
        # forbidden value, which is how the Stage 2h seam recorded a
        # document the real validator rejects.
        import unittest.mock as mock
        step = self.auth.STEP_PR_CREATE
        record = with_receipt(self.record, step, self.auth.RECEIPT_SUCCEEDED, self.clock())
        receipt = record["steps"][step]["receipt"]
        scalar, pair = "\U0001f600", "\ud83d\ude00"
        self.assertNotEqual(scalar, pair)
        receipt["observed"] = {scalar: {"forbidden": 1}, pair: "ok"}
        self.assertEqual(len(receipt["observed"]), 2)
        receipt["receipt_digest_sha256"] = self.auth.receipt_digest(receipt)
        with self.assertRaises(self.auth.AuthorizationError) as ctx:
            self.auth.validate_authorization(record)
        self.assertEqual(ctx.exception.problem, self.auth.PROBLEM_BAD_TYPE)
        # The JSON round-trip IS lossy on this document (the defect), and
        # the seam no longer uses it.
        import json
        collapsed = json.loads(json.dumps(record))
        self.assertEqual(len(collapsed["steps"][step]["receipt"]["observed"]), 1)
        self.assertNotIn("json", (REPO_ROOT / "pr_delivery" / "mission_parent.py").read_text())
        # The structural copy preserves both keys, both values and every
        # type, is a different object at every container, and the real
        # validator rejects the copy exactly as it rejects the original.
        copied = self.seam._plain_copy(record)
        self.assertEqual(copied, record)
        self.assertIsNot(copied, record)
        self.assertIsNot(copied["steps"][step]["receipt"], receipt)
        self.assertEqual(sorted(copied["steps"][step]["receipt"]["observed"]),
                         sorted([scalar, pair]))
        self.assertEqual(copied["steps"][step]["receipt"]["observed"][scalar],
                         {"forbidden": 1})

        def same_types(a, b):
            self.assertIs(type(a), type(b))
            if type(a) is dict:
                self.assertEqual(list(a), list(b))
                for key in a:
                    same_types(a[key], b[key])
            elif type(a) is list:
                self.assertEqual(len(a), len(b))
                for x, y in zip(a, b):
                    same_types(x, y)
            else:
                self.assertEqual(a, b)

        same_types(copied, record)
        with self.assertRaises(self.auth.AuthorizationError) as ctx:
            self.auth.validate_authorization(copied)
        self.assertEqual(ctx.exception.problem, self.auth.PROBLEM_BAD_TYPE)
        # Through the seam, the unchanged validator sees the faithful copy
        # and refuses it exactly as it refuses the original: the contract's
        # own error, no Mission call, nothing written. (The Stage 2h seam
        # returned valid: True on this document.)
        operation_id = self.oid()
        before = self.read_bytes()
        with mock.patch.object(self.auth, "validate_authorization",
                               wraps=self.auth.validate_authorization) as spy:
            with self.assertRaises(self.auth.AuthorizationError) as ctx:
                self.attest(record, step, operation_id=operation_id)
        self.assertEqual(spy.call_count, 1)
        self.assertEqual(ctx.exception.problem, self.auth.PROBLEM_BAD_TYPE)
        self.assertEqual(self.read_bytes(), before)
        self.assertEqual(self.attested(), [])
        # Lead finding 01: a string no encoding carries (a lone surrogate)
        # is NOT refused by the seam, because the unchanged store writes
        # and reads it back and the unchanged validator accepts it. The
        # structural copy carries it faithfully, the validator's verdict on
        # the copy equals its verdict on the original, and the whole
        # attestation path accepts it; nothing of it reaches the marker,
        # which holds only ids and closed-vocabulary states.
        lone = chr(0xD83D)
        with self.assertRaises(UnicodeEncodeError):
            lone.encode("utf-8")
        carried = with_receipt(self.record, step, self.auth.RECEIPT_SUCCEEDED, self.clock())
        carried_receipt = carried["steps"][step]["receipt"]
        carried_receipt["observed"] = {"note": lone, lone: "ok"}
        carried_receipt["receipt_digest_sha256"] = self.auth.receipt_digest(carried_receipt)
        self.auth.validate_authorization(carried)
        self.assertIsNone(self.seam._preflight_problem(carried, step))
        copied = self.seam._plain_copy(carried)
        same_types(copied, carried)
        self.assertEqual(copied["steps"][step]["receipt"]["observed"],
                         {"note": lone, lone: "ok"})
        self.assertIs(copied["steps"][step]["receipt"]["observed"]["note"], lone)

        def verdict(document):
            try:
                self.auth.validate_authorization(document)
                return "accepted"
            except self.auth.AuthorizationError as exc:
                return exc.problem

        self.assertEqual(verdict(copied), verdict(carried))
        self.assertEqual(verdict(copied), "accepted")
        self.assertEqual(verdict(self.seam._plain_copy(record)), verdict(record))
        result = self.attest(carried, step)
        self.assertTrue(result["valid"], result)
        marker = self.attested()[-1]
        self.assertEqual(marker["locator"], carried_receipt["receipt_id"])
        self.assertEqual(marker["content_digest_sha256"],
                         carried_receipt["receipt_digest_sha256"])
        for value in marker["receipt_attestation"].values():
            self.assertNotIn(lone, value)
        # The unchanged validator refuses a lone surrogate only where its
        # own grammar does (an id), identically on original and copy.
        with self.assertRaises(self.auth.AuthorizationError):
            self.auth._require_id(lone, "receipt_id")
        # Without the surrogate key the forbidden value alone is refused by
        # the unchanged validator through the seam, as before, with nothing
        # written: the copy carried the forbidden value to the validator.
        del receipt["observed"][pair]
        receipt["receipt_digest_sha256"] = self.auth.receipt_digest(receipt)
        operation_id = self.oid()
        before = self.read_bytes()
        with self.assertRaises(self.auth.AuthorizationError) as ctx:
            self.attest(record, step, operation_id=operation_id)
        self.assertEqual(ctx.exception.problem, self.auth.PROBLEM_BAD_TYPE)
        self.assertEqual(self.read_bytes(), before)
        # Faithfulness over the value kinds the preflight admits: floats
        # (including -0.0 and 1e308), booleans apart from integers, nested
        # empty containers, and keys that differ only by normalization.
        tricky = dict(self.record, extra={
            "f": [0.1, -0.0, 1e308, 1.0, 1, True, False, None, [], {}],
            "e\u0301": 1, "\u00e9": 2, "": 3,
        })
        copied = self.seam._plain_copy(tricky)
        same_types(copied, tricky)
        self.assertEqual(len(copied["extra"]), 4)
        self.assertIs(copied["extra"]["f"][5], True)
        self.assertIs(type(copied["extra"]["f"][4]), int)
        self.assertEqual(str(copied["extra"]["f"][1]), "-0.0")

    def test_F7_reviewers_moved_constant_mutant_is_caught_and_would_substitute(self):
        # Round 09 finding 1, executable on the REAL path: the constant
        # moved to the end of the module with a wrapping initializer (no
        # new definition, value preserved) DOES record a substituted state
        # and digest after both real validators ran — and it is a
        # different top level under the pin, so it fails.
        import types
        source = (REPO_ROOT / "pr_delivery" / "mission_parent.py").read_text()
        constant_line = 'PROBLEM_PARENT_ABSENT = "pr_delivery_mission_parent_absent"\n'
        self.assertEqual(source.count(constant_line), 1)
        wrapping = (
            'PROBLEM_PARENT_ABSENT = (globals().__setitem__("validated_receipt", (lambda fn: '
            '(lambda document, step: (lambda value: value if value is None else '
            'ValidatedReceipt(**dict(value.__dict__, receipt_state="succeeded", '
            'receipt_digest_sha256="9" * 64)))(fn(document, step))))(validated_receipt)), '
            '"pr_delivery_mission_parent_absent")[1]\n')
        mutant = source.replace(constant_line, "") + "\n\n" + wrapping
        problems = attestation_confinement_problems(
            dict(_product_sources(), **{ATTEST_SEAM_FILE: mutant}))
        self.assertTrue(problems)
        self.assertTrue(any("PROBLEM_PARENT_ABSENT" in p or "then definitions" in p
                            for p in problems), problems)
        # The four compared definitions are byte-identical in the mutant.
        golden = dict((n.name, _dump(_strip_docstrings(n)))
                      for n in ast.parse(SEAM_ATTESTATION_PROGRAM).body)
        for node in ast.parse(mutant).body:
            if isinstance(node, (ast.FunctionDef, ast.ClassDef)) and node.name in golden:
                self.assertEqual(_dump(_strip_docstrings(node)), golden[node.name])
        module = types.ModuleType("mutant_moved_constant")
        exec(compile(mutant, "<mutant>", "exec"), module.__dict__)
        self.assertEqual(module.PROBLEM_PARENT_ABSENT, self.seam.PROBLEM_PARENT_ABSENT)
        executing = with_receipt(self.record, self.auth.STEP_PUSH,
                                 self.auth.RECEIPT_EXECUTING, self.clock())
        real = executing["steps"][self.auth.STEP_PUSH]["receipt"]
        module.attest_validated_receipt(executing, self.auth.STEP_PUSH, self.service,
                                        self.oid(), self.seq(), self.context)
        substituted = self.attested()[-1]
        self.assertEqual(substituted["receipt_attestation"]["receipt_state"], "succeeded")
        self.assertEqual(substituted["content_digest_sha256"], "9" * 64)
        self.assertNotEqual(substituted["content_digest_sha256"],
                            real["receipt_digest_sha256"])

    def test_F7_reviewers_helper_relocation_mutant_is_caught_and_would_substitute(self):
        # Round 10, executable on the REAL store: the wrapper relocated
        # into _preflight_problem — a helper the old golden set omitted —
        # leaves the previously compared definitions byte-identical, runs
        # both real validators, and DOES persist "9"*64 and "succeeded"
        # for an executing receipt (reloaded through a fresh service from
        # disk). The derived-reachability pin fails it.
        import types
        source = (REPO_ROOT / "pr_delivery" / "mission_parent.py").read_text()
        anchor = "def _preflight_problem(document, step):\n"
        self.assertEqual(source.count(anchor), 1)
        wrapper = (
            '    globals().__setitem__("validated_receipt", (lambda fn: (lambda document, step: '
            '(lambda value: value if value is None else ValidatedReceipt(**dict(value.__dict__, '
            'receipt_state="succeeded", receipt_digest_sha256="9" * 64)))(fn(document, step))))'
            '(globals()["validated_receipt"]))\n')
        mutant = source.replace(anchor, anchor + wrapper)
        golden = dict((n.name, _dump(_strip_docstrings(n)))
                      for n in ast.parse(SEAM_ATTESTATION_PROGRAM).body)
        for node in ast.parse(mutant).body:
            if isinstance(node, (ast.FunctionDef, ast.ClassDef)) and node.name in (
                "_plain_copy", "ValidatedReceipt", "validated_receipt",
                "attest_validated_receipt"
            ):
                self.assertEqual(_dump(_strip_docstrings(node)), golden[node.name])
        problems = attestation_confinement_problems(
            dict(_product_sources(), **{ATTEST_SEAM_FILE: mutant}))
        self.assertTrue(any("_preflight_problem is not the reviewed program" in p
                            for p in problems), problems)
        self.assertIn("_preflight_problem",
                      _reachable_definitions(ast.parse(mutant), ATTEST_SEAM_FUNCTION))
        module = types.ModuleType("mutant_helper_relocation")
        exec(compile(mutant, "<mutant>", "exec"), module.__dict__)
        executing = with_receipt(self.record, self.auth.STEP_PUSH,
                                 self.auth.RECEIPT_EXECUTING, self.clock())
        real = executing["steps"][self.auth.STEP_PUSH]["receipt"]
        result = module.attest_validated_receipt(executing, self.auth.STEP_PUSH, self.service,
                                                 self.oid(), self.seq(), self.context)
        self.assertTrue(result["valid"])
        fresh = self.mission_service.MissionService(
            self.mission_store.MissionStore(self.directory), self.clock)
        substituted = self.ms.attested_artifacts(fresh.get_state(self.mission_id)["record"])[-1]
        self.assertEqual(substituted["receipt_attestation"]["receipt_state"], "succeeded")
        self.assertEqual(substituted["content_digest_sha256"], "9" * 64)
        self.assertNotEqual(substituted["content_digest_sha256"],
                            real["receipt_digest_sha256"])
        # The genuine program records the validated values; the derived set
        # today is every definition in the module, so nothing is outside it.
        genuine = with_receipt(self.record, self.auth.STEP_PR_CREATE,
                               self.auth.RECEIPT_EXECUTING, self.clock())
        self.attest(genuine, self.auth.STEP_PR_CREATE)
        marker = self.attested()[-1]
        self.assertEqual(marker["receipt_attestation"]["receipt_state"],
                         self.auth.RECEIPT_EXECUTING)
        tree = ast.parse(source)
        every = {n.name for n in tree.body if isinstance(n, (ast.FunctionDef, ast.ClassDef))}
        self.assertEqual(_reachable_definitions(tree, ATTEST_SEAM_FUNCTION), every)
        self.assertEqual(every, set(SEAM_MODULE_DEFINITIONS))

    def test_F7_same_receipt_step_transition_is_new_evidence_on_the_real_store(self):
        # Round 09 finding 2, on the REAL store on disk: the same receipt
        # (reference and digest) attested first under a PENDING step and
        # then under a SUCCEEDED step is two facts; the second is the
        # completion evidence and is recorded. A true duplicate (same
        # receipt, same receipt state, same step state) stays idempotent
        # with nothing written; a replay of the same reserved id returns
        # the recorded outcome; a stale sequence refuses; a fresh service
        # over the same directory sees both; observation and
        # reconciliation report exactly one completed effect; repeating
        # the observation is not an event.
        from mission import reconciliation as rc
        step = self.auth.STEP_COMMIT
        record = with_receipt(self.record, step, self.auth.RECEIPT_SUCCEEDED, self.clock())
        receipt = record["steps"][step]["receipt"]
        record["steps"][step]["state"] = self.auth.STEP_PENDING
        self.auth.validate_authorization(record)
        first = self.attest(record, step)
        self.assertTrue(first["valid"])
        self.assertFalse(first["succeeded"])
        first_artifact = first["outcome"]["artifact_id"]
        # A true duplicate of the pending observation: nothing written.
        duplicate_id = self.oid()
        before = self.read_bytes()
        with self.assertRaises(mission_record.MissionError) as ctx:
            self.attest(record, step, operation_id=duplicate_id)
        self.assertEqual(ctx.exception.problem, self.mss.PROBLEM_RECEIPT_ALREADY_ATTESTED)
        self.assertEqual(self.read_bytes(), before)
        self.assertIsNone(self.store.load()["reservations"][duplicate_id]["consumed_by"])
        # The step moves to succeeded: the SAME receipt, same digest.
        record["steps"][step]["state"] = self.auth.STEP_SUCCEEDED
        self.auth.validate_authorization(record)
        self.assertEqual(record["steps"][step]["receipt"]["receipt_digest_sha256"],
                         receipt["receipt_digest_sha256"])
        sequence = self.seq()
        second_id = self.oid()
        second = self.attest(record, step, operation_id=second_id, expected_sequence=sequence)
        self.assertTrue(second["valid"], second)
        self.assertTrue(second["succeeded"])
        self.assertNotEqual(second["outcome"]["artifact_id"], first_artifact)
        markers = [(a["artifact_id"], a["receipt_attestation"]) for a in self.attested()]
        self.assertEqual([m[0] for m in markers], [first_artifact, second["outcome"]["artifact_id"]])
        self.assertEqual([m[1]["step_state"] for m in markers],
                         [self.auth.STEP_PENDING, self.auth.STEP_SUCCEEDED])
        self.assertEqual([self.ms.receipt_effect_completed(m[1]) for m in markers],
                         [False, True])
        for _, marker in markers:
            self.assertEqual(marker["receipt_state"], self.auth.RECEIPT_SUCCEEDED)
        self.assertEqual([a["locator"] for a in self.attested()],
                         [receipt["receipt_id"]] * 2)
        self.assertEqual([a["content_digest_sha256"] for a in self.attested()],
                         [receipt["receipt_digest_sha256"]] * 2)
        # Replay of the second reserved id: the recorded outcome, no write.
        bytes_after = self.read_bytes()
        replay = self.attest(record, step, operation_id=second_id, expected_sequence=sequence)
        self.assertTrue(replay["outcome"]["idempotent"])
        self.assertEqual(replay["outcome"]["artifact_id"], second["outcome"]["artifact_id"])
        self.assertEqual(self.read_bytes(), bytes_after)
        # A true duplicate of the succeeded observation, and a stale
        # sequence: each refuses with nothing written and its reservation
        # unconsumed.
        for label, kwargs, problem in (
            ("duplicate succeeded", {}, self.mss.PROBLEM_RECEIPT_ALREADY_ATTESTED),
            ("stale sequence", {"expected_sequence": sequence},
             self.mss.PROBLEM_STALE_SEQUENCE),
        ):
            with self.subTest(label):
                operation_id = self.oid()
                before = self.read_bytes()
                with self.assertRaises(mission_record.MissionError) as ctx:
                    self.attest(record, step, operation_id=operation_id, **kwargs)
                self.assertEqual(ctx.exception.problem, problem)
                self.assertEqual(self.read_bytes(), before)
                self.assertIsNone(self.store.load()["reservations"][operation_id][
                    "consumed_by"])
        # Going BACK to the pending observation of the same receipt is a
        # duplicate of the first fact, not new evidence.
        record["steps"][step]["state"] = self.auth.STEP_PENDING
        self.auth.validate_authorization(record)
        with self.assertRaises(mission_record.MissionError) as ctx:
            self.attest(record, step)
        self.assertEqual(ctx.exception.problem, self.mss.PROBLEM_RECEIPT_ALREADY_ATTESTED)
        # Persistence: a fresh service over the same directory reloads
        # both attestations through the validator; observation and
        # reconciliation report exactly one completed effect; a second
        # identical reconciliation pass writes nothing.
        fresh = self.mission_service.MissionService(
            self.mission_store.MissionStore(self.directory), self.clock)
        reloaded = self.ms.attested_artifacts(fresh.get_state(self.mission_id)["record"])
        self.assertEqual([a["artifact_id"] for a in reloaded],
                         [first_artifact, second["outcome"]["artifact_id"]])
        head = fresh.get_journal(self.mission_id)["cursor"]
        value = fresh.observe(self.mission_id, {"cursor": head, "reports": {}})[
            "delivery_receipts"]["value"]
        self.assertEqual(value["effects_completed"], [second["outcome"]["artifact_id"]])
        self.assertEqual([a["effect_completed"] for a in value["attested"]], [False, True])
        outcome = fresh.reconcile(self.mission_id, self.oid(), self.seq(),
                                  {"cursor": head, "reports": {}}, self.context)
        found = sorted(f["detail"][-3:] for f in outcome["findings"]
                       if f["kind"] == rc.FINDING_DELIVERY_ATTESTED)
        self.assertEqual(found, [" no", "yes"])
        head = fresh.get_journal(self.mission_id)["cursor"]
        quiet_id = self.oid()
        before = self.read_bytes()
        again = fresh.reconcile(self.mission_id, quiet_id, self.seq(),
                                {"cursor": head, "reports": {}}, self.context)
        self.assertFalse(again["changed"])
        self.assertEqual(self.read_bytes(), before)

    def test_F7_completion_condition_is_the_seams_at_every_layer(self):
        # Round 08 finding 5, the Reviewer's exact document: the unchanged
        # validator accepts a succeeded receipt under a PENDING step. The
        # seam says succeeded: False; the marker, the pure completion
        # helper, the reconciliation finding and the observation must say
        # the same, never more.
        from mission import reconciliation as rc
        step = self.auth.STEP_BASE_REFRESH
        record = with_receipt(self.record, step, self.auth.RECEIPT_SUCCEEDED, self.clock())
        record["steps"][step]["state"] = self.auth.STEP_PENDING
        self.auth.validate_authorization(record)
        result = self.attest(record, step)
        self.assertTrue(result["valid"])
        self.assertEqual(result["receipt_state"], self.auth.RECEIPT_SUCCEEDED)
        self.assertFalse(result["succeeded"])
        marker = self.attested()[-1]["receipt_attestation"]
        self.assertEqual(marker["receipt_state"], self.auth.RECEIPT_SUCCEEDED)
        self.assertEqual(marker["step_state"], self.auth.STEP_PENDING)
        self.assertFalse(self.ms.receipt_effect_completed(marker))
        head = self.service.get_journal(self.mission_id)["cursor"]
        report = self.service.observe(self.mission_id, {"cursor": head, "reports": {}})
        value = report["delivery_receipts"]["value"]
        self.assertEqual(value["effects_completed"], [])
        self.assertEqual(value["attested"][-1]["effect_completed"], False)
        self.assertEqual(value["attested"][-1]["step_state"], self.auth.STEP_PENDING)
        outcome = self.service.reconcile(self.mission_id, self.oid(), self.seq(),
                                         {"cursor": head, "reports": {}}, self.context)
        found = [f for f in outcome["findings"] if f["kind"] == rc.FINDING_DELIVERY_ATTESTED]
        self.assertEqual(len(found), 1)
        self.assertIn("effect completed: no", found[0]["detail"])
        # Every combination of receipt state and step state the contract
        # accepts agrees with the seam; only succeeded under succeeded is
        # a completed effect, at the seam, the helper and the projection.
        combos = [
            (self.auth.RECEIPT_SUCCEEDED, self.auth.STEP_SUCCEEDED, True),
            (self.auth.RECEIPT_SUCCEEDED, self.auth.STEP_PENDING, False),
            (self.auth.RECEIPT_SUCCEEDED, self.auth.STEP_NOT_NEEDED, False),
            (self.auth.RECEIPT_DERIVED, self.auth.STEP_PENDING, False),
            (self.auth.RECEIPT_EXECUTING, self.auth.STEP_EXECUTING, False),
            (self.auth.RECEIPT_FAILED_RETRYABLE, self.auth.STEP_FAILED_RETRYABLE, False),
            (self.auth.RECEIPT_VOID, self.auth.STEP_PENDING, False),
        ]
        for receipt_state, step_state, expected in combos:
            with self.subTest((receipt_state, step_state)):
                document = with_receipt(self.record, self.auth.STEP_COMMIT,
                                        receipt_state, self.clock())
                document["steps"][self.auth.STEP_COMMIT]["state"] = step_state
                self.auth.validate_authorization(document)
                result = self.attest(document, self.auth.STEP_COMMIT)
                self.assertTrue(result["valid"], result)
                self.assertIs(result["succeeded"], expected)
                marker = self.attested()[-1]["receipt_attestation"]
                self.assertIs(self.ms.receipt_effect_completed(marker), expected)
        self.assertEqual(self.ms.STEP_STATE_SUCCEEDED, self.auth.STEP_SUCCEEDED)
        self.assertEqual(self.ms.RECEIPT_STATE_SUCCEEDED, self.auth.RECEIPT_SUCCEEDED)

    def test_F7_parent_seam_regressions_and_signature_are_unchanged(self):
        import inspect
        parameters = inspect.signature(self.seam.parent_mission_authority).parameters
        self.assertEqual(list(parameters), ["delivery_document", "mission_service"])
        self.assertEqual(
            list(inspect.signature(self.seam.attest_validated_receipt).parameters),
            ["delivery_document", "step", "mission_service", "operation_id",
             "expected_sequence", "context"])
        # No bypass parameter, principal, flag or target: the delivery
        # target is still fixed inside the read-only seam.
        source = (REPO_ROOT / "pr_delivery" / "mission_parent.py").read_text()
        for word in ("bypass", "skip_valid", "trusted", "force", "override",
                     "sign", "mint", "issue_mission_authorization",
                     "apply_human_decision", "revoke", "DeliveryStore",
                     "pr_delivery.json", "subprocess"):
            self.assertNotIn(word, source, word)
        self.assertNotIn("git", source.replace("github_pr", ""))
        self.assertEqual(source.count("DELIVERY_TARGET_GITHUB_PR"), 1)
        # The two-key optional Mission parent contract is unchanged.
        self.assertIn('("workflow_id", "mission_authorization_digest_sha256")',
                      (REPO_ROOT / "pr_delivery" / "authorization.py").read_text())
        # The Mission-side operation accepts no widening parameter, and the
        # E4 / E5 truthfulness pins still hold over the seam.
        params = list(inspect.signature(
            self.service.attest_delivery_receipt).parameters)
        self.assertEqual(params, ["mission_id", "operation_id", "expected_sequence",
                                  "attestation", "context"])
        lowered = source.lower()
        for phrase in ("cryptographic proof", "cannot be forged",
                       "forging is impossible", "tamper-proof"):
            self.assertNotIn(phrase, lowered)
        self.assertIn("not cryptographic authenticity", lowered)
        self.assertIn("monkey-patching", lowered)


# ====================================================================
# R. Review round 1 findings (F1-F5): reproduction now refused, class closed
# ====================================================================


class RReconciliationFixture(ServiceFixture):
    """Build real history through the real service, tamper ONE thing,
    then assert refusal at BOTH entry points: the central validator over
    the tampered document, and the service over the tampered store."""

    def both_refuse(self, document, authorization_id, mission_id, revision,
                    direct_problem):
        direct = self.mission_authorization.validate_authorization_use(
            document, authorization_id, mission_id, revision, self.clock()
        )
        self.assertFalse(direct.valid, direct)
        self.assertEqual(direct.problem, direct_problem, direct.detail)
        self.write_raw(json.dumps(document))
        via = self.service.validate_authorization(authorization_id,
                                                  mission_id, revision)
        self.assertFalse(via.valid)
        self.assertIn(via.problem, (direct_problem,
                                    self.mission_authorization.PROBLEM_STORE_UNREADABLE))
        digest = document["authorizations"].get(authorization_id, {}).get(
            "authorization_digest_sha256"
        )
        if digest is not None:
            parent = self.service.check_parent_authority(mission_id, digest,
                                                         "github_pr")
            self.assertFalse(parent.valid)
        with self.assertRaises(self.mission_store.MissionStoreError):
            self.store.load()
        return direct

    def resign(self, authorization):
        authorization["authorization_digest_sha256"] = (
            self.mission_authorization.authorization_digest(authorization)
        )


class RF1AuthorizationAgreesWithDecisionTests(RReconciliationFixture):

    def setUp(self):
        super(RF1AuthorizationAgreesWithDecisionTests, self).setUp()
        self.mission_id = self.propose(
            requested_action_scope=[mission_record.ACTION_SCOPE_REPOSITORY_READ],
            requested_delivery_target=None,
        )["mission_id"]
        self.approved = self.approve(
            self.mission_id, 1, expires_at=self.clock() + 10,
            actions=[mission_record.ACTION_SCOPE_REPOSITORY_READ], targets=[],
        )
        self.authorization_id = self.approved["authorization_id"]
        self.good = json.loads(self.read_bytes())

    def tampered(self, **changes):
        document = json.loads(json.dumps(self.good))
        authorization = document["authorizations"][self.authorization_id]
        for key, value in changes.items():
            if key.startswith("principal."):
                authorization["human_principal"][key.split(".", 1)[1]] = value
            else:
                authorization[key] = value
        self.resign(authorization)
        return document

    def test_R1a_widened_action_scope_refuses(self):
        document = self.tampered(authorized_action_scope=[
            mission_record.ACTION_SCOPE_ENGINEERING_CHANGE,
            mission_record.ACTION_SCOPE_REPOSITORY_READ,
        ])
        self.both_refuse(document, self.authorization_id, self.mission_id, 1,
                         self.mission_authorization.PROBLEM_DECISION_MISMATCH)

    def test_R1b_added_delivery_target_refuses_and_parent_check_stays_false(self):
        document = self.tampered(authorized_delivery_targets=["github_pr"])
        self.both_refuse(document, self.authorization_id, self.mission_id, 1,
                         self.mission_authorization.PROBLEM_DECISION_MISMATCH)
        digest = document["authorizations"][self.authorization_id][
            "authorization_digest_sha256"]
        self.assertFalse(self.service.check_parent_authority(
            self.mission_id, digest, "github_pr"
        ).valid)

    def test_R1c_removed_expiry_refuses_even_after_the_deadline(self):
        document = self.tampered(expires_at=None)
        self.clock.advance(20)
        direct = self.both_refuse(
            document, self.authorization_id, self.mission_id, 1,
            self.mission_authorization.PROBLEM_DECISION_MISMATCH,
        )
        self.assertIn("expires_at", direct.detail)

    def test_R1d_changed_principal_or_issue_time_refuses(self):
        for changes in (
            {"principal.principal_ref": "2"},
            {"principal.configured_subject": "someone"},
            {"principal.received_at": self.clock() + 1},
            {"issued_at": self.clock() + 1},
        ):
            document = self.tampered(**changes)
            self.both_refuse(document, self.authorization_id, self.mission_id,
                             1, self.mission_authorization.PROBLEM_DECISION_MISMATCH)

    def test_R1f_rewriting_decision_and_authorization_together_still_refuses(self):
        # Even a consistent rewrite of BOTH records cannot approve more
        # than the revision requested; the request is the outer bound.
        document = json.loads(json.dumps(self.good))
        decision = document["missions"][self.mission_id]["decisions"][0]
        decision["approved_delivery_targets"] = ["github_pr"]
        decision["decision_digest_sha256"] = self.mission_decision.decision_digest(
            decision["mission_id"], decision["revision"], decision["decision"],
            decision["approved_action_scope"], ["github_pr"],
            decision["expires_at"], None,
        )
        authorization = document["authorizations"][self.authorization_id]
        authorization["authorized_delivery_targets"] = ["github_pr"]
        self.resign(authorization)
        self.both_refuse(document, self.authorization_id, self.mission_id, 1,
                         self.mission_authorization.PROBLEM_HISTORY_INCONSISTENT)

    def test_R1e_reservation_binding_disagreement_refuses(self):
        decision_id = self.service.get(self.mission_id)["record"]["decisions"][0][
            "decision_id"]
        document = json.loads(json.dumps(self.good))
        document["reservations"][decision_id]["context"]["principal_ref"] = "2"
        self.both_refuse(document, self.authorization_id, self.mission_id, 1,
                         self.mission_authorization.PROBLEM_HISTORY_INCONSISTENT)
        document = json.loads(json.dumps(self.good))
        request_id = document["missions"][self.mission_id]["request_id"]
        document["reservations"][request_id]["consumed_by"] = None
        self.both_refuse(document, self.authorization_id, self.mission_id, 1,
                         self.mission_authorization.PROBLEM_HISTORY_INCONSISTENT)


class RF2DenialHistoryTests(RReconciliationFixture):

    def test_R2a_flipping_denied_to_awaiting_cannot_reopen_approval(self):
        mission_id = self.propose()["mission_id"]
        self.deny(mission_id, 1)
        document = json.loads(self.read_bytes())
        document["missions"][mission_id]["state"] = "AWAITING_DECISION"
        direct = self.mission_authorization.reconcile_mission_history(
            document, document["missions"][mission_id]
        )
        self.assertIsNotNone(direct)
        self.assertEqual(direct[0],
                         self.mission_authorization.PROBLEM_HISTORY_INCONSISTENT)
        self.write_raw(json.dumps(document))
        with self.assertRaises(self.mission_store.MissionStoreError):
            self.approve(mission_id, 1)
        self.assertEqual(json.loads(self.read_bytes())["authorizations"], {})
        self.assertEqual(self.service.validate_authorization(
            "ma-" + "0" * 32, mission_id, 1
        ).problem, self.mission_authorization.PROBLEM_STORE_UNREADABLE)

    def test_R2b_deleting_the_historical_denied_event_refuses(self):
        mission_id = self.propose()["mission_id"]
        self.deny(mission_id, 1)
        self.edit(mission_id, 1, objective="after denial")
        approved = self.approve(mission_id, 2)
        document = json.loads(self.read_bytes())
        document["authority_ledger"] = [
            e for e in document["authority_ledger"] if e["kind"] != "DENIED"
        ]
        self.both_refuse(document, approved["authorization_id"], mission_id, 2,
                         self.mission_authorization.PROBLEM_LEDGER_INCONSISTENT)

    def test_R2c_deleting_or_reordering_a_decision_refuses(self):
        mission_id = self.propose()["mission_id"]
        self.deny(mission_id, 1)
        self.edit(mission_id, 1, objective="after denial")
        approved = self.approve(mission_id, 2)
        good = json.loads(self.read_bytes())
        # Drop the DENY decision from history (ledger still has it, and its
        # consumed reservation is now an orphan: the registry check sees it).
        document = json.loads(json.dumps(good))
        document["missions"][mission_id]["decisions"] = [
            d for d in document["missions"][mission_id]["decisions"]
            if d["decision"] != "DENY"
        ]
        self.both_refuse(document, approved["authorization_id"], mission_id, 2,
                         self.mission_authorization.PROBLEM_HISTORY_INCONSISTENT)
        # Reorder: approve before edit.
        document = json.loads(json.dumps(good))
        decisions = document["missions"][mission_id]["decisions"]
        decisions[1], decisions[2] = decisions[2], decisions[1]
        self.both_refuse(document, approved["authorization_id"], mission_id, 2,
                         self.mission_authorization.PROBLEM_HISTORY_INCONSISTENT)

    def test_R2d_state_that_contradicts_history_refuses_in_every_shape(self):
        mission_id = self.propose()["mission_id"]
        approved = self.approve(mission_id, 1)
        good = json.loads(self.read_bytes())
        for state in ("AWAITING_DECISION", "DENIED"):
            document = json.loads(json.dumps(good))
            document["missions"][mission_id]["state"] = state
            self.both_refuse(document, approved["authorization_id"], mission_id,
                             1, self.mission_authorization.PROBLEM_HISTORY_INCONSISTENT)
        # A revision the history never produced.
        document = json.loads(json.dumps(good))
        mission = document["missions"][mission_id]
        extra = json.loads(json.dumps(mission["revisions"][0]))
        extra["revision"] = 2
        extra["provenance"]["revision"] = 2
        extra["provenance"]["reference_kind"] = "decision"
        extra["provenance"]["reference_id"] = mission["decisions"][0]["decision_id"]
        mission["revisions"].append(extra)
        mission["current_revision"] = 2
        mission["state"] = "AWAITING_DECISION"
        self.both_refuse(document, approved["authorization_id"], mission_id, 1,
                         self.mission_authorization.PROBLEM_HISTORY_INCONSISTENT)

    def test_R2e_genuine_histories_still_reconcile(self):
        mission_id = self.propose()["mission_id"]
        self.deny(mission_id, 1)
        self.edit(mission_id, 1, objective="v2")
        approved = self.approve(mission_id, 2, expires_at=self.clock() + 5)
        self.edit(mission_id, 2, objective="v3")
        self.clock.advance(10)
        final = self.approve(mission_id, 3)
        document = self.store.load()
        self.assertIsNone(self.mission_authorization.reconcile_mission_history(
            document, document["missions"][mission_id]
        ))
        self.assertTrue(self.service.validate_authorization(
            final["authorization_id"], mission_id, 3
        ).valid)
        self.assertEqual(self.service.validate_authorization(
            approved["authorization_id"], mission_id, 2
        ).problem, self.mission_authorization.PROBLEM_REVOKED)


class RF3ProjectionsUseTheCentralCheckTests(ServiceFixture):

    def approve_envelope(self, mission_id, decision_id, expires_at=None):
        current = self.service.get(mission_id)["record"]["revisions"][-1]
        return self.mission_decision.HumanDecisionEnvelope(
            context=self.context, decision_id=decision_id,
            mission_id=mission_id, revision=1,
            decision=self.mission_decision.DECISION_APPROVE,
            received_at=self.clock(),
            approved_action_scope=current["proposal"]["requested_action_scope"],
            approved_delivery_targets=["github_pr"], expires_at=expires_at,
        )

    def test_R3a_contradictory_store_cannot_replay_as_live_authority(self):
        mission_id = self.propose()["mission_id"]
        decision_id = self.service.mint_decision_id(self.context)
        envelope = self.approve_envelope(mission_id, decision_id)
        self.service.apply_human_decision(envelope)
        document = json.loads(self.read_bytes())
        issued = dict(document["authority_ledger"][0])
        issued.update({"entry_id": "ml-" + "8" * 32, "kind": "REVOKED",
                       "reason": "tampered"})
        document["authority_ledger"].append(issued)
        self.write_raw(json.dumps(document))
        with self.assertRaises(self.mission_store.MissionStoreError):
            self.service.apply_human_decision(envelope)
        with self.assertRaises(self.mission_store.MissionStoreError):
            self.service.get(mission_id)
        self.assertEqual(self.read_bytes().decode(), json.dumps(document))

    def test_R3b_expired_authority_is_never_advertised_as_live(self):
        mission_id = self.propose()["mission_id"]
        decision_id = self.service.mint_decision_id(self.context)
        expires = self.clock() + 10
        first = self.service.apply_human_decision(
            self.approve_envelope(mission_id, decision_id, expires_at=expires)
        )
        self.assertTrue(first["authorization_live"])
        self.assertIsNone(first["authorization_problem"])
        self.assertEqual(self.service.get(mission_id)["live_authorization_id"],
                         first["authorization_id"])
        self.clock.advance(20)
        replay = self.service.apply_human_decision(
            self.approve_envelope(mission_id, decision_id, expires_at=expires)
        )
        self.assertTrue(replay["idempotent"])
        self.assertFalse(replay["authorization_live"])
        self.assertEqual(replay["authorization_problem"],
                         self.mission_authorization.PROBLEM_EXPIRED)
        self.assertIsNone(self.service.get(mission_id)["live_authorization_id"])
        self.assertNotIn("authorization_revoked", replay)
        self.assertNotIn("authorization_expired", replay)

    def test_R3c_replay_content_is_the_recorded_decision(self):
        mission_id = self.propose()["mission_id"]
        decision_id = self.service.mint_decision_id(self.context)
        envelope = self.approve_envelope(mission_id, decision_id,
                                         expires_at=self.clock() + 100)
        self.service.apply_human_decision(envelope)
        self.edit(mission_id, 1, objective="v2")
        replay = self.service.apply_human_decision(envelope)
        recorded = self.service.get(mission_id)["record"]["decisions"][0]
        self.assertEqual(replay["authorized_action_scope"],
                         recorded["approved_action_scope"])
        self.assertEqual(replay["authorized_delivery_targets"],
                         recorded["approved_delivery_targets"])
        self.assertEqual(replay["expires_at"], recorded["expires_at"])
        self.assertFalse(replay["authorization_live"])
        self.assertEqual(replay["authorization_problem"],
                         self.mission_authorization.PROBLEM_REVOKED)
        # No projection code path reads the mutable authorization fields.
        source = (REPO_ROOT / "mission" / "service.py").read_text()
        outcome_src = source[source.index("def _decision_outcome("):
                             source.index("def _approve(")]
        for forbidden in ('["revocation"]', 'authorization["',
                          '["authorized_action_scope"]',
                          '["authorized_delivery_targets"]'):
            self.assertNotIn(forbidden, outcome_src, forbidden)
        tools = (REPO_ROOT / "grok_mcp" / "mission_tools.py").read_text()
        self.assertNotIn('["revocation"]', tools)


class RF4DirectoryModeTests(StoreFixture):

    def make_store(self, mode):
        os.makedirs(self.directory, mode=0o700, exist_ok=True)
        self.store.save(self.mission_store.default_document())
        os.chmod(self.directory, mode)
        with open(self.store.path, "rb") as handle:
            return handle.read(), sorted(os.listdir(self.directory))

    def test_R4a_existing_open_directory_refuses_reads_locks_and_writes(self):
        for mode in (0o777, 0o770, 0o705, 0o750):
            self.tmp.cleanup()
            self.tmp = tempfile.TemporaryDirectory()
            self.directory = os.path.join(self.tmp.name, "protected")
            self.store = self.mission_store.MissionStore(self.directory)
            before, listing = self.make_store(mode)
            self.assertEqual(stat.S_IMODE(os.stat(self.store.path).st_mode),
                             0o600)
            with self.assertRaises(self.mission_store.MissionStoreError) as ctx:
                self.store.load()
            self.assertEqual(ctx.exception.problem,
                             self.mission_store.PROBLEM_STORE_UNREADABLE, oct(mode))
            self.assertIn("directory", str(ctx.exception))
            with self.assertRaises(self.mission_store.MissionStoreError):
                self.store.save(self.mission_store.default_document())
            with self.assertRaises(self.mission_store.MissionStoreError):
                with self.store.lock():
                    pass
            from mission import service as mission_service
            service = mission_service.MissionService(self.store, lambda: 5)
            with self.assertRaises(self.mission_store.MissionStoreError):
                service.mint_request_id(make_context())
            self.assertFalse(service.validate_authorization(
                "ma-" + "0" * 32, "mn-" + "0" * 32, 1
            ).valid)
            with open(self.store.path, "rb") as handle:
                self.assertEqual(handle.read(), before, oct(mode))
            self.assertEqual(sorted(os.listdir(self.directory)), listing)
            self.assertEqual(stat.S_IMODE(os.stat(self.directory).st_mode), mode)

    def test_R4b_protected_directory_works_and_a_missing_one_is_created_0700(self):
        before, listing = self.make_store(0o700)
        self.assertEqual(self.store.load(), self.mission_store.default_document())
        with self.store.lock():
            self.store.save(self.mission_store.default_document())
        fresh = self.mission_store.MissionStore(
            os.path.join(self.tmp.name, "fresh")
        )
        self.assertEqual(fresh.load(), self.mission_store.default_document())
        fresh.save(self.mission_store.default_document())
        self.assertEqual(stat.S_IMODE(os.stat(fresh.directory).st_mode), 0o700)
        self.assertEqual(stat.S_IMODE(os.stat(fresh.path).st_mode), 0o600)


class RF5ParentSeamRequiresGithubPrTests(ServiceFixture):

    def setUp(self):
        super(RF5ParentSeamRequiresGithubPrTests, self).setUp()
        from pr_delivery import mission_parent
        self.mission_parent = mission_parent
        self.plain = self.propose(requested_delivery_target=None)["mission_id"]
        self.approved = self.approve(self.plain, 1)
        self.block = {
            "workflow_id": self.plain,
            "mission_authorization_digest_sha256":
                self.approved["authorization_digest_sha256"],
        }

    def test_R5a_seam_accepts_no_target_argument(self):
        import inspect
        parameters = inspect.signature(
            self.mission_parent.parent_mission_authority
        ).parameters
        self.assertEqual(list(parameters), ["delivery_document",
                                            "mission_service"])
        with self.assertRaises(TypeError):
            self.mission_parent.parent_mission_authority(
                {"mission": self.block}, self.service, None
            )
        with self.assertRaises(TypeError):
            self.mission_parent.parent_mission_authority(
                {"mission": self.block}, self.service, delivery_target=None
            )
        projection = self.mission_parent.parent_mission_authority(
            {"mission": self.block}, self.service
        )
        self.assertFalse(projection["valid"])
        self.assertEqual(projection["problem"],
                         self.mission_authorization.PROBLEM_TARGET_OUTSIDE_SCOPE)
        self.assertEqual(projection["delivery_target"], "github_pr")

    def test_R5b_service_parent_check_refuses_absent_or_unknown_target(self):
        for target in (None, "", "npm", "GITHUB_PR", 7):
            check = self.service.check_parent_authority(
                self.plain, self.approved["authorization_digest_sha256"], target
            )
            self.assertFalse(check.valid, target)
            self.assertEqual(check.problem,
                             self.mission_authorization.PROBLEM_TARGET_OUTSIDE_SCOPE)
        # And with an authorization that DOES carry github_pr, only the
        # exact target validates.
        mission_id = self.propose()["mission_id"]
        approved = self.approve(mission_id, 1)
        self.assertTrue(self.service.check_parent_authority(
            mission_id, approved["authorization_digest_sha256"], "github_pr"
        ).valid)
        self.assertFalse(self.service.check_parent_authority(
            mission_id, approved["authorization_digest_sha256"], None
        ).valid)


# ====================================================================
# S. Review turn 2 findings: legitimate case and tampered case together
# ====================================================================


class TickingClock(object):
    """Every read advances: receipt and application never coincide."""

    def __init__(self, start=1_000_000):
        self.now = start

    def __call__(self):
        self.now += 1
        return self.now

    def advance(self, seconds):
        self.now += seconds
        return self.now


class SF1ReceiptVersusApplicationTimeTests(ServiceFixture):

    def setUp(self):
        super(SF1ReceiptVersusApplicationTimeTests, self).setUp()
        self.clock = TickingClock()
        self.service = self.mission_service.MissionService(self.store, self.clock)

    def test_S1a_edit_with_ticking_clock_is_accepted_and_reconciles(self):
        # The Reviewer's reproduction: receipt at one clock read, application
        # at the next. Every step is the honest path.
        mission_id = self.propose()["mission_id"]
        edited = self.edit(mission_id, 1, objective="v2")
        self.assertEqual(edited["revision"], 2)
        record_ = self.service.get(mission_id)["record"]
        decision_record = record_["decisions"][0]
        entry = record_["revisions"][1]
        self.assertLess(decision_record["received_at"], decision_record["decided_at"])
        self.assertEqual(entry["provenance"]["received_at"],
                         decision_record["received_at"])
        self.assertEqual(entry["created_at"], decision_record["decided_at"])
        approved = self.approve(mission_id, 2)
        self.assertTrue(self.service.validate_authorization(
            approved["authorization_id"], mission_id, 2
        ).valid)
        document = self.store.load()
        self.assertIsNone(self.mission_authorization.reconcile_mission_history(
            document, document["missions"][mission_id]
        ))

    def test_S1b_delayed_envelope_is_accepted(self):
        mission_id = self.propose()["mission_id"]
        decision_id = self.service.mint_decision_id(self.context)
        received = self.clock()
        self.clock.advance(3600)
        edited = self.service.apply_human_decision(
            self.mission_decision.HumanDecisionEnvelope(
                context=self.context, decision_id=decision_id,
                mission_id=mission_id, revision=1,
                decision=self.mission_decision.DECISION_EDIT,
                received_at=received, proposal=proposal(objective="delayed"),
            )
        )
        self.assertEqual(edited["revision"], 2)
        decision_id = self.service.mint_decision_id(self.context)
        received = self.clock()
        self.clock.advance(600)
        current = self.service.get(mission_id)["record"]["revisions"][-1]
        approved = self.service.apply_human_decision(
            self.mission_decision.HumanDecisionEnvelope(
                context=self.context, decision_id=decision_id,
                mission_id=mission_id, revision=2,
                decision=self.mission_decision.DECISION_APPROVE,
                received_at=received,
                approved_action_scope=current["proposal"]["requested_action_scope"],
                approved_delivery_targets=["github_pr"],
            )
        )
        self.assertTrue(approved["authorization_live"])
        stored = self.service.get(mission_id)
        principal = stored["authorizations"][0]["human_principal"]
        self.assertEqual(principal["received_at"], received)
        self.assertGreater(stored["authorizations"][0]["issued_at"], received)
        self.assertTrue(self.service.validate_authorization(
            approved["authorization_id"], mission_id, 2
        ).valid)

    def test_S1c_contradictory_redundant_receipt_timestamps_refuse(self):
        mission_id = self.propose()["mission_id"]
        self.edit(mission_id, 1, objective="v2")
        approved = self.approve(mission_id, 2)
        good = json.loads(self.read_bytes())
        auth = self.mission_authorization
        # (a) APPROVE decision's received_at changed; provenance untouched.
        document = json.loads(json.dumps(good))
        decision = document["missions"][mission_id]["decisions"][1]
        decision["received_at"] += 1
        direct = auth.validate_authorization_use(
            document, approved["authorization_id"], mission_id, 2, self.clock()
        )
        self.assertFalse(direct.valid)
        self.assertEqual(direct.problem, auth.PROBLEM_MALFORMED_STATE)
        self.write_raw(json.dumps(document))
        with self.assertRaises(self.mission_store.MissionStoreError):
            self.store.load()
        # (b) provenance received_at changed; decision untouched.
        document = json.loads(json.dumps(good))
        decision = document["missions"][mission_id]["decisions"][1]
        decision["provenance"]["received_at"] += 1
        direct = auth.validate_authorization_use(
            document, approved["authorization_id"], mission_id, 2, self.clock()
        )
        self.assertFalse(direct.valid)
        self.assertIn(direct.problem, (auth.PROBLEM_MALFORMED_STATE,
                                       auth.PROBLEM_DECISION_MISMATCH))
        # (c) revision provenance receipt time disagrees with the EDIT
        #     decision that produced the revision.
        document = json.loads(json.dumps(good))
        document["missions"][mission_id]["revisions"][1]["provenance"][
            "received_at"] += 1
        direct = auth.validate_authorization_use(
            document, approved["authorization_id"], mission_id, 2, self.clock()
        )
        self.assertFalse(direct.valid)
        self.assertEqual(direct.problem, auth.PROBLEM_HISTORY_INCONSISTENT)
        # (d) revision application time disagrees with the decision time.
        document = json.loads(json.dumps(good))
        document["missions"][mission_id]["revisions"][1]["created_at"] += 1
        direct = auth.validate_authorization_use(
            document, approved["authorization_id"], mission_id, 2, self.clock()
        )
        self.assertEqual(direct.problem, auth.PROBLEM_HISTORY_INCONSISTENT)
        for document_text in (json.dumps(good),):
            self.write_raw(document_text)
            self.assertIsNotNone(self.store.load())


class SF2RegistryWideIdentityTests(ServiceFixture):

    def setUp(self):
        super(SF2RegistryWideIdentityTests, self).setUp()
        self.auth = self.mission_authorization
        self.first = self.propose()["mission_id"]
        self.first_approved = self.approve(self.first, 1)
        self.second = self.propose()["mission_id"]
        self.second_approved = self.approve(self.second, 1)
        self.good = json.loads(self.read_bytes())
        # Two genuine Missions reconcile and both authorizations validate.
        for mission_id, approved in ((self.first, self.first_approved),
                                     (self.second, self.second_approved)):
            self.assertTrue(self.service.validate_authorization(
                approved["authorization_id"], mission_id, 1
            ).valid)

    def refuse_everywhere(self, document, problem):
        for mission_id, approved in ((self.first, self.first_approved),
                                     (self.second, self.second_approved)):
            direct = self.auth.validate_authorization_use(
                document, approved["authorization_id"], mission_id, 1,
                self.clock(),
            )
            self.assertFalse(direct.valid, direct)
            self.assertEqual(direct.problem, problem, direct.detail)
        self.write_raw(json.dumps(document))
        with self.assertRaises(self.mission_store.MissionStoreError):
            self.store.load()
        for mission_id, approved in ((self.first, self.first_approved),
                                     (self.second, self.second_approved)):
            self.assertFalse(self.service.validate_authorization(
                approved["authorization_id"], mission_id, 1
            ).valid)
            self.assertFalse(self.service.check_parent_authority(
                mission_id, approved["authorization_digest_sha256"], "github_pr"
            ).valid)

    def test_S2a_decision_id_reused_across_missions_refuses(self):
        document = json.loads(json.dumps(self.good))
        first_decision = document["missions"][self.first]["decisions"][0]
        second_decision = document["missions"][self.second]["decisions"][0]
        reused = first_decision["decision_id"]
        second_decision["decision_id"] = reused
        second_decision["provenance"]["reference_id"] = reused
        authorization = document["authorizations"][
            self.second_approved["authorization_id"]]
        authorization["human_principal"]["reference_id"] = reused
        authorization["authorization_digest_sha256"] = (
            self.auth.authorization_digest(authorization)
        )
        for entry in document["authority_ledger"]:
            if entry["mission_id"] == self.second:
                entry["decision_id"] = reused
        self.refuse_everywhere(document, self.auth.PROBLEM_HISTORY_INCONSISTENT)

    def test_S2b_orphan_consumed_reservation_refuses(self):
        document = json.loads(json.dumps(self.good))
        orphan = "md-" + "c" * 32
        document["reservations"][orphan] = {
            "reserved_at": self.clock(), "kind": "decision",
            "context": self.context.as_dict(), "consumed_by": orphan,
        }
        self.refuse_everywhere(document, self.auth.PROBLEM_HISTORY_INCONSISTENT)
        document = json.loads(json.dumps(self.good))
        orphan = "mq-" + "c" * 32
        document["reservations"][orphan] = {
            "reserved_at": self.clock(), "kind": "request",
            "context": self.context.as_dict(), "consumed_by": self.first,
        }
        self.refuse_everywhere(document, self.auth.PROBLEM_HISTORY_INCONSISTENT)

    def test_S2c_request_id_reused_across_missions_refuses(self):
        document = json.loads(json.dumps(self.good))
        first = document["missions"][self.first]
        second = document["missions"][self.second]
        second["request_id"] = first["request_id"]
        second["revisions"][0]["provenance"]["reference_id"] = first["request_id"]
        self.refuse_everywhere(document, self.auth.PROBLEM_HISTORY_INCONSISTENT)

    def test_S2d_unconsumed_reservations_and_new_missions_still_reconcile(self):
        self.service.mint_request_id(self.context)
        self.service.mint_decision_id(self.context)
        third = self.propose()["mission_id"]
        self.edit(third, 1, objective="v2")
        document = self.store.load()
        for mission_id in (self.first, self.second, third):
            self.assertIsNone(self.auth.reconcile_mission_history(
                document, document["missions"][mission_id]
            ))
        self.assertIsNone(self.auth.reconcile_registry(document))


class SF3ExpirationHistoryTests(ServiceFixture):

    def setUp(self):
        super(SF3ExpirationHistoryTests, self).setUp()
        self.auth = self.mission_authorization
        self.mission_id = self.propose()["mission_id"]
        self.first = self.approve(self.mission_id, 1, expires_at=self.clock() + 10)

    def test_S3a_absent_expired_event_before_the_next_mutation_is_legitimate(self):
        self.clock.advance(20)
        self.assertEqual(self.ledger_kinds(self.mission_id), ["ISSUED"])
        document = self.store.load()
        self.assertIsNone(self.auth.reconcile_mission_history(
            document, document["missions"][self.mission_id]
        ))
        self.assertEqual(self.service.validate_authorization(
            self.first["authorization_id"], self.mission_id, 1
        ).problem, self.auth.PROBLEM_EXPIRED)
        # An EXPIRED entry that no mutation recorded is a contradiction.
        document = json.loads(self.read_bytes())
        issued = document["authority_ledger"][0]
        document["authority_ledger"].append(dict(
            issued, entry_id="ml-" + "7" * 32, kind="EXPIRED",
            decision_id=None, reason="expires_at passed",
            recorded_at=self.clock(),
        ))
        direct = self.auth.validate_authorization_use(
            document, self.first["authorization_id"], self.mission_id, 1,
            self.clock(),
        )
        self.assertEqual(direct.problem, self.auth.PROBLEM_LEDGER_INCONSISTENT)

    def test_S3b_recorded_expiration_is_reconciled_exactly(self):
        self.clock.advance(20)
        self.edit(self.mission_id, 1, objective="v2")
        second = self.approve(self.mission_id, 2)
        self.assertEqual(self.ledger_kinds(self.mission_id),
                         ["ISSUED", "EXPIRED", "INVALIDATED_BY_EDIT", "ISSUED"])
        good = json.loads(self.read_bytes())
        self.assertIsNone(self.auth.reconcile_mission_history(
            good, good["missions"][self.mission_id]
        ))

        def refuse(document, label):
            direct = self.auth.validate_authorization_use(
                document, second["authorization_id"], self.mission_id, 2,
                self.clock(),
            )
            self.assertFalse(direct.valid, label)
            self.assertEqual(direct.problem, self.auth.PROBLEM_LEDGER_INCONSISTENT,
                             (label, direct.detail))
            self.write_raw(json.dumps(document))
            with self.assertRaises(self.mission_store.MissionStoreError):
                self.store.load()
            self.assertFalse(self.service.validate_authorization(
                second["authorization_id"], self.mission_id, 2
            ).valid)

        # Deleted.
        document = json.loads(json.dumps(good))
        document["authority_ledger"] = [
            e for e in document["authority_ledger"] if e["kind"] != "EXPIRED"
        ]
        refuse(document, "deleted")
        # Moved before ISSUED.
        document = json.loads(json.dumps(good))
        ledger = document["authority_ledger"]
        ledger[0], ledger[1] = ledger[1], ledger[0]
        refuse(document, "moved")
        # Retimed away from the mutation that produced it.
        document = json.loads(json.dumps(good))
        document["authority_ledger"][1]["recorded_at"] -= 5
        refuse(document, "retimed")
        # Duplicated.
        document = json.loads(json.dumps(good))
        document["authority_ledger"].insert(2, dict(
            document["authority_ledger"][1], entry_id="ml-" + "6" * 32
        ))
        refuse(document, "duplicated")

    def test_S3c_expired_then_denied_after_edit_records_in_policy_order(self):
        # A different mutation shape after expiry: EDIT then DENY. EXPIRED is
        # recorded once, at the first mutation, and never again.
        self.clock.advance(20)
        self.edit(self.mission_id, 1, objective="v2")
        self.deny(self.mission_id, 2)
        self.assertEqual(self.ledger_kinds(self.mission_id),
                         ["ISSUED", "EXPIRED", "INVALIDATED_BY_EDIT", "DENIED"])
        document = self.store.load()
        self.assertIsNone(self.auth.reconcile_mission_history(
            document, document["missions"][self.mission_id]
        ))


# ====================================================================
# T. Scenario A response contract: propose returns the exact proposal
# ====================================================================


class TProposeResponseContractTests(ServiceFixture):

    def assert_coherent(self, outcome, expected_proposal, revision, mission_id):
        """The response's proposal, revision and digest describe ONE stored
        revision and agree with each other and with the record."""
        self.assertEqual(outcome["mission_id"], mission_id)
        self.assertEqual(outcome["revision"], revision)
        self.assertEqual(outcome["proposal"],
                         mission_record.validate_proposal(expected_proposal))
        self.assertEqual(outcome["proposal_digest_sha256"],
                         mission_record.proposal_digest(outcome["proposal"]))
        entry = self.service.get(mission_id)["record"]["revisions"][revision - 1]
        self.assertEqual(outcome["proposal"], entry["proposal"])
        self.assertEqual(outcome["proposal_digest_sha256"],
                         entry["proposal_digest_sha256"])
        self.assertEqual(entry["revision"], revision)

    def test_T1_initial_propose_returns_the_exact_canonical_proposal(self):
        request_id = self.service.mint_request_id(self.context)
        # Non-canonical input order is normalized; what comes back is the
        # exact canonical proposal that was stored, not the raw input.
        raw = proposal(requested_action_scope=list(reversed(
            proposal()["requested_action_scope"]
        )))
        outcome = self.service.propose(request_id, raw, self.context)
        self.assertFalse(outcome["idempotent"])
        self.assertEqual(outcome["state"], "AWAITING_DECISION")
        self.assert_coherent(outcome, raw, 1, outcome["mission_id"])
        self.assertEqual(outcome["proposal"]["requested_action_scope"],
                         sorted(raw["requested_action_scope"]))
        self.assertEqual(set(outcome), {
            "mission_id", "revision", "state", "request_id", "idempotent",
            "proposal", "proposal_digest_sha256",
        })
        # The returned proposal is a copy: mutating it changes nothing.
        outcome["proposal"]["objective"] = "mutated by caller"
        self.assertEqual(self.service.get(outcome["mission_id"])["record"][
            "revisions"][0]["proposal"]["objective"], raw["objective"])

    def test_T2_exact_retry_returns_the_same_triple(self):
        request_id = self.service.mint_request_id(self.context)
        first = self.service.propose(request_id, proposal(), self.context)
        self.clock.advance(30)
        retry = self.service.propose(request_id, proposal(), self.context)
        self.assertTrue(retry["idempotent"])
        for key in ("mission_id", "revision", "state", "request_id",
                    "proposal", "proposal_digest_sha256"):
            self.assertEqual(retry[key], first[key], key)
        self.assert_coherent(retry, proposal(), 1, first["mission_id"])
        self.assertEqual(self.service.get(first["mission_id"])["authorizations"],
                         [])

    def test_T3_retry_after_edits_reports_the_current_coherent_triple(self):
        request_id = self.service.mint_request_id(self.context)
        first = self.service.propose(request_id, proposal(), self.context)
        mission_id = first["mission_id"]
        self.edit(mission_id, 1, objective="second objective")
        self.edit(mission_id, 2, objective="third objective",
                  requested_scope="narrower")
        retry = self.service.propose(request_id, proposal(), self.context)
        self.assertTrue(retry["idempotent"])
        self.assertEqual(retry["mission_id"], mission_id)
        self.assert_coherent(
            retry, proposal(objective="third objective",
                            requested_scope="narrower"), 3, mission_id,
        )
        self.assertNotEqual(retry["proposal_digest_sha256"],
                            first["proposal_digest_sha256"])
        self.assertNotEqual(retry["proposal"], first["proposal"])
        # A-2' unchanged: the replay comparison is against revision 1, so
        # the CURRENT content is a conflict and revision-1 content replays.
        self.assertRefuses(self.mission_service.PROBLEM_REQUEST_ID_CONFLICT,
                           self.service.propose, request_id,
                           proposal(objective="third objective",
                                    requested_scope="narrower"), self.context)
        again = self.service.propose(request_id, proposal(), self.context)
        self.assertEqual(again, retry)


if __name__ == "__main__":
    unittest.main()
