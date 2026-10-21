/**
 * DI SEATING (mission-owned): which DI character holds a desk when desks run short. A team MEMBER is
 * entitled to a desk before any runtime agent, whatever the arrival order, so the continuous page and a
 * reload seat the same characters. Pure over an `OfficeState`-shaped host (`DiSeatingHost`); imported only
 * by `reconcileDiCanvas` (hooks/useExtensionMessages.ts) and its seating tests.
 */
import type { DiOfficeState } from '../../../../core/src/messages.js';

/**
 * The order in which DI characters are entitled to desks: every team MEMBER first (office-state mission
 * order), then every verified runtime agent (office-state order), each character once. It is the same order
 * `reconcileDiCharacters` adds them in, so a fresh page load and a continuous page agree.
 */
export function diSeatingOrder(
  state: Pick<DiOfficeState, 'missions'> & {
    runtimeAgents?: { agents: ReadonlyArray<{ characterId: number | null }> };
  },
): number[] {
  const order: number[] = [];
  const seen = new Set<number>();
  const push = (id: number | null) => {
    if (id === null || seen.has(id)) return;
    seen.add(id);
    order.push(id);
  };
  for (const mission of state.missions.missions)
    for (const member of mission.members.members) push(member.characterId);
  for (const agent of state.runtimeAgents?.agents ?? []) push(agent.characterId);
  return order;
}

/** What the DI seating reconciliation needs of the office (OfficeState provides it). */
export interface DiSeatingHost {
  readonly characters: ReadonlyMap<number, { seatId: string | null }>;
  readonly seats: ReadonlyMap<string, unknown>;
  seatIfFree(agentId: number): boolean;
  vacateSeat(agentId: number): string | null;
}

/**
 * DI SEATING: the first `seats` characters of `order` (members, then runtime agents) are entitled to a desk.
 * Any entitled character with none takes a free seat, and when none is free it takes the desk of the
 * LOWEST-priority character that holds one without being entitled. Arrival order therefore never decides who
 * waits: a late team member is seated before an earlier runtime agent, a reload reaches the same seating as
 * the continuous page, and when desks genuinely run out the characters left deskless are the last in `order`
 * (a runtime agent unless the members alone outnumber the desks). Ids, zones, selection and activity are
 * untouched; nobody is added or removed; a second call with the same input moves nothing.
 */
export function reconcileDiSeats(host: DiSeatingHost, order: readonly number[]): void {
  const present = order.filter((id) => host.characters.has(id));
  const entitled = new Set(present.slice(0, host.seats.size));
  const needy = present.filter(
    (id) => entitled.has(id) && host.characters.get(id)!.seatId === null,
  );
  if (needy.length === 0) return;
  const squatters = present
    .filter((id) => !entitled.has(id) && host.characters.get(id)!.seatId !== null)
    .reverse();
  for (const id of needy) {
    if (host.seatIfFree(id)) continue;
    const victim = squatters.shift();
    if (victim === undefined) return;
    host.vacateSeat(victim);
    host.seatIfFree(id);
  }
}
