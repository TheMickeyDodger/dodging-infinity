"""Configuration for the Grok MCP endpoint, read at CLI-main time only.

``load_config(path, environment)`` reads ONE JSON file and returns a
``ServerConfig``. It is called from ``grok_mcp.cli.main`` and nowhere
else; importing this package reads nothing, and no constructor in the
package touches the filesystem or the environment.

Keys (all optional except ``repository``):

- ``repository``: the local repository path handed to the operator
  session. It is configuration, never a tool argument.
- ``bind_host`` (default ``127.0.0.1``), ``port`` (default 8765),
  ``endpoint_path`` (default ``/mcp``).
- ``allowed_origins``: list of exact ``Origin`` values to serve; a
  request with any other ``Origin`` header is refused with 403.
- ``bearer_token``: the static connector credential. It may instead
  come from the environment mapping the CLI passes in
  (``GROK_MCP_BEARER_TOKEN``), so the file need not hold a secret.
- ``mission_store_dir`` (optional): the absolute protected directory of
  the Mission Core store. When present the CLI wires a Mission service
  into the controller so the ``di_mission_*`` tools work; when absent
  those tools refuse with an observable reason and nothing else changes.
  Loading the config creates nothing there.

The config file and its directory must not be group- or
world-accessible; an open mode is refused before the file is read.
There is no hardcoded credential, and ``ServerConfig`` hides the token
from ``repr`` and ``str``. No error message ever contains the token.
"""

import json
import os
import stat
from dataclasses import dataclass, field
from typing import Optional, Tuple

BEARER_TOKEN_ENV = "GROK_MCP_BEARER_TOKEN"
DEFAULT_BIND_HOST = "127.0.0.1"
DEFAULT_PORT = 8765
DEFAULT_ENDPOINT_PATH = "/mcp"


class ConfigError(Exception):
    """The configuration is unusable; nothing was started."""


@dataclass(frozen=True)
class ServerConfig:
    bind_host: str
    port: int
    endpoint_path: str
    bearer_token: str = field(repr=False)
    allowed_origins: Tuple[str, ...]
    repository: str
    mission_store_dir: Optional[str] = None


def _refuse_open_permissions(path, kind):
    mode = os.stat(path).st_mode
    if mode & (stat.S_IRWXG | stat.S_IRWXO):
        raise ConfigError(
            "%s %s is group- or world-accessible; use mode 700 for the"
            " directory and 600 for the file" % (kind, path)
        )


def _token_ok(token):
    return (
        isinstance(token, str) and token.strip() != "" and token == token.strip()
        and not any(ch.isspace() or ord(ch) < 32 or ord(ch) == 127 for ch in token)
    )


def load_config(path, environment):
    """Read and validate the config file, failing closed; never partial."""
    if not isinstance(path, str) or not path:
        raise ConfigError("a config file path is required (--config PATH)")
    resolved = os.path.abspath(path)
    if not os.path.isfile(resolved):
        raise ConfigError(
            "config file %s not found; create it with mode 600 in a"
            " mode-700 directory containing at least the JSON key"
            " repository (path)" % resolved
        )
    _refuse_open_permissions(os.path.dirname(resolved), "config directory")
    _refuse_open_permissions(resolved, "config file")
    try:
        with open(resolved, "r", encoding="utf-8") as handle:
            raw = json.load(handle)
    except (OSError, ValueError) as exc:
        raise ConfigError(
            "config file %s could not be read as JSON (%s)"
            % (resolved, type(exc).__name__)
        )
    if not isinstance(raw, dict):
        raise ConfigError("config file %s must contain a JSON object" % resolved)
    repository = raw.get("repository")
    if not isinstance(repository, str) or not repository.strip():
        raise ConfigError("config key repository must be a non-empty path string")
    bind_host = raw.get("bind_host", DEFAULT_BIND_HOST)
    if not isinstance(bind_host, str) or not bind_host.strip():
        raise ConfigError("config key bind_host must be a non-empty string")
    port = raw.get("port", DEFAULT_PORT)
    if isinstance(port, bool) or not isinstance(port, int) or not 0 <= port <= 65535:
        raise ConfigError("config key port must be an integer in 0..65535")
    endpoint_path = raw.get("endpoint_path", DEFAULT_ENDPOINT_PATH)
    if (not isinstance(endpoint_path, str) or not endpoint_path.startswith("/")
            or any(ch.isspace() for ch in endpoint_path)):
        raise ConfigError(
            "config key endpoint_path must be a path starting with '/'"
        )
    origins = raw.get("allowed_origins", [])
    if not isinstance(origins, list) or not all(
        isinstance(item, str) and item.strip() for item in origins
    ):
        raise ConfigError(
            "config key allowed_origins must be a list of non-empty strings"
        )
    mission_store_dir = raw.get("mission_store_dir")
    if mission_store_dir is not None and (
        not isinstance(mission_store_dir, str)
        or not os.path.isabs(mission_store_dir)
        or any(ch.isspace() or ord(ch) < 32 for ch in mission_store_dir)
    ):
        raise ConfigError(
            "config key mission_store_dir must be an absolute directory path"
        )
    token = raw.get("bearer_token")
    if token is None:
        token = environment.get(BEARER_TOKEN_ENV)
    if token is None:
        raise ConfigError(
            "no bearer token: set config key bearer_token or the %s"
            " environment variable" % BEARER_TOKEN_ENV
        )
    if not _token_ok(token):
        raise ConfigError(
            "the bearer token must be a non-empty string with no whitespace"
            " or control characters"
        )
    return ServerConfig(
        bind_host=bind_host.strip(),
        port=port,
        endpoint_path=endpoint_path,
        bearer_token=token,
        allowed_origins=tuple(item.strip() for item in origins),
        repository=repository,
        mission_store_dir=mission_store_dir,
    )
