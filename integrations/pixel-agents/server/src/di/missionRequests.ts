/**
 * Item 4 — Mission requests: the DI server's side of the request panel.
 *
 * A client sends `diMissionRequest`; the server checks it is EXACTLY one of fourteen closed
 * operations, relays it to the request panel's local adapter with the bearer token only the server
 * holds, and answers the ASKING connection with exactly one `diMissionRequestReply`.
 *
 * What this module is not: it holds no authority and decides nothing. A decision is the person's
 * typed word, relayed unchanged to the adapter, which binds it to the displayed Mission, revision
 * and digest and records it as an operator attestation that is NOT independently verified. By
 * owner decision (c) the person may instead decline, reject or request changes (`decline`,
 * `reject`, `requestChanges`): the same exact offer, no typed word, and for a change request the
 * person's reason, relayed unchanged; the adapter records each the same way. The server never
 * types a word or a reason on a person's behalf and never turns a reply, a provider's text or a
 * team's result into a decision. It offers no shell, no PTY, no filesystem read and no command: a
 * request names only reference-shaped ids, a bounded plain-language text, the person's typed word
 * or reason, and one of four run verbs.
 *
 * A WRITE (the ten POST operations: see WRITES) that may have reached the panel and came back
 * with no usable answer leaves its effect unknown, and its reply's state is `unknown`: no answer in
 * time, a broken connection, an answer too large or unreadable, or the panel's own report of an
 * unexpected error or an oversized answer after it handled the request. Nothing here ever sends a
 * request again. The same failures on a READ are `unavailable`: a read changes nothing. A request
 * that was never sent (no panel, an unusable discovery file, nothing answering, refused here) is
 * `unavailable` too: nothing was sent.
 *
 * Every diMissionRequest with a valid type, seq and op gets exactly one reply, correlated by that
 * seq and op. A message without a valid seq or op gets none: nothing here invents either.
 *
 * Log lines carry the operation and problem codes only: never the token, a person's words, a
 * provider's text or a result.
 */

import type { DiMissionRequestOp, DiMissionRequestReply } from '../../../core/src/messages.js';
import type { BackendEndpoint, DiscoveryResult } from './backendDiscovery.js';
import { readBackendDiscovery } from './backendDiscovery.js';
import type { PanelCall, PanelResult, PanelTransportProblem } from './missionPanelClient.js';
import { requestPanel } from './missionPanelClient.js';

/** The most characters of one turn (the adapter's own bound, counted in code points). */
export const MAX_MISSION_TEXT_CHARS = 8000;
/** The longest typed reply relayed (the adapter's own bound). */
export const MAX_MISSION_REPLY_CHARS = 200;
/** (c): the longest change-request reason relayed (Mission Core's own bound, counted in code points). */
export const MAX_CHANGE_REASON_CHARS = 2000;
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

/** The exact offer a decision on the displayed proposal binds, with or without the typed word. */
const OFFER_KEYS = [
  'requestRef',
  'missionId',
  'revision',
  'digest',
  'offerDigest',
  'expiresAt',
  'scope',
  'deliveryTargets',
  'repositoryUrl',
] as const;

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
  // (c): the person's chosen non-approval outcome, on exactly the offer shown; no typed word.
  decline: { required: OFFER_KEYS, optional: [] },
  reject: { required: OFFER_KEYS, optional: [] },
  requestChanges: { required: [...OFFER_KEYS, 'reason'], optional: [] },
  withdraw: { required: ['requestRef', 'missionId', 'revision', 'digest'], optional: [] },
  run: { required: ['requestRef', 'command'], optional: [] },
};

/** The adapter route each decision-shaped operation is sent to. */
const ROUTE_OF = {
  decide: 'decide',
  decline: 'decline',
  reject: 'reject',
  requestChanges: 'request-changes',
  withdraw: 'withdraw',
} as const;

/**
 * A refusal carries a seq and an op only when the message is inside the CORRELATION DOMAIN (a valid
 * type, seq and op). Outside it both are null: the message cannot be answered, and nothing invents them.
 */
export type ParsedMissionRequest =
  | { ok: true; seq: number; op: DiMissionRequestOp; call: PanelCall }
  | { ok: false; seq: number | null; op: DiMissionRequestOp | null; reason: string };

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
  const correlated = msg.type === 'diMissionRequest' && seq !== null && op !== null;
  const refuse = (reason: string): ParsedMissionRequest => ({
    ok: false,
    seq: correlated ? seq : null,
    op: correlated ? op : null,
    reason,
  });
  if (msg.type !== 'diMissionRequest') return refuse('not a diMissionRequest');
  if (seq === null) return refuse('seq must be a non-negative integer');
  if (op === null) return refuse('op is not one of the fourteen operations');
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
    case 'decline':
    case 'reject':
    case 'requestChanges':
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
      if (op !== 'withdraw') {
        if (op === 'decide') {
          // The person's typed word, relayed UNCHANGED: whether it is exactly the affirmative is
          // the adapter's rule and is checked there before anything is read or written.
          const problem = wordsProblem(m.reply, MAX_MISSION_REPLY_CHARS, 'reply');
          if (problem !== null) return refuse(problem);
          body.reply = m.reply;
        }
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
        if (op === 'requestChanges') {
          // (c): the person's reason, relayed UNCHANGED: whether it is readable is Mission Core's
          // rule, checked again by the adapter before anything is read or written.
          const problem = wordsProblem(m.reason, MAX_CHANGE_REASON_CHARS, 'reason');
          if (problem !== null) return refuse(problem);
          body.reason = m.reason;
        }
      }
      const verb = ROUTE_OF[op];
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

/**
 * The operations that may change the record, by what each adapter route DOES (mission_panel/http_adapter.py and service.py), not
 * by its name: the ten POST routes. Among them, offer records the frozen offer, checkApproval attests an approval of unknown
 * outcome, and (c) decline, reject and requestChanges record the person's chosen non-approval outcome. The other four operations
 * (refresh, conversation, request, result) are the GET routes, which read.
 */
const WRITES: ReadonlySet<DiMissionRequestOp> = new Set<DiMissionRequestOp>([
  'offer',
  'turn',
  'submit',
  'decide',
  'checkApproval',
  'decline',
  'reject',
  'requestChanges',
  'withdraw',
  'run',
]);

const UNKNOWN_OUTCOME =
  'Whether it applied this request is not known; read the record again before acting.';

/** A failure that leaves a write's outcome unknowable is `unknown`; the same failure on a read is `unavailable` (a read changes nothing). */
function unknowable(op: DiMissionRequestOp): DiMissionRequestReply['state'] {
  return WRITES.has(op) ? 'unknown' : 'unavailable';
}

/** What a transport failure means for the person, said as data (the client words it): what is established, and for a write that may have been sent, that its outcome is NOT KNOWN. */
function transportReply(
  seq: number,
  op: DiMissionRequestOp,
  problem: PanelTransportProblem,
): DiMissionRequestReply {
  const write = WRITES.has(op);
  switch (problem) {
    case 'panel_unreachable':
      return reply(
        seq,
        op,
        'unavailable',
        problem,
        "Nothing answered at the address in the request panel's discovery file. Nothing was sent.",
        null,
      );
    case 'panel_request_invalid':
      return reply(
        seq,
        op,
        'unavailable',
        problem,
        'This office refused to send that request. Nothing was sent.',
        null,
      );
    case 'panel_timeout':
      return reply(
        seq,
        op,
        unknowable(op),
        problem,
        write
          ? `The request panel did not answer in time. ${UNKNOWN_OUTCOME}`
          : 'The request panel did not answer in time.',
        null,
      );
    case 'panel_network_error':
      return reply(
        seq,
        op,
        unknowable(op),
        problem,
        write
          ? `The connection to the request panel failed. ${UNKNOWN_OUTCOME}`
          : 'The connection to the request panel failed.',
        null,
      );
    case 'panel_response_too_large':
      return reply(
        seq,
        op,
        unknowable(op),
        problem,
        write
          ? `The request panel sent more than this office will read. ${UNKNOWN_OUTCOME}`
          : 'The request panel sent more than this office will read.',
        null,
      );
  }
}

/** An answer at 200 that states no definite outcome (not an object, or no boolean ok): `unknown` for a write (it may have run), `unavailable` for a read. Never read as a refusal. */
function unreadable(seq: number, op: DiMissionRequestOp): DiMissionRequestReply {
  return reply(
    seq,
    op,
    unknowable(op),
    'panel_answer_unreadable',
    WRITES.has(op)
      ? `The request panel's answer could not be read. ${UNKNOWN_OUTCOME}`
      : "The request panel's answer could not be read.",
    null,
  );
}

/**
 * The panel's own answers that report a failure AFTER it handled the request (mission_panel/http_adapter.py: an unexpected error
 * during the service call, and an answer replaced because it was too large to send). For a WRITE, whether it took effect is not
 * known, so the reply is `unknown`, in this office's words: the adapter's own reason ("nothing is shown as changed") is not relayed
 * for a write. For a read they stay refusals, relayed as before.
 */
const AFTER_HANDLING: Readonly<Record<string, string>> = {
  mission_panel_internal: `The request panel reported an unexpected error while handling this request. ${UNKNOWN_OUTCOME}`,
  mission_panel_response_too_large: `The request panel's answer to this request was too large to send, so the panel replaced it after handling the request. ${UNKNOWN_OUTCOME}`,
};

/** The reply to a message this office refuses itself: correlated by its seq and op; null, NO reply, for a message outside the correlation domain. */
export function invalidMissionRequestReply(
  parsed: Extract<ParsedMissionRequest, { ok: false }>,
): DiMissionRequestReply | null {
  if (parsed.seq === null || parsed.op === null) return null;
  return reply(parsed.seq, parsed.op, 'refused', 'request_invalid', parsed.reason, null);
}

/** The reply when this office has no request panel: nothing was sent, so nothing was changed. */
export function panelNotConfiguredReply(
  parsed: Extract<ParsedMissionRequest, { ok: true }>,
): DiMissionRequestReply {
  return reply(
    parsed.seq,
    parsed.op,
    'unavailable',
    'panel_not_configured',
    'This office was started without a request panel (--mission-panel-discovery).',
    null,
  );
}

function isObject(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

// ---------------------------------------------------------------------------------------------
// The response boundary, for the refusals that name locations on this machine.
//
// When the MissionBridge refuses to prepare a workspace, its sentence NAMES ABSOLUTE PATHS (the workspaces root, the repository, a worktree, a git error): the producer words it for an operator.
// Relayed verbatim, a private path of this machine crossed to the client and was drawn in its refusal notice. This office does not try to find and remove a path inside that prose (that needs a
// parser for arbitrary text, and a path with a space defeats a pattern). It keeps the prose OUT: for the producer's own closed list of workspace-preparation refusals, the reply is stated from the
// refusal's CODE, in this office's words, and only two scalar details are kept.
//
// REACH, exactly: the twelve codes in the table below, which is the producer's `DURABLE_WORKSPACE_PROBLEMS` (target_runtime/mission_bridge.py; tests/test_mission_panel_refusal_parity.py reads
// that list and fails if this table differs from it). For any OTHER refusal the adapter's prose and details are relayed as before, and nothing here examines them for paths.

/** Appended to every code-based sentence, so the person knows the adapter's own wording was left out and why. */
export const WORKSPACE_REFUSAL_SUFFIX =
  'The adapter’s own wording names locations on this machine, so it is not shown here.';

// BEGIN WORKSPACE_REFUSAL_CODES
/** What each workspace-preparation refusal says, from its CODE alone: no path, no git output, nothing the producer's prose carried. */
export const WORKSPACE_REFUSAL_WORDS: Readonly<Record<string, string>> = {
  'mission_bridge_repository_unavailable':
    'The repository these Mission workspaces are made from is not usable: it is not configured, is not a readable local checkout, or is not the approved project.',
  'mission_bridge_workspace_root_unavailable':
    'The folder where Mission workspaces are made is not usable: it is not configured, is not an existing writable folder, or is inside a repository. It must be an existing folder outside every repository.',
  'mission_bridge_workspace_collision':
    'Something already exists where this Mission’s workspace would go and it is not a workspace of the configured repository. It is never adopted.',
  'mission_bridge_workspace_foreign':
    'A workspace at the expected place cannot be shown to have been prepared by DI for this Mission, so its ownership is not provable and it is not adopted.',
  'mission_bridge_workspace_conflict':
    'The workspace on record for this Mission does not match what is there now: it is missing, has moved, or does not belong to the configured repository. A recorded workspace is never re-created, pruned or reset.',
  'mission_bridge_workspace_active':
    'The workspace holds Herdr state that this Mission’s run does not represent, so a herd may be at work there. Nothing is started over it.',
  'mission_bridge_workspace_busy':
    'Another Mission workspace preparation holds the lock on the workspaces folder. Nothing was created; trying again inspects the workspace again.',
  'mission_bridge_workspace_preparation_failed':
    'Git did not confirm the workspace. The next attempt inspects whatever is there and adopts only an exact workspace.',
  'mission_bridge_workspace_identity':
    'The workspace’s origin repository could not be read, or is not the approved repository for this Mission.',
  'mission_bridge_workspace_not_clean':
    'The workspace’s state could not be read or is not clean: uncommitted content would be indistinguishable from the run’s own work, so it is kept as it is.',
  'mission_bridge_baseline_unreadable':
    'The workspace’s starting commit could not be read as a full commit id, so there is no baseline to run against.',
  'mission_bridge_workspace_trust':
    'Workspace trust is not established, so a started herd would stop at the trust dialog. Nothing was started.',
};
// END WORKSPACE_REFUSAL_CODES

/** The two details of such a refusal this office keeps (whether the refusal was recorded durably, and which request it is); every other detail is left out. */
function keptDetails(details: unknown): Record<string, unknown> {
  const kept: Record<string, unknown> = {};
  if (!isObject(details)) return kept;
  if (typeof details.refusal_recorded === 'boolean') kept.refusal_recorded = details.refusal_recorded;
  if (typeof details.request_ref === 'string' && GRAMMAR.requestRef.test(details.request_ref)) {
    kept.request_ref = details.request_ref;
  }
  return kept;
}

/** The reply for the adapter's HTTP answer (the adapter's own words, bounded and typed). */
export function replyFromPanel(
  parsed: Extract<ParsedMissionRequest, { ok: true }>,
  result: PanelResult,
): DiMissionRequestReply {
  const { seq, op } = parsed;
  if (result.kind === 'transport_error') return transportReply(seq, op, result.problem);
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
  if (result.status !== 200) {
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
  // ONLY ok === true and ok === false are the panel's definite answers. An answer that is not an object, or an object stating
  // neither, is UNREADABLE: it states no refusal, so it is never read as one.
  if (!isObject(body) || (body.ok !== true && body.ok !== false)) return unreadable(seq, op);
  if (body.ok === true) return reply(seq, op, 'ok', null, null, body);
  const problem =
    typeof body.problem === 'string' && GRAMMAR.problem.test(body.problem)
      ? body.problem
      : 'adapter_problem_unrecognised';
  if (WRITES.has(op) && Object.hasOwn(AFTER_HANDLING, problem)) {
    return reply(seq, op, 'unknown', problem, AFTER_HANDLING[problem], null);
  }
  // A workspace-preparation refusal (the producer's closed list, above) is stated from its code: the adapter's prose and its other details are NOT relayed.
  if (Object.prototype.hasOwnProperty.call(WORKSPACE_REFUSAL_WORDS, problem)) {
    return reply(seq, op, 'refused', problem, `${WORKSPACE_REFUSAL_WORDS[problem]} ${WORKSPACE_REFUSAL_SUFFIX}`, {
      details: keptDetails(body.details),
    });
  }
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
      // Inside the correlation domain: one correlated refusal. Outside it: no reply, and nothing invented.
      const refusal = invalidMissionRequestReply(parsed);
      if (refusal !== null) answer(refusal);
      return;
    }
    if (this.discoveryPath === null) {
      answer(panelNotConfiguredReply(parsed));
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
          `The request panel's discovery file could not be used (${discovery.problem}). Whether the panel is running is not known.`,
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
