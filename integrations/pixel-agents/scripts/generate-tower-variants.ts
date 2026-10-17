/**
 * Skyscraper VARIETY (M3 S2): distinct tower designs composed from the existing r4 tower art.
 *
 *   npx tsx scripts/generate-tower-variants.ts
 *
 * The district reused five tower sprites for 23 instances. Every variant here is a different
 * BUILDING, not a recolour: each one changes height, massing (setback, slender or twin shafts),
 * crown or roofline (another tower's crown, or a drawn crown), and/or window geometry. The
 * compositions reuse sections of the r4 towers: crown, a repeating floor slice and the base.
 * A pairwise silhouette and pixel difference over all the designs is written to the report.
 *
 * Footprints stay compatible with the slot each design fills. Width and the SOLID (blocking) base
 * rows are those of the original tower; only the walk-behind height changes. The world build
 * re-anchors each instance by its bottom edge, so the blocked tiles (and the pedestrian graph) are
 * unchanged.
 *
 * Inputs: the r4 CITY_* furniture art, read only. Outputs: furniture/CITY_TOWER_V*,
 * packaging/di/TOWER-VARIANTS.json. Deterministic.
 */
import fs from 'node:fs';
import path from 'node:path';

import { PNG } from 'pngjs';

const ROOT = path.resolve(__dirname, '..');
const FURN = path.join(ROOT, 'webview-ui/public/assets/furniture');

interface Img {
  w: number;
  h: number;
  d: Buffer;
}
const load = (id: string): Img => {
  const p = PNG.sync.read(fs.readFileSync(path.join(FURN, id, `${id}.png`)));
  return { w: p.width, h: p.height, d: Buffer.from(p.data) };
};
const blank = (w: number, h: number): Img => ({ w, h, d: Buffer.alloc(w * h * 4) });
const px = (im: Img, x: number, y: number): number[] => {
  if (x < 0 || y < 0 || x >= im.w || y >= im.h) return [0, 0, 0, 0];
  const i = (y * im.w + x) * 4;
  return [im.d[i], im.d[i + 1], im.d[i + 2], im.d[i + 3]];
};
const put = (im: Img, x: number, y: number, c: number[]): void => {
  if (x < 0 || y < 0 || x >= im.w || y >= im.h) return;
  const i = (y * im.w + x) * 4;
  im.d[i] = c[0];
  im.d[i + 1] = c[1];
  im.d[i + 2] = c[2];
  im.d[i + 3] = c[3] ?? 255;
};
function rowSpan(im: Img, y: number): [number, number] | null {
  let a = -1;
  let b = -1;
  for (let x = 0; x < im.w; x++)
    if (im.d[(y * im.w + x) * 4 + 3] > 0) {
      if (a < 0) a = x;
      b = x;
    }
  return a < 0 ? null : [a, b];
}

/** Measured bands of each source tower (rows): crown [top, bodyStart), body [bodyStart, baseStart),
 *  base [baseStart, h). Read off the opaque row extents of each sprite. */
interface Bands {
  top: number;
  bodyStart: number;
  baseStart: number;
  solidRows: number;
  fw: number;
  slice?: { at: number; p: number }; // a measured repeat slice, where the automatic one is wrong
}
const BANDS: Record<string, Bands> = {
  CITY_TOWER_ROUND: { top: 10, bodyStart: 30, baseStart: 120, solidRows: 4, fw: 6 },
  CITY_TOWER_GARDEN: { top: 10, bodyStart: 18, baseStart: 130, solidRows: 4, fw: 6 },
  // the automatic period lands on the clock stage and would stack clock faces; repeat the brick
  // window storeys below it instead
  CITY_CLOCK_TOWER: { top: 10, bodyStart: 44, baseStart: 92, solidRows: 3, fw: 4, slice: { at: 72, p: 10 } },
  CITY_SKYSCRAPER_GLASS: { top: 0, bodyStart: 50, baseStart: 160, solidRows: 4, fw: 7 },
  CITY_SKYSCRAPER_DECO: { top: 0, bodyStart: 60, baseStart: 154, solidRows: 4, fw: 7 },
  CITY_SUPERTALL: { top: 0, bodyStart: 50, baseStart: 184, solidRows: 3, fw: 4 },
};

/** The vertical repeat period of a body band (6..20 rows), by least row-to-row difference. */
function period(src: Img, b: Bands): { p: number; at: number } {
  if (b.slice) return b.slice;
  const y0 = b.bodyStart + Math.floor((b.baseStart - b.bodyStart) * 0.35);
  const y1 = b.bodyStart + Math.floor((b.baseStart - b.bodyStart) * 0.75);
  let best = { p: 8, score: Infinity };
  for (let p = 6; p <= 20; p++) {
    let score = 0;
    let n = 0;
    for (let y = y0; y + p < y1; y++)
      for (let x = 0; x < src.w; x++) {
        const a = px(src, x, y);
        const c = px(src, x, y + p);
        score += Math.abs(a[0] - c[0]) + Math.abs(a[1] - c[1]) + Math.abs(a[2] - c[2]) + Math.abs(a[3] - c[3]);
        n++;
      }
    const s = score / Math.max(1, n);
    if (s < best.score * 0.97) best = { p, score: s };
  }
  return { p: best.p, at: y0 };
}

/** Copy rows [y0,y1) of src into dst at row dy, centred on dcx, width-scaled (nearest). */
function blitRows(src: Img, y0: number, y1: number, dst: Img, dy: number, scaleX = 1, dcx = (dst.w - 1) / 2, scaleY = 1): void {
  const cx = (src.w - 1) / 2;
  const outRows = Math.round((y1 - y0) * scaleY);
  for (let oy = 0; oy < outRows; oy++) {
    const sy = y0 + Math.min(y1 - y0 - 1, Math.floor(oy / scaleY));
    for (let x = 0; x < dst.w; x++) {
      const sx = Math.round(cx + (x - dcx) / scaleX);
      const c = px(src, sx, sy);
      if (c[3] > 0) put(dst, x, dy + oy, c);
    }
  }
}

function palette(im: Img) {
  const counts = new Map<string, { n: number; c: number[] }>();
  for (let y = 0; y < im.h; y++)
    for (let x = 0; x < im.w; x++) {
      const c = px(im, x, y);
      if (c[3] < 200) continue;
      const k = `${c[0] >> 3},${c[1] >> 3},${c[2] >> 3}`;
      const e = counts.get(k) ?? { n: 0, c };
      e.n++;
      counts.set(k, e);
    }
  const top = [...counts.values()].sort((a, b) => b.n - a.n).slice(0, 40).map((e) => e.c);
  const lum = (c: number[]) => c[0] * 0.3 + c[1] * 0.59 + c[2] * 0.11;
  return {
    dark: [...top].sort((a, b) => lum(a) - lum(b))[0],
    light: [...top].sort((a, b) => lum(b) - lum(a))[0],
    mid: top[0],
  };
}

interface Shaft {
  from: string; // body source
  floors: number; // extra (or fewer, negative) floor slices
  width?: number; // body width scale
  setback?: number; // fraction of the body (from the top) narrowed to 74%
}
interface Spec {
  id: string;
  name: string;
  slot: string; // the original whose footprint this design fills
  shafts: Array<Shaft & { dx?: number; crown: string }>;
  hybridLower?: { from: string; floors: number }; // a lower body section from another tower
  skybridge?: boolean;
  windows?: 'fins' | 'bands' | 'lit' | 'grid';
  distinct: string; // what makes it a different building, in words
}

/** Materials for drawn roofs, sampled from the art itself so roofs match the facades. */
interface Materials {
  outline: number[];
  stone: number[];
  copper: number[];
  zinc: number[];
  concrete: number[];
}
function dominant(im: Img, y0: number, y1: number, lumMin: number, lumMax: number): number[] {
  const counts = new Map<string, { n: number; c: number[] }>();
  for (let y = y0; y < y1; y++)
    for (let x = 0; x < im.w; x++) {
      const c = px(im, x, y);
      const l = c[0] * 0.3 + c[1] * 0.59 + c[2] * 0.11;
      if (c[3] < 200 || l < lumMin || l > lumMax) continue;
      const k = `${c[0] >> 3},${c[1] >> 3},${c[2] >> 3}`;
      const e = counts.get(k) ?? { n: 0, c };
      e.n++;
      counts.set(k, e);
    }
  return [...counts.values()].sort((p, q) => q.n - p.n)[0]?.c ?? [150, 150, 150, 255];
}
/** Five tones, light to dark, around a base colour (upper-left light convention of the r4 art). */
function ramp(c: number[]): number[][] {
  return [1.32, 1.14, 1, 0.8, 0.62].map((f) => [0, 1, 2].map((i) => Math.max(0, Math.min(255, Math.round(c[i] * f)))).concat(255));
}
const LIGHT = (() => {
  const v = [-0.55, -0.62, 0.56];
  const n = Math.hypot(v[0], v[1], v[2]);
  return v.map((x) => x / n);
})();
function shadeIndex(nx: number, ny: number, nz: number): number {
  const i = nx * LIGHT[0] + ny * LIGHT[1] + nz * LIGHT[2];
  return i > 0.85 ? 0 : i > 0.62 ? 1 : i > 0.38 ? 2 : i > 0.12 ? 3 : 4;
}

/** A closed elliptical roof slab over a cut shaft top (rim, shaded deck, dark outline). Returns its top row. */
function roofSlab(dst: Img, roofY: number, cx: number, half: number, m: Materials, helipad: boolean): number {
  const rx = half;
  const ry = Math.max(3, Math.round(half * 0.32));
  const deck = ramp(helipad ? m.concrete : m.stone);
  const rim = ramp(m.stone);
  for (let y = -ry; y <= ry; y++)
    for (let x = -rx; x <= rx; x++) {
      const d = (x * x) / (rx * rx) + (y * y) / (ry * ry);
      if (d > 1) continue;
      const edge = d > 0.78;
      const outlineRing = (x * x) / ((rx - 1) * (rx - 1)) + (y * y) / ((ry - 1) * (ry - 1)) > 1;
      const tone = outlineRing ? m.outline : edge ? rim[y < 0 ? 0 : 2] : deck[x < -rx * 0.3 ? 1 : x > rx * 0.4 ? 3 : 2];
      put(dst, cx + x, roofY + y, tone);
    }
  if (helipad) {
    const r = Math.max(2, Math.min(6, rx - 4));
    for (let a = 0; a < 64; a++) put(dst, cx + Math.round(Math.cos((a / 64) * Math.PI * 2) * r), roofY + Math.round(Math.sin((a / 64) * Math.PI * 2) * Math.max(1, r * 0.32)), [232, 214, 120, 255]);
    for (let y = -1; y <= 1; y++) {
      put(dst, cx - 2, roofY + y, [240, 240, 236, 255]);
      put(dst, cx + 2, roofY + y, [240, 240, 236, 255]);
    }
    put(dst, cx - 1, roofY, [240, 240, 236, 255]);
    put(dst, cx, roofY, [240, 240, 236, 255]);
    put(dst, cx + 1, roofY, [240, 240, 236, 255]);
  }
  return roofY - ry;
}

/** A shaded dome with a stone rim and a lantern. Base spans the whole shaft top. Returns its top row. */
function dome(dst: Img, roofY: number, cx: number, half: number, m: Materials): number {
  const rx = half;
  const ry = Math.max(4, Math.round(half * 0.72));
  const tones = ramp(m.zinc);
  const rim = ramp(m.stone);
  for (let y = -ry; y <= 0; y++)
    for (let x = -rx; x <= rx; x++) {
      const nx = x / rx;
      const ny = y / ry;
      const d = nx * nx + ny * ny;
      if (d > 1) continue;
      const nz = Math.sqrt(Math.max(0, 1 - d));
      let t = tones[shadeIndex(nx, ny, nz)];
      if (Math.abs(x) % 5 === 2 && d < 0.85) t = tones[Math.min(4, shadeIndex(nx, ny, nz) + 1)]; // ribs
      const outer = (x * x) / ((rx - 1) * (rx - 1)) + (y * y) / ((ry - 1) * (ry - 1)) > 1;
      put(dst, cx + x, roofY - 2 + y, outer ? m.outline : t);
    }
  for (let x = -rx - 1; x <= rx + 1; x++) {
    put(dst, cx + x, roofY - 1, Math.abs(x) > rx ? m.outline : rim[x < 0 ? 0 : 2]);
    put(dst, cx + x, roofY, Math.abs(x) > rx ? m.outline : rim[3]);
    put(dst, cx + x, roofY + 1, m.outline);
  }
  const top = roofY - 2 - ry;
  for (let y = top - 3; y <= top; y++) for (let x = -1; x <= 1; x++) put(dst, cx + x, y, x === -1 ? rim[1] : x === 1 ? rim[3] : rim[2]);
  put(dst, cx, top - 4, m.outline);
  put(dst, cx, top - 5, [214, 176, 84, 255]);
  return top - 5;
}

/** A shaded pyramid roof with eaves: lit left face, shadowed right face, ridge and courses. */
function pyramid(dst: Img, roofY: number, cx: number, half: number, m: Materials, steep = 1.1): number {
  const h = Math.max(4, Math.round(half * steep));
  const t = ramp(m.copper);
  for (let x = -half - 1; x <= half + 1; x++) {
    put(dst, cx + x, roofY, m.outline);
    put(dst, cx + x, roofY - 1, Math.abs(x) > half ? m.outline : t[3]);
  }
  for (let y = 0; y < h; y++) {
    const w = Math.round(half * (1 - y / h));
    for (let x = -w; x <= w; x++) {
      const edge = Math.abs(x) === w;
      let tone = x < 0 ? t[1] : x > 0 ? t[3] : t[0];
      if (y % 3 === 2 && !edge) tone = x < 0 ? t[2] : t[4];
      put(dst, cx + x, roofY - 2 - y, edge ? m.outline : tone);
    }
  }
  put(dst, cx, roofY - 2 - h, m.outline);
  put(dst, cx, roofY - 3 - h, [214, 176, 84, 255]);
  return roofY - 3 - h;
}

/** A shaded crenellated parapet closing a cut top. */
function crenellated(dst: Img, roofY: number, cx: number, half: number, m: Materials): number {
  const t = ramp(m.stone);
  for (let x = -half; x <= half; x++) {
    const k = Math.floor((x + half) / 3);
    const merlon = k % 2 === 0;
    const top = roofY - (merlon ? 6 : 3);
    for (let y = top; y <= roofY + 1; y++) {
      const edge = x === -half || x === half || y === top || y === roofY + 1;
      const lit = (x + half) % 3 === 0;
      const shadow = (x + half) % 3 === 2;
      put(dst, cx + x, y, edge ? m.outline : y === top + 1 ? t[0] : lit ? t[1] : shadow ? t[3] : t[2]);
    }
  }
  return roofY - 6;
}

/** A finished-roof topper: a lattice mast or a needle spire rising from a closed roof. */
function topper(dst: Img, kind: 'mast' | 'spire', topY: number, cx: number, m: Materials): void {
  const t = ramp(m.stone);
  if (kind === 'spire') {
    const h = 30;
    for (let y = 0; y < h; y++) {
      const w = Math.max(0, Math.round(((h - y) / h) * 3));
      for (let x = -w; x <= w; x++) put(dst, cx + x, topY - y, Math.abs(x) === w && w > 0 ? m.outline : x < 0 ? t[0] : x > 0 ? t[3] : t[1]);
    }
    put(dst, cx, topY - h, m.outline);
    return;
  }
  for (let y = 0; y < 22; y++) {
    put(dst, cx, topY - y, m.outline);
    put(dst, cx - 1, topY - y, y % 4 === 0 ? m.outline : t[1]);
  }
  for (const yy of [topY - 7, topY - 14]) for (let x = -3; x <= 3; x++) put(dst, cx + x, yy, m.outline);
  put(dst, cx, topY - 22, [220, 60, 50, 255]);
}

function treatWindows(dst: Img, y0: number, y1: number, kind: Spec['windows'], pal: ReturnType<typeof palette>): void {
  if (!kind) return;
  const isGlass = (c: number[]) => c[3] > 0 && c[2] > c[0] + 25 && c[2] > 110;
  for (let y = y0; y < y1; y++)
    for (let x = 0; x < dst.w; x++) {
      const c = px(dst, x, y);
      if (!isGlass(c)) continue;
      if (kind === 'fins' && x % 4 === 0) put(dst, x, y, pal.dark);
      else if (kind === 'bands' && Math.floor((y - y0) / 3) % 3 === 2) put(dst, x, y, pal.mid);
      else if (kind === 'lit' && (Math.floor(x / 3) * 7 + Math.floor((y - y0) / 4) * 3) % 5 === 0) put(dst, x, y, [240, 206, 120, 255]);
      else if (kind === 'grid' && (x % 3 === 0 || (y - y0) % 4 === 0)) put(dst, x, y, pal.dark);
    }
}

/** Rows of a shaft (top→bottom), as source row indices, with `floors` extra repeat slices. */
function shaftRows(src: Img, b: Bands, floors: number): number[] {
  const { p, at } = period(src, b);
  const rows: number[] = [];
  for (let y = b.bodyStart; y < at; y++) rows.push(y);
  if (floors >= 0) {
    for (let k = 0; k < floors; k++) for (let y = at; y < at + p; y++) rows.push(y);
    for (let y = at; y < b.baseStart; y++) rows.push(y);
  } else {
    for (let y = at + -floors * p; y < b.baseStart; y++) rows.push(y);
  }
  return rows;
}

function build(spec: Spec, src: Record<string, Img>): Img {
  const slot = BANDS[spec.slot];
  const base = src[spec.slot];
  const baseRows = base.h - slot.baseStart;
  const W = base.w;
  const CROWN_ROOM = 64;
  const plans = spec.shafts.map((s) => {
    const b = BANDS[s.from];
    let rows = shaftRows(src[s.from], b, s.floors).map((y) => ({ from: s.from, y }));
    if (spec.hybridLower) {
      const hl = spec.hybridLower;
      const lower = shaftRows(src[hl.from], BANDS[hl.from], hl.floors).map((y) => ({ from: hl.from, y }));
      rows = [...rows.slice(0, Math.floor(rows.length * 0.55)), ...lower.slice(Math.floor(lower.length * 0.45))];
    }
    return { s, rows };
  });
  const bodyH = Math.max(...plans.map((p) => p.rows.length));
  const H = CROWN_ROOM + bodyH + baseRows;
  const dst = blank(W, Math.ceil(H / 16) * 16);
  const pad = dst.h - H;
  const bodyTop = pad + CROWN_ROOM;
  const pal = palette(base);
  const roofs: Array<{ cx: number; roofY: number; topWidth: number; crown: string }> = [];
  for (const { s, rows } of plans) {
    const cx = (W - 1) / 2 + (s.dx ?? 0);
    const widthScale = s.width ?? 1;
    const start = bodyTop + (bodyH - rows.length); // shorter shafts start lower (bottom aligned)
    rows.forEach((r, i) => {
      const t = i / rows.length;
      const scale = widthScale * (s.setback && t < s.setback ? 0.74 : 1);
      blitRows(src[r.from], r.y, r.y + 1, dst, start + i, scale, cx);
    });
    const top = rowSpan(src[rows[0].from], rows[0].y + 1);
    const topScale = widthScale * (s.setback ? 0.74 : 1);
    roofs.push({ cx: Math.round(cx), roofY: start, topWidth: top ? Math.round((top[1] - top[0] + 1) * topScale) : 20, crown: s.crown });
  }
  if (spec.skybridge && roofs.length === 2) {
    const y0 = bodyTop + Math.floor(bodyH * 0.38);
    const a = Math.min(roofs[0].cx, roofs[1].cx);
    const c = Math.max(roofs[0].cx, roofs[1].cx);
    for (let y = y0; y < y0 + 7; y++)
      for (let x = a; x <= c; x++) {
        if (y === y0 || y === y0 + 6) put(dst, x, y, pal.dark);
        else if (y === y0 + 3 && x % 3 !== 0) put(dst, x, y, [150, 190, 220, 255]);
        else put(dst, x, y, pal.light);
      }
  }
  blitRows(base, slot.baseStart, base.h, dst, bodyTop + bodyH);
  treatWindows(dst, bodyTop, bodyTop + bodyH, spec.windows, pal);
  const m: Materials = {
    outline: pal.dark,
    stone: dominant(base, slot.baseStart, base.h, 130, 215),
    copper: dominant(src.CITY_CLOCK_TOWER, 12, 40, 60, 200),
    zinc: [132, 146, 162, 255],
    concrete: [150, 150, 144, 255],
  };
  const pasteCrown = (from: string, r: (typeof roofs)[number]): number => {
    const cs = src[from];
    const cb = BANDS[from];
    const baseSpan = rowSpan(cs, cb.bodyStart - 1);
    const crownBaseW = baseSpan ? baseSpan[1] - baseSpan[0] + 1 : cs.w;
    const k = Math.min(1.25, r.topWidth / crownBaseW);
    const ch = Math.round((cb.bodyStart - cb.top) * k);
    blitRows(cs, cb.top, cb.bodyStart, dst, r.roofY - ch + 1, k, r.cx, k);
    let top = r.roofY - ch + 1;
    while (top < r.roofY && !(px(dst, r.cx, top)[3] > 0)) top++;
    return top;
  };
  for (const r of roofs) {
    const half = Math.max(3, Math.floor(r.topWidth / 2));
    const own = spec.shafts.find((s) => s.crown === r.crown)?.from ?? spec.slot;
    if (r.crown in BANDS) pasteCrown(r.crown, r);
    else if (r.crown === 'dome') dome(dst, r.roofY + 1, r.cx, half, m);
    else if (r.crown === 'pyramid') pyramid(dst, r.roofY + 1, r.cx, half, m);
    else if (r.crown === 'helipad') roofSlab(dst, r.roofY + 1, r.cx, half, m, true);
    else if (r.crown === 'crenellated') crenellated(dst, r.roofY + 1, r.cx, half, m);
    else {
      // mast or spire: close the roof first (the shaft's own cap; a low pyramid on the brick belfry)
      const top = own === 'CITY_CLOCK_TOWER' ? pyramid(dst, r.roofY + 1, r.cx, half, m, 0.55) : pasteCrown(own, r);
      topper(dst, r.crown as 'mast' | 'spire', top, r.cx, m);
    }
  }
  let first = 0;
  while (first < dst.h && !rowSpan(dst, first)) first++;
  const keepFrom = Math.floor(first / 16) * 16;
  const out = blank(dst.w, dst.h - keepFrom);
  dst.d.copy(out.d, 0, keepFrom * dst.w * 4);
  return out;
}

/** Bottom-aligned silhouette and pixel difference between two designs (fractions of the union). */
function difference(a: Img, b: Img) {
  const w = Math.max(a.w, b.w);
  const h = Math.max(a.h, b.h);
  let union = 0;
  let silDiff = 0;
  let pixDiff = 0;
  for (let y = 0; y < h; y++)
    for (let x = 0; x < w; x++) {
      const ca = px(a, x - Math.floor((w - a.w) / 2), y - (h - a.h));
      const cb = px(b, x - Math.floor((w - b.w) / 2), y - (h - b.h));
      const oa = ca[3] > 0;
      const ob = cb[3] > 0;
      if (oa || ob) union++;
      if (oa !== ob) silDiff++;
      else if (oa && ob && Math.abs(ca[0] - cb[0]) + Math.abs(ca[1] - cb[1]) + Math.abs(ca[2] - cb[2]) > 60) pixDiff++;
    }
  return { silhouette: silDiff / union, pixels: pixDiff / union, heightDelta: Math.abs(a.h - b.h) };
}

const R = 'CITY_TOWER_ROUND';
const G = 'CITY_TOWER_GARDEN';
const C = 'CITY_CLOCK_TOWER';
const D = 'CITY_SKYSCRAPER_DECO';
const GL = 'CITY_SKYSCRAPER_GLASS';
const SPECS: Spec[] = [
  { id: 'CITY_TOWER_V01', name: 'Stepped Glass Drum', slot: R, shafts: [{ from: R, floors: 1, setback: 0.4, crown: R }], windows: 'bands', distinct: 'setback upper drum, banded glazing' },
  { id: 'CITY_TOWER_V02', name: 'Mast Cylinder', slot: R, shafts: [{ from: R, floors: 6, crown: 'mast' }], windows: 'grid', distinct: 'six floors taller, flat roof with a radio mast, gridded windows' },
  { id: 'CITY_TOWER_V03', name: 'Twin Drums', slot: R, shafts: [{ from: R, floors: 4, width: 0.46, dx: -24, crown: 'dome' }, { from: R, floors: 1, width: 0.46, dx: 24, crown: 'helipad' }], skybridge: true, distinct: 'two slender shafts of unequal height joined by a skybridge' },
  { id: 'CITY_TOWER_V04', name: 'Slender Glass-Crown Drum', slot: R, shafts: [{ from: R, floors: 4, width: 0.72, crown: GL }], windows: 'fins', distinct: "slender drum with the glass tower's pointed crown, vertical fins" },
  { id: 'CITY_TOWER_V05', name: 'Domed Rotunda', slot: R, shafts: [{ from: R, floors: -4, crown: 'dome' }], windows: 'lit', distinct: 'short wide rotunda under a dome, lit windows' },
  { id: 'CITY_TOWER_V06', name: 'Garden-Base Glass Spire', slot: G, shafts: [{ from: R, floors: 3, width: 0.8, crown: D }], hybridLower: { from: G, floors: 0 }, distinct: 'terraced garden podium under a glass drum with a gold deco spire' },
  { id: 'CITY_TOWER_V07', name: 'Tall Terrace', slot: G, shafts: [{ from: G, floors: 4, crown: G }], windows: 'lit', distinct: 'four terraces taller, lit windows' },
  { id: 'CITY_TOWER_V08', name: 'Terrace Spire', slot: G, shafts: [{ from: G, floors: 3, setback: 0.5, crown: 'spire' }], distinct: 'three terraces taller, half the height narrowed, needle spire' },
  { id: 'CITY_TOWER_V09', name: 'Garden Block', slot: G, shafts: [{ from: G, floors: -3, crown: 'helipad' }], windows: 'grid', distinct: 'low flat-roofed terrace block with a helipad' },
  { id: 'CITY_TOWER_V10', name: 'Pyramid Terrace', slot: G, shafts: [{ from: G, floors: 2, crown: 'pyramid' }], windows: 'fins', distinct: 'copper pyramid roof over finned terraces' },
  { id: 'CITY_TOWER_V11', name: 'Stepped Arcology', slot: G, shafts: [{ from: G, floors: 7, setback: 0.45, crown: G }], windows: 'bands', distinct: 'tallest terrace tower with a deep setback' },
  { id: 'CITY_TOWER_V12', name: 'Tall Belfry', slot: C, shafts: [{ from: C, floors: 3, crown: C }], distinct: 'three storeys taller belfry shaft' },
  { id: 'CITY_TOWER_V13', name: 'Brick Keep', slot: C, shafts: [{ from: C, floors: -2, crown: 'crenellated' }], distinct: 'two storeys shorter, crenellated flat top instead of the spire' },
  { id: 'CITY_TOWER_V14', name: 'Twin Belfries', slot: C, shafts: [{ from: C, floors: 3, width: 0.55, dx: -13, crown: 'pyramid' }, { from: C, floors: 1, width: 0.55, dx: 13, crown: 'pyramid' }], skybridge: true, distinct: 'two slender belfries of unequal height under copper pyramids, bridged' },
  { id: 'CITY_TOWER_V15', name: 'Domed Observatory Tower', slot: C, shafts: [{ from: C, floors: 7, setback: 0.5, crown: 'dome' }], distinct: 'seven storeys taller with a deep setback upper stage under a dome' },
  { id: 'CITY_TOWER_V16', name: 'Signal Mast Tower', slot: C, shafts: [{ from: C, floors: 0, width: 0.8, crown: 'mast' }], distinct: 'slimmer shaft with a lattice signal mast' },
  { id: 'CITY_TOWER_V17', name: 'Stepped Deco Crown', slot: D, shafts: [{ from: D, floors: -2, crown: 'crenellated' }], windows: 'lit', distinct: 'shorter deco tower with a stepped flat crown and lit windows' },
];

const sources: Record<string, Img> = {};
for (const id of Object.keys(BANDS)) sources[id] = load(id);
const designs: Record<string, Img> = {};
for (const id of Object.keys(BANDS)) designs[id] = sources[id];
const report: Array<Record<string, unknown>> = [];
for (const spec of SPECS) {
  const im = build(spec, sources);
  designs[spec.id] = im;
  const fh = im.h / 16;
  const slot = BANDS[spec.slot];
  const dir = path.join(FURN, spec.id);
  fs.mkdirSync(dir, { recursive: true });
  const png = new PNG({ width: im.w, height: im.h });
  im.d.copy(png.data);
  fs.writeFileSync(path.join(dir, `${spec.id}.png`), PNG.sync.write(png));
  const manifest = { id: spec.id, name: spec.name, category: 'decor', type: 'asset', canPlaceOnWalls: false, canPlaceOnSurfaces: false, backgroundTiles: fh - slot.solidRows, width: im.w, height: im.h, footprintW: slot.fw, footprintH: fh };
  fs.writeFileSync(path.join(dir, 'manifest.json'), `${JSON.stringify(manifest, null, 2)}\n`);
  report.push({ id: spec.id, name: spec.name, slot: spec.slot, distinct: spec.distinct, composition: { shafts: spec.shafts, hybridLower: spec.hybridLower ?? null, skybridge: !!spec.skybridge, windows: spec.windows ?? 'original' }, size: { w: im.w, h: im.h }, footprint: { w: slot.fw, h: fh, solidRows: slot.solidRows, backgroundTiles: fh - slot.solidRows } });
}
const periods = Object.fromEntries(Object.keys(BANDS).map((id) => [id, period(sources[id], BANDS[id])]));
const ids = Object.keys(designs);
const pairs: Array<{ a: string; b: string; silhouette: number; pixels: number; heightDelta: number }> = [];
for (let i = 0; i < ids.length; i++)
  for (let j = i + 1; j < ids.length; j++) {
    const d = difference(designs[ids[i]], designs[ids[j]]);
    pairs.push({ a: ids[i], b: ids[j], silhouette: Math.round(d.silhouette * 1000) / 1000, pixels: Math.round(d.pixels * 1000) / 1000, heightDelta: d.heightDelta });
  }
const WEAK = (p: { silhouette: number; pixels: number }) => p.silhouette < 0.08 && p.pixels < 0.15;
const weakest = [...pairs].sort((p, q) => p.silhouette + p.pixels - (q.silhouette + q.pixels)).slice(0, 10);
const out = {
  kind: 'skyscraper design variants (M3 S2)',
  generator: 'scripts/generate-tower-variants.ts',
  sourceBands: BANDS,
  measuredFloorPeriods: periods,
  variants: report,
  designs: ids,
  distinctnessRule: 'pairwise over all designs: weak when silhouette difference < 0.08 AND pixel difference < 0.15 (bottom-aligned, fractions of the union). Palette-only changes are not used.',
  weakPairs: pairs.filter(WEAK),
  weakestPairs: weakest,
};
fs.writeFileSync(path.join(ROOT, 'packaging/di/TOWER-VARIANTS.json'), `${JSON.stringify(out, null, 2)}\n`);
console.log(JSON.stringify({ variants: report.length, periods, weak: out.weakPairs.length, weakest: weakest.slice(0, 6) }));
