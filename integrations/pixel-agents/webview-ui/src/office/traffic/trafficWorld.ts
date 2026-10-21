/**
 * DECORATIVE TRAFFIC (living city 6–9): the world's own lane-aligned vehicle furniture brought to life on the
 * lanes derived from its road tiles (trafficGraph.ts). Presentation only: a vehicle has no meaning, no
 * authority and no state beyond where it is drawn. An ambulance, a fire engine or a police cruiser is an
 * ORDINARY car that happens to look like one: it obeys every stop, has no siren and no priority, and nothing
 * here reads or writes a Mission, an agent, an observation or anything canonical.
 *
 * HOW IT MOVES. Each vehicle owns a short rolling ROUTE of tiles and CLAIMS tiles ahead of its head and holds
 * those under its body (2 to 4 tiles, its own sprite's length), in one shared occupancy map, so two vehicles can never hold the same tile and a
 * follower stops behind its leader (a queue). It brakes to stop exactly at the last tile it holds. Before every
 * intersection or zebra crossing it stops at the STOP LINE for a short dwell, then claims the WHOLE zone it will
 * cross (the path through the box or crossing, plus the tile it leaves onto) in one all-or-nothing step: a
 * vehicle is never stranded inside an intersection or across a crossing, and a circular wait cannot form
 * inside a box. Its choice at a box (straight, right, left) is a seeded draw; there is no path search, so
 * routing is bounded by construction (a route is at most TRAFFIC_ROUTE_MAX_TILES).
 *
 * PRIORITY. A vehicle never claims a tile a pedestrian stands on or is about to reach, nor one adjacent to a
 * real agent. If someone ever steps INTO a vehicle's tile, the vehicle yields: it fades out at once and its
 * tiles are released. Vehicles touch no walkability data, so decoration can never block a real agent. This
 * holds under reduced motion too (motion is held; priority never is).
 *
 * WHAT IS ANIMATED. Only furniture pieces that already sit on a lane, facing the lane's direction. They keep
 * their art and their place: the static copy is hidden at render time while its slot is active, so the layout
 * is untouched and 'off' is exactly the world as it was. Parked lots and the other street props stay parked.
 *
 * SAFE RESTART. Nothing here is saved or restored: a new world rebuilds the same slots from the same seed, so
 * a reload can never duplicate a vehicle, and no continuity across a restart is claimed.
 */
import {
  TRAFFIC_ACCEL,
  TRAFFIC_AGENT_HALO_TILES,
  TRAFFIC_BODY_MAX_TILES,
  TRAFFIC_BODY_MIN_TILES,
  TRAFFIC_BRAKE,
  TRAFFIC_CLAIM_AHEAD_TILES,
  TRAFFIC_END_DWELL_SEC,
  TRAFFIC_FADE_SEC,
  TRAFFIC_LANE_MIN_TILES,
  TRAFFIC_MAX_VEHICLES,
  TRAFFIC_RESPAWN_SEC,
  TRAFFIC_ROUTE_LOOKAHEAD_TILES,
  TRAFFIC_ROUTE_MAX_TILES,
  TRAFFIC_SEED,
  TRAFFIC_SPEED_MAX,
  TRAFFIC_SPEED_MIN,
  TRAFFIC_START_STAGGER_SEC,
  TRAFFIC_STOP_DWELL_SEC,
  TRAFFIC_STOP_JITTER_SEC,
  TRAFFIC_STUCK_SEC,
  TRAFFIC_TURN_WEIGHT_LEFT,
  TRAFFIC_TURN_WEIGHT_RIGHT,
  TRAFFIC_TURN_WEIGHT_STRAIGHT,
} from '../../constants.js';
import { hashString, SeededRng } from '../ambient/ambientRng.js';
import type { TrafficGraph } from './trafficGraph.js';
import {
  HEADING_DX,
  HEADING_DY,
  HEADING_E,
  HEADING_N,
  HEADING_S,
  HEADING_W,
  indexOf,
  KIND_BOX,
  KIND_ROAD,
  laneOf,
} from './trafficGraph.js';

/** The longest simulation step; the game loop already caps a frame, this only guards direct callers. */
export const MAX_STEP_SEC = 0.25;
/** Seconds a blocked vehicle at a stop line keeps its chosen movement before drawing again. */
export const RETARGET_SEC = 3;
/** Below this speed (tiles per second) a vehicle counts as stopped. */
export const STOPPED_SPEED = 0.25;
export const EPS = 1e-3;
/** A vehicle that yields to someone stepping into it fades this much faster. */
export const YIELD_FADE_FACTOR = 4;
/** A vehicle's route is trimmed once this many tiles of it lie behind the body, so the route stays short. */
export const ROUTE_TRIM_TILES = 16;

export type VehicleState = 'inactive' | 'gone' | 'in' | 'drive' | 'stop' | 'end' | 'out';

/** A movement through the zone ahead: the tiles to claim together, and where the route goes on from. */
interface Movement {
  readonly turn: 'straight' | 'right' | 'left';
  readonly group: readonly number[];
  readonly endLane: number;
  readonly endIdx: number;
  readonly atEnd: 'open' | 'segment';
}

export interface Vehicle {
  /** Durable local identity, from its slot: `veh-007`. */
  readonly id: string;
  readonly slot: number;
  /** TAXI, SEDAN_BLUE, POLICE_CRUISER ... (the part of the furniture type between VEHICLE_ and the facing). */
  readonly kind: string;
  /** True for the three kinds that LOOK like emergency vehicles. It changes nothing but the label. */
  readonly emergency: boolean;
  /** The placed furniture this vehicle brings to life (its static copy is hidden while it is active). */
  readonly homeUid: string;
  /** Tiles the body holds along its lane: its own sprite's length (TRAFFIC_BODY_MIN_TILES..MAX). */
  readonly body: number;
  state: VehicleState;
  /** 0..1 opacity while fading in or out. */
  fade: number;
  fadeRate: number;
  /** Rolling route (tile indices) and the position of the head along it, in route-index units. */
  route: number[];
  head: number;
  /** The highest route index this vehicle holds. */
  claimed: number;
  endLane: number;
  endIdx: number;
  routeEnd: 'open' | 'zone' | 'segment';
  /** Speed in tiles per second, and the cruise speed this vehicle prefers. */
  v: number;
  readonly vmax: number;
  stopTimer: number;
  stopNeed: number;
  retarget: number;
  pending: Movement | null;
  /** Start stagger, respawn delay and end dwell, in seconds. */
  timer: number;
  /** Seconds spent unable to move for any reason other than a stop-line dwell. */
  stuck: number;
  /** Pose for drawing, in tile units: the head's tile position (the tile's centre is its column and row). */
  hx: number;
  hy: number;
  heading: number;
  readonly home: { readonly lane: number; readonly idx: number };
  readonly rng: SeededRng;
}

export interface VehicleHome {
  readonly uid: string;
  readonly kind: string;
  readonly heading: number;
  readonly lane: number;
  /** Index in the lane of the head tile; the body occupies idx - (body - 1) .. idx. */
  readonly idx: number;
  readonly body: number;
}

/** What the world gives traffic to give way to, as tile indices (see markPeople). */
export interface TrafficPeople {
  /** Real agents (and the greeter): the tile each stands on. */
  agentOwn: readonly number[];
  /** Real agents: the tiles each is about to walk onto. */
  agentNear: readonly number[];
  /** Residents and pets: the tile each stands on. */
  walkerOwn: readonly number[];
  /** Residents and pets: the tiles each is about to walk onto. */
  walkerNear: readonly number[];
}

export const NO_PEOPLE: TrafficPeople = {
  agentOwn: [],
  agentNear: [],
  walkerOwn: [],
  walkerNear: [],
};

export interface TrafficCounters {
  spawns: number;
  /** Stop-line dwells completed (a vehicle stopped, waited and entered a zone). */
  stops: number;
  /** Times a vehicle gave way to someone stepping into its tile. */
  yields: number;
  /** Times a vehicle was removed after waiting longer than TRAFFIC_STUCK_SEC. */
  watchdogs: number;
  /** Times a claim was refused for a pedestrian (not for another vehicle). */
  pedestrianDenials: number;
  /** Times a claim was refused because another vehicle held the tile (a vehicle waiting for another). */
  vehicleDenials: number;
  /** Times a segment end was reached. */
  ends: number;
  /** The most vehicles stopped in one lane segment at once, ever (a queue). */
  maxQueue: number;
  /** Total tiles driven, in whole tiles. */
  tilesDriven: number;
}

const EMERGENCY_KINDS: ReadonlySet<string> = new Set([
  'POLICE_CRUISER',
  'FIRE_ENGINE',
  'AMBULANCE',
]);
const HEADING_OF_FACING: Readonly<Record<string, number>> = {
  RIGHT: HEADING_E,
  DOWN: HEADING_S,
  LEFT: HEADING_W,
  UP: HEADING_N,
};
const VEHICLE_TYPE = /^VEHICLE_(.+)_(UP|DOWN|LEFT|RIGHT)$/;

/** The facing words of the sprite family: kind + heading names the furniture type. */
export function vehicleType(kind: string, heading: number): string {
  return `VEHICLE_${kind}_${['RIGHT', 'DOWN', 'LEFT', 'UP'][heading]}`;
}

/**
 * The placed vehicles that already sit on a lane, facing the lane's direction, with their whole body (2 to 4 tiles) on
 * consecutive free lane tiles outside any zone. Only east-west pieces qualify in practice (a facing-up piece is
 * 2 wide); the others stay parked. Deterministic: sorted by row, then column.
 */
export function findVehicleHomes(
  furniture: readonly { uid: string; type: string; col: number; row: number }[],
  graph: TrafficGraph,
  entryOf: (
    type: string,
  ) => { footprintW: number; footprintH: number; backgroundTiles?: number } | undefined,
): VehicleHome[] {
  const homes: VehicleHome[] = [];
  for (const f of furniture) {
    const m = VEHICLE_TYPE.exec(f.type);
    if (!m) continue;
    const heading = HEADING_OF_FACING[m[2]];
    if (heading !== HEADING_E && heading !== HEADING_W) continue;
    const entry = entryOf(f.type);
    if (!entry) continue;
    const bg = entry.backgroundTiles ?? 0;
    const body = entry.footprintW;
    if (
      body < TRAFFIC_BODY_MIN_TILES ||
      body > TRAFFIC_BODY_MAX_TILES ||
      entry.footprintH - bg !== 1
    )
      continue;
    const row = f.row + bg;
    const tiles: number[] = [];
    for (let dc = 0; dc < body; dc++) tiles.push(row * graph.cols + f.col + dc);
    // the head is the leading end: east-most facing east, west-most facing west
    const headTile = heading === HEADING_E ? tiles[tiles.length - 1] : tiles[0];
    const code = graph.laneAt(headTile, heading);
    if (code < 0) continue;
    const lane = graph.lanes[laneOf(code)];
    const idx = indexOf(code);
    if (idx < body - 1) continue;
    let aligned = true;
    for (let k = 0; k < body; k++) {
      const t = lane.tiles[idx - k];
      if (!tiles.includes(t) || lane.kind[idx - k] !== KIND_ROAD) aligned = false;
    }
    // the lane must be long enough to be driven on at all
    if (!aligned || lane.tiles.length < TRAFFIC_LANE_MIN_TILES) continue;
    homes.push({ uid: f.uid, kind: m[1], heading, lane: lane.id, idx, body });
  }
  homes.sort((a, b) => {
    const ta = graph.lanes[a.lane].tiles[a.idx];
    const tb = graph.lanes[b.lane].tiles[b.idx];
    return (
      Math.floor(ta / graph.cols) - Math.floor(tb / graph.cols) ||
      (ta % graph.cols) - (tb % graph.cols)
    );
  });
  return homes.slice(0, TRAFFIC_MAX_VEHICLES);
}

export class TrafficWorld {
  readonly vehicles: Vehicle[] = [];
  readonly counters: TrafficCounters = {
    spawns: 0,
    stops: 0,
    yields: 0,
    watchdogs: 0,
    pedestrianDenials: 0,
    vehicleDenials: 0,
    ends: 0,
    maxQueue: 0,
    tilesDriven: 0,
  };
  /** Bumped whenever the set of hidden furniture changes (the owner rebuilds its furniture instances). */
  hiddenVersion = 0;
  private cap = 0;
  private hidden: ReadonlySet<string> = new Set();
  private readonly occ: Int16Array;
  private readonly standing: Uint32Array;
  private readonly danger: Uint32Array;
  private stamp = 0;
  private tick = 0;

  readonly graph: TrafficGraph;

  constructor(graph: TrafficGraph, homes: readonly VehicleHome[], seed = TRAFFIC_SEED) {
    this.graph = graph;
    this.occ = new Int16Array(graph.cols * graph.rows);
    this.standing = new Uint32Array(graph.cols * graph.rows);
    this.danger = new Uint32Array(graph.cols * graph.rows);
    // a seeded shuffle gives every cap a spread subset, not "the first N along the map"
    const order = new SeededRng(seed).shuffle(homes.map((_, i) => i));
    order.forEach((homeIndex, slot) => {
      const home = homes[homeIndex];
      const rng = new SeededRng(hashString(home.uid, seed));
      this.vehicles.push({
        id: `veh-${String(slot).padStart(3, '0')}`,
        slot,
        kind: home.kind,
        emergency: EMERGENCY_KINDS.has(home.kind),
        homeUid: home.uid,
        body: home.body,
        state: 'inactive',
        fade: 0,
        fadeRate: 1 / TRAFFIC_FADE_SEC,
        route: [],
        head: 0,
        claimed: 0,
        endLane: home.lane,
        endIdx: home.idx,
        routeEnd: 'open',
        v: 0,
        vmax: rng.range(TRAFFIC_SPEED_MIN, TRAFFIC_SPEED_MAX),
        stopTimer: 0,
        stopNeed: 0,
        retarget: 0,
        pending: null,
        timer: 0,
        stuck: 0,
        hx: 0,
        hy: 0,
        heading: home.heading,
        home: { lane: home.lane, idx: home.idx },
        rng,
      });
    });
  }

  get activeCap(): number {
    return this.cap;
  }

  /** The placed furniture whose static copy must not be drawn while its vehicle is active. */
  hiddenUids(): ReadonlySet<string> {
    return this.hidden;
  }

  /** Set how many vehicles are allowed. Slots past it go back to being parked furniture at once. */
  setCap(cap: number): void {
    const next = Math.max(0, Math.min(Math.floor(cap), this.vehicles.length));
    if (next === this.cap) return;
    this.cap = next;
    const hidden = new Set<string>();
    for (const v of this.vehicles) {
      if (v.slot < next) {
        hidden.add(v.homeUid);
        if (v.state === 'inactive') {
          v.state = 'gone';
          v.timer = v.rng.range(0, TRAFFIC_START_STAGGER_SEC);
        }
      } else if (v.state !== 'inactive') {
        this.releaseAll(v);
        v.state = 'inactive';
        v.fade = 0;
      }
    }
    this.hidden = hidden;
    this.hiddenVersion++;
  }

  /** Vehicles currently visible (any opacity above nothing). */
  visible(): Vehicle[] {
    return this.vehicles.filter((v) => v.state !== 'inactive' && v.state !== 'gone' && v.fade > 0);
  }

  /** One step. Motion is held under `reduced`, priority never is. */
  update(dt: number, people: TrafficPeople, reduced: boolean): void {
    if (this.cap === 0) return;
    this.markPeople(people);
    // priority first, every time: nobody is ever driven through, and a claim already held inside
    // an agent's halo is given back so the vehicle stops short of them
    for (const v of this.vehicles) {
      if (!this.onRoad(v)) continue;
      this.yieldIfStoodIn(v);
      if (this.onRoad(v)) this.releaseHaloClaims(v);
    }
    if (reduced) {
      this.settleStill();
      this.refreshPoses();
      return;
    }
    if (!(dt > 0)) return;
    const step = Math.min(dt, MAX_STEP_SEC);
    const n = this.vehicles.length;
    // a rotating start order, so no vehicle always wins a contested tile
    const start = this.tick % n;
    for (let k = 0; k < n; k++) this.step(this.vehicles[(start + k) % n], step);
    this.tick++;
    this.refreshPoses();
    this.measureQueues();
  }

  // ── people ──────────────────────────────────────────────────────────────

  private markPeople(people: TrafficPeople): void {
    const { cols, rows } = this.graph;
    this.stamp++;
    const s = this.stamp;
    const total = cols * rows;
    const halo = TRAFFIC_AGENT_HALO_TILES;
    for (const t of people.agentOwn) {
      if (t < 0 || t >= total) continue;
      this.standing[t] = s;
      const c = t % cols;
      const r = Math.floor(t / cols);
      for (let dy = -halo; dy <= halo; dy++) {
        for (let dx = -halo; dx <= halo; dx++) {
          const cc = c + dx;
          const rr = r + dy;
          if (cc >= 0 && rr >= 0 && cc < cols && rr < rows) this.danger[rr * cols + cc] = s;
        }
      }
    }
    for (const t of people.agentNear) if (t >= 0 && t < total) this.danger[t] = s;
    for (const t of people.walkerOwn) {
      if (t < 0 || t >= total) continue;
      this.standing[t] = s;
      this.danger[t] = s;
    }
    for (const t of people.walkerNear) if (t >= 0 && t < total) this.danger[t] = s;
  }

  private onRoad(v: Vehicle): boolean {
    return v.state === 'in' || v.state === 'drive' || v.state === 'stop' || v.state === 'end';
  }

  private keepFrom(v: Vehicle): number {
    return Math.max(0, Math.floor(v.head - (v.body - 1) - 0.5) + 1);
  }

  private yieldIfStoodIn(v: Vehicle): void {
    for (let k = this.keepFrom(v); k <= v.claimed && k < v.route.length; k++) {
      if (this.standing[v.route[k]] === this.stamp) {
        this.counters.yields++;
        this.leave(v, true);
        return;
      }
    }
  }

  /** Give back claims ahead of the body that now sit in a person's danger halo. The body itself
   *  is left to yieldIfStoodIn: stepping into it removes the vehicle, standing beside it only
   *  stops it claiming further. */
  private releaseHaloClaims(v: Vehicle): void {
    const body = Math.floor(v.head);
    while (
      v.claimed > body &&
      v.claimed < v.route.length &&
      this.danger[v.route[v.claimed]] === this.stamp
    ) {
      const tile = v.route[v.claimed];
      if (this.occ[tile] === v.slot + 1) this.occ[tile] = 0;
      v.claimed--;
    }
  }

  // ── claims ──────────────────────────────────────────────────────────────

  private canClaim(v: Vehicle, tile: number): boolean {
    const held = this.occ[tile];
    if (held !== 0 && held !== v.slot + 1) {
      this.counters.vehicleDenials++;
      return false;
    }
    if (this.danger[tile] === this.stamp) {
      this.counters.pedestrianDenials++;
      return false;
    }
    return true;
  }

  private claim(v: Vehicle, tile: number): void {
    this.occ[tile] = v.slot + 1;
  }

  private releaseBehind(v: Vehicle): void {
    const upTo = Math.min(this.keepFrom(v), v.route.length);
    for (let k = 0; k < upTo; k++)
      if (this.occ[v.route[k]] === v.slot + 1) this.occ[v.route[k]] = 0;
  }

  private releaseAll(v: Vehicle): void {
    for (const t of v.route) if (this.occ[t] === v.slot + 1) this.occ[t] = 0;
  }

  /** Start fading out: gracefully, or fast because someone stepped in. Its tiles are free for others at once. */
  private leave(v: Vehicle, fast: boolean): void {
    this.releaseAll(v);
    v.state = 'out';
    v.v = 0;
    v.fadeRate = (fast ? YIELD_FADE_FACTOR : 1) / TRAFFIC_FADE_SEC;
  }

  // ── spawn ───────────────────────────────────────────────────────────────

  /** Put a vehicle back at its home place if its body's tiles are free; returns whether it did. */
  private trySpawn(v: Vehicle, instant: boolean): boolean {
    const lane = this.graph.lanes[v.home.lane];
    const tiles: number[] = [];
    for (let k = v.body - 1; k >= 0; k--) tiles.push(lane.tiles[v.home.idx - k]);
    for (const t of tiles) if (!this.canClaim(v, t)) return false;
    for (const t of tiles) this.claim(v, t);
    v.route = tiles;
    v.head = v.body - 1;
    v.claimed = v.body - 1;
    v.endLane = v.home.lane;
    v.endIdx = v.home.idx;
    v.routeEnd = 'open';
    v.v = 0;
    v.stopTimer = 0;
    v.stuck = 0;
    v.pending = null;
    v.fadeRate = 1 / TRAFFIC_FADE_SEC;
    v.state = instant ? 'drive' : 'in';
    v.fade = instant ? 1 : 0;
    this.counters.spawns++;
    return true;
  }

  /** Reduced motion: no fading and no driving, but every active vehicle is shown, where it can be. */
  private settleStill(): void {
    for (const v of this.vehicles) {
      if (v.state === 'out') {
        this.releaseAll(v);
        v.state = 'gone';
        v.fade = 0;
      }
      if (v.state === 'gone') {
        if (this.trySpawn(v, true)) v.timer = 0;
      } else if (v.state === 'in') {
        v.state = 'drive';
        v.fade = 1;
      }
    }
  }

  // ── motion ──────────────────────────────────────────────────────────────

  private step(v: Vehicle, dt: number): void {
    switch (v.state) {
      case 'inactive':
        return;
      case 'gone':
        v.timer -= dt;
        if (v.timer <= 0 && !this.trySpawn(v, false)) v.timer = 1;
        return;
      case 'out':
        v.fade -= dt * v.fadeRate;
        if (v.fade <= 0) {
          v.fade = 0;
          this.releaseAll(v);
          v.state = 'gone';
          v.timer = TRAFFIC_RESPAWN_SEC;
        }
        return;
      case 'in':
        v.fade += dt / TRAFFIC_FADE_SEC;
        if (v.fade >= 1) {
          v.fade = 1;
          v.state = 'drive';
        }
        return;
    }
    if (v.timer > 0) {
      v.timer -= dt;
      return;
    }
    this.extendRoute(v);
    this.claimAhead(v);
    // speed: brake so as to stop exactly at the last tile held
    const gap = Math.max(0, v.claimed - v.head);
    const target = Math.min(v.vmax, Math.sqrt(2 * TRAFFIC_BRAKE * gap));
    v.v =
      v.v < target
        ? Math.min(target, v.v + TRAFFIC_ACCEL * dt)
        : Math.max(target, v.v - TRAFFIC_BRAKE * dt);
    const before = v.head;
    v.head = Math.min(v.claimed, v.head + v.v * dt);
    this.counters.tilesDriven += Math.floor(v.head) - Math.floor(before);
    this.releaseBehind(v);
    this.trim(v);

    const last = v.route.length - 1;
    const atEnd = v.head >= last - EPS && v.claimed >= last;
    let waiting = v.v < STOPPED_SPEED;
    if (v.routeEnd === 'segment' && atEnd) {
      v.state = 'end';
      v.timer = 0;
      v.stopTimer += dt;
      waiting = false;
      if (v.stopTimer >= TRAFFIC_END_DWELL_SEC) {
        this.counters.ends++;
        this.leave(v, false);
        return;
      }
    } else if (v.routeEnd === 'zone' && atEnd) {
      v.state = 'stop';
      v.stopTimer += dt;
      v.retarget -= dt;
      if (v.stopTimer >= v.stopNeed) {
        this.tryEnter(v);
        waiting = v.state === 'stop';
      } else waiting = false; // the dwell itself is not being stuck
    } else {
      v.state = 'drive';
      v.stopTimer = 0;
    }
    // the gridlock valve: a vehicle that cannot move for a very long time is removed and re-enters
    if (waiting) {
      v.stuck += dt;
      if (v.stuck > TRAFFIC_STUCK_SEC) {
        this.counters.watchdogs++;
        this.leave(v, false);
      }
    } else v.stuck = 0;
  }

  /** Append lane tiles to the route until the lookahead is satisfied, a zone starts, or the segment ends. */
  private extendRoute(v: Vehicle): void {
    const lane = this.graph.lanes[v.endLane];
    while (
      v.routeEnd === 'open' &&
      v.route.length - Math.ceil(v.head) < TRAFFIC_ROUTE_LOOKAHEAD_TILES &&
      v.route.length < TRAFFIC_ROUTE_MAX_TILES
    ) {
      const next = v.endIdx + 1;
      if (next >= lane.tiles.length) {
        v.routeEnd = 'segment';
        break;
      }
      if (lane.kind[next] !== KIND_ROAD) {
        v.routeEnd = 'zone';
        v.stopNeed = TRAFFIC_STOP_DWELL_SEC + v.rng.next() * TRAFFIC_STOP_JITTER_SEC;
        v.stopTimer = 0;
        break;
      }
      v.route.push(lane.tiles[next]);
      v.endIdx = next;
    }
  }

  /** Claim the tiles just ahead, one at a time, as far as they are free of vehicles and pedestrians. */
  private claimAhead(v: Vehicle): void {
    const limit = Math.min(v.route.length - 1, Math.floor(v.head) + TRAFFIC_CLAIM_AHEAD_TILES);
    while (v.claimed < limit) {
      const t = v.route[v.claimed + 1];
      if (!this.canClaim(v, t)) break;
      this.claim(v, t);
      v.claimed++;
    }
  }

  /** Drop the part of the route that is behind the body, so the route stays short. */
  private trim(v: Vehicle): void {
    const drop = this.keepFrom(v);
    if (drop < ROUTE_TRIM_TILES) return;
    v.route.splice(0, drop);
    v.head -= drop;
    v.claimed -= drop;
  }

  // ── zones ───────────────────────────────────────────────────────────────

  /** The tiles of a straight run through the zone that starts at `from`, plus the tile it leaves onto. */
  private runFrom(
    laneId: number,
    from: number,
  ): { tiles: number[]; endIdx: number; atEnd: 'open' | 'segment' } {
    const lane = this.graph.lanes[laneId];
    const tiles: number[] = [];
    let k = from;
    while (k < lane.tiles.length && lane.kind[k] !== KIND_ROAD) tiles.push(lane.tiles[k++]);
    if (k < lane.tiles.length) {
      tiles.push(lane.tiles[k]);
      return { tiles, endIdx: k, atEnd: 'open' };
    }
    return { tiles, endIdx: lane.tiles.length - 1, atEnd: 'segment' };
  }

  /** Every way through the zone ahead of the stop line this vehicle is waiting at. */
  private movements(v: Vehicle): Movement[] {
    const g = this.graph;
    const lane = g.lanes[v.endLane];
    const s = v.endIdx;
    const out: Movement[] = [];
    let j = s + 1;
    while (j < lane.tiles.length && lane.kind[j] !== KIND_ROAD) j++;
    const straight = this.runFrom(v.endLane, s + 1);
    out.push({
      turn: 'straight',
      group: straight.tiles,
      endLane: v.endLane,
      endIdx: straight.endIdx,
      atEnd: straight.atEnd,
    });
    let e = -1;
    for (let k = s + 1; k < j; k++) {
      if (lane.kind[k] === KIND_BOX) {
        e = k;
        break;
      }
    }
    if (e < 0) return out;
    const h = lane.heading;
    const entry = lane.tiles[e];
    const ex = entry % g.cols;
    const ey = Math.floor(entry / g.cols);
    const prefix = lane.tiles.slice(s + 1, e);
    const tile = (x: number, y: number): number =>
      x < 0 || y < 0 || x >= g.cols || y >= g.rows ? -1 : y * g.cols + x;
    const inBox = (t: number): boolean => t >= 0 && g.boxTiles[t] === 1;
    for (const turn of ['right', 'left'] as const) {
      const nh = turn === 'right' ? (h + 1) & 3 : (h + 3) & 3;
      let path: number[];
      let exit: number;
      if (turn === 'right') {
        path = [entry];
        exit = tile(ex + HEADING_DX[nh], ey + HEADING_DY[nh]);
      } else {
        const d = [HEADING_DX[h], HEADING_DY[h]];
        const dl = [HEADING_DX[nh], HEADING_DY[nh]];
        path = [
          entry,
          tile(ex + d[0], ey + d[1]),
          tile(ex + 2 * d[0], ey + 2 * d[1]),
          tile(ex + 2 * d[0] + dl[0], ey + 2 * d[1] + dl[1]),
          tile(ex + 2 * d[0] + 2 * dl[0], ey + 2 * d[1] + 2 * dl[1]),
        ];
        exit = tile(ex + 2 * d[0] + 3 * dl[0], ey + 2 * d[1] + 3 * dl[1]);
      }
      if (exit < 0 || !path.every(inBox)) continue;
      const code = g.laneAt(exit, nh);
      if (code < 0) continue;
      const lane2 = g.lanes[laneOf(code)];
      const idx2 = indexOf(code);
      if (lane2.tiles.length - idx2 < TRAFFIC_LANE_MIN_TILES) continue;
      const run = this.runFrom(lane2.id, idx2);
      out.push({
        turn,
        group: [...prefix, ...path, ...run.tiles],
        endLane: lane2.id,
        endIdx: run.endIdx,
        atEnd: run.atEnd,
      });
    }
    return out;
  }

  private chooseMovement(v: Vehicle): Movement | null {
    const options = this.movements(v);
    if (options.length === 0) return null;
    const weight = (m: Movement): number =>
      m.turn === 'straight'
        ? TRAFFIC_TURN_WEIGHT_STRAIGHT
        : m.turn === 'right'
          ? TRAFFIC_TURN_WEIGHT_RIGHT
          : TRAFFIC_TURN_WEIGHT_LEFT;
    let total = 0;
    for (const m of options) total += weight(m);
    let draw = v.rng.next() * total;
    for (const m of options) {
      draw -= weight(m);
      if (draw <= 0) return m;
    }
    return options[0];
  }

  /** At the stop line, after the dwell: claim the whole zone and the tile beyond it together, or wait. */
  private tryEnter(v: Vehicle): void {
    if (!v.pending || v.retarget <= 0) {
      v.pending = this.chooseMovement(v);
      v.retarget = RETARGET_SEC;
      if (!v.pending) {
        v.routeEnd = 'segment';
        return;
      }
    }
    const group = v.pending.group;
    for (const t of group) if (!this.canClaim(v, t)) return;
    for (const t of group) this.claim(v, t);
    for (const t of group) v.route.push(t);
    v.claimed = v.route.length - 1;
    v.endLane = v.pending.endLane;
    v.endIdx = v.pending.endIdx;
    v.routeEnd = v.pending.atEnd;
    v.pending = null;
    v.stopTimer = 0;
    v.stopNeed = TRAFFIC_STOP_DWELL_SEC + v.rng.next() * TRAFFIC_STOP_JITTER_SEC;
    v.state = 'drive';
    this.counters.stops++;
  }

  // ── pose and measurement ────────────────────────────────────────────────

  private refreshPoses(): void {
    const { cols } = this.graph;
    for (const v of this.vehicles) {
      if (v.route.length === 0 || v.state === 'inactive' || v.state === 'gone') continue;
      const i = Math.min(v.route.length - 1, Math.max(0, Math.floor(v.head)));
      const f = v.head - i;
      const a = v.route[i];
      const b = v.route[Math.min(i + 1, v.route.length - 1)];
      const ax = a % cols;
      const ay = Math.floor(a / cols);
      const bx = b % cols;
      const by = Math.floor(b / cols);
      v.hx = ax + (bx - ax) * f;
      v.hy = ay + (by - ay) * f;
      let dx = bx - ax;
      let dy = by - ay;
      if (dx === 0 && dy === 0 && i > 0) {
        const p = v.route[i - 1];
        dx = ax - (p % cols);
        dy = ay - Math.floor(p / cols);
      }
      if (dx > 0) v.heading = HEADING_E;
      else if (dx < 0) v.heading = HEADING_W;
      else if (dy > 0) v.heading = HEADING_S;
      else if (dy < 0) v.heading = HEADING_N;
    }
  }

  private measureQueues(): void {
    const stopped = new Map<number, number>();
    for (const v of this.vehicles) {
      if (
        (v.state === 'drive' || v.state === 'stop') &&
        v.v < STOPPED_SPEED &&
        v.route.length > 0
      ) {
        stopped.set(v.endLane, (stopped.get(v.endLane) ?? 0) + 1);
      }
    }
    for (const n of stopped.values()) if (n > this.counters.maxQueue) this.counters.maxQueue = n;
  }

  /** A snapshot of what is happening now (for tests and the budget measurements). */
  snapshot(): {
    active: number;
    driving: number;
    stopped: number;
    fading: number;
    absent: number;
    routeMax: number;
  } {
    let driving = 0;
    let stopped = 0;
    let fading = 0;
    let absent = 0;
    let routeMax = 0;
    let active = 0;
    for (const v of this.vehicles) {
      if (v.state === 'inactive') continue;
      active++;
      routeMax = Math.max(routeMax, v.route.length);
      if (v.state === 'gone') absent++;
      else if (v.state === 'in' || v.state === 'out') fading++;
      else if (v.v >= STOPPED_SPEED) driving++;
      else stopped++;
    }
    return { active, driving, stopped, fading, absent, routeMax };
  }

  /** The vehicle holding a tile, or null. For tests that audit the occupancy invariant. */
  holder(tile: number): Vehicle | null {
    const slot = this.occ[tile];
    return slot === 0 ? null : (this.vehicles[slot - 1] ?? null);
  }

  /** The tiles a vehicle believes it holds: its own claim, from its tail to the last tile it has claimed. The
   *  no-shared-tile audit reads THIS (not the occupancy map), so a lost or overwritten claim cannot hide. */
  heldTiles(v: Vehicle): number[] {
    // a vehicle that is absent, parked or fading out has released everything, whatever its old route says
    return this.onRoad(v) ? v.route.slice(this.keepFrom(v), v.claimed + 1) : [];
  }
}
