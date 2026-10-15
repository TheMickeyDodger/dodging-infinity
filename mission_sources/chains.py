"""Version 2 roster: the recorded child task chains of each mission, beside
its direct chain, classified with the version 1 roster's OWN vocabulary.

Pure, like ``roster``: it reads mission views, one runtime snapshot
reading and one chain observation reading (both from the same capture)
and returns new plain data; it reads no clock, file or process.

The direct chain is exactly ``roster.classify``'s binding (agreement,
two-pass confirmation and observed roots unchanged). A child chain exists
only where the parent root's children record, read by the capture at the
mission's OWN workspace whose current task IS the mission's task, records a
child for that task. Each child chain is then decided in this fixed order,
every disagreement a reported conflict and never resolved by order:

1. a snapshot or chain observation that is not fresh: ``unverified``;
1a. a PARENT mission whose own binding is not CONFIRMED UNIQUE: it must
   be active, its execution record must resolve to one task and one
   workspace, each claimed once (``roster``'s own ``_execution_binding``),
   and, when the
   caller supplies the first source pass's bindings, its canonical
   identity must AGREE with that pass (``roster.binding_identity``):
   otherwise ``unverified`` (``parent_binding_not_confirmed``), never a
   live member and never an empty-looking chain. A child is only as live
   as the parent agreement it is attributed through;
2. a record without a child task id or repository: ``ambiguous``
   (``child_record_incomplete``);
3. a child repository that is the parent's own workspace, or a child root
   whose own record names the parent or itself back: ``ambiguous``
   (``child_record_cycle``);
4. a child task id or repository claimed more than once across every
   active mission's direct and child chains: ``ambiguous``
   (``task_claimed_by_multiple_missions`` /
   ``workspace_claimed_by_multiple_missions``);
5. a child root the capture did not observe: ``unverified``
   (``workspace_not_observed``), never a live member; ``root.observation``
   says why (unavailable, over capacity or not captured);
6. a child root with no current task: ``disappeared``
   (``task_not_in_snapshot``);
7. a child root whose current task is ANOTHER task: ``ambiguous``
   (``child_record_stale``);
8. a child task whose status ENDS membership (``herdr.vintage``):
   ``not_applicable`` (``chain_membership_ended``), no member;
9. the snapshot holds no / several tasks with that id, or one at another
   workspace: ``disappeared`` / ``ambiguous``, as in version 1;
10. otherwise ``bound``, and its recorded bindings are classified exactly
    as the direct chain's are.

Dependency resolution is a SEPARATE answer (``herdr.dependencies``): a
child whose root was observed, whose current task is the recorded one and
whose status is COMPLETE satisfies its dependency; any other observed child
(another task, no task, any other status, FAILED and ABANDONED included)
leaves it unsatisfied; a child that could not be observed leaves it
unknown. A recorded status from the children record never decides either.

Claims are counted across EVERY bound chain of every mission before any
member is classified, so one live agent or session is never represented
twice; ``member_ref`` is task-qualified and every member names its chain.
"""

import copy

from mission_sources import contract
from mission_sources import contract_v2
from mission_sources import roster

_NOT_FRESH = roster.REASON_SNAPSHOT_NOT_FRESH


def _binding(state, reason, task_id):
    return {"state": state, "reason": reason, "task_id": task_id}


def _children_block(state, problem, caveat=None, record_count=None,
                    other_parent_count=None):
    return {"state": state, "problem": problem, "caveat": caveat,
            "record_count": record_count,
            "other_parent_count": other_parent_count}


def _root_block(chain_reading, path):
    if path is None:
        return {"path": None, "observation": contract_v2.ROOT_UNKNOWN,
                "problem": None}
    observation, problem = chain_reading.root_observation(path)
    return {"path": path, "observation": observation, "problem": problem}


def _membership_end(entry, task_id):
    """The herdr membership rule's answer for ``task_id`` at a root entry:
    known only when that root's CURRENT task is ``task_id``."""
    task = entry["task"] if entry is not None else None
    if task is None or task_id is None or task["task_id"] != task_id:
        return {"ended": None, "status": None,
                "rule": contract_v2.MEMBERSHIP_RULE}
    return {"ended": task["membership_ended"], "status": task["status"],
            "rule": contract_v2.MEMBERSHIP_RULE}


def _parent_children(view, chain_reading):
    """``(children_record, records)`` of one mission: the parent root's
    children record when the mission's own workspace was observed and its
    current task IS the mission's task; else an explicit reason."""
    execution = view["execution"]
    if view["lifecycle"]["standing"] != contract.STANDING_ACTIVE:
        return _children_block(contract_v2.CHILDREN_NOT_APPLICABLE,
                               contract_v2.PROBLEM_MISSION_NOT_ACTIVE), []
    if not chain_reading.is_live():
        return _children_block(
            contract_v2.CHILDREN_UNAVAILABLE,
            contract_v2.PROBLEM_CHAIN_OBSERVATION_NOT_FRESH), []
    if execution["task_id"] is None or (
            execution["workspace_realpath"] is None) or (
                not execution["consistent"]) or execution["record_ambiguous"]:
        return _children_block(
            contract_v2.CHILDREN_NOT_APPLICABLE,
            contract_v2.PROBLEM_MISSION_NOT_BOUND_TO_ROOT), []
    entry = chain_reading.root(execution["workspace_realpath"])
    if entry is None:
        return _children_block(
            contract_v2.CHILDREN_UNAVAILABLE,
            contract_v2.PROBLEM_PARENT_ROOT_NOT_OBSERVED), []
    if entry["task"] is None or entry["task"]["task_id"] != execution[
            "task_id"]:
        return _children_block(contract_v2.CHILDREN_UNAVAILABLE,
                               contract_v2.PROBLEM_PARENT_TASK_MISMATCH), []
    children = entry["children"]
    block = _children_block(children["state"], children["problem"],
                            children["caveat"], children["record_count"],
                            children["other_parent_count"])
    return block, list(children["records"])


def _descendants(entry):
    if entry is None or entry["children"]["state"] not in (
            contract_v2.CHILDREN_OBSERVED,
            contract_v2.CHILDREN_OBSERVED_EMPTY):
        return None
    return entry["children"]["record_count"]


def _names_back(entry, paths):
    """Whether a child root's own children record names any of ``paths``."""
    if entry is None:
        return False
    return any(record["repo"] in paths
               for record in entry["children"]["records"])


def _dependency(record, chain_reading, live):
    recorded = record["dependency"]
    child_task = record["child_task_id"]
    if child_task is None or record["repo"] is None:
        resolution = contract_v2.DEPENDENCY_UNSATISFIED
    elif not live:
        resolution = contract_v2.DEPENDENCY_UNKNOWN
    else:
        entry = chain_reading.root(record["repo"])
        if entry is None:
            resolution = contract_v2.DEPENDENCY_UNKNOWN
        elif entry["task"] is None or entry["task"]["task_id"] != child_task:
            resolution = contract_v2.DEPENDENCY_UNSATISFIED
        elif entry["task"]["dependency_complete"]:
            resolution = contract_v2.DEPENDENCY_SATISFIED
        else:
            resolution = contract_v2.DEPENDENCY_UNSATISFIED
    return {"recorded": recorded, "resolution": resolution,
            "rule": contract_v2.DEPENDENCY_RULE}


def _child_chain(view, record, chain_reading, live, task_claims,
                 workspace_claims, tasks_by_id, parent_confirmed):
    """``(chain_entry, bound_task_or_None)`` for one recorded child."""
    child_task = record["child_task_id"]
    repo = record["repo"]
    parent = view["execution"]["workspace_realpath"]
    entry = chain_reading.root(repo) if repo is not None else None
    task = None
    if not live:
        binding = _binding(contract.BINDING_UNVERIFIED, _NOT_FRESH,
                           child_task)
    elif not parent_confirmed:
        binding = _binding(contract.BINDING_UNVERIFIED,
                           contract_v2.REASON_PARENT_BINDING_UNCONFIRMED,
                           child_task)
    elif child_task is None or repo is None:
        binding = _binding(contract.BINDING_AMBIGUOUS,
                           contract_v2.REASON_CHILD_RECORD_INCOMPLETE,
                           child_task)
    elif repo == parent or _names_back(entry, (parent, repo)):
        binding = _binding(contract.BINDING_AMBIGUOUS,
                           contract_v2.REASON_CHILD_RECORD_CYCLE, child_task)
    elif task_claims.get(child_task, 0) > 1:
        binding = _binding(contract.BINDING_AMBIGUOUS,
                           roster.REASON_TASK_MULTIPLY_CLAIMED, child_task)
    elif workspace_claims.get(repo, 0) > 1:
        binding = _binding(contract.BINDING_AMBIGUOUS,
                           roster.REASON_WORKSPACE_MULTIPLY_CLAIMED,
                           child_task)
    elif entry is None:
        binding = _binding(contract.BINDING_UNVERIFIED,
                           roster.REASON_WORKSPACE_NOT_OBSERVED, child_task)
    elif entry["task"] is None:
        binding = _binding(contract.BINDING_DISAPPEARED,
                           roster.REASON_TASK_NOT_OBSERVED, child_task)
    elif entry["task"]["task_id"] != child_task:
        binding = _binding(contract.BINDING_AMBIGUOUS,
                           contract_v2.REASON_CHILD_RECORD_STALE, child_task)
    elif entry["task"]["membership_ended"]:
        binding = _binding(contract.BINDING_NOT_APPLICABLE,
                           contract_v2.REASON_MEMBERSHIP_ENDED, child_task)
    else:
        matches = tasks_by_id.get(child_task, [])
        if not matches:
            binding = _binding(contract.BINDING_DISAPPEARED,
                               roster.REASON_TASK_NOT_OBSERVED, child_task)
        elif len(matches) > 1:
            binding = _binding(contract.BINDING_AMBIGUOUS,
                               roster.REASON_TASK_DUPLICATED, child_task)
        elif matches[0].workspace_realpath != repo:
            binding = _binding(contract.BINDING_AMBIGUOUS,
                               roster.REASON_TASK_WORKSPACE_MISMATCH,
                               child_task)
        else:
            binding = _binding(contract.BINDING_BOUND, roster.REASON_BOUND,
                               child_task)
            task = matches[0]
    chain = {
        "chain": contract_v2.CHAIN_CHILD,
        "chain_task_id": child_task,
        "parent_task_id": record["parent_task_id"],
        "root": _root_block(chain_reading, repo),
        "binding": binding,
        "membership_end": _membership_end(entry, child_task),
        "dependency": _dependency(record, chain_reading, live),
        "record": {"child_task_id": child_task, "repo": repo,
                   "workspace_id": record["workspace_id"],
                   "declared_agents": copy.deepcopy(
                       record["declared_agents"])},
        "descendants_not_followed": _descendants(entry),
    }
    return chain, task


def _direct_chain(mission, chain_reading, tasks_by_id):
    """``(chain_entry, bound_task_or_None)``: the direct chain is
    ``roster.classify``'s binding; only when the mission's own root shows
    its task ENDED does the chain entry say so in place of
    ``task_not_in_snapshot`` (the mission's own ``binding`` is unchanged)."""
    execution = mission["execution"]
    binding = copy.deepcopy(mission["binding"])
    task_id = execution["task_id"]
    path = execution["workspace_realpath"]
    entry = chain_reading.root(path) if path is not None else None
    end = _membership_end(entry, task_id)
    task = None
    if binding["state"] == contract.BINDING_BOUND:
        task = tasks_by_id[binding["task_id"]][0]
    elif binding["state"] == contract.BINDING_DISAPPEARED and (
            binding["reason"] == roster.REASON_TASK_NOT_OBSERVED) and (
                end["ended"] is True):
        binding = _binding(contract.BINDING_NOT_APPLICABLE,
                           contract_v2.REASON_MEMBERSHIP_ENDED, task_id)
    chain = {
        "chain": contract_v2.CHAIN_DIRECT,
        "chain_task_id": task_id,
        "parent_task_id": None,
        "root": _root_block(chain_reading, path),
        "binding": binding,
        "membership_end": end,
        "dependency": None,
        "record": None,
        "descendants_not_followed": None,
    }
    return chain, task


def _claims(views, children_of):
    """How many active missions' direct OR child chains claim each task id
    and each workspace (roster's own direct counts, plus every recorded
    child of an active mission)."""
    by_task, by_workspace = roster._claims(views)
    for view in views:
        if view["lifecycle"]["standing"] != contract.STANDING_ACTIVE:
            continue
        for record in children_of[view["mission_ref"]]:
            if record["child_task_id"] is not None:
                by_task[record["child_task_id"]] = by_task.get(
                    record["child_task_id"], 0) + 1
            if record["repo"] is not None:
                by_workspace[record["repo"]] = by_workspace.get(
                    record["repo"], 0) + 1
    return by_task, by_workspace


def _parent_confirmed(view, direct_task_claims, direct_workspace_claims,
                      confirmed_bindings):
    """Whether the mission's OWN binding is confirmed unique: it is active,
    its record passes ``roster``'s own execution checks over the DIRECT
    claims, and, when the first pass's bindings are supplied, its canonical
    identity agrees with that pass (the direct members' two-pass rule)."""
    if view["lifecycle"]["standing"] != contract.STANDING_ACTIVE:
        return False
    if roster._execution_binding(view, direct_task_claims,
                                 direct_workspace_claims) is not None:
        return False
    if confirmed_bindings is not None and confirmed_bindings.get(
            view["mission_ref"]) != roster.binding_identity(view):
        return False
    return True


def _qualified(member, chain, chain_task_id, mission_ref):
    member["member_ref"] = "%s#%s#%s" % (mission_ref, chain_task_id,
                                         member["logical"])
    member["chain"] = chain
    member["chain_task_id"] = chain_task_id
    return member


def classify_v2(views, reading, chain_reading, observed_roots=None,
                confirmed_bindings=None):
    """Return ``(missions, unmatched_runtime_agent_count)``: every mission a
    copy of its view plus ``binding`` (version 1, unchanged), ``members``
    (every bound chain, task-qualified), ``task_chains`` and
    ``children_record``. The input is not mutated."""
    missions, _ = roster.classify(views, reading, observed_roots,
                                  confirmed_bindings)
    live = reading.is_live() and chain_reading.is_live()
    tasks_by_id = {}
    for task in reading.tasks:
        tasks_by_id.setdefault(task.task_id, []).append(task)
    children_of, blocks = {}, {}
    for view in views:
        block, records = _parent_children(view, chain_reading)
        blocks[view["mission_ref"]] = block
        children_of[view["mission_ref"]] = records
    task_claims, workspace_claims = _claims(views, children_of)
    direct_task_claims, direct_workspace_claims = roster._claims(views)
    decided = []
    bound = []
    for mission in missions:
        ref = mission["mission_ref"]
        direct, direct_task = _direct_chain(mission, chain_reading,
                                            tasks_by_id)
        chains = [(direct, direct_task)]
        parent_confirmed = _parent_confirmed(
            mission, direct_task_claims, direct_workspace_claims,
            confirmed_bindings)
        for record in children_of[ref]:
            chains.append(_child_chain(mission, record, chain_reading, live,
                                       task_claims, workspace_claims,
                                       tasks_by_id, parent_confirmed))
        for chain, task in chains:
            if task is not None:
                bound.append((mission, task))
        decided.append((mission, chains))
    claims = roster._Claims(bound, reading.agents)
    out = []
    for mission, chains in decided:
        result = copy.deepcopy(mission)
        members = []
        for chain, task in chains:
            if task is None:
                continue
            for member in roster.classify_members(mission, task, claims,
                                                  reading.observed_at):
                members.append(_qualified(member, chain["chain"],
                                          task.task_id,
                                          mission["mission_ref"]))
        members.sort(key=lambda m: (m["chain"] != contract_v2.CHAIN_DIRECT,
                                    m["chain_task_id"], m["logical"],
                                    m["agent"]))
        result["members"] = members
        result["task_chains"] = [chain for chain, _ in chains]
        result["children_record"] = blocks[mission["mission_ref"]]
        out.append(result)
    unmatched = None
    if reading.is_live():
        named = set(claims.agent_names)
        unmatched = sum(1 for a in reading.agents if a.agent not in named)
    return out, unmatched
