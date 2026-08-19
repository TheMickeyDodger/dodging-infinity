"""Declarative policy model for a Herdr instance."""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Any


DEFAULT_POLICY = {
    "rules": [],
    "git": {
        "commit": "require-human",
        "push": "require-human",
    },
    "review": {
        "required": True,
        "max_rounds": 5,
    },
    "scope": {
        "allowed": [],
        "blocked": [],
    },
}


def _merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Recursively merge policy layers.

    `rules` accumulate across layers. Other values are overridden by the
    more-specific layer.
    """
    result = copy.deepcopy(base)

    for key, value in override.items():
        if key == "rules":
            existing = result.setdefault("rules", [])
            for rule in value or []:
                if rule not in existing:
                    existing.append(rule)
        elif (
            isinstance(value, dict)
            and isinstance(result.get(key), dict)
        ):
            result[key] = _merge(result[key], value)
        else:
            result[key] = copy.deepcopy(value)

    return result


@dataclass(frozen=True)
class HerdrPolicy:
    """Resolved policy for one Herdr instance."""

    data: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def resolve(cls, *layers: dict[str, Any] | None) -> "HerdrPolicy":
        """Resolve policy from least-specific to most-specific layers."""
        resolved = copy.deepcopy(DEFAULT_POLICY)

        for layer in layers:
            if layer:
                resolved = _merge(resolved, layer)

        policy = cls(resolved)
        policy.validate()
        return policy

    def validate(self) -> None:
        git = self.data.get("git", {})

        commit_modes = {
            "require-human",
            "after-review",
            "allowed",
            "forbidden",
        }
        push_modes = {
            "require-human",
            "after-review",
            "allowed",
            "forbidden",
        }

        if git.get("commit") not in commit_modes:
            raise ValueError(
                f"Invalid git.commit policy: {git.get('commit')!r}"
            )

        if git.get("push") not in push_modes:
            raise ValueError(
                f"Invalid git.push policy: {git.get('push')!r}"
            )

        max_rounds = self.data.get("review", {}).get("max_rounds", 5)
        if not isinstance(max_rounds, int) or max_rounds < 1:
            raise ValueError("review.max_rounds must be a positive integer")

        rules = self.data.get("rules", [])
        if not isinstance(rules, list) or not all(
            isinstance(rule, str) for rule in rules
        ):
            raise ValueError("rules must be a list of strings")

    @property
    def rules(self) -> list[str]:
        return list(self.data.get("rules", []))

    def get(self, *path: str, default: Any = None) -> Any:
        value: Any = self.data

        for key in path:
            if not isinstance(value, dict) or key not in value:
                return default
            value = value[key]

        return value

    def to_dict(self) -> dict[str, Any]:
        return copy.deepcopy(self.data)
