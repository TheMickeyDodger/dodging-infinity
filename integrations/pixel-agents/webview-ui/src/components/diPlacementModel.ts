/**
 * The characters of a DI office state in the two orders the three-office allocation needs (DOM-free, unit-tested):
 *   entitlement — `diSeatingOrder` unchanged: team members in Mission order, then verified runtime agents; it decides who holds a desk when desks run short;
 *   placement   — team by team in Mission order (one Mission is one team), within a team the LEAD(S) first then the members by stable identity key
 *                 (ascending); then each runtime agent as its own team of one.
 * Identity is the canonical tuple key (diIdentityKey / diRuntimeIdentityKey), never the process-local character number.
 */
import type { DiOfficeState } from '../../../core/src/messages.js';
import type { AllocCharacter } from '../office/layout/diAllocation.js';
import { diSeatingOrder } from '../office/layout/diSeating.js';
import { diIdentityKey, diRuntimeIdentityKey } from './diOfficeFormat.js';

export interface DiPlacementModel {
  entitlement: AllocCharacter[];
  placementOrder: number[];
  /** characterId → canonical identity key, for every present character. */
  keys: Map<number, string>;
}

export function diPlacementModel(
  state: Pick<DiOfficeState, 'missions'> & {
    runtimeAgents?: DiOfficeState['runtimeAgents'];
  },
): DiPlacementModel {
  const info = new Map<number, AllocCharacter>();
  const teams: number[][] = [];
  for (const mission of state.missions.missions) {
    const teamKey = JSON.stringify(['mission', mission.sourceId, mission.missionRef]);
    const ids: number[] = [];
    for (const member of mission.members.members) {
      const id = member.characterId;
      if (id === null || info.has(id)) continue;
      info.set(id, {
        id,
        key: diIdentityKey(mission, member),
        teamKey,
        isLead: member.role === 'lead',
      });
      ids.push(id);
    }
    teams.push(ids);
  }
  const runtime: number[] = [];
  for (const agent of state.runtimeAgents?.agents ?? []) {
    const id = agent.characterId;
    if (id === null || info.has(id)) continue;
    const key = diRuntimeIdentityKey(agent);
    info.set(id, { id, key, teamKey: JSON.stringify(['runtime', key]), isLead: false });
    runtime.push(id);
  }
  const placementOrder: number[] = [];
  for (const ids of teams) {
    const members = ids.map((id) => info.get(id)!);
    const rank = (c: AllocCharacter): number => (c.isLead ? 0 : 1);
    members.sort((a, b) => rank(a) - rank(b) || (a.key < b.key ? -1 : a.key > b.key ? 1 : 0));
    for (const c of members) placementOrder.push(c.id);
  }
  placementOrder.push(...runtime);
  const entitlement = diSeatingOrder(state)
    .map((id) => info.get(id))
    .filter((c): c is AllocCharacter => c !== undefined);
  const keys = new Map<number, string>([...info].map(([id, c]) => [id, c.key]));
  return { entitlement, placementOrder, keys };
}
