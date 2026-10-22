/**
 * The seat each DI character should be added with (DOM-free, unit-tested): the seam between the DI office state, the identity-keyed seat
 * preferences and the three-office allocation (office/layout/diAllocation.ts).
 *
 * With offices detected: the allocation decides (pass 1 reserves every held, preferred and server-vouched seat; pass 2 places the fresh ones by the quota rule).
 * Without (legacy layouts): a character's own identity-keyed preference, else the seat the server vouches for in this process, else nothing — exactly the
 * previous behaviour, except that the preference is keyed by identity instead of by the process-local number.
 */
import type { AllocResult } from '../office/layout/diAllocation.js';
import { allocateDiSeats } from '../office/layout/diAllocation.js';
import type { DiOfficeDetection } from '../office/layout/diOffices.js';
import type { DiPlacementModel } from './diPlacementModel.js';

export interface DiSeatPlanHost {
  officeDetection: DiOfficeDetection | null;
  seats: ReadonlyMap<string, { seatCol: number; seatRow: number }>;
  characters: ReadonlyMap<number, { seatId: string | null }>;
}

export interface DiSeatPlan {
  /** The seat to add (or keep) each present character with; absent = none planned (the generic seating decides). */
  seatFor: ReadonlyMap<number, string>;
  /** How each planned character's seat was decided (offices detected only). */
  placement: ReadonlyMap<number, 'preserved' | 'fresh' | 'fallback'>;
  /** The allocation itself, or null without detected offices. */
  allocation: AllocResult | null;
}

export function planDiSeats(
  host: DiSeatPlanHost,
  model: DiPlacementModel,
  prefs: ReadonlyMap<string, string>,
  /** the seat the server vouches for, per character number (offered only for this process's saves) */
  vouchedSeat: (id: number) => string | undefined,
): DiSeatPlan {
  const claimsOf = (id: number): string[] => {
    const key = model.keys.get(id);
    const out: string[] = [];
    const pref = key === undefined ? undefined : prefs.get(key);
    if (pref !== undefined) out.push(pref);
    const vouched = vouchedSeat(id);
    if (vouched !== undefined) out.push(vouched);
    return out;
  };
  const detection = host.officeDetection;
  if (!detection) {
    const seatFor = new Map<number, string>();
    for (const c of model.entitlement) {
      const claim = claimsOf(c.id)[0];
      if (claim !== undefined) seatFor.set(c.id, claim);
    }
    return { seatFor, placement: new Map(), allocation: null };
  }
  const seatPos = new Map<string, { col: number; row: number }>();
  for (const office of detection.offices)
    for (const uid of office.seatIds) {
      const seat = host.seats.get(uid);
      if (seat) seatPos.set(uid, { col: seat.seatCol, row: seat.seatRow });
    }
  const held = new Map<number, string>();
  for (const c of model.entitlement) {
    const seat = host.characters.get(c.id)?.seatId;
    if (seat) held.set(c.id, seat);
  }
  const preferred = new Map<number, readonly string[]>();
  for (const c of model.entitlement) preferred.set(c.id, claimsOf(c.id));
  const allocation = allocateDiSeats({
    offices: detection.offices.map((o) => ({
      workstationIds: o.workstationIds,
      tableIds: o.tableIds,
      chairIds: o.chairIds,
    })),
    seatPos,
    entitlement: model.entitlement,
    placementOrder: model.placementOrder,
    held,
    preferred,
  });
  const placement = new Map<number, 'preserved' | 'fresh' | 'fallback'>();
  for (const id of allocation.seatFor.keys())
    placement.set(id, allocation.fallback.has(id) ? 'fallback' : allocation.fresh.has(id) ? 'fresh' : 'preserved');
  return { seatFor: allocation.seatFor, placement, allocation };
}
