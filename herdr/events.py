"""Append-only Mission Control event journal."""

from __future__ import annotations

import json
import time
import uuid

from typing import Any

from .instance import HerdrInstance


EVENT_SCHEMA_VERSION = 1
EVENTS_FILE = "events.jsonl"


def event_path(herd: HerdrInstance):
    return herd.herd_root / "state" / EVENTS_FILE


def append_event(
    herd: HerdrInstance,
    event_type: str,
    *,
    actor: str = "control-plane",
    data: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Append one structured event to a Herdr's durable journal."""
    event_type = event_type.strip()
    actor = actor.strip()

    if not event_type:
        raise ValueError("Event type cannot be empty.")

    if not actor:
        raise ValueError("Event actor cannot be empty.")

    if data is not None and not isinstance(data, dict):
        raise ValueError("Event data must be an object.")

    event = {
        "schema_version": EVENT_SCHEMA_VERSION,
        "id": uuid.uuid4().hex,
        "timestamp_ms": time.time_ns() // 1_000_000,
        "repo": str(herd.repo),
        "type": event_type,
        "actor": actor,
        "data": dict(data or {}),
    }

    path = event_path(herd)
    path.parent.mkdir(parents=True, exist_ok=True)

    with path.open("a", encoding="utf-8") as handle:
        handle.write(
            json.dumps(event, sort_keys=True)
            + "\n"
        )

    return event


def read_events(
    herd: HerdrInstance,
    *,
    limit: int = 100,
) -> list[dict[str, Any]]:
    """Read the newest durable events in chronological order."""
    if not isinstance(limit, int) or limit < 1:
        raise ValueError("limit must be a positive integer.")

    path = event_path(herd)

    if not path.exists():
        return []

    events = []

    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue

        try:
            event = json.loads(line)
        except Exception:
            continue

        if isinstance(event, dict):
            events.append(event)

    return events[-limit:]
