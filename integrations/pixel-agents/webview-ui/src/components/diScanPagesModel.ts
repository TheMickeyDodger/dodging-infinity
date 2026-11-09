/**
 * The rail's pages on the scan face (Stage 2, pass B): the Missions table (04), the full-page Reception (03), New
 * Mission and the proposal (gap 1, Figures 4.3a/4.3b).
 *
 * Presentation and navigation only. Nothing here approves, answers, recovers, accepts, sends or delivers: every such
 * control is drawn and disabled with its reason until Stage 4 wires it to an existing human-gated route. A fact this
 * build does not read is said to be unread (null), never turned into a number or into "none".
 */
import type {
  DiReception,
  DiReceptionCategoryId,
  DiReceptionEntry,
  DiReceptionSourceEntry,
  DiRetainedMission,
  DiRetainedSource,
  DiRetentionFact,
  DiRetentionFactState,
} from '../../../core/src/messages.js';
import type { DiDraftStanding, MrNotice, MrOverview } from './diMissionRequestModel.js';
import { composerProblem, DI_DRAFT_HEAD, DI_DRAFT_HEAD_SUBMITTED, DI_DRAFT_HEAD_UNKNOWN, DI_DRAFT_NOTHING, DI_DRAFT_SOURCE_SUBMITTED, DI_DRAFT_SOURCE_UNKNOWN, draftRunnableWords, draftStanding } from './diMissionRequestModel.js';
import type { DiMissionStatusId } from './diMissionStatus.js';
import {
  DI_MISSION_PROGRESS_STATUS,
  DI_MISSION_STATUS,
  DI_REQUEST_STAGE_STATUS,
  DI_TERMINAL_LITERAL_STATUS,
} from './diMissionStatus.js';
import type { InboxItem, InboxModel, PanelEntry } from './diReceptionItems.js';
import { attentionLine, emptyStateLines, PANEL_SHORT_LABEL, plainDelivery } from './diReceptionItems.js';
import { entryGoal, firstLayerWhy, plainServerWording } from './diReceptionPlain.js';
import type { DiScanPill, DiScanRow, DiScanTone, DiScanWhy } from './diScanModel.js';
import { knownOrNull, plainSource, receptionDoubt, statusPill } from './diScanModel.js';

/** Said beside every action drawn at this stage (Stage 2 is layout only; Stage 4 wires the routes). */
export const DI_LAYOUT_NOTE = 'Layout preview. These controls are drawn but not connected yet.';

// ── 04 Missions ──────────────────────────────────────────────────────────────────────────────────

/**
 * The Missions table's STATUS filters (AC-4.4-11). Mine/Team is a separate ownership SCOPE, not a status. Each filter is DERIVED from the Mission status contract
 * (diMissionStatus.ts) through the row: Running reads the contract's running fact, Done a terminal (closed) record, and Archived the tab, which no source declares today.
 */
export type DiMissionsStatus = 'all' | 'needs' | 'stalled' | 'running' | 'done' | 'archived';

export const DI_MISSIONS_STATUSES: readonly { id: DiMissionsStatus; label: string }[] = [
  { id: 'all', label: 'All' },
  { id: 'needs', label: 'Needs you' },
  { id: 'stalled', label: 'Stalled' },
  { id: 'running', label: 'Running' },
  { id: 'done', label: 'Done' },
  { id: 'archived', label: 'Archived' },
];

/** Each status filter's count. Null: this build does not read the fact the filter needs, so no number is claimed. */
export type DiMissionsCounts = Record<DiMissionsStatus, number | null>;

/** Team is a Paid ownership scope (Phase 4): drawn, unavailable, with this reason. Not an upsell. */
export const DI_TEAM_UNAVAILABLE = 'Team Missions are part of Paid and are not available in Free.';
export const DI_MISSIONS_FOOTER =
  'dodging-infinity is the harness for every Mission. A finished or cancelled Mission moves to Archived at once; its reasoning and logs are reclaimed 30 days later, and its summary is kept.';
export const DI_MISSIONS_EMPTY = 'No Missions yet. Missions you start appear here.';
export const DI_STALLS_NOT_READ = 'Stalls are not reported by this catalog.';

const finished = (row: DiScanRow): boolean =>
  row.tab === 'merged' || row.tab === 'closed' || row.pills.some((pill) => pill.tone === 'merged');

/** Whether `row` matches `status`; null when the fact the filter needs is not read for this row. */
function matches(row: DiScanRow, status: DiMissionsStatus): boolean | null {
  // an archived Mission leaves every filter but Archived (AC-4.4-28)
  if (status === 'archived') return row.tab === 'archived';
  if (row.tab === 'archived') return false;
  switch (status) {
    case 'all':
      return true;
    case 'needs':
      return knownOrNull(row.needsYou);
    case 'running':
      return knownOrNull(row.state.running);
    case 'done':
      return finished(row);
    case 'stalled':
      return row.state.stalled;
  }
}

export function missionsCounts(rows: readonly DiScanRow[]): DiMissionsCounts {
  const counts = {} as DiMissionsCounts;
  for (const { id } of DI_MISSIONS_STATUSES) {
    let n: number | null = 0;
    for (const row of rows) {
      const m = matches(row, id);
      if (m === null) {
        n = null;
        break;
      }
      if (m) n += 1;
    }
    counts[id] = n;
  }
  return counts;
}

/** What a filter cannot decide: how many Missions may also match, and why, in the rows' own words. */
export interface DiMissionsUnknown {
  count: number;
  /** The one kind every unknown row shares (not read, stale, unrecognised, unavailable…); `not_known` when they differ. Kinds are never merged into each other. */
  kind: DiScanWhy['kind'];
  /** The distinct reasons, in row order (plain words). */
  reasons: string[];
  /** The server's reason codes behind them (diagnostics only). */
  codes: string[];
}

/** Why `row` is unknown for `status`: the unknown fact's own reason (the union makes one always present). */
function whyFor(row: DiScanRow, status: DiMissionsStatus): DiScanWhy {
  if (status === 'stalled') return { kind: 'not_read', text: DI_STALLS_NOT_READ };
  const fact = status === 'needs' ? row.needsYou : row.state.running;
  // only rows whose fact is NOT known reach here; a known fact has no reason to give
  return fact.known ? { kind: 'not_known', text: 'its status is not known' } : fact.why;
}

export function missionsUnknown(
  rows: readonly DiScanRow[],
  status: DiMissionsStatus,
): DiMissionsUnknown {
  const unknown = rows.filter((row) => matches(row, status) === null);
  const whys = unknown.map((row) => whyFor(row, status));
  return {
    count: unknown.length,
    kind: whys.length > 0 && whys.every((w) => w.kind === whys[0]!.kind) ? whys[0]!.kind : 'not_known',
    reasons: [...new Set(whys.map((w) => w.text))],
    codes: [...new Set(whys.flatMap((w) => w.codes ?? []))],
  };
}

/** The Missions a status filter shows: only those READ as matching. Picking a filter changes no Mission. */
// ── Deliverable 2: the retained-only archive (Missions gone from their store, as local retention holds them) ────────────────────────────────────────

/** The lifecycle model each retention family's records were listed under, so a retained summary keeps the SAME status vocabulary it had while listed. */
const RETAINED_FAMILY_MODEL: Readonly<Record<string, string>> = {
  mission_core: 'mission_core.mission_state/1',
  workflow_store: 'workflow_authority.workflow_phase/2',
};

/** What each retention phase means for a Mission gone from its store, in a person's words. Each is distinct, and none reads as intact. */
const RETAINED_PHASE: Readonly<Record<string, string>> = {
  observed: 'Gone from its store before its 30-day deadline (the store’s own limit); its summary is kept and its remaining local data is reclaimed at the deadline',
  reclaiming: 'Its reclamation started and is not finished: its summary is kept, and the next retention pass retries the rest',
  reclaimed: 'Reclaimed 30 days after it finished; only its summary is kept',
  deleting: 'Its owner’s deletion has started and is not finished; its summary is already removed, and the next retention pass retries the rest',
  deleted: 'Deleted at its owner’s request; its summary is removed too',
};

/**
 * Round 10: a LISTED Mission's reduced retention phase, said as it is, with whether it is finished: an interrupted reclamation or deletion is never said as done.
 * Exhaustive over the reduced phases (a new phase fails the type check). Used by the Mission's own details (DiScanPanel `LocalRetention`).
 */
export type DiReducedPhase = 'reclaiming' | 'reclaimed' | 'deleting' | 'deleted';
export const LISTED_RETENTION_PHASE: Readonly<Record<DiReducedPhase, { finished: boolean; words: string }>> = {
  reclaiming: {
    finished: false,
    words:
      'Its reclamation started and is not finished: some of its reasoning and logs may still be kept here, and the next retention pass retries the rest. Its summary is kept.',
  },
  reclaimed: { finished: true, words: 'Reclaimed 30 days after it finished: its reasoning and logs are no longer kept here. Its summary is kept.' },
  deleting: {
    finished: false,
    words:
      'Its owner’s deletion started and is not finished: its summary is already removed, and the next retention pass retries the remaining cleanup.',
  },
  deleted: { finished: true, words: 'Deleted by its owner, its summary too. Only a record that it was deleted remains here.' },
};

const FACT_NAMES = { verdict: 'Verdict', ci: 'CI results', comments: 'Comments' } as const;

export interface DiRetainedFact {
  key: keyof typeof FACT_NAMES;
  name: string;
  /** The fact's own state, verbatim (one of the closed `DiRetentionFactState`). */
  state: DiRetentionFactState;
  /** What is shown: the recorded detail, or the honest absence (never blank). */
  words: string;
  /** Where it was recorded, or looked for. */
  source: string;
}

export interface DiRetainedRow {
  ref: string;
  sourceId: string;
  nativeId: string;
  phase: string;
  phaseWords: string;
  /** Its final status in the CLOSED vocabulary it had while listed (null: no summary is kept, or none captured yet). */
  status: { id: DiMissionStatusId; label: string; glyph: string; meaning: string } | null;
  /** Why the status is Unrecognised, when it is (the recorded final state is none of the producer's own shapes); null otherwise. */
  statusReason: string | null;
  /** Its recorded final state and where it came from: provenance, never the status itself. */
  provenance: string | null;
  facts: DiRetainedFact[];
  /** Every control a listed Mission has, drawn disabled with why: a Mission gone from its store is read-only here. */
  actions: { id: string; label: string; reason: string }[];
}

export interface DiArchiveReading {
  /** Retained-only Missions not already listed among the rows (none is counted twice). */
  rows: DiRetainedRow[];
  /**
   * RAW: the retained-only Missions counted from the sources that WERE read (listed or not, awaiting their summary included); null when the archive is not
   * reported at all. It is NOT the archive's size when a source could not be read. Never read it directly: `archiveCount` is its one reader, and
   * diArchiveCountPin.test.ts fails if any other source file names it (round 10: a third state must not be foldable by a reader who misses a convention).
   */
  countedFromReadSources: number | null;
  /** Kept and counted, but beyond what a source lists. */
  unlisted: number;
  /** Removed from their store, owed a summary not yet captured (disjoint from the rest). */
  awaiting: number;
  /** Sources whose archive could not be read, each with why: what they hold is NOT known (never 0). */
  unavailable: { sourceId: string; problem: string }[];
  /** False when this office does not report Missions gone from their store at all. */
  reported: boolean;
}

const RETAINED_GONE_REASON =
  'Its record is no longer in this office’s store, so nothing can start, run, deliver or open it from here; only what local retention kept is shown.';

/**
 * The producer's own final-state shapes (mission_retention/reclaim.py `_mission_core_summary`; the workflow summary records the bare phase), each read by the SAME
 * table a LISTED record of that shape is read by, so its chip is the one it had before it left its store:
 *  - a bare terminal literal: `DI_TERMINAL_LITERAL_STATUS`, by its family's model;
 *  - `<progress> (Mission State progress; record state <state>)`: a record its own progress closed, read as Deliverable 1 reads terminal progress
 *    (`DI_MISSION_PROGRESS_STATUS`): COMPLETED progress is Ended, NEVER Verified (reported completion is not verified success);
 *  - `<state> (withdrawn)`: a proposal closed by withdrawal before anything started, read as Deliverable 1 reads a withdrawn request
 *    (`DI_REQUEST_STAGE_STATUS.withdrawn`), never as an open decision.
 * Anything else is Unrecognised, with why; the recorded text is provenance, never the status.
 */
const PROGRESS_COMPOSITE = /^([A-Z_]+) \(Mission State progress; record state [A-Z_]+\)$/;
const WITHDRAWN_COMPOSITE = /^[A-Z_]+ \(withdrawn\)$/;

function retainedStatus(family: string, finalState: string): { status: NonNullable<DiRetainedRow['status']>; reason: string | null } {
  let id: DiMissionStatusId | null = null;
  if (family === 'mission_core') {
    const progress = PROGRESS_COMPOSITE.exec(finalState)?.[1];
    if (progress !== undefined && Object.prototype.hasOwnProperty.call(DI_MISSION_PROGRESS_STATUS, progress))
      id = DI_MISSION_PROGRESS_STATUS[progress as keyof typeof DI_MISSION_PROGRESS_STATUS];
    else if (WITHDRAWN_COMPOSITE.test(finalState)) id = DI_REQUEST_STAGE_STATUS.withdrawn;
  }
  if (id === null) {
    const model = RETAINED_FAMILY_MODEL[family];
    id = (model !== undefined ? DI_TERMINAL_LITERAL_STATUS[model]?.[finalState] : undefined) ?? null;
  }
  const known = id !== null;
  const entry = DI_MISSION_STATUS[id ?? 'unrecognised'];
  return {
    status: { id: id ?? 'unrecognised', label: entry.label, glyph: entry.glyph, meaning: entry.meaning },
    reason: known ? null : `This office does not know the recorded final state “${finalState}” for a ${family} Mission, so its status is not guessed.`,
  };
}

/**
 * Round 10: what each retained fact's state CLAIMS, in a person's words: exhaustive over the closed state (a new state fails the type check rather than falling back
 * to a confident word). Confirmed absence, not read, unreadable and not known are four different claims; only a recorded fact shows its detail. Shared by every
 * surface that shows a retained summary.
 */
export const RETENTION_FACT_WORDS: Readonly<Record<Exclude<DiRetentionFactState, 'recorded'>, string>> = {
  not_recorded_locally: 'None recorded: it was looked for where it is kept, and none is there',
  not_read: 'Not read: where it would be kept was not consulted here, so nothing is claimed about it',
  unreadable: 'Could not be read: a record may exist, but this office could not read it',
  not_known: 'Not known: kept before this office told an absent record apart from an unreadable one, so its absence was never established',
};

export function retentionFactWords(fact: DiRetentionFact): string {
  if (fact.state === 'recorded') return fact.detail ?? 'Recorded (no detail was kept)';
  return RETENTION_FACT_WORDS[fact.state];
}

function retainedFact(key: DiRetainedFact['key'], fact: DiRetentionFact): DiRetainedFact {
  return { key, name: FACT_NAMES[key], state: fact.state, words: retentionFactWords(fact), source: fact.source };
}

function retainedRow(item: DiRetainedMission): DiRetainedRow {
  const summary = item.summary;
  const read = summary === null ? null : retainedStatus(item.family, summary.finalState);
  const deleteReason =
    item.phase === 'deleted'
      ? 'Its local data and its summary were already deleted at its owner’s request.'
      : item.phase === 'deleting'
        ? 'Its owner’s deletion has started and is not finished: its summary is already removed, and the next retention pass retries the remaining cleanup.'
        : 'Its record is no longer in this office’s store, so it cannot be deleted from here: the owner’s delete acts on a listed Mission’s record, and this one has left its store.';
  return {
    ref: item.missionRef,
    sourceId: item.sourceId,
    nativeId: item.nativeId,
    phase: item.phase,
    phaseWords:
      item.phase === 'observed' && summary === null
        ? 'Gone from its store; its summary is not captured yet, and capture is retried'
        : (RETAINED_PHASE[item.phase] ?? `Its retention state is not one this office knows (${item.phase})`),
    status: read?.status ?? null,
    statusReason: read?.reason ?? null,
    provenance: summary === null ? null : `Recorded final state ${summary.finalState} (${summary.finalStateSource})`,
    facts:
      summary === null
        ? []
        : [retainedFact('verdict', summary.verdict), retainedFact('ci', summary.ci), retainedFact('comments', summary.comments)],
    actions: [
      { id: 'start', label: 'Start', reason: RETAINED_GONE_REASON },
      { id: 'deliver', label: 'Delivery', reason: RETAINED_GONE_REASON },
      { id: 'herdr', label: 'Herdr', reason: RETAINED_GONE_REASON },
      { id: 'delete', label: 'Delete local data', reason: deleteReason },
    ],
  };
}

/**
 * Round 10: what an EMPTY listed store may say, by the archive's own state (never "No Missions yet" over an archive that was not reported or could not be read):
 * `no_missions` only when the archive was read in full and holds none; `archive_only` when it holds finished Missions; `archive_not_reported` and
 * `archive_unavailable` (with each source's reason) say that whether any are kept is not known.
 */
export type DiMissionsEmpty =
  | { kind: 'no_missions' }
  | { kind: 'archive_only' }
  | { kind: 'archive_not_reported' }
  | { kind: 'archive_unavailable'; sources: { sourceId: string; problem: string }[] };

export function missionsEmptyState(archive: DiArchiveReading): DiMissionsEmpty {
  const count = archiveCount(archive);
  if (count.kind === 'not_reported') return { kind: 'archive_not_reported' };
  if (count.kind === 'at_least') return { kind: 'archive_unavailable', sources: count.unavailable };
  return count.count > 0 ? { kind: 'archive_only' } : { kind: 'no_missions' };
}

export function missionsEmptyWords(empty: DiMissionsEmpty): string {
  switch (empty.kind) {
    case 'no_missions':
      return DI_MISSIONS_EMPTY;
    case 'archive_only':
      return 'No Missions are listed. Finished Missions are kept under Archived.';
    case 'archive_not_reported':
      return 'No Missions are listed, and this office does not report Missions gone from their store, so whether any finished Missions are kept is not known.';
    case 'archive_unavailable':
      return `No Missions are listed, and the archive of ${empty.sources.map((s) => `${s.sourceId} (${s.problem})`).join(', ')} could not be read, so whether any finished Missions are kept there is not known.`;
  }
}

/** The archive as a person may read it: retained-only Missions (deduplicated against the listed rows), exact counts, and what could not be read, said. */
export function archiveReading(retained: readonly DiRetainedSource[] | null, rows: readonly DiScanRow[]): DiArchiveReading {
  if (retained === null) return { rows: [], countedFromReadSources: null, unlisted: 0, awaiting: 0, unavailable: [], reported: false };
  const listed = new Set(rows.map((r) => r.id));
  const out: DiRetainedRow[] = [];
  let known = 0;
  let unlisted = 0;
  let awaiting = 0;
  const unavailable: DiArchiveReading['unavailable'] = [];
  for (const source of retained) {
    if (source.state !== 'observed' || source.total === null || source.awaiting === null) {
      unavailable.push({ sourceId: source.sourceId, problem: source.problem ?? 'not_known' });
      continue;
    }
    const fresh = source.missions.filter((m) => !listed.has(m.missionRef));
    out.push(...fresh.map(retainedRow));
    // the source counts EXACTLY; a listed one still among the rows is counted there, never twice
    const alsoListed = source.missions.length - fresh.length;
    known += source.total - alsoListed + source.awaiting;
    unlisted += source.total - source.missions.length;
    awaiting += source.awaiting;
  }
  return { rows: out, countedFromReadSources: known, unlisted, awaiting, unavailable, reported: true };
}

/**
 * The archive's count AS IT MAY BE SAID, the one reader of `countedFromReadSources`: `exact` only when every source was read; `at_least` (a floor, with the
 * sources that could not be read) when any was not; `not_reported` when this office reports no archive at all. A consumer must say which; it cannot add a floor
 * as if it were the total, or read an unreported archive as 0.
 */
export type DiArchiveCount =
  | { kind: 'not_reported' }
  | { kind: 'exact'; count: number }
  | { kind: 'at_least'; count: number; unavailable: DiArchiveReading['unavailable'] };

export function archiveCount(archive: DiArchiveReading): DiArchiveCount {
  const counted = archive.countedFromReadSources;
  if (!archive.reported || counted === null) return { kind: 'not_reported' };
  if (archive.unavailable.length > 0) return { kind: 'at_least', count: counted, unavailable: archive.unavailable };
  return { kind: 'exact', count: counted };
}

export function missionsShown(rows: readonly DiScanRow[], status: DiMissionsStatus): DiScanRow[] {
  return rows.filter((row) => matches(row, status) === true);
}

// ── 03 Reception ─────────────────────────────────────────────────────────────────────────────────

/** What a Reception card is about, which decides the controls drawn for it (AC-4.7-10). */
export type DiReceptionCardKind =
  'proposal' | 'question' | 'stalled' | 'ci' | 'delivery' | 'info' | 'other';

export interface DiReceptionCard {
  key: string;
  kind: DiReceptionCardKind;
  /** The Mission the item belongs to, when it names one. */
  missionRef: string | null;
  /** The Mission's name (or the item's own title when it names no Mission). Display text, never interpreted. */
  title: string;
  tag: { label: string; tone: DiScanTone };
  /** What is needed, in one line. */
  need: string;
  /** Its repos, who or what raised it, and when (each said as not recorded when it is not). */
  meta: string;
  /** What the person can do where this card draws no control for it. Empty when the controls say it. */
  note: string;
  /** Suggested answers to a question. */
  suggestions: readonly string[];
  /** The existing request workspace's own route for this item (view only), when its source names one. */
  request: { conversationId: string | null; requestRef: string | null } | null;
  /** The status of the listed Mission this item is about, the SAME pill its row wears (the one contract). Absent: the item names no listed Mission, or it is a sample. */
  missionStatus?: DiScanPill;
}

/**
 * The full-page Reception's source. On the product it is projected from the SAME inbox model the rail badge reads, so
 * the header and the badge are one number by construction (AC-4.7-16).
 */
export interface DiScanReception {
  source: 'fixture' | 'office';
  /** How many need the person: the header, and the rail badge. */
  attention: number;
  /**
   * Why `attention` is only a FLOOR (pass C, AC-4.7-16 under uncertainty): the server's coverage is incomplete (a
   * category not reported or not read, a stale answer), so more may need the person. Null only when coverage is whole.
   * The rail badge and the header both read THIS, so they express the same state, not only the same digits.
   */
  attentionDoubt: DiScanCountDoubt | null;
  /** Action items: they stay until handled (no clear control, AC-4.7-06). */
  cards: readonly DiReceptionCard[];
  /** Information items: All adds them, and Clear info clears them from this view. */
  information: readonly DiReceptionCard[];
  /** Counted as needing the person but not listed here (the source's display cap). */
  notListed: number;
  /** The "Earlier" list, named by its own heading. Null: not read, and `earlierUnread` says exactly why. */
  earlier: DiScanEarlier | null;
  /** When `earlier` is null on the office's data, the exact cause (so the page claims nothing). Absent: the layout preview's own words. */
  earlierUnread?: string;
  /** The empty note, scoped: it says nothing is LISTED, never that nothing needs the person. */
  empty: readonly string[];
  /** What was checked, one line per source. */
  checked: readonly { label: string; headline: string }[];
}

/**
 * The Reception's "Earlier" list. Its `heading` is the whole claim about what the list holds (a label is a claim about its
 * contents), and `notes` are the limits on that claim, one sentence each. `count` is the number of cards in the window, not a total.
 */
export interface DiScanEarlier {
  heading: string;
  count: number;
  notes: readonly string[];
  items: readonly DiReceptionCard[];
}

/** What the request panel's overview gives the "Earlier" list: the list, or null with the exact cause. */
export interface DiScanEarlierRead {
  earlier: DiScanEarlier | null;
  unread: string;
}

/** Why a count is only a floor: plain words for the screen, the server's reason codes kept as provenance. */
export interface DiScanCountDoubt {
  text: string;
  codes: readonly string[];
}

/**
 * A count's badge: the number when it is whole; "N+" when it is a floor (some known, more may); "?" when none is known but
 * some may (never no badge: an absent badge says "nothing"); null (no badge) only when it is known that nothing is waiting.
 */
export function countBadge(n: number, doubt: DiScanCountDoubt | null): string | null {
  if (doubt === null) return n > 0 ? String(n) : null;
  return n > 0 ? `${n}+` : '?';
}

/** The Reception header's words for the same state the badge shows. */
export function receptionHeadline(n: number, doubt: DiScanCountDoubt | null): string {
  if (doubt === null) return attentionLine(n);
  if (n > 0) return `At least ${n} ${n === 1 ? 'needs' : 'need'} attention; more may`;
  return 'Not known whether anything needs attention';
}

export const DI_EARLIER_NOT_READ =
  'Items handled earlier today are not read in this layout preview.';
export const DI_REPOS_NOT_RECORDED = 'Repos not recorded';

const KIND_OF: Readonly<Partial<Record<DiReceptionCategoryId, DiReceptionCardKind>>> = {
  clarification: 'question',
  authorization: 'proposal',
  delivery_decision: 'delivery',
};

const OPEN_TO_ACT = 'Open the Mission to see what it needs.';

function titleFor(missionRef: string | null, rows: readonly DiScanRow[], fallback: string): string {
  return rows.find((row) => row.id === missionRef)?.title ?? fallback;
}

function missionCard(
  entry: DiReceptionEntry,
  twin: PanelEntry | null,
  rows: readonly DiScanRow[],
  label: string,
): DiReceptionCard {
  const kind = KIND_OF[entry.category] ?? 'other';
  return {
    key: entry.key,
    kind,
    missionRef: entry.missionRef,
    title: titleFor(entry.missionRef, rows, entryGoal(entry)),
    tag: { label, tone: 'needs' },
    need: firstLayerWhy(entry),
    meta: `${DI_REPOS_NOT_RECORDED} · from ${plainSource(entry.sourceId)} · time not recorded${entry.current ? '' : ' · out of date'}`,
    note: kind === 'other' ? OPEN_TO_ACT : '',
    suggestions: [],
    request:
      twin === null ? null : { conversationId: twin.conversationId, requestRef: twin.requestRef },
  };
}

function panelCard(entry: PanelEntry, label: string): DiReceptionCard {
  const kind: DiReceptionCardKind = entry.decision
    ? 'proposal'
    : entry.category === 'clarification'
      ? 'question'
      : 'other';
  return {
    key: entry.key,
    kind,
    missionRef: entry.missionRef,
    title: entry.title,
    tag: { label, tone: 'needs' },
    need: entry.what,
    meta: `${DI_REPOS_NOT_RECORDED} · from the ${PANEL_SHORT_LABEL.toLowerCase()} · time not recorded`,
    note: kind === 'other' ? entry.next : '',
    suggestions: [],
    request: { conversationId: entry.conversationId, requestRef: entry.requestRef },
  };
}

function deliveryCard(
  entry: DiReceptionSourceEntry,
  rows: readonly DiScanRow[],
  tag: DiReceptionCard['tag'],
  kind: DiReceptionCardKind,
): DiReceptionCard {
  const plain = plainDelivery(entry);
  return {
    key: entry.key,
    kind,
    missionRef: entry.missionRef,
    title: titleFor(entry.missionRef, rows, 'Delivery proposal'),
    tag,
    need: kind === 'info' ? plain.what : (plain.why ?? plain.what),
    meta: plain.when,
    note: kind === 'info' ? '' : plain.next,
    suggestions: [],
    request: null,
  };
}

function itemCard(
  item: InboxItem,
  rows: readonly DiScanRow[],
  labelOf: (id: DiReceptionCategoryId) => string,
): DiReceptionCard {
  const label = labelOf(item.category);
  if (item.kind === 'mission') return withMissionStatus(missionCard(item.entry, item.panel, rows, label), rows);
  if (item.kind === 'panel') return withMissionStatus(panelCard(item.entry, label), rows);
  return withMissionStatus(deliveryCard(item.entry, rows, { label, tone: 'needs' }, 'other'), rows);
}

/** A card about a LISTED Mission carries that Mission's status pill, from the same contract its row reads. */
function withMissionStatus(card: DiReceptionCard, rows: readonly DiScanRow[]): DiReceptionCard {
  const status = rows.find((row) => row.id === card.missionRef)?.status;
  return status === undefined ? card : { ...card, missionStatus: statusPill(status) };
}

/**
 * The page's source from the inbox model the rail badge reads (`model.attention` is both numbers). The scan face never
 * hides an action item, so the model it is given is built with nothing hidden.
 */
export function receptionFromInbox(
  model: InboxModel,
  reception: DiReception | undefined,
  rows: readonly DiScanRow[],
  hasUnknowns = false,
  earlier: DiScanEarlierRead | null = null,
): DiScanReception {
  const labelOf = (id: DiReceptionCategoryId) =>
    plainServerWording(reception?.categories.find((c) => c.category === id)?.label ?? id);
  const cards = model.visible.map((item) => itemCard(item, rows, labelOf));
  const doubt = reception
    ? receptionDoubt(reception)
    : { text: ['Reception is not read in this office.'], codes: [] };
  return {
    source: 'office',
    attention: model.attention,
    attentionDoubt:
      doubt.text.length === 0 ? null : { text: doubt.text.join(' '), codes: doubt.codes },
    cards,
    information: model.information.map((entry) =>
      withMissionStatus(deliveryCard(entry, rows, { label: 'Information', tone: 'idle' }, 'info'), rows),
    ),
    notListed: Math.max(0, model.attention - cards.length),
    // Null stays null (never zero) when the request panel was not read; `earlierUnread` then says why.
    earlier: earlier === null ? null : earlier.earlier,
    ...(earlier !== null && earlier.earlier === null ? { earlierUnread: earlier.unread } : {}),
    empty: emptyStateLines(model, reception, hasUnknowns),
    checked: model.sources.map((line) => ({ label: line.label, headline: line.headline })),
  };
}

const sampleMeta = (who: string, when: string): string =>
  `public · dodging-infinity · ${who} · ${when}`;

const sampleCard = (
  card: Pick<DiReceptionCard, 'key' | 'kind' | 'missionRef' | 'title' | 'tag' | 'need' | 'meta'> &
    Partial<DiReceptionCard>,
): DiReceptionCard => ({ note: '', suggestions: [], request: null, ...card });

/**
 * The SAMPLE Reception (03-reception.png and gap 8): one item of each kind, pointing at the v3 sample cast
 * (`DI_SCAN_V3_CAST`) so opening a Mission from it really navigates. Not live records; used only by the specimen.
 */
export const DI_RECEPTION_SPECIMEN: DiScanReception = {
  source: 'fixture',
  attention: 5,
  // the SAMPLE is whole by construction: a drawn specimen, not a read
  attentionDoubt: null,
  cards: [
    sampleCard({
      key: 'r-stalled',
      kind: 'stalled',
      missionRef: 'v3-install',
      title: 'Install guide for new users',
      tag: { label: 'Stalled', tone: 'failed' },
      need: 'The Executor stopped responding. Recover was tried once, 12 minutes ago.',
      meta: sampleMeta('Supervisor', '13m ago'),
    }),
    sampleCard({
      key: 'r-proposal',
      kind: 'proposal',
      missionRef: 'v3-dark',
      title: 'Dark mode toggle',
      tag: { label: 'Proposal ready', tone: 'needs' },
      need: '4 roles across 2 repos. Nothing runs until you approve.',
      meta: sampleMeta('DI', 'now'),
    }),
    sampleCard({
      key: 'r-question',
      kind: 'question',
      missionRef: 'v3-probe',
      title: 'Stabilize readiness probe',
      tag: { label: 'Lead question', tone: 'needs' },
      need: 'Keep the 5s probe timeout, or raise it to 10s?',
      meta: sampleMeta('Lead', '6m ago'),
      suggestions: ['5s', '10s'],
    }),
    sampleCard({
      key: 'r-ci',
      kind: 'ci',
      missionRef: 'v3-inbox',
      title: 'Reception inbox grouping',
      tag: { label: 'CI passed', tone: 'pass' },
      // the merge language removed (owner ruling, 2026-10-28): no "unlock Merge"; delivery has no merge step
      need: '4 of 4 checks passed.',
      meta: sampleMeta('CI', '2m ago'),
    }),
    sampleCard({
      key: 'r-merge',
      kind: 'delivery',
      missionRef: 'v3-settings',
      title: 'Settings screen copy pass',
      // the merge language removed (owner ruling, 2026-10-28): the sample says what delivery produced, never "Ready to merge"
      tag: { label: 'CI accepted', tone: 'pass' },
      need: 'The pull request is open.',
      meta: sampleMeta('Reviewer', '1d ago'),
    }),
  ],
  information: [
    sampleCard({
      key: 'r-info-review',
      kind: 'info',
      missionRef: 'v3-inbox',
      title: 'Reception inbox grouping',
      tag: { label: 'Information', tone: 'idle' },
      need: 'The Reviewer approved round 2.',
      meta: sampleMeta('Reviewer', '20m ago'),
    }),
    sampleCard({
      key: 'r-info-plan',
      kind: 'info',
      missionRef: 'v3-probe',
      title: 'Stabilize readiness probe',
      tag: { label: 'Information', tone: 'idle' },
      need: 'The Lead updated the plan.',
      meta: sampleMeta('Lead', '40m ago'),
    }),
  ],
  notListed: 0,
  earlier: {
    heading: 'Earlier today: 3 handled',
    count: 3,
    notes: [],
    items: [
      sampleCard({
        key: 'r-done-approve',
        kind: 'info',
        missionRef: 'v3-settings',
        title: 'Settings screen copy pass',
        tag: { label: 'Handled', tone: 'idle' },
        need: 'You approved the proposal.',
        meta: sampleMeta('You', '9:12 AM'),
      }),
      sampleCard({
        key: 'r-done-answer',
        kind: 'info',
        missionRef: 'v3-inbox',
        title: 'Reception inbox grouping',
        tag: { label: 'Handled', tone: 'idle' },
        need: 'You answered the Lead.',
        meta: sampleMeta('You', '10:05 AM'),
      }),
      sampleCard({
        key: 'r-done-repo',
        kind: 'info',
        missionRef: 'v3-install',
        title: 'Install guide for new users',
        tag: { label: 'Handled', tone: 'idle' },
        need: 'You allowed a repository.',
        meta: sampleMeta('You', '11:40 AM'),
      }),
    ],
  },
  empty: ['Nothing is listed as needing you right now.'],
  checked: [],
};

// ── New Mission and the proposal ─────────────────────────────────────────────────────────────────

/** At most this many Orchestrators run at once (AC-4.3-14/-26). */
export const DI_ORCHESTRATOR_LIMIT = 15;
/** The harness every Mission runs from: locked, never removable (AC-4.3-06, D107). */
export const DI_HARNESS_FULL = 'TheMickeyDodger/dodging-infinity';

export interface DiProposalRole {
  role: string;
  /** The exact model, as DI produced it (AC-4.3-05). There is no control to change it (AC-4.3-34). */
  model: string;
  provider: string;
  effort: string;
}

export interface DiProposalSpecimen {
  title: string;
  ask: string;
  askedAt: string;
  proposedAt: string;
  summary: string;
  roles: readonly DiProposalRole[];
  harness: string;
  /** The target repos DI chose. */
  targets: readonly string[];
  scopeIn: string;
  scopeOut: string;
}

/** What New Mission is drawn against. */
export interface DiNewMissionSource {
  source: 'fixture' | 'office';
  /** Recent asks, newest first. Null: not read in this build. */
  recent: readonly string[] | null;
  /** A sample proposal to draw (the specimen only). */
  proposal: DiProposalSpecimen | null;
}

/** Said where the request panel's recent conversations are not read (no panel in this office, or not read yet): never an empty list. */
export const DI_RECENT_NOT_READ = 'Recent asks are not read in this office.';
/** Said where the panel's overview WAS read and lists no conversation with words kept. */
export const DI_RECENT_NONE = 'No recent asks are recorded.';
export const DI_NEW_MISSION_OFFICE: DiNewMissionSource = {
  source: 'office',
  recent: null,
  proposal: null,
};

/** Figure 4.3b's proposal, as SAMPLE data: each role's exact model as drawn there. */
export const DI_PROPOSAL_SPECIMEN: DiProposalSpecimen = {
  title: 'Dark mode toggle',
  ask: 'Add a dark mode toggle to Settings. Put the theme tokens in pixel-agents so the world matches, and document it in the DI guide.',
  askedAt: '4:02 PM',
  proposedAt: '4:03 PM',
  summary:
    'Add the toggle to Settings, move the theme tokens into pixel-agents, and add a page to the DI guide.',
  roles: [
    { role: 'Supervisor', model: 'gpt-6-astra', provider: 'Codex', effort: 'xhigh' },
    { role: 'Lead', model: 'claude-opus-5', provider: 'Claude', effort: 'high' },
    { role: 'Executor', model: 'claude-opus-5-5', provider: 'Claude', effort: 'xhigh' },
    { role: 'Reviewer', model: 'gpt-6-astra', provider: 'Codex', effort: 'read-only' },
  ],
  harness: DI_HARNESS_FULL,
  targets: ['pixel-agents'],
  scopeIn: 'Settings toggle, theme tokens, DI guide page',
  scopeOut: 'Delivery, world map',
};

export const DI_NEW_MISSION_SPECIMEN: DiNewMissionSource = {
  source: 'fixture',
  recent: ['Fix flaky readiness test', 'Update install guide', 'Copy pass on Settings'],
  proposal: DI_PROPOSAL_SPECIMEN,
};

/**
 * The Orchestrator capacity GUARD's inputs (AC-4.3-14/-49), kept SEPARATE from the display count on purpose: the Running filter counts only a team observed working, while
 * the guard must keep its protective strength. `holding`: Missions that hold an Orchestrator (`DI_STATUS_CAPACITY`: running, blocked, a bound task observed without a
 * confirmed team, a requested start). `notKnown`: Missions for which it is not known whether they do (stale, disconnected, unknown, unrecognised, unavailable): an unknown is
 * NEVER spare capacity, so the guard counts it as held. Each row is read from its status's own `capacity` (diMissionStatus.ts), the EXECUTION reading, never from its
 * filing tab. A row without a contract status (the labelled specimen) is read from its tab and running fact.
 */
export interface DiOrchestratorUse {
  holding: number;
  notKnown: number;
}

export function orchestratorUse(rows: readonly DiScanRow[]): DiOrchestratorUse {
  const use: DiOrchestratorUse = { holding: 0, notKnown: 0 };
  for (const row of rows) {
    // read from the status's own EXECUTION capacity, never from how the record is filed: a record its progress closed can still hold an Orchestrator (round 3, finding 1)
    const bears =
      row.status !== undefined
        ? row.status.capacity.bears
        : row.tab !== 'open'
          ? 'none'
          : !row.state.running.known
          ? 'not_known'
          : row.state.running.value
            ? 'holds'
            : 'none';
    if (bears === 'holds') use.holding += 1;
    else if (bears === 'not_known') use.notKnown += 1;
  }
  return use;
}

/** Whether the guard refuses another Orchestrator: every held slot AND every slot not known to be free counts. */
export function atOrchestratorLimit(use: DiOrchestratorUse): boolean {
  return use.holding + use.notKnown >= DI_ORCHESTRATOR_LIMIT;
}

/** The guard's own words: in use, or in use or cannot be checked (with both counts), never "running" (a held slot is not a team observed working). */
export function orchestratorLimitWords(use: DiOrchestratorUse): string {
  return use.notKnown === 0
    ? `${DI_ORCHESTRATOR_LIMIT} of ${DI_ORCHESTRATOR_LIMIT} Orchestrators are in use`
    : `${DI_ORCHESTRATOR_LIMIT} of ${DI_ORCHESTRATOR_LIMIT} Orchestrators are in use or cannot be checked (${use.holding} in use, ${use.notKnown} cannot be checked)`;
}

/**
 * New Mission is ONE screen with ONE composer: its Ask box IS the request workspace's Ask draft (the workspace's own composer state), and Send sends it as ONE
 * turn of the workspace's restricted, read-only conversation (`turn`). The assistant may ask questions or draft a proposal, and the draft is shown in place.
 * Nothing on this screen submits, approves or starts anything: submitting the draft, approving it and pressing Start stay the request workspace's own gates.
 */
export const DI_NEW_MISSION_SUB =
  'Describe the work once and press Send. DI drafts a proposal here; nothing runs until you submit it, approve it and press Start.';
export const DI_ASK_SEND_LABEL = 'Send';
/** After the Cmd Return key hint. */
export const DI_ASK_SEND_HINT = 'to send. The assistant can ask questions and draft a proposal; it cannot approve or start anything.';
export const DI_ASK_NO_PANEL = 'Mission requests are not available in this office, so nothing can be sent from here.';
export const DI_ASK_UNVOUCHED = 'Not connected to the office, so nothing can be sent now.';
/** F1 (review round 1): the request panel is configured but not available NOW; the box is still the one Ask draft (kept as typed), and Send is held. */
export const DI_ASK_UNAVAILABLE = 'Mission requests are not available now, so nothing can be sent from here.';
export const DI_ASK_BUSY = 'Waiting for the assistant to answer your last message.';
/** B8: the request workspace is showing its own Ask composer, which holds the SAME draft: New Mission draws no second box, and says where the one box is. */
export const DI_ASK_IN_WORKSPACE = 'Your ask is open in the request workspace. Send it there, or bring it back here.';
export const DI_ASK_IN_WORKSPACE_NOTE =
  'Your ask is open in the request workspace. It is the same draft, so it is typed in one place at a time.';
export const DI_ASK_BRING_BACK = 'Bring the ask back here';

/**
 * Review round 2, blocker 1: a conversation picked in Recent whose READ was refused or not answered. Nothing of it is shown, so nothing is sent into it.
 * It names both ways on the page word for word: DI_NEW_READ_AGAIN and DI_ASK_LEAVE_UNREAD (B2e: picking the selected ask in Recent now CLOSES it, so the
 * words no longer send a person there to try again).
 */
export const DI_ASK_UNREAD =
  'This conversation could not be read, so nothing is sent into it. Read this conversation again, or start a new conversation, keeping your ask.';
/**
 * R3-REC (review round 3): the way out of a conversation that could not be read, on New Mission in exactly that state (no thread, so not the thread's own
 * "Start a new conversation", is drawn then). Unlike that control, it KEEPS the typed ask. DI_ASK_UNREAD names it, word for word.
 */
export const DI_ASK_LEAVE_UNREAD = 'Start a new conversation, keeping your ask';
/**
 * D1: the way out of ANY conversation New Mission shows (picked in Recent, in every state of its read, or begun here): back to the blank start. Like the
 * unread way out it KEEPS the typed ask (a pick never touched the box, so leaving it does not either); the first Escape does the same.
 */
export const DI_NEW_CLOSE_CONVERSATION = 'Close this conversation';
export const DI_NEW_CLOSE_CONVERSATION_NOTE = 'Back to a blank New Mission. Your ask stays in the box; nothing is sent or deleted. Esc does the same.';
/** B2e: the labelled way to read the conversation shown AGAIN, drawn while its read failed (the selected Recent ask closes it instead). */
export const DI_NEW_READ_AGAIN = 'Read this conversation again';
/** A conversation picked in Recent while its read is still in flight: nothing of it is shown yet. */
export const DI_ASK_READING = 'Reading this conversation. Send waits until it is shown.';
/** Where the request workspace stands for New Mission: none in this office, not vouched for, not available now, the conversation picked could not be read, busy with the last message, or ready. */
export type DiAskPanel = 'none' | 'unvouched' | 'unavailable' | 'unread' | 'reading' | 'elsewhere' | 'busy' | 'ready';

/**
 * Why Send is off, or null when it is offered. The rule that holds first says it: the Orchestrator limit (AC-4.3-14/-49; an unknown is never spare capacity),
 * an empty ask (AC-4.3-18), no request panel in this office, a client that cannot vouch for the office, the ask open in the request workspace (its own composer
 * holds it there: B8), a message still being answered, then the composer's
 * own bound (the request workspace's rule, `composerProblem`).
 */
export function sendAskReason(ask: string, use: DiOrchestratorUse, panel: DiAskPanel): string | null {
  if (atOrchestratorLimit(use)) return `${orchestratorLimitWords(use)}. Finish or stop one to start another.`;
  if (ask.trim() === '') return 'Type an ask first.';
  if (panel === 'none') return DI_ASK_NO_PANEL;
  if (panel === 'unvouched') return DI_ASK_UNVOUCHED;
  if (panel === 'unavailable') return DI_ASK_UNAVAILABLE;
  if (panel === 'unread') return DI_ASK_UNREAD;
  if (panel === 'reading') return DI_ASK_READING;
  if (panel === 'elsewhere') return DI_ASK_IN_WORKSPACE;
  if (panel === 'busy') return DI_ASK_BUSY;
  return composerProblem(ask);
}

/** One recent ask: its words, and the conversation it began (null for the labelled sample, whose asks only fill the box). */
export interface DiRecentAsk {
  conversationId: string | null;
  text: string;
}
export type DiRecentAsks = { kind: 'not_read'; words: string } | { kind: 'read'; items: readonly DiRecentAsk[] };

/**
 * Recent asks, ONLY as they were actually read: the labelled sample's own list, or the request panel's overview (its recent conversations, each with the
 * first words of its first ask, as the panel keeps them). No overview read: said as not read, never as none. A conversation whose words are no longer
 * kept (reclaimed or deleted) has no words to show and is not listed as an ask.
 */
export function recentAsks(source: DiNewMissionSource, overview: MrOverview | null): DiRecentAsks {
  if (source.recent !== null) return { kind: 'read', items: source.recent.map((text) => ({ conversationId: null, text })) };
  if (overview === null) return { kind: 'not_read', words: DI_RECENT_NOT_READ };
  return {
    kind: 'read',
    items: overview.conversations
      .filter((c) => c.firstWords.trim() !== '')
      .map((c) => ({ conversationId: c.conversationId, text: c.firstWords })),
  };
}

// The draft's standing and its shared words live in the request model (review round 2, item 2: the workspace's own draft card is written from the SAME
// reading); re-exported here for New Mission's card and its tests.
export { DI_DRAFT_HEAD, DI_DRAFT_HEAD_SUBMITTED, DI_DRAFT_HEAD_UNKNOWN, DI_DRAFT_NOTHING, DI_DRAFT_SOURCE_SUBMITTED, DI_DRAFT_SOURCE_UNKNOWN, draftRunnableWords, draftStanding };
export type { DiDraftStanding };
/** New Mission's card: drafting's claim and where the gates are (words only: none of them approves, starts or submits anything). */
export const DI_DRAFT_IN_PLACE =
  'Drafting approved nothing and started nothing. Submitting it, approving it and pressing Start are separate steps in the request workspace.';
export const DI_DRAFT_REPLACED = 'A later draft replaced this one in the conversation.';
export const DI_DRAFT_OPEN_LABEL = 'Open it in the request workspace to submit';

/** The card's head and note for a standing. `latest`: a later draft did not replace this one. */
export function draftNote(standing: DiDraftStanding, latest: boolean): { head: string; body: string } {
  const head = standing === 'submitted' ? DI_DRAFT_HEAD_SUBMITTED : standing === 'not_known' ? DI_DRAFT_HEAD_UNKNOWN : DI_DRAFT_HEAD;
  if (!latest) return { head, body: DI_DRAFT_REPLACED };
  return { head, body: standing === 'draft' || standing === 'refused' ? DI_DRAFT_IN_PLACE : DI_DRAFT_NOTHING };
}



/**
 * What one of New Mission's own requests came to, on New Mission. Send (`turn`): in the request workspace's own notice words (DiMissionRequest: its notices
 * list). A conversation READ (a Recent pick, or the watch while the page shows it; review round 2, blocker 1): refused or not answered, it was NOT READ.
 */
export function sendOutcomeWords(kind: string, op = 'turn'): string {
  if (op === 'conversation') return kind === 'info' ? '' : 'Not read.';
  return kind === 'unknown' ? 'Outcome not known.' : kind === 'refusal' ? 'Refused.' : kind === 'unavailable' ? 'Not sent.' : '';
}
/**
 * After a conversation read that failed, what the page shows of it now: the thread as it was last READ; the thread as this window last had it from a message
 * (never read since); or nothing, when the pick had cleared it and no read landed.
 */
export const DI_READ_LAST_READ = 'What is shown is the conversation as last read.';
export const DI_READ_LAST_HAD = 'What is shown is the conversation as this window last had it, not read again.';
export const DI_READ_NOTHING_SHOWN = 'Nothing of it is shown here, and Send will not send into it.';
/** A conversation READ successfully that holds no messages: said as that, distinct from not read. */
export const DI_READ_EMPTY = 'This conversation was read: it holds no messages.';

/** The ops New Mission itself asks: the drafting turn (Send) and the conversation read. Only their notices are said on New Mission. */
const NEW_MISSION_OPS: readonly string[] = ['turn', 'conversation'];
export interface DiPageOutcome {
  /** The newest of a group of identical notices (the watch repeats a refused read on every poll: said once). */
  notice: MrNotice;
  /** Every notice in the group, so Dismiss dismisses them all. */
  ids: number[];
}
/**
 * New Mission's outcomes, newest first: its own ops' notices, a READ's only where `isCurrentRead` attributes it to the conversation shown (by the request it
 * answers, never by when it arrived: review round 2); identical notices grouped.
 */
export function pageOutcomes(notices: readonly MrNotice[], isCurrentRead: (notice: MrNotice) => boolean): DiPageOutcome[] {
  const groups = new Map<string, DiPageOutcome>();
  for (const notice of notices) {
    if (!NEW_MISSION_OPS.includes(notice.op)) continue;
    if (notice.op === 'conversation' && !isCurrentRead(notice)) continue;
    const key = `${notice.op}|${notice.kind}|${notice.text}`;
    const group = groups.get(key);
    if (group === undefined) groups.set(key, { notice, ids: [notice.id] });
    else {
      group.ids.push(notice.id);
      if (notice.id > group.notice.id) group.notice = notice;
    }
  }
  return [...groups.values()].sort((a, b) => b.notice.id - a.notice.id);
}
