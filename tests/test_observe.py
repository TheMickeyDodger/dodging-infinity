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
    render_observation,
)

R = Path(__file__).resolve().parents[1]

TOP_KEYS = [
    "schema_version", "generated_at", "completeness", "repository", "config",
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
        self.assertEqual(obs["schema_version"], 1)
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
            models = {r["role"]: r["model"] for r in obs["config"]["roles"]}
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
