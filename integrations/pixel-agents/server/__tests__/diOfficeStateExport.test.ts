/**
 * C1 (items 08/09): the office state the WEBVIEW keyboard/navigation regression renders is the
 * REAL server projection of a labelled packaged fixture, never a hand-built object. Without
 * `DI_EXPORT_OFFICE_STATE=1` this test fails when the committed webview fixture drifts from the
 * projection; with it, the fixture is (re)written.
 */
import * as crypto from 'crypto';
import * as fs from 'fs';
import * as path from 'path';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { AgentStateStore } from '../src/agentStateStore.js';
import type { BackendResult } from '../src/di/backendClient.js';
import { DiOffice } from '../src/di/diOffice.js';

const ROOT = path.join(__dirname, '..', '..');
const SOURCE = 'packaging/di/fixtures/herdr-task-session-mismatch-v2.FIXTURE.json';
const TARGET = 'webview-ui/test/fixtures/di-office-state-session-mismatch-v2.FIXTURE.json';
const TRANSITIONS = 'webview-ui/test/fixtures/di-office-state-transitions-v2.FIXTURE.json';

describe('C1: the webview regression renders the real projection of a labelled fixture', () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  it('the committed office state equals the projection of the packaged fixture', async () => {
    const raw = fs.readFileSync(path.join(ROOT, SOURCE));
    const fixture = JSON.parse(raw.toString('utf-8')) as {
      label: string;
      frames: Array<{ envelope: { response: { generated_at: number } } }>;
    };
    const body = fixture.frames[0].envelope;
    const clockMs = body.response.generated_at * 1000;
    const office = new DiOffice({
      store: new AgentStateStore(),
      discoveryPath: '/sandbox/backend-discovery.json',
      contractVersion: 2,
      deps: {
        readDiscovery: () => ({ ok: true, endpoint: { port: 1, token: 't' } }),
        request: async (): Promise<BackendResult> => ({
          kind: 'http',
          status: 200,
          headers: { 'x-di-freshness-health': 'fresh' },
          body,
          bodyProblem: null,
        }),
        nowMs: () => clockMs,
        log: () => undefined,
      },
    });
    await office.poll();
    const state = office.officeStateMessage();
    office.stop();
    expect(state.live).toBe(true);
    const doc = {
      fixture: true,
      label:
        'FIXTURE — the office state of herdr-task-session-mismatch-v2, projected by the real ' +
        'server (DiOffice) — SYNTHETIC VISUAL/KEYBOARD TEST; evidences nothing about any live team',
      derived_from: {
        path: SOURCE,
        sha256: crypto.createHash('sha256').update(raw).digest('hex'),
        label: fixture.label,
      },
      generator: 'server/__tests__/diOfficeStateExport.test.ts',
      state,
    };
    const text = JSON.stringify(doc, null, 2) + '\n';
    const target = path.join(ROOT, TARGET);
    if (process.env.DI_EXPORT_OFFICE_STATE === '1') {
      fs.mkdirSync(path.dirname(target), { recursive: true });
      fs.writeFileSync(target, text);
    }
    expect(fs.readFileSync(target, 'utf-8')).toBe(text);
  });

  /**
   * Round 11: the TRANSITIONS the webview's reconciliation regression replays, each produced by
   * ONE real DiOffice (so every character number is the one the office really assigns): live,
   * then one verified runtime agent and one drawn member REMOVED, then the same body polled
   * after it expired (STALE), then a poll the backend cannot answer (UNAVAILABLE).
   */
  it('the committed transition states equal one DiOffice stepping through them', async () => {
    const raw = fs.readFileSync(path.join(ROOT, SOURCE));
    const fixture = JSON.parse(raw.toString('utf-8')) as {
      label: string;
      frames: Array<{ envelope: { response: Record<string, unknown> & { generated_at: number } } }>;
    };
    const live = fixture.frames[0].envelope;
    const t0 = live.response.generated_at * 1000;
    const removed = JSON.parse(JSON.stringify(live)) as {
      response: {
        runtime_agents: { agents: Array<{ agent: string; agent_ref: string; identity: { state: string } }> };
        missions: Array<{ members: Array<{ member_ref: string }> }>;
      };
    };
    const runtimeGone = removed.response.runtime_agents.agents.find(
      (a) => a.identity.state === 'verified',
    )!;
    removed.response.runtime_agents.agents = removed.response.runtime_agents.agents.filter(
      (a) => a !== runtimeGone,
    );
    const withMembers = removed.response.missions.find((m) => m.members.length > 0)!;
    const memberGone = withMembers.members[0];
    withMembers.members = withMembers.members.slice(1);

    let clockMs = t0;
    let next: BackendResult = {
      kind: 'http',
      status: 200,
      headers: { 'x-di-freshness-health': 'fresh' },
      body: live,
      bodyProblem: null,
    };
    const office = new DiOffice({
      store: new AgentStateStore(),
      discoveryPath: '/sandbox/backend-discovery.json',
      contractVersion: 2,
      deps: {
        readDiscovery: () => ({ ok: true, endpoint: { port: 1, token: 't' } }),
        request: async (): Promise<BackendResult> => next,
        nowMs: () => clockMs,
        log: () => undefined,
      },
    });
    const states: Record<string, unknown> = {};
    await office.poll();
    states.live = office.officeStateMessage();
    next = { ...next, body: removed } as BackendResult;
    await office.poll();
    states.removed = office.officeStateMessage();
    next = { ...next, body: live } as BackendResult;
    await office.poll();
    states.liveAgain = office.officeStateMessage();
    clockMs = t0 + 3_600_000; // an hour later: the same body is past its valid_until
    await office.poll();
    states.stale = office.officeStateMessage();
    clockMs = t0;
    next = { kind: 'transport_error', problem: 'backend_unreachable' };
    await office.poll();
    states.unavailable = office.officeStateMessage();
    office.stop();
    expect((states.live as { live: boolean }).live).toBe(true);
    expect((states.stale as { live: boolean }).live).toBe(false);
    expect((states.unavailable as { live: boolean }).live).toBe(false);
    const doc = {
      fixture: true,
      label:
        'FIXTURE — office states of herdr-task-session-mismatch-v2 as ONE real DiOffice steps ' +
        'live → removed → live again → stale → unavailable — SYNTHETIC RECONCILIATION TEST; ' +
        'evidences nothing about any live team',
      derived_from: {
        path: SOURCE,
        sha256: crypto.createHash('sha256').update(raw).digest('hex'),
        label: fixture.label,
        edits:
          `removed: runtime agent ${runtimeGone.agent_ref} and member ${memberGone.member_ref} ` +
          'are deleted from the emission; stale: the same live body polled one hour after it ' +
          'was generated; unavailable: the backend request fails (backend_unreachable)',
      },
      generator: 'server/__tests__/diOfficeStateExport.test.ts',
      removedRuntimeAgentRef: runtimeGone.agent_ref,
      removedMemberRef: memberGone.member_ref,
      states,
    };
    const text = JSON.stringify(doc, null, 2) + '\n';
    const target = path.join(ROOT, TRANSITIONS);
    if (process.env.DI_EXPORT_OFFICE_STATE === '1') fs.writeFileSync(target, text);
    expect(fs.readFileSync(target, 'utf-8')).toBe(text);
  });
});
