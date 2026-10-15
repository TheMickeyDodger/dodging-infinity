/**
 * The DI office consumer loop, with injected discovery / request / clock. The backend
 * answer is the CONTRACT-v3 canonical FIXTURE (synthetic) — fixture evidence only.
 */
import * as crypto from 'crypto';
import * as fs from 'fs';
import * as path from 'path';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { AgentStateStore } from '../src/agentStateStore.js';
import { DI_CATALOG_WAIT_SECONDS, DI_CONSUMER_POLL_INTERVAL_SECONDS } from '../src/constants.js';
import type { BackendResult } from '../src/di/backendClient.js';
import type { DiscoveryResult } from '../src/di/backendDiscovery.js';
import { DiOffice } from '../src/di/diOffice.js';
import { clone, loadFixture } from './helpers/diFixture.js';

const ENDPOINT_OK: DiscoveryResult = {
  ok: true,
  endpoint: { port: 1, token: 'secret-not-logged' },
};
const GENERATED_AT = 1800000000;

function answer(
  edit?: (fixture: Record<string, unknown>) => void,
  headers: Record<string, string> = {},
): BackendResult {
  const body = clone(loadFixture());
  edit?.(body);
  return {
    kind: 'http',
    status: 200,
    headers: { 'x-di-freshness-health': 'fresh', ...headers },
    body,
    bodyProblem: null,
  };
}

let clockMs = GENERATED_AT * 1000;
let logs: string[];

function office(
  discovery: () => DiscoveryResult,
  request: () => Promise<BackendResult>,
  store = new AgentStateStore(),
): { office: DiOffice; store: AgentStateStore; request: ReturnType<typeof vi.fn> } {
  const requestSpy = vi.fn(request);
  const o = new DiOffice({
    store,
    discoveryPath: '/sandbox/backend-discovery.json',
    deps: {
      readDiscovery: discovery,
      request: requestSpy,
      nowMs: () => clockMs,
      log: (l) => logs.push(l),
    },
  });
  return { office: o, store, request: requestSpy };
}

beforeEach(() => {
  clockMs = GENERATED_AT * 1000;
  logs = [];
  vi.useFakeTimers();
});

afterEach(() => {
  vi.useRealTimers();
});

describe('DiOffice', () => {
  it('absent discovery file: unavailable, NO request, nothing launched, no characters', async () => {
    const {
      office: o,
      store,
      request,
    } = office(
      () => ({ ok: false, problem: 'discovery_absent' }),
      async () => answer(),
    );
    await o.poll();
    expect(request).not.toHaveBeenCalled();
    expect(o.getView().live).toBe(false);
    expect(o.getView().missions).toEqual({
      state: 'unavailable',
      reason: 'discovery:discovery_absent',
    });
    expect([...store.keys()]).toEqual([]);
    o.stop();
  });

  it('a live FIXTURE answer projects exactly the live member as a character', async () => {
    const { office: o, store } = office(
      () => ENDPOINT_OK,
      async () => answer(),
    );
    await o.poll();
    expect(o.getView().live).toBe(true);
    const chars = [...o.liveCharacters().values()];
    expect(chars).toEqual(['synthetic-core:mn-1714199978b50a9d93349749406a85a7#executor1']);
    expect([...store.keys()]).toHaveLength(1);
    expect(store.get([...store.keys()][0])?.folderName).toBe(
      'synthetic-core:mn-1714199978b50a9d93349749406a85a7',
    );
    o.stop();
  });

  it('a replaced/session_changed member gets NO character and is not counted (Supervisor ruling)', async () => {
    const { office: o, store } = office(
      () => ENDPOINT_OK,
      async () =>
        answer((f) => {
          const core = (f.response as Record<string, unknown>).missions as Array<
            Record<string, unknown>
          >;
          const member = (core[0].members as Array<Record<string, unknown>>)[0];
          member.classification = 'replaced';
          member.reason = 'session_changed';
          (member.session as Record<string, unknown>).live = 'sess-exec-2';
        }),
    );
    await o.poll();
    expect(o.getView().live).toBe(true);
    expect([...o.liveCharacters().values()]).toEqual([]);
    expect([...store.keys()]).toEqual([]);
    expect(logs.at(-1)).toContain('characters=0');
    o.stop();
  });

  it('the local floor removes characters at valid_until + 1, not before', async () => {
    const { office: o, store } = office(
      () => ENDPOINT_OK,
      async () => answer(),
    );
    o.start();
    await vi.advanceTimersByTimeAsync(0);
    expect([...store.keys()]).toHaveLength(1);
    clockMs = (GENERATED_AT + 60) * 1000; // exactly valid_until: still fresh
    await vi.advanceTimersByTimeAsync(1000);
    expect([...store.keys()]).toHaveLength(1);
    o.stop();
    clockMs = (GENERATED_AT + 61) * 1000;
    // re-evaluate directly (timers stopped): the floor takes liveness away
    (o as unknown as { reevaluate(): void }).reevaluate();
    expect([...store.keys()]).toHaveLength(0);
    expect(o.getView().notLiveReason).toBe('expired_locally:response_expired');
  });

  it('a returning member gets its OWN number back (stable identity)', async () => {
    let live = true;
    const { office: o } = office(
      () => ENDPOINT_OK,
      async () => (live ? answer() : answer(undefined, { 'x-di-freshness-health': 'stale' })),
    );
    await o.poll();
    const first = [...o.liveCharacters().keys()][0];
    live = false;
    await o.poll();
    expect(o.liveCharacters().size).toBe(0);
    live = true;
    await o.poll();
    expect([...o.liveCharacters().keys()][0]).toBe(first);
    o.stop();
  });

  it('a backend-declared minimum LARGER than our policy floor is honored (one request in flight)', async () => {
    const declaredMinimum = DI_CONSUMER_POLL_INTERVAL_SECONDS + 3;
    const { office: o, request } = office(
      () => ENDPOINT_OK,
      async () =>
        answer(
          (f) =>
            ((
              (f.response as Record<string, unknown>).limits as Record<string, number>
            ).min_poll_interval_seconds = declaredMinimum),
        ),
    );
    o.start();
    await vi.advanceTimersByTimeAsync(0);
    expect(request).toHaveBeenCalledTimes(1);
    await vi.advanceTimersByTimeAsync(declaredMinimum * 1000 - 1);
    expect(request).toHaveBeenCalledTimes(1);
    await vi.advanceTimersByTimeAsync(1);
    expect(request).toHaveBeenCalledTimes(2);
    o.stop();
  });

  it('POLICY PIN: consumer wait_seconds = 1 and poll floor = 6 s (Supervisor + Lead engineering selection; a change must be deliberate)', () => {
    expect(DI_CATALOG_WAIT_SECONDS).toBe(1);
    expect(DI_CONSUMER_POLL_INTERVAL_SECONDS).toBe(6);
  });

  it('our 6 s floor applies when the backend declares a SMALLER minimum (the v3 fixture declares 5)', async () => {
    const declaredMinimum = (
      (loadFixture().response as Record<string, unknown>).limits as Record<string, number>
    ).min_poll_interval_seconds;
    expect(declaredMinimum).toBeLessThan(DI_CONSUMER_POLL_INTERVAL_SECONDS);
    const { office: o, request } = office(
      () => ENDPOINT_OK,
      async () => answer(),
    );
    o.start();
    await vi.advanceTimersByTimeAsync(0);
    expect(request).toHaveBeenCalledTimes(1);
    await vi.advanceTimersByTimeAsync(declaredMinimum * 1000); // the backend's minimum alone would fire here
    expect(request).toHaveBeenCalledTimes(1);
    await vi.advanceTimersByTimeAsync(
      (DI_CONSUMER_POLL_INTERVAL_SECONDS - declaredMinimum) * 1000 - 1,
    );
    expect(request).toHaveBeenCalledTimes(1);
    await vi.advanceTimersByTimeAsync(1);
    expect(request).toHaveBeenCalledTimes(2);
    o.stop();
  });

  it('scheduling is completion-relative: the next request starts the interval after the RESPONSE, not after the previous start', async () => {
    const REQUEST_DURATION_MS = 3_000;
    const { office: o, request } = office(
      () => ENDPOINT_OK,
      () => new Promise((resolve) => setTimeout(() => resolve(answer()), REQUEST_DURATION_MS)),
    );
    const intervalMs = DI_CONSUMER_POLL_INTERVAL_SECONDS * 1000; // the fixture declares less
    o.start();
    await vi.advanceTimersByTimeAsync(0);
    expect(request).toHaveBeenCalledTimes(1);
    // A start-to-start scheduler would fire here; a completion-relative one must not.
    await vi.advanceTimersByTimeAsync(intervalMs);
    expect(request).toHaveBeenCalledTimes(1);
    await vi.advanceTimersByTimeAsync(REQUEST_DURATION_MS - 1);
    expect(request).toHaveBeenCalledTimes(1);
    await vi.advanceTimersByTimeAsync(1);
    expect(request).toHaveBeenCalledTimes(2);
    o.stop();
  });

  it('cached answers are not restamped: the view carries the envelope and response times verbatim', async () => {
    const { office: o } = office(
      () => ENDPOINT_OK,
      async () =>
        answer((f) => {
          f.served = 'cached';
          f.retry_after_seconds = 3;
        }),
    );
    clockMs = (GENERATED_AT + 4) * 1000;
    await o.poll();
    const view = o.getView();
    expect(view.served).toBe('cached');
    expect(view.times.requestedAt).toBe(GENERATED_AT);
    expect(view.times.generatedAt).toBe(GENERATED_AT);
    expect(view.times.validUntil).toBe(GENERATED_AT + 60);
    expect(view.times.evaluatedAt).toBe(GENERATED_AT + 4);
    o.stop();
  });

  it('HTTP and transport failures are honest unavailable states', async () => {
    const results: BackendResult[] = [
      { kind: 'transport_error', problem: 'backend_unreachable' },
      { kind: 'http', status: 401, headers: {}, body: undefined, bodyProblem: 'body_not_json' },
      { kind: 'http', status: 200, headers: {}, body: undefined, bodyProblem: 'body_not_json' },
    ];
    const reasons = ['transport:backend_unreachable', 'http_401', 'body_not_json'];
    for (let i = 0; i < results.length; i++) {
      const { office: o, store } = office(
        () => ENDPOINT_OK,
        async () => results[i],
      );
      await o.poll();
      expect(o.getView().missions).toEqual({ state: 'unavailable', reason: reasons[i] });
      expect([...store.keys()]).toEqual([]);
      o.stop();
    }
  });

  it('the catalog route carries the pinned wait, and the token is never logged', async () => {
    // The real request (built office -> HTTP) is asserted in diBuiltOfficeFixtureBackend.test.ts.
    const { routePath } = await import('../src/di/backendClient.js');
    const url = new URL(`http://x${routePath('catalog', DI_CATALOG_WAIT_SECONDS)!}`);
    expect(url.searchParams.get('wait_seconds')).toBe(String(DI_CATALOG_WAIT_SECONDS));
    const { office: withLog } = office(
      () => ENDPOINT_OK,
      async () => answer(),
    );
    await withLog.poll();
    expect(logs.join('\n')).not.toContain('secret-not-logged');
    withLog.stop();
  });
});

/** The early v2 FIXTURE (synthetic, hash-pinned) — its linked-child members are LIVE. */
function loadV2Fixture(): Record<string, unknown> {
  const p = path.join(__dirname, 'fixtures/di-backend/v2/catalog-answer-v2.fc409bbc.FIXTURE.json');
  const bytes = fs.readFileSync(p);
  const digest = crypto.createHash('sha256').update(bytes).digest('hex');
  if (digest !== 'fc409bbc41a6ee3a17253acebe85092a4d118511954fadf2c457ecc11b891556') {
    throw new Error(`v2 FIXTURE hash mismatch: ${digest}`);
  }
  return JSON.parse(bytes.toString('utf-8')) as Record<string, unknown>;
}

describe('DiOffice — contract v2 (FIXTURE evidence only)', () => {
  it('LIVE linked-child members become characters: the poll completes, no consumer_error', async () => {
    const body = loadV2Fixture();
    clockMs = Number((body.response as Record<string, unknown>).generated_at) * 1000;
    const store = new AgentStateStore();
    const o = new DiOffice({
      store,
      discoveryPath: '/sandbox/backend-discovery.json',
      contractVersion: 2,
      deps: {
        readDiscovery: () => ENDPOINT_OK,
        request: async () => ({
          kind: 'http',
          status: 200,
          headers: { 'x-di-freshness-health': 'fresh' },
          body,
          bodyProblem: null,
        }),
        nowMs: () => clockMs,
        log: (l) => logs.push(l),
      },
    });
    await o.poll();
    const view = o.getView();
    expect(view.notLiveReason).not.toBe('consumer_error');
    expect(view.live).toBe(true);
    // Every LIVE member is a character — direct AND linked-child; no fixed count.
    const liveMembers =
      view.missions.state === 'observed'
        ? view.missions.value.flatMap((m) =>
            m.presence.live && m.members.state === 'observed'
              ? m.members.value
                  .filter((x) => x.presence.live)
                  .map((x) => ({ ref: x.memberRef, coverage: x.coverage }))
              : [],
          )
        : [];
    expect(liveMembers.some((m) => m.coverage === 'child')).toBe(true);
    expect([...o.liveCharacters().values()].sort()).toEqual(liveMembers.map((m) => m.ref).sort());
    o.stop();
  });
});
