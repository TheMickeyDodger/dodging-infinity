"""The Runtime-owned VERIFICATION PRODUCER (Task 8, slice S-VI; ledger
R2-9).

It runs the Mission's APPROVED verification argv (the proposal's
``verification`` key the human approved) in the leased workspace, under
the owned-process rules every production spawn here follows: the scope is
ASSIGNED to the workflow before the spawn (``process_ownership
.assign_scope``), the child is started through ``spawn_owned`` (its own
session, a durable pending record before it exists, a child-side stamp),
and its whole group is reaped through ``reap_owned`` on every exit path —
so no descendant survives unrecorded, and a group that could not be
settled is RECORDED as unsettled rather than reported clean.

What it captures is what actually happened, never a narrative: the
complete stdout+stderr log bytes (streamed to a file, then stored under
their own sha256), the argv exactly as run, the real exit status, the
wall-clock start and finish from the Runtime's clock and the measured
duration from a monotonic clock. The record binds the exact candidate
identity and baseline the CALLER captured before the run (and re-proves
after it), and is stored under the digest of its canonical bytes in the
shared artifact directory (``mission_control.delivery_artifacts``, the one
format module). A nonzero exit is recorded truthfully too — the delivery
layer refuses it; this module never decides.

Deadline posture: like every engineering child here, no deadline — a
verification can legitimately run long, and a timeout would be a silent
truncation of evidence (the engine-timeout limitation is recorded).
"""

import os
import secrets
import stat
import time

from mission_control import delivery_artifacts as artifacts
from target_runtime import process_ownership
from workflow_authority.atomic import READ_ABSENT, classify_missing

VERIFICATION_OWNER_UNIT = "verification"
VERIFICATION_LABEL = "mission-verification"
# The reap settle bound of the verification group (local cleanup of a
# process this component started, never a deadline on the verification).
VERIFICATION_REAP_SETTLE_SECONDS = 10.0

#: What the verification scope's OWNERSHIP RECORDS say about any earlier
#: attempt (Task 8 R19-3, ``prior_ownership``).
PRIOR_CLEAR = "clear"
PRIOR_UNRESOLVED = "unresolved"
PRIOR_UNAVAILABLE = "unavailable"


#: The largest process-group id a record may name (``pid_t`` is a signed
#: 32-bit integer); a larger number is not a group id.
MAX_GROUP_ID = 2 ** 31 - 1


def _group_id(raw):
    """The process-group id an owned root's group record names — ASCII
    digits only (``spawn_stamp`` writes ``str(group)``), in ``2 ..
    MAX_GROUP_ID`` — or None for anything else: undecodable bytes, a
    non-ASCII digit ``str.isdigit`` would admit, an empty record, an id no
    process group can hold. Never raises."""
    text = raw.strip()
    if not text or len(text) > len(str(MAX_GROUP_ID)) or not all(
            48 <= byte <= 57 for byte in text):
        return None
    value = int(text)
    return value if 1 < value <= MAX_GROUP_ID else None


def _scope_roots(workflow_id, control_identity, scope_base=None):
    """Every owned root of this workflow's verification scope, read
    STRICTLY (see ``prior_ownership``): ``([(name, directory, pgid or
    None), ...], None)`` — None for a root no group was ever stamped into —
    or ``(None, (state, detail))`` when absent (``PRIOR_CLEAR``, no roots)
    or when the evidence cannot be read (``PRIOR_UNAVAILABLE``)."""
    scope = process_ownership.owner_scope(
        process_ownership.OWNER_TYPE_WORKFLOW, control_identity, workflow_id,
        VERIFICATION_OWNER_UNIT, base=scope_base)
    prefix = process_ownership.owned_root_base(scope)
    for label, path in (("scope", scope), ("owned-root prefix", prefix)):
        try:
            mode = os.lstat(path).st_mode
        except FileNotFoundError:
            # Task 8 R25-2: ``lstat`` raises FileNotFoundError THROUGH a
            # dangling ANCESTOR too. CLEAR — which lets the Broker settle a
            # start-unknown attempt and admit another claim — only when the
            # traversal establishes GENUINE absence; otherwise UNAVAILABLE.
            missing = classify_missing(path)
            if missing.availability != READ_ABSENT:
                return None, (PRIOR_UNAVAILABLE, "its %s cannot be examined (%s)" % (
                    label, missing.problem))
            return [], (PRIOR_CLEAR, "no verification scope exists" if label == "scope"
                        else "the scope holds no owned roots")
        except OSError as exc:
            return None, (PRIOR_UNAVAILABLE, "its %s cannot be examined (%s)" % (
                label, exc.__class__.__name__))
        if not stat.S_ISDIR(mode):
            return None, (PRIOR_UNAVAILABLE, "its %s is not a directory" % label)
        if label == "scope":
            identity, reason = process_ownership.validate_assignment(
                scope, base=scope_base)
            if identity is None:
                return None, (PRIOR_UNAVAILABLE,
                              "its assignment does not validate (%s)" % reason)
    try:
        names = sorted(os.listdir(prefix))
    except OSError as exc:
        return None, (PRIOR_UNAVAILABLE, "its owned roots cannot be listed (%s)" % (
            exc.__class__.__name__))
    # Task 8 R27-1: the scope's owner ledger — genuinely absent says nothing;
    # PRESENT but NOT OBSERVED is unavailable once a root is stamped; readable
    # and CONTRADICTORY is unavailable too.
    groups, ledger_gap = process_ownership.ledger_groups(scope)
    roots = []
    for name in names:
        directory = os.path.join(prefix, name)
        try:
            mode = os.lstat(directory).st_mode
        except OSError as exc:
            return None, (PRIOR_UNAVAILABLE, "owned root %s cannot be examined (%s)" % (
                name, exc.__class__.__name__))
        if stat.S_ISREG(mode):
            continue              # not a root (the prefix's freeze file, for one)
        if not stat.S_ISDIR(mode):
            return None, (PRIOR_UNAVAILABLE,
                          "owned-root entry %s is neither a root directory nor a file" % name)
        record = os.path.join(directory, process_ownership.OWNED_ROOT_PGID_FILE)
        try:
            # Task 8 R26-1: non-waiting, descriptor-validated, bounded.
            raw = process_ownership.read_ownership_record(record)
        except FileNotFoundError:
            # Task 8 R25-2 (the same reader): never stamped only when the
            # record is GENUINELY missing; a dangling record is unavailable.
            missing = classify_missing(record)
            if missing.availability != READ_ABSENT:
                return None, (PRIOR_UNAVAILABLE,
                              "owned root %s's group record cannot be read (%s)" % (
                                  name, missing.problem))
            roots.append((name, directory, None))
            continue
        except OSError as exc:
            return None, (PRIOR_UNAVAILABLE,
                          "owned root %s's group record cannot be read (%s)" % (
                              name, exc.__class__.__name__))
        pgid = _group_id(raw)
        if pgid is None:
            return None, (PRIOR_UNAVAILABLE,
                          "owned root %s's group record is not a group id" % name)
        if ledger_gap is not None:
            return None, (PRIOR_UNAVAILABLE,
                          "owned root %s cannot be checked against its owner ledger (%s)"
                          % (name, ledger_gap[1]))
        if process_ownership.ledger_contradicts(groups, directory, pgid):
            return None, (PRIOR_UNAVAILABLE,
                          "owned root %s's group record is contradicted by its owner"
                          " ledger" % name)
        roots.append((name, directory, pgid))
    return roots, None


def owned_root_count(workflow_id, control_identity, scope_base=None):
    """How many owned roots this workflow's verification scope holds, read
    strictly: ``(count, None)``, or ``(None, detail)`` when the evidence
    cannot be read. A root is created BEFORE its spawn's process exists
    (``create_owned_root``), so an attempt that added none started no
    process."""
    roots, problem = _scope_roots(workflow_id, control_identity, scope_base)
    if roots is None:
        return None, problem[1]
    return len(roots), None


def prior_ownership(workflow_id, control_identity, scope_base=None):
    """Whether a verification process of this workflow may still be alive,
    READ from the existing process-ownership records of its verification
    scope — the assignment (``validate_assignment``), the owned roots and
    their recorded groups (``owned_roots``, ``group_is_ours``) — and never
    acted on: nothing is signalled, reaped or removed here. Recovering an
    owner-dead scope is the Runtime's startup recovery
    (``runtime.recover_inherited_processes``) under the same assignment.
    Returns ``(state, detail)``:

    - ``PRIOR_CLEAR``: no scope exists (nothing was ever spawned under
      it), or every owned root in it is stamped and its recorded group is
      gone — or its id now names a DIFFERENT leader (reused: the recorded
      group is gone);
    - ``PRIOR_UNRESOLVED``: a recorded group is alive and corroborated as
      this verification's (its owner died while it ran, or it survived
      the reap); or alive with its recorded leader gone (surviving
      descendants whose ownership cannot be corroborated — never signalled
      and never read as gone); or a root was never stamped (a spawn whose
      group was never recorded);
    - ``PRIOR_UNAVAILABLE``: the evidence cannot be read — the scope, its
      owned-root prefix, a root or its group record cannot be examined
      (any error other than "no such file"), a path is not what the
      records say it is, or the assignment is missing, malformed, forged
      or conflicting.

    ABSENT IS NOT INACCESSIBLE: the scope, the prefix, each root and its
    group record are examined with calls that RAISE (``lstat``,
    ``listdir``, ``open``) rather than the boolean predicates
    (``lexists``, ``isdir``, ``exists``) that turn a permission or I/O
    error into "not there" — ``FileNotFoundError`` alone reads as absent,
    and (Task 8 R25-2) only when the traversal (``classify_missing``)
    confirms it: ``lstat`` raises it through a dangling ANCESTOR too, which
    is UNAVAILABLE.
    ``process_ownership.owned_roots`` is not used for that reason (its
    ``isdir`` guards map an unreadable prefix to "no roots"); the
    records, their names and the per-group predicates are its own.

    Task 8 R27-1: ``PRIOR_CLEAR``'s "gone" and "reused" readings rest on a
    record a stamp wrote WHOLE: the stamp writer replaces each record
    atomically (``spawn_stamp._write_record``), so a failed parent
    confirmation cannot leave a fragment (``446`` of ``44603``, a cut start
    time) that reads as a different, gone group. For records an earlier,
    truncating writer may have left, the scope's owner ledger is read
    (``process_ownership.ledger_groups``) in three branches never collapsed:
    genuinely ABSENT (no ledger, no row for the spawn) says nothing; PRESENT
    but NOT OBSERVED is ``PRIOR_UNAVAILABLE`` once a root is stamped — never
    CLEAR while the observation is unavailable; READABLE and naming a
    DIFFERENT group for a root's spawn is ``PRIOR_UNAVAILABLE``
    (``ledger_contradicts``). A live leader whose recorded start is cut short
    is ``PRIOR_UNRESOLVED`` (``UNCORROBORATED_START_FRAGMENT``). The stated
    residual: a legacy group-record fragment whose ledger is genuinely absent,
    or has no row for its spawn, is read as it is."""
    roots, problem = _scope_roots(workflow_id, control_identity, scope_base)
    if problem is not None:
        return problem
    for name, directory, pgid in roots:
        if pgid is None:
            return PRIOR_UNRESOLVED, (
                "owned root %s was never stamped with a process group" % name)
    for name, directory, pgid in roots:
        if not process_ownership._group_alive(pgid):
            continue
        try:
            ours, why = process_ownership.group_is_ours(directory)
        except (OSError, ValueError) as exc:    # e.g. an undecodable nonce or start record
            return PRIOR_UNAVAILABLE, (
                "owned root %s's corroboration records cannot be read (%s)" % (
                    name, exc.__class__.__name__))
        if ours is not None:
            return PRIOR_UNRESOLVED, (
                "group %d of owned root %s is alive and corroborated as this"
                " verification's" % (pgid, name))
        if why == process_ownership.UNCORROBORATED_START_MISMATCH:
            continue
        if why in process_ownership.LEADER_UNAVAILABLE_REASONS:
            # Task 8 R22-1: the leader's start time could not be read (and,
            # for one reason, nor whether it exists) — unavailable, never a
            # gone leader. The GROUP is observed alive; the leader's liveness
            # is not claimed here.
            return PRIOR_UNAVAILABLE, (
                "group %d of owned root %s is alive and its leader's start time"
                " cannot be read" % (pgid, name))
        return PRIOR_UNRESOLVED, (
            "group %d of owned root %s is alive and its ownership cannot be"
            " corroborated (%s)" % (pgid, name, why or "its recorded leader is gone"))
    return PRIOR_CLEAR, "%d owned root(s), every recorded group gone" % len(roots)


class VerificationStartUnknown(Exception):
    """The SPAWN itself raised (Task 8 R19): ``spawn_owned`` records the
    group AFTER ``Popen`` created the child, so an exception out of it —
    other than ``SpawnGated``, which is raised before any record or process
    — does not prove that no process started. The cause is chained."""


class VerificationOutcomeUnknown(Exception):
    """POST-START (Task 8 R19): the verification STARTED and waiting for it
    failed, so its exit status is unknown. ``settlement`` is what its reap
    established; ``reap_error`` names a reap that failed too — the WAIT's
    failure is the chained cause, never replaced by the reap's."""

    def __init__(self, settlement, reap_error=None):
        super(VerificationOutcomeUnknown, self).__init__(settlement, reap_error)
        self.settlement = settlement
        self.reap_error = reap_error


class VerificationUnrecorded(Exception):
    """POST-START (Task 8 R19): the verification RAN — spawned, waited to
    its exit status, its reap attempted — and recording its result failed
    (closing its log, reading the clocks, adopting the log, storing the
    record). ``exit_status`` and ``settlement`` are what was observed; the
    cause is chained. The run is never re-run in its place."""

    def __init__(self, exit_status, settlement):
        super(VerificationUnrecorded, self).__init__(exit_status, settlement)
        self.exit_status = exit_status
        self.settlement = settlement


def _close_quietly(*handles):
    """Close each handle; NEVER raises — returns the first exception, so
    closing cannot replace a cause already in flight."""
    problem = None
    for handle in handles:
        try:
            handle.close()
        except Exception as exc:                       # noqa: BLE001
            problem = problem or exc
    return problem


def produce(argv, lease_path, workflow_id, mission_id, mission_revision,
            control_identity, candidate_identity, base_oid,
            workflow_store_directory, clock, monotonic=None, scope_base=None):
    """Run the approved argv once in ``lease_path`` and return ``(digest,
    record)`` of the stored verification record; a run that fails is a
    record. ``scope_base`` is the ownership scope base (None: the production
    default; a hermetic test passes its own).

    What it raises is classified by the PHASE REACHED (Task 8 R19), never
    by exception type alone: before the spawn is called (the scope
    assignment, the artifact directory, the log file) and ``SpawnGated``
    (raised by ``spawn_owned`` before any record or process) propagate as
    themselves — PROVEN pre-start, nothing ran; anything else out of the
    spawn raises ``VerificationStartUnknown`` — a child may exist. (Task 8
    R26: that includes ``process_ownership.SpawnUnconfirmed`` — a child
    STARTED and the parent could not confirm its stamp. It is reported
    start-unknown with the rest; the started process and its root stay
    reachable through the chained cause, and no direct wait or reap runs on
    this path: the next pass decides from the owned roots, and a live group
    is settled, if at all, by recovery.) Once the
    spawn returned, EVERYTHING is post-start: a failed wait raises
    ``VerificationOutcomeUnknown`` (the reap still runs, and never replaces
    the wait's cause); a reap that raises leaves the run ``unsettled``, as
    a refused reap does; any later failure — closing the log, the clocks,
    adopting the log, storing the record — raises
    ``VerificationUnrecorded``. A process-level interruption (not an
    ``Exception``) propagates as itself, after the reap."""
    monotonic = monotonic or time.monotonic
    scope = process_ownership.assign_scope(
        process_ownership.OWNER_TYPE_WORKFLOW, control_identity, workflow_id,
        VERIFICATION_OWNER_UNIT, base=scope_base)
    directory = artifacts.artifact_directory(workflow_store_directory)
    os.makedirs(directory, mode=0o700, exist_ok=True)
    partial = os.path.join(directory, ".partial-%s%s" % (
        secrets.token_hex(8), artifacts.LOG_SUFFIX))
    ran_at = clock()
    started = monotonic()
    settlement = artifacts.SETTLEMENT_UNSETTLED
    log_handle = open(partial, "wb")
    try:
        nothing = open(os.devnull, "rb")
    except BaseException:
        _close_quietly(log_handle)
        raise
    try:
        os.chmod(partial, 0o600)
        try:
            process = process_ownership.spawn_owned(
                list(argv), VERIFICATION_LABEL, directory=scope,
                owned_root_base_dir=scope, hold_leader=True, cwd=lease_path,
                stdin=nothing, stdout=log_handle, stderr=log_handle)
        except process_ownership.SpawnGated:
            raise
        except Exception as exc:                       # noqa: BLE001
            raise VerificationStartUnknown(exc.__class__.__name__) from exc
    except BaseException:
        _close_quietly(log_handle, nothing)            # the in-flight cause is kept
        raise
    # POST-START: the spawn returned, a process ran.
    exit_status = wait_error = reap_error = None
    try:
        # Task 8 R28-1: the spawn is HELD, so this wait OBSERVES the command's
        # exit WITHOUT collecting it: its number stays this process's until the
        # reap below has decided — a descendant the command left running is
        # reaped as surely this group's, and a reused number is never in play.
        exit_status = process.wait()
    except BaseException as exc:
        wait_error = exc
    try:
        verdict, _detail = process_ownership.reap_owned(
            process.pid, directory=scope,
            settle_seconds=VERIFICATION_REAP_SETTLE_SECONDS)
        if verdict in (process_ownership.REAPED,
                       process_ownership.ALREADY_GONE):
            settlement = artifacts.SETTLEMENT_SETTLED
    except BaseException as exc:
        reap_error = exc
    finally:
        # The reap is decided: a reap that signalled collected the leader; a
        # refused one leaves it uncollected (nothing here waits on it again).
        process_ownership.disarm_hold(process)
    close_error = _close_quietly(log_handle, nothing)
    for error in (wait_error, reap_error):
        if error is not None and not isinstance(error, Exception):
            raise error
    if wait_error is not None:
        raise VerificationOutcomeUnknown(settlement, reap_error) from wait_error
    try:
        if close_error is not None:
            raise close_error
        duration = monotonic() - started
        finished_at = clock()
        log_sha256, log_bytes = artifacts.adopt_file(directory, partial,
                                                     artifacts.LOG_SUFFIX)
        record = artifacts.verification_record(
            workflow_id, mission_id, mission_revision, lease_path, argv,
            exit_status, log_sha256, log_bytes, ran_at, finished_at,
            round(duration, 6), candidate_identity, base_oid, settlement)
        return artifacts.store_document(directory, record), record
    except Exception as exc:                           # noqa: BLE001
        raise VerificationUnrecorded(exit_status, settlement) from exc
