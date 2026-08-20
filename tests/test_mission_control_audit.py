import json
import tempfile
import unittest
from pathlib import Path

from mission_control.audit import MissionControlAuditLog


class MissionControlAuditLogTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.repo = Path(self.tempdir.name).resolve()
        self.audit = MissionControlAuditLog(self.repo)

    def tearDown(self):
        self.tempdir.cleanup()

    def test_append_persists_structured_record(self):
        record = self.audit.append(
            "herd-1",
            "herd.created",
            actor="human",
            data={
                "terminal_id": "terminal-123",
                "model": "gpt-5.6-sol",
            },
        )

        self.assertTrue(self.audit.path.exists())
        self.assertEqual(record.herd_id, "herd-1")
        self.assertEqual(record.event_type, "herd.created")
        self.assertEqual(record.actor, "human")
        self.assertEqual(
            record.data["terminal_id"],
            "terminal-123",
        )

        loaded = self.audit.read()

        self.assertEqual(loaded, [record])

    def test_append_is_chronological_and_append_only(self):
        first = self.audit.append(
            "herd-1",
            "proposal.created",
            data={"proposal_id": "proposal-1"},
        )
        second = self.audit.append(
            "herd-1",
            "proposal.approved",
            actor="human",
            data={"proposal_id": "proposal-1"},
        )

        records = self.audit.read()

        self.assertEqual(
            [record.id for record in records],
            [first.id, second.id],
        )
        self.assertLessEqual(
            first.timestamp_ms,
            second.timestamp_ms,
        )

    def test_read_filters_by_herd(self):
        self.audit.append(
            "herd-1",
            "execution.started",
        )
        self.audit.append(
            "herd-2",
            "execution.started",
        )

        records = self.audit.read(
            herd_id="herd-2",
        )

        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].herd_id, "herd-2")

    def test_read_filters_by_event_type(self):
        self.audit.append(
            "herd-1",
            "execution.started",
        )
        self.audit.append(
            "herd-1",
            "execution.completed",
        )
        self.audit.append(
            "herd-1",
            "execution.failed",
        )

        records = self.audit.read(
            event_types={
                "execution.completed",
                "execution.failed",
            },
        )

        self.assertEqual(
            [record.event_type for record in records],
            [
                "execution.completed",
                "execution.failed",
            ],
        )

    def test_read_limit_returns_newest_records_in_order(self):
        for index in range(5):
            self.audit.append(
                "herd-1",
                f"event.{index}",
            )

        records = self.audit.read(limit=2)

        self.assertEqual(
            [record.event_type for record in records],
            ["event.3", "event.4"],
        )

    def test_empty_log_reads_as_empty(self):
        self.assertEqual(
            self.audit.read(),
            [],
        )

    def test_invalid_append_arguments_are_rejected(self):
        with self.assertRaises(ValueError):
            self.audit.append(
                "",
                "event",
            )

        with self.assertRaises(ValueError):
            self.audit.append(
                "herd-1",
                "",
            )

        with self.assertRaises(ValueError):
            self.audit.append(
                "herd-1",
                "event",
                actor="",
            )

        with self.assertRaises(ValueError):
            self.audit.append(
                "herd-1",
                "event",
                data="not-an-object",
            )

    def test_invalid_read_filters_are_rejected(self):
        with self.assertRaises(ValueError):
            self.audit.read(limit=0)

        with self.assertRaises(ValueError):
            self.audit.read(herd_id="")

        with self.assertRaises(ValueError):
            self.audit.read(event_types=[])

    def test_corrupt_audit_line_fails_closed(self):
        self.audit.root.mkdir(parents=True)
        self.audit.path.write_text(
            '{"schema_version":1}\n'
            '{not-json}\n'
        )

        with self.assertRaisesRegex(
            RuntimeError,
            "journal is corrupt",
        ):
            self.audit.read()

    def test_wrong_repo_record_is_rejected(self):
        record = self.audit.append(
            "herd-1",
            "event",
        )
        data = record.to_dict()
        data["repo_path"] = "/tmp/different-repo"

        self.audit.path.write_text(
            json.dumps(data) + "\n"
        )

        with self.assertRaisesRegex(
            RuntimeError,
            "different repository",
        ):
            self.audit.read()


if __name__ == "__main__":
    unittest.main()
