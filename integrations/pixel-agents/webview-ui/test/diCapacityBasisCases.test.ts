/**
 * Deliverable 2, the capacity seam (lead finding after round 8): a basis the office and the backend share BY NAME must rest on the SAME record fact and decide the
 * same cases. ONE committed table (test/fixtures/di-capacity-basis-cases.FIXTURE.json) is run through the office here and through the backend's retention by
 * tests/test_mission_retention.py E8; whatever the office reads that the backend cannot mirror is declared (`DI_CAPACITY_ASYMMETRIC`) with its reason.
 *
 * Then the production-shaped pair the REAL server projects (server/__tests__/diMissionProgressWire.test.ts exports): a terminal BLOCKED workflow that never sent a
 * handoff (no durable dispatch receipt), and its counterexample BLOCKED after one was sent. Archival is a VIEW: it never frees capacity, and live or uncertain
 * workers keep the guard closed whatever the filing says.
 */
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

import { describe, test } from 'vitest';

import type { DiMission, DiOfficeState } from '../../core/src/messages.ts';
import {
  capacityReleaseBasis,
  DI_CAPACITY_ASYMMETRIC,
  missionArchive,
  missionStatus,
} from '../src/components/diMissionStatus.ts';
import { scanRowsFromOffice } from '../src/components/diScanModel.ts';
import { orchestratorUse } from '../src/components/diScanPagesModel.ts';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const fixture = (name: string): unknown =>
  JSON.parse(fs.readFileSync(path.join(root, 'test/fixtures', name), 'utf-8')) as unknown;
const officeState = (name: string): DiOfficeState => (fixture(name) as { state: DiOfficeState }).state;

type Office = {
  sourceKind: string;
  lifecycleModel: string;
  standing: string;
  lifecycleState: string;
  runStarted: boolean;
  cancelQuiescence?: string;
  sentHandoffReceipts?: number | null;
  workflowPhase?: string;
};
type Case = { case: string; basis?: string; office: Office; office_source?: string };
const table = fixture('di-capacity-basis-cases.FIXTURE.json') as {
  shared: Case[];
  refused: Case[];
  asymmetric: { bases: Record<string, string>; cases: Case[] };
};

/** The bound Mission Core Mission of the real projection, nobody live, reshaped to exactly one table case. */
const BASE = officeState('di-office-state-session-mismatch-v2.FIXTURE.json');
const BOUND_REF = 'mc:mn-1aa604085c36ca55d01b57634ee13f41';
function shaped(office: Office): DiMission {
  const m = structuredClone(BASE.missions.missions.find((x) => x.missionRef === BOUND_REF)!);
  for (const member of m.members.members) member.live = false;
  m.sourceKind = office.sourceKind;
  m.lifecycleModel = office.lifecycleModel;
  m.standing = office.standing;
  m.lifecycleState = office.lifecycleState;
  m.runStarted = office.runStarted;
  m.cancelQuiescence = office.cancelQuiescence ?? null;
  m.sentHandoffReceipts = office.sentHandoffReceipts ?? null;
  if (m.v2.projected && office.workflowPhase !== undefined)
    m.v2.workflowPhase = { state: 'observed', phase: office.workflowPhase, unrecognized: null, problem: null };
  return m;
}

/** What the catalog's roster projects for a NON-ACTIVE record: binding not_applicable (`mission_not_active`) and no members (mission_sources/roster.py; pinned by
 * tests/test_mission_sources.py `test_eligibility_alone_never_creates_members`, with a live runtime agent present). The ordinary finished record. */
function rosterWithheld(m: DiMission): DiMission {
  m.bindingState = 'not_applicable';
  m.members = { state: 'projected_reason', reason: 'binding_not_applicable:mission_not_active', members: [] };
  return m;
}
/** The bound fixture's team, bound, in one of three shapes: confirmed live, live but not confirmed as itself, or confirmed live with no known activity. */
function withTeam(m: DiMission, kind: 'confirmed' | 'unconfirmed' | 'unknown_activity'): DiMission {
  const bound = BASE.missions.missions.find((x) => x.missionRef === BOUND_REF)!;
  m.bindingState = 'bound';
  m.members = structuredClone(bound.members);
  for (const member of m.members.members) {
    member.live = true;
    member.classification = kind === 'unconfirmed' ? 'unconfirmed' : 'present';
    if (kind === 'unknown_activity') member.activity = 'unknown';
  }
  return m;
}
const STALE_BASE: DiOfficeState = { ...structuredClone(BASE), live: false, notLiveReason: 'client_expired:valid_until_passed' };

describe('one table, two layers: the office decides every case as the backend does, from the same field', () => {
  test('every SHARED case: the same basis, from its declared source field, live or not (each is a terminal, durable record fact)', () => {
    assert.ok(table.shared.length >= 7);
    for (const c of table.shared)
      for (const live of [true, false])
        assert.deepEqual(capacityReleaseBasis(shaped(c.office), live), { basis: c.basis, source: c.office_source }, `${c.case} live=${live}`);
  });
  test('every REFUSED case: no basis in the office either (the backend refuses each on no_positive_basis)', () => {
    for (const c of table.refused)
      for (const live of [true, false]) assert.equal(capacityReleaseBasis(shaped(c.office), live), null, `${c.case} live=${live}`);
  });
  test('the declared asymmetry is the office’s own, word for word, and each asymmetric case reads as declared', () => {
    assert.deepEqual(DI_CAPACITY_ASYMMETRIC, table.asymmetric.bases);
    for (const c of table.asymmetric.cases)
      assert.deepEqual(capacityReleaseBasis(shaped(c.office), true), { basis: c.basis, source: c.office_source }, c.case);
  });
});

describe('the live-worker guard (lead conditions on the seam): ordinary release kept, live or uncertain workers never released, stale never releases what it saw', () => {
  test('1. an ORDINARY finished record (the roster withholds its members) still releases, on every shared basis, live and stale', () => {
    for (const c of table.shared)
      for (const s of [BASE, STALE_BASE])
        assert.deepEqual(missionStatus(rosterWithheld(shaped(c.office)), s).capacity, { bears: 'none', basis: c.basis, source: c.office_source }, `${c.case} live=${s.live}`);
  });
  test('2. a confirmed-live team holds, active or terminal; a STALE office never releases what its last good observation had live', () => {
    for (const c of table.shared) {
      assert.equal(missionStatus(withTeam(shaped(c.office), 'confirmed'), BASE).capacity.bears, 'holds', c.case);
      const staleReading = missionStatus(withTeam(shaped(c.office), 'confirmed'), STALE_BASE).capacity;
      assert.equal(staleReading.bears, 'not_known', c.case);
      assert.equal(staleReading.bears === 'not_known' && staleReading.why.kind, 'stale', c.case);
    }
  });
  test('3. a member live but NOT confirmed as itself, or confirmed with no known activity: held or not known, never released, live or stale', () => {
    for (const c of table.shared)
      for (const kind of ['unconfirmed', 'unknown_activity'] as const)
        for (const s of [BASE, STALE_BASE]) {
          const reading = missionStatus(withTeam(shaped(c.office), kind), s).capacity;
          assert.notEqual(reading.bears, 'none', `${c.case} ${kind} live=${s.live}: ${JSON.stringify(reading)}`);
        }
  });
  test('4. sources that disagree read unknown and release nothing: the unknown branch stays ahead of the basis', () => {
    // a workflow whose declared phase is before DISPATCHED (a workflow_before_dispatch basis on its own) while the catalog records a run start: they disagree
    const m = shaped({ sourceKind: 'workflow_store', lifecycleModel: 'workflow_authority.workflow_phase/2', standing: 'active', lifecycleState: 'PLANNED', runStarted: true, workflowPhase: 'PLANNED' });
    m.bindingState = 'unbound';
    m.members = { state: 'observed', reason: null, members: [] };
    assert.ok(capacityReleaseBasis(m, true) !== null, 'a basis would apply on its own');
    const read = missionStatus(m, BASE);
    assert.equal(read.status, 'unknown', JSON.stringify(read.status));
    assert.equal(read.capacity.bears, 'not_known');
  });
});

describe('the production-shaped pair the real server projects: a terminal BLOCKED workflow, before and after a handoff was sent', () => {
  const WORKFLOW = 'synthetic_workflow_store:wf-0005';
  const neverSent = officeState('di-office-state-workflow-blocked-never-sent-v2.FIXTURE.json');
  const afterSend = officeState('di-office-state-workflow-blocked-after-send-v2.FIXTURE.json');
  const find = (s: DiOfficeState) => s.missions.missions.find((m) => m.missionRef === WORKFLOW)!;
  const stale = (s: DiOfficeState): DiOfficeState => ({
    ...structuredClone(s),
    live: false,
    notLiveReason: 'client_expired:valid_until_passed',
  });

  test('never sent: the office and the backend agree (the same basis, the same receipts), and it holds while the office is not live too', () => {
    const m = find(neverSent);
    assert.equal(m.sentHandoffReceipts, 0);
    assert.equal(m.retention?.state === 'observed' && m.retention.basis, 'workflow_not_dispatched');
    for (const s of [neverSent, stale(neverSent)]) {
      const capacity = missionStatus(find(s), s).capacity;
      assert.deepEqual(capacity, { bears: 'none', basis: 'workflow_not_dispatched', source: 'mission.sentHandoffReceipts = 0' }, String(s.live));
      assert.equal(missionArchive(find(s), s).archived, true);
    }
    const rows = scanRowsFromOffice(neverSent).filter((r) => r.id === WORKFLOW);
    assert.equal(rows[0]?.tab, 'archived');
    assert.deepEqual(orchestratorUse(rows), { holding: 0, notKnown: 0 });
  });
  test('after a handoff was sent: no basis in either layer, never released, and not archived', () => {
    const m = find(afterSend);
    assert.equal(m.sentHandoffReceipts, 1);
    assert.equal(m.retention?.state === 'observed' && m.retention.reason, 'no_positive_basis');
    for (const s of [afterSend, stale(afterSend)]) {
      assert.equal(missionStatus(find(s), s).capacity.bears, 'not_known', String(s.live));
      assert.equal(missionArchive(find(s), s).archived, false);
    }
    const rows = scanRowsFromOffice(afterSend).filter((r) => r.id === WORKFLOW);
    assert.equal(rows[0]?.tab, 'open');
    assert.deepEqual(orchestratorUse(rows), { holding: 0, notKnown: 1 });
  });
  test('the production shape the capacity seam caught: the archived never-sent record with a confirmed-live team holds while live, is not known while stale', () => {
    // seam-web (first run) found this releasing capacity on a terminal record; it stays as the regression
    for (const s of [structuredClone(neverSent), stale(neverSent)]) {
      withTeam(find(s), 'confirmed');
      assert.equal(missionArchive(find(s), s).archived, true, 'the backend still files it Archived');
      assert.equal(missionStatus(find(s), s).capacity.bears, s.live ? 'holds' : 'not_known', String(s.live));
      const use = orchestratorUse(scanRowsFromOffice(s).filter((r) => r.id === WORKFLOW));
      assert.equal(use.holding + use.notKnown, 1, 'counted against the limit, never released');
    }
  });
  test('archived or not, a Mission whose sources DISAGREE (a confirmed-live team, no recorded start) holds: the filing never frees capacity', () => {
    // the bound Mission Core Mission: live, confirmed members, yet no recorded run start (its sources disagree), and a backend retention reading that files it Archived
    const s = structuredClone(BASE);
    const m = s.missions.missions.find((x) => x.missionRef === BOUND_REF)!;
    m.runStarted = false;
    m.retention = {
      state: 'observed', problem: null, eligible: true, basis: 'no_start_recorded', basisSource: 'run.intent = null',
      reason: null, phase: 'intact', phaseAt: null, summary: null, retainedFor: 0,
    };
    assert.equal(missionArchive(m, s).archived, true);
    const capacity = missionStatus(m, s).capacity;
    assert.equal(capacity.bears, 'holds', JSON.stringify(capacity));
  });
  test('receipts not reported (an older producer): never 0, and never a basis', () => {
    const s = structuredClone(neverSent);
    find(s).sentHandoffReceipts = null;
    assert.equal(capacityReleaseBasis(find(s), true), null);
  });
});
