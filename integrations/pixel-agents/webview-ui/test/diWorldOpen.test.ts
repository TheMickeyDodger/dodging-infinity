/**
 * AC-4.4-26 (pass C): clicking an agent or its Mission label opens THAT Mission. Which Mission a drawn character opens,
 * on the REAL server projection `di-office-state-transitions-v2.FIXTURE.json` (live), where TWO Missions have drawn
 * characters, so a resolver that answered "the selected row" or "the first Mission" could not pass:
 *  - a team member opens its own Mission;
 *  - a sub-agent opens its PARENT's Mission;
 *  - a runtime agent opens nothing: its contract never assigns it to a Mission (RUNTIME_ASSIGNMENT_STATES is only
 *    `unassigned` and `unverified_reference`), so its root is where it runs, not a Mission it works on;
 *  - an unknown character opens nothing.
 * The real clicks in the App are proven in diScanFace.test.ts.
 */
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

import { describe, expect, it } from 'vitest';

import type { DiOfficeState } from '../../core/src/messages.ts';
import { diMissionRefForCharacter } from '../src/components/diOfficeFormat.ts';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const LIVE = (
  JSON.parse(
    fs.readFileSync(path.join(root, 'test/fixtures/di-office-state-transitions-v2.FIXTURE.json'), 'utf-8'),
  ) as { states: Record<'live', DiOfficeState> }
).states.live;
const HERD = 'herd:20261017-150121-aaaaaa@5a4574665441';
const CORE = 'mc:mn-1aa604085c36ca55d01b57634ee13f41';
const noParent = () => null;

describe('AC-4.4-26: the Mission a drawn character opens (real projection, two Missions drawn)', () => {
  it('the fixture really draws two Missions, so the binding is discriminated', () => {
    const drawn = LIVE.missions.missions
      .filter((m) => m.members.members.some((x) => x.characterId !== null))
      .map((m) => m.missionRef);
    expect(drawn).toEqual([HERD, CORE]);
  });
  it('each team member opens ITS OWN Mission (characters 1 to 4 one Mission, 5 to 8 the other)', () => {
    expect([1, 2, 3, 4].map((id) => diMissionRefForCharacter(LIVE, id, noParent))).toEqual([HERD, HERD, HERD, HERD]);
    expect([5, 6, 7, 8].map((id) => diMissionRefForCharacter(LIVE, id, noParent))).toEqual([CORE, CORE, CORE, CORE]);
  });
  it("a sub-agent opens its PARENT's Mission, whichever Mission that is", () => {
    const parents = new Map<number, number>([
      [-1, 6],
      [-2, 2],
      [-3, 9],
    ]);
    const parentOf = (id: number) => parents.get(id) ?? null;
    expect(diMissionRefForCharacter(LIVE, -1, parentOf)).toBe(CORE);
    expect(diMissionRefForCharacter(LIVE, -2, parentOf)).toBe(HERD);
    // a runtime agent's sub-agent has no Mission either
    expect(diMissionRefForCharacter(LIVE, -3, parentOf)).toBeNull();
  });
  it('a runtime agent opens nothing (its contract never assigns it to a Mission), nor does an unknown character', () => {
    for (const id of [9, 10, 11, 12, 13, 14, 15, 16]) expect(diMissionRefForCharacter(LIVE, id, noParent), String(id)).toBeNull();
    expect(diMissionRefForCharacter(LIVE, 999, noParent)).toBeNull();
  });
});
