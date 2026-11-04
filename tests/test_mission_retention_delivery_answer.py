"""Deliverable 2, round 10 (lead ruling: archived is read-only, not information-erasing): a REAL producer
answer holding a COMPLETE Herdr task whose delivery is linked by its own task and worktree, and a COMPLETE
Herdr task with no delivery record, exported for the office's archived-delivery tests.

Built by the catalog's own composition (``query_v2`` with its real delivery hooks: the delivery links reader,
the task-and-worktree key, the delivery handoff) over a record the delivery layer itself mints
(``pr_delivery.authorization.new_authorization``) and task views the Herdr source itself builds
(``target_runtime.herdr_task_source._view``), inside the producer's own hermetic harness
(tests/test_catalog_delivery_link.py ``WorktreeFixtures``: a private temporary directory, no process, no socket,
a watchdog). Nothing outside that directory is read or written, and no user store is touched.

The ONE edit to the producer's answer: this test's temporary directory, and the random suffixes of the
directories made under it, are written as fixed placeholders, so the export is reproducible; with them, what is
DERIVED from those paths is recomputed for the placeholder exactly as it is computed (each workspace's root key,
and the harness's measured registry size, ``len(json.dumps(document))`` over the same document).
"""
import json
import os
import re
import sys
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "tests"))

import test_catalog_delivery_link as link_tests  # noqa: E402
from target_runtime import herdr_task_source as hts  # noqa: E402

EXPORT = (REPO_ROOT / "integrations" / "pixel-agents" / "server" / "__tests__" / "fixtures" / "di-backend"
          / "archived-delivery-herdr-complete.FIXTURE.json")
PLACEHOLDER_ROOT = "/synthetic-test-root"
TASK_LINKED = "task-a"
TASK_UNLINKED = "task-b"


class _RecordingRegistry(link_tests.StubRegistry):
    """The harness's own registry stub, recording the document it measures (so its size can be measured again
    over the placeholder paths). Patched in for this test's composition only."""

    documents = []

    def __init__(self, directory, document):
        super(_RecordingRegistry, self).__init__(directory, document)
        _RecordingRegistry.documents.append(document)


class ArchivedDeliveryAnswerTests(link_tests.WorktreeFixtures):

    def complete_view(self, workspace, task_id):
        """The Herdr source's own view of a COMPLETE task (the only state a linked record follows)."""
        source = {"source_id": "hd", "producer": "synthetic"}
        return hts._view(source, [workspace], os.path.realpath(workspace),
                         {"id": task_id, "status": "COMPLETE"})

    def answer(self):
        worktrees = self.layout()
        (workspace_a, git_a), (workspace_b, _git_b) = worktrees["wt-a"], worktrees["wt-b"]
        self.workspaces = [os.path.realpath(workspace_a), os.path.realpath(workspace_b)]
        record = self.minted(link_tests.ID_A, git_a, TASK_LINKED)
        _RecordingRegistry.documents = []
        with mock.patch.object(link_tests, "StubRegistry", _RecordingRegistry):
            links = self.composed([record], [self.complete_view(workspace_a, TASK_LINKED),
                                             self.complete_view(workspace_b, TASK_UNLINKED)])
        self.assertEqual(len(_RecordingRegistry.documents), 1)
        self.registry_document = _RecordingRegistry.documents[0]
        return self.response, links

    def portable(self, response):
        """The answer as text AS THE PRODUCER WOULD EMIT IT were this test's temporary directory the placeholder: the
        directory (and mkdtemp suffixes) written as placeholders, and each workspace's root key (the Herdr source's own
        ``root_key``, a digest of the workspace path, part of its native id) recomputed for the placeholder path."""
        measured = len(json.dumps(self.registry_document))
        self.assertEqual(response["registry"]["serialized_bytes"], measured, "the registry size is the harness's measurement")
        response = dict(response, registry=dict(response["registry"],
                                                serialized_bytes=len(self.substitute(json.dumps(self.registry_document)))))
        return self.substitute(json.dumps(response, indent=2, sort_keys=True)) + "\n"

    def substitute(self, text):
        for workspace in self.workspaces:
            text = text.replace(hts.root_key(workspace), hts.root_key(workspace.replace(self.root, PLACEHOLDER_ROOT)))
        text = text.replace(self.root, PLACEHOLDER_ROOT)
        return re.sub(r"(%s/[a-z]+)-[A-Za-z0-9_]{8}" % re.escape(PLACEHOLDER_ROOT), r"\1-0", text)

    def test_AD1_the_real_answer_links_the_complete_task_and_leaves_the_other_unlinked(self):
        response, links = self.answer()
        herdr = [m for m in response["missions"] if m["lifecycle"]["model"] == "herdr.task/1"]
        self.assertEqual(len(herdr), 2)
        by_task = dict((m["execution"]["task_id"], m) for m in herdr)
        linked, unlinked = by_task[TASK_LINKED], by_task[TASK_UNLINKED]
        self.assertEqual(links[linked["mission_ref"]],
                         {"state": "linked", "delivery_id": link_tests.ID_A,
                          "basis": "record_task_worktree_binding"})
        self.assertEqual(links[unlinked["mission_ref"]]["state"], "unlinked_no_record")
        # both are the producer's COMPLETE shape: a terminal standing (what archives a Herdr task)
        for mission in (linked, unlinked):
            self.assertEqual(mission["lifecycle"]["standing"], "terminal", mission["mission_ref"])
        # the unlinked one carries the producer's handoff (the creation guidance an archived view must not offer)
        self.assertIsNotNone(unlinked.get("delivery_handoff"))

    def test_AD2_exports_the_answer_reproducibly(self):
        response, _links = self.answer()
        text = self.portable(response)
        self.assertNotIn(self.root, text)
        doc = json.dumps({
            "fixture": True,
            "generator": "tests/test_mission_retention_delivery_answer.py AD2",
            "label": ("FIXTURE (generated, not live): the catalog's own query_v2 answer over a delivery record the"
                      " delivery layer minted for a COMPLETE Herdr task's own task and worktree, and a second COMPLETE"
                      " task with no record; the only edit is the test's temporary directory written as "
                      + PLACEHOLDER_ROOT),
            "now": link_tests.NOW,
            "response": json.loads(text),
        }, indent=2, sort_keys=True) + "\n"
        if os.environ.get("DI_EXPORT_PANEL_ANSWERS") == "1":
            EXPORT.write_text(doc, encoding="utf-8")
        self.assertEqual(EXPORT.read_text(encoding="utf-8"), doc)


if __name__ == "__main__":
    unittest.main()
