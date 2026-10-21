/**
 * FIXTURE PREVIEW ONLY: read-only snapshots of ambient resident positions, so evidence capture can
 * track named identities over time. Copies only; nothing here can change the simulation.
 */
import type { OfficeState } from '../engine/officeState.js';
import type { TransitWorld } from '../transit/transitWorld.js';

/** One resident as the fixture-preview probe reports it (a copy, never a live reference). */
export interface AmbientProbeEntry {
  id: string;
  job: string;
  district: string;
  state: string;
  col: number;
  row: number;
  x: number;
  y: number;
  legsWalked: number;
  /** Places entered so far (home place first). */
  places: string[];
  /** The pending leg (routine index) and the current state's remaining timer, raw. */
  routineIndex: number;
  timer: number;
  /** Whether the resident is ON DUTY (drawn, simulated, hoverable); an off-duty resident is frozen and not drawn. */
  onDuty: boolean;
}

/** A train as the probe copies it, RAW (no rounding, station by index): read straight from the
 *  live train, not through the snapshot serializer, so evidence can compare it independently. */
export interface TrainProbeEntry {
  id: string;
  dir: number;
  x: number;
  target: number;
  row: number;
  state: string;
  timer: number;
  v: number;
  passengers: string[];
}

/** A real agent's position, copied for evidence (agents are never affected by residents). */
export interface AgentProbeEntry {
  id: number;
  col: number;
  row: number;
  seatId: string | null;
}

declare global {
  interface Window {
    /** FIXTURE PREVIEW ONLY: read-only resident positions for evidence capture. */
    __pixelAgentsAmbientProbe?: {
      snapshot: () => AmbientProbeEntry[];
      agents: () => AgentProbeEntry[];
      /** The decorative subway: trains, totals and the bounded event log (null without a line). */
      transit?: () => ReturnType<TransitWorld['snapshot']> | null;
      /** Character ids eligible for the working halo now (office/halo.ts). */
      halo?: () => number[];
      /** What the last ambient snapshot application did (null if none was attempted). */
      restore?: () => unknown;
      /** Raw train copies (TrainProbeEntry), independent of the snapshot serializer. */
      trainsRaw?: () => TrainProbeEntry[];
    };
  }
}

/** The FULL simulated roster, on duty or not, each with its duty flag (the drawn list is `onDuty`). */
export function snapshotAmbient(officeState: OfficeState): AmbientProbeEntry[] {
  const world = officeState.ambient;
  return (world?.residents ?? []).map((r) => ({
    id: r.id,
    job: r.job,
    district: r.district,
    state: r.state,
    col: r.tileCol,
    row: r.tileRow,
    x: r.x,
    y: r.y,
    legsWalked: r.legsWalked,
    places: [...r.placesVisited],
    routineIndex: r.routineIndex,
    timer: r.timer,
    onDuty: world?.isOnDuty(r) ?? true,
  }));
}

export function snapshotTrainsRaw(transit: TransitWorld | null): TrainProbeEntry[] {
  return (transit?.trains ?? []).map((t) => ({ id: t.id, dir: t.dir, x: t.x, target: t.target, row: t.row, state: t.state, timer: t.timer, v: t.v, passengers: [...t.passengers] }));
}

export function snapshotAgents(officeState: OfficeState): AgentProbeEntry[] {
  return [...officeState.characters.values()].map((ch) => ({
    id: ch.id,
    col: ch.tileCol,
    row: ch.tileRow,
    seatId: ch.seatId,
  }));
}
