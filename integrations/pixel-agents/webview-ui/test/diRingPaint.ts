/**
 * RING PAINT: a narrow, test-side measurement of whether a drawn perimeter is actually PAINTED at an expected box boundary. It exists for ONE job: the Mission details Back
 * box is measured as its outer LAYOUT RECTANGLE (diScanHeaderGeometry.test.ts), and a layout rectangle is satisfied by a ring of zero contrast; this keeps the property the old
 * ink rule had, that SOMETHING IS ACTUALLY DRAWN there, without a threshold that a faint, lock-faithful ring cannot meet.
 *
 * WHAT IT MEASURES. For each of the four sides of the box, over the STRAIGHT part of the side only (CORNER_PX are left out at each end, so the rounded corners are not evidence),
 * the per-channel MEDIAN along the side of the pixels at each offset from the boundary line (offset 0 is the outermost pixel of the box, positive inward, negative outward).
 * The ADJACENT BACKGROUND is the median of the profile at offsets -5, -4 and -3 (just outside the box, past a 2 px guard); the bright chevron in the middle of the box is never
 * sampled, because nothing deeper than 3 px inside the boundary is read. A side's PEAK is the largest channel difference, over offsets -1 to +3, between the profile and the
 * adjacent background: how far the ring stands off the background right at the boundary.
 *
 * WHAT IT IS NOT. It is not a contrast floor, not a perceptual metric and not a 3:1 rule for a decorative edge. The minimum a side must reach is NOT chosen here: it is
 * derived from the REFERENCE (half of the weakest side of the locked reference, measured by this same code at the reference's native size) and recorded in the fixture before any
 * candidate result is used; a negative control (the ring pixels overwritten by the adjacent background, box and chevron unchanged) must FAIL it. A nonzero difference alone does
 * not establish perceptual equivalence.
 */
export interface Pixels {
  width: number;
  height: number;
  data: Uint8Array | Uint8ClampedArray;
}
export interface BoxPx {
  x: number;
  y: number;
  w: number;
  h: number;
}
export const SIDES = ['top', 'right', 'bottom', 'left'] as const;
export type Side = (typeof SIDES)[number];
export type Rgb = [number, number, number];
export interface SidePeak {
  /** the largest channel difference, over the ring offsets, between the profile and the adjacent background */
  peak: number;
  /** the offset (positive inward) at which it occurs */
  at: number;
  ring: Rgb;
  adjacent: Rgb;
}

/** pixels left out at each end of a side: the corner radius (about 6 px, DERIVED from the crop, not measured) plus 6 */
export const CORNER_PX = 12;
/** the adjacent background: 3 to 5 px OUTSIDE the boundary (the two nearest outside pixels are skipped: a soft ring or a halo may reach them) */
export const ADJACENT_OFFSETS = [-5, -4, -3] as const;
/** the ring is searched from 1 px outside to 3 px inside the boundary line */
export const RING_OFFSETS = [-1, 0, 1, 2, 3] as const;
/** the fraction of the reference's weakest side that every side must reach (stated before any candidate result is read) */
export const PAINT_FRACTION = 0.5;

interface Edges {
  left: number;
  right: number;
  top: number;
  bottom: number;
}
const edgesOf = (box: BoxPx): Edges => ({
  left: Math.round(box.x),
  top: Math.round(box.y),
  right: Math.round(box.x + box.w) - 1,
  bottom: Math.round(box.y + box.h) - 1,
});

/** the pixel at offset `k` (positive inward) from the boundary of `side`, at position `t` along the side */
function at(side: Side, e: Edges, k: number, t: number): [number, number] {
  if (side === 'top') return [t, e.top + k];
  if (side === 'bottom') return [t, e.bottom - k];
  if (side === 'left') return [e.left + k, t];
  return [e.right - k, t];
}
/** the positions along a side: the straight part (corners left out) or, for the control, the whole side */
function along(side: Side, e: Edges, straight: boolean): number[] {
  const horizontal = side === 'top' || side === 'bottom';
  const a = horizontal ? e.left : e.top;
  const b = horizontal ? e.right : e.bottom;
  const lo = straight ? a + CORNER_PX : a;
  const hi = straight ? b - CORNER_PX : b;
  const out: number[] = [];
  for (let t = lo; t <= hi; t++) out.push(t);
  return out;
}

function median(values: number[]): number {
  const v = [...values].sort((p, q) => p - q);
  const m = v.length >> 1;
  return v.length === 0 ? NaN : v.length % 2 === 1 ? v[m]! : (v[m - 1]! + v[m]!) / 2;
}
const px = (img: Pixels, x: number, y: number): Rgb | null => {
  if (x < 0 || y < 0 || x >= img.width || y >= img.height) return null;
  const i = (y * img.width + x) * 4;
  return [img.data[i]!, img.data[i + 1]!, img.data[i + 2]!];
};
/** the per-channel median, along the straight part of a side, of the pixels at offset `k` */
function profile(img: Pixels, e: Edges, side: Side, k: number): Rgb {
  const ch: [number[], number[], number[]] = [[], [], []];
  for (const t of along(side, e, true)) {
    const [x, y] = at(side, e, k, t);
    const p = px(img, x, y);
    if (p === null) continue;
    ch[0].push(p[0]);
    ch[1].push(p[1]);
    ch[2].push(p[2]);
  }
  return [median(ch[0]), median(ch[1]), median(ch[2])];
}
const maxDiff = (a: Rgb, b: Rgb): number => Math.max(Math.abs(a[0] - b[0]), Math.abs(a[1] - b[1]), Math.abs(a[2] - b[2]));

function adjacentOf(img: Pixels, e: Edges, side: Side): Rgb {
  const rows = ADJACENT_OFFSETS.map((k) => profile(img, e, side, k));
  return [median(rows.map((r) => r[0])), median(rows.map((r) => r[1])), median(rows.map((r) => r[2]))];
}

/** each side's peak: how far the ring stands off the adjacent background at the boundary */
export function ringPeaks(img: Pixels, box: BoxPx): Record<Side, SidePeak> {
  const e = edgesOf(box);
  const out = {} as Record<Side, SidePeak>;
  for (const side of SIDES) {
    const adjacent = adjacentOf(img, e, side);
    let best: SidePeak = { peak: -1, at: 0, ring: adjacent, adjacent };
    for (const k of RING_OFFSETS) {
      const p = profile(img, e, side, k);
      const d = maxDiff(p, adjacent);
      if (d > best.peak) best = { peak: d, at: k, ring: p, adjacent };
    }
    out[side] = best;
  }
  return out;
}

/** THE NEGATIVE CONTROL: a copy in which every pixel of the ring band (offsets -1 to +3, the WHOLE length of each side) is overwritten with that side's adjacent background; the box and everything inside it deeper than 3 px (the chevron) are unchanged */
export function withRingRemoved(img: Pixels, box: BoxPx): Pixels {
  const e = edgesOf(box);
  const data = new Uint8Array(img.data);
  for (const side of SIDES) {
    const adjacent = adjacentOf(img, e, side);
    for (const k of RING_OFFSETS)
      for (const t of along(side, e, false)) {
        const [x, y] = at(side, e, k, t);
        if (x < 0 || y < 0 || x >= img.width || y >= img.height) continue;
        const i = (y * img.width + x) * 4;
        data[i] = adjacent[0];
        data[i + 1] = adjacent[1];
        data[i + 2] = adjacent[2];
      }
  }
  return { width: img.width, height: img.height, data };
}

/** the sides whose peak is below `min` (an empty list means every side is painted) */
export const sidesBelow = (peaks: Record<Side, SidePeak>, min: number): Side[] => SIDES.filter((s) => peaks[s].peak < min);

/**
 * The rule, from the REFERENCE: every side must reach PAINT_FRACTION of the reference's weakest side. It is usable only if that minimum is above what the same code reads on the
 * reference with its ring removed (`distinguishes`); if it is not, the check cannot tell the reference from the negative control and that concrete limitation is reported.
 */
export function ruleFromReference(reference: Record<Side, SidePeak>, removed: Record<Side, SidePeak>): { weakest: number; strongestRemoved: number; minPeak: number; distinguishes: boolean } {
  const weakest = Math.min(...SIDES.map((s) => reference[s].peak));
  const strongestRemoved = Math.max(...SIDES.map((s) => removed[s].peak));
  const minPeak = Math.floor(PAINT_FRACTION * weakest);
  return { weakest, strongestRemoved, minPeak, distinguishes: weakest > strongestRemoved && minPeak > strongestRemoved };
}
