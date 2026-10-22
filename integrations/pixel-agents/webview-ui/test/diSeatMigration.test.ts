/**
 * THREE-OFFICE SEATING on the REAL living metropolis (real catalog, real shipped world, the real OfficeState seat/detection code), with the seat plan the DI
 * hook builds (`diPlacementModel` + `planDiSeats` + `reconcileDiSeats`, then the identity-keyed preferences):
 *   fresh placement lands on WORKSTATIONS (the seat faces a desk AND electronics by the auto-on rule) and spreads by the quota rule — one Mission of four
 *   members is 2/1/1, five + two singles 3/2/2; the 188 workstations fill exactly once and the extras take table seats (no electronics) and then chairs,
 *   each labelled as what it is; a saved table seat is honoured; the reserved seat is never used;
 *   a SAVED Palace-only roster migrates ONCE (the number-keyed seats of an earlier process are never trusted, so the rule meets an empty identity store),
 *   then never moves again; a preference that already exists is honoured and NOT re-placed; identity — not the number — carries seating across a restart,
 *   a VALID 1000-character task id included; a preserved lounge-chair seat stays; discovery-order changes move nobody.
 * Evidence here is SYNTHETIC (fixture identities); real-identity acceptance is Root-coordinated on the final build.
 */
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';

import { beforeAll, test } from 'vitest';

import { checkDiSeatPreferences } from '../../core/src/diSeatPreferences.ts';
import type { DiOfficeState } from '../../core/src/messages.ts';
import { diPlacementModel } from '../src/components/diPlacementModel.ts';
import { planDiSeats } from '../src/components/diSeatPlan.ts';
import { OfficeState } from '../src/office/engine/officeState.ts';
import { diSeatingOrder, reconcileDiSeats } from '../src/office/layout/diSeating.ts';
import { mergeSeatPrefs, type SeatPrefs,seatPrefsFromRecord, seatPrefsToRecord } from '../src/office/layout/diSeatPrefs.ts';
import { getDeskTiles, layoutToSeats } from '../src/office/layout/layoutSerializer.ts';
import { electronicsTileSet, seatFacesElectronics } from '../src/office/layout/seatElectronics.ts';
import type { OfficeLayout } from '../src/office/types.ts';
import { Direction } from '../src/office/types.ts';
import { loadBundledCatalog } from './helpers/bundledCatalog.ts';

const ROOT = path.join(__dirname, '..', '..');
const report = (k: string, v: unknown) => console.log(`[bounds] ${JSON.stringify({ [k]: v })}`);
beforeAll(() => loadBundledCatalog());
const LAYOUT = JSON.parse(fs.readFileSync(path.join(ROOT, 'packaging/di/worlds/living-metropolis.json'), 'utf8')) as OfficeLayout;
const OFFICES = ['Palace', 'Botanical Atrium', 'Deco Observatory'] as const;

// ── synthetic DI state (labelled fixture identities) ─────────────────────────────────────────────────

interface Mem { id: number; ref: string; role?: string; task?: string }
const mission = (ref: string, members: Mem[]) => ({
  sourceId: 'syn',
  missionRef: `syn:${ref}`,
  members: {
    members: members.map((m) => ({
      characterId: m.id,
      coverage: 'direct',
      memberRef: m.ref,
      role: m.role ?? 'executor',
      taskId: { state: 'observed', value: m.task ?? 'task-1' },
    })),
  },
});
const stateOf = (missions: ReturnType<typeof mission>[], runtime: Array<{ id: number; ref: string }> = []) =>
  ({
    missions: { missions },
    runtimeAgents: { agents: runtime.map((r) => ({ characterId: r.id, agentRef: r.ref, rootRef: 'syn:root', sourceId: 'syn' })) },
  }) as unknown as Pick<DiOfficeState, 'missions' | 'runtimeAgents'>;
/** a team: lead + n-1 members, numbers from `first` */
const team = (ref: string, n: number, first: number): Mem[] =>
  Array.from({ length: n }, (_, k) => ({ id: first + k, ref: `${ref}-${k === 0 ? 'lead' : `m${k}`}`, role: k === 0 ? 'lead' : 'executor' }));

function office(): OfficeState {
  const o = new OfficeState(LAYOUT);
  o.setReceptionistEnabled(true);
  return o;
}
/** What the DI hook does for a new office state: plan, add the characters that are not on the canvas, reconcile desks, record where everyone sits. */
function run(
  o: OfficeState,
  state: Pick<DiOfficeState, 'missions' | 'runtimeAgents'>,
  prefs: SeatPrefs,
  vouched: (id: number) => string | undefined = () => undefined,
) {
  const model = diPlacementModel(state);
  const plan = planDiSeats(o, model, prefs, vouched);
  for (const c of model.entitlement) if (!o.characters.has(c.id)) o.addAgent(c.id, 0, 0, plan.seatFor.get(c.id), true, 'zone');
  reconcileDiSeats(o, diSeatingOrder(state));
  const sitting = new Map<string, string>();
  for (const [id, key] of model.keys) {
    const seat = o.characters.get(id)?.seatId;
    if (!seat) continue;
    sitting.set(key, seat);
    o.noteDiPlacement(id, key, plan.placement.get(id) ?? 'unplanned');
  }
  return { model, plan, sitting, prefs: mergeSeatPrefs(prefs, sitting) };
}
const perOffice = (o: OfficeState) => {
  const a = o.officeAssignment()!;
  const counts = Object.fromEntries(OFFICES.map((l) => [l, 0])) as Record<string, number>;
  for (const c of a.characters) if (c.office) counts[c.office]++;
  return OFFICES.map((l) => counts[l]);
};
const facingTile = (s: { seatCol: number; seatRow: number; facingDir: number }) =>
  s.facingDir === Direction.UP ? [s.seatCol, s.seatRow - 1] : s.facingDir === Direction.LEFT ? [s.seatCol - 1, s.seatRow] : s.facingDir === Direction.RIGHT ? [s.seatCol + 1, s.seatRow] : [s.seatCol, s.seatRow + 1];

test('fresh placement of one Mission of four members is 2/1/1 across the three offices, every member on a workstation that faces a desk AND electronics', () => {
  const o = office();
  const state = stateOf([mission('mn-1', team('a', 4, 1))]);
  const r = run(o, state, new Map());
  assert.deepEqual(perOffice(o), [2, 1, 1]);
  const desks = getDeskTiles(LAYOUT.furniture);
  const electronics = electronicsTileSet(LAYOUT.furniture);
  const seats = layoutToSeats(LAYOUT.furniture);
  const a = o.officeAssignment()!;
  assert.equal(a.characters.length, 4);
  for (const c of a.characters) {
    assert.equal(c.seatKind, 'workstation');
    const s = seats.get(c.seatId!)!;
    assert.ok(desks.has(facingTile(s).join(',')), `${c.seatId} faces an isDesk tile`);
    assert.ok(seatFacesElectronics(s, electronics), `${c.seatId} faces electronics: a monitor or PC is in front of the seat`);
    assert.equal(c.placement, 'fresh');
    assert.ok(c.identityKey && r.model.keys.get(c.id) === c.identityKey, 'the exported identity is the fixture identity');
  }
  // the lead and the first member share the Palace and sit next to each other; the next two are in the Atrium and the Observatory
  const byRef = new Map(a.characters.map((c) => [JSON.parse(c.identityKey!)[4] as string, c]));
  assert.equal(byRef.get('a-lead')!.office, 'Palace');
  assert.equal(byRef.get('a-m1')!.office, 'Palace');
  assert.equal(byRef.get('a-m2')!.office, 'Botanical Atrium');
  assert.equal(byRef.get('a-m3')!.office, 'Deco Observatory');
  report('fresh_four_member_mission', a.characters.map((c) => [JSON.parse(c.identityKey!)[4], c.office, c.seatId]));
});

test('one Mission of five with two single agents is 3/2/2, all on workstations; a large team with small ones follows the quota rule', () => {
  const o = office();
  const r = run(o, stateOf([mission('mn-big', team('big', 5, 1))], [{ id: 6, ref: 'solo-1' }, { id: 7, ref: 'solo-2' }]), new Map());
  assert.deepEqual(perOffice(o), [3, 2, 2]);
  for (const c of o.officeAssignment()!.characters) assert.equal(c.seatKind, 'workstation');
  assert.equal(r.plan.allocation!.fallback.size, 0);
  // one large team and small teams: 30 + 5 + 3 + 2 = 40 → quotas 14/13/13, whatever the teams are
  const o2 = office();
  const members = [mission('mn-L', team('L', 30, 1)), mission('mn-A', team('A', 5, 100)), mission('mn-B', team('B', 3, 200)), mission('mn-C', team('C', 2, 300))];
  const r2 = run(o2, stateOf(members), new Map());
  assert.deepEqual(r2.plan.allocation!.quotas, [14, 13, 13]);
  assert.deepEqual(perOffice(o2), [14, 13, 13]);
  for (const c of o2.officeAssignment()!.characters) assert.equal(c.seatKind, 'workstation');
  report('large_and_small_teams', { counts: perOffice(o2), quotas: r2.plan.allocation!.quotas });
});

test('the 188 workstations fill exactly once; the next 57 take TABLE seats and the rest chairs, each labelled; the reserved seat is never used', () => {
  const o = office();
  const runtime = Array.from({ length: 250 }, (_, i) => ({ id: i + 1, ref: `agent-${i}` }));
  const r = run(o, stateOf([], runtime), new Map());
  const a = o.officeAssignment()!;
  const onWs = a.characters.filter((c) => c.seatKind === 'workstation');
  const onTable = a.characters.filter((c) => c.seatKind === 'table, not workstation');
  const onChair = a.characters.filter((c) => c.seatKind === 'chair, not workstation');
  assert.equal(onWs.length, 188);
  assert.equal(new Set(onWs.map((c) => c.seatId)).size, 188, 'every workstation exactly once');
  assert.equal(onTable.length, 57, 'every table seat is used before any chair');
  assert.equal(onChair.length, 5, 'the five over the workstation and table capacity');
  for (const c of [...onTable, ...onChair]) assert.equal(c.placement, 'fallback', 'labelled as the fallback it is');
  for (const c of onWs) assert.equal(c.placement, 'fresh');
  assert.equal(r.plan.allocation!.fallback.size, 62);
  assert.deepEqual(perOffice(o), [70, 90, 90], 'Palace 44 workstations + 21 tables + 5 chairs; each satellite 72 + 18');
  for (const c of a.characters) assert.notEqual(c.seatId, 'expansion-150', 'the reserved reception seat is never used');
  assert.deepEqual(
    a.offices.map((x) => [x.seats, x.workstations, x.tables, x.otherChairs]),
    [[124, 44, 21, 59], [90, 72, 18, 0], [90, 72, 18, 0]],
    'total seats, then workstations / table seats / other chairs per office, after the reservation',
  );
  report('workstation_fill', { workstationsUsed: onWs.length, tables: onTable.length, chairs: onChair.length });
}, 120_000);

test('a SAVED table-seat preference (satellite and Palace) is honoured by the whole plan, labelled a table; the new team is on workstations', () => {
  const o = office();
  const det = o.officeDetection!;
  const atriumTable = det.offices[1].tableIds[3];
  const palaceTable = det.offices[0].tableIds[2];
  const state = stateOf([mission('mn-old', [{ id: 1, ref: 'old-a', role: 'lead' }, { id: 2, ref: 'old-b', role: 'executor' }]), mission('mn-new', team('n', 3, 3))]);
  const model = diPlacementModel(state);
  const prefs: SeatPrefs = new Map([[model.keys.get(1)!, atriumTable], [model.keys.get(2)!, palaceTable]]);
  const r = run(o, state, prefs);
  const a = o.officeAssignment()!;
  const byId = new Map(a.characters.map((c) => [c.id, c]));
  assert.equal(byId.get(1)!.seatId, atriumTable);
  assert.equal(byId.get(1)!.seatKind, 'table, not workstation');
  assert.equal(byId.get(1)!.office, 'Botanical Atrium');
  assert.equal(byId.get(1)!.placement, 'preserved');
  assert.equal(byId.get(2)!.seatId, palaceTable);
  assert.equal(byId.get(2)!.seatKind, 'table, not workstation');
  assert.equal(byId.get(2)!.placement, 'preserved');
  for (const c of a.characters.filter((x) => x.id > 2)) assert.equal(c.seatKind, 'workstation');
  assert.equal(r.plan.allocation!.fresh.size, 3);
  assert.equal(r.plan.allocation!.fallback.size, 0, 'a preserved table seat is not a fallback');
});

test('MIXED: a preserved lounge-chair preference keeps its chair; the new team gets workstations by the quota rule (2/1/1)', () => {
  const o = office();
  const lounge = o.officeDetection!.offices[0].chairIds[7];
  const state = stateOf([mission('mn-old', [{ id: 1, ref: 'old-lead', role: 'lead' }]), mission('mn-new', team('n', 3, 2))]);
  const model = diPlacementModel(state);
  const prefs: SeatPrefs = new Map([[model.keys.get(1)!, lounge]]);
  const r = run(o, state, prefs);
  const a = o.officeAssignment()!;
  const old = a.characters.find((c) => c.id === 1)!;
  assert.equal(old.seatId, lounge);
  assert.equal(old.seatKind, 'chair, not workstation');
  assert.equal(old.placement, 'preserved');
  for (const c of a.characters.filter((x) => x.id !== 1)) assert.equal(c.seatKind, 'workstation');
  assert.deepEqual(perOffice(o), [2, 1, 1]);
  assert.equal(r.plan.allocation!.fresh.size, 3);
});

test('a SAVED Palace-only roster migrates ONCE: the old process\'s numbers are never trusted, the rule re-places by identity, and it never moves again', () => {
  const state = stateOf([mission('mn-1', team('a', 4, 1))]);
  // the earlier build seated all four in the Palace by number; a restarted server drops those number-keyed seats (see diSeatPreferences.test.ts), so the
  // new process knows no seat for anyone and no identity preference exists: the rule alone decides
  const first = office();
  const r1 = run(first, state, new Map());
  assert.deepEqual(perOffice(first), [2, 1, 1], 'the Palace-only roster is spread, once');
  // the preferences are written at once; a record that passes the contract
  const record = seatPrefsToRecord(r1.prefs);
  assert.equal(checkDiSeatPreferences(record), null);
  assert.equal(record.entries.length, 4);
  // the next load (a fresh OfficeState, the saved preferences, the same identities): identical seats
  const loaded = seatPrefsFromRecord(JSON.parse(JSON.stringify(record)))!;
  const second = office();
  run(second, state, loaded);
  const seatsOf = (o: OfficeState) => Object.fromEntries(o.officeAssignment()!.characters.map((c) => [c.identityKey!, c.seatId]));
  assert.deepEqual(seatsOf(second), seatsOf(first), 'never moves again');
  assert.ok([...second.officeAssignment()!.characters].every((c) => c.placement === 'preserved'));
});

test('an identity-keyed preference that already exists is honoured and NOT re-placed, even a Palace-only arrangement', () => {
  const state = stateOf([mission('mn-1', team('a', 4, 1))]);
  const o = office();
  const model = diPlacementModel(state);
  const palace = o.officeDetection!.offices[0].workstationIds.slice(10, 14);
  const prefs: SeatPrefs = new Map(model.entitlement.map((c, i) => [c.key, palace[i]] as const));
  run(o, state, prefs);
  assert.deepEqual(perOffice(o), [4, 0, 0], 'chosen by this design earlier: honoured, not re-balanced');
  assert.deepEqual(o.officeAssignment()!.characters.map((c) => c.seatId).sort(), [...palace].sort());
});

test('identity, not the number, carries seating across a restart: the same identities under reassigned numbers get the same seats', () => {
  const p1 = office();
  const members = team('a', 4, 1);
  const r1 = run(p1, stateOf([mission('mn-1', members)]), new Map());
  const record = seatPrefsToRecord(r1.prefs);
  const before = Object.fromEntries(p1.officeAssignment()!.characters.map((c) => [JSON.parse(c.identityKey!)[4], c.seatId]));
  // a restarted server numbers the same four identities differently, and in another discovery order
  const renumbered: Mem[] = [members[2], members[0], members[3], members[1]].map((m, i) => ({ ...m, id: 40 + i * 3 }));
  const p2 = office();
  run(p2, stateOf([mission('mn-1', renumbered)]), seatPrefsFromRecord(record)!);
  const after = Object.fromEntries(p2.officeAssignment()!.characters.map((c) => [JSON.parse(c.identityKey!)[4], c.seatId]));
  assert.deepEqual(after, before, 'every identity is back in its own seat');
  assert.deepEqual(p2.officeAssignment()!.characters.map((c) => c.id).sort(), [40, 43, 46, 49]);
});

test('a VALID long identity (a 1000-character task id) persists and is honoured across a simulated restart', () => {
  const longTask = 'T'.repeat(1000);
  const members: Mem[] = [
    { id: 1, ref: `lead-with-a-long-task`, role: 'lead', task: longTask },
    { id: 2, ref: 'plain', role: 'executor' },
  ];
  const p1 = office();
  const r1 = run(p1, stateOf([mission('mn-long', members)]), new Map());
  const record = seatPrefsToRecord(r1.prefs);
  assert.equal(checkDiSeatPreferences(record), null, 'admissible: its bound is the catalog contract\'s 1024');
  const wire = JSON.parse(JSON.stringify(record));
  assert.ok(wire.entries.some((e: { i: unknown[] }) => (e.i[3] as string | null)?.length === 1000), 'stored whole, never truncated');
  const seatBefore = p1.officeAssignment()!.characters.find((c) => c.id === 1)!.seatId;
  const p2 = office();
  run(p2, stateOf([mission('mn-long', members.map((m) => ({ ...m, id: m.id + 100 })))]), seatPrefsFromRecord(wire)!);
  assert.equal(p2.officeAssignment()!.characters.find((c) => c.id === 101)!.seatId, seatBefore);
});

test('NO CHURN: reordering Missions and members, and a character joining or leaving, moves nobody who already sits', () => {
  const o = office();
  const a = team('a', 3, 1);
  const b = team('b', 2, 10);
  const r1 = run(o, stateOf([mission('mn-a', a), mission('mn-b', b)]), new Map());
  const seats = () => Object.fromEntries(o.officeAssignment()!.characters.map((c) => [c.id, c.seatId]));
  const before = seats();
  // the discovery order is reversed (Missions and members)
  run(o, stateOf([mission('mn-b', [...b].reverse()), mission('mn-a', [...a].reverse())]), r1.prefs);
  assert.deepEqual(seats(), before);
  // a new agent joins (listed first): everyone else stays
  run(o, stateOf([mission('mn-new', [{ id: 99, ref: 'newcomer', role: 'lead' }]), mission('mn-a', a), mission('mn-b', b)]), r1.prefs);
  for (const [id, seat] of Object.entries(before)) assert.equal(seats()[Number(id)], seat);
  // one leaves
  o.removeAgent(99);
  for (let i = 0; i < 20; i++) o.update(0.1); // the despawn effect finishes and the character is gone
  assert.equal(o.characters.has(99), false);
  run(o, stateOf([mission('mn-a', a), mission('mn-b', b)]), r1.prefs);
  assert.deepEqual(seats(), before);
});

test('the reserved reception seat can never be assigned by the plan, whatever the roster or the saved preference says', () => {
  const o = office();
  const state = stateOf([mission('mn-1', team('a', 4, 1))]);
  const model = diPlacementModel(state);
  const prefs: SeatPrefs = new Map([[model.keys.get(1)!, 'expansion-150']]);
  run(o, state, prefs, (id) => (id === 2 ? 'expansion-150' : undefined));
  for (const c of o.officeAssignment()!.characters) assert.notEqual(c.seatId, 'expansion-150');
  assert.deepEqual(perOffice(o), [2, 1, 1]);
});
