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

The document validation and the record construction are the
transport-neutral intake seam, ``workflow_authority.intake`` (I2 of
task 20261010-164104-1761f6), which the Grok Bot lane calls too. This
module keeps them under their existing names as THIN WRAPPERS, so the
Telegram lane is unchanged byte for byte: the same refusals, problem
codes, messages, records, rendered text and digests. The one
translation is deliberate: the seam names a minted ``workflow_id`` and
a minted ``telegram_approval`` with distinct codes, and this lane keeps
reporting both under its one historical code,
``mission_codex_minted_transport_binding``.

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
import os
import secrets

from telegram_operator.state import RUNTIME_LOCK_FILE_NAME
from workflow_authority import canonical as canonical_module
from workflow_authority import intake as intake_module
from workflow_authority import record as record_module
from workflow_authority import rendering as rendering_module
from workflow_authority.authorization import AuthorizationError  # noqa: F401 (re-exported)
from workflow_authority.migrate import SUPERSEDED_FOR_V2_KEY

# One-shot mission approval validity, seconds from creation. The single
# source is the intake seam; this lane's name for it is unchanged.
MISSION_APPROVAL_VALIDITY_SECONDS = (
    intake_module.MISSION_APPROVAL_VALIDITY_SECONDS
)

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

# The document-validation problem codes are the intake seam's, re-exported
# under this lane's existing names (single source).
PROBLEM_BODY_INVALID_JSON = intake_module.PROBLEM_BODY_INVALID_JSON
# This lane's ONE historical code for either minted transport binding.
PROBLEM_MINTED_BINDING = "mission_codex_minted_transport_binding"
PROBLEM_MINTED_INTENT = intake_module.PROBLEM_MINTED_INTENT
PROBLEM_AUTHORITY_TEXT = intake_module.PROBLEM_AUTHORITY_TEXT
PROBLEM_CONTROL_SHAPE = intake_module.PROBLEM_CONTROL_SHAPE
PROBLEM_CONTROL_PATH = intake_module.PROBLEM_CONTROL_PATH
PROBLEM_CONTROL_MISMATCH = intake_module.PROBLEM_CONTROL_MISMATCH
PROBLEM_BASELINE_REF = intake_module.PROBLEM_BASELINE_REF
PROBLEM_TARGET_SHAPE = intake_module.PROBLEM_TARGET_SHAPE
PROBLEM_TARGET_HOST = intake_module.PROBLEM_TARGET_HOST
PROBLEM_TARGET_URL = intake_module.PROBLEM_TARGET_URL
PROBLEM_ISSUE_SHAPE = intake_module.PROBLEM_ISSUE_SHAPE
PROBLEM_BASELINE_SHAPE = intake_module.PROBLEM_BASELINE_SHAPE
PROBLEM_HANDOFF_SHAPE = intake_module.PROBLEM_HANDOFF_SHAPE
PROBLEM_REVISION_SHAPE = intake_module.PROBLEM_REVISION_SHAPE

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
# I1 (task 20261010-164104-1761f6): a Telegram decision callback named
# a workflow that did not originate on the Telegram lane.
PROBLEM_NOT_TELEGRAM_ORIGIN = "mission_not_telegram_origin"

# The seam's exception class, under this lane's existing name.
MissionError = intake_module.MissionError

# The seam's distinct minted-binding codes, which this lane reports under
# PROBLEM_MINTED_BINDING.
_SEAM_MINTED_BINDING_PROBLEMS = frozenset(
    problem for _, problem in intake_module.MINTED_BINDING_PROBLEMS
)


def _default_workflow_id_factory():
    return "wf-" + secrets.token_hex(12)


def _default_nonce_factory():
    return secrets.token_hex(32)


def validate_mission_document(body, configured_repository):
    """Parse and validate a Mission Authorization body, failing closed.

    The Telegram lane's wrapper around the shared intake seam
    (``workflow_authority.intake.validate_mission_document``): the same
    validator, unchanged, with this lane's one historical code for a
    minted transport binding (the message is the seam's, unchanged).
    Returns the validated document. Raises MissionError (or
    AuthorizationError from the structural guards) on every failure;
    never partially accepts.
    """
    try:
        return intake_module.validate_mission_document(
            body, configured_repository
        )
    except MissionError as exc:
        if exc.problem not in _SEAM_MINTED_BINDING_PROBLEMS:
            raise
        raise MissionError(str(exc), PROBLEM_MINTED_BINDING)


def build_workflow_record(document, human_intent, user_id, chat_id,
                          now, workflow_id, nonce_factory=None):
    """Build the durable workflow record for a validated mission.

    ``human_intent`` is the ADAPTER-stamped exact text the transport
    accepted; the record stores it byte-exact and the rendering binds
    it. The rendered text is computed inside ``new_record`` by the
    single renderer, so the render binding holds by construction.
    Raises workflow_authority RecordError when any bound is exceeded
    (the rendered text over the authority-text bound included) — the
    caller refuses honestly and nothing is persisted. The Telegram
    lane's wrapper around ``workflow_authority.intake``: this lane
    stamps the Telegram user and chat, and no origin.
    """
    make_nonce = nonce_factory or _default_nonce_factory
    return intake_module.build_workflow_record(
        document, human_intent, now=now, workflow_id=workflow_id,
        nonce=make_nonce(), telegram_user_id=user_id,
        telegram_chat_id=chat_id,
    )


def supersede_chat_missions(workflows_document, chat_id):
    """Void every still-approvable PLANNED mission in this chat.

    A newer mission invalidates older unconsumed ones (revision
    invalidation, the v1 precedent). Superseded records leave the
    active set (phase -> BLOCKED, a table-allowed transition) so the
    store cap can prune them. Returns the exact count superseded.

    A relay-origin workflow (I1: non-null ``origin``) is in NO Telegram
    chat's scope, so it is skipped: never superseded, voided or
    altered by a Telegram chat's newer mission.
    """
    count = 0
    for entry in workflows_document["workflows"].values():
        if entry["origin"] is not None:
            continue
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
    # The shared intake seam's step (supersede + terminal phase),
    # unchanged in behaviour.
    intake_module.void_workflow(entry)
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
    # I1: a Telegram callback never resolves against a workflow that
    # did not originate on the Telegram lane. Refused FIRST, before any
    # binding below reads the (null) telegram binding.
    if entry["origin"] is not None:
        return None, PROBLEM_NOT_TELEGRAM_ORIGIN
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
    # I1: this consumption records a Telegram update id, which a
    # relay-origin workflow must never carry. Refused here AND by the
    # record validator (two independent layers).
    if entry["origin"] is not None:
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

    Returns ``(running, detail)``. ``running`` is True when the
    probe's non-blocking exclusive ``flock`` attempt fails with ANY
    ``OSError``: a lock held by another process is the expected cause,
    but every other flock error is reported the same way, so True means
    the probe could not take the lock, not proof that a live Runtime
    holds it. It is False when the lock file is absent, cannot be
    opened, or was free (the probe took it and released it at once).
    The lock attempt never waits (``LOCK_NB``); the lock file itself is
    opened with an ordinary open. Every not-running shape carries an
    ACTIONABLE detail: authorized missions will not start until the
    Runtime runs.
    """
    lock_path = os.path.join(state_directory, RUNTIME_LOCK_FILE_NAME)
    if not os.path.exists(lock_path):
        return False, (
            "Runtime is NOT installed or has never run (no %s in %s)."
            " Authorized missions will NOT start. Install and start"
            " the Runtime service: run scripts/dirun-agent.sh install"
            " from the control repository, or tgop runtime-service"
            " install --root <control repository> (or start it in the"
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
            " install or tgop runtime-service start, or in the"
            " foreground: dirun run." % lock_path
        )
    finally:
        os.close(descriptor)
