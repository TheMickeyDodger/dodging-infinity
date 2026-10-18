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
# A task-native record DEFERS (``deferrals``): to a canonical record naming
# exactly its task and root (an alias), to one naming only one of them (a
# conflict), to a recorded parent chain, or to canonical sources that could
# not be read.
REASON_COVERED_BY_CANONICAL = "covered_by_canonical_record"
REASON_CANONICAL_CONFLICT = "canonical_claim_conflict"
REASON_CANONICAL_SOURCE_NOT_FRESH = "canonical_source_not_fresh"
REASON_COVERED_BY_PARENT_CHAIN = "covered_by_parent_chain"
# A task-native record's membership is DERIVED from its root's current task
# record, so its team binds only when at least one recorded role is held by
# a live agent on EXACTLY the recorded session (``_corroborated``); without
# that, a historical record names no team.
REASON_NOT_CORROBORATED = "task_record_not_corroborated_by_a_live_session"

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


def _corroborated(task, agents):
    """Whether at least one recorded role of ``task`` is held by a live
    agent of the same name on EXACTLY the recorded session. One such role
    gates the TEAM only: every member is still classified on its own."""
    live = set((agent.agent, agent.session) for agent in agents
               if agent.session is not None)
    return any((recorded.agent, recorded.session) in live
               for recorded in task.bindings if recorded.session is not None)


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


def task_native(view):
    """Whether a view is a task-native record: any model that is not one of
    the canonical kinds' own (``contract.CANONICAL_LIFECYCLE_MODELS``)."""
    return view["lifecycle"]["model"] not in (
        contract.CANONICAL_LIFECYCLE_MODELS)


# A canonical record claims its task and root unless its own model says it
# is inactive or terminal (an unrecognized literal may still be live).
_CANONICAL_CLAIMING = (contract.STANDING_ACTIVE,
                       contract.STANDING_UNKNOWN_FUTURE)


def deferrals(views, canonical_complete=True, parent_links=None):
    """``{mission_ref: binding}`` of every ACTIVE task-native view that
    DEFERS, so the task-native fallback can never bypass, ambiguate or
    duplicate a canonical binding. In this fixed order:

    - a canonical record (Mission Core or WorkflowStore, any binding state,
      standing active or unrecognized) naming EXACTLY its task id and root:
      an alias, ``not_applicable`` (``covered_by_canonical_record``);
    - one naming only its task id or only its root: a CONFLICT, never
      resolved by priority, ``ambiguous`` (``canonical_claim_conflict``);
    - a recorded parent chain (``parent_links``, contract v2) naming exactly
      its task and root: ``not_applicable`` (``covered_by_parent_chain``);
      naming only one of them: ``ambiguous`` (``canonical_claim_conflict``);
    - when ``canonical_complete`` is false (a canonical source could not be
      read, so a covering record may be unseen): ``unverified``
      (``canonical_source_not_fresh``).

    A deferring view claims nothing, binds nothing and has no members; the
    canonical record's own binding is decided exactly as without it."""
    canonical = [view for view in views if not task_native(view)
                 and view["lifecycle"]["standing"] in _CANONICAL_CLAIMING]
    pairs = set((v["execution"]["task_id"], v["execution"]["workspace_realpath"])
                for v in canonical)
    tasks = set(v["execution"]["task_id"] for v in canonical) - {None}
    workspaces = set(v["execution"]["workspace_realpath"]
                     for v in canonical) - {None}
    links = set(parent_links or ())
    link_tasks = set(task for task, _ in links) - {None}
    link_workspaces = set(workspace for _, workspace in links) - {None}
    out = {}
    for view in views:
        if not task_native(view) or (
                view["lifecycle"]["standing"] != contract.STANDING_ACTIVE):
            continue
        task_id = view["execution"]["task_id"]
        workspace = view["execution"]["workspace_realpath"]
        if (task_id, workspace) in pairs:
            binding = _binding(contract.BINDING_NOT_APPLICABLE,
                               REASON_COVERED_BY_CANONICAL, task_id)
        elif task_id in tasks or workspace in workspaces:
            binding = _binding(contract.BINDING_AMBIGUOUS,
                               REASON_CANONICAL_CONFLICT, task_id)
        elif (task_id, workspace) in links:
            binding = _binding(contract.BINDING_NOT_APPLICABLE,
                               REASON_COVERED_BY_PARENT_CHAIN, task_id)
        elif task_id in link_tasks or workspace in link_workspaces:
            binding = _binding(contract.BINDING_AMBIGUOUS,
                               REASON_CANONICAL_CONFLICT, task_id)
        elif not canonical_complete:
            binding = _binding(contract.BINDING_UNVERIFIED,
                               REASON_CANONICAL_SOURCE_NOT_FRESH, task_id)
        else:
            continue
        out[view["mission_ref"]] = binding
    return out


def canonical_sources_complete(source_blocks):
    """Whether every registered CANONICAL source was read fresh (a source
    block per registered source, as a query response lists them)."""
    return all(block["health"] == contract.HEALTH_FRESH
               for block in source_blocks
               if block["kind"] in contract.CANONICAL_KINDS)


def _claims(views, deferred=None):
    """How many ACTIVE missions claim each task id and each workspace; a
    deferring task-native view (``deferrals``) claims nothing."""
    deferred = deferred or {}
    by_task, by_workspace = {}, {}
    for view in views:
        if view["mission_ref"] in deferred:
            continue
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


def discovery(views, canonical_complete=True):
    """The workspace roots canonical bindings name (see the module
    docstring): ``{"roots": sorted roots, "missions_by_root": {root:
    mission_ref}, "bindings": {mission_ref: binding_identity}, "conflicts":
    [{"workspace_realpath", "mission_refs"}]}``. ``bindings`` are the
    eligible bindings of THIS pass, to be confirmed by a later one.
    Pure; reads only the views (mission views, or a query response's
    missions); a root is a place to LOOK, never a membership. A deferring
    task-native view (``deferrals``) names no root and claims nothing.

    ``roots`` is in SCHEDULING order, never identity or membership
    evidence: the canonical roots sorted, then the task-native roots in
    the order their source listed its views (``target_runtime``'s reader
    lists the most recently started task first). The runtime capture
    observes at most its fixed capacity of roots in this order and REPORTS
    the rest as over capacity."""
    deferred = deferrals(views, canonical_complete)
    claims_by_task, workspace_claims = _claims(views, deferred)
    missions_by_root, claimed, bindings = {}, {}, {}
    for view in views:
        if view["lifecycle"]["standing"] != contract.STANDING_ACTIVE:
            continue
        if view["mission_ref"] in deferred:
            continue
        workspace = view["execution"]["workspace_realpath"]
        if workspace is not None:
            claimed.setdefault(workspace, []).append(view["mission_ref"])
        if _execution_binding(view, claims_by_task, workspace_claims) is None:
            missions_by_root[workspace] = view["mission_ref"]
            bindings[view["mission_ref"]] = binding_identity(view)
    by_ref = dict((view["mission_ref"], view) for view in views)
    canonical_roots = sorted(root for root, ref in missions_by_root.items()
                             if not task_native(by_ref[ref]))
    native_roots = [view["execution"]["workspace_realpath"] for view in views
                    if task_native(view) and missions_by_root.get(
                        view["execution"]["workspace_realpath"])
                    == view["mission_ref"]]
    return {
        "roots": canonical_roots + native_roots,
        "missions_by_root": missions_by_root,
        "bindings": bindings,
        "conflicts": [{"workspace_realpath": workspace,
                       "mission_refs": sorted(refs)}
                      for workspace, refs in sorted(claimed.items())
                      if len(refs) > 1],
    }


def classify(views, reading, observed_roots=None, confirmed_bindings=None,
             canonical_complete=True, deferred=None):
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
    (``binding_not_confirmed_by_both_passes``). ``None``: no such check.
    ``canonical_complete`` / ``deferred``: see ``deferrals`` (``deferred``,
    when given, replaces the views-only computation, e.g. with parent
    links)."""
    if deferred is None:
        deferred = deferrals(views, canonical_complete)
    claims_by_task, workspace_claims = _claims(views, deferred)
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
        elif view["mission_ref"] in deferred:
            binding = dict(deferred[view["mission_ref"]])
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
    # A task-native team binds only when a live session corroborates it,
    # decided BEFORE any claim is counted: an uncorroborated historical
    # record must never make a live team's agents ambiguous.
    for index, (view, binding, task) in enumerate(decided):
        if task is not None and task_native(view) and not _corroborated(
                task, reading.agents):
            decided[index] = (view, _binding(
                contract.BINDING_UNVERIFIED, REASON_NOT_CORROBORATED,
                task.task_id), None)
    bound = [(view, task) for view, _, task in decided if task is not None]
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
