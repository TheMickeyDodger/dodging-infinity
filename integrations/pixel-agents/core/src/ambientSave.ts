/**
 * THE AMBIENT SNAPSHOT (M3 S4) — the ONE definition of what the DI office may save about its
 * decorative simulation, shared by the server (checked before anything is written) and the webview
 * (checked again, and against the world, before anything is applied).
 *
 * Version 2 carries ACTIVE JOURNEYS as durable state:
 * - per resident: its durable id, routine index (its pending leg and, for a rider, its destination
 *   through the world's own ride plan), tile, coarse state and that state's remaining timer;
 * - per train: direction, position, next station, track row, operating state (running, dwelling,
 *   crossing over, terminus dwell), remaining timer, speed, and its ONBOARD residents.
 *
 * What it never holds: routines, jobs, names, paths, caches or animation frames (derived, rebuilt or
 * transient), or anything agent-, Mission- or canonical-shaped. Residents are rebuilt
 * deterministically from the world; a snapshot only restores the state of residents that exist. A
 * snapshot is not simulation while closed: everything resumes from the saved point.
 */
import { LAYOUT_MAX_DIM } from './layoutLimits.js';

export const AMBIENT_SAVE_VERSION = 2;
/** Hard byte ceiling of a saved snapshot, measured on its COMPACT JSON serialization (escapes
 *  included). The count and length bounds alone do NOT keep every admissible shape under it: ids
 *  full of `"` or `\` serialize at two bytes per character and can exceed it, and this ceiling is
 *  what refuses them. */
export const AMBIENT_SAVE_MAX_BYTES = 65_536;
export const AMBIENT_SAVE_MAX_RESIDENTS = 200;
export const AMBIENT_SAVE_MAX_TRAINS = 4;
/** Riders aboard all trains together (the commuter bound, with headroom). */
export const AMBIENT_SAVE_MAX_ABOARD = 32;
export const AMBIENT_SAVE_ID_MAX = 128;
export const AMBIENT_SAVE_TRAIN_ID_MAX = 32;
export const AMBIENT_SAVE_FINGERPRINT_MAX = 256;
/** Routine indices are bounded by the routine stop bound (10) with headroom. */
export const AMBIENT_SAVE_ROUTINE_INDEX_MAX = 63;
/** Timers (dwell, wait, crossover) are seconds remaining. */
export const AMBIENT_SAVE_TIMER_MAX = 3600;
export const AMBIENT_SAVE_SPEED_MAX = 64;

/** Resident states: 0 standing (dwell), 1 walking (resumes by re-planning from its tile toward its
 *  pending stop), 2 waiting for a train, 3 aboard a train, 4 backing off after a failed path search. */
export type AmbientSaveStateCode = 0 | 1 | 2 | 3 | 4;
/** [id, routineIndex, col, row, state, timer] */
export type AmbientSaveResident = [string, number, number, number, AmbientSaveStateCode, number];
/** Train states: 0 running, 1 dwelling, 2 crossing over, 3 terminus dwell after crossing over. */
export type AmbientSaveTrainState = 0 | 1 | 2 | 3;
/** [id, dir (+1 east / -1 west), x (tiles), next station index, track row, state, timer, speed, onboard ids] */
export type AmbientSaveTrain = [string, 1 | -1, number, number, number, AmbientSaveTrainState, number, number, string[]];

export interface AmbientSaveState {
  v: typeof AMBIENT_SAVE_VERSION;
  /** Fingerprint of the world (tiles, furniture) and the resident set the snapshot belongs to. */
  world: string;
  /** Wall-clock milliseconds when saved (informational; never used to advance anything). */
  savedAt: number;
  residents: AmbientSaveResident[];
  trains: AmbientSaveTrain[];
}

const KEYS = ['v', 'world', 'savedAt', 'residents', 'trains'];
const isInt = (v: unknown, lo: number, hi: number): v is number =>
  typeof v === 'number' && Number.isInteger(v) && v >= lo && v <= hi;
const isNum = (v: unknown, lo: number, hi: number): v is number =>
  typeof v === 'number' && Number.isFinite(v) && v >= lo && v <= hi;
const isBoundedString = (v: unknown, max: number): v is string =>
  typeof v === 'string' && v.length > 0 && v.length <= max && /^[\x20-\x7e]+$/.test(v);

/**
 * The byte length of the ACTUAL compact JSON serialization: it measures JSON.stringify's output, so
 * every escape is counted (`"` and `\` serialize as two characters each). Character count equals
 * UTF-8 byte count because the OUTPUT is pure ASCII when every string is printable ASCII (checked
 * before this is called): the escapes are ASCII too. Called on an unchecked value holding non-ASCII
 * text it would under-count bytes, so it is only meaningful after the string checks. Runs in Node
 * and in the browser alike (no Buffer).
 */
export function ambientSaveBytes(state: unknown): number {
  try {
    return JSON.stringify(state).length;
  } catch {
    return Infinity;
  }
}

/**
 * null when the value is an admissible snapshot, else the reason it is refused. Checks the shape,
 * every bound, and the onboard linkage: every resident in state 3 (aboard) is listed aboard exactly
 * one train, and every listed passenger is a resident in state 3.
 */
export function checkAmbientSave(state: unknown): string | null {
  if (typeof state !== 'object' || state === null || Array.isArray(state)) return 'snapshot is not an object';
  const o = state as Record<string, unknown>;
  const keys = Object.keys(o);
  if (keys.length !== KEYS.length || !KEYS.every((k) => keys.includes(k))) return 'snapshot keys differ from the contract';
  if (o.v !== AMBIENT_SAVE_VERSION) return 'snapshot version unsupported';
  if (!isBoundedString(o.world, AMBIENT_SAVE_FINGERPRINT_MAX)) return 'snapshot world fingerprint malformed';
  if (!isNum(o.savedAt, 0, Number.MAX_SAFE_INTEGER)) return 'snapshot savedAt malformed';
  if (!Array.isArray(o.residents) || o.residents.length > AMBIENT_SAVE_MAX_RESIDENTS) return 'snapshot residents malformed or over the bound';
  const states = new Map<string, number>();
  for (const r of o.residents) {
    if (!Array.isArray(r) || r.length !== 6) return 'snapshot resident entry malformed';
    const [id, ri, col, row, st, timer] = r as unknown[];
    if (!isBoundedString(id, AMBIENT_SAVE_ID_MAX)) return 'snapshot resident id malformed';
    if (states.has(id)) return 'snapshot resident id duplicated';
    if (!isInt(ri, 0, AMBIENT_SAVE_ROUTINE_INDEX_MAX)) return 'snapshot routine index out of bounds';
    if (!isInt(col, 0, LAYOUT_MAX_DIM - 1) || !isInt(row, 0, LAYOUT_MAX_DIM - 1)) return 'snapshot resident tile out of bounds';
    if (!isInt(st, 0, 4)) return 'snapshot resident state malformed';
    if (!isNum(timer, 0, AMBIENT_SAVE_TIMER_MAX)) return 'snapshot resident timer out of bounds';
    states.set(id, st);
  }
  if (!Array.isArray(o.trains) || o.trains.length > AMBIENT_SAVE_MAX_TRAINS) return 'snapshot trains malformed or over the bound';
  const trainIds = new Set<string>();
  const aboard = new Set<string>();
  for (const t of o.trains) {
    if (!Array.isArray(t) || t.length !== 9) return 'snapshot train entry malformed';
    const [id, dir, x, target, row, st, timer, speed, passengers] = t as unknown[];
    if (!isBoundedString(id, AMBIENT_SAVE_TRAIN_ID_MAX) || trainIds.has(id)) return 'snapshot train id malformed or duplicated';
    trainIds.add(id);
    if (dir !== 1 && dir !== -1) return 'snapshot train direction malformed';
    if (!isNum(x, 0, LAYOUT_MAX_DIM)) return 'snapshot train position out of bounds';
    if (!isInt(target, 0, 63) || !isInt(row, 0, LAYOUT_MAX_DIM - 1)) return 'snapshot train target or row out of bounds';
    if (!isInt(st, 0, 3)) return 'snapshot train state malformed';
    if (!isNum(timer, 0, AMBIENT_SAVE_TIMER_MAX) || !isNum(speed, 0, AMBIENT_SAVE_SPEED_MAX)) return 'snapshot train timer or speed out of bounds';
    if (!Array.isArray(passengers)) return 'snapshot train passengers malformed';
    for (const p of passengers) {
      if (!isBoundedString(p, AMBIENT_SAVE_ID_MAX) || aboard.has(p)) return 'snapshot passenger malformed or aboard twice';
      if (states.get(p) !== 3) return 'snapshot passenger is not a resident aboard';
      aboard.add(p);
    }
  }
  if (aboard.size > AMBIENT_SAVE_MAX_ABOARD) return 'snapshot has too many residents aboard';
  for (const [id, st] of states) if (st === 3 && !aboard.has(id)) return 'snapshot resident aboard no train';
  if (ambientSaveBytes(state) > AMBIENT_SAVE_MAX_BYTES) return `snapshot over ${AMBIENT_SAVE_MAX_BYTES} bytes`;
  return null;
}
