#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
import threading
import webbrowser
from pathlib import Path

from herdr.server import DEFAULT_PORT, create_server


def run_web(repo: Path, port: int, no_open: bool) -> int:
    server = create_server(repo, port=port)
    host, bound_port = server.server_address[:2]
    url = f"http://{host}:{bound_port}/"

    print()
    print("=== DODGING INFINITY MISSION CONTROL ===")
    print(url)
    print("Press Ctrl+C to stop.")

    if not no_open:
        threading.Timer(
            0.8,
            lambda: webbrowser.open(url),
        ).start()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nMission Control stopped.")
    finally:
        server.server_close()

    return 0


def run_mission_control(repo: Path) -> int:
    try:
        from herdr.tui import run_tui
    except ImportError as exc:
        print(
            "Mission Control TUI could not be loaded\n"
            f"for this interpreter:\n  {sys.executable}\n"
            f"({exc})\n"
            "The web dashboard is still available with: infinity web",
            file=sys.stderr,
        )
        return 1
    return run_tui(repo)


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="infinity",
        description="Dodging Infinity Mission Control.",
    )
    parser.add_argument(
        "mode",
        nargs="?",
        choices=("mission-control", "web"),
        default="mission-control",
    )
    parser.add_argument("--repo", default=".")
    parser.add_argument(
        "--port",
        type=int,
        default=DEFAULT_PORT,
    )
    parser.add_argument(
        "--no-open",
        action="store_true",
    )
    args = parser.parse_args()

    repo = Path(args.repo).expanduser().resolve()

    if args.mode == "web":
        return run_web(
            repo,
            args.port,
            args.no_open,
        )

    return run_mission_control(repo)


if __name__ == "__main__":
    raise SystemExit(main())
