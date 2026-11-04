"""Deliverable 3, part 1 (integration across the REAL seam): workflow Missions in the phases only a workflow
declares, read by the REAL catalog, exported for the server's projection and the webview's consumed state.

One real workflow store (``workflow_authority.store.WorkflowStore``) holding records built by the workflow
authority's own test constructor (tests/test_workflow_authority.py ``make_record``) in three phases:
DISPATCHED (a handoff was sent: start requested), VERIFIED (the declared verified success) and
NEEDS_REAUTHORIZATION (waiting on a person). Read by ``query_v2`` with its default workflow-store reader,
in this test's own temporary directory; nothing outside it is read or written.

For reproducibility the temporary directory is written as a fixed placeholder, with the measured registry
size recomputed for it (the harness's own ``len(json.dumps(document))``, measured again over the
substituted document), and ``secrets.token_hex`` is a deterministic counter for this run.
"""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "tests"))

from mission_sources import contract, contract_v2, query_v2  # noqa: E402
from target_runtime import dispatch as dispatch_module  # noqa: E402
from test_mission_lifecycle_answers import deterministic_token_hex  # noqa: E402
from test_workflow_authority import make_record  # noqa: E402
from workflow_authority import record as workflow_record  # noqa: E402
from workflow_authority import store as workflow_store  # noqa: E402

EXPORT = (REPO_ROOT / "integrations" / "pixel-agents" / "server" / "__tests__" / "fixtures" / "di-backend"
          / "workflow-phase-answers.FIXTURE.json")
PLACEHOLDER_ROOT = "/synthetic-test-root"
SOURCE_ID = "wf"
NOW = 1_800_000_000
PHASES = (("wf-dispatched", "DISPATCHED"), ("wf-verified", workflow_record.PHASE_VERIFIED),
          ("wf-reauthorize", "NEEDS_REAUTHORIZATION"))


class WorkflowPhaseAnswerTests(unittest.TestCase):

    def setUp(self):
        patcher = mock.patch("secrets.token_hex", deterministic_token_hex())
        patcher.start()
        self.addCleanup(patcher.stop)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = os.path.realpath(self.tmp.name)

    def answer(self):
        directory = os.path.join(self.root, "wf-store")
        os.mkdir(directory, 0o700)
        document = workflow_store.default_document()
        for workflow_id, phase in PHASES:
            entry = make_record(workflow_id, human_intent="Synthetic workflow in %s" % phase)
            entry["phase"] = phase
            if phase in ("DISPATCHED", workflow_record.PHASE_VERIFIED):
                # as the workflow authority records a sent handoff: the target engine it went to, and the runtime's
                # own receipt of it (the store validates both on save)
                entry["target_engine"] = {"alias": "herd-synthetic", "task_id": "task-%s" % workflow_id,
                                          "repo": "/control/repo", "dispatched_at": NOW - 600}
                entry["receipts"] = [dispatch_module.dispatch_receipt(
                    entry, now=NOW - 600, sequence_number=1, turn_id_factory=lambda: "disp-0000000000000001")]
            if phase == workflow_record.PHASE_VERIFIED:
                summary = "Synthetic verified result: the change was checked and accepted."
                entry["verified_result"] = {"summary": summary, "digest": workflow_record.text_digest(summary),
                                            "recorded_at": NOW - 60}
            workflow_store.add_workflow(document, entry)
        with workflow_store.exclusive_store_lock(directory):
            workflow_store.WorkflowStore(directory).save(document)
        registry_document = {"mission_sources_registry_schema_version": 1, "sources": [
            {"source_id": SOURCE_ID, "kind": "workflow_store", "store_dir": directory, "producer": "local_request",
             "max_age_seconds": 60, "registered_at": NOW}]}

        class Registry(object):                    # the one method query_v2 calls
            def load_measured(self, absent_is_empty=True):
                return registry_document, len(json.dumps(registry_document))
        Registry.directory = os.path.join(self.root, "registry")
        snapshot = {"schema": contract.SNAPSHOT_SCHEMA, "version": contract.SNAPSHOT_VERSION, "observed_at": NOW,
                    "producer": "synthetic", "tasks": [], "agents": []}
        chains = {"schema": contract_v2.CHAIN_OBSERVATION_SCHEMA, "version": contract_v2.CHAIN_OBSERVATION_VERSION,
                  "observed_at": NOW, "producer": "synthetic", "roots": [], "unavailable": [], "over_capacity": []}
        self.registry_document = registry_document
        return query_v2.query_v2(Registry(), snapshot, chains, NOW, max_age_seconds=contract.DEFAULT_MAX_AGE_SECONDS)

    def test_WP1_the_real_reader_declares_each_phase(self):
        response = self.answer()
        phases = dict((m["native_id"], m["workflow_phase"]["phase"]) for m in response["missions"])
        self.assertEqual(phases, dict(PHASES))
        dispatched = next(m for m in response["missions"] if m["native_id"] == "wf-dispatched")
        self.assertEqual(dispatched["native"]["sent_handoff_receipts"], 1)

    def test_WP2_exports_the_answer_reproducibly(self):
        response = self.answer()
        measured = len(json.dumps(self.registry_document))
        self.assertEqual(response["registry"]["serialized_bytes"], measured, "the registry size is the harness's measurement")
        response = dict(response, registry=dict(
            response["registry"], serialized_bytes=len(self.substitute(json.dumps(self.registry_document)))))
        doc = json.dumps({
            "fixture": True,
            "generator": "tests/test_workflow_phase_answers.py WP2",
            "label": ("FIXTURE (generated, not live): the REAL catalog answer (query_v2, its workflow-store reader)"
                      " over one real workflow store holding records built by the workflow authority's test"
                      " constructor in DISPATCHED and VERIFIED (each with the target engine it was sent to and the"
                      " runtime's own handoff receipt; VERIFIED with its verified result) and NEEDS_REAUTHORIZATION."
                      " SYNTHETIC record content: the constructor's fields, the phase set, no engine ran. For reproducibility only: the temporary directory is written as "
                      + PLACEHOLDER_ROOT + " (the measured registry size recomputed for it) and secrets.token_hex"
                      " is a deterministic counter for this run"),
            "now": NOW,
            "response": json.loads(self.substitute(json.dumps(response, sort_keys=True))),
        }, indent=2, sort_keys=True) + "\n"
        self.assertNotIn(self.root, doc)
        if os.environ.get("DI_EXPORT_PANEL_ANSWERS") == "1":
            EXPORT.write_text(doc, encoding="utf-8")
        self.assertEqual(EXPORT.read_text(encoding="utf-8"), doc)

    def substitute(self, text):
        return text.replace(self.root, PLACEHOLDER_ROOT)


if __name__ == "__main__":
    unittest.main()
