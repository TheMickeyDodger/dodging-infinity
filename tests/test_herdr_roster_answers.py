"""Deliverable 3, part 1 (integration across the REAL seam): the producer's own Herdr roster deciding what
the office may say about workers, exported for the server's projection and the webview's consumed state.

One real catalog answer (``query_v2``, the producer's roster, Herdr task views the Herdr source itself builds
with ``target_runtime.herdr_task_source._view``) holding one task per outcome:

- a task the runtime SEES with recorded roles but NO live session on them: the roster's own ``unverified`` /
  ``task_record_not_corroborated_by_a_live_session`` (no worker is currently confirmed; that is not proof the
  task never started);
- a task whose recorded role is held by a live agent reporting busy (actual worker activity), and one whose
  live agent reports blocked;
- tasks whose own status is FAILED, ABORTED, COMPLETE and ERROR (the source's inactive standing), and one
  whose status is a literal this office does not know (SUSPENDED): passed through as the real producer would.

Inside the producer's own hermetic harness (tests/test_catalog_delivery_link.py ``WorktreeFixtures``: a
private temporary directory, no process, no socket, a watchdog). The RUNTIME SNAPSHOT is the one synthetic
input (it stands for the runtime capture of live sessions; no process is observed), labelled in the export.
The test's temporary directory is written as a fixed placeholder, with what is derived from those paths
recomputed exactly as it is computed (each workspace's root key, the measured registry size).
"""
import json
import os
import re
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "tests"))

import test_catalog_delivery_link as link_tests  # noqa: E402
from mission_sources import contract, query_v2  # noqa: E402
from mission_sources import sources as sources_module  # noqa: E402
from target_runtime import herdr_task_source as hts  # noqa: E402

EXPORT = (REPO_ROOT / "integrations" / "pixel-agents" / "server" / "__tests__" / "fixtures" / "di-backend"
          / "herdr-roster-answers.FIXTURE.json")
PLACEHOLDER_ROOT = "/synthetic-test-root"
SOURCE_ID = "hd"
NOW = link_tests.NOW

# (task id, the task's own status, the live agent on its recorded session: None, or that agent's status)
TASKS = (
    ("task-unverified", "ACTIVE", None),
    ("task-working", "ACTIVE", "working"),   # the runtime's own literal (contract.RUNTIME_STATUS_ACTIVITY: working -> busy)
    ("task-blocked", "ACTIVE", "blocked"),
    ("task-failed", "FAILED", None),
    ("task-aborted", "ABORTED", None),
    ("task-complete", "COMPLETE", None),
    ("task-suspended", "SUSPENDED", None),
    ("task-error", "ERROR", None),          # the source's inactive standing: waiting on something
)


class HerdrRosterAnswerTests(link_tests.WorktreeFixtures):

    def answer(self):
        views, tasks, agents, self.workspaces = [], [], [], []
        source = {"source_id": SOURCE_ID, "producer": "synthetic"}
        for index, (task_id, status, live) in enumerate(TASKS):
            workspace = os.path.realpath(self.private_dir("ws-%d" % index))
            self.workspaces.append(workspace)
            # the fields a real Herdr task record carries (its repo is its own root, so the source finds it consistent)
            record = {"id": task_id, "status": status, "repo": workspace, "version": 1, "started_at": NOW - 600,
                      "description": "Synthetic task %s: %s" % (task_id, status.lower())}
            views.append(hts._view(source, [workspace], workspace, record))
            session = "session-%d" % index
            tasks.append({"task_id": task_id, "workspace_realpath": workspace, "bindings": [
                {"logical": "lead", "role": "lead", "agent": "lead-%d" % index, "session": session,
                 "pane_id": "pane-%d" % index, "workspace_id": "w%d" % index}]})
            if live is not None:
                agents.append({"agent": "lead-%d" % index, "provider": "claude", "status": live, "session": session,
                               "pane_id": "pane-%d" % index, "workspace_id": "w%d" % index})
        snapshot = {"schema": contract.SNAPSHOT_SCHEMA, "version": contract.SNAPSHOT_VERSION, "observed_at": NOW,
                    "producer": "synthetic", "tasks": tasks, "agents": agents}
        base = self.fresh_dir("answer")
        store_dir = os.path.join(base, "sources", SOURCE_ID)
        os.makedirs(store_dir, mode=0o700)
        document = {"mission_sources_registry_schema_version": 1, "sources": [
            {"source_id": SOURCE_ID, "kind": contract.KIND_TASK_NATIVE, "store_dir": store_dir,
             "producer": "local_request", "max_age_seconds": 60, "registered_at": NOW}]}

        def read(source, now, budget):
            return sources_module._reading(source, now, contract.HEALTH_FRESH, None, views)

        registry = link_tests.StubRegistry(os.path.join(base, "registry"), document)
        response = query_v2.query_v2(registry, snapshot, link_tests.empty_chains(), NOW,
                                     readers={contract.KIND_TASK_NATIVE: read})
        self.document = document
        return response, snapshot

    def by_task(self, response):
        return dict((m["execution"]["task_id"], m) for m in response["missions"])

    def test_HR1_the_producers_roster_decides_each_task(self):
        response, _snapshot = self.answer()
        missions = self.by_task(response)
        self.assertEqual(sorted(missions), sorted(t[0] for t in TASKS))
        unverified = missions["task-unverified"]["binding"]
        self.assertEqual((unverified["state"], unverified["reason"]),
                         ("unverified", "task_record_not_corroborated_by_a_live_session"))
        self.assertEqual(missions["task-unverified"]["members"], [])
        for task_id in ("task-working", "task-blocked"):
            self.assertEqual(missions[task_id]["binding"]["state"], "bound", task_id)
            self.assertEqual(len(missions[task_id]["members"]), 1, task_id)
        self.assertEqual(missions["task-suspended"]["lifecycle"]["state"], "SUSPENDED")

    def test_HR2_exports_the_answer_reproducibly(self):
        response, snapshot = self.answer()
        doc = json.dumps({
            "fixture": True,
            "generator": "tests/test_herdr_roster_answers.py HR2",
            "label": ("FIXTURE (generated, not live): the REAL catalog answer (query_v2 and the producer's roster)"
                      " over Herdr task views the Herdr source builds, one per roster outcome. The runtime"
                      " snapshot is SYNTHETIC (it stands for the runtime capture of live sessions; no process is"
                      " observed). The test's temporary directory is written as " + PLACEHOLDER_ROOT + " (each"
                      " workspace's root key and the measured registry size recomputed for it)"),
            "now": NOW,
            "runtime_snapshot": json.loads(self.substitute(json.dumps(snapshot))),
            "response": json.loads(self.portable(response)),
        }, indent=2, sort_keys=True) + "\n"
        self.assertNotIn(self.root, doc)
        if os.environ.get("DI_EXPORT_PANEL_ANSWERS") == "1":
            EXPORT.write_text(doc, encoding="utf-8")
        self.assertEqual(EXPORT.read_text(encoding="utf-8"), doc)

    def substitute(self, text):
        for workspace in self.workspaces:
            text = text.replace(hts.root_key(workspace), hts.root_key(workspace.replace(self.root, PLACEHOLDER_ROOT)))
        text = text.replace(self.root, PLACEHOLDER_ROOT)
        return re.sub(r"(%s/[a-z]+)-[A-Za-z0-9_]{8}" % re.escape(PLACEHOLDER_ROOT), r"\1-0", text)

    def portable(self, response):
        self.assertEqual(response["registry"]["serialized_bytes"], len(json.dumps(self.document)),
                         "the registry size is the harness's measurement")
        response = dict(response, registry=dict(response["registry"],
                                                serialized_bytes=len(self.substitute(json.dumps(self.document)))))
        return self.substitute(json.dumps(response, sort_keys=True))


if __name__ == "__main__":
    unittest.main()
