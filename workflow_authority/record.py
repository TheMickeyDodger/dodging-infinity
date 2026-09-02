"""The DI-REMOTE-2 durable workflow authority record.

The record's key set is CLOSED and is validated on every load AND
every save: unknown keys, missing keys, wrong types, and ``bool``
where a number is expected all fail closed (booleans are a subclass of
``int`` in Python, so ``True`` could otherwise masquerade as user id 1
or revision 1). ``delivery_authority`` must be exactly the string
``"none"``; any other value — including its absence and ``True`` —
fails closed, because no workflow record may ever carry delivery
authority.

The binding set is exactly the mission's constraint list: control
identity + policy digest, canonical GitHub target + OPTIONAL issue or
PR, approved baseline, the exact original human intent, exact
rendered Mission Authorization + digest + revision + every
authority-content field (objective, constraints, rules,
desired_outcome, acceptance, unresolved_questions, execution_scope),
Telegram user/chat/messages, nonce/expiry/consumption, handoff
revision/text/digest, phase, workspace lease, preparation/evidence
receipts, Codex turn identities, ambiguity state, and
``delivery_authority``. No field beyond that list exists.

Digest bindings are VERIFIED here, not just shape-checked: the stored
mission and handoff digests must equal the digest of the stored text,
so a record whose text drifted from what was authorized can never
validate. Additionally, the rendered Mission Authorization is
RE-RENDERED from the record's own fields on every validation and must
be byte-equal to the stored ``rendered_text``
(``workflow_authority.rendering`` is the single renderer): a record
field altered independently of the digested text — either target
form edited into the other included — can never validate anywhere
this schema is enforced (store load, store save, the adapter's
mission evaluate, the Broker gate, role-turn prompt construction).

Schema version 2 (DI-REMOTE-2 corrective I1) added ``human_intent``,
``unresolved_questions``, ``execution_scope``, the per-field
authority content, and the optional ``issue_or_pr``. Version-1
records lack authority fields that must never be fabricated, so they
fail closed here and are migrated only by the explicit human-invoked
store migration (which retires them into a preserved backup).

DI-REMOTE-3 I1 adds ``result_placeholder`` and six additive
``result_delivery`` fields. Like ``verified_result``/``result_delivery``
before them they are durable, nullable, MUTABLE NON-AUTHORITY state:
outside the render binding, and outside the binding set named above —
``delivery_authority`` is still the only authority field and is still
required to be exactly ``"none"``. The schema version is deliberately
NOT bumped for them; see ``_TOP_LEVEL_KEYS`` and
``workflow_authority.store._normalize_additive_keys``. (Pinned by
tests/test_di_remote_3_schema.py:
``test_X3_delivery_authority_stays_none_on_every_new_field`` and
``test_schema_version_is_not_bumped``.)
"""

import os

from workflow_authority import canonical, rendering
from workflow_authority.digest import text_digest

WORKFLOW_SCHEMA_VERSION = 2

DELIVERY_AUTHORITY_NONE = "none"

# Lifecycle phases, exactly the mission lifecycle. PLANNED ->
# AUTHORIZED -> WORKSPACE_READY -> PREPARED -> VALIDATED ->
# DISPATCHED -> VERIFIED -> COMPLETED, with BLOCKED and
# NEEDS_REAUTHORIZATION reachable as the mission text describes.
PHASE_PLANNED = "PLANNED"
PHASE_AUTHORIZED = "AUTHORIZED"
PHASE_WORKSPACE_READY = "WORKSPACE_READY"
PHASE_PREPARED = "PREPARED"
PHASE_VALIDATED = "VALIDATED"
PHASE_DISPATCHED = "DISPATCHED"
PHASE_VERIFIED = "VERIFIED"
PHASE_COMPLETED = "COMPLETED"
PHASE_BLOCKED = "BLOCKED"
PHASE_NEEDS_REAUTHORIZATION = "NEEDS_REAUTHORIZATION"

PHASES = (
    PHASE_PLANNED,
    PHASE_AUTHORIZED,
    PHASE_WORKSPACE_READY,
    PHASE_PREPARED,
    PHASE_VALIDATED,
    PHASE_DISPATCHED,
    PHASE_VERIFIED,
    PHASE_COMPLETED,
    PHASE_BLOCKED,
    PHASE_NEEDS_REAUTHORIZATION,
)

# Terminal phases have no outgoing transitions.
TERMINAL_PHASES = (PHASE_COMPLETED, PHASE_BLOCKED)

# The explicit allowed-transition table. Any transition not present
# here fails closed with its own problem code. Reachability of BLOCKED
# and NEEDS_REAUTHORIZATION follows the mission text: every fail-closed
# condition (substitution, mismatch, ambiguity, replay, expiry, crash
# uncertainty, dirty or unsafe state, cross-workflow capability reuse)
# can block the workflow at any non-terminal stage, and reauthorization
# is required when the handoff-validation turn demands it (PREPARED),
# when the approved handoff is no longer permissible before dispatch
# (VALIDATED), or when the approval/baseline binding is found expired
# or altered before preparation (AUTHORIZED, WORKSPACE_READY). A fresh
# authorization returns the workflow to AUTHORIZED.
ALLOWED_TRANSITIONS = {
    PHASE_PLANNED: frozenset((PHASE_AUTHORIZED, PHASE_BLOCKED)),
    PHASE_AUTHORIZED: frozenset(
        (PHASE_WORKSPACE_READY, PHASE_NEEDS_REAUTHORIZATION,
         PHASE_BLOCKED)
    ),
    PHASE_WORKSPACE_READY: frozenset(
        (PHASE_PREPARED, PHASE_NEEDS_REAUTHORIZATION, PHASE_BLOCKED)
    ),
    PHASE_PREPARED: frozenset(
        (PHASE_VALIDATED, PHASE_NEEDS_REAUTHORIZATION, PHASE_BLOCKED)
    ),
    PHASE_VALIDATED: frozenset(
        (PHASE_DISPATCHED, PHASE_NEEDS_REAUTHORIZATION, PHASE_BLOCKED)
    ),
    # DISPATCHED reaches NEEDS_REAUTHORIZATION when the corrective
    # follow-up bound is exceeded (ruling R-2: an
    # authorization-scope bound, never a stranded dead end).
    PHASE_DISPATCHED: frozenset(
        (PHASE_VERIFIED, PHASE_NEEDS_REAUTHORIZATION, PHASE_BLOCKED)
    ),
    PHASE_VERIFIED: frozenset((PHASE_COMPLETED, PHASE_BLOCKED)),
    PHASE_COMPLETED: frozenset(),
    PHASE_BLOCKED: frozenset(),
    PHASE_NEEDS_REAUTHORIZATION: frozenset(
        (PHASE_AUTHORIZED, PHASE_BLOCKED)
    ),
}

# Closed value sets.
ISSUE_OR_PR_KIND_ISSUE = "issue"
ISSUE_OR_PR_KIND_PR = "pr"
ISSUE_OR_PR_KINDS = (ISSUE_OR_PR_KIND_ISSUE, ISSUE_OR_PR_KIND_PR)

APPROVAL_KIND_MISSION_V2 = "mission_authorization_v2"
APPROVAL_KINDS = (APPROVAL_KIND_MISSION_V2,)

DECISION_APPROVE = "approve"
DECISION_REJECT = "reject"
DECISIONS = (DECISION_APPROVE, DECISION_REJECT)

RECEIPT_KIND_PREPARATION = "preparation"
RECEIPT_KIND_EVIDENCE = "evidence"
RECEIPT_KINDS = (RECEIPT_KIND_PREPARATION, RECEIPT_KIND_EVIDENCE)

# The six DI-REMOTE-2 Codex role turns named by the mission text.
TURN_ROLE_PLANNING = "planning"
TURN_ROLE_PREPARE = "prepare"
TURN_ROLE_HANDOFF_VALIDATION = "handoff_validation"
TURN_ROLE_STATUS_RECOVERY = "status_recovery"
TURN_ROLE_VERIFICATION = "verification"
TURN_ROLE_FOLLOW_UP = "follow_up"
TURN_ROLES = (
    TURN_ROLE_PLANNING,
    TURN_ROLE_PREPARE,
    TURN_ROLE_HANDOFF_VALIDATION,
    TURN_ROLE_STATUS_RECOVERY,
    TURN_ROLE_VERIFICATION,
    TURN_ROLE_FOLLOW_UP,
)

AMBIGUITY_NONE = "none"
AMBIGUITY_CRASH_UNCERTAIN = "crash_uncertain"
AMBIGUITY_STATES = (AMBIGUITY_NONE, AMBIGUITY_CRASH_UNCERTAIN)

# Hard bounds, never derived from input. A value beyond its bound is
# REFUSED with the exact observed and allowed sizes, never truncated
# (standing truthfulness rule: a silently shortened record would
# misstate what was authorized or observed).
MAX_ID_CHARS = 128
# The workflow id alphabet (round-08 finding B1): ids name the leased
# workspace directory, so a path-traversing id must be
# UNREPRESENTABLE in a valid record, not merely refused downstream.
# Legitimate ids are "wf-" + lowercase hex.
WORKFLOW_ID_ALPHABET = frozenset(
    "abcdefghijklmnopqrstuvwxyz0123456789-"
)
MAX_TELEGRAM_MESSAGE_IDS = 64
MAX_RECEIPTS = 256
MAX_CODEX_TURNS = 256
MAX_BOUNDED_SUMMARY_CHARS = 2000
MAX_AMBIGUITY_DETAIL_CHARS = 2000
# Deliberately equal to the remote protocol's MAX_ENVELOPE_CHARS
# (telegram_operator.protocol): authority texts travel through
# protocol envelopes, so a text this layer accepted but the envelope
# layer cannot carry would be an authorization that can never be
# displayed. A cross-module test pins the equality.
MAX_AUTHORITY_TEXT_CHARS = 16384
# Per-field bound for each authority-content field. Individual fields
# can sum past MAX_AUTHORITY_TEXT_CHARS; the rendered-total bound
# above then refuses the record with exact sizes — refusal, never
# truncation.
MAX_AUTHORITY_FIELD_CHARS = 8000
# Deliberately equal to the transport's MAX_INTENT_CHARS
# (telegram_operator.protocol): the stored human intent is exactly
# the text the transport accepted, so a wider bound here could store
# nothing the transport allows and a narrower one would refuse
# accepted intent. A cross-module test pins the equality.
MAX_HUMAN_INTENT_CHARS = 4000

PROBLEM_NOT_AN_OBJECT = "workflow_record_not_an_object"
PROBLEM_SCHEMA_VERSION = "workflow_record_schema_version"
PROBLEM_UNKNOWN_KEY = "workflow_record_unknown_key"
PROBLEM_MISSING_KEY = "workflow_record_missing_key"
PROBLEM_BAD_TYPE = "workflow_record_bad_type"
PROBLEM_BAD_VALUE = "workflow_record_bad_value"
PROBLEM_TOO_LARGE = "workflow_record_too_large"
PROBLEM_DELIVERY_AUTHORITY = "workflow_record_delivery_authority"
PROBLEM_DIGEST_MISMATCH = "workflow_record_digest_mismatch"
PROBLEM_RENDER_BINDING = "workflow_record_render_binding"
PROBLEM_BASELINE_REF = "workflow_record_baseline_ref_grammar"
PROBLEM_PATH_CHARACTER = "workflow_record_path_character"
# DI-REMOTE-3 I1: the result placeholder's state/field table (§3.1)
# fails closed per FIELD, each with its own problem code, so an
# out-of-table combination names the exact field that is wrong rather
# than collapsing into the generic bad-value code.
PROBLEM_PLACEHOLDER_STATE_FIELDS = (
    "workflow_record_placeholder_state_fields"
)
PROBLEM_PLACEHOLDER_MESSAGE_ID = (
    "workflow_record_placeholder_message_id"
)
PROBLEM_PLACEHOLDER_SENT_AT = "workflow_record_placeholder_sent_at"
PROBLEM_PLACEHOLDER_BOUND_AT = "workflow_record_placeholder_bound_at"
PROBLEM_PLACEHOLDER_TEXT_DIGEST = (
    "workflow_record_placeholder_text_digest"
)
PROBLEM_PLACEHOLDER_CHAT_MISMATCH = (
    "workflow_record_placeholder_chat_mismatch"
)
# RULING R-18: the record schema was LOOSER than the canonicalizer —
# it admitted an issue/PR number the canonicalizer could never emit.
PROBLEM_ISSUE_URL_TOO_LONG = "workflow_record_issue_url_too_long"
# DI-REMOTE-3 I5 (round-01 F1): the result_delivery receipt's six
# additive fields carry TOTAL per-state invariants (which state must
# carry which digest / id / timestamp / problem, and which must be
# null), enforced at the durable boundary so a FALSE receipt — a
# `delivered_by_edit` with no rendered digest, no edited message id and
# no timestamps — is UNREPRESENTABLE rather than merely refused
# downstream. An out-of-table combination names the exact field.
PROBLEM_DELIVERY_STATE_FIELDS = (
    "workflow_record_delivery_state_fields"
)
# DI-REMOTE-3 I5 (round-02 G1): beyond per-field SHAPE, the delivery
# receipt must be RELATIONALLY TRUE of its record. A `delivered_by_edit`
# receipt whose `edited_message_id` does not name the bound placeholder
# message claims an edit that never happened, and the edit engine never
# re-claims a delivered_by_edit — so the lie would suppress delivery
# permanently. Failing closed here makes it UNREPRESENTABLE.
PROBLEM_DELIVERY_BINDING = "workflow_record_delivery_binding"
PROBLEM_UNKNOWN_PHASE = "workflow_unknown_phase"
PROBLEM_INVALID_TRANSITION = "workflow_invalid_transition"


class RecordError(Exception):
    """A workflow record failed validation; message is actionable."""

    def __init__(self, message, problem):
        super(RecordError, self).__init__(message)
        self.problem = problem


def _fail(problem, message):
    raise RecordError(message, problem)


def _require_dict(value, location):
    if not isinstance(value, dict):
        _fail(
            PROBLEM_BAD_TYPE,
            "%s must be an object, not %s"
            % (location, type(value).__name__),
        )


def _require_closed_keys(value, allowed, location):
    unknown = sorted(set(value) - set(allowed))
    if unknown:
        _fail(
            PROBLEM_UNKNOWN_KEY,
            "%s has unknown keys: %s (the key set is closed; an"
            " unexpected key could carry unauthorized meaning)"
            % (location, ", ".join(repr(key) for key in unknown)),
        )
    missing = sorted(set(allowed) - set(value))
    if missing:
        _fail(
            PROBLEM_MISSING_KEY,
            "%s is missing required keys: %s"
            % (location, ", ".join(repr(key) for key in missing)),
        )


def _require_str(value, location, max_chars, allow_empty=False):
    if not isinstance(value, str):
        _fail(
            PROBLEM_BAD_TYPE,
            "%s must be a string, not %s"
            % (location, type(value).__name__),
        )
    if not allow_empty and not value:
        _fail(PROBLEM_BAD_VALUE, "%s must be non-empty" % location)
    if len(value) > max_chars:
        _fail(
            PROBLEM_TOO_LARGE,
            "%s is %d characters; the hard bound is %d characters"
            " and the record is refused, not truncated"
            % (location, len(value), max_chars),
        )


def _require_int(value, location, minimum=None):
    if isinstance(value, bool) or not isinstance(value, int):
        _fail(
            PROBLEM_BAD_TYPE,
            "%s must be an integer (bool is not accepted), not %r"
            % (location, value),
        )
    if minimum is not None and value < minimum:
        _fail(
            PROBLEM_BAD_VALUE,
            "%s must be >= %d; got %d" % (location, minimum, value),
        )


def _require_timestamp(value, location):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        _fail(
            PROBLEM_BAD_TYPE,
            "%s must be a number (bool is not accepted), not %r"
            % (location, value),
        )
    if value < 0:
        _fail(
            PROBLEM_BAD_VALUE,
            "%s must be non-negative; got %r" % (location, value),
        )


def _require_bool(value, location):
    if not isinstance(value, bool):
        _fail(
            PROBLEM_BAD_TYPE,
            "%s must be a boolean, not %s"
            % (location, type(value).__name__),
        )


def _require_hex(value, location, length):
    _require_str(value, location, max_chars=length)
    if len(value) != length or any(
        ch not in "0123456789abcdef" for ch in value
    ):
        _fail(
            PROBLEM_BAD_VALUE,
            "%s must be exactly %d lowercase hex characters"
            % (location, length),
        )


def _require_member(value, allowed, location):
    if not isinstance(value, str) or value not in allowed:
        _fail(
            PROBLEM_BAD_VALUE,
            "%s must be one of %s; got %r (unknown values fail"
            " closed)" % (location, ", ".join(allowed), value),
        )


# Line terminators beyond the ASCII control range. Together with the
# ord<0x20 controls these cover EVERY terminator str.splitlines()
# honours, so a value passing these checks can never span more than
# one rendered line (round-02 finding F-6: the binding-line block
# renders these values UNQUOTED at column 0, so they must be
# line-free BY VALIDATION — the containment class the rendering
# registry calls "line-free").
_NON_ASCII_LINE_TERMINATORS = "\x85\u2028\u2029"

# Characters git's own check-ref-format forbids, refused here as a
# closed grammar for the DISPLAYED baseline ref (round-02 F-6: the
# ref is Codex-authored free text; a ref that could carry line
# structure or ref-syntax metacharacters is refused, never rendered).
_REF_FORBIDDEN_CHARS = frozenset("~^:?*[\\")


def baseline_ref_grammar_problem(value):
    """The closed grammar for a displayed baseline ref.

    Returns an actionable reason string when ``value`` violates the
    grammar, or None when it conforms. The SINGLE source for both
    enforcement layers (the mission-document validator and the record
    validator), each of which wraps the reason in its own problem
    code. The grammar follows git check-ref-format: no whitespace of
    any kind (every str.splitlines() terminator included), no control
    characters, none of ``~ ^ : ? * [ \\``, no ``..``, no leading or
    trailing ``/``, no trailing ``.lock``.
    """
    if not isinstance(value, str) or not value:
        return "must be a non-empty string"
    for ch in value:
        if ord(ch) < 0x20 or ord(ch) == 0x7F:
            return "contains control character %r" % ch
        if ch.isspace() or ch in _NON_ASCII_LINE_TERMINATORS:
            return (
                "contains whitespace/line-terminator %r (a ref with"
                " line structure could forge displayed binding"
                " lines)" % ch
            )
        if ch in _REF_FORBIDDEN_CHARS:
            return "contains forbidden ref character %r" % ch
    if ".." in value:
        return "contains '..'"
    if value.startswith("/") or value.endswith("/"):
        return "has a leading or trailing '/'"
    if value.endswith(".lock"):
        return "ends with '.lock'"
    return None


def path_character_problem(value):
    """Line-free path rule (round-02 F-6), the SHARED public helper.

    Returns an actionable reason string when ``value`` contains a
    control character or any line-terminator form (a path with line
    structure could forge displayed binding lines), or None when it
    is line-free. Spaces stay legal — they cannot break a rendered
    line. Used by the record validator here and by the mission
    document validator (each layer wraps it in its own problem code).
    """
    if not isinstance(value, str):
        return "must be a string"
    for ch in value:
        if (
            ord(ch) < 0x20
            or ord(ch) == 0x7F
            or ch in _NON_ASCII_LINE_TERMINATORS
        ):
            return (
                "contains control or line-terminator character %r"
                % (ch,)
            )
    return None


def _require_realpath(value, location):
    _require_str(value, location, max_chars=1024)
    if not os.path.isabs(value):
        _fail(
            PROBLEM_BAD_VALUE,
            "%s must be an absolute real path; got %r"
            % (location, value),
        )
    reason = path_character_problem(value)
    if reason is not None:
        _fail(
            PROBLEM_PATH_CHARACTER,
            "%s %s; a path with line structure could forge displayed"
            " binding lines, so it is refused" % (location, reason),
        )


_TOP_LEVEL_KEYS = (
    "schema_version",
    "workflow_id",
    "human_intent",
    "control_identity",
    "target",
    "approved_baseline",
    "mission_authorization",
    "telegram",
    "approval",
    "handoff",
    "phase",
    "workspace_lease",
    "receipts",
    "codex_turns",
    "ambiguity",
    # I5 lifecycle-completion fields (durable, nullable, NOT rendered
    # — like receipts/codex_turns they are mutable non-authority
    # state, so they are outside the render binding).
    "target_engine",
    "verified_result",
    # DI-REMOTE-3 I1 (plan §2.2): the bot-owned per-workflow RESULT
    # PLACEHOLDER binding. NULLABLE, and null is not "not needed" — it
    # means the record predates / sits outside the placeholder
    # architecture (the §4 legacy population, delivered by the legacy
    # at-most-once lane). Durable, mutable, non-authority state, so it
    # is outside the render binding exactly like verified_result.
    #
    # WORKFLOW_SCHEMA_VERSION is deliberately NOT bumped for this key
    # (plan §2.1): a bump routes every record already on disk into
    # 'tgop migrate-workflows', whose only v1->v2 behaviour is
    # RETIREMENT, which would destroy in-flight COMPLETED records — a
    # direct violation of the migration truth in strategy §4. The key
    # is instead materialized on records already on disk by the
    # explicit, non-fabricating store._normalize_additive_keys at the
    # LOAD boundary; validation below stays fully strict and closed.
    "result_placeholder",
    "result_delivery",
    # I6 carried item: the last DISTINCT target observation
    # (task_status, completeness, observed_at) — written only when the
    # observed pair changes, so an indefinitely unobservable target no
    # longer renders in /status like a healthy one. Nullable, mutable,
    # non-authority: outside the render binding.
    "last_observation",
    "delivery_authority",
)

# Bounded verified-result summary (E-5 shape: capability-free, the
# text the human sees as the mission outcome). Hard constant.
MAX_VERIFIED_SUMMARY_CHARS = 4000


def _validate_control_identity(value, location):
    _require_dict(value, location)
    _require_closed_keys(
        value, ("repository_realpath", "policy_digest_sha256"), location
    )
    _require_realpath(
        value["repository_realpath"], location + ".repository_realpath"
    )
    _require_hex(
        value["policy_digest_sha256"],
        location + ".policy_digest_sha256",
        64,
    )


def _validate_target(value, location):
    _require_dict(value, location)
    _require_closed_keys(
        value,
        ("canonical_host", "owner", "repo", "canonical_url",
         "issue_or_pr"),
        location,
    )
    _require_str(value["canonical_host"], location + ".canonical_host",
                 max_chars=255)
    _require_str(value["owner"], location + ".owner", max_chars=255)
    _require_str(value["repo"], location + ".repo", max_chars=255)
    _require_str(
        value["canonical_url"], location + ".canonical_url",
        max_chars=canonical.MAX_TARGET_URL_CHARS,
    )
    # The ONE canonicalizer validates the stored URL on every load and
    # save; the repository identity fields must agree with its parse,
    # so a record can never carry a URL naming one repository and
    # display fields naming another.
    try:
        target = canonical.canonicalize_repository_url(
            value["canonical_url"]
        )
    except canonical.CanonicalizationError as exc:
        _fail(
            PROBLEM_BAD_VALUE,
            "%s.canonical_url failed canonicalization (%s): %s"
            % (location, exc.problem, exc),
        )
    if (
        value["canonical_host"] != target.host
        or value["owner"] != target.owner
        or value["repo"] != target.repo
    ):
        _fail(
            PROBLEM_BAD_VALUE,
            "%s host/owner/repo (%r, %r, %r) do not match the"
            " canonical parse of canonical_url (%r, %r, %r)"
            % (location, value["canonical_host"], value["owner"],
               value["repo"], target.host, target.owner, target.repo),
        )
    issue_or_pr = value["issue_or_pr"]
    if issue_or_pr is None:
        # Repository-only target: representable, and rendered with
        # its own unambiguous binding-line form.
        return
    _require_dict(issue_or_pr, location + ".issue_or_pr")
    _require_closed_keys(
        issue_or_pr, ("kind", "number"), location + ".issue_or_pr"
    )
    _require_member(
        issue_or_pr["kind"], ISSUE_OR_PR_KINDS,
        location + ".issue_or_pr.kind",
    )
    _require_int(
        issue_or_pr["number"], location + ".issue_or_pr.number",
        minimum=1,
    )
    # RULING R-18 — the CANONICAL CONTRACT, enforced at the RECORD
    # boundary so the bad state is UNREPRESENTABLE rather than merely
    # refused downstream (the same posture as
    # `result_placeholder.chat_id == telegram.chat_id`).
    #
    # `canonical.canonicalize_target_url` derives `number` from the
    # FULL issue/PR URL, and that whole URL is bounded by
    # `MAX_TARGET_URL_CHARS`. But the record stores the REPOSITORY
    # url, so `number` is NOT re-derivable from it, and this validator
    # previously bounded the number not at all. The schema therefore
    # accepted records the canonicalizer could never produce — and
    # THAT mismatch, not the render guard, was the defect.
    #
    # The bound below is the canonicalizer's OWN, reconstructed from
    # the canonical constants: the full issue/PR URL this record
    # describes must itself fit. Nothing is hard-coded — a change to
    # MAX_TARGET_URL_CHARS or to either segment name moves this bound
    # with it.
    #
    # This does NOT replace the runtime render guard, which stays as
    # DEFENCE IN DEPTH and keeps its load-bearing labelling. It makes
    # the guard cover only what is already unrepresentable here.
    segment = (
        canonical.ISSUE_SEGMENT
        if issue_or_pr["kind"] == ISSUE_OR_PR_KIND_ISSUE
        else canonical.PULL_SEGMENT
    )
    issue_url_chars = (
        len(value["canonical_url"])
        + len("/") + len(segment) + len("/")
        + len(str(issue_or_pr["number"]))
    )
    if issue_url_chars > canonical.MAX_TARGET_URL_CHARS:
        _fail(
            PROBLEM_ISSUE_URL_TOO_LONG,
            "%s.issue_or_pr.number %d makes the canonical %s URL %d"
            " characters; the canonical bound is %d. The canonicalizer"
            " could never emit this target, so the record is refused"
            " here rather than accepted and found undeliverable later"
            % (location, issue_or_pr["number"], segment,
               issue_url_chars, canonical.MAX_TARGET_URL_CHARS),
        )


def _validate_approved_baseline(value, location):
    _require_dict(value, location)
    _require_closed_keys(value, ("ref", "commit_sha"), location)
    _require_str(value["ref"], location + ".ref", max_chars=512)
    # Round-02 F-6: the ref is Codex-authored and rendered UNQUOTED
    # on a binding line, so it must satisfy the closed ref grammar —
    # line structure is UNREPRESENTABLE in a valid record.
    reason = baseline_ref_grammar_problem(value["ref"])
    if reason is not None:
        _fail(
            PROBLEM_BASELINE_REF,
            "%s.ref %s; a ref outside the closed git ref grammar"
            " could forge displayed binding lines, so it is refused"
            % (location + ".ref", reason),
        )
    _require_hex(value["commit_sha"], location + ".commit_sha", 40)


def _validate_mission_authorization(value, location):
    _require_dict(value, location)
    _require_closed_keys(
        value,
        ("rendered_text", "digest_sha256", "revision")
        + rendering.AUTHORITY_CONTENT_KEYS,
        location,
    )
    for key in rendering.AUTHORITY_CONTENT_KEYS:
        _require_str(
            value[key], "%s.%s" % (location, key),
            max_chars=MAX_AUTHORITY_FIELD_CHARS,
        )
    _require_str(
        value["rendered_text"], location + ".rendered_text",
        max_chars=MAX_AUTHORITY_TEXT_CHARS,
    )
    _require_hex(
        value["digest_sha256"], location + ".digest_sha256", 64
    )
    _require_int(value["revision"], location + ".revision", minimum=1)
    if text_digest(value["rendered_text"]) != value["digest_sha256"]:
        _fail(
            PROBLEM_DIGEST_MISMATCH,
            "%s.digest_sha256 does not match the digest of the stored"
            " rendered_text; the record no longer binds the exact"
            " authorized text and is refused" % location,
        )


def _validate_telegram(value, location):
    _require_dict(value, location)
    _require_closed_keys(
        value,
        ("user_id", "chat_id", "message_ids", "plan_message_id"),
        location,
    )
    _require_int(value["user_id"], location + ".user_id", minimum=1)
    _require_int(value["chat_id"], location + ".chat_id", minimum=1)
    message_ids = value["message_ids"]
    if not isinstance(message_ids, list):
        _fail(
            PROBLEM_BAD_TYPE,
            "%s.message_ids must be a list, not %s"
            % (location, type(message_ids).__name__),
        )
    if len(message_ids) > MAX_TELEGRAM_MESSAGE_IDS:
        _fail(
            PROBLEM_TOO_LARGE,
            "%s.message_ids has %d entries; the hard bound is %d"
            % (location, len(message_ids), MAX_TELEGRAM_MESSAGE_IDS),
        )
    for index, item in enumerate(message_ids):
        _require_int(
            item, "%s.message_ids[%d]" % (location, index), minimum=1
        )
    plan_message_id = value["plan_message_id"]
    if plan_message_id is not None:
        _require_int(
            plan_message_id, location + ".plan_message_id", minimum=1
        )


def _validate_approval(value, location):
    _require_dict(value, location)
    _require_closed_keys(
        value,
        ("approval_kind", "nonce", "created_at", "expires_at",
         "consumed_at", "consumed_by_update_id", "decision",
         "superseded"),
        location,
    )
    _require_member(
        value["approval_kind"], APPROVAL_KINDS,
        location + ".approval_kind",
    )
    _require_str(value["nonce"], location + ".nonce",
                 max_chars=MAX_ID_CHARS)
    _require_timestamp(value["created_at"], location + ".created_at")
    _require_timestamp(value["expires_at"], location + ".expires_at")
    if value["consumed_at"] is not None:
        _require_timestamp(
            value["consumed_at"], location + ".consumed_at"
        )
    if value["consumed_by_update_id"] is not None:
        _require_int(
            value["consumed_by_update_id"],
            location + ".consumed_by_update_id",
        )
    if value["decision"] is not None:
        _require_member(
            value["decision"], DECISIONS, location + ".decision"
        )
    _require_bool(value["superseded"], location + ".superseded")


def _validate_handoff(value, location):
    _require_dict(value, location)
    _require_closed_keys(
        value, ("revision", "text", "digest_sha256"), location
    )
    _require_int(value["revision"], location + ".revision", minimum=1)
    _require_str(
        value["text"], location + ".text",
        max_chars=MAX_AUTHORITY_TEXT_CHARS,
    )
    if value["text"] != value["text"].strip():
        # Byte-exact dispatch (I5): the spawn bridge normalizes
        # surrounding whitespace, so a padded handoff could not reach
        # the target byte-identically. Padded text is therefore
        # UNREPRESENTABLE in a valid record — the bridge's strip is
        # provably an identity for every dispatchable handoff.
        _fail(
            PROBLEM_BAD_VALUE,
            "%s.text must carry no leading or trailing whitespace"
            " (byte-exact dispatch requirement)" % location,
        )
    _require_hex(
        value["digest_sha256"], location + ".digest_sha256", 64
    )
    if text_digest(value["text"]) != value["digest_sha256"]:
        _fail(
            PROBLEM_DIGEST_MISMATCH,
            "%s.digest_sha256 does not match the digest of the stored"
            " handoff text; the record no longer binds the exact"
            " approved handoff and is refused" % location,
        )


def _validate_workspace_lease(value, location):
    if value is None:
        return
    _require_dict(value, location)
    _require_closed_keys(
        value,
        ("lease_id", "path_realpath", "acquired_at", "released_at"),
        location,
    )
    _require_str(value["lease_id"], location + ".lease_id",
                 max_chars=MAX_ID_CHARS)
    _require_realpath(
        value["path_realpath"], location + ".path_realpath"
    )
    _require_timestamp(value["acquired_at"], location + ".acquired_at")
    if value["released_at"] is not None:
        _require_timestamp(
            value["released_at"], location + ".released_at"
        )


def _validate_receipts(value, location):
    if not isinstance(value, list):
        _fail(
            PROBLEM_BAD_TYPE,
            "%s must be a list, not %s"
            % (location, type(value).__name__),
        )
    if len(value) > MAX_RECEIPTS:
        _fail(
            PROBLEM_TOO_LARGE,
            "%s has %d entries; the hard bound is %d"
            % (location, len(value), MAX_RECEIPTS),
        )
    for index, receipt in enumerate(value):
        where = "%s[%d]" % (location, index)
        _require_dict(receipt, where)
        _require_closed_keys(
            receipt,
            ("kind", "turn_id", "recorded_at", "digest",
             "bounded_summary"),
            where,
        )
        _require_member(receipt["kind"], RECEIPT_KINDS, where + ".kind")
        _require_str(receipt["turn_id"], where + ".turn_id",
                     max_chars=MAX_ID_CHARS)
        _require_timestamp(
            receipt["recorded_at"], where + ".recorded_at"
        )
        _require_hex(receipt["digest"], where + ".digest", 64)
        _require_str(
            receipt["bounded_summary"], where + ".bounded_summary",
            max_chars=MAX_BOUNDED_SUMMARY_CHARS, allow_empty=True,
        )


def _validate_codex_turns(value, location):
    if not isinstance(value, list):
        _fail(
            PROBLEM_BAD_TYPE,
            "%s must be a list, not %s"
            % (location, type(value).__name__),
        )
    if len(value) > MAX_CODEX_TURNS:
        _fail(
            PROBLEM_TOO_LARGE,
            "%s has %d entries; the hard bound is %d"
            % (location, len(value), MAX_CODEX_TURNS),
        )
    for index, turn in enumerate(value):
        where = "%s[%d]" % (location, index)
        _require_dict(turn, where)
        _require_closed_keys(
            turn, ("turn_id", "role", "process_id", "recorded_at"),
            where,
        )
        _require_str(turn["turn_id"], where + ".turn_id",
                     max_chars=MAX_ID_CHARS)
        _require_member(turn["role"], TURN_ROLES, where + ".role")
        _require_int(turn["process_id"], where + ".process_id",
                     minimum=1)
        _require_timestamp(turn["recorded_at"], where + ".recorded_at")


def _validate_ambiguity(value, location):
    _require_dict(value, location)
    _require_closed_keys(value, ("state", "detail"), location)
    _require_member(
        value["state"], AMBIGUITY_STATES, location + ".state"
    )
    if value["detail"] is not None:
        _require_str(
            value["detail"], location + ".detail",
            max_chars=MAX_AMBIGUITY_DETAIL_CHARS,
        )


def _validate_target_engine(value, location):
    if value is None:
        return
    _require_dict(value, location)
    _require_closed_keys(
        value, ("alias", "task_id", "repo", "dispatched_at"), location
    )
    for key in ("alias", "task_id", "repo"):
        _require_str(value[key], "%s.%s" % (location, key),
                     max_chars=MAX_ID_CHARS)
    _require_timestamp(
        value["dispatched_at"], location + ".dispatched_at"
    )


def _validate_verified_result(value, location):
    if value is None:
        return
    _require_dict(value, location)
    _require_closed_keys(
        value, ("summary", "digest", "recorded_at"), location
    )
    _require_str(value["summary"], location + ".summary",
                 max_chars=MAX_VERIFIED_SUMMARY_CHARS, allow_empty=True)
    _require_hex(value["digest"], location + ".digest", 64)
    if text_digest(value["summary"]) != value["digest"]:
        _fail(
            PROBLEM_DIGEST_MISMATCH,
            "%s.digest does not match the digest of the stored"
            " verified-result summary" % location,
        )
    _require_timestamp(
        value["recorded_at"], location + ".recorded_at"
    )


def _validate_last_observation(value, location):
    if value is None:
        return
    _require_dict(value, location)
    _require_closed_keys(
        value, ("task_status", "completeness", "observed_at"), location
    )
    # Both projected fields are nullable strings: an UNOBSERVABLE target
    # records (None, None) — that pair is itself a distinct, actionable
    # observation, never confused with a healthy running one.
    for key in ("task_status", "completeness"):
        if value[key] is not None:
            _require_str(value[key], "%s.%s" % (location, key),
                         max_chars=MAX_ID_CHARS)
    _require_timestamp(
        value["observed_at"], location + ".observed_at"
    )


DELIVERY_RESERVED = "reserved"
DELIVERY_DELIVERED = "delivered"
DELIVERY_PARTIAL = "partial"
# The LEGACY three, unchanged in meaning (strategy §4): a legacy
# marker stays exactly as truthful and as terminal as it is today, and
# its /status phrasing is unchanged (pinned verbatim by T-X2).
DELIVERY_LEGACY_STATES = (
    DELIVERY_RESERVED, DELIVERY_DELIVERED, DELIVERY_PARTIAL,
)

# DI-REMOTE-3 I1 (plan §3.3): the edit-path delivery states. Declared
# here so the durable schema admits them; the delivery engine that
# WRITES them, and the /status branch that RENDERS them, land in I5.
# Until then they are representable but never produced — an honest
# statement of what this increment does and does not deliver.
DELIVERY_EDIT_PENDING = "edit_pending"
DELIVERY_DELIVERED_BY_EDIT = "delivered_by_edit"
DELIVERY_DEGRADED_UNBINDABLE = "degraded_unbindable"
DELIVERY_DEGRADED_UNRENDERABLE = "degraded_unrenderable"
DELIVERY_EDIT_INDEFINITE = "edit_indefinite"
DELIVERY_EDIT_STATES = (
    DELIVERY_EDIT_PENDING,
    DELIVERY_DELIVERED_BY_EDIT,
    DELIVERY_DEGRADED_UNBINDABLE,
    DELIVERY_DEGRADED_UNRENDERABLE,
    DELIVERY_EDIT_INDEFINITE,
)
DELIVERY_STATES = DELIVERY_LEGACY_STATES + DELIVERY_EDIT_STATES

# The three keys every result_delivery marker has carried since the
# DI-REMOTE-2 delivery increment.
RESULT_DELIVERY_LEGACY_KEYS = (
    "state", "reserved_at", "telegram_message_id",
)
# DI-REMOTE-3 I1 (plan §2.3): six ADDITIVE nullable fields. They are
# OPTIONAL in the closed key set, not required, because a legacy
# marker carrying only the three keys above already exists in stores
# and in the existing (unmodifiable) tests and must keep validating
# byte-for-byte as it does today; ABSENT is exactly equivalent to
# null. store._normalize_additive_keys materializes them at the LOAD
# boundary so a loaded marker always has the total shape. Unknown keys
# are still REFUSED — the dict stays closed.
RESULT_DELIVERY_ADDITIVE_KEYS = (
    "verified_result_digest",
    "rendered_digest",
    "edited_message_id",
    "attempted_at",
    "settled_at",
    "problem",
)


# --- Result placeholder (plan §2.2 / §3.1) ---------------------------
PLACEHOLDER_REQUIRED = "required"
PLACEHOLDER_SENDING = "sending"
PLACEHOLDER_BOUND = "bound"
PLACEHOLDER_FAILED_UNSENT = "failed_unsent"
PLACEHOLDER_INDEFINITE = "indefinite"
PLACEHOLDER_UNBINDABLE = "unbindable"
PLACEHOLDER_STATES = (
    PLACEHOLDER_REQUIRED,
    PLACEHOLDER_SENDING,
    PLACEHOLDER_BOUND,
    PLACEHOLDER_FAILED_UNSENT,
    PLACEHOLDER_INDEFINITE,
    PLACEHOLDER_UNBINDABLE,
)

RESULT_PLACEHOLDER_KEYS = (
    "state",
    "chat_id",
    "message_id",
    "requested_at",
    "sent_at",
    "bound_at",
    "text_digest",
)

# The CLOSED state/field table of plan §3.1. True means the field MUST
# be non-null in that state; False means it MUST be null. There is no
# permissive fallthrough: a state absent from this table is refused,
# and every present/absent combination outside the table fails closed
# with the field's own problem code.
#
#   required      requested durably; nothing has been sent yet.
#   sending       write-ahead of the send intent. sent_at and
#                 text_digest are recorded BEFORE the send, because an
#                 ``indefinite`` outcome may leave a real message on
#                 screen: without the digest of the exact text that was
#                 about to be sent, the object the human is told to
#                 recover (strategy §1.3 — the text carries the
#                 workflow id) is unidentifiable.
#   failed_unsent send proved definite-zero-effect; nothing exists.
#   indefinite    R-5 ambiguous. TERMINAL — never auto-retried.
#   bound         the object exists and is ours.
#   unbindable    R-3, a POST-BOUND state: the binding it lost is
#                 retained so /status can name it truthfully.
_PLACEHOLDER_FIELD_TABLE = {
    PLACEHOLDER_REQUIRED: {
        "message_id": False, "sent_at": False,
        "bound_at": False, "text_digest": False,
    },
    PLACEHOLDER_SENDING: {
        "message_id": False, "sent_at": True,
        "bound_at": False, "text_digest": True,
    },
    PLACEHOLDER_FAILED_UNSENT: {
        "message_id": False, "sent_at": True,
        "bound_at": False, "text_digest": True,
    },
    PLACEHOLDER_INDEFINITE: {
        "message_id": False, "sent_at": True,
        "bound_at": False, "text_digest": True,
    },
    PLACEHOLDER_BOUND: {
        "message_id": True, "sent_at": True,
        "bound_at": True, "text_digest": True,
    },
    PLACEHOLDER_UNBINDABLE: {
        "message_id": True, "sent_at": True,
        "bound_at": True, "text_digest": True,
    },
}

_PLACEHOLDER_PROBLEM_BY_FIELD = {
    "message_id": PROBLEM_PLACEHOLDER_MESSAGE_ID,
    "sent_at": PROBLEM_PLACEHOLDER_SENT_AT,
    "bound_at": PROBLEM_PLACEHOLDER_BOUND_AT,
    "text_digest": PROBLEM_PLACEHOLDER_TEXT_DIGEST,
}


def _require_closed_keys_with_optional(
    value, required, optional, location
):
    """Closed-key check where some keys are additive and optional.

    Unknown keys are refused exactly as ``_require_closed_keys``
    refuses them (the dict stays CLOSED); only the listed optional
    keys may be absent, and their absence means exactly null.
    """
    unknown = sorted(set(value) - set(required) - set(optional))
    if unknown:
        _fail(
            PROBLEM_UNKNOWN_KEY,
            "%s has unknown keys: %s (the key set is closed; an"
            " unexpected key could carry unauthorized meaning)"
            % (location, ", ".join(repr(key) for key in unknown)),
        )
    missing = sorted(set(required) - set(value))
    if missing:
        _fail(
            PROBLEM_MISSING_KEY,
            "%s is missing required keys: %s"
            % (location, ", ".join(repr(key) for key in missing)),
        )


def _validate_result_placeholder(value, location, telegram_chat_id):
    """Validate the bot-owned result placeholder binding.

    ``None`` is the LEGACY lane (plan §1.1): the record predates or
    sits outside the placeholder architecture. It is NOT "placeholder
    not needed", and nothing here fabricates one.
    """
    if value is None:
        return
    _require_dict(value, location)
    _require_closed_keys(value, RESULT_PLACEHOLDER_KEYS, location)
    _require_member(
        value["state"], PLACEHOLDER_STATES, location + ".state"
    )
    state = value["state"]
    _require_int(value["chat_id"], location + ".chat_id", minimum=1)
    if value["chat_id"] != telegram_chat_id:
        _fail(
            PROBLEM_PLACEHOLDER_CHAT_MISMATCH,
            "%s.chat_id is %r but the record's telegram.chat_id is"
            " %r; the placeholder is copied from the record at request"
            " time and is thereafter immutable, so a placeholder bound"
            " in another chat is unrepresentable"
            % (location, value["chat_id"], telegram_chat_id),
        )
    _require_timestamp(
        value["requested_at"], location + ".requested_at"
    )
    expected = _PLACEHOLDER_FIELD_TABLE.get(state)
    if expected is None:
        # No permissive fallthrough: a state with no row is refused
        # rather than validated by omission.
        _fail(
            PROBLEM_PLACEHOLDER_STATE_FIELDS,
            "%s.state %r has no row in the placeholder state/field"
            " table; it is refused rather than accepted by omission"
            % (location, state),
        )
    for field in ("message_id", "sent_at", "bound_at", "text_digest"):
        field_location = "%s.%s" % (location, field)
        present = value[field] is not None
        if present != expected[field]:
            _fail(
                _PLACEHOLDER_PROBLEM_BY_FIELD[field],
                "%s must be %s in placeholder state %r; got %r"
                % (
                    field_location,
                    "non-null" if expected[field] else "null",
                    state,
                    value[field],
                ),
            )
        if not present:
            continue
        if field == "message_id":
            _require_int(value[field], field_location, minimum=1)
        elif field == "text_digest":
            _require_hex(value[field], field_location, 64)
        else:
            _require_timestamp(value[field], field_location)



# DI-REMOTE-3 I5 (round-01 F1): the delivery state/field table. True =
# the additive field MUST be non-null in that state; False = it MUST be
# null. This mirrors exactly what telegram_operator/adapter.py writes:
#   - the legacy lane (reserved/delivered/partial) is written as the
#     historical three-key dict, so every additive field is null;
#   - edit_pending is the write-ahead: the digests and attempted_at are
#     set, but settled_at, edited_message_id and problem are not yet;
#   - delivered_by_edit records the edited message id and settles, with
#     no problem;
#   - the three degraded/indefinite edit states settle with a problem
#     and no edited message id.
# `telegram_message_id` is governed separately above (non-null iff the
# legacy DELIVERED state). The table is pinned to the engine's ACTUAL
# output by the cross-boundary test
# tests/test_di_remote_3_delivery.py::EngineFieldTablePinTests
# ::test_engine_output_satisfies_the_delivery_field_table, which drives
# the real adapter to produce ALL EIGHT of these states — the five
# edit-lane states plus legacy delivered/reserved/partial — and asserts
# each on-disk marker's field profile against an independently authored
# expectation. A table that drifts from what the engine writes makes
# that state's delivery unsavable and fails there; its totality
# assertion also fails if a delivery state is added without being driven.
_DELIVERY_FIELD_TABLE = {
    DELIVERY_RESERVED: {
        "verified_result_digest": False, "rendered_digest": False,
        "edited_message_id": False, "attempted_at": False,
        "settled_at": False, "problem": False,
    },
    DELIVERY_DELIVERED: {
        "verified_result_digest": False, "rendered_digest": False,
        "edited_message_id": False, "attempted_at": False,
        "settled_at": False, "problem": False,
    },
    DELIVERY_PARTIAL: {
        "verified_result_digest": False, "rendered_digest": False,
        "edited_message_id": False, "attempted_at": False,
        "settled_at": False, "problem": False,
    },
    DELIVERY_EDIT_PENDING: {
        "verified_result_digest": True, "rendered_digest": True,
        "edited_message_id": False, "attempted_at": True,
        "settled_at": False, "problem": False,
    },
    DELIVERY_DELIVERED_BY_EDIT: {
        "verified_result_digest": True, "rendered_digest": True,
        "edited_message_id": True, "attempted_at": True,
        "settled_at": True, "problem": False,
    },
    DELIVERY_DEGRADED_UNBINDABLE: {
        "verified_result_digest": True, "rendered_digest": True,
        "edited_message_id": False, "attempted_at": True,
        "settled_at": True, "problem": True,
    },
    DELIVERY_DEGRADED_UNRENDERABLE: {
        "verified_result_digest": True, "rendered_digest": True,
        "edited_message_id": False, "attempted_at": True,
        "settled_at": True, "problem": True,
    },
    DELIVERY_EDIT_INDEFINITE: {
        "verified_result_digest": True, "rendered_digest": True,
        "edited_message_id": False, "attempted_at": True,
        "settled_at": True, "problem": True,
    },
}


def _validate_result_delivery(value, location):
    if value is None:
        return
    _require_dict(value, location)
    _require_closed_keys_with_optional(
        value, RESULT_DELIVERY_LEGACY_KEYS,
        RESULT_DELIVERY_ADDITIVE_KEYS, location,
    )
    _require_member(value["state"], DELIVERY_STATES,
                    location + ".state")
    _require_timestamp(value["reserved_at"], location + ".reserved_at")
    if value["state"] == DELIVERY_DELIVERED:
        # A DELIVERED marker carries the exact Telegram message id.
        # A RESERVED marker (crash window between reserve and confirm)
        # and a PARTIAL marker (some chunks displayed, the send did not
        # complete) carry none: both are TERMINAL for auto-delivery —
        # never re-sent (no double, and PARTIAL never re-displays the
        # chunks already shown) and surfaced honestly in /status (no
        # silent drop).
        _require_int(
            value["telegram_message_id"],
            location + ".telegram_message_id", minimum=1,
        )
    elif value["telegram_message_id"] is not None:
        _fail(
            PROBLEM_BAD_VALUE,
            "%s.telegram_message_id must be null unless the delivery is"
            " confirmed delivered" % location,
        )
    # DI-REMOTE-3 I5 (round-01 F1): the six additive fields now carry
    # TOTAL per-state invariants, enforced HERE at the durable boundary
    # so a FALSE receipt is UNREPRESENTABLE rather than merely refused
    # downstream — the same bad-state-representable class R-18 closed
    # for issue numbers. `_DELIVERY_FIELD_TABLE` mirrors EXACTLY what
    # the delivery engine writes for each state on BOTH lanes: the
    # legacy three (reserved/delivered/partial) carry none of the
    # additive fields; each edit-lane state carries exactly its proven
    # digests / edited id / timestamps / problem and nulls the rest. A
    # state with no row is refused, never accepted by omission.
    state = value["state"]
    expected = _DELIVERY_FIELD_TABLE.get(state)
    if expected is None:
        _fail(
            PROBLEM_DELIVERY_STATE_FIELDS,
            "%s.state %r has no row in the delivery state/field table;"
            " it is refused rather than accepted by omission"
            % (location, state),
        )
    for key in RESULT_DELIVERY_ADDITIVE_KEYS:
        field_location = "%s.%s" % (location, key)
        present = value.get(key) is not None
        if present != expected[key]:
            _fail(
                PROBLEM_DELIVERY_STATE_FIELDS,
                "%s must be %s in delivery state %r; got %r"
                % (
                    field_location,
                    "non-null" if expected[key] else "null",
                    state, value.get(key),
                ),
            )
        if not present:
            continue
        if key in ("verified_result_digest", "rendered_digest"):
            _require_hex(value[key], field_location, 64)
        elif key == "edited_message_id":
            _require_int(value[key], field_location, minimum=1)
        elif key in ("attempted_at", "settled_at"):
            _require_timestamp(value[key], field_location)
        elif key == "problem":
            # Bounded truthful detail for a degraded delivery, rendered
            # in /status. It reuses MAX_BOUNDED_SUMMARY_CHARS — the
            # module's existing bound for a stored, human-displayed
            # summary string (receipts' bounded_summary) — because that
            # is exactly what this field is.
            _require_str(
                value[key], field_location,
                max_chars=MAX_BOUNDED_SUMMARY_CHARS,
            )


def _validate_delivery_relations(document, location):
    """DI-REMOTE-3 I5 (round-02 G1 / round-03 H1): RELATIONAL truth of
    the delivery receipt — beyond the per-field SHAPE
    `_DELIVERY_FIELD_TABLE` enforces, the receipt's values must be TRUE
    of the record they sit in.

    An EDIT-LANE receipt (any state in `DELIVERY_EDIT_STATES`) could
    only have been written by the edit engine, and the engine claims a
    record for editing ONLY when
    `telegram_operator/adapter.py::_edit_delivery_claimable`'s own
    preconditions hold: `phase == COMPLETED`, a non-null
    `verified_result`, and a placeholder in state `bound`. Every edit
    state (delivered_by_edit, edit_pending, edit_indefinite, and both
    degraded states) is written with exactly those held — the
    cross-boundary engine-pin test drives each edit state and
    re-validates the engine's real output under exactly these rules, so
    a drift would fail there — so a receipt in ANY edit state without
    them is IMPOSSIBLE: the engine never produced it, and it
    would strand FOREVER (the same preconditions fail before the engine
    ever looks at the receipt, so it is never claimed, retried, or
    surfaced as broken). Failing closed makes that unrepresentable.

    This is TOTAL over the edit lane: the prerequisites gate on
    membership in `DELIVERY_EDIT_STATES`, so a state added to that tuple
    later inherits them and fails closed by omission rather than
    slipping through.

    `delivered_by_edit` additionally names the object it edited: its
    `edited_message_id` MUST equal the bound placeholder's message id
    (a receipt naming a never-edited object, e.g. 999, would suppress
    the real delivery while /status reports success). The delivery CHAT
    is provably the bound chat by construction — the edit targets
    `result_placeholder.chat_id`, pinned equal to `telegram.chat_id` —
    so there is no separate delivery-chat field to reconcile.

    `rendered_digest` is deliberately NOT checked here. It is the digest
    of the RESULT MESSAGE text, and that renderer lives in the Telegram
    adapter — a HIGHER layer this store-only module must not import.
    That relation is enforced where the receipt is read as proof
    (`telegram_operator/adapter.py::_edit_delivery_claimable`), which
    re-derives the current render and reclaims a receipt whose premise
    no longer holds: for `delivered_by_edit`, a rendered_digest that no
    longer matches; for the TERMINAL `degraded_unrenderable`, a render
    that is not actually oversized (a false terminal). That read-as-proof
    layer audits every non-claimable state locally except
    `degraded_unbindable`, whose premise (the Telegram object is gone)
    cannot be re-verified without another Telegram call and where R-3
    forbids any replacement send — it is terminal by design.
    """
    delivery = document["result_delivery"]
    if delivery is None or delivery["state"] not in DELIVERY_EDIT_STATES:
        return
    state = delivery["state"]
    placeholder = document["result_placeholder"] or {}
    if document["phase"] != PHASE_COMPLETED:
        _fail(
            PROBLEM_DELIVERY_BINDING,
            "%s carries an edit-lane result_delivery receipt (%r) but"
            " phase is %r, not COMPLETED; the edit engine only ever"
            " writes such a receipt on a COMPLETED record, so this"
            " state is impossible and would strand forever"
            % (location, state, document["phase"]),
        )
    if document["verified_result"] is None:
        _fail(
            PROBLEM_DELIVERY_BINDING,
            "%s carries an edit-lane result_delivery receipt (%r) but"
            " has NO verified_result; the edit engine only edits a"
            " record that has one, so this receipt is impossible"
            % (location, state),
        )
    if placeholder.get("state") != PLACEHOLDER_BOUND:
        _fail(
            PROBLEM_DELIVERY_BINDING,
            "%s carries an edit-lane result_delivery receipt (%r) but"
            " its result_placeholder state is %r, not 'bound'; the edit"
            " engine claims ONLY a bound placeholder, so an edit receipt"
            " against a %r placeholder is impossible and would strand"
            % (location, state, placeholder.get("state"),
               placeholder.get("state")),
        )
    if state == DELIVERY_DELIVERED_BY_EDIT:
        bound_message_id = placeholder.get("message_id")
        if delivery["edited_message_id"] != bound_message_id:
            _fail(
                PROBLEM_DELIVERY_BINDING,
                "%s.result_delivery.edited_message_id %r must equal the"
                " bound result_placeholder.message_id %r: a"
                " delivered_by_edit receipt must name the object that"
                " was actually edited, or it claims a delivery it cannot"
                " prove and suppresses the real one"
                % (location, delivery["edited_message_id"],
                   bound_message_id),
            )


def validate_record(document, location="workflow record"):
    """Validate one workflow record against the closed schema.

    Raises RecordError (with an actionable message and a distinct
    ``problem`` code) on the first failure. Called on every load AND
    every save; a record that does not validate is never accepted from
    disk and never written to disk.
    """
    _require_dict(document, location)
    version = document.get("schema_version")
    if isinstance(version, bool) or version != WORKFLOW_SCHEMA_VERSION:
        _fail(
            PROBLEM_SCHEMA_VERSION,
            "%s has schema_version %r; this layer understands only %d."
            " A version-1 record lacks authority fields (the exact"
            " human intent, unresolved questions, execution scope)"
            " that must never be fabricated: run 'tgop"
            " migrate-workflows' to retire v1 records into a preserved"
            " backup, or move the store aside (keeping it for"
            " inspection) — never delete it: it carries authorization"
            " records" % (location, version, WORKFLOW_SCHEMA_VERSION),
        )
    _require_closed_keys(document, _TOP_LEVEL_KEYS, location)
    _require_str(
        document["workflow_id"], location + ".workflow_id",
        max_chars=MAX_ID_CHARS,
    )
    if any(
        ch not in WORKFLOW_ID_ALPHABET
        for ch in document["workflow_id"]
    ):
        _fail(
            PROBLEM_BAD_VALUE,
            "%s.workflow_id %r contains characters outside the"
            " closed id alphabet (lowercase letters, digits,"
            " hyphen); ids name the leased workspace directory, so"
            " anything else — path separators and traversals"
            " included — is unrepresentable"
            % (location, document["workflow_id"]),
        )
    _require_str(
        document["human_intent"], location + ".human_intent",
        max_chars=MAX_HUMAN_INTENT_CHARS,
    )
    _validate_control_identity(
        document["control_identity"], location + ".control_identity"
    )
    _validate_target(document["target"], location + ".target")
    _validate_approved_baseline(
        document["approved_baseline"], location + ".approved_baseline"
    )
    _validate_mission_authorization(
        document["mission_authorization"],
        location + ".mission_authorization",
    )
    _validate_telegram(document["telegram"], location + ".telegram")
    _validate_approval(document["approval"], location + ".approval")
    _validate_handoff(document["handoff"], location + ".handoff")
    _require_member(document["phase"], PHASES, location + ".phase")
    _validate_workspace_lease(
        document["workspace_lease"], location + ".workspace_lease"
    )
    _validate_receipts(document["receipts"], location + ".receipts")
    _validate_codex_turns(
        document["codex_turns"], location + ".codex_turns"
    )
    _validate_ambiguity(document["ambiguity"], location + ".ambiguity")
    _validate_target_engine(
        document["target_engine"], location + ".target_engine"
    )
    _validate_verified_result(
        document["verified_result"], location + ".verified_result"
    )
    _validate_result_placeholder(
        document["result_placeholder"],
        location + ".result_placeholder",
        document["telegram"]["chat_id"],
    )
    _validate_result_delivery(
        document["result_delivery"], location + ".result_delivery"
    )
    # G1: the receipt must be RELATIONALLY true of its record, not just
    # well-shaped. Runs after placeholder and delivery are individually
    # validated, so both cross-referenced fields are known-good here.
    _validate_delivery_relations(document, location)
    _validate_last_observation(
        document["last_observation"], location + ".last_observation"
    )
    authority = document["delivery_authority"]
    if not isinstance(authority, str) or authority != (
        DELIVERY_AUTHORITY_NONE
    ):
        _fail(
            PROBLEM_DELIVERY_AUTHORITY,
            "%s.delivery_authority must be exactly the string %r; got"
            " %r. No workflow record may carry delivery authority"
            % (location, DELIVERY_AUTHORITY_NONE, authority),
        )
    # TOTAL render binding: re-render the Mission Authorization from
    # the record's own fields (every shape above already validated,
    # so the render cannot fail) and require byte-equality with the
    # stored, digest-bound rendered_text. Every rendered field —
    # workflow id, revision, control, policy digest, both target
    # forms, baseline, approval identity, the exact human intent, all
    # seven authority-content sections, and the handoff — is thereby
    # bound: altering any stored field independently of the digested
    # text can never validate.
    if rendering.render_record_text(document) != (
        document["mission_authorization"]["rendered_text"]
    ):
        _fail(
            PROBLEM_RENDER_BINDING,
            "%s.mission_authorization.rendered_text does not equal"
            " the deterministic rendering of the record's own fields;"
            " a field was altered independently of the digested text"
            " the human approved, so the record is refused" % location,
        )


def validate_transition(current_phase, new_phase):
    """Check one phase transition against the explicit allowed table.

    Returns ``(True, None)`` when the transition is in the table and
    ``(False, problem)`` otherwise — unknown phases and transitions
    outside the table each fail closed with their own problem code.
    """
    if current_phase not in ALLOWED_TRANSITIONS:
        return False, PROBLEM_UNKNOWN_PHASE
    if new_phase not in ALLOWED_TRANSITIONS:
        return False, PROBLEM_UNKNOWN_PHASE
    if new_phase not in ALLOWED_TRANSITIONS[current_phase]:
        return False, PROBLEM_INVALID_TRANSITION
    return True, None


def apply_transition(record, new_phase):
    """Apply a validated phase transition to ``record``, or fail closed.

    Raises RecordError when the transition is not in the allowed
    table; the record is left unchanged in that case.
    """
    ok, problem = validate_transition(record.get("phase"), new_phase)
    if not ok:
        raise RecordError(
            "phase transition %r -> %r is not in the allowed"
            " transition table" % (record.get("phase"), new_phase),
            problem,
        )
    record["phase"] = new_phase


def new_record(workflow_id, human_intent, repository_realpath,
               policy_digest_sha256, canonical_host, owner, repo,
               canonical_url, issue_or_pr_kind, issue_or_pr_number,
               baseline_ref, baseline_commit_sha, objective,
               constraints, rules, desired_outcome, acceptance,
               unresolved_questions, execution_scope,
               mission_revision, telegram_user_id, telegram_chat_id,
               approval_nonce, approval_created_at,
               approval_expires_at, handoff_revision, handoff_text):
    """Build a fresh PLANNED workflow record with exact bindings.

    The rendered Mission Authorization text is computed HERE, by the
    single renderer, from exactly the stored fields — so the render
    binding holds by construction. ``issue_or_pr_kind`` and
    ``issue_or_pr_number`` are both None for a repository-only
    target; supplying exactly one of them builds a structure the
    validation below refuses (nothing is fabricated or dropped).
    Digests are computed here from the exact stored texts, so the
    record validates by construction; the result is validated before
    it is returned, as a self-check.
    """
    if issue_or_pr_kind is None and issue_or_pr_number is None:
        issue_or_pr = None
    else:
        issue_or_pr = {
            "kind": issue_or_pr_kind,
            "number": issue_or_pr_number,
        }
    authority_content = {
        "objective": objective,
        "constraints": constraints,
        "rules": rules,
        "desired_outcome": desired_outcome,
        "acceptance": acceptance,
        "unresolved_questions": unresolved_questions,
        "execution_scope": execution_scope,
    }
    # Pre-check every value the renderer consumes, so a bad-typed
    # input fails closed with a RecordError here instead of crashing
    # inside the renderer before the self-validation below runs.
    _require_str(human_intent, "human_intent",
                 max_chars=MAX_HUMAN_INTENT_CHARS)
    for key in sorted(authority_content):
        _require_str(authority_content[key], key,
                     max_chars=MAX_AUTHORITY_FIELD_CHARS)
    _require_str(handoff_text, "handoff_text",
                 max_chars=MAX_AUTHORITY_TEXT_CHARS)
    _require_int(mission_revision, "mission_revision", minimum=1)
    _require_int(handoff_revision, "handoff_revision", minimum=1)
    _require_int(telegram_user_id, "telegram_user_id", minimum=1)
    _require_int(telegram_chat_id, "telegram_chat_id", minimum=1)
    if issue_or_pr is not None:
        _require_member(issue_or_pr["kind"], ISSUE_OR_PR_KINDS,
                        "issue_or_pr_kind")
        _require_int(issue_or_pr["number"], "issue_or_pr_number",
                     minimum=1)
    rendered_text = rendering.render_authorization_text(
        workflow_id=workflow_id,
        revision=mission_revision,
        control_realpath=repository_realpath,
        policy_digest=policy_digest_sha256,
        canonical_url=canonical_url,
        issue_or_pr=issue_or_pr,
        baseline_ref=baseline_ref,
        baseline_sha=baseline_commit_sha,
        user_id=telegram_user_id,
        chat_id=telegram_chat_id,
        human_intent=human_intent,
        authority_content=authority_content,
        handoff_revision=handoff_revision,
        handoff_text=handoff_text,
    )
    mission_authorization = {
        "rendered_text": rendered_text,
        "digest_sha256": text_digest(rendered_text),
        "revision": mission_revision,
    }
    mission_authorization.update(authority_content)
    document = {
        "schema_version": WORKFLOW_SCHEMA_VERSION,
        "workflow_id": workflow_id,
        "human_intent": human_intent,
        "control_identity": {
            "repository_realpath": repository_realpath,
            "policy_digest_sha256": policy_digest_sha256,
        },
        "target": {
            "canonical_host": canonical_host,
            "owner": owner,
            "repo": repo,
            "canonical_url": canonical_url,
            "issue_or_pr": issue_or_pr,
        },
        "approved_baseline": {
            "ref": baseline_ref,
            "commit_sha": baseline_commit_sha,
        },
        "mission_authorization": mission_authorization,
        "telegram": {
            "user_id": telegram_user_id,
            "chat_id": telegram_chat_id,
            "message_ids": [],
            "plan_message_id": None,
        },
        "approval": {
            "approval_kind": APPROVAL_KIND_MISSION_V2,
            "nonce": approval_nonce,
            "created_at": approval_created_at,
            "expires_at": approval_expires_at,
            "consumed_at": None,
            "consumed_by_update_id": None,
            "decision": None,
            "superseded": False,
        },
        "handoff": {
            "revision": handoff_revision,
            "text": handoff_text,
            "digest_sha256": text_digest(handoff_text),
        },
        "phase": PHASE_PLANNED,
        "workspace_lease": None,
        "receipts": [],
        "codex_turns": [],
        "ambiguity": {"state": AMBIGUITY_NONE, "detail": None},
        "target_engine": None,
        "verified_result": None,
        # A brand-new record starts on the LEGACY/ungated lane (plan
        # §1.1): nothing has requested a placeholder yet. The adapter's
        # approval path writes the ``required`` request in I3, in the
        # same locked transaction that arms the mission.
        "result_placeholder": None,
        # result_delivery starts null, so its six additive fields
        # (RESULT_DELIVERY_ADDITIVE_KEYS) are vacuously null too.
        "result_delivery": None,
        "last_observation": None,
        "delivery_authority": DELIVERY_AUTHORITY_NONE,
    }
    validate_record(document)
    return document
