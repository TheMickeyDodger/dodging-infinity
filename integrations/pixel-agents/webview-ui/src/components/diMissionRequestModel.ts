/**
 * Item 4 — Mission requests: the panel's model. Pure functions and a reducer; no React, no DOM,
 * no transport. Everything a window shows or may do is decided here, so it can be tested without a
 * browser and a renderer cannot disagree with it.
 *
 * The standing rules, each enforced by a function below:
 *
 *  - What the adapter sends is UNTRUSTED data (a provider's words, a person's words, a team's
 *    result among it). It is parsed defensively, shown as inert text, and never treated as an
 *    action or a decision.
 *  - A DISPLAY IS FROZEN. The proposal a person is reading keeps its Mission, revision and digest.
 *    If the records move to a different proposal, the display is INVALIDATED and decisions are
 *    withheld until the person chooses to read the current one; it is never silently re-pointed,
 *    because that would let someone approve something other than what they read.
 *  - A window offers only the actions the records support (`lifecycle.actions`), and holds an
 *    action it already asked for until the answer arrives.
 *  - A write whose outcome is unknown is shown as UNKNOWN and is never replayed here: the window
 *    re-reads the record.
 *  - Nothing is animated as a success that has not been recorded.
 */
import type {
  DiMissionRequest,
  DiMissionRequestOp,
  DiMissionRequestReply,
} from '../../../core/src/messages.js';

// ---------------------------------------------------------------------------------------------
// Constants

/** How often the panel re-reads the overview while connected (closed: only for the entry). */
export const MR_POLL_CLOSED_MS = 6000;
/** ... while the workspace is open. */
export const MR_POLL_OPEN_MS = 3000;
/** ... the open conversation while a turn is with the provider. */
export const MR_POLL_TURN_MS = 1200;
/** The longest a notice stays in the log of notices. */
export const MR_MAX_NOTICES = 6;
/** The most characters of a turn the composer sends (the adapter's bound, in code points). */
export const MR_MAX_TEXT_CHARS = 8000;
/** Lines of a long text shown before "Show the rest". */
export const MR_READING_LINES = 14;
export const MR_READING_LINES_WIDE = 40;
/** The word the person types. The adapter's rule is exact; this mirrors it for the control. */
export const MR_DECISION_WORD = 'approve';
export const MR_DECISION_WORDS: readonly string[] = ['approve', 'approved'];
/** Below this much room beside the office panel, the workspace covers the world and says so. */
export const MR_MIN_BESIDE_PX = 360;
export const MR_PANE_PX = 600;
export const MR_PANE_WIDE_PX = 960;
/** The width of the world to keep visible beside the workspace, where the window allows it. */
export const MR_WORLD_KEEP_PX = 240;
/**
 * The narrowest pane that puts the request list BESIDE the open request in wider reading: the 300px list, the 12px gap, the 24px of
 * padding, and at least 300px left for the open request. Below it the list stacks above the request, as in ordinary reading.
 */
export const MR_SPLIT_MIN_PX = 640;

// ---------------------------------------------------------------------------------------------
// The adapter's answers, as untrusted JSON, parsed into the shapes the panel uses.

import type { MrProofRules } from './diMissionRequestContract.js';
import { proofRulesAbsentWords, readProofRules } from './diMissionRequestContract.js';
import type { MrStartCause } from './diMissionRequestStartCause.js';
import { parseStartCause } from './diMissionRequestStartCause.js';

type Json = Record<string, unknown>;

const isObject = (v: unknown): v is Json =>
  typeof v === 'object' && v !== null && !Array.isArray(v);
const str = (v: unknown): string | null => (typeof v === 'string' ? v : null);
const int = (v: unknown): number | null =>
  typeof v === 'number' && Number.isInteger(v) ? v : null;
const strings = (v: unknown): string[] =>
  Array.isArray(v) ? v.filter((x): x is string => typeof x === 'string') : [];

export type MrAction =
  | 'decide'
  | 'withdraw'
  | 'check_approval'
  | 'dispatch'
  | 'observe'
  | 'reconcile'
  | 'verify'
  | 'result';
const ACTIONS: readonly MrAction[] = [
  'decide',
  'withdraw',
  'check_approval',
  'dispatch',
  'observe',
  'reconcile',
  'verify',
  'result',
];

export interface MrLifecycle {
  stage: string;
  label: string;
  detail: string;
  terminal: boolean;
  actions: MrAction[];
  notice: string | null;
  approvalExpiresAt: number | null;
  provenanceProof: string | null;
  residualRisk: string | null;
}

export interface MrProposal {
  objective: string;
  targetContext: string;
  repositoryUrl: string | null;
  requestedScope: string;
  actionScope: string[];
  deliveryTarget: string | null;
  /**
   * The proof rules the proposal binds, read under the canonical closed shape (see
   * `diMissionRequestContract.ts`). A proposal whose rules, or any other field, this window cannot show is
   * marked `unrepresented`, and approving it from here is refused.
   */
  proof: MrProofRules;
}

export interface MrAttestation {
  state: string;
  attestedAt: number | null;
  relayedReply: string | null;
  provenanceProof: string | null;
  residualRisk: string | null;
  expiresAt: number | null;
}

export interface MrObservation {
  at: number | null;
  targetStopped: boolean | null;
  taskStatus: string | null;
}

export interface MrRun {
  phase: string;
  hold: boolean;
  holdStatement: string | null;
  paused: boolean;
  outcome: string | null;
  firstObservation: MrObservation | null;
  latestObservation: MrObservation | null;
  engineeringVerified: boolean;
  progressNote: string | null;
  delivered: boolean;
  deliveryBasis: string | null;
  blockers: Array<{ code: string; detail: string }>;
}

export interface MrRequest {
  requestRef: string;
  missionId: string;
  revision: number;
  proposal: MrProposal | null;
  digest: string;
  createdAt: number;
  lifecycle: MrLifecycle;
  supersedes: string | null;
  supersededBy: string | null;
  cancelledAt: number | null;
  attestation: MrAttestation | null;
  run: MrRun | null;
  /**
   * Why an in-doubt Start is in doubt, when the panel could bind a retained startup diagnostic to this exact attempt
   * (see `diMissionRequestStartCause.ts`). Null whenever the panel sent none or sent something this window cannot
   * account for. It is a report, derived by the panel at read time, never a decision input.
   */
  startCause: MrStartCause | null;
}

export interface MrSummary {
  requestRef: string;
  missionId: string;
  revision: number;
  createdAt: number;
  objective: string;
  digest: string;
  stage: string;
  label: string;
  terminal: boolean;
  actions: MrAction[];
  notice: string | null;
}

/** The closed vocabulary of the progression probe (anything else reads as unreadable). */
export const MR_PROGRESSION_STATES = [
  'running',
  'stopped',
  'stale',
  'mismatch',
  'unreadable',
  'failing',
] as const;
export type MrProgressionState = (typeof MR_PROGRESSION_STATES)[number];

/** Whether a progression runtime is passing for this panel: evidence about a process, never about a Mission. */
export interface MrProgression {
  state: MrProgressionState;
  detail: string;
  passes: number | null;
  intervalSeconds: number | null;
  lastPassAgeSeconds: number | null;
}

export interface MrCapabilities {
  runAvailable: boolean;
  /** null: this panel cannot start work, so it has no progression to report. */
  progression: MrProgression | null;
  repositoryUrl: string | null;
  deliveryAuthority: string;
  decisionProof: string;
  decisionPrincipal: string;
  approvalValidSeconds: number;
  scopeMeanings: Record<string, string>;
}

/**
 * What the panel says it read from a conversation's newest completed reply for questions. `not_reported`
 * is a panel that predates the field (or a turn recorded before it read for questions): it is NOT the
 * same as `read` with no questions. `unreadable` is a value this window cannot account for.
 */
export type MrQuestionsReporting = 'read' | 'not_reported' | 'not_applicable' | 'unreadable';

/** `mission_panel.journal.MAX_QUESTIONS` / `MAX_QUESTION_CHARS`, restated and pinned equal by test. */
export const MR_MAX_QUESTIONS = 3;
export const MR_MAX_QUESTION_CHARS = 280;

export interface MrConversationSummary {
  conversationId: string;
  turns: number;
  lastActivity: number;
  firstWords: string;
  /** The panel's own derivation: the newest completed reply asked questions and nothing came after. */
  awaitingAnswer: boolean;
  questionsReporting: MrQuestionsReporting;
  questionsProblem: string | null;
  awaitingTurnId: string | null;
  /** UNTRUSTED display text declared by the assistant; never authority, never parsed. */
  awaitingQuestions: string[];
  latestTurnState: string | null;
}

export interface MrOverview {
  serverNow: number;
  capabilities: MrCapabilities;
  requests: MrSummary[];
  requestsTruncated: boolean;
  conversations: MrConversationSummary[];
}

export interface MrDraft {
  proposal: MrProposal;
  digest: string;
  runnableProblem: string | null;
}

export interface MrSubmission {
  state: string;
  requestRef: string | null;
  supersedes: string | null;
  problem: string | null;
  possibleMatches: string[];
}

export interface MrTurn {
  turnId: string;
  conversationId: string;
  sequence: number;
  state: string;
  inFlight: boolean;
  userText: string;
  assistantText: string | null;
  outcomeStatus: string | null;
  outcomeCode: string | null;
  outcomeDetail: string | null;
  draft: MrDraft | null;
  draftProblem: string | null;
  submission: MrSubmission | null;
  /**
   * The questions the panel READ in this completed reply's `mission-questions` block (its own validated extraction, never re-parsed here).
   * Empty when none were declared, when the panel version predates the field, or when the block was not readable: then the reply is shown as written.
   */
  declaredQuestions: string[];
  /**
   * What the panel said about questions in this reply, for DISPLAY only: `declared` (it read them), `none_declared` (it read the reply and found none), `not_reported`
   * (a panel that predates the field, or no report) or `refused` (it read a block it would not accept). Only a `not_reported` or `none_declared` reply may be shown
   * with its technical blocks folded away; a `refused` reply is always shown exactly as written. Nothing here is a count, an authority or a state.
   */
  questionsStatus: MrTurnQuestionsStatus;
}
export type MrTurnQuestionsStatus = 'declared' | 'none_declared' | 'not_reported' | 'refused';

/**
 * The decision OFFER a window displays: FROZEN by the adapter when first shown and shared by every
 * window while it is live. A decision submits exactly this and binds exactly its absolute expiry.
 */
export interface MrOffer {
  requestRef: string;
  missionId: string;
  revision: number;
  digest: string;
  /** The display digest: the digest of every field below that is bound. */
  offerDigest: string;
  expiresAt: number;
  issuedAt: number;
  scope: string[];
  deliveryTargets: string[];
  repositoryUrl: string | null;
  approvalValidSeconds: number;
  provenanceProof: string | null;
  residualRisk: string | null;
  serverNow: number;
}

export interface MrResult {
  recoverable: boolean;
  reason: string | null;
  resultText: string | null;
  resultTruncated: boolean;
  reviewText: string | null;
  reviewTruncated: boolean;
  delivered: boolean;
  reviewEvidence: string | null;
  resultDigest: string | null;
  reviewDigest: string | null;
}

function parseProposal(v: unknown): MrProposal | null {
  if (!isObject(v)) return null;
  const objective = str(v.objective);
  const requestedScope = str(v.requested_scope);
  if (objective === null || requestedScope === null) return null;
  return {
    objective,
    targetContext: str(v.target_context) ?? '',
    repositoryUrl: str(v.repository_url),
    requestedScope,
    actionScope: strings(v.requested_action_scope),
    deliveryTarget: str(v.requested_delivery_target),
    proof: readProofRules(v),
  };
}

function parseLifecycle(v: unknown): MrLifecycle | null {
  if (!isObject(v)) return null;
  const stage = str(v.stage);
  const label = str(v.label);
  const detail = str(v.detail);
  if (stage === null || label === null || detail === null || typeof v.terminal !== 'boolean')
    return null;
  const provenance = isObject(v.provenance) ? v.provenance : null;
  return {
    stage,
    label,
    detail,
    terminal: v.terminal,
    // Only the actions this panel knows how to offer: an unknown one is dropped, never guessed.
    actions: strings(v.actions).filter((a): a is MrAction =>
      (ACTIONS as readonly string[]).includes(a),
    ),
    notice: str(v.notice),
    approvalExpiresAt: int(v.approval_expires_at),
    provenanceProof: provenance ? str(provenance.proof) : null,
    residualRisk: str(v.residual_risk),
  };
}

function parseObservation(v: unknown): MrObservation | null {
  if (!isObject(v)) return null;
  return {
    at: int(v.at),
    targetStopped: typeof v.target_stopped === 'boolean' ? v.target_stopped : null,
    taskStatus: str(v.task_status),
  };
}

function parseRun(v: unknown): MrRun | null {
  if (!isObject(v) || str(v.phase) === null) return null;
  const pending = isObject(v.pending_proof) ? v.pending_proof : null;
  const delivery = isObject(v.delivery) ? v.delivery : null;
  const blockers = pending && Array.isArray(pending.blockers) ? pending.blockers : [];
  return {
    phase: str(v.phase) as string,
    hold: v.hold === true,
    holdStatement: str(v.hold_statement),
    paused: v.paused === true,
    outcome: str(v.outcome),
    firstObservation: parseObservation(v.first_observation),
    latestObservation: parseObservation(v.latest_observation),
    engineeringVerified: v.engineering_verified === true,
    progressNote: str(v.progress_note),
    delivered: delivery !== null && delivery.delivered === true,
    deliveryBasis: delivery ? str(delivery.basis) : null,
    blockers: blockers.flatMap((b) =>
      isObject(b) && str(b.code) !== null
        ? [{ code: str(b.code) as string, detail: str(b.detail) ?? '' }]
        : [],
    ),
  };
}

export function parseRequest(v: unknown): MrRequest | null {
  if (!isObject(v)) return null;
  const lifecycle = parseLifecycle(v.lifecycle);
  const requestRef = str(v.request_ref);
  const missionId = str(v.mission_id);
  const digest = str(v.proposal_digest_sha256);
  const revision = int(v.revision);
  if (
    lifecycle === null ||
    requestRef === null ||
    missionId === null ||
    digest === null ||
    revision === null
  ) {
    return null;
  }
  const lineage = isObject(v.lineage) ? v.lineage : {};
  const cancellation = isObject(v.cancellation) ? v.cancellation : null;
  const attested = isObject(v.attestation) ? v.attestation : null;
  const binding = attested && isObject(attested.binding) ? attested.binding : null;
  const label = attested && isObject(attested.provenance_label) ? attested.provenance_label : null;
  return {
    requestRef,
    missionId,
    revision,
    proposal: parseProposal(v.proposal),
    digest,
    createdAt: int(v.created_at) ?? 0,
    lifecycle,
    supersedes: str(lineage.supersedes),
    supersededBy: str(lineage.superseded_by),
    cancelledAt: cancellation ? int(cancellation.cancelled_at) : null,
    attestation: attested
      ? {
          state: str(attested.state) ?? 'unknown',
          attestedAt: int(attested.attested_at),
          relayedReply: str(attested.relayed_reply),
          provenanceProof: label ? str(label.proof) : null,
          residualRisk: str(attested.residual_risk),
          expiresAt: binding ? int(binding.expires_at) : null,
        }
      : null,
    run: parseRun(v.run),
    startCause: parseStartCause(v.start_cause),
  };
}

function parseSummary(v: unknown): MrSummary | null {
  if (!isObject(v) || !isObject(v.lifecycle)) return null;
  const requestRef = str(v.request_ref);
  const missionId = str(v.mission_id);
  const stage = str(v.lifecycle.stage);
  const label = str(v.lifecycle.label);
  const digest = str(v.proposal_digest_sha256);
  const revision = int(v.revision);
  if (
    requestRef === null ||
    missionId === null ||
    stage === null ||
    label === null ||
    digest === null ||
    revision === null
  ) {
    return null;
  }
  return {
    requestRef,
    missionId,
    revision,
    createdAt: int(v.created_at) ?? 0,
    objective: str(v.objective) ?? '',
    digest,
    stage,
    label,
    terminal: v.lifecycle.terminal === true,
    actions: strings(v.lifecycle.actions).filter((a): a is MrAction =>
      (ACTIONS as readonly string[]).includes(a),
    ),
    notice: str(v.lifecycle.notice),
  };
}

export function parseProgression(v: unknown): MrProgression | null {
  if (!isObject(v)) return null;
  const state = (MR_PROGRESSION_STATES as readonly string[]).includes(v.state as string)
    ? (v.state as MrProgressionState)
    : 'unreadable';
  return {
    state,
    detail: str(v.detail) ?? '',
    passes: int(v.passes),
    intervalSeconds: int(v.interval_seconds),
    lastPassAgeSeconds: int(v.last_pass_age_seconds),
  };
}

/** What a person is asking for here: named once, so no panel or heading is a bare category. */
export const MR_NAME = 'Codex Orchestrator';
export const MR_TAGLINE =
  'Ask Codex for engineering work in ordinary words. Codex helps you shape the request; you decide; DI\u2019s team does it in an isolated copy.';

/**
 * The CLOSED set of state labels the always-visible progression line can carry. Every state a person must
 * be able to tell apart has its OWN label, in words (never colour alone), and no state shares one with
 * another: ON is the only label that says work advances by itself.
 */
export const MR_PROGRESS_LABELS = [
  'ON',
  'OFF',
  'STALLED',
  'NOT FOR THESE REQUESTS',
  'UNKNOWN',
  'FAILED',
  'UNAVAILABLE',
] as const;
export type MrProgressLabel = (typeof MR_PROGRESS_LABELS)[number];

export interface MrProgressView {
  tone: 'ok' | 'warn';
  /** Which of the closed labels this state carries. */
  label: MrProgressLabel;
  /** The whole always-visible sentence, label included. Short: the state and what Start does. */
  text: string;
  /** Diagnostics for the disclosure: counters, the probe's own words, what the runtime does and never does. */
  details: string[];
}

const PROGRESS_NEVER =
  'The progression runtime never starts a Mission (Start is always yours), never records a failure as a success, and never starts the work a second time.';

/**
 * The always-visible line about automatic progress, and the disclosure behind it.
 *
 * The line is SHORT, but shortening never blurs the states: each of ON, OFF, STALLED, NOT FOR THESE
 * REQUESTS, UNKNOWN, FAILED and UNAVAILABLE has its own label and its own consequence on the line itself,
 * and only ON says the work advances by itself. Nothing needed to tell them apart is in the disclosure;
 * the disclosure holds counters and diagnostics. `reachable` is whether this window can currently vouch
 * for the request panel at all (it cannot when disconnected or the panel is unreachable).
 */
export function progressionWords(
  progression: MrProgression | null,
  runAvailable: boolean,
  reachable = true,
): MrProgressView | null {
  const make = (
    tone: 'ok' | 'warn',
    label: MrProgressLabel,
    sentence: string,
    details: string[],
  ): MrProgressView => {
    const lead =
      label === 'FAILED' ? 'Automatic progress has FAILED.' : `Automatic progress is ${label}.`;
    return { tone, label, text: `${lead} ${sentence}`, details };
  };
  if (!reachable) {
    return make('warn', 'UNAVAILABLE', 'This window cannot read the request panel right now.', [
      'Nothing shown here is current until the request panel can be read again.',
      PROGRESS_NEVER,
    ]);
  }
  if (!runAvailable) return null;
  if (progression === null) {
    return make(
      'warn',
      'UNKNOWN',
      'This panel cannot tell, so a started Mission may not advance.',
      ['The panel reported nothing about the progression runtime.', PROGRESS_NEVER],
    );
  }
  const facts: string[] = [];
  if (progression.detail !== '') facts.push(`Runtime status: ${progression.detail}.`);
  if (progression.passes !== null) facts.push(`Passes so far: ${progression.passes}.`);
  if (progression.lastPassAgeSeconds !== null)
    facts.push(`Last pass: ${progression.lastPassAgeSeconds} s ago.`);
  if (progression.intervalSeconds !== null)
    facts.push(`It passes about every ${progression.intervalSeconds} s.`);
  const details = [...facts, PROGRESS_NEVER];
  switch (progression.state) {
    case 'running':
      return make('ok', 'ON', 'Start begins the work; after that it advances by itself.', details);
    case 'stopped':
      return make(
        'warn',
        'OFF',
        'After Start nothing advances the work unless you do it by hand or start the runtime.',
        details,
      );
    case 'stale':
      return make(
        'warn',
        'STALLED',
        'The runtime has stopped reporting, so a started Mission may not advance.',
        details,
      );
    case 'mismatch':
      return make(
        'warn',
        'NOT FOR THESE REQUESTS',
        'A runtime is running for different locations, so it will not advance these.',
        details,
      );
    case 'failing':
      return make(
        'warn',
        'FAILED',
        'The runtime is running but its last pass failed; nothing is being advanced.',
        details,
      );
    case 'unreadable':
    default:
      return make(
        'warn',
        'UNKNOWN',
        'Its status cannot be read, so a started Mission may not advance.',
        details,
      );
  }
}

export function parseOverview(v: unknown): MrOverview | null {
  if (!isObject(v) || !isObject(v.capabilities) || !Array.isArray(v.requests)) return null;
  const serverNow = int(v.server_now);
  const caps = v.capabilities;
  const decision = isObject(caps.decision) ? caps.decision : null;
  if (serverNow === null || decision === null) return null;
  const meanings: Record<string, string> = {};
  if (isObject(caps.scope_meanings)) {
    for (const [k, m] of Object.entries(caps.scope_meanings))
      if (typeof m === 'string') meanings[k] = m;
  }
  return {
    serverNow,
    capabilities: {
      runAvailable: caps.run_available === true,
      progression: parseProgression(caps.progression),
      repositoryUrl: str(caps.repository_url),
      deliveryAuthority: str(caps.delivery_authority) ?? 'none',
      decisionProof: str(decision.proof) ?? 'operator_attested_not_independently_verified',
      decisionPrincipal: str(decision.transport) ?? 'operator_attested_relay',
      approvalValidSeconds: int(decision.approval_valid_seconds) ?? 900,
      scopeMeanings: meanings,
    },
    requests: v.requests.flatMap((r) => {
      const s = parseSummary(r);
      return s === null ? [] : [s];
    }),
    requestsTruncated: v.requests_truncated === true,
    conversations: (Array.isArray(v.conversations) ? v.conversations : []).flatMap((c) =>
      isObject(c) && str(c.conversation_id) !== null ? [parseConversationSummary(c)] : [],
    ),
  };
}

const TURN_ID_GRAMMAR = /^ct-[0-9a-f]{32}$/;

/** A control character (C0 or DEL): a declared question is one plain line. */
function hasControlCharacter(text: string): boolean {
  for (let i = 0; i < text.length; i += 1) {
    const code = text.charCodeAt(i);
    if (code < 0x20 || code === 0x7f) return true;
  }
  return false;
}

/** A declared question as the panel's journal bounds it: one plain line, never repaired. */
function validQuestion(q: unknown): q is string {
  return (
    typeof q === 'string' &&
    q.trim() !== '' &&
    q.length <= MR_MAX_QUESTION_CHARS &&
    !hasControlCharacter(q)
  );
}

/** The code points Python's `str.splitlines` breaks lines at (the panel's extractor splits replies with it). */
const LINE_BREAK_CODES = new Set([0x0a, 0x0b, 0x0c, 0x0d, 0x1c, 0x1d, 0x1e, 0x85, 0x2028, 0x2029]);

/** Split a text into lines exactly where `str.splitlines()` does (CRLF is one break; no trailing empty line). */
function splitLikePythonLines(text: string): string[] {
  const out: string[] = [];
  let start = 0;
  for (let i = 0; i < text.length; i++) {
    const code = text.charCodeAt(i);
    if (!LINE_BREAK_CODES.has(code)) continue;
    out.push(text.slice(start, i));
    if (code === 0x0d && text.charCodeAt(i + 1) === 0x0a) i++;
    start = i + 1;
  }
  if (start < text.length) out.push(text.slice(start));
  return out;
}

/**
 * A completed reply without its `mission-questions` block, for DISPLAY only (the exact reply stays available behind a disclosure and is never
 * rewritten). Mirrors the panel's own block rules (a line starting with ``` opens a fence; ours opens with exactly ```mission-questions; any fence closes on
 * a line that is exactly ```; other fences are skipped whole). It answers only when it finds exactly ONE closed block; anything else is null and the
 * reply is shown as written.
 */
export function replyWithoutQuestionsBlock(text: string): string | null {
  const lines = splitLikePythonLines(text);
  const kept: string[] = [];
  let opened = false;
  let ours = false;
  let blocks = 0;
  for (const line of lines) {
    if (!line.startsWith('```')) {
      if (!(opened && ours)) kept.push(line);
      continue;
    }
    if (opened) {
      if (line.trim() === '```') {
        const wasOurs = ours;
        opened = false;
        ours = false;
        if (wasOurs) blocks++;
        else kept.push(line);
      } else if (!ours) kept.push(line);
      continue;
    }
    opened = true;
    ours = line.trimEnd() === '```mission-questions';
    if (!ours) kept.push(line);
  }
  if (opened || blocks !== 1) return null;
  return kept.join('\n').replace(/\n{3,}/g, '\n\n').trim();
}

/**
 * An OLDER reply's prose with its closed fenced blocks folded away, for DISPLAY only: the exact reply stays available behind a disclosure and nothing in a block is
 * parsed, listed or counted. It answers only when the reply has at least one closed fenced block and EVERY fence in it is closed (a fence opens on a line that starts
 * with three backticks and closes on a line that is exactly three backticks, as the panel's own extractor reads them); an unclosed or malformed fence, or no fence at
 * all, answers null and the reply is shown exactly as written. Nothing is repaired.
 */
export function replyWithoutFencedBlocks(text: string): { prose: string; blocks: number } | null {
  const kept: string[] = [];
  let opened = false;
  let blocks = 0;
  for (const line of splitLikePythonLines(text)) {
    if (opened) {
      if (line.trim() === '```') {
        opened = false;
        blocks++;
      }
      continue;
    }
    if (line.startsWith('```')) {
      opened = true;
      continue;
    }
    kept.push(line);
  }
  if (opened || blocks === 0) return null;
  return { prose: kept.join('\n').replace(/\n{3,}/g, '\n\n').trim(), blocks };
}

/**
 * One conversation of the overview. The question fields are ADDITIVE: a panel that predates them sends
 * none, which reads `not_reported` (never "no questions"). A value that breaks a bound or a grammar is
 * `unreadable` with nothing awaited — refused, never repaired or truncated.
 */
function parseConversationSummary(c: Json): MrConversationSummary {
  const base = {
    conversationId: str(c.conversation_id) as string,
    turns: int(c.turns) ?? 0,
    lastActivity: int(c.last_activity) ?? 0,
    firstWords: str(c.first_words) ?? '',
    latestTurnState: str(c.latest_turn_state),
  };
  const none = (reporting: MrQuestionsReporting, problem: string | null = null) => ({
    ...base,
    awaitingAnswer: false,
    questionsReporting: reporting,
    questionsProblem: problem,
    awaitingTurnId: null,
    awaitingQuestions: [] as string[],
  });
  if (!('awaiting_answer' in c) || !('questions_reporting' in c)) return none('not_reported');
  const reportedAs = str(c.questions_reporting);
  const reporting: MrQuestionsReporting | null =
    reportedAs === 'read'
      ? 'read'
      : reportedAs === 'not_reported_by_this_panel_version'
        ? 'not_reported'
        : reportedAs === 'not_applicable'
          ? 'not_applicable'
          : null;
  if (reporting === null || typeof c.awaiting_answer !== 'boolean') return none('unreadable');
  const problem = c.questions_problem === null ? null : str(c.questions_problem);
  if (c.questions_problem !== null && problem === null) return none('unreadable');
  const questions = c.awaiting_questions;
  if (!Array.isArray(questions) || questions.length > MR_MAX_QUESTIONS) return none('unreadable');
  if (!questions.every(validQuestion) || new Set(questions).size !== questions.length)
    return none('unreadable');
  const turnId = c.awaiting_turn_id === null ? null : str(c.awaiting_turn_id);
  if (c.awaiting_answer) {
    // Awaiting needs a read reply, at least one question and the turn that asked it.
    if (
      reporting !== 'read' ||
      questions.length === 0 ||
      turnId === null ||
      !TURN_ID_GRAMMAR.test(turnId)
    )
      return none('unreadable');
  } else if (questions.length > 0 || turnId !== null) {
    return none('unreadable');
  }
  return {
    ...base,
    awaitingAnswer: c.awaiting_answer,
    questionsReporting: reporting,
    questionsProblem: problem,
    awaitingTurnId: turnId,
    awaitingQuestions: [...questions],
  };
}

/** The questions the panel read from one turn's reply: only a `read` report with no problem and valid, unique, bounded items counts. */
function parseTurnQuestions(v: unknown): string[] {
  if (!isObject(v) || v.reporting !== 'read' || v.problem !== null) return [];
  const items = v.items;
  if (!Array.isArray(items) || items.length === 0 || items.length > MR_MAX_QUESTIONS) return [];
  if (!items.every(validQuestion) || new Set(items).size !== items.length) return [];
  return [...items];
}

/** How the panel's report on a reply's questions reads, for display (see `MrTurn.questionsStatus`); never repaired, never a source of any count. */
function turnQuestionsStatus(v: unknown, declared: readonly string[]): MrTurnQuestionsStatus {
  if (declared.length > 0) return 'declared';
  if (!isObject(v) || v.reporting !== 'read') return 'not_reported';
  if (v.problem !== null) return 'refused';
  return Array.isArray(v.items) && v.items.length === 0 ? 'none_declared' : 'refused';
}

function parseTurn(v: unknown): MrTurn | null {
  if (!isObject(v)) return null;
  const turnId = str(v.turn_id);
  const conversationId = str(v.conversation_id);
  const state = str(v.state);
  const userText = str(v.user_text);
  const sequence = int(v.sequence);
  if (
    turnId === null ||
    conversationId === null ||
    state === null ||
    userText === null ||
    sequence === null
  ) {
    return null;
  }
  const outcome = isObject(v.outcome) ? v.outcome : null;
  const draft = isObject(v.draft) ? v.draft : null;
  const draftProposal = draft ? parseProposal(draft.proposal) : null;
  const submission = isObject(v.submission) ? v.submission : null;
  const declaredQuestions = parseTurnQuestions(v.questions);
  return {
    turnId,
    conversationId,
    sequence,
    state,
    inFlight: v.in_flight === true,
    userText,
    assistantText: str(v.assistant_text),
    outcomeStatus: outcome ? str(outcome.status) : null,
    outcomeCode: outcome ? str(outcome.error_code) : null,
    outcomeDetail: outcome ? str(outcome.detail) : null,
    draft:
      draft && draftProposal && str(draft.proposal_digest_sha256) !== null
        ? {
            proposal: draftProposal,
            digest: str(draft.proposal_digest_sha256) as string,
            runnableProblem: str(draft.runnable_problem),
          }
        : null,
    draftProblem: str(v.draft_problem),
    declaredQuestions,
    questionsStatus: turnQuestionsStatus(v.questions, declaredQuestions),
    submission: submission
      ? {
          state: str(submission.state) ?? 'unknown',
          requestRef: str(submission.request_ref),
          supersedes: str(submission.supersedes),
          problem: str(submission.problem),
          possibleMatches: strings(submission.possible_matches),
        }
      : null,
  };
}

export function parseConversation(
  v: unknown,
): {
  conversationId: string;
  turns: MrTurn[];
  total: number;
  omitted: number;
  limits: { turns: number; bytes: number } | null;
  split: { byTurns: number; byBytes: number } | null;
} | null {
  if (!isObject(v) || str(v.conversation_id) === null || !Array.isArray(v.turns)) return null;
  const turns = v.turns.flatMap((t) => {
    const p = parseTurn(t);
    return p === null ? [] : [p];
  });
  const total = int(v.turns_total) ?? turns.length;
  const limits = isObject(v.view_limits) ? v.view_limits : null;
  const limitTurns = limits === null ? null : int(limits.turns);
  const limitBytes = limits === null ? null : int(limits.bytes);
  const byTurns = int(v.turns_omitted_by_turn_limit);
  const byBytes = int(v.turns_omitted_by_byte_limit);
  return {
    conversationId: str(v.conversation_id) as string,
    turns,
    total,
    // The panel's own count when it gave one; otherwise what is missing, never less than that.
    omitted: Math.max(int(v.turns_omitted) ?? 0, total - turns.length, 0),
    limits: limitTurns !== null && limitBytes !== null ? { turns: limitTurns, bytes: limitBytes } : null,
    // Believed only when it accounts for exactly what is missing; otherwise the window does not claim a reason.
    split:
      byTurns !== null && byBytes !== null && byTurns + byBytes === Math.max(total - turns.length, 0)
        ? { byTurns, byBytes }
        : null,
  };
}

export function parseOffer(v: unknown): MrOffer | null {
  if (!isObject(v)) return null;
  const requestRef = str(v.request_ref);
  const missionId = str(v.mission_id);
  const digest = str(v.proposal_digest_sha256);
  const revision = int(v.revision);
  if (requestRef === null || missionId === null || digest === null || revision === null)
    return null;
  const provenance = isObject(v.provenance) ? v.provenance : null;
  const offerDigest = str(v.offer_digest_sha256);
  const expiresAt = int(v.expires_at);
  if (offerDigest === null || expiresAt === null) return null;
  return {
    requestRef,
    missionId,
    revision,
    digest,
    offerDigest,
    expiresAt,
    issuedAt: int(v.issued_at) ?? 0,
    scope: strings(v.approved_action_scope),
    deliveryTargets: strings(v.approved_delivery_targets),
    repositoryUrl: str(v.repository_url),
    approvalValidSeconds: int(v.approval_valid_seconds) ?? 900,
    provenanceProof: provenance ? str(provenance.proof) : null,
    residualRisk: str(v.residual_risk),
    serverNow: int(v.server_now) ?? 0,
  };
}

export function parseResult(v: unknown): MrResult | null {
  if (!isObject(v) || typeof v.recoverable !== 'boolean') return null;
  return {
    recoverable: v.recoverable,
    reason: str(v.reason),
    resultText: str(v.result_text),
    resultTruncated: v.result_truncated === true,
    reviewText: str(v.review_text),
    reviewTruncated: v.review_truncated === true,
    delivered: v.delivered === true,
    reviewEvidence: str(v.review_evidence),
    resultDigest: str(v.result_digest_sha256),
    reviewDigest: str(v.review_digest_sha256),
  };
}

// ---------------------------------------------------------------------------------------------
// The decision word

/** Mirrors the adapter's rule (the whole reply, case-insensitive, surrounding space ignored). */
export function isDecisionWord(typed: string): boolean {
  return MR_DECISION_WORDS.includes(typed.trim().toLowerCase());
}

// ---------------------------------------------------------------------------------------------
// The state

/** A proposal's identity as a person is shown it. A decision is bound to exactly this. */
export interface MrBinding {
  requestRef: string;
  missionId: string;
  revision: number;
  digest: string;
}

export const bindingOf = (r: MrRequest): MrBinding => ({
  requestRef: r.requestRef,
  missionId: r.missionId,
  revision: r.revision,
  digest: r.digest,
});

export const sameBinding = (a: MrBinding, b: MrBinding): boolean =>
  a.requestRef === b.requestRef &&
  a.missionId === b.missionId &&
  a.revision === b.revision &&
  a.digest === b.digest;

/** What a person is reading for one request: frozen, with the live record beside it. */
export interface MrView {
  /** What is DISPLAYED. Replaced by `latest` only when the binding is unchanged. */
  displayed: MrRequest;
  /** The record as last read. */
  latest: MrRequest;
  /** True when `latest` is a different proposal from `displayed`: decisions are withheld. */
  stale: boolean;
  offer: MrOffer | null;
  /** Counts the times the offer was dropped (expired), so a new one is read once each time. */
  offerEpoch: number;
  result: MrResult | null;
  /** When (client wall clock, ms) `latest` was read: for countdowns and "read N s ago". */
  readAtMs: number;
}

export type MrNoticeKind = 'info' | 'refusal' | 'unknown' | 'unavailable';
export interface MrNotice {
  id: number;
  kind: MrNoticeKind;
  op: DiMissionRequestOp;
  text: string;
}

export interface MrPending {
  seq: number;
  op: DiMissionRequestOp;
  requestRef: string | null;
  turnId: string | null;
}

export type MrAvailability =
  { kind: 'unknown' } | { kind: 'ok' } | { kind: 'unavailable'; problem: string; reason: string };

export interface MrConversation {
  id: string | null;
  /** The turns the panel SHOWED: the most recent ones that fit its bounds, oldest first. */
  turns: MrTurn[];
  /** How many turns the conversation holds in the record. */
  total: number;
  /** How many older turns the panel left out of what it showed (never silently: see `omittedWords`). */
  omitted: number;
  /** The panel's own view bounds, when it stated them. */
  limits: { turns: number; bytes: number } | null;
  /** Of the omitted turns, how many the turn bound left out and how many the byte budget did; null if not stated. */
  split: { byTurns: number; byBytes: number } | null;
}

export const EMPTY_CONVERSATION: MrConversation = {
  id: null,
  turns: [],
  total: 0,
  omitted: 0,
  limits: null,
  split: null,
};

/**
 * The sentence shown whenever the panel left older turns out of a conversation: how many it shows of how
 * many, that the rest are kept (not lost), and the bound that decided it. Null when nothing was left out.
 * A bound true for plain text and false for real text is not an honest bound, so the panel measures in the
 * serialized bytes that cross to this window and says here that it did.
 */
export function omittedWords(conversation: MrConversation): string | null {
  if (conversation.omitted <= 0) return null;
  const shown = conversation.turns.length;
  const earlier =
    conversation.omitted === 1 ? '1 earlier turn is' : `${conversation.omitted} earlier turns are`;
  const { limits, split } = conversation;
  const megabytes = limits === null ? null : Math.floor(limits.bytes / (1024 * 1024));
  const turnsBound = limits === null ? 'a fixed number of turns' : `${limits.turns} turns`;
  const bytesBound = megabytes === null ? 'a fixed amount of text' : `about ${megabytes} MB of text`;
  let why: string;
  if (split !== null && split.byBytes > 0 && split.byTurns > 0) {
    why = `: ${split.byTurns} because this window receives at most ${turnsBound}, and ${split.byBytes} more because the turns shown already reach the ${bytesBound} it receives at once`;
  } else if (split !== null && split.byBytes > 0) {
    why = `: the turns shown already reach the ${bytesBound} this window receives at once`;
  } else if (split !== null && split.byTurns > 0) {
    why = `: this window receives at most ${turnsBound} at a time`;
  } else {
    // The panel did not say which bound decided: say both, and that it is whichever came first.
    why = `: this window receives at most ${turnsBound} and ${bytesBound} at a time, whichever is reached first`;
  }
  return `Showing the most recent ${shown} of ${conversation.total} turns. ${earlier} kept in the record and not shown here${why}.`;
}

export type MrTab = 'ask' | 'requests';

export interface MrState {
  open: boolean;
  tab: MrTab;
  wide: boolean;
  availability: MrAvailability;
  overview: MrOverview | null;
  /** Client wall clock (ms) when the overview was read, and the server clock then (seconds). */
  overviewAtMs: number | null;
  selectedRef: string | null;
  views: Record<string, MrView>;
  conversation: MrConversation;
  composer: string;
  revising: string | null;
  typed: string;
  pending: MrPending[];
  notices: MrNotice[];
  nextSeq: number;
  nextNotice: number;
  /** True while the client cannot vouch for what it holds (disconnected / expired office). */
  unvouched: string | null;
}

export const INITIAL_STATE: MrState = {
  open: false,
  tab: 'ask',
  wide: false,
  availability: { kind: 'unknown' },
  overview: null,
  overviewAtMs: null,
  selectedRef: null,
  views: {},
  conversation: EMPTY_CONVERSATION,
  composer: '',
  revising: null,
  typed: '',
  pending: [],
  notices: [],
  nextSeq: 1,
  nextNotice: 1,
  unvouched: null,
};

export type MrEvent =
  | { type: 'open'; tab?: MrTab }
  | { type: 'tab'; tab: MrTab }
  | { type: 'close' }
  | { type: 'wide'; on: boolean }
  | { type: 'composer'; text: string }
  | { type: 'typed'; text: string }
  | { type: 'select'; requestRef: string | null }
  | { type: 'revise'; requestRef: string | null }
  | { type: 'newConversation' }
  | { type: 'openConversation'; conversationId: string }
  | { type: 'acknowledge'; requestRef: string }
  | { type: 'dropOffer'; requestRef: string }
  | { type: 'dismiss'; id: number }
  | { type: 'unvouched'; reason: string | null }
  | {
      type: 'sent';
      seq: number;
      op: DiMissionRequestOp;
      requestRef: string | null;
      turnId: string | null;
    }
  | { type: 'reply'; reply: DiMissionRequestReply; nowMs: number };

/** The operations that WRITE (everything but the five reads). */
export const WRITE_OPS: readonly DiMissionRequestOp[] = [
  'turn',
  'submit',
  'decide',
  'checkApproval',
  'withdraw',
  'run',
];
export const isWrite = (op: DiMissionRequestOp): boolean => WRITE_OPS.includes(op);

/** Whether an action of this kind for this request is already waiting for its answer. */
export function isPending(
  state: MrState,
  op: DiMissionRequestOp,
  requestRef: string | null = null,
): boolean {
  return state.pending.some(
    (p) => p.op === op && (requestRef === null || p.requestRef === requestRef),
  );
}

function addNotice(
  state: MrState,
  kind: MrNoticeKind,
  op: DiMissionRequestOp,
  text: string,
): MrState {
  const notice: MrNotice = { id: state.nextNotice, kind, op, text };
  return {
    ...state,
    nextNotice: state.nextNotice + 1,
    notices: [notice, ...state.notices].slice(0, MR_MAX_NOTICES),
  };
}

function withView(state: MrState, request: MrRequest, nowMs: number): MrState {
  const existing = state.views[request.requestRef];
  if (!existing) {
    return {
      ...state,
      views: {
        ...state.views,
        [request.requestRef]: {
          displayed: request,
          latest: request,
          stale: false,
          offer: null,
          offerEpoch: 0,
          result: null,
          readAtMs: nowMs,
        },
      },
    };
  }
  // The display moves with the record ONLY while it is the same proposal. A different Mission,
  // revision or digest invalidates it; it is never re-pointed.
  // The binding names the proposal by its digest, which covers every field including the proof rules.
  // The content is compared as well, so a read that carries DIFFERENT words or rules under an unchanged
  // binding (a panel this window does not agree with) invalidates the display instead of silently
  // replacing what the person read. A read that carries NO body is not a different body: the panel stops
  // sending one for a revision it no longer holds (once it is approved, when it was revised outside the
  // panel), and the unchanged digest still names exactly what the person was shown.
  const bodyDiffers =
    existing.displayed.proposal !== null &&
    request.proposal !== null &&
    JSON.stringify(existing.displayed.proposal) !== JSON.stringify(request.proposal);
  const same = sameBinding(bindingOf(existing.displayed), bindingOf(request)) && !bodyDiffers;
  const stale = existing.stale || !same;
  const keepsBody =
    !stale && request.proposal === null && existing.displayed.proposal !== null
      ? { proposal: existing.displayed.proposal }
      : {};
  return {
    ...state,
    views: {
      ...state.views,
      [request.requestRef]: {
        ...existing,
        displayed: stale ? existing.displayed : { ...request, ...keepsBody },
        latest: request,
        stale,
        readAtMs: nowMs,
      },
    },
    // The typed word belonged to a display that has just been invalidated.
    typed: stale && !existing.stale && state.selectedRef === request.requestRef ? '' : state.typed,
  };
}

/** A person's words for a refusal the adapter or this office made. */
export function problemWords(problem: string | null, reason: string | null): string {
  const said = reason !== null && reason.trim() !== '' ? reason.trim() : null;
  switch (problem) {
    case 'local_request_reply_not_affirmative':
      return `That is not the word. Type exactly "${MR_DECISION_WORD}" to approve. Nothing was recorded.`;
    case 'mission_panel_response_too_large':
      return 'The panel\u2019s answer was larger than this window can receive, so it was refused instead of being cut. Nothing was changed.';
    case 'mission_panel_display_stale':
      return 'The offer you were shown is no longer the offer of record: the proposal changed, was withdrawn or replaced, or the offer was not the one issued. Nothing was approved. Read the current proposal, then decide on that.';
    case 'mission_panel_offer_expired':
      return 'The approval window you were shown has passed. Nothing about the proposal changed and nothing was approved. Read the offer again for a new window, then decide.';
    case 'local_request_cancelled':
      return 'This proposal was withdrawn and is permanently closed.';
    case 'mission_panel_withdrawal_key_unavailable':
      return 'This panel no longer holds the key to withdraw this proposal. It was shown once and is kept only in memory, so it cannot be withdrawn from here.';
    case 'mission_panel_action_not_offered':
      return (
        said ??
        'That action is not available for this request now. The record may have moved; it was read again.'
      );
    case 'mission_panel_conversation_busy':
      return 'The assistant is still answering the last message in this conversation. Wait for it, then send yours.';
    case 'mission_panel_provider_busy':
      return 'The assistant is answering other conversations right now. Nothing was sent; try again in a moment.';
    case 'office_busy':
      return 'This office is busy with other requests. Nothing was sent; try again in a moment.';
    case 'request_invalid':
      return said ?? 'This office refused to send that request. Nothing was sent.';
    default:
      return (
        said ??
        `The request was refused (${problem ?? 'no reason given'}). Nothing was changed by this message.`
      );
  }
}

/** The honest words for a panel that cannot be reached. */
export function unavailableWords(problem: string, reason: string): string {
  switch (problem) {
    case 'panel_not_configured':
      return 'Mission requests are not set up in this office. Start the request panel (dimission_panel.py) and give this office its discovery file with --mission-panel-discovery.';
    case 'panel_discovery_unavailable':
      return `The request panel is not running, so no request can be made or read right now. ${reason}`;
    case 'panel_unreachable':
      return 'The request panel is not listening. Nothing was sent.';
    case 'panel_credentials_rejected':
      return 'The request panel did not accept this office. It may have restarted; close and reopen the panel.';
    default:
      return reason;
  }
}

function applyReply(state: MrState, reply: DiMissionRequestReply, nowMs: number): MrState {
  const pending = state.pending.find((p) => p.seq === reply.seq) ?? null;
  let next: MrState = { ...state, pending: state.pending.filter((p) => p.seq !== reply.seq) };
  const { op } = reply;
  const answer = reply.answer;

  if (reply.state === 'unavailable') {
    const reason = unavailableWords(reply.problem ?? 'panel_unavailable', reply.reason ?? '');
    const unknownOutcome =
      isWrite(op) && (reply.problem === 'panel_timeout' || reply.problem === 'panel_network_error');
    next = {
      ...next,
      availability: unknownOutcome
        ? next.availability
        : { kind: 'unavailable', problem: reply.problem ?? 'panel_unavailable', reason },
    };
    if (op === 'refresh' && !unknownOutcome) return { ...next, overview: null, overviewAtMs: null };
    if (unknownOutcome)
      return addNotice(
        next,
        'unknown',
        op,
        reply.reason ?? 'The outcome of that request is not known.',
      );
    return isWrite(op) ? addNotice(next, 'unavailable', op, reason) : next;
  }

  if (reply.state === 'refused') {
    next = {
      ...next,
      availability: next.availability.kind === 'unavailable' ? { kind: 'ok' } : next.availability,
    };
    const words = problemWords(reply.problem, reply.reason);
    if (reply.problem === 'mission_panel_display_stale' && pending?.requestRef) {
      const view = next.views[pending.requestRef];
      if (view) {
        next = {
          ...next,
          typed: '',
          views: { ...next.views, [pending.requestRef]: { ...view, stale: true } },
        };
      }
    }
    if (reply.problem === 'mission_panel_offer_expired' && pending?.requestRef) {
      // Only time passed: the proposal on display is unchanged and stays. The offer is dropped
      // and what was typed cleared; a NEW offer is read only when the person asks for it.
      const view = next.views[pending.requestRef];
      if (view) {
        next = {
          ...next,
          typed: '',
          views: {
            ...next.views,
            [pending.requestRef]: { ...view, offer: null, offerEpoch: view.offerEpoch + 1 },
          },
        };
      }
    }
    // A refused read of one request means the record is not there to show; say so once.
    return addNotice(next, 'refusal', op, words);
  }

  next = { ...next, availability: { kind: 'ok' } };
  if (answer === null) return next;

  switch (op) {
    case 'refresh': {
      const overview = parseOverview(answer);
      if (overview === null)
        return addNotice(
          next,
          'unavailable',
          op,
          'The request panel sent an overview this office does not understand.',
        );
      return { ...next, overview, overviewAtMs: nowMs };
    }
    case 'conversation': {
      const parsed = parseConversation(answer);
      if (parsed === null || next.conversation.id !== parsed.conversationId) return next;
      return {
        ...next,
        conversation: {
          id: parsed.conversationId,
          turns: parsed.turns,
          total: parsed.total,
          omitted: parsed.omitted,
          limits: parsed.limits,
          split: parsed.split,
        },
      };
    }
    case 'request':
    case 'checkApproval':
    case 'withdraw':
    case 'run':
    case 'decide':
    case 'submit': {
      let out = next;
      if (op === 'submit') {
        const submission = isObject(answer.submission) ? answer.submission : null;
        const request = parseRequest(answer.request);
        if (request) {
          out = withView(out, request, nowMs);
          out = {
            ...out,
            selectedRef: request.requestRef,
            revising: null,
            typed: '',
            tab: 'requests',
          };
        } else if (submission && str(submission.state) === 'UNCERTAIN') {
          out = addNotice(
            out,
            'unknown',
            op,
            'Whether the proposal was recorded is not known. It was not submitted again. Look at the list of requests below, then continue the conversation if it is not there.',
          );
        } else if (submission && str(submission.state) === 'RESERVED') {
          out = addNotice(
            out,
            'info',
            op,
            'Another window is submitting this proposal now. It will appear in the list.',
          );
        }
        return out;
      }
      const request = parseRequest(op === 'request' ? answer : answer.request);
      if (request) out = withView(out, request, nowMs);
      if (op === 'decide' && request) out = { ...out, typed: '' };
      if (op === 'withdraw' && request)
        out = addNotice(out, 'info', op, 'The proposal was withdrawn and is permanently closed.');
      return out;
    }
    case 'offer': {
      const offer = parseOffer(answer);
      if (offer === null) return next;
      const view = next.views[offer.requestRef];
      if (!view) return next;
      // An offer for a different proposal than the one on display invalidates the display: it is
      // never accepted as the offer for what the person is reading.
      if (!offerMatchesDisplay(view.displayed, offer)) {
        return {
          ...next,
          typed: '',
          views: { ...next.views, [offer.requestRef]: { ...view, stale: true } },
        };
      }
      return { ...next, views: { ...next.views, [offer.requestRef]: { ...view, offer } } };
    }
    case 'result': {
      const result = parseResult(answer);
      const ref = str(answer.request_ref) ?? pending?.requestRef ?? null;
      if (result === null || ref === null || !next.views[ref]) return next;
      return { ...next, views: { ...next.views, [ref]: { ...next.views[ref], result } } };
    }
    case 'turn': {
      const turn = isObject(answer.turn) ? parseTurn(answer.turn) : null;
      if (turn === null) return next;
      const known = next.conversation.id === turn.conversationId ? next.conversation.turns : [];
      return {
        ...next,
        composer: '',
        conversation: {
          id: turn.conversationId,
          turns: [...known.filter((t) => t.turnId !== turn.turnId), turn].sort(
            (a, b) => a.sequence - b.sequence,
          ),
          // A new turn is the newest: the record holds at least that many, and what was left out
          // before is still left out.
          total: Math.max(next.conversation.id === turn.conversationId ? next.conversation.total : 0, turn.sequence),
          omitted: next.conversation.id === turn.conversationId ? next.conversation.omitted : 0,
          limits: next.conversation.id === turn.conversationId ? next.conversation.limits : null,
          split: next.conversation.id === turn.conversationId ? next.conversation.split : null,
        },
      };
    }
  }
}

export function reduce(state: MrState, event: MrEvent): MrState {
  switch (event.type) {
    case 'open':
      return { ...state, open: true, tab: event.tab ?? state.tab };
    case 'tab':
      return { ...state, tab: event.tab };
    case 'close':
      return { ...state, open: false };
    case 'wide':
      return { ...state, wide: event.on };
    case 'composer':
      return { ...state, composer: event.text };
    case 'typed':
      return { ...state, typed: event.text };
    case 'select':
      // A different request is a different decision: the typed word does not follow it.
      return {
        ...state,
        selectedRef: event.requestRef,
        typed: event.requestRef === state.selectedRef ? state.typed : '',
      };
    case 'revise':
      return {
        ...state,
        revising: event.requestRef,
        tab: event.requestRef === null ? state.tab : 'ask',
      };
    case 'newConversation':
      return { ...state, conversation: EMPTY_CONVERSATION, composer: '', revising: null };
    case 'openConversation':
      return {
        ...state,
        conversation: { ...EMPTY_CONVERSATION, id: event.conversationId },
        revising: null,
      };
    case 'acknowledge': {
      const view = state.views[event.requestRef];
      if (!view) return state;
      return {
        ...state,
        typed: '',
        views: {
          ...state.views,
          [event.requestRef]: {
            ...view,
            displayed: view.latest,
            stale: false,
            offer: null,
            offerEpoch: view.offerEpoch + 1,
            result: null,
          },
        },
      };
    }
    case 'dropOffer': {
      // The person asked to read the offer again (it expired): drop it and what was typed.
      const view = state.views[event.requestRef];
      if (!view) return state;
      return {
        ...state,
        typed: '',
        views: {
          ...state.views,
          [event.requestRef]: { ...view, offer: null, offerEpoch: view.offerEpoch + 1 },
        },
      };
    }
    case 'dismiss':
      return { ...state, notices: state.notices.filter((n) => n.id !== event.id) };
    case 'unvouched':
      // Nothing stays on screen as if current once the client cannot vouch for it: the list and
      // the views are dropped, the conversation's words (the person's own) are kept.
      return event.reason === null
        ? { ...state, unvouched: null }
        : {
            ...state,
            unvouched: event.reason,
            overview: null,
            overviewAtMs: null,
            views: {},
            typed: '',
            pending: [],
          };
    case 'sent':
      return {
        ...state,
        nextSeq: Math.max(state.nextSeq, event.seq + 1),
        pending: [
          ...state.pending,
          { seq: event.seq, op: event.op, requestRef: event.requestRef, turnId: event.turnId },
        ],
      };
    case 'reply':
      return applyReply(state, event.reply, event.nowMs);
  }
}

// ---------------------------------------------------------------------------------------------
// What the window may do, derived from the state

/** The actions offered for a request, held back while stale, unvouched, or already asked for. */
export function offeredActions(state: MrState, requestRef: string): MrAction[] {
  const view = state.views[requestRef];
  if (!view || view.stale || state.unvouched !== null) return [];
  return view.latest.lifecycle.actions;
}

const RUN_OP: Partial<Record<MrAction, DiMissionRequestOp>> = {
  decide: 'decide',
  withdraw: 'withdraw',
  check_approval: 'checkApproval',
  dispatch: 'run',
  observe: 'run',
  reconcile: 'run',
  verify: 'run',
  result: 'result',
};

/** True while this action for this request is waiting for its answer (the control holds). */
export function actionPending(state: MrState, action: MrAction, requestRef: string): boolean {
  const op = RUN_OP[action];
  return op !== undefined && isPending(state, op, requestRef);
}

/**
 * What a request asks of the person, one item each: a decision, else an approval to check, and a verification. The reception inbox makes exactly one
 * entry per item, and `waitingOf` counts the same items, so the panel card, the inbox and the coverage line cannot disagree.
 */
export type MrRequestNeed = 'decide' | 'check_approval' | 'verify';
export function requestNeeds(actions: readonly MrAction[]): MrRequestNeed[] {
  const out: MrRequestNeed[] = [];
  if (actions.includes('decide')) out.push('decide');
  else if (actions.includes('check_approval')) out.push('check_approval');
  if (actions.includes('verify')) out.push('verify');
  return out;
}

/** A request that needs the person: a decision, an approval to check, or a verification. */
export function needsYou(actions: readonly MrAction[]): boolean {
  return requestNeeds(actions).length > 0;
}

/**
 * Everything the panel has waiting on the person, defined ONCE and said the same everywhere (the panel's own card, the inbox's cards and the coverage
 * line). The two kinds stay distinct: requests (a decision, an approval check, a verification) and conversations whose newest reply asked questions
 * with nothing after it. A conversation the panel did not read for questions is not counted (its coverage line says so).
 */
export interface MrWaiting {
  requests: number;
  conversations: number;
  total: number;
}
export function waitingOf(overview: MrOverview | null): MrWaiting {
  if (overview === null) return { requests: 0, conversations: 0, total: 0 };
  const requests = overview.requests.reduce((n, r) => n + requestNeeds(r.actions).length, 0);
  const conversations = overview.conversations.filter((c) => c.awaitingAnswer).length;
  return { requests, conversations, total: requests + conversations };
}

export interface MrSummaryCounts {
  needsYou: number;
  running: number;
  total: number;
}

const RUNNING_STAGES = ['dispatched', 'running', 'pending_proof', 'dispatch_uncertain'];

export function countsOf(overview: MrOverview | null): MrSummaryCounts {
  if (overview === null) return { needsYou: 0, running: 0, total: 0 };
  return {
    needsYou: waitingOf(overview).requests,
    running: overview.requests.filter((r) => RUNNING_STAGES.includes(r.stage)).length,
    total: overview.requests.length,
  };
}

/**
 * True when the request capability is NOT available in this office (as opposed to configured, or
 * not vouched for by this window). Only this case is demoted — to a quiet one-line notice the panel
 * places below Find; a configured entry keeps its fixed place right after the attention entry.
 */
export function requestsUnavailable(state: MrState): boolean {
  return state.unvouched === null && state.availability.kind === 'unavailable';
}

/** The sentence the entry shows. */
export function entryWords(state: MrState): string {
  if (state.unvouched !== null)
    return 'Requests are not shown: this window cannot vouch for them now.';
  if (state.availability.kind === 'unavailable') return state.availability.reason;
  if (state.overview === null) return 'Reading requests…';
  const c = countsOf(state.overview);
  const w = waitingOf(state.overview);
  const requestsWord = (n: number): string => (n === 1 ? 'request' : 'requests');
  const asking =
    w.conversations > 0
      ? `${w.conversations} ${w.conversations === 1 ? 'conversation' : 'conversations'} waiting for your answer`
      : null;
  if (c.total === 0)
    return asking === null
      ? 'No requests yet. Describe the work you want done.'
      : `${asking}. No requests yet.`;
  // Every number names what it counts. "Needs you" covers the three things a request can ask of the person (a decision, an approval to check, a verification),
  // the same items `waitingOf` counts for the inbox, so the card, the inbox and the coverage line cannot disagree; the last figure is the REQUEST total.
  const parts: string[] = [];
  if (c.needsYou > 0)
    parts.push(`${c.needsYou} ${c.needsYou === 1 ? 'request needs' : 'requests need'} you`);
  if (asking !== null) parts.push(asking);
  if (c.running > 0) parts.push(`${c.running} ${requestsWord(c.running)} in progress`);
  parts.push(`${c.total} ${requestsWord(c.total)} in all`);
  return parts.join(' · ');
}

// ---------------------------------------------------------------------------------------------
// Time

/** The server's clock now, estimated from the overview's reading. Seconds. */
export function serverNowSeconds(
  overview: MrOverview | null,
  overviewAtMs: number | null,
  nowMs: number,
): number | null {
  if (overview === null || overviewAtMs === null) return null;
  return overview.serverNow + Math.max(0, Math.floor((nowMs - overviewAtMs) / 1000));
}

export function remainingSeconds(
  expiresAt: number | null,
  serverNow: number | null,
): number | null {
  if (expiresAt === null || serverNow === null) return null;
  return expiresAt - serverNow;
}

export function countdownWords(seconds: number): string {
  if (seconds <= 0) return 'The approval window has passed.';
  const minutes = Math.floor(seconds / 60);
  const rest = seconds % 60;
  const text = minutes > 0 ? `${minutes} min ${rest} s` : `${rest} s`;
  return `The approval is valid for ${text} more. Start the run before then, or it expires.`;
}

/** "3 min ago" style age for a reading, from client wall-clock milliseconds. */
export function agoWords(readAtMs: number, nowMs: number): string {
  const s = Math.max(0, Math.floor((nowMs - readAtMs) / 1000));
  if (s < 5) return 'just now';
  if (s < 60) return `${s} s ago`;
  return `${Math.floor(s / 60)} min ago`;
}

// ---------------------------------------------------------------------------------------------
// The journey, as steps

export const JOURNEY: ReadonlyArray<{ key: string; label: string }> = [
  { key: 'proposed', label: 'Proposed' },
  { key: 'approved', label: 'Approved' },
  { key: 'started', label: 'Started' },
  { key: 'working', label: 'Team working' },
  { key: 'verified', label: 'Verified' },
];

/** The index of the step a stage is at, or null for a stage that is not on the journey. */
export function journeyIndex(stage: string): number | null {
  switch (stage) {
    case 'of_record':
    case 'approval_uncertain':
      return 0;
    case 'approved':
    case 'approved_elsewhere':
      return 1;
    case 'dispatch_uncertain':
    case 'dispatched':
      return 2;
    case 'running':
    case 'pending_proof':
      return 3;
    case 'verified':
      return 4;
    default:
      return null;
  }
}

// ---------------------------------------------------------------------------------------------
// Messages

export type MrFields = Omit<DiMissionRequest, 'type' | 'seq' | 'op'>;

export function buildMessage(
  op: DiMissionRequestOp,
  seq: number,
  fields: MrFields = {},
): DiMissionRequest {
  return { type: 'diMissionRequest', seq, op, ...fields };
}

/** True when the offer is for exactly the proposal on display. */
export function offerMatchesDisplay(displayed: MrRequest, offer: MrOffer): boolean {
  return (
    offer.requestRef === displayed.requestRef &&
    offer.missionId === displayed.missionId &&
    offer.revision === displayed.revision &&
    offer.digest === displayed.digest
  );
}

/** Whether the displayed offer can still be decided on, by the clock the window can read. */
export type MrOfferState = 'missing' | 'live' | 'expired';
export function offerState(view: MrView, serverNowSec: number | null): MrOfferState {
  if (view.offer === null || !offerMatchesDisplay(view.displayed, view.offer)) return 'missing';
  return serverNowSec !== null && view.offer.expiresAt <= serverNowSec ? 'expired' : 'live';
}

/**
 * Why a decision on the displayed proposal is refused from this window, or null when it may be taken. The
 * proposal's whole content, proof rules included, must be on the screen: if the proposal is not shown, or any
 * part of what it binds cannot be represented, approving it would be approving what the person never saw.
 */
export function decisionBlockedWords(view: MrView): string | null {
  const proposal = view.displayed.proposal;
  if (proposal === null) {
    return 'The proposal itself is not shown here, so it cannot be approved from this window. Nothing was approved.';
  }
  if (proposal.proof.unrepresented !== null) return proofRulesAbsentWords(proposal.proof);
  return null;
}

/**
 * The decision, bound to what the person is SHOWN: the proposal on display AND the offer on
 * display, submitted whole (display digest and every bound field), never the live record. With no
 * offer on display there is nothing to decide on, so there are no fields.
 */
export function decisionFields(view: MrView, reply: string): MrFields | null {
  const offer = view.offer;
  if (offer === null || !offerMatchesDisplay(view.displayed, offer)) return null;
  // Never a decision on a proposal this window cannot show in full (round 27 F1).
  if (decisionBlockedWords(view) !== null) return null;
  const b = bindingOf(view.displayed);
  return {
    requestRef: b.requestRef,
    missionId: b.missionId,
    revision: b.revision,
    digest: b.digest,
    reply,
    offerDigest: offer.offerDigest,
    expiresAt: offer.expiresAt,
    scope: offer.scope,
    deliveryTargets: offer.deliveryTargets,
    repositoryUrl: offer.repositoryUrl,
  };
}

export function withdrawFields(view: MrView): MrFields {
  const b = bindingOf(view.displayed);
  return {
    requestRef: b.requestRef,
    missionId: b.missionId,
    revision: b.revision,
    digest: b.digest,
  };
}

/** How many characters (code points) a turn holds, as the adapter counts them. */
export function codePointCount(text: string): number {
  return Array.from(text).length;
}

export function composerProblem(text: string): string | null {
  if (text.trim() === '') return 'Write what you want done first.';
  if (codePointCount(text) > MR_MAX_TEXT_CHARS) {
    return `That is longer than ${MR_MAX_TEXT_CHARS} characters. Shorten it.`;
  }
  return null;
}

/** Words for a turn that has not produced a reply. */
export function turnStateWords(turn: MrTurn): string | null {
  switch (turn.state) {
    case 'RESERVED':
      return 'The assistant is reading your message. It is a read-only turn: it can ask questions and draft, but it cannot approve or start anything.';
    case 'FAILED':
      return 'The assistant reported a failure for this message. Nothing was approved or started. You can send another message.';
    case 'REFUSED':
      return 'The assistant could not be started for this message, so nothing was asked. You can send another message.';
    case 'UNCERTAIN':
      return 'The outcome of this message is not known: the assistant may or may not have answered. It was not sent again. You can send another message.';
    default:
      return null;
  }
}

/** Lines of a text, for the reading blocks. A trailing newline does not add an empty line. */
export function linesOf(text: string): string[] {
  const lines = text.split(/\r\n|\n/);
  if (lines.length > 1 && lines[lines.length - 1] === '') lines.pop();
  return lines;
}

/** The short form of a digest for reading, with the full value one disclosure away. */
export function shortDigest(digest: string): string {
  return digest.length > 12 ? `${digest.slice(0, 12)}…` : digest;
}

/**
 * A request's plain title: the first line of what was asked for, kept short. The whole text is always shown in the proposal;
 * this is only the heading above it. Null when nothing was asked in words.
 */
export function plainTitle(objective: string | null | undefined): string | null {
  const first = (objective ?? '').split(/\r\n|\n/).find((line) => line.trim() !== '');
  if (first === undefined) return null;
  const line = first.trim();
  return line.length > 100 ? `${line.slice(0, 99).trimEnd()}\u2026` : line;
}

/**
 * The panel's "waiting for your decision" words open with one sentence of provenance ("This proposal is of record: Mission Core
 * holds it as revision N with digest D."). That sentence is exact and stays, but it is a reference, not the status. Split it off
 * ONLY when the words open with exactly that sentence; any other words are returned untouched, so nothing is ever dropped.
 */
export function splitRecordSentence(detail: string): { record: string | null; rest: string } {
  const found = /^(This proposal is of record: Mission Core holds it as revision \d+ with digest [0-9a-f]+\.)\s+(\S[\s\S]*)$/.exec(detail);
  return found === null ? { record: null, rest: detail } : { record: found[1], rest: found[2] };
}

export function scopeLine(scope: string, meanings: Record<string, string>): string {
  const meaning = meanings[scope];
  return meaning ? `${meaning} (${scope})` : scope;
}

// ---------------------------------------------------------------------------------------------
// Where the workspace sits

export interface MrLayout {
  /** True when there is no room to leave the world visible: the workspace covers everything. */
  covering: boolean;
  /** The width beside the office panel (ignored when covering). */
  widthPx: number;
  /** How far from the right edge it sits: the office panel's width, or 0 when that is hidden. */
  rightPx: number;
}

/**
 * The workspace takes the width it wants beside the office panel, leaving at least
 * `MR_WORLD_KEEP_PX` of the world visible. Where even `MR_MIN_BESIDE_PX` does not fit it covers the
 * window instead, and the workspace says so in words (never only in a tooltip).
 */
export function layoutFor(
  viewportPx: number,
  panelCollapsed: boolean,
  wide: boolean,
  panelPx: number,
): MrLayout {
  const rightPx = panelCollapsed ? 0 : panelPx;
  const room = viewportPx - rightPx - MR_WORLD_KEEP_PX;
  const widthPx = Math.min(wide ? MR_PANE_WIDE_PX : MR_PANE_PX, room);
  return widthPx < MR_MIN_BESIDE_PX
    ? { covering: true, widthPx: viewportPx, rightPx: 0 }
    : { covering: false, widthPx, rightPx };
}

/**
 * Wider reading puts the request list BESIDE the open request only where the pane has the room for both (`MR_SPLIT_MIN_PX`). Where it
 * does not (a window with little room beside the sidebar) the list stacks above the request exactly as in ordinary reading, so wider
 * reading is never worse than ordinary reading at the same width: the open request is never squeezed to a sliver and nothing scrolls
 * sideways.
 */
export function twoColumnReading(layout: MrLayout, wide: boolean): boolean {
  return wide && !layout.covering && layout.widthPx >= MR_SPLIT_MIN_PX;
}

/**
 * Shown at the top of the workspace whenever the office is an EXPLICIT fixture preview
 * (`fixtureMode`): the conversation, proposals and results are synthetic, and nothing in the window
 * is a real request awaiting a real decision. A screenshot of the workspace alone carries it.
 */
export const MR_FIXTURE_WORDS =
  'FIXTURE / VISUAL TEST: the conversation, proposals and results in this window are synthetic. Nothing here is a real request awaiting a real decision, and no approval given here is real.';

/** The short, always-visible statement of the same truth; `MR_FIXTURE_WORDS` (unchanged) is one disclosure away. */
export const MR_FIXTURE_SHORT =
  'Conversation, proposals and results here are synthetic; no approval given here is real.';

/** Said ONCE above the transcript (and offered to assistive technology on each assistant reply), instead of a long label on every reply. */
export const MR_DRAFTING_NOTE =
  'The assistant only drafts with you. Its words are not a decision and carry no authority; decisions happen in Requests.';

/** The compact note on an older reply whose technical blocks are folded away (view only). */
export function technicalBlockNote(blocks: number): string {
  return blocks === 1
    ? 'This earlier reply includes a technical block (view only; not a current question).'
    : `This earlier reply includes ${blocks} technical blocks (view only; not current questions).`;
}

export const COVERING_WORDS =
  'This window is narrow, so the requests cover the office and its panel. Close the requests to use the world again.';
