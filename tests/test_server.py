import json
import tempfile
import threading
import unittest

from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from herdr.server import create_server


class FakeControlPlane:
    def __init__(self):
        self.dispatched = []

    def dispatch_task(
        self,
        repo,
        text,
        *,
        rejection_drill=False,
        task_policy=None,
    ):
        record = {
            "repo": str(Path(repo).resolve()),
            "text": text,
            "rejection_drill": rejection_drill,
            "task_policy": task_policy,
        }
        self.dispatched.append(record)
        return {
            "id": "task-1",
            "status": "ACTIVE",
            "description": text,
        }

    def snapshot(self, repo):
        return {
            "repo": str(Path(repo).resolve()),
            "schema_version": 1,
        }

    def events(self, repo, *, limit=100):
        return [
            {
                "repo": str(Path(repo).resolve()),
                "limit": limit,
            }
        ]


class MissionControlServerTests(unittest.TestCase):
    def start_server(self, repo=None):
        server = create_server(
            repo,
            port=0,
            control_plane=FakeControlPlane(),
        )
        thread = threading.Thread(
            target=server.serve_forever,
            daemon=True,
        )
        thread.start()

        self.addCleanup(thread.join, 2)
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)

        host, port = server.server_address[:2]
        return f"http://{host}:{port}"

    def post_json(self, url, payload):
        request = Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
            },
            method="POST",
        )

        with urlopen(request, timeout=2) as response:
            return (
                response.status,
                json.loads(
                    response.read().decode("utf-8")
                ),
            )

    def read_json(self, url):
        with urlopen(url, timeout=2) as response:
            return (
                response.status,
                json.loads(
                    response.read().decode("utf-8")
                ),
            )

    def test_health_endpoint(self):
        base = self.start_server()

        status, payload = self.read_json(
            base + "/api/v1/health"
        )

        self.assertEqual(status, 200)
        self.assertEqual(
            payload,
            {
                "api_version": 1,
                "status": "ok",
            },
        )

    def test_snapshot_and_events_use_default_repo(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)

        repo = Path(temp.name)
        base = self.start_server(repo)

        status, snapshot = self.read_json(
            base + "/api/v1/snapshot"
        )
        self.assertEqual(status, 200)
        self.assertEqual(
            snapshot["repo"],
            str(repo.resolve()),
        )

        status, payload = self.read_json(
            base + "/api/v1/events?limit=7"
        )
        self.assertEqual(status, 200)
        self.assertEqual(
            payload["events"][0]["repo"],
            str(repo.resolve()),
        )
        self.assertEqual(
            payload["events"][0]["limit"],
            7,
        )

    def test_unknown_endpoint_returns_json_404(self):
        base = self.start_server()

        with self.assertRaises(HTTPError) as ctx:
            urlopen(
                base + "/api/v1/nope",
                timeout=2,
            )

        self.assertEqual(ctx.exception.code, 404)

        payload = json.loads(
            ctx.exception.read().decode("utf-8")
        )
        self.assertEqual(
            payload["error"]["code"],
            "not_found",
        )
    def test_post_task_dispatches_through_control_plane(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        repo = Path(temp.name)

        control = FakeControlPlane()
        server = create_server(
            repo,
            port=0,
            control_plane=control,
        )
        thread = threading.Thread(
            target=server.serve_forever,
            daemon=True,
        )
        thread.start()

        self.addCleanup(thread.join, 2)
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)

        host, port = server.server_address[:2]
        base = f"http://{host}:{port}"

        status, payload = self.post_json(
            base + "/api/v1/task",
            {
                "text": "Investigate the anomaly",
                "rejection_drill": True,
                "task_policy": {
                    "rules": [
                        "Do not modify migrations",
                    ],
                },
            },
        )

        self.assertEqual(status, 200)
        self.assertEqual(
            payload["task"]["id"],
            "task-1",
        )
        self.assertEqual(
            control.dispatched,
            [
                {
                    "repo": str(repo.resolve()),
                    "text": "Investigate the anomaly",
                    "rejection_drill": True,
                    "task_policy": {
                        "rules": [
                            "Do not modify migrations",
                        ],
                    },
                }
            ],
        )


    def test_ui_shell_is_served(self):
        base = self.start_server()

        with urlopen(base + "/", timeout=2) as response:
            body = response.read().decode("utf-8")

            self.assertEqual(response.status, 200)
            self.assertTrue(
                response.headers.get("Content-Type", "").startswith(
                    "text/html"
                )
            )

        self.assertIn("Dodging Infinity", body)
        self.assertIn("Mission Control", body)
        self.assertIn('id="objective"', body)
        self.assertIn("/api/v1/task", body)


if __name__ == "__main__":
    unittest.main()
