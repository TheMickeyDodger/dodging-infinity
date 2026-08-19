"""Package-owned Herdr configuration defaults and presets."""

from __future__ import annotations

import copy

from .policy import DEFAULT_POLICY


HERD = ".herd"
CFG = "herd.config.json"

ROLE_FILES = {
    "supervisor": "supervisor.md",
    "lead": "lead.md",
    "executor": "executor.md",
    "reviewer": "reviewer.md",
}


DEFAULT = {
    "version": 4,
    "project": {
        "name": "",
        "test_command": "",
        "allow_auto_merge": False,
        "allow_push": False,
        "allow_deploy": False,
        "commit_confirmation": True,
        "push_confirmation": True,
    },
    "orchestration": {
        "heartbeat_seconds": 900,
        "leads": 1,
        "pods": 1,
        "agent_start_timeout_ms": 60000,
        "shell_ready_timeout_ms": 30000,
        "agent_task_timeout_ms": 600000,
        "heartbeat_autostart": True,
    },
    "policy": copy.deepcopy(DEFAULT_POLICY),
    "context": {
        "clear_before_new_task": True,
        "clear_roles": [
            "supervisor",
            "lead",
            "executor",
            "reviewer",
        ],
        "checkpoint_history": True,
        "task_history_max_chars": 60000,
        "show_context_hint": True,
        "reset_commands": {
            "claude": "/clear",
            "codex": "/new",
        },
    },
    "roles": {
        "supervisor": {
            "kind": "claude",
            "args": [
                "--model",
                "fable",
                "--permission-mode",
                "acceptEdits",
            ],
        },
        "lead": {
            "kind": "claude",
            "args": [
                "--model",
                "opus",
                "--permission-mode",
                "acceptEdits",
            ],
        },
        "executor": {
            "kind": "claude",
            "args": [
                "--model",
                "fable",
                "--permission-mode",
                "acceptEdits",
            ],
        },
        "reviewer": {
            "kind": "claude",
            "args": [
                "--model",
                "opus",
                "--permission-mode",
                "default",
            ],
        },
    },
}


PRESETS = {
    "max-quality": {
        "description": (
            "Claude Fable supervisor/executor + Opus lead + "
            "GPT-5.6 Sol high read-only reviewer"
        ),
        "roles": {
            "supervisor": {
                "kind": "claude",
                "args": [
                    "--model",
                    "fable",
                    "--permission-mode",
                    "auto",
                ],
            },
            "lead": {
                "kind": "claude",
                "args": [
                    "--model",
                    "opus",
                    "--permission-mode",
                    "auto",
                ],
            },
            "executor": {
                "kind": "claude",
                "args": [
                    "--model",
                    "fable",
                    "--permission-mode",
                    "auto",
                ],
            },
            "reviewer": {
                "kind": "codex",
                "args": [
                    "-m",
                    "gpt-5.6-sol",
                    "-c",
                    'model_reasoning_effort="high"',
                    "-c",
                    'sandbox_mode="read-only"',
                    "-c",
                    'approval_policy="never"',
                ],
            },
        },
    },
    "all-claude": {
        "description": (
            "Claude-only subscription herd using "
            "Fable/Opus with auto mode"
        ),
        "roles": {
            "supervisor": {
                "kind": "claude",
                "args": [
                    "--model",
                    "fable",
                    "--permission-mode",
                    "auto",
                ],
            },
            "lead": {
                "kind": "claude",
                "args": [
                    "--model",
                    "opus",
                    "--permission-mode",
                    "auto",
                ],
            },
            "executor": {
                "kind": "claude",
                "args": [
                    "--model",
                    "fable",
                    "--permission-mode",
                    "auto",
                ],
            },
            "reviewer": {
                "kind": "claude",
                "args": [
                    "--model",
                    "opus",
                    "--permission-mode",
                    "auto",
                ],
            },
        },
    },
    "conservative": {
        "description": (
            "Claude-only herd retaining explicit edit approvals"
        ),
        "roles": copy.deepcopy(
            DEFAULT["roles"]
        ),
    },
}


def apply_preset_to_config(
    data: dict,
    name: str | None,
) -> dict:
    if not name:
        return data

    if name not in PRESETS:
        raise ValueError(
            f"Unknown preset `{name}`. Available: "
            f"{', '.join(sorted(PRESETS))}"
        )

    data["roles"] = copy.deepcopy(
        PRESETS[name]["roles"]
    )
    data["preset"] = name

    return data
