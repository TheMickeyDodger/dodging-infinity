/**
 * LIVING METROPOLIS (M3 S2) — the r4 metropolis world (352 x 224, sha256 9aca9d53...34bc16) with
 * finite new ground appended to the SOUTH (80 rows; every existing cell keeps its index), and on it:
 * two palace-scale office complexes (Botanical Atrium, Deco Observatory), a walkable Gothic
 * cathedral quarter, a Market Boulevard (Target, Walmart, dining, a fountain plaza, landmarks), a
 * Grand Boulevard and promenade joining it all to the existing world, static parked-traffic clusters,
 * distinct tower designs on the existing skyline slots, and the wall-join repair sweep.
 *
 *   npx tsx scripts/build-living-world.ts [--out <file>]   (default packaging/di/worlds/living-metropolis.json)
 *
 * Conventions are r4's own (scripts/build-metropolis-world.mjs): the same tiles, ground palettes,
 * wall colour, carpets and the palace's room patterns. The complexes' workrooms are furnished like the
 * palace's (desk, computer, bench) and their meeting rooms with real chairs. Per lead1, the r4
 * aggregates (exactly 41 PCs in total; no chair outside the home) are r4-shape checks, not mission
 * constraints. What binds, and is asserted, is that the ORIGINAL 41 computers and the original
 * in-home seats are present and unchanged by uid. Totals and new seats outside the home are reported
 * by count and location. Chairs outside the home are not assignable: the product's existing
 * homeSeats() rule keeps every real-agent seat in the palace. Every new sprite is STATIC.
 * PRESERVATION is asserted before writing:
 *  - every r4 cell is unchanged except the recorded sweep repairs;
 *  - every r4 furniture record is unchanged except the recorded tower-design swaps (same footprint
 *    width, same solid rows, same bottom edge);
 *  - pets and home are unchanged.
 * Deterministic.
 */
import crypto from 'node:crypto';
import fs from 'node:fs';
import path from 'node:path';

const ROOT = path.resolve(__dirname, '..');
const args = process.argv.slice(2);
const OUT = args.includes('--out') ? path.resolve(args[args.indexOf('--out') + 1]) : path.join(ROOT, 'packaging/di/worlds/living-metropolis.json');
const IN_FILE = path.join(ROOT, 'packaging/di/worlds/metropolis.json');
const IN_SHA = '9aca9d53805625a08fd118e0e72b20b19abb664588de094c9dc2f1f3d834bc16';
const REPORT = path.join(ROOT, 'packaging/di/LIVING-WORLD-BUILD.json');

// ── tiles (webview TileType) ────────────────────────────────────────────────
const T = { WALL: 0, F2: 2, F3: 3, F6: 6, F7: 7, F9: 9, GRASS: 10, FOREST: 11, TRAIL: 12, POND: 14, ASPHALT: 15, DASH_EW: 16, DASH_NS: 17, PAVEMENT: 18, COBBLE: 19, SAND: 20, SEA: 22, RIVER: 23, BOARD: 24, CHECKER: 25, PARQUET: 26, ZEBRA: 27, VOID: 255 } as const;
const WATER = new Set<number>([14, 22, 23]);
const ROAD = new Set<number>([15, 16, 17]);
interface Colour {
  h: number;
  s: number;
  b: number;
  c: number;
  colorize?: boolean;
}
const c4 = (h: number, s: number, b: number, c: number): Colour => ({ h, s, b, c });
interface Carpet {
  variant: number;
  color: Colour;
  accentColor: Colour;
  order: number;
}
interface Furn {
  uid: string;
  type: string;
  col: number;
  row: number;
  color?: Colour;
}

// ── catalog (footprints) ────────────────────────────────────────────────────
interface Entry {
  w: number;
  h: number;
  bg: number;
  surface: boolean;
  wall: boolean;
  category: string;
}
const CATALOG = new Map<string, Entry>();
{
  const dir = path.join(ROOT, 'webview-ui/public/assets/furniture');
  type Node = { type: string; id?: string; footprintW?: number; footprintH?: number; category?: string; backgroundTiles?: number; canPlaceOnSurfaces?: boolean; canPlaceOnWalls?: boolean; members?: Node[] };
  const visit = (node: Node, inh: Partial<Entry>) => {
    const props = {
      category: node.category ?? inh.category ?? 'misc',
      bg: node.backgroundTiles ?? inh.bg ?? 0,
      surface: node.canPlaceOnSurfaces ?? inh.surface ?? false,
      wall: node.canPlaceOnWalls ?? inh.wall ?? false,
    };
    if (node.type === 'asset' && node.id) CATALOG.set(node.id, { w: node.footprintW ?? 1, h: node.footprintH ?? 1, ...props });
    for (const m of node.members ?? []) visit(m, props);
  };
  for (const id of fs.readdirSync(dir)) {
    const f = path.join(dir, id, 'manifest.json');
    if (fs.existsSync(f)) visit(JSON.parse(fs.readFileSync(f, 'utf-8')), {});
  }
}
const entryOf = (type: string): Entry => {
  const e = CATALOG.get(type.replace(/:left$/, ''));
  if (!e) throw new Error(`unknown furniture type ${type}`);
  return e;
};

// ── the r4 input, pinned ────────────────────────────────────────────────────
const inBytes = fs.readFileSync(IN_FILE);
const inSha = crypto.createHash('sha256').update(inBytes).digest('hex');
if (inSha !== IN_SHA) throw new Error(`REFUSING: ${IN_FILE} sha256 ${inSha} != pinned ${IN_SHA}`);
const R4 = JSON.parse(inBytes.toString('utf-8'));
const COLS: number = R4.cols;
const OLD_ROWS: number = R4.rows;
const ADD_ROWS = 88; // 80 rows of quarters and boulevard (S2) + 8 rows for the subway line (S3)
const ROWS = OLD_ROWS + ADD_ROWS;
const N = COLS * ROWS;
const OLD_N = COLS * OLD_ROWS;
const pad = <V>(a: V[], v: V) => [...a, ...new Array<V>(N - a.length).fill(v)];
const tiles: number[] = pad(R4.tiles as number[], T.GRASS as number);
const tileColors: Array<Colour | null> = pad(R4.tileColors as Array<Colour | null>, null);
const carpetTiles: Array<Carpet | null> = pad(R4.carpetTiles as Array<Carpet | null>, null);
const areaTiles: Array<string | null> = pad(R4.areaTiles as Array<string | null>, null);
const furniture: Furn[] = (R4.furniture as Furn[]).map((f) => ({ ...f }));
const R4_FURN = R4.furniture.length as number;
const idx = (c: number, r: number) => r * COLS + c;
const inWorld = (c: number, r: number) => c >= 0 && r >= 0 && c < COLS && r < ROWS;
const tileAt = (c: number, r: number) => (inWorld(c, r) ? tiles[idx(c, r)] : -1);

// deterministic randomness
function rng(seed: number) {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

// ── painting NEW ground only (r4 cells are changed only by the recorded sweep) ─
const GROUND: Record<number, Colour[]> = {
  [T.GRASS]: [c4(100, 40, -15, 0), c4(96, 40, -13, 0), c4(104, 38, -17, 0)],
  [T.FOREST]: [c4(30, 42, -34, 0), c4(24, 44, -36, 0)],
  [T.ASPHALT]: [c4(220, 8, -42, 10)],
  [T.DASH_EW]: [c4(220, 8, -42, 10)],
  [T.DASH_NS]: [c4(220, 8, -42, 10)],
  [T.ZEBRA]: [c4(220, 8, -42, 10)],
  [T.PAVEMENT]: [c4(40, 8, -8, 0), c4(36, 8, -10, 0)],
  [T.COBBLE]: [c4(30, 22, -12, 10)],
  [T.SAND]: [c4(42, 48, 4, 0), c4(40, 44, 1, 0)],
  [T.SEA]: [c4(205, 62, -28, 10)],
  [T.RIVER]: [c4(195, 52, -14, 0), c4(198, 50, -17, 0)],
  [T.POND]: [c4(196, 50, -20, 0)],
  [T.BOARD]: [c4(30, 40, -12, 0)],
  [T.CHECKER]: [c4(212, 12, -6, 0)],
  [T.PARQUET]: [c4(28, 46, -16, 0)],
};
const WALL_COLOUR = c4(226, 28, -100, -45); // the palace's own wall colour
const pr = rng(2026);
function paint(c: number, r: number, t: number, colour?: Colour | null): void {
  if (!inWorld(c, r)) return;
  if (r < OLD_ROWS) throw new Error(`paint(${c},${r}) would change an r4 cell`);
  const i = idx(c, r);
  tiles[i] = t;
  if (t === T.WALL) {
    tileColors[i] = colour ?? WALL_COLOUR;
    return;
  }
  if (colour) {
    tileColors[i] = colour;
    return;
  }
  const p = GROUND[t];
  tileColors[i] = p ? p[Math.floor(pr() * p.length)] : null;
}
function rect(c0: number, r0: number, c1: number, r1: number, t: number, colour?: Colour | null): void {
  for (let r = r0; r <= r1; r++) for (let c = c0; c <= c1; c++) paint(c, r, t, colour);
}
function carpet(c0: number, r0: number, c1: number, r1: number, variant: number, color: Colour, accentColor: Colour, order = 1): void {
  for (let r = r0; r <= r1; r++) for (let c = c0; c <= c1; c++) if (r >= OLD_ROWS && inWorld(c, r)) carpetTiles[idx(c, r)] = { variant, color, accentColor, order };
}
function area(c0: number, r0: number, c1: number, r1: number, label: string): void {
  for (let r = r0; r <= r1; r++) for (let c = c0; c <= c1; c++) if (r >= OLD_ROWS && inWorld(c, r)) areaTiles[idx(c, r)] = label;
}

// ── furniture with an occupancy grid (r4's place / placeWall rules) ─────────
const occupied = new Uint8Array(N); // 1 floor item, 2 desk surface, 3 surface item placed
for (const f of furniture) {
  const e = CATALOG.get(f.type.replace(/:left$/, ''));
  if (!e) continue;
  for (let dr = 0; dr < e.h; dr++) for (let dc = 0; dc < e.w; dc++) if (inWorld(f.col + dc, f.row + dr)) occupied[idx(f.col + dc, f.row + dr)] = 1;
}
let uidN = 0;
const refused = new Map<string, number>();
const placedByTag = new Map<string, number>();
function record(type: string, col: number, row: number, tag: string, color?: Colour): void {
  const rec: Furn = { uid: `lw-${tag}-${++uidN}`, type, col, row };
  if (color) rec.color = color;
  furniture.push(rec);
  placedByTag.set(tag, (placedByTag.get(tag) ?? 0) + 1);
}
const refuse = (tag: string) => {
  refused.set(tag, (refused.get(tag) ?? 0) + 1);
  return false;
};
interface PlaceOpts {
  tag: string;
  on?: Set<number>;
  water?: boolean;
  color?: Colour;
  /** only the SOLID rows must be free (a vehicle's walk-behind roof may overlap street dressing) */
  solidOnly?: boolean;
}
function place(type: string, col: number, row: number, opts: PlaceOpts): boolean {
  const e = entryOf(type);
  if (type.startsWith('DECK_')) throw new Error(`${type}: DECK_* is the palace deck's own family`);
  if (row < OLD_ROWS && !opts.tag.startsWith('traffic')) throw new Error(`${type}@${col},${row}: new furniture only on new ground (or traffic on roads)`);
  let onDesk = e.surface;
  for (let dr = 0; dr < e.h && onDesk; dr++) for (let dc = 0; dc < e.w; dc++) if (!inWorld(col + dc, row + dr) || occupied[idx(col + dc, row + dr)] !== 2) onDesk = false;
  if (onDesk) {
    for (let dr = 0; dr < e.h; dr++) for (let dc = 0; dc < e.w; dc++) occupied[idx(col + dc, row + dr)] = 3;
    record(type, col, row, opts.tag, opts.color);
    return true;
  }
  for (let dr = 0; dr < e.h; dr++)
    for (let dc = 0; dc < e.w; dc++) {
      const c = col + dc;
      const r = row + dr;
      if (!inWorld(c, r)) return refuse(opts.tag);
      if (occupied[idx(c, r)] && !(opts.solidOnly && dr < e.bg)) return refuse(opts.tag);
      const t = tiles[idx(c, r)];
      if (t === T.WALL || t === T.VOID) return refuse(opts.tag);
      if (dr >= e.bg) {
        if (opts.on ? !opts.on.has(t) : WATER.has(t) && !opts.water) return refuse(opts.tag);
      }
    }
  const mark = e.category === 'desks' ? 2 : 1;
  for (let dr = opts.solidOnly ? e.bg : 0; dr < e.h; dr++) for (let dc = 0; dc < e.w; dc++) occupied[idx(col + dc, row + dr)] = mark;
  record(type, col, row, opts.tag, opts.color);
  return true;
}
/** A wall item hung on a WALL row: its bottom footprint row must lie on wall tiles. */
function placeWall(type: string, col: number, rowOfWall: number, tag: string): boolean {
  const e = entryOf(type);
  const row = rowOfWall - (e.h - 1);
  for (let dc = 0; dc < e.w; dc++) if (tileAt(col + dc, rowOfWall) !== T.WALL) return refuse(tag);
  for (let dr = 0; dr < e.h - 1; dr++) for (let dc = 0; dc < e.w; dc++) if (inWorld(col + dc, row + dr) && occupied[idx(col + dc, row + dr)]) return refuse(tag);
  for (let dr = 0; dr < e.h - 1; dr++) for (let dc = 0; dc < e.w; dc++) if (inWorld(col + dc, row + dr)) occupied[idx(col + dc, row + dr)] = 1;
  record(type, col, row, tag);
  return true;
}
/** A decorative overlay drawn over walls and floor (facade motifs): no blocking rows, no checks
 *  beyond bounds; used only for all-background art (CATHEDRAL_* facade pieces, windows). */
function overlay(type: string, col: number, row: number, tag: string): void {
  const e = entryOf(type);
  if (e.bg < e.h) throw new Error(`${type} has solid rows; overlay() is for walk-behind art only`);
  if (!inWorld(col, row) || !inWorld(col + e.w - 1, row + e.h - 1)) throw new Error(`${type} overlay out of bounds`);
  record(type, col, row, tag);
}
const ON = (...ts: number[]) => new Set<number>(ts);

// ════════════════════════════════════════════════════════════════════════════
// 1. NEW GROUND: base, coast continuity, Grand Boulevard, streets, promenade
// ════════════════════════════════════════════════════════════════════════════
const S0 = OLD_ROWS; // 224
const S1 = ROWS - 1; // 303
const EAST_EDGE = 308; // columns >= 308 continue the coast straight down (boardwalk, sand, river, sea)
for (let r = S0; r <= S1; r++)
  for (let c = 0; c < COLS; c++) {
    if (c >= EAST_EDGE) {
      const t = R4.tiles[idx(c, OLD_ROWS - 1)];
      paint(c, r, t);
      areaTiles[idx(c, r)] = R4.areaTiles[idx(c, OLD_ROWS - 1)];
    } else if (c <= 3) {
      paint(c, r, T.FOREST);
      areaTiles[idx(c, r)] = 'Autumn Wildwood';
    } else paint(c, r, T.GRASS);
  }
// Grand Boulevard: sidewalk 224-225, road 226-228 (asphalt / centre dash / asphalt, r4's 3-row
// road), promenade 229-231
const BLVD = { c0: 0, c1: EAST_EDGE - 1, sideN: [S0, S0 + 1], road: [S0 + 2, S0 + 3, S0 + 4], sideS: [S0 + 5, S0 + 7] };
rect(4, S0, BLVD.c1, S0 + 1, T.PAVEMENT);
rect(4, S0 + 2, BLVD.c1, S0 + 2, T.ASPHALT);
rect(4, S0 + 3, BLVD.c1, S0 + 3, T.DASH_EW);
rect(4, S0 + 4, BLVD.c1, S0 + 4, T.ASPHALT);
rect(4, S0 + 5, BLVD.c1, S0 + 7, T.PAVEMENT);
area(0, S0, BLVD.c1, S0 + 7, 'Grand Boulevard');
// promenade along the south edge
rect(4, 300, BLVD.c1, 302, T.PAVEMENT);
area(4, 298, BLVD.c1, S1, 'Grand Boulevard');
// N-S pedestrian streets between the quarters
const STREETS = [[76, 79], [152, 155], [248, 251]];
for (const [a, b] of STREETS) {
  rect(a, S0 + 8, b, 302, T.PAVEMENT);
  area(a, S0 + 8, b, 299, 'Grand Boulevard');
}
// zebra crossings over the boulevard road at every street and in front of each entrance
const CROSSINGS: Array<[number, number]> = [[76, 79], [152, 155], [248, 251], [38, 41], [114, 117], [199, 204], [276, 279]];
for (const [a, b] of CROSSINGS) rect(a, S0 + 2, b, S0 + 4, T.ZEBRA);

// street dressing: lamps and trees along the promenade side of the boulevard
for (let c = 6; c < BLVD.c1 - 2; c += 9) {
  if (CROSSINGS.some(([a, b]) => c >= a - 2 && c <= b + 2)) continue;
  place('DETAIL_LAMP_MODERN', c, S0 + 5, { tag: 'street', on: ON(T.PAVEMENT) });
  place(c % 2 ? 'FOREST_OAK_SMALL' : 'FOREST_BIRCH', c + 3, S0 + 5 + (c % 2 ? 0 : -1) + 1, { tag: 'street', on: ON(T.PAVEMENT) });
}

// ════════════════════════════════════════════════════════════════════════════
// 2. OFFICE COMPLEXES at the palace scale bar (72 x 64, palace block 64 x 64)
// ════════════════════════════════════════════════════════════════════════════
interface Room {
  name: string;
  kind: 'grand lobby' | 'workroom' | 'meeting room' | 'quiet room' | 'atrium' | 'corridor' | 'nave' | 'chapel' | 'choir' | 'transept' | 'sacristy';
  c0: number;
  r0: number;
  c1: number;
  r1: number;
}
interface Building {
  label: string;
  frame: Array<[number, number, number, number]>;
  rooms: Room[];
  entrances: Array<{ c: number; r: number }>;
}
const BUILDINGS: Building[] = [];
interface Theme {
  label: string;
  wall: Colour;
  lobbyFloor: number;
  lobbyColour: Colour;
  corridorFloor: number;
  corridorColour: Colour;
  roomFloor: number;
  roomColour: Colour;
  quietFloor: number;
  quietColour: Colour;
  carpetMain: Colour;
  carpetAccent: Colour;
  carpetSecond: Colour;
  court: 'garden' | 'rotunda';
}
const IN = ON(T.F2, T.F3, T.F6, T.F7, T.F9, T.CHECKER, T.PARQUET, T.GRASS, T.COBBLE, T.PAVEMENT);

function carve(c0: number, r0: number, c1: number, r1: number, t: number, colour: Colour): void {
  rect(c0, r0, c1, r1, t, colour);
}

function workroom(b: Room, top: number, k: number, rows: number, tag: string, th: Theme): void {
  let n = 0;
  for (let j = 0; j < rows; j++) {
    const r = b.r0 + 1 + j * 4;
    if (r + 3 > b.r1) break;
    for (let c = b.c0 + 1; c + 3 <= b.c1 + 1; c += 4) {
      if (place('DESK_FRONT', c, r, { tag, on: IN })) {
        // the palace's workstation: a computer on the desk, a bench in front of it
        place('PC_FRONT_OFF', c + 1, r, { tag });
        if ((c + r + k) % 3 === 0) place('COFFEE', c + 2, r + 1, { tag });
        place('CUSHIONED_BENCH', c + 1, r + 2, { tag, on: IN });
        n++;
      }
    }
  }
  for (let c = b.c0 + 1; c < b.c1 - 1; c += 3) placeWall(['DOUBLE_BOOKSHELF', 'CLOCK', 'LARGE_PAINTING', 'WHITEBOARD', 'HANGING_PLANT'][(c + k) % 5], c, top, tag);
  place('LARGE_PLANT', b.c1 - 1, b.r1 - 2, { tag, on: IN });
  place('PLANT_2', b.c0, b.r1 - 1, { tag, on: IN });
  place('BIN', b.c1, b.r0, { tag, on: IN });
  void th;
  void n;
}
function meeting(b: Room, top: number, k: number, tag: string, th: Theme): void {
  const x = Math.floor((b.c0 + b.c1) / 2) - 1;
  const y = b.r0 + 2;
  carpet(x - 2, y - 1, x + 4, y + 4, 0, th.carpetSecond, th.carpetAccent);
  place('TABLE_FRONT', x, y, { tag, on: IN });
  for (const dy of [0, 2]) {
    place('WOODEN_CHAIR_SIDE', x - 1, y + dy, { tag, on: IN });
    place('WOODEN_CHAIR_SIDE:left', x + 3, y + dy, { tag, on: IN });
  }
  place('COFFEE', x + 1, y + 1, { tag });
  placeWall('WHITEBOARD', x, top, tag);
  placeWall('CLOCK', x + 3, top, tag);
  placeWall('SMALL_PAINTING_2', b.c0 + 1, top, tag);
  placeWall('LARGE_PAINTING', b.c1 - 2, top, tag);
  place('PLANT_2', b.c0, b.r1 - 1, { tag, on: IN });
  place('PLANT_2', b.c1, b.r1 - 1, { tag, on: IN });
  place('AQUA_TANK_CYLINDER', b.c1 - 1, b.r0, { tag, on: IN });
  void k;
}
function quiet(b: Room, top: number, k: number, tag: string, th: Theme): void {
  carpet(b.c0, b.r0, b.c1, b.r1, 1, th.carpetMain, th.carpetAccent, 2);
  const x = b.c0 + 1;
  const y = b.r0 + 1;
  place('LOUNGE_SOFA_FRONT', x + 1, y, { tag, on: IN });
  place('LOUNGE_SOFA_SIDE', x, y + 1, { tag, on: IN });
  place('LOUNGE_SOFA_SIDE:left', x + 3, y + 1, { tag, on: IN });
  place('COFFEE_TABLE', x + 1, y + 1, { tag, on: IN });
  place('COFFEE', x + 1, y + 2, { tag });
  for (let c = b.c0; c < b.c1; c += 2) placeWall(c % 4 === 0 ? 'DOUBLE_BOOKSHELF' : 'BOOKSHELF', c, top, tag);
  place('DETAIL_LAMP_ORNATE', b.c1, b.r0, { tag, on: IN });
  place('LARGE_PLANT', b.c1 - 1, b.r1 - 2, { tag, on: IN });
  void k;
}
function library(b: Room, top: number, k: number, tag: string, th: Theme): void {
  for (let c = b.c0; c < b.c1; c += 2) placeWall('DOUBLE_BOOKSHELF', c, top, tag);
  carpet(b.c0 + 1, b.r0 + 2, b.c1 - 1, b.r1 - 1, 0, th.carpetSecond, th.carpetAccent);
  for (let r = b.r0 + 2; r + 2 <= b.r1; r += 4)
    for (let c = b.c0 + 1; c + 2 <= b.c1; c += 4) {
      place('SMALL_TABLE_FRONT', c, r + 1, { tag, on: IN });
      place('CUSHIONED_CHAIR_FRONT', c, r, { tag, on: IN });
    }
  place('DETAIL_LAMP_ORNATE', b.c0, b.r0, { tag, on: IN });
  place('LARGE_PLANT', b.c1 - 1, b.r1 - 2, { tag, on: IN });
  void k;
}
function lobby(b: Room, top: number, entrance: [number, number], tag: string, th: Theme, court: Theme['court']): void {
  const [e0, e1] = entrance;
  // runner from the entrance to the corridor
  carpet(e0 + 1, b.r0, e1 - 1, b.r1, 0, th.carpetMain, th.carpetAccent, 2);
  // reception counter
  const cx = Math.floor((b.c0 + b.c1) / 2);
  place('DESK_FRONT', cx - 7, b.r0 + 5, { tag, on: IN });
  place('DESK_FRONT', cx + 5, b.r0 + 5, { tag, on: IN });
  place('PC_FRONT_OFF', cx - 6, b.r0 + 5, { tag });
  place('PC_FRONT_OFF', cx + 6, b.r0 + 5, { tag });
  place('CUSHIONED_BENCH', cx - 6, b.r0 + 4, { tag, on: IN });
  place('CUSHIONED_BENCH', cx + 6, b.r0 + 4, { tag, on: IN });
  // sofa lounges either side
  for (const x of [b.c0 + 1, b.c1 - 5]) {
    const y = b.r0 + 7;
    place('LOUNGE_SOFA_FRONT', x + 1, y, { tag, on: IN });
    place('LOUNGE_SOFA_SIDE', x, y + 1, { tag, on: IN });
    place('LOUNGE_SOFA_SIDE:left', x + 3, y + 1, { tag, on: IN });
    place('COFFEE_TABLE', x + 1, y + 1, { tag, on: IN });
    place('COFFEE', x + 1, y + 2, { tag });
    place('LOUNGE_SOFA_BACK', x + 1, y + 3, { tag, on: IN });
    carpet(x - 1, y - 1, x + 4, y + 4, 2, th.carpetSecond, th.carpetAccent, 1);
  }
  // centrepiece
  if (court === 'garden') {
    place('DETAIL_FOUNTAIN_SQUARE', cx - 1, b.r0 + 9, { tag, on: IN });
    for (const x of [b.c0 + 7, b.c1 - 8]) place('LARGE_PLANT', x, b.r0 + 1, { tag, on: IN });
  } else {
    place('AQUA_TANK_PANORAMIC', cx - 2, b.r0 + 9, { tag, on: IN });
    for (const x of [b.c0 + 7, b.c1 - 8]) place('DETAIL_SCULPTURE', x, b.r0 + 1, { tag, on: IN });
  }
  for (const [x, y] of [[b.c0, b.r0], [b.c1 - 1, b.r0], [b.c0, b.r1 - 2], [b.c1 - 1, b.r1 - 2]]) place('LARGE_PLANT', x, y, { tag, on: IN });
  for (let c = b.c0 + 1; c < b.c1 - 1; c += 3) if (c + 1 < e0 || c > e1) placeWall(['LARGE_PAINTING', 'CLOCK', 'LARGE_PAINTING', 'HANGING_PLANT'][c % 4], c, top, tag);
  place('DETAIL_LAMP_ORNATE', e0 - 1, b.r0, { tag, on: IN });
  place('DETAIL_LAMP_ORNATE', e1 + 1, b.r0, { tag, on: IN });
}
function corridorDressing(b: Room, top: number | null, tag: string): void {
  const horizontal = b.c1 - b.c0 > b.r1 - b.r0;
  if (horizontal) {
    for (let c = b.c0 + 2; c < b.c1 - 1; c += 7) place('POT', c, b.r0, { tag, on: IN });
    if (top !== null) for (let c = b.c0 + 4; c < b.c1 - 2; c += 7) placeWall(c % 14 < 7 ? 'SMALL_PAINTING' : 'LARGE_PAINTING', c, top, tag);
  } else {
    for (let r = b.r0 + 3; r < b.r1 - 1; r += 8) place('PLANT', b.c0, r, { tag, on: IN });
  }
}
function gardenCourt(b: Room, tag: string, k: number): void {
  // a planted glass atrium: lawn, a koi pond with lily pads, tiered fountains, trees, flowers, benches
  rect(b.c0, b.r0, b.c1, b.r1, T.GRASS, c4(104, 46, -12, 0));
  const pc0 = b.c0 + 8;
  const pc1 = b.c1 - 8;
  const pr0 = b.r0 + 9;
  const pr1 = b.r1 - 9;
  rect(pc0, pr0, pc1, pr1, T.POND);
  rect(pc0 - 1, pr0 - 1, pc1 + 1, pr0 - 1, T.COBBLE);
  rect(pc0 - 1, pr1 + 1, pc1 + 1, pr1 + 1, T.COBBLE);
  rect(pc0 - 1, pr0, pc0 - 1, pr1, T.COBBLE);
  rect(pc1 + 1, pr0, pc1 + 1, pr1, T.COBBLE);
  const R = rng(4100 + k);
  for (let n = 0; n < 14; n++) place(['AQUA_KOI', 'AQUA_KOI', 'AQUA_LILY_PADS'][n % 3], pc0 + Math.floor(R() * (pc1 - pc0)), pr0 + Math.floor(R() * (pr1 - pr0 + 1)), { tag, water: true, on: ON(T.POND) });
  // cross paths
  const mx = Math.floor((b.c0 + b.c1) / 2);
  const my = Math.floor((b.r0 + b.r1) / 2);
  rect(mx - 1, b.r0, mx, pr0 - 2, T.COBBLE);
  rect(mx - 1, pr1 + 2, mx, b.r1, T.COBBLE);
  rect(b.c0, my, pc0 - 2, my, T.COBBLE);
  rect(pc1 + 2, my, b.c1, my, T.COBBLE);
  for (const [x, y] of [[pc0 - 4, pr0 - 4], [pc1 + 2, pr0 - 4], [pc0 - 4, pr1 + 2], [pc1 + 2, pr1 + 2]]) place('DETAIL_FOUNTAIN_TIERED', x, y, { tag, on: IN });
  for (const [dc, dr] of [[1, 1], [b.c1 - b.c0 - 3, 1], [1, b.r1 - b.r0 - 3], [b.c1 - b.c0 - 3, b.r1 - b.r0 - 3], [5, 3], [b.c1 - b.c0 - 7, 3], [5, b.r1 - b.r0 - 5], [b.c1 - b.c0 - 7, b.r1 - b.r0 - 5]])
    place((dc + dr) % 2 ? 'FOREST_OAK_SMALL' : 'FOREST_BIRCH', b.c0 + dc, b.r0 + dr - 1, { tag, on: IN });
  for (let n = 0; n < 70; n++) place(['FOREST_FLOWERS', 'FOREST_FLOWERS', 'DETAIL_PLANTER', 'FOREST_BUSH', 'POT'][n % 5], b.c0 + Math.floor(R() * (b.c1 - b.c0 + 1)), b.r0 + Math.floor(R() * (b.r1 - b.r0 + 1)), { tag, on: ON(T.GRASS) });
  for (const [x, y] of [[mx - 4, pr0 - 3], [mx + 2, pr0 - 3], [mx - 4, pr1 + 2], [mx + 2, pr1 + 2]]) place('DETAIL_PARK_BENCH', x, y, { tag, on: IN });
  place('DETAIL_LILY_POND', b.c0 + 2, my + 3, { tag, on: IN });
  place('DETAIL_LILY_POND', b.c1 - 3, my - 4, { tag, on: IN });
}
function rotundaCourt(b: Room, tag: string, th: Theme): void {
  // the observatory hall: checker marble, a great gold-and-navy rotunda carpet, telescopes on the
  // rim, a crown at the centre, sculptures, aquarium columns, lamps
  rect(b.c0, b.r0, b.c1, b.r1, T.F9, c4(45, 30, -40, 10));
  const cx = (b.c0 + b.c1) / 2;
  const cy = (b.r0 + b.r1) / 2;
  const rad = Math.min(b.c1 - b.c0, b.r1 - b.r0) / 2 - 1;
  for (let r = b.r0; r <= b.r1; r++)
    for (let c = b.c0; c <= b.c1; c++) {
      const d = Math.hypot(c - cx, r - cy);
      if (d <= rad) carpetTiles[idx(c, r)] = { variant: d > rad - 2 ? 2 : d > rad - 5 ? 1 : 0, color: th.carpetMain, accentColor: th.carpetAccent, order: 1 };
    }
  place('GATEWAY_CROWN', Math.floor(cx) - 1, Math.floor(cy) - 2, { tag, on: IN });
  for (let a = 0; a < 8; a++) {
    const ang = (a / 8) * Math.PI * 2;
    const x = Math.round(cx + Math.cos(ang) * (rad - 3)) - 1;
    const y = Math.round(cy + Math.sin(ang) * (rad - 3)) - 1;
    place(a % 2 ? 'DETAIL_TELESCOPE' : 'DETAIL_SCULPTURE', x, y, { tag, on: IN });
  }
  for (const [x, y] of [[b.c0 + 1, b.r0 + 1], [b.c1 - 2, b.r0 + 1], [b.c0 + 1, b.r1 - 4], [b.c1 - 2, b.r1 - 4]]) place('AQUA_TANK_CYLINDER', x, y, { tag, on: IN });
  for (const [x, y] of [[b.c0 + 4, b.r0], [b.c1 - 4, b.r0], [b.c0 + 4, b.r1 - 2], [b.c1 - 4, b.r1 - 2]]) place('DETAIL_LAMP_ORNATE', x, y, { tag, on: IN });
  for (const [x, y] of [[b.c0 + 1, Math.floor(cy) - 1], [b.c1 - 2, Math.floor(cy) - 1]]) place('DETAIL_OBELISK', x, y, { tag, on: IN });
  place('DETAIL_VIEWER', Math.floor(cx) - 4, b.r0 + 2, { tag, on: IN });
  place('DETAIL_VIEWER', Math.floor(cx) + 4, b.r0 + 2, { tag, on: IN });
}

function complex(x0: number, y0: number, th: Theme, k: number): void {
  const tag = th.label.toLowerCase().replace(/\s+/g, '-');
  const W = 72;
  const H = 64;
  rect(x0, y0, x0 + W - 1, y0 + H - 1, T.WALL, th.wall);
  const R = (name: string, kind: Room['kind'], c0: number, r0: number, c1: number, r1: number): Room => ({ name, kind, c0: x0 + c0, r0: y0 + r0, c1: x0 + c1, r1: y0 + r1 });
  const rooms: Room[] = [
    R('Grand lobby', 'grand lobby', 21, 1, 50, 13),
    R('North-west workroom', 'workroom', 1, 1, 19, 13),
    R('North-east workroom', 'workroom', 52, 1, 70, 13),
    R('North gallery', 'corridor', 1, 15, 70, 16),
    R('West gallery', 'corridor', 20, 17, 21, 46),
    R('East gallery', 'corridor', 50, 17, 51, 46),
    R('South gallery', 'corridor', 1, 47, 70, 48),
    R(th.court === 'garden' ? 'Glass atrium garden' : 'Observatory rotunda', 'atrium', 23, 18, 48, 45),
    R('West meeting room', 'meeting room', 1, 18, 18, 25),
    R('West workroom', 'workroom', 1, 27, 18, 37),
    R('West quiet room I', 'quiet room', 1, 39, 8, 45),
    R('West quiet room II', 'quiet room', 10, 39, 18, 45),
    R('East meeting room', 'meeting room', 53, 18, 70, 25),
    R('East workroom', 'workroom', 53, 27, 70, 37),
    R('East quiet room I', 'quiet room', 53, 39, 61, 45),
    R('East quiet room II', 'quiet room', 63, 39, 70, 45),
    R('South-west workroom', 'workroom', 1, 50, 22, 62),
    R('Boardroom', 'meeting room', 24, 50, 35, 62),
    R('Library', 'quiet room', 37, 50, 47, 62),
    R('South-east workroom', 'workroom', 49, 50, 70, 62),
  ];
  for (const rm of rooms) {
    const floor = rm.kind === 'grand lobby' ? [th.lobbyFloor, th.lobbyColour] : rm.kind === 'corridor' ? [th.corridorFloor, th.corridorColour] : rm.kind === 'quiet room' ? [th.quietFloor, th.quietColour] : [th.roomFloor, th.roomColour];
    carve(rm.c0, rm.r0, rm.c1, rm.r1, floor[0] as number, floor[1] as Colour);
  }
  const door = (c0: number, r0: number, c1: number, r1: number) => carve(x0 + c0, y0 + r0, x0 + c1, y0 + r1, th.corridorFloor, th.corridorColour);
  door(33, 0, 38, 0); // the entrance from the boulevard
  door(33, 14, 38, 14); // lobby -> north gallery
  door(9, 14, 10, 14);
  door(60, 14, 61, 14);
  door(19, 21, 19, 22);
  door(19, 31, 19, 32);
  door(19, 42, 19, 42);
  door(4, 46, 5, 46);
  door(52, 21, 52, 22);
  door(52, 31, 52, 32);
  door(52, 42, 52, 42);
  door(66, 46, 67, 46);
  door(10, 49, 11, 49);
  door(29, 49, 30, 49);
  door(41, 49, 42, 49);
  door(59, 49, 60, 49);
  // the atrium/rotunda opens onto all four galleries through arcades
  for (const y of [21, 22, 26, 27, 31, 32, 36, 37, 41, 42]) {
    door(22, y, 22, y);
    door(49, y, 49, y);
  }
  for (let x = 26; x <= 45; x += 1) if (x % 4 !== 1) door(x, 17, x, 17);
  for (let x = 33; x <= 38; x++) door(x, 46, x, 46);
  // furnishing
  const by = (n: string) => rooms.find((r) => r.name === n)!;
  lobby(by('Grand lobby'), y0 + 0, [x0 + 33, x0 + 38], tag, th, th.court);
  workroom(by('North-west workroom'), y0 + 0, k, 3, tag, th);
  workroom(by('North-east workroom'), y0 + 0, k + 1, 3, tag, th);
  workroom(by('West workroom'), y0 + 26, k + 2, 2, tag, th);
  workroom(by('East workroom'), y0 + 26, k + 3, 2, tag, th);
  workroom(by('South-west workroom'), y0 + 49, k + 4, 3, tag, th);
  workroom(by('South-east workroom'), y0 + 49, k + 5, 3, tag, th);
  meeting(by('West meeting room'), y0 + 17, k, tag, th);
  meeting(by('East meeting room'), y0 + 17, k + 1, tag, th);
  meeting(by('Boardroom'), y0 + 49, k + 2, tag, th);
  for (const n of ['West quiet room I', 'West quiet room II', 'East quiet room I', 'East quiet room II']) quiet(by(n), by(n).r0 - 1, k, tag, th);
  library(by('Library'), y0 + 49, k, tag, th);
  corridorDressing(by('North gallery'), y0 + 14, tag);
  corridorDressing(by('South gallery'), null, tag);
  corridorDressing(by('West gallery'), null, tag);
  corridorDressing(by('East gallery'), null, tag);
  const court = rooms.find((r) => r.kind === 'atrium')!;
  if (th.court === 'garden') gardenCourt(court, tag, k);
  else rotundaCourt(court, tag, th);
  // forecourt plaza between the boulevard promenade and the entrance
  rect(x0 + 30, y0 - 2, x0 + 41, y0 - 1, T.COBBLE);
  place('DETAIL_PLANTER', x0 + 28, y0 - 2, { tag, on: IN });
  place('DETAIL_PLANTER', x0 + 42, y0 - 2, { tag, on: IN });
  area(x0 - 4, S0 + 8, x0 + W - 1, 299, th.label);
  BUILDINGS.push({ label: th.label, frame: [[x0, y0, x0 + W - 1, y0 + H - 1]], rooms, entrances: [{ c: x0 + 35, r: y0 }] });
}

const ATRIUM: Theme = {
  label: 'Botanical Atrium',
  wall: c4(150, 30, -92, -40),
  lobbyFloor: T.CHECKER,
  lobbyColour: c4(90, 18, -2, 0),
  corridorFloor: T.PARQUET,
  corridorColour: c4(34, 38, -10, 0),
  roomFloor: T.F3,
  roomColour: c4(95, 20, -14, -10),
  quietFloor: T.PARQUET,
  quietColour: c4(30, 44, -18, 0),
  carpetMain: c4(120, 34, -26, 0),
  carpetAccent: c4(48, 26, 14, 0),
  carpetSecond: c4(90, 30, -32, 0),
  court: 'garden',
};
const OBSERVATORY: Theme = {
  label: 'Deco Observatory',
  wall: c4(228, 40, -96, -40),
  lobbyFloor: T.F9,
  lobbyColour: c4(45, 30, -40, 10),
  corridorFloor: T.F7,
  corridorColour: c4(222, 25, -40, -10),
  roomFloor: T.PARQUET,
  roomColour: c4(24, 40, -30, 0),
  quietFloor: T.F2,
  quietColour: c4(220, 20, -30, -10),
  carpetMain: c4(222, 46, -32, 0),
  carpetAccent: c4(45, 60, 6, 0),
  carpetSecond: c4(205, 40, -24, 0),
  court: 'rotunda',
};
complex(4, S0 + 10, ATRIUM, 1);
complex(80, S0 + 10, OBSERVATORY, 2);

// ════════════════════════════════════════════════════════════════════════════
// 3. THE CATHEDRAL QUARTER — walkable Gothic architecture (G01 motifs + r4 tiles)
// ════════════════════════════════════════════════════════════════════════════
const QA = JSON.parse(fs.readFileSync(path.join(ROOT, 'packaging/di/LIVING-WORLD-ART-QA.json'), 'utf-8'));
const G01 = QA.sheets.find((s: { id: string }) => s.id === 'G01');
const ARCH_COLS: number[] = G01?.portalArchTileColumns ?? [];
if (ARCH_COLS.length < 3) throw new Error('G01 portal arch columns missing — run prepare-living-world-art first');
const STONE = c4(38, 14, -40, -20);
const NAVE_FLOOR = c4(38, 14, -4, 0);
const AISLE_FLOOR = c4(32, 16, -12, 0);
const RUBY = c4(350, 60, -16, 0);
const COBALT = c4(222, 62, -18, 0);
const EMERALD = c4(140, 50, -22, 0);
const AMBER = c4(40, 70, -4, 0);
const GLASS = [RUBY, COBALT, AMBER, EMERALD];
{
  const tag = 'cathedral';
  const Q = { c0: 156, r0: S0 + 8, c1: 247, r1: 299 };
  // three places: the grounds, the interior and the parvis (residents walk between them, so the
  // portal and the transept doors carry real in-and-out traffic)
  area(Q.c0, Q.r0, Q.c1, Q.r1, 'Cathedral Quarter');
  area(172, 238, 231, 289, 'Cathedral');
  area(164, 252, 239, 265, 'Cathedral');
  area(166, 290, 237, 299, 'Cathedral Parvis');
  // grounds: lawn, the parvis in front of the west front (here the SOUTH front), paths
  rect(166, 290, 237, 299, T.COBBLE);
  rect(156, 258, 163, 259, T.COBBLE);
  rect(240, 258, 247, 259, T.COBBLE);
  rect(198, Q.r0, 205, Q.r0 + 4, T.COBBLE); // from the boulevard to the cloister garden
  // the building: body 172..231 x 238..289, transept 164..239 x 252..265
  const frame: Array<[number, number, number, number]> = [[172, 238, 231, 289], [164, 252, 239, 265]];
  for (const [a, b, c, d] of frame) rect(a, b, c, d, T.WALL, STONE);
  const rooms: Room[] = [];
  const room = (name: string, kind: Room['kind'], c0: number, r0: number, c1: number, r1: number, t: number, col: Colour) => {
    carve(c0, r0, c1, r1, t, col);
    rooms.push({ name, kind, c0, r0, c1, r1 });
  };
  room('Nave', 'nave', 188, 250, 215, 288, T.CHECKER, NAVE_FLOOR);
  room('West aisle', 'nave', 180, 250, 187, 280, T.COBBLE, AISLE_FLOOR);
  room('East aisle', 'nave', 216, 250, 223, 280, T.COBBLE, AISLE_FLOOR);
  room('Narthex west', 'corridor', 186, 281, 187, 288, T.COBBLE, AISLE_FLOOR);
  room('Narthex east', 'corridor', 216, 281, 217, 288, T.COBBLE, AISLE_FLOOR);
  room('Transept', 'transept', 166, 254, 237, 263, T.CHECKER, NAVE_FLOOR);
  room('Choir', 'choir', 188, 240, 215, 248, T.PARQUET, c4(30, 40, -22, 0));
  room('North-west chapel', 'chapel', 180, 241, 186, 248, T.CHECKER, NAVE_FLOOR);
  room('North-east chapel', 'chapel', 217, 241, 223, 248, T.CHECKER, NAVE_FLOOR);
  carve(182, 249, 184, 249, T.COBBLE, AISLE_FLOOR); // a doorway in a one-stone wall
  carve(219, 249, 221, 249, T.COBBLE, AISLE_FLOOR);
  // the choir screen (jube): stone across the head of the nave with a central opening
  rect(188, 249, 215, 249, T.WALL, STONE);
  carve(200, 249, 203, 249, T.PARQUET, c4(30, 40, -22, 0));
  room('Sacristy', 'sacristy', 173, 241, 178, 250, T.PARQUET, c4(30, 40, -22, 0));
  room('Treasury', 'sacristy', 225, 241, 230, 250, T.PARQUET, c4(30, 40, -22, 0));
  carve(179, 244, 179, 245, T.COBBLE, AISLE_FLOOR);
  carve(224, 244, 224, 245, T.COBBLE, AISLE_FLOOR);
  for (const [i, y] of [[1, 267], [2, 273], [3, 279]] as Array<[number, number]>) {
    room(`West chapel ${i}`, 'chapel', 173, y, 178, y + 3, T.CHECKER, NAVE_FLOOR);
    carve(179, y + 1, 179, y + 2, T.COBBLE, AISLE_FLOOR);
    room(`East chapel ${i}`, 'chapel', 225, y, 230, y + 3, T.CHECKER, NAVE_FLOOR);
    carve(224, y + 1, 224, y + 2, T.COBBLE, AISLE_FLOOR);
  }
  // doors: the open triple portal on the south front (walkable through every archway), transept doors
  const PORTAL_X = 197;
  const FACADE_ROW = 289;
  const entrances: Array<{ c: number; r: number }> = [];
  for (const a of ARCH_COLS) {
    carve(PORTAL_X + a, FACADE_ROW, PORTAL_X + a, FACADE_ROW, T.COBBLE, AISLE_FLOOR);
    entrances.push({ c: PORTAL_X + a, r: FACADE_ROW });
  }
  carve(164, 258, 165, 259, T.COBBLE, AISLE_FLOOR);
  carve(238, 258, 239, 259, T.COBBLE, AISLE_FLOOR);
  entrances.push({ c: 164, r: 258 }, { c: 239, r: 258 });
  // facade: portal, rose above it, twin towers on solid tower bases
  overlay('CATHEDRAL_PORTAL', PORTAL_X, FACADE_ROW - 6, tag);
  overlay('CATHEDRAL_ROSE_WINDOW', 200, FACADE_ROW - 11, tag);
  overlay('CATHEDRAL_TOWER_TOP', 180, FACADE_ROW - 9, tag);
  overlay('CATHEDRAL_TOWER_TOP', 218, FACADE_ROW - 9, tag);
  // interior: high altar, choir stalls, nave pews, arcade columns, crossing medallion, runner
  place('CATHEDRAL_ALTAR', 200, 240, { tag, on: IN });
  for (const x of [198, 205]) place('GATEWAY_BRAZIER', x, 241, { tag, on: IN });
  // Gothic pews: continuous dark-timber runs (procedural CATHEDRAL_PEW_L/M/R; decor, never seats)
  // aligned to the nave axis either side of the central aisle; choir stalls the same, facing the altar
  const pew = (x0: number, x1: number, solidRow: number) => {
    for (let x = x0; x <= x1; x++) place(x === x0 ? 'CATHEDRAL_PEW_L' : x === x1 ? 'CATHEDRAL_PEW_R' : 'CATHEDRAL_PEW_M', x, solidRow - 1, { tag, on: IN });
  };
  for (const y of [246, 248]) {
    pew(190, 196, y);
    pew(207, 213, y);
  }
  for (let y = 267; y <= 285; y += 2) {
    pew(190, 198, y);
    pew(205, 213, y);
  }
  carpet(200, 250, 203, 288, 0, RUBY, c4(45, 60, 6, 0), 2);
  carpet(196, 255, 207, 262, 2, COBALT, c4(45, 60, 6, 0), 1);
  for (const y of [251, 253, 264, 268, 272, 276])
    for (const x of [187, 216]) place('CATHEDRAL_COLUMN', x, y - 3, { tag, on: IN });
  for (const x of [176, 227]) for (const y of [256, 261]) place('CATHEDRAL_COLUMN', x, y - 3, { tag, on: IN });
  // stained glass: lancets on the choir and chapel walls, roses on the apse and transept walls, with
  // procedural coloured light pools on the floor beneath (engineering light, not baked art)
  const pool = (c: number, r: number, w: number, h: number, colour: Colour) => {
    for (let y = r; y < r + h; y++) for (let x = c; x < c + w; x++) if (tiles[idx(x, y)] !== T.WALL) tileColors[idx(x, y)] = colour;
  };
  let g = 0;
  for (const x of [190, 193, 196, 207, 210, 213]) {
    overlay('CATHEDRAL_LANCET', x, 239 - 2, tag);
    pool(x, 240, 1, 2, GLASS[g++ % 4]);
  }
  overlay('CATHEDRAL_ROSE_WINDOW', 200, 239 - 4, tag);
  overlay('CATHEDRAL_ROSE_WINDOW', 167, 253 - 4, tag);
  overlay('CATHEDRAL_ROSE_WINDOW', 233, 253 - 4, tag);
  pool(167, 254, 4, 3, COBALT);
  pool(233, 254, 4, 3, RUBY);
  for (const y of [267, 273, 279]) {
    for (const x of [175, 227]) {
      overlay('CATHEDRAL_LANCET', x, y - 1 - 2, tag);
      pool(x - 1, y, 3, 2, GLASS[g++ % 4]);
    }
    place('LARGE_PAINTING', 176, y - 2, { tag, on: IN }) || placeWall('LARGE_PAINTING', 176, y - 1, tag);
    place('GATEWAY_BRAZIER', 173, y + 1, { tag, on: IN });
    place('GATEWAY_BRAZIER', 230, y + 1, { tag, on: IN });
    place('LOUNGE_CUSHIONED_BENCH', 176, y + 2, { tag, on: IN });
    place('LOUNGE_CUSHIONED_BENCH', 227, y + 2, { tag, on: IN });
  }
  for (const [x, y] of [[180, 242], [222, 242]]) place('DETAIL_SCULPTURE', x, y, { tag, on: IN });
  placeWall('DOUBLE_BOOKSHELF', 174, 240, tag);
  placeWall('DOUBLE_BOOKSHELF', 226, 240, tag);
  place('AQUA_TREASURE', 227, 246, { tag, on: IN });
  for (const x of [168, 172, 231, 235]) place('DETAIL_LAMP_ORNATE', x, 254, { tag, on: IN });
  // grounds dressing: parvis fountains, lamps, statues, trees; cloister garden north of the apse
  for (const x of [176, 222]) place('DETAIL_FOUNTAIN_TIERED', x, 293, { tag, on: IN });
  for (let x = 170; x <= 232; x += 8) if (x < 194 || x > 210) place('DETAIL_LAMP_ORNATE', x, 290, { tag, on: IN });
  for (const x of [189, 213]) place('DETAIL_SCULPTURE', x, 296, { tag, on: IN });
  for (const x of [160, 166, 236, 242]) for (const y of [236, 244, 270, 280]) place(y % 2 ? 'FOREST_OAK_SMALL' : 'FOREST_BIRCH', x, y, { tag, on: ON(T.GRASS) });
  for (const x of [178, 186, 216, 224]) place('DETAIL_GARDEN_ARCH_P1', x, Q.r0, { tag, on: ON(T.GRASS) });
  place('DETAIL_OBELISK', 194, Q.r0 + 1, { tag, on: ON(T.GRASS) });
  place('DETAIL_OBELISK', 208, Q.r0 + 1, { tag, on: ON(T.GRASS) });
  for (let n = 0; n < 40; n++) {
    const R = rng(7300 + n);
    place(['FOREST_FLOWERS', 'FOREST_BUSH', 'DETAIL_PLANTER'][n % 3], Q.c0 + Math.floor(R() * (Q.c1 - Q.c0)), Q.r0 + Math.floor(R() * 6), { tag, on: ON(T.GRASS) });
  }
  BUILDINGS.push({ label: 'Cathedral', frame, rooms, entrances });
}

// ════════════════════════════════════════════════════════════════════════════
// 4. MARKET BOULEVARD — Target, Walmart, parking, dining, fountain plaza, landmarks
// ════════════════════════════════════════════════════════════════════════════
const PARKED: string[] = [];
{
  const tag = 'market';
  const M = { c0: 252, r0: S0 + 8, c1: EAST_EDGE - 1, r1: 299 };
  rect(M.c0, M.r0, M.c1, M.r1, T.PAVEMENT);
  area(M.c0, M.r0, M.c1, M.r1, 'Market Boulevard');
  // Target with its forecourt; Walmart with its forecourt
  place('BIGBOX_TARGET', 253, 234, { tag, on: ON(T.PAVEMENT) });
  place('BIGBOX_TARGET_FORECOURT', 263, 234, { tag, on: ON(T.PAVEMENT) });
  area(253, 234, 268, 240, 'Target');
  place('BIGBOX_WALMART', 253, 250, { tag, on: ON(T.PAVEMENT) });
  place('BIGBOX_WALMART_FORECOURT', 263, 250, { tag, on: ON(T.PAVEMENT) });
  area(253, 250, 268, 256, 'Walmart');
  // parking lots with parked cars (front/rear facing bays)
  const lot = (c0: number, r0: number, c1: number, r1: number, seed: number) => {
    rect(c0, r0, c1, r1, T.ASPHALT);
    for (let c = c0 + 1; c <= c1 - 1; c += 3) {
      paint(c, r0 + 1, T.DASH_NS);
      paint(c, r1 - 1, T.DASH_NS);
    }
    const R = rng(seed);
    const cars = ['TAXI', 'HATCHBACK_RED', 'SEDAN_BLUE', 'SUV_SILVER'];
    for (let c = c0 + 2; c + 1 <= c1 - 1; c += 3) {
      if (R() < 0.75) {
        const id = `VEHICLE_${cars[Math.floor(R() * 4)]}_DOWN`;
        if (place(id, c, r0, { tag: 'traffic-parking', on: ROAD })) PARKED.push(id);
      }
      if (R() < 0.75) {
        const id = `VEHICLE_${cars[Math.floor(R() * 4)]}_UP`;
        if (place(id, c, r1 - entryOf(id).h + 1, { tag: 'traffic-parking', on: ROAD })) PARKED.push(id);
      }
    }
  };
  lot(272, 234, 306, 240, 51);
  lot(272, 250, 306, 256, 52);
  // the emergency fleet's decorative bays beside the lots (parked, no response semantics)
  for (const [id, c] of [['VEHICLE_FIRE_ENGINE_DOWN', 273], ['VEHICLE_AMBULANCE_DOWN', 276], ['VEHICLE_POLICE_CRUISER_DOWN', 279], ['VEHICLE_POLICE_CRUISER_UP', 297], ['VEHICLE_FIRE_ENGINE_UP', 300], ['VEHICLE_AMBULANCE_UP', 303]] as Array<[string, number]>) {
    rect(c, 243, c + 1, 246, T.ASPHALT);
    if (place(id, c, 246 - entryOf(id).h + 1, { tag: 'traffic-parking', on: ROAD })) PARKED.push(id);
  }
  // dining row with terraces
  let x = 253;
  for (const t of ['CITY_CAFE', 'CITY_BAKERY', 'CITY_MARKET_HALL', 'CITY_CAFE']) {
    if (place(t, x, 262, { tag, on: ON(T.PAVEMENT) })) x += entryOf(t).w + 1;
  }
  for (let c = 254; c <= 284; c += 4) place('DETAIL_CAFE_TABLE', c, 269, { tag, on: ON(T.PAVEMENT) });
  place('DETAIL_KIOSK', 286, 266, { tag, on: ON(T.PAVEMENT) });
  place('DETAIL_KIOSK', 290, 266, { tag, on: ON(T.PAVEMENT) });
  // Grand Fountain Plaza with landmarks
  rect(262, 274, 306, 297, T.COBBLE);
  place('DETAIL_FOUNTAIN_SQUARE', 283, 284, { tag, on: ON(T.COBBLE) });
  for (const [cx, cy] of [[274, 278], [292, 278], [274, 291], [292, 291]]) place('DETAIL_FOUNTAIN_TIERED', cx, cy, { tag, on: ON(T.COBBLE) });
  place('DETAIL_OBELISK', 283, 278, { tag, on: ON(T.COBBLE) });
  place('DETAIL_SCULPTURE', 268, 284, { tag, on: ON(T.COBBLE) });
  place('DETAIL_SCULPTURE', 298, 284, { tag, on: ON(T.COBBLE) });
  place('CITY_LIBRARY', 253, 276, { tag, on: ON(T.PAVEMENT, T.COBBLE) });
  place('CITY_OBSERVATORY', 299, 289, { tag, on: ON(T.PAVEMENT, T.COBBLE) }) || place('CITY_OBSERVATORY', 253, 289, { tag, on: ON(T.PAVEMENT, T.COBBLE) });
  for (let c = 264; c <= 304; c += 6) {
    place('DETAIL_LAMP_ORNATE', c, 274, { tag, on: ON(T.COBBLE) });
    place('DETAIL_PARK_BENCH', c + 2, 296, { tag, on: ON(T.COBBLE) });
  }
  for (const [cx, cy] of [[266, 287], [300, 280], [278, 296], [288, 296]]) place('FOREST_OAK_SMALL', cx, cy, { tag, on: ON(T.COBBLE, T.PAVEMENT) });
  place('DETAIL_PHONE_BOOTH', 270, 266, { tag, on: ON(T.PAVEMENT) });
  place('DETAIL_HYDRANT', 252, 248, { tag, on: ON(T.PAVEMENT) });
  place('DETAIL_BIKE_RACK', 262, 248, { tag, on: ON(T.PAVEMENT) });
}

// ════════════════════════════════════════════════════════════════════════════
// 4b. THE SUBWAY LINE (S3): two tracks along the south edge (row 306 eastbound, row 308 westbound),
// an island platform between them at each of four stations, reached from the north platform by a
// boarded level crossing over the eastbound track. Termini at Atrium (west) and Market (east), where
// trains cross over between the tracks. The transit runtime derives the line from this data: track
// rows from SUBWAY_RAIL, stations from areas named '... Station'. Rails and track bed block, so
// pedestrians cross only at the level crossings.
// ════════════════════════════════════════════════════════════════════════════
const TRACK_E = 306;
const ISLAND = 307;
const TRACK_W = 308;
const TRACK_X0 = 22;
const TRACK_X1 = 302;
const STATIONS = [
  { name: 'Atrium Station', c: 40, house: 'SUBWAY_HEADHOUSE_CONSERVATORY' },
  { name: 'Observatory Station', c: 116, house: 'SUBWAY_HEADHOUSE_DECO' },
  { name: 'Cathedral Station', c: 201, house: 'SUBWAY_HEADHOUSE_BRICK' },
  { name: 'Market Station', c: 284, house: null },
];
const PLATFORM_HALF = 12;
{
  const tag = 'subway';
  rect(4, 304, EAST_EDGE - 1, 304, T.PAVEMENT);
  rect(TRACK_X0, TRACK_E, TRACK_X1, TRACK_W, T.TRAIL, c4(28, 14, -30, 0));
  const onStation = (c: number) => STATIONS.find((st) => c >= st.c - PLATFORM_HALF && c <= st.c + PLATFORM_HALF - 1);
  for (const st of STATIONS) {
    rect(st.c - PLATFORM_HALF, 305, st.c + PLATFORM_HALF - 1, 305, T.PAVEMENT);
    rect(st.c - PLATFORM_HALF, ISLAND, st.c + PLATFORM_HALF - 1, ISLAND, T.PAVEMENT);
    rect(st.c - 1, TRACK_E, st.c, TRACK_E, T.BOARD); // the level crossing
  }
  for (let c = TRACK_X0; c <= TRACK_X1; c++) {
    const st = onStation(c);
    const crossing = st && (c === st.c - 1 || c === st.c);
    if (!crossing) place('SUBWAY_RAIL', c, TRACK_E, { tag, on: ON(T.TRAIL) });
    place('SUBWAY_RAIL', c, TRACK_W, { tag, on: ON(T.TRAIL) });
    if (!st) place('SUBWAY_BALLAST', c, ISLAND, { tag, on: ON(T.TRAIL) });
  }
  for (const st of STATIONS) {
    area(st.c - 18, 298, st.c + 13, ROWS - 1, st.name);
    if (st.house) place(st.house, st.c - 18, 305 - (entryOf(st.house).h - 1), { tag, on: ON(T.PAVEMENT, T.GRASS) });
    else {
      place('SUBWAY_KIOSK', st.c - 16, 305 - (entryOf('SUBWAY_KIOSK').h - 1), { tag, on: ON(T.PAVEMENT, T.GRASS) });
      place('SUBWAY_ELEVATOR', st.c + PLATFORM_HALF, 305 - (entryOf('SUBWAY_ELEVATOR').h - 1), { tag, on: ON(T.PAVEMENT, T.GRASS) });
    }
    place('SUBWAY_TURNSTILES', st.c - 6, 305 - (entryOf('SUBWAY_TURNSTILES').h - 1), { tag, on: ON(T.PAVEMENT) });
    place('SUBWAY_TICKET_MACHINE', st.c - 8, 305 - (entryOf('SUBWAY_TICKET_MACHINE').h - 1), { tag, on: ON(T.PAVEMENT) });
    place('SUBWAY_ROUTE_BOARD', st.c + 3, 305 - (entryOf('SUBWAY_ROUTE_BOARD').h - 1), { tag, on: ON(T.PAVEMENT) });
    place('SUBWAY_WAITING_GROUP', st.c + 7, 305 - (entryOf('SUBWAY_WAITING_GROUP').h - 1), { tag, on: ON(T.PAVEMENT) });
    for (const x of [st.c - 11, st.c - 7, st.c + 2, st.c + 6]) overlay('SUBWAY_WARNING_STRIP', x, ISLAND - (entryOf('SUBWAY_WARNING_STRIP').h - 1), tag);
    place('SUBWAY_PLATFORM_SHELTER', st.c + 9, 303 - (entryOf('SUBWAY_PLATFORM_SHELTER').h - 1), { tag, on: ON(T.PAVEMENT, T.GRASS) });
  }
  // signal gantries over the tracks between stations (walk-behind art; the tracks stay clear)
  for (let k = 0; k + 1 < STATIONS.length; k++) {
    const mid = Math.round((STATIONS[k].c + STATIONS[k + 1].c) / 2) - 2;
    overlay('SUBWAY_GANTRY', mid, TRACK_W + 1 - (entryOf('SUBWAY_GANTRY').h - 1), tag);
  }
  for (let c = 8; c < EAST_EDGE - 4; c += 7) if (!onStation(c) && !onStation(c + 2)) place(c % 2 ? 'FOREST_OAK_SMALL' : 'FOREST_BIRCH', c, 309, { tag, on: ON(T.GRASS) });
}

// ════════════════════════════════════════════════════════════════════════════
// 5. TRAFFIC — static, lane-aligned queue clusters (parked props: no motion, no vehicle AI)
// ════════════════════════════════════════════════════════════════════════════
const JAMS: Array<{ row: number; c0: number; facing: string; vehicles: string[] }> = [];
function jam(row: number, cStart: number, facing: 'LEFT' | 'RIGHT', kinds: string[], tag: string): void {
  // vehicles queue nose to tail along lane `row`; every SOLID tile must be plain road (never a zebra
  // crossing), never within 1 tile of a crossing, never on an intersection, never occupied
  let c = cStart;
  const placed: string[] = [];
  for (const k of kinds) {
    const id = `VEHICLE_${k}_${facing}`;
    const e = entryOf(id);
    const solidRow = row;
    const top = solidRow - (e.h - 1);
    let ok = true;
    for (let dc = -1; dc <= e.w; dc++) {
      const t = tileAt(c + dc, solidRow);
      if (t === T.ZEBRA) ok = false;
      if (dc >= 0 && dc < e.w && !ROAD.has(t)) ok = false;
      // intersection: a road tile two rows beyond the lane on both sides means a crossing street
      if (dc >= 0 && dc < e.w && ROAD.has(tileAt(c + dc, solidRow - 2)) && ROAD.has(tileAt(c + dc, solidRow + 2))) ok = false;
    }
    if (ok && place(id, c, top, { tag, on: ROAD, solidOnly: true })) placed.push(id);
    c += facing === 'LEFT' ? e.w : e.w;
  }
  JAMS.push({ row, c0: cStart, facing, vehicles: placed });
}
// find the metropolis E-W roads (rows whose centre line is DASH_EW across the skyline)
const roadRows: number[] = [];
for (let r = 1; r < OLD_ROWS - 1; r++) {
  let n = 0;
  for (let c = 176; c < 308; c++) if (tiles[idx(c, r)] === T.DASH_EW) n++;
  if (n > 40) roadRows.push(r);
}
const TAXIS = ['TAXI', 'TAXI', 'SEDAN_BLUE', 'TAXI', 'SUV_SILVER', 'TAXI', 'HATCHBACK_RED', 'TAXI'];
/** Clear lane segments: plain road, at least 2 tiles from any crossing, no intersection, the vehicle's
 *  solid lane row unoccupied (the walk-behind roof row may overlap street dressing, as r4's taxis do). */
function laneSegments(row: number, c0: number, c1: number): Array<[number, number]> {
  const ok = (c: number) => {
    if (!ROAD.has(tileAt(c, row))) return false;
    for (let d = -2; d <= 2; d++) if (tileAt(c + d, row) === T.ZEBRA || tileAt(c + d, row - 1) === T.ZEBRA || tileAt(c + d, row + 1) === T.ZEBRA) return false;
    if (ROAD.has(tileAt(c, row - 2)) && ROAD.has(tileAt(c, row + 2))) return false;
    return !occupied[idx(c, row)];
  };
  const out: Array<[number, number]> = [];
  let start = -1;
  for (let c = c0; c <= c1 + 1; c++) {
    if (c <= c1 && ok(c)) {
      if (start < 0) start = c;
    } else if (start >= 0) {
      out.push([start, c - 1]);
      start = -1;
    }
  }
  return out.sort((p, q) => q[1] - q[0] - (p[1] - p[0]));
}
/** A jam: vehicles nose to tail filling the longest clear segments of a lane (up to `segments`). */
function autoJam(row: number, c0: number, c1: number, facing: 'LEFT' | 'RIGHT', kinds: string[], segments: number): void {
  for (const [a0, a1] of laneSegments(row, c0, c1).slice(0, segments)) {
    if (a1 - a0 + 1 < 8) continue;
    const queue: string[] = [];
    let c = a0 + 1;
    for (let k = 0; ; k++) {
      const id = `VEHICLE_${kinds[k % kinds.length]}_${facing}`;
      const e = entryOf(id);
      if (c + e.w - 1 > a1 - 1) break;
      if (place(id, c, row - (e.h - 1), { tag: 'traffic-jam', on: ROAD, solidOnly: true })) queue.push(id);
      c += e.w;
    }
    JAMS.push({ row, c0: a0, facing, vehicles: queue });
  }
}
// the skyline's roads: every E-W road through the tower district, both lanes, two clear stretches each
for (const r of roadRows) {
  autoJam(r - 1, 186, 306, 'LEFT', TAXIS, 2);
  autoJam(r + 1, 186, 306, 'RIGHT', ['TAXI', 'SEDAN_BLUE', 'TAXI', 'POLICE_CRUISER', 'TAXI', 'SUV_SILVER', 'TAXI', 'AMBULANCE', 'TAXI', 'HATCHBACK_RED'], 1);
}
// the Grand Boulevard
jam(S0 + 2, 120, 'LEFT', ['TAXI', 'SEDAN_BLUE', 'TAXI', 'TAXI', 'SUV_SILVER', 'TAXI', 'HATCHBACK_RED'], 'traffic-jam');
jam(S0 + 4, 160, 'RIGHT', ['TAXI', 'FIRE_ENGINE', 'TAXI', 'SEDAN_BLUE', 'TAXI', 'POLICE_CRUISER'], 'traffic-jam');
jam(S0 + 4, 46, 'RIGHT', ['SUV_SILVER', 'TAXI', 'HATCHBACK_RED', 'TAXI', 'AMBULANCE'], 'traffic-jam');

// every delivered vehicle sprite in use at least once: any facing the queues and lots did not use is
// parked along the Grand Boulevard's clear stretches (LEFT-facing in the westbound lane, RIGHT in the
// eastbound lane)
{
  const used = new Set(furniture.filter((f) => f.type.startsWith('VEHICLE_')).map((f) => f.type));
  const all = ['FIRE_ENGINE', 'POLICE_CRUISER', 'AMBULANCE', 'TAXI', 'HATCHBACK_RED', 'SEDAN_BLUE', 'SUV_SILVER'].flatMap((v) => ['LEFT', 'RIGHT'].map((f) => `VEHICLE_${v}_${f}`));
  for (const id of all.filter((t) => !used.has(t))) {
    const facing = id.endsWith('_LEFT') ? 'LEFT' : 'RIGHT';
    const row = facing === 'LEFT' ? S0 + 2 : S0 + 4;
    const e = entryOf(id);
    for (const [a0, a1] of laneSegments(row, 8, BLVD.c1 - 8)) {
      if (a1 - a0 + 1 < e.w + 4) continue;
      if (place(id, a0 + 2, row - (e.h - 1), { tag: 'traffic-jam', on: ROAD, solidOnly: true })) break;
    }
  }
}

// ════════════════════════════════════════════════════════════════════════════
// 6. TOWER DISTINCTNESS — every skyline slot a different design (same width, solid rows, bottom)
// ════════════════════════════════════════════════════════════════════════════
const TOWER_TYPES = new Set(['CITY_TOWER_ROUND', 'CITY_TOWER_GARDEN', 'CITY_CLOCK_TOWER', 'CITY_SKYSCRAPER_DECO', 'CITY_SKYSCRAPER_GLASS', 'CITY_SUPERTALL']);
const towerIdx = furniture.slice(0, R4_FURN).map((f, i) => [f, i] as const).filter(([f]) => TOWER_TYPES.has(f.type));
const censusBefore: Record<string, number> = {};
for (const [f] of towerIdx) censusBefore[f.type] = (censusBefore[f.type] ?? 0) + 1;
const slotOf = (t: string) => (t === 'CITY_TOWER_ROUND' || t === 'CITY_TOWER_GARDEN' ? 'six' : t === 'CITY_CLOCK_TOWER' ? 'clock' : 'seven');
const DESIGNS: Record<string, string[]> = {
  six: ['CITY_TOWER_ROUND', 'CITY_TOWER_V06', 'CITY_TOWER_V01', 'CITY_TOWER_V07', 'CITY_TOWER_V02', 'CITY_TOWER_V08', 'CITY_TOWER_V03', 'CITY_TOWER_V09', 'CITY_TOWER_V04', 'CITY_TOWER_V10', 'CITY_TOWER_V05', 'CITY_TOWER_V11', 'CITY_TOWER_GARDEN', 'CITY_SUPERTALL'],
  clock: ['CITY_CLOCK_TOWER', 'CITY_TOWER_V12', 'CITY_TOWER_V13', 'CITY_TOWER_V14', 'CITY_TOWER_V15', 'CITY_TOWER_V16'],
  sevenDeco: ['CITY_SKYSCRAPER_DECO', 'CITY_TOWER_V17'],
};
const swaps: Array<{ uid: string; from: string; to: string; at: [number, number]; now: [number, number] }> = [];
{
  const bySlot: Record<string, Array<(typeof towerIdx)[number]>> = { six: [], clock: [], sevenDeco: [] };
  for (const t of towerIdx) {
    const s = slotOf(t[0].type);
    if (s === 'seven') {
      if (t[0].type === 'CITY_SKYSCRAPER_DECO') bySlot.sevenDeco.push(t);
    } else bySlot[s].push(t);
  }
  for (const [slot, list] of Object.entries(bySlot)) {
    list.sort((a, b) => a[0].row - b[0].row || a[0].col - b[0].col);
    const designs = DESIGNS[slot];
    if (list.length > designs.length) throw new Error(`${slot}: ${list.length} slots but ${designs.length} designs`);
    list.forEach(([f, i], k) => {
      const to = designs[k];
      if (to === f.type) return;
      const eo = entryOf(f.type);
      const en = entryOf(to);
      const solidOld = eo.h - eo.bg;
      const solidNew = en.h - en.bg;
      if (en.w > eo.w || solidNew > solidOld) throw new Error(`${to} does not fit the ${f.type} slot`);
      const col = f.col + Math.floor((eo.w - en.w) / 2);
      const row = f.row + eo.h - en.h; // same bottom edge
      if (row < 0) throw new Error(`${to} would rise above the world at ${f.col},${f.row}`);
      swaps.push({ uid: f.uid, from: f.type, to, at: [f.col, f.row], now: [col, row] });
      furniture[i] = { ...f, type: to, col, row };
    });
  }
}
const censusAfter: Record<string, number> = {};
for (const [, i] of towerIdx) censusAfter[furniture[i].type] = (censusAfter[furniture[i].type] ?? 0) + 1;

// ════════════════════════════════════════════════════════════════════════════
// 6b. GALLERY OFFICE EXHIBITS (R5) — M01's sixteen distinct works in the r4 Gallery Office
// (252,13)-(278,35): galleries A (x 253-259) and B (x 261-267), cafe, lounge hall. Gallery paintings and
// sculptures are replaced IN PLACE (same uid: INTENTIONAL deltas, listed in the report); the urn,
// vitrine, fossil and placard are added. Palace decor, office art and street dressing are untouched.
// ════════════════════════════════════════════════════════════════════════════
const VENUE = { c0: 252, r0: 13, c1: 278, r1: 35 };
const exhibits = { replaced: [] as Array<{ uid: string; from: string; to: string; at: [number, number]; now: [number, number] }>, added: [] as Array<{ uid: string; type: string; at: [number, number] }> };
{
  const byUid = new Map(furniture.map((f, i) => [f.uid, i] as const));
  const clearOcc = (f: Furn) => {
    const e = entryOf(f.type);
    for (let dr = 0; dr < e.h; dr++) for (let dc = 0; dc < e.w; dc++) if (inWorld(f.col + dc, f.row + dr)) occupied[idx(f.col + dc, f.row + dr)] = 0;
  };
  const markOcc = (type: string, col: number, row: number) => {
    const e = entryOf(type);
    for (let dr = 0; dr < e.h; dr++) for (let dc = 0; dc < e.w; dc++) if (inWorld(col + dc, row + dr)) occupied[idx(col + dc, row + dr)] = 1;
  };
  const inVenue = (c: number, r: number) => c >= VENUE.c0 && c <= VENUE.c1 && r >= VENUE.r0 - 2 && r <= VENUE.r1;
  // [uid, new type, col, row-of-bottom-tile]
  const PLAN: Array<[string, string, number, number]> = [
    // two gallery pots step aside for the new hang (position only)
    ['mw-office-67', 'POT', 259, 22],
    ['mw-office-77', 'POT', 267, 21],
    ['mw-wall-58', 'MUSEUM_PAINTING_LANDSCAPE', 253, 13],
    ['mw-wall-59', 'MUSEUM_PAINTING_SEASCAPE', 256, 13],
    ['mw-wall-60', 'MUSEUM_PAINTING_ABSTRACT', 254, 24],
    ['mw-wall-105', 'MUSEUM_BOTANICAL_PAIR', 258, 24],
    ['mw-wall-68', 'MUSEUM_PAINTING_PORTRAIT', 262, 13],
    ['mw-wall-69', 'MUSEUM_TRIPTYCH', 265, 13],
    ['mw-wall-70', 'MUSEUM_TAPESTRY', 262, 24],
    ['mw-wall-106', 'MUSEUM_PAINTING_STILL_LIFE', 266, 24],
    ['mw-office-61', 'MUSEUM_BUST', 254, 18],
    ['mw-office-62', 'MUSEUM_BRONZE', 257, 19],
    ['mw-office-71', 'MUSEUM_ABSTRACT_STONE', 262, 18],
    ['mw-office-72', 'MUSEUM_DISPLAY_CASE', 265, 19],
  ];
  const olds = PLAN.map(([uid]) => {
    const i = byUid.get(uid);
    if (i === undefined) throw new Error(`exhibit plan: ${uid} not in the r4 world`);
    const f = furniture[i];
    if (!inVenue(f.col, f.row)) throw new Error(`exhibit plan: ${uid} is not in the Gallery Office`);
    return [i, f] as const;
  });
  for (const [, f] of olds) clearOcc(f);
  PLAN.forEach(([uid, type, col, bottom], k) => {
    const [i, f] = olds[k];
    const e = entryOf(type);
    const row = bottom - (e.h - 1);
    if (e.wall) for (let dc = 0; dc < e.w; dc++) if (tileAt(col + dc, bottom) !== T.WALL) throw new Error(`${type} at ${col},${bottom}: not on a wall`);
    for (let dr = 0; dr < e.h; dr++)
      for (let dc = 0; dc < e.w; dc++) if (occupied[idx(col + dc, row + dr)]) throw new Error(`${type} at ${col},${row} overlaps`);
    markOcc(type, col, row);
    furniture[i] = { ...f, type, col, row };
    exhibits.replaced.push({ uid, from: f.type, to: type, at: [f.col, f.row], now: [col, row] });
  });
  const ADD: Array<[string, number, number]> = [
    ['MUSEUM_URN', 258, 22],
    ['MUSEUM_VITRINE', 264, 21],
    ['MUSEUM_FOSSIL', 271, 29],
    ['MUSEUM_PLACARD', 271, 30],
  ];
  for (const [type, col, bottom] of ADD) {
    const e = entryOf(type);
    const row = bottom - (e.h - 1);
    for (let dr = 0; dr < e.h; dr++)
      for (let dc = 0; dc < e.w; dc++) {
        const c = col + dc;
        const r = row + dr;
        if (occupied[idx(c, r)] || tiles[idx(c, r)] === T.WALL) throw new Error(`${type} at ${col},${row} blocked`);
      }
    markOcc(type, col, row);
    const rec: Furn = { uid: `lw-exhibit-${++uidN}`, type, col, row };
    furniture.push(rec);
    exhibits.added.push({ uid: rec.uid, type, at: [col, row] });
  }
  const designs = new Set([...exhibits.replaced.map((x) => x.to), ...exhibits.added.map((x) => x.type)].filter((t) => t.startsWith('MUSEUM_')));
  if (designs.size !== 16) throw new Error(`exhibits: ${designs.size} distinct designs, expected 16`);
}

// ════════════════════════════════════════════════════════════════════════════
// 7. WALL-JOIN SWEEP over the FINAL world (r4 palace included)
// ════════════════════════════════════════════════════════════════════════════
// VOID cells that touch a wall are holes in masonry: they render as the black rectangle / wedges the
// human reported. Each VOID component touching a WALL becomes WALL (walkability is unchanged: VOID and
// WALL are both non-walkable). VOID components not touching any wall are left and counted.
const sweep = { voidComponents: 0, voidComponentsTouchingWall: 0, voidTilesFixed: 0, voidTilesLeft: 0, fixedR4Cells: [] as number[] };
{
  const seen = new Uint8Array(N);
  for (let s = 0; s < N; s++) {
    if (tiles[s] !== T.VOID || seen[s]) continue;
    sweep.voidComponents++;
    const comp: number[] = [];
    let touches = false;
    const q = [s];
    seen[s] = 1;
    while (q.length) {
      const p = q.pop()!;
      comp.push(p);
      const c = p % COLS;
      const r = (p - c) / COLS;
      for (const [dc, dr] of [[1, 0], [-1, 0], [0, 1], [0, -1]]) {
        const cc = c + dc;
        const rr = r + dr;
        if (!inWorld(cc, rr)) continue;
        const n = idx(cc, rr);
        if (tiles[n] === T.WALL) touches = true;
        if (tiles[n] === T.VOID && !seen[n]) {
          seen[n] = 1;
          q.push(n);
        }
      }
    }
    if (touches) {
      sweep.voidComponentsTouchingWall++;
      for (const p of comp) {
        // the wall colour of the nearest wall neighbour (the masonry it belongs to)
        const c = p % COLS;
        const r = (p - c) / COLS;
        let col: Colour | null = null;
        for (let d = 1; d < 6 && !col; d++)
          for (const [dc, dr] of [[d, 0], [-d, 0], [0, d], [0, -d]]) if (!col && inWorld(c + dc, r + dr) && tiles[idx(c + dc, r + dr)] === T.WALL) col = tileColors[idx(c + dc, r + dr)];
        tiles[p] = T.WALL;
        tileColors[p] = col ?? WALL_COLOUR;
        if (p < OLD_N) sweep.fixedR4Cells.push(p);
      }
      sweep.voidTilesFixed += comp.length;
    } else sweep.voidTilesLeft += comp.length;
  }
}
// ════════════════════════════════════════════════════════════════════════════
// 7b. CONNECTIVITY — product blocking rules (every footprint row below backgroundTiles blocks, wall
// and surface items included). Every cell reachable in r4 must stay reachable (unless a new item
// stands on it), and every walkable region of 6+ cells on the new ground must join the network. New
// items that cut a route are removed and counted (never r4 items).
// ════════════════════════════════════════════════════════════════════════════
function solidMask(list: Furn[]): Uint8Array {
  const m = new Uint8Array(N);
  for (const f of list) {
    const e = CATALOG.get(f.type.replace(/:left$/, ''));
    if (!e) continue;
    for (let dr = e.bg; dr < e.h; dr++)
      for (let dc = 0; dc < e.w; dc++) {
        const c = f.col + dc;
        const r = f.row + dr;
        if (inWorld(c, r)) m[idx(c, r)] = 1;
      }
  }
  return m;
}
const passable = (t: number) => t !== T.WALL && t !== T.VOID && !WATER.has(t);
function reach(tileArr: number[], solid: Uint8Array, rows: number, seed: number): Uint8Array {
  const seen = new Uint8Array(COLS * rows);
  if (!passable(tileArr[seed]) || solid[seed]) throw new Error('connectivity seed not walkable');
  const q = [seed];
  seen[seed] = 1;
  while (q.length) {
    const p = q.pop()!;
    const c = p % COLS;
    const r = (p - c) / COLS;
    for (const [dc, dr] of [[1, 0], [-1, 0], [0, 1], [0, -1]]) {
      const cc = c + dc;
      const rr = r + dr;
      if (cc < 0 || rr < 0 || cc >= COLS || rr >= rows) continue;
      const n = rr * COLS + cc;
      if (!seen[n] && passable(tileArr[n]) && !solid[n]) {
        seen[n] = 1;
        q.push(n);
      }
    }
  }
  return seen;
}
// ════════════════════════════════════════════════════════════════════════════
// R1 (M3 final polish) — CROSSING LANDINGS: street decor standing on the pavement cell where a zebra
// crossing lands (the cell just beyond each end, across the road) is moved ALONG the pavement to the
// nearest spot where its solid rows are free, off every landing, crossing and road, and not on any
// other item. Recorded by uid. (The r4 city carried 13 such crossings leading into a bush, lamp,
// planter, phone booth, bike rack, bin or bench.) Runs BEFORE the connectivity pass, which then proves
// no route was cut by a moved item.
// ════════════════════════════════════════════════════════════════════════════
const r1 = { crossings: 0, crossingsBlocked: 0, moved: [] as Array<{ uid: string; type: string; from: [number, number]; to: [number, number] }>, unmovable: [] as Array<{ uid: string; type: string; at: [number, number] }>, thresholdsFound: 0, thresholdsFixed: 0, thresholdCells: [] as Array<[number, number]>, trimmedCells: [] as Array<[number, number]> };
{
  const DECOR = /^(DETAIL_|FOREST_BUSH|FOREST_|POT$|PLANT|BIN|LARGE_PLANT)/;
  const zseen = new Uint8Array(N);
  const landings = new Set<number>();
  const zebraOrRoad = (i: number) => tiles[i] === T.ZEBRA || ROAD.has(tiles[i]);
  for (let i = 0; i < N; i++) {
    if (zseen[i] || tiles[i] !== T.ZEBRA) continue;
    const comp: number[] = [];
    const q = [i];
    zseen[i] = 1;
    while (q.length) {
      const p = q.pop()!;
      comp.push(p);
      const c = p % COLS;
      const r = (p - c) / COLS;
      for (const [dc, dr] of [[1, 0], [-1, 0], [0, 1], [0, -1]]) {
        if (!inWorld(c + dc, r + dr)) continue;
        const n = idx(c + dc, r + dr);
        if (!zseen[n] && tiles[n] === T.ZEBRA) {
          zseen[n] = 1;
          q.push(n);
        }
      }
    }
    r1.crossings++;
    const cs = comp.map((p) => p % COLS);
    const rs = comp.map((p) => Math.floor(p / COLS));
    const [c0, c1, r0, r1_] = [Math.min(...cs), Math.max(...cs), Math.min(...rs), Math.max(...rs)];
    // the crossing runs across the road: its two ends are the sides where the tiles are not road
    const vertical = [r0 - 1, r1_ + 1].every((r) => inWorld(c0, r) && !zebraOrRoad(idx(c0, r)));
    // a zebra COLUMN whose end lands on a WALL (a building corner against the road) is trimmed back to
    // road — the road's own tile and colour from the next cell beyond — when the crossing is 2+ wide
    let [k0, k1] = [c0, c1];
    if (vertical)
      for (const c of [c0, c1]) {
        if (k1 - k0 < 1) break;
        const intoWall = [r0 - 1, r1_ + 1].some((r) => tileAt(c, r) === T.WALL);
        const from = c === k1 ? c + 1 : c - 1;
        if (!intoWall || ![...Array(r1_ - r0 + 1).keys()].every((k) => ROAD.has(tileAt(from, r0 + k)))) continue;
        for (let r = r0; r <= r1_; r++) {
          const i = idx(c, r);
          tiles[i] = tiles[idx(from, r)];
          tileColors[i] = tileColors[idx(from, r)] ?? null;
          r1.trimmedCells.push([c, r]);
          if (i < OLD_N && !sweep.fixedR4Cells.includes(i)) sweep.fixedR4Cells.push(i);
        }
        if (c === k1) k1--;
        else k0++;
      }
    if (vertical) for (let c = k0; c <= k1; c++) for (const r of [r0 - 1, r1_ + 1]) landings.add(idx(c, r));
    else for (let r = r0; r <= r1_; r++) for (const c of [c0 - 1, c1 + 1]) if (inWorld(c, r)) landings.add(idx(c, r));
  }
  const solidCells = (f: Furn, col: number, row: number): number[] => {
    const e = CATALOG.get(f.type.replace(/:left$/, ''));
    if (!e) return [];
    const out: number[] = [];
    for (let dr = e.bg; dr < e.h; dr++) for (let dc = 0; dc < e.w; dc++) out.push(inWorld(col + dc, row + dr) ? idx(col + dc, row + dr) : -1);
    return out;
  };
  const blockedCrossings = new Set<number>();
  for (const L of landings) {
    const blockers = furniture.filter((f) => solidCells(f, f.col, f.row).includes(L));
    if (!blockers.length) continue;
    blockedCrossings.add(L);
    for (const f of blockers) {
      if (!DECOR.test(f.type)) {
        if (!r1.unmovable.some((u) => u.uid === f.uid)) r1.unmovable.push({ uid: f.uid, type: f.type, at: [f.col, f.row] });
        continue;
      }
      if (r1.moved.some((m) => m.uid === f.uid)) continue; // already moved for another landing
      const others = solidMask(furniture.filter((g) => g !== f));
      let done = false;
      // along the pavement first; then one or two rows back from the kerb (never onto the road)
      const tries: Array<[number, number]> = [];
      for (const dx of [1, -1, 2, -2, 3, -3, 4, -4, 5, -5, 6, -6, 7, -7, 8, -8]) tries.push([dx, 0]);
      for (const dy of [-1, 1, -2, 2]) for (const dx of [0, 1, -1, 2, -2, 3, -3, 4, -4]) tries.push([dx, dy]);
      for (const [dx, dy] of tries) {
        const cells = solidCells(f, f.col + dx, f.row + dy);
        if (cells.some((cc) => cc < 0 || landings.has(cc) || zebraOrRoad(cc) || !passable(tiles[cc]) || others[cc])) continue;
        r1.moved.push({ uid: f.uid, type: f.type, from: [f.col, f.row], to: [f.col + dx, f.row + dy] });
        f.col += dx;
        f.row += dy;
        done = true;
        break;
      }
      if (!done && !r1.unmovable.some((u) => u.uid === f.uid)) r1.unmovable.push({ uid: f.uid, type: f.type, at: [f.col, f.row] });
    }
  }
  r1.crossingsBlocked = blockedCrossings.size;
}
const SEED = idx(270, OLD_ROWS - 1); // aquarium-quarter pavement on r4's south edge: walkable in both worlds
const r4Reach = reach(R4.tiles, (() => {
  const m = solidMask(R4.furniture);
  return m;
})(), OLD_ROWS, SEED);
const connectivity = { rounds: 0, removed: [] as Array<{ type: string; col: number; row: number; uid: string }>, r4ReachableLost: 0, newGroundPocketCells: 0, newGroundPocketRegions: 0 };
for (let round = 0; round < 60; round++) {
  const solid = solidMask(furniture);
  const fin = reach(tiles, solid, ROWS, SEED);
  const bad = new Uint8Array(N);
  let nBad = 0;
  for (let i = 0; i < OLD_N; i++) if (r4Reach[i] && !solid[i] && passable(tiles[i]) && !fin[i]) {
    bad[i] = 1;
    nBad++;
  }
  // new-ground pockets of 6+ cells
  const seen = new Uint8Array(N);
  let pocketRegions = 0;
  let pocketCells = 0;
  for (let s = OLD_N; s < N; s++) {
    if (seen[s] || fin[s] || solid[s] || !passable(tiles[s])) continue;
    const comp: number[] = [];
    const q = [s];
    seen[s] = 1;
    while (q.length) {
      const p = q.pop()!;
      comp.push(p);
      const c = p % COLS;
      const r = (p - c) / COLS;
      for (const [dc, dr] of [[1, 0], [-1, 0], [0, 1], [0, -1]]) {
        const cc = c + dc;
        const rr = r + dr;
        if (!inWorld(cc, rr)) continue;
        const n = idx(cc, rr);
        if (!seen[n] && !fin[n] && !solid[n] && passable(tiles[n])) {
          seen[n] = 1;
          q.push(n);
        }
      }
    }
    if (comp.length >= 6) {
      pocketRegions++;
      pocketCells += comp.length;
      for (const p of comp) bad[p] = 1;
      nBad += comp.length;
    }
  }
  connectivity.rounds = round;
  connectivity.r4ReachableLost = 0;
  connectivity.newGroundPocketCells = pocketCells;
  connectivity.newGroundPocketRegions = pocketRegions;
  if (!nBad) break;
  // remove new items whose blocked cells touch both the network and a cut-off cell (else: touch a cut-off cell)
  const touching = (f: Furn, want: (n: number) => boolean) => {
    const e = entryOf(f.type);
    for (let dr = e.bg; dr < e.h; dr++)
      for (let dc = 0; dc < e.w; dc++)
        for (const [ddc, ddr] of [[1, 0], [-1, 0], [0, 1], [0, -1]]) {
          const c = f.col + dc + ddc;
          const r = f.row + dr + ddr;
          if (inWorld(c, r) && want(idx(c, r))) return true;
        }
    return false;
  };
  let cut = furniture.filter((f, i) => i >= R4_FURN && touching(f, (n) => bad[n] === 1) && touching(f, (n) => fin[n] === 1));
  if (!cut.length) cut = furniture.filter((f, i) => i >= R4_FURN && touching(f, (n) => bad[n] === 1));
  if (!cut.length) {
    connectivity.r4ReachableLost = nBad;
    break;
  }
  // remove the cheapest first: decor before desks/chairs/landmarks
  const rank = (f: Furn) => (/^(POT|PLANT|BIN|FOREST_|DETAIL_PLANTER|DETAIL_LAMP|LARGE_PLANT|COFFEE)/.test(f.type) ? 0 : entryOf(f.type).wall ? 1 : /^VEHICLE_/.test(f.type) ? 2 : 3);
  cut.sort((a, b) => rank(a) - rank(b));
  const minRank = rank(cut[0]);
  const drop = new Set(cut.filter((f) => rank(f) === minRank).map((f) => f.uid));
  for (let i = furniture.length - 1; i >= R4_FURN; i--) {
    if (drop.has(furniture[i].uid)) {
      connectivity.removed.push({ type: furniture[i].type, col: furniture[i].col, row: furniture[i].row, uid: furniture[i].uid });
      furniture.splice(i, 1);
    }
  }
}
if (connectivity.r4ReachableLost) throw new Error(`CONNECTIVITY: ${connectivity.r4ReachableLost} cells cut off with no removable new item`);

// FLOOR painted in a WALL-DARK colour (brightness <= -80 on a walkable floor) renders as a black
// rectangle — the r4 palace carries one such strip at (95-98, 79). Each takes the most common colour of
// its non-dark neighbours of the same floor type (colour only; the tile type is unchanged).
const darkFloor = { found: 0, fixed: 0, cells: [] as Array<[number, number]> };
for (let i = 0; i < N; i++) {
  const t = tiles[i];
  const col = tileColors[i];
  if (t === T.WALL || t === T.VOID || !col || col.b > -80) continue;
  darkFloor.found++;
  const c = i % COLS;
  const r = (i - c) / COLS;
  const votes = new Map<string, { n: number; col: Colour }>();
  for (let d = 1; d <= 4; d++)
    for (let dr = -d; dr <= d; dr++)
      for (let dc = -d; dc <= d; dc++) {
        if (!inWorld(c + dc, r + dr)) continue;
        const j = idx(c + dc, r + dr);
        const cj = tileColors[j];
        if (tiles[j] !== t || !cj || cj.b <= -80) continue;
        const k = JSON.stringify(cj);
        const v = votes.get(k) ?? { n: 0, col: cj };
        v.n++;
        votes.set(k, v);
      }
  const best = [...votes.values()].sort((a, b) => b.n - a.n)[0];
  if (!best) continue;
  tileColors[i] = best.col;
  darkFloor.fixed++;
  darkFloor.cells.push([c, r]);
  if (i < OLD_N) sweep.fixedR4Cells.push(i);
}
// R1 — THRESHOLDS: a doorway cell (walls on one axis, floor on the other) whose COLOUR equals neither
// side it joins takes the colour of the side sharing its floor PATTERN (the first side when both or
// neither do). Colour only; the tile type is unchanged. r4 cells changed this way join the recorded sweep.
{
  const isWall = (c: number, r: number) => tileAt(c, r) === T.WALL;
  const isFloor = (c: number, r: number) => {
    const t = tileAt(c, r);
    return t > 0 && t !== T.VOID;
  };
  const key = (i: number) => JSON.stringify(tileColors[i] ?? null);
  for (let r = 0; r < ROWS; r++)
    for (let c = 0; c < COLS; c++) {
      if (!isFloor(c, r)) continue;
      const h = isWall(c - 1, r) && isWall(c + 1, r) && isFloor(c, r - 1) && isFloor(c, r + 1);
      const v = isWall(c, r - 1) && isWall(c, r + 1) && isFloor(c - 1, r) && isFloor(c + 1, r);
      if (!h && !v) continue;
      const i = idx(c, r);
      const a = h ? idx(c, r - 1) : idx(c - 1, r);
      const b = h ? idx(c, r + 1) : idx(c + 1, r);
      if (key(i) === key(a) || key(i) === key(b)) continue;
      r1.thresholdsFound++;
      const side = tiles[i] === tiles[b] && tiles[i] !== tiles[a] ? b : a;
      tileColors[i] = tileColors[side] ?? null;
      r1.thresholdsFixed++;
      r1.thresholdCells.push([c, r]);
      if (i < OLD_N && !sweep.fixedR4Cells.includes(i)) sweep.fixedR4Cells.push(i);
    }
}
// thick-wall seam count (repaired at render time by wallTiles.ts thickSides): wall tiles with a south
// wall and an east (or west) neighbour that also runs south
let seamTiles = 0;
for (let r = 0; r < ROWS - 1; r++)
  for (let c = 0; c < COLS; c++) {
    if (tiles[idx(c, r)] !== T.WALL || tiles[idx(c, r + 1)] !== T.WALL) continue;
    const e = c + 1 < COLS && tiles[idx(c + 1, r)] === T.WALL && tiles[idx(c + 1, r + 1)] === T.WALL;
    const w = c > 0 && tiles[idx(c - 1, r)] === T.WALL && tiles[idx(c - 1, r + 1)] === T.WALL;
    if (e || w) seamTiles++;
  }

// ════════════════════════════════════════════════════════════════════════════
// 8. PRESERVATION (asserted) and the layout
// ════════════════════════════════════════════════════════════════════════════
const fixedSet = new Set(sweep.fixedR4Cells);
const same = (a: unknown, b: unknown) => JSON.stringify(a ?? null) === JSON.stringify(b ?? null);
let cellDiffs = 0;
for (let i = 0; i < OLD_N; i++) {
  if (fixedSet.has(i)) continue;
  if (tiles[i] !== R4.tiles[i] || !same(tileColors[i], R4.tileColors[i]) || !same(carpetTiles[i], R4.carpetTiles[i]) || !same(areaTiles[i], R4.areaTiles[i])) cellDiffs++;
}
if (cellDiffs) throw new Error(`PRESERVATION: ${cellDiffs} r4 cells changed outside the recorded sweep`);
const swapUids = new Set([...swaps.map((s) => s.uid), ...exhibits.replaced.map((x) => x.uid), ...r1.moved.map((m) => m.uid)]);
let furnDiffs = 0;
for (let i = 0; i < R4_FURN; i++) if (!swapUids.has(R4.furniture[i].uid) && !same(furniture[i], R4.furniture[i])) furnDiffs++;
if (furnDiffs) throw new Error(`PRESERVATION: ${furnDiffs} r4 furniture records changed outside the tower swaps, exhibits and R1 moves`);
const HOME = R4.home;
const inHome = (f: Furn) => f.col >= HOME.c0 && f.col < HOME.c1 && f.row >= HOME.r0 && f.row < HOME.r1;
const r4Pcs = (R4.furniture as Furn[]).filter((f) => /^PC_/.test(f.type));
if (r4Pcs.length !== 41) throw new Error(`r4 baseline should hold 41 computers, holds ${r4Pcs.length}`);
const byUid = new Map(furniture.map((f) => [f.uid, f]));
const pcsLost = r4Pcs.filter((f) => !same(byUid.get(f.uid), f));
if (pcsLost.length) throw new Error(`PRESERVATION: ${pcsLost.length} of the original 41 computers missing or changed`);
const r4HomeSeats = (R4.furniture as Furn[]).filter((f) => entryOf(f.type).category === 'chairs' && inHome(f));
const seatsLost = r4HomeSeats.filter((f) => !same(byUid.get(f.uid), f));
if (seatsLost.length) throw new Error(`PRESERVATION: ${seatsLost.length} original home seats missing or changed`);
const pcs = furniture.filter((f) => /^PC_/.test(f.type)).length;
const seatsOutside = furniture.filter((f) => entryOf(f.type).category === 'chairs' && !inHome(f));
const seatsOutsideByArea: Record<string, number> = {};
for (const f of seatsOutside) {
  const a = areaTiles[idx(f.col, f.row)] ?? 'unlabelled';
  seatsOutsideByArea[a] = (seatsOutsideByArea[a] ?? 0) + 1;
}
const carpetCells = carpetTiles.filter(Boolean).length;
if (carpetCells > 4096) throw new Error(`carpet cells ${carpetCells} over 4096`);

const NEW_AREAS: Array<[string, string]> = [
  ['Grand Boulevard', '#9a9a9a'],
  ['Botanical Atrium', '#50a060'],
  ['Deco Observatory', '#c8a040'],
  ['Cathedral Quarter', '#b8a888'],
  ['Cathedral', '#d8c8a0'],
  ['Cathedral Parvis', '#a89878'],
  ['Market Boulevard', '#d06050'],
  ['Target', '#cc0000'],
  ['Walmart', '#0071ce'],
  ['Atrium Station', '#5a8a9a'],
  ['Observatory Station', '#5a8a9a'],
  ['Cathedral Station', '#5a8a9a'],
  ['Market Station', '#5a8a9a'],
];
const layout = {
  version: R4.version,
  cols: COLS,
  rows: ROWS,
  tiles,
  tileColors,
  carpetTiles,
  furniture,
  pets: R4.pets,
  areas: [...R4.areas, ...NEW_AREAS.map(([label, color]) => ({ label, color }))],
  areaTiles,
  home: R4.home,
  layoutRevision: (R4.layoutRevision ?? 0) + 1,
};
fs.writeFileSync(OUT, JSON.stringify(layout));
const outSha = crypto.createHash('sha256').update(fs.readFileSync(OUT)).digest('hex');

// ════════════════════════════════════════════════════════════════════════════
// 9. METRICS — the same classifier for the palace and the new buildings
// ════════════════════════════════════════════════════════════════════════════
const solidAt = new Uint8Array(N);
for (const f of furniture) {
  const e = entryOf(f.type);
  if (e.surface || e.wall) continue;
  for (let dr = e.bg; dr < e.h; dr++) for (let dc = 0; dc < e.w; dc++) if (inWorld(f.col + dc, f.row + dr)) solidAt[idx(f.col + dc, f.row + dr)] = 1;
}
const walkable = (i: number) => tiles[i] !== T.WALL && tiles[i] !== T.VOID && !WATER.has(tiles[i]);
function measure(label: string, rects: Array<[number, number, number, number]>, rooms: Room[] | null) {
  const inside = new Uint8Array(N);
  for (const [a, b, c, d] of rects) for (let r = b; r <= d; r++) for (let cc = a; cc <= c; cc++) inside[idx(cc, r)] = 1;
  let footprint = 0;
  let floor = 0;
  let usable = 0;
  for (let i = 0; i < N; i++) {
    if (!inside[i]) continue;
    footprint++;
    if (walkable(i)) {
      floor++;
      if (!solidAt[i]) usable++;
    }
  }
  const items = furniture.filter((f) => inside[idx(f.col, Math.min(ROWS - 1, f.row + entryOf(f.type).h - 1))]);
  const pcs = items.filter((f) => /^PC_/.test(f.type)).length;
  const desks = items.filter((f) => f.type === 'DESK_FRONT').length;
  const seats = items.filter((f) => entryOf(f.type).category === 'chairs').length;
  // a workstation: a computer with a seat within two tiles (the palace's desk-bench and table-side patterns)
  const workstations = items.filter((f) => /^PC_/.test(f.type) && items.some((g) => entryOf(g.type).category === 'chairs' && Math.abs(g.col - f.col) <= 2 && Math.abs(g.row - f.row) <= 2)).length;
  // rooms: floor regions separated by walls and by door cells (a floor cell with walls on two
  // opposite sides); classified by contents, identically for every building
  // a door is an opening in a 1-thick wall: its floor run along the wall line is short (<= 8) and
  // the runs on both sides of the wall are longer (the rooms it joins)
  const open = (cc: number, rr: number) => inWorld(cc, rr) && tiles[idx(cc, rr)] !== T.WALL && tiles[idx(cc, rr)] !== T.VOID;
  const runH = (c: number, r: number) => {
    if (!open(c, r)) return 0;
    let a = c;
    let b = c;
    while (open(a - 1, r) && c - a < 40) a--;
    while (open(b + 1, r) && b - c < 40) b++;
    return b - a + 1;
  };
  const runV = (c: number, r: number) => {
    if (!open(c, r)) return 0;
    let a = r;
    let b = r;
    while (open(c, a - 1) && r - a < 40) a--;
    while (open(c, b + 1) && b - r < 40) b++;
    return b - a + 1;
  };
  const doorCache = new Map<number, boolean>();
  const door = (i: number) => {
    const hit = doorCache.get(i);
    if (hit !== undefined) return hit;
    const c = i % COLS;
    const r = (i - c) / COLS;
    let d = false;
    const h0 = runH(c, r);
    if (h0 <= 8 && open(c, r - 1) && open(c, r + 1) && runH(c, r - 1) > h0 + 1 && runH(c, r + 1) > h0 + 1) d = true;
    const v0 = runV(c, r);
    if (!d && v0 <= 8 && open(c - 1, r) && open(c + 1, r) && runV(c - 1, r) > v0 + 1 && runV(c + 1, r) > v0 + 1) d = true;
    doorCache.set(i, d);
    return d;
  };
  const seen = new Uint8Array(N);
  const regions: Array<{ cells: number; kind: string; bbox: [number, number, number, number] }> = [];
  for (let s = 0; s < N; s++) {
    if (!inside[s] || seen[s] || tiles[s] === T.WALL || tiles[s] === T.VOID || door(s)) continue;
    const q = [s];
    seen[s] = 1;
    const cells: number[] = [];
    while (q.length) {
      const p = q.pop()!;
      cells.push(p);
      const c = p % COLS;
      const r = (p - c) / COLS;
      for (const [dc, dr] of [[1, 0], [-1, 0], [0, 1], [0, -1]]) {
        const cc = c + dc;
        const rr = r + dr;
        if (!inWorld(cc, rr)) continue;
        const n = idx(cc, rr);
        if (!inside[n] || seen[n] || tiles[n] === T.WALL || tiles[n] === T.VOID || door(n)) continue;
        seen[n] = 1;
        q.push(n);
      }
    }
    if (cells.length < 6) continue;
    let c0 = COLS;
    let r0 = ROWS;
    let c1 = 0;
    let r1 = 0;
    const set = new Set(cells);
    for (const p of cells) {
      const c = p % COLS;
      const r = (p - c) / COLS;
      c0 = Math.min(c0, c);
      c1 = Math.max(c1, c);
      r0 = Math.min(r0, r);
      r1 = Math.max(r1, r);
    }
    const inRegion = (f: Furn) => set.has(idx(f.col, Math.min(ROWS - 1, f.row + entryOf(f.type).h - 1)));
    const its = items.filter(inRegion);
    const n = (re: RegExp) => its.filter((f) => re.test(f.type)).length;
    const wallArt = items.filter((f) => entryOf(f.type).wall && f.col >= c0 && f.col <= c1 && f.row + entryOf(f.type).h - 1 === r0 - 1).length;
    const thick = cells.length / Math.max(c1 - c0 + 1, r1 - r0 + 1);
    const touchesEntrance = cells.some((p) => {
      const c = p % COLS;
      const r = (p - c) / COLS;
      return [[0, -1], [0, 1], [-1, 0], [1, 0]].some(([dc, dr]) => {
        const cc = c + dc;
        const rr = r + dr;
        return inWorld(cc, rr) && door(idx(cc, rr)) && !inside[idx(cc + dc, rr + dr)];
      });
    });
    let kind = 'hall';
    if (thick <= 3.2) kind = 'corridor';
    else if (n(/^PC_/) + n(/^DESK_FRONT$/) >= 3) kind = 'workroom';
    else if (n(/^TABLE_FRONT$/) >= 1) kind = 'meeting room';
    else if (touchesEntrance && cells.length >= 150) kind = 'grand lobby';
    else if (cells.length <= 200 && (n(/SOFA|BOOKSHELF|LOUNGE_CUSHIONED_CHAIR/) >= 1 || wallArt >= 2)) kind = 'quiet room';
    regions.push({ cells: cells.length, kind, bbox: [c0, r0, c1, r1] });
  }
  const byKind: Record<string, number> = {};
  for (const g of regions) byKind[g.kind] = (byKind[g.kind] ?? 0) + 1;
  const designed: Record<string, number> = {};
  if (rooms) for (const r of rooms) designed[r.kind] = (designed[r.kind] ?? 0) + 1;
  return { label, footprintTiles: footprint, floorTiles: floor, usableFloorTiles: usable, furnishings: items.length, computers: pcs, desks, seats, workstations, roomsByClassifier: byKind, roomsAsDesigned: rooms ? designed : null, regions: regions.length };
}
const palace = measure('Palace (r4 home block 64 x 64)', [[HOME.c0, HOME.r0, HOME.c1 - 1, HOME.r1 - 1]], null);
const metrics = [palace, ...BUILDINGS.map((b) => measure(b.label, b.frame, b.rooms))];

// reachability of the new entrances is checked by the webview test suite on the shipped world; the
// builder records the doors it made
const report = {
  kind: 'living-metropolis world build (M3 S2, FORMATIVE)',
  generator: 'scripts/build-living-world.ts',
  input: { file: 'packaging/di/worlds/metropolis.json', sha256: IN_SHA, cols: COLS, rows: OLD_ROWS, furniture: R4_FURN },
  output: { file: path.relative(ROOT, OUT), sha256: outSha, bytes: fs.statSync(OUT).size, cols: COLS, rows: ROWS, cells: N, furniture: furniture.length, newFurniture: furniture.length - R4_FURN, carpetCells, areas: layout.areas.length },
  expansion: { addedRows: ADD_ROWS, addedCells: COLS * ADD_ROWS, rule: 'appended south; every r4 cell keeps its index' },
  preservation: { r4CellsChangedOutsideSweep: 0, r4FurnitureChangedOutsideTowerSwaps: 0, original41ComputersUnchangedByUid: true, originalHomeSeatsUnchangedByUid: r4HomeSeats.length, computersTotal: pcs, seatsOutsideHome: seatsOutside.length, seatsOutsideHomeByArea: seatsOutsideByArea, seatsOutsideHomeAssignable: false, seatRule: 'webview officeState homeSeats(): only chairs inside the home/deck living regions are seats; chairs elsewhere draw and collide but are never assigned to a real agent', pets: R4.pets.length, home: R4.home },
  connectivity: { ...connectivity, removedCount: connectivity.removed.length, removedByType: connectivity.removed.reduce((m: Record<string, number>, f) => ((m[f.type] = (m[f.type] ?? 0) + 1), m), {}) },
  r1: { ...r1, rule: 'crossing landings cleared by moving street decor along the pavement; doorway thresholds take the colour of the side sharing their floor pattern', deliberatelyLeft: [{ class: 'isolated wall', cells: 8, reason: 'designed colonnade piers between the north gallery and the atrium of both complexes (openings at every column except x%4==1)' }, { class: 'blocked doorway (by tiles)', cells: 4, reason: 'inherited palace paintings hung across two-cell wall gaps at (65-66,98) and (82-83,71); they read as wall with a painting, and the rooms behind are reachable through other doors (livingWorld room-level reachability)' }] },
  sweep: { ...sweep, darkFloor, fixedR4Cells: sweep.fixedR4Cells.length, fixedR4CellList: sweep.fixedR4Cells.slice(0, 400).map((p) => [p % COLS, Math.floor(p / COLS)]), thickWallSeamTiles: seamTiles, seamRepair: 'render-time (webview-ui/src/office/wallTiles.ts thickSides/seamFree)' },
  exhibits: { venue: VENUE, ...exhibits, distinctDesigns: 16, rule: 'replaced records keep their uid (INTENTIONAL deltas, listed); palace decor, office art and street dressing untouched' },
  towers: { censusBefore, censusAfter, instances: towerIdx.length, distinctDesignsAfter: Object.keys(censusAfter).length, swaps },
  traffic: {
    final: (() => {
      const v = furniture.filter((f) => f.type.startsWith('VEHICLE_'));
      const by = (re: RegExp) => v.filter((f) => re.test(f.uid)).length;
      const kinds: Record<string, number> = {};
      for (const f of v) kinds[f.type] = (kinds[f.type] ?? 0) + 1;
      return { vehicles: v.length, inJams: by(/^lw-traffic-jam-/), parked: by(/^lw-traffic-parking-/), distinctSprites: Object.keys(kinds).length, byType: kinds };
    })(),
    jamsAsPlaced: JAMS,
    jamVehiclesBeforeConnectivity: JAMS.reduce((n, j) => n + j.vehicles.length, 0),
    parked: PARKED.length, honestDescription: 'static parked props in lane-aligned queues and parking bays; no motion, no vehicle AI, no signals; emergency vehicles are decorative dressing with no response semantics' },
  buildings: BUILDINGS.map((b) => ({ label: b.label, frame: b.frame, entrances: b.entrances, rooms: b.rooms.map((r) => ({ name: r.name, kind: r.kind, rect: [r.c0, r.r0, r.c1, r.r1] })) })),
  metrics,
  placed: Object.fromEntries(placedByTag),
  refusedPlacements: Object.fromEntries(refused),
};
fs.writeFileSync(REPORT, `${JSON.stringify(report, null, 2)}\n`);
console.log(JSON.stringify({ connectivity: { rounds: connectivity.rounds, removed: connectivity.removed.length, byType: report.connectivity.removedByType, pocketRegions: connectivity.newGroundPocketRegions, pocketCells: connectivity.newGroundPocketCells }, out: report.output, sweep: { ...report.sweep, fixedR4CellList: undefined }, towers: { before: censusBefore, distinctAfter: report.towers.distinctDesignsAfter, swaps: swaps.length }, traffic: report.traffic.final, metrics, placed: report.placed, refused: report.refusedPlacements }, null, 1));
