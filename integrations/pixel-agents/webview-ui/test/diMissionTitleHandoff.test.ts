/**
 * Plain-English primary naming (owner ruling, 2026-10-28): a Mission's primary title is never the handoff's authority boilerplate, a "This is…" scrap, a proposal digest or a machine id.
 *
 * The handoff a bridge-dispatched Mission's Herdr task records is a FIXED template (target_runtime/mission_bridge.py `handoff_text`): its first line is authority only, `AUTHORIZED MISSION <id>
 * (revision <n>, proposal digest <64 hex>). This is the approved Mission content only, NOT an engineering plan; the Supervisor owns all technical decisions.`; the goal follows in its OBJECTIVE
 * section. The Herdr-task catalog source (target_runtime/herdr_task_source.py `_objective`) took the description's FIRST non-empty line, cut at 160 characters, so what reached the office was that
 * lead cut after " This is ".
 *
 * This file holds the OFFICE side of plain-English naming: the producer's cut, as context (a copy of the Python template and its 160-character cut; the producer's own fix is pinned by
 * tests/test_herdr_task_objective_handoff.py), and the no-goal headline (i-a).
 */
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

import { describe, expect, it } from 'vitest';

import type { DiMission, DiOfficeState } from '../../core/src/messages.ts';
import { missionObjective } from '../src/components/diOperationalStatus.ts';
import { missionTitle } from '../src/components/diPlainSummary.ts';
import { scanRowsFromOffice } from '../src/components/diScanModel.ts';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');

const MISSION_ID = `mn-${'589a2181de974f8b0fee47cb43e475f7'}`;
const DIGEST = '3033dab9ca1127b3a8dc907fcbbfbeb522aa461d3035277ff41413fa58820bcb';
/** A copy of the bridge template's first line (mission_bridge.py `handoff_text`, its prefix from run_association.py `handoff_prefix`). */
const LEAD = `AUTHORIZED MISSION ${MISSION_ID} (revision 1, proposal digest ${DIGEST}). This is the approved Mission content only, NOT an engineering plan; the Supervisor owns all technical decisions.`;
/** A copy of the Herdr-task source's bound (herdr_task_source.py `_objective`, MAX_OBJECTIVE_CHARS = 160). */
const cut160 = (line: string) => (line.length <= 160 ? line : `${line.slice(0, 159)}…`);

const withObjective = (value: string | null): DiMission =>
  ({
    missionRef: `mc1:${MISSION_ID}`,
    sourceId: 'herdr_tasks',
    sourceKind: 'herdr_task',
    nativeId: MISSION_ID,
    objective: value === null ? { state: 'not_reported', value: null, reason: null } : { state: 'observed', value, reason: null },
  }) as unknown as DiMission;

/**
 * The cut the producer made, kept as context for the producer fix (target_runtime/herdr_task_source.py `_objective`, pinned by tests/test_herdr_task_objective_handoff.py). What the office made of
 * it, "This is …", was OBSERVED before that fix (logs/naming-observe-20261028T234029Z.log) and is not asserted here: the fix is at the producer, and no office guard for the cut lead is built (the
 * catalog re-reads each task description on every pass, so no cut copy persists once the producer is fixed).
 */
describe('the producer cut, as it was', () => {
  it('the bridge template line cut at 160 characters ends inside the lead (" This is …")', () => {
    expect(cut160(LEAD)).toHaveLength(160);
    expect(cut160(LEAD).endsWith('This is …')).toBe(true);
  });

  it('a goal line, as the fixed producer now emits it, is the title in plain words', () => {
    expect(missionTitle(withObjective('Fix the login timeout so sessions stay alive.')).text).toBe('Fix the login timeout so sessions stay alive');
  });
});

/**
 * (i-a), on the scan face: a Mission with NO goal reported is headed "No goal reported" (the source reported none; "recorded" would claim none exists), and the source-qualified note with its
 * state code stays visible as SECONDARY detail (the row's description), never deleted to tidy the headline.
 */
describe('a Mission with no goal reported, on the scan face', () => {
  const state = (
    JSON.parse(fs.readFileSync(path.join(root, 'test/fixtures/di-office-state-session-mismatch-v2.FIXTURE.json'), 'utf-8')) as { state: DiOfficeState }
  ).state;
  const noGoal = state.missions.missions.filter((m) => m.objective.value === null);
  const rows = scanRowsFromOffice(state);

  it('the fixture has such Missions (a non-empty guard)', () => {
    expect(noGoal.length).toBeGreaterThan(0);
  });

  it('the headline is exactly "No goal reported": no source id, no state code', () => {
    for (const m of noGoal) {
      const row = rows.find((r) => r.id === m.missionRef)!;
      expect(row.title, m.missionRef).toBe('No goal reported');
    }
  });

  it('the source and state stay as secondary detail: the row description carries the source-qualified note', () => {
    for (const m of noGoal) {
      const row = rows.find((r) => r.id === m.missionRef)!;
      expect(row.description, m.missionRef).toBe(missionObjective(m).text);
      expect(row.description, m.missionRef).toContain(m.sourceId);
    }
  });
});
