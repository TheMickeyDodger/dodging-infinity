"""One repository-scoped Herdr instance."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

from .policy import HerdrPolicy


class HerdrInstance:
    """Repository-scoped configuration and policy interface."""

    HERD_DIR = ".herd"
    CONFIG_FILE = "herd.config.json"

    def __init__(self, repo: str | Path):
        self.repo = Path(repo).expanduser().resolve()

    @property
    def herd_root(self) -> Path:
        return self.repo / self.HERD_DIR

    @property
    def config_path(self) -> Path:
        return self.herd_root / self.CONFIG_FILE

    @property
    def initialized(self) -> bool:
        return self.config_path.exists()

    def load_config(self) -> dict[str, Any]:
        if not self.initialized:
            raise RuntimeError(
                f"{self.repo} is not an initialized Herdr repository."
            )

        return json.loads(self.config_path.read_text())

    def save_config(self, config: dict[str, Any]) -> None:
        self.herd_root.mkdir(parents=True, exist_ok=True)
        self.config_path.write_text(
            json.dumps(config, indent=2) + "\n"
        )

    def effective_policy(
        self,
        task_policy: dict[str, Any] | None = None,
    ) -> HerdrPolicy:
        config = self.load_config()

        return HerdrPolicy.resolve(
            config.get("policy"),
            task_policy,
        )

    def _update_policy(self, policy: dict[str, Any]) -> HerdrPolicy:
        # Validate before touching the file.
        resolved = HerdrPolicy.resolve(policy)

        config = self.load_config()
        config["policy"] = copy.deepcopy(policy)
        self.save_config(config)

        return resolved

    def merge_policy(
        self,
        policy: dict[str, Any],
    ) -> HerdrPolicy:
        """Merge repository-scoped policy into the current Herdr policy."""
        if not isinstance(policy, dict):
            raise ValueError("Policy must be an object.")

        config = self.load_config()
        current = copy.deepcopy(
            config.get("policy", {})
        )

        def merge(base, override):
            result = copy.deepcopy(base)

            for key, value in override.items():
                if key == "rules":
                    rules = result.setdefault(
                        "rules",
                        [],
                    )

                    for rule in value or []:
                        if rule not in rules:
                            rules.append(rule)

                elif (
                    isinstance(value, dict)
                    and isinstance(
                        result.get(key),
                        dict,
                    )
                ):
                    result[key] = merge(
                        result[key],
                        value,
                    )

                else:
                    result[key] = copy.deepcopy(
                        value
                    )

            return result

        merged = merge(
            current,
            policy,
        )

        return self._update_policy(
            merged
        )

    def set_policy(
        self,
        dotted_path: str,
        value: Any,
    ) -> HerdrPolicy:
        parts = [part for part in dotted_path.split(".") if part]

        if not parts:
            raise ValueError("Policy path cannot be empty.")

        config = self.load_config()
        policy = copy.deepcopy(config.get("policy", {}))

        target = policy

        for part in parts[:-1]:
            child = target.get(part)

            if child is None:
                child = {}
                target[part] = child
            elif not isinstance(child, dict):
                raise ValueError(
                    f"Cannot set {dotted_path!r}: "
                    f"{part!r} is not an object."
                )

            target = child

        target[parts[-1]] = value

        return self._update_policy(policy)

    def add_rule(self, rule: str) -> HerdrPolicy:
        rule = rule.strip()

        if not rule:
            raise ValueError("Rule cannot be empty.")

        config = self.load_config()
        policy = copy.deepcopy(config.get("policy", {}))
        rules = policy.setdefault("rules", [])

        if rule not in rules:
            rules.append(rule)

        return self._update_policy(policy)

    def remove_rule(self, rule: str) -> HerdrPolicy:
        config = self.load_config()
        policy = copy.deepcopy(config.get("policy", {}))
        rules = policy.setdefault("rules", [])

        if rule in rules:
            rules.remove(rule)

        return self._update_policy(policy)
