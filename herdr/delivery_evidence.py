"""Read-only collection of the Herdr evidence a PR delivery is bound to.

The delivery package (``pr_delivery``) never reads ``.herd/``; it consumes
recorded evidence references. This module is the ONE reader: it takes the
task state and the canonical review artifact ``herdctl review-decision``
persisted under ``.herd/state/reviews/`` and emits a structured document
with the artifact digest. It grants nothing and writes nothing; the
human carries the document into the authorization ceremony, which binds
it to the exact live candidate identity.
"""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path

from .observe import _decision_token
from .vintage import ROUND_FILE_RE, latest_round_for_task

TASK_STATE_RELATIVE = Path(".herd") / "state" / "task.json"
REVIEWS_RELATIVE = Path(".herd") / "state" / "reviews"


class EvidenceError(ValueError):
    """The evidence cannot be collected exactly; message actionable."""


def collect(repo_root: str | Path, now=None) -> dict:
    root = Path(repo_root).resolve()
    task_path = root / TASK_STATE_RELATIVE
    try:
        task_bytes = task_path.read_bytes()
        task = json.loads(task_bytes)
    except (OSError, ValueError) as exc:
        raise EvidenceError(f"task state unreadable at {task_path}: {exc}")
    if not isinstance(task, dict):
        raise EvidenceError("task state is not an object")
    task_id = task.get("id")
    if not isinstance(task_id, str) or not task_id:
        raise EvidenceError("task state carries no task id")
    if task.get("status") != "COMPLETE":
        raise EvidenceError(
            f"task {task_id} status is {task.get('status')!r}, not COMPLETE"
        )
    if task.get("last_review_decision") != "APPROVE":
        raise EvidenceError(
            f"task {task_id} last review decision is"
            f" {task.get('last_review_decision')!r}, not APPROVE"
        )
    reviews_dir = root / REVIEWS_RELATIVE
    try:
        names = sorted(p.name for p in reviews_dir.iterdir())
    except OSError as exc:
        raise EvidenceError(f"reviews directory unreadable: {exc}")
    latest = latest_round_for_task(names, task_id)
    if latest is None:
        raise EvidenceError(f"no review round recorded for task {task_id}")
    round_number, name = latest
    recorded_file = task.get("last_review_file")
    if recorded_file and Path(recorded_file).name != name:
        raise EvidenceError(
            f"task state names review file {recorded_file!r} but the latest"
            f" round on disk is {name!r}; ambiguous"
        )
    review_path = reviews_dir / name
    try:
        review_bytes = review_path.read_bytes()
    except OSError as exc:
        raise EvidenceError(f"review artifact unreadable: {exc}")
    # ONE parser for the canonical token (round-01 N2): the same
    # `_decision_token` the observation projection uses.
    token = _decision_token(review_bytes.decode("utf-8", "replace"))
    if token != "APPROVE":
        raise EvidenceError(
            f"review artifact {name} records protocol token {token!r}, not"
            " APPROVE"
        )
    if not ROUND_FILE_RE.fullmatch(name):
        raise EvidenceError(f"review artifact name {name!r} is not a round")
    recorded_at = time.time() if now is None else now
    return {
        "engineering_complete": {
            "task_id": task_id,
            "status": "COMPLETE",
            "task_state_sha256": hashlib.sha256(task_bytes).hexdigest(),
            "recorded_at": recorded_at,
        },
        "reviewer_approve": {
            "task_id": task_id,
            "round": round_number,
            "review_file_name": name,
            "review_file_sha256": hashlib.sha256(review_bytes).hexdigest(),
            "decision": "APPROVE",
            "recorded_at": recorded_at,
        },
    }
