"""Read-only observability projection for one Herdr repository.

`observe()` builds a schema-versioned, bounded, point-in-time projection of
one repository's herd: repository/Git identity, configuration, mission,
current task, runtime topology, allowlisted live-agent state, recorded child
dependencies, review metadata, artifact presence/freshness, bounded recent
task summaries, and explicit per-source diagnostics.

Hard guarantees:

- Strictly read-only. This module never creates, rewrites, appends to, or
  repairs any file; never prompts, focuses, starts, or stops an agent; and
  never mutates Git state. Its only live runtime query is the read-only
  `herdr agent get` reached through `herdr.runtime.agent_info`, and every
  Git query runs under `--no-optional-locks` so even `.git/index` is left
  byte-identical.
- Never raises. Every source is individually guarded; a destroyed input
  yields a bounded partial observation with diagnostics, not a traceback.
- Structurally stable. The top-level key set is fixed and every section is
  always present with a `state` field from the closed vocabulary
  `available | missing | malformed | unreadable | unavailable | empty`.
- Hard-bounded. All caps below are module constants and are never derived
  from repository input (which is exactly what may be corrupt).

`completeness` is visibility only, never a gate: it is "PARTIAL" when any
diagnostic records a `malformed`, `unreadable`, or `unavailable` source
(truth exists that could not be seen), and "COMPLETE" otherwise — cleanly
observed absences (`missing`, `empty`) do not demote it. Listing
truncations whose totals are exact are disclosed as `available`
diagnostics and do not demote; an exhausted directory-scan budget makes
every derived count a lower bound, so it is disclosed as an `unavailable`
diagnostic and does demote.
"""

from __future__ import annotations

import json
import re
import subprocess
import time
from pathlib import Path
from stat import S_ISDIR

from . import vintage
from .runtime import agent_info


#: Bumped to 3 by I7b: `config.roles[].model` is renamed
#: `configured_model`. A rename rather than an addition, deliberately:
#: leaving `model` in place beside a qualified twin would let a
#: consumer keep reading the unqualified key forever, which is the
#: defect rather than a migration path.
#:
#: Bumped to 2 by I6: four sections were added (`vintage`,
#: `checkpoint`, `roles`, `turns`) and `mission` gained the
#: vintage fields it needs to be omitted honestly. A consumer pinned
#: to v1 sees a different shape, and the version is how it finds out
#: rather than by a missing key at read time.
OBSERVE_SCHEMA_VERSION = 3

# ---- hard bound constants (module-level; NEVER derived from input) ----
_OBSERVE_MAX_FILE_BYTES = 1048576   # refuse to read a state file larger than this
_OBSERVE_MAX_AGENT_PROBES = 64      # live `herdr agent get` calls per run
_OBSERVE_MAX_LISTED_AGENTS = 32
_OBSERVE_MAX_RECENT_TASKS = 10
_OBSERVE_MAX_REVIEW_FILES = 40
_OBSERVE_MAX_CHILDREN = 32
_OBSERVE_MAX_ARTIFACTS = 16
_OBSERVE_MAX_STRING = 200           # EVERY projected scalar string truncated to this
_OBSERVE_MAX_DIR_ENTRIES = 2000     # cap directory scans before sorting
_OBSERVE_MAX_DIRTY_LINES = 2000     # cap on porcelain STATUS LINES counted as dirty paths
_OBSERVE_STALE_SECONDS = 86400      # artifact freshness LABEL only

# Fixed artifact allowlist (plus a bounded newest-first glob of *brief-*.md).
_OBSERVE_ARTIFACT_NAMES = (
    "mission.json",
    "task.json",
    "runtime.json",
    "children.json",
    "task-checkpoint.md",
    "supervisor-status.md",
)

# Diagnostic states that demote completeness to PARTIAL.
_PARTIAL_STATES = ("malformed", "unreadable", "unavailable")


def _trunc(value):
    """Bound one projected scalar string to _OBSERVE_MAX_STRING characters
    total, ellipsis included; non-strings pass through."""
    if not isinstance(value, str):
        return value
    if len(value) <= _OBSERVE_MAX_STRING:
        return value
    return value[:_OBSERVE_MAX_STRING - 1] + "…"


def _note(diags, source, state, detail):
    diags.append({
        "source": source,
        "state": state,
        "detail": _trunc(str(detail)),
    })


def _human_duration(seconds):
    """Local duration formatter (same semantics as herdctl.human_duration).

    Kept local on purpose: importing the CLI from the package would be a
    layering inversion.
    """
    try:
        seconds = max(0, int(seconds))
    except Exception:
        return "?"
    h, rem = divmod(seconds, 3600)
    m, sec = divmod(rem, 60)
    if h:
        return f"{h}h {m}m {sec}s"
    if m:
        return f"{m}m {sec}s"
    return f"{sec}s"


def _git_query(repo, *args):
    """Run one read-only git query; return stdout, or None on any failure.

    `--no-optional-locks` is required: without it a plain status query
    refreshes and rewrites `.git/index`, which would violate the
    byte-for-byte non-mutation guarantee.
    """
    cmd = ["git", "--no-optional-locks", "-C", str(repo), *args]
    try:
        p = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
        )
    except Exception:
        return None
    if p.returncode != 0:
        return None
    return p.stdout


def _read_state_text(path):
    """Bounded raw read of one state file. Returns (text, state, detail).

    `text` is non-None only when state == "available". Size is checked
    BEFORE reading so an oversized file is never pulled into memory.
    """
    try:
        if path.is_dir():
            return None, "unreadable", f"{path.name} is a directory"
        if not path.is_file():
            return None, "missing", f"{path.name} does not exist"
        size = path.stat().st_size
        if size > _OBSERVE_MAX_FILE_BYTES:
            return (
                None,
                "unreadable",
                f"{path.name} is {size} bytes "
                f"(observation limit {_OBSERVE_MAX_FILE_BYTES})",
            )
        return path.read_text(errors="replace"), "available", None
    except UnicodeDecodeError:
        return None, "unreadable", f"{path.name} could not be decoded"
    except OSError as exc:
        return None, "unreadable", f"{path.name}: {exc.__class__.__name__}"


def _read_json_object(path):
    """Bounded read of one JSON-object state file. Returns (value, state, detail)."""
    text, state, detail = _read_state_text(path)
    if state != "available":
        return None, state, detail
    try:
        value = json.loads(text)
    except ValueError:
        return None, "malformed", f"{path.name} is not valid JSON"
    if not isinstance(value, dict):
        return None, "malformed", f"{path.name} does not contain a JSON object"
    return value, "available", None


def _int_or_none(value):
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def _str_or_none(value):
    return _trunc(value) if isinstance(value, str) else None


def _list_dir(path, suffix=None):
    """Bounded directory scan. Returns (entries, hit_cap).

    At most _OBSERVE_MAX_DIR_ENTRIES directory entries are SCANNED before
    any sorting, so a directory with a million files cannot blow up memory
    or wall clock. `hit_cap` reflects exhausted scan budget — every scanned
    entry consumes budget whether or not it matches `suffix`, and a True
    value means more entries exist than could be scanned, so any counts
    derived from `entries` are lower bounds over an arbitrary subset and
    must be disclosed as such. Never raises.
    """
    entries = []
    hit_cap = False
    try:
        iterator = path.iterdir()
    except OSError:
        return entries, hit_cap
    try:
        scanned = 0
        for entry in iterator:
            if scanned >= _OBSERVE_MAX_DIR_ENTRIES:
                hit_cap = True
                break
            scanned += 1
            if suffix is not None and not entry.name.endswith(suffix):
                continue
            entries.append(entry)
    except OSError:
        pass
    return entries, hit_cap


def _stat_or_none(path):
    try:
        return path.stat()
    except OSError:
        return None


# ---- section builders -------------------------------------------------


def _repository_section(root, diags):
    section = {
        "state": "available",
        "path": _trunc(str(root)),
        "is_git_repo": None,
        "branch": None,
        "head": None,
        "head_short": None,
        "dirty": None,
        "dirty_file_count": None,
        "dirty_file_count_capped": None,
        "remote": None,
    }
    try:
        exists = root.is_dir()
    except OSError:
        exists = False
    if not exists:
        section["state"] = "missing"
        _note(diags, "repository", "missing", "repository path is not a directory")
        return section
    inside = _git_query(root, "rev-parse", "--is-inside-work-tree")
    if inside is None or inside.strip() != "true":
        section["is_git_repo"] = False
        _note(diags, "repository", "empty", "not a git repository; git identity unavailable")
        return section
    section["is_git_repo"] = True
    branch = _git_query(root, "branch", "--show-current")
    if branch is not None and branch.strip():
        section["branch"] = _trunc(branch.strip())
    head = _git_query(root, "rev-parse", "HEAD")
    if head is not None and re.fullmatch(r"[0-9a-f]{4,64}", head.strip() or ""):
        section["head"] = _trunc(head.strip())
        section["head_short"] = _trunc(head.strip()[:12])
    porcelain = _git_query(root, "status", "--porcelain")
    if porcelain is not None:
        # The reported count is capped at a hard constant and NEVER
        # presented as exact when the cap applies: the capped flag is set
        # and a demoting diagnostic discloses that the true count is higher.
        line_count = len(porcelain.splitlines())
        capped = line_count > _OBSERVE_MAX_DIRTY_LINES
        section["dirty_file_count"] = min(line_count, _OBSERVE_MAX_DIRTY_LINES)
        section["dirty_file_count_capped"] = capped
        section["dirty"] = line_count > 0
        if capped:
            _note(
                diags, "repository", "unavailable",
                f"dirty path count capped at {_OBSERVE_MAX_DIRTY_LINES} "
                "status lines; the true count is higher",
            )
    remote = _git_query(root, "remote", "get-url", "origin")
    if remote is not None and remote.strip():
        section["remote"] = _trunc(remote.strip())
    return section


def _model_from_args(args):
    """Parse the model from role args `--model`/`-m`; never guess."""
    if not isinstance(args, list):
        return None
    for i, item in enumerate(args[:-1]):
        if item in ("--model", "-m") and isinstance(args[i + 1], str):
            return _trunc(args[i + 1])
    return None


def _config_section(root, diags):
    section = {
        "state": "missing",
        "project_name": None,
        "preset": None,
        "orchestration": {"leads": None, "pods": None, "heartbeat_seconds": None},
        "review": {"required": None, "max_rounds": None},
        "git": {"commit": None, "push": None},
        "roles": [],
    }
    data, state, detail = _read_json_object(root / ".herd" / "herd.config.json")
    section["state"] = state
    if state != "available":
        _note(diags, "config", state, detail)
        return section, None
    project = data.get("project")
    if isinstance(project, dict):
        section["project_name"] = _str_or_none(project.get("name"))
    section["preset"] = _str_or_none(data.get("preset"))
    orchestration = data.get("orchestration")
    if isinstance(orchestration, dict):
        for key in ("leads", "pods", "heartbeat_seconds"):
            section["orchestration"][key] = _int_or_none(orchestration.get(key))
    policy = data.get("policy")
    if isinstance(policy, dict):
        review = policy.get("review")
        if isinstance(review, dict):
            required = review.get("required")
            section["review"]["required"] = required if isinstance(required, bool) else None
            section["review"]["max_rounds"] = _int_or_none(review.get("max_rounds"))
        git = policy.get("git")
        if isinstance(git, dict):
            section["git"]["commit"] = _str_or_none(git.get("commit"))
            section["git"]["push"] = _str_or_none(git.get("push"))
    roles = data.get("roles")
    if isinstance(roles, dict):
        names = sorted(roles, key=str)
        for role in names[:_OBSERVE_MAX_LISTED_AGENTS]:
            spec = roles[role] if isinstance(roles[role], dict) else {}
            section["roles"].append({
                "role": _trunc(str(role)),
                "kind": _str_or_none(spec.get("kind")),
                # R-74 F3: NAMED `configured_model`, not `model`.
                #
                # I6's rule is to carry the identity of what you
                # describe or be omitted, and a JSON consumer is handed
                # a key rather than the section heading — it reads
                # `obs["config"]["roles"][i][<key>]`. A key called
                # `model` reads as THE ROLE'S MODEL, which is a claim
                # about the running herd that this value cannot make:
                # it is parsed from the role's `--model` argument and
                # says what the herd was ASKED to start. The key
                # carries the qualification so it travels with the
                # value into every consumer.
                "configured_model": _model_from_args(spec.get("args")),
            })
        if len(names) > _OBSERVE_MAX_LISTED_AGENTS:
            _note(
                diags, "config", "available",
                f"roles truncated to {_OBSERVE_MAX_LISTED_AGENTS} of {len(names)}",
            )
        if section["roles"]:
            # R-76: THE LIMIT REACHES THE STRUCTURED CONSUMER TOO.
            #
            # `configured_model` tells an author THAT THIS VALUE IS
            # CONFIGURED. It does not tell them that NO RUNNING VALUE
            # EXISTS — and a reasonable author reading a qualified key
            # INFERS AN UNQUALIFIED COUNTERPART somewhere they have
            # not yet found. A tool built on it could then surface it
            # to a human as "model", re-introducing the defect one
            # layer downstream, and its author would have had no way
            # to learn the limit from the interface they consumed.
            # F3 propagating through an API boundary is still F3.
            #
            # WHAT THIS IS AND IS NOT. It is a statement about OUR
            # OBSERVABILITY — what the agent interface exposes — and
            # it is checkable against that interface. It is NOT a claim about an agent: there is no `running_model`, no
            # `model_observable`, no verdict, and no field whose value
            # would assert something about a model that is running.
            # The prohibition was against fabricating what IS; this
            # describes what we can SEE.
            # FRONT-LOADED DELIBERATELY, and this is a finding about
            # the channel: `_note` TRUNCATES its detail. The first
            # draft of this sentence explained `configured_model`
            # first and closed the wrong inference last — and the cap
            # cut off exactly the closing clause, leaving the half a
            # consumer already knew. A limit statement on a truncating
            # channel has to lead with the load-bearing fact.
            #
            # AND IT FITS WITHIN THE BOUND (R-78). Front-loading kept
            # the meaning intact while the delivered bytes still
            # ended MID-WORD, and a sentence delivered mid-word to a
            # machine consumer is one whose author did not decide
            # where it should end. Clause 3 is shortened until the
            # whole sentence completes inside the cap: it still says
            # what the KEY means and asserts no fact about an agent.
            _note(
                diags, "config", "available",
                "NO running-model value exists in this document and"
                " there is no unqualified `model` key: the model a"
                " RUNNING agent uses is not observable through the"
                " agent interface. `configured_model` states intent.",
            )
    return section, data


def _mission_section(root, diags):
    section = {
        "state": "missing",
        "version": None,
        "objective": None,
        "constraint_count": None,
        "rule_count": None,
        "acceptance_count": None,
        "verification_count": None,
        "created_at": None,
    }
    data, state, detail = _read_json_object(root / ".herd" / "state" / "mission.json")
    section["state"] = state
    if state != "available":
        _note(diags, "mission", state, detail)
        return section
    section["version"] = _int_or_none(data.get("version"))
    section["objective"] = _str_or_none(data.get("objective"))
    for field, key in (
        ("constraint_count", "constraints"),
        ("rule_count", "rules"),
        ("acceptance_count", "acceptance_criteria"),
        ("verification_count", "verification"),
    ):
        value = data.get(key)
        section[field] = len(value) if isinstance(value, list) else None
    section["created_at"] = _int_or_none(data.get("created_at"))
    # R-46 AJ-1: the mission's own TASK IDENTITY, from the artifact.
    # `mission.json` in this repository carries none, and that absence
    # is the finding rather than a gap to fill with a default: a field
    # whose vintage is unestablished is OMITTED at render.
    section["task_id"] = vintage.task_id_of_document(data)
    return section


def _task_section(root, now, diags):
    section = {
        "state": "missing",
        "id": None,
        "status": None,
        "description": None,
        "started_at": None,
        "elapsed_seconds": None,
        "elapsed_human": None,
        "heartbeat_count": None,
        "manual_prompt_count": None,
        "rejection_drill": None,
        "rule_count": None,
    }
    data, state, detail = _read_json_object(root / ".herd" / "state" / "task.json")
    section["state"] = state
    if state != "available":
        _note(diags, "task", state, detail)
        return section, None
    section["id"] = _str_or_none(data.get("id"))
    section["status"] = _str_or_none(data.get("status"))
    section["description"] = _str_or_none(data.get("description"))
    started = _int_or_none(data.get("started_at"))
    section["started_at"] = started
    if started is not None:
        completed = _int_or_none(data.get("completed_at"))
        elapsed = (completed if completed is not None else now) - started
        section["elapsed_seconds"] = max(0, elapsed)
        section["elapsed_human"] = _human_duration(elapsed)
    section["heartbeat_count"] = _int_or_none(data.get("heartbeat_count"))
    section["manual_prompt_count"] = _int_or_none(data.get("manual_prompt_count"))
    drill = data.get("rejection_drill")
    section["rejection_drill"] = drill if isinstance(drill, bool) else None
    policy = data.get("policy")
    if isinstance(policy, dict) and isinstance(policy.get("rules"), list):
        section["rule_count"] = len(policy["rules"])
    task_id = data.get("id") if isinstance(data.get("id"), str) else None
    return section, task_id


def _runtime_section(root, diags):
    section = {
        "state": "missing",
        "version": None,
        "workspace_id": None,
        "agent_count": None,
        "pane_count": None,
        "created_at": None,
    }
    data, state, detail = _read_json_object(root / ".herd" / "state" / "runtime.json")
    section["state"] = state
    if state != "available":
        _note(diags, "runtime", state, detail)
        return section, state, None
    section["version"] = _int_or_none(data.get("version"))
    section["workspace_id"] = _str_or_none(data.get("workspace_id"))
    agents = data.get("agents")
    if isinstance(agents, dict):
        section["agent_count"] = len(agents)
    panes = data.get("panes")
    if isinstance(panes, dict):
        section["pane_count"] = len(panes)
    section["created_at"] = _int_or_none(data.get("created_at"))
    return section, state, (agents if "agents" in data else None)


def _expected_roles(config_data):
    """Best-effort expected logical roles, clamped so corrupt counts stay cheap."""
    if not isinstance(config_data, dict):
        return set()
    orchestration = config_data.get("orchestration")
    if not isinstance(orchestration, dict):
        return set()

    def clamp(key):
        value = orchestration.get(key, 1)
        if isinstance(value, bool) or not isinstance(value, int):
            return 1
        return max(1, min(value, _OBSERVE_MAX_LISTED_AGENTS))

    leads = clamp("leads")
    pods = clamp("pods")
    expected = {"supervisor"}
    expected |= {f"lead{i}" for i in range(1, leads + 1)}
    for i in range(1, pods + 1):
        expected.add(f"executor{i}")
        expected.add(f"reviewer{i}")
    return expected


def _probe_one(name):
    """One bounded read-only status query. Returns (status, probe).

    Only the allowlisted status string is projected; the raw payload from
    the runtime query is never emitted.
    """
    if not isinstance(name, str) or not name:
        return None, "unknown"
    try:
        info = agent_info(name)
        status = info.get("status") if isinstance(info, dict) else None
    except Exception:
        return None, "missing"
    if status == "missing":
        return None, "missing"
    if not isinstance(status, str) or status == "unknown":
        return None, "unknown"
    return status, "ok"


def _agents_section(config_data, runtime_state, agents_value, probe_agents, diags):
    section = {
        "state": "unavailable",
        "probed": 0,
        "unprobed": 0,
        "truncated": False,
        "listed": [],
    }
    if runtime_state != "available":
        section["state"] = runtime_state
        _note(diags, "agents", runtime_state, "runtime state is not available; no agents to observe")
        return section
    if agents_value is None:
        section["state"] = "empty"
        _note(diags, "agents", "empty", "runtime state has no `agents` mapping")
        return section
    if not isinstance(agents_value, dict):
        section["state"] = "malformed"
        _note(diags, "agents", "malformed", "`agents` in runtime state is not a JSON object")
        return section
    if not agents_value:
        section["state"] = "empty"
        _note(diags, "agents", "empty", "runtime `agents` mapping is empty")
        return section

    # Expected (config-derived) roles are ordered first so a truncated
    # probe can never hide an expected-role problem.
    ordered = sorted(agents_value, key=str)
    expected = _expected_roles(config_data)
    if expected:
        ordered = (
            [k for k in ordered if isinstance(k, str) and k in expected]
            + [k for k in ordered if not (isinstance(k, str) and k in expected)]
        )
    total = len(ordered)
    to_probe = ordered[:_OBSERVE_MAX_AGENT_PROBES] if probe_agents else []
    probes = {}
    for logical in to_probe:
        probes[logical] = _probe_one(agents_value[logical])
    section["probed"] = len(to_probe)
    section["unprobed"] = total - len(to_probe)
    section["truncated"] = total > _OBSERVE_MAX_LISTED_AGENTS
    for logical in ordered[:_OBSERVE_MAX_LISTED_AGENTS]:
        name = agents_value[logical]
        status, probe = probes.get(logical, (None, "unprobed"))
        section["listed"].append({
            "logical": _trunc(str(logical)),
            "agent": _trunc(name) if isinstance(name, str) else None,
            "status": _trunc(status) if isinstance(status, str) else None,
            "probe": probe,
        })
    section["state"] = "available"
    if not probe_agents:
        _note(
            diags, "agents", "unavailable",
            f"live probing disabled; {total} agent(s) left unprobed",
        )
    elif section["unprobed"]:
        _note(
            diags, "agents", "unavailable",
            f"{section['unprobed']} agent(s) beyond the probe cap "
            f"({_OBSERVE_MAX_AGENT_PROBES}) were not probed",
        )
    if section["truncated"]:
        _note(
            diags, "agents", "available",
            f"listing truncated to {_OBSERVE_MAX_LISTED_AGENTS} of {total} agent(s)",
        )
    return section


def _children_section(root, task_id, diags):
    """Recorded child dependencies for the current task, from THIS repo only.

    Only the record persisted in `.herd/state/children.json` is projected;
    child repositories' filesystems are never touched (repository scope),
    so `recorded_status` is the recorded value, not resolved liveness.
    """
    section = {
        "state": "missing",
        "parent_task_id": _trunc(task_id) if isinstance(task_id, str) else None,
        "count": None,
        "truncated": False,
        "listed": [],
    }
    data, state, detail = _read_json_object(root / ".herd" / "state" / "children.json")
    if state == "missing":
        # Absent children.json is the normal no-children representation.
        section["state"] = "empty"
        section["count"] = 0
        return section
    section["state"] = state
    if state != "available":
        _note(diags, "children", state, detail)
        return section
    records = data.get("children")
    if not isinstance(records, list):
        section["state"] = "malformed"
        _note(diags, "children", "malformed", "`children` in children.json is not a list")
        return section
    if task_id is None:
        section["state"] = "empty"
        section["count"] = 0
        _note(diags, "children", "empty", "no current task id to correlate children against")
        return section
    # `count` is the TRUE matched count over every record: the record list
    # is already memory-bounded by _OBSERVE_MAX_FILE_BYTES at read time,
    # so no record cap is needed and none is applied — only the listing
    # below is truncated, with a disclosure diagnostic.
    matched = [
        rec for rec in records
        if isinstance(rec, dict) and rec.get("parent_task_id") == task_id
    ]
    section["count"] = len(matched)
    section["truncated"] = len(matched) > _OBSERVE_MAX_CHILDREN
    for rec in matched[:_OBSERVE_MAX_CHILDREN]:
        section["listed"].append({
            "repo": _str_or_none(rec.get("repo")),
            "task_id": _str_or_none(rec.get("task_id")),
            "recorded_status": _str_or_none(rec.get("task_status")),
            "role": _str_or_none(rec.get("role")),
        })
    if section["truncated"]:
        _note(
            diags, "children", "available",
            f"children truncated to {_OBSERVE_MAX_CHILDREN} of {len(matched)}",
        )
    section["state"] = "available" if matched else "empty"
    return section


def observe_spawn_records(repo):
    """Project every persisted child-spawn record from THIS repository.

    This is deliberately separate from canonical :func:`observe`, whose
    ``children`` section remains correlated to the current task. The
    projection reads only ``.herd/state/children.json`` under ``repo``;
    child repository paths are returned as bounded scalar data and are
    never opened, resolved, or followed.

    The return shape is fixed and bounded. ``count`` is exact whenever
    the JSON object and its ``children`` list are readable; ``truncated``
    discloses when the exact count exceeds the listing cap. Any malformed
    record makes the whole projection ``malformed`` so reconciliation can
    fail closed instead of silently ignoring unprojectable evidence.
    """
    projection = {
        "state": "unavailable",
        "count": None,
        "truncated": False,
        "listed": [],
        "detail": None,
    }
    try:
        root = Path(repo).expanduser()
        data, state, detail = _read_json_object(
            root / ".herd" / "state" / "children.json"
        )
        if state == "missing":
            projection.update({"state": "empty", "count": 0})
            return projection
        if state != "available":
            projection.update({"state": state, "detail": _trunc(detail)})
            return projection
        records = data.get("children")
        if not isinstance(records, list):
            projection.update({
                "state": "malformed",
                "detail": "`children` in children.json is not a list",
            })
            return projection

        projection["count"] = len(records)
        projection["truncated"] = len(records) > _OBSERVE_MAX_CHILDREN
        malformed = None
        for index, record in enumerate(records):
            if not isinstance(record, dict):
                malformed = "child record %d is not a JSON object" % index
                break
            parent_task_id = record.get("parent_task_id")
            dependency = record.get("dependency")
            repo_value = record.get("repo")
            task_id = record.get("task_id")
            if not (
                (parent_task_id is None or isinstance(parent_task_id, str))
                and isinstance(dependency, bool)
                and isinstance(repo_value, str)
                and repo_value.strip()
                and isinstance(task_id, str)
                and task_id.strip()
            ):
                malformed = "child record %d has malformed identity fields" % index
                break
            if any(
                isinstance(value, str) and len(value) > _OBSERVE_MAX_STRING
                for value in (parent_task_id, repo_value, task_id)
            ):
                malformed = "child record %d has an overlong identity field" % index
                break
            if index < _OBSERVE_MAX_CHILDREN:
                projection["listed"].append({
                    "parent_task_id": _str_or_none(parent_task_id),
                    "dependency": dependency,
                    "repo": _str_or_none(repo_value),
                    "task_id": _str_or_none(task_id),
                    "recorded_status": _str_or_none(record.get("task_status")),
                    "role": _str_or_none(record.get("role")),
                })
        if malformed is not None:
            # I4 item 3. The scan STOPS at the first malformed record,
            # so `listed` holds only the records before it while
            # `count` holds the full list length and `truncated` was
            # derived before the scan. Returning that trio asserts a
            # complete short listing: count 3, listed 1, truncated
            # False, with the inconsistency visible only to a caller
            # that also reads `state`. That is the recorded "silent
            # truncation presented as fact" class, so the malformed
            # return carries NO count and NO listing — matching the
            # `unreadable` path, which already returns count None.
            projection.update({
                "state": "malformed",
                "detail": malformed,
                "count": None,
                "truncated": False,
                "listed": [],
            })
            return projection
        projection["state"] = "available" if records else "empty"
        if projection["truncated"]:
            projection["detail"] = (
                "spawn records truncated to %d of %d"
                % (_OBSERVE_MAX_CHILDREN, len(records))
            )
        return projection
    except Exception as exc:
        projection.update({
            "state": "unavailable",
            "detail": _trunc(
                "spawn-record projection failed: %s"
                % exc.__class__.__name__
            ),
        })
        return projection


# Canonical header line written into every persisted review artifact by
# `herdctl review-decision`. Any recorded token is captured; only APPROVE
# and REJECT are valid decisions.
_PROTOCOL_HEADER_RE = re.compile(r"^Protocol token: `([^`\n]*)`$", re.M)
_PROTOCOL_VALID_TOKENS = ("APPROVE", "REJECT")


def _decision_token(text):
    """Decision for one persisted review artifact; never guesses.

    A canonical artifact carries a `Protocol token:` header line before its
    `## Transcript` marker. When the marker exists and a header line is
    present in the preamble before it, that header is AUTHORITATIVE:
    `APPROVE`/`REJECT` resolve to that value, and any other recorded token
    (`MISSING`, `ACCEPT`, ...) yields None WITHOUT consulting the
    transcript — prose quoting the protocol can never override the
    canonical record. The exact contiguous `HERD_DECISION:` token scan is
    the fallback ONLY when no `Protocol token:` line of any shape exists in
    a canonical preamble, or when the text has no `## Transcript` marker at
    all (non-canonical). A malformed preamble header (e.g. indented) is
    authoritative-but-invalid: it yields None and suppresses the fallback.
    Header-shaped lines are never honoured outside a canonical preamble.
    """
    if text.startswith("## Transcript"):
        canonical = True
        header_region = ""
    else:
        marker = text.find("\n## Transcript")
        canonical = marker >= 0
        header_region = text[:marker] if canonical else ""
    if canonical:
        header = _PROTOCOL_HEADER_RE.search(header_region)
        if header:
            token = header.group(1)
            return token if token in _PROTOCOL_VALID_TOKENS else None
        for line in header_region.splitlines():
            if line.strip().startswith("Protocol token:"):
                # A malformed header line (indented, wrong quoting, ...) is
                # authoritative-but-invalid: a present-but-unparseable
                # record blocks the fallback rather than letting transcript
                # text decide over it.
                return None
    best = None
    best_pos = -1
    for token, decision in (
        ("HERD_DECISION: APPROVE", "APPROVE"),
        ("HERD_DECISION: REJECT", "REJECT"),
    ):
        pos = text.rfind(token)
        if pos > best_pos:
            best_pos = pos
            best = decision
    return best


def _reviews_section(root, task_id, diags):
    """Bounded review-file metadata ONLY — transcript text is never emitted."""
    section = {
        "state": "missing",
        "task_id": _trunc(task_id) if isinstance(task_id, str) else None,
        "rounds": None,
        "total_files": None,
        "truncated": False,
        "listed": [],
    }
    directory = root / ".herd" / "state" / "reviews"
    try:
        if not directory.is_dir():
            section["state"] = "missing"
            return section
    except OSError:
        section["state"] = "unreadable"
        _note(diags, "reviews", "unreadable", "reviews directory could not be inspected")
        return section
    entries, hit_cap = _list_dir(directory)
    if hit_cap:
        # Exhausted scan budget: counts below are lower bounds over an
        # arbitrary scanned subset — disclosed, and demotes completeness.
        _note(
            diags, "reviews", "unavailable",
            f"directory scan capped at {_OBSERVE_MAX_DIR_ENTRIES} entries; "
            "round and file counts are lower bounds",
        )
    if task_id is None:
        section["total_files"] = 0
        section["state"] = "empty"
        section["rounds"] = 0
        _note(diags, "reviews", "empty", "no current task id to correlate reviews against")
        return section
    pattern = re.compile(re.escape(task_id) + r"-round-(\d+)\.md\Z")
    matched = []
    for entry in entries:
        m = pattern.fullmatch(entry.name)
        if m:
            try:
                matched.append((int(m.group(1)), entry))
            except ValueError:
                continue
    matched.sort(key=lambda pair: pair[0])
    # `total_files` counts THIS task's review round files (pre-truncation),
    # matching what the field name promises.
    section["total_files"] = len(matched)
    section["rounds"] = matched[-1][0] if matched else 0
    section["truncated"] = len(matched) > _OBSERVE_MAX_REVIEW_FILES
    unreadable = 0
    for round_no, entry in matched[-_OBSERVE_MAX_REVIEW_FILES:]:
        st = _stat_or_none(entry)
        text, state, _detail = _read_state_text(entry)
        decision = _decision_token(text) if state == "available" else None
        if state != "available":
            unreadable += 1
        section["listed"].append({
            "file": _trunc(entry.name),
            "round": round_no,
            "decision": decision,
            "size": int(st.st_size) if st else None,
            "mtime": int(st.st_mtime) if st else None,
        })
    if section["truncated"]:
        _note(
            diags, "reviews", "available",
            f"reviews truncated to the most recent {_OBSERVE_MAX_REVIEW_FILES} "
            f"of {len(matched)} round file(s)",
        )
    if unreadable:
        _note(
            diags, "reviews", "unreadable",
            f"{unreadable} review file(s) could not be inspected for a decision token",
        )
    section["state"] = "available" if matched else "empty"
    return section


def _artifact_entry(name, path, now):
    """One artifact projection. Returns (entry, error_name); never raises.

    The entry is ALWAYS produced — a stat failure yields an absent-shaped
    entry plus the error name, so a fixed-allowlist name can never vanish
    from the listing (the section is structural, never conditional).
    """
    entry = {
        "name": _trunc(name),
        "present": False,
        "size": None,
        "mtime": None,
        "age_seconds": None,
        "freshness": None,
    }
    try:
        st = path.stat()
    except FileNotFoundError:
        return entry, None
    except OSError as exc:
        return entry, exc.__class__.__name__
    if S_ISDIR(st.st_mode):
        return entry, None
    age = max(0, now - int(st.st_mtime))
    entry.update({
        "present": True,
        "size": int(st.st_size),
        "mtime": int(st.st_mtime),
        "age_seconds": age,
        "freshness": "fresh" if age <= _OBSERVE_STALE_SECONDS else "stale",
    })
    return entry, None


def _role_binding_section(root, agents_value, diags):
    """Which roles are bound, and HOW STRONGLY (R-61 AY-4).

    I2c's residual: `identity.binding_gap` requires a NON-EMPTY stable
    mapping, not a COMPLETE one, so a record missing `pane_id` or
    `cwd` still binds and `classify` then compares fewer fields. The
    binding is not wrong; it is WEAKER, and no surface said so. A surface
    reporting a role as bound has to be able to say which fields the
    comparison will actually use.
    """
    from . import identity
    section = {
        "state": "missing",
        "stable_fields": list(identity.STABLE_FIELDS),
        "listed": [],
        # A role this herd RUNS and has NOT bound. Named rather than described: "which roles are bound" is unanswered until the
                # unbound ones are visible, and R-53's whole finding was that
        # within a surface an unbound role must not read as healthy.
        "unbound_roles": [],
    }
    try:
        document = identity.load_bindings(root / ".herd")
    except identity.BindingsCorrupt as exc:
        section["state"] = "unreadable"
        _note(diags, "roles", "unreadable", str(exc))
        return section
    except OSError as exc:
        section["state"] = "unreadable"
        _note(diags, "roles", "unreadable", exc.__class__.__name__)
        return section
    roles = document.get("roles") or {}
    if not roles:
        section["state"] = "empty"
        _note(
            diags, "roles", "empty",
            "no role bindings are recorded; every role classifies as"
            " UNBOUND until bootstrap binds them (R-53)",
        )
        return section
    for logical in sorted(roles):
        binding = roles[logical]
        strength, captured, missing = vintage.binding_strength(binding)
        section["listed"].append({
            "logical": _trunc(logical),
            "agent": _trunc(_str_or_none(
                binding.get("agent") if isinstance(binding, dict)
                else None
            )),
            "strength": strength,
            "captured": captured,
            "missing": missing,
        })
        if strength != "complete":
            _note(
                diags, "roles", "available",
                "role %s is bound with a %s identity: %s captured, %s"
                " missing. `classify` compares only the captured"
                " fields, so this binding is weaker than a complete"
                " one" % (logical, strength,
                          ", ".join(captured) or "none",
                          ", ".join(missing)),
            )
    for logical in sorted(agents_value or {}):
        if logical not in roles:
            section["unbound_roles"].append(_trunc(logical))
            _note(
                diags, "roles", "available",
                "role %s is running in this herd and has NO recorded"
                " binding; it classifies as UNBOUND and is NAMED here"
                " rather than left out of the answer to 'which roles"
                " are bound'" % (logical,),
            )
    section["state"] = "available"
    return section


#: The recovery states a ROW may carry. Anything else is OMITTED and
#: its role is merely NAMED. An allowlist, not a denylist: a recovery
#: value added later renders only if it is added here deliberately,
#: so within this gate a new value stays out of a reader-resolvable
#: column.
_RENDERABLE_RECOVERY = (
    "none", "in_flight", "needs_recovery", "blocked_needs_decision",
)


def _turns_section(root, current, agents_value, diags):
    """Recorded TURN OUTCOMES for the current task (R-55 AS-1).

    TASK-SCOPED (AW-2): the turn record is task-mixed, and selecting
    without a scope returns a confident wrong answer. With no current task there is no scope, so the selection is not
    made — an empty result here is "no scope to select within",
    which the diagnostic says rather than leaving the reader to read
    it as "no turns failed".
    """
    from . import turns as turns_module
    section = {
        "state": "missing",
        "task_id": current.task_id,
        "counts": None,
        "undelivered": None,
        "observer_build": None,
        "skewed": [],
        "listed": [],
        # PER-ROLE RECOVERY STATE (R-63 BA-4's fourth property), and
        # the two branches AJ-1 allows. `roles` holds one entry per
        # role that HAS a recorded turn; `omitted_roles` names the
        # ones that do not, so the omission is disclosed instead of
        # being a gap the reader has to notice.
        "roles": [],
        "omitted_roles": [],
    }
    try:
        document = turns_module.load_turns(root / ".herd")
    except turns_module.TurnRecordError as exc:
        section["state"] = "unreadable"
        _note(diags, "turns", "unreadable", str(exc))
        return section
    if not current.known:
        section["state"] = "unavailable"
        _note(
            diags, "turns", "unavailable",
            "no current task id, so turn selection has no scope; this"
            " is an absent SELECTOR, not an absence of turns",
        )
        return section
    scoped = turns_module.turns_for_task(document, current.task_id)
    section["counts"] = turns_module.outcome_counts(scoped)
    section["undelivered"] = sum(
        1 for entry in scoped
        if entry.get("routed_at") and not turns_module.delivered(entry)
    )
    # R-65 (version skew): a record made by a DIFFERENT BUILD of the
    # observer is a claim from different logic. Reported, so the
    # disagreement between the running process and the source on disk
    # is visible instead of silent.
    current_build = turns_module.observer_build()
    section["observer_build"] = current_build
    section["skewed"] = sorted({
        entry.get("observer_build")
        for entry in scoped
        if entry.get("observer_build") != current_build
    })
    for build in section["skewed"]:
        _note(
            diags, "turns", "available",
            "turn record(s) here were written by observer build %s and"
            " the code on disk is build %s; a claim made by a"
            " different build was made by different logic"
            % (build or "unknown", current_build or "unknown"),
        )
    # R-66 decision: `role_state` is WIRED, not deleted.
    #
    # It answers the question a restarting reader actually has —
    # which roles need recovery — and that is BA-4's fourth
    # destination property. Leaving it unreachable would invite a
    # future reader to believe per-role recovery state is surfaced
    # when it is not, and "someone will wire it later" is how R-63
    # was very nearly justified.
    #
    # AJ-1's TWO BRANCHES apply per role. A role with a recorded turn
    # CARRIES its identity and renders. A role with NO recorded turn
    # has no turn state to describe, so it does NOT render a row —
    # there is no slot for a reader to misread as health — and it is
    # named in `omitted_roles` with the reason, so the omission is
    # not itself silent.
    for logical in sorted(agents_value or {}):
        state = turns_module.role_state(document, logical)
        # THE CAVEAT-SLOT INVARIANT, and it is the whole question this
        # increment is aimed at.
        #
        # `[available]` became a caveat slot because it was rendered
        # AS A PROPERTY OF THE THING DESCRIBED, in a field a reader
        # resolves. `no_turn_recorded` is the value with that shape
        # here: put it in a `recovery=` column and a reader resolves
                # it to a state the role is IN. So within this section it
        # stays out of every row.
                # The role is NAMED in `omitted_roles` and no field describes
        # it — within this render a name is not a slot, being empty of
        # anything to resolve.
        #
        # Written as a POSITIVE filter rather than a `continue`: a later edit removing the branch below still leaves this value
        # out of every row, because a row is built only from the states
        # that are allowed to render.
        if state["recovery"] not in _RENDERABLE_RECOVERY:
            section["omitted_roles"].append(_trunc(logical))
            continue
        section["roles"].append({
            "logical": _trunc(logical),
            "current": _trunc(_str_or_none(state.get("current"))),
            "last_outcome": _trunc(
                _str_or_none(state.get("last_outcome"))
            ),
            "last_cause": _trunc(_str_or_none(state.get("last_cause"))),
            "recovery": state["recovery"],
        })
        if state["recovery"] in ("needs_recovery",
                                 "blocked_needs_decision"):
            _note(
                diags, "turns", "available",
                "role %s needs attention: its last turn ended %s (%s)"
                % (logical, state.get("last_outcome"),
                   state.get("last_cause")),
            )
    section["state"] = "available" if scoped else "empty"
    for entry in scoped[-_OBSERVE_MAX_LISTED_AGENTS:]:
        section["listed"].append({
            "turn_id": _trunc(_str_or_none(entry.get("turn_id"))),
            "logical": _trunc(_str_or_none(entry.get("logical"))),
            "outcome": _trunc(_str_or_none(entry.get("outcome"))),
            "cause": _trunc(_str_or_none(entry.get("cause"))),
            "delivered": turns_module.delivered(entry),
        })
    for entry in scoped:
        if entry.get("outcome") in turns_module.FAILURE_OUTCOMES:
            _note(
                diags, "turns", "available",
                "turn %s (%s) ended %s: %s" % (
                    entry.get("turn_id"), entry.get("logical"),
                    entry.get("outcome"), entry.get("cause"),
                ),
            )
        elif entry.get("routed_at") and not turns_module.delivered(entry):
            _note(
                diags, "turns", "available",
                "turn %s was ROUTED and its EFFECT was never observed;"
                " routed is not delivered (R-55 AS-4)"
                % (entry.get("turn_id"),),
            )
    return section


def _checkpoint_section(root, current, diags):
    """The task checkpoint, WITH the task it belongs to (AJ-3).

    Specimen 2: line 1 of `task-checkpoint.md` named a COMPLETE prior
    task, and a restart consulting it to learn what it was doing would
    have resumed the wrong mission. The file is not wrong — it is a
    truthful record of a task that is over. What was missing is a surface saying WHICH task, so the reader
    could tell.
    """
    section = {
        "state": "missing",
        "task_id": None,
        "vintage": vintage.VINTAGE_UNKNOWN,
        "label": None,
        "renders": False,
    }
    text, state, detail = _read_state_text(
        root / ".herd" / "state" / "task-checkpoint.md"
    )
    section["state"] = state
    if state != "available":
        if state != "missing":
            _note(diags, "checkpoint", state, detail)
        return section
    section["task_id"] = vintage.task_id_of_text(text)
    section["vintage"] = vintage.classify(
        section["task_id"], current.task_id
    )
    section["renders"] = vintage.renders(section["vintage"])
    if section["renders"]:
        section["label"] = vintage.vintage_label(
            section["vintage"], section["task_id"],
        )
    if section["vintage"] == vintage.VINTAGE_PRIOR:
        _note(
            diags, "checkpoint", "available",
            "task-checkpoint.md describes prior task %s, not the"
            " current task %s; it is rendered as SUPERSEDED and must"
            " not be used to resume" % (
                section["task_id"], current.task_id,
            ),
        )
    elif section["vintage"] == vintage.VINTAGE_UNKNOWN:
        _note(
            diags, "checkpoint", "available",
            "task-checkpoint.md names no task on line 1, so its"
            " vintage cannot be established and it is OMITTED"
            " (R-46 AJ-1)",
        )
    return section


def _vintage_section(current, diags):
    """WHICH TASK IS RUNNING NOW, and what disagrees with it (AJ-2)."""
    section = current.as_dict()
    section["state"] = "available" if current.known else "unavailable"
    if not current.known:
        _note(
            diags, "vintage", "unavailable",
            "task.json does not say which task is running, so every"
            " field's vintage is UNKNOWN and vintage-bearing fields"
            " are omitted",
        )
    for artifact, task_id in current.disagreements:
        _note(
            diags, "vintage", "available",
            "%s names task %s; task.json is authoritative and names"
            " %s. The disagreement is REPORTED, not reconciled"
            % (artifact, task_id, current.task_id),
        )
    return section


def _artifacts_section(root, now, diags):
    section = {"state": "missing", "listed": []}
    state_dir = root / ".herd" / "state"
    try:
        have_dir = state_dir.is_dir()
    except OSError:
        have_dir = False
    for name in _OBSERVE_ARTIFACT_NAMES:
        entry, error_name = _artifact_entry(name, state_dir / name, now)
        section["listed"].append(entry)
        if error_name:
            _note(
                diags, "artifacts", "unreadable",
                f"{name}: could not be inspected ({error_name})",
            )
    if have_dir:
        briefs, hit_cap = _list_dir(state_dir)
        if hit_cap:
            # Exhausted scan budget: the newest-first brief listing is
            # best-effort over an arbitrary scanned subset — disclosed,
            # and demotes completeness.
            _note(
                diags, "artifacts", "unavailable",
                f"state directory scan capped at {_OBSERVE_MAX_DIR_ENTRIES} "
                "entries; brief listing is best-effort newest-first over "
                "scanned entries only",
            )
        briefs = [
            e for e in briefs
            if e.name.endswith(".md") and "brief-" in e.name
        ]
        briefs.sort(
            key=lambda e: ((_stat_or_none(e).st_mtime if _stat_or_none(e) else 0), e.name),
            reverse=True,
        )
        room = max(0, _OBSERVE_MAX_ARTIFACTS - len(section["listed"]))
        if len(briefs) > room:
            _note(
                diags, "artifacts", "available",
                f"brief listing truncated to {room} of {len(briefs)} scanned",
            )
        for brief in briefs[:room]:
            entry, error_name = _artifact_entry(brief.name, brief, now)
            section["listed"].append(entry)
            if error_name:
                _note(
                    diags, "artifacts", "unreadable",
                    f"{brief.name}: could not be inspected ({error_name})",
                )
    if not have_dir:
        section["state"] = "missing"
    elif any(e.get("present") for e in section["listed"]):
        section["state"] = "available"
    else:
        section["state"] = "empty"
    return section


def _recent_tasks_section(root, diags):
    section = {
        "state": "missing",
        "total": None,
        "truncated": False,
        "listed": [],
    }
    directory = root / ".herd" / "state" / "tasks"
    try:
        if not directory.is_dir():
            return section
    except OSError:
        section["state"] = "unreadable"
        _note(diags, "recent_tasks", "unreadable", "tasks directory could not be inspected")
        return section
    entries, hit_cap = _list_dir(directory, suffix=".json")
    section["total"] = len(entries)
    if hit_cap:
        # Exhausted scan budget: `total` is a lower bound over an arbitrary
        # scanned subset — disclosed, and demotes completeness.
        _note(
            diags, "recent_tasks", "unavailable",
            f"directory scan capped at {_OBSERVE_MAX_DIR_ENTRIES} entries; "
            "`total` reflects only scanned entries",
        )
    entries.sort(
        key=lambda e: ((_stat_or_none(e).st_mtime if _stat_or_none(e) else 0), e.name),
        reverse=True,
    )
    problems = 0
    for entry in entries[:_OBSERVE_MAX_RECENT_TASKS]:
        data, state, _detail = _read_json_object(entry)
        if state != "available":
            problems += 1
            data = {}
        started = _int_or_none(data.get("started_at"))
        completed = _int_or_none(data.get("completed_at"))
        duration = _int_or_none(data.get("duration_seconds"))
        if duration is None and started is not None and completed is not None:
            duration = completed - started
        section["listed"].append({
            "id": _str_or_none(data.get("id")) or _trunc(entry.name[:-5]),
            "status": _str_or_none(data.get("status")),
            "description": _str_or_none(data.get("description")),
            "started_at": started,
            "completed_at": completed,
            "duration_human": _human_duration(duration) if duration is not None else None,
        })
    section["truncated"] = len(entries) > _OBSERVE_MAX_RECENT_TASKS
    if section["truncated"]:
        _note(
            diags, "recent_tasks", "available",
            f"listing truncated to {_OBSERVE_MAX_RECENT_TASKS} of {len(entries)} archived task(s)",
        )
    if problems:
        _note(
            diags, "recent_tasks", "malformed",
            f"{problems} archived task file(s) could not be parsed",
        )
    section["state"] = "available" if entries else "empty"
    return section


def _legacy_section(root):
    """The stale legacy journal appears ONLY here and never feeds any
    activity, timeline, or "current" field."""
    path = root / ".herd" / "state" / "events.jsonl"
    st = _stat_or_none(path)
    present = bool(st)
    return {
        "state": "available" if present else "empty",
        "events_jsonl": {
            "present": present,
            "size": int(st.st_size) if st else None,
            "mtime": int(st.st_mtime) if st else None,
            "note": (
                "stale legacy journal from the removed Mission Control stack; "
                "not a current or authoritative activity stream"
            ),
        },
    }


def _fallback_observation(now, diags):
    """Structurally complete observation used if projection itself fails."""
    def section():
        return {"state": "unavailable"}

    return {
        "schema_version": OBSERVE_SCHEMA_VERSION,
        "generated_at": now,
        "completeness": "PARTIAL",
        "repository": section(),
        "config": section(),
        # STRUCTURAL, conditional: the fallback carries every
        # section the full projection does, so within it a failed
        # projection leaves each section in place. `renders` is False
        # because an unavailable section has no vintage, and AJ-1's
        # second branch is exactly what an unknown vintage gets.
        "vintage": {"state": "unavailable", "task_id": None,
                    "status": None, "source": vintage.SOURCE_NONE,
                    "disagreements": []},
        "checkpoint": {"state": "unavailable", "task_id": None,
                       "vintage": vintage.VINTAGE_UNKNOWN,
                       "label": None, "renders": False},
        "roles": {"state": "unavailable", "stable_fields": [],
                  "listed": [], "unbound_roles": []},
        "turns": {"state": "unavailable", "task_id": None,
                  "counts": None, "undelivered": None,
                  "observer_build": None, "skewed": [], "listed": [],
                  "roles": [], "omitted_roles": []},
        "mission": section(),
        "task": section(),
        "runtime": section(),
        "agents": section(),
        "children": section(),
        "reviews": section(),
        "artifacts": section(),
        "recent_tasks": section(),
        "legacy": section(),
        "diagnostics": diags,
    }


def observe(repo, now=None, probe_agents=True):
    """Build the canonical, bounded, read-only observation for one repository.

    Returns a dict on EVERY path — including a non-git directory, a
    directory with no `.herd/`, and a `.herd/state` full of garbage.
    """
    if now is None:
        now = time.time()
    try:
        now = int(now)
    except Exception:
        now = int(time.time())
    diags = []
    try:
        root = Path(repo).expanduser()
    except Exception:
        _note(diags, "repository", "unavailable", "repository reference is not a path")
        return _fallback_observation(now, diags)
    try:
        repository = _repository_section(root, diags)
        config, config_data = _config_section(root, diags)
        mission = _mission_section(root, diags)
        task, task_id = _task_section(root, now, diags)
        runtime, runtime_state, agents_value = _runtime_section(root, diags)
        agents = _agents_section(
            config_data, runtime_state, agents_value, probe_agents, diags,
        )
        current = vintage.current_task(root)
        vintage_section = _vintage_section(current, diags)
        checkpoint = _checkpoint_section(root, current, diags)
        # AJ-1 applied to the mission: it renders only when its own
        # task identity can be established.
        mission["vintage"] = vintage.classify(
            mission.get("task_id"), current.task_id
        )
        mission["renders"] = vintage.renders(mission["vintage"])
        mission["label"] = (
            vintage.vintage_label(mission["vintage"],
                                  mission.get("task_id"))
            if mission["renders"] else None
        )
        if not mission["renders"] and mission.get("state") == "available":
            # DISCLOSED, not DEGRADED. The state here is `available`
            # deliberately: the projection succeeded and the field was
            # deliberately omitted, which is a fact about the ARTIFACT
            # rather than a failure of the observation. Marking it
            # `unavailable` would demote every herd to PARTIAL for as
            # long as mission.json lacks a task id, and a completeness
            # marker stuck at PARTIAL says little.
            _note(
                diags, "mission", "available",
                "mission.json carries no task identity, so its vintage"
                " cannot be established and the objective is OMITTED"
                " rather than rendered under a health marker"
                " (R-46 AJ-1)",
            )
        children = _children_section(root, task_id, diags)
        reviews = _reviews_section(root, task_id, diags)
        artifacts = _artifacts_section(root, now, diags)
        roles = _role_binding_section(root, agents_value, diags)
        turn_section = _turns_section(
            root, current, agents_value, diags
        )
        recent_tasks = _recent_tasks_section(root, diags)
        legacy = _legacy_section(root)
    except Exception as exc:  # belt-and-braces: observe() must never raise
        _note(
            diags, "observation", "unavailable",
            f"projection failed: {exc.__class__.__name__}",
        )
        return _fallback_observation(now, diags)
    completeness = (
        "PARTIAL"
        if any(d.get("state") in _PARTIAL_STATES for d in diags)
        else "COMPLETE"
    )
    # The section list is built STRUCTURALLY, never conditionally: a
    # destroyed input can never make a section vanish.
    return {
        "schema_version": OBSERVE_SCHEMA_VERSION,
        "generated_at": now,
        "completeness": completeness,
        "repository": repository,
        "config": config,
        "vintage": vintage_section,
        "checkpoint": checkpoint,
        "roles": roles,
        "turns": turn_section,
        "mission": mission,
        "task": task,
        "runtime": runtime,
        "agents": agents,
        "children": children,
        "reviews": reviews,
        "artifacts": artifacts,
        "recent_tasks": recent_tasks,
        "legacy": legacy,
        "diagnostics": diags,
    }


# ---- rendering --------------------------------------------------------


def _fmt(value, absent="?"):
    if value is None:
        return absent
    return str(value)


def render_observation(obs):
    """Concise human text for one observation. Never raises."""
    if not isinstance(obs, dict):
        return "Herd observation: unavailable\n"
    lines = []

    def get(section, key, default=None):
        value = obs.get(section)
        if isinstance(value, dict):
            return value.get(key, default)
        return default

    generated = obs.get("generated_at")
    try:
        stamp = time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime(int(generated)))
    except Exception:
        stamp = "?"
    lines.append(
        f"Herd observation — schema v{_fmt(obs.get('schema_version'))} "
        f"— {_fmt(obs.get('completeness'))}"
    )
    lines.append(f"Generated: {stamp} ({_fmt(generated)})")

    lines.append(f"Repository [{_fmt(get('repository', 'state'))}]: {_fmt(get('repository', 'path'))}")
    if get("repository", "is_git_repo"):
        dirty = get("repository", "dirty")
        if dirty and get("repository", "dirty_file_count_capped"):
            dirty_txt = (
                f"dirty (>={_fmt(get('repository', 'dirty_file_count'))} "
                "file(s), count capped)"
            )
        elif dirty:
            dirty_txt = f"dirty ({_fmt(get('repository', 'dirty_file_count'))} file(s))"
        else:
            dirty_txt = "clean" if dirty is not None else "dirty: ?"
        lines.append(
            f"  git: branch {_fmt(get('repository', 'branch'), '(detached)')} "
            f"@ {_fmt(get('repository', 'head_short'))} | {dirty_txt} "
            f"| remote: {_fmt(get('repository', 'remote'), '(none)')}"
        )
    else:
        lines.append("  git: not a git repository")

    orchestration = get("config", "orchestration") or {}
    roles = get("config", "roles") or []
    # R-74 F3: the LABEL TRAVELS WITH THE VALUE.
    #
    # This rendered `executor(claude/fable)` under a `Config` heading,
    # and a reader scanning it read "executor is running claude on
    # fable" — a claim about the running herd. The heading did not
    # protect it, because a reader reads the tuple, not the section
    # they are three lines below. The qualification is now inside the
    # field, where it is read at the same moment as the value.
    #
    # `(unset)` rather than `?` when no `--model` is configured: the
    # value is not UNKNOWN, it is ABSENT, and `?` invites a reader to
    # resolve it into "some default the tool picked".
    role_bits = ", ".join(
        f"{r.get('role')}(kind={_fmt(r.get('kind'))},"
        f" model-CONFIGURED={_fmt(r.get('configured_model'), '(unset)')})"
        for r in roles if isinstance(r, dict)
    )
    lines.append(
        f"Config [{_fmt(get('config', 'state'))}]: {_fmt(get('config', 'project_name'))} "
        f"| preset={_fmt(get('config', 'preset'), '(none)')} "
        f"| leads={_fmt(orchestration.get('leads') if isinstance(orchestration, dict) else None)} "
        f"pods={_fmt(orchestration.get('pods') if isinstance(orchestration, dict) else None)}"
    )
    if role_bits:
        lines.append(f"  roles: {role_bits}")
        # THE LIMIT, STATED — because silence is what invites the
        # misreading. A reader who is told the value is CONFIGURED
        # still has to be told that the RUNNING value is unavailable,
        # or they will take the configured one as the best answer
        # going. R-74 F1: the running model is not observable at the
        # agent interface, and inventing a plausible value there
        # would be the fake health this mission exists to remove.
        lines.append(
            "  NOTE: these are CONFIGURED models — what this herd was"
            " asked to start. The model a running agent is actually"
            " using is NOT observable through the agent interface, so"
            " nothing here reports it."
        )

    # R-46 AJ-1, TWO BRANCHES AND NO THIRD.
    #
    # The mission line used to read `Mission [available]: <objective>`
    # and the objective was two tasks old. `[available]` was a CAVEAT
    # SLOT — written to mean "readable", read as "current" — and that
    # is the third branch the ruling forbids. A field whose task
    # identity is unestablished is OMITTED, and the reason is
    # stated where the field would have been so the omission is not
    # itself silent.
    if get("mission", "renders"):
        lines.append(
            f"Mission [{_fmt(get('mission', 'label'))}]: "
            f"v{_fmt(get('mission', 'version'))} — "
            f"{_fmt(get('mission', 'objective'), '(none)')}"
        )
    else:
        lines.append(
            "Mission: OMITTED — mission.json carries no task identity,"
            " so nothing here can say WHICH task's objective it is"
        )

    if get("checkpoint", "renders"):
        lines.append(f"Checkpoint [{_fmt(get('checkpoint', 'label'))}]")
    elif get("checkpoint", "state") == "available":
        lines.append(
            "Checkpoint: OMITTED — task-checkpoint.md names no task on"
            " line 1, so its vintage cannot be established"
        )

    lines.append(
        f"Task [{_fmt(get('task', 'state'))}]: {_fmt(get('task', 'status'), '(none)')} "
        f"{_fmt(get('task', 'id'), '')} "
        f"| elapsed {_fmt(get('task', 'elapsed_human'))} "
        f"| heartbeats={_fmt(get('task', 'heartbeat_count'))} "
        f"prompts={_fmt(get('task', 'manual_prompt_count'))}"
    )

    current_id = get("vintage", "task_id")
    disagreements = get("vintage", "disagreements") or []
    lines.append(
        f"Current task [{_fmt(get('vintage', 'state'))}]: "
        f"{_fmt(current_id, '(unknown)')} "
        f"(authority: {_fmt(get('vintage', 'source'))})"
    )
    for row in disagreements:
        if isinstance(row, dict):
            lines.append(
                f"  DISAGREES: {_fmt(row.get('artifact'))} names "
                f"{_fmt(row.get('task_id'))} — reported, not reconciled"
            )

    roles_listed = get("roles", "listed") or []
    lines.append(
        f"Role bindings [{_fmt(get('roles', 'state'))}]: "
        f"{len(roles_listed)} bound"
    )
    unbound = get("roles", "unbound_roles") or []
    if unbound:
        lines.append(
            "  UNBOUND in this herd: %s — named, not described;"
            " nothing here reports an identity for them"
            % ", ".join(str(r) for r in unbound)
        )
    for entry in roles_listed:
        if not isinstance(entry, dict):
            continue
        missing = entry.get("missing") or []
        detail = (
            "" if not missing
            else " | missing: " + ", ".join(str(m) for m in missing)
        )
        lines.append(
            f"  {_fmt(entry.get('logical')):<12} "
            f"{_fmt(entry.get('agent'), '(none)'):<34} "
            f"identity={_fmt(entry.get('strength'))}{detail}"
        )

    turn_counts = get("turns", "counts") or {}
    lines.append(
        f"Turns [{_fmt(get('turns', 'state'))}] for "
        f"{_fmt(get('turns', 'task_id'), '(no scope)')}: "
        + (", ".join(
            f"{name}={count}" for name, count in sorted(turn_counts.items())
        ) if turn_counts else "(none recorded)")
        + (f" | routed-but-unobserved={_fmt(get('turns', 'undelivered'))}"
           if get("turns", "undelivered") else "")
    )
    for entry in (get("turns", "roles") or []):
        if not isinstance(entry, dict):
            continue
        cause = entry.get("last_cause")
        lines.append(
            f"  {_fmt(entry.get('logical')):<12} "
            f"recovery={_fmt(entry.get('recovery')):<22} "
            f"last={_fmt(entry.get('last_outcome'), '-')}"
            + (f" — {cause}" if cause else "")
        )
    omitted = get("turns", "omitted_roles") or []
    if omitted:
        lines.append(
            "  no turn recorded for %s in this task: OMITTED rather"
            " than shown as healthy" % ", ".join(str(r) for r in omitted)
        )
    for build in (get("turns", "skewed") or []):
        lines.append(
            f"  BUILD SKEW: record(s) written by observer build "
            f"{_fmt(build, 'unknown')}; code on disk is "
            f"{_fmt(get('turns', 'observer_build'), 'unknown')}"
        )
    for entry in (get("turns", "listed") or []):
        if not isinstance(entry, dict):
            continue
        lines.append(
            f"  {_fmt(entry.get('turn_id')):<14} "
            f"{_fmt(entry.get('logical')):<12} "
            f"{_fmt(entry.get('outcome')):<17} "
            f"{_fmt(entry.get('cause'), '')}"
        )

    lines.append(
        f"Runtime [{_fmt(get('runtime', 'state'))}]: "
        f"workspace {_fmt(get('runtime', 'workspace_id'), '(none)')} "
        f"| agents={_fmt(get('runtime', 'agent_count'))} "
        f"panes={_fmt(get('runtime', 'pane_count'))}"
    )

    listed_agents = get("agents", "listed") or []
    lines.append(
        f"Agents [{_fmt(get('agents', 'state'))}]: "
        f"probed={_fmt(get('agents', 'probed'), '0')} "
        f"unprobed={_fmt(get('agents', 'unprobed'), '0')}"
    )
    for entry in listed_agents:
        if not isinstance(entry, dict):
            continue
        lines.append(
            f"  {_fmt(entry.get('logical')):<12} {_fmt(entry.get('agent'), '(invalid)'):<34} "
            f"{_fmt(entry.get('status'), '-'):<8} [{_fmt(entry.get('probe'))}]"
        )

    lines.append(
        f"Children [{_fmt(get('children', 'state'))}]: "
        f"{_fmt(get('children', 'count'), '0')} recorded for current task "
        f"(recorded status only, not resolved liveness)"
    )

    listed_reviews = get("reviews", "listed") or []
    latest_decision = None
    for entry in reversed(listed_reviews):
        if isinstance(entry, dict):
            latest_decision = entry.get("decision")
            break
    lines.append(
        f"Reviews [{_fmt(get('reviews', 'state'))}]: "
        f"rounds={_fmt(get('reviews', 'rounds'), '0')} "
        f"| latest decision: {_fmt(latest_decision, '(none)')}"
    )

    listed_artifacts = get("artifacts", "listed") or []
    present = [a for a in listed_artifacts if isinstance(a, dict) and a.get("present")]
    stale = [a for a in present if a.get("freshness") == "stale"]
    lines.append(
        f"Artifacts [{_fmt(get('artifacts', 'state'))}]: "
        f"{len(present)} present, {len(stale)} stale"
    )

    listed_tasks = get("recent_tasks", "listed") or []
    task_rows_note = f" (showing 3 of {len(listed_tasks)} listed)" if len(listed_tasks) > 3 else ""
    lines.append(
        f"Recent tasks [{_fmt(get('recent_tasks', 'state'))}]: "
        f"total={_fmt(get('recent_tasks', 'total'), '0')}{task_rows_note}"
    )
    for entry in listed_tasks[:3]:
        if not isinstance(entry, dict):
            continue
        lines.append(
            f"  {_fmt(entry.get('id'))} {_fmt(entry.get('status'), '-')} "
            f"{_fmt(entry.get('duration_human'), '')}"
        )

    legacy = get("legacy", "events_jsonl") or {}
    if isinstance(legacy, dict) and legacy.get("present"):
        lines.append("Legacy: events.jsonl present — stale legacy journal, not current activity")

    # R-46 AJ-4: an append-only artifact presents its STALEST content
    # FIRST, which is how a status header said two increments were
    # delegated while the tail of the same file recorded five closed.
    # A reader who stops at the top reads the oldest state in the file
    # and has no way to know it.
    lines.append(
        "Append-only artifacts (supervisor-status.md, evidence and"
        " ruling files): newest entry is at the END. Current truth for"
        " WHICH TASK IS RUNNING is task.json, shown above."
    )

    diagnostics = obs.get("diagnostics")
    diagnostics = diagnostics if isinstance(diagnostics, list) else []
    if diagnostics:
        diag_note = (
            f" (showing {_OBSERVE_MAX_LISTED_AGENTS})"
            if len(diagnostics) > _OBSERVE_MAX_LISTED_AGENTS else ""
        )
        lines.append(f"Diagnostics: {len(diagnostics)}{diag_note}")
        for diag in diagnostics[:_OBSERVE_MAX_LISTED_AGENTS]:
            if not isinstance(diag, dict):
                continue
            lines.append(
                f"  - {_fmt(diag.get('source'))} [{_fmt(diag.get('state'))}] "
                f"{_fmt(diag.get('detail'), '')}"
            )
    else:
        lines.append("Diagnostics: none")
    return "\n".join(lines)
