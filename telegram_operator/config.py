"""Adapter configuration, loaded from OUTSIDE the target repository.

The configuration file holds the Telegram bot token, the exact numeric
Telegram user-ID allowlist, and exactly one target repository path. It
lives under ``~/Library/Application Support/DodgingInfinity/telegram/``
so that no secret ever sits inside a repository worktree. Loading fails
closed with an actionable message when the file is missing, malformed,
or readable by group/other (the bot token is a credential; a
world/group-readable config is refused outright, never "fixed" by the
adapter).
"""

import json
import os
import stat
from dataclasses import dataclass
from typing import Optional, Tuple

CONFIG_DIR_RELATIVE = os.path.join(
    "Library", "Application Support", "DodgingInfinity", "telegram"
)
CONFIG_FILE_NAME = "config.json"

# Permission bits that must NOT be set on the config file or its
# directory: any group/other access exposes the bot token.
FORBIDDEN_MODE_BITS = (
    stat.S_IRWXG | stat.S_IRWXO
)


class ConfigError(Exception):
    """Configuration is missing, unsafe, or invalid; message is actionable."""


@dataclass(frozen=True)
class AdapterConfig:
    """Validated adapter configuration.

    ``allowed_user_ids`` is a tuple of exact numeric Telegram user ids;
    membership checks must be exact integer equality (booleans are
    rejected at load time so ``True`` can never masquerade as user 1).
    ``repository`` is the resolved realpath of the single configured
    target repository.
    """

    bot_token: str
    allowed_user_ids: Tuple[int, ...]
    repository: str


def default_config_path(home=None):
    """Absolute path of the config file under the operator's home."""
    base = home if home is not None else os.path.expanduser("~")
    return os.path.join(base, CONFIG_DIR_RELATIVE, CONFIG_FILE_NAME)


def _is_within(child_realpath, parent_realpath):
    """Component-wise realpath containment: child is parent or below.

    The prefix is sep-terminated so a sibling like ``<repo>-backup``
    is never falsely treated as inside ``<repo>``.
    """
    if child_realpath == parent_realpath:
        return True
    return child_realpath.startswith(
        parent_realpath.rstrip(os.sep) + os.sep
    )


def _refuse_open_permissions(path, kind):
    """Raise ConfigError if group/other can access ``path`` at all."""
    mode = os.stat(path).st_mode
    if mode & FORBIDDEN_MODE_BITS:
        raise ConfigError(
            "%s %s is accessible by group/other (mode %o); refusing to"
            " load a config that can leak the bot token. Fix with:"
            " chmod %s %r" % (
                kind,
                path,
                stat.S_IMODE(mode),
                "700" if kind == "config directory" else "600",
                path,
            )
        )


def load_config(path=None):
    """Load and validate the adapter configuration, failing closed.

    Returns an AdapterConfig. Raises ConfigError with an actionable
    message on every failure; never partially loads.
    """
    # Normalize to an absolute path FIRST: a relative path like
    # "config.json" has dirname "", and os.stat("") in the directory
    # permission check raises an uncaught FileNotFoundError (round-4
    # finding OP4).
    resolved = (
        os.path.abspath(path) if path is not None else default_config_path()
    )
    if not os.path.isfile(resolved):
        raise ConfigError(
            "config file %s not found. Create the directory with mode 700"
            " and the file with mode 600 containing JSON keys bot_token"
            " (string), allowed_user_ids (list of numeric Telegram user"
            " ids), repository (path)." % resolved
        )
    _refuse_open_permissions(os.path.dirname(resolved), "config directory")
    _refuse_open_permissions(resolved, "config file")
    try:
        with open(resolved, "r", encoding="utf-8") as handle:
            raw = json.load(handle)
    except (OSError, ValueError) as exc:
        raise ConfigError(
            "config file %s could not be read as JSON (%s); repair the"
            " file or recreate it" % (resolved, exc)
        )
    if not isinstance(raw, dict):
        raise ConfigError(
            "config file %s must contain a JSON object, not %s"
            % (resolved, type(raw).__name__)
        )
    token = raw.get("bot_token")
    if not isinstance(token, str) or not token.strip() or token != token.strip():
        raise ConfigError(
            "config key bot_token must be a non-empty string with no"
            " leading/trailing whitespace"
        )
    if any(ch.isspace() or ord(ch) < 32 or ord(ch) == 127 for ch in token):
        # Belt-and-braces behind the transport's terminal exception
        # handler: a control character inside the token makes every
        # request URL invalid before any HTTP is attempted.
        raise ConfigError(
            "config key bot_token contains whitespace or a control"
            " character; paste the token exactly as issued"
        )
    ids = raw.get("allowed_user_ids")
    if not isinstance(ids, list) or not ids:
        raise ConfigError(
            "config key allowed_user_ids must be a non-empty list of"
            " numeric Telegram user ids"
        )
    checked = []
    for value in ids:
        # bool is a subclass of int; True must never authorize user 1.
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise ConfigError(
                "allowed_user_ids entries must be positive integers;"
                " got %r" % (value,)
            )
        checked.append(value)
    if len(set(checked)) != len(checked):
        raise ConfigError("allowed_user_ids contains duplicate entries")
    repo = raw.get("repository")
    if not isinstance(repo, str) or not repo.strip():
        raise ConfigError(
            "config key repository must be a non-empty path string"
        )
    repo_real = os.path.realpath(os.path.abspath(os.path.expanduser(repo)))
    if not os.path.isdir(repo_real):
        raise ConfigError(
            "configured repository %s (resolved to %s) is not a"
            " directory" % (repo, repo_real)
        )
    # Repository containment (round-5 review finding R5-B1): the
    # config file and its directory — which is, by construction, the
    # very directory the daemon derives its state, lock, and
    # LaunchAgent log paths from — must resolve OUTSIDE the configured
    # repository. Compared on REALPATHS, so a symlinked directory
    # cannot disguise an in-repo location that abspath alone would let
    # through.
    checks = (
        ("config file", os.path.realpath(resolved)),
        (
            "config/state directory",
            os.path.realpath(os.path.dirname(resolved)),
        ),
    )
    for label, candidate in checks:
        if _is_within(candidate, repo_real):
            raise ConfigError(
                "%s %s resolves to %s, which is inside the configured"
                " repository %s; refusing. The bot token and the"
                " authority-bearing adapter state that lives beside"
                " the config (approval records, nonces, the update"
                " offset, the lock, the agent logs) must never sit in"
                " a worktree the Codex Operator edits and commits"
                " from — it could be staged, committed, or read by"
                " the very automation this adapter drives. Move the"
                " config outside the repository."
                % (label, resolved, candidate, repo_real)
            )
    unknown = sorted(
        key for key in raw
        if key not in ("bot_token", "allowed_user_ids", "repository")
    )
    if unknown:
        raise ConfigError(
            "config file %s has unrecognized keys: %s; remove them (a"
            " typo here could silently disable an intended restriction)"
            % (resolved, ", ".join(unknown))
        )
    return AdapterConfig(
        bot_token=token,
        allowed_user_ids=tuple(checked),
        repository=repo_real,
    )
