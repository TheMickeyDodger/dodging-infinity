/**
 * Item 2 (original 10) — the EVIDENCE LIBRARY of one source-qualified Mission, derived
 * SERVER-SIDE as a pure function of what the catalog already projected. Four DISTINCT types,
 * never merged:
 *
 * - artifacts:     every recorded Mission Core artifact that is NOT a delivery receipt, with its
 *                  role (PRODUCED / ORIGINAL_INPUT / VERIFICATION), locator KIND (the report
 *                  carries no locator), digest and Mission Core's own candidate comparison;
 * - verification:  the recorded evidence records (submitted / ACCEPTED / invalidated, verifying
 *                  kind, freshness, whether the present claim rests on it) and the artifacts each
 *                  record ITSELF references — a recorded link, never inferred from names;
 * - reviews:       the admitted canonical review preamble (round, reviewer, token, gate, verdict,
 *                  currency): linkage-corroborated, NOT digest-anchored, candidate applicability
 *                  unproven — and (item 2 E2) the reviewer-DECLARED public findings the LOCAL
 *                  review gate validated and persisted from the routed reviewer's explicit
 *                  findings envelope: not_reported / not_recorded / empty / present / refused /
 *                  unexpected, never collapsed. The transcript is never read or relayed;
 * - receipts:      delivery-receipt references: ATTESTED (the delivery layer's validated marker)
 *                  or an unattested, unverified claim. Never a success that was not recorded.
 *
 * Every section resolves to exactly one of: observed, known_empty, not_recorded (the producer
 * reported nothing), not_applicable (unsupported for this source kind), unavailable (could not be
 * read, with the reason) or refused (the producer refused the record, with its problem). STALE is
 * the library's `currency`, kept apart: a listing from an answer that is no longer live is shown
 * and labelled stale, and content reads close. Nothing here reads a file or resolves anything.
 */

import type {
  ArtifactItemView,
  CanonicalReviewView,
  EvidenceItemView,
  ExecutionIdentityView,
  MissionStateView,
  MissionV2View,
  ReportFact,
  ReviewFindingsView,
  TypedList,
} from './catalogProjection.js';

export type EvidenceSectionState =
  | 'observed'
  | 'known_empty'
  | 'not_recorded'
  | 'projected_reason'
  | 'not_applicable'
  | 'unavailable'
  | 'refused';

export interface EvidenceSection<T> {
  state: EvidenceSectionState;
  reason: string | null;
  /** The real number of this type's records; null when it is not known exactly. */
  total: number | null;
  items: T[];
}

/**
 * Whether the office may ASK the backend for this artifact's content. `requestable` is never a
 * claim that the content is safe or will be shown: the backend decides containment, disclosure
 * class, digest and binding at read time, and may refuse.
 */
export interface EvidenceContentRequest {
  state: 'requestable' | 'not_requestable';
  reason: string | null;
}

export interface EvidenceArtifact {
  artifactId: string;
  key: string | null;
  role: string;
  locatorKind: string;
  contentDigest: string | null;
  availableReported: boolean | null;
  recordedAt: number | null;
  /** Mission Core's own comparison with an observed candidate report; never inferred. */
  candidate: 'matches_observed' | 'differs_from_observed' | 'not_observed';
  content: EvidenceContentRequest;
}

export interface EvidenceRecord {
  evidenceId: string;
  requirementKey: string | null;
  kind: string | null;
  standing: string | null;
  freshness: string | null;
  accepted: boolean | null;
  invalidated: boolean | null;
  verifyingKind: boolean | null;
  bearsOnClaim: boolean | null;
  drifted: boolean | null;
  unconfirmed: boolean | null;
  /** The record's own links, in order, capped (see `artifactIdsTotal`). */
  artifactIds: string[];
  /** The record's TRUE link count: above `artifactIds.length` exactly when the cap applied. */
  artifactIdsTotal: number;
}

/**
 * The reviewer-DECLARED public findings of a round, exactly as projected (`ReviewFindingsView`):
 * model-authored text, never a verdict or an approval; the provenance ceiling travels with it.
 */
export type EvidenceFindings = ReviewFindingsView;

export interface EvidenceReview {
  findings: EvidenceFindings;
  round: number | null;
  reviewerLogical: string | null;
  reviewerAgent: string | null;
  protocolToken: string | null;
  gate: string | null;
  verdict: string | null;
  currency: string | null;
  integrity: string | null;
  candidateApplicability: string | null;
}

export interface EvidenceReceipt {
  artifactId: string;
  attested: boolean;
  deliveryId: string | null;
  step: string | null;
  receiptState: string | null;
  stepState: string | null;
  effectCompleted: boolean | null;
  contentDigest: string | null;
  recordedAt: number | null;
}

/**
 * Item 2 (completed-Mission artifacts): the EXECUTION a content read would stand on, kept apart
 * from the listing's freshness (`currency`). `active_binding`: a currently bound Mission.
 * `historical_completed_execution`: a COMPLETED Mission (roster `not_applicable /
 * mission_not_active`) whose execution identity the backend ESTABLISHED from its canonical record
 * — historical recorded execution, NOT a live team, never whole-candidate applicability. `none`:
 * neither (reads closed; the identity's state and code say why for a completed Mission).
 */
export interface EvidenceExecution {
  basis: 'active_binding' | 'historical_completed_execution' | 'none';
  identityState: ExecutionIdentityView['state'];
  identityProblem: string | null;
  /** The record's own lifecycle literal, display only (never switched on). */
  lifecycleState: string | null;
  /** The task the record's receipt names (display only). */
  taskId: string | null;
}

export interface EvidenceLibraryPresentation {
  /**
   * The cursor this listing was observed at; a content request must echo it. A bound Mission's
   * journal digest; a historical read's cursor binds the journal head AND the execution identity.
   */
  cursor: string | null;
  execution: EvidenceExecution;
  currency: 'current' | 'stale';
  currencyReason: string | null;
  /** Mission-level gate on content reads (the backend re-checks all of it, and more). */
  contentReads: { state: 'available' | 'unavailable'; reason: string | null };
  artifacts: EvidenceSection<EvidenceArtifact>;
  verification: EvidenceSection<EvidenceRecord>;
  reviews: EvidenceSection<EvidenceReview>;
  receipts: EvidenceSection<EvidenceReceipt>;
}

const RECEIPT_LOCATOR_KIND = 'DELIVERY_RECEIPT_REFERENCE';
const READABLE_LOCATOR_KIND = 'REPOSITORY_PATH';

function section<T>(
  state: EvidenceSectionState,
  reason: string | null,
  total: number | null = null,
  items: T[] = [],
): EvidenceSection<T> {
  return { state, reason, total, items };
}

/** The Mission State gate shared by the three report-derived sections, or null when open. */
function missionStateGate(ms: MissionStateView): EvidenceSection<never> | null {
  if (ms.state === 'observed' && ms.report !== null) return null;
  if (ms.state === 'not_applicable') {
    return section('not_applicable', ms.problem ?? 'mission_state_not_applicable');
  }
  if (ms.state === 'observed') return section('unavailable', 'mission_state_report_absent');
  return section('unavailable', ms.problem ?? 'mission_state_not_observed');
}

/** One type's records out of a typed report list, never collapsing its states. */
function listSection<T, U>(
  fact: ReportFact<TypedList<T>>,
  belongs: (item: T) => boolean,
  present: (item: T) => U,
): EvidenceSection<U> {
  if (fact.shape === 'unexpected') return section('unavailable', 'report_fact_shape_unexpected');
  if (fact.value === null) return section('not_recorded', null);
  const shown = fact.value.items.filter(belongs).map(present);
  const exact = fact.value.total === fact.value.items.length;
  if (!exact && shown.length === 0) {
    return section('projected_reason', 'beyond_the_listing_cap');
  }
  if (shown.length === 0) return section('known_empty', null, 0);
  return section('observed', null, exact ? shown.length : null, shown);
}

const isReceipt = (a: ArtifactItemView): boolean =>
  a.locatorKind === RECEIPT_LOCATOR_KIND || a.attested;

function contentRequest(a: ArtifactItemView): EvidenceContentRequest {
  if (a.locatorKind !== READABLE_LOCATOR_KIND) {
    return { state: 'not_requestable', reason: 'locator_kind_has_no_content' };
  }
  if (a.availableReported !== true) {
    return { state: 'not_requestable', reason: 'artifact_recorded_unavailable' };
  }
  if (a.contentDigest === null) {
    return { state: 'not_requestable', reason: 'artifact_has_no_recorded_digest' };
  }
  return { state: 'requestable', reason: null };
}

function artifact(a: ArtifactItemView): EvidenceArtifact {
  return {
    artifactId: a.artifactId,
    key: a.key,
    role: a.role,
    locatorKind: a.locatorKind,
    contentDigest: a.contentDigest,
    availableReported: a.availableReported,
    recordedAt: a.recordedAt,
    candidate:
      a.matchesObserved === true
        ? 'matches_observed'
        : a.matchesObserved === false
          ? 'differs_from_observed'
          : 'not_observed',
    content: contentRequest(a),
  };
}

function receipt(a: ArtifactItemView): EvidenceReceipt {
  const r = a.attested ? a.receipt : null;
  return {
    artifactId: a.artifactId,
    attested: a.attested,
    deliveryId: r?.deliveryId ?? null,
    step: r?.step ?? null,
    receiptState: r?.receiptState ?? null,
    stepState: r?.stepState ?? null,
    effectCompleted: r?.effectCompleted ?? null,
    contentDigest: a.contentDigest,
    recordedAt: a.recordedAt,
  };
}

function record(e: EvidenceItemView): EvidenceRecord {
  return {
    evidenceId: e.evidenceId,
    requirementKey: e.requirementKey,
    kind: e.kind,
    standing: e.standing,
    freshness: e.freshness,
    accepted: e.accepted,
    invalidated: e.invalidated,
    verifyingKind: e.verifyingKind,
    bearsOnClaim: e.bearsOnClaim,
    drifted: e.drifted,
    unconfirmed: e.unconfirmed,
    artifactIds: [...e.artifactIds],
    artifactIdsTotal: e.artifactIdsTotal,
  };
}

function reviews(review: CanonicalReviewView): EvidenceSection<EvidenceReview> {
  switch (review.state) {
    case 'admitted':
      if (review.record === null) return section('unavailable', 'review_record_absent');
      return section('observed', null, 1, [
        {
          round: review.record.round,
          reviewerLogical: review.record.reviewerLogical,
          reviewerAgent: review.record.reviewerAgent,
          protocolToken: review.record.protocolToken,
          gate: review.gate,
          verdict: review.verdict,
          currency: review.currency,
          integrity: review.integrity,
          candidateApplicability: review.candidateApplicability,
          findings: { ...review.findings, items: review.findings.items.map((f) => ({ ...f })) },
        },
      ]);
    case 'not_recorded':
      return section('not_recorded', null);
    case 'not_applicable':
      return section('not_applicable', review.problem ?? 'review_not_applicable');
    case 'refused':
      return section('refused', review.problem ?? 'review_refused');
    case 'unavailable':
      return section('unavailable', review.problem ?? 'review_unavailable');
    default:
      return section('unavailable', 'review_state_unrecognized');
  }
}

export interface EvidenceLibraryInput {
  sourceKind: string;
  bindingState: string;
  bindingReason: string | null;
  lifecycleState: string | null;
  taskId: string | null;
  officeLive: boolean;
  officeReason: string | null;
  v2: MissionV2View | null;
}

/** The library of one Mission; null for a contract v1 office (nothing to project). */
export function presentEvidenceLibrary(
  input: EvidenceLibraryInput,
): EvidenceLibraryPresentation | null {
  const v2 = input.v2;
  if (v2 === null) return null;
  const ms = v2.missionState;
  const missionCore = input.sourceKind === 'mission_core';
  const gate = missionCore
    ? missionStateGate(ms)
    : section<never>('not_applicable', 'source_kind_has_no_mission_core_evidence');
  const report = gate === null ? ms.report : null;
  const identity = ms.executionIdentity;
  const active = input.bindingState === 'bound';
  // The roster's own classification of an inactive Mission, and the backend's ESTABLISHED
  // identity (emitted only for a COMPLETED record): never a switch on the lifecycle literal.
  const inactive =
    input.bindingState === 'not_applicable' && input.bindingReason === 'mission_not_active';
  const historical = missionCore && !active && inactive && identity.state === 'established';
  const journal = report?.provenance?.journalDigest ?? null;
  const cursor = historical ? identity.cursorSha256 : journal;
  const unbound =
    !inactive || identity.state === 'not_applicable'
      ? 'mission_binding_not_bound'
      : identity.state === 'not_reported'
        ? 'historical_identity_not_reported'
        : identity.state === 'unexpected'
          ? 'historical_identity_unexpected'
          : 'historical_identity_not_established';
  const contentReads = !missionCore
    ? 'source_kind_has_no_artifact_content'
    : !input.officeLive
      ? 'office_not_live'
      : report === null
        ? 'mission_state_not_observed'
        : !active && !historical
          ? unbound
          : cursor === null
            ? 'journal_cursor_not_reported'
            : null;
  return {
    cursor,
    execution: {
      basis: active ? 'active_binding' : historical ? 'historical_completed_execution' : 'none',
      identityState: identity.state,
      identityProblem: identity.problem,
      lifecycleState: input.lifecycleState,
      taskId: input.taskId,
    },
    currency: input.officeLive ? 'current' : 'stale',
    currencyReason: input.officeLive ? null : (input.officeReason ?? 'office_not_live'),
    contentReads:
      contentReads === null
        ? { state: 'available', reason: null }
        : { state: 'unavailable', reason: contentReads },
    artifacts:
      report === null
        ? { ...gate! }
        : listSection(report.artifactItems, (a) => !isReceipt(a), artifact),
    verification:
      report === null ? { ...gate! } : listSection(report.evidenceItems, () => true, record),
    reviews: reviews(v2.canonicalReview),
    receipts:
      report === null ? { ...gate! } : listSection(report.artifactItems, isReceipt, receipt),
  };
}
