"""The read-only delivery HANDOFF facts of a task-native mission.

What a person needs to know BEFORE they run the existing human delivery ceremony for a Herdr task, as OBSERVED facts, each with its reason:
where the task's worktree is (its git directory), whether that worktree's HEAD is on a named branch, and whether the task has the evidence the
ceremony collects (a COMPLETE task and the latest canonical APPROVE, gathered by the EXISTING collector ``herdr.delivery_evidence.collect``).

THIS CONFERS NOTHING. It is a status read. It mints no record, authorizes nothing, stages nothing, changes no branch, writes nothing and starts no
process (the evidence is read through the catalog's hardened reader and validated by the collector's own rule; HEAD is one bounded read). It is NEVER a statement that a delivery record may be
created: the exact staged candidate a ``pull_request`` ceremony needs cannot be determined without a git process, so it is ALWAYS reported as
``not_determined`` here, and a ``blockers`` list that is empty still does not say the ceremony will accept the worktree.

HOW THE COLLECTOR IS REUSED, AND WHAT IS NOT. ``herdr.delivery_evidence.collect`` reads UNBOUNDED and follows symlinks, and its import closure
reaches ``herdr.observe`` (which imports ``subprocess`` and ``herdr.runtime``); none of that may be brought into this bounded reader. So
``collect`` is NOT called. Its VALIDATION, the one rule for "the current task's COMPLETE plus the latest canonical APPROVE", was split out as
``delivery_evidence.evaluate`` (``collect`` calls it too, unchanged in behaviour), and THIS module reads under the catalog's own guarantees and
hands ``evaluate`` the bytes: every ``.herd/state`` file through ``state_artifact.read_state_artifact`` (one O_NOFOLLOW open, a single link, the bound enforced on the
bytes actually read, the directory chain re-checked after the read), HEAD through ``prepare.read_workspace_instruction`` (the primitive underneath
it: O_NOFOLLOW | O_NONBLOCK, a regular-file check on the descriptor, a single link; HEAD lives under the git directory, not ``.herd/state``), and
the reviews directory listed by a bounded scan of a chain that must resolve to itself. Nothing is stat-checked and then read. A file past the bound is reported ``unreadable`` (a limit of THIS read), never ``refused``
(the collector's verdict), because the ceremony's own collector has no such bound and might decide differently.

Where the collector legitimately refuses (no COMPLETE, no canonical APPROVE, an unreadable record) its own reason is reported VERBATIM (bounded);
missing evidence is never synthesized. A ceremony's ``--workflow-id`` has no source here: a Herdr task is not a workflow-store Mission, so there is
no workflow id to offer, and the block says the person supplies one (``workflow_id: human_supplied``) rather than naming a guess.

THE DECLARATION (``link_basis``). The return path ("a record made from this task in this worktree comes back to THIS task") is a property of how this
producer associates records, and it must hold for a fresh task that has NO record yet. So the block states, always and independently of any link, the
basis this producer links Herdr tasks by. A client may make the canonical return-path statement only from that explicit value; silence (a producer
that sends no block) or an unrecognised value is not a declaration.

Why this pair of facts matters for the return path: the ceremony seals ``repository.git_dir_realpath`` (from the live worktree) and
``workflow_identity.engineering_task_id`` (from the collected evidence) into the record it mints, and the catalog links a Herdr task by exactly
that pair (``record_task_worktree_binding``). So the task id the collector would seal is checked here to be THIS mission's own; if it is not, the
evidence is reported as refused, because a record made from it would not come back to this task.
"""

import os

from herdr import delivery_evidence
from mission_sources import contract_v2
from target_runtime import prepare as prepare_module
from target_runtime import state_artifact
from target_runtime.workspace_origin import workspace_git_dir

#: The lifecycle model this describes (spelled in two literals, as the mission_sources package does, to keep identifier scans honest).
TASK_MODEL = "he" "rdr.task/1"
#: HEAD is a short file: a ``ref: refs/heads/<name>`` line or one object id.
MAX_HEAD_BYTES = 512
#: How many names the reviews directory may hold before it is reported unreadable rather than scanned further.
MAX_REVIEW_ENTRIES = 2048
STATE_SUBDIRS = (".herd", "state")
REVIEW_SUBDIRS = (".herd", "state", "reviews")
TASK_STATE_NAME = "task.json"
MAX_REASON_CHARS = 400
BRANCH_NAMED = "named"
BRANCH_DETACHED = "detached"
BRANCH_UNREADABLE = "unreadable"
EVIDENCE_COLLECTED = "collected"
EVIDENCE_REFUSED = "refused"
EVIDENCE_UNREADABLE = "unreadable"
BLOCKER_GIT_DIR = "git_dir_not_read"
BLOCKER_HEAD_DETACHED = "head_detached"
BLOCKER_HEAD_UNREADABLE = "head_not_read"
BLOCKER_EVIDENCE = "evidence_not_collected"
BLOCKER_TASK_ID = "task_id_not_recorded"
BLOCKERS = (BLOCKER_GIT_DIR, BLOCKER_HEAD_DETACHED, BLOCKER_HEAD_UNREADABLE,
            BLOCKER_EVIDENCE, BLOCKER_TASK_ID)
STATE_OBSERVED = "observed"
STATE_NOT_APPLICABLE = "not_applicable"
STATE_NOT_REPORTED = "not_reported"
#: The association basis THIS producer links a Herdr task by, DECLARED here even when nothing is linked. A link's own ``basis`` is null when it is
#: unlinked, so without this a fresh task with no record could not be told how its record would come back. It is the contract's own constant, the
#: same one ``mission_delivery_link`` emits, so what is declared and what is linked by cannot diverge. A client treats ONLY this exact value as the
#: declaration; its absence (an older producer sends no block) or any other value is NOT a declaration.
LINK_BASIS = contract_v2.MISSION_LINK_BASIS_TASK_WORKTREE
STAGED_NOT_DETERMINED = "not_determined"
WORKFLOW_ID_HUMAN_SUPPLIED = "human_supplied"

_HEX = "0123456789abcdef"


def empty(state):
    """A block that carries no facts: ``not_applicable`` (this model has no handoff) or ``not_reported`` (this producer could not say)."""
    return {"state": state, "link_basis": None, "repository": None,
            "engineering_task_id": None,
            "git_dir_realpath": None, "branch": None, "evidence": None,
            "staged_candidate": None, "workflow_id": None, "blockers": []}


def expected_blockers(task_id, git_dir, branch, evidence):
    """The blockers the facts imply, in the fixed order of ``BLOCKERS``. The contract validator recomputes this, so a producer cannot send a
    shorter list than its own facts imply."""
    found = []
    if git_dir is None:
        found.append(BLOCKER_GIT_DIR)
    if branch["state"] == BRANCH_DETACHED:
        found.append(BLOCKER_HEAD_DETACHED)
    elif branch["state"] == BRANCH_UNREADABLE:
        found.append(BLOCKER_HEAD_UNREADABLE)
    if evidence["state"] != EVIDENCE_COLLECTED:
        found.append(BLOCKER_EVIDENCE)
    if task_id is None:
        found.append(BLOCKER_TASK_ID)
    return [b for b in BLOCKERS if b in found]


def _bounded(text):
    text = " ".join(str(text).split())
    return text if len(text) <= MAX_REASON_CHARS else text[:MAX_REASON_CHARS - 1] + "…"


def branch_of(git_dir):
    """``{"state", "ref"}`` for the HEAD in ``git_dir``: ``named`` only for ``ref: refs/heads/<name>`` (what the ceremony requires),
    ``detached`` for one bare object id, otherwise ``unreadable``. One bounded read; no process."""
    if git_dir is None:
        return {"state": BRANCH_UNREADABLE, "ref": None}
    # The repository's ONE hardened read (``prepare.read_workspace_instruction``), not an ordinary open: one O_NOFOLLOW | O_NONBLOCK open (a
    # symlink is refused, and a FIFO opens instead of waiting for a writer, so the regular-file check below actually runs), an fstat of the
    # DESCRIPTOR that must say regular file with a single link, and the byte bound enforced on the bytes read from that same descriptor. A
    # byte cap alone bounds how much is read, not whether the open can block or follow a link.
    status, count, _digest, text = prepare_module.read_workspace_instruction(git_dir, "HEAD")
    if status != prepare_module.INSTRUCTION_READ or count > MAX_HEAD_BYTES:
        return {"state": BRANCH_UNREADABLE, "ref": None}
    line = text.strip()
    if line.startswith("ref: refs/heads/") and len(line) > len("ref: refs/heads/") and "\n" not in line:
        return {"state": BRANCH_NAMED, "ref": line[len("ref: "):]}
    if len(line) in (40, 64) and all(ch in _HEX for ch in line):
        return {"state": BRANCH_DETACHED, "ref": None}
    return {"state": BRANCH_UNREADABLE, "ref": None}


def _read_bytes(root, subdirs, name, trouble):
    """The bytes of one ``.herd/state`` artifact through the hardened reader, or ``OSError`` (and the reason in ``trouble``) when it was
    not read. An absent file is ``FileNotFoundError``, as the collector's own reads raise."""
    status, _count, _digest, text = state_artifact.read_state_artifact(root, subdirs, name)
    if status == prepare_module.INSTRUCTION_READ:
        return text.encode("utf-8")
    if status == prepare_module.INSTRUCTION_ABSENT:
        raise FileNotFoundError("%s is absent" % name)
    trouble.append(status)
    raise OSError("%s was not read under this office's bounds (%s)" % (name, status))


def _review_names(root, trouble):
    """The names under ``.herd/state/reviews``, from a bounded scan of a directory chain that resolves to itself before AND after, or
    ``OSError``."""
    reviews = os.path.join(root, *REVIEW_SUBDIRS)
    if os.path.realpath(reviews) != reviews:
        trouble.append("directory_chain_not_plain")
        raise OSError("the reviews directory is not a plain directory chain under the workspace")
    names = []
    with os.scandir(reviews) as entries:
        for entry in entries:
            if len(names) >= MAX_REVIEW_ENTRIES:
                trouble.append("directory_over_entry_bound")
                raise OSError("the reviews directory holds more than %d entries" % MAX_REVIEW_ENTRIES)
            names.append(entry.name)
    if os.path.realpath(reviews) != reviews:
        trouble.append("directory_chain_changed")
        raise OSError("the reviews directory changed while it was read")
    return sorted(names)


def evidence_of(workspace, task_id):
    """``{"state", "reason"}`` from the EXISTING validation (``delivery_evidence.evaluate``) over bytes read under this module's bounds (see
    the module doc). The collector's own refusal reason is kept verbatim (bounded). The task id it would seal must be this mission's own."""
    root = os.path.realpath(workspace)
    trouble = []
    try:
        task_bytes = _read_bytes(root, STATE_SUBDIRS, TASK_STATE_NAME, trouble)
    except OSError as exc:
        # nothing was read: an absent or refused task record. The collector would say the same in its own words
        return {"state": EVIDENCE_UNREADABLE, "reason": _bounded("task state unreadable: %s" % exc)}
    try:
        document = delivery_evidence.evaluate(
            task_bytes, TASK_STATE_NAME,
            lambda: _review_names(root, trouble),
            lambda name: _read_bytes(root, REVIEW_SUBDIRS, name, trouble))
    except delivery_evidence.EvidenceError as exc:
        if trouble:
            return {"state": EVIDENCE_UNREADABLE,
                    "reason": _bounded("the evidence could not be read under this office's bounds (%s); the ceremony's own collector reads "
                                       "without them and may decide differently: %s" % (", ".join(trouble), exc))}
        return {"state": EVIDENCE_REFUSED, "reason": _bounded(exc)}
    sealed = document["engineering_complete"]["task_id"]
    if sealed != task_id:
        return {"state": EVIDENCE_REFUSED,
                "reason": _bounded("the evidence is for task %s, not this task %s, so a record made from it would not link here"
                                   % (sealed, task_id))}
    return {"state": EVIDENCE_COLLECTED, "reason": None}


def handoff_for_mission(mission):
    """The handoff block of one mission view (see the module doc). A model that is not a Herdr task is ``not_applicable``; a Herdr task whose
    workspace is not a readable absolute path is ``not_reported`` (unknown, never "none")."""
    if mission["lifecycle"]["model"] != TASK_MODEL:
        return empty(STATE_NOT_APPLICABLE)
    execution = mission["execution"]
    root = execution.get("workspace_realpath")
    if type(root) is not str or not os.path.isabs(root):
        return empty(STATE_NOT_REPORTED)
    task_id = execution.get("task_id")
    task_id = task_id if type(task_id) is str and task_id else None
    git_dir = workspace_git_dir(root)
    branch = branch_of(git_dir)
    if task_id is None:
        evidence = {"state": EVIDENCE_REFUSED, "reason": "this mission has no current task, so there is no task to collect evidence for"}
    else:
        evidence = evidence_of(root, task_id)
    return {"state": STATE_OBSERVED, "link_basis": LINK_BASIS, "repository": root,
            "engineering_task_id": task_id,
            "git_dir_realpath": git_dir, "branch": branch, "evidence": evidence,
            "staged_candidate": STAGED_NOT_DETERMINED,
            "workflow_id": WORKFLOW_ID_HUMAN_SUPPLIED,
            "blockers": expected_blockers(task_id, git_dir, branch, evidence)}
