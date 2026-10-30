/**
 * The reception desk's physical entrance and its decorative receptionist, against the REAL bundled
 * catalog and the REAL shipped world presets (no stand-in layouts for the claims about shipped
 * worlds). Covers: the desk is found where each world already has it; a layout without it has none;
 * the receptionist is present with zero real agents, through a layout reload and reduced motion,
 * is never a real agent (not in the agent map, not hit-tested as one, not persisted, not seated),
 * never moves, and the new bounds are pinned to their exact values (the repository's convention).
 */
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';

import { beforeAll, test } from 'vitest';

import {
  CHARACTER_HIT_HEIGHT,
  DI_RECEPTION_DESK_TYPE,
  DI_RECEPTION_DESK_UID,
  DI_RECEPTIONIST_ID,
  DI_RECEPTIONIST_PALETTE,
  GREETER_ID,
  TILE_SIZE,
} from '../src/constants.ts';
import { OfficeState } from '../src/office/engine/officeState.ts';
import { getCatalogEntry } from '../src/office/layout/furnitureCatalog.ts';
import { getBlockedTiles, layoutToTileMap } from '../src/office/layout/layoutSerializer.ts';
import { findReceptionPost, isReceptionHit } from '../src/office/layout/receptionPost.ts';
import { isWalkable } from '../src/office/layout/tileMap.ts';
import type { FootprintOf } from '../src/office/layout/worldViews.ts';
import type { OfficeLayout } from '../src/office/types.ts';
import { loadBundledCatalog } from './helpers/bundledCatalog.ts';

const ROOT = path.join(__dirname, '..', '..');
const WORLDS = path.join(ROOT, 'packaging/di/worlds');
const report = (k: string, v: unknown) => console.log(`[bounds] ${JSON.stringify({ [k]: v })}`);

const footprintOf: FootprintOf = (type) => {
  const e = getCatalogEntry(type);
  return e ? { w: e.footprintW, h: e.footprintH, seat: e.category === 'chairs' } : null;
};
const load = (file: string): OfficeLayout =>
  JSON.parse(fs.readFileSync(path.join(WORLDS, file), 'utf-8')) as OfficeLayout;
const walkableIn = (layout: OfficeLayout) => {
  const tileMap = layoutToTileMap(layout);
  const blocked = getBlockedTiles(layout.furniture);
  return (c: number, r: number) => isWalkable(c, r, tileMap, blocked);
};

beforeAll(() => loadBundledCatalog());

/**
 * Every shipped world embeds the same palace, shifted: [file, columns shift, rows shift, the post's
 * palace-relative tile]. In the expanded office the lobby desk sits against the lobby's north wall,
 * so the receptionist stands at its west end; where the entrance hall is open (the forest, the
 * metropolis and the living metropolis) it stands behind the desk, centred.
 */
const SHIPPED: ReadonlyArray<[string, number, number, [number, number]]> = [
  ['expanded-office.json', 0, 0, [22, 9]],
  ['vast-forest.json', 56, 40, [24, 8]],
  ['metropolis.json', 56, 40, [24, 8]],
  ['living-metropolis.json', 56, 40, [24, 8]],
];

test('the new reception bounds are pinned to their exact values', () => {
  report('di.reception', {
    DI_RECEPTION_DESK_UID,
    DI_RECEPTION_DESK_TYPE,
    DI_RECEPTIONIST_ID,
    DI_RECEPTIONIST_PALETTE,
  });
  // The lobby desk every shipped preset already carries (palace-relative (23, 9)).
  assert.equal(DI_RECEPTION_DESK_UID, 'expansion-148');
  assert.equal(DI_RECEPTION_DESK_TYPE, 'DESK_FRONT');
  // Far outside real agent ids (positive), sub-agent ids (-1 downward) and the greeter's.
  assert.equal(DI_RECEPTIONIST_ID, -1_000_000_001);
  assert.notEqual(DI_RECEPTIONIST_ID, GREETER_ID);
  // The receptionist job's own palette (ambientJobs.ts).
  assert.equal(DI_RECEPTIONIST_PALETTE, 3);
  // Superseded (D-10, CONTROL-MAP "Reception left-rail only"): the in-world desk TARGET, an inbox opener, is removed,
  // and with it the two constants that pinned its label and stacking order (DI_RECEPTION_LABEL,
  // DI_RECEPTION_TARGET_Z_INDEX). The desk, the receptionist and the post pinned above are unchanged.
});

for (const [file, dc, dr, stand] of SHIPPED) {
  test(`${file}: the existing lobby desk is the reception desk, with a walkable post behind it`, () => {
    const layout = load(file);
    const post = findReceptionPost(layout, footprintOf, walkableIn(layout));
    assert.ok(post, 'a reception post');
    assert.equal(post.deskUid, DI_RECEPTION_DESK_UID);
    assert.deepEqual([post.deskCol - dc, post.deskRow - dr], [23, 9], 'the palace lobby desk');
    assert.deepEqual([post.standCol - dc, post.standRow - dr], stand, 'the palace-relative post');
    // Always touching the desk's footprint, never inside it.
    const inDesk =
      post.standCol >= post.deskCol &&
      post.standCol < post.deskCol + post.deskW &&
      post.standRow >= post.deskRow &&
      post.standRow < post.deskRow + post.deskH;
    assert.equal(inDesk, false);
    const touches =
      Math.abs(post.standCol - Math.min(Math.max(post.standCol, post.deskCol), post.deskCol + post.deskW - 1)) +
        Math.abs(post.standRow - Math.min(Math.max(post.standRow, post.deskRow), post.deskRow + post.deskH - 1)) ===
      1;
    assert.ok(touches, 'next to the desk');
    assert.ok(walkableIn(layout)(post.standCol, post.standRow), 'a walkable tile');
    // Deterministic: the same layout, the same post.
    assert.deepEqual(findReceptionPost(layout, footprintOf, walkableIn(layout)), post);
    // The receptionist never stands on a chair tile (a real agent's seat).
    const chairs = layout.furniture.filter((f) => getCatalogEntry(f.type)?.category === 'chairs');
    for (const c of chairs) {
      const e = getCatalogEntry(c.type)!;
      const standsOn: boolean =
        post.standCol >= c.col &&
        post.standCol < c.col + e.footprintW &&
        post.standRow >= c.row &&
        post.standRow < c.row + e.footprintH;
      assert.equal(standsOn, false, `not on chair ${c.uid}`);
    }
  });
}

test('a layout without the reception desk has no post (the bundled default, a deleted desk, a recycled uid)', () => {
  const dflt = JSON.parse(
    fs.readFileSync(path.join(ROOT, 'webview-ui/public/assets/default-layout-1.json'), 'utf-8'),
  ) as OfficeLayout;
  assert.equal(findReceptionPost(dflt, footprintOf, walkableIn(dflt)), null);

  const layout = load('expanded-office.json');
  const without = {
    ...layout,
    furniture: layout.furniture.filter((f) => f.uid !== DI_RECEPTION_DESK_UID),
  };
  assert.equal(findReceptionPost(without, footprintOf, walkableIn(without)), null);

  // The uid on a different furniture type is not a desk.
  const recycled = {
    ...layout,
    furniture: layout.furniture.map((f) =>
      f.uid === DI_RECEPTION_DESK_UID ? { ...f, type: 'PLANT' } : f,
    ),
  };
  assert.equal(findReceptionPost(recycled, footprintOf, walkableIn(recycled)), null);

  // Nowhere to stand: no post, never a receptionist inside furniture or a wall.
  const walled = findReceptionPost(layout, footprintOf, () => false);
  assert.equal(walled, null);
});

test('the post falls back deterministically when the tile behind the desk is taken', () => {
  const layout = load('expanded-office.json');
  const open = walkableIn(layout);
  const first = findReceptionPost(layout, footprintOf, open)!;
  const second = findReceptionPost(
    layout,
    footprintOf,
    (c, r) => open(c, r) && !(c === first.standCol && r === first.standRow),
  )!;
  assert.ok(second, 'another tile next to the desk');
  assert.notDeepEqual([second.standCol, second.standRow], [first.standCol, first.standRow]);
  assert.equal(second.deskUid, first.deskUid);
});

test('the hit area is the desk and the receptionist, nothing else', () => {
  const layout = load('expanded-office.json');
  const post = findReceptionPost(layout, footprintOf, walkableIn(layout))!;
  const T = TILE_SIZE;
  const head = CHARACTER_HIT_HEIGHT - T;
  const hit = (x: number, y: number) => isReceptionHit(post, x, y, T, head);
  assert.ok(hit((post.deskCol + 0.5) * T, (post.deskRow + 0.5) * T), 'on the desk');
  assert.ok(hit((post.standCol + 0.5) * T, (post.standRow + 0.5) * T), 'on the receptionist');
  assert.ok(hit((post.standCol + 0.5) * T, post.standRow * T - head + 1), 'on the receptionist’s head');
  assert.equal(hit((post.deskCol - 1.5) * T, (post.deskRow + 0.5) * T), false, 'beside the desk');
  assert.equal(hit((post.deskCol + 0.5) * T, (post.deskRow + post.deskH + 1.5) * T), false, 'below it');
});

function expanded(): OfficeState {
  return new OfficeState(load('expanded-office.json'));
}

test('ordinary Pixel is untouched: no receptionist until the DI office asks for one', () => {
  const o = expanded();
  assert.equal(o.receptionist, null);
  assert.equal(o.receptionPost, null);
  assert.deepEqual(o.getCharacters(), []);
});

test('the receptionist is present with ZERO real agents, outside the agent map, and never an agent', () => {
  const o = expanded();
  o.setReceptionistEnabled(true);
  const r = o.receptionist;
  assert.ok(r, 'a receptionist');
  assert.equal(r.id, DI_RECEPTIONIST_ID);
  assert.equal(r.isReceptionist, true);
  assert.equal(o.characters.size, 0, 'zero real agents');
  assert.deepEqual(o.getCharacters(), [r], 'drawn through getCharacters only');
  // Not an agent for any agent loop: persistence, selection, hit testing, seats.
  assert.deepEqual(o.getPersistableSeats(), {});
  assert.equal(r.seatId, null);
  assert.equal(r.isActive, false);
  assert.equal(o.getCharacterAt(r.x, r.y - 4), null, 'a click on it never selects an agent');
  assert.equal(o.selectedAgentId, null);
  // …but it IS the reception hit target, as is the desk.
  assert.equal(o.getReceptionAt(r.x, r.y - 4), true);
  const post = o.receptionPost!;
  assert.equal(o.getReceptionAt((post.deskCol + 1) * TILE_SIZE, (post.deskRow + 1) * TILE_SIZE), true);
  assert.equal(o.getReceptionAt(1, 1), false);
});

test('real agents keep every seat and never share the receptionist’s identity or tile logic', () => {
  const o = expanded();
  const seatsBefore = o.seats.size;
  assert.ok(o.seats.has('expansion-150'), 'before the DI office is on, the lobby bench is an ordinary seat');
  o.setReceptionistEnabled(true);
  // P2-B: the receptionist holds no seat itself, but its own WORKSTATION (the one chair that faces the reception desk) is reserved: it is the
  // only seat removed, and no real agent can ever hold it
  assert.equal(o.seats.size, seatsBefore - 1, 'exactly the reserved workstation is removed');
  assert.ok(!o.seats.has('expansion-150'), 'the reserved bench seat is not a seat');
  assert.deepEqual([...o.reservedSeatIds], ['expansion-150']);
  o.addAgent(1);
  o.addAgent(2);
  assert.equal(o.characters.size, 2);
  assert.equal(o.characters.has(DI_RECEPTIONIST_ID), false);
  assert.deepEqual(Object.keys(o.getPersistableSeats()).sort(), ['1', '2']);
  const assigned = [...o.seats.values()].filter((s) => s.assigned).length;
  assert.equal(assigned, 2, 'only the two real agents hold seats');
  assert.equal(o.getCharacters().filter((c) => c.isReceptionist).length, 1);
});

test('the receptionist never wanders, however long the office runs', () => {
  const o = expanded();
  o.setReceptionistEnabled(true);
  o.addAgent(1);
  const r = o.receptionist!;
  const at = { x: r.x, y: r.y, col: r.tileCol, row: r.tileRow };
  for (let i = 0; i < 4000; i++) o.update(0.05);
  assert.equal(o.receptionist, r, 'the same receptionist');
  assert.deepEqual({ x: r.x, y: r.y, col: r.tileCol, row: r.tileRow }, at);
});

test('it survives a layout reload, reconnect and the DI mode switch; reduced motion keeps it visible', () => {
  const o = expanded();
  o.setReceptionistEnabled(true);
  const post = o.receptionPost!;
  o.rebuildFromLayout(load('expanded-office.json'));
  assert.ok(o.receptionist, 'present after the layout is loaded again');
  assert.deepEqual(
    [o.receptionPost!.deskUid, o.receptionPost!.standCol, o.receptionPost!.standRow],
    [post.deskUid, post.standCol, post.standRow],
  );
  o.reducedMotion = true;
  o.rebuildFromLayout(load('expanded-office.json'));
  const r = o.receptionist!;
  assert.ok(r, 'present under reduced motion');
  assert.equal(r.matrixEffect, null, 'no spawn effect to wait for: visible at once');
  for (let i = 0; i < 100; i++) o.update(0.05);
  assert.deepEqual(o.getCharacters(), [r]);
  // Switching DI mode off removes it (ordinary Pixel); on again restores it.
  o.setReceptionistEnabled(false);
  assert.equal(o.receptionist, null);
  o.setReceptionistEnabled(true);
  assert.ok(o.receptionist);
  // A layout without the desk has no receptionist (and no desk target).
  o.rebuildFromLayout({
    ...load('expanded-office.json'),
    furniture: load('expanded-office.json').furniture.filter((f) => f.uid !== DI_RECEPTION_DESK_UID),
  });
  assert.equal(o.receptionist, null);
  assert.equal(o.receptionPost, null);
});

test('it is outside the ambient population: not a resident, not on duty, not rotated off', () => {
  const o = expanded();
  o.setAmbientEnabled(true);
  o.setReceptionistEnabled(true);
  const r = o.receptionist!;
  // 3 minutes of simulated time: the duty rotation runs; the receptionist is not part of it.
  for (let i = 0; i < 3600; i++) o.update(0.05);
  assert.equal(o.receptionist, r);
  assert.ok(o.getCharacters().includes(r));
  assert.equal(
    (o.ambient?.residents ?? []).some((x) => x.id === String(DI_RECEPTIONIST_ID)),
    false,
  );
});
