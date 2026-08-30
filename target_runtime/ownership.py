"""I5: attributable ownership, and cleanup that touches only what it owns.

Why a predicate module rather than a few checks at the call site
================================================================

There are ~40 live agents on this machine belonging to other herds, and
a cleanup path that guesses wrong destroys someone else's work. So the
question "does this workflow own that resource?" is answered in exactly
one place, from durable recorded evidence, and every cleanup route is
built on it.

WHAT IS NOT EVIDENCE HERE
=========================

**Within this module a name, prefix, alias, or heuristic carries no
ownership weight.**

This is stated first because it is the ATTRACTIVE WRONG ANSWER. The
orphaned agents this increment was pointed at are called
``h566a1-wf-7200299-…``, and ``dispatch.ALIAS_PREFIX`` mints an alias
from the workflow id, so a name-prefix match would look like a working
predicate and would pass a hand-written test. The architecture record already rules the alias a derived label, and
within this machine's population a prefix rule would match anything
a user happened to name similarly.

So `owns_*` below reads exactly three things: the recorded lease
realpath, the recorded task id, and the workflow id used to DERIVE the
lease path. A displayed name is outside that set.
`alias_is_not_evidence` exists so the rule has a callable form a test
can drive, and `tests/test_ownership.py` drives a
resource that matches by alias alone and asserts it is NOT owned.

FAIL CLOSED, AND WHAT THAT COSTS
================================

Within this module, a resource whose ownership is not PROVEN is left
untouched and the cleanup reports itself DEGRADED. More
BLOCKED outcomes are the accepted cost. A cleanup that reported success while leaving
unprovable resources behind would be the recorded "silent truncation
presented as fact" class in its most expensive form, so `CleanupReport`
counts only what it actually removed and carries the unprovable ones by
name.

STALE VERSUS CURRENT
====================

A status that is true but STALE reads exactly like one that is true and
CURRENT, unless something monotonic is checked alongside it. The live
agent vocabulary carries two monotonic counters, ``revision`` and
``state_change_seq``; `observation_is_current` requires BOTH to have
advanced against a prior observation. Outside that: an agent that has
been idle since the prior observation reads here the same as one whose
reporting has stopped, and separating those is beyond what this module
attempts — it reports "not current",
which is the fail-closed direction.
"""

import os

from target_runtime import dispatch as dispatch_module
from target_runtime import workspace_trust as trust_module
from target_runtime.workspace import lease_path

#: Ownership verdicts.
OWNED = "owned"
NOT_OWNED = "not_owned"
UNPROVABLE = "unprovable"

VERDICTS = (OWNED, NOT_OWNED, UNPROVABLE)

#: Cleanup problem codes.
PROBLEM_CLEANUP_DEGRADED = "cleanup_degraded_unprovable_resources"


def alias_is_not_evidence(alias):
    """Returns False for every input, and it is a function so the
    rule can be DRIVEN.

    Within this module an alias, or a displayed name, carries zero
    ownership weight. A test that hands this an alias matching the
    workflow exactly still gets False, so the rule is executable rather
    than a comment someone can quietly stop honouring.
    """
    return False


def recorded_lease_realpath(entry):
    """This workflow's own lease realpath from the DURABLE record, or
    None when the record does not carry one.

    None rather than a derived fallback: within this function, deriving
    a path for a workflow that recorded no lease would manufacture
    ownership of a directory it may not have created.
    """
    lease = entry.get("workspace_lease")
    if not isinstance(lease, dict):
        return None
    recorded = lease.get("path_realpath")
    if not isinstance(recorded, str) or not recorded:
        return None
    return os.path.realpath(recorded)


def recorded_task_id(entry):
    """The durably bound target task id, or None.

    The unresolved sentinel is reported as None: within this scheme a workflow whose identity was not established has
    no task id to own by,
    and treating the sentinel as an id would make every unresolved
    workflow claim every other unresolved one's resources.
    """
    engine = entry.get("target_engine")
    if not isinstance(engine, dict):
        return None
    task_id = engine.get("task_id")
    if not isinstance(task_id, str) or not task_id:
        return None
    if task_id == dispatch_module.UNRESOLVED_TASK_ID:
        return None
    return task_id


def owns_workspace(entry, path, workspaces_root):
    """Whether this workflow owns the workspace directory at ``path``.

    Three conditions, all required, and each one closes a different
    way of being wrong: the path resolves EQUAL to the lease realpath
    the record itself carries; that recorded path is EQUAL to the
    lease path DERIVED from the workflow id under the managed root, so
    within this check a record naming someone else's directory does
    not authorise touching it; and the path sits strictly inside the
    managed root.

    Returns one of `VERDICTS`. UNPROVABLE — not NOT_OWNED — when the
    record carries no lease, because "we cannot tell" and "it is
    someone else's" are different things to a cleanup that must report
    degradation truthfully.
    """
    recorded = recorded_lease_realpath(entry)
    if recorded is None:
        return UNPROVABLE
    workflow_id = entry.get("workflow_id")
    if not isinstance(workflow_id, str) or not workflow_id:
        return UNPROVABLE
    real_root = os.path.realpath(workspaces_root)
    derived = os.path.realpath(lease_path(workspaces_root, workflow_id))
    if recorded != derived:
        return NOT_OWNED
    if not _within(recorded, real_root):
        return NOT_OWNED
    if os.path.realpath(path) != recorded:
        return NOT_OWNED
    return OWNED


def owns_trust_entry(entry, key, workspaces_root):
    """Whether this workflow owns the `projects` key ``key``.

    The key is compared against `workspace_trust.trust_key` of this
    workflow's OWN lease path, computed through that module rather than
    retyped here, so the NFC normalization and the realpath rule stay
    in one place. SC-1 rides along with it: the trust key resolves to
    the enclosing git root, and a materialized workspace is its own git
    root.
    """
    recorded = recorded_lease_realpath(entry)
    if recorded is None:
        return UNPROVABLE
    if owns_workspace(entry, recorded, workspaces_root) != OWNED:
        return NOT_OWNED
    return OWNED if key == trust_module.trust_key(recorded) else NOT_OWNED


def owns_child_record(entry, record, workspaces_root):
    """Whether a control-side spawn record names a resource this
    workflow owns.

    Requires BOTH: the record's ``repo`` resolves equal to this
    workflow's owned lease path, AND its ``task_id`` equals the
    durably bound target task id. One alone is not enough: within this
    predicate a repo match alone would claim a record written by a
    different dispatch into a reused path, and a task-id match alone would claim a record naming a
    directory outside this workflow's own lease.

    UNPROVABLE when this workflow has no bound task id, which is the
    crashed-before-identity case: within that branch the resource is
    left untouched and the caller reports degradation.
    """
    if not isinstance(record, dict):
        return UNPROVABLE
    task_id = recorded_task_id(entry)
    if task_id is None:
        return UNPROVABLE
    repo = record.get("repo")
    if not isinstance(repo, str) or not repo:
        return UNPROVABLE
    if owns_workspace(entry, repo, workspaces_root) != OWNED:
        return NOT_OWNED
    recorded_id = record.get("task_id")
    if not isinstance(recorded_id, str) or not recorded_id:
        return UNPROVABLE
    return OWNED if recorded_id == task_id else NOT_OWNED


def observation_is_current(prior, current):
    """Whether ``current`` is a FRESH observation relative to ``prior``.

    Both monotonic counters must have advanced. One alone is not
    enough: `state_change_seq` can advance without a new turn, and
    `revision` alone was the signal that reported "still finished from
    last time" as though it meant "finished this round".

    Returns False when either observation is missing a counter, which
    is the fail-closed direction — an unreadable counter is treated as
    "not proven current" rather than as "current".
    """
    prior_pair = _counters(prior)
    current_pair = _counters(current)
    if prior_pair is None or current_pair is None:
        return False
    return (
        current_pair[0] > prior_pair[0]
        and current_pair[1] > prior_pair[1]
    )


def _counters(observation):
    if not isinstance(observation, dict):
        return None
    revision = observation.get("revision")
    sequence = observation.get("state_change_seq")
    for value in (revision, sequence):
        if isinstance(value, bool) or not isinstance(value, int):
            return None
    return (revision, sequence)


def _within(child_realpath, parent_realpath):
    return child_realpath.startswith(parent_realpath + os.sep)


class CleanupReport(object):
    """What a cleanup actually did — counted, not claimed.

    `removed` holds only resources whose ownership was PROVEN and whose
    removal succeeded. `unprovable` and `failed` hold the rest, by
    name, and either makes the report DEGRADED. `degraded` is derived from those lists rather than set by a caller,
    so within this class reporting success over a non-empty remainder is
    unrepresentable.
    """

    def __init__(self):
        self.removed = []
        self.skipped_not_owned = []
        self.unprovable = []
        self.failed = []

    @property
    def degraded(self):
        return bool(self.unprovable) or bool(self.failed)

    def record(self, kind, name, verdict, ok=True, detail=None):
        if verdict == UNPROVABLE:
            self.unprovable.append((kind, name, detail))
        elif verdict == NOT_OWNED:
            self.skipped_not_owned.append((kind, name))
        elif not ok:
            self.failed.append((kind, name, detail))
        else:
            self.removed.append((kind, name))

    def summary(self):
        """A bounded line for a durable receipt.

        It names the degraded state FIRST when degraded, because a
        reader scanning summaries must not have to reach the end of the
        line to learn that the cleanup was incomplete.
        """
        head = "cleanup DEGRADED" if self.degraded else "cleanup complete"
        return (
            "%s: removed %d, skipped %d not owned, %d unprovable,"
            " %d failed" % (
                head, len(self.removed), len(self.skipped_not_owned),
                len(self.unprovable), len(self.failed),
            )
        )
