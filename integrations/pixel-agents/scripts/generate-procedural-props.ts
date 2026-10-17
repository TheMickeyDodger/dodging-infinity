/**
 * PROCEDURAL GOTHIC PEWS (M3 S2, engineering-made art; no generated source art).
 *
 *   npx tsx scripts/generate-cathedral-pews.ts
 *
 * Three 16 x 32 pieces that join into one continuous pew of any length: a carved left bench-end, a
 * seamlessly tileable middle (backrest panels, seat plank, apron), and a carved right bench-end. Dark
 * timber, upper-left light, a subtle dark outline, matching the r4 art's conventions. Seen from the
 * south (the backs of pews facing the altar). Category 'decor', so a pew is never a seat. Footprint
 * 1 x 2: the backrest rises into a walk-behind top row; the bottom row blocks.
 * Outputs: webview-ui/public/assets/furniture/CATHEDRAL_PEW_{L,M,R}/ and SUBWAY_{RAIL,BALLAST}/ (see below).
 */
import fs from 'node:fs';
import path from 'node:path';

import { PNG } from 'pngjs';

const ROOT = path.resolve(__dirname, '..');
const FURN = path.join(ROOT, 'webview-ui/public/assets/furniture');
type RGB = [number, number, number];
const OUTLINE: RGB = [36, 22, 14];
const DARK: RGB = [62, 38, 24];
const BASE: RGB = [84, 53, 33];
const MID: RGB = [104, 67, 41];
const LIGHT: RGB = [128, 84, 52];
const HIGH: RGB = [150, 102, 64];

function piece(kind: 'L' | 'M' | 'R'): PNG {
  const png = new PNG({ width: 16, height: 32 });
  png.data.fill(0);
  const put = (x: number, y: number, c: RGB) => {
    if (x < 0 || y < 0 || x > 15 || y > 31) return;
    const i = (y * 16 + x) * 4;
    png.data[i] = c[0];
    png.data[i + 1] = c[1];
    png.data[i + 2] = c[2];
    png.data[i + 3] = 255;
  };
  for (let x = 0; x < 16; x++) {
    // backrest: top rail (outline, highlight), panels with grooves every 8 px, shadow under the rail
    put(x, 11, OUTLINE);
    put(x, 12, HIGH);
    put(x, 13, LIGHT);
    for (let y = 14; y <= 20; y++) put(x, y, x % 8 === 3 ? DARK : y === 14 ? BASE : MID);
    // carved pointed-arch panel detail inside each 8 px panel
    const px = x % 8;
    if (px >= 5 && px <= 6) for (let y = 16; y <= 19; y++) put(x, y, BASE);
    if (px === 5 || px === 6) put(x, 15, DARK);
    // seat plank, apron, shadowed underside
    put(x, 21, OUTLINE);
    put(x, 22, HIGH);
    put(x, 23, LIGHT);
    for (let y = 24; y <= 27; y++) put(x, y, y === 24 ? BASE : DARK);
    put(x, 28, OUTLINE);
  }
  // legs every 8 px (the shadowed floor shows between them)
  for (const lx of [1, 9]) for (let y = 29; y <= 31; y++) put(lx, y, OUTLINE), put(lx + 1, y, DARK);
  // carved bench-ends with a pointed finial
  const end = (x0: number, dir: 1 | -1) => {
    for (let y = 9; y <= 31; y++)
      for (let k = 0; k < 3; k++) {
        const x = x0 + dir * k;
        const edge = k === 0 || y === 9 || y === 31;
        put(x, y, edge ? OUTLINE : k === 1 ? (dir === 1 ? LIGHT : BASE) : MID);
      }
    put(x0 + dir, 8, OUTLINE);
    put(x0 + dir, 7, LIGHT);
    for (let y = 13; y <= 18; y++) put(x0 + dir, y, y === 13 ? HIGH : DARK); // carved slot
  };
  if (kind === 'L') end(0, 1);
  if (kind === 'R') end(15, -1);
  return png;
}

for (const [kind, name] of [['L', 'Pew (left end)'], ['M', 'Pew (middle)'], ['R', 'Pew (right end)']] as const) {
  const id = `CATHEDRAL_PEW_${kind}`;
  const dir = path.join(FURN, id);
  fs.mkdirSync(dir, { recursive: true });
  fs.writeFileSync(path.join(dir, `${id}.png`), PNG.sync.write(piece(kind)));
  const manifest = { id, name, category: 'decor', type: 'asset', canPlaceOnWalls: false, canPlaceOnSurfaces: false, backgroundTiles: 1, width: 16, height: 32, footprintW: 1, footprintH: 2 };
  fs.writeFileSync(path.join(dir, 'manifest.json'), `${JSON.stringify(manifest, null, 2)}\n`);
}
console.log('CATHEDRAL_PEW_L/M/R written');

/**
 * SUBWAY TRACK TILES (procedural, engineering-made): SUBWAY_RAIL (sleepers and two steel rails on
 * ballast, running east-west) and SUBWAY_BALLAST (track bed without rails). Both 1 x 1 and blocking,
 * so pedestrians cross only at the boarded level crossings the world build leaves open.
 */
function trackTile(rails: boolean): PNG {
  const png = new PNG({ width: 16, height: 16 });
  const put = (x: number, y: number, c: RGB) => {
    const i = (y * 16 + x) * 4;
    png.data[i] = c[0];
    png.data[i + 1] = c[1];
    png.data[i + 2] = c[2];
    png.data[i + 3] = 255;
  };
  for (let y = 0; y < 16; y++)
    for (let x = 0; x < 16; x++) {
      const n = (x * 7 + y * 13 + ((x * y) % 5)) % 9;
      put(x, y, n < 3 ? [104, 98, 90] : n < 6 ? [122, 116, 106] : [138, 131, 120]); // ballast
    }
  if (rails) {
    for (const sx of [1, 5, 9, 13])
      for (let y = 2; y <= 13; y++) {
        put(sx, y, y === 2 || y === 13 ? OUTLINE : DARK);
        put(sx + 1, y, y === 2 || y === 13 ? OUTLINE : BASE);
      }
    for (const ry of [4, 10])
      for (let x = 0; x < 16; x++) {
        put(x, ry - 1, [70, 74, 80]);
        put(x, ry, [196, 200, 206]);
        put(x, ry + 1, [120, 124, 132]);
      }
  }
  return png;
}
for (const [id, name, rails] of [['SUBWAY_RAIL', 'Subway track (rails)', true], ['SUBWAY_BALLAST', 'Subway track bed', false]] as const) {
  const dir = path.join(FURN, id);
  fs.mkdirSync(dir, { recursive: true });
  fs.writeFileSync(path.join(dir, `${id}.png`), PNG.sync.write(trackTile(rails)));
  const manifest = { id, name, category: 'decor', type: 'asset', canPlaceOnWalls: false, canPlaceOnSurfaces: false, backgroundTiles: 0, width: 16, height: 16, footprintW: 1, footprintH: 1 };
  fs.writeFileSync(path.join(dir, 'manifest.json'), `${JSON.stringify(manifest, null, 2)}\n`);
}
console.log('SUBWAY_RAIL/BALLAST written');
