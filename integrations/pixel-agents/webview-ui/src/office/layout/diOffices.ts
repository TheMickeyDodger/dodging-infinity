/**
 * THE THREE GRAND OFFICES (DOM-free, unit-tested): which chairs of a large world are WORKSTATIONS in a working office.
 *
 * A WORKSTATION is a reachable office seat that faces a desk AND faces electronics by the engine's own auto-on rule
 * (`seatFacesElectronics`: the scan that lights a PC when an agent sits), so a seat at a bare table or a coffee table is NOT one.
 * Every eligible seat falls in exactly one of three TIERS, in the order a FRESH character is placed on them:
 *   1  workstation          — desk-facing, reachable, faces electronics
 *   2  table, not workstation — desk-facing, reachable, no electronics (a library or meeting table, a coffee table)
 *   3  chair, not workstation — any other eligible seat (the home's lounge chairs)
 * The ELIGIBLE seat set (`seatIds`) is the union of the three and is the same set it always was: no seat becomes invalid, so a saved or
 * preferred seat of any tier is still honoured. Only the tier decides where a fresh character goes.
 *
 * Applies only to a layout with an EXPLICIT `home` (the living metropolis and the worlds built like it). Every other layout
 * (legacy-size, content-derived home, custom, imported, the bundled default) gets `null`, and the engine keeps `homeSeats`
 * exactly as before. Offices come in a fixed OFFICE ORDER that allocation relies on:
 *
 *   0  the home office (the Palace): exactly today's `homeSeats` (seats inside the home and its deck) minus the receptionist's
 *      reserved workstation, so no saved Palace seat id changes meaning. Its desk-facing, reachable chairs that face electronics are its
 *      WORKSTATIONS and the other desk-facing ones its TABLE seats; its remaining chairs (lounge chairs) stay eligible seats — every
 *      non-workstation seat can be HELD (a saved or preferred seat is never moved) and is the honest last resort when no workstation is
 *      free anywhere (tables first, then chairs) — but is never what a fresh agent is placed on while a workstation is free;
 *   1… the layout's own office DISTRICTS, in `layout.areas` order: a district whose label matches an office pattern, that holds
 *      at least DI_OFFICE_MIN_WORKSTATIONS DESK-FACING seats (a chair adjacent to and facing a desk) and that is walkably reachable from
 *      the home. A satellite office's seats ARE its desk-facing, reachable chairs (its workstations and its table seats), so café,
 *      cathedral, retail, park and lounge chairs outside the home are never seats of an office.
 *
 * If no office district is found (home only) the result is null: that layout keeps `homeSeats` exactly as before.
 * Computed ONCE per layout build (one typed-array breadth-first search for reachability); the result is read-only evidence
 * (`seatIds` / `workstationIds` per office) as well as the engine's seat filter.
 */
import {
  DI_HOME_OFFICE_LABEL,
  DI_OFFICE_DISTRICT_PATTERNS,
  DI_OFFICE_MAX_OFFICES,
  DI_OFFICE_MIN_WORKSTATIONS,
} from '../../constants.js';
import type { Direction, OfficeLayout } from '../types.js';
import { seatFacesElectronics } from './seatElectronics.js';
import {
  districtsOf,
  type FootprintOf,
  inRegions,
  isLargeWorld,
  livingRegions,
  type TileRect,
} from './worldViews.js';

export interface DiOffice {
  /** Position in OFFICE ORDER (0 = home). */
  index: number;
  /** The place label: the home is 'Palace', a district its own area label. */
  label: string;
  kind: 'home' | 'district';
  /** The office's tile box (home rect; district bounding box): what wander/spawn confinement uses. */
  box: TileRect;
  /** Every seat a character may HOLD in this office (workstations and, in the home, other chairs), ascending by (row, col, uid). */
  seatIds: readonly string[];
  /** TIER 1 — the desk-facing, reachable seats that face electronics: the only seats a FRESH character is placed on while any is free. Subset of `seatIds`. */
  workstationIds: readonly string[];
  /** TIER 2 — desk-facing, reachable seats with NO electronics (a bare or coffee table): 'table, not workstation'. Held if saved or preferred; a fresh character only when no workstation is free anywhere. */
  tableIds: readonly string[];
  /** TIER 3 — `seatIds` that are neither (the home's lounge chairs); empty for a district office. 'chair, not workstation'. */
  chairIds: readonly string[];
}

export interface DiOfficeDetection {
  offices: readonly DiOffice[];
  /** The receptionist's reserved seats, withheld from every office. */
  reservedSeatIds: readonly string[];
  /** seat id → office index, for every workstation of every office. */
  officeIndexBySeat: ReadonlyMap<string, number>;
}

interface SeatLite {
  seatCol: number;
  seatRow: number;
  facingDir: Direction;
}

const STEPS: ReadonlyArray<readonly [number, number]> = [
  [1, 0],
  [-1, 0],
  [0, 1],
  [0, -1],
];

/** The tile one step in front of a seat (the desk it works at). */
function facing(seat: SeatLite): [number, number] {
  // Direction values: DOWN 0, LEFT 1, RIGHT 2, UP 3 (types.ts)
  switch (seat.facingDir) {
    case 3:
      return [seat.seatCol, seat.seatRow - 1];
    case 1:
      return [seat.seatCol - 1, seat.seatRow];
    case 2:
      return [seat.seatCol + 1, seat.seatRow];
    default:
      return [seat.seatCol, seat.seatRow + 1];
  }
}

const byPosition = (
  seats: ReadonlyMap<string, SeatLite>,
): ((a: string, b: string) => number) => (a, b) => {
  const sa = seats.get(a)!;
  const sb = seats.get(b)!;
  return sa.seatRow - sb.seatRow || sa.seatCol - sb.seatCol || (a < b ? -1 : a > b ? 1 : 0);
};

/**
 * Select the offices of `layout`, or null when it has none to detect (see the module comment).
 * `seats` is EVERY chair seat of the layout; `deskTiles` the `col,row` keys of desk furniture; `isWalkable` the walkable test
 * (tiles and blocked tiles); `reservedSeatIds` the receptionist's workstation.
 */
export function detectOffices(args: {
  layout: Pick<OfficeLayout, 'cols' | 'rows' | 'tiles' | 'furniture' | 'home' | 'areas' | 'areaTiles'>;
  seats: ReadonlyMap<string, SeatLite>;
  footprintOf: FootprintOf;
  deskTiles: ReadonlySet<string>;
  /** the `col,row` keys of electronics furniture (`electronicsTileSet`): what makes a desk-facing seat a workstation */
  electronicsTiles: ReadonlySet<string>;
  isWalkable: (col: number, row: number) => boolean;
  reservedSeatIds: ReadonlySet<string>;
}): DiOfficeDetection | null {
  const { layout, seats, footprintOf, deskTiles, electronicsTiles, isWalkable, reservedSeatIds } = args;
  if (!layout.home || !isLargeWorld(layout.cols, layout.rows)) return null;
  const regions = livingRegions(layout, footprintOf);
  if (!regions) return null;
  const homeBox = regions[0];
  const order = byPosition(seats);

  // 0 — the home: exactly the seats today's homeSeats keeps (inside home + deck), minus the reserved workstation.
  const homeIds = [...seats]
    .filter(([uid, s]) => !reservedSeatIds.has(uid) && inRegions(s.seatCol, s.seatRow, regions))
    .map(([uid]) => uid)
    .sort(order);
  if (homeIds.length === 0) return null;

  // reachability from the home: one breadth-first search over the walkable grid from the home seats' walkable neighbours
  const { cols, rows } = layout;
  const reached = new Uint8Array(cols * rows);
  const queue: number[] = [];
  const visit = (c: number, r: number): void => {
    if (c < 0 || r < 0 || c >= cols || r >= rows) return;
    const k = r * cols + c;
    if (reached[k] === 1 || !isWalkable(c, r)) return;
    reached[k] = 1;
    queue.push(k);
  };
  for (const uid of homeIds) {
    const s = seats.get(uid)!;
    for (const [dc, dr] of STEPS) visit(s.seatCol + dc, s.seatRow + dr);
  }
  for (let i = 0; i < queue.length; i++) {
    const c = queue[i] % cols;
    const r = (queue[i] - c) / cols;
    for (const [dc, dr] of STEPS) visit(c + dc, r + dr);
  }
  const reachable = (s: SeatLite): boolean =>
    STEPS.some(([dc, dr]) => {
      const c = s.seatCol + dc;
      const r = s.seatRow + dr;
      return c >= 0 && r >= 0 && c < cols && r < rows && reached[r * cols + c] === 1;
    });
  const deskFacing = (s: SeatLite): boolean => {
    const [fc, fr] = facing(s);
    return deskTiles.has(`${fc},${fr}`);
  };
  const isDeskSeat = (uid: string): boolean => {
    const s = seats.get(uid)!;
    return deskFacing(s) && reachable(s);
  };
  const isWorkstation = (uid: string): boolean => isDeskSeat(uid) && seatFacesElectronics(seats.get(uid)!, electronicsTiles);
  const homeWs = homeIds.filter(isWorkstation);
  const homeWsSet = new Set(homeWs);
  const homeTables = homeIds.filter((u) => isDeskSeat(u) && !homeWsSet.has(u));
  const homeTableSet = new Set(homeTables);
  const offices: DiOffice[] = [
    {
      index: 0,
      label: DI_HOME_OFFICE_LABEL,
      kind: 'home',
      box: homeBox,
      seatIds: homeIds,
      workstationIds: homeWs,
      tableIds: homeTables,
      chairIds: homeIds.filter((u) => !homeWsSet.has(u) && !homeTableSet.has(u)),
    },
  ];

  // 1… — office districts, in the layout's own areas order
  const claimed = new Set(homeIds);
  for (const district of districtsOf(layout)) {
    if (offices.length >= DI_OFFICE_MAX_OFFICES) break;
    if (!DI_OFFICE_DISTRICT_PATTERNS.some((p) => p.match.test(district.label))) continue;
    const ids = [...seats]
      .filter(([uid, s]) => {
        if (claimed.has(uid) || reservedSeatIds.has(uid)) return false;
        if (inRegions(s.seatCol, s.seatRow, regions)) return false; // the home's own place wins
        if (layout.areaTiles?.[s.seatRow * cols + s.seatCol] !== district.label) return false;
        return deskFacing(s) && reachable(s);
      })
      .map(([uid]) => uid)
      .sort(order);
    // `ids` are the district's desk-facing, reachable seats (workstations and table seats alike): the threshold is not a count of tier-1 workstations
    if (ids.length < DI_OFFICE_MIN_WORKSTATIONS) continue;
    for (const uid of ids) claimed.add(uid);
    offices.push({
      index: offices.length,
      label: district.label,
      kind: 'district',
      box: district.box,
      seatIds: ids,
      workstationIds: ids.filter(isWorkstation),
      tableIds: ids.filter((u) => !isWorkstation(u)),
      chairIds: [],
    });
  }
  // home only: nothing to spread across — this layout keeps its legacy seating exactly
  if (offices.length < 2) return null;

  const officeIndexBySeat = new Map<string, number>();
  for (const o of offices) for (const uid of o.seatIds) officeIndexBySeat.set(uid, o.index);
  return { offices, reservedSeatIds: [...reservedSeatIds].sort(), officeIndexBySeat };
}
