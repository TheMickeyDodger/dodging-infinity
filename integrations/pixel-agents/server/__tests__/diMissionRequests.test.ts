/**
 * Item 4 — the DI server's side of the Mission request panel.
 *
 * Three layers, strongest last:
 *  1. the closed grammar of `diMissionRequest` (hostile input is refused before anything is sent);
 *  2. the handler and the panel client against in-process HTTP stand-ins (reply mapping, the
 *     honesty of a failure that leaves a write's outcome unknown, the bound on calls in flight,
 *     and that the bearer token and a person's words never reach a log line);
 *  3. the REAL request panel adapter — `dimission_panel`'s HTTP adapter, controller, journal,
 *     restricted conversation, local request surface, Mission Core, operator-attested approval and
 *     `MissionBridge` — driven through the real handler. Only the Codex process, the process the
 *     bridge would spawn and the team's observed state are scripted (tests/_panel_e2e_server.py),
 *     which is what no test can have for real. Nothing here is evidence of a live provider or Herdr.
 */
import { type ChildProcessWithoutNullStreams, spawn } from 'child_process';
import * as fs from 'fs';
import * as http from 'http';
import type { AddressInfo } from 'net';
import * as os from 'os';
import * as path from 'path';
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it } from 'vitest';

import type { DiMissionRequestReply } from '../../core/src/messages.js';
import { AgentStateStore } from '../src/agentStateStore.js';
import { handleDiClientMessage } from '../src/di/diClientMessages.js';
import type { PanelCall, PanelResult } from '../src/di/missionPanelClient.js';
import {
  isPanelRoute,
  MAX_PANEL_REQUEST_BODY_BYTES,
  MAX_PANEL_RESPONSE_BYTES,
  requestPanel,
} from '../src/di/missionPanelClient.js';
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

  it('a body that is not an object, or a non-200, is refused with the status, not trusted', () => {
    expect(replyFromPanel(parsed, http200('text'))).toMatchObject({
      state: 'refused',
      problem: 'adapter_http_200',
    });
    expect(replyFromPanel(parsed, http200([1]))).toMatchObject({
      state: 'refused',
      problem: 'adapter_http_200',
    });
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

  it('a transport failure AFTER the send says the outcome is unknown; refused-to-connect says nothing was sent', () => {
    const unreachable = replyFromPanel(parsed, {
      kind: 'transport_error',
      problem: 'panel_unreachable',
    });
    expect(unreachable).toMatchObject({ state: 'unavailable', problem: 'panel_unreachable' });
    expect(unreachable.reason).toMatch(/Nothing was sent/);
    for (const problem of ['panel_timeout', 'panel_network_error'] as const) {
      const unknown = replyFromPanel(parsed, { kind: 'transport_error', problem });
      expect(unknown.state).toBe('unavailable');
      expect(unknown.reason).toMatch(/not known/);
      expect(unknown.reason).toMatch(/read the record again/);
      expect(unknown.reason).not.toMatch(/Nothing was (sent|applied)/);
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

  it('with the discovery file unreadable the panel is "not running", nothing is sent', async () => {
    await make(ok, '/disc.json', () => ({ ok: false, problem: 'discovery_absent' })).handle(
      { type: 'diMissionRequest', seq: 1, op: 'refresh' },
      send,
    );
    expect(sent[0]).toMatchObject({ state: 'unavailable', problem: 'panel_discovery_unavailable' });
    expect(sent[0].reason).toMatch(/discovery_absent/);
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

  it('a thrown request is an "unknown outcome" answer, never a thrown error', async () => {
    await expect(
      make(async () => {
        throw new Error('socket exploded /Users/x/secret');
      }).handle({ type: 'diMissionRequest', seq: 2, op: 'turn', text: 'hello' }, send),
    ).resolves.toBeUndefined();
    expect(sent[0]).toMatchObject({ state: 'unavailable', problem: 'panel_network_error' });
    expect(JSON.stringify(sent[0])).not.toContain('/Users/');
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
  it('is refused when this office has no panel relay', () => {
    expect(
      handleDiClientMessage(
        { type: 'diMissionRequest', seq: 1, op: 'refresh' },
        () => undefined,
        ctxBase,
      ),
    ).toMatchObject({ refused: 'no request panel in this office' });
  });
});

describe('requestPanel: the client (in-process HTTP stand-in)', () => {
  let server: http.Server;
  let port = 0;
  let seen: Array<{ req: http.IncomingMessage; body: string }> = [];
  let respond: (req: http.IncomingMessage, res: http.ServerResponse) => void;
  beforeEach(async () => {
    seen = [];
    respond = (_req, res) => {
      res.writeHead(200, { 'content-type': 'application/json' });
      res.end('{"ok":true}');
    };
    server = http.createServer((req, res) => {
      const chunks: Buffer[] = [];
      req.on('data', (c: Buffer) => chunks.push(c));
      req.on('end', () => {
        seen.push({ req, body: Buffer.concat(chunks).toString('utf-8') });
        respond(req, res);
      });
    });
    await new Promise<void>((r) => server.listen(0, '127.0.0.1', () => r()));
    port = (server.address() as AddressInfo).port;
  });
  afterEach(async () => {
    await new Promise<void>((r) => server.close(() => r()));
  });

  it('a GET carries the bearer, a Host naming the same listener, no Origin, no cookie, no body', async () => {
    const result = await requestPanel({ port, token: TOKEN }, { method: 'GET', path: '/overview' });
    expect(result).toMatchObject({ kind: 'http', status: 200, body: { ok: true } });
    const { req, body } = seen[0];
    expect(req.method).toBe('GET');
    expect(req.headers.authorization).toBe(`Bearer ${TOKEN}`);
    expect(req.headers.host).toBe(`127.0.0.1:${port}`);
    expect(req.headers.origin).toBeUndefined();
    expect(req.headers.cookie).toBeUndefined();
    expect(req.headers['content-length']).toBeUndefined();
    expect(body).toBe('');
  });

  it('a POST sends exactly its JSON with an exact Content-Length', async () => {
    await requestPanel(
      { port, token: TOKEN },
      {
        method: 'POST',
        path: '/conversations/turn',
        body: { conversation_id: null, text: 'héllo 😀' },
      },
    );
    const { req, body } = seen[0];
    expect(req.headers['content-type']).toBe('application/json');
    expect(Number(req.headers['content-length'])).toBe(Buffer.byteLength(body));
    expect(JSON.parse(body)).toEqual({ conversation_id: null, text: 'héllo 😀' });
  });

  it('refuses locally, before any socket, what the adapter would refuse', async () => {
    const before = seen.length;
    const refusals = await Promise.all([
      requestPanel({ port, token: TOKEN }, { method: 'GET', path: '/catalog' }),
      requestPanel({ port, token: TOKEN }, { method: 'POST', path: '/overview', body: {} }),
      requestPanel({ port, token: TOKEN }, { method: 'POST', path: '/conversations/turn' }),
      requestPanel({ port, token: TOKEN }, { method: 'GET', path: '/overview', body: { a: 1 } }),
      requestPanel(
        { port, token: TOKEN },
        {
          method: 'POST',
          path: '/conversations/turn',
          body: { text: 'x'.repeat(MAX_PANEL_REQUEST_BODY_BYTES) },
        },
      ),
    ]);
    for (const r of refusals)
      expect(r).toEqual({ kind: 'transport_error', problem: 'panel_request_invalid' });
    expect(seen.length).toBe(before);
  });

  it('bounds the response it will read', async () => {
    respond = (_req, res) => {
      res.writeHead(200);
      res.end('x'.repeat(MAX_PANEL_RESPONSE_BYTES + 10));
    };
    expect(
      await requestPanel({ port, token: TOKEN }, { method: 'GET', path: '/overview' }),
    ).toEqual({ kind: 'transport_error', problem: 'panel_response_too_large' });
  });

  it('a silent adapter is a timeout (outcome unknown); a closed port is unreachable (nothing sent)', async () => {
    respond = () => undefined;
    expect(
      await requestPanel({ port, token: TOKEN }, { method: 'GET', path: '/overview' }, 150),
    ).toEqual({ kind: 'transport_error', problem: 'panel_timeout' });
    const closed = http.createServer();
    await new Promise<void>((r) => closed.listen(0, '127.0.0.1', () => r()));
    const dead = (closed.address() as AddressInfo).port;
    await new Promise<void>((r) => closed.close(() => r()));
    expect(
      await requestPanel({ port: dead, token: TOKEN }, { method: 'GET', path: '/overview' }),
    ).toEqual({ kind: 'transport_error', problem: 'panel_unreachable' });
  });

  it('a non-JSON body is reported as such, not parsed into something else', async () => {
    respond = (_req, res) => {
      res.writeHead(200);
      res.end('<html>');
    };
    expect(
      await requestPanel({ port, token: TOKEN }, { method: 'GET', path: '/overview' }),
    ).toMatchObject({ kind: 'http', status: 200, bodyProblem: 'body_not_json' });
  });
});

// ── the REAL adapter ────────────────────────────────────────────────────────────────────────────

const REPO_ROOT = path.resolve(__dirname, '../../../..');
const HARNESS = path.join(REPO_ROOT, 'tests', '_panel_e2e_server.py');

class Harness {
  private child: ChildProcessWithoutNullStreams | null = null;
  private lines: string[] = [];
  private waiters: Array<(line: string) => void> = [];
  private buffer = '';
  readonly tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'di-e2e-'));
  readonly discovery = path.join(this.tmp, 'disc', 'panel.json');
  stderr = '';

  async start(): Promise<void> {
    this.child = spawn(
      'python3',
      [HARNESS, '--work-dir', path.join(this.tmp, 'w'), '--discovery-file', this.discovery],
      {
        env: { ...process.env, PYTHONPATH: REPO_ROOT, PYTHONDONTWRITEBYTECODE: '1' },
        stdio: ['pipe', 'pipe', 'pipe'],
      },
    );
    this.child.stdout.on('data', (d: Buffer) => {
      this.buffer += d.toString('utf-8');
      let i: number;
      while ((i = this.buffer.indexOf('\n')) >= 0) {
        const line = this.buffer.slice(0, i);
        this.buffer = this.buffer.slice(i + 1);
        const waiter = this.waiters.shift();
        if (waiter) waiter(line);
        else this.lines.push(line);
      }
    });
    this.child.stderr.on('data', (d: Buffer) => {
      this.stderr += d.toString('utf-8');
    });
    const hello = JSON.parse(await this.line(30_000)) as { endpoint: string };
    if (hello.endpoint !== 'mission_panel_e2e')
      throw new Error(`unexpected startup: ${JSON.stringify(hello)}`);
  }

  private line(timeoutMs: number): Promise<string> {
    const queued = this.lines.shift();
    if (queued !== undefined) return Promise.resolve(queued);
    return new Promise((resolve, reject) => {
      const timer = setTimeout(
        () =>
          reject(
            new Error(`harness silent for ${timeoutMs} ms; stderr: ${this.stderr.slice(0, 1500)}`),
          ),
        timeoutMs,
      );
      this.waiters.push((l) => {
        clearTimeout(timer);
        resolve(l);
      });
    });
  }

  async control(command: Record<string, unknown>): Promise<Record<string, unknown>> {
    this.child?.stdin.write(`${JSON.stringify(command)}\n`);
    return JSON.parse(await this.line(30_000)) as Record<string, unknown>;
  }

  async stop(): Promise<void> {
    const child = this.child;
    if (!child) return;
    const exited = new Promise<void>((resolve) => child.once('exit', () => resolve()));
    if (child.exitCode === null) {
      try {
        child.stdin.write('{"op":"stop"}\n');
      } catch {
        /* already gone */
      }
      const timer = setTimeout(() => child.kill('SIGTERM'), 10_000);
      await exited;
      clearTimeout(timer);
    }
    this.child = null;
  }

  token(): string {
    return (JSON.parse(fs.readFileSync(this.discovery, 'utf-8')) as { token: string }).token;
  }
}

describe('REAL request panel adapter, through the real handler', { timeout: 90_000 }, () => {
  let harness: Harness;
  let panel: DiMissionRequests;
  let seq = 0;
  const logs: string[] = [];
  const replies: DiMissionRequestReply[] = [];

  const ask = async (fields: Record<string, unknown>): Promise<DiMissionRequestReply> => {
    let got: DiMissionRequestReply | null = null;
    await panel.handle({ type: 'diMissionRequest', seq: ++seq, ...fields }, (m) => {
      got = m as unknown as DiMissionRequestReply;
    });
    if (got === null) throw new Error('no reply');
    const reply: DiMissionRequestReply = got;
    expect(reply.seq).toBe(seq);
    replies.push(reply);
    return reply;
  };
  type Json = Record<string, any>;
  const answer = (r: DiMissionRequestReply): Json => (r.answer ?? {}) as Json;

  const settled = async (conversationId: string): Promise<Json> => {
    for (let i = 0; i < 120; i++) {
      const view = answer(await ask({ op: 'conversation', conversationId }));
      if (view.turns.at(-1).state !== 'RESERVED') return view.turns.at(-1);
      await new Promise((r) => setTimeout(r, 100));
    }
    throw new Error('the conversation turn never settled');
  };

  const PROPOSAL = {
    objective: 'Make the readiness probe stable under load',
    target_context: 'the readiness subsystem',
    requested_scope: 'the probe and its tests; no schema change',
    requested_action_scope: ['repository_read', 'engineering_change'],
  };
  const reply = (text: string): string =>
    `${text}\n\n\`\`\`mission-proposal\n${JSON.stringify(PROPOSAL)}\n\`\`\`\n`;

  beforeAll(async () => {
    harness = new Harness();
    await harness.start();
    panel = new DiMissionRequests(harness.discovery, { log: (l) => logs.push(l) });
  }, 60_000);
  afterAll(async () => {
    await harness.stop();
    // Only the directory this harness made, by its exact path, directly under the temp root.
    if (path.dirname(harness.tmp) === os.tmpdir() && /^di-e2e-[A-Za-z0-9]{6}$/.test(path.basename(harness.tmp))) {
      fs.rmSync(harness.tmp, { recursive: true, force: true });
    }
  });

  it('walks request -> clarification -> exact proposal -> decision -> dispatch -> verified result', async () => {
    expect(answer(await ask({ op: 'refresh' })).requests).toEqual([]);
    await harness.control({ op: 'provider', reply: 'Which probe do you mean?' });
    const first = answer(await ask({ op: 'turn', text: 'make the probe stable' })).turn as Json;
    const clarifying = await settled(first.conversation_id);
    expect(clarifying).toMatchObject({
      state: 'COMPLETED',
      draft: null,
      provider_text: 'untrusted',
    });
    await harness.control({ op: 'provider', reply: reply('The readiness probe.') });
    const second = answer(
      await ask({
        op: 'turn',
        conversationId: first.conversation_id,
        text: 'the readiness probe, no schema change',
      }),
    ).turn as Json;
    const drafted = await settled(first.conversation_id);
    expect(second.sequence).toBe(2);
    expect(drafted.draft.proposal.requested_action_scope).toEqual([
      'engineering_change',
      'repository_read',
    ]);
    expect((await harness.control({ op: 'state' })).requests).toBe(0); // a draft is not of record

    const submitted = answer(
      await ask({
        op: 'submit',
        turnId: drafted.turn_id,
        digest: drafted.draft.proposal_digest_sha256,
      }),
    );
    const view = submitted.request as Json;
    expect(view.lifecycle.stage).toBe('of_record');
    expect(view.proposal_digest_sha256).toBe(drafted.draft.proposal_digest_sha256);
    const ref = view.request_ref as string;

    const offer = answer(await ask({ op: 'offer', requestRef: ref }));
    expect(offer).toMatchObject({ reply: 'approve', revision: 1, mission_id: view.mission_id });
    // A second window is shown the SAME frozen offer, with the same absolute expiry.
    const sameOffer = answer(await ask({ op: 'offer', requestRef: ref }));
    expect(sameOffer.offer_digest_sha256).toBe(offer.offer_digest_sha256);
    expect(sameOffer.expires_at).toBe(offer.expires_at);
    const bind = {
      requestRef: ref,
      missionId: offer.mission_id,
      revision: offer.revision,
      digest: offer.proposal_digest_sha256,
      offerDigest: offer.offer_digest_sha256,
      expiresAt: offer.expires_at,
      scope: offer.approved_action_scope,
      deliveryTargets: offer.approved_delivery_targets,
      repositoryUrl: offer.repository_url,
    };

    const notYet = await ask({ op: 'decide', ...bind, reply: 'yes, go ahead' });
    expect(notYet).toMatchObject({
      state: 'refused',
      problem: 'local_request_reply_not_affirmative',
    });
    expect((await harness.control({ op: 'state' })).authorizations).toBe(0);
    const stale = await ask({ op: 'decide', ...bind, digest: '0'.repeat(64), reply: 'approve' });
    expect(stale).toMatchObject({ state: 'refused', problem: 'mission_panel_display_stale' });
    expect(answer(stale).details).toMatchObject({
      display_invalid: true,
      binding: 'offer_tampered',
    });
    // The same offer with a different expiry is tampering too: the retained offer is the truth.
    const moved = await ask({
      op: 'decide',
      ...bind,
      expiresAt: (offer.expires_at as number) + 600,
      reply: 'approve',
    });
    expect(answer(moved).details).toMatchObject({ binding: 'offer_tampered' });

    const approved = answer(await ask({ op: 'decide', ...bind, reply: 'approve' }));
    expect(approved.request.lifecycle).toMatchObject({
      stage: 'approved',
      approval_expires_at: offer.expires_at,
    });
    expect(approved.request.lifecycle.provenance.proof).toBe(
      'operator_attested_not_independently_verified',
    );
    const again = answer(await ask({ op: 'decide', ...bind, reply: 'approve' }));
    expect(again.decision.idempotent).toBe(true);
    expect(await harness.control({ op: 'state' })).toMatchObject({
      authorizations: 1,
      spawn_calls: 0,
    });

    const started = answer(await ask({ op: 'run', requestRef: ref, command: 'dispatch' }));
    expect(started.request.lifecycle.stage).toBe('dispatched');
    expect(started.request.lifecycle.detail).toMatch(/exactly once/);
    const dup = await ask({ op: 'run', requestRef: ref, command: 'dispatch' });
    expect(dup).toMatchObject({ state: 'refused', problem: 'mission_panel_action_not_offered' });
    await harness.control({ op: 'team', status: 'RUNNING' });
    expect(
      answer(await ask({ op: 'run', requestRef: ref, command: 'observe' })).request.lifecycle.stage,
    ).toBe('running');
    const early = await ask({ op: 'run', requestRef: ref, command: 'verify' });
    expect(early).toMatchObject({ state: 'refused', problem: 'mission_panel_action_not_offered' });
    await harness.control({ op: 'artifacts', request_ref: ref });
    await harness.control({ op: 'team', status: 'COMPLETE' });
    const done = answer(await ask({ op: 'run', requestRef: ref, command: 'verify' }));
    expect(done.request.lifecycle.stage).toBe('verified');
    const result = answer(await ask({ op: 'result', requestRef: ref }));
    expect(result).toMatchObject({
      recoverable: true,
      delivered: false,
      delivery_authority: 'none',
    });
    expect(result.result_text).toContain('readiness probe fix landed');
    expect(await harness.control({ op: 'state' })).toMatchObject({
      spawn_calls: 1,
      authorizations: 1,
      requests: 1,
    });
  });

  it('a withdrawal is a withdrawal, and a restart loses the key honestly', async () => {
    await harness.control({ op: 'provider', reply: reply('Proposal A.') });
    const t = answer(await ask({ op: 'turn', text: 'another change' })).turn as Json;
    const d = await settled(t.conversation_id);
    const v = answer(
      await ask({ op: 'submit', turnId: d.turn_id, digest: d.draft.proposal_digest_sha256 }),
    ).request as Json;
    const bind = {
      requestRef: v.request_ref,
      missionId: v.mission_id,
      revision: v.revision,
      digest: v.proposal_digest_sha256,
    };
    await harness.control({ op: 'restart' });
    const noKey = await ask({ op: 'withdraw', ...bind });
    expect(noKey).toMatchObject({
      state: 'refused',
      problem: 'mission_panel_withdrawal_key_unavailable',
    });
    expect(answer(await ask({ op: 'request', requestRef: v.request_ref })).lifecycle.stage).toBe(
      'of_record',
    );
  });

  it('a withdrawn proposal is closed and is called withdrawn, never denied', async () => {
    await harness.control({ op: 'provider', reply: reply('Proposal B.') });
    const t = answer(await ask({ op: 'turn', text: 'a third change' })).turn as Json;
    const d = await settled(t.conversation_id);
    const v = answer(
      await ask({ op: 'submit', turnId: d.turn_id, digest: d.draft.proposal_digest_sha256 }),
    ).request as Json;
    const out = answer(
      await ask({
        op: 'withdraw',
        requestRef: v.request_ref,
        missionId: v.mission_id,
        revision: v.revision,
        digest: v.proposal_digest_sha256,
      }),
    );
    expect(out.request.lifecycle.stage).toBe('withdrawn');
    expect(JSON.stringify(out.request.lifecycle)).not.toMatch(/den(y|ied|ial)/i);
    const stalePost = await ask({
      op: 'decide',
      requestRef: v.request_ref,
      missionId: v.mission_id,
      revision: v.revision,
      digest: v.proposal_digest_sha256,
      reply: 'approve',
      offerDigest: '8'.repeat(64),
      expiresAt: 4_000_000_000,
      scope: ['engineering_change', 'repository_read'],
      deliveryTargets: [],
      repositoryUrl: 'https://github.com/Example/Repo',
    });
    // No offer was ever issued for this digest: refused as a stale display, before any authority.
    expect(stalePost).toMatchObject({ state: 'refused', problem: 'mission_panel_display_stale' });
    expect(answer(stalePost).details).toMatchObject({ binding: 'offer_unknown' });
  });

  it('an unreadable provider turn is shown as uncertain and is never retried', async () => {
    const before = (await harness.control({ op: 'state' })).codex_calls as number;
    await harness.control({ op: 'provider', fail: 'garbage' });
    const t = answer(await ask({ op: 'turn', text: 'this will be unreadable' })).turn as Json;
    const settledTurn = await settled(t.conversation_id);
    expect(settledTurn.state).toBe('UNCERTAIN');
    expect(settledTurn.assistant_text).toBeNull();
    expect((await harness.control({ op: 'state' })).codex_calls).toBe(before + 1);
  });

  it('the worst conversation the panel accepts crosses the relay inside the bound, and says what it left out', async () => {
    // Round 27 F2. Fifty turns, each with a user text and an assistant text at the accepted maximum
    // (8000 characters), seeded through the REAL journal's own validation. Astral characters are four bytes
    // raw and twelve ASCII-escaped; control characters are six once JSON-escaped. A character count cannot
    // tell these apart: only the serialized BYTES that cross the relay can.
    const endpoint = JSON.parse(fs.readFileSync(harness.discovery, 'utf-8')) as { port: number };
    const rawBytes = (conversationId: string): Promise<{ declared: number; actual: number }> =>
      new Promise((resolve, reject) => {
        http
          .get(
            {
              host: '127.0.0.1',
              port: endpoint.port,
              path: `/conversations/${conversationId}`,
              headers: {
                Authorization: `Bearer ${harness.token()}`,
                Host: `127.0.0.1:${endpoint.port}`,
              },
            },
            (res) => {
              const chunks: Buffer[] = [];
              res.on('data', (c: Buffer) => chunks.push(c));
              res.on('end', () =>
                resolve({
                  declared: Number(res.headers['content-length']),
                  actual: Buffer.concat(chunks).length,
                }),
              );
            },
          )
          .on('error', reject);
      });
    for (const character of ['astral', 'control'] as const) {
      const seeded = await harness.control({ op: 'seed_turns', turns: 50, character });
      const conversationId = seeded.conversation_id as string;
      // 1. The raw bytes the adapter really sends.
      const raw = await rawBytes(conversationId);
      expect(raw.declared, character).toBe(raw.actual);
      expect(raw.actual, character).toBeLessThanOrEqual(MAX_PANEL_RESPONSE_BYTES);
      expect(raw.actual, `${character}: a worst case, not a toy`).toBeGreaterThan(1024 * 1024);
      // 2. Through the real relay: readable, typed, and honest about the cut.
      const reply = await ask({ op: 'conversation', conversationId });
      expect(reply.state, character).toBe('ok');
      const view = answer(reply);
      expect(view.turns_total).toBe(50);
      expect(view.turns_shown + view.turns_omitted).toBe(50);
      expect(view.turns_omitted, `${character}: 50 maximal turns cannot all fit`).toBeGreaterThan(0);
      expect(view.turns_truncated).toBe(true);
      // WHICH bound cut, with real counts: here every omitted turn was left out by the byte budget.
      expect(view.turns_omitted_by_byte_limit).toBe(view.turns_omitted);
      expect(view.turns_omitted_by_turn_limit).toBe(0);
      console.info(
        `MEASURED ${character} x50 through the relay: raw_response_bytes=${raw.actual} ` +
          `content_length=${raw.declared} bound=${MAX_PANEL_RESPONSE_BYTES} shown=${view.turns_shown} ` +
          `omitted=${view.turns_omitted} (by bytes=${view.turns_omitted_by_byte_limit}, by turn bound=${view.turns_omitted_by_turn_limit})`,
      );
      expect(view.turns).toHaveLength(view.turns_shown);
      // The newest turn is always shown, whole: nothing inside a turn is cut.
      const newest = view.turns.at(-1);
      expect(newest.sequence).toBe(50);
      expect([...newest.user_text]).toHaveLength(8000);
      expect([...newest.assistant_text]).toHaveLength(8000);
    }
  });

  it('never exposes the bearer token to a client, in any reply or any log line', async () => {
    const token = harness.token();
    const everything = JSON.stringify(replies) + logs.join('\n');
    expect(everything).not.toContain(token);
    expect(everything).not.toContain('Bearer');
    expect(everything).not.toMatch(/lc-[0-9a-f]{64}/); // a withdrawal key
    expect(everything).not.toContain(harness.tmp); // no private path
  });

  it('after the panel stops it is "not running", with nothing sent', async () => {
    await harness.stop();
    expect(fs.existsSync(harness.discovery)).toBe(false);
    const gone = await ask({ op: 'refresh' });
    expect(gone).toMatchObject({ state: 'unavailable', problem: 'panel_discovery_unavailable' });
  });
});
