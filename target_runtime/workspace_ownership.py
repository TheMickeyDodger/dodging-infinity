"""DOMAIN B (R-29): Herdr WORKSPACES and their long-lived agent sessions.

Two ownership domains, and why this module exists
=================================================

**Domain A** is local helper processes DI itself starts — pgids,
`Popen`, reaping, the freeze. `target_runtime/process_ownership.py`
governs that, and it stays, scoped.

**Domain B** is what a completed workflow actually leaves behind: a
Herdr WORKSPACE and the long-lived Claude agent sessions inside it. The
sessions belong to the WORKSPACE, not to the short-lived CLI process
that asked for the workspace to be created — that process exits at
once while the sessions persist. Owning the requesting process therefore leaves whatever outlives the
request unowned.

I5's objective names Domain B directly: *workflow → herd task → logical
role → transient agent/session → workspace → pane*. Every link past
"logical role" is Domain B, and terminal workflow cleanup is a Domain B
operation. An ownership module that reaps only subprocesses would still
not perform it, however well it were wired.

WHY THIS IS THE MOST DANGEROUS CODE IN THE INCREMENT
====================================================

There are FIFTEEN workspaces on the machine this was written against
and exactly ONE is ours. Closing the wrong one destroys other people's
live sessions, and unlike a leaked sleeper that is unrecoverable. So this module is built to REFUSE: on anything less than an exact,
unique chain its posture is to act on no workspace and say why.

Two structural choices carry that:

1. `close_fn` is a REQUIRED parameter with no default. There is no
   value it can take by omission, so no caller — and no test — can
   reach a real workspace close by forgetting an argument. The
   dangerous capability has to be handed in deliberately, every time.
2. The proof is computed BEFORE a close is attempted, and the close is
reached only through the single `OWNED` verdict.

THE EVIDENCE CHAIN, AND WHAT "EXACT AND UNIQUE" MEANS
=====================================================

Ownership of a workspace is proven only when every link agrees:

- the workflow record's own lease realpath and durably bound task id;
- EXACTLY ONE control-side child record whose ``repo`` resolves to that
  lease and whose ``task_id`` equals that task id;
- that child record naming a ``workspace_id``;
- EXACTLY ONE live workspace carrying that id;
- the live workspace's agent NAME SET matching the set of agent names
  the child record recorded, EXACTLY.

The comparison is over agent NAMES rather than logical roles, and that
is a fact about the dependency rather than a choice: `herdr workspace
list` reports `workspace_id`, `label` and pane counts and carries NO
agent mapping, so the live side is built by joining `herdr agent list`
on `workspace_id`, which yields names. Both sides are therefore
recorded identifiers — names written into `children.json` at spawn,
compared against names the live registry reports — and neither side is
a prefix, an alias or a resemblance.

Anything else — zero matches, several matches, a disagreement, or an
unreadable source — is refused. A workspace whose recorded agents no
longer exist does NOT match, and is refused rather than closed: that is
the shape of the recorded wV specimen, and refusing it is the intended
outcome, not a gap to close later.

Names are not evidence here either. The chain runs on recorded identifiers — lease realpath, task id,
workspace id, agent names taken from the RECORD and compared against the
live set — and a prefix, an alias or a resemblance sits outside what it
consults.
"""

import os

from target_runtime import ownership as ownership_module

#: Verdicts, reusing Domain A's vocabulary so a caller reads one set.
OWNED = ownership_module.OWNED
NOT_OWNED = ownership_module.NOT_OWNED
UNPROVABLE = ownership_module.UNPROVABLE

PROBLEM_NO_CHILD_RECORD = "workspace_no_matching_child_record"
PROBLEM_MULTIPLE_CHILD_RECORDS = "workspace_multiple_child_records"
PROBLEM_NO_WORKSPACE_ID = "workspace_child_record_has_no_workspace_id"
PROBLEM_WORKSPACE_NOT_FOUND = "workspace_id_not_live"
PROBLEM_MULTIPLE_WORKSPACES = "workspace_id_not_unique"
PROBLEM_AGENTS_DISAGREE = "workspace_agents_do_not_match_the_record"
PROBLEM_EVIDENCE_DEGRADED = "workspace_evidence_degraded"
PROBLEM_STALE_PROOF = "workspace_proof_no_longer_matches_live"


class WorkspaceCloseRefused(Exception):
    """Raised when a caller asks for a close the evidence forbids."""


class ProofSnapshot(object):
    """The ONE proven binding, and the only way to obtain an id.

    R-40 AD-1/AD-2. The previous shape returned
    ``(verdict, workspace_id, ...)``, and a caller bound the verdict
    to ``_verdict`` and used the id anyway — so preservation received
    an id derived from a proof that had FAILED, and the close then
    read live state a SECOND time and took its own id. Two reads are
    two facts, and only one of them was proven.

    A snapshot is built ONLY on an OWNED verdict, so an id from an
    UNPROVABLE or NOT_OWNED proof is unusable BY CONSTRUCTION rather
    than by convention: there is no object to take one from.

    It records what was proven — workspace id, task id, the exact
    agent name set and the lease — so `still_matches` can ask whether
    the world is still that world immediately before the destructive
    step.
    """

    __slots__ = ("workspace_id", "task_id", "agent_names", "lease")

    def __init__(self, workspace_id, task_id, agent_names, lease):
        object.__setattr__(self, "workspace_id", workspace_id)
        object.__setattr__(self, "task_id", task_id)
        object.__setattr__(self, "agent_names", frozenset(agent_names))
        object.__setattr__(self, "lease", lease)

    def __setattr__(self, name, value):
        raise AttributeError(
            "a proof snapshot is immutable; re-prove rather than"
            " editing what was proven"
        )

    def still_matches(self, live_workspaces, child_records=None,
                      entry=None, workspaces_root=None):
        """Whether the live world is STILL the world this proved.

        AD-4/AF-4: it revalidates the FULL binding, not a subset. The first version took only ``live_workspaces`` and could
        therefore check the workspace id and the agent set, and no more
        than those — so the task id, the lease and the child-record binding
        went unchecked at the exact moment of the destructive act. A
        revalidation that checks a subset certifies a subset.

        The extra inputs are optional ONLY so a caller that genuinely
        has no control-side reading can still check the live half; a
        caller that has them and omits them gets a weaker check, which
        is why `close_proven_workspace` passes all of them.

        A stale proof is not a proof: the same rule already governs
        pids and process groups, and here being wrong costs somebody
        else's live sessions.
        """
        if not isinstance(live_workspaces, list):
            return False
        matching = [
            workspace for workspace in live_workspaces
            if isinstance(workspace, dict)
            and workspace.get("workspace_id") == self.workspace_id
        ]
        if len(matching) != 1:
            return False
        observed = matching[0].get("agent_names")
        if not isinstance(observed, (set, frozenset, list, tuple)):
            return False
        if frozenset(observed) != self.agent_names:
            return False
        if entry is not None:
            # THE LEASE, re-read from the record at close time.
            if ownership_module.recorded_lease_realpath(entry) != (
                self.lease
            ):
                return False
            # THE TASK ID, likewise.
            if ownership_module.recorded_task_id(entry) != self.task_id:
                return False
        if child_records is not None and entry is not None \
                and workspaces_root is not None:
            # THE CHILD-RECORD BINDING: still exactly one, still
            # naming this workspace id.
            owned = [
                record for record in child_records
                if isinstance(record, dict)
                and ownership_module.owns_child_record(
                    entry, record, workspaces_root
                ) == OWNED
            ]
            if len(owned) != 1:
                return False
            if owned[0].get("workspace_id") != self.workspace_id:
                return False
            recorded = _recorded_agents(owned[0])
            if recorded is None or set(recorded.values()) != set(
                self.agent_names
            ):
                return False
        return True


def _recorded_agents(child_record):
    """``{logical: agent_name}`` from a child record, or None.

    None rather than an empty mapping when the field is absent or
    malformed: "the record does not say" and "the record says there
    are none" are different, and only the second could ever match a
    workspace with no agents.
    """
    agents = child_record.get("agents")
    if not isinstance(agents, dict) or not agents:
        return None
    pairs = {}
    for logical, name in agents.items():
        if not isinstance(logical, str) or not isinstance(name, str):
            return None
        if not logical or not name:
            return None
        pairs[logical] = name
    return pairs


def prove_ownership(entry, child_records, live_workspaces,
                    workspaces_root):
    """Whether this workflow owns exactly one live workspace.

    ``child_records`` is the control-side spawn-record listing;
    ``live_workspaces`` is a read-only projection of the machine's
    workspaces, each a mapping with ``workspace_id`` and
    ``agent_names`` — the KEY THE CODE BELOW ACTUALLY READS.

    That name is stated exactly because the docstring once said
    ``agents`` while the code read ``agent_names``: a producer written against the prose would have emitted the wrong
    key, each comparison would have found no agent set, and within that
    seam OWNED would have become unreachable, silently.
    `test_the_producer_and_consumer_agree_on_the_key` pins the two
    together, so within this pair prose that drifts from code fails.
    Both are passed in: within this module no live state is read, so a
    caller is not surprised by it consulting a source of its own.

    Returns ``(verdict, workspace_id, problem, detail)``.
    """
    task_id = ownership_module.recorded_task_id(entry)
    if task_id is None:
        return (UNPROVABLE, None, PROBLEM_EVIDENCE_DEGRADED,
                "the workflow has no durably bound target task id, so"
                " no workspace can be tied to it")
    if not isinstance(child_records, list):
        return (UNPROVABLE, None, PROBLEM_EVIDENCE_DEGRADED,
                "the control-side child listing is unreadable")
    if not isinstance(live_workspaces, list):
        return (UNPROVABLE, None, PROBLEM_EVIDENCE_DEGRADED,
                "the live workspace listing is unreadable")

    matching = [
        record for record in child_records
        if isinstance(record, dict)
        and ownership_module.owns_child_record(
            entry, record, workspaces_root
        ) == OWNED
    ]
    if not matching:
        return (NOT_OWNED, None, PROBLEM_NO_CHILD_RECORD,
                "no control-side child record names this workflow's"
                " lease AND its bound task id")
    if len(matching) > 1:
        return (NOT_OWNED, None, PROBLEM_MULTIPLE_CHILD_RECORDS,
                "%d child records match; EXACTLY ONE is required, so"
                " an ambiguous set closes nothing" % len(matching))

    record = matching[0]
    workspace_id = record.get("workspace_id")
    if not isinstance(workspace_id, str) or not workspace_id:
        return (UNPROVABLE, None, PROBLEM_NO_WORKSPACE_ID,
                "the matching child record names no workspace_id")

    live = [
        workspace for workspace in live_workspaces
        if isinstance(workspace, dict)
        and workspace.get("workspace_id") == workspace_id
    ]
    if not live:
        return (NOT_OWNED, None, PROBLEM_WORKSPACE_NOT_FOUND,
                "no live workspace carries id %r; there is nothing to"
                " close" % workspace_id)
    if len(live) > 1:
        return (NOT_OWNED, None, PROBLEM_MULTIPLE_WORKSPACES,
                "%d live workspaces carry id %r; an ambiguous set"
                " closes nothing" % (len(live), workspace_id))

    recorded = _recorded_agents(record)
    if recorded is None:
        return (UNPROVABLE, None, PROBLEM_AGENTS_DISAGREE,
                "the child record carries no usable agent mapping, so"
                " the live workspace cannot be matched against it")
    observed = live[0].get("agent_names")
    if not isinstance(observed, (set, frozenset, list, tuple)):
        return (UNPROVABLE, None, PROBLEM_EVIDENCE_DEGRADED,
                "the live workspace reports no agent name set")
    observed = set(observed)
    expected = set(recorded.values())
    if observed != expected:
        return (NOT_OWNED, None, PROBLEM_AGENTS_DISAGREE,
                "the live workspace's agents %r do not match the"
                " recorded %r; a workspace whose sessions are other"
                " than the ones this workflow created is left alone"
                % (sorted(observed), sorted(expected)))
    return (
        OWNED,
        ProofSnapshot(
            workspace_id=workspace_id,
            task_id=task_id,
            agent_names=observed,
            lease=ownership_module.recorded_lease_realpath(entry),
        ),
        None,
        None,
    )


def close_proven_workspace(snapshot, live_now, close_fn,
                           child_records=None, entry=None,
                           workspaces_root=None):
    """Close the workspace THIS SNAPSHOT proved, and no other.

    R-40 AD-3/AD-4. It consumes the ONE snapshot rather than re-reading
    the world, and REVALIDATES it against a fresh live reading taken
    immediately before the close. If the world has moved — agents
    changed, the workspace gone, two workspaces now carrying the id —
    it FAILS CLOSED and retains.

    ``close_fn`` has NO DEFAULT: the capability to close a real
    workspace is handed in on purpose, so a caller that omits it gets
    a TypeError rather than a live close.

    Returns ``(closed, workspace_id, problem, detail)``.
    """
    if snapshot is None:
        return False, None, PROBLEM_EVIDENCE_DEGRADED, (
            "no proven snapshot; a workspace is closed only from an"
            " OWNED proof"
        )
    if not snapshot.still_matches(
        live_now, child_records=child_records, entry=entry,
        workspaces_root=workspaces_root,
    ):
        return False, snapshot.workspace_id, PROBLEM_STALE_PROOF, (
            "the live world no longer matches the proof, so the"
            " workspace is retained: a stale proof is not a proof"
        )
    close_fn(snapshot.workspace_id)
    return True, snapshot.workspace_id, None, None


def production_close(workspace_id):
    """The real `herdr workspace close`.

    Defined here so production has one place to hand to
    `close_owned_workspace`, and deliberately NOT referenced as a
    default anywhere. Within this module it has no caller; a caller that wants a real
    close passes this function in by name, which is what makes the
    dangerous path explicit at every call site.
    """
    from herdr.tasks import run
    completed = run(["herdr", "workspace", "close", workspace_id])
    if getattr(completed, "returncode", 1) != 0:
        raise WorkspaceCloseRefused(
            "herdr workspace close %s failed: %s"
            % (workspace_id, getattr(completed, "stderr", ""))
        )
    return True
