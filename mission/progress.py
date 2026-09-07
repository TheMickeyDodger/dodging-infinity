"""Pure, deterministic evaluators over a Mission State record.

Every function here is a function of its arguments and nothing else: no
I/O, no store, no clock of its own (``now`` is always a parameter), no
randomness, no mutation of its inputs. The store and the service call
these; nothing here calls them back.

Proof (R-9). ``requirement_status`` is one of ``SATISFIED`` /
``MISSING`` / ``SUBMITTED_NOT_ACCEPTED`` / ``STALE`` / ``MISMATCHED`` /
``INVALIDATED`` / ``CONTRADICTED``. ``SATISFIED`` requires an ACCEPTED,
non-invalidated evidence record bound to the given activation and that
requirement, of a kind the requirement declares (which by construction
excludes ``NARRATIVE_CLAIM`` and ``PROCESS_EXIT``), whose accepted digest
equals its submitted digest, whose referenced artifacts cover every
required artifact key of the requirement with the contract's role, the
contract's expected digest and ``available: true``, and whose acceptance
is within the requirement's staleness bound of ``now``. Two accepted,
non-invalidated records with differing digests are ``CONTRADICTED``.
Claims are never read.

Readiness (R-8). Per required resource key, the latest observation (in
accepted order) must be ``READY`` and no older than the contract's
``max_age_seconds``; ``UNKNOWN``, ``NOT_READY``, absent, stale and
future-dated observations all count as not ready. The result is a
refusal input only: it carries no permission.

Required artifacts (R-19, R-20.2). Independently of any requirement,
``required_artifact_problems`` walks every declared required artifact of
the contract. The rule is LATEST RECORD PER KEY: among the artifact
records carrying that contract key, the one with the highest sequence is
the one judged, and it must carry the contract's role, ``available:
true`` and exactly the declared digest. A later record can therefore
only replace a bad one with one that genuinely matches, and a later
``available: false`` record conservatively blocks closure even when an
earlier referenced record was good. Artifacts carry no activation
binding; they are content-identified against the approved digest. This
pass is deliberately redundant with contract coherence (R-20.1, refused
at validation) and with the per-requirement artifact check: redundancy
on a fail-closed path is correct.

Dependencies (R-14, R-18, R-21). Two questions, kept apart on purpose:

- BIND time is historical and stays readable. ``target_matches`` asks
  whether a reference satisfies the approved target as declared: the
  declared revision exists on the referenced Mission with the declared
  digest (``EXACT_MISSION``), the referenced Mission's current revision
  carries the declared digest (``ELIGIBILITY``), or the resource key
  matches exactly. A binding records history; it confers no eligibility,
  and a later EDIT of the prerequisite never makes the binding record
  malformed.
- CLOSURE time is strictly current and completion-bound.
  ``prerequisite_problems`` requires, for every bound required MISSION
  slot, that the prerequisite's CURRENT revision is the declared one
  (``EXACT_MISSION``) or that its current proposal digest equals the
  declared digest (``ELIGIBILITY``) — else
  ``mission_state_prerequisite_drifted`` — AND that its own progress is
  ``COMPLETED`` with a closure bound to that same revision — else
  ``mission_state_prerequisite_not_complete``. A completion recorded under
  a different activation or revision never satisfies the slot.
  Stated limit (R-21.3): authorization EXPIRY on a prerequisite after it
  legitimately completed does not retroactively unmake that completion,
  because the registry view carries no expiry input; an EDIT does, via
  the current-revision check, and so does the ``superseded_by_edit``
  revocation an EDIT performs.

``dependency_status`` is state-only (``UNBOUND`` / ``BOUND_UNRESOLVED`` /
``RESOLVED``) so that checkpoint recomputation never reads the mutable
registry; ``closure_failures`` and ``closure_eligibility`` read it.

Closure, split in two halves with exactly one definition each (R-24).
``local_closure_failures`` is the locally provable half: proof
satisfied, every declared required artifact covered, no active HARD
blocker, every required slot RESOLVED, readiness satisfied. It reads
only the contract, the Mission's own state and ``now``.
``closure_failures`` is ``local_closure_failures`` plus
``prerequisite_problems`` (the foreign, registry-aware half). The store
re-proves the LOCAL half of every persisted COMPLETED closure on every
load and save (``closure_proof_problem``), over ``state_as_of`` the
closure's sequence with the closure's own ``closed_at`` as the clock, so
the proof is reconstructible and never drifts; it likewise re-proves an
unsuccessful closure's asserted reason (``budget_exhausted`` requires the
attempts to have reached the approved bound, ``hard_blocker_unresolvable``
requires an active HARD blocker). Stated limit, deliberate: the foreign
half (another Mission's current revision, progress and closure binding)
and the activation authorization's liveness are checked by the service
at ``complete`` time and surfaced as CURRENT drift by the read-time
``closure_eligibility`` projection; they are NOT load-time checks, so a
foreign EDIT or the passage of time can never make a valid historical
record unreadable.

Checkpoints (R-11, R-22). ``derive_checkpoint_fields`` derives, from the
approved contract and local accepted state ONLY, a checkpoint's active
blockers, outstanding dependencies, budget and EXACTLY ONE of
``next_permitted_step`` or ``refusal``; ``checkpoint_disagreement``
recomputes them from ``state_as_of`` the checkpoint's own sequence with
its own ``recorded_at`` as the clock, so a stored checkpoint stays stable
across reload and across prerequisite drift. Because local state cannot
see whether a required MISSION prerequisite still holds, the derivation
never claims ``CLOSE_COMPLETED`` when the contract declares such a slot:
it derives the conservative ``CONFIRM_PREREQUISITES_AND_CLOSE`` instead.
``CLOSE_COMPLETED`` is derived only when no required MISSION slot exists.
A registry-aware ``closure_eligibility`` block belongs to a read-time
projection, never to the stored checkpoint.

Budget (R-10 as corrected by R-23; THE one statement, referenced
elsewhere). Attempts are consumed only by recording a continuation,
hard-bounded by the approved ``continuation_budget.max_attempts``; at the
bound a further attempt refuses ``mission_state_budget_exhausted``.
Successful completion is a function of proof, required artifacts,
blockers, dependencies, prerequisites and readiness ONLY; it never reads
remaining budget, so consuming the final permitted attempt and then
holding complete proof yields COMPLETED: success comes from proof, never
from budget and never from exhaustion. Exhaustion with proof NOT
satisfied permits exactly one outcome, unsuccessful closure; the derived
checkpoint field is then a refusal, never a retry step. Budget is never
reset, is never credited by a replayed operation, and can only be raised
by an EDIT to a new revision plus a fresh APPROVE.
"""

import copy

from mission import record
from mission import state as state_module

REQUIREMENT_SATISFIED = "SATISFIED"
REQUIREMENT_MISSING = "MISSING"
REQUIREMENT_SUBMITTED_NOT_ACCEPTED = "SUBMITTED_NOT_ACCEPTED"
REQUIREMENT_STALE = "STALE"
REQUIREMENT_MISMATCHED = "MISMATCHED"
REQUIREMENT_INVALIDATED = "INVALIDATED"
REQUIREMENT_CONTRADICTED = "CONTRADICTED"
REQUIREMENT_STATUSES = (
    REQUIREMENT_SATISFIED, REQUIREMENT_MISSING,
    REQUIREMENT_SUBMITTED_NOT_ACCEPTED, REQUIREMENT_STALE,
    REQUIREMENT_MISMATCHED, REQUIREMENT_INVALIDATED, REQUIREMENT_CONTRADICTED,
)

SLOT_UNBOUND = "UNBOUND"
SLOT_BOUND_UNRESOLVED = "BOUND_UNRESOLVED"
SLOT_RESOLVED = "RESOLVED"
SLOT_STATUSES = (SLOT_UNBOUND, SLOT_BOUND_UNRESOLVED, SLOT_RESOLVED)

PROBLEM_PROOF_NOT_SATISFIED = "mission_state_proof_not_satisfied"
PROBLEM_HARD_BLOCKER_ACTIVE = "mission_state_hard_blocker_active"
PROBLEM_DEPENDENCY_UNRESOLVED = "mission_state_dependency_unresolved"
PROBLEM_DEPENDENCY_TARGET_MISMATCH = "mission_state_dependency_target_mismatch"
PROBLEM_PREREQUISITE_NOT_COMPLETE = "mission_state_prerequisite_not_complete"
PROBLEM_PREREQUISITE_DRIFTED = "mission_state_prerequisite_drifted"
PROBLEM_REQUIRED_ARTIFACT_UNAVAILABLE = "mission_state_required_artifact_unavailable"
PROBLEM_RESOURCE_NOT_READY = "mission_state_resource_not_ready"
PROBLEM_DEPENDENCY_CYCLE = "mission_state_dependency_cycle"
PROBLEM_CHECKPOINT_DISAGREES = "mission_state_checkpoint_disagrees"
PROBLEM_CLOSURE_NOT_PROVABLE = "mission_state_closure_not_provable"

_DERIVED_CHECKPOINT_FIELDS = (
    "active_blocker_ids", "outstanding_dependency_ids", "budget",
    "next_permitted_step", "refusal",
)


# -- projection ---------------------------------------------------------


def _event_as_of(event, sequence):
    if event is None or event["sequence"] > sequence:
        return None
    return event


def state_as_of(state, sequence):
    """A deep copy of ``state`` holding only what had been accepted at
    ``sequence``: records and nested events with a later sequence are
    absent, ``sequence`` is ``sequence``, and ``progress`` is re-derived
    (closure if present, else BLOCKED with an active HARD blocker, else
    IN_PROGRESS with an activation, else NOT_STARTED)."""
    projected = copy.deepcopy(state)
    for name in ("contract_activations", "claims", "artifacts", "evidence",
                 "blockers", "dependencies", "resource_readiness",
                 "checkpoints", "continuations", "applied_operations"):
        projected[name] = [e for e in projected[name] if e["sequence"] <= sequence]
    for evidence in projected["evidence"]:
        evidence["acceptance"] = _event_as_of(evidence["acceptance"], sequence)
        evidence["invalidation"] = _event_as_of(evidence["invalidation"], sequence)
    for name in ("blockers", "dependencies"):
        for entry in projected[name]:
            entry["resolution"] = _event_as_of(entry["resolution"], sequence)
    projected["closure"] = _event_as_of(projected["closure"], sequence)
    projected["sequence"] = sequence
    if projected["applied_operations"]:
        projected["updated_at"] = projected["applied_operations"][-1]["applied_at"]
    else:
        projected["updated_at"] = projected["created_at"]
    if projected["closure"] is not None:
        projected["progress"] = projected["closure"]["progress"]
    elif not projected["contract_activations"]:
        projected["progress"] = state_module.PROGRESS_NOT_STARTED
    elif state_module.active_hard_blockers(projected):
        projected["progress"] = state_module.PROGRESS_BLOCKED
    else:
        projected["progress"] = state_module.PROGRESS_IN_PROGRESS
    return projected


def progress_at(state, sequence):
    """The progress the history held AT ``sequence``, without copying:
    the closure's outcome once recorded; NOT_STARTED before any activation;
    BLOCKED while a HARD blocker opened at or before ``sequence`` had no
    resolution at or before it; IN_PROGRESS otherwise. Equal to
    ``state_as_of(state, sequence)["progress"]`` by construction."""
    closure = state["closure"]
    if closure is not None and closure["sequence"] <= sequence:
        return closure["progress"]
    if not any(a["sequence"] <= sequence for a in state["contract_activations"]):
        return state_module.PROGRESS_NOT_STARTED
    for blocker in state["blockers"]:
        if blocker["sequence"] > sequence or (
            blocker["severity"] != state_module.BLOCKER_SEVERITY_HARD
        ):
            continue
        resolution = blocker["resolution"]
        if resolution is None or resolution["sequence"] > sequence:
            return state_module.PROGRESS_BLOCKED
    return state_module.PROGRESS_IN_PROGRESS


# -- proof ----------------------------------------------------------------


def kind_can_satisfy(kind):
    """The module-constant table: can this evidence kind EVER satisfy?"""
    return kind in record.SATISFYING_EVIDENCE_KINDS


def _required_artifact_index(contract):
    return dict((a["key"], a) for a in contract["required_artifacts"])


def _artifact_covers(artifact, declared):
    return (
        artifact is not None
        and artifact["key"] == declared["key"]
        and artifact["role"] == declared["role"]
        and artifact["available"] is True
        and artifact["content_digest_sha256"] is not None
        and artifact["content_digest_sha256"] == declared[
            "expected_content_digest_sha256"]
    )


def _evidence_mismatch(contract, requirement, evidence, state):
    """Why an accepted record does not match its requirement, or None."""
    if evidence["kind"] not in requirement["evidence_kinds"] or (
        not kind_can_satisfy(evidence["kind"])
    ):
        return "kind %s is not declared by requirement %r" % (
            evidence["kind"], requirement["key"])
    if evidence["acceptance"]["content_digest_sha256"] != (
        evidence["content_digest_sha256"]
    ):
        return "accepted digest disagrees with the submitted digest"
    declared_index = _required_artifact_index(contract)
    referenced = [state_module.artifact_by_id(state, artifact_id)
                  for artifact_id in evidence["artifact_ids"]]
    for key in requirement["required_artifact_keys"]:
        declared = declared_index[key]
        if not any(_artifact_covers(artifact, declared) for artifact in referenced):
            return ("required artifact %r is not referenced as available with"
                    " the approved digest" % key)
    return None


def requirement_status(contract, requirement, state, activation_id, now):
    candidates = [
        e for e in state["evidence"]
        if e["requirement_key"] == requirement["key"]
        and e["activation_id"] == activation_id
    ]
    if not candidates:
        return REQUIREMENT_MISSING
    accepted = [e for e in candidates if state_module.is_accepted(e)]
    if not accepted:
        if any(e["acceptance"] is None and e["invalidation"] is None
               for e in candidates):
            return REQUIREMENT_SUBMITTED_NOT_ACCEPTED
        return REQUIREMENT_INVALIDATED
    if len(set(e["content_digest_sha256"] for e in accepted)) > 1:
        return REQUIREMENT_CONTRADICTED
    for evidence in accepted:
        if _evidence_mismatch(contract, requirement, evidence, state) is not None:
            return REQUIREMENT_MISMATCHED
    latest = max(accepted, key=lambda e: e["acceptance"]["sequence"])
    age = now - latest["acceptance"]["accepted_at"]
    if age < 0 or age > requirement["max_evidence_age_seconds"]:
        return REQUIREMENT_STALE
    return REQUIREMENT_SATISFIED


def required_artifact_problems(contract, state):
    """R-20.2: every declared required artifact, independent of any
    requirement. The latest record for the key must carry the contract's
    role, ``available: true`` and exactly the declared digest."""
    latest = {}
    for artifact in state["artifacts"]:
        if artifact["key"] is not None:
            latest[artifact["key"]] = artifact
    problems = []
    for declared in contract["required_artifacts"]:
        artifact = latest.get(declared["key"])
        if not _artifact_covers(artifact, declared):
            problems.append((PROBLEM_REQUIRED_ARTIFACT_UNAVAILABLE,
                             "required artifact %r is %s" % (
                                 declared["key"],
                                 "not recorded" if artifact is None else
                                 "recorded but not available with the approved"
                                 " role and digest")))
    return problems


def evaluate_proof(contract, state, activation_id, now):
    statuses = {}
    for requirement in contract["requirements"]:
        statuses[requirement["key"]] = requirement_status(
            contract, requirement, state, activation_id, now)
    return {
        "satisfied": all(s == REQUIREMENT_SATISFIED for s in statuses.values()),
        "requirements": statuses,
    }


# -- readiness ------------------------------------------------------------


def readiness(contract, state, now):
    latest = {}
    for observation in state["resource_readiness"]:
        latest[observation["resource_key"]] = observation
    resources = {}
    for required in contract["required_resource_readiness"]:
        observation = latest.get(required["resource_key"])
        ready = False
        if observation is not None and observation["status"] == (
            state_module.READINESS_READY
        ):
            age = now - observation["observed_at"]
            ready = 0 <= age <= required["max_age_seconds"]
        resources[required["resource_key"]] = (
            state_module.READINESS_READY if ready else state_module.READINESS_NOT_READY
        )
    return {
        "satisfied": all(v == state_module.READINESS_READY
                         for v in resources.values()),
        "resources": resources,
    }


# -- dependencies ---------------------------------------------------------


def bound_dependency(state, activation_id, key):
    for dependency in state["dependencies"]:
        if dependency["activation_id"] == activation_id and dependency["key"] == key:
            return dependency
    return None


def dependency_status(contract, state, activation_id):
    slots = {}
    for slot in contract["required_dependencies"]:
        dependency = bound_dependency(state, activation_id, slot["key"])
        if dependency is None:
            slots[slot["key"]] = SLOT_UNBOUND
        elif dependency["resolution"] is None:
            slots[slot["key"]] = SLOT_BOUND_UNRESOLVED
        else:
            slots[slot["key"]] = SLOT_RESOLVED
    return {
        "satisfied": all(v == SLOT_RESOLVED for v in slots.values()),
        "slots": slots,
    }


def target_matches(target, reference, registry):
    """Does ``reference`` satisfy the approved ``target`` exactly, given a
    registry view ``{mission_id: {current_revision, revision_digests,
    progress}}``?"""
    form = target["form"]
    if form == record.TARGET_FORM_EXACT_RESOURCE:
        return reference == target["resource_key"]
    entry = registry.get(reference)
    if entry is None:
        return False
    if form == record.TARGET_FORM_EXACT_MISSION:
        return reference == target["mission_id"] and entry["revision_digests"].get(
            target["revision"]) == target["proposal_digest_sha256"]
    return entry["revision_digests"].get(entry["current_revision"]) == (
        target["proposal_digest_sha256"])


def _current_target_revision(target, entry):
    """R-21.2: the revision of the prerequisite that the approved target
    names, PROVIDED the prerequisite's current revision is that revision
    (EXACT_MISSION) or currently carries the declared digest (ELIGIBILITY);
    None when the prerequisite has drifted away from the target."""
    current = entry["current_revision"]
    if target["form"] == record.TARGET_FORM_EXACT_MISSION:
        if current != target["revision"]:
            return None
        return current
    if entry["revision_digests"].get(current) != target["proposal_digest_sha256"]:
        return None
    return current


def prerequisite_problems(contract, state, activation_id, registry):
    """Closure-time prerequisite check for every BOUND required slot (see
    the module docstring, R-21): the historical target must match
    (``mission_state_dependency_target_mismatch``), the prerequisite must
    not have drifted from the declared revision / digest
    (``mission_state_prerequisite_drifted``), and a MISSION prerequisite
    must be COMPLETED with a closure bound to that same revision
    (``mission_state_prerequisite_not_complete``). ``[(problem, detail)]``
    in slot order."""
    problems = []
    for slot in contract["required_dependencies"]:
        dependency = bound_dependency(state, activation_id, slot["key"])
        if dependency is None:
            continue
        target = slot["target"]
        reference = dependency["reference"]
        if slot["kind"] != record.DEPENDENCY_KIND_MISSION:
            if not target_matches(target, reference, registry):
                problems.append((PROBLEM_DEPENDENCY_TARGET_MISMATCH,
                                 "slot %r is bound to %r, which is not the approved"
                                 " resource" % (slot["key"], reference)))
            continue
        entry = registry.get(reference)
        # Historical identity first: the reference must be the declared
        # Mission and (EXACT_MISSION) the declared revision must exist there
        # with the declared digest. ELIGIBILITY has no historical half: its
        # bind-time predicate IS the current-digest predicate, so a
        # non-matching current digest is reported as drift below.
        if entry is None or (
            target["form"] == record.TARGET_FORM_EXACT_MISSION
            and not target_matches(target, reference, registry)
        ):
            problems.append((PROBLEM_DEPENDENCY_TARGET_MISMATCH,
                             "slot %r is bound to %r, which does not match its"
                             " approved target" % (slot["key"], reference)))
            continue
        required_revision = _current_target_revision(target, entry)
        if required_revision is None:
            problems.append((PROBLEM_PREREQUISITE_DRIFTED,
                             "prerequisite mission %s for slot %r is now at"
                             " revision %d, which is not the approved target"
                             % (dependency["reference"], slot["key"],
                                entry["current_revision"])))
            continue
        completed = (
            entry.get("progress") == state_module.PROGRESS_COMPLETED
            and entry.get("closure_activation_id") is not None
            and entry.get("closure_revision") == required_revision
            and entry.get("closure_proposal_digest_sha256") == (
                entry["revision_digests"].get(required_revision))
        )
        if not completed:
            problems.append((PROBLEM_PREREQUISITE_NOT_COMPLETE,
                             "prerequisite mission %s for slot %r is %s and its"
                             " closure is bound to revision %s, not COMPLETED at"
                             " revision %d" % (dependency["reference"], slot["key"],
                                               entry.get("progress"),
                                               entry.get("closure_revision"),
                                               required_revision)))
    return problems


def dependency_graph_problem(mission_state_map):
    """Self-reference or a cycle among MISSION dependencies across every
    state record, as ``(problem, detail)``; None when acyclic. Every
    MISSION dependency is an edge, resolved or not."""
    edges = {}
    for mission_id in sorted(mission_state_map):
        targets = []
        for dependency in mission_state_map[mission_id].get("dependencies", []):
            if dependency["kind"] != record.DEPENDENCY_KIND_MISSION:
                continue
            if dependency["reference"] == mission_id:
                return (state_module.PROBLEM_DEPENDENCY_SELF,
                        "mission %s depends on itself" % mission_id)
            targets.append(dependency["reference"])
        edges[mission_id] = sorted(set(targets))
    white, grey, black = 0, 1, 2
    colour = dict((m, white) for m in edges)
    for start in sorted(edges):
        if colour[start] != white:
            continue
        stack = [(start, iter(edges[start]))]
        colour[start] = grey
        path = [start]
        while stack:
            node, children = stack[-1]
            advanced = False
            for child in children:
                if child not in colour:
                    continue
                if colour[child] == grey:
                    cycle = path[path.index(child):] + [child]
                    return (PROBLEM_DEPENDENCY_CYCLE,
                            "dependency cycle %s" % " -> ".join(cycle))
                if colour[child] == white:
                    colour[child] = grey
                    path.append(child)
                    stack.append((child, iter(edges[child])))
                    advanced = True
                    break
            if not advanced:
                colour[node] = black
                stack.pop()
                path.pop()
    return None


# -- budget, closure, checkpoints -------------------------------------------


def count_operations(state, kind):
    """Applied operations of ``kind`` in the ledger."""
    return sum(1 for e in state["applied_operations"] if e["kind"] == kind)


def budget(contract, state):
    """Derived from the applied-operation LEDGER, which the store
    reconciles two-way against the effect records (R-31.2): deleting a
    continuation or checkpoint record can never restore budget, because
    the document refuses to load, and even the count itself never reads
    the deletable list."""
    declared = contract["continuation_budget"]
    attempts = count_operations(state, state_module.OPERATION_RECORD_CONTINUATION)
    checkpoints = count_operations(state, state_module.OPERATION_RECORD_CHECKPOINT)
    return {
        "attempts_consumed": attempts,
        "attempts_remaining": max(0, declared["max_attempts"] - attempts),
        "checkpoints_consumed": checkpoints,
        "checkpoints_remaining": max(0, declared["max_checkpoints"] - checkpoints),
    }


def attempts_exhausted(contract, state):
    return count_operations(
        state, state_module.OPERATION_RECORD_CONTINUATION
    ) >= contract["continuation_budget"]["max_attempts"]


def local_closure_failures(contract, state, activation_id, now):
    """The locally provable half of closure eligibility (R-24.1), in fixed
    order, each with its own code: proof, required artifacts (R-20.2),
    HARD blockers, unresolved required slots (state-only), readiness. It
    never reads the registry and never reads remaining budget (R-23)."""
    failures = []
    proof = evaluate_proof(contract, state, activation_id, now)
    if not proof["satisfied"]:
        unsatisfied = sorted(k for k, v in proof["requirements"].items()
                             if v != REQUIREMENT_SATISFIED)
        failures.append((PROBLEM_PROOF_NOT_SATISFIED,
                         "requirements not satisfied: %s" % ", ".join(
                             "%s=%s" % (k, proof["requirements"][k])
                             for k in unsatisfied)))
    failures.extend(required_artifact_problems(contract, state))
    hard = state_module.active_hard_blockers(state)
    if hard:
        failures.append((PROBLEM_HARD_BLOCKER_ACTIVE,
                         "active HARD blockers: %s" % ", ".join(
                             b["blocker_id"] for b in hard)))
    slots = dependency_status(contract, state, activation_id)
    if not slots["satisfied"]:
        failures.append((PROBLEM_DEPENDENCY_UNRESOLVED,
                         "required dependency slots not resolved: %s" % ", ".join(
                             "%s=%s" % (k, v) for k, v in sorted(slots["slots"].items())
                             if v != SLOT_RESOLVED)))
    ready = readiness(contract, state, now)
    if not ready["satisfied"]:
        failures.append((PROBLEM_RESOURCE_NOT_READY,
                         "required resources not ready: %s" % ", ".join(
                             k for k, v in sorted(ready["resources"].items())
                             if v != state_module.READINESS_READY)))
    return failures


def closure_failures(contract, state, activation_id, now, registry):
    """Every reason successful closure is not eligible: the local half
    (``local_closure_failures``) followed by the foreign half
    (``prerequisite_problems``). Empty means eligible on these checks; the
    service layers contract staleness and live authority in front."""
    return local_closure_failures(contract, state, activation_id, now) + (
        prerequisite_problems(contract, state, activation_id, registry))


def closure_proof_problem(contract, state, closure):
    """R-24.2 / R-24.3: re-prove a persisted closure from the Mission's
    own state as of the closure's sequence, with ``closed_at`` as the
    clock. Returns a detail string, or None when the closure is provable.
    ``closed_by_caller`` and ``abandoned_by_caller`` assert nothing."""
    as_of = state_as_of(state, closure["sequence"])
    if closure["progress"] == state_module.PROGRESS_COMPLETED:
        failures = local_closure_failures(
            contract, as_of, closure["activation_id"], closure["closed_at"])
        if failures:
            return ("COMPLETED closure is not provable from local state at its"
                    " own sequence and time: %s" % "; ".join(
                        "%s (%s)" % (p, d) for p, d in failures))
        return None
    if closure["reason"] == state_module.CLOSURE_REASON_BUDGET_EXHAUSTED:
        if not attempts_exhausted(contract, as_of):
            return ("closure asserts budget_exhausted but %d of %d attempts were"
                    " consumed at its sequence" % (
                        count_operations(
                            as_of, state_module.OPERATION_RECORD_CONTINUATION),
                        contract["continuation_budget"]["max_attempts"]))
        return None
    if closure["reason"] == state_module.CLOSURE_REASON_HARD_BLOCKER:
        if not state_module.active_hard_blockers(as_of):
            return ("closure asserts hard_blocker_unresolvable but no HARD"
                    " blocker was active at its sequence")
        return None
    return None


def closure_eligibility(contract, state, activation_id, now, registry):
    """The registry-aware, read-time projection (R-22): never stored and
    never part of a checkpoint's derived fields."""
    failures = closure_failures(contract, state, activation_id, now, registry)
    return {
        "eligible": not failures,
        "failures": [{"problem": p, "detail": d} for p, d in failures],
    }


def _declares_mission_prerequisite(contract):
    return any(slot["kind"] == record.DEPENDENCY_KIND_MISSION
               for slot in contract["required_dependencies"])


def derive_checkpoint_fields(contract, state, activation_id, now):
    """The derived half of a checkpoint from the approved contract and
    local accepted state at ``now`` (registry-free; see R-22, R-23)."""
    proof = evaluate_proof(contract, state, activation_id, now)
    artifacts_ok = not required_artifact_problems(contract, state)
    hard = state_module.active_hard_blockers(state)
    slots = dependency_status(contract, state, activation_id)
    ready = readiness(contract, state, now)
    complete = proof["satisfied"] and artifacts_ok and not hard and (
        slots["satisfied"] and ready["satisfied"])
    step = None
    refusal = None
    if complete:
        step = (state_module.NEXT_STEP_CONFIRM_PREREQUISITES_AND_CLOSE
                if _declares_mission_prerequisite(contract)
                else state_module.NEXT_STEP_CLOSE_COMPLETED)
    elif attempts_exhausted(contract, state):
        refusal = {
            "problem": state_module.PROBLEM_BUDGET_EXHAUSTED,
            "detail": "%d of %d continuation attempts consumed and proof is not"
                      " complete; no retry is permitted and only unsuccessful"
                      " closure remains" % (
                          count_operations(
                              state, state_module.OPERATION_RECORD_CONTINUATION),
                          contract["continuation_budget"]["max_attempts"]),
        }
    elif hard:
        step = state_module.NEXT_STEP_RESOLVE_BLOCKERS
    elif not slots["satisfied"]:
        step = state_module.NEXT_STEP_RESOLVE_DEPENDENCIES
    elif not ready["satisfied"]:
        step = state_module.NEXT_STEP_OBSERVE_RESOURCE_READINESS
    elif any(v == REQUIREMENT_SUBMITTED_NOT_ACCEPTED
             for v in proof["requirements"].values()):
        step = state_module.NEXT_STEP_ACCEPT_EVIDENCE
    else:
        step = state_module.NEXT_STEP_SUBMIT_EVIDENCE
    outstanding = [
        d["dependency_id"] for d in state["dependencies"]
        if d["resolution"] is None
    ]
    return {
        "active_blocker_ids": sorted(
            b["blocker_id"] for b in state_module.active_blockers(state)),
        "outstanding_dependency_ids": sorted(outstanding),
        "budget": budget(contract, state),
        "next_permitted_step": step,
        "refusal": refusal,
    }


def checkpoint_disagreement(contract, state, checkpoint):
    """Recompute the checkpoint's derived fields from the state AS OF its
    own sequence with its own ``recorded_at`` as the clock; the detail of
    the first disagreement, or None. A checkpoint beyond the approved
    checkpoint budget disagrees too."""
    as_of = state_as_of(state, checkpoint["sequence"])
    derived = derive_checkpoint_fields(
        contract, as_of, checkpoint["activation_id"], checkpoint["recorded_at"])
    for field in _DERIVED_CHECKPOINT_FIELDS:
        if checkpoint[field] != derived[field]:
            return ("checkpoint %s field %s is %r; recomputation gives %r"
                    % (checkpoint["checkpoint_id"], field, checkpoint[field],
                       derived[field]))
    if derived["budget"]["checkpoints_consumed"] > contract["continuation_budget"][
        "max_checkpoints"
    ]:
        return ("checkpoint %s is number %d; the approved budget permits %d"
                % (checkpoint["checkpoint_id"],
                   derived["budget"]["checkpoints_consumed"],
                   contract["continuation_budget"]["max_checkpoints"]))
    return None
