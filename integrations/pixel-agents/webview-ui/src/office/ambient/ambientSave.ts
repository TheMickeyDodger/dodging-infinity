/**
 * THE AMBIENT SNAPSHOT on the webview side (M3 S4): serialize the decorative simulation into the
 * shared contract (core/src/ambientSave.ts, version 2) and restore a saved one.
 *
 * WHAT IS RESTORED, from the saved point:
 * - every resident's tile, routine index (its pending leg) and state with its remaining timer:
 *   standing, waiting for a train, or ABOARD a train;
 * - every train's position, direction, next station, track, operating state (running, dwelling,
 *   crossing over, terminus dwell), remaining timer, speed and ONBOARD membership.
 * A rider's pending destination is its routine index read through the world's own ride plan, so a
 * restored journey CONTINUES: the train resumes from its saved progress, and the rider alights when
 * the train actually reaches its destination after the runtime resumes.
 *
 * BOUNDED, RECONSTRUCTIBLE RESETS (not saved, rebuilt):
 * - a WALKING resident resumes by re-planning from its saved tile toward its saved pending stop
 *   (paths and leg caches are derived; a resident part-way between two tiles resumes from the
 *   centre of the tile it was on);
 * - animation microframes start from frame 0.
 *
 * GUARANTEES:
 * - LOAD FABRICATES NOTHING: no boarding, arrival or alighting event and no counter is emitted by a
 *   restore. A restored rider's later real alighting is logged with `restored: true`, so it never
 *   reads as a ride completed in this runtime.
 * - CLOSED TIME NEVER ADVANCES A JOURNEY: `savedAt` is informational only.
 * - NO DUPLICATES: residents are rebuilt from the world; a snapshot only restores state for exactly
 *   that resident set. A rider aboard is one resident in state `riding`, referenced by its train.
 * - REFUSAL WITH A REASON: a snapshot that fails the contract, belongs to another world, or does not
 *   fit this world (any resident, tile, ride or train inconsistent) is refused WHOLE, before anything
 *   changes. Nothing is ever quietly relocated or resolved to arrival.
 */
import type { AmbientSaveResident, AmbientSaveState, AmbientSaveStateCode, AmbientSaveTrain, AmbientSaveTrainState } from '../../../../core/src/ambientSave.js';
import { AMBIENT_SAVE_VERSION, checkAmbientSave } from '../../../../core/src/ambientSave.js';
import type { TrainState, TransitWorld } from '../transit/transitWorld.js';
import type { AmbientWorld } from './ambientWorld.js';

/** FNV-1a over a sequence of numbers (32-bit, hex). */
function fnv(values: Iterable<number>): string {
  let h = 0x811c9dc5;
  for (const v of values) {
    h ^= v & 0xffff;
    h = Math.imul(h, 0x01000193) >>> 0;
    h ^= (v >>> 16) & 0xffff;
    h = Math.imul(h, 0x01000193) >>> 0;
  }
  return h.toString(16).padStart(8, '0');
}
/** Every resident's routine tiles and ride plan, in roster order: what a saved journey is a journey THROUGH. */
function* journeyPlan(world: AmbientWorld): Iterable<number> {
  for (const r of world.residents) {
    for (const t of r.routine) yield t;
    yield -1;
    if (r.rides) for (const v of r.rides) yield v + 2;
    yield -2;
  }
}
function* chars(list: Iterable<string>): Iterable<number> {
  for (const s of list) {
    for (let i = 0; i < s.length; i++) yield s.charCodeAt(i);
    yield 0;
  }
}

type FingerprintLayout = {
  cols: number;
  rows: number;
  tiles: readonly number[];
  layoutRevision?: number;
  furniture?: ReadonlyArray<{ type: string; col: number; row: number }>;
};

/** The world (tiles AND furniture) + resident-set + journey-plan fingerprint a snapshot is bound to. The plan is in it
 *  so a snapshot is only ever applied to the routines and rides it was saved from: a roster whose routines changed
 *  refuses the old snapshot as another world, rather than putting a saved resident on a different journey. */
export function ambientFingerprint(layout: FingerprintLayout, world: AmbientWorld): string {
  return [
    `${layout.cols}x${layout.rows}`,
    `r${layout.layoutRevision ?? 0}`,
    `t${fnv(layout.tiles)}`,
    `f${fnv(chars((layout.furniture ?? []).map((f) => `${f.type}@${f.col},${f.row}`)))}`,
    `a${fnv(chars(world.residents.map((r) => r.id)))}`,
    `j${fnv(journeyPlan(world))}`,
    `n${world.residents.length}`,
  ].join('|');
}

// 'need-path' is a walk about to be planned toward the SAME pending stop, so it saves as walking
const RESIDENT_CODES: Record<string, AmbientSaveStateCode> = { dwell: 0, walk: 1, 'need-path': 1, waiting: 2, riding: 3, retry: 4 };
const TRAIN_CODES: Record<TrainState, AmbientSaveTrainState> = { run: 0, dwell: 1, crossover: 2, 'dwell-turn': 3 };
const r3 = (v: number) => Math.round(v * 1000) / 1000;

export function serializeAmbient(layout: FingerprintLayout, world: AmbientWorld, transit: TransitWorld | null, savedAt: number): AmbientSaveState {
  const residents: AmbientSaveResident[] = world.residents.map((r) => {
    const code = RESIDENT_CODES[r.state] ?? 0;
    const timer = code === 0 || code === 2 || code === 4 ? Math.max(0, r3(r.timer)) : 0;
    return [r.id, r.routineIndex, r.tileCol, r.tileRow, code, timer];
  });
  const trains: AmbientSaveTrain[] = (transit?.trains ?? []).map((t) => [t.id, t.dir, r3(t.x), t.target, t.row, TRAIN_CODES[t.state], Math.max(0, r3(t.timer)), Math.max(0, r3(t.v)), [...t.passengers]]);
  return { v: AMBIENT_SAVE_VERSION, world: ambientFingerprint(layout, world), savedAt, residents, trains };
}

export interface AmbientRestoreResult {
  outcome: 'applied' | 'refused' | 'other-world';
  reason?: string;
  residentsRestored: number;
  aboardRestored: number;
  waitingRestored: number;
  trainsRestored: number;
  /** Applied only: re-serializing IMMEDIATELY after the commit (before any update) reproduces the
   *  snapshot's residents and trains exactly — the restore's own fidelity check. */
  exact?: boolean;
  /** Applied only: the transit counters immediately after the commit (a restore adds nothing). */
  totalsAfter?: { boardings: number; alightings: number; arrivals: number; crossovers: number };
}

const none = { residentsRestored: 0, aboardRestored: 0, waitingRestored: 0, trainsRestored: 0 };

/** Validate the snapshot against THIS world completely, then (and only then) restore it. */
export function applyAmbientSnapshot(layout: FingerprintLayout, world: AmbientWorld, transit: TransitWorld | null, snapshot: unknown): AmbientRestoreResult {
  const reason = checkAmbientSave(snapshot);
  if (reason !== null) return { outcome: 'refused', reason, ...none };
  const s = snapshot as AmbientSaveState;
  if (s.world !== ambientFingerprint(layout, world)) return { outcome: 'other-world', reason: 'the snapshot belongs to a different world or resident set', ...none };
  const plan = world.planRestore(s.residents);
  if (typeof plan === 'string') return { outcome: 'refused', reason: plan, ...none };
  if (s.trains.length > 0 && !transit) return { outcome: 'refused', reason: 'the snapshot has trains but this world has no subway line', ...none };
  const trainPlan = transit ? transit.planRestore(s.trains, (id) => world.rideDestinationAt(id, plan)) : [];
  if (typeof trainPlan === 'string') return { outcome: 'refused', reason: trainPlan, ...none };
  // everything fits: restore, emitting nothing
  world.commitRestore(plan);
  if (transit) transit.commitRestore(trainPlan);
  const echo = serializeAmbient(layout, world, transit, s.savedAt);
  return {
    outcome: 'applied',
    exact: JSON.stringify([echo.residents, echo.trains]) === JSON.stringify([s.residents, s.trains]),
    totalsAfter: transit ? { ...transit.totals } : { boardings: 0, alightings: 0, arrivals: 0, crossovers: 0 },
    residentsRestored: plan.length,
    aboardRestored: plan.filter((p) => p.state === 'riding').length,
    waitingRestored: plan.filter((p) => p.state === 'waiting').length,
    trainsRestored: trainPlan.length,
  };
}
