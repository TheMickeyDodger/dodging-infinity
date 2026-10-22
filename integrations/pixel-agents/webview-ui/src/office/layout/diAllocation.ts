/**
 * THE THREE-OFFICE ALLOCATION (DOM-free, unit-tested): which seat each real DI character gets when several offices are open.
 * The rule is written out, with worked examples, in `.herd/state/exec-p2bc.md` §C3; this file implements exactly that and nothing else.
 *
 *   Pass 1 — RESERVE what is already decided, for ALL entitled characters before anyone is placed: every seat a character currently holds, then (in
 *            entitlement order) its identity-preferred seat, then its server-vouched same-process seat — each only if still valid and unclaimed. A held or
 *            preferred seat (a lounge chair included) is KEPT and counted, never moved to a desk (no churn).
 *   Quotas — over WORKSTATION (tier 1) capacity: N_ws = min(#entitled, Σ workstations); floor + remainder in OFFICE ORDER; capped capacity is redistributed.
 *   Pass 2 — place the FRESH characters in placement order against the reserved occupancy: the team's current office if it is under quota and has a free
 *            workstation, else the office with the largest remaining quota (ties by office order); the free workstation nearest the team's lead or first placed
 *            member there, else nearest the office anchor. Only when NO workstation is free in ANY office does a fresh character take a TABLE seat (tier 2:
 *            desk-facing, no electronics), and only when no table seat is free either an other chair (tier 3) — each labelled as what it is, the team's own
 *            office first, else the first office in order, nearest the team's anchor.
 *
 * Deterministic for the same inputs; nobody is invented or duplicated; one character per seat; a reserved seat can never be used (it is not in `offices`).
 */

export interface AllocOffice {
  /** TIER 1 — workstations (desk-facing, reachable, facing electronics): what fresh characters are placed on. */
  workstationIds: readonly string[];
  /** TIER 2 — table seats (desk-facing, no electronics): held if saved or preferred; the first fallback for a fresh character when no workstation is free anywhere. */
  tableIds: readonly string[];
  /** TIER 3 — other eligible chairs: held if saved or preferred; the last resort for a fresh character when no workstation or table seat is free anywhere. */
  chairIds: readonly string[];
}

export interface AllocCharacter {
  id: number;
  /** The canonical identity key (a stable string); used only to break ties deterministically. */
  key: string;
  /** The team: a Mission's members share one; a runtime agent is its own team. */
  teamKey: string;
  isLead: boolean;
}

export interface AllocInput {
  /** The offices in OFFICE ORDER. */
  offices: readonly AllocOffice[];
  /** The tile of every seat of every office. */
  seatPos: ReadonlyMap<string, { col: number; row: number }>;
  /** Present characters in ENTITLEMENT order (`diSeatingOrder`). */
  entitlement: readonly AllocCharacter[];
  /** Present characters in PLACEMENT order: teams in Mission order, lead first then stable key; then each runtime agent alone. */
  placementOrder: readonly number[];
  /** The seat each character holds now. */
  held: ReadonlyMap<number, string>;
  /** Per character, its claims in priority order (identity-keyed preference, then the server-vouched same-process seat). */
  preferred: ReadonlyMap<number, readonly string[]>;
}

export type SeatKind = 'workstation' | 'table' | 'chair';

export interface AllocResult {
  /** The seat of every character that holds, was preferred or was placed on one. */
  seatFor: ReadonlyMap<number, string>;
  kindOf: ReadonlyMap<number, SeatKind>;
  officeOf: ReadonlyMap<number, number>;
  /** Characters placed in pass 2 (not held, not preferred). */
  fresh: ReadonlySet<number>;
  /** Characters on a table seat or an other chair although they were placed fresh (the honest fallback): 'table, not workstation' or 'chair, not workstation'. */
  fallback: ReadonlySet<number>;
  /** Entitled characters that got no seat at all. */
  deskless: readonly number[];
  quotas: readonly number[];
  assigned: readonly number[];
}

/**
 * floor(N/E) per office, the remainder to the first offices in order, over-capacity excess handed forward one unit at a time (wrapping).
 *
 * Invariant: Σq = n ≤ Σcapacity throughout. While some excess is still in hand, Σ of the current quotas plus that excess equals n, so some office has
 * q < capacity (else Σq ≥ Σcapacity ≥ n and nothing would be in hand); hence every full cycle of `e` steps places at least one unit, and the loop below
 * always finishes within the bound fixed from the INITIAL excess. If it does not, that is a programming error and throws — never a silent short quota.
 */
export function officeQuotas(total: number, capacity: readonly number[]): number[] {
  const e = capacity.length;
  if (e === 0) return [];
  const sum = capacity.reduce((a, b) => a + b, 0);
  const n = Math.min(Math.max(0, total), sum);
  const base = Math.floor(n / e);
  const rem = n % e;
  const q = capacity.map((_, i) => base + (i < rem ? 1 : 0));
  for (let i = 0; i < e; i++) {
    if (q[i] <= capacity[i]) continue;
    let excess = q[i] - capacity[i];
    const limit = e * (excess + 1); // fixed BEFORE the loop: it must not shrink as the excess does
    q[i] = capacity[i];
    let cursor = (i + 1) % e;
    for (let step = 0; excess > 0 && step < limit; step++) {
      if (q[cursor] < capacity[cursor]) {
        q[cursor]++;
        excess--;
      }
      cursor = (cursor + 1) % e;
    }
    if (excess !== 0)
      throw new Error(`officeQuotas: ${excess} unit(s) of excess could not be placed (n=${n}, capacity=${capacity.join(',')})`);
  }
  if (q.reduce((a, b) => a + b, 0) !== n || q.some((v, i) => v > capacity[i]))
    throw new Error(`officeQuotas: quotas ${q.join(',')} break the invariant for n=${n}, capacity=${capacity.join(',')}`);
  return q;
}

const byPosition = (
  seatPos: ReadonlyMap<string, { col: number; row: number }>,
  col: number,
  row: number,
) => (a: string, b: string): number => {
  const pa = seatPos.get(a)!;
  const pb = seatPos.get(b)!;
  const da = Math.abs(pa.col - col) + Math.abs(pa.row - row);
  const db = Math.abs(pb.col - col) + Math.abs(pb.row - row);
  return da - db || pa.row - pb.row || pa.col - pb.col || (a < b ? -1 : a > b ? 1 : 0);
};

/** The office anchor: the seat nearest the centroid of the given seats (ties by row, col, id); null when there are none. */
function anchorOf(
  ids: readonly string[],
  seatPos: ReadonlyMap<string, { col: number; row: number }>,
): { col: number; row: number } | null {
  if (ids.length === 0) return null;
  let sc = 0;
  let sr = 0;
  for (const id of ids) {
    const p = seatPos.get(id)!;
    sc += p.col;
    sr += p.row;
  }
  const cc = sc / ids.length;
  const cr = sr / ids.length;
  const nearest = [...ids].sort((a, b) => {
    const pa = seatPos.get(a)!;
    const pb = seatPos.get(b)!;
    const da = Math.abs(pa.col - cc) + Math.abs(pa.row - cr);
    const db = Math.abs(pb.col - cc) + Math.abs(pb.row - cr);
    return da - db || pa.row - pb.row || pa.col - pb.col || (a < b ? -1 : a > b ? 1 : 0);
  })[0];
  return seatPos.get(nearest)!;
}

export function allocateDiSeats(input: AllocInput): AllocResult {
  const { offices, seatPos, entitlement, placementOrder, held, preferred } = input;
  const officeOfSeat = new Map<string, number>();
  const kindOfSeat = new Map<string, SeatKind>();
  offices.forEach((o, i) => {
    for (const id of o.workstationIds) {
      officeOfSeat.set(id, i);
      kindOfSeat.set(id, 'workstation');
    }
    for (const id of o.tableIds) {
      officeOfSeat.set(id, i);
      kindOfSeat.set(id, 'table');
    }
    for (const id of o.chairIds) {
      officeOfSeat.set(id, i);
      kindOfSeat.set(id, 'chair');
    }
  });
  const totalSeats = officeOfSeat.size;
  const entitled = entitlement.slice(0, Math.min(entitlement.length, totalSeats));
  const entitledIds = new Set(entitled.map((c) => c.id));
  const byId = new Map(entitlement.map((c) => [c.id, c] as const));

  const seatFor = new Map<number, string>();
  const claimedBy = new Map<string, number>();
  const kindOf = new Map<number, SeatKind>();
  const officeOf = new Map<number, number>();
  const assigned = offices.map(() => 0);
  const reserve = (id: number, seat: string): void => {
    seatFor.set(id, seat);
    claimedBy.set(seat, id);
    const office = officeOfSeat.get(seat)!;
    officeOf.set(id, office);
    kindOf.set(id, kindOfSeat.get(seat)!);
    assigned[office]++;
  };

  // PASS 1 — reserve every decided seat before anyone is placed
  for (const c of entitled) {
    const seat = held.get(c.id);
    if (seat !== undefined && officeOfSeat.has(seat) && !claimedBy.has(seat)) reserve(c.id, seat);
  }
  for (const c of entitled) {
    if (seatFor.has(c.id)) continue;
    for (const claim of preferred.get(c.id) ?? []) {
      if (officeOfSeat.has(claim) && !claimedBy.has(claim)) {
        reserve(c.id, claim);
        break;
      }
    }
  }
  const reservedIds = new Set(seatFor.keys());

  // quotas over WORKSTATION capacity
  const capacity = offices.map((o) => o.workstationIds.length);
  const quotas = officeQuotas(entitled.length, capacity);

  // PASS 2 — place the fresh characters in placement order
  const freeWorkstations = (office: number): string[] =>
    offices[office].workstationIds.filter((s) => !claimedBy.has(s));
  const freeTables = (office: number): string[] =>
    offices[office].tableIds.filter((s) => !claimedBy.has(s));
  const freeChairs = (office: number): string[] =>
    offices[office].chairIds.filter((s) => !claimedBy.has(s));
  const position = new Map(placementOrder.map((id, i) => [id, i] as const));
  /** a team's placed members, as (placement position, office, seat, lead?) */
  const teamPlaced = new Map<string, Array<{ pos: number; office: number; seat: string; lead: boolean }>>();
  const notePlaced = (id: number): void => {
    const c = byId.get(id);
    const seat = seatFor.get(id);
    if (!c || seat === undefined) return;
    const list = teamPlaced.get(c.teamKey) ?? [];
    list.push({ pos: position.get(id) ?? Infinity, office: officeOfSeat.get(seat)!, seat, lead: c.isLead });
    teamPlaced.set(c.teamKey, list);
  };
  for (const id of placementOrder) if (reservedIds.has(id)) notePlaced(id);

  const fresh = new Set<number>();
  const fallback = new Set<number>();
  const deskless: number[] = [];
  const anchorSeat = (teamKey: string, office: number): { col: number; row: number } | null => {
    const inOffice = (teamPlaced.get(teamKey) ?? []).filter((m) => m.office === office);
    if (inOffice.length === 0) return null;
    const lead = inOffice.find((m) => m.lead);
    const chosen = lead ?? [...inOffice].sort((a, b) => a.pos - b.pos)[0];
    return seatPos.get(chosen.seat)!;
  };

  for (const id of placementOrder) {
    if (!entitledIds.has(id) || reservedIds.has(id)) continue;
    const c = byId.get(id)!;
    const placed = teamPlaced.get(c.teamKey) ?? [];
    const current = placed.length > 0 ? [...placed].sort((a, b) => b.pos - a.pos)[0].office : undefined;
    let target = -1;
    if (current !== undefined && assigned[current] < quotas[current] && freeWorkstations(current).length > 0) {
      target = current;
    } else {
      let bestRemaining = -Infinity;
      for (let i = 0; i < offices.length; i++) {
        if (freeWorkstations(i).length === 0) continue;
        const remaining = quotas[i] - assigned[i];
        if (remaining > bestRemaining) {
          bestRemaining = remaining;
          target = i;
        }
      }
    }
    let seat: string | undefined;
    let kind: SeatKind = 'workstation';
    if (target >= 0) {
      const free = freeWorkstations(target);
      const anchor =
        anchorSeat(c.teamKey, target) ?? anchorOf(offices[target].workstationIds, seatPos)!;
      seat = [...free].sort(byPosition(seatPos, anchor.col, anchor.row))[0];
    } else {
      // no workstation is free in ANY office: the honest fallback, a table seat and then an other chair (never because it is nearer)
      const tier = (['table', 'chair'] as const).find((t) =>
        offices.some((_, i) => (t === 'table' ? freeTables(i) : freeChairs(i)).length > 0),
      );
      if (tier !== undefined) {
        kind = tier;
        const freeOf = tier === 'table' ? freeTables : freeChairs;
        const withFree = offices.map((_, i) => i).filter((i) => freeOf(i).length > 0);
        const office = current !== undefined && withFree.includes(current) ? current : withFree[0];
        const base =
          offices[office].workstationIds.length > 0
            ? offices[office].workstationIds
            : tier === 'table'
              ? offices[office].tableIds
              : offices[office].chairIds;
        const anchor = anchorSeat(c.teamKey, office) ?? anchorOf(base, seatPos)!;
        seat = [...freeOf(office)].sort(byPosition(seatPos, anchor.col, anchor.row))[0];
      }
    }
    if (seat === undefined) {
      deskless.push(id);
      continue;
    }
    reserve(id, seat);
    kindOf.set(id, kind);
    fresh.add(id);
    if (kind !== 'workstation') fallback.add(id);
    notePlaced(id);
  }

  // entitled characters that were never reached by the placement order (not in it) get no seat either
  for (const c of entitled) if (!seatFor.has(c.id) && !deskless.includes(c.id)) deskless.push(c.id);
  // characters beyond the seats are entitled to nothing
  for (const c of entitlement) if (!entitledIds.has(c.id)) deskless.push(c.id);

  return { seatFor, kindOf, officeOf, fresh, fallback, deskless, quotas, assigned };
}
