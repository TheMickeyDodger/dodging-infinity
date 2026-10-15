"""Roster semantics: which agents are members of which ACTIVE mission.

Pure: it reads mission views (``mission_sources.sources``) and one
validated runtime snapshot reading (``contract.read_runtime_snapshot``)
and returns new plain data; it reads no clock, file or process and
changes nothing.

Membership is canonical, never guessed. An agent is a member of a
mission only through this chain, every link required and exact:

1. the mission's own lifecycle standing is ``active`` (for the WorkflowStore
   this is the store's own non-terminal eligibility, which by itself never
   creates a member: a NEEDS_REAUTHORIZATION record that retains its binding
   keeps its team, a PLANNED record without one stays memberless);
2. the runtime snapshot is ``fresh`` (any other health: binding
   ``unverified``, no members);
3. the mission's own record names a resolved task id AND a workspace
   real path, consistently;
4. exactly one ACTIVE mission (across every source) claims that task id,
   and exactly one ACTIVE mission claims that workspace real path (two
   missions naming the same workspace are a reported CONFLICT,
   ``workspace_claimed_by_multiple_missions``, never resolved by order);
5. when the caller states which workspace roots its snapshot OBSERVED,
   that workspace is one of them (otherwise ``unverified``,
   ``workspace_not_observed``: could not look, never ``disappeared``);
   when the caller supplies the canonical bindings CONFIRMED by an earlier
   source pass (``discovery``'s ``bindings``), the mission's own binding
   identity (``binding_identity``) equals the confirmed one (otherwise
   ``unverified``, ``binding_not_confirmed_by_both_passes``: a binding seen
   in only one pass, or changed between passes, is never presented live;
   a later refresh in which both passes agree acquires it); and the
   snapshot holds exactly one task with that id, and that task's workspace
   real path equals the mission's;
6. the agent is named by one of that task's recorded role bindings.

There is no fallback by agent name, alias, workspace or cwd. A broken
link yields an explicit binding state (``unbound``, ``disappeared``,
``ambiguous``, ``unverified``, ``not_applicable``) with a reason, and no
members.

Each recorded binding of a bound task is then classified against the
live agents the snapshot reports. Claims are counted across EVERY bound
task first, so one physical agent or session can never yield two
representations. In this fixed order:

- ``ambiguous`` when the logical name is duplicated in the task, when the
  agent name or the recorded session is claimed by more than one binding
  (in this task or any other bound task), when more than one live agent
  has that name, or when the live session is shared by several agents;
- ``disappeared`` when no live agent has that name;
- ``ambiguous`` when the pane or workspace is unknown on EITHER side
  (``stable_identity_unconfirmed``) or differs (``stable_identity_conflict``),
  when either side does not report a session (``session_unconfirmed``), or
  when the recorded role or the live provider is not reported
  (``role_or_provider_unconfirmed``): true role and provider provenance is
  required, so their absence can never yield a present or replaced member;
- ``replaced`` when everything matches except the session id (the
  recorded session was replaced);
- ``present`` only when agent, pane, workspace and session all match and
  both role and provider are reported.

``activity`` is the live agent's own status mapped by
``contract.RUNTIME_STATUS_ACTIVITY`` (an unknown literal is
``unknown_future``). Activity is NOT mission state: an idle or done agent
leaves the mission's lifecycle, standing and binding unchanged; this
module never completes, closes or verifies anything. ``model``,
``cost``, ``tokens`` and ``progress`` are always ``unknown``: the
snapshot carries no supported field for them. A role or provider the
snapshot does not report is shown as ``unknown`` and makes the member
``ambiguous`` (never present or replaced).

Live agents that are not named by any bound task are unrelated runtime
sessions: they are counted (``unmatched_runtime_agent_count``) and never
listed, and the count is ``None`` when the snapshot is not fresh.

``discovery`` projects, from mission views alone, the workspace roots a
runtime snapshot should observe: exactly the workspaces of ACTIVE missions
whose own record passes links 3 and 4 (consistent, unambiguous, a resolved
task id and a recorded workspace, the task and the workspace each claimed
once). It names roots to LOOK AT; it never makes a member. Membership is
still decided only by ``classify`` against the snapshot.
"""

import copy

from mission_sources import contract

REASON_NOT_ACTIVE = "mission_not_active"
REASON_SNAPSHOT_NOT_FRESH = "runtime_snapshot_not_fresh"
REASON_RECORD_AMBIGUITY = "mission_record_ambiguity"
REASON_WORKSPACE_INCONSISTENT = "mission_workspace_inconsistent"
REASON_NOT_STARTED = "run_not_started"
REASON_RUN_OUTCOME_UNKNOWN = "run_outcome_unknown"
REASON_TASK_UNRESOLVED = "task_identity_unresolved"
REASON_WORKSPACE_UNRECORDED = "workspace_unrecorded"
REASON_TASK_MULTIPLY_CLAIMED = "task_claimed_by_multiple_missions"
REASON_WORKSPACE_MULTIPLY_CLAIMED = "workspace_claimed_by_multiple_missions"
REASON_WORKSPACE_NOT_OBSERVED = "workspace_not_observed"
REASON_BINDING_UNCONFIRMED = "binding_not_confirmed_by_both_passes"
REASON_TASK_NOT_OBSERVED = "task_not_in_snapshot"
REASON_TASK_DUPLICATED = "task_duplicated_in_snapshot"
REASON_TASK_WORKSPACE_MISMATCH = "task_workspace_mismatch"
REASON_BOUND = "canonical_chain_complete"

MEMBER_REASON_MATCH = "session_confirmed"
MEMBER_REASON_SESSION_CHANGED = "session_changed"
MEMBER_REASON_NOT_LIVE = "agent_not_live"
MEMBER_REASON_NAME_DUPLICATED = "agent_name_duplicated"
MEMBER_REASON_LOGICAL_DUPLICATED = "binding_duplicated"
MEMBER_REASON_STABLE_CONFLICT = "stable_identity_conflict"
MEMBER_REASON_SESSION_UNCONFIRMED = "session_unconfirmed"
MEMBER_REASON_AGENT_MULTIPLY_CLAIMED = "agent_claimed_by_multiple_bindings"
MEMBER_REASON_SESSION_MULTIPLY_CLAIMED = "session_claimed_by_multiple_bindings"
MEMBER_REASON_LIVE_SESSION_SHARED = "live_session_shared_by_agents"
MEMBER_REASON_STABLE_UNCONFIRMED = "stable_identity_unconfirmed"
MEMBER_REASON_PROVENANCE_UNCONFIRMED = "role_or_provider_unconfirmed"


def _known(value):
    return contract.UNKNOWN if value is None else value


def _binding(state, reason, task_id=None):
    return {"state": state, "reason": reason, "task_id": task_id}


def _member(view, task_id, recorded, live, classification, reason,
            observed_at):
    activity = contract.ACTIVITY_UNKNOWN
    provider = contract.UNKNOWN
    live_session = None
    if live is not None:
        activity = contract.RUNTIME_STATUS_ACTIVITY.get(
            live.status, contract.ACTIVITY_UNKNOWN_FUTURE)
        provider = _known(live.provider)
        live_session = live.session
    member = {
        "member_ref": "%s#%s" % (view["mission_ref"], recorded.logical),
        "mission_ref": view["mission_ref"],
        "task_id": task_id,
        "logical": recorded.logical,
        "role": _known(recorded.role),
        "agent": recorded.agent,
        "provider": provider,
        "pane_id": recorded.pane_id,
        "workspace_id": recorded.workspace_id,
        "session": {"recorded": recorded.session, "live": live_session},
        "classification": classification,
        "reason": reason,
        "activity": activity,
        "observed_at": observed_at,
    }
    for fact in contract.UNREPORTED_FACTS:
        member[fact] = contract.UNKNOWN
    return member


class _Claims(object):
    """Who claims what, across EVERY bound task of one classification."""

    def __init__(self, bound, agents):
        self.agent_names = {}
        self.recorded_sessions = {}
        for _, task in bound:
            for recorded in task.bindings:
                self.agent_names[recorded.agent] = self.agent_names.get(
                    recorded.agent, 0) + 1
                if recorded.session is not None:
                    self.recorded_sessions[recorded.session] = \
                        self.recorded_sessions.get(recorded.session, 0) + 1
        self.live_by_name = {}
        self.live_sessions = {}
        for agent in agents:
            self.live_by_name.setdefault(agent.agent, []).append(agent)
            if agent.session is not None:
                self.live_sessions[agent.session] = self.live_sessions.get(
                    agent.session, 0) + 1


def _member_classification(recorded, logical_count, claims):
    """``(classification, reason, live_agent_or_None)`` for one binding.

    Order matters and is fixed: every duplicate and every missing identity
    fact is ambiguous BEFORE any present / replaced claim is considered."""
    if logical_count > 1:
        return contract.MEMBER_AMBIGUOUS, MEMBER_REASON_LOGICAL_DUPLICATED, None
    if claims.agent_names.get(recorded.agent, 0) > 1:
        return contract.MEMBER_AMBIGUOUS, MEMBER_REASON_AGENT_MULTIPLY_CLAIMED, \
            None
    if recorded.session is not None and claims.recorded_sessions.get(
            recorded.session, 0) > 1:
        return (contract.MEMBER_AMBIGUOUS,
                MEMBER_REASON_SESSION_MULTIPLY_CLAIMED, None)
    live = claims.live_by_name.get(recorded.agent, [])
    if not live:
        return contract.MEMBER_DISAPPEARED, MEMBER_REASON_NOT_LIVE, None
    if len(live) > 1:
        return contract.MEMBER_AMBIGUOUS, MEMBER_REASON_NAME_DUPLICATED, None
    agent = live[0]
    if agent.session is not None and claims.live_sessions.get(
            agent.session, 0) > 1:
        return (contract.MEMBER_AMBIGUOUS, MEMBER_REASON_LIVE_SESSION_SHARED,
                agent)
    stable = (recorded.pane_id, agent.pane_id, recorded.workspace_id,
              agent.workspace_id)
    if any(value is None for value in stable):
        return (contract.MEMBER_AMBIGUOUS, MEMBER_REASON_STABLE_UNCONFIRMED,
                agent)
    if recorded.pane_id != agent.pane_id or \
            recorded.workspace_id != agent.workspace_id:
        return contract.MEMBER_AMBIGUOUS, MEMBER_REASON_STABLE_CONFLICT, agent
    if recorded.session is None or agent.session is None:
        return (contract.MEMBER_AMBIGUOUS, MEMBER_REASON_SESSION_UNCONFIRMED,
                agent)
    if recorded.role is None or agent.provider is None:
        return (contract.MEMBER_AMBIGUOUS,
                MEMBER_REASON_PROVENANCE_UNCONFIRMED, agent)
    if recorded.session != agent.session:
        return contract.MEMBER_REPLACED, MEMBER_REASON_SESSION_CHANGED, agent
    return contract.MEMBER_PRESENT, MEMBER_REASON_MATCH, agent


def classify_members(view, task, claims, observed_at):
    """Classify every recorded binding of one bound task."""
    logical_counts = {}
    for recorded in task.bindings:
        logical_counts[recorded.logical] = logical_counts.get(
            recorded.logical, 0) + 1
    members = []
    for recorded in task.bindings:
        classification, reason, live = _member_classification(
            recorded, logical_counts[recorded.logical], claims)
        members.append(_member(view, task.task_id, recorded, live,
                               classification, reason, observed_at))
    members.sort(key=lambda m: (m["logical"], m["agent"]))
    return members


def _claims(views):
    """How many ACTIVE missions claim each task id and each workspace."""
    by_task, by_workspace = {}, {}
    for view in views:
        if view["lifecycle"]["standing"] == contract.STANDING_ACTIVE:
            execution = view["execution"]
            if execution["task_id"] is not None:
                by_task[execution["task_id"]] = by_task.get(
                    execution["task_id"], 0) + 1
            if execution["workspace_realpath"] is not None:
                by_workspace[execution["workspace_realpath"]] = by_workspace.get(
                    execution["workspace_realpath"], 0) + 1
    return by_task, by_workspace


def _execution_binding(view, claims, workspace_claims):
    """The binding state from the mission's own record, before the
    snapshot is consulted; ``None`` when the record supports a lookup."""
    execution = view["execution"]
    if execution["record_ambiguous"]:
        return _binding(contract.BINDING_AMBIGUOUS, REASON_RECORD_AMBIGUITY)
    if not execution["consistent"]:
        return _binding(contract.BINDING_AMBIGUOUS,
                        REASON_WORKSPACE_INCONSISTENT)
    task_id = execution["task_id"]
    if task_id is None:
        if execution["receipt_recorded"]:
            return _binding(contract.BINDING_UNBOUND, REASON_TASK_UNRESOLVED)
        if execution["run_started"]:
            return _binding(contract.BINDING_UNBOUND,
                            REASON_RUN_OUTCOME_UNKNOWN)
        return _binding(contract.BINDING_UNBOUND, REASON_NOT_STARTED)
    if execution["workspace_realpath"] is None:
        return _binding(contract.BINDING_UNBOUND, REASON_WORKSPACE_UNRECORDED,
                        task_id)
    if claims.get(task_id, 0) > 1:
        return _binding(contract.BINDING_AMBIGUOUS,
                        REASON_TASK_MULTIPLY_CLAIMED, task_id)
    if workspace_claims.get(execution["workspace_realpath"], 0) > 1:
        return _binding(contract.BINDING_AMBIGUOUS,
                        REASON_WORKSPACE_MULTIPLY_CLAIMED, task_id)
    return None


def binding_identity(view):
    """One mission's canonical binding identity, exactly as its source
    projects it: the source-qualified ref and source, the native lifecycle
    model, the declared producer, and the native execution identity (task
    id, workspace real path, identity source). Nothing is derived."""
    execution = view["execution"]
    return {"mission_ref": view["mission_ref"],
            "source_id": view["source_id"],
            "model": view["lifecycle"]["model"],
            "declared": view["producer"]["declared"],
            "task_id": execution["task_id"],
            "workspace_realpath": execution["workspace_realpath"],
            "identity_source": execution["identity_source"]}


def discovery(views):
    """The workspace roots canonical bindings name (see the module
    docstring): ``{"roots": sorted roots, "missions_by_root": {root:
    mission_ref}, "bindings": {mission_ref: binding_identity}, "conflicts":
    [{"workspace_realpath", "mission_refs"}]}``. ``bindings`` are the
    eligible bindings of THIS pass, to be confirmed by a later one.
    Pure; reads only the views (mission views, or a query response's
    missions); a root is a place to LOOK, never a membership."""
    claims_by_task, workspace_claims = _claims(views)
    missions_by_root, claimed, bindings = {}, {}, {}
    for view in views:
        if view["lifecycle"]["standing"] != contract.STANDING_ACTIVE:
            continue
        workspace = view["execution"]["workspace_realpath"]
        if workspace is not None:
            claimed.setdefault(workspace, []).append(view["mission_ref"])
        if _execution_binding(view, claims_by_task, workspace_claims) is None:
            missions_by_root[workspace] = view["mission_ref"]
            bindings[view["mission_ref"]] = binding_identity(view)
    return {
        "roots": sorted(missions_by_root),
        "missions_by_root": missions_by_root,
        "bindings": bindings,
        "conflicts": [{"workspace_realpath": workspace,
                       "mission_refs": sorted(refs)}
                      for workspace, refs in sorted(claimed.items())
                      if len(refs) > 1],
    }


def classify(views, reading, observed_roots=None, confirmed_bindings=None):
    """Return ``(missions, unmatched_runtime_agent_count)``.

    ``views`` are mission views from fresh sources; ``reading`` is a
    ``contract.SnapshotReading``. Every returned mission is a copy of its
    view plus ``binding`` and ``members``; the input is not mutated.
    Bindings are decided first for every mission; members are classified
    only afterwards, against claims counted across ALL bound tasks, so one
    live agent or session can never be represented twice.
    ``observed_roots``: the workspace roots the snapshot actually observed,
    when the caller knows them; a mission whose workspace is not among them
    is ``unverified`` (``workspace_not_observed``), never ``disappeared``.
    ``None`` (unknown) keeps the snapshot lookup alone.
    ``confirmed_bindings``: ``{mission_ref: binding_identity}`` from an
    earlier source pass; a mission whose identity differs, or that the
    earlier pass did not confirm, is ``unverified``
    (``binding_not_confirmed_by_both_passes``). ``None``: no such check."""
    claims_by_task, workspace_claims = _claims(views)
    tasks_by_id = {}
    for task in reading.tasks:
        tasks_by_id.setdefault(task.task_id, []).append(task)
    live = reading.is_live()
    decided = []
    bound = []
    for view in sorted(views, key=lambda v: v["mission_ref"]):
        task = None
        if view["lifecycle"]["standing"] != contract.STANDING_ACTIVE:
            binding = _binding(contract.BINDING_NOT_APPLICABLE,
                               REASON_NOT_ACTIVE)
        elif not live:
            binding = _binding(contract.BINDING_UNVERIFIED,
                               REASON_SNAPSHOT_NOT_FRESH,
                               view["execution"]["task_id"])
        else:
            binding = _execution_binding(view, claims_by_task,
                                         workspace_claims)
            if binding is None:
                task_id = view["execution"]["task_id"]
                matches = tasks_by_id.get(task_id, [])
                if observed_roots is not None and view["execution"][
                        "workspace_realpath"] not in observed_roots:
                    binding = _binding(contract.BINDING_UNVERIFIED,
                                       REASON_WORKSPACE_NOT_OBSERVED, task_id)
                elif confirmed_bindings is not None and confirmed_bindings.get(
                        view["mission_ref"]) != binding_identity(view):
                    binding = _binding(contract.BINDING_UNVERIFIED,
                                       REASON_BINDING_UNCONFIRMED, task_id)
                elif not matches:
                    binding = _binding(contract.BINDING_DISAPPEARED,
                                       REASON_TASK_NOT_OBSERVED, task_id)
                elif len(matches) > 1:
                    binding = _binding(contract.BINDING_AMBIGUOUS,
                                       REASON_TASK_DUPLICATED, task_id)
                elif matches[0].workspace_realpath != \
                        view["execution"]["workspace_realpath"]:
                    binding = _binding(contract.BINDING_AMBIGUOUS,
                                       REASON_TASK_WORKSPACE_MISMATCH, task_id)
                else:
                    binding = _binding(contract.BINDING_BOUND, REASON_BOUND,
                                       task_id)
                    task = matches[0]
                    bound.append((view, task))
        decided.append((view, binding, task))
    claims = _Claims(bound, reading.agents)
    out = []
    for view, binding, task in decided:
        mission = copy.deepcopy(view)
        mission["binding"] = binding
        mission["members"] = ([] if task is None else classify_members(
            view, task, claims, reading.observed_at))
        out.append(mission)
    unmatched = None
    if live:
        named = set(claims.agent_names)
        unmatched = sum(1 for a in reading.agents if a.agent not in named)
    return out, unmatched
