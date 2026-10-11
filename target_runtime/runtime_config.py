"""The DI Runtime's OWN production configuration (I3 of task
20261010-164104-1761f6): ``dirun --runtime-config PATH``.

The Runtime's only configuration input is the control repository it is pinned
to and the workflow store it advances. Until now it could read them only
from the Telegram adapter's configuration (``--config``, or the adapter's
default file), whose loader requires a Telegram bot token and user ids, so
an operator without Telegram (a Grok Bot workflow store) had no supported
way to start a Runtime at all. This is a SEPARATE, EXPLICIT composition:

- selected only by its own flag; ``--config`` and the default (the Telegram
  adapter configuration, ``telegram_operator.config.load_config``) are
  UNCHANGED, and naming both flags is refused as ambiguous;
- its own CLOSED schema, no shared widened key set::

      {"schema": "dodging-infinity.runtime-config.v1",
       "control_repository": "<the control repository's canonical path>",
       "workflow_store": "<the canonical path of THIS file's directory>"}

- the same protection the adapter configuration has: the file and its
  directory carry no group/other permission, and both lie OUTSIDE the
  control repository, compared on REALPATHS for the file AND its directory
  (as ``telegram_operator.config`` does), so a symlink cannot disguise an
  in-repository file;
- an explicit, verified binding: the control repository is named by its
  canonical path (so it compares exactly with every workflow record's
  recorded control realpath) and must hold the control policy documents;
  the workflow store is named, and must BE the directory this file lives
  in (the Runtime's state directory, as for ``--config``).

Every refusal is a ``RuntimeConfigError`` carrying its own problem code; it is
a ``ConfigError``, so ``dirun`` exits 2 for it exactly as for the adapter
configuration's refusals. Nothing here contains, reads or defaults a Telegram
field: a file carrying one is refused as ambiguous, never repaired.
"""

import json
import os
import stat
from dataclasses import dataclass

from telegram_operator.config import ConfigError
from target_runtime.workspace import _is_within

RUNTIME_CONFIG_SCHEMA = "dodging-infinity.runtime-config.v1"
RUNTIME_CONFIG_KEYS = ("schema", "control_repository", "workflow_store")
# The conventional file name (any name inside the store is accepted).
RUNTIME_CONFIG_FILE_NAME = "runtime-config.json"
# The adapter configuration's closed key set (telegram_operator.config,
# pinned equal by test): any one of them makes a Runtime file ambiguous.
TELEGRAM_ADAPTER_KEYS = ("bot_token", "allowed_user_ids", "repository")
# The control repository's policy documents (the control-policy digest is
# computed over them; workflow_authority.digest).
CONTROL_POLICY_DOCUMENTS = ("AGENTS.md", "OPERATOR_PROTOCOL.md")
# As for the adapter configuration: no group/other access to the file or
# its directory (telegram_operator.config.FORBIDDEN_MODE_BITS, pinned).
FORBIDDEN_MODE_BITS = stat.S_IRWXG | stat.S_IRWXO

PROBLEM_MISSING = "runtime_config_missing"
PROBLEM_PERMISSIONS = "runtime_config_permissions"
PROBLEM_MALFORMED = "runtime_config_malformed"
PROBLEM_AMBIGUOUS = "runtime_config_ambiguous"
PROBLEM_PATH = "runtime_config_path_invalid"
PROBLEM_STORE_MISMATCH = "runtime_config_store_mismatch"
PROBLEM_IN_REPOSITORY = "runtime_config_inside_control_repository"


class RuntimeConfigError(ConfigError):
    """A refused Runtime configuration; ``problem`` names the class."""

    def __init__(self, problem, reason):
        super(RuntimeConfigError, self).__init__("%s: %s" % (problem, reason))
        self.problem = problem
        self.reason = reason


@dataclass(frozen=True)
class RuntimeConfig:
    """A validated Runtime configuration. ``repository`` is the control
    repository's canonical path (what the Broker is pinned to, named as in
    the adapter configuration); ``workflow_store`` is the store the Runtime
    advances, the directory the file lives in."""

    repository: str
    workflow_store: str
    path: str


def _refuse(problem, reason):
    raise RuntimeConfigError(problem, reason)


def _canonical_directory(name, value):
    """``value`` as an absolute, canonical (its own realpath) directory."""
    if not isinstance(value, str) or not value or "\x00" in value:
        _refuse(PROBLEM_PATH, "%s must be a non-empty path string" % name)
    if not os.path.isabs(value):
        _refuse(PROBLEM_PATH, "%s %r is not an absolute path" % (name, value))
    real = os.path.realpath(value)
    if real != value:
        _refuse(PROBLEM_PATH,
                "%s %r is not canonical: it resolves to %r. Name it by its"
                " canonical path, so it compares exactly with what DI"
                " records" % (name, value, real))
    if not os.path.isdir(real):
        _refuse(PROBLEM_PATH, "%s %r is not a directory" % (name, value))
    return real


def load_runtime_config(path):
    """Load and validate a Runtime configuration, failing closed with one
    problem code per refusal class. Never partially loads."""
    if not isinstance(path, str) or not path or "\x00" in path:
        _refuse(PROBLEM_MISSING, "no Runtime configuration path was given")
    resolved = os.path.abspath(path)
    if not os.path.isfile(resolved):
        _refuse(PROBLEM_MISSING,
                "Runtime configuration %s not found. Create it (mode 600, in"
                " a directory of mode 700) with exactly the keys %s"
                % (resolved, ", ".join(RUNTIME_CONFIG_KEYS)))
    directory = os.path.dirname(resolved)
    for kind, target, fix in (("directory", directory, "700"),
                              ("file", resolved, "600")):
        mode = os.stat(target).st_mode
        if mode & FORBIDDEN_MODE_BITS:
            _refuse(PROBLEM_PERMISSIONS,
                    "Runtime configuration %s %s is accessible by group/other"
                    " (mode %o): it names the control repository the Runtime"
                    " is pinned to, beside the store's authority state. Fix"
                    " with: chmod %s %r"
                    % (kind, target, stat.S_IMODE(mode), fix, target))
    try:
        with open(resolved, "r", encoding="utf-8") as handle:
            raw = json.load(handle)
    except (OSError, ValueError) as exc:
        _refuse(PROBLEM_MALFORMED,
                "Runtime configuration %s could not be read as JSON (%s)"
                % (resolved, type(exc).__name__))
    if not isinstance(raw, dict):
        _refuse(PROBLEM_MALFORMED,
                "Runtime configuration %s must hold a JSON object, not %s"
                % (resolved, type(raw).__name__))
    foreign = sorted(key for key in raw if key in TELEGRAM_ADAPTER_KEYS)
    if foreign:
        _refuse(PROBLEM_AMBIGUOUS,
                "Runtime configuration %s carries Telegram adapter key(s) %s:"
                " one file is one schema. The adapter configuration is read"
                " through --config, or from its default file when neither"
                " option is given; a Runtime configuration holds exactly %s"
                % (resolved, ", ".join(foreign),
                                 ", ".join(RUNTIME_CONFIG_KEYS)))
    if raw.get("schema") != RUNTIME_CONFIG_SCHEMA:
        _refuse(PROBLEM_MALFORMED,
                "Runtime configuration %s must name schema %r exactly"
                % (resolved, RUNTIME_CONFIG_SCHEMA))
    missing = sorted(set(RUNTIME_CONFIG_KEYS) - set(raw))
    unknown = sorted(set(raw) - set(RUNTIME_CONFIG_KEYS))
    if missing or unknown:
        _refuse(PROBLEM_MALFORMED,
                "Runtime configuration %s must hold exactly the keys %s"
                " (missing: %s; unrecognized: %s)"
                % (resolved, ", ".join(RUNTIME_CONFIG_KEYS),
                   ", ".join(missing) or "none", ", ".join(unknown) or "none"))
    control = _canonical_directory("control_repository",
                                   raw["control_repository"])
    for document in CONTROL_POLICY_DOCUMENTS:
        candidate = os.path.join(control, document)
        if not os.path.isfile(candidate) or os.path.islink(candidate):
            _refuse(PROBLEM_PATH,
                    "control_repository %r holds no regular %s: it is not a"
                    " control repository" % (control, document))
    store = _canonical_directory("workflow_store", raw["workflow_store"])
    home = os.path.realpath(directory)
    if store != home:
        _refuse(PROBLEM_STORE_MISMATCH,
                "workflow_store %r is not the directory this configuration"
                " lives in (%r): the Runtime advances exactly the store its"
                " configuration sits in, so the two must be the same"
                " directory" % (store, home))
    # Containment on REALPATHS for the configuration FILE as well as its
    # directory (parity with telegram_operator.config): abspath does not
    # resolve the final component, so a symlinked file whose target sits
    # inside the control repository is caught only by its own realpath.
    for label, inner, outer in (("workflow store", store, control),
                                ("configuration file",
                                 os.path.realpath(resolved), control),
                                ("control repository", control, store)):
        if _is_within(inner, outer):
            _refuse(PROBLEM_IN_REPOSITORY,
                    "the %s %r lies inside %r: the store, its authority state"
                    " and this configuration must sit OUTSIDE the control"
                    " repository, and the control repository outside the"
                    " store" % (label, inner, outer))
    return RuntimeConfig(repository=control, workflow_store=store,
                         path=resolved)
