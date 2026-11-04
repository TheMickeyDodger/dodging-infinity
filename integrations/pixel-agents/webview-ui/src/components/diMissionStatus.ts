/**
 * THE MISSION STATUS CONTRACT: one closed vocabulary for what a Mission's status is, and the DECLARED mappings, as data, from every source vocabulary the office reads onto
 * it. Every surface that says a Mission's status reads it from here and keeps no vocabulary of its own: the Missions list rows and their pills, the Missions table's status
 * filters and counts, Mission details, Reception, the office panel and its inspector, the rail, and the request panel's journey and stage. A surface that needs another
 * granularity (a pill's tone, a filter) derives it from these ids.
 *
 * THE VOCABULARY (`DI_MISSION_STATUS`). Each status has an id, a label, a SHAPE (status is never colour alone), one plain-language meaning, whether it says the run is over
 * (`terminal`), and its certainty: `current` for a confident status, or the KIND of uncertainty it is (`stale`, `not_known`, `unrecognised`, `unavailable`). An uncertain
 * status always carries its reason (`why`), and the result type refuses one without it. Nothing a source reports outside its declared vocabulary is dropped or defaulted to a
 * confident status: it is `unrecognised`, with the raw value.
 *
 * THE SOURCE OF TRUTH. For one Mission the first of these that applies decides, and everything that decided is cited in `sources` as `field = value`:
 *  1. The OFFICE (`officeStatus`): an office that is not live makes the Mission `stale` or `disconnected`. Nothing about it is current then; the sentence keeps its last known standing.
 *  2. The record's STANDING, the producer's own classification (`DI_STANDING_STATUS`): only `active`, `inactive` and `terminal` are recognised; anything else is `unrecognised`.
 *  3. The Mission State's REPORTED completion: complete without success is `failed` (reported by the source, not verified).
 *  (Mission Core's OWN PROGRESS record, `DI_MISSION_PROGRESS_STATUS`, is NOT a step here: it describes the RECORD, never execution; see `missionStatus`. It files a record
 *  closed, and decides the word only where no start was ever recorded.)
 *  4. A TERMINAL standing: the producer's own terminal literal, matched EXACTLY by lifecycle model and literal (`DI_TERMINAL_LITERAL_STATUS`), makes it `failed` or `stopped`;
 *     otherwise the Mission State's verified success makes it `verified`; otherwise `ended`. A record whose run never started keeps its terminal status and says so.
 *  5. The TEAM (active standing only): an observed roster, an established binding and members live AND confirmed as themselves are the ONLY observation that work is
 *     happening now, and the only thing that makes a Mission `running`. A confirmed live member whose terminal reports blocked makes it `blocked`; otherwise `running`.
 *  6. The RUN-START evidence: a workflow store's declared phase (`DI_WORKFLOW_PHASE_STATUS`, the producer's own lifecycle; a phase outside it is `unrecognised`, with the
 *     declared value cited, never a fall-through to the next fact), then the catalog's execution fact `runStarted`
 *     (`execution.run_started`, verbatim). A record with NO start recorded is `not_started`; a dispatched one is `start_requested`. `runStarted` can only ever say that
 *     nothing started: `true` is never evidence that a worker started (`DI_RUN_STARTED_MEANING`: for a Herdr task it means only that a task record exists).
 *  7. Otherwise the standing decides: `inactive` is `waiting`. An `active` record whose binding to its task is established but with no confirmed live member is
 *     `task_observed` (`DI_BOUND_TASK_STATUS`: its bound task is observed, its workers are not), never `running`; a roster the source could not provide is `unavailable`;
 *     anything else is `unknown`.
 * WHEN SOURCES DISAGREE the status is `unknown` (not known), with both sides cited, never the stronger reading: a record that says no run started while team members are
 * confirmed live; a workflow phase before dispatch with a recorded run start; a verified phase with none.
 *
 * WHAT IS NEVER AN INPUT. `lifecycleState` is a NATIVE literal (catalogProjection.ts: verbatim and case-preserved): it is displayed, and consulted in exactly two places,
 * each by exact match: the terminal-literal table of step 4 under a terminal standing, and the capacity release bases (`DI_CAPACITY_RELEASE`) under their declared
 * lifecycle model; never by substring, never case-folded. A terminal's own `activity`
 * (`done`, `busy`, …) is never Mission completion. A task record, a start intent and a task observation are never evidence that a worker started. The progression probe
 * (`DI_PROGRESSION_PROBE_STATUS`) is EVIDENCE OF LIVENESS of a PROCESS, never about a Mission (target_runtime/mission_progress_status.py): a healthy runtime is NEVER
 * evidence that a specific Mission started; it never changes a status, and only says how current the record shown is likely to be. Mine and Team are an ownership SCOPE,
 * never a status. Two records that look alike stay two records: nothing here keys on a name or an objective.
 */
import type { DiMember, DiMission, DiOfficeState } from '../../../core/src/messages.js';
import type { MrProgression, MrProgressionState } from './diMissionRequestModel.js';
import { isNormalWaiting } from './diOfficeFormat.js';
import type { DiScanKnown, DiScanWhy } from './diScanModel.js';

// ── The vocabulary ───────────────────────────────────────────────────────────────────────────

export const DI_MISSION_STATUS_IDS = [
  'not_started',
  'start_requested',
  'task_observed',
  'running',
  'waiting',
  'blocked',
  'failed',
  'stopped',
  'ended',
  'verified',
  'stale',
  'disconnected',
  'unknown',
  'unrecognised',
  'unavailable',
] as const;
export type DiMissionStatusId = (typeof DI_MISSION_STATUS_IDS)[number];

/** The statuses that are NOT a current, confident reading. Each carries its reason. */
export type DiMissionUncertainStatus =
  'stale' | 'disconnected' | 'unknown' | 'unrecognised' | 'unavailable';
export type DiMissionCurrentStatus = Exclude<DiMissionStatusId, DiMissionUncertainStatus>;

export interface DiMissionStatusEntry {
  /** Short words for the badge and the pill. */
  label: string;
  /** A distinct shape: the status is legible without colour. */
  glyph: string;
  /** What the status means, in one plain sentence. */
  meaning: string;
  /** The status by itself says the run is over. */
  terminal: boolean;
  /** `current` for a confident status; otherwise the kind of uncertainty its reason has. */
  certainty: 'current' | Exclude<DiScanWhy['kind'], 'not_read'>;
}

export const DI_MISSION_STATUS: Readonly<Record<DiMissionStatusId, DiMissionStatusEntry>> = {
  not_started: {
    label: 'Not started',
    glyph: '□',
    meaning: 'No start was ever recorded for this Mission, so nothing is working on it.',
    terminal: false,
    certainty: 'current',
  },
  start_requested: {
    label: 'Start requested',
    glyph: '◔',
    meaning: 'A start was recorded and a team was asked for; DI has not observed the team running.',
    terminal: false,
    certainty: 'current',
  },
  task_observed: {
    label: 'Task observed',
    glyph: '◎',
    meaning:
      'Its bound task is observed (the task record and its team binding are established), but no team member is confirmed live: that is not evidence any worker started.',
    terminal: false,
    certainty: 'current',
  },
  running: {
    label: 'Running',
    glyph: '●',
    meaning:
      'Its team is observed working: team members are live and confirmed as themselves, and at least one reports work (its terminal reports busy).',
    terminal: false,
    certainty: 'current',
  },
  waiting: {
    label: 'Waiting',
    glyph: '◐',
    meaning:
      'Not active right now: the record waits, for a decision, a reauthorization, a new revision or a next task; or its team is observed live but no member reports work (each reports idle or done).',
    terminal: false,
    certainty: 'current',
  },
  blocked: {
    label: 'Blocked',
    glyph: '▲',
    meaning:
      'A live, confirmed team member’s terminal reports blocked (the terminal’s own report).',
    terminal: false,
    certainty: 'current',
  },
  failed: {
    label: 'Failed',
    glyph: '✕',
    meaning:
      'The record reports the work finished without success (reported by the source, not verified).',
    terminal: true,
    certainty: 'current',
  },
  stopped: {
    label: 'Stopped',
    glyph: '⊘',
    meaning:
      'The record says its run was stopped, cancelled or abandoned before it finished; that is the record’s outcome, not an observation that its workers stopped. Stopped is not verified success.',
    terminal: true,
    certainty: 'current',
  },
  ended: {
    label: 'Ended',
    glyph: '■',
    meaning: 'The record has ended. Ended is not verified success.',
    terminal: true,
    certainty: 'current',
  },
  verified: {
    label: 'Verified',
    glyph: '◆',
    meaning:
      'DI verified the run’s result. Verified is not delivered: delivery stays a separate, human step.',
    terminal: true,
    certainty: 'current',
  },
  stale: {
    label: 'Stale',
    glyph: '◇',
    meaning: 'Last known only: the office is not live, so its status is not current.',
    terminal: false,
    certainty: 'stale',
  },
  disconnected: {
    label: 'Disconnected',
    glyph: '◌',
    meaning: 'The office is disconnected: the last known status is shown and is not current.',
    terminal: false,
    certainty: 'stale',
  },
  unknown: {
    label: 'Unknown',
    glyph: '○',
    meaning: 'Not known: what was read cannot decide it, or what would decide it was not read.',
    terminal: false,
    certainty: 'not_known',
  },
  unrecognised: {
    label: 'Unrecognised',
    glyph: '?',
    meaning:
      'A source reported a value outside its declared vocabulary, so its status is not decided.',
    terminal: false,
    certainty: 'unrecognised',
  },
  unavailable: {
    label: 'Unavailable',
    glyph: '⊗',
    meaning: 'A source that decides it said it cannot provide it right now.',
    terminal: false,
    certainty: 'unavailable',
  },
};

interface DiMissionStatusFace {
  label: string;
  glyph: string;
  meaning: string;
  terminal: boolean;
  /** One plain-language sentence about THIS Mission. */
  sentence: string;
  /** The exact fields that produced it, as `field = value`. */
  sources: string[];
  /**
   * A qualification RENDERED beside the status on every face that shows the Mission's detail, whatever the status (round 5, finding 2): a reported completion whose
   * success is not affirmed (`DI_COMPLETION_NOT_AFFIRMED`). Never folded into `sentence`, which a Running face does not show. Null when there is none.
   */
  qualification: string | null;
  /**
   * How this Mission bears on the Orchestrator capacity guard (`capacityOf`): it HOLDS one (execution evidence: a team observed, a bound task observed, a requested
   * start), holds NONE (only on a declared, source-grounded positive basis that its execution ended, `DI_CAPACITY_RELEASE`, its source field named), or it is NOT KNOWN,
   * with its reason. Never derived from filing, from a fresh answer, or from a roster that lists no members (the roster WITHHOLDS a non-active record's members; it stops
   * nothing).
   */
  capacity: DiCapacityReading;
}
export type DiCapacityBasis =
  | 'cancel_quiescent'
  | 'no_start_recorded'
  | 'engine_task_stopped'
  | 'verified_target_stopped'
  | 'workflow_target_stopped'
  | 'workflow_not_dispatched'
  | 'workflow_before_dispatch';
export type DiCapacityReading =
  | { bears: 'holds'; basis: string }
  | { bears: 'none'; basis: DiCapacityBasis; source: string }
  | { bears: 'not_known'; why: DiScanWhy };

/** A current status has no reason to give; an uncertain one always has its reason (the union refuses one without it). */
export type DiMissionStatusResult =
  | (DiMissionStatusFace & { status: DiMissionCurrentStatus; why: null })
  | (DiMissionStatusFace & { status: DiMissionUncertainStatus; why: DiScanWhy });

/** A result's capacity before `missionStatus` reads the Mission's own evidence (`capacityOf`): an execution status holds; anything else is not known here. */
const defaultCapacity = (status: DiMissionStatusId): DiCapacityReading =>
  DI_STATUS_CAPACITY[status] === 'holds'
    ? { bears: 'holds', basis: DI_MISSION_STATUS[status].meaning }
    : { bears: 'not_known', why: { kind: 'not_known', text: 'nothing read here establishes whether it holds an Orchestrator' } };

const face = (
  status: DiMissionStatusId,
  sentence: string,
  sources: string[],
  capacity: DiCapacityReading = defaultCapacity(status),
): DiMissionStatusFace => {
  const entry = DI_MISSION_STATUS[status];
  return {
    capacity,
    label: entry.label,
    glyph: entry.glyph,
    meaning: entry.meaning,
    terminal: entry.terminal,
    sentence,
    sources,
    qualification: null,
  };
};
function current(
  status: DiMissionCurrentStatus,
  sentence: string,
  sources: string[],
  capacity?: DiCapacityReading,
): DiMissionStatusResult {
  return { ...face(status, sentence, sources, capacity), status, why: null };
}
function uncertain(
  status: DiMissionUncertainStatus,
  why: DiScanWhy,
  sentence: string,
  sources: string[],
  capacity?: DiCapacityReading,
): DiMissionStatusResult {
  return { ...face(status, sentence, sources, capacity), status, why };
}

const field = (name: string, value: string | number | boolean | null): string =>
  `${name} = ${value === null ? 'null' : typeof value === 'string' ? JSON.stringify(value) : String(value)}`;

// ── The declared mappings ────────────────────────────────────────────────────────────────────

/** The status a standing gives when nothing more specific decides (steps 2 and 7). Source field: `mission.standing`. */
export const DI_STANDING_STATUS = {
  active: 'unknown',
  inactive: 'waiting',
  terminal: 'ended',
} as const satisfies Record<string, DiMissionStatusId>;
type DiStanding = keyof typeof DI_STANDING_STATUS;

/**
 * Step 4: each producer's own TERMINAL literals, by lifecycle model, exactly as the producer writes them (source fields: `mission.lifecycleModel`, `mission.lifecycleState`,
 * under `mission.standing = "terminal"`). Every terminal literal of each model is declared, including those that are simply `ended`; a literal outside the table is `ended`.
 */
export const DI_TERMINAL_LITERAL_STATUS: Readonly<
  Record<string, Readonly<Record<string, 'failed' | 'stopped' | 'ended'>>>
> = {
  // mission_sources/sources.py MISSION_CORE_STANDING (terminal rows); mission/record.py STATE_*
  'mission_core.mission_state/1': {
    BLOCKED: 'stopped',
    CANCELLED: 'stopped',
    COMPLETED: 'ended',
    CLOSED: 'ended',
    REJECTED: 'ended',
  },
  // workflow_authority/record.py TERMINAL_PHASES
  'workflow_authority.workflow_phase/2': { BLOCKED: 'stopped', COMPLETED: 'ended' },
  // herdr/vintage.py TERMINAL_TASK_STATUSES, and ABORTED (target_runtime/herdr_task_source.py standing)
  'herdr.task/1': {
    FAILED: 'failed',
    ABORTED: 'stopped',
    ABANDONED: 'stopped',
    COMPLETE: 'ended',
    COMPLETED: 'ended',
  },
};

/**
 * Step 6: a workflow store's DECLARED phase (workflow_authority/record.py PHASES, one linear lifecycle: PLANNED → AUTHORIZED → WORKSPACE_READY → PREPARED → VALIDATED →
 * DISPATCHED → VERIFIED → COMPLETED, with BLOCKED and NEEDS_REAUTHORIZATION). Source field: `v2.workflowPhase.phase`, read only for `sourceKind = "workflow_store"`.
 */
export const DI_WORKFLOW_PHASE_STATUS = {
  PLANNED: 'not_started',
  AUTHORIZED: 'not_started',
  WORKSPACE_READY: 'not_started',
  PREPARED: 'not_started',
  VALIDATED: 'not_started',
  DISPATCHED: 'start_requested',
  VERIFIED: 'verified',
  COMPLETED: 'ended',
  BLOCKED: 'stopped',
  NEEDS_REAUTHORIZATION: 'waiting',
} as const satisfies Record<string, DiMissionCurrentStatus>;

/**
 * Step 6: what each source's `execution.run_started` (on the wire `runStarted`) MEANS, as its producer computes it. In no source does it mean that a worker started, so a
 * `true` never makes anything running; only `false` decides anything (`not_started`).
 *  - `herdr_task` (target_runtime/herdr_task_source.py `_view`): `true` for EVERY existing task record, unconditionally, and `receipt_recorded` is the same value. It means
 *    "a task record exists", NOT "a run started"; `false` only for a root with no current task record.
 *  - `mission_core` (mission_sources/sources.py): `true` when a start INTENT is recorded (written before the effect was requested).
 *  - `workflow_store` (mission_sources/sources.py): `true` when a target engine is recorded.
 * Source field: `mission.runStarted`, read with `mission.sourceKind`.
 */
export const DI_RUN_STARTED_MEANING = {
  herdr_task: {
    true: 'a task record exists',
    false: 'there is no current task record at this root',
  },
  mission_core: { true: 'a start intent is recorded', false: 'no start intent is recorded' },
  workflow_store: { true: 'a target engine is recorded', false: 'no target engine is recorded' },
} as const satisfies Record<string, { true: string; false: string }>;

/**
 * Step 7: an ACTIVE record whose binding to its task is established (`bound`: the producer's canonical chain from the record to its task and roster is complete; for a
 * Herdr task, at least one recorded role was held by a live agent on its recorded session) but with NO member live and confirmed as itself. Its bound task is observed and
 * its workers are not: `task_observed`, never `running`. Source fields: `mission.standing`, `mission.bindingState`, `mission.bindingReason`, `members[].live`,
 * `members[].classification`, and `mission.runStarted` with its meaning when it is reported.
 */
export const DI_BOUND_TASK_STATUS = {
  standing: 'active',
  bindingState: 'bound',
  confirmedLive: 0,
  status: 'task_observed',
} as const;

const runMeaning = (mission: DiMission, value: boolean): string | null => {
  const meaning = (DI_RUN_STARTED_MEANING as Record<string, { true: string; false: string }>)[
    mission.sourceKind
  ];
  return meaning === undefined ? null : value ? meaning.true : meaning.false;
};

/**
 * Step 3b: Mission Core's OWN progress record (`v2.missionState.progress`, from `mission_state.report.progress`; mission/state.py PROGRESS_STATES), a lifecycle SEPARATE
 * from the outer Mission state. TERMINAL progress (mission/state.py TERMINAL_PROGRESS_STATES) closes the Mission whatever the outer state says. Non-terminal progress (null
 * here) decides nothing by itself: IN_PROGRESS is a progress record, not evidence that a worker started, and a COMPLETED outer record may still show IN_PROGRESS
 * (local_request/surface.py RUN_PROGRESS_NOTE). Source fields: `v2.missionState.progress.progress`, `.closureReason`, `.unrecognized`, `.state`, `.reason`.
 */
export const DI_MISSION_PROGRESS_STATUS = {
  NOT_STARTED: null,
  IN_PROGRESS: null,
  BLOCKED: null,
  COMPLETED: 'ended',
  CLOSED_UNSUCCESSFUL: 'stopped',
  ABANDONED: 'stopped',
} as const satisfies Record<string, 'ended' | 'stopped' | null>;
type DiProgressLiteral = keyof typeof DI_MISSION_PROGRESS_STATUS;
const isProgress = (value: string): value is DiProgressLiteral =>
  Object.prototype.hasOwnProperty.call(DI_MISSION_PROGRESS_STATUS, value);

/** Mission Core's terminal progress for this Mission, with the status it maps to, or null (no Mission State, no progress, or progress that is not terminal). */
function terminalProgress(
  mission: DiMission,
): { literal: DiProgressLiteral; status: 'ended' | 'stopped' } | null {
  const progress = mission.v2.projected ? mission.v2.missionState.progress : undefined;
  if (progress === undefined || progress.state !== 'observed' || progress.progress === null)
    return null;
  if (!isProgress(progress.progress)) return null;
  const status = DI_MISSION_PROGRESS_STATUS[progress.progress];
  return status === null ? null : { literal: progress.progress, status };
}

/**
 * Whether the Mission's RECORD is over, for FILING only (the Closed tab, the Done filter): its classified standing is terminal, or Mission Core's own progress is terminal.
 * It says nothing about execution and nothing about capacity: a record its progress closed can still have live workers holding an Orchestrator (the capacity guard reads
 * each status's own `capacity`, never this). A stale office keeps the last known answer.
 */
export function recordClosed(mission: DiMission): boolean {
  return mission.standing === 'terminal' || terminalProgress(mission) !== null;
}

/**
 * How each status bears on the Orchestrator limit (AC-4.3-14). An EXECUTION status (`holds`: a team observed working or blocked, a bound task observed, a requested start)
 * is itself the evidence and holds an Orchestrator. Every other status `needs_basis`: it holds NONE only on a declared positive basis (`DI_CAPACITY_RELEASE`) and is
 * otherwise NOT KNOWN, with its reason. The capacity GUARD counts held and not known alike, so an unknown never reads as spare capacity; it is a separate input from the
 * DISPLAY count (the Running filter counts confirmed-live Running only). How a record is FILED, whether the answer is fresh, and whether its roster lists members never
 * free capacity by themselves.
 */
export const DI_STATUS_CAPACITY: Readonly<Record<DiMissionStatusId, 'holds' | 'needs_basis'>> = {
  not_started: 'needs_basis',
  start_requested: 'holds',
  task_observed: 'holds',
  running: 'holds',
  waiting: 'needs_basis',
  blocked: 'holds',
  failed: 'needs_basis',
  stopped: 'needs_basis',
  ended: 'needs_basis',
  verified: 'needs_basis',
  stale: 'needs_basis',
  disconnected: 'needs_basis',
  unknown: 'needs_basis',
  unrecognised: 'needs_basis',
  unavailable: 'needs_basis',
};

/**
 * The ONLY positive bases on which a Mission holds NO Orchestrator, each naming the source field that affirmatively establishes that its execution ended (round 4, finding
 * 2). Anything else is not known, whatever its status word: a terminal STANDING alone is no basis (Mission Core makes a failed verification terminal BLOCKED, including
 * `verify_target_not_stopped`, whose target is still running; mission/service.py verify, mission/record.py VERIFY_CONJUNCTS), a FRESH answer is no basis (freshness says
 * the answer is current, not that execution ended), and a roster that lists no members is no basis (the roster withholds a non-active record's members; it stops nothing).
 *  - `engine_task_stopped`: `mission.lifecycleState`, under `mission.lifecycleModel = "herdr.task/1"`, in herd's OWN stopped set (herdr/tasks.py treats these alike as "the
 *    prior task has stopped"; DI's own verification reads the same set for `target_stopped`, target_runtime/broker.py `_TARGET_TERMINAL_STATUSES`). A stopped task record
 *    never resumes (new work is a new record), so it holds while the office is not live too. The vintage literals herd does not write (COMPLETED, ABANDONED, FAILED) are no
 *    basis.
 *  - `cancel_quiescent` (Deliverable 2): `mission.cancelQuiescence` in `cancelQuiescent`, on a Mission Core record CANCELLED (terminal): the bridge's cancel
 *    completion records that quiescence only from an affirmative observation (target_runtime/mission_bridge.py `_complete_cancel`). Terminal, so it holds while
 *    the office is not live too. The backend rechecks the same basis from the record itself (mission_retention/eligibility.py, pinned to this table).
 *  - `verified_target_stopped`: `mission.lifecycleState = "COMPLETED"` under `mission_core.mission_state/1`: Mission Core records COMPLETED only from a verification whose
 *    every conjunct held, `target_stopped` included. Terminal, so it holds while the office is not live too.
 *  - `workflow_target_stopped`: a workflow store's observed `v2.workflowPhase.phase` VERIFIED or COMPLETED: the broker's only VERIFIED write follows its observation that
 *    the target task stopped (target_runtime/broker.py `_verify`: `target_complete`, herd's stopped set), VERIFIED is reached only from DISPATCHED and COMPLETED only from
 *    VERIFIED (workflow_authority/record.py), and neither dispatches again. BLOCKED alone is NO basis: the broker also writes it while the target may still run (its
 *    readiness gate); only its receipts say whether anything was ever sent (below).
 *  - `no_start_recorded`: `mission.runStarted = false`, ONLY for a source that records the start BEFORE the effect (`noStartRecordedBySource`, round 5, finding 1),
 *    so its absence is a recorded fact that no effect was requested: Mission Core (the bridge records the run intent durably before its only spawn,
 *    target_runtime/mission_bridge.py) and a Herdr root with no task record (herd saves the task record before it prompts the supervisor, herdr/tasks.py, and nothing
 *    in herd deletes one). In a LIVE office; while it is not live, only for a TERMINAL record (a terminal record has no way to record a start later).
 *    A WORKFLOW's `runStarted` is NOT evidence: it is whether `target_engine` is recorded (mission_sources/sources.py), and the broker records that AFTER the external
 *    spawn, so a post-effect exception leaves the workflow BLOCKED with no engine and work started (target_runtime/broker.py `_dispatch`).
 *  - `workflow_not_dispatched` (Deliverable 2, the capacity seam): a workflow store's terminal BLOCKED record with `mission.sentHandoffReceipts = 0`: the broker saves a
 *    durable dispatch receipt BEFORE every spawn (target_runtime/broker.py) and the record refuses, never evicts, past its receipt bound (workflow_authority/record.py), so
 *    zero is a recorded fact that nothing was ever sent. The SAME receipts the backend's retention reads (mission_retention/eligibility.py, the same name), pinned case by
 *    case by tests/test_mission_retention.py E8. Terminal and durable, so it holds while the office is not live too. With any receipt, BLOCKED stays no basis.
 *  - `workflow_before_dispatch`: a workflow store's observed `v2.workflowPhase.phase` BEFORE DISPATCHED (`workflowUndispatchedPhases`): the broker saves DISPATCHED
 *    durably BEFORE it calls the spawn, so a phase before it is a recorded fact that no spawn was made. In a LIVE office only (it may have dispatched since). A capacity
 *    reading of an UNFINISHED record, which retention never archives: declared office-only (`DI_CAPACITY_ASYMMETRIC`). NEEDS_REAUTHORIZATION is reachable after
 *    DISPATCHED too, so it is no basis.
 * Live evidence always comes first: an execution status, or team members confirmed live, holds whatever a basis says. `capacityReleaseBasis` is the one reading of these
 * bases; anything that frees capacity or retention on them (D2) reuses it.
 */
export const DI_CAPACITY_RELEASE = {
  herdrModel: 'herdr.task/1',
  herdrStoppedStatuses: ['COMPLETE', 'ABORTED', 'ERROR'],
  missionCoreModel: 'mission_core.mission_state/1',
  missionCoreVerifiedState: 'COMPLETED',
  missionCoreCancelledState: 'CANCELLED',
  /**
   * Deliverable 2: the recorded cancel quiescence values that AFFIRMATIVELY establish execution ended or never began (mission/record.py `CANCEL_QUIESCENCE_*`):
   * `nothing_started` (cancelled before any run intent), `owned_group_reaped` (the owned process group was reaped, or observed already gone) and
   * `task_observed_stopped` (the target task was observed stopped). `unproven` is not a basis; nothing else is either.
   */
  cancelQuiescent: ['nothing_started', 'owned_group_reaped', 'task_observed_stopped'],
  workflowStoppedPhases: ['VERIFIED', 'COMPLETED'],
  workflowUndispatchedPhases: ['PLANNED', 'AUTHORIZED', 'WORKSPACE_READY', 'PREPARED', 'VALIDATED'],
  /** The terminal phase whose receipts decide it: `workflow_not_dispatched` when `sentHandoffReceipts = 0`, no basis otherwise. */
  workflowBlockedPhase: 'BLOCKED',
  /** The sources whose `runStarted = false` is a recorded pre-effect fact, each with where the producer records the start before the effect. */
  noStartRecordedBySource: {
    mission_core: 'target_runtime/mission_bridge.py: the run intent is recorded durably before the only spawn',
    herdr_task: 'herdr/tasks.py: the task record is saved before the supervisor is prompted; nothing in herd deletes one',
  },
} as const;

/**
 * The capacity bases with NO backend-retention mirror, each with why (tests/test_mission_retention.py E8 fails on any basis that is neither mirrored case by case nor
 * declared here). Every other basis rests on the same record fact as its namesake in mission_retention/eligibility.py.
 */
export const DI_CAPACITY_ASYMMETRIC: Readonly<Partial<Record<DiCapacityBasis, string>>> = {
  engine_task_stopped: 'a Herdr task lives in a store DI does not own; no backend retention reads it',
  workflow_before_dispatch:
    'a workflow before DISPATCHED is not finished: the office frees its capacity while live, and retention (terminal records only) never archives it',
};

const CAPACITY_NO_BASIS = 'nothing in its record establishes that its execution ended';
const CAPACITY_SOURCES_DISAGREE = 'its sources disagree, so nothing read here establishes that its execution ended';
const CAPACITY_SEEN_LIVE_UNCONFIRMED =
  'a member is seen live but not confirmed as itself, which contradicts its record, so nothing read here establishes that its execution ended';
const CAPACITY_SEEN_LIVE_STALE =
  'the office is not live, and its last known record had team members live, so nothing establishes that its execution ended';
const CAPACITY_NO_BASIS_STALE =
  'the office is not live, and nothing in its last known record establishes that its execution ended';

/**
 * The declared positive basis for capacity NONE (`DI_CAPACITY_RELEASE`), with its source field, or null when nothing establishes that its execution ended. Exported as the
 * ONE reading of these bases, for anything else that frees capacity or retention on them.
 */
export function capacityReleaseBasis(mission: DiMission, live: boolean): { basis: DiCapacityBasis; source: string } | null {
  if (!isStanding(mission.standing)) return null;
  const rule = DI_CAPACITY_RELEASE;
  if (
    mission.lifecycleModel === rule.herdrModel &&
    (rule.herdrStoppedStatuses as readonly string[]).includes(mission.lifecycleState)
  )
    return { basis: 'engine_task_stopped', source: field('mission.lifecycleState', mission.lifecycleState) };
  if (
    mission.lifecycleModel === rule.missionCoreModel &&
    mission.standing === 'terminal' &&
    mission.lifecycleState === rule.missionCoreVerifiedState
  )
    return { basis: 'verified_target_stopped', source: field('mission.lifecycleState', mission.lifecycleState) };
  if (
    mission.lifecycleModel === rule.missionCoreModel &&
    mission.standing === 'terminal' &&
    mission.lifecycleState === rule.missionCoreCancelledState &&
    typeof mission.cancelQuiescence === 'string' &&
    (rule.cancelQuiescent as readonly string[]).includes(mission.cancelQuiescence)
  )
    // a terminal record: it holds while the office is not live too
    return { basis: 'cancel_quiescent', source: field('mission.cancelQuiescence', mission.cancelQuiescence) };
  const phase = declaredPhase(mission);
  if (phase !== null && (rule.workflowStoppedPhases as readonly string[]).includes(phase.phase))
    return { basis: 'workflow_target_stopped', source: field('v2.workflowPhase.phase', phase.phase) };
  if (
    phase !== null &&
    phase.phase === rule.workflowBlockedPhase &&
    mission.standing === 'terminal' &&
    mission.sentHandoffReceipts === 0
  )
    // the backend's retention reads these same receipts; terminal and durable, so it holds while the office is not live too
    return { basis: 'workflow_not_dispatched', source: field('mission.sentHandoffReceipts', 0) };
  if (live && phase !== null && (rule.workflowUndispatchedPhases as readonly string[]).includes(phase.phase))
    return { basis: 'workflow_before_dispatch', source: field('v2.workflowPhase.phase', phase.phase) };
  if (
    mission.runStarted === false &&
    Object.prototype.hasOwnProperty.call(rule.noStartRecordedBySource, mission.sourceKind) &&
    (live || mission.standing === 'terminal')
  )
    return { basis: 'no_start_recorded', source: field('mission.runStarted', false) };
  return null;
}

/**
 * Deliverable 2: whether a Mission is ARCHIVED (a VIEW change, made the moment it is finished or cancelled WITH a positive basis; nothing is deleted by it), and why.
 * One rule, the backend's: a Mission of a store DI owns (Mission Core, a workflow store) is archived exactly when the catalog's `retention` reading says it is
 * eligible (the backend rechecked the record itself: terminal standing or terminal progress, a positive basis named by its source field, and, on progress closure, that
 * no live contract could still dispatch it), or that its payload is already being or has been reclaimed or deleted. A reading that is not reported or unavailable keeps
 * the Mission in the active filters: uncertainty is never permission. A Herdr task (a store DI does not own, `retention` not applicable) is archived by Deliverable 1's
 * own reading: its record closed AND a positive capacity-release basis (`capacityReleaseBasis`).
 */
export type DiMissionArchive = {
  archived: boolean;
  /** The retention phase when the Mission's payload is being or has been removed; null while intact. */
  reduced: 'reclaiming' | 'reclaimed' | 'deleting' | 'deleted' | null;
  why: string;
  sources: string[];
};
const REDUCED_PHASES = ['reclaiming', 'reclaimed', 'deleting', 'deleted'] as const;
const DI_OWNED_MODELS: readonly string[] = [DI_CAPACITY_RELEASE.missionCoreModel, 'workflow_authority.workflow_phase/2'];

export function missionArchive(mission: DiMission, state: DiOfficeState): DiMissionArchive {
  const retention = mission.retention;
  if (retention !== undefined && retention.state === 'observed') {
    const reduced = (REDUCED_PHASES as readonly string[]).includes(retention.phase ?? '')
      ? (retention.phase as DiMissionArchive['reduced'])
      : null;
    if (reduced !== null)
      return { archived: true, reduced, why: `its local data is ${reduced}`, sources: [field('retention.phase', retention.phase)] };
    if (retention.eligible === true)
      return {
        archived: true,
        reduced: null,
        why: `finished or cancelled, and ${retention.basisSource ?? 'its record'} establishes that its execution ended or never began`,
        sources: [field('retention.eligible', true), field('retention.basis', retention.basis)],
      };
    return {
      archived: false,
      reduced: null,
      why: 'it is not eligible to archive yet',
      sources: [field('retention.eligible', false), field('retention.reason', retention.reason)],
    };
  }
  if (DI_OWNED_MODELS.includes(mission.lifecycleModel))
    return {
      archived: false,
      reduced: null,
      why: 'the backend did not report whether it is eligible, so it stays with the active Missions',
      sources: [field('retention.state', retention?.state ?? 'absent')],
    };
  const live = officeStatus(state).status === 'running';
  const basis = recordClosed(mission) ? capacityReleaseBasis(mission, live) : null;
  return basis === null
    ? { archived: false, reduced: null, why: 'nothing establishes that its execution ended', sources: [field('mission.standing', mission.standing)] }
    : { archived: true, reduced: null, why: 'its record is over and its execution ended', sources: [basis.source] };
}

/**
 * A Mission's capacity reading from its EXECUTION status and its own evidence: live evidence holds; a declared positive basis frees; everything else is not known, with its
 * reason (stale when the office is not live). Never from filing, freshness or an empty roster.
 */
function capacityOf(mission: DiMission, state: DiOfficeState, executionId: DiMissionStatusId): DiCapacityReading {
  const live = officeStatus(state).status === 'running';
  if (DI_STATUS_CAPACITY[executionId] === 'holds')
    return { bears: 'holds', basis: DI_MISSION_STATUS[executionId].meaning };
  // Deliverable 2, the capacity seam (the lead accepted the principle): a confirmed-live team holds WHATEVER the record's standing. A terminal record with a
  // confirmed-live team is evidence that contradicts the record, never a release.
  const teamLive =
    live &&
    mission.members.state === 'observed' &&
    mission.bindingState === 'bound' &&
    mission.members.members.some(isConfirmedLive);
  if (teamLive) return { bears: 'holds', basis: 'team members are observed live and confirmed as themselves' };
  // where its sources DISAGREE (`unknown`), no one of them is taken as establishing anything
  if (executionId === 'unknown') return { bears: 'not_known', why: { kind: 'not_known', text: CAPACITY_SOURCES_DISAGREE } };
  const basis = capacityReleaseBasis(mission, live);
  if (basis !== null) {
    // A member SEEN live contradicts a release it did not establish: a stale office whose last good observation had the team live (an unknown never becomes
    // spare capacity), or a live member not confirmed as itself. The basis then frees nothing. An ordinary finished record has no members at all: the catalog
    // withholds a non-active record's members (mission_sources/roster.py, binding not_applicable `mission_not_active`; pinned by tests/test_mission_sources.py
    // `test_eligibility_alone_never_creates_members`), so ordinary release is unchanged.
    if (mission.members.state === 'observed' && mission.members.members.some((m) => m.live))
      return {
        bears: 'not_known',
        why: live ? { kind: 'not_known', text: CAPACITY_SEEN_LIVE_UNCONFIRMED } : { kind: 'stale', text: CAPACITY_SEEN_LIVE_STALE },
      };
    return { bears: 'none', ...basis };
  }
  return {
    bears: 'not_known',
    why: live ? { kind: 'not_known', text: CAPACITY_NO_BASIS } : { kind: 'stale', text: CAPACITY_NO_BASIS_STALE },
  };
}

/**
 * The request panel's stages (mission_panel/lifecycle.py STAGES), each onto the one status. The panel's own label and detail stay its headline; this is the status word.
 * Source field: `request.lifecycle.stage`. A stage outside this table is `unrecognised`.
 */
export const DI_REQUEST_STAGE_STATUS = {
  of_record: 'not_started',
  withdrawn: 'ended',
  revised: 'ended',
  withdrawal_incomplete: 'not_started',
  approval_uncertain: 'not_started',
  approved: 'not_started',
  approved_elsewhere: 'not_started',
  expired: 'not_started',
  dispatch_uncertain: 'start_requested',
  dispatched: 'start_requested',
  // STATE_RUNNING is TASK-observation evidence (target_runtime/mission_bridge.py `_observe_run`: the bound target task the receipt names, even after it stopped), never
  // evidence that a worker started or the team worked
  running: 'task_observed',
  pending_proof: 'waiting',
  verified: 'verified',
  blocked: 'stopped',
  run_cancelled: 'stopped',
  closed_elsewhere: 'ended',
  unreadable: 'unavailable',
  declined: 'ended',
  rejected: 'ended',
  changes_requested: 'waiting',
  outcome_uncertain: 'unknown',
} as const satisfies Record<string, DiMissionStatusId>;
type DiRequestStage = keyof typeof DI_REQUEST_STAGE_STATUS;

/**
 * The progression probe's states (target_runtime/mission_progress_status.py PROBE_STATES). The probe is evidence about a PROCESS, never about a Mission, so NO state maps
 * onto a Mission status: each says only whether a running or start-requested Mission advances by itself, and why not. Source field: `progression.state`.
 */
export const DI_PROGRESSION_PROBE_STATUS: Readonly<
  Record<MrProgressionState, { advancing: boolean; words: string }>
> = {
  // `advancing`: whether the request's record is being advanced and re-read automatically, i.e. how current it is likely to be. Never whether any work happened.
  running: { advancing: true, words: 'the progression runtime is passing' },
  stopped: { advancing: false, words: 'the progression runtime is not running' },
  stale: { advancing: false, words: 'the progression runtime has stopped reporting' },
  mismatch: { advancing: false, words: 'a progression runtime is running for different locations' },
  unreadable: { advancing: false, words: 'the progression runtime’s status cannot be read' },
  failing: { advancing: false, words: 'the progression runtime’s last pass failed' },
};

// ── The office (step 1) ──────────────────────────────────────────────────────────────────────

/** The socket is NOT connected (`transport_<state>`, state verbatim: the three non-connected states). */
const NOT_CONNECTED_REASONS = [
  'transport_connecting',
  'transport_reconnecting',
  'transport_disconnected',
];
/** Connected again, but no NEW office message yet: a reconnected socket by itself restores nothing. */
const AWAITING_OFFICE_STATE_REASON = 'transport_reconnected_awaiting_office_state';
/** Connected but silent past the last state's valid_until (`client_expired:<why>`). */
const CLIENT_EXPIRED_PREFIX = 'client_expired:';
const OFFICE_STALE_WHY = 'its status is not current while the office is not live';
const OFFICE_DISCONNECTED_WHY = 'the office is disconnected, so its status is not current';

/** True for a reason the CLIENT itself raised (diOfficeFormat.clientInvalidationReason), known or not. */
function isClientReason(reason: string | null): boolean {
  return (
    reason !== null && (reason.startsWith('transport_') || reason.startsWith(CLIENT_EXPIRED_PREFIX))
  );
}

/**
 * The server-reported NORMAL wait (e.g. `catalog_refresh_throttled`), or null. It is a wait only while the client still vouches for the state: the client's own invalidation
 * reasons take PRECEDENCE, so an older (cached) envelope problem never presents a lost connection as a calm backend wait.
 */
export function backendNormalWait(state: DiOfficeState): string | null {
  const problem = state.envelope.envelopeProblem;
  return !isClientReason(state.notLiveReason) && isNormalWaiting(problem) ? problem : null;
}

/**
 * The office as a whole, in the same vocabulary: is what the panel shows current? Live is `running`; a normal operating wait of the backend is `waiting`; a socket that is
 * NOT connected is `disconnected`; a socket connected again but with no new office state yet is `waiting` (it restores nothing, and it is NOT disconnected); a connected but
 * silent one past `valid_until` is `stale`; anything else not live, including a `transport_*` reason this module does not recognise, is `stale`. PRECEDENCE: the client's
 * own reasons are evaluated BEFORE the envelope's server-reported normal wait, so a cached throttle never masks a lost connection.
 */
export function officeStatus(state: DiOfficeState): DiMissionStatusResult {
  const reason = state.notLiveReason;
  if (state.live)
    return current('running', 'Live: the office state is current.', [field('state.live', true)]);
  const base = [field('state.live', false), field('state.notLiveReason', reason)];
  if (reason !== null && NOT_CONNECTED_REASONS.includes(reason))
    return uncertain(
      'disconnected',
      { kind: 'stale', text: OFFICE_DISCONNECTED_WHY },
      'Disconnected from the office server: the last state is shown and is not current.',
      base,
    );
  if (reason === AWAITING_OFFICE_STATE_REASON)
    return current(
      'waiting',
      'Connected again, waiting for a new office state: the last state is shown and is not current.',
      base,
    );
  if (reason !== null && reason.startsWith(CLIENT_EXPIRED_PREFIX))
    return uncertain(
      'stale',
      { kind: 'stale', text: OFFICE_STALE_WHY },
      'No new office state arrived before the last one expired (connected but silent): the last state is shown and is not current.',
      base,
    );
  if (reason !== null && reason.startsWith('transport_'))
    // A transport reason this module does not recognise: never "disconnected", never a backend wait.
    return uncertain(
      'stale',
      { kind: 'stale', text: OFFICE_STALE_WHY },
      `Not live (${reason}): the last state is shown and is not current.`,
      base,
    );
  const problem = state.envelope.envelopeProblem;
  if (isNormalWaiting(problem))
    return current(
      'waiting',
      `Waiting on the backend (${problem}${
        state.envelope.retryAfterSeconds === null
          ? ''
          : `, retry after ${state.envelope.retryAfterSeconds} s`
      }); a normal state, not an error. Nothing shown is current.`,
      [
        ...base,
        field('envelope.envelopeProblem', problem),
        field('envelope.retryAfterSeconds', state.envelope.retryAfterSeconds),
      ],
    );
  if (reason === null)
    return uncertain(
      'unknown',
      { kind: 'not_known', text: 'the office is not live and gave no reason' },
      'Not live, and no reason was given.',
      base,
    );
  return uncertain(
    'stale',
    { kind: 'stale', text: OFFICE_STALE_WHY },
    `Not live (${reason}): the last state is shown and is not current.`,
    [...base, field('envelope.fresh', state.envelope.fresh)],
  );
}

// ── The facts each step reads ────────────────────────────────────────────────────────────────

/** Live AND confirmed as itself in the last observation (never replaced, ambiguous, gone). */
export function isConfirmedLive(m: DiMember): boolean {
  return m.live && m.classification === 'present';
}

const RUN_STARTED_NOT_READ = 'this office does not report whether its run started';

/** The catalog's execution fact `run_started`, verbatim: known yes, known no, or NOT READ (a server that predates the field), never guessed. */
export function runStartedOf(mission: DiMission): DiScanKnown<boolean> {
  return typeof mission.runStarted === 'boolean'
    ? { known: true, value: mission.runStarted }
    : { known: false, why: { kind: 'not_read', text: RUN_STARTED_NOT_READ } };
}

const isStanding = (value: string): value is DiStanding =>
  Object.prototype.hasOwnProperty.call(DI_STANDING_STATUS, value);
const isPhase = (value: string): value is keyof typeof DI_WORKFLOW_PHASE_STATUS =>
  Object.prototype.hasOwnProperty.call(DI_WORKFLOW_PHASE_STATUS, value);
const isStage = (value: string): value is DiRequestStage =>
  Object.prototype.hasOwnProperty.call(DI_REQUEST_STAGE_STATUS, value);

/** A workflow store's declared, recognised phase; null for every other record, and for a phase that is not observed or not recognised (the Phase column says those). */
function declaredPhase(
  mission: DiMission,
): { phase: keyof typeof DI_WORKFLOW_PHASE_STATUS; status: DiMissionCurrentStatus } | null {
  if (mission.sourceKind !== 'workflow_store' || !mission.v2.projected) return null;
  const declared = mission.v2.workflowPhase;
  if (
    declared === undefined ||
    declared.state !== 'observed' ||
    declared.phase === null ||
    !isPhase(declared.phase)
  )
    return null;
  return { phase: declared.phase, status: DI_WORKFLOW_PHASE_STATUS[declared.phase] };
}

/**
 * Round 5, item 4: what each member ACTIVITY says about work, as the producer reports it (mission_sources/contract.py `RUNTIME_STATUS_ACTIVITY`: the runtime's own
 * `working` is `busy`, and `idle`, `blocked`, `done`, `unknown`; any other runtime literal is `unknown_future`). Liveness says only that the SNAPSHOT is fresh
 * (`SnapshotReading.is_live`: health fresh), never that a member does work, so Running needs a live, confirmed member whose activity is `busy`. A literal outside this
 * table is `not_known`. Source field: `members[ref].activity`.
 */
export const DI_MEMBER_ACTIVITY_WORK = {
  busy: 'working',
  blocked: 'blocked',
  idle: 'not_working',
  done: 'not_working',
  unknown: 'not_known',
  unknown_future: 'not_known',
} as const satisfies Record<string, 'working' | 'blocked' | 'not_working' | 'not_known'>;
const workOf = (m: DiMember): 'working' | 'blocked' | 'not_working' | 'not_known' =>
  Object.prototype.hasOwnProperty.call(DI_MEMBER_ACTIVITY_WORK, m.activity)
    ? DI_MEMBER_ACTIVITY_WORK[m.activity as keyof typeof DI_MEMBER_ACTIVITY_WORK]
    : 'not_known';
const activityField = (m: DiMember): string => field(`members[${m.memberRef}].activity`, m.activity);

const memberField = (m: DiMember): string =>
  `${field(`members[${m.memberRef}].live`, m.live)}, ${field(`members[${m.memberRef}].classification`, m.classification)}`;

// ── The derivation ───────────────────────────────────────────────────────────────────────────

/**
 * Mission Core's progress for one Mission, as the contract reads it: `terminal` (observed, current, a terminal literal), `unreadable` (the source reports it but it cannot
 * be taken as a current observation: an unrecognised literal, an unexpected shape, a standing that is unavailable or not current), or null (no progress, non-terminal
 * progress, or none reported: nothing to reconcile). A RETAINED value is never read: the wire carries none beside an unavailable or non-current standing.
 */
type DiProgressReading =
  | { kind: 'terminal'; literal: DiProgressLiteral; status: 'ended' | 'stopped'; sources: string[] }
  | { kind: 'unreadable'; result: DiMissionStatusResult; sources: string[] };

/**
 * The server's reasons for a progress fact the report CARRIES that is not a current observation (server officeView.ts presentProgress): an unexpected shape, and a
 * standing that is not current, including NO standing at all. Kept DISTINCT from `progress_not_reported` (a producer that predates the fact: nothing to reconcile) and
 * from a Mission State that is absent. A source-declared `unavailable` standing is recognised by its standing, whatever its reason.
 */
const DI_PROGRESS_NOT_CURRENT_REASONS: readonly string[] = ['progress_unexpected', 'progress_standing_not_current'];

function progressReading(mission: DiMission, standing: string[]): DiProgressReading | null {
  const progress = mission.v2.projected ? mission.v2.missionState.progress : undefined;
  if (progress === undefined) return null;
  const cited = [
    field('v2.missionState.progress.state', progress.state),
    field('v2.missionState.progress.standing', progress.standing),
    field('v2.missionState.progress.progress', progress.progress),
  ];
  if (
    progress.state === 'observed' &&
    (progress.progress === null || !isProgress(progress.progress))
  ) {
    const sources = [
      ...cited,
      field('v2.missionState.progress.unrecognized', progress.unrecognized),
    ];
    return {
      kind: 'unreadable',
      sources,
      result: uncertain(
        'unrecognised',
        {
          kind: 'unrecognised',
          text: 'its Mission State reports a progress this office does not recognise',
        },
        `Its Mission State reports the progress “${progress.unrecognized ?? progress.progress ?? 'not text'}”, which this office does not recognise, so whether its record is over is not decided.`,
        [...standing, ...sources],
      ),
    };
  }
  // the source reports a progress fact that cannot be taken as a current observation: genuine unavailability, with the source's own reason
  if (
    progress.state === 'unavailable' &&
    (progress.standing !== null || DI_PROGRESS_NOT_CURRENT_REASONS.includes(progress.reason ?? ''))
  ) {
    const reason = progress.reason ?? 'progress_unavailable';
    const sources = [...cited, field('v2.missionState.progress.reason', reason)];
    return {
      kind: 'unreadable',
      sources,
      result: uncertain(
        'unavailable',
        {
          kind: 'unavailable',
          text: 'its Mission State progress could not be read',
          codes: [reason],
        },
        'Its Mission State progress could not be read as current, so whether its record is over is not known.',
        [...standing, ...sources],
      ),
    };
  }
  const closed = terminalProgress(mission);
  if (closed === null) return null;
  return {
    kind: 'terminal',
    literal: closed.literal,
    status: closed.status,
    sources: [
      field('v2.missionState.progress.progress', closed.literal),
      field('v2.missionState.progress.closureReason', progress.closureReason),
    ],
  };
}

/**
 * One Mission's status, by the source of truth above. Pure: it reads only fields the office state has already projected.
 *
 * Mission Core's PROGRESS describes the RECORD, never execution (mission/state.py: "a descriptive progress lifecycle, not execution"; mission/state_service.py `abandon`
 * stops no run and no worker). So the status is first read from EXECUTION alone (`executionStatus`), and progress only adds to it:
 *  - where NO START WAS EVER RECORDED (the execution reading is `not_started`: nothing to stop and no Orchestrator held), terminal progress decides the word (ABANDONED and
 *    CLOSED_UNSUCCESSFUL are `stopped`, COMPLETED is `ended`, or `verified` with verified success), and progress it cannot read makes the word `unrecognised` or
 *    `unavailable` with its reason;
 *  - EVERYWHERE ELSE the execution reading stands, its capacity with it (live workers stay `running` and hold an Orchestrator; an uncertain liveness stays uncertain), and
 *    the progress is cited and said as a description of the record.
 * Filing is separate: `recordClosed` files a record its own progress closed under Closed, whatever its execution reads.
 */
export function missionStatus(mission: DiMission, state: DiOfficeState): DiMissionStatusResult {
  const execution = executionStatus(mission, state);
  const read = reconcileProgress(mission, execution);
  return withCompletionNote(mission, { ...read, capacity: capacityOf(mission, state, execution.status) });
}

/**
 * What a reported completion WITHOUT affirmed success says (rounds 4 and 5): only the lack of affirmation, never a cause. `reported_success` is false whenever the review is
 * missing, pending, stale, not approving or inapplicable, OR the task report itself is not applicable with a fresh APPROVE beside it (mission/observation.py; no hold
 * records that last case), so naming any one cause would invent it. The evidence is cited in `sources`, not interpreted.
 */
export const DI_COMPLETION_NOT_AFFIRMED =
  'Its Mission State reports the task complete, but its reported success is not affirmed: that is not a failure.';

/**
 * A reported completion whose success is not affirmed, given as the result's `qualification` (rendered beside the status) and cited, never a Failed and never folded
 * into the sentence. `reported_success = false` supports exactly one claim, that reported success is NOT AFFIRMED: never "not verified", "unsuccessful" or any other
 * verdict (round 6). Verified success is a SEPARATE fact, computed apart from it (mission/observation.py `verified_success = verified and not blocking`; review
 * unavailable and unknown are information holds that do not block it), so where verified success IS recorded there is no qualification at all.
 */
function withCompletionNote(mission: DiMission, result: DiMissionStatusResult): DiMissionStatusResult {
  const completion = mission.v2.projected ? mission.v2.missionState.completion : null;
  if (
    completion === null ||
    completion.state !== 'observed' ||
    completion.reportedComplete !== true ||
    completion.reportedSuccess === true ||
    completion.verifiedSuccess === true
  )
    return result;
  return {
    ...result,
    qualification: DI_COMPLETION_NOT_AFFIRMED,
    sources: [
      ...result.sources,
      field('v2.missionState.completion.reportedComplete', true),
      field('v2.missionState.completion.reportedSuccess', completion.reportedSuccess),
    ],
  };
}

/** Mission Core's progress reconciled with the execution reading (see `missionStatus`). */
function reconcileProgress(mission: DiMission, execution: DiMissionStatusResult): DiMissionStatusResult {
  const standing = [field('mission.standing', mission.standing)];
  const progress = progressReading(mission, standing);
  if (progress === null) return execution;
  if (execution.status === 'not_started') {
    if (progress.kind === 'unreadable')
      // the run is known quiet (no start recorded), so nothing is held: only whether its record is over is not known
      return {
        ...progress.result,
        sources: [
          ...progress.result.sources,
          ...execution.sources.filter((x) => !standing.includes(x)),
        ],
      };
    const completion = mission.v2.projected ? mission.v2.missionState.completion : null;
    const verified = completion?.state === 'observed' && completion.verifiedSuccess === true;
    const id = progress.status === 'ended' && verified ? 'verified' : progress.status;
    const reason = mission.v2.projected
      ? (mission.v2.missionState.progress?.closureReason ?? null)
      : null;
    return current(
      id,
      `No start was ever recorded for it, and its Mission State progress is ${progress.literal}${
        reason === null ? '' : ` (${reason.replace(/_/g, ' ')})`
      }: its record is over (record state ${mission.lifecycleState}).${id === 'ended' ? ' Ended is not verified success.' : ''}`,
      [
        ...execution.sources,
        ...progress.sources,
        ...(id === 'verified' ? [field('v2.missionState.completion.verifiedSuccess', true)] : []),
      ],
    );
  }
  const note =
    progress.kind === 'terminal'
      ? ` Its Mission State progress is ${progress.literal}: that describes the record, not execution, so it says nothing about whether the work stopped.`
      : ' Its Mission State progress could not be read as current; that describes the record, not execution.';
  return {
    ...execution,
    sentence: `${execution.sentence}${note}`,
    sources: [...execution.sources, ...progress.sources],
  };
}

/** The EXECUTION reading of one Mission (steps 1 to 7 above), before its record's own progress is considered. */
function executionStatus(mission: DiMission, state: DiOfficeState): DiMissionStatusResult {
  const shown = `record state ${mission.lifecycleState}`;
  const standing = [field('mission.standing', mission.standing)];
  // 1. the office
  const office = officeStatus(state);
  if (office.status !== 'running') {
    const lost = office.status === 'disconnected';
    return uncertain(
      lost ? 'disconnected' : 'stale',
      { kind: 'stale', text: lost ? OFFICE_DISCONNECTED_WHY : OFFICE_STALE_WHY },
      `Last known: ${mission.standing} (${shown}); not current while the office is not live.`,
      [...office.sources, ...standing],
    );
  }
  // 2. the standing
  if (!isStanding(mission.standing))
    return uncertain(
      'unrecognised',
      {
        kind: 'unrecognised',
        text: `its record reports a standing this office does not recognise (“${mission.standing}”)`,
      },
      `Unrecognised standing “${mission.standing}” (${shown}): its status is not decided.`,
      standing,
    );
  const run = runStartedOf(mission);
  const runField = run.known ? [field('mission.runStarted', run.value)] : [];
  const neverStarted = run.known && !run.value ? ' Its run never started.' : '';
  // 3. (round 4, finding 1) a reported completion whose success is not affirmed decides NOTHING here: reported_success is false whenever the review is pending,
  //    missing, stale or inapplicable (mission/observation.py), an absence of affirmation, never a failure report; `missionStatus` cites it beside the execution reading
  const completion = mission.v2.projected ? mission.v2.missionState.completion : null;
  // 4. a terminal standing
  if (mission.standing === 'terminal') {
    const refined = DI_TERMINAL_LITERAL_STATUS[mission.lifecycleModel]?.[mission.lifecycleState];
    const literal = [
      field('mission.lifecycleModel', mission.lifecycleModel),
      field('mission.lifecycleState', mission.lifecycleState),
    ];
    if (refined === 'failed' || refined === 'stopped')
      return current(
        refined,
        `The record has ended as ${mission.lifecycleState} (${DI_MISSION_STATUS[refined].label.toLowerCase()}, as the source reports it).${neverStarted}`,
        [...standing, ...literal, ...runField],
      );
    const verified = completion?.state === 'observed' ? completion.verifiedSuccess : null;
    if (verified === true)
      return current(
        'verified',
        `The record has ended (${shown}) and its Mission State records verified success.`,
        [...standing, field('v2.missionState.completion.verifiedSuccess', true)],
      );
    return current(
      'ended',
      `The record has ended (${shown}). Ended is not verified success.${neverStarted}`,
      [
        ...standing,
        ...(refined === 'ended' ? literal : []),
        ...(verified === null
          ? []
          : [field('v2.missionState.completion.verifiedSuccess', verified)]),
        ...runField,
      ],
    );
  }
  // 5. the team (active only): observed roster, established binding, members confirmed live
  const binding = [
    field('mission.bindingState', mission.bindingState),
    field('mission.bindingReason', mission.bindingReason),
  ];
  const members = mission.members.state === 'observed' ? mission.members.members : [];
  const teamCounts =
    mission.standing === 'active' &&
    mission.members.state === 'observed' &&
    mission.bindingState === 'bound';
  const confirmed = teamCounts ? members.filter(isConfirmedLive) : [];
  const phase = declaredPhase(mission);
  const phaseField = phase === null ? [] : [field('v2.workflowPhase.phase', phase.phase)];
  // sources that disagree: never the stronger reading
  const disagree = (sentence: string, sources: string[]) =>
    uncertain(
      'unknown',
      { kind: 'not_known', text: 'its sources disagree about whether its run started' },
      sentence,
      sources,
    );
  if (confirmed.length > 0 && ((run.known && !run.value) || phase?.status === 'not_started'))
    return disagree(
      `The record says no run started${phase === null ? '' : ` (workflow phase ${phase.phase})`}, but ${confirmed.length} team member(s) are observed live: the sources disagree.`,
      [...standing, ...runField, ...phaseField, ...binding, ...confirmed.map(memberField)],
    );
  if (
    phase !== null &&
    ((phase.status === 'not_started' && run.known && run.value) ||
      (phase.status === 'verified' && run.known && !run.value))
  )
    return disagree(
      `Workflow phase ${phase.phase}, but the catalog records ${run.known && run.value ? 'a run start' : 'no run start'}: the sources disagree.`,
      [...standing, ...phaseField, ...runField],
    );
  if (confirmed.length > 0) {
    const blocked = confirmed.filter((m) => m.activity === 'blocked');
    // A terminal's own `blocked` report counts ONLY from a confirmed, live member (memberTerminalNotes keeps every other one as a last-known detail).
    if (blocked.length > 0)
      return current(
        'blocked',
        `${blocked.length} live team member terminal(s) report blocked (the terminal's own report).`,
        [
          ...standing,
          ...runField,
          ...binding,
          ...blocked.map(
            (m) =>
              `${field(`members[${m.memberRef}].activity`, m.activity)}, ${field(`members[${m.memberRef}].live`, true)}, ${field(`members[${m.memberRef}].classification`, m.classification)}`,
          ),
        ],
      );
    // Round 5, item 4: live is the snapshot's freshness, not work. Running needs a confirmed, live member that REPORTS work (`DI_MEMBER_ACTIVITY_WORK`).
    const sources = [...standing, ...runField, ...binding, ...members.map(memberField), ...confirmed.map(activityField)];
    const working = confirmed.filter((m) => workOf(m) === 'working');
    const seen = `${confirmed.length} of ${members.length} team member(s) observed live`;
    if (working.length > 0)
      return current(
        'running',
        `${seen}; ${working.length} ${working.length === 1 ? 'reports' : 'report'} work (busy).`,
        sources,
      );
    if (confirmed.every((m) => workOf(m) === 'not_working'))
      return current('waiting', `${seen}, but none reports work now (each reports idle or done).`, sources);
    const unread = confirmed.filter((m) => workOf(m) === 'not_known').length;
    return uncertain(
      'unknown',
      { kind: 'not_known', text: `no live team member reports work, and ${unread} report no known activity` },
      `${seen}; none reports work, and ${unread} report no known activity.`,
      sources,
    );
  }
  // 6. the run-start evidence: the declared phase (a phase outside the producer's vocabulary is said as unrecognised, never a fall-through), then the catalog's own fact
  const declared =
    mission.sourceKind === 'workflow_store' && mission.v2.projected
      ? mission.v2.workflowPhase
      : undefined;
  if (phase === null && declared !== undefined && declared.state === 'observed')
    return uncertain(
      'unrecognised',
      { kind: 'unrecognised', text: 'its workflow phase is one this office does not recognise' },
      `Its producer declares the workflow phase “${declared.unrecognized ?? declared.phase ?? 'not text'}”, which this office does not recognise: its status is not decided.`,
      [
        ...standing,
        field('mission.sourceKind', mission.sourceKind),
        field('v2.workflowPhase.state', declared.state),
        field('v2.workflowPhase.phase', declared.phase),
        field('v2.workflowPhase.unrecognized', declared.unrecognized),
        ...runField,
      ],
    );
  if (phase !== null)
    return current(
      phase.status,
      `Workflow phase ${phase.phase} (declared by the producer).${phase.status === 'start_requested' ? ' No team member is confirmed live.' : ''}`,
      [...standing, ...phaseField, ...runField],
    );
  if (run.known && !run.value)
    return current(
      'not_started',
      `No start was ever recorded for it: ${runMeaning(mission, false) ?? 'its catalog records no run start'} (${shown}).`,
      [...standing, field('mission.sourceKind', mission.sourceKind), ...runField],
    );
  // 7. the standing
  if (mission.standing === 'inactive')
    return current(DI_STANDING_STATUS.inactive, `Not active (${shown}).`, [
      ...standing,
      ...runField,
    ]);
  if (mission.members.state !== 'observed') {
    const memberState = [
      ...standing,
      ...runField,
      ...binding,
      field('mission.members.state', mission.members.state),
    ];
    return mission.members.state === 'unavailable'
      ? uncertain(
          'unavailable',
          {
            kind: 'unavailable',
            text: 'its team could not be read',
            ...(mission.members.reason === null ? {} : { codes: [mission.members.reason] }),
          },
          // the source's reason code is provenance (the why's codes and the sources), never shown as text
          `The record says ${mission.lifecycleState}; its team could not be read (the source reports it unavailable).`,
          [...memberState, field('mission.members.reason', mission.members.reason)],
        )
      : uncertain(
          'unknown',
          { kind: 'not_known', text: `its team is not observed (${mission.members.state})` },
          `The record says ${mission.lifecycleState}; its team is not observed (${mission.members.state}).`,
          memberState,
        );
  }
  if (mission.bindingState !== 'bound')
    return uncertain(
      'unknown',
      { kind: 'not_known', text: `its team is not established (binding ${mission.bindingState})` },
      `The record says ${mission.lifecycleState}; its team is not established (binding ${mission.bindingState}).`,
      [...standing, ...runField, ...binding],
    );
  // bound, no confirmed live member: its task is observed, its workers are not (DI_BOUND_TASK_STATUS)
  const recorded = run.known ? runMeaning(mission, run.value) : null;
  return current(
    DI_BOUND_TASK_STATUS.status,
    `Its bound task is observed (${shown}${recorded === null ? '' : `; ${recorded}`}), but no team member is confirmed live: nothing says any worker started.`,
    [
      ...standing,
      field('mission.sourceKind', mission.sourceKind),
      ...runField,
      ...binding,
      ...members.map(memberField),
    ],
  );
}

/** Whether the Mission is running, for the Running filter and the orchestrator limit: a current status is a known yes or no; an uncertain one is not known with its reason. */
export function runningFact(result: DiMissionStatusResult): DiScanKnown<boolean> {
  return result.why === null
    ? { known: true, value: result.status === 'running' }
    : { known: false, why: result.why };
}

// ── The request panel ────────────────────────────────────────────────────────────────────────

/**
 * A request's status from the panel's own stage, by `DI_REQUEST_STAGE_STATUS`. The progression probe never changes it (it is evidence about a process): for a request whose
 * start was requested or whose task is observed, it only says how current its record is likely to be, and is cited.
 */
export function requestStageStatus(
  stage: string,
  progression: MrProgression | null,
): DiMissionStatusResult {
  const sources = [field('request.lifecycle.stage', stage)];
  if (!isStage(stage))
    return uncertain(
      'unrecognised',
      {
        kind: 'unrecognised',
        text: `the request panel reports a stage this office does not recognise (“${stage}”)`,
      },
      `The request panel reports the stage “${stage}”, which this office does not recognise.`,
      sources,
    );
  const id = DI_REQUEST_STAGE_STATUS[stage];
  const probe = progression === null ? null : DI_PROGRESSION_PROBE_STATUS[progression.state];
  // the probe says only how current the record shown is likely to be (C2): a healthy runtime is never evidence that the request's Mission started or that anyone worked
  const qualifies =
    probe !== null && progression !== null && (id === 'start_requested' || id === 'task_observed');
  // the panel's stage id is provenance (cited in `sources`), never prose: its words are the status's own meaning
  const sentence = `${DI_MISSION_STATUS[id].meaning}${
    qualifies
      ? probe.advancing
        ? ` Its record is advanced and re-read automatically (${probe.words}); that is about the process, not evidence that any worker started.`
        : ` Nothing advances or re-reads its record automatically right now (${probe.words}), so it may be older than it looks.`
      : ''
  }`;
  const cited = qualifies ? [...sources, field('progression.state', progression.state)] : sources;
  if (id === 'unknown')
    return uncertain(
      'unknown',
      { kind: 'not_known', text: 'the request panel does not know the outcome it relayed' },
      sentence,
      cited,
    );
  if (id === 'unavailable')
    return uncertain(
      'unavailable',
      { kind: 'unavailable', text: 'the request panel cannot read this request’s record' },
      sentence,
      cited,
    );
  return current(id, sentence, cited);
}
