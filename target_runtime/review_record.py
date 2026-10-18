"""NARROW CANONICAL REVIEW RECORD CONTENT INSPECTION: the one admitted
artifact content class of query contract version 2 (``contract_v2``). It is
not general artifact inspection: evidence and result artifact content stay
refused and unsupported.

Reached from a SUPPORTED CANONICAL REFERENCE, never from a registration:
the task record (``.herd/state/task.json``, already one of the capture's
fixed state files) carries ``id``, ``review_rounds``,
``last_review_decision`` and ``last_review_file``, exactly as the reviewer
gate writes them (``herdctl review-decision``). Before ANY content read,
all of this must hold, or the block is refused with a precise reason:

- the task id equals the task the mission's execution is bound to (the
  caller passes it; without one nothing is read: ``not_applicable``);
- the reference is complete, ``review_rounds`` is an int in 1..99 (the file
  name carries exactly two digits) and ``last_review_decision`` is one of
  APPROVE, REJECT, MALFORMED;
- ``last_review_file`` is EQUAL, as a string, to the expected path: the
  pinned root, ``.herd/state/reviews``, and ``<id>-round-<NN>.md``. It is
  ONLY compared: nothing ever opens, resolves or follows the recorded path.

The file is then opened by DESCENT from the pinned root through the
EXPECTED components only, each directory opened relative to the previous
descriptor without following links (a symlinked, missing or non-directory
component refuses), the leaf opened no-follow and non-blocking; the leaf
must be a regular file with exactly one link and NOT writable by group or
other (``0o022``). That last rule is a DELIBERATE, REASONED deviation from
the private-file rule (``0o077``): review files are written world-readable
(0644), so the private-file rule would refuse every real one, and the
threat it would guard (disclosure) is not this class's: a world-WRITABLE
review file would let anyone forge a verdict, so writability is refused.

Only the PREAMBLE is read: BYTE BY BYTE, stopping at the literal
``## Transcript`` line, so no byte after that marker is ever read from the
descriptor; a marker not found within ``MAX_REVIEW_PREAMBLE_BYTES`` (plus
the marker line itself) or ``MAX_REVIEW_PREAMBLE_LINES`` refuses. After the
read, the root's identity and every component's identity are re-checked
against the descriptors held, so a root or directory replaced during the
read refuses (the ``state_artifact`` re-check model).

The preamble must be EXACTLY the writer's shape, else the whole file
refuses (no partial projection)::

    # Reviewer round <N>
    <blank>
    Reviewer: `<logical>` / `<agent>`
    <blank>
    Protocol token: `<raw token or MISSING>`
    <blank>
    ## Transcript

Four fields project: ``round`` (equal to ``review_rounds``),
``reviewer_logical``, ``reviewer_agent`` and ``protocol_token``. A raw token
outside APPROVE / REJECT / MISSING refuses (``review_raw_token_out_of_enum``)
and is never emitted. Corroboration is the writer's own pairing of TWO
vocabularies (the preamble carries the RAW token or MISSING, the task
carries the PARSED decision or MALFORMED): APPROVE/APPROVE, REJECT/REJECT
and MISSING/MALFORMED are accepted, every other pair refuses
(``review_decision_disagrees``). ``gate`` is the GATE fact (``parsed`` /
``unparsed``) and ``verdict`` the review fact, never collapsed: MISSING /
MALFORMED means the gate could not parse a valid token, NOT that the
reviewer rejected anything. ``currency`` is ``possibly_stale`` unless the
task is ACTIVE: the writer updates the task record only while ACTIVE.

INTEGRITY is ``linkage_corroborated``: a canonical review file has no
recorded digest anywhere, so its bytes are NOT digest-anchored; the
canonical linkage and the corroboration above are all there is, and this
module never invents a digest or a binding. Nothing here writes, repairs or
reinterprets a task record, and no other file, frozen capture or
transcript is ever consulted — with ONE exception, item 2 E2's
reviewer-declared findings SIBLING record (``<task>-round-NN.findings.json``,
``review_findings`` below): named by the task's ``last_review_findings``
reference, opened by the SAME descent under the EXPECTED name only, read one
byte at a time within its bound, digest-checked against the reference and
bound to this round's reviewer and exact preamble bytes. It is written by the
local review gate from the routed reviewer's explicit findings envelope; this
module never reads a transcript byte to obtain it.
"""

import errno
import hashlib
import json
import os
import re
import stat

from mission_sources import contract_v2 as v2

REVIEW_SUBDIRS = (".herd", "state", "reviews")
MARKER = b"## Transcript"
_DIRECTORY_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
_LEAF_FLAGS = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC
#: Group- or other-WRITABLE: refused (see the module on why not 0o077).
WRITABLE_BY_OTHERS = 0o022
_NAME = r"[A-Za-z0-9][A-Za-z0-9._:-]{0,%d}" % (v2.MAX_REVIEWER_NAME_CHARS - 1)
_ROUND_LINE = re.compile(r"# Reviewer round ([1-9][0-9]{0,2})")
_REVIEWER_LINE = re.compile(r"Reviewer: `(%s)` / `(%s)`" % (_NAME, _NAME))
_TOKEN_LINE = re.compile(r"Protocol token: `([^`\n]{1,64})`")


class _Refused(Exception):
    def __init__(self, problem):
        super(_Refused, self).__init__(problem)
        self.problem = problem


def block(state, problem=None, record=None, gate=None, verdict=None,
          currency=None, task_status=None, findings=None):
    """One review block (``contract_v2.REVIEW_KEYS`` plus ``findings``)."""
    return {"state": state, "problem": problem, "record": record,
            "gate": gate, "verdict": verdict, "currency": currency,
            "task_status": task_status,
            "integrity": (v2.INTEGRITY_LINKAGE_CORROBORATED
                          if state == v2.REVIEW_ADMITTED else None),
            v2.REVIEW_FINDINGS_KEY: findings}


def expected_name(task_id, rounds):
    """The canonical file name the writer uses for a round."""
    return "%s-round-%02d.md" % (task_id, rounds)


def _text_or_none(value):
    return value if type(value) is str and value else None


def canonical_review(root, task, expected_task_id):
    """The review block for the pinned ``root`` (an absolute, resolved
    directory the capture already validated), its parsed ``task`` record
    (None when ``task.json`` is absent) and the task id the mission's
    execution is bound to (None: no mission names this root)."""
    if expected_task_id is None:
        return block(v2.REVIEW_NOT_APPLICABLE, v2.PROBLEM_REVIEW_NO_BINDING)
    if type(task) is not dict:
        return block(v2.REVIEW_REFUSED, v2.PROBLEM_REVIEW_TASK_ABSENT)
    status = _text_or_none(task.get("status"))
    task_id = task.get("id")
    if type(task_id) is not str or task_id != expected_task_id:
        return block(v2.REVIEW_REFUSED, v2.PROBLEM_REVIEW_TASK_MISMATCH,
                     task_status=status)
    currency = (v2.CURRENCY_CURRENT
                if status == v2.REVIEW_CURRENT_TASK_STATUS
                else v2.CURRENCY_POSSIBLY_STALE)
    rounds = task.get("review_rounds")
    decision = task.get("last_review_decision")
    recorded = task.get("last_review_file")
    if decision is None and recorded is None and rounds in (None, 0):
        return block(v2.REVIEW_NOT_RECORDED, currency=currency,
                     task_status=status)
    try:
        if rounds is None or decision is None or recorded is None:
            raise _Refused(v2.PROBLEM_REVIEW_REFERENCE_INCOMPLETE)
        if type(rounds) is not int or rounds < 1:
            raise _Refused(v2.PROBLEM_REVIEW_ROUND_INVALID)
        if rounds > v2.MAX_REVIEW_ROUND:
            raise _Refused(v2.PROBLEM_REVIEW_ROUND_OUT_OF_CLASS)
        if type(decision) is not str or decision not in (
                v2.TASK_REVIEW_DECISIONS):
            raise _Refused(v2.PROBLEM_REVIEW_DECISION_UNKNOWN)
        name = expected_name(task_id, rounds)
        if "/" in name or "\x00" in name or os.sep in name:
            raise _Refused(v2.PROBLEM_REVIEW_FILE_NAME)
        directory = os.path.join(root, *REVIEW_SUBDIRS)
        # STRING comparison only: the recorded path is never opened.
        if type(recorded) is not str or "\x00" in recorded or (
                not os.path.isabs(recorded)) or (
                    os.path.normpath(recorded) != recorded) or (
                        os.path.dirname(recorded) != directory):
            raise _Refused(v2.PROBLEM_REVIEW_FILE_OUTSIDE)
        if os.path.basename(recorded) != name:
            raise _Refused(v2.PROBLEM_REVIEW_FILE_NAME)
        lines = read_preamble(root, name)
        record = parse_preamble(lines)
        if record["round"] != rounds:
            raise _Refused(v2.PROBLEM_REVIEW_ROUND_DISAGREES)
        token = record["protocol_token"]
        if (token, decision) not in v2.CORROBORATED_PAIRS:
            raise _Refused(v2.PROBLEM_REVIEW_DISAGREES)
    except _Refused as exc:
        return block(v2.REVIEW_REFUSED, exc.problem, task_status=status)
    parsed = token in v2.VERDICTS
    # The verdict above is complete BEFORE findings are looked at; nothing
    # about findings can change it.
    return block(v2.REVIEW_ADMITTED, record=record,
                 gate=v2.GATE_PARSED if parsed else v2.GATE_UNPARSED,
                 verdict=token if parsed else None, currency=currency,
                 task_status=status,
                 findings=review_findings(root, task, task_id, record, lines))


# -- reviewer-declared PUBLIC findings (item 2 E2) ----------------------------
#
# Read ONLY from the sibling record the local review gate persisted
# (``herdctl review-decision``): the task's ``last_review_findings`` reference
# names it and its sha256; the file is opened by the SAME descent as the
# preamble, under the EXPECTED name only (the reference is compared, never
# opened), read within ``MAX_RECORD_BYTES``, and must be EXACTLY the
# contract's record (``target_runtime.review_findings.validate_record``) for this task,
# this round, this reviewer and this preamble's exact bytes. The transcript is
# never read: the preamble digest is over the bytes the marker-bounded
# preamble read already holds.

FINDINGS_REFERENCE_KEY = "last_review_findings"
_REFERENCE_KEYS = ("state", "problem", "file", "sha256", "count", "round")
_DIGEST = re.compile(r"[0-9a-f]{64}")
#: The descent's refusals, as the findings record's own.
_DESCENT_TO_FINDINGS = {
    v2.PROBLEM_REVIEW_NOT_CONTAINED: v2.PROBLEM_FINDINGS_NOT_CONTAINED,
    v2.PROBLEM_REVIEW_ROOT_CHANGED: v2.PROBLEM_FINDINGS_ROOT_CHANGED,
    v2.PROBLEM_REVIEW_ABSENT: v2.PROBLEM_FINDINGS_ABSENT,
    v2.PROBLEM_REVIEW_UNREADABLE: v2.PROBLEM_FINDINGS_UNREADABLE,
    v2.PROBLEM_REVIEW_NOT_REGULAR: v2.PROBLEM_FINDINGS_NOT_REGULAR,
    v2.PROBLEM_REVIEW_HARDLINK: v2.PROBLEM_FINDINGS_HARDLINK,
    v2.PROBLEM_REVIEW_WRITABLE: v2.PROBLEM_FINDINGS_WRITABLE,
}


def findings_block(state, problem=None, record=None):
    """One findings block (``contract_v2.FINDINGS_KEYS``)."""
    if record is None:
        return {"state": state, "problem": problem, "count": None,
                "items": [], "candidate_sha256": None, "routed_at": None,
                "linkage": None, "provenance": None}
    return {"state": state, "problem": None, "count": record["count"],
            "items": [dict(f) for f in record["findings"]],
            "candidate_sha256": record["candidate_sha256"],
            "routed_at": record["routed_at"], "linkage": record["linkage"],
            "provenance": record["provenance"]}


def findings_name(task_id, rounds):
    """The canonical sibling name the gate writes for a round's findings."""
    return "%s-round-%02d.findings.json" % (task_id, rounds)


def _reference_ok(reference):
    if type(reference) is not dict or set(reference) != set(_REFERENCE_KEYS):
        return False
    rounds, state = reference["round"], reference["state"]
    if type(rounds) is not int or not 1 <= rounds <= v2.MAX_REVIEW_ROUND:
        return False
    # Closed TYPES before any lookup: a state or problem that arrived as a
    # list or an object is malformed, never a dict key (round 15 F3: an
    # unhashable state raised and dropped the whole chain observation).
    if type(state) is not str:
        return False
    if reference["problem"] is not None and type(
            reference["problem"]) is not str:
        return False
    if state == v2.FINDINGS_REFERENCE_RECORDED:
        return (reference["problem"] is None
                and type(reference["file"]) is str
                and type(reference["sha256"]) is str
                and _DIGEST.fullmatch(reference["sha256"]) is not None
                and type(reference["count"]) is int
                and 0 <= reference["count"] <= v2.MAX_FINDINGS)
    allowed = {v2.FINDINGS_NOT_RECORDED: v2.FINDINGS_NOT_RECORDED_PROBLEMS,
               v2.FINDINGS_REFUSED: v2.FINDINGS_WRITER_PROBLEMS}.get(state)
    return (allowed is not None and reference["problem"] in allowed
            and reference["file"] is None and reference["sha256"] is None
            and reference["count"] is None)


def _read_record(descriptor):
    """At most ``MAX_FINDINGS_RECORD_BYTES`` of the record, ONE byte at a
    time like every read in this module; one byte more refuses."""
    data = bytearray()
    while True:
        try:
            byte = os.read(descriptor, 1)
        except OSError:
            raise _Refused(v2.PROBLEM_FINDINGS_UNREADABLE)
        if not byte:
            return bytes(data)
        data += byte
        if len(data) > v2.MAX_FINDINGS_RECORD_BYTES:
            raise _Refused(v2.PROBLEM_FINDINGS_OVER_BOUND)


def review_findings(root, task, task_id, record, lines):
    """The findings block of an ADMITTED review whose preamble ``record``
    was parsed from ``lines``; never raises."""
    if FINDINGS_REFERENCE_KEY not in task:
        return findings_block(v2.FINDINGS_NOT_RECORDED,
                              v2.PROBLEM_FINDINGS_LEGACY)
    reference = task[FINDINGS_REFERENCE_KEY]
    if not _reference_ok(reference):
        return findings_block(v2.FINDINGS_REFUSED,
                              v2.PROBLEM_FINDINGS_REFERENCE)
    if reference["round"] != record["round"]:
        return findings_block(v2.FINDINGS_REFUSED, v2.PROBLEM_FINDINGS_ROUND)
    if reference["state"] == v2.FINDINGS_NOT_RECORDED:
        return findings_block(v2.FINDINGS_NOT_RECORDED, reference["problem"])
    if reference["state"] == v2.FINDINGS_REFUSED:
        return findings_block(v2.FINDINGS_REFUSED, reference["problem"])
    name = findings_name(task_id, record["round"])
    if reference["file"] != name:      # compared, never opened
        return findings_block(v2.FINDINGS_REFUSED,
                              v2.PROBLEM_FINDINGS_REFERENCE)
    try:
        data = _descend(root, name, _read_record)
    except _Refused as exc:
        return findings_block(v2.FINDINGS_REFUSED, _DESCENT_TO_FINDINGS.get(
            exc.problem, exc.problem))
    if hashlib.sha256(data).hexdigest() != reference["sha256"]:
        return findings_block(v2.FINDINGS_REFUSED,
                              v2.PROBLEM_FINDINGS_DIGEST_MISMATCH)
    try:
        parsed = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, ValueError, RecursionError):
        return findings_block(v2.FINDINGS_REFUSED,
                              v2.PROBLEM_FINDINGS_NOT_JSON)
    try:
        persisted = v2.validate_findings_record(parsed)
    except v2.contract.ContractError:
        return findings_block(v2.FINDINGS_REFUSED, v2.PROBLEM_FINDINGS_SHAPE)
    for problem, agrees in (
            (v2.PROBLEM_FINDINGS_TASK, persisted["task_id"] == task_id),
            (v2.PROBLEM_FINDINGS_ROUND, persisted["round"] == record["round"]),
            (v2.PROBLEM_FINDINGS_REVIEWER,
             (persisted["reviewer_logical"], persisted["reviewer_agent"])
             == (record["reviewer_logical"], record["reviewer_agent"])),
            (v2.PROBLEM_FINDINGS_PREAMBLE,
             persisted["preamble_sha256"] == hashlib.sha256(
                 b"\n".join(lines) + b"\n").hexdigest()),
            (v2.PROBLEM_FINDINGS_COUNT,
             persisted["count"] == reference["count"])):
        if not agrees:
            return findings_block(v2.FINDINGS_REFUSED, problem)
    return findings_block(v2.FINDINGS_PRESENT if persisted["count"]
                          else v2.FINDINGS_EMPTY, record=persisted)


def _identity(info):
    return info.st_dev, info.st_ino


def read_preamble(root, name):
    """The preamble lines (bytes, without newlines) of ``root``'s canonical
    review file ``name``, by descriptor-relative descent through the
    expected components; raises ``_Refused``. No byte after the marker
    line is ever read."""
    return _descend(root, name, _read_until_marker)


def _descend(root, name, read_leaf):
    """``read_leaf(leaf_descriptor)`` of ``root``'s ``.herd/state/reviews/name``,
    reached by descriptor-relative descent through the expected components
    (no link followed, a regular file with one link, not group/other
    writable), with every identity re-checked after the read; raises
    ``_Refused`` with the review codes."""
    try:
        pinned = os.lstat(root)
    except OSError:
        raise _Refused(v2.PROBLEM_REVIEW_NOT_CONTAINED)
    if not stat.S_ISDIR(pinned.st_mode) or os.path.realpath(root) != root:
        raise _Refused(v2.PROBLEM_REVIEW_NOT_CONTAINED)
    held = []
    try:
        try:
            descriptor = os.open(root, _DIRECTORY_FLAGS)
        except OSError:
            raise _Refused(v2.PROBLEM_REVIEW_NOT_CONTAINED)
        held.append(descriptor)
        if _identity(os.fstat(descriptor)) != _identity(pinned):
            raise _Refused(v2.PROBLEM_REVIEW_ROOT_CHANGED)
        identities = []
        for component in REVIEW_SUBDIRS:
            try:
                descriptor = os.open(component, _DIRECTORY_FLAGS,
                                     dir_fd=held[-1])
            except FileNotFoundError:
                raise _Refused(v2.PROBLEM_REVIEW_ABSENT)
            except OSError:
                # A link (ELOOP), a non-directory (ENOTDIR) or anything
                # else: the expected component is not a contained directory.
                raise _Refused(v2.PROBLEM_REVIEW_NOT_CONTAINED)
            held.append(descriptor)
            identities.append((component, _identity(os.fstat(descriptor))))
        try:
            leaf = os.open(name, _LEAF_FLAGS, dir_fd=held[-1])
        except FileNotFoundError:
            raise _Refused(v2.PROBLEM_REVIEW_ABSENT)
        except OSError as exc:
            if exc.errno in (errno.ELOOP, errno.EMLINK):
                raise _Refused(v2.PROBLEM_REVIEW_NOT_CONTAINED)
            raise _Refused(v2.PROBLEM_REVIEW_UNREADABLE)
        held.append(leaf)
        info = os.fstat(leaf)
        if not stat.S_ISREG(info.st_mode):
            raise _Refused(v2.PROBLEM_REVIEW_NOT_REGULAR)
        if info.st_nlink != 1:
            raise _Refused(v2.PROBLEM_REVIEW_HARDLINK)
        if stat.S_IMODE(info.st_mode) & WRITABLE_BY_OTHERS:
            raise _Refused(v2.PROBLEM_REVIEW_WRITABLE)
        lines = read_leaf(leaf)
        # Re-check, AFTER the read, that the root and every component are
        # still the ones descended through (a replaced one refuses).
        try:
            if _identity(os.lstat(root)) != _identity(pinned) or (
                    os.path.realpath(root) != root):
                raise _Refused(v2.PROBLEM_REVIEW_ROOT_CHANGED)
            for (component, identity), parent in zip(identities, held):
                if _identity(os.stat(component, dir_fd=parent,
                                     follow_symlinks=False)) != identity:
                    raise _Refused(v2.PROBLEM_REVIEW_ROOT_CHANGED)
            if _identity(os.stat(name, dir_fd=held[len(REVIEW_SUBDIRS)],
                                 follow_symlinks=False)) != _identity(info):
                raise _Refused(v2.PROBLEM_REVIEW_ROOT_CHANGED)
        except OSError:
            raise _Refused(v2.PROBLEM_REVIEW_ROOT_CHANGED)
        return lines
    finally:
        for descriptor in reversed(held):
            os.close(descriptor)


def _read_until_marker(descriptor):
    """Read ONE byte at a time and stop at the end of the marker line; the
    bytes before it are bounded by the preamble bounds."""
    limit = v2.MAX_REVIEW_PREAMBLE_BYTES + len(MARKER) + 1
    lines, current, consumed = [], bytearray(), 0
    while True:
        try:
            byte = os.read(descriptor, 1)
        except OSError:
            raise _Refused(v2.PROBLEM_REVIEW_UNREADABLE)
        if not byte:
            raise _Refused(v2.PROBLEM_REVIEW_NO_MARKER)
        consumed += 1
        if byte == b"\n":
            line = bytes(current)
            if line == MARKER:
                if consumed - len(MARKER) - 1 > v2.MAX_REVIEW_PREAMBLE_BYTES:
                    raise _Refused(v2.PROBLEM_REVIEW_OVER_BOUND)
                return lines
            lines.append(line)
            current = bytearray()
            if len(lines) > v2.MAX_REVIEW_PREAMBLE_LINES:
                raise _Refused(v2.PROBLEM_REVIEW_OVER_BOUND)
        else:
            current += byte
        if consumed >= limit:
            raise _Refused(v2.PROBLEM_REVIEW_OVER_BOUND)


def parse_preamble(lines):
    """The four fields of an EXACT writer-shaped preamble; raises
    ``_Refused``. A raw token outside the admitted enum refuses by its own
    reason and is never returned."""
    try:
        text = [line.decode("utf-8") for line in lines]
    except UnicodeDecodeError:
        raise _Refused(v2.PROBLEM_REVIEW_NOT_UTF8)
    if len(text) != 6 or text[1] or text[3] or text[5]:
        raise _Refused(v2.PROBLEM_REVIEW_SHAPE)
    heading = _ROUND_LINE.fullmatch(text[0])
    reviewer = _REVIEWER_LINE.fullmatch(text[2])
    token = _TOKEN_LINE.fullmatch(text[4])
    if heading is None or reviewer is None or token is None:
        raise _Refused(v2.PROBLEM_REVIEW_SHAPE)
    if token.group(1) not in v2.PROTOCOL_TOKENS:
        raise _Refused(v2.PROBLEM_REVIEW_TOKEN_OUT_OF_ENUM)
    number = int(heading.group(1))
    if number > v2.MAX_REVIEW_ROUND:
        raise _Refused(v2.PROBLEM_REVIEW_ROUND_OUT_OF_CLASS)
    return {"round": number, "reviewer_logical": reviewer.group(1),
            "reviewer_agent": reviewer.group(2),
            "protocol_token": token.group(1)}
