/**
 * (b2b) The DI server's side of the office delivery adapter: `diDeliveryRequest` in, exactly one `diDeliveryReply` out, to the asking
 * connection only.
 *
 * NO REAL TRANSPORT ON ANY PATH HERE: the relay's HTTP request is ALWAYS an injected stand-in, and the relay refuses to be built without one.
 * Nothing in this file opens a socket, reads a real discovery file or starts a process.
 *
 * What this pins:
 *  - THE PARSER: exactly two closed operations, each with exactly its own keys. A delivery id of exactly `prd-` and 24 hex. For advanceOnce,
 *    a confirmation of EXACTLY the adapter's four snake_case keys, each of exactly its type, naming the same delivery. The inputs supplied
 *    here are built to break the check: prototype names, wrong types, camelCase keys, near-miss digests. Each is refused, none repaired.
 *  - THE ROUND-TRIP: the body sent to the adapter is the confirmation's four keys with their values UNCHANGED, never renamed.
 *  - THE STATES, round 48. `unavailable` means NOTHING WAS CHANGED. `unknown` is the advanceOnce outcome nobody can know: a timeout, a broken
 *    connection, an answer too large or unreadable, or a server error. A client must never read it as safe to retry.
 *  - THE WORDING, round 48: unusable discovery says the discovery file could not be used, NEVER that the adapter is not running.
 *  - THE TOKEN: no reply and no log line carries the adapter's token, even when the adapter's OWN answer carries it.
 *  - THE RELAY: ONE adapter request per message and never a second, whatever the first produced. Nothing is sent when not configured, when
 *    discovery is unusable or when the message is invalid. At most MAX_CONCURRENT_DELIVERY_CALLS in flight; the rest are refused without
 *    sending.
 */

import { describe, expect, it } from 'vitest';

import type { BackendEndpoint, DiscoveryResult } from '../src/di/backendDiscovery.js';
import type { DeliveryCall, DeliveryResult } from '../src/di/deliveryRequests.js';
import {
  DiDeliveryRequests,
  invalidRequestReply,
  MAX_CONCURRENT_DELIVERY_CALLS,
  parseDeliveryRequest,
  replyFromDelivery,
} from '../src/di/deliveryRequests.js';

const ID = 'prd-0123456789abcdef01234567';
const BINDING = { delivery_id: ID, revision: 2, candidate_identity_digest_sha256: 'c'.repeat(64), step: 'COMMIT' };
const TOKEN = 'tok-'.padEnd(68, 'z');
/** A token shaped like the real ones, 64 lowercase hex, beginning with a LETTER: it matches the plain-word pattern entirely. */
const HEX_TOKEN = `a${'b3'.repeat(31)}c`;
const endpointWith = (token: string): BackendEndpoint => ({ url: 'http://127.0.0.1:43210', port: 43210, token }) as unknown as BackendEndpoint;
const ENDPOINT = endpointWith(TOKEN);
const found = (endpoint: BackendEndpoint): DiscoveryResult => ({ ok: true, endpoint }) as unknown as DiscoveryResult;

const status = (extra: Record<string, unknown> = {}): Record<string, unknown> => ({ type: 'diDeliveryRequest', seq: 7, op: 'status', deliveryId: ID, ...extra });
const advance = (confirmation: unknown = { ...BINDING }, extra: Record<string, unknown> = {}): Record<string, unknown> => ({
  type: 'diDeliveryRequest',
  seq: 8,
  op: 'advanceOnce',
  deliveryId: ID,
  confirmation,
  ...extra,
});

function relay(opts: { discoveryPath?: string | null; discovery?: DiscoveryResult; endpoint?: BackendEndpoint; result?: () => Promise<DeliveryResult> } = {}) {
  const calls: Array<{ endpoint: BackendEndpoint; call: DeliveryCall }> = [];
  const logs: string[] = [];
  const sent: Array<Record<string, unknown>> = [];
  const r = new DiDeliveryRequests(opts.discoveryPath === undefined ? '/abs/delivery.json' : opts.discoveryPath, {
    readDiscovery: () => opts.discovery ?? found(opts.endpoint ?? ENDPOINT),
    request: (endpoint, call) => {
      calls.push({ endpoint, call });
      return (opts.result ?? (async () => ({ kind: 'http', status: 200, body: { ok: true, status: { delivery: {} } } }) as DeliveryResult))();
    },
    log: (line) => logs.push(line),
  });
  return { r, calls, logs, sent, send: (m: Record<string, unknown>) => sent.push(m) };
}

describe('parseDeliveryRequest: exactly two closed operations, each with exactly its own keys', () => {
  it('status parses to one GET of the delivery status route, with no body', () => {
    const parsed = parseDeliveryRequest(status());
    expect(parsed).toEqual({ ok: true, seq: 7, op: 'status', deliveryId: ID, call: { method: 'GET', path: `/deliveries/${ID}/status`, body: null } });
  });

  it('advanceOnce parses to one POST of the advance-once route whose body is the confirmation with its four keys and values UNCHANGED', () => {
    const parsed = parseDeliveryRequest(advance());
    expect(parsed.ok).toBe(true);
    if (!parsed.ok) return;
    expect(parsed.call.method).toBe('POST');
    expect(parsed.call.path).toBe(`/deliveries/${ID}/advance-once`);
    expect(parsed.call.body).toEqual(BINDING);
    expect(Object.keys(parsed.call.body ?? {}).sort()).toEqual(['candidate_identity_digest_sha256', 'delivery_id', 'revision', 'step']);
  });

  it('refuses every message that is not exactly one of the two, and echoes a valid seq (built to break the check, not merely wrong)', () => {
    const cases: Array<[string, Record<string, unknown>]> = [
      ['wrong type', { ...status(), type: 'diMissionRequest' }],
      ['seq a string', status({ seq: '7' })],
      ['seq negative', status({ seq: -1 })],
      ['seq fractional', status({ seq: 1.5 })],
      ['seq too large', status({ seq: 2 ** 31 })],
      ['op unknown', status({ op: 'advance' })],
      ['op a prototype name', status({ op: '__proto__' })],
      ['op constructor', status({ op: 'constructor' })],
      ['op hasOwnProperty', status({ op: 'hasOwnProperty' })],
      ['status carrying a confirmation', status({ confirmation: { ...BINDING } })],
      ['status with an extra key', status({ extra: 1 })],
      ['advanceOnce without a confirmation', { type: 'diDeliveryRequest', seq: 8, op: 'advanceOnce', deliveryId: ID }],
      ['advanceOnce with an extra key', advance({ ...BINDING }, { force: true })],
      ['deliveryId absent', { type: 'diDeliveryRequest', seq: 7, op: 'status' }],
      ['deliveryId upper-case', status({ deliveryId: ID.toUpperCase() })],
      ['deliveryId short', status({ deliveryId: 'prd-0123' })],
      ['deliveryId not hex', status({ deliveryId: `prd-${'g'.repeat(24)}` })],
      ['deliveryId with a path', status({ deliveryId: `${ID}/../x` })],
      ['deliveryId a number', status({ deliveryId: 7 })],
      ['confirmation an array', advance([ID, 2, 'c'.repeat(64), 'COMMIT'])],
      ['confirmation null', advance(null)],
      ['confirmation a string', advance(JSON.stringify(BINDING))],
      ['confirmation missing a key', advance({ delivery_id: ID, revision: 2, step: 'COMMIT' })],
      ['confirmation with an extra key', advance({ ...BINDING, extra: 1 })],
      ['confirmation with camelCase keys', advance({ deliveryId: ID, revision: 2, candidateIdentityDigestSha256: 'c'.repeat(64), step: 'COMMIT' })],
      ['confirmation with a __proto__ key (as JSON.parse makes it)', advance(JSON.parse(`{"delivery_id":"${ID}","revision":2,"candidate_identity_digest_sha256":"${'c'.repeat(64)}","__proto__":"COMMIT"}`))],
      ['revision zero', advance({ ...BINDING, revision: 0 })],
      ['revision fractional', advance({ ...BINDING, revision: 1.5 })],
      ['revision a string', advance({ ...BINDING, revision: '2' })],
      ['revision too large', advance({ ...BINDING, revision: 2 ** 31 })],
      ['digest upper-case', advance({ ...BINDING, candidate_identity_digest_sha256: 'C'.repeat(64) })],
      ['digest 63 characters', advance({ ...BINDING, candidate_identity_digest_sha256: 'c'.repeat(63) })],
      ['digest a number', advance({ ...BINDING, candidate_identity_digest_sha256: 7 })],
      ['step unknown', advance({ ...BINDING, step: 'MERGE' })],
      ['step lower-case', advance({ ...BINDING, step: 'commit' })],
      ['confirmation naming another delivery', advance({ ...BINDING, delivery_id: 'prd-ffffffffffffffffffffffff' })],
    ];
    for (const [name, msg] of cases) {
      const parsed = parseDeliveryRequest(msg);
      expect(parsed.ok, name).toBe(false);
      if (!parsed.ok) {
        expect(typeof parsed.reason, name).toBe('string');
        // Owed item 1: the correlation domain. Inside it (a valid type, seq and op) the refusal carries THAT seq and op; outside it,
        // neither is invented: both are null. Stated here, never read from the product under test.
        const validSeq =
          typeof msg.seq === 'number' &&
          Number.isInteger(msg.seq) &&
          msg.seq >= 0 &&
          msg.seq < 2 ** 31;
        const validOp = msg.op === 'status' || msg.op === 'advanceOnce';
        const correlated = msg.type === 'diDeliveryRequest' && validSeq && validOp;
        expect(parsed.seq, name).toBe(correlated ? msg.seq : null);
        expect(parsed.op, name).toBe(correlated ? msg.op : null);
      }
    }
  });

  it('owed item 1: a refusal inside the correlation domain carries its own seq and op; outside it there is NO reply, and nothing is invented', () => {
    const inside = parseDeliveryRequest(status({ deliveryId: 'prd-0123' }));
    expect(inside.ok).toBe(false);
    if (!inside.ok) {
      expect(invalidRequestReply(inside)).toMatchObject({
        type: 'diDeliveryReply',
        seq: 7,
        op: 'status',
        state: 'refused',
        problem: 'request_invalid',
        answer: null,
      });
    }
    const outside: Array<[string, Record<string, unknown>]> = [
      ['seq a string', status({ seq: '7' })],
      ['op unknown', status({ op: 'advance' })],
      ['wrong type', { ...status(), type: 'diMissionRequest' }],
    ];
    expect(outside.map(([name]) => name)).toEqual(['seq a string', 'op unknown', 'wrong type']);
    for (const [name, msg] of outside) {
      const parsed = parseDeliveryRequest(msg);
      expect(parsed.ok, name).toBe(false);
      if (!parsed.ok) expect(invalidRequestReply(parsed), name).toBeNull();
    }
  });

  it('an op outside the closed two parses with NO op: a prototype name is never taken for an operation', () => {
    for (const op of ['advance', '__proto__', 'constructor', 'hasOwnProperty', 'toString']) {
      const parsed = parseDeliveryRequest(status({ op }));
      expect(parsed.ok, op).toBe(false);
      if (!parsed.ok) expect(parsed.op, op).toBeNull();
    }
  });

  it('these four malformed shapes (an empty object, a bare type, a null-prototype object, an extra key named toString) are refused without a throw', () => {
    const shapes: unknown[] = [{}, { type: 'diDeliveryRequest' }, Object.create(null), { type: 'diDeliveryRequest', seq: 1, op: 'status', deliveryId: ID, toString: 1 }];
    for (const msg of shapes) expect(() => parseDeliveryRequest(msg as Record<string, unknown>)).not.toThrow();
  });
});

describe('replyFromDelivery: the adapter answer, said in this office reply shape', () => {
  const st = parseDeliveryRequest(status());
  const adv = parseDeliveryRequest(advance());
  if (!st.ok || !adv.ok) throw new Error('fixtures must parse');

  it('the reply always has exactly its seven keys and echoes seq and op', () => {
    const out = replyFromDelivery(st, { kind: 'http', status: 200, body: { ok: true, status: {} } });
    expect(Object.keys(out).sort()).toEqual(['answer', 'op', 'problem', 'reason', 'seq', 'state', 'type']);
    expect([out.type, out.seq, out.op]).toEqual(['diDeliveryReply', 7, 'status']);
  });

  it('unreachable is unavailable: nothing was sent, and nothing is claimed about whether the adapter runs', () => {
    const out = replyFromDelivery(adv, { kind: 'transport_error', problem: 'delivery_unreachable' });
    expect([out.state, out.problem, out.answer]).toEqual(['unavailable', 'delivery_unreachable', null]);
    expect(out.reason).toBe("Nothing answered at the address in the delivery adapter's discovery file. Nothing was sent.");
  });

  it('ROUND 48: an advanceOnce whose outcome nobody can know is UNKNOWN, never unavailable, and says the outcome is not known', () => {
    for (const problem of ['delivery_timeout', 'delivery_network_error', 'delivery_response_too_large'] as const) {
      const out = replyFromDelivery(adv, { kind: 'transport_error', problem });
      expect([out.state, out.problem, out.answer], problem).toEqual(['unknown', problem, null]);
      expect(out.reason, problem).toMatch(/Whether it took a step is not known/);
    }
    expect(replyFromDelivery(adv, { kind: 'http', status: 500, body: {} })).toMatchObject({ state: 'unknown', problem: 'adapter_http_500' });
    for (const body of [null, 'ok', [true], 7]) {
      expect(replyFromDelivery(adv, { kind: 'http', status: 200, body })).toMatchObject({ state: 'unknown', problem: 'delivery_answer_unreadable' });
    }
  });

  it('ROUND 48: the same failures on a status READ are unavailable, since a read changes nothing; never ok', () => {
    for (const problem of ['delivery_timeout', 'delivery_network_error', 'delivery_response_too_large'] as const) {
      expect(replyFromDelivery(st, { kind: 'transport_error', problem }).state, problem).toBe('unavailable');
    }
    for (const body of [null, 'ok', [true], 7]) {
      expect(replyFromDelivery(st, { kind: 'http', status: 200, body })).toMatchObject({ state: 'unavailable', problem: 'delivery_answer_unreadable' });
    }
  });

  // ROUND 49: the boundary the non-object cases above stop at. These answers ARE objects, but none states a boolean acceptance or
  // refusal, so none is a definite fact: an advance may have run (unknown), and a status read changes nothing (unavailable).
  it('ROUND 49: an OBJECT answer with no boolean ok (absent, a string, a number, null) is UNREADABLE: unknown for an advance, unavailable for a status read', () => {
    for (const body of [{}, { ok: 'false' }, { ok: 'true' }, { ok: 1 }, { ok: 0 }, { ok: null }, { problem: 'guard_refused', result: {} }]) {
      const name = JSON.stringify(body);
      expect(replyFromDelivery(adv, { kind: 'http', status: 200, body }), name).toMatchObject({ state: 'unknown', problem: 'delivery_answer_unreadable', answer: null });
      expect(replyFromDelivery(st, { kind: 'http', status: 200, body }), name).toMatchObject({ state: 'unavailable', problem: 'delivery_answer_unreadable', answer: null });
    }
  });

  it('ROUND 49: only ok === true and ok === false are definite readings, the one ok and the other refused', () => {
    expect(replyFromDelivery(adv, { kind: 'http', status: 200, body: { ok: true } }).state).toBe('ok');
    expect(replyFromDelivery(adv, { kind: 'http', status: 200, body: { ok: false, problem: 'guard_refused', result: {} } }).state).toBe('refused');
    expect(replyFromDelivery(st, { kind: 'http', status: 200, body: { ok: false, problem: 'not_found' } }).state).toBe('refused');
  });

  it('a rejected token is unavailable, not a refusal of the request', () => {
    const out = replyFromDelivery(st, { kind: 'http', status: 401, body: { ok: false, problem: 'unauthorized' } });
    expect([out.state, out.problem]).toEqual(['unavailable', 'delivery_credentials_rejected']);
  });

  it('an HTTP refusal (4xx) is refused, with the adapter word only when it is a plain word', () => {
    expect(replyFromDelivery(adv, { kind: 'http', status: 400, body: { ok: false, problem: 'malformed_body' } })).toMatchObject({ state: 'refused', problem: 'malformed_body' });
    expect(replyFromDelivery(st, { kind: 'http', status: 404, body: 'Not found' })).toMatchObject({ state: 'refused', problem: 'adapter_http_404' });
    expect(replyFromDelivery(st, { kind: 'http', status: 400, body: { ok: false, problem: '../../etc/passwd' } })).toMatchObject({ state: 'refused', problem: 'adapter_http_400' });
  });

  it('an adapter ok is ok, its answer relayed as it is', () => {
    const body = { ok: true, result: { advanced: true, step: 'COMMIT', receipt: { receipt_id: 'r' } } };
    expect(replyFromDelivery(adv, { kind: 'http', status: 200, body })).toEqual({ type: 'diDeliveryReply', seq: 8, op: 'advanceOnce', state: 'ok', problem: null, reason: null, answer: body });
  });

  it('a domain refusal (200, ok false) is refused, with its plain problem word and its detail as the reason', () => {
    const body = { ok: false, problem: 'guard_refused', result: { advanced: false, refusal: 'guard_refused', detail: 'Nothing ran: the record no longer selects the confirmed step.' } };
    expect(replyFromDelivery(adv, { kind: 'http', status: 200, body })).toEqual({
      type: 'diDeliveryReply',
      seq: 8,
      op: 'advanceOnce',
      state: 'refused',
      problem: 'guard_refused',
      reason: 'Nothing ran: the record no longer selects the confirmed step.',
      answer: body,
    });
    const odd = { ok: false, problem: 'Not A Word!', result: {} };
    expect(replyFromDelivery(adv, { kind: 'http', status: 200, body: odd })).toMatchObject({ state: 'refused', problem: 'adapter_problem_unrecognised', reason: null });
  });
});

describe('DiDeliveryRequests: ONE adapter request per message, never a second, nothing sent when it should not be', () => {
  it('cannot be built without an injected transport: there is no default real one', () => {
    expect(() => new DiDeliveryRequests('/abs/delivery.json', {} as never)).toThrow(/transport/);
  });

  it('pins its concurrency bound at one delivery call at a time', () => {
    expect(MAX_CONCURRENT_DELIVERY_CALLS).toBe(1);
  });

  it('not configured: unavailable, nothing sent', async () => {
    const t = relay({ discoveryPath: null });
    await t.r.handle(status(), t.send);
    expect(t.calls).toEqual([]);
    expect(t.sent).toHaveLength(1);
    expect(t.sent[0]).toMatchObject({ type: 'diDeliveryReply', seq: 7, state: 'unavailable', problem: 'delivery_not_configured' });
  });

  it('ROUND 48: unusable discovery is unavailable, nothing sent, and its words say the FILE could not be used, never that the adapter is not running', async () => {
    const t = relay({ discovery: { ok: false, problem: 'discovery_unreadable' } as unknown as DiscoveryResult });
    await t.r.handle(advance(), t.send);
    expect(t.calls).toEqual([]);
    expect(t.sent).toEqual([expect.objectContaining({ state: 'unavailable', problem: 'delivery_discovery_unavailable' })]);
    expect(t.sent[0].reason).toBe("The delivery adapter's discovery file could not be used (discovery_unreadable). Whether the adapter is running is not known.");
    expect(t.sent[0].reason).not.toMatch(/not running/);
  });

  it('an invalid message: refused request_invalid, nothing sent', async () => {
    const t = relay();
    await t.r.handle(advance({ ...BINDING, step: 'MERGE' }), t.send);
    expect(t.calls).toEqual([]);
    expect(t.sent).toEqual([expect.objectContaining({ seq: 8, state: 'refused', problem: 'request_invalid' })]);
  });

  // OWED ITEM 1: this test used to require an answer "with an op from the closed two" for a message whose op was not one of them,
  // which meant an INVENTED op ('status'). A message outside the correlation domain now gets NO reply: nothing is echoed, and
  // nothing is invented.
  it('a message whose op is not one of the two, or whose seq is not one, gets NO reply and reaches nothing: no op or seq is invented', async () => {
    const cases: Array<[string, Record<string, unknown>]> = [
      ['op __proto__', status({ op: '__proto__' })],
      ['op constructor', status({ op: 'constructor' })],
      ['op toString', status({ op: 'toString' })],
      ['op advance', status({ op: 'advance' })],
      ['seq a string', status({ seq: 'x' })],
    ];
    expect(cases.length).toBe(5);
    for (const [name, msg] of cases) {
      const t = relay();
      await t.r.handle(msg, t.send);
      expect(t.calls, name).toEqual([]);
      expect(t.sent, name).toEqual([]);
    }
  });

  it('a valid status: exactly one request, to the discovered endpoint, with the parsed call, and exactly one reply', async () => {
    const t = relay();
    await t.r.handle(status(), t.send);
    expect(t.calls).toEqual([{ endpoint: ENDPOINT, call: { method: 'GET', path: `/deliveries/${ID}/status`, body: null } }]);
    expect(t.sent).toHaveLength(1);
    expect(t.sent[0]).toMatchObject({ seq: 7, op: 'status', state: 'ok' });
  });

  it('NO RETRY: a domain refusal, a timeout, a network error or a thrown request each make exactly ONE call; an unknowable advance is UNKNOWN', async () => {
    const outcomes: Array<[string, () => Promise<DeliveryResult>, string]> = [
      ['domain refusal', async () => ({ kind: 'http', status: 200, body: { ok: false, problem: 'guard_refused', result: {} } }), 'refused'],
      ['timeout', async () => ({ kind: 'transport_error', problem: 'delivery_timeout' }), 'unknown'],
      ['network error', async () => ({ kind: 'transport_error', problem: 'delivery_network_error' }), 'unknown'],
      ['rejected', async () => Promise.reject(new Error('socket hang up')), 'unknown'],
      [
        'thrown',
        () => {
          throw new Error('boom');
        },
        'unknown',
      ],
    ];
    for (const [name, result, state] of outcomes) {
      const t = relay({ result });
      await t.r.handle(advance(), t.send);
      expect(t.calls, name).toHaveLength(1);
      expect(t.sent, name).toHaveLength(1);
      expect(t.sent[0].state, name).toBe(state);
    }
  });

  // The second handle is NOT awaited before the assertions. Awaited first, a second call (the bound removed) would hang the test on the
  // stand-in's never-resolving promise, so the catching assertion was never reached (mutant R7 was killed only by a timeout). Unawaited,
  // a second call fails "expected 1 call" at once, on an assertion.
  // b3: the busy refusal names WHICH operation holds the one-call slot, by CODE (a renderer branches on the code, never on the prose).
  it('busy, an ADVANCE holding the slot: a second message is refused office_busy_advance_in_flight and NOT sent, in words that keep the step uncertain; the first still answers', async () => {
    const releases: Array<(r: DeliveryResult) => void> = [];
    const t = relay({ result: () => new Promise<DeliveryResult>((resolve) => releases.push(resolve)) });
    const first = t.r.handle(advance(), t.send);
    const second = t.r.handle(status(), t.send);
    expect(t.calls).toHaveLength(1);
    expect(t.sent).toEqual([
      expect.objectContaining({ seq: 7, op: 'status', state: 'refused', problem: 'office_busy_advance_in_flight' }),
    ]);
    const reason = String(t.sent[0].reason);
    expect(reason).toMatch(/advance/i);
    expect(reason).toMatch(/may still be running/);
    expect(reason).not.toMatch(/\bis (still )?running\b/);
    expect(reason).toMatch(/Nothing was sent for this request/);
    expect(reason).toMatch(/restart/i);
    expect(reason).toMatch(/not cancellation/);
    for (const release of releases) release({ kind: 'http', status: 200, body: { ok: true, result: { advanced: true } } });
    await Promise.all([first, second]);
    expect(t.sent).toHaveLength(2);
    expect(t.sent[1]).toMatchObject({ seq: 8, state: 'ok' });
  });

  it('busy, a STATUS READ holding the slot: a second message is refused office_busy_status_in_flight and NOT sent, and nothing in its words says a step may be running', async () => {
    const releases: Array<(r: DeliveryResult) => void> = [];
    const t = relay({ result: () => new Promise<DeliveryResult>((resolve) => releases.push(resolve)) });
    const first = t.r.handle(status(), t.send);
    const second = t.r.handle(advance(), t.send);
    expect(t.calls).toHaveLength(1);
    expect(t.sent).toEqual([
      expect.objectContaining({ seq: 8, op: 'advanceOnce', state: 'refused', problem: 'office_busy_status_in_flight' }),
    ]);
    const reason = String(t.sent[0].reason);
    expect(reason).toMatch(/status read/);
    expect(reason).not.toMatch(/step|advance/i);
    expect(reason).toMatch(/Nothing was sent for this request/);
    for (const release of releases) release({ kind: 'http', status: 200, body: { ok: true, status: { delivery: {} } } });
    await Promise.all([first, second]);
  });

  it('the slot holder follows the call that holds it: after an advance has answered, a status read in flight is the one named', async () => {
    const releases: Array<(r: DeliveryResult) => void> = [];
    const t = relay({ result: () => new Promise<DeliveryResult>((resolve) => releases.push(resolve)) });
    const first = t.r.handle(advance(), t.send);
    expect(t.calls).toHaveLength(1);
    releases[0]({ kind: 'http', status: 200, body: { ok: true, result: { advanced: true } } });
    await first;
    const reading = t.r.handle(status(), t.send);
    expect(t.calls).toHaveLength(2);
    const third = t.r.handle(advance(), t.send);
    expect(t.calls).toHaveLength(2);
    expect(t.sent.at(-1)).toMatchObject({ seq: 8, state: 'refused', problem: 'office_busy_status_in_flight' });
    releases[1]({ kind: 'http', status: 200, body: { ok: true, status: { delivery: {} } } });
    await Promise.all([reading, third]);
  });

  it('the token never reaches a reply or a log line in these outcomes (an ok, a 401, a timeout)', async () => {
    for (const result of [
      async (): Promise<DeliveryResult> => ({ kind: 'http', status: 200, body: { ok: true } }),
      async (): Promise<DeliveryResult> => ({ kind: 'http', status: 401, body: {} }),
      async (): Promise<DeliveryResult> => ({ kind: 'transport_error', problem: 'delivery_timeout' }),
    ]) {
      const t = relay({ result });
      await t.r.handle(advance(), t.send);
      expect(JSON.stringify(t.sent)).not.toContain(TOKEN);
      expect(t.logs.join('\n')).not.toContain(TOKEN);
      expect(t.logs.join('\n')).not.toContain('c'.repeat(64));
    }
  });

  it("THE ADAPTER'S OWN ANSWER CARRYING A TOKEN-SHAPED WORD: no reply and no log line carries the token", async () => {
    const crafted: Array<[string, () => Promise<DeliveryResult>]> = [
      ['a domain refusal whose problem IS the token', async () => ({ kind: 'http', status: 200, body: { ok: false, problem: HEX_TOKEN, result: {} } })],
      ['a 400 whose problem IS the token', async () => ({ kind: 'http', status: 400, body: { ok: false, problem: HEX_TOKEN } })],
      ['an ok answer carrying the token in its body', async () => ({ kind: 'http', status: 200, body: { ok: true, status: { note: HEX_TOKEN } } })],
    ];
    for (const [name, result] of crafted) {
      const t = relay({ endpoint: endpointWith(HEX_TOKEN), result });
      await t.r.handle(advance(), t.send);
      expect(t.sent, name).toHaveLength(1);
      expect(JSON.stringify(t.sent), name).not.toContain(HEX_TOKEN);
      expect(t.logs.join('\n'), name).not.toContain(HEX_TOKEN);
    }
  });

  it("the log carries only this module's own codes: a word that came from the adapter is logged as adapter_word, and travels only in the reply", async () => {
    const t = relay({ result: async () => ({ kind: 'http', status: 200, body: { ok: false, problem: 'guard_refused', result: {} } }) });
    await t.r.handle(advance(), t.send);
    expect(t.logs).toEqual(['[Pixel Agents DI] delivery request: advanceOnce refused adapter_word']);
    expect(t.sent[0].problem).toBe('guard_refused');
  });

  it('a withheld answer is answered by the state it would leave: an advance UNKNOWN (it may have run), a status read unavailable', async () => {
    const carrying = async (): Promise<DeliveryResult> => ({ kind: 'http', status: 200, body: { ok: true, note: HEX_TOKEN } });
    const a = relay({ endpoint: endpointWith(HEX_TOKEN), result: carrying });
    await a.r.handle(advance(), a.send);
    expect(a.sent[0]).toMatchObject({ seq: 8, op: 'advanceOnce', state: 'unknown', problem: 'adapter_answer_withheld', answer: null });
    const s = relay({ endpoint: endpointWith(HEX_TOKEN), result: carrying });
    await s.r.handle(status(), s.send);
    expect(s.sent[0]).toMatchObject({ seq: 7, op: 'status', state: 'unavailable', problem: 'adapter_answer_withheld', answer: null });
  });
});

describe('ROUND 48: the contract keeps an unknown outcome distinct from nothing-changed', () => {
  it('DiDeliveryState names unknown, and the reply description defines unavailable as nothing changed and unknown as not known', async () => {
    const fs = await import('fs');
    const path = await import('path');
    const { Parser } = await import('@asyncapi/parser');
    const spec = fs.readFileSync(path.join(__dirname, '../../core/asyncapi.yaml'), 'utf-8');
    const { document } = await new Parser().parse(spec);
    const schemas = document!.components().schemas();
    expect((schemas.get('DiDeliveryState')!.json() as { enum: string[] }).enum).toEqual(['ok', 'refused', 'unavailable', 'unknown']);
    const description = (schemas.get('DiDeliveryReply')!.json() as { description: string }).description;
    expect(description).toMatch(/unavailable: NOTHING WAS CHANGED/);
    expect(description).toMatch(/unknown: advanceOnce only[\s\S]*WHETHER A STEP WAS TAKEN IS NOT KNOWN/);
    expect(description).not.toMatch(/could not be\s+reached or is not configured \(nothing was changed\)/);
  });
});
