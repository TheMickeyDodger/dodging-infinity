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
import type { AmbientResident, AmbientWorld } from './ambientWorld.js';

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

/**
 * What a PREDECESSOR migration did (`outcome: 'migrated'`): a snapshot saved by the previous build, applied to the residents of this build's
 * plan for the same layout. Residents whose id AND plan are unchanged were restored exactly; the rest start fresh at their posts. Fixed vocabulary.
 */
export interface AmbientMigrationReport {
  /** Residents restored EXACTLY (id, job, district, routine and ride plan all unchanged). */
  restoredExact: number;
  /** Residents of this world that start fresh at their posts (= resident count − restoredExact). */
  resetFresh: number;
  /** Saved residents this world no longer has (their saved state is dropped). */
  dropped: number;
  trainsRestored: number;
  /** Why the fresh residents start fresh; sums to `resetFresh`. */
  reasons: { changedRoutine: number; newResident: number; invalidTile: number };
}

/** Why a migration refuses (nothing applied, the saved file untouched). Fixed vocabulary. */
export const MIGRATION_REFUSED_IN_TRANSIT = 'migration_unsupported:in_transit_resident_changed';
export const MIGRATION_REFUSED_TRAINS = 'migration_unsupported:trains_not_restorable';

/** The previous build's residents (and subway) for the same layout, built on demand by the engine. */
export interface AmbientPredecessor {
  world: AmbientWorld;
  transit: TransitWorld | null;
}

export interface AmbientRestoreResult {
  /**
   * `applied`: the snapshot is this world's own (equal fingerprint), restored exactly. `migrated`: a snapshot of the PREVIOUS build's plan for this
   * layout, restored where the plan is unchanged and started fresh where it changed — never an exact round trip (see `migration`). `refused`: the
   * whole snapshot is refused with a reason, nothing changed. `other-world`: it belongs to another layout or resident set.
   */
  outcome: 'applied' | 'migrated' | 'refused' | 'other-world';
  reason?: string;
  migration?: AmbientMigrationReport;
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

/** The layout part of a fingerprint (`cols x rows | revision | tiles | furniture`): what must be equal before a predecessor is even built. */
const layoutPart = (fingerprint: string): string => fingerprint.split('|').slice(0, 4).join('|');

const sameTiles = (a: ArrayLike<number> | undefined, b: ArrayLike<number> | undefined): boolean => {
  if (!a || !b) return !a && !b;
  if (a.length !== b.length) return false;
  for (let i = 0; i < a.length; i++) if (a[i] !== b[i]) return false;
  return true;
};
/** One resident's PLAN is unchanged between two builds: same job and district, same routine, same ride plan (its id is how they are paired). */
const samePlan = (a: AmbientResident, b: AmbientResident): boolean =>
  a.job === b.job &&
  a.district === b.district &&
  a.palette === b.palette &&
  a.hueShift === b.hueShift &&
  sameTiles(a.routine, b.routine) &&
  sameTiles(a.rides, b.rides);

/**
 * Validate the snapshot against THIS world completely, then (and only then) restore it.
 *
 * `predecessor` (optional, lazily built): when the snapshot is NOT this world's but its layout part matches and its fingerprint EQUALS the
 * predecessor's — recomputed from the retained previous derivation, never a loose match — the snapshot is migrated (see `migrate`). Anything
 * else is `other-world`, exactly as without it.
 */
export function applyAmbientSnapshot(
  layout: FingerprintLayout,
  world: AmbientWorld,
  transit: TransitWorld | null,
  snapshot: unknown,
  predecessor?: () => AmbientPredecessor | null,
): AmbientRestoreResult {
  const reason = checkAmbientSave(snapshot);
  if (reason !== null) return { outcome: 'refused', reason, ...none };
  const s = snapshot as AmbientSaveState;
  const fingerprint = ambientFingerprint(layout, world);
  if (s.world !== fingerprint) {
    if (predecessor && layoutPart(s.world) === layoutPart(fingerprint)) {
      const previous = predecessor();
      if (previous && s.world === ambientFingerprint(layout, previous.world))
        return migrate(world, transit, s, previous);
    }
    return { outcome: 'other-world', reason: 'the snapshot belongs to a different world or resident set', ...none };
  }
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

/**
 * PREDECESSOR MIGRATION (exact-predecessor only; the strict validators above are not touched). `s` was saved by the previous build for THIS layout: its
 * fingerprint equals the previous plan's. It is validated whole against that previous world first (so a snapshot that was never valid is refused with
 * the same reasons as today), then applied to this build's residents:
 * - a resident whose id AND plan (job, district, routine, rides) are unchanged is restored exactly (the same per-entry validation; a saved tile that no
 *   longer fits this world starts it fresh instead);
 * - every other resident starts fresh at its post; saved residents this world no longer has are dropped; a changed resident's saved position or routine
 *   index is NEVER applied;
 * - TRANSIT IS ALL OR NOTHING: if no saved waiting/riding resident is changed or reset, the trains and passengers are restored exactly through the
 *   unchanged transit validator (a restore adds no boarding, arrival or counter); if ANY waiting/riding resident is changed or reset, the whole migration is
 *   REFUSED with a fixed reason and nothing is applied. Transit is never partially reset.
 */
function migrate(
  world: AmbientWorld,
  transit: TransitWorld | null,
  s: AmbientSaveState,
  previous: AmbientPredecessor,
): AmbientRestoreResult {
  // 1. the snapshot must be a valid snapshot of the PREVIOUS plan (residents, tiles, rides, trains), checked as strictly as ever
  const previousPlan = previous.world.planRestore(s.residents);
  if (typeof previousPlan === 'string') return { outcome: 'refused', reason: previousPlan, ...none };
  if (s.trains.length > 0 && !previous.transit)
    return { outcome: 'refused', reason: 'the snapshot has trains but this world has no subway line', ...none };
  const previousTrains = previous.transit
    ? previous.transit.planRestore(s.trains, (id) => previous.world.rideDestinationAt(id, previousPlan))
    : [];
  if (typeof previousTrains === 'string') return { outcome: 'refused', reason: previousTrains, ...none };

  // 2. which saved residents have an unchanged counterpart here
  const unchanged = new Set<string>();
  let changedRoutine = 0;
  for (const old of previous.world.residents) {
    const now = world.residents.find((r) => r.id === old.id);
    if (now && samePlan(old, now)) unchanged.add(old.id);
    else if (now) changedRoutine++;
  }
  const partial = world.planPartialRestore(s.residents, unchanged);
  const restoredIds = new Set(partial.plan.map((p) => p.r.id));

  // 3. in transit: restore exactly or refuse the migration whole
  const inTransit = s.residents.filter((r) => r[4] === 2 || r[4] === 3).map((r) => r[0]);
  const passengers = s.trains.flatMap((t) => [...t[8]]);
  if ([...inTransit, ...passengers].some((id) => !restoredIds.has(id)))
    return { outcome: 'refused', reason: MIGRATION_REFUSED_IN_TRANSIT, ...none };
  const trainPlan = transit ? transit.planRestore(s.trains, (id) => world.rideDestinationAt(id, partial.plan)) : [];
  if (typeof trainPlan === 'string' || (s.trains.length > 0 && !transit))
    return { outcome: 'refused', reason: MIGRATION_REFUSED_TRAINS, ...none };

  // 4. apply; the residents with no plan step stay as built (fresh at their posts)
  world.commitRestore(partial.plan);
  if (transit) transit.commitRestore(trainPlan);
  const savedIds = new Set(s.residents.map((r) => r[0]));
  const newResident = world.residents.filter((r) => !savedIds.has(r.id)).length;
  const invalidTile = partial.invalid.size;
  const resetFresh = world.residents.length - partial.plan.length;
  return {
    outcome: 'migrated',
    reason: `migrated from the previous build's plan: ${partial.plan.length} residents restored exactly, ${resetFresh} start fresh`,
    migration: {
      restoredExact: partial.plan.length,
      resetFresh,
      dropped: s.residents.filter((r) => !world.residents.some((x) => x.id === r[0])).length,
      trainsRestored: trainPlan.length,
      reasons: { changedRoutine, newResident, invalidTile },
    },
    totalsAfter: transit ? { ...transit.totals } : { boardings: 0, alightings: 0, arrivals: 0, crossovers: 0 },
    residentsRestored: partial.plan.length,
    aboardRestored: partial.plan.filter((p) => p.state === 'riding').length,
    waitingRestored: partial.plan.filter((p) => p.state === 'waiting').length,
    trainsRestored: trainPlan.length,
  };
}
