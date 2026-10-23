/**
 * WEATHER REGIONS (item 18): which patch of the world each tile belongs to, for the regional weather. DOM-free.
 *
 * A region map is derived ONCE per layout from the layout's own district labels (`areaTiles`) and cached by that array's
 * identity, exactly like the indoor/outdoor mask. It is in WORLD coordinates (one entry per tile), so the camera, the zoom, a
 * district jump or the Map never change it, and neither does anything else the viewer does.
 *
 *  - LABELLED worlds: each district label maps to ONE of a small fixed set of region CLASSES by the table below; a label the
 *    table does not know, and a tile with no label, takes the City class (the documented default).
 *  - UNLABELLED worlds (no district label the table knows): a world at least CITY_REGION_BAND_MIN_COLS wide is split into
 *    CITY_REGION_FALLBACK_BANDS equal vertical bands west to east, each its own region of the City class; a narrower layout is
 *    ONE region. So a small or imported layout is always covered, never left without weather.
 *
 * The region INDEX is the slot the weather schedule keys on. In a labelled world the index IS the class; in a banded world it is
 * the band. The number of slots is fixed (CITY_REGION_SLOTS), so nothing here grows with the world.
 */
import {
  CITY_REGION_BAND_MIN_COLS,
  CITY_REGION_EDGE_TILES,
  CITY_REGION_FALLBACK_BANDS,
  CITY_REGION_SLOTS,
} from '../../constants.js';

/** The region classes. The numbers are the slot indices of a labelled world. */
export const REGION_CITY = 0;
export const REGION_COAST = 1;
export const REGION_WOODLAND = 2;
export const REGION_HIGHLANDS = 3;
export const REGION_FARMLAND = 4;
export type RegionClass = 0 | 1 | 2 | 3 | 4;

/** Plain names, by class, for the controls. */
export const REGION_CLASS_NAMES: readonly string[] = [
  'City',
  'Coast',
  'Woodland',
  'Highlands',
  'Farmland',
];

/** The declarative label -> class table (the district labels of the shipped living metropolis; any other label is City). */
export const REGION_LABEL_CLASS: Readonly<Record<string, RegionClass>> = {
  Metropolis: REGION_CITY,
  'Grand Boulevard': REGION_CITY,
  'Botanical Atrium': REGION_CITY,
  'Deco Observatory': REGION_CITY,
  'Cathedral Quarter': REGION_CITY,
  Cathedral: REGION_CITY,
  'Cathedral Parvis': REGION_CITY,
  'Market Boulevard': REGION_CITY,
  Target: REGION_CITY,
  Walmart: REGION_CITY,
  'Atrium Station': REGION_CITY,
  'Observatory Station': REGION_CITY,
  'Cathedral Station': REGION_CITY,
  'Market Station': REGION_CITY,
  'North Shore': REGION_COAST,
  Harbour: REGION_COAST,
  'Aquarium Quarter': REGION_COAST,
  'Mirror Lakes': REGION_COAST,
  'Autumn Wildwood': REGION_WOODLAND,
  'Silver Pine Highlands': REGION_HIGHLANDS,
  'River Valley Farmland': REGION_FARMLAND,
};

/** Marks a tile outside the map (never read for weather: such a tile is not outdoors either). */
export const REGION_NONE = 255;

export type RegionLayoutKind = 'districts' | 'bands' | 'single';

export interface RegionMap {
  cols: number;
  rows: number;
  /** cols * rows region slot indices, row-major (REGION_NONE past the end of the array). */
  cells: Uint8Array;
  /** How many slots this layout actually uses (they are numbered from 0). */
  slots: number;
  /** The class of each slot (CITY_REGION_SLOTS entries): what the weather eligibility table is read with. */
  classes: Uint8Array;
  kind: RegionLayoutKind;
}

function allClasses(): Uint8Array {
  const classes = new Uint8Array(CITY_REGION_SLOTS);
  classes.fill(REGION_CITY);
  return classes;
}

export function buildRegionMap(
  cols: number,
  rows: number,
  areaTiles: ReadonlyArray<string | null> | undefined,
): RegionMap {
  const total = cols * rows;
  const cells = new Uint8Array(total).fill(REGION_NONE);
  let known = 0;
  if (areaTiles) {
    const n = Math.min(total, areaTiles.length);
    for (let i = 0; i < n; i++) {
      const label = areaTiles[i];
      if (label !== null && label !== undefined && Object.hasOwn(REGION_LABEL_CLASS, label))
        known++;
    }
  }
  if (known > 0 && areaTiles) {
    const n = Math.min(total, areaTiles.length);
    for (let i = 0; i < total; i++) {
      const label = i < n ? areaTiles[i] : null;
      cells[i] =
        label !== null && label !== undefined && Object.hasOwn(REGION_LABEL_CLASS, label)
          ? REGION_LABEL_CLASS[label]
          : REGION_CITY;
    }
    const classes = new Uint8Array(CITY_REGION_SLOTS);
    for (let k = 0; k < CITY_REGION_SLOTS; k++) classes[k] = k; // the index IS the class
    return { cols, rows, cells, slots: CITY_REGION_SLOTS, classes, kind: 'districts' };
  }
  const bands = cols >= CITY_REGION_BAND_MIN_COLS ? CITY_REGION_FALLBACK_BANDS : 1;
  for (let r = 0; r < rows; r++) {
    for (let c = 0; c < cols; c++) cells[r * cols + c] = Math.floor((c * bands) / cols);
  }
  return {
    cols,
    rows,
    cells,
    slots: bands,
    classes: allClasses(),
    kind: bands > 1 ? 'bands' : 'single',
  };
}

const regionCache = new WeakMap<object, RegionMap>();
const noAreas: object = {};

/** The region map of a layout, built once per `areaTiles` array (or, without one, once for the layout's tile array). */
export function regionMapFor(layout: {
  cols: number;
  rows: number;
  tiles: readonly number[];
  areaTiles?: Array<string | null>;
}): RegionMap {
  const key: object = layout.areaTiles ?? layout.tiles ?? noAreas;
  const cached = regionCache.get(key);
  if (cached && cached.cols === layout.cols && cached.rows === layout.rows) return cached;
  const built = buildRegionMap(layout.cols, layout.rows, layout.areaTiles);
  regionCache.set(key, built);
  return built;
}

/** The region slot at a tile, or REGION_NONE outside the map. */
export function regionAt(map: RegionMap, col: number, row: number): number {
  if (col < 0 || row < 0 || col >= map.cols || row >= map.rows) return REGION_NONE;
  return map.cells[row * map.cols + col];
}

/** Plain name of a slot, for the controls ("City", "Coast"; "West", "Middle", "East" for a banded world; "Everywhere" for one region). */
export function regionName(map: RegionMap, slot: number): string {
  if (map.kind === 'districts') return REGION_CLASS_NAMES[map.classes[slot]] ?? 'Elsewhere';
  if (map.kind === 'single') return 'Everywhere';
  return ['West', 'Middle', 'East'][slot] ?? 'Elsewhere';
}

/**
 * THE SOFT EDGE between regions (item 18). A tile within CITY_REGION_EDGE_TILES of a tile of ANOTHER region (a 4-neighbour step at a time)
 * is an edge tile: its weather veil and its particle strength blend toward that region's, by distance, so two regions' weather meets in a
 * few tiles' ramp and not a straight line. It is a function of the region map alone (the world), so it never depends on the camera, the
 * zoom, the time or anything the viewer does, and each region's own state stays what it is: only the blend at the border is soft.
 */
export interface RegionEdges {
  /** Per tile: 0 outside every soft edge, else 1..CITY_REGION_EDGE_TILES, the distance in tiles to the nearest tile of another region. */
  dist: Uint8Array;
  /** Per tile: the slot of that other region (meaningful where `dist` is above 0). */
  foreign: Uint8Array;
}

/** How far a tile at distance `d` from a foreign region leans toward it, 0..0.5 (a tile at the border leans almost halfway). */
export function edgeWeight(d: number): number {
  if (d <= 0 || d > CITY_REGION_EDGE_TILES) return 0;
  return 0.5 * (1 - (d - 0.5) / CITY_REGION_EDGE_TILES);
}

/** A tile's strength leaned toward the neighbouring region by `w`; exactly its own when the two are equal (no floating-point noise in a uniform state). */
export function blendStrength(own: number, foreign: number, w: number): number {
  return w > 0 && foreign !== own ? (1 - w) * own + w * foreign : own;
}

const edgeCache = new WeakMap<RegionMap, RegionEdges>();

/** The soft-edge field of a region map, built once per map (a layout's own, so it is dropped with it). */
export function regionEdges(map: RegionMap): RegionEdges {
  const cached = edgeCache.get(map);
  if (cached) return cached;
  const { cols, rows, cells } = map;
  const n = cols * rows;
  const dist = new Uint8Array(n);
  const foreign = new Uint8Array(n);
  const queue = new Int32Array(n);
  let head = 0;
  let tail = 0;
  const neighbours = [-1, 1, -cols, cols];
  // distance 1: a tile with a 4-neighbour in another region
  for (let i = 0; i < n; i++) {
    const here = cells[i];
    if (here === REGION_NONE) continue;
    const c = i % cols;
    for (let k = 0; k < 4; k++) {
      if ((k === 0 && c === 0) || (k === 1 && c === cols - 1)) continue;
      const j = i + neighbours[k];
      if (j < 0 || j >= n) continue;
      const there = cells[j];
      if (there !== REGION_NONE && there !== here) {
        dist[i] = 1;
        foreign[i] = there;
        queue[tail++] = i;
        break;
      }
    }
  }
  // spread inward through the same region, up to the edge width
  while (head < tail) {
    const i = queue[head++];
    if (dist[i] >= CITY_REGION_EDGE_TILES) continue;
    const c = i % cols;
    for (let k = 0; k < 4; k++) {
      if ((k === 0 && c === 0) || (k === 1 && c === cols - 1)) continue;
      const j = i + neighbours[k];
      if (j < 0 || j >= n || dist[j] !== 0 || cells[j] !== cells[i]) continue;
      dist[j] = dist[i] + 1;
      foreign[j] = foreign[i];
      queue[tail++] = j;
    }
  }
  const edges = { dist, foreign };
  edgeCache.set(map, edges);
  return edges;
}
