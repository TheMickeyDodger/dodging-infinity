/**
 * Item 4 — Mission requests: the DI server's side of the request panel.
 *
 * A client sends `diMissionRequest`; the server checks it is EXACTLY one of eleven closed
 * operations, relays it to the request panel's local adapter with the bearer token only the server
 * holds, and answers the ASKING connection with exactly one `diMissionRequestReply`.
 *
 * What this module is not: it holds no authority and decides nothing. A decision is the person's
 * typed word, relayed unchanged to the adapter, which binds it to the displayed Mission, revision
 * and digest and records it as an operator attestation that is NOT independently verified. The
 * server never types a word on a person's behalf and never turns a reply, a provider's text or a
 * team's result into a decision. It offers no shell, no PTY, no filesystem read and no command: a
 * request names only reference-shaped ids, a bounded plain-language text, the person's typed word,
 * and one of four run verbs.
 *
 * A write that fails AFTER it was sent leaves its effect unknown. The reply says so
 * (`panel_timeout`, `panel_network_error`) and nothing here ever replays a write; the client must
 * re-read the record. `panel_unreachable` is the one failure that means nothing was applied.
 *
 * Log lines carry the operation and problem codes only: never the token, a person's words, a
 * provider's text or a result.
 */

import type { DiMissionRequestOp, DiMissionRequestReply } from '../../../core/src/messages.js';
import type { BackendEndpoint, DiscoveryResult } from './backendDiscovery.js';
import { readBackendDiscovery } from './backendDiscovery.js';
import type { PanelCall, PanelResult } from './missionPanelClient.js';
import { requestPanel } from './missionPanelClient.js';

/** The most characters of one turn (the adapter's own bound, counted in code points). */
export const MAX_MISSION_TEXT_CHARS = 8000;
/** The longest typed reply relayed (the adapter's own bound). */
export const MAX_MISSION_REPLY_CHARS = 200;
/** Panel calls in flight at once, across every connection. */
export const MAX_CONCURRENT_PANEL_CALLS = 4;
/** The longest adapter reason relayed to a client. */
const MAX_RELAYED_REASON_CHARS = 2000;
const MAX_SEQ = 2147483647;

export const RUN_COMMANDS = ['dispatch', 'observe', 'reconcile', 'verify'] as const;

const GRAMMAR = {
  requestRef: /^lr-[0-9a-f]{32}$/,
  conversationId: /^cv-[0-9a-f]{32}$/,
  turnId: /^ct-[0-9a-f]{32}$/,
  missionId: /^mn-[0-9a-f]{32}$/,
  digest: /^[0-9a-f]{64}$/,
  problem: /^[a-z0-9_]{1,128}$/,
  /** A scope or delivery target name, as the record spells one. */
  scopeName: /^[a-z][a-z_]{0,39}$/,
  /** The repository the offer names: a canonical github.com repository URL, shape only. */
  repositoryUrl: /^https:\/\/github\.com\/[A-Za-z0-9._-]{1,100}\/[A-Za-z0-9._-]{1,100}$/,
} as const;
/** The most scope or delivery names one offer carries. */
const MAX_OFFER_NAMES = 8;
/** An absolute expiry is a time before the year 2100. */
const MAX_EXPIRES_AT = 4_102_444_800;

/** C0 controls other than tab, newline and carriage return. */
const UNSAFE_CONTROL = /[\u0000-\u0008\u000B\u000C\u000E-\u001F\u007F]/;

type Field = 'requestRef' | 'conversationId' | 'turnId' | 'missionId' | 'digest';

/** Each operation's exact keys: required, and optional. Nothing else is accepted. */
const OPERATIONS: Record<
  DiMissionRequestOp,
  { required: readonly string[]; optional: readonly string[] }
> = {
  refresh: { required: [], optional: [] },
  conversation: { required: ['conversationId'], optional: [] },
  request: { required: ['requestRef'], optional: [] },
  offer: { required: ['requestRef'], optional: [] },
  result: { required: ['requestRef'], optional: [] },
  turn: { required: ['text'], optional: ['conversationId'] },
  submit: { required: ['turnId', 'digest'], optional: ['supersedes'] },
  decide: {
    required: [
      'requestRef',
      'missionId',
      'revision',
      'digest',
      'reply',
      'offerDigest',
      'expiresAt',
      'scope',
      'deliveryTargets',
      'repositoryUrl',
    ],
    optional: [],
  },
  checkApproval: { required: ['requestRef'], optional: [] },
  withdraw: { required: ['requestRef', 'missionId', 'revision', 'digest'], optional: [] },
  run: { required: ['requestRef', 'command'], optional: [] },
};

export type ParsedMissionRequest =
  | { ok: true; seq: number; op: DiMissionRequestOp; call: PanelCall }
  | { ok: false; seq: number; op: DiMissionRequestOp | null; reason: string };

function codePoints(text: string): number {
  let count = 0;
  for (const _ of text) count++;
  return count;
}

/** No lone surrogate: text that cannot be encoded as UTF-8 is never sent. */
function isWellFormedText(text: string): boolean {
  for (let i = 0; i < text.length; i++) {
    const unit = text.charCodeAt(i);
    if (unit >= 0xd800 && unit <= 0xdbff) {
      const next = text.charCodeAt(i + 1);
      if (next >= 0xdc00 && next <= 0xdfff) {
        i++;
        continue;
      }
      return false;
    }
    if (unit >= 0xdc00 && unit <= 0xdfff) return false;
  }
  return true;
}

function fieldOk(field: Field, value: unknown): value is string {
  return typeof value === 'string' && GRAMMAR[field].test(value);
}

/** Words a person typed or a turn of their request: bounded, well formed, no control bytes. */
function wordsProblem(value: unknown, limit: number, what: string): string | null {
  if (typeof value !== 'string') return `${what} must be text`;
  if (value.trim() === '') return `${what} must not be empty`;
  if (codePoints(value) > limit) return `${what} is longer than ${limit} characters`;
  if (!isWellFormedText(value)) return `${what} is not well-formed Unicode text`;
  if (UNSAFE_CONTROL.test(value)) return `${what} carries a control character`;
  return null;
}

/**
 * A `diMissionRequest`, EXACTLY: `type`, `seq`, `op`, and that operation's own keys (an optional
 * one may be absent), each in its closed grammar. Anything else is refused, never repaired.
 */
export function parseMissionRequest(msg: Record<string, unknown>): ParsedMissionRequest {
  const seq =
    typeof msg.seq === 'number' && Number.isInteger(msg.seq) && msg.seq >= 0 && msg.seq <= MAX_SEQ
      ? msg.seq
      : null;
  const op =
    typeof msg.op === 'string' && Object.hasOwn(OPERATIONS, msg.op)
      ? (msg.op as DiMissionRequestOp)
      : null;
  const refuse = (reason: string): ParsedMissionRequest => ({
    ok: false,
    seq: seq ?? 0,
    op,
    reason,
  });
  if (msg.type !== 'diMissionRequest') return refuse('not a diMissionRequest');
  if (seq === null) return refuse('seq must be a non-negative integer');
  if (op === null) return refuse('op is not one of the eleven operations');
  const spec = OPERATIONS[op];
  const allowed = new Set<string>(['type', 'seq', 'op', ...spec.required, ...spec.optional]);
  for (const key of Object.keys(msg)) {
    if (!allowed.has(key)) return refuse(`${op} does not take "${key}"`);
  }
  for (const key of spec.required) {
    if (!(key in msg)) return refuse(`${op} needs "${key}"`);
  }

  const m = msg as Record<string, unknown>;
  const need = (key: Field): string | null => (fieldOk(key, m[key]) ? (m[key] as string) : null);

  switch (op) {
    case 'refresh':
      return { ok: true, seq, op, call: { method: 'GET', path: '/overview' } };
    case 'conversation': {
      const id = need('conversationId');
      if (id === null) return refuse('conversationId is not a cv- id');
      return { ok: true, seq, op, call: { method: 'GET', path: `/conversations/${id}` } };
    }
    case 'request':
    case 'result': {
      const id = need('requestRef');
      if (id === null) return refuse('requestRef is not an lr- reference');
      const suffix = op === 'request' ? '' : `/${op}`;
      return { ok: true, seq, op, call: { method: 'GET', path: `/requests/${id}${suffix}` } };
    }
    case 'offer': {
      // Issuing (or re-reading) the frozen offer records bookkeeping: a POST with no body keys.
      const id = need('requestRef');
      if (id === null) return refuse('requestRef is not an lr- reference');
      return {
        ok: true,
        seq,
        op,
        call: { method: 'POST', path: `/requests/${id}/offer`, body: {} },
      };
    }
    case 'turn': {
      const problem = wordsProblem(m.text, MAX_MISSION_TEXT_CHARS, 'text');
      if (problem !== null) return refuse(problem);
      let conversationId: string | null = null;
      if (m.conversationId !== undefined && m.conversationId !== null) {
        conversationId = need('conversationId');
        if (conversationId === null) return refuse('conversationId is not a cv- id');
      }
      return {
        ok: true,
        seq,
        op,
        call: {
          method: 'POST',
          path: '/conversations/turn',
          body: { conversation_id: conversationId, text: m.text },
        },
      };
    }
    case 'submit': {
      const turnId = need('turnId');
      const digest = need('digest');
      if (turnId === null) return refuse('turnId is not a ct- id');
      if (digest === null) return refuse('digest is not 64 lowercase hex characters');
      let supersedes: string | null = null;
      if (m.supersedes !== undefined && m.supersedes !== null) {
        if (!fieldOk('requestRef', m.supersedes))
          return refuse('supersedes is not an lr- reference');
        supersedes = m.supersedes;
      }
      return {
        ok: true,
        seq,
        op,
        call: {
          method: 'POST',
          path: '/proposals/submit',
          body: { turn_id: turnId, draft_digest_sha256: digest, supersedes },
        },
      };
    }
    case 'checkApproval': {
      const id = need('requestRef');
      if (id === null) return refuse('requestRef is not an lr- reference');
      return {
        ok: true,
        seq,
        op,
        call: { method: 'POST', path: `/requests/${id}/check-approval`, body: {} },
      };
    }
    case 'decide':
    case 'withdraw': {
      const id = need('requestRef');
      const missionId = need('missionId');
      const digest = need('digest');
      if (id === null) return refuse('requestRef is not an lr- reference');
      if (missionId === null) return refuse('missionId is not an mn- id');
      if (!Number.isInteger(m.revision) || (m.revision as number) < 1) {
        return refuse('revision must be a positive integer');
      }
      if (digest === null) return refuse('digest is not 64 lowercase hex characters');
      const body: Record<string, unknown> = {
        mission_id: missionId,
        revision: m.revision,
        proposal_digest_sha256: digest,
      };
      if (op === 'decide') {
        // The person's typed word, relayed UNCHANGED: whether it is exactly the affirmative is
        // the adapter's rule and is checked there before anything is read or written.
        const problem = wordsProblem(m.reply, MAX_MISSION_REPLY_CHARS, 'reply');
        if (problem !== null) return refuse(problem);
        body.reply = m.reply;
        // The EXACT offer the person was shown. The adapter compares it with the offer it
        // retained and with the current record; nothing here repairs or re-derives any of it.
        const offerDigest = fieldOk('digest', m.offerDigest) ? m.offerDigest : null;
        if (offerDigest === null) return refuse('offerDigest is not 64 lowercase hex characters');
        if (
          !Number.isInteger(m.expiresAt) ||
          (m.expiresAt as number) < 1 ||
          (m.expiresAt as number) > MAX_EXPIRES_AT
        ) {
          return refuse('expiresAt must be the displayed absolute expiry');
        }
        for (const key of ['scope', 'deliveryTargets'] as const) {
          const value = m[key];
          if (
            !Array.isArray(value) ||
            value.length > MAX_OFFER_NAMES ||
            !value.every((v) => typeof v === 'string' && GRAMMAR.scopeName.test(v))
          ) {
            return refuse(`${key} must be a short list of names`);
          }
        }
        if (
          m.repositoryUrl !== null &&
          !(typeof m.repositoryUrl === 'string' && GRAMMAR.repositoryUrl.test(m.repositoryUrl))
        ) {
          return refuse('repositoryUrl must be null or a github.com repository URL');
        }
        body.offer_digest_sha256 = offerDigest;
        body.expires_at = m.expiresAt;
        body.approved_action_scope = m.scope;
        body.approved_delivery_targets = m.deliveryTargets;
        body.repository_url = m.repositoryUrl;
      }
      const verb = op === 'decide' ? 'decide' : 'withdraw';
      return {
        ok: true,
        seq,
        op,
        call: { method: 'POST', path: `/requests/${id}/${verb}`, body },
      };
    }
    case 'run': {
      const id = need('requestRef');
      if (id === null) return refuse('requestRef is not an lr- reference');
      if (
        typeof m.command !== 'string' ||
        !(RUN_COMMANDS as readonly string[]).includes(m.command)
      ) {
        return refuse(`command must be one of ${RUN_COMMANDS.join(', ')}`);
      }
      return {
        ok: true,
        seq,
        op,
        call: { method: 'POST', path: `/requests/${id}/run`, body: { command: m.command } },
      };
    }
  }
}

function reply(
  seq: number,
  op: DiMissionRequestOp,
  state: DiMissionRequestReply['state'],
  problem: string | null,
  reason: string | null,
  answer: Record<string, unknown> | null,
): DiMissionRequestReply {
  return { type: 'diMissionRequestReply', seq, op, state, problem, reason, answer };
}

/** What a transport failure means for the person, said as data (the client words it). */
const TRANSPORT_REASONS: Record<string, string> = {
  panel_unreachable: 'The request panel is not listening. Nothing was sent.',
  panel_timeout:
    'The request panel did not answer in time. Whether it applied this request is not known; read the record again before acting.',
  panel_network_error:
    'The connection to the request panel failed. Whether it applied this request is not known; read the record again before acting.',
  panel_response_too_large: 'The request panel sent more than this office will read.',
  panel_request_invalid: 'This office refused to send that request. Nothing was sent.',
};

function isObject(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

/** The reply for the adapter's HTTP answer (the adapter's own words, bounded and typed). */
export function replyFromPanel(
  parsed: Extract<ParsedMissionRequest, { ok: true }>,
  result: PanelResult,
): DiMissionRequestReply {
  const { seq, op } = parsed;
  if (result.kind === 'transport_error') {
    return reply(
      seq,
      op,
      'unavailable',
      result.problem,
      TRANSPORT_REASONS[result.problem] ?? null,
      null,
    );
  }
  const body = result.body;
  if (result.status === 401) {
    return reply(
      seq,
      op,
      'unavailable',
      'panel_credentials_rejected',
      'The request panel did not accept this office. It may have restarted; reopen the panel.',
      null,
    );
  }
  if (result.status !== 200 || !isObject(body)) {
    const said = isObject(body) && typeof body.error === 'string' ? body.error.slice(0, 64) : null;
    return reply(
      seq,
      op,
      'refused',
      `adapter_http_${result.status}`,
      said ?? `The request panel answered HTTP ${result.status}.`,
      null,
    );
  }
  if (body.ok === true) return reply(seq, op, 'ok', null, null, body);
  const problem =
    typeof body.problem === 'string' && GRAMMAR.problem.test(body.problem)
      ? body.problem
      : 'adapter_problem_unrecognised';
  const reason =
    typeof body.reason === 'string' ? body.reason.slice(0, MAX_RELAYED_REASON_CHARS) : null;
  return reply(seq, op, 'refused', problem, reason, {
    details: isObject(body.details) ? body.details : {},
  });
}

export interface MissionRequestDeps {
  readDiscovery: (filePath: string) => DiscoveryResult;
  request: (endpoint: BackendEndpoint, call: PanelCall) => Promise<PanelResult>;
  log: (line: string) => void;
}

export class DiMissionRequests {
  private readonly deps: MissionRequestDeps;
  private inFlight = 0;

  /**
   * @param discoveryPath the request panel adapter's discovery file (READ ONLY), or null when
   *   this office was started without one: every request is then answered `unavailable`.
   */
  constructor(
    private readonly discoveryPath: string | null,
    deps: Partial<MissionRequestDeps> = {},
  ) {
    this.deps = {
      readDiscovery: deps.readDiscovery ?? readBackendDiscovery,
      request: deps.request ?? ((endpoint, call) => requestPanel(endpoint, call)),
      log: deps.log ?? ((line) => console.log(line)),
    };
  }

  /** Answer one `diMissionRequest` to the asking connection only. Never throws. */
  async handle(
    msg: Record<string, unknown>,
    send: (message: Record<string, unknown>) => void,
  ): Promise<void> {
    const parsed = parseMissionRequest(msg);
    const answer = (r: DiMissionRequestReply): void =>
      send(r as unknown as Record<string, unknown>);
    if (!parsed.ok) {
      this.deps.log(`[Pixel Agents DI] mission request refused: ${parsed.op ?? 'unknown'} invalid`);
      answer(
        reply(
          parsed.seq,
          parsed.op ?? 'refresh',
          'refused',
          'request_invalid',
          parsed.reason,
          null,
        ),
      );
      return;
    }
    if (this.discoveryPath === null) {
      answer(
        reply(
          parsed.seq,
          parsed.op,
          'unavailable',
          'panel_not_configured',
          'This office was started without a request panel (--mission-panel-discovery).',
          null,
        ),
      );
      return;
    }
    const discovery = this.deps.readDiscovery(this.discoveryPath);
    if (!discovery.ok) {
      answer(
        reply(
          parsed.seq,
          parsed.op,
          'unavailable',
          'panel_discovery_unavailable',
          `The request panel is not running (${discovery.problem}).`,
          null,
        ),
      );
      return;
    }
    if (this.inFlight >= MAX_CONCURRENT_PANEL_CALLS) {
      answer(
        reply(
          parsed.seq,
          parsed.op,
          'refused',
          'office_busy',
          'Too many requests are in progress. Nothing was sent; try again.',
          null,
        ),
      );
      return;
    }
    this.inFlight++;
    let result: PanelResult;
    try {
      try {
        result = await this.deps.request(discovery.endpoint, parsed.call);
      } catch {
        result = { kind: 'transport_error', problem: 'panel_network_error' };
      }
    } finally {
      this.inFlight--;
    }
    const out = replyFromPanel(parsed, result);
    this.deps.log(
      `[Pixel Agents DI] mission request: ${out.op} ${out.state} ${out.problem ?? 'ok'}`,
    );
    answer(out);
  }
}
