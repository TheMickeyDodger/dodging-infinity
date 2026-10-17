#!/usr/bin/env node
/**
 * Generates the forest world's ORIGINAL pixel art: trees, undergrowth, rocks, the observation
 * deck's railings and fittings, and the outdoor floor patterns. Everything is drawn here, in
 * code, from a fixed seed — no external image, tileset or font is read — so the output carries
 * no third-party licence obligation (it is part of this project, MIT).
 *
 *   node scripts/generate-forest-assets.mjs            # (re)write the assets in place
 *   node scripts/generate-forest-assets.mjs --check    # verify the committed bytes match
 *
 * Output (deterministic, byte-stable):
 *   webview-ui/public/assets/floors/floor_9.png .. floor_13.png
 *     APPENDED floor patterns (tile values FLOOR_10..FLOOR_14). Existing floor_0..floor_8 —
 *     tile values FLOOR_1..FLOOR_9, which the preserved palace uses — are never touched.
 *   webview-ui/public/assets/furniture/FOREST_* / DECK_* / GATEWAY_* (manifest.json + PNG)
 *     All 'decor' category: none is a chair, so none creates a seat.
 */
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

import { PNG } from 'pngjs';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const ASSETS = path.join(ROOT, 'webview-ui/public/assets');
const CHECK = process.argv.includes('--check');

// ── deterministic randomness ────────────────────────────────────────────────
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

// ── a tiny RGBA canvas ──────────────────────────────────────────────────────
class Sprite {
  constructor(w, h) {
    this.w = w;
    this.h = h;
    this.px = new Array(w * h).fill(null); // [r,g,b] | null
  }
  in(x, y) {
    return x >= 0 && y >= 0 && x < this.w && y < this.h;
  }
  get(x, y) {
    return this.in(x, y) ? this.px[y * this.w + x] : null;
  }
  set(x, y, c) {
    if (this.in(x, y)) this.px[y * this.w + x] = c;
  }
  /** Recolour every opaque pixel that touches transparency (4-neighbour) — the dark outline. */
  outline(color) {
    const edge = [];
    for (let y = 0; y < this.h; y++)
      for (let x = 0; x < this.w; x++) {
        if (!this.get(x, y)) continue;
        if (
          !this.get(x - 1, y) ||
          !this.get(x + 1, y) ||
          !this.get(x, y - 1) ||
          !this.get(x, y + 1)
        )
          edge.push([x, y]);
      }
    for (const [x, y] of edge) this.set(x, y, color);
  }
  toPng() {
    const png = new PNG({ width: this.w, height: this.h });
    for (let i = 0; i < this.px.length; i++) {
      const c = this.px[i];
      png.data[i * 4] = c ? c[0] : 0;
      png.data[i * 4 + 1] = c ? c[1] : 0;
      png.data[i * 4 + 2] = c ? c[2] : 0;
      png.data[i * 4 + 3] = c ? 255 : 0;
    }
    return PNG.sync.write(png, { colorType: 6 });
  }
}

const gray = (v) => [v, v, v];

// ── palettes (RGB) ──────────────────────────────────────────────────────────
const PINE = {
  outline: [20, 38, 32],
  dark: [30, 70, 52],
  mid: [42, 98, 64],
  light: [64, 130, 76],
  hi: [104, 164, 94],
};
const OAK = {
  outline: [26, 44, 22],
  dark: [44, 84, 36],
  mid: [64, 116, 46],
  light: [94, 148, 58],
  hi: [138, 182, 80],
};
const BIRCH = {
  outline: [36, 52, 24],
  dark: [70, 106, 40],
  mid: [100, 140, 52],
  light: [136, 170, 70],
  hi: [178, 202, 104],
};
const BARK = {
  outline: [38, 24, 16],
  dark: [70, 46, 28],
  mid: [100, 68, 40],
  light: [128, 90, 54],
};
const BIRCH_BARK = {
  outline: [52, 50, 46],
  dark: [150, 146, 136],
  mid: [206, 202, 190],
  light: [232, 230, 220],
  mark: [60, 58, 54],
};
const STONE = {
  outline: [40, 42, 50],
  dark: [86, 90, 100],
  mid: [118, 122, 132],
  light: [156, 160, 168],
};
const DECK_WOOD = {
  outline: [56, 38, 24],
  dark: [140, 104, 66],
  mid: [186, 148, 100],
  light: [218, 186, 138],
};
const BRASS = {
  outline: [60, 40, 14],
  dark: [140, 98, 34],
  mid: [196, 150, 60],
  light: [236, 204, 120],
};
const LAMP = { glass: [255, 214, 120], glow: [255, 240, 186] };
const FLOWER = [
  [214, 72, 72],
  [236, 196, 72],
  [232, 232, 236],
  [168, 112, 214],
];

// ── trees ───────────────────────────────────────────────────────────────────
function trunk(s, cx, top, bottom, half, bark) {
  for (let y = top; y <= bottom; y++) {
    const flare = y >= bottom - 1 ? 1 : 0;
    for (let x = cx - half - flare; x <= cx + half - 1 + flare; x++) {
      const rel = (x - (cx - half)) / (2 * half);
      s.set(x, y, rel < 0.3 ? bark.light : rel > 0.7 ? bark.dark : bark.mid);
    }
  }
}

/** A conifer: stacked, overlapping triangular tiers lit from the upper left. */
function pine(w, h, seed, tiers) {
  const s = new Sprite(w, h);
  const r = rng(seed);
  const cx = w / 2;
  const trunkTop = h - Math.round(h * 0.2);
  trunk(s, cx, trunkTop - 2, h - 1, Math.max(1, Math.round(w / 14)), BARK);
  const top = 1;
  const bottom = trunkTop + 1;
  const span = bottom - top;
  for (let i = 0; i < tiers; i++) {
    const ty = top + Math.round((span * i) / (tiers + 0.6));
    const th = Math.round(span / tiers) + Math.round(h / 14);
    const maxHalf = (w / 2 - 1) * (0.42 + (0.58 * (i + 1)) / tiers);
    for (let y = ty; y < Math.min(ty + th, bottom); y++) {
      const t = (y - ty + 1) / th;
      const half = Math.max(1, maxHalf * t);
      for (let x = Math.floor(cx - half); x < Math.ceil(cx + half); x++) {
        const rel = (x - (cx - half)) / (2 * half);
        let c = PINE.mid;
        if (rel < 0.34) c = PINE.light;
        if (rel > 0.68) c = PINE.dark;
        if (y >= ty + th - 2) c = PINE.dark; // the shadow line under each tier
        if (c !== PINE.dark && r() < 0.12) c = rel < 0.5 ? PINE.hi : PINE.mid;
        if (c === PINE.mid && r() < 0.08) c = PINE.dark;
        s.set(x, y, c);
      }
    }
  }
  s.outline(PINE.outline);
  return s;
}

/** A broadleaf: a canopy of overlapping leaf clumps over a short trunk. */
function broadleaf(w, h, seed, pal, bark, clumps) {
  const s = new Sprite(w, h);
  const r = rng(seed);
  const cx = w / 2;
  const trunkTop = Math.round(h * 0.62);
  trunk(s, cx, trunkTop, h - 1, Math.max(2, Math.round(w / 14)), bark);
  if (bark === BIRCH_BARK) {
    for (let y = trunkTop + 1; y < h - 1; y += 3) s.set(cx - 1 + (y % 2), y, BIRCH_BARK.mark);
  }
  // Clumps are drawn back to front: the lower clumps overlap the higher ones.
  const sorted = [...clumps].sort((a, b) => a[1] - b[1]);
  for (const [ccx, ccy, rad] of sorted) {
    for (let y = Math.floor(ccy - rad); y <= Math.ceil(ccy + rad); y++)
      for (let x = Math.floor(ccx - rad); x <= Math.ceil(ccx + rad); x++) {
        const dx = x - ccx;
        const dy = y - ccy;
        const d = Math.sqrt(dx * dx + dy * dy);
        if (d > rad + (r() - 0.5) * 0.9) continue;
        const lit = (-dx - dy) / (rad * 1.4); // upper left is lit
        let c = pal.mid;
        if (lit > 0.35) c = pal.light;
        if (lit > 0.7 && r() < 0.5) c = pal.hi;
        if (lit < -0.35) c = pal.dark;
        if (d > rad - 1.2 && dy > 0) c = pal.dark; // the clump's lower rim
        if (r() < 0.07) c = c === pal.dark ? pal.mid : pal.dark;
        s.set(x, y, c);
      }
  }
  s.outline(pal.outline);
  return s;
}

function clumpsFor(w, h, r, count, spread) {
  const cx = w / 2;
  const out = [[cx, h * 0.36, w * 0.3]];
  for (let i = 0; i < count; i++) {
    const a = (i / count) * Math.PI * 2 + r() * 0.5;
    out.push([
      cx + Math.cos(a) * w * spread * (0.8 + r() * 0.3),
      h * 0.34 + Math.sin(a) * h * spread * 0.62,
      w * (0.17 + r() * 0.06),
    ]);
  }
  return out;
}

// ── undergrowth, rocks, deadwood ────────────────────────────────────────────
function bush(seed) {
  const s = broadleaf(16, 16, seed, OAK, BARK, [
    [8, 9, 6.2],
    [4.5, 10, 3.6],
    [11.5, 10, 3.6],
    [8, 6, 4],
  ]);
  // No trunk under a bush: clear the stub and re-outline.
  for (let y = 14; y < 16; y++) for (let x = 0; x < 16; x++) s.set(x, y, null);
  s.outline(OAK.outline);
  return s;
}

function fern(seed) {
  const s = new Sprite(16, 16);
  const r = rng(seed);
  const fronds = [
    [-1, -0.9],
    [-0.7, -1],
    [0, -1],
    [0.7, -1],
    [1, -0.85],
  ];
  for (const [fx, fy] of fronds) {
    for (let t = 0; t < 7; t++) {
      const x = Math.round(8 + fx * t);
      const y = Math.round(14 + fy * t);
      s.set(x, y, t < 3 ? PINE.dark : r() < 0.5 ? PINE.light : PINE.mid);
      if (t > 1 && t < 6) {
        s.set(x + 1, y, PINE.mid);
        s.set(x - 1, y + 1, PINE.light);
      }
    }
  }
  s.outline(PINE.outline);
  return s;
}

function flowers(seed) {
  const s = new Sprite(16, 16);
  const r = rng(seed);
  for (let i = 0; i < 7; i++) {
    const x = 2 + Math.floor(r() * 12);
    const y = 5 + Math.floor(r() * 9);
    s.set(x, y + 1, OAK.dark);
    s.set(x, y + 2, OAK.dark);
    const c = FLOWER[Math.floor(r() * FLOWER.length)];
    s.set(x, y, c);
    s.set(x - 1, y, c);
    s.set(x + 1, y, c);
    s.set(x, y - 1, c);
    s.set(x, y + 0, [250, 236, 160]);
  }
  for (let i = 0; i < 6; i++) {
    const x = 1 + Math.floor(r() * 14);
    s.set(x, 14, OAK.mid);
    s.set(x, 13, OAK.light);
  }
  return s;
}

function rock(seed, w, h) {
  const s = new Sprite(w, h);
  const r = rng(seed);
  const cx = w / 2;
  const cy = h * 0.6;
  const rx = w * 0.42;
  const ry = h * 0.36;
  for (let y = 0; y < h; y++)
    for (let x = 0; x < w; x++) {
      const dx = (x - cx) / rx;
      const dy = (y - cy) / ry;
      const d = dx * dx + dy * dy;
      if (d > 1 + (r() - 0.5) * 0.12) continue;
      let c = STONE.mid;
      if (dx + dy < -0.5) c = STONE.light;
      if (dx + dy > 0.6 || dy > 0.6) c = STONE.dark;
      if (r() < 0.06) c = STONE.dark;
      s.set(x, y, c);
    }
  // a few moss specks
  for (let i = 0; i < 4; i++)
    s.set(Math.floor(cx - rx / 2 + r() * rx), Math.floor(cy - ry / 2), OAK.light);
  s.outline(STONE.outline);
  return s;
}

function stump() {
  const s = new Sprite(16, 16);
  for (let y = 7; y < 15; y++)
    for (let x = 4; x < 12; x++) s.set(x, y, x < 6 ? BARK.light : x > 9 ? BARK.dark : BARK.mid);
  for (let x = 3; x < 13; x++) s.set(x, 14, BARK.dark);
  for (let y = 5; y < 9; y++)
    for (let x = 4; x < 12; x++) {
      const d = Math.abs(x - 7.5) / 4 + Math.abs(y - 6.5) / 2;
      if (d <= 1) s.set(x, y, d < 0.45 ? DECK_WOOD.mid : DECK_WOOD.light);
    }
  s.outline(BARK.outline);
  return s;
}

function log() {
  const s = new Sprite(32, 16);
  for (let y = 7; y < 14; y++)
    for (let x = 2; x < 28; x++) s.set(x, y, y < 9 ? BARK.light : y > 11 ? BARK.dark : BARK.mid);
  for (let y = 7; y < 14; y++)
    for (let x = 27; x < 31; x++) {
      const d = Math.abs(x - 28.5) / 2 + Math.abs(y - 10) / 3.5;
      if (d <= 1) s.set(x, y, d < 0.5 ? DECK_WOOD.mid : DECK_WOOD.light);
    }
  for (let x = 6; x < 26; x += 5) s.set(x, 10, BARK.dark);
  s.set(12, 6, OAK.mid);
  s.set(13, 6, OAK.light);
  s.set(20, 6, OAK.mid);
  s.outline(BARK.outline);
  return s;
}

// ── observation deck fittings ───────────────────────────────────────────────
/** A railing segment that tiles seamlessly left to right (rails continuous, posts every 8 px). */
function railing() {
  const s = new Sprite(16, 16);
  for (const x of [3, 11])
    for (let y = 4; y < 15; y++) (s.set(x, y, DECK_WOOD.light), s.set(x + 1, y, DECK_WOOD.dark));
  for (let x = 0; x < 16; x++) {
    s.set(x, 3, DECK_WOOD.outline);
    s.set(x, 4, DECK_WOOD.light);
    s.set(x, 5, DECK_WOOD.mid);
    s.set(x, 6, DECK_WOOD.outline);
    s.set(x, 9, DECK_WOOD.mid);
    s.set(x, 10, DECK_WOOD.dark);
    s.set(x, 15, DECK_WOOD.outline);
  }
  return s;
}

/** A railing segment that tiles seamlessly top to bottom (the deck's east and west edges). */
function railingSide() {
  const s = new Sprite(16, 16);
  for (let y = 0; y < 16; y++) {
    s.set(6, y, DECK_WOOD.outline);
    s.set(7, y, DECK_WOOD.light);
    s.set(8, y, DECK_WOOD.mid);
    s.set(9, y, DECK_WOOD.outline);
  }
  for (const y of [2, 10])
    for (let dy = 0; dy < 4; dy++)
      for (let x = 5; x < 11; x++)
        s.set(x, y + dy, dy === 0 ? DECK_WOOD.light : x < 8 ? DECK_WOOD.mid : DECK_WOOD.dark);
  return s;
}

function telescope() {
  const s = new Sprite(16, 32);
  // tripod
  for (let y = 18; y < 31; y++) {
    const t = (y - 18) / 12;
    s.set(Math.round(8 - t * 5), y, BARK.dark);
    s.set(Math.round(8 + t * 5), y, BARK.dark);
    s.set(8, y, BARK.mid);
  }
  // tube, angled up towards the north-west
  for (let i = 0; i < 12; i++) {
    const x = 12 - i;
    const y = 18 - Math.round(i * 0.6);
    s.set(x, y, BRASS.mid);
    s.set(x, y - 1, BRASS.light);
    s.set(x, y + 1, BRASS.dark);
  }
  s.set(1, 11, BRASS.light);
  s.set(1, 12, LAMP.glow);
  s.outline(BRASS.outline);
  return s;
}

function lantern() {
  const s = new Sprite(16, 32);
  for (let y = 12; y < 31; y++) (s.set(7, y, BARK.mid), s.set(8, y, BARK.dark));
  for (let x = 5; x < 11; x++) s.set(x, 30, BARK.dark);
  for (let y = 4; y < 12; y++)
    for (let x = 5; x < 11; x++)
      s.set(
        x,
        y,
        y === 4 || y === 11
          ? BARK.dark
          : x === 5 || x === 10
            ? BARK.mid
            : y < 7
              ? LAMP.glow
              : LAMP.glass,
      );
  for (let x = 4; x < 12; x++) s.set(x, 3, BARK.dark);
  s.outline(BARK.outline);
  return s;
}

/** A bench seen from behind (facing north, out over the forest). DECOR, not a chair: no seat. */
function deckBench() {
  const s = new Sprite(32, 16);
  for (let x = 2; x < 30; x++) {
    s.set(x, 4, DECK_WOOD.light);
    s.set(x, 5, DECK_WOOD.mid);
    s.set(x, 7, DECK_WOOD.light);
    s.set(x, 8, DECK_WOOD.mid);
    s.set(x, 10, DECK_WOOD.dark);
  }
  for (const x of [4, 27])
    for (let y = 4; y < 15; y++) (s.set(x, y, DECK_WOOD.dark), s.set(x + 1, y, DECK_WOOD.mid));
  s.outline(DECK_WOOD.outline);
  return s;
}

function signpost() {
  const s = new Sprite(16, 32);
  for (let y = 10; y < 31; y++) (s.set(7, y, BARK.mid), s.set(8, y, BARK.dark));
  for (let y = 8; y < 14; y++)
    for (let x = 1; x < 14; x++) s.set(x, y, y === 8 ? DECK_WOOD.light : DECK_WOOD.mid);
  for (let y = 9; y < 13; y++) s.set(14, y, DECK_WOOD.mid);
  (s.set(15, 10, DECK_WOOD.mid), s.set(15, 11, DECK_WOOD.mid));
  for (const x of [3, 5, 7, 9, 11]) s.set(x, 11, DECK_WOOD.dark);
  s.outline(DECK_WOOD.outline);
  return s;
}

// ── floor patterns (grayscale, 16x16, tile seamlessly; colorized per tile) ───
function pattern(seed, base, draw) {
  const s = new Sprite(16, 16);
  for (let y = 0; y < 16; y++) for (let x = 0; x < 16; x++) s.set(x, y, gray(base));
  draw(s, rng(seed));
  return s;
}
const wrap = (v) => (v + 16) % 16;
const PATTERNS = [
  // floor_9 -> FLOOR_10: meadow grass (short blades)
  pattern(901, 128, (s, r) => {
    for (let i = 0; i < 22; i++) {
      const x = Math.floor(r() * 16);
      const y = Math.floor(r() * 16);
      s.set(x, y, gray(150));
      s.set(x, wrap(y + 1), gray(112));
    }
    for (let i = 0; i < 10; i++) s.set(Math.floor(r() * 16), Math.floor(r() * 16), gray(104));
  }),
  // floor_10 -> FLOOR_11: forest floor (moss and leaf litter)
  pattern(1001, 116, (s, r) => {
    for (let i = 0; i < 6; i++) {
      const cx = Math.floor(r() * 16);
      const cy = Math.floor(r() * 16);
      for (let k = 0; k < 7; k++)
        s.set(wrap(cx + Math.floor(r() * 4) - 1), wrap(cy + Math.floor(r() * 3) - 1), gray(100));
    }
    for (let i = 0; i < 14; i++) s.set(Math.floor(r() * 16), Math.floor(r() * 16), gray(136));
  }),
  // floor_11 -> FLOOR_12: dirt trail (pebbles, faint ruts)
  pattern(1101, 140, (s, r) => {
    for (let x = 0; x < 16; x++) if (r() < 0.5) s.set(x, 5, gray(128));
    for (let x = 0; x < 16; x++) if (r() < 0.5) s.set(x, 11, gray(128));
    for (let i = 0; i < 7; i++) {
      const x = Math.floor(r() * 16);
      const y = Math.floor(r() * 16);
      s.set(x, y, gray(168));
      s.set(x, wrap(y + 1), gray(112));
    }
  }),
  // floor_12 -> FLOOR_13: deck planks (four boards, staggered joints, nails)
  pattern(1201, 150, (s, r) => {
    for (let b = 0; b < 4; b++) {
      const y0 = b * 4;
      for (let x = 0; x < 16; x++) {
        s.set(x, y0 + 3, gray(96));
        if (r() < 0.3) s.set(x, y0 + 1, gray(138));
      }
      const joint = (b * 7 + 3) % 16;
      for (let y = y0; y < y0 + 3; y++) s.set(joint, y, gray(104));
      s.set(wrap(joint + 2), y0 + 1, gray(110));
    }
  }),
  // floor_13 -> FLOOR_14: still water (soft ripple dashes)
  pattern(1301, 120, (s, r) => {
    for (let i = 0; i < 6; i++) {
      const x = Math.floor(r() * 16);
      const y = Math.floor(r() * 16);
      for (let k = 0; k < 3; k++) s.set(wrap(x + k), y, gray(152));
      s.set(wrap(x + 1), wrap(y + 1), gray(108));
    }
  }),
];

// ── the north gateway: one grand arch, sliced into pillars and a walk-under crown ──
const SAND = {
  outline: [58, 42, 34],
  dark: [146, 120, 94],
  mid: [190, 166, 134],
  light: [222, 204, 172],
  hi: [242, 230, 204],
};
const GOLD = {
  outline: [90, 58, 12],
  dark: [170, 122, 30],
  mid: [222, 174, 56],
  light: [252, 222, 126],
};
const GATE_W = 128;
const GATE_H = 96;
/** The whole arch, drawn once (symmetric about its centre), lit from the upper left. */
function gatewayComposite() {
  const s = new Sprite(GATE_W, GATE_H);
  const cx = (GATE_W - 1) / 2;
  const springY = 60;
  const outerR = 37;
  const innerR = 27;
  const stone = (x, shade) => shade ?? (x < cx ? SAND.light : SAND.mid);
  // spandrels + archivolt between the pillars (above the springing line)
  for (let y = 18; y <= springY; y++)
    for (let x = 26; x <= GATE_W - 27; x++) {
      const d = Math.hypot(x - cx, y - springY);
      if (d < innerR) continue; // the opening
      if (d <= outerR) {
        // voussoirs: alternate shades by angular block, a gold trim on the intrados
        const ang = Math.atan2(springY - y, x - cx);
        const block = Math.floor((ang / Math.PI) * 11);
        let c = block % 2 === 0 ? SAND.light : SAND.mid;
        if (d < innerR + 1.6) c = GOLD.mid;
        else if (d > outerR - 1.2) c = SAND.dark;
        else if (Math.abs(ang - Math.PI / 2) < 0.16) c = d < innerR + 5 ? GOLD.light : GOLD.mid; // keystone
        s.set(x, y, c);
      } else {
        // spandrel relief: a sunk panel with a gold rosette either side
        let c = SAND.mid;
        if ((x + y) % 7 === 0) c = SAND.light;
        const rx = x < cx ? cx - 30 : cx + 30;
        if (Math.hypot(x - rx, y - 27) < 3)
          c = Math.hypot(x - rx, y - 27) < 1.5 ? GOLD.light : GOLD.mid;
        s.set(x, y, c);
      }
    }
  // entablature with gold trim
  for (let y = 7; y <= 17; y++)
    for (let x = 0; x < GATE_W; x++) {
      let c = stone(x);
      if (y === 7 || y === 8) c = SAND.hi;
      if (y === 12) c = GOLD.mid;
      if (y === 16 || y === 17) c = SAND.dark;
      if (y >= 9 && y <= 11 && x % 6 === 0) c = SAND.dark; // dentils
      s.set(x, y, c);
    }
  // pediment with a gold sunburst
  for (let y = 0; y <= 6; y++) {
    const half = 20 + (y / 6) * 30;
    for (let x = Math.ceil(cx - half); x <= Math.floor(cx + half); x++) {
      const d = Math.hypot(x - cx, y - 7);
      let c = x < cx ? SAND.light : SAND.mid;
      if (d < 6) c = d < 3 ? GOLD.light : GOLD.mid;
      else if (d < 7 && (x + y) % 2 === 0) c = GOLD.dark;
      s.set(x, y, c);
    }
  }
  // the pillars: plinth, fluted shaft, gold-banded capital, a warm sconce
  for (const px of [4, GATE_W - 28]) {
    for (let y = 18; y <= 23; y++)
      for (let x = px - 1; x <= px + 24; x++)
        s.set(
          x,
          y,
          y === 20 || y === 21
            ? GOLD.mid
            : stone(x - px + cx - 12, x - px < 12 ? SAND.hi : SAND.light),
        );
    for (let y = 24; y <= 85; y++)
      for (let x = px + 2; x <= px + 21; x++) {
        const rel = x - px - 2;
        let c = rel < 7 ? SAND.light : rel > 14 ? SAND.dark : SAND.mid;
        if (rel % 4 === 3) c = SAND.dark; // fluting
        if (rel === 0) c = SAND.hi;
        s.set(x, y, c);
      }
    for (let y = 86; y < GATE_H; y++)
      for (let x = px - 2; x <= px + 25; x++)
        s.set(x, y, y === 86 ? GOLD.mid : y > 92 ? SAND.dark : SAND.mid);
    // sconce
    const sx = px + 9;
    for (let y = 44; y <= 54; y++)
      for (let x = sx; x <= sx + 5; x++)
        s.set(
          x,
          y,
          y === 44 || y === 54
            ? GOLD.dark
            : x === sx || x === sx + 5
              ? GOLD.mid
              : y < 48
                ? LAMP.glow
                : LAMP.glass,
        );
  }
  // a lantern hanging from the keystone
  for (let y = 34; y <= 40; y++) s.set(Math.floor(cx), y, GOLD.dark);
  for (let y = 41; y <= 49; y++)
    for (let x = Math.floor(cx) - 3; x <= Math.floor(cx) + 4; x++)
      s.set(
        x,
        y,
        y === 41 || y === 49
          ? GOLD.dark
          : x === Math.floor(cx) - 3 || x === Math.floor(cx) + 4
            ? GOLD.mid
            : y < 45
              ? LAMP.glow
              : LAMP.glass,
      );
  s.outline(SAND.outline);
  return s;
}
function slice(src, x0, y0, w, h) {
  const out = new Sprite(w, h);
  for (let y = 0; y < h; y++) for (let x = 0; x < w; x++) out.set(x, y, src.get(x0 + x, y0 + y));
  return out;
}
const GATE = gatewayComposite();
for (let y = 64; y < GATE_H; y++)
  for (let x = 32; x < 96; x++)
    if (GATE.get(x, y))
      throw new Error('gateway: the walk-under opening below the crown must be clear');

function brazier() {
  const s = new Sprite(16, 32);
  for (let y = 18; y < 31; y++)
    for (let x = 5; x <= 10; x++) s.set(x, y, x < 7 ? SAND.light : x > 8 ? SAND.dark : SAND.mid);
  for (let x = 3; x <= 12; x++) (s.set(x, 30, SAND.dark), s.set(x, 31, SAND.dark));
  for (let y = 13; y <= 17; y++)
    for (let x = 2 + (y - 13) / 2; x <= 13 - (y - 13) / 2; x++)
      s.set(Math.round(x), y, y === 13 ? GOLD.light : GOLD.mid);
  const flame = [
    [7, 3],
    [8, 2],
    [6, 5],
    [7, 5],
    [8, 5],
    [9, 4],
    [5, 8],
    [6, 7],
    [7, 7],
    [8, 7],
    [9, 7],
    [10, 8],
  ];
  for (let y = 6; y <= 12; y++)
    for (let x = 4; x <= 11; x++) s.set(x, y, y > 9 ? [236, 120, 40] : [250, 176, 64]);
  for (const [x, y] of flame) s.set(x, y, LAMP.glow);
  s.outline(GOLD.outline);
  return s;
}
function planter() {
  const s = broadleaf(32, 48, 61, OAK, BARK, clumpsFor(32, 58, rng(62), 5, 0.24));
  for (let y = 30; y < 48; y++)
    for (let x = 4; x < 28; x++) {
      const t = (y - 30) / 17;
      const half = 12 - t * 4;
      if (Math.abs(x - 15.5) > half) continue;
      let c = x < 12 ? SAND.light : x > 20 ? SAND.dark : SAND.mid;
      if (y === 30 || y === 31) c = GOLD.mid;
      if (y === 40) c = GOLD.dark;
      s.set(x, y, c);
    }
  s.outline(SAND.outline);
  return s;
}

// ── catalogue of generated items ────────────────────────────────────────────
const r0 = rng(7);
const ITEMS = [
  // name, label, sprite, footprintW, footprintH, backgroundTiles
  ['FOREST_PINE_TALL', 'Tall Pine', pine(32, 64, 11, 6), 2, 4, 3],
  ['FOREST_PINE', 'Pine', pine(32, 48, 12, 5), 2, 3, 2],
  ['FOREST_PINE_SMALL', 'Young Pine', pine(16, 32, 13, 4), 1, 2, 1],
  ['FOREST_OAK', 'Oak', broadleaf(48, 48, 21, OAK, BARK, clumpsFor(48, 48, r0, 6, 0.27)), 3, 3, 2],
  [
    'FOREST_OAK_SMALL',
    'Small Oak',
    broadleaf(32, 32, 22, OAK, BARK, clumpsFor(32, 32, r0, 5, 0.25)),
    2,
    2,
    1,
  ],
  [
    'FOREST_BIRCH',
    'Birch',
    broadleaf(32, 48, 31, BIRCH, BIRCH_BARK, clumpsFor(32, 50, r0, 5, 0.24)),
    2,
    3,
    2,
  ],
  ['FOREST_BUSH', 'Bush', bush(41), 1, 1, 0],
  ['FOREST_FERN', 'Fern', fern(42), 1, 1, 1],
  ['FOREST_FLOWERS', 'Wildflowers', flowers(43), 1, 1, 1],
  ['FOREST_ROCK', 'Rock', rock(44, 16, 16), 1, 1, 0],
  ['FOREST_BOULDER', 'Boulder', rock(45, 32, 32), 2, 2, 1],
  ['FOREST_STUMP', 'Stump', stump(), 1, 1, 0],
  ['FOREST_LOG', 'Fallen Log', log(), 2, 1, 0],
  ['DECK_RAILING', 'Deck Railing', railing(), 1, 1, 0],
  ['DECK_RAILING_SIDE', 'Deck Railing (Side)', railingSide(), 1, 1, 0],
  ['DECK_TELESCOPE', 'Telescope', telescope(), 1, 2, 1],
  ['DECK_LANTERN', 'Lantern', lantern(), 1, 2, 1],
  ['DECK_BENCH', 'Lookout Bench', deckBench(), 2, 1, 0],
  ['DECK_SIGNPOST', 'Trail Sign', signpost(), 1, 2, 1],
  // The gateway: pillars block only their base row; the crown is walk-under in EVERY row
  // (backgroundTiles = its full height), so the opening below it is never invisibly blocked.
  ['GATEWAY_PILLAR_L', 'Gateway Pillar (West)', slice(GATE, 0, 0, 32, GATE_H), 2, 6, 5],
  ['GATEWAY_CROWN', 'Gateway Crown', slice(GATE, 32, 0, 64, 64), 4, 4, 4],
  ['GATEWAY_PILLAR_R', 'Gateway Pillar (East)', slice(GATE, 96, 0, 32, GATE_H), 2, 6, 5],
  ['GATEWAY_BRAZIER', 'Gateway Brazier', brazier(), 1, 2, 1],
  ['GATEWAY_PLANTER', 'Gateway Planter', planter(), 2, 3, 2],
];

// ── write or check ──────────────────────────────────────────────────────────
const outputs = new Map(); // abs path -> Buffer
PATTERNS.forEach((p, i) =>
  outputs.set(path.join(ASSETS, 'floors', `floor_${9 + i}.png`), p.toPng()),
);
for (const [id, name, sprite, fw, fh, bg] of ITEMS) {
  if (sprite.w !== fw * 16 || sprite.h !== fh * 16)
    throw new Error(`${id}: sprite/footprint mismatch`);
  const manifest = {
    id,
    name,
    category: 'decor',
    type: 'asset',
    canPlaceOnWalls: false,
    canPlaceOnSurfaces: false,
    backgroundTiles: bg,
    width: sprite.w,
    height: sprite.h,
    footprintW: fw,
    footprintH: fh,
  };
  const dir = path.join(ASSETS, 'furniture', id);
  outputs.set(
    path.join(dir, 'manifest.json'),
    Buffer.from(JSON.stringify(manifest, null, 2) + '\n'),
  );
  outputs.set(path.join(dir, `${id}.png`), sprite.toPng());
}

let mismatches = 0;
for (const [file, bytes] of outputs) {
  if (CHECK) {
    const same = fs.existsSync(file) && Buffer.compare(fs.readFileSync(file), bytes) === 0;
    if (!same) {
      mismatches++;
      console.error(`MISMATCH ${path.relative(ROOT, file)}`);
    }
  } else {
    fs.mkdirSync(path.dirname(file), { recursive: true });
    fs.writeFileSync(file, bytes);
  }
}
if (CHECK && mismatches > 0) process.exit(1);
console.log(
  `${CHECK ? 'checked' : 'wrote'} ${outputs.size} files (${PATTERNS.length} floor patterns, ${ITEMS.length} items)`,
);
