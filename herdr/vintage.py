"""WHEN is this true of? — task vintage for every rendered field.

THE CLASS THIS CLOSES
---------------------

Every field on the observation surface was individually `available` and
individually WRONG ABOUT WHEN. `herdctl observe` rendered
``Mission [available]`` — the March-2026 vendor-packet objective from a
task two tasks earlier — directly above ``Task [available]``, which was
correct and current. Both carried the same marker. One render, two
ages, one authority.

Liveness asks IS THIS TRUE. Vintage asks WHEN IS THIS TRUE OF. A field
can be current, correct, and authoritative about the wrong moment, and
that is invisible precisely because every field looks healthy.

TWO BRANCHES, AND THE THIRD IS THE TRAP
---------------------------------------

R-46 AJ-1 gives exactly two: a rendered field CARRIES the identity of
the task it describes, or it is OMITTED. The attractive third branch —
render it with a caveat — is what produced the specimen. ``[available]``
IS a caveat slot. It was written to mean "readable" and it was read as
"current".

So `renders` returns False for UNKNOWN, and the renderer drops the
field. A field whose vintage is unestablished does not appear.
That is a deliberate loss of information, and it is the right loss: a reader shown less asks, and a reader who sees a stale
objective under a healthy marker does not.

WHAT IS AUTHORITATIVE FOR "WHAT IS RUNNING NOW"
-----------------------------------------------

`task.json`, and within this module it is the sole authority (AJ-2). It is the artifact the runtime
writes when a task starts and updates when it ends; every other
artifact naming a task id is a DESCRIPTION of some task, not a claim
about which one is current. `current_task` says so explicitly and
reports every artifact that DISAGREES rather than reconciling them —
a disagreement is evidence about the herd, and silently preferring one
side would destroy it.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

#: The field describes the task that is running now.
VINTAGE_CURRENT = "current"
#: The field describes a DIFFERENT, earlier task. It may render, and it
#: renders as prior/superseded — as current (AJ-3).
VINTAGE_PRIOR = "prior"
#: The field's task identity could not be established. It does NOT
#: render (AJ-1, branch two).
VINTAGE_UNKNOWN = "unknown"

VINTAGES = (VINTAGE_CURRENT, VINTAGE_PRIOR, VINTAGE_UNKNOWN)

#: How `current_task` learned which task is running.
SOURCE_TASK_JSON = "task.json"
SOURCE_NONE = "none"

#: A task id as this repository writes them: UTCSTAMP-SHORTHEX.
TASK_ID_RE = re.compile(r"\d{8}-\d{6}-[0-9a-f]{6}")

#: Line 1 of `task-checkpoint.md`, which is the only place that file
#: names the task it belongs to.
CHECKPOINT_HEADING_RE = re.compile(
    r"^#\s*Task checkpoint\s*[—-]\s*(" + TASK_ID_RE.pattern + r")"
)

#: Statuses that mean the task is over. A checkpoint or mission naming
#: one of these describes a COMPLETE prior task (AJ-3).
TERMINAL_TASK_STATUSES = ("COMPLETE", "COMPLETED", "ABANDONED", "FAILED")


class CurrentTask:
    """Which task is running now, and who disagrees.

    `disagreements` is a list of ``(artifact, task_id)`` for every
    artifact that names a DIFFERENT task. It is reported, resolved: this object says what is authoritative and what
    conflicts with it, and leaves the conflict visible.
    """

    __slots__ = ("task_id", "status", "source", "disagreements")

    def __init__(self, task_id, status, source, disagreements=None):
        self.task_id = task_id
        self.status = status
        self.source = source
        self.disagreements = list(disagreements or ())

    @property
    def known(self):
        return isinstance(self.task_id, str) and bool(self.task_id)

    def as_dict(self):
        return {
            "task_id": self.task_id,
            "status": self.status,
            "source": self.source,
            "disagreements": [
                {"artifact": artifact, "task_id": task_id}
                for artifact, task_id in self.disagreements
            ],
        }


def task_id_of_text(text):
    """The task id a checkpoint-shaped document names, or None.

    LINE ONE ONLY, and deliberately: a checkpoint body quotes task ids from other tasks all the time —
    prior work, superseded rounds, cited evidence — and scanning the
    whole document would let one of them be mistaken for the document's
    own subject. The heading is
    the one place the document says what it is ABOUT.
    """
    if not isinstance(text, str) or not text:
        return None
    first = text.splitlines()[0] if text.splitlines() else ""
    match = CHECKPOINT_HEADING_RE.match(first.strip())
    return match.group(1) if match else None


def task_id_of_document(data):
    """The task id a JSON artifact claims, or None.

    None is the ordinary answer for `mission.json`, which carries no
    task id at all — and under AJ-1 that is exactly why the mission
    field does not render. The absence is the finding, not a gap to
    paper over with a default.
    """
    if not isinstance(data, dict):
        return None
    for key in ("task_id", "task", "id"):
        value = data.get(key)
        if isinstance(value, str) and TASK_ID_RE.fullmatch(value):
            return value
    return None


def _read_json(path):
    try:
        return json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return None


def _read_text(path):
    try:
        return Path(path).read_text(errors="replace")
    except OSError:
        return None


def current_task(root):
    """Which task is running now (AJ-2), and what disagrees with it.

    EXPLICIT: `task.json` is the authority and the only authority. The
    checkpoint and the mission are consulted ONLY to report
    disagreement — a restart that learned "what am I doing" from the
    checkpoint is exactly specimen 2, in which a COMPLETE prior task's
    id sat on line 1 and would have been resumed.

    Returns a `CurrentTask`. When `task.json` is absent or unreadable
    the task id is None, which makes every other field's vintage
    UNKNOWN and therefore unrendered. That is the fail-closed
    direction: an observation that is unable to say which task is running should
    show less, not guess.
    """
    state = Path(root) / ".herd" / "state"
    data = _read_json(state / "task.json")
    task_id = None
    status = None
    if isinstance(data, dict):
        candidate = data.get("id")
        if isinstance(candidate, str) and candidate:
            task_id = candidate
        raw_status = data.get("status")
        if isinstance(raw_status, str) and raw_status:
            status = raw_status
    source = SOURCE_TASK_JSON if task_id else SOURCE_NONE

    disagreements = []
    checkpoint = task_id_of_text(_read_text(state / "task-checkpoint.md"))
    if checkpoint and checkpoint != task_id:
        disagreements.append(("task-checkpoint.md", checkpoint))
    mission_id = task_id_of_document(_read_json(state / "mission.json"))
    if mission_id and mission_id != task_id:
        disagreements.append(("mission.json", mission_id))
    return CurrentTask(task_id, status, source, disagreements)


def classify(field_task_id, current_task_id):
    """The vintage of a field describing ``field_task_id``.

    UNKNOWN when either side is missing, and both directions matter: a field with no task id has no vintage, and a field WITH one still
    has no vintage when the observer is unable to say which task is
    current.
    Comparing against an unknown current task would let every field
    read as PRIOR, which is a different wrong answer rather than a
    safer one.
    """
    if not isinstance(field_task_id, str) or not field_task_id:
        return VINTAGE_UNKNOWN
    if not isinstance(current_task_id, str) or not current_task_id:
        return VINTAGE_UNKNOWN
    return (VINTAGE_CURRENT if field_task_id == current_task_id
            else VINTAGE_PRIOR)


def renders(vintage):
    """Whether a field of this vintage may appear on a surface.

    THE TWO-BRANCH RULE, in one place so no renderer can invent a
    third. CURRENT and PRIOR both render — PRIOR carries its own task
    id and its superseded label, which is what makes it safe. UNKNOWN
    does not render at all.
    """
    return vintage in (VINTAGE_CURRENT, VINTAGE_PRIOR)


def is_terminal_status(status):
    if not isinstance(status, str):
        return False
    return status.strip().upper() in TERMINAL_TASK_STATUSES


def vintage_label(vintage, field_task_id, status=None):
    """The text a surface puts beside a rendered field.

    A PRIOR field says SUPERSEDED and names the task it belongs to, so
    the reader gets the age and the identity in the same glance. There
    is no label for UNKNOWN because there is no render for UNKNOWN.
    """
    if vintage == VINTAGE_CURRENT:
        return "current %s" % field_task_id
    if vintage == VINTAGE_PRIOR:
        completed = " COMPLETE" if is_terminal_status(status) else ""
        return "SUPERSEDED — belongs to prior task %s%s" % (
            field_task_id, completed,
        )
    raise ValueError(
        "there is no label for an UNKNOWN vintage: a field whose task"
        " identity cannot be established is OMITTED, not captioned"
        " (R-46 AJ-1). Captioning it is the third branch that produced"
        " the specimen"
    )


#: Where an append-only artifact's CURRENT truth lives (AJ-4).
#:
#: An append-only log presents its STALEST content FIRST, which is how
#: a status file carried a header saying two increments were delegated
#: while its tail recorded five closed. A reader who stops at the top
#: reads the oldest state in the file and has no way to know it.
APPEND_ONLY_POINTER = (
    "APPEND-ONLY: the newest entry is at the END of this file. The"
    " header describes the state when the file was created, not now."
)


def append_only_notice(artifact, current_truth):
    """One line telling a reader where this artifact's truth is NOW."""
    return "%s — %s Current truth: %s." % (
        artifact, APPEND_ONLY_POINTER, current_truth,
    )


# --- AW-2: selection is task-scoped, in every case -------------------


class TaskScopeRequired(ValueError):
    """A record was selected without saying which task it belongs to.

    `reviews/` is TASK-MIXED and the FILENAME is the only carrier of
    task identity, so a selector keyed on round number alone returns a
    confident wrong answer with no warning. Raising is the whole
    point: the caller has to supply the scope, and is unable to get an answer
    by omitting it.
    """


ROUND_FILE_RE = re.compile(
    r"(?P<task>" + TASK_ID_RE.pattern + r")-round-(?P<round>\d+)\.md\Z"
)


def round_files_for_task(names, task_id):
    """``(round_number, name)`` for THIS task only, ascending.

    Raises `TaskScopeRequired` when no task id is given. The domain is
    the names passed in; this reads no directory of its own, so a
    caller decides what it is selecting over and can be tested against
    a mixed listing.
    """
    if not isinstance(task_id, str) or not task_id:
        raise TaskScopeRequired(
            "round selection requires a task id: the reviews directory"
            " holds rounds from every task and the filename is the"
            " only thing that distinguishes them (R-59 AW-2)"
        )
    found = []
    for name in names:
        match = ROUND_FILE_RE.fullmatch(str(name))
        if match and match.group("task") == task_id:
            found.append((int(match.group("round")), str(name)))
    found.sort()
    return found


def latest_round_for_task(names, task_id):
    """The highest-numbered round belonging to THIS task, or None."""
    rounds = round_files_for_task(names, task_id)
    return rounds[-1] if rounds else None


# --- AY-4: how strongly is a role bound? -----------------------------

#: The stable fields a binding can carry. Imported from `identity` at
#: call time rather than retyped, so the two is unable to drift.
def binding_strength(binding):
    """``(strength, captured, missing)`` for one role binding.

    R-61 AY-4, carried from I2c's residual: `identity.binding_gap`
    requires a NON-EMPTY stable mapping, not a COMPLETE one. A record
    missing `pane_id` or `cwd` still binds, and `classify` then
    compares fewer fields — a smaller identity, silently. The binding
    is not wrong; it is WEAKER, and until now no surface said so.

    `strength` is `complete` when every stable field was captured,
    `partial` when some were, and `none` when the binding carries no
    stable identity at all.
    """
    from . import identity
    if not isinstance(binding, dict):
        return "none", [], list(identity.STABLE_FIELDS)
    stable = binding.get("stable")
    stable = stable if isinstance(stable, dict) else {}
    captured = [
        field for field in identity.STABLE_FIELDS
        if isinstance(stable.get(field), str) and stable.get(field)
    ]
    missing = [
        field for field in identity.STABLE_FIELDS if field not in captured
    ]
    if not captured:
        return "none", captured, missing
    return ("complete" if not missing else "partial"), captured, missing
