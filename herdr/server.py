"""Local HTTP API for Dodging Infinity Mission Control."""

from __future__ import annotations

import json

from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from .control_plane import HerdrControlPlane


API_VERSION = 1
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765

UI_ROOT = Path(__file__).with_name("ui")


class MissionControlHTTPServer(ThreadingHTTPServer):
    """Threaded local server carrying Mission Control dependencies."""

    daemon_threads = True

    def __init__(
        self,
        server_address,
        handler_class,
        *,
        control_plane: HerdrControlPlane,
        default_repo: str | Path | None,
    ):
        super().__init__(server_address, handler_class)
        self.control_plane = control_plane
        self.default_repo = (
            Path(default_repo).expanduser().resolve()
            if default_repo is not None
            else None
        )


class MissionControlHandler(BaseHTTPRequestHandler):
    """Read-only Mission Control API handler."""

    server: MissionControlHTTPServer

    def log_message(self, format, *args):
        return

    def _write_json(
        self,
        status: HTTPStatus | int,
        payload: Any,
    ) -> None:
        body = (
            json.dumps(payload, sort_keys=True)
            .encode("utf-8")
        )

        self.send_response(int(status))
        self.send_header(
            "Content-Type",
            "application/json; charset=utf-8",
        )
        self.send_header(
            "Content-Length",
            str(len(body)),
        )
        self.send_header(
            "Cache-Control",
            "no-store",
        )
        self.end_headers()
        self.wfile.write(body)

    def _error(
        self,
        status: HTTPStatus | int,
        code: str,
        message: str,
    ) -> None:
        self._write_json(
            status,
            {
                "error": {
                    "code": code,
                    "message": message,
                }
            },
        )

    def _resolve_repo(
        self,
        query: dict[str, list[str]],
    ) -> Path | None:
        values = query.get("repo") or []

        if values and values[0].strip():
            return Path(
                values[0]
            ).expanduser().resolve()

        return self.server.default_repo

    def _read_json_body(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0"))

        if length <= 0:
            return {}

        raw = self.rfile.read(length)

        try:
            payload = json.loads(raw.decode("utf-8"))
        except Exception:
            raise ValueError("Request body must be valid JSON.")

        if not isinstance(payload, dict):
            raise ValueError("Request body must be a JSON object.")

        return payload

    def do_POST(self) -> None:
        parsed = urlparse(self.path)

        try:
            if parsed.path == "/api/v1/task":
                payload = self._read_json_body()
                repo_value = payload.get("repo")

                repo = (
                    Path(repo_value).expanduser().resolve()
                    if isinstance(repo_value, str) and repo_value.strip()
                    else self.server.default_repo
                )

                if repo is None:
                    self._error(
                        HTTPStatus.BAD_REQUEST,
                        "repo_required",
                        "A repository path is required.",
                    )
                    return

                text = payload.get("text")

                if not isinstance(text, str) or not text.strip():
                    raise ValueError(
                        "text must be a non-empty string."
                    )

                result = self.server.control_plane.dispatch_task(
                    repo,
                    text.strip(),
                    rejection_drill=bool(
                        payload.get("rejection_drill", False)
                    ),
                    task_policy=payload.get("task_policy"),
                )

                self._write_json(
                    HTTPStatus.OK,
                    {
                        "task": result,
                    },
                )
                return

            self._error(
                HTTPStatus.NOT_FOUND,
                "not_found",
                "Mission Control endpoint not found.",
            )

        except ValueError as exc:
            self._error(
                HTTPStatus.BAD_REQUEST,
                "invalid_request",
                str(exc),
            )
        except Exception as exc:
            self._error(
                HTTPStatus.INTERNAL_SERVER_ERROR,
                "internal_error",
                str(exc),
            )

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        query = parse_qs(
            parsed.query,
            keep_blank_values=True,
        )

        try:
            if parsed.path in {"/", "/index.html"}:
                body = (UI_ROOT / "index.html").read_bytes()

                self.send_response(int(HTTPStatus.OK))
                self.send_header(
                    "Content-Type",
                    "text/html; charset=utf-8",
                )
                self.send_header(
                    "Content-Length",
                    str(len(body)),
                )
                self.send_header(
                    "Cache-Control",
                    "no-store",
                )
                self.end_headers()
                self.wfile.write(body)
                return

            if parsed.path == "/api/v1/health":
                self._write_json(
                    HTTPStatus.OK,
                    {
                        "status": "ok",
                        "api_version": API_VERSION,
                    },
                )
                return

            if parsed.path == "/api/v1/snapshot":
                repo = self._resolve_repo(query)

                if repo is None:
                    self._error(
                        HTTPStatus.BAD_REQUEST,
                        "repo_required",
                        "A repository path is required.",
                    )
                    return

                self._write_json(
                    HTTPStatus.OK,
                    self.server.control_plane.snapshot(
                        repo
                    ),
                )
                return

            if parsed.path == "/api/v1/events":
                repo = self._resolve_repo(query)

                if repo is None:
                    self._error(
                        HTTPStatus.BAD_REQUEST,
                        "repo_required",
                        "A repository path is required.",
                    )
                    return

                limit_values = (
                    query.get("limit")
                    or ["100"]
                )

                try:
                    limit = int(limit_values[0])
                except ValueError:
                    raise ValueError(
                        "limit must be a positive integer."
                    )

                self._write_json(
                    HTTPStatus.OK,
                    {
                        "events": (
                            self.server.control_plane.events(
                                repo,
                                limit=limit,
                            )
                        )
                    },
                )
                return

            self._error(
                HTTPStatus.NOT_FOUND,
                "not_found",
                "Mission Control endpoint not found.",
            )

        except ValueError as exc:
            self._error(
                HTTPStatus.BAD_REQUEST,
                "invalid_request",
                str(exc),
            )
        except Exception as exc:
            self._error(
                HTTPStatus.INTERNAL_SERVER_ERROR,
                "internal_error",
                str(exc),
            )


def create_server(
    repo: str | Path | None = None,
    *,
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    control_plane: HerdrControlPlane | None = None,
) -> MissionControlHTTPServer:
    """Create a local Mission Control HTTP server."""
    if host not in {
        "127.0.0.1",
        "localhost",
    }:
        raise ValueError(
            "Mission Control must bind to a loopback address."
        )

    if not isinstance(port, int) or not 0 <= port <= 65535:
        raise ValueError(
            "port must be an integer between 0 and 65535."
        )

    return MissionControlHTTPServer(
        (host, port),
        MissionControlHandler,
        control_plane=(
            control_plane
            or HerdrControlPlane()
        ),
        default_repo=repo,
    )


def serve(
    repo: str | Path | None = None,
    *,
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
) -> None:
    """Serve the Mission Control API until interrupted."""
    server = create_server(
        repo,
        host=host,
        port=port,
    )

    try:
        server.serve_forever()
    finally:
        server.server_close()
