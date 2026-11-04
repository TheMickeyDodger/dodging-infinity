/**
 * Owed item 2 (round 39's advisory): the scan face filed a Mission under "Merged" whenever its completion was observed as VERIFIED
 * SUCCESS (diScanModel.ts tabForMission). Verified Mission success does not establish a merged pull request, and the design names no
 * other source for the tab: CONTROL-MAP.md lists "Open/Merged/Closed/Archived tabs" only as a Home filter (the in-pin design pack,
 * its manifest checked). So a Mission is Merged only when its OWN record says so; verified success alone files it as any other
 * Mission. Pure model functions over a fixture: no browser, no network, no process.
 */
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

import { describe, expect, it } from 'vitest';

import type { DiMission, DiOfficeState } from '../../core/src/messages.ts';
import { scanRowsFromOffice } from '../src/components/diScanModel.ts';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const fixture = (
  JSON.parse(
    fs.readFileSync(path.join(root, 'test/fixtures/di-office-state-session-mismatch-v2.FIXTURE.json'), 'utf-8'),
  ) as { state: DiOfficeState }
).state;

/** The fixture with ONE projected Mission changed: its completion observed as `verifiedSuccess`, its lifecycle as given. */
function withOne(lifecycleState: string): { state: DiOfficeState; mission: DiMission } {
  const state = structuredClone(fixture);
  const mission = state.missions.missions.find((m) => m.v2.projected);
  expect(mission, 'the fixture holds a projected Mission').toBeDefined();
  const m = mission as DiMission;
  m.lifecycleState = lifecycleState;
  m.v2.missionState.completion = { ...m.v2.missionState.completion, state: 'observed', verifiedSuccess: true };
  return { state, mission: m };
}

describe('owed item 2: verified success is not a merged pull request', () => {
  it('a Mission whose completion is verified success, and whose own record says nothing of a merge, is not filed under Merged and wears no Merged pill', () => {
    const { state, mission } = withOne('running');
    expect(mission.lifecycleState.toLowerCase()).not.toContain('merg');
    const row = scanRowsFromOffice(state).find((r) => r.id === mission.missionRef);
    expect(row, 'the Mission has a row').toBeDefined();
    expect(row?.tab).not.toBe('merged');
    expect(row?.pills.map((pill) => pill.label)).not.toContain('Merged');
  });

  it('a lifecycle literal that merely READS "merged" files nothing under Merged: no producer declares a merged literal, and the native literal is never sniffed (Deliverable 1)', () => {
    // Before, any literal containing "merg" (case-folded, by substring) filed the row under Merged. No source this office reads declares one (mission/record.py,
    // workflow_authority/record.py, herdr/vintage.py), and lifecycleState is verbatim and never switched on (catalogProjection.ts). The row files by its standing.
    for (const literal of ['merged', 'MERGED', 'auto-merged', 'archived']) {
      const { state, mission } = withOne(literal);
      const row = scanRowsFromOffice(state).find((r) => r.id === mission.missionRef);
      // CHANGED IN Deliverable 2: the row files by the positive-basis rule, never by its literal; with no basis for an unknown literal, it stays open
      expect(row?.tab, literal).not.toBe('merged');
      expect(row?.tab, literal).toBe('open');
      expect(row?.pills.map((pill) => pill.label), literal).not.toContain('Merged');
    }
  });
});
