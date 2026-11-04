/**
 * An office state whose Orchestrator capacity is KNOWN-AVAILABLE, for interaction tests that are about something else (the New Mission handoff, closing the request
 * workspace) and must not rest on ambient capacity.
 *
 * Why it exists: the session-mismatch fixture (real projection output, 64 Missions) holds 2 Orchestrators and has 14 Missions whose capacity cannot be checked. The
 * guard counts those as in use (an unknown never reads as spare capacity), so 2 + 14 reaches the limit of 15 and Continue is rightly disabled. The interaction tests
 * had been passing only while unknowns counted as zero.
 *
 * The variation is LABELLED SYNTHETIC: the same state with only the Missions whose capacity reading is not known removed; nothing else is edited. Its use is pinned
 * EXACTLY (`AVAILABLE_USE`), so if the reading ever changes this fails loudly rather than quietly adapting. The guard itself is never touched or special-cased; the
 * unmodified fixture keeps its own countercase (Continue refused, with its reason).
 */
import type { DiOfficeState } from '../../core/src/messages.ts';
import { missionStatus } from '../src/components/diMissionStatus.ts';
import { scanRowsFromOffice } from '../src/components/diScanModel.ts';
import { atOrchestratorLimit, type DiOrchestratorUse, orchestratorUse } from '../src/components/diScanPagesModel.ts';

/** The unmodified session-mismatch fixture's use, as the guard reads it. */
export const FIXTURE_USE: DiOrchestratorUse = { holding: 2, notKnown: 14 };
/** The available-capacity variation's use: the same 2 held, nothing that cannot be checked. */
export const AVAILABLE_USE: DiOrchestratorUse = { holding: 2, notKnown: 0 };

export function capacityUseOf(state: DiOfficeState): DiOrchestratorUse {
  const use = orchestratorUse(scanRowsFromOffice(state));
  return { holding: use.holding, notKnown: use.notKnown };
}

export function availableCapacityOffice(state: DiOfficeState): DiOfficeState {
  const variation = structuredClone(state);
  variation.missions.missions = state.missions.missions.filter((mission) => missionStatus(mission, state).capacity.bears !== 'not_known');
  const use = capacityUseOf(variation);
  if (use.holding !== AVAILABLE_USE.holding || use.notKnown !== AVAILABLE_USE.notKnown || atOrchestratorLimit(orchestratorUse(scanRowsFromOffice(variation))))
    throw new Error(`the available-capacity variation is not what it claims: ${JSON.stringify(use)}`);
  return variation;
}
