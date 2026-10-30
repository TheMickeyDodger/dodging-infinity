/**
 * S3: the producer's DECLARED workflow phase crosses the wire (`DiMissionV2.workflowPhase`), from the facts the office already projects and nothing else.
 *
 * Over the hash-pinned contract-v2 emission (`catalog-answer-v2.fc409bbc.FIXTURE.json`, SYNTHETIC ids; fixture evidence only, never live acceptance), edited per case with the repository's own
 * `declareWorkflowPhases` helper, so each state of the declared field is exercised through the real parser, the real office view and the real message builder. A v1 office is the contract-v3
 * canonical fixture read at the default (v1) contract version.
 *
 * Source facts this respects (catalogProjection.ts): the declared field exists only inside the v2 block (`WorkflowPhaseView`, `MissionView.v2`); an ABSENT `workflow_phase` is `not_reported`
 * (unknown, never "none"); `observed` is a separate explicit producer state, valid only for a workflow_authority record; `not_applicable` is separate and carries a null phase; and a v1 office has
 * NO v2 block at all (`v2: null`). The wire keeps those distinctions where the source has them: the three phase states inside the field, and block absence one level up (`projected: false`, with the
 * field ABSENT, so no phase value of any kind exists for a v1 office).
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
import { clone, declareWorkflowPhases, loadFixture } from './helpers/diFixture.js';

type Raw = Record<string, unknown>;
const FRESH: ServeTimeVerdict = { health: { known: true, value: 'fresh' }, problem: null };
const V2_PATH = path.join(__dirname, 'fixtures/di-backend/v2/catalog-answer-v2.fc409bbc.FIXTURE.json');
const V2_SHA256 = 'fc409bbc41a6ee3a17253acebe85092a4d118511954fadf2c457ecc11b891556';
const V2_NOW = 1000000; // the fixture's own generated_at
const V1_NOW = 1800000000;
const WF = 'synthetic_workflow_store:wf-0005'; // emitted lifecycle DISPATCHED
const CORE = 'synthetic_mission_core:mn-00000000000000000000000000000002';

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
  const message = buildDiOfficeStateMessage(buildOfficeView(parsed.envelope, FRESH, V2_NOW, 2), () => 41);
  return Object.fromEntries(message.missions.missions.map((m) => [m.missionRef, m]));
}
/** A v1 office's wire Missions (the contract-v3 canonical fixture at the default, v1, contract version). */
function v1Missions(): DiMission[] {
  const parsed = parseEnvelope(clone(loadFixture()));
  if (!parsed.ok) throw new Error('v1 fixture did not parse');
  return buildDiOfficeStateMessage(buildOfficeView(parsed.envelope, FRESH, V1_NOW), () => 41).missions.missions;
}
const phaseOf = (m: DiMission) => {
  if (!m.v2.projected) throw new Error('expected a projected v2 block');
  return m.v2.workflowPhase!;
};

describe('the declared phase on the wire, state by state (a v2 office)', () => {
  it('a record that declares nothing is not_reported: unknown, never "none" (the historical emission predates the field)', () => {
    for (const m of Object.values(v2Missions())) {
      expect(phaseOf(m), m.missionRef).toEqual({ state: 'not_reported', phase: null, unrecognized: null, problem: null });
    }
  });

  it('declared as the producer emits it: the workflow record OBSERVED with its phase, Mission Core NOT_APPLICABLE with the producer\'s own problem', () => {
    const missions = v2Missions((raw) => declareWorkflowPhases(raw.response as Raw));
    expect(phaseOf(missions[WF]!)).toEqual({ state: 'observed', phase: 'DISPATCHED', unrecognized: null, problem: null });
    expect(phaseOf(missions[CORE]!)).toEqual({ state: 'not_applicable', phase: null, unrecognized: null, problem: 'lifecycle_model_has_no_workflow_phase' });
  });

  it('observed with a phase outside the producer\'s vocabulary: kept as inert text, never mapped to a known phase', () => {
    const missions = v2Missions((raw) => {
      declareWorkflowPhases(raw.response as Raw);
      (missionRaw(raw, WF).workflow_phase as Raw).phase = 'SOMEDAY';
    });
    expect(phaseOf(missions[WF]!)).toEqual({ state: 'observed', phase: null, unrecognized: 'SOMEDAY', problem: null });
  });

  it('an unrecognised value is bounded (a producer cannot make the wire carry unbounded text), and a non-text one carries none', () => {
    const long = v2Missions((raw) => {
      declareWorkflowPhases(raw.response as Raw);
      (missionRaw(raw, WF).workflow_phase as Raw).phase = 'X'.repeat(500);
    });
    expect(phaseOf(long[WF]!)).toMatchObject({ state: 'observed', phase: null, unrecognized: 'X'.repeat(64) });
    const number = v2Missions((raw) => {
      declareWorkflowPhases(raw.response as Raw);
      (missionRaw(raw, WF).workflow_phase as Raw).phase = 7;
    });
    expect(phaseOf(number[WF]!)).toEqual({ state: 'observed', phase: null, unrecognized: null, problem: null });
  });
});

describe('nothing is derived from the lifecycle literal', () => {
  it('a record whose lifecycle says DISPATCHED but which declares NO phase is not_reported, not DISPATCHED', () => {
    const wf = v2Missions()[WF]!;
    expect(wf.lifecycleState).toBe('DISPATCHED');
    expect(phaseOf(wf)).toEqual({ state: 'not_reported', phase: null, unrecognized: null, problem: null });
  });

  it('a declared phase that DISAGREES with the lifecycle literal is relayed as declared', () => {
    const wf = v2Missions((raw) => {
      declareWorkflowPhases(raw.response as Raw);
      (missionRaw(raw, WF).workflow_phase as Raw).phase = 'PLANNED';
    })[WF]!;
    expect(wf.lifecycleState).toBe('DISPATCHED');
    expect(phaseOf(wf).phase).toBe('PLANNED');
  });
});

describe('a v1 office has NO v2 block, and so no phase value of any kind', () => {
  it('projected is false and the field is ABSENT (not null, not a placeholder state): the surface reads the block\'s absence', () => {
    const missions = v1Missions();
    expect(missions.length).toBeGreaterThan(0);
    for (const m of missions) {
      expect(m.v2.projected).toBe(false);
      expect('workflowPhase' in m.v2, m.missionRef).toBe(false);
    }
  });
});

describe('the wire shape', () => {
  it('is closed: exactly these four keys, and "state" is one of the producer\'s three, never null', () => {
    const missions = v2Missions((raw) => declareWorkflowPhases(raw.response as Raw));
    for (const m of Object.values(missions)) {
      const phase = phaseOf(m);
      expect(Object.keys(phase).sort()).toEqual(['phase', 'problem', 'state', 'unrecognized']);
      expect(['observed', 'not_applicable', 'not_reported']).toContain(phase.state);
    }
  });

  it('observed appears only on a workflow_authority record (the producer\'s own rule), and not_applicable never carries a phase', () => {
    const missions = v2Missions((raw) => declareWorkflowPhases(raw.response as Raw));
    for (const m of Object.values(missions)) {
      const phase = phaseOf(m);
      if (phase.state === 'observed') expect(m.lifecycleModel).toBe('workflow_authority.workflow_phase/2');
      if (phase.state === 'not_applicable') expect(phase.phase).toBeNull();
    }
  });
});
