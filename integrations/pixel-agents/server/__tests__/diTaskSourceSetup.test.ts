/**
 * The DI-native task source's supported setup step (`dicatalog.py task-source --ensure`), read
 * through the REAL office adapter from REAL producer emissions (tests/test_task_source_setup.py
 * ExportForConsumerTests, contract 2), hash-pinned: an existing office catalog BEFORE the step,
 * and the SAME long-lived catalog owner's next ordinary refresh AFTER it. SYNTHETIC fixture
 * evidence only — never a live observation.
 */
import * as crypto from 'crypto';
import * as fs from 'fs';
import * as path from 'path';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { AgentStateStore } from '../src/agentStateStore.js';
import type { BackendResult } from '../src/di/backendClient.js';
import { parseEnvelope, projectResponse } from '../src/di/catalogProjection.js';
import { DiOffice } from '../src/di/diOffice.js';
import { buildOfficeView } from '../src/di/officeView.js';
import { readServeTimeVerdict } from '../src/di/serveTimeVerdict.js';

const FIXTURES = {
  before: {
    file: 'task-source-setup-before-v2.FIXTURE.json',
    sha256: 'a8c53c732a36d637b088e6ac8b622c2136d822762e5cc5a67dd156c6c0ca152c',
  },
  after: {
    file: 'task-source-setup-after-v2.FIXTURE.json',
    sha256: '5edede62fc4af8104deb210dfce7c67d1df79895e8866d2f72303f65ccd89ea4',
  },
} as const;

// Stated here, independently of the producer.
const TASK = '20261021-150000-abcdef';
const ROOT = '/__synthetic__/di-fixture-root/repo-e2e';
const ROOT_KEY = crypto.createHash('sha256').update(ROOT).digest('hex').slice(0, 12);
const MISSION_REF = `di_herdr_tasks:${TASK}@${ROOT_KEY}`;
const ACTIVITY: Record<string, string> = {
  lead1: 'busy',
  executor1: 'idle',
  reviewer1: 'idle',
  supervisor: 'busy',
};

function emitted(which: keyof typeof FIXTURES): Record<string, unknown> {
  const p = path.join(__dirname, 'fixtures/di-backend/herdr-task', FIXTURES[which].file);
  const bytes = fs.readFileSync(p);
  const digest = crypto.createHash('sha256').update(bytes).digest('hex');
  if (digest !== FIXTURES[which].sha256) throw new Error(`FIXTURE hash mismatch: ${digest}`);
  return JSON.parse(bytes.toString('utf-8')) as Record<string, unknown>;
}

function response(body: Record<string, unknown>): Record<string, unknown> {
  return body.response as Record<string, unknown>;
}

const FRESH = readServeTimeVerdict({ 'x-di-freshness-health': 'fresh' });

function view(body: Record<string, unknown>) {
  const parsed = parseEnvelope(body);
  if (!parsed.ok) throw new Error(`envelope refused: ${JSON.stringify(parsed.verdict)}`);
  return buildOfficeView(parsed.envelope, FRESH, Number(response(body).generated_at), 2);
}

describe('task-source setup: what the office shows before the step', () => {
  it('names the missing source in the plain statement, and lists no herdr team', () => {
    const body = emitted('before');
    const projected = projectResponse(response(body), 2);
    expect(projected.ok).toBe(true);
    if (!projected.ok) return;
    expect(projected.view.runtimeAgents.state).toEqual({ known: true, value: 'not_applicable' });
    expect(projected.view.runtimeAgents.problem).toBe('task_native_source_not_registered');
    expect(projected.view.sources.map((s) => s.kind).sort()).toEqual([
      'mission_core',
      'workflow_store',
    ]);
    const office = view(body);
    expect(office.live).toBe(true);
    // The README quotes this exact line next to the command that fixes it.
    expect(office.runtimeAgents.plainStatement).toBe(
      'Runtime agents: not_applicable (task_native_source_not_registered).',
    );
    if (office.missions.state !== 'observed') throw new Error('missions not observed');
    expect(office.missions.value.filter((m) => m.sourceKind === 'herdr_task')).toEqual([]);
  });
});

describe('task-source setup: the next ordinary refresh after the step', () => {
  it('projects the synthetic team with its exact source, task, root and session identity', () => {
    const projected = projectResponse(response(emitted('after')), 2);
    expect(projected.ok).toBe(true);
    if (!projected.ok) return;
    const herdr = projected.view.missions.filter((m) => m.sourceId === 'di_herdr_tasks');
    expect(herdr.map((m) => m.missionRef)).toEqual([MISSION_REF]);
    const [mission] = herdr;
    expect(mission.lifecycle.model).toEqual({ known: true, value: 'herdr.task/1' });
    expect(mission.execution.taskId).toBe(TASK);
    expect(mission.execution.workspaceRealpath).toBe(ROOT);
    expect(mission.producerDeclared).toBe('herdctl');
    expect(mission.binding.state).toEqual({ known: true, value: 'bound' });
    expect(mission.members.map((m) => m.logical).sort()).toEqual(Object.keys(ACTIVITY).sort());
    for (const m of mission.members) {
      expect(m.memberRef).toBe(`${MISSION_REF}#${TASK}#${m.logical}`);
      expect(m.agent).toBe(`e2e-${m.logical}`);
      expect(m.session).toEqual({
        recorded: `sess-e2e-${m.logical}`,
        live: `sess-e2e-${m.logical}`,
      });
      expect(m.classification).toEqual({ known: true, value: 'present' });
      expect(m.activity).toEqual({ known: true, value: ACTIVITY[m.logical] });
    }
    const source = projected.view.sources.find((s) => s.sourceId === 'di_herdr_tasks');
    expect(source?.kind).toBe('task_native');
    expect(source?.health).toEqual({ known: true, value: 'fresh' });
  });
});

describe('task-source setup: the office draws the team once, idle roles stay idle', () => {
  let clockMs = 0;
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  function office(body: Record<string, unknown>) {
    clockMs = Number(response(body).generated_at) * 1000;
    const store = new AgentStateStore();
    const o = new DiOffice({
      store,
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
    return o;
  }

  it('before the step: no character for the unregistered team', async () => {
    const o = office(emitted('before'));
    await o.poll();
    expect([...o.liveCharacters().values()]).toHaveLength(0);
    o.stop();
  });

  it('after the step: four characters, and only the busy roles animate', async () => {
    const o = office(emitted('after'));
    await o.poll();
    expect([...o.liveCharacters().values()]).toHaveLength(4);
    const message = o.officeStateMessage();
    const [mission] = message.missions.missions.filter((m) => m.missionRef === MISSION_REF);
    expect(mission.sourceKind).toBe('herdr_task');
    const members = mission.members.members;
    expect(members).toHaveLength(4);
    for (const m of members) {
      expect(m.live).toBe(true);
      expect(m.characterId).not.toBeNull();
      expect(m.sessionRecorded).toEqual({
        state: 'observed',
        value: `sess-e2e-${m.logical}`,
        reason: null,
      });
      expect(m.activity).toBe(ACTIVITY[m.logical]);
      // Truthful activity: an idle role is drawn, and never animates as working.
      expect(m.animateActive).toBe(ACTIVITY[m.logical] === 'busy');
    }
    expect(new Set(members.map((m) => m.characterId)).size).toBe(4);
    expect(message.runtimeAgents?.agents).toEqual([]);
    o.stop();
  });
});
