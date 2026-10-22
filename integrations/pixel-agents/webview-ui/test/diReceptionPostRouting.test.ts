/**
 * P2-B, the permanent receptionist post, as ENGINE behaviour (real bundled catalog, real shipped worlds for the claims about them):
 *  - B1 the receptionist's own workstation is reserved: no real agent can hold it on any seating path (fresh, restore of a saved seat id, the DI
 *    reconciliation), and a layout without the post keeps every seat;
 *  - B2 the stand tile is blocked for agents, pets and spawning only when that cuts nothing in two, with the lobby's connectivity proven;
 *    residents that would cross the fixed post are DETOURED and progress to their stop (a control reproduces the old stuck-forever failure);
 *    in the sole-passage fallback (`tileBlocked` false) the tile is genuinely passable — a resident and a real agent actually traverse it;
 *  - no ambient resident targets or stands on the post; over a long run on the living metropolis nobody is stuck at it.
 */
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';

import { beforeAll, test } from 'vitest';

import {
  DI_RECEPTION_BENCH_UID,
  DI_RECEPTION_DESK_TYPE,
  DI_RECEPTION_DESK_UID,
  TILE_SIZE,
} from '../src/constants.ts';
import { OfficeState } from '../src/office/engine/officeState.ts';
import { reconcileDiSeats } from '../src/office/layout/diSeating.ts';
import { getCatalogEntry } from '../src/office/layout/furnitureCatalog.ts';
import { getBlockedTiles, layoutToTileMap } from '../src/office/layout/layoutSerializer.ts';
import { findReceptionPost, keepsNeighboursConnected } from '../src/office/layout/receptionPost.ts';
import { isWalkable } from '../src/office/layout/tileMap.ts';
import type { OfficeLayout } from '../src/office/types.ts';
import { TileType } from '../src/office/types.ts';
import { loadBundledCatalog } from './helpers/bundledCatalog.ts';

const ROOT = path.join(__dirname, '..', '..');
const WORLDS = path.join(ROOT, 'packaging/di/worlds');
const report = (k: string, v: unknown) => console.log(`[bounds] ${JSON.stringify({ [k]: v })}`);
beforeAll(() => loadBundledCatalog());
const load = (name: string): OfficeLayout =>
  JSON.parse(fs.readFileSync(path.join(WORLDS, `${name}.json`), 'utf8')) as OfficeLayout;
const footprintOf = (type: string) => {
  const e = getCatalogEntry(type);
  return e ? { w: e.footprintW, h: e.footprintH, seat: e.category === 'chairs' } : null;
};

// ── synthetic rooms (a real DESK_FRONT as the reception desk; everything else is floor and wall) ───────────

function room(cols: number, rows: number, floor: (c: number, r: number) => boolean, deskAt: [number, number]): OfficeLayout {
  const tiles: number[] = [];
  for (let r = 0; r < rows; r++)
    for (let c = 0; c < cols; c++) tiles.push(floor(c, r) ? TileType.FLOOR_1 : TileType.WALL);
  return {
    version: 1,
    cols,
    rows,
    tiles: tiles as OfficeLayout['tiles'],
    furniture: [{ uid: DI_RECEPTION_DESK_UID, type: DI_RECEPTION_DESK_TYPE, col: deskAt[0], row: deskAt[1] }],
  };
}
/** An open hall: the desk at (18..20, 6..7) so the stand tile is (19, 5), in the open. */
const openHall = () =>
  room(40, 20, (c, r) => c >= 1 && c <= 38 && r >= 1 && r <= 18, [18, 6]);
/** Two rooms joined by ONE one-tile-wide corridor along row 5; the desk hangs below it, so the stand tile (22, 5) is the only way through. */
const corridorWorld = () =>
  room(
    44,
    14,
    (c, r) => (c >= 1 && c <= 13 && r >= 1 && r <= 12) || (c >= 31 && c <= 42 && r >= 1 && r <= 12) || (r === 5 && c >= 14 && c <= 30),
    [21, 6],
  );

interface Amb {
  residents: Array<{
    id: string;
    routine: Int32Array;
    routineIndex: number;
    state: string;
    tileCol: number;
    tileRow: number;
    x: number;
    y: number;
    timer: number;
    route: Int32Array;
    routePos: number;
    legCache: Map<string, Int32Array>;
    blocked: number;
  }>;
  occupancy: Int16Array | Int32Array | Uint8Array;
  stats: { agentBlockReplans: number };
  postTile: number;
  blockObserver: ((tile: number, cause: 'agent' | 'seat-zone') => void) | null;
  postDetours: number;
  postDetourFailures: number;
  cols: number;
}
const ambOf = (o: OfficeState): Amb => o.ambient as unknown as Amb;

/** Put resident 0 at `from` heading for `to` and back (a test-only override of one resident's plan; the others run as built). */
function steer(o: OfficeState, from: [number, number], to: [number, number]) {
  const amb = ambOf(o);
  const cols = amb.cols;
  const r = amb.residents[0];
  const a = from[1] * cols + from[0];
  const b = to[1] * cols + to[0];
  amb.occupancy[r.tileRow * cols + r.tileCol]--;
  r.routine = Int32Array.from([a, b, a]);
  r.routineIndex = 1;
  r.tileCol = from[0];
  r.tileRow = from[1];
  r.x = from[0] * TILE_SIZE + TILE_SIZE / 2;
  r.y = from[1] * TILE_SIZE + TILE_SIZE / 2;
  r.state = 'need-path';
  r.timer = 0;
  r.route = new Int32Array(0);
  r.routePos = 0;
  r.legCache.clear();
  r.blocked = 0;
  amb.occupancy[a]++;
  return { r, a, b, cols };
}

// ── B1: the reserved workstation ──────────────────────────────────────────────────────────────────────

test('B1: the reserved bench seat can never be held by a real agent — fresh, restored from a saved seat id, or by the DI reconciliation', () => {
  const o = new OfficeState(load('living-metropolis'));
  o.setReceptionistEnabled(true);
  assert.ok(!o.seats.has(DI_RECEPTION_BENCH_UID));
  const capacity = o.seats.size;
  // fresh: fill every seat and then some; nobody ever sits on the bench
  for (let id = 1; id <= capacity + 6; id++) o.addAgent(id, 0, 0, undefined, true);
  // a restored agent whose SAVED seat id is the bench: the id is ignored, deterministically, and it is seated normally or left standing
  o.addAgent(5000, 0, 0, DI_RECEPTION_BENCH_UID, true);
  assert.notEqual(o.characters.get(5000)!.seatId, DI_RECEPTION_BENCH_UID);
  // the DI reconciliation over everyone (desks run short): still never the bench
  reconcileDiSeats(o, [5000, ...Array.from({ length: capacity + 6 }, (_, i) => i + 1)]);
  for (const ch of o.characters.values()) assert.notEqual(ch.seatId, DI_RECEPTION_BENCH_UID);
  assert.equal([...o.seats.values()].filter((s) => s.assigned).length, capacity, 'every real seat is held, and the reserved one is not among them');
  assert.equal([...o.characters.values()].filter((c) => c.seatId === null).length, o.characters.size - capacity);
}, 120_000);

test('B1: re-enabling the DI office on a layout where an agent already sits on the bench moves it to a normal seat, with the seat bookkeeping consistent', () => {
  const o = new OfficeState(load('expanded-office'));
  o.addAgent(1, 0, 0, DI_RECEPTION_BENCH_UID, true);
  assert.equal(o.characters.get(1)!.seatId, DI_RECEPTION_BENCH_UID, 'before the DI office is on it is an ordinary seat');
  o.setReceptionistEnabled(true);
  const seat = o.characters.get(1)!.seatId;
  assert.ok(seat && seat !== DI_RECEPTION_BENCH_UID, 'moved off the reserved seat onto a real one');
  assert.equal([...o.seats.values()].filter((s) => s.assigned).length, 1);
  o.setReceptionistEnabled(false);
  assert.ok(o.seats.has(DI_RECEPTION_BENCH_UID), 'switching the DI office off gives the seat back');
});

test('B1: a layout without the post keeps every seat (a custom world, a deleted desk)', () => {
  const layout = load('expanded-office');
  const without: OfficeLayout = { ...layout, furniture: layout.furniture.filter((f) => f.uid !== DI_RECEPTION_DESK_UID) };
  const off = new OfficeState(without);
  const on = new OfficeState(without);
  on.setReceptionistEnabled(true);
  assert.equal(on.receptionPost, null);
  assert.deepEqual([...on.seats.keys()], [...off.seats.keys()]);
  assert.equal(on.reservedSeatIds.size, 0);
});

// ── B2: the occupied tile ─────────────────────────────────────────────────────────────────────────────

/** Walkable component size around a seat neighbour of the home, and whether every given tile is in it. */
function flood(o: OfficeState, start: [number, number]): Set<number> {
  const { cols, rows } = o.getLayout();
  const seen = new Set<number>();
  const queue: number[] = [];
  const visit = (c: number, r: number) => {
    if (c < 0 || r < 0 || c >= cols || r >= rows) return;
    const k = r * cols + c;
    if (seen.has(k) || !isWalkable(c, r, o.tileMap, o.blockedTiles)) return;
    seen.add(k);
    queue.push(k);
  };
  visit(start[0], start[1]);
  for (let i = 0; i < queue.length; i++) {
    const c = queue[i] % cols;
    const r = (queue[i] - c) / cols;
    visit(c + 1, r);
    visit(c - 1, r);
    visit(c, r + 1);
    visit(c, r - 1);
  }
  return seen;
}

test('B2: on the living metropolis the stand tile is blocked for agents, pets and spawning, and the lobby stays connected (a path around it exists)', () => {
  const layout = load('living-metropolis');
  const off = new OfficeState(layout);
  const on = new OfficeState(layout);
  on.setReceptionistEnabled(true);
  const post = on.receptionPost!;
  assert.equal(post.tileBlocked, true);
  const key = `${post.standCol},${post.standRow}`;
  assert.ok(on.blockedTiles.has(key) && !off.blockedTiles.has(key), 'blocked now, open before');
  assert.ok(!on.walkableTiles.some((t) => t.col === post.standCol && t.row === post.standRow), 'no character is spawned or sent to wander there');
  // connectivity: every tile that was reachable from the lobby still is, except the stand tile itself
  const standIdxFor = post.standRow * layout.cols + post.standCol;
  const around = ([[1, 0], [-1, 0], [0, -1], [0, 1]] as const)
    .map(([dc, dr]) => [post.standCol + dc, post.standRow + dr] as [number, number])
    .find(([c, r]) => (r * layout.cols + c) !== standIdxFor && isWalkable(c, r, off.tileMap, off.blockedTiles));
  assert.ok(around, 'the stand tile has a walkable neighbour to start the flood from');
  const start: [number, number] = around!;
  const before = flood(off, start);
  const after = flood(on, start);
  assert.equal(after.size, before.size - 1, 'the walkable component loses exactly the stand tile');
  const standIdx = post.standRow * layout.cols + post.standCol;
  for (const k of before) if (k !== standIdx) assert.ok(after.has(k), `tile ${k} is still reachable around the receptionist`);
  // and every seat of every office still has a reachable neighbour
  for (const seat of on.seats.values()) {
    const reachable = [[1, 0], [-1, 0], [0, 1], [0, -1]].some(([dc, dr]) => after.has((seat.seatRow + dr) * layout.cols + seat.seatCol + dc));
    assert.ok(reachable, `seat ${seat.uid} is reachable`);
  }
  report('lobby_connectivity', { componentBefore: before.size, componentAfter: after.size });
}, 120_000);

test('B2: the connectivity test itself — a tile that is the only way through is never blocked, a tile in the open is', () => {
  const corridor = corridorWorld();
  const walkC = (c: number, r: number) => isWalkable(c, r, layoutToTileMap(corridor), getBlockedTiles(corridor.furniture));
  assert.equal(keepsNeighboursConnected(22, 5, walkC, corridor.cols, corridor.rows), false, 'the corridor tile is a sole passage');
  const hall = openHall();
  const walkH = (c: number, r: number) => isWalkable(c, r, layoutToTileMap(hall), getBlockedTiles(hall.furniture));
  assert.equal(keepsNeighboursConnected(19, 5, walkH, hall.cols, hall.rows), true);
  const post = findReceptionPost(corridor, footprintOf, walkC, (c, r) => keepsNeighboursConnected(c, r, walkC, corridor.cols, corridor.rows))!;
  assert.deepEqual([post.standCol, post.standRow, post.tileBlocked], [22, 5, false], 'the fallback: the first walkable candidate, left open');
});

test('B2: no ambient resident targets the post on any shipped world that has one, and none stands on it at the start', () => {
  for (const name of ['expanded-office', 'vast-forest', 'metropolis', 'living-metropolis']) {
    const layout = load(name);
    const o = new OfficeState(layout);
    o.setReceptionistEnabled(true);
    o.setAmbientEnabled(true);
    const post = o.receptionPost!;
    const idx = post.standRow * layout.cols + post.standCol;
    for (const r of ambOf(o).residents) {
      assert.ok(!Array.from(r.routine).includes(idx), `${name}: ${r.id} does not target the post tile`);
      assert.notEqual(r.tileRow * layout.cols + r.tileCol, idx, `${name}: ${r.id} does not start on the post tile`);
    }
  }
}, 120_000);

test('B2 ROUTING: a resident whose leg crosses the fixed post is detoured around it, progresses to its stop, and never occupies the post tile (bounded replans)', () => {
  const o = new OfficeState(openHall());
  o.setReceptionistEnabled(true);
  o.setAmbientEnabled(true);
  const post = o.receptionPost!;
  assert.equal(post.tileBlocked, true);
  const amb = ambOf(o);
  assert.equal(amb.postTile, post.standRow * amb.cols + post.standCol, 'the ambient world knows the fixed post');
  // a leg along the post's own row: the straight route crosses (19, 5)
  const { r, b, cols } = steer(o, [4, post.standRow], [34, post.standRow]);
  const standIdx = post.standRow * cols + post.standCol;
  const causes = observeReplans(o, standIdx);
  let reachedAt = -1;
  for (let step = 0; step < 1200; step++) {
    o.update(0.1);
    const here = r.tileRow * cols + r.tileCol;
    assert.notEqual(here, standIdx, `step ${step}: the resident never stands on the post`);
    if (here === b && reachedAt < 0) reachedAt = step / 10;
  }
  assert.ok(reachedAt >= 0 && reachedAt < 90, `it reached its stop within a bounded time (${reachedAt} s)`);
  assert.ok(amb.postDetours >= 1, 'its leg was detoured');
  assert.equal(amb.postDetourFailures, 0);
  assert.ok(amb.stats.agentBlockReplans <= 2, `no unbounded replans (${amb.stats.agentBlockReplans})`);
  assert.equal(causes.standWithNoAgent, 0, 'no resident is ever turned back at the post: every leg was detoured');
  report('post_detour', { reachedAtSeconds: reachedAt, detours: amb.postDetours, replans: amb.stats.agentBlockReplans, causes });
}, 120_000);

test('B2 CONTROL: without the route block the same resident is refused the post tile forever (the defect this fix removes)', () => {
  const o = new OfficeState(openHall());
  o.setReceptionistEnabled(true);
  o.setAmbientEnabled(true);
  const post = o.receptionPost!;
  const amb = ambOf(o);
  amb.postTile = -1; // the old behaviour: plans and routes still cross the tile while it stays an agent tile
  const { r, b, cols } = steer(o, [4, post.standRow], [34, post.standRow]);
  let reached = false;
  for (let step = 0; step < 1200; step++) {
    o.update(0.1);
    if (r.tileRow * cols + r.tileCol === b) reached = true;
  }
  assert.equal(reached, false, 'it never gets across');
  assert.ok(amb.stats.agentBlockReplans >= 20, `it retries the same blocked leg again and again (${amb.stats.agentBlockReplans} replans)`);
}, 120_000);

/** Math.random as a seeded stream, so the real agent's own wandering is the same in every run being compared. */
function withSeededRandom<T>(seed: number, fn: () => T): T {
  let state = seed >>> 0;
  const real = Math.random;
  Math.random = () => {
    state = (state + 0x6d2b79f5) >>> 0;
    let t = state;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
  try {
    return fn();
  } finally {
    Math.random = real;
  }
}

/** The give-way replans of a run, by what blocked the resident: the stand tile with no real agent on or next to it, a real agent (its tile, or the tile it steps onto next), or an assigned seat's zone. */
interface Causes {
  standWithNoAgent: number;
  agentTile: number;
  seatZone: number;
}
function observeReplans(o: OfficeState, standIdx: number): Causes {
  const causes: Causes = { standWithNoAgent: 0, agentTile: 0, seatZone: 0 };
  const cols = o.getLayout().cols;
  ambOf(o).blockObserver = (tile, why) => {
    if (why === 'seat-zone') {
      causes.seatZone++;
      return;
    }
    const agentThere = [...o.characters.values()].some(
      (c) => c.tileRow * cols + c.tileCol === tile || (c.path[0] !== undefined && c.path[0].row * cols + c.path[0].col === tile),
    );
    if (tile === standIdx && !agentThere) causes.standWithNoAgent++;
    else causes.agentTile++;
  };
  return causes;
}

/**
 * ONE corridor run: the same world, crowd, seed, agent journey and duration, with the DI office (the receptionist) on or off. A real agent walks west to east
 * through the one-tile passage; resident 0 is steered along the same journey; `leave` removes the agent once it has crossed, otherwise it keeps wandering.
 */
function corridorRun(enabled: boolean, leave: boolean) {
  return withSeededRandom(12345, () => {
    const o = new OfficeState(corridorWorld());
    if (enabled) o.setReceptionistEnabled(true);
    o.setAmbientEnabled(true);
    const amb = ambOf(o);
    const cols = o.getLayout().cols;
    const standIdx = enabled ? o.receptionPost!.standRow * cols + o.receptionPost!.standCol : 5 * cols + 22; // the same tile in both runs
    const causes = observeReplans(o, standIdx);
    o.addAgent(1, 0, 0, undefined, true);
    const agent = o.characters.get(1)!;
    agent.tileCol = 4;
    agent.tileRow = 5;
    agent.x = 4 * TILE_SIZE + TILE_SIZE / 2;
    agent.y = 5 * TILE_SIZE + TILE_SIZE / 2;
    assert.equal(o.walkToTile(1, 38, 5), true, 'a path exists through the passage');
    const { r, b } = steer(o, [4, 5], [36, 8]);
    let agentOnPost = false;
    let residentOnPost = false;
    let agentDone = -1;
    let residentDone = -1;
    let removed = false;
    const last = new Map<string, { tile: number; since: number }>();
    let worstStuck = 0;
    /** residents that stayed on one tile, walking, for 30 s or more — and whether that tile is the stand tile or next to it */
    const stuckResidents = new Set<string>();
    let stuckAtPost = 0;
    const nearPost = (tile: number) => {
      const dc = Math.abs((tile % cols) - (standIdx % cols));
      const dr = Math.abs(Math.floor(tile / cols) - Math.floor(standIdx / cols));
      return dc + dr <= 1;
    };
    for (let step = 1; step <= 1800; step++) {
      o.update(0.1);
      if (!removed && agent.tileRow * cols + agent.tileCol === standIdx) agentOnPost = true;
      if (r.tileRow * cols + r.tileCol === standIdx) residentOnPost = true;
      if (agentDone < 0 && agent.tileCol === 38 && agent.tileRow === 5) agentDone = step / 10;
      if (leave && agentDone >= 0 && !removed) {
        o.removeAgent(1);
        removed = true;
      }
      if (residentDone < 0 && r.tileRow * cols + r.tileCol === b) residentDone = step / 10;
      if (step % 10 === 0)
        for (const x of amb.residents) {
          const tile = x.tileRow * cols + x.tileCol;
          const was = last.get(x.id);
          if (!was || was.tile !== tile || x.state !== 'walk') last.set(x.id, { tile, since: step / 10 });
          else {
            worstStuck = Math.max(worstStuck, step / 10 - was.since);
            if (step / 10 - was.since >= 30 && !stuckResidents.has(`${x.id}@${tile}`)) {
              stuckResidents.add(`${x.id}@${tile}`);
              if (nearPost(tile)) stuckAtPost++;
            }
          }
        }
    }
    return {
      tileBlocked: enabled ? o.receptionPost!.tileBlocked : null,
      replans: amb.stats.agentBlockReplans,
      causes,
      agentOnPost,
      residentOnPost,
      agentDone,
      residentDone,
      worstStuck,
      stuckResidents: stuckResidents.size,
      stuckAtPost,
      residents: amb.residents.length,
    };
  });
}

test('B2 FALLBACK (differential): the same corridor, crowd, seed, journeys and duration with the receptionist OFF and ON — the post induces NO replans; the resident and the real agent traverse it in bounded time; no resident is stuck at or because of the post', () => {
  const off = corridorRun(false, true);
  const on = corridorRun(true, true);
  report('post_corridor_differential', { off, on });
  assert.equal(on.tileBlocked, false, 'blocking it would cut the world in two: the tile stays passable');
  assert.equal(on.residents, 140);
  // post-induced replans: none (a block on the stand tile with no real agent on or next to it); every give-way replan is a real agent's
  assert.equal(on.causes.standWithNoAgent, 0, 'no replan is caused by the receptionist');
  assert.equal(on.causes.seatZone, 0);
  assert.equal(on.causes.agentTile, on.replans, 'every replan is an ordinary give-way to a real agent');
  // and the DIFFERENTIAL: the very same crowd and journeys give exactly the same replans without the receptionist at all
  assert.equal(on.replans, off.replans, 'the receptionist changes nothing about the traffic');
  assert.deepEqual(on.causes, off.causes);
  assert.equal(on.worstStuck, off.worstStuck, 'the longest wait of any resident is the same with and without the receptionist');
  assert.equal(on.stuckResidents, off.stuckResidents);
  // the real agent and the resident actually cross the receptionist\'s tile and reach the far side in bounded time (identical to the run without it)
  assert.ok(on.agentOnPost && on.residentOnPost, 'both walked THROUGH the receptionist\'s tile');
  assert.ok(on.agentDone >= 0 && on.agentDone < 60, `the agent reached the far side (${on.agentDone} s)`);
  assert.ok(on.residentDone >= 0 && on.residentDone < 120, `the resident reached the far side (${on.residentDone} s)`);
  assert.equal(on.agentDone, off.agentDone);
  assert.equal(on.residentDone, off.residentDone);
  // no resident is stuck AT or BECAUSE OF the post: none stood on or next to the stand tile, walking, for half a minute, and no replan was the post's (above).
  // (This does not claim that no resident anywhere waits long: ordinary give-way to the real agent in this one-tile corridor is what it is, and is identical
  // with the receptionist off — see the next test.)
  assert.equal(on.stuckAtPost, 0, 'nobody is stuck at the post');
  assert.equal(on.stuckAtPost, off.stuckAtPost);
}, 120_000);

test('B2 FALLBACK (an observed PRE-EXISTING behaviour, reported, not fixed here): left to wander, a real agent in a one-tile passage makes a 140-resident crowd give way constantly — identically with the receptionist OFF', () => {
  const off = corridorRun(false, false);
  const on = corridorRun(true, false);
  report('post_corridor_agent_wanders', { off, on });
  assert.equal(on.causes.standWithNoAgent, 0, 'not one of those replans is the receptionist\'s doing');
  assert.equal(on.causes.seatZone, 0);
  assert.equal(on.causes.agentTile, on.replans, 'every one is a real agent\'s tile');
  assert.equal(on.replans, off.replans, 'the count is the same with no receptionist at all: it is the crowd against the agent');
  assert.equal(on.worstStuck, off.worstStuck, 'and so is the longest wait of any resident (existing give-way behaviour in a one-tile synthetic corridor)');
  assert.equal(on.stuckResidents, off.stuckResidents);
  assert.equal(on.stuckAtPost, off.stuckAtPost, 'whether any resident waits at the post\'s tile is the same with and without the receptionist');
  assert.deepEqual(on.causes, off.causes);
}, 120_000);

test('B2 FALLBACK (additional focused proof): ONE resident alone and a real agent cross the one-tile passage; the replan count is all give-way to the agent, none the post\'s', () => {
  withSeededRandom(777, () => {
    const o = new OfficeState(corridorWorld());
    o.setReceptionistEnabled(true);
    o.setAmbientEnabled(true);
    const post = o.receptionPost!;
    assert.equal(post.tileBlocked, false);
    const amb = ambOf(o);
    assert.equal(amb.postTile, -1, 'no fixed-post route block');
    const cols = o.getLayout().cols;
    const standIdx = post.standRow * cols + post.standCol;
    const only = amb.residents[0];
    amb.residents.splice(1);
    amb.occupancy.fill(0);
    amb.occupancy[only.tileRow * cols + only.tileCol] = 1;
    amb.stats.agentBlockReplans = 0;
    const causes = observeReplans(o, standIdx);
    o.addAgent(1, 0, 0, undefined, true);
    const agent = o.characters.get(1)!;
    agent.tileCol = 4;
    agent.tileRow = 5;
    agent.x = 4 * TILE_SIZE + TILE_SIZE / 2;
    agent.y = 5 * TILE_SIZE + TILE_SIZE / 2;
    assert.equal(o.walkToTile(1, 38, 5), true);
    const { r, b } = steer(o, [4, 5], [36, 8]);
    let onPost = false;
    let done = -1;
    for (let step = 1; step <= 1800; step++) {
      o.update(0.1);
      if (r.tileRow * cols + r.tileCol === standIdx) onPost = true;
      if (done < 0 && r.tileRow * cols + r.tileCol === b) done = step / 10;
      if (agent.tileCol === 38 && agent.tileRow === 5 && o.characters.has(1)) o.removeAgent(1);
    }
    assert.ok(onPost, 'it walked through the receptionist\'s tile');
    assert.ok(done >= 0 && done < 120, `it reached the far side (${done} s)`);
    assert.equal(causes.standWithNoAgent, 0);
    assert.equal(causes.seatZone, 0);
    assert.equal(causes.agentTile, amb.stats.agentBlockReplans, 'whatever replans there were were give-way to the real agent');
    report('post_corridor_isolated', { seconds: done, replans: amb.stats.agentBlockReplans, causes });
  });
}, 120_000);

test('B2: over a long simulated run on the living metropolis no resident stands on the post or is stuck near it', () => {
  const layout = load('living-metropolis');
  const o = new OfficeState(layout);
  o.setReceptionistEnabled(true);
  o.setAmbientEnabled(true);
  const post = o.receptionPost!;
  const amb = ambOf(o);
  const cols = layout.cols;
  const standIdx = post.standRow * cols + post.standCol;
  const last = new Map<string, { tile: number; since: number }>();
  let worstStuck = 0;
  for (let step = 1; step <= 12000; step++) {
    o.update(0.1);
    if (step % 10 !== 0) continue; // sample every simulated second
    const t = step / 10;
    for (const r of amb.residents) {
      const tile = r.tileRow * cols + r.tileCol;
      assert.notEqual(tile, standIdx, `${r.id} stood on the post at ${t} s`);
      const was = last.get(r.id);
      if (!was || was.tile !== tile || r.state !== 'walk') last.set(r.id, { tile, since: t });
      else worstStuck = Math.max(worstStuck, t - was.since);
    }
  }
  assert.ok(worstStuck < 30, `no walking resident stayed on one tile for 30 s (${worstStuck} s)`);
  assert.ok(amb.stats.agentBlockReplans <= 5, `the post caused no unbounded replans (${amb.stats.agentBlockReplans})`);
  assert.equal(amb.postDetourFailures, 0, 'every leg that crossed the post found its detour');
  report('living_metropolis_long_run', { seconds: 1200, worstWalkingStuckSeconds: worstStuck, replans: amb.stats.agentBlockReplans, detours: amb.postDetours });
}, 120_000);
