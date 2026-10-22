/**
 * C1 (items 08/09, with 04 and 12): finding, selecting and describing the DI office's records.
 *
 * Every record keeps its own source-qualified identity: two records that look alike (one role
 * name in two roots, one objective from two sources) stay two results, never merged. Navigation
 * is PRESENTATION ONLY: it moves the selection, the camera and the list, and never creates
 * membership, starts work or changes any record.
 *
 * Plain wording (item 12): every distinct state keeps a DISTINCT plain phrase; only the raw code
 * moves behind the labelled provenance disclosure, where it is always shown verbatim.
 */
import type {
  DiMember,
  DiMission,
  DiObservedValue,
  DiOfficeState,
  DiRuntimeAgent,
} from '../../../core/src/messages.js';
import { formatEpoch, formatReviewPreamble, missionHeadline } from './diOfficeFormat.js';

/**
 * The keyboard focus indicator: the reception inbox's proven mechanism (the one the compact
 * reception desk used before it), drawn inside the element so a scrolling list never clips it.
 */
export const DI_FOCUS_RING =
  'focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-accent';

// -- plain wording --------------------------------------------------------------------------

/**
 * A team member's identity, in words — as of the LAST runtime observation (round 11): whether
 * that observation is still current is said separately (`officeFreshness`), never assumed here.
 */
export function plainMemberIdentity(classification: string): string {
  switch (classification) {
    case 'present':
      return 'running identity confirmed in the last observation: the observed live session was the recorded one';
    case 'replaced':
      return 'replaced in the last observation: a different live session carried this name, so it is not this member';
    case 'disappeared':
      return 'not in the last live listing: no listed agent carried this name';
    case 'ambiguous':
      return 'ambiguous in the last observation: which listed agent is this member could not be decided';
    default:
      return 'identity in an unrecognised state (see provenance)';
  }
}

/**
 * The canonical binding reasons (`mission_sources/roster.py`, `chains.py`), in words. Each says
 * what the record or the last observation SHOWED; none claims a lookup that was not made.
 */
const BINDING_REASON_TEXT: Record<string, string> = {
  canonical_chain_complete:
    'its task was matched in the last runtime snapshot (members are checked one by one)',
  mission_not_active: 'the record is not active',
  mission_record_ambiguity: 'the record itself is ambiguous',
  mission_workspace_inconsistent: 'the record names inconsistent workspaces',
  run_not_started: 'no run has started for it, so there is nothing to look up',
  run_outcome_unknown: 'a run started but no task is recorded, so there is nothing to look up',
  task_identity_unresolved:
    'a receipt is recorded but its task is not resolved, so there is nothing to look up',
  workspace_unrecorded: 'no workspace is recorded for it, so it cannot be looked up',
  task_claimed_by_multiple_missions: 'its task is claimed by more than one record',
  workspace_claimed_by_multiple_missions: 'its workspace is claimed by more than one record',
  runtime_snapshot_not_fresh: 'the runtime snapshot was not fresh, so it was not checked',
  workspace_not_observed: 'its workspace was not observed',
  binding_not_confirmed_by_both_passes: 'the two source passes did not both confirm it',
  task_not_in_snapshot: 'its task was not listed in the last runtime snapshot',
  task_duplicated_in_snapshot: 'its task appeared more than once in the last runtime snapshot',
  task_workspace_mismatch: 'its task ran in a different workspace than recorded',
  covered_by_canonical_record: 'a canonical record covers the same task',
  canonical_claim_conflict: 'a canonical record claims part of it',
  canonical_source_not_fresh:
    'the canonical sources could not be read fresh, so it was not checked',
  covered_by_parent_chain: 'a recorded parent chain covers it',
  task_record_not_corroborated_by_a_live_session:
    'no recorded role was observed live on exactly its recorded session',
  chain_membership_ended: 'its chain membership ended',
  child_record_incomplete: 'the child record is incomplete',
  child_record_cycle: 'the child records form a cycle',
  child_record_stale: 'the child record names another task',
  parent_binding_not_confirmed: 'its parent binding is not confirmed',
};

/** A canonical binding reason in words; an unrecognised one is carried verbatim. */
export function plainBindingReason(reason: string): string {
  return BINDING_REASON_TEXT[reason] ?? `reason ${reason}`;
}

/**
 * A Mission's team binding, in words, WITH its canonical reason (round 11, F3): `unbound` and
 * `unverified` say the binding is not established or not confirmed — never that a search ran
 * and found nothing; `disappeared` says only what the last runtime snapshot listed.
 */
export function plainBinding(bindingState: string, bindingReason: string): string {
  const why = plainBindingReason(bindingReason);
  switch (bindingState) {
    case 'bound':
      return `team bound: ${why}`;
    case 'unbound':
      return `team not bound: ${why}`;
    case 'disappeared':
      return `team not seen: ${why}`;
    case 'ambiguous':
      return `team ambiguous: ${why}`;
    case 'unverified':
      return `team not verified: ${why}`;
    case 'not_applicable':
      return `no team binding applies: ${why}`;
    default:
      return `team binding in an unrecognised state (${bindingState}; ${why})`;
  }
}

/** A Mission's standing, in words, with its record state verbatim. */
export function plainStanding(standing: string, lifecycleState: string): string {
  switch (standing) {
    case 'active':
      return `active (record state ${lifecycleState})`;
    case 'inactive':
      return `not active (record state ${lifecycleState})`;
    case 'terminal':
      return `ended (record state ${lifecycleState}; ended is not verified success)`;
    default:
      return `standing not recognised (record state ${lifecycleState})`;
  }
}

/** The identity state of a runtime record, in words. */
export function plainIdentityState(state: string): string {
  switch (state) {
    case 'verified':
      return 'verified';
    case 'unverified':
      return 'not verified';
    case 'ambiguous':
      return 'ambiguous (not verified)';
    case 'unavailable':
      return 'not observable (not verified)';
    default:
      return 'in an unrecognised state (not verified)';
  }
}

const IDENTITY_REASON_TEXT: Record<string, string> = {
  session_replaced:
    'its recorded session was replaced: in the last observation the live agent with this name ran a different session than the role binding records',
  native_identity_degraded:
    "the native identity check did not pass: in the last observation the live agent's stable identity did not match the record",
  role_binding_incomplete: 'the role binding record is incomplete',
  role_not_read_in_first_source_pass: 'its role was not read in the first of the two source passes',
  role_records_changed_between_source_passes:
    'its role records changed between the two source passes',
  stable_records_disagree: 'its role record has no pane or workspace to check',
  agent_name_duplicated_in_listing: 'more than one listed agent had this name',
  live_session_unknown: 'the listed agent reported no session',
  live_session_shared_by_agents: 'its listed session was shared by more than one agent',
  stable_identity_not_requested_within_bound:
    'its stable identity was not requested (the capture bound was reached)',
  stable_identity_not_observed: 'its stable identity was not observed',
  team_binding_ambiguous: 'its team binding is ambiguous',
  identity_recorded_by_multiple_roots: 'the same identity is recorded by more than one root',
};

/** Why a runtime record is not verified, in words (the raw reason stays in provenance). */
export function plainIdentityReason(reason: string): string {
  return IDENTITY_REASON_TEXT[reason.split(':')[0]] ?? 'the reason is in the provenance below';
}

/** A not-live / not-current reason, in words (the raw reason stays available, verbatim). */
export function plainNotLive(reason: string | null): string {
  if (reason === null) return 'no reason was given';
  if (reason.startsWith('runtime_identity_')) return 'its runtime identity is not verified';
  if (reason === 'transport_reconnected_awaiting_office_state')
    return 'this view reconnected and is waiting for a new office state';
  if (reason.startsWith('transport_')) return 'this view is not connected to the office';
  if (reason.startsWith('transport:')) return 'the office could not reach its backend';
  if (reason.startsWith('client_expired')) return 'no newer office state arrived before it expired';
  if (reason.startsWith('expired_locally')) return 'the office state has expired';
  if (reason === 'office_not_live') return 'the office is not live';
  if (reason === 'agent_not_live') return 'its agent was not in the last live listing';
  return 'the reason is in the provenance below';
}

/**
 * Round 11 (F1): whether what the office state says was observed is CURRENT, from current
 * evidence only — the presented office state's own liveness (server freshness and this view's
 * connection, after the client's invalidation). Stale is never shown as live.
 */
export interface DiFreshness {
  current: boolean;
  /** The raw not-current reason, verbatim (null when current). */
  reason: string | null;
  statement: string;
}

export function officeFreshness(state: DiOfficeState, observedAt: number | null): DiFreshness {
  const when = formatEpoch(observedAt);
  if (state.live)
    return {
      current: true,
      reason: null,
      statement: `Observed at ${when}; the office state is live, so this observation is current.`,
    };
  const reason = state.notLiveReason;
  return {
    current: false,
    reason,
    statement: `Last observed at ${when}. NOT current: ${plainNotLive(reason)}; it may have changed since.`,
  };
}

/** What a selection remembers of its record, to say truthfully that it is gone. */
export interface DiSelectionMemory {
  key: string;
  label: string;
  kind: DiRecordTarget['kind'];
  missionRef: string | null;
}

/**
 * Round 11 (F2): why a selected record is no longer in the office state — the canonical reason
 * when the state gives one (the list that held it is not observed), else that the observed list
 * no longer includes it (no reason is recorded for that).
 */
export function missingRecordReason(
  state: DiOfficeState,
  memory: DiSelectionMemory,
): { plain: string; canonical: string | null } {
  if (memory.kind === 'runtime' || memory.kind === 'unverified') {
    const r = state.runtimeAgents;
    if (!r) return { plain: 'this office state reports no runtime agents', canonical: null };
    if (r.state !== 'observed') {
      const canonical = r.problem ?? r.state;
      return {
        plain: `the runtime agents list is ${r.state ?? 'not reported'}: ${plainNotLive(canonical)}`,
        canonical: `runtime_agents ${r.state ?? 'not reported'} (${r.problem ?? 'no problem given'})`,
      };
    }
    return {
      plain:
        'the observed runtime agents list no longer includes it; no reason is recorded for that',
      canonical: null,
    };
  }
  const missions = state.missions;
  if (missions.state !== 'observed')
    return {
      plain: `the Missions list is ${missions.state}: ${plainNotLive(missions.reason)}`,
      canonical: `missions ${missions.state} (${missions.reason ?? 'no reason given'})`,
    };
  const mission = state.missions.missions.find((m) => m.missionRef === memory.missionRef);
  if (!mission)
    return {
      plain:
        'the observed Missions list no longer includes its Mission; no reason is recorded for that',
      canonical: null,
    };
  if (memory.kind === 'member' && mission.members.state !== 'observed')
    return {
      plain: `its Mission's member list is ${mission.members.state}: ${plainNotLive(mission.members.reason)}`,
      canonical: `members ${mission.members.state} (${mission.members.reason ?? 'no reason given'})`,
    };
  return {
    plain: `its Mission no longer lists it (the binding is now: ${plainBinding(mission.bindingState, mission.bindingReason)})`,
    canonical: `binding ${mission.bindingState} (${mission.bindingReason})`,
  };
}

/**
 * item 09: a Mission's outcome facts, each SEPARATE from every other and from any terminal state:
 * reported completion, independent verification, review and delivery. Nothing is inferred.
 */
export function plainMissionOutcomes(mission: DiMission): {
  completion: string;
  verification: string;
  review: string;
  delivery: string;
} {
  const delivery = 'not reported (the office projects no delivery evidence)';
  if (mission.herdrTask) {
    const none = 'not supported for a plain Herdr task record';
    return { completion: none, verification: none, review: none, delivery };
  }
  if (!mission.v2.projected) {
    const none = 'not reported by this office (query contract v1 projects no Mission State)';
    return { completion: none, verification: none, review: none, delivery };
  }
  const c = mission.v2.missionState.completion;
  const yn = (v: boolean | null) => (v === null ? 'not recorded' : v ? 'yes' : 'no');
  const observed = c.state === 'observed';
  const preamble = formatReviewPreamble(mission.v2.canonicalReview);
  return {
    completion: observed
      ? `reported complete: ${yn(c.reportedComplete)} · reported success: ${yn(c.reportedSuccess)} (as reported, not verified)`
      : 'not observed (the reason is in the provenance below)',
    verification: observed
      ? `verified success: ${yn(c.verifiedSuccess)} · closure verified: ${yn(c.closureVerified)}`
      : 'not observed (the reason is in the provenance below)',
    review: preamble.admitted
      ? `canonical review verdict: ${preamble.verdict} (read-only preamble; not proven to apply to the current candidate)`
      : 'no admitted canonical review',
    delivery,
  };
}

/** An observed fact that NO supported field reports yet: unknown, and said why. */
export function plainUnreported(v: DiObservedValue): string {
  if (v.state === 'observed' && v.value === 'unknown')
    return 'unknown (no supported source reports it)';
  if (v.state === 'observed' && v.value)
    return `${v.value} (as reported; not independently confirmed)`;
  return 'unknown (not reported)';
}

// -- records ----------------------------------------------------------------------------------

export type DiRecordTarget =
  | { kind: 'mission'; mission: DiMission }
  | { kind: 'member'; mission: DiMission; member: DiMember }
  | { kind: 'runtime'; agent: DiRuntimeAgent }
  | { kind: 'unverified'; agent: DiRuntimeAgent };

export interface DiRecord {
  /** Stable across updates: the record's own complete ref (never its character number). */
  key: string;
  target: DiRecordTarget;
  characterId: number | null;
  /** Plain title and qualifier: what the record is, where it comes from, whether it is drawn. */
  title: string;
  detail: string;
  sourceId: string;
  missionRef: string | null;
  /** Lower-cased searchable fields: objective, identifiers, role and identity fields. */
  fields: string[];
}

const lower = (values: Array<string | null | undefined>) =>
  values.filter((v): v is string => typeof v === 'string' && v !== '').map((v) => v.toLowerCase());
const observedValue = (v: DiObservedValue) => (v.state === 'observed' ? v.value : null);
const drawnText = (characterId: number | null) =>
  characterId !== null ? 'on the map' : 'not drawn';

/**
 * The short registry alias of a runtime record's root (alias only — no source). It does NOT by itself
 * keep two records apart: two sources can carry the same alias. First-layer names that must tell
 * lookalikes apart use diPlainSummary.firstLayerName (herd and source, as the list decides).
 */
export function rootLabel(agent: Pick<DiRuntimeAgent, 'rootAliases'>): string {
  return agent.rootAliases.length > 0 ? `root ${agent.rootAliases[0]}` : 'root alias not recorded';
}

export function missionKey(missionRef: string): string {
  return `mission:${missionRef}`;
}

/** Every record of the office state, in panel order: Missions, members, runtime, unverified. */
export function diRecords(state: DiOfficeState): DiRecord[] {
  const missions: DiRecord[] = [];
  const members: DiRecord[] = [];
  for (const mission of state.missions.missions) {
    const drawn = mission.members.members.filter((m) => m.characterId !== null).length;
    missions.push({
      key: missionKey(mission.missionRef),
      target: { kind: 'mission', mission },
      characterId: null,
      title: missionHeadline(mission),
      detail: `Mission · source ${mission.sourceId} · ${plainBinding(mission.bindingState, mission.bindingReason)} · ${
        drawn > 0 ? `${drawn} agent(s) on the map` : 'no agent on the map'
      }`,
      sourceId: mission.sourceId,
      missionRef: mission.missionRef,
      fields: lower([
        observedValue(mission.objective),
        mission.missionRef,
        mission.nativeId,
        mission.sourceId,
        ...(mission.herdrTask?.rootAliases ?? []),
      ]),
    });
    for (const member of mission.members.members) {
      members.push({
        key: `member:${member.memberRef}`,
        target: { kind: 'member', mission, member },
        characterId: member.characterId,
        title: `${member.logical} (${member.role})`,
        detail: `team member · ${missionHeadline(mission)} · source ${mission.sourceId} · ${drawnText(member.characterId)}`,
        sourceId: mission.sourceId,
        missionRef: mission.missionRef,
        fields: lower([
          member.logical,
          member.role,
          member.agent,
          member.provider,
          member.memberRef,
          observedValue(member.taskId),
        ]),
      });
    }
  }
  const runtime: DiRecord[] = [];
  const unverified: DiRecord[] = [];
  for (const agent of state.runtimeAgents?.agents ?? []) {
    const record: DiRecord = {
      key: `runtime:${agent.agentRef}`,
      target: agent.identityVerified ? { kind: 'runtime', agent } : { kind: 'unverified', agent },
      characterId: agent.characterId,
      title: `${agent.logical} · ${agent.agent} · ${rootLabel(agent)}`,
      detail: agent.identityVerified
        ? `runtime agent, not a team member · source ${agent.sourceId} · ${drawnText(agent.characterId)}`
        : `runtime identity ${plainIdentityState(agent.identityState)}; role/task membership not established · source ${agent.sourceId} · not drawn`,
      sourceId: agent.sourceId,
      missionRef: null,
      fields: lower([
        agent.logical,
        agent.role,
        agent.agent,
        agent.provider,
        agent.agentRef,
        agent.taskId,
        ...agent.rootAliases,
      ]),
    };
    (agent.identityVerified ? runtime : unverified).push(record);
  }
  return [...missions, ...members, ...runtime, ...unverified];
}

export function recordByKey(state: DiOfficeState, key: string | null): DiRecord | null {
  if (key === null) return null;
  return diRecords(state).find((r) => r.key === key) ?? null;
}

/** The record a drawn character is (members first, then runtime agents), or null. */
export function recordForCharacter(
  state: DiOfficeState,
  characterId: number | null,
): DiRecord | null {
  if (characterId === null) return null;
  const records = diRecords(state).filter((r) => r.target.kind !== 'mission');
  return records.find((r) => r.characterId === characterId) ?? null;
}

// -- find ----------------------------------------------------------------------------------------

/** At most this many results are listed; the statement says how many more matched. */
export const DI_FIND_MAX = 40;

export interface DiFindOutcome {
  query: string;
  total: number;
  results: DiRecord[];
  /** More than one record matched: they are DIFFERENT records, and the statement says so. */
  ambiguous: boolean;
  statement: string;
}

/** The records whose objective, identifiers, role or identity fields contain `query`. */
export function findRecords(state: DiOfficeState, query: string): DiFindOutcome | null {
  const q = query.trim().toLowerCase();
  if (q === '') return null;
  const shown = query.trim();
  const matches = diRecords(state).filter((r) => r.fields.some((f) => f.includes(q)));
  const total = matches.length;
  const results = matches.slice(0, DI_FIND_MAX);
  let statement: string;
  if (total === 0) {
    statement = `No Mission or agent matches “${shown}” in this office state.`;
  } else if (total === 1) {
    statement = `1 record matches “${shown}”.`;
  } else {
    const sources = new Set(matches.map((r) => r.sourceId)).size;
    statement =
      `${total} records match “${shown}”: they are different records ` +
      `(${sources > 1 ? `${sources} sources, ` : ''}separate roots, Missions or roles), never merged — choose one.`;
  }
  if (total > results.length)
    statement += ` Showing the first ${results.length}; ${total - results.length} more match — refine the search.`;
  return { query: shown, total, results, ambiguous: total > 1, statement };
}
