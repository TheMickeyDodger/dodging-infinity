/**
 * Deliverable 1 (Mission status contract): the catalog's own execution fact `missions[].execution.run_started` crosses the wire as `DiMission.runStarted`, so the
 * office can say a Mission whose run never started is NOT STARTED instead of guessing from a binding, a phase or a task record. Before this, catalogProjection.ts parsed
 * the field (`MissionView.execution.runStarted`) and dropped it: nothing put it on the wire and no UI read it.
 *
 * Over the hash-pinned contract-v2 emission (`catalog-answer-v2.fc409bbc.FIXTURE.json`, SYNTHETIC ids; fixture evidence only, never live acceptance) and the contract-v3
 * canonical fixture read as a v1 office. Each case goes through the real parser, the real office view and the real message builder. The value is the catalog's, verbatim:
 * flipping the raw field flips the wire, so nothing here infers it from a lifecycle literal, a standing or a binding reason.
 */
import * as crypto from 'crypto';
import * as fs from 'fs';
import * as path from 'path';
import { describe, expect, it } from 'vitest';

import type { DiMission } from '../../core/src/messages.js';
import { parseEnvelope } from '../src/di/catalogProjection.js';
import { buildDiOfficeStateMessage } from '../src/di/officeStateMessage.js';
import { buildOfficeView } from '../src/di/officeView.js';
import type { ServeTimeVerdict } from '../src/di/serveTimeVerdict.js';
import { clone, loadFixture } from './helpers/diFixture.js';

type Raw = Record<string, unknown>;
const FRESH: ServeTimeVerdict = { health: { known: true, value: 'fresh' }, problem: null };
const V2_PATH = path.join(
  __dirname,
  'fixtures/di-backend/v2/catalog-answer-v2.fc409bbc.FIXTURE.json',
);
const V2_SHA256 = 'fc409bbc41a6ee3a17253acebe85092a4d118511954fadf2c457ecc11b891556';
const V2_NOW = 1000000; // the fixture's own generated_at
const V1_NOW = 1800000000;
/** Mission Core, RUNNING, bound: the catalog records a run start (`run_started: true`). */
const STARTED = 'synthetic_mission_core:mn-00000000000000000000000000000002';
/** Mission Core, AWAITING_DECISION: no run was ever started (`run_started: false`). */
const NEVER_STARTED = 'synthetic_mission_core:mn-00000000000000000000000000000009';
/** Workflow store, phase DISPATCHED, no target engine recorded (`run_started: false`). */
const DISPATCHED_NO_ENGINE = 'synthetic_workflow_store:wf-0005';

function loadV2(): Raw {
  const bytes = fs.readFileSync(V2_PATH);
  const digest = crypto.createHash('sha256').update(bytes).digest('hex');
  if (digest !== V2_SHA256) throw new Error(`v2 FIXTURE hash mismatch: ${digest}`);
  return JSON.parse(bytes.toString('utf-8')) as Raw;
}
const missionRaw = (raw: Raw, ref: string): Raw => {
  const found = ((raw.response as Raw).missions as Raw[]).find((m) => m.mission_ref === ref);
  if (!found) throw new Error(`no ${ref}`);
  return found;
};

/** The v2 office's wire Missions by ref. `edit` varies the raw answer. */
function v2Missions(edit?: (raw: Raw) => void): Record<string, DiMission> {
  const raw = clone(loadV2());
  edit?.(raw);
  const parsed = parseEnvelope(raw);
  if (!parsed.ok) throw new Error(`did not parse: ${parsed.verdict.detail}`);
  const message = buildDiOfficeStateMessage(
    buildOfficeView(parsed.envelope, FRESH, V2_NOW, 2),
    () => 41,
  );
  return Object.fromEntries(message.missions.missions.map((m) => [m.missionRef, m]));
}

describe('runStarted: the catalog execution fact reaches the wire, verbatim', () => {
  it('a Mission whose catalog record says a run started carries runStarted true', () => {
    expect(v2Missions()[STARTED]?.runStarted).toBe(true);
  });

  it('a Mission whose catalog record says no run ever started carries runStarted false (Mission Core and workflow store alike)', () => {
    const missions = v2Missions();
    expect(missions[NEVER_STARTED]?.runStarted).toBe(false);
    expect(missions[DISPATCHED_NO_ENGINE]?.runStarted).toBe(false);
  });

  it('the value is the field itself, not an inference: flipping the raw field flips the wire and nothing else decides it', () => {
    const flipped = v2Missions((raw) => {
      (missionRaw(raw, STARTED).execution as Raw).run_started = false;
      (missionRaw(raw, NEVER_STARTED).execution as Raw).run_started = true;
    });
    expect(flipped[STARTED]?.runStarted).toBe(false);
    expect(flipped[NEVER_STARTED]?.runStarted).toBe(true);
    // the lifecycle literal and standing are untouched by the flip: runStarted is not derived from them
    expect(flipped[STARTED]?.lifecycleState).toBe('RUNNING');
    expect(flipped[NEVER_STARTED]?.lifecycleState).toBe('AWAITING_DECISION');
  });

  it('a v1 office carries it too: the execution block is part of both contract versions', () => {
    const parsed = parseEnvelope(clone(loadFixture()));
    if (!parsed.ok) throw new Error('v1 fixture did not parse');
    const missions = buildDiOfficeStateMessage(
      buildOfficeView(parsed.envelope, FRESH, V1_NOW),
      () => 41,
    ).missions.missions;
    expect(missions.length).toBeGreaterThan(0);
    for (const mission of missions)
      expect(typeof mission.runStarted, mission.missionRef).toBe('boolean');
  });
});
