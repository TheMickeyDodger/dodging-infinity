/**
 * The characters of a DI office state in the two orders the three-office allocation needs (components/diPlacementModel.ts): the entitlement order is
 * `diSeatingOrder` unchanged; the placement order is team by team (one Mission = one team), the lead(s) first then the members by stable identity key,
 * then each runtime agent alone. Identity is the canonical tuple key, never the process-local character number.
 */
import assert from 'node:assert/strict';

import { test } from 'vitest';

import type { DiOfficeState } from '../../core/src/messages.ts';
import { diIdentityKey, diRuntimeIdentityKey } from '../src/components/diOfficeFormat.ts';
import { diPlacementModel } from '../src/components/diPlacementModel.ts';
import { diSeatingOrder } from '../src/office/layout/diSeating.ts';

interface M {
  characterId: number | null;
  memberRef: string;
  role: string;
  coverage?: string;
  taskId?: string;
}
const member = (m: M) => ({
  characterId: m.characterId,
  coverage: m.coverage ?? 'direct',
  memberRef: m.memberRef,
  role: m.role,
  taskId: { state: 'observed', value: m.taskId ?? 'task-1' },
});
const mission = (missionRef: string, members: M[], sourceId = 'src') => ({
  sourceId,
  missionRef,
  members: { members: members.map(member) },
});
const state = (
  missions: ReturnType<typeof mission>[],
  runtime: Array<{ characterId: number | null; agentRef: string; rootRef: string; sourceId: string }> = [],
) =>
  ({ missions: { missions }, runtimeAgents: { agents: runtime } }) as unknown as Pick<DiOfficeState, 'missions' | 'runtimeAgents'>;

test('placement order: teams in Mission order; within a team the lead first, then members by identity key; then each runtime agent alone', () => {
  const s = state(
    [
      mission('mn-A', [
        { characterId: 3, memberRef: 'zeta', role: 'executor' },
        { characterId: 1, memberRef: 'alpha', role: 'reviewer' },
        { characterId: 2, memberRef: 'lead1', role: 'lead' },
      ]),
      mission('mn-B', [
        { characterId: 5, memberRef: 'b2', role: 'executor' },
        { characterId: 4, memberRef: 'b1', role: 'lead' },
      ]),
    ],
    [{ characterId: 9, agentRef: 'r9', rootRef: 'root', sourceId: 'src' }, { characterId: 8, agentRef: 'r8', rootRef: 'root', sourceId: 'src' }],
  );
  const m = diPlacementModel(s);
  // team A: lead (2) first, then alpha (1) before zeta (3) by identity key; team B: lead (4) then 5; runtime agents in office-state order 9, 8
  assert.deepEqual(m.placementOrder, [2, 1, 3, 4, 5, 9, 8]);
  assert.deepEqual(
    m.entitlement.map((c) => c.id),
    diSeatingOrder(s),
    'the entitlement order is diSeatingOrder, unchanged',
  );
  assert.deepEqual(diSeatingOrder(s), [3, 1, 2, 5, 4, 9, 8]);
});

test('teams: a Mission is one team; each runtime agent is its own team of one; only members of a Mission can be a lead', () => {
  const s = state(
    [mission('mn-A', [{ characterId: 1, memberRef: 'l', role: 'lead' }, { characterId: 2, memberRef: 'm', role: 'executor' }])],
    [{ characterId: 3, agentRef: 'a', rootRef: 'root', sourceId: 'src' }, { characterId: 4, agentRef: 'b', rootRef: 'root', sourceId: 'src' }],
  );
  const { entitlement } = diPlacementModel(s);
  const byId = new Map(entitlement.map((c) => [c.id, c]));
  assert.equal(byId.get(1)!.teamKey, byId.get(2)!.teamKey);
  assert.notEqual(byId.get(3)!.teamKey, byId.get(4)!.teamKey);
  assert.notEqual(byId.get(3)!.teamKey, byId.get(1)!.teamKey);
  assert.deepEqual([...entitlement].map((c) => c.isLead), [true, false, false, false]);
});

test('identity is the canonical key, never the number: the same members under other numbers give the same keys', () => {
  const a = diPlacementModel(
    state([mission('mn-A', [{ characterId: 1, memberRef: 'l', role: 'lead' }, { characterId: 2, memberRef: 'm', role: 'executor' }])]),
  );
  const b = diPlacementModel(
    state([mission('mn-A', [{ characterId: 41, memberRef: 'l', role: 'lead' }, { characterId: 17, memberRef: 'm', role: 'executor' }])]),
  );
  assert.deepEqual([...a.keys.values()].sort(), [...b.keys.values()].sort());
  const lead = { sourceId: 'src', missionRef: 'mn-A' };
  assert.equal(
    a.keys.get(1),
    diIdentityKey(lead, { coverage: 'direct', memberRef: 'l', taskId: { state: 'observed', value: 'task-1' } } as never),
  );
  const rt = diPlacementModel(state([], [{ characterId: 5, agentRef: 'a', rootRef: 'root', sourceId: 'src' }]));
  assert.equal(rt.keys.get(5), diRuntimeIdentityKey({ sourceId: 'src', rootRef: 'root', agentRef: 'a' }));
});

test('characters with no number yet are not placed; a number appearing twice counts once', () => {
  const m = diPlacementModel(
    state([
      mission('mn-A', [
        { characterId: null, memberRef: 'gone', role: 'lead' },
        { characterId: 1, memberRef: 'a', role: 'executor' },
      ]),
      mission('mn-B', [{ characterId: 1, memberRef: 'dup', role: 'executor' }]),
    ]),
  );
  assert.deepEqual(m.placementOrder, [1]);
  assert.deepEqual(m.entitlement.map((c) => c.id), [1]);
});
