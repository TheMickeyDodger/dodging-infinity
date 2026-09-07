"""Ledger / effect reconciliation of one Mission State record (R-31).

The applied-operation ledger and the effect records must be mutually
reconcilable, the way Task 4's ``reconcile_mission_history`` requires
stored state to be exactly what the recorded decision history produces.

Outcome schema (R-31.1, R-31.7). Every applied operation's ``outcome`` is
closed and bounded for its kind (``state.OUTCOME_COMMON_KEYS`` plus
``state.OUTCOME_KEYS_BY_KIND``), and every stored value passes the
repository's own type validators BEFORE any equality comparison:
identities through ``require_id`` with the right prefix, integers through
``require_int`` (which rejects bool), booleans through ``require_bool``,
digests through ``require_hex``, nested ``budget`` closed with every value
an int, nested ``refusal`` closed with string members or None, id lists
bounded with every element an id. ``True == 1`` and ``1.0 == 1`` in
Python, so a substitution inside a nested structure would compare equal
to the genuine value; it is refused as malformed instead
(``mission_state_outcome_malformed``).

Reconciliation (R-31.2, R-31.3, R-31.5). For every applied operation:
the outcome's ``operation_id`` and ``sequence`` are its own; its
``progress`` is what the replayed history held AT that sequence
(``progress.progress_at``, never current state); a contract-dependent
operation's provenance revision equals the activation current at its
sequence; and the effect its kind implies exists EXACTLY ONCE and agrees
field by field with the outcome. The other way, every effect record and
nested event has exactly one producing operation of the right kind.
Missing, duplicated, multiply attributed and orphaned effects refuse
(``mission_state_effect_inconsistent``). The identical-rebind no-op
(R-28) is modelled as a first-class case: a ``bind_dependency`` outcome
with ``new_binding: false`` must have NO dependency record naming the
operation and an existing dependency for that slot, under the same
activation, bound to the same reference at an EARLIER sequence.

Invocation binding (R-34). The invocation content digest the service
stored at application time is RE-DERIVED here from the reconciled effect
payload — ``state.invocation_digest(kind, mission_id, sequence - 1,
arguments)`` with the arguments rebuilt from the effect record — and must
equal the stored ``content_digest_sha256``
(``mission_state_invocation_mismatch``). That binds every payload field
of every kind by construction, including fields no outcome carries
(an artifact's ``available``, a claim's ``statement``, a checkpoint's
work lists), with no field list to keep in sync. Completeness: every
kind's invocation is fully re-derivable from stored state —
``expected_sequence`` is the operation's sequence minus one (the service
appends at ``expected_sequence + 1``), ``activate_proof_contract`` takes
no arguments, a ``bind_dependency`` operation is re-derived as
``declare_dependency(kind, reference)`` when the record's slot key is
None and as ``bind_dependency(slot_key, reference)`` otherwise (the
no-op from the outcome), and every other kind's arguments are the
stored effect fields. No kind is partially derivable: there is no
stated hole.

Historical service preconditions (R-39). Service rules that are
reconstructible from stored state hold in history too, evaluated against
the state AS OF the operation's sequence and the contract bound THEN: a
``record_continuation`` recorded while the history was BLOCKED refuses
(the service requires IN_PROGRESS), and the store refuses an artifact
recorded under a required key with a role the bound contract did not
declare (``mission_state_history_impossible``). The store also requires
every operation's cited provenance revision to have EXISTED at that
operation's position (``mission_state_revision_impossible``, R-40) —
existence, never equality with a stale activation or the present
revision, so a caller closure or abandonment after an EDIT stays valid.

Stated limit (R-42): consistency is not authenticity. A digest stored
beside its payload proves INTERNAL CONSISTENCY — that the payload is the
one the recorded invocation described — not that the record is genuine:
an attacker who edits the payload AND recomputes the stored digest
defeats the invocation binding by itself. What still refuses such a
coordinated rewrite is the other reconstructible evidence: the historical
service preconditions above, revision existence, the authority window,
the monotone time base, sequence-to-operation binding, reservation
consumption and ``reconcile_registry``. Nothing here is tamper-proof,
nothing here signs anything, and no trust service, credential or signing
surface exists in this package.

Derived fields (R-31.6). A derived field is reconciled, not merely typed:
``attempt`` equals the ledger's continuation count at that sequence;
checkpoint fields equal the checkpoint record's, which the store
recomputes; ``attempts_remaining`` is reconciled by the store against the
contract bound at that operation (the store holds the contracts). The
per-field classification is in the task evidence.
"""

from mission import progress as progress_module
from mission import record
from mission import state as state_module


def _malformed(where, detail):
    record.fail(state_module.PROBLEM_OUTCOME_MALFORMED, "%s: %s" % (where, detail))


def _inconsistent(where, detail):
    record.fail(state_module.PROBLEM_EFFECT_INCONSISTENT, "%s: %s" % (where, detail))


def _exactly_one(where, candidates, what):
    if len(candidates) != 1:
        _inconsistent(where, "%s has %d %s record(s) naming it; exactly one is"
                      " required" % (where, len(candidates), what))
    return candidates[0]


def _require_equal(where, outcome, key, expected):
    if outcome[key] != expected:
        _inconsistent(where, "outcome.%s is %r but the effect record holds %r"
                      % (key, outcome[key], expected))


_OUTCOME_ID_PREFIXES = {
    "activation_id": record.PROOF_CONTRACT_ID_PREFIX,
    "authorization_id": record.AUTHORIZATION_ID_PREFIX,
    "claim_id": record.CLAIM_ID_PREFIX,
    "artifact_id": record.ARTIFACT_ID_PREFIX,
    "evidence_id": record.EVIDENCE_ID_PREFIX,
    "blocker_id": record.BLOCKER_ID_PREFIX,
    "dependency_id": record.DEPENDENCY_ID_PREFIX,
    "checkpoint_id": record.CHECKPOINT_ID_PREFIX,
}
_OUTCOME_BOOL_KEYS = ("accepted", "invalidated", "resolved", "new_binding")
_OUTCOME_INT_KEYS = ("attempt", "attempts_remaining", "revision")
_OUTCOME_OPTIONAL_STR_KEYS = ("key", "slot_key", "next_permitted_step", "detail")


def _typed_outcome_value(where, key, value):
    """R-31.7: every stored outcome value passes the repository's own type
    validators BEFORE any equality comparison, so bool-for-int and
    float-for-int substitutions are refused, never compared away. Nested
    structures are closed and typed."""
    location = "%s.%s" % (where, key)
    if key in _OUTCOME_ID_PREFIXES:
        record.require_id(value, _OUTCOME_ID_PREFIXES[key], location)
    elif key in _OUTCOME_BOOL_KEYS:
        record.require_bool(value, location)
    elif key in _OUTCOME_INT_KEYS:
        record.require_int(value, location, minimum=0)
    elif key.endswith("_sha256"):
        record.require_hex(value, location, 64)
    elif key == "active_blocker_ids":
        _typed_id_list(location, value, record.BLOCKER_ID_PREFIX,
                       state_module.MAX_BLOCKER_RECORDS)
    elif key == "outstanding_dependency_ids":
        _typed_id_list(location, value, record.DEPENDENCY_ID_PREFIX,
                       state_module.MAX_DEPENDENCY_RECORDS)
    elif key == "budget":
        record.require_dict(value, location)
        record.require_closed_keys(value, state_module.CHECKPOINT_BUDGET_KEYS, location)
        for name in state_module.CHECKPOINT_BUDGET_KEYS:
            record.require_int(value[name], "%s.%s" % (location, name), minimum=0)
    elif key == "refusal":
        if value is not None:
            record.require_dict(value, location)
            record.require_closed_keys(value, state_module.REFUSAL_KEYS, location)
            record.require_str(value["problem"], location + ".problem",
                               record.MAX_PRINCIPAL_REF_CHARS)
            record.require_str(value["detail"], location + ".detail",
                               state_module.MAX_REFUSAL_DETAIL_CHARS)
    elif key in _OUTCOME_OPTIONAL_STR_KEYS:
        if value is not None:
            record.require_str(value, location, state_module.MAX_CLAIM_STATEMENT_CHARS,
                               allow_empty=True)
    else:
        record.require_str(value, location, state_module.MAX_RESOURCE_REFERENCE_CHARS)


def _typed_id_list(location, value, prefix, max_items):
    if not isinstance(value, list):
        record.fail(record.PROBLEM_BAD_TYPE, "%s must be a list" % location)
    if len(value) > max_items:
        record.fail(record.PROBLEM_TOO_LARGE, "%s exceeds %d entries" % (location, max_items))
    for index, item in enumerate(value):
        record.require_id(item, prefix, "%s[%d]" % (location, index))
    if value != sorted(set(value)):
        record.fail(record.PROBLEM_BAD_VALUE, "%s must be sorted and duplicate-free"
                    % location)


def _validate_outcome_shape(where, operation):
    """R-31.1 / R-31.7: closed, bounded, TYPED outcome for the operation's
    kind, validated before anything is compared."""
    outcome = operation["outcome"]
    kind = operation["kind"]
    allowed = state_module.OUTCOME_COMMON_KEYS + state_module.OUTCOME_KEYS_BY_KIND[kind]
    try:
        record.require_closed_keys(outcome, allowed, where + ".outcome")
        record.require_id(outcome["mission_id"], record.MISSION_ID_PREFIX,
                          where + ".outcome.mission_id")
        record.require_id(outcome["operation_id"], record.STATE_OPERATION_ID_PREFIX,
                          where + ".outcome.operation_id")
        record.require_int(outcome["sequence"], where + ".outcome.sequence", minimum=1)
        state_module.require_progress(outcome["progress"], where + ".outcome.progress")
        for key in state_module.OUTCOME_KEYS_BY_KIND[kind]:
            _typed_outcome_value(where + ".outcome", key, outcome[key])
    except record.MissionError as exc:
        _malformed(where + ".outcome", "%s: %s" % (exc.problem, exc))
    if outcome["operation_id"] != operation["operation_id"]:
        _malformed(where + ".outcome", "names operation %s, not its own %s"
                   % (outcome["operation_id"], operation["operation_id"]))
    if outcome["sequence"] != operation["sequence"]:
        _malformed(where + ".outcome", "sequence %r is not the operation's %d"
                   % (outcome["sequence"], operation["sequence"]))
    return outcome


def _nested(records, event, operation_id):
    return [r for r in records
            if r[event] is not None and r[event]["operation_id"] == operation_id]


def _invocation_arguments(kind, effect, outcome, state):
    """The service's invocation ``arguments`` rebuilt from the effect (see
    ``state_service``): one shape per kind, identical to what the service
    digested at application time."""
    if kind == state_module.OPERATION_ACTIVATE_CONTRACT:
        return {}
    if kind == state_module.OPERATION_RECORD_CLAIM:
        return {"requirement_key": effect["requirement_key"],
                "statement": effect["statement"]}
    if kind == state_module.OPERATION_RECORD_ARTIFACT:
        return {"key": effect["key"], "role": effect["role"],
                "locator_kind": effect["locator_kind"], "locator": effect["locator"],
                "content_digest_sha256": effect["content_digest_sha256"],
                "available": effect["available"],
                "derived_from": list(effect["derived_from"])}
    if kind == state_module.OPERATION_SUBMIT_EVIDENCE:
        return {"requirement_key": effect["requirement_key"], "kind": effect["kind"],
                "content_digest_sha256": effect["content_digest_sha256"],
                "artifact_ids": list(effect["artifact_ids"])}
    if kind == state_module.OPERATION_ACCEPT_EVIDENCE:
        return {"evidence_id": effect["evidence_id"],
                "content_digest_sha256": effect["acceptance"]["content_digest_sha256"]}
    if kind == state_module.OPERATION_INVALIDATE_EVIDENCE:
        return {"evidence_id": effect["evidence_id"],
                "reason": effect["invalidation"]["reason"]}
    if kind == state_module.OPERATION_OPEN_BLOCKER:
        return {"key": effect["key"], "description": effect["description"]}
    if kind == state_module.OPERATION_RESOLVE_BLOCKER:
        return {"blocker_id": effect["blocker_id"],
                "evidence_id": effect["resolution"]["evidence_id"]}
    if kind == state_module.OPERATION_BIND_DEPENDENCY:
        if effect is None:
            # The identical-rebind no-op: the invocation was bind_dependency.
            return {"slot_key": outcome["slot_key"], "reference": outcome["reference"]}
        if effect["key"] is None:
            return {"kind": effect["kind"], "reference": effect["reference"]}
        return {"slot_key": effect["key"], "reference": effect["reference"]}
    if kind == state_module.OPERATION_RESOLVE_DEPENDENCY:
        return {"dependency_id": effect["dependency_id"],
                "evidence_id": effect["resolution"]["evidence_id"]}
    if kind == state_module.OPERATION_OBSERVE_RESOURCE_READINESS:
        return {"resource_key": effect["resource_key"], "status": effect["status"],
                "observed_at": effect["observed_at"]}
    if kind == state_module.OPERATION_RECORD_CONTINUATION:
        return {"reason": effect["reason"]}
    if kind == state_module.OPERATION_RECORD_CHECKPOINT:
        return {"completed_work": list(effect["completed_work"]),
                "outstanding_work": list(effect["outstanding_work"]),
                "retry_condition": effect["retry_condition"],
                "stop_condition": effect["stop_condition"]}
    if kind == state_module.OPERATION_COMPLETE:
        return {"detail": effect["detail"]}
    if kind == state_module.OPERATION_CLOSE_UNSUCCESSFUL:
        return {"reason": effect["reason"], "detail": effect["detail"]}
    return {"detail": effect["detail"]}


def _bind_invocation(where, operation, effect, outcome, mission_id, state):
    """R-34: the stored invocation digest equals the one re-derived from
    the reconciled effect payload."""
    expected = state_module.invocation_digest(
        operation["kind"], mission_id, operation["sequence"] - 1,
        _invocation_arguments(operation["kind"], effect, outcome, state))
    if operation["content_digest_sha256"] != expected:
        record.fail(state_module.PROBLEM_INVOCATION_MISMATCH,
                    "%s: the stored invocation digest does not equal the digest"
                    " re-derived from the effect payload; a payload field was"
                    " changed after the invocation was recorded" % where)


def reconcile_effects(state, location):
    """R-31.2 / R-31.3 / R-31.5: forward (every operation -> exactly one
    effect, agreeing with the outcome, at the history's progress) and
    reverse (every effect -> exactly one operation). Called by
    ``state_validation.validate_state_record`` after every list has been
    validated, so on every load and every save."""
    mission_id = state["mission_id"]
    by_operation = {
        "contract_activations": {}, "claims": {}, "artifacts": {}, "evidence": {},
        "blockers": {}, "dependencies": {}, "resource_readiness": {},
        "checkpoints": {}, "continuations": {},
    }
    for name in by_operation:
        for entry in state[name]:
            by_operation[name].setdefault(entry["operation_id"], []).append(entry)
    for index, operation in enumerate(state["applied_operations"]):
        where = "%s.applied_operations[%d]" % (location, index)
        outcome = _validate_outcome_shape(where, operation)
        kind = operation["kind"]
        operation_id = operation["operation_id"]
        if outcome["mission_id"] != mission_id:
            _malformed(where + ".outcome", "names another mission")
        # Historical progress: what the replayed history held AT this
        # sequence, never current state.
        held = progress_module.progress_at(state, operation["sequence"])
        if outcome["progress"] != held:
            _inconsistent(where, "outcome.progress %r but the history at sequence"
                          " %d held %r" % (outcome["progress"],
                                           operation["sequence"], held))
        if kind in state_module.CONTRACT_DEPENDENT_KINDS:
            current = None
            for activation in state["contract_activations"]:
                if activation["sequence"] < operation["sequence"]:
                    current = activation
            if current is None:
                _inconsistent(where, "%s applied with no contract activation"
                              " before sequence %d" % (kind, operation["sequence"]))
            if operation["provenance"]["revision"] != current["revision"]:
                record.fail(state_module.PROBLEM_PROVENANCE_MISMATCH,
                            "%s.provenance.revision %d is not the revision %d of"
                            " the activation current at its sequence"
                            % (where, operation["provenance"]["revision"],
                               current["revision"]))

        def direct(name):
            return _exactly_one(where, by_operation[name].get(operation_id, []), name)

        effect = None
        if kind == state_module.OPERATION_ACTIVATE_CONTRACT:
            effect = direct("contract_activations")
            for key in ("activation_id", "revision", "proposal_digest_sha256",
                        "contract_digest_sha256", "authorization_id"):
                _require_equal(where, outcome, key, effect[key])
        elif kind == state_module.OPERATION_RECORD_CLAIM:
            effect = direct("claims")
            _require_equal(where, outcome, "claim_id", effect["claim_id"])
            _require_equal(where, outcome, "requirement_key", effect["requirement_key"])
        elif kind == state_module.OPERATION_RECORD_ARTIFACT:
            effect = direct("artifacts")
            for key in ("artifact_id", "key", "role"):
                _require_equal(where, outcome, key, effect[key])
        elif kind == state_module.OPERATION_SUBMIT_EVIDENCE:
            effect = direct("evidence")
            for key in ("evidence_id", "requirement_key", "kind"):
                _require_equal(where, outcome, key, effect[key])
            _require_equal(where, outcome, "accepted", False)
        elif kind == state_module.OPERATION_ACCEPT_EVIDENCE:
            effect = _exactly_one(where, _nested(state["evidence"], "acceptance",
                                                 operation_id), "acceptance")
            _require_equal(where, outcome, "evidence_id", effect["evidence_id"])
            _require_equal(where, outcome, "accepted", True)
        elif kind == state_module.OPERATION_INVALIDATE_EVIDENCE:
            effect = _exactly_one(where, _nested(state["evidence"], "invalidation",
                                                 operation_id), "invalidation")
            _require_equal(where, outcome, "evidence_id", effect["evidence_id"])
            _require_equal(where, outcome, "invalidated", True)
        elif kind == state_module.OPERATION_OPEN_BLOCKER:
            effect = direct("blockers")
            for key in ("blocker_id", "key", "severity"):
                _require_equal(where, outcome, key, effect[key])
        elif kind == state_module.OPERATION_RESOLVE_BLOCKER:
            effect = _exactly_one(where, _nested(state["blockers"], "resolution",
                                                 operation_id), "blocker resolution")
            _require_equal(where, outcome, "blocker_id", effect["blocker_id"])
            _require_equal(where, outcome, "resolved", True)
            _require_equal(where, outcome, "evidence_id",
                           effect["resolution"]["evidence_id"])
        elif kind == state_module.OPERATION_BIND_DEPENDENCY:
            named = by_operation["dependencies"].get(operation_id, [])
            if outcome["new_binding"]:
                effect = _exactly_one(where, named, "dependency")
                _require_equal(where, outcome, "dependency_id", effect["dependency_id"])
                _require_equal(where, outcome, "slot_key", effect["key"])
                _require_equal(where, outcome, "reference", effect["reference"])
            else:
                # R-28 / R-31.3: the identical-rebind no-op. No dependency
                # names this operation, and the slot was already bound,
                # under the same activation, at an EARLIER sequence to the
                # same reference.
                if named:
                    _inconsistent(where, "a no-op rebind outcome but %d dependency"
                                  " record(s) name the operation" % len(named))
                if outcome["slot_key"] is None:
                    _malformed(where + ".outcome", "a no-op rebind must name a slot")
                earlier = [
                    d for d in state["dependencies"]
                    if d["dependency_id"] == outcome["dependency_id"]
                    and d["key"] == outcome["slot_key"]
                    and d["reference"] == outcome["reference"]
                    and d["sequence"] < operation["sequence"]
                    and d["activation_id"] == current["activation_id"]
                ]
                if len(earlier) != 1:
                    _inconsistent(where, "no-op rebind of slot %r has no earlier"
                                  " binding to %r under the same activation"
                                  % (outcome["slot_key"], outcome["reference"]))
                effect = None
        elif kind == state_module.OPERATION_RESOLVE_DEPENDENCY:
            effect = _exactly_one(where, _nested(state["dependencies"], "resolution",
                                                 operation_id), "dependency resolution")
            _require_equal(where, outcome, "dependency_id", effect["dependency_id"])
            _require_equal(where, outcome, "resolved", True)
            _require_equal(where, outcome, "evidence_id",
                           effect["resolution"]["evidence_id"])
        elif kind == state_module.OPERATION_OBSERVE_RESOURCE_READINESS:
            effect = direct("resource_readiness")
            _require_equal(where, outcome, "resource_key", effect["resource_key"])
            _require_equal(where, outcome, "status", effect["status"])
        elif kind == state_module.OPERATION_RECORD_CONTINUATION:
            effect = direct("continuations")
            # R-39: the service records a continuation only while
            # IN_PROGRESS; the history just before this operation must
            # agree.
            before = progress_module.progress_at(state, operation["sequence"] - 1)
            if before != state_module.PROGRESS_IN_PROGRESS:
                record.fail(state_module.PROBLEM_HISTORY_IMPOSSIBLE,
                            "%s records a continuation while the history was %s;"
                            " the service accepts one only while IN_PROGRESS"
                            % (where, before))
            _require_equal(where, outcome, "attempt", effect["attempt"])
            consumed = sum(
                1 for e in state["applied_operations"]
                if e["kind"] == state_module.OPERATION_RECORD_CONTINUATION
                and e["sequence"] <= operation["sequence"])
            if effect["attempt"] != consumed:
                _inconsistent(where, "continuation attempt %d but the ledger holds"
                              " %d continuation operations at that sequence"
                              % (effect["attempt"], consumed))
        elif kind == state_module.OPERATION_RECORD_CHECKPOINT:
            effect = direct("checkpoints")
            for key in ("checkpoint_id", "next_permitted_step", "refusal", "budget",
                        "active_blocker_ids", "outstanding_dependency_ids"):
                _require_equal(where, outcome, key, effect[key])
        else:
            closure = state["closure"]
            if closure is None or closure["operation_id"] != operation_id:
                _inconsistent(where, "%s operation without the closure it produced"
                              % kind)
            _require_equal(where, outcome, "reason", closure["reason"])
            _require_equal(where, outcome, "detail", closure["detail"])
            if state_module.CLOSING_OPERATIONS[kind] != closure["progress"]:
                _inconsistent(where, "%s operation but closure progress %s"
                              % (kind, closure["progress"]))
            effect = closure
            # R-36: an asserting unsuccessful closure is contract-dependent.
            if kind == state_module.OPERATION_CLOSE_UNSUCCESSFUL and (
                state_module.closure_is_contract_dependent(closure)
            ):
                bound = None
                for activation in state["contract_activations"]:
                    if activation["sequence"] < operation["sequence"]:
                        bound = activation
                if bound is None or operation["provenance"]["revision"] != bound["revision"]:
                    record.fail(state_module.PROBLEM_PROVENANCE_MISMATCH,
                                "%s asserts %s but its provenance revision is not the"
                                " bound activation's revision"
                                % (where, closure["reason"]))
        _bind_invocation(where, operation, effect, outcome, mission_id, state)
    # Reverse: every effect record and nested event has exactly one
    # producing operation of the right kind (the per-record binding
    # checks above guarantee existence and kind; here the COUNT).
    expected_kind = {
        "contract_activations": state_module.OPERATION_ACTIVATE_CONTRACT,
        "claims": state_module.OPERATION_RECORD_CLAIM,
        "artifacts": state_module.OPERATION_RECORD_ARTIFACT,
        "evidence": state_module.OPERATION_SUBMIT_EVIDENCE,
        "blockers": state_module.OPERATION_OPEN_BLOCKER,
        "dependencies": state_module.OPERATION_BIND_DEPENDENCY,
        "resource_readiness": state_module.OPERATION_OBSERVE_RESOURCE_READINESS,
        "checkpoints": state_module.OPERATION_RECORD_CHECKPOINT,
        "continuations": state_module.OPERATION_RECORD_CONTINUATION,
    }
    ledger = dict((e["operation_id"], e["kind"]) for e in state["applied_operations"])
    for name, kind in expected_kind.items():
        for operation_id, entries in by_operation[name].items():
            if ledger.get(operation_id) != kind or len(entries) != 1:
                _inconsistent("%s.%s" % (location, name),
                              "%d record(s) attributed to operation %s (%s)"
                              % (len(entries), operation_id, ledger.get(operation_id)))
    for records, event, kind in (
        (state["evidence"], "acceptance", state_module.OPERATION_ACCEPT_EVIDENCE),
        (state["evidence"], "invalidation", state_module.OPERATION_INVALIDATE_EVIDENCE),
        (state["blockers"], "resolution", state_module.OPERATION_RESOLVE_BLOCKER),
        (state["dependencies"], "resolution", state_module.OPERATION_RESOLVE_DEPENDENCY),
    ):
        seen = {}
        for entry in records:
            if entry[event] is None:
                continue
            operation_id = entry[event]["operation_id"]
            seen[operation_id] = seen.get(operation_id, 0) + 1
            if ledger.get(operation_id) != kind:
                _inconsistent(location, "%s event attributed to operation %s of"
                              " kind %s" % (event, operation_id, ledger.get(operation_id)))
        for operation_id, count in seen.items():
            if count != 1:
                _inconsistent(location, "%s event reused across %d records for"
                              " operation %s" % (event, count, operation_id))
    if state["closure"] is not None and ledger.get(state["closure"]["operation_id"]) not in (
        state_module.CLOSING_OPERATIONS
    ):
        _inconsistent(location, "closure attributed to a non-closing operation")
