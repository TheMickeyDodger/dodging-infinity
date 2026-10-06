"""The content-addressed documents of a Mission-bound delivery (Task 8,
slice S-VI; ledger R2-7, R2-9): their closed shapes, their canonical bytes
and their durable store — ONE pure module shared by the Runtime-owned
writer of the verification record (``target_runtime.verification``) and
the Mission-side readers (``mission_control.delivery``), so neither
re-implements the other's format.

Three documents, each stored under its own sha256 (the digest of its
canonical JSON bytes) in the protected artifact directory beside the
workflow store, written atomically and re-hashed on EVERY read (a document
whose bytes do not hash to its name is refused, never repaired):

- the VERIFICATION RECORD: what the Runtime's verification producer
  actually ran and observed — the approved argv, the exit status, the
  digest and size of the complete log it captured (the log bytes are a
  content-addressed blob of their own), wall-clock start/finish and the
  measured duration, the settlement of the owned process group, and the
  exact candidate identity and baseline it was bound to;
- the DELIVERY PROPOSAL: everything the P1-A6 authority will bind (the
  authority template), the Mission parent, the workflow and task
  identity, the verification record digest, and the absolute expiry;
- the DELIVERY DECISION: the canonical document of one client-confirmed
  decision about one exact proposal;
- the DELIVERY BINDING: the one-decision -> one-delivery binding the
  Runtime persists BEFORE the delivery record exists (the decision, its
  accepted evidence, the proposal and the deterministic delivery id).

Nothing here decides, spawns or touches a store other than this artifact
directory.
"""

import hashlib
import json
import os
import secrets

from workflow_authority import record as workflow_record
from workflow_authority.digest import canonical_json_bytes, sha256_hex

ARTIFACT_DIR_NAME = "mission-delivery-artifacts"

# The workflow receipts that name these documents (their ``digest`` IS the
# document's content address; the summary is informational only).
VERIFICATION_TURN_PREFIX = "dverif-"
PROPOSAL_TURN_PREFIX = "dprop-"
BINDING_TURN_PREFIX = "dbind-"
# Task 8 S-VII (Lead disposition L1): the Runtime's durable record of a
# RETRYABLE delivery transport failure that NO P1-A6 receipt carries (it
# happened before the step's receipt — a remote query at the step's
# precheck). One receipt per (delivery, recorded state, problem, step); its
# digest is the identity of that fact (``held_digest``). ``di_delivery_status``
# names it while the delivery record is still at exactly that state.
HELD_TURN_PREFIX = "dheld-"
HELD_SUMMARY_PREFIX = "delivery held: "
# The source-branch preparation's durable provenance (``mission_control
# .delivery``): one receipt per recorded STATE of one attempt, all under
# the attempt's digest (workflow, lease, ref, bound commit). The states:
ATTACH_TURN_PREFIX = "dattach-"
ATTACH_SUMMARY_PREFIX = "delivery preparation "
ATTACH_INTENDED = "intended"      # admitted; the ref creation is about to run
ATTACH_PARTIAL = "partial"        # admitted; the ONE HEAD move is about to run
ATTACH_FAILED = "failed"          # a step refused or not admitted (detail says which)
ATTACH_NO_EFFECT = "no_effect"    # reconciled: the earlier attempt changed nothing
ATTACH_ATTACHED = "attached"      # HEAD names the ref at the bound commit
ATTACH_UNRESOLVED = "unresolved"  # found state not provable; never retried
ATTACH_STATES = (ATTACH_INTENDED, ATTACH_PARTIAL, ATTACH_FAILED, ATTACH_NO_EFFECT,
                 ATTACH_ATTACHED, ATTACH_UNRESOLVED)
LOG_SUFFIX = ".log"
DOCUMENT_SUFFIX = ".json"

VERIFICATION_SCHEMA = "di_verification_record_v1"
VERIFICATION_KEYS = (
    "schema", "workflow_id", "mission_id", "mission_revision",
    "repository_realpath", "command_argv", "exit_status", "log_sha256",
    "log_bytes", "ran_at", "finished_at", "duration_seconds",
    "candidate_identity_digest_sha256", "base_oid", "settlement",
)
SETTLEMENT_SETTLED = "settled"
SETTLEMENT_UNSETTLED = "unsettled"

PROPOSAL_SCHEMA = "di_delivery_proposal_v1"
PROPOSAL_KEYS = (
    "schema", "mission", "workflow_id", "task_id", "authority_template",
    "verification_record_digest_sha256", "proposed_at", "expires_at",
)
PROPOSAL_MISSION_KEYS = (
    "mission_id", "revision", "authorization_id",
    "authorization_digest_sha256", "proposal_digest_sha256",
)

DECISION_SCHEMA = "di_delivery_decision_v1"
DECISION_KEYS = (
    "schema", "mission_id", "revision", "proposal_digest_sha256",
    "candidate_identity_digest_sha256", "decision_id", "confirmed_at",
    "action",
)
DECISION_ACCEPT = "accept"
DECISION_DECLINE = "decline"
DECISION_ACTIONS = (DECISION_ACCEPT, DECISION_DECLINE)

BINDING_SCHEMA = "di_delivery_binding_v1"
BINDING_KEYS = (
    "schema", "mission_id", "revision", "decision_id",
    "decision_document_digest_sha256", "evidence_id",
    "proposal_digest_sha256", "delivery_id",
)

PROBLEM_ARTIFACT_ABSENT = "mission_delivery_artifact_absent"
PROBLEM_ARTIFACT_TAMPERED = "mission_delivery_artifact_tampered"
PROBLEM_ARTIFACT_SHAPE = "mission_delivery_artifact_shape"


class ArtifactError(Exception):
    """An artifact is absent, tampered or malformed; ``problem`` names it."""

    def __init__(self, message, problem):
        super(ArtifactError, self).__init__(message)
        self.problem = problem


def artifact_directory(workflow_store_directory):
    """The artifact directory beside the workflow store (the same
    protected per-user directory)."""
    return os.path.join(workflow_store_directory, ARTIFACT_DIR_NAME)


def canonical_bytes(document):
    return canonical_json_bytes(document)


def document_digest(document):
    return sha256_hex(canonical_bytes(document))


def _is_hex(value, length):
    return (isinstance(value, str) and len(value) == length
            and all(c in "0123456789abcdef" for c in value))


def _write_atomic(directory, name, data):
    os.makedirs(directory, mode=0o700, exist_ok=True)
    path = os.path.join(directory, name)
    temporary = os.path.join(directory, ".%s.%s.tmp" % (name, secrets.token_hex(8)))
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        view = memoryview(data)
        while view:
            written = os.write(descriptor, view)
            view = view[written:]
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    os.replace(temporary, path)
    directory_descriptor = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(directory_descriptor)
    finally:
        os.close(directory_descriptor)
    return path


def store_bytes(directory, data, suffix):
    """Store ``data`` under its own sha256 (idempotent); returns the digest."""
    digest = sha256_hex(bytes(data))
    if not os.path.exists(os.path.join(directory, digest + suffix)):
        _write_atomic(directory, digest + suffix, bytes(data))
    return digest


def adopt_file(directory, partial_path, suffix):
    """Content-address an already-written file (the producer's streamed
    log): hash it, then move it under its digest. Returns ``(digest,
    size)``."""
    hasher = hashlib.sha256()
    size = 0
    with open(partial_path, "rb") as handle:
        while True:
            chunk = handle.read(65536)
            if not chunk:
                break
            hasher.update(chunk)
            size += len(chunk)
    digest = hasher.hexdigest()
    target = os.path.join(directory, digest + suffix)
    if os.path.exists(target):
        os.unlink(partial_path)
    else:
        os.chmod(partial_path, 0o600)
        os.replace(partial_path, target)
    return digest, size


def load_bytes(directory, digest, suffix):
    """The bytes stored under ``digest``, re-hashed; refuses absent or
    tampered content."""
    if not _is_hex(digest, 64):
        raise ArtifactError("%r is not a sha256 digest" % (digest,),
                            PROBLEM_ARTIFACT_SHAPE)
    path = os.path.join(directory, digest + suffix)
    try:
        with open(path, "rb") as handle:
            data = handle.read()
    except FileNotFoundError:
        raise ArtifactError("artifact %s%s is absent" % (digest, suffix),
                            PROBLEM_ARTIFACT_ABSENT)
    if sha256_hex(data) != digest:
        raise ArtifactError("artifact %s%s does not hash to its name"
                            % (digest, suffix), PROBLEM_ARTIFACT_TAMPERED)
    return data


def store_document(directory, document):
    """Store a document under the digest of its canonical bytes."""
    return store_bytes(directory, canonical_bytes(document), DOCUMENT_SUFFIX)


def load_document(directory, digest, schema, keys):
    """The document stored under ``digest``, verified: its bytes hash to
    the digest, ARE its canonical bytes, and it carries exactly ``keys``
    with ``schema``."""
    data = load_bytes(directory, digest, DOCUMENT_SUFFIX)
    try:
        document = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        raise ArtifactError("artifact %s is not JSON" % digest,
                            PROBLEM_ARTIFACT_SHAPE)
    if not isinstance(document, dict) or sorted(document) != sorted(keys) or (
        document.get("schema") != schema
    ):
        raise ArtifactError("artifact %s is not a %s document" % (digest, schema),
                            PROBLEM_ARTIFACT_SHAPE)
    if canonical_bytes(document) != data:
        raise ArtifactError("artifact %s is not in canonical form" % digest,
                            PROBLEM_ARTIFACT_SHAPE)
    return document


def verification_record(workflow_id, mission_id, mission_revision,
                        repository_realpath, command_argv, exit_status,
                        log_sha256, log_bytes, ran_at, finished_at,
                        duration_seconds, candidate_identity, base_oid,
                        settlement):
    return {
        "schema": VERIFICATION_SCHEMA, "workflow_id": workflow_id,
        "mission_id": mission_id, "mission_revision": mission_revision,
        "repository_realpath": repository_realpath,
        "command_argv": list(command_argv), "exit_status": exit_status,
        "log_sha256": log_sha256, "log_bytes": log_bytes, "ran_at": ran_at,
        "finished_at": finished_at, "duration_seconds": duration_seconds,
        "candidate_identity_digest_sha256": candidate_identity,
        "base_oid": base_oid, "settlement": settlement,
    }


def load_verification(directory, digest):
    """A verification record, verified end to end: the document and the
    complete log it names (bytes re-hashed, size matched)."""
    record = load_document(directory, digest, VERIFICATION_SCHEMA,
                           VERIFICATION_KEYS)
    log = load_bytes(directory, record["log_sha256"], LOG_SUFFIX)
    if len(log) != record["log_bytes"]:
        raise ArtifactError("verification log size disagrees with its record",
                            PROBLEM_ARTIFACT_TAMPERED)
    return record


def decision_document(mission_id, revision, proposal_digest, candidate_identity,
                      decision_id, confirmed_at, action):
    return {
        "schema": DECISION_SCHEMA, "mission_id": mission_id, "revision": revision,
        "proposal_digest_sha256": proposal_digest,
        "candidate_identity_digest_sha256": candidate_identity,
        "decision_id": decision_id, "confirmed_at": confirmed_at, "action": action,
    }


def workflow_receipt(prefix, digest, summary, now):
    """The workflow receipt naming the document stored under ``digest``."""
    return {
        "kind": workflow_record.RECEIPT_KIND_EVIDENCE,
        "turn_id": prefix + digest[:12],
        "recorded_at": now,
        "digest": digest,
        "bounded_summary": summary[:workflow_record.MAX_BOUNDED_SUMMARY_CHARS],
    }


def held_digest(delivery_id, state, problem, step):
    """The identity of ONE recorded held-delivery fact."""
    return document_digest({"delivery_id": delivery_id, "state": state,
                            "problem": problem, "step": step})


def held_summary(delivery_id, state, problem, step, detail):
    return "%sdelivery=%s state=%s problem=%s step=%s — %s" % (
        HELD_SUMMARY_PREFIX, delivery_id, state, problem, step, detail)


def parse_held(summary):
    """The fields of a ``held_summary`` (``delivery``, ``state``,
    ``problem``, ``step``, ``detail``), or None for anything else."""
    if not isinstance(summary, str) or not summary.startswith(HELD_SUMMARY_PREFIX):
        return None
    head, separator, detail = summary[len(HELD_SUMMARY_PREFIX):].partition(" — ")
    if not separator:
        return None
    fields = {}
    for token in head.split(" "):
        key, equals, value = token.partition("=")
        if not equals or key not in ("delivery", "state", "problem", "step") or (
            key in fields
        ):
            return None
        fields[key] = value
    if len(fields) != 4:
        return None
    fields["detail"] = detail
    return fields


def workflow_receipts(entry, prefix):
    """Every receipt of ``entry`` whose turn id carries ``prefix``."""
    return [receipt for receipt in entry.get("receipts") or []
            if isinstance(receipt, dict) and isinstance(receipt.get("turn_id"), str)
            and receipt["turn_id"].startswith(prefix)]


def verification_receipt(digest, record, now):
    return workflow_receipt(VERIFICATION_TURN_PREFIX, digest, (
        "delivery verification: exit=%d candidate=%s base=%s settlement=%s"
        % (record["exit_status"], record["candidate_identity_digest_sha256"],
           record["base_oid"], record["settlement"])), now)


def attach_digest(workflow_id, lease_path, ref, head_oid):
    """The identity of ONE source-branch preparation attempt."""
    return document_digest({"workflow_id": workflow_id, "lease": lease_path,
                            "ref": ref, "head": head_oid})


def attach_receipt(digest, state, ref, head_oid, lease_path, now, detail=None):
    return workflow_receipt(ATTACH_TURN_PREFIX, digest, "%s%s: ref=%s head=%s lease=%s%s" % (
        ATTACH_SUMMARY_PREFIX, state, ref, head_oid, lease_path,
        "" if detail is None else " detail=%s" % detail), now)


def attach_states(entry, digest):
    """The recorded states of the preparation attempt ``digest``, in
    order; a receipt whose state is not one of the closed states reads as
    ``unresolved`` (never as progress)."""
    states = []
    for receipt in workflow_receipts(entry, ATTACH_TURN_PREFIX):
        if receipt.get("digest") != digest:
            continue
        summary = receipt.get("bounded_summary") or ""
        state = None
        if summary.startswith(ATTACH_SUMMARY_PREFIX):
            state = summary[len(ATTACH_SUMMARY_PREFIX):].split(":", 1)[0]
        states.append(state if state in ATTACH_STATES else ATTACH_UNRESOLVED)
    return states


def binding_document(mission_id, revision, decision_id, decision_digest,
                     evidence_id, proposal_digest, delivery_id):
    return {
        "schema": BINDING_SCHEMA, "mission_id": mission_id, "revision": revision,
        "decision_id": decision_id,
        "decision_document_digest_sha256": decision_digest,
        "evidence_id": evidence_id, "proposal_digest_sha256": proposal_digest,
        "delivery_id": delivery_id,
    }
