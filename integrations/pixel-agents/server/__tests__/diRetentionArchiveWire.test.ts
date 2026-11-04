/**
 * Deliverable 2, archive health, end to end on the office's side: the REAL catalog's `retained_by_source` readings (exported by tests/test_mission_retention.py CR4 and
 * AC5 from the real query and reader) injected into the packaged contract-v2 answer, projected by the REAL server (parseEnvelope, buildOfficeView,
 * buildDiOfficeStateMessage), and exported for the webview's retained-only rendering. An unreadable retention store is carried as `unavailable` WITH its reason and
 * no count, never as an empty archive; the only edit to the readings is their source id, re-addressed to the packaged answer's workflow source.
 */
import * as crypto from 'crypto';
import * as fs from 'fs';
import * as path from 'path';
import { describe, expect, it } from 'vitest';

import { parseEnvelope } from '../src/di/catalogProjection.js';
import { buildDiOfficeStateMessage } from '../src/di/officeStateMessage.js';
import { buildOfficeView } from '../src/di/officeView.js';
import type { ServeTimeVerdict } from '../src/di/serveTimeVerdict.js';
import { clone } from './helpers/diFixture.js';

type Raw = Record<string, unknown>;
const ROOT = path.join(__dirname, '..', '..');
const FRESH: ServeTimeVerdict = { health: { known: true, value: 'fresh' }, problem: null };
const V2_REL = 'server/__tests__/fixtures/di-backend/v2/catalog-answer-v2.fc409bbc.FIXTURE.json';
const V2_NOW = 1000000;
const WORKFLOW_SOURCE = 'synthetic_workflow_store';
const WORKFLOW = `${WORKFLOW_SOURCE}:wf-0005`;
const UNAVAILABLE_REL = 'server/__tests__/fixtures/di-backend/retention-archive-unavailable.FIXTURE.json';
const OBSERVED_REL = 'server/__tests__/fixtures/di-backend/retention-archive-observed.FIXTURE.json';
const TARGET_UNAVAILABLE = 'webview-ui/test/fixtures/di-office-state-archive-unavailable-v2.FIXTURE.json';
const TARGET_OBSERVED = 'webview-ui/test/fixtures/di-office-state-archive-observed-v2.FIXTURE.json';

const read = (rel: string): Raw => JSON.parse(fs.readFileSync(path.join(ROOT, rel), 'utf-8')) as Raw;
/** A reading re-addressed to the packaged answer's workflow source (its only edit). */
function readdressed(reading: Raw): Raw {
  const from = String(reading.source_id);
  return {
    ...reading,
    source_id: WORKFLOW_SOURCE,
    missions: (reading.missions as Raw[]).map((m) => ({
      ...m,
      source_id: WORKFLOW_SOURCE,
      mission_ref: String(m.mission_ref).replace(`${from}:`, `${WORKFLOW_SOURCE}:`),
    })),
  };
}

function project(edit: (raw: Raw) => void) {
  const bytes = fs.readFileSync(path.join(ROOT, V2_REL));
  const raw = clone(JSON.parse(bytes.toString('utf-8')) as Raw);
  edit(raw);
  const parsed = parseEnvelope(raw);
  if (!parsed.ok) throw new Error(`did not parse: ${parsed.verdict.detail}`);
  return { bytes, state: buildDiOfficeStateMessage(buildOfficeView(parsed.envelope, FRESH, V2_NOW, 2), () => 41) };
}

function exportState(target: string, edit: (raw: Raw) => void, editWords: string, what: string) {
  const { bytes, state } = project(edit);
  const doc = {
    fixture: true,
    label:
      `FIXTURE — the contract-v2 office state with ${what}, projected by the real server (parseEnvelope, buildOfficeView, buildDiOfficeStateMessage) ` +
      '— SYNTHETIC ARCHIVE REGRESSION; evidences nothing about any live Mission',
    derived_from: { path: V2_REL, sha256: crypto.createHash('sha256').update(bytes).digest('hex'), edit: editWords },
    generator: 'server/__tests__/diRetentionArchiveWire.test.ts',
    state,
  };
  const text = JSON.stringify(doc, null, 2) + '\n';
  const at = path.join(ROOT, target);
  if (process.env.DI_EXPORT_OFFICE_STATE === '1') fs.writeFileSync(at, text);
  expect(fs.readFileSync(at, 'utf-8')).toBe(text);
  return state;
}

/** The original store EMPTY: its one workflow Mission gone from the answer. */
const emptied = (raw: Raw) => {
  const response = raw.response as Raw;
  response.missions = (response.missions as Raw[]).filter((m) => m.mission_ref !== WORKFLOW);
};

describe('archive health reaches the office: unreadable is never a known-empty archive', () => {
  it('an EMPTY workflow store whose retention store is malformed: unavailable, with its reason and no count', () => {
    const reading = readdressed((read(UNAVAILABLE_REL).retained_by_source as Raw[])[0]!);
    const state = exportState(
      TARGET_UNAVAILABLE,
      (raw) => {
        emptied(raw);
        (raw.response as Raw).retained_by_source = [reading];
      },
      `${WORKFLOW} removed (its store emptied), and response.retained_by_source set to the real reading of ${UNAVAILABLE_REL}, its source id re-addressed`,
      'an emptied workflow store whose retention store could not be read',
    );
    expect(state.missions.missions.some((m) => m.missionRef === WORKFLOW)).toBe(false);
    expect(state.missions.retained).toEqual([
      { sourceId: WORKFLOW_SOURCE, state: 'unavailable', problem: 'mission_retention_unreadable', total: null, awaiting: null, missions: [] },
    ]);
  });
  it('a readable archive: observed, with its exact counts and each Mission’s summary as retention kept it', () => {
    const reading = readdressed(read(OBSERVED_REL).reading as Raw);
    const state = exportState(
      TARGET_OBSERVED,
      (raw) => {
        emptied(raw);
        (raw.response as Raw).retained_by_source = [reading];
      },
      `${WORKFLOW} removed (its store emptied), and response.retained_by_source set to the real reading of ${OBSERVED_REL}, its source id re-addressed`,
      'an emptied workflow store whose two terminal Missions were reclaimed whole',
    );
    const [only] = state.missions.retained ?? [];
    expect(only).toMatchObject({ sourceId: WORKFLOW_SOURCE, state: 'observed', problem: null, total: 2, awaiting: 0 });
    expect(only!.missions.map((m) => [m.missionRef, m.summary?.finalState])).toEqual([
      [`${WORKFLOW_SOURCE}:wf-done`, 'COMPLETED'],
      [`${WORKFLOW_SOURCE}:wf-never`, 'BLOCKED'],
    ]);
  });
  it('the projection refuses a reading that claims a count while unavailable, or lists more than it counts', () => {
    for (const bad of [
      { source_id: WORKFLOW_SOURCE, state: 'unavailable', problem: 'x', total: 0, awaiting: null, missions: [] },
      { source_id: WORKFLOW_SOURCE, state: 'observed', problem: null, total: 0, awaiting: 0, missions: (readdressed(read(OBSERVED_REL).reading as Raw).missions as Raw[]) },
    ]) {
      const { state } = project((raw) => {
        (raw.response as Raw).retained_by_source = [bad];
      });
      // the whole answer is refused as malformed: never shown, never read as a count
      expect(state.live).toBe(false);
      expect(state.notLiveReason).toBe('malformed:response_shape_invalid');
      expect(state.missions.retained).toBeUndefined();
    }
  });
  // Round 10: a retained fact's state is CLOSED, and each claim reaches the office as made: confirmed absence, not read, unreadable and not known are never
  // folded into one another, and a state this office does not know, or a detail on anything but a recorded fact, refuses the whole answer.
  it('each retained fact claim, as the real producers make it, reaches the office unchanged; an unknown claim refuses the answer', () => {
    const produced = read('webview-ui/test/fixtures/di-retained-fact-states.FIXTURE.json') as { facts: Record<string, Raw>; upgraded_v1_summary: Raw };
    const claims: Raw[] = [...Object.values(produced.facts), produced.upgraded_v1_summary.verdict as Raw, produced.upgraded_v1_summary.ci as Raw];
    expect(new Set(claims.map((f) => f.state))).toEqual(new Set(['not_read', 'unreadable', 'not_recorded_locally', 'not_known']));
    const withCi = (ci: Raw) => (raw: Raw) => {
      const reading = readdressed(read(OBSERVED_REL).reading as Raw);
      const first = (reading.missions as Raw[])[0]!;
      first.summary = { ...(first.summary as Raw), ci };
      (raw.response as Raw).retained_by_source = [reading];
    };
    for (const ci of claims) {
      const { state } = project(withCi(ci));
      expect(state.missions.retained?.[0]?.missions[0]?.summary?.ci, String(ci.state)).toEqual({ state: ci.state, detail: ci.detail, source: ci.source });
    }
    for (const bad of [
      { state: 'absent', detail: null, source: 's' },
      { state: 'unreadable', detail: 'a record may say anything', source: 's' },
      { state: 'recorded', detail: null, source: 's' },
    ]) {
      const { state } = project(withCi(bad));
      expect(state.live, bad.state).toBe(false);
      expect(state.notLiveReason).toBe('malformed:response_shape_invalid');
    }
  });
  it('not reported (absent, or null): the office carries no archive at all, never an empty one', () => {
    for (const value of [undefined, null]) {
      const { state } = project((raw) => {
        if (value === null) (raw.response as Raw).retained_by_source = null;
      });
      expect(state.missions.retained).toBeUndefined();
    }
  });
});
