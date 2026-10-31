/**
 * The record-link prerequisite, office side: the producer's DECLARED per-Mission delivery link (`missions[].delivery_link`,
 * mission_sources/contract_v2.py) is validated strictly when present, carried into the office view as `deliveryLink`, and sent on
 * the wire as `DiMissionV2.deliveryLink`. When the catalog's answer does not carry the field, NOTHING is projected: the key is
 * omitted at every layer, never defaulted, so an answer that predates it is read exactly as before.
 *
 * Over the hash-pinned contract-v2 emission (`catalog-answer-v2.fc409bbc.FIXTURE.json`, SYNTHETIC ids, which predates the
 * field), edited per case by this file's own `declareLinks` to the shape the current producer emits: the workflow Mission gets the
 * case's link, and every other model gets `unlinked_model_not_recorded`. Each case runs through the real projector, the real
 * office view and the real message builder. A v1 office is the contract-v3 canonical fixture at the default (v1) contract version.
 *
 * The rule this holds the office to (the producer's own): only a workflow_authority Mission can be linked; Mission Core and every
 * other model is `unlinked_model_not_recorded` (not known); a linked Mission carries ONE id of the ceremony's shape and the
 * declared basis; an unlinked one carries neither. A link authorizes nothing.
 */
import * as crypto from 'crypto';
import * as fs from 'fs';
import * as path from 'path';
import { describe, expect, it } from 'vitest';

import type { DiMission, DiMissionV2 } from '../../core/src/messages.js';
import {
  MISSION_DELIVERY_ID,
  MISSION_DELIVERY_LINK_BASES,
  MISSION_DELIVERY_LINK_KEYS,
  MISSION_DELIVERY_LINK_STATES,
} from '../src/di/catalogContract.js';
import { parseEnvelope, projectResponse } from '../src/di/catalogProjection.js';
import { buildDiOfficeStateMessage } from '../src/di/officeStateMessage.js';
import { buildOfficeView } from '../src/di/officeView.js';
import type { ServeTimeVerdict } from '../src/di/serveTimeVerdict.js';
import { clone, loadFixture } from './helpers/diFixture.js';

type Raw = Record<string, unknown>;
const FRESH: ServeTimeVerdict = { health: { known: true, value: 'fresh' }, problem: null };
const V2_PATH = path.join(__dirname, 'fixtures/di-backend/v2/catalog-answer-v2.fc409bbc.FIXTURE.json');
const V2_SHA256 = 'fc409bbc41a6ee3a17253acebe85092a4d118511954fadf2c457ecc11b891556';
const V2_NOW = 1000000; // the fixture's own generated_at
const V1_NOW = 1800000000;
const WF = 'synthetic_workflow_store:wf-0005';
const CORE = 'synthetic_mission_core:mn-00000000000000000000000000000002';
const CORE_OTHER = 'synthetic_mission_core:mn-00000000000000000000000000000009';
/** Every Mission of the fixture, sorted: what a per-Mission loop must iterate over. */
const ALL = [CORE, CORE_OTHER, WF].sort();
const WORKFLOW_MODEL = 'workflow_authority.workflow_phase/2';
const ID = 'prd-' + '0a'.repeat(12);

const NOT_RECORDED = { state: 'unlinked_model_not_recorded', delivery_id: null, basis: null };
const linkedRaw = (id: string) => ({ state: 'linked', delivery_id: id, basis: 'record_mission_binding' });
const unlinkedRaw = (state: string) => ({ state, delivery_id: null, basis: null });

function loadV2(): Raw {
  const bytes = fs.readFileSync(V2_PATH);
  const digest = crypto.createHash('sha256').update(bytes).digest('hex');
  if (digest !== V2_SHA256) throw new Error(`v2 FIXTURE hash mismatch: ${digest}`);
  return JSON.parse(bytes.toString('utf-8')) as Raw;
}
const missionsOf = (raw: Raw) => (raw.response as Raw).missions as Raw[];
const missionRaw = (raw: Raw, ref: string): Raw => {
  const found = missionsOf(raw).find((m) => m.mission_ref === ref);
  if (!found) throw new Error(`no ${ref}`);
  return found;
};

/** The producer's emission shape: the workflow Mission gets `workflowLink`, every other model is not recorded. */
function declareLinks(raw: Raw, workflowLink: unknown): void {
  for (const m of missionsOf(raw)) {
    const model = (m.lifecycle as { model: string }).model;
    m.delivery_link = clone(model === WORKFLOW_MODEL ? workflowLink : NOT_RECORDED);
  }
}

/** The v2 projection of the (edited) answer: the parsed Mission views by ref, or the refusal. */
function projected(edit?: (raw: Raw) => void) {
  const raw = clone(loadV2());
  edit?.(raw);
  return projectResponse(raw.response as Raw, 2);
}
function views(edit?: (raw: Raw) => void) {
  const result = projected(edit);
  if (!result.ok) throw new Error(`did not project: ${result.verdict.detail}`);
  return Object.fromEntries(result.view.missions.map((m) => [m.missionRef, m]));
}
/** The v2 office's wire Missions by ref. */
function wire(edit?: (raw: Raw) => void): Record<string, DiMission> {
  const raw = clone(loadV2());
  edit?.(raw);
  const parsed = parseEnvelope(raw);
  if (!parsed.ok) throw new Error(`did not parse: ${parsed.verdict.detail}`);
  const message = buildDiOfficeStateMessage(buildOfficeView(parsed.envelope, FRESH, V2_NOW, 2), () => 41);
  return Object.fromEntries(message.missions.missions.map((m) => [m.missionRef, m]));
}
function v1Wire(): DiMission[] {
  const parsed = parseEnvelope(clone(loadFixture()));
  if (!parsed.ok) throw new Error('v1 fixture did not parse');
  return buildDiOfficeStateMessage(buildOfficeView(parsed.envelope, FRESH, V1_NOW), () => 41).missions.missions;
}
const v2Of = (m: DiMission): DiMissionV2 => {
  if (!m.v2.projected) throw new Error('expected a projected v2 block');
  return m.v2;
};

describe('the closed vocabulary is the producer\'s, by exact value', () => {
  it('keys, states, basis and the delivery id shape equal mission_sources/contract_v2.py\'s literals', () => {
    expect([...MISSION_DELIVERY_LINK_KEYS]).toEqual(['state', 'delivery_id', 'basis']);
    expect([...MISSION_DELIVERY_LINK_STATES]).toEqual([
      'linked',
      'unlinked_model_not_recorded',
      'unlinked_store_not_registered',
      'unlinked_store_unavailable',
      'unlinked_reader_failed',
      'unlinked_no_record',
      'unlinked_ambiguous',
      'unlinked_sources_incomplete',
      'unlinked_id_not_addressable',
      'unlinked_repository_not_recorded',
      'unlinked_task_worktree_not_recorded',
    ]);
    // the two origin-only values are LEGACY (an older producer); the task-and-worktree values are distinct and last
    expect([...MISSION_DELIVERY_LINK_BASES]).toEqual([
      'record_mission_binding',
      'record_repository_binding',
      'record_task_worktree_binding',
    ]);
    expect(MISSION_DELIVERY_ID.test(ID)).toBe(true);
    for (const bad of ['prd-' + 'A'.repeat(24), 'prd-' + '0'.repeat(23), 'prd-' + '0'.repeat(25), ID + '\n', 'legacy-0001']) {
      expect(MISSION_DELIVERY_ID.test(bad), bad).toBe(false);
    }
  });
});

describe('an answer that predates the field projects NOTHING: the key is omitted, never defaulted', () => {
  it('no Mission view and no wire Mission carries deliveryLink', () => {
    // the Missions iterated are asserted first: a loop over nothing asserts nothing (round 59)
    const v = views();
    expect(Object.keys(v).sort()).toEqual(ALL);
    for (const [ref, m] of Object.entries(v)) {
      expect(m.v2, ref).not.toBeNull();
      expect('deliveryLink' in m.v2!, ref).toBe(false);
    }
    const w = wire();
    expect(Object.keys(w).sort()).toEqual(ALL);
    for (const m of Object.values(w)) expect('deliveryLink' in v2Of(m), m.missionRef).toBe(false);
  });

  it('a v1 office has no v2 block, so no link of any kind', () => {
    const missions = v1Wire();
    expect(missions.length).toBeGreaterThan(0);
    for (const m of missions) {
      expect(m.v2.projected).toBe(false);
      expect('deliveryLink' in m.v2, m.missionRef).toBe(false);
    }
  });
});

describe('as the producer declares it, in the view and on the wire', () => {
  it('linked: the workflow Mission carries its ONE id and the basis; every Mission Core Mission is not known', () => {
    const edit = (raw: Raw) => declareLinks(raw, linkedRaw(ID));
    const v = views(edit);
    expect(v[WF]!.v2!.deliveryLink).toEqual({ state: 'linked', deliveryId: ID, basis: 'record_mission_binding' });
    for (const ref of [CORE, CORE_OTHER]) {
      expect(v[ref]!.v2!.deliveryLink).toEqual({ state: 'unlinked_model_not_recorded', deliveryId: null, basis: null });
    }
    const w = wire(edit);
    expect(v2Of(w[WF]!).deliveryLink).toEqual({ state: 'linked', deliveryId: ID, basis: 'record_mission_binding' });
    for (const ref of [CORE, CORE_OTHER]) {
      expect(v2Of(w[ref]!).deliveryLink).toEqual({ state: 'unlinked_model_not_recorded', deliveryId: null, basis: null });
    }
  });

  it('every unlinked workflow state is relayed as declared, with no id and no basis', () => {
    expect(MISSION_DELIVERY_LINK_STATES.length).toBe(11);
    for (const state of MISSION_DELIVERY_LINK_STATES) {
      if (
        state === 'linked' ||
        state === 'unlinked_model_not_recorded' ||
        state === 'unlinked_repository_not_recorded' ||
        state === 'unlinked_task_worktree_not_recorded'
      ) {
        continue;
      }
      const w = wire((raw) => declareLinks(raw, unlinkedRaw(state)));
      expect(v2Of(w[WF]!).deliveryLink, state).toEqual({ state, deliveryId: null, basis: null });
    }
  });

  it('the wire block is closed: exactly state, deliveryId and basis', () => {
    const w = wire((raw) => declareLinks(raw, linkedRaw(ID)));
    expect(Object.keys(w).sort()).toEqual(ALL);
    for (const m of Object.values(w)) {
      expect(v2Of(m).deliveryLink, m.missionRef).toBeTypeOf('object');
      expect(Object.keys(v2Of(m).deliveryLink as object).sort(), m.missionRef).toEqual(['basis', 'deliveryId', 'state']);
    }
  });
});

describe('STRICT when present: the projector refuses what the producer\'s rule forbids', () => {
  const refusals: Array<[string, (raw: Raw) => void]> = [
    ['linked without an id', (raw) => declareLinks(raw, { state: 'linked', delivery_id: null, basis: 'record_mission_binding' })],
    ['linked with an id outside the ceremony shape', (raw) => declareLinks(raw, linkedRaw('legacy-0001'))],
    ['linked with a list of ids', (raw) => declareLinks(raw, { state: 'linked', delivery_id: [ID], basis: 'record_mission_binding' })],
    ['linked without a basis', (raw) => declareLinks(raw, { state: 'linked', delivery_id: ID, basis: null })],
    ['linked on an engineering task id', (raw) => declareLinks(raw, { state: 'linked', delivery_id: ID, basis: 'engineering_task_id' })],
    ['unlinked with an id', (raw) => declareLinks(raw, { state: 'unlinked_no_record', delivery_id: ID, basis: null })],
    ['unlinked with a basis', (raw) => declareLinks(raw, { state: 'unlinked_no_record', delivery_id: null, basis: 'record_mission_binding' })],
    ['an undeclared state', (raw) => declareLinks(raw, unlinkedRaw('unlinked_newest'))],
    ['an extra key', (raw) => declareLinks(raw, { ...linkedRaw(ID), count: 1 })],
    ['a missing key', (raw) => declareLinks(raw, { state: 'unlinked_no_record', delivery_id: null })],
    ['not an object', (raw) => declareLinks(raw, 'linked')],
    ['a workflow Mission said to be of an unrecorded model', (raw) => declareLinks(raw, NOT_RECORDED)],
    [
      'a workflow Mission linked by repository',
      (raw) => declareLinks(raw, { state: 'linked', delivery_id: ID, basis: 'record_repository_binding' }),
    ],
    [
      'a workflow Mission linked by task and worktree',
      (raw) => declareLinks(raw, { state: 'linked', delivery_id: ID, basis: 'record_task_worktree_binding' }),
    ],
    [
      'a workflow Mission whose origin was not read',
      (raw) => declareLinks(raw, unlinkedRaw('unlinked_repository_not_recorded')),
    ],
    [
      'a workflow Mission whose task or worktree was not read',
      (raw) => declareLinks(raw, unlinkedRaw('unlinked_task_worktree_not_recorded')),
    ],
    [
      'a basis this office does not know (fail closed: never read as the stronger reading)',
      (raw) => declareLinks(raw, { state: 'linked', delivery_id: ID, basis: 'record_canonical_worktree_binding' }),
    ],
    [
      'a Mission Core Mission linked',
      (raw) => {
        declareLinks(raw, unlinkedRaw('unlinked_no_record'));
        missionRaw(raw, CORE).delivery_link = linkedRaw(ID);
      },
    ],
    [
      'a Mission Core Mission given a workflow reason',
      (raw) => {
        declareLinks(raw, unlinkedRaw('unlinked_no_record'));
        missionRaw(raw, CORE).delivery_link = unlinkedRaw('unlinked_no_record');
      },
    ],
  ];

  it('the rule\'s own shapes project (the positive control)', () => {
    expect(projected((raw) => declareLinks(raw, linkedRaw(ID))).ok).toBe(true);
    expect(projected((raw) => declareLinks(raw, unlinkedRaw('unlinked_ambiguous'))).ok).toBe(true);
  });

  // THE CROSS-VERSION BOUNDARY. A current producer links a Herdr task by `record_task_worktree_binding`; an OLDER producer by the
  // origin alone (`record_repository_binding`). Both project, each as ITSELF: the projection must never turn the older, weaker basis
  // into the newer one (nor the reverse), because the wording shown to the person is chosen by the basis.
  it.each(['record_task_worktree_binding', 'record_repository_binding'] as const)(
    'a Herdr task linked by %s projects exactly that basis, never the other one',
    (basis) => {
      const v = views((raw) => {
        const mission = missionRaw(raw, CORE);
        (mission.lifecycle as { model: string }).model = 'herdr.task/1';
        mission.delivery_link = { state: 'linked', delivery_id: ID, basis };
        missionRaw(raw, CORE_OTHER).delivery_link = NOT_RECORDED;
        missionRaw(raw, WF).delivery_link = linkedRaw(ID);
      });
      expect(v[CORE]!.v2!.deliveryLink).toEqual({ state: 'linked', deliveryId: ID, basis });
    },
  );

  it.each(['unlinked_task_worktree_not_recorded', 'unlinked_repository_not_recorded'] as const)(
    'a Herdr task whose %s projects that state as declared, with no id and no basis',
    (state) => {
      const v = views((raw) => {
        const mission = missionRaw(raw, CORE);
        (mission.lifecycle as { model: string }).model = 'herdr.task/1';
        mission.delivery_link = unlinkedRaw(state);
        missionRaw(raw, CORE_OTHER).delivery_link = NOT_RECORDED;
        missionRaw(raw, WF).delivery_link = linkedRaw(ID);
      });
      expect(v[CORE]!.v2!.deliveryLink).toEqual({ state, deliveryId: null, basis: null });
    },
  );

  for (const [name, edit] of refusals) {
    it(`refuses: ${name}`, () => {
      const result = projected(edit);
      expect(result.ok, name).toBe(false);
      if (!result.ok) expect(result.verdict.detail, name).toMatch(/delivery_link/);
    });
  }
});

describe('the wire declaration (core/asyncapi.yaml)', () => {
  it('DiMissionV2.deliveryLink is OPTIONAL and closed, with the producer\'s states, its basis and the ceremony id shape', async () => {
    const { Parser } = await import('@asyncapi/parser');
    const spec = fs.readFileSync(path.join(__dirname, '../../core/asyncapi.yaml'), 'utf-8');
    const { document } = await new Parser().parse(spec);
    const schemas = document!.components().schemas();
    const v2 = schemas.get('DiMissionV2')!.json() as { required: string[]; properties: Record<string, unknown> };
    expect(Object.keys(v2.properties)).toContain('deliveryLink');
    expect(v2.required).not.toContain('deliveryLink');
    const link = schemas.get('DiMissionDeliveryLink')!.json() as {
      additionalProperties: boolean;
      required: string[];
      properties: Record<string, { pattern?: string }>;
      description: string;
    };
    expect(link.additionalProperties).toBe(false);
    expect([...link.required].sort()).toEqual(['basis', 'deliveryId', 'state']);
    expect(link.properties.deliveryId!.pattern).toBe('^prd-[0-9a-f]{24}$');
    expect((schemas.get('DiMissionDeliveryLinkState')!.json() as { enum: string[] }).enum).toEqual([
      ...MISSION_DELIVERY_LINK_STATES,
    ]);
    expect((schemas.get('DiMissionDeliveryLinkBasis')!.json() as { enum: string[] }).enum).toEqual([
      ...MISSION_DELIVERY_LINK_BASES,
    ]);
    expect(link.description).toMatch(/authorizes nothing/);
    expect(link.description).toMatch(/not known/);
  });
});
