/**
 * Prepare the delivered living-world source art for runtime (M3 S6). READ-ONLY on the sources.
 *
 *   npx tsx scripts/prepare-living-world-art.ts <absolute source dir> [--check]
 *
 * For each delivered sheet (hash-verified; a mismatch refuses):
 *  - alpha census: alpha==0 pixels carrying hidden RGB, alpha==255 count, the alpha 1..2 fringe;
 *  - RGB is ZEROED wherever alpha==0 (in memory) so no matte can resurface;
 *  - segmentation by ALPHA PROJECTION at a measured cutoff (alpha >= ALPHA_CUTOFF), never a fixed
 *    stride and never bbox on alpha > 0 (the fringe would inflate every box) — before/after boxes
 *    are recorded;
 *  - character sheets: facing rows are ADJUDICATED per sheet on a named semantic cue (face/nose,
 *    cap brim, held prop) from enlarged source crops — recorded with who adjudicated and the cue —
 *    never assumed from the contract and never decided by a silhouette-offset vote. Programmatic
 *    signals (skin side, silhouette extension, head offset) are recorded beside it as corroboration
 *    only; disagreement is reported, not voted away. Mirror IoU is reported as SIMILARITY only;
 *    frames are downsampled (block mode, one scale per sheet) and anchored bottom-centre;
 *  - retail sheet: four motifs by connected components, one shared scale, written as furniture.
 * Outputs: webview-ui/public/assets/ambient/<id>/ (characters), webview-ui/public/assets/furniture/
 * BIGBOX_* (retail), and packaging/di/LIVING-WORLD-ART-QA.json. Deterministic: no clock, no random.
 */
import crypto from 'node:crypto';
import fs from 'node:fs';
import path from 'node:path';

import { PNG } from 'pngjs';

const ROOT = path.resolve(__dirname, '..');
const ALPHA_CUTOFF = 16;
const CHAR_TARGET_HEIGHT = 27; // game pixels for the tallest motif of a character sheet
const CHAR_CANVAS_H = 32;
const MIN_GAP = 6; // source pixels of empty projection that separate two motifs
const RETAIL_STORE_W = 160; // 10 tiles

interface Source {
  id: string;
  file: string;
  sha256: string;
  kind: 'full' | 'min' | 'retail' | 'vehicles' | 'cathedral' | 'exhibits' | 'subway';
  runtimeId?: string;
  label?: string;
}

/**
 * FACING ADJUDICATION — the basis of every row mapping. Source band order top to bottom; the
 * contract REQUESTED down, left, right, up, which is a request, not a fact. Adjudicated by executor1
 * by visual inspection of enlarged source crops (bands 2 and 3 side by side; bands 1 and 4 for
 * front/back), 2026-10-16, after lead1 found the earlier silhouette-vote mapping of S04 inverted.
 */
const ADJUDICATED: Record<string, { order: [string, string, string, string]; cue: string }> = {
  S03: { order: ['DOWN', 'LEFT', 'RIGHT', 'UP'], cue: 'face and nose point left in band 2, right in band 3; band 1 shows the face, band 4 the back of the cap' },
  S04: { order: ['DOWN', 'LEFT', 'RIGHT', 'UP'], cue: 'cap brim and face point left in band 2 (ponytail trails right), right in band 3 (ponytail trails left) — matches lead1 visual adjudication' },
  S05: { order: ['DOWN', 'LEFT', 'RIGHT', 'UP'], cue: 'face and helmet brim point left in band 2 with the air tank on the trailing right; mirrored in band 3' },
  S06: { order: ['DOWN', 'LEFT', 'RIGHT', 'UP'], cue: 'nose and face point left in band 2 with the hair bun trailing right; reversed in band 3' },
  S07: { order: ['DOWN', 'LEFT', 'RIGHT', 'UP'], cue: 'cap brim and face point left in band 2; right in band 3' },
  S16: { order: ['DOWN', 'LEFT', 'RIGHT', 'UP'], cue: 'eye and cheek blush on the left with the bolt tail trailing right in band 2; reversed in band 3; band 1 shows the face, band 4 the back' },
  S19: { order: ['DOWN', 'LEFT', 'RIGHT', 'UP'], cue: 'face points left in band 2 with the headband tails trailing right; reversed in band 3' },
  S23: { order: ['DOWN', 'LEFT', 'RIGHT', 'UP'], cue: 'face and gaze point left in band 2; right in band 3' },
  S27: { order: ['DOWN', 'LEFT', 'RIGHT', 'UP'], cue: 'nose and face point left in band 2; right in band 3' },
  S31: { order: ['DOWN', 'RIGHT', 'LEFT', 'UP'], cue: 'snout points right and the carrot is held in front on the right in band 2; reversed in band 3 — DIFFERS from the contract; agrees with the Operator report. The skin-side signal is unreliable here (the head band is mostly ears)' },
};

const SOURCES: Source[] = [
  { id: 'S02', file: 'lw-S02-retail.png', sha256: 'e222566e8f2fa0b6c1e65cb7fbb9e93ea1ad8d5b16db83301aa79b506ffdd07e', kind: 'retail' },
  { id: 'S03', file: 'lw-S03-bellhop.png', sha256: '23db60c823eb3872dda1dab9bd574f57d8047c275dac9d8578f562edb4824b8f', kind: 'full', runtimeId: 'bellhop', label: 'Bellhop' },
  { id: 'S04', file: 'lw-S04-police.png', sha256: 'e84206795b00d954a550601125a658522ccc83a5540ce83190c29bd84f9ca60d', kind: 'full', runtimeId: 'police', label: 'Police officer' },
  { id: 'S05', file: 'lw-S05-firefighter.png', sha256: '3820c2a11156efd55ab7033728cc6f572862d5a90531cea1fabeff5409bf0b33', kind: 'full', runtimeId: 'firefighter', label: 'Firefighter' },
  { id: 'S06', file: 'lw-S06-store-clerk.png', sha256: '6a0948d62290e357b52c133ca3c41111d710724d1de2aba8f90797ebc6159e9a', kind: 'full', runtimeId: 'store-clerk', label: 'Store clerk' },
  { id: 'S07', file: 'lw-S07-barista.png', sha256: '80bea6e2622ff2b4d92748b0ba8a4248a142f3515c2175306fdd2353767a054b', kind: 'full', runtimeId: 'barista', label: 'Barista' },
  { id: 'S16', file: 'lw-S16-pocket-creature.png', sha256: 'ece8acd8f1abe3bdaba0ba49b33afc6d92b0760670b163a8073d6c703d41946e', kind: 'min', runtimeId: 'egg-pocket-creature', label: 'Pocket-creature homage' },
  { id: 'S19', file: 'lw-S19-shinobi.png', sha256: '2f22ceb4f50db2e561c1f7061feb229266aac9ac60929973ab47a98eaff92539', kind: 'min', runtimeId: 'egg-shinobi', label: 'Shinobi homage' },
  { id: 'S23', file: 'lw-S23-martial-artist.png', sha256: '35986084ced5d0b08680f7458dfb04de975a638ff05145b28316b296f5383919', kind: 'min', runtimeId: 'egg-martial-artist', label: 'Martial-artist homage' },
  { id: 'S27', file: 'lw-S27-cul-de-sac-kid.png', sha256: '467c2f2ea00b194e9ee44227a7aea6699a1905c740d3e8b7437e5a4292ada12f', kind: 'min', runtimeId: 'egg-cul-de-sac-kid', label: 'Cul-de-sac-kid homage' },
  { id: 'T01', file: 'lw-T01-emergency-vehicles.png', sha256: 'f51abbd6cbdca2beaaa71638d94cbb426c403d71c3380e8a57176000cba83c84', kind: 'vehicles' },
  { id: 'T02', file: 'lw-T02-city-cars.png', sha256: '79c028bbf8831f1df217a710506b3b05361ab8f2b7b73bbec976a97b2df7b40d', kind: 'vehicles' },
  { id: 'G01', file: 'lw-G01-cathedral.png', sha256: '5001efa44fd6cca0d38fe3d1f604aacc129da4d16604e9c7c1432df2a0f1d646', kind: 'cathedral' },
  { id: 'SUB', file: 'living-world-subway.png', sha256: 'f4d894278e8a2a1aff8f485f475236ff526bd8cd3bb8be4482a719b3b4d5fd2f', kind: 'subway' },
  { id: 'M01', file: 'lw-M01-museum-exhibits.png', sha256: '1ac7e501ee28aa759cc7ee806875569d21eb00c6a8e44e16f5e53edb1a21974b', kind: 'exhibits' },
  { id: 'S31', file: 'lw-S31-trickster-hare.png', sha256: '1fd5387dd0f04579b87b53fe2db766b12a6064ce945782f13e5b9c959a1906d0', kind: 'min', runtimeId: 'egg-trickster-hare', label: 'Trickster-hare homage' },
];

interface Img {
  w: number;
  h: number;
  data: Buffer; // RGBA
}
interface Box {
  x0: number;
  y0: number;
  x1: number; // exclusive
  y1: number; // exclusive
}

const sha = (b: Buffer) => crypto.createHash('sha256').update(b).digest('hex');
const opaque = (img: Img, x: number, y: number, cutoff = ALPHA_CUTOFF) =>
  img.data[(y * img.w + x) * 4 + 3] >= cutoff;

function census(img: Img) {
  let a0 = 0;
  let a0Hidden = 0;
  let a255 = 0;
  let fringe = 0;
  let maxA = 0;
  for (let i = 0; i < img.w * img.h; i++) {
    const a = img.data[i * 4 + 3];
    if (a === 0) {
      a0++;
      if (img.data[i * 4] || img.data[i * 4 + 1] || img.data[i * 4 + 2]) a0Hidden++;
    }
    if (a === 255) a255++;
    if (a === 1 || a === 2) fringe++;
    if (a > maxA) maxA = a;
  }
  return { pixels: img.w * img.h, alpha0: a0, alpha0WithHiddenRgb: a0Hidden, alpha255: a255, alpha1to2Fringe: fringe, maxAlpha: maxA };
}

function zeroHiddenRgb(img: Img): number {
  let n = 0;
  for (let i = 0; i < img.w * img.h; i++) {
    if (img.data[i * 4 + 3] === 0 && (img.data[i * 4] || img.data[i * 4 + 1] || img.data[i * 4 + 2])) {
      img.data[i * 4] = img.data[i * 4 + 1] = img.data[i * 4 + 2] = 0;
      n++;
    }
  }
  return n;
}

/** Runs of indices whose projection count > 0, separated by >= MIN_GAP empty indices. */
function bands(counts: number[], minMass: number): Array<[number, number]> {
  const out: Array<[number, number]> = [];
  let start = -1;
  let gap = 0;
  for (let i = 0; i <= counts.length; i++) {
    const on = i < counts.length && counts[i] > 0;
    if (on) {
      if (start < 0) start = i;
      gap = 0;
    } else if (start >= 0) {
      gap++;
      if (gap >= MIN_GAP || i === counts.length) {
        out.push([start, i - gap + 1]);
        start = -1;
        gap = 0;
      }
    }
  }
  // drop specks: bands whose mass is tiny
  return out.filter(([a, b]) => counts.slice(a, b).reduce((s, v) => s + v, 0) >= minMass);
}

function boxOf(img: Img, box: Box, cutoff: number): Box | null {
  let x0 = Infinity;
  let y0 = Infinity;
  let x1 = -1;
  let y1 = -1;
  for (let y = box.y0; y < box.y1; y++)
    for (let x = box.x0; x < box.x1; x++)
      if (img.data[(y * img.w + x) * 4 + 3] >= cutoff) {
        if (x < x0) x0 = x;
        if (y < y0) y0 = y;
        if (x > x1) x1 = x;
        if (y > y1) y1 = y;
      }
  return x1 < 0 ? null : { x0, y0, x1: x1 + 1, y1: y1 + 1 };
}

/** Grid of motif boxes by alpha projection: row bands, then column bands within each row. */
function segmentGrid(img: Img, expectRows: number, expectCols: number) {
  const rowCounts = new Array(img.h).fill(0);
  for (let y = 0; y < img.h; y++) for (let x = 0; x < img.w; x++) if (opaque(img, x, y)) rowCounts[y]++;
  const total = rowCounts.reduce((a, b) => a + b, 0);
  const rows = bands(rowCounts, total * 0.01);
  const grid: Box[][] = [];
  for (const [ry0, ry1] of rows) {
    const colCounts = new Array(img.w).fill(0);
    for (let y = ry0; y < ry1; y++) for (let x = 0; x < img.w; x++) if (opaque(img, x, y)) colCounts[x]++;
    const rowTotal = colCounts.reduce((a, b) => a + b, 0);
    const cols = bands(colCounts, rowTotal * 0.02);
    grid.push(cols.map(([cx0, cx1]) => boxOf(img, { x0: cx0, y0: ry0, x1: cx1, y1: ry1 }, ALPHA_CUTOFF)!).filter(Boolean));
  }
  const ok = grid.length === expectRows && grid.every((r) => r.length === expectCols);
  return { grid, ok, observed: grid.map((r) => r.length) };
}

/** Facing signals for one motif at native resolution. Positive = facing RIGHT. */
function facingSignals(img: Img, b: Box) {
  const h = b.y1 - b.y0;
  const headY1 = b.y0 + Math.round(h * 0.38);
  const bodyY0 = b.y0 + Math.round(h * 0.45);
  // body centre: mass centroid of the lower 55%
  let bx = 0;
  let bn = 0;
  for (let y = bodyY0; y < b.y1; y++)
    for (let x = b.x0; x < b.x1; x++)
      if (opaque(img, x, y)) {
        bx += x;
        bn++;
      }
  const bodyCx = bn ? bx / bn : (b.x0 + b.x1) / 2;
  // head band extents and skin-tone centroid
  let left = Infinity;
  let right = -Infinity;
  let sx = 0;
  let sn = 0;
  let hx = 0;
  let hn = 0;
  for (let y = b.y0; y < headY1; y++)
    for (let x = b.x0; x < b.x1; x++) {
      const i = (y * img.w + x) * 4;
      if (img.data[i + 3] < ALPHA_CUTOFF) continue;
      if (x < left) left = x;
      if (x > right) right = x;
      hx += x;
      hn++;
      const r = img.data[i];
      const g = img.data[i + 1];
      const bl = img.data[i + 2];
      if (r > 140 && r > g && g > bl && r - bl > 45 && r - g < 80) {
        sx += x;
        sn++;
      }
    }
  const headCx = hn ? hx / hn : bodyCx;
  return {
    extension: (right - bodyCx) - (bodyCx - left), // snout/prop protrusion side
    headShift: headCx - bodyCx,
    skinShift: sn >= 20 ? sx / sn - headCx : 0,
    skinPixels: sn,
  };
}

/** Binary masks of two motifs aligned bottom-centre; IoU of `a` against the horizontal mirror of `b`. */
function mirrorIoU(img: Img, a: Box, b: Box): number {
  const w = Math.max(a.x1 - a.x0, b.x1 - b.x0);
  const h = Math.max(a.y1 - a.y0, b.y1 - b.y0);
  let inter = 0;
  let uni = 0;
  for (let y = 0; y < h; y++)
    for (let x = 0; x < w; x++) {
      const ax = a.x0 + x - Math.floor((w - (a.x1 - a.x0)) / 2);
      const ay = a.y1 - h + y;
      const bxm = b.x1 - 1 - (x - Math.floor((w - (b.x1 - b.x0)) / 2));
      const by = b.y1 - h + y;
      const inA = ax >= a.x0 && ax < a.x1 && ay >= a.y0 && opaque(img, ax, ay);
      const inB = bxm >= b.x0 && bxm < b.x1 && by >= b.y0 && opaque(img, bxm, by);
      if (inA && inB) inter++;
      if (inA || inB) uni++;
    }
  return uni ? inter / uni : 0;
}

/** Block-mode downsample of a source box into a w x h frame anchored bottom-centre on `anchorX`. */
function downsample(img: Img, b: Box, scale: number, canvasW: number, canvasH: number, footCx: number): string[][] {
  const frame: string[][] = Array.from({ length: canvasH }, () => new Array<string>(canvasW).fill(''));
  const inv = 1 / scale;
  const outW = Math.ceil((b.x1 - b.x0) * scale);
  const outH = Math.ceil((b.y1 - b.y0) * scale);
  // the foot centre lands on the canvas centre column; the motif bottom on the canvas bottom
  const offX = Math.round(canvasW / 2 - (footCx - b.x0) * scale);
  const offY = canvasH - outH;
  for (let ty = 0; ty < outH; ty++)
    for (let tx = 0; tx < outW; tx++) {
      const sx0 = Math.floor(b.x0 + tx * inv);
      const sy0 = Math.floor(b.y0 + ty * inv);
      const sx1 = Math.min(b.x1, Math.max(sx0 + 1, Math.floor(b.x0 + (tx + 1) * inv)));
      const sy1 = Math.min(b.y1, Math.max(sy0 + 1, Math.floor(b.y0 + (ty + 1) * inv)));
      const buckets = new Map<number, { n: number; r: number; g: number; b: number }>();
      let solid = 0;
      let area = 0;
      for (let y = sy0; y < sy1; y++)
        for (let x = sx0; x < sx1; x++) {
          area++;
          const i = (y * img.w + x) * 4;
          if (img.data[i + 3] < ALPHA_CUTOFF) continue;
          solid++;
          const key = ((img.data[i] >> 4) << 8) | ((img.data[i + 1] >> 4) << 4) | (img.data[i + 2] >> 4);
          const e = buckets.get(key) ?? { n: 0, r: 0, g: 0, b: 0 };
          e.n++;
          e.r += img.data[i];
          e.g += img.data[i + 1];
          e.b += img.data[i + 2];
          buckets.set(key, e);
        }
      if (solid * 2 < area) continue;
      let best = { n: 0, r: 0, g: 0, b: 0 };
      for (const e of buckets.values()) if (e.n > best.n) best = e;
      const hex = (v: number) => Math.round(v / best.n).toString(16).padStart(2, '0');
      const fx = tx + offX;
      const fy = ty + offY;
      if (fx >= 0 && fx < canvasW && fy >= 0 && fy < canvasH) frame[fy][fx] = `#${hex(best.r)}${hex(best.g)}${hex(best.b)}`;
    }
  return frame;
}

function footCentre(img: Img, b: Box): number {
  // mass centroid of the lowest 8% of the motif (the feet / base)
  const y0 = b.y1 - Math.max(2, Math.round((b.y1 - b.y0) * 0.08));
  let sx = 0;
  let n = 0;
  for (let y = y0; y < b.y1; y++)
    for (let x = b.x0; x < b.x1; x++)
      if (opaque(img, x, y)) {
        sx += x;
        n++;
      }
  return n ? sx / n : (b.x0 + b.x1) / 2;
}

function writePng(file: string, frames: string[][][][]): void {
  // frames[dir][frame] -> rows of hex; sheet = dirs as rows, frames as columns
  const fh = frames[0][0].length;
  const fw = frames[0][0][0].length;
  const cols = Math.max(...frames.map((d) => d.length));
  const png = new PNG({ width: fw * cols, height: fh * frames.length });
  png.data.fill(0);
  frames.forEach((dir, r) =>
    dir.forEach((frame, c) =>
      frame.forEach((row, y) =>
        row.forEach((px, x) => {
          if (!px) return;
          const i = ((r * fh + y) * png.width + c * fw + x) * 4;
          png.data[i] = parseInt(px.slice(1, 3), 16);
          png.data[i + 1] = parseInt(px.slice(3, 5), 16);
          png.data[i + 2] = parseInt(px.slice(5, 7), 16);
          png.data[i + 3] = 255;
        }),
      ),
    ),
  );
  fs.mkdirSync(path.dirname(file), { recursive: true });
  fs.writeFileSync(file, PNG.sync.write(png));
}

function load(dir: string, s: Source): { img: Img; bytes: Buffer } {
  const bytes = fs.readFileSync(path.join(dir, s.file));
  const digest = sha(bytes);
  if (digest !== s.sha256) throw new Error(`${s.file}: sha256 ${digest} != expected ${s.sha256} (refusing)`);
  const png = PNG.sync.read(bytes);
  return { img: { w: png.width, h: png.height, data: png.data }, bytes };
}

function prepareCharacter(dir: string, s: Source) {
  const { img } = load(dir, s);
  const before = census(img);
  const zeroed = zeroHiddenRgb(img);
  const cols = s.kind === 'full' ? 8 : 4;
  const seg = segmentGrid(img, 4, cols);
  if (!seg.ok) return { id: s.id, file: s.file, kind: s.kind, census: before, zeroedHiddenRgb: zeroed, segmentation: { ok: false, observedPerRow: seg.observed }, admitted: false, reason: 'grid segmentation did not yield 4 rows of the expected columns' };
  // before/after boxes for one motif: alpha>0 vs alpha>=cutoff
  const sample = seg.grid[0][0];
  const pad: Box = { x0: Math.max(0, sample.x0 - 40), y0: Math.max(0, sample.y0 - 40), x1: Math.min(img.w, sample.x1 + 40), y1: Math.min(img.h, sample.y1 + 40) };
  const bboxAlpha1 = boxOf(img, pad, 1);
  const bboxCutoff = boxOf(img, pad, ALPHA_CUTOFF);
  // facing: rows 1 and 2 are the side rows (by position); measure which way each faces
  const side = [1, 2].map((r) => {
    const sig = seg.grid[r].map((b) => facingSignals(img, b));
    const mean = (k: 'extension' | 'headShift' | 'skinShift') => sig.reduce((a, v) => a + v[k], 0) / sig.length;
    const votes = [Math.sign(mean('extension')), Math.sign(mean('headShift')), Math.sign(mean('skinShift'))].filter((v) => v !== 0);
    const sum = votes.reduce((a, v) => a + v, 0);
    return { row: r + 1, extension: mean('extension'), headShift: mean('headShift'), skinShift: mean('skinShift'), skinPixels: sig.reduce((a, v) => a + v.skinPixels, 0), votes, facing: sum > 0 ? 'RIGHT' : sum < 0 ? 'LEFT' : 'UNDETERMINED' };
  });
  const mirror = seg.grid[1].map((b, i) => mirrorIoU(img, b, seg.grid[2][i]));
  const meanMirror = mirror.reduce((a, v) => a + v, 0) / mirror.length;
  // down vs up: skin pixels in the head band (a face shows more skin than the back of a head)
  const skin = [0, 3].map((r) => seg.grid[r].reduce((a, b) => a + facingSignals(img, b).skinPixels, 0));
  const adjudication = ADJUDICATED[s.id];
  if (!adjudication) throw new Error(`${s.id}: no facing adjudication recorded — refusing to map rows`);
  const observedOrder = adjudication.order;
  // corroboration only: does the skin-side signal of each side row agree with the adjudicated facing?
  const skinAgrees = side.map((row, k) => {
    const adjudicated = observedOrder[k + 1];
    const skinSays = row.skinShift > 0 ? 'RIGHT' : row.skinShift < 0 ? 'LEFT' : 'NONE';
    return { row: row.row, adjudicated, skinSignal: skinSays, agrees: skinSays === adjudicated };
  });
  // canonical output order DOWN, LEFT, RIGHT, UP
  const srcRowFor = { DOWN: 0, LEFT: observedOrder.indexOf('LEFT'), RIGHT: observedOrder.indexOf('RIGHT'), UP: 3 };
  const maxH = Math.max(...seg.grid.flat().map((b) => b.y1 - b.y0));
  const maxW = Math.max(...seg.grid.flat().map((b) => b.x1 - b.x0));
  const scale = CHAR_TARGET_HEIGHT / maxH;
  const canvasW = Math.max(16, Math.ceil(maxW * scale) + 4);
  const frames = (['DOWN', 'LEFT', 'RIGHT', 'UP'] as const).map((d) =>
    seg.grid[srcRowFor[d]].map((b) => downsample(img, b, scale, canvasW, CHAR_CANVAS_H, footCentre(img, b))),
  );
  // anchor QA: foot-centre jitter across the walk frames (game pixels)
  const walkCols = s.kind === 'full' ? [0, 1, 2, 3] : [0, 1];
  const footJitter = seg.grid.map((row) => {
    const xs = walkCols.map((c) => (footCentre(img, row[c]) - row[c].x0) * scale);
    return Math.round((Math.max(...xs) - Math.min(...xs)) * 100) / 100;
  });
  const outDir = path.join(ROOT, 'webview-ui/public/assets/ambient', s.runtimeId!);
  writePng(path.join(outDir, `${s.runtimeId}.png`), frames);
  const manifest = {
    id: s.runtimeId,
    label: s.label,
    source: { id: s.id, file: s.file, sha256: s.sha256 },
    contract: s.kind === 'full' ? 'FULL 8x4' : 'MIN 4x4',
    frameWidth: canvasW,
    frameHeight: CHAR_CANVAS_H,
    rows: ['DOWN', 'LEFT', 'RIGHT', 'UP'],
    columns: s.kind === 'full' ? ['walk1', 'walk2', 'walk3', 'walk4', 'idle1', 'idle2', 'gesture1', 'gesture2'] : ['walk1', 'walk2', 'idle1', 'gesture1'],
    walk: walkCols,
    idle: s.kind === 'full' ? [4, 5] : [2],
    gesture: s.kind === 'full' ? [6, 7] : [3],
    walkCycle: s.kind === 'full' ? '4-frame (C1-C4, all drawn)' : '2-frame (C1, C2, both drawn; no derived frames)',
    adjudicatedSourceRowOrder: observedOrder,
    facingBasis: 'visual adjudication by executor1 on a named cue (see LIVING-WORLD-ART-QA.json)',
    differsFromContract: observedOrder[1] !== 'LEFT',
  };
  fs.writeFileSync(path.join(outDir, 'manifest.json'), `${JSON.stringify(manifest, null, 2)}\n`);
  return {
    id: s.id,
    runtimeId: s.runtimeId,
    file: s.file,
    kind: s.kind,
    census: before,
    zeroedHiddenRgb: zeroed,
    alphaCutoff: ALPHA_CUTOFF,
    bboxSample: { alphaAbove0: bboxAlpha1, alphaAtCutoff: bboxCutoff },
    segmentation: { ok: true, method: 'alpha projection bands (no fixed stride)', observedPerRow: seg.observed },
    facing: {
      basis: 'ADJUDICATED (visual, executor1) — not a programmatic vote',
      adjudicatedSourceRowOrder: observedOrder,
      cue: adjudication.cue,
      contractRequestedOrder: ['DOWN', 'LEFT', 'RIGHT', 'UP'],
      differsFromContract: observedOrder[1] !== 'LEFT',
      corroboration: { skinSignalPerSideRow: skinAgrees, sideRowSignals: side, downRowSkinPixels: skin[0], upRowSkinPixels: skin[1] },
      mirrorSimilarityIoU: Math.round(meanMirror * 1000) / 1000,
      mirrorSimilarityNote: 'IoU of band 2 against the horizontal mirror of band 3 (bottom-centre aligned masks). SIMILARITY ONLY: separately drawn profiles of one character score high; this is not evidence that either side was derived by mirroring, and no art is labelled mirrored on this basis.',
    },
    scale: Math.round(scale * 10000) / 10000,
    frame: { w: canvasW, h: CHAR_CANVAS_H },
    footJitterPxPerRow: footJitter,
    cells: { delivered: cols * 4, extracted: cols * 4, alphaVerified: cols * 4 },
    output: path.relative(ROOT, outDir),
  };
}

function prepareRetail(dir: string, s: Source) {
  const { img } = load(dir, s);
  const before = census(img);
  const zeroed = zeroHiddenRgb(img);
  // two motif rows, each split into a store (wide) and a forecourt by the column projection
  const seg = segmentGrid(img, 2, 2);
  if (!seg.ok) return { id: s.id, census: before, segmentation: { ok: false, observedPerRow: seg.observed }, admitted: false };
  const scale = RETAIL_STORE_W / Math.max(seg.grid[0][0].x1 - seg.grid[0][0].x0, seg.grid[1][0].x1 - seg.grid[1][0].x0);
  const names = [
    ['BIGBOX_TARGET', 'Target', 'BIGBOX_TARGET_FORECOURT', 'Target forecourt'],
    ['BIGBOX_WALMART', 'Walmart', 'BIGBOX_WALMART_FORECOURT', 'Walmart forecourt'],
  ];
  const assets = [];
  for (let r = 0; r < 2; r++)
    for (let c = 0; c < 2; c++) {
      const b = seg.grid[r][c];
      const id = names[r][c * 2];
      const label = names[r][c * 2 + 1];
      const outW = Math.ceil((b.x1 - b.x0) * scale);
      const outH = Math.ceil((b.y1 - b.y0) * scale);
      const fw = Math.ceil(outW / 16);
      const fh = Math.ceil(outH / 16);
      const frame = downsample(img, b, scale, fw * 16, fh * 16, (b.x0 + b.x1) / 2);
      const solidRows = c === 0 ? 3 : 2; // building base rows block; the rest is walk-behind
      const dirOut = path.join(ROOT, 'webview-ui/public/assets/furniture', id);
      writePng(path.join(dirOut, `${id}.png`), [[frame]]);
      const manifest = { id, name: label, category: 'decor', type: 'asset', canPlaceOnWalls: false, canPlaceOnSurfaces: false, backgroundTiles: Math.max(0, fh - solidRows), width: fw * 16, height: fh * 16, footprintW: fw, footprintH: fh };
      fs.writeFileSync(path.join(dirOut, 'manifest.json'), `${JSON.stringify(manifest, null, 2)}\n`);
      assets.push({ id, label, sourceBox: b, drawn: { w: outW, h: outH }, footprint: { w: fw, h: fh, solidRows, backgroundTiles: Math.max(0, fh - solidRows) } });
    }
  return { id: s.id, file: s.file, kind: 'retail STATIC', census: before, zeroedHiddenRgb: zeroed, alphaCutoff: ALPHA_CUTOFF, segmentation: { ok: true, method: 'alpha projection bands (unequal columns, no fixed stride)', observedPerRow: seg.observed }, scale: Math.round(scale * 10000) / 10000, assets, cells: { delivered: 4, extracted: 4, alphaVerified: 4 }, lettering: 'TARGET and Walmart store names, plus a "Grocery" band on the Walmart facade (authorized retail exception)' };
}

/**
 * VEHICLE FACING ADJUDICATION. The sheets REQUESTED columns down, left, right, up; that is a request.
 * Adjudicated by executor1 on enlarged source crops, 2026-10-16. The cue for every side view: the red
 * tail lamp marks the REAR, while the bonnet, the raked windscreen and the side mirror mark the
 * FRONT. Front views show the headlights, grille and windscreen; rear views show the tail lamps,
 * the rear window or doors, and the plate. The fire engine is also read by its cab (windscreen) end.
 * Corroboration only: the share of saturated red-lamp pixels in each end of a side view. It is not
 * applicable to red-bodied vehicles, and is recorded as such.
 */
const VEHICLES: Record<string, Array<{ id: string; label: string; lengthPx: number; redBody: boolean }>> = {
  T01: [
    { id: 'FIRE_ENGINE', label: 'Fire engine (decorative, parked)', lengthPx: 50, redBody: true },
    { id: 'POLICE_CRUISER', label: 'Police cruiser (decorative, parked)', lengthPx: 34, redBody: false },
    { id: 'AMBULANCE', label: 'Ambulance (decorative, parked)', lengthPx: 44, redBody: false },
  ],
  T02: [
    { id: 'TAXI', label: 'Yellow taxi (parked)', lengthPx: 32, redBody: false },
    { id: 'HATCHBACK_RED', label: 'Red hatchback (parked)', lengthPx: 28, redBody: true },
    { id: 'SEDAN_BLUE', label: 'Blue sedan (parked)', lengthPx: 33, redBody: false },
    { id: 'SUV_SILVER', label: 'Silver SUV (parked)', lengthPx: 34, redBody: false },
  ],
};
const VEHICLE_ADJUDICATED_COLUMNS = ['DOWN', 'LEFT', 'RIGHT', 'UP'];
const VEHICLE_CUE = 'side views: red tail lamp at the rear end, bonnet + raked windscreen + side mirror at the front; front view: headlights, grille, windscreen; rear view: tail lamps, rear window/doors, plate';

function redLampEnds(img: Img, b: Box) {
  const w = b.x1 - b.x0;
  const band = Math.max(4, Math.round(w * 0.12));
  let left = 0;
  let right = 0;
  for (let y = b.y0; y < b.y1; y++)
    for (let x = b.x0; x < b.x1; x++) {
      const i = (y * img.w + x) * 4;
      if (img.data[i + 3] < ALPHA_CUTOFF) continue;
      const r = img.data[i];
      const g = img.data[i + 1];
      const bl = img.data[i + 2];
      if (r > 170 && g < 80 && bl < 80) {
        if (x < b.x0 + band) left++;
        else if (x >= b.x1 - band) right++;
      }
    }
  return { left, right };
}

function writeFurniture(id: string, label: string, frame: string[][], fw: number, fh: number, solidRows: number) {
  const dirOut = path.join(ROOT, 'webview-ui/public/assets/furniture', id);
  writePng(path.join(dirOut, `${id}.png`), [[frame]]);
  const manifest = { id, name: label, category: 'decor', type: 'asset', canPlaceOnWalls: false, canPlaceOnSurfaces: false, backgroundTiles: Math.max(0, fh - solidRows), width: fw * 16, height: fh * 16, footprintW: fw, footprintH: fh };
  fs.writeFileSync(path.join(dirOut, 'manifest.json'), `${JSON.stringify(manifest, null, 2)}\n`);
}

function prepareVehicles(dir: string, s: Source) {
  const { img } = load(dir, s);
  const before = census(img);
  const zeroed = zeroHiddenRgb(img);
  const rows = VEHICLES[s.id];
  const seg = segmentGrid(img, rows.length, 4);
  if (!seg.ok) return { id: s.id, census: before, segmentation: { ok: false, observedPerRow: seg.observed }, admitted: false };
  const assets = [];
  const corroboration = [];
  for (let r = 0; r < rows.length; r++) {
    const v = rows[r];
    const cells = seg.grid[r];
    const sideW = ((cells[1].x1 - cells[1].x0) + (cells[2].x1 - cells[2].x0)) / 2;
    const scale = v.lengthPx / sideW;
    for (let c = 0; c < 4; c++) {
      const b = cells[c];
      const facing = VEHICLE_ADJUDICATED_COLUMNS[c];
      const outW = Math.ceil((b.x1 - b.x0) * scale);
      const outH = Math.ceil((b.y1 - b.y0) * scale);
      const fw = Math.max(1, Math.ceil(outW / 16));
      const fh = Math.max(1, Math.ceil(outH / 16));
      const frame = downsample(img, b, scale, fw * 16, fh * 16, (b.x0 + b.x1) / 2);
      const id = `VEHICLE_${v.id}_${facing}`;
      writeFurniture(id, `${v.label} facing ${facing.toLowerCase()}`, frame, fw, fh, 1);
      assets.push({ id, facing, sourceBox: b, drawn: { w: outW, h: outH }, footprint: { w: fw, h: fh, solidRows: 1, backgroundTiles: fh - 1 } });
      if (facing === 'LEFT' || facing === 'RIGHT') {
        const ends = redLampEnds(img, b);
        const rearAt = ends.left > ends.right * 1.5 ? 'left' : ends.right > ends.left * 1.5 ? 'right' : 'unclear';
        const implied = rearAt === 'right' ? 'LEFT' : rearAt === 'left' ? 'RIGHT' : 'NONE';
        corroboration.push({ id, adjudicated: facing, redLampPixels: ends, signal: v.redBody ? 'not applicable (red body)' : implied, agrees: v.redBody ? null : implied === facing });
      }
    }
  }
  return {
    id: s.id,
    file: s.file,
    kind: 'vehicles STATIC (parked props; no motion, no vehicle AI)',
    census: before,
    zeroedHiddenRgb: zeroed,
    alphaCutoff: ALPHA_CUTOFF,
    segmentation: { ok: true, method: 'alpha projection bands (no fixed stride)', observedPerRow: seg.observed },
    facing: { basis: 'ADJUDICATED (visual, executor1)', adjudicatedColumnOrder: VEHICLE_ADJUDICATED_COLUMNS, contractRequestedOrder: ['DOWN', 'LEFT', 'RIGHT', 'UP'], differsFromContract: false, cue: VEHICLE_CUE, corroboration },
    assets,
    cells: { delivered: rows.length * 4, extracted: assets.length, alphaVerified: assets.length },
  };
}

/** G01: six motifs in two bands, extracted by alpha projection (the portal spans 43% of the sheet). */
const CATHEDRAL = [
  [
    { id: 'CATHEDRAL_ROSE_WINDOW', label: 'Rose window', widthPx: 64, solidRows: 0 },
    { id: 'CATHEDRAL_LANCET', label: 'Stained-glass lancet', widthPx: 16, solidRows: 0 },
    { id: 'CATHEDRAL_PORTAL', label: 'Open triple portal', widthPx: 160, solidRows: 0 },
  ],
  [
    { id: 'CATHEDRAL_TOWER_TOP', label: 'Bell-tower upper stage', widthPx: 96, solidRows: 0 },
    { id: 'CATHEDRAL_ALTAR', label: 'High altar', widthPx: 64, solidRows: 2 },
    { id: 'CATHEDRAL_COLUMN', label: 'Clustered column', widthPx: 16, solidRows: 1 },
  ],
];

function prepareCathedral(dir: string, s: Source) {
  const { img } = load(dir, s);
  const before = census(img);
  const zeroed = zeroHiddenRgb(img);
  // the two motif bands touch vertically (no empty row run), so split at the emptiest row of the
  // middle third, then segment columns within each band by projection
  const rowCounts = new Array(img.h).fill(0);
  for (let y = 0; y < img.h; y++) for (let x = 0; x < img.w; x++) if (opaque(img, x, y)) rowCounts[y]++;
  let split = Math.floor(img.h / 3);
  for (let y = Math.floor(img.h / 3); y < Math.floor((img.h * 2) / 3); y++) if (rowCounts[y] < rowCounts[split]) split = y;
  const grid: Box[][] = [];
  for (const [ry0, ry1] of [[0, split], [split, img.h]]) {
    const colCounts = new Array(img.w).fill(0);
    for (let y = ry0; y < ry1; y++) for (let x = 0; x < img.w; x++) if (opaque(img, x, y)) colCounts[x]++;
    const total = colCounts.reduce((a, b) => a + b, 0);
    grid.push(bands(colCounts, total * 0.02).map(([cx0, cx1]) => boxOf(img, { x0: cx0, y0: ry0, x1: cx1, y1: ry1 }, ALPHA_CUTOFF)!).filter(Boolean));
  }
  const seg = { grid, ok: grid.every((r) => r.length === 3), observed: grid.map((r) => r.length), splitRow: split, splitRowOpaque: rowCounts[split] };
  if (!seg.ok) return { id: s.id, census: before, segmentation: { ok: false, observedPerRow: seg.observed, splitRow: split }, admitted: false };
  const assets = [];
  let portalArches: number[] = [];
  for (let r = 0; r < 2; r++)
    for (let c = 0; c < 3; c++) {
      const m = CATHEDRAL[r][c];
      const b = seg.grid[r][c];
      const scale = m.widthPx / (b.x1 - b.x0);
      const outW = Math.ceil((b.x1 - b.x0) * scale);
      const outH = Math.ceil((b.y1 - b.y0) * scale);
      const fw = Math.max(1, Math.ceil(outW / 16));
      const fh = Math.max(1, Math.ceil(outH / 16));
      const frame = downsample(img, b, scale, fw * 16, fh * 16, (b.x0 + b.x1) / 2);
      writeFurniture(m.id, m.label, frame, fw, fh, m.solidRows);
      const entry: Record<string, unknown> = { id: m.id, sourceBox: b, scale: Math.round(scale * 10000) / 10000, drawn: { w: outW, h: outH }, footprint: { w: fw, h: fh, solidRows: m.solidRows, backgroundTiles: fh - m.solidRows } };
      if (m.id === 'CATHEDRAL_PORTAL') {
        // walkable archway tiles: footprint columns whose bottom tile is at least 60% transparent
        // in the game-scale frame (the archways are true alpha-0 holes in the source art)
        portalArches = [];
        for (let tx = 0; tx < fw; tx++) {
          let clear = 0;
          for (let y = fh * 16 - 16; y < fh * 16; y++) for (let x = tx * 16; x < tx * 16 + 16; x++) if (!frame[y][x]) clear++;
          if (clear >= 256 * 0.6) portalArches.push(tx);
        }
        entry.archTileColumns = portalArches;
        entry.archBasis = 'bottom tile row of the game-scale frame, >= 60% transparent; the source archways are alpha 0 (lead1 sampled (950,400), (1160,400), (1380,400))';
      }
      assets.push(entry);
    }
  return {
    id: s.id,
    file: s.file,
    kind: 'cathedral kit STATIC',
    census: before,
    zeroedHiddenRgb: zeroed,
    alphaCutoff: ALPHA_CUTOFF,
    segmentation: { ok: true, method: 'band split at the emptiest middle row, then alpha projection columns (unequal motif widths, no fixed stride)', splitRow: seg.splitRow, splitRowOpaquePixels: seg.splitRowOpaque, observedPerRow: seg.observed, columnSpans: seg.grid.map((row) => row.map((b) => [b.x0, b.x1 - 1])) },
    assets,
    portalArchTileColumns: portalArches,
    cells: { delivered: 6, extracted: assets.length, alphaVerified: assets.length },
  };
}

/**
 * M01 MUSEUM EXHIBITS: 16 distinct motifs, extracted by alpha components with a gutter threshold wide
 * enough that the triptych's three panels and the botanical pair stay ONE exhibit each. This sheet
 * differs from the others: it has true alpha 255, almost no hidden matte, and a faint red fringe at
 * alpha 1-2. The fringe sits below the alpha cutoff, so it never inflates a box or enters a pixel.
 * PARTIAL alpha inside a sprite (the glass of the case and the vitrine) is KEPT: output pixels carry
 * the mean alpha of their source block, never flattened to opaque.
 */
const EXHIBITS: Array<Array<{ id: string; label: string; fw: number; fh: number; wall: boolean }>> = [
  [
    { id: 'MUSEUM_PAINTING_LANDSCAPE', label: 'Landscape painting', fw: 2, fh: 3, wall: true },
    { id: 'MUSEUM_PAINTING_PORTRAIT', label: 'Portrait painting', fw: 2, fh: 3, wall: true },
    { id: 'MUSEUM_PAINTING_ABSTRACT', label: 'Abstract canvas', fw: 2, fh: 3, wall: true },
    { id: 'MUSEUM_PAINTING_STILL_LIFE', label: 'Still-life painting', fw: 2, fh: 3, wall: true },
  ],
  [
    { id: 'MUSEUM_PAINTING_SEASCAPE', label: 'Seascape painting', fw: 3, fh: 2, wall: true },
    { id: 'MUSEUM_TRIPTYCH', label: 'Triptych', fw: 3, fh: 3, wall: true },
    { id: 'MUSEUM_TAPESTRY', label: 'Tapestry', fw: 2, fh: 4, wall: true },
    { id: 'MUSEUM_BOTANICAL_PAIR', label: 'Botanical studies (pair)', fw: 2, fh: 2, wall: true },
  ],
  [
    { id: 'MUSEUM_BUST', label: 'Marble bust', fw: 1, fh: 2, wall: false },
    { id: 'MUSEUM_BRONZE', label: 'Bronze figure', fw: 1, fh: 3, wall: false },
    { id: 'MUSEUM_ABSTRACT_STONE', label: 'Abstract stone sculpture', fw: 2, fh: 2, wall: false },
    { id: 'MUSEUM_URN', label: 'Ceramic urn', fw: 1, fh: 2, wall: false },
  ],
  [
    { id: 'MUSEUM_DISPLAY_CASE', label: 'Glass display case', fw: 2, fh: 2, wall: false },
    { id: 'MUSEUM_VITRINE', label: 'Low vitrine', fw: 2, fh: 1, wall: false },
    { id: 'MUSEUM_FOSSIL', label: 'Mounted fossil', fw: 3, fh: 3, wall: false },
    { id: 'MUSEUM_PLACARD', label: 'Placard and rope', fw: 1, fh: 1, wall: false },
  ],
];

function prepareExhibits(dir: string, s: Source) {
  const { img } = load(dir, s);
  const before = census(img);
  const zeroed = zeroHiddenRgb(img);
  const rowCounts = new Array(img.h).fill(0);
  for (let y = 0; y < img.h; y++) for (let x = 0; x < img.w; x++) if (opaque(img, x, y)) rowCounts[y]++;
  // rows: the emptiest row near each quarter boundary (rows of motifs nearly touch: one gap is 3 px,
  // one has 25 opaque pixels)
  const splits: number[] = [];
  for (let k = 1; k < 4; k++) {
    const lo = Math.floor((img.h * k) / 4 - img.h / 8);
    const hi = Math.floor((img.h * k) / 4 + img.h / 8);
    let m = lo;
    for (let y = lo; y < hi; y++) if (rowCounts[y] < rowCounts[m]) m = y;
    splits.push(m);
  }
  const bandsY: Array<[number, number]> = [[0, splits[0]], [splits[0], splits[1]], [splits[1], splits[2]], [splits[2], img.h]];
  // within a band: 8-connected alpha components, joined when any two pixels lie within JOIN px (the
  // triptych's panels and the botanical pair are 0-1 px apart); a fossil and a placard whose boxes
  // merely overlap stay apart
  const JOIN = 4;
  const grid: Box[][] = bandsY.map(([ry0, ry1]) => {
    const w = img.w;
    const h = ry1 - ry0;
    const lab = new Int32Array(w * h).fill(-1);
    const parent: number[] = [];
    const find = (x: number): number => (parent[x] === x ? x : (parent[x] = find(parent[x])));
    let n = 0;
    for (let y = 0; y < h; y++)
      for (let x = 0; x < w; x++) {
        if (lab[y * w + x] >= 0 || !opaque(img, x, ry0 + y)) continue;
        const id = n++;
        parent.push(id);
        const q = [y * w + x];
        lab[y * w + x] = id;
        while (q.length) {
          const k = q.pop()!;
          const xx = k % w;
          const yy = (k - xx) / w;
          for (let dy = -1; dy <= 1; dy++)
            for (let dx = -1; dx <= 1; dx++) {
              const nx = xx + dx;
              const ny = yy + dy;
              if (nx < 0 || ny < 0 || nx >= w || ny >= h) continue;
              const kk = ny * w + nx;
              if (lab[kk] < 0 && opaque(img, nx, ry0 + ny)) {
                lab[kk] = id;
                q.push(kk);
              }
            }
        }
      }
    for (let y = 0; y < h; y++)
      for (let x = 0; x < w; x++) {
        const a0 = lab[y * w + x];
        if (a0 < 0) continue;
        for (let dy = -JOIN; dy <= JOIN; dy++)
          for (let dx = -JOIN; dx <= JOIN; dx++) {
            const nx = x + dx;
            const ny = y + dy;
            if (nx < 0 || ny < 0 || nx >= w || ny >= h) continue;
            const b0 = lab[ny * w + nx];
            if (b0 >= 0 && find(b0) !== find(a0)) parent[find(b0)] = find(a0);
          }
      }
    const boxes = new Map<number, Box & { n: number }>();
    for (let y = 0; y < h; y++)
      for (let x = 0; x < w; x++) {
        const l = lab[y * w + x];
        if (l < 0) continue;
        const r = find(l);
        const bx = boxes.get(r) ?? { x0: x, y0: ry0 + y, x1: x + 1, y1: ry0 + y + 1, n: 0 };
        bx.x0 = Math.min(bx.x0, x);
        bx.x1 = Math.max(bx.x1, x + 1);
        bx.y0 = Math.min(bx.y0, ry0 + y);
        bx.y1 = Math.max(bx.y1, ry0 + y + 1);
        bx.n++;
        boxes.set(r, bx);
      }
    const mass = [...boxes.values()].reduce((t, bx) => t + bx.n, 0);
    return [...boxes.values()].filter((bx) => bx.n >= mass * 0.01).sort((p0, q0) => p0.x0 - q0.x0).map(({ x0, y0, x1, y1 }) => ({ x0, y0, x1, y1 }));
  });
  const ok = grid.length === 4 && grid.every((r) => r.length === 4);
  if (!ok) return { id: s.id, census: before, segmentation: { ok: false, observedPerRow: grid.map((r) => r.length) }, admitted: false };
  const assets = [];
  let partialAlphaPixels = 0;
  for (let r = 0; r < 4; r++)
    for (let c = 0; c < 4; c++) {
      const m = EXHIBITS[r][c];
      const b = grid[r][c];
      const cw = m.fw * 16;
      const ch = m.fh * 16;
      const padBottom = m.wall ? 6 : 1; // wall art sits on the wall face like r4's paintings
      const availW = cw - 4;
      const availH = ch - padBottom - 1;
      const scale = Math.min(availW / (b.x1 - b.x0), availH / (b.y1 - b.y0));
      const outW = Math.max(1, Math.round((b.x1 - b.x0) * scale));
      const outH = Math.max(1, Math.round((b.y1 - b.y0) * scale));
      const png = new PNG({ width: cw, height: ch });
      png.data.fill(0);
      const offX = Math.floor((cw - outW) / 2);
      const offY = ch - padBottom - outH;
      for (let ty = 0; ty < outH; ty++)
        for (let tx = 0; tx < outW; tx++) {
          const sx0 = Math.floor(b.x0 + tx / scale);
          const sy0 = Math.floor(b.y0 + ty / scale);
          const sx1 = Math.min(b.x1, Math.max(sx0 + 1, Math.floor(b.x0 + (tx + 1) / scale)));
          const sy1 = Math.min(b.y1, Math.max(sy0 + 1, Math.floor(b.y0 + (ty + 1) / scale)));
          const buckets = new Map<number, { n: number; r: number; g: number; b: number; a: number }>();
          let solid = 0;
          let area = 0;
          for (let y = sy0; y < sy1; y++)
            for (let x = sx0; x < sx1; x++) {
              area++;
              const i = (y * img.w + x) * 4;
              const a = img.data[i + 3];
              if (a < ALPHA_CUTOFF) continue; // the red alpha-1/2 fringe never enters a pixel
              solid++;
              const key = ((img.data[i] >> 4) << 8) | ((img.data[i + 1] >> 4) << 4) | (img.data[i + 2] >> 4);
              const e = buckets.get(key) ?? { n: 0, r: 0, g: 0, b: 0, a: 0 };
              e.n++;
              e.r += img.data[i];
              e.g += img.data[i + 1];
              e.b += img.data[i + 2];
              e.a += a;
              buckets.set(key, e);
            }
          if (solid * 2 < area) continue;
          let best = { n: 0, r: 0, g: 0, b: 0, a: 0 };
          for (const e of buckets.values()) if (e.n > best.n) best = e;
          const j = ((offY + ty) * cw + offX + tx) * 4;
          png.data[j] = Math.round(best.r / best.n);
          png.data[j + 1] = Math.round(best.g / best.n);
          png.data[j + 2] = Math.round(best.b / best.n);
          const alpha = Math.round(best.a / best.n);
          png.data[j + 3] = alpha >= 250 ? 255 : alpha; // glass keeps its partial alpha
          if (alpha < 250) partialAlphaPixels++;
        }
      const dirOut = path.join(ROOT, 'webview-ui/public/assets/furniture', m.id);
      fs.mkdirSync(dirOut, { recursive: true });
      fs.writeFileSync(path.join(dirOut, `${m.id}.png`), PNG.sync.write(png));
      const manifest = { id: m.id, name: m.label, category: m.wall ? 'wall' : 'decor', type: 'asset', canPlaceOnWalls: m.wall, canPlaceOnSurfaces: false, backgroundTiles: m.fh - 1, width: cw, height: ch, footprintW: m.fw, footprintH: m.fh };
      fs.writeFileSync(path.join(dirOut, 'manifest.json'), `${JSON.stringify(manifest, null, 2)}\n`);
      assets.push({ id: m.id, sourceBox: b, scale: Math.round(scale * 10000) / 10000, drawn: { w: outW, h: outH }, footprint: { w: m.fw, h: m.fh, solidRows: 1, backgroundTiles: m.fh - 1, wall: m.wall } });
    }
  return {
    id: s.id,
    file: s.file,
    kind: 'museum exhibits STATIC',
    census: before,
    zeroedHiddenRgb: zeroed,
    alphaCutoff: ALPHA_CUTOFF,
    fringe: 'alpha 1-2 (faint red) excluded by the cutoff: never in a box, never in a pixel',
    partialAlphaPixelsKept: partialAlphaPixels,
    segmentation: { ok: true, method: 'rows split at the emptiest row per quarter; within a row, 8-connected alpha components joined within 4 px (the triptych and the botanical pair stay one exhibit each; the fossil and the placard, whose boxes overlap, stay apart; no fixed stride)', rowSplits: splits, observedPerRow: grid.map((r) => r.length), columnSpans: grid.map((row) => row.map((b) => [b.x0, b.x1 - 1])) },
    assets,
    cells: { delivered: 16, extracted: assets.length, alphaVerified: assets.length },
  };
}

/**
 * SUBWAY (the delivered 5 x 4 sheet; requested cells per subway-cell-map.json). Its rows hold 5, 5, 4
 * and 6 motifs, so cells are found per row band by alpha components. Components are merged pairwise at
 * the smallest gap until the row's expected count is reached: the three turnstiles and the passenger
 * group stay one motif each. Train cars keep a shared scale so the lead cars and passenger cars
 * match. Cars and the interior strip are not placed as furniture; the transit runtime draws them.
 * Facing: the passenger car body is end-symmetric, so the LEFT and RIGHT labels are not observable;
 * frames A and B are used for motion. The lead cars are adjudicated by the streamlined cab nose and
 * headlamp.
 */
const SUBWAY: Array<Array<{ id: string; label: string; widthPx: number; solidRows: number; car?: boolean }>> = [
  [
    { id: 'SUBWAY_HEADHOUSE_CONSERVATORY', label: 'Conservatory station headhouse', widthPx: 80, solidRows: 2 },
    { id: 'SUBWAY_HEADHOUSE_DECO', label: 'Art-deco station headhouse', widthPx: 80, solidRows: 2 },
    { id: 'SUBWAY_HEADHOUSE_BRICK', label: 'Red-brick station headhouse', widthPx: 80, solidRows: 2 },
    { id: 'SUBWAY_KIOSK', label: 'Covered stair kiosk', widthPx: 48, solidRows: 1 },
    { id: 'SUBWAY_PLATFORM_SHELTER', label: 'Platform shelter with benches', widthPx: 80, solidRows: 1 },
  ],
  [
    { id: 'SUBWAY_TURNSTILES', label: 'Turnstiles', widthPx: 48, solidRows: 1 },
    { id: 'SUBWAY_TICKET_MACHINE', label: 'Ticket machine', widthPx: 16, solidRows: 1 },
    { id: 'SUBWAY_ELEVATOR', label: 'Glass elevator head', widthPx: 48, solidRows: 1 },
    { id: 'SUBWAY_CAR_A', label: 'Passenger car (frame A)', widthPx: 64, solidRows: 0, car: true },
    { id: 'SUBWAY_CAR_B', label: 'Passenger car (frame B)', widthPx: 64, solidRows: 0, car: true },
  ],
  [
    { id: 'SUBWAY_CAR_A2', label: 'Passenger car (frame A, second source)', widthPx: 64, solidRows: 0, car: true },
    { id: 'SUBWAY_CAR_B2', label: 'Passenger car (frame B, second source)', widthPx: 64, solidRows: 0, car: true },
    { id: 'SUBWAY_LEAD_LEFT', label: 'Lead car, cab facing left', widthPx: 64, solidRows: 0, car: true },
    { id: 'SUBWAY_LEAD_RIGHT', label: 'Lead car, cab facing right', widthPx: 64, solidRows: 0, car: true },
  ],
  [
    { id: 'SUBWAY_INTERIOR_STRIP', label: 'Train interior strip', widthPx: 64, solidRows: 0 },
    { id: 'SUBWAY_TUNNEL_MOUTH', label: 'Arched tunnel mouth', widthPx: 64, solidRows: 1 },
    { id: 'SUBWAY_GANTRY', label: 'Overhead railway gantry', widthPx: 64, solidRows: 0 },
    { id: 'SUBWAY_ROUTE_BOARD', label: 'Route-map board (blank)', widthPx: 32, solidRows: 1 },
    { id: 'SUBWAY_WARNING_STRIP', label: 'Platform-edge warning strip', widthPx: 48, solidRows: 0 },
    { id: 'SUBWAY_WAITING_GROUP', label: 'Waiting passengers (decorative)', widthPx: 32, solidRows: 1 },
  ],
];

function prepareSubway(dir: string, s: Source) {
  const { img } = load(dir, s);
  const before = census(img);
  const zeroed = zeroHiddenRgb(img);
  const rowCounts = new Array(img.h).fill(0);
  for (let y = 0; y < img.h; y++) for (let x = 0; x < img.w; x++) if (opaque(img, x, y)) rowCounts[y]++;
  const rowsB = bands(rowCounts, rowCounts.reduce((a, b) => a + b, 0) * 0.01);
  if (rowsB.length !== 4) return { id: s.id, census: before, segmentation: { ok: false, rows: rowsB.length }, admitted: false };
  const grid: Box[][] = rowsB.map(([ry0, ry1], r) => {
    // components (8-connected) in the band, then merge the closest neighbours (by horizontal gap)
    // until the row's expected count
    const w = img.w;
    const h = ry1 - ry0;
    const lab = new Int32Array(w * h).fill(-1);
    const boxes: Array<Box & { n: number }> = [];
    for (let y = 0; y < h; y++)
      for (let x = 0; x < w; x++) {
        if (lab[y * w + x] >= 0 || !opaque(img, x, ry0 + y)) continue;
        const id = boxes.length;
        const bx = { x0: x, y0: ry0 + y, x1: x + 1, y1: ry0 + y + 1, n: 0 };
        const q = [y * w + x];
        lab[y * w + x] = id;
        while (q.length) {
          const k = q.pop()!;
          const xx = k % w;
          const yy = (k - xx) / w;
          bx.n++;
          bx.x0 = Math.min(bx.x0, xx);
          bx.x1 = Math.max(bx.x1, xx + 1);
          bx.y0 = Math.min(bx.y0, ry0 + yy);
          bx.y1 = Math.max(bx.y1, ry0 + yy + 1);
          for (let dy = -1; dy <= 1; dy++)
            for (let dx = -1; dx <= 1; dx++) {
              const nx = xx + dx;
              const ny = yy + dy;
              if (nx < 0 || ny < 0 || nx >= w || ny >= h) continue;
              const kk = ny * w + nx;
              if (lab[kk] < 0 && opaque(img, nx, ry0 + ny)) {
                lab[kk] = id;
                q.push(kk);
              }
            }
        }
        boxes.push(bx);
      }
    const mass = boxes.reduce((t, b) => t + b.n, 0);
    let list = boxes.filter((b) => b.n >= mass * 0.002).sort((a, b) => a.x0 - b.x0);
    const want = SUBWAY[r].length;
    while (list.length > want) {
      let best = 0;
      let gap = Infinity;
      for (let i = 0; i + 1 < list.length; i++) {
        const g = list[i + 1].x0 - list[i].x1;
        if (g < gap) {
          gap = g;
          best = i;
        }
      }
      const a = list[best];
      const b = list[best + 1];
      list.splice(best, 2, { x0: Math.min(a.x0, b.x0), y0: Math.min(a.y0, b.y0), x1: Math.max(a.x1, b.x1), y1: Math.max(a.y1, b.y1), n: a.n + b.n });
      list = list.sort((p0, q0) => p0.x0 - q0.x0);
    }
    return list.map(({ x0, y0, x1, y1 }) => ({ x0, y0, x1, y1 }));
  });
  const ok = grid.every((row, r) => row.length === SUBWAY[r].length);
  if (!ok) return { id: s.id, census: before, segmentation: { ok: false, observedPerRow: grid.map((r) => r.length) }, admitted: false };
  // one scale for all cars, from the passenger car (frame A, row 2)
  const carScale = SUBWAY[1][3].widthPx / (grid[1][3].x1 - grid[1][3].x0);
  const assets = [];
  for (let r = 0; r < 4; r++)
    for (let c = 0; c < SUBWAY[r].length; c++) {
      const m = SUBWAY[r][c];
      const b = grid[r][c];
      const scale = m.car ? carScale : m.widthPx / (b.x1 - b.x0);
      const outW = Math.ceil((b.x1 - b.x0) * scale);
      const outH = Math.ceil((b.y1 - b.y0) * scale);
      const fw = Math.max(1, Math.ceil(outW / 16));
      const fh = Math.max(1, Math.ceil(outH / 16));
      const frame = downsample(img, b, scale, fw * 16, fh * 16, (b.x0 + b.x1) / 2);
      writeFurniture(m.id, m.label, frame, fw, fh, m.solidRows);
      assets.push({ id: m.id, sourceBox: b, scale: Math.round(scale * 10000) / 10000, drawn: { w: outW, h: outH }, footprint: { w: fw, h: fh, solidRows: m.solidRows }, runtimeDrawn: !!m.car });
    }
  const mirror = mirrorIoU(img, grid[1][3], grid[2][0]);
  return {
    id: s.id,
    file: s.file,
    kind: 'subway kit (static props; cars drawn by the transit runtime with A/B frames)',
    census: before,
    zeroedHiddenRgb: zeroed,
    alphaCutoff: ALPHA_CUTOFF,
    segmentation: { ok: true, method: 'row bands by projection; per row, alpha components merged at the smallest horizontal gap to the requested count (5, 5, 4, 6); no fixed stride', observedPerRow: grid.map((r) => r.length), columnSpans: grid.map((row) => row.map((b) => [b.x0, b.x1 - 1])) },
    facing: {
      passengerCars: 'end-symmetric body: the requested LEFT/RIGHT labels are not observable; used direction-neutral',
      passengerCarMirrorSimilarityIoU: Math.round(mirror * 1000) / 1000,
      leadCars: 'adjudicated by executor1: the streamlined cab nose and headlamp are at the LEFT end of row-3 cell 3 and at the RIGHT end of cell 4, matching the request',
    },
    carScale: Math.round(carScale * 10000) / 10000,
    assets,
    cells: { delivered: 20, extracted: assets.length, alphaVerified: assets.length },
  };
}

const dir = process.argv[2];
if (!dir || !path.isAbsolute(dir)) {
  console.error('usage: npx tsx scripts/prepare-living-world-art.ts <absolute source dir>');
  process.exit(2);
}
const results = SOURCES.map((s) =>
  s.kind === 'retail' ? prepareRetail(dir, s) : s.kind === 'vehicles' ? prepareVehicles(dir, s) : s.kind === 'cathedral' ? prepareCathedral(dir, s) : s.kind === 'exhibits' ? prepareExhibits(dir, s) : s.kind === 'subway' ? prepareSubway(dir, s) : prepareCharacter(dir, s),
);
const report = {
  kind: 'living-world art preparation QA (M3 S6)',
  alphaCutoff: ALPHA_CUTOFF,
  generator: 'scripts/prepare-living-world-art.ts',
  note: 'Counts are per stage: delivered cells, extracted, alpha-verified. INTEGRATED counts are recorded by the runtime evidence, not here.',
  sheets: results,
};
fs.writeFileSync(path.join(ROOT, 'packaging/di/LIVING-WORLD-ART-QA.json'), `${JSON.stringify(report, null, 2)}\n`);
for (const r of results) {
  const f = (r as { facing?: { adjudicatedSourceRowOrder: string[]; mirrorSimilarityIoU: number; corroboration: { skinSignalPerSideRow: Array<{ agrees: boolean }> } } }).facing;
  console.log(`${r.id} seg=${JSON.stringify((r as { segmentation: unknown }).segmentation)}${f?.adjudicatedSourceRowOrder ? ` adjudicated=${f.adjudicatedSourceRowOrder.join(',')} skinAgrees=${f.corroboration.skinSignalPerSideRow.map((x) => x.agrees).join('/')} mirrorSimilarity=${f.mirrorSimilarityIoU}` : ''}`);
}
