"""Target-Herdr dispatch: the exact stored handoff, nothing else.

Dispatch goes through the EXISTING structured child-spawn bridge
(``herdr.orchestrator.execute_spawn_request``) — no parallel path.
The spawn request the control layer emits toward the target carries
EXACTLY four fields:

- ``target_repo`` — the leased workspace realpath (resolved from the
  protected record, never from a caller);
- ``task`` — ``record["handoff"]["text"]`` BYTE-EXACT: no prefix, no
  suffix, no template, no re-wrap, no normalization. (The bridge
  strips surrounding whitespace; the authority layer makes padded
  handoff text unrepresentable, so that strip is provably an
  identity for every dispatchable record.)
- ``alias`` — a fixed derivation from the workflow id.
- ``preset`` — the fixed DI-owned unattended target execution
  posture, never sourced from mutable workflow or authority content.

Nothing else: no ``rules``, no ``policy``, no ``task_policy``, no
``test_command``, no ``force``, no ``rejection_drill``. The preset
controls agent permission posture only. The child Herdr's own role
contracts, policy resolution, lifecycle, review depth, recovery, and
Git gates apply untouched — the control layer adds no strategy, which
is what makes the target Supervisor demonstrably the FIRST
strategy-bearing component (plan D-5). Bounded corrective follow-ups
use the SAME fixed execution posture through the SAME gate; new
authority content requires a new authorized revision through the full
mission path.
"""

import secrets

from herdr.orchestrator import execute_spawn_request

ALIAS_PREFIX = "di-remote-2-"

# DI-owned unattended execution posture for every remote target
# Herdr. This is trusted Runtime configuration: it is deliberately
# not read from Mission Authorization, handoff text, role output,
# user text, target instructions, or any mutable workflow field.
DI_TARGET_EXECUTION_PRESET = "all-claude"

# The SINGLE source of the unresolved-task-id sentinel (I4):
# ``target_identity_from_spawn`` NEVER returns None — a spawn result
# carrying no usable id falls back to THIS literal. Consumers (the
# Runtime's dispatch-ambiguity predicate) must reference this
# constant, never retype the literal; a contract test ties the
# constant to the fallback behaviour.
UNRESOLVED_TASK_ID = "unknown"

# Hard bound on corrective follow-up dispatches per Mission
# Authorization, never derived from input. Ruling R-2: this is an
# AUTHORIZATION-SCOPE bound (how much corrective dispatch one human
# approval covers), NOT a Herdr review-round limit and NOT a mission
# timeout. Exceeding it transitions the workflow to
# NEEDS_REAUTHORIZATION (durable, visible), never a stranded dead end.
MAX_FOLLOW_UP_DISPATCHES = 2

DISPATCH_RECEIPT_MARKER = "dispatched handoff revision"
# The marker for the bounded failed-acceptance evidence a verification
# turn records when it requests a corrective follow-up.
CORRECTION_RECEIPT_MARKER = "correction requested after verification"
# The marker for the dispatch-time protected-surface baseline receipt
# (ruling R-2): its digest is the framed digest of the control
# repository's protected surfaces AT DISPATCH, and verification later
# requires the live recomputation to byte-match it. Stamped exactly
# once, at the INITIAL dispatch (the semantic anchor is "the control
# machinery the child ran under"; comparing against the FIRST
# dispatch detects any drift across the whole execution window,
# follow-ups included). A workflow with no such receipt was
# dispatched before the baseline existed and fails closed at
# verification — never retro-fitted, never fabricated.
SURFACE_RECEIPT_MARKER = "protected-surface baseline at dispatch"


def surface_receipt(entry, digest, now, turn_id_factory=None):
    """The E-5 receipt binding the dispatch-time protected-surface
    digest. Capability-free: a digest, exact counts nowhere (they
    live in the digest computation), no path."""
    make_turn_id = turn_id_factory or (
        lambda: "surf-" + secrets.token_hex(8)
    )
    return {
        "kind": "evidence",
        "turn_id": make_turn_id(),
        "recorded_at": now,
        "digest": digest,
        "bounded_summary": "%s (framed sha256, exact)" % (
            SURFACE_RECEIPT_MARKER
        ),
    }


def surface_baseline_digest(entry):
    """The dispatch-time protected-surface baseline digest, or None
    when no such receipt exists (a pre-baseline workflow — the
    verification gates fail closed on None; nothing is ever
    fabricated). The FIRST stamped receipt wins: the baseline is a
    dispatch-time datum and is never re-stamped."""
    for receipt in entry["receipts"]:
        if receipt.get("kind") == "evidence" and receipt.get(
            "bounded_summary", ""
        ).startswith(SURFACE_RECEIPT_MARKER):
            return receipt.get("digest")
    return None


def build_spawn_request(entry):
    """The complete four-field spawn request.

    Target, task, and alias are resolved from the protected record;
    the unattended permission posture is the fixed DI-owned Runtime
    constant.
    """
    return {
        "target_repo": entry["workspace_lease"]["path_realpath"],
        "task": entry["handoff"]["text"],
        "alias": ALIAS_PREFIX + entry["workflow_id"],
        "preset": DI_TARGET_EXECUTION_PRESET,
    }


def target_identity_from_spawn(spawn_result, entry, now):
    """The durable target-Herdr identity, bounded, from the spawn
    result (D1). The real bridge returns the task identity twice:
    ``task.id`` and ``child_record.task_id``. Both must be present,
    non-empty strings and agree exactly; otherwise the identity stays
    unresolved. Alias is display-only and is never identity evidence.
    Never stores a capability or a raw result blob."""
    result = spawn_result if isinstance(spawn_result, dict) else {}

    def _bounded(value, fallback):
        if isinstance(value, str) and value.strip():
            return value[:128]
        return fallback

    task = result.get("task")
    child_record = result.get("child_record")
    task_id = task.get("id") if isinstance(task, dict) else None
    recorded_task_id = (
        child_record.get("task_id")
        if isinstance(child_record, dict) else None
    )
    usable_task_id = (
        task_id
        if isinstance(task_id, str)
        and task_id.strip()
        and isinstance(recorded_task_id, str)
        and recorded_task_id.strip()
        and task_id == recorded_task_id
        else UNRESOLVED_TASK_ID
    )

    return {
        "alias": ALIAS_PREFIX + entry["workflow_id"],
        "task_id": _bounded(usable_task_id, UNRESOLVED_TASK_ID),
        "repo": _bounded(
            result.get("repo"),
            entry["target"]["canonical_url"],
        ),
        "dispatched_at": now,
    }


def latest_correction_evidence(entry):
    """The most recent failed-acceptance evidence summary a
    verification turn recorded, or None."""
    for receipt in reversed(entry["receipts"]):
        summary = receipt.get("bounded_summary", "")
        if receipt.get("kind") == "evidence" and summary.startswith(
            CORRECTION_RECEIPT_MARKER
        ):
            return summary
    return None


def build_follow_up_spawn_request(entry):
    """The corrective follow-up spawn request (D6).

    The ``task`` is a CORRECTIVE BRIEF assembled ONLY from the
    record's own already-validated authority fields (objective,
    constraints, acceptance, desired outcome — none of which may
    carry strategy) plus the failed-acceptance evidence a
    verification turn recorded. It carries NO technical solution: the
    text is built from a FIXED template with authority values slotted
    in, so no engineering plan can be introduced here — planning
    returns to the target Supervisor, which remains the first
    strategy-bearing component. Still exactly four fields, including
    the same fixed DI-owned execution posture as the initial dispatch.
    """
    authorization = entry["mission_authorization"]
    correction = latest_correction_evidence(entry) or (
        "verification requested a bounded correction"
    )
    task = (
        "CORRECTIVE FOLLOW-UP for an already-authorized mission. This"
        " is authority and boundaries only — NOT an engineering plan;"
        " the Supervisor owns all technical decisions.\n"
        "\nCORRECTIVE OBJECTIVE\n%s\n"
        "\nFAILED ACCEPTANCE EVIDENCE\n%s\n"
        "\nUNCHANGED CONSTRAINTS\n%s\n"
        "\nDESIRED CORRECTED OUTCOME\n%s\n"
        "\nACCEPTANCE (unchanged)\n%s\n"
    ) % (
        authorization["objective"],
        correction,
        authorization["constraints"],
        authorization["desired_outcome"],
        authorization["acceptance"],
    )
    return {
        "target_repo": entry["workspace_lease"]["path_realpath"],
        "task": task,
        "alias": ALIAS_PREFIX + entry["workflow_id"],
        "preset": DI_TARGET_EXECUTION_PRESET,
    }


def dispatch_count(entry):
    """Exact number of dispatches initiated for this workflow."""
    return sum(
        1 for receipt in entry["receipts"]
        if receipt["kind"] == "evidence"
        and receipt["bounded_summary"].startswith(
            DISPATCH_RECEIPT_MARKER
        )
    )


def dispatch_receipt(entry, now, sequence_number,
                     turn_id_factory=None):
    """The E-5 evidence receipt for one initiated dispatch.

    Capability-free: names the target by owner/repo and the handoff
    by revision and digest — never the workspace path or lease id.
    """
    make_turn_id = turn_id_factory or (
        lambda: "disp-" + secrets.token_hex(8)
    )
    return {
        "kind": "evidence",
        "turn_id": make_turn_id(),
        "recorded_at": now,
        "digest": entry["handoff"]["digest_sha256"],
        "bounded_summary": (
            "%s %d to %s/%s (dispatch %d, exact)" % (
                DISPATCH_RECEIPT_MARKER,
                entry["handoff"]["revision"],
                entry["target"]["owner"],
                entry["target"]["repo"],
                sequence_number,
            )
        ),
    }


def production_spawn(parent_repo, request):
    """The real bridge; hermetic tests inject a recorder instead."""
    return execute_spawn_request(parent_repo, request)
