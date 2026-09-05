"""Deterministic reviewed-candidate identity.

The candidate is the STAGED index relative to a base commit: exactly what
one commit would produce. Its identity is a framed digest over the
ordered changed entries only — per entry ``status`` (A/M/D), ``mode``
(after; before for a deletion), ``blob`` (after; before for a deletion),
and the path bytes — with NO tree hash and NO unrelated base file, so the
same candidate on two disjoint bases yields ONE identity. That is what
makes "the exact reviewed candidate survives safe base drift" provable
rather than asserted.

Input is ``git diff-index --cached --raw --abbrev=40 --no-renames -z``
(or ``diff-tree -r`` with the same flags once the commit exists). ``-z``
carries raw path bytes with no quoting ambiguity; ``--no-renames`` makes a
rename a deletion plus an addition, which is content- and path-exact.
Ordering is explicit: entries are sorted by the UTF-8 byte sequence of
the path, never by git's print order. Refused, each with its own problem
code: type changes and unmerged/unknown statuses, gitlinks (submodules),
non-UTF-8 or line-structured paths, duplicates, an empty candidate, and
more than ``MAX_CANDIDATE_ENTRIES``.

``compare`` reports the FIRST difference in path order with a distinct
code — missing path, extra path, status, mode, content — never one
collapsed code.
"""

from workflow_authority.digest import framed_digest
from workflow_authority.record import path_character_problem

from pr_delivery.authorization import (
    CANDIDATE_MODES,
    CANDIDATE_STATUS_ADDED,
    CANDIDATE_STATUS_DELETED,
    CANDIDATE_STATUS_MODIFIED,
    MAX_CANDIDATE_ENTRIES,
    ZERO_OID,
)

PROBLEM_RAW_FORMAT = "pr_delivery_candidate_raw_format"
PROBLEM_STATUS = "pr_delivery_candidate_bad_status"
PROBLEM_SUBMODULE = "pr_delivery_candidate_submodule"
PROBLEM_PATH = "pr_delivery_candidate_path"
PROBLEM_DUPLICATE = "pr_delivery_candidate_duplicate_path"
PROBLEM_EMPTY = "pr_delivery_candidate_empty"
PROBLEM_TOO_MANY = "pr_delivery_candidate_too_many"
PROBLEM_PATH_MISSING = "pr_delivery_candidate_path_missing"
PROBLEM_PATH_EXTRA = "pr_delivery_candidate_path_extra"
PROBLEM_STATUS_CHANGED = "pr_delivery_candidate_status_changed"
PROBLEM_MODE_CHANGED = "pr_delivery_candidate_mode_changed"
PROBLEM_CONTENT_CHANGED = "pr_delivery_candidate_content_changed"

_GITLINK_MODE = "160000"


class CandidateError(Exception):
    """The candidate cannot be identified exactly; message actionable."""

    def __init__(self, message, problem):
        super(CandidateError, self).__init__(message)
        self.problem = problem


def _entry_from_raw(meta, path_bytes):
    fields = meta.split(b" ")
    if len(fields) != 5 or not fields[0].startswith(b":"):
        raise CandidateError(
            "unrecognized raw diff record %r" % (meta,), PROBLEM_RAW_FORMAT
        )
    old_mode = fields[0][1:].decode("ascii", "replace")
    new_mode = fields[1].decode("ascii", "replace")
    old_blob = fields[2].decode("ascii", "replace")
    new_blob = fields[3].decode("ascii", "replace")
    status = fields[4].decode("ascii", "replace")
    if status not in (CANDIDATE_STATUS_ADDED, CANDIDATE_STATUS_MODIFIED,
                      CANDIDATE_STATUS_DELETED):
        raise CandidateError(
            "candidate entry %r has status %r; only A, M and D are"
            " deliverable (type changes, unmerged and unknown entries fail"
            " closed)" % (path_bytes, status),
            PROBLEM_STATUS,
        )
    if status == CANDIDATE_STATUS_DELETED:
        mode, blob = old_mode, old_blob
    else:
        mode, blob = new_mode, new_blob
    if mode == _GITLINK_MODE or old_mode == _GITLINK_MODE:
        raise CandidateError(
            "candidate entry %r is a gitlink (submodule); refused"
            % (path_bytes,),
            PROBLEM_SUBMODULE,
        )
    if mode not in CANDIDATE_MODES:
        raise CandidateError(
            "candidate entry %r has mode %r; only %s are deliverable"
            % (path_bytes, mode, ", ".join(CANDIDATE_MODES)),
            PROBLEM_STATUS,
        )
    if len(blob) != 40 or any(ch not in "0123456789abcdef" for ch in blob):
        raise CandidateError(
            "candidate entry %r blob %r is not a full object id"
            % (path_bytes, blob),
            PROBLEM_RAW_FORMAT,
        )
    if blob == ZERO_OID:
        raise CandidateError(
            "candidate entry %r has no resolved blob (unstaged or"
            " intent-to-add); stage the exact candidate first"
            % (path_bytes,),
            PROBLEM_RAW_FORMAT,
        )
    try:
        path = path_bytes.decode("utf-8")
    except UnicodeDecodeError:
        raise CandidateError(
            "candidate path %r is not valid UTF-8; refused" % (path_bytes,),
            PROBLEM_PATH,
        )
    reason = path_character_problem(path)
    if reason is not None or not path:
        raise CandidateError(
            "candidate path %r %s; refused" % (path, reason or "is empty"),
            PROBLEM_PATH,
        )
    return {"path": path, "status": status, "mode": mode, "blob": blob}


def parse_raw_z(data):
    """Parse ``--raw -z`` output into the ordered entry list, or refuse."""
    if not isinstance(data, (bytes, bytearray)):
        raise CandidateError("raw diff must be bytes", PROBLEM_RAW_FORMAT)
    tokens = bytes(data).split(b"\0")
    if tokens and tokens[-1] == b"":
        tokens.pop()
    if len(tokens) % 2 != 0:
        raise CandidateError(
            "raw diff record stream is not meta/path pairs (a rename or"
            " copy record would carry two paths; --no-renames is required)",
            PROBLEM_RAW_FORMAT,
        )
    entries = []
    for index in range(0, len(tokens), 2):
        entries.append(_entry_from_raw(tokens[index], tokens[index + 1]))
    if not entries:
        raise CandidateError(
            "the candidate is empty (nothing staged relative to the base)",
            PROBLEM_EMPTY,
        )
    if len(entries) > MAX_CANDIDATE_ENTRIES:
        raise CandidateError(
            "the candidate has %d entries; the hard bound is %d"
            % (len(entries), MAX_CANDIDATE_ENTRIES),
            PROBLEM_TOO_MANY,
        )
    entries.sort(key=lambda entry: entry["path"].encode("utf-8"))
    for previous, current in zip(entries, entries[1:]):
        if previous["path"] == current["path"]:
            raise CandidateError(
                "candidate path %r appears twice" % (current["path"],),
                PROBLEM_DUPLICATE,
            )
    return entries


def identity_digest(entries):
    """The framed sha256 over the ordered entries (four parts each)."""
    parts = []
    for entry in entries:
        parts.append(entry["status"].encode("ascii"))
        parts.append(entry["mode"].encode("ascii"))
        parts.append(entry["blob"].encode("ascii"))
        parts.append(entry["path"].encode("utf-8"))
    return framed_digest(parts)


def compare(bound_entries, live_entries):
    """``(problem, detail)`` for the first difference in path order, or
    ``(None, None)`` when the live candidate IS the bound candidate."""
    bound = {entry["path"]: entry for entry in bound_entries}
    live = {entry["path"]: entry for entry in live_entries}
    for path in sorted(set(bound) | set(live),
                       key=lambda item: item.encode("utf-8")):
        if path not in live:
            return PROBLEM_PATH_MISSING, "bound path %r is absent" % path
        if path not in bound:
            return PROBLEM_PATH_EXTRA, "extra path %r is present" % path
        expected, actual = bound[path], live[path]
        if expected["status"] != actual["status"]:
            return (
                PROBLEM_STATUS_CHANGED,
                "path %r status %s became %s"
                % (path, expected["status"], actual["status"]),
            )
        if expected["mode"] != actual["mode"]:
            return (
                PROBLEM_MODE_CHANGED,
                "path %r mode %s became %s"
                % (path, expected["mode"], actual["mode"]),
            )
        if expected["blob"] != actual["blob"]:
            return PROBLEM_CONTENT_CHANGED, "path %r content changed" % path
    return None, None


def paths_from_raw_z(data):
    """Every path named by a ``--raw -z`` stream, decoded, unordered
    and unvalidated beyond UTF-8 — used for the base-drift overlap proof
    where the base's OWN changes are not the candidate."""
    tokens = bytes(data).split(b"\0")
    if tokens and tokens[-1] == b"":
        tokens.pop()
    if len(tokens) % 2 != 0:
        raise CandidateError(
            "raw diff record stream is not meta/path pairs",
            PROBLEM_RAW_FORMAT,
        )
    paths = []
    for index in range(1, len(tokens), 2):
        try:
            paths.append(tokens[index].decode("utf-8"))
        except UnicodeDecodeError:
            raise CandidateError(
                "base-changed path %r is not valid UTF-8" % (tokens[index],),
                PROBLEM_PATH,
            )
    return paths


def overlaps(candidate_paths, changed_paths):
    """Conflicting ``(candidate_path, changed_path)`` pairs: an exact
    match, or one path being a directory prefix of the other (a file
    replacing a directory or vice versa is not disjoint even though no
    path string is shared)."""
    conflicts = []
    for mine in candidate_paths:
        for theirs in changed_paths:
            if (
                mine == theirs
                or theirs.startswith(mine + "/")
                or mine.startswith(theirs + "/")
            ):
                conflicts.append((mine, theirs))
    return conflicts
