"""Deliverable 3, part 1 (integration across the REAL seam): an ARCHIVED Mission's actions are refused
SERVER-SIDE, over the real panel HTTP route, not merely hidden in the office.

Every test serves the real ``mission_panel.http_adapter.PanelServer`` over a service built with the real
``MissionCoreRetention``, on the isolated temporary stores the panel fixture creates and owns (never the
user's), and sends the exact ``POST /requests/<lr-ref>/<action>`` the office's relay sends
(server/src/di/missionRequests.ts). What it asserts is what the route ANSWERS and what the stores then hold:

- a verified (archived) Mission: ``run dispatch`` (a restart) and ``run observe`` (execution) are refused
  as not offered, with nothing written and nothing started;
- the same Mission after its 30-day reclamation: ``run dispatch`` is still refused;
- the countercase: on an approved Mission that is NOT archived, the same ``run dispatch`` is accepted, so the
  refusal comes from the archived state, not from the route.

The answers are exported to
``integrations/pixel-agents/server/__tests__/fixtures/di-backend/mission-panel-archived-refusals.FIXTURE.json``
(``DI_EXPORT_PANEL_ANSWERS=1`` writes it; otherwise it is checked for drift) for the office's relay. For
reproducibility ``secrets.token_hex`` is a deterministic counter for this run (labelled in the export).
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

from mission_panel import http_adapter  # noqa: E402
from mission_panel import journal as journal_module  # noqa: E402
from mission_panel import service as service_module  # noqa: E402
from mission_retention import reclaim  # noqa: E402
from test_mission_lifecycle_answers import deterministic_token_hex  # noqa: E402
from test_mission_retention import RetentionFixture  # noqa: E402
from test_mission_retention_http import TOKEN  # noqa: E402

ANSWERS = (REPO_ROOT / "integrations" / "pixel-agents" / "server" / "__tests__" / "fixtures"
           / "di-backend" / "mission-panel-archived-refusals.FIXTURE.json")
EXPECTED = {"archived_run_dispatch", "archived_run_observe", "reclaimed_run_dispatch", "active_run_dispatch"}


class ArchivedRefusalOverHttpTests(RetentionFixture):

    exported = {}

    def setUp(self):
        patcher = mock.patch("secrets.token_hex", deterministic_token_hex())
        patcher.start()
        self.addCleanup(patcher.stop)
        super(ArchivedRefusalOverHttpTests, self).setUp()
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

    def post(self, path, body):
        """The exact request the office's relay sends for an action on a request."""
        port = self.server.server_address[1]
        payload = json.dumps(body).encode()
        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
        try:
            connection.request("POST", path, payload, {
                "Host": "127.0.0.1:%d" % port, "Authorization": "Bearer " + TOKEN,
                "Content-Type": "application/json", "Content-Length": str(len(payload))})
            response = connection.getresponse()
            return response.status, json.loads(response.read())
        finally:
            connection.close()

    def stores(self):
        return dict((name, self.bytes_of(name)) for name in (
            "missions.json", "local_requests.json", "mission_panel_journal.json"))

    def keep(self, name, status, body):
        ArchivedRefusalOverHttpTests.exported[name] = {"status": status, "body": body}

    def assert_refused(self, status, body):
        # refused BECAUSE of the archived stage: the action is not offered for a verified Mission
        self.assertEqual((status, body.get("ok"), body.get("problem")),
                         (200, False, service_module.PROBLEM_ACTION_NOT_OFFERED), body)
        self.assertEqual(body["details"]["stage"], "verified", body)

    def test_AR1_a_verified_archived_mission_refuses_a_restart_and_execution_with_nothing_written(self):
        view = self.completed()
        before, spawned = self.stores(), list(self.spawn.calls)
        for command in ("dispatch", "observe"):
            status, body = self.post("/requests/%s/run" % view["request_ref"], {"command": command})
            self.assert_refused(status, body)
            self.keep("archived_run_" + command, status, body)
        self.assertEqual(self.stores(), before, "a refused action writes nothing")
        self.assertEqual(self.spawn.calls, spawned, "nothing more was started")

    def test_AR2_after_its_reclamation_a_restart_is_still_refused(self):
        view = self.completed()
        self.clock.now = self.terminal_at(view["mission_id"]) + reclaim.RETENTION_SECONDS
        self.retention.sweep()
        before = self.stores()
        status, body = self.post("/requests/%s/run" % view["request_ref"], {"command": "dispatch"})
        self.assert_refused(status, body)
        self.keep("reclaimed_run_dispatch", status, body)
        self.assertEqual(self.stores(), before)

    def test_AR3_countercase_the_same_route_starts_a_mission_that_is_not_archived(self):
        view = self.of_record()
        self.approve(view)
        status, body = self.post("/requests/%s/run" % view["request_ref"], {"command": "dispatch"})
        self.assertEqual((status, body.get("ok")), (200, True), body)
        self.keep("active_run_dispatch", status, body)

    @classmethod
    def tearDownClass(cls):
        super(ArchivedRefusalOverHttpTests, cls).tearDownClass()
        if set(cls.exported) != EXPECTED:
            return                                    # a filtered run checks nothing
        document = {
            "fixture": True,
            "label": "FIXTURE (generated, not live): the real answers of mission_panel's POST /requests/<lr-ref>/run"
                     " and /withdraw on an ARCHIVED (verified, then reclaimed) Mission and, as the countercase, on an"
                     " approved Mission that is not archived; served by mission_panel.http_adapter.PanelServer over"
                     " an isolated temporary store. For reproducibility secrets.token_hex is a deterministic counter"
                     " for this run",
            "generator": "tests/test_mission_archived_refusal_http.py",
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
