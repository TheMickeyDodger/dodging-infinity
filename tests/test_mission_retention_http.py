"""The owner's "Delete local data" over the REAL panel HTTP route (Deliverable 2, round 9 finding 1).

Every test serves the real ``mission_panel.http_adapter.PanelServer`` over a service built with the
real ``MissionCoreRetention``, on an isolated temporary protected store the fixture creates and owns
(never the user's), and issues the actual ``POST /missions/<mn-id>/delete-local-data`` the office's
relay sends. What it asserts is what the route ANSWERS (its HTTP status and body) and what the stores
then hold, never an id's shape:

- an automatically RECLAIMED Mission (payload gone, summary KEPT) is still listed, and its owner's
  delete completes: the kept summary is removed, the tombstone remains;
- a deletion that started and could not finish is answered as ACCEPTED and pending (``ok: true``),
  never as a refusal, and the next pass finishes it;
- a second delete is a refusal (already deleted).

The answer bodies are exported, with the Mission's id normalized, to
``integrations/pixel-agents/server/__tests__/fixtures/di-backend/mission-panel-delete-answers.FIXTURE.json``
(``DI_EXPORT_PANEL_ANSWERS=1`` writes it; otherwise it is checked for drift), so the office's relay
and the webview are tested on the bytes this route really sends.
"""

import http.client
import json
import os
import sys
import threading
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "tests"))

from local_request import store as store_module  # noqa: E402
from mission_panel import http_adapter  # noqa: E402
from mission_panel import journal as journal_module  # noqa: E402
from mission_panel import service as service_module  # noqa: E402
from mission_retention import reclaim  # noqa: E402
from mission_retention import store as retention_store  # noqa: E402
from test_mission_retention import USER_WORDS, RetentionFixture  # noqa: E402

TOKEN = "t" * 43
ANSWERS = (REPO_ROOT / "integrations" / "pixel-agents" / "server" / "__tests__" / "fixtures"
           / "di-backend" / "mission-panel-delete-answers.FIXTURE.json")
PLACEHOLDER_ID = "mn-" + "0" * 32
OWN = object()                                  # post_delete's default: this panel's own store


class DeleteOverHttpTests(RetentionFixture):

    exported = {}

    def setUp(self):
        super(DeleteOverHttpTests, self).setUp()
        panel = service_module.PanelService(
            self.panel_surface, journal_module.PanelJournal(self.state), self.convo,
            self.registry, self.clock, bridge=self.bridge, retention=self.retention)
        self.server = http_adapter.PanelServer(panel, TOKEN)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.stop)

    def stop(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(10)

    def post_delete(self, mission_id, displayed, mission_ref=None, store=OWN):
        """The exact request the office's relay sends (server/src/di/missionRequests.ts): the
        selected Mission's source-qualified ref and its store's identity, as the catalog reported
        them (by default, this panel's own store)."""
        port = self.server.server_address[1]
        payload = json.dumps({
            "displayed_state": displayed,
            "mission_ref": mission_ref or "mc:" + mission_id,
            "store_identity": self.retention.store_identity if store is OWN else store}).encode()
        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
        try:
            connection.request("POST", "/missions/%s/delete-local-data" % mission_id, payload, {
                "Host": "127.0.0.1:%d" % port, "Authorization": "Bearer " + TOKEN,
                "Content-Type": "application/json", "Content-Length": str(len(payload))})
            response = connection.getresponse()
            return response.status, json.loads(response.read())
        finally:
            connection.close()

    def keep(self, name, status, body, mission_id):
        text = json.dumps(body, sort_keys=True).replace(mission_id, PLACEHOLDER_ID)
        DeleteOverHttpTests.exported[name] = {"status": status, "body": json.loads(text)}

    def test_H1_a_reclaimed_listed_mission_is_deleted_over_the_route_its_kept_summary_removed(self):
        view = self.completed()
        mission_id = view["mission_id"]
        self.clock.now = self.terminal_at(mission_id) + reclaim.RETENTION_SECONDS
        self.assertEqual(self.retention.sweep().reclaimed, 1)
        kept = self.record(mission_id)
        self.assertEqual(kept["phase"], retention_store.PHASE_RECLAIMED)
        self.assertIsNotNone(kept["summary"], "automatic reclamation KEPT the summary")
        status, body = self.post_delete(mission_id, "COMPLETED")
        self.assertEqual(status, 200, body)
        self.assertEqual((body["ok"], body["deletion"]), (True, service_module.DELETION_COMPLETED))
        self.assertEqual((body["deleted"]["phase"], body["deleted"]["previously"]),
                         ("deleted", "reclaimed"))
        self.assertTrue(body["deleted"]["removed"]["summary"], "a REAL summary was removed")
        # the stores: the summary is gone, the tombstone remains, nothing reads as intact
        tombstone = self.record(mission_id)
        self.assertEqual((tombstone["phase"], tombstone["summary"]),
                         (retention_store.PHASE_DELETED, None))
        self.assertEqual(sorted(tombstone), sorted(retention_store.RECORD_KEYS))
        self.assert_payload_gone(mission_id)
        self.keep("completed_after_reclamation", status, body, mission_id)
        # and asked again, it is a refusal (already deleted), on the same route
        status, again = self.post_delete(mission_id, "COMPLETED")
        self.assertEqual((status, again["ok"]), (200, False), again)
        self.assertEqual(again["problem"],
                         service_module.PROBLEM_DELETE_PREFIX + reclaim.REFUSE_ALREADY_DELETED)
        self.keep("refused_already_deleted", status, again, mission_id)

    def test_H2_a_deletion_that_started_and_failed_is_answered_accepted_and_pending(self):
        view = self.rejected()
        mission_id = view["mission_id"]
        failure = store_module.LocalRequestStoreError("the request store could not be written")
        with mock.patch.object(reclaim.request_store, "reclaim_requests", side_effect=failure):
            status, body = self.post_delete(mission_id, "REJECTED")
        self.assertEqual(status, 200, body)
        self.assertEqual((body["ok"], body["deletion"]), (True, service_module.DELETION_PENDING),
                         "accepted and pending, never a refusal")
        self.assertNotIn("problem", body)
        self.assertEqual(body["deleted"]["phase"], retention_store.PHASE_DELETING)
        # the stores agree: deleting, its summary gone, its words gone, never intact
        record = self.record(mission_id)
        self.assertEqual((record["phase"], record["summary"]), (retention_store.PHASE_DELETING, None))
        self.assertNotIn(USER_WORDS, self.bytes_of("mission_panel_journal.json").decode("utf-8"))
        self.keep("pending_started_not_finished", status, body, mission_id)
        # the next pass finishes what the caller was told is in progress
        self.retention.sweep()
        self.assertEqual(self.record(mission_id)["phase"], retention_store.PHASE_DELETED)
        self.assert_payload_gone(mission_id)

    def test_H3_a_refused_delete_over_the_route_leaves_a_kept_summary_kept(self):
        view = self.completed()
        mission_id = view["mission_id"]
        self.clock.now = self.terminal_at(mission_id) + reclaim.RETENTION_SECONDS
        self.retention.sweep()
        kept = self.record(mission_id)
        with self.guard.hold(mission_id):
            status, body = self.post_delete(mission_id, "COMPLETED")
        self.assertEqual((status, body["ok"]), (200, False), body)
        self.assertEqual(body["problem"], service_module.PROBLEM_DELETE_PREFIX + reclaim.REFUSE_BUSY)
        self.assertEqual(self.record(mission_id), kept, "refused: the kept summary is still kept")
        self.keep("refused_busy", status, body, mission_id)

    def test_H4_a_stale_display_is_refused_over_the_route_with_nothing_written(self):
        view = self.rejected()
        mission_id = view["mission_id"]
        before = retention_store.RetentionStore(self.state).load()["records"]
        status, body = self.post_delete(mission_id, "COMPLETED")        # the record says REJECTED
        self.assertEqual((status, body["ok"]), (200, False), body)
        self.assertEqual(body["problem"], service_module.PROBLEM_DELETE_PREFIX + reclaim.REFUSE_STALE)
        self.assertEqual(retention_store.RetentionStore(self.state).load()["records"], before)
        self.assert_payload_intact()
        self.keep("refused_status_stale", status, body, mission_id)

    def two_sources_one_native_id(self):
        """The same native Mission id in TWO registered mission_core stores (the registry refuses
        only a duplicate source id or store): this panel's own, and a second, isolated one."""
        from test_mission_retention import CatalogRetentionTests
        view = self.completed()
        other = os.path.join(self.tmp.name, "second-mission-core-store")
        os.mkdir(other, 0o700)
        for name in ("missions.json", "mission_panel_journal.json", "local_requests.json"):
            target = os.path.join(other, name)
            with open(target, "wb") as handle:
                handle.write(self.bytes_of(name))
            os.chmod(target, 0o600)
        response = CatalogRetentionTests.query(
            self, [("mc", "mission_core", self.state), ("mc-two", "mission_core", other)],
            self.clock.now)
        rows = {m["source_id"]: m for m in response["missions"]
                if m["native_id"] == view["mission_id"]}
        self.assertEqual(sorted(rows), ["mc", "mc-two"], "two rows, one native id")
        return view, other, rows

    def other_bytes(self, other):
        return {name: open(os.path.join(other, name), "rb").read()
                for name in ("missions.json", "mission_panel_journal.json", "local_requests.json")}

    def test_TI1_the_catalog_and_the_panel_name_the_panels_store_identically(self):
        from mission_sources import sources as sources_module
        _, other, rows = self.two_sources_one_native_id()
        self.assertEqual(rows["mc"]["native"]["store_identity"], self.retention.store_identity)
        self.assertEqual(rows["mc-two"]["native"]["store_identity"],
                         reclaim.store_identity("mission_core", other))
        self.assertNotEqual(rows["mc"]["native"]["store_identity"],
                            rows["mc-two"]["native"]["store_identity"])
        for directory in (self.state, other):                  # the two derivations, pinned equal
            self.assertEqual(sources_module.store_identity("mission_core", directory),
                             reclaim.store_identity("mission_core", directory))

    def test_TI2_selecting_one_sources_row_never_deletes_or_reports_on_the_other(self):
        view, other, rows = self.two_sources_one_native_id()
        mission_id = view["mission_id"]
        before_other = self.other_bytes(other)
        before_records = retention_store.RetentionStore(self.state).load()["records"]
        # the SECOND source's row, sent to this panel: refused first, nothing resolved by native id
        status, body = self.post_delete(mission_id, "COMPLETED", "mc-two:" + mission_id,
                                        rows["mc-two"]["native"]["store_identity"])
        self.assertEqual((status, body["ok"]), (200, False), body)
        self.assertEqual(body["problem"],
                         service_module.PROBLEM_DELETE_PREFIX + reclaim.REFUSE_SOURCE_MISMATCH)
        self.assertEqual(retention_store.RetentionStore(self.state).load()["records"], before_records)
        self.assert_payload_intact()
        self.assertEqual(self.other_bytes(other), before_other, "the other store is untouched")
        self.keep("refused_source_mismatch", status, body, mission_id)
        # a request that names no store (an older catalog reported none: null) is refused the same way
        status, body = self.post_delete(mission_id, "COMPLETED", "mc:" + mission_id, None)
        self.assertEqual(body["problem"],
                         service_module.PROBLEM_DELETE_PREFIX + reclaim.REFUSE_SOURCE_MISMATCH, body)
        self.assertIn("no named store", body["reason"])
        self.assert_payload_intact()
        # and a malformed one is a bad request, never resolved
        status, body = self.post_delete(mission_id, "COMPLETED", "mc:" + mission_id, "")
        self.assertEqual(body["problem"], service_module.PROBLEM_BAD_REQUEST, body)
        self.assert_payload_intact()
        # this panel's own row: deleted here, and ONLY here
        status, body = self.post_delete(mission_id, "COMPLETED", "mc:" + mission_id,
                                        rows["mc"]["native"]["store_identity"])
        self.assertEqual((status, body["ok"], body["deletion"]), (200, True, "completed"), body)
        self.assert_payload_gone(mission_id)
        self.assertEqual(self.other_bytes(other), before_other,
                         "the other store's Mission with the same native id is untouched")
        self.assertIn(USER_WORDS.encode(), before_other["mission_panel_journal.json"])

    @classmethod
    def tearDownClass(cls):
        super(DeleteOverHttpTests, cls).tearDownClass()
        if set(cls.exported) != {"completed_after_reclamation", "refused_already_deleted",
                                 "pending_started_not_finished", "refused_busy",
                                 "refused_status_stale", "refused_source_mismatch"}:
            return                                    # a filtered run checks nothing
        document = {
            "fixture": True,
            "label": "FIXTURE (generated, not live): the real answers of mission_panel's"
                     " POST /missions/<mn-id>/delete-local-data, served by"
                     " mission_panel.http_adapter.PanelServer over an isolated temporary store;"
                     " the Mission id is normalized to %s" % PLACEHOLDER_ID,
            "generator": "tests/test_mission_retention_http.py",
            "answers": dict(sorted(cls.exported.items())),
        }
        text = json.dumps(document, indent=2, sort_keys=True) + "\n"
        if os.environ.get("DI_EXPORT_PANEL_ANSWERS") == "1":
            ANSWERS.write_text(text, encoding="utf-8")
        elif ANSWERS.read_text(encoding="utf-8") != text:
            raise AssertionError("%s drifted from the route's real answers; regenerate with"
                                 " DI_EXPORT_PANEL_ANSWERS=1" % ANSWERS)


if __name__ == "__main__":
    unittest.main()
