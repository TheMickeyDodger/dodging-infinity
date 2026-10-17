/**
 * DI RESPONSIVE OFFICE WORLD (DI mode only; DOM-free, unit-tested).
 *
 * Scaling cannot fix a SHAPE mismatch: a wide room in a near-square main area leaves empty
 * backdrop at any zoom. So the DEFAULT layout (recognised by its structural fingerprint,
 * never a custom one) is reshaped with existing mechanisms: its VOID padding is cropped, and
 * a hall of workstation bands (expandLayout + the default room's own wall, floor, desk, PC and
 * bench) is added below it until the world's proportions suit the available aspect. Integer
 * fit-zoom then does only the scaling.
 *
 * The same bands carry CAPACITY (more bound workers -> more bands, within DI_WORLD_MAX_ROWS) and MISSION
 * GROUPING: the first Mission holds the room; every further Mission gets its own SLOTS (half
 * bands, so small Missions share a band), each an Area labelled for it and mapped through
 * areaMappings so the seat picker keeps it together.
 * Zone assignment is sticky, and furniture uids are deterministic, so characters keep their
 * seats across reflows and growth.
 */
import {
  DI_BAND_ROWS,
  DI_HALL_HEAD_ROWS,
  DI_POD_DESK,
  DI_POD_FIRST_COL,
  DI_POD_PC,
  DI_POD_PITCH,
  DI_POD_SEAT,
  DI_POD_SEATS,
  DI_SLOTS_PER_BAND,
  DI_WORLD_MAX_ROWS,
  DI_ZONE_COLORS,
  TILE_SIZE,
  ZOOM_MAX,
  ZOOM_MIN,
} from '../../constants.js';
import { expandLayout } from '../editor/editorActions.js';
import type { OfficeLayout, PlacedFurniture } from '../types.js';
import { TileType } from '../types.js';

/** FNV-1a (32-bit) over the layout's STRUCTURE: dims, tiles, furniture placement. */
export function layoutFingerprint(
  layout: Pick<OfficeLayout, 'cols' | 'rows' | 'tiles' | 'furniture'>,
): string {
  const text = JSON.stringify([
    layout.cols,
    layout.rows,
    layout.tiles,
    layout.furniture.map((f) => [f.uid, f.type, f.col, f.row]),
  ]);
  let hash = 0x811c9dc5;
  for (let i = 0; i < text.length; i++) {
    hash ^= text.charCodeAt(i);
    hash = Math.imul(hash, 0x01000193) >>> 0;
  }
  return `fnv1a32:${hash.toString(16).padStart(8, '0')}`;
}

/**
 * Integer zoom (DEVICE pixels per sprite pixel) that fits the whole grid in the canvas BACKING
 * STORE (canvas.width/height, i.e. CSS size x DPR), clamped to the editor's zoom range.
 */
export function diFitZoom(backingW: number, backingH: number, cols: number, rows: number): number {
  if (backingW <= 0 || backingH <= 0 || cols <= 0 || rows <= 0) return ZOOM_MIN;
  const fit = Math.floor(Math.min(backingW / (cols * TILE_SIZE), backingH / (rows * TILE_SIZE)));
  return Math.max(ZOOM_MIN, Math.min(ZOOM_MAX, fit));
}

/** The layout cropped to its non-VOID bounding box (furniture shifted with it). */
export function cropToContent(layout: OfficeLayout): OfficeLayout {
  let minC = layout.cols;
  let minR = layout.rows;
  let maxC = -1;
  let maxR = -1;
  for (let r = 0; r < layout.rows; r++)
    for (let c = 0; c < layout.cols; c++)
      if (layout.tiles[r * layout.cols + c] !== TileType.VOID) {
        minC = Math.min(minC, c);
        maxC = Math.max(maxC, c);
        minR = Math.min(minR, r);
        maxR = Math.max(maxR, r);
      }
  if (maxC < 0) return layout;
  const cols = maxC - minC + 1;
  const rows = maxR - minR + 1;
  const pick = <T>(arr: T[] | undefined): T[] | undefined => {
    if (!arr) return undefined;
    const out: T[] = [];
    for (let r = minR; r <= maxR; r++)
      for (let c = minC; c <= maxC; c++) out.push(arr[r * layout.cols + c]);
    return out;
  };
  return {
    ...layout,
    cols,
    rows,
    tiles: pick(layout.tiles)!,
    tileColors: pick(layout.tileColors),
    carpetTiles: pick(layout.carpetTiles),
    areaTiles: pick(layout.areaTiles),
    furniture: layout.furniture.map((f) => ({ ...f, col: f.col - minC, row: f.row - minR })),
  };
}

/** Pod columns within a band of a room `cols` wide (interior 1..cols-2, 3-wide desks). */
export function podColumns(cols: number): number[] {
  const out: number[] = [];
  for (let c = DI_POD_FIRST_COL; c + 2 <= cols - 2; c += DI_POD_PITCH) out.push(c);
  return out;
}

export function bandSeats(cols: number): number {
  return podColumns(cols).length * DI_POD_SEATS;
}

/** The pods of slot `half` (0..DI_SLOTS_PER_BAND-1) within a band. */
export function slotPods(cols: number, half: number): number[] {
  const pods = podColumns(cols);
  const per = Math.ceil(pods.length / DI_SLOTS_PER_BAND);
  return pods.slice(half * per, (half + 1) * per);
}

/** Seats one slot guarantees (the smallest slot, so capacity is never over-promised). */
export function slotSeats(cols: number): number {
  let min = Infinity;
  for (let h = 0; h < DI_SLOTS_PER_BAND; h++) min = Math.min(min, slotPods(cols, h).length);
  return Number.isFinite(min) ? min * DI_POD_SEATS : 0;
}

/** Bands needed to hold `slots` slots. */
export function bandsForSlots(slots: number): number {
  return Math.ceil(slots / DI_SLOTS_PER_BAND);
}

/** Rows of a world with `bands` bands under a room `roomRows` tall. */
export function worldRows(roomRows: number, bands: number): number {
  return bands === 0 ? roomRows : roomRows + DI_HALL_HEAD_ROWS + DI_BAND_ROWS * bands;
}

export function maxBands(roomRows: number): number {
  return Math.max(0, Math.floor((DI_WORLD_MAX_ROWS - roomRows - DI_HALL_HEAD_ROWS) / DI_BAND_ROWS));
}

/** Band count whose world proportions best suit `aspect` (main area width / height). */
export function aspectBands(roomCols: number, roomRows: number, aspect: number): number {
  if (!(aspect > 0)) return 0;
  const targetRows = roomCols / aspect;
  let best = 0;
  let bestErr = Math.abs(roomRows - targetRows);
  for (let b = 1; b <= maxBands(roomRows); b++) {
    const err = Math.abs(worldRows(roomRows, b) - targetRows);
    if (err < bestErr) {
      best = b;
      bestErr = err;
    }
  }
  return best;
}

/** Which Mission holds the room and each hall SLOT (half band; null = unassigned). */
export interface DiZoneAssignment {
  room: string | null;
  slots: Array<string | null>;
}

/**
 * Sticky zone assignment. A Mission keeps the zones it holds while it has drawn characters;
 * an ended Mission's zones are released (never kept to look populated). The room goes to the
 * first present Mission holding no slot; each Mission then gets slots until its zones seat
 * every one of its characters, within `limit` slots. Missions in office-state order.
 */
export function assignDiZones(
  prev: DiZoneAssignment,
  missions: ReadonlyArray<{ missionRef: string; count: number }>,
  roomSeats: number,
  seatsPerSlot: number,
  limit: number,
): DiZoneAssignment {
  const present = new Set(missions.filter((m) => m.count > 0).map((m) => m.missionRef));
  const bands = prev.slots.map((ref) => (ref !== null && present.has(ref) ? ref : null));
  let room = prev.room !== null && present.has(prev.room) ? prev.room : null;
  if (room === null)
    room = missions.find((m) => m.count > 0 && !bands.includes(m.missionRef))?.missionRef ?? null;
  for (const m of missions) {
    if (m.count === 0 || seatsPerSlot <= 0) continue;
    let capacity =
      (room === m.missionRef ? roomSeats : 0) +
      bands.filter((ref) => ref === m.missionRef).length * seatsPerSlot;
    while (capacity < m.count) {
      let free = bands.indexOf(null);
      if (free < 0) {
        if (bands.length >= limit) break;
        bands.push(null);
        free = bands.length - 1;
      }
      bands[free] = m.missionRef;
      capacity += seatsPerSlot;
    }
  }
  while (bands.length > 0 && bands[bands.length - 1] === null) bands.pop();
  return { room, slots: bands };
}

/**
 * Build the world: the cropped default `room`, plus `bandCount` workstation bands below it,
 * with an Area per assigned Mission. Returns the layout and the folder (missionRef) -> Area
 * mappings for the seat picker.
 */
export function buildDiWorld(
  room: OfficeLayout,
  bandCount: number,
  assignment: DiZoneAssignment,
  labelOf: (missionRef: string) => string,
): { layout: OfficeLayout; mappings: Record<string, string[]> } {
  const W = room.cols;
  const R0 = room.rows;
  let layout: OfficeLayout = { ...room, areas: [], areaTiles: undefined };
  const bands = Math.min(bandCount, maxBands(R0));
  const rows = worldRows(R0, bands);
  while (layout.rows < rows) {
    const grown = expandLayout(layout, 'down');
    if (!grown) break;
    layout = grown.layout;
  }
  const at = (r: number, c: number) => r * W + c;
  const tiles = [...layout.tiles];
  const colors = layout.tileColors ? [...layout.tileColors] : new Array(tiles.length).fill(null);
  const furniture: PlacedFurniture[] = [...layout.furniture];

  if (bands > 0) {
    // The hall reuses the room's own wall and floor (type + colour).
    const wallIdx = room.tiles.findIndex((t) => t === TileType.WALL);
    const floorIdx = room.tiles.findIndex(
      (t, i) => t !== TileType.WALL && t !== TileType.VOID && i >= W * (R0 - 1),
    );
    const wallColor = wallIdx >= 0 ? (room.tileColors?.[wallIdx] ?? null) : null;
    const floorType = floorIdx >= 0 ? room.tiles[floorIdx] : TileType.FLOOR_1;
    const floorColor = floorIdx >= 0 ? (room.tileColors?.[floorIdx] ?? null) : null;
    const occupied = new Set(room.furniture.filter((f) => f.row === R0 - 1).map((f) => f.col));
    // Doorways through the hall's top wall: under floor in the room's bottom row.
    const doors = new Set<number>();
    for (let c = 1; c + 1 < W - 1 && doors.size < 4; c++) {
      const open = (cc: number) =>
        room.tiles[at(R0 - 1, cc)] !== TileType.WALL &&
        room.tiles[at(R0 - 1, cc)] !== TileType.VOID &&
        !occupied.has(cc);
      if (open(c) && open(c + 1) && ![...doors].some((d) => Math.abs(d - c) < 6)) {
        doors.add(c);
        doors.add(c + 1);
      }
    }
    for (let c = 0; c < W; c++) {
      const door = doors.has(c);
      tiles[at(R0, c)] = door ? floorType : TileType.WALL;
      colors[at(R0, c)] = door ? floorColor : wallColor;
    }
    for (let r = R0 + 1; r < rows; r++)
      for (let c = 0; c < W; c++) {
        const wall = c === 0 || c === W - 1;
        tiles[at(r, c)] = wall ? TileType.WALL : floorType;
        colors[at(r, c)] = wall ? wallColor : floorColor;
      }
    for (let b = 0; b < bands; b++) {
      const r = R0 + DI_HALL_HEAD_ROWS + DI_BAND_ROWS * b;
      podColumns(W).forEach((c, p) => {
        const id = `di-band-${b}-pod-${p}`;
        furniture.push(
          { uid: `${id}-desk`, type: DI_POD_DESK, col: c, row: r },
          { uid: `${id}-pc`, type: DI_POD_PC, col: c + 1, row: r },
          { uid: `${id}-seat-a`, type: DI_POD_SEAT, col: c, row: r + 2 },
          { uid: `${id}-seat-b`, type: DI_POD_SEAT, col: c + 2, row: r + 2 },
        );
      });
    }
  }

  // Mission zones -> Areas (labels unique per Mission) + folder mappings.
  const labels = new Map<string, string>();
  const used = new Set<string>();
  const slotLimit = bands * DI_SLOTS_PER_BAND;
  const zoneOrder = [assignment.room, ...assignment.slots.slice(0, slotLimit)].filter(
    (ref): ref is string => ref !== null,
  );
  for (const ref of zoneOrder) {
    if (labels.has(ref)) continue;
    let label = labelOf(ref);
    for (let n = 2; used.has(label); n++) label = `${labelOf(ref)} (${n})`;
    used.add(label);
    labels.set(ref, label);
  }
  const areaTiles = new Array<string | null>(tiles.length).fill(null);
  const paint = (r: number, c: number, label: string) => {
    const t = tiles[at(r, c)];
    if (t !== TileType.WALL && t !== TileType.VOID) areaTiles[at(r, c)] = label;
  };
  if (assignment.room !== null)
    for (let r = 0; r < R0; r++)
      for (let c = 0; c < W; c++) paint(r, c, labels.get(assignment.room)!);
  assignment.slots.slice(0, slotLimit).forEach((ref, i) => {
    if (ref === null) return;
    const b = Math.floor(i / DI_SLOTS_PER_BAND);
    const half = i % DI_SLOTS_PER_BAND;
    const top = R0 + DI_HALL_HEAD_ROWS + DI_BAND_ROWS * b;
    // The slot's columns: from just after the previous slot's last pod to its own last pod's
    // aisle (the first slot starts at the hall's inner wall, the last ends at the other).
    const pods = slotPods(W, half);
    const prev = half > 0 ? slotPods(W, half - 1) : [];
    const from = prev.length > 0 ? prev[prev.length - 1] + 3 : 1;
    const to =
      half === DI_SLOTS_PER_BAND - 1 || pods.length === 0 ? W - 2 : pods[pods.length - 1] + 2;
    for (let r = top; r < top + DI_BAND_ROWS; r++)
      for (let c = from; c <= to; c++) paint(r, c, labels.get(ref)!);
  });
  const areas = [...labels.values()].map((label, i) => ({
    label,
    color: DI_ZONE_COLORS[i % DI_ZONE_COLORS.length],
  }));
  const mappings: Record<string, string[]> = {};
  for (const [ref, label] of labels) mappings[ref] = [label];
  return {
    layout: { ...layout, tiles, tileColors: colors, furniture, areas, areaTiles },
    mappings,
  };
}
