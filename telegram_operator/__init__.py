"""Telegram Remote Operator adapter: a trusted local interface boundary.

The adapter receives allowlisted private-chat Telegram intent on the
trusted Mac and routes it EXCLUSIVELY through the local Codex Gateway
(``codex_gateway``) into the Codex Operator workflow. It is an adapter,
not an execution system: it must never import, invoke, read, construct,
dispatch, monitor, or control Herdr, herdctl, or any orchestration
state; the static suite enforces that boundary both statically and
behaviorally. Ordinary Telegram text carries no execution or delivery
authority. Remote commit/push/PR/tag/release/deploy authority is out of
scope and deferred; only the human, locally, authorizes delivery.

All adapter configuration and durable state live OUTSIDE the target
repository, under the operator's home directory. Tests are hermetic:
transports are injected and no real Telegram, Codex, or network call is
ever made by the test suite.
"""

from telegram_operator.config import (
    AdapterConfig,
    ConfigError,
    default_config_path,
    load_config,
)
from telegram_operator.state import (
    STATE_SCHEMA_VERSION,
    StateError,
    StateStore,
    acquire_single_instance_lock,
    default_state_dir,
)

__all__ = [
    "AdapterConfig",
    "ConfigError",
    "STATE_SCHEMA_VERSION",
    "StateError",
    "StateStore",
    "acquire_single_instance_lock",
    "default_config_path",
    "default_state_dir",
    "load_config",
]
