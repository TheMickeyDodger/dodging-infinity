"""The PR Delivery Authorization: a distinct, durable human authority record.

Two halves. The AUTHORITY half is immutable after the human ceremony and is
bound by ``authority_digest_sha256`` (canonical JSON, re-verified on every
load and save: a tampered record fails closed rather than delivering). The
STATE half is mutable durable per-step state: phase, receipts, revocation,
blocker, the base OID the candidate currently sits on, and the pull request
once one exists.

The key set is closed at every level; every key is required; hard bounds
are module constants never derived from input; validation refuses, it never
truncates or repairs. Distinct problem codes per failure (``pr_delivery_*``).

This record is NOT a Mission Authorization and can never substitute for
one: it carries no objective/constraints/rules content of its own (only an
optional REFERENCE to a Mission Authorization digest), and the Mission
Authorization's structural ``delivery_authority: "none"`` is untouched. It
is also not the 600-second manual token: it binds an exact reviewed
candidate, exact evidence, an allowed-action set, an absolute deadline, a
revision, and revocation state, and it never authorizes anything by itself
— only a receipt derived from it (``receipts.py``) reaches a git hook.

Receipt SHAPES live here too because a receipt is part of this record;
receipt derivation and live validation live in ``receipts.py``.
"""

import os

from workflow_authority import canonical
from workflow_authority.digest import json_digest
from workflow_authority.record import (
    MAX_ID_CHARS,
    WORKFLOW_ID_ALPHABET,
    baseline_ref_grammar_problem,
    path_character_problem,
)

SCHEMA_VERSION = 1

MODE_PULL_REQUEST = "pull_request"

STEP_BASE_REFRESH = "BASE_REFRESH"
STEP_COMMIT = "COMMIT"
STEP_PUSH = "PUSH"
STEP_PR_CREATE = "PR_CREATE"
STEPS = (STEP_BASE_REFRESH, STEP_COMMIT, STEP_PUSH, STEP_PR_CREATE)

PHASE_AUTHORIZED = "AUTHORIZED"
PHASE_BASE_CURRENT = "BASE_CURRENT"
PHASE_COMMITTED = "COMMITTED"
PHASE_PUSHED = "PUSHED"
PHASE_PR_OPENED = "PR_OPENED"
PHASE_COMPLETE = "COMPLETE"
PHASE_BLOCKED = "BLOCKED"
PHASE_REVOKED = "REVOKED"
PHASES = (
    PHASE_AUTHORIZED, PHASE_BASE_CURRENT, PHASE_COMMITTED, PHASE_PUSHED,
    PHASE_PR_OPENED, PHASE_COMPLETE, PHASE_BLOCKED, PHASE_REVOKED,
)
TERMINAL_PHASES = (PHASE_COMPLETE, PHASE_BLOCKED, PHASE_REVOKED)

# Explicit allowed-transition table: every phase change goes through
# ``apply_transition``; anything not listed is refused.
ALLOWED_TRANSITIONS = {
    PHASE_AUTHORIZED: frozenset(
        (PHASE_BASE_CURRENT, PHASE_BLOCKED, PHASE_REVOKED)
    ),
    PHASE_BASE_CURRENT: frozenset(
        (PHASE_COMMITTED, PHASE_BLOCKED, PHASE_REVOKED)
    ),
    PHASE_COMMITTED: frozenset((PHASE_PUSHED, PHASE_BLOCKED, PHASE_REVOKED)),
    PHASE_PUSHED: frozenset((PHASE_PR_OPENED, PHASE_BLOCKED, PHASE_REVOKED)),
    PHASE_PR_OPENED: frozenset((PHASE_COMPLETE, PHASE_BLOCKED, PHASE_REVOKED)),
    PHASE_COMPLETE: frozenset(),
    PHASE_BLOCKED: frozenset(),
    PHASE_REVOKED: frozenset(),
}

# Which step each non-terminal phase permits next.
STEP_FOR_PHASE = {
    PHASE_AUTHORIZED: STEP_BASE_REFRESH,
    PHASE_BASE_CURRENT: STEP_COMMIT,
    PHASE_COMMITTED: STEP_PUSH,
    PHASE_PUSHED: STEP_PR_CREATE,
}

STEP_PENDING = "pending"
STEP_NOT_NEEDED = "not_needed"
STEP_EXECUTING = "executing"
STEP_SUCCEEDED = "succeeded"
STEP_FAILED_RETRYABLE = "failed_retryable"
STEP_BLOCKED = "blocked"
STEP_STATES = (
    STEP_PENDING, STEP_NOT_NEEDED, STEP_EXECUTING, STEP_SUCCEEDED,
    STEP_FAILED_RETRYABLE, STEP_BLOCKED,
)

RECEIPT_DERIVED = "derived"
RECEIPT_EXECUTING = "executing"
RECEIPT_SUCCEEDED = "succeeded"
RECEIPT_VOID = "void"
RECEIPT_FAILED_RETRYABLE = "failed_retryable"
RECEIPT_STATES = (
    RECEIPT_DERIVED, RECEIPT_EXECUTING, RECEIPT_SUCCEEDED, RECEIPT_VOID,
    RECEIPT_FAILED_RETRYABLE,
)

AUTHORIZATION_SOURCE_LOCAL_TERMINAL = "local_terminal"
AUTHORIZATION_SOURCES = (AUTHORIZATION_SOURCE_LOCAL_TERMINAL,)

EXPIRATION_POLICY_ABSOLUTE = "absolute_deadline"
EXPIRATION_POLICIES = (EXPIRATION_POLICY_ABSOLUTE,)

CANDIDATE_STATUS_ADDED = "A"
CANDIDATE_STATUS_MODIFIED = "M"
CANDIDATE_STATUS_DELETED = "D"
CANDIDATE_STATUSES = (
    CANDIDATE_STATUS_ADDED, CANDIDATE_STATUS_MODIFIED,
    CANDIDATE_STATUS_DELETED,
)
# Regular file, executable, symlink. A gitlink (160000) is refused.
CANDIDATE_MODES = ("100644", "100755", "120000")

EVIDENCE_ENGINEERING_STATUS_COMPLETE = "COMPLETE"
EVIDENCE_REVIEW_DECISION_APPROVE = "APPROVE"

# Hard bounds, never derived from input.
MAX_CANDIDATE_ENTRIES = 4096
MAX_PR_TITLE_CHARS = 256
MAX_PR_BODY_CHARS = 16384
MAX_AUTHORIZATION_VALIDITY_SECONDS = 604800
DEFAULT_AUTHORIZATION_VALIDITY_SECONDS = 86400
MAX_HUMAN_TEXT_CHARS = 8000
MAX_EVIDENCE_TEXT_CHARS = 4000
MAX_STEP_ATTEMPTS = 8
MAX_REMOTE_URL_CHARS = 512 + 4
MAX_REVERIFICATION_ARGV = 64

# Receipt binding fields per step: CLOSED tuples, pinned by exact value in
# tests/test_static.py. A receipt carries every field of its step and the
# live validator in receipts.py compares every one of them.
BASE_REFRESH_RECEIPT_BINDING_FIELDS = (
    "repository_realpath", "git_dir_realpath", "remote_name",
    "remote_url_exact", "remote_url_fetch", "source_ref", "base_ref",
    "old_base_oid", "new_base_oid", "fast_forward",
    "base_changed_paths_digest", "candidate_identity_digest",
)
COMMIT_RECEIPT_BINDING_FIELDS = (
    "repository_realpath", "git_dir_realpath", "branch", "source_ref",
    "head_before", "staged_sha256", "candidate_identity_digest",
    "expected_tree_oid", "committer_name", "committer_email",
    "message_sha256",
)
PUSH_RECEIPT_BINDING_FIELDS = (
    "repository_realpath", "remote_name", "remote_url_exact",
    "remote_url_push", "source_ref", "source_commit", "destination_ref",
    "expected_remote_old_oid", "candidate_identity_digest",
)
PR_CREATE_RECEIPT_BINDING_FIELDS = (
    "owner", "repo", "remote_url_exact", "head_branch", "head_sha",
    "base_branch", "title_sha256", "body_sha256",
    "candidate_identity_digest",
)
RECEIPT_BINDING_FIELDS = {
    STEP_BASE_REFRESH: BASE_REFRESH_RECEIPT_BINDING_FIELDS,
    STEP_COMMIT: COMMIT_RECEIPT_BINDING_FIELDS,
    STEP_PUSH: PUSH_RECEIPT_BINDING_FIELDS,
    STEP_PR_CREATE: PR_CREATE_RECEIPT_BINDING_FIELDS,
}
RECEIPT_KEYS = (
    "receipt_id", "step", "delivery_id", "parent_authority_digest_sha256",
    "derived_at", "attempt", "state", "binding", "observed",
    "receipt_digest_sha256",
)
# The receipt digest covers everything except the two mutable keys.
RECEIPT_DIGEST_EXCLUDED_KEYS = ("state", "observed", "receipt_digest_sha256")

ZERO_OID = "0" * 40

PROBLEM_NOT_AN_OBJECT = "pr_delivery_not_an_object"
PROBLEM_SCHEMA_VERSION = "pr_delivery_schema_version"
PROBLEM_UNKNOWN_KEY = "pr_delivery_unknown_key"
PROBLEM_MISSING_KEY = "pr_delivery_missing_key"
PROBLEM_BAD_TYPE = "pr_delivery_bad_type"
PROBLEM_BAD_VALUE = "pr_delivery_bad_value"
PROBLEM_TOO_LARGE = "pr_delivery_too_large"
PROBLEM_AUTHORITY_DIGEST = "pr_delivery_authority_digest_mismatch"
PROBLEM_RECEIPT_DIGEST = "pr_delivery_receipt_digest_mismatch"
PROBLEM_MODE = "pr_delivery_mode"
PROBLEM_ALLOWED_ACTIONS = "pr_delivery_allowed_actions"
PROBLEM_REMOTE_GRAMMAR = "pr_delivery_remote_grammar"
PROBLEM_REMOTE_IDENTITY = "pr_delivery_remote_identity"
PROBLEM_REPOSITORY_IDENTITY = "pr_delivery_repository_identity"
PROBLEM_REF_GRAMMAR = "pr_delivery_ref_grammar"
PROBLEM_EXPIRATION_POLICY = "pr_delivery_expiration_policy"
PROBLEM_CANDIDATE_ENTRY = "pr_delivery_candidate_entry"
PROBLEM_CANDIDATE_IDENTITY = "pr_delivery_candidate_identity"
PROBLEM_EVIDENCE = "pr_delivery_evidence"
PROBLEM_UNKNOWN_PHASE = "pr_delivery_unknown_phase"
PROBLEM_INVALID_TRANSITION = "pr_delivery_invalid_transition"
PROBLEM_STEP_STATE = "pr_delivery_step_state"


class AuthorizationError(Exception):
    """A PR Delivery Authorization failed validation; message actionable."""

    def __init__(self, message, problem):
        super(AuthorizationError, self).__init__(message)
        self.problem = problem


def _fail(problem, message):
    raise AuthorizationError(message, problem)


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
            "%s has unknown keys: %s (the key set is closed; an unexpected"
            " key could carry unauthorized meaning)"
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
            "%s is %d characters; the hard bound is %d characters and the"
            " record is refused, not truncated"
            % (location, len(value), max_chars),
        )


def _require_line_free_str(value, location, max_chars, allow_empty=False):
    _require_str(value, location, max_chars, allow_empty=allow_empty)
    reason = path_character_problem(value)
    if reason is not None:
        _fail(PROBLEM_BAD_VALUE, "%s %s" % (location, reason))


def _require_int(value, location, minimum=None, maximum=None):
    if isinstance(value, bool) or not isinstance(value, int):
        _fail(
            PROBLEM_BAD_TYPE,
            "%s must be an integer (bool is not accepted), not %r"
            % (location, value),
        )
    if minimum is not None and value < minimum:
        _fail(PROBLEM_BAD_VALUE,
              "%s must be >= %d; got %d" % (location, minimum, value))
    if maximum is not None and value > maximum:
        _fail(PROBLEM_TOO_LARGE,
              "%s must be <= %d; got %d" % (location, maximum, value))


def _require_timestamp(value, location):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        _fail(
            PROBLEM_BAD_TYPE,
            "%s must be a number (bool is not accepted), not %r"
            % (location, value),
        )
    if value < 0:
        _fail(PROBLEM_BAD_VALUE,
              "%s must be non-negative; got %r" % (location, value))


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
            "%s must be one of %s; got %r (unknown values fail closed)"
            % (location, ", ".join(allowed), value),
        )


def _require_id(value, location):
    _require_str(value, location, max_chars=MAX_ID_CHARS)
    if any(ch not in WORKFLOW_ID_ALPHABET for ch in value):
        _fail(
            PROBLEM_BAD_VALUE,
            "%s must use only the closed identifier alphabet" % location,
        )


def _require_realpath(value, location):
    _require_line_free_str(value, location, max_chars=4096)
    if not os.path.isabs(value) or os.path.realpath(value) != value:
        _fail(
            PROBLEM_BAD_VALUE,
            "%s must be an absolute, already-resolved real path; got %r"
            % (location, value),
        )


def _require_ref(value, location):
    _require_str(value, location, max_chars=512)
    reason = baseline_ref_grammar_problem(value)
    if reason is not None:
        _fail(PROBLEM_REF_GRAMMAR, "%s %s" % (location, reason))
    if not value.startswith("refs/heads/"):
        _fail(
            PROBLEM_REF_GRAMMAR,
            "%s must be a branch ref under refs/heads/; got %r"
            % (location, value),
        )


def _require_branch_pair(value, location):
    _require_dict(value, location)
    _require_closed_keys(value, ("branch", "ref"), location)
    _require_ref(value["ref"], location + ".ref")
    _require_line_free_str(value["branch"], location + ".branch",
                           max_chars=500)
    if value["ref"] != "refs/heads/" + value["branch"]:
        _fail(
            PROBLEM_REF_GRAMMAR,
            "%s.ref %r does not name branch %r"
            % (location, value["ref"], value["branch"]),
        )


# --- remote grammar -----------------------------------------------------

def parse_exact_remote_url(url):
    """The configured remote URL, accepted in exactly two spellings.

    ``https://github.com/<owner>/<repo>`` or the same with a ``.git``
    suffix. Everything else fails closed (SSH forms included — a second
    canonicalizer is exactly what the ONE-source rule forbids). Returns
    the ``CanonicalTarget`` of the suffix-free form.
    """
    if not isinstance(url, str) or not url:
        _fail(PROBLEM_REMOTE_GRAMMAR,
              "remote URL must be a non-empty string, got %r" % (url,))
    if len(url) > MAX_REMOTE_URL_CHARS:
        _fail(
            PROBLEM_REMOTE_GRAMMAR,
            "remote URL is %d characters; the hard bound is %d"
            % (len(url), MAX_REMOTE_URL_CHARS),
        )
    stripped = url[:-4] if url.endswith(".git") else url
    try:
        return canonical.canonicalize_repository_url(stripped)
    except canonical.CanonicalizationError as exc:
        _fail(
            PROBLEM_REMOTE_GRAMMAR,
            "remote URL %r is not the canonical GitHub repository form"
            " (with optional .git suffix): %s (%s). SSH and every other"
            " form fail closed" % (url, exc, exc.problem),
        )


# --- sub-validators -----------------------------------------------------

def _validate_repository(value, location):
    _require_dict(value, location)
    _require_closed_keys(
        value,
        ("realpath", "git_dir_realpath", "canonical_host", "owner", "repo",
         "repository_url"),
        location,
    )
    _require_realpath(value["realpath"], location + ".realpath")
    _require_realpath(value["git_dir_realpath"],
                      location + ".git_dir_realpath")
    _require_str(value["repository_url"], location + ".repository_url",
                 max_chars=canonical.MAX_TARGET_URL_CHARS)
    try:
        target = canonical.canonicalize_repository_url(
            value["repository_url"]
        )
    except canonical.CanonicalizationError as exc:
        _fail(
            PROBLEM_REPOSITORY_IDENTITY,
            "%s.repository_url failed canonicalization (%s): %s"
            % (location, exc.problem, exc),
        )
    if (
        value["canonical_host"] != target.host
        or value["owner"] != target.owner
        or value["repo"] != target.repo
    ):
        _fail(
            PROBLEM_REPOSITORY_IDENTITY,
            "%s host/owner/repo do not match the canonical parse of"
            " repository_url" % location,
        )
    return target


def _validate_remote(value, location, repository_target):
    """``url_exact`` is the configured value under the canonical grammar;
    ``url_fetch`` and ``url_push`` are the EXPANDED values git resolves
    for the remote name at authorization time (``remote get-url`` and
    ``remote get-url --push``: after every ``url.<base>.insteadOf``,
    ``pushInsteadOf`` and ``remote.<name>.pushurl``). The hook is handed
    the expanded push URL, so the push receipt binds it and a rewrite
    added after authorization no longer matches (round-01 B2)."""
    _require_dict(value, location)
    _require_closed_keys(
        value, ("name", "url_exact", "url_fetch", "url_push",
                "repository_url"),
        location,
    )
    _require_line_free_str(value["url_fetch"], location + ".url_fetch",
                           max_chars=4096)
    _require_line_free_str(value["url_push"], location + ".url_push",
                           max_chars=4096)
    _require_line_free_str(value["name"], location + ".name", max_chars=255)
    if any(ch.isspace() for ch in value["name"]) or value["name"].startswith(
        "-"
    ):
        _fail(PROBLEM_BAD_VALUE,
              "%s.name must be a plain remote name" % location)
    target = parse_exact_remote_url(value["url_exact"])
    if value["repository_url"] != target.repository_url:
        _fail(
            PROBLEM_REMOTE_IDENTITY,
            "%s.repository_url %r is not the canonical form of url_exact"
            " %r" % (location, value["repository_url"], value["url_exact"]),
        )
    if not canonical.same_repository_identity(target, repository_target):
        _fail(
            PROBLEM_REMOTE_IDENTITY,
            "%s names repository %s but the record's repository is %s"
            % (location, canonical.repository_identity_key(target),
               canonical.repository_identity_key(repository_target)),
        )


def validate_candidate_entries(entries, location):
    """Structural validation of the ordered candidate entry list."""
    if not isinstance(entries, list):
        _fail(PROBLEM_BAD_TYPE, "%s must be a list" % location)
    if not entries:
        _fail(PROBLEM_CANDIDATE_ENTRY,
              "%s is empty; an empty candidate is not deliverable" % location)
    if len(entries) > MAX_CANDIDATE_ENTRIES:
        _fail(
            PROBLEM_TOO_LARGE,
            "%s has %d entries; the hard bound is %d"
            % (location, len(entries), MAX_CANDIDATE_ENTRIES),
        )
    previous = None
    for index, entry in enumerate(entries):
        where = "%s[%d]" % (location, index)
        _require_dict(entry, where)
        _require_closed_keys(entry, ("path", "status", "mode", "blob"),
                             where)
        _require_line_free_str(entry["path"], where + ".path",
                               max_chars=4096)
        _require_member(entry["status"], CANDIDATE_STATUSES,
                        where + ".status")
        _require_member(entry["mode"], CANDIDATE_MODES, where + ".mode")
        _require_hex(entry["blob"], where + ".blob", 40)
        encoded = entry["path"].encode("utf-8")
        if previous is not None and encoded <= previous:
            _fail(
                PROBLEM_CANDIDATE_ENTRY,
                "%s.path is not in strict UTF-8 byte order after the"
                " previous entry (duplicate or misordered)" % where,
            )
        previous = encoded


def _validate_candidate(value, location):
    _require_dict(value, location)
    _require_closed_keys(
        value, ("identity_digest_sha256", "entry_count", "entries"),
        location,
    )
    _require_hex(value["identity_digest_sha256"],
                 location + ".identity_digest_sha256", 64)
    validate_candidate_entries(value["entries"], location + ".entries")
    _require_int(value["entry_count"], location + ".entry_count",
                 minimum=1)
    if value["entry_count"] != len(value["entries"]):
        _fail(
            PROBLEM_CANDIDATE_IDENTITY,
            "%s.entry_count %d does not match %d entries"
            % (location, value["entry_count"], len(value["entries"])),
        )
    # Identity is recomputed here on every validation so a record can
    # never carry a digest that its own entries do not produce.
    from pr_delivery import candidate as candidate_module
    computed = candidate_module.identity_digest(value["entries"])
    if computed != value["identity_digest_sha256"]:
        _fail(
            PROBLEM_CANDIDATE_IDENTITY,
            "%s.identity_digest_sha256 does not match the digest of the"
            " stored entries" % location,
        )


def _validate_evidence_common(value, location):
    _require_hex(value["candidate_identity_digest_sha256"],
                 location + ".candidate_identity_digest_sha256", 64)
    _require_hex(value["base_oid"], location + ".base_oid", 40)
    _require_timestamp(value["recorded_at"], location + ".recorded_at")


def _validate_evidence(value, location, candidate_digest):
    _require_dict(value, location)
    _require_closed_keys(
        value,
        ("engineering_complete", "reviewer_approve",
         "independent_verification"),
        location,
    )
    eng = value["engineering_complete"]
    where = location + ".engineering_complete"
    _require_dict(eng, where)
    _require_closed_keys(
        eng,
        ("task_id", "status", "task_state_sha256", "recorded_at",
         "candidate_identity_digest_sha256", "base_oid"),
        where,
    )
    _require_line_free_str(eng["task_id"], where + ".task_id",
                           max_chars=MAX_ID_CHARS)
    _require_member(eng["status"], (EVIDENCE_ENGINEERING_STATUS_COMPLETE,),
                    where + ".status")
    _require_hex(eng["task_state_sha256"], where + ".task_state_sha256", 64)
    _validate_evidence_common(eng, where)

    rev = value["reviewer_approve"]
    where = location + ".reviewer_approve"
    _require_dict(rev, where)
    _require_closed_keys(
        rev,
        ("task_id", "round", "review_file_name", "review_file_sha256",
         "decision", "recorded_at", "candidate_identity_digest_sha256",
         "base_oid"),
        where,
    )
    _require_line_free_str(rev["task_id"], where + ".task_id",
                           max_chars=MAX_ID_CHARS)
    _require_int(rev["round"], where + ".round", minimum=1)
    _require_line_free_str(rev["review_file_name"],
                           where + ".review_file_name", max_chars=255)
    _require_hex(rev["review_file_sha256"], where + ".review_file_sha256",
                 64)
    _require_member(rev["decision"], (EVIDENCE_REVIEW_DECISION_APPROVE,),
                    where + ".decision")
    _validate_evidence_common(rev, where)
    if rev["task_id"] != eng["task_id"]:
        _fail(
            PROBLEM_EVIDENCE,
            "%s.task_id %r is not the engineering task %r"
            % (where, rev["task_id"], eng["task_id"]),
        )

    ver = value["independent_verification"]
    where = location + ".independent_verification"
    _require_dict(ver, where)
    _require_closed_keys(
        ver,
        ("command_argv", "exit_status", "log_sha256", "log_bytes",
         "ran_at", "recorded_at", "candidate_identity_digest_sha256",
         "base_oid"),
        where,
    )
    _validate_argv(ver["command_argv"], where + ".command_argv")
    _require_int(ver["exit_status"], where + ".exit_status", minimum=0,
                 maximum=0)
    _require_hex(ver["log_sha256"], where + ".log_sha256", 64)
    _require_int(ver["log_bytes"], where + ".log_bytes", minimum=0)
    _require_timestamp(ver["ran_at"], where + ".ran_at")
    _validate_evidence_common(ver, where)

    for name, item in (
        ("engineering_complete", eng), ("reviewer_approve", rev),
        ("independent_verification", ver),
    ):
        if item["candidate_identity_digest_sha256"] != candidate_digest:
            _fail(
                PROBLEM_EVIDENCE,
                "%s.%s is bound to candidate identity %s, not this record's"
                " candidate %s (inapplicable evidence)"
                % (location, name,
                   item["candidate_identity_digest_sha256"],
                   candidate_digest),
            )


def _validate_argv(value, location):
    if not isinstance(value, list) or not value:
        _fail(PROBLEM_BAD_TYPE,
              "%s must be a non-empty list of strings" % location)
    if len(value) > MAX_REVERIFICATION_ARGV:
        _fail(PROBLEM_TOO_LARGE,
              "%s has more than %d elements"
              % (location, MAX_REVERIFICATION_ARGV))
    for index, item in enumerate(value):
        _require_str(item, "%s[%d]" % (location, index), max_chars=4096)
    head = os.path.basename(value[0])
    if head in ("sh", "bash", "zsh", "dash", "ksh", "fish", "cmd", "pwsh",
                "powershell"):
        # A shell as argv[0] would make argv[1:] a script, which is a
        # shell string by another name. Refused (Lead M4).
        _fail(
            PROBLEM_BAD_VALUE,
            "%s[0] %r is a shell; the argv must name the program"
            " directly" % (location, value[0]),
        )


def _validate_committer(value, location):
    _require_dict(value, location)
    _require_closed_keys(value, ("name", "email"), location)
    _require_line_free_str(value["name"], location + ".name", max_chars=255)
    _require_line_free_str(value["email"], location + ".email",
                           max_chars=255)
    for key in ("name", "email"):
        if "<" in value[key] or ">" in value[key]:
            _fail(PROBLEM_BAD_VALUE,
                  "%s.%s may not contain angle brackets" % (location, key))


def _validate_pr_content(value, location):
    _require_dict(value, location)
    _require_closed_keys(
        value, ("title", "objective", "architecture_notes",
                "nonblocking_risks"),
        location,
    )
    _require_line_free_str(value["title"], location + ".title",
                           max_chars=MAX_PR_TITLE_CHARS)
    for key in ("objective", "architecture_notes", "nonblocking_risks"):
        _require_str(value[key], location + "." + key,
                     max_chars=MAX_HUMAN_TEXT_CHARS, allow_empty=True)


def _validate_human_authorization(value, location):
    _require_dict(value, location)
    _require_closed_keys(
        value, ("identity", "source", "authorized_at",
                "confirmation_digest_sha256"),
        location,
    )
    _require_line_free_str(value["identity"], location + ".identity",
                           max_chars=255)
    _require_member(value["source"], AUTHORIZATION_SOURCES,
                    location + ".source")
    _require_timestamp(value["authorized_at"], location + ".authorized_at")
    _require_hex(value["confirmation_digest_sha256"],
                 location + ".confirmation_digest_sha256", 64)


def _validate_expiration(value, location, authorized_at):
    _require_dict(value, location)
    _require_closed_keys(value, ("policy", "expires_at"), location)
    _require_member(value["policy"], EXPIRATION_POLICIES,
                    location + ".policy")
    _require_timestamp(value["expires_at"], location + ".expires_at")
    if value["expires_at"] <= authorized_at:
        _fail(PROBLEM_EXPIRATION_POLICY,
              "%s.expires_at is not after the authorization time" % location)
    if value["expires_at"] - authorized_at > (
        MAX_AUTHORIZATION_VALIDITY_SECONDS
    ):
        _fail(
            PROBLEM_EXPIRATION_POLICY,
            "%s spans more than %d seconds"
            % (location, MAX_AUTHORIZATION_VALIDITY_SECONDS),
        )


def _validate_allowed_actions(value, location):
    if not isinstance(value, list) or not value:
        _fail(PROBLEM_ALLOWED_ACTIONS,
              "%s must be a non-empty list" % location)
    seen = set()
    for index, item in enumerate(value):
        if not isinstance(item, str) or item not in STEPS:
            _fail(
                PROBLEM_ALLOWED_ACTIONS,
                "%s[%d] %r is not one of %s; the action set is closed and"
                " can never be widened"
                % (location, index, item, ", ".join(STEPS)),
            )
        if item in seen:
            _fail(PROBLEM_ALLOWED_ACTIONS,
                  "%s repeats %r" % (location, item))
        seen.add(item)
    if list(value) != [step for step in STEPS if step in seen]:
        _fail(PROBLEM_ALLOWED_ACTIONS,
              "%s must list its actions in step order" % location)


def receipt_digest(receipt):
    """Digest of a receipt over everything but its mutable keys."""
    return json_digest({
        key: receipt[key] for key in RECEIPT_KEYS
        if key not in RECEIPT_DIGEST_EXCLUDED_KEYS
    })


def validate_receipt(receipt, step, delivery_id, authority_digest,
                     location):
    """Structural + digest validation of one stored receipt."""
    _require_dict(receipt, location)
    _require_closed_keys(receipt, RECEIPT_KEYS, location)
    _require_id(receipt["receipt_id"], location + ".receipt_id")
    _require_member(receipt["step"], STEPS, location + ".step")
    if receipt["step"] != step:
        _fail(PROBLEM_BAD_VALUE,
              "%s.step %r stored under step %r"
              % (location, receipt["step"], step))
    if receipt["delivery_id"] != delivery_id:
        _fail(PROBLEM_BAD_VALUE,
              "%s.delivery_id is not this record's" % location)
    _require_hex(receipt["parent_authority_digest_sha256"],
                 location + ".parent_authority_digest_sha256", 64)
    if receipt["parent_authority_digest_sha256"] != authority_digest:
        _fail(
            PROBLEM_RECEIPT_DIGEST,
            "%s is bound to a different parent authority digest" % location,
        )
    _require_timestamp(receipt["derived_at"], location + ".derived_at")
    _require_int(receipt["attempt"], location + ".attempt", minimum=1,
                 maximum=MAX_STEP_ATTEMPTS)
    _require_member(receipt["state"], RECEIPT_STATES, location + ".state")
    binding = receipt["binding"]
    where = location + ".binding"
    _require_dict(binding, where)
    _require_closed_keys(binding, RECEIPT_BINDING_FIELDS[step], where)
    for key, item in binding.items():
        if isinstance(item, bool):
            continue
        _require_line_free_str(item, "%s.%s" % (where, key), max_chars=4096,
                               allow_empty=False)
    if receipt["observed"] is not None:
        _require_dict(receipt["observed"], location + ".observed")
        for key, item in receipt["observed"].items():
            _require_str(key, location + ".observed key", max_chars=64)
            if item is not None and not isinstance(item, (str, int, bool)):
                _fail(PROBLEM_BAD_TYPE,
                      "%s.observed.%s must be scalar" % (location, key))
            if isinstance(item, str):
                _require_str(item, "%s.observed.%s" % (location, key),
                             max_chars=MAX_EVIDENCE_TEXT_CHARS,
                             allow_empty=True)
    _require_hex(receipt["receipt_digest_sha256"],
                 location + ".receipt_digest_sha256", 64)
    if receipt_digest(receipt) != receipt["receipt_digest_sha256"]:
        _fail(PROBLEM_RECEIPT_DIGEST,
              "%s.receipt_digest_sha256 does not match its content"
              % location)


def _validate_steps(value, location, delivery_id, authority_digest):
    _require_dict(value, location)
    _require_closed_keys(value, STEPS, location)
    for step in STEPS:
        entry = value[step]
        where = "%s.%s" % (location, step)
        _require_dict(entry, where)
        _require_closed_keys(entry, ("state", "receipt", "voided"), where)
        _require_member(entry["state"], STEP_STATES, where + ".state")
        if not isinstance(entry["voided"], list):
            _fail(PROBLEM_BAD_TYPE, "%s.voided must be a list" % where)
        if len(entry["voided"]) > MAX_STEP_ATTEMPTS:
            _fail(PROBLEM_TOO_LARGE,
                  "%s.voided exceeds %d" % (where, MAX_STEP_ATTEMPTS))
        for index, item in enumerate(entry["voided"]):
            _require_id(item, "%s.voided[%d]" % (where, index))
        receipt = entry["receipt"]
        if receipt is None:
            if entry["state"] in (STEP_EXECUTING, STEP_SUCCEEDED,
                                  STEP_FAILED_RETRYABLE):
                _fail(PROBLEM_STEP_STATE,
                      "%s.state %r requires a receipt"
                      % (where, entry["state"]))
            continue
        validate_receipt(receipt, step, delivery_id, authority_digest,
                         where + ".receipt")
        expected = {
            STEP_EXECUTING: RECEIPT_EXECUTING,
            STEP_SUCCEEDED: RECEIPT_SUCCEEDED,
            STEP_FAILED_RETRYABLE: RECEIPT_FAILED_RETRYABLE,
        }.get(entry["state"])
        if expected is not None and receipt["state"] != expected:
            _fail(
                PROBLEM_STEP_STATE,
                "%s.state %r disagrees with receipt state %r"
                % (where, entry["state"], receipt["state"]),
            )


def _validate_revocation(value, location):
    _require_dict(value, location)
    _require_closed_keys(value, ("revoked", "revoked_at", "revoked_by",
                                 "reason"), location)
    _require_bool(value["revoked"], location + ".revoked")
    if value["revoked"]:
        _require_timestamp(value["revoked_at"], location + ".revoked_at")
        _require_line_free_str(value["revoked_by"], location + ".revoked_by",
                               max_chars=255)
        _require_str(value["reason"], location + ".reason",
                     max_chars=MAX_EVIDENCE_TEXT_CHARS, allow_empty=True)
    else:
        for key in ("revoked_at", "revoked_by", "reason"):
            if value[key] is not None:
                _fail(PROBLEM_BAD_VALUE,
                      "%s.%s must be null when not revoked"
                      % (location, key))


def _validate_blocker(value, location):
    if value is None:
        return
    _require_dict(value, location)
    _require_closed_keys(value, ("problem", "detail", "recorded_at"),
                         location)
    _require_line_free_str(value["problem"], location + ".problem",
                           max_chars=128)
    _require_str(value["detail"], location + ".detail",
                 max_chars=MAX_EVIDENCE_TEXT_CHARS, allow_empty=True)
    _require_timestamp(value["recorded_at"], location + ".recorded_at")


def _validate_pull_request(value, location):
    if value is None:
        return
    _require_dict(value, location)
    _require_closed_keys(value, ("number", "url", "head_sha", "base_ref"),
                         location)
    _require_int(value["number"], location + ".number", minimum=1)
    _require_str(value["url"], location + ".url",
                 max_chars=canonical.MAX_TARGET_URL_CHARS)
    try:
        target = canonical.canonicalize_target_url(value["url"])
    except canonical.CanonicalizationError as exc:
        _fail(PROBLEM_BAD_VALUE,
              "%s.url failed canonicalization (%s)" % (location, exc.problem))
    if target.kind != canonical.KIND_PR or target.number != value["number"]:
        _fail(PROBLEM_BAD_VALUE,
              "%s.url does not name pull request %d"
              % (location, value["number"]))
    _require_hex(value["head_sha"], location + ".head_sha", 40)
    _require_ref(value["base_ref"], location + ".base_ref")


def _validate_base_state(value, location):
    _require_dict(value, location)
    _require_closed_keys(
        value, ("current_base_oid", "refreshed_at", "advance_after_commit"),
        location,
    )
    _require_hex(value["current_base_oid"], location + ".current_base_oid",
                 40)
    if value["refreshed_at"] is not None:
        _require_timestamp(value["refreshed_at"], location + ".refreshed_at")
    advance = value["advance_after_commit"]
    if advance is not None:
        where = location + ".advance_after_commit"
        _require_dict(advance, where)
        _require_closed_keys(advance, ("old_base_oid", "new_base_oid",
                                       "recorded_at"), where)
        _require_hex(advance["old_base_oid"], where + ".old_base_oid", 40)
        _require_hex(advance["new_base_oid"], where + ".new_base_oid", 40)
        _require_timestamp(advance["recorded_at"], where + ".recorded_at")


AUTHORITY_KEYS = (
    "schema_version", "delivery_id", "revision", "previous_delivery_id",
    "workflow_identity", "mission", "repository", "remote", "mode",
    "source", "target_base", "original_baseline", "candidate", "evidence",
    "allowed_actions", "committer", "reverification", "pr_content",
    "human_authorization", "expiration",
)
STATE_KEYS = (
    "phase", "steps", "base_state", "revocation", "blocker",
    "pull_request", "updated_at",
)
_TOP_LEVEL_KEYS = AUTHORITY_KEYS + ("authority_digest_sha256",) + STATE_KEYS


def authority_digest(document):
    """Canonical digest of the immutable authority half."""
    return json_digest({key: document[key] for key in AUTHORITY_KEYS})


def validate_authorization(document, location="PR delivery authorization"):
    """Validate one record completely, failing closed on the first problem.

    Raises AuthorizationError with a distinct ``problem`` code.
    """
    _require_dict(document, location)
    version = document.get("schema_version")
    if isinstance(version, bool) or version != SCHEMA_VERSION:
        _fail(
            PROBLEM_SCHEMA_VERSION,
            "%s has schema_version %r; this layer understands only %d"
            % (location, version, SCHEMA_VERSION),
        )
    _require_closed_keys(document, _TOP_LEVEL_KEYS, location)
    _require_id(document["delivery_id"], location + ".delivery_id")
    _require_int(document["revision"], location + ".revision", minimum=1)
    if document["previous_delivery_id"] is not None:
        _require_id(document["previous_delivery_id"],
                    location + ".previous_delivery_id")
    if document["revision"] == 1 and document["previous_delivery_id"] is not (
        None
    ):
        _fail(PROBLEM_BAD_VALUE,
              "%s.revision 1 cannot have a previous_delivery_id" % location)
    if document["revision"] > 1 and document["previous_delivery_id"] is None:
        _fail(PROBLEM_BAD_VALUE,
              "%s.revision > 1 requires previous_delivery_id" % location)
    identity = document["workflow_identity"]
    where = location + ".workflow_identity"
    _require_dict(identity, where)
    _require_closed_keys(identity, ("workflow_id", "engineering_task_id"),
                         where)
    _require_id(identity["workflow_id"], where + ".workflow_id")
    _require_line_free_str(identity["engineering_task_id"],
                           where + ".engineering_task_id",
                           max_chars=MAX_ID_CHARS)
    mission = document["mission"]
    if mission is not None:
        where = location + ".mission"
        _require_dict(mission, where)
        _require_closed_keys(
            mission, ("workflow_id", "mission_authorization_digest_sha256"),
            where,
        )
        _require_id(mission["workflow_id"], where + ".workflow_id")
        _require_hex(mission["mission_authorization_digest_sha256"],
                     where + ".mission_authorization_digest_sha256", 64)
    repository_target = _validate_repository(document["repository"],
                                             location + ".repository")
    _validate_remote(document["remote"], location + ".remote",
                     repository_target)
    if document["mode"] != MODE_PULL_REQUEST:
        _fail(
            PROBLEM_MODE,
            "%s.mode must be exactly %r; got %r"
            % (location, MODE_PULL_REQUEST, document["mode"]),
        )
    _require_branch_pair(document["source"], location + ".source")
    _require_branch_pair(document["target_base"], location + ".target_base")
    if document["source"]["ref"] == document["target_base"]["ref"]:
        _fail(PROBLEM_BAD_VALUE,
              "%s.source and target_base name the same branch" % location)
    baseline = document["original_baseline"]
    where = location + ".original_baseline"
    _require_dict(baseline, where)
    _require_closed_keys(baseline, ("ref", "commit_sha"), where)
    _require_ref(baseline["ref"], where + ".ref")
    _require_hex(baseline["commit_sha"], where + ".commit_sha", 40)
    if baseline["ref"] != document["target_base"]["ref"]:
        _fail(PROBLEM_BAD_VALUE,
              "%s.ref must be the target base ref" % where)
    _validate_candidate(document["candidate"], location + ".candidate")
    _validate_evidence(
        document["evidence"], location + ".evidence",
        document["candidate"]["identity_digest_sha256"],
    )
    _validate_allowed_actions(document["allowed_actions"],
                              location + ".allowed_actions")
    _validate_committer(document["committer"], location + ".committer")
    reverification = document["reverification"]
    where = location + ".reverification"
    _require_dict(reverification, where)
    _require_closed_keys(reverification, ("argv",), where)
    _validate_argv(reverification["argv"], where + ".argv")
    _validate_pr_content(document["pr_content"], location + ".pr_content")
    _validate_human_authorization(document["human_authorization"],
                                  location + ".human_authorization")
    _validate_expiration(
        document["expiration"], location + ".expiration",
        document["human_authorization"]["authorized_at"],
    )
    _require_hex(document["authority_digest_sha256"],
                 location + ".authority_digest_sha256", 64)
    if authority_digest(document) != document["authority_digest_sha256"]:
        _fail(
            PROBLEM_AUTHORITY_DIGEST,
            "%s.authority_digest_sha256 does not match the authority"
            " content; the record was altered after authorization and is"
            " refused" % location,
        )
    _require_member(document["phase"], PHASES, location + ".phase")
    _validate_steps(document["steps"], location + ".steps",
                    document["delivery_id"],
                    document["authority_digest_sha256"])
    _validate_base_state(document["base_state"], location + ".base_state")
    _validate_revocation(document["revocation"], location + ".revocation")
    _validate_blocker(document["blocker"], location + ".blocker")
    _validate_pull_request(document["pull_request"],
                           location + ".pull_request")
    _require_timestamp(document["updated_at"], location + ".updated_at")
    if document["phase"] == PHASE_REVOKED and not document["revocation"][
        "revoked"
    ]:
        _fail(PROBLEM_BAD_VALUE,
              "%s.phase REVOKED without a revocation" % location)
    if document["phase"] == PHASE_BLOCKED and document["blocker"] is None:
        _fail(PROBLEM_BAD_VALUE,
              "%s.phase BLOCKED without a blocker" % location)


def validate_transition(current_phase, new_phase):
    if current_phase not in ALLOWED_TRANSITIONS:
        _fail(PROBLEM_UNKNOWN_PHASE, "unknown phase %r" % (current_phase,))
    if new_phase not in ALLOWED_TRANSITIONS:
        _fail(PROBLEM_UNKNOWN_PHASE, "unknown phase %r" % (new_phase,))
    if new_phase not in ALLOWED_TRANSITIONS[current_phase]:
        _fail(
            PROBLEM_INVALID_TRANSITION,
            "transition %s -> %s is not allowed" % (current_phase, new_phase),
        )


def apply_transition(record, new_phase, now):
    validate_transition(record["phase"], new_phase)
    record["phase"] = new_phase
    record["updated_at"] = now


def is_expired(record, now):
    return now >= record["expiration"]["expires_at"]


def is_revoked(record):
    return bool(record["revocation"]["revoked"])


def new_authorization(delivery_id, authority, now):
    """Assemble a fresh record from the immutable authority fields.

    ``authority`` carries every AUTHORITY key except ``schema_version``
    and ``delivery_id``. The state half starts at AUTHORIZED with every
    step pending, the current base at the original baseline, no
    revocation, no blocker, no pull request. The result is validated
    before it is returned, so an invalid ceremony never yields a record.
    """
    document = {"schema_version": SCHEMA_VERSION, "delivery_id": delivery_id}
    for key in AUTHORITY_KEYS:
        if key in document:
            continue
        if key not in authority:
            _fail(PROBLEM_MISSING_KEY,
                  "new authorization is missing %r" % key)
        document[key] = authority[key]
    unknown = sorted(set(authority) - set(AUTHORITY_KEYS))
    if unknown:
        _fail(PROBLEM_UNKNOWN_KEY,
              "new authorization has unknown keys: %s"
              % ", ".join(map(repr, unknown)))
    document["authority_digest_sha256"] = authority_digest(document)
    document["phase"] = PHASE_AUTHORIZED
    document["steps"] = {
        step: {"state": STEP_PENDING, "receipt": None, "voided": []}
        for step in STEPS
    }
    document["base_state"] = {
        "current_base_oid": document["original_baseline"]["commit_sha"],
        "refreshed_at": None,
        "advance_after_commit": None,
    }
    document["revocation"] = {
        "revoked": False, "revoked_at": None, "revoked_by": None,
        "reason": None,
    }
    document["blocker"] = None
    document["pull_request"] = None
    document["updated_at"] = now
    validate_authorization(document)
    return document
