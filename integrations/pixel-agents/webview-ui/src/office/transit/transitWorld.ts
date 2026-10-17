/**
 * THE SUBWAY (M3 S3) — a local, decorative transit line: trains that actually move between stations
 * and ambient residents that actually board, ride and alight. Presentation only: no schedule, no
 * timetable authority, no Mission or service meaning, nothing written anywhere.
 *
 * The line is DERIVED from world data, never stored as new layout fields (the layout schema stays
 * strict):
 * - Track rows are the two rows carrying the most SUBWAY_RAIL furniture. The upper one is eastbound,
 *   the lower one westbound.
 * - The track span is the extent of the rails.
 * - Stations are areas whose label ends in "Station". Each has an island platform (the walkable row
 *   between the tracks) and a forecourt (the row above the eastbound track's platform).
 *
 * Trains: push-pull sets (a cab at each end, two passenger cars). Each runs end to end, stopping at
 * every station. At a terminus it dwells (passengers alight), crosses over to the other track
 * (visible lateral move), dwells again (passengers board) and departs the other way. Two trains run
 * mirror-image schedules, so they never share a track segment. Kinematics: accelerate to cruise,
 * brake to stop exactly at the platform centre. Bounded: a fixed train count, a bounded event log.
 */
import {
  TRANSIT_ACCEL_TILES_PER_SEC2,
  TRANSIT_CROSSOVER_SEC,
  TRANSIT_CRUISE_TILES_PER_SEC,
  TRANSIT_DWELL_SEC,
  TRANSIT_EVENT_LOG_MAX,
  TRANSIT_FRAME_SEC,
} from '../../constants.js';
import type { OfficeLayout } from '../types.js';
import { TILE_SIZE } from '../types.js';

export interface TransitStation {
  readonly name: string;
  /** Platform centre column (where a train's centre stops). */
  readonly c: number;
  /** Walkable island-platform tiles (indices) between the tracks: where riders wait and alight. */
  readonly island: readonly number[];
}

export interface TransitLine {
  readonly cols: number;
  /** Eastbound (upper) and westbound (lower) track rows. */
  readonly eastRow: number;
  readonly westRow: number;
  readonly x0: number;
  readonly x1: number;
  /** West to east. */
  readonly stations: readonly TransitStation[];
}

export type TrainState = 'run' | 'dwell' | 'crossover' | 'dwell-turn';
export interface Train {
  readonly id: string;
  /** +1 eastbound, -1 westbound. */
  dir: 1 | -1;
  /** Train centre, in tiles (column units). */
  x: number;
  /** Speed, tiles per second. */
  v: number;
  state: TrainState;
  timer: number;
  /** The station index the train is heading for (or standing at). */
  target: number;
  /** Track row the train is on (the destination row during a crossover). */
  row: number;
  /** Pixel y of the train's rail line (interpolated during a crossover). */
  railY: number;
  passengers: string[];
  frame: 0 | 1;
  frameTimer: number;
  /** Stations served, for evidence. */
  stopsMade: number;
}

export type TransitEventKind = 'arrive' | 'depart' | 'board' | 'alight' | 'crossover';
export interface TransitEvent {
  readonly t: number;
  readonly kind: TransitEventKind;
  readonly train: string;
  readonly station: string;
  readonly resident?: string;
  /** On an alighting: the rider was restored aboard from a snapshot (it boarded in an earlier runtime). */
  readonly restored?: true;
}

/** One train's validated restore step (planRestore / commitRestore). */
export interface TrainRestorePlan {
  tr: Train;
  dir: 1 | -1;
  x: number;
  target: number;
  row: number;
  state: TrainState;
  timer: number;
  v: number;
  passengers: string[];
}

/** What the transit line needs from the ambient population (implemented by AmbientWorld). */
export interface TransitRiders {
  /** Residents waiting on this station's island platform for a ride, with the station index each wants. */
  waitingAt(station: number): Array<{ id: string; destination: number }>;
  board(id: string, train: string): boolean;
  alight(id: string, station: number): boolean;
  /** Keep a rider's (hidden) position with its train, for the probe and the Map. */
  carry(id: string, x: number, y: number): void;
  destinationOf(id: string): number;
}

const RAIL = 'SUBWAY_RAIL';
const STATION_SUFFIX = /Station$/;

/** The line from the world's own data, or null when the world has no subway. */
export function deriveTransitLine(
  layout: Pick<OfficeLayout, 'cols' | 'rows' | 'furniture'> & Partial<Pick<OfficeLayout, 'areaTiles' | 'areas'>>,
  isWalkable: (col: number, row: number) => boolean,
): TransitLine | null {
  const perRow = new Map<number, { n: number; x0: number; x1: number }>();
  for (const f of layout.furniture) {
    if (f.type !== RAIL) continue;
    const e = perRow.get(f.row) ?? { n: 0, x0: Infinity, x1: -Infinity };
    e.n++;
    e.x0 = Math.min(e.x0, f.col);
    e.x1 = Math.max(e.x1, f.col);
    perRow.set(f.row, e);
  }
  const rows = [...perRow.entries()].sort((a, b) => b[1].n - a[1].n).slice(0, 2);
  if (rows.length < 2) return null;
  const [eastRow, westRow] = rows.map(([r]) => r).sort((a, b) => a - b);
  if (westRow - eastRow !== 2) return null;
  const islandRow = eastRow + 1;
  const x0 = Math.min(...rows.map(([, e]) => e.x0));
  const x1 = Math.max(...rows.map(([, e]) => e.x1));
  const stations: TransitStation[] = [];
  const labels = (layout.areas ?? []).map((a) => a.label).filter((l) => STATION_SUFFIX.test(l));
  for (const name of labels) {
    const island: number[] = [];
    for (let c = x0; c <= x1; c++) {
      const i = islandRow * layout.cols + c;
      if (layout.areaTiles?.[i] === name && isWalkable(c, islandRow)) island.push(i);
    }
    if (island.length < 4) continue;
    const cs = island.map((i) => i % layout.cols);
    stations.push({ name, c: Math.round((Math.min(...cs) + Math.max(...cs) + 1) / 2), island });
  }
  stations.sort((a, b) => a.c - b.c);
  if (stations.length < 2) return null;
  return { cols: layout.cols, eastRow, westRow, x0, x1, stations };
}

/** The pixel line the wheels sit on: the near (lower) rail of the track tile. */
const railYOf = (row: number) => row * TILE_SIZE + 12;

export class TransitWorld {
  readonly trains: Train[];
  readonly events: TransitEvent[] = [];
  /** Totals since load (bounded counters, for evidence). */
  readonly totals = { boardings: 0, alightings: 0, arrivals: 0, crossovers: 0 };
  /** Riders restored aboard from a snapshot: their boarding happened in an EARLIER runtime. */
  private readonly restoredAboard = new Set<string>();
  private t = 0;
  readonly line: TransitLine;
  private readonly riders: TransitRiders | null;

  constructor(line: TransitLine, riders: TransitRiders | null) {
    this.line = line;
    this.riders = riders;
    const last = line.stations.length - 1;
    this.trains = [
      { id: 'train-1', dir: 1, x: line.stations[0].c, v: 0, state: 'dwell-turn', timer: 3, target: 0, row: line.eastRow, railY: railYOf(line.eastRow), passengers: [], frame: 0, frameTimer: 0, stopsMade: 0 },
      { id: 'train-2', dir: -1, x: line.stations[last].c, v: 0, state: 'dwell-turn', timer: 3, target: last, row: line.westRow, railY: railYOf(line.westRow), passengers: [], frame: 0, frameTimer: 0, stopsMade: 0 },
    ];
  }

  private log(kind: TransitEventKind, train: Train, station: number, resident?: string, restored = false): void {
    this.events.push({ t: Math.round(this.t * 1000) / 1000, kind, train: train.id, station: this.line.stations[station].name, ...(resident ? { resident } : {}), ...(restored ? { restored: true } : {}) });
    if (this.events.length > TRANSIT_EVENT_LOG_MAX) this.events.splice(0, this.events.length - TRANSIT_EVENT_LOG_MAX);
  }

  private alightAll(train: Train, station: number): void {
    if (!this.riders) return;
    const stay: string[] = [];
    for (const id of train.passengers) {
      if (this.riders.destinationOf(id) === station && this.riders.alight(id, station)) {
        this.totals.alightings++;
        const restored = this.restoredAboard.delete(id);
        this.log('alight', train, station, id, restored);
      } else stay.push(id);
    }
    train.passengers = stay;
  }

  private boardAll(train: Train, station: number): void {
    if (!this.riders) return;
    for (const w of this.riders.waitingAt(station)) {
      const ahead = (w.destination - station) * train.dir > 0;
      if (!ahead || w.destination === station) continue;
      if (this.riders.board(w.id, train.id)) {
        train.passengers.push(w.id);
        this.totals.boardings++;
        this.log('board', train, station, w.id);
      }
    }
  }

  update(dt: number): void {
    this.t += dt;
    const stations = this.line.stations;
    const last = stations.length - 1;
    for (const tr of this.trains) {
      if (tr.state === 'run') {
        const goal = stations[tr.target].c;
        const dist = (goal - tr.x) * tr.dir;
        const brake = (tr.v * tr.v) / (2 * TRANSIT_ACCEL_TILES_PER_SEC2);
        if (dist <= brake + 0.02) tr.v = Math.max(0.35, tr.v - TRANSIT_ACCEL_TILES_PER_SEC2 * dt);
        else tr.v = Math.min(TRANSIT_CRUISE_TILES_PER_SEC, tr.v + TRANSIT_ACCEL_TILES_PER_SEC2 * dt);
        tr.x += tr.dir * Math.min(tr.v * dt, Math.max(0, dist));
        tr.frameTimer += dt;
        if (tr.frameTimer >= TRANSIT_FRAME_SEC) {
          tr.frameTimer = 0;
          tr.frame = tr.frame === 0 ? 1 : 0;
        }
        if ((goal - tr.x) * tr.dir <= 0.01) {
          tr.x = goal;
          tr.v = 0;
          tr.state = 'dwell';
          tr.timer = TRANSIT_DWELL_SEC;
          tr.stopsMade++;
          this.totals.arrivals++;
          this.log('arrive', tr, tr.target);
          this.alightAll(tr, tr.target);
          const terminus = (tr.dir === 1 && tr.target === last) || (tr.dir === -1 && tr.target === 0);
          if (!terminus) this.boardAll(tr, tr.target);
        }
      } else if (tr.state === 'dwell') {
        tr.timer -= dt;
        if (tr.timer <= 0) {
          const terminus = (tr.dir === 1 && tr.target === last) || (tr.dir === -1 && tr.target === 0);
          if (terminus) {
            tr.state = 'crossover';
            tr.timer = TRANSIT_CROSSOVER_SEC;
            tr.row = tr.dir === 1 ? this.line.westRow : this.line.eastRow;
            this.totals.crossovers++;
            this.log('crossover', tr, tr.target);
          } else {
            this.log('depart', tr, tr.target);
            tr.target += tr.dir;
            tr.state = 'run';
          }
        }
      } else if (tr.state === 'crossover') {
        tr.timer -= dt;
        const from = railYOf(tr.row === this.line.westRow ? this.line.eastRow : this.line.westRow);
        const to = railYOf(tr.row);
        const k = Math.min(1, Math.max(0, 1 - tr.timer / TRANSIT_CROSSOVER_SEC));
        tr.railY = from + (to - from) * k;
        if (tr.timer <= 0) {
          tr.railY = to;
          tr.dir = tr.dir === 1 ? -1 : 1;
          tr.state = 'dwell-turn';
          tr.timer = TRANSIT_DWELL_SEC;
          this.boardAll(tr, tr.target);
        }
      } else {
        // 'dwell-turn': standing at a terminus after crossing over, boarding for the run back
        tr.timer -= dt;
        if (tr.timer <= 0) {
          this.boardAll(tr, tr.target);
          this.log('depart', tr, tr.target);
          tr.target += tr.dir;
          tr.state = 'run';
        }
      }
      if (this.riders) for (const id of tr.passengers) this.riders.carry(id, tr.x * TILE_SIZE, tr.railY);
    }
  }

  /**
   * RESTORE, step 1 (validate, change nothing): every saved train must be one of this line's trains,
   * on the right track for its direction and state, inside the track span, standing exactly at its
   * station when dwelling or crossing over, with every rider aboard heading to a station still AHEAD.
   * Returns the plan, or the reason the whole snapshot is refused.
   */
  planRestore(
    entries: ReadonlyArray<readonly [string, number, number, number, number, number, number, number, readonly string[]]>,
    destinationOf: (resident: string) => number,
  ): TrainRestorePlan[] | string {
    const last = this.line.stations.length - 1;
    if (entries.length !== this.trains.length) return `the snapshot holds ${entries.length} trains; this line runs ${this.trains.length}`;
    const out: TrainRestorePlan[] = [];
    for (const [id, dir, x, target, row, code, timer, v, passengers] of entries) {
      const tr = this.trains.find((t) => t.id === id);
      if (!tr) return `train ${id} is not on this line`;
      if (dir !== 1 && dir !== -1) return `train ${id}: direction malformed`;
      if (target < 0 || target > last) return `train ${id}: station ${target} is not on this line`;
      const state = (['run', 'dwell', 'crossover', 'dwell-turn'] as const)[code];
      if (!state) return `train ${id}: state malformed`;
      const runRow = dir === 1 ? this.line.eastRow : this.line.westRow;
      const otherRow = dir === 1 ? this.line.westRow : this.line.eastRow;
      if (row !== (state === 'crossover' ? otherRow : runRow)) return `train ${id}: on the wrong track for its direction and state`;
      if (x < this.line.x0 || x > this.line.x1) return `train ${id}: off the track span`;
      const atStation = Math.abs(this.line.stations[target].c - x) < 0.01;
      if (state !== 'run' && !atStation) return `train ${id}: ${state} away from its station`;
      if (state === 'crossover' && !((dir === 1 && target === last) || (dir === -1 && target === 0))) return `train ${id}: crossing over away from a terminus`;
      const dirAfter = state === 'crossover' ? -dir : dir;
      for (const p of passengers) {
        const d = destinationOf(p);
        if (d < 0) return `train ${id}: rider ${p} has no pending destination`;
        const ahead = state === 'run' ? (d - target) * dir >= 0 : (d - target) * dirAfter > 0;
        if (!ahead) return `train ${id}: rider ${p} is heading to a station behind the train`;
      }
      out.push({ tr, dir: dir as 1 | -1, x, target, row, state, timer, v: state === 'run' ? v : 0, passengers: [...passengers] });
    }
    return out;
  }

  /** RESTORE, step 2: apply the plan. Emits NO event and moves NO counter. Riders restored aboard are
   *  remembered, so their later real alighting is logged `restored: true`. Animation frames reset. */
  commitRestore(plan: readonly TrainRestorePlan[]): void {
    this.restoredAboard.clear();
    for (const p of plan) {
      const tr = p.tr;
      tr.dir = p.dir;
      tr.x = p.x;
      tr.target = p.target;
      tr.row = p.row;
      tr.state = p.state;
      tr.timer = p.timer;
      tr.v = p.v;
      tr.passengers = [...p.passengers];
      tr.frame = 0;
      tr.frameTimer = 0;
      if (p.state === 'crossover') {
        const from = railYOf(p.row === this.line.westRow ? this.line.eastRow : this.line.westRow);
        const to = railYOf(p.row);
        const k = Math.min(1, Math.max(0, 1 - p.timer / TRANSIT_CROSSOVER_SEC));
        tr.railY = from + (to - from) * k;
      } else tr.railY = railYOf(p.row);
      for (const id of p.passengers) this.restoredAboard.add(id);
    }
  }

  /** Read-only snapshot for the probe and evidence. */
  snapshot(): { trains: Array<{ id: string; dir: number; x: number; railY: number; state: TrainState; target: string; passengers: string[]; stopsMade: number }>; totals: TransitWorld['totals']; events: readonly TransitEvent[] } {
    return {
      trains: this.trains.map((tr) => ({ id: tr.id, dir: tr.dir, x: Math.round(tr.x * 100) / 100, railY: Math.round(tr.railY), state: tr.state, target: this.line.stations[tr.target].name, passengers: [...tr.passengers], stopsMade: tr.stopsMade })),
      totals: { ...this.totals },
      events: this.events.slice(-TRANSIT_EVENT_LOG_MAX),
    };
  }
}

/** A train's length in tiles (two 6-tile cabs, two 4-tile cars), for the Map marker. */
export const TRAIN_LENGTH_TILES = 20;

/** The cars of a train, left to right, with their sprite types (cabs at both ends). */
export const TRAIN_CARS: ReadonlyArray<{ type: string; frameB?: string }> = [
  { type: 'SUBWAY_LEAD_LEFT' },
  { type: 'SUBWAY_CAR_A', frameB: 'SUBWAY_CAR_B' },
  { type: 'SUBWAY_CAR_A', frameB: 'SUBWAY_CAR_B' },
  { type: 'SUBWAY_LEAD_RIGHT' },
];
