/**
 * AMBIENT RESIDENTS — a local, decorative population that keeps the whole world inhabited while
 * the viewer is open, including the original palace and deck, at zero real agents.
 *
 * Separation from real agents (by construction):
 * - Residents live in this module's own list, never in OfficeState.characters, so every agent loop
 *   (seats, palette diversity, hit-testing, persistence, DI projection) is untouched by them.
 * - Residents NEVER sit in, stop at, or stand beside a seat: their stops exclude every seat tile and
 *   its neighbours. Real agents always win occupancy: a resident standing on an agent's tile or
 *   beside an ASSIGNED seat steps aside at once, and a resident never steps onto an agent's tile
 *   (it waits and re-plans; only resident-on-resident blocking may pass through).
 * - They carry a job label and nothing else: no Mission, activity, status, progress or authority
 *   meaning, and no route to any action. Nothing here sends a message or writes anything.
 *
 * Routing: ONE pedestrian graph over the whole world (ambientGraph.ts) for long legs, bounded A*
 * (ambientPath.ts) for short ones. Determinism: identities, jobs, posts and routines come from a
 * fixed seed and the world's data; positions advance with real frame time.
 */
import {
  AMBIENT_AGENT_BLOCK_REPLAN_SEC,
  AMBIENT_CONGESTION_PASS_SEC,
  AMBIENT_DECK_MIN,
  AMBIENT_DISTRICT_WEIGHT_EXPONENT,
  AMBIENT_DUTY_ROTATION_SEC,
  AMBIENT_DWELL_MAX_SEC,
  AMBIENT_DWELL_MIN_SEC,
  AMBIENT_HUB_SPACING,
  AMBIENT_IDLE_FRAME_SEC,
  AMBIENT_LEG_CACHE_PER_RESIDENT,
  AMBIENT_LOCAL_ROUTE_RADIUS,
  AMBIENT_PALACE_MIN,
  AMBIENT_PATH_MAX_EXPANSIONS,
  AMBIENT_PATH_MAX_LENGTH,
  AMBIENT_PATH_RETRY_SEC,
  AMBIENT_PATH_SEARCHES_PER_TICK,
  AMBIENT_PATROL_RADIUS_TILES,
  AMBIENT_POPULATION_CAP,
  AMBIENT_POPULATION_ORDINARY,
  AMBIENT_POST_DWELL_FACTOR,
  AMBIENT_RIDE_WAIT_MAX_SEC,
  AMBIENT_RIDER_KIND_WEIGHTS,
  AMBIENT_RIDER_REACH_TILES,
  AMBIENT_RIDERS_MAX,
  AMBIENT_ROAMING_DWELL_FACTOR,
  AMBIENT_ROUTE_MAX_LENGTH,
  AMBIENT_ROUTINE_MAX_STOPS,
  AMBIENT_ROUTINE_STOPS,
  AMBIENT_WALK_FRAME_DURATION_SEC,
  AMBIENT_WALK_SPEED_PX_PER_SEC,
  AMBIENT_WORKING_OFFICE_ON_DUTY,
  AMBIENT_WORKING_OFFICE_TILES_PER_RESIDENT,
  AMBIENT_WORLD_SEED,
  AMBIENT_YIELD_RADIUS,
  CHARACTER_HIT_HALF_WIDTH,
  CHARACTER_HIT_HEIGHT,
  WATER_FLOOR_TILES,
} from '../../constants.js';
import type { TileRect } from '../layout/worldViews.js';
import { districtsOf, inRegions } from '../layout/worldViews.js';
import type { TransitLine } from '../transit/transitWorld.js';
import type { OfficeLayout, TileType as TileTypeVal } from '../types.js';
import { Direction, TILE_SIZE, TileType } from '../types.js';
import { PedestrianGraph } from './ambientGraph.js';
import type { AmbientJobId } from './ambientJobs.js';
import {
  AMBIENT_EGGS,
  AMBIENT_JOBS,
  jobsForDistrict,
  REQUIRED_AMBIENT_JOBS,
} from './ambientJobs.js';
import type { WalkMask } from './ambientPath.js';
import { AmbientPathfinder } from './ambientPath.js';
import { hashString, SeededRng } from './ambientRng.js';

export type AmbientState = 'dwell' | 'need-path' | 'walk' | 'retry' | 'waiting' | 'riding';

/** What a subway rider's day is: to a station at least two along (commute), to the next one (hop), or round three. */
export type RiderKind = 'commute' | 'hop' | 'excursion';

/** A rider's journey plan as the world derived it: its kind and the stations it rides to, in order, then home to the first. */
export interface RiderJourney {
  readonly id: string;
  readonly kind: RiderKind;
  /** Station indices: the one it boards at first, then each it rides to. */
  readonly stations: readonly number[];
}

export interface AmbientResident {
  /** Durable local identity: derived from the seed, place and job, stable across loads. */
  readonly id: string;
  readonly job: AmbientJobId;
  /** Home place: a district label, or Palace / Deck / Forest. */
  readonly district: string;
  readonly palette: number;
  readonly hueShift: number;
  /** Post first, then patrol stops, then (roaming jobs) one stop in another place. Tile indices. */
  readonly routine: Int32Array;
  /**
   * RIDES (subway commuters only): rides[i] >= 0 means that on reaching routine[i] (a station's
   * island platform) the resident waits for a train to station rides[i]; it alights at routine[i + 1].
   */
  readonly rides?: Int8Array;
  routineIndex: number;
  state: AmbientState;
  tileCol: number;
  tileRow: number;
  /** Pixel position (tile centre while standing). */
  x: number;
  y: number;
  dir: Direction;
  /** Current leg: tile indices, walked from routePos. */
  route: Int32Array;
  routePos: number;
  moveProgress: number;
  frame: number;
  frameTimer: number;
  /** Dwell countdown ('dwell') or retry countdown ('retry'). */
  timer: number;
  /** Seconds blocked before the current step (congestion). */
  blocked: number;
  legsWalked: number;
  /** Distinct places entered so far (cross-world walking evidence). */
  readonly placesVisited: Set<string>;
  readonly rng: SeededRng;
  /** Cached legs keyed "from>to" routine indices; at most AMBIENT_LEG_CACHE_PER_RESIDENT. */
  readonly legCache: Map<string, Int32Array>;
}

export interface AmbientStats {
  searches: number;
  localRoutes: number;
  graphRoutes: number;
  found: number;
  budgetHits: number;
  failures: number;
  maxExpanded: number;
  maxHubsSettled: number;
  cacheHits: number;
  congestionWaits: number;
  passThroughs: number;
  agentYields: number;
  agentBlockReplans: number;
}

/** A seat as the ambient world needs it (OfficeState.seats values). */
export interface AmbientSeat {
  uid: string;
  seatCol: number;
  seatRow: number;
}

/** Real-agent context for one tick: residents always give way to these. */
export interface AmbientAgentContext {
  /** Tile indices real agents stand on or are about to step onto. */
  agentTiles: ReadonlySet<number>;
  /** Seats currently assigned to real agents. */
  assignedSeats: readonly string[];
}

export interface AmbientBuildInput {
  layout: Pick<OfficeLayout, 'cols' | 'rows'> & Partial<Pick<OfficeLayout, 'areas' | 'areaTiles'>>;
  tileMap: TileTypeVal[][];
  blockedTiles: Set<string>;
  /** The home office then (optionally) the deck: they become the Palace and Deck places. */
  homeRegions: readonly TileRect[] | null;
  /** Every seat (residents keep clear of all of them). */
  seats?: readonly AmbientSeat[];
  seed?: number;
  /** Requested population; clamped to [0, AMBIENT_POPULATION_CAP]. */
  population?: number;
  /** The subway line, when the world has one: a bounded set of residents commute on it. */
  transit?: TransitLine | null;
  /** At most this many residents of a WORKING OFFICE (a pool holding a seat) are on duty at once; default AMBIENT_WORKING_OFFICE_ON_DUTY. */
  onDutyCap?: number;
  /** A working office shows at most ceil(T / this) on duty, T being its pool's resident-eligible tiles (`pool.tiles.length`); 0 or less: no density bound, only the cap. Default AMBIENT_WORKING_OFFICE_TILES_PER_RESIDENT. */
  onDutyTilesPerResident?: number;
}

/** One resident's validated restore step (AmbientWorld.planRestore / commitRestore). */
export interface RestorePlan {
  r: AmbientResident;
  routineIndex: number;
  tile: number;
  state: AmbientState;
  timer: number;
}

const HUE_SHIFTS = [0, 0, 0, 45, 90, 135, 180, 225, 270, 315] as const;
const MIN_POOL_TILES = 24;
/** The original forest quadrant (the vast-forest world sits unchanged in the top-left corner). */
const FOREST_LABEL = 'Forest';
const FOREST_COLS = 176;
const FOREST_ROWS = 112;
const NEIGHBOURS: ReadonlyArray<readonly [number, number]> = [
  [0, -1],
  [0, 1],
  [-1, 0],
  [1, 0],
];

function slug(label: string): string {
  return label
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-|-$/g, '');
}

function directionBetween(fc: number, fr: number, tc: number, tr: number): Direction {
  if (tc > fc) return Direction.RIGHT;
  if (tc < fc) return Direction.LEFT;
  if (tr > fr) return Direction.DOWN;
  return Direction.UP;
}

/** The ambient walk mask: walkable floor (never wall, void or water), not blocked by furniture,
 *  and outside any `excluded` region (none by default since C1). */
export function buildWalkMask(
  cols: number,
  rows: number,
  tileMap: TileTypeVal[][],
  blockedTiles: Set<string>,
  excluded: readonly TileRect[] | null = null,
): WalkMask {
  const walkable = new Uint8Array(cols * rows);
  for (let r = 0; r < rows; r++) {
    const line = tileMap[r];
    if (!line) continue;
    for (let c = 0; c < cols; c++) {
      const t = line[c];
      if (t === undefined || t === TileType.WALL || t === TileType.VOID || WATER_FLOOR_TILES.has(t))
        continue;
      if (blockedTiles.has(`${c},${r}`)) continue;
      if (excluded && inRegions(c, r, excluded)) continue;
      walkable[r * cols + c] = 1;
    }
  }
  return { cols, rows, walkable };
}

/** Largest-remainder allocation of `total` across weights, honouring per-pool minimums. */
function allocate(weights: number[], total: number, mins: number[]): number[] {
  const n = weights.length;
  if (n === 0 || total <= 0) return weights.map(() => 0);
  if (mins.reduce((a, b) => a + b, 0) > total)
    return allocate(
      weights,
      total,
      mins.map(() => 0),
    );
  let remaining = total - mins.reduce((a, b) => a + b, 0);
  const sum = weights.reduce((a, b) => a + b, 0) || 1;
  const exact = weights.map((w) => (w / sum) * remaining);
  const out = mins.map((b, i) => b + Math.floor(exact[i]));
  remaining = total - out.reduce((a, b) => a + b, 0);
  const order = exact
    .map((e, i) => ({ i, frac: e - Math.floor(e) }))
    .sort((a, b) => b.frac - a.frac || a.i - b.i);
  for (let k = 0; k < remaining; k++) out[order[k % n].i]++;
  return out;
}

interface Pool {
  label: string;
  tiles: number[];
  contains: (idx: number) => boolean;
  min: number;
  /** The place's ROOMS: its floor split at door openings (one room for open ground). */
  rooms: number[][];
  /** The legacy whole-floor pool of a layout without places. */
  fallback?: boolean;
}

/**
 * One WORKING OFFICE's duty roster (derived from the world and the clock; NEVER saved). `order` is the
 * fixed seed-hashed order of its members; `onDuty` holds who is on duty, oldest first; a resident in
 * `retiring` steps off duty at its next dwell boundary, and `owed` newcomers come on as places free.
 */
interface DutyGroup {
  readonly label: string;
  readonly cap: number;
  readonly order: AmbientResident[];
  readonly onDuty: AmbientResident[];
  readonly retiring: Set<AmbientResident>;
  next: number;
  clock: number;
  owed: number;
}

/**
 * ROOMS of a place: its tiles split at DOOR cells — an opening in a one-tile-thick wall, whose floor
 * run along the wall line is short (<= 8) and whose runs on both sides are longer. Open ground has no
 * doors and stays one room. Rooms under MIN_ROOM_TILES are folded away (alcoves, door reveals).
 */
const MIN_ROOM_TILES = 6;
export function roomsOf(
  tiles: number[],
  cols: number,
  rows: number,
  tileMap: TileTypeVal[][],
): number[][] {
  const open = (c: number, r: number) =>
    c >= 0 &&
    r >= 0 &&
    c < cols &&
    r < rows &&
    tileMap[r][c] !== TileType.WALL &&
    tileMap[r][c] !== TileType.VOID;
  const run = (c: number, r: number, dc: number, dr: number): number => {
    if (!open(c, r)) return 0;
    let n = 1;
    for (let k = 1; k < 40 && open(c + dc * k, r + dr * k); k++) n++;
    for (let k = 1; k < 40 && open(c - dc * k, r - dr * k); k++) n++;
    return n;
  };
  const isDoor = (c: number, r: number): boolean => {
    const h = run(c, r, 1, 0);
    if (
      h <= 8 &&
      open(c, r - 1) &&
      open(c, r + 1) &&
      run(c, r - 1, 1, 0) > h + 1 &&
      run(c, r + 1, 1, 0) > h + 1
    )
      return true;
    const v = run(c, r, 0, 1);
    return (
      v <= 8 &&
      open(c - 1, r) &&
      open(c + 1, r) &&
      run(c - 1, r, 0, 1) > v + 1 &&
      run(c + 1, r, 0, 1) > v + 1
    );
  };
  const member = new Set<number>();
  for (const t of tiles) if (!isDoor(t % cols, Math.floor(t / cols))) member.add(t);
  const seen = new Set<number>();
  const rooms: number[][] = [];
  for (const s of tiles) {
    if (!member.has(s) || seen.has(s)) continue;
    const room: number[] = [];
    const q = [s];
    seen.add(s);
    while (q.length) {
      const p = q.pop()!;
      room.push(p);
      const c = p % cols;
      for (const n of [c > 0 ? p - 1 : -1, c < cols - 1 ? p + 1 : -1, p - cols, p + cols])
        if (n >= 0 && member.has(n) && !seen.has(n)) {
          seen.add(n);
          q.push(n);
        }
    }
    if (room.length >= MIN_ROOM_TILES) rooms.push(room);
  }
  if (rooms.length <= 1) return [tiles]; // open ground: one place, patrolled around each post
  // a building: galleries and corridors (thin rooms) are walked through, not destinations; large halls
  // (lobby, nave, atrium) are dealt as ZONES so stops spread across them
  const thin = (room: number[]) => {
    let c0 = Infinity;
    let c1 = -1;
    let r0 = Infinity;
    let r1 = -1;
    for (const t of room) {
      const c = t % cols;
      const r = (t - c) / cols;
      c0 = Math.min(c0, c);
      c1 = Math.max(c1, c);
      r0 = Math.min(r0, r);
      r1 = Math.max(r1, r);
    }
    const w = c1 - c0 + 1;
    const h = r1 - r0 + 1;
    // a straight passage (about 3 tiles wide or less) or a ring of galleries (sparse in its box)
    return room.length / Math.max(w, h) <= 3.2 || room.length / (w * h) < 0.3;
  };
  const destinations = rooms.filter((room) => !thin(room));
  if (destinations.length === 0) return [tiles];
  const out: number[][] = [];
  for (const room of destinations) {
    if (room.length <= HALL_ZONE_SPLIT_TILES) {
      out.push(room);
      continue;
    }
    const zones = new Map<number, number[]>();
    for (const t of room) {
      const key =
        Math.floor(Math.floor(t / cols) / HALL_ZONE_TILES) * 1000 +
        Math.floor((t % cols) / HALL_ZONE_TILES);
      const z = zones.get(key);
      if (z) z.push(t);
      else zones.set(key, [t]);
    }
    for (const z of zones.values()) if (z.length >= HALL_ZONE_MIN_TILES) out.push(z);
  }
  return out;
}
const HALL_ZONE_SPLIT_TILES = 400;
const HALL_ZONE_TILES = 16;
const HALL_ZONE_MIN_TILES = 40; // slivers of a hall at a zone edge are not destinations of their own

export class AmbientWorld {
  readonly residents: AmbientResident[];
  readonly pathfinder: AmbientPathfinder;
  readonly graph: PedestrianGraph;
  readonly seed: number;
  /** Seat tiles and their walkable neighbours: never a resident stop. */
  readonly seatZone: ReadonlySet<number>;
  /** Per seat uid: its tile and walkable neighbours (given way when the seat is assigned). */
  private readonly seatNeighbours: ReadonlyMap<string, readonly number[]>;
  readonly stats: AmbientStats = {
    searches: 0,
    localRoutes: 0,
    graphRoutes: 0,
    found: 0,
    budgetHits: 0,
    failures: 0,
    maxExpanded: 0,
    maxHubsSettled: 0,
    cacheHits: 0,
    congestionWaits: 0,
    passThroughs: 0,
    agentYields: 0,
    agentBlockReplans: 0,
  };
  /** Residents standing on each tile (their current tile). */
  private readonly occupancy: Uint16Array;
  /** Place label per tile index (null = no place). */
  readonly placeOf: (idx: number) => string | null;
  /** Room coverage of many-roomed places: rooms, rooms on some routine, rooms the stop cap left out. */
  coverage = { rooms: 0, covered: 0, uncoveredByCap: 0, longestRoutine: 0 };
  /** Subway riders (ids), a bounded subset. */
  riders: string[] = [];
  /** Each rider's journey plan (kind and stations), derived from the world and the seed. */
  journeys: RiderJourney[] = [];
  /** EASTER EGGS (M3 S6): each family and the place it was homed in (null: no matching place). */
  eggs: Array<{ job: AmbientJobId; home: string | null; id: string | null }> = [];
  private cursor = 0;
  /** Working offices' duty rosters (derived; never saved). Empty: every resident is on duty. */
  private dutyGroups: DutyGroup[] = [];
  private rosters: Array<{ label: string; tiles: number; members: number; cap: number }> = [];
  /** Residents off duty: frozen, not drawn, not hoverable, not counted in `occupancy`. */
  private readonly offDuty = new Set<AmbientResident>();
  private visibleCache: AmbientResident[] | null = null;

  constructor(
    residents: AmbientResident[],
    pathfinder: AmbientPathfinder,
    graph: PedestrianGraph,
    seed: number,
    seatZone: ReadonlySet<number>,
    seatNeighbours: ReadonlyMap<string, readonly number[]>,
    placeOf: (idx: number) => string | null,
  ) {
    this.residents = residents;
    this.pathfinder = pathfinder;
    this.graph = graph;
    this.seed = seed;
    this.seatZone = seatZone;
    this.seatNeighbours = seatNeighbours;
    this.placeOf = placeOf;
    this.occupancy = new Uint16Array(pathfinder.mask.cols * pathfinder.mask.rows);
    for (const r of residents) this.occupancy[r.tileRow * pathfinder.mask.cols + r.tileCol]++;
  }

  private get cols(): number {
    return this.pathfinder.mask.cols;
  }

  /**
   * WORKING-OFFICE VISIBLE OCCUPANCY. Each group is one working office (a pool that holds a seat) with its
   * `tiles`: the pool's RESIDENT-ELIGIBLE tiles as `addPool` builds them (`pool.tiles.length`: walkable, on the
   * main pedestrian network, not a seat tile or a seat tile's walkable neighbour, in that place), NOT the room's
   * or the place's area. Its bound is min(`cap`, max(1, ceil(tiles / `tilesPerResident`))). A group larger than
   * its bound has at most that many members ON DUTY at once: the first `bound` of a seed-hashed order at
   * t = 0 (so a reopened save shows exactly the bound), then one steps off at a dwell boundary and the next in the
   * order steps on every AMBIENT_DUTY_ROTATION_SEC. OFF DUTY IS FROZEN, never deletion: nothing about the
   * resident changes (id, job, routine, routineIndex, rides, tile, state, timer), it is simply not simulated,
   * not drawn, not hit-testable and not an obstacle until its turn. Derived from the world and this world's
   * own update clock, so it is never saved and reduced motion (which never calls `update`) freezes it. A
   * resident with a ride plan is never rotated.
   */
  configureDuty(
    groups: ReadonlyArray<{ label: string; tiles: number; members: readonly AmbientResident[] }>,
    cap: number,
    tilesPerResident: number,
  ): void {
    this.dutyGroups = [];
    this.rosters = [];
    this.offDuty.clear();
    this.visibleCache = null;
    const ceiling = Math.max(1, Math.floor(cap));
    const per = tilesPerResident > 0 ? tilesPerResident : 0; // 0 or less: no density bound, only the cap
    for (const group of groups) {
      const members = group.members.filter((r) => !r.rides);
      const bound =
        per > 0 ? Math.min(ceiling, Math.max(1, Math.ceil(group.tiles / per))) : ceiling;
      this.rosters.push({
        label: group.label,
        tiles: group.tiles,
        members: members.length,
        cap: bound,
      });
      if (members.length <= bound) continue;
      const order = [...members].sort(
        (a, b) =>
          hashString(a.id, this.seed) - hashString(b.id, this.seed) || (a.id < b.id ? -1 : 1),
      );
      for (const r of order.slice(bound)) {
        this.offDuty.add(r);
        if (r.state !== 'riding') this.occupancy[r.tileRow * this.cols + r.tileCol]--;
      }
      this.dutyGroups.push({
        label: group.label,
        cap: bound,
        order,
        onDuty: order.slice(0, bound),
        retiring: new Set(),
        next: bound % order.length,
        clock: AMBIENT_DUTY_ROTATION_SEC,
        owed: 0,
      });
    }
  }

  /** Every working office with the resident-eligible tiles (`pool.tiles.length`) its bound was computed from, its resident count and its bound (also those left untouched). */
  dutyRosters(): ReadonlyArray<{ label: string; tiles: number; members: number; cap: number }> {
    return this.rosters;
  }

  /** Whether `r` is on duty (drawn, simulated, hoverable). Everyone is, outside a working office. */
  isOnDuty(r: AmbientResident): boolean {
    return !this.offDuty.has(r);
  }

  /** The residents to DRAW and hit-test: the roster itself when nobody is off duty, else the on-duty residents. */
  visibleResidents(): readonly AmbientResident[] {
    if (this.offDuty.size === 0) return this.residents;
    if (this.visibleCache === null)
      this.visibleCache = this.residents.filter((r) => !this.offDuty.has(r));
    return this.visibleCache;
  }

  /** Advance every duty roster by `dt`: schedule hand-overs, retire the flagged at a dwell boundary, bring the next on. */
  private rotateDuty(dt: number): void {
    for (const g of this.dutyGroups) {
      g.clock -= dt;
      while (g.clock <= 0) {
        g.clock += AMBIENT_DUTY_ROTATION_SEC;
        const leaving = g.onDuty.find((r) => !g.retiring.has(r));
        if (leaving) {
          g.retiring.add(leaving);
          g.owed++;
        }
      }
      for (const r of [...g.retiring]) if (r.state === 'dwell') this.stepOff(g, r);
      while (g.owed > 0 && g.onDuty.length < g.cap) if (!this.stepOn(g)) g.owed = 0;
    }
  }

  private stepOff(g: DutyGroup, r: AmbientResident): void {
    this.offDuty.add(r);
    this.occupancy[r.tileRow * this.cols + r.tileCol]--;
    g.onDuty.splice(g.onDuty.indexOf(r), 1);
    g.retiring.delete(r);
    this.visibleCache = null;
  }

  private stepOn(g: DutyGroup): boolean {
    for (let k = 0; k < g.order.length; k++) {
      const r = g.order[g.next];
      g.next = (g.next + 1) % g.order.length;
      if (!this.offDuty.has(r)) continue;
      this.offDuty.delete(r);
      this.occupancy[r.tileRow * this.cols + r.tileCol]++;
      g.onDuty.push(r);
      g.owed--;
      this.visibleCache = null;
      return true;
    }
    return false;
  }

  /** Tiles real agents have priority on this tick: their own tiles plus assigned seats' zones. */
  priorityTiles(ctx: AmbientAgentContext | undefined): Set<number> {
    const set = new Set<number>(ctx?.agentTiles ?? []);
    for (const uid of ctx?.assignedSeats ?? [])
      for (const t of this.seatNeighbours.get(uid) ?? []) set.add(t);
    return set;
  }

  /** Advance the simulation by `dt` seconds. At most AMBIENT_PATH_SEARCHES_PER_TICK searches. */
  update(dt: number, ctx?: AmbientAgentContext): void {
    const priority = this.priorityTiles(ctx);
    const agentTiles = ctx?.agentTiles ?? new Set<number>();
    if (this.dutyGroups.length > 0) this.rotateDuty(dt);
    const off = this.offDuty;
    for (const r of this.residents) {
      if (off.size > 0 && off.has(r)) continue; // off duty: frozen
      const here = r.tileRow * this.cols + r.tileCol;
      // Agents win: a resident standing on an agent's tile or an assigned seat's zone steps aside.
      const midStep = r.state === 'walk' && r.moveProgress > 0;
      if (!midStep && priority.has(here) && this.yieldFrom(r, priority)) continue;
      if (r.state === 'riding') continue; // the train carries it
      if (r.state === 'waiting') {
        // on the platform for a train; after the bound it walks instead
        r.frameTimer += dt;
        if (r.frameTimer >= AMBIENT_IDLE_FRAME_SEC) {
          r.frameTimer -= AMBIENT_IDLE_FRAME_SEC;
          r.frame++;
        }
        r.timer -= dt;
        if (r.timer <= 0) {
          r.routineIndex = (r.routineIndex + 1) % r.routine.length;
          r.state = 'need-path';
          r.frame = 0;
        }
        continue;
      }
      if (r.state === 'dwell' || r.state === 'retry') {
        // standing pose cycle (sheet-drawn residents show idle, idle, gesture; frame is transient)
        r.frameTimer += dt;
        if (r.frameTimer >= AMBIENT_IDLE_FRAME_SEC) {
          r.frameTimer -= AMBIENT_IDLE_FRAME_SEC;
          r.frame++;
        }
        r.timer -= dt;
        if (r.timer <= 0) {
          if (r.state === 'dwell') r.routineIndex = (r.routineIndex + 1) % r.routine.length;
          r.state = 'need-path';
          r.frame = 0;
        }
      } else if (r.state === 'walk') {
        this.walk(r, dt, priority, agentTiles);
      }
    }
    let budget = AMBIENT_PATH_SEARCHES_PER_TICK;
    const n = this.residents.length;
    for (let k = 0; k < n && budget > 0; k++) {
      const r = this.residents[(this.cursor + k) % n];
      if (r.state !== 'need-path' || (off.size > 0 && off.has(r))) continue;
      budget--;
      this.plan(r);
    }
    if (n > 0) this.cursor = (this.cursor + 1) % n;
  }

  /** Re-route to the nearest walkable tile outside the agents' priority tiles (BFS, bounded). */
  private yieldFrom(r: AmbientResident, priority: ReadonlySet<number>): boolean {
    // Already stepping out along a yield route whose next tile is clear: keep going.
    if (r.state === 'walk' && r.routePos < r.route.length && !priority.has(r.route[r.routePos]))
      return false;
    const steps = this.yieldPath(r, priority);
    if (!steps) return false;
    r.route = Int32Array.from(steps);
    r.routePos = 0;
    r.moveProgress = 0;
    r.blocked = 0;
    r.state = 'walk';
    this.stats.agentYields++;
    return true;
  }

  /**
   * REDUCED MOTION holds the residents' MOTION, never real-agent PRIORITY. Nothing walks, animates or
   * plans here; but a resident standing on an agent's tile or an assigned seat's zone still yields: it
   * is placed AT ONCE (no animation) on the same free tile the walking yield would head for, and the
   * yield is counted the same way. A resident that was walking or waiting re-plans from its new tile
   * when motion resumes; one dwelling keeps its frozen dwell. Returns the yields made.
   */
  yieldWithoutMotion(ctx: AmbientAgentContext | undefined): number {
    const priority = this.priorityTiles(ctx);
    if (priority.size === 0) return 0;
    const cols = this.cols;
    let yields = 0;
    for (const r of this.residents) {
      if (r.state === 'riding' || this.offDuty.has(r)) continue;
      const here = r.tileRow * cols + r.tileCol;
      if (!priority.has(here)) continue;
      const steps = this.yieldPath(r, priority);
      if (!steps) continue;
      const to = steps[steps.length - 1];
      const tc = to % cols;
      const tr = (to - tc) / cols;
      this.occupancy[here]--;
      this.occupancy[to]++;
      r.tileCol = tc;
      r.tileRow = tr;
      r.x = tc * TILE_SIZE + TILE_SIZE / 2;
      r.y = tr * TILE_SIZE + TILE_SIZE / 2;
      r.moveProgress = 0;
      r.route = new Int32Array(0);
      r.routePos = 0;
      r.blocked = 0;
      if (r.state === 'walk' || r.state === 'waiting') r.state = 'need-path';
      const place = this.placeOf(to);
      if (place) r.placesVisited.add(place);
      this.stats.agentYields++;
      yields++;
    }
    return yields;
  }

  /** The steps from a resident's tile to the nearest walkable, unoccupied tile outside the agents'
   *  priority tiles (BFS within AMBIENT_YIELD_RADIUS), or null when there is none in reach. */
  private yieldPath(r: AmbientResident, priority: ReadonlySet<number>): number[] | null {
    const cols = this.cols;
    const start = r.tileRow * cols + r.tileCol;
    const parent = new Map<number, number>([[start, -1]]);
    let frontier = [start];
    for (let depth = 0; depth < AMBIENT_YIELD_RADIUS && frontier.length; depth++) {
      const next: number[] = [];
      for (const u of frontier) {
        const uc = u % cols;
        const ur = (u - uc) / cols;
        for (const [dc, dr] of NEIGHBOURS) {
          const vc = uc + dc;
          const vr = ur + dr;
          if (!this.pathfinder.isWalkable(vc, vr)) continue;
          const v = vr * cols + vc;
          if (parent.has(v)) continue;
          parent.set(v, u);
          if (!priority.has(v) && this.occupancy[v] === 0) {
            const steps: number[] = [];
            for (let x = v; x !== start; x = parent.get(x)!) steps.push(x);
            return steps.reverse();
          }
          next.push(v);
        }
      }
      frontier = next;
    }
    return null;
  }

  /** Bounded A* for short legs, the whole-world graph otherwise. */
  findRoute(from: number, to: number): { path: Int32Array; ok: boolean; same: boolean } {
    const cols = this.cols;
    const fc = from % cols;
    const fr = (from - fc) / cols;
    const tc = to % cols;
    const tr = (to - tc) / cols;
    this.stats.searches++;
    if (Math.abs(fc - tc) + Math.abs(fr - tr) <= AMBIENT_LOCAL_ROUTE_RADIUS) {
      this.stats.localRoutes++;
      const local = this.pathfinder.search(
        fc,
        fr,
        tc,
        tr,
        AMBIENT_PATH_MAX_EXPANSIONS,
        AMBIENT_PATH_MAX_LENGTH,
      );
      this.stats.maxExpanded = Math.max(this.stats.maxExpanded, local.expanded);
      if (local.status === 'same') return { path: new Int32Array(0), ok: true, same: true };
      if (local.status === 'found')
        return {
          path: Int32Array.from(local.path, (p) => p.row * cols + p.col),
          ok: true,
          same: false,
        };
      if (local.status === 'budget') this.stats.budgetHits++;
    }
    this.stats.graphRoutes++;
    const g = this.graph.route(from, to, AMBIENT_ROUTE_MAX_LENGTH);
    this.stats.maxHubsSettled = Math.max(this.stats.maxHubsSettled, g.hubsSettled);
    if (g.status === 'same') return { path: g.path, ok: true, same: true };
    return { path: g.path, ok: g.status === 'found', same: false };
  }

  private plan(r: AmbientResident): void {
    const here = r.tileRow * this.cols + r.tileCol;
    const target = r.routine[r.routineIndex];
    const fromIndex = r.routine.indexOf(here);
    const key = `${fromIndex}>${r.routineIndex}`;
    let path = fromIndex >= 0 ? r.legCache.get(key) : undefined;
    if (path) {
      this.stats.cacheHits++;
    } else {
      const found = this.findRoute(here, target);
      if (found.same) {
        this.arrive(r);
        return;
      }
      if (!found.ok) {
        this.stats.failures++;
        r.routineIndex = (r.routineIndex + 1) % r.routine.length;
        r.state = 'retry';
        r.timer = AMBIENT_PATH_RETRY_SEC;
        return;
      }
      this.stats.found++;
      path = found.path;
      if (fromIndex >= 0) {
        if (r.legCache.size >= AMBIENT_LEG_CACHE_PER_RESIDENT) {
          const oldest = r.legCache.keys().next().value;
          if (oldest !== undefined) r.legCache.delete(oldest);
        }
        r.legCache.set(key, path);
      }
    }
    r.route = path;
    r.routePos = 0;
    r.moveProgress = 0;
    r.blocked = 0;
    r.state = 'walk';
  }

  private walk(
    r: AmbientResident,
    dt: number,
    priority: ReadonlySet<number>,
    agentTiles: ReadonlySet<number>,
  ): void {
    if (r.routePos >= r.route.length) {
      this.arrive(r);
      return;
    }
    const cols = this.cols;
    const next = r.route[r.routePos];
    const nc = next % cols;
    const nr = (next - nc) / cols;
    if (r.moveProgress === 0) {
      // Congestion and priority checks happen only before committing to a step.
      if (agentTiles.has(next) || priority.has(next)) {
        r.blocked += dt;
        if (r.blocked >= AMBIENT_AGENT_BLOCK_REPLAN_SEC) {
          // Never through a real agent or onto an assigned seat's zone: re-plan instead.
          this.stats.agentBlockReplans++;
          r.blocked = 0;
          r.state = 'need-path';
        }
        return;
      }
      if (this.occupancy[next] > 0) {
        if (r.blocked === 0) this.stats.congestionWaits++;
        r.blocked += dt;
        if (r.blocked < AMBIENT_CONGESTION_PASS_SEC) return;
        this.stats.passThroughs++; // a readable queue, then pass: no permanent gridlock
      }
      r.blocked = 0;
    }
    r.frameTimer += dt;
    if (r.frameTimer >= AMBIENT_WALK_FRAME_DURATION_SEC) {
      r.frameTimer -= AMBIENT_WALK_FRAME_DURATION_SEC;
      r.frame = (r.frame + 1) % 4;
    }
    r.dir = directionBetween(r.tileCol, r.tileRow, nc, nr);
    r.moveProgress += (AMBIENT_WALK_SPEED_PX_PER_SEC / TILE_SIZE) * dt;
    const t = Math.min(r.moveProgress, 1);
    const fx = r.tileCol * TILE_SIZE + TILE_SIZE / 2;
    const fy = r.tileRow * TILE_SIZE + TILE_SIZE / 2;
    const tx = nc * TILE_SIZE + TILE_SIZE / 2;
    const ty = nr * TILE_SIZE + TILE_SIZE / 2;
    r.x = fx + (tx - fx) * t;
    r.y = fy + (ty - fy) * t;
    if (r.moveProgress >= 1) {
      this.occupancy[r.tileRow * cols + r.tileCol]--;
      this.occupancy[next]++;
      r.tileCol = nc;
      r.tileRow = nr;
      r.x = tx;
      r.y = ty;
      r.routePos++;
      r.moveProgress = 0;
      const place = this.placeOf(next);
      if (place) r.placesVisited.add(place);
      if (r.routePos >= r.route.length) this.arrive(r);
    }
  }

  private arrive(r: AmbientResident): void {
    r.state = 'dwell';
    r.legsWalked++;
    r.frame = 0;
    r.route = new Int32Array(0);
    r.routePos = 0;
    // dwell profile: a posted job holds its post, a roaming job keeps moving (the profile is derived, never saved)
    const factor =
      AMBIENT_JOBS[r.job].style === 'posted'
        ? r.routineIndex === 0
          ? AMBIENT_POST_DWELL_FACTOR
          : 1
        : AMBIENT_ROAMING_DWELL_FACTOR;
    r.timer = r.rng.range(AMBIENT_DWELL_MIN_SEC, AMBIENT_DWELL_MAX_SEC) * factor;
    if (r.rides && r.rides[r.routineIndex] >= 0) {
      r.state = 'waiting';
      r.timer = AMBIENT_RIDE_WAIT_MAX_SEC;
    }
  }

  /**
   * RESTORE, step 1 (validate, change nothing): check every saved resident entry against THIS world
   * and return a restore plan, or the reason the whole snapshot is refused. The resident set must be
   * exactly this world's; a standing, walking or waiting resident's tile must be walkable and connected
   * to its post; a waiting resident must be on an island platform with a ride pending at that leg; a
   * resident aboard must have a ride pending at its leg.
   */
  planRestore(
    entries: ReadonlyArray<readonly [string, number, number, number, number, number]>,
  ): RestorePlan[] | string {
    if (entries.length !== this.residents.length)
      return `the snapshot holds ${entries.length} residents; this world has ${this.residents.length}`;
    const plan: RestorePlan[] = [];
    const seen = new Set<string>();
    for (const [id, ri, col, row, code, timer] of entries) {
      const r = this.byId(id);
      if (!r || seen.has(id)) return `resident ${id} is not in this world`;
      seen.add(id);
      if (ri < 0 || ri >= r.routine.length)
        return `resident ${id}: routine index ${ri} outside its routine`;
      const tile = row * this.cols + col;
      if (code === 3) {
        if (!r.rides || r.rides[ri] < 0)
          return `resident ${id} is aboard with no ride pending at its leg`;
        plan.push({ r, routineIndex: ri, tile, state: 'riding', timer: 0 });
        continue;
      }
      if (!this.pathfinder.isWalkable(col, row) || !this.graph.connected(r.routine[0], tile))
        return `resident ${id}: saved tile ${col},${row} is not reachable in this world`;
      if (code === 2) {
        if (!r.rides || r.rides[ri] < 0)
          return `resident ${id} is waiting with no ride pending at its leg`;
        if (!this.stationTiles.some((st) => st.has(tile)))
          return `resident ${id} is waiting off any platform`;
        plan.push({ r, routineIndex: ri, tile, state: 'waiting', timer });
        continue;
      }
      const state: AmbientState = code === 1 ? 'need-path' : code === 4 ? 'retry' : 'dwell';
      plan.push({ r, routineIndex: ri, tile, state, timer: code === 1 ? 0 : timer });
    }
    return plan;
  }

  /** The station a planned resident is riding to (its ride plan at its saved leg), or -1. */
  rideDestinationAt(id: string, plan: readonly RestorePlan[]): number {
    const p = plan.find((x) => x.r.id === id);
    return p && p.state === 'riding' && p.r.rides ? p.r.rides[p.routineIndex] : -1;
  }

  /** RESTORE, step 2 (only after every check passed): apply the plan. Emits nothing; no counter
   *  moves. Paths, leg caches and animation frames reset (derived and transient). */
  commitRestore(plan: readonly RestorePlan[]): void {
    for (const p of plan) {
      const r = p.r;
      // an off-duty resident is not counted in `occupancy` (it blocks nobody), so it is neither taken out nor put back
      const counted = !this.offDuty.has(r);
      if (counted && r.state !== 'riding') this.occupancy[r.tileRow * this.cols + r.tileCol]--;
      r.routineIndex = p.routineIndex;
      r.route = new Int32Array(0);
      r.routePos = 0;
      r.moveProgress = 0;
      r.frame = 0;
      r.blocked = 0;
      r.state = p.state;
      r.timer = p.timer;
      r.tileCol = p.tile % this.cols;
      r.tileRow = (p.tile - r.tileCol) / this.cols;
      r.x = r.tileCol * TILE_SIZE + TILE_SIZE / 2;
      r.y = r.tileRow * TILE_SIZE + TILE_SIZE / 2;
      // a rider aboard keeps its last ground tile but occupies none; the train carries it from its
      // first update
      if (counted && p.state !== 'riding') this.occupancy[p.tile]++;
    }
  }

  // ── TransitRiders: the subway's view of its passengers ─────────
  /** Island-platform tiles per station (set by OfficeState when a line exists). */
  stationTiles: ReadonlyArray<ReadonlySet<number>> = [];
  private byId(id: string): AmbientResident | undefined {
    return this.residents.find((r) => r.id === id);
  }
  waitingAt(station: number): Array<{ id: string; destination: number }> {
    const tiles = this.stationTiles[station];
    if (!tiles) return [];
    const out: Array<{ id: string; destination: number }> = [];
    for (const r of this.residents)
      if (r.state === 'waiting' && r.rides && tiles.has(r.tileRow * this.cols + r.tileCol))
        out.push({ id: r.id, destination: r.rides[r.routineIndex] });
    return out;
  }
  destinationOf(id: string): number {
    const r = this.byId(id);
    return r?.rides && r.state === 'riding' ? r.rides[r.routineIndex] : -1;
  }
  board(id: string): boolean {
    const r = this.byId(id);
    if (!r || r.state !== 'waiting') return false;
    this.occupancy[r.tileRow * this.cols + r.tileCol]--;
    r.state = 'riding';
    r.route = new Int32Array(0);
    r.routePos = 0;
    return true;
  }
  alight(id: string, station: number): boolean {
    const r = this.byId(id);
    if (!r || r.state !== 'riding' || !r.rides) return false;
    // step off onto the routine's next tile: the destination station's island platform
    const next = (r.routineIndex + 1) % r.routine.length;
    const tile = r.routine[next];
    if (!this.stationTiles[station]?.has(tile)) return false;
    r.routineIndex = next;
    r.tileCol = tile % this.cols;
    r.tileRow = (tile - r.tileCol) / this.cols;
    r.x = r.tileCol * TILE_SIZE + TILE_SIZE / 2;
    r.y = r.tileRow * TILE_SIZE + TILE_SIZE / 2;
    this.occupancy[tile]++;
    r.state = 'dwell';
    r.timer = AMBIENT_DWELL_MIN_SEC;
    r.legsWalked++;
    const place = this.placeOf(tile);
    if (place) r.placesVisited.add(place);
    return true;
  }
  carry(id: string, x: number, y: number): void {
    const r = this.byId(id);
    if (r && r.state === 'riding') {
      r.x = x;
      r.y = y;
    }
  }

  /** The resident under a world-pixel point (same hit box as a character), or null. */
  residentAt(worldX: number, worldY: number): AmbientResident | null {
    let best: AmbientResident | null = null;
    for (const r of this.residents) {
      if (r.state === 'riding') continue; // aboard a train: not on the ground, not hit-testable
      if (this.offDuty.has(r)) continue; // off duty: not drawn, so never hoverable or clickable
      if (
        worldX >= r.x - CHARACTER_HIT_HALF_WIDTH &&
        worldX <= r.x + CHARACTER_HIT_HALF_WIDTH &&
        worldY >= r.y - CHARACTER_HIT_HEIGHT &&
        worldY <= r.y
      ) {
        if (!best || r.y > best.y) best = r;
      }
    }
    return best;
  }

  /** Counts by job label, for the legend. */
  summary(): {
    total: number;
    onDuty: number;
    offDuty: number;
    byJob: Array<{ job: AmbientJobId; label: string; count: number }>;
  } {
    const counts = new Map<AmbientJobId, number>();
    for (const r of this.residents) counts.set(r.job, (counts.get(r.job) ?? 0) + 1);
    return {
      total: this.residents.length,
      onDuty: this.residents.length - this.offDuty.size,
      offDuty: this.offDuty.size,
      byJob: [...counts].map(([job, count]) => ({ job, label: AMBIENT_JOBS[job].label, count })),
    };
  }
}

/** Build the deterministic roster and the whole-world pedestrian graph for a world. */
export function buildAmbientWorld(input: AmbientBuildInput): AmbientWorld {
  const { layout, tileMap, blockedTiles, homeRegions } = input;
  const { cols, rows } = layout;
  const seed = (input.seed ?? AMBIENT_WORLD_SEED) >>> 0;
  const population = Math.max(
    0,
    Math.min(AMBIENT_POPULATION_CAP, Math.floor(input.population ?? AMBIENT_POPULATION_ORDINARY)),
  );
  const mask = buildWalkMask(cols, rows, tileMap, blockedTiles);
  const pathfinder = new AmbientPathfinder(mask);
  const graph = new PedestrianGraph(mask, AMBIENT_HUB_SPACING);
  const walk = (i: number): boolean => mask.walkable[i] === 1;

  // Seats: residents keep clear of every seat tile and its walkable neighbours.
  const seatZone = new Set<number>();
  const seatNeighbours = new Map<string, number[]>();
  for (const s of input.seats ?? []) {
    const zone: number[] = [s.seatRow * cols + s.seatCol];
    for (const [dc, dr] of NEIGHBOURS) {
      const c = s.seatCol + dc;
      const r = s.seatRow + dr;
      if (c >= 0 && r >= 0 && c < cols && r < rows && walk(r * cols + c)) zone.push(r * cols + c);
    }
    seatNeighbours.set(s.uid, zone);
    for (const t of zone) seatZone.add(t);
  }

  // Places: the home office (Palace) and deck (C1), then the layout's own districts, then the
  // original forest quadrant's unlabelled floor. Each tile belongs to at most one place.
  const office = homeRegions?.[0] ?? null;
  const deck = homeRegions?.[1] ?? null;
  const inRect = (rect: TileRect | null, i: number): boolean => {
    if (!rect) return false;
    const c = i % cols;
    const r = (i - c) / cols;
    return c >= rect.c0 && c < rect.c1 && r >= rect.r0 && r < rect.r1;
  };
  const isLarge = cols > FOREST_COLS || rows > FOREST_ROWS;
  const placeOf = (i: number): string | null => {
    if (inRect(office, i)) return 'Palace';
    if (inRect(deck, i)) return 'Deck';
    const l = layout.areaTiles?.[i] ?? null;
    if (l) return l;
    const c = i % cols;
    const r = (i - c) / cols;
    return isLarge && c < FOREST_COLS && r < FOREST_ROWS ? FOREST_LABEL : null;
  };
  // Residents live on the MAIN pedestrian network (the largest walkable component). Pockets the
  // network cannot reach stay counted in graph.stats/componentTiles and are reported, not used.
  let main = 0;
  for (let k = 1; k < graph.componentTiles.length; k++)
    if (graph.componentTiles[k] > graph.componentTiles[main]) main = k;
  const onNetwork = (i: number): boolean => graph.componentOf(i) === main;
  const pools: Pool[] = [];
  const addPool = (label: string, min: number): void => {
    const contains = (i: number): boolean =>
      walk(i) && onNetwork(i) && !seatZone.has(i) && placeOf(i) === label;
    const tiles: number[] = [];
    for (let i = 0; i < cols * rows; i++) if (contains(i)) tiles.push(i);
    if (tiles.length < MIN_POOL_TILES) return;
    // rooms come from the place's FLOOR (walls only; furniture never fragments a room), then keep
    // the tiles a resident may actually stand on
    const floor: number[] = [];
    for (let i = 0; i < cols * rows; i++) {
      const c = i % cols;
      const r = (i - c) / cols;
      const t = tileMap[r][c];
      if (
        placeOf(i) === label &&
        t !== TileType.WALL &&
        t !== TileType.VOID &&
        !WATER_FLOOR_TILES.has(t)
      )
        floor.push(i);
    }
    const rooms = roomsOf(floor, cols, rows, tileMap)
      .map((room) => room.filter(contains))
      .filter((room) => room.length > 0);
    if (rooms.length === 0) rooms.push(tiles);
    // a building of many rooms needs enough residents for its routines to reach every room
    const roomMin = rooms.length > 1 ? Math.ceil(rooms.length / AMBIENT_ROUTINE_STOPS) + 1 : 0;
    pools.push({ label, tiles, contains, min: Math.max(min, roomMin), rooms });
  };
  if (office) addPool('Palace', AMBIENT_PALACE_MIN);
  if (deck) addPool('Deck', AMBIENT_DECK_MIN);
  for (const d of districtsOf(layout)) addPool(d.label, 2);
  addPool(FOREST_LABEL, 2);
  if (pools.length === 0) {
    // A legacy layout without places: the whole floor is one pool.
    const contains = (i: number): boolean => walk(i) && onNetwork(i) && !seatZone.has(i);
    const tiles: number[] = [];
    for (let i = 0; i < cols * rows; i++) if (contains(i)) tiles.push(i);
    if (tiles.length >= MIN_POOL_TILES)
      pools.push({ label: 'Office', tiles, contains, min: 0, rooms: [tiles], fallback: true });
  }
  if (pools.length === 0 || population === 0)
    return new AmbientWorld([], pathfinder, graph, seed, seatZone, seatNeighbours, placeOf);

  const counts = allocate(
    pools.map((p) => Math.pow(p.tiles.length, AMBIENT_DISTRICT_WEIGHT_EXPONENT)),
    population,
    pools.map((p) => p.min),
  );

  const plan: Array<{ pool: number; job: AmbientJobId }> = [];
  pools.forEach((p, i) => {
    const jobs = jobsForDistrict(p.label);
    for (let k = 0; k < counts[i]; k++) plan.push({ pool: i, job: jobs[k % jobs.length] });
  });
  for (const required of REQUIRED_AMBIENT_JOBS) {
    if (plan.some((p) => p.job === required) || plan.length === 0) continue;
    let poolIndex = pools.findIndex((p) => jobsForDistrict(p.label).includes(required));
    if (poolIndex < 0) poolIndex = counts.indexOf(Math.max(...counts));
    const inPool = plan.filter((p) => p.pool === poolIndex);
    const tally = new Map<AmbientJobId, number>();
    for (const p of inPool) tally.set(p.job, (tally.get(p.job) ?? 0) + 1);
    const victimJob = [...tally].sort(
      (a, b) =>
        b[1] - a[1] ||
        (REQUIRED_AMBIENT_JOBS.includes(a[0]) ? 1 : 0) -
          (REQUIRED_AMBIENT_JOBS.includes(b[0]) ? 1 : 0),
    )[0]?.[0];
    const victim = [...plan].reverse().find((p) => p.pool === poolIndex && p.job === victimJob);
    if (victim) victim.job = required;
  }
  // EASTER EGGS: one resident per family, in the first place matching its homes (within the cap)
  const eggHomes: Array<{ job: AmbientJobId; pool: number }> = [];
  for (const egg of AMBIENT_EGGS) {
    let pool = -1;
    for (const re of egg.homes) {
      pool = pools.findIndex((p) => re.test(p.label));
      if (pool >= 0) break;
    }
    if (pool >= 0 && plan.length < AMBIENT_POPULATION_CAP) plan.push({ pool, job: egg.job });
    eggHomes.push({ job: egg.job, pool });
  }

  // Rooms are dealt round-robin (in a seeded order) to each place's residents' posts and stops, so a
  // many-roomed building has every room on someone's routine: inhabited, not just reachable.
  const roomOrder = pools.map((p, i) =>
    new SeededRng(hashString(`rooms-${p.label}-${i}`, seed)).shuffle(p.rooms.map((_, k) => k)),
  );
  const roomCursor = pools.map(() => 0);
  const nextRoom = (pool: number): number[] => {
    const order = roomOrder[pool];
    const room = pools[pool].rooms[order[roomCursor[pool] % order.length]];
    roomCursor[pool]++;
    return room;
  };
  // SIBLING places share a first word ("Cathedral", "Cathedral Parvis", "Cathedral Quarter"): their
  // residents always include a stop in a sibling, so the doors between them carry real traffic.
  const firstWord = (l: string) => l.trim().split(/\s+/)[0].toLowerCase();
  const siblings = pools.map((p, i) =>
    pools
      .map((_, j) => j)
      .filter((j) => j !== i && firstWord(pools[j].label) === firstWord(p.label)),
  );
  // a stop INSIDE a room: prefer a tile with all four neighbours walkable (never a doorway cell)
  const roomTile = (rng: SeededRng, room: number[]): number => {
    for (let tries = 0; tries < 16; tries++) {
      const t = room[rng.int(room.length)];
      const c = t % cols;
      const r = (t - c) / cols;
      if (NEIGHBOURS.every(([dc, dr]) => pathfinder.isWalkable(c + dc, r + dr))) return t;
    }
    return room[rng.int(room.length)];
  };
  const serial = new Map<string, number>();
  const residents: AmbientResident[] = plan.map(({ pool, job }) => {
    const home = pools[pool];
    const stem = `amb-${slug(home.label)}-${job}`;
    const k = (serial.get(stem) ?? 0) + 1;
    serial.set(stem, k);
    const id = `${stem}-${k}`;
    const rng = new SeededRng(hashString(id, seed));
    const manyRooms = home.rooms.length > 1;
    const post = manyRooms
      ? pickPost(rng, { ...home, tiles: nextRoom(pool) }, pathfinder, cols)
      : pickPost(rng, home, pathfinder, cols);
    const roaming = AMBIENT_JOBS[job].style === 'roaming';
    const radius = roaming
      ? Math.round(AMBIENT_PATROL_RADIUS_TILES * 1.6)
      : AMBIENT_PATROL_RADIUS_TILES;
    const routine: number[] = [post];
    for (let s = 1; s < AMBIENT_ROUTINE_STOPS; s++) {
      if (manyRooms) {
        routine.push(roomTile(rng, nextRoom(pool)));
      } else routine.push(pickStop(rng, post, radius, home, cols));
    }
    if (siblings[pool].length > 0) {
      const sib = pools[siblings[pool][rng.int(siblings[pool].length)]];
      const t = roomTile(rng, sib.rooms[rng.int(sib.rooms.length)]);
      if (graph.connected(post, t)) routine.splice(2, 0, t);
    }
    // Cross-world walking: roaming jobs visit one stop in another place, reachable on the graph.
    if (roaming && pools.length > 1) {
      for (let tries = 0; tries < 8; tries++) {
        const other = pools[(pool + 1 + rng.int(pools.length - 1)) % pools.length];
        const far = other.tiles[rng.int(other.tiles.length)];
        if (graph.connected(post, far)) {
          routine.push(far);
          break;
        }
      }
    }
    const pc = post % cols;
    const pr = (post - pc) / cols;
    return {
      id,
      job,
      district: home.label,
      palette: AMBIENT_JOBS[job].palette,
      hueShift: rng.pick(HUE_SHIFTS),
      routine: Int32Array.from(routine),
      routineIndex: 0,
      state: 'dwell',
      tileCol: pc,
      tileRow: pr,
      x: pc * TILE_SIZE + TILE_SIZE / 2,
      y: pr * TILE_SIZE + TILE_SIZE / 2,
      dir: Direction.DOWN,
      route: new Int32Array(0),
      routePos: 0,
      moveProgress: 0,
      frame: 0,
      frameTimer: 0,
      timer: rng.range(0, AMBIENT_DWELL_MAX_SEC),
      blocked: 0,
      legsWalked: 0,
      placesVisited: new Set([home.label]),
      rng,
      legCache: new Map(),
    };
  });
  // COVERAGE: every room of a many-roomed place is on some routine. A room the deal missed gets a stop
  // on the routine of that place's resident with the fewest stops (deterministic).
  const coverage = { rooms: 0, covered: 0, uncoveredByCap: 0, longestRoutine: 0 };
  pools.forEach((p, i) => {
    if (p.rooms.length <= 1) return;
    const mine = residents.filter((_, k) => plan[k].pool === i);
    if (mine.length === 0) return;
    for (const room of p.rooms) {
      coverage.rooms++;
      const set = new Set(room);
      if (mine.some((r) => r.routine.some((t) => set.has(t)))) {
        coverage.covered++;
        continue;
      }
      const hosts = mine.filter((r) => r.routine.length < AMBIENT_ROUTINE_MAX_STOPS);
      if (hosts.length === 0) {
        coverage.uncoveredByCap++;
        continue;
      }
      const host = [...hosts].sort(
        (a, b) => a.routine.length - b.routine.length || (a.id < b.id ? -1 : 1),
      )[0];
      const t = roomTile(host.rng, room);
      if (!graph.connected(host.routine[0], t)) continue;
      const k = residents.indexOf(host);
      residents[k] = { ...host, routine: Int32Array.from([...host.routine, t]) };
      mine[mine.indexOf(host)] = residents[k];
      coverage.covered++;
    }
  });
  for (const r of residents) {
    if (r.routine.length > AMBIENT_ROUTINE_MAX_STOPS)
      throw new Error(`routine of ${r.id} exceeds the stop bound`);
    coverage.longestRoutine = Math.max(coverage.longestRoutine, r.routine.length);
  }
  // SUBWAY RIDERS: a bounded subset of the residents who live at a station or on the Grand or Market Boulevard (as ever),
  // and of the residents of OPEN-GROUND places near the line, ride between stations (a many-roomed building elsewhere
  // keeps every resident its room coverage needs, so it supplies none). Each rider has a JOURNEY of one of three
  // kinds, chosen from its own seeded stream:
  //   commute    post -> platform A -> (ride, past a station when the line has one) -> platform B -> a forecourt stop
  //              near B -> platform B -> (ride) -> platform A -> post
  //   hop        the same, to the NEXT station along the line
  //   excursion  three stations: A -> B -> C -> A, with a forecourt stop at B and at C
  // Platform tiles are DEALT round-robin per station (seeded; one deal for the tiles riders wait on, one for the tiles they
  // alight on), so riders spread along a platform instead of standing on one another. Nothing here is saved: a rider's plan is derived from the world and the seed, so every load rebuilds the
  // same one (and the snapshot fingerprint binds to it). Rooms are open ground, so room coverage is unaffected.
  const transit = input.transit ?? null;
  const riderIds: string[] = [];
  const journeys: RiderJourney[] = [];
  if (transit) {
    const islands = transit.stations.map((st) =>
      st.island.filter((t) => walk(t) && onNetwork(t) && !seatZone.has(t)),
    );
    // Two deals per station over the same shuffled platform: the tiles a rider WAITS on (a long stand) come from the front,
    // the tiles it ALIGHTS on (a short one) from the back, so waiting riders never share a tile until a platform has more
    // waiting riders than tiles.
    const orders = islands.map((tiles, s) =>
      new SeededRng(hashString(`platform-${s}`, seed)).shuffle([...tiles]),
    );
    const dealt = islands.map(() => ({ wait: 0, alight: 0 }));
    const waitTile = (s: number): number => orders[s][dealt[s].wait++ % orders[s].length];
    const alightTile = (s: number): number =>
      orders[s][orders[s].length - 1 - (dealt[s].alight++ % orders[s].length)];
    // the forecourt above each station's platform (open ground on the network)
    const forecourtRow = transit.eastRow - 2;
    const forecourts = transit.stations.map((st) => {
      const list: number[] = [];
      for (let c = st.c - 10; c <= st.c + 10; c++) {
        const t = forecourtRow * cols + c;
        if (c >= 0 && c < cols && walk(t) && onNetwork(t)) list.push(t);
      }
      return list;
    });
    const nearLine = (t: number): boolean => {
      const tc = t % cols;
      const tr = (t - tc) / cols;
      return transit.stations.some((st) =>
        st.island.some((i) => {
          const c = i % cols;
          return Math.abs(c - tc) + Math.abs((i - c) / cols - tr) <= AMBIENT_RIDER_REACH_TILES;
        }),
      );
    };
    const candidates = residents
      .map((r, k) => ({ r, k }))
      .filter(
        ({ r, k }) =>
          /Station$|^Grand Boulevard$|^Market Boulevard$/.test(r.district) ||
          (pools[plan[k].pool].rooms.length <= 1 && nearLine(r.routine[0])),
      )
      .sort((a, b) => hashString(a.r.id, seed) - hashString(b.r.id, seed));
    const stationCount = transit.stations.length;
    const W = AMBIENT_RIDER_KIND_WEIGHTS;
    const weightSum = W.commute + W.hop + W.excursion;
    for (const { r, k } of candidates) {
      if (riderIds.length >= AMBIENT_RIDERS_MAX) break;
      const post = r.routine[0];
      const pc = post % cols;
      let a = 0;
      for (let s2 = 1; s2 < stationCount; s2++)
        if (Math.abs(transit.stations[s2].c - pc) < Math.abs(transit.stations[a].c - pc)) a = s2;
      const others = transit.stations.map((_, s) => s).filter((s) => s !== a);
      const roll = r.rng.int(weightSum);
      let kind: RiderKind =
        roll < W.commute ? 'commute' : roll < W.commute + W.hop ? 'hop' : 'excursion';
      if (kind === 'excursion' && stationCount < 3) kind = 'commute';
      let b: number;
      let c3 = -1;
      if (kind === 'hop') {
        const next = [a - 1, a + 1].filter((s) => s >= 0 && s < stationCount);
        b = next[r.rng.int(next.length)];
      } else if (kind === 'commute') {
        const far = others.filter((s) => Math.abs(s - a) >= 2);
        const pickFrom = far.length > 0 ? far : others;
        b = pickFrom[r.rng.int(pickFrom.length)];
      } else {
        b = others[r.rng.int(others.length)];
        const rest = others.filter((s) => s !== b);
        c3 = rest[r.rng.int(rest.length)];
      }
      const used = c3 >= 0 ? [a, b, c3] : [a, b];
      if (used.some((s) => islands[s].length === 0 || forecourts[s].length === 0)) continue;
      const near = (s: number) => forecourts[s][r.rng.int(forecourts[s].length)];
      const pA = waitTile(a);
      const pB = alightTile(b);
      const nearB = near(b);
      const pB2 = waitTile(b);
      let routine: number[];
      const rideAt: Array<[number, number]> = [[1, b]];
      if (c3 >= 0) {
        const pC = alightTile(c3);
        const nearC = near(c3);
        const pC2 = waitTile(c3);
        const pA2 = alightTile(a);
        routine = [post, pA, pB, nearB, pB2, pC, nearC, pC2, pA2];
        rideAt.push([4, c3], [7, a]);
      } else {
        const pA2 = alightTile(a);
        routine = [post, pA, pB, nearB, pB2, pA2];
        rideAt.push([4, a]);
      }
      if (
        routine.length > AMBIENT_ROUTINE_MAX_STOPS ||
        !routine.every((t) => graph.connected(post, t))
      )
        continue;
      const rides = new Int8Array(routine.length).fill(-1);
      for (const [i, s] of rideAt) rides[i] = s;
      residents[k] = {
        ...r,
        routine: Int32Array.from(routine),
        rides,
        routineIndex: 0,
        legCache: new Map(),
      };
      riderIds.push(r.id);
      journeys.push({ id: r.id, kind, stations: used });
    }
  }
  const built = new AmbientWorld(
    residents,
    pathfinder,
    graph,
    seed,
    seatZone,
    seatNeighbours,
    placeOf,
  );
  built.coverage = coverage;
  built.riders = riderIds;
  built.journeys = journeys;
  built.eggs = eggHomes.map(({ job, pool }) => {
    const r = pool >= 0 ? residents.find((x) => x.job === job) : undefined;
    return { job, home: r ? r.district : null, id: r ? r.id : null };
  });
  if (transit) built.stationTiles = transit.stations.map((st) => new Set(st.island));
  // WORKING OFFICES: a pool that HOLDS A SEAT (the Palace, a Mission's room, the legacy single Office) shows at
  // most min(`onDutyCap`, ceil(T / `onDutyTilesPerResident`)) residents on duty at once, T = the pool's resident-eligible tiles, `p.tiles.length` (AmbientWorld.configureDuty). A seatless district, and any
  // rider, is never rotated. Nothing here changes who exists, how many, or any routine tile.
  const seats = input.seats ?? [];
  const holdsSeat = (p: Pool): boolean =>
    p.fallback === true
      ? seats.length > 0
      : seats.some((st) => placeOf(st.seatRow * cols + st.seatCol) === p.label);
  built.configureDuty(
    pools
      .map((p, i) => ({ p, i }))
      .filter(({ p }) => holdsSeat(p))
      .map(({ p, i }) => ({
        label: p.label,
        tiles: p.tiles.length,
        members: residents.filter((_, k) => plan[k].pool === i),
      })),
    input.onDutyCap ?? AMBIENT_WORKING_OFFICE_ON_DUTY,
    input.onDutyTilesPerResident ?? AMBIENT_WORKING_OFFICE_TILES_PER_RESIDENT,
  );
  return built;
}

/** A post with open space around it (at least two walkable neighbours), seeded. */
function pickPost(rng: SeededRng, pool: Pool, pathfinder: AmbientPathfinder, cols: number): number {
  let best = pool.tiles[rng.int(pool.tiles.length)];
  for (let tries = 0; tries < 12; tries++) {
    const t = pool.tiles[rng.int(pool.tiles.length)];
    const c = t % cols;
    const r = (t - c) / cols;
    let open = 0;
    for (const [dc, dr] of NEIGHBOURS) if (pathfinder.isWalkable(c + dc, r + dr)) open++;
    if (open >= 2) return t;
    best = t;
  }
  return best;
}

/** A patrol stop within `radius` (Manhattan) of the post, in the same place, seeded. Falls back to
 *  the post itself (a stay-put leg) rather than an arbitrarily distant tile. */
function pickStop(rng: SeededRng, post: number, radius: number, pool: Pool, cols: number): number {
  const pc = post % cols;
  const pr = (post - pc) / cols;
  for (let tries = 0; tries < 40; tries++) {
    const dc = rng.int(2 * radius + 1) - radius;
    const dr = rng.int(2 * radius + 1) - radius;
    if (Math.abs(dc) + Math.abs(dr) > radius || (dc === 0 && dr === 0)) continue;
    const c = pc + dc;
    const r = pr + dr;
    if (c < 0 || r < 0 || c >= cols) continue;
    const i = r * cols + c;
    if (pool.contains(i)) return i;
  }
  return post;
}
