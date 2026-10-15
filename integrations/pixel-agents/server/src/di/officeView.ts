/**
 * What the DI office may present, decided server-side from one catalog answer.
 * The renderer receives this projected state only — it never reads a store,
 * re-derives a record or infers anything the backend did not project.
 *
 * Liveness is layered, and every layer must hold:
 * 1. The backend answered with a well-formed envelope carrying a response.
 * 2. The backend's SERVE-TIME verdict (X-DI-Freshness-Health) is explicitly `fresh`.
 *    The consumer never reimplements the freshness rule.
 * 3. LOCAL SAFETY FLOOR: `now <= valid_until` (integer epoch seconds, INCLUSIVE —
 *    fresh at equality, stale one second after). A floor only: it can take
 *    liveness away, never grant it.
 * 4. Per mission (v1 DIRECT roster only): `lifecycle.standing` is `active` AND
 *    `binding.state` is `bound`. Standing alone is never enough; decisions never
 *    switch on `lifecycle.state`.
 * 5. Per member: the mission is live AND the classification is `present` or
 *    `replaced`. `session.live`, `pane_id`, `workspace_id` and `agent` are identity
 *    and evidence fields — they NEVER confer liveness.
 * There is no healthy fallback anywhere: every other branch is not-live and carries
 * its reason. Nothing here derives a linked child task's state from its parent:
 * child lifecycles are not projected in v1, so linked-child roles are NOT COVERED.
 *
 * Observation states (each rendered distinctly; none substitutes for another):
 * - observed:         the read succeeded and the value is present;
 * - known_empty:      the read succeeded and there are none (a positive observation);
 * - not_recorded:     the read succeeded and this field is null for this record;
 * - projected_reason: the backend projected a specific reason instead of a value
 *                     (e.g. members under binding `disappeared / task_not_in_snapshot`);
 * - not_applicable:   the field does not exist for this SOURCE KIND at all (never
 *                     rendered as empty — e.g. `updated_at` on a workflow_store);
 * - unavailable:      we could NOT read (with the reason).
 */

import type { Classified, MemberClassification, QueryContractVersion } from './catalogContract.js';
import { DEFAULT_QUERY_CONTRACT_VERSION } from './catalogContract.js';
import type {
  BoundedList,
  CatalogEnvelope,
  CatalogResponseView,
  MemberView,
  MissionReportView,
  MissionStateView,
  MissionView,
  ReportFact,
  RuntimeCaptureDiagnostics,
  ShapeVerdict,
  TaskChainView,
  UnreportedFact,
} from './catalogProjection.js';
import { projectResponse } from './catalogProjection.js';
import type { Coverage } from './identityMap.js';
import type { ServeTimeVerdict } from './serveTimeVerdict.js';
import { verdictIsFresh } from './serveTimeVerdict.js';

/**
 * Coverage of the member roster, stated in office state and in evidence: the backend
 * projects each Mission record's OWN receipt execution task only. Roles of linked
 * child tasks are not projected, so they are NOT COVERED — not live, not ended.
 */
export interface CoverageStatement {
  scope: string;
  statement: string;
  linkedChildRoles: string;
  linkedChildStatement: string;
}

export const DI_COVERAGE: CoverageStatement = {
  scope: 'direct_receipt_task',
  statement: "Members are the roster of each Mission record's own receipt execution task only.",
  linkedChildRoles: 'not_covered',
  linkedChildStatement:
    'Roles of linked child tasks are not projected by the backend; they are not covered here.',
};

/**
 * contract v2 coverage: the direct task AND the children recorded for it, at depth 1. A child
 * root's own children are counted (descendants not followed), never captured: a DEPTH LIMIT,
 * stated as such, never "no children".
 */
export const DI_COVERAGE_V2: CoverageStatement = {
  scope: 'direct_and_linked_child_tasks_depth_1',
  statement:
    "Members are the roster of each Mission's direct task and of the child tasks recorded for it (chain depth 1).",
  linkedChildRoles: 'covered_depth_1',
  linkedChildStatement:
    "Linked child roles are covered at depth 1; a child's own children are depth-limited (counted, not followed).",
};

export function coverageFor(version: QueryContractVersion): CoverageStatement {
  return version === 2 ? DI_COVERAGE_V2 : DI_COVERAGE;
}

export type Observation<T> =
  | { state: 'observed'; value: T }
  | { state: 'known_empty' }
  | { state: 'not_recorded' }
  | { state: 'projected_reason'; reason: string }
  | { state: 'not_applicable'; reason: string }
  | { state: 'unavailable'; reason: string };

export interface Presence {
  live: boolean;
  /** Why not live; null only when live. */
  reason: string | null;
}

export interface MemberPresentation {
  /** From the explicit chain field (v1 projects the direct roster only, by contract). */
  coverage: Coverage | 'unrecognized';
  memberRef: string;
  missionRef: string;
  sourceId: string;
  /** Raw task id for identity; `taskObservation` for display. */
  taskId: string | null;
  taskObservation: Observation<string>;
  logical: string;
  role: string;
  agent: string;
  provider: string;
  paneId: Observation<string>;
  workspaceId: Observation<string>;
  /** Evidence fields — never liveness. */
  sessionRecorded: Observation<string>;
  sessionLive: Observation<string>;
  classification: string;
  reason: string;
  activity: string;
  activityKnown: boolean;
  presence: Presence;
  unreported: Record<string, UnreportedFact>;
  /** contract v2 chain (`direct` | `child`, verbatim); `direct` for v1. */
  chain: string;
  chainTaskId: string | null;
}

/** One allowlisted report fact, display-ready: its observation state, certainty and entries. */
export interface ReportFactPresentation {
  name: string;
  state: Observation<never>['state'];
  reason: string | null;
  standing: string | null;
  source: string | null;
  freshness: string | null;
  detail: string | null;
  observedAt: number | null;
  revision: number | null;
  position: number | null;
  entries: Array<{ label: string; value: string | null }>;
}

/** completion: reported_* and verified_* are DIFFERENT facts and stay separate fields. */
export interface CompletionPresentation {
  state: Observation<never>['state'];
  reason: string | null;
  reportedComplete: boolean | null;
  reportedSuccess: boolean | null;
  verifiedSuccess: boolean | null;
  closureVerified: boolean | null;
  contradicted: boolean | null;
  closure: string | null;
  /** Displayed prefix; `holdsTotal` is the real length (null = UNKNOWN, not a list). */
  holds: string[];
  holdsTotal: number | null;
  limitsIdentity: string | null;
  limitsSeparationOfDuties: string | null;
  limitsNonVerifyingEvidenceKinds: string[];
  limitsNonVerifyingEvidenceKindsTotal: number | null;
}

export interface ProvenancePresentation {
  state: Observation<never>['state'];
  reason: string | null;
  recordSource: string | null;
  revision: number | null;
  position: number | null;
  freshnessBoundSeconds: number | null;
  journalDigest: string | null;
  movedDuringCollection: string | null;
  collectionStatus: string | null;
  collectedAtPosition: number | null;
  headPosition: number | null;
  cursorPosition: number | null;
  /** Displayed prefix; `driftTotal` is the real length (null = UNKNOWN, not a list). */
  drift: string[];
  driftTotal: number | null;
  sources: Array<{
    kind: string;
    source: string | null;
    standing: string | null;
    observedAt: number | null;
    detail: string | null;
  }>;
}

/** contract v2 blocks of one Mission, display-ready and verbatim. */
export interface MissionV2Presentation {
  taskChains: Array<{
    chain: string;
    chainTaskId: string | null;
    parentTaskId: string | null;
    bindingState: string;
    bindingReason: string;
    rootObservation: string;
    rootProblem: string | null;
    membershipEnded: boolean | null;
    membershipStatus: string | null;
    membershipRule: string;
    dependencyRecorded: boolean | null;
    dependencyResolution: string | null;
    dependencyRule: string | null;
    descendantsNotFollowed: number | null;
    declaredAgents: Array<{ logical: string; agent: string }>;
  }>;
  childrenRecord: {
    state: string | null;
    problem: string | null;
    caveat: string | null;
    recordCount: number | null;
    otherParentCount: number | null;
  };
  missionState: {
    state: string | null;
    problem: string | null;
    detail: string | null;
    cursor: string | null;
    baseline: string | null;
    candidateApplicability: string | null;
    kinds: Array<{ kind: string; supplied: string | null; reason: string | null }>;
    /** The allowlisted canonical report facts (G3), each with its certainty and state. */
    facts: ReportFactPresentation[];
    completion: CompletionPresentation;
    provenance: ProvenancePresentation;
  };
  canonicalReview: {
    state: string | null;
    problem: string | null;
    gate: string | null;
    verdict: string | null;
    currency: string | null;
    integrity: string | null;
    taskStatus: string | null;
    candidateApplicability: string | null;
    round: number | null;
    reviewerLogical: string | null;
    reviewerAgent: string | null;
    protocolToken: string | null;
  };
}

export interface MissionPresentation {
  missionRef: string;
  sourceId: string;
  nativeId: string;
  sourceKind: 'mission_core' | 'workflow_store' | 'unrecognized';
  lifecycleModel: string;
  /** Verbatim native literal; display only, never switched on. */
  lifecycleState: string;
  standing: string;
  standingKnown: boolean;
  bindingState: string;
  bindingReason: string;
  producerDeclared: string;
  objective: Observation<string>;
  updatedAt: Observation<number>;
  identitySource: Observation<string>;
  paused: Observation<boolean>;
  cancelRequested: Observation<boolean>;
  ambiguity: Observation<string>;
  presence: Presence;
  members: Observation<MemberPresentation[]>;
  /** contract v2 blocks; null for a v1 office. */
  v2: MissionV2Presentation | null;
}

export interface OfficeView {
  /** Did we obtain a well-formed answer at all? */
  connection: { state: 'answered' } | { state: 'unavailable'; reason: string };
  live: boolean;
  notLiveReason: string | null;
  served: string | null;
  envelopeProblem: string | null;
  envelopeDetail: string | null;
  retryAfterSeconds: number | null;
  serveTimeVerdict: { health: string | null; problem: string | null } | null;
  /** Kept DISTINCT; never collapsed into one "last updated". */
  times: {
    requestedAt: number | null;
    generatedAt: number | null;
    validUntil: number | null;
    snapshotObservedAt: number | null;
    /** This consumer's own clock at evaluation (labelled as such). */
    evaluatedAt: number;
  };
  registryHealth: string | null;
  snapshotHealth: string | null;
  snapshotProblem: string | null;
  runtimeCapture: RuntimeCaptureDiagnostics | null;
  minPollIntervalSeconds: number | null;
  /** The two independent freshness levels (null when no response was projected). */
  freshness: { envelope: FreshnessLevel; runtime: FreshnessLevel } | null;
  coverage: CoverageStatement;
  missions: Observation<MissionPresentation[]>;
  /** The ONE contract version this office process reads (selected at launch). */
  contractVersion: QueryContractVersion;
  /** contract v2 chain observation; null for v1 (or with no response). */
  chainObservation: {
    health: string;
    problem: string | null;
    observedAt: number | null;
    maxAgeSeconds: number | null;
    rootCount: number | null;
    unavailableCount: number | null;
    overCapacityCount: number | null;
  } | null;
}

const MISSION_CORE_MODEL = 'mission_core.mission_state/1';
const WORKFLOW_STORE_MODEL = 'workflow_authority.workflow_phase/2';

function raw<T extends string>(c: Classified<T>): string {
  return c.known ? c.value : `unrecognized:${String(c.raw)}`;
}

function nullable<T>(value: T | null): Observation<T> {
  return value === null ? { state: 'not_recorded' } : { state: 'observed', value };
}

function list<T>(values: T[]): Observation<T[]> {
  return values.length === 0 ? { state: 'known_empty' } : { state: 'observed', value: values };
}

function notApplicable<T>(field: string, kind: string): Observation<T> {
  return { state: 'not_applicable', reason: `${field}_not_applicable_to_${kind}` };
}

/** Inclusive local floor (CONTRACT-v3 expiry boundary). */
export function withinValidUntil(nowSeconds: number, validUntil: number): boolean {
  return nowSeconds <= validUntil;
}

export function unavailableView(
  reason: string,
  nowSeconds: number,
  version: QueryContractVersion = DEFAULT_QUERY_CONTRACT_VERSION,
): OfficeView {
  return {
    connection: { state: 'unavailable', reason },
    live: false,
    notLiveReason: reason,
    served: null,
    envelopeProblem: null,
    envelopeDetail: null,
    retryAfterSeconds: null,
    serveTimeVerdict: null,
    times: {
      requestedAt: null,
      generatedAt: null,
      validUntil: null,
      snapshotObservedAt: null,
      evaluatedAt: nowSeconds,
    },
    registryHealth: null,
    snapshotHealth: null,
    snapshotProblem: null,
    runtimeCapture: null,
    minPollIntervalSeconds: null,
    freshness: null,
    coverage: coverageFor(version),
    missions: { state: 'unavailable', reason },
    contractVersion: version,
    chainObservation: null,
  };
}

function shapeReason(verdict: ShapeVerdict): string {
  return `${verdict.health}:${verdict.problem}`;
}

function sourceKindOf(mission: MissionView): MissionPresentation['sourceKind'] {
  const { model } = mission.lifecycle;
  if (model.known && model.value === MISSION_CORE_MODEL) return 'mission_core';
  if (model.known && model.value === WORKFLOW_STORE_MODEL) return 'workflow_store';
  return 'unrecognized';
}

function missionPresence(
  mission: MissionView,
  responseLive: boolean,
  responseReason: string | null,
): Presence {
  if (!responseLive) return { live: false, reason: responseReason ?? 'response_not_live' };
  const { standing } = mission.lifecycle;
  if (!standing.known) return { live: false, reason: 'standing_unrecognized' };
  if (standing.value === 'unknown_future')
    return { live: false, reason: 'standing_unknown_future' };
  if (standing.value !== 'active') return { live: false, reason: `standing_${standing.value}` };
  // contract v2: the Mission is live when at least one of its chains is bound (its direct task
  // or a linked child); v1: its own binding. Members are then decided by their OWN chain.
  if (mission.v2 !== null) {
    const bound = mission.v2.taskChains.some(
      (c) => c.binding.state.known && c.binding.state.value === 'bound',
    );
    if (bound) return { live: true, reason: null };
    const direct = mission.v2.taskChains.find((c) => c.chain.known && c.chain.value === 'direct');
    const b = direct?.binding ?? mission.binding;
    return {
      live: false,
      reason: b.state.known
        ? `binding_${b.state.value}:${raw(b.reason)}`
        : 'binding_state_unrecognized',
    };
  }
  const { state, reason } = mission.binding;
  if (!state.known) return { live: false, reason: 'binding_state_unrecognized' };
  if (state.value !== 'bound') {
    return { live: false, reason: `binding_${state.value}:${raw(reason)}` };
  }
  return { live: true, reason: null };
}

/**
 * contract v2: the chain a member belongs to, by EXACT task-qualified agreement on both the
 * explicit kind and the chain task id — for direct and child alike, never inferred. Missing,
 * unlisted or ambiguous association is a reason, never a silent or first-wins match.
 */
function chainOf(
  mission: MissionView,
  member: MemberView,
): { chain: TaskChainView } | { problem: string } {
  if (mission.v2 === null) return { problem: 'chain_not_projected' };
  if (!member.chain.known) return { problem: 'chain_unrecognized' };
  if (member.chainTaskId === null) return { problem: 'chain_task_missing' };
  const kind = member.chain.value;
  const matches = mission.v2.taskChains.filter(
    (c) => c.chain.known && c.chain.value === kind && c.chainTaskId === member.chainTaskId,
  );
  if (matches.length === 0) return { problem: 'chain_not_listed' };
  if (matches.length > 1) return { problem: 'chain_ambiguous' };
  return { chain: matches[0] };
}

/**
 * NAMED DECISION (Supervisor ruling 2026-10-14; Reviewer item): ONLY `present` is a
 * confirmed live Mission character.
 *
 * `present` — agent, pane, workspace AND session match the canonical role binding.
 * `replaced` (reason `session_changed`) is a THIRD state, distinct from both confirmed-live
 *   and disappeared: an OBSERVED replacement runtime is not CANONICALLY SESSION-CONFIRMED
 *   membership. Same name, same pane and same workspace do NOT erase the session mismatch.
 *   It stays VISIBLE, verbatim (`replaced` / `session_changed`, with BOTH the recorded and
 *   the observed session), but it is not live: no character, not counted, not animated,
 *   until the canonical binding confirms the new session. It falls through to the
 *   not-live reason `member_replaced:session_changed` below; it is never relabelled
 *   disappeared, terminal, dead, gone or ended.
 */
export const LIVE_MEMBER_CLASSIFICATIONS: readonly MemberClassification[] = ['present'];

function memberPresence(
  member: MemberView,
  missionLive: Presence,
  association: { chain: TaskChainView } | { problem: string } | null,
): Presence {
  if (!missionLive.live) return { live: false, reason: missionLive.reason };
  if (association !== null) {
    // contract v2: a member is live only while ITS OWN chain — matched exactly — is bound (an
    // ended chain, e.g. a FAILED child or the direct task ENDED, is never live; no fallback).
    if ('problem' in association) return { live: false, reason: association.problem };
    const b = association.chain.binding;
    if (!b.state.known) return { live: false, reason: 'chain_binding_unrecognized' };
    if (b.state.value !== 'bound')
      return { live: false, reason: `chain_binding_${b.state.value}:${raw(b.reason)}` };
  }
  const c = member.classification;
  if (!c.known) return { live: false, reason: 'classification_unrecognized' };
  if (LIVE_MEMBER_CLASSIFICATIONS.includes(c.value)) return { live: true, reason: null };
  return { live: false, reason: `member_${c.value}:${raw(member.reason)}` };
}

function membersObservation(
  mission: MissionView,
  members: MemberPresentation[],
  snapshotUnavailable: string | null,
): Observation<MemberPresentation[]> {
  const { state, reason } = mission.binding;
  if (!state.known) return { state: 'unavailable', reason: 'binding_state_unrecognized' };
  // contract v2: the roster spans every bound chain, so it is observed whenever ANY chain is
  // bound (the Mission binding alone is the direct task's v1 fact).
  const v2Bound =
    mission.v2 !== null &&
    mission.v2.taskChains.some((c) => c.binding.state.known && c.binding.state.value === 'bound');
  if (v2Bound && snapshotUnavailable === null) return list(members);
  // Without a FRESH runtime observation (capture not configured, starved, timed out,
  // refused) no roster was observed: an empty list here is NOT "no members".
  if (
    snapshotUnavailable !== null &&
    ['bound', 'disappeared', 'unverified'].includes(state.value)
  ) {
    return { state: 'unavailable', reason: snapshotUnavailable };
  }
  switch (state.value) {
    case 'bound':
      return list(members);
    case 'unverified':
      // The backend could not look (snapshot not fresh, workspace not observed, …).
      return { state: 'unavailable', reason: `binding_unverified:${raw(reason)}` };
    case 'disappeared':
    case 'unbound':
    case 'ambiguous':
    case 'not_applicable':
      // A specific projected reason, not a generic "unavailable" and not "none".
      return { state: 'projected_reason', reason: `binding_${state.value}:${raw(reason)}` };
  }
}

function presentMember(
  member: MemberView,
  mission: MissionView,
  missionLive: Presence,
): MemberPresentation {
  return {
    // From the EXPLICIT field only: an unrecognized chain is never labelled direct coverage.
    coverage: member.chain.known ? member.chain.value : 'unrecognized',
    memberRef: member.memberRef,
    missionRef: member.missionRef,
    sourceId: mission.sourceId,
    taskId: member.taskId,
    taskObservation: nullable(member.taskId),
    logical: member.logical,
    role: member.role,
    agent: member.agent,
    provider: member.provider,
    paneId: nullable(member.paneId),
    workspaceId: nullable(member.workspaceId),
    sessionRecorded: nullable(member.session.recorded),
    sessionLive: nullable(member.session.live),
    classification: raw(member.classification),
    reason: raw(member.reason),
    activity: raw(member.activity),
    activityKnown: member.activity.known,
    presence: memberPresence(
      member,
      missionLive,
      mission.v2 !== null ? chainOf(mission, member) : null,
    ),
    unreported: member.unreported,
    chain: raw(member.chain),
    chainTaskId: member.chainTaskId,
  };
}

/** Facts null when there is NO ACTIVATION CONTRACT: not applicable, never "none recorded". */
const ACTIVATION_FACTS = new Set(['proof', 'readiness', 'dependencies', 'budget']);
const yesNo = (v: boolean | null): string | null => (v === null ? null : v ? 'true' : 'false');
const num = (v: number | null): string | null => (v === null ? null : String(v));
/** A count of a collection: null is UNKNOWN (the field was not a list), never a claimed 0. */
const count = (v: number | null): string => (v === null ? 'unknown (not a list)' : String(v));

/**
 * A bounded list as display entries: its REAL total (and how many the display cap omitted),
 * then the shown items. A non-list is UNKNOWN — never empty, never 0.
 */
function listEntries(
  label: string,
  list: BoundedList,
  itemLabel: string,
): Array<{ label: string; value: string | null }> {
  if (!list.known || list.total === null) return [{ label, value: 'unknown (not a list)' }];
  const omitted = list.total - list.items.length;
  return [
    {
      label,
      value:
        omitted > 0
          ? `${list.total} (showing ${list.items.length}; ${omitted} omitted by the display cap)`
          : String(list.total),
    },
    ...list.items.map((value) => ({ label: itemLabel, value })),
  ];
}

/** Why no report fact can be shown for this Mission (null when the report is present). */
function reportAbsence(
  ms: MissionStateView,
): { state: 'not_applicable' | 'unavailable'; reason: string } | null {
  if (ms.state === 'not_applicable')
    return { state: 'not_applicable', reason: ms.problem ?? 'mission_state_not_applicable' };
  if (ms.report === null)
    return { state: 'unavailable', reason: ms.problem ?? `mission_state_${ms.state}:no_report` };
  return null;
}

function presentFact<T>(
  name: string,
  fact: ReportFact<T>,
  absence: { state: 'not_applicable' | 'unavailable'; reason: string } | null,
  entries: (value: T) => Array<{ label: string; value: string | null }>,
  isEmpty: (value: T) => boolean = () => false,
): ReportFactPresentation {
  const base = {
    name,
    standing: fact.standing,
    source: fact.source,
    freshness: fact.freshness,
    detail: fact.detail,
    observedAt: fact.observedAt,
    revision: fact.revision,
    position: fact.position,
  };
  if (absence) return { ...base, state: absence.state, reason: absence.reason, entries: [] };
  if (fact.shape === 'unexpected')
    return { ...base, state: 'unavailable', reason: 'fact_shape_unrecognized', entries: [] };
  if (fact.standing === 'unavailable')
    return {
      ...base,
      state: 'unavailable',
      reason: fact.detail ?? 'fact_unavailable',
      entries: [],
    };
  if (fact.value === null) {
    return ACTIVATION_FACTS.has(name)
      ? { ...base, state: 'not_applicable', reason: 'no_activation_contract', entries: [] }
      : { ...base, state: 'not_recorded', reason: null, entries: [] };
  }
  if (isEmpty(fact.value)) return { ...base, state: 'known_empty', reason: null, entries: [] };
  return { ...base, state: 'observed', reason: null, entries: entries(fact.value) };
}

function presentReport(ms: MissionStateView): {
  facts: ReportFactPresentation[];
  completion: CompletionPresentation;
  provenance: ProvenancePresentation;
} {
  const absence = reportAbsence(ms);
  const r: MissionReportView | null = ms.report;
  const empty = <T>(): ReportFact<T> => ({
    shape: 'ok',
    standing: null,
    source: null,
    freshness: null,
    detail: null,
    observedAt: null,
    revision: null,
    position: null,
    value: null,
  });
  const f = <K extends keyof MissionReportView>(k: K) =>
    (r ? r[k] : empty()) as MissionReportView[K];
  const facts: ReportFactPresentation[] = [
    presentFact('blockers', f('blockers'), absence, (v) => [
      { label: 'hard blocker active', value: yesNo(v.hardActive) },
      ...listEntries('active blockers', v.active, 'blocker'),
    ]),
    presentFact('pending_decisions', f('pendingDecisions'), absence, (v) => [
      { label: 'awaiting decision', value: yesNo(v.awaitingDecision) },
      { label: 'live authorization', value: yesNo(v.liveAuthorization) },
      { label: 'next permitted step', value: v.nextPermittedStep },
      { label: 'revision', value: num(v.revision) },
    ]),
    presentFact('review', f('review'), absence, (v) => [{ label: 'review', value: v }]),
    presentFact('task', f('task'), absence, (v) => [{ label: 'task', value: v }]),
    presentFact('candidate', f('candidate'), absence, (v) => [
      ...listEntries('artifact digests', v.artifactDigests, 'digest'),
      { label: 'baseline digest', value: v.baselineDigest ?? 'unobserved' },
    ]),
    presentFact('delivery', f('delivery'), absence, (v) => [{ label: 'delivery', value: v }]),
    presentFact('delivery_receipts', f('deliveryReceipts'), absence, (v) => [
      { label: 'recorded', value: count(v.recorded) },
      { label: 'attested', value: count(v.attested) },
      { label: 'unattested', value: count(v.unattested) },
      { label: 'effects completed', value: count(v.effectsCompleted) },
      { label: 'report bound to', value: v.reportBoundTo },
      { label: 'report bound attested', value: v.reportBoundAttested },
      { label: 'report fresh', value: v.reportFresh },
    ]),
    presentFact('proof', f('proof'), absence, (v) => [
      { label: 'satisfied', value: yesNo(v.satisfied) },
      ...listEntries('requirements', v.requirements, 'requirement'),
    ]),
    presentFact('readiness', f('readiness'), absence, (v) => [
      { label: 'satisfied', value: yesNo(v.satisfied) },
    ]),
    presentFact('dependencies', f('dependencies'), absence, (v) => [
      { label: 'satisfied', value: yesNo(v.satisfied) },
    ]),
    presentFact('budget', f('budget'), absence, (v) => [
      { label: 'attempts remaining', value: num(v.attemptsRemaining) },
      { label: 'checkpoints remaining', value: num(v.checkpointsRemaining) },
    ]),
    presentFact(
      'evidence',
      f('evidence'),
      absence,
      (v) => listEntries('references', v, 'reference'),
      (v) => v.known && v.total === 0,
    ),
    presentFact(
      'artifacts',
      f('artifacts'),
      absence,
      (v) => listEntries('references', v, 'reference'),
      (v) => v.known && v.total === 0,
    ),
  ];
  const c = r?.completion ?? null;
  const completion: CompletionPresentation = {
    state: absence ? absence.state : c === null ? 'unavailable' : 'observed',
    reason: absence ? absence.reason : c === null ? 'completion_not_reported' : null,
    reportedComplete: c?.reportedComplete ?? null,
    reportedSuccess: c?.reportedSuccess ?? null,
    verifiedSuccess: c?.verifiedSuccess ?? null,
    closureVerified: c?.closureVerified ?? null,
    contradicted: c?.contradicted ?? null,
    closure: c?.closure ?? null,
    holds: c?.holds.items ?? [],
    holdsTotal: c?.holds.known ? c.holds.total : null,
    limitsIdentity: c?.limits?.identity ?? null,
    limitsSeparationOfDuties: c?.limits?.separationOfDuties ?? null,
    limitsNonVerifyingEvidenceKinds: c?.limits?.nonVerifyingEvidenceKinds.items ?? [],
    limitsNonVerifyingEvidenceKindsTotal: c?.limits?.nonVerifyingEvidenceKinds.known
      ? c.limits.nonVerifyingEvidenceKinds.total
      : null,
  };
  const p = r?.provenance ?? null;
  const provenance: ProvenancePresentation = {
    state: absence ? absence.state : p === null ? 'unavailable' : 'observed',
    reason: absence ? absence.reason : p === null ? 'provenance_not_reported' : null,
    recordSource: p?.recordSource ?? null,
    revision: p?.revision ?? null,
    position: p?.position ?? null,
    freshnessBoundSeconds: p?.freshnessBoundSeconds ?? null,
    journalDigest: p?.journalDigest ?? null,
    movedDuringCollection: p?.movedDuringCollection ?? null,
    collectionStatus: p?.collectionStatus ?? null,
    collectedAtPosition: p?.collectedAtPosition ?? null,
    headPosition: p?.headPosition ?? null,
    cursorPosition: p?.cursorPosition ?? null,
    drift: p?.drift.items ?? [],
    driftTotal: p?.drift.known ? p.drift.total : null,
    sources: p?.sources ?? [],
  };
  return { facts, completion, provenance };
}

function presentMissionV2(mission: MissionView): MissionV2Presentation | null {
  const v2 = mission.v2;
  if (v2 === null) return null;
  const review = v2.canonicalReview;
  return {
    taskChains: v2.taskChains.map((c) => ({
      chain: raw(c.chain),
      chainTaskId: c.chainTaskId,
      parentTaskId: c.parentTaskId,
      bindingState: raw(c.binding.state),
      bindingReason: raw(c.binding.reason),
      rootObservation: c.root.observation,
      rootProblem: c.root.problem,
      membershipEnded: c.membershipEnd.ended,
      membershipStatus: c.membershipEnd.status,
      membershipRule: c.membershipEnd.rule,
      dependencyRecorded: c.dependency?.recorded ?? null,
      dependencyResolution: c.dependency?.resolution ?? null,
      dependencyRule: c.dependency?.rule ?? null,
      descendantsNotFollowed: c.descendantsNotFollowed,
      declaredAgents: c.record?.declaredAgents ?? [],
    })),
    childrenRecord: { ...v2.childrenRecord },
    missionState: {
      state: v2.missionState.state,
      problem: v2.missionState.problem,
      detail: v2.missionState.detail,
      cursor: v2.missionState.inputs?.cursor ?? null,
      baseline: v2.missionState.inputs?.baseline ?? null,
      candidateApplicability: v2.missionState.inputs?.candidateApplicability ?? null,
      kinds: v2.missionState.inputs?.kinds ?? [],
      ...presentReport(v2.missionState),
    },
    canonicalReview: {
      state: review.state,
      problem: review.problem,
      gate: review.gate,
      verdict: review.verdict,
      currency: review.currency,
      integrity: review.integrity,
      taskStatus: review.taskStatus,
      candidateApplicability: review.candidateApplicability,
      round: review.record?.round ?? null,
      reviewerLogical: review.record?.reviewerLogical ?? null,
      reviewerAgent: review.record?.reviewerAgent ?? null,
      protocolToken: review.record?.protocolToken ?? null,
    },
  };
}

function presentMission(
  mission: MissionView,
  responseLive: boolean,
  responseReason: string | null,
  snapshotUnavailable: string | null,
): MissionPresentation {
  const presence = missionPresence(mission, responseLive, responseReason);
  const members = mission.members.map((m) => presentMember(m, mission, presence));
  const kind = sourceKindOf(mission);
  const native = mission.native;
  return {
    missionRef: mission.missionRef,
    sourceId: mission.sourceId,
    nativeId: mission.nativeId,
    sourceKind: kind,
    lifecycleModel: raw(mission.lifecycle.model),
    lifecycleState: mission.lifecycle.state,
    standing: raw(mission.lifecycle.standing),
    standingKnown: mission.lifecycle.standing.known,
    bindingState: raw(mission.binding.state),
    bindingReason: raw(mission.binding.reason),
    producerDeclared: mission.producerDeclared,
    objective: nullable(mission.objective),
    // The workflow_store reader never records these: they do not apply to that kind.
    updatedAt:
      kind === 'workflow_store' ? notApplicable('updated_at', kind) : nullable(mission.updatedAt),
    identitySource:
      kind === 'workflow_store'
        ? notApplicable('identity_source', kind)
        : nullable(mission.execution.identitySource),
    paused:
      native.kind === 'mission_core' ? nullable(native.paused) : notApplicable('paused', kind),
    cancelRequested:
      native.kind === 'mission_core'
        ? nullable(native.cancelRequested)
        : notApplicable('cancel_requested', kind),
    ambiguity:
      native.kind === 'workflow_store'
        ? nullable(native.ambiguity)
        : notApplicable('ambiguity', kind),
    presence,
    members: membersObservation(mission, members, snapshotUnavailable),
    v2: presentMissionV2(mission),
  };
}

export interface FreshnessLevel {
  fresh: boolean;
  /** Why not fresh; null only when fresh. */
  reason: string | null;
}

/** LEVEL 1 — the envelope: the backend's serve-time verdict AND the inclusive local floor. */
function envelopeFreshness(
  envelope: CatalogEnvelope,
  verdict: ServeTimeVerdict,
  view: CatalogResponseView,
  nowSeconds: number,
): FreshnessLevel {
  if (!envelope.served.known) return { fresh: false, reason: 'served_unrecognized' };
  if (verdict.health === null) return { fresh: false, reason: 'no_serve_time_verdict' };
  if (!verdictIsFresh(verdict)) {
    const problem = verdict.problem ? `:${verdict.problem}` : '';
    return { fresh: false, reason: `serve_time_${raw(verdict.health)}${problem}` };
  }
  if (!withinValidUntil(nowSeconds, view.validUntil)) {
    return { fresh: false, reason: 'expired_locally:response_expired' };
  }
  return { fresh: true, reason: null };
}

/**
 * LEVEL 2 — the nested runtime: the capture must report `available: true` AND the runtime
 * snapshot must be `fresh`. Anything else — not configured, starved, timed out, refused,
 * an absent or unrecognized value — is not fresh, and the capture's own problem code
 * (e.g. capture_not_finished_within_wait) is carried verbatim as the reason.
 */
function runtimeFreshness(
  capture: RuntimeCaptureDiagnostics,
  view: CatalogResponseView,
): FreshnessLevel {
  const snap = view.runtimeSnapshot;
  const snapshotFresh = snap.health.known && snap.health.value === 'fresh';
  if (capture.available === true && snapshotFresh) return { fresh: true, reason: null };
  const nested = capture.problem ?? snap.problem ?? 'unspecified';
  if (!snapshotFresh)
    return { fresh: false, reason: `runtime_snapshot_${raw(snap.health)}:${nested}` };
  return {
    fresh: false,
    reason: `runtime_capture_${capture.available === false ? 'unavailable' : 'not_reported'}:${nested}`,
  };
}

/** Build the office view from one well-formed envelope and its serve-time verdict. */
export function buildOfficeView(
  envelope: CatalogEnvelope,
  verdict: ServeTimeVerdict,
  nowSeconds: number,
  version: QueryContractVersion = DEFAULT_QUERY_CONTRACT_VERSION,
): OfficeView {
  const base: OfficeView = {
    connection: { state: 'answered' },
    live: false,
    notLiveReason: null,
    served: raw(envelope.served),
    envelopeProblem: envelope.problem,
    envelopeDetail: envelope.detail,
    retryAfterSeconds: envelope.retryAfterSeconds,
    serveTimeVerdict: {
      health: verdict.health === null ? null : raw(verdict.health),
      problem: verdict.problem,
    },
    times: {
      requestedAt: envelope.requestedAt,
      generatedAt: null,
      validUntil: null,
      snapshotObservedAt: null,
      evaluatedAt: nowSeconds,
    },
    registryHealth: null,
    snapshotHealth: null,
    snapshotProblem: null,
    runtimeCapture: envelope.runtimeCapture,
    minPollIntervalSeconds: null,
    freshness: null,
    coverage: coverageFor(version),
    missions: { state: 'unavailable', reason: 'no_response' },
    contractVersion: version,
    chainObservation: null,
  };

  if (envelope.response === null) {
    const reason = `no_response:${envelope.problem ?? verdict.problem ?? 'unspecified'}`;
    return { ...base, notLiveReason: reason, missions: { state: 'unavailable', reason } };
  }

  const projected = projectResponse(envelope.response, version);
  if (!projected.ok) {
    const reason = shapeReason(projected.verdict);
    return { ...base, notLiveReason: reason, missions: { state: 'unavailable', reason } };
  }
  const view: CatalogResponseView = projected.view;

  // Two INDEPENDENT freshness levels; a character may render live only when BOTH hold.
  // A fresh envelope can legitimately coexist with an unavailable runtime, and "the
  // capture did not finish" must never share an encoding with "the roster is empty".
  const envelopeLevel = envelopeFreshness(envelope, verdict, view, nowSeconds);
  const runtimeLevel = runtimeFreshness(envelope.runtimeCapture, view);
  const notLiveReason = envelopeLevel.reason ?? runtimeLevel.reason;
  const snapshotUnavailable = runtimeLevel.reason;

  const live = envelopeLevel.fresh && runtimeLevel.fresh;
  return {
    ...base,
    live,
    notLiveReason,
    freshness: { envelope: envelopeLevel, runtime: runtimeLevel },
    times: {
      requestedAt: envelope.requestedAt,
      generatedAt: view.generatedAt,
      validUntil: view.validUntil,
      snapshotObservedAt: view.runtimeSnapshot.observedAt,
      evaluatedAt: nowSeconds,
    },
    registryHealth: raw(view.registry.health),
    snapshotHealth: raw(view.runtimeSnapshot.health),
    snapshotProblem: view.runtimeSnapshot.problem,
    minPollIntervalSeconds: view.minPollIntervalSeconds,
    chainObservation:
      view.chainObservation === null
        ? null
        : {
            health: raw(view.chainObservation.health),
            problem: view.chainObservation.problem,
            observedAt: view.chainObservation.observedAt,
            maxAgeSeconds: view.chainObservation.maxAgeSeconds,
            rootCount: view.chainObservation.rootCount,
            unavailableCount: view.chainObservation.unavailableCount,
            overCapacityCount: view.chainObservation.overCapacityCount,
          },
    missions: list(
      view.missions.map((m) => presentMission(m, live, notLiveReason, snapshotUnavailable)),
    ),
  };
}
