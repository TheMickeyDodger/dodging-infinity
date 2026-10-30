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
} from '../../../core/src/messages.js';
import type { InboxItem, InboxModel, PanelEntry } from './diReceptionItems.js';
import { attentionLine, emptyStateLines, PANEL_SHORT_LABEL, plainDelivery } from './diReceptionItems.js';
import { entryGoal, firstLayerWhy, plainServerWording } from './diReceptionPlain.js';
import type { DiScanRow, DiScanTone, DiScanWhy } from './diScanModel.js';
import { knownOrNull, knownTrue, plainSource, receptionDoubt } from './diScanModel.js';

/** Said beside every action drawn at this stage (Stage 2 is layout only; Stage 4 wires the routes). */
export const DI_LAYOUT_NOTE = 'Layout preview. These controls are drawn but not connected yet.';

// ── 04 Missions ──────────────────────────────────────────────────────────────────────────────────

/** The Missions table's STATUS filters (AC-4.4-11). Mine/Team is a separate ownership SCOPE, not a status. */
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
  'dodging-infinity is the harness for every Mission. Finished Missions archive after 30 days.';
export const DI_MISSIONS_EMPTY = 'No Missions yet. Missions you start appear here.';
export const DI_STALLS_NOT_READ = 'Stalls are not read in this layout preview.';

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
  /** `not_read` only when every unknown row is unread; otherwise `not_known`. */
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
    kind: whys.length > 0 && whys.every((w) => w.kind === 'not_read') ? 'not_read' : 'not_known',
    reasons: [...new Set(whys.map((w) => w.text))],
    codes: [...new Set(whys.flatMap((w) => w.codes ?? []))],
  };
}

/** The Missions a status filter shows: only those READ as matching. Picking a filter changes no Mission. */
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
  if (item.kind === 'mission') return missionCard(item.entry, item.panel, rows, label);
  if (item.kind === 'panel') return panelCard(item.entry, label);
  return deliveryCard(item.entry, rows, { label, tone: 'needs' }, 'other');
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
      deliveryCard(entry, rows, { label: 'Information', tone: 'idle' }, 'info'),
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

export const DI_RECENT_NOT_READ = 'Recent asks are not read in this layout preview.';
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

/** How many Orchestrators are running, as read (only a fact read as running counts). */
export function runningCount(rows: readonly DiScanRow[]): number {
  // only a Mission KNOWN to be running counts toward the limit (an object is always truthy: say it explicitly)
  return rows.filter((row) => knownTrue(row.state.running)).length;
}

/**
 * G1/G2: New Mission does not send. It hands the typed ask to the EXISTING request workspace's Ask (that workspace is the one driver of the request state, and the human gate stays there), so the
 * control is named for what it does.
 */
export const DI_ASK_CONTINUE_LABEL = 'Continue in the request workspace';
export const DI_ASK_CONTINUE_HINT = 'to continue in the request workspace';
export const DI_ASK_NO_WORKSPACE =
  'Mission requests are not available in this office, so the ask cannot be carried to the request workspace.';
/** The visible refusal: what is held, that nothing changed, and where to look. The person chooses; the office does not choose for them. */
export const DI_ASK_REFUSED =
  "The request workspace's Ask already holds an unsent draft that is different from this text. Nothing was changed: your text is still here, and that draft is still in the workspace's Ask. Open the existing request workspace to see it, then choose which to keep.";

/**
 * Why "Continue in the request workspace" is off, or null when it is offered. The rule that holds first says it: the limit (AC-4.3-14/-49), an empty ask (AC-4.3-18), or a workspace that is not
 * available in this office.
 */
export function continueAskReason(ask: string, running: number, workspace: boolean): string | null {
  if (running >= DI_ORCHESTRATOR_LIMIT)
    return `${DI_ORCHESTRATOR_LIMIT} of ${DI_ORCHESTRATOR_LIMIT} Orchestrators are running. Finish or stop one to start another.`;
  if (ask.trim() === '') return 'Type an ask first.';
  if (!workspace) return DI_ASK_NO_WORKSPACE;
  return null;
}

export type DiAskHandoff = 'transfer' | 'same' | 'refused';

/**
 * The three cases of handing the typed ask to the workspace's unsent Ask draft (lead's ruling): an EMPTY draft (exactly no text) takes the typed text in full; an EXACTLY equal draft is a no-op
 * success; any other draft, including one of only spaces, refuses the handoff, so no one's text is ever replaced or merged.
 */
export function askHandoff(typed: string, draft: string): DiAskHandoff {
  if (draft === '') return 'transfer';
  if (draft === typed) return 'same';
  return 'refused';
}
