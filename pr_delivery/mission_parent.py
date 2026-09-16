"""P1-A6 parent-authority seam: does a delivery record's optional Mission
parent name a Mission Authorization that permits ``github_pr``?

Dependency direction. AUTHORITY flows Mission -> PR Delivery; IMPORTS
flow consumer -> core. This module imports the neutral Mission Core;
the Mission Core never imports ``pr_delivery`` (pinned). Nothing else in
this package reaches the Mission Core, and nothing in this package calls
this seam yet: it is the production consumption point a later delivery
transaction will call before deriving bounded delivery authorization,
and in this bundle it is wired into no step the delivery machine
executes. The parent check ``parent_mission_authority`` is read-only and
effect-free: it performs no Git action and mutates no state in either
store. The SECOND function of this module, ``attest_validated_receipt``
(below), is the one exception: a local evidence write into the Mission
store and nothing else — it still performs no Git action and never
writes the delivery store.

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

The receipt attestation path (Task 7, Stage 2). ``attest_validated_receipt``
is the SECOND function of this seam and the only one that writes: a
LOCAL EVIDENCE WRITE into the Mission store recording that the existing
receipt validator accepted one stored receipt of a delivery record whose
Mission parent validates. Its order is: a bounded PREFLIGHT of the
caller's document and step (exact builtin types, closed depth, item
count, string and key length — module constants, with the remaining
traversal budget charged BEFORE any container's children are enqueued, and all of it before the document is
copied, formatted or handed to any validator); a PRIVATE STRUCTURAL
COPY of the document (``_plain_copy``: every dict and list rebuilt, every
string, number, boolean and null carried over as the same immutable
value — no serialization, so no two distinct keys can collapse and no
value can be altered; the copy is faithful by construction and is
proven equal, key for key and type for type, to the original, including
for strings no encoding carries, which the unchanged store and validator
accept and which are therefore NOT refused here); the
unchanged ``validate_authorization`` over that copy (the authority
digest, every step's stored receipt and the step/receipt state
agreement); the unchanged ``validate_receipt`` over the copy's receipt
with the expected step, delivery identity and parent authority digest
taken from the validated copy — never from the receipt being checked;
the Mission parent through ``parent_mission_authority`` over the same
copy; and only then the Mission Core's distinct, non-authorizing
``attest_delivery_receipt`` operation, through the same atomic apply
path as every other state operation.

What gets recorded is bound to what was validated, by construction:
``validated_receipt`` is the ONE function that validates, and it returns
an immutable ``ValidatedReceipt`` whose every field is read from the
same private copy the validators accepted; ``attest_validated_receipt``
records only attribute reads of that object and of the parent
projection. The static pins prove this on the SOURCE by whole-program
equality over a DERIVED set: every definition in this module reachable
from the recording function ``attest_validated_receipt`` through the
names it and they use (today all eight: the parent check and its
projection helper, the refusal projection helper, the preflight, the
copy helper, the dataclass with no base, no metaclass keyword and no
method, the validating function and the recording function itself) is,
by AST equality after docstrings, exactly the program the tests carry,
and the module's top level holds nothing but its docstring, its imports,
literal constants and its definitions, in that order. The compared set
is not a hand list: it is recomputed from the source, so a helper that
joins the call path is inside the comparison the moment it is reached.
There is no whitelist to be incomplete: any decorator, base, metaclass,
extra statement, rebinding or substitution in any reachable definition
is a different program. The
private copy is the RUNTIME half of the guarantee: nothing the caller
holds can change between validation and recording, and a document that
is not plain data is refused by the preflight before either. The
preflight narrows nothing the unchanged validator accepts: it bounds
size, depth and type only, and every string the delivery store can hold
reaches the validator unchanged.
The Mission Core re-validates the parent authorization inside its lock
at the Mission's current revision, so a revision or authority change
between validation and application refuses with nothing written. A
validation failure raises the contract's own ``AuthorizationError`` (or
returns a refusal projection for a document outside the preflight
bounds, an absent receipt or an invalid parent) BEFORE the Mission call,
which is therefore unreachable on failure.

What the attestation is not. It is evidence, not authority: it cannot
approve or close a Mission, alter a human decision, waive proof, remove
a blocker, raise a budget, or permit any delivery action, and this
module still performs no repository action and is wired into no step the
delivery machine executes. Structural receipt validity is not success:
the receipt state is handed over verbatim, and only the succeeded state
is ever read as a completed effect. The digests are binding values, not
secrets. The confinement is repository source and call-path confinement
proven by the static pins — not protection against arbitrary code in
the process, runtime monkey-patching or a rewrite of storage or source,
and not cryptographic authenticity.
"""

from dataclasses import dataclass

from mission import record as mission_record
from pr_delivery import authorization as delivery_authorization

PROBLEM_PARENT_ABSENT = "pr_delivery_mission_parent_absent"
PROBLEM_RECEIPT_ABSENT = "pr_delivery_mission_receipt_absent"
PROBLEM_STEP_UNKNOWN = "pr_delivery_mission_step_unknown"
PROBLEM_PARENT_INVALID = "pr_delivery_mission_parent_invalid"
# The caller's document or step is outside the preflight bounds: refused
# by size and type alone, before it is copied, formatted or validated.
PROBLEM_DOCUMENT_UNBOUNDED = "pr_delivery_mission_document_unbounded"

# Preflight bounds on the caller's delivery document, never derived from
# input. They are wider than any value the delivery contract permits
# (a record holds at most MAX_CANDIDATE_ENTRIES entries of four short
# fields, a PR body of MAX_PR_BODY_CHARS, an argv of MAX_REVERIFICATION_ARGV
# strings of at most 4096 characters, nested at most five levels), so a
# valid record always passes and an oversized one is refused before any
# work proportional to its size.
MAX_DELIVERY_DOCUMENT_ITEMS = 65536
MAX_DELIVERY_DOCUMENT_DEPTH = 8
MAX_DELIVERY_DOCUMENT_STR_CHARS = 16384
MAX_DELIVERY_DOCUMENT_KEY_CHARS = 128
MAX_DELIVERY_DOCUMENT_INT_BITS = 63

ATTESTATION_KEYS = (
    "valid", "problem", "detail", "receipt_id", "step", "receipt_state",
    "succeeded", "delivery_id", "mission_id", "authorization_id", "revision",
    "outcome",
)

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


def _attestation(valid, problem, detail, receipt_id=None, step=None,
                 receipt_state=None, succeeded=False, delivery_id=None,
                 mission_id=None, authorization_id=None, revision=None,
                 outcome=None):
    projection = {
        "valid": valid, "problem": problem, "detail": detail,
        "receipt_id": receipt_id, "step": step, "receipt_state": receipt_state,
        "succeeded": succeeded, "delivery_id": delivery_id,
        "mission_id": mission_id, "authorization_id": authorization_id,
        "revision": revision, "outcome": outcome,
    }
    assert tuple(sorted(projection)) == tuple(sorted(ATTESTATION_KEYS))
    return projection


def _preflight_problem(document, step):
    """Why the caller's document or step is outside the preflight bounds,
    or None. Exact builtin types only (``type(x) is dict / list / str /
    int / float / bool / NoneType``; no subclass, no other object); every
    bound applied BEFORE the value is copied, formatted or compared; the
    remaining item budget charged for a container's children BEFORE any
    of them is enqueued, so the pending stack never holds more than the
    budget and an oversized container costs one length check; and every
    message built from constants — never from the input. Nothing here
    judges string CONTENT: a string the unchanged store can hold is
    carried to the unchanged validator exactly as it came."""
    if type(step) is not str:
        return "the step is not a string"
    if len(step) > MAX_DELIVERY_DOCUMENT_KEY_CHARS:
        return "the step is longer than %d characters" % MAX_DELIVERY_DOCUMENT_KEY_CHARS
    if type(document) is not dict:
        return "the delivery document is not a plain object"
    remaining = MAX_DELIVERY_DOCUMENT_ITEMS - 1
    pending = [(document, 1)]
    while pending:
        container, depth = pending.pop()
        if depth > MAX_DELIVERY_DOCUMENT_DEPTH:
            return ("the delivery document is nested deeper than %d levels"
                    % MAX_DELIVERY_DOCUMENT_DEPTH)
        size = len(container)
        if size > remaining:
            return ("the delivery document holds more than %d items"
                    % MAX_DELIVERY_DOCUMENT_ITEMS)
        remaining = remaining - size
        if type(container) is dict:
            for key in dict.keys(container):
                if type(key) is not str:
                    return "the delivery document has a key that is not a string"
                if len(key) > MAX_DELIVERY_DOCUMENT_KEY_CHARS:
                    return ("the delivery document has a key longer than %d"
                            " characters" % MAX_DELIVERY_DOCUMENT_KEY_CHARS)
            children = dict.values(container)
        else:
            children = container
        for value in children:
            kind = type(value)
            if kind is dict or kind is list:
                pending.append((value, depth + 1))
            elif kind is str:
                if len(value) > MAX_DELIVERY_DOCUMENT_STR_CHARS:
                    return ("the delivery document holds a string longer than %d"
                            " characters" % MAX_DELIVERY_DOCUMENT_STR_CHARS)
            elif kind is int:
                if value.bit_length() > MAX_DELIVERY_DOCUMENT_INT_BITS:
                    return ("the delivery document holds an integer wider than %d"
                            " bits" % MAX_DELIVERY_DOCUMENT_INT_BITS)
            elif kind is not float and kind is not bool and value is not None:
                return "the delivery document holds a value that is not plain data"
    return None


def _plain_copy(value):
    """A private structural copy of preflight-accepted plain data: every
    dict and list is rebuilt, every string, number, boolean and null is
    the same immutable value. No serialization is involved, so nothing
    is re-encoded, no two distinct keys can collapse and no value can
    change: the copy is faithful by construction."""
    kind = type(value)
    if kind is dict:
        return dict((key, _plain_copy(item)) for key, item in dict.items(value))
    if kind is list:
        return [_plain_copy(item) for item in value]
    return value


@dataclass(frozen=True)
class ValidatedReceipt:
    """The bound values of ONE receipt the unchanged validator accepted,
    every field read from the same private copy it validated. Immutable,
    with no base class, no metaclass and no method: nothing runs at or
    after construction that could replace a field."""

    receipt_id: str
    receipt_digest_sha256: str
    delivery_id: str
    step: str
    receipt_state: str
    step_state: str
    parent_authority_digest_sha256: str
    authorization_digest_sha256: str
    succeeded: bool
    record: dict


def validated_receipt(delivery_document, step):
    """Validate FIRST, through the unchanged contract, over a private
    structural copy of the caller's document, and return the bound values
    of the receipt that was validated — or None when the step holds no
    receipt. Pinned by whole-program AST equality: ``record`` and
    ``receipt`` are each bound exactly once, and every returned field is a
    read of those two names (and of ``step``). Raises the contract's
    ``AuthorizationError`` on any validation failure; the caller
    preflights the document first."""
    record = _plain_copy(delivery_document)
    delivery_authorization.validate_authorization(record)
    receipt = record["steps"][step]["receipt"]
    if receipt is None:
        return None
    # Expected step, delivery identity and parent authority digest come
    # from the VALIDATED copy, never from the receipt under check.
    delivery_authorization.validate_receipt(
        receipt, step, record["delivery_id"], record["authority_digest_sha256"],
        "delivery %s step %s receipt" % (record["delivery_id"], step))
    return ValidatedReceipt(
        receipt_id=receipt["receipt_id"],
        receipt_digest_sha256=receipt["receipt_digest_sha256"],
        delivery_id=record["delivery_id"],
        step=step,
        receipt_state=receipt["state"],
        step_state=record["steps"][step]["state"],
        parent_authority_digest_sha256=record["authority_digest_sha256"],
        authorization_digest_sha256=(
            None if record["mission"] is None
            else record["mission"]["mission_authorization_digest_sha256"]),
        succeeded=(
            receipt["state"] == delivery_authorization.RECEIPT_SUCCEEDED
            and record["steps"][step]["state"] == delivery_authorization.STEP_SUCCEEDED),
        record=record,
    )


def attest_validated_receipt(delivery_document, step, mission_service,
                             operation_id, expected_sequence, context):
    """Preflight, validate through ``validated_receipt``, check the Mission
    parent over the same private copy, then record through the Mission
    Core's distinct operation exactly the values that were validated —
    the receipt's verbatim state AND the step's verbatim state, so the
    Mission side can derive the same completion answer as ``succeeded``
    here and never a more positive one. The Mission call is the LAST
    statement, reachable only after every step before it passed, and its
    attestation is built solely from attribute reads of the
    ``ValidatedReceipt`` and the parent projection.

    Raises the contract's ``AuthorizationError`` when the delivery record
    or the receipt is malformed, tampered or bound to another step,
    delivery or authority; returns a refusal projection (no Mission call)
    when the document or step is outside the preflight bounds, the step
    is unknown, the step holds no receipt, or the Mission parent does not
    validate; otherwise returns the attestation projection carrying the
    Mission operation's outcome. A Mission-side refusal (stale sequence,
    authority no longer valid, already attested) raises the Mission
    Core's ``MissionError`` with nothing written."""
    unbounded = _preflight_problem(delivery_document, step)
    if unbounded is not None:
        return _attestation(False, PROBLEM_DOCUMENT_UNBOUNDED,
                            unbounded + "; nothing was validated or attested")
    if step not in delivery_authorization.STEPS:
        return _attestation(
            False, PROBLEM_STEP_UNKNOWN,
            "the attestation step is not one of the closed delivery steps;"
            " nothing was attested", step=step)
    validated = validated_receipt(delivery_document, step)
    if validated is None:
        return _attestation(
            False, PROBLEM_RECEIPT_ABSENT,
            "the delivery record holds no receipt for the step; there is nothing"
            " to attest", step=step)
    parent = parent_mission_authority(validated.record, mission_service)
    if not parent["valid"]:
        return _attestation(
            False, PROBLEM_PARENT_INVALID,
            "the delivery record's Mission parent does not validate (%s: %s);"
            " the receipt is not attested" % (parent["problem"], parent["detail"]),
            receipt_id=validated.receipt_id, step=validated.step,
            receipt_state=validated.receipt_state,
            delivery_id=validated.delivery_id, mission_id=parent["mission_id"],
            authorization_id=parent["authorization_id"], revision=parent["revision"])
    outcome = mission_service.attest_delivery_receipt(
        parent["mission_id"], operation_id, expected_sequence, {
            "receipt_id": validated.receipt_id,
            "receipt_digest_sha256": validated.receipt_digest_sha256,
            "delivery_id": validated.delivery_id,
            "step": validated.step,
            "receipt_state": validated.receipt_state,
            "step_state": validated.step_state,
            "parent_authority_digest_sha256": validated.parent_authority_digest_sha256,
            "authorization_digest_sha256": validated.authorization_digest_sha256,
        }, context)
    return _attestation(
        True, None,
        "the existing receipt validator accepted receipt %s of delivery %s"
        " for step %s in state %s and the Mission parent validates; the"
        " receipt reference is attested as artifact %s (succeeded: %s)"
        % (validated.receipt_id, validated.delivery_id, validated.step,
           validated.receipt_state, outcome["artifact_id"], validated.succeeded),
        receipt_id=validated.receipt_id, step=validated.step,
        receipt_state=validated.receipt_state, succeeded=validated.succeeded,
        delivery_id=validated.delivery_id, mission_id=parent["mission_id"],
        authorization_id=parent["authorization_id"], revision=parent["revision"],
        outcome=outcome)
