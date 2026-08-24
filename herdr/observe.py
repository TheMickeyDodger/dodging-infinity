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

from .runtime import agent_info


OBSERVE_SCHEMA_VERSION = 1

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
                "model": _model_from_args(spec.get("args")),
            })
        if len(names) > _OBSERVE_MAX_LISTED_AGENTS:
            _note(
                diags, "config", "available",
                f"roles truncated to {_OBSERVE_MAX_LISTED_AGENTS} of {len(names)}",
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
        children = _children_section(root, task_id, diags)
        reviews = _reviews_section(root, task_id, diags)
        artifacts = _artifacts_section(root, now, diags)
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
    role_bits = ", ".join(
        f"{r.get('role')}({_fmt(r.get('kind'))}/{_fmt(r.get('model'))})"
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

    lines.append(
        f"Mission [{_fmt(get('mission', 'state'))}]: "
        f"v{_fmt(get('mission', 'version'))} — {_fmt(get('mission', 'objective'), '(none)')}"
    )

    lines.append(
        f"Task [{_fmt(get('task', 'state'))}]: {_fmt(get('task', 'status'), '(none)')} "
        f"{_fmt(get('task', 'id'), '')} "
        f"| elapsed {_fmt(get('task', 'elapsed_human'))} "
        f"| heartbeats={_fmt(get('task', 'heartbeat_count'))} "
        f"prompts={_fmt(get('task', 'manual_prompt_count'))}"
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
