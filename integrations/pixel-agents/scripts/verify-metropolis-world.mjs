#!/usr/bin/env node
/**
 * STANDALONE WORLD VERIFIER — preservation and bounds are checked on the bytes; the ROUTE checks
 * are GEOMETRY ONLY.
 *
 *   node scripts/verify-metropolis-world.mjs [--world <metropolis.json>]
 *
 * Prints a JSON report; exits non-zero on any failed check.
 *
 * LIMITED CLAIM — read this before quoting a route result. This script does NOT run the runtime.
 * It carries a DUPLICATE of the walk rule (not WALL/VOID, not a water floor, not a solid —
 * non-background — footprint row of any furniture) so it can run with plain node. A duplicate can
 * only show the GEOMETRY is consistent with that rule: it cannot show the shipped runtime excludes
 * water or honours footprints. The duplicate's water set is compared against the runtime constant
 * (WATER_FLOOR_TILES in webview-ui/src/constants.ts, read from source), so drift fails here — but
 * runtime walkability, water exclusion and paths are PROVEN only by the runtime route test,
 * webview-ui/test/metropolisRoutes.test.ts (tileMap.isWalkable / findPath, layoutSerializer
 * getBlockedTiles, the real catalogue; water named independently of the runtime constant).
 *
 *  - preservation: the forest world block (176 x 112) byte-identical at (0,0) — tiles, tileColors,
 *    carpetTiles, areaTiles — and its 2,945 furniture records and 2 pets unchanged; the 41 PCs;
 *  - bounds: cells / furniture / pets / areas within core/src/layoutLimits.ts (read from source);
 *  - home: the explicit `home` equals the palace block; every seat (chair) lies inside it;
 *  - GEOMETRY, routes: from the palace interior, every district is reachable on foot (BFS);
 *  - GEOMETRY, bridges: each bridge (the three river bridges and the Highland stream stone bridge)
 *    carries a bank-to-bank crossing, and with its deck removed (deck cells as water) that
 *    crossing is GONE within a 10-tile window either side — the bridge, not a ford, carries it;
 *  - GEOMETRY, arches and gates: every placed opening piece (…_P2 'opening') is walkable under its
 *    span WITH ALL OTHER OBSTACLES (bottomWalkable) and not blocked by itself; every leg piece
 *    blocks; the tunnel arch admits the Grand Aquarium's door;
 *  - GEOMETRY, water: the duplicate water set equals the runtime constant; each water floor is
 *    present. (No "water is not walkable" assertion here: under a duplicate that excludes water by
 *    definition it could not fail — that proof is the runtime test's.)
 */
import crypto from 'node:crypto';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const args = process.argv.slice(2);
const worldPath = args.includes('--world') ? path.resolve(args[args.indexOf('--world') + 1]) : path.join(ROOT, 'packaging/di/worlds/metropolis.json');
const forestPath = path.join(ROOT, 'packaging/di/worlds/vast-forest.json');
const sha = (b) => crypto.createHash('sha256').update(b).digest('hex');
const worldBytes = fs.readFileSync(worldPath);
const L = JSON.parse(worldBytes);
const F = JSON.parse(fs.readFileSync(forestPath, 'utf-8'));
const checks = [];
const check = (name, ok, detail) => checks.push({ name, ok: !!ok, ...(detail === undefined ? {} : { detail }) });
const same = (a, b) => JSON.stringify(a ?? null) === JSON.stringify(b ?? null);

// catalogue (footprints, background rows, categories)
const CAT = new Map();
const dir = path.join(ROOT, 'webview-ui/public/assets/furniture');
const visit = (n, inh) => {
  const p = { category: n.category ?? inh.category, bg: n.backgroundTiles ?? inh.bg ?? 0 };
  if (n.type === 'asset') CAT.set(n.id, { w: n.footprintW, h: n.footprintH, ...p });
  for (const m of n.members ?? []) visit(m, p);
};
for (const id of fs.readdirSync(dir)) visit(JSON.parse(fs.readFileSync(path.join(dir, id, 'manifest.json'), 'utf-8')), {});
const entry = (t) => CAT.get(t.replace(/:left$/, ''));

// bounds from the ONE definition (source constants)
const limitsSrc = fs.readFileSync(path.join(ROOT, 'core/src/layoutLimits.ts'), 'utf-8');
const constOf = (name) => Number(new RegExp(`export const ${name} = ([0-9_]+)`).exec(limitsSrc)[1].replace(/_/g, ''));
const MAX = { dim: constOf('LAYOUT_MAX_DIM'), cells: constOf('LAYOUT_MAX_CELLS'), furniture: constOf('LAYOUT_MAX_FURNITURE'), pets: constOf('LAYOUT_MAX_PETS'), areas: constOf('LAYOUT_MAX_AREAS') };
check('bounds: cols/rows within LAYOUT_MAX_DIM, cells within LAYOUT_MAX_CELLS', L.cols <= MAX.dim && L.rows <= MAX.dim && L.cols * L.rows <= MAX.cells, { cols: L.cols, rows: L.rows, cells: L.cols * L.rows, cap: MAX.cells });
check('bounds: furniture / pets / areas within caps', L.furniture.length <= MAX.furniture && (L.pets ?? []).length <= MAX.pets && (L.areas ?? []).length <= MAX.areas, { furniture: L.furniture.length, pets: L.pets.length, areas: L.areas.length });

// preservation
let cellDiffs = 0;
for (let r = 0; r < F.rows; r++)
  for (let c = 0; c < F.cols; c++) {
    const i = r * L.cols + c;
    const j = r * F.cols + c;
    if (L.tiles[i] !== F.tiles[j] || !same(L.tileColors[i], F.tileColors[j]) || !same(L.carpetTiles?.[i], F.carpetTiles?.[j]) || !same(L.areaTiles?.[i], F.areaTiles?.[j])) cellDiffs++;
  }
check('preserved forest block byte-identical at (0,0): tiles, tileColors, carpetTiles, areaTiles', cellDiffs === 0, { cells: F.cols * F.rows, differing: cellDiffs });
const byUid = new Map(L.furniture.map((f) => [f.uid, f]));
const furnDiff = F.furniture.filter((f) => !same(byUid.get(f.uid), f)).map((f) => f.uid);
check('all 2,945 forest furniture records present and unchanged', furnDiff.length === 0 && F.furniture.length === 2945, { differing: furnDiff.slice(0, 10) });
check('both pets unchanged', same(L.pets, F.pets) && L.pets.length === 2, { pets: L.pets.map((p) => p.id) });
const pcs = L.furniture.filter((f) => /^PC_/.test(f.type));
check('41 computers, all of them the forest input\'s own records', pcs.length === 41 && pcs.every((f) => same(f, F.furniture.find((g) => g.uid === f.uid))), { count: pcs.length });
check('uids unique', new Set(L.furniture.map((f) => f.uid)).size === L.furniture.length);

// home + seats
const home = L.home;
check('home is the palace block (56,40)-(120,104)', same(home, { c0: 56, r0: 40, c1: 120, r1: 104 }), home);
const seats = L.furniture.filter((f) => entry(f.type)?.category === 'chairs');
const seatsOut = seats.filter((f) => !(f.col >= home.c0 && f.col < home.c1 && f.row >= home.r0 && f.row < home.r1));
check('every seat (chair) lies inside the home', seatsOut.length === 0, { seats: seats.length, outside: seatsOut.map((f) => f.uid).slice(0, 10) });

// walkability — a DUPLICATE of the runtime rule (geometry only; see the header)
const WATER = new Set([14, 22, 23]);
const constantsSrc = fs.readFileSync(path.join(ROOT, 'webview-ui/src/constants.ts'), 'utf-8');
const runtimeWater = /export const WATER_FLOOR_TILES[^=]*= new Set\(\[([^\]]*)\]\)/.exec(constantsSrc);
const runtimeWaterSet = runtimeWater ? runtimeWater[1].split(',').map((x) => Number(x.trim())) : null;
check(
  'GEOMETRY: the duplicate water set equals the runtime WATER_FLOOR_TILES (read from source)',
  runtimeWaterSet && runtimeWaterSet.length === WATER.size && runtimeWaterSet.every((t) => WATER.has(t)),
  { duplicate: [...WATER], runtime: runtimeWaterSet },
);
const W = L.cols;
const H = L.rows;
const blocked = new Uint8Array(W * H);
const solidOf = new Map();
for (const f of L.furniture) {
  const e = entry(f.type);
  if (!e) continue;
  for (let dr = e.bg; dr < e.h; dr++)
    for (let dc = 0; dc < e.w; dc++) {
      const c = f.col + dc;
      const r = f.row + dr;
      if (c >= 0 && r >= 0 && c < W && r < H) {
        blocked[r * W + c] = 1;
        solidOf.set(r * W + c, f.uid);
      }
    }
}
const baseWalk = (tiles) => (c, r) => {
  if (c < 0 || r < 0 || c >= W || r >= H) return false;
  const t = tiles[r * W + c];
  return t !== 0 && t !== 255 && !WATER.has(t) && !blocked[r * W + c];
};
const walk = baseWalk(L.tiles);
function bfs(origins, walkable) {
  const seen = new Uint8Array(W * H);
  const q = [];
  for (const [c, r] of origins) if (walkable(c, r)) {
    seen[r * W + c] = 1;
    q.push(r * W + c);
  }
  for (let k = 0; k < q.length; k++) {
    const p = q[k];
    const c = p % W;
    const r = (p - c) / W;
    for (const [nc, nr] of [[c + 1, r], [c - 1, r], [c, r + 1], [c, r - 1]])
      if (walkable(nc, nr) && !seen[nr * W + nc]) {
        seen[nr * W + nc] = 1;
        q.push(nr * W + nc);
      }
  }
  return seen;
}
const waterCells = Object.fromEntries([...WATER].map((t) => [t, L.tiles.filter((x) => x === t).length]));
check('GEOMETRY: each water floor (pond 14, sea 22, river 23) is present in the world', Object.values(waterCells).every((n) => n > 0), { waterCells });

// routes: from the palace interior to every district
const palaceOrigin = (() => {
  for (let r = 70; r < 90; r++) for (let c = 80; c < 100; c++) if (walk(c, r)) return [c, r];
  return null;
})();
const reach = bfs([palaceOrigin], walk);
const districtReach = {};
for (const a of L.areas) {
  let cells = 0;
  let reached = 0;
  for (let i = 0; i < W * H; i++)
    if (L.areaTiles[i] === a.label && walk(i % W, (i - (i % W)) / W)) {
      cells++;
      if (reach[i]) reached++;
    }
  districtReach[a.label] = { walkableCells: cells, reachedFromPalace: reached, fraction: +(reached / Math.max(1, cells)).toFixed(3) };
}
check('GEOMETRY: every district is reachable on foot from the palace interior', Object.values(districtReach).every((d) => d.reachedFromPalace > 0), { origin: palaceOrigin, districtReach });

// bridges: carry the crossing, and are necessary for it
const deckCells = (b) => {
  const out = [];
  for (let r = b.r0; r < b.r1; r++) for (let c = b.c0; c < b.c1; c++) if (L.tiles[r * W + c] === 24) out.push(r * W + c);
  return out;
};
const recorded = [
  { c0: 248, c1: 251, axis: 'ns', name: 'Valley Road bridge' },
  { c0: 281, c1: 283, axis: 'ns', name: 'harbour avenue bridge' },
  { c0: 205, c1: 207, axis: 'ns', name: 'farm footbridge' },
];
const bridgeReport = [];
const WINDOW = 10; // tiles either side of a bridge: the crossing is judged LOCALLY
for (const b of recorded) {
  // the river band under this bridge: the first contiguous water run in column c0 - 1
  let rTop = -1;
  let rBot = -1;
  for (let r = 120; r < 175; r++) {
    const t = L.tiles[r * W + b.c0 - 1];
    if (WATER.has(t)) {
      if (rTop < 0) rTop = r;
      rBot = r;
    } else if (rTop >= 0) break;
  }
  const north = [b.c0, rTop - 3];
  const south = [b.c0, rBot + 3];
  const deck = new Set(deckCells({ ...b, r0: rTop - 4, r1: rBot + 5 }));
  const inWindow = (c) => c >= b.c0 - WINDOW && c < b.c1 + WINDOW;
  const local = (tiles) => {
    const w = baseWalk(tiles);
    return (c, r) => inWindow(c) && w(c, r);
  };
  const crossing = bfs([north], local(L.tiles))[south[1] * W + south[0]] === 1;
  const noDeckTiles = L.tiles.map((t, i) => (deck.has(i) ? 23 : t));
  const without = bfs([north], local(noDeckTiles))[south[1] * W + south[0]] === 1;
  bridgeReport.push({ ...b, river: [rTop, rBot], north, south, window: WINDOW, crossing, crossingWithoutDeck: without, deckCells: deck.size });
}
// the Highland stream stone bridge: an east-west deck over the north-south stream — the SAME
// proof as its peers (crossing within a local window, and gone with the deck removed)
{
  const sb = L.furniture.find((f) => f.type === 'DETAIL_BRIDGE_STONE');
  const row = sb ? sb.row + 2 : -1; // the deck row the trail runs on
  const deckCols = [];
  if (sb) for (let c = sb.col - 4; c < sb.col + 10; c++) if (L.tiles[row * W + c] === 24) deckCols.push(c);
  const spansStream = deckCols.some((c) => WATER.has(L.tiles[(row - 2) * W + c]) && WATER.has(L.tiles[(row + 2) * W + c]));
  const west = [Math.min(...deckCols) - 3, row];
  const east = [Math.max(...deckCols) + 3, row];
  const inWindow = (r) => r >= row - WINDOW && r <= row + WINDOW;
  const local = (tiles) => {
    const w = baseWalk(tiles);
    return (c, r) => inWindow(r) && c >= west[0] - 2 && c <= east[0] + 2 && w(c, r);
  };
  const deck = new Set(deckCols.flatMap((c) => [-1, 0, 1].map((d) => (row + d) * W + c)).filter((i) => L.tiles[i] === 24));
  const crossing = deckCols.length > 0 && bfs([west], local(L.tiles))[east[1] * W + east[0]] === 1;
  const noDeckTiles = L.tiles.map((t, i) => (deck.has(i) ? 23 : t));
  const without = deckCols.length > 0 && bfs([west], local(noDeckTiles))[east[1] * W + east[0]] === 1;
  bridgeReport.push({ name: 'Highland stream stone bridge (ew)', axis: 'ew', row, deckCols, spansStream, west, east, window: WINDOW, crossing: crossing && spansStream, crossingWithoutDeck: without, deckCells: deck.size });
}
check('GEOMETRY: each bridge (4) carries a bank-to-bank crossing and is NECESSARY for it (no ford)', bridgeReport.length === 4 && bridgeReport.every((b) => b.crossing && b.crossingWithoutDeck === false), bridgeReport);

// arches: openings walkable, legs blocking
const archReport = [];
for (const f of L.furniture.filter((x) => /_P\d$/.test(x.type))) {
  const e = entry(f.type);
  const open = e.bg >= e.h;
  const cells = [];
  for (let dr = 0; dr < e.h; dr++) for (let dc = 0; dc < e.w; dc++) cells.push([f.col + dc, f.row + dr]);
  const bottom = cells.filter(([, r]) => r === f.row + e.h - 1);
  archReport.push({ uid: f.uid, type: f.type, opening: open, bottomWalkable: bottom.map(([c, r]) => walk(c, r)), blockedBySelf: bottom.map(([c, r]) => solidOf.get(r * W + c) === f.uid) });
}
// an opening must be WALKABLE with every obstacle counted (bottomWalkable: the global walk rule,
// other furniture and water included), not merely free of its own footprint; a leg blocks
check(
  'GEOMETRY: arch/gate openings walkable with all obstacles, legs block (drawn opening == route)',
  archReport.length > 0 &&
    archReport.every((a) =>
      a.opening
        ? a.bottomWalkable.every((x) => x) && a.blockedBySelf.every((x) => !x)
        : a.blockedBySelf.every((x) => x) && a.bottomWalkable.every((x) => !x),
    ),
  archReport,
);
const tunnelOpen = archReport.find((a) => a.type === 'AQUA_TUNNEL_ARCH_P2');
check('GEOMETRY: the Grand Aquarium door is reachable through the tunnel arch opening', tunnelOpen && reach[(186) * W + 287] === 1, { tunnelOpen, doorReached: reach[186 * W + 287] === 1 });

const failed = checks.filter((c) => !c.ok);
console.log(
  JSON.stringify(
    {
      world: path.relative(ROOT, worldPath),
      sha256: sha(worldBytes),
      claim: 'preservation + bounds on the bytes; routes, bridges, arches and water are GEOMETRY ONLY under a duplicated walk rule — runtime walkability is proven by webview-ui/test/metropolisRoutes.test.ts',
      passed: checks.length - failed.length,
      failed: failed.length,
      checks,
    },
    null,
    2,
  ),
);
process.exit(failed.length ? 1 : 0);
