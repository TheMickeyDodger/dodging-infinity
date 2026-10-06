"""Regression suite for the Herdr Observability Layer (herdr/observe.py).

Hermetic: builds temp git repos, patches the runtime probe entry point,
and never requires a `herdr` binary on PATH or any network access.
Run as: PYTHONPATH=$PWD python3 tests/test_observe.py
"""

import argparse
import contextlib
import hashlib
import io
import itertools
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from _hermetic_git import run_git_completed

import herdctl
from herdr import observe as obs_mod
from herdr.observe import (
    OBSERVE_SCHEMA_VERSION,
    _OBSERVE_MAX_AGENT_PROBES,
    _OBSERVE_MAX_ARTIFACTS,
    _OBSERVE_MAX_CHILDREN,
    _OBSERVE_MAX_DIRTY_LINES,
    _OBSERVE_MAX_FILE_BYTES,
    _OBSERVE_MAX_LISTED_AGENTS,
    _OBSERVE_MAX_RECENT_TASKS,
    _OBSERVE_MAX_REVIEW_FILES,
    _OBSERVE_MAX_STRING,
    observe,
    observe_spawn_records,
    render_observation,
)

R = Path(__file__).resolve().parents[1]

#: I6 (R-46/R-55/R-61) added four sections. The order is the order
#: `observe` emits them, and this pin is what makes a section
#: appearing or vanishing a test failure rather than a surprise for a
#: consumer — which is why it is updated deliberately here rather
#: than loosened to a set comparison.
TOP_KEYS = [
    "schema_version", "generated_at", "completeness", "repository", "config",
    "vintage", "checkpoint", "roles", "turns",
    "mission", "task", "runtime", "agents", "children", "reviews",
    "artifacts", "recent_tasks", "legacy", "diagnostics",
]

SECTION_KEYS = [
    "repository", "config", "mission", "task", "runtime", "agents",
    "children", "reviews", "artifacts", "recent_tasks", "legacy",
]

STATE_VOCAB = {"available", "missing", "malformed", "unreadable", "unavailable", "empty"}

TASK_ID = "20260101-000000-abcdef"

SENTINEL_RAW_KEY = "SENTINEL_RAW_PAYLOAD_KEY_9c4f"


def fake_agent_info(status="idle"):
    return Mock(return_value={"status": status, "raw": {SENTINEL_RAW_KEY: 1}})


def git(repo, *args, check=True):
    # Hermetic delegate: invocation-local identity from the shared
    # helper, so commits need no ambient Git identity (guarded by
    # tests/test_hermetic_git.py).
    return run_git_completed(
        ["--no-optional-locks", "-C", str(repo), *args], check=check,
    )


def make_git_repo(base, name="repo"):
    repo = Path(base) / name
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True, capture_output=True)
    git(repo, "config", "user.email", "test@example.com")
    git(repo, "config", "user.name", "Test")
    (repo / "README.md").write_text("hello\n")
    git(repo, "add", "README.md")
    git(repo, "commit", "-qm", "initial")
    return repo


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n")


def populate_herd(repo, task_id=TASK_ID):
    state = repo / ".herd" / "state"
    state.mkdir(parents=True, exist_ok=True)
    write_json(repo / ".herd" / "herd.config.json", {
        "version": 4,
        "preset": "all-claude",
        "project": {"name": "demo-project"},
        "orchestration": {"leads": 1, "pods": 1, "heartbeat_seconds": 900},
        "policy": {
            "review": {"required": True, "max_rounds": 5},
            "git": {"commit": "require-human", "push": "require-human"},
        },
        "roles": {
            "supervisor": {"kind": "claude", "args": ["--model", "fable"]},
            "lead": {"kind": "claude", "args": ["--model", "opus"]},
            "executor": {"kind": "claude", "args": ["--model", "fable"]},
            "reviewer": {"kind": "codex", "args": ["-m", "gpt-5.6-sol"]},
        },
    })
    write_json(state / "mission.json", {
        "version": 1,
        "objective": "Test objective",
        "constraints": ["c1", "c2"],
        "rules": ["r1"],
        "acceptance_criteria": ["a1", "a2", "a3"],
        "verification": ["v1"],
    })
    write_json(state / "task.json", {
        "version": 1,
        "id": task_id,
        "status": "ACTIVE",
        "description": "Test task",
        "started_at": 1787000000,
        "heartbeat_count": 2,
        "manual_prompt_count": 1,
        "rejection_drill": False,
        "policy": {"rules": ["r1", "r2"]},
    })
    write_json(state / "runtime.json", {
        "version": 2,
        "workspace_id": "wT",
        "created_at": 1787000000,
        "panes": {"supervisor": "wT:p1", "lead1": "wT:p2",
                  "executor1": "wT:p3", "reviewer1": "wT:p4"},
        "agents": {"supervisor": "h-t-sup", "lead1": "h-t-lead1",
                   "executor1": "h-t-exec1", "reviewer1": "h-t-rev1"},
    })
    write_json(state / "children.json", {
        "version": 1,
        "children": [{
            "parent_task_id": task_id,
            "repo": "/tmp/child-repo",
            "task_id": "child-task-1",
            "task_status": "ACTIVE",
            "role": "child",
        }],
    })
    reviews = state / "reviews"
    reviews.mkdir(exist_ok=True)
    (reviews / f"{task_id}-round-01.md").write_text(
        "# Reviewer round 1\n\ntranscript...\nHERD_DECISION: APPROVE\n"
    )
    tasks = state / "tasks"
    tasks.mkdir(exist_ok=True)
    write_json(tasks / "20251231-old.json", {
        "id": "20251231-old", "status": "COMPLETE",
        "started_at": 1786000000, "completed_at": 1786003600,
        "duration_seconds": 3600, "description": "old task",
    })
    (state / "task-checkpoint.md").write_text("checkpoint\n")
    (state / "events.jsonl").write_text('{"legacy": true}\n')
    (state / "exec1-brief-20260101.md").write_text("brief\n")
    return repo


def minimal_env(home):
    # The git dir MUST come from live PATH resolution, never from an
    # absolute git-binary literal (rule F in tests/test_hermetic_git.py):
    # a literal here would silently take child processes out of the
    # executed identity sweep's field of view.
    which_git = shutil.which("git")
    git_dir = os.path.dirname(which_git) if which_git else "/usr/bin"
    return {
        "PATH": git_dir + os.pathsep + "/usr/bin" + os.pathsep + "/bin",
        "HOME": str(home),
        "PYTHONPATH": str(R),
    }


def run_cli(args, cwd, env):
    return subprocess.run(
        [sys.executable, str(R / "herdctl.py"), *args],
        capture_output=True, text=True, cwd=str(cwd), env=env,
    )


class SchemaStabilityTests(unittest.TestCase):
    def assert_schema(self, obs):
        self.assertEqual(list(obs.keys()), TOP_KEYS)
        self.assertEqual(obs["schema_version"], OBSERVE_SCHEMA_VERSION)
        self.assertEqual(obs["schema_version"], 3)
        self.assertIn(obs["completeness"], {"COMPLETE", "PARTIAL"})
        self.assertIsInstance(obs["generated_at"], int)
        self.assertIsInstance(obs["diagnostics"], list)
        for key in SECTION_KEYS:
            self.assertIsInstance(obs[key], dict, key)
            self.assertIn(obs[key].get("state"), STATE_VOCAB, key)
        for diag in obs["diagnostics"]:
            self.assertEqual(set(diag.keys()), {"source", "state", "detail"})

    def test_fully_empty_directory(self):
        with tempfile.TemporaryDirectory() as td:
            bare = Path(td) / "bare"
            bare.mkdir()
            obs = observe(bare)
            self.assert_schema(obs)
            self.assertEqual(obs["repository"]["is_git_repo"], False)
            self.assertEqual(obs["config"]["state"], "missing")
            self.assertEqual(obs["task"]["state"], "missing")
            self.assertEqual(obs["runtime"]["state"], "missing")

    def test_fully_populated_repository(self):
        with tempfile.TemporaryDirectory() as td:
            repo = populate_herd(make_git_repo(td))
            with patch.object(obs_mod, "agent_info", fake_agent_info()):
                obs = observe(repo)
            self.assert_schema(obs)
            for key in ["repository", "config", "mission", "task", "runtime",
                        "agents", "children", "reviews", "artifacts", "recent_tasks"]:
                self.assertEqual(obs[key]["state"], "available", key)
            self.assertEqual(obs["completeness"], "COMPLETE")
            self.assertEqual(obs["repository"]["is_git_repo"], True)
            self.assertFalse(obs["repository"]["dirty_file_count_capped"])
            self.assertEqual(obs["config"]["project_name"], "demo-project")
            self.assertEqual(obs["config"]["preset"], "all-claude")
            self.assertEqual(obs["config"]["review"]["max_rounds"], 5)
            models = {r["role"]: r["configured_model"]
                      for r in obs["config"]["roles"]}
            self.assertEqual(models["supervisor"], "fable")
            self.assertEqual(models["reviewer"], "gpt-5.6-sol")
            self.assertEqual(obs["task"]["id"], TASK_ID)
            self.assertEqual(obs["task"]["rule_count"], 2)
            self.assertIsInstance(obs["task"]["elapsed_seconds"], int)
            self.assertEqual(obs["mission"]["constraint_count"], 2)
            self.assertEqual(obs["mission"]["acceptance_count"], 3)
            self.assertEqual(obs["runtime"]["agent_count"], 4)
            self.assertEqual(obs["runtime"]["pane_count"], 4)


class RenderingStabilityTests(unittest.TestCase):
    def test_renders_all_fixtures_without_raw_payloads(self):
        with tempfile.TemporaryDirectory() as td:
            bare = Path(td) / "bare"
            bare.mkdir()
            repo = populate_herd(make_git_repo(td))
            with patch.object(obs_mod, "agent_info", fake_agent_info()):
                fixtures = [observe(bare), observe(repo), observe(repo, probe_agents=False)]
            for obs in fixtures:
                text = render_observation(obs)
                self.assertIsInstance(text, str)
                # Anchored labels: "Task [" / "Agents [" cannot be satisfied
                # by other lines (e.g. "Recent tasks") the way bare
                # substrings could.
                self.assertIn("Herd observation — schema", text)
                self.assertIn("Task [", text)
                self.assertIn("Agents [", text)
                self.assertIn("Diagnostics:", text)
                self.assertNotIn(SENTINEL_RAW_KEY, text)
                self.assertNotIn(SENTINEL_RAW_KEY, json.dumps(obs))
        self.assertEqual(render_observation(None), "Herd observation: unavailable\n")
        self.assertIsInstance(render_observation({}), str)


class BoundedProbeTests(unittest.TestCase):
    def build_huge_runtime(self, repo, total=100_000):
        expected = {
            "supervisor": "SUP-AGENT", "lead1": "LEAD-AGENT",
            "executor1": "EXEC-AGENT", "reviewer1": "REV-AGENT",
        }
        alphabet = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
        agents = dict(expected)
        for combo in itertools.product(alphabet, repeat=3):
            if len(agents) >= total:
                break
            agents["".join(combo)] = "x"
        payload = json.dumps(
            {"version": 2, "agents": agents},
            separators=(",", ":"),
        )
        self.assertLessEqual(len(payload.encode()), _OBSERVE_MAX_FILE_BYTES)
        path = repo / ".herd" / "state" / "runtime.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(payload)
        return expected, len(agents)

    def test_probe_cap_and_expected_roles_first(self):
        with tempfile.TemporaryDirectory() as td:
            repo = populate_herd(make_git_repo(td))
            expected, total = self.build_huge_runtime(repo)
            fake = fake_agent_info()
            with patch.object(obs_mod, "agent_info", fake):
                obs = observe(repo)
            self.assertEqual(fake.call_count, _OBSERVE_MAX_AGENT_PROBES)
            probed_names = {call.args[0] for call in fake.call_args_list}
            for name in expected.values():
                self.assertIn(name, probed_names)
            agents = obs["agents"]
            self.assertEqual(agents["probed"], _OBSERVE_MAX_AGENT_PROBES)
            self.assertEqual(agents["unprobed"], total - _OBSERVE_MAX_AGENT_PROBES)
            self.assertTrue(agents["truncated"])
            self.assertLessEqual(len(agents["listed"]), _OBSERVE_MAX_LISTED_AGENTS)
            for entry in agents["listed"]:
                self.assertEqual(set(entry.keys()), {"logical", "agent", "status", "probe"})
            probe_diags = [d for d in obs["diagnostics"]
                           if d["source"] == "agents" and "probe cap" in d["detail"]]
            self.assertEqual(len(probe_diags), 1)
            self.assertEqual(obs["completeness"], "PARTIAL")

    def test_probe_disabled_is_hermetic(self):
        with tempfile.TemporaryDirectory() as td:
            repo = populate_herd(make_git_repo(td))
            fake = fake_agent_info()
            with patch.object(obs_mod, "agent_info", fake):
                obs = observe(repo, probe_agents=False)
            self.assertEqual(fake.call_count, 0)
            self.assertEqual(obs["agents"]["probed"], 0)
            self.assertEqual(obs["agents"]["unprobed"], 4)
            for entry in obs["agents"]["listed"]:
                self.assertEqual(entry["probe"], "unprobed")
                self.assertIsNone(entry["status"])
            self.assertTrue(any(
                d["source"] == "agents" and d["state"] == "unavailable"
                for d in obs["diagnostics"]
            ))

    def test_missing_binary_yields_missing_probe(self):
        with tempfile.TemporaryDirectory() as td:
            repo = populate_herd(make_git_repo(td))

            def boom(name):
                raise FileNotFoundError("no runtime binary")

            with patch.object(obs_mod, "agent_info", boom):
                obs = observe(repo)
            for entry in obs["agents"]["listed"]:
                self.assertEqual(entry["probe"], "missing")


class BoundedHistoryTests(unittest.TestCase):
    def test_recent_tasks_capped_newest_first(self):
        with tempfile.TemporaryDirectory() as td:
            repo = populate_herd(make_git_repo(td))
            tasks = repo / ".herd" / "state" / "tasks"
            shutil.rmtree(tasks)
            tasks.mkdir()
            for i in range(500):
                path = tasks / f"task-{i:04d}.json"
                path.write_text(json.dumps({
                    "id": f"task-{i:04d}", "status": "COMPLETE",
                    "started_at": 1000 + i, "completed_at": 2000 + i,
                }))
                stamp = 1_000_000_000 + i
                os.utime(path, (stamp, stamp))
            obs = observe(repo, probe_agents=False)
            recent = obs["recent_tasks"]
            self.assertEqual(recent["total"], 500)
            self.assertEqual(len(recent["listed"]), _OBSERVE_MAX_RECENT_TASKS)
            self.assertEqual(recent["listed"][0]["id"], "task-0499")
            self.assertEqual(recent["listed"][-1]["id"], "task-0490")
            self.assertTrue(recent["truncated"])
            self.assertTrue(any(
                d["source"] == "recent_tasks" and "truncated" in d["detail"]
                for d in obs["diagnostics"]
            ))


class BoundsAndTruncationTests(unittest.TestCase):
    def test_huge_strings_truncate_everywhere(self):
        with tempfile.TemporaryDirectory() as td:
            repo = populate_herd(make_git_repo(td))
            state = repo / ".herd" / "state"
            big = "X" * 300_000
            write_json(state / "runtime.json", {
                "version": 2, "workspace_id": "wT", "agents": {"supervisor": big},
            })
            write_json(state / "task.json", {
                "id": TASK_ID, "status": "ACTIVE", "description": big,
                "started_at": 1787000000,
            })
            write_json(state / "mission.json", {"version": 1, "objective": big})
            with patch.object(obs_mod, "agent_info", fake_agent_info()):
                obs = observe(repo)
            # Truncated strings are exactly _OBSERVE_MAX_STRING characters
            # total, visible ellipsis included (code and README agree).
            self.assertEqual(len(obs["agents"]["listed"][0]["agent"]), _OBSERVE_MAX_STRING)
            self.assertEqual(len(obs["task"]["description"]), _OBSERVE_MAX_STRING)
            self.assertEqual(len(obs["mission"]["objective"]), _OBSERVE_MAX_STRING)
            self.assertLess(len(render_observation(obs)), 20_000)
            self.assertLess(len(json.dumps(obs)), 100_000)

    def test_reviews_children_artifacts_bounded(self):
        with tempfile.TemporaryDirectory() as td:
            repo = populate_herd(make_git_repo(td))
            state = repo / ".herd" / "state"
            reviews = state / "reviews"
            for i in range(1, 61):
                (reviews / f"{TASK_ID}-round-{i:02d}.md").write_text(
                    f"round {i}\nHERD_DECISION: REJECT\n"
                )
            write_json(state / "children.json", {"children": [
                {"parent_task_id": TASK_ID, "repo": f"/tmp/c{i}",
                 "task_id": f"c{i}", "task_status": "ACTIVE", "role": "child"}
                for i in range(40)
            ]})
            for i in range(30):
                (state / f"x{i:02d}-brief-20260101.md").write_text("b\n")
            obs = observe(repo, probe_agents=False)
            self.assertEqual(len(obs["reviews"]["listed"]), _OBSERVE_MAX_REVIEW_FILES)
            self.assertEqual(obs["reviews"]["total_files"], 60)
            self.assertEqual(obs["reviews"]["rounds"], 60)
            self.assertTrue(obs["reviews"]["truncated"])
            self.assertEqual(obs["reviews"]["listed"][0]["round"], 21)
            self.assertEqual(obs["reviews"]["listed"][-1]["round"], 60)
            self.assertEqual(obs["children"]["count"], 40)
            self.assertEqual(len(obs["children"]["listed"]), _OBSERVE_MAX_CHILDREN)
            self.assertTrue(obs["children"]["truncated"])
            self.assertLessEqual(len(obs["artifacts"]["listed"]), _OBSERVE_MAX_ARTIFACTS)


class ScanBudgetDisclosureTests(unittest.TestCase):
    """Exhausted directory-scan budgets must be disclosed as `unavailable`
    diagnostics (counts become lower bounds), never silently swallowed
    (reviewer findings B2/B3); listing truncation with exact totals stays
    a non-demoting `available` diagnostic (B4)."""

    def test_task_scan_budget_spent_on_nonmatching_entries_is_disclosed(self):
        # Reviewer B2 counter-example: 50 *.json among 3000 *.md files.
        with tempfile.TemporaryDirectory() as td:
            repo = populate_herd(make_git_repo(td))
            tasks = repo / ".herd" / "state" / "tasks"
            shutil.rmtree(tasks)
            tasks.mkdir()
            for i in range(3000):
                (tasks / f"noise-{i:04d}.md").write_text("noise\n")
            for i in range(50):
                (tasks / f"task-{i:04d}.json").write_text(json.dumps(
                    {"id": f"task-{i:04d}", "status": "COMPLETE"}
                ))
            obs = observe(repo, probe_agents=False)
            recent = obs["recent_tasks"]
            self.assertLessEqual(recent["total"], 50)
            scan_diags = [
                d for d in obs["diagnostics"]
                if d["source"] == "recent_tasks" and "scan capped" in d["detail"]
            ]
            self.assertEqual(len(scan_diags), 1)
            self.assertEqual(scan_diags[0]["state"], "unavailable")
            self.assertEqual(obs["completeness"], "PARTIAL")

    def test_brief_scan_cap_is_disclosed(self):
        # Reviewer B3 counter-example: >2000 state entries full of briefs.
        with tempfile.TemporaryDirectory() as td:
            repo = populate_herd(make_git_repo(td))
            state = repo / ".herd" / "state"
            for i in range(3000):
                (state / f"a{i:04d}-brief-old.md").write_text("old\n")
            (state / "zzz-brief-NEWEST.md").write_text("newest\n")
            obs = observe(repo, probe_agents=False)
            scan_diags = [
                d for d in obs["diagnostics"]
                if d["source"] == "artifacts" and "scan capped" in d["detail"]
            ]
            self.assertEqual(len(scan_diags), 1)
            self.assertEqual(scan_diags[0]["state"], "unavailable")
            self.assertIn("best-effort", scan_diags[0]["detail"])
            self.assertEqual(obs["completeness"], "PARTIAL")
            self.assertLessEqual(len(obs["artifacts"]["listed"]), _OBSERVE_MAX_ARTIFACTS)

    def test_children_count_is_exact_beyond_listing_cap(self):
        # Reviewer B4 counter-example: 5000 matching records (under the
        # 1 MiB file bound) — `count` must be the TRUE matched count.
        with tempfile.TemporaryDirectory() as td:
            repo = populate_herd(make_git_repo(td))
            write_json(repo / ".herd" / "state" / "children.json", {"children": [
                {"parent_task_id": TASK_ID, "repo": f"/tmp/c{i}",
                 "task_id": f"c{i}", "task_status": "ACTIVE"}
                for i in range(5000)
            ]})
            with patch.object(obs_mod, "agent_info", fake_agent_info()):
                obs = observe(repo)
            children = obs["children"]
            self.assertEqual(children["count"], 5000)
            self.assertEqual(len(children["listed"]), _OBSERVE_MAX_CHILDREN)
            self.assertTrue(children["truncated"])
            self.assertTrue(any(
                d["source"] == "children" and "truncated to 32 of 5000" in d["detail"]
                for d in obs["diagnostics"]
            ))
            # Listing truncation with an exact total is disclosed but does
            # not demote completeness.
            self.assertEqual(obs["completeness"], "COMPLETE")

    def test_dirty_file_count_cap_disclosed(self):
        # R2-B1: >_OBSERVE_MAX_DIRTY_LINES dirty paths must never be
        # reported as an exact count — capped value + flag + demoting
        # diagnostic, and the human render must say the count is capped.
        with tempfile.TemporaryDirectory() as td:
            repo = populate_herd(make_git_repo(td))
            for i in range(_OBSERVE_MAX_DIRTY_LINES + 500):
                (repo / f"dirty-{i:04d}.txt").write_text("x\n")
            obs = observe(repo, probe_agents=False)
            repository = obs["repository"]
            self.assertTrue(repository["dirty"])
            self.assertEqual(repository["dirty_file_count"], _OBSERVE_MAX_DIRTY_LINES)
            self.assertTrue(repository["dirty_file_count_capped"])
            cap_diags = [
                d for d in obs["diagnostics"]
                if d["source"] == "repository" and "capped" in d["detail"]
            ]
            self.assertEqual(len(cap_diags), 1)
            self.assertEqual(cap_diags[0]["state"], "unavailable")
            self.assertEqual(obs["completeness"], "PARTIAL")
            # Anchor to the git line: the diagnostics echo also contains the
            # substring "count capped", so a whole-render assertIn would stay
            # green with the render label removed (reviewer R3-B1).
            git_line = [l for l in render_observation(obs).splitlines()
                        if "git:" in l][0]
            self.assertIn("count capped", git_line)
            self.assertIn(">=", git_line)

    def test_dirty_file_count_exact_when_under_cap(self):
        with tempfile.TemporaryDirectory() as td:
            repo = populate_herd(make_git_repo(td))
            (repo / "one-dirty.txt").write_text("x\n")
            obs = observe(repo, probe_agents=False)
            repository = obs["repository"]
            self.assertTrue(repository["dirty"])
            self.assertFalse(repository["dirty_file_count_capped"])
            git_line = [l for l in render_observation(obs).splitlines()
                        if "git:" in l][0]
            self.assertNotIn("count capped", git_line)
            self.assertIn("file(s))", git_line)

    def test_reviews_scan_cap_is_disclosed(self):
        # R2-B4: an exhausted reviews directory scan must emit the
        # `unavailable` lower-bounds diagnostic and demote completeness.
        with tempfile.TemporaryDirectory() as td:
            repo = populate_herd(make_git_repo(td))
            reviews = repo / ".herd" / "state" / "reviews"
            for i in range(2100):
                (reviews / f"noise-{i:04d}.md").write_text("noise\n")
            obs = observe(repo, probe_agents=False)
            scan_diags = [
                d for d in obs["diagnostics"]
                if d["source"] == "reviews" and "scan capped" in d["detail"]
            ]
            self.assertEqual(len(scan_diags), 1)
            self.assertEqual(scan_diags[0]["state"], "unavailable")
            self.assertIn("lower bounds", scan_diags[0]["detail"])
            self.assertEqual(obs["completeness"], "PARTIAL")

    def test_render_disclosures_for_sampled_rows(self):
        # The human render never shows a subset of rows without saying so.
        obs = {
            "schema_version": 1, "generated_at": 0, "completeness": "COMPLETE",
            "recent_tasks": {
                "state": "available", "total": 10, "truncated": False,
                "listed": [{"id": f"t{i}", "status": "COMPLETE"} for i in range(10)],
            },
            "diagnostics": [
                {"source": "x", "state": "available", "detail": "d"}
                for _ in range(40)
            ],
        }
        text = render_observation(obs)
        self.assertIn("(showing 3 of 10 listed)", text)
        self.assertIn("Diagnostics: 40 (showing 32)", text)

    def test_artifact_allowlist_survives_stat_failure(self):
        # A stat failure must not remove a fixed-allowlist name (the
        # section is structural); it degrades to a diagnostic instead.
        with tempfile.TemporaryDirectory() as td:
            repo = populate_herd(make_git_repo(td))
            (repo / ".herd" / "state" / "supervisor-status.md").write_text("s\n")
            real_stat = Path.stat

            def failing_stat(self, **kwargs):
                if self.name == "supervisor-status.md":
                    raise PermissionError("denied")
                return real_stat(self, **kwargs)

            with patch("pathlib.Path.stat", failing_stat):
                obs = observe(repo, probe_agents=False)
            names = [e["name"] for e in obs["artifacts"]["listed"]]
            self.assertIn("supervisor-status.md", names)
            entry = next(e for e in obs["artifacts"]["listed"]
                         if e["name"] == "supervisor-status.md")
            self.assertFalse(entry["present"])
            self.assertTrue(any(
                d["source"] == "artifacts"
                and "supervisor-status.md" in d["detail"]
                and d["state"] == "unreadable"
                for d in obs["diagnostics"]
            ))
            self.assertEqual(obs["completeness"], "PARTIAL")


class GracefulDegradationTests(unittest.TestCase):
    FILES = [
        (Path(".herd") / "herd.config.json", "config"),
        (Path(".herd") / "state" / "mission.json", "mission"),
        (Path(".herd") / "state" / "task.json", "task"),
        (Path(".herd") / "state" / "runtime.json", "runtime"),
        (Path(".herd") / "state" / "children.json", "children"),
    ]

    def check(self, repo, section, expected_state, expect_diag=True):
        obs = observe(repo, probe_agents=False)
        self.assertEqual(list(obs.keys()), TOP_KEYS)
        self.assertEqual(obs[section]["state"], expected_state, section)
        if expect_diag:
            self.assertTrue(
                any(d["source"] == section for d in obs["diagnostics"]),
                f"no diagnostic for {section}",
            )
        self.assertIsInstance(render_observation(obs), str)

    def test_each_source_degrades_gracefully(self):
        for rel, section in self.FILES:
            with tempfile.TemporaryDirectory() as td:
                repo = populate_herd(make_git_repo(td))
                target = repo / rel

                target.unlink()
                if section == "children":
                    self.check(repo, section, "empty", expect_diag=False)
                else:
                    self.check(repo, section, "missing")

                target.write_text("{not json")
                self.check(repo, section, "malformed")

                for payload in ("[]", "null", '"just a string"', "3"):
                    target.write_text(payload)
                    self.check(repo, section, "malformed")

                if os.geteuid() != 0:
                    target.write_text("{}")
                    target.chmod(0)
                    try:
                        self.check(repo, section, "unreadable")
                    finally:
                        target.chmod(0o644)

                target.unlink()
                target.mkdir()
                self.check(repo, section, "unreadable")
                target.rmdir()

                target.write_text("x" * (_OBSERVE_MAX_FILE_BYTES + 1))
                self.check(repo, section, "unreadable")

    def test_degraded_cli_still_exits_zero(self):
        with tempfile.TemporaryDirectory() as td:
            repo = populate_herd(make_git_repo(td))
            (repo / ".herd" / "state" / "task.json").write_text("{broken")
            with patch.object(obs_mod, "agent_info", fake_agent_info()):
                buf = io.StringIO()
                with contextlib.redirect_stdout(buf):
                    herdctl.observe(argparse.Namespace(repo=str(repo), json=False))
            self.assertIn("Herd observation", buf.getvalue())


class UninitializedRepoTests(unittest.TestCase):
    def test_bare_directory_observation(self):
        with tempfile.TemporaryDirectory() as td:
            bare = Path(td) / "bare"
            bare.mkdir()
            obs = observe(bare)
            self.assertEqual(list(obs.keys()), TOP_KEYS)
            self.assertEqual(obs["repository"]["is_git_repo"], False)
            self.assertIsNone(obs["repository"]["branch"])
            self.assertIsInstance(render_observation(obs), str)

    def test_bare_directory_cli_exit_zero(self):
        with tempfile.TemporaryDirectory() as td:
            bare = Path(td) / "bare"
            bare.mkdir()
            home = Path(td) / "home"
            home.mkdir()
            p = run_cli(["observe"], cwd=bare, env=minimal_env(home))
            self.assertEqual(p.returncode, 0, p.stderr)
            self.assertNotIn("Traceback", p.stderr)
            self.assertIn("Herd observation", p.stdout)


class AllSpawnRecordProjectionTests(unittest.TestCase):
    def assert_projection_shape(self, projection):
        self.assertEqual(
            set(projection),
            {"state", "count", "truncated", "listed", "detail"},
        )
        self.assertIn(projection["state"], STATE_VOCAB)
        self.assertIsInstance(projection["truncated"], bool)
        self.assertIsInstance(projection["listed"], list)

    def test_no_children_file_is_clean_zero(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_git_repo(td)
            projection = observe_spawn_records(repo)
            self.assert_projection_shape(projection)
            self.assertEqual(projection["state"], "empty")
            self.assertEqual(projection["count"], 0)
            self.assertFalse(projection["truncated"])
            self.assertEqual(projection["listed"], [])
            self.assertIsNone(projection["detail"])

    def test_projects_clean_outer_spawn_without_current_task(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_git_repo(td)
            state = repo / ".herd" / "state"
            record = {
                "requested_at": 1,
                "parent_repo": str(repo),
                "parent_task_id": None,
                "dependency": False,
                "repo": "/managed/lease-realpath",
                "task_id": "target-task-exact",
                "task_status": "ACTIVE",
                "workspace_id": "w",
                "agents": {},
            }
            write_json(state / "children.json", {
                "version": 1, "children": [record],
            })
            projection = observe_spawn_records(repo)
            self.assert_projection_shape(projection)
            self.assertEqual(projection["state"], "available")
            self.assertEqual(projection["count"], 1)
            # Task 8 ownership correction (cause 3): the recorded workspace
            # id is carried exactly; the EMPTY agent mapping is not a usable
            # agent set and projects as None.
            self.assertEqual(projection["listed"], [{
                "parent_task_id": None,
                "dependency": False,
                "repo": "/managed/lease-realpath",
                "task_id": "target-task-exact",
                "recorded_status": "ACTIVE",
                "role": None,
                "workspace_id": "w",
                "agents": None,
            }])
            # Canonical observe remains current-task-correlated: no
            # current task means zero children, even though the narrow
            # all-spawn-record projection sees the persisted outer spawn.
            canonical = observe(repo, probe_agents=False)
            self.assertEqual(list(canonical), TOP_KEYS)
            self.assertEqual(canonical["children"]["state"], "empty")
            self.assertEqual(canonical["children"]["count"], 0)
            self.assertEqual(canonical["children"]["listed"], [])

    def spawn_record(self, **overrides):
        """The record ``HerdrControlPlane.spawn_child`` writes, key for key."""
        record = {
            "requested_at": 1,
            "parent_repo": "/control",
            "parent_task_id": None,
            "dependency": False,
            "repo": "/managed/lease-realpath",
            "task_id": "target-task-exact",
            "task_status": "ACTIVE",
            "workspace_id": "ws-started-1",
            "agents": {"supervisor": "sup-1", "lead": "lead-1", "pod": "pod-1"},
        }
        record.update(overrides)
        return record

    def project(self, *records):
        with tempfile.TemporaryDirectory() as td:
            repo = make_git_repo(td)
            write_json(repo / ".herd" / "state" / "children.json", {
                "version": 1, "children": list(records),
            })
            return observe_spawn_records(repo)

    def test_the_spawned_workspace_and_agents_are_projected_exactly(self):
        """Task 8 ownership correction (cause 3): the ordinary spawn's child
        record names its runtime workspace and agents, and the release's
        child-record proof reads both — the projection carries them exactly,
        so an ordinary spawn is provable rather than degraded."""
        projection = self.project(self.spawn_record())
        self.assertEqual(projection["state"], "available")
        (listed,) = projection["listed"]
        self.assertEqual(
            (listed.get("workspace_id"), listed.get("agents")),
            ("ws-started-1", {"supervisor": "sup-1", "lead": "lead-1", "pod": "pod-1"}))

    def test_an_unusable_workspace_identity_projects_as_none_never_truncated(self):
        """Fail closed per FIELD: an identity that is absent, not a string,
        empty or beyond the string bound, or an agent mapping that is absent,
        empty, oversized or holds any non-exact entry, projects as None — a
        truncated id or a partial agent set would describe another workspace.
        The record itself (its repo and task) stays projected and the
        projection stays ``available``: the proof then reports no workspace
        id / no usable agents, never a match."""
        long_id = "w" * (_OBSERVE_MAX_STRING + 1)
        too_many = dict(("r%d" % i, "a%d" % i)
                        for i in range(_OBSERVE_MAX_LISTED_AGENTS + 1))
        cases = (
            ({"workspace_id": None}, "workspace_id"),
            ({"workspace_id": 7}, "workspace_id"),
            ({"workspace_id": ""}, "workspace_id"),
            ({"workspace_id": long_id}, "workspace_id"),
            ({"agents": None}, "agents"),
            ({"agents": {}}, "agents"),
            ({"agents": ["sup-1", "lead-1"]}, "agents"),
            ({"agents": too_many}, "agents"),
            ({"agents": {"supervisor": "sup-1", "lead": 3}}, "agents"),
            ({"agents": {"supervisor": "sup-1", "lead": ""}}, "agents"),
            ({"agents": {"supervisor": "sup-1", "lead": long_id}}, "agents"),
        )
        for overrides, field in cases:
            with self.subTest(overrides=repr(overrides)[:80]):
                projection = self.project(self.spawn_record(**overrides))
                self.assertEqual(projection["state"], "available")
                (listed,) = projection["listed"]
                self.assertIsNone(listed[field])
                other = "agents" if field == "workspace_id" else "workspace_id"
                self.assertIsNotNone(listed[other])
                self.assertEqual((listed["repo"], listed["task_id"]),
                                 ("/managed/lease-realpath", "target-task-exact"))
        # A record without either key (a pre-correction writer) projects
        # both as None; it is still listed, not malformed.
        legacy = self.spawn_record()
        del legacy["workspace_id"], legacy["agents"]
        (listed,) = self.project(legacy)["listed"]
        self.assertEqual((listed["workspace_id"], listed["agents"]), (None, None))
        # Exactly at the bounds, both are carried.
        at_bound = dict(("r%d" % i, "a" * _OBSERVE_MAX_STRING)
                        for i in range(_OBSERVE_MAX_LISTED_AGENTS))
        (listed,) = self.project(self.spawn_record(
            workspace_id="w" * _OBSERVE_MAX_STRING, agents=at_bound))["listed"]
        self.assertEqual((listed["workspace_id"], listed["agents"]),
                         ("w" * _OBSERVE_MAX_STRING, at_bound))

    def test_malformed_json_object_list_and_record_fail_closed(self):
        payloads = (
            ("{not json", "malformed"),
            (json.dumps([]), "malformed"),
            (json.dumps({"children": {}}), "malformed"),
            (json.dumps({"children": ["not-an-object"]}), "malformed"),
            (json.dumps({"children": [{
                "parent_task_id": None, "dependency": False,
                "repo": "/child", "task_id": None,
            }]}), "malformed"),
        )
        for payload, expected in payloads:
            with self.subTest(payload=payload):
                with tempfile.TemporaryDirectory() as td:
                    repo = make_git_repo(td)
                    path = repo / ".herd" / "state" / "children.json"
                    path.parent.mkdir(parents=True)
                    path.write_text(payload)
                    projection = observe_spawn_records(repo)
                    self.assert_projection_shape(projection)
                    self.assertEqual(projection["state"], expected)
                    self.assertIsNotNone(projection["detail"])

    def test_hard_file_size_bound_reports_unreadable(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_git_repo(td)
            path = repo / ".herd" / "state" / "children.json"
            path.parent.mkdir(parents=True)
            path.write_text("x" * (_OBSERVE_MAX_FILE_BYTES + 1))
            projection = observe_spawn_records(repo)
            self.assertEqual(projection["state"], "unreadable")
            self.assertIsNone(projection["count"])
            self.assertEqual(projection["listed"], [])
            self.assertIn("observation limit", projection["detail"])

    def test_exact_count_beyond_cap_discloses_truncation(self):
        with tempfile.TemporaryDirectory() as td:
            repo = make_git_repo(td)
            total = _OBSERVE_MAX_CHILDREN + 7
            write_json(
                repo / ".herd" / "state" / "children.json",
                {"children": [{
                    "parent_task_id": None,
                    "dependency": False,
                    "repo": "/tmp/child-%d" % index,
                    "task_id": "task-%d" % index,
                    "task_status": "ACTIVE",
                } for index in range(total)]},
            )
            projection = observe_spawn_records(repo)
            self.assertEqual(projection["state"], "available")
            self.assertEqual(projection["count"], total)
            self.assertEqual(
                len(projection["listed"]), _OBSERVE_MAX_CHILDREN
            )
            self.assertTrue(projection["truncated"])
            self.assertIn(
                "truncated to %d of %d"
                % (_OBSERVE_MAX_CHILDREN, total),
                projection["detail"],
            )


class ScopedSpawnRecordProjectionTests(unittest.TestCase):
    """Task 8 cap correction: ``observe_spawn_records(repo, relevant=…)``
    classifies EVERY record and bounds only the RELEVANT set, so unrelated
    history can neither hide a relevant record nor truncate it."""

    LEASE = "/managed/workspaces/wf-m-lease"

    def record(self, repo, task_id, n=0, **overrides):
        """The record ``HerdrControlPlane.spawn_child`` writes, key for key."""
        record = {
            "requested_at": 1000 + n, "parent_repo": "/control",
            "parent_task_id": None, "dependency": False, "repo": repo,
            "task_id": task_id, "task_status": "ACTIVE",
            "workspace_id": "ws-%d" % n,
            "agents": {"supervisor": "sup-%d" % n, "lead": "lead-%d" % n},
        }
        record.update(overrides)
        return record

    def unrelated(self, count, start=0):
        return [self.record("/managed/workspaces/wf-old-%03d" % n,
                            "20260901-0000%02d-%06x" % (n % 60, n), n)
                for n in range(start, start + count)]

    def project(self, records, relevant):
        with tempfile.TemporaryDirectory() as td:
            repo = make_git_repo(td)
            write_json(repo / ".herd" / "state" / "children.json",
                       {"version": 1, "children": list(records)})
            return observe_spawn_records(repo), observe_spawn_records(repo, relevant=relevant)

    def names_lease(self, calls=None):
        def rule(repo):
            if calls is not None:
                calls.append(repo)
            return repo == self.LEASE
        return rule

    def listed(self, record):
        return {"parent_task_id": None, "dependency": False, "repo": record["repo"],
                "task_id": record["task_id"], "recorded_status": "ACTIVE", "role": None,
                "workspace_id": record["workspace_id"], "agents": record["agents"]}

    def test_relevant_records_beyond_an_unrelated_prefix_are_listed_completely(self):
        first = self.record(self.LEASE, "20260924-133512-aaaaaa", 100)
        # A differently-tasked record of the SAME lease stays relevant: the
        # scope is the lease, never the task.
        second = self.record(self.LEASE, "20260924-140000-bbbbbb", 101)
        records = self.unrelated(40) + [first, second] + self.unrelated(3, start=40)
        calls = []
        unscoped, scoped = self.project(records, self.names_lease(calls))
        # The default projection is unchanged: global count, truncated at
        # 32 in file order — neither relevant record is visible there.
        self.assertEqual((unscoped["state"], unscoped["count"], unscoped["truncated"],
                          len(unscoped["listed"])), ("available", 45, True, 32))
        self.assertNotIn(self.LEASE, [r["repo"] for r in unscoped["listed"]])
        self.assertNotIn("scope", unscoped)
        # Scoped: every record classified once, by its repo STRING, in order;
        # the relevant set complete and exact.
        self.assertEqual(calls, [r["repo"] for r in records])
        self.assertEqual(scoped, {
            "state": "available", "count": 2, "truncated": False,
            "listed": [self.listed(first), self.listed(second)],
            "detail": None, "scope": {"records": 45}})

    def test_a_genuinely_over_bound_relevant_set_is_truncated(self):
        relevant = [self.record(self.LEASE, "20260924-1400%02d-cccccc" % n, 200 + n)
                    for n in range(_OBSERVE_MAX_CHILDREN + 1)]
        records = self.unrelated(10) + relevant + self.unrelated(7, start=10)
        _unscoped, scoped = self.project(records, self.names_lease())
        self.assertEqual((scoped["state"], scoped["count"], scoped["truncated"]),
                         ("available", _OBSERVE_MAX_CHILDREN + 1, True))
        self.assertEqual(scoped["listed"], [self.listed(r)
                                            for r in relevant[:_OBSERVE_MAX_CHILDREN]])
        self.assertEqual(scoped["detail"],
                         "relevant spawn records truncated to %d of %d (%d records in"
                         " the file)" % (_OBSERVE_MAX_CHILDREN, _OBSERVE_MAX_CHILDREN + 1,
                                         len(records)))
        # Exactly at the bound: complete, not truncated.
        _unscoped, scoped = self.project(
            self.unrelated(40) + relevant[:_OBSERVE_MAX_CHILDREN], self.names_lease())
        self.assertEqual((scoped["count"], scoped["truncated"], len(scoped["listed"])),
                         (_OBSERVE_MAX_CHILDREN, False, _OBSERVE_MAX_CHILDREN))

    def test_undecidable_relevance_fails_closed_wherever_it_sits(self):
        """A record whose relevance cannot be decided might be relevant: it
        is never passed over as unrelated — the projection is malformed, with
        no count and no listing."""
        def raises_on_nul(repo):
            if "\0" in repo:
                raise ValueError("embedded null byte")
            return False

        cases = (
            ("not an object", ["not-a-record"], self.names_lease(),
             "child record 40 is not a JSON object"),
            ("no repo", [self.record(None, "t", 1)], self.names_lease(),
             "child record 40 names no repository, so its relevance cannot be decided"),
            ("blank repo", [self.record("  ", "t", 1)], self.names_lease(),
             "child record 40 names no repository, so its relevance cannot be decided"),
            ("the rule raises", [self.record("/x\0y", "t", 1)], raises_on_nul,
             "child record 40: its relevance could not be decided (ValueError)"),
            ("the rule answers neither yes nor no", [self.record("/elsewhere", "t", 1)],
             lambda repo: None if repo == "/elsewhere" else False,
             "child record 40: its relevance could not be decided (NoneType)"),
        )
        for label, odd, rule, detail in cases:
            with self.subTest(label):
                _unscoped, scoped = self.project(self.unrelated(40) + odd, rule)
                self.assertEqual(scoped, {"state": "malformed", "count": None,
                                          "truncated": False, "listed": [],
                                          "detail": detail})

    def test_a_malformed_relevant_record_fails_closed_an_unrelated_one_does_not(self):
        good = self.record(self.LEASE, "20260924-133512-aaaaaa", 100)
        broken_unrelated = self.record("/managed/workspaces/wf-old-x", None, 5,
                                       dependency="no")
        _unscoped, scoped = self.project([broken_unrelated, good], self.names_lease())
        self.assertEqual((scoped["state"], scoped["count"], scoped["listed"]),
                         ("available", 1, [self.listed(good)]))
        broken_relevant = self.record(self.LEASE, None, 6)
        _unscoped, scoped = self.project([good, broken_relevant], self.names_lease())
        self.assertEqual(scoped, {"state": "malformed", "count": None, "truncated": False,
                                  "listed": [], "detail":
                                  "child record 1 has malformed identity fields"})

    def test_no_relevant_record_is_a_clean_empty_scope(self):
        _unscoped, scoped = self.project(self.unrelated(50), self.names_lease())
        self.assertEqual(scoped, {"state": "empty", "count": 0, "truncated": False,
                                  "listed": [], "detail": None, "scope": {"records": 50}})


class IncrementalScopedReadTests(unittest.TestCase):
    """Task 8 input-size correction: a SCOPED read of ``children.json`` is
    incremental and bounded PER VALUE (``_SpawnRecordStream``), so a valid
    UNRELATED prefix that pushes the file past ``_OBSERVE_MAX_FILE_BYTES``
    no longer makes a small RELEVANT set unobservable; the UNSCOPED read
    keeps the whole-file bound and its refusal exactly. Files are written in
    the writer's own format (``json.dumps(…, indent=2) + "\\n"``)."""

    LEASE = ScopedSpawnRecordProjectionTests.LEASE
    record = ScopedSpawnRecordProjectionTests.record
    unrelated = ScopedSpawnRecordProjectionTests.unrelated
    names_lease = ScopedSpawnRecordProjectionTests.names_lease
    listed = ScopedSpawnRecordProjectionTests.listed

    def setUp(self):
        self.base = Path(tempfile.mkdtemp(prefix="is-observe-"))
        self.addCleanup(shutil.rmtree, str(self.base), True)
        self.path = self.base / ".herd" / "state" / "children.json"
        self.path.parent.mkdir(parents=True)

    @staticmethod
    def text(records, **document):
        document = dict({"version": 1}, **document)
        document["children"] = list(records)
        return json.dumps(document, indent=2) + "\n"

    def write(self, records=None, raw=None):
        data = raw if raw is not None else self.text(records).encode("ascii")
        self.path.write_bytes(data)
        return len(data)

    def relevant_pair(self):
        return [self.record(self.LEASE, "20260924-133512-aaaaaa", 9001),
                self.record(self.LEASE, "20260924-140000-bbbbbb", 9002)]

    def past_the_bound(self, relevant, before=5, over=64 * 1024):
        """Records whose file is ``over`` bytes past the bound: the relevant
        ones placed after ``before`` unrelated records and after the bulk."""
        head = self.unrelated(before)
        count = 0
        records = head + [relevant[0]]
        while True:
            count += 500
            records = head + [relevant[0]] + self.unrelated(count, start=before) + relevant[1:]
            if len(self.text(records)) > _OBSERVE_MAX_FILE_BYTES + over:
                return records

    def sized(self, relevant, size):
        """Records whose writer-format text is EXACTLY ``size`` bytes: the
        bulk padded through an UNRELATED record's extra field."""
        bulk = self.unrelated(2)
        while len(self.text(bulk + relevant)) < size - 4096:
            bulk = bulk + self.unrelated(200, start=len(bulk))
        while len(self.text(bulk + relevant)) > size - 64:
            bulk = bulk[:-1]
        bulk[0] = dict(bulk[0], pad="")
        missing = size - len(self.text(bulk + relevant))
        bulk[0]["pad"] = "x" * missing
        records = bulk + relevant
        self.assertEqual(len(self.text(records)), size)
        return records

    def scoped(self):
        return observe_spawn_records(self.base, relevant=self.names_lease())

    def expected(self, relevant, total):
        return {"state": "available", "count": len(relevant), "truncated": False,
                "listed": [self.listed(r) for r in relevant], "detail": None,
                "scope": {"records": total}}

    # -- the positive past the bound, and the unscoped read unchanged -------------

    def test_IS1_past_the_bound_the_relevant_set_is_observed_and_unscoped_still_refuses(self):
        relevant = self.relevant_pair()
        records = self.past_the_bound(relevant)
        size = self.write(records)
        self.assertGreater(size, _OBSERVE_MAX_FILE_BYTES)
        # The same-lease record of ANOTHER task stays relevant (the scope is
        # the lease, never the task): both are listed, in file order.
        self.assertEqual(self.scoped(), self.expected(relevant, len(records)))
        unscoped = observe_spawn_records(self.base)
        self.assertEqual(unscoped, {
            "state": "unreadable", "count": None, "truncated": False, "listed": [],
            "detail": "children.json is %d bytes (observation limit %d)"
                      % (size, _OBSERVE_MAX_FILE_BYTES)})

    def test_IS2_below_at_and_above_the_bound_the_relevant_set_is_observed(self):
        relevant = self.relevant_pair()
        for label, size in (("below", _OBSERVE_MAX_FILE_BYTES - 1),
                            ("at", _OBSERVE_MAX_FILE_BYTES),
                            ("above", _OBSERVE_MAX_FILE_BYTES + 1)):
            with self.subTest(label):
                records = self.sized(relevant, size)
                self.assertEqual(self.write(records), size)
                self.assertEqual(self.scoped(), self.expected(relevant, len(records)))
                unscoped = observe_spawn_records(self.base)
                if label == "above":
                    self.assertEqual((unscoped["state"], unscoped["detail"]),
                                     ("unreadable", "children.json is %d bytes (observation"
                                      " limit %d)" % (size, _OBSERVE_MAX_FILE_BYTES)))
                else:
                    self.assertEqual((unscoped["state"], unscoped["count"],
                                      unscoped["truncated"]),
                                     ("available", len(records), True))

    def test_IS7_unrelated_observation_clients_keep_the_whole_file_bound(self):
        """The canonical ``observe`` children section (task-correlated,
        unscoped) keeps today's refusal past the bound too."""
        records = self.past_the_bound(self.relevant_pair())
        size = self.write(records)
        diags = []
        section = obs_mod._children_section(self.base, "some-task", diags)
        self.assertEqual((section["state"], section["count"]), ("unreadable", None))
        self.assertEqual(diags[-1]["detail"], "children.json is %d bytes (observation"
                         " limit %d)" % (size, _OBSERVE_MAX_FILE_BYTES))

    # -- the bounded-resource guarantee ------------------------------------------

    def test_IS3_one_pass_with_memory_independent_of_the_file(self):
        import tracemalloc
        relevant = self.relevant_pair()
        records = self.past_the_bound(relevant, over=7 * 1024 * 1024)
        size = self.write(records)
        self.assertGreater(size, 8 * _OBSERVE_MAX_FILE_BYTES)
        stream, state, detail = obs_mod._SpawnRecordStream.open(self.path)
        try:
            count = sum(1 for _record in stream)
        finally:
            stream.close()
        longest = max(len(json.dumps(r, indent=2)) for r in records) + 64
        # ONE forward pass over every byte; the text held never exceeds one
        # chunk plus the longest value (here small records).
        self.assertEqual((state, count, stream.bytes_read), ("available", len(records), size))
        self.assertLessEqual(stream.peak_chars, obs_mod._OBSERVE_SCAN_CHUNK_BYTES + longest)
        # The REAL projection's peak Python allocation stays far below the
        # file (a whole-file read would hold the text and every record).
        tracemalloc.start()
        try:
            projection = self.scoped()
            _current, peak = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()
        self.assertEqual(projection, self.expected(relevant, len(records)))
        self.assertLess(peak, 2 * _OBSERVE_MAX_FILE_BYTES, peak)

    def test_IS3b_the_per_value_ceiling_holds_for_the_largest_admissible_value(self):
        relevant = self.relevant_pair()
        big = dict(self.unrelated(1, start=77)[0], pad="y" * (_OBSERVE_MAX_FILE_BYTES - 4096))
        records = [big] + relevant + self.unrelated(10)
        size = self.write(records)
        stream, state, _detail = obs_mod._SpawnRecordStream.open(self.path)
        try:
            self.assertEqual(sum(1 for _record in stream), len(records))
        finally:
            stream.close()
        self.assertEqual(stream.bytes_read, size)
        self.assertLessEqual(stream.peak_chars,
                             _OBSERVE_MAX_FILE_BYTES + obs_mod._OBSERVE_SCAN_CHUNK_BYTES)
        self.assertEqual(self.scoped(), self.expected(relevant, len(records)))

    def test_IS4_a_value_over_the_per_value_bound_fails_closed(self):
        relevant = self.relevant_pair()
        big = dict(self.unrelated(1, start=77)[0], pad="y" * (_OBSERVE_MAX_FILE_BYTES + 1))
        self.write(relevant[:1] + [big] + relevant[1:])
        self.assertEqual(self.scoped(), {
            "state": "malformed", "count": None, "truncated": False, "listed": [],
            "detail": "a value in children.json does not complete within %d characters"
                      " (not valid JSON, or longer than the per-value bound); its"
                      " relevance cannot be decided" % _OBSERVE_MAX_FILE_BYTES})

    def test_IS4b_a_completed_value_below_at_and_above_the_per_value_bound(self):
        """A value that COMPLETES is measured too: its text of bound − 1 and
        bound characters is read; bound + 1 is refused (the check runs on
        every successful decode, not only on a value still incomplete)."""
        relevant = self.relevant_pair()
        base = dict(self.unrelated(1, start=88)[0], pad="")
        for label, length, admitted in (("below", _OBSERVE_MAX_FILE_BYTES - 1, True),
                                        ("at", _OBSERVE_MAX_FILE_BYTES, True),
                                        ("above", _OBSERVE_MAX_FILE_BYTES + 1, False)):
            with self.subTest(label):
                big = dict(base, pad="z" * (length - len(json.dumps(base))))
                self.assertEqual(len(json.dumps(big)), length)
                raw = ('{"version": 1, "children": [%s, %s, %s]}\n' % (
                    json.dumps(relevant[0]), json.dumps(big), json.dumps(relevant[1])))
                self.write(raw=raw.encode("ascii"))
                if admitted:
                    self.assertEqual(self.scoped(), self.expected(relevant, 3))
                else:
                    self.assert_refused(
                        "malformed", "a value in children.json does not complete within %d"
                        " characters (not valid JSON, or longer than the per-value bound);"
                        " its relevance cannot be decided" % _OBSERVE_MAX_FILE_BYTES)

    def test_IS5b_a_number_split_at_a_chunk_edge_is_taken_whole(self):
        """The chunk edge placed after EVERY character of a number token
        NESTED IN A RECORD — ``1e|+05``, ``1e+|05``, ``1.5e|-2`` … — parses
        to the one correct value. Scope, stated exactly: the RECORD is the
        value decoded here, so the split is completed by the OBJECT's
        re-decode; the SCALAR completion rule (``_complete`` on a numeric
        value) is exercised by IS5c, not by this test."""
        for number, expected in (("1e+05", 1e5), ("1e-3", 1e-3), ("1E5", 1e5),
                                 ("1.5e-2", 0.015), ("12345", 12345), ("-0.25", -0.25)):
            raw = ('{"version": 1, "children": [{"parent_task_id": null, "dependency":'
                   ' false, "repo": "%s", "task_id": "t-1", "requested_at": %s,'
                   ' "workspace_id": "ws-1", "agents": {"supervisor": "s"}}]}\n'
                   % (self.LEASE, number))
            offset = raw.index(": %s," % number) + 2
            self.write(raw=raw.encode("ascii"))
            for cut in range(1, len(number) + 1):
                with self.subTest(number=number, after=number[:cut]):
                    with patch.object(obs_mod, "_OBSERVE_SCAN_CHUNK_BYTES", offset + cut):
                        stream, state, detail = obs_mod._SpawnRecordStream.open(self.path)
                        try:
                            records = list(stream)
                        finally:
                            stream.close()
                        self.assertEqual((state, [r["requested_at"] for r in records]),
                                         ("available", [expected]))
                        scoped = self.scoped()
                        self.assertEqual((scoped["state"], scoped["count"]), ("available", 1))

    def test_IS5c_a_document_level_scalar_split_at_a_chunk_edge_is_taken_whole(self):
        """The SCALAR branch: a numeric MEMBER VALUE of the document object
        (``"version": 1e+05``) with the chunk edge after every character of
        its token. The old rule (a decode ending before the buffer's end is
        complete) took ``1`` from ``1e``/``1e+``/``1.`` and then rejected the
        rest of the SAME token as punctuation — a valid file refused."""
        relevant = self.relevant_pair()
        for number in ("1e+05", "1e-3", "7E5", "1.5", "-2E-3"):
            raw = '{"children": [%s, %s], "version": %s}\n' % (
                json.dumps(relevant[0]), json.dumps(relevant[1]), number)
            offset = raw.index('"version": ') + len('"version": ')
            self.write(raw=raw.encode("ascii"))
            for cut in range(1, len(number) + 1):
                with self.subTest(number=number, after=number[:cut]):
                    with patch.object(obs_mod, "_OBSERVE_SCAN_CHUNK_BYTES", offset + cut):
                        self.assertEqual(self.scoped(), self.expected(relevant, 2))

    def test_IS5_chunk_boundaries_never_split_a_value(self):
        relevant = self.relevant_pair()
        records = self.unrelated(12) + [relevant[0]] + self.unrelated(5, start=12) + relevant[1:]
        raw = (json.dumps({"children": records, "version": 1234567, "flag": True,
                           "none": None}, indent=2) + "  \n").encode("ascii")
        self.write(raw=raw)
        reference = self.scoped()
        self.assertEqual(reference, self.expected(relevant, len(records)))
        for chunk in (1, 2, 3, 5, 7, 11, 64, 4093):
            with self.subTest(chunk=chunk):
                with patch.object(obs_mod, "_OBSERVE_SCAN_CHUNK_BYTES", chunk):
                    self.assertEqual(self.scoped(), reference)

    # -- fail closed, truthfully, past the bound ------------------------------------

    def assert_refused(self, state, detail):
        self.assertEqual(self.scoped(), {"state": state, "count": None, "truncated": False,
                                         "listed": [], "detail": detail})

    def test_IS6a_a_malformed_relevant_record_past_the_bound(self):
        relevant = self.relevant_pair()
        relevant[1] = dict(relevant[1], task_id=None)
        records = self.past_the_bound(relevant)
        self.write(records)
        self.assert_refused("malformed", "child record %d has malformed identity fields"
                            % (len(records) - 1))

    def test_IS6b_undecidable_relevance_past_the_bound(self):
        for label, odd, detail in (
                ("not an object", "not-a-record", "is not a JSON object"),
                ("no repository", self.record(None, "t", 3),
                 "names no repository, so its relevance cannot be decided")):
            with self.subTest(label):
                records = self.past_the_bound(self.relevant_pair()) + [odd]
                self.write(records)
                self.assert_refused("malformed", "child record %d %s"
                                    % (len(records) - 1, detail))

    def test_IS6c_read_failures_past_the_bound(self):
        records = self.past_the_bound(self.relevant_pair())
        self.write(records)
        with self.subTest("permissions"):
            os.chmod(self.path, 0)
            try:
                self.assert_refused("unreadable", "children.json: PermissionError")
            finally:
                os.chmod(self.path, 0o644)
        with self.subTest("decode"):
            raw = self.text(records).encode("ascii")
            cut = raw.rindex(b"wf-old-")
            self.write(raw=raw[:cut] + b"\xff" + raw[cut + 1:])
            self.assert_refused("unreadable", "children.json could not be decoded")
        with self.subTest("I/O"):
            self.write(records)
            real_open = open

            class Failing:
                def __init__(self, handle):
                    self.handle, self.reads = handle, 0

                def read(self, size):
                    self.reads += 1
                    if self.reads == 5:
                        raise OSError("device error")
                    return self.handle.read(size)

                def close(self):
                    self.handle.close()

            with patch.object(obs_mod, "open", lambda *a, **k: Failing(real_open(*a, **k)),
                              create=True):
                self.assert_refused("unreadable", "children.json: OSError")
        with self.subTest("a directory"):
            self.path.unlink()
            self.path.mkdir()
            self.assert_refused("unreadable", "children.json is a directory")
            self.path.rmdir()

    def test_IS6d_a_truncated_file_past_the_bound(self):
        raw = self.text(self.past_the_bound(self.relevant_pair())).encode("ascii")
        self.write(raw=raw[:int(len(raw) * 0.9)])
        self.assert_refused("malformed", "children.json is not valid JSON")

    def test_IS4c_refusing_an_over_long_value_never_holds_more_than_the_ceiling(self):
        """A 3 MiB value is refused BEFORE its text is read past the bound:
        the buffer never exceeds one bound plus one chunk."""
        huge = dict(self.unrelated(1, start=99)[0], pad="w" * (3 * _OBSERVE_MAX_FILE_BYTES))
        self.write([huge] + self.relevant_pair())
        stream, state, _detail = obs_mod._SpawnRecordStream.open(self.path)
        try:
            with self.assertRaises(obs_mod._SpawnRecordRefused) as refused:
                list(stream)
        finally:
            stream.close()
        self.assertEqual((state, refused.exception.state), ("available", "malformed"))
        self.assertLessEqual(stream.peak_chars,
                             _OBSERVE_MAX_FILE_BYTES + obs_mod._OBSERVE_SCAN_CHUNK_BYTES)
        self.assertLess(stream.bytes_read, 2 * _OBSERVE_MAX_FILE_BYTES)

    def test_IS6h_a_file_also_not_valid_json_reports_that_first(self):
        """Parse-first order, as the whole-file read: a malformed RELEVANT
        record early in a file that is ALSO cut off reports the file."""
        relevant = self.relevant_pair()
        relevant[0] = dict(relevant[0], task_id=None)
        raw = self.text(relevant + self.unrelated(30)).encode("ascii")
        self.write(raw=raw[:-40])
        self.assert_refused("malformed", "children.json is not valid JSON")
        self.write(raw=raw)
        self.assert_refused("malformed", "child record 0 has malformed identity fields")

    def test_IS8_the_file_is_closed_on_every_path(self):
        import gc
        import warnings
        cases = {
            "available": self.text(self.relevant_pair() + self.unrelated(5)),
            "refused mid-scan": self.text(self.relevant_pair())[:-9],
            "refused at open": "[]",
            "malformed record": self.text([dict(self.relevant_pair()[0], task_id=None)]),
        }
        for label, text in sorted(cases.items()):
            with self.subTest(label):
                self.write(raw=text.encode("ascii"))
                with warnings.catch_warnings(record=True) as caught:
                    warnings.simplefilter("always", ResourceWarning)
                    self.scoped()
                    gc.collect()
                self.assertEqual([w for w in caught if issubclass(w.category, ResourceWarning)],
                                 [])

    def test_IS9_differential_cases_project_identically_through_both_readers(self):
        """DIFFERENTIAL CASES (finite — evidence over these shapes, not a
        proof over every file): the scoped projection through the incremental
        reader equals the projection through the ORIGINAL whole-file read
        (the same function with the reader swapped for ``_read_json_object``)
        for each named case below, at the default chunk and at a tiny one.
        The readers are NOT identical everywhere: the incremental read is
        stricter on a duplicate ``children`` member (refused; ``json.loads``
        keeps the last) and on undecodable bytes (refused; the whole-file
        read replaces them) — IS6f and IS6c pin those divergences — and it
        reads files past the whole-file bound (IS1, IS2)."""
        class WholeFile(list):
            @classmethod
            def open(cls, path):
                data, state, detail = obs_mod._read_json_object(path)
                if state != "available":
                    return None, state, detail
                records = data.get("children")
                if not isinstance(records, list):
                    return None, "malformed", "`children` in children.json is not a list"
                return cls(records), "available", None

            def drain(self):
                pass

            def close(self):
                pass

        relevant = self.relevant_pair()
        over = [self.record(self.LEASE, "20260924-1400%02d-cccccc" % n, 300 + n)
                for n in range(_OBSERVE_MAX_CHILDREN + 1)]
        corpus = {
            "relevant beyond a prefix": self.unrelated(40) + relevant + self.unrelated(3, 40),
            "no relevant record": self.unrelated(50),
            "an over-bound relevant set": self.unrelated(10) + over,
            "exactly the listing bound": over[:_OBSERVE_MAX_CHILDREN],
            "a malformed unrelated record": [dict(self.unrelated(1)[0], dependency="x")]
                                            + relevant,
            "a malformed relevant record": relevant[:1] + [dict(relevant[1], task_id=None)],
            "an overlong relevant field": [dict(relevant[0], task_id="t" * 300)],
            "not an object": self.unrelated(3) + ["x"],
            "no repository": self.unrelated(3) + [self.record(None, "t", 1)],
            "empty": [],
        }
        texts = dict((label, self.text(records)) for label, records in corpus.items())
        texts.update({"not a list": json.dumps({"children": {"a": 1}}),
                      "no children": json.dumps({"version": 1}),
                      "a top-level array": json.dumps(relevant),
                      "cut off": self.text(relevant)[:-5],
                      "an empty file": ""})
        for label, text in sorted(texts.items()):
            self.write(raw=text.encode("ascii"))
            with patch.object(obs_mod, "_SpawnRecordStream", WholeFile):
                reference = self.scoped()
            for chunk in (obs_mod._OBSERVE_SCAN_CHUNK_BYTES, 7):
                with self.subTest(label, chunk=chunk):
                    with patch.object(obs_mod, "_OBSERVE_SCAN_CHUNK_BYTES", chunk):
                        self.assertEqual(self.scoped(), reference)

    def test_IS6g_a_number_truncated_at_the_end_of_the_file_is_malformed(self):
        """A number or exponent cut off by the END OF THE FILE at a NONZERO
        offset (the read that meets the end compacts the buffer — the
        offsets are rebased): a truthful ``malformed`` refusal, never a
        cursor artefact. At top level, ``  1e`` is not valid JSON at all —
        not "a document that is not an object"."""
        relevant = self.relevant_pair()
        body = '{"children": [%s, %s], "version": ' % (
            json.dumps(relevant[0]), json.dumps(relevant[1]))
        for label, raw in (("an exponent in the object", body + "1e"),
                           ("a signed exponent in the object", body + "1e+"),
                           ("a whole number in the object", body + "12"),
                           ("a decimal point in the object", body + "3."),
                           ("an exponent at top level", "  1e"),
                           ("a signed exponent at top level", "\n\n  25E-")):
            with self.subTest(label):
                self.assertGreater(raw.index(raw.strip()[-2:]), 0)
                self.write(raw=raw.encode("ascii"))
                self.assert_refused("malformed", "children.json is not valid JSON")

    def test_IS6e_an_over_bound_relevant_set_past_the_bound_is_truncated(self):
        relevant = [self.record(self.LEASE, "20260924-1400%02d-cccccc" % n, 200 + n)
                    for n in range(_OBSERVE_MAX_CHILDREN + 1)]
        records = self.past_the_bound(relevant[:1]) + relevant[1:]
        self.write(records)
        self.assertEqual(self.scoped(), {
            "state": "available", "count": _OBSERVE_MAX_CHILDREN + 1, "truncated": True,
            "listed": [self.listed(r) for r in relevant[:_OBSERVE_MAX_CHILDREN]],
            "detail": "relevant spawn records truncated to %d of %d (%d records in the"
                      " file)" % (_OBSERVE_MAX_CHILDREN, _OBSERVE_MAX_CHILDREN + 1,
                                  len(records)),
            "scope": {"records": len(records)}})

    def test_IS6f_the_document_shape_is_strict(self):
        relevant = self.relevant_pair()
        body = self.text(relevant)
        cases = (
            ("a duplicate children member",
             body.rstrip()[:-1] + ', "children": []}\n',
             "children.json names `children` more than once"),
            ("trailing content", body + "x", "children.json is not valid JSON"),
            ("not an object", json.dumps(relevant), "children.json does not contain a JSON object"),
            ("children not a list", json.dumps({"children": {"a": 1}}),
             "`children` in children.json is not a list"),
            ("no children", json.dumps({"version": 1}), "`children` in children.json is not a list"),
            ("an empty file", "", "children.json is not valid JSON"),
        )
        for label, text, detail in cases:
            with self.subTest(label):
                self.write(raw=text.encode("ascii"))
                self.assert_refused("malformed", detail)
        self.path.unlink()
        self.assertEqual(self.scoped(), {"state": "empty", "count": 0, "truncated": False,
                                         "listed": [], "detail": None})


class CorrelationTests(unittest.TestCase):
    def test_reviews_children_and_freshness_correlate(self):
        with tempfile.TemporaryDirectory() as td:
            repo = populate_herd(make_git_repo(td))
            state = repo / ".herd" / "state"
            reviews = state / "reviews"
            (reviews / f"{TASK_ID}-round-01.md").write_text("...\nHERD_DECISION: REJECT\n")
            (reviews / f"{TASK_ID}-round-02.md").write_text("...\nHERD_DECISION: APPROVE\n")
            (reviews / f"{TASK_ID}-round-03.md").write_text("...\nHERD_DECISION: ACCEPT\n")
            (reviews / f"{TASK_ID}-round-04.md").write_text("...\nLGTM\n")
            (reviews / "othertask-round-01.md").write_text("HERD_DECISION: APPROVE\n")
            write_json(state / "children.json", {"children": [
                {"parent_task_id": TASK_ID, "repo": "/tmp/c1", "task_id": "c1",
                 "task_status": "ACTIVE", "role": "child"},
                {"parent_task_id": TASK_ID, "repo": "/tmp/c2", "task_id": "c2",
                 "task_status": "COMPLETE", "role": "child"},
                {"parent_task_id": "someone-else", "repo": "/tmp/c3",
                 "task_id": "c3", "task_status": "ACTIVE", "role": "child"},
            ]})
            old = 1_000_000_000
            (state / "supervisor-status.md").write_text("old\n")
            os.utime(state / "supervisor-status.md", (old, old))
            obs = observe(repo, probe_agents=False)

            reviews_section = obs["reviews"]
            self.assertEqual(reviews_section["task_id"], TASK_ID)
            self.assertEqual(reviews_section["rounds"], 4)
            # total_files counts THIS task's round files only, as the field
            # name promises (othertask-round-01.md is excluded).
            self.assertEqual(reviews_section["total_files"], 4)
            by_round = {e["round"]: e for e in reviews_section["listed"]}
            self.assertEqual(sorted(by_round), [1, 2, 3, 4])
            self.assertEqual(by_round[1]["decision"], "REJECT")
            self.assertEqual(by_round[2]["decision"], "APPROVE")
            self.assertIsNone(by_round[3]["decision"])
            self.assertIsNone(by_round[4]["decision"])
            for entry in reviews_section["listed"]:
                self.assertEqual(
                    set(entry.keys()), {"file", "round", "decision", "size", "mtime"},
                )
                self.assertNotIn("othertask", entry["file"])

            children = obs["children"]
            self.assertEqual(children["parent_task_id"], TASK_ID)
            self.assertEqual(children["count"], 2)
            self.assertEqual(
                {e["task_id"] for e in children["listed"]}, {"c1", "c2"},
            )
            for entry in children["listed"]:
                self.assertEqual(
                    set(entry.keys()), {"repo", "task_id", "recorded_status", "role"},
                )

            artifacts = {e["name"]: e for e in obs["artifacts"]["listed"]}
            self.assertEqual(artifacts["task-checkpoint.md"]["freshness"], "fresh")
            self.assertEqual(artifacts["supervisor-status.md"]["freshness"], "stale")
            self.assertFalse(artifacts["mission.json"]["present"] is None)


class ReviewDecisionHeaderTests(unittest.TestCase):
    """Round-2b operator finding: real persisted review artifacts carry the
    decision in a `Protocol token:` header, and the embedded pane-captured
    transcript line-wraps the HERD_DECISION token across two lines. The
    header must be parsed exactly (APPROVE/REJECT only); the contiguous
    transcript token stays as a fallback only; nothing else resolves."""

    @staticmethod
    def artifact(header_token, transcript):
        return (
            "# Reviewer round\n\n"
            "Reviewer: `reviewer1` / `h-t-rev1`\n\n"
            f"Protocol token: `{header_token}`\n\n"
            "## Transcript\n\n" + transcript
        )

    def test_header_is_authoritative_and_fallback_is_exact_only(self):
        with tempfile.TemporaryDirectory() as td:
            repo = populate_herd(make_git_repo(td))
            reviews = repo / ".herd" / "state" / "reviews"
            wrapped_reject = "  findings...\n\n  HERD_DECISION:\n  REJECT\n"
            wrapped_approve = "  looks good\n\n  HERD_DECISION:\n  APPROVE\n"
            # Real artifact shape: header + line-wrapped token in transcript.
            (reviews / f"{TASK_ID}-round-01.md").write_text(
                self.artifact("REJECT", wrapped_reject)
            )
            (reviews / f"{TASK_ID}-round-02.md").write_text(
                self.artifact("APPROVE", wrapped_approve)
            )
            # Wrapped token with NO usable header must still yield null.
            (reviews / f"{TASK_ID}-round-03.md").write_text(
                self.artifact("MISSING", wrapped_approve)
            )
            # Non-canonical header token is never accepted.
            (reviews / f"{TASK_ID}-round-04.md").write_text(
                self.artifact("ACCEPT", "  HERD_DECISION: ACCEPT\n")
            )
            # A header-shaped line INSIDE the transcript body is not a
            # header — the spoof line is column-0/unindented on purpose, so
            # only the pre-transcript region guard can reject it.
            (reviews / f"{TASK_ID}-round-05.md").write_text(
                self.artifact("MISSING", "Protocol token: `APPROVE`\n")
            )
            # A PRESENT header is authoritative: a recorded MISSING must
            # not fall through to a CONTIGUOUS token in reviewer prose
            # (which would invent a decision contradicting the record).
            (reviews / f"{TASK_ID}-round-06.md").write_text(
                self.artifact(
                    "MISSING",
                    "  The protocol requires ending with "
                    "HERD_DECISION: APPROVE or\n  HERD_DECISION: REJECT "
                    "exactly.\n",
                )
            )
            # A valid header also beats a contradicting contiguous token.
            (reviews / f"{TASK_ID}-round-07.md").write_text(
                self.artifact("REJECT", "  HERD_DECISION: APPROVE\n")
            )
            # No `## Transcript` marker at all: header-shaped lines are
            # never honoured outside a canonical preamble.
            (reviews / f"{TASK_ID}-round-08.md").write_text(
                "# Reviewer round 8\n\nsome prose\n\n"
                "Protocol token: `APPROVE`\n\nmore prose\n"
            )
            # Canonical marker present but NO header line in the preamble:
            # a column-0 header-shaped line in the body must not be treated
            # as the header (only the pre-transcript region is searched).
            (reviews / f"{TASK_ID}-round-09.md").write_text(
                "# Reviewer round 9\n\nReviewer: `r`\n\n## Transcript\n\n"
                "Protocol token: `APPROVE`\n"
            )
            # A malformed (indented) preamble header is authoritative-but-
            # invalid: it yields null AND suppresses the fallback, so a
            # contiguous body token cannot decide over an unparseable record.
            (reviews / f"{TASK_ID}-round-10.md").write_text(
                "# Reviewer round 10\n\n  Protocol token: `REJECT`\n\n"
                "## Transcript\n\n  HERD_DECISION: APPROVE\n"
            )
            # Mid-line prose mentioning "Protocol token:" is NOT a header
            # line: it must not suppress the fallback, so the contiguous
            # body token RESOLVES (match-precision guard — reviewer MHDR8).
            (reviews / f"{TASK_ID}-round-11.md").write_text(
                "# Reviewer round 11\n\n"
                "The Protocol token: field is required.\n\n"
                "## Transcript\n\n  HERD_DECISION: APPROVE\n"
            )
            obs = observe(repo, probe_agents=False)
            by_round = {e["round"]: e["decision"]
                        for e in obs["reviews"]["listed"]}
            self.assertEqual(by_round[1], "REJECT")
            self.assertEqual(by_round[2], "APPROVE")
            self.assertIsNone(by_round[3])
            self.assertIsNone(by_round[4])
            self.assertIsNone(by_round[5])
            self.assertIsNone(by_round[6])
            self.assertEqual(by_round[7], "REJECT")
            self.assertIsNone(by_round[8])
            self.assertIsNone(by_round[9])
            self.assertIsNone(by_round[10])
            self.assertEqual(by_round[11], "APPROVE")


class CLITests(unittest.TestCase):
    def test_json_output_equals_projection(self):
        with tempfile.TemporaryDirectory() as td:
            repo = populate_herd(make_git_repo(td))
            fixed_now = 1787600000.0
            with patch.object(obs_mod, "agent_info", fake_agent_info()), \
                    patch.object(obs_mod.time, "time", lambda: fixed_now):
                # resolve_repo_ref resolves symlinks (macOS /var -> /private/var),
                # so compare against the projection of the same resolved path.
                direct = observe(repo.resolve())
                buf = io.StringIO()
                with contextlib.redirect_stdout(buf):
                    herdctl.observe(argparse.Namespace(repo=str(repo), json=True))
            out = buf.getvalue()
            parsed = json.loads(out)
            self.assertEqual(parsed, direct)
            self.assertEqual(out, json.dumps(direct, indent=2) + "\n")

    def test_unknown_repo_ref_exits_two(self):
        buf = io.StringIO()
        with self.assertRaises(SystemExit) as ctx:
            with contextlib.redirect_stderr(buf):
                herdctl.observe(argparse.Namespace(
                    repo="definitely-not-a-registered-herd-repo-xq7",
                    json=False,
                ))
        self.assertEqual(ctx.exception.code, 2)
        self.assertEqual(len(buf.getvalue().strip().splitlines()), 1)

    def test_unknown_repo_ref_subprocess(self):
        with tempfile.TemporaryDirectory() as td:
            home = Path(td) / "home"
            home.mkdir()
            p = run_cli(
                ["observe", "--repo", "definitely-not-a-registered-herd-repo-xq7"],
                cwd=R, env=minimal_env(home),
            )
            self.assertEqual(p.returncode, 2)
            self.assertNotIn("Traceback", p.stderr)
            self.assertEqual(len(p.stderr.strip().splitlines()), 1)
            self.assertEqual(p.stdout, "")

    def test_json_subprocess_parses(self):
        with tempfile.TemporaryDirectory() as td:
            repo = populate_herd(make_git_repo(td))
            home = Path(td) / "home"
            home.mkdir()
            p = run_cli(
                ["observe", "--repo", str(repo), "--json"],
                cwd=R, env=minimal_env(home),
            )
            self.assertEqual(p.returncode, 0, p.stderr)
            parsed = json.loads(p.stdout)
            self.assertEqual(list(parsed.keys()), TOP_KEYS)
            self.assertEqual(p.stdout, json.dumps(parsed, indent=2) + "\n")

    def test_observe_help_exposes_flags(self):
        with tempfile.TemporaryDirectory() as td:
            home = Path(td) / "home"
            home.mkdir()
            p = run_cli(["observe", "--help"], cwd=R, env=minimal_env(home))
            self.assertEqual(p.returncode, 0)
            self.assertIn("--repo", p.stdout)
            self.assertIn("--json", p.stdout)


class CompatibilityTests(unittest.TestCase):
    def test_existing_subparsers_still_exist(self):
        with tempfile.TemporaryDirectory() as td:
            home = Path(td) / "home"
            home.mkdir()
            p = run_cli(["--help"], cwd=R, env=minimal_env(home))
            self.assertEqual(p.returncode, 0)
            for name in ["status", "health", "doctor", "mission", "task",
                         "review-decision", "approve-commit", "approve-push",
                         "observe"]:
                self.assertIn(name, p.stdout)


class NonMutationTests(unittest.TestCase):
    def snapshot(self, repo):
        digests = {}
        for path in sorted((repo / ".herd").rglob("*")):
            if path.is_file():
                digests[str(path.relative_to(repo))] = hashlib.sha256(
                    path.read_bytes()
                ).hexdigest()
        for name in (".git/index", ".git/HEAD"):
            p = repo / name
            digests[name] = (
                hashlib.sha256(p.read_bytes()).hexdigest() if p.is_file() else None
            )
        head = git(repo, "rev-parse", "HEAD").stdout
        porcelain = git(repo, "status", "--porcelain").stdout
        return digests, head, porcelain

    def test_observation_is_byte_identical(self):
        with tempfile.TemporaryDirectory() as td:
            repo = populate_herd(make_git_repo(td))
            # Clean tracked files with a STALE stat cache: this is the one
            # condition under which a plain (unflagged) `git status` would
            # rewrite .git/index, so without it this test cannot detect a
            # missing --no-optional-locks (reviewer finding B1).
            for i in range(20):
                (repo / f"clean{i}.txt").write_text("clean\n")
            git(repo, "add", "-A")
            git(repo, "commit", "-qm", "clean tracked files")
            time.sleep(1.1)
            for i in range(20):
                os.utime(repo / f"clean{i}.txt", None)
            (repo / "README.md").write_text("dirty change\n")
            (repo / "untracked.txt").write_text("untracked\n")
            before = self.snapshot(repo)
            with patch.object(obs_mod, "agent_info", fake_agent_info()):
                observe(repo)
                for as_json in (False, True):
                    buf = io.StringIO()
                    with contextlib.redirect_stdout(buf):
                        herdctl.observe(
                            argparse.Namespace(repo=str(repo), json=as_json)
                        )
            after = self.snapshot(repo)
            self.assertEqual(before[0], after[0])
            self.assertEqual(before[1], after[1])
            self.assertEqual(before[2], after[2])
            self.assertIn("README.md", before[2])

    def test_git_argv_carries_no_optional_locks(self):
        """Assert the flag on the argv actually executed, not on source text
        (a docstring mention must never satisfy this guarantee)."""
        with tempfile.TemporaryDirectory() as td:
            repo = populate_herd(make_git_repo(td))
            real_run = subprocess.run
            git_cmds = []

            def recording_run(cmd, *args, **kwargs):
                if isinstance(cmd, list) and cmd and Path(cmd[0]).name == "git":
                    git_cmds.append(list(cmd))
                return real_run(cmd, *args, **kwargs)

            with patch("subprocess.run", recording_run):
                observe(repo, probe_agents=False)
            self.assertGreaterEqual(len(git_cmds), 3)
            for cmd in git_cmds:
                self.assertIn("--no-optional-locks", cmd, cmd)

    def test_no_write_path_is_reachable(self):
        with tempfile.TemporaryDirectory() as td:
            repo = populate_herd(make_git_repo(td))
            real_io_open = io.open

            def guarded_open(file, mode="r", *args, **kwargs):
                if any(flag in str(mode) for flag in ("w", "a", "+", "x")):
                    raise AssertionError(f"write-mode open blocked: {mode!r}")
                return real_io_open(file, mode, *args, **kwargs)

            def blocked(*args, **kwargs):
                raise AssertionError("filesystem mutation attempted")

            with patch("pathlib.Path.mkdir", blocked), \
                    patch("pathlib.Path.write_text", blocked), \
                    patch("pathlib.Path.touch", blocked), \
                    patch("os.utime", blocked), \
                    patch("io.open", guarded_open), \
                    patch("builtins.open", guarded_open), \
                    patch.object(obs_mod, "agent_info", fake_agent_info()):
                obs = observe(repo)
            self.assertEqual(list(obs.keys()), TOP_KEYS)
            self.assertEqual(obs["task"]["state"], "available")


class StaticSourceGuardTests(unittest.TestCase):
    FORBIDDEN = [
        "write_text", "mkdir", "save_state", "save_task", "save_mission",
        "archive_task", "registry_save", "agent prompt", "agent read",
        "context_hint",
    ]

    def test_observe_source_has_no_mutation_vocabulary(self):
        src = (R / "herdr" / "observe.py").read_text()
        for token in self.FORBIDDEN:
            self.assertNotIn(token, src, token)
        self.assertIn("--no-optional-locks", src)

    def test_observe_source_has_no_timeout_behavior(self):
        # Operator scope clarification 2026-08-24: the human explicitly
        # forbids adding timeouts; any timeout behavior is a blocking defect.
        src = (R / "herdr" / "observe.py").read_text()
        self.assertNotIn("timeout", src.lower())


class LegacyJournalTests(unittest.TestCase):
    def test_events_jsonl_is_legacy_only(self):
        with tempfile.TemporaryDirectory() as td:
            repo = populate_herd(make_git_repo(td))
            obs = observe(repo, probe_agents=False)
            legacy = obs["legacy"]["events_jsonl"]
            self.assertTrue(legacy["present"])
            self.assertIn("legacy", legacy["note"].lower())
            without_legacy = {k: v for k, v in obs.items() if k != "legacy"}
            self.assertNotIn("events.jsonl", json.dumps(without_legacy))
            names = [e["name"] for e in obs["artifacts"]["listed"]]
            self.assertNotIn("events.jsonl", names)


if __name__ == "__main__":
    unittest.main(verbosity=2)
