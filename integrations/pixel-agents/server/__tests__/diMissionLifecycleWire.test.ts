/**
 * Deliverable 3, part 1 (integration across the REAL seam), office side. Three real producer exports, each through the REAL server:
 *
 * - the LIFECYCLE: one Mission Core Mission's real catalog answer at every step (tests/test_mission_lifecycle_answers.py ML2: of record, approved and never
 *   started, start requested, task observed, completed (canonical status Ended, NOT Verified), reclaimed by the startup overdue sweep, deleted by its owner), each set as the response of the packaged
 *   contract-v2 envelope and projected by parseEnvelope, buildOfficeView and buildDiOfficeStateMessage;
 * - the ROSTER: the producer's own Herdr roster deciding each task (tests/test_herdr_roster_answers.py HR2: not corroborated by a live session, a live
 *   agent busy, one blocked, FAILED, ABORTED, COMPLETE, and a status literal the office does not know), projected the same way;
 * - the ARCHIVED REFUSALS: the panel's real HTTP answers to a restart of an archived Mission (tests/test_mission_archived_refusal_http.py) through the
 *   office's REAL relay (DiMissionRequests.handle).
 *
 * The projected states and relayed replies are exported for the webview (DI_EXPORT_OFFICE_STATE=1 writes; otherwise they are checked for drift). Nothing in
 * an answer is edited here; each envelope's request time is its answer's own `now`.
 */
import * as crypto from 'crypto';
import * as fs from 'fs';
import * as path from 'path';
import { describe, expect, it } from 'vitest';

import type { DiMissionRequestReply, DiOfficeState } from '../../core/src/messages.js';
import { parseEnvelope } from '../src/di/catalogProjection.js';
import type { PanelResult } from '../src/di/missionPanelClient.js';
import { DiMissionRequests } from '../src/di/missionRequests.js';
import { buildDiOfficeStateMessage } from '../src/di/officeStateMessage.js';
import { buildOfficeView } from '../src/di/officeView.js';
import type { ServeTimeVerdict } from '../src/di/serveTimeVerdict.js';
import { clone } from './helpers/diFixture.js';

type Raw = Record<string, unknown>;
const ROOT = path.join(__dirname, '..', '..');
const FRESH: ServeTimeVerdict = { health: { known: true, value: 'fresh' }, problem: null };
const ENVELOPE_REL = 'server/__tests__/fixtures/di-backend/v2/catalog-answer-v2.fc409bbc.FIXTURE.json';
const LIFECYCLE_REL = 'server/__tests__/fixtures/di-backend/mission-lifecycle-answers.FIXTURE.json';
const ROSTER_REL = 'server/__tests__/fixtures/di-backend/herdr-roster-answers.FIXTURE.json';
const WORKFLOW_REL = 'server/__tests__/fixtures/di-backend/workflow-phase-answers.FIXTURE.json';
const WORKFLOW_TARGET = 'webview-ui/test/fixtures/di-office-state-workflow-phases-v2.FIXTURE.json';
/** How long after the answer's own time the office reads it again for the EXPIRED case: well past any answer's validity (max_age_seconds 60). */
const EXPIRED_AFTER_SECONDS = 600;
const REFUSALS_REL = 'server/__tests__/fixtures/di-backend/mission-panel-archived-refusals.FIXTURE.json';
const LIFECYCLE_TARGET = 'webview-ui/test/fixtures/di-office-state-mission-lifecycle-v2.FIXTURE.json';
const ROSTER_TARGET = 'webview-ui/test/fixtures/di-office-state-herdr-roster-v2.FIXTURE.json';
const REPLIES_TARGET = 'webview-ui/test/fixtures/di-mission-request-archived-refusal-replies.FIXTURE.json';

const bytesOf = (rel: string) => fs.readFileSync(path.join(ROOT, rel));
const sha = (bytes: Buffer) => crypto.createHash('sha256').update(bytes).digest('hex');
const envelopeBytes = bytesOf(ENVELOPE_REL);

/** The real server's office state for one real answer, set as the packaged envelope's response at its own time. */
function project(response: Raw, now: number): DiOfficeState {
  const raw = clone(JSON.parse(envelopeBytes.toString('utf-8')) as Raw);
  raw.response = response;
  raw.requested_at = now;
  const parsed = parseEnvelope(raw);
  if (!parsed.ok) throw new Error(`did not parse: ${parsed.verdict.detail}`);
  return buildDiOfficeStateMessage(buildOfficeView(parsed.envelope, FRESH, now, 2), () => 41);
}

function exportDoc(target: string, doc: Raw): void {
  const text = JSON.stringify(doc, null, 2) + '\n';
  const at = path.join(ROOT, target);
  if (process.env.DI_EXPORT_OFFICE_STATE === '1') fs.writeFileSync(at, text);
  expect(fs.readFileSync(at, 'utf-8')).toBe(text);
}

const LABEL_TAIL = 'projected by the real server (parseEnvelope, buildOfficeView, buildDiOfficeStateMessage); evidences nothing about any live Mission';

describe('one Mission Core Mission through its whole lifecycle, as the real producer answers it, reaches the office step by step', () => {
  it('projects every step: the same Mission present at each, with its record state and retention phase as the producer stated them', () => {
    const bytes = bytesOf(LIFECYCLE_REL);
    const answers = JSON.parse(bytes.toString('utf-8')) as {
      mission_id: string;
      overdue_mission_id: string;
      source_id: string;
      steps: Array<{ step: string; now: number; response: Raw }>;
    };
    const ref = `${answers.source_id}:${answers.mission_id}`;
    const states: Record<string, DiOfficeState> = {};
    const read: Array<[string, string | undefined, string | null | undefined]> = [];
    for (const { step, now, response } of answers.steps) {
      const state = project(response, now);
      expect(state.live, step).toBe(true);
      const mission = state.missions.missions.find((m) => m.missionRef === ref);
      expect(mission, step).toBeDefined();
      read.push([step, mission!.v2.projected ? mission!.v2.missionState.progress?.state : undefined, mission!.retention?.phase]);
      states[step] = state;
    }
    expect(read.map(([step, , phase]) => [step, phase])).toEqual([
      ['of_record', 'intact'],
      ['approved_never_started', 'intact'],
      ['start_requested', 'intact'],
      ['task_observed', 'intact'],
      ['completed_ended_archived', 'intact'],
      ['reclaimed_at_startup', 'reclaimed'],
      ['deleted_by_owner', 'deleted'],
    ]);
    // the SAME Mission's task-observed answer, read by the office after its validity has passed: the office's own local expiry, never a fresh reading
    const observed = answers.steps.find((s) => s.step === 'task_observed')!;
    const expired = project(observed.response, observed.now + EXPIRED_AFTER_SECONDS);
    expect(expired.live).toBe(false);
    expect(expired.missions.missions.some((m) => m.missionRef === ref)).toBe(true);
    exportDoc(LIFECYCLE_TARGET, {
      fixture: true,
      label: `FIXTURE — one Mission Core Mission's REAL catalog answer at each lifecycle step (tests/test_mission_lifecycle_answers.py ML2), ${LABEL_TAIL}`,
      derived_from: {
        answers: { path: LIFECYCLE_REL, sha256: sha(bytes) },
        envelope: { path: ENVELOPE_REL, sha256: sha(envelopeBytes) },
        edit: 'each step: the envelope response set to that step’s answer, its requested_at to the step’s own now; nothing in an answer edited',
      },
      generator: 'server/__tests__/diMissionLifecycleWire.test.ts',
      mission_ref: ref,
      overdue_mission_ref: `${answers.source_id}:${answers.overdue_mission_id}`,
      steps: answers.steps.map((s) => s.step),
      states,
      expired: { step: 'task_observed', read_after_seconds: EXPIRED_AFTER_SECONDS, state: expired },
    });
  });
});

describe('the producer’s own Herdr roster reaches the office: what it may say about workers is what the roster decided', () => {
  it('projects the roster answer: the uncorroborated task has no members, the working and blocked tasks one each', () => {
    const bytes = bytesOf(ROSTER_REL);
    const answer = JSON.parse(bytes.toString('utf-8')) as { now: number; response: Raw };
    const state = project(answer.response, answer.now);
    expect(state.live).toBe(true);
    const task = (id: string) => state.missions.missions.find((m) => m.nativeId.startsWith(`${id}@`))!;
    expect(state.missions.missions).toHaveLength(8);
    expect([task('task-unverified').bindingState, task('task-unverified').bindingReason]).toEqual([
      'unverified',
      'task_record_not_corroborated_by_a_live_session',
    ]);
    for (const id of ['task-working', 'task-blocked']) expect(task(id).bindingState, id).toBe('bound');
    expect(task('task-suspended').lifecycleState).toBe('SUSPENDED');
    exportDoc(ROSTER_TARGET, {
      fixture: true,
      label: `FIXTURE — the REAL catalog answer of the producer's Herdr roster, one task per outcome (tests/test_herdr_roster_answers.py HR2; its runtime snapshot is SYNTHETIC), ${LABEL_TAIL}`,
      derived_from: {
        answer: { path: ROSTER_REL, sha256: sha(bytes) },
        envelope: { path: ENVELOPE_REL, sha256: sha(envelopeBytes) },
        edit: 'the envelope response set to the answer, its requested_at to the answer’s own now; nothing in the answer edited',
      },
      generator: 'server/__tests__/diMissionLifecycleWire.test.ts',
      state,
    });
  });
});

describe('workflow Missions in the phases only a workflow declares, as the real reader answers, reach the office', () => {
  it('projects the workflow store: DISPATCHED with its sent-handoff receipt, VERIFIED and NEEDS_REAUTHORIZATION', () => {
    const bytes = bytesOf(WORKFLOW_REL);
    const answer = JSON.parse(bytes.toString('utf-8')) as { now: number; response: Raw };
    const state = project(answer.response, answer.now);
    expect(state.live).toBe(true);
    const phase = (id: string) => state.missions.missions.find((m) => m.nativeId === id)!;
    expect(phase('wf-dispatched').v2.workflowPhase?.phase).toBe('DISPATCHED');
    expect(phase('wf-dispatched').sentHandoffReceipts).toBe(1);
    expect(phase('wf-verified').v2.workflowPhase?.phase).toBe('VERIFIED');
    expect(phase('wf-reauthorize').v2.workflowPhase?.phase).toBe('NEEDS_REAUTHORIZATION');
    exportDoc(WORKFLOW_TARGET, {
      fixture: true,
      label: `FIXTURE — the REAL catalog answer over one real workflow store (tests/test_workflow_phase_answers.py WP2), ${LABEL_TAIL}`,
      derived_from: {
        answer: { path: WORKFLOW_REL, sha256: sha(bytes) },
        envelope: { path: ENVELOPE_REL, sha256: sha(envelopeBytes) },
        edit: 'the envelope response set to the answer, its requested_at to the answer’s own now; nothing in the answer edited',
      },
      generator: 'server/__tests__/diMissionLifecycleWire.test.ts',
      state,
    });
  });
});

describe('an archived Mission’s restart is refused SERVER-SIDE, and the office’s real relay says so', () => {
  type Answer = { status: number; body: Raw };
  const bytes = bytesOf(REFUSALS_REL);
  const answers = (JSON.parse(bytes.toString('utf-8')) as { answers: Record<string, Answer> }).answers;
  const REF = 'lr-' + '0'.repeat(32);

  async function relay(answer: Answer, command: string): Promise<DiMissionRequestReply> {
    const sent: DiMissionRequestReply[] = [];
    const relayer = new DiMissionRequests('/disc.json', {
      readDiscovery: () => ({ ok: true as const, endpoint: { port: 4321, token: 'relay-test-token' } }),
      request: async (): Promise<PanelResult> => ({ kind: 'http', status: answer.status, body: answer.body, bodyProblem: null }),
      log: () => undefined,
    });
    await relayer.handle({ type: 'diMissionRequest', seq: 7, op: 'run', requestRef: REF, command }, (m) => sent.push(m as unknown as DiMissionRequestReply));
    expect(sent).toHaveLength(1);
    return sent[0]!;
  }

  it('refused for the archived and the reclaimed Mission, with the panel’s own reason; accepted for the Mission that is not archived', async () => {
    const replies: Record<string, DiMissionRequestReply> = {};
    for (const [name, command] of [
      ['archived_run_dispatch', 'dispatch'],
      ['archived_run_observe', 'observe'],
      ['reclaimed_run_dispatch', 'dispatch'],
    ] as const) {
      const reply = await relay(answers[name]!, command);
      expect(reply.state, name).toBe('refused');
      expect(reply.problem, name).toBe('mission_panel_action_not_offered');
      expect(reply.reason, name).toMatch(/is not offered for a proposal that is verified/);
      replies[name] = reply;
    }
    const active = await relay(answers.active_run_dispatch!, 'dispatch');
    expect(active.state).toBe('ok');
    replies.active_run_dispatch = active;
    exportDoc(REPLIES_TARGET, {
      fixture: true,
      label: 'FIXTURE — the office’s REAL relay (DiMissionRequests.handle) fed the panel’s real HTTP answers to a restart of an archived Mission, and the not-archived countercase',
      derived_from: { answers: { path: REFUSALS_REL, sha256: sha(bytes) } },
      generator: 'server/__tests__/diMissionLifecycleWire.test.ts',
      replies,
    });
  });
});
