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
  QueryContractVersion,
  Served,
  Standing,
} from './catalogContract.js';
import {
  ACTIVITIES,
  BINDING_REASONS,
  BINDING_STATES,
  CHAIN_BINDING_REASONS,
  CHAIN_KINDS,
  classify,
  DEFAULT_QUERY_CONTRACT_VERSION,
  ENDPOINT,
  ENVELOPE_KEYS,
  HEALTH_STATES,
  LIFECYCLE_MODELS,
  MEMBER_CLASSIFICATIONS,
  MEMBER_REASONS,
  QUERY_CONTRACT,
  RESPONSE_KEYS,
  RESPONSE_KEYS_V2,
  SERVED_VALUES,
  STANDINGS,
  SUPPORTED_ENDPOINT_VERSIONS,
  SUPPORTED_QUERY_CONTRACT_VERSIONS,
  UNKNOWN_LITERAL,
  UNREPORTED_FACTS,
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
}

export function readRuntimeCapture(value: unknown): RuntimeCaptureDiagnostics {
  const o = isObject(value) ? value : {};
  const b = (v: unknown): boolean | null => (typeof v === 'boolean' ? v : null);
  const s = (v: unknown): string | null => (typeof v === 'string' ? v : null);
  const n = (v: unknown): number | null =>
    typeof v === 'number' && Number.isInteger(v) && v >= 0 ? v : null;
  return {
    configured: b(o.configured),
    available: b(o.available),
    problem: s(o.problem),
    detail: s(o.detail),
    observedAt: n(o.observed_at),
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
}

/** contract v2 — Mission State: per-kind inputs and the allowlisted canonical report facts. */
export interface MissionStateView {
  state: string;
  problem: string | null;
  detail: string | null;
  report: MissionReportView | null;
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
}

export interface MissionV2View {
  taskChains: TaskChainView[];
  childrenRecord: ChildrenRecordView;
  missionState: MissionStateView;
  canonicalReview: CanonicalReviewView;
}

export type MissionNative =
  | { kind: 'mission_core'; cancelRequested: boolean | null; paused: boolean | null }
  | { kind: 'workflow_store'; ambiguity: string | null }
  | { kind: 'unrecognized'; raw: unknown };

export type RecordedProvenance =
  | { kind: 'mission_core'; principalKind: string | null; transport: string | null }
  | { kind: 'workflow_store'; originTransport: string | null; telegramBound: boolean | null }
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
function refText(item: unknown): string {
  const json = JSON.stringify(item) as string | undefined;
  const text = typeof item === 'string' ? item : (json ?? String(item));
  return text.length > REF_TEXT_MAX ? `${text.slice(0, REF_TEXT_MAX - 1)}…` : text;
}

/** Permissive readers for the verbatim report: a wrong type reads as null, never throws. */
const rb = (v: unknown): boolean | null => (typeof v === 'boolean' ? v : null);
const rs = (v: unknown): string | null => (typeof v === 'string' ? v : null);
const ri = (v: unknown): number | null => (typeof v === 'number' && Number.isInteger(v) ? v : null);

/** Absent or null (the verbatim report may omit or null a field). */
const isNil = (v: unknown): v is null | undefined => v === null || v === undefined;

const UNKNOWN_LIST: BoundedList = { known: false, total: null, items: [] };

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
  };
}

function projectMissionV2(m: Json, where: string): MissionV2View {
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
    },
  };
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
    v2: version === 2 ? projectMissionV2(m, where) : null,
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
  exactKeys(raw, version === 2 ? RESPONSE_KEYS_V2 : RESPONSE_KEYS, 'response');
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
    contractVersion: version,
    chainObservation: version === 2 ? projectChainObservation(raw.chain_observation) : null,
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
