import json
import tempfile
import unittest

from pathlib import Path

from herdr.control_plane import HerdrControlPlane
from herdr.events import (
    append_event,
    event_path,
    read_events,
)
from herdr.instance import HerdrInstance


class MissionControlEventTests(unittest.TestCase):
    def make_instance(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        return HerdrInstance(Path(temp.name))

    def test_append_and_read_events(self):
        herd = self.make_instance()

        first = append_event(
            herd,
            "task.dispatched",
            actor="supervisor",
            data={"task_id": "task-1"},
        )

        second = append_event(
            herd,
            "agent.status_changed",
            actor="executor1",
            data={"status": "working"},
        )

        events = read_events(herd)

        self.assertEqual(len(events), 2)
        self.assertEqual(events[0]["id"], first["id"])
        self.assertEqual(events[1]["id"], second["id"])
        self.assertEqual(events[0]["type"], "task.dispatched")
        self.assertEqual(events[1]["actor"], "executor1")
        self.assertEqual(events[1]["data"]["status"], "working")

        for event in events:
            self.assertEqual(event["schema_version"], 1)
            self.assertEqual(event["repo"], str(herd.repo))
            json.dumps(event)

    def test_read_limit_returns_newest_in_chronological_order(self):
        herd = self.make_instance()

        for index in range(5):
            append_event(
                herd,
                "test.event",
                data={"index": index},
            )

        events = read_events(herd, limit=2)

        self.assertEqual(
            [event["data"]["index"] for event in events],
            [3, 4],
        )

    def test_malformed_journal_line_is_skipped(self):
        herd = self.make_instance()

        append_event(herd, "valid.event")

        with event_path(herd).open("a", encoding="utf-8") as handle:
            handle.write("not json\n")

        events = read_events(herd)

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["type"], "valid.event")

    def test_control_plane_exposes_event_journal(self):
        herd = self.make_instance()

        control = HerdrControlPlane()
        control.emit_event(
            herd.repo,
            "runtime.started",
            actor="supervisor",
            data={"workspace_id": "ws-1"},
        )
        control.emit_event(
            herd.repo,
            "task.dispatched",
            data={"task_id": "task-1"},
        )

        events = control.events(
            herd.repo,
            limit=1,
        )

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["type"], "task.dispatched")
        self.assertEqual(events[0]["data"]["task_id"], "task-1")


if __name__ == "__main__":
    unittest.main()
