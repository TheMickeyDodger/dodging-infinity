"""Closed validation of one Mission State record (see ``mission.state``
for the shapes, vocabularies, bounds and constructors it validates).

``validate_state_record`` checks every field, bound and INTRA-record
binding: closed key sets; identity grammar; a ``sequence`` equal to the
number of applied operations, which are exact and contiguous; every
sub-record and nested event naming the applied operation that produced
it, with that operation's kind and sequence and a provenance block
referencing it; contract-dependent records binding the activation that
was current at their sequence; acceptance digests equal to submission
digests and never on a non-satisfying evidence kind; resolutions naming
ACCEPTED evidence; artifacts deriving only from earlier artifacts; one
binding per dependency slot per activation; contiguous continuation
attempts; checkpoints carrying exactly one of next step / refusal with
refs equal to their activation; a closure present exactly when progress
is terminal, with a reason from that outcome's closed table, produced by
the last operation and bound to the activation current at closure; and
progress agreeing with the record (NOT_STARTED has no activation,
BLOCKED if and only if an active HARD blocker exists).

Provenance truthfulness (R-33, R-33a). A sub-record's or nested
event's provenance block must EQUAL the provenance of the operation that
produced it: the same authenticated context across all four
``CONTEXT_KEYS``, the same ``mission_id``, the same ``revision`` and the
same recording time. For a contract-dependent operation that revision
must also equal the revision of the activation current at its sequence.
Refusal is ``mission_state_provenance_mismatch``. This is truthfulness,
not separation of duties (R-27): the same principal may submit and
accept; an event cannot be attributed to a principal that did not
perform it.

Resolution validity (R-32). A blocker or dependency resolution may not
name evidence whose invalidation is at or before the resolution's
sequence (``mission_state_evidence_invalidated``). Evidence invalidated
LATER keeps its historical resolution readable: the comparison is by
sequence, never by current status.

Ledger / effect reconciliation (R-31). After every list is validated,
``_reconcile_effects`` proves, for every applied operation, that its
outcome is closed and well-typed for its kind, that every id it names
resolves inside the record to a record of the right kind, that its
``operation_id``/``sequence`` are its own, that its recorded ``progress``
is exactly what the replayed history held at that sequence
(``progress.state_as_of``, never current state), and that the effect its
kind implies exists EXACTLY ONCE and binds back to it; and, the other
way, that every effect record and nested event has exactly one producing
operation. Missing, duplicated, multiply attributed and orphaned effects
refuse (``mission_state_effect_inconsistent``); a malformed outcome
refuses (``mission_state_outcome_malformed``) before any write. The
identical-rebind no-op (R-28) is a first-class case: a ``bind_dependency``
operation whose validated outcome says ``new_binding: false`` must have
NO dependency record naming it and an existing dependency for that slot,
under the same activation, bound at an EARLIER sequence. Budget is derived
from the ledger the reconciliation proves.

Cross-DOCUMENT bindings (the Mission, its revisions and authorizations,
the contract content, foreign Missions, reservations) are the store's
job (``mission.store``), which calls this first.
"""

from mission import record
from mission import state as state_module
from mission import state_reconcile




class _Bindings(object):
    """The per-record context every sub-record is checked against."""

    def __init__(self, state, location):
        self.state = state
        self.location = location
        self.mission_id = state["mission_id"]
        self.operations = {}
        self.activation_ids = set()
        self.artifact_ids = []
        self.evidence = {}
        self.blocker_ids = set()
        self.dependency_ids = set()


def _bound_list(value, location, max_items, problem=state_module.PROBLEM_STATE_FULL):
    """A list within a module-constant bound. Record histories refuse with
    ``mission_state_full`` (never evicted); per-record input lists refuse
    with ``mission_too_large`` (never truncated)."""
    if not isinstance(value, list):
        record.fail(record.PROBLEM_BAD_TYPE,
                    "%s must be a list, not %s" % (location, type(value).__name__))
    if len(value) > max_items:
        record.fail(problem,
                    "%s holds %d entries; the hard bound is %d and the record"
                    " is refused, not truncated" % (location, len(value),
                                                    max_items))
    return value


def _require_provenance(bindings, value, operation_id, location,
                        operation=None):
    provenance = record.validate_provenance(value, location)
    if provenance["reference_kind"] != record.REFERENCE_KIND_STATE_OPERATION or (
        provenance["reference_id"] != operation_id
        or provenance["mission_id"] != bindings.mission_id
    ):
        record.fail(record.PROBLEM_PROVENANCE,
                    "%s must reference state operation %s of mission %s"
                    % (location, operation_id, bindings.mission_id))
    if operation is not None and provenance != operation["provenance"]:
        # R-33 / R-33a: same context (all four keys), mission, revision
        # and recording time as the operation that produced the event.
        differing = sorted(key for key in provenance
                           if provenance[key] != operation["provenance"].get(key))
        record.fail(state_module.PROBLEM_PROVENANCE_MISMATCH,
                    "%s attributes the event differently from operation %s"
                    " (differs in %s); an event cannot be attributed to a"
                    " principal, revision or time other than its operation's"
                    % (location, operation_id, ", ".join(differing)))
    return provenance


def _require_operation(bindings, value, kind, location, time_field=None,
                       at_or_before=False):
    """The sub-record (or nested event) ``value`` names an applied
    operation of ``kind`` and carries exactly that operation's sequence.
    With ``time_field``, that recording timestamp must equal the
    operation's ``applied_at`` (or, with ``at_or_before``, not exceed it:
    the readiness observation case) — R-25.3."""
    operation_id = value["operation_id"]
    record.require_id(operation_id, record.STATE_OPERATION_ID_PREFIX,
                      location + ".operation_id")
    operation = bindings.operations.get(operation_id)
    if operation is None or operation["kind"] != kind:
        record.fail(state_module.PROBLEM_OPERATION_BINDING,
                    "%s names operation %s, which is not an applied %s"
                    " operation of this record" % (location, operation_id, kind))
    # R-37: the stored sequence is an int (bool and float refused) BEFORE
    # it is compared with anything.
    record.require_int(value["sequence"], location + ".sequence", minimum=1)
    if value["sequence"] != operation["sequence"]:
        record.fail(state_module.PROBLEM_OPERATION_BINDING,
                    "%s.sequence %r is not the sequence %d of operation %s"
                    % (location, value["sequence"], operation["sequence"],
                       operation_id))
    _require_provenance(bindings, value["provenance"], operation_id,
                        location + ".provenance", operation)
    if time_field is not None:
        recorded = record.require_timestamp(value[time_field],
                                            "%s.%s" % (location, time_field))
        if at_or_before:
            if recorded > operation["applied_at"]:
                record.fail(state_module.PROBLEM_TIME_INCONSISTENT,
                            "%s.%s %d is later than the %d at which operation %s"
                            " was applied; a future-dated observation is refused"
                            % (location, time_field, recorded,
                               operation["applied_at"], operation_id))
        elif recorded != operation["applied_at"]:
            record.fail(state_module.PROBLEM_TIME_INCONSISTENT,
                        "%s.%s %d is not the time %d at which operation %s was"
                        " applied" % (location, time_field, recorded,
                                      operation["applied_at"], operation_id))
    return operation


def _require_activation(bindings, value, location, revision_bound=True):
    """``value`` binds the activation that was current at its sequence;
    with ``revision_bound`` (every contract-dependent record) its
    provenance revision equals that activation's revision (R-33a)."""
    activation_id = value["activation_id"]
    record.require_id(activation_id, record.PROOF_CONTRACT_ID_PREFIX,
                      location + ".activation_id")
    current = None
    for activation in bindings.state["contract_activations"]:
        if activation["sequence"] < value["sequence"]:
            current = activation
    if current is None or current["activation_id"] != activation_id:
        record.fail(state_module.PROBLEM_ACTIVATION_BINDING,
                    "%s binds activation %s, but the activation current at"
                    " sequence %d is %s"
                    % (location, activation_id, value["sequence"],
                       None if current is None else current["activation_id"]))
    if revision_bound and value["provenance"]["revision"] != current["revision"]:
        record.fail(state_module.PROBLEM_PROVENANCE_MISMATCH,
                    "%s.provenance.revision %d is not the revision %d of the"
                    " activation it binds"
                    % (location, value["provenance"]["revision"],
                       current["revision"]))
    return current


def _validate_applied_operations(bindings):
    state = bindings.state
    location = bindings.location + ".applied_operations"
    operations = _bound_list(state["applied_operations"], location,
                             state_module.MAX_APPLIED_OPERATIONS)
    last_applied = 0
    for index, entry in enumerate(operations):
        where = "%s[%d]" % (location, index)
        record.require_dict(entry, where)
        record.require_closed_keys(entry, state_module.APPLIED_OPERATION_KEYS, where)
        operation_id = record.require_id(
            entry["operation_id"], record.STATE_OPERATION_ID_PREFIX,
            where + ".operation_id",
        )
        if operation_id in bindings.operations:
            record.fail(state_module.PROBLEM_OPERATION_BINDING,
                        "%s repeats operation id %s" % (where, operation_id))
        record.require_member(entry["kind"], state_module.OPERATION_KINDS, where + ".kind")
        record.require_hex(entry["content_digest_sha256"],
                           where + ".content_digest_sha256", 64)
        applied = record.require_timestamp(entry["applied_at"], where + ".applied_at")
        if applied < last_applied:
            record.fail(state_module.PROBLEM_TIME_INCONSISTENT,
                        "%s.applied_at %d precedes the previous operation's %d;"
                        " operation times are non-decreasing in sequence order"
                        % (where, applied, last_applied))
        last_applied = applied
        record.require_int(entry["sequence"], where + ".sequence", minimum=1)
        if entry["sequence"] != index + 1:
            record.fail(state_module.PROBLEM_SEQUENCE,
                        "%s.sequence must be %d: applied operations are exact"
                        " and contiguous" % (where, index + 1))
        provenance = _require_provenance(bindings, entry["provenance"],
                                         operation_id, where + ".provenance")
        if provenance["received_at"] != applied:
            record.fail(state_module.PROBLEM_TIME_INCONSISTENT,
                        "%s.provenance.received_at %d is not the operation's"
                        " applied_at %d" % (where, provenance["received_at"],
                                            applied))
        record.require_dict(entry["outcome"], where + ".outcome")
        bindings.operations[operation_id] = entry
    if state["sequence"] != len(operations):
        record.fail(state_module.PROBLEM_SEQUENCE,
                    "%s.sequence %r is not the number of applied operations %d"
                    % (bindings.location, state["sequence"], len(operations)))


def _validate_activations(bindings):
    state = bindings.state
    location = bindings.location + ".contract_activations"
    entries = _bound_list(state["contract_activations"], location,
                          state_module.MAX_CONTRACT_ACTIVATIONS)
    previous = None
    for index, entry in enumerate(entries):
        where = "%s[%d]" % (location, index)
        record.require_dict(entry, where)
        record.require_closed_keys(entry, state_module.ACTIVATION_KEYS, where)
        activation_id = record.require_id(
            entry["activation_id"], record.PROOF_CONTRACT_ID_PREFIX,
            where + ".activation_id",
        )
        if activation_id in bindings.activation_ids:
            record.fail(state_module.PROBLEM_ACTIVATION_ORDER,
                        "%s repeats activation id %s" % (where, activation_id))
        record.require_int(entry["revision"], where + ".revision", minimum=1)
        record.require_hex(entry["proposal_digest_sha256"],
                           where + ".proposal_digest_sha256", 64)
        record.require_id(entry["authorization_id"],
                          record.AUTHORIZATION_ID_PREFIX,
                          where + ".authorization_id")
        record.require_hex(entry["authorization_digest_sha256"],
                           where + ".authorization_digest_sha256", 64)
        record.require_hex(entry["contract_digest_sha256"],
                           where + ".contract_digest_sha256", 64)
        _require_operation(bindings, entry, state_module.OPERATION_ACTIVATE_CONTRACT, where,
                           time_field="activated_at")
        if entry["provenance"]["revision"] != entry["revision"]:
            record.fail(state_module.PROBLEM_PROVENANCE_MISMATCH,
                        "%s.provenance.revision %d is not the revision %d it"
                        " activates" % (where, entry["provenance"]["revision"],
                                        entry["revision"]))
        if previous is not None and (
            entry["revision"] <= previous["revision"]
            or entry["sequence"] <= previous["sequence"]
        ):
            record.fail(state_module.PROBLEM_ACTIVATION_ORDER,
                        "%s activates revision %d after revision %d: an"
                        " activation is append-only and one per revision,"
                        " strictly later" % (where, entry["revision"],
                                             previous["revision"]))
        bindings.activation_ids.add(activation_id)
        previous = entry


def _validate_ordered(bindings, name, max_items, keys, validator):
    """A list of closed records in strictly increasing sequence order."""
    state = bindings.state
    location = "%s.%s" % (bindings.location, name)
    entries = _bound_list(state[name], location, max_items)
    last_sequence = 0
    for index, entry in enumerate(entries):
        where = "%s[%d]" % (location, index)
        record.require_dict(entry, where)
        record.require_closed_keys(entry, keys, where)
        record.require_int(entry["sequence"], where + ".sequence", minimum=1)
        if entry["sequence"] <= last_sequence:
            record.fail(state_module.PROBLEM_SEQUENCE,
                        "%s is out of sequence order (append-only)" % where)
        last_sequence = entry["sequence"]
        validator(bindings, entry, where)


def _validate_claim(bindings, entry, where):
    record.require_id(entry["claim_id"], record.CLAIM_ID_PREFIX, where + ".claim_id")
    _require_operation(bindings, entry, state_module.OPERATION_RECORD_CLAIM, where,
                       time_field="claimed_at")
    _require_activation(bindings, entry, where)
    record.require_contract_key(entry["requirement_key"], where + ".requirement_key")
    record.require_str(entry["statement"], where + ".statement",
                       state_module.MAX_CLAIM_STATEMENT_CHARS)


def _validate_artifact(bindings, entry, where):
    artifact_id = record.require_id(entry["artifact_id"], record.ARTIFACT_ID_PREFIX,
                                    where + ".artifact_id")
    _require_operation(bindings, entry, state_module.OPERATION_RECORD_ARTIFACT, where,
                       time_field="recorded_at")
    if entry["key"] is not None:
        record.require_contract_key(entry["key"], where + ".key")
    record.require_member(entry["role"], record.ARTIFACT_ROLES, where + ".role")
    record.require_member(entry["locator_kind"], state_module.LOCATOR_KINDS,
                          where + ".locator_kind")
    record.require_str(entry["locator"], where + ".locator", state_module.MAX_LOCATOR_CHARS)
    if entry["content_digest_sha256"] is not None:
        record.require_hex(entry["content_digest_sha256"],
                           where + ".content_digest_sha256", 64)
    record.require_bool(entry["available"], where + ".available")
    links = _bound_list(entry["derived_from"], where + ".derived_from",
                        state_module.MAX_ARTIFACT_LINKS, record.PROBLEM_TOO_LARGE)
    if links != sorted(set(links)):
        record.fail(record.PROBLEM_BAD_VALUE,
                    "%s.derived_from must be sorted and duplicate-free" % where)
    if entry["role"] == record.ARTIFACT_ROLE_ORIGINAL_INPUT and links:
        record.fail(state_module.PROBLEM_ARTIFACT_DERIVATION,
                    "%s is an original input and cannot derive from anything"
                    % where)
    for index, link in enumerate(links):
        record.require_id(link, record.ARTIFACT_ID_PREFIX,
                          "%s.derived_from[%d]" % (where, index))
        if link == artifact_id:
            record.fail(state_module.PROBLEM_ARTIFACT_DERIVATION,
                        "%s derives from itself" % where)
        if link not in bindings.artifact_ids:
            record.fail(state_module.PROBLEM_UNKNOWN_ARTIFACT,
                        "%s.derived_from names %s, which is not an EARLIER"
                        " artifact of this record" % (where, link))
    if artifact_id in bindings.artifact_ids:
        record.fail(record.PROBLEM_BAD_VALUE,
                    "%s repeats artifact id %s" % (where, artifact_id))
    bindings.artifact_ids.append(artifact_id)


def _validate_evidence(bindings, entry, where):
    evidence_id = record.require_id(entry["evidence_id"], record.EVIDENCE_ID_PREFIX,
                                    where + ".evidence_id")
    _require_operation(bindings, entry, state_module.OPERATION_SUBMIT_EVIDENCE, where,
                       time_field="submitted_at")
    _require_activation(bindings, entry, where)
    record.require_contract_key(entry["requirement_key"], where + ".requirement_key")
    record.require_member(entry["kind"], record.EVIDENCE_KINDS, where + ".kind")
    record.require_hex(entry["content_digest_sha256"],
                       where + ".content_digest_sha256", 64)
    artifact_ids = _bound_list(entry["artifact_ids"], where + ".artifact_ids",
                               state_module.MAX_ARTIFACT_LINKS, record.PROBLEM_TOO_LARGE)
    if artifact_ids != sorted(set(artifact_ids)):
        record.fail(record.PROBLEM_BAD_VALUE,
                    "%s.artifact_ids must be sorted and duplicate-free" % where)
    for index, artifact_id in enumerate(artifact_ids):
        record.require_id(artifact_id, record.ARTIFACT_ID_PREFIX,
                          "%s.artifact_ids[%d]" % (where, index))
        artifact = state_module.artifact_by_id(bindings.state, artifact_id)
        if artifact is None or artifact["sequence"] > entry["sequence"]:
            record.fail(state_module.PROBLEM_UNKNOWN_ARTIFACT,
                        "%s.artifact_ids names %s, which is not an artifact"
                        " recorded before this evidence" % (where, artifact_id))
    submitted = record.require_timestamp(entry["submitted_at"],
                                         where + ".submitted_at")
    acceptance = entry["acceptance"]
    if acceptance is not None:
        sub = where + ".acceptance"
        record.require_dict(acceptance, sub)
        record.require_closed_keys(acceptance, state_module.ACCEPTANCE_KEYS, sub)
        record.require_int(acceptance["sequence"], sub + ".sequence", minimum=1)
        _require_operation(bindings, acceptance, state_module.OPERATION_ACCEPT_EVIDENCE, sub,
                           time_field="accepted_at")
        if acceptance["sequence"] <= entry["sequence"]:
            record.fail(state_module.PROBLEM_OPERATION_BINDING,
                        "%s must be a LATER operation than the submission" % sub)
        accepted = record.require_timestamp(acceptance["accepted_at"],
                                            sub + ".accepted_at")
        if accepted < submitted:
            record.fail(record.PROBLEM_BAD_VALUE,
                        "%s.accepted_at precedes the submission" % sub)
        record.require_id(acceptance["activation_id"], record.PROOF_CONTRACT_ID_PREFIX,
                          sub + ".activation_id")
        if acceptance["activation_id"] != entry["activation_id"]:
            record.fail(state_module.PROBLEM_ACCEPTANCE_ACTIVATION_MISMATCH,
                        "%s binds activation %s but the submission it accepts was"
                        " made under %s" % (sub, acceptance["activation_id"],
                                            entry["activation_id"]))
        _require_activation(bindings, acceptance, sub)
        record.require_hex(acceptance["content_digest_sha256"],
                           sub + ".content_digest_sha256", 64)
        if acceptance["content_digest_sha256"] != entry["content_digest_sha256"]:
            record.fail(state_module.PROBLEM_EVIDENCE_DIGEST,
                        "%s accepted content digest disagrees with the submitted"
                        " digest" % sub)
        if entry["kind"] not in record.SATISFYING_EVIDENCE_KINDS:
            record.fail(state_module.PROBLEM_EVIDENCE_KIND_NOT_SATISFYING,
                        "%s: %s evidence may be recorded but can never be"
                        " accepted as satisfying proof" % (where, entry["kind"]))
    invalidation = entry["invalidation"]
    if invalidation is not None:
        sub = where + ".invalidation"
        record.require_dict(invalidation, sub)
        record.require_closed_keys(invalidation, state_module.INVALIDATION_KEYS, sub)
        record.require_int(invalidation["sequence"], sub + ".sequence", minimum=1)
        _require_operation(bindings, invalidation, state_module.OPERATION_INVALIDATE_EVIDENCE,
                           sub, time_field="invalidated_at")
        if invalidation["sequence"] <= entry["sequence"]:
            record.fail(state_module.PROBLEM_OPERATION_BINDING,
                        "%s must be a LATER operation than the submission" % sub)
        invalidated = record.require_timestamp(invalidation["invalidated_at"],
                                               sub + ".invalidated_at")
        if invalidated < submitted:
            record.fail(record.PROBLEM_BAD_VALUE,
                        "%s.invalidated_at precedes the submission" % sub)
        if acceptance is not None and acceptance["sequence"] >= invalidation["sequence"]:
            record.fail(state_module.PROBLEM_ACCEPTANCE_AFTER_INVALIDATION,
                        "%s: acceptance at sequence %d is at or after the"
                        " invalidation at sequence %d; the service would have"
                        " refused it" % (where, acceptance["sequence"],
                                         invalidation["sequence"]))
        record.require_str(invalidation["reason"], sub + ".reason",
                           state_module.MAX_STATE_REASON_CHARS)
    if evidence_id in bindings.evidence:
        record.fail(record.PROBLEM_BAD_VALUE,
                    "%s repeats evidence id %s" % (where, evidence_id))
    bindings.evidence[evidence_id] = entry


def _validate_resolution(bindings, entry, where, opened_at):
    resolution = entry["resolution"]
    if resolution is None:
        return
    sub = where + ".resolution"
    record.require_dict(resolution, sub)
    record.require_closed_keys(resolution, state_module.RESOLUTION_KEYS, sub)
    record.require_int(resolution["sequence"], sub + ".sequence", minimum=1)
    kind = (state_module.OPERATION_RESOLVE_BLOCKER if "blocker_id" in entry
            else state_module.OPERATION_RESOLVE_DEPENDENCY)
    _require_operation(bindings, resolution, kind, sub, time_field="resolved_at")
    if resolution["sequence"] <= entry["sequence"]:
        record.fail(state_module.PROBLEM_OPERATION_BINDING,
                    "%s must be a LATER operation than the record it resolves"
                    % sub)
    resolved = record.require_timestamp(resolution["resolved_at"],
                                        sub + ".resolved_at")
    if resolved < opened_at:
        record.fail(record.PROBLEM_BAD_VALUE,
                    "%s.resolved_at precedes the record it resolves" % sub)
    evidence_id = resolution["evidence_id"]
    record.require_id(evidence_id, record.EVIDENCE_ID_PREFIX, sub + ".evidence_id")
    evidence = bindings.evidence.get(evidence_id)
    if evidence is None:
        record.fail(state_module.PROBLEM_UNKNOWN_EVIDENCE,
                    "%s names evidence %s, which this record does not hold"
                    % (sub, evidence_id))
    if evidence["acceptance"] is None or (
        evidence["acceptance"]["sequence"] > resolution["sequence"]
    ):
        record.fail(state_module.PROBLEM_EVIDENCE_NOT_ACCEPTED,
                    "%s names evidence %s, which was not ACCEPTED before the"
                    " resolution; submission alone resolves nothing"
                    % (sub, evidence_id))
    if evidence["invalidation"] is not None and (
        evidence["invalidation"]["sequence"] <= resolution["sequence"]
    ):
        record.fail(state_module.PROBLEM_EVIDENCE_INVALIDATED,
                    "%s names evidence %s, which was invalidated at sequence %d,"
                    " at or before the resolution at sequence %d"
                    % (sub, evidence_id, evidence["invalidation"]["sequence"],
                       resolution["sequence"]))


def _validate_blocker(bindings, entry, where):
    blocker_id = record.require_id(entry["blocker_id"], record.BLOCKER_ID_PREFIX,
                                   where + ".blocker_id")
    _require_operation(bindings, entry, state_module.OPERATION_OPEN_BLOCKER, where,
                       time_field="opened_at")
    _require_activation(bindings, entry, where)
    record.require_contract_key(entry["key"], where + ".key")
    record.require_member(entry["severity"], state_module.BLOCKER_SEVERITIES,
                          where + ".severity")
    record.require_str(entry["description"], where + ".description",
                       state_module.MAX_BLOCKER_DESCRIPTION_CHARS)
    opened = record.require_timestamp(entry["opened_at"], where + ".opened_at")
    _validate_resolution(bindings, entry, where, opened)
    if blocker_id in bindings.blocker_ids:
        record.fail(record.PROBLEM_BAD_VALUE,
                    "%s repeats blocker id %s" % (where, blocker_id))
    bindings.blocker_ids.add(blocker_id)


def _validate_dependency(bindings, entry, where, bound_slots):
    dependency_id = record.require_id(entry["dependency_id"],
                                      record.DEPENDENCY_ID_PREFIX,
                                      where + ".dependency_id")
    _require_operation(bindings, entry, state_module.OPERATION_BIND_DEPENDENCY, where,
                       time_field="declared_at")
    _require_activation(bindings, entry, where)
    if entry["key"] is not None:
        record.require_contract_key(entry["key"], where + ".key")
        slot = (entry["activation_id"], entry["key"])
        if slot in bound_slots:
            record.fail(state_module.PROBLEM_DEPENDENCY_REBIND,
                        "%s binds slot %r a second time under the same"
                        " activation; a bound slot is never rebound, changing"
                        " a prerequisite is a contract change"
                        % (where, entry["key"]))
        bound_slots.add(slot)
    kind = record.require_member(entry["kind"], record.DEPENDENCY_KINDS,
                                 where + ".kind")
    if kind == record.DEPENDENCY_KIND_MISSION:
        record.require_id(entry["reference"], record.MISSION_ID_PREFIX,
                          where + ".reference")
        if entry["reference"] == bindings.mission_id:
            record.fail(state_module.PROBLEM_DEPENDENCY_SELF,
                        "%s: a mission cannot depend on itself" % where)
    else:
        record.require_str(entry["reference"], where + ".reference",
                           state_module.MAX_RESOURCE_REFERENCE_CHARS)
    declared = record.require_timestamp(entry["declared_at"], where + ".declared_at")
    _validate_resolution(bindings, entry, where, declared)
    if dependency_id in bindings.dependency_ids:
        record.fail(record.PROBLEM_BAD_VALUE,
                    "%s repeats dependency id %s" % (where, dependency_id))
    bindings.dependency_ids.add(dependency_id)


def _validate_readiness(bindings, entry, where):
    _require_operation(bindings, entry, state_module.OPERATION_OBSERVE_RESOURCE_READINESS, where,
                       time_field="observed_at", at_or_before=True)
    record.require_contract_key(entry["resource_key"], where + ".resource_key")
    record.require_member(entry["status"], state_module.READINESS_STATUSES, where + ".status")


def _validate_continuation(bindings, entry, where, expected_attempt):
    _require_operation(bindings, entry, state_module.OPERATION_RECORD_CONTINUATION, where,
                       time_field="recorded_at")
    record.require_int(entry["attempt"], where + ".attempt", minimum=1)
    if entry["attempt"] != expected_attempt:
        record.fail(state_module.PROBLEM_CONTINUATION_ORDER,
                    "%s.attempt must be %d: attempts are numbered contiguously"
                    " and never reset" % (where, expected_attempt))
    record.require_str(entry["reason"], where + ".reason", state_module.MAX_STATE_REASON_CHARS)


def _validate_work_items(value, where):
    items = _bound_list(value, where, state_module.MAX_WORK_ITEMS, record.PROBLEM_TOO_LARGE)
    for index, item in enumerate(items):
        record.require_str(item, "%s[%d]" % (where, index), state_module.MAX_WORK_ITEM_CHARS)


def _validate_checkpoint(bindings, entry, where):
    record.require_id(entry["checkpoint_id"], record.CHECKPOINT_ID_PREFIX,
                      where + ".checkpoint_id")
    _require_operation(bindings, entry, state_module.OPERATION_RECORD_CHECKPOINT, where,
                       time_field="recorded_at")
    activation = _require_activation(bindings, entry, where)
    _validate_work_items(entry["completed_work"], where + ".completed_work")
    _validate_work_items(entry["outstanding_work"], where + ".outstanding_work")
    refs = entry["refs"]
    record.require_dict(refs, where + ".refs")
    record.require_closed_keys(refs, state_module.CHECKPOINT_REFS_KEYS, where + ".refs")
    # R-41: typed before compared (True / 1.0 would compare equal to 1).
    record.require_int(refs["revision"], where + ".refs.revision", minimum=1)
    record.require_hex(refs["proposal_digest_sha256"],
                       where + ".refs.proposal_digest_sha256", 64)
    record.require_hex(refs["contract_digest_sha256"],
                       where + ".refs.contract_digest_sha256", 64)
    if refs != {
        "revision": activation["revision"],
        "proposal_digest_sha256": activation["proposal_digest_sha256"],
        "contract_digest_sha256": activation["contract_digest_sha256"],
    }:
        record.fail(state_module.PROBLEM_CHECKPOINT_REFS,
                    "%s.refs disagree with activation %s"
                    % (where, activation["activation_id"]))
    blocker_ids = _bound_list(entry["active_blocker_ids"],
                              where + ".active_blocker_ids", state_module.MAX_BLOCKER_RECORDS,
                              record.PROBLEM_TOO_LARGE)
    for index, blocker_id in enumerate(blocker_ids):
        record.require_id(blocker_id, record.BLOCKER_ID_PREFIX,
                          "%s.active_blocker_ids[%d]" % (where, index))
        if blocker_id not in bindings.blocker_ids:
            record.fail(state_module.PROBLEM_UNKNOWN_BLOCKER,
                        "%s.active_blocker_ids names unknown blocker %s"
                        % (where, blocker_id))
    dependency_ids = _bound_list(entry["outstanding_dependency_ids"],
                                 where + ".outstanding_dependency_ids",
                                 state_module.MAX_DEPENDENCY_RECORDS, record.PROBLEM_TOO_LARGE)
    for index, dependency_id in enumerate(dependency_ids):
        record.require_id(dependency_id, record.DEPENDENCY_ID_PREFIX,
                          "%s.outstanding_dependency_ids[%d]" % (where, index))
        if dependency_id not in bindings.dependency_ids:
            record.fail(state_module.PROBLEM_UNKNOWN_DEPENDENCY,
                        "%s.outstanding_dependency_ids names unknown"
                        " dependency %s" % (where, dependency_id))
    budget = entry["budget"]
    record.require_dict(budget, where + ".budget")
    record.require_closed_keys(budget, state_module.CHECKPOINT_BUDGET_KEYS, where + ".budget")
    for key in state_module.CHECKPOINT_BUDGET_KEYS:
        record.require_int(budget[key], "%s.budget.%s" % (where, key), minimum=0)
    record.require_str(entry["retry_condition"], where + ".retry_condition",
                       state_module.MAX_CONDITION_CHARS)
    record.require_str(entry["stop_condition"], where + ".stop_condition",
                       state_module.MAX_CONDITION_CHARS)
    step = entry["next_permitted_step"]
    refusal = entry["refusal"]
    if (step is None) == (refusal is None):
        record.fail(state_module.PROBLEM_CHECKPOINT_STEP,
                    "%s must carry EXACTLY ONE of next_permitted_step or"
                    " refusal" % where)
    if step is not None:
        record.require_member(step, state_module.NEXT_STEPS, where + ".next_permitted_step")
    else:
        sub = where + ".refusal"
        record.require_dict(refusal, sub)
        record.require_closed_keys(refusal, state_module.REFUSAL_KEYS, sub)
        record.require_str(refusal["problem"], sub + ".problem",
                           record.MAX_PRINCIPAL_REF_CHARS)
        if not refusal["problem"].startswith("mission_"):
            record.fail(record.PROBLEM_BAD_VALUE,
                        "%s.problem must be a mission_* problem code" % sub)
        record.require_str(refusal["detail"], sub + ".detail",
                           state_module.MAX_REFUSAL_DETAIL_CHARS)


def _validate_closure(bindings):
    state = bindings.state
    location = bindings.location
    closure = state["closure"]
    progress = state["progress"]
    if closure is None:
        if progress in state_module.TERMINAL_PROGRESS_STATES:
            record.fail(state_module.PROBLEM_CLOSURE,
                        "%s is %s but records no closure" % (location, progress))
        return
    where = location + ".closure"
    if progress not in state_module.TERMINAL_PROGRESS_STATES:
        record.fail(state_module.PROBLEM_CLOSURE,
                    "%s records a closure while progress is %s"
                    % (location, progress))
    record.require_dict(closure, where)
    record.require_closed_keys(closure, state_module.CLOSURE_KEYS, where)
    if closure["progress"] != progress:
        record.fail(state_module.PROBLEM_CLOSURE,
                    "%s.progress %r disagrees with the record's progress %s"
                    % (where, closure["progress"], progress))
    if closure["reason"] not in state_module.CLOSURE_REASONS_BY_PROGRESS[progress]:
        record.fail(state_module.PROBLEM_CLOSURE,
                    "%s.reason %r is not a recorded reason for %s; the"
                    " reasons are %s"
                    % (where, closure["reason"], progress,
                       ", ".join(state_module.CLOSURE_REASONS_BY_PROGRESS[progress])))
    record.require_str(closure["detail"], where + ".detail", state_module.MAX_STATE_REASON_CHARS)
    record.require_timestamp(closure["closed_at"], where + ".closed_at")
    record.require_int(closure["sequence"], where + ".sequence", minimum=1)
    operation = bindings.operations.get(closure["operation_id"])
    expected_kind = None
    for kind, outcome in state_module.CLOSING_OPERATIONS.items():
        if outcome == progress:
            expected_kind = kind
    if operation is None or operation["kind"] != expected_kind:
        record.fail(state_module.PROBLEM_CLOSURE,
                    "%s must be produced by a %s operation" % (where, expected_kind))
    _require_operation(bindings, closure, expected_kind, where, time_field="closed_at")
    if closure["sequence"] != state["sequence"]:
        record.fail(state_module.PROBLEM_CLOSURE,
                    "%s is not the last operation of the record; terminal is"
                    " terminal" % where)
    if closure["activation_id"] is None:
        if state["contract_activations"] or progress == state_module.PROGRESS_COMPLETED:
            record.fail(state_module.PROBLEM_CLOSURE,
                        "%s must name the activation it closed under" % where)
    else:
        # COMPLETED closures and asserting unsuccessful reasons are
        # contract-dependent operations and bind their revision (R-36);
        # abandon / caller closure may legitimately follow an EDIT.
        _require_activation(bindings, closure, where,
                            revision_bound=state_module.closure_is_contract_dependent(
                                closure))


def _validate_progress_consistency(bindings):
    state = bindings.state
    location = bindings.location
    progress = state["progress"]
    if progress in state_module.TERMINAL_PROGRESS_STATES:
        return
    if progress == state_module.PROGRESS_NOT_STARTED:
        if state["contract_activations"]:
            record.fail(state_module.PROBLEM_PROGRESS_DISAGREES,
                        "%s is NOT_STARTED but holds a contract activation"
                        % location)
        return
    if not state["contract_activations"]:
        record.fail(state_module.PROBLEM_PROGRESS_DISAGREES,
                    "%s is %s without any contract activation" % (location, progress))
    blocked = bool(state_module.active_hard_blockers(state))
    if blocked != (progress == state_module.PROGRESS_BLOCKED):
        record.fail(state_module.PROBLEM_PROGRESS_DISAGREES,
                    "%s is %s but %s"
                    % (location, progress,
                       "an active HARD blocker exists" if blocked
                       else "no active HARD blocker exists"))
    for kind in state_module.CLOSING_OPERATIONS:
        if any(op["kind"] == kind for op in state["applied_operations"]):
            record.fail(state_module.PROBLEM_CLOSURE,
                        "%s applied a %s operation but is not terminal"
                        % (location, kind))


def validate_state_record(value, location="mission state"):
    """Every field, bound and intra-record binding, or refuse. Cross-
    document bindings (the mission, its revisions, its authorizations,
    the contract content, foreign missions) are checked by the store."""
    record.require_dict(value, location)
    record.require_closed_keys(value, state_module.STATE_RECORD_KEYS, location)
    record.require_int(value["schema_version"], location + ".schema_version")
    if value["schema_version"] != state_module.STATE_SCHEMA_VERSION:
        record.fail(record.PROBLEM_BAD_VALUE,
                    "%s.schema_version must be %d" % (location, state_module.STATE_SCHEMA_VERSION))
    record.require_id(value["mission_id"], record.MISSION_ID_PREFIX,
                      location + ".mission_id")
    record.require_int(value["sequence"], location + ".sequence", minimum=0)
    created = record.require_timestamp(value["created_at"], location + ".created_at")
    updated = record.require_timestamp(value["updated_at"], location + ".updated_at")
    if updated < created:
        record.fail(record.PROBLEM_BAD_VALUE,
                    "%s.updated_at precedes created_at" % location)
    state_module.require_progress(value["progress"], location + ".progress")
    bindings = _Bindings(value, location)
    _validate_applied_operations(bindings)
    _validate_activations(bindings)
    _validate_ordered(bindings, "claims", state_module.MAX_CLAIMS, state_module.CLAIM_KEYS, _validate_claim)
    _validate_ordered(bindings, "artifacts", state_module.MAX_ARTIFACT_RECORDS, state_module.ARTIFACT_KEYS,
                      _validate_artifact)
    _validate_ordered(bindings, "evidence", state_module.MAX_EVIDENCE_RECORDS, state_module.EVIDENCE_KEYS,
                      _validate_evidence)
    _validate_ordered(bindings, "blockers", state_module.MAX_BLOCKER_RECORDS, state_module.BLOCKER_KEYS,
                      _validate_blocker)
    bound_slots = set()
    _validate_ordered(
        bindings, "dependencies", state_module.MAX_DEPENDENCY_RECORDS, state_module.DEPENDENCY_KEYS,
        lambda b, e, w: _validate_dependency(b, e, w, bound_slots),
    )
    _validate_ordered(bindings, "resource_readiness",
                      state_module.MAX_RESOURCE_READINESS_OBSERVATIONS,
                      state_module.READINESS_OBSERVATION_KEYS, _validate_readiness)
    counter = {"attempt": 0}

    def continuation(b, e, w):
        counter["attempt"] += 1
        _validate_continuation(b, e, w, counter["attempt"])

    _validate_ordered(bindings, "continuations", state_module.MAX_CONTINUATION_RECORDS,
                      state_module.CONTINUATION_KEYS, continuation)
    _validate_ordered(bindings, "checkpoints", state_module.MAX_CHECKPOINT_RECORDS,
                      state_module.CHECKPOINT_KEYS, _validate_checkpoint)
    _validate_closure(bindings)
    _validate_progress_consistency(bindings)
    state_reconcile.reconcile_effects(value, location)
    return value
