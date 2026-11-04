/**
 * LABELLED SYNTHETIC request-panel replies for the scan face's New Mission browser tests. Nothing here is live: no request panel, no Codex, no Herdr.
 *
 * Each builder returns an answer in the request panel's OWN wire shape (mission_panel/service.py: `overview`, `send_turn` -> `_turn_view`,
 * `conversation_view`), wrapped as the office's relay wraps it (`diMissionRequestReply`). The words are marked SYNTHETIC so no capture or
 * log can be read as a real request, a real assistant reply or a real proposal.
 */

/** The synthetic conversation, turn and draft digest (each in the panel's own identifier grammar). */
export const SYNTHETIC_CONVERSATION_ID = `cv-${'a'.repeat(32)}`;
export const SYNTHETIC_OTHER_CONVERSATION_ID = `cv-${'c'.repeat(32)}`;
export const SYNTHETIC_TURN_ID = `ct-${'b'.repeat(32)}`;
export const SYNTHETIC_DIGEST = 'd'.repeat(64);
export const SYNTHETIC_SERVER_NOW = 1_800_000_100;

/** A drafted proposal, in the four fields Codex writes plus the ones the panel sets (docs/pixel-agents-office.md "Requesting"). */
export const SYNTHETIC_PROPOSAL = {
  objective: 'SYNTHETIC (a test draft, not a real request): make the readiness probe stable under load',
  target_context: 'the readiness subsystem',
  repository_url: 'https://example.invalid/synthetic-repository.git',
  requested_scope: 'the probe and its tests; no schema change',
  requested_action_scope: ['repository_read', 'engineering_change'],
  requested_delivery_target: null,
};

/** The assistant's prose, as a completed turn carries it (the panel keeps the exact reply; the draft is read from its fenced block). */
export const SYNTHETIC_ASSISTANT_TEXT = 'SYNTHETIC assistant reply: here is a draft proposal for you to read.';

type Json = Record<string, unknown>;

/** One turn in `_turn_view` shape. A reserved turn is in flight with no reply; a completed one carries the reply and, optionally, a draft. */
export function syntheticTurn(userText: string, over: Json = {}): Json {
  return {
    turn_id: SYNTHETIC_TURN_ID,
    conversation_id: SYNTHETIC_CONVERSATION_ID,
    sequence: 1,
    created_at: SYNTHETIC_SERVER_NOW - 5,
    finished_at: null,
    state: 'RESERVED',
    in_flight: true,
    user_text: userText,
    assistant_text: null,
    provider_text: 'untrusted',
    outcome: null,
    draft: null,
    draft_problem: null,
    submission: null,
    questions: { reporting: 'not_applicable', items: [], problem: null },
    reclaimed: null,
    ...over,
  };
}

/** The same turn, completed: the reply and the drafted proposal (`draft`), as the panel records them. */
export function syntheticDraftedTurn(userText: string): Json {
  return syntheticTurn(userText, {
    state: 'COMPLETED',
    in_flight: false,
    finished_at: SYNTHETIC_SERVER_NOW,
    assistant_text: SYNTHETIC_ASSISTANT_TEXT,
    outcome: { status: 'ok', error_code: null, detail: null },
    draft: { proposal: SYNTHETIC_PROPOSAL, proposal_digest_sha256: SYNTHETIC_DIGEST, runnable_problem: null },
    questions: { reporting: 'read', items: [], problem: null },
  });
}

/** `conversation_view`: the turns of one conversation, with the panel's own bounds (nothing left out). */
export function syntheticConversation(turns: readonly Json[], conversationId = SYNTHETIC_CONVERSATION_ID): Json {
  return {
    ok: true,
    conversation_id: conversationId,
    turns_total: turns.length,
    turns_shown: turns.length,
    turns_omitted: 0,
    turns_omitted_by_turn_limit: 0,
    turns_omitted_by_byte_limit: 0,
    turns_truncated: false,
    view_limits: { turns: 50, bytes: 3 * 1024 * 1024 },
    turns: [...turns],
  };
}

/** One recent conversation as `overview` lists it (`first_words`: the first 120 characters of its first ask). */
export function syntheticConversationSummary(conversationId: string, firstWords: string): Json {
  return {
    conversation_id: conversationId,
    turns: 1,
    last_activity: SYNTHETIC_SERVER_NOW - 60,
    first_words: firstWords,
    reclaimed: null,
    awaiting_answer: false,
    questions_reporting: 'read',
    questions_problem: null,
    awaiting_turn_id: null,
    awaiting_questions: [],
    latest_turn_state: 'COMPLETED',
  };
}

/** `overview`: no requests; the recent conversations given (none by default). */
export function syntheticOverview(conversations: readonly Json[] = []): Json {
  return {
    ok: true,
    server_now: SYNTHETIC_SERVER_NOW,
    capabilities: { decision: {} },
    requests: [],
    requests_truncated: false,
    conversations: [...conversations],
  };
}

/** The relay's envelope around one answer, addressed to the request the page sent. */
export function relayReply(seq: unknown, op: string, answer: Json): Json {
  return { type: 'diMissionRequestReply', seq, op, state: 'ok', problem: null, reason: null, answer };
}

/** SYNTHETIC proposals of record, for the request workspace's own gates (Approve on one awaiting a decision; Start on one approved). */
export const SYNTHETIC_PENDING_REF = `lr-${'5'.repeat(32)}`;
export const SYNTHETIC_APPROVED_REF = `lr-${'6'.repeat(32)}`;
const SYNTHETIC_MISSION = (ref: string) => `mn-${ref.slice(3)}`;
const recordProposal = (): Json => ({
  objective: 'SYNTHETIC (a test record, not a real request): make the readiness probe stable',
  target_context: 'readiness',
  repository_url: 'https://example.invalid/synthetic-repository.git',
  requested_scope: 'the probe only',
  requested_action_scope: ['engineering_change', 'repository_read'],
  requested_delivery_target: null,
  proof_contract: {
    requirements: [
      { key: 'run_result', description: 'The result is bound to the run.', evidence_kinds: ['verification_record'], required_artifact_keys: [], max_evidence_age_seconds: 3600 },
    ],
    required_artifacts: [],
    required_dependencies: [],
    required_resource_readiness: [],
    degradation_policy: { permitted_blocker_keys: [] },
    continuation_budget: { max_attempts: 1, max_checkpoints: 2 },
  },
});
const recordLifecycle = (ref: string): Json =>
  ref === SYNTHETIC_PENDING_REF
    ? { stage: 'of_record', label: 'Waiting for your decision', detail: 'Nothing has been approved.', terminal: false, actions: ['decide', 'withdraw'], notice: null, approval_expires_at: null, provenance: null, residual_risk: null }
    : { stage: 'approved', label: 'Approved, not started', detail: 'Nothing has started.', terminal: false, actions: ['dispatch'], notice: null, approval_expires_at: SYNTHETIC_SERVER_NOW + 900, provenance: null, residual_risk: null };

/** One request as `overview` lists it. */
export function syntheticRequestSummary(ref: string): Json {
  return {
    request_ref: ref,
    mission_id: SYNTHETIC_MISSION(ref),
    revision: 1,
    created_at: SYNTHETIC_SERVER_NOW - 100,
    objective: (recordProposal().objective as string) + (ref === SYNTHETIC_APPROVED_REF ? ' (approved)' : ''),
    proposal_digest_sha256: SYNTHETIC_DIGEST,
    lifecycle: recordLifecycle(ref),
  };
}
/** One request as `request` reads it. */
export function syntheticRequest(ref: string): Json {
  return {
    ok: true,
    request_ref: ref,
    mission_id: SYNTHETIC_MISSION(ref),
    revision: 1,
    proposal: recordProposal(),
    proposal_digest_sha256: SYNTHETIC_DIGEST,
    created_at: SYNTHETIC_SERVER_NOW - 100,
    lifecycle: recordLifecycle(ref),
    lineage: { supersedes: null, superseded_by: null, turn_id: null },
    cancellation: null,
    attestation: null,
    outcome: null,
    run: null,
  };
}
/** The frozen decision offer for the request awaiting a decision (`offer`). */
export function syntheticOffer(ref: string): Json {
  return {
    ok: true,
    request_ref: ref,
    mission_id: SYNTHETIC_MISSION(ref),
    revision: 1,
    proposal_digest_sha256: SYNTHETIC_DIGEST,
    offer_digest_sha256: 'f'.repeat(64),
    expires_at: SYNTHETIC_SERVER_NOW + 900,
    issued_at: SYNTHETIC_SERVER_NOW,
    approved_action_scope: ['engineering_change', 'repository_read'],
    approved_delivery_targets: [],
    repository_url: 'https://example.invalid/synthetic-repository.git',
    approval_valid_seconds: 900,
    server_now: SYNTHETIC_SERVER_NOW,
  };
}

/** `overview` with requests listed (and recent conversations). */
export function syntheticOverviewWithRequests(refs: readonly string[], conversations: readonly Json[] = []): Json {
  return { ...syntheticOverview(conversations), requests: refs.map(syntheticRequestSummary) };
}

/**
 * The request workspace's AUTHORITY controls, by their exact labels (DiMissionRequest.tsx: the draft's Submit, the decision's Approve, Decline, Reject and Ask for changes,
 * Withdraw, and the run's Start): none may appear on New Mission. Exact, so "Start a new conversation" (which starts nothing) is not mistaken for Start.
 */
export const GATE_LABEL = /^(Submit as\b|Approve$|Start the run$|Decline$|Reject\b|Confirm: reject|Withdraw\b|Ask for changes|Request changes)/;

/** The ops that WRITE (or decide, withdraw or run): New Mission may send exactly one kind, a drafting `turn`, and never any other. */
export const AUTHORITY_OPS = ['submit', 'decide', 'decline', 'reject', 'requestChanges', 'withdraw', 'run', 'dispatch', 'deleteLocalData'] as const;
