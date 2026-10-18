"""Reviewer-declared PUBLIC review findings (item 2): the declaration contract
a Reviewer follows in its CURRENT output, the extraction a review gate runs
over that output, the routing record the gate links it to, and the persisted
record an observation reader re-validates. Pure: no I/O, no clock.

THE DECLARATION (an explicit, purpose-declared envelope; version 1). The
Reviewer, read-only, writes it as whole lines of its own output::

    HERD_FINDINGS_BEGIN v1
    nonce <the route's 16-hex nonce>
    task <task id>
    round <review round>
    reviewer <its logical role name>
    candidate <the routed candidate's 64-hex sha256>
    count <N>
    [F1] <blocker|non_blocking> | <repo-relative path[:line] or -> | <summary> [/F1]
    ...
    HERD_FINDINGS_END v1 nonce=<nonce> count=<N>

Version 2, the COMPACT declaration (round 15), carries the same findings in
lines of at most ``MAX_COMPACT_LINE_CHARS``, numbered parts and no candidate
line (the stored route pins it); its grammar and checks are in
``_validate_compact``. Either version is accepted; a route takes one.

ONLY that envelope is consumed, and only when it echoes the CURRENT route's
single-use nonce. Nothing else is read for findings: not a terse tail, not
surrounding prose, not a code example, not another round's envelope, not a
generic search. The one transport normalization is the reviewer TUI's own
decoration, applied to every line alike: trailing whitespace, then one
leading ``"\\u2022 "`` bullet, then leading spaces are removed. A line under
any other prefix (``> ``, a sentence) is not an envelope line, and NOTHING
inside a fenced code block is (round 15 F1): from a line of three or more
backticks or tildes to a line of the same character at least as long (or to
the end, when it never closes), every line is an EXAMPLE. A current-nonce
envelope found only there is ``findings_declaration_only_in_example``, never
a declaration. Residual: a TUI that renders a fence WITHOUT its marker lines
leaves nothing here to recognize; the request must therefore never carry a
filled current-nonce example envelope (placeholders only).

LINKAGE is checked against the ROUTING CONTEXT the gate recorded itself
(``new_route``: task, round, reviewer logical and agent, candidate, nonce),
never against two strings a caller supplies together: the route must be for
this task, this round and the reviewer whose output is read, and the envelope
must name the same task, round, reviewer and candidate and echo the nonce.
Every other outcome FAILS CLOSED with its own code; nothing is reconstructed,
and nothing is truncated: a count, an index sequence or a closing tag that
does not add up refuses the whole declaration, so a dropped blocker is a
failure, never a shorter list.

THE PROVENANCE CEILING (``PROVENANCE``, written into every record): the
findings are model-authored text the routed reviewer's output declared,
captured by the local gate and bound to the round, reviewer and routed
candidate by the echoed nonce. That is not a cryptographic proof of reviewer
identity, not a verdict, not approval authority, and not proof that the
reviewed bytes are the candidate's. The verdict is the round's protocol token
alone, decided before and independently of any of this.
"""

import hashlib
import json
import re

CONTRACT_VERSION = 1
ROUTE_SCHEMA = "di.review.findings_route"
RECORD_SCHEMA = "di.review.findings_record"
BEGIN_LINE = "HERD_FINDINGS_BEGIN v1"
END_PREFIX = "HERD_FINDINGS_END v1"
#: The COMPACT declaration (version 2, round 15 decision): see
#: ``_validate_compact``. Both versions are accepted; one route takes one.
BEGIN_LINE_V2 = "HERD_FINDINGS_BEGIN v2"
END_PREFIX_V2 = "HERD_FINDINGS_END v2"
SEVERITIES = ("blocker", "non_blocking")
LINKAGE = "route_nonce_echoed_in_routed_reviewer_output"
PROVENANCE = (
    "Reviewer-declared public findings: model-authored text the routed"
    " reviewer's own output declared in the findings envelope, captured by the"
    " local review gate and bound to this task, round, reviewer and routed"
    " candidate by the route's single-use nonce it echoed. This is not a"
    " cryptographic proof of reviewer identity, not a verdict and not approval"
    " authority, and it does not prove that the reviewed bytes are the"
    " candidate's; the verdict is the round's protocol token alone.")

#: Bounds. This module sits in ``target_runtime`` so the bound-pin gate
#: (tests/test_workflow_authority.py ``BoundConstantPinTests``) DERIVES
#: every one of them; each is pinned there by an exact literal.
MAX_FINDINGS = 32
MAX_SUMMARY_CHARS = 280
MAX_AREA_CHARS = 200
MAX_LINE_NUMBER = 9999999
#: The sum of every finding's summary and area, in UTF-8 bytes.
MAX_FINDINGS_AGGREGATE_BYTES = 8192
MAX_ENVELOPE_LINE_CHARS = 400
#: The most capture text one extraction scans; more refuses, never a partial
#: scan (a partial scan could miss a duplicate).
MAX_CAPTURE_CHARS = 1048576
#: The persisted record, encoded; the per-field and aggregate bounds keep a
#: valid record below it by construction.
MAX_RECORD_BYTES = 32768
#: Every line of a COMPACT declaration, in characters (not bytes), after the
#: TUI decoration is removed. Set from the Lead's measurement of a supported
#: zoomed capture (a finding line wrapped at ~93 effective characters
#: including the 2-character decoration): 88 + 2 leaves a margin of 3.
MAX_COMPACT_LINE_CHARS = 88
#: Numbered parts one compact finding may span.
MAX_FINDING_PARTS = 8

OUTCOME_RECORDED = "recorded"
OUTCOME_REFUSED = "refused"
OUTCOME_NOT_RECORDED = "not_recorded"

PROBLEM_NO_ROUTE = "findings_declaration_not_routed"
PROBLEM_MISSING = "findings_declaration_missing"
#: The ONLY current-nonce envelope was inside a code fence: an example, never
#: a declaration (round 15 F1), and distinct from no envelope at all.
PROBLEM_ONLY_IN_EXAMPLE = "findings_declaration_only_in_example"
PROBLEM_AMBIGUOUS = "findings_declaration_ambiguous"
PROBLEM_INCOMPLETE = "findings_declaration_incomplete"
PROBLEM_MALFORMED = "findings_declaration_malformed"
PROBLEM_OVER_BOUND = "findings_declaration_over_bound"
PROBLEM_COUNT_MISMATCH = "findings_declaration_count_mismatch"
PROBLEM_WRONG_TASK = "findings_declaration_wrong_task"
PROBLEM_WRONG_ROUND = "findings_declaration_wrong_round"
PROBLEM_WRONG_REVIEWER = "findings_declaration_wrong_reviewer"
PROBLEM_WRONG_CANDIDATE = "findings_declaration_wrong_candidate"
PROBLEM_ROUTE_MALFORMED = "findings_route_malformed"
PROBLEM_ROUTE_TASK = "findings_route_not_for_this_task"
PROBLEM_ROUTE_ROUND = "findings_route_not_for_this_round"
PROBLEM_ROUTE_REVIEWER = "findings_route_not_for_this_reviewer"
PROBLEM_CAPTURE_OVER_BOUND = "findings_capture_over_bound"
PROBLEM_CAPTURE_UNAVAILABLE = "findings_capture_unavailable"
PROBLEM_WRITER_ERROR = "findings_writer_error"
#: Recorded as NOT RECORDED: nothing was declared for this round.
NOT_RECORDED_PROBLEMS = (PROBLEM_NO_ROUTE, PROBLEM_MISSING,
                         PROBLEM_ONLY_IN_EXAMPLE)
#: Recorded as REFUSED: a declaration (or its linkage) did not hold.
WRITER_PROBLEMS = (
    PROBLEM_AMBIGUOUS, PROBLEM_INCOMPLETE, PROBLEM_MALFORMED,
    PROBLEM_OVER_BOUND, PROBLEM_COUNT_MISMATCH, PROBLEM_WRONG_TASK,
    PROBLEM_WRONG_ROUND, PROBLEM_WRONG_REVIEWER, PROBLEM_WRONG_CANDIDATE,
    PROBLEM_ROUTE_MALFORMED, PROBLEM_ROUTE_TASK, PROBLEM_ROUTE_ROUND,
    PROBLEM_ROUTE_REVIEWER, PROBLEM_CAPTURE_OVER_BOUND,
    PROBLEM_CAPTURE_UNAVAILABLE, PROBLEM_WRITER_ERROR)

ROUTE_KEYS = ("schema", "version", "nonce", "task_id", "round",
              "reviewer_logical", "reviewer_agent", "candidate_sha256",
              "routed_at")
RECORD_KEYS = ("schema", "version", "task_id", "round", "reviewer_logical",
               "reviewer_agent", "candidate_sha256", "route_nonce",
               "routed_at", "preamble_sha256", "linkage", "provenance",
               "count", "findings")
FINDING_KEYS = ("index", "severity", "area", "line", "summary")

_NONCE = re.compile(r"[0-9a-f]{16}")
_DIGEST = re.compile(r"[0-9a-f]{64}")
_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}")
_TASK_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")
_END = re.compile(r"HERD_FINDINGS_END v1 nonce=(\S+) count=(\S+)")
_END_V2 = re.compile(r"HERD_FINDINGS_END v2 nonce=(\S+) count=(\S+) parts=(\S+)")
#: ANY version's END line (delimiting and orphan detection only): group 2 is
#: the nonce it echoes.
_END_ANY = re.compile(r"HERD_FINDINGS_END (v1|v2) nonce=(\S+) count=\S+(?: parts=\S+)?")
_BEGINS = (BEGIN_LINE, BEGIN_LINE_V2)
#: One compact part line: ``[F<finding> <part>/<parts>] <text>``; the text is
#: non-empty and starts and ends with a non-space (the TUI strips trailing
#: spaces, so a space at a part boundary could never be carried faithfully).
_PART = re.compile(r"\[F([1-9][0-9]?) ([1-9])/([1-9])\] (\S(?:.*\S)?)")
_OPEN_TAG = re.compile(r"\[F([0-9]{1,3})\] ")
_FINDING = re.compile(r"\[F([0-9]{1,3})\] (.*) \[/F\1\]")
#: A code-fence line (after the TUI decoration is removed): three or more
#: backticks or tildes, then anything (an info string).
_FENCE = re.compile(r"(`{3,}|~{3,})(.*)")
#: What a fenced line becomes for the envelope scan: never an envelope line.
_MASKED = "\x00"
_AREA = re.compile(r"[A-Za-z0-9_][A-Za-z0-9._/-]*")
#: Characters a summary may not carry: C0 controls, DEL, C1 controls and
#: the bidirectional embedding/override/isolate controls.
_REFUSED_CHARACTERS = frozenset(
    [chr(c) for c in range(0x00, 0x20)] + [chr(0x7F)]
    + [chr(c) for c in range(0x80, 0xA0)]
    + [chr(c) for c in range(0x202A, 0x202F)]
    + [chr(c) for c in range(0x2066, 0x206A)])
_HEADER = ("nonce", "task", "round", "reviewer", "candidate", "count")
#: The compact header: no candidate on the wire. The stored route pins the
#: candidate and the echoed single-use nonce binds this declaration to that
#: route (redundancy removed; the binding is not weakened).
_HEADER_V2 = ("nonce", "task", "round", "reviewer", "count")


class RecordRefused(Exception):
    """A persisted record is not exactly this contract's shape."""


class _Refused(Exception):
    def __init__(self, problem):
        super(_Refused, self).__init__(problem)
        self.problem = problem


def _outcome(state, problem=None, findings=None, foreign=0):
    return {"state": state, "problem": problem, "findings": findings,
            "foreign_envelopes": foreign}


def new_route(nonce, task_id, round_number, reviewer_logical, reviewer_agent,
              candidate_sha256, routed_at):
    """The routing record a review gate writes when it ROUTES a review: the
    context a later declaration must match. Raises ValueError."""
    route = {"schema": ROUTE_SCHEMA, "version": CONTRACT_VERSION,
             "nonce": nonce, "task_id": task_id, "round": round_number,
             "reviewer_logical": reviewer_logical,
             "reviewer_agent": reviewer_agent,
             "candidate_sha256": candidate_sha256, "routed_at": routed_at}
    if not _route_ok(route):
        raise ValueError("not a valid findings route")
    return route


def _route_ok(route):
    return (type(route) is dict and set(route) == set(ROUTE_KEYS)
            and route["schema"] == ROUTE_SCHEMA
            and route["version"] == CONTRACT_VERSION
            and type(route["nonce"]) is str
            and _NONCE.fullmatch(route["nonce"]) is not None
            and type(route["task_id"]) is str
            and _TASK_ID.fullmatch(route["task_id"]) is not None
            and type(route["round"]) is int and route["round"] >= 1
            and type(route["reviewer_logical"]) is str
            and _NAME.fullmatch(route["reviewer_logical"]) is not None
            and type(route["reviewer_agent"]) is str
            and _NAME.fullmatch(route["reviewer_agent"]) is not None
            and type(route["candidate_sha256"]) is str
            and _DIGEST.fullmatch(route["candidate_sha256"]) is not None
            and type(route["routed_at"]) is int and route["routed_at"] >= 0)


def _undecorate(line):
    line = line.rstrip()
    if line.startswith("• "):
        line = line[2:]
    return line.lstrip(" ")


def _summary_ok(text):
    return bool(text) and text == text.strip() and not any(
        c in _REFUSED_CHARACTERS for c in text)


def _parse_finding(index, body):
    """One finding line's body (between the tags); raises ``_Refused``."""
    parts = body.split(" | ", 2)
    if len(parts) != 3:
        raise _Refused(PROBLEM_MALFORMED)
    severity, area_text, summary = parts
    if severity not in SEVERITIES:
        raise _Refused(PROBLEM_MALFORMED)
    area, line = None, None
    if area_text != "-":
        path, _, number = area_text.partition(":")
        if len(path) > MAX_AREA_CHARS:
            raise _Refused(PROBLEM_OVER_BOUND)
        if _AREA.fullmatch(path) is None or ".." in path.split("/") or (
                "" in path.split("/")):
            raise _Refused(PROBLEM_MALFORMED)
        if number:
            if not number.isdigit() or number.startswith("0") or (
                    int(number) > MAX_LINE_NUMBER):
                raise _Refused(PROBLEM_MALFORMED)
            line = int(number)
        elif ":" in area_text:
            raise _Refused(PROBLEM_MALFORMED)
        area = path
    if len(summary) > MAX_SUMMARY_CHARS:
        raise _Refused(PROBLEM_OVER_BOUND)
    if not _summary_ok(summary):
        raise _Refused(PROBLEM_MALFORMED)
    return {"index": index, "severity": severity, "area": area, "line": line,
            "summary": summary}


def _envelopes(lines):
    """``(complete, incomplete_nonces, unattributed)``: every envelope found
    by its whole BEGIN line, delimited by the first END line after it."""
    complete, incomplete, unattributed = [], [], 0
    claimed_ends = set()
    i = 0
    while i < len(lines):
        if lines[i] not in _BEGINS:
            i += 1
            continue
        start = i
        nonce_line = lines[i + 1] if i + 1 < len(lines) else ""
        nonce = nonce_line[len("nonce "):] if nonce_line.startswith(
            "nonce ") else None
        if nonce is not None and _NONCE.fullmatch(nonce) is None:
            nonce = None
        end = None
        j = i + 1
        while j < len(lines):
            if lines[j] in _BEGINS:
                break                      # nested: this one never ended
            if _END_ANY.fullmatch(lines[j]):
                end = j
                break
            j += 1
        if nonce is None:
            unattributed += 1
        elif end is None:
            incomplete.append(nonce)
        else:
            complete.append((nonce, lines[start:end + 1]))
        if end is not None:
            claimed_ends.add(end)
            i = end + 1
        else:
            i = j if j > i else i + 1
    orphans = []
    for k, line in enumerate(lines):
        match = _END_ANY.fullmatch(line)
        if match and k not in claimed_ends:
            orphans.append(match.group(2))
    return complete, incomplete + orphans, unattributed


def _split_examples(lines):
    """``(declared, examples)``: the same lines twice, each with the OTHER
    part masked. A fence opens on a fence line and closes only on a line of
    the SAME character, at least as long, with nothing after it but spaces;
    an unclosed fence runs to the end (everything after it is an example).
    Fence lines themselves belong to neither part."""
    declared, examples = [], []
    fence = None
    for line in lines:
        match = _FENCE.fullmatch(line)
        if fence is None and match:
            fence = match.group(1)
            declared.append(_MASKED)
            examples.append(_MASKED)
            continue
        if fence is not None:
            if match and match.group(1)[0] == fence[0] and len(
                    match.group(1)) >= len(fence) and not match.group(2).strip():
                fence = None
                declared.append(_MASKED)
                examples.append(_MASKED)
                continue
            declared.append(_MASKED)
            examples.append(line)
            continue
        declared.append(line)
        examples.append(_MASKED)
    return declared, examples


def _validate_envelope(block, route, task_id, round_number, reviewer_logical):
    """The findings of ONE complete current envelope; raises ``_Refused``."""
    if block[0] == BEGIN_LINE_V2:
        return _validate_compact(block, task_id, round_number,
                                 reviewer_logical)
    if len(block) > MAX_FINDINGS + len(_HEADER) + 2:
        raise _Refused(PROBLEM_OVER_BOUND)
    if any(len(line) > MAX_ENVELOPE_LINE_CHARS for line in block):
        raise _Refused(PROBLEM_OVER_BOUND)
    header = {}
    for offset, name in enumerate(_HEADER, 1):
        if offset >= len(block) - 1:
            raise _Refused(PROBLEM_MALFORMED)
        prefix = name + " "
        if not block[offset].startswith(prefix):
            raise _Refused(PROBLEM_MALFORMED)
        header[name] = block[offset][len(prefix):]
    end = _END.fullmatch(block[-1])
    if end is None:                        # another version's END line
        raise _Refused(PROBLEM_MALFORMED)
    if end.group(1) != header["nonce"]:
        raise _Refused(PROBLEM_INCOMPLETE)
    if not header["count"].isdigit() or not end.group(2).isdigit():
        raise _Refused(PROBLEM_MALFORMED)
    count = int(header["count"])
    if count > MAX_FINDINGS:
        raise _Refused(PROBLEM_OVER_BOUND)
    if not header["round"].isdigit() or _DIGEST.fullmatch(
            header["candidate"]) is None or _NAME.fullmatch(
                header["reviewer"]) is None or _TASK_ID.fullmatch(
                    header["task"]) is None:
        raise _Refused(PROBLEM_MALFORMED)
    findings = []
    for line in block[len(_HEADER) + 1:-1]:
        match = _FINDING.fullmatch(line)
        if match is None:
            if _OPEN_TAG.match(line):
                raise _Refused(PROBLEM_INCOMPLETE)   # its closing tag is cut
            raise _Refused(PROBLEM_MALFORMED)
        findings.append((int(match.group(1)), match.group(2)))
    if int(end.group(2)) != count or len(findings) != count or [
            n for n, _ in findings] != list(range(1, count + 1)):
        raise _Refused(PROBLEM_COUNT_MISMATCH)
    parsed = [_parse_finding(n, body) for n, body in findings]
    aggregate = sum(len(f["summary"].encode("utf-8"))
                    + len((f["area"] or "").encode("utf-8")) for f in parsed)
    if aggregate > MAX_FINDINGS_AGGREGATE_BYTES:
        raise _Refused(PROBLEM_OVER_BOUND)
    if header["task"] != task_id:
        raise _Refused(PROBLEM_WRONG_TASK)
    if int(header["round"]) != round_number:
        raise _Refused(PROBLEM_WRONG_ROUND)
    if header["reviewer"] != reviewer_logical:
        raise _Refused(PROBLEM_WRONG_REVIEWER)
    if header["candidate"] != route["candidate_sha256"]:
        raise _Refused(PROBLEM_WRONG_CANDIDATE)
    return parsed


def _linkage_and_bounds(parsed, header, task_id, round_number,
                        reviewer_logical):
    aggregate = sum(len(f["summary"].encode("utf-8"))
                    + len((f["area"] or "").encode("utf-8")) for f in parsed)
    if aggregate > MAX_FINDINGS_AGGREGATE_BYTES:
        raise _Refused(PROBLEM_OVER_BOUND)
    if header["task"] != task_id:
        raise _Refused(PROBLEM_WRONG_TASK)
    if int(header["round"]) != round_number:
        raise _Refused(PROBLEM_WRONG_ROUND)
    if header["reviewer"] != reviewer_logical:
        raise _Refused(PROBLEM_WRONG_REVIEWER)


def _validate_compact(block, task_id, round_number, reviewer_logical):
    """The findings of ONE complete current COMPACT envelope::

        HERD_FINDINGS_BEGIN v2
        nonce <16 hex>
        task <task id>
        round <n>
        reviewer <logical>
        count <N>
        [F1 1/2] blocker | path:line |
        [F1 2/2] the summary, continued in a numbered part
        HERD_FINDINGS_END v2 nonce=<16 hex> count=<N> parts=<P>

    Every line is at most ``MAX_COMPACT_LINE_CHARS``. Finding ``i`` is its
    parts ``1/n .. n/n``, in order, consecutive, all present, joined by
    EXACTLY one space into the long form's ``severity | area | summary``.
    Findings run ``1 .. N`` in order, and the END restates ``N`` and the
    total part lines ``P``. Anything missing, duplicated, reordered,
    renumbered, miscounted or wrapped (a continuation is not a part line)
    REFUSES the whole declaration; nothing is ever rejoined by guesswork.
    Raises ``_Refused``."""
    if any(len(line) > MAX_COMPACT_LINE_CHARS for line in block):
        raise _Refused(PROBLEM_OVER_BOUND)
    if len(block) > MAX_FINDINGS * MAX_FINDING_PARTS + len(_HEADER_V2) + 2:
        raise _Refused(PROBLEM_OVER_BOUND)
    header = {}
    for offset, name in enumerate(_HEADER_V2, 1):
        if offset >= len(block) - 1:
            raise _Refused(PROBLEM_MALFORMED)
        prefix = name + " "
        if not block[offset].startswith(prefix):
            raise _Refused(PROBLEM_MALFORMED)
        header[name] = block[offset][len(prefix):]
    end = _END_V2.fullmatch(block[-1])
    if end is None:                        # another version's END line
        raise _Refused(PROBLEM_MALFORMED)
    if end.group(1) != header["nonce"]:
        raise _Refused(PROBLEM_INCOMPLETE)
    if not (header["count"].isdigit() and end.group(2).isdigit()
            and end.group(3).isdigit()):
        raise _Refused(PROBLEM_MALFORMED)
    if not header["round"].isdigit() or _NAME.fullmatch(
            header["reviewer"]) is None or _TASK_ID.fullmatch(
                header["task"]) is None:
        raise _Refused(PROBLEM_MALFORMED)
    count = int(header["count"])
    if count > MAX_FINDINGS:
        raise _Refused(PROBLEM_OVER_BOUND)
    parts = []
    for line in block[len(_HEADER_V2) + 1:-1]:
        match = _PART.fullmatch(line)
        if match is None:
            raise _Refused(PROBLEM_MALFORMED)
        parts.append((int(match.group(1)), int(match.group(2)),
                      int(match.group(3)), match.group(4)))
    if int(end.group(2)) != count or int(end.group(3)) != len(parts):
        raise _Refused(PROBLEM_COUNT_MISMATCH)
    if any(n > MAX_FINDING_PARTS for _, _, n, _ in parts):
        raise _Refused(PROBLEM_OVER_BOUND)
    bodies, position = [], 0
    for index in range(1, count + 1):
        if position >= len(parts):
            raise _Refused(PROBLEM_COUNT_MISMATCH)
        total = parts[position][2]
        texts = []
        for part in range(1, total + 1):
            if position >= len(parts) or parts[position][:3] != (
                    index, part, total):
                raise _Refused(PROBLEM_COUNT_MISMATCH)
            texts.append(parts[position][3])
            position += 1
        bodies.append((index, " ".join(texts)))
    if position != len(parts):
        raise _Refused(PROBLEM_COUNT_MISMATCH)
    parsed = [_parse_finding(index, body) for index, body in bodies]
    _linkage_and_bounds(parsed, header, task_id, round_number,
                        reviewer_logical)
    return parsed


def extract_declaration(capture, route, task_id, round_number,
                        reviewer_logical, reviewer_agent):
    """The outcome of ONE review round's declaration: ``state`` (recorded /
    refused / not_recorded), ``problem``, ``findings`` (a list only when
    recorded) and ``foreign_envelopes`` (envelopes echoing another nonce,
    counted and ignored). ``route`` is the routing record the gate wrote;
    ``task_id``, ``round_number`` and the reviewer are what the gate knows
    now; ``capture`` is the routed reviewer's own captured output."""
    if not _route_ok(route):
        return _outcome(OUTCOME_REFUSED, PROBLEM_ROUTE_MALFORMED)
    if route["task_id"] != task_id:
        return _outcome(OUTCOME_REFUSED, PROBLEM_ROUTE_TASK)
    if route["round"] != round_number:
        return _outcome(OUTCOME_REFUSED, PROBLEM_ROUTE_ROUND)
    if (route["reviewer_logical"], route["reviewer_agent"]) != (
            reviewer_logical, reviewer_agent):
        return _outcome(OUTCOME_REFUSED, PROBLEM_ROUTE_REVIEWER)
    if type(capture) is not str:
        return _outcome(OUTCOME_REFUSED, PROBLEM_CAPTURE_UNAVAILABLE)
    if len(capture) > MAX_CAPTURE_CHARS:
        return _outcome(OUTCOME_REFUSED, PROBLEM_CAPTURE_OVER_BOUND)
    lines, examples = _split_examples(
        [_undecorate(line) for line in capture.split("\n")])
    complete, incomplete, unattributed = _envelopes(lines)
    nonce = route["nonce"]
    current = [block for n, block in complete if n == nonce]
    foreign = sum(1 for n, _ in complete if n != nonce)
    if unattributed:
        return _outcome(OUTCOME_REFUSED, PROBLEM_MALFORMED, foreign=foreign)
    # A broken envelope echoing the current nonce is INCOMPLETE whatever
    # else is present (its findings are unknown); two whole ones AMBIGUOUS.
    if nonce in incomplete:
        return _outcome(OUTCOME_REFUSED, PROBLEM_INCOMPLETE, foreign=foreign)
    if len(current) > 1:
        return _outcome(OUTCOME_REFUSED, PROBLEM_AMBIGUOUS, foreign=foreign)
    if not current:
        example_complete, example_incomplete, _ = _envelopes(examples)
        in_example = nonce in example_incomplete or any(
            n == nonce for n, _ in example_complete)
        return _outcome(OUTCOME_NOT_RECORDED,
                        PROBLEM_ONLY_IN_EXAMPLE if in_example
                        else PROBLEM_MISSING, foreign=foreign)
    try:
        findings = _validate_envelope(current[0], route, task_id,
                                      round_number, reviewer_logical)
    except _Refused as exc:
        return _outcome(OUTCOME_REFUSED, exc.problem, foreign=foreign)
    return _outcome(OUTCOME_RECORDED, findings=findings, foreign=foreign)


# -- the task reference ---------------------------------------------------------

#: The task record key a gate writes (``last_review_findings``) holds exactly
#: these keys; the reader (``target_runtime.review_record``) re-checks them.
REFERENCE_KEYS = ("state", "problem", "file", "sha256", "count", "round")


def task_reference(state, problem, round_number, name=None, digest=None,
                   count=None):
    """The task's reference to one round's findings outcome: a RECORDED
    outcome names its sibling file, digest and count; any other carries its
    code and nothing else."""
    return {"state": state, "problem": problem, "file": name,
            "sha256": digest, "count": count, "round": round_number}


# -- the persisted record -----------------------------------------------------

def build_record(route, task_id, round_number, reviewer_logical,
                 reviewer_agent, preamble_bytes, findings):
    """The record a gate persists for a RECORDED outcome."""
    return validate_record({
        "schema": RECORD_SCHEMA, "version": CONTRACT_VERSION,
        "task_id": task_id, "round": round_number,
        "reviewer_logical": reviewer_logical,
        "reviewer_agent": reviewer_agent,
        "candidate_sha256": route["candidate_sha256"],
        "route_nonce": route["nonce"], "routed_at": route["routed_at"],
        "preamble_sha256": hashlib.sha256(preamble_bytes).hexdigest(),
        "linkage": LINKAGE, "provenance": PROVENANCE,
        "count": len(findings), "findings": [dict(f) for f in findings]})


def encode_record_unchecked(record):
    return (json.dumps(record, indent=2, sort_keys=True, ensure_ascii=False)
            + "\n").encode("utf-8")


def encode_record(record):
    """The exact bytes a gate writes (validated, within MAX_RECORD_BYTES)."""
    data = encode_record_unchecked(validate_record(record))
    if len(data) > MAX_RECORD_BYTES:
        raise RecordRefused("the record exceeds %d bytes" % MAX_RECORD_BYTES)
    return data


def _record_finding_ok(finding, index):
    if type(finding) is not dict or set(finding) != set(FINDING_KEYS):
        return False
    area, line = finding["area"], finding["line"]
    return (finding["index"] == index and type(finding["index"]) is int
            and finding["severity"] in SEVERITIES
            and (area is None or (type(area) is str
                                  and len(area) <= MAX_AREA_CHARS
                                  and _AREA.fullmatch(area) is not None
                                  and ".." not in area.split("/")
                                  and "" not in area.split("/")))
            and (line is None or (type(line) is int and area is not None
                                  and 1 <= line <= MAX_LINE_NUMBER))
            and type(finding["summary"]) is str
            and len(finding["summary"]) <= MAX_SUMMARY_CHARS
            and _summary_ok(finding["summary"]))


def validate_record(record):
    """``record`` when it is EXACTLY this contract's record; raises
    ``RecordRefused`` otherwise. Never repairs."""
    def refuse(why):
        raise RecordRefused(why)
    if type(record) is not dict or set(record) != set(RECORD_KEYS):
        refuse("keys")
    if record["schema"] != RECORD_SCHEMA or (
            record["version"] != CONTRACT_VERSION):
        refuse("schema")
    for name, pattern in (("task_id", _TASK_ID),
                          ("reviewer_logical", _NAME),
                          ("reviewer_agent", _NAME),
                          ("candidate_sha256", _DIGEST),
                          ("route_nonce", _NONCE),
                          ("preamble_sha256", _DIGEST)):
        if type(record[name]) is not str or pattern.fullmatch(
                record[name]) is None:
            refuse(name)
    if type(record["round"]) is not int or record["round"] < 1:
        refuse("round")
    if type(record["routed_at"]) is not int or record["routed_at"] < 0:
        refuse("routed_at")
    if record["linkage"] != LINKAGE or record["provenance"] != PROVENANCE:
        refuse("linkage or provenance")
    findings = record["findings"]
    if type(findings) is not list or len(findings) > MAX_FINDINGS or (
            type(record["count"]) is not int) or record["count"] != len(
                findings):
        refuse("count")
    for index, finding in enumerate(findings, 1):
        if not _record_finding_ok(finding, index):
            refuse("finding %d" % index)
    aggregate = sum(len(f["summary"].encode("utf-8"))
                    + len((f["area"] or "").encode("utf-8"))
                    for f in findings)
    if aggregate > MAX_FINDINGS_AGGREGATE_BYTES:
        refuse("aggregate")
    return record


def preamble_digest(preamble_bytes):
    return hashlib.sha256(preamble_bytes).hexdigest()
