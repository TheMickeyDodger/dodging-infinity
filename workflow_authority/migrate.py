"""Explicit, human-invoked schema migrations. Never automatic.

Two migrations live here, both with the same shape (byte-exact
preserved backup first, atomic rewrite, no-op on current version,
refuse unknown versions and malformed files outright):

**Adapter state, version 1 -> 2** (``tgop migrate-state``): schema
version 2 exists so that DI-REMOTE-1-era approvals can NEVER
authorize a DI-REMOTE-2 target: the migration marks every
pre-existing approval record ``superseded_for_v2`` — a DISTINCT
field, for v2 purposes only. The v1 ``superseded`` flag and every
other v1 field are left byte-identical in meaning, so DI-REMOTE-1
local semantics are unchanged.

**Workflow store, version 1 -> 2** (``tgop migrate-workflows``):
workflow-record schema 2 added authority fields (the exact original
human intent, unresolved questions, execution scope, per-field
authority content) that a version-1 record never carried and that
must NEVER be fabricated — fabricating the human's intent would
forge authority. The fail-closed path is therefore RETIREMENT, not
upgrade: the v1 file is preserved byte-exact in the backup, and the
rewritten store carries schema version 2 with NO active records; a
retired v1 workflow can never be resumed under v2 and requires a
fresh Mission Authorization. Loading a v1 store never auto-migrates
and never auto-reinitializes; it fails closed naming this command.
"""

import json
import os

from telegram_operator.state import (
    STATE_FILE_NAME,
    STATE_SCHEMA_VERSION,
    TOP_LEVEL_SHAPE,
    StateError,
    StateStore,
)
from workflow_authority.store import (
    WORKFLOW_STORE_SCHEMA_VERSION,
    WORKFLOWS_FILE_NAME,
    StoreError,
    WorkflowStore,
    exclusive_store_lock,
)

V1_SCHEMA_VERSION = 1
BACKUP_SUFFIX = ".v1.backup"
SUPERSEDED_FOR_V2_KEY = "superseded_for_v2"
MIGRATION_COMMAND = "tgop migrate-state"

WORKFLOW_V1_SCHEMA_VERSION = 1
WORKFLOW_MIGRATION_COMMAND = "tgop migrate-workflows"


class MigrationError(Exception):
    """Migration cannot proceed safely; message is actionable."""


def _validate_v1_shape(document, path):
    """Validate a version-1 state document's top-level shape.

    The v1 and v2 top-level key sets are identical; only the schema
    version value differs, so the adapter's shape table is reused with
    the version check pinned to 1.
    """
    if not isinstance(document, dict):
        raise MigrationError(
            "state file %s must contain a JSON object, not %s;"
            " refusing to migrate" % (path, type(document).__name__)
        )
    for key, expected in TOP_LEVEL_SHAPE.items():
        if key not in document:
            raise MigrationError(
                "state file %s is missing required key %r; refusing to"
                " migrate a malformed file" % (path, key)
            )
        value = document[key]
        if isinstance(value, bool) and expected is not bool:
            raise MigrationError(
                "state file %s key %r has invalid boolean value;"
                " refusing to migrate a malformed file" % (path, key)
            )
        if not isinstance(value, expected):
            raise MigrationError(
                "state file %s key %r has invalid type %s; refusing to"
                " migrate a malformed file"
                % (path, key, type(value).__name__)
            )
    for approval_id, record in document["approvals"].items():
        if not isinstance(record, dict):
            raise MigrationError(
                "state file %s approval %r is not an object; refusing"
                " to migrate a malformed file" % (path, approval_id)
            )


def _write_backup(backup_path, original_bytes, directory):
    descriptor = os.open(
        backup_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600
    )
    try:
        os.write(descriptor, original_bytes)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    directory_descriptor = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(directory_descriptor)
    finally:
        os.close(directory_descriptor)


def migrate_state(directory):
    """Migrate ``state.json`` in ``directory`` from schema 1 to 2.

    Returns ``(changed, message)``. ``changed`` is True only when the
    file was actually rewritten. Raises MigrationError with an
    actionable message on every unsafe condition it detects;
    underlying filesystem errors (``OSError`` — read-only directory,
    full disk) propagate to the caller, which must present them
    actionably. In EVERY failure path the original state file is left
    untouched (a backup already written is preserved, and a re-run
    resumes from it).
    """
    path = os.path.join(directory, STATE_FILE_NAME)
    if not os.path.exists(path):
        return (
            False,
            "no state file at %s; nothing to migrate (a fresh adapter"
            " run starts at schema version %d)"
            % (path, STATE_SCHEMA_VERSION),
        )
    try:
        with open(path, "rb") as handle:
            original_bytes = handle.read()
    except OSError as exc:
        raise MigrationError(
            "state file %s could not be read (%s); fix permissions and"
            " re-run %s" % (path, exc, MIGRATION_COMMAND)
        )
    try:
        document = json.loads(original_bytes.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise MigrationError(
            "state file %s could not be parsed as JSON (%s); refusing"
            " to migrate a malformed file. Move it aside (keeping it"
            " for inspection) — it is NOT safe to delete it"
            % (path, exc)
        )
    if not isinstance(document, dict):
        raise MigrationError(
            "state file %s must contain a JSON object, not %s;"
            " refusing to migrate" % (path, type(document).__name__)
        )
    version = document.get("state_schema_version")
    if not isinstance(version, bool) and version == STATE_SCHEMA_VERSION:
        try:
            StateStore(directory).load()
        except StateError as exc:
            raise MigrationError(
                "state file %s claims schema version %d but does not"
                " validate (%s); refusing to touch it"
                % (path, STATE_SCHEMA_VERSION, exc)
            )
        return (
            False,
            "state file %s is already at schema version %d; nothing"
            " changed" % (path, STATE_SCHEMA_VERSION),
        )
    if isinstance(version, bool) or version != V1_SCHEMA_VERSION:
        raise MigrationError(
            "state file %s has state_schema_version %r; this migration"
            " understands only %d -> %d. Move the file aside (keeping"
            " it for inspection) rather than deleting it"
            % (path, version, V1_SCHEMA_VERSION, STATE_SCHEMA_VERSION)
        )
    _validate_v1_shape(document, path)
    backup_path = path + BACKUP_SUFFIX
    if os.path.exists(backup_path):
        with open(backup_path, "rb") as handle:
            backup_bytes = handle.read()
        if backup_bytes != original_bytes:
            raise MigrationError(
                "backup file %s already exists and differs from the"
                " current state file; refusing to overwrite a"
                " preserved backup. Move it aside, then re-run %s"
                % (backup_path, MIGRATION_COMMAND)
            )
        # Byte-identical backup: this migration's own earlier run was
        # interrupted after the backup and before the rewrite. The v1
        # bytes are already preserved exactly; proceed without
        # touching the backup.
    else:
        _write_backup(backup_path, original_bytes, directory)
    document["state_schema_version"] = STATE_SCHEMA_VERSION
    marked = 0
    for record in document["approvals"].values():
        record[SUPERSEDED_FOR_V2_KEY] = True
        marked += 1
    StateStore(directory).save(document)
    return (
        True,
        "migrated %s to schema version %d; %d pre-existing approval"
        " record(s) marked %s (v1 local semantics unchanged); v1"
        " backup preserved at %s"
        % (
            path,
            STATE_SCHEMA_VERSION,
            marked,
            SUPERSEDED_FOR_V2_KEY,
            backup_path,
        ),
    )


def migrate_workflow_store(directory):
    """Migrate ``workflows.json`` in ``directory`` from schema 1 to 2.

    Returns ``(changed, message)``; raises MigrationError on every
    unsafe condition. The chosen fail-closed path is RETIREMENT:
    version-1 records lack authority fields (the exact original human
    intent, unresolved questions, execution scope) that must never be
    fabricated, so the v1 file is preserved byte-exact in the backup
    and the rewritten store is schema version 2 with NO active
    records — the exact retired count is reported, and every retired
    workflow requires a fresh Mission Authorization. In EVERY failure
    path the original store file is left untouched (a backup already
    written is preserved, and a re-run resumes from it). The whole
    operation holds the store's cross-process lock so a live adapter
    or Runtime cycle can never interleave with the rewrite.
    """
    path = os.path.join(directory, WORKFLOWS_FILE_NAME)
    if not os.path.exists(path):
        return (
            False,
            "no workflow store at %s; nothing to migrate (a fresh"
            " store starts at schema version %d)"
            % (path, WORKFLOW_STORE_SCHEMA_VERSION),
        )
    with exclusive_store_lock(directory):
        try:
            with open(path, "rb") as handle:
                original_bytes = handle.read()
        except OSError as exc:
            raise MigrationError(
                "workflow store %s could not be read (%s); fix"
                " permissions and re-run %s"
                % (path, exc, WORKFLOW_MIGRATION_COMMAND)
            )
        try:
            document = json.loads(original_bytes.decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as exc:
            raise MigrationError(
                "workflow store %s could not be parsed as JSON (%s);"
                " refusing to migrate a malformed file. Move it aside"
                " (keeping it for inspection) — it is NOT safe to"
                " delete it: it carries authorization records"
                % (path, exc)
            )
        if not isinstance(document, dict):
            raise MigrationError(
                "workflow store %s must contain a JSON object, not %s;"
                " refusing to migrate" % (path, type(document).__name__)
            )
        version = document.get("workflow_store_schema_version")
        if not isinstance(version, bool) and version == (
            WORKFLOW_STORE_SCHEMA_VERSION
        ):
            try:
                WorkflowStore(directory).load()
            except StoreError as exc:
                raise MigrationError(
                    "workflow store %s claims schema version %d but"
                    " does not validate (%s); refusing to touch it"
                    % (path, WORKFLOW_STORE_SCHEMA_VERSION, exc)
                )
            return (
                False,
                "workflow store %s is already at schema version %d;"
                " nothing changed"
                % (path, WORKFLOW_STORE_SCHEMA_VERSION),
            )
        if isinstance(version, bool) or version != (
            WORKFLOW_V1_SCHEMA_VERSION
        ):
            raise MigrationError(
                "workflow store %s has workflow_store_schema_version"
                " %r; this migration understands only %d -> %d. Move"
                " the file aside (keeping it for inspection) rather"
                " than deleting it"
                % (path, version, WORKFLOW_V1_SCHEMA_VERSION,
                   WORKFLOW_STORE_SCHEMA_VERSION)
            )
        workflows = document.get("workflows")
        if not isinstance(workflows, dict):
            raise MigrationError(
                "workflow store %s key 'workflows' is not an object;"
                " refusing to migrate a malformed file. Move it aside"
                " (keeping it for inspection)" % path
            )
        retired = len(workflows)
        backup_path = path + BACKUP_SUFFIX
        if os.path.exists(backup_path):
            with open(backup_path, "rb") as handle:
                backup_bytes = handle.read()
            if backup_bytes != original_bytes:
                raise MigrationError(
                    "backup file %s already exists and differs from"
                    " the current workflow store; refusing to"
                    " overwrite a preserved backup. Move it aside,"
                    " then re-run %s"
                    % (backup_path, WORKFLOW_MIGRATION_COMMAND)
                )
            # Byte-identical backup: this migration's own earlier run
            # was interrupted after the backup and before the
            # rewrite; the v1 bytes are already preserved exactly.
        else:
            _write_backup(backup_path, original_bytes, directory)
        WorkflowStore(directory).save(
            {
                "workflow_store_schema_version": (
                    WORKFLOW_STORE_SCHEMA_VERSION
                ),
                "workflows": {},
            }
        )
    return (
        True,
        "migrated %s to schema version %d; %d version-1 workflow"
        " record(s) RETIRED (exact count) into the preserved"
        " byte-exact backup at %s. Retired workflows lack authority"
        " fields that are never fabricated (exact human intent,"
        " unresolved questions, execution scope), so they can never"
        " be resumed under v2 — each requires a fresh Mission"
        " Authorization"
        % (path, WORKFLOW_STORE_SCHEMA_VERSION, retired, backup_path),
    )
