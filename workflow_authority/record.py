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
DELIVERY_STATES = (
    DELIVERY_RESERVED, DELIVERY_DELIVERED, DELIVERY_PARTIAL,
)


def _validate_result_delivery(value, location):
    if value is None:
        return
    _require_dict(value, location)
    _require_closed_keys(
        value, ("state", "reserved_at", "telegram_message_id"),
        location,
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
    _validate_result_delivery(
        document["result_delivery"], location + ".result_delivery"
    )
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
        "result_delivery": None,
        "last_observation": None,
        "delivery_authority": DELIVERY_AUTHORITY_NONE,
    }
    validate_record(document)
    return document
