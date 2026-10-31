/**
 * The delivery HANDOFF, office side: the producer's DECLARED read-only facts for the existing human delivery ceremony
 * (`missions[].delivery_handoff`, mission_sources/contract_v2.py) are validated strictly when present, carried into the office view as
 * `deliveryHandoff`, and sent on the wire as `DiMissionV2.deliveryHandoff`. When the catalog's answer does not carry the block, NOTHING is
 * projected at any layer (an older producer): its absence is never a default and never a declaration of anything.
 *
 * What this holds the office to:
 *  - the block CONFERS NOTHING: no "ready" field exists on the wire; the staged candidate is always `not_determined` and the workflow id
 *    always `human_supplied`; the blockers are exactly what the facts imply (a producer cannot send fewer);
 *  - `linkBasis` is carried EXACTLY as the producer wrote it and is NOT interpreted here: a legacy or unrecognised value reaches the client
 *    unchanged, so the client can refuse to read it as the canonical declaration instead of having been handed a guess;
 *  - the fresh-task case: a task with NO delivery record (`unlinked_no_record`) carries the declaration, so the return path can be told
 *    before any record exists.
 *
 * Over the hash-pinned contract-v2 emission (SYNTHETIC ids), edited per case by this file's own `declareHandoff`; each case runs through the
 * real projector, the real office view and the real message builder.
 */
import * as crypto from 'crypto';
import * as fs from 'fs';
import * as path from 'path';
import { describe, expect, it } from 'vitest';

import type { DiMission, DiMissionV2 } from '../../core/src/messages.js';
import {
  HANDOFF_BLOCKERS,
  HANDOFF_CANONICAL_LINK_BASIS,
  HANDOFF_MAX_REASON_CHARS,
  MISSION_DELIVERY_HANDOFF_KEYS,
  MISSION_HANDOFF_STATES,
} from '../src/di/catalogContract.js';
import { parseEnvelope, projectResponse } from '../src/di/catalogProjection.js';
import { buildDiOfficeStateMessage } from '../src/di/officeStateMessage.js';
import { buildOfficeView } from '../src/di/officeView.js';
import type { ServeTimeVerdict } from '../src/di/serveTimeVerdict.js';
import { clone } from './helpers/diFixture.js';

type Raw = Record<string, unknown>;
const FRESH: ServeTimeVerdict = { health: { known: true, value: 'fresh' }, problem: null };
const V2_PATH = path.join(__dirname, 'fixtures/di-backend/v2/catalog-answer-v2.fc409bbc.FIXTURE.json');
const V2_SHA256 = 'fc409bbc41a6ee3a17253acebe85092a4d118511954fadf2c457ecc11b891556';
const V2_NOW = 1000000;
const WF = 'synthetic_workflow_store:wf-0005';
const CORE = 'synthetic_mission_core:mn-00000000000000000000000000000002';
const CORE_OTHER = 'synthetic_mission_core:mn-00000000000000000000000000000009';
const HERDR_MODEL = 'herdr.task/1';
const NONE = { state: 'not_applicable', link_basis: null, repository: null, engineering_task_id: null, git_dir_realpath: null, branch: null, evidence: null, staged_candidate: null, workflow_id: null, blockers: [] };

const observed = (over: Raw = {}): Raw => ({
  state: 'observed',
  link_basis: 'record_task_worktree_binding',
  repository: '/work/wt-a',
  engineering_task_id: 'task-a',
  git_dir_realpath: '/main/.git/worktrees/wt-a',
  branch: { state: 'named', ref: 'refs/heads/feature/p1' },
  evidence: { state: 'collected', reason: null },
  staged_candidate: 'not_determined',
  workflow_id: 'human_supplied',
  blockers: [],
  ...over,
});

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

/** CORE is made the Herdr task and given `block`; every other model carries a not_applicable block (the producer's emission shape). */
function declareHandoff(raw: Raw, block: unknown): void {
  for (const m of missionsOf(raw)) m.delivery_handoff = clone(NONE);
  const mission = missionRaw(raw, CORE);
  (mission.lifecycle as { model: string }).model = HERDR_MODEL;
  mission.delivery_handoff = clone(block);
}
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
function wire(edit?: (raw: Raw) => void): Record<string, DiMission> {
  const raw = clone(loadV2());
  edit?.(raw);
  const parsed = parseEnvelope(raw);
  if (!parsed.ok) throw new Error(`did not parse: ${parsed.verdict.detail}`);
  const message = buildDiOfficeStateMessage(buildOfficeView(parsed.envelope, FRESH, V2_NOW, 2), () => 41);
  return Object.fromEntries(message.missions.missions.map((m) => [m.missionRef, m]));
}
const v2Of = (m: DiMission): DiMissionV2 => {
  if (!m.v2.projected) throw new Error('expected a projected v2 block');
  return m.v2;
};

describe('the closed vocabulary is the producer\'s, by exact value', () => {
  it('keys, states, blockers and the canonical declaration equal mission_sources/contract_v2.py\'s literals', () => {
    expect([...MISSION_DELIVERY_HANDOFF_KEYS]).toEqual([
      'state', 'link_basis', 'repository', 'engineering_task_id', 'git_dir_realpath', 'branch', 'evidence', 'staged_candidate', 'workflow_id', 'blockers',
    ]);
    expect([...MISSION_HANDOFF_STATES]).toEqual(['observed', 'not_applicable', 'not_reported']);
    expect([...HANDOFF_BLOCKERS]).toEqual(['git_dir_not_read', 'head_detached', 'head_not_read', 'evidence_not_collected', 'task_id_not_recorded']);
    expect(HANDOFF_MAX_REASON_CHARS).toBe(400);
    expect(HANDOFF_CANONICAL_LINK_BASIS).toBe('record_task_worktree_binding');
  });
});

describe('ABSENT: an answer that predates the block projects NOTHING, at every layer', () => {
  it('no deliveryHandoff on the view or on the wire; nothing is defaulted', () => {
    for (const m of Object.values(views())) expect(m.v2, m.missionRef).not.toHaveProperty('deliveryHandoff');
    for (const m of Object.values(wire())) expect(v2Of(m), m.missionRef).not.toHaveProperty('deliveryHandoff');
  });
});

describe('PRESENT: carried exactly, once per layer, and conferring nothing', () => {
  it('an observed block projects field for field and reaches the wire unchanged', () => {
    const v = views((raw) => declareHandoff(raw, observed()));
    expect(v[CORE]!.v2!.deliveryHandoff).toEqual({
      state: 'observed',
      linkBasis: 'record_task_worktree_binding',
      repository: '/work/wt-a',
      engineeringTaskId: 'task-a',
      gitDirRealpath: '/main/.git/worktrees/wt-a',
      branch: { state: 'named', ref: 'refs/heads/feature/p1' },
      evidence: { state: 'collected', reason: null },
      stagedCandidate: 'not_determined',
      workflowId: 'human_supplied',
      blockers: [],
    });
    const w = wire((raw) => declareHandoff(raw, observed()));
    expect(v2Of(w[CORE]!).deliveryHandoff).toEqual(v[CORE]!.v2!.deliveryHandoff);
    for (const ref of [WF, CORE_OTHER]) {
      expect(v2Of(w[ref]!).deliveryHandoff, ref).toEqual({
        state: 'not_applicable', linkBasis: null, repository: null, engineeringTaskId: null, gitDirRealpath: null,
        branch: null, evidence: null, stagedCandidate: null, workflowId: null, blockers: [],
      });
    }
  });

  it('the wire block has NO field that could be read as permission: no ready, mint, authorized or allowed', () => {
    const w = wire((raw) => declareHandoff(raw, observed()));
    const keys = Object.keys(v2Of(w[CORE]!).deliveryHandoff as object);
    expect(keys.sort()).toEqual(
      ['blockers', 'branch', 'engineeringTaskId', 'evidence', 'gitDirRealpath', 'linkBasis', 'repository', 'stagedCandidate', 'state', 'workflowId'],
    );
    for (const key of keys) expect(key).not.toMatch(/ready|mint|authori|permit|allowed|can[A-Z]/);
  });

  it('the shape of THIS mission\'s situation, built from a fixture\'s own state: a detached head and refused evidence, with the collector\'s reason verbatim', () => {
    const reason = "task 20261030-144734-e1b5c9 status is 'ACTIVE', not COMPLETE";
    const block = observed({
      branch: { state: 'detached', ref: null },
      evidence: { state: 'refused', reason },
      blockers: ['head_detached', 'evidence_not_collected'],
    });
    const w = wire((raw) => declareHandoff(raw, block));
    const out = v2Of(w[CORE]!).deliveryHandoff!;
    expect(out.blockers).toEqual(['head_detached', 'evidence_not_collected']);
    expect(out.evidence).toEqual({ state: 'refused', reason });
    expect(out.stagedCandidate).toBe('not_determined');
  });

  it('a FRESH task with no record: the declaration travels with an unlinked link, so the return path can be told before any record exists', () => {
    const v = views((raw) => {
      declareHandoff(raw, observed());
      missionRaw(raw, CORE).delivery_link = { state: 'unlinked_no_record', delivery_id: null, basis: null };
      missionRaw(raw, CORE_OTHER).delivery_link = { state: 'unlinked_model_not_recorded', delivery_id: null, basis: null };
      missionRaw(raw, WF).delivery_link = { state: 'unlinked_no_record', delivery_id: null, basis: null };
    });
    expect(v[CORE]!.v2!.deliveryLink).toEqual({ state: 'unlinked_no_record', deliveryId: null, basis: null });
    expect(v[CORE]!.v2!.deliveryHandoff!.linkBasis).toBe('record_task_worktree_binding');
  });

  it('linkBasis is carried EXACTLY as written and never interpreted: a legacy or unrecognised value reaches the client unchanged', () => {
    for (const declared of ['record_repository_binding', 'record_something_new', 'x'.repeat(80)]) {
      const w = wire((raw) => declareHandoff(raw, observed({ link_basis: declared })));
      expect(v2Of(w[CORE]!).deliveryHandoff!.linkBasis, declared).toBe(declared);
    }
    // silence on a present block is carried as silence, not filled in
    const w = wire((raw) => declareHandoff(raw, observed({ link_basis: null })));
    expect(v2Of(w[CORE]!).deliveryHandoff!.linkBasis).toBeNull();
  });
});

describe('STRICT when present: the projector refuses what the producer\'s rule forbids', () => {
  const refusals: Array<[string, (raw: Raw) => void]> = [
    ['an extra key', (raw) => declareHandoff(raw, observed({ ready: true }))],
    ['a missing key', (raw) => declareHandoff(raw, (({ blockers: _b, ...rest }) => rest)(observed()))],
    ['a staged candidate said to be satisfied', (raw) => declareHandoff(raw, observed({ staged_candidate: 'staged' }))],
    ['a workflow id guessed', (raw) => declareHandoff(raw, observed({ workflow_id: 'wf-0001' }))],
    ['FEWER blockers than a detached head implies', (raw) => declareHandoff(raw, observed({ branch: { state: 'detached', ref: null }, blockers: [] }))],
    ['blockers the facts do not imply', (raw) => declareHandoff(raw, observed({ blockers: ['head_detached'] }))],
    ['an undeclared blocker', (raw) => declareHandoff(raw, observed({ blockers: ['everything_is_fine'] }))],
    ['a named branch with no ref', (raw) => declareHandoff(raw, observed({ branch: { state: 'named', ref: null } }))],
    ['a ref that is not a branch', (raw) => declareHandoff(raw, observed({ branch: { state: 'named', ref: 'refs/tags/v1' } }))],
    ['collected evidence with a reason', (raw) => declareHandoff(raw, observed({ evidence: { state: 'collected', reason: 'ok' } }))],
    ['refused evidence with no reason', (raw) => declareHandoff(raw, observed({ evidence: { state: 'refused', reason: null } }))],
    ['a reason over its bound', (raw) => declareHandoff(raw, observed({ evidence: { state: 'refused', reason: 'r'.repeat(401) }, blockers: ['evidence_not_collected'] }))],
    ['an empty link basis', (raw) => declareHandoff(raw, observed({ link_basis: '' }))],
    ['a link basis over its bound', (raw) => declareHandoff(raw, observed({ link_basis: 'b'.repeat(81) }))],
    ['an undeclared state', (raw) => declareHandoff(raw, observed({ state: 'ready' }))],
    ['a not_reported block that carries facts', (raw) => declareHandoff(raw, observed({ state: 'not_reported' }))],
    ['a Herdr task said to be not_applicable', (raw) => declareHandoff(raw, NONE)],
    [
      'a model without a handoff given an observed block',
      (raw) => {
        declareHandoff(raw, observed());
        missionRaw(raw, WF).delivery_handoff = observed();
      },
    ],
    ['not an object', (raw) => declareHandoff(raw, 'observed')],
  ];

  it('the producer\'s own shapes project (the positive control)', () => {
    expect(projected((raw) => declareHandoff(raw, observed())).ok).toBe(true);
    expect(projected((raw) => declareHandoff(raw, { ...NONE, state: 'not_reported' })).ok).toBe(true);
  });

  for (const [name, edit] of refusals) {
    it(`refuses: ${name}`, () => {
      const result = projected(edit);
      expect(result.ok, name).toBe(false);
      if (!result.ok) expect(result.verdict.detail, name).toMatch(/delivery_handoff/);
    });
  }
});
