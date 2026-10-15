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
transcript is ever consulted.
"""

import errno
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
          currency=None, task_status=None):
    """One review block (``contract_v2.REVIEW_KEYS``)."""
    return {"state": state, "problem": problem, "record": record,
            "gate": gate, "verdict": verdict, "currency": currency,
            "task_status": task_status,
            "integrity": (v2.INTEGRITY_LINKAGE_CORROBORATED
                          if state == v2.REVIEW_ADMITTED else None)}


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
        record = parse_preamble(read_preamble(root, name))
        if record["round"] != rounds:
            raise _Refused(v2.PROBLEM_REVIEW_ROUND_DISAGREES)
        token = record["protocol_token"]
        if (token, decision) not in v2.CORROBORATED_PAIRS:
            raise _Refused(v2.PROBLEM_REVIEW_DISAGREES)
    except _Refused as exc:
        return block(v2.REVIEW_REFUSED, exc.problem, task_status=status)
    parsed = token in v2.VERDICTS
    return block(v2.REVIEW_ADMITTED, record=record,
                 gate=v2.GATE_PARSED if parsed else v2.GATE_UNPARSED,
                 verdict=token if parsed else None, currency=currency,
                 task_status=status)


def _identity(info):
    return info.st_dev, info.st_ino


def read_preamble(root, name):
    """The preamble lines (bytes, without newlines) of ``root``'s canonical
    review file ``name``, by descriptor-relative descent through the
    expected components; raises ``_Refused``. No byte after the marker
    line is ever read."""
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
        lines = _read_until_marker(leaf)
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
