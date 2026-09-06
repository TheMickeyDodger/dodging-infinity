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
  F  P1-A6 parent-authority seam in pr_delivery
"""

import ast
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
