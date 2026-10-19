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
/** item 2: the REAL backend answers (tests/test_artifact_content.py), already normalized (D1). */
const EVIDENCE_SOURCE = 'server/__tests__/fixtures/di-backend/v2/artifact-content-v1.FIXTURE.json';
const EVIDENCE_TARGET = 'webview-ui/test/fixtures/di-office-state-evidence-v2.FIXTURE.json';
const HISTORICAL_SOURCE =
  'server/__tests__/fixtures/di-backend/v2/artifact-content-historical-v1.FIXTURE.json';
const HISTORICAL_TARGET =
  'webview-ui/test/fixtures/di-office-state-evidence-historical-v2.FIXTURE.json';
/** item 3: the REAL backend answers of the live code review (tests/test_code_review_owner.py). */
const REVIEW_SOURCE = 'server/__tests__/fixtures/di-backend/v2/code-review-v1.FIXTURE.json';
const REVIEW_TARGET = 'webview-ui/test/fixtures/di-office-state-code-review-v2.FIXTURE.json';

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
        runtime_agents: {
          agents: Array<{ agent: string; agent_ref: string; identity: { state: string } }>;
        };
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

  /**
   * item 2: the evidence library the webview's browser regression renders — ONE real DiOffice
   * over the backend's exported catalog answer, and the diEvidenceContent messages that SAME
   * office relays for each exported backend content answer (re-checked by the bridge, exactly as
   * live). Plus the same body polled after it expired (STALE).
   */
  it('the committed evidence states equal one DiOffice reading every exported answer', async () => {
    const raw = fs.readFileSync(path.join(ROOT, EVIDENCE_SOURCE));
    const fixture = JSON.parse(raw.toString('utf-8')) as {
      catalog_answer: { response: { generated_at: number } };
      content_answers: Record<string, { artifact_id: string; mission_ref: string; cursor: string }>;
    };
    const body = fixture.catalog_answer;
    const t0 = body.response.generated_at * 1000;
    let clockMs = t0;
    let answer: unknown = null;
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
        requestArtifact: async (): Promise<BackendResult> => ({
          kind: 'http',
          status: 200,
          headers: {},
          body: answer,
          bodyProblem: null,
        }),
        nowMs: () => clockMs,
        log: () => undefined,
      },
    });
    await office.poll();
    const live = office.officeStateMessage();
    const replies: Record<string, unknown> = {};
    for (const name of Object.keys(fixture.content_answers).sort()) {
      const a = fixture.content_answers[name];
      answer = a;
      clockMs += 1_000; // one read per interval, as the office allows
      const sent: unknown[] = [];
      await office.readEvidence(
        {
          type: 'diEvidenceRead',
          missionRef: a.mission_ref,
          artifactId: a.artifact_id,
          cursor: a.cursor,
        },
        (m) => sent.push(m),
      );
      expect(sent).toHaveLength(1);
      replies[name] = sent[0];
    }
    clockMs = t0 + 3_600_000;
    await office.poll();
    const stale = office.officeStateMessage();
    office.stop();
    // Round 15 F4: the SAME body with its one recorded verification record linking 64 artifacts
    // (Mission Core's per-record bound) and 70 (beyond it), each polled by a fresh real DiOffice.
    const withLinks = async (count: number) => {
      const edited = JSON.parse(JSON.stringify(body)) as {
        response: {
          missions: Array<{
            mission_state: { report: { evidence: { value: Array<{ artifact_ids: string[] }> } } };
          }>;
        };
      };
      const record = edited.response.missions[0].mission_state.report.evidence.value[0];
      const real = record.artifact_ids;
      record.artifact_ids = [
        ...real,
        ...Array.from(
          { length: count - real.length },
          (_, i) => `mf-${(i + 1).toString(16).padStart(32, '0')}`,
        ),
      ];
      const linked = new DiOffice({
        store: new AgentStateStore(),
        discoveryPath: '/sandbox/backend-discovery.json',
        contractVersion: 2,
        deps: {
          readDiscovery: () => ({ ok: true, endpoint: { port: 1, token: 't' } }),
          request: async (): Promise<BackendResult> => ({
            kind: 'http',
            status: 200,
            headers: { 'x-di-freshness-health': 'fresh' },
            body: edited,
            bodyProblem: null,
          }),
          nowMs: () => t0,
          log: () => undefined,
        },
      });
      await linked.poll();
      const message = linked.officeStateMessage();
      linked.stop();
      return message;
    };
    const links64 = await withLinks(64);
    const links70 = await withLinks(70);
    expect(live.live).toBe(true);
    expect(stale.live).toBe(false);
    const doc = {
      fixture: true,
      label:
        'FIXTURE — the evidence library of artifact-content-v1 as ONE real DiOffice projects it ' +
        'and relays every exported backend content answer; then the same body polled after it ' +
        'expired; links64/links70: the same body with its ONE verification record linking 64 / 70 ' +
        'artifacts (synthetic extra ids), each polled by a fresh DiOffice — SYNTHETIC ' +
        'VISUAL/KEYBOARD/SECURITY TEST; evidences nothing about any live Mission',
      derived_from: {
        path: EVIDENCE_SOURCE,
        sha256: crypto.createHash('sha256').update(raw).digest('hex'),
      },
      generator: 'server/__tests__/diOfficeStateExport.test.ts',
      states: { live, stale, links64, links70 },
      replies,
    };
    const text = JSON.stringify(doc, null, 2) + '\n';
    const target = path.join(ROOT, EVIDENCE_TARGET);
    if (process.env.DI_EXPORT_OFFICE_STATE === '1') fs.writeFileSync(target, text);
    expect(fs.readFileSync(target, 'utf-8')).toBe(text);
  });

  /**
   * item 2 (completed-Mission artifacts): the evidence library of the HISTORICAL backend export —
   * a COMPLETED Mission observed through the task-native producer — as ONE real DiOffice projects
   * it and relays every exported content answer; then the same body after it expired (STALE), and
   * the same body with the completed Mission's execution identity MOVED (a synthetic second
   * identity and cursor), as a listing that moved after a read.
   */
  it('the committed historical states equal one DiOffice reading the historical export', async () => {
    const raw = fs.readFileSync(path.join(ROOT, HISTORICAL_SOURCE));
    const fixture = JSON.parse(raw.toString('utf-8')) as {
      catalog_answer: { response: { generated_at: number; missions: Array<Record<string, any>> } };
      content_answers: Record<string, { artifact_id: string; mission_ref: string; cursor: string }>;
    };
    const t0 = fixture.catalog_answer.response.generated_at * 1000;
    let clockMs = t0;
    let body: unknown = fixture.catalog_answer;
    let answer: unknown = null;
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
        requestArtifact: async (): Promise<BackendResult> => ({
          kind: 'http',
          status: 200,
          headers: {},
          body: answer,
          bodyProblem: null,
        }),
        nowMs: () => clockMs,
        log: () => undefined,
      },
    });
    await office.poll();
    const live = office.officeStateMessage();
    const replies: Record<string, unknown> = {};
    for (const name of Object.keys(fixture.content_answers).sort()) {
      const a = fixture.content_answers[name];
      answer = a;
      clockMs += 1_000;
      const sent: unknown[] = [];
      await office.readEvidence(
        {
          type: 'diEvidenceRead',
          missionRef: a.mission_ref,
          artifactId: a.artifact_id,
          cursor: a.cursor,
        },
        (m) => sent.push(m),
      );
      expect(sent).toHaveLength(1);
      replies[name] = sent[0];
    }
    const moved = JSON.parse(
      JSON.stringify(fixture.catalog_answer),
    ) as typeof fixture.catalog_answer;
    for (const m of moved.response.missions) {
      const identity = m.mission_state?.execution_identity;
      if (identity && identity.state === 'established') {
        identity.identity_sha256 = 'a'.repeat(64);
        identity.cursor_sha256 = 'b'.repeat(64);
      }
    }
    body = moved;
    clockMs += 1_000;
    await office.poll();
    const movedState = office.officeStateMessage();
    body = fixture.catalog_answer;
    clockMs = t0 + 3_600_000;
    await office.poll();
    const stale = office.officeStateMessage();
    office.stop();
    expect(live.live).toBe(true);
    expect(stale.live).toBe(false);
    const doc = {
      fixture: true,
      label:
        'FIXTURE — the evidence library of artifact-content-historical-v1 (a COMPLETED Mission, ' +
        'observed through the task-native producer) as ONE real DiOffice projects it and relays ' +
        'every exported backend content answer; moved: the same body with a SYNTHETIC second ' +
        'execution identity and cursor; then the same body polled after it expired — SYNTHETIC ' +
        'VISUAL/KEYBOARD/SECURITY TEST; seeded lifecycle facts, isolated principals and supplied ' +
        'inputs are TEST DATA: no real dispatch, execution success, verification or human ' +
        'authorization; evidences nothing about any live Mission',
      derived_from: {
        path: HISTORICAL_SOURCE,
        sha256: crypto.createHash('sha256').update(raw).digest('hex'),
      },
      generator: 'server/__tests__/diOfficeStateExport.test.ts',
      states: { live, moved: movedState, stale },
      replies,
    };
    const text = JSON.stringify(doc, null, 2) + '\n';
    const target = path.join(ROOT, HISTORICAL_TARGET);
    if (process.env.DI_EXPORT_OFFICE_STATE === '1') fs.writeFileSync(target, text);
    expect(fs.readFileSync(target, 'utf-8')).toBe(text);
  });

  /**
   * item 3: the live code review the webview's browser regression drives — ONE real DiOffice over
   * the backend's exported catalog answer, and the diCodeReview messages that SAME office relays
   * for the overview and for every openable file (each re-checked by the bridge, exactly as live).
   * Plus the office states a review must be cleared by: the same body after it expired (STALE), with
   * the assignment no longer active (INACTIVE), with the agent's task replaced (MOVED), with the
   * agent gone (AGENT GONE), and a poll the backend cannot answer (UNAVAILABLE).
   */
  it('the committed code review states equal one DiOffice reading every exported answer', async () => {
    const raw = fs.readFileSync(path.join(ROOT, REVIEW_SOURCE));
    const fixture = JSON.parse(raw.toString('utf-8')) as {
      catalog_answer: { response: Record<string, any> & { generated_at: number } };
      overview: Record<string, any>;
      files: Record<string, Record<string, any>>;
    };
    const body = fixture.catalog_answer;
    const t0 = body.response.generated_at * 1000;
    let clockMs = t0;
    let current: unknown = body;
    let reviewAnswer: unknown = null;
    const office = new DiOffice({
      store: new AgentStateStore(),
      discoveryPath: '/sandbox/backend-discovery.json',
      contractVersion: 2,
      deps: {
        readDiscovery: () => ({ ok: true, endpoint: { port: 1, token: 't' } }),
        request: async (): Promise<BackendResult> =>
          (current as BackendResult | { kind?: undefined }).kind === 'transport_error'
            ? (current as BackendResult)
            : {
                kind: 'http',
                status: 200,
                headers: { 'x-di-freshness-health': 'fresh' },
                body: current,
                bodyProblem: null,
              },
        requestReview: async (): Promise<BackendResult> => ({
          kind: 'http',
          status: 200,
          headers: {},
          body: reviewAnswer,
          bodyProblem: null,
        }),
        nowMs: () => clockMs,
        log: () => undefined,
      },
    });
    await office.poll();
    const live = office.officeStateMessage();
    const mission = live.missions.missions[0];
    const member = mission.members.members[0];
    expect(member.codeReview?.state).toBe('offered');
    const ref = {
      missionRef: mission.missionRef,
      memberRef: member.memberRef,
      taskId: member.codeReview!.taskId!,
      listing: member.codeReview!.listing!,
    };
    const read = async (extra: Record<string, unknown>, backendAnswer: unknown) => {
      reviewAnswer = backendAnswer;
      clockMs += 1_000; // one read per interval, as the office allows
      const sent: unknown[] = [];
      await office.readCodeReview({ type: 'diCodeReviewRead', ...ref, ...extra }, (m) =>
        sent.push(m),
      );
      expect(sent).toHaveLength(1);
      return sent[0];
    };
    const overview = await read({}, fixture.overview);
    const files: Record<string, unknown> = {};
    for (const entry of fixture.overview.overview.files as Array<{ path: string; state: string }>) {
      if (!['text', 'submodule', 'no_content_diff'].includes(entry.state)) continue; // the openable states
      const backend = fixture.files[entry.path];
      files[entry.path] = await read(
        { snapshot: backend.snapshot, fileId: backend.file_id },
        backend,
      );
    }
    // The other states are polled by the SAME office from edited copies of the same body.
    const edited = (edit: (b: any) => void) => {
      const copy = JSON.parse(JSON.stringify(body));
      edit(copy);
      return copy;
    };
    const poll = async (next: unknown, at: number) => {
      current = next;
      clockMs = at;
      await office.poll();
      return office.officeStateMessage();
    };
    const stale = await poll(body, t0 + 3_600_000);
    const inactive = await poll(
      edited((b) => (b.response.missions[0].lifecycle.standing = 'inactive')),
      t0,
    );
    const moved = await poll(
      edited((b) => {
        const m = b.response.missions[0];
        m.execution.task_id = '20261014-000000-bbbbbb';
        m.binding.task_id = '20261014-000000-bbbbbb';
        m.members[0].task_id = '20261014-000000-bbbbbb';
      }),
      t0,
    );
    const agentGone = await poll(
      edited((b) => (b.response.missions[0].members = [])),
      t0,
    );
    const unavailable = await poll({ kind: 'transport_error', problem: 'backend_unreachable' }, t0);
    office.stop();
    expect(live.live).toBe(true);
    expect(stale.live).toBe(false);
    expect(inactive.missions.missions[0].members.members[0].codeReview?.reason).toBe(
      'assignment_not_active',
    );
    expect(moved.missions.missions[0].members.members[0].codeReview?.listing).not.toBe(
      ref.listing,
    );
    expect(agentGone.missions.missions[0].members.members).toHaveLength(0);
    expect(unavailable.live).toBe(false);
    const doc = {
      fixture: true,
      label:
        'FIXTURE — the live code review of code-review-v1 as ONE real DiOffice projects it and ' +
        'relays the exported backend answers for the overview and every openable file; then the ' +
        'same body after it expired (stale), with the assignment inactive, with the task ' +
        'replaced (moved), with the agent gone, and a poll the backend cannot answer — SYNTHETIC ' +
        'VISUAL/KEYBOARD/SECURITY TEST over a fixture git workspace; evidences nothing about any ' +
        'live Mission or team',
      derived_from: {
        path: REVIEW_SOURCE,
        sha256: crypto.createHash('sha256').update(raw).digest('hex'),
      },
      generator: 'server/__tests__/diOfficeStateExport.test.ts',
      reference: ref,
      states: { live, stale, inactive, moved, agentGone, unavailable },
      replies: { overview, files },
    };
    const text = JSON.stringify(doc, null, 2) + '\n';
    const target = path.join(ROOT, REVIEW_TARGET);
    if (process.env.DI_EXPORT_OFFICE_STATE === '1') fs.writeFileSync(target, text);
    expect(fs.readFileSync(target, 'utf-8')).toBe(text);
  });
});
