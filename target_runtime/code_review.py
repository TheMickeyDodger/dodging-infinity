"""Live code review (item 3, original 20): a READ-ONLY view of what is changed
in the workspace of a currently ACTIVE assignment.

What a caller names: a Mission ref and the task id it was shown
(``task_id``), then, for one file, the snapshot it was shown and the file id
the overview listed. A Mission Core ref is ``<source_id>:mn-<hex>``. A plain
Herdr task ref is ``<source_id>:<task_id>@<root key>``. Never a path, a root,
a ref, a pathspec or a git option: the workspace comes from the catalog
record's bound ``workspace_realpath``, every path from git's own listing of
that workspace.

ELIGIBILITY is a different question from the evidence library's. That one
asks whether a recorded artifact may be shown; this one asks whether an
assignment is ACTIVE NOW. Before any git process starts, all of this must
hold or the answer REFUSES with its own code:

- the owner's last refreshed answer is fresh and lists the Mission exactly
  once, as a Mission Core record OR a plain Herdr task (``herdr.task/1``)
  whose standing is ``active`` and whose binding is ``bound`` (the catalog's
  existing corroboration: a fresh runtime snapshot, its task observed once
  at its own workspace);
- its execution is resolved and consistent, and its task is EXACTLY the task
  the caller was shown (an assignment replaced by another task in the same
  workspace is a different assignment: ``assignment_task_changed``);
- the Mission's source is still registered, read from the registry NOW, as
  the listing named it.

A COMPLETED Mission is admitted here ONLY when the caller injects a finished-execution
provider (``read(..., finished=...)``), which stands on the evidence library's record-side
admission (``artifact_content.historical_execution``); the version 2 owner (``CatalogService.code_review``)
injects it (I3), and a caller that injects none gets a COMPLETED Mission refused as not active, whatever
its record still proves.
Even then it is read ONLY when the recorded workspace's OWN task record (``.herd/state/task.json``:
one hardened no-follow read, repeated at the release check) names THIS run (the one shared
predicate, ``run_association.names_this_run``) AND is the task the verified receipt names: the
workspace is shown as it is NOW, and its ownership was checked at one instant.

WHAT IS COMPARED, exactly (``comparison`` says it in every answer):

- BASE is the commit HEAD names in the workspace, its object id shown;
  CANDIDATE is the working tree as observed at ``observed_at``.
- STAGED and UNSTAGED changes are covered together, as the net difference
  HEAD -> working tree (a file changed in the index and again after it is one
  entry, marked ``both``). UNTRACKED files git lists (ignored ones are not
  listed) are covered as additions, within the reader's bound.
- A RENAME is shown when git pairs a deleted and an added path (HEAD ->
  working tree, at least 50% similar); otherwise it is a deletion and an
  addition. An untracked file takes part in no pairing.
- NOT covered, and said so: ignored files, submodules (not entered), the
  contents of files git cannot show as text (binary, not UTF-8, over the
  bound), anything under a private location.
- A shared worktree shows the TEAM's changes in it. Nothing here attributes a
  change to the agent the caller selected: authorship is not evidenced.

MOVEMENT is the normal case. The overview is observed twice (HEAD, the branch,
the status listing, the counts, each listed file's stat) and must agree with
itself or it is retried, then refused. It carries a ``snapshot`` id over all
of that; a file read states the snapshot it belongs to, and the read is
refused (``snapshot_moved``) when the workspace is no longer that snapshot.
Before anything is returned the registry and the catalog are read AGAIN and
the workspace re-observed: an answer whose target or workspace moved while it
was read is DISCARDED. The index is deliberately not part of the snapshot's
identity (a ``git status`` by the agent rewrites it without changing the
comparison): what is staged is, through the listing.

POLICY EXCLUSIONS precede disclosure. A path under a private location (git's
own directory, Herdr's private state, agent state, credential directories,
``.env*``), under a hidden name this view does not list, or with a
credential-like name or key-material extension is listed WITHHELD: its name
and kind of change, never its counts or content. And no git read that could
hash or diff a file is ever pointed at one: before any such read the tracked
NAMES are read (the index and the tree only; no working file is opened),
every private location among them is decided, and the reads that can hash a
file (the status listing, the counts, the modes, a file's diff) are told to
LEAVE THOSE LOCATIONS OUT in git's own pathspec, so git never visits, stats
or hashes anything under one. A withheld tracked file's name and kind of
change come from reads that open no file: what is staged from the index
against HEAD, and what differs in the working tree from the INDEX'S OWN
RECORD of the file compared (here, by this module) with the file's stat:
git is never asked to compare a working file for it. The names, the private
locations, where a private path came from, and every read that could hash or
diff a file all rest on ONE private copy of the index, made once and checked
unchanged at the end; a file's diff is made through that same copy and names
its path as a file, nothing under it. Patch text is UNTRUSTED: it is data,
never rendered as markup, followed, fetched or executed.

NOTHING here writes. The git side is ``git_transport``'s live reads only
(plumbing, with the target's whole ``.git`` byte-identical before and after,
measured), the file side is the hardened no-follow reader.
"""

import hashlib
import json
import os
import re

from mission_sources import contract
from mission_sources import registry as registry_module
from target_runtime import git_transport as transport_module
from target_runtime import prepare as prepare_module
from target_runtime import run_association
from target_runtime import state_artifact

SCHEMA = "di.code_review"
VERSION = 1
ANSWER_KEYS = ("schema", "version", "state", "problem", "detail", "view",
               "mission_ref", "task_id", "snapshot", "file_id", "target",
               "comparison", "overview", "file", "observed_at", "accounting")
TARGET_KEYS = ("source_id", "worktree", "head", "branch", "detached",
               "attribution")
COMPARISON_KEYS = ("mode", "base", "candidate", "covers", "does_not_cover",
                   "filters_not_run")
OVERVIEW_KEYS = ("files", "totals")
OVERVIEW_FILE_KEYS = ("file_id", "path", "old_path", "change", "staging",
                      "state", "reason", "additions", "deletions")
TOTALS_KEYS = ("files", "additions", "deletions", "withheld", "unshown")
FILE_KEYS = ("file_id", "path", "old_path", "change", "staging", "state",
             "reason", "additions", "deletions", "mode_change", "similarity",
             "hunks", "truncated", "lines_total", "lines_retained",
             "bytes_total", "digest_sha256")
HUNK_KEYS = ("old_start", "old_lines", "new_start", "new_lines", "section",
             "lines")
LINE_KEYS = ("kind", "old", "new", "text", "clipped")
ACCOUNTING_KEYS = ("git_commands", "git_bytes", "registry_bytes",
                   "untracked_bytes")

VIEW_OVERVIEW = "overview"
VIEW_FILE = "file"
STATE_OBSERVED = "observed"
STATE_REFUSED = "refused"

#: The two attribution words an answer may carry: a shared worktree shows the
#: team's changes; this view never claims more.
ATTRIBUTION_SHARED = "shared_worktree"

MODE_HEAD_TO_WORKTREE = "head_to_worktree"
BASE_HEAD_COMMIT = "head_commit"
CANDIDATE_WORKING_TREE = "working_tree"
COVERS = ("staged", "unstaged", "untracked", "submodule_pointers")
DOES_NOT_COVER = ("ignored", "submodule_contents", "binary_content",
                  "files_over_bound", "private_locations")

CHANGES = ("added", "modified", "deleted", "renamed", "type_changed",
           "untracked", "conflicted")
STAGINGS = ("staged", "unstaged", "both", "untracked", "conflicted")
#: What can be shown of ONE file. ``text`` has hunks; every other state is
#: explicit, with no content.
STATE_TEXT = "text"
STATE_EMPTY = "empty"
STATE_BINARY = "binary"
STATE_NOT_UTF8 = "not_utf8"
STATE_OVERSIZED = "oversized"
STATE_WITHHELD = "withheld"
STATE_UNSUPPORTED = "unsupported"
STATE_NO_CONTENT = "no_content_diff"
STATE_CONFLICTED = "conflicted"
#: A tracked submodule whose recorded commit changed: the entry compares the
#: commit it points to (shown as a pointer diff), never what is inside it.
STATE_SUBMODULE = "submodule"
FILE_STATES = (STATE_TEXT, STATE_EMPTY, STATE_BINARY, STATE_NOT_UTF8,
               STATE_OVERSIZED, STATE_WITHHELD, STATE_UNSUPPORTED,
               STATE_NO_CONTENT, STATE_CONFLICTED, STATE_SUBMODULE)
#: What is shown of a file's own changes: the states that carry hunks.
SHOWN_STATES = (STATE_TEXT, STATE_SUBMODULE)

REASON_PRIVATE = "private_location"
REASON_HIDDEN = "hidden_name_not_listed"
REASON_CREDENTIAL = "credential_like_name"
REASON_KEY_MATERIAL = "key_material_extension"
REASON_NOT_REGULAR = "untracked_not_a_regular_file"
REASON_UNREADABLE = "untracked_unreadable"
REASON_HARDLINK = "untracked_hardlinked"
REASON_TYPE_CHANGE = "type_change_not_shown"
REASON_NESTED_REPOSITORY = "nested_repository"

KIND_CONTEXT = "context"
KIND_ADD = "add"
KIND_DELETE = "del"
KIND_NO_NEWLINE = "no_newline"
LINE_KINDS = (KIND_CONTEXT, KIND_ADD, KIND_DELETE, KIND_NO_NEWLINE)

# -- bounds: module constants, never derived from input, never raised ----
#: Every ``*_CHARS`` bound below counts UTF-16 CODE UNITS: what JavaScript's
#: ``.length`` counts, which is what the office checks every one of them with,
#: and what the answer's byte budget is built on (JSON escapes one unit to at
#: most six bytes). It is NOT Python's ``len``, which counts code points: a
#: character outside the Basic Multilingual Plane (an emoji, for one) is ONE
#: code point and TWO units, so a line of 601 emoji is 601 to ``len`` and
#: 1,202 to the office, which would refuse it. ``_units`` measures and
#: ``_clip_units`` cuts in this unit, never inside a character.
#: Changed paths listed in ONE overview. Past it the overview REFUSES
#: (``too_many_changed_files``): a partial list under complete-looking totals
#: would be a silent drop.
MAX_REVIEW_FILES = 500
#: A path longer than this is not listed (the overview refuses).
MAX_REVIEW_PATH_CHARS = 1024
#: The branch name shown.
MAX_BRANCH_CHARS = 255
#: The reader's own bound for an UNTRACKED file (the hardened primitive's).
MAX_UNTRACKED_FILE_BYTES = prepare_module.MAX_INSTRUCTION_FILE_BYTES
#: What ONE untracked read can cost in real I/O: the reader pulls one byte
#: PAST its bound, and that byte is how it knows a file is over it. The budget
#: holds and charges this, not the bound, so the bytes actually read never
#: exceed ``MAX_UNTRACKED_TOTAL_BYTES``.
MAX_UNTRACKED_READ_COST_BYTES = MAX_UNTRACKED_FILE_BYTES + 1
#: All untracked bytes read for ONE overview.
MAX_UNTRACKED_TOTAL_BYTES = 16777216
#: Diff lines RETAINED for one file (the rest is counted, flagged truncated).
MAX_REVIEW_FILE_LINES = 6000
#: Line text characters RETAINED for one file.
MAX_REVIEW_FILE_CHARS = 1048576
#: One line is shown to this many characters; the rest is counted (``clipped``).
MAX_REVIEW_LINE_CHARS = 1200
#: A hunk's section heading, as git prints it.
MAX_SECTION_CHARS = 200
#: Observations that may disagree with each other before the read is refused.
MAX_OBSERVATION_ATTEMPTS = 3
MAX_DETAIL_CHARS = 240
#: A content-filter driver name listed (``filters_not_run``): the repository
#: names them, so the backend bounds each one, in the same unit as the rest.
MAX_FILTER_NAME_CHARS = 64

#: The request grammar. ``catalog_http`` re-declares it (it may not import
#: this module); parity is pinned by test.
#: Mission Core (``source:mn-<32 hex>``) or a plain Herdr task
#: (``source:<task id>@<12 hex root key>``, ``herdr_task_source.root_key``).
#: Artifact content stays Mission-Core-only; this pattern is the review
#: route's own.
MISSION_REF_PATTERN = (
    r"[a-z0-9][a-z0-9._-]{0,63}:(?:mn-[0-9a-f]{32}"
    r"|[0-9]{8}-[0-9]{6}-[0-9a-f]{6}@[0-9a-f]{12})"
)
#: A plain Herdr task's lifecycle model (``herdr_task_source.LIFECYCLE_MODEL``).
#: Re-declared: this module does not import the task-native reader.
HERDR_TASK_MODEL = "herdr.task/1"
TASK_ID_PATTERN = r"[0-9]{8}-[0-9]{6}-[0-9a-f]{6}"
SNAPSHOT_PATTERN = r"[0-9a-f]{64}"
FILE_ID_PATTERN = r"rf-[0-9a-f]{32}"

PROBLEM_CONTRACT_VERSION = "review_needs_contract_version_2"
PROBLEM_SHUT_DOWN = "owner_shut_down"
PROBLEM_BUSY = "review_read_in_progress"
PROBLEM_NOT_CORROBORATED = "no_fresh_catalog_answer"
PROBLEM_MISSION_NOT_LISTED = "mission_not_listed"
PROBLEM_MISSION_AMBIGUOUS = "mission_listed_more_than_once"
PROBLEM_SOURCE_KIND = "assignment_not_a_mission_core_record"
PROBLEM_NOT_ACTIVE = "assignment_not_active"
PROBLEM_NOT_BOUND = "assignment_binding_not_bound"
PROBLEM_TASK_CHANGED = "assignment_task_changed"
PROBLEM_UNRESOLVED = "assignment_task_or_workspace_unresolved"
PROBLEM_REGISTRY = "registry_unreadable"
PROBLEM_SOURCE_NOT_REGISTERED = "source_not_registered"
PROBLEM_REGISTRATION_CHANGED = "source_registration_changed"
PROBLEM_WORKSPACE_UNUSABLE = "workspace_not_a_readable_directory"
PROBLEM_NOT_WORKTREE_ROOT = "workspace_is_not_a_worktree_root"
PROBLEM_NO_BASE = "no_base_commit"
PROBLEM_GIT_FAILED = "git_read_failed"
PROBLEM_GIT_DEADLINE = "git_read_deadline"
PROBLEM_INVENTORY_OVER_BOUND = "inventory_over_bound"
PROBLEM_TOO_MANY_FILES = "too_many_changed_files"
PROBLEM_PATH_UNSUPPORTED = "path_not_representable"
PROBLEM_INVENTORY_INCONSISTENT = "inventory_inconsistent"
PROBLEM_BUDGET = "review_read_budget_exhausted"
PROBLEM_SNAPSHOT_MOVED = "snapshot_moved"
PROBLEM_FILE_NOT_LISTED = "file_not_in_snapshot"
PROBLEM_MOVED_DURING_READ = "workspace_moved_during_read"
PROBLEM_ASSIGNMENT_CHANGED = "assignment_changed_during_read"
PROBLEM_FILE_DIFF_OVER_BOUND = "file_diff_over_bound"
PROBLEM_DIFF_NOT_UNDERSTOOD = "diff_not_understood"
#: THE FINISHED-EXECUTION REASONS (I3 made them closed codes; the TypeScript list gained them in the same change). Three are the review route's
#: words for a refusal of the evidence library's record-side admission (``FINISHED_REFUSALS``); the fourth (I2) is this route's OWN, from its
#: workspace-side witness: no refusal of the record-side admission maps to it.
FINISHED_NOT_ESTABLISHED = "finished_execution_not_established"
FINISHED_UNVERIFIABLE = "finished_ownership_unverifiable"
FINISHED_CONTESTED = "finished_ownership_contested"
FINISHED_WORKSPACE_NOT_CORROBORATED = "finished_workspace_not_corroborated"
FINISHED_REASONS = (FINISHED_NOT_ESTABLISHED, FINISHED_UNVERIFIABLE,
                    FINISHED_CONTESTED, FINISHED_WORKSPACE_NOT_CORROBORATED)
#: The CLOSED problem vocabulary (a consumer maps every one; no default): the
#: active route's thirty codes, then the four finished-execution reasons.
PROBLEMS = (
    PROBLEM_CONTRACT_VERSION, PROBLEM_SHUT_DOWN, PROBLEM_BUSY,
    PROBLEM_NOT_CORROBORATED, PROBLEM_MISSION_NOT_LISTED,
    PROBLEM_MISSION_AMBIGUOUS, PROBLEM_SOURCE_KIND, PROBLEM_NOT_ACTIVE,
    PROBLEM_NOT_BOUND, PROBLEM_TASK_CHANGED, PROBLEM_UNRESOLVED,
    PROBLEM_REGISTRY, PROBLEM_SOURCE_NOT_REGISTERED,
    PROBLEM_REGISTRATION_CHANGED, PROBLEM_WORKSPACE_UNUSABLE,
    PROBLEM_NOT_WORKTREE_ROOT, PROBLEM_NO_BASE, PROBLEM_GIT_FAILED,
    PROBLEM_GIT_DEADLINE, PROBLEM_INVENTORY_OVER_BOUND,
    PROBLEM_TOO_MANY_FILES, PROBLEM_PATH_UNSUPPORTED,
    PROBLEM_INVENTORY_INCONSISTENT, PROBLEM_BUDGET, PROBLEM_SNAPSHOT_MOVED,
    PROBLEM_FILE_NOT_LISTED, PROBLEM_MOVED_DURING_READ,
    PROBLEM_ASSIGNMENT_CHANGED, PROBLEM_FILE_DIFF_OVER_BOUND,
    PROBLEM_DIFF_NOT_UNDERSTOOD,
) + FINISHED_REASONS

#: What the review route says for each refusal of the evidence library's record-side admission (KEY: that
#: library's own code). TOTAL in both directions (a test derives the reachable codes from its source): an
#: existing review code where one already says the thing, else one of the three record-side finished reasons.
FINISHED_REFUSALS = {
    "no_fresh_catalog_answer": PROBLEM_NOT_CORROBORATED,
    "mission_not_listed": PROBLEM_MISSION_NOT_LISTED,
    "mission_listed_more_than_once": PROBLEM_MISSION_AMBIGUOUS,
    "source_kind_has_no_artifact_content": PROBLEM_SOURCE_KIND,
    "mission_binding_not_bound": FINISHED_NOT_ESTABLISHED,
    "mission_task_or_workspace_unresolved": PROBLEM_UNRESOLVED,
    "mission_state_not_observed": FINISHED_UNVERIFIABLE,
    "cursor_not_the_listed_cursor": FINISHED_UNVERIFIABLE,
    "registry_unreadable": PROBLEM_REGISTRY,
    "source_not_registered": PROBLEM_SOURCE_NOT_REGISTERED,
    "mission_store_unavailable": FINISHED_UNVERIFIABLE,
    "mission_absent_from_store": FINISHED_UNVERIFIABLE,
    "mission_moved_since_listing": FINISHED_UNVERIFIABLE,
    "mission_task_or_workspace_changed": FINISHED_UNVERIFIABLE,
    "workspace_not_one_resolved_root": PROBLEM_UNRESOLVED,
    "request_read_budget_exhausted": PROBLEM_BUDGET,
    "historical_identity_not_established": FINISHED_NOT_ESTABLISHED,
    "historical_uniqueness_unverifiable": FINISHED_UNVERIFIABLE,
    "historical_identity_conflict": FINISHED_CONTESTED,
    "source_registration_changed": PROBLEM_REGISTRATION_CHANGED,
    "historical_identity_changed": FINISHED_UNVERIFIABLE,
}

#: The registration fields a listing's source block and the registry share.
_REGISTRATION_FIELDS = ("source_id", "kind", "store_dir", "producer",
                        "max_age_seconds")

# -- the disclosure policy (this view's own, on top of containment) -----
#: A component that is a PRIVATE LOCATION wherever it appears: git's own
#: directory, Herdr's private state, agent state, credential directories.
#: ``.env`` and every ``.env.*`` are private too.
PRIVATE_COMPONENTS = frozenset((
    ".git", ".herd", ".claude", ".codex", ".cortex", ".ssh", ".aws",
    ".gnupg", ".kube", ".azure", ".netrc", ".npmrc", ".pypirc",
))
#: The hidden names a source tree legitimately carries and this view shows.
#: Any OTHER hidden component is withheld (fail closed): the list is the
#: allowance, never the denial.
HIDDEN_LISTED = frozenset((
    ".github", ".gitignore", ".gitattributes", ".gitmodules",
    ".gitleaks.toml", ".editorconfig", ".npmignore", ".nvmrc",
    ".node-version", ".python-version", ".dockerignore", ".vscode",
    ".husky", ".changeset", ".storybook", ".babelrc", ".browserslistrc",
))
HIDDEN_LISTED_PREFIXES = (".eslint", ".prettier", ".stylelint")
#: Re-declarations of the evidence library's disclosure lists (parity pinned
#: by test): credential-like names and key material are withheld here too.
CREDENTIAL_NAME_PARTS = (
    "secret", "credential", "password", "passwd", "token", "apikey",
    "api_key", "api-key", "private_key", "private-key", "privatekey",
    "id_rsa", "id_dsa", "id_ecdsa", "id_ed25519", "keystore", "keychain",
)
KEY_MATERIAL_EXTENSIONS = (
    ".pem", ".key", ".p12", ".pfx", ".der", ".crt", ".cer", ".gpg", ".asc",
    ".kdbx", ".jks", ".ppk", ".ovpn",
)

#: The status codes of a tracked path that mean an UNMERGED entry.
_UNMERGED = frozenset(("DD", "AU", "UD", "UA", "DU", "AA", "UU"))
_COUNT_RECORD = re.compile(r"^(\d+|-)\t(\d+|-)\t(.*)$", re.DOTALL)
_HUNK_HEADER = re.compile(
    r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@(?: (.*))?$")


class _Refused(Exception):
    def __init__(self, problem, detail):
        super(_Refused, self).__init__(problem)
        self.problem = problem
        self.detail = detail


class _Moved(Exception):
    """The workspace changed between two observations of one read."""


class Accounting(object):
    """What ONE request actually did: counted, so a test (and a reader) can
    see the budget was spent, not assumed."""

    def __init__(self):
        self.git_commands = 0
        self.git_bytes = 0
        self.registry_bytes = 0
        self.untracked_bytes = 0

    def summary(self):
        return dict((key, getattr(self, key)) for key in ACCOUNTING_KEYS)


def default_transport():
    """The real git boundary, built with NO argument and NO override (the
    transport seam's rule): the owner calls this only when a review is first
    asked and no boundary was injected."""
    return transport_module.GitTransport()


def _answer(state, problem, detail, view, mission_ref, task_id, snapshot,
            file_id, target, comparison, overview, file_, observed_at,
            accounting):
    return {
        "schema": SCHEMA, "version": VERSION, "state": state,
        "problem": problem,
        "detail": None if detail is None else _clip_units(
            detail, MAX_DETAIL_CHARS)[0],
        "view": view, "mission_ref": mission_ref, "task_id": task_id,
        "snapshot": snapshot, "file_id": file_id, "target": target,
        "comparison": comparison, "overview": overview, "file": file_,
        "observed_at": observed_at,
        "accounting": (Accounting() if accounting is None
                       else accounting).summary()}


def refusal(problem, detail, view, mission_ref, task_id, snapshot, file_id,
            observed_at=None, accounting=None):
    """A refused answer: no target, no comparison, no content."""
    if problem not in PROBLEMS:
        raise ValueError("an undeclared problem code: %r" % (problem,))
    return _answer(STATE_REFUSED, problem, detail, view, mission_ref,
                   task_id, snapshot, file_id, None, None, None, None,
                   observed_at, accounting)


# -- the active assignment: corroboration ------------------------------

def _registration(entry):
    return dict((field, entry[field]) for field in _REGISTRATION_FIELDS)


#: The ONE fixed artifact the workspace witness reads: the recorded workspace's OWN task record.
TASK_RECORD_DIRS = (".herd", "state")
TASK_RECORD_NAME = "task.json"
#: Every projected string is bounded to this many characters, ellipsis included: the legacy observer's own bound, so the two readers project the same fields.
TASK_FIELD_MAX_CHARS = 200


def _text_or_none(value):
    if not isinstance(value, str):
        return None
    if len(value) <= TASK_FIELD_MAX_CHARS:
        return value
    return value[:TASK_FIELD_MAX_CHARS - 1] + "…"


def _task_record(workspace, accounting):
    """The workspace's OWN task record as the identity-bearing projection
    ``{id, description, started_at, digest_sha256}``, from ONE hardened
    no-follow read of ``.herd/state/task.json`` (the review route's reader: it
    refuses an escaping chain, a symlink, a hardlink, a non-regular file, more
    than the instruction bound and non-UTF-8, where the legacy observer reads
    leniently). What a read can cost is HELD before it, the exact bytes settled
    after; a refused read keeps its hold. Anything that is not a readable JSON
    object is refused as ``finished_workspace_not_corroborated``."""
    if accounting.untracked_bytes + MAX_UNTRACKED_READ_COST_BYTES > (
            MAX_UNTRACKED_TOTAL_BYTES):
        raise _Refused(PROBLEM_BUDGET, "the untracked files already read for"
                       " this view used its %d-byte budget"
                       % MAX_UNTRACKED_TOTAL_BYTES)
    status, count, digest, text = state_artifact.read_state_artifact(
        workspace, TASK_RECORD_DIRS, TASK_RECORD_NAME)
    if status != prepare_module.INSTRUCTION_READ:
        accounting.untracked_bytes += MAX_UNTRACKED_READ_COST_BYTES
        raise _Refused(FINISHED_WORKSPACE_NOT_CORROBORATED, "the workspace's"
                       " own task record was not read (%s)" % status)
    accounting.untracked_bytes += count
    try:
        data = json.loads(text)
    except ValueError:
        raise _Refused(FINISHED_WORKSPACE_NOT_CORROBORATED, "the workspace's"
                       " own task record is not valid JSON")
    if not isinstance(data, dict):
        raise _Refused(FINISHED_WORKSPACE_NOT_CORROBORATED, "the workspace's"
                       " own task record is not a JSON object")
    started = data.get("started_at")
    return {"id": _text_or_none(data.get("id")),
            "description": _text_or_none(data.get("description")),
            "started_at": started if type(started) is int else None,
            "digest_sha256": digest}


def _witness(proof, accounting):
    """The WORKSPACE-SIDE witness of a finished assignment: the recorded
    workspace's own task record names THIS run (the one shared predicate,
    ``run_association.names_this_run``, over the intent facts the record-side
    proof carries) AND is the task the verified receipt names (a separate
    comparison: the predicate does not compare ids). Returned in the bound, so
    the release-time comparison sees a record that changed."""
    intent, mission_id = proof.get("intent"), proof.get("mission_id")
    if not (isinstance(mission_id, str) and isinstance(intent, dict)
            and type(intent.get("revision")) is int
            and isinstance(intent.get("proposal_digest_sha256"), str)
            and type(intent.get("recorded_at")) is int):
        raise _Refused(FINISHED_WORKSPACE_NOT_CORROBORATED, "the record-side"
                       " proof carries no usable run intent to compare the"
                       " workspace's task record with")
    record = _task_record(proof["workspace"], accounting)
    if not run_association.names_this_run(
            {"task": {"description": record["description"],
                      "started_at": record["started_at"]}},
            mission_id, intent):
        raise _Refused(FINISHED_WORKSPACE_NOT_CORROBORATED, "the workspace's"
                       " own task record does not begin with this run's"
                       " handoff, or its task started before the intent was"
                       " recorded")
    if record["id"] != proof["task_id"]:
        raise _Refused(FINISHED_WORKSPACE_NOT_CORROBORATED, "the workspace's"
                       " own task record names another task than the verified"
                       " receipt")
    return record


def _finished(answer, finished, accounting, mission_ref, task_id):
    """The COMPLETED assignment of ``mission_ref``, from the injected
    ``finished`` provider (it stands on the evidence library's record-side
    admission and is given the SAME held ``answer``): the bound the active
    path would build, carrying the WHOLE proof AND the workspace-side witness
    so the release-time comparison sees any change in either. The bytes the
    provider read are charged even when it refuses. A refusal is mapped
    through ``FINISHED_REFUSALS`` (a code the table does not know fails closed
    to unverifiable)."""
    proof, charged, refusal_ = finished(answer, mission_ref)
    if accounting is not None:
        accounting.registry_bytes += charged
    if refusal_ is not None:
        problem, detail = refusal_
        raise _Refused(FINISHED_REFUSALS.get(problem, FINISHED_UNVERIFIABLE),
                       detail)
    if proof["task_id"] != task_id:
        raise _Refused(PROBLEM_TASK_CHANGED, "the Mission's recorded task is"
                       " not the one this review was opened for; open it"
                       " again")
    witness = _witness(proof, Accounting() if accounting is None
                       else accounting)
    return {"source_id": proof["source_id"], "task_id": proof["task_id"],
            "workspace": proof["workspace"],
            "registration": proof["registration"], "finished": proof,
            "witness": witness}


def _corroborated(answer, freshness, now, mission_ref, task_id, finished=None,
                  accounting=None):
    """The ACTIVE assignment of ``mission_ref`` in the owner's last refreshed
    answer, as a dict of the facts that identify it, or ``_Refused``. With a
    ``finished`` provider, a TERMINAL assignment is judged by it instead
    (``_finished``); every other standing, and the active path, are unchanged."""
    if answer is None or answer.get("response") is None:
        raise _Refused(PROBLEM_NOT_CORROBORATED, "no refreshed catalog answer"
                       " is held; nothing shows the assignment is active")
    response = answer["response"]
    health, problem = freshness(response, now)
    if health != contract.HEALTH_FRESH:
        raise _Refused(PROBLEM_NOT_CORROBORATED, "the last catalog answer is"
                       " %s (%s) now; it shows nothing" % (health, problem))
    listed = [m for m in response["missions"]
              if m["mission_ref"] == mission_ref]
    if not listed:
        raise _Refused(PROBLEM_MISSION_NOT_LISTED, "the last catalog answer"
                       " does not list this Mission")
    if len(listed) > 1:
        raise _Refused(PROBLEM_MISSION_AMBIGUOUS, "the last catalog answer"
                       " lists this Mission more than once")
    mission = listed[0]
    model = mission["lifecycle"]["model"]
    herdr = model == HERDR_TASK_MODEL
    if model != contract.LIFECYCLE_MODEL_MISSION_CORE and not herdr:
        raise _Refused(PROBLEM_SOURCE_KIND, "only a Mission Core record or a"
                       " Herdr task names an assignment with a bound"
                       " workspace; this one is %s" % model)
    if mission["lifecycle"]["standing"] != contract.STANDING_ACTIVE:
        # A finished Herdr task is not a Mission Core historical execution.
        # The finished provider stays on the Mission Core path.
        if (not herdr) and finished is not None and mission["lifecycle"][
                "standing"] == contract.STANDING_TERMINAL:
            return _finished(answer, finished, accounting, mission_ref,
                             task_id)
        raise _Refused(PROBLEM_NOT_ACTIVE, "the assignment's standing is %s,"
                       " not active; a review is offered only for work in"
                       " progress" % mission["lifecycle"]["standing"])
    binding = mission["binding"]
    if binding["state"] != contract.BINDING_BOUND:
        raise _Refused(PROBLEM_NOT_BOUND, "the assignment's binding is %s (%s);"
                       " its workspace is not corroborated now"
                       % (binding["state"], binding["reason"]))
    execution = mission["execution"]
    if execution["task_id"] is None or execution[
            "workspace_realpath"] is None or not execution["consistent"] or (
                execution["record_ambiguous"]) or (
                    binding["task_id"] != execution["task_id"]):
        raise _Refused(PROBLEM_UNRESOLVED, "the assignment's task and"
                       " workspace are not resolved consistently")
    if execution["task_id"] != task_id:
        raise _Refused(PROBLEM_TASK_CHANGED, "the Mission's current task is not"
                       " the one this review was opened for; open it again")
    blocks = [b for b in response["sources"]
              if b["source_id"] == mission["source_id"]]
    if len(blocks) != 1:
        raise _Refused(PROBLEM_NOT_CORROBORATED, "the listing does not carry"
                       " exactly one block for the Mission's source")
    return {"source_id": mission["source_id"],
            "task_id": execution["task_id"],
            "workspace": execution["workspace_realpath"],
            "registration": _registration(blocks[0])}


def _registry_now(registry, accounting):
    """The registry document, read NOW (never the copy read before)."""
    try:
        document, count = registry.load_measured(absent_is_empty=False)
    except registry_module.RegistryError as exc:
        raise _Refused(PROBLEM_REGISTRY, "the source registry could not be"
                       " read (%s)" % exc.problem)
    accounting.registry_bytes += count
    return document


def _registered(document, bound, when):
    """``_Refused`` unless the Mission's source is registered, exactly once,
    exactly as the listing named it."""
    found = [s for s in document["sources"]
             if s["source_id"] == bound["source_id"]]
    if not found:
        raise _Refused(PROBLEM_SOURCE_NOT_REGISTERED, "the Mission's source is"
                       " not registered %s" % when)
    if len(found) > 1 or _registration(found[0]) != bound["registration"]:
        raise _Refused(PROBLEM_REGISTRATION_CHANGED, "the Mission's source is"
                       " not registered as the listing named it %s" % when)


# -- the disclosure policy ----------------------------------------------

def withheld_reason(path):
    """The reason a repository-relative ``path`` is WITHHELD, or None. Pure:
    no filesystem access. Decided before git is asked for anything."""
    parts = path.split("/")
    for part in parts:
        if part in PRIVATE_COMPONENTS or part == ".env" or part.startswith(
                ".env."):
            return REASON_PRIVATE
    for part in parts:
        if part.startswith(".") and part not in HIDDEN_LISTED and not (
                part.startswith(HIDDEN_LISTED_PREFIXES)):
            return REASON_HIDDEN
    for part in parts:
        lowered = part.lower()
        if any(word in lowered for word in CREDENTIAL_NAME_PARTS):
            return REASON_CREDENTIAL
        if lowered.endswith(KEY_MATERIAL_EXTENSIONS):
            return REASON_KEY_MATERIAL
    return None


def private_locations(paths):
    """The private LOCATIONS covering ``paths``: for each path the policy
    withholds, its shortest withheld prefix (the file itself, or the
    directory above it whose own name is the reason), sorted and without
    repeats. Pure; the policy is ``withheld_reason`` alone, asked of each
    prefix, so there is no second copy of the rules to drift from it."""
    found = set()
    for path in paths:
        if withheld_reason(path) is None:
            continue
        parts = path.split("/")
        for count in range(1, len(parts) + 1):
            prefix = "/".join(parts[:count])
            if withheld_reason(prefix) is not None:
                found.add(prefix)
                break
    return tuple(sorted(found))


def _private_entries(staged, unstaged, inherited=()):
    """``[(xy, path)]``, in the shape of the status listing, of the changed
    PRIVATE tracked paths: ``staged`` is what the index differs from HEAD in
    (``parse_modes`` of ``diff-index --cached``), ``unstaged`` what the
    working tree differs from the index in by stat alone (``unstaged_changes``).
    Both come from reads that open no file. ``inherited`` are public NAMES that
    took their privacy from where their content came from (the destination of
    an exact move out of a private location): they are listed here, with the
    private paths, because every read that could hash them left them out."""
    entries = []
    for path in sorted(set(staged) | set(unstaged)):
        if withheld_reason(path) is None and path not in inherited:
            continue                # a public path is the listing's own
        index = staged[path][2] if path in staged else " "
        tree = unstaged[path][2] if path in unstaged else " "
        entries.append(("UU" if "U" in (index, tree) else index + tree, path))
    return entries


_NO_BLOB = "0" * 40
_EMPTY_BLOB = "e69de29bb2d1d6434b8b29ae775ad8c2e48c5391"


def exact_private_moves(staged_ids):
    """``{new_path: old_path}``: a STAGED addition at a public name whose blob
    is exactly the blob a staged removal at a private name had. Git pairs a
    rename by reading both bodies, and a private body is never read, so the
    pairing it would have made is done here from the blob ids alone: an
    unchanged file moved from a private name to a public one stays withheld.
    (A move that also changes the content is not recognised: that needs the
    private body.) The empty blob pairs with nothing: it holds nothing."""
    removed = {}
    for path, (old_id, _new, letter) in staged_ids.items():
        if letter == "D" and old_id not in (_NO_BLOB, _EMPTY_BLOB) and (
                withheld_reason(path) is not None):
            removed.setdefault(old_id, []).append(path)
    moves = {}
    for path, (_old, new_id, letter) in staged_ids.items():
        if letter == "A" and new_id in removed and (
                withheld_reason(path) is None):
            moves[path] = sorted(removed[new_id])[0]
    return moves


def _safe_path(path):
    """Whether ``path`` is a contained, representable relative path."""
    if not path or _units(path) > MAX_REVIEW_PATH_CHARS or "\x00" in path:
        return False
    if path.startswith("/") or "\\" in path:
        return False
    return all(part not in ("", ".", "..") for part in path.split("/"))


# -- parsing git's output (pure) ---------------------------------------

def parse_inventory(data):
    """``[(xy, path)]`` of a ``status --porcelain=v1 -z --no-renames``
    capture, or ``_Refused``. Every path must be strict UTF-8."""
    entries = []
    for raw in data.split(b"\x00"):
        if not raw:
            continue
        if len(raw) < 4 or raw[2:3] != b" ":
            raise _Refused(PROBLEM_INVENTORY_INCONSISTENT, "a status entry is"
                           " not in the shape this view reads")
        try:
            xy = raw[:2].decode("ascii")
            path = raw[3:].decode("utf-8")
        except UnicodeDecodeError:
            raise _Refused(PROBLEM_PATH_UNSUPPORTED, "a changed path is not"
                           " valid UTF-8; the listing cannot name it")
        # git lists an untracked directory that is itself a repository as ONE
        # entry with a trailing slash (it does not enter it); nothing else
        # ends in one
        named = path[:-1] if xy == "??" and path.endswith("/") else path
        if not _safe_path(named):
            raise _Refused(PROBLEM_PATH_UNSUPPORTED, "a changed path is not a"
                           " contained, listable relative path")
        entries.append((xy, path))
    return entries


_MODE_RECORD = re.compile(
    r"^:([0-7]{6}) ([0-7]{6}) ([0-9a-f]{40}) ([0-9a-f]{40}) "
    r"([ACDMRTUX])([0-9]{0,3})$")
GITLINK_MODE = "160000"


def parse_modes(data):
    """``{path: (old_mode, new_mode, letter)}`` of a ``diff-index --raw -z -M``
    capture (a renamed file under its NEW path), or ``_Refused``."""
    return dict((path, record[:3])
                for path, record in _parse_raw(data).items())


def parse_raw_ids(data):
    """``{path: (old_id, new_id, letter)}`` of the same kind of capture: the
    blob ids git recorded (an all-zero id is "none")."""
    return dict((path, (record[3], record[4], record[2]))
                for path, record in _parse_raw(data).items())


def _parse_raw(data):
    fields = data.split(b"\x00")
    modes = {}
    index = 0
    while index < len(fields):
        raw = fields[index]
        index += 1
        if not raw:
            continue
        try:
            record = raw.decode("ascii")
        except UnicodeDecodeError:
            raise _Refused(PROBLEM_INVENTORY_INCONSISTENT, "a mode record is"
                           " not in the shape this view reads")
        match = _MODE_RECORD.match(record)
        if match is None:
            raise _Refused(PROBLEM_INVENTORY_INCONSISTENT, "a mode record is"
                           " not in the shape this view reads")
        old_mode, new_mode, old_id, new_id, letter, _score = match.groups()
        wanted = 2 if letter in "RC" else 1
        names = fields[index:index + wanted]
        index += wanted
        if len(names) != wanted or not all(names):
            raise _Refused(PROBLEM_INVENTORY_INCONSISTENT, "a mode record has"
                           " no path")
        try:
            path = names[-1].decode("utf-8")
        except UnicodeDecodeError:
            raise _Refused(PROBLEM_PATH_UNSUPPORTED, "a changed path is not"
                           " valid UTF-8; the listing cannot name it")
        modes[path] = (old_mode, new_mode, letter, old_id, new_id)
    return modes


def parse_counts(data):
    """``{path: (additions, deletions, old_path_or_None)}`` of a
    ``diff-index --numstat -z -M`` capture; ``additions`` and ``deletions``
    are None for a binary file. ``_Refused`` on anything unrecognised."""
    fields = data.split(b"\x00")
    counts = {}
    index = 0
    while index < len(fields):
        raw = fields[index]
        index += 1
        if not raw and index >= len(fields):
            break
        try:
            record = raw.decode("utf-8")
        except UnicodeDecodeError:
            raise _Refused(PROBLEM_PATH_UNSUPPORTED, "a counted path is not"
                           " valid UTF-8")
        match = _COUNT_RECORD.match(record)
        if match is None:
            raise _Refused(PROBLEM_INVENTORY_INCONSISTENT, "a count record is"
                           " not in the shape this view reads")
        added, deleted, path = match.groups()
        old = None
        if path == "":
            if index + 1 >= len(fields):
                raise _Refused(PROBLEM_INVENTORY_INCONSISTENT, "a rename"
                               " record is incomplete")
            try:
                old = fields[index].decode("utf-8")
                path = fields[index + 1].decode("utf-8")
            except UnicodeDecodeError:
                raise _Refused(PROBLEM_PATH_UNSUPPORTED, "a renamed path is"
                               " not valid UTF-8")
            index += 2
        if not _safe_path(path) or (old is not None and not _safe_path(old)):
            raise _Refused(PROBLEM_PATH_UNSUPPORTED, "a counted path is not a"
                           " contained, listable relative path")
        if path in counts:
            raise _Refused(PROBLEM_INVENTORY_INCONSISTENT, "a path is counted"
                           " twice")
        counts[path] = (None if added == "-" else int(added),
                        None if deleted == "-" else int(deleted), old)
    return counts


_STAT_ENTRY = re.compile(
    rb"([0-7]{6}) ([0-9a-f]{40}) ([0-3])\t([^\x00]*)\x00"
    rb"  ctime: (\d+):(\d+)\n  mtime: (\d+):(\d+)\n"
    rb"  dev: (\d+)\tino: (\d+)\n  uid: (\d+)\tgid: (\d+)\n"
    rb"  size: (\d+)\tflags: (\d+)\n")


def parse_index_stat(data):
    """``{path: (mode, size, mtime_seconds)}`` of the index's own record of each
    entry (``ls-files -z -s --debug``): the mode, and the size and modification
    SECOND the index recorded. The record also carries the modification time's
    nanoseconds (and the change time, inode, owner): they are present in the
    output and are NOT kept. The comparison is at the second because that is how
    this build of git itself compares an entry (measured: a same-size rewrite
    60 ms later is not seen by git's own comparison of a non-racy entry either),
    so a stricter one would list as changed what git lists as unchanged. The
    consequence is stated to the reader (``WITHHELD_CHANGE_WORDS``): an edit of
    the same size within the recorded second is not seen. Whether to tighten it
    is a decision, not an oversight. An unmerged entry (a stage other than 0) is
    not here: ``diff-index --cached`` names it. ``_Refused`` on anything else."""
    recorded = {}
    position = 0
    while position < len(data):
        match = _STAT_ENTRY.match(data, position)
        if match is None:
            raise _Refused(PROBLEM_INVENTORY_INCONSISTENT, "an index record is"
                           " not in the shape this view reads")
        position = match.end()
        if match.group(3) != b"0":
            continue
        try:
            path = match.group(4).decode("utf-8")
        except UnicodeDecodeError:
            raise _Refused(PROBLEM_PATH_UNSUPPORTED, "a recorded path is not"
                           " valid UTF-8")
        if not _safe_path(path):
            raise _Refused(PROBLEM_PATH_UNSUPPORTED, "a recorded path is not a"
                           " contained, listable relative path")
        recorded[path] = (match.group(1).decode("ascii"),
                          int(match.group(13)), int(match.group(7)))
    return recorded


def unstaged_changes(recorded, current):
    """``{path: (None, None, letter)}``: what differs between the index's
    record (``parse_index_stat``) and the working tree, by STAT ALONE. ``current``
    gives a path's ``[st_mode, st_size, st_mtime_ns, st_ino]`` (None when it is
    gone). ``D`` gone, ``T`` another kind of file, ``M`` another size or another
    modification second (the index records seconds, 32 bits wide). Nothing here
    opens a file, and git is not asked to compare one, so no recorded time, however
    far ahead, can make a file be read. A file edited to the same size within the
    second its entry was recorded is not seen; a file only touched is."""
    changes = {}
    for path, (mode, size, seconds) in recorded.items():
        now = current(path)
        if now is None:
            letter = "D"
        else:
            kind = now[0] & 0o170000
            wanted = {"120000": 0o120000, "160000": 0o040000}.get(
                mode, 0o100000)
            if kind != wanted:
                letter = "T"
            elif mode == "160000":
                continue                    # a submodule is not entered
            elif (now[1] & 0xFFFFFFFF) != size or (
                    (now[2] // 1000000000) & 0xFFFFFFFF) != seconds:
                letter = "M"
            else:
                continue
        changes[path] = (None, None, letter)
    return changes


def _staging(xy):
    if xy == "??":
        return "untracked"
    if xy in _UNMERGED:
        return "conflicted"
    staged, unstaged = xy[0] != " ", xy[1] != " "
    return "both" if staged and unstaged else (
        "staged" if staged else "unstaged")


def _change(xy):
    """The NET change HEAD -> working tree a status code means (renames are
    paired afterwards, from git's own counts)."""
    if xy == "??":
        return "untracked"
    if xy in _UNMERGED:
        return "conflicted"
    index, tree = xy[0], xy[1]
    if tree == "D" and index != "A":
        return "deleted"
    if index == "D":
        return "deleted"
    if index == "A" or tree == "A":
        return "added"
    if "T" in (index, tree):
        return "type_changed"
    return "modified"


def _units(text):
    """``text``'s length in UTF-16 code units: the unit every ``*_CHARS``
    bound is in (see the bounds above). A lone half of a pair counts one, as
    it does to JavaScript."""
    if text.isascii():
        return len(text)
    return len(text.encode("utf-16-le", "surrogatepass")) // 2


def _clip_units(text, limit):
    """``text`` cut to at most ``limit`` UTF-16 code units, NEVER inside a
    character: one outside the BMP is two units and is kept whole or left
    out whole (half a pair is not text). Returns ``(kept, omitted)``, where
    ``omitted`` is the number of CHARACTERS left out: the count a reader sees
    as "N more characters"."""
    if _units(text) <= limit:
        return text, 0
    used = 0
    end = 0
    for character in text:
        step = 2 if character > "\uffff" else 1
        if used + step > limit:
            break
        used += step
        end += 1
    return text[:end], len(text) - end


def _clip(text):
    return _clip_units(text, MAX_REVIEW_LINE_CHARS)


class _Unparsed(Exception):
    pass


class _NotUtf8(Exception):
    pass


def parse_file_diff(data):
    """The structure of ONE file's unified diff, or ``_Unparsed``.

    Returns ``{"binary", "mode_change", "similarity", "hunks",
    "additions", "deletions", "lines_total", "lines_retained", "truncated"}``
    where the counts are EXACT over the whole diff and ``hunks`` retains at
    most ``MAX_REVIEW_FILE_LINES`` lines / ``MAX_REVIEW_FILE_CHARS``
    characters. Anything git prints that this does not recognise, or a hunk
    whose lines do not add up to its own header, is not understood: nothing
    is guessed."""
    try:
        data.decode("utf-8")
    except UnicodeDecodeError:
        raise _NotUtf8()
    lines = data.split(b"\n")
    if lines and lines[-1] == b"":
        lines.pop()
    sections = [i for i, raw in enumerate(lines)
                if raw.startswith(b"diff --git ")]
    if len(sections) != 1 or sections[0] != 0:
        raise _Unparsed("the diff is not exactly one file's")
    result = {"binary": False, "mode_change": None, "similarity": None,
              "hunks": [], "additions": 0, "deletions": 0, "lines_total": 0,
              "lines_retained": 0, "truncated": False}
    old_mode = new_mode = None
    retained_chars = 0
    closed = False
    position = 1
    total = len(lines)
    try:
        while position < total:
            raw = lines[position]
            if raw.startswith(b"@@ "):
                break
            text = raw.decode("utf-8")
            if text.startswith("old mode "):
                old_mode = text[9:]
            elif text.startswith("new mode "):
                new_mode = text[9:]
            elif text.startswith(("deleted file mode ", "new file mode ")):
                pass
            elif text.startswith("similarity index "):
                result["similarity"] = int(text[17:].rstrip("%"))
            elif text.startswith(("dissimilarity index ", "rename from ",
                                  "rename to ", "index ", "--- ", "+++ ")):
                pass
            elif text.startswith(("Binary files ", "GIT binary patch")):
                result["binary"] = True
            else:
                raise _Unparsed("an unrecognised diff header line")
            position += 1
        if old_mode is not None or new_mode is not None:
            if old_mode is None or new_mode is None:
                raise _Unparsed("an incomplete mode change")
            result["mode_change"] = {"old": old_mode, "new": new_mode}
        while position < total:
            header = _HUNK_HEADER.match(lines[position].decode("utf-8"))
            if header is None:
                raise _Unparsed("a hunk header was expected")
            old_start, old_lines, new_start, new_lines, section = \
                header.groups()
            hunk = {"old_start": int(old_start),
                    "old_lines": 1 if old_lines is None else int(old_lines),
                    "new_start": int(new_start),
                    "new_lines": 1 if new_lines is None else int(new_lines),
                    "section": None if not section
                    else _clip_units(section, MAX_SECTION_CHARS)[0],
                    "lines": []}
            position += 1
            old_no, new_no = hunk["old_start"], hunk["new_start"]
            remaining_old, remaining_new = hunk["old_lines"], hunk[
                "new_lines"]
            while remaining_old > 0 or remaining_new > 0 or (
                    position < total
                    and lines[position].startswith(b"\\")):
                if position >= total:
                    raise _Unparsed("a hunk ends before its header says")
                raw = lines[position]
                position += 1
                text = raw.decode("utf-8")
                marker, body = text[:1], text[1:]
                if marker == "\\":
                    entry = {"kind": KIND_NO_NEWLINE, "old": None,
                             "new": None, "text": body}
                elif marker == " ":
                    entry = {"kind": KIND_CONTEXT, "old": old_no,
                             "new": new_no, "text": body}
                    old_no += 1
                    new_no += 1
                    remaining_old -= 1
                    remaining_new -= 1
                elif marker == "-":
                    entry = {"kind": KIND_DELETE, "old": old_no,
                             "new": None, "text": body}
                    old_no += 1
                    remaining_old -= 1
                    result["deletions"] += 1
                elif marker == "+":
                    entry = {"kind": KIND_ADD, "old": None, "new": new_no,
                             "text": body}
                    new_no += 1
                    remaining_new -= 1
                    result["additions"] += 1
                else:
                    raise _Unparsed("an unrecognised diff line")
                if remaining_old < 0 or remaining_new < 0:
                    raise _Unparsed("a hunk is longer than its header says")
                if entry["kind"] != KIND_NO_NEWLINE:
                    result["lines_total"] += 1
                text_shown, clipped = _clip(entry["text"])
                entry["text"], entry["clipped"] = text_shown, clipped
                # RETENTION IS A PREFIX. Once one line does not fit (the line
                # cap, or the character cap, which an unequal line can reach
                # while a shorter one after it would still fit) no later line
                # is kept: a hole would sit under a "first N lines" claim. The
                # rest is still COUNTED above, so the totals stay exact.
                # An end-of-file marker is not a line (lines_total does not
                # count it) and it belongs to the line before it: the two are
                # kept TOGETHER or cut together. A line is kept only if it AND
                # the marker that follows it fit the character bound, so a
                # marker is never dropped while its line stays (the dialog
                # would then show "no newline" nowhere, and nothing would say
                # so); a line that does not fit with its marker is cut, which
                # is a truncation like any other and is said.
                owned = 0
                if entry["kind"] != KIND_NO_NEWLINE and position < total and (
                        lines[position].startswith(b"\\")):
                    owned = _units(
                        _clip(lines[position].decode("utf-8")[1:])[0])
                if entry["kind"] == KIND_NO_NEWLINE:
                    if closed:
                        continue            # its line was cut: said already
                    if retained_chars + _units(text_shown) > (
                            MAX_REVIEW_FILE_CHARS):
                        closed = True       # cannot happen (room was kept)
                        result["truncated"] = True
                        continue
                elif closed or result["lines_retained"] >= (
                        MAX_REVIEW_FILE_LINES) or (
                            retained_chars + _units(text_shown) + owned
                            > MAX_REVIEW_FILE_CHARS):
                    closed = True
                    result["truncated"] = True
                    continue
                retained_chars += _units(text_shown)
                hunk["lines"].append(entry)
                if entry["kind"] != KIND_NO_NEWLINE:
                    result["lines_retained"] += 1
            result["hunks"].append(hunk)
    except (UnicodeDecodeError, ValueError):
        raise _Unparsed("the diff is not understood")
    # Hunks that kept no line at all (everything past a cap) are dropped from
    # the retained list; the totals above stay exact.
    result["hunks"] = [h for h in result["hunks"] if h["lines"]]
    return result


def synthesize_new_file(text):
    """The structure of an UNTRACKED file shown as all additions (git has no
    diff for it): the parse shape of ``parse_file_diff``, from its text."""
    pieces = text.split("\n")
    no_newline = pieces[-1] != ""
    if not no_newline:
        pieces.pop()
    result = {"binary": False, "mode_change": None, "similarity": None,
              "hunks": [], "additions": len(pieces), "deletions": 0,
              "lines_total": len(pieces), "lines_retained": 0,
              "truncated": False}
    if not pieces:
        return result
    hunk = {"old_start": 0, "old_lines": 0, "new_start": 1,
            "new_lines": len(pieces), "section": None, "lines": []}
    retained_chars = 0
    marker = " No newline at end of file"
    for number, piece in enumerate(pieces, 1):
        shown, clipped = _clip(piece)
        # the last line and its end-of-file marker are kept together or cut together
        owned = _units(marker) if no_newline and number == len(pieces) else 0
        if result["lines_retained"] >= MAX_REVIEW_FILE_LINES or (
                retained_chars + _units(shown) + owned
                > MAX_REVIEW_FILE_CHARS):
            result["truncated"] = True
            break
        retained_chars += _units(shown)
        hunk["lines"].append({"kind": KIND_ADD, "old": None, "new": number,
                              "text": shown, "clipped": clipped})
        result["lines_retained"] += 1
    if no_newline and not result["truncated"]:
        hunk["lines"].append({"kind": KIND_NO_NEWLINE, "old": None,
                              "new": None, "text": marker, "clipped": 0})
    result["hunks"] = [hunk]
    return result


# -- observing the workspace ---------------------------------------------

def _git(accounting, verb, *args):
    """One transport read (``verb`` is one of the transport's live reads),
    accounted; a failure or a refusal is a typed ``_Refused``."""
    accounting.git_commands += 1
    try:
        result = verb(*args)
    except transport_module.GitViewRefused as refused:
        raise _Refused(PROBLEM_INVENTORY_OVER_BOUND, "%s; there is too much"
                       " in this repository to review in one view" % refused)
    except (transport_module.GitTransportError, OSError):
        raise _Refused(PROBLEM_GIT_FAILED, "git could not read the workspace")
    if isinstance(result, dict):
        status = result.get("status")
        if status == transport_module.CAPTURE_REFUSED_DEADLINE:
            raise _Refused(PROBLEM_GIT_DEADLINE, "a git read passed its"
                           " deadline of %d seconds"
                           % transport_module.MAX_REVIEW_GIT_SECONDS)
        if status == transport_module.CAPTURE_REFUSED_OVER_BOUND:
            return result
        accounting.git_bytes += result.get("total_bytes", 0)
    return result


def _enter_view(accounting, transport, workspace):
    """Open the ONE private git directory an observation reads through:
    ``(manager, view)``, the manager to be left when the observation ends.
    Refuses as ``_git`` does."""
    accounting.git_commands += 1
    try:
        manager = transport.live_view(workspace)
        return manager, manager.__enter__()
    except transport_module.GitViewRefused as refused:
        raise _Refused(PROBLEM_INVENTORY_OVER_BOUND, "%s; there is too much"
                       " in this repository to review in one view" % refused)
    except (transport_module.GitTransportError, OSError):
        raise _Refused(PROBLEM_GIT_FAILED, "git could not read the workspace")


def _workspace(path):
    if type(path) is not str or not os.path.isabs(path) or "\x00" in path or (
            os.path.realpath(path) != path):
        raise _Refused(PROBLEM_WORKSPACE_UNUSABLE, "the workspace the record"
                       " names is not an absolute, resolved path")
    if not os.path.isdir(path):
        raise _Refused(PROBLEM_WORKSPACE_UNUSABLE, "the workspace the record"
                       " names is not a directory now")
    return path


def _stat(path, relative):
    try:
        st = os.lstat(os.path.join(path, *relative.split("/")))
    except OSError:
        return None
    return [st.st_mode, st.st_size, st.st_mtime_ns, st.st_ino]


def _file_id(snapshot, path):
    return "rf-" + hashlib.sha256(
        (snapshot + "\x00" + path).encode("utf-8")).hexdigest()[:32]


def _verified(accounting, view):
    """``_Refused`` unless the private index is still exactly the copy made."""
    try:
        view.verify()
    except transport_module.GitTransportError:
        raise _Refused(PROBLEM_GIT_FAILED, "the private index changed while it"
                       " was read through")


def _observe(transport, workspace, bound, accounting, hold=None):
    """ONE observation of the workspace: ``(identity, files)`` where
    ``identity`` is the JSON-able facts a snapshot is made of and ``files``
    the listing (paths, change, staging, counts, states).

    Raises ``_Refused`` on anything unusable. An overview observes twice and
    compares the identities (``_observe_stable``); a file read compares one
    observation with the snapshot it was asked against. ``hold`` (a list) keeps
    the observation's private git directory OPEN for the caller, who leaves it:
    a file's diff is made through the SAME index the observation decided from."""
    toplevel = _git(accounting, transport.live_toplevel, workspace)
    if toplevel != workspace:
        raise _Refused(PROBLEM_NOT_WORKTREE_ROOT, "the workspace is not the"
                       " root of a git worktree (git names a different root)")
    try:
        head = _git(accounting, transport.live_head, workspace)
    except _Refused as refused:
        if refused.problem == PROBLEM_GIT_FAILED:
            raise _Refused(PROBLEM_NO_BASE, "the workspace has no commit to"
                           " compare against (an unborn branch)")
        raise
    # The content-filter drivers the repository's own configuration names are
    # LISTED, so the review can say they were not run. No read below runs one:
    # each goes through a git directory of its own making that holds none of
    # the repository's configuration (transport: "LIVE reads").
    filters = _git(accounting, transport.live_filter_drivers, workspace)
    # ONE private git directory for the rest of the observation, so the index
    # the private locations are decided from is the index every read below
    # uses. Private locations are decided FIRST, from the names alone (reads
    # that open no working file); every read that could hash or diff a file is
    # then told to leave them out.
    manager, view = _enter_view(accounting, transport, workspace)
    if hold is not None:
        hold.extend((manager, view))
    try:
        named = _git(accounting, transport.live_names, workspace, view)
        if named["status"] != transport_module.CAPTURE_CAPTURED:
            raise _Refused(PROBLEM_INVENTORY_OVER_BOUND, "the list of tracked"
                           " paths is larger than its bound (at least %d"
                           " bytes); there is too much in this repository to"
                           " review in one view"
                           % named["total_bytes_lower_bound"])
        staged = parse_modes(named["staged"])
        moves = exact_private_moves(parse_raw_ids(named["staged"]))
        # A destination that took its privacy from its source is as private as
        # the source: it is left out of every read below, FIRST, with the rest
        locations = tuple(sorted(set(private_locations(
            [raw.decode("utf-8", "surrogateescape")
             for raw in named["names"].split(b"\x00") if raw]
            + sorted(staged))) | set(moves)))
        recorded = _git(accounting, transport.live_index_stat, workspace,
                        locations, view)
        inventory = _git(accounting, transport.live_inventory, workspace,
                         locations, view)
        counted = _git(accounting, transport.live_counts, workspace,
                       locations, view)
        moded = _git(accounting, transport.live_modes, workspace, locations,
                     view)
        _verified(accounting, view)
    finally:
        if hold is None:
            manager.__exit__(None, None, None)
    for capture in (recorded, inventory, counted, moded):
        if capture["status"] != transport_module.CAPTURE_CAPTURED:
            raise _Refused(PROBLEM_INVENTORY_OVER_BOUND, "a listing is larger"
                           " than its bound (at least %d bytes); there is too"
                           " much to review in one view"
                           % capture["total_bytes_lower_bound"])
    if not (named["head"] == recorded["head"] == inventory["head"]
            == counted["head"] == moded["head"] == head["oid"]):
        raise _Moved()                      # HEAD moved between the reads
    status_entries = parse_inventory(inventory["data"])
    # An untracked directory that is itself a repository is ONE entry git does
    # not enter (it is listed with a trailing slash): named without it here.
    nested = set(path[:-1] for _xy, path in status_entries
                 if path.endswith("/"))
    status_entries = [(xy, path[:-1] if path.endswith("/") else path)
                      for xy, path in status_entries]
    # the changed PRIVATE tracked paths, which the listing above was told to
    # leave out: their names and kinds of change, from reads that open no file
    private = _private_entries(
        staged, unstaged_changes(parse_index_stat(recorded["data"]),
                                 lambda path: _stat(workspace, path)),
        set(moves))
    status_entries.extend(private)
    counts = parse_counts(counted["data"])
    modes = parse_modes(moded["data"])
    if len(status_entries) > MAX_REVIEW_FILES:
        raise _Refused(PROBLEM_TOO_MANY_FILES, "more than %d files are changed"
                       " (%d); there is too much changed to review in one"
                       " view" % (MAX_REVIEW_FILES, len(status_entries)))
    # a private file moved, unchanged, to a public name: the pair git could
    # not make (it would read the private body) is made from the blob ids
    gone = set(moves.values())
    status_entries = [(xy, path) for xy, path in status_entries
                      if not (path in gone and path not in moves)]
    by_path = dict((path, xy) for xy, path in status_entries)
    if len(by_path) != len(status_entries):
        raise _Refused(PROBLEM_INVENTORY_INCONSISTENT, "a path is listed"
                       " twice")
    files = []
    consumed = set()
    for path, (added, deleted, old) in sorted(counts.items()):
        if old is not None:
            # git paired a deletion and an addition: both must be listed.
            if by_path.get(old) is None or by_path.get(path) is None:
                raise _Moved()
            consumed.add(old)
            consumed.add(path)
            files.append({"path": path, "old_path": old, "change": "renamed",
                          "staging": _staging(by_path[path]),
                          "additions": added, "deletions": deleted,
                          "counted": True, "binary": added is None,
                          "nested": False,
                          "gitlink": GITLINK_MODE in modes.get(path, ())[:2]})
    for xy, path in status_entries:
        if path in consumed:
            continue
        entry = {"path": path, "old_path": moves.get(path),
                 "change": "renamed" if path in moves else _change(xy),
                 "staging": _staging(xy), "additions": None,
                 "deletions": None, "counted": False, "binary": False,
                 "nested": path in nested,
                 "gitlink": GITLINK_MODE in modes.get(path, ())[:2]}
        if path in counts and xy != "??":
            added, deleted = counts[path][:2]
            entry.update(additions=added, deletions=deleted, counted=True,
                         binary=added is None)
        elif path in counts:
            raise _Moved()
        files.append(entry)
    listed = set(f["path"] for f in files)
    if any(path not in listed for path in counts):
        raise _Moved()
    files.sort(key=lambda f: f["path"])
    stats = dict((f["path"], _stat(workspace, f["path"])) for f in files)
    identity = {
        "source_id": bound["source_id"], "task_id": bound["task_id"],
        "workspace": workspace, "head": head["oid"], "ref": head["ref"],
        "filters": filters, "locations": list(locations),
        # the listing of the private files is part of what a snapshot vouches
        # for (the status digest below no longer covers them: it was told to
        # leave them out)
        "private": hashlib.sha256(json.dumps(
            [private, sorted(moves.items())],
            separators=(",", ":")).encode("utf-8")).hexdigest(),
        "inventory": inventory["digest"], "counts": counted["digest"],
        # the submodules' entries only: git's raw listing also names every
        # file whose stat no longer matches the index (until the index is
        # refreshed, which the agent's own ``git status`` does) and prints
        # the blob ids it happens to have computed — none of that changes the
        # comparison, so none of it may move the snapshot
        "modes": hashlib.sha256(json.dumps(
            sorted((path, mode) for path, mode in modes.items()
                   if GITLINK_MODE in mode[:2]),
            separators=(",", ":")).encode("utf-8")).hexdigest(),
        "stats": [[p, stats[p]] for p in sorted(stats)],
    }
    return identity, files


def _snapshot(identity):
    return hashlib.sha256(json.dumps(
        identity, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _observe_stable(transport, workspace, bound, accounting):
    """An observation that AGREES with a second one, or ``_Refused``."""
    for _ in range(MAX_OBSERVATION_ATTEMPTS):
        try:
            first, files = _observe(transport, workspace, bound, accounting)
            second, _again = _observe(transport, workspace, bound, accounting)
        except _Moved:
            continue
        if first == second:
            return first, files
    raise _Refused(PROBLEM_MOVED_DURING_READ, "the workspace kept changing"
                   " while it was read; try again")


# -- per-file states ------------------------------------------------------

#: Every hardened-reader status but ``read``, mapped EXPLICITLY for an
#: untracked file (a new reader status without an entry fails a test).
_UNTRACKED_REFUSALS = {
    prepare_module.INSTRUCTION_REFUSED_OVER_BOUND: (STATE_OVERSIZED, None),
    prepare_module.INSTRUCTION_REFUSED_NON_UTF8: (STATE_NOT_UTF8, None),
    prepare_module.INSTRUCTION_REFUSED_NOT_REGULAR:
        (STATE_UNSUPPORTED, REASON_NOT_REGULAR),
    prepare_module.INSTRUCTION_REFUSED_ESCAPES:
        (STATE_UNSUPPORTED, REASON_NOT_REGULAR),
    prepare_module.INSTRUCTION_REFUSED_HARDLINK:
        (STATE_UNSUPPORTED, REASON_HARDLINK),
    prepare_module.INSTRUCTION_REFUSED_UNREADABLE:
        (STATE_UNSUPPORTED, REASON_UNREADABLE),
}


def _read_untracked(workspace, path, accounting):
    """``(state, reason, text)`` of an untracked file through the hardened
    no-follow reader. What a read can cost (the bound and the byte past it)
    is HELD before the read, the exact bytes settled after (a refused read
    keeps its hold)."""
    if accounting.untracked_bytes + MAX_UNTRACKED_READ_COST_BYTES > (
            MAX_UNTRACKED_TOTAL_BYTES):
        raise _Refused(PROBLEM_BUDGET, "the untracked files already read for"
                       " this view used its %d-byte budget"
                       % MAX_UNTRACKED_TOTAL_BYTES)
    parts = path.split("/")
    status, count, _digest, text = state_artifact.read_state_artifact(
        workspace, parts[:-1], parts[-1])
    if status == prepare_module.INSTRUCTION_READ:
        accounting.untracked_bytes += count
        if "\x00" in text:
            return STATE_BINARY, None, None
        return (STATE_EMPTY if count == 0 else STATE_TEXT), None, text
    if status == prepare_module.INSTRUCTION_ABSENT:
        raise _Moved()                      # listed, then gone: it moved
    accounting.untracked_bytes += MAX_UNTRACKED_READ_COST_BYTES
    try:
        state, reason = _UNTRACKED_REFUSALS[status]
    except KeyError:
        raise _Refused(PROBLEM_INVENTORY_INCONSISTENT, "the reader reported a"
                       " status this view does not know (%s)" % status)
    return state, reason, None


def _annotate(workspace, files, accounting):
    """Give every listed file its STATE (and counts for an untracked one).
    A policy exclusion is decided first, from the path alone: a withheld file
    is neither read nor counted."""
    for entry in files:
        entry["reason"] = None
        reason = withheld_reason(entry["path"]) or (
            None if entry["old_path"] is None
            else withheld_reason(entry["old_path"]))
        change = entry["change"]
        if reason is not None:
            entry.update(state=STATE_WITHHELD, reason=reason, additions=None,
                         deletions=None)
        elif change == "conflicted":
            entry.update(state=STATE_CONFLICTED, additions=None,
                         deletions=None)
        elif change == "type_changed":
            entry.update(state=STATE_UNSUPPORTED, reason=REASON_TYPE_CHANGE,
                         additions=None, deletions=None)
        elif entry["nested"]:
            entry.update(state=STATE_UNSUPPORTED, reason=REASON_NESTED_REPOSITORY,
                         additions=None, deletions=None)
        elif change == "untracked":
            state, why, text = _read_untracked(workspace, entry["path"],
                                               accounting)
            entry.update(state=state, reason=why, additions=None,
                         deletions=None)
            if text is not None:
                shown = synthesize_new_file(text)
                entry.update(additions=shown["additions"], deletions=0)
        else:
            _tracked_state(entry)
            if entry["gitlink"] and entry["state"] == STATE_TEXT:
                entry["state"] = STATE_SUBMODULE
    return files


def _tracked_state(entry):
    """The state of a TRACKED change from the listing alone: git's counts say
    binary (``-``), nothing counted (a change with no content difference),
    a pure mode change, or text. Whether the text is valid UTF-8 and within
    the diff bound is only known when the diff is read."""
    if not entry["counted"]:
        entry["state"] = STATE_NO_CONTENT
    elif entry["binary"]:
        entry["state"] = STATE_BINARY
    elif entry["additions"] == 0 and entry["deletions"] == 0:
        entry["state"] = STATE_EMPTY if entry["change"] in (
            "added", "deleted") else STATE_NO_CONTENT
    else:
        entry["state"] = STATE_TEXT


def _overview_file(entry, snapshot):
    return {
        "file_id": _file_id(snapshot, entry["path"]),
        "path": entry["path"], "old_path": entry["old_path"],
        "change": entry["change"], "staging": entry["staging"],
        "state": entry["state"], "reason": entry["reason"],
        "additions": entry["additions"], "deletions": entry["deletions"],
    }


def _overview(files, snapshot):
    listed = [_overview_file(entry, snapshot) for entry in files]
    return {
        "files": listed,
        "totals": {
            "files": len(listed),
            "additions": sum(f["additions"] or 0 for f in listed),
            "deletions": sum(f["deletions"] or 0 for f in listed),
            "withheld": sum(1 for f in listed if f["state"] == STATE_WITHHELD),
            "unshown": sum(1 for f in listed
                           if f["state"] not in SHOWN_STATES),
        },
    }


def _file_block(workspace, transport, entry, snapshot, head, identity,
                accounting, view):
    """The ``file`` block of one listed file: its state, and for a text file
    its hunks, read now and cross-checked against the listing's own counts."""
    block = dict(_overview_file(entry, snapshot))
    block.update(mode_change=None, similarity=None, hunks=[],
                 truncated=False, lines_total=0, lines_retained=0,
                 bytes_total=0, digest_sha256=None)
    # A counted change with no lines (a mode change alone) is read too: the
    # diff is where the mode change is stated.
    if entry["state"] not in SHOWN_STATES and not (
            entry["state"] == STATE_NO_CONTENT and entry["counted"]):
        return block
    if entry["change"] == "untracked":
        state, _why, text = _read_untracked(workspace, entry["path"],
                                            accounting)
        if state != STATE_TEXT:
            raise _Moved()                  # it changed since it was listed
        shown = synthesize_new_file(text)
        raw = text.encode("utf-8")
        block.update(hunks=shown["hunks"], truncated=shown["truncated"],
                     lines_total=shown["lines_total"],
                     lines_retained=shown["lines_retained"],
                     bytes_total=len(raw),
                     digest_sha256=hashlib.sha256(raw).hexdigest(),
                     additions=shown["additions"], deletions=0)
        return block
    paths = [entry["path"]] if entry["old_path"] is None else [
        entry["old_path"], entry["path"]]
    # BEFORE git is asked for anything: the diff is read through the index the
    # observation decided from, against the commit it observed, and the path
    # must still be the kind of thing it was listed as (a file that has become
    # a directory is refused here, not discovered from what a diff returns)
    if view.oid != head:
        raise _Moved()
    listed = dict((p, st) for p, st in identity["stats"]).get(entry["path"])
    now = _stat(workspace, entry["path"])
    if (listed is None) != (now is None) or (
            listed is not None and (listed[0] & 0o170000) != (
                now[0] & 0o170000)):
        raise _Moved()                      # it is no longer what was listed
    # a path is selected as a FILE (nothing under it); the one exception is a
    # submodule's pointer, which IS a directory on disk and is a single entry in
    # the index (git is told not to enter it)
    capture = _git(accounting, transport.live_file_diff, workspace, paths,
                   tuple(identity["locations"]), view, not entry["gitlink"])
    if capture["status"] == transport_module.CAPTURE_CAPTURED and (
            capture["head"] != head):
        raise _Moved()                      # HEAD moved since the listing
    if capture["status"] != transport_module.CAPTURE_CAPTURED:
        raise _Refused(PROBLEM_FILE_DIFF_OVER_BOUND, "this file's diff is"
                       " larger than %d bytes (at least %d); it is not shown"
                       % (transport_module.MAX_REVIEW_FILE_DIFF_BYTES,
                          capture["total_bytes_lower_bound"]))
    block["bytes_total"] = capture["total_bytes"]
    block["digest_sha256"] = capture["digest"]
    try:
        shown = parse_file_diff(capture["data"])
    except _NotUtf8:
        block.update(state=STATE_NOT_UTF8)
        return block
    except _Unparsed as exc:
        raise _Refused(PROBLEM_DIFF_NOT_UNDERSTOOD, str(exc))
    if shown["binary"]:
        block.update(state=STATE_BINARY, additions=None, deletions=None)
        return block
    if not shown["hunks"] and not shown["truncated"]:
        block.update(state=STATE_NO_CONTENT, mode_change=shown["mode_change"],
                     similarity=shown["similarity"])
        return block
    if (entry["additions"], entry["deletions"]) != (
            shown["additions"], shown["deletions"]):
        raise _Refused(PROBLEM_MOVED_DURING_READ, "the file changed between"
                       " the listing and the read (the counts differ)")
    block.update(mode_change=shown["mode_change"],
                 similarity=shown["similarity"], hunks=shown["hunks"],
                 truncated=shown["truncated"],
                 lines_total=shown["lines_total"],
                 lines_retained=shown["lines_retained"])
    return block


# -- the request ----------------------------------------------------------

def _target(bound, workspace, identity):
    branch = identity["ref"]
    if branch is not None and branch.startswith("refs/heads/"):
        branch = branch[len("refs/heads/"):]
    return {
        "source_id": bound["source_id"],
        "worktree": os.path.join(
            os.path.basename(os.path.dirname(workspace)),
            os.path.basename(workspace)),
        "head": identity["head"],
        "branch": None if branch is None else _clip_units(
            branch, MAX_BRANCH_CHARS)[0],
        "detached": identity["ref"] is None,
        "attribution": ATTRIBUTION_SHARED,
    }


def _comparison(identity):
    return {
        "mode": MODE_HEAD_TO_WORKTREE,
        "base": {"kind": BASE_HEAD_COMMIT, "oid": identity["head"]},
        "candidate": {"kind": CANDIDATE_WORKING_TREE},
        "covers": list(COVERS),
        "does_not_cover": list(DOES_NOT_COVER),
        # Content filters (git-lfs, a smudge/clean pair) the repository
        # configures were NOT run: a file one manages is compared as it is
        # stored, so it can look changed.
        "filters_not_run": [_clip_units(name, MAX_FILTER_NAME_CHARS)[0]
                            for name in identity["filters"]],
    }


def _revalidated(registry, corroboration, freshness, clock, bound, accounting,
                 transport, workspace, identity, mission_ref, task_id,
                 finished=None):
    """``_Refused`` unless, NOW, the registry and the catalog still show the
    SAME active assignment and the workspace is still the observed one. The
    owner need not have refreshed: a removal or repoint is seen here."""
    when = "while the workspace was read; the answer is discarded"
    _registered(_registry_now(registry, accounting), bound, when)
    if _corroborated(corroboration(), freshness, clock(), mission_ref,
                     task_id, finished=finished,
                     accounting=accounting) != bound:
        raise _Refused(PROBLEM_ASSIGNMENT_CHANGED, "the assignment changed %s"
                       % when)
    try:
        again, _files = _observe(transport, workspace, bound, accounting)
    except _Moved:
        again = None
    if again != identity:
        raise _Refused(PROBLEM_MOVED_DURING_READ, "the workspace changed %s"
                       % when)


def _read(registry, corroboration, freshness, clock, transport, mission_ref,
          task_id, snapshot, file_id, accounting, finished=None):
    bound = _corroborated(corroboration(), freshness, clock(), mission_ref,
                          task_id, finished=finished, accounting=accounting)
    _registered(_registry_now(registry, accounting), bound, "now")
    workspace = _workspace(bound["workspace"])
    held = []
    try:
        if file_id is None:
            identity, files = _observe_stable(transport, workspace, bound,
                                              accounting)
        else:
            # A file is asked for against a snapshot that was itself observed
            # twice and agreed with itself: one observation EQUAL to it is the
            # same proof, and a different one is a workspace that moved. (The
            # release check below observes again.) The observation's private
            # git directory stays open: the file is read through it.
            try:
                identity, files = _observe(transport, workspace, bound,
                                           accounting, held)
            except _Moved:
                raise _Refused(PROBLEM_MOVED_DURING_READ, "the workspace"
                               " changed while it was read")
        current = _snapshot(identity)
        file_ = None
        if file_id is not None and snapshot != current:
            raise _Refused(PROBLEM_SNAPSHOT_MOVED, "the workspace is no longer"
                           " the snapshot this file was listed in; refresh the"
                           " overview")
        try:
            if file_id is None:
                _annotate(workspace, files, accounting)
            else:
                # One file is read: only it is annotated (an untracked file is
                # read once for its state, not every untracked file in the
                # tree).
                chosen = [f for f in files
                          if _file_id(current, f["path"]) == file_id]
                if len(chosen) != 1:
                    raise _Refused(PROBLEM_FILE_NOT_LISTED, "this snapshot"
                                   " lists no such file")
                _annotate(workspace, chosen, accounting)
                file_ = _file_block(workspace, transport, chosen[0], current,
                                    identity["head"], identity, accounting,
                                    held[1])
                _verified(accounting, held[1])
        except _Moved:
            raise _Refused(PROBLEM_MOVED_DURING_READ, "the workspace changed"
                           " while it was read")
    finally:
        if held:
            held[0].__exit__(None, None, None)
    _revalidated(registry, corroboration, freshness, clock, bound, accounting,
                 transport, workspace, identity, mission_ref, task_id,
                 finished=finished)
    return (_target(bound, workspace, identity), _comparison(identity),
            current, _overview(files, current) if file_id is None else None,
            None if file_id is None else file_)


def read(registry, corroboration, freshness, clock, transport, mission_ref,
         task_id, snapshot=None, file_id=None, finished=None):
    """One review request; ALWAYS an answer (observed, or refused with its
    code and an honest account of what was read). ``corroboration`` returns
    the owner's last refreshed answer (or None) each time it is called;
    ``freshness`` is the served version's consumer operation; ``clock`` the
    owner's clock; ``transport`` the constructor-injected git boundary. With
    no ``file_id`` this is the OVERVIEW; with one (and the ``snapshot`` it was
    listed in) it is that file's diff. ``finished`` is the optional provider of a
    COMPLETED Mission's record-side admission (None: a COMPLETED Mission is
    refused as not active, as before); the version 2 owner passes one."""
    view = VIEW_OVERVIEW if file_id is None else VIEW_FILE
    accounting = Accounting()
    try:
        target, comparison, current, overview, file_ = _read(
            registry, corroboration, freshness, clock, transport,
            mission_ref, task_id, snapshot, file_id, accounting,
            finished=finished)
    except _Refused as exc:
        return refusal(exc.problem, exc.detail, view, mission_ref, task_id,
                       snapshot, file_id, clock(), accounting)
    return _answer(STATE_OBSERVED, None, None, view, mission_ref, task_id,
                   current, file_id, target, comparison, overview, file_,
                   clock(), accounting)
