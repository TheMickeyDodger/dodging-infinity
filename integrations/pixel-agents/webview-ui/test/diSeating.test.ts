/**
 * DI SEATING: an ACTIVE team member is seated before a runtime agent, however and whenever they arrive.
 *
 * The defect this pins (measured on the built office, expanded-office, 51 seats): 48 idle runtime agents
 * held 48 desks; a new team of four arrived; the first three members took the 3 free seats and the fourth
 * (supervisor, BUSY) was added with NO seat, while a fresh page load of the same state seated all four
 * members and left a runtime agent deskless. The rule now: seats go to the members first, then to the
 * runtime agents in office-state order, whatever the arrival order, so the continuous page and a reload
 * agree, and when seats genuinely run out the character left deskless is a runtime agent.
 *
 * What must not move: character ids and source-qualified refs (nothing is removed or re-added),
 * selection, activity and glow, and ordinary NON-DI seating (the generic engine is unchanged).
 */
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';

import { afterEach, beforeAll, beforeEach, test, vi } from 'vitest';

import type { DiOfficeState } from '../../core/src/messages.ts';
import { reconcileDiCharacters } from '../src/components/diOfficeFormat.ts';
import { OfficeState } from '../src/office/engine/officeState.ts';
import { diSeatingOrder, reconcileDiSeats } from '../src/office/layout/diSeating.ts';
import type { OfficeLayout } from '../src/office/types.ts';
import { loadBundledCatalog } from './helpers/bundledCatalog.ts';

const RUNTIME = 'di:runtime-agents';
const MISSION = 'di_herdr_tasks:20261021-170013-00a00d@251cac842844';
const ROOT = path.join(__dirname, '..', '..');
const expanded = (): OfficeLayout =>
  JSON.parse(fs.readFileSync(path.join(ROOT, 'packaging/di/worlds/expanded-office.json'), 'utf-8'));

beforeAll(() => loadBundledCatalog());
beforeEach(() => {
  let s = 0x2f6b1d3;
  vi.spyOn(Math, 'random').mockImplementation(() => {
    s = (Math.imul(s, 1664525) + 1013904223) >>> 0;
    return s / 4294967296;
  });
});
afterEach(() => vi.restoreAllMocks());

/** A member row as reconcileDiCharacters reads it (canonical identity tuple fields included). */
const mem = (characterId: number | null, mission: string, ref: string) => ({
  characterId,
  coverage: 'direct',
  memberRef: `${mission}#${ref}`,
  taskId: { state: 'observed', value: 'task-1', reason: null },
});
/** The runtime-agent fields reconcileDiCharacters' identity key reads. */
const runtimeIdentity = (agentRef: string) => ({
  agentRef,
  sourceId: agentRef.split(':')[0],
  rootRef: agentRef.split('#')[0],
});

const range = (from: number, to: number): number[] =>
  Array.from({ length: to - from + 1 }, (_, i) => from + i);

function add(o: OfficeState, ids: number[], folder: string): void {
  for (const id of ids) o.addAgent(id, undefined, undefined, undefined, true, folder);
}
/** The slice of an office message the seating order is read from: members (mission order), then runtime agents. */
function message(members: number[], runtime: number[]): DiOfficeState {
  return {
    missions: {
      state: 'observed',
      reason: null,
      missions: [
        {
          missionRef: MISSION,
          members: {
            state: 'observed',
            reason: null,
            members: members.map((id) => ({ characterId: id })),
          },
        },
      ],
    },
    runtimeAgents: { agents: runtime.map((id) => ({ characterId: id })) },
  } as unknown as DiOfficeState;
}
const deskless = (o: OfficeState): number[] =>
  [...o.characters.values()]
    .filter((c) => c.seatId === null)
    .map((c) => c.id)
    .sort((a, b) => a - b);
const seated = (o: OfficeState): number[] =>
  [...o.characters.values()]
    .filter((c) => c.seatId !== null)
    .map((c) => c.id)
    .sort((a, b) => a - b);

const MEMBERS = range(49, 52);
const RUNTIMES = range(1, 48); // 48 verified idle runtime agents, in office-state order

/** The CONTINUOUS page: the runtime agents are drawn first and hold 48 of the 51 desks; then the team arrives. */
function continuous(): OfficeState {
  const o = new OfficeState(expanded());
  add(o, RUNTIMES, RUNTIME);
  add(o, MEMBERS, MISSION);
  return o;
}
/** A FRESH page load of the same state: the canonical reconcile adds members first, then runtime agents. */
function fresh(): OfficeState {
  const o = new OfficeState(expanded());
  add(o, MEMBERS, MISSION);
  add(o, RUNTIMES, RUNTIME);
  return o;
}

test('the seating order is the members (mission order) then the runtime agents (office-state order)', () => {
  assert.deepEqual(diSeatingOrder(message([7, 3], [9, 2])), [7, 3, 9, 2]);
  assert.deepEqual(diSeatingOrder(message([], [5])), [5]);
  const noRuntime = { missions: message([4], []).missions } as unknown as DiOfficeState;
  assert.deepEqual(diSeatingOrder(noRuntime), [4], 'an office message without runtime agents');
  const nulls = message([1], []);
  (
    nulls.missions.missions[0].members.members as unknown as Array<{ characterId: number | null }>
  ).push({ characterId: null });
  assert.deepEqual(diSeatingOrder(nulls), [1], 'a member with no character is not seated');
  const twice = message([6], [6, 8]);
  assert.deepEqual(diSeatingOrder(twice), [6, 8], 'one character is never listed twice');
});

test('ORDER GUARD: the seating order is exactly the order reconcileDiCharacters adds characters in', () => {
  // Several missions, a member with no character, and a runtime agent that is ALSO a member. If
  // reconcileDiCharacters' iteration ever changes, this fails: the two must stay in step so a fresh load
  // and a continuous page agree.
  const state = {
    missions: {
      state: 'observed',
      reason: null,
      missions: [
        {
          missionRef: 'a:1',
          members: {
            state: 'observed',
            reason: null,
            members: [
              mem(7, 'a:1', 'lead1'),
              mem(null, 'a:1', 'ghost'),
              mem(3, 'a:1', 'executor1'),
            ],
          },
        },
        {
          missionRef: 'b:2',
          members: { state: 'observed', reason: null, members: [mem(11, 'b:2', 'lead1')] },
        },
        { missionRef: 'c:3', members: { state: 'observed', reason: null, members: [] } },
      ],
    },
    runtimeAgents: {
      agents: [
        { characterId: 3, ...runtimeIdentity('x:r1#lead1') }, // already a member: listed once
        { characterId: null, ...runtimeIdentity('x:r2#lead1') },
        { characterId: 20, ...runtimeIdentity('x:r3#lead1') },
        { characterId: 9, ...runtimeIdentity('x:r4#lead1') },
      ],
    },
  } as unknown as DiOfficeState;
  const added = [...reconcileDiCharacters(new Map(), state, []).next.keys()];
  assert.deepEqual(
    added,
    [7, 3, 11, 20, 9],
    'precondition: what reconcileDiCharacters adds, in order',
  );
  assert.deepEqual(diSeatingOrder(state), added);
});

test('the generic engine is unchanged: without the DI reconciliation a late arrival finds no seat', () => {
  const o = continuous();
  assert.equal(o.seats.size, 51);
  assert.deepEqual(
    deskless(o),
    [52],
    'the supervisor, BUSY, is deskless while 48 idle runtime agents hold desks',
  );
  assert.deepEqual(
    seated(o).filter((id) => id >= 49),
    [49, 50, 51],
  );
});

test('ORDER: members arriving AFTER the runtime agents are seated; the one left deskless is a runtime agent', () => {
  const o = continuous();
  const before = new Map([...o.characters].map(([id, c]) => [id, c.folderName]));
  reconcileDiSeats(o, diSeatingOrder(message(MEMBERS, RUNTIMES)));
  assert.deepEqual(
    seated(o).filter((id) => id >= 49),
    MEMBERS,
    'every member has a desk',
  );
  assert.deepEqual(deskless(o), [48], 'exactly one is deskless and it is the last runtime agent');
  assert.equal(seated(o).length, 51, 'all 51 desks are held');
  const seatUids = [...o.characters.values()].map((c) => c.seatId).filter((s) => s !== null);
  assert.equal(new Set(seatUids).size, seatUids.length, 'no desk is held twice');
  for (const seat of o.seats.values()) assert.equal(seat.assigned, true);
  assert.deepEqual(
    [...o.characters].map(([id, c]) => [id, c.folderName]),
    [...before],
    'ids and zones unchanged',
  );
});

test('ORDER: members arriving BEFORE the runtime agents: nothing to change', () => {
  const o = fresh();
  assert.deepEqual(deskless(o), [48]);
  const seats = [...o.characters.values()].map((c) => `${c.id}:${c.seatId}`);
  reconcileDiSeats(o, diSeatingOrder(message(MEMBERS, RUNTIMES)));
  assert.deepEqual(
    [...o.characters.values()].map((c) => `${c.id}:${c.seatId}`),
    seats,
    'no seat moved',
  );
});

test('RELOAD: the continuous page and a fresh load agree on who has a desk', () => {
  const a = continuous();
  reconcileDiSeats(a, diSeatingOrder(message(MEMBERS, RUNTIMES)));
  const b = fresh();
  reconcileDiSeats(b, diSeatingOrder(message(MEMBERS, RUNTIMES)));
  assert.deepEqual(deskless(a), deskless(b));
  assert.deepEqual(seated(a), seated(b));
  assert.deepEqual(deskless(b), [48], 'and it is the same one, whichever way the state is reached');
});

test('ORDER: a runtime agent that arrives later but ranks earlier takes the desk of the last one', () => {
  const o = continuous();
  reconcileDiSeats(o, diSeatingOrder(message(MEMBERS, RUNTIMES)));
  add(o, [60], RUNTIME); // arrives last, but the office lists it FIRST among the runtime agents
  const order = diSeatingOrder(message(MEMBERS, [60, ...RUNTIMES]));
  reconcileDiSeats(o, order);
  const f = new OfficeState(expanded());
  add(f, [...MEMBERS], MISSION);
  add(f, [60, ...RUNTIMES], RUNTIME);
  reconcileDiSeats(f, order);
  assert.deepEqual(deskless(o), deskless(f), 'the same as a fresh load of that state');
  assert.deepEqual(
    deskless(o),
    [47, 48],
    'the last two in office-state order wait for a desk (one more than seats)',
  );
  assert.deepEqual(
    seated(o).filter((id) => MEMBERS.includes(id)),
    MEMBERS,
  );
});

test('IDENTITY: ids, zones, selection, activity and look are untouched by reseating', () => {
  const o = continuous();
  o.selectedAgentId = 48; // the character about to lose its desk is the one on screen
  o.cameraFollowId = 48;
  const ch48 = o.characters.get(48)!;
  ch48.isActive = true;
  const look = (id: number) => {
    const c = o.characters.get(id)!;
    return [c.id, c.palette, c.hueShift, c.isActive, c.folderName, c.isSubagent].join('|');
  };
  const before = [...o.characters.keys()].map(look);
  reconcileDiSeats(o, diSeatingOrder(message(MEMBERS, RUNTIMES)));
  assert.deepEqual(
    [...o.characters.keys()].map(look),
    before,
    'same characters, same look, same zones',
  );
  assert.equal(o.selectedAgentId, 48, 'the selection stays on the same character');
  assert.equal(o.cameraFollowId, 48);
  const persisted = o.getPersistableSeats();
  assert.equal(persisted[48].seatId, null, 'a deskless character is persisted as such');
  assert.equal(typeof persisted[52].seatId, 'string');
  const c = o.characters.get(48)!;
  const tile = `${c.tileCol},${c.tileRow}`;
  assert.equal(o.blockedTiles.has(tile), false, 'it stands on a walkable tile, not on a desk');
});

test('IDLE ACCOUNTING: idle members keep their desks, and busy or idle never decides who waits', () => {
  const o = continuous();
  for (const id of [49, 50]) o.characters.get(id)!.isActive = false; // idle members: present, not absent
  for (const id of [47, 48]) o.characters.get(id)!.isActive = true; // busy runtime agents
  reconcileDiSeats(o, diSeatingOrder(message(MEMBERS, RUNTIMES)));
  for (const id of [49, 50, 51, 52])
    assert.notEqual(o.characters.get(id)!.seatId, null, `member ${id} is seated`);
  assert.deepEqual(
    deskless(o),
    [48],
    'the order decides, not the activity: a busy runtime agent still waits',
  );
  assert.notEqual(
    o.characters.get(47)!.seatId,
    null,
    'the busy runtime agent that ranks inside the seats keeps its desk',
  );
});

test('HONEST OVERFLOW: only a runtime agent is left deskless while the members fit', () => {
  const o = new OfficeState(expanded());
  const runtime = range(200, 269);
  add(o, runtime, RUNTIME); // 70 runtime agents arrive first; 51 desks
  add(o, MEMBERS, MISSION);
  reconcileDiSeats(o, diSeatingOrder(message(MEMBERS, runtime)));
  assert.equal(deskless(o).length, 70 + 4 - 51);
  for (const id of deskless(o))
    assert.ok(runtime.includes(id), `deskless ${id} is a runtime agent`);
  for (const id of MEMBERS) assert.notEqual(o.characters.get(id)!.seatId, null);
});

test('HONEST OVERFLOW: when even the members outnumber the seats, the last members wait, nobody else', () => {
  const o = new OfficeState(expanded());
  const members = range(100, 155); // 56 members, 51 desks
  add(o, members, MISSION);
  add(o, [1, 2], RUNTIME);
  reconcileDiSeats(o, diSeatingOrder(message(members, [1, 2])));
  assert.deepEqual(
    deskless(o),
    [...members.slice(51), 1, 2].sort((a, b) => a - b),
  );
});

test('stable: a second reconciliation of the same state moves nothing', () => {
  const o = continuous();
  const order = diSeatingOrder(message(MEMBERS, RUNTIMES));
  reconcileDiSeats(o, order);
  const once = [...o.characters.values()].map(
    (c) => `${c.id}:${c.seatId}:${c.tileCol},${c.tileRow}:${c.state}`,
  );
  reconcileDiSeats(o, order);
  assert.deepEqual(
    [...o.characters.values()].map(
      (c) => `${c.id}:${c.seatId}:${c.tileCol},${c.tileRow}:${c.state}`,
    ),
    once,
  );
});

test('vacateSeat frees the desk, stands the character on a walkable tile, and does nothing twice', () => {
  const o = fresh();
  const holder = [...o.characters.values()].find((c) => c.seatId !== null)!;
  const uid = holder.seatId!;
  assert.equal(o.vacateSeat(holder.id), uid);
  assert.equal(o.seats.get(uid)!.assigned, false);
  assert.equal(holder.seatId, null);
  assert.equal(o.blockedTiles.has(`${holder.tileCol},${holder.tileRow}`), false);
  assert.equal(o.vacateSeat(holder.id), null, 'it holds none now');
  assert.equal(o.vacateSeat(99999), null, 'an unknown character');
  assert.equal(o.seatIfFree(holder.id), true, 'and it can be seated again when a desk is free');
  assert.notEqual(holder.seatId, null);
});
