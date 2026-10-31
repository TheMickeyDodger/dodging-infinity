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


def collect(repo_root: str | Path, now=None, expect_task_id=None) -> dict:
    """Read the task state and the review artifacts under ``repo_root`` (UNBOUNDED reads that follow symlinks after the initial resolve: this
    is the human ceremony's own local collector) and validate them with ``evaluate``."""
    root = Path(repo_root).resolve()
    task_path = root / TASK_STATE_RELATIVE
    try:
        task_bytes = task_path.read_bytes()
    except OSError as exc:
        raise EvidenceError(f"task state unreadable at {task_path}: {exc}")
    reviews_dir = root / REVIEWS_RELATIVE
    return evaluate(
        task_bytes,
        task_path,
        lambda: sorted(p.name for p in reviews_dir.iterdir()),
        lambda name: (reviews_dir / name).read_bytes(),
        now=now,
        expect_task_id=expect_task_id,
    )


def evaluate(task_bytes, task_label, list_review_names, read_review, now=None, expect_task_id=None) -> dict:
    """The VALIDATION of the evidence, over what a caller has already read: the ONE rule for "the current task's COMPLETE plus the latest
    canonical APPROVE, with artifact digests", shared by ``collect`` and by any caller that must read under its OWN bounds.

    ``task_bytes``: the task record's bytes. ``task_label``: how to name it in a refusal. ``list_review_names()``: the names under the reviews
    directory; ``read_review(name)``: the bytes of one review artifact. Either raises ``OSError`` when it cannot read; that is reported as
    this module's own refusal. This function opens nothing and starts nothing; every bound, every symlink rule and every containment rule is
    the CALLER's, because it is the caller that reads.

    ``expect_task_id``: the task the caller PREPARED this for. When given, a record whose current task is any other is REFUSED, not collected: a
    route prepared for task A and run later, after task B became current and complete, must not seal B's identity into a record the person
    believes is A's. The check happens here, where the evidence is made, so it holds at execution time."""
    try:
        task = json.loads(task_bytes)
    except ValueError as exc:
        raise EvidenceError(f"task state unreadable at {task_label}: {exc}")
    if not isinstance(task, dict):
        raise EvidenceError("task state is not an object")
    task_id = task.get("id")
    if not isinstance(task_id, str) or not task_id:
        raise EvidenceError("task state carries no task id")
    if expect_task_id is not None and task_id != expect_task_id:
        raise EvidenceError(
            f"the current task is {task_id!r}, not the expected {expect_task_id!r};"
            " refusing to collect evidence for a different task"
        )
    if task.get("status") != "COMPLETE":
        raise EvidenceError(
            f"task {task_id} status is {task.get('status')!r}, not COMPLETE"
        )
    if task.get("last_review_decision") != "APPROVE":
        raise EvidenceError(
            f"task {task_id} last review decision is"
            f" {task.get('last_review_decision')!r}, not APPROVE"
        )
    try:
        names = list_review_names()
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
    try:
        review_bytes = read_review(name)
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
