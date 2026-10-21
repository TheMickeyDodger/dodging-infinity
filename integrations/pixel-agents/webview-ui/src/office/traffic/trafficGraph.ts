/**
 * THE ROAD GRAPH (living city 6–9, traffic): the lanes decorative traffic may use, DERIVED from the world's
 * own tiles and never stored as new layout fields. DOM-free and pure: tiles in, lanes out.
 *
 * What a road is here. The metropolis lays its streets as 3-tile-wide BANDS: asphalt, a dashed centre line
 * (FLOOR_16 east-west, FLOOR_17 north-south), asphalt, with zebra crossings (FLOOR_27) across them. A band is
 * found from its dashed centre and kept only where all three tiles of every cross-section are road. Right-hand
 * traffic: the lane on the far side of an east-west band's centre runs west, the near side east; a north-south
 * band runs south on the west side and north on the east side.
 *
 * - INTERSECTIONS are 3x3 boxes where an east-west and a north-south band cross, with all nine tiles road and
 *   unblocked. ZEBRA tiles are the crossings. Both are controlled ZONES: a vehicle stops before one and enters
 *   only after claiming the whole of it (see trafficWorld.ts).
 * - A tile blocked by furniture (a parked prop standing in the street) is not road to a vehicle: a lane is CUT
 *   there into separate free SEGMENTS, and an intersection with any blocked tile is not an intersection.
 * - Everything is BOUNDED: at most TRAFFIC_MAX_LANES lane segments and TRAFFIC_MAX_BOXES intersections, in a
 *   fixed order, so a world can never make the graph grow without limit.
 */
import { TRAFFIC_MAX_BOXES, TRAFFIC_MAX_LANES } from '../../constants.js';
import { TileType } from '../types.js';

/** Headings, clockwise on screen (y grows downward): a right turn is +1, a left turn is +3. */
export const HEADING_E = 0;
export const HEADING_S = 1;
export const HEADING_W = 2;
export const HEADING_N = 3;
export const HEADING_DX: readonly number[] = [1, 0, -1, 0];
export const HEADING_DY: readonly number[] = [0, 1, 0, -1];

export const KIND_ROAD = 0;
export const KIND_ZEBRA = 1;
export const KIND_BOX = 2;

/** One free lane SEGMENT: tiles in travel order. */
export interface Lane {
  readonly id: number;
  readonly heading: number;
  /** Tile indices (row * cols + col), in the direction of travel. */
  readonly tiles: Int32Array;
  /** Per tile: KIND_ROAD, KIND_ZEBRA or KIND_BOX. */
  readonly kind: Uint8Array;
}

export interface Band {
  readonly horizontal: boolean;
  /** The centre row (east-west) or column (north-south). */
  readonly centre: number;
  readonly lo: number;
  readonly hi: number;
}

export interface Box {
  readonly cx: number;
  readonly cy: number;
}

export interface TrafficGraph {
  readonly cols: number;
  readonly rows: number;
  readonly lanes: readonly Lane[];
  readonly bands: readonly Band[];
  readonly boxes: readonly Box[];
  /** Per tile: 1 when it is inside a valid intersection box. */
  readonly boxTiles: Uint8Array;
  /** The lane and index holding `tile` for travel in `heading`, or -1. Decode with laneOf / indexOf. */
  laneAt(tile: number, heading: number): number;
}

/** A lane tile is coded lane * LANE_STRIDE + index; lane lengths are bounded by the layout dimension (<= 384), so the index always fits. */
export const LANE_STRIDE = 1024;
export const laneOf = (code: number): number => Math.floor(code / LANE_STRIDE);
export const indexOf = (code: number): number => code % LANE_STRIDE;

const isRoadTile = (t: number): boolean =>
  t === TileType.FLOOR_15 ||
  t === TileType.FLOOR_16 ||
  t === TileType.FLOOR_17 ||
  t === TileType.FLOOR_27;

export function buildTrafficGraph(
  cols: number,
  rows: number,
  tiles: readonly number[],
  isBlocked: (tile: number) => boolean,
): TrafficGraph {
  const typeAt = (c: number, r: number): number =>
    c < 0 || r < 0 || c >= cols || r >= rows ? -1 : (tiles[r * cols + c] ?? -1);
  const road = (c: number, r: number): boolean => isRoadTile(typeAt(c, r));

  function findBands(horizontal: boolean): Band[] {
    const major = horizontal ? rows : cols;
    const minor = horizontal ? cols : rows;
    const centreType = horizontal ? TileType.FLOOR_16 : TileType.FLOOR_17;
    const tileAt = (a: number, b: number): number => (horizontal ? typeAt(b, a) : typeAt(a, b));
    const roadAt = (a: number, b: number): boolean => isRoadTile(tileAt(a, b));
    const crossOk = (a: number, b: number): boolean =>
      roadAt(a - 1, b) && roadAt(a, b) && roadAt(a + 1, b);
    // runs of centre-like tiles (dashed, or zebra) with road either side, that contain a real dashed tile
    const runs: Array<[number, number, number]> = [];
    for (let a = 0; a < major; a++) {
      let start = -1;
      let sawCentre = false;
      for (let b = 0; b <= minor; b++) {
        let like = false;
        if (b < minor) {
          const t = tileAt(a, b);
          like =
            (t === centreType || t === TileType.FLOOR_27) && roadAt(a - 1, b) && roadAt(a + 1, b);
        }
        if (like) {
          if (start < 0) {
            start = b;
            sawCentre = false;
          }
          if (tileAt(a, b) === centreType) sawCentre = true;
        } else if (start >= 0) {
          if (b - start >= 3 && sawCentre) runs.push([a, start, b - 1]);
          start = -1;
        }
      }
    }
    // join runs across a short gap (an intersection) when the whole 3-wide cross-section there is road
    const merged: Array<[number, number, number]> = [];
    for (const [a, lo, hi] of runs) {
      const last = merged[merged.length - 1];
      if (last && last[0] === a && lo - last[2] - 1 <= 3) {
        let ok = true;
        for (let b = last[2] + 1; b < lo; b++) {
          if (!crossOk(a, b)) {
            ok = false;
            break;
          }
        }
        if (ok) {
          last[2] = hi;
          continue;
        }
      }
      merged.push([a, lo, hi]);
    }
    // keep only the maximal stretches whose every cross-section is road
    const out: Band[] = [];
    for (const [a, lo, hi] of merged) {
      let s = -1;
      for (let b = lo; b <= hi + 1; b++) {
        const ok = b <= hi && crossOk(a, b);
        if (ok && s < 0) s = b;
        else if (!ok && s >= 0) {
          if (b - s >= 3) out.push({ horizontal, centre: a, lo: s, hi: b - 1 });
          s = -1;
        }
      }
    }
    return out;
  }

  const bands = [...findBands(true), ...findBands(false)];
  const hBands = bands.filter((b) => b.horizontal);
  const vBands = bands.filter((b) => !b.horizontal);

  // intersections: a valid box needs both bands to hold it and all nine tiles to be road and unblocked
  const boxes: Box[] = [];
  const boxTiles = new Uint8Array(cols * rows);
  const dead = new Set<number>(); // tiles a lane may not use (blocked, or part of an unusable box)
  for (const h of hBands) {
    for (const v of vBands) {
      const cx = v.centre;
      const cy = h.centre;
      if (cx - 1 < h.lo || cx + 1 > h.hi || cy - 1 < v.lo || cy + 1 > v.hi) continue;
      let allRoad = true;
      let anyBlocked = false;
      for (let dy = -1; dy <= 1; dy++) {
        for (let dx = -1; dx <= 1; dx++) {
          if (!road(cx + dx, cy + dy)) allRoad = false;
          else if (isBlocked((cy + dy) * cols + (cx + dx))) anyBlocked = true;
        }
      }
      if (!allRoad) continue;
      if (anyBlocked || boxes.length >= TRAFFIC_MAX_BOXES) {
        for (let dy = -1; dy <= 1; dy++)
          for (let dx = -1; dx <= 1; dx++) dead.add((cy + dy) * cols + (cx + dx));
        continue;
      }
      boxes.push({ cx, cy });
      for (let dy = -1; dy <= 1; dy++)
        for (let dx = -1; dx <= 1; dx++) boxTiles[(cy + dy) * cols + (cx + dx)] = 1;
    }
  }

  // raw lanes, then cut at dead tiles into free segments
  const raw: Array<{ heading: number; tiles: number[] }> = [];
  for (const h of hBands) {
    const east: number[] = [];
    const west: number[] = [];
    for (let c = h.lo; c <= h.hi; c++) east.push((h.centre + 1) * cols + c);
    for (let c = h.hi; c >= h.lo; c--) west.push((h.centre - 1) * cols + c);
    raw.push({ heading: HEADING_E, tiles: east }, { heading: HEADING_W, tiles: west });
  }
  for (const v of vBands) {
    const south: number[] = [];
    const north: number[] = [];
    for (let r = v.lo; r <= v.hi; r++) south.push(r * cols + (v.centre - 1));
    for (let r = v.hi; r >= v.lo; r--) north.push(r * cols + (v.centre + 1));
    raw.push({ heading: HEADING_S, tiles: south }, { heading: HEADING_N, tiles: north });
  }

  const lanes: Lane[] = [];
  const at = new Map<number, number>();
  const kindOf = (tile: number): number =>
    boxTiles[tile] ? KIND_BOX : tiles[tile] === TileType.FLOOR_27 ? KIND_ZEBRA : KIND_ROAD;
  const addSegment = (heading: number, seg: number[]): void => {
    if (seg.length < 2 || lanes.length >= TRAFFIC_MAX_LANES) return;
    const id = lanes.length;
    const laneTiles = Int32Array.from(seg);
    const kind = new Uint8Array(seg.length);
    for (let i = 0; i < seg.length; i++) {
      kind[i] = kindOf(seg[i]);
      at.set(seg[i] * 4 + heading, id * LANE_STRIDE + i);
    }
    lanes.push({ id, heading, tiles: laneTiles, kind });
  };
  for (const lane of raw) {
    let seg: number[] = [];
    for (const tile of lane.tiles) {
      if (isBlocked(tile) || dead.has(tile)) {
        addSegment(lane.heading, seg);
        seg = [];
      } else seg.push(tile);
    }
    addSegment(lane.heading, seg);
  }

  return {
    cols,
    rows,
    lanes,
    bands,
    boxes,
    boxTiles,
    laneAt: (tile, heading) => at.get(tile * 4 + heading) ?? -1,
  };
}
