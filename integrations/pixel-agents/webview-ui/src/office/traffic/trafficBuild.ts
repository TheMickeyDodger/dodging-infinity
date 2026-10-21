/**
 * BUILDING THE TRAFFIC OF A WORLD (living city 6–9): from a layout to a TrafficWorld, or none.
 *
 * The one subtlety is a fixed point. A placed vehicle that sits on a lane is something to ANIMATE, so it must not
 * block that lane; but every OTHER solid thing in the street (a parked prop, a vehicle that is not on a lane)
 * must. Which vehicles sit on a lane depends on which tiles block. So: start with every vehicle piece as a
 * candidate (non-blocking), derive the lanes, keep the vehicles that really are on one, block everything else,
 * and repeat until the set stops changing. The set can only shrink, so this ends; it is also capped at a few
 * passes, and a world that has not settled by then gets NO animated vehicles rather than a street in which a
 * parked car could be driven through.
 */
import type { TrafficGraph } from './trafficGraph.js';
import { buildTrafficGraph } from './trafficGraph.js';
import type { VehicleHome } from './trafficWorld.js';
import { findVehicleHomes, TrafficWorld } from './trafficWorld.js';

const VEHICLE_PIECE = /^VEHICLE_/;
/** The most fixed-point passes the plan makes before it gives up (and animates no vehicle). */
export const MAX_PASSES = 5;

export interface TrafficLayout {
  readonly cols: number;
  readonly rows: number;
  readonly tiles: readonly number[];
  readonly furniture: readonly { uid: string; type: string; col: number; row: number }[];
}

export interface TrafficBuild {
  readonly graph: TrafficGraph;
  readonly homes: readonly VehicleHome[];
  /** Fixed-point passes it took (a world that did not settle has no homes). */
  readonly passes: number;
  readonly settled: boolean;
}

type EntryOf = (
  type: string,
) => { footprintW: number; footprintH: number; backgroundTiles?: number } | undefined;
type BlockedOf = (
  furniture: { uid: string; type: string; col: number; row: number }[],
) => Set<string>;

export function planTraffic(
  layout: TrafficLayout,
  entryOf: EntryOf,
  blockedOf: BlockedOf,
): TrafficBuild {
  const { cols, rows, tiles, furniture } = layout;
  let candidates = new Set(furniture.filter((f) => VEHICLE_PIECE.test(f.type)).map((f) => f.uid));
  const graphWithout = (excluded: ReadonlySet<string>): TrafficGraph => {
    const blocked = blockedOf(furniture.filter((f) => !excluded.has(f.uid)));
    return buildTrafficGraph(cols, rows, tiles, (t) =>
      blocked.has(`${t % cols},${Math.floor(t / cols)}`),
    );
  };
  let graph = graphWithout(candidates);
  let homes = findVehicleHomes(furniture, graph, entryOf);
  for (let pass = 1; pass <= MAX_PASSES; pass++) {
    const next = new Set(homes.map((h) => h.uid));
    if (next.size === candidates.size && [...next].every((u) => candidates.has(u)))
      return { graph, homes, passes: pass, settled: true };
    candidates = next;
    graph = graphWithout(candidates);
    homes = findVehicleHomes(furniture, graph, entryOf);
  }
  // never settled: no animated vehicles, and every vehicle blocks like the furniture it is
  return { graph: graphWithout(new Set()), homes: [], passes: MAX_PASSES, settled: false };
}

export function buildTrafficWorld(
  layout: TrafficLayout,
  entryOf: EntryOf,
  blockedOf: BlockedOf,
): TrafficWorld {
  const plan = planTraffic(layout, entryOf, blockedOf);
  return new TrafficWorld(plan.graph, plan.homes);
}
