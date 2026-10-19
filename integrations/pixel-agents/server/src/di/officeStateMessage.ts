/**
 * Serializes the server-side OfficeView into the additive `diOfficeState` ServerMessage
 * (core/asyncapi.yaml). Display-ready and fully projected: every decision (liveness,
 * observation state, which characters animate) is made HERE, so the webview renders
 * without reading stores, deriving lifecycle or parsing refs.
 */

import type {
  DiChainObservation,
  DiEvidenceLibrary,
  DiMember,
  DiMission,
  DiMissionV2,
  DiObservationState,
  DiObservedValue,
  DiOfficeState,
  DiRuntimeAgent,
  DiRuntimeAgents,
} from '../../../core/src/messages.js';
import { DI_RUNTIME_AGENTS_MAX_UNVERIFIED } from '../constants.js';
import type { UnreportedFact } from './catalogProjection.js';
import type { ReviewOffer } from './codeReview.js';
import { reviewOfferFor } from './codeReview.js';
import type {
  MemberPresentation,
  MissionPresentation,
  Observation,
  OfficeView,
  RuntimeAgentPresentation,
} from './officeView.js';
import { deriveReception } from './receptionDesk.js';

export type CharacterIdFor = (member: MemberPresentation) => number | null;
export type RuntimeCharacterIdFor = (agent: RuntimeAgentPresentation) => number | null;

function observed<T>(o: Observation<T>): DiObservedValue {
  switch (o.state) {
    case 'observed':
      return { state: 'observed', value: String(o.value), reason: null };
    case 'known_empty':
    case 'not_recorded':
      return { state: o.state, value: null, reason: null };
    case 'projected_reason':
    case 'not_applicable':
    case 'unavailable':
      return { state: o.state, value: null, reason: o.reason };
  }
}

/** A list observation as `{ state, reason, items }`; items only when observed. */
function listOf<T, U>(
  o: Observation<T[]>,
  map: (item: T) => U,
): { state: DiObservationState; reason: string | null; items: U[] } {
  if (o.state === 'observed') return { state: 'observed', reason: null, items: o.value.map(map) };
  const shown = observed(o);
  return { state: shown.state, reason: shown.reason, items: [] };
}

/** The literal "unknown" is a reported value; an absent key is a contract gap, not a value. */
function unreported(fact: UnreportedFact | undefined): DiObservedValue {
  if (!fact || fact.state === 'absent') {
    return { state: 'unavailable', value: null, reason: 'field_absent' };
  }
  if (fact.state === 'unknown_literal')
    return { state: 'observed', value: 'unknown', reason: null };
  if (fact.state === 'null') return { state: 'not_recorded', value: null, reason: null };
  return { state: 'observed', value: String(fact.value), reason: null };
}

function member(
  m: MemberPresentation,
  characterIdFor: CharacterIdFor,
  offer: ReviewOffer,
): DiMember {
  return {
    characterId: m.presence.live ? characterIdFor(m) : null,
    coverage: m.coverage,
    memberRef: m.memberRef,
    missionRef: m.missionRef,
    logical: m.logical,
    role: m.role,
    agent: m.agent,
    provider: m.provider,
    taskId: observed(m.taskObservation),
    paneId: observed(m.paneId),
    workspaceId: observed(m.workspaceId),
    sessionRecorded: observed(m.sessionRecorded),
    sessionLive: observed(m.sessionLive),
    classification: m.classification,
    reason: m.reason,
    activity: m.activity,
    live: m.presence.live,
    notLiveReason: m.presence.reason,
    // Only a LIVE member whose own reported activity is exactly `busy` animates.
    animateActive: m.presence.live && m.activityKnown && m.activity === 'busy',
    model: unreported(m.unreported.model),
    cost: unreported(m.unreported.cost),
    tokens: unreported(m.unreported.tokens),
    progress: unreported(m.unreported.progress),
    chain: m.chain,
    chainTaskId: m.chainTaskId,
    // item 3: whether a live code review is offered for this agent's assignment (decided from
    // the typed facts of the listing; the listing token is what every later read is admitted by).
    codeReview: {
      state: offer.state,
      reason: offer.reason,
      taskId: offer.taskId,
      listing: offer.listing,
    },
  };
}

/** A v1 office projects no v2 block: explicitly `projected: false`, never an empty "none". */
const V2_NOT_PROJECTED: DiMissionV2 = {
  projected: false,
  taskChains: [],
  childrenRecord: {
    state: null,
    problem: null,
    caveat: null,
    recordCount: null,
    otherParentCount: null,
  },
  missionState: {
    state: null,
    problem: null,
    detail: null,
    cursor: null,
    baseline: null,
    candidateApplicability: null,
    kinds: [],
    facts: [],
    completion: {
      state: 'not_applicable',
      reason: 'contract_v1_projects_no_mission_state',
      reportedComplete: null,
      reportedSuccess: null,
      verifiedSuccess: null,
      closureVerified: null,
      contradicted: null,
      closure: null,
      holds: [],
      holdsTotal: null,
      limitsIdentity: null,
      limitsSeparationOfDuties: null,
      limitsNonVerifyingEvidenceKinds: [],
      limitsNonVerifyingEvidenceKindsTotal: null,
    },
    provenance: {
      state: 'not_applicable',
      reason: 'contract_v1_projects_no_mission_state',
      recordSource: null,
      revision: null,
      position: null,
      freshnessBoundSeconds: null,
      journalDigest: null,
      movedDuringCollection: null,
      collectionStatus: null,
      collectedAtPosition: null,
      headPosition: null,
      cursorPosition: null,
      drift: [],
      driftTotal: null,
      sources: [],
    },
  },
  canonicalReview: {
    state: null,
    problem: null,
    gate: null,
    verdict: null,
    currency: null,
    integrity: null,
    taskStatus: null,
    candidateApplicability: null,
    round: null,
    reviewerLogical: null,
    reviewerAgent: null,
    protocolToken: null,
  },
};

function missionV2(m: MissionPresentation): DiMissionV2 {
  if (m.v2 === null) return V2_NOT_PROJECTED;
  return {
    projected: true,
    taskChains: m.v2.taskChains.map((c) => ({ ...c, declaredAgents: [...c.declaredAgents] })),
    childrenRecord: { ...m.v2.childrenRecord },
    missionState: {
      ...m.v2.missionState,
      kinds: m.v2.missionState.kinds.map((k) => ({ ...k })),
      facts: m.v2.missionState.facts.map((f) => ({
        ...f,
        entries: f.entries.map((e) => ({ ...e })),
      })),
      completion: {
        ...m.v2.missionState.completion,
        holds: [...m.v2.missionState.completion.holds],
        limitsNonVerifyingEvidenceKinds: [
          ...m.v2.missionState.completion.limitsNonVerifyingEvidenceKinds,
        ],
      },
      provenance: {
        ...m.v2.missionState.provenance,
        drift: [...m.v2.missionState.provenance.drift],
        sources: m.v2.missionState.provenance.sources.map((x) => ({ ...x })),
      },
    },
    canonicalReview: { ...m.v2.canonicalReview },
  };
}

/** item 2: the evidence library on the wire, exactly as presented (copied, never shared). */
function evidenceLibrary(m: MissionPresentation): DiEvidenceLibrary | null {
  const e = m.evidence;
  if (e === null) return null;
  return {
    cursor: e.cursor,
    currency: e.currency,
    currencyReason: e.currencyReason,
    execution: { ...e.execution },
    contentReads: { ...e.contentReads },
    artifacts: {
      ...e.artifacts,
      items: e.artifacts.items.map((a) => ({ ...a, content: { ...a.content } })),
    },
    verification: {
      ...e.verification,
      items: e.verification.items.map((r) => ({ ...r, artifactIds: [...r.artifactIds] })),
    },
    reviews: {
      ...e.reviews,
      items: e.reviews.items.map((r) => ({
        ...r,
        findings: { ...r.findings, items: r.findings.items.map((f) => ({ ...f })) },
      })),
    },
    receipts: { ...e.receipts, items: e.receipts.items.map((r) => ({ ...r })) },
  };
}

function chainObservation(view: OfficeView): DiChainObservation {
  const c = view.chainObservation;
  return c === null
    ? {
        projected: false,
        health: null,
        problem: null,
        observedAt: null,
        maxAgeSeconds: null,
        rootCount: null,
        unavailableCount: null,
        overCapacityCount: null,
      }
    : { projected: true, ...c };
}

function mission(m: MissionPresentation, characterIdFor: CharacterIdFor): DiMission {
  const members = listOf(m.members, (x) =>
    member(x, characterIdFor, reviewOfferFor(m, x)),
  );
  return {
    missionRef: m.missionRef,
    sourceId: m.sourceId,
    nativeId: m.nativeId,
    sourceKind: m.sourceKind,
    lifecycleModel: m.lifecycleModel,
    lifecycleState: m.lifecycleState,
    standing: m.standing,
    bindingState: m.bindingState,
    bindingReason: m.bindingReason,
    producerDeclared: m.producerDeclared,
    live: m.presence.live,
    notLiveReason: m.presence.reason,
    objective: observed(m.objective),
    updatedAt: observed(m.updatedAt),
    identitySource: observed(m.identitySource),
    paused: observed(m.paused),
    cancelRequested: observed(m.cancelRequested),
    ambiguity: observed(m.ambiguity),
    members: { state: members.state, reason: members.reason, members: members.items },
    v2: missionV2(m),
    ...(m.evidence === null ? {} : { evidence: evidenceLibrary(m)! }),
    ...(m.herdrTask === null
      ? {}
      : { herdrTask: { ...m.herdrTask, rootAliases: [...m.herdrTask.rootAliases] } }),
  };
}

function runtimeAgent(
  a: RuntimeAgentPresentation,
  characterIdFor: RuntimeCharacterIdFor,
): DiRuntimeAgent {
  return {
    agentRef: a.agentRef,
    characterId: a.presence.live ? characterIdFor(a) : null,
    sourceId: a.sourceId,
    rootRef: a.rootRef,
    rootAliases: [...a.rootAliases],
    logical: a.logical,
    role: a.role,
    agent: a.agent,
    provider: a.provider,
    session: a.session,
    paneId: a.paneId,
    workspaceId: a.workspaceId,
    activity: a.activity,
    identityState: a.identityState,
    identityReason: a.identityReason,
    identityVerified: a.identityVerified,
    assignmentState: a.assignmentState,
    assignmentReason: a.assignmentReason,
    taskRef: a.taskRef,
    taskId: a.taskId,
    taskStatus: a.taskStatus,
    live: a.presence.live,
    notLiveReason: a.presence.reason,
    plainText: a.plainText,
  };
}

/**
 * item 07: every VERIFIED runtime agent (each may be a drawn character), then the others up to the
 * cap; `agentsTotal` is the real count, so a capped list never reads as complete.
 */
function runtimeAgents(view: OfficeView, characterIdFor: RuntimeCharacterIdFor): DiRuntimeAgents {
  const r = view.runtimeAgents;
  const verified = r.agents.filter((a) => a.identityVerified);
  const others = r.agents.filter((a) => !a.identityVerified);
  const shown = [...verified, ...others.slice(0, DI_RUNTIME_AGENTS_MAX_UNVERIFIED)];
  return {
    reported: r.reported,
    state: r.state,
    problem: r.problem,
    plainStatement: r.plainStatement,
    caveat: r.caveat,
    observedAt: r.observedAt,
    firstPassAt: r.firstPassAt,
    secondPassAt: r.secondPassAt,
    counts:
      r.counts === null ? [] : Object.entries(r.counts).map(([name, value]) => ({ name, value })),
    agents: shown.map((a) => runtimeAgent(a, characterIdFor)),
    agentsTotal: r.agents.length,
    agentsCap: verified.length + DI_RUNTIME_AGENTS_MAX_UNVERIFIED,
    agentsOverBound: r.agentsOverBound,
    capture: { ...r.capture },
    locators:
      r.locators === null
        ? null
        : {
            ...r.locators,
            excludedByReason: r.locators.excludedByReason.map((e) => ({
              name: e.reason,
              value: e.count,
            })),
          },
  };
}

export function buildDiOfficeStateMessage(
  view: OfficeView,
  characterIdFor: CharacterIdFor,
  runtimeCharacterIdFor: RuntimeCharacterIdFor = () => null,
): DiOfficeState {
  const missions = listOf(view.missions, (m) => mission(m, characterIdFor));
  return {
    type: 'diOfficeState',
    live: view.live,
    notLiveReason: view.notLiveReason,
    connection:
      view.connection.state === 'answered'
        ? { state: 'observed', value: 'answered', reason: null }
        : { state: 'unavailable', value: null, reason: view.connection.reason },
    envelope: {
      fresh: view.freshness?.envelope.fresh ?? false,
      reason: view.freshness?.envelope.reason ?? view.notLiveReason,
      served: view.served,
      verdictHealth: view.serveTimeVerdict?.health ?? null,
      verdictProblem: view.serveTimeVerdict?.problem ?? null,
      envelopeProblem: view.envelopeProblem,
      envelopeDetail: view.envelopeDetail,
      retryAfterSeconds: view.retryAfterSeconds,
      registryHealth: view.registryHealth,
    },
    runtime: {
      fresh: view.freshness?.runtime.fresh ?? false,
      reason: view.freshness?.runtime.reason ?? view.notLiveReason,
      configured: view.runtimeCapture?.configured ?? null,
      available: view.runtimeCapture?.available ?? null,
      captureProblem: view.runtimeCapture?.problem ?? null,
      snapshotHealth: view.snapshotHealth,
      snapshotProblem: view.snapshotProblem,
    },
    times: { ...view.times },
    coverage: { ...view.coverage },
    missions: { state: missions.state, reason: missions.reason, missions: missions.items },
    contractVersion: view.contractVersion,
    chainObservation: chainObservation(view),
    reception: deriveReception(view),
    runtimeAgents: runtimeAgents(view, runtimeCharacterIdFor),
  };
}
