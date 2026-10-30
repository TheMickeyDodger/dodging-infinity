/**
 * Typed, read-only projection of one DI catalog answer (CONTRACT-v3 key sets, verbatim).
 *
 * Strict where the contract is fixed, permissive where it is open:
 * - The nine-key envelope and the eleven-key response are FIXED key sets (the backend's
 *   own validator enforces exact equality). A different set is `malformed /
 *   response_shape_invalid`; an unrecognized contract or version is `incompatible /
 *   response_contract_unsupported`. Both are first-class NOT-LIVE states — never
 *   best-effort parsed.
 * - Body keys are always PRESENT; a value may be JSON null. Key presence is structure,
 *   never a signal: a null is "not recorded", a missing required key is malformed.
 * - Enumerated values are classified, not trusted: an unrecognized value is kept as
 *   `{ known: false, raw }` and renders as unrecognized, never as a known state.
 * - `lifecycle.state` is the source's native literal, kept verbatim (case preserved),
 *   and never switched on.
 * - `native` and `producer.recorded` differ BY SOURCE KIND; each is modelled per kind.
 * - `runtime_capture` is diagnostics: parsed permissively, never required, never gating.
 * - Member `model` / `cost` / `tokens` / `progress` are the LITERAL string "unknown",
 *   which is distinct from an absent key and from null.
 */

import type {
  Activity,
  BindingReason,
  BindingState,
  ChainBindingReason,
  ChainKind,
  Classified,
  Health,
  LifecycleModel,
  MemberClassification,
  MemberReason,
  MissionDeliveryLinkBasis,
  MissionDeliveryLinkState,
  QueryContractVersion,
  RuntimeAgentsState,
  RuntimeAssignmentState,
  RuntimeIdentityState,
  Served,
  Standing,
  WorkflowPhase,
} from './catalogContract.js';
import {
  ACTIVITIES,
  BINDING_REASONS,
  BINDING_STATES,
  CHAIN_BINDING_REASONS,
  CHAIN_KINDS,
  classify,
  DEFAULT_QUERY_CONTRACT_VERSION,
  DELIVERY_AUTHORIZATION_CHECKS,
  DELIVERY_BINDING_MISSION_KEYS,
  DELIVERY_COVERAGE_DIRECT_CLI,
  DELIVERY_COVERAGE_KEYS,
  DELIVERY_COVERAGE_ROUTE,
  DELIVERY_ENTRY_STATES,
  DELIVERY_LINK_KEYS,
  DELIVERY_LINK_STATES,
  DELIVERY_MODES,
  DELIVERY_PRESENTATION_ENTRY_KEYS,
  DELIVERY_PRESENTATIONS_KEYS,
  DELIVERY_PRESENTATIONS_PROBLEMS,
  DELIVERY_PRESENTATIONS_SOURCE_ID,
  DELIVERY_PRESENTATIONS_STATES,
  DELIVERY_STORE_STATES,
  ENDPOINT,
  ENVELOPE_KEYS,
  HEALTH_STATES,
  HERDR_TASK_MODEL,
  LIFECYCLE_MODELS,
  LOCATOR_ACCOUNT_KEYS,
  MAX_DELIVERY_PRESENTATIONS,
  MEMBER_CLASSIFICATIONS,
  MEMBER_REASONS,
  MISSION_DELIVERY_ID,
  MISSION_DELIVERY_LINK_BASES,
  MISSION_DELIVERY_LINK_KEYS,
  MISSION_DELIVERY_LINK_STATES,
  QUERY_CONTRACT,
  RESPONSE_KEYS,
  RESPONSE_KEYS_V2,
  RUNTIME_AGENT_KEYS,
  RUNTIME_AGENTS_CAVEAT,
  RUNTIME_AGENTS_COUNT_KEYS,
  RUNTIME_AGENTS_KEYS,
  RUNTIME_AGENTS_STATES,
  RUNTIME_ASSIGNMENT_STATES,
  RUNTIME_IDENTITY_STATES,
  SERVED_VALUES,
  STANDINGS,
  SUPPORTED_ENDPOINT_VERSIONS,
  SUPPORTED_QUERY_CONTRACT_VERSIONS,
  UNKNOWN_LITERAL,
  UNREPORTED_FACTS,
  WORKFLOW_PHASE_KEYS,
  WORKFLOW_PHASES,
} from './catalogContract.js';

/** A not-live reason expressed in the backend's own health vocabulary. */
export interface ShapeVerdict {
  health: 'malformed' | 'incompatible';
  problem: 'response_shape_invalid' | 'response_contract_unsupported';
  detail: string;
}

type Json = Record<string, unknown>;

class ShapeError extends Error {
  constructor(readonly verdict: ShapeVerdict) {
    super(verdict.detail);
  }
}

function malformed(detail: string): never {
  throw new ShapeError({ health: 'malformed', problem: 'response_shape_invalid', detail });
}

function isObject(value: unknown): value is Json {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function obj(value: unknown, where: string): Json {
  if (!isObject(value)) malformed(`${where} is not an object`);
  return value;
}

/** Required key present (value may be null). */
function key(o: Json, name: string, where: string): unknown {
  if (!(name in o)) malformed(`${where}.${name} is missing`);
  return o[name];
}

function int(value: unknown, where: string): number {
  if (typeof value !== 'number' || !Number.isInteger(value) || value < 0) {
    malformed(`${where} is not a non-negative integer`);
  }
  return value;
}

function intOrNull(value: unknown, where: string): number | null {
  return value === null ? null : int(value, where);
}

function str(value: unknown, where: string): string {
  if (typeof value !== 'string') malformed(`${where} is not a string`);
  return value;
}

function strOrNull(value: unknown, where: string): string | null {
  return value === null ? null : str(value, where);
}

function bool(value: unknown, where: string): boolean {
  if (typeof value !== 'boolean') malformed(`${where} is not a boolean`);
  return value;
}

function exactKeys(o: Json, expected: readonly string[], where: string): void {
  const actual = Object.keys(o).sort();
  const wanted = [...expected].sort();
  if (actual.length !== wanted.length || actual.some((k, i) => k !== wanted[i])) {
    malformed(`${where} key set differs from the contract`);
  }
}

// ── Envelope ────────────────────────────────────────────────────

/** `runtime_capture`, read permissively as diagnostics. Never gates rendering. */
export interface RuntimeCaptureDiagnostics {
  configured: boolean | null;
  available: boolean | null;
  problem: string | null;
  detail: string | null;
  observedAt: number | null;
  /**
   * item 07: the capture's ROOT ACCOUNT as counts (requested, captured, unavailable, over its
   * fixed capacity — reported, never silently truncated); null when the capture reported none.
   */
  roots: {
    discovered: number | null;
    captured: number | null;
    unavailable: number | null;
    overCapacity: number | null;
  } | null;
  /** item 07: a task-native root observed ONLY so a listing exists (never a membership). */
  listingAnchor: boolean | null;
}

export function readRuntimeCapture(value: unknown): RuntimeCaptureDiagnostics {
  const o = isObject(value) ? value : {};
  const b = (v: unknown): boolean | null => (typeof v === 'boolean' ? v : null);
  const s = (v: unknown): string | null => (typeof v === 'string' ? v : null);
  const n = (v: unknown): number | null =>
    typeof v === 'number' && Number.isInteger(v) && v >= 0 ? v : null;
  const len = (v: unknown): number | null => (Array.isArray(v) ? v.length : null);
  const roots = isObject(o.roots) ? o.roots : null;
  const discovery = isObject(o.discovery) ? o.discovery : null;
  return {
    configured: b(o.configured),
    available: b(o.available),
    problem: s(o.problem),
    detail: s(o.detail),
    observedAt: n(o.observed_at),
    roots:
      roots === null
        ? null
        : {
            discovered: len(roots.discovered),
            captured: len(roots.captured),
            unavailable: len(roots.unavailable),
            overCapacity: len(roots.over_capacity),
          },
    listingAnchor:
      discovery === null || !('listing_anchor' in discovery)
        ? null
        : typeof discovery.listing_anchor === 'string',
  };
}

export interface CatalogEnvelope {
  /** Envelope clock when the backend's refresh began. Never restamped here. */
  requestedAt: number;
  served: Classified<Served>;
  problem: string | null;
  detail: string | null;
  retryAfterSeconds: number | null;
  /** The raw response object (project with projectResponse), or null. */
  response: Json | null;
  runtimeCapture: RuntimeCaptureDiagnostics;
}

export type EnvelopeResult =
  { ok: true; envelope: CatalogEnvelope } | { ok: false; verdict: ShapeVerdict };

export function parseEnvelope(body: unknown): EnvelopeResult {
  try {
    const o = obj(body, 'envelope');
    exactKeys(o, ENVELOPE_KEYS, 'envelope');
    if (
      o.endpoint !== ENDPOINT ||
      typeof o.endpoint_version !== 'number' ||
      !SUPPORTED_ENDPOINT_VERSIONS.has(o.endpoint_version)
    ) {
      throw new ShapeError({
        health: 'incompatible',
        problem: 'response_contract_unsupported',
        detail: 'envelope endpoint or endpoint_version is not the supported one',
      });
    }
    const retry = o.retry_after_seconds;
    if (retry !== null && (typeof retry !== 'number' || !Number.isFinite(retry) || retry < 0)) {
      malformed('envelope.retry_after_seconds is not a non-negative number or null');
    }
    if (o.response !== null && !isObject(o.response))
      malformed('envelope.response is not an object or null');
    return {
      ok: true,
      envelope: {
        requestedAt: int(o.requested_at, 'envelope.requested_at'),
        served: classify(SERVED_VALUES, o.served),
        problem: strOrNull(o.problem, 'envelope.problem'),
        detail: strOrNull(o.detail, 'envelope.detail'),
        retryAfterSeconds: retry as number | null,
        response: o.response as Json | null,
        runtimeCapture: readRuntimeCapture(o.runtime_capture),
      },
    };
  } catch (err) {
    if (err instanceof ShapeError) return { ok: false, verdict: err.verdict };
    throw err;
  }
}

// ── Response ────────────────────────────────────────────────────

/** A member fact the backend never reports, kept distinct from absence and null. */
export type UnreportedFact =
  | { state: 'unknown_literal' }
  | { state: 'absent' }
  | { state: 'null' }
  | { state: 'reported'; value: unknown };

export function readUnreportedFact(o: Json, name: string): UnreportedFact {
  if (!(name in o)) return { state: 'absent' };
  const value = o[name];
  if (value === UNKNOWN_LITERAL) return { state: 'unknown_literal' };
  if (value === null) return { state: 'null' };
  return { state: 'reported', value };
}

export interface MemberView {
  memberRef: string;
  missionRef: string;
  taskId: string | null;
  logical: string;
  role: string;
  agent: string;
  provider: string;
  paneId: string | null;
  workspaceId: string | null;
  session: { recorded: string | null; live: string | null };
  classification: Classified<MemberClassification>;
  reason: Classified<MemberReason>;
  activity: Classified<Activity>;
  observedAt: number | null;
  unreported: Record<(typeof UNREPORTED_FACTS)[number], UnreportedFact>;
  /** contract v2: the member's chain (`direct` | `child`); every v1 member is direct. */
  chain: Classified<ChainKind>;
  /** contract v2: the task of the member's chain (null in v1). */
  chainTaskId: string | null;
}

/** contract v2 — one task chain (depth 1), verbatim. */
export interface TaskChainView {
  chain: Classified<ChainKind>;
  chainTaskId: string | null;
  parentTaskId: string | null;
  binding: {
    state: Classified<BindingState>;
    reason: Classified<ChainBindingReason>;
    taskId: string | null;
  };
  root: { observation: string; path: string | null; problem: string | null };
  membershipEnd: { ended: boolean | null; rule: string; status: string | null };
  dependency: { recorded: boolean; resolution: string; rule: string } | null;
  /** A child root's own children, never captured: a DEPTH LIMIT (null = not recorded). */
  descendantsNotFollowed: number | null;
  record: {
    childTaskId: string;
    repo: string;
    workspaceId: string;
    declaredAgents: Array<{ logical: string; agent: string }>;
  } | null;
}

/** contract v2 — the children record (four outcomes, never collapsed). */
export interface ChildrenRecordView {
  state: string;
  problem: string | null;
  caveat: string | null;
  recordCount: number | null;
  otherParentCount: number | null;
}

/**
 * A bounded list read from the verbatim report. `known: false` = the field was not a list
 * (UNKNOWN — never "none", never 0). When known, `total` is the REAL length and `items` the
 * displayed prefix: `total - items.length` entries were OMITTED by the display cap, so a
 * capped list never reads as complete.
 */
export interface BoundedList {
  known: boolean;
  total: number | null;
  items: string[];
}

/**
 * One canonical Mission State fact as Mission Core reports it: its standing, source, freshness,
 * detail, revision and position, and its value (typed per fact).
 * - `value === null` (`shape: 'ok'`): the fact reports a null value — for proof, readiness,
 *   dependencies and budget that is NO ACTIVATION CONTRACT (not applicable), never "none";
 * - `shape: 'unexpected'`: not the emitted envelope shape (rendered unavailable, never guessed).
 */
export interface ReportFact<T> {
  shape: 'ok' | 'unexpected';
  standing: string | null;
  source: string | null;
  freshness: string | null;
  detail: string | null;
  observedAt: number | null;
  revision: number | null;
  position: number | null;
  value: T | null;
}

/**
 * The ALLOWLISTED canonical facts of Mission Core's own report (G3), typed. Nothing else of
 * the report is relayed; evidence and artifact entries are RECORDED REFERENCES, displayed as
 * bounded text and never dereferenced. A count of a non-list is null (UNKNOWN), never 0.
 */
export interface MissionReportView {
  revision: number | null;
  observedAt: number | null;
  blockers: ReportFact<{ active: BoundedList; hardActive: boolean | null }>;
  pendingDecisions: ReportFact<{
    awaitingDecision: boolean | null;
    liveAuthorization: boolean | null;
    nextPermittedStep: string | null;
    revision: number | null;
  }>;
  review: ReportFact<string>;
  task: ReportFact<string>;
  candidate: ReportFact<{ artifactDigests: BoundedList; baselineDigest: string | null }>;
  /** reported_* and verified_* are DIFFERENT facts, kept apart. */
  completion: {
    reportedComplete: boolean | null;
    reportedSuccess: boolean | null;
    verifiedSuccess: boolean | null;
    closureVerified: boolean | null;
    contradicted: boolean | null;
    closure: string | null;
    holds: BoundedList;
    limits: {
      identity: string | null;
      separationOfDuties: string | null;
      nonVerifyingEvidenceKinds: BoundedList;
    } | null;
  } | null;
  /** Where the report came from and whether the journal moved while it was collected. */
  provenance: {
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
    drift: BoundedList;
    sources: Array<{
      kind: string;
      source: string | null;
      standing: string | null;
      observedAt: number | null;
      detail: string | null;
    }>;
  } | null;
  delivery: ReportFact<string>;
  deliveryReceipts: ReportFact<{
    recorded: number | null;
    attested: number | null;
    unattested: number | null;
    effectsCompleted: number | null;
    reportBoundTo: string | null;
    reportBoundAttested: string | null;
    reportFresh: string | null;
  }>;
  proof: ReportFact<{ satisfied: boolean | null; requirements: BoundedList }>;
  readiness: ReportFact<{ satisfied: boolean | null }>;
  dependencies: ReportFact<{ satisfied: boolean | null }>;
  budget: ReportFact<{ attemptsRemaining: number | null; checkpointsRemaining: number | null }>;
  evidence: ReportFact<BoundedList>;
  artifacts: ReportFact<BoundedList>;
  /**
   * item 2 (evidence library): the SAME two recorded collections, TYPED, so nothing is ever
   * parsed back out of display text. STRICT: one malformed item makes the whole fact
   * `unexpected` (rendered unavailable, never guessed). The display cap applies; `total` is the
   * real length.
   */
  artifactItems: ReportFact<TypedList<ArtifactItemView>>;
  evidenceItems: ReportFact<TypedList<EvidenceItemView>>;
}

/** A list read item by item; `items` is the shown prefix, `total` the real length. */
export interface TypedList<T> {
  total: number;
  items: T[];
}

/**
 * One RECORDED Mission Core artifact as the report states it. Metadata only: the report carries
 * no locator, and nothing here dereferences anything. `receipt` is the delivery layer's attested
 * marker, present only on an attested receipt artifact.
 */
export interface ArtifactItemView {
  artifactId: string;
  key: string | null;
  role: string;
  locatorKind: string;
  contentDigest: string | null;
  availableReported: boolean | null;
  observedDigest: string | null;
  matchesObserved: boolean | null;
  recordedAt: number | null;
  attested: boolean;
  receipt: {
    deliveryId: string | null;
    step: string | null;
    receiptState: string | null;
    stepState: string | null;
    authorizationId: string | null;
    effectCompleted: boolean | null;
  } | null;
}

/** One recorded evidence record (submitted; ACCEPTED only when the record says so). */
export interface EvidenceItemView {
  evidenceId: string;
  requirementKey: string | null;
  kind: string | null;
  standing: string | null;
  freshness: string | null;
  accepted: boolean | null;
  invalidated: boolean | null;
  verifyingKind: boolean | null;
  drifted: boolean | null;
  unconfirmed: boolean | null;
  bearsOnClaim: boolean | null;
  /**
   * The artifacts the record ITSELF references (a recorded link, never inferred), in recorded
   * order, at most EVIDENCE_LINKS_MAX of them.
   */
  artifactIds: string[];
  /** How many links the record carries: more than `artifactIds.length` only when capped. */
  artifactIdsTotal: number;
}

/**
 * contract v2 (item 2, completed-Mission artifacts) — the HISTORICAL execution identity the
 * backend derived for an observed Mission Core record (`mission_state.execution_identity`):
 * `established` ONLY for a COMPLETED record whose verified run, intent, receipt and PREPARED
 * digest-bound workspace agree; `cursorSha256` then binds the journal head AND that identity and
 * is what a completed Mission's content read must present. `not_reported`: the producer sends
 * no block (nothing inferred); `unexpected`: a malformed block (never guessed).
 */
export interface ExecutionIdentityView {
  state: 'not_reported' | 'established' | 'not_established' | 'not_applicable' | 'unexpected';
  problem: string | null;
  identitySha256: string | null;
  cursorSha256: string | null;
}

/** contract v2 — Mission State: per-kind inputs and the allowlisted canonical report facts. */
export interface MissionStateView {
  state: string;
  problem: string | null;
  detail: string | null;
  report: MissionReportView | null;
  executionIdentity: ExecutionIdentityView;
  inputs: {
    cursor: string | null;
    baseline: string | null;
    candidateApplicability: string | null;
    kinds: Array<{ kind: string; supplied: string | null; reason: string | null }>;
  } | null;
}

/**
 * contract v2 — the ADMITTED canonical review record. Preamble only (four fields); the gate
 * (parsed | unparsed) and the verdict (APPROVE | REJECT | null) are kept apart.
 */
export interface CanonicalReviewView {
  state: string;
  problem: string | null;
  gate: string | null;
  verdict: string | null;
  currency: string | null;
  integrity: string | null;
  taskStatus: string | null;
  candidateApplicability: string;
  record: {
    round: number;
    reviewerLogical: string;
    reviewerAgent: string;
    protocolToken: string;
  } | null;
  /** item 2 E2: the reviewer-declared PUBLIC findings the local gate recorded (never transcript). */
  findings: ReviewFindingsView;
}

/**
 * item 2 E2 — the findings block of an admitted review, read STRICTLY from what the backend
 * emitted: `not_reported` (an older producer emitted no `findings` key), the four backend states
 * (`not_recorded` / `empty` / `present` / `refused`, with its code), or `unexpected` (any
 * deviation from the closed shape: shown as unavailable, never guessed). Items are the
 * reviewer's DECLARED public text; nothing here is a verdict or an approval.
 */
export interface ReviewFindingsView {
  state: 'not_reported' | 'not_recorded' | 'empty' | 'present' | 'refused' | 'unexpected';
  problem: string | null;
  count: number | null;
  items: Array<{
    index: number;
    severity: 'blocker' | 'non_blocking';
    area: string | null;
    line: number | null;
    summary: string;
  }>;
  candidateSha256: string | null;
  routedAt: number | null;
  provenance: string | null;
}

/**
 * contract v2 — the workflow phase the PRODUCER DECLARES as its own typed field
 * (`missions[].workflow_phase`, mission_sources.contract_v2). It is read only from that field, never
 * derived from the display-only `lifecycle.state`:
 * - observed:       a workflow_authority.workflow_phase/2 record's own phase (closed vocabulary);
 * - not_applicable: a model with no workflow phase (Mission Core), with the producer's problem;
 * - not_reported:   a v2 answer from a producer that predates the declared field — unknown, never
 *                   "none" (the backend's rule: an additive change keeps the contract version).
 */
export type WorkflowPhaseView =
  | { state: 'observed'; phase: Classified<WorkflowPhase> }
  | { state: 'not_applicable'; problem: string }
  | { state: 'not_reported' };

/**
 * contract v2 — the DECLARED delivery link of one Mission (`missions[].delivery_link`, mission_sources.contract_v2). A linked
 * Mission carries ONE delivery id, of the shape a ceremony mints, with the rule that linked it; an unlinked one carries neither,
 * only the producer's reason. It selects which delivery's status may be read and authorizes nothing.
 */
export type MissionDeliveryLinkView =
  | { state: 'linked'; deliveryId: string; basis: MissionDeliveryLinkBasis }
  | { state: Exclude<MissionDeliveryLinkState, 'linked'>; deliveryId: null; basis: null };

export interface MissionV2View {
  taskChains: TaskChainView[];
  childrenRecord: ChildrenRecordView;
  missionState: MissionStateView;
  canonicalReview: CanonicalReviewView;
  workflowPhase: WorkflowPhaseView;
  /** ABSENT (the key is omitted, never defaulted) when the producer's answer predates `delivery_link`. */
  deliveryLink?: MissionDeliveryLinkView;
}

export type MissionNative =
  | { kind: 'mission_core'; cancelRequested: boolean | null; paused: boolean | null }
  | { kind: 'workflow_store'; ambiguity: string | null }
  | { kind: 'herdr_task'; taskRecordVersion: number | null; startedAt: number | null }
  | { kind: 'unrecognized'; raw: unknown };

export type RecordedProvenance =
  | { kind: 'mission_core'; principalKind: string | null; transport: string | null }
  | { kind: 'workflow_store'; originTransport: string | null; telegramBound: boolean | null }
  | {
      kind: 'herdr_task';
      schema: string | null;
      schemaVersion: number | null;
      rootAliases: string[];
      membership: string | null;
    }
  | { kind: 'unrecognized'; raw: unknown };

export interface MissionView {
  missionRef: string;
  sourceId: string;
  nativeId: string;
  lifecycle: {
    model: Classified<LifecycleModel>;
    /** Native literal, verbatim and case-preserved. NEVER switch on it. */
    state: string;
    standing: Classified<Standing>;
  };
  /** Declared provenance only — never an identity join. */
  producerDeclared: string;
  producerRecorded: RecordedProvenance;
  revision: number | null;
  objective: string | null;
  updatedAt: number | null;
  execution: {
    taskId: string | null;
    identitySource: string | null;
    workspaceRealpath: string | null;
    runStarted: boolean;
    receiptRecorded: boolean;
    consistent: boolean;
    recordAmbiguous: boolean;
  };
  native: MissionNative;
  binding: {
    state: Classified<BindingState>;
    reason: Classified<BindingReason>;
    taskId: string | null;
  };
  members: MemberView[];
  /** contract v2 blocks; null for a v1 response. */
  v2: MissionV2View | null;
}

export interface SourceView {
  sourceId: string;
  kind: string;
  producerDeclared: string;
  health: Classified<Health>;
  problem: string | null;
  observedAt: number | null;
  recordCount: number | null;
  maxAgeSeconds: number | null;
  /** item 07: a task-native source's per-alias locator account; null when it carries none. */
  locators: LocatorAccountView | null;
}

/** item 07 — every registered alias accounted for once; exclusions with their reasons. */
export interface LocatorAccountView {
  registeredAliases: number;
  physicalRoots: number;
  sharedRootGroups: number;
  rootsWithCurrentTask: number;
  rootsWithoutCurrentTask: number;
  aliasesWithoutHerdState: number;
  excluded: Array<{ alias: string; reason: string }>;
}

/** item 07 — one live agent a task-native role record identifies (never a team member). */
export interface RuntimeAgentView {
  agentRef: string;
  sourceId: string;
  rootRef: string;
  rootAliases: string[];
  logical: string;
  role: string | null;
  agent: string;
  session: string | null;
  paneId: string | null;
  workspaceId: string | null;
  provider: string | null;
  activity: Classified<Activity> | null;
  identity: { state: Classified<RuntimeIdentityState>; reason: string };
  assignment: {
    state: Classified<RuntimeAssignmentState>;
    reason: string;
    taskRef: string | null;
    taskId: string | null;
    taskStatus: string | null;
  };
}

/**
 * item 07 — `response.runtime_agents`. `reported: false` = an older producer that predates the
 * field: UNKNOWN, never "none". When reported it is projected STRICTLY (closed keys).
 */
export interface RuntimeAgentsView {
  reported: boolean;
  state: Classified<RuntimeAgentsState> | null;
  problem: string | null;
  observedAt: number | null;
  firstPassAt: number | null;
  secondPassAt: number | null;
  caveat: string | null;
  counts: Record<(typeof RUNTIME_AGENTS_COUNT_KEYS)[number], number> | null;
  agents: RuntimeAgentView[];
  agentsOverBound: number;
}

/** One presented delivery proposal, exactly the allowlisted facts the catalog projects (D1). */
export interface DeliveryPresentationEntryView {
  sourceId: string;
  proposalDigest: string;
  presentedAt: number;
  expiresAt: number;
  mode: (typeof DELIVERY_MODES)[number];
  repositoryUrl: string | null;
  bindingMission: { workflowId: string; authorizationDigest: string | null } | null;
  state: (typeof DELIVERY_ENTRY_STATES)[number];
  matchingDeliveries: number | null;
  link: {
    state: (typeof DELIVERY_LINK_STATES)[number];
    missionRef: string | null;
    authorizationDigest: (typeof DELIVERY_AUTHORIZATION_CHECKS)[number];
  };
}

/**
 * D1 — `response.delivery_presentations` (contract v2). `reported: false` = an older catalog that
 * predates the block: UNKNOWN to this office, never "none" and never known_empty. When reported it
 * is projected STRICTLY.
 */
export interface DeliveryPresentationsView {
  reported: boolean;
  state: (typeof DELIVERY_PRESENTATIONS_STATES)[number] | null;
  problem: (typeof DELIVERY_PRESENTATIONS_PROBLEMS)[number] | null;
  deliveryStore: (typeof DELIVERY_STORE_STATES)[number] | null;
  total: number | null;
  entries: DeliveryPresentationEntryView[];
}

export interface CatalogResponseView {
  generatedAt: number;
  validUntil: number;
  /** A DURATION in seconds, not an epoch. */
  maxAgeSeconds: number;
  minPollIntervalSeconds: number | null;
  registry: { health: Classified<Health>; problem: string | null; sourceCount: number | null };
  sources: SourceView[];
  runtimeSnapshot: {
    health: Classified<Health>;
    problem: string | null;
    observedAt: number | null;
    maxAgeSeconds: number | null;
    producer: string | null;
    taskCount: number | null;
    agentCount: number | null;
  };
  missions: MissionView[];
  unmatchedRuntimeAgentCount: number | null;
  /** item 07: runtime agents (both contract versions; absent from older producers). */
  runtimeAgents: RuntimeAgentsView;
  /** D1: the Grok delivery presentations block (contract v2 only; absent from older producers). */
  deliveryPresentations: DeliveryPresentationsView;
  /** The contract version this response was projected as. */
  contractVersion: QueryContractVersion;
  /** contract v2 chain observation health; null for v1. */
  chainObservation: {
    health: Classified<Health>;
    problem: string | null;
    observedAt: number | null;
    maxAgeSeconds: number | null;
    producer: string | null;
    rootCount: number | null;
    unavailableCount: number | null;
    overCapacityCount: number | null;
  } | null;
}

export type ResponseResult =
  { ok: true; view: CatalogResponseView } | { ok: false; verdict: ShapeVerdict };

const MISSION_CORE_MODEL = 'mission_core.mission_state/1';
const WORKFLOW_STORE_MODEL = 'workflow_authority.workflow_phase/2';

function projectNative(
  model: Classified<LifecycleModel>,
  raw: unknown,
  where: string,
): MissionNative {
  const o = obj(raw, where);
  const b = (v: unknown): boolean | null => (typeof v === 'boolean' ? v : null);
  if (model.known && model.value === MISSION_CORE_MODEL) {
    return { kind: 'mission_core', cancelRequested: b(o.cancel_requested), paused: b(o.paused) };
  }
  if (model.known && model.value === WORKFLOW_STORE_MODEL) {
    return {
      kind: 'workflow_store',
      ambiguity: typeof o.ambiguity === 'string' ? o.ambiguity : null,
    };
  }
  if (model.known && model.value === HERDR_TASK_MODEL) {
    const n = (v: unknown): number | null =>
      typeof v === 'number' && Number.isInteger(v) ? v : null;
    return {
      kind: 'herdr_task',
      taskRecordVersion: n(o.task_record_version),
      startedAt: n(o.started_at),
    };
  }
  return { kind: 'unrecognized', raw };
}

function projectRecorded(
  model: Classified<LifecycleModel>,
  raw: unknown,
  where: string,
): RecordedProvenance {
  const o = obj(raw, where);
  const s = (v: unknown): string | null => (typeof v === 'string' ? v : null);
  if (model.known && model.value === MISSION_CORE_MODEL) {
    return { kind: 'mission_core', principalKind: s(o.principal_kind), transport: s(o.transport) };
  }
  if (model.known && model.value === WORKFLOW_STORE_MODEL) {
    return {
      kind: 'workflow_store',
      originTransport: s(o.origin_transport),
      telegramBound: typeof o.telegram_bound === 'boolean' ? o.telegram_bound : null,
    };
  }
  if (model.known && model.value === HERDR_TASK_MODEL) {
    const aliases = Array.isArray(o.root_aliases)
      ? o.root_aliases.filter((a): a is string => typeof a === 'string')
      : [];
    return {
      kind: 'herdr_task',
      schema: s(o.schema),
      schemaVersion:
        typeof o.schema_version === 'number' && Number.isInteger(o.schema_version)
          ? o.schema_version
          : null,
      rootAliases: aliases,
      membership: s(o.membership),
    };
  }
  return { kind: 'unrecognized', raw };
}

function projectMember(
  raw: unknown,
  missionRef: string,
  where: string,
  version: QueryContractVersion,
): MemberView {
  const m = obj(raw, where);
  const memberRef = str(key(m, 'member_ref', where), `${where}.member_ref`);
  const ownMissionRef = str(key(m, 'mission_ref', where), `${where}.mission_ref`);
  // A member belongs to exactly the mission that carries it: an equality of two explicit
  // fields. Refs are OPAQUE identities — never parsed to decide what something is.
  if (ownMissionRef !== missionRef) {
    malformed(`${where}.mission_ref differs from its mission`);
  }
  const session = obj(key(m, 'session', where), `${where}.session`);
  const unreported = {} as Record<(typeof UNREPORTED_FACTS)[number], UnreportedFact>;
  for (const fact of UNREPORTED_FACTS) unreported[fact] = readUnreportedFact(m, fact);
  return {
    memberRef,
    missionRef,
    taskId: strOrNull(key(m, 'task_id', where), `${where}.task_id`),
    logical: str(key(m, 'logical', where), `${where}.logical`),
    role: str(key(m, 'role', where), `${where}.role`),
    agent: str(key(m, 'agent', where), `${where}.agent`),
    provider: str(key(m, 'provider', where), `${where}.provider`),
    paneId: strOrNull(key(m, 'pane_id', where), `${where}.pane_id`),
    workspaceId: strOrNull(key(m, 'workspace_id', where), `${where}.workspace_id`),
    session: {
      recorded: strOrNull(
        key(session, 'recorded', `${where}.session`),
        `${where}.session.recorded`,
      ),
      live: strOrNull(key(session, 'live', `${where}.session`), `${where}.session.live`),
    },
    classification: classify(MEMBER_CLASSIFICATIONS, key(m, 'classification', where)),
    reason: classify(MEMBER_REASONS, key(m, 'reason', where)),
    activity: classify(ACTIVITIES, key(m, 'activity', where)),
    observedAt: intOrNull(key(m, 'observed_at', where), `${where}.observed_at`),
    unreported,
    chain:
      version === 2
        ? classify(CHAIN_KINDS, key(m, 'chain', where))
        : { known: true, value: 'direct' },
    chainTaskId:
      version === 2 ? strOrNull(key(m, 'chain_task_id', where), `${where}.chain_task_id`) : null,
  };
}

function projectTaskChain(raw: unknown, where: string): TaskChainView {
  const c = obj(raw, where);
  const binding = obj(key(c, 'binding', where), `${where}.binding`);
  const root = obj(key(c, 'root', where), `${where}.root`);
  const end = obj(key(c, 'membership_end', where), `${where}.membership_end`);
  const depRaw = key(c, 'dependency', where);
  const recRaw = key(c, 'record', where);
  const boolOrNull = (v: unknown, w: string): boolean | null => (v === null ? null : bool(v, w));
  let record: TaskChainView['record'] = null;
  if (recRaw !== null) {
    const r = obj(recRaw, `${where}.record`);
    const agents = key(r, 'declared_agents', `${where}.record`);
    if (!Array.isArray(agents)) malformed(`${where}.record.declared_agents is not a list`);
    record = {
      childTaskId: str(key(r, 'child_task_id', `${where}.record`), `${where}.record.child_task_id`),
      repo: str(key(r, 'repo', `${where}.record`), `${where}.record.repo`),
      workspaceId: str(key(r, 'workspace_id', `${where}.record`), `${where}.record.workspace_id`),
      declaredAgents: agents.map((a, i) => {
        const w = `${where}.record.declared_agents[${i}]`;
        const o = obj(a, w);
        return {
          logical: str(key(o, 'logical', w), `${w}.logical`),
          agent: str(key(o, 'agent', w), `${w}.agent`),
        };
      }),
    };
  }
  let dependency: TaskChainView['dependency'] = null;
  if (depRaw !== null) {
    const d = obj(depRaw, `${where}.dependency`);
    dependency = {
      recorded: bool(key(d, 'recorded', `${where}.dependency`), `${where}.dependency.recorded`),
      resolution: str(
        key(d, 'resolution', `${where}.dependency`),
        `${where}.dependency.resolution`,
      ),
      rule: str(key(d, 'rule', `${where}.dependency`), `${where}.dependency.rule`),
    };
  }
  return {
    chain: classify(CHAIN_KINDS, key(c, 'chain', where)),
    chainTaskId: strOrNull(key(c, 'chain_task_id', where), `${where}.chain_task_id`),
    parentTaskId: strOrNull(key(c, 'parent_task_id', where), `${where}.parent_task_id`),
    binding: {
      state: classify(BINDING_STATES, key(binding, 'state', `${where}.binding`)),
      reason: classify(CHAIN_BINDING_REASONS, key(binding, 'reason', `${where}.binding`)),
      taskId: strOrNull(key(binding, 'task_id', `${where}.binding`), `${where}.binding.task_id`),
    },
    root: {
      observation: str(key(root, 'observation', `${where}.root`), `${where}.root.observation`),
      path: strOrNull(key(root, 'path', `${where}.root`), `${where}.root.path`),
      problem: strOrNull(key(root, 'problem', `${where}.root`), `${where}.root.problem`),
    },
    membershipEnd: {
      ended: boolOrNull(
        key(end, 'ended', `${where}.membership_end`),
        `${where}.membership_end.ended`,
      ),
      rule: str(key(end, 'rule', `${where}.membership_end`), `${where}.membership_end.rule`),
      status: strOrNull(
        key(end, 'status', `${where}.membership_end`),
        `${where}.membership_end.status`,
      ),
    },
    dependency,
    descendantsNotFollowed: intOrNull(
      key(c, 'descendants_not_followed', where),
      `${where}.descendants_not_followed`,
    ),
    record,
  };
}

/** Bounded display text for a recorded reference (never dereferenced). */
const REF_TEXT_MAX = 160;
/** Display cap for listed entries; the real total and the omitted count are always carried. */
const REF_LIST_MAX = 32;
/**
 * Links kept per evidence record: Mission Core's own per-record bound (`mission/state.py`
 * MAX_ARTIFACT_LINKS = 64), so a canonical record is never cut. A longer list (not canonical)
 * keeps its first EVIDENCE_LINKS_MAX and its TRUE total — never a silent cut (round 15 F4).
 */
export const EVIDENCE_LINKS_MAX = 64;
function refText(item: unknown): string {
  const json = JSON.stringify(item) as string | undefined;
  const text = typeof item === 'string' ? item : (json ?? String(item));
  return text.length > REF_TEXT_MAX ? `${text.slice(0, REF_TEXT_MAX - 1)}…` : text;
}

const FINDINGS_KEYS = [
  'candidate_sha256',
  'count',
  'items',
  'linkage',
  'problem',
  'provenance',
  'routed_at',
  'state',
];
const FINDING_KEYS = ['area', 'index', 'line', 'severity', 'summary'];
const FINDINGS_MAX = 32;
const FINDING_SUMMARY_MAX = 280;
const FINDING_AREA_MAX = 200;
const FINDINGS_PROBLEM = /^findings_[a-z_]{1,60}$/;
const sameKeys = (o: Json, keys: string[]): boolean =>
  Object.keys(o).sort().join('\u0000') === keys.join('\u0000');

const EXECUTION_IDENTITY_KEYS = ['cursor_sha256', 'identity_sha256', 'problem', 'state'];
const EXECUTION_PROBLEM = /^execution_[a-z_]{1,60}$/;

/** The execution identity block, strictly; any deviation is `unexpected` (never guessed). */
function projectExecutionIdentity(ms: Json): ExecutionIdentityView {
  const none = (
    state: ExecutionIdentityView['state'],
    problem: string | null = null,
  ): ExecutionIdentityView => ({ state, problem, identitySha256: null, cursorSha256: null });
  if (!('execution_identity' in ms)) return none('not_reported');
  const v = ms.execution_identity;
  if (!isObject(v) || !sameKeys(v, EXECUTION_IDENTITY_KEYS)) return none('unexpected');
  if (v.state === 'established') {
    if (
      v.problem !== null ||
      typeof v.identity_sha256 !== 'string' ||
      !/^[0-9a-f]{64}$/.test(v.identity_sha256) ||
      typeof v.cursor_sha256 !== 'string' ||
      !/^[0-9a-f]{64}$/.test(v.cursor_sha256)
    ) {
      return none('unexpected');
    }
    return {
      state: 'established',
      problem: null,
      identitySha256: v.identity_sha256,
      cursorSha256: v.cursor_sha256,
    };
  }
  if (
    (v.state === 'not_established' || v.state === 'not_applicable') &&
    v.identity_sha256 === null &&
    v.cursor_sha256 === null &&
    typeof v.problem === 'string' &&
    EXECUTION_PROBLEM.test(v.problem)
  ) {
    return none(v.state, v.problem);
  }
  return none('unexpected');
}

/** The findings block, strictly; any deviation is `unexpected` (never guessed). */
function projectFindings(review: Json): ReviewFindingsView {
  const none = (state: ReviewFindingsView['state'], problem: string | null = null) => ({
    state,
    problem,
    count: null,
    items: [],
    candidateSha256: null,
    routedAt: null,
    provenance: null,
  });
  if (!('findings' in review)) return none('not_reported');
  const f = review.findings;
  if (f === null) return none('unexpected');
  if (!isObject(f) || !sameKeys(f, FINDINGS_KEYS)) return none('unexpected');
  const state = f.state;
  if (state === 'not_recorded' || state === 'refused') {
    const nulls = ['count', 'candidate_sha256', 'routed_at', 'linkage', 'provenance'];
    if (
      typeof f.problem !== 'string' ||
      !FINDINGS_PROBLEM.test(f.problem) ||
      !Array.isArray(f.items) ||
      f.items.length !== 0 ||
      nulls.some((k) => f[k] !== null)
    ) {
      return none('unexpected');
    }
    return none(state, f.problem);
  }
  if (state !== 'empty' && state !== 'present') return none('unexpected');
  if (f.problem !== null || !Array.isArray(f.items) || f.items.length > FINDINGS_MAX) {
    return none('unexpected');
  }
  const count = ri(f.count);
  if (count === null || count !== f.items.length || (state === 'empty') !== (count === 0)) {
    return none('unexpected');
  }
  if (
    typeof f.candidate_sha256 !== 'string' ||
    !/^[0-9a-f]{64}$/.test(f.candidate_sha256) ||
    ri(f.routed_at) === null ||
    typeof f.linkage !== 'string' ||
    typeof f.provenance !== 'string'
  ) {
    return none('unexpected');
  }
  const items: ReviewFindingsView['items'] = [];
  for (const [i, raw] of (f.items as unknown[]).entries()) {
    if (!isObject(raw) || !sameKeys(raw, FINDING_KEYS)) return none('unexpected');
    const severity = raw.severity;
    const area = raw.area;
    const line = raw.line;
    const summary = raw.summary;
    if (
      raw.index !== i + 1 ||
      (severity !== 'blocker' && severity !== 'non_blocking') ||
      (area !== null && (typeof area !== 'string' || area.length > FINDING_AREA_MAX)) ||
      (line !== null && (ri(line) === null || (line as number) < 1 || area === null)) ||
      typeof summary !== 'string' ||
      summary.length === 0 ||
      summary.length > FINDING_SUMMARY_MAX
    ) {
      return none('unexpected');
    }
    items.push({
      index: i + 1,
      severity,
      area: area as string | null,
      line: line as number | null,
      summary,
    });
  }
  return {
    state,
    problem: null,
    count,
    items,
    candidateSha256: f.candidate_sha256,
    routedAt: ri(f.routed_at),
    provenance: f.provenance,
  };
}

/** Permissive readers for the verbatim report: a wrong type reads as null, never throws. */
const rb = (v: unknown): boolean | null => (typeof v === 'boolean' ? v : null);
const rs = (v: unknown): string | null => (typeof v === 'string' ? v : null);
const ri = (v: unknown): number | null => (typeof v === 'number' && Number.isInteger(v) ? v : null);

/** Absent or null (the verbatim report may omit or null a field). */
const isNil = (v: unknown): v is null | undefined => v === null || v === undefined;

const UNKNOWN_LIST: BoundedList = { known: false, total: null, items: [] };

/** Mission Core's own id grammar for the two record kinds the library lists. */
const ARTIFACT_ID = /^mf-[0-9a-f]{32}$/;
const EVIDENCE_ID = /^mv-[0-9a-f]{32}$/;
const DIGEST = /^[0-9a-f]{64}$/;

/** A list read STRICTLY: every item must read, or the whole list is null (unexpected). */
function typedList<T>(v: unknown, read: (item: unknown) => T | null): TypedList<T> | null {
  if (!Array.isArray(v)) return null;
  const items: T[] = [];
  for (const item of v) {
    const r = read(item);
    if (r === null) return null;
    if (items.length < REF_LIST_MAX) items.push(r);
  }
  return { total: v.length, items };
}

const digestOrNull = (v: unknown): string | null | undefined =>
  v === null ? null : typeof v === 'string' && DIGEST.test(v) ? v : undefined;

function readArtifactItem(v: unknown): ArtifactItemView | null {
  if (!isObject(v)) return null;
  const id = v.artifact_id;
  const role = v.role;
  const kind = v.locator_kind;
  if (typeof id !== 'string' || !ARTIFACT_ID.test(id)) return null;
  if (typeof role !== 'string' || typeof kind !== 'string') return null;
  if (typeof v.attested !== 'boolean') return null;
  const content = digestOrNull(v.content_digest_sha256);
  const observed = digestOrNull(v.observed_digest_sha256 ?? null);
  if (content === undefined || observed === undefined) return null;
  const marker = v.receipt_attestation;
  if (!isNil(marker) && !isObject(marker)) return null;
  if (v.attested !== isObject(marker)) return null;
  return {
    artifactId: id,
    key: rs(v.key),
    role: refText(role),
    locatorKind: refText(kind),
    contentDigest: content,
    availableReported: rb(v.available_reported),
    observedDigest: observed,
    matchesObserved: rb(v.matches_observed),
    recordedAt: ri(v.recorded_at),
    attested: v.attested,
    receipt: isObject(marker)
      ? {
          deliveryId: isNil(marker.delivery_id) ? null : refText(marker.delivery_id),
          step: isNil(marker.step) ? null : refText(marker.step),
          receiptState: isNil(marker.receipt_state) ? null : refText(marker.receipt_state),
          stepState: isNil(marker.step_state) ? null : refText(marker.step_state),
          authorizationId: isNil(marker.authorization_id) ? null : refText(marker.authorization_id),
          effectCompleted: rb(marker.effect_completed),
        }
      : null,
  };
}

function readEvidenceItem(v: unknown): EvidenceItemView | null {
  if (!isObject(v)) return null;
  const id = v.evidence_id;
  if (typeof id !== 'string' || !EVIDENCE_ID.test(id)) return null;
  const links = v.artifact_ids;
  if (!Array.isArray(links)) return null;
  if (!links.every((a) => typeof a === 'string' && ARTIFACT_ID.test(a))) return null;
  return {
    evidenceId: id,
    requirementKey: isNil(v.requirement_key) ? null : refText(v.requirement_key),
    kind: isNil(v.kind) ? null : refText(v.kind),
    standing: rs(v.standing),
    freshness: rs(v.freshness),
    accepted: rb(v.accepted),
    invalidated: rb(v.invalidated),
    verifyingKind: rb(v.verifying_kind),
    drifted: rb(v.drifted),
    unconfirmed: rb(v.unconfirmed),
    bearsOnClaim: rb(v.bears_on_claim),
    artifactIds: (links as string[]).slice(0, EVIDENCE_LINKS_MAX),
    artifactIdsTotal: links.length,
  };
}

/** A list as a BoundedList: a non-list is UNKNOWN (never empty); a capped list keeps its total. */
function boundedList(v: unknown): BoundedList {
  if (!Array.isArray(v)) return UNKNOWN_LIST;
  return { known: true, total: v.length, items: v.slice(0, REF_LIST_MAX).map(refText) };
}

/** An object map's entries ("key value") as a BoundedList; a non-object is UNKNOWN. */
function boundedEntries(v: unknown, sep: string): BoundedList {
  if (!isObject(v)) return UNKNOWN_LIST;
  const keys = Object.keys(v).sort();
  return {
    known: true,
    total: keys.length,
    items: keys.slice(0, REF_LIST_MAX).map((k) => refText(`${k}${sep}${String(v[k])}`)),
  };
}

/** A collection's length, or null (UNKNOWN) when it is not a list — never a claimed 0. */
const countOf = (v: unknown): number | null => (Array.isArray(v) ? v.length : null);

function reportFact<T>(raw: unknown, readValue: (value: unknown) => T | null): ReportFact<T> {
  const none = {
    standing: null,
    source: null,
    freshness: null,
    detail: null,
    observedAt: null,
    revision: null,
    position: null,
    value: null,
  };
  if (!isObject(raw)) return { shape: 'unexpected', ...none };
  const value = raw.value;
  let read: T | null = null;
  let shape: ReportFact<T>['shape'] = 'ok';
  if (value !== null && value !== undefined) {
    read = readValue(value);
    if (read === null) shape = 'unexpected';
  }
  return {
    shape,
    standing: rs(raw.standing),
    source: rs(raw.source),
    freshness: rs(raw.freshness),
    detail: rs(raw.detail),
    observedAt: ri(raw.observed_at),
    revision: ri(raw.revision),
    position: ri(raw.position),
    value: read,
  };
}

function projectProvenance(raw: unknown): MissionReportView['provenance'] {
  if (!isObject(raw)) return null;
  const collection = isObject(raw.collection) ? raw.collection : {};
  const at = isObject(collection.collected_at) ? collection.collected_at : {};
  const head = isObject(collection.head) ? collection.head : {};
  const cursor = isObject(raw.cursor) ? raw.cursor : {};
  const sources = isObject(raw.sources) ? raw.sources : {};
  return {
    recordSource: rs(raw.record_source),
    revision: ri(raw.revision),
    position: ri(raw.position),
    freshnessBoundSeconds: ri(raw.freshness_bound_seconds),
    journalDigest: rs(raw.journal_digest_sha256),
    movedDuringCollection: isNil(raw.moved_during_collection)
      ? null
      : refText(raw.moved_during_collection),
    collectionStatus: rs(collection.status),
    collectedAtPosition: ri(at.position),
    headPosition: ri(head.position),
    cursorPosition: ri(cursor.position),
    drift: boundedList(raw.drift),
    sources: Object.keys(sources)
      .sort()
      .map((kind) => {
        const o = isObject(sources[kind]) ? (sources[kind] as Json) : {};
        return {
          kind,
          source: rs(o.source),
          standing: rs(o.standing),
          observedAt: ri(o.observed_at),
          detail: rs(o.detail),
        };
      }),
  };
}

function projectReport(raw: unknown): MissionReportView | null {
  if (!isObject(raw)) return null;
  const c = raw.completion;
  return {
    revision: ri(raw.revision),
    observedAt: ri(raw.observed_at),
    blockers: reportFact(raw.blockers, (v) =>
      isObject(v) ? { active: boundedList(v.active), hardActive: rb(v.hard_active) } : null,
    ),
    pendingDecisions: reportFact(raw.pending_decisions, (v) =>
      isObject(v)
        ? {
            awaitingDecision: rb(v.awaiting_decision),
            liveAuthorization: rb(v.live_authorization),
            nextPermittedStep: isNil(v.next_permitted_step) ? null : refText(v.next_permitted_step),
            revision: ri(v.revision),
          }
        : null,
    ),
    review: reportFact(raw.review, rs),
    task: reportFact(raw.task, rs),
    candidate: reportFact(raw.candidate, (v) =>
      isObject(v)
        ? {
            artifactDigests: boundedEntries(v.artifact_digests, ' '),
            baselineDigest: rs(v.baseline_digest_sha256),
          }
        : null,
    ),
    completion: isObject(c)
      ? {
          reportedComplete: rb(c.reported_complete),
          reportedSuccess: rb(c.reported_success),
          verifiedSuccess: rb(c.verified_success),
          closureVerified: rb(c.closure_verified),
          contradicted: rb(c.contradicted),
          closure: isNil(c.closure) ? null : refText(c.closure),
          holds: boundedList(c.holds),
          limits: isObject(c.limits)
            ? {
                identity: rs(c.limits.identity),
                separationOfDuties: rs(c.limits.separation_of_duties),
                nonVerifyingEvidenceKinds: boundedList(c.limits.non_verifying_evidence_kinds),
              }
            : null,
        }
      : null,
    provenance: projectProvenance(raw.provenance),
    delivery: reportFact(raw.delivery, (v) => refText(v)),
    deliveryReceipts: reportFact(raw.delivery_receipts, (v) =>
      isObject(v)
        ? {
            recorded: countOf(v.recorded),
            attested: countOf(v.attested),
            unattested: countOf(v.unattested),
            effectsCompleted: countOf(v.effects_completed),
            reportBoundTo: isNil(v.report_bound_to) ? null : refText(v.report_bound_to),
            reportBoundAttested: isNil(v.report_bound_attested)
              ? null
              : refText(v.report_bound_attested),
            reportFresh: isNil(v.report_fresh) ? null : refText(v.report_fresh),
          }
        : null,
    ),
    proof: reportFact(raw.proof, (v) =>
      isObject(v)
        ? { satisfied: rb(v.satisfied), requirements: boundedEntries(v.requirements, ': ') }
        : null,
    ),
    readiness: reportFact(raw.readiness, (v) =>
      isObject(v) ? { satisfied: rb(v.satisfied) } : null,
    ),
    dependencies: reportFact(raw.dependencies, (v) =>
      isObject(v) ? { satisfied: rb(v.satisfied) } : null,
    ),
    budget: reportFact(raw.budget, (v) =>
      isObject(v)
        ? {
            attemptsRemaining: ri(v.attempts_remaining),
            checkpointsRemaining: ri(v.checkpoints_remaining),
          }
        : null,
    ),
    evidence: reportFact(raw.evidence, (v) => (Array.isArray(v) ? boundedList(v) : null)),
    artifacts: reportFact(raw.artifacts, (v) => (Array.isArray(v) ? boundedList(v) : null)),
    artifactItems: reportFact(raw.artifacts, (v) => typedList(v, readArtifactItem)),
    evidenceItems: reportFact(raw.evidence, (v) => typedList(v, readEvidenceItem)),
  };
}

/**
 * The declared `missions[].workflow_phase` (contract v2): STRICT when present — closed keys, and
 * only a workflow_authority.workflow_phase/2 record may be `observed` (the producer's own rule);
 * absent → `not_reported`. An out-of-vocabulary phase stays visible as unrecognized.
 */
function projectWorkflowPhase(
  m: Json,
  where: string,
  model: Classified<LifecycleModel>,
): WorkflowPhaseView {
  if (!('workflow_phase' in m)) return { state: 'not_reported' };
  const at = `${where}.workflow_phase`;
  const w = obj(m.workflow_phase, at);
  exactKeys(w, WORKFLOW_PHASE_KEYS, at);
  const state = str(w.state, `${at}.state`);
  const workflow = model.known && model.value === 'workflow_authority.workflow_phase/2';
  if (state === 'observed') {
    if (!workflow) malformed(`${at} is observed on a record whose model has no workflow phase`);
    if (w.problem !== null) malformed(`${at}.problem must be null when observed`);
    return { state: 'observed', phase: classify(WORKFLOW_PHASES, w.phase) };
  }
  if (state === 'not_applicable') {
    if (workflow) malformed(`${at} is not_applicable on a workflow_authority record`);
    if (w.phase !== null) malformed(`${at}.phase must be null when not_applicable`);
    return { state: 'not_applicable', problem: str(w.problem, `${at}.problem`) };
  }
  return malformed(`${at}.state is not a declared state`);
}

/**
 * The declared `missions[].delivery_link` (contract v2): STRICT when present — closed keys, a declared state, and the producer's
 * own rule. A workflow Mission is never `unlinked_model_not_recorded` and links only by `record_mission_binding`. A Herdr task
 * may be that state (a catalog that did not address repositories) or any other delivery-link state, and links only by
 * `record_repository_binding`. Every other model is `unlinked_model_not_recorded`. `unlinked_repository_not_recorded` is Herdr
 * only. A linked Mission carries ONE id of the ceremony's shape and the declared basis; an unlinked one carries neither.
 * ABSENT → nothing (the caller omits the key).
 */
function projectDeliveryLink(
  m: Json,
  where: string,
  model: Classified<LifecycleModel>,
): MissionDeliveryLinkView | null {
  if (!('delivery_link' in m)) return null;
  const at = `${where}.delivery_link`;
  const link = obj(m.delivery_link, at);
  exactKeys(link, MISSION_DELIVERY_LINK_KEYS, at);
  const state = member(MISSION_DELIVERY_LINK_STATES, link.state, `${at}.state`);
  const workflow = model.known && model.value === WORKFLOW_STORE_MODEL;
  const herdr = model.known && model.value === HERDR_TASK_MODEL;
  if (state === 'unlinked_model_not_recorded') {
    if (workflow) malformed(`${at}: a workflow Mission is not unlinked_model_not_recorded`);
  } else if (!workflow && !herdr) {
    malformed(`${at}: exactly the Missions whose model no delivery record names are unlinked_model_not_recorded`);
  }
  if (state === 'unlinked_repository_not_recorded' && !herdr) {
    malformed(`${at}: unlinked_repository_not_recorded is a Herdr task whose workspace origin was not read`);
  }
  if (state === 'linked') {
    const id = link.delivery_id;
    if (typeof id !== 'string' || !MISSION_DELIVERY_ID.test(id)) {
      malformed(`${at}.delivery_id is not one delivery id of the shape a ceremony mints`);
    }
    const basis = member(MISSION_DELIVERY_LINK_BASES, link.basis, `${at}.basis`);
    if (basis === 'record_mission_binding' && !workflow) {
      malformed(`${at}: record_mission_binding links a workflow Mission`);
    }
    if (basis === 'record_repository_binding' && !herdr) {
      malformed(`${at}: record_repository_binding links a Herdr task`);
    }
    return { state, deliveryId: id, basis };
  }
  if (link.delivery_id !== null || link.basis !== null) {
    malformed(`${at}: an unlinked Mission carries no delivery id and no basis`);
  }
  return { state, deliveryId: null, basis: null };
}

function projectMissionV2(
  m: Json,
  where: string,
  model: Classified<LifecycleModel>,
): MissionV2View {
  const chainsRaw = key(m, 'task_chains', where);
  if (!Array.isArray(chainsRaw)) malformed(`${where}.task_chains is not a list`);
  const cr = obj(key(m, 'children_record', where), `${where}.children_record`);
  const ms = obj(key(m, 'mission_state', where), `${where}.mission_state`);
  const admitted = obj(key(m, 'admitted_artifacts', where), `${where}.admitted_artifacts`);
  const review = obj(
    key(admitted, 'canonical_review', `${where}.admitted_artifacts`),
    `${where}.admitted_artifacts.canonical_review`,
  );
  const rw = `${where}.admitted_artifacts.canonical_review`;
  const recordRaw = key(review, 'record', rw);
  let record: CanonicalReviewView['record'] = null;
  if (recordRaw !== null) {
    const r = obj(recordRaw, `${rw}.record`);
    // The four preamble fields ONLY — nothing else of the file is ever relayed.
    record = {
      round: int(key(r, 'round', `${rw}.record`), `${rw}.record.round`),
      reviewerLogical: str(
        key(r, 'reviewer_logical', `${rw}.record`),
        `${rw}.record.reviewer_logical`,
      ),
      reviewerAgent: str(key(r, 'reviewer_agent', `${rw}.record`), `${rw}.record.reviewer_agent`),
      protocolToken: str(key(r, 'protocol_token', `${rw}.record`), `${rw}.record.protocol_token`),
    };
  }
  const inputsRaw = key(ms, 'inputs', `${where}.mission_state`);
  let inputs: MissionStateView['inputs'] = null;
  if (inputsRaw !== null) {
    const iw = `${where}.mission_state.inputs`;
    const i = obj(inputsRaw, iw);
    const kindsRaw = 'kinds' in i ? obj(i.kinds, `${iw}.kinds`) : {};
    inputs = {
      cursor: 'cursor' in i ? strOrNull(i.cursor, `${iw}.cursor`) : null,
      baseline: 'baseline' in i ? strOrNull(i.baseline, `${iw}.baseline`) : null,
      candidateApplicability:
        'candidate_applicability' in i
          ? strOrNull(i.candidate_applicability, `${iw}.candidate_applicability`)
          : null,
      kinds: Object.keys(kindsRaw)
        .sort()
        .map((kind) => {
          const k = obj(kindsRaw[kind], `${iw}.kinds.${kind}`);
          return {
            kind,
            supplied: strOrNull(
              key(k, 'supplied', `${iw}.kinds.${kind}`),
              `${iw}.kinds.${kind}.supplied`,
            ),
            reason: strOrNull(
              key(k, 'reason', `${iw}.kinds.${kind}`),
              `${iw}.kinds.${kind}.reason`,
            ),
          };
        }),
    };
  }
  return {
    taskChains: chainsRaw.map((c, i) => projectTaskChain(c, `${where}.task_chains[${i}]`)),
    childrenRecord: {
      state: str(key(cr, 'state', `${where}.children_record`), `${where}.children_record.state`),
      problem: strOrNull(
        key(cr, 'problem', `${where}.children_record`),
        `${where}.children_record.problem`,
      ),
      caveat: strOrNull(
        key(cr, 'caveat', `${where}.children_record`),
        `${where}.children_record.caveat`,
      ),
      recordCount: intOrNull(
        key(cr, 'record_count', `${where}.children_record`),
        `${where}.children_record.record_count`,
      ),
      otherParentCount: intOrNull(
        key(cr, 'other_parent_count', `${where}.children_record`),
        `${where}.children_record.other_parent_count`,
      ),
    },
    missionState: {
      state: str(key(ms, 'state', `${where}.mission_state`), `${where}.mission_state.state`),
      problem: strOrNull(
        key(ms, 'problem', `${where}.mission_state`),
        `${where}.mission_state.problem`,
      ),
      detail: strOrNull(
        key(ms, 'detail', `${where}.mission_state`),
        `${where}.mission_state.detail`,
      ),
      report: projectReport(key(ms, 'report', `${where}.mission_state`)),
      executionIdentity: projectExecutionIdentity(ms),
      inputs,
    },
    canonicalReview: {
      state: str(key(review, 'state', rw), `${rw}.state`),
      problem: strOrNull(key(review, 'problem', rw), `${rw}.problem`),
      gate: strOrNull(key(review, 'gate', rw), `${rw}.gate`),
      verdict: strOrNull(key(review, 'verdict', rw), `${rw}.verdict`),
      currency: strOrNull(key(review, 'currency', rw), `${rw}.currency`),
      integrity: strOrNull(key(review, 'integrity', rw), `${rw}.integrity`),
      taskStatus: strOrNull(key(review, 'task_status', rw), `${rw}.task_status`),
      candidateApplicability: str(
        key(review, 'candidate_applicability', rw),
        `${rw}.candidate_applicability`,
      ),
      record,
      findings: projectFindings(review),
    },
    workflowPhase: projectWorkflowPhase(m, where, model),
    ...deliveryLinkIfDeclared(projectDeliveryLink(m, where, model)),
  };
}

/** The key only when the producer declared a link: an answer that predates the field gets no key at all, never a default. */
function deliveryLinkIfDeclared(link: MissionDeliveryLinkView | null): { deliveryLink?: MissionDeliveryLinkView } {
  return link === null ? {} : { deliveryLink: link };
}

function projectMission(raw: unknown, where: string, version: QueryContractVersion): MissionView {
  const m = obj(raw, where);
  const sourceId = str(key(m, 'source_id', where), `${where}.source_id`);
  const nativeId = str(key(m, 'native_id', where), `${where}.native_id`);
  const missionRef = str(key(m, 'mission_ref', where), `${where}.mission_ref`);
  const lifecycle = obj(key(m, 'lifecycle', where), `${where}.lifecycle`);
  const model = classify(LIFECYCLE_MODELS, key(lifecycle, 'model', `${where}.lifecycle`));
  const producer = obj(key(m, 'producer', where), `${where}.producer`);
  const execution = obj(key(m, 'execution', where), `${where}.execution`);
  const binding = obj(key(m, 'binding', where), `${where}.binding`);
  const membersRaw = key(m, 'members', where);
  if (!Array.isArray(membersRaw)) malformed(`${where}.members is not a list`);
  return {
    missionRef,
    sourceId,
    nativeId,
    lifecycle: {
      model,
      state: str(key(lifecycle, 'state', `${where}.lifecycle`), `${where}.lifecycle.state`),
      standing: classify(STANDINGS, key(lifecycle, 'standing', `${where}.lifecycle`)),
    },
    producerDeclared: str(
      key(producer, 'declared', `${where}.producer`),
      `${where}.producer.declared`,
    ),
    producerRecorded: projectRecorded(
      model,
      key(producer, 'recorded', `${where}.producer`),
      `${where}.producer.recorded`,
    ),
    revision: intOrNull(key(m, 'revision', where), `${where}.revision`),
    objective: strOrNull(key(m, 'objective', where), `${where}.objective`),
    updatedAt: intOrNull(key(m, 'updated_at', where), `${where}.updated_at`),
    execution: {
      taskId: strOrNull(
        key(execution, 'task_id', `${where}.execution`),
        `${where}.execution.task_id`,
      ),
      identitySource: strOrNull(
        key(execution, 'identity_source', `${where}.execution`),
        `${where}.execution.identity_source`,
      ),
      workspaceRealpath: strOrNull(
        key(execution, 'workspace_realpath', `${where}.execution`),
        `${where}.execution.workspace_realpath`,
      ),
      runStarted: bool(
        key(execution, 'run_started', `${where}.execution`),
        `${where}.execution.run_started`,
      ),
      receiptRecorded: bool(
        key(execution, 'receipt_recorded', `${where}.execution`),
        `${where}.execution.receipt_recorded`,
      ),
      consistent: bool(
        key(execution, 'consistent', `${where}.execution`),
        `${where}.execution.consistent`,
      ),
      recordAmbiguous: bool(
        key(execution, 'record_ambiguous', `${where}.execution`),
        `${where}.execution.record_ambiguous`,
      ),
    },
    native: projectNative(model, key(m, 'native', where), `${where}.native`),
    binding: {
      state: classify(BINDING_STATES, key(binding, 'state', `${where}.binding`)),
      reason: classify(BINDING_REASONS, key(binding, 'reason', `${where}.binding`)),
      taskId: strOrNull(key(binding, 'task_id', `${where}.binding`), `${where}.binding.task_id`),
    },
    members: membersRaw.map((member, i) =>
      projectMember(member, missionRef, `${where}.members[${i}]`, version),
    ),
    v2: version === 2 ? projectMissionV2(m, where, model) : null,
  };
}

/** Per-version projectors, side by side (v2 is added beside v1, never instead of it). */
const RESPONSE_PROJECTORS: ReadonlyMap<QueryContractVersion, (raw: Json) => CatalogResponseView> =
  new Map<QueryContractVersion, (raw: Json) => CatalogResponseView>([
    [1, (raw) => projectResponseCommon(raw, 1)],
    [2, (raw) => projectResponseCommon(raw, 2)],
  ]);

/**
 * Project a response as the ONE version this process was launched for. A response of any
 * other contract or version — including another supported one — is `incompatible /
 * response_contract_unsupported`: versions are never negotiated or mixed per request.
 */
export function projectResponse(
  raw: Json,
  selected: QueryContractVersion = DEFAULT_QUERY_CONTRACT_VERSION,
): ResponseResult {
  try {
    const version = raw.contract_version;
    const projector =
      raw.contract === QUERY_CONTRACT &&
      typeof version === 'number' &&
      version === selected &&
      SUPPORTED_QUERY_CONTRACT_VERSIONS.has(version)
        ? RESPONSE_PROJECTORS.get(selected)
        : undefined;
    if (!projector) {
      throw new ShapeError({
        health: 'incompatible',
        problem: 'response_contract_unsupported',
        detail: `response contract or contract_version is not the selected version ${selected}`,
      });
    }
    return { ok: true, view: projector(raw) };
  } catch (err) {
    if (err instanceof ShapeError) return { ok: false, verdict: err.verdict };
    throw err;
  }
}

function projectResponseCommon(raw: Json, version: QueryContractVersion): CatalogResponseView {
  // item 07: `runtime_agents` is ADDITIVE in both versions — the exact v1/v2 key set, or that set
  // plus exactly `runtime_agents` (projected strictly when present).
  const baseKeys: readonly string[] = version === 2 ? RESPONSE_KEYS_V2 : RESPONSE_KEYS;
  // D1: `delivery_presentations` is ADDITIVE and contract v2 ONLY. A v2 catalog that predates it
  // simply lacks the key (read as "not reported"); at contract v1 the key is never valid.
  exactKeys(
    raw,
    [
      ...baseKeys,
      ...('runtime_agents' in raw ? ['runtime_agents'] : []),
      ...(version === 2 && 'delivery_presentations' in raw ? ['delivery_presentations'] : []),
    ],
    'response',
  );
  const generatedAt = int(raw.generated_at, 'response.generated_at');
  const validUntil = int(raw.valid_until, 'response.valid_until');
  const maxAgeSeconds = int(raw.max_age_seconds, 'response.max_age_seconds');
  if (maxAgeSeconds < 1 || validUntil < generatedAt || validUntil > generatedAt + maxAgeSeconds) {
    malformed('response.valid_until lies outside the envelope bound');
  }
  const limits = obj(raw.limits, 'response.limits');
  const poll = limits.min_poll_interval_seconds;
  const registry = obj(raw.registry, 'response.registry');
  const snapshot = obj(raw.runtime_snapshot, 'response.runtime_snapshot');
  if (!Array.isArray(raw.sources)) malformed('response.sources is not a list');
  if (!Array.isArray(raw.missions)) malformed('response.missions is not a list');
  return {
    generatedAt,
    validUntil,
    maxAgeSeconds,
    minPollIntervalSeconds:
      typeof poll === 'number' && Number.isInteger(poll) && poll >= 1 ? poll : null,
    registry: {
      health: classify(HEALTH_STATES, key(registry, 'health', 'response.registry')),
      problem: strOrNull(
        key(registry, 'problem', 'response.registry'),
        'response.registry.problem',
      ),
      sourceCount: intOrNull(
        key(registry, 'source_count', 'response.registry'),
        'response.registry.source_count',
      ),
    },
    sources: (raw.sources as unknown[]).map((s, i) => {
      const where = `response.sources[${i}]`;
      const o = obj(s, where);
      return {
        sourceId: str(key(o, 'source_id', where), `${where}.source_id`),
        kind: str(key(o, 'kind', where), `${where}.kind`),
        producerDeclared: str(key(o, 'producer', where), `${where}.producer`),
        health: classify(HEALTH_STATES, key(o, 'health', where)),
        problem: strOrNull(key(o, 'problem', where), `${where}.problem`),
        observedAt: intOrNull(key(o, 'observed_at', where), `${where}.observed_at`),
        recordCount: intOrNull(key(o, 'record_count', where), `${where}.record_count`),
        maxAgeSeconds: intOrNull(key(o, 'max_age_seconds', where), `${where}.max_age_seconds`),
        locators: 'locators' in o ? projectLocators(o.locators, `${where}.locators`) : null,
      };
    }),
    runtimeSnapshot: {
      health: classify(HEALTH_STATES, key(snapshot, 'health', 'response.runtime_snapshot')),
      problem: strOrNull(
        key(snapshot, 'problem', 'response.runtime_snapshot'),
        'runtime_snapshot.problem',
      ),
      observedAt: intOrNull(
        key(snapshot, 'observed_at', 'response.runtime_snapshot'),
        'runtime_snapshot.observed_at',
      ),
      maxAgeSeconds: intOrNull(
        key(snapshot, 'max_age_seconds', 'response.runtime_snapshot'),
        'runtime_snapshot.max_age_seconds',
      ),
      producer: strOrNull(
        key(snapshot, 'producer', 'response.runtime_snapshot'),
        'runtime_snapshot.producer',
      ),
      taskCount: intOrNull(
        key(snapshot, 'task_count', 'response.runtime_snapshot'),
        'runtime_snapshot.task_count',
      ),
      agentCount: intOrNull(
        key(snapshot, 'agent_count', 'response.runtime_snapshot'),
        'runtime_snapshot.agent_count',
      ),
    },
    missions: (raw.missions as unknown[]).map((m, i) =>
      projectMission(m, `response.missions[${i}]`, version),
    ),
    unmatchedRuntimeAgentCount: intOrNull(
      raw.unmatched_runtime_agent_count,
      'response.unmatched_runtime_agent_count',
    ),
    runtimeAgents: projectRuntimeAgents(raw),
    deliveryPresentations: projectDeliveryPresentations(raw),
    contractVersion: version,
    chainObservation: version === 2 ? projectChainObservation(raw.chain_observation) : null,
  };
}

function projectLocators(raw: unknown, where: string): LocatorAccountView {
  const o = obj(raw, where);
  exactKeys(o, LOCATOR_ACCOUNT_KEYS, where);
  const excluded = key(o, 'excluded', where);
  if (!Array.isArray(excluded)) malformed(`${where}.excluded is not a list`);
  return {
    registeredAliases: int(o.registered_aliases, `${where}.registered_aliases`),
    physicalRoots: int(o.physical_roots, `${where}.physical_roots`),
    sharedRootGroups: int(o.shared_root_groups, `${where}.shared_root_groups`),
    rootsWithCurrentTask: int(o.roots_with_current_task, `${where}.roots_with_current_task`),
    rootsWithoutCurrentTask: int(
      o.roots_without_current_task,
      `${where}.roots_without_current_task`,
    ),
    aliasesWithoutHerdState: int(
      o.aliases_without_herd_state,
      `${where}.aliases_without_herd_state`,
    ),
    excluded: excluded.map((e, i) => {
      const w = `${where}.excluded[${i}]`;
      const x = obj(e, w);
      exactKeys(x, ['alias', 'reason'], w);
      return { alias: str(x.alias, `${w}.alias`), reason: str(x.reason, `${w}.reason`) };
    }),
  };
}

const RUNTIME_AGENTS_NOT_REPORTED: RuntimeAgentsView = {
  reported: false,
  state: null,
  problem: null,
  observedAt: null,
  firstPassAt: null,
  secondPassAt: null,
  caveat: null,
  counts: null,
  agents: [],
  agentsOverBound: 0,
};

/** item 07 — `response.runtime_agents`, STRICT when present (mission_sources/contract.py). */
function projectRuntimeAgents(raw: Json): RuntimeAgentsView {
  if (!('runtime_agents' in raw)) return RUNTIME_AGENTS_NOT_REPORTED;
  const where = 'response.runtime_agents';
  const o = obj(raw.runtime_agents, where);
  exactKeys(o, RUNTIME_AGENTS_KEYS, where);
  const state = classify(RUNTIME_AGENTS_STATES, o.state);
  if (!state.known) malformed(`${where}.state is not a declared state`);
  const observed = state.value === 'observed';
  const problem = strOrNull(o.problem, `${where}.problem`);
  if (observed !== (problem === null))
    malformed(`${where}.problem must be set exactly when not observed`);
  const caveat = strOrNull(o.caveat, `${where}.caveat`);
  if (caveat !== (observed ? RUNTIME_AGENTS_CAVEAT : null))
    malformed(`${where}.caveat must state the two-pass limit when observed`);
  const countsRaw = obj(o.counts, `${where}.counts`);
  exactKeys(countsRaw, RUNTIME_AGENTS_COUNT_KEYS, `${where}.counts`);
  const counts = {} as Record<(typeof RUNTIME_AGENTS_COUNT_KEYS)[number], number>;
  for (const k of RUNTIME_AGENTS_COUNT_KEYS) counts[k] = int(countsRaw[k], `${where}.counts.${k}`);
  if (!Array.isArray(o.agents)) malformed(`${where}.agents is not a list`);
  const agents = (o.agents as unknown[]).map((a, i) =>
    projectRuntimeAgent(a, `${where}.agents[${i}]`),
  );
  if (agents.length > 0 && !observed) malformed(`${where} lists agents while not observed`);
  return {
    reported: true,
    state,
    problem,
    observedAt: intOrNull(o.observed_at, `${where}.observed_at`),
    firstPassAt: intOrNull(o.first_pass_at, `${where}.first_pass_at`),
    secondPassAt: intOrNull(o.second_pass_at, `${where}.second_pass_at`),
    caveat,
    counts,
    agents,
    agentsOverBound: int(o.agents_over_bound, `${where}.agents_over_bound`),
  };
}

export const DELIVERY_PRESENTATIONS_NOT_REPORTED: DeliveryPresentationsView = {
  reported: false,
  state: null,
  problem: null,
  deliveryStore: null,
  total: null,
  entries: [],
};

const HEX64 = /^[0-9a-f]{64}$/;

function member<T extends string>(values: readonly T[], value: unknown, where: string): T {
  const c = classify(values, value);
  if (!c.known) malformed(`${where} is not a declared value`);
  return c.value;
}

function hex64(value: unknown, where: string): string {
  const v = str(value, where);
  if (!HEX64.test(v)) malformed(`${where} is not a sha256 hex digest`);
  return v;
}

/**
 * D1 — `response.delivery_presentations`, STRICT when present (mission_sources/contract_v2.py
 * `validate_delivery_presentations`): closed keys and vocabularies, `problem` set exactly when
 * unavailable, entries only when observed with `total` equal to them, a fixed source id and stated
 * coverage, unique proposal digests, and the unknown/authorized/link consistency rules. A block
 * that breaks any of them refuses the WHOLE response, never a partially trusted list.
 */
function projectDeliveryPresentations(raw: Json): DeliveryPresentationsView {
  if (!('delivery_presentations' in raw)) return DELIVERY_PRESENTATIONS_NOT_REPORTED;
  const where = 'response.delivery_presentations';
  const o = obj(raw.delivery_presentations, where);
  exactKeys(o, DELIVERY_PRESENTATIONS_KEYS, where);
  const state = member(DELIVERY_PRESENTATIONS_STATES, o.state, `${where}.state`);
  const problemRaw = strOrNull(o.problem, `${where}.problem`);
  const problem =
    problemRaw === null
      ? null
      : member(DELIVERY_PRESENTATIONS_PROBLEMS, problemRaw, `${where}.problem`);
  if ((state === 'unavailable') !== (problem !== null))
    malformed(`${where}.problem must be set exactly when unavailable`);
  if (o.source_id !== DELIVERY_PRESENTATIONS_SOURCE_ID)
    malformed(`${where}.source_id is not the registered source`);
  const deliveryStore = member(DELIVERY_STORE_STATES, o.delivery_store, `${where}.delivery_store`);
  const coverage = obj(o.coverage, `${where}.coverage`);
  exactKeys(coverage, DELIVERY_COVERAGE_KEYS, `${where}.coverage`);
  if (
    coverage.route !== DELIVERY_COVERAGE_ROUTE ||
    coverage.max_entries !== MAX_DELIVERY_PRESENTATIONS ||
    coverage.direct_cli !== DELIVERY_COVERAGE_DIRECT_CLI
  )
    malformed(`${where}.coverage is not the stated coverage`);
  const total = intOrNull(o.total, `${where}.total`);
  if (!Array.isArray(o.entries)) malformed(`${where}.entries is not a list`);
  if (o.entries.length > MAX_DELIVERY_PRESENTATIONS)
    malformed(`${where}.entries exceeds the contract bound`);
  if (state !== 'observed') {
    if (total !== null || o.entries.length > 0)
      malformed(`${where} carries entries while not observed`);
  } else if (total !== o.entries.length) {
    malformed(`${where}.total is not the number of entries`);
  }
  const entries = (o.entries as unknown[]).map((e, i) =>
    projectDeliveryPresentationEntry(e, `${where}.entries[${i}]`),
  );
  if (new Set(entries.map((e) => e.proposalDigest)).size !== entries.length)
    malformed(`${where}.entries repeats a proposal digest`);
  return { reported: true, state, problem, deliveryStore, total, entries };
}

function projectDeliveryPresentationEntry(
  raw: unknown,
  where: string,
): DeliveryPresentationEntryView {
  const e = obj(raw, where);
  exactKeys(e, DELIVERY_PRESENTATION_ENTRY_KEYS, where);
  if (e.source_id !== DELIVERY_PRESENTATIONS_SOURCE_ID)
    malformed(`${where}.source_id is not the registered source`);
  const mission =
    e.binding_mission === null ? null : obj(e.binding_mission, `${where}.binding_mission`);
  if (mission !== null)
    exactKeys(mission, DELIVERY_BINDING_MISSION_KEYS, `${where}.binding_mission`);
  const state = member(DELIVERY_ENTRY_STATES, e.state, `${where}.state`);
  const matching = intOrNull(e.matching_deliveries, `${where}.matching_deliveries`);
  if ((state === 'unknown') !== (matching === null))
    malformed(`${where}.matching_deliveries is unset exactly when the state is unknown`);
  if (state === 'authorized' && !matching)
    malformed(`${where} is authorized without a matching delivery`);
  const link = obj(e.link, `${where}.link`);
  exactKeys(link, DELIVERY_LINK_KEYS, `${where}.link`);
  const linkState = member(DELIVERY_LINK_STATES, link.state, `${where}.link.state`);
  const missionRef = strOrNull(link.mission_ref, `${where}.link.mission_ref`);
  if ((linkState === 'linked') !== (missionRef !== null))
    malformed(`${where}.link.mission_ref is set exactly when linked`);
  return {
    sourceId: DELIVERY_PRESENTATIONS_SOURCE_ID,
    proposalDigest: hex64(e.proposal_digest_sha256, `${where}.proposal_digest_sha256`),
    presentedAt: int(e.presented_at, `${where}.presented_at`),
    expiresAt: int(e.expires_at, `${where}.expires_at`),
    mode: member(DELIVERY_MODES, e.mode, `${where}.mode`),
    repositoryUrl: strOrNull(e.repository_url, `${where}.repository_url`),
    bindingMission:
      mission === null
        ? null
        : {
            workflowId: str(mission.workflow_id, `${where}.binding_mission.workflow_id`),
            authorizationDigest:
              mission.authorization_digest_sha256 === null
                ? null
                : hex64(
                    mission.authorization_digest_sha256,
                    `${where}.binding_mission.authorization_digest_sha256`,
                  ),
          },
    state,
    matchingDeliveries: matching,
    link: {
      state: linkState,
      missionRef,
      authorizationDigest: member(
        DELIVERY_AUTHORIZATION_CHECKS,
        link.authorization_digest,
        `${where}.link.authorization_digest`,
      ),
    },
  };
}

function projectRuntimeAgent(raw: unknown, where: string): RuntimeAgentView {
  const a = obj(raw, where);
  exactKeys(a, RUNTIME_AGENT_KEYS, where);
  const identity = obj(a.identity, `${where}.identity`);
  exactKeys(identity, ['state', 'reason'], `${where}.identity`);
  const assignment = obj(a.assignment, `${where}.assignment`);
  exactKeys(
    assignment,
    ['state', 'reason', 'task_ref', 'task_id', 'task_status'],
    `${where}.assignment`,
  );
  if (!Array.isArray(a.root_aliases)) malformed(`${where}.root_aliases is not a list`);
  str(a.workspace_realpath, `${where}.workspace_realpath`); // checked, never relayed
  const activity = strOrNull(a.activity, `${where}.activity`);
  return {
    agentRef: str(a.agent_ref, `${where}.agent_ref`),
    sourceId: str(a.source_id, `${where}.source_id`),
    rootRef: str(a.root_ref, `${where}.root_ref`),
    rootAliases: (a.root_aliases as unknown[]).map((x, i) => str(x, `${where}.root_aliases[${i}]`)),
    logical: str(a.logical, `${where}.logical`),
    role: strOrNull(a.role, `${where}.role`),
    agent: str(a.agent, `${where}.agent`),
    session: strOrNull(a.session, `${where}.session`),
    paneId: strOrNull(a.pane_id, `${where}.pane_id`),
    workspaceId: strOrNull(a.workspace_id, `${where}.workspace_id`),
    provider: strOrNull(a.provider, `${where}.provider`),
    activity: activity === null ? null : classify(ACTIVITIES, activity),
    identity: {
      state: classify(RUNTIME_IDENTITY_STATES, identity.state),
      reason: str(identity.reason, `${where}.identity.reason`),
    },
    assignment: {
      state: classify(RUNTIME_ASSIGNMENT_STATES, assignment.state),
      reason: str(assignment.reason, `${where}.assignment.reason`),
      taskRef: strOrNull(assignment.task_ref, `${where}.assignment.task_ref`),
      taskId: strOrNull(assignment.task_id, `${where}.assignment.task_id`),
      taskStatus: strOrNull(assignment.task_status, `${where}.assignment.task_status`),
    },
  };
}

function projectChainObservation(raw: unknown): CatalogResponseView['chainObservation'] {
  const where = 'response.chain_observation';
  const o = obj(raw, where);
  return {
    health: classify(HEALTH_STATES, key(o, 'health', where)),
    problem: strOrNull(key(o, 'problem', where), `${where}.problem`),
    observedAt: intOrNull(key(o, 'observed_at', where), `${where}.observed_at`),
    maxAgeSeconds: intOrNull(key(o, 'max_age_seconds', where), `${where}.max_age_seconds`),
    producer: strOrNull(key(o, 'producer', where), `${where}.producer`),
    rootCount: intOrNull(key(o, 'root_count', where), `${where}.root_count`),
    unavailableCount: intOrNull(key(o, 'unavailable_count', where), `${where}.unavailable_count`),
    overCapacityCount: intOrNull(
      key(o, 'over_capacity_count', where),
      `${where}.over_capacity_count`,
    ),
  };
}
