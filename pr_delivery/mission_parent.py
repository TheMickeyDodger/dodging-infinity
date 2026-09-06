"""P1-A6 parent-authority seam: does a delivery record's optional Mission
parent name a Mission Authorization that permits ``github_pr``?

Dependency direction. AUTHORITY flows Mission -> PR Delivery; IMPORTS
flow consumer -> core. This module imports the neutral Mission Core;
the Mission Core never imports ``pr_delivery`` (pinned). Nothing else in
this package reaches the Mission Core, and nothing in this package calls
this seam yet: it is the production consumption point a later delivery
transaction will call before deriving bounded delivery authorization,
and in this bundle it is wired into no step the delivery machine
executes. It is read-only and effect-free: it performs no Git action and
mutates no state in either store.

What it reads. Only the delivery record's EXISTING optional ``mission``
block ``{workflow_id, mission_authorization_digest_sha256}`` — no schema
change, so the legacy/manual standalone shape (``mission: null``) is
preserved exactly and an existing on-disk record's authority digest is
untouched. The revision, manifest digest, scope and targets are read
back from the authoritative Mission record rather than duplicated here,
because duplicates drift.

What it checks. The Mission Core resolves the authorization by digest,
requires the authorization's ``mission_id`` to equal ``workflow_id`` (a
disagreement refuses), and asks the ONE central Mission validator
whether that authorization permits the ``github_pr`` delivery target for
that Mission at the authorization's bound revision. The target is fixed
here, not a parameter: this seam exists for exactly one delivery target
and offers no way to skip or redirect the check. Nothing here
reimplements a check. A legacy record whose ``workflow_id`` is a
DI-REMOTE workflow id simply does not resolve and the check refuses —
correct, because it was never a Mission Core authorization.

What it returns. A reviewable projection naming, explicitly, the
authorization id, Mission id, Mission revision, manifest (proposal)
digest, authorized action scope and authorized delivery targets, plus
``valid`` / ``problem`` / ``detail``, the ``workflow_id`` read from the
record and the ``delivery_target`` asked about. A valid projection means
the PARENT Mission scope permits a later delivery to be sought; it is
not commit, push, PR creation, or merge permission by itself.
"""

from mission import record as mission_record

PROBLEM_PARENT_ABSENT = "pr_delivery_mission_parent_absent"

PROJECTION_KEYS = (
    "valid", "problem", "detail", "authorization_id", "mission_id",
    "revision", "proposal_digest_sha256", "authorized_action_scope",
    "authorized_delivery_targets", "workflow_id", "delivery_target",
)


def _projection(check_dict, workflow_id, delivery_target):
    projection = dict(check_dict)
    projection["workflow_id"] = workflow_id
    projection["delivery_target"] = delivery_target
    assert tuple(sorted(projection)) == tuple(sorted(PROJECTION_KEYS))
    return projection


def parent_mission_authority(delivery_document, mission_service):
    """Validate the delivery record's Mission parent through the Mission
    Core's one validation path, always for the ``github_pr`` delivery
    target: the target is not a parameter, so no caller can disable or
    redirect the check. Read-only; never raises for a malformed or absent
    parent, it refuses with a problem code instead."""
    delivery_target = mission_record.DELIVERY_TARGET_GITHUB_PR
    mission = None
    if isinstance(delivery_document, dict):
        mission = delivery_document.get("mission")
    if not isinstance(mission, dict):
        return _projection({
            "valid": False,
            "problem": PROBLEM_PARENT_ABSENT,
            "detail": ("the delivery record carries no Mission parent (the"
                       " legacy/manual standalone shape); no parent Mission"
                       " authority can be confirmed"),
            "authorization_id": None, "mission_id": None, "revision": None,
            "proposal_digest_sha256": None, "authorized_action_scope": None,
            "authorized_delivery_targets": None,
        }, None, delivery_target)
    workflow_id = mission.get("workflow_id")
    digest = mission.get("mission_authorization_digest_sha256")
    check = mission_service.check_parent_authority(
        workflow_id, digest, delivery_target
    )
    return _projection(check.as_dict(), workflow_id, delivery_target)
