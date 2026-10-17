#!/usr/bin/env node
/**
 * Composes the VAST FOREST world: the preserved palace (a verified 64x64 layout export) placed
 * WHOLE at one uniform translation inside a 176 x 112 world of woodland, trails, clearings, a
 * pond and an observation deck on the palace's north lookout. Deterministic: the same palace
 * bytes always give the same world bytes.
 *
 *   node scripts/build-forest-world.mjs --palace <verified-live-export.json> [--out <file>]
 *
 * The palace is copied: its tiles / tileColors / carpetTiles / areaTiles block lands cell-for-cell
 * at (PALACE_DCOL, PALACE_DROW) and every furniture record keeps uid, type and color with
 * col + PALACE_DCOL and row + PALACE_DROW, pets unchanged — EXCEPT the one documented change set
 * of the NORTH GATEWAY below.
 *
 * THE NORTH GATEWAY (human design steering for the palace's north grove, palace cols 21-28,
 * rows 0-8): the grove becomes a grand arch into the forest. The canonical palace has no exterior
 * opening — the grove pocket (palace rows 0-4) is sealed from the interior by the row-5 wall — and
 * the approved acceptance requires a useful observation deck and usable routes, so the gateway's
 * clear approach IS the palace-interior-to-deck route. The change set, and nothing else:
 *   - tiles: a 4-wide threshold through the row-5 wall (PALACE_THRESHOLD), each tile taking the
 *     palace's OWN doorway threshold (tile value and colour of PALACE_DOOR_SOURCE);
 *   - furniture: the grove's five LARGE_PLANTs are RE-SITED (uid, type and colour intact) into
 *     the gateway's planted flanks (PALACE_RESITES); the two lookout benches, the workstation
 *     below and every other palace record are untouched;
 *   - new: the arch (GATEWAY_PILLAR_L / _CROWN / _R, standing on the palace's north edge with
 *     its crown walk-under in every row), braziers and a planter on the deck (GATEWAY_ITEMS).
 * Whole-palace byte equality is therefore NOT claimed for the world; importing the canonical
 * export is still the exact restore path back to the original sealed palace.
 * Forest decor is 'decor' category only (no chairs, so no seats out in the trees), its furniture
 * never overlaps the palace block, and the strip directly above the palace's north wall (where
 * the palace's wall hangings at row -1 now land, at world row PALACE_DROW - 1) stays clear.
 */
import crypto from 'node:crypto';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');

// ── fixed world geometry ────────────────────────────────────────────────────
export const WORLD_COLS = 176;
export const WORLD_ROWS = 112;
export const PALACE_DCOL = 56;
export const PALACE_DROW = 40;
export const PALACE_DIM = 64;
/** sha256 of the canonical palace export this world was composed from. */
export const PALACE_SHA256 = '61b1b525e3da38251fc609ec1089deb19b326f410c2a38d08d9b76aded76310b';
/** The gateway threshold through the row-5 wall (palace coordinates), and the palace doorway cell
 *  its look is copied from (one of the 2-wide doorways in the palace's own row-11 wall). */
export const PALACE_THRESHOLD = [
  [23, 5],
  [24, 5],
  [25, 5],
  [26, 5],
];
export const PALACE_DOOR_SOURCE = [33, 11];
/** The grove's five plants, re-sited into the gateway's planted flanks (palace coordinates). */
export const PALACE_RESITES = {
  'lookout-399': [17, -4],
  'lookout-400': [14, -4],
  'lookout-401': [31, -4],
  'lookout-402': [21, 2],
  'lookout-403': [27, 2],
};
/** The arch and its fittings (palace coordinates; negative rows stand on the deck). */
export const GATEWAY_ITEMS = [
  ['gateway-pillar-w', 'GATEWAY_PILLAR_L', 21, -5],
  ['gateway-crown', 'GATEWAY_CROWN', 23, -5],
  ['gateway-pillar-e', 'GATEWAY_PILLAR_R', 27, -5],
  ['gateway-brazier-w', 'GATEWAY_BRAZIER', 19, -3],
  ['gateway-brazier-e', 'GATEWAY_BRAZIER', 30, -3],
  ['gateway-planter-e', 'GATEWAY_PLANTER', 34, -4],
];
/** Furniture records the forest + deck may spend (the palace keeps its own 406). */
export const FOREST_FURNITURE_BUDGET = 3_400;

// Tile values (webview TileType; FLOOR_10.. are the APPENDED outdoor patterns).
const T = { VOID: 255, GRASS: 10, FOREST: 11, TRAIL: 12, DECK: 13, WATER: 14 };

// Ground colours (floor Colorize mode, same {h,s,b,c} shape as the palace's own tileColors).
const GROUND = {
  [T.FOREST]: [
    { h: 98, s: 30, b: -36, c: 0 },
    { h: 94, s: 30, b: -33, c: 0 },
    { h: 90, s: 28, b: -31, c: 0 },
  ],
  [T.GRASS]: [
    { h: 100, s: 40, b: -17, c: 0 },
    { h: 96, s: 40, b: -15, c: 0 },
    { h: 92, s: 38, b: -13, c: 0 },
  ],
  [T.TRAIL]: [
    { h: 32, s: 34, b: -14, c: 0 },
    { h: 28, s: 30, b: -18, c: 0 },
  ],
  [T.DECK]: [{ h: 30, s: 38, b: -6, c: 0 }],
  [T.WATER]: [
    { h: 200, s: 46, b: -16, c: 0 },
    { h: 204, s: 44, b: -20, c: 0 },
  ],
};
// Furniture tints (Adjust mode): variety between neighbouring trees. null = the sprite as drawn.
const TINTS = [
  null,
  { h: 0, s: 0, b: -12, c: 0 },
  { h: 10, s: -6, b: -6, c: 0 },
  { h: -10, s: 6, b: 4, c: 0 },
];

// Generated items: footprint and background rows (must match scripts/generate-forest-assets.mjs).
const ITEM = {
  FOREST_PINE_TALL: [2, 4, 3],
  FOREST_PINE: [2, 3, 2],
  FOREST_PINE_SMALL: [1, 2, 1],
  FOREST_OAK: [3, 3, 2],
  FOREST_OAK_SMALL: [2, 2, 1],
  FOREST_BIRCH: [2, 3, 2],
  FOREST_BUSH: [1, 1, 0],
  FOREST_FERN: [1, 1, 1],
  FOREST_FLOWERS: [1, 1, 1],
  FOREST_ROCK: [1, 1, 0],
  FOREST_BOULDER: [2, 2, 1],
  FOREST_STUMP: [1, 1, 0],
  FOREST_LOG: [2, 1, 0],
  DECK_RAILING: [1, 1, 0],
  DECK_RAILING_SIDE: [1, 1, 0],
  DECK_TELESCOPE: [1, 2, 1],
  DECK_LANTERN: [1, 2, 1],
  DECK_BENCH: [2, 1, 0],
  DECK_SIGNPOST: [1, 2, 1],
};

const TREES = new Set([
  'FOREST_PINE_TALL',
  'FOREST_PINE',
  'FOREST_PINE_SMALL',
  'FOREST_OAK',
  'FOREST_OAK_SMALL',
  'FOREST_BIRCH',
]);

// The observation deck (world coords): on the palace's north lookout (palace row 0, cols 21-28).
export const DECK = { c0: 66, c1: 95, r0: 26, r1: 39, stairC0: 79, stairC1: 82 };

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

/** Smooth value noise in [0,1) on a coarse lattice (deterministic). */
function valueNoise(seed, scale) {
  const r = rng(seed);
  const gw = Math.ceil(WORLD_COLS / scale) + 2;
  const gh = Math.ceil(WORLD_ROWS / scale) + 2;
  const lattice = Array.from({ length: gw * gh }, () => r());
  const at = (x, y) => lattice[y * gw + x];
  const smooth = (t) => t * t * (3 - 2 * t);
  return (c, r2) => {
    const fx = c / scale;
    const fy = r2 / scale;
    const x0 = Math.floor(fx);
    const y0 = Math.floor(fy);
    const tx = smooth(fx - x0);
    const ty = smooth(fy - y0);
    const a = at(x0, y0) * (1 - tx) + at(x0 + 1, y0) * tx;
    const b = at(x0, y0 + 1) * (1 - tx) + at(x0 + 1, y0 + 1) * tx;
    return a * (1 - ty) + b * ty;
  };
}

export function composeForestWorld(palace) {
  if (palace.cols !== PALACE_DIM || palace.rows !== PALACE_DIM)
    throw new Error('palace is not 64x64');
  const W = WORLD_COLS;
  const H = WORLD_ROWS;
  const idx = (c, r) => r * W + c;
  const inPalace = (c, r) =>
    c >= PALACE_DCOL &&
    c < PALACE_DCOL + PALACE_DIM &&
    r >= PALACE_DROW &&
    r < PALACE_DROW + PALACE_DIM;
  const kind = new Array(W * H).fill(T.FOREST);

  // ── trails, clearings, pond, deck (tile layer) ──
  const paint = (c, r, k) => {
    if (c < 0 || r < 0 || c >= W || r >= H || inPalace(c, r)) return;
    kind[idx(c, r)] = k;
  };
  const ellipse = (cx, cy, rx, ry, k, jitterSeed) => {
    const jr = rng(jitterSeed);
    for (let r = Math.floor(cy - ry - 1); r <= cy + ry + 1; r++)
      for (let c = Math.floor(cx - rx - 1); c <= cx + rx + 1; c++) {
        const d = ((c - cx) / rx) ** 2 + ((r - cy) / ry) ** 2;
        if (d <= 1 + (jr() - 0.5) * 0.25) paint(c, r, k);
      }
  };
  const trail = (points, width = 2) => {
    for (let i = 0; i < points.length - 1; i++) {
      const [x0, y0] = points[i];
      const [x1, y1] = points[i + 1];
      const steps = Math.max(Math.abs(x1 - x0), Math.abs(y1 - y0)) * 2;
      for (let s = 0; s <= steps; s++) {
        const x = Math.round(x0 + ((x1 - x0) * s) / steps);
        const y = Math.round(y0 + ((y1 - y0) * s) / steps);
        for (let dx = 0; dx < width; dx++)
          for (let dy = 0; dy < width; dy++) paint(x + dx, y + dy, T.TRAIL);
      }
    }
  };
  const wiggle = (x0, y0, x1, y1, amp, period, seed) => {
    const pts = [];
    const n = Math.max(2, Math.round(Math.hypot(x1 - x0, y1 - y0) / 3));
    const ph = rng(seed)() * Math.PI * 2;
    for (let i = 0; i <= n; i++) {
      const t = i / n;
      const bx = x0 + (x1 - x0) * t;
      const by = y0 + (y1 - y0) * t;
      const len = Math.hypot(x1 - x0, y1 - y0) || 1;
      const nx = -(y1 - y0) / len;
      const ny = (x1 - x0) / len;
      const off = i === 0 || i === n ? 0 : amp * Math.sin((t * len) / period + ph);
      pts.push([Math.round(bx + nx * off), Math.round(by + ny * off)]);
    }
    return pts;
  };

  // clearings and meadows
  const CLEARINGS = [
    [80, 7, 10, 5],
    [33, 14, 15, 9],
    [143, 14, 12, 7],
    [22, 63, 12, 9],
    [153, 70, 12, 9],
    [20, 100, 11, 6],
    [156, 101, 11, 6],
    [112, 8, 6, 4],
    [52, 30, 5, 3],
    [8, 34, 5, 4],
    [168, 40, 5, 6],
  ];
  CLEARINGS.forEach(([cx, cy, rx, ry], i) => ellipse(cx, cy, rx, ry, T.GRASS, 100 + i));
  // the pond in the north-west vista
  ellipse(33, 14, 8, 4, T.WATER, 200);

  // trails: the deck stair north, a loop around the palace, and branches to every clearing
  trail(wiggle(DECK.stairC0 + 1, DECK.r0 - 1, 80, 9, 3, 4, 301));
  trail(wiggle(46, 21, 130, 21, 2, 6, 302));
  trail(wiggle(50, 21, 50, 107, 2, 7, 303));
  trail(wiggle(126, 21, 126, 107, 2, 7, 304));
  trail(wiggle(50, 107, 127, 107, 1, 8, 305));
  trail(wiggle(46, 21, 38, 22, 1, 4, 306));
  trail(wiggle(130, 21, 141, 19, 1, 4, 307));
  trail(wiggle(50, 62, 30, 63, 3, 5, 308));
  trail(wiggle(127, 70, 143, 70, 3, 5, 309));
  trail(wiggle(50, 104, 26, 100, 2, 5, 310));
  trail(wiggle(127, 104, 148, 101, 2, 5, 311));
  trail(wiggle(84, 9, 106, 8, 2, 5, 312));
  trail(wiggle(41, 18, 52, 21, 1, 4, 313));

  // the deck: plank floor on the lookout, stairs gap in the north railing
  for (let r = DECK.r0; r <= DECK.r1; r++)
    for (let c = DECK.c0; c <= DECK.c1; c++) paint(c, r, T.DECK);

  // ── tiles + tileColors (+ empty carpet / area layers) ──
  const shadeNoise = valueNoise(7, 9);
  const dither = rng(77);
  const tiles = new Array(W * H);
  const tileColors = new Array(W * H);
  const carpetTiles = new Array(W * H).fill(null);
  const areaTiles = new Array(W * H).fill(null);
  for (let r = 0; r < H; r++)
    for (let c = 0; c < W; c++) {
      const i = idx(c, r);
      if (inPalace(c, r)) {
        const pi = (r - PALACE_DROW) * PALACE_DIM + (c - PALACE_DCOL);
        tiles[i] = palace.tiles[pi];
        tileColors[i] = palace.tileColors ? palace.tileColors[pi] : null;
        carpetTiles[i] = palace.carpetTiles ? palace.carpetTiles[pi] : null;
        areaTiles[i] = palace.areaTiles ? palace.areaTiles[pi] : null;
        continue;
      }
      const k = kind[i];
      tiles[i] = k;
      const shades = GROUND[k];
      const n = shadeNoise(c, r) + (dither() - 0.5) * 0.35;
      const shade = Math.max(0, Math.min(shades.length - 1, Math.floor(n * shades.length)));
      tileColors[i] = { ...shades[shade] };
    }

  // ── the gateway threshold: the palace's own doorway tile through its row-5 wall ──
  const [sc, sr] = PALACE_DOOR_SOURCE;
  const doorTile = palace.tiles[sr * PALACE_DIM + sc];
  const doorColor = palace.tileColors?.[sr * PALACE_DIM + sc] ?? null;
  for (const [pcol, prow] of PALACE_THRESHOLD) {
    const i = idx(pcol + PALACE_DCOL, prow + PALACE_DROW);
    if (tiles[i] !== 0) throw new Error(`doorway (${pcol},${prow}) is not a palace wall`);
    tiles[i] = doorTile;
    tileColors[i] = doorColor ? { ...doorColor } : null;
  }

  // ── furniture ──
  const furniture = palace.furniture.map((f) => {
    const [col, row] = PALACE_RESITES[f.uid] ?? [f.col, f.row];
    return { ...f, col: col + PALACE_DCOL, row: row + PALACE_DROW };
  });
  for (const uid of Object.keys(PALACE_RESITES))
    if (!palace.furniture.some((f) => f.uid === uid))
      throw new Error(`re-site: ${uid} not in the palace`);
  const gateway = GATEWAY_ITEMS.map(([uid, type, col, row]) => ({
    uid,
    type,
    col: col + PALACE_DCOL,
    row: row + PALACE_DROW,
  }));
  const forest = [];
  const trunk = new Set(); // cells blocked by a placed item's non-background rows
  const covered = new Set(); // every footprint cell of a placed item
  const key = (c, r) => `${c},${r}`;
  const reservedStrip = (c, r) =>
    r === PALACE_DROW - 1 && c >= PALACE_DCOL && c < PALACE_DCOL + PALACE_DIM;
  const canopyOk = (c, r) =>
    c >= 0 &&
    r >= 0 &&
    c < W &&
    r < H &&
    !inPalace(c, r) &&
    !reservedStrip(c, r) &&
    kind[idx(c, r)] !== T.DECK;
  const place = (type, col, row, list, uid, color, groundOk) => {
    const [fw, fh, bg] = ITEM[type];
    for (let dr = 0; dr < fh; dr++)
      for (let dc = 0; dc < fw; dc++) {
        const c = col + dc;
        const r = row + dr;
        if (!canopyOk(c, r) && !(groundOk && groundOk(c, r, dr >= bg))) return false;
        if (dr >= bg) {
          if (trunk.has(key(c, r))) return false;
          const k = kind[idx(c, r)];
          // Trees root in the woodland only; small decor may also sit in a clearing.
          const rootOk = TREES.has(type) ? k === T.FOREST : k === T.FOREST || k === T.GRASS;
          if (!groundOk && !rootOk) return false;
        }
      }
    for (let dr = 0; dr < fh; dr++)
      for (let dc = 0; dc < fw; dc++) {
        covered.add(key(col + dc, row + dr));
        if (dr >= bg) trunk.add(key(col + dc, row + dr));
      }
    const item = { uid, type, col, row };
    if (color) item.color = { ...color };
    list.push(item);
    return true;
  };

  // deck fittings (railings, telescopes, lanterns, benches, trail sign)
  const deckItems = [];
  let d = 0;
  const deckUid = () => `deck-${String(++d).padStart(3, '0')}`;
  const onDeck = (c, r) => kind[idx(c, r)] === T.DECK && !reservedStrip(c, r);
  const deckPlace = (type, c, r) => place(type, c, r, deckItems, deckUid(), null, onDeck);
  for (let c = DECK.c0; c <= DECK.c1; c++) {
    if (c >= DECK.stairC0 && c <= DECK.stairC1) continue;
    deckPlace('DECK_RAILING', c, DECK.r0);
  }
  for (let r = DECK.r0 + 1; r <= DECK.r1 - 1; r++) {
    deckPlace('DECK_RAILING_SIDE', DECK.c0, r);
    deckPlace('DECK_RAILING_SIDE', DECK.c1, r);
  }
  for (const c of [70, 75, 86, 91]) deckPlace('DECK_TELESCOPE', c, DECK.r0 + 1);
  for (const [c, r] of [
    [DECK.c0 + 1, DECK.r0 + 1],
    [DECK.c1 - 1, DECK.r0 + 1],
    [DECK.c0 + 1, DECK.r1 - 3],
    [DECK.c1 - 1, DECK.r1 - 3],
    [DECK.stairC0 - 1, DECK.r0 + 1],
    [DECK.stairC1 + 1, DECK.r0 + 1],
  ])
    deckPlace('DECK_LANTERN', c, r);
  for (const c of [69, 73, 85, 89]) deckPlace('DECK_BENCH', c, DECK.r0 + 4);
  for (const c of [69, 73, 85, 89]) deckPlace('DECK_BENCH', c, DECK.r0 + 7);
  place('DECK_SIGNPOST', DECK.stairC1 + 2, DECK.r0 - 3, deckItems, deckUid(), null);

  // trees: jittered lattice, density from noise, kinds by region; tree lines on clearing edges
  const r = rng(424242);
  const density = valueNoise(11, 14);
  const nearOpen = (c, rr, dist) => {
    for (let dr = -dist; dr <= dist; dr++)
      for (let dc = -dist; dc <= dist; dc++) {
        const cc = c + dc;
        const r2 = rr + dr;
        if (cc < 0 || r2 < 0 || cc >= W || r2 >= H) continue;
        const k = kind[idx(cc, r2)];
        if (k === T.GRASS || k === T.WATER) return true;
      }
    return false;
  };
  const pickTree = (c, rr) => {
    const north = rr < PALACE_DROW - 4;
    const west = c < PALACE_DCOL;
    const roll = r();
    if (north) {
      if (roll < 0.42) return 'FOREST_PINE_TALL';
      if (roll < 0.72) return 'FOREST_PINE';
      if (roll < 0.84) return 'FOREST_PINE_SMALL';
      if (roll < 0.93) return 'FOREST_BIRCH';
      return 'FOREST_OAK';
    }
    if (west) {
      if (roll < 0.34) return 'FOREST_OAK';
      if (roll < 0.52) return 'FOREST_BIRCH';
      if (roll < 0.74) return 'FOREST_PINE';
      if (roll < 0.86) return 'FOREST_OAK_SMALL';
      return 'FOREST_PINE_TALL';
    }
    if (roll < 0.3) return 'FOREST_PINE_TALL';
    if (roll < 0.52) return 'FOREST_OAK';
    if (roll < 0.7) return 'FOREST_PINE';
    if (roll < 0.84) return 'FOREST_BIRCH';
    return 'FOREST_OAK_SMALL';
  };
  let f = 0;
  const forestUid = () => `forest-${String(++f).padStart(4, '0')}`;
  for (let gy = 0; gy < H + 3; gy += 2)
    for (let gx = 0; gx < W; gx += 2) {
      const c = gx + Math.floor(r() * 2);
      const rr = gy + Math.floor(r() * 2); // the trunk row
      const type0 = pickTree(c, rr);
      const edge = nearOpen(c, rr, 2);
      const p = edge ? 0.94 : 0.58 + 0.4 * density(c, rr);
      if (r() > p) continue;
      const type = edge && type0 === 'FOREST_PINE_SMALL' ? 'FOREST_PINE' : type0;
      const [fw, fh] = ITEM[type];
      const col = c - Math.floor((fw - 1) / 2);
      const row = rr - fh + 1;
      const tint = TINTS[Math.floor(r() * TINTS.length)];
      if (!place(type, col, row, forest, forestUid(), tint)) {
        // a smaller tree where the big one does not fit
        place('FOREST_PINE_SMALL', c, rr - 1, forest, forestUid(), tint);
      }
    }
  // undergrowth along trail and clearing edges, flowers and stones in the open
  for (let rr = 0; rr < H; rr++)
    for (let c = 0; c < W; c++) {
      const k = kind[idx(c, rr)];
      if (covered.has(key(c, rr)) || inPalace(c, rr) || reservedStrip(c, rr)) continue;
      const roll = r();
      if (k === T.FOREST) {
        if (nearOpen(c, rr, 1) || kindNear(kind, W, H, c, rr, T.TRAIL)) {
          if (roll < 0.1) place('FOREST_FERN', c, rr, forest, forestUid(), null);
          else if (roll < 0.17) place('FOREST_BUSH', c, rr, forest, forestUid(), null);
        } else if (roll < 0.012) place('FOREST_ROCK', c, rr, forest, forestUid(), null);
        else if (roll < 0.02) place('FOREST_STUMP', c, rr, forest, forestUid(), null);
        else if (roll < 0.026) place('FOREST_LOG', c, rr, forest, forestUid(), null);
        else if (roll < 0.06) place('FOREST_FERN', c, rr, forest, forestUid(), null);
      } else if (k === T.GRASS) {
        if (roll < 0.06) place('FOREST_FLOWERS', c, rr, forest, forestUid(), null);
        else if (roll < 0.068) place('FOREST_ROCK', c, rr, forest, forestUid(), null);
        else if (roll < 0.071) place('FOREST_BOULDER', c, rr, forest, forestUid(), null);
      }
    }

  const added = gateway.length + deckItems.length + forest.length;
  if (added > FOREST_FURNITURE_BUDGET)
    throw new Error(
      `forest + deck furniture ${added} exceeds the budget ${FOREST_FURNITURE_BUDGET}`,
    );
  furniture.push(...gateway, ...deckItems, ...forest);

  // Same key order as the office's own export, so import -> export is byte-identical.
  return {
    version: 1,
    cols: W,
    rows: H,
    layoutRevision: palace.layoutRevision ?? 1,
    tiles,
    tileColors,
    furniture,
    pets: palace.pets ?? [],
    carpetTiles,
    areaTiles,
    areas: palace.areas ?? [],
  };
}

function kindNear(kind, W, H, c, r, k) {
  for (const [dc, dr] of [
    [1, 0],
    [-1, 0],
    [0, 1],
    [0, -1],
  ]) {
    const cc = c + dc;
    const rr = r + dr;
    if (cc >= 0 && rr >= 0 && cc < W && rr < H && kind[rr * W + cc] === k) return true;
  }
  return false;
}

function main() {
  const args = process.argv.slice(2);
  const get = (flag) => {
    const i = args.indexOf(flag);
    return i >= 0 ? args[i + 1] : undefined;
  };
  const palacePath = get('--palace');
  const out = get('--out') ?? path.join(ROOT, 'packaging/di/worlds/vast-forest.json');
  if (!palacePath) {
    console.error(
      'usage: build-forest-world.mjs --palace <verified-live-export.json> [--out <file>]',
    );
    process.exit(2);
  }
  const bytes = fs.readFileSync(palacePath);
  const sha = crypto.createHash('sha256').update(bytes).digest('hex');
  if (sha !== PALACE_SHA256) {
    console.error(`palace sha256 ${sha} is not the canonical ${PALACE_SHA256}`);
    process.exit(1);
  }
  const world = composeForestWorld(JSON.parse(bytes.toString('utf-8')));
  const json = JSON.stringify(world, null, 2);
  fs.mkdirSync(path.dirname(out), { recursive: true });
  fs.writeFileSync(out, json);
  const outSha = crypto.createHash('sha256').update(json).digest('hex');
  console.log(
    `wrote ${out}: ${world.cols}x${world.rows}, ${world.furniture.length} furniture ` +
      `(${world.furniture.length - JSON.parse(bytes.toString('utf-8')).furniture.length} forest+deck), ` +
      `${Buffer.byteLength(json)} bytes pretty, ${Buffer.byteLength(JSON.stringify(world))} bytes compact, sha256 ${outSha}`,
  );
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) main();
