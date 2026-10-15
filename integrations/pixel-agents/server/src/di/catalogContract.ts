/**
 * The DI backend read contract, as the CONSUMER needs it — names and closed value
 * sets taken VERBATIM from CONTRACT-v3 (sha256 55ee7e32…afb9), the serializer-
 * generated contract. Nothing here is derived from a fixture instance.
 *
 * Value-set policy: every enumeration below is closed by v3, but the consumer is
 * still PERMISSIVE about values it does not recognise — classify() surfaces them
 * as `{ known: false, raw }` instead of throwing or defaulting, so a future value
 * renders as "unrecognized", never as some known state.
 *
 * Case is preserved exactly as emitted: `lifecycle.state` is the source's native
 * literal (RUNNING, DISPATCHED, …), NOT an enumeration, and is never switched on;
 * decisions use `lifecycle.standing`.
 */

export const ENDPOINT = 'di.mission_catalog';
export const QUERY_CONTRACT = 'di.mission_sources.query';

/**
 * Supported versions are SETS with per-version handling (a dual-version backend is
 * planned: v1 preserved alongside a future v2). Only v1 shapes exist today; any
 * other version is `incompatible / response_contract_unsupported`, never parsed
 * best-effort. Adding a version means adding a projector for it, not a rewrite.
 */
export const SUPPORTED_ENDPOINT_VERSIONS: ReadonlySet<number> = new Set([1]);
export const SUPPORTED_QUERY_CONTRACT_VERSIONS: ReadonlySet<number> = new Set([1, 2]);
/**
 * ONE version per process, selected at LAUNCH (mirroring the backend's own launch-time
 * flag): a process reads exactly that version; any other served version is
 * `incompatible / response_contract_unsupported` — never negotiated per request.
 */
export type QueryContractVersion = 1 | 2;
export const DEFAULT_QUERY_CONTRACT_VERSION: QueryContractVersion = 1;

/** The frozen nine-key answer envelope (key set fixed; values are T | null). */
export const ENVELOPE_KEYS = [
  'detail',
  'endpoint',
  'endpoint_version',
  'problem',
  'requested_at',
  'response',
  'retry_after_seconds',
  'runtime_capture',
  'served',
] as const;

/** The eleven-key query response. */
export const RESPONSE_KEYS = [
  'contract',
  'contract_version',
  'generated_at',
  'limits',
  'max_age_seconds',
  'missions',
  'registry',
  'runtime_snapshot',
  'sources',
  'unmatched_runtime_agent_count',
  'valid_until',
] as const;

/**
 * contract v2 response = the v1 keys plus exactly `chain_observation` (bound from the EMITTED
 * key paths, exec1-v2-contract-early-b04ff40f / emitted-key-paths-v2.77006912).
 */
export const RESPONSE_KEYS_V2 = [...RESPONSE_KEYS, 'chain_observation'] as const;

/** contract v2 member and task-chain `chain` values. */
export const CHAIN_KINDS = ['direct', 'child'] as const;

export const SERVED_VALUES = ['refreshed', 'cached', 'none'] as const;

/**
 * Envelope-level `problem` values: the catalog gave NO response and says why.
 * Expected operating states, never crashes, never live. `catalog_no_runtime_root`
 * is deliberately ABSENT: v3 places it in `runtime_capture.problem` WITH a response
 * present and `served: "refreshed"` — the catalog still answers.
 */
export const ENVELOPE_PROBLEMS = [
  'catalog_slot_occupied',
  'catalog_query_not_finished_within_wait',
  'catalog_owner_shut_down',
  'catalog_query_failed',
  'catalog_refresh_throttled',
  'catalog_bad_request',
] as const;

export const HEALTH_STATES = [
  'fresh',
  'stale',
  'unavailable',
  'malformed',
  'incompatible',
] as const;
export const STANDINGS = ['active', 'inactive', 'terminal', 'unknown_future'] as const;
export const LIFECYCLE_MODELS = [
  'mission_core.mission_state/1',
  'workflow_authority.workflow_phase/2',
] as const;
export const BINDING_STATES = [
  'bound',
  'unbound',
  'disappeared',
  'ambiguous',
  'unverified',
  'not_applicable',
] as const;
export const BINDING_REASONS = [
  'binding_not_confirmed_by_both_passes',
  'canonical_chain_complete',
  'mission_not_active',
  'mission_record_ambiguity',
  'mission_workspace_inconsistent',
  'run_not_started',
  'run_outcome_unknown',
  'runtime_snapshot_not_fresh',
  'task_claimed_by_multiple_missions',
  'task_duplicated_in_snapshot',
  'task_identity_unresolved',
  'task_not_in_snapshot',
  'task_workspace_mismatch',
  'workspace_claimed_by_multiple_missions',
  'workspace_not_observed',
  'workspace_unrecorded',
] as const;
/**
 * contract v2 — `task_chains[].binding.reason` ONLY: the v1 values plus the child-chain decision
 * order's own. `missions[].binding` stays the unchanged v1 fact under v2, so it keeps
 * BINDING_REASONS and a chain-only reason there is unrecognized, never accepted.
 */
export const CHAIN_BINDING_REASONS = [
  ...BINDING_REASONS,
  'chain_membership_ended',
  'child_record_incomplete',
  'child_record_cycle',
  'child_record_stale',
  // round-3 §3 step 1a (B1): the parent binding is not confirmed unique — CHAIN-ONLY, distinct
  // from the member-position binding_not_confirmed_by_both_passes
  'parent_binding_not_confirmed',
] as const;
export const MEMBER_CLASSIFICATIONS = ['present', 'replaced', 'disappeared', 'ambiguous'] as const;
export const MEMBER_REASONS = [
  'agent_claimed_by_multiple_bindings',
  'agent_name_duplicated',
  'agent_not_live',
  'binding_duplicated',
  'live_session_shared_by_agents',
  'role_or_provider_unconfirmed',
  'session_changed',
  'session_claimed_by_multiple_bindings',
  'session_confirmed',
  'session_unconfirmed',
  'stable_identity_conflict',
  'stable_identity_unconfirmed',
] as const;
export const ACTIVITIES = ['busy', 'idle', 'blocked', 'done', 'unknown', 'unknown_future'] as const;

/** Facts the backend never reports: always the LITERAL string "unknown" in a member. */
export const UNREPORTED_FACTS = ['model', 'cost', 'tokens', 'progress'] as const;
export const UNKNOWN_LITERAL = 'unknown';

/** Fallback cadence when no response carries `limits.min_poll_interval_seconds` (v3: 5). */
export const DEFAULT_MIN_POLL_INTERVAL_SECONDS = 5;

/** A value of a closed set, or an unrecognized raw value surfaced as such. */
export type Classified<T extends string> =
  { known: true; value: T } | { known: false; raw: unknown };

export function classify<T extends string>(values: readonly T[], raw: unknown): Classified<T> {
  return typeof raw === 'string' && (values as readonly string[]).includes(raw)
    ? { known: true, value: raw as T }
    : { known: false, raw };
}

export type Health = (typeof HEALTH_STATES)[number];
export type Standing = (typeof STANDINGS)[number];
export type LifecycleModel = (typeof LIFECYCLE_MODELS)[number];
export type BindingState = (typeof BINDING_STATES)[number];
export type BindingReason = (typeof BINDING_REASONS)[number];
export type ChainBindingReason = (typeof CHAIN_BINDING_REASONS)[number];
export type MemberClassification = (typeof MEMBER_CLASSIFICATIONS)[number];
export type MemberReason = (typeof MEMBER_REASONS)[number];
export type Activity = (typeof ACTIVITIES)[number];
export type Served = (typeof SERVED_VALUES)[number];
export type ChainKind = (typeof CHAIN_KINDS)[number];
