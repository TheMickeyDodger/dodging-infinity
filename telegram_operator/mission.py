"""DI-REMOTE-2 Mission Authorization: rendering + one-shot v2 approval.

A v2 planning turn returns a Mission Authorization DOCUMENT (the
closed-schema authority object). This module validates its VALUES
against the adapter's own bindings, builds the durable workflow
authority record (whose deterministic display rendering is computed
inside the record constructor by the single renderer in
``workflow_authority.rendering``), and implements the one-shot v2
approval that the displayed-content ordering contract arms. A v2 approval dispatches NO gateway turn: it consumes one-shot,
durably records authorization in the workflow store, and the Runtime
(a separate process) picks it up.

Authority split, enforced fail-closed:

- Codex authors the AUTHORITY content and the target/baseline/handoff
  bindings. It must NOT mint transport bindings: ``workflow_id`` and
  ``telegram_approval`` must arrive null and are stamped by the
  adapter (the authority on its own transport), or the document is
  refused. ``human_intent`` is in the same class with its own problem
  code: the exact original human intent (the Telegram text that
  initiated the mission) is stamped by the adapter from what the
  transport actually accepted — a document that supplies or alters it
  is refused, so a planning turn can never change what the human
  said.
- The adapter verifies ``control.repository_realpath`` equals its own
  configured repository exactly; a mission naming any other control
  repository is refused. The policy digest is shape-checked here;
  verifying it against the LIVE control repository is the Runtime's
  obligation before dispatch.
- DI-REMOTE-1 approvals can NEVER authorize a v2 mission: v2
  approvals live only in the workflow store with an explicit
  ``approval_kind`` (structural layer), AND any v2 callback whose id
  resolves to a v1-era approval record is refused by the explicit
  ``superseded_for_v2`` consumer, whose default for a missing or
  false marking is REFUSAL (fail-closed layer). Both layers hold
  independently.
"""

import fcntl
import json
import os
import secrets

from telegram_operator.state import RUNTIME_LOCK_FILE_NAME
from workflow_authority import canonical as canonical_module
from workflow_authority import record as record_module
from workflow_authority import rendering as rendering_module
from workflow_authority.authorization import (
    AuthorizationError,
    validate_authorization_structure,
    validate_authorization_values_deep,
)
from workflow_authority.digest import text_digest
from workflow_authority.migrate import SUPERSEDED_FOR_V2_KEY

# One-shot mission approval validity, seconds from creation. Hard
# constant, same bound as the v1 plan approval.
MISSION_APPROVAL_VALIDITY_SECONDS = 15 * 60

# v2 callback prefixes (v1 uses lowercase "a:"/"r:").
CALLBACK_MISSION_APPROVE_PREFIX = "A:"
CALLBACK_MISSION_REJECT_PREFIX = "R:"

# The exact header prepended to every displayed Mission Authorization.
MISSION_MESSAGE_HEADER = (
    "MISSION AUTHORIZATION (approve or reject with the buttons; typed"
    " text cannot approve):\n"
)

# Single sources: the canonicalizer owns the target grammar, the
# record layer owns the authority-content bound. These names stay
# importable here (and value-pinned by tests) as DERIVED aliases —
# never as second copies.
CANONICAL_TARGET_HOST = canonical_module.CANONICAL_TARGET_HOST
MAX_AUTHORITY_FIELD_CHARS = record_module.MAX_AUTHORITY_FIELD_CHARS
MAX_TARGET_NAME_CHARS = canonical_module.MAX_TARGET_NAME_CHARS

AUTHORITY_CONTENT_KEYS = rendering_module.AUTHORITY_CONTENT_KEYS

PROBLEM_BODY_INVALID_JSON = "mission_body_invalid_json"
PROBLEM_MINTED_BINDING = "mission_codex_minted_transport_binding"
PROBLEM_MINTED_INTENT = "mission_codex_minted_human_intent"
PROBLEM_AUTHORITY_TEXT = "mission_authority_text_invalid"
PROBLEM_CONTROL_SHAPE = "mission_control_shape"
PROBLEM_CONTROL_PATH = "mission_control_path_character"
PROBLEM_CONTROL_MISMATCH = "mission_control_mismatch"
PROBLEM_BASELINE_REF = "mission_baseline_ref_grammar"
PROBLEM_TARGET_SHAPE = "mission_target_shape"
PROBLEM_TARGET_HOST = "mission_target_host"
PROBLEM_TARGET_URL = "mission_target_url_not_canonical"
PROBLEM_ISSUE_SHAPE = "mission_issue_or_pr_shape"
PROBLEM_BASELINE_SHAPE = "mission_baseline_shape"
PROBLEM_HANDOFF_SHAPE = "mission_handoff_shape"
PROBLEM_REVISION_SHAPE = "mission_revision_shape"

PROBLEM_UNKNOWN_WORKFLOW = "mission_unknown_workflow"
PROBLEM_RECORD_INVALID = "mission_record_invalid"
PROBLEM_NOT_A_MISSION_APPROVAL = "mission_wrong_approval_kind"
PROBLEM_UNPROVEN_PLANNING = "mission_no_fresh_planning_turn"
PROBLEM_SUPERSEDED = "mission_superseded"
PROBLEM_ALREADY_CONSUMED = "mission_already_consumed"
PROBLEM_USER_MISMATCH = "mission_user_mismatch"
PROBLEM_CHAT_MISMATCH = "mission_chat_mismatch"
PROBLEM_UNBOUND_MESSAGE = "mission_unbound_message"
PROBLEM_MESSAGE_MISMATCH = "mission_message_mismatch"
PROBLEM_REPOSITORY_MISMATCH = "mission_repository_mismatch"
PROBLEM_WRONG_PHASE = "mission_wrong_phase"
PROBLEM_EXPIRED = "mission_expired"
PROBLEM_V1_APPROVAL = "v1_approval_never_authorizes_v2"
PROBLEM_STORE_UNREADABLE = "mission_store_unreadable"


class MissionError(Exception):
    """A Mission Authorization failed validation; message actionable."""

    def __init__(self, message, problem):
        super(MissionError, self).__init__(message)
        self.problem = problem


def _default_workflow_id_factory():
    return "wf-" + secrets.token_hex(12)


def _default_nonce_factory():
    return secrets.token_hex(32)


def _require_text(document, key):
    value = document.get(key)
    if not isinstance(value, str) or not value.strip():
        raise MissionError(
            "Mission Authorization %r must be a non-empty string"
            % (key,),
            PROBLEM_AUTHORITY_TEXT,
        )
    if len(value) > MAX_AUTHORITY_FIELD_CHARS:
        raise MissionError(
            "Mission Authorization %r is %d characters; the hard"
            " bound is %d and the document is refused, not truncated"
            % (key, len(value), MAX_AUTHORITY_FIELD_CHARS),
            PROBLEM_AUTHORITY_TEXT,
        )
    return value


def validate_mission_document(body, configured_repository):
    """Parse and validate a Mission Authorization body, failing closed.

    Runs the closed-structure guards, the deep nested-strategy walk,
    and every VALUE check the adapter can make deterministically.
    Returns the validated document. Raises MissionError (or
    AuthorizationError from the structural guards) on every failure;
    never partially accepts.
    """
    try:
        document = json.loads(body)
    except (TypeError, ValueError):
        raise MissionError(
            "Mission Authorization body is not valid JSON",
            PROBLEM_BODY_INVALID_JSON,
        )
    validate_authorization_structure(document)
    validate_authorization_values_deep(document)
    # Transport bindings are the ADAPTER's authority: Codex must not
    # mint them.
    for key in ("workflow_id", "telegram_approval"):
        if document.get(key) is not None:
            raise MissionError(
                "Mission Authorization %r must be null: transport"
                " bindings are stamped by the adapter, never minted by"
                " the Operator" % (key,),
                PROBLEM_MINTED_BINDING,
            )
    # The exact original human intent is in the same adapter-stamped
    # class, with its own problem code: a document that supplies or
    # alters it is refused outright — the Operator can never change
    # what the human said.
    if document.get("human_intent") is not None:
        raise MissionError(
            "Mission Authorization 'human_intent' must be null: the"
            " exact original human intent is stamped by the adapter"
            " from the text the transport accepted, never minted or"
            " altered by the Operator",
            PROBLEM_MINTED_INTENT,
        )
    for key in AUTHORITY_CONTENT_KEYS:
        _require_text(document, key)
    control = document.get("control")
    if not isinstance(control, dict) or set(control.keys()) != {
        "repository_realpath", "policy_digest_sha256"
    }:
        raise MissionError(
            "Mission Authorization control must be an object with"
            " exactly repository_realpath and policy_digest_sha256",
            PROBLEM_CONTROL_SHAPE,
        )
    # Round-02 F-6: the control path is rendered UNQUOTED on a
    # binding line, so a path carrying control characters or line
    # terminators is refused with its own code, BEFORE the mismatch
    # check (so this layer is independently load-bearing, not
    # shadowed by the exact-equality pin below). The line-free rule
    # is the SHARED public helper — single source, two layers, each
    # with its own code.
    realpath = control["repository_realpath"]
    if isinstance(realpath, str) and (
        record_module.path_character_problem(realpath) is not None
    ):
        raise MissionError(
            "Mission Authorization control repository path %s; a path"
            " with line structure could forge displayed binding"
            " lines. Refused"
            % record_module.path_character_problem(realpath),
            PROBLEM_CONTROL_PATH,
        )
    if control["repository_realpath"] != configured_repository:
        raise MissionError(
            "Mission Authorization names control repository %r; this"
            " adapter is pinned to %r. Refused"
            % (control["repository_realpath"], configured_repository),
            PROBLEM_CONTROL_MISMATCH,
        )
    policy_digest = control["policy_digest_sha256"]
    if (
        not isinstance(policy_digest, str)
        or len(policy_digest) != 64
        or any(ch not in "0123456789abcdef" for ch in policy_digest)
    ):
        raise MissionError(
            "Mission Authorization policy digest must be 64 lowercase"
            " hex characters",
            PROBLEM_CONTROL_SHAPE,
        )
    target = document.get("target")
    if not isinstance(target, dict) or set(target.keys()) != {
        "canonical_host", "owner", "repo", "canonical_url"
    }:
        raise MissionError(
            "Mission Authorization target must be an object with"
            " exactly canonical_host, owner, repo, canonical_url",
            PROBLEM_TARGET_SHAPE,
        )
    if target["canonical_host"] != CANONICAL_TARGET_HOST:
        raise MissionError(
            "Mission Authorization target host %r is not the canonical"
            " %r" % (target["canonical_host"], CANONICAL_TARGET_HOST),
            PROBLEM_TARGET_HOST,
        )
    # The ONE canonicalizer (workflow_authority.canonical) validates
    # the URL; its distinct problem codes propagate so every hostile
    # shape (confusable host, traversal, multi-target, ...) is
    # refused with its own code. The displayed owner/repo must equal
    # the canonical parse, or the URL could name a different
    # repository than the displayed identity.
    try:
        parsed_target = canonical_module.canonicalize_repository_url(
            target["canonical_url"]
        )
    except canonical_module.CanonicalizationError as exc:
        raise MissionError(str(exc), exc.problem)
    if (
        target["owner"] != parsed_target.owner
        or target["repo"] != parsed_target.repo
    ):
        raise MissionError(
            "Mission Authorization target owner/repo (%r, %r) do not"
            " match the canonical parse of the target URL (%r, %r)"
            % (target["owner"], target["repo"], parsed_target.owner,
               parsed_target.repo),
            PROBLEM_TARGET_URL,
        )
    issue_or_pr = document.get("issue_or_pr")
    if issue_or_pr is not None and (
        not isinstance(issue_or_pr, dict)
        or set(issue_or_pr.keys()) != {"kind", "number"}
        or issue_or_pr["kind"] not in record_module.ISSUE_OR_PR_KINDS
        or isinstance(issue_or_pr["number"], bool)
        or not isinstance(issue_or_pr["number"], int)
        or issue_or_pr["number"] < 1
    ):
        raise MissionError(
            "Mission Authorization issue_or_pr must be null"
            " (repository-only target) or {kind: issue|pr, number:"
            " positive integer}",
            PROBLEM_ISSUE_SHAPE,
        )
    baseline = document.get("baseline")
    if (
        not isinstance(baseline, dict)
        or set(baseline.keys()) != {"ref", "commit_sha"}
        or not isinstance(baseline["ref"], str)
        or not baseline["ref"]
        or not isinstance(baseline["commit_sha"], str)
        or len(baseline["commit_sha"]) != 40
        or any(
            ch not in "0123456789abcdef"
            for ch in baseline["commit_sha"]
        )
    ):
        raise MissionError(
            "Mission Authorization baseline must be {ref: non-empty"
            " string, commit_sha: 40 lowercase hex}",
            PROBLEM_BASELINE_SHAPE,
        )
    # Round-02 F-6: the ref is Codex-authored and rendered UNQUOTED
    # on a binding line — the closed git ref grammar (single source:
    # workflow_authority.record.baseline_ref_grammar_problem) is
    # enforced HERE, before any record exists, with this layer's own
    # problem code; the record layer enforces it again on every
    # load/save.
    reason = record_module.baseline_ref_grammar_problem(
        baseline["ref"]
    )
    if reason is not None:
        raise MissionError(
            "Mission Authorization baseline ref %s; a ref outside"
            " the closed git ref grammar could forge displayed"
            " binding lines. Refused" % reason,
            PROBLEM_BASELINE_REF,
        )
    handoff = document.get("handoff")
    if (
        not isinstance(handoff, dict)
        or set(handoff.keys()) != {"revision", "text"}
        or isinstance(handoff["revision"], bool)
        or not isinstance(handoff["revision"], int)
        or handoff["revision"] < 1
        or not isinstance(handoff["text"], str)
        or not handoff["text"].strip()
        or handoff["text"] != handoff["text"].strip()
    ):
        raise MissionError(
            "Mission Authorization handoff must be {revision: positive"
            " integer, text: non-empty string with no leading or"
            " trailing whitespace (byte-exact dispatch requirement)}",
            PROBLEM_HANDOFF_SHAPE,
        )
    if len(handoff["text"]) > record_module.MAX_AUTHORITY_TEXT_CHARS:
        raise MissionError(
            "Mission Authorization handoff text is %d characters; the"
            " hard bound is %d and the document is refused, not"
            " truncated" % (
                len(handoff["text"]),
                record_module.MAX_AUTHORITY_TEXT_CHARS,
            ),
            PROBLEM_HANDOFF_SHAPE,
        )
    revision = document.get("revision")
    if (
        isinstance(revision, bool)
        or not isinstance(revision, int)
        or revision < 1
    ):
        raise MissionError(
            "Mission Authorization revision must be a positive integer",
            PROBLEM_REVISION_SHAPE,
        )
    return document


def build_workflow_record(document, human_intent, user_id, chat_id,
                          now, workflow_id, nonce_factory=None):
    """Build the durable workflow record for a validated mission.

    ``human_intent`` is the ADAPTER-stamped exact text the transport
    accepted; the record stores it byte-exact and the rendering binds
    it. The rendered text is computed inside ``new_record`` by the
    single renderer, so the render binding holds by construction.
    Raises workflow_authority RecordError when any bound is exceeded
    (the rendered text over the authority-text bound included) — the
    caller refuses honestly and nothing is persisted.
    """
    make_nonce = nonce_factory or _default_nonce_factory
    issue_or_pr = document["issue_or_pr"]
    return record_module.new_record(
        workflow_id=workflow_id,
        human_intent=human_intent,
        repository_realpath=(
            document["control"]["repository_realpath"]
        ),
        policy_digest_sha256=(
            document["control"]["policy_digest_sha256"]
        ),
        canonical_host=document["target"]["canonical_host"],
        owner=document["target"]["owner"],
        repo=document["target"]["repo"],
        canonical_url=document["target"]["canonical_url"],
        issue_or_pr_kind=(
            None if issue_or_pr is None else issue_or_pr["kind"]
        ),
        issue_or_pr_number=(
            None if issue_or_pr is None else issue_or_pr["number"]
        ),
        baseline_ref=document["baseline"]["ref"],
        baseline_commit_sha=document["baseline"]["commit_sha"],
        objective=document["objective"],
        constraints=document["constraints"],
        rules=document["rules"],
        desired_outcome=document["desired_outcome"],
        acceptance=document["acceptance"],
        unresolved_questions=document["unresolved_questions"],
        execution_scope=document["execution_scope"],
        mission_revision=document["revision"],
        telegram_user_id=user_id,
        telegram_chat_id=chat_id,
        approval_nonce=make_nonce(),
        approval_created_at=now,
        approval_expires_at=now + MISSION_APPROVAL_VALIDITY_SECONDS,
        handoff_revision=document["handoff"]["revision"],
        handoff_text=document["handoff"]["text"],
    )


def supersede_chat_missions(workflows_document, chat_id):
    """Void every still-approvable PLANNED mission in this chat.

    A newer mission invalidates older unconsumed ones (revision
    invalidation, the v1 precedent). Superseded records leave the
    active set (phase -> BLOCKED, a table-allowed transition) so the
    store cap can prune them. Returns the exact count superseded.
    """
    count = 0
    for entry in workflows_document["workflows"].values():
        if (
            entry["telegram"]["chat_id"] == chat_id
            and entry["phase"] == record_module.PHASE_PLANNED
            and entry["approval"]["consumed_at"] is None
            and not entry["approval"]["superseded"]
        ):
            entry["approval"]["superseded"] = True
            record_module.apply_transition(
                entry, record_module.PHASE_BLOCKED
            )
            count += 1
    return count


def void_mission(workflows_document, workflow_id):
    """Durably-voidable helper: supersede + terminal phase.

    Caller must SAVE the store afterwards; a void that only mutates
    memory is the recorded fail-closed-state-asserted-only-in-memory
    class.
    """
    entry = workflows_document["workflows"].get(workflow_id)
    if entry is None:
        return False
    entry["approval"]["superseded"] = True
    if entry["phase"] not in record_module.TERMINAL_PHASES:
        record_module.apply_transition(
            entry, record_module.PHASE_BLOCKED
        )
    return True


def refuse_v1_approval_for_v2(state_document, approval_id):
    """The explicit ``superseded_for_v2`` consumer (ruling E-4).

    Given a v2-labelled callback id, refuse it OUTRIGHT if the id
    resolves to a DI-REMOTE-1 approval record, whatever its marking:
    a True ``superseded_for_v2`` means the migration superseded it for
    v2 purposes; a MISSING or False marking grants nothing either —
    the fail-closed default is refusal, because no v1 approval ever
    carries the v2 binding set. Returns a problem string or None.

    This layer is checked BEFORE the workflow-store lookup, so it
    holds even without the structural layer (and vice versa).
    """
    approvals = state_document.get("approvals")
    if not isinstance(approvals, dict):
        return None
    entry = approvals.get(approval_id)
    if entry is None:
        return None
    marking = entry.get(SUPERSEDED_FOR_V2_KEY)
    if marking is True:
        return PROBLEM_V1_APPROVAL
    # Missing or False: STILL refused — absence of the marking never
    # grants v2 authority (fail-closed default).
    return PROBLEM_V1_APPROVAL


def evaluate_mission_callback(workflows_document, workflow_id, user_id,
                              chat_id, repository, message_id, now):
    """Validate a v2 decision callback against the stored workflow.

    Fixed check order, each failing closed with a distinct problem
    code. The stored record is re-validated against the closed schema
    first, so on-disk tampering (altered rendered text, revision, or
    digest) is refused before any binding comparison. Returns
    ``(record, None)`` only when every binding matches.
    """
    entry = workflows_document["workflows"].get(workflow_id)
    if entry is None:
        return None, PROBLEM_UNKNOWN_WORKFLOW
    try:
        record_module.validate_record(entry)
    except record_module.RecordError:
        # validate_record enforces the TOTAL render binding
        # (record.PROBLEM_RENDER_BINDING): the rendered text must be
        # byte-equal to the deterministic rendering of the record's
        # own fields, so ANY field tampered independently of the
        # digested text — revision, either target form, baseline, the
        # human intent, every authority section — is refused right
        # here. The former first-binding-line check was subsumed by
        # that equality and removed as dead code.
        return None, PROBLEM_RECORD_INVALID
    # I3 (D4c authority gap): a PLANNED record armed by the pre-I2
    # resumed/ambient path carries NO planning-turn identity
    # (codex_turns holds no role "planning" entry). An approval must
    # never consume a record whose authorization did not come from a
    # proven fresh planning turn — such records fail closed here and
    # can only be replaced by re-sending the intent through the
    # planning boundary.
    if not any(
        turn["role"] == record_module.TURN_ROLE_PLANNING
        for turn in entry["codex_turns"]
    ):
        return None, PROBLEM_UNPROVEN_PLANNING
    approval = entry["approval"]
    # BELT ONLY (round-06 finding N2): E-4's actual structural layer
    # is validate_record's closed APPROVAL_KINDS set (single element),
    # which the re-validation above has already enforced — a record
    # with any other kind can never reach this line. Kept as an
    # explicit second read of the discriminator, not as the
    # protection.
    if approval["approval_kind"] != (
        record_module.APPROVAL_KIND_MISSION_V2
    ):
        return None, PROBLEM_NOT_A_MISSION_APPROVAL
    if approval["superseded"]:
        return None, PROBLEM_SUPERSEDED
    if approval["consumed_at"] is not None:
        return None, PROBLEM_ALREADY_CONSUMED
    if entry["telegram"]["user_id"] != user_id:
        return None, PROBLEM_USER_MISMATCH
    if entry["telegram"]["chat_id"] != chat_id:
        return None, PROBLEM_CHAT_MISMATCH
    if entry["control_identity"]["repository_realpath"] != repository:
        return None, PROBLEM_REPOSITORY_MISMATCH
    bound_message = entry["telegram"]["plan_message_id"]
    if isinstance(bound_message, bool) or not isinstance(
        bound_message, int
    ):
        # Persisted-but-not-actionable: the record exists durably
        # before the mission is displayed, and becomes actionable only
        # once the complete display is proven and the exact message id
        # bound. A callback omitting its message id must never pass a
        # None/None comparison (the recorded tautology class).
        return None, PROBLEM_UNBOUND_MESSAGE
    if bound_message != message_id:
        return None, PROBLEM_MESSAGE_MISMATCH
    if entry["phase"] != record_module.PHASE_PLANNED:
        return None, PROBLEM_WRONG_PHASE
    if now >= approval["expires_at"]:
        return None, PROBLEM_EXPIRED
    return entry, None


def consume_mission(workflows_document, workflow_id, decision,
                    update_id, now):
    """One-shot consumption of a v2 mission approval.

    Returns True exactly once. APPROVE transitions PLANNED ->
    AUTHORIZED (the Runtime claims it from there); REJECT transitions
    PLANNED -> BLOCKED. The caller must durably persist the workflow
    store BEFORE any external acknowledgement.
    """
    entry = workflows_document["workflows"].get(workflow_id)
    if entry is None:
        return False
    approval = entry["approval"]
    if approval["consumed_at"] is not None or approval["superseded"]:
        return False
    if entry["phase"] != record_module.PHASE_PLANNED:
        return False
    approval["consumed_at"] = now
    approval["consumed_by_update_id"] = update_id
    approval["decision"] = decision
    if decision == record_module.DECISION_APPROVE:
        record_module.apply_transition(
            entry, record_module.PHASE_AUTHORIZED
        )
    else:
        record_module.apply_transition(
            entry, record_module.PHASE_BLOCKED
        )
    return True


def workflow_phase_counts(workflows_document):
    """Exact per-phase workflow counts for /status."""
    counts = {}
    for entry in workflows_document["workflows"].values():
        counts[entry["phase"]] = counts.get(entry["phase"], 0) + 1
    return counts


def runtime_status(state_directory):
    """Probe the Runtime's single-instance lock, non-destructively.

    Returns ``(running, detail)``. ``running`` is True only when a
    live process holds the Runtime lock. The probe acquires nothing
    when the lock is held and releases immediately when it is not; it
    never blocks. Every not-running shape carries an ACTIONABLE
    detail: authorized missions will not start until the Runtime runs.
    """
    lock_path = os.path.join(state_directory, RUNTIME_LOCK_FILE_NAME)
    if not os.path.exists(lock_path):
        return False, (
            "Runtime is NOT installed or has never run (no %s in %s)."
            " Authorized missions will NOT start. Install and start"
            " the Runtime service: run scripts/dirun-agent.sh install"
            " from the control repository (or start it in the"
            " foreground with: dirun run)." % (
                RUNTIME_LOCK_FILE_NAME, state_directory,
            )
        )
    try:
        descriptor = os.open(lock_path, os.O_RDWR)
    except OSError as exc:
        return False, (
            "Runtime lock %s could not be probed (%s). Authorized"
            " missions may not start; check the Runtime service."
            % (lock_path, exc)
        )
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            return True, "Runtime is running (lock held)."
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        return False, (
            "Runtime is NOT running (its lock file %s exists but no"
            " process holds it). Authorized missions will NOT start."
            " Start the Runtime service: scripts/dirun-agent.sh"
            " install, or in the foreground: dirun run." % lock_path
        )
    finally:
        os.close(descriptor)
