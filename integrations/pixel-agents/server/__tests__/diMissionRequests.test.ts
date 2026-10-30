/**
 * Item 4 — the DI server's side of the Mission request panel, with no socket and no process:
 *  1. the closed grammar of `diMissionRequest` (hostile input is refused before anything is sent);
 *  2. the reply mapping, and the handler with an injected request function in place of the panel
 *     client (the honesty of a failure that leaves a write's outcome unknown, the bound on calls in
 *     flight, and that the bearer token and a person's words never reach a log line);
 *  3. the office's dispatch of a `diMissionRequest`.
 *
 * The panel client against an in-process HTTP stand-in, and the REAL request panel adapter driven
 * through the real handler, are in diMissionRequestsTransport.test.ts. The request-panel repair
 * moved them there unchanged and did not run them; that file's header says what that leaves unknown.
 */
import { beforeEach, describe, expect, it } from 'vitest';

import type { DiMissionRequestReply } from '../../core/src/messages.js';
import { AgentStateStore } from '../src/agentStateStore.js';
import { handleDiClientMessage } from '../src/di/diClientMessages.js';
import type { PanelCall, PanelResult } from '../src/di/missionPanelClient.js';
import { isPanelRoute } from '../src/di/missionPanelClient.js';
import {
  DiMissionRequests,
  MAX_CONCURRENT_PANEL_CALLS,
  MAX_MISSION_REPLY_CHARS,
  MAX_MISSION_TEXT_CHARS,
  parseMissionRequest,
  replyFromPanel,
} from '../src/di/missionRequests.js';

const REF = `lr-${'a'.repeat(32)}`;
const CONV = `cv-${'b'.repeat(32)}`;
const TURN = `ct-${'c'.repeat(32)}`;
const MISSION = `mn-${'d'.repeat(32)}`;
const DIGEST = 'e'.repeat(64);
const TOKEN = 'secret-bearer-token-do-not-leak';

const OFFER_DIGEST = '9'.repeat(64);
const decide = (extra: Record<string, unknown> = {}): Record<string, unknown> => ({
  type: 'diMissionRequest',
  seq: 1,
  op: 'decide',
  requestRef: REF,
  missionId: MISSION,
  revision: 1,
  digest: DIGEST,
  reply: 'approve',
  offerDigest: OFFER_DIGEST,
  expiresAt: 1_000_900,
  scope: ['engineering_change', 'repository_read'],
  deliveryTargets: [],
  repositoryUrl: 'https://github.com/Example/Repo',
  ...extra,
});

/**
 * Which operations may change the record, by what each adapter route DOES (mission_panel/http_adapter.py and service.py), not by
 * its name. The four GET routes read. The seven POST routes may change the record; among them, offer records the frozen offer,
 * and checkApproval attests an approval of unknown outcome.
 */
const READS = ['refresh', 'conversation', 'request', 'result'] as const;
const WRITES = ['offer', 'turn', 'submit', 'decide', 'checkApproval', 'withdraw', 'run'] as const;
type Op = (typeof READS)[number] | (typeof WRITES)[number];
const FIELDS: Record<Exclude<Op, 'decide'>, Record<string, unknown>> = {
  refresh: {},
  conversation: { conversationId: CONV },
  request: { requestRef: REF },
  result: { requestRef: REF },
  offer: { requestRef: REF },
  turn: { text: 'hello' },
  submit: { turnId: TURN, digest: DIGEST },
  checkApproval: { requestRef: REF },
  withdraw: { requestRef: REF, missionId: MISSION, revision: 1, digest: DIGEST },
  run: { requestRef: REF, command: 'dispatch' },
};
const message = (op: Op, seq = 9): Record<string, unknown> =>
  op === 'decide' ? decide({ seq }) : { type: 'diMissionRequest', seq, op, ...FIELDS[op] };
const parsedAs = (op: Op): Extract<ReturnType<typeof parseMissionRequest>, { ok: true }> => {
  const parsed = parseMissionRequest(message(op));
  if (!parsed.ok) throw new Error(`fixture ${op}`);
  return parsed;
};

describe('parseMissionRequest: a closed grammar', () => {
  const good: Array<[string, Record<string, unknown>, PanelCall]> = [
    ['refresh', { op: 'refresh' }, { method: 'GET', path: '/overview' }],
    [
      'conversation',
      { op: 'conversation', conversationId: CONV },
      { method: 'GET', path: `/conversations/${CONV}` },
    ],
    ['request', { op: 'request', requestRef: REF }, { method: 'GET', path: `/requests/${REF}` }],
    [
      'offer',
      { op: 'offer', requestRef: REF },
      { method: 'POST', path: `/requests/${REF}/offer`, body: {} },
    ],
    [
      'result',
      { op: 'result', requestRef: REF },
      { method: 'GET', path: `/requests/${REF}/result` },
    ],
    [
      'turn (new)',
      { op: 'turn', text: 'make the probe stable' },
      {
        method: 'POST',
        path: '/conversations/turn',
        body: { conversation_id: null, text: 'make the probe stable' },
      },
    ],
    [
      'turn (existing)',
      { op: 'turn', conversationId: CONV, text: 'and keep it fast' },
      {
        method: 'POST',
        path: '/conversations/turn',
        body: { conversation_id: CONV, text: 'and keep it fast' },
      },
    ],
    [
      'submit',
      { op: 'submit', turnId: TURN, digest: DIGEST },
      {
        method: 'POST',
        path: '/proposals/submit',
        body: { turn_id: TURN, draft_digest_sha256: DIGEST, supersedes: null },
      },
    ],
    [
      'submit (revision)',
      { op: 'submit', turnId: TURN, digest: DIGEST, supersedes: REF },
      {
        method: 'POST',
        path: '/proposals/submit',
        body: { turn_id: TURN, draft_digest_sha256: DIGEST, supersedes: REF },
      },
    ],
    [
      'decide',
      {
        op: 'decide',
        requestRef: REF,
        missionId: MISSION,
        revision: 3,
        digest: DIGEST,
        reply: 'approve',
        offerDigest: OFFER_DIGEST,
        expiresAt: 1_000_900,
        scope: ['engineering_change'],
        deliveryTargets: [],
        repositoryUrl: 'https://github.com/Example/Repo',
      },
      {
        method: 'POST',
        path: `/requests/${REF}/decide`,
        body: {
          mission_id: MISSION,
          revision: 3,
          proposal_digest_sha256: DIGEST,
          reply: 'approve',
          offer_digest_sha256: OFFER_DIGEST,
          expires_at: 1_000_900,
          approved_action_scope: ['engineering_change'],
          approved_delivery_targets: [],
          repository_url: 'https://github.com/Example/Repo',
        },
      },
    ],
    [
      'checkApproval',
      { op: 'checkApproval', requestRef: REF },
      { method: 'POST', path: `/requests/${REF}/check-approval`, body: {} },
    ],
    [
      'withdraw',
      { op: 'withdraw', requestRef: REF, missionId: MISSION, revision: 1, digest: DIGEST },
      {
        method: 'POST',
        path: `/requests/${REF}/withdraw`,
        body: { mission_id: MISSION, revision: 1, proposal_digest_sha256: DIGEST },
      },
    ],
    [
      'run',
      { op: 'run', requestRef: REF, command: 'dispatch' },
      { method: 'POST', path: `/requests/${REF}/run`, body: { command: 'dispatch' } },
    ],
  ];

  for (const [name, fields, call] of good) {
    it(`${name}: exactly its keys become exactly its call`, () => {
      const parsed = parseMissionRequest({ type: 'diMissionRequest', seq: 7, ...fields });
      expect(parsed).toEqual({ ok: true, seq: 7, op: fields.op, call });
      expect(isPanelRoute((parsed as { call: PanelCall }).call)).toBe(true);
    });
  }

  it('every call it can build is one of the adapter routes, and no other', () => {
    for (const [, fields] of good) {
      const parsed = parseMissionRequest({ type: 'diMissionRequest', seq: 0, ...fields });
      expect(parsed.ok).toBe(true);
      if (parsed.ok) expect(isPanelRoute(parsed.call)).toBe(true);
    }
    for (const bad of [
      { method: 'GET' as const, path: '/catalog' },
      { method: 'GET' as const, path: '/shell' },
      { method: 'POST' as const, path: '/overview' },
      { method: 'GET' as const, path: `/requests/${REF}/decide` },
      { method: 'POST' as const, path: `/requests/../overview` },
      { method: 'GET' as const, path: `/requests/${REF}?x=1` },
      { method: 'GET' as const, path: `//overview` },
    ]) {
      expect(isPanelRoute(bad)).toBe(false);
    }
  });

  const refuse = (msg: Record<string, unknown>): string => {
    const parsed = parseMissionRequest(msg);
    expect(parsed.ok).toBe(false);
    return parsed.ok ? '' : parsed.reason;
  };

  it('refuses a wrong type, a bad seq and an unknown or inherited op', () => {
    expect(refuse({ type: 'other', seq: 1, op: 'refresh' })).toMatch(/not a diMissionRequest/);
    for (const seq of [-1, 1.5, 2 ** 31, '1', null, undefined, NaN, Infinity]) {
      expect(refuse({ type: 'diMissionRequest', seq, op: 'refresh' })).toMatch(/seq/);
    }
    for (const op of [
      'approve',
      'exec',
      'shell',
      'constructor',
      '__proto__',
      'toString',
      '',
      7,
      null,
    ]) {
      expect(refuse({ type: 'diMissionRequest', seq: 1, op })).toMatch(/op is not/);
    }
  });

  it('refuses any key the operation does not take, including authority-shaped ones', () => {
    for (const key of [
      'approved',
      'principal',
      'token',
      'path',
      'cwd',
      'shell',
      'argv',
      'model',
      'provider',
      'authorization',
    ]) {
      expect(refuse({ type: 'diMissionRequest', seq: 1, op: 'refresh', [key]: 'x' })).toMatch(
        /does not take/,
      );
      expect(refuse(decide({ [key]: 'x' }))).toMatch(/does not take/);
    }
  });

  it('refuses a missing required key', () => {
    for (const key of [
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
    ]) {
      const message = decide();
      delete message[key];
      expect(refuse(message)).toMatch(new RegExp(`needs "${key}"`));
    }
  });

  it('refuses ids outside their grammar: uppercase, paths, wrong prefix, wrong length', () => {
    const bad = [
      `LR-${'a'.repeat(32)}`,
      `lr-${'a'.repeat(31)}`,
      `lr-${'g'.repeat(32)}`,
      '../../etc/passwd',
      `lr-${'a'.repeat(32)}/x`,
      '',
      7,
      null,
      `${REF}\n`,
    ];
    for (const value of bad) {
      expect(
        refuse({ type: 'diMissionRequest', seq: 1, op: 'request', requestRef: value }),
      ).toMatch(/requestRef/);
      expect(refuse(decide({ requestRef: value }))).toMatch(/requestRef/);
    }
    expect(refuse(decide({ missionId: `mn-${'D'.repeat(32)}` }))).toMatch(/missionId/);
    expect(refuse(decide({ digest: 'E'.repeat(64) }))).toMatch(/digest/);
    expect(refuse(decide({ digest: 'e'.repeat(63) }))).toMatch(/digest/);
    expect(
      refuse({ type: 'diMissionRequest', seq: 1, op: 'conversation', conversationId: null }),
    ).toMatch(/conversationId/);
    expect(
      refuse({ type: 'diMissionRequest', seq: 1, op: 'submit', turnId: CONV, digest: DIGEST }),
    ).toMatch(/turnId/);
  });

  it('refuses a revision that is not a positive integer', () => {
    for (const revision of [0, -1, 1.5, '1', true, null, NaN]) {
      expect(refuse(decide({ revision }))).toMatch(/revision/);
    }
  });

  it('refuses a decision whose displayed offer is malformed, field by field', () => {
    const cases: Array<[string, Record<string, unknown>]> = [
      ['offerDigest', { offerDigest: 'A'.repeat(64) }],
      ['offerDigest', { offerDigest: '9'.repeat(63) }],
      ['offerDigest', { offerDigest: null }],
      ['expiresAt', { expiresAt: 0 }],
      ['expiresAt', { expiresAt: -5 }],
      ['expiresAt', { expiresAt: 1.5 }],
      ['expiresAt', { expiresAt: '1000900' }],
      ['expiresAt', { expiresAt: 5_000_000_000 }],
      ['scope', { scope: 'engineering_change' }],
      ['scope', { scope: ['Engineering Change'] }],
      ['scope', { scope: ['../../etc'] }],
      ['scope', { scope: Array.from({ length: 9 }, () => 'a') }],
      ['scope', { scope: [7] }],
      ['deliveryTargets', { deliveryTargets: 'none' }],
      ['deliveryTargets', { deliveryTargets: ['a b'] }],
      ['repositoryUrl', { repositoryUrl: 'file:///etc/passwd' }],
      ['repositoryUrl', { repositoryUrl: 'https://evil.example/Example/Repo' }],
      ['repositoryUrl', { repositoryUrl: 'https://github.com/Example/Repo/pull/3' }],
      ['repositoryUrl', { repositoryUrl: 7 }],
    ];
    for (const [key, change] of cases) {
      expect(refuse(decide(change)), JSON.stringify(change)).toMatch(new RegExp(key));
    }
    expect(parseMissionRequest(decide({ repositoryUrl: null })).ok).toBe(true);
  });

  it('refuses a run verb that is not one of the four', () => {
    for (const command of [
      'pause',
      'cancel',
      'prove',
      'dispatch; rm -rf',
      '',
      'DISPATCH',
      7,
      null,
    ]) {
      expect(
        refuse({ type: 'diMissionRequest', seq: 1, op: 'run', requestRef: REF, command }),
      ).toMatch(/command/);
    }
  });

  it('refuses unsafe words: empty, over the bound, lone surrogates, control bytes', () => {
    const turn = (text: unknown): Record<string, unknown> => ({
      type: 'diMissionRequest',
      seq: 1,
      op: 'turn',
      text,
    });
    for (const text of [
      '',
      '   ',
      '\n\t',
      7,
      null,
      undefined,
      'x'.repeat(MAX_MISSION_TEXT_CHARS + 1),
      'lone \ud800 surrogate',
      'lone \udc00 low',
      'a\u0000b',
      'a\u001bb',
      'del\u007f',
    ]) {
      expect(refuse(turn(text))).toBeTruthy();
    }
    expect(parseMissionRequest(turn('x'.repeat(MAX_MISSION_TEXT_CHARS))).ok).toBe(true);
    expect(parseMissionRequest(turn('line one\nline two\ttab\r\n')).ok).toBe(true);
    // The bound counts CHARACTERS (code points), as the adapter does, not UTF-16 units:
    expect(parseMissionRequest(turn('😀'.repeat(MAX_MISSION_TEXT_CHARS))).ok).toBe(true);
    expect(parseMissionRequest(turn('😀'.repeat(MAX_MISSION_TEXT_CHARS + 1))).ok).toBe(false);
  });

  it('relays the typed reply UNCHANGED, bounded, and judges nothing about it', () => {
    for (const reply of ['approve', 'Approve ', ' APPROVED', 'yes please', 'approve it']) {
      const parsed = parseMissionRequest(decide({ reply }));
      expect(parsed.ok).toBe(true);
      if (parsed.ok) expect((parsed.call.body as { reply: string }).reply).toBe(reply);
    }
    expect(parseMissionRequest(decide({ reply: 'x'.repeat(MAX_MISSION_REPLY_CHARS + 1) })).ok).toBe(
      false,
    );
    expect(parseMissionRequest(decide({ reply: '' })).ok).toBe(false);
    expect(parseMissionRequest(decide({ reply: 'app\u0000rove' })).ok).toBe(false);
    expect(parseMissionRequest(decide({ reply: { toString: () => 'approve' } })).ok).toBe(false);
  });

  it('keeps the seq of a refused message that had one, so the client can match the refusal', () => {
    const parsed = parseMissionRequest({
      type: 'diMissionRequest',
      seq: 41,
      op: 'run',
      requestRef: 'x',
      command: 'dispatch',
    });
    expect(parsed).toMatchObject({ ok: false, seq: 41, op: 'run' });
  });
});

describe('replyFromPanel: what the adapter said, and what a failure leaves unknown', () => {
  const parsed = parseMissionRequest(decide({ seq: 9 }));
  if (!parsed.ok) throw new Error('fixture');
  const http200 = (body: unknown): PanelResult => ({
    kind: 'http',
    status: 200,
    body,
    bodyProblem: null,
  });

  it("ok: the adapter's answer, typed", () => {
    expect(replyFromPanel(parsed, http200({ ok: true, request: { stage: 'x' } }))).toEqual({
      type: 'diMissionRequestReply',
      seq: 9,
      op: 'decide',
      state: 'ok',
      problem: null,
      reason: null,
      answer: { ok: true, request: { stage: 'x' } },
    });
  });

  it("refused: the adapter's code and words, with its details", () => {
    const reply = replyFromPanel(
      parsed,
      http200({
        ok: false,
        problem: 'mission_panel_display_stale',
        reason: 'not the one Mission Core holds',
        details: { binding: 'stale_revision' },
      }),
    );
    expect(reply).toMatchObject({
      state: 'refused',
      problem: 'mission_panel_display_stale',
      reason: 'not the one Mission Core holds',
      answer: { details: { binding: 'stale_revision' } },
    });
  });

  it('an unrecognisable problem code or an over-long reason is bounded, never relayed raw', () => {
    const reply = replyFromPanel(
      parsed,
      http200({
        ok: false,
        problem: 'Has Spaces <b>',
        reason: 'r'.repeat(5000),
        details: 'not an object',
      }),
    );
    expect(reply.problem).toBe('adapter_problem_unrecognised');
    expect((reply.reason ?? '').length).toBe(2000);
    expect(reply.answer).toEqual({ details: {} });
  });

  it('an answer at 200 that states no boolean ok is UNREADABLE: unknown for a write, unavailable for a read, never a refusal', () => {
    const unreadable: Array<[string, PanelResult]> = [
      ['text', http200('text')],
      ['an array', http200([1])],
      ['null', http200(null)],
      ['{}', http200({})],
      ['ok "true"', http200({ ok: 'true', problem: 'mission_panel_display_stale' })],
      ['ok 1', http200({ ok: 1 })],
      ['ok null', http200({ ok: null })],
      ['not JSON', { kind: 'http', status: 200, body: undefined, bodyProblem: 'body_not_json' }],
    ];
    for (const [what, result] of unreadable) {
      for (const op of WRITES) {
        const out = replyFromPanel(parsedAs(op), result);
        expect(out, `${op}, ${what}`).toMatchObject({
          seq: 9,
          op,
          state: 'unknown',
          problem: 'panel_answer_unreadable',
          answer: null,
        });
        expect(out.reason, `${op}, ${what}`).toMatch(/not known/);
        expect(out.reason, `${op}, ${what}`).not.toMatch(/Nothing was (sent|applied|changed)/);
      }
      for (const op of READS) {
        expect(replyFromPanel(parsedAs(op), result), `${op}, ${what}`).toMatchObject({
          state: 'unavailable',
          problem: 'panel_answer_unreadable',
        });
      }
    }
  });

  it('a non-200 is refused with its status, not trusted', () => {
    expect(
      replyFromPanel(parsed, {
        kind: 'http',
        status: 400,
        body: { error: 'bad_request' },
        bodyProblem: null,
      }),
    ).toMatchObject({ state: 'refused', problem: 'adapter_http_400', reason: 'bad_request' });
    expect(
      replyFromPanel(parsed, {
        kind: 'http',
        status: 403,
        body: undefined,
        bodyProblem: 'body_not_json',
      }),
    ).toMatchObject({ problem: 'adapter_http_403' });
  });

  it('401 is "unavailable: credentials rejected" (the panel restarted), never a refusal of the request', () => {
    expect(
      replyFromPanel(parsed, {
        kind: 'http',
        status: 401,
        body: { error: 'bearer_token_required' },
        bodyProblem: null,
      }),
    ).toMatchObject({ state: 'unavailable', problem: 'panel_credentials_rejected' });
  });

  it('a transport failure AFTER a write was sent is UNKNOWN; one before anything was sent, or on a read, is unavailable', () => {
    for (const op of [...WRITES, ...READS]) {
      for (const problem of ['panel_unreachable', 'panel_request_invalid'] as const) {
        const out = replyFromPanel(parsedAs(op), { kind: 'transport_error', problem });
        expect(out, `${op}, ${problem}`).toMatchObject({ state: 'unavailable', problem });
        expect(out.reason, `${op}, ${problem}`).toMatch(/Nothing was sent/);
        expect(out.reason, `${op}, ${problem}`).not.toMatch(/not (running|listening)/);
      }
    }
    for (const problem of ['panel_timeout', 'panel_network_error', 'panel_response_too_large'] as const) {
      for (const op of WRITES) {
        const out = replyFromPanel(parsedAs(op), { kind: 'transport_error', problem });
        expect(out, `${op}, ${problem}`).toMatchObject({ state: 'unknown', problem, answer: null });
        expect(out.reason, `${op}, ${problem}`).toMatch(/not known/);
        expect(out.reason, `${op}, ${problem}`).toMatch(/read the record again/);
        expect(out.reason, `${op}, ${problem}`).not.toMatch(/Nothing was (sent|applied|changed)/);
      }
      for (const op of READS) {
        const out = replyFromPanel(parsedAs(op), { kind: 'transport_error', problem });
        expect(out, `${op}, ${problem}`).toMatchObject({ state: 'unavailable', problem });
        // A read changes nothing, so it is not worded as a write of unknown outcome.
        expect(out.reason, `${op}, ${problem}`).not.toMatch(/not known/);
      }
    }
  });

  it("the panel's own report of an error, or of an oversized answer, after it handled a WRITE is UNKNOWN in this office's words; on a read it stays a refusal", () => {
    // The adapter's own wording for each (mission_panel/http_adapter.py); the byte count is illustrative.
    const answers = [
      ['mission_panel_internal', 'the panel hit an unexpected error; nothing is shown as changed'],
      [
        'mission_panel_response_too_large',
        'the answer was larger than the 1000 bytes this panel sends, so it was refused instead of being cut',
      ],
    ] as const;
    for (const [problem, said] of answers) {
      const result = http200({ ok: false, problem, reason: said, details: {} });
      for (const op of WRITES) {
        const out = replyFromPanel(parsedAs(op), result);
        expect(out, `${op}, ${problem}`).toMatchObject({ seq: 9, op, state: 'unknown', problem });
        expect(out.reason, `${op}, ${problem}`).toMatch(/not known/);
        expect(out.reason, `${op}, ${problem}`).not.toContain(said);
        expect(out.reason, `${op}, ${problem}`).not.toMatch(
          /Nothing was (sent|applied|changed)|nothing is shown as changed/i,
        );
      }
      for (const op of READS) {
        expect(replyFromPanel(parsedAs(op), result), `${op}, ${problem}`).toMatchObject({
          state: 'refused',
          problem,
          reason: said,
        });
      }
    }
  });
});

describe('DiMissionRequests: the handler', () => {
  const endpoint = { port: 4321, token: TOKEN };
  const discoveryOk = () => ({ ok: true as const, endpoint });
  let sent: DiMissionRequestReply[];
  let logs: string[];
  let calls: PanelCall[];
  const send = (m: Record<string, unknown>): void =>
    void sent.push(m as unknown as DiMissionRequestReply);

  beforeEach(() => {
    sent = [];
    logs = [];
    calls = [];
  });

  const make = (
    request: (e: typeof endpoint, c: PanelCall) => Promise<PanelResult>,
    discovery: string | null = '/disc.json',
    readDiscovery: (
      p: string,
    ) => ReturnType<typeof discoveryOk> | { ok: false; problem: 'discovery_absent' } = discoveryOk,
  ) =>
    new DiMissionRequests(discovery, {
      readDiscovery,
      request: async (e, c) => {
        calls.push(c);
        return request(e, c);
      },
      log: (l) => logs.push(l),
    });

  const ok = async (): Promise<PanelResult> => ({
    kind: 'http',
    status: 200,
    body: { ok: true },
    bodyProblem: null,
  });

  it('relays one valid request and answers the asking connection once', async () => {
    await make(ok).handle({ type: 'diMissionRequest', seq: 5, op: 'refresh' }, send);
    expect(sent).toHaveLength(1);
    expect(sent[0]).toMatchObject({
      type: 'diMissionRequestReply',
      seq: 5,
      op: 'refresh',
      state: 'ok',
    });
    expect(calls).toEqual([{ method: 'GET', path: '/overview' }]);
  });

  it('an invalid request is refused with its seq and is never sent', async () => {
    await make(ok).handle(
      { type: 'diMissionRequest', seq: 8, op: 'run', requestRef: REF, command: 'pause' },
      send,
    );
    expect(sent[0]).toMatchObject({
      seq: 8,
      op: 'run',
      state: 'refused',
      problem: 'request_invalid',
    });
    expect(calls).toEqual([]);
  });

  it('with no panel configured every request is "unavailable: not configured"', async () => {
    await make(ok, null).handle({ type: 'diMissionRequest', seq: 1, op: 'refresh' }, send);
    expect(sent[0]).toMatchObject({ state: 'unavailable', problem: 'panel_not_configured' });
    expect(calls).toEqual([]);
  });

  it('with the discovery file unusable nothing is sent, and the reply claims neither that the panel is running nor that it is not', async () => {
    await make(ok, '/disc.json', () => ({ ok: false, problem: 'discovery_absent' })).handle(
      { type: 'diMissionRequest', seq: 1, op: 'refresh' },
      send,
    );
    expect(sent[0]).toMatchObject({ state: 'unavailable', problem: 'panel_discovery_unavailable' });
    expect(sent[0].reason).toMatch(/discovery_absent/);
    expect(sent[0].reason).toMatch(/discovery file/);
    expect(sent[0].reason).not.toMatch(/not (running|listening)|is running(?! is not known)/);
    expect(calls).toEqual([]);
  });

  it('bounds the calls in flight and says "busy, nothing was sent" past the bound', async () => {
    const releases: Array<() => void> = [];
    let slowMode = true;
    const handler = make((): Promise<PanelResult> =>
      slowMode
        ? new Promise((resolve) =>
            releases.push(() =>
              resolve({ kind: 'http', status: 200, body: { ok: true }, bodyProblem: null }),
            ),
          )
        : ok(),
    );
    const first = Array.from({ length: MAX_CONCURRENT_PANEL_CALLS }, (_, i) =>
      handler.handle({ type: 'diMissionRequest', seq: i, op: 'refresh' }, send),
    );
    await Promise.resolve();
    await handler.handle({ type: 'diMissionRequest', seq: 99, op: 'refresh' }, send);
    expect(sent.at(-1)).toMatchObject({ seq: 99, state: 'refused', problem: 'office_busy' });
    expect(sent.at(-1)?.reason).toMatch(/Nothing was sent/);
    expect(calls).toHaveLength(MAX_CONCURRENT_PANEL_CALLS);
    slowMode = false;
    releases.forEach((r) => r());
    await Promise.all(first);
    // The slots are free again on the SAME handler once the calls finished ...
    await handler.handle({ type: 'diMissionRequest', seq: 100, op: 'refresh' }, send);
    expect(sent.at(-1)).toMatchObject({ seq: 100, state: 'ok' });
    // ... and a call that THROWS releases its slot too (a leak would exhaust the bound):
    let throwing = true;
    const flaky = make(async () => {
      if (throwing) throw new Error('boom');
      return ok();
    });
    for (let i = 0; i < MAX_CONCURRENT_PANEL_CALLS + 2; i++) {
      await flaky.handle({ type: 'diMissionRequest', seq: 200 + i, op: 'refresh' }, send);
      expect(sent.at(-1)).toMatchObject({ state: 'unavailable', problem: 'panel_network_error' });
    }
    throwing = false;
    await flaky.handle({ type: 'diMissionRequest', seq: 300, op: 'refresh' }, send);
    expect(sent.at(-1)).toMatchObject({ seq: 300, state: 'ok' });
  });

  it('a thrown request is an "unknown outcome" answer for a write and "unavailable" for a read, never a thrown error', async () => {
    const thrower = async (): Promise<PanelResult> => {
      throw new Error('socket exploded /Users/x/secret');
    };
    await expect(
      make(thrower).handle({ type: 'diMissionRequest', seq: 2, op: 'turn', text: 'hello' }, send),
    ).resolves.toBeUndefined();
    expect(sent[0]).toMatchObject({
      seq: 2,
      op: 'turn',
      state: 'unknown',
      problem: 'panel_network_error',
    });
    await make(thrower).handle({ type: 'diMissionRequest', seq: 3, op: 'refresh' }, send);
    expect(sent[1]).toMatchObject({
      seq: 3,
      op: 'refresh',
      state: 'unavailable',
      problem: 'panel_network_error',
    });
    expect(JSON.stringify(sent)).not.toContain('/Users/');
  });

  it('a message outside the correlation domain (no valid type, seq and op) gets no reply and nothing is sent; one inside it gets exactly one correlated refusal', async () => {
    for (const bad of [
      { type: 'diMissionRequest', op: 'refresh' },
      { type: 'diMissionRequest', seq: -1, op: 'refresh' },
      { type: 'diMissionRequest', seq: '1', op: 'refresh' },
      { type: 'diMissionRequest', seq: 1, op: 'exec' },
      { type: 'diMissionRequest', seq: 1 },
      { type: 'other', seq: 1, op: 'refresh' },
    ]) {
      await make(ok).handle(bad, send);
    }
    expect(sent).toEqual([]);
    expect(calls).toEqual([]);
    expect(logs.filter((l) => l.includes('mission request refused'))).toHaveLength(6);
    await make(ok).handle({ type: 'diMissionRequest', seq: 8, op: 'refresh', path: '/etc' }, send);
    expect(sent).toHaveLength(1);
    expect(sent[0]).toMatchObject({
      seq: 8,
      op: 'refresh',
      state: 'refused',
      problem: 'request_invalid',
    });
    expect(calls).toEqual([]);
  });

  it("never logs the token, a person's words, a typed reply or the adapter's answer", async () => {
    const words = 'private words about a customer named Zaphod';
    await make(async () => ({
      kind: 'http',
      status: 200,
      body: { ok: true, secret_result: 'RESULT-TEXT-XYZ' },
      bodyProblem: null,
    })).handle({ type: 'diMissionRequest', seq: 1, op: 'turn', text: words }, send);
    await make(ok).handle(decide({ reply: 'approve' }), send);
    const everything = logs.join('\n');
    for (const needle of [TOKEN, words, 'Zaphod', 'RESULT-TEXT-XYZ', 'approve', DIGEST])
      expect(everything).not.toContain(needle);
    expect(everything).toContain('turn ok');
    expect(JSON.stringify(sent)).not.toContain(TOKEN);
  });
});

describe('handleDiClientMessage: diMissionRequest', () => {
  const ctxBase = { store: new AgentStateStore(), cache: {} as never, extensionVersion: 'test' };
  it('reaches the relay only when the message is exactly valid', () => {
    const seen: Array<Record<string, unknown>> = [];
    const ctx = { ...ctxBase, missionRequest: (m: Record<string, unknown>) => void seen.push(m) };
    expect(
      handleDiClientMessage(
        { type: 'diMissionRequest', seq: 1, op: 'refresh' },
        () => undefined,
        ctx,
      ),
    ).toBe('handled');
    expect(seen).toHaveLength(1);
    for (const bad of [
      { type: 'diMissionRequest', seq: 1, op: 'refresh', path: '/etc' },
      { type: 'diMissionRequest', seq: -1, op: 'refresh' },
      { type: 'diMissionRequest', op: 'refresh' },
      { type: 'diMissionRequest', seq: 1, op: 'run', requestRef: REF, command: 'rm' },
    ]) {
      const disposition = handleDiClientMessage(bad, () => undefined, ctx);
      expect(disposition).toMatchObject({ refused: expect.stringContaining('diMissionRequest') });
    }
    expect(seen).toHaveLength(1);
  });
  it('is answered "unavailable: not configured", once and correlated, when this office has no panel relay', () => {
    const replies: Array<Record<string, unknown>> = [];
    expect(
      handleDiClientMessage(decide({ seq: 4 }), (m) => void replies.push(m), ctxBase),
    ).toMatchObject({ refused: 'no request panel in this office' });
    expect(replies).toHaveLength(1);
    expect(replies[0]).toMatchObject({
      type: 'diMissionRequestReply',
      seq: 4,
      op: 'decide',
      state: 'unavailable',
      problem: 'panel_not_configured',
      answer: null,
    });
  });

  it('answers a malformed message inside the correlation domain exactly once, correlated, and one outside it not at all; neither reaches the relay', () => {
    const seen: Array<Record<string, unknown>> = [];
    const ctx = { ...ctxBase, missionRequest: (m: Record<string, unknown>) => void seen.push(m) };
    const inDomain: Array<[Record<string, unknown>, string]> = [
      [{ type: 'diMissionRequest', seq: 1, op: 'refresh', path: '/etc' }, 'refresh'],
      [{ type: 'diMissionRequest', seq: 2, op: 'run', requestRef: REF, command: 'rm' }, 'run'],
      [decide({ seq: 3, reply: '' }), 'decide'],
    ];
    for (const [msg, op] of inDomain) {
      const replies: Array<Record<string, unknown>> = [];
      expect(handleDiClientMessage(msg, (m) => void replies.push(m), ctx)).toMatchObject({
        refused: expect.stringContaining('diMissionRequest'),
      });
      expect(replies, op).toHaveLength(1);
      expect(replies[0], op).toMatchObject({
        type: 'diMissionRequestReply',
        seq: msg.seq,
        op,
        state: 'refused',
        problem: 'request_invalid',
        answer: null,
      });
    }
    for (const msg of [
      { type: 'diMissionRequest', seq: -1, op: 'refresh' },
      { type: 'diMissionRequest', op: 'refresh' },
      { type: 'diMissionRequest', seq: 1, op: 'exec' },
      { type: 'diMissionRequest', seq: 1.5, op: 'refresh' },
    ]) {
      const replies: Array<Record<string, unknown>> = [];
      expect(handleDiClientMessage(msg, (m) => void replies.push(m), ctx)).toMatchObject({
        refused: expect.stringContaining('diMissionRequest'),
      });
      expect(replies, JSON.stringify(msg)).toEqual([]);
    }
    expect(seen).toEqual([]);
  });
});
