"""Recovery of a legacy Mission-Core VERIFIED run's result: the ONE
implementation.

Moved here, unchanged in its checks, from ``MissionBridge.result`` (I3 of
task 20261010-164104-1761f6), which now delegates to it, so the local
operator surface (``direquest.py result``) and Grok Bot's read-only
``result`` run exactly the same code: the accepted evidence, its two
recorded artifacts, the locator shape restriction, the hardened reader
(``target_runtime.state_artifact``), the re-hash against each recorded
digest, and the UNRECOVERABLE outcome when content is gone or changed (a
digest alone is never a verified result).

READ-ONLY: it reads Mission Core's durable records and the two recorded
locators, and nothing else; it records, starts, advances and consumes
nothing. Its import closure excludes the dispatch path (no
``target_runtime.dispatch``, ``mission_bridge``, ``broker`` or ``evidence``,
no ``herdr``), pinned by test, so a transport can reach it without reaching
anything that can start work.
"""

from local_request import surface as surface_module
from target_runtime.state_artifact import read_state_artifact
from workflow_authority.digest import json_digest

DELIVERY_AUTHORITY = "none"
RESULT_ARTIFACT_KEY = "run_result_artifact"
REVIEW_ARTIFACT_KEY = "run_review_artifact"
# The two durable target artifacts a run result is bound to, as
# workspace-relative locators the hardened primitive can re-read.
STATE_DIRS = (".herd", "state")
REVIEW_DIRS = (".herd", "state", "reviews")


def result_binding(mission_id, task_id, result_name, result_digest,
                   review_name, review_digest):
    """The exact content of a run's result evidence."""
    return {
        "mission_id": mission_id, "task_id": task_id,
        "result_locator": "/".join(STATE_DIRS + (result_name,)),
        "result_digest_sha256": result_digest,
        "review_locator": "/".join(REVIEW_DIRS + (review_name,)),
        "review_digest_sha256": review_digest,
    }


def unrecoverable(mission_id, reason):
    return {"mission_id": mission_id, "verified_result": False,
            "recoverable": False, "reason": reason}


def recover_result(missions, mission_id, mission=None):
    """Recover a VERIFIED run's result from durable records alone (no
    caller context): the accepted evidence, its two artifacts, and the
    content re-read and re-hashed from the recorded locators. A digest
    whose content is gone or changed is UNRECOVERABLE, never a verified
    result.

    ``missions`` is Mission Core's service (only its reads are used);
    ``mission`` is the Mission record when the caller already read it
    (``MissionBridge`` reads it through its own refusal wrapper), else it
    is read here."""
    if mission is None:
        mission = missions.get(mission_id)["record"]
    run = mission.get("run")
    verification = run["verification"] if run else None
    if not verification or not verification["verified"]:
        return {"mission_id": mission_id, "verified_result": False,
                "recoverable": False, "reason": "no VERIFIED run"}
    state = missions.get_state(mission_id)["record"]
    evidence = next(e for e in state["evidence"]
                    if e["evidence_id"] == verification["result_evidence_id"])
    artifacts = dict((a["artifact_id"], a) for a in state["artifacts"])
    linked = [artifacts[i] for i in evidence["artifact_ids"]]
    by_key = dict((a["key"], a) for a in linked)
    workspace = run["intent"]["workspace_realpath"]
    contents = {}
    for key in (RESULT_ARTIFACT_KEY, REVIEW_ARTIFACT_KEY):
        artifact = by_key.get(key)
        parts = tuple(artifact["locator"].split("/")) if artifact else ()
        if artifact is None or parts[:-1] not in (STATE_DIRS, REVIEW_DIRS):
            return unrecoverable(mission_id, "%s is not recorded" % key)
        status, _, digest, text = read_state_artifact(
            workspace, parts[:-1], parts[-1])
        if digest != artifact["content_digest_sha256"] or text is None:
            return unrecoverable(
                mission_id, "%s content is gone or changed (%s); a digest"
                " alone is never a verified result" % (key, status))
        contents[key] = (artifact, text)
    result_artifact, result_text = contents[RESULT_ARTIFACT_KEY]
    review_artifact, review_text = contents[REVIEW_ARTIFACT_KEY]
    binding = result_binding(
        mission_id, run["receipt"]["task_id"],
        result_artifact["locator"].split("/")[-1],
        result_artifact["content_digest_sha256"],
        review_artifact["locator"].split("/")[-1],
        review_artifact["content_digest_sha256"])
    if json_digest(binding) != evidence["content_digest_sha256"] or (
        evidence["acceptance"] is None
    ):
        return unrecoverable(mission_id, "the recorded binding does"
                             " not match its accepted evidence")
    return {
        "mission_id": mission_id, "verified_result": True,
        "recoverable": True, "task_id": run["receipt"]["task_id"],
        "evidence_id": evidence["evidence_id"], "binding": binding,
        "result_text": result_text, "review_text": review_text,
        "review_evidence": "target-produced, not independent verification",
        "delivered": surface_module.run_status(
            mission, missions.get_state(mission_id))["delivery"][
                "delivered"],
        "delivery_authority": DELIVERY_AUTHORITY,
    }
