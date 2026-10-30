/**
 * The reception inbox's ITEMS: one list built from three sources that stay distinguishable —
 *
 * - `mission`: the office server's Mission-keyed entries (`DiReception.entries`);
 * - `delivery`: delivery proposals presented through the Grok Bot route, keyed on their source and
 *   proposal digest (`DiReception.sourceEntries`), linked to a Mission only when the catalog evidences
 *   one;
 * - `panel`: what the Mission request panel reports needs the person — a conversation awaiting their
 *   answer, a proposal awaiting their decision or an approval to check, a result ready to verify —
 *   read from the panel overview this window already holds (`MrState`), never fetched here.
 *
 * Pure presentation: it reads, derives no outcome and adds no action. Nothing here approves, starts,
 * relays or delivers; every "next step" is a view-only route or truthful text. A source that was not
 * read, or does not report a kind, is said so in its own coverage line — never as "none".
 *
 * A text the assistant or a request carries (a question, an objective) is UNTRUSTED display content:
 * shown as quoted words, never interpreted.
 */
import type {
  DiMission,
  DiReception,
  DiReceptionCategoryId,
  DiReceptionEntry,
  DiReceptionOutcome,
  DiReceptionSourceCoverage,
  DiReceptionSourceEntry,
} from '../../../core/src/messages.js';
import type { MrOverview, MrState, MrSummary } from './diMissionRequestModel.js';
import { requestNeeds, waitingOf } from './diMissionRequestModel.js';
import { formatObserved } from './diOfficeFormat.js';
import { hideKey as missionHideKey, utcStamp } from './diReceptionFormat.js';
import { routeAction } from './diReceptionPlain.js';

export const PANEL_SOURCE_ID = 'request_panel';
export const PANEL_SOURCE_LABEL = 'Mission request panel';
export const DELIVERY_SOURCE_LABEL = 'Delivery proposals (Grok Bot route)';
export const PANEL_SHORT_LABEL = 'Request panel';
export const DELIVERY_SHORT_LABEL = 'Delivery proposals';

// ── the panel as a source ────────────────────────────────────────────────────────────────────────

/** What this window knows about the request panel. */
export type PanelSource =
  | { kind: 'not_provided' }
  | { kind: 'not_available'; reason: string }
  | { kind: 'not_vouched'; reason: string }
  | { kind: 'not_read' }
  | { kind: 'read'; overview: MrOverview };

export function panelSourceOf(state: MrState | null): PanelSource {
  if (state === null) return { kind: 'not_provided' };
  if (state.unvouched !== null) return { kind: 'not_vouched', reason: state.unvouched };
  if (state.availability.kind === 'unavailable')
    return { kind: 'not_available', reason: state.availability.reason };
  if (state.overview === null) return { kind: 'not_read' };
  return { kind: 'read', overview: state.overview };
}

export interface PanelEntry {
  key: string;
  signature: string;
  category: DiReceptionCategoryId;
  /** The panel's own id for the thing: `cv-…` (a conversation) or `lr-…` (a request). */
  identity: string;
  conversationId: string | null;
  requestRef: string | null;
  /** UNTRUSTED display text: the person's first words, or the request's objective. */
  title: string;
  what: string;
  why: string;
  next: string;
  /** The authority / provenance explanation, disclosed behind the card rather than led with. */
  about: string;
  /** UNTRUSTED display text the assistant declared (a conversation awaiting an answer). */
  questions: string[];
  /** A Mission, only when the request's Mission Core id names exactly one Mission in this office. */
  missionRef: string | null;
  linkNote: string;
  /** True for a request whose pending DECISION is offered (not an approval check, not a result). */
  decision: boolean;
  /** The request's Mission Core id and revision, and the panel's own stage and digest (source facts). */
  missionId: string | null;
  revision: number | null;
  stage: string | null;
  digest: string | null;
  /**
   * Set when Mission Core ALSO lists a pending decision on this Mission that cannot be shown to be this
   * same decision: said plainly, and the two stay separate items.
   */
  unresolved: string | null;
}

export type InboxItem =
  | {
      kind: 'mission';
      key: string;
      hideKey: string;
      category: DiReceptionCategoryId;
      entry: DiReceptionEntry;
      /**
       * The request panel's request for the SAME decision, when the evidence proves it (see
       * {@link reconcileDecisions}). One item, both sources' facts, counted once.
       */
      panel: PanelEntry | null;
    }
  | {
      kind: 'delivery';
      key: string;
      hideKey: string;
      category: DiReceptionCategoryId;
      entry: DiReceptionSourceEntry;
    }
  | {
      kind: 'panel';
      key: string;
      hideKey: string;
      category: DiReceptionCategoryId;
      entry: PanelEntry;
    };

const nul = '\u0000';

/** The one Mission whose Mission Core id is `missionId`, or null (none, or more than one). */
export function missionForRequest(
  missionId: string,
  missions: readonly DiMission[],
): string | null {
  const found = missions.filter((m) => m.sourceKind === 'mission_core' && m.nativeId === missionId);
  return found.length === 1 ? found[0].missionRef : null;
}

function requestEntries(overview: MrOverview, missions: readonly DiMission[]): PanelEntry[] {
  const out: PanelEntry[] = [];
  const make = (
    r: MrSummary,
    category: DiReceptionCategoryId,
    what: string,
    why: string,
    next: string,
    about: string,
  ): PanelEntry => {
    const missionRef = missionForRequest(r.missionId, missions);
    return {
      key: JSON.stringify([PANEL_SOURCE_ID, category, r.requestRef]),
      signature: JSON.stringify([r.stage, r.revision, r.digest, r.actions]),
      category,
      identity: r.requestRef,
      conversationId: null,
      requestRef: r.requestRef,
      title: r.objective,
      what,
      why,
      next,
      about,
      questions: [],
      missionRef,
      linkNote:
        missionRef === null
          ? 'Not tied to a Mission in this list (its Mission record is not listed here, or is not unique).'
          : 'Tied to a Mission in this list by its Mission Core record.',
      decision: category === 'authorization' && r.actions.includes('decide'),
      missionId: r.missionId,
      revision: r.revision,
      stage: r.stage,
      digest: r.digest,
      unresolved: null,
    };
  };
  for (const r of overview.requests) {
    // one entry per thing the request asks of the person: the SAME items `waitingOf` counts
    for (const need of requestNeeds(r.actions)) {
      if (need === 'decide')
        out.push(
          make(
            r,
            'authorization',
            `A proposal is waiting for your decision (${r.label}).`,
            'The request panel lists it as waiting for you to decide.',
            'Open the request panel, read the exact proposal, then decide there.',
            'A decision is made only in the request panel, by typing a word it records as an operator attestation (not independently verified). Opening the panel from here sends nothing.',
          ),
        );
      else if (need === 'check_approval')
        out.push(
          make(
            r,
            'authorization',
            `An approval was recorded for this request (${r.label}).`,
            'The request panel offers you its approval check for this request.',
            'Open the request panel to check the approval.',
            'Checking an approval reads the panel’s record of it; it does not approve anything. Opening the panel from here sends nothing.',
          ),
        );
      else
        out.push(
          make(
            r,
            'ready_for_review',
            `The team’s result is ready for you to verify (${r.label}).`,
            'The request panel offers you its verify step for this request.',
            'Open the request panel to read the result and verify it.',
            'A team reporting its result is a report, not a verification: verifying is a separate act you request in the panel. Opening the panel from here sends nothing.',
          ),
        );
    }
  }
  return out;
}

function conversationEntries(overview: MrOverview): PanelEntry[] {
  return overview.conversations
    .filter((c) => c.awaitingAnswer)
    .map((c) => ({
      key: JSON.stringify([PANEL_SOURCE_ID, 'clarification', c.conversationId]),
      signature: JSON.stringify([c.awaitingTurnId, c.awaitingQuestions]),
      category: 'clarification' as const,
      identity: c.conversationId,
      conversationId: c.conversationId,
      requestRef: null,
      title: c.firstWords,
      what: `The assistant asked ${c.awaitingQuestions.length === 1 ? 'a question' : `${c.awaitingQuestions.length} questions`} in this conversation and is waiting for your reply.`,
      why: 'The newest reply in this conversation asked for your answer, and nothing has followed it.',
      next: 'Open the request panel (Ask) to read the conversation and reply.',
      about:
        'The questions are the assistant’s own words, shown as quoted text: untrusted content that decides and authorizes nothing, and not a gate the request panel enforces. “Waiting” means the panel read the newest reply for questions and found no later turn.',
      questions: c.awaitingQuestions,
      // A conversation is tied to a Mission only by a submitted request; the overview carries no such
      // reference, so it is never guessed.
      missionRef: null,
      linkNote: 'Not tied to a Mission: a conversation has none until a request is submitted.',
      decision: false,
      missionId: null,
      revision: null,
      stage: null,
      digest: null,
      unresolved: null,
    }));
}

// ── delivery wording ─────────────────────────────────────────────────────────────────────────────

const MODE_WORDS: Readonly<Record<string, string>> = {
  pull_request: 'a pull request',
  pr_update: 'an update to an existing pull request',
};

export interface PlainDelivery {
  /** The supported fact: a proposal was presented, and what is (not) recorded. */
  what: string;
  /** “presented [time]; …” — the time facts, as stated. */
  when: string;
  /** Why it is on this list (null: it is information only). */
  why: string | null;
  /** What the person can do, in plain words. */
  next: string;
  link: string;
  /** What this reader can and cannot see, disclosed behind the card. */
  about: string;
}

const LINK_WORDS: Readonly<Record<string, (workflowId: string | null) => string>> = {
  linked: () => 'Tied to the Mission shown on this card.',
  unlinked_no_mission_binding: () => 'Not tied to a Mission: the proposal names none.',
  unlinked_no_registered_mission: (w) =>
    `Not tied to a Mission: ${w === null ? 'it names no workflow' : 'the workflow it names has no registered Mission'} (the exact workflow is in the details).`,
  unlinked_ambiguous: (w) =>
    `Not tied to a Mission: ${w === null ? 'it names no workflow' : 'the workflow it names belongs to more than one registered Mission'} (the exact workflow is in the details).`,
  unlinked_registry_unavailable: () =>
    'Could not be matched to a Mission: the list of Missions could not be read.',
  unlinked_sources_incomplete: () =>
    'Could not be fully matched to a Mission: some Mission sources could not be read.',
};

const DELIVERY_ABOUT =
  'This view only reads what the Grok Bot recorded when it presented the proposal and what the delivery store holds. ' +
  'It shows that a proposal was presented and whether a matching authorization is recorded. It cannot see a ' +
  'decision or decline made elsewhere, or a proposal presented directly from the command line, and it cannot ' +
  'approve or deliver anything. The approval window is when the proposal expires; it is not a promise that an ' +
  'approval can still be made.';

const WINDOW_PASSED_REASON = 'approval_window_ended_since_catalog_read';

/** Why a cached delivery fact is out of date, in plain words (the codes stay behind the card). */
export function plainStaleWords(reasons: readonly string[]): string {
  const words = new Set<string>();
  for (const r of reasons) {
    if (r === WINDOW_PASSED_REASON) continue;
    if (r.startsWith('envelope_not_fresh')) words.add('the catalog answer is out of date');
    else if (r.startsWith('client:')) words.add('this window can no longer vouch for the office');
    else words.add('its data is out of date');
  }
  return [...words].join('; ');
}

/**
 * A delivery proposal in plain words. It never says an approval is usable and never infers a decline.
 * Its wording respects whether the cached record is current: when it is out of date it says what was
 * recorded WHEN LAST READ and that its current status needs checking. A window that has passed since
 * the read is said to have passed — it is never turned into a new outcome from the clock alone.
 */
export function plainDelivery(e: DiReceptionSourceEntry): PlainDelivery {
  const mode = MODE_WORDS[e.mode] ?? e.mode;
  const repo = e.repositoryUrl === null ? '' : ` for ${e.repositoryUrl}`;
  const link = (LINK_WORDS[e.linkState] ?? (() => `Link: ${e.linkState}.`))(e.workflowId);
  const presented = `presented ${utcStamp(e.presentedAt)}`;
  const windowEnd = utcStamp(e.expiresAt);
  const next =
    'If you want it, decide in the Grok Bot conversation where it was presented. Nothing here can approve or deliver it.';
  const windowPassed = e.staleReasons.includes(WINDOW_PASSED_REASON);
  const otherStale = plainStaleWords(e.staleReasons);
  const outOfDate = !e.current || e.staleReasons.length > 0;
  const base = { next, link, about: DELIVERY_ABOUT };
  switch (e.state) {
    case 'awaiting_no_recorded_authorization':
      if (!outOfDate)
        return {
          ...base,
          what: `A delivery proposal was presented (${mode}${repo}) and no matching authorization is recorded.`,
          when: `${presented}; no recorded authorization; approval window until ${windowEnd}`,
          why: `No authorization is recorded for it yet; its approval window closes ${windowEnd}.`,
        };
      return {
        ...base,
        what: `A delivery proposal was presented (${mode}${repo}). When last read, no matching authorization was recorded.`,
        when: windowPassed
          ? `${presented}; when last read, no recorded authorization; its approval window has now passed (${windowEnd}); its current status needs checking`
          : `${presented}; when last read, no recorded authorization; approval window until ${windowEnd} (as read)`,
        why: windowPassed
          ? 'Its approval window has passed since this was read, so its current status needs checking.'
          : `This is out of date${otherStale === '' ? '' : ` (${otherStale})`}, so its current status needs checking.`,
      };
    case 'authorized':
      return {
        ...base,
        what: `A delivery proposal (${mode}${repo}) has a matching authorization on record${outOfDate ? ' as last read' : ''}.`,
        when: `${presented}; a matching authorization is recorded${outOfDate ? ' (as last read; out of date)' : ''}`,
        why: null,
      };
    case 'expired_without_authorization':
      return {
        ...base,
        what: `A delivery proposal (${mode}${repo}) has no matching authorization recorded and its approval window has closed${outOfDate ? ', as last read' : ''}.`,
        when: `${presented}; no recorded authorization; its approval window ended ${windowEnd}${outOfDate ? ' (as last read; out of date)' : ''}`,
        why: null,
      };
    default:
      return {
        ...base,
        what: `A delivery proposal was presented (${mode}${repo}); whether a matching authorization is recorded was not determined.`,
        when: `${presented}; approval window until ${windowEnd}; the delivery store was not checked`,
        why: null,
      };
  }
}

// ── the model ────────────────────────────────────────────────────────────────────────────────────

/** One per-source coverage line: what was checked, said in the source's own terms. */
export interface SourceLine {
  sourceId: string;
  label: string;
  /** The label in the one-line summary above the cards ("Delivery proposals"). */
  shortLabel: string;
  /** What that line says about this source: "3 waiting", or exactly why it was not read — never "none" for a source that was not read. */
  headline: string;
  outcome: DiReceptionOutcome;
  /** One short plain phrase. */
  summary: string;
  /** What this source does and does not cover. */
  statement: string;
}

export interface InboxModel {
  /** Everything that needs the person, hidden or not. */
  all: InboxItem[];
  /** What the LIST draws: `all` less what is hidden in this view. A view preference; it never changes `attention`. */
  visible: InboxItem[];
  /** How many of `all` are hidden in this view only (still counted in `attention`). */
  hiddenCount: number;
  /** Delivery proposals that do not wait on the person (information). */
  information: DiReceptionSourceEntry[];
  /**
   * The number the sidebar and the dialog's headline both say: EVERY item that needs the person (plus any the display cap left out), hidden in this view or not. "Hide in this view" resolves, approves and dismisses
   * nothing, so it cannot change how many things need attention; counting only `visible` made the number say "Nothing listed" while an entry still needed the person (and made the operational face disagree with the
   * scan face, which hides nothing, about the same office state: AC-4.7-16).
   */
  attention: number;
  sources: SourceLine[];
}

const CATEGORY_RANK: Readonly<Record<string, number>> = {
  blockers: 0,
  clarification: 1,
  authorization: 2,
  failed_verification: 3,
  ready_for_review: 4,
  delivery_decision: 5,
};
const KIND_RANK = { mission: 0, panel: 1, delivery: 2 } as const;

function deliveryLine(reception: DiReception): SourceLine {
  const found = (reception.sourceCoverage ?? []).find(
    (c: DiReceptionSourceCoverage) => c.sourceId === 'grok_delivery_presentations',
  );
  if (found === undefined)
    return {
      sourceId: 'grok_delivery_presentations',
      label: DELIVERY_SOURCE_LABEL,
      shortLabel: DELIVERY_SHORT_LABEL,
      headline: 'not reported by this office',
      outcome: 'unsupported',
      summary: 'not reported by this office',
      statement:
        'This office server does not report delivery proposals. That is unknown here, not “none”.',
    };
  const entries = reception.sourceEntries ?? [];
  const undetermined = entries.filter((e) => e.state === 'unknown').length;
  const summary =
    found.outcome === 'present'
      ? `${found.total ?? 0} presented · ${entries.filter((e) => e.needsYou).length} waiting for you` +
        (undetermined > 0 ? ` · ${undetermined} not determined` : '')
      : found.outcome === 'known_empty'
        ? 'none recorded by the Grok Bot route'
        : found.outcome === 'unsupported'
          ? 'not reported here'
          : 'can’t be read just now';
  const headline =
    found.outcome === 'present'
      ? `${entries.filter((e) => e.needsYou).length} waiting`
      : found.outcome === 'known_empty'
        ? 'none recorded'
        : summary;
  return {
    sourceId: found.sourceId,
    label: DELIVERY_SOURCE_LABEL,
    shortLabel: DELIVERY_SHORT_LABEL,
    headline,
    outcome: found.outcome,
    summary,
    statement: found.statement,
  };
}

const PANEL_COVERAGE =
  'Covers the request panel’s overview only: the 10 most recent conversations and the 20 newest ' +
  'requests it lists. A conversation is “waiting for you” only when the panel read its newest reply ' +
  'for questions and nothing came after it; replies recorded before the panel read for questions ' +
  'are not reported, which is different from “no questions”.';

function panelLine(
  source: PanelSource,
  overview: MrOverview | null,
  entries: PanelEntry[],
): SourceLine {
  const base = { sourceId: PANEL_SOURCE_ID, label: PANEL_SOURCE_LABEL, shortLabel: PANEL_SHORT_LABEL };
  switch (source.kind) {
    case 'not_provided':
      return {
        ...base,
        outcome: 'unsupported',
        headline: 'not set up in this office',
        summary: 'not set up in this office',
        statement:
          'This office was started without a request panel, so no questions, decisions or results from it can be listed. That is unknown, not “none”.',
      };
    case 'not_available':
      return {
        ...base,
        outcome: 'unsupported',
        headline: 'not available in this office',
        summary: 'not available in this office',
        statement: `${source.reason} Its questions, decisions and results can’t be listed here, which is not “none”.`,
      };
    case 'not_vouched':
      return {
        ...base,
        outcome: 'unavailable',
        headline: 'can’t be vouched for right now',
        summary: 'can’t be vouched for right now',
        statement:
          'This window can no longer vouch for the request panel, so nothing from it is shown as current.',
      };
    case 'not_read':
      return {
        ...base,
        outcome: 'unavailable',
        headline: 'not read yet',
        summary: 'not read yet',
        statement:
          'The request panel has not been read in this window yet, so nothing from it is listed. That is not “none”.',
      };
    case 'read': {
      const o = overview as MrOverview;
      const notReported = o.conversations.filter(
        (c) => c.questionsReporting === 'not_reported',
      ).length;
      const unreadable = o.conversations.filter(
        (c) => c.questionsReporting === 'unreadable',
      ).length;
      // the count the request panel's own card says and the inbox lists: ONE definition (`waitingOf`), the two kinds kept distinct
      const waiting = waitingOf(o);
      const bits = [
        `${waiting.total} waiting for you (${waiting.requests} ${waiting.requests === 1 ? 'request' : 'requests'}, ${waiting.conversations} ${waiting.conversations === 1 ? 'conversation' : 'conversations'})`,
      ];
      if (notReported > 0)
        bits.push(`${notReported} older conversation${notReported === 1 ? '' : 's'} not reported`);
      if (unreadable > 0) bits.push(`${unreadable} unreadable`);
      return {
        ...base,
        outcome:
          entries.length > 0 ? 'present' : notReported + unreadable > 0 ? 'partial' : 'known_empty',
        headline: `${waiting.total} waiting`,
        summary: `read · ${bits.join(' · ')}`,
        statement:
          `${PANEL_COVERAGE}${o.requestsTruncated ? ' The request list was cut off at its limit.' : ''}` +
          (notReported > 0
            ? ` ${notReported} conversation(s) were recorded before the panel read replies for questions: whether they wait for you is not reported.`
            : ''),
      };
    }
  }
}

/** Mission Core's pending decision revision for an entry, from its own cause detail (null: not stated). */
export function pendingDecisionRevision(entry: DiReceptionEntry): number | null {
  const cause = entry.causes.find((c) => c.cause === 'awaiting_decision');
  const value = cause?.details.find((d) => d.label === 'revision')?.value;
  if (value === undefined || value === null || !/^[0-9]+$/.test(value)) return null;
  return Number(value);
}

const isPendingDecision = (e: DiReceptionEntry): boolean =>
  e.category === 'authorization' && e.causes.some((c) => c.cause === 'awaiting_decision');

/**
 * What the person can do next on a Mission card, DERIVED FROM THE SAME ROUTE DATA THAT DECIDES THE BUTTON: the text and the "Open the request panel"
 * button come from one result, so they cannot disagree.
 *  - a decision the request panel also lists (merged): the decision is made there — the panel records it and this inbox approves nothing — and the
 *    panel button is offered only when this view can open the panel;
 *  - otherwise the Operator route is named, and only with the accurate reason there is no in-office route: no panel set up here, the panel
 *    could not be read, the panel does not list this decision (unlinked), or this kind of item has no panel route at all.
 * Never offers a control that acts: the button only opens the panel.
 */
export interface NextStep {
  text: string;
  panelButton: boolean;
}
export function missionNextStep(
  entry: DiReceptionEntry,
  twin: PanelEntry | null,
  panel: PanelSource,
  canOpenPanel: boolean,
): NextStep {
  if (twin !== null)
    return twin.requestRef !== null && canOpenPanel
      ? {
          text: 'Decide in the request panel (it records your decision; this inbox approves nothing).',
          panelButton: true,
        }
      : {
          text: 'Decide in the request panel. It can’t be opened from this view, and this inbox approves nothing.',
          panelButton: false,
        };
  let reason: string;
  if (!isPendingDecision(entry)) reason = 'This kind of item has no request-panel route.';
  else
    switch (panel.kind) {
      case 'not_provided':
        reason = 'This office has no request panel set up, so there is no in-office route for this decision.';
        break;
      case 'not_available':
        reason = 'The request panel isn’t available in this office, so there is no in-office route for this decision.';
        break;
      case 'not_vouched':
      case 'not_read':
        reason = 'The request panel couldn’t be read just now, so this decision can’t be tied to a request in it.';
        break;
      case 'read':
        reason = 'The request panel doesn’t list this decision (no request in it is tied to this Mission), so it can’t be decided there.';
        break;
    }
  return { text: `${reason} ${routeAction(entry.route)}`.trim(), panelButton: false };
}

export type DecisionLinkage = {
  /** Mission entry key → the panel request proven to be the same decision. */
  merged: Map<string, PanelEntry>;
  /** Panel requests that were folded into a Mission item. */
  consumed: Set<PanelEntry>;
  /** Panel requests whose Mission also lists a pending decision that is NOT proven to be this one. */
  unresolved: Map<PanelEntry, string>;
};

/**
 * One decision observed twice is ONE decision. Mission Core's `pending_decisions` (an entry with the
 * cause `awaiting_decision` at revision R) and the request panel's request offering `decide` for the
 * same Mission at revision R are reconciled ONLY when the evidence proves it:
 *
 * - the request's `mission_id` equals the entry's native id, and the entry is a `mission_core` one;
 * - exactly ONE `mission_core` Mission in this office has that native id (the linkage is unambiguous),
 *   and exactly one pending-decision entry and one `decide` request name it;
 * - that Mission and the pending-decision entry are the SAME record: equal source id and equal full
 *   `missionRef` (and equal to any Mission the request was already tied to);
 * - both state the same revision.
 *
 * Anything short of that stays two items (a verify or result item, a delivery proposal, a different
 * revision, an ambiguous source, a title that merely looks alike are never merged), and a request whose
 * Mission also lists a pending decision it cannot be tied to says so explicitly. Never by title or
 * native id alone.
 */
export function reconcileDecisions(
  entries: readonly DiReceptionEntry[],
  panelEntries: readonly PanelEntry[],
  missions: readonly DiMission[],
): DecisionLinkage {
  const out: DecisionLinkage = { merged: new Map(), consumed: new Set(), unresolved: new Map() };
  const pending = entries.filter(isPendingDecision);
  const decisions = panelEntries.filter((p) => p.decision && p.missionId !== null);
  for (const p of decisions) {
    const named = pending.filter((e) => e.nativeId === p.missionId);
    if (named.length === 0) continue; // nothing in Mission Core names it: simply a separate item
    const core = missions.filter(
      (m) => m.sourceKind === 'mission_core' && m.nativeId === p.missionId,
    );
    const coreEntries = named.filter((e) => e.sourceKind === 'mission_core');
    const sameMission = decisions.filter((q) => q.missionId === p.missionId);
    if (core.length !== 1 || named.length !== 1 || coreEntries.length !== 1) {
      out.unresolved.set(
        p,
        'Mission Core also lists a decision pending on a Mission with this id, but it cannot be tied to this request (the Mission is not unique among the sources read).',
      );
      continue;
    }
    if (sameMission.length !== 1) {
      out.unresolved.set(
        p,
        'More than one request is waiting on this Mission, so Mission Core’s pending decision cannot be tied to this one.',
      );
      continue;
    }
    const entry = coreEntries[0];
    // The Mission and the pending decision must be the SAME record, not two that share a native id:
    // the source and the full ref must agree, and so must any Mission the request was already linked to.
    const mission = core[0];
    if (mission.sourceId !== entry.sourceId || mission.missionRef !== entry.missionRef) {
      out.unresolved.set(
        p,
        `Mission Core lists a decision pending on ${entry.missionRef}, but this request's Mission is ${mission.missionRef}: they share an id but not a record, so they are not tied.`,
      );
      continue;
    }
    if (p.missionRef !== null && p.missionRef !== entry.missionRef) {
      out.unresolved.set(
        p,
        `This request is tied to ${p.missionRef}, but Mission Core's pending decision is on ${entry.missionRef}: they are not tied.`,
      );
      continue;
    }
    const revision = pendingDecisionRevision(entry);
    if (revision === null) {
      out.unresolved.set(
        p,
        'Mission Core lists a decision pending on this Mission without a revision, so it cannot be tied to this request.',
      );
      continue;
    }
    if (revision !== p.revision) {
      out.unresolved.set(
        p,
        `Mission Core lists a decision pending on this Mission at revision ${revision}, but this request is at revision ${p.revision ?? 'unknown'}: they are not shown to be the same decision.`,
      );
      continue;
    }
    out.merged.set(entry.key, p);
    out.consumed.add(p);
  }
  return out;
}

/**
 * The inbox: every item that needs the person, in the desk's category order, plus the information
 * list and one coverage line per source.
 */
export function buildInbox(
  reception: DiReception | undefined,
  panel: PanelSource,
  missions: readonly DiMission[],
  hidden: ReadonlySet<string>,
): InboxModel {
  const items: InboxItem[] = [];
  const information: DiReceptionSourceEntry[] = [];
  const sources: SourceLine[] = [];
  let notListed = 0;
  if (reception) {
    notListed = Math.max(0, reception.entriesTotal - reception.entries.length);
    for (const entry of reception.sourceEntries ?? []) {
      if (entry.needsYou)
        items.push({
          kind: 'delivery',
          key: entry.key,
          hideKey: `${entry.key}${nul}${entry.signature}`,
          category: entry.category,
          entry,
        });
      else information.push(entry);
    }
    sources.push(deliveryLine(reception));
  }
  const overview = panel.kind === 'read' ? panel.overview : null;
  const panelEntries =
    overview === null
      ? []
      : [...conversationEntries(overview), ...requestEntries(overview, missions)];
  const linkage = reconcileDecisions(reception?.entries ?? [], panelEntries, missions);
  for (const entry of reception?.entries ?? []) {
    const twin = linkage.merged.get(entry.key) ?? null;
    items.push({
      kind: 'mission',
      key: entry.key,
      hideKey:
        twin === null ? missionHideKey(entry) : `${missionHideKey(entry)}${nul}${twin.signature}`,
      category: entry.category,
      entry,
      panel: twin,
    });
  }
  for (const entry of panelEntries) {
    if (linkage.consumed.has(entry)) continue;
    const note = linkage.unresolved.get(entry) ?? null;
    const shown = note === null ? entry : { ...entry, unresolved: note };
    items.push({
      kind: 'panel',
      key: entry.key,
      hideKey: `${entry.key}${nul}${entry.signature}`,
      category: entry.category,
      entry: shown,
    });
  }
  sources.push(panelLine(panel, overview, panelEntries));
  items.sort(
    (a, b) =>
      (CATEGORY_RANK[a.category] ?? 9) - (CATEGORY_RANK[b.category] ?? 9) ||
      KIND_RANK[a.kind] - KIND_RANK[b.kind],
  );
  const visible = items.filter((i) => !hidden.has(i.hideKey));
  return {
    all: items,
    visible,
    hiddenCount: items.length - visible.length,
    information,
    attention: items.length + notListed,
    sources,
  };
}

// ── search and filter ────────────────────────────────────────────────────────────────────────────

export interface InboxFilter {
  query: string;
  category: DiReceptionCategoryId | null;
}

export const EMPTY_FILTER: InboxFilter = { query: '', category: null };

function haystack(item: InboxItem, labelOf: (c: DiReceptionCategoryId) => string): string {
  const parts: string[] = [labelOf(item.category), item.kind, item.key];
  if (item.kind === 'mission') {
    const e = item.entry;
    parts.push(
      formatObserved(e.objective),
      e.missionRef,
      e.sourceId,
      e.nativeId,
      ...e.causes.map((c) => c.label),
    );
    if (item.panel !== null) parts.push(item.panel.identity, item.panel.what);
  } else if (item.kind === 'delivery') {
    const e = item.entry;
    parts.push(
      e.proposalDigest,
      e.state,
      e.mode,
      e.repositoryUrl ?? '',
      e.workflowId ?? '',
      e.missionRef ?? '',
      plainDelivery(e).what,
    );
  } else {
    const e = item.entry;
    parts.push(e.identity, e.title, e.what, e.missionRef ?? '', ...e.questions);
  }
  return parts.join(' ').toLowerCase();
}

/** The items a search and a category chip leave in view: every whitespace-separated word must match. */
export function filterItems(
  items: readonly InboxItem[],
  filter: InboxFilter,
  labelOf: (c: DiReceptionCategoryId) => string,
): InboxItem[] {
  const words = filter.query.toLowerCase().split(/\s+/).filter(Boolean);
  return items.filter(
    (i) =>
      (filter.category === null || i.category === filter.category) &&
      words.every((w) => haystack(i, labelOf).includes(w)),
  );
}

export function chipsOf(
  reception: DiReception | undefined,
  items: readonly InboxItem[],
): Array<{ category: DiReceptionCategoryId; label: string; count: number }> {
  return (reception?.categories ?? [])
    .map((c) => ({
      category: c.category,
      label: c.label,
      count: items.filter((i) => i.category === c.category).length,
    }))
    .filter((c) => c.count > 0);
}

/** The one sentence the sidebar entry and the dialog's headline both say. */
export function attentionLine(count: number): string {
  return count === 0
    ? 'Nothing listed as needing attention'
    : `${count} ${count === 1 ? 'needs' : 'need'} attention`;
}

/**
 * The ordinary empty state, SCOPED in both branches. Even a successful read covers only bounded, recent
 * records (the panel's 10 conversations and 20 requests, the Grok route's 16 presentations, the
 * Missions this catalog lists), so it never says nothing needs the person — only that nothing is
 * LISTED. When any source could not be read, or does not report a kind, one more line says so; the
 * per-source bounds and the truncation lines stay with their sources.
 */
export function emptyStateLines(
  model: InboxModel,
  reception: DiReception | undefined,
  hasUnknowns: boolean,
): string[] {
  const unchecked =
    hasUnknowns ||
    (reception?.notices.length ?? 0) > 0 ||
    model.sources.some(
      (s) => s.outcome === 'unsupported' || s.outcome === 'unavailable' || s.outcome === 'partial',
    );
  const scoped = 'Nothing is listed as needing you right now.';
  return unchecked
    ? [
        scoped,
        'Some places could not be checked, so this is not a guarantee. See “What was checked” below.',
      ]
    : [scoped];
}

/**
 * Delivery proposals whose authorization status was NOT DETERMINED: presented, but this view could not
 * check whether a matching authorization is recorded, so none of them is called waiting, authorized or
 * expired. They stay visible as an uncertainty, never as "nothing needs you".
 */
export function undeterminedDeliveries(reception: DiReception | undefined): number {
  return (reception?.sourceEntries ?? []).filter((e) => e.state === 'unknown').length;
}
