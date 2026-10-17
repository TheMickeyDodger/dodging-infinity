#!/usr/bin/env node
/**
 * Composes the CONNECTED METROPOLIS WORLD (352 x 224 = 78,848 cells): the verified VAST FOREST
 * world (packaging/di/worlds/vast-forest.json, sha256 ee3430d1...671d82 — the palace already
 * inside it at +56,+40) placed WHOLE by PURE TRANSLATION (0, 0) into the north-west quadrant, and
 * eight new districts around it, joined by roads, trails, bridges and boardwalks:
 *
 *   north-west  PALACE FOREST (preserved, 176 x 112)   north    METROPOLIS (132 x 112)
 *   north-east  NORTH SHORE (dunes, beach, lighthouse)  south-w  AUTUMN WILDWOOD (forest)
 *   south-mid   MIRROR LAKES  /  SILVER PINE HIGHLANDS (forest)
 *   south       RIVER VALLEY FARMLAND                   south-e  HARBOUR / AQUARIUM QUARTER
 *
 *   node scripts/build-metropolis-world.mjs [--out <file>]     (default packaging/di/worlds/metropolis.json)
 *
 * PRESERVATION (checked by the builder before it writes, and reported): every forest-world cell —
 * tiles, tileColors, carpetTiles, areaTiles — lands at the same (col, row), every one of its 2,945
 * furniture records keeps uid, type, col, row and colour, and its two pets are kept unchanged. No
 * new furniture overlaps the forest block, no new tile is painted inside it. The forest world's
 * own approved north-gateway change set (4 threshold cells, 5 plant re-sites) is INPUT, inherited
 * unchanged. Connecting change to preserved content in THIS world: none — roads END at the forest
 * block's edge on walkable forest floor (the forest's boundary is forest floor everywhere).
 *
 * HOME: the layout stores `home` = the palace block (56,40)-(120,104) explicitly, so the Office
 * view, seat assignment and agent/pet confinement are anchored to the palace itself; no content
 * elsewhere (walls, seating, deck-style furniture) can move or stretch it.
 *
 * Composition (deterministic, fixed seeds): organic woods from a noise density field (canopy,
 * understory, thickets, clearings, species zones, ragged edges) with BIOME VARIANT sprites
 * (scripts/generate-biome-variants.mjs — foliage-only recolour, never a whole-sprite hue
 * rotation); graded shores (deep -> shallows -> wet sand -> beach -> dune/bank); a metropolis of
 * filled blocks with a varied skyline, plazas and street planting; satellite offices built from
 * the palace's own room patterns (desk rows, sofa lounges, meeting tables, wall art rows, carpets,
 * fish tanks, plants) using LOUNGE_* DECOR seating (scripts/generate-lounge-seating.mjs) so no seat
 * exists outside the palace. Every new item is 'decor' or a desk/table and every sprite is STATIC.
 */
import crypto from 'node:crypto';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const args = process.argv.slice(2);
const OUT = args.includes('--out')
  ? path.resolve(args[args.indexOf('--out') + 1])
  : path.join(ROOT, 'packaging/di/worlds/metropolis.json');
const FOREST_FILE = path.join(ROOT, 'packaging/di/worlds/vast-forest.json');
export const FOREST_SHA256 = 'ee3430d13b7368579f0a0ecfe5ea891534b88839413a951b16e8f7b73d671d82';

export const W = 352;
export const H = 224;
export const FOREST_DCOL = 0;
export const FOREST_DROW = 0;
/** The palace block inside the forest world (+56,+40, 64 x 64), translated. */
export const HOME = { c0: 56 + FOREST_DCOL, r0: 40 + FOREST_DROW, c1: 120 + FOREST_DCOL, r1: 104 + FOREST_DROW };

// ── tiles (webview TileType; FLOOR_15.. are generate-metropolis-floors.mjs's APPENDED patterns) ──
const T = {
  WALL: 0,
  GRASS: 10,
  FOREST: 11,
  TRAIL: 12,
  DECK: 13,
  POND: 14,
  ASPHALT: 15,
  DASH_EW: 16,
  DASH_NS: 17,
  PAVEMENT: 18,
  COBBLE: 19,
  SAND: 20,
  SOIL: 21,
  SEA: 22,
  RIVER: 23,
  BOARD: 24,
  CHECKER: 25,
  PARQUET: 26,
  ZEBRA: 27,
  CROPS: 28,
};
const WATER = new Set([T.SEA, T.RIVER, T.POND]);
const c4 = (h, s, b, c) => ({ h, s, b, c });

// ── districts: [label, c0, r0, c1, r1, area colour] (c1/r1 exclusive) ───────
export const DISTRICTS = [
  ['Metropolis', 176, 0, 308, 112, '#c08040'],
  ['North Shore', 308, 0, 352, 112, '#40a0d0'],
  ['Autumn Wildwood', 0, 112, 88, 224, '#d06020'],
  ['Mirror Lakes', 88, 112, 176, 168, '#3080c0'],
  ['Silver Pine Highlands', 88, 168, 176, 224, '#70a0a0'],
  ['River Valley Farmland', 176, 112, 264, 224, '#a0b040'],
  ['Harbour', 264, 112, 352, 168, '#2060a0'],
  ['Aquarium Quarter', 264, 168, 352, 224, '#20b0c0'],
];
export const FOREST_BLOCK = { c0: 0, r0: 0, c1: 176, r1: 112 };

// ── deterministic randomness and noise ──────────────────────────────────────
function rng(seed) {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}
function hash2(x, y, seed) {
  let h = (Math.imul(x, 374761393) + Math.imul(y, 668265263) + Math.imul(seed, 982451653)) | 0;
  h = Math.imul(h ^ (h >>> 13), 1274126177);
  return ((h ^ (h >>> 16)) >>> 0) / 4294967296;
}
/** Smooth value noise in [0,1). */
function vnoise(x, y, seed) {
  const x0 = Math.floor(x);
  const y0 = Math.floor(y);
  const fx = x - x0;
  const fy = y - y0;
  const sx = fx * fx * (3 - 2 * fx);
  const sy = fy * fy * (3 - 2 * fy);
  const a = hash2(x0, y0, seed);
  const b = hash2(x0 + 1, y0, seed);
  const c = hash2(x0, y0 + 1, seed);
  const d = hash2(x0 + 1, y0 + 1, seed);
  return a + (b - a) * sx + (c - a) * sy + (a - b - c + d) * sx * sy;
}
/** Fractal noise (3 octaves) in ~[0,1). */
const fbm = (x, y, seed) => (vnoise(x, y, seed) * 0.57 + vnoise(x * 2.1, y * 2.1, seed + 7) * 0.29 + vnoise(x * 4.3, y * 4.3, seed + 13) * 0.14);

// ── catalogue: footprints from the bundled manifests ────────────────────────
const CATALOG = new Map();
{
  const dir = path.join(ROOT, 'webview-ui/public/assets/furniture');
  const visit = (node, inh) => {
    const props = {
      category: node.category ?? inh.category,
      bg: node.backgroundTiles ?? inh.bg ?? 0,
      surface: node.canPlaceOnSurfaces ?? inh.surface ?? false,
      wall: node.canPlaceOnWalls ?? inh.wall ?? false,
    };
    if (node.type === 'asset') CATALOG.set(node.id, { w: node.footprintW, h: node.footprintH, ...props });
    for (const m of node.members ?? []) visit(m, props);
  };
  for (const id of fs.readdirSync(dir))
    visit(JSON.parse(fs.readFileSync(path.join(dir, id, 'manifest.json'), 'utf-8')), {});
}
const entryOf = (type) => CATALOG.get(type.replace(/:left$/, ''));

// ── the preserved forest ────────────────────────────────────────────────────
const forestBytes = fs.readFileSync(FOREST_FILE);
const forestSha = crypto.createHash('sha256').update(forestBytes).digest('hex');
if (forestSha !== FOREST_SHA256) {
  console.error(`REFUSING: ${FOREST_FILE} sha256 ${forestSha} != pinned ${FOREST_SHA256}`);
  process.exit(1);
}
const F = JSON.parse(forestBytes);

const N = W * H;
const tiles = new Array(N).fill(T.GRASS);
const tileColors = new Array(N).fill(null);
const carpetTiles = new Array(N).fill(null);
const areaTiles = new Array(N).fill(null);
const idx = (c, r) => r * W + c;
const inForest = (c, r) =>
  c >= FOREST_BLOCK.c0 && c < FOREST_BLOCK.c1 && r >= FOREST_BLOCK.r0 && r < FOREST_BLOCK.r1;
const inWorld = (c, r) => c >= 0 && r >= 0 && c < W && r < H;
for (let r = 0; r < F.rows; r++)
  for (let c = 0; c < F.cols; c++) {
    const i = idx(c + FOREST_DCOL, r + FOREST_DROW);
    const j = r * F.cols + c;
    tiles[i] = F.tiles[j];
    tileColors[i] = F.tileColors[j] ?? null;
    carpetTiles[i] = F.carpetTiles?.[j] ?? null;
    areaTiles[i] = F.areaTiles?.[j] ?? null;
  }

// ── painting (never inside the forest block) ────────────────────────────────
const GROUND = {
  [T.GRASS]: [c4(100, 40, -15, 0), c4(96, 40, -13, 0), c4(104, 38, -17, 0)],
  [T.FOREST]: [c4(98, 30, -36, 0), c4(94, 30, -33, 0)],
  [T.TRAIL]: [c4(32, 34, -14, 0), c4(28, 30, -18, 0)],
  [T.ASPHALT]: [c4(220, 8, -42, 10)],
  [T.DASH_EW]: [c4(220, 8, -42, 10)],
  [T.DASH_NS]: [c4(220, 8, -42, 10)],
  [T.ZEBRA]: [c4(220, 8, -42, 10)],
  [T.PAVEMENT]: [c4(40, 8, -8, 0), c4(36, 8, -10, 0)],
  [T.COBBLE]: [c4(30, 22, -12, 10)],
  [T.SAND]: [c4(42, 48, 4, 0), c4(40, 44, 1, 0)],
  [T.SOIL]: [c4(24, 42, -32, 10)],
  [T.SEA]: [c4(205, 62, -28, 10)],
  [T.RIVER]: [c4(195, 52, -14, 0), c4(198, 50, -17, 0)],
  [T.POND]: [c4(196, 50, -20, 0)],
  [T.BOARD]: [c4(30, 40, -12, 0)],
  [T.CHECKER]: [c4(212, 12, -6, 0)],
  [T.PARQUET]: [c4(28, 46, -16, 0)],
  [T.CROPS]: [c4(84, 48, -22, 0), c4(52, 56, -6, 0)],
  [T.DECK]: [c4(30, 38, -6, 0)],
};
const WALL_COLOUR = c4(226, 28, -100, -45); // the palace's own wall colour
/** Per-district ground overrides: what makes each wood / place read as its own. */
const DISTRICT_GROUND = {
  'Autumn Wildwood': {
    [T.FOREST]: [c4(30, 42, -34, 0), c4(24, 44, -36, 0), c4(36, 40, -31, 0)],
    [T.GRASS]: [c4(52, 44, -16, 0), c4(46, 40, -18, 0), c4(58, 40, -14, 0)],
  },
  'Silver Pine Highlands': {
    [T.FOREST]: [c4(150, 22, -34, 0), c4(140, 20, -31, 0), c4(160, 18, -36, 0)],
    [T.GRASS]: [c4(120, 24, -10, 0), c4(110, 22, -6, 0), c4(130, 20, -12, 0)],
  },
  'Mirror Lakes': { [T.GRASS]: [c4(108, 44, -14, 0), c4(112, 42, -12, 0), c4(104, 46, -16, 0)] },
};
function districtAt(c, r) {
  for (const d of DISTRICTS) if (c >= d[1] && c < d[3] && r >= d[2] && r < d[4]) return d[0];
  return null;
}
const pr = rng(7);
function paint(c, r, t, colour) {
  if (!inWorld(c, r) || inForest(c, r)) return;
  const i = idx(c, r);
  tiles[i] = t;
  if (t === T.WALL) {
    tileColors[i] = WALL_COLOUR;
    return;
  }
  if (colour) {
    tileColors[i] = colour;
    return;
  }
  const palette = DISTRICT_GROUND[districtAt(c, r)]?.[t] ?? GROUND[t];
  tileColors[i] = palette ? palette[Math.floor(pr() * palette.length)] : null;
}
const tileAt = (c, r) => (inWorld(c, r) ? tiles[idx(c, r)] : -1);
function rect(c0, r0, c1, r1, t, colour) {
  for (let r = r0; r < r1; r++) for (let c = c0; c < c1; c++) paint(c, r, t, colour);
}
/** A trail/road along a polyline, `w` wide; `wobble` bends it with noise (organic trails). */
function path_(points, w, t, wobble = 0, seed = 1) {
  for (let k = 0; k + 1 < points.length; k++) {
    const [c0, r0] = points[k];
    const [c1, r1] = points[k + 1];
    const steps = Math.max(Math.abs(c1 - c0), Math.abs(r1 - r0));
    const nx = -(r1 - r0) / Math.max(1, steps);
    const ny = (c1 - c0) / Math.max(1, steps);
    for (let s = 0; s <= steps; s++) {
      const off = wobble ? (fbm(s / 9, k * 3.7, seed) - 0.5) * 2 * wobble * Math.sin((Math.PI * s) / steps) : 0;
      const c = Math.round(c0 + ((c1 - c0) * s) / steps + nx * off);
      const r = Math.round(r0 + ((r1 - r0) * s) / steps + ny * off);
      const h = Math.floor(w / 2);
      for (let dr = -h; dr < w - h; dr++) for (let dc = -h; dc < w - h; dc++) paint(c + dc, r + dr, t);
    }
  }
}

// ── furniture placement with an occupancy grid ──────────────────────────────
const furniture = F.furniture.map((f) => ({ ...f, col: f.col + FOREST_DCOL, row: f.row + FOREST_DROW }));
const occupied = new Uint8Array(N); // 1 = floor item, 2 = desk surface free for surface items
for (const f of furniture) {
  const e = entryOf(f.type);
  if (!e) continue;
  for (let dr = 0; dr < e.h; dr++)
    for (let dc = 0; dc < e.w; dc++) if (inWorld(f.col + dc, f.row + dr)) occupied[idx(f.col + dc, f.row + dr)] = 1;
}
let uidN = 0;
const placedByType = new Map();
const record = (type, col, row, tag, color) => {
  const rec = { uid: `mw-${tag ?? 'x'}-${++uidN}`, type, col, row };
  if (color) rec.color = color;
  furniture.push(rec);
  placedByType.set(type, (placedByType.get(type) ?? 0) + 1);
};
/**
 * Place `type` with its footprint's top-left at (col, row). Refused (returns false) if any
 * footprint cell is outside the world, inside the forest block, occupied, on a wall, or (for its
 * SOLID rows) on a tile not in `on` (default: any non-water ground). A surface item
 * (canPlaceOnSurfaces) may instead sit wholly on a free desk surface.
 */
function place(type, col, row, opts = {}) {
  const e = entryOf(type);
  if (!e) throw new Error(`unknown furniture type ${type}`);
  if (e.category === 'chairs') throw new Error(`${type} is a chair: no seats outside the palace`);
  if (type.startsWith('DECK_')) throw new Error(`${type}: DECK_* is the palace deck's own family`);
  if (/^PC/.test(type)) throw new Error(`${type}: computers stay the palace's 41`);
  let onDesk = e.surface;
  for (let dr = 0; dr < e.h && onDesk; dr++)
    for (let dc = 0; dc < e.w; dc++) if (!inWorld(col + dc, row + dr) || occupied[idx(col + dc, row + dr)] !== 2) onDesk = false;
  if (onDesk) {
    for (let dr = 0; dr < e.h; dr++) for (let dc = 0; dc < e.w; dc++) occupied[idx(col + dc, row + dr)] = 3;
    record(type, col, row, opts.tag, opts.color);
    return true;
  }
  for (let dr = 0; dr < e.h; dr++)
    for (let dc = 0; dc < e.w; dc++) {
      const c = col + dc;
      const r = row + dr;
      if (!inWorld(c, r) || inForest(c, r) || occupied[idx(c, r)]) return false;
      const t = tiles[idx(c, r)];
      if (t === T.WALL) return false;
      if (dr >= e.bg) {
        if (opts.on ? !opts.on.has(t) : WATER.has(t)) return false;
      }
    }
  const mark = e.category === 'desks' ? 2 : 1;
  for (let dr = 0; dr < e.h; dr++) for (let dc = 0; dc < e.w; dc++) occupied[idx(col + dc, row + dr)] = mark;
  record(type, col, row, opts.tag, opts.color);
  return true;
}
/** A wall-category item hung on a WALL row: its bottom footprint row must lie on wall tiles. */
function placeWall(type, col, rowOfWall, tag = 'wall') {
  const e = entryOf(type);
  const row = rowOfWall - (e.h - 1);
  for (let dc = 0; dc < e.w; dc++) if (tileAt(col + dc, rowOfWall) !== T.WALL) return false;
  for (let dr = 0; dr < e.h - 1; dr++)
    for (let dc = 0; dc < e.w; dc++) if (inWorld(col + dc, row + dr) && occupied[idx(col + dc, row + dr)]) return false;
  for (let dr = 0; dr < e.h - 1; dr++) for (let dc = 0; dc < e.w; dc++) if (inWorld(col + dc, row + dr)) occupied[idx(col + dc, row + dr)] = 1;
  record(type, col, row, tag);
  return true;
}
const pick = (r, list) => list[Math.floor(r() * list.length)];
function weighted(r, types) {
  const total = types.reduce((n, [, wgt]) => n + wgt, 0);
  let roll = r() * total;
  for (const [t, wgt] of types) if ((roll -= wgt) <= 0) return t;
  return types[0][0];
}
/** Scatter `count` attempts of weighted types over a rectangle. */
function scatter(box, types, count, opts = {}) {
  const [c0, r0, c1, r1] = box;
  const r = rng(opts.seed ?? 99);
  for (let k = 0; k < count; k++)
    place(weighted(r, types), c0 + Math.floor(r() * (c1 - c0)), r0 + Math.floor(r() * (r1 - r0)), opts);
}
const ON = (...ts) => new Set(ts);

// ════════════════════════════════════════════════════════════════════════════
// 1. GROUND
// ════════════════════════════════════════════════════════════════════════════
rect(0, 112, 88, 224, T.FOREST); // Autumn Wildwood
rect(88, 168, 176, 224, T.FOREST); // Silver Pine Highlands
rect(88, 112, 176, 168, T.GRASS); // Mirror Lakes
rect(176, 112, 264, 224, T.GRASS); // farmland base
rect(176, 0, 308, 112, T.PAVEMENT); // metropolis base: pavement
rect(264, 112, 318, 224, T.PAVEMENT); // harbour + aquarium quarter land

// ── THE COAST: a graded band, its line from noise (no repeating stamp) ──
// deep sea -> mid -> shallows -> wet sand -> beach -> dune (or a built quay in the harbour)
const shoreCol = (r) => Math.round(318 + (fbm(r / 16, 0.5, 301) - 0.5) * 7 + (fbm(r / 41, 3.3, 302) - 0.5) * 5);
const isHarbourRow = (r) => r >= 112 && r < 168;
for (let r = 0; r < H; r++) {
  const s = shoreCol(r);
  const wet = 1 + Math.round(fbm(r / 7, 1.1, 303) * 1.6);
  const beach = 3 + Math.round(fbm(r / 11, 2.2, 304) * 4);
  const dune = 2 + Math.round(fbm(r / 9, 4.4, 305) * 3);
  for (let c = 300; c < W; c++) {
    const d = c - s; // >= 0: water
    if (d >= 0) {
      const shallowW = 2 + fbm(r / 8, c / 8, 306) * 3;
      if (d < shallowW) paint(c, r, T.RIVER, c4(184, 46, Math.round(-2 - d * 3), 0));
      else {
        const depth = Math.min(1, (d - shallowW) / 14);
        paint(c, r, T.SEA, c4(Math.round(196 + depth * 12), 60, Math.round(-12 - depth * 20 + (fbm(c / 6, r / 6, 307) - 0.5) * 6), 10));
      }
    } else if (isHarbourRow(r)) {
      if (c >= 311) paint(c, r, T.PAVEMENT); // the built quay meets deep water at a wall
    } else if (-d <= wet) paint(c, r, T.SAND, c4(38, 36, -16, 0));
    else if (-d <= wet + beach) paint(c, r, T.SAND, c4(42, 46, Math.round(2 + (fbm(c / 5, r / 5, 308) - 0.5) * 6), 0));
    else if (-d <= wet + beach + dune && c >= 308) paint(c, r, T.SAND, c4(46, 26, -10, 0));
  }
  if (isHarbourRow(r)) for (let c = s; c < s + 3; c++) paint(c, r, T.SEA, c4(200, 58, -24, 10)); // dredged quay water
}

// ── METROPOLIS street grid ──
const AVENUES_EW = [10, 38, 66, 94]; // top row of each 3-row avenue
const AVENUES_NS = [184, 216, 248, 280]; // left col of each 3-col avenue
for (const r of AVENUES_EW) {
  rect(176, r, 308, r + 3, T.ASPHALT);
  rect(176, r + 1, 308, r + 2, T.DASH_EW);
}
for (const c of AVENUES_NS) {
  rect(c, 0, c + 3, 112, T.ASPHALT);
  rect(c + 1, 0, c + 2, 112, T.DASH_NS);
}
for (const r of AVENUES_EW) for (const c of AVENUES_NS) rect(c, r, c + 3, r + 3, T.ASPHALT);
for (const r of AVENUES_EW)
  for (const c of AVENUES_NS) {
    rect(c - 2, r, c, r + 3, T.ZEBRA);
    rect(c + 3, r, c + 5, r + 3, T.ZEBRA);
  }
// the promenade (boardwalk) along the waterfront, full height
for (let r = 0; r < H; r++) rect(308, r, 310, r + 1, T.BOARD);
// civic set-pieces
rect(219, 41, 248, 66, T.COBBLE); // Civic Plaza
rect(251, 69, 280, 94, T.GRASS); // Central Park
rect(187, 69, 216, 94, T.GRASS); // Companion Park
rect(283, 41, 308, 66, T.COBBLE); // Waterfront Square
// Central Park paths and its pond
path_([[251, 81], [280, 81]], 2, T.COBBLE);
path_([[265, 69], [265, 94]], 2, T.COBBLE);
for (let r = 71; r < 79; r++) for (let c = 268; c < 278; c++) if (((c - 273) / 5) ** 2 + ((r - 75) / 3.6) ** 2 <= 1) paint(c, r, T.POND);

// ── MIRROR LAKES: organic lakes with graded water and banks ──
const lakeMask = new Uint8Array(N);
function lake(cx, cy, rx, ry, seed) {
  for (let r = Math.floor(cy - ry - 6); r <= cy + ry + 6; r++)
    for (let c = Math.floor(cx - rx - 6); c <= cx + rx + 6; c++) {
      if (!inWorld(c, r) || inForest(c, r)) continue;
      const k = 1 + (fbm(c / 7, r / 7, seed) - 0.5) * 0.55;
      if (((c - cx) / (rx * k)) ** 2 + ((r - cy) / (ry * k)) ** 2 <= 1) lakeMask[idx(c, r)] = 1;
    }
}
lake(112, 136, 17, 12, 11);
lake(152, 133, 16, 11, 12);
// an island in the east lake
for (let r = 128; r < 139; r++) for (let c = 147; c < 158; c++) if (((c - 152) / 4.5) ** 2 + ((r - 133) / 3.2) ** 2 <= 1) lakeMask[idx(c, r)] = 0;
// tarns in the woods (open water clearings)
lake(30, 196, 9, 6, 13); // Wildwood tarn
lake(150, 210, 8, 5, 14); // Highland tarn
const distToLand = (c, r, max) => {
  for (let d = 1; d <= max; d++)
    for (let dr = -d; dr <= d; dr++)
      for (let dc = -d; dc <= d; dc++) {
        if (Math.max(Math.abs(dr), Math.abs(dc)) !== d) continue;
        const cc = c + dc;
        const rr = r + dr;
        if (inWorld(cc, rr) && !lakeMask[idx(cc, rr)]) return d;
      }
  return max + 1;
};
const distToWater = (c, r, max) => {
  for (let d = 1; d <= max; d++)
    for (let dr = -d; dr <= d; dr++)
      for (let dc = -d; dc <= d; dc++) {
        if (Math.max(Math.abs(dr), Math.abs(dc)) !== d) continue;
        const cc = c + dc;
        const rr = r + dr;
        if (inWorld(cc, rr) && lakeMask[idx(cc, rr)]) return d;
      }
  return max + 1;
};
for (let r = 112; r < H; r++)
  for (let c = 0; c < 176; c++) {
    if (inForest(c, r)) continue;
    if (lakeMask[idx(c, r)]) {
      const d = distToLand(c, r, 6);
      paint(c, r, d <= 2 ? T.RIVER : T.POND, d <= 2 ? c4(178, 40, Math.round(-4 - d * 3), 0) : c4(Math.round(196 + d * 2), 50, Math.round(-14 - d * 2.5), 0));
    } else {
      const d = distToWater(c, r, 3);
      if (d === 1) paint(c, r, T.SAND, c4(34, 30, -18, 0)); // wet mud bank
      else if (d === 2 && fbm(c / 4, r / 4, 21) > 0.45) paint(c, r, T.SAND, c4(40, 34, -8, 0)); // pebbly shore
      else if (d <= 3 && districtAt(c, r) === 'Mirror Lakes') paint(c, r, T.GRASS, c4(98, 52, -10, 0)); // lush bank grass
    }
  }

// ── THE RIVER: meanders from the east lake through the valley to the harbour basin ──
const riverRow = (c) => Math.round(146 + 5 * Math.sin((c - 160) / 14) + (fbm(c / 20, 0.7, 31) - 0.5) * 4);
for (let c = 160; c < 320; c++) {
  const r0 = riverRow(c);
  for (let k = -1; k < 5; k++) {
    if (k === -1 || k === 4) {
      if (fbm(c / 5, k, 32) > 0.5 && !WATER.has(tileAt(c, r0 + k))) paint(c, r0 + k, T.SAND, c4(36, 32, -14, 0)); // sand bars
      continue;
    }
    paint(c, r0 + k, T.RIVER, k === 0 || k === 3 ? c4(186, 44, -6, 0) : c4(196, 52, -16, 0));
  }
}
path_([[160, 141], [162, 146]], 4, T.RIVER);
rect(296, 140, 318, 158, T.SEA, c4(202, 58, -22, 10)); // harbour basin (river mouth)
// the Highlands spring stream, north into the west lake
for (let r = 147; r < 186; r++) for (let k = 0; k < 2; k++) paint(118 + k + Math.round(2 * Math.sin(r / 6)), r, T.RIVER, c4(188, 48, -8, 0));

// ROADS and TRAILS between districts
rect(176, 66, 184, 69, T.ASPHALT); // Forest Road meets the forest block's east edge
rect(176, 67, 184, 68, T.DASH_EW);
path_([[96, 112], [96, 124], [132, 124], [132, 160], [132, 196]], 2, T.TRAIL, 3, 41);
path_([[96, 124], [70, 130], [44, 150], [30, 180], [50, 210], [80, 200], [110, 196], [132, 196]], 2, T.TRAIL, 4, 42);
path_([[44, 150], [20, 130], [10, 116]], 2, T.TRAIL, 3, 43);
path_([[132, 196], [160, 186], [172, 176], [176, 176]], 2, T.TRAIL, 3, 44);
path_([[249, 112], [249, 200]], 3, T.COBBLE);
path_([[176, 176], [249, 176], [300, 176]], 3, T.COBBLE);
path_([[249, 124], [300, 124]], 3, T.COBBLE);
path_([[176, 124], [249, 124]], 2, T.TRAIL);
path_([[168, 124], [176, 124]], 2, T.TRAIL);
rect(280, 112, 283, 124, T.ASPHALT); // metropolis avenue into the harbour
const bridges = [];
function bridgeNS(c, w) {
  const r0 = Math.min(...Array.from({ length: w }, (_, k) => riverRow(c + k))) - 2;
  const r1 = Math.max(...Array.from({ length: w }, (_, k) => riverRow(c + k))) + 6;
  rect(c, r0, c + w, r1, T.BOARD);
  bridges.push({ c0: c, c1: c + w, r0, r1, axis: 'ns' });
}
path_([[206, 124], [206, 176]], 2, T.TRAIL); // the farm lane; the bridge deck below then carries it over the river
bridgeNS(248, 3);
bridgeNS(281, 2);
bridgeNS(205, 2);
// the Brook Trail: Wildwood -> Mirror Lakes across the Highland stream, on the stone bridge
const streamBridgeRow = 160;
const streamCol = (r) => 118 + Math.round(2 * Math.sin(r / 6));
path_([[88, streamBridgeRow], [132, streamBridgeRow]], 2, T.TRAIL);
const sb0 = Math.min(streamCol(streamBridgeRow - 1), streamCol(streamBridgeRow)) - 2;
const sb1 = Math.max(streamCol(streamBridgeRow - 1), streamCol(streamBridgeRow)) + 4;
for (let r = streamBridgeRow - 1; r <= streamBridgeRow; r++) for (let c = sb0; c < sb1; c++) paint(c, r, T.BOARD);
bridges.push({ c0: sb0, c1: sb1, r0: streamBridgeRow - 1, r1: streamBridgeRow + 1, axis: 'ew' });
// harbour piers into the sea and the basin quay
for (const r of [116, 128, 160]) {
  const s = shoreCol(r);
  rect(s - 2, r, s + 18, r + 2, T.BOARD);
}
rect(300, 140, 302, 158, T.BOARD);
// lake jetties
rect(126, 136, 132, 138, T.BOARD);
rect(150, 120, 152, 126, T.BOARD);

// FARMLAND fields
const FIELDS = [
  [182, 128, 202, 140, T.CROPS, 'wheat'],
  [212, 128, 244, 138, T.CROPS, 'corn'],
  [182, 156, 202, 172, T.SOIL, 'hay'],
  [212, 158, 244, 172, T.CROPS, 'wheat'],
  [182, 182, 244, 196, T.CROPS, 'mixed'],
  [214, 202, 244, 220, T.SOIL, 'orchard'],
];
for (const [c0, r0, c1, r1, t] of FIELDS) rect(c0, r0, c1, r1, t);

// ════════════════════════════════════════════════════════════════════════════
// 2. OFFICES built from the palace's own room patterns (LOUNGE_* = decor seating)
// ════════════════════════════════════════════════════════════════════════════
const OFFICES = [];
const IN = ON(T.PARQUET, T.CHECKER, T.GRASS, T.FOREST, T.COBBLE);
function carpet(c0, r0, c1, r1, variant, color, accentColor, order = 1) {
  for (let r = r0; r < r1; r++)
    for (let c = c0; c < c1; c++) if (!inForest(c, r)) carpetTiles[idx(c, r)] = { variant, color, accentColor, order };
}
/** Carpet schemes per office (main, accent): one deliberate palette per building, like the palace. */
const THEMES = [
  { main: c4(185, 45, -16, 0), accent: c4(45, 60, 6, 0), second: c4(200, 35, -28, 0) }, // teal + gold
  { main: c4(350, 46, -26, 0), accent: c4(45, 55, 4, 0), second: c4(20, 40, -30, 0) }, // burgundy + gold
  { main: c4(120, 34, -26, 0), accent: c4(48, 26, 14, 0), second: c4(90, 30, -32, 0) }, // forest green + cream
  { main: c4(222, 46, -32, 0), accent: c4(45, 12, 18, 0), second: c4(205, 40, -24, 0) }, // navy + ivory
];
const theme = (k) => THEMES[((k % THEMES.length) + THEMES.length) % THEMES.length];
// room templates — each furnishes the interior rect [c0,c1) x [r0,r1); `top` is the wall row above
const ROOM = {
  /** desk rows like the palace's: desk, something on it, a bench in front; plants between */
  studio(c0, r0, c1, r1, top, k) {
    for (let r = r0 + 1; r + 3 <= r1 - 1; r += 4)
      for (let c = c0 + 1; c + 3 <= c1; c += 4) {
        if (place('DESK_FRONT', c, r, { tag: 'office', on: IN })) {
          place(['AQUA_TANK_DESK', 'COFFEE', 'COFFEE'][(c + r + k) % 3], c + (k % 2), r, { tag: 'office' });
          place('LOUNGE_CUSHIONED_BENCH', c + 1, r + 2, { tag: 'office', on: IN });
        }
      }
    for (let c = c0 + 1; c < c1 - 1; c += 3) placeWall(['DOUBLE_BOOKSHELF', 'CLOCK', 'LARGE_PAINTING', 'HANGING_PLANT'][(c + k) % 4], c, top);
    place('PLANT_2', c0, r1 - 2, { tag: 'office', on: IN });
    place('LARGE_PLANT', c1 - 2, r1 - 3, { tag: 'office', on: IN });
  },
  /** a sofa lounge (palace pattern): four sofas round a coffee table, lamps, tank, art */
  lounge(c0, r0, c1, r1, top, k) {
    const groups = Math.max(1, Math.floor((c1 - c0 - 1) / 6));
    for (let g = 0; g < groups; g++) {
      const x = c0 + 1 + g * 6;
      const y = r0 + 2;
      place('LOUNGE_SOFA_FRONT', x + 1, y, { tag: 'office', on: IN });
      place('LOUNGE_SOFA_SIDE', x, y + 1, { tag: 'office', on: IN });
      place('LOUNGE_SOFA_SIDE:left', x + 3, y + 1, { tag: 'office', on: IN });
      place('COFFEE_TABLE', x + 1, y + 1, { tag: 'office', on: IN });
      place('COFFEE', x + 1, y + 2, { tag: 'office' });
      place('LOUNGE_SOFA_BACK', x + 1, y + 3, { tag: 'office', on: IN });
      carpet(x - 1, y - 1, x + 5, y + 5, 1, theme(k).main, theme(k).accent, 2);
    }
    for (let c = c0 + 1; c < c1 - 2; c += 4) placeWall(c % 8 < 4 ? 'LARGE_PAINTING' : 'SMALL_PAINTING', c, top);
    place('AQUA_TANK_PANORAMIC', c0 + 1, r1 - 4, { tag: 'office', on: IN });
    place('DETAIL_LAMP_ORNATE', c1 - 2, r0, { tag: 'office', on: IN });
    place('LARGE_PLANT', c1 - 3, r1 - 4, { tag: 'office', on: IN });
    place('PLANT', c0, r0, { tag: 'office', on: IN });
  },
  /** a meeting room: long table with side chairs (palace pattern), whiteboard, plants */
  meeting(c0, r0, c1, r1, top, k) {
    const x = Math.floor((c0 + c1) / 2) - 1;
    const y = r0 + 2;
    carpet(x - 2, y - 1, x + 5, y + 6, 0, theme(k).second, theme(k).accent);
    place('TABLE_FRONT', x, y, { tag: 'office', on: IN });
    for (const dy of [0, 2]) {
      place('LOUNGE_WOODEN_CHAIR_SIDE', x - 1, y + dy, { tag: 'office', on: IN });
      place('LOUNGE_WOODEN_CHAIR_SIDE:left', x + 3, y + dy, { tag: 'office', on: IN });
    }
    place('COFFEE', x + 1, y + 1, { tag: 'office' });
    placeWall('WHITEBOARD', x, top);
    placeWall('CLOCK', x + 3, top);
    placeWall('SMALL_PAINTING_2', c0 + 1, top);
    place('PLANT_2', c0, r1 - 2, { tag: 'office', on: IN });
    place('PLANT_2', c1 - 1, r1 - 2, { tag: 'office', on: IN });
    place('AQUA_TANK_CYLINDER', c1 - 2, r0, { tag: 'office', on: IN });
  },
  /** a gallery: paintings shoulder to shoulder, sculptures on plinths, benches, ropes */
  gallery(c0, r0, c1, r1, top, k) {
    for (let c = c0; c < c1 - 1; c += 2) placeWall(['LARGE_PAINTING', 'SMALL_PAINTING', 'LARGE_PAINTING', 'SMALL_PAINTING_2'][(c + k) % 4], c, top);
    carpet(c0 + 1, r0 + 3, c1 - 1, r1 - 1, 2, theme(k).main, theme(k).accent);
    place('DETAIL_SCULPTURE', c0 + 1, r0 + 3, { tag: 'office', on: IN });
    place('DETAIL_OBELISK', c1 - 3, r0 + 3, { tag: 'office', on: IN });
    place('AQUA_BENCH', Math.floor((c0 + c1) / 2) - 1, r0 + 5, { tag: 'office', on: IN });
    place('AQUA_STANCHION', c0 + 1, r0 + 1, { tag: 'office', on: IN });
    place('AQUA_STANCHION', c1 - 3, r0 + 1, { tag: 'office', on: IN });
    place('AQUA_INFO_PEDESTAL', c0 + 4, r0 + 1, { tag: 'office', on: IN });
    place('POT', c0, r1 - 1, { tag: 'office', on: IN });
    place('POT', c1 - 1, r1 - 1, { tag: 'office', on: IN });
  },
  /** a library / reading room */
  library(c0, r0, c1, r1, top, k) {
    for (let c = c0; c < c1 - 1; c += 2) placeWall('DOUBLE_BOOKSHELF', c, top);
    carpet(c0 + 1, r0 + 2, c1 - 1, r1 - 1, 0, theme(k).second, theme(k).accent);
    for (let c = c0 + 1; c + 2 <= c1 - 1; c += 4) {
      place('SMALL_TABLE_FRONT', c, r0 + 3, { tag: 'office', on: IN });
      place('LOUNGE_CUSHIONED_CHAIR_FRONT', c, r0 + 2, { tag: 'office', on: IN });
      place('COFFEE', c, r0 + 3, { tag: 'office' });
    }
    place('DETAIL_LAMP_ORNATE', c0, r0, { tag: 'office', on: IN });
    place('LARGE_PLANT', c1 - 2, r1 - 3, { tag: 'office', on: IN });
  },
  /** a cafe corner */
  cafe(c0, r0, c1, r1, top) {
    for (let c = c0 + 1; c + 3 <= c1; c += 4) placeWall(c % 8 < 4 ? 'HANGING_PLANT' : 'SMALL_PAINTING', c, top);
    for (let r = r0 + 1; r + 2 <= r1; r += 3) for (let c = c0 + 1; c + 3 <= c1; c += 4) place('DETAIL_CAFE_TABLE', c, r, { tag: 'office', on: IN });
    place('PLANT', c0, r0, { tag: 'office', on: IN });
  },
  /** an indoor garden atrium: grass, trees, a fountain, flowers, benches */
  atrium(c0, r0, c1, r1, top, k) {
    rect(c0, r0, c1, r1, T.GRASS, c4(104, 46, -12, 0));
    place('DETAIL_FOUNTAIN_TIERED', Math.floor((c0 + c1) / 2) - 1, Math.floor((r0 + r1) / 2) - 2, { tag: 'office', on: IN });
    for (const [dc, dr] of [[1, 1], [c1 - c0 - 3, 1], [1, r1 - r0 - 3], [c1 - c0 - 3, r1 - r0 - 3]])
      place(k % 2 ? 'FOREST_OAK_SMALL' : 'FOREST_BIRCH', c0 + dc, r0 + dr - 1, { tag: 'office', on: IN });
    scatter([c0, r0, c1, r1], [['FOREST_FLOWERS', 4], ['DETAIL_PLANTER', 2], ['FOREST_BUSH', 2], ['AQUA_BENCH', 1]], 40, { tag: 'office', on: IN, seed: 900 + k });
    for (let c = c0 + 1; c < c1 - 1; c += 3) placeWall('HANGING_PLANT', c, top);
  },
};
/**
 * A walled office: outer wall, a row of rooms along the north separated by partitions, a
 * horizontal partition with doors, and a hall along the south with the entrance. Rooms' wall art
 * hangs on the north wall; the hall's on the partition.
 */
function office(label, c0, r0, c1, r1, { floor, roomFloors, rooms, hall, split, k }) {
  rect(c0, r0, c1, r1, T.WALL);
  rect(c0 + 1, r0 + 1, c1 - 1, r1 - 1, floor);
  const mid = r0 + split;
  rect(c0 + 1, mid, c1 - 1, mid + 1, T.WALL);
  const n = rooms.length;
  const width = Math.floor((c1 - c0 - 1) / n);
  const bounds = [];
  for (let i = 0; i < n; i++) {
    const rc0 = c0 + 1 + i * width;
    const rc1 = i === n - 1 ? c1 - 1 : rc0 + width - 1;
    if (i < n - 1) rect(rc1, r0 + 1, rc1 + 1, mid, T.WALL);
    rect(rc0, r0 + 1, rc1, mid, roomFloors[i % roomFloors.length]);
    // a door from each room into the hall
    const dc = Math.floor((rc0 + rc1) / 2);
    paint(dc, mid, roomFloors[i % roomFloors.length]);
    paint(dc + 1, mid, roomFloors[i % roomFloors.length]);
    bounds.push([rc0, r0 + 1, rc1, mid]);
  }
  // the entrance in the south wall
  const door = Math.floor((c0 + c1) / 2);
  paint(door, r1 - 1, floor);
  paint(door + 1, r1 - 1, floor);
  bounds.forEach(([a, b, c, d], i) => ROOM[rooms[i]](a, b, c, d, r0, k));
  ROOM[hall](c0 + 1, mid + 1, c1 - 1, r1 - 1, mid, k);
  OFFICES.push({ label, c0, r0, c1, r1 });
}
office('Aquarium Lounge Office', 188, 42, 214, 64, {
  floor: T.PARQUET, roomFloors: [T.CHECKER, T.PARQUET, T.CHECKER], rooms: ['meeting', 'studio', 'library'], hall: 'lounge', split: 11, k: 0,
});
office('Gallery Office', 252, 13, 278, 35, {
  floor: T.CHECKER, roomFloors: [T.PARQUET, T.CHECKER, T.PARQUET], rooms: ['gallery', 'gallery', 'cafe'], hall: 'lounge', split: 11, k: 1,
});
office('Garden Atrium Office', 220, 70, 246, 92, {
  floor: T.PARQUET, roomFloors: [T.PARQUET, T.CHECKER, T.PARQUET], rooms: ['studio', 'meeting', 'studio'], hall: 'atrium', split: 11, k: 2,
});
office("Harbour Master's House", 284, 158, 308, 168, {
  floor: T.PARQUET, roomFloors: [T.PARQUET, T.CHECKER], rooms: ['meeting', 'library'], hall: 'lounge', split: 5, k: 3,
});

// the GRAND AQUARIUM: galleries of tanks round a central hall, the split tunnel arch at its door
{
  const A = { c0: 270, r0: 186, c1: 306, r1: 218 };
  rect(A.c0, A.r0, A.c1, A.r1, T.WALL);
  rect(A.c0 + 1, A.r0 + 1, A.c1 - 1, A.r1 - 1, T.CHECKER);
  // inner gallery walls with openings
  rect(A.c0 + 1, A.r0 + 10, A.c1 - 1, A.r0 + 11, T.WALL);
  for (const c of [278, 287, 288, 297]) paint(c, A.r0 + 10, T.CHECKER);
  rect(A.c0 + 12, A.r0 + 1, A.c0 + 13, A.r0 + 10, T.WALL);
  rect(A.c0 + 24, A.r0 + 1, A.c0 + 25, A.r0 + 10, T.WALL);
  // entrance (north wall, facing the reef lagoon)
  for (const c of [287, 288, 289]) paint(c, A.r0, T.CHECKER);
  for (const [t, c] of [['AQUA_TUNNEL_ARCH_P1', 286], ['AQUA_TUNNEL_ARCH_P2', 287], ['AQUA_TUNNEL_ARCH_P3', 288]])
    if (!place(t, c, A.r0 - 4, { tag: 'aqua', on: ON(T.PAVEMENT, T.SAND) })) throw new Error(`${t}: the tunnel arch must stand whole`);
  const AI = ON(T.CHECKER);
  // Jellyfish room (west), Reef room (middle), Touch-pool room (east)
  for (let c = A.c0 + 1; c < A.c0 + 11; c += 3) place('AQUA_JELLY_CYLINDER', c, A.r0 + 1, { tag: 'aqua', on: AI });
  carpet(A.c0 + 2, A.r0 + 6, A.c0 + 11, A.r0 + 10, 1, c4(270, 40, -30, 0), c4(300, 50, 0, 0));
  place('AQUA_BENCH', A.c0 + 4, A.r0 + 7, { tag: 'aqua', on: AI });
  place('AQUA_TANK_REEF', A.c0 + 14, A.r0 + 1, { tag: 'aqua', on: AI });
  place('AQUA_TANK_REEF', A.c0 + 19, A.r0 + 1, { tag: 'aqua', on: AI });
  place('AQUA_TANK_CURVED', A.c0 + 17, A.r0 + 5, { tag: 'aqua', on: AI });
  place('AQUA_INFO_PEDESTAL', A.c0 + 14, A.r0 + 6, { tag: 'aqua', on: AI });
  place('AQUA_TOUCH_POOL', A.c0 + 26, A.r0 + 2, { tag: 'aqua', on: AI });
  place('AQUA_TOUCH_POOL', A.c0 + 30, A.r0 + 2, { tag: 'aqua', on: AI });
  place('AQUA_AERATOR', A.c0 + 26, A.r0 + 6, { tag: 'aqua', on: AI });
  place('AQUA_STANCHION', A.c0 + 30, A.r0 + 6, { tag: 'aqua', on: AI });
  // the Grand Tank Hall (south): panoramic tanks round a patterned floor and benches
  for (let c = A.c0 + 2; c + 5 <= A.c1 - 1; c += 6) place('AQUA_TANK_PANORAMIC', c, A.r1 - 4, { tag: 'aqua', on: AI });
  for (let r = A.r0 + 12; r + 4 <= A.r1 - 4; r += 5) {
    place('AQUA_TANK_CYLINDER', A.c0 + 1, r, { tag: 'aqua', on: AI });
    place('AQUA_TANK_CYLINDER', A.c1 - 3, r, { tag: 'aqua', on: AI });
  }
  carpet(A.c0 + 6, A.r0 + 13, A.c1 - 6, A.r1 - 5, 1, c4(205, 60, -20, 0), c4(190, 40, 20, 0));
  carpet(A.c0 + 10, A.r0 + 15, A.c1 - 10, A.r1 - 7, 2, c4(40, 55, -4, 0), c4(205, 50, 6, 0), 2);
  for (let c = A.c0 + 8; c + 2 <= A.c1 - 8; c += 5) place('AQUA_BENCH', c, A.r0 + 18, { tag: 'aqua', on: AI });
  place('AQUA_AERATOR', A.c0 + 6, A.r0 + 13, { tag: 'aqua', on: AI });
  place('AQUA_AERATOR', A.c1 - 8, A.r0 + 13, { tag: 'aqua', on: AI });
  for (const c of [A.c0 + 5, A.c1 - 7]) place('AQUA_INFO_PEDESTAL', c, A.r0 + 22, { tag: 'aqua', on: AI });
  for (let c = A.c0 + 2; c < A.c1 - 2; c += 3) placeWall(c % 2 ? 'LARGE_PAINTING' : 'SMALL_PAINTING', c, A.r0 + 10);
  for (let c = A.c0 + 1; c < A.c1 - 2; c += 4) placeWall('HANGING_PLANT', c, A.r0);
  OFFICES.push({ label: 'Grand Aquarium', ...A });
}

// ════════════════════════════════════════════════════════════════════════════
// 3. METROPOLIS: filled blocks, a varied skyline, plazas, street planting
// ════════════════════════════════════════════════════════════════════════════
const ADJ = (h, s, b) => ({ h, s, b, c: 0 });
const blocksEW = [0, ...AVENUES_EW.map((r) => r + 3)];
const blocksNS = [176, ...AVENUES_NS.map((c) => c + 3)];
const SPECIAL = [
  ...OFFICES.filter((o) => o.c0 < 308 && o.r1 <= 112),
  { c0: 219, r0: 41, c1: 248, r1: 66 },
  { c0: 251, r0: 69, c1: 280, r1: 94 },
  { c0: 187, r0: 69, c1: 216, r1: 94 },
  { c0: 283, r0: 41, c1: 308, r1: 66 },
];
/** The skyline by zone: downtown core tall, civic midtown, waterfront hotels, low west. */
const ZONE = {
  core: ['CITY_SUPERTALL', 'CITY_SKYSCRAPER_GLASS', 'CITY_SKYSCRAPER_DECO', 'CITY_TOWER_ROUND', 'CITY_TOWER_GARDEN'],
  tower: ['CITY_TOWER_ROUND', 'CITY_TOWER_GARDEN', 'CITY_SKYSCRAPER_GLASS', 'CITY_CLOCK_TOWER', 'CITY_HOTEL', 'CITY_APARTMENTS'],
  civic: ['CITY_TOWN_HALL', 'CITY_LIBRARY', 'CITY_MUSEUM', 'CITY_THEATRE', 'CITY_OBSERVATORY', 'CITY_FIRE_STATION', 'CITY_CHAPEL'],
  living: ['CITY_TOWNHOUSE', 'CITY_APARTMENTS', 'CITY_SHOP_ROW', 'CITY_CAFE', 'CITY_BAKERY', 'CITY_TOWNHOUSE'],
  works: ['CITY_WAREHOUSE', 'CITY_WORKSHOP', 'CITY_MARKET_HALL', 'CITY_SHOP_ROW'],
};
function zoneOf(i, j) {
  if (i === 0) return j >= 3 ? 'tower' : 'living';
  if (j === 0) return 'living';
  if (i === 4) return 'works';
  if (i === 1 && j >= 1 && j <= 3) return 'core';
  if (j === 4) return 'tower';
  if (i === 2) return 'civic';
  return i === 3 ? 'living' : 'tower';
}
const cityR = rng(2024);
/** Fill a frontage line: buildings shoulder to shoulder, bases on row `base`, within [c0,c1). */
function frontage(c0, c1, base, minTop, pool) {
  let c = c0;
  let guard = 0;
  while (c < c1 - 3 && guard++ < 60) {
    const fits = pool.filter((t) => {
      const e = entryOf(t);
      return c + e.w <= c1 && base - e.h + 1 >= minTop;
    });
    if (fits.length === 0) break;
    const type = pick(cityR, fits);
    const e = entryOf(type);
    if (place(type, c, base - e.h + 1, { tag: 'city', on: ON(T.PAVEMENT) })) c += e.w;
    else c += 1;
  }
}
for (let i = 0; i < blocksEW.length; i++)
  for (let j = 0; j < blocksNS.length; j++) {
    const r0 = blocksEW[i] + 1;
    const r1 = (AVENUES_EW[i] ?? 112) - 1;
    const c0 = blocksNS[j] + 1;
    const c1 = (AVENUES_NS[j] ?? 308) - 1;
    if (r1 - r0 < 5 || c1 - c0 < 5) continue;
    if (SPECIAL.some((o) => o.c0 < c1 && o.c1 > c0 && o.r0 < r1 && o.r1 > r0)) continue;
    const zone = zoneOf(i, j);
    const pool = ZONE[zone];
    const depth = r1 - r0;
    if (depth >= 12 && depth < 20) {
      // a shallow block: a low line facing the avenue above, the zone's line facing the one below
      frontage(c0, c1, r0 + 6, r0, ZONE.living);
      frontage(c0, c1, r1 - 1, r0 + 7, pool.filter((t) => entryOf(t).h <= depth - 7).length ? pool : ZONE.works);
    } else if (depth >= 20) {
      // two frontages back to back: a north line facing the avenue above, a south line below
      const northBase = r0 + Math.min(12, Math.floor(depth / 2));
      frontage(c0, c1, northBase, r0, pool);
      frontage(c0, c1, r1 - 1, northBase + 1, zone === 'core' ? ZONE.tower : pool);
    } else frontage(c0, c1, r1 - 1, r0, depth >= 10 ? pool : ZONE.living);
    // any courtyard left over becomes a pocket garden
    scatter([c0, r0, c1, r1], [['FOREST_OAK_SMALL', 2], ['DETAIL_PLANTER', 2], ['FOREST_BUSH', 2], ['DETAIL_PARK_BENCH', 1], ['DETAIL_BIKE_RACK', 1]], 30, { tag: 'court', on: ON(T.PAVEMENT), seed: i * 31 + j });
  }
// stations on the southern edge
place('CITY_TRAIN_STATION', 186, 98, { tag: 'city', on: ON(T.PAVEMENT) });
place('CITY_TRANSIT_TERMINAL', 196, 99, { tag: 'city', on: ON(T.PAVEMENT) });
// street planting and furniture along every avenue: trees and lamps alternate, then details
const streetR = rng(77);
const STREET = [['DETAIL_HYDRANT', 1], ['DETAIL_MAILBOX', 1], ['DETAIL_BIN', 2], ['DETAIL_PHONE_BOOTH', 1], ['DETAIL_BIKE_RACK', 1], ['DETAIL_BICYCLE', 1], ['DETAIL_PLANTER', 2], ['DETAIL_KIOSK', 1], ['DETAIL_PARK_BENCH', 1]];
const kerbs = [];
for (const r of AVENUES_EW) for (let c = 177; c < 306; c += 3) kerbs.push([c, r - 1], [c, r + 3]);
for (const c of AVENUES_NS) for (let r = 1; r < 110; r += 3) kerbs.push([c - 1, r], [c + 3, r]);
kerbs.forEach(([c, r], k) => {
  const t = k % 4 === 0 ? 'DETAIL_LAMP_ORNATE' : k % 4 === 2 ? 'FOREST_BUSH' : weighted(streetR, STREET);
  place(t, c, r - (entryOf(t).h - 1), { tag: 'street', on: ON(T.PAVEMENT) });
});
// parked vehicles in kerb lanes (decor; static)
const VEH = ['DETAIL_TAXI', 'DETAIL_VAN', 'DETAIL_SCOOTER', 'DETAIL_TAXI'];
for (const r of AVENUES_EW) for (let c = 179; c < 306; c += 9) place(VEH[(c + r) % 4], c, r + 1, { tag: 'vehicle', on: ON(T.ASPHALT, T.DASH_EW) });
for (const c of AVENUES_NS) for (let r = 4; r < 108; r += 11) place('DETAIL_TAXI', c + 1, r, { tag: 'vehicle', on: ON(T.ASPHALT, T.DASH_NS) });
for (const c of [189, 233, 291]) place('DETAIL_CONE', c, 37, { tag: 'street', on: ON(T.PAVEMENT, T.ASPHALT) });
place('DETAIL_BARRIER', 300, 36, { tag: 'street', on: ON(T.PAVEMENT) });
// CIVIC PLAZA: the town hall faces a symmetric square of fountains, obelisk and flower beds
rect(232, 48, 235, 66, T.PAVEMENT, c4(36, 10, -2, 0)); // the processional way
rect(219, 54, 248, 56, T.PAVEMENT, c4(36, 10, -2, 0));
for (let r = 49; r < 64; r += 3) {
  place('FOREST_OAK_SMALL', 219, r, { tag: 'plaza', on: ON(T.COBBLE) });
  place('FOREST_OAK_SMALL', 246, r, { tag: 'plaza', on: ON(T.COBBLE) });
}
for (const [cx, cy] of [[223, 51], [243, 51], [223, 60], [243, 60]])
  for (const [dc, dr] of [[-2, -1], [2, -1], [-2, 2], [2, 2], [0, -2], [0, 3]]) place('FOREST_FLOWERS', cx + dc, cy + dr, { tag: 'plaza', on: ON(T.COBBLE) });
for (const c of [227, 239]) place('DETAIL_KIOSK', c, 47, { tag: 'plaza', on: ON(T.COBBLE) });
place('CITY_TOWN_HALL', 230, 41, { tag: 'plaza', on: ON(T.COBBLE) });
place('DETAIL_FOUNTAIN_TIERED', 232, 52, { tag: 'plaza', on: ON(T.COBBLE) });
for (const c of [222, 242]) place('DETAIL_FOUNTAIN_SQUARE', c, 50, { tag: 'plaza', on: ON(T.COBBLE) });
place('DETAIL_OBELISK', 232, 58, { tag: 'plaza', on: ON(T.COBBLE) });
for (const c of [221, 225, 238, 242]) place('DETAIL_SCULPTURE', c, 59, { tag: 'plaza', on: ON(T.COBBLE) });
for (let c = 220; c < 247; c += 3) {
  place('DETAIL_PLANTER', c, 63, { tag: 'plaza', on: ON(T.COBBLE) });
  place('DETAIL_LAMP_ORNATE', c, 46, { tag: 'plaza', on: ON(T.COBBLE) });
}
for (const c of [220, 228, 236, 244]) place('DETAIL_PARK_BENCH', c, 55, { tag: 'plaza', on: ON(T.COBBLE) });
// WATERFRONT SQUARE: hotel and theatre over a cafe square
place('CITY_THEATRE', 285, 42, { tag: 'square', on: ON(T.COBBLE) });
place('CITY_HOTEL', 296, 42, { tag: 'square', on: ON(T.COBBLE) });
for (let c = 285; c < 306; c += 4) place('DETAIL_CAFE_TABLE', c, 54, { tag: 'square', on: ON(T.COBBLE) });
for (let c = 284; c < 306; c += 4) place('FOREST_OAK_SMALL', c, 60, { tag: 'square', on: ON(T.COBBLE) });
place('DETAIL_FOUNTAIN_SQUARE', 294, 57, { tag: 'square', on: ON(T.COBBLE) });
for (const [dc, dr] of [[-2, 0], [4, 0], [1, -2], [1, 3]]) place('FOREST_FLOWERS', 294 + dc, 57 + dr, { tag: 'square', on: ON(T.COBBLE) });
for (let c = 285; c < 306; c += 5) place('DETAIL_LAMP_MODERN', c, 50, { tag: 'square', on: ON(T.COBBLE) });
scatter([283, 41, 308, 66], [['DETAIL_PLANTER', 2], ['DETAIL_BIN', 1], ['DETAIL_KIOSK', 1], ['DETAIL_BICYCLE', 1]], 24, { tag: 'square', on: ON(T.COBBLE), seed: 51 });
// CENTRAL PARK: groves, lawns, the pond, a garden arch on the path
for (const [c0, r0, c1, r1, s] of [[252, 70, 264, 80, 1], [267, 83, 279, 93, 2], [252, 83, 264, 93, 3]])
  scatter([c0, r0, c1, r1], [['FOREST_OAK', 3], ['FOREST_OAK_SMALL', 3], ['FOREST_BIRCH', 2], ['FOREST_PINE', 1], ['FOREST_BUSH', 3], ['FOREST_FLOWERS', 3]], 60, { tag: 'park', on: ON(T.GRASS), seed: 41 + s });
scatter([266, 70, 280, 80], [['COAST_REEDS', 2], ['AQUA_LILY_PADS', 2], ['FOREST_FLOWERS', 2], ['ANIMAL_DUCK', 1]], 30, { tag: 'park', on: ON(T.GRASS, T.POND, T.RIVER), seed: 47 });
place('DETAIL_GARDEN_ARCH_P1', 264, 86, { tag: 'park', on: ON(T.COBBLE, T.GRASS) });
place('DETAIL_GARDEN_ARCH_P2', 265, 86, { tag: 'park', on: ON(T.COBBLE, T.GRASS) });
place('DETAIL_GARDEN_ARCH_P3', 266, 86, { tag: 'park', on: ON(T.COBBLE, T.GRASS) });
for (const [c, r] of [[256, 81], [270, 81], [263, 74], [263, 90]]) place('DETAIL_PARK_BENCH', c, r - 1, { tag: 'park', on: ON(T.GRASS, T.COBBLE) });
// COMPANION PARK: the companion CATALOGUE as a designed park — every pet STATIC decoration, in a
// place it plausibly belongs: a dog meadow by the benches, a cat garden among flowers and shrubs,
// a small-pets corner by the hutches, birds by the birdhouses, and the GOLDFISH and the turtle in
// the park pond (never loose on grass). Paths cross it; nothing here implies an agent.
const PETS = [...CATALOG.keys()].filter((k) => k.startsWith('PET_')).sort();
{
  const PARK = [187, 69, 216, 94];
  path_([[187, 81], [216, 81]], 2, T.COBBLE, 2, 61);
  path_([[201, 69], [201, 94]], 2, T.COBBLE, 2, 62);
  // the pond (south-east quarter): graded water, a sandy rim
  for (let r = 84; r < 93; r++)
    for (let c = 203; c < 215; c++) {
      const k = ((c - 209) / 5.5) ** 2 + ((r - 88.5) / 4) ** 2 + (fbm(c / 3, r / 3, 63) - 0.5) * 0.3;
      if (k <= 0.7) paint(c, r, T.POND, c4(196, 50, -16, 0));
      else if (k <= 1) paint(c, r, T.RIVER, c4(180, 42, -4, 0));
      else if (k <= 1.25) paint(c, r, T.SAND, c4(40, 34, -6, 0));
    }
  const W_ = ON(T.POND, T.RIVER);
  const G = ON(T.GRASS);
  const at = (t, c, r, on = G) => place(t, c, r, { tag: 'companion', on });
  // pond life
  for (const [t, c, r] of [['PET_GOLDFISH_ORANGE', 207, 87], ['PET_GOLDFISH_RED_WHITE', 210, 89], ['PET_GOLDFISH_BLACK_MOOR', 208, 90], ['PET_TURTLE', 212, 87], ['AQUA_LILY_PADS', 205, 89], ['AQUA_LILY_PADS', 211, 85], ['AQUA_KOI', 209, 86]])
    at(t, c, r, W_);
  for (const [c, r] of [[203, 85], [214, 91], [204, 92]]) at('COAST_REEDS', c, r, ON(T.SAND, T.GRASS));
  // the dog meadow (north-west quarter): dogs round the trees and benches
  const dogs = PETS.filter((p) => /RETRIEVER|LABRADOR|CORGI|DACHSHUND|HUSKY|POODLE|BEAGLE|DALMATIAN|COLLIE|SHIBA|PUG|SHEPHERD/.test(p));
  for (const [c, r] of [[190, 72], [196, 73], [192, 77]]) at('FOREST_OAK', c, r - 2);
  for (const [c, r] of [[188, 79], [197, 79]]) at('DETAIL_PARK_BENCH', c, r - 1);
  const dR = rng(64);
  dogs.forEach((d, k) => {
    for (let tries = 0; tries < 20; tries++) if (at(d, 188 + Math.floor(dR() * 12), 70 + Math.floor(dR() * 10))) break;
  });
  // the cat garden (north-east quarter): flower beds and shrubs to lounge among
  const cats = PETS.filter((p) => /COON|SIAMESE|SHORTHAIR|RAGDOLL|BENGAL|SPHYNX|PERSIAN|NORWEGIAN|FOLD|BOMBAY|RUSSIAN|ABYSSINIAN/.test(p));
  for (const [c, r] of [[204, 71], [210, 72], [213, 76], [206, 77]]) {
    at('FOREST_BUSH', c, r);
    at('FOREST_FLOWERS', c + 1, r);
    at('FOREST_FLOWERS', c, r + 1);
  }
  at('DETAIL_GARDEN_ARCH_P1', 203, 73);
  cats.forEach((d, k) => {
    for (let tries = 0; tries < 20; tries++) if (at(d, 203 + Math.floor(dR() * 12), 70 + Math.floor(dR() * 10))) break;
  });
  // the small-pets corner (south-west quarter): hutches and crates, rabbits, guinea pig, hamster,
  // ferret and hedgehog; birds by the birdhouses
  for (const [t, c, r] of [['FARM_HARVEST_CRATES', 189, 84], ['FARM_HARVEST_CRATES', 192, 84], ['FARM_HAY_STACK', 195, 84], ['DETAIL_BIRDHOUSE', 190, 89], ['DETAIL_BIRDHOUSE', 196, 89]]) at(t, c, r);
  const small = PETS.filter((p) => /RABBIT|GUINEA|HAMSTER|FERRET|HEDGEHOG|PARROT|COCKATIEL/.test(p));
  small.forEach((d) => {
    for (let tries = 0; tries < 20; tries++) if (at(d, 188 + Math.floor(dR() * 12), 85 + Math.floor(dR() * 8))) break;
  });
  scatter(PARK, [['FOREST_FLOWERS', 3], ['FOREST_BUSH', 1]], 30, { tag: 'companion', on: G, seed: 65 });
  for (let c = 188; c < 216; c += 5) at('FOREST_OAK_SMALL', c, 92);
}
// the west edge strip (cols 176-183) facing the palace forest: a planted buffer
scatter([176, 0, 184, 112], [['FOREST_OAK', 2], ['FOREST_OAK_SMALL', 3], ['FOREST_BUSH', 3], ['FOREST_FLOWERS', 2], ['DETAIL_LAMP_ORNATE', 1]], 120, { tag: 'buffer', on: ON(T.PAVEMENT), seed: 49 });

// ════════════════════════════════════════════════════════════════════════════
// 4. COAST, HARBOUR, AQUARIUM QUARTER
// ════════════════════════════════════════════════════════════════════════════
// NORTH SHORE: the lighthouse on a rocky point, dune grass, beach life, boats and sea life
{
  const s = shoreCol(5);
  for (let r = 0; r < 11; r++) for (let c = s - 3; c < s + 4; c++) if ((c - s) ** 2 / 16 + (r - 5) ** 2 / 30 <= 1) paint(c, r, T.SAND, c4(30, 12, -20, 0));
  place('COAST_LIGHTHOUSE', s - 2, 1, { tag: 'shore', on: ON(T.SAND) });
  for (const [dc, dr] of [[-3, 7], [1, 7], [3, 2], [-4, 3]]) place('COAST_ROCKS', s + dc, dr, { tag: 'shore', on: ON(T.SAND, T.RIVER, T.SEA) });
}
const sR = rng(61);
for (let r = 12; r < 112; r++) {
  const s = shoreCol(r);
  if (sR() < 0.35) place(sR() < 0.6 ? 'COAST_REEDS' : 'FOREST_FERN', 310 + Math.floor(sR() * 3), r, { tag: 'dune', on: ON(T.SAND) });
  if (r % 4 === 0) {
    const t = weighted(sR, [['COAST_UMBRELLA', 3], ['COAST_SHELLS', 2], ['COAST_DRIFTWOOD', 1], ['COAST_LIFE_RING', 1], ['COAST_TIDEPOOL', 1], ['COAST_ROCKS', 1]]);
    place(t, s - 3 - Math.floor(sR() * 3), r, { tag: 'beach', on: ON(T.SAND) });
  }
}
place('FARM_PRODUCE_STAND', 311, 40, { tag: 'beach', on: ON(T.SAND, T.BOARD) });
place('FARM_PRODUCE_STAND', 311, 78, { tag: 'beach', on: ON(T.SAND, T.BOARD) });
scatter([322, 2, 350, 110], [['COAST_SAILBOAT', 3], ['COAST_FISHING_BOAT', 2], ['COAST_BUOY', 3], ['COAST_ROWBOAT', 1], ['AQUA_MANTA', 1], ['AQUA_SHARK', 1], ['AQUA_SEA_TURTLE', 1], ['AQUA_FISH_SCHOOL', 1]], 46, { tag: 'sea', on: ON(T.SEA), seed: 62 });
scatter([322, 168, 350, 222], [['COAST_SAILBOAT', 2], ['COAST_BUOY', 3], ['AQUA_FISH_SCHOOL', 1], ['AQUA_SEA_TURTLE', 1]], 22, { tag: 'sea', on: ON(T.SEA), seed: 63 });
// HARBOUR: piers with moored boats, a fish market, warehouses, quayside clutter in ordered lines
for (const r of [116, 128, 160]) {
  const s = shoreCol(r);
  for (let c = s - 2; c < s + 16; c += 2) place('COAST_PIER_STRAIGHT', c, r - 1, { tag: 'pier', on: ON(T.BOARD, T.SEA, T.PAVEMENT) });
  place('COAST_PIER_END', s + 16, r - 1, { tag: 'pier', on: ON(T.BOARD, T.SEA) });
  for (let c = s; c < s + 16; c += 5) {
    place('COAST_FISHING_BOAT', c, r + 2, { tag: 'moored', on: ON(T.SEA) });
    place('COAST_ROWBOAT', c + 3, r - 3, { tag: 'moored', on: ON(T.SEA) });
  }
  for (let c = s - 1; c < s + 16; c += 4) place('COAST_BOLLARD', c, r + 2, { tag: 'pier', on: ON(T.SEA, T.BOARD) });
}
for (const [t, c] of [['CITY_WAREHOUSE', 266], ['CITY_WAREHOUSE', 274], ['CITY_MARKET_HALL', 286], ['CITY_WORKSHOP', 296]]) place(t, c, 113, { tag: 'harbour', on: ON(T.PAVEMENT) });
for (let c = 268; c < 300; c += 4) place('COAST_FISH_STALL', c, 128, { tag: 'market', on: ON(T.PAVEMENT) });
for (let c = 268; c < 300; c += 6) {
  place('FARM_BARRELS', c, 133, { tag: 'quay', on: ON(T.PAVEMENT) });
  place('DETAIL_BOXES', c + 2, 133, { tag: 'quay', on: ON(T.PAVEMENT) });
  place('DETAIL_PALLET', c + 2, 134, { tag: 'quay', on: ON(T.PAVEMENT) });
  place('COAST_NET_RACK', c + 4, 136, { tag: 'quay', on: ON(T.PAVEMENT) });
}
for (let r = 114; r < 166; r += 3) {
  const s = shoreCol(r);
  if (r >= 138 && r < 160) continue;
  place('COAST_DOCK_POST', s - 1, r, { tag: 'quay', on: ON(T.PAVEMENT, T.SEA) });
}
for (let c = 296; c < 318; c += 2) place('COAST_HARBOUR_WALL', c, 137, { tag: 'quay', on: ON(T.PAVEMENT, T.SAND) });
for (let r = 141; r < 157; r += 4) place('COAST_ROWBOAT', 304 + (r % 3) * 3, r, { tag: 'basin', on: ON(T.SEA) });
scatter([264, 140, 296, 150], [['DETAIL_LAMP_ORNATE', 2], ['COAST_ROPE', 2], ['COAST_LIFE_RING', 1], ['DETAIL_BOXES', 1]], 24, { tag: 'quay', on: ON(T.PAVEMENT), seed: 71 });
// AQUARIUM QUARTER: the reef lagoon before the Grand Aquarium, a seaside plaza and shops
{
  for (let r = 168; r < 184; r++)
    for (let c = 272; c < 306; c++) {
      const k = ((c - 289) / 15) ** 2 + ((r - 175) / 5.5) ** 2;
      if (k <= 1) paint(c, r, k > 0.62 ? T.RIVER : T.POND, k > 0.62 ? c4(178, 46, -2, 0) : c4(192, 56, -14, 0));
      else if (k <= 1.25) paint(c, r, T.SAND, c4(42, 40, 2, 0));
    }
  scatter([273, 169, 306, 183], [['AQUA_CORAL_RED', 2], ['AQUA_CORAL_FAN', 2], ['AQUA_CORAL_BRAIN', 2], ['AQUA_CORAL_TUBE', 2], ['AQUA_ANEMONE', 2], ['AQUA_KELP', 2], ['AQUA_SEAGRASS', 2], ['AQUA_CLOWNFISH', 2], ['AQUA_BLUE_TANG', 2], ['AQUA_BUTTERFLYFISH', 2], ['AQUA_KOI', 1], ['AQUA_SEAHORSE', 1], ['AQUA_JELLY_PINK', 1], ['AQUA_JELLY_BLUE', 1], ['AQUA_OCTOPUS', 1], ['AQUA_STARFISH', 1], ['AQUA_CRAB', 1], ['AQUA_LOBSTER', 1], ['AQUA_PUFFERFISH', 1], ['AQUA_SHIPWRECK', 1], ['AQUA_RUIN_ARCH', 1], ['AQUA_BOULDER', 1], ['AQUA_TREASURE', 1], ['PET_GOLDFISH_ORANGE', 1], ['PET_GOLDFISH_RED_WHITE', 1], ['PET_GOLDFISH_BLACK_MOOR', 1]], 150, { tag: 'lagoon', on: ON(T.POND, T.RIVER), seed: 84 });
  for (const [t, c, r] of [['CITY_CAFE', 266, 205], ['CITY_BAKERY', 266, 212], ['CITY_SHOP_ROW', 266, 198]]) place(t, c, r, { tag: 'aqua-street', on: ON(T.PAVEMENT) });
  for (let c = 270; c < 306; c += 4) {
    place('DETAIL_LAMP_MODERN', c, 184, { tag: 'aqua-plaza', on: ON(T.PAVEMENT, T.SAND) });
    place('DETAIL_PLANTER', c + 2, 220, { tag: 'aqua-plaza', on: ON(T.PAVEMENT) });
  }
  for (let r = 188; r < 220; r += 5) {
    place('FOREST_OAK_SMALL', 307, r, { tag: 'aqua-plaza', on: ON(T.PAVEMENT, T.SAND) });
    place('DETAIL_PARK_BENCH', 307, r + 2, { tag: 'aqua-plaza', on: ON(T.PAVEMENT, T.SAND) });
  }
  // the quarter's beach south of the harbour
  const bR = rng(86);
  for (let r = 168; r < 224; r += 3) {
    const s = shoreCol(r);
    place(weighted(bR, [['COAST_UMBRELLA', 2], ['COAST_SHELLS', 2], ['COAST_TIDEPOOL', 1], ['COAST_DRIFTWOOD', 1], ['COAST_REEDS', 1]]), s - 3 - Math.floor(bR() * 3), r, { tag: 'beach', on: ON(T.SAND) });
  }
}

// ════════════════════════════════════════════════════════════════════════════
// 5. RIVER VALLEY FARMLAND
// ════════════════════════════════════════════════════════════════════════════
{
  const farmR = rng(91);
  // farmsteads: grouped yards, each a working cluster (house, barn, silo, coop, well, stack)
  const YARDS = [
    [180, 113, ['FARM_FARMHOUSE', 'FARM_BARN', 'FARM_SILO', 'FARM_COOP', 'FARM_WELL']],
    [216, 113, ['FARM_STABLE', 'FARM_BARN', 'FARM_WINDMILL', 'FARM_WATER_TOWER']],
    [252, 160, ['FARM_COTTAGE', 'FARM_GREENHOUSE', 'FARM_PRODUCE_STAND']],
    [180, 198, ['FARM_COTTAGE', 'FARM_GREENHOUSE', 'FARM_SILO']],
  ];
  for (const [c0, r0, list] of YARDS) {
    let c = c0;
    for (const t of list) {
      if (place(t, c, r0, { tag: 'farmstead', on: ON(T.GRASS) })) c += entryOf(t).w + 1;
      else if (place(t, c, r0 + 3, { tag: 'farmstead', on: ON(T.GRASS) })) c += entryOf(t).w + 1;
    }
    scatter([c0, r0 + 5, c + 2, r0 + 10], [['FARM_HAY_STACK', 2], ['FARM_BARRELS', 2], ['FARM_HANDCART', 1], ['FARM_FIREWOOD', 1], ['FARM_GRAIN_SACK', 2], ['FARM_WHEELBARROW', 1], ['ANIMAL_FARM_DOG', 1], ['ANIMAL_BARN_CAT', 1], ['ANIMAL_CHICKEN', 2]], 26, { tag: 'yard', on: ON(T.GRASS), seed: c0 + r0 });
  }
  // fields planted in rows (the crop sprites tile), hedgerows round them
  for (const [c0, r0, c1, r1, , kind] of FIELDS) {
    if (kind === 'orchard') {
      for (let r = r0; r + 4 <= r1; r += 4) for (let c = c0 + (r % 8 ? 0 : 2); c + 4 <= c1; c += 5) place('FARM_APPLE_TREE', c, r, { tag: 'orchard', on: ON(T.SOIL, T.GRASS) });
      place('FARM_BEEHIVES', c0 - 3, r0 + 2, { tag: 'orchard', on: ON(T.GRASS) });
      place('FARM_BEEHIVES', c0 - 3, r0 + 5, { tag: 'orchard', on: ON(T.GRASS) });
      continue;
    }
    if (kind === 'hay') {
      for (let r = r0 + 1; r + 2 <= r1; r += 4) for (let c = c0 + 1; c + 2 <= c1; c += 4) place(farmR() < 0.6 ? 'FARM_HAY_ROUND' : 'FARM_HAY_STACK', c, r, { tag: 'field', on: ON(T.SOIL) });
      place('FARM_TRACTOR', c0 + 8, r1 - 3, { tag: 'field', on: ON(T.SOIL) });
      place('FARM_TRAILER', c0 + 12, r1 - 3, { tag: 'field', on: ON(T.SOIL) });
    } else {
      const crops = kind === 'wheat' ? ['FARM_WHEAT'] : kind === 'corn' ? ['FARM_CORN'] : ['FARM_WHEAT', 'FARM_CORN', 'FARM_PUMPKINS'];
      for (let r = r0; r + 2 <= r1; r += 2)
        for (let c = c0; c + 2 <= c1; c += 2) {
          if (farmR() < 0.08) continue; // a few gaps, not a stamp
          const band = Math.floor((c - c0) / 8) % crops.length;
          place(crops[band], c, r, { tag: 'crop', on: ON(T.CROPS) });
        }
      place('FARM_SCARECROW', Math.floor((c0 + c1) / 2), Math.floor((r0 + r1) / 2) - 1, { tag: 'crop', on: ON(T.CROPS) });
    }
    for (let c = c0 - 1; c <= c1; c += 1) {
      place('FOREST_BUSH', c, r0 - 1, { tag: 'hedge', on: ON(T.GRASS) });
      if (farmR() < 0.8) place('FOREST_BUSH', c, r1, { tag: 'hedge', on: ON(T.GRASS) });
    }
  }
  // paddocks: fenced, gated, grazed (animals STATIC)
  const PADDOCKS = [
    [212, 140, 244, 154, ['ANIMAL_COW_HOLSTEIN', 'ANIMAL_COW_BROWN', 'ANIMAL_CALF', 'ANIMAL_HIGHLAND_COW']],
    [182, 142, 202, 154, ['ANIMAL_SHEEP', 'ANIMAL_LAMB', 'ANIMAL_GOAT_WHITE', 'ANIMAL_GOAT_BROWN', 'ANIMAL_ALPACA']],
    [182, 200, 210, 220, ['ANIMAL_HORSE_CHESTNUT_L', 'ANIMAL_HORSE_CHESTNUT_R', 'ANIMAL_HORSE_WHITE', 'ANIMAL_HORSE_BAY', 'ANIMAL_PONY', 'ANIMAL_DONKEY_L', 'ANIMAL_DONKEY_R']],
    [252, 136, 262, 156, ['ANIMAL_PIG', 'ANIMAL_PIGLET', 'ANIMAL_CHICKEN', 'ANIMAL_ROOSTER', 'ANIMAL_CHICK', 'ANIMAL_TURKEY', 'ANIMAL_GOOSE', 'ANIMAL_DUCK', 'ANIMAL_PEACOCK']],
  ];
  for (const [c0, r0, c1, r1, animals] of PADDOCKS) {
    for (let c = c0; c < c1; c += 2) {
      place('DETAIL_FENCE', c, r0, { tag: 'fence' });
      if (c !== c0 + 6) place('DETAIL_FENCE', c, r1 - 1, { tag: 'fence' });
    }
    place('FARM_GATE', c0 + 6, r1 - 2, { tag: 'fence' });
    animals.forEach((a, k) => {
      for (let n = 0; n < 3; n++) {
        const col = c0 + 1 + Math.floor(farmR() * (c1 - c0 - 3));
        const row = r0 + 1 + Math.floor(farmR() * (r1 - r0 - 3));
        place(a, col, row, { tag: 'animal' }) || place(a, c0 + 1 + ((k * 3) % (c1 - c0 - 3)), r0 + 2, { tag: 'animal' });
      }
    });
    place('FARM_TROUGH', c0 + 2, r0 + 2, { tag: 'farm' });
  }
  // riverbank reeds, irregular
  for (let c = 162; c < 300; c++) {
    const r0 = riverRow(c);
    if (farmR() < 0.3) place(farmR() < 0.5 ? 'COAST_REEDS' : 'COAST_CATTAILS', c, r0 - 3, { tag: 'bank', on: ON(T.GRASS, T.SAND) });
    if (farmR() < 0.3) place(farmR() < 0.5 ? 'COAST_CATTAILS' : 'COAST_REEDS', c, r0 + 5, { tag: 'bank', on: ON(T.GRASS, T.SAND) });
  }
  // lone field trees and copses break the grid
  scatter([176, 112, 264, 224], [['FOREST_OAK', 2], ['FOREST_OAK_SMALL', 2], ['FOREST_BIRCH', 1]], 60, { tag: 'copse', on: ON(T.GRASS), seed: 95 });
}

// ── bridges: plank decks carry the crossings; span sprites (walk-over) dress them ──
for (const b of bridges) if (b.axis === 'ns') for (let r = b.r0; r + 3 <= b.r1; r += 3) for (let c = b.c0; c + 2 <= b.c1 + 1; c += 2) place('COAST_PIER_STRAIGHT', c, r, { tag: 'bridge', on: ON(T.BOARD) });
place('DETAIL_BRIDGE_STONE', sb0, streamBridgeRow - 2, { tag: 'bridge', on: ON(T.BOARD, T.RIVER, T.GRASS, T.TRAIL, T.SAND) });

// ════════════════════════════════════════════════════════════════════════════
// 6. WOODS: organic planting from a density field (canopy, understory, thickets, clearings)
// ════════════════════════════════════════════════════════════════════════════
/**
 * density field d in [0,1]: < clearAt -> clearing (meadow ground, flowers, landmarks), above
 * thickAt -> thicket (packed small trees and shrubs); between, canopy trees with an understory.
 * Species come from a second field (zones), so stands — not individual trees — differ.
 */
function woodland(box, { seed, scale, clearAt, thickAt, canopy, understory, thicket, meadow, tag, edgeTo }) {
  const [c0, r0, c1, r1] = box;
  const r = rng(seed);
  const dens = (c, rr) => {
    let d = fbm(c / scale, rr / scale, seed);
    // ragged edges: thin out toward a neighbouring open district
    if (edgeTo) d -= Math.max(0, 1 - edgeTo(c, rr) / 6) * 0.25;
    return d;
  };
  const zone = (c, rr) => fbm(c / (scale * 1.7), rr / (scale * 1.7), seed + 101);
  // clearings: meadow ground
  for (let rr = r0; rr < r1; rr++)
    for (let c = c0; c < c1; c++) if (tileAt(c, rr) === T.FOREST && dens(c, rr) < clearAt) paint(c, rr, T.GRASS);
  const cells = [];
  for (let rr = r0; rr < r1; rr++) for (let c = c0; c < c1; c++) cells.push([c, rr]);
  for (let k = cells.length - 1; k > 0; k--) {
    const j = Math.floor(r() * (k + 1));
    [cells[k], cells[j]] = [cells[j], cells[k]];
  }
  // canopy first (large trees claim space), then thickets, then understory, then meadow life
  for (const [c, rr] of cells) {
    const d = dens(c, rr);
    if (d >= clearAt && d < thickAt && r() < 0.25 + (d - clearAt) * 1.4) place(weighted(r, canopy(zone(c, rr))), c, rr, { tag, on: ON(T.FOREST) });
  }
  for (const [c, rr] of cells) {
    const d = dens(c, rr);
    if (d >= thickAt && r() < 0.75) place(weighted(r, thicket(zone(c, rr))), c, rr, { tag, on: ON(T.FOREST) });
  }
  for (const [c, rr] of cells) {
    const d = dens(c, rr);
    if (d >= clearAt && r() < 0.12 + (d - clearAt) * 0.1) place(weighted(r, understory(zone(c, rr))), c, rr, { tag, on: ON(T.FOREST) });
  }
  for (const [c, rr] of cells) if (tileAt(c, rr) === T.GRASS && dens(c, rr) < clearAt && r() < 0.05) place(weighted(r, meadow), c, rr, { tag, on: ON(T.GRASS) });
}
const distToDistrict = (names) => (c, rr) => {
  let best = 99;
  for (const d of DISTRICTS)
    if (names.includes(d[0])) {
      const dc = Math.max(d[1] - c, 0, c - (d[3] - 1));
      const dr = Math.max(d[2] - rr, 0, rr - (d[4] - 1));
      best = Math.min(best, Math.max(dc, dr));
    }
  return best;
};
// AUTUMN WILDWOOD: red maple, amber oak, golden birch stands; evergreen pockets; deep thickets
woodland([0, 112, 88, 224], {
  seed: 111,
  scale: 13,
  clearAt: 0.31,
  thickAt: 0.63,
  edgeTo: distToDistrict(['Mirror Lakes']),
  canopy: (z) =>
    z < 0.38
      ? [['AUTUMN_OAK_RED', 5], ['AUTUMN_OAK_SMALL_RED', 3], ['AUTUMN_OAK_AMBER', 2], ['FOREST_PINE', 1]]
      : z < 0.58
        ? [['AUTUMN_OAK_AMBER', 5], ['AUTUMN_OAK_SMALL_AMBER', 3], ['AUTUMN_BIRCH_AMBER', 2], ['AUTUMN_OAK_RUSSET', 2]]
        : [['AUTUMN_BIRCH_GOLD', 5], ['AUTUMN_OAK_SMALL_GOLD', 3], ['AUTUMN_BIRCH_AMBER', 2], ['FOREST_PINE_TALL', 1]],
  thicket: (z) =>
    z < 0.5
      ? [['AUTUMN_OAK_SMALL_RED', 3], ['AUTUMN_BUSH_RED', 4], ['AUTUMN_BUSH_RUSSET', 3], ['FOREST_PINE_SMALL', 2]]
      : [['AUTUMN_OAK_SMALL_GOLD', 3], ['AUTUMN_BUSH_GOLD', 4], ['AUTUMN_BUSH_AMBER', 3], ['AUTUMN_FERN_GOLD', 2]],
  understory: () => [['AUTUMN_FERN_AMBER', 3], ['AUTUMN_FERN_GOLD', 2], ['AUTUMN_BUSH_AMBER', 2], ['FOREST_STUMP', 1], ['FOREST_LOG', 1], ['DETAIL_MUSHROOMS', 2], ['FOREST_ROCK', 1]],
  meadow: [['FOREST_FLOWERS', 4], ['AUTUMN_FERN_GOLD', 1], ['DETAIL_MUSHROOMS', 1]],
  tag: 'wildwood',
});
// SILVER PINE HIGHLANDS: spruce ridges, fir slopes, golden larch patches, boulder fields
woodland([88, 168, 176, 224], {
  seed: 121,
  scale: 11,
  clearAt: 0.33,
  thickAt: 0.66,
  edgeTo: distToDistrict(['Mirror Lakes', 'River Valley Farmland']),
  canopy: (z) =>
    z < 0.42
      ? [['HIGHLAND_PINE_TALL_SPRUCE', 5], ['HIGHLAND_PINE_SPRUCE', 4], ['HIGHLAND_PINE_SMALL_SPRUCE', 2], ['FOREST_BOULDER', 1]]
      : z < 0.6
        ? [['HIGHLAND_PINE_TALL_FIR', 4], ['HIGHLAND_PINE_FIR', 4], ['HIGHLAND_PINE_SMALL_FIR', 2], ['FOREST_PINE_TALL', 1]]
        : [['HIGHLAND_PINE_LARCH', 4], ['HIGHLAND_PINE_SMALL_LARCH', 3], ['HIGHLAND_PINE_FIR', 2], ['FOREST_BOULDER', 1]],
  thicket: () => [['HIGHLAND_PINE_SMALL_SPRUCE', 4], ['HIGHLAND_PINE_SMALL_FIR', 3], ['HIGHLAND_BUSH_FIR', 3], ['FOREST_ROCK', 1]],
  understory: () => [['HIGHLAND_BUSH_FIR', 2], ['FOREST_ROCK', 3], ['FOREST_BOULDER', 1], ['FOREST_FERN', 1], ['FOREST_STUMP', 1]],
  meadow: [['FOREST_FLOWERS', 3], ['FOREST_ROCK', 2], ['COAST_ROCKS', 1]],
  tag: 'highlands',
});
// landmarks set in the clearings
const landmark = (t, c, r) =>
  place(t, c, r, { tag: 'landmark', on: ON(T.FOREST, T.GRASS, T.TRAIL) }) ||
  place(t, c + 2, r + 1, { tag: 'landmark', on: ON(T.FOREST, T.GRASS, T.TRAIL) });
for (const [t, c, r] of [
  ['DETAIL_RUIN_ARCH', 18, 126], ['DETAIL_STANDING_STONES', 25, 131], ['DETAIL_STANDING_STONES', 14, 133],
  ['DETAIL_DEER_STATUE', 60, 166], ['DETAIL_TENT', 40, 196], ['DETAIL_LOG_CIRCLE', 44, 199], ['DETAIL_FIRE_RING', 41, 202],
  ['DETAIL_PICNIC_TABLE', 48, 196], ['DETAIL_BACKPACK', 47, 202], ['DETAIL_TREASURE_CHEST', 70, 186], ['DETAIL_HOLLOW_LOG', 30, 160],
  ['DETAIL_BIRDHOUSE', 66, 140], ['DETAIL_BIRDHOUSE', 12, 170], ['DETAIL_SIGNPOST', 94, 121], ['DETAIL_LILY_POND', 22, 205],
  ['COAST_SPRING', 116, 185], ['DETAIL_VIEWER', 160, 180], ['DETAIL_TELESCOPE', 163, 181], ['DETAIL_LOOKOUT_SIGN', 158, 184],
  ['DETAIL_STAIRS', 140, 194], ['DETAIL_RUIN_ARCH', 102, 214], ['DETAIL_TENT', 146, 202],
]) landmark(t, c, r);

// ── MIRROR LAKES: lush banks, reeds in the shallows' margin, jetties, boats, picnic meadows ──
{
  const lR = rng(101);
  for (let r = 112; r < 168; r++)
    for (let c = 88; c < 176; c++) {
      const t = tileAt(c, r);
      const d = distToWater(c, r, 2);
      if (t === T.SAND && d === 1 && lR() < 0.32) place(lR() < 0.5 ? 'COAST_REEDS' : 'COAST_CATTAILS', c, r, { tag: 'shore', on: ON(T.SAND, T.GRASS) });
      if (lakeMask[idx(c, r)] && distToLand(c, r, 2) === 1 && lR() < 0.06) place('AQUA_LILY_PADS', c, r, { tag: 'shallows', on: ON(T.RIVER) });
    }
  for (const [c, r] of [[126, 133], [128, 139], [149, 118], [153, 126]]) place('COAST_ROWBOAT', c, r, { tag: 'lake', on: ON(T.POND, T.RIVER) });
  place('COAST_CANOE', 108, 136, { tag: 'lake', on: ON(T.POND) });
  place('COAST_CANOE', 160, 132, { tag: 'lake', on: ON(T.POND) });
  for (const c of [126, 128, 130]) place('COAST_DOCK_POST', c, 135, { tag: 'jetty', on: ON(T.BOARD, T.POND, T.RIVER) });
  // willow-and-birch groves along the banks, open picnic lawns between
  scatter([88, 112, 176, 168], [['FOREST_BIRCH', 3], ['FOREST_OAK', 2], ['FOREST_OAK_SMALL', 2], ['FOREST_BUSH', 2], ['FOREST_FLOWERS', 3]], 260, { tag: 'lakes', on: ON(T.GRASS), seed: 102 });
  for (const [t, c, r] of [['DETAIL_PICNIC_TABLE', 100, 152], ['DETAIL_PICNIC_TABLE', 104, 155], ['DETAIL_PARK_BENCH', 136, 148], ['DETAIL_PARK_BENCH', 142, 120], ['FARM_COTTAGE', 162, 150], ['DETAIL_TENT', 140, 154], ['DETAIL_FIRE_RING', 144, 156], ['COAST_WATERFALL', 160, 138], ['DETAIL_LAMP_ORNATE', 132, 140]])
    place(t, c, r, { tag: 'lakes', on: ON(T.GRASS, T.SAND, T.POND, T.RIVER) });
  scatter([88, 112, 176, 168], [['ANIMAL_DUCK', 3], ['ANIMAL_GOOSE', 1]], 30, { tag: 'lakes', on: ON(T.SAND, T.GRASS), seed: 103 });
  for (let c = 150; c < 155; c++) place('FOREST_PINE_SMALL', c, 130, { tag: 'island', on: ON(T.GRASS) });
}

// ════════════════════════════════════════════════════════════════════════════
// 7. AREAS (district labels — only NEW districts; the forest block's areaTiles stay as input)
// ════════════════════════════════════════════════════════════════════════════
const areas = DISTRICTS.map(([label, , , , , color]) => ({ label, color }));
for (const [label, c0, r0, c1, r1] of DISTRICTS)
  for (let r = r0; r < r1; r++) for (let c = c0; c < c1; c++) if (!inForest(c, r)) areaTiles[idx(c, r)] = label;

const world = {
  version: 1,
  cols: W,
  rows: H,
  layoutRevision: F.layoutRevision,
  tiles,
  tileColors,
  furniture,
  pets: F.pets,
  carpetTiles,
  areaTiles,
  areas,
  home: HOME,
};

// ════════════════════════════════════════════════════════════════════════════
// 8. PRESERVATION CHECK (refuse to write on any difference)
// ════════════════════════════════════════════════════════════════════════════
const problems = [];
for (let r = 0; r < F.rows; r++)
  for (let c = 0; c < F.cols; c++) {
    const i = idx(c + FOREST_DCOL, r + FOREST_DROW);
    const j = r * F.cols + c;
    if (tiles[i] !== F.tiles[j]) problems.push(`tile ${c},${r}`);
    if (JSON.stringify(tileColors[i]) !== JSON.stringify(F.tileColors[j] ?? null)) problems.push(`tileColor ${c},${r}`);
    if (JSON.stringify(carpetTiles[i]) !== JSON.stringify(F.carpetTiles?.[j] ?? null)) problems.push(`carpet ${c},${r}`);
    if ((areaTiles[i] ?? null) !== (F.areaTiles?.[j] ?? null)) problems.push(`area ${c},${r}`);
  }
F.furniture.forEach((f, k) => {
  const g = furniture[k];
  if (g.uid !== f.uid || g.type !== f.type || g.col !== f.col + FOREST_DCOL || g.row !== f.row + FOREST_DROW || JSON.stringify(g.color) !== JSON.stringify(f.color))
    problems.push(`furniture ${f.uid}`);
});
if (JSON.stringify(world.pets) !== JSON.stringify(F.pets)) problems.push('pets');
const newItems = furniture.slice(F.furniture.length);
if (newItems.some((f) => inForest(f.col, f.row))) problems.push('new furniture inside the forest block');
if (newItems.some((f) => f.color)) problems.push('a new item carries a colour override (biome variants are sprites, not hue rotations)');
if (new Set(furniture.map((f) => f.uid)).size !== furniture.length) problems.push('duplicate uid');
if (problems.length) {
  console.error(`REFUSING: ${problems.slice(0, 20).join('; ')}`);
  process.exit(1);
}

const json = JSON.stringify(world, null, 2);
fs.mkdirSync(path.dirname(OUT), { recursive: true });
fs.writeFileSync(OUT, json);
const packs = {};
for (const f of newItems) {
  const p = f.type.split('_')[0];
  packs[p] = (packs[p] ?? 0) + 1;
}
console.log(
  JSON.stringify(
    {
      out: path.relative(ROOT, OUT),
      sha256: crypto.createHash('sha256').update(json).digest('hex'),
      bytesPretty: Buffer.byteLength(json),
      bytesCompact: Buffer.byteLength(JSON.stringify(world)),
      cols: W,
      rows: H,
      home: HOME,
      forest: { sha256: forestSha, translation: [FOREST_DCOL, FOREST_DROW], cells: F.cols * F.rows, furniture: F.furniture.length, pets: F.pets.length, preserved: true },
      furnitureTotal: furniture.length,
      furnitureNew: newItems.length,
      distinctTypesNew: new Set(newItems.map((f) => f.type)).size,
      newByPrefix: packs,
      offices: OFFICES.map((o) => o.label),
      bridges,
    },
    null,
    2,
  ),
);
