#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import time
from pathlib import Path

from herdr.config import (
    CFG,
    DEFAULT,
    HERD,
    PRESETS,
    ROLE_FILES,
    apply_preset_to_config,
)
from herdr.control_plane import HerdrControlPlane
from herdr.registry import REGISTRY
from herdr.guards import install_git_guard as install_git_guard_runtime
from herdr.instance import HerdrInstance
from herdr.runtime import agent_info, jrun, prompt, run, split, start_agent
from herdr.policy import DEFAULT_POLICY, HerdrPolicy

STATE = "state/runtime.json"
TASK_STATE = "state/task.json"
MISSION_STATE = "state/mission.json"
TASK_ARCHIVE = "state/tasks"
APPROVAL = "state/commit-approval.json"
PUSH_APPROVAL = "state/push-approval.json"
SUPPORTED = {
    "pi", "claude", "codex", "gemini", "cursor", "devin", "agy", "cline",
    "omp", "mastracode", "opencode", "copilot", "kimi", "kiro", "droid",
    "amp", "grok", "hermes", "kilo", "qodercli", "maki"
}
INTEGRATIONS = {
    "claude", "codex", "opencode", "copilot", "kimi", "droid", "grok",
    "hermes", "kilo", "qodercli", "cursor", "mastracode"
}





def current_repo():
    p = run(["git", "rev-parse", "--show-toplevel"])
    return Path(p.stdout.strip()).resolve() if p.returncode == 0 else Path.cwd().resolve()


def hroot(r):
    return r / HERD


def cfg(r):
    p = hroot(r) / CFG
    if not p.exists():
        raise SystemExit(f"{r} is not initialized. Run `herdctl init` there first.")
    return json.loads(p.read_text())


def effective_policy(r, task_policy=None):
    """Resolve the effective policy for one herd instance.

    Repository policy comes from `.herd/herd.config.json`. A task-specific
    policy may optionally override it for one top-level task.
    """
    c = cfg(r)
    return HerdrPolicy.resolve(
        c.get("policy"),
        task_policy,
    )


def state(r):
    p = hroot(r) / STATE
    if not p.exists():
        raise SystemExit(f"No runtime state for {r}. Run `herdctl bootstrap` first.")
    return json.loads(p.read_text())


def save_state(r, s):
    p = hroot(r) / STATE
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(s, indent=2) + "\n")


def registry_load():
    if not REGISTRY.exists():
        return {"version": 1, "repos": {}}
    try:
        return json.loads(REGISTRY.read_text())
    except Exception:
        return {"version": 1, "repos": {}}


def registry_save(data):
    REGISTRY.parent.mkdir(parents=True, exist_ok=True)
    REGISTRY.write_text(json.dumps(data, indent=2) + "\n")


def deep_merge_defaults(current, defaults):
    """Add missing config keys without overwriting user values."""
    if not isinstance(current, dict) or not isinstance(defaults, dict):
        return current
    for key, value in defaults.items():
        if key not in current:
            current[key] = json.loads(json.dumps(value))
        elif isinstance(current[key], dict) and isinstance(value, dict):
            deep_merge_defaults(current[key], value)
    return current




def preset_name_from_config(data):
    roles = data.get("roles", {})
    for name, spec in PRESETS.items():
        if roles == spec.get("roles", {}):
            return name
    return data.get("preset") or "custom"


def repo_alias(r):
    rr = r.resolve()
    for alias, ent in registry_load().get("repos", {}).items():
        try:
            if Path(ent["path"]).resolve() == rr:
                return alias
        except Exception:
            continue
    return r.name


def local_exclude_path(r):
    raw = gitout(r, "rev-parse", "--git-path", "info/exclude", allow_fail=True)
    if raw:
        p = Path(raw)
        if not p.is_absolute():
            p = (r / p).resolve()
        return p
    return r / ".git" / "info" / "exclude"


def ensure_local_herd_exclude(r):
    """Keep harness files local without modifying a tracked .gitignore."""
    p = local_exclude_path(r)
    p.parent.mkdir(parents=True, exist_ok=True)
    old = p.read_text() if p.exists() else ""
    lines = {line.strip() for line in old.splitlines()}
    if ".herd/" not in lines:
        with p.open("a") as f:
            if old and not old.endswith("\n"):
                f.write("\n")
            f.write("\n# Local Herd harness\n.herd/\n")
    return p


def task_path(r):
    return hroot(r) / TASK_STATE


def load_task(r):
    p = task_path(r)
    if not p.exists():
        return {"status": "IDLE"}
    try:
        return json.loads(p.read_text())
    except Exception:
        return {"status": "ERROR", "error": "unreadable task state"}


def save_task(r, data):
    p = task_path(r)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, indent=2) + "\n")


def mission_path(r):
    return hroot(r) / MISSION_STATE


def load_mission(r):
    p = mission_path(r)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text())
    except Exception:
        return None


def save_mission(r, data):
    p = mission_path(r)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, indent=2) + "\n")


def archive_task(r, data):
    task_id = str(data.get("id") or int(time.time()))
    d = hroot(r) / TASK_ARCHIVE
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{task_id}.json").write_text(json.dumps(data, indent=2) + "\n")


def human_duration(seconds):
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



def context_hint(agent):
    """Best-effort Claude UI hint, not token accounting."""
    p = run(["herdr", "agent", "read", agent, "--source", "visible"])
    if p.returncode:
        return None
    text = p.stdout
    matches = re.findall(r"(?:save|saving)\s+([0-9]+(?:\.[0-9]+)?\s*[kKmM]?)\s+tokens", text, re.I)
    if matches:
        return matches[-1].replace(" ", "")
    # Some Claude builds show a down-arrow usage indicator instead.
    matches = re.findall(r"[↓↘]\s*([0-9]+(?:\.[0-9]+)?\s*[kKmM]?)\s+tokens", text, re.I)
    return matches[-1].replace(" ", "") if matches else None


def register_repo(r, alias=None):
    data = registry_load()
    repos = data.setdefault("repos", {})
    base = (alias or r.name).strip().lower()
    base = re.sub(r"[^a-z0-9._-]+", "-", base).strip("-") or "repo"
    if base in repos and Path(repos[base]["path"]).resolve() != r.resolve():
        if alias:
            raise SystemExit(f"Alias `{base}` already points to {repos[base]['path']}")
        n = 2
        while f"{base}-{n}" in repos:
            n += 1
        base = f"{base}-{n}"
    repos[base] = {"path": str(r.resolve()), "registered_at": int(time.time())}
    registry_save(data)
    return base


def resolve_repo_ref(ref=None):
    if not ref:
        return current_repo()
    maybe = Path(ref).expanduser()
    if maybe.exists():
        p = run(["git", "-C", str(maybe), "rev-parse", "--show-toplevel"])
        if p.returncode:
            raise SystemExit(f"Not a git repository: {maybe}")
        return Path(p.stdout.strip()).resolve()
    ent = registry_load().get("repos", {}).get(ref)
    if not ent:
        raise SystemExit(f"Unknown repo `{ref}`. Run `herdctl repos`.")
    return Path(ent["path"]).resolve()


def gitout(r, *args, allow_fail=False):
    p = run(["git", "-C", str(r), *args])
    if p.returncode and not allow_fail:
        raise SystemExit(p.stderr.strip() or p.stdout.strip())
    return p.stdout.strip()


def prefix(r):
    slug = re.sub(r"[^a-z0-9]+", "-", r.name.lower()).strip("-")[:10]
    dig = hashlib.sha1(str(r).encode()).hexdigest()[:5]
    return f"h{dig}-{slug}"[:18].rstrip("-")


def aname(pre, short):
    return re.sub(r"[^a-z0-9_-]", "-", f"{pre}-{short}".lower())[:32].rstrip("-_")


# ---------- commit confirmation ----------

def repo_identity(r):
    root = Path(gitout(r, "rev-parse", "--show-toplevel")).resolve()
    branch = gitout(root, "branch", "--show-current", allow_fail=True) or "(detached HEAD)"
    head = gitout(root, "rev-parse", "HEAD", allow_fail=True) or "(unborn)"
    remote = gitout(root, "remote", "get-url", "origin", allow_fail=True) or "(no origin)"
    gitdir = gitout(root, "rev-parse", "--git-dir")
    if not Path(gitdir).is_absolute():
        gitdir = str((root / gitdir).resolve())
    staged = run(["git", "-C", str(root), "diff", "--cached", "--binary"]).stdout.encode()
    return {
        "repo_root": str(root),
        "git_dir": gitdir,
        "branch": branch,
        "head": head,
        "remote": remote,
        "staged_sha256": hashlib.sha256(staged).hexdigest(),
    }


def approval_path(r):
    return hroot(r) / APPROVAL


def approval_valid(r, consume=False):
    p = approval_path(r)
    if not p.exists():
        return False, "No approval exists. Run `herdctl approve-commit`."
    try:
        tok = json.loads(p.read_text())
    except Exception:
        return False, "Approval token is unreadable. Re-authorize."
    if int(tok.get("expires_at", 0)) < int(time.time()):
        p.unlink(missing_ok=True)
        return False, "Approval expired. Re-authorize."
    now = repo_identity(r)
    for key in ["repo_root", "git_dir", "branch", "head", "staged_sha256"]:
        if tok.get(key) != now.get(key):
            p.unlink(missing_ok=True)
            return False, f"Approval invalidated because `{key}` changed. Re-authorize."
    if consume:
        p.unlink(missing_ok=True)
    return True, "approved"


def push_approval_path(r):
    return hroot(r) / PUSH_APPROVAL


def push_identity(r, remote_name="origin", target_branch=None, target_tag=None):
    root = Path(gitout(r, "rev-parse", "--show-toplevel")).resolve()
    branch = gitout(root, "branch", "--show-current", allow_fail=True) or "(detached HEAD)"
    head = gitout(root, "rev-parse", "HEAD", allow_fail=True) or "(unborn)"
    if branch == "(detached HEAD)":
        raise SystemExit("Push approval requires a named local branch.")
    remote_url = gitout(root, "remote", "get-url", remote_name, allow_fail=True)
    if not remote_url:
        raise SystemExit(f"Remote `{remote_name}` not found.")
    if target_tag:
        source_ref = f"refs/tags/{target_tag}"
        source_oid = gitout(root, "rev-parse", source_ref, allow_fail=True)
        if not source_oid:
            raise SystemExit(f"Tag `{target_tag}` not found.")
        target_ref = source_ref
    else:
        target_branch = target_branch or branch
        source_ref = f"refs/heads/{branch}"
        source_oid = head
        target_ref = f"refs/heads/{target_branch}"
    return {
        "repo_root": str(root),
        "branch": branch,
        "head": head,
        "remote_name": remote_name,
        "remote_url": remote_url,
        "source_ref": source_ref,
        "source_oid": source_oid,
        "target_ref": target_ref,
    }


def push_approval_valid(r, remote_name=None, remote_url=None, updates=None, consume=False):
    p = push_approval_path(r)
    if not p.exists():
        return False, "No push approval exists. Run `herdctl approve-push`."
    try:
        tok = json.loads(p.read_text())
    except Exception:
        return False, "Push approval token is unreadable. Re-authorize."
    if int(tok.get("expires_at", 0)) < int(time.time()):
        p.unlink(missing_ok=True)
        return False, "Push approval expired. Re-authorize."
    target_ref = str(tok.get("target_ref") or "")
    try:
        if target_ref.startswith("refs/tags/"):
            now = push_identity(
                r,
                tok.get("remote_name", "origin"),
                target_tag=target_ref.removeprefix("refs/tags/"),
            )
        else:
            now = push_identity(
                r,
                tok.get("remote_name", "origin"),
                target_ref.removeprefix("refs/heads/") or None,
            )
    except SystemExit as e:
        p.unlink(missing_ok=True)
        return False, str(e)
    for key in ["repo_root", "branch", "head", "remote_name", "remote_url", "target_ref"]:
        if tok.get(key) != now.get(key):
            p.unlink(missing_ok=True)
            return False, f"Push approval invalidated because `{key}` changed. Re-authorize."
    if remote_name is not None and tok.get("remote_name") != remote_name:
        return False, "Push approval is for a different remote name."
    if remote_url is not None and tok.get("remote_url") != remote_url:
        return False, "Push approval is for a different remote URL."
    if updates is not None:
        if len(updates) != 1:
            return False, "Push approval permits exactly one ref update."
        local_ref, local_oid, remote_ref, _remote_oid = updates[0]
        expected_local = tok.get("source_ref") or f"refs/heads/{tok.get('branch')}"
        expected_oid = tok.get("source_oid") or tok.get("head")
        if local_ref != expected_local:
            return False, f"Push local ref `{local_ref}` does not match approved `{expected_local}`."
        if local_oid != expected_oid:
            return False, "Push source changed after approval."
        if remote_ref != tok.get("target_ref"):
            return False, f"Push target `{remote_ref}` does not match approved `{tok.get('target_ref')}`."
    if consume:
        p.unlink(missing_ok=True)
    return True, "approved"


def simple_git_commit(command):
    try:
        toks = shlex.split(command, posix=True)
    except Exception:
        return False, "Could not safely parse command. Commit must be standalone `git commit ...`."
    if any(t in {"&&", "||", ";", "|", "&"} for t in toks):
        return False, "Commit must be standalone, not chained with other shell operations."
    if not toks or Path(toks[0]).name != "git":
        return False, ""
    if "--no-verify" in toks or "-n" in toks:
        return False, "`--no-verify` is forbidden by the herd commit guard."
    if "-C" in toks:
        return False, "`git -C ... commit` is blocked. Commit from the confirmed worktree."
    if len(toks) < 2 or toks[1] != "commit":
        return False, ""
    return True, ""


def simple_git_push(command):
    try:
        toks = shlex.split(command, posix=True)
    except Exception:
        return False, "Could not safely parse command. Push must be standalone `git push ...`."
    if any(t in {"&&", "||", ";", "|", "&"} for t in toks):
        return False, "Push must be standalone, not chained with other shell operations."
    if not toks or Path(toks[0]).name != "git":
        return False, ""
    if "-C" in toks:
        return False, "`git -C ... push` is blocked. Push from the confirmed worktree."
    if len(toks) < 2 or toks[1] != "push":
        return False, ""
    if "--dry-run" in toks or "-n" in toks:
        return True, "dry-run"
    if "--no-verify" in toks:
        return False, "`git push --no-verify` is forbidden by the herd push guard."
    if any(t in toks for t in ["--force", "-f", "--force-with-lease", "--mirror", "--delete"]):
        return False, "Destructive/force push flags are blocked by the herd push guard."
    return True, ""


def guard_pretool():
    try:
        data = json.load(sys.stdin)
    except Exception:
        return 0
    if data.get("tool_name") != "Bash":
        return 0
    command = (data.get("tool_input") or {}).get("command", "")
    if "git" not in command:
        return 0

    cwd = Path(data.get("cwd") or os.getcwd()).resolve()
    p = run(["git", "-C", str(cwd), "rev-parse", "--show-toplevel"])
    r = Path(p.stdout.strip()).resolve() if p.returncode == 0 else None

    if re.search(r"(?:^|[\s;&|])(?:/[^\s]+/)?git\s+[^\n]*\bcommit\b", command):
        ok, reason = simple_git_commit(command)
        if not ok:
            print(reason or "Commit blocked: use a standalone `git commit ...` after approval.", file=sys.stderr)
            return 2
        if not r:
            print("Commit blocked: unable to identify repository.", file=sys.stderr)
            return 2
        if not (hroot(r) / CFG).exists():
            print(f"Commit blocked: {r} is not initialized for herd commit confirmation.", file=sys.stderr)
            return 2
        valid, msg = approval_valid(r, consume=False)
        if not valid:
            print(f"Commit blocked for {r.name}: {msg}", file=sys.stderr)
            return 2

    if re.search(r"(?:^|[\s;&|])(?:/[^\s]+/)?git\s+[^\n]*\bpush\b", command):
        ok, reason = simple_git_push(command)
        if not ok:
            print(reason or "Push blocked: use a standalone `git push ...` after approval.", file=sys.stderr)
            return 2
        if reason == "dry-run":
            return 0
        if not r:
            print("Push blocked: unable to identify repository.", file=sys.stderr)
            return 2
        if not (hroot(r) / CFG).exists():
            print(f"Push blocked: {r} is not initialized for herd push confirmation.", file=sys.stderr)
            return 2
        valid, msg = push_approval_valid(r, consume=False)
        if not valid:
            print(f"Push blocked for {r.name}: {msg}", file=sys.stderr)
            return 2
    return 0


def guard_precommit(r):
    valid, msg = approval_valid(r, consume=False)
    if not valid:
        print(f"HERD COMMIT BLOCKED: {msg}", file=sys.stderr)
        return 1
    print(f"HERD COMMIT PRE-CHECK AUTHORIZED: {r}", file=sys.stderr)
    return 0


def guard_reference_transaction(r, phase):
    updates = []
    for line in sys.stdin.read().splitlines():
        parts = line.split()
        if len(parts) >= 3:
            updates.append((parts[0], parts[1], parts[2]))
    if phase == "committed":
        _consume_push_approval_on_transfer(r, updates)
    head_ref = gitout(r, "symbolic-ref", "-q", "HEAD", allow_fail=True)
    touches_head = bool(head_ref and any(ref == head_ref for _, _, ref in updates))
    if not touches_head:
        return 0
    if phase == "prepared":
        valid, msg = approval_valid(r, consume=False)
        if not valid:
            print(f"HERD HISTORY UPDATE BLOCKED: {msg}", file=sys.stderr)
            return 1
        return 0
    if phase == "committed":
        # Consume only after Git reports that the ref transaction committed.
        approval_path(r).unlink(missing_ok=True)
        return 0
    return 0


def guard_prepush(r, remote_name, remote_url):
    updates = []
    for line in sys.stdin.read().splitlines():
        parts = line.split()
        if len(parts) >= 4:
            updates.append((parts[0], parts[1], parts[2], parts[3]))
    valid, msg = push_approval_valid(r, remote_name=remote_name, remote_url=remote_url, updates=updates, consume=False)
    if not valid:
        print(f"HERD PUSH BLOCKED: {msg}", file=sys.stderr)
        return 1
    # Do not consume here: git also runs pre-push for `git push --dry-run`
    # and gives this hook no way to tell a rehearsal from a real transfer.
    # _consume_push_approval_on_transfer (reference-transaction, committed
    # phase) consumes the token once the approved commit is observed on the
    # approved remote-tracking ref.
    print(f"HERD PUSH AUTHORIZED: {r} -> {remote_name}", file=sys.stderr)
    return 0


def _consume_push_approval_on_transfer(r, updates):
    """Consume the push approval once the approved commit is observed on the
    approved remote-tracking ref, which is evidence that a transfer completed.

    `git push --dry-run` never updates the tracking ref, so it cannot consume.
    `git fetch` moves the tracking ref to the approved head only when that
    commit is already on the remote, so consuming is correct there as well.
    Unreadable or malformed tokens are consumed: fail closed.
    """
    p = push_approval_path(r)
    if not p.exists():
        return
    try:
        tok = json.loads(p.read_text())
    except Exception:
        p.unlink(missing_ok=True)
        return
    remote_name = tok.get("remote_name")
    branch = str(tok.get("target_ref", "")).removeprefix("refs/heads/")
    head = tok.get("head")
    if not remote_name or not branch or not head:
        p.unlink(missing_ok=True)
        return
    tracking_ref = f"refs/remotes/{remote_name}/{branch}"
    for _old_oid, new_oid, ref in updates:
        if ref == tracking_ref and new_oid == head:
            p.unlink(missing_ok=True)
            return


def _install_pre_push_hook(r):
    hook_raw = gitout(r, "rev-parse", "--git-path", "hooks/pre-push")
    hook = Path(hook_raw)
    if not hook.is_absolute():
        hook = (r / hook).resolve()
    hook.parent.mkdir(parents=True, exist_ok=True)
    marker = "# HERD PUSH GUARD v0.2.3"
    if hook.exists() and marker in hook.read_text(errors="ignore"):
        return
    backup = hook.with_name("pre-push.pre-herd")
    had_backup = False
    if hook.exists():
        if backup.exists():
            raise SystemExit(f"Cannot safely install push guard: both {hook} and {backup} exist.")
        hook.rename(backup)
        had_backup = True
    backup_call = f'"{backup}" "$@" < "$TMP"' if had_backup else ": # no previous hook"
    script = (
        "#!/usr/bin/env bash\n"
        + marker + "\nset -e\n"
        + 'ROOT="$(git rev-parse --show-toplevel)"\n'
        + 'TMP="$(mktemp)"\n'
        + 'trap \'rm -f "$TMP"\' EXIT\n'
        + 'cat > "$TMP"\n'
        + 'herdctl _guard-prepush --repo-path "$ROOT" --remote-name "$1" --remote-url "$2" < "$TMP"\n'
        + backup_call + "\n"
    )
    hook.write_text(script)
    hook.chmod(0o755)


def _install_one_git_hook(r, hook_name, marker, guard_line):
    hook_raw = gitout(r, "rev-parse", "--git-path", f"hooks/{hook_name}")
    hook = Path(hook_raw)
    if not hook.is_absolute():
        hook = (r / hook).resolve()
    hook.parent.mkdir(parents=True, exist_ok=True)
    if hook.exists() and marker in hook.read_text(errors="ignore"):
        return
    backup = hook.with_name(f"{hook_name}.pre-herd")
    had_backup = False
    if hook.exists():
        if backup.exists():
            raise SystemExit(f"Cannot safely install commit guard: both {hook} and {backup} exist.")
        hook.rename(backup)
        had_backup = True
    backup_call = f'"{backup}" "$@"' if had_backup else ": # no previous hook"
    hook.write_text(
        "#!/usr/bin/env bash\n" + marker + "\nset -e\n"
        + 'ROOT="$(git rev-parse --show-toplevel)"\n'
        + guard_line + "\n"
        + backup_call + "\n"
    )
    hook.chmod(0o755)


def install_git_guard(r):
    """Compatibility wrapper for package-owned Git guards."""
    try:
        install_git_guard_runtime(r)
    except RuntimeError as exc:
        raise SystemExit(str(exc))



def install_claude_guard():
    settings = Path.home() / ".claude" / "settings.json"
    settings.parent.mkdir(parents=True, exist_ok=True)
    if settings.exists():
        try:
            data = json.loads(settings.read_text())
        except Exception:
            raise SystemExit(f"Cannot edit invalid JSON: {settings}")
    else:
        data = {}

    changed = False
    groups = data.setdefault("hooks", {}).setdefault("PreToolUse", [])
    found = False
    for group in groups:
        for hook in group.get("hooks", []):
            if "_guard-pretool" in json.dumps(hook):
                found = True
                break
    if not found:
        groups.append({
            "matcher": "Bash",
            "hooks": [{"type": "command", "command": "herdctl", "args": ["_guard-pretool"]}],
        })
        changed = True

    # Explicitly configure conservative auto-mode defaults. This keeps Claude's
    # built-in trust boundary (working repo + configured remotes) while avoiding
    # each fresh agent session trying to invent a broader environment profile.
    auto = data.setdefault("autoMode", {})
    env = auto.get("environment")
    if env is None:
        auto["environment"] = ["$defaults"]
        changed = True
    elif isinstance(env, list) and "$defaults" not in env:
        env.insert(0, "$defaults")
        changed = True

    if changed:
        settings.write_text(json.dumps(data, indent=2) + "\n")
    return settings, changed


# ---------- setup / registry ----------

def init_cmd(args):
    """Thin CLI client for Herdr initialization."""
    r = resolve_repo_ref(args.repo)
    cp = HerdrControlPlane()

    try:
        result = cp.initialize(
            r,
            preset=args.preset,
            test_command=args.test_command,
            alias=args.alias,
        )
    except (RuntimeError, ValueError) as exc:
        raise SystemExit(str(exc))

    print(f"Initialized {result['herd_root']}")
    print(f"Registered as `{result['alias']}`")
    print(f"Kept harness local via {result['exclude_path']}")
    print("Installed repository-level commit + push guards.")

    if args.preset:
        print(f"Applied preset: {args.preset}")

    if args.test_command is not None:
        print(
            f"Verification command: "
            f"{args.test_command}"
        )

    print(
        f"Review {result['config_path']}"
    )



def presets_cmd(_):
    for name, spec in sorted(PRESETS.items()):
        print(f"{name:16} {spec.get('description','')}")


def preset_apply_cmd(args):
    r = resolve_repo_ref(args.repo)
    p = hroot(r) / CFG
    data = cfg(r)
    apply_preset_to_config(data, args.name)
    p.write_text(json.dumps(data, indent=2) + "\n")
    print(f"Applied preset `{args.name}` to {repo_alias(r)}.")
    print("Re-bootstrap with `herdctl bootstrap --repo %s --force` to replace running agent runtimes." % repo_alias(r))


def parse_policy_value(raw):
    """Parse a CLI policy value as JSON when possible, otherwise as text."""
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return raw


def set_nested_policy_value(policy, dotted_path, value):
    """Set a dotted policy path such as `git.push`."""
    parts = [p for p in dotted_path.split(".") if p]
    if not parts:
        raise ValueError("Policy path cannot be empty.")

    target = policy
    for part in parts[:-1]:
        child = target.get(part)
        if child is None:
            child = {}
            target[part] = child
        elif not isinstance(child, dict):
            raise ValueError(
                f"Cannot set {dotted_path!r}: {part!r} is not an object."
            )
        target = child

    target[parts[-1]] = value


def policy_cmd(args):
    """Thin CLI client for repository-scoped Herdr policy."""
    r = resolve_repo_ref(args.repo)
    herd = HerdrInstance(r)

    try:
        for assignment in args.set_values or []:
            if "=" not in assignment:
                raise ValueError(
                    f"Invalid --set value {assignment!r}. "
                    "Expected PATH=VALUE, for example git.push=forbidden."
                )

            dotted_path, raw = assignment.split("=", 1)

            herd.set_policy(
                dotted_path.strip(),
                parse_policy_value(raw.strip()),
            )

        for rule in args.add_rule or []:
            herd.add_rule(rule)

        for rule in args.remove_rule or []:
            herd.remove_rule(rule)

        policy = herd.effective_policy()

    except (RuntimeError, ValueError) as exc:
        raise SystemExit(str(exc))

    if args.set_values or args.add_rule or args.remove_rule:
        print(f"Updated policy for `{repo_alias(r)}`.")

    print(json.dumps(policy.to_dict(), indent=2))


def rules_cmd(args):
    """Human-facing repository rule management."""
    r = resolve_repo_ref(args.repo)
    cp = HerdrControlPlane()

    try:
        if args.action == "add":
            if not args.rule:
                raise ValueError(
                    "A rule is required. "
                    'Example: herdctl rules add "Never modify migrations"'
                )

            policy = cp.add_rule(
                r,
                args.rule,
            )

            print(
                f"Added rule for `{repo_alias(r)}`."
            )

        elif args.action == "remove":
            if not args.rule:
                raise ValueError(
                    "A rule is required. "
                    'Example: herdctl rules remove "Never modify migrations"'
                )

            policy = cp.remove_rule(
                r,
                args.rule,
            )

            print(
                f"Removed rule for `{repo_alias(r)}`."
            )

        else:
            policy = cp.policy(r)

    except (RuntimeError, ValueError) as exc:
        raise SystemExit(str(exc))

    rules = policy.rules

    if not rules:
        print(
            f"No rules configured for `{repo_alias(r)}`."
        )
        return

    print(
        f"Rules for `{repo_alias(r)}`:"
    )

    for index, rule in enumerate(
        rules,
        start=1,
    ):
        print(
            f"{index}. {rule}"
        )


def set_test_cmd(args):
    r = resolve_repo_ref(args.repo)
    p = hroot(r) / CFG
    data = cfg(r)
    data.setdefault("project", {})["test_command"] = args.command
    p.write_text(json.dumps(data, indent=2) + "\n")
    print(f"Verification command for `{repo_alias(r)}` set to: {args.command}")


def register_cmd(args):
    if args.path:
        p = Path(args.path).expanduser()
        q = run(["git", "-C", str(p), "rev-parse", "--show-toplevel"])
        if q.returncode:
            raise SystemExit(f"Not a git repository: {p}")
        r = Path(q.stdout.strip()).resolve()
    else:
        r = current_repo()
    alias = register_repo(r, args.alias)
    print(f"Registered `{alias}` -> {r}")


def repos_cmd(args):
    data = registry_load()
    repos = data.get("repos", {})
    if args.prune:
        removed = [a for a, ent in repos.items() if not Path(ent["path"]).exists()]
        for alias in removed:
            repos.pop(alias, None)
        registry_save(data)
        if removed:
            print("Pruned: " + ", ".join(sorted(removed)))
    if not repos:
        print("No registered repos.")
        return
    for alias, ent in sorted(repos.items()):
        p = Path(ent["path"])
        status = "missing" if not p.exists() else "registered"
        if p.exists() and (p / HERD / STATE).exists():
            status = "runtime"
        print(f"{alias:20} {status:12} {p}")


def upgrade_cmd(args):
    r = resolve_repo_ref(args.repo)
    hr = hroot(r)
    if not hr.exists():
        raise SystemExit(f"{r} is not initialized. Run `herdctl init` first.")
    src = Path(__file__).resolve().parent
    stamp = time.strftime("%Y%m%d-%H%M%S")
    backup = hr / "state" / f"upgrade-backup-{stamp}"
    backup.mkdir(parents=True, exist_ok=True)

    # Refresh role contracts, preserving a local backup for easy rollback.
    for name in ROLE_FILES.values():
        dst = hr / "roles" / name
        if dst.exists():
            (backup / name).write_text(dst.read_text())
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_text((src / "roles" / name).read_text())

    for name in ["architecture.md", "conventions.md", "decisions.md", "mistakes.md", "task-history.md"]:
        dst = hr / "memory" / name
        if not dst.exists():
            src_mem = src / "memory" / name
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_text(src_mem.read_text() if src_mem.exists() else f"# {name}\n")

    cp = hr / CFG
    current = json.loads(cp.read_text()) if cp.exists() else {}
    deep_merge_defaults(current, DEFAULT)
    current["version"] = DEFAULT["version"]
    current.setdefault("project", {}).setdefault("name", r.name)
    if args.preset:
        apply_preset_to_config(current, args.preset)
    cp.write_text(json.dumps(current, indent=2) + "\n")
    exclude = ensure_local_herd_exclude(r)
    install_git_guard(r)
    print(f"Upgraded {r} to harness config v{DEFAULT['version']}.")
    print(f"Role backup: {backup}")
    print(f"Harness remains local via {exclude}")
    if args.preset:
        print(f"Applied preset: {args.preset}")


def safety_install(_):
    settings, changed = install_claude_guard()
    print(("Updated" if changed else "Already configured") + f" Claude Code global commit/push guard + conservative auto-mode defaults in {settings}")
    print("Git commit/history/push guards are installed per initialized repository by `herdctl init` / `herdctl upgrade`.")


def approve_commit(args):
    r = resolve_repo_ref(args.repo)
    c = cfg(r)
    ident = repo_identity(r)
    alias = None
    for a, ent in registry_load().get("repos", {}).items():
        if Path(ent["path"]).resolve() == r.resolve():
            alias = a
            break
    alias = alias or c.get("project", {}).get("name") or r.name
    staged = gitout(r, "diff", "--cached", "--name-status", allow_fail=True)
    stat = gitout(r, "diff", "--cached", "--stat", allow_fail=True)
    print("\nCOMMIT AUTHORIZATION REQUEST")
    print("----------------------------")
    print(f"Repo alias : {alias}")
    print(f"Codebase   : {r.name}")
    print(f"Repo root  : {ident['repo_root']}")
    print(f"Branch     : {ident['branch']}")
    print(f"HEAD       : {ident['head']}")
    print(f"Origin     : {ident['remote']}")
    print(f"Staged hash: {ident['staged_sha256'][:16]}...")
    print("\nStaged files:")
    print(staged or "(none — only an empty commit would match this approval)")
    if stat:
        print("\n" + stat)
    if not args.yes:
        typed = input(f"\nType the repo alias `{alias}` to authorize exactly ONE commit: ").strip()
        if typed != alias:
            raise SystemExit("Not authorized. No approval token created.")
    tok = dict(ident)
    tok.update(
        {
            "alias": alias,
            "approved_at": int(time.time()),
            "expires_at": int(time.time()) + int(args.ttl),
        }
    )
    ap = approval_path(r)
    ap.parent.mkdir(parents=True, exist_ok=True)
    ap.write_text(json.dumps(tok, indent=2) + "\n")
    print(f"Authorized ONE commit for {args.ttl}s, bound to this repo/worktree/branch/HEAD/staged diff.")


def approve_push(args):
    r = resolve_repo_ref(args.repo)
    alias = repo_alias(r)
    ident = push_identity(
        r,
        args.remote,
        args.target_branch,
        getattr(args, "tag", None),
    )
    target_branch = args.target_branch or ident["branch"]
    upstream = gitout(r, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}", allow_fail=True) or "(no upstream)"
    ahead = "?"
    log = ""
    if not getattr(args, "tag", None):
        ahead = gitout(r, "rev-list", "--count", f"{args.remote}/{target_branch}..HEAD", allow_fail=True) or "?"
        log = gitout(r, "log", "--oneline", f"{args.remote}/{target_branch}..HEAD", allow_fail=True)
    print("\nPUSH AUTHORIZATION REQUEST")
    print("--------------------------")
    print(f"Repo alias : {alias}")
    print(f"Codebase   : {r.name}")
    print(f"Repo root  : {ident['repo_root']}")
    print(f"Branch     : {ident['branch']}")
    print(f"HEAD       : {ident['head']}")
    print(f"Remote     : {ident['remote_name']}")
    print(f"Remote URL : {ident['remote_url']}")
    print(f"Target     : {ident['target_ref']}")
    print(f"Upstream   : {upstream}")
    print(f"Ahead      : {ahead}")
    if log:
        print("\nCommits that would be pushed (best effort):")
        print(log)
    if not args.yes:
        typed = input(f"\nType the repo alias `{alias}` to authorize exactly ONE push: ").strip()
        if typed != alias:
            raise SystemExit("Not authorized. No push approval token created.")
    tok = dict(ident)
    tok.update({"alias": alias, "approved_at": int(time.time()), "expires_at": int(time.time()) + int(args.ttl)})
    pp = push_approval_path(r)
    pp.parent.mkdir(parents=True, exist_ok=True)
    pp.write_text(json.dumps(tok, indent=2) + "\n")
    print(f"Authorized ONE push for {args.ttl}s, bound to this repo/branch/HEAD/remote/target ref.")
    if getattr(args, "tag", None):
        print("Tag approvals are consumed only after `herdctl push-tag` confirms a successful transfer; `git push --dry-run` does not consume them.")
    else:
        print("Consumed when the approved commit is observed on the remote-tracking ref; `git push --dry-run` does not consume it. Unused approvals expire at the TTL.")


def push_tag_cmd(args):
    r = resolve_repo_ref(args.repo)
    tag_ref = f"refs/tags/{args.tag}"
    pp = push_approval_path(r)

    valid, msg = push_approval_valid(r)
    if not valid:
        raise SystemExit(msg)

    try:
        tok = json.loads(pp.read_text())
    except Exception:
        raise SystemExit("Push approval token is unreadable. Re-authorize.")

    if tok.get("source_ref") != tag_ref or tok.get("target_ref") != tag_ref:
        raise SystemExit(
            f"Push approval is not for tag `{args.tag}`. Re-authorize with `herdctl approve-push --tag {args.tag}`."
        )

    remote = tok.get("remote_name", "origin")
    result = run(
        ["git", "push", remote, f"{tag_ref}:{tag_ref}"],
        cwd=r,
    )

    if result.stdout:
        print(result.stdout, end="")
    if result.stderr:
        print(result.stderr, end="", file=sys.stderr)

    if result.returncode:
        raise SystemExit(result.returncode)

    pp.unlink(missing_ok=True)
    print(f"Tag `{args.tag}` pushed to `{remote}`; one-shot push approval consumed.")



# ---------- herd runtime ----------

def doctor(args):
    r = resolve_repo_ref(args.repo)
    try:
        c = cfg(r)
    except SystemExit:
        c = None
    rows = []
    for b in ["git", "python3", "herdr"]:
        rows.append((b, shutil.which(b) is not None))
    if c:
        kinds = {x.get("kind", "") for x in c["roles"].values()}
        for k in sorted(kinds):
            rows.append((f"Herdr kind {k}", k in SUPPORTED))
        if "claude" in kinds:
            rows.append(("claude", shutil.which("claude") is not None))
        if "codex" in kinds:
            rows.append(("codex", shutil.which("codex") is not None))
    width = max(len(a) for a, _ in rows)
    bad = False
    for label, ok in rows:
        print(f"{label:<{width}}  {'OK' if ok else 'MISSING'}")
        bad |= not ok
    if shutil.which("herdr"):
        p = run(["herdr", "agent", "list"])
        print(f"{'Herdr server':<{width}}  {'OK' if p.returncode == 0 else 'WARNING'}")
    hook_raw = gitout(r, "rev-parse", "--git-path", "hooks/pre-commit", allow_fail=True)
    hook = Path(hook_raw) if hook_raw else None
    if hook and not hook.is_absolute():
        hook = (r / hook).resolve()
    hook_ok = bool(hook and hook.exists() and "HERD COMMIT GUARD" in hook.read_text(errors="ignore"))
    ref_raw = gitout(r, "rev-parse", "--git-path", "hooks/reference-transaction", allow_fail=True)
    ref_hook = Path(ref_raw) if ref_raw else None
    if ref_hook and not ref_hook.is_absolute(): ref_hook = (r / ref_hook).resolve()
    ref_ok = bool(ref_hook and ref_hook.exists() and "HERD REFERENCE GUARD" in ref_hook.read_text(errors="ignore"))
    push_raw = gitout(r, "rev-parse", "--git-path", "hooks/pre-push", allow_fail=True)
    push_hook = Path(push_raw) if push_raw else None
    if push_hook and not push_hook.is_absolute(): push_hook = (r / push_hook).resolve()
    push_ok = bool(push_hook and push_hook.exists() and "HERD PUSH GUARD" in push_hook.read_text(errors="ignore"))
    print(f"{'Git pre-commit guard':<{width}}  {'OK' if hook_ok else 'MISSING'}")
    print(f"{'Git ref-update guard':<{width}}  {'OK' if ref_ok else 'MISSING'}")
    print(f"{'Git pre-push guard':<{width}}  {'OK' if push_ok else 'MISSING'}")
    if bad or not hook_ok or not ref_ok or not push_ok:
        raise SystemExit(1)


def integrations(args):
    c = cfg(resolve_repo_ref(args.repo))
    kinds = sorted({x.get("kind") for x in c["roles"].values() if x.get("kind")})
    for k in kinds:
        if k in INTEGRATIONS:
            print(f"Installing Herdr integration: {k}")
            p = run(["herdr", "integration", "install", k])
            if p.stdout.strip():
                print(p.stdout.strip())
            if p.returncode and p.stderr.strip():
                print(p.stderr.strip(), file=sys.stderr)
        else:
            print(f"{k}: no managed integration step; Herdr detection may still work.")



def bootstrap(args):
    """Thin CLI client for Herdr lifecycle startup."""
    r = resolve_repo_ref(args.repo)
    cp = HerdrControlPlane()

    try:
        cp.start(
            r,
            force=args.force,
        )
    except RuntimeError as exc:
        raise SystemExit(str(exc))

    print("Herd is live.")
    status(argparse.Namespace(repo=str(r)))



def resolve_role(r, logical):
    s = state(r)
    if logical in s["agents"]:
        return s["agents"][logical]
    if logical in s["agents"].values():
        return logical
    raise SystemExit(f"Unknown role {logical}. Available: {', '.join(s['agents'])}")


def role_type_for_logical(logical):
    if logical == "supervisor":
        return "supervisor"
    if logical.startswith("lead"):
        return "lead"
    if logical.startswith("executor"):
        return "executor"
    return "reviewer"


def status(args):
    r = resolve_repo_ref(args.repo)
    s = state(r)
    c = cfg(r)
    t = load_task(r)
    task_status = t.get("status", "IDLE")
    started = int(t.get("started_at", 0) or 0)
    completed = int(t.get("completed_at", 0) or 0)
    elapsed = (completed or int(time.time())) - started if started else 0
    print(f"Repository: {r}")
    if task_status != "IDLE":
        suffix = f" | {human_duration(elapsed)}" if started else ""
        print(f"Task: {task_status} {t.get('id','')} {suffix}")
        if t.get("heartbeat_count") is not None:
            print(f"Heartbeat prompts: {t.get('heartbeat_count', 0)}")
    else:
        print("Task: IDLE")
    show_hint = bool(c.get("context", {}).get("show_context_hint", True))
    for logical, agent in s["agents"].items():
        info = agent_info(agent)
        st = info["status"]
        hint = context_hint(agent) if show_hint and st != "missing" else None
        extra = f" context≈{hint}" if hint else ""
        print(f"{logical:12} {agent:32} {st}{extra}")


def send_runtime_reset(agent, command, timeout_ms=30000):
    """Reset one interactive runtime without destroying its Herdr pane/session process."""
    p = prompt(agent, command, timeout_ms, False)
    if p.returncode:
        return p
    time.sleep(0.5)
    return run([
        "herdr", "agent", "wait", agent,
        "--until", "idle", "--until", "done", "--until", "blocked",
        "--timeout", str(timeout_ms),
    ])


def clear_contexts_internal(r, c, s):
    t = load_task(r)
    if t.get("status") == "ACTIVE":
        raise SystemExit("Refusing to clear contexts during an ACTIVE top-level task.")
    allowed_types = set(c.get("context", {}).get("clear_roles", ["supervisor", "lead", "executor", "reviewer"]))
    reset_commands = c.get("context", {}).get("reset_commands", {"claude": "/clear", "codex": "/new"})
    selected = []
    for logical, agent in s["agents"].items():
        typ = role_type_for_logical(logical)
        if typ not in allowed_types:
            continue
        st = agent_info(agent)["status"]
        if st not in {"idle", "done"}:
            raise SystemExit(f"Refusing to clear `{logical}` while status is `{st}`. Resolve/finish it first.")
        kind = c.get("roles", {}).get(typ, {}).get("kind")
        reset = reset_commands.get(kind)
        if not reset:
            raise SystemExit(
                f"No context reset command configured for runtime kind `{kind}` ({logical}). "
                f"Set context.reset_commands.{kind} in {CFG}, or disable automatic clearing for that role."
            )
        selected.append((logical, agent, typ, kind, reset))

    print("Checkpointed task context is preserved on disk; clearing live model context...")
    for logical, agent, typ, kind, reset in selected:
        print(f"Clearing {logical} -> {agent} ({kind}: {reset})")
        p = send_runtime_reset(agent, reset)
        if p.returncode:
            raise SystemExit(p.stderr.strip() or p.stdout.strip() or f"Could not clear {logical}")

    timeout = int(c["orchestration"].get("agent_task_timeout_ms", 600000))
    agents = s["agents"]
    for logical, agent, typ, _kind, _reset in selected:
        print(f"Re-seeding {logical} contract")
        p = prompt(agent, bootstrap_text(r, logical, typ, agents, c), timeout, True)
        if p.returncode:
            print(f"WARNING: contract re-seed for {logical} did not settle cleanly: {p.stderr.strip()}", file=sys.stderr)


def clear_contexts_cmd(args):
    r = resolve_repo_ref(args.repo)
    clear_contexts_internal(r, cfg(r), state(r))
    print("Contexts cleared and role contracts re-seeded.")


def task_status_cmd(args):
    r = resolve_repo_ref(args.repo)
    t = load_task(r)
    print(json.dumps(t, indent=2))


def trim_task_history(path, max_chars):
    if max_chars <= 0 or not path.exists():
        return
    text = path.read_text(errors="replace")
    if len(text) <= max_chars:
        return
    header = "# Task History\n\nCompact checkpoints from completed top-level tasks.\n"
    tail = text[-max_chars:]
    marker = tail.find("\n---\n")
    if marker >= 0:
        tail = tail[marker + 1:]
    path.write_text(header + "\n\n" + tail.lstrip())


def task_complete_cmd(args):
    r = resolve_repo_ref(args.repo)
    t = load_task(r)
    if t.get("status") != "ACTIVE":
        raise SystemExit(f"No ACTIVE task to complete (current status: {t.get('status','IDLE')}).")

    cp = HerdrControlPlane()

    try:
        cp.require_child_dependencies_complete(
            r,
            t.get("id"),
        )
    except RuntimeError as exc:
        raise SystemExit(str(exc))

    checkpoint = ""
    checkpoint_path = None
    if args.checkpoint_file:
        checkpoint_path = Path(args.checkpoint_file)
        if not checkpoint_path.is_absolute():
            checkpoint_path = (r / checkpoint_path).resolve()
        try:
            checkpoint_path.relative_to(r.resolve())
        except ValueError:
            raise SystemExit("Checkpoint file must be inside the repository boundary.")
        if not checkpoint_path.exists():
            raise SystemExit(f"Checkpoint file not found: {checkpoint_path}")
        checkpoint = checkpoint_path.read_text(errors="replace")[:24000]
    elif args.note:
        checkpoint = args.note[:24000]

    now = int(time.time())
    t["status"] = "COMPLETE"
    t["completed_at"] = now
    t["duration_seconds"] = now - int(t.get("started_at", now))
    if checkpoint_path:
        t["checkpoint_file"] = str(checkpoint_path)
    if args.note:
        t["note"] = args.note
    save_task(r, t)
    archive_task(r, t)

    if cfg(r).get("context", {}).get("checkpoint_history", True):
        hist = hroot(r) / "memory" / "task-history.md"
        hist.parent.mkdir(parents=True, exist_ok=True)
        if not hist.exists():
            hist.write_text("# Task History\n\nCompact checkpoints from completed top-level tasks.\n")
        with hist.open("a") as f:
            f.write("\n\n---\n\n")
            f.write(f"## {time.strftime('%Y-%m-%d %H:%M:%S')} — {t.get('id','task')}\n\n")
            f.write(f"**Request:** {t.get('description','')}\n\n")
            f.write(f"**Duration:** {human_duration(t.get('duration_seconds',0))}\n\n")
            if checkpoint:
                f.write(checkpoint.rstrip() + "\n")
            else:
                f.write("No explicit checkpoint was supplied.\n")
        trim_task_history(hist, int(cfg(r).get("context", {}).get("task_history_max_chars", 60000)))
    print(f"Task {t.get('id')} marked COMPLETE in {human_duration(t.get('duration_seconds',0))}.")
    print("Idle-aware heartbeat will now sleep until a new ACTIVE task exists.")


def task_abort_cmd(args):
    r = resolve_repo_ref(args.repo)
    t = load_task(r)
    if t.get("status") != "ACTIVE":
        raise SystemExit(f"No ACTIVE task to abort (current status: {t.get('status','IDLE')}).")
    now = int(time.time())
    t["status"] = "ABORTED"
    t["completed_at"] = now
    t["duration_seconds"] = now - int(t.get("started_at", now))
    t["abort_reason"] = args.reason or "human aborted"
    save_task(r, t)
    archive_task(r, t)
    print(f"Task {t.get('id')} marked ABORTED.")


def task(args):
    """Thin CLI client for top-level task dispatch."""
    r = resolve_repo_ref(args.repo)
    cp = HerdrControlPlane()

    if getattr(args, "mission", False):
        mission = load_mission(r)
        if mission is None:
            raise SystemExit("No mission exists.")
        sections = [
            f"OBJECTIVE\n{str(mission.get('objective') or '').strip()}",
        ]

        for key, heading in (
            ("constraints", "CONSTRAINTS"),
            ("rules", "RULES"),
            ("acceptance_criteria", "ACCEPTANCE CRITERIA"),
            ("verification", "VERIFICATION"),
        ):
            values = mission.get(key) or []
            if values:
                sections.append(
                    heading + "\n" + "\n".join(
                        f"- {value}" for value in values
                    )
                )

        args.text = "\n\n".join(sections)

        if mission.get("rules"):
            args.rule = list(args.rule or []) + list(mission.get("rules", []))

    try:
        task_policy = None

        if args.rule:
            task_policy = {
                "rules": args.rule,
            }

        cp.dispatch_task(
            r,
            args.text,
            rejection_drill=args.rejection_drill,
            task_policy=task_policy,
        )
    except (RuntimeError, ValueError) as exc:
        raise SystemExit(str(exc))



def reviewer_transcript(agent, lines=500):
    p = run(["herdr", "agent", "read", agent, "--source", "recent-unwrapped", "--lines", str(lines)])
    if p.returncode:
        p = run(["herdr", "agent", "read", agent, "--source", "visible"])
    return (p.stdout or "") + (("\n" + p.stderr) if p.stderr else "")


def parse_review_decision(text, tail_lines=80):
    # The contract itself contains example protocol tokens, so only accept a
    # terminal token near the end of the reviewer's current visible transcript.
    lines = text.splitlines()
    tail = "\n".join(lines[-tail_lines:])
    matches = list(re.finditer(r"HERD_DECISION:\s*([A-Za-z_-]+)", tail))
    raw = matches[-1].group(1).upper() if matches else None
    return (raw if raw in {"APPROVE", "REJECT"} else None), raw


def review_decision_cmd(args):
    r = resolve_repo_ref(args.repo)
    reviewer = resolve_role(r, args.reviewer)
    text = reviewer_transcript(reviewer, args.lines)
    decision, raw = parse_review_decision(text)
    task_state = load_task(r)
    task_id = task_state.get("id") or "no-task"
    rounds = int(task_state.get("review_rounds", 0)) + 1
    outdir = hroot(r) / "state" / "reviews"
    outdir.mkdir(parents=True, exist_ok=True)
    outfile = outdir / f"{task_id}-round-{rounds:02d}.md"
    outfile.write_text(
        f"# Reviewer round {rounds}\n\n"
        f"Reviewer: `{args.reviewer}` / `{reviewer}`\n\n"
        f"Protocol token: `{raw or 'MISSING'}`\n\n"
        "## Transcript\n\n" + text
    )
    if task_state.get("status") == "ACTIVE":
        task_state["review_rounds"] = rounds
        task_state["last_review_decision"] = decision or "MALFORMED"
        task_state["last_review_file"] = str(outfile)
        save_task(r, task_state)
    result = {
        "valid": bool(decision),
        "decision": decision,
        "raw_token": raw,
        "round": rounds,
        "review_file": str(outfile),
    }
    print(json.dumps(result, indent=2))


def prompt_cmd(args):
    r = resolve_repo_ref(args.repo)
    c = cfg(r)
    agent = resolve_role(r, args.role)
    p = prompt(agent, args.text, int(c["orchestration"].get("agent_task_timeout_ms", 600000)), not args.no_wait)
    t = load_task(r)
    if t.get("status") == "ACTIVE":
        t["manual_prompt_count"] = int(t.get("manual_prompt_count", 0)) + 1
        save_task(r, t)
    if p.stdout.strip():
        print(p.stdout.strip())
    if p.returncode:
        raise SystemExit(p.stderr.strip())


def read_cmd(args):
    r = resolve_repo_ref(args.repo)
    agent = resolve_role(r, args.role)
    status_now = agent_info(agent)["status"]
    source = args.source
    if source == "auto":
        source = "visible" if status_now in {"working", "blocked", "unknown"} else "recent-unwrapped"
    cmd = ["herdr", "agent", "read", agent, "--source", source]
    if args.lines:
        cmd += ["--lines", str(args.lines)]
    p = run(cmd)
    if p.returncode and source != "visible" and "agent_not_idle" in (p.stderr + p.stdout):
        p = run(["herdr", "agent", "read", agent, "--source", "visible"])
    print(p.stdout, end="")
    if p.returncode:
        raise SystemExit(p.stderr.strip())


def focus_cmd(args):
    r = resolve_repo_ref(args.repo)
    agent = resolve_role(r, args.role)
    p = run(["herdr", "agent", "focus", agent])
    if p.returncode:
        raise SystemExit(p.stderr.strip())


def heartbeat(args):
    """Thin CLI client for the package-owned heartbeat controller."""
    r = resolve_repo_ref(args.repo)
    cp = HerdrControlPlane()

    try:
        cp.heartbeat(
            r,
            once=args.once,
        )
    except RuntimeError as exc:
        raise SystemExit(str(exc))


def stop_hb(args):
    """Thin CLI client for stopping heartbeat."""
    r = resolve_repo_ref(args.repo)
    cp = HerdrControlPlane()

    try:
        cp.stop_heartbeat(r)
    except RuntimeError as exc:
        raise SystemExit(str(exc))


def restart_hb(args):
    """Thin CLI client for restarting heartbeat."""
    r = resolve_repo_ref(args.repo)
    cp = HerdrControlPlane()

    try:
        cp.restart_heartbeat(r)
    except RuntimeError as exc:
        raise SystemExit(str(exc))



def guard_pretool_cmd(_):
    raise SystemExit(guard_pretool())


def guard_precommit_cmd(args):
    raise SystemExit(guard_precommit(Path(args.repo_path).resolve()))


def guard_reference_cmd(args):
    raise SystemExit(guard_reference_transaction(Path(args.repo_path).resolve(), args.phase))


def guard_prepush_cmd(args):
    raise SystemExit(guard_prepush(Path(args.repo_path).resolve(), args.remote_name, args.remote_url))



def mission_create_cmd(args):
    r = resolve_repo_ref(args.repo)
    mission = {
        "version": 1,
        "objective": args.objective,
        "constraints": args.constraint or [],
        "rules": args.rule or [],
        "acceptance_criteria": args.acceptance or [],
        "verification": args.verification or [],
    }
    save_mission(r, mission)
    print(json.dumps(mission, indent=2))


def mission_show_cmd(args):
    r = resolve_repo_ref(args.repo)
    mission = load_mission(r)
    if mission is None:
        raise SystemExit("No mission exists.")
    print(json.dumps(mission, indent=2))

def main():
    p = argparse.ArgumentParser(prog="herdctl")
    sp = p.add_subparsers(dest="cmd", required=True)

    q = sp.add_parser("init")
    q.add_argument("--repo")
    q.add_argument("--alias")
    q.add_argument("--preset", choices=sorted(PRESETS))
    q.add_argument("--test-command")
    q.set_defaults(fn=init_cmd)

    q = sp.add_parser("presets")
    q.set_defaults(fn=presets_cmd)

    q = sp.add_parser("preset")
    q.add_argument("name", choices=sorted(PRESETS))
    q.add_argument("--repo")
    q.set_defaults(fn=preset_apply_cmd)

    q = sp.add_parser("policy")
    q.add_argument("--repo")
    q.add_argument(
        "--set",
        dest="set_values",
        action="append",
        help="set policy PATH=VALUE; may be repeated",
    )
    q.add_argument(
        "--add-rule",
        action="append",
        help="append a repository rule; may be repeated",
    )
    q.add_argument(
        "--remove-rule",
        action="append",
        help="remove an exact repository rule; may be repeated",
    )
    q.set_defaults(fn=policy_cmd)

    q = sp.add_parser(
        "rules",
        help="view or manage repository-scoped Herdr rules",
    )
    q.add_argument(
        "action",
        nargs="?",
        choices=[
            "add",
            "remove",
        ],
    )
    q.add_argument(
        "rule",
        nargs="?",
    )
    q.add_argument(
        "--repo",
    )
    q.set_defaults(
        fn=rules_cmd
    )

    q = sp.add_parser("set-test")
    q.add_argument("command")
    q.add_argument("--repo")
    q.set_defaults(fn=set_test_cmd)

    q = sp.add_parser("register")
    q.add_argument("path", nargs="?")
    q.add_argument("--alias")
    q.set_defaults(fn=register_cmd)

    q = sp.add_parser("repos")
    q.add_argument("--prune", action="store_true", help="remove registry entries whose paths no longer exist")
    q.set_defaults(fn=repos_cmd)

    q = sp.add_parser("upgrade")
    q.add_argument("--repo")
    q.add_argument("--preset", choices=sorted(PRESETS))
    q.set_defaults(fn=upgrade_cmd)

    q = sp.add_parser("safety-install")
    q.set_defaults(fn=safety_install)

    q = sp.add_parser("approve-commit")
    q.add_argument("--repo")
    q.add_argument("--ttl", type=int, default=600)
    q.add_argument("--yes", action="store_true", help="non-interactive; use only after an external human confirmation")
    q.set_defaults(fn=approve_commit)

    q = sp.add_parser("approve-push")
    q.add_argument("--repo")
    q.add_argument("--remote", default="origin")
    target = q.add_mutually_exclusive_group()
    target.add_argument("--target-branch")
    target.add_argument("--tag")
    q.add_argument("--ttl", type=int, default=600)
    q.add_argument("--yes", action="store_true", help="non-interactive; use only after an external human confirmation")
    q.set_defaults(fn=approve_push)

    q = sp.add_parser("push-tag")
    q.add_argument("tag")
    q.add_argument("--repo")
    q.set_defaults(fn=push_tag_cmd)

    q = sp.add_parser("doctor")
    q.add_argument("--repo")
    q.set_defaults(fn=doctor)

    q = sp.add_parser("integrations")
    q.add_argument("--repo")
    q.set_defaults(fn=integrations)

    q = sp.add_parser("bootstrap")
    q.add_argument("--repo")
    q.add_argument("--force", action="store_true")
    q.set_defaults(fn=bootstrap)

    q = sp.add_parser("status")
    q.add_argument("--repo")
    q.set_defaults(fn=status)

    q = sp.add_parser("mission")
    msp = q.add_subparsers(dest="mission_cmd", required=True)

    m = msp.add_parser("create")
    m.add_argument("objective")
    m.add_argument("--repo")
    m.add_argument("--constraint", action="append")
    m.add_argument("--rule", action="append")
    m.add_argument("--acceptance", action="append")
    m.add_argument("--verification", action="append")
    m.set_defaults(fn=mission_create_cmd)

    m = msp.add_parser("show")
    m.add_argument("--repo")
    m.set_defaults(fn=mission_show_cmd)

    q = sp.add_parser("task")
    q.add_argument("text", nargs="?")
    q.add_argument("--repo")
    q.add_argument("--mission", action="store_true", help="dispatch the current Herdr mission")
    q.add_argument(
        "--rule",
        action="append",
        default=[],
        help="apply a rule to this task only; may be repeated",
    )
    q.add_argument("--rejection-drill", action="store_true", help="force one process-only Reviewer rejection to validate same-session routing")
    q.set_defaults(fn=task)

    q = sp.add_parser("task-status")
    q.add_argument("--repo")
    q.set_defaults(fn=task_status_cmd)

    q = sp.add_parser("task-complete")
    q.add_argument("--repo")
    q.add_argument("--checkpoint-file")
    q.add_argument("--note")
    q.set_defaults(fn=task_complete_cmd)

    q = sp.add_parser("task-abort")
    q.add_argument("--repo")
    q.add_argument("--reason")
    q.set_defaults(fn=task_abort_cmd)

    q = sp.add_parser("clear-contexts")
    q.add_argument("--repo")
    q.set_defaults(fn=clear_contexts_cmd)

    q = sp.add_parser("review-decision")
    q.add_argument("--repo")
    q.add_argument("--reviewer", default="reviewer1")
    q.add_argument("--lines", type=int, default=500)
    q.set_defaults(fn=review_decision_cmd)

    q = sp.add_parser("prompt")
    q.add_argument("role")
    q.add_argument("text", nargs="?")
    q.add_argument("--repo")
    q.add_argument("--no-wait", action="store_true")
    q.set_defaults(fn=prompt_cmd)

    q = sp.add_parser("read")
    q.add_argument("role")
    q.add_argument("--repo")
    q.add_argument("--lines", type=int, default=160)
    q.add_argument("--source", choices=["auto", "visible", "recent", "recent-unwrapped", "detection"], default="auto")
    q.set_defaults(fn=read_cmd)

    q = sp.add_parser("focus")
    q.add_argument("role")
    q.add_argument("--repo")
    q.set_defaults(fn=focus_cmd)

    q = sp.add_parser("heartbeat")
    q.add_argument("--repo")
    q.add_argument("--once", action="store_true")
    q.set_defaults(fn=heartbeat)

    q = sp.add_parser("stop-heartbeat")
    q.add_argument("--repo")
    q.set_defaults(fn=stop_hb)

    q = sp.add_parser("restart-heartbeat")
    q.add_argument("--repo")
    q.set_defaults(fn=restart_hb)

    q = sp.add_parser("_guard-pretool", help=argparse.SUPPRESS)
    q.set_defaults(fn=guard_pretool_cmd)

    q = sp.add_parser("_guard-precommit", help=argparse.SUPPRESS)
    q.add_argument("--repo-path", required=True)
    q.set_defaults(fn=guard_precommit_cmd)

    q = sp.add_parser("_guard-reference", help=argparse.SUPPRESS)
    q.add_argument("--repo-path", required=True)
    q.add_argument("--phase", required=True)
    q.set_defaults(fn=guard_reference_cmd)

    q = sp.add_parser("_guard-prepush", help=argparse.SUPPRESS)
    q.add_argument("--repo-path", required=True)
    q.add_argument("--remote-name", required=True)
    q.add_argument("--remote-url", required=True)
    q.set_defaults(fn=guard_prepush_cmd)

    args = p.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
