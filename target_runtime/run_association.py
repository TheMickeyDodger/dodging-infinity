"""The two PURE helpers that say which run a workspace's task record belongs to.

``handoff_prefix`` is the first sentence of an authorized Mission's handoff.
``names_this_run`` is the durable proof that a workspace's CURRENT task record
(as an observation, or a hardened read, projected it) begins with this run's
exact prefix and started no earlier than the intent was recorded.

They live here, unchanged from ``target_runtime.mission_bridge`` (which
re-exports both under the same names, so every caller and test is the same
function), because the read-only live code review shares the predicate and may
not import the bridge or anything that imports the control stack.

THIS MODULE IMPORTS NOTHING: no I/O, no environment, no subprocess, no clock,
no mutation. A structural test pins that, and the two exports.

What this is NOT. ``names_this_run`` does not compare task ids; a caller
compares the workspace's task id to the verified receipt id itself. And it is
ONE predicate over TWO readers (the bridge's observation, the review route's
hardened state read), not one shared observation: the readers are meant to
differ, because the hardened one refuses more. That is the accepted design and
its limitation.
"""

__all__ = ("handoff_prefix", "names_this_run")


def handoff_prefix(mission_id, revision, digest):
    """The handoff's first sentence: it names the exact Mission, revision
    and proposal digest. The child Herdr records the handoff as its own
    ``task.json`` description, and the observer projects that field
    truncated to 200 characters; this prefix is 150, so the target's OWN
    durable task record names the intent that started it."""
    return "AUTHORIZED MISSION %s (revision %d, proposal digest %s)." % (
        mission_id, revision, digest)


def names_this_run(raw_observation, mission_id, intent):
    """Durable proof that the workspace's CURRENT task is the one THIS
    run's intent started: the target's own ``task.json`` description (as
    observed) begins with this intent's exact handoff prefix (Mission id,
    revision, proposal digest), and the task started no earlier than the
    intent was recorded. An old or foreign child sharing the workspace
    fails both or either, and is never adopted."""
    task = raw_observation.get("task") if isinstance(
        raw_observation, dict) and isinstance(raw_observation.get("task"),
                                              dict) else {}
    description, started = task.get("description"), task.get("started_at")
    return bool(
        isinstance(description, str)
        and description.startswith(handoff_prefix(
            mission_id, intent["revision"], intent["proposal_digest_sha256"]))
        and isinstance(started, int) and not isinstance(started, bool)
        and started >= intent["recorded_at"])
