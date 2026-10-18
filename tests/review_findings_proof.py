"""ISOLATED CANDIDATE PROOF of a reviewer-declared findings declaration (item 2).

A LOCAL TEST, never the canonical gate and never installed-producer acceptance:
it loads THIS checkout's ``herdctl.py`` writer in isolation, builds a NEW herd
root inside an EMPTY directory the caller names (it refuses any other), records
the given route there as the task's routing context, feeds the given capture to
the real ``review-decision`` path, and reads the result back through the real
reader (``target_runtime.review_record.canonical_review``). Nothing outside
that directory is written; no production ``.herd`` state, pane, session or
installed producer is touched.

Usage (from the repository root)::

    python3 tests/review_findings_proof.py --route ROUTE.json \\
        --capture CAPTURE.txt --out EMPTY_DIR

``ROUTE.json`` is the route ``herdctl review-findings-route`` printed for the
round; ``CAPTURE.txt`` is the supported idle capture of the routed reviewer.
It prints one JSON object labelled ``isolated_candidate_proof`` with the
decision, the findings outcome and, when recorded, the persisted findings.
"""

import argparse
import contextlib
import importlib.util
import io
import json
import os
import sys
import types
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
LABEL = ("LOCAL TEST: isolated candidate writer over a saved capture. Not the"
         " canonical review-decision gate, not installed-producer acceptance,"
         " and not approval: findings are model-authored metadata.")


def prove(route, capture, out):
    """The proof outcome for ``route`` (a dict) and ``capture`` (text), in the
    empty directory ``out``. Raises ``SystemExit`` on a refused setup."""
    sys.path.insert(0, str(REPO_ROOT))
    from target_runtime import review_record
    if not os.path.isdir(out) or os.listdir(out):
        raise SystemExit("--out must be an existing EMPTY directory")
    if type(route) is not dict or type(route.get("round")) is not int:
        raise SystemExit("the route must be the JSON object the gate printed")
    root = os.path.realpath(out)
    state = os.path.join(root, ".herd", "state")
    os.makedirs(state)
    task = {"version": 1, "id": route.get("task_id"), "status": "ACTIVE",
            "review_rounds": route["round"] - 1,
            "review_findings_route": route}
    with open(os.path.join(state, "task.json"), "w") as handle:
        json.dump(task, handle)
    spec = importlib.util.spec_from_file_location(
        "herdctl_isolated_proof", REPO_ROOT / "herdctl.py")
    herdctl = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(herdctl)
    herdctl.resolve_repo_ref = lambda ref: Path(root)
    herdctl.resolve_role = lambda repo, name: route.get("reviewer_agent")
    herdctl.reviewer_transcript = lambda agent, lines=500: capture
    printed = io.StringIO()
    with contextlib.redirect_stdout(printed):
        herdctl.review_decision_cmd(types.SimpleNamespace(
            repo=root, reviewer=route.get("reviewer_logical"), lines=500))
    decision = json.loads(printed.getvalue())
    with open(os.path.join(state, "task.json")) as handle:
        task = json.load(handle)
    block = review_record.canonical_review(root, task, task.get("id"))
    findings = block.get("findings") or {}
    return {
        "isolated_candidate_proof": LABEL,
        "store": root,
        "decision": decision.get("decision"),
        "findings_outcome": decision.get("findings"),
        "reader": {"review_state": block.get("state"),
                   "verdict": block.get("verdict"),
                   "findings_state": findings.get("state"),
                   "findings_problem": findings.get("problem"),
                   "findings": findings.get("items", [])},
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--route", required=True)
    parser.add_argument("--capture", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args(argv)
    with open(args.route) as handle:
        route = json.load(handle)
    with open(args.capture, encoding="utf-8") as handle:
        capture = handle.read()
    print(json.dumps(prove(route, capture, args.out), indent=2))


if __name__ == "__main__":
    main()
