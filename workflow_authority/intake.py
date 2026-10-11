"""The transport-neutral Mission Authorization intake seam.

A fresh restrictive planning turn returns a Mission Authorization
DOCUMENT. This module is the ONE place that validates such a document
against the closed schema and the transport's pinned control
repository, and the ONE place that builds the durable workflow record
from a validated document. Both transports call it:

- the Telegram lane, through thin wrappers that keep their existing
  names, refusals, problem codes and output in
  ``telegram_operator.mission``;
- the Grok Bot lane (I2 of task 20261010-164104-1761f6), through
  ``grok_bot.conforming``.

One validator, two callers: a copied authority validator drifts, and a
drifted validator means the one ENFORCED differs from the one REVIEWED.

Why it lives HERE. Everything it needs is already in
``workflow_authority`` (the closed key schema, the record, the single
renderer, the canonicalizer), and it imports nothing else but the
standard library: no transport, so ``workflow_authority`` still imports
no transport, and neither transport has to import the other.

Authority split, enforced fail-closed:

- Codex authors the AUTHORITY content and the target/baseline/handoff
  bindings. It must NOT mint transport bindings: ``workflow_id`` and
  ``telegram_approval`` (the closed schema's approval-binding key) must
  arrive null and are stamped by the adapter. ``human_intent`` is in the
  same class: the exact original human request is stamped by the adapter
  from what the transport actually accepted, never supplied by the
  Operator. Each of the three has its OWN problem code here; the
  Telegram lane reports the first two under its one historical code.
- ``control.repository_realpath`` must equal the transport's own
  configured control repository exactly. Cross-repository means the
  TARGET may differ, never the control.
- ``delivery_authority`` must be exactly the string ``"none"``.
"""

import json

from workflow_authority import canonical as canonical_module
from workflow_authority import record as record_module
from workflow_authority import rendering as rendering_module
from workflow_authority.authorization import (
    validate_authorization_structure,
    validate_authorization_values_deep,
)

# One-shot mission approval validity, seconds from creation. Hard
# constant, the single source for both transports (the Telegram lane
# re-exports it under its existing name).
MISSION_APPROVAL_VALIDITY_SECONDS = 15 * 60

PROBLEM_BODY_INVALID_JSON = "mission_body_invalid_json"
# The Operator minted a transport binding. Distinct codes per key, so a
# caller can tell exactly which binding was minted.
PROBLEM_MINTED_WORKFLOW_ID = "mission_codex_minted_workflow_id"
PROBLEM_MINTED_APPROVAL_BINDING = "mission_codex_minted_approval_binding"
PROBLEM_MINTED_INTENT = "mission_codex_minted_human_intent"
PROBLEM_AUTHORITY_TEXT = "mission_authority_text_invalid"
PROBLEM_CONTROL_SHAPE = "mission_control_shape"
PROBLEM_CONTROL_PATH = "mission_control_path_character"
PROBLEM_CONTROL_MISMATCH = "mission_control_mismatch"
PROBLEM_BASELINE_REF = "mission_baseline_ref_grammar"
PROBLEM_TARGET_SHAPE = "mission_target_shape"
PROBLEM_TARGET_HOST = "mission_target_host"
PROBLEM_TARGET_URL = "mission_target_url_not_canonical"
PROBLEM_ISSUE_SHAPE = "mission_issue_or_pr_shape"
PROBLEM_BASELINE_SHAPE = "mission_baseline_shape"
PROBLEM_HANDOFF_SHAPE = "mission_handoff_shape"
PROBLEM_REVISION_SHAPE = "mission_revision_shape"

# The minted-binding code for each adapter-stamped binding key.
MINTED_BINDING_PROBLEMS = (
    ("workflow_id", PROBLEM_MINTED_WORKFLOW_ID),
    ("telegram_approval", PROBLEM_MINTED_APPROVAL_BINDING),
)


class MissionError(Exception):
    """A Mission Authorization failed validation; message actionable."""

    def __init__(self, message, problem):
        super(MissionError, self).__init__(message)
        self.problem = problem


def _require_text(document, key):
    value = document.get(key)
    if not isinstance(value, str) or not value.strip():
        raise MissionError(
            "Mission Authorization %r must be a non-empty string"
            % (key,),
            PROBLEM_AUTHORITY_TEXT,
        )
    if len(value) > record_module.MAX_AUTHORITY_FIELD_CHARS:
        raise MissionError(
            "Mission Authorization %r is %d characters; the hard"
            " bound is %d and the document is refused, not truncated"
            % (key, len(value), record_module.MAX_AUTHORITY_FIELD_CHARS),
            PROBLEM_AUTHORITY_TEXT,
        )
    return value


def validate_mission_document(body, configured_repository):
    """Parse and validate a Mission Authorization body, failing closed.

    Runs the closed-structure guards, the deep nested-strategy walk,
    and every VALUE check the adapter can make deterministically.
    Returns the validated document. Raises MissionError (or
    AuthorizationError from the structural guards) on every failure;
    never partially accepts.
    """
    try:
        document = json.loads(body)
    except (TypeError, ValueError):
        raise MissionError(
            "Mission Authorization body is not valid JSON",
            PROBLEM_BODY_INVALID_JSON,
        )
    validate_authorization_structure(document)
    validate_authorization_values_deep(document)
    # Transport bindings are the ADAPTER's authority: Codex must not
    # mint them.
    for key, problem in MINTED_BINDING_PROBLEMS:
        if document.get(key) is not None:
            raise MissionError(
                "Mission Authorization %r must be null: transport"
                " bindings are stamped by the adapter, never minted by"
                " the Operator" % (key,),
                problem,
            )
    # The exact original human intent is in the same adapter-stamped
    # class, with its own problem code: a document that supplies or
    # alters it is refused outright — the Operator can never change
    # what the human said.
    if document.get("human_intent") is not None:
        raise MissionError(
            "Mission Authorization 'human_intent' must be null: the"
            " exact original human intent is stamped by the adapter"
            " from the text the transport accepted, never minted or"
            " altered by the Operator",
            PROBLEM_MINTED_INTENT,
        )
    for key in rendering_module.AUTHORITY_CONTENT_KEYS:
        _require_text(document, key)
    control = document.get("control")
    if not isinstance(control, dict) or set(control.keys()) != {
        "repository_realpath", "policy_digest_sha256"
    }:
        raise MissionError(
            "Mission Authorization control must be an object with"
            " exactly repository_realpath and policy_digest_sha256",
            PROBLEM_CONTROL_SHAPE,
        )
    # Round-02 F-6: the control path is rendered UNQUOTED on a
    # binding line, so a path carrying control characters or line
    # terminators is refused with its own code, BEFORE the mismatch
    # check (so this layer is independently load-bearing, not
    # shadowed by the exact-equality pin below). The line-free rule
    # is the SHARED public helper — single source, two layers, each
    # with its own code.
    realpath = control["repository_realpath"]
    if isinstance(realpath, str) and (
        record_module.path_character_problem(realpath) is not None
    ):
        raise MissionError(
            "Mission Authorization control repository path %s; a path"
            " with line structure could forge displayed binding"
            " lines. Refused"
            % record_module.path_character_problem(realpath),
            PROBLEM_CONTROL_PATH,
        )
    if control["repository_realpath"] != configured_repository:
        raise MissionError(
            "Mission Authorization names control repository %r; this"
            " adapter is pinned to %r. Refused"
            % (control["repository_realpath"], configured_repository),
            PROBLEM_CONTROL_MISMATCH,
        )
    policy_digest = control["policy_digest_sha256"]
    if (
        not isinstance(policy_digest, str)
        or len(policy_digest) != 64
        or any(ch not in "0123456789abcdef" for ch in policy_digest)
    ):
        raise MissionError(
            "Mission Authorization policy digest must be 64 lowercase"
            " hex characters",
            PROBLEM_CONTROL_SHAPE,
        )
    target = document.get("target")
    if not isinstance(target, dict) or set(target.keys()) != {
        "canonical_host", "owner", "repo", "canonical_url"
    }:
        raise MissionError(
            "Mission Authorization target must be an object with"
            " exactly canonical_host, owner, repo, canonical_url",
            PROBLEM_TARGET_SHAPE,
        )
    if target["canonical_host"] != canonical_module.CANONICAL_TARGET_HOST:
        raise MissionError(
            "Mission Authorization target host %r is not the canonical"
            " %r" % (target["canonical_host"],
                     canonical_module.CANONICAL_TARGET_HOST),
            PROBLEM_TARGET_HOST,
        )
    # The ONE canonicalizer (workflow_authority.canonical) validates
    # the URL; its distinct problem codes propagate so every hostile
    # shape (confusable host, traversal, multi-target, ...) is
    # refused with its own code. The displayed owner/repo must equal
    # the canonical parse, or the URL could name a different
    # repository than the displayed identity.
    try:
        parsed_target = canonical_module.canonicalize_repository_url(
            target["canonical_url"]
        )
    except canonical_module.CanonicalizationError as exc:
        raise MissionError(str(exc), exc.problem)
    if (
        target["owner"] != parsed_target.owner
        or target["repo"] != parsed_target.repo
    ):
        raise MissionError(
            "Mission Authorization target owner/repo (%r, %r) do not"
            " match the canonical parse of the target URL (%r, %r)"
            % (target["owner"], target["repo"], parsed_target.owner,
               parsed_target.repo),
            PROBLEM_TARGET_URL,
        )
    issue_or_pr = document.get("issue_or_pr")
    if issue_or_pr is not None and (
        not isinstance(issue_or_pr, dict)
        or set(issue_or_pr.keys()) != {"kind", "number"}
        or issue_or_pr["kind"] not in record_module.ISSUE_OR_PR_KINDS
        or isinstance(issue_or_pr["number"], bool)
        or not isinstance(issue_or_pr["number"], int)
        or issue_or_pr["number"] < 1
    ):
        raise MissionError(
            "Mission Authorization issue_or_pr must be null"
            " (repository-only target) or {kind: issue|pr, number:"
            " positive integer}",
            PROBLEM_ISSUE_SHAPE,
        )
    baseline = document.get("baseline")
    if (
        not isinstance(baseline, dict)
        or set(baseline.keys()) != {"ref", "commit_sha"}
        or not isinstance(baseline["ref"], str)
        or not baseline["ref"]
        or not isinstance(baseline["commit_sha"], str)
        or len(baseline["commit_sha"]) != 40
        or any(
            ch not in "0123456789abcdef"
            for ch in baseline["commit_sha"]
        )
    ):
        raise MissionError(
            "Mission Authorization baseline must be {ref: non-empty"
            " string, commit_sha: 40 lowercase hex}",
            PROBLEM_BASELINE_SHAPE,
        )
    # Round-02 F-6: the ref is Codex-authored and rendered UNQUOTED
    # on a binding line — the closed git ref grammar (single source:
    # workflow_authority.record.baseline_ref_grammar_problem) is
    # enforced HERE, before any record exists, with this layer's own
    # problem code; the record layer enforces it again on every
    # load/save.
    reason = record_module.baseline_ref_grammar_problem(
        baseline["ref"]
    )
    if reason is not None:
        raise MissionError(
            "Mission Authorization baseline ref %s; a ref outside"
            " the closed git ref grammar could forge displayed"
            " binding lines. Refused" % reason,
            PROBLEM_BASELINE_REF,
        )
    handoff = document.get("handoff")
    if (
        not isinstance(handoff, dict)
        or set(handoff.keys()) != {"revision", "text"}
        or isinstance(handoff["revision"], bool)
        or not isinstance(handoff["revision"], int)
        or handoff["revision"] < 1
        or not isinstance(handoff["text"], str)
        or not handoff["text"].strip()
        or handoff["text"] != handoff["text"].strip()
    ):
        raise MissionError(
            "Mission Authorization handoff must be {revision: positive"
            " integer, text: non-empty string with no leading or"
            " trailing whitespace (byte-exact dispatch requirement)}",
            PROBLEM_HANDOFF_SHAPE,
        )
    if len(handoff["text"]) > record_module.MAX_AUTHORITY_TEXT_CHARS:
        raise MissionError(
            "Mission Authorization handoff text is %d characters; the"
            " hard bound is %d and the document is refused, not"
            " truncated" % (
                len(handoff["text"]),
                record_module.MAX_AUTHORITY_TEXT_CHARS,
            ),
            PROBLEM_HANDOFF_SHAPE,
        )
    revision = document.get("revision")
    if (
        isinstance(revision, bool)
        or not isinstance(revision, int)
        or revision < 1
    ):
        raise MissionError(
            "Mission Authorization revision must be a positive integer",
            PROBLEM_REVISION_SHAPE,
        )
    return document


def void_workflow(entry):
    """Durably-voidable step on ONE record: supersede + terminal phase.

    The approval is marked superseded (every later decision on it is
    refused) and a non-terminal record moves to BLOCKED, a
    table-allowed transition. Mutates ``entry`` only: the caller holds
    the store lock and must SAVE the store afterwards — a void that
    only mutates memory is the recorded fail-closed-state-asserted-only-
    in-memory class. Shared by both transports (the Telegram lane's
    ``void_mission`` wraps it, unchanged).
    """
    entry["approval"]["superseded"] = True
    if entry["phase"] not in record_module.TERMINAL_PHASES:
        record_module.apply_transition(
            entry, record_module.PHASE_BLOCKED
        )


def build_workflow_record(document, human_intent, now, workflow_id,
                          nonce, telegram_user_id=None,
                          telegram_chat_id=None, origin=None):
    """Build the durable PLANNED workflow record for a validated mission.

    ``human_intent`` is the ADAPTER-stamped exact text the transport
    accepted; the record stores it byte-exact and the rendering binds
    it. The transport bindings come from the CALLER, never from the
    document: the Telegram lane passes its user and chat ids (and no
    origin); a relay lane passes its ``origin`` (and no Telegram ids).
    The rendered text is computed inside ``record.new_record`` by the
    single renderer, so the render binding holds by construction. The
    approval expires ``MISSION_APPROVAL_VALIDITY_SECONDS`` after ``now``.
    Raises ``record.RecordError`` when any bound is exceeded (the
    rendered text over the authority-text bound included) — the caller
    refuses honestly and nothing is persisted.
    """
    issue_or_pr = document["issue_or_pr"]
    return record_module.new_record(
        workflow_id=workflow_id,
        human_intent=human_intent,
        repository_realpath=(
            document["control"]["repository_realpath"]
        ),
        policy_digest_sha256=(
            document["control"]["policy_digest_sha256"]
        ),
        canonical_host=document["target"]["canonical_host"],
        owner=document["target"]["owner"],
        repo=document["target"]["repo"],
        canonical_url=document["target"]["canonical_url"],
        issue_or_pr_kind=(
            None if issue_or_pr is None else issue_or_pr["kind"]
        ),
        issue_or_pr_number=(
            None if issue_or_pr is None else issue_or_pr["number"]
        ),
        baseline_ref=document["baseline"]["ref"],
        baseline_commit_sha=document["baseline"]["commit_sha"],
        objective=document["objective"],
        constraints=document["constraints"],
        rules=document["rules"],
        desired_outcome=document["desired_outcome"],
        acceptance=document["acceptance"],
        unresolved_questions=document["unresolved_questions"],
        execution_scope=document["execution_scope"],
        mission_revision=document["revision"],
        telegram_user_id=telegram_user_id,
        telegram_chat_id=telegram_chat_id,
        approval_nonce=nonce,
        approval_created_at=now,
        approval_expires_at=now + MISSION_APPROVAL_VALIDITY_SECONDS,
        handoff_revision=document["handoff"]["revision"],
        handoff_text=document["handoff"]["text"],
        origin=origin,
    )
