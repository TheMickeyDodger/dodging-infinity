/**
 * Deliverable 1, Lead correction C3: the office state of an ACTIVE, BOUND Herdr task whose team is NOT confirmed, projected by ONE real DiOffice from the labelled packaged
 * fixture, for the webview's status regression (diMissionStatus.test.ts). Without `DI_EXPORT_OFFICE_STATE=1` this test fails when the committed webview fixture drifts from
 * the projection; with it, the fixture is (re)written.
 *
 * WHY THIS CASE. For `herdr_task` the catalog's `execution.run_started` is `true` for EVERY existing task record (target_runtime/herdr_task_source.py `_view`: `started_run =
 * True` whenever a record exists, and `receipt_recorded` is the same value), so it says only that a task record exists, never that a worker started. A Herdr task is bound
 * only when at least one recorded role is held by a live agent on exactly its recorded session (mission_sources/roster.py `_corroborated`, applied in `bind`); a task whose
 * workers never started is therefore never bound (it projects as `unverified` / `task_record_not_corroborated_by_a_live_session`). The one producer-shaped record that can be
 * bound with NO member confirmed is this: one recorded role IS live on its recorded session but its live row reports no pane or workspace, so the roster calls it `ambiguous`
 * / `stable_identity_unconfirmed` (roster.py `_member_classification`), and the other roles never started, so they are `disappeared` / `agent_not_live` with no recorded
 * session. The office projects every one of them NOT live (only `present` counts live). Before the fix the webview read that record as Running from the task record alone.
 *
 * THE EDIT. Only the bound task's member records in the packaged fixture's raw answer are changed, to exactly the shape `roster._member` emits for those two cases. Nothing
 * else (its binding, its `execution.run_started`, its lifecycle) is touched: they are the producer's own values.
 *
 * WHAT THIS DOES NOT COVER. It exercises a bound task with one live but UNCONFIRMED session. It is NOT a Mission whose workers never started: that record is never bound
 * (above), and its own coverage is on its real projection path, the packaged fixture's `unverified` / `task_record_not_corroborated_by_a_live_session` Herdr tasks with no
 * runtime agent at their root (webview-ui/test/diMissionStatusSurfaces.test.ts, "review finding 3").
 */
import * as crypto from 'crypto';
import * as fs from 'fs';
import * as path from 'path';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import type { DiOfficeState } from '../../core/src/messages.js';
import { AgentStateStore } from '../src/agentStateStore.js';
import type { BackendResult } from '../src/di/backendClient.js';
import { DiOffice } from '../src/di/diOffice.js';

const ROOT = path.join(__dirname, '..', '..');
const SOURCE = 'packaging/di/fixtures/herdr-task-session-mismatch-v2.FIXTURE.json';
const TARGET = 'webview-ui/test/fixtures/di-office-state-herdr-unconfirmed-team-v2.FIXTURE.json';
/** The packaged fixture's one bound, active Herdr task. */
const BOUND = 'herd:20261017-150121-aaaaaa@5a4574665441';
/** The one role still live on its recorded session (it corroborates the team); its live row reports no pane. */
const CORROBORATING = 'executor1';

type RawMember = Record<string, unknown> & {
  logical: string;
  session: { recorded: string | null; live: string | null };
};
type RawMission = Record<string, unknown> & { mission_ref: string; members: RawMember[] };

/** The member records `roster._member` emits for a bound task whose team is not confirmed (see the header). */
function unconfirmTeam(member: RawMember): RawMember {
  if (member.logical === CORROBORATING)
    // live on exactly its recorded session, but its live row reports no pane: roster.py _member_classification `stable_identity_unconfirmed`
    return {
      ...member,
      pane_id: null,
      classification: 'ambiguous',
      reason: 'stable_identity_unconfirmed',
    };
  // never started: no recorded session, nothing live under its name (roster.py `agent_not_live`; `_member` with no live agent)
  return {
    ...member,
    session: { recorded: null, live: null },
    classification: 'disappeared',
    reason: 'agent_not_live',
    activity: 'unknown',
    provider: 'unknown',
  };
}

describe('C3: the webview status regression renders the real projection of a bound Herdr task with no confirmed team', () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  it('the committed office state equals the projection of the packaged fixture with the producer-shaped unconfirmed team', async () => {
    const raw = fs.readFileSync(path.join(ROOT, SOURCE));
    const fixture = JSON.parse(raw.toString('utf-8')) as {
      label: string;
      frames: Array<{ envelope: { response: { generated_at: number; missions: RawMission[] } } }>;
    };
    const body = fixture.frames[0]!.envelope;
    const bound = body.response.missions.find((m) => m.mission_ref === BOUND);
    expect(bound, 'the packaged fixture holds the bound Herdr task').toBeDefined();
    expect(bound!.members.some((m) => m.logical === CORROBORATING)).toBe(true);
    bound!.members = bound!.members.map(unconfirmTeam);
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
    const state: DiOfficeState = office.officeStateMessage();
    office.stop();

    // The wire facts the regression stands on, as the REAL projection produced them (none is set by hand downstream).
    expect(state.live).toBe(true);
    const mission = state.missions.missions.find((m) => m.missionRef === BOUND)!;
    expect(mission.sourceKind).toBe('herdr_task');
    expect(mission.standing).toBe('active');
    expect(mission.bindingState).toBe('bound');
    // the always-true proxy: a task record exists, so the producer says run_started
    expect(mission.runStarted).toBe(true);
    expect(mission.members.state).toBe('observed');
    expect(mission.members.members.length).toBeGreaterThan(0);
    // no member is live AND confirmed as itself
    expect(mission.members.members.filter((m) => m.live && m.classification === 'present')).toEqual(
      [],
    );

    const doc = {
      fixture: true,
      label:
        'FIXTURE — the office state of herdr-task-session-mismatch-v2 with its bound Herdr task’s team NOT confirmed (one role live on its recorded session but ' +
        'its pane unconfirmed; the others never started), projected by the real server (DiOffice) — SYNTHETIC STATUS REGRESSION; evidences nothing about any live team',
      derived_from: {
        path: SOURCE,
        sha256: crypto.createHash('sha256').update(raw).digest('hex'),
        label: fixture.label,
        edit: `only ${BOUND}'s member records, to roster._member's shape for stable_identity_unconfirmed (${CORROBORATING}) and agent_not_live (every other role)`,
      },
      generator: 'server/__tests__/diHerdrUnconfirmedTeamExport.test.ts',
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
});
