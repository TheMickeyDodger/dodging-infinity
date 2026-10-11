"""The conforming Grok Bot route (I2 of task 20261010-164104-1761f6).

The DEFAULT engineering route: a Grok Bot ``request`` becomes ONE
DI-REMOTE-2 workflow, the same durable authority object the Telegram lane
produces, and nothing else (no Mission-Core proposal). The sequence:

1. ``request``, OPERATOR_PROTOCOL.md route (b), two turns, each through its
   own OperatorSession (``prepare`` then ``execute``, one provider call per
   prepared turn). The INITIAL Operator turn
   (``operator_session.RestrictedCodexOperatorSession``, the restrictive
   read-only posture) either CLARIFIES in prose (returned to the human,
   quoted; nothing is proposed or recorded) or emits the DI-REMOTE-2
   ROUTING SIGNAL, whose body carries no authority and is discarded. The
   signal triggers the SEPARATE fresh restrictive planning turn
   (``operator_session.codex.RestrictedPlanningOperatorSession`` ->
   ``codex_gateway.gateway.submit_planning`` ->
   ``codex_gateway.role_turn.run_planning_turn``, rooted at the configured
   control repository, posture verified on the exact argv, no session),
   whose answer set is closed to exactly ``mission_authorization``: it
   authors a closed-schema Mission Authorization document, and anything
   else fails closed. The shared intake seam
   (``workflow_authority.intake``) validates it against the configured
   control repository and builds the record. The TRUSTED local seam stamps
   the bindings, never the document: the workflow id from the local
   factory, the approval from the local clock and a local nonce, the origin
   ``{"transport": "grok_bot", "relay_ref": <gr- request_ref>}``, and
   ``human_intent`` as the exact text this transport accepted, verbatim. A
   document that arrives with any of them pre-filled is refused. The record
   is saved PLANNED in the workflow store the Runtime claims from.
2. ``present``: the EXACT rendered Mission Authorization (the text whose
   digest the authority layer verifies), within a fixed frame, plus the
   binding a reply must restate and the local arming command. The receipt is
   recorded durably BEFORE the display is returned.
3. Local arming (``grokbot.py ... authorize-workflow``, never an MCP tool)
   commits to exactly the displayed binding and prints a one-time code.
4. ``approve``: the caller's binding is compared field by field and
   type-exactly with the receipt and refused on any difference, naming the
   fields; nothing is filled in or corrected. The arming must fire for
   exactly that binding, once, durably consumed BEFORE anything is applied.
   Only then is the approval consumed one-shot in the workflow record
   (``consumed_at``, ``decision`` approve, ``consumed_by_update_id`` null:
   a relay has no Telegram update) and the workflow moves PLANNED ->
   AUTHORIZED. A DI Runtime (``dirun``) running against that store claims
   it from there; Grok Bot starts nothing, and its status and result only
   read what the record holds (I3).

Withdrawal: the caller may withdraw its OWN request while it still awaits
its first decision (PLANNED, approval neither consumed nor superseded),
proving ownership with the request's one-shot withdrawal capability or, after
a lost reply, the sealed origin proof. A request_ref or workflow id alone
never suffices. Once the approval is consumed, the work may be running and
there is no supported seam to stop it here: that cancel is refused by name
and claims nothing.

What this module is NOT: it never dispatches, spawns, observes or
completes anything (the Runtime does), never grants delivery authority
(``delivery_authority`` is structurally "none"), and never arms (only the
local arming command writes commitments).

The planning turn's launch posture, and its limit, stated: the turn runs
``codex exec`` with ``--sandbox read-only --ignore-user-config
--ignore-rules --strict-config -c approval_policy=never``, verified on the
exact argv before the process starts. Read-only confines WRITES by its
shell commands, not READS: the human's text can induce it to read any file
the serving user can read. Its document is validated, and its prose is
never returned to the caller.
"""

import errno
import fcntl
import hmac
import json
import os
import secrets
import shlex
import stat

from local_request import store as request_store
from local_request import surface as surface_module
from operator_session.codex import RestrictedPlanningOperatorSession
from workflow_authority import intake as intake_module
from workflow_authority import record as record_module
from workflow_authority.authorization import AuthorizationError
from workflow_authority.digest import text_digest

from grok_bot import framing
from grok_bot import index as index_module

LANE_WORKFLOW = "di_remote_2_workflow"
LANE_LEGACY = "legacy_mission_core"

PROBLEM_NOT_CONFIGURED = "grok_bot_workflow_not_configured"
PROBLEM_OPERATOR_ENVELOPE = "grok_bot_operator_envelope_invalid"
PROBLEM_OPERATOR_REPLY_TOO_LARGE = "grok_bot_operator_reply_too_large"
PROBLEM_OPERATOR_REPLY_EMPTY = "grok_bot_operator_reply_empty"
PROBLEM_PLANNING_FAILED = "grok_bot_planning_turn_failed"
PROBLEM_PLANNING_ENVELOPE = "grok_bot_planning_envelope_invalid"
PROBLEM_UNENCODABLE = "grok_bot_authorization_unencodable"
PROBLEM_STORE_REFUSED = "grok_bot_workflow_store_refused"
PROBLEM_WORKFLOW_STORE_UNREADABLE = "grok_bot_workflow_store_unreadable"
PROBLEM_UNKNOWN_REQUEST = "grok_bot_workflow_request_unknown"
PROBLEM_WORKFLOW_MISSING = "grok_bot_workflow_missing"
PROBLEM_NOT_AWAITING = "grok_bot_workflow_not_awaiting_approval"
PROBLEM_STALE_BINDING = "grok_bot_workflow_binding_changed"
PROBLEM_EXPIRED = "grok_bot_authorization_expired"
PROBLEM_SUPERSEDED = "grok_bot_authorization_superseded"
PROBLEM_ALREADY_CONSUMED = "grok_bot_authorization_already_consumed"
PROBLEM_WRONG_LANE_FIELDS = "grok_bot_binding_wrong_lane"
PROBLEM_CAPABILITY_REFUSED = "grok_bot_workflow_capability_refused"
PROBLEM_NOT_PENDING = "grok_bot_workflow_not_pending"
PROBLEM_RUNNING_CANCEL = "grok_bot_workflow_running_cancel_unsupported"
PROBLEM_RECOVER_NOT_APPLICABLE = "grok_bot_workflow_recover_not_applicable"
PROBLEM_RUN_NOT_APPLICABLE = "grok_bot_workflow_run_not_applicable"
PROBLEM_LEGACY_REAUTHORIZATION = "grok_bot_legacy_reauthorization_required"
# I3 (B-1): the legacy lane's Grok arming is retired; it mints nothing.
PROBLEM_LEGACY_ARMING_RETIRED = "grok_bot_legacy_arming_retired"
# I3 (B-2): a legacy run command Grok Bot cannot perform and a new
# authorization would not help with (observe, pause, resume, cancel).
PROBLEM_LEGACY_RUN_UNAVAILABLE = "grok_bot_legacy_run_unavailable"

REPLY_CLARIFICATION = "clarification"
REPLY_ROUTING = "routing_signal"

CANCELLATION_PENDING = "pending_withdrawal"
CANCELLATION_RUNNING = "running_work_cancellation_not_supported"

# The one remedy that reaches a FRESH Mission Authorization, displayed
# wherever a request can no longer be approved (I2 review, finding 2). An
# identical request (the same text AND the same conversation_ref) is
# deliberately answered with the request it already made (duplicate safety,
# invariant I-32), so "send the request again" would only return this very
# request: the request must DIFFER.
FRESH_REQUEST_REMEDY = (
    "To get a fresh Mission Authorization, send a request that DIFFERS from"
    " this one: change its text, or send it from a new conversation (a"
    " different conversation_ref). An identical request (the same text and"
    " the same conversation_ref) is deliberately answered with this same"
    " request again, never a new one.")

LEGACY_REAUTHORIZATION_REMEDY = (
    "This request predates the conforming route: it holds a Mission-Core"
    " proposal, not a DI-REMOTE-2 Mission Authorization, and it is never"
    " converted into one. Nothing was approved, applied or started. "
    + FRESH_REQUEST_REMEDY +
    " The new request runs a fresh planning turn, which authors a Mission"
    " Authorization that you can present, arm and approve; only a DI"
    " Runtime running against its workflow store then advances it (status"
    " shows what the record holds).")

# The advice a legacy request shows where the human is about to act (I3,
# B-1/B-2), each followed literally by the tests. Only what genuinely needs
# a new authorization gets the reauthorization remedy: dispatching or
# advancing work, and arming an approval.
LEGACY_RUN_REMEDY = (
    "Grok Bot runs nothing: a legacy Mission-Core Mission is never"
    " dispatched, advanced or verified through this transport, and its"
    " authorization is never converted into a new one. Nothing was run. "
    + LEGACY_REAUTHORIZATION_REMEDY)
LEGACY_ARMING_REMEDY = (
    "Nothing was armed and no approval code was minted: a legacy"
    " Mission-Core proposal can never be approved through Grok Bot, so a"
    " code for it could never be used. " + LEGACY_REAUTHORIZATION_REMEDY)
LEGACY_OBSERVE_ADVICE = (
    "Grok Bot observes no target and runs nothing. What Mission Core"
    " recorded for this request is readable with status, and its recorded"
    " verified result with result (the run tool's result command answers"
    " the same). Nothing was run.")
LEGACY_RESULT_NOTE = (
    "A legacy Mission-Core result, recovered READ-ONLY by the same"
    " implementation the local operator surface's result runs"
    " (target_runtime.legacy_result). Grok Bot names no path: the content is"
    " read only from the two locators Mission Core recorded, through the"
    " hardened state-artifact reader, and re-hashed against the recorded"
    " digests; content that is missing, gone or changed is UNRECOVERABLE,"
    " never a verified result. The review text is the target's own"
    " (target-produced evidence, not independent verification). Nothing was"
    " started, advanced, recorded or consumed.")
# The local operator surface's spelling of each run-control command.
DIREQUEST_CONTROL_COMMANDS = {"pause": "pause", "resume": "resume",
                              "cancel": "cancel-run"}
DI_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def legacy_control_advice(command, request_ref, state_dir, control_repo):
    """Accurate advice for pausing, resuming or cancelling a legacy run:
    Grok Bot cannot, a new request would not, and the operator's local
    surface on the DI machine can (its own command, spelled exactly)."""
    argv = ["python3", os.path.join(DI_ROOT, "direquest.py"), "--state-dir",
            state_dir, DIREQUEST_CONTROL_COMMANDS[command], request_ref,
            "--control-repo", control_repo or "CONTROL_REPO"]
    return (
        "Grok Bot cannot %s a legacy run: it holds nothing that can reach"
        " running work, and a new request would not affect this one."
        " Nothing was changed. On the DI machine, under the operator's own"
        " authority, the local operator surface keeps this command: %s%s."
        " The run's recorded state is readable with status."
        % (command, " ".join(shlex.quote(part) for part in argv),
           "" if control_repo else
           " (CONTROL_REPO: the control repository's absolute path)"))

# The Runtime's OWN configuration (target_runtime.runtime_config, mirrored and
# pinned equal by test): its schema, its closed keys and the conventional
# file name a remedy displays.
RUNTIME_CONFIG_SCHEMA = "dodging-infinity.runtime-config.v1"
RUNTIME_CONFIG_KEYS = ("schema", "control_repository", "workflow_store")
RUNTIME_CONFIG_FILE_NAME = "runtime-config.json"


def runtime_commands(store_directory):
    """The command that runs a DI Runtime bound to THIS workflow store whose
    workspace trust is written where the Herdr it starts reads it, as an
    argv list: ``--runtime-config`` with a Runtime configuration inside the
    store (``target_runtime.cli``)."""
    config = os.path.join(os.path.realpath(store_directory),
                          RUNTIME_CONFIG_FILE_NAME)
    return {"foreground": ["python3", os.path.join(DI_ROOT, "dirun.py"),
                           "--runtime-config", config, "run"]}


def runtime_config_document(store_directory, control_repository):
    """Exactly what that Runtime configuration must hold, for THIS store
    and its control repository (no other field, and nothing to fill in),
    by their canonical paths, which is what the Runtime's loader requires."""
    return {"schema": RUNTIME_CONFIG_SCHEMA,
            "control_repository": os.path.realpath(control_repository),
            "workflow_store": os.path.realpath(store_directory)}


def runtime_recovery_remedy(store_directory, control_repository):
    """How to get a DI Runtime advancing THIS workflow store, with its
    prerequisite, what NOT to do, and what is not supported; Grok Bot
    starts nothing."""
    command = runtime_commands(store_directory)["foreground"]
    config = command[3]
    return (
        "Grok Bot starts nothing. A DI Runtime (dirun.py) advances only the"
        " workflow store it is configured for. To advance THIS store, start"
        " it with its own Runtime configuration, which binds this store and"
        " writes workspace trust where the Herdr it starts reads it: %s (it"
        " runs only while that command runs; nothing restarts it)."
        " PREREQUISITE: %s must already exist, readable by its owner only"
        " (mode 600, its directory mode 700), holding exactly: %s. Grok Bot"
        " creates none and has not checked whether one exists; dirun refuses"
        " to start, naming the problem, if it is missing or wrong. Do not"
        " start a Runtime for this store with --config (the Telegram adapter"
        " configuration): such a Runtime writes workspace trust beside its"
        " own state, where the Herdr it starts does not read it, so it would"
        " stop each approved workflow at dispatch. The per-user service"
        " installer (scripts/dirun-agent.sh) does not take a Runtime"
        " configuration."
        % (" ".join(shlex.quote(part) for part in command),
           shlex.quote(config),
           json.dumps(runtime_config_document(store_directory,
                                              control_repository))))


RUNTIME_NOTE = (
    "The workflow is AUTHORIZED and durably recorded. Grok Bot starts"
    " nothing: the workflow is advanced only by a DI Runtime (dirun) that"
    " is running against THIS workflow store. Read status for what the"
    " record shows and whether a process holds the Runtime lock here (which"
    " alone is not evidence of progress), and result for a verified result"
    " once the Broker records one. Delivery authority remains none.")

DISPLAY_HEADER = "DODGING INFINITY MISSION AUTHORIZATION (DI-REMOTE-2)"


def _refuse(problem, reason, **details):
    raise surface_module.LocalRequestRefusal(problem, reason, **details)


# Imported at CALL time, for the conforming route's own paths alone, as the
# gateway imports role_turn: ``codex_gateway.role_turn`` loads the DI-REMOTE
# protocol module and ``workflow_authority.store`` the protected-config
# module, both under ``telegram_operator``, which importing this transport
# must not load at startup (tests/test_grok_bot.py pins the loaded roots).
def _role_turn():
    from codex_gateway import role_turn
    return role_turn


def workflow_store():
    from workflow_authority import store
    return store


def classify_operator_reply(message):
    """The INITIAL Operator turn's reply: ``(REPLY_CLARIFICATION, quoted
    prose)`` or ``(REPLY_ROUTING, None)``; anything else is refused.

    The order is the guarantee. FIRST, on the raw reply, envelope presence
    is decided from column-0 lines alone (``framing.envelope_lines``). A
    column-0 envelope must be exactly ONE DI-REMOTE-2 routing signal
    (``role_turn.parse_routing_signal``); a second column-0 envelope, a
    malformed, unknown or conflicting envelope, any other kind, or the
    retired ``DI-GROKBOT-1 PROPOSAL`` is refused with its own code, never
    downgraded to prose and
    never neutralized into something displayable. The signal's body is
    discarded. ONLY a reply with no column-0 envelope is prose: bounded by
    ``framing.MAX_ENVELOPE_CHARS`` (refused, never truncated) and then quoted
    for display (``framing.quote_prose``). The prose is display-only: it
    proposes nothing and is never stored.
    """
    if not isinstance(message, str):
        _refuse(PROBLEM_OPERATOR_REPLY_EMPTY,
                "the Operator turn returned no reply; nothing was proposed")
    lines = framing.envelope_lines(message)
    if lines:
        if any(line.startswith(framing.MARKER_FAMILY) for line in lines):
            problem = "retired_grok_proposal_envelope"
        else:
            try:
                problem = _role_turn().parse_routing_signal(message)
            except RecursionError:
                # A decoder that recurses out on a deep envelope: refused,
                # never a crash.
                problem = "envelope_too_deep_to_parse"
        if problem is not None:
            _refuse(PROBLEM_OPERATOR_ENVELOPE,
                    "the Operator's reply carries a column-0 envelope that is"
                    " not the DI-REMOTE-2 routing signal (%s); it is refused,"
                    " never shown as a clarification, and nothing was"
                    " proposed" % problem, envelope_problem=problem)
        return REPLY_ROUTING, None
    if not message.strip():
        _refuse(PROBLEM_OPERATOR_REPLY_EMPTY,
                "the Operator turn returned an empty reply; nothing was"
                " proposed")
    if len(message) > framing.MAX_ENVELOPE_CHARS:
        _refuse(PROBLEM_OPERATOR_REPLY_TOO_LARGE,
                "the Operator's reply is %d characters; the hard bound is %d"
                " and it is refused, not truncated. Nothing was proposed"
                % (len(message), framing.MAX_ENVELOPE_CHARS))
    return REPLY_CLARIFICATION, framing.quote_prose(message)


def new_request_ref():
    return index_module.WORKFLOW_REF_PREFIX + secrets.token_hex(
        index_module.WORKFLOW_REF_HEX // 2)


def new_workflow_id():
    return "wf-" + secrets.token_hex(12)


def new_nonce():
    return secrets.token_hex(32)


def new_capability():
    """A withdrawal capability of the local request store's own shape, so
    the shared seal and digest apply unchanged."""
    return request_store.TOKEN_PREFIX + secrets.token_hex(
        request_store.TOKEN_HEX_CHARS // 2)


def capability_shape_ok(token):
    if not isinstance(token, str) or not token.startswith(
        request_store.TOKEN_PREFIX
    ):
        return False
    body = token[len(request_store.TOKEN_PREFIX):]
    return len(body) == request_store.TOKEN_HEX_CHARS and not set(
        body) - set("0123456789abcdef")


def relay_origin(request_ref):
    return {"transport": record_module.ORIGIN_TRANSPORT_GROK_BOT,
            "relay_ref": request_ref}


def binding_of(entry, request_ref):
    """The binding a reply must restate: exactly these five values."""
    return {
        "request_ref": request_ref,
        "workflow_id": entry["workflow_id"],
        "revision": entry["mission_authorization"]["revision"],
        "authorization_digest_sha256": (
            entry["mission_authorization"]["digest_sha256"]),
        "expires_at": entry["approval"]["expires_at"],
    }


def display_text(entry, binding):
    """The human-facing text: the binding, then the EXACT rendered Mission
    Authorization (byte for byte; its sha256 is the displayed digest),
    then fixed instructions. Every binding value is constrained by the
    record's validation (closed alphabets, integers, hex), and the
    rendered text is the single renderer's own output."""
    return "\n".join([
        DISPLAY_HEADER,
        "Request: %s" % binding["request_ref"],
        "Workflow: %s" % binding["workflow_id"],
        "Mission revision: %d" % binding["revision"],
        "Authorization digest (sha256 of the text below): %s"
        % binding["authorization_digest_sha256"],
        "Approval expires at (unix seconds): %d" % binding["expires_at"],
        "",
        "The exact Mission Authorization you are approving, byte for byte:",
        "",
        entry["mission_authorization"]["rendered_text"],
        "",
        "To approve exactly this Mission Authorization, first arm it on the"
        " DI machine: run the arming command presented with it in your local"
        " shell, approving that command only if every value in it matches"
        " this display. It prints a one-time approval code.",
        "Then reply with a separate message containing only: approved",
        "This approves engineering only: no commit, push, pull request,"
        " merge, release or deploy authority (delivery authority: none).",
        "Approval is operator-attested, not cryptographically authenticated.",
    ])


def is_pending(entry):
    approval = entry["approval"]
    return (entry["phase"] == record_module.PHASE_PLANNED
            and approval["consumed_at"] is None
            and not approval["superseded"])


def not_awaiting_remedy(entry):
    """What to do about a record that no longer awaits its first decision:
    an approved one is read with status and result; one that can never be
    approved needs a request that differs."""
    if entry["approval"]["consumed_at"] is not None:
        return ("It was already decided: read it with status and result.")
    return FRESH_REQUEST_REMEDY


def _is_grok_origin(entry):
    origin = entry["origin"]
    return origin is not None and origin["transport"] == (
        record_module.ORIGIN_TRANSPORT_GROK_BOT)


def void_expired_relay_workflows(workflows_document, now):
    """Void (supersede + BLOCKED) every grok-origin workflow that is still
    PLANNED with an approval neither consumed nor superseded whose expiry
    has PASSED against ``now``. Nothing else is touched: no Telegram-origin
    record, no still-valid approval, nothing consumed, superseded or past
    PLANNED. Such a record can never be approved again, and left PLANNED
    it would never leave the active set of the shared, capped store. The
    caller holds the store lock and SAVES the store. Returns the voided
    workflow ids."""
    voided = []
    for workflow_id, entry in sorted(workflows_document["workflows"].items()):
        if (
            _is_grok_origin(entry)
            and is_pending(entry)
            and now >= entry["approval"]["expires_at"]
        ):
            intake_module.void_workflow(entry)
            voided.append(workflow_id)
    return voided


# --- Read-only projection (I3, B-2 and B-3 item 5) --------------------------
#
# ``status`` and ``result`` read the workflow store WITHOUT its writer lock.
# Every save is an atomic replace (``workflow_authority.atomic``: temp file,
# fsync, ``os.replace``; the lock file is separate so the replace never
# invalidates a held descriptor), so a reader sees a complete old or a
# complete new file, never a torn one, and it never waits behind a writer:
# the Runtime holds that lock across a whole action, a fresh provider turn
# included. Atomic replace guarantees COMPLETENESS only, never freshness: the
# read is a complete durable SNAPSHOT that may precede a concurrent commit
# (by however long that writer's operation takes), labelled so, with
# ``read_at`` the time of the read, not a commit time. A malformed or
# unreadable store still fails closed (``StoreError``). Every WRITE path
# keeps the blocking exclusive lock.
#
# Nothing here imports a Runtime module: the names below are MIRRORED from
# ``target_runtime`` (and ``telegram_operator.state``) and pinned equal by
# test, so this transport's import closure never reaches the dispatch path.

RUNTIME_LOCK_FILE_NAME = "runtime.lock"
WRITER_LOCK_FILE_NAME = "workflows.lock"
LOCK_HELD = "held"
LOCK_UNHELD = "unheld"
LOCK_ABSENT = "absent"
LOCK_UNKNOWN = "unreadable_or_unknown"
# How many of the record's most recent receipts a status carries (the
# record holds at most 256, each summary at most 2000 characters).
MAX_STATUS_RECEIPTS = 8
# target_runtime.dispatch: the corrective follow-up bound, the dispatch
# receipt marker and the unresolved target-task sentinel.
MAX_FOLLOW_UP_DISPATCHES = 2
DISPATCH_RECEIPT_MARKER = "dispatched handoff revision"
UNRESOLVED_TASK_ID = "unknown"
# The receipts that carry a stop reason, by marker (target_runtime.broker
# VERIFICATION_BLOCK_MARKER / RECOVERY_BLOCK_MARKER, workspace_trust
# TRUST_BLOCK_RECEIPT_MARKER: "<marker>: <problem code>[ — <detail>]"; and
# readiness BOOTSTRAP_RECEIPT_MARKER with its BOOTSTRAP_FAILED state).
STOP_REASON_MARKERS = ("verification blocked", "recovery blocked",
                       "workspace trust not established")
BOOTSTRAP_RECEIPT_MARKER = "target bootstrap readiness"
BOOTSTRAP_FAILED = "bootstrap_failed"
RUNTIME_PHASES = (
    record_module.PHASE_AUTHORIZED, record_module.PHASE_WORKSPACE_READY,
    record_module.PHASE_PREPARED, record_module.PHASE_VALIDATED,
    record_module.PHASE_DISPATCHED, record_module.PHASE_VERIFIED)
STOP_PHASES = (record_module.PHASE_BLOCKED,
               record_module.PHASE_NEEDS_REAUTHORIZATION)

SNAPSHOT_STATEMENT = (
    "A COMPLETE snapshot of the durable workflow record, read without"
    " waiting for the store's writer lock: never a torn record, but it MAY"
    " PRECEDE a commit that was in progress at the read. read_at is the time"
    " of the read, an observation time, not the time of any commit. It"
    " states what the record held then and claims nothing about the"
    " present.")
RUNTIME_LOCK_STATEMENT = (
    "A HELD Runtime lock shows only that some process held the Runtime lock"
    " in this directory at the probe. It is not evidence that this workflow"
    " is advancing, and not evidence that its work is verified.")
WRITER_LOCK_STATEMENT = (
    "A HELD writer lock shows only that SOME PROCESS held the workflow"
    " store's writer lock at the probe; it does not show which process, or"
    " what it was doing. This read did not wait for it. It is not evidence"
    " of progress.")
PROBE_STATEMENT = (
    "Probed without blocking (one non-blocking exclusive attempt, released"
    " at once). When the lock is free the probe holds it for an instant, so"
    " a Runtime starting in exactly that instant would find it held and"
    " exit. Whether anything then starts that Runtime again is NOT promised"
    " here: it depends on how the Runtime is run (a configured service may"
    " restart it; a foreground run has no such recovery). Any probe error,"
    " releasing the probe's own hold included, is reported as"
    " unreadable_or_unknown, never as held.")
OBSERVATION_STATEMENT = (
    "This is the LAST RECORDED observation of the target, as recorded at"
    " recorded_at. The Runtime records an observation only when the"
    " observed pair changes, and no subsequent distinct observation is"
    " recorded. Its age bounds only what was last recorded: it proves"
    " neither present health nor present failure.")
UNOBSERVABLE_STATEMENT = (
    "At that recorded observation the target was UNOBSERVABLE (no task"
    " status and no completeness were recorded); that was its condition at"
    " that observation, not necessarily now.")
PROGRESS_STATEMENT = (
    "DI records no heartbeat. last_recorded_change_at is the latest time"
    " the record itself carries; a quiet wait writes nothing, so its age is"
    " not evidence of a stall, and a recent change is not evidence of"
    " present progress.")
NO_STOP_REASON_STATEMENT = (
    "The record carries no reason for this stop: several Runtime stops"
    " record none (their problem code reaches only the Runtime's own error"
    " output). Nothing is inferred in its place.")
VERIFICATION_STATEMENT = (
    "VERIFIED is decided by the Broker alone, over its conjunction of checks"
    " on fresh evidence. The verification turn's verified_result outcome is"
    " necessary, never sufficient, and Herdr lifecycle COMPLETE alone never"
    " produces it. The target's own canonical Reviewer APPROVE is"
    " TARGET-PRODUCED evidence that its review ran and concluded, never"
    " independent verification.")
RESULT_SOURCE_STATEMENT = (
    "Read directly from the durable workflow record, by pull. It does not"
    " depend on, wait for or use any Telegram result-delivery lane.")


# The errors a lock probe can meet: an ``OSError`` from the system call, or
# a ``ValueError`` (a path the OS cannot take). Each is reported as
# ``LOCK_UNKNOWN`` with its reason; none escapes a read-only status.
_PROBE_ERRORS = (OSError, ValueError)


def _error_text(exc):
    code = getattr(exc, "errno", None)
    if code is None:
        return type(exc).__name__
    return "%s: %s" % (type(exc).__name__, errno.errorcode.get(code, code))


def _attempt_lock(descriptor):
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        return LOCK_HELD, "another holder had the lock at the probe"
    except _PROBE_ERRORS as exc:
        return LOCK_UNKNOWN, ("the probe failed (%s); the lock's state is"
                              " unknown" % _error_text(exc))
    try:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
    except _PROBE_ERRORS as exc:
        return LOCK_UNKNOWN, ("the probe took the free lock but could not"
                              " release it (%s); the lock's state is unknown"
                              % _error_text(exc))
    return LOCK_UNHELD, "no process held the lock at the probe"


def _probe_open_file(descriptor):
    """Only a REGULAR file is probed for its lock. Anything else at the lock
    path (a FIFO, a directory, a device) is UNKNOWN: it is not a lock file,
    and it is neither locked nor read."""
    try:
        mode = os.fstat(descriptor).st_mode
    except _PROBE_ERRORS as exc:
        return LOCK_UNKNOWN, ("the lock file could not be examined (%s); its"
                              " state is unknown" % _error_text(exc))
    if not stat.S_ISREG(mode):
        return LOCK_UNKNOWN, ("the lock path is not a regular file (file type"
                              " %o); its state is unknown"
                              % stat.S_IFMT(mode))
    return _attempt_lock(descriptor)


def probe_lock(path):
    """``(state, detail)`` for one advisory lock file, WITHOUT blocking and
    without creating it: ``LOCK_HELD``, ``LOCK_UNHELD``, ``LOCK_ABSENT`` or
    ``LOCK_UNKNOWN``. TOTAL: it never raises. Never blocking includes the
    OPEN: it is non-blocking, because a blocking open of a FIFO waits for a
    writer forever; a FIFO (or any non-regular file) at the path is UNKNOWN,
    never locked and never read. Only a would-block answer is HELD; every
    other error (opening, examining, attempting, releasing the probe's own
    hold, or closing its descriptor) is UNKNOWN with its reason, never
    assumed held."""
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
    except FileNotFoundError:
        return LOCK_ABSENT, "no lock file exists at this path"
    except _PROBE_ERRORS as exc:
        return LOCK_UNKNOWN, ("the lock file could not be opened (%s); its"
                              " state is unknown" % _error_text(exc))
    state, detail = _probe_open_file(descriptor)
    try:
        os.close(descriptor)
    except _PROBE_ERRORS as exc:
        return LOCK_UNKNOWN, ("the probe could not close its descriptor (%s)"
                              " after finding: %s; the lock's state is"
                              " unknown" % (_error_text(exc), detail))
    return state, detail


def runtime_lock_status(store_directory, control_repository):
    """The Runtime lock beside this workflow store, probed without
    blocking. It shows a process holding a lock, never progress."""
    path = os.path.join(store_directory, RUNTIME_LOCK_FILE_NAME)
    state, detail = probe_lock(path)
    status = {"state": state, "path": path, "detail": detail,
              "statement": RUNTIME_LOCK_STATEMENT,
              "probe": PROBE_STATEMENT}
    if state != LOCK_HELD:
        status["remedy"] = runtime_recovery_remedy(store_directory,
                                                   control_repository)
    return status


def writer_lock_status(store_directory):
    path = os.path.join(store_directory, WRITER_LOCK_FILE_NAME)
    state, detail = probe_lock(path)
    return {"state": state, "path": path, "detail": detail,
            "statement": WRITER_LOCK_STATEMENT}


def dispatches_recorded(entry):
    """The dispatches the record holds (one receipt each, follow-ups
    included), exactly as ``target_runtime.dispatch.dispatch_count``
    counts them."""
    return sum(1 for receipt in entry["receipts"]
               if receipt["kind"] == "evidence"
               and receipt["bounded_summary"].startswith(
                   DISPATCH_RECEIPT_MARKER))


def stop_reason(entry):
    """The most recent receipt that carries a stop reason, or None."""
    for receipt in reversed(entry["receipts"]):
        summary = receipt["bounded_summary"]
        for marker in STOP_REASON_MARKERS:
            if summary.startswith(marker + ": "):
                code = summary[len(marker) + 2:].split(" \u2014 ", 1)[0]
                return {"marker": marker, "problem_code": code.strip(),
                        "summary": summary,
                        "recorded_at": receipt["recorded_at"]}
        failed = "%s: %s" % (BOOTSTRAP_RECEIPT_MARKER, BOOTSTRAP_FAILED)
        if summary.startswith(failed):
            return {"marker": BOOTSTRAP_RECEIPT_MARKER,
                    "problem_code": BOOTSTRAP_FAILED, "summary": summary,
                    "recorded_at": receipt["recorded_at"]}
    return None


def _timestamps(entry):
    approval, lease = entry["approval"], entry["workspace_lease"]
    found = [approval["created_at"], approval["consumed_at"]]
    if lease is not None:
        found += [lease["acquired_at"], lease["released_at"]]
    found += [receipt["recorded_at"] for receipt in entry["receipts"]]
    found += [turn["recorded_at"] for turn in entry["codex_turns"]]
    for key, field in (("target_engine", "dispatched_at"),
                       ("last_observation", "observed_at"),
                       ("verified_result", "recorded_at")):
        if entry[key] is not None:
            found.append(entry[key][field])
    return [value for value in found if value is not None]


def workflow_projection(entry, now):
    """What the durable record states, attributed to the record: never a
    claim about the present target, the Runtime, or progress."""
    roles = {}
    for turn in entry["codex_turns"]:
        roles[turn["role"]] = roles.get(turn["role"], 0) + 1
    dispatches = dispatches_recorded(entry)
    engine = entry["target_engine"]
    observation = entry["last_observation"]
    result_record = entry["verified_result"]
    projection = {
        "receipts": {
            "count": len(entry["receipts"]),
            "most_recent": [
                {"kind": receipt["kind"], "turn_id": receipt["turn_id"],
                 "recorded_at": receipt["recorded_at"],
                 "summary": receipt["bounded_summary"]}
                for receipt in entry["receipts"][-MAX_STATUS_RECEIPTS:]],
        },
        "codex_turns": {"count": len(entry["codex_turns"]),
                        "by_role": roles},
        "dispatches": {
            "recorded": dispatches,
            "follow_ups_recorded": max(0, dispatches - 1),
            "follow_up_bound": MAX_FOLLOW_UP_DISPATCHES,
            "follow_up_bound_reached": (
                dispatches - 1 >= MAX_FOLLOW_UP_DISPATCHES),
        },
        "target_engine": None if engine is None else {
            "task_id": engine["task_id"],
            "dispatched_at": engine["dispatched_at"],
            "identity_resolved": engine["task_id"] != UNRESOLVED_TASK_ID,
        },
        "last_recorded_observation": None,
        # The Broker's recorded result, as the record holds it (read it whole
        # with result); None when none is recorded.
        "verified_result": None if result_record is None else {
            "recorded_at": result_record["recorded_at"],
            "digest_sha256": result_record["digest"]},
        "last_recorded_change_at": max(_timestamps(entry)),
        "progress_statement": PROGRESS_STATEMENT,
        "stop": None,
    }
    if observation is not None:
        unobservable = (observation["task_status"] is None
                        and observation["completeness"] is None)
        projection["last_recorded_observation"] = {
            "task_status": observation["task_status"],
            "completeness": observation["completeness"],
            "recorded_at": observation["observed_at"],
            "age_at_read_seconds": max(0, now - observation["observed_at"]),
            "unobservable_at_that_observation": unobservable,
            "statement": OBSERVATION_STATEMENT + (
                " " + UNOBSERVABLE_STATEMENT if unobservable else ""),
        }
    if entry["phase"] in STOP_PHASES or entry["approval"]["superseded"]:
        reason = stop_reason(entry)
        projection["stop"] = {
            "phase": entry["phase"],
            "superseded": entry["approval"]["superseded"],
            "reason_recorded": reason is not None,
            "reason": reason,
            "statement": None if reason is not None else (
                NO_STOP_REASON_STATEMENT),
        }
    return projection


def next_step(entry, now, runtime_lock_state, store_directory,
              control_repository):
    """What the human can do next, accurate for this record."""
    phase = entry["phase"]
    if phase == record_module.PHASE_PLANNED:
        if is_pending(entry) and now < entry["approval"]["expires_at"]:
            return ("Not yet approved: call present with this request_ref,"
                    " show the human its display_text whole, arm it locally"
                    " with the arming command, then relay the reply with"
                    " approve.")
        return ("This Mission Authorization can no longer be approved. "
                + FRESH_REQUEST_REMEDY)
    if phase in RUNTIME_PHASES:
        text = ("Approved. Grok Bot starts nothing: only a DI Runtime"
                " running against this workflow store advances it. Read"
                " status again for what the record shows.")
        if runtime_lock_state != LOCK_HELD:
            text += " " + runtime_recovery_remedy(store_directory,
                                                  control_repository)
        return text
    if phase == record_module.PHASE_COMPLETED:
        return "Completed. Read the verified result with result."
    if phase == record_module.PHASE_NEEDS_REAUTHORIZATION:
        return ("This workflow needs a new human authorization and is never"
                " resumed. " + FRESH_REQUEST_REMEDY)
    return ("This workflow stopped and is never resumed. "
            + FRESH_REQUEST_REMEDY)


class WorkflowRoute(object):
    """The conforming route's durable side: the configured control
    repository the planning turn is rooted at and every document is pinned
    to, and the workflow store the Runtime claims from."""

    def __init__(self, workflow_store, control_repository,
                 planning_session=None, workflow_id_factory=None,
                 nonce_factory=None, ref_factory=None,
                 capability_factory=None):
        self.store = workflow_store
        self.control_repository = control_repository
        # The planning turn's OperatorSession: production builds the
        # restricted planning session, whose provider entry point is
        # resolved on the gateway module at call time.
        self.planning_session = (planning_session
                                 or RestrictedPlanningOperatorSession())
        self._workflow_id = workflow_id_factory or new_workflow_id
        self._nonce = nonce_factory or new_nonce
        self._ref = ref_factory or new_request_ref
        self._capability = capability_factory or new_capability

    # -- the trusted stamping seam -----------------------------------------

    def plan(self, text, index, now):
        """Run the SEPARATE fresh planning turn (after the initial turn's
        routing signal) and build the stamped PLANNED record. The turn goes
        through the planning session: ``prepare`` builds the request (the
        human's exact text, rooted at the control repository) and
        ``execute`` makes its one provider call with that same request.
        Returns ``(entry, request_ref)``; persists nothing. The caller
        validated ``text`` (non-empty, bounded, no lone surrogate)."""
        request_ref = self._fresh_ref(index)
        try:
            prepared = self.planning_session.prepare(
                text, self.control_repository, session_id=None,
                source=framing.TRANSPORT)
            result = self.planning_session.execute(prepared)
        except Exception as exc:
            _refuse(PROBLEM_PLANNING_FAILED,
                    "the fresh planning turn raised %s; nothing was"
                    " recorded" % type(exc).__name__)
        if result.status != _role_turn().ROLE_TURN_COMPLETED:
            detail = getattr(result.error, "detail", None)
            _refuse(PROBLEM_PLANNING_FAILED,
                    "the fresh planning turn did not complete (%s: %s);"
                    " nothing was recorded" % (result.status, result.reason),
                    planning_status=result.status,
                    planning_reason=result.reason,
                    planning_detail=detail)
        try:
            body, problem = _role_turn().parse_planning_response(
                result.message)
        except RecursionError:
            body, problem = None, "envelope_too_deep_to_parse"
        if problem is not None:
            _refuse(PROBLEM_PLANNING_ENVELOPE,
                    "the fresh planning turn did not return a valid Mission"
                    " Authorization envelope (%s); nothing was recorded"
                    % problem, envelope_problem=problem)
        try:
            document = intake_module.validate_mission_document(
                body, self.control_repository)
        except (intake_module.MissionError, AuthorizationError) as exc:
            _refuse(exc.problem,
                    "the Mission Authorization failed validation: %s;"
                    " nothing was recorded" % exc)
        except RecursionError:
            _refuse(PROBLEM_PLANNING_ENVELOPE,
                    "the Mission Authorization nests too deeply to decode;"
                    " nothing was recorded",
                    envelope_problem="document_too_deep_to_decode")
        try:
            entry = intake_module.build_workflow_record(
                document, text, now=now, workflow_id=self._workflow_id(),
                nonce=self._nonce(), origin=relay_origin(request_ref))
            if result.turn is not None:
                entry["codex_turns"] = [dict(result.turn)]
            record_module.validate_record(entry)
        except record_module.RecordError as exc:
            _refuse(exc.problem,
                    "the Mission Authorization exceeds a hard bound or"
                    " fails the record schema: %s; it was refused, not"
                    " truncated, and nothing was recorded" % exc)
        except UnicodeError:
            _refuse(PROBLEM_UNENCODABLE,
                    "the Mission Authorization carries text that cannot be"
                    " encoded (a lone surrogate); nothing was recorded")
        return entry, request_ref

    def _fresh_ref(self, index):
        for _ in range(8):
            ref = self._ref()
            if index_module.is_workflow_ref(ref) and (
                index.workflow_request(ref) is None
            ):
                return ref
        _refuse(PROBLEM_STORE_REFUSED,
                "no unused request_ref could be minted; nothing was run")

    def record(self, entry, now):
        """Save the stamped record PLANNED, first voiding expired grok-origin
        pending records (``void_expired_relay_workflows``). Returns the
        voided ids."""
        with workflow_store().exclusive_store_lock(self.store.directory):
            document = self.store.load()
            voided = void_expired_relay_workflows(document, now)
            ok, problem, pruned = workflow_store().add_workflow(document, entry)
            if not ok:
                _refuse(PROBLEM_STORE_REFUSED,
                        "the workflow store refused the record (%s; %d"
                        " terminal records pruned); nothing was recorded"
                        % (problem, pruned), store_problem=problem)
            self.store.save(document)
        return voided

    def mint_capability(self):
        """The request's one-shot withdrawal capability: returned once, in
        the request reply; only its digest is kept."""
        return self._capability()

    def void_one(self, workflow_id):
        """Best-effort durable void of a record this route just saved but
        could not index (so it never stays an orphan pending approval)."""
        with workflow_store().exclusive_store_lock(self.store.directory):
            document = self.store.load()
            entry = document["workflows"].get(workflow_id)
            if entry is not None and is_pending(entry):
                intake_module.void_workflow(entry)
                self.store.save(document)

    # -- reads ---------------------------------------------------------------

    def entry(self, request_ref, held):
        """The record ``held`` names for ``request_ref``, checked to be this
        request's own (its origin names this request_ref)."""
        with workflow_store().exclusive_store_lock(self.store.directory):
            document = self.store.load()
        return self._own(document, request_ref, held)

    @staticmethod
    def _own(document, request_ref, held):
        entry = document["workflows"].get(held["workflow_id"])
        if entry is None:
            _refuse(PROBLEM_WORKFLOW_MISSING,
                    "request %s's workflow %s is not in the workflow store (a"
                    " terminal record may have been pruned at the store's"
                    " cap); nothing was changed"
                    % (request_ref, held["workflow_id"]),
                    request_ref=request_ref, workflow_id=held["workflow_id"])
        if entry["origin"] != relay_origin(request_ref):
            _refuse(PROBLEM_WORKFLOW_MISSING,
                    "workflow %s does not originate from request %s; nothing"
                    " was changed" % (held["workflow_id"], request_ref),
                    request_ref=request_ref)
        return entry

    def snapshot_entry(self, request_ref, held):
        """The record ``held`` names, read WITHOUT the store's writer lock
        (READ-ONLY paths only: see the projection section above). Fails
        closed on a malformed or unreadable store."""
        return self._own(self.store.load(), request_ref, held)

    def status(self, request_ref, held, now):
        """READ-ONLY: starts, advances and consumes nothing, creates no
        file, and never waits for a writer."""
        entry = self.snapshot_entry(request_ref, held)
        approval = entry["approval"]
        target = entry["target"]
        runtime_lock = runtime_lock_status(self.store.directory,
                                           self.control_repository)
        return {
            "ok": True, "status": "workflow_status", "lane": LANE_WORKFLOW,
            "request_ref": request_ref, "workflow_id": entry["workflow_id"],
            "phase": entry["phase"],
            "awaiting_approval": is_pending(entry),
            "approval": {"expires_at": approval["expires_at"],
                         "consumed": approval["consumed_at"] is not None,
                         "decision": approval["decision"],
                         "superseded": approval["superseded"]},
            "target": {"canonical_url": target["canonical_url"],
                       "issue_or_pr": target["issue_or_pr"]},
            "authorization_digest_sha256": (
                entry["mission_authorization"]["digest_sha256"]),
            "snapshot": {"read_at": now, "statement": SNAPSHOT_STATEMENT,
                         "writer_lock": writer_lock_status(
                             self.store.directory)},
            "record": workflow_projection(entry, now),
            "runtime_lock": runtime_lock,
            "verification_statement": VERIFICATION_STATEMENT,
            "next": next_step(entry, now, runtime_lock["state"],
                              self.store.directory, self.control_repository),
        }

    def result(self, request_ref, held, now):
        """READ-ONLY: the verified result exactly as the Broker recorded it,
        by pull from the durable record, or a statement that none is."""
        entry = self.snapshot_entry(request_ref, held)
        result_record = entry["verified_result"]
        phase = entry["phase"]
        result = {
            "ok": True, "status": "workflow_result", "lane": LANE_WORKFLOW,
            "request_ref": request_ref, "workflow_id": entry["workflow_id"],
            "phase": phase,
            "snapshot": {"read_at": now, "statement": SNAPSHOT_STATEMENT},
            "verified_result": None if result_record is None else {
                "summary": result_record["summary"],
                "digest_sha256": result_record["digest"],
                "recorded_at": result_record["recorded_at"]},
            "verification_statement": VERIFICATION_STATEMENT,
            "source": RESULT_SOURCE_STATEMENT,
        }
        if result_record is None:
            result["note"] = (
                "No verified result is recorded for this workflow (phase"
                " %s): only the Broker records one, when every verification"
                " check passes. Read status for what the record shows."
                % phase)
        elif phase == record_module.PHASE_VERIFIED:
            result["note"] = ("Recorded when the Broker moved the workflow"
                              " to VERIFIED; it is not yet recorded"
                              " COMPLETED.")
        elif phase == record_module.PHASE_BLOCKED:
            result["note"] = ("Recorded when the Broker moved the workflow"
                              " to VERIFIED; the workflow later stopped"
                              " BLOCKED (read status).")
        return result

    # -- approval -------------------------------------------------------------

    @staticmethod
    def _check_approvable(entry, request_ref, shown, now):
        current = binding_of(entry, request_ref)
        changed = sorted(name for name in index_module.WORKFLOW_BINDING_KEYS
                         if current[name] != shown[name])
        if changed:
            _refuse(PROBLEM_STALE_BINDING,
                    "the workflow no longer carries the binding displayed for"
                    " request %s (%s changed); nothing was recorded. Present"
                    " it again" % (request_ref, ", ".join(changed)),
                    fields=changed)
        approval = entry["approval"]
        if approval["superseded"]:
            _refuse(PROBLEM_SUPERSEDED,
                    "the Mission Authorization for request %s was superseded"
                    " (withdrawn, or voided after its expiry); it can never be"
                    " approved. %s Nothing was recorded"
                    % (request_ref, FRESH_REQUEST_REMEDY))
        if approval["consumed_at"] is not None:
            _refuse(PROBLEM_ALREADY_CONSUMED,
                    "the Mission Authorization for request %s was already"
                    " decided (one-shot); a replay applies nothing" %
                    request_ref, phase=entry["phase"])
        if entry["phase"] != record_module.PHASE_PLANNED:
            _refuse(PROBLEM_NOT_AWAITING,
                    "workflow %s is %s, not awaiting approval; nothing was"
                    " recorded" % (entry["workflow_id"], entry["phase"]),
                    phase=entry["phase"])
        if now >= approval["expires_at"]:
            _refuse(PROBLEM_EXPIRED,
                    "the Mission Authorization for request %s expired at %d;"
                    " it can never be approved. %s Nothing was recorded"
                    % (request_ref, approval["expires_at"],
                       FRESH_REQUEST_REMEDY))

    def check_approvable(self, request_ref, held, shown, now):
        """Every record-side precondition, read-only, BEFORE the arming is
        fired."""
        self._check_approvable(self.entry(request_ref, held), request_ref,
                               shown, now)

    def consume(self, request_ref, held, shown, now):
        """One-shot consumption and PLANNED -> AUTHORIZED, re-checked under
        the store lock and durably saved. The approval block is not widened:
        ``consumed_by_update_id`` stays null (a relay has no Telegram
        update)."""
        with workflow_store().exclusive_store_lock(self.store.directory):
            document = self.store.load()
            entry = self._own(document, request_ref, held)
            self._check_approvable(entry, request_ref, shown, now)
            approval = entry["approval"]
            approval["consumed_at"] = now
            approval["decision"] = record_module.DECISION_APPROVE
            record_module.apply_transition(entry,
                                           record_module.PHASE_AUTHORIZED)
            self.store.save(document)
        return {
            "ok": True, "status": "authorized", "lane": LANE_WORKFLOW,
            "request_ref": request_ref, "workflow_id": entry["workflow_id"],
            "phase": entry["phase"],
            "authorization_digest_sha256": shown["authorization_digest_sha256"],
            "runtime": RUNTIME_NOTE,
        }

    # -- withdrawal -------------------------------------------------------------

    def withdraw(self, request_ref, held, capability, now):
        """Pending withdrawal, proven by the request's own capability. A
        consumed approval is refused by name: no running work is stopped,
        because nothing here can stop it."""
        if not capability_shape_ok(capability) or not hmac.compare_digest(
            request_store.token_digest(capability).encode("ascii"),
            held["capability_sha256"].encode("ascii"),
        ):
            _refuse(PROBLEM_CAPABILITY_REFUSED,
                    "cancel of request %s refused: the capability is not this"
                    " request's own; knowing a request_ref or a workflow id"
                    " never permits control" % request_ref,
                    request_ref=request_ref)
        if now >= held["capability_expires_at"]:
            _refuse(PROBLEM_CAPABILITY_REFUSED,
                    "cancel of request %s refused: its withdrawal capability"
                    " expired at %d; nothing was cancelled"
                    % (request_ref, held["capability_expires_at"]),
                    request_ref=request_ref)
        with workflow_store().exclusive_store_lock(self.store.directory):
            document = self.store.load()
            entry = self._own(document, request_ref, held)
            approval = entry["approval"]
            if approval["consumed_at"] is not None:
                _refuse(PROBLEM_RUNNING_CANCEL,
                        "request %s's Mission Authorization was already"
                        " decided (%s at %s); the workflow is %s and its work"
                        " may be running under the Runtime. There is no"
                        " supported way to suspend or stop running work from"
                        " here, so NOTHING was cancelled, stopped or changed,"
                        " and no quiescence is claimed. Delivery authority"
                        " remains none." % (request_ref, approval["decision"],
                                            approval["consumed_at"],
                                            entry["phase"]),
                        cancellation_kind=CANCELLATION_RUNNING,
                        phase=entry["phase"], request_ref=request_ref)
            if not is_pending(entry):
                _refuse(PROBLEM_NOT_PENDING,
                        "request %s is no longer pending (phase %s, superseded"
                        " %s): it was already withdrawn or voided after its"
                        " expiry; nothing was changed"
                        % (request_ref, entry["phase"], approval["superseded"]),
                        phase=entry["phase"], request_ref=request_ref)
            intake_module.void_workflow(entry)
            self.store.save(document)
        return {
            "ok": True, "status": "withdrawn", "lane": LANE_WORKFLOW,
            "cancellation_kind": CANCELLATION_PENDING,
            "request_ref": request_ref, "workflow_id": entry["workflow_id"],
            "phase": entry["phase"], "superseded": True,
            "running_work_stopped": False,
            "note": "The pending Mission Authorization was withdrawn before"
                    " any decision: it can never be approved, and nothing had"
                    " started.",
        }
