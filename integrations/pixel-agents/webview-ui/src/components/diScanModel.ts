/**
 * The office side panel's scan list: tabs, two-line rows, status dots and the expanded swarm card.
 *
 * Presentation only. Real rows are read from the office state the panel already holds. Nothing here
 * invents a pull request, a CI result or a delivery number the record does not carry. The locked
 * specimen (`DI_SCAN_LOCK`) is the 2026-10-25 visual source of truth, used to render that mock and
 * nothing else.
 */
import type {
  DiMember,
  DiMission,
  DiObservedValue,
  DiOfficeState,
} from '../../../core/src/messages.js';
import {
  reviewNotOfferedConsequence,
  type ReviewTargetRef,
  unavailableWords,
} from './diCodeReviewModel.js';
import type { DlStep, DlView } from './diDeliveryModel.js';
import { missionObjective, missionStatus } from './diOperationalStatus.js';
import { missionTitle, plainMissionDescription, shortMissionRefs } from './diPlainSummary.js';
import type { PanelSource } from './diReceptionItems.js';
import { missionForRequest } from './diReceptionItems.js';
import { plainServerWording } from './diReceptionPlain.js';

/** What the office said about a code review for a Mission (see DiScanRow.reviewOffer). */
export type DiScanReviewOffer =
  | { kind: 'offered'; target: ReviewTargetRef }
  | { kind: 'unavailable'; reason: string | null }
  | { kind: 'unknown' };
/** A row that carries no statement about a code review (the specimen rows; an office that predates the review). */
export const DI_SCAN_NO_REVIEW_INFO: DiScanReviewOffer = { kind: 'unknown' };

/** The reasons a review is not offered that belong to the Mission (they say the most), in the order the first one present is used. */
const REVIEW_REASON_PRIORITY = [
  'not_a_mission_core_record',
  'contract_version_1_office',
  // a FINISHED Mission whose execution the office does not list says the most about it
  'finished_execution_not_listed',
  'assignment_not_active',
  'assignment_not_bound',
  'member_not_in_direct_task',
  'member_not_live',
  'task_not_observed',
] as const;

/**
 * The reasons of a Mission's OWN offer (`DiMission.codeReview`, the offer of a FINISHED Mission) that the row says. The others it can carry (not a Mission
 * Core record, an older office, not finished, no task) are either the members' to say or do not apply to a row, so they change nothing.
 */
const MISSION_LEVEL_REASONS = ['finished_execution_not_listed'] as const;

/** The name shown for a Mission-level reference, which has no agent to name. */
export const MISSION_REVIEW_NAME = 'this Mission';

/**
 * The review offer of a listed Mission, from the typed facts of its offers (never a name or a path). The first member whose review is OFFERED is the
 * reference (the worktree is the task's one; the listing shows the team's changes either way). Otherwise a FINISHED Mission that is offered as a whole is
 * the reference, by a Mission-level reference with no member. When none is offered, the reason is the office's own, the most Mission-level one present.
 * When nothing is said, nothing is claimed.
 */
export function reviewOfferOf(mission: DiMission): DiScanReviewOffer {
  const members = mission.members.state === 'observed' ? mission.members.members : [];
  const offered = members.find(
    (m) => m.codeReview?.state === 'offered' && m.codeReview.listing !== null && m.codeReview.taskId !== null,
  );
  if (offered?.codeReview?.listing && offered.codeReview.taskId) {
    return {
      kind: 'offered',
      target: {
        missionRef: mission.missionRef,
        memberRef: offered.memberRef,
        taskId: offered.codeReview.taskId,
        listing: offered.codeReview.listing,
        agentName: offered.agent,
      },
    };
  }
  const own = mission.codeReview;
  if (own?.state === 'offered' && own.listing !== null && own.taskId !== null) {
    return {
      kind: 'offered',
      target: {
        missionRef: mission.missionRef,
        memberRef: '',
        taskId: own.taskId,
        listing: own.listing,
        agentName: MISSION_REVIEW_NAME,
        level: 'mission',
      },
    };
  }
  const ownReason =
    own?.state === 'unavailable' && own.reason !== null && (MISSION_LEVEL_REASONS as readonly string[]).includes(own.reason)
      ? [own.reason]
      : [];
  const reasons = [
    ...ownReason,
    ...members.flatMap((m) => (m.codeReview?.state === 'unavailable' ? [m.codeReview.reason] : [])),
  ];
  if (reasons.length === 0) return { kind: 'unknown' };
  const best = REVIEW_REASON_PRIORITY.find((r) => reasons.includes(r));
  return { kind: 'unavailable', reason: best ?? reasons[0] ?? null };
}

export type DiScanTab = 'open' | 'merged' | 'closed' | 'archived';

export const DI_SCAN_TABS: readonly { id: DiScanTab; label: string }[] = [
  { id: 'open', label: 'Open' },
  { id: 'merged', label: 'Merged' },
  { id: 'closed', label: 'Closed' },
  { id: 'archived', label: 'Archived' },
];

/** `idle`: a neutral status (waiting, unknown, ended, stopped, stale), drawn as a grey dot beside its word. */
export type DiScanTone = 'needs' | 'building' | 'merged' | 'pass' | 'failed' | 'idle';

export interface DiScanPill {
  id: string;
  label: string;
  tone: DiScanTone;
}

/**
 * One fact about a swarm role, said with HOW it is known. A model is OBSERVED from the running herd (and goes STALE when
 * the member is no longer live); a provider is CONFIGURED for the role. Absence stays absence: never a guess, never a
 * blank that would read as "none".
 */
export interface DiScanFact {
  kind:
    | 'observed'
    | 'configured'
    | 'stale'
    | 'none_reported'
    | 'not_recorded'
    | 'unavailable'
    | 'not_applicable';
  /** The value when there is one; null otherwise. */
  value: string | null;
  /** The plain line shown under the role. */
  text: string;
}

export interface DiScanAvatar {
  role: string;
  name: string;
  /** Bust index 0–3, or a character sheet index when no bust exists. */
  palette: number;
  /** One recorded status line. Empty when the member recorded none. */
  note: string;
  /** Drawn character, when the office has one. */
  characterId: number | null;
  /** The role's exact model, as observed (gap 4, D23). */
  model: DiScanFact;
  /** The role's provider, as configured (gap 4, D23). */
  provider: DiScanFact;
}

/**
 * Where a Mission stands on the road to delivery, in order. `ci_failed` sits beside `ci_passed`: CI has reported on the
 * open pull request either way.
 */
export type DiDeliveryStage =
  | 'working'
  | 'approved'
  | 'committed'
  | 'pushed'
  | 'pr_open'
  | 'ci_failed'
  | 'ci_passed'
  | 'ci_accepted'
  | 'merged';

/**
 * The delivery state Mission details is drawn against. `fixture`: a labelled sample state (the lock specimen).
 * `not_read`: this layout build does not read a Mission's delivery state; reading it is owed by Stage 4.
 */
export interface DiScanDelivery {
  source: 'fixture' | 'not_read';
  stage: DiDeliveryStage | null;
}

/**
 * M8: the Mission's delivery link as the office relays the producer's DECLARED `deliveryLink` (catalog `missions[].delivery_link`).
 * `linked`: exactly one delivery record is linked to this Mission (the only case in which its delivery is read here). `unlinked`: the
 * producer's reason, in words. `not_reported`: the office's answer carries no link at all (a v1 office, or a catalog that predates
 * the field): unknown, never "none". Absent from a row: the row is the labelled specimen, whose Delivery is layout only.
 */
export type DiScanDeliveryLink =
  | { kind: 'linked'; deliveryId: string; words: string }
  | { kind: 'unlinked'; state: string; words: string }
  | { kind: 'not_reported'; words: string };

/** The basis of a workflow link, in words (`record_mission_binding`). */
export const DI_DELIVERY_LINKED = 'Linked by the delivery record’s own Mission binding. A link names which delivery is read here; it authorizes nothing.';
/** The basis of a Herdr link, in words (`record_repository_binding`). */
export const DI_DELIVERY_LINKED_REPOSITORY =
  'Linked because this workspace’s origin matches one delivery record’s repository. A link names which delivery is read here; it authorizes nothing.';
/** Each unlinked state's reason, in words (the producer's meanings: core/asyncapi.yaml DiMissionDeliveryLinkState). */
export const DI_DELIVERY_UNLINKED: Readonly<Record<string, string>> = {
  unlinked_model_not_recorded:
    'Not known: delivery records do not name a Mission of this kind, so no delivery is linked.',
  unlinked_store_not_registered: 'Not known: the catalog has no delivery store registered.',
  unlinked_store_unavailable: 'Not known: the catalog’s delivery store could not be read, or a record in it did not validate.',
  unlinked_reader_failed: 'Not known: the catalog’s delivery link reader failed.',
  unlinked_no_record: 'No delivery record in the catalog’s delivery store names this Mission.',
  unlinked_ambiguous: 'More than one delivery record names this Mission, or another Mission has its id; none is chosen.',
  unlinked_sources_incomplete: 'Not known: a workflow source was not read, so this Mission’s id is not known to be unique.',
  unlinked_id_not_addressable: 'The one record that names this Mission has a delivery id of a shape this office cannot address.',
  unlinked_repository_not_recorded:
    'This Herdr task’s workspace has no origin URL this office could read, so no delivery is linked.',
};
export const DI_DELIVERY_LINK_NOT_REPORTED = 'Not known: this office’s catalog does not report a delivery link for this Mission.';

/** A real Mission's delivery link, from the wire's v2 block; nothing is inferred when the block or the field is absent. */
function deliveryLinkOf(mission: DiMission): DiScanDeliveryLink {
  const link = mission.v2.projected ? mission.v2.deliveryLink : undefined;
  if (link === undefined) return { kind: 'not_reported', words: DI_DELIVERY_LINK_NOT_REPORTED };
  if (link.state === 'linked' && typeof link.deliveryId === 'string') {
    const words = link.basis === 'record_repository_binding' ? DI_DELIVERY_LINKED_REPOSITORY : DI_DELIVERY_LINKED;
    return { kind: 'linked', deliveryId: link.deliveryId, words };
  }
  const words = DI_DELIVERY_UNLINKED[link.state];
  return {
    kind: 'unlinked',
    state: link.state,
    words: words ?? 'Not known: this office reported a link state this window does not recognise.',
  };
}

/**
 * What the Mission's state offers the person (gap 3). `record`: derived from the office record; `fixture`: sample state.
 * A fact this build does not read is `null`, NOT `false`: unread is not absent (reading it is owed by Stage 4).
 */
export interface DiScanStateFacts {
  source: 'fixture' | 'record';
  /** Not known when the Mission's status is stale, disconnected or unknown: never "not running" (round 5). */
  running: DiScanKnown<boolean>;
  awaitingApproval: boolean | null;
  repoRequest: boolean | null;
  stalled: boolean | null;
  logRecorded: boolean | null;
}

/** A Mission's phase on the road from proposal to delivery (the Missions table's Phase column, 04-missions.png). */
export interface DiScanPhase {
  word: 'Proposal' | 'Build' | 'Review' | 'Delivery';
  /** 1 to 4: Proposal, Build, Review, Delivery. */
  step: 1 | 2 | 3 | 4;
  /** Every phase is behind it (a finished Mission). */
  finished: boolean;
}

/**
 * A real row's Phase cell, from the producer's DECLARED workflow phase (never from the display-only lifecycle state). The kinds are the source's own, kept apart, at the two levels the source has them:
 *  - inside the v2 block: `word` (an `observed` phase in the producer's vocabulary; observed is valid only for a workflow_authority record), `unrecognized` (observed, but outside the vocabulary: its text, inert),
 *    `not_applicable` (this kind of record has no workflow phase; Mission Core is one) and `not_reported` (the producer reported none: unknown, never "none");
 *  - the block itself: `not_provided` (a v1 office has no v2 block, `projected: false`, so no phase of any kind; also a projected block whose field is missing, a server that predates it).
 * An absent v2 field is NOT an observed phase, and no step is drawn: the four-step bar is not a mapping of these ten phases.
 */
export type DiScanDeclaredPhase =
  | { kind: 'word'; word: string }
  | { kind: 'unrecognized'; text: string | null }
  | { kind: 'not_applicable'; problem: string | null }
  | { kind: 'not_reported' }
  | { kind: 'not_provided' };

export const DI_PHASE_NOT_APPLICABLE = 'Not applicable';
export const DI_PHASE_NOT_REPORTED = 'Not reported';
export const DI_PHASE_NOT_PROVIDED = 'Not provided by this office';
const DI_PHASE_NOT_APPLICABLE_WHY = 'This kind of record has no workflow phase.';
const DI_PHASE_NOT_REPORTED_WHY = 'The producer did not report a workflow phase for this record.';
const DI_PHASE_NOT_PROVIDED_WHY = 'This office does not provide a workflow phase for its Missions.';

/** The producer's own phase identifier as a word: lower-cased, underscores to spaces, first letter capitalised, nothing else (no mapping, no renaming). */
export function phaseWord(phase: string): string {
  const spaced = phase.toLowerCase().replace(/_/g, ' ');
  return spaced.charAt(0).toUpperCase() + spaced.slice(1);
}

/** What the wire says about this Mission's declared phase, as a display kind. The block's absence is read from `projected`, never from a phase value. */
export function declaredPhaseOf(mission: DiMission): DiScanDeclaredPhase {
  if (!mission.v2.projected) return { kind: 'not_provided' };
  const declared = mission.v2.workflowPhase;
  if (declared === undefined) return { kind: 'not_provided' };
  switch (declared.state) {
    case 'observed':
      return declared.phase !== null ? { kind: 'word', word: phaseWord(declared.phase) } : { kind: 'unrecognized', text: declared.unrecognized };
    case 'not_applicable':
      return { kind: 'not_applicable', problem: declared.problem };
    case 'not_reported':
      return { kind: 'not_reported' };
  }
}

/** The cell's words: the visible text, and the sentence that says what it means (null for a word, which needs none). */
export function declaredPhaseWords(declared: DiScanDeclaredPhase): { text: string; why: string | null } {
  switch (declared.kind) {
    case 'word':
      return { text: declared.word, why: null };
    case 'unrecognized':
      return { text: declared.text === null ? 'Unrecognized phase' : `Unrecognized: ${declared.text}`, why: 'The producer declared a phase this view does not recognise.' };
    case 'not_applicable':
      return { text: DI_PHASE_NOT_APPLICABLE, why: DI_PHASE_NOT_APPLICABLE_WHY };
    case 'not_reported':
      return { text: DI_PHASE_NOT_REPORTED, why: DI_PHASE_NOT_REPORTED_WHY };
    case 'not_provided':
      return { text: DI_PHASE_NOT_PROVIDED, why: DI_PHASE_NOT_PROVIDED_WHY };
  }
}

/**
 * The Missions table's own columns (CONTROL-MAP S3). Null: the office record carries no such field, and the table says
 * so rather than guess (no owner, repo or phase is invented).
 */
export interface DiScanTableFacts {
  owner: string | null;
  /** The harness repo, and how many target repos the Mission adds (AC-4.4-13). */
  repos: { harness: string; targets: number } | null;
  /** The four-step phase: the labelled specimen's alone. Real rows leave it null: no document maps the producer's ten phases onto four steps, so none is drawn. */
  phase: DiScanPhase | null;
  /** The producer's DECLARED workflow phase for a real row (null on the specimen). Shown as its own word; see `DiScanDeclaredPhase`. */
  declared: DiScanDeclaredPhase | null;
  /** Time since the Mission's last recorded activity, on the office clock ("now", "6m", "1d"). */
  last: string | null;
}

/**
 * Why a row's fact is not known (B-H1 round 5). `not_read`: nothing was read to decide it (no Reception, a fact this build
 * does not read); `not_known`: something was read but cannot decide it (a list cut short, a stale or unknown status).
 */
export interface DiScanWhy {
  kind: 'not_read' | 'not_known';
  /** Plain words for the screen. */
  text: string;
  /** The server's own reason codes, kept as provenance (diagnostics only, never shown as text). */
  codes?: readonly string[];
}

/**
 * A fact that is KNOWN, with its value, or NOT KNOWN, with its reason. The two are inseparable: the compiler refuses an
 * unknown without a reason (round 5: a nullable boolean beside an optional reason let an unexplained unknown exist).
 */
export type DiScanKnown<T> = { known: true; value: T } | { known: false; why: DiScanWhy };

export const known = <T>(value: T): DiScanKnown<T> => ({ known: true, value });

/** True only for a fact KNOWN to be true: an unknown is never taken as yes, and never as no. */
export function knownTrue(fact: DiScanKnown<boolean>): boolean {
  return fact.known && fact.value;
}

/** The value when known; null when not (for a count that must not be confident). */
export function knownOrNull<T>(fact: DiScanKnown<T>): T | null {
  return fact.known ? fact.value : null;
}

export interface DiScanRow {
  id: string;
  title: string;
  /** Second line: a recorded context, never a guessed delivery. */
  context: string;
  description: string;
  pills: DiScanPill[];
  tab: DiScanTab;
  /**
   * Not known when Reception was not read, or when its list is cut short and does not name this Mission: never "does
   * not need you" (round 5).
   */
  needsYou: DiScanKnown<boolean>;
  swarm: readonly DiScanAvatar[];
  /** Detail footer, right side. Real rows name the record; the lock names its sample PR line. */
  footer: string;
  /** Extra mission-detail lines. Empty when the record has none. */
  handoff: string;
  /** Right side of the mission-detail card. Empty when nothing was recorded. */
  updated: string;
  /** A live code review can be opened for this mission. */
  reviewOffered: boolean;
  /**
   * What the office said about a code review for this Mission (Stage 4): the admitted reference to read it by, the reason none
   * is offered, or nothing at all. It carries NEVER a path: the reference is the office's own (Mission, member, task, listing).
   */
  reviewOffer: DiScanReviewOffer;
  /** The delivery state Mission details draws Delivery against. */
  delivery: DiScanDelivery;
  /** M8: a real Mission's delivery link (see DiScanDeliveryLink). Absent: the specimen. */
  deliveryLink?: DiScanDeliveryLink;
  /**
   * The office's source kind (`herdr_task`, `mission_core`, …). Absent on the labelled specimen.
   * Delivery uses it so a Herdr task does not wear the layout-preview lock.
   */
  sourceKind?: string;
  /** The state Mission details draws its state actions against. */
  state: DiScanStateFacts;
  /**
   * The request-panel proposal(s) of this Mission waiting for a decision, where the panel was read and its requests tie to this Mission by its Mission Core id (`missionForRequest`).
   * Absent or null: none is known (the panel lists only its own requests, so this is never "no decision is waiting").
   */
  decision?: DiScanDecision | null;
  /** The office lists an evidence library for this Mission (the record's own `evidence` block). Absent: none is listed (the specimen lists none). */
  evidenceListed?: boolean;
  /**
   * A Herdr task's review thread, from the evidence library's recorded review. Absent: this row is not a Herdr task
   * (Comments stays the Paid label). A Herdr task always carries one, including when the review was not reported.
   */
  reviewThread?: DiScanThreadSource;
  /** The Missions table's columns. Absent: none of them is recorded. */
  table?: DiScanTableFacts;
}

/** How many request-panel proposals of a Mission wait for a decision, and the one to open when there is exactly one. */
export interface DiScanDecision {
  requestRef: string | null;
  count: number;
}

/**
 * M9 / R4 on a REAL row: a decision exists only for a request-panel proposal, and it is taken IN the request panel: the exact approval (the typed word, `decide`) or, by owner decision (c), a decline,
 * a rejection or a change request with its reason (`decline`, `reject`, `requestChanges`), each on the exact offer shown. So the office offers NAVIGATION into that decision, labelled as what it does
 * (never an "Approve" button that does not approve), and Request changes and Reject, which are taken in the panel, are drawn disabled here with that reason.
 */
export const DI_DECIDE_LABEL = 'Decide in the request panel';
export const DI_DECIDE_WHAT =
  'Opens this proposal in the request panel, where your decision is recorded: approve it by typing the exact approval word, or decline it, reject it, or ask for changes with your reason.';
export const DI_DECIDE_UNAVAILABLE = 'The request panel is not available in this office, so the proposal cannot be opened from here.';
export const DI_REQUEST_CHANGES_IN_PANEL =
  'Asking for changes is done in the request panel, beside the approval: open the proposal there and write what should change.';
export const DI_REJECT_IN_PANEL =
  "Rejecting is done in the request panel, beside the approval, and asks you to confirm. Withdrawing a request is still its proposer's own act.";
/** Several proposals of one Mission wait: there is no single one to open. */
export const decideSeveral = (count: number): string =>
  `${count} proposals of this Mission wait for a decision in the request panel; open the request panel to choose one.`;

/** Each Mission's waiting request-panel proposals, by Mission ref: a request whose actions include `decide`, tied to exactly one listed Mission Core record. */
function decisionsByMission(panel: PanelSource, missions: readonly DiMission[]): Map<string, string[]> {
  const out = new Map<string, string[]>();
  if (panel.kind !== 'read') return out;
  for (const request of panel.overview.requests) {
    if (!request.actions.includes('decide')) continue;
    const ref = missionForRequest(request.missionId, missions);
    if (ref === null) continue;
    out.set(ref, [...(out.get(ref) ?? []), request.requestRef]);
  }
  return out;
}

/** A compact age on one clock, both times in epoch seconds: "now", "6m", "13h", "1d", "2mo", "3y" (04-missions.png Last). */
export function compactAge(nowSeconds: number, thenSeconds: number): string {
  const seconds = Math.max(0, Math.round(nowSeconds - thenSeconds));
  if (seconds < 60) return 'now';
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h`;
  const days = Math.floor(hours / 24);
  if (days < 30) return `${days}d`;
  if (days < 365) return `${Math.floor(days / 30)}mo`;
  return `${Math.floor(days / 365)}y`;
}

/** The record's own update time against the office clock; null when either is not recorded. */
function lastActivity(updatedAt: DiObservedValue, evaluatedAt: number | undefined): string | null {
  if (updatedAt.state !== 'observed' || typeof evaluatedAt !== 'number') return null;
  const then = Number((updatedAt.value ?? '').trim());
  if ((updatedAt.value ?? '').trim() === '' || !Number.isFinite(then)) return null;
  return compactAge(evaluatedAt, then);
}

const ROLE_LABEL: Record<string, string> = {
  supervisor: 'Supervisor',
  lead: 'Lead',
  executor: 'Executor',
  reviewer: 'Reviewer',
};

const ROLE_ORDER = ['supervisor', 'lead', 'executor', 'reviewer'];

/**
 * The bust for each role is that character (lock: Ken, Leo, Mia, Sam).
 * A seat key such as `supervisor` or `lead1` is not a display name.
 */
const ROLE_CAST: Record<string, { palette: number; name: string }> = {
  supervisor: { palette: 0, name: 'Ken' },
  lead: { palette: 1, name: 'Leo' },
  executor: { palette: 2, name: 'Mia' },
  reviewer: { palette: 3, name: 'Sam' },
};

function roleLabel(role: string): string {
  return ROLE_LABEL[role.toLowerCase()] ?? role;
}

/**
 * The Home tab a Mission is filed under. "Merged" only when the Mission's OWN record says merged: verified Mission success does not
 * establish a merged pull request (owed item 2, round 39), and the design names no other source for the tab (CONTROL-MAP.md: the tabs
 * are a Home filter). A verified-success Mission files as any other.
 */
export function tabForMission(
  mission: DiMission,
  status: ReturnType<typeof missionStatus>,
): DiScanTab {
  const life = mission.lifecycleState.toLowerCase();
  if (life.includes('archiv')) return 'archived';
  if (life.includes('merg')) return 'merged';
  if (mission.standing === 'terminal' || status.status === 'ended') return 'closed';
  return 'open';
}

function pillsFor(
  mission: DiMission,
  status: ReturnType<typeof missionStatus>,
  tab: DiScanTab,
  needsYou: DiScanKnown<boolean>,
): DiScanPill[] {
  const life = mission.lifecycleState.toLowerCase();
  const pills: DiScanPill[] = [];
  if (knownTrue(needsYou)) pills.push({ id: 'needs', label: 'Needs you', tone: 'needs' });
  if (status.status === 'running')
    pills.push({ id: 'building', label: 'Building', tone: 'building' });
  if (status.status === 'failed' || life.includes('fail'))
    pills.push({ id: 'failed', label: 'Failed', tone: 'failed' });
  if (tab === 'merged') pills.push({ id: 'merged', label: 'Merged', tone: 'merged' });
  // Every row says its status (CONTROL-MAP "01 Home": status dot + word): where none of the above applies, the
  // operational status's own word, never nothing. A stopped record says Stopped rather than the broader Ended.
  if (!pills.some((pill) => pill.id !== 'needs')) {
    if (status.status === 'blocked') pills.push({ id: 'blocked', label: 'Blocked', tone: 'needs' });
    else if (status.status === 'ended' && life === 'aborted')
      pills.push({ id: 'stopped', label: 'Stopped', tone: 'idle' });
    else pills.push({ id: status.status, label: status.label, tone: 'idle' });
  }
  return pills;
}

function roleKey(role: string): string {
  return role.trim().toLowerCase();
}

function roleRank(role: string): number {
  const index = ROLE_ORDER.indexOf(role);
  return index === -1 ? ROLE_ORDER.length : index;
}

/** One seat per role. Duplicate listings of the same role must not crowd out a later role. */
function swarmFor(mission: DiMission): DiScanAvatar[] {
  if (mission.members.state !== 'observed') return [];
  const chosen = new Map<string, DiMember>();
  for (const member of mission.members.members) {
    const key = roleKey(member.role);
    if (key === '') continue;
    const prev = chosen.get(key);
    if (prev === undefined || betterSeat(member, prev)) chosen.set(key, member);
  }
  return [...chosen.entries()]
    .sort((a, b) => roleRank(a[0]) - roleRank(b[0]) || a[0].localeCompare(b[0]))
    .map(([, member], index) => avatarFromMember(member, index));
}

function betterSeat(next: DiMember, prev: DiMember): boolean {
  const score = (member: DiMember) =>
    (member.characterId !== null ? 2 : 0) + (paneText(member) !== '' ? 1 : 0);
  return score(next) > score(prev);
}

function noteForActivity(activity: string): string {
  const value = activity.trim();
  const key = value.toLowerCase();
  if (key === 'busy' || key === 'working') return 'In progress.';
  if (key === 'blocked') return 'Blocked.';
  if (key === 'waiting' || key === 'idle') return 'Waiting.';
  if (key === 'done') return 'Done.';
  if (value === '') return '';
  return value.endsWith('.') ? value : `${value}.`;
}

function paneText(member: DiMember): string {
  const pane = member.paneId;
  if (pane !== undefined && pane.state === 'observed' && pane.value !== null && pane.value !== '')
    return pane.value;
  return '';
}

/**
 * The name under the role. The four swarm roles always wear their locked character
 * (Ken, Leo, Mia, Sam), never the seat key (`lead1`, `supervisor`) and never a blank.
 * `logical` is only a fallback for a role outside that cast.
 */
export function plainAgentName(role: string, logical: string): string {
  const cast = ROLE_CAST[roleKey(role)];
  if (cast) return cast.name;
  const raw = logical.trim();
  const compact = raw.toLowerCase().replace(/[\s_-]+/g, '');
  if (/^(supervisor|lead|executor|reviewer)\d*$/.test(compact)) return roleLabel(role);
  if (/^[a-z][a-z'-]{1,30}$/.test(raw)) return `${raw.charAt(0).toUpperCase()}${raw.slice(1)}`;
  return raw !== '' ? raw : roleLabel(role);
}

function plainLifecycle(state: string): string {
  const key = state.trim().toLowerCase();
  const known: Record<string, string> = {
    active: 'Active',
    aborted: 'Stopped',
    merged: 'Merged',
    closed: 'Closed',
    archived: 'Archived',
    failed: 'Failed',
  };
  if (known[key]) return known[key];
  if (key === '') return 'Status not recorded';
  return state.replace(/[_-]+/g, ' ').replace(/\b\w/g, (letter) => letter.toUpperCase());
}

/** A record source in plain words ("Herdr", or the source id's own words). */
export function plainSource(sourceId: string): string {
  const known: Record<string, string> = {
    di_herdr_tasks: 'Herdr',
    herdr_tasks: 'Herdr',
    herd: 'Herdr',
    herdr: 'Herdr',
  };
  if (known[sourceId]) return known[sourceId];
  const words = sourceId.replace(/^di_/, '').replace(/[_-]+/g, ' ').trim();
  if (words === '') return 'Recorded source';
  return words.replace(/\b[a-z]/g, (letter) => letter.toUpperCase());
}

function plainContext(lifecycle: string, sourceId: string): string {
  return `${plainLifecycle(lifecycle)} · ${plainSource(sourceId)}`;
}

function plainFooter(shortRef: string, sourceId: string): string {
  const source = plainSource(sourceId);
  const suffix = ` · ${sourceId}`;
  if (shortRef.endsWith(suffix)) return `${shortRef.slice(0, -sourceId.length)}${source}`;
  return shortRef;
}

/**
 * A role's model, said with how it is known: observed on a live member, stale on one that is not live, and every absence
 * named as itself.
 */
export function modelFact(value: DiObservedValue, live: boolean): DiScanFact {
  switch (value.state) {
    case 'observed': {
      const v = (value.value ?? '').trim();
      if (v === '' || v.toLowerCase() === 'unknown')
        return { kind: 'not_recorded', value: null, text: 'Model not reported' };
      return live
        ? { kind: 'observed', value: v, text: `Model ${v} (observed)` }
        : { kind: 'stale', value: v, text: `Model ${v} (last seen, not live)` };
    }
    case 'known_empty':
      return { kind: 'none_reported', value: null, text: 'Model none reported' };
    case 'not_recorded':
      return { kind: 'not_recorded', value: null, text: 'Model not recorded' };
    case 'projected_reason':
    case 'unavailable':
      return { kind: 'unavailable', value: null, text: 'Model unavailable' };
    case 'not_applicable':
      return { kind: 'not_applicable', value: null, text: 'Model does not apply to this source' };
  }
}

/** A role's provider: a configured string, said to be configured. */
export function providerFact(provider: string): DiScanFact {
  const v = provider.trim();
  return v === ''
    ? { kind: 'not_recorded', value: null, text: 'Provider not recorded' }
    : { kind: 'configured', value: v, text: `Provider ${v} (configured)` };
}

const NOT_RECORDED: DiObservedValue = { state: 'not_recorded', value: null, reason: null };

function avatarFromMember(member: DiMember, index: number): DiScanAvatar {
  const key = member.role.toLowerCase();
  const name = plainAgentName(
    member.role,
    member.logical.trim() !== '' ? member.logical : member.agent,
  );
  const note = noteForActivity(member.activity);
  return {
    role: roleLabel(member.role),
    name,
    palette: ROLE_CAST[key]?.palette ?? index % 6,
    note,
    characterId: member.characterId,
    // a record that carries no model reads as "not recorded", never as a guess
    model: modelFact(member.model ?? NOT_RECORDED, member.live === true),
    provider: providerFact(member.provider),
  };
}

/**
 * C-U1 (pass C): what each kind of Reception item is called for a person using Missions and Reception (by the server's
 * stable category id), so the screen never speaks the record contracts. A category this build does not know keeps the
 * server's own label in plain wording.
 */
const UNCHECKED_WORDS: Readonly<Record<string, string>> = {
  blockers: 'blockers',
  clarification: 'questions for you',
  authorization: 'approvals',
  failed_verification: 'failed checks',
  ready_for_review: 'results to review',
  delivery_decision: 'delivery decisions',
};

/** "a, b and c" */
function listed(words: readonly string[]): string {
  return words.length <= 1 ? (words[0] ?? '') : `${words.slice(0, -1).join(', ')} and ${words[words.length - 1]}`;
}

/** A server notice in a person's words (C-U1); its reason code stays in `codes`. */
function noticeWords(reason: string): string {
  if (reason.startsWith('connection_unavailable')) return 'Reception could not be read.';
  if (reason.startsWith('envelope_not_fresh')) return "Reception's information is out of date.";
  return 'Part of Reception could not be read.';
}

/**
 * Why the SERVER's Reception cannot say a Mission does NOT need the person (round 6). Every category this Mission's
 * approved scope REQUIRES counts (CONTROL-MAP: clarification and delivery decisions included), so coverage that is not
 * given (unsupported), cannot be given right now (unavailable, a stale answer), or is given only for some Missions, is
 * MISSING coverage and reads as unknown, never as "does not need you". Empty only when coverage is whole.
 * C-U1 (pass C, Supervisor): the screen gets ONE short plain summary, said once however many categories are missing
 * ("Some things could not be checked: questions for you, delivery decisions and approvals."), in a person's words, never
 * the implementation's (which record or catalog carries what). The server's reason codes are kept, every one, in `codes`.
 */
export function receptionDoubt(reception: NonNullable<DiOfficeState['reception']>): {
  text: string[];
  codes: string[];
} {
  const text: string[] = [];
  const codes: string[] = [];
  for (const notice of reception.notices ?? []) {
    text.push(noticeWords(notice.reason));
    codes.push(notice.reason);
  }
  const unchecked: string[] = [];
  for (const category of reception.categories ?? []) {
    const word = UNCHECKED_WORDS[category.category] ?? plainServerWording(category.label).toLowerCase();
    if (category.outcome === 'unsupported' || category.outcome === 'unavailable') {
      const code = category.reason ?? category.gaps[0]?.reason ?? null;
      if (code !== null) codes.push(code);
      unchecked.push(word);
      continue;
    }
    if (category.gaps.length > 0) unchecked.push(word);
    for (const gap of category.gaps) codes.push(gap.reason);
  }
  const words = [...new Set(unchecked)];
  if (words.length > 0) text.push(`Some things could not be checked: ${listed(words)}.`);
  if ((reception.categories ?? []).length === 0) text.push('Reception says nothing about what it checked.');
  return { text: [...new Set(text)], codes: [...new Set(codes)] };
}

/** Whether Reception says this Mission needs the person, with the uncertainty kept (rounds 5 and 6). */
function needsYouFrom(
  reception: DiOfficeState['reception'],
  listed: ReadonlySet<string>,
  missionRef: string,
  doubt: { text: readonly string[]; codes: readonly string[] },
): DiScanKnown<boolean> {
  if (reception === undefined || reception === null)
    return { known: false, why: { kind: 'not_read', text: 'Reception is not read in this office.' } };
  if (listed.has(missionRef)) return known(true);
  const total = reception.entriesTotal;
  // absence from a list that was cut short is not evidence of absence
  if (typeof total === 'number' && total > reception.entries.length)
    return {
      known: false,
      why: { kind: 'not_known', text: `Reception lists only ${reception.entries.length} of ${total} items.` },
    };
  // not listed: "does not need you" only where the server's coverage is whole; otherwise its own reasons say why not
  if (doubt.text.length > 0)
    return { known: false, why: { kind: 'not_known', text: doubt.text.join(' '), codes: doubt.codes } };
  return known(false);
}

/** Whether the Mission is running: only a CURRENT status decides it; stale, disconnected or unknown stays unknown. */
function runningFrom(status: ReturnType<typeof missionStatus>): DiScanKnown<boolean> {
  switch (status.status) {
    case 'stale':
      return {
        known: false,
        why: { kind: 'not_known', text: 'its status is not current while the office is not live' },
      };
    case 'disconnected':
      return {
        known: false,
        why: { kind: 'not_known', text: 'the office is disconnected, so its status is not current' },
      };
    case 'unknown':
      return { known: false, why: { kind: 'not_known', text: 'its status is not known' } };
    default:
      return known(status.status === 'running');
  }
}

/** Rows for the scan panel from the office state. No pull-request line is synthesised. */
export function scanRowsFromOffice(state: DiOfficeState, panel: PanelSource = { kind: 'not_provided' }): DiScanRow[] {
  const listed = new Set((state.reception?.entries ?? []).map((entry) => entry.missionRef));
  const doubt = state.reception ? receptionDoubt(state.reception) : { text: [], codes: [] };
  const shorts = shortMissionRefs(state.missions.missions);
  const waiting = decisionsByMission(panel, state.missions.missions);
  return state.missions.missions.map((mission) => {
    const refs = waiting.get(mission.missionRef) ?? null;
    const status = missionStatus(mission, state);
    const tab = tabForMission(mission, status);
    const needsYou = needsYouFrom(state.reception, listed, mission.missionRef, doubt);
    const objective = missionObjective(mission);
    const reviewOffer = reviewOfferOf(mission);
    return {
      id: mission.missionRef,
      title: missionTitle(mission).text,
      context: plainContext(mission.lifecycleState, mission.sourceId),
      // with no goal reported, the headline says only that; the source-qualified note with its state is this secondary detail (plain-English naming, 2026-10-28)
      description: objective.kind === 'goal' ? plainMissionDescription(objective.text) : objective.text,
      pills: pillsFor(mission, status, tab, needsYou),
      tab,
      needsYou,
      swarm: swarmFor(mission),
      footer: plainFooter(shorts.get(mission.missionRef) ?? mission.missionRef, mission.sourceId),
      handoff: '',
      updated: '',
      reviewOffer,
      // offered by a member, or by the finished Mission as a whole: the one question reviewOfferOf answers
      reviewOffered: reviewOffer.kind === 'offered',
      // The record carries no delivery stage; M8 reads ONE linked delivery's status in Mission details, through its own session.
      delivery: { source: 'not_read', stage: null },
      deliveryLink: deliveryLinkOf(mission),
      sourceKind: mission.sourceKind,
      // "running" is read from the record, and "awaiting a decision" from the request panel when one of its requests waiting for `decide` ties to this Mission. Every other fact is NOT
      // read, so it is null, never false: the panel must not turn an unread fact into "nothing is waiting" (the request panel lists only its own requests, so no tie is not "no decision").
      state: {
        source: 'record',
        running: runningFrom(status),
        awaitingApproval: refs !== null ? true : null,
        repoRequest: null,
        stalled: null,
        logRecorded: null,
      },
      decision: refs !== null ? { requestRef: refs.length === 1 ? refs[0]! : null, count: refs.length } : null,
      evidenceListed: mission.evidence !== undefined,
      ...(mission.sourceKind === 'herdr_task' ? { reviewThread: herdrReviewThread(mission) } : {}),
      // The office record carries no owner or repos: the table says "Not recorded" (none is invented). The four-step phase is the specimen's alone; a real row shows the producer's DECLARED
      // workflow phase as its own word (`declared`), with its absences kept apart. Last activity is the record's own update time on the office clock, when both are recorded.
      table: {
        owner: null,
        repos: null,
        phase: null,
        declared: declaredPhaseOf(mission),
        last: lastActivity(mission.updatedAt, state.times?.evaluatedAt),
      },
    };
  });
}

export function rowOnTab(row: DiScanRow, tab: DiScanTab): boolean {
  if (row.tab === tab) return true;
  // A row that carries a Merged pill stays reachable from Merged even when its list tab is Open
  // (the locked mock shows those pills on the Open list).
  return tab === 'merged' && row.pills.some((pill) => pill.tone === 'merged');
}

export function visibleScanRows(
  rows: readonly DiScanRow[],
  tab: DiScanTab,
  query: string,
  needsOnly: boolean,
  hidden: ReadonlySet<string>,
): DiScanRow[] {
  const q = query.trim().toLowerCase();
  return rows.filter((row) => {
    if (hidden.has(row.id) || !rowOnTab(row, tab)) return false;
    if (needsOnly && !knownTrue(row.needsYou)) return false;
    if (q === '') return true;
    const hay = [
      row.title,
      row.context,
      row.description,
      row.footer,
      ...row.pills.map((pill) => pill.label),
      ...row.swarm.flatMap((avatar) => [avatar.name, avatar.role]),
    ]
      .join('\n')
      .toLowerCase();
    return hay.includes(q);
  });
}

/** Open-tab missions that need the person. This is the count on the Open tab. */
export function openAttentionCount(
  rows: readonly DiScanRow[],
  hidden: ReadonlySet<string>,
): number {
  return rows.filter((row) => !hidden.has(row.id) && row.tab === 'open' && knownTrue(row.needsYou))
    .length;
}

/**
 * Which face the office panel shows. The product default is the locked scan panel. Harnesses and
 * e2e set an explicit operational face (or the e2e flag) so their existing contracts stay on the
 * operational list.
 */
export function officePanelPresentation(input: {
  face?: string | null;
  e2e?: boolean;
}): 'scan' | 'operational' {
  if (input.face === 'scan') return 'scan';
  if (input.face === 'operational' || input.e2e === true) return 'operational';
  return 'scan';
}

const lockAvatar = (
  role: string,
  name: string,
  palette: number,
  note: string,
  model: DiScanFact,
  provider: DiScanFact,
): DiScanAvatar => ({
  role,
  name,
  palette,
  note,
  characterId: null,
  model,
  provider,
});

/** Sample model observations: one of each kind the Swarm must keep apart (observed, stale, not recorded). */
const sampleModel = (value: string | null, live: boolean): DiScanFact =>
  modelFact(
    value === null
      ? { state: 'not_recorded', value: null, reason: null }
      : { state: 'observed', value, reason: null },
    live,
  );

const LOCK_SWARM: readonly DiScanAvatar[] = [
  lockAvatar(
    'Supervisor',
    'Ken',
    0,
    'All clear. No blockers.',
    sampleModel('gpt-5.5', true),
    providerFact('codex'),
  ),
  lockAvatar(
    'Lead',
    'Leo',
    1,
    'Drafting structure.',
    sampleModel('claude-opus-5', true),
    providerFact('claude'),
  ),
  lockAvatar(
    'Executor',
    'Mia',
    2,
    'In progress.',
    sampleModel('claude-opus-5', false),
    providerFact('claude'),
  ),
  lockAvatar('Reviewer', 'Sam', 3, 'Waiting.', sampleModel(null, true), providerFact('codex')),
];

const lockState = ({
  running = false,
  ...facts
}: Partial<Omit<DiScanStateFacts, 'running'>> & { running?: boolean }): DiScanStateFacts => ({
  source: 'fixture',
  awaitingApproval: false,
  repoRequest: false,
  stalled: false,
  logRecorded: false,
  ...facts,
  running: known(running),
});

/** The public harness every Mission runs from (D107, AC-4.3-45). */
export const DI_HARNESS_REPO = 'dodging-infinity';

/** Sample table facts: the person owns every sample Mission; repos are the harness plus `targets` target repos. */
const lockTable = (targets: number, phase: DiScanPhase, last: string): DiScanTableFacts => ({
  owner: 'You',
  repos: { harness: DI_HARNESS_REPO, targets },
  phase,
  declared: null,
  last,
});

/** The number is joined in so "#4821" is not one colour-like literal. */
const prLine = (number: string, rest: string): string => `PR #${number} · ${rest}`;

/**
 * The locked mock (Travis Sellers, 2026-10-25): the scan panel's visual source of truth.
 * Sample missions, not live records.
 */
export const DI_SCAN_LOCK: readonly DiScanRow[] = [
  {
    id: 'lock-next-panel',
    title: 'Next panel overhaul',
    context: 'D7 passed · next panel overhaul',
    description:
      'Redesign and implementation of the next iteration of the core panel system. Includes layout engine updates, responsive primitives, and migration plan.',
    pills: [
      { id: 'needs', label: 'Needs you', tone: 'needs' },
      { id: 'building', label: 'Building', tone: 'building' },
    ],
    tab: 'open',
    needsYou: known(true),
    swarm: LOCK_SWARM,
    footer: prLine('4821', 'CI passed on D7 · 2h ago'),
    handoff:
      'Supervisor has the context. Reviewer queue is clear.\nNo blockers.\nNext: Lead handoff to Executor.',
    updated: 'Updated just now',
    reviewOffered: false,
    reviewOffer: DI_SCAN_NO_REVIEW_INFO,
    delivery: { source: 'fixture', stage: 'ci_passed' },
    state: lockState({ running: true, awaitingApproval: true }),
    table: lockTable(1, { word: 'Delivery', step: 4, finished: false }, 'now'),
  },
  {
    id: 'lock-auth',
    title: 'Auth service migration',
    context: 'D7 passed · auth-service',
    description:
      'Move the authentication service onto the current identity contract and retire the legacy session store.',
    pills: [
      { id: 'merged', label: 'Merged', tone: 'merged' },
      { id: 'pass', label: 'CI pass', tone: 'pass' },
    ],
    tab: 'open',
    needsYou: known(false),
    swarm: LOCK_SWARM,
    footer: prLine('4810', 'CI passed on D7 · 5h ago'),
    handoff: '',
    updated: '',
    reviewOffered: false,
    reviewOffer: DI_SCAN_NO_REVIEW_INFO,
    delivery: { source: 'fixture', stage: 'merged' },
    state: lockState({ logRecorded: true }),
    table: lockTable(1, { word: 'Delivery', step: 4, finished: true }, '5h'),
  },
  {
    id: 'lock-payments',
    title: 'Payment retry engine',
    context: 'D6 failed · payments',
    description:
      'Retry failed payment captures with a bounded backoff, and show the failure when the gateway keeps rejecting them.',
    pills: [
      { id: 'building', label: 'Building', tone: 'building' },
      { id: 'failed', label: 'Failed', tone: 'failed' },
    ],
    tab: 'open',
    needsYou: known(false),
    swarm: LOCK_SWARM,
    footer: prLine('4794', 'CI failed on D6 · 1d ago'),
    handoff: '',
    updated: '',
    reviewOffered: false,
    reviewOffer: DI_SCAN_NO_REVIEW_INFO,
    delivery: { source: 'fixture', stage: 'ci_failed' },
    state: lockState({ running: true, logRecorded: true }),
    table: lockTable(1, { word: 'Delivery', step: 4, finished: false }, '1d'),
  },
  {
    id: 'lock-docs',
    title: 'Docs refresh & restructure',
    context: 'D7 passed · documentation',
    description:
      'Refresh the documentation structure so the panel, the world and the mission notes read as one system.',
    pills: [
      { id: 'merged', label: 'Merged', tone: 'merged' },
      { id: 'pass', label: 'CI pass', tone: 'pass' },
    ],
    tab: 'open',
    needsYou: known(false),
    swarm: LOCK_SWARM,
    footer: prLine('4802', 'CI passed on D7 · 3h ago'),
    handoff: '',
    updated: '',
    reviewOffered: false,
    reviewOffer: DI_SCAN_NO_REVIEW_INFO,
    delivery: { source: 'fixture', stage: 'merged' },
    state: lockState({ logRecorded: true }),
    table: lockTable(0, { word: 'Delivery', step: 4, finished: true }, '3h'),
  },
];

const v3Footer = (rest: string): string => `Sample Mission · ${rest}`;

/**
 * The v3 sample cast (reconciled-free-ui-v3 03-reception.png and 04-missions.png): five SAMPLE Missions, not live
 * records, one in each state Reception must draw (CI waiting, a question, a stall, CI accepted, a proposal). Used only
 * by the labelled specimen (the scan harness with `di-scan-cast=v3`), never by the product.
 */
export const DI_SCAN_V3_CAST: readonly DiScanRow[] = [
  {
    id: 'v3-inbox',
    title: 'Reception inbox grouping',
    context: 'CI waiting for you',
    description:
      'Group the Reception inbox by Mission, with one line on what each needs from you.',
    pills: [
      { id: 'needs', label: 'Needs you', tone: 'needs' },
      { id: 'building', label: 'Building', tone: 'building' },
    ],
    tab: 'open',
    needsYou: known(true),
    swarm: LOCK_SWARM,
    footer: v3Footer('CI passed, 4 of 4 checks'),
    handoff: '',
    updated: '',
    reviewOffered: false,
    reviewOffer: DI_SCAN_NO_REVIEW_INFO,
    delivery: { source: 'fixture', stage: 'ci_passed' },
    state: lockState({ running: true, logRecorded: true }),
    table: lockTable(1, { word: 'Delivery', step: 4, finished: false }, '2m'),
  },
  {
    id: 'v3-probe',
    title: 'Stabilize readiness probe',
    context: 'Lead asked a question',
    description:
      'Stop the readiness probe failing on slow starts, and settle how long the probe waits.',
    pills: [
      { id: 'needs', label: 'Needs you', tone: 'needs' },
      { id: 'building', label: 'Building', tone: 'building' },
    ],
    tab: 'open',
    needsYou: known(true),
    swarm: LOCK_SWARM,
    footer: v3Footer('review round 2'),
    handoff: '',
    updated: '',
    reviewOffered: false,
    reviewOffer: DI_SCAN_NO_REVIEW_INFO,
    delivery: { source: 'fixture', stage: 'working' },
    state: lockState({ running: true }),
    table: lockTable(0, { word: 'Review', step: 3, finished: false }, '6m'),
  },
  {
    id: 'v3-install',
    title: 'Install guide for new users',
    context: 'Executor stopped responding',
    description: 'Write the install guide for new users, and check every step on a clean machine.',
    pills: [{ id: 'stalled', label: 'Stalled', tone: 'failed' }],
    tab: 'open',
    needsYou: known(true),
    swarm: LOCK_SWARM,
    footer: v3Footer('stalled in Build'),
    handoff: '',
    updated: '',
    reviewOffered: false,
    reviewOffer: DI_SCAN_NO_REVIEW_INFO,
    delivery: { source: 'fixture', stage: 'working' },
    state: lockState({ stalled: true, logRecorded: true }),
    table: lockTable(0, { word: 'Build', step: 2, finished: false }, '13m'),
  },
  {
    id: 'v3-settings',
    title: 'Settings screen copy pass',
    // the merge language removed (owner ruling, 2026-10-28): the sample no longer says "Ready to merge"; delivery has no merge step
    context: 'CI results accepted',
    description: 'Tighten the Settings screen copy so every label is short and plain.',
    pills: [{ id: 'ready', label: 'CI accepted', tone: 'pass' }],
    tab: 'open',
    needsYou: known(true),
    swarm: LOCK_SWARM,
    footer: v3Footer('CI results accepted'),
    handoff: '',
    updated: '',
    reviewOffered: false,
    reviewOffer: DI_SCAN_NO_REVIEW_INFO,
    delivery: { source: 'fixture', stage: 'ci_accepted' },
    state: lockState({ logRecorded: true }),
    table: lockTable(0, { word: 'Delivery', step: 4, finished: false }, '1d'),
  },
  {
    id: 'v3-dark',
    title: 'Dark mode toggle',
    context: 'Proposal ready',
    description:
      'Add a dark mode toggle to Settings, put the theme tokens in pixel-agents so the world matches, and document it in the DI guide.',
    pills: [{ id: 'needs', label: 'Needs you', tone: 'needs' }],
    tab: 'open',
    needsYou: known(true),
    swarm: LOCK_SWARM,
    footer: v3Footer('proposal ready'),
    handoff: '',
    updated: '',
    reviewOffered: false,
    reviewOffer: DI_SCAN_NO_REVIEW_INFO,
    delivery: { source: 'fixture', stage: 'working' },
    state: lockState({ awaitingApproval: true }),
    table: lockTable(1, { word: 'Proposal', step: 1, finished: false }, 'now'),
  },
];

/**
 * The panel's screens. There is no Herdr screen: Herdr opens the machine's own terminal, never an in-app one
 * (GAP-RESOLUTIONS 9, 11, 13). `missions`, `reception`, `new` and `proposal` are rail pages (pass B); `mission`, `diff`
 * and `comments` are one Mission's nested screens.
 */
export type DiScanView =
  | 'list'
  | 'mission'
  | 'diff'
  | 'comments'
  | 'evidence'
  | 'missions'
  | 'reception'
  | 'new'
  | 'proposal';

/** A Mission's own nested screens (they need a selected Mission). */
export const DI_SCAN_MISSION_VIEWS: readonly DiScanView[] = ['mission', 'diff', 'comments', 'evidence'];

/**
 * R10, the established part: the Mission's GENERIC listed evidence, the existing evidence library, named with the existing heading of the office section that holds it. Never "View log" or "View
 * checks": no log or check kind exists (an artifact's key is opaque to its contract, so a key that reads like a log is listed under that key and never labelled one).
 */
export const DI_EVIDENCE_LABEL = 'Evidence and review';
export const DI_EVIDENCE_NOT_LISTED = 'The office lists no evidence library for this Mission.';
export const DI_EVIDENCE_NOT_AVAILABLE = 'The evidence library cannot be shown in this office.';
/** The rail's pages (they need no selected Mission). */
export const DI_SCAN_PAGE_VIEWS: readonly DiScanView[] = ['missions', 'reception', 'new', 'proposal'];

export interface DiScanTreeEntry {
  id: string;
  name: string;
  depth: number;
  kind: 'dir' | 'file';
  /** A changed file's mark (M modified, A added). Absent: unchanged. */
  mark?: string;
  /** Real data only: the mark's plain words ("deleted"), so the letter is never the only carrier. */
  markLabel?: string;
  /** Real data only: the row's whole accessible name (name, kind of change, and the file's state when it is not plain text). */
  rowLabel?: string;
  /** Real data only: the file's repository path, which the review session selects by (never sent to the office as a path). */
  path?: string;
}

export interface DiScanDiffLine {
  kind: 'ctx' | 'del' | 'add' | 'hunk';
  oldNo: string;
  newNo: string;
  text: string;
}

/** One file's changes. */
export interface DiScanDiffFile {
  path: string;
  /** The status bar's language word. */
  language: string;
  lines: readonly DiScanDiffLine[];
  /** Real data only: why there are no lines to draw (binary, withheld, not UTF-8, still being read ...), in words. */
  notice?: { state: string; title: string; body: string };
  /** Real data only: a sentence under the lines (the file was cut short, and by how much). */
  foot?: string;
}

/**
 * A Mission's code diff: the working tree, read only (CONTROL-MAP C3/C4). Nothing here edits or commits; delivery is the
 * Mission's own gate.
 */
export interface DiScanDiff {
  tree: readonly DiScanTreeEntry[];
  branch: string;
  filesChanged: string;
  added: string;
  removed: string;
  /** The status bar's file facts, carried BY the diff (never defaulted by the view). */
  encoding: string;
  lineEnding: string;
  indent: string;
  /** The file shown first (a tree id). */
  initial: string;
  /** Each changed file's diff, by tree id. A file listed in the tree but not here has no changes. */
  files: Readonly<Record<string, DiScanDiffFile>>;
  /** Real data only: the ONE note in the tree-note slot (whose changes these are, and what the listing does not cover). */
  note?: string;
  /** Real data only: the editor bar's tag (what was read, and when), in the slot the specimen labels "Sample diff (fixture)". */
  tag?: string;
  /** Real data only: why the totals are only a floor, when they are (said to assistive technology and as the group's title). */
  countsNote?: string;
  /**
   * Real data only: the COMPARISON BASE (route field `comparison.baseOid`), short for the bar and whole for its title. It is the revision the
   * diff is computed against; it is NOT the checkout's identity (`target.head`), which is a different field and is carried in `branchTitle`.
   */
  base?: { short: string; full: string };
  /**
   * Real data only: ONE collective phrase for the file facts the route does not carry (encoding, line ending, indentation), in place of
   * the three per-fact values the specimen draws (its sample "UTF-8", "LF", "Spaces: 2"). `visible` is what the bar shows; `accessible` ENUMERATES the three facts, for assistive
   * technology and the hover title. The collective form is honest only while all three are unavailable together (see
   * `DI_DIFF_STATUS_WORDS.format` in diScanDiffRead.ts for the condition it depends on).
   */
  format?: { visible: string; accessible: string };
  /**
   * Real data only: the branch in full, for its title (the visible text may be shortened or truncated; the full value stays reachable).
   * For a detached HEAD it is `Detached HEAD at <target.head>`: the checkout's own revision, from its own field, never the base's.
   */
  branchTitle?: string;
  /** Real data only: words that complete the branch's accessible name when the visible text is shorter (a detached HEAD shows "Detached"). */
  branchHidden?: string;
  /**
   * Real data only: true when the visible branch text is a FIXED LABEL ("Detached", "Branch unknown"), not a variable value. The bar may
   * truncate a real branch name with an ellipsis (its full value stays in the title and the text); it must never truncate a fixed label.
   */
  branchFixed?: true;
}


const LOCK_TREE: readonly DiScanTreeEntry[] = [
  { id: 'world', name: 'world', depth: 0, kind: 'dir' },
  { id: 'open', name: 'open', depth: 1, kind: 'dir' },
  { id: 'npo', name: 'next-panel-overhaul', depth: 2, kind: 'dir' },
  { id: 'code', name: 'code-diff.ts', depth: 3, kind: 'file', mark: 'M' },
  { id: 'components', name: 'components', depth: 1, kind: 'dir' },
  { id: 'next', name: 'NextPanel.tsx', depth: 2, kind: 'file', mark: 'M' },
  { id: 'header', name: 'PanelHeader.tsx', depth: 2, kind: 'file', mark: 'A' },
  { id: 'hooks', name: 'hooks', depth: 1, kind: 'dir' },
  { id: 'use', name: 'useNextPanel.ts', depth: 2, kind: 'file', mark: 'M' },
  { id: 'utils', name: 'utils', depth: 1, kind: 'dir' },
  { id: 'diff', name: 'diff.ts', depth: 2, kind: 'file', mark: 'M' },
  { id: 'parse', name: 'parse.ts', depth: 2, kind: 'file', mark: 'M' },
  { id: 'styles', name: 'styles.css', depth: 0, kind: 'file', mark: 'M' },
  { id: 'types', name: 'types.ts', depth: 0, kind: 'file', mark: 'M' },
  { id: 'index', name: 'index.ts', depth: 0, kind: 'file' },
];

const ctx = (n: number, text: string): DiScanDiffLine => ({
  kind: 'ctx',
  oldNo: String(n),
  newNo: String(n),
  text,
});
const del = (n: number, text: string): DiScanDiffLine => ({ kind: 'del', oldNo: String(n), newNo: '', text });
const add = (n: number, text: string): DiScanDiffLine => ({ kind: 'add', oldNo: '', newNo: String(n), text });
const hunk = (text: string): DiScanDiffLine => ({ kind: 'hunk', oldNo: '', newNo: '', text });

/** The locked code-diff nest for the first specimen Mission: sample changes, one per changed file. */
export const DI_SCAN_LOCK_DIFF: DiScanDiff = {
  branch: 'main',
  filesChanged: '8 files changed',
  added: '124',
  removed: '38',
  encoding: 'UTF-8',
  lineEnding: 'LF',
  indent: 'Spaces: 2',
  tree: LOCK_TREE,
  initial: 'code',
  files: {
    code: {
      path: 'src/world/open/next-panel-overhaul/code-diff.ts',
      language: 'TypeScript',
      lines: [
        hunk('@@ -42,14 +42,14 @@'),
        ctx(42, 'export function createDiffView(oldText: string, newText: string): DiffChange[] {'),
        ctx(43, "  const oldLines = oldText.split('\\n');"),
        ctx(44, "  const newLines = newText.split('\\n');"),
        ctx(45, '  const differences: DiffChange[] = [];'),
        ctx(46, ''),
        ctx(47, '  let i = 0, j = 0;'),
        ctx(48, ''),
        del(49, '  // Simple diff implementation'),
        add(49, '  // Two-pointer diff implementation'),
        ctx(50, '  while (i < oldLines.length || j < newLines.length) {'),
        ctx(51, '    // ... rest of implementation'),
        ctx(52, '  }'),
        ctx(53, ''),
        ctx(54, '  return differences;'),
        ctx(55, '}'),
      ],
    },
    next: {
      path: 'src/world/components/NextPanel.tsx',
      language: 'TypeScript JSX',
      lines: [
        hunk('@@ -12,7 +12,8 @@'),
        ctx(12, 'export function NextPanel({ rows }: NextPanelProps) {'),
        del(13, '  const shown = rows;'),
        add(13, '  const shown = useNextPanel(rows);'),
        add(14, '  const empty = shown.length === 0;'),
        ctx(14, '  return ('),
        ctx(15, '    <section className="next-panel">'),
      ],
    },
    header: {
      path: 'src/world/components/PanelHeader.tsx',
      language: 'TypeScript JSX',
      lines: [
        hunk('@@ -0,0 +1,6 @@'),
        add(1, 'export function PanelHeader({ title }: { title: string }) {'),
        add(2, '  return ('),
        add(3, '    <header className="panel-header">'),
        add(4, '      <h2>{title}</h2>'),
        add(5, '    </header>'),
        add(6, '  );'),
      ],
    },
    use: {
      path: 'src/world/hooks/useNextPanel.ts',
      language: 'TypeScript',
      lines: [
        hunk('@@ -3,6 +3,7 @@'),
        ctx(3, 'export function useNextPanel(rows: Row[]): Row[] {'),
        del(4, '  return rows;'),
        add(4, '  const [query] = useQuery();'),
        add(5, '  return rows.filter((row) => row.title.includes(query));'),
        ctx(5, '}'),
      ],
    },
    diff: {
      path: 'src/world/utils/diff.ts',
      language: 'TypeScript',
      lines: [
        hunk('@@ -20,5 +20,5 @@'),
        ctx(20, 'export function lineCount(text: string): number {'),
        del(21, "  return text.split('\\n').length - 1;"),
        add(21, "  return text === '' ? 0 : text.split('\\n').length;"),
        ctx(22, '}'),
      ],
    },
    parse: {
      path: 'src/world/utils/parse.ts',
      language: 'TypeScript',
      lines: [
        hunk('@@ -8,4 +8,5 @@'),
        ctx(8, 'export function parseHunk(header: string): Hunk | null {'),
        add(9, '  if (!header.startsWith(\'@@\')) return null;'),
        ctx(9, '  const match = HUNK.exec(header);'),
        ctx(10, '  return match ? toHunk(match) : null;'),
      ],
    },
    styles: {
      path: 'src/styles.css',
      language: 'CSS',
      lines: [
        hunk('@@ -31,4 +31,4 @@'),
        ctx(31, '.next-panel {'),
        del(32, '  gap: 8px;'),
        add(32, '  gap: 12px;'),
        ctx(33, '}'),
      ],
    },
    types: {
      path: 'src/types.ts',
      language: 'TypeScript',
      lines: [
        hunk('@@ -1,4 +1,5 @@'),
        ctx(1, 'export interface Row {'),
        ctx(2, '  id: string;'),
        ctx(3, '  title: string;'),
        add(4, '  phase: string;'),
        ctx(4, '}'),
      ],
    },
  },
};

/**
 * UNKNOWN IS NOT ABSENT (review finding 1, the trash scope, pass B B-H1: one rule). Every fact a scan view shows comes
 * from a source that says HOW it is known, so a view cannot turn "not looked at" into "none", or invent a default:
 *  - `sample`: labelled sample data, drawn with the label it carries;
 *  - `not_read`: this build has not looked (the view SAYS so, and shows no metadata for it);
 *  - `unavailable`: it was looked for and cannot be had here (the view says why).
 * The two absences stay distinct all the way to the words on screen.
 */
export type DiScanDiffSource =
  | { kind: 'sample'; diff: DiScanDiff }
  // Stage 4: the route READ this Mission's own working tree; `selectedPath`/`firstOpenablePath` let the view choose a file
  | {
      kind: 'read';
      diff: DiScanDiff;
      selectedPath: string | null;
      firstOpenablePath: string | null;
      /** An empty listing's own words (a finding, not a verdict). Absent when files were listed. */
      emptyNotice?: { title: string; body: string };
    }
  | { kind: 'not_read'; reason: string }
  // asked, nothing has come back yet: not "empty", not "not read"
  | { kind: 'reading'; reason: string }
  // the office says no review is offered for this Mission (or the session was cleared): the route's own reason, in words
  | { kind: 'unavailable'; reason: string; title: string; detail: string }
  // asked, and the office or the backend refused: the route's own problem, in words
  | { kind: 'refused'; reason: string; title: string; detail: string };

export const DI_DIFF_NOT_READ = 'This office has not said whether a code review is offered for this Mission.';
/** An offered review has not returned a listing yet. Not a layout preview, and not an empty diff. */
export const DI_DIFF_READING = 'Reading this Mission’s working tree now.';

/**
 * What Code Diff can say from the row alone. The labelled specimen stays a sample. A real Mission
 * follows the office's review offer: offered (a bound workspace the route can read), unavailable
 * (the office's own reason), or unknown (the office said nothing, so nothing is claimed).
 */
export function scanDiff(row: DiScanRow): DiScanDiffSource {
  if (row.id === 'lock-next-panel') return { kind: 'sample', diff: DI_SCAN_LOCK_DIFF };
  const offer = row.reviewOffer;
  if (offer.kind === 'offered') return { kind: 'reading', reason: DI_DIFF_READING };
  if (offer.kind === 'unavailable') {
    return {
      kind: 'unavailable',
      reason: reviewNotOfferedConsequence(offer.reason),
      title: 'Code review is not offered',
      detail: unavailableWords(offer.reason),
    };
  }
  return { kind: 'not_read', reason: DI_DIFF_NOT_READ };
}

/** One comment in a Mission's thread. */
export interface DiScanComment {
  id: string;
  author: string;
  /** Bust index for the author's avatar. */
  palette: number;
  when: string;
  text: string;
}

/**
 * The SAMPLE thread for the first specimen Mission (07-comments.png). Comments are shared only in Paid (D106); in Free a
 * real Mission has no thread, and this sample exists only to draw the full-width thread layout.
 */
export const DI_SCAN_LOCK_THREAD: readonly DiScanComment[] = [
  {
    id: 'c1',
    author: 'Ken',
    palette: 0,
    when: '2h ago',
    text: 'Layout engine updates look good. Need to finalize the migration plan.',
  },
  {
    id: 'c2',
    author: 'Leo',
    palette: 1,
    when: '1h ago',
    text: 'Should we include the new responsive primitives in this iteration?',
  },
  {
    id: 'c3',
    author: 'Mia',
    palette: 2,
    when: '45m ago',
    text: 'Working on the primitives now. Ready for review by end of day.',
  },
  { id: 'c4', author: 'Sam', palette: 3, when: '10m ago', text: 'Looks solid. I will take a look.' },
];

/** Comments are shared in Paid (D106); in Free a Mission's thread is UNAVAILABLE, which this label says, and nothing more. */
export const DI_COMMENTS_PAID =
  'Comments are part of Paid team sharing. They are not available in Free.';

/**
 * No `not_read` kind, by the domain: in Free a thread is unavailable by edition (D106), so there is nothing to have read
 * or not. A Paid thread could be unread; Paid is outside this slice, and adding it must add that kind.
 */
export type DiScanThreadSource =
  | { kind: 'sample'; thread: readonly DiScanComment[] }
  | { kind: 'read'; thread: readonly DiScanComment[] }
  | { kind: 'unavailable'; reason: string };

/** A Herdr task whose evidence library was not on the office message. */
export const DI_HERDR_COMMENTS_UNREPORTED = 'Review comments are not reported for this Herdr task.';
/** The library was read and records no review. */
export const DI_HERDR_COMMENTS_NOT_RECORDED = 'A review is not recorded for this Herdr task.';

function herdrReviewThread(mission: DiMission): DiScanThreadSource {
  const reviews = mission.evidence?.reviews;
  if (reviews === undefined) return { kind: 'unavailable', reason: DI_HERDR_COMMENTS_UNREPORTED };
  if (reviews.state === 'observed' && reviews.items.length > 0) {
    return {
      kind: 'read',
      thread: reviews.items.map((review, index) => {
        const findings =
          review.findings.state === 'present' ? review.findings.items.map((item) => item.summary).join(' ') : '';
        const verdict = review.verdict ?? review.protocolToken ?? 'Review recorded';
        const author = review.reviewerLogical ?? review.reviewerAgent ?? 'the recorded reviewer';
        return {
          id: `review-${review.round ?? index}`,
          author,
          palette: index % 6,
          when: review.round !== null ? `Round ${review.round}` : '',
          text: findings !== '' ? `${verdict}. ${findings}` : verdict,
        };
      }),
    };
  }
  if (reviews.state === 'known_empty' || reviews.state === 'not_recorded') {
    return { kind: 'unavailable', reason: DI_HERDR_COMMENTS_NOT_RECORDED };
  }
  const why = reviews.reason ? `${reviews.state}: ${reviews.reason}` : reviews.state;
  return { kind: 'unavailable', reason: `Review comments are not available for this Herdr task (${why}).` };
}

export function scanThread(row: DiScanRow): DiScanThreadSource {
  if (row.id === 'lock-next-panel') return { kind: 'sample', thread: DI_SCAN_LOCK_THREAD };
  if (row.reviewThread !== undefined) return row.reviewThread;
  if (row.sourceKind === 'herdr_task') return { kind: 'unavailable', reason: DI_HERDR_COMMENTS_UNREPORTED };
  return { kind: 'unavailable', reason: DI_COMMENTS_PAID };
}

/**
 * Whether the office READ its Missions. `read` covers an observed list and a list read as empty; `not_read` and
 * `unavailable` are different facts and are said differently (an unread list is never "No Missions yet").
 */
export type DiScanMissionsRead =
  | { kind: 'read' }
  | { kind: 'not_read'; reason: string }
  | { kind: 'unavailable'; reason: string };

export const DI_MISSIONS_READ: DiScanMissionsRead = { kind: 'read' };

export function missionsRead(list: {
  state: DiObservedValue['state'];
  reason: string | null;
}): DiScanMissionsRead {
  if (list.state === 'observed' || list.state === 'known_empty') return DI_MISSIONS_READ;
  if (list.state === 'unavailable' || list.state === 'projected_reason')
    return {
      kind: 'unavailable',
      reason: `Missions are unavailable: ${list.reason ?? 'the office gave no reason'}.`,
    };
  return {
    kind: 'not_read',
    reason: `Missions are not read in this office${list.reason ? `: ${list.reason}` : ''}.`,
  };
}

// ---------------------------------------------------------------------------------------------
// Mission details: presentation rules for Delivery, state actions and the trash (pass A, Stage 2).
// Nothing here performs an action. Each control drawn from deliverySteps (the specimen, and a Mission with no linked delivery)
// and from the state actions and the trash is disabled at this stage, until Stage 4 wires it to an existing human-gated route.
// M8 wires Delivery's COMMIT, PUSH and PR_CREATE for a Mission whose ONE delivery the catalog links (linkedDeliverySteps), through
// Mission details' own delivery session: its one action is the b3 local confirmation, and the adapter's guard decides.

export type DiDeliveryStepId = 'commit' | 'push' | 'pr' | 'accept_ci';

/**
 * The merge language REMOVED (owner ruling, 2026-10-28; owner-decisions.md:93): there is no merge step and no control for one. A disabled merge control would say "not now" where the truth is
 * "not ever, by design" (pr_delivery's no-merge boundary is pinned; its steps end at opening a pull request, pr_delivery/authorization.py STEPS). Nothing replaces it but this statement of what
 * delivery produces (text, never a control), and it says nothing about merging or holding: there is no hold operation and no per-step authorization to name.
 */
export const DI_DELIVERY_END = 'Delivery ends with an open pull request.';

export interface DiDeliveryStepView {
  id: DiDeliveryStepId;
  label: string;
  /** ready: the canonical state matches this step; locked: it does not (see reason); done: already past it. */
  gate: 'ready' | 'locked' | 'done';
  /** Why it is locked, or what was done. Empty while ready. */
  reason: string;
}

const STAGE_ORDER: readonly DiDeliveryStage[] = [
  'working',
  'approved',
  'committed',
  'pushed',
  'pr_open',
  'ci_failed',
  'ci_passed',
  'ci_accepted',
  'merged',
];
/** Rank on the road to delivery; a failed and a passed CI report rank alike. */
const rank = (stage: DiDeliveryStage): number =>
  stage === 'ci_failed' ? STAGE_ORDER.indexOf('ci_passed') : STAGE_ORDER.indexOf(stage);

export const DI_DELIVERY_NOT_READ = 'Delivery state is not read in this layout preview.';

/**
 * Stopping stays outside this office. Said on the control itself, so the screen does not call the
 * connected Code Diff, Comments and Evidence routes a layout preview.
 */
export const DI_STOP_NOT_CONNECTED = 'Stopping a Mission is not connected in this office.';

/**
 * The four Delivery steps (gap 2), each offered only when the canonical state matches (gap 3) and otherwise locked with a plain reason. CHANGED BY THE OWNER RULING (2026-10-28): the former fifth
 * step, "Hold to merge" (AC-4.6-48, AC-4.6-40), is removed; there is no merge step, and DI_DELIVERY_END follows the steps as a statement.
 */
export function deliverySteps(delivery: DiScanDelivery): DiDeliveryStepView[] {
  const steps: Array<{
    id: DiDeliveryStepId;
    label: string;
    readyAt: DiDeliveryStage;
    doneAt: DiDeliveryStage;
    done: string;
    locked: (stage: DiDeliveryStage) => string;
  }> = [
    {
      id: 'commit',
      label: 'Commit',
      readyAt: 'approved',
      doneAt: 'committed',
      done: 'Committed.',
      locked: () => 'Commit waits for the changes to be approved.',
    },
    {
      id: 'push',
      label: 'Push',
      readyAt: 'committed',
      doneAt: 'pushed',
      done: 'Pushed.',
      locked: () => 'Push needs a commit first.',
    },
    {
      id: 'pr',
      label: 'Open pull request',
      readyAt: 'pushed',
      doneAt: 'pr_open',
      done: 'Pull request open.',
      locked: () => 'A pull request needs a pushed branch.',
    },
    {
      id: 'accept_ci',
      label: 'Accept CI results',
      readyAt: 'ci_passed',
      doneAt: 'ci_accepted',
      done: 'CI results accepted.',
      locked: (stage) =>
        stage === 'ci_failed'
          ? 'CI failed. There are no passing results to accept.'
          : stage === 'pr_open'
            ? 'CI results are not in yet.'
            : 'CI results need an open pull request.',
    },
  ];
  return steps.map((step) => {
    const stage = delivery.stage;
    if (delivery.source === 'not_read' || stage === null)
      return { id: step.id, label: step.label, gate: 'locked', reason: DI_DELIVERY_NOT_READ };
    if (rank(stage) >= rank(step.doneAt))
      return { id: step.id, label: step.label, gate: 'done', reason: step.done };
    if (stage === step.readyAt)
      return { id: step.id, label: step.label, gate: 'ready', reason: '' };
    return { id: step.id, label: step.label, gate: 'locked', reason: step.locked(stage) };
  });
}

/** The four Delivery steps, each locked with one stated reason (a real Mission that is not linked). */
export function lockedDeliverySteps(reason: string): DiDeliveryStepView[] {
  const steps: Array<[DiDeliveryStepId, string]> = [
    ['commit', 'Commit'],
    ['push', 'Push'],
    ['pr', 'Open pull request'],
    ['accept_ci', 'Accept CI results'],
  ];
  return steps.map(([id, label]) => ({ id, label, gate: 'locked', reason }));
}

/** M8: a wired step's words (a linked Mission, read through Mission details' session). Each names WHO established it (round 58). */
export const DI_STEP_ELIGIBLE = 'Eligible now, as the record states it. Confirm it below.';
/** Only a row OTHER than the step the answer names, when it names one: then this is the adapter's own statement. */
export const DI_STEP_NOT_ELIGIBLE = 'Not the step eligible now, as the adapter’s answer states it.';
/** The ADAPTER's verdict: its answer says no step is eligible. */
export const DI_STEP_NONE_BY_ADAPTER = 'Not eligible now, as the adapter’s answer states it.';
/** THE ANSWER: this window could not use what the answer says about eligibility; the adapter's verdict is not known here. */
export const DI_STEP_ANSWER_UNUSABLE = 'This window could not use the adapter’s answer about which step is eligible, so it offers no step here.';
/** THIS WINDOW: the answer names this step eligible, but without its local-confirmation note this window offers no confirmation. */
export const DI_STEP_NO_NOTE = 'The adapter’s answer names this step eligible, but carries no local-confirmation note, so this window offers no confirmation for it.';
export const DI_STEP_UNREAD = 'Read this delivery’s status below to see which step is eligible.';
export const DI_ACCEPT_CI_NOT_CONNECTED = 'Accepting CI results is not connected in this office.';

/**
 * M8: the four steps of a LINKED Mission's delivery, from the adapter's answer and nothing else. COMMIT, PUSH and PR_CREATE are wired.
 * Before any read none is offered. After one, each row's reason names who established it (round 58): the step the answer names and
 * this window offers is ready; when the answer names a step, every other row is not that step (the adapter's statement); when no step is
 * offered, the reason is the ADAPTER's (its answer says none is eligible), THE ANSWER's (this window could not use it), or THIS
 * WINDOW's (a named step without its local-confirmation note). Nothing here infers "done" from a recorded state (the answer's facts say
 * what is recorded). Accept CI is NOT wired: it stays unconnected, and says so.
 */
export function linkedDeliverySteps(view: DlView | null): DiDeliveryStepView[] {
  const wired: Array<[DiDeliveryStepId, string, DlStep | null]> = [
    ['commit', 'Commit', 'COMMIT'],
    ['push', 'Push', 'PUSH'],
    ['pr', 'Open pull request', 'PR_CREATE'],
    ['accept_ci', 'Accept CI results', null],
  ];
  return wired.map(([id, label, step]): DiDeliveryStepView => {
    if (step === null) return { id, label, gate: 'locked', reason: DI_ACCEPT_CI_NOT_CONNECTED };
    if (view === null) return { id, label, gate: 'locked', reason: DI_STEP_UNREAD };
    if (view.eligible?.step === step) return { id, label, gate: 'ready', reason: DI_STEP_ELIGIBLE };
    if (view.noStepBy === 'adapter') return { id, label, gate: 'locked', reason: DI_STEP_NONE_BY_ADAPTER };
    if (view.noStepBy === 'answer') return { id, label, gate: 'locked', reason: DI_STEP_ANSWER_UNUSABLE };
    if (view.noStepBy === 'window' && view.namedStep === step) return { id, label, gate: 'locked', reason: DI_STEP_NO_NOTE };
    // the answer names a step, and it is not this one
    return { id, label, gate: 'locked', reason: DI_STEP_NOT_ELIGIBLE };
  });
}

export type DiStateActionId =
  | 'stop'
  | 'decide'
  | 'approve'
  | 'request_changes'
  | 'reject'
  | 'repo_allow'
  | 'repo_decline'
  | 'recover'
  | 'view_log'
  | 'cancel';

export interface DiStateActionView {
  id: DiStateActionId;
  label: string;
  /** A hold-to-confirm control: a single click does nothing (AC-4.9-06, AC-UX-18). */
  hold: boolean;
  /** A NAVIGATION into an existing human-gated route (the request panel's decision), enabled where that route can be opened; nothing is decided here. */
  navigate?: true;
  /** Why this action is drawn disabled on a real row: its own named missing route (shown as text, not only as a title). */
  reason?: string;
}

/** The state actions the Mission's state offers, and only those (gap 3). Recover only restarts work (AC-4.9-03/-08). */
export function stateActions(facts: DiScanStateFacts): DiStateActionView[] {
  // an action is offered only for a fact READ as true; an unread (null) fact offers nothing and claims nothing
  const out: DiStateActionView[] = [];
  if (knownTrue(facts.running)) {
    out.push({
      id: 'stop',
      label: 'Hold to stop',
      hold: true,
      ...(facts.source === 'record' ? { reason: DI_STOP_NOT_CONNECTED } : {}),
    });
  }
  if (facts.awaitingApproval === true)
    out.push(
      ...(facts.source === 'record'
        ? // a real row: the decision is the request panel's, so the office navigates there, and the two acts taken there say so
          [
            { id: 'decide' as const, label: DI_DECIDE_LABEL, hold: false, navigate: true as const },
            { id: 'request_changes' as const, label: 'Request changes', hold: false, reason: DI_REQUEST_CHANGES_IN_PANEL },
            { id: 'reject' as const, label: 'Reject', hold: false, reason: DI_REJECT_IN_PANEL },
          ]
        : // the labelled specimen keeps its layout preview
          [
            { id: 'approve' as const, label: 'Approve', hold: false },
            { id: 'request_changes' as const, label: 'Request changes', hold: false },
            { id: 'reject' as const, label: 'Reject', hold: false },
          ]),
    );
  if (facts.repoRequest === true)
    out.push(
      { id: 'repo_allow', label: 'Allow repository', hold: false },
      { id: 'repo_decline', label: 'Decline repository', hold: false },
    );
  if (facts.stalled === true) out.push({ id: 'recover', label: 'Recover', hold: false });
  if (facts.stalled === true || facts.logRecorded === true)
    out.push({ id: 'view_log', label: 'View log', hold: false });
  if (facts.stalled === true) out.push({ id: 'cancel', label: 'Hold to cancel', hold: true });
  return out;
}

/** The facts this build did not read (null), in plain words, in a fixed order. */
export function unreadStateFacts(facts: DiScanStateFacts): string[] {
  const words: Array<[boolean | null, string]> = [
    [knownOrNull(facts.running), 'whether it is running'],
    [facts.awaitingApproval, 'approvals'],
    [facts.repoRequest, 'repository requests'],
    [facts.stalled, 'stalls'],
    [facts.logRecorded, 'logs'],
  ];
  return words.filter(([value]) => value === null).map(([, word]) => word);
}

export interface DiTrashGate {
  /** The Mission's state allows the delete (a finished or cancelled Mission). */
  open: boolean;
  /** Why it does not; empty when it does. */
  reason: string;
}

/** The trash is offered only for a finished or cancelled Mission, never a running one (gap 5, AC-4.4-29/-30). */
export function trashGate(row: DiScanRow): DiTrashGate {
  if (row.pills.some((pill) => pill.tone === 'building'))
    return { open: false, reason: 'A running Mission cannot be deleted.' };
  if (row.tab !== 'open' || row.pills.some((pill) => pill.tone === 'merged'))
    return { open: true, reason: '' };
  return { open: false, reason: 'Only a finished or cancelled Mission can be deleted.' };
}

/** The rail's Orchestrators: the Missions still in progress (open, not merged), in list order. */
export function railOrchestrators(
  rows: readonly DiScanRow[],
  hidden: ReadonlySet<string>,
): DiScanRow[] {
  return rows.filter(
    (row) =>
      !hidden.has(row.id) &&
      row.tab === 'open' &&
      !row.pills.some((pill) => pill.tone === 'merged'),
  );
}
