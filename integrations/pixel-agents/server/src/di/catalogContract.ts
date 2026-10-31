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
  // item 07: a plain Herdr task read from the engine's own task record (task-native source,
  // target_runtime/herdr_task_source.py). Never a Mission Core record.
  'herdr.task/1',
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
  // item 07 (mission_sources/roster.py): a task-native record DEFERS to a canonical record or a
  // recorded parent chain, or its team is not corroborated by any live session.
  'covered_by_canonical_record',
  'canonical_claim_conflict',
  'canonical_source_not_fresh',
  'covered_by_parent_chain',
  'task_record_not_corroborated_by_a_live_session',
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

/**
 * contract v2 — `missions[].workflow_phase`, the workflow phase the PRODUCER DECLARES as its own typed
 * field (mission_sources/contract_v2.py WORKFLOW_PHASE_KEYS / WORKFLOW_PHASES, the latter pinned by
 * the producer's tests to workflow_authority/record.py PHASES). The consumer reads that declared
 * field only; it never derives a phase from the display-only `lifecycle.state`.
 */
export const WORKFLOW_PHASE_KEYS = ['state', 'problem', 'phase'] as const;
export const WORKFLOW_PHASES = [
  'PLANNED',
  'AUTHORIZED',
  'WORKSPACE_READY',
  'PREPARED',
  'VALIDATED',
  'DISPATCHED',
  'VERIFIED',
  'COMPLETED',
  'BLOCKED',
  'NEEDS_REAUTHORIZATION',
] as const;

/**
 * item 07 — `response.runtime_agents` (mission_sources/contract.py, both contract versions): live
 * agents a task-native source's role records identify that no bound team represents. Two answers
 * are kept apart and never substitute for each other: `identity` (is this live row exactly the
 * recorded role?) and `assignment` (what the root's own task record says; never membership).
 * STRICT when present (closed keys); absent from an older producer = not reported.
 */
export const RUNTIME_AGENTS_KEYS = [
  'state',
  'problem',
  'observed_at',
  'first_pass_at',
  'second_pass_at',
  'caveat',
  'counts',
  'agents',
  'agents_over_bound',
] as const;
export const RUNTIME_AGENTS_STATES = ['observed', 'unavailable', 'not_applicable'] as const;
export const RUNTIME_AGENTS_COUNT_KEYS = [
  'roots_requested',
  'roots_read',
  'roles_recorded',
  'roles_live',
  'verified',
  'unverified',
  'ambiguous',
  'unavailable',
  'represented_by_teams',
  'roots_over_capacity',
  'names_over_bound',
  'roots_role_records_unreadable',
  // round 9: sources whose request is UNKNOWN (registry unread) — never folded into a zero
  'registries_unread',
] as const;
export const RUNTIME_AGENT_KEYS = [
  'agent_ref',
  'source_id',
  'root_ref',
  'root_aliases',
  'workspace_realpath',
  'logical',
  'role',
  'agent',
  'session',
  'pane_id',
  'workspace_id',
  'provider',
  'activity',
  'identity',
  'assignment',
] as const;
export const RUNTIME_IDENTITY_STATES = [
  'verified',
  'unverified',
  'ambiguous',
  'unavailable',
] as const;
export const RUNTIME_ASSIGNMENT_STATES = ['unassigned', 'unverified_reference'] as const;
export const RUNTIME_AGENTS_CAVEAT = 'two_pass_agreement_not_atomic_aba_not_detected';
/** item 07 — the task-native source's `locators` account (target_runtime/herdr_task_source.py). */
export const LOCATOR_ACCOUNT_KEYS = [
  'schema',
  'version',
  'registered_aliases',
  'physical_roots',
  'shared_root_groups',
  'roots_with_current_task',
  'roots_without_current_task',
  'aliases_without_herd_state',
  'excluded',
] as const;
export const HERDR_TASK_MODEL = 'herdr.task/1';

/**
 * D1 — `response.delivery_presentations` (mission_sources/contract_v2.py, contract v2 ONLY): the
 * explicitly registered, read-only reader of the Grok Bot's delivery presentations. ADDITIVE: an
 * older catalog process sends no such key, which reads "not reported by this catalog" — never
 * empty. When present it is projected STRICTLY (closed keys, closed vocabularies).
 */
export const DELIVERY_PRESENTATIONS_SOURCE_ID = 'grok_delivery_presentations';
/** `grok_bot.index.MAX_DELIVERY_RECEIPTS`, restated by the contract. */
export const MAX_DELIVERY_PRESENTATIONS = 16;
export const DELIVERY_PRESENTATIONS_KEYS = [
  'state',
  'problem',
  'source_id',
  'delivery_store',
  'coverage',
  'total',
  'entries',
] as const;
export const DELIVERY_PRESENTATIONS_STATES = ['not_registered', 'observed', 'unavailable'] as const;
export const DELIVERY_PRESENTATIONS_PROBLEMS = [
  'presentations_directory_missing',
  'presentations_directory_not_private',
  'presentations_file_absent',
  'presentations_unreadable',
  'presentations_json_invalid',
  'presentations_schema_unsupported',
  'presentations_invalid',
  'delivery_store_unreadable',
  'delivery_store_invalid',
  'reader_failed',
] as const;
export const DELIVERY_STORE_STATES = ['registered', 'not_registered'] as const;
export const DELIVERY_COVERAGE_KEYS = ['route', 'max_entries', 'direct_cli'] as const;
export const DELIVERY_COVERAGE_ROUTE = 'grok_bot';
export const DELIVERY_COVERAGE_DIRECT_CLI = 'not_stored';
export const DELIVERY_PRESENTATION_ENTRY_KEYS = [
  'source_id',
  'proposal_digest_sha256',
  'presented_at',
  'expires_at',
  'mode',
  'repository_url',
  'binding_mission',
  'state',
  'matching_deliveries',
  'link',
] as const;
export const DELIVERY_MODES = ['pull_request', 'pr_update'] as const;
export const DELIVERY_BINDING_MISSION_KEYS = [
  'workflow_id',
  'authorization_digest_sha256',
] as const;
export const DELIVERY_ENTRY_STATES = [
  'awaiting_no_recorded_authorization',
  'authorized',
  'expired_without_authorization',
  'unknown',
] as const;
export const DELIVERY_LINK_KEYS = ['state', 'mission_ref', 'authorization_digest'] as const;
export const DELIVERY_LINK_STATES = [
  'linked',
  'unlinked_no_mission_binding',
  'unlinked_no_registered_mission',
  'unlinked_ambiguous',
  'unlinked_registry_unavailable',
  'unlinked_sources_incomplete',
] as const;
export const DELIVERY_AUTHORIZATION_CHECKS = ['corroborated', 'differs', 'not_comparable'] as const;

/**
 * contract v2 — `missions[].delivery_link`, which ONE delivery record (if any) the catalog's registered delivery store links to
 * a Mission, DECLARED by the producer with the rule that established it (mission_sources/contract_v2.py MISSION_LINK_KEYS /
 * MISSION_LINK_STATES / MISSION_LINK_BASES). A workflow_authority Mission is linked by `record_mission_binding`. A Herdr task
 * is linked by `record_task_worktree_binding` when task-worktree addressing is on: the record's sealed worktree git dir AND sealed
 * engineering task id are both the task's own. An OLDER producer linked a Herdr task by `record_repository_binding` (the workspace
 * origin alone, which every worktree of that origin shares) and reported `unlinked_repository_not_recorded`; both stay VALID here so
 * an older catalog is still read, but they are a DIFFERENT, WEAKER claim and are never presented as the task-and-worktree link (the
 * wording is chosen by the basis; see diScanModel.ts). A basis or state outside these lists refuses the whole answer. Mission Core
 * stays `unlinked_model_not_recorded`.
 * `delivery_id` is set exactly when linked, of the shape a ceremony mints. ADDITIVE: an answer from a producer that predates
 * the field carries no key, and then nothing is projected (never a default). These names are distinct from DELIVERY_LINK_*,
 * which belong to a delivery PRESENTATION's link to a Mission.
 */
export const MISSION_DELIVERY_LINK_KEYS = ['state', 'delivery_id', 'basis'] as const;
export const MISSION_DELIVERY_LINK_STATES = [
  'linked',
  'unlinked_model_not_recorded',
  'unlinked_store_not_registered',
  'unlinked_store_unavailable',
  'unlinked_reader_failed',
  'unlinked_no_record',
  'unlinked_ambiguous',
  'unlinked_sources_incomplete',
  'unlinked_id_not_addressable',
  'unlinked_repository_not_recorded',
  'unlinked_task_worktree_not_recorded',
] as const;
export const MISSION_DELIVERY_LINK_BASES = [
  'record_mission_binding',
  'record_repository_binding',
  'record_task_worktree_binding',
] as const;
/**
 * contract v2 — `missions[].delivery_handoff` (mission_sources/contract_v2.py HANDOFF_*): the READ-ONLY facts a person needs before running the
 * EXISTING human delivery ceremony for a Herdr task. It confers nothing, is never "ready", and always reports the staged candidate as
 * `not_determined` and the workflow id as `human_supplied`. `link_basis` is the basis the producer DECLARES it links a Herdr task by, stated
 * whether or not anything is linked (an unlinked link's own basis is null); a client may make the canonical return-path statement only from the
 * exact canonical value, never from silence (an older producer sends no block) or an unrecognised value. ADDITIVE: an answer from a producer that
 * predates the block carries no key, and then nothing is projected (never a default).
 */
export const MISSION_DELIVERY_HANDOFF_KEYS = [
  'state',
  'link_basis',
  'repository',
  'engineering_task_id',
  'git_dir_realpath',
  'branch',
  'evidence',
  'staged_candidate',
  'workflow_id',
  'blockers',
] as const;
export const MISSION_HANDOFF_STATES = ['observed', 'not_applicable', 'not_reported'] as const;
export const HANDOFF_BRANCH_STATES = ['named', 'detached', 'unreadable'] as const;
export const HANDOFF_EVIDENCE_STATES = ['collected', 'refused', 'unreadable'] as const;
export const HANDOFF_BLOCKERS = [
  'git_dir_not_read',
  'head_detached',
  'head_not_read',
  'evidence_not_collected',
  'task_id_not_recorded',
] as const;
export const HANDOFF_MAX_REASON_CHARS = 400;
export const HANDOFF_MAX_BASIS_CHARS = 80;
/** The ONLY value of `link_basis` that declares the canonical association (the producer's `record_task_worktree_binding`). */
export const HANDOFF_CANONICAL_LINK_BASIS = 'record_task_worktree_binding';
/** The delivery id a ceremony mints: `prd-` and 24 lowercase hex (the producer's `is_delivery_id`). */
export const MISSION_DELIVERY_ID = /^prd-[0-9a-f]{24}$/;

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
export type WorkflowPhase = (typeof WORKFLOW_PHASES)[number];
export type ChainKind = (typeof CHAIN_KINDS)[number];
export type RuntimeAgentsState = (typeof RUNTIME_AGENTS_STATES)[number];
export type DeliveryPresentationsState = (typeof DELIVERY_PRESENTATIONS_STATES)[number];
export type DeliveryEntryState = (typeof DELIVERY_ENTRY_STATES)[number];
export type DeliveryLinkState = (typeof DELIVERY_LINK_STATES)[number];
export type MissionDeliveryLinkState = (typeof MISSION_DELIVERY_LINK_STATES)[number];
export type MissionDeliveryLinkBasis = (typeof MISSION_DELIVERY_LINK_BASES)[number];
export type MissionHandoffState = (typeof MISSION_HANDOFF_STATES)[number];
export type HandoffBranchState = (typeof HANDOFF_BRANCH_STATES)[number];
export type HandoffEvidenceState = (typeof HANDOFF_EVIDENCE_STATES)[number];
export type HandoffBlocker = (typeof HANDOFF_BLOCKERS)[number];
export type RuntimeIdentityState = (typeof RUNTIME_IDENTITY_STATES)[number];
export type RuntimeAssignmentState = (typeof RUNTIME_ASSIGNMENT_STATES)[number];
