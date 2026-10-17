#!/usr/bin/env node
/**
 * Generates the METROPOLIS world's ORIGINAL floor patterns, drawn here in code from fixed seeds
 * (no external image is read; MIT, part of this project). Grayscale 16 x 16, tiling seamlessly,
 * colourised per tile by the layout's tileColors exactly like every other floor.
 *
 *   node scripts/generate-metropolis-floors.mjs            # (re)write in place
 *   node scripts/generate-metropolis-floors.mjs --check    # verify the committed bytes match
 *
 * APPEND-ONLY: writes webview-ui/public/assets/floors/floor_14.png .. floor_27.png = tile values
 * FLOOR_15 .. FLOOR_28. floor_0 .. floor_13 (FLOOR_1 .. FLOOR_14 — the palace's and the forest's)
 * are never touched. Which of these are WATER (never walkable) is decided in the webview's
 * constants (UNWALKABLE_TILE_TYPES), not here.
 */
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

import { PNG } from 'pngjs';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const FLOORS = path.join(ROOT, 'webview-ui/public/assets/floors');
const CHECK = process.argv.includes('--check');
const FIRST_FILE_INDEX = 14;

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
const wrap = (v) => (v + 16) % 16;

function pattern(seed, base, draw) {
  const px = new Array(256).fill(base);
  const set = (x, y, v) => {
    px[wrap(y) * 16 + wrap(x)] = v;
  };
  draw(set, rng(seed), px);
  const png = new PNG({ width: 16, height: 16 });
  for (let i = 0; i < 256; i++) {
    png.data[i * 4] = png.data[i * 4 + 1] = png.data[i * 4 + 2] = px[i];
    png.data[i * 4 + 3] = 255;
  }
  return PNG.sync.write(png, { colorType: 6 });
}

const speckle = (set, r, n, v) => {
  for (let i = 0; i < n; i++) set(Math.floor(r() * 16), Math.floor(r() * 16), v);
};

/** [tile name, bytes] in tile-value order: FLOOR_15 first. */
const PATTERNS = [
  // FLOOR_15: asphalt
  ['asphalt', pattern(1501, 96, (set, r) => (speckle(set, r, 26, 108), speckle(set, r, 18, 84)))],
  // FLOOR_16: asphalt, dashed centre line running east-west
  [
    'road dash (east-west)',
    pattern(1601, 96, (set, r) => {
      speckle(set, r, 22, 108);
      speckle(set, r, 14, 84);
      for (let x = 2; x < 10; x++) {
        set(x, 7, 210);
        set(x, 8, 196);
      }
    }),
  ],
  // FLOOR_17: asphalt, dashed centre line running north-south
  [
    'road dash (north-south)',
    pattern(1701, 96, (set, r) => {
      speckle(set, r, 22, 108);
      speckle(set, r, 14, 84);
      for (let y = 2; y < 10; y++) {
        set(7, y, 210);
        set(8, y, 196);
      }
    }),
  ],
  // FLOOR_18: pavement slabs (2 x 2 slabs per tile, offset joints)
  [
    'pavement slabs',
    pattern(1801, 168, (set, r) => {
      for (let i = 0; i < 16; i++) {
        set(i, 0, 132);
        set(i, 8, 132);
        set(0, i < 8 ? i : 99, 132);
        set(8, i >= 8 ? i : 99, 132);
      }
      for (let y = 1; y < 8; y++) set(0, y, 132);
      for (let y = 9; y < 16; y++) set(8, y, 132);
      speckle(set, r, 10, 180);
    }),
  ],
  // FLOOR_19: cobblestones (plaza)
  [
    'cobblestones',
    pattern(1901, 150, (set) => {
      const stones = [
        [0, 0, 5, 4],
        [5, 0, 6, 4],
        [11, 0, 5, 4],
        [0, 4, 3, 4],
        [3, 4, 6, 4],
        [9, 4, 7, 4],
        [0, 8, 6, 4],
        [6, 8, 5, 4],
        [11, 8, 5, 4],
        [0, 12, 4, 4],
        [4, 12, 6, 4],
        [10, 12, 6, 4],
      ];
      for (const [x0, y0, w, h] of stones) {
        for (let x = x0; x < x0 + w; x++) set(x, y0 + h - 1, 104);
        for (let y = y0; y < y0 + h; y++) set(x0 + w - 1, y, 104);
        set(x0 + 1, y0 + 1, 178);
      }
    }),
  ],
  // FLOOR_20: sand (stipple and ripples)
  [
    'sand',
    pattern(2001, 176, (set, r) => {
      speckle(set, r, 30, 194);
      speckle(set, r, 18, 156);
      for (let x = 0; x < 16; x++) if (r() < 0.4) set(x, 11, 162);
    }),
  ],
  // FLOOR_21: ploughed soil (furrows east-west)
  [
    'ploughed soil',
    pattern(2101, 120, (set, r) => {
      for (let x = 0; x < 16; x++) {
        set(x, 3, 88);
        set(x, 4, 150);
        set(x, 11, 88);
        set(x, 12, 150);
      }
      speckle(set, r, 10, 104);
    }),
  ],
  // FLOOR_22: deep water (sea; long swell crests)
  [
    'deep water',
    pattern(2201, 92, (set, r) => {
      for (let i = 0; i < 5; i++) {
        const x = Math.floor(r() * 16);
        const y = Math.floor(r() * 16);
        for (let k = 0; k < 4; k++) set(x + k, y, 132);
        set(x + 1, y + 1, 76);
      }
    }),
  ],
  // FLOOR_23: river / lake water (short ripples, lighter)
  [
    'river water',
    pattern(2301, 116, (set, r) => {
      for (let i = 0; i < 7; i++) {
        const x = Math.floor(r() * 16);
        const y = Math.floor(r() * 16);
        set(x, y, 158);
        set(x + 1, y, 150);
        set(x + 1, y + 1, 100);
      }
    }),
  ],
  // FLOOR_24: boardwalk planks running north-south (bridges, piers)
  [
    'boardwalk (north-south)',
    pattern(2401, 150, (set, r) => {
      for (let b = 0; b < 4; b++) {
        const x0 = b * 4;
        for (let y = 0; y < 16; y++) {
          set(x0 + 3, y, 96);
          if (r() < 0.3) set(x0 + 1, y, 138);
        }
        const joint = (b * 7 + 3) % 16;
        for (let x = x0; x < x0 + 3; x++) set(x, joint, 104);
      }
    }),
  ],
  // FLOOR_25: large checker tiles (halls, galleries)
  [
    'checker tiles',
    pattern(2501, 0, (set) => {
      for (let y = 0; y < 16; y++)
        for (let x = 0; x < 16; x++) set(x, y, (x < 8) !== (y < 8) ? 116 : 172);
      for (let i = 0; i < 16; i++) {
        set(i, 0, 140);
        set(0, i, 140);
      }
    }),
  ],
  // FLOOR_26: herringbone parquet (offices)
  [
    'parquet',
    pattern(2601, 150, (set) => {
      for (let y = 0; y < 16; y++)
        for (let x = 0; x < 16; x++) {
          const d = (x + y) % 8;
          const block = Math.floor(x / 4) % 2;
          if (d === 0 && block) set(x, y, 104);
          if ((x - y + 16) % 8 === 0 && !block) set(x, y, 104);
        }
      for (let i = 0; i < 16; i += 4) for (let y = 0; y < 16; y++) set(i, y, 124);
    }),
  ],
  // FLOOR_27: zebra crossing (stripes running north-south, cross east-west)
  [
    'zebra crossing',
    pattern(2701, 96, (set, r) => {
      speckle(set, r, 12, 108);
      for (let x = 0; x < 16; x++) {
        if (x % 4 < 2) for (let y = 1; y < 15; y++) set(x, y, 214);
      }
    }),
  ],
  // FLOOR_28: crop field rows (north-south green rows; colourise green/gold)
  [
    'crop rows',
    pattern(2801, 108, (set, r) => {
      for (let y = 0; y < 16; y++)
        for (const x of [2, 3, 6, 7, 10, 11, 14, 15]) set(x, y, r() < 0.8 ? 168 : 140);
    }),
  ],
];

let mismatches = 0;
PATTERNS.forEach(([name, bytes], i) => {
  const file = path.join(FLOORS, `floor_${FIRST_FILE_INDEX + i}.png`);
  if (CHECK) {
    const same = fs.existsSync(file) && Buffer.compare(fs.readFileSync(file), bytes) === 0;
    if (!same) {
      console.error(`MISMATCH ${path.relative(ROOT, file)} (${name})`);
      mismatches++;
    }
  } else fs.writeFileSync(file, bytes);
});
if (CHECK) {
  console.log(mismatches ? `${mismatches} mismatch(es)` : `${PATTERNS.length} floor patterns match`);
  process.exit(mismatches ? 1 : 0);
}
console.log(
  `wrote floor_${FIRST_FILE_INDEX}..floor_${FIRST_FILE_INDEX + PATTERNS.length - 1}.png (FLOOR_15..FLOOR_${14 + PATTERNS.length})`,
);
