/**
 * (b2b) The DI server's side of the office delivery adapter (owner decision (b), the delivery bridge).
 *
 * A client sends `diDeliveryRequest`. The server checks it is EXACTLY one of two closed operations, relays it to the office delivery
 * adapter's loopback interface (didelivery_office.py, a process the person starts) with the bearer token only the server holds, and answers
 * the ASKING connection with exactly one `diDeliveryReply`, correlated by seq and op.
 *
 * `status` reads the delivery and the one step eligible now. `advanceOnce` asks the adapter for AT MOST ONE step. It carries the
 * confirmation: the exact binding of that step as the adapter's status gave it. pr_delivery's expected-step guard refuses it before any
 * effect unless the binding still matches the record. The confirmation goes back with the adapter's OWN four keys and their values
 * unchanged; nothing here renames it (core/asyncapi.yaml, DiDeliveryConfirmation, says why).
 *
 * THE STATES (round 48). `unavailable` means NOTHING WAS CHANGED: nothing was sent, the credentials were rejected before any step, or the
 * request was a status read. `unknown` is an advanceOnce whose outcome nobody can know: no answer in time, a broken connection, an answer too
 * large, unreadable or withheld, or a server error. It is never `unavailable`, because "nothing changed" is a guarantee it cannot give.
 * UNREADABLE (round 49): an answer that is not an object, or an object stating NEITHER ok === true NOR ok === false. Only those two are
 * definite readings, so `refused` is never inferred from an answer that does not say it.
 *
 * What this module is not: it holds no authority and decides nothing. It sends at most ONE adapter request per message: there is one call
 * site, and no loop, retry or re-entry. A refusal, an unknown outcome or a transport failure is answered as it stands, and the client must
 * read the status again. It offers no shell, no PTY, no filesystem read, no process and no command.
 *
 * THE TRANSPORT IS INJECTED, AND THERE IS NO DEFAULT: this relay is built only with a `request` it is given, and refuses to be built
 * without one. The HTTP client to the adapter is deliveryClient.ts, and the office entry injects it (diCli.ts, `deliveryRelay`).
 *
 * THE TOKEN (a hardening the Lead authorized; a disclosed conditional limit, not an observed leak). The token goes only into the transport
 * call. A reply whose serialized form contains it EXACTLY is withheld: an advance's becomes `unknown`, a status read's `unavailable`.
 * The log carries only THIS module's own problem codes; any word that came from the adapter is logged as the fixed marker `adapter_word`.
 * LIMITS of the reply check: it matches the token as one exact substring, so a token split across fields would not match; and the
 * comparison is not constant-time, which is acceptable for a leak check and would not be for authentication.
 */

import type { DiDeliveryOp, DiDeliveryReply } from '../../../core/src/messages.js';
import type { BackendEndpoint, DiscoveryProblem, DiscoveryResult } from './backendDiscovery.js';
import { readBackendDiscovery } from './backendDiscovery.js';

/** One delivery call at a time per office: a delivery advances one step at a time, so a second concurrent call is refused, never queued. */
export const MAX_CONCURRENT_DELIVERY_CALLS = 1;
const MAX_SEQ = 2147483647;
const MAX_REVISION = 2147483647;
const MAX_RELAYED_REASON_CHARS = 2000;
const DELIVERY_ID = /^prd-[0-9a-f]{24}$/;
const DIGEST = /^[0-9a-f]{64}$/;
const PROBLEM_WORD = /^[a-z][a-z0-9_]{0,80}$/;
const STEPS: ReadonlySet<string> = new Set(['BASE_REFRESH', 'COMMIT', 'PUSH', 'PR_CREATE']);
/** The adapter's own confirmation keys (office_delivery/adapter.py, _CONFIRMATION_KEYS). Never renamed. */
const CONFIRMATION_KEYS = ['delivery_id', 'revision', 'candidate_identity_digest_sha256', 'step'] as const;
/** Each operation's exact keys. Nothing else is accepted. */
const OPERATION_KEYS: Readonly<Record<DiDeliveryOp, readonly string[]>> = {
  status: ['type', 'seq', 'op', 'deliveryId'],
  advanceOnce: ['type', 'seq', 'op', 'deliveryId', 'confirmation'],
};
/** The problem codes THIS module produces: the only ones its log lines carry. */
const OWN_CODES: ReadonlySet<string> = new Set([
  'request_invalid',
  'delivery_not_configured',
  'delivery_discovery_unavailable',
  'office_busy_advance_in_flight',
  'office_busy_status_in_flight',
  'delivery_credentials_rejected',
  'delivery_unreachable',
  'delivery_timeout',
  'delivery_network_error',
  'delivery_response_too_large',
  'delivery_request_invalid',
  'delivery_answer_unreadable',
  'adapter_problem_unrecognised',
  'adapter_answer_withheld',
]);
const HTTP_CODE = /^adapter_http_\d{3}$/;
const UNKNOWN_OUTCOME = 'Whether it took a step is not known; read the delivery status again before acting.';

export interface DeliveryCall {
  method: 'GET' | 'POST';
  path: string;
  body: Record<string, unknown> | null;
}

export type DeliveryTransportProblem =
  | 'delivery_unreachable'
  | 'delivery_timeout'
  | 'delivery_network_error'
  | 'delivery_response_too_large'
  | 'delivery_request_invalid';

export type DeliveryResult =
  | { kind: 'http'; status: number; body: unknown }
  | { kind: 'transport_error'; problem: DeliveryTransportProblem };

export type ParsedDeliveryRequest =
  | { ok: true; seq: number; op: DiDeliveryOp; deliveryId: string; call: DeliveryCall }
  | { ok: false; seq: number | null; op: DiDeliveryOp | null; reason: string };

function isObject(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

/** Exactly these own keys: a SET question, so nothing is sorted or coerced, and a prototype name is just another key. */
function hasExactlyKeys(value: Record<string, unknown>, keys: readonly string[]): boolean {
  return Object.keys(value).length === keys.length && keys.every((key) => Object.hasOwn(value, key));
}

/**
 * A `diDeliveryRequest`, EXACTLY: `type`, `seq`, `op`, and that operation's own keys, each in its closed grammar. Anything else is
 * refused, never repaired. It reads properties, tests patterns and checks own keys and types; it calls nothing that reaches outside.
 */
export function parseDeliveryRequest(msg: Record<string, unknown>): ParsedDeliveryRequest {
  const seq =
    typeof msg.seq === 'number' && Number.isInteger(msg.seq) && msg.seq >= 0 && msg.seq <= MAX_SEQ ? msg.seq : null;
  const op = typeof msg.op === 'string' && Object.hasOwn(OPERATION_KEYS, msg.op) ? (msg.op as DiDeliveryOp) : null;
  // THE CORRELATION DOMAIN (owed item 1, as diMissionRequest has it): a refusal carries this message's seq and op only when its type,
  // seq and op are ALL valid. Outside the domain neither is invented: both are null, and the message gets no reply.
  const correlated = msg.type === 'diDeliveryRequest' && seq !== null && op !== null;
  const refuse = (reason: string): ParsedDeliveryRequest => ({
    ok: false,
    seq: correlated ? seq : null,
    op: correlated ? op : null,
    reason,
  });
  if (msg.type !== 'diDeliveryRequest') return refuse('not a diDeliveryRequest');
  if (seq === null) return refuse('seq must be a non-negative integer');
  if (op === null) return refuse('op is not one of the two operations');
  if (!hasExactlyKeys(msg, OPERATION_KEYS[op])) return refuse(`${op} takes exactly its own keys`);
  const deliveryId = msg.deliveryId;
  if (typeof deliveryId !== 'string' || !DELIVERY_ID.test(deliveryId)) return refuse('deliveryId is not a delivery id');
  if (op === 'status') {
    return { ok: true, seq, op, deliveryId, call: { method: 'GET', path: `/deliveries/${deliveryId}/status`, body: null } };
  }
  const c = msg.confirmation;
  if (!isObject(c) || !hasExactlyKeys(c, CONFIRMATION_KEYS)) {
    return refuse("the confirmation must carry exactly the adapter's four keys");
  }
  if (typeof c.delivery_id !== 'string' || c.delivery_id !== deliveryId) return refuse('the confirmation names a different delivery');
  if (typeof c.revision !== 'number' || !Number.isInteger(c.revision) || c.revision < 1 || c.revision > MAX_REVISION) {
    return refuse('the confirmation revision is not a revision');
  }
  if (typeof c.candidate_identity_digest_sha256 !== 'string' || !DIGEST.test(c.candidate_identity_digest_sha256)) {
    return refuse('the confirmation digest is not a sha256 digest');
  }
  if (typeof c.step !== 'string' || !STEPS.has(c.step)) return refuse('the confirmation step is not a delivery step');
  // The same four keys, the same values: the binding returns exactly as the adapter issued it.
  const body: Record<string, unknown> = {
    delivery_id: c.delivery_id,
    revision: c.revision,
    candidate_identity_digest_sha256: c.candidate_identity_digest_sha256,
    step: c.step,
  };
  return { ok: true, seq, op, deliveryId, call: { method: 'POST', path: `/deliveries/${deliveryId}/advance-once`, body } };
}

function reply(
  seq: number,
  op: DiDeliveryOp,
  state: DiDeliveryReply['state'],
  problem: string | null,
  reason: string | null,
  answer: Record<string, unknown> | null,
): DiDeliveryReply {
  return { type: 'diDeliveryReply', seq, op, state, problem, reason, answer };
}

/**
 * The reply to a message this office will not send: correlated by ITS OWN seq and op inside the correlation domain; null, NO reply,
 * for a message outside it. No seq or op is ever invented (owed item 1).
 */
export function invalidRequestReply(
  parsed: Extract<ParsedDeliveryRequest, { ok: false }>,
): DiDeliveryReply | null {
  if (parsed.seq === null || parsed.op === null) return null;
  return reply(parsed.seq, parsed.op, 'refused', 'request_invalid', parsed.reason, null);
}

/** The reply when this office has no delivery adapter: nothing was sent, so nothing was changed. */
export function notConfiguredReply(parsed: Extract<ParsedDeliveryRequest, { ok: true }>): DiDeliveryReply {
  return reply(parsed.seq, parsed.op, 'unavailable', 'delivery_not_configured', 'This office was started without a delivery adapter.', null);
}

/** A failure that leaves an advance's outcome unknowable is `unknown`; the same failure on a status read is `unavailable` (a read changes nothing). */
function unknowable(op: DiDeliveryOp): DiDeliveryReply['state'] {
  return op === 'advanceOnce' ? 'unknown' : 'unavailable';
}

/** What a transport failure means for the person: what is established, and for an advance after sending, that the outcome is NOT KNOWN. */
function transportReply(seq: number, op: DiDeliveryOp, problem: DeliveryTransportProblem): DiDeliveryReply {
  switch (problem) {
    case 'delivery_unreachable':
      return reply(seq, op, 'unavailable', problem, "Nothing answered at the address in the delivery adapter's discovery file. Nothing was sent.", null);
    case 'delivery_request_invalid':
      return reply(seq, op, 'unavailable', problem, 'This office refused to send that request. Nothing was sent.', null);
    case 'delivery_timeout':
      return reply(seq, op, unknowable(op), problem, op === 'advanceOnce' ? `The delivery adapter did not answer in time. ${UNKNOWN_OUTCOME}` : 'The delivery adapter did not answer in time.', null);
    case 'delivery_network_error':
      return reply(seq, op, unknowable(op), problem, op === 'advanceOnce' ? `The connection to the delivery adapter failed. ${UNKNOWN_OUTCOME}` : 'The connection to the delivery adapter failed.', null);
    case 'delivery_response_too_large':
      return reply(seq, op, unknowable(op), problem, op === 'advanceOnce' ? `The delivery adapter sent more than this office will read. ${UNKNOWN_OUTCOME}` : 'The delivery adapter sent more than this office will read.', null);
  }
}

/** An answer that states no definite outcome: `unknown` for an advance (it may have run), `unavailable` for a status read. */
function unreadable(seq: number, op: DiDeliveryOp): DiDeliveryReply {
  return reply(
    seq,
    op,
    unknowable(op),
    'delivery_answer_unreadable',
    op === 'advanceOnce' ? `The delivery adapter's answer could not be read. ${UNKNOWN_OUTCOME}` : "The delivery adapter's answer could not be read.",
    null,
  );
}

/** The reply for the adapter's answer: its own plain problem words only, its answer relayed as UNTRUSTED data. */
export function replyFromDelivery(parsed: Extract<ParsedDeliveryRequest, { ok: true }>, result: DeliveryResult): DiDeliveryReply {
  const { seq, op } = parsed;
  if (result.kind === 'transport_error') return transportReply(seq, op, result.problem);
  const body = result.body;
  if (result.status === 401) {
    return reply(
      seq,
      op,
      'unavailable',
      'delivery_credentials_rejected',
      "The delivery adapter did not accept this office's credentials, before taking any step. It may have restarted; start it again and reopen the office.",
      null,
    );
  }
  if (result.status >= 500) {
    return reply(seq, op, unknowable(op), `adapter_http_${result.status}`, op === 'advanceOnce' ? `The delivery adapter answered HTTP ${result.status}. ${UNKNOWN_OUTCOME}` : `The delivery adapter answered HTTP ${result.status}.`, null);
  }
  if (result.status !== 200) {
    const word =
      isObject(body) && typeof body.problem === 'string' && PROBLEM_WORD.test(body.problem) ? body.problem : `adapter_http_${result.status}`;
    return reply(seq, op, 'refused', word, `The delivery adapter answered HTTP ${result.status}.`, null);
  }
  // ROUND 49: ONLY ok === true and ok === false are the adapter's definite answers. An answer that is not an object, or an object stating
  // neither (no ok, a string, a number, null), is UNREADABLE: it states no refusal, so it is never read as one. (A refusal itself claims
  // no more about what changed than the adapter's answer says: the adapter answers an exception during a step with ok false too.)
  if (!isObject(body) || (body.ok !== true && body.ok !== false)) return unreadable(seq, op);
  if (body.ok === true) return reply(seq, op, 'ok', null, null, body);
  const problem = typeof body.problem === 'string' && PROBLEM_WORD.test(body.problem) ? body.problem : 'adapter_problem_unrecognised';
  const detail =
    problem !== 'adapter_problem_unrecognised' && isObject(body.result) && typeof body.result.detail === 'string'
      ? body.result.detail.slice(0, MAX_RELAYED_REASON_CHARS)
      : null;
  return reply(seq, op, 'refused', problem, detail, body);
}

/** A reply that would carry the token, withheld: the state the loss of the answer leaves. Withholding loses whatever the adapter said, so an advance's outcome becomes unknown. */
function withheld(out: DiDeliveryReply): DiDeliveryReply {
  return reply(
    out.seq,
    out.op,
    unknowable(out.op),
    'adapter_answer_withheld',
    out.op === 'advanceOnce'
      ? `The delivery adapter's answer was withheld because it carried this office's credentials. ${UNKNOWN_OUTCOME}`
      : "The delivery adapter's answer was withheld because it carried this office's credentials.",
    null,
  );
}

/** The problem as a log line may carry it: this module's own codes, or the fixed marker for a word that came from the adapter. */
function loggable(problem: string | null): string {
  if (problem === null) return 'ok';
  return OWN_CODES.has(problem) || HTTP_CODE.test(problem) ? problem : 'adapter_word';
}

export interface DeliveryRequestDeps {
  readDiscovery: (filePath: string) => DiscoveryResult;
  request: (endpoint: BackendEndpoint, call: DeliveryCall) => Promise<DeliveryResult>;
  log: (line: string) => void;
}

/**
 * b3: the refusal when the one-call slot is held, by WHICH operation holds it. Two conditions, two codes: a renderer branches on the
 * code, never on the prose. "Nothing was sent" is said of THIS request only. An advance in flight is "in progress", which is true of
 * the request; its step "may still be running", which stays uncertain, because the adapter may have finished with the answer still in
 * flight. A restart frees this office's slot; it is not cancellation, and it does not settle the step's outcome.
 */
function busyReply(seq: number, op: DiDeliveryOp, holder: DiDeliveryOp): DiDeliveryReply {
  return holder === 'advanceOnce'
    ? reply(
        seq,
        op,
        'refused',
        'office_busy_advance_in_flight',
        "An advance this office requested is still waiting for the delivery adapter's answer, and its step may still be running. Nothing was sent for this request. Wait for that answer and read the status again, or restart the office deliberately: a restart frees this office's request slot, is not cancellation, and leaves the step's outcome to be read from the delivery status.",
        null,
      )
    : reply(
        seq,
        op,
        'refused',
        'office_busy_status_in_flight',
        "A status read this office requested is still waiting for the delivery adapter's answer. Nothing was sent for this request; ask again when that read answers.",
        null,
      );
}

export class DiDeliveryRequests {
  private readonly deps: DeliveryRequestDeps;
  private inFlight = 0;
  /** The operation holding the one-call slot (MAX_CONCURRENT_DELIVERY_CALLS is 1, so there is at most one), or null when it is free. */
  private slotHolder: DiDeliveryOp | null = null;

  /**
   * @param discoveryPath the delivery adapter's discovery file (READ ONLY), or null when this office was started without one: every
   *   request is then answered `unavailable`.
   * @param deps `request` is REQUIRED: there is no default transport.
   */
  constructor(
    private readonly discoveryPath: string | null,
    deps: Partial<DeliveryRequestDeps> & Pick<DeliveryRequestDeps, 'request'>,
  ) {
    if (typeof deps?.request !== 'function') {
      throw new Error('DiDeliveryRequests needs an injected delivery transport; it has no default.');
    }
    this.deps = {
      readDiscovery: deps.readDiscovery ?? readBackendDiscovery,
      request: deps.request,
      log: deps.log ?? ((line) => console.log(line)),
    };
  }

  /**
   * Answer one `diDeliveryRequest` to the asking connection only, with ONE adapter request at most. The two calls that reach outside this
   * module, the discovery read and the transport, are each caught and turned into a reply.
   */
  async handle(msg: Record<string, unknown>, send: (message: Record<string, unknown>) => void): Promise<void> {
    const parsed = parseDeliveryRequest(msg);
    const answer = (r: DiDeliveryReply): void => send(r as unknown as Record<string, unknown>);
    if (!parsed.ok) {
      this.deps.log(`[Pixel Agents DI] delivery request refused: ${parsed.op ?? 'unknown'} invalid`);
      // Inside the correlation domain: one correlated refusal. Outside it: no reply, and nothing invented.
      const refusal = invalidRequestReply(parsed);
      if (refusal !== null) answer(refusal);
      return;
    }
    if (this.discoveryPath === null) {
      answer(notConfiguredReply(parsed));
      return;
    }
    let discovery: DiscoveryResult;
    try {
      discovery = this.deps.readDiscovery(this.discoveryPath);
    } catch {
      const problem: DiscoveryProblem = 'discovery_unreadable';
      discovery = { ok: false, problem };
    }
    if (!discovery.ok) {
      // What is established is that the discovery file could not be used. Whether the adapter runs is NOT established by that (round 48).
      answer(
        reply(
          parsed.seq,
          parsed.op,
          'unavailable',
          'delivery_discovery_unavailable',
          `The delivery adapter's discovery file could not be used (${discovery.problem}). Whether the adapter is running is not known.`,
          null,
        ),
      );
      return;
    }
    if (this.inFlight >= MAX_CONCURRENT_DELIVERY_CALLS) {
      // A held slot always has its holder recorded; were it ever missing, the cautious wording (an advance may be running) is used.
      answer(busyReply(parsed.seq, parsed.op, this.slotHolder ?? 'advanceOnce'));
      return;
    }
    this.inFlight++;
    this.slotHolder = parsed.op;
    let result: DeliveryResult;
    try {
      try {
        result = await this.deps.request(discovery.endpoint, parsed.call);
      } catch {
        result = { kind: 'transport_error', problem: 'delivery_network_error' };
      }
    } finally {
      this.inFlight--;
      this.slotHolder = null;
    }
    let out = replyFromDelivery(parsed, result);
    const token = discovery.endpoint.token;
    if (typeof token === 'string' && token !== '' && JSON.stringify(out).includes(token)) out = withheld(out);
    this.deps.log(`[Pixel Agents DI] delivery request: ${out.op} ${out.state} ${loggable(out.problem)}`);
    answer(out);
  }
}
