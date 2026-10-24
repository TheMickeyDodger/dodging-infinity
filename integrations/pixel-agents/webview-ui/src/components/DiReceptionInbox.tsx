import { useEffect, useLayoutEffect, useRef, useState } from 'react';

import type {
  DiOfficeState,
  DiReception,
  DiReceptionCategoryId,
  DiReceptionEntry,
  DiReceptionOutcome,
  DiReceptionSourceEntry,
} from '../../../core/src/messages.js';
import type { DiKeptView } from './diMinimize.js';
import {
  focusExposed,
  keepView,
  restoreFocus,
  restoreScroll,
  useLastContentFocus,
} from './diMinimize.js';
import { DiMinimizedChip } from './DiMinimizedTray.js';
import { useDiModal } from './diModal.js';
import { DI_FOCUS_RING } from './diNavigation.js';
import { viewLimits } from './diPlainSummary.js';
import { EntryDetail } from './DiReceptionEntryDetail.js';
import {
  formatCategoryHeadline,
  formatEntryCurrency,
  formatGap,
  formatTruncation,
  outcomeClass,
  plainCategoryHeadline,
  RECEPTION_GLYPH,
  utcStamp,
} from './diReceptionFormat.js';
import type { InboxFilter, InboxItem, PanelEntry, PanelSource } from './diReceptionItems.js';
import {
  attentionLine,
  buildInbox,
  chipsOf,
  EMPTY_FILTER,
  emptyStateLines,
  filterItems,
  missionNextStep,
  pendingDecisionRevision,
  plainDelivery,
  plainStaleWords,
  undeterminedDeliveries,
} from './diReceptionItems.js';
import {
  entryGoal,
  firstLayerHappened,
  firstLayerWhy,
  notReportedLabels,
  plainReasons,
  plainServerWording,
  uncertaintyHeadline,
  uncertaintyLines,
} from './diReceptionPlain.js';
import { useRovingFocus } from './diRovingFocus.js';

/** The Mission routes the inbox may offer, all existing and view-only (absent: only "Show this Mission"). */
export interface DiReceptionMissionActions {
  /** Whether the Mission is in the office state (so its full-size details can open). */
  hasMission: (missionRef: string) => boolean;
  /** Whether the office reports an evidence library for it. */
  hasEvidence: (missionRef: string) => boolean;
  /** Close the inbox, then open that Mission's full-size details (at its evidence when asked). */
  openDetails: (missionRef: string, section?: 'evidence') => void;
}

/** Where the inbox may send the person in the request panel (view-only reads; nothing is sent). */
export interface DiReceptionPanelActions {
  /** Close the inbox, then open the request panel's Ask tab on this conversation. */
  openConversation: (conversationId: string) => void;
  /** Close the inbox, then open the request panel's Requests tab on this request. */
  openRequest: (requestRef: string) => void;
}

interface DiReceptionInboxProps {
  state: Pick<DiOfficeState, 'reception' | 'coverage' | 'fixtureMode' | 'missions'>;
  /** What this window knows about the Mission request panel (absent: it provides none). */
  panel?: PanelSource;
  panelActions?: DiReceptionPanelActions;
  /** Entries hidden in THIS view only (owned by the panel; never persisted, never sent). */
  hidden: ReadonlySet<string>;
  onHide: (hideKey: string) => void;
  onShowHidden: () => void;
  /**
   * The search and category filter. Owned by App when given, so it survives the dialog closing for a
   * Mission's details or evidence and is still there when the inbox is opened again.
   */
  filter?: InboxFilter;
  onFilterChange?: (filter: InboxFilter) => void;
  /** The entry that last had focus: focus returns to it when the inbox is opened again. */
  lastKey?: string | null;
  onFocusedKey?: (key: string) => void;
  /** View-only: show this entry's Mission in the Missions list and select its first drawn agent. */
  onJump: (missionRef: string) => void;
  missionActions?: DiReceptionMissionActions;
  onClose: () => void;
  returnFocusTo: { current: HTMLElement | null };
  /** AC3: minimized, the dialog stays mounted and hidden and the app is usable; Restore brings it back as it was. */
  minimized?: boolean;
  onMinimize?: () => void;
  onRestore?: () => void;
}

/** What each category covers: the old status stack, now one disclosure behind the entries. */
function Coverage({ reception }: { reception: DiReception }) {
  return (
    <ul className="flex flex-col gap-1" data-testid="di-reception-categories">
      {reception.categories.map((c) => (
        <li key={c.category} data-category={c.category} data-outcome={c.outcome}>
          <details>
            <summary className={`cursor-pointer break-words ${DI_FOCUS_RING}`}>
              {`${plainServerWording(c.label)}: `}
              <span className={outcomeClass(c.outcome)}>{plainCategoryHeadline(c)}</span>
            </summary>
            <div className="pl-8 text-text-muted break-all">{formatCategoryHeadline(c)}</div>
            <div className="pl-8 text-text-muted break-words">{plainServerWording(c.statement)}</div>
            <div className="pl-8 text-text-muted break-words">{`canonical backing: ${c.backing}`}</div>
            {c.gaps.map((g) => (
              <div key={`${g.outcome}:${g.reason}`} className="pl-8 text-text-muted break-all">
                {formatGap(g)}
              </div>
            ))}
          </details>
        </li>
      ))}
    </ul>
  );
}

/**
 * One entry as a card: what happened, why it needs a person and what they can do next, in plain
 * words from the entry's own canonical causes and route. The exact identity, every cause with its
 * details and the full route text are one labelled disclosure away.
 */
function InboxCard({
  entry,
  categoryLabel,
  fixture,
  index,
  count,
  rows,
  onJump,
  onHide,
  itemHideKey,
  onFocusKey,
  actions,
  twin = null,
  panel,
  panelActions,
}: {
  entry: DiReceptionEntry;
  categoryLabel: string;
  fixture: boolean;
  index: number;
  count: number;
  rows: ReturnType<typeof useRovingFocus<HTMLLIElement>>;
  onJump: (missionRef: string) => void;
  onHide: (hideKey: string) => void;
  /** The item's canonical hideKey: the Mission key, plus the panel's signature when the card is merged. It is what the inbox filters on. */
  itemHideKey: string;
  onFocusKey: (key: string) => void;
  actions?: DiReceptionMissionActions;
  /** The request panel's request for this SAME decision (proven by the evidence), or null. */
  twin?: PanelEntry | null;
  /** What this window knows about the request panel: decides the next-step wording together with `panelActions`. */
  panel: PanelSource;
  panelActions?: DiReceptionPanelActions;
}) {
  const goal = entryGoal(entry);
  const stale = !entry.current;
  const happened = firstLayerHappened(entry);
  const reasons = plainReasons(entry);
  // the next step and the panel button come from ONE result, so the text can never disagree with the button
  const next = missionNextStep(entry, twin, panel, panelActions !== undefined);
  return (
    <li
      ref={rows.refAt(index)}
      tabIndex={0}
      aria-keyshortcuts="ArrowUp ArrowDown Home End"
      aria-label={`${categoryLabel}: ${goal}`}
      className={`di-op-card di-rc-card ${DI_FOCUS_RING}`}
      onKeyDown={rows.onKeyDown(index, count)}
      onFocus={() => onFocusKey(entry.key)}
      data-testid="di-reception-entry"
      data-entry-kind="mission"
      data-entry-key={entry.key}
    >
      <div className="di-op-card-meta">
        <span className="text-warning">{`${RECEPTION_GLYPH.present} ${categoryLabel}`}</span>
        <span className={stale ? 'text-warning' : 'di-op-muted'}>{formatEntryCurrency(entry)}</span>
      </div>
      <p className="di-op-objective">{goal}</p>
      <dl className="di-rc-facts" data-testid="di-reception-facts">
        <dt>What happened</dt>
        <dd data-testid="di-reception-happened">
          {happened.lines.map((line) => (
            <span key={line} className="di-rc-line">
              {line}
            </span>
          ))}
          {happened.more > 0 && (
            <span className="di-rc-line di-op-muted" data-testid="di-reception-more">
              {`${happened.more} more ${happened.more === 1 ? 'reason is' : 'reasons are'} listed in the details.`}
            </span>
          )}
          {twin !== null && (
            <span className="di-rc-line" data-testid="di-reception-twin">
              The request panel shows the same decision.
            </span>
          )}
        </dd>
        <dt>Why it needs you</dt>
        <dd data-testid="di-reception-why">
          <span className="di-rc-line">{firstLayerWhy(entry)}</span>
        </dd>
        <dt>What you can do</dt>
        <dd data-testid="di-reception-next">{next.text}</dd>
      </dl>
      {entry.occurrences > 1 && (
        <div className="text-warning">{`reported ${entry.occurrences} times (one canonical record)`}</div>
      )}
      {entry.identityConflict && (
        <div className="text-warning">
          another record shares this identity with different content: both are shown
        </div>
      )}
      <div className="di-op-actions">
        {next.panelButton && twin !== null && twin.requestRef !== null && panelActions && (
          <button
            type="button"
            className={`di-op-button di-op-button-primary ${DI_FOCUS_RING}`}
            title="Opens the request panel here; nothing is sent or decided by opening it"
            data-testid="di-reception-open-panel"
            onClick={() => panelActions.openRequest(twin.requestRef as string)}
          >
            Open the request panel
          </button>
        )}
        <button
          type="button"
          className={`di-op-button ${next.panelButton ? '' : 'di-op-button-primary'} ${DI_FOCUS_RING}`}
          title="View only: shows this Mission in the Missions list and selects its first drawn agent"
          onClick={() => onJump(entry.missionRef)}
        >
          Show this Mission
        </button>
        {actions?.hasMission(entry.missionRef) && (
          <button
            type="button"
            className={`di-op-button ${DI_FOCUS_RING}`}
            title="View only: opens this Mission’s full-size details"
            onClick={() => actions.openDetails(entry.missionRef)}
          >
            Show details
          </button>
        )}
        {actions?.hasEvidence(entry.missionRef) && (
          <button
            type="button"
            className={`di-op-button ${DI_FOCUS_RING}`}
            title="View only: opens this Mission’s evidence library"
            onClick={() => actions.openDetails(entry.missionRef, 'evidence')}
          >
            Show evidence
          </button>
        )}
        <button
          type="button"
          className={`di-op-link ${DI_FOCUS_RING}`}
          title="A preference of this view only: resolves nothing and approves nothing"
          onClick={() => onHide(itemHideKey)}
        >
          Hide in this view
        </button>
      </div>
      <details>
        <summary className={`cursor-pointer text-text-muted ${DI_FOCUS_RING}`}>
          Identity, causes and the route to act
        </summary>
        {reasons.length > 0 && (
          <ul className="pl-8 text-text-muted" data-testid="di-reception-reasons">
            {reasons.map((r) => (
              <li key={r} data-testid="di-reception-reason">
                {r}
              </li>
            ))}
          </ul>
        )}
        <EntryDetail entry={entry} fixture={fixture} />
        {twin !== null && (
          <div
            className="flex flex-col gap-1 pl-8 text-text-muted break-all"
            data-testid="di-reception-twin-detail"
          >
            <div>{`The request panel lists this same decision (request ${twin.requestRef ?? twin.identity}, stage ${twin.stage ?? 'not stated'}). One decision, seen by two sources: Mission Core pending_decisions (revision ${pendingDecisionRevision(entry) ?? 'not stated'}) and the request panel's request ${twin.requestRef ?? twin.identity} (revision ${twin.revision ?? 'not stated'}, stage ${twin.stage ?? 'not stated'}, digest ${twin.digest ?? 'not stated'}).`}</div>
            <div>{twin.about}</div>
          </div>
        )}
      </details>
    </li>
  );
}

/** The Mission-linked buttons shared by every card that names a Mission. */
function MissionButtons({
  missionRef,
  onJump,
  actions,
}: {
  missionRef: string;
  onJump: (missionRef: string) => void;
  actions?: DiReceptionMissionActions;
}) {
  return (
    <>
      <button
        type="button"
        className={`di-op-button ${DI_FOCUS_RING}`}
        title="View only: shows this Mission in the Missions list and selects its first drawn agent"
        onClick={() => onJump(missionRef)}
      >
        Show this Mission
      </button>
      {actions?.hasMission(missionRef) && (
        <button
          type="button"
          className={`di-op-button ${DI_FOCUS_RING}`}
          title="View only: opens this Mission’s full-size details, where its code review is offered"
          onClick={() => actions.openDetails(missionRef)}
        >
          Show details
        </button>
      )}
      {actions?.hasEvidence(missionRef) && (
        <button
          type="button"
          className={`di-op-button ${DI_FOCUS_RING}`}
          title="View only: opens this Mission’s evidence library"
          onClick={() => actions.openDetails(missionRef, 'evidence')}
        >
          Show evidence
        </button>
      )}
    </>
  );
}

const HIDE_TITLE = 'A preference of this view only: resolves nothing and approves nothing';

/**
 * A delivery proposal that has no recorded authorization. It says it was PRESENTED, when, and until
 * when its approval window runs; it never says an approval is usable and offers no control: the way
 * to act is the original route, as text.
 */
function DeliveryCard({
  entry,
  label,
  index,
  count,
  rows,
  onJump,
  onHide,
  onFocusKey,
  actions,
}: {
  entry: DiReceptionSourceEntry;
  label: string;
  index: number;
  count: number;
  rows: ReturnType<typeof useRovingFocus<HTMLLIElement>>;
  onJump: (missionRef: string) => void;
  onHide: (hideKey: string) => void;
  onFocusKey: (key: string) => void;
  actions?: DiReceptionMissionActions;
}) {
  const plain = plainDelivery(entry);
  return (
    <li
      ref={rows.refAt(index)}
      tabIndex={0}
      aria-keyshortcuts="ArrowUp ArrowDown Home End"
      aria-label={`${label}: ${plain.what}`}
      className={`di-op-card di-rc-card ${DI_FOCUS_RING}`}
      onKeyDown={rows.onKeyDown(index, count)}
      onFocus={() => onFocusKey(entry.key)}
      data-testid="di-reception-entry"
      data-entry-kind="delivery"
      data-entry-key={entry.key}
    >
      <div className="di-op-card-meta">
        <span className="text-warning">{`${RECEPTION_GLYPH.present} ${label}`}</span>
        <span
          className={entry.current ? 'di-op-muted' : 'text-warning'}
          data-testid="di-reception-currency"
        >
          {entry.current
            ? 'current'
            : `last read, now out of date${
                plainStaleWords(entry.staleReasons) === ''
                  ? ''
                  : `: ${plainStaleWords(entry.staleReasons)}`
              }`}
        </span>
      </div>
      <p className="di-op-objective">{plain.what}</p>
      <dl className="di-rc-facts" data-testid="di-reception-facts">
        <dt>What happened</dt>
        <dd data-testid="di-reception-happened">
          <span className="di-rc-line">{`Delivery ${plain.when}.`}</span>
          <span className="di-rc-line" data-testid="di-reception-link">
            {plain.link}
          </span>
        </dd>
        <dt>Why it needs you</dt>
        <dd data-testid="di-reception-why">
          <span className="di-rc-line">{plain.why}</span>
        </dd>
        <dt>What you can do</dt>
        <dd data-testid="di-reception-next">{plain.next}</dd>
      </dl>
      <div className="di-op-actions">
        {entry.missionRef !== null && (
          <MissionButtons missionRef={entry.missionRef} onJump={onJump} actions={actions} />
        )}
        <button
          type="button"
          className={`di-op-link ${DI_FOCUS_RING}`}
          title={HIDE_TITLE}
          onClick={() => onHide(`${entry.key}\u0000${entry.signature}`)}
        >
          Hide in this view
        </button>
      </div>
      <details>
        <summary className={`cursor-pointer text-text-muted ${DI_FOCUS_RING}`}>
          Identity and the exact record
        </summary>
        <div
          className="flex flex-col gap-1 pl-8 text-text-muted break-all"
          data-testid="di-reception-detail"
        >
          <div>{`source ${entry.sourceId} · proposal ${entry.proposalDigest}`}</div>
          <div>{`state ${entry.state} · mode ${entry.mode} · matching deliveries ${entry.matchingDeliveries ?? 'unknown'}`}</div>
          <div>{`presented ${utcStamp(entry.presentedAt)} · expires ${utcStamp(entry.expiresAt)}`}</div>
          <div>{`link ${entry.linkState}${entry.missionRef === null ? '' : ` → ${entry.missionRef}`} · workflow ${entry.workflowId ?? 'none named'} · authorization digest comparison: ${entry.authorizationCheck}`}</div>
          <div>{`currency: ${entry.current ? 'current' : `stale (${entry.staleReasons.join('; ') || 'reason not given'})`}`}</div>
          <div>{`route: ${entry.route}`}</div>
          <div data-testid="di-reception-about-entry">{plain.about}</div>
        </div>
      </details>
    </li>
  );
}

/**
 * Something the Mission request panel reports needs the person: a conversation waiting for their
 * answer, a proposal waiting for their decision, an approval to check, a result to verify. Its words
 * are the panel's own and UNTRUSTED display text. The one primary action opens the panel where the
 * person reads it and acts; nothing is answered, decided, started or verified from here.
 */
function PanelCard({
  entry,
  label,
  index,
  count,
  rows,
  onJump,
  onHide,
  onFocusKey,
  actions,
  panelActions,
}: {
  entry: PanelEntry;
  label: string;
  index: number;
  count: number;
  rows: ReturnType<typeof useRovingFocus<HTMLLIElement>>;
  onJump: (missionRef: string) => void;
  onHide: (hideKey: string) => void;
  onFocusKey: (key: string) => void;
  actions?: DiReceptionMissionActions;
  panelActions?: DiReceptionPanelActions;
}) {
  const isConversation = entry.conversationId !== null;
  return (
    <li
      ref={rows.refAt(index)}
      tabIndex={0}
      aria-keyshortcuts="ArrowUp ArrowDown Home End"
      aria-label={`${label}: ${entry.what}`}
      className={`di-op-card di-rc-card ${DI_FOCUS_RING}`}
      onKeyDown={rows.onKeyDown(index, count)}
      onFocus={() => onFocusKey(entry.key)}
      data-testid="di-reception-entry"
      data-entry-kind="panel"
      data-entry-key={entry.key}
    >
      <div className="di-op-card-meta">
        <span className="text-warning">{`${RECEPTION_GLYPH.present} ${label}`}</span>
        <span className="di-op-muted">from the request panel</span>
      </div>
      <p className="di-op-objective" data-testid="di-reception-title">
        {entry.title}
      </p>
      <dl className="di-rc-facts" data-testid="di-reception-facts">
        <dt>What happened</dt>
        <dd data-testid="di-reception-happened">
          <span className="di-rc-line">{entry.what}</span>
          {entry.unresolved !== null && (
            <span className="di-rc-line" data-testid="di-reception-unresolved">
              {entry.unresolved}
            </span>
          )}
          {entry.questions.map((q) => (
            <span key={q} className="di-rc-line" data-testid="di-reception-question">
              {`Asked: “${q}”`}
            </span>
          ))}
        </dd>
        <dt>Why it needs you</dt>
        <dd data-testid="di-reception-why">
          <span className="di-rc-line">{entry.why}</span>
        </dd>
        <dt>What you can do</dt>
        <dd data-testid="di-reception-next">{entry.next}</dd>
      </dl>
      <div className="di-op-actions">
        {panelActions && (
          <button
            type="button"
            className={`di-op-button di-op-button-primary ${DI_FOCUS_RING}`}
            title="Opens the request panel here; nothing is sent or decided by opening it"
            data-testid="di-reception-open-panel"
            onClick={() =>
              isConversation
                ? panelActions.openConversation(entry.conversationId as string)
                : panelActions.openRequest(entry.requestRef as string)
            }
          >
            {isConversation ? 'Open the request panel (Ask)' : 'Open the request panel'}
          </button>
        )}
        {entry.missionRef !== null && (
          <MissionButtons missionRef={entry.missionRef} onJump={onJump} actions={actions} />
        )}
        <button
          type="button"
          className={`di-op-link ${DI_FOCUS_RING}`}
          title={HIDE_TITLE}
          onClick={() => onHide(`${entry.key}\u0000${entry.signature}`)}
        >
          Hide in this view
        </button>
      </div>
      <details>
        <summary className={`cursor-pointer text-text-muted ${DI_FOCUS_RING}`}>
          Identity and the panel’s record
        </summary>
        <div
          className="flex flex-col gap-1 pl-8 text-text-muted break-all"
          data-testid="di-reception-detail"
        >
          <div>{`source ${'request_panel'} · ${isConversation ? 'conversation' : 'request'} ${entry.identity}`}</div>
          <div>{entry.linkNote}</div>
          <div data-testid="di-reception-about-entry">{entry.about}</div>
        </div>
      </details>
    </li>
  );
}

/**
 * The RECEPTION INBOX: the ONE dialog the sidebar's attention entry and the in-world reception desk
 * both open (PR69's full-size details pattern, diModal.ts: #root inert, focus in, Tab trapped, Escape
 * and Close restore focus to whatever opened it).
 *
 * Reads top to bottom: the short headline, a search and category filter, what could not be checked
 * (material unknowns stay in view, in plain words), then the entries as cards that say what happened,
 * why it needs a person and the supported next step. The whole dialog body is ONE scroll region: the
 * list is never capped or nested. Coverage, the read-only explanation and the exact reasons are
 * disclosures below.
 *
 * Read-only: nothing here approves, starts, retries or delivers. Closing it controls nothing.
 */
export function DiReceptionInbox({
  state,
  panel = { kind: 'not_provided' },
  panelActions,
  hidden,
  onHide,
  onShowHidden,
  filter: controlledFilter,
  onFilterChange,
  lastKey = null,
  onFocusedKey,
  onJump,
  missionActions,
  onClose,
  returnFocusTo,
  minimized = false,
  onMinimize,
  onRestore,
}: DiReceptionInboxProps) {
  const dialogRef = useRef<HTMLDivElement>(null);
  const onKeyDown = useDiModal(dialogRef, onClose, returnFocusTo, !minimized);
  // AC3: what hiding loses (the body's scroll) is kept on minimize and given back on restore, and focus returns to
  // the last control in the dialog that had it (never the toggles; diMinimize.ts); the search, the category
  // filter, the hidden entries and the open disclosures stay as they are.
  const minimizeRef = useRef<HTMLButtonElement>(null);
  const restoreRef = useRef<HTMLButtonElement>(null);
  const keptView = useRef<DiKeptView | null>(null);
  const lastContentFocus = useLastContentFocus(dialogRef, minimizeRef);
  const minimizeInbox = () => {
    if (dialogRef.current) keptView.current = keepView(dialogRef.current, lastContentFocus.current);
    onMinimize?.();
  };
  const shownMinimized = useRef(minimized);
  useLayoutEffect(() => {
    if (shownMinimized.current === minimized) return;
    shownMinimized.current = minimized;
    if (minimized) {
      focusExposed(restoreRef.current);
      return;
    }
    const kept = keptView.current;
    keptView.current = null;
    restoreScroll(kept);
    restoreFocus(kept, minimizeRef.current, dialogRef.current);
  }, [minimized]);
  const rows = useRovingFocus<HTMLLIElement>();
  const [localFilter, setLocalFilter] = useState<InboxFilter>(EMPTY_FILTER);
  const filter = controlledFilter ?? localFilter;
  const setFilter = onFilterChange ?? setLocalFilter;
  const { reception } = state;
  const fixture = state.fixtureMode === true;
  const model = buildInbox(reception, panel, state.missions?.missions ?? [], hidden);
  const limits = viewLimits(state);
  const labelOf = (id: DiReceptionCategoryId) =>
    plainServerWording(reception?.categories.find((c) => c.category === id)?.label ?? id);
  const shown = filterItems(model.visible, filter, labelOf);
  const chips = chipsOf(reception, model.visible);
  const filtering = filter.query.trim() !== '' || filter.category !== null;
  const undetermined = undeterminedDeliveries(reception);
  const unknowns = [
    ...(reception ? uncertaintyLines(reception) : []),
    ...(undetermined > 0
      ? [
          `${undetermined} delivery ${undetermined === 1 ? 'proposal was' : 'proposals were'} presented, but whether ${undetermined === 1 ? 'it has' : 'each has'} a recorded authorization was not determined.`,
        ]
      : []),
  ];
  const notReported = reception ? notReportedLabels(reception) : [];
  const uncertainty = reception ? uncertaintyHeadline(reception, undetermined) : null;
  const truncation = reception ? formatTruncation(reception) : null;
  const information = model.information.filter(
    (e) =>
      filterItems(
        [{ kind: 'delivery', key: e.key, hideKey: '', category: e.category, entry: e }],
        filter,
        labelOf,
      ).length > 0,
  );
  const clearFilter = () => setFilter(EMPTY_FILTER);
  // Coming back to the inbox (from a Mission's details or evidence, or the request panel) puts focus
  // on the entry that last had it, when that entry is still listed.
  useEffect(() => {
    if (lastKey === null) return;
    const card = Array.from(
      dialogRef.current?.querySelectorAll<HTMLElement>('[data-entry-key]') ?? [],
    ).find((el) => el.getAttribute('data-entry-key') === lastKey);
    card?.focus();
    // Once, when the dialog mounts.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  const remember = (key: string) => onFocusedKey?.(key);
  const waitingOnYou = reception ? model.attention : 0;
  return (
    <>
      {minimized && onRestore && (
        <DiMinimizedChip>
          <button
            type="button"
            ref={restoreRef}
            // AC4 when entries wait: a still glow, the ▲ shape and the count in words
            className={`di-op-theme di-op-collapsed${waitingOnYou > 0 ? ' di-op-attn' : ''} ${DI_FOCUS_RING}`}
            data-testid="di-reception-restore"
            data-attention={waitingOnYou > 0 ? 'true' : undefined}
            onClick={onRestore}
            title="Restore reception as you left it"
            aria-label={`Restore reception, ${reception ? attentionLine(model.attention) : 'no reception desk'}`}
            data-focus-at-hide={keptView.current?.focusAtHide}
            data-focus-return={keptView.current?.focusReturn}
          >
            {waitingOnYou > 0 && (
              <span className="di-op-attn-glyph" aria-hidden="true">
                ▲
              </span>
            )}
            <span className="di-op-collapsed-label">Restore reception</span>
            <span className="di-op-collapsed-state">
              {reception ? attentionLine(model.attention) : 'no reception desk'}
            </span>
          </button>
        </DiMinimizedChip>
      )}
      <div
        className="di-op-theme di-op-inspector-backdrop"
        data-testid="di-reception-inbox-backdrop"
        hidden={minimized}
        onMouseDown={(event) => {
          if (event.target === event.currentTarget) onClose();
        }}
      >
        <div
          ref={dialogRef}
          className="di-op-inspector"
          role="dialog"
          aria-modal="true"
          aria-labelledby="di-reception-inbox-title"
          tabIndex={-1}
          onKeyDown={onKeyDown}
          data-testid="di-reception-inbox"
        >
          <header className="di-op-inspector-head">
            <div className="di-op-inspector-heading">
              <div className="di-op-muted">Reception{fixture ? ' · FIXTURE DATA' : ''}</div>
              <h2 id="di-reception-inbox-title" className="di-op-inspector-title">
                Reception
              </h2>
              <div className="di-op-now" data-testid="di-reception-inbox-summary">
                {reception
                  ? attentionLine(model.attention)
                  : 'This office provides no reception desk.'}
              </div>
              <div className="di-op-muted" data-testid="di-reception-inbox-quiet">
                View only: nothing here approves, starts or delivers anything.
              </div>
            </div>
            <div className="di-op-actions di-op-inspector-tools">
              {onMinimize && (
                <button
                  type="button"
                  ref={minimizeRef}
                  className={`di-op-button ${DI_FOCUS_RING}`}
                  data-testid="di-reception-minimize"
                  onClick={minimizeInbox}
                  title="Minimize reception; Restore brings it back as you left it"
                >
                  Minimize
                </button>
              )}
              <button
                type="button"
                className={`di-op-button ${DI_FOCUS_RING}`}
                data-testid="di-reception-inbox-close"
                onClick={onClose}
              >
                Close
              </button>
            </div>
          </header>
          <div className="di-op-inspector-body" data-testid="di-reception-inbox-body">
            {reception ? (
              <>
                <section
                  className="di-op-find di-rc-section"
                  aria-label="Search and filter"
                  data-testid="di-reception-filter"
                >
                  <label htmlFor="di-reception-search" className="sr-only">
                    Search what needs you
                  </label>
                  <input
                    id="di-reception-search"
                    type="search"
                    autoComplete="off"
                    spellCheck={false}
                    placeholder="Search by Mission, category or id"
                    className={DI_FOCUS_RING}
                    data-testid="di-reception-search"
                    aria-controls="di-reception-entries"
                    aria-keyshortcuts="ArrowDown"
                    value={filter.query}
                    onChange={(e) => setFilter({ ...filter, query: e.target.value })}
                    onKeyDown={(e) => {
                      if (e.key === 'ArrowDown' && shown.length > 0) {
                        e.preventDefault();
                        rows.focusFirst();
                      }
                    }}
                  />
                  {chips.length > 1 && (
                    <div
                      className="di-op-actions"
                      role="group"
                      aria-label="Filter by category"
                      data-testid="di-reception-chips"
                    >
                      <button
                        type="button"
                        className={`di-op-button ${filter.category === null ? 'di-op-button-primary' : ''} ${DI_FOCUS_RING}`}
                        aria-pressed={filter.category === null}
                        onClick={() => setFilter({ ...filter, category: null })}
                      >{`All (${model.visible.length})`}</button>
                      {chips.map((c) => (
                        <button
                          key={c.category}
                          type="button"
                          className={`di-op-button ${filter.category === c.category ? 'di-op-button-primary' : ''} ${DI_FOCUS_RING}`}
                          aria-pressed={filter.category === c.category}
                          data-category={c.category}
                          onClick={() =>
                            setFilter({
                              ...filter,
                              category: filter.category === c.category ? null : c.category,
                            })
                          }
                        >{`${plainServerWording(c.label)} (${c.count})`}</button>
                      ))}
                    </div>
                  )}
                  <div
                    className="di-op-find-outcome"
                    role="status"
                    data-testid="di-reception-filter-status"
                  >
                    {filtering ? `Showing ${shown.length} of ${model.visible.length} entries.` : ''}
                  </div>
                </section>

                {/* ONE or TWO plain lines above the cards — what could not be fully checked and what each source says — with ALL the detail behind ONE
                  disclosure. Material uncertainty stays visible in plain words; a source that was not read is never reported as "none". */}
                <section
                  className="di-rc-section di-rc-unknowns"
                  aria-label="What was checked"
                  data-testid="di-reception-unknowns"
                >
                  {reception.notices.map((n) => (
                    <div key={n.reason} className="text-warning" data-testid="di-reception-notice">
                      {`${RECEPTION_GLYPH.unavailable} ${plainServerWording(n.statement)}`}
                    </div>
                  ))}
                  {uncertainty !== null && (
                    <div className="text-warning" data-testid="di-reception-uncertainty">
                      {`${RECEPTION_GLYPH.unavailable} ${uncertainty}`}
                    </div>
                  )}
                  <div className="di-op-muted" data-testid="di-reception-source-summary">
                    {model.sources.map((src) => `${src.shortLabel}: ${src.headline}`).join(' · ')}
                  </div>
                  <details data-testid="di-reception-coverage">
                    <summary className={`cursor-pointer text-text-muted ${DI_FOCUS_RING}`}>
                      What was checked, and what each category covers
                    </summary>
                    <div className="di-op-detail-body">
                      <h3 className="di-op-muted">What was checked</h3>
                      <ul className="flex flex-col gap-1" data-testid="di-reception-sources">
                        {model.sources.map((src) => (
                          <li
                            key={src.sourceId}
                            data-source={src.sourceId}
                            data-outcome={src.outcome}
                          >
                            <span className={outcomeClass(src.outcome as DiReceptionOutcome)}>
                              {`${RECEPTION_GLYPH[src.outcome]} ${plainServerWording(src.label)}: ${src.summary}`}
                            </span>
                            <div className="pl-8 text-text-muted break-words">
                              {plainServerWording(src.statement)}
                            </div>
                          </li>
                        ))}
                      </ul>
                      {(unknowns.length > 0 || notReported.length > 0) && (
                        <>
                          <h3 className="di-op-muted">What could not be checked</h3>
                          {unknowns.map((line) => (
                            <div key={line} data-testid="di-reception-unknown">
                              {`${RECEPTION_GLYPH.unavailable} ${line}`}
                            </div>
                          ))}
                          {notReported.length > 0 && (
                            <div data-testid="di-reception-not-reported">
                              {`${RECEPTION_GLYPH.unsupported} Not reported by the catalog: ${notReported.join('; ')}.`}
                            </div>
                          )}
                        </>
                      )}
                      <h3 className="di-op-muted">{`What each category covers (${reception.categories.length})`}</h3>
                      <Coverage reception={reception} />
                    </div>
                  </details>
                </section>

                {shown.length === 0 ? (
                  <div className="di-op-muted di-rc-section" data-testid="di-reception-no-entries">
                    {model.visible.length > 0 ? (
                      <>
                        No entries match this search or filter.{' '}
                        <button
                          type="button"
                          className={`di-op-link ${DI_FOCUS_RING}`}
                          onClick={clearFilter}
                        >
                          Clear the filter
                        </button>
                      </>
                    ) : model.all.length === 0 ? (
                      emptyStateLines(model, reception, unknowns.length > 0).map((line) => (
                        <p key={line}>{line}</p>
                      ))
                    ) : (
                      'Every entry is hidden in this view.'
                    )}
                  </div>
                ) : (
                  <ul
                    id="di-reception-entries"
                    className="di-rc-list"
                    aria-label="What needs you (arrow keys move between entries)"
                    data-testid="di-reception-entries"
                  >
                    {shown.map((item, i) => (
                      <InboxRow
                        key={item.hideKey}
                        item={item}
                        label={labelOf(item.category)}
                        fixture={fixture}
                        index={i}
                        count={shown.length}
                        rows={rows}
                        onJump={onJump}
                        onHide={onHide}
                        onFocusKey={remember}
                        actions={missionActions}
                        panel={panel}
                        panelActions={panelActions}
                      />
                    ))}
                  </ul>
                )}
                {truncation && (
                  <div className="text-warning" data-testid="di-reception-truncated">
                    {truncation}
                  </div>
                )}
                {model.hiddenCount > 0 && (
                  <div className="di-op-card-foot di-rc-section" data-testid="di-reception-hidden">
                    <span className="di-op-muted">{`${model.hiddenCount} hidden in this view only`}</span>
                    <button
                      type="button"
                      className={`di-op-link ${DI_FOCUS_RING}`}
                      onClick={onShowHidden}
                    >
                      {`Show ${model.hiddenCount} hidden`}
                    </button>
                  </div>
                )}

                {information.length > 0 && (
                  <details className="di-rc-section" data-testid="di-reception-information">
                    <summary className={DI_FOCUS_RING}>
                      {`Delivery proposals not listed as waiting on you (${information.length})`}
                    </summary>
                    <div className="di-op-detail-body">
                      <p className="di-op-muted">
                        Information only: each was presented through the Grok Bot route. Where it
                        says the status was not determined, this view could not check whether an
                        authorization is recorded, so it may still be waiting for a decision.
                        Nothing here says an approval is usable.
                      </p>
                      <ul className="flex flex-col gap-1">
                        {information.map((e) => {
                          const plain = plainDelivery(e);
                          return (
                            <li
                              key={e.key}
                              data-testid="di-reception-information-entry"
                              data-state={e.state}
                            >
                              <div>{plain.what}</div>
                              <div className="text-text-muted">{`Delivery ${plain.when}.`}</div>
                              <div className="text-text-muted">{plain.link}</div>
                            </li>
                          );
                        })}
                      </ul>
                    </div>
                  </details>
                )}

                <details className="di-rc-section" data-testid="di-reception-about">
                  <summary className={DI_FOCUS_RING}>
                    About reception: what it is, how it refreshes and what hiding does
                  </summary>
                  <div className="di-op-detail-body">
                    <p>
                      Reception lists what your Missions, delivery proposals and the request panel
                      report that needs a person. It cannot approve, start, retry or deliver
                      anything: each entry says where you act, and hiding an entry here is a
                      preference of this view that resolves nothing.
                    </p>
                    <p>{`Read-only · contract version ${reception.contractVersion} · ${reception.refreshStatement}`}</p>
                    <p>{reception.dismissalStatement}</p>
                  </div>
                </details>
                {limits && (
                  <details className="di-rc-section" data-testid="di-reception-inbox-limits">
                    <summary className={DI_FOCUS_RING}>What this view may not show</summary>
                    <div className="di-op-detail-body">
                      <p>{limits.summary}</p>
                      <ul>
                        {limits.items.map((item) => (
                          <li key={item}>{item}</li>
                        ))}
                      </ul>
                    </div>
                  </details>
                )}
              </>
            ) : (
              <section className="di-op-muted" data-testid="di-reception-absent">
                Reception desk: not provided by this office.
              </section>
            )}
          </div>
        </div>
      </div>
    </>
  );
}

/** One row of the list, whichever source it came from. */
function InboxRow({
  item,
  label,
  fixture,
  index,
  count,
  rows,
  onJump,
  onHide,
  onFocusKey,
  actions,
  panel,
  panelActions,
}: {
  item: InboxItem;
  label: string;
  fixture: boolean;
  index: number;
  count: number;
  rows: ReturnType<typeof useRovingFocus<HTMLLIElement>>;
  onJump: (missionRef: string) => void;
  onHide: (hideKey: string) => void;
  onFocusKey: (key: string) => void;
  actions?: DiReceptionMissionActions;
  panel: PanelSource;
  panelActions?: DiReceptionPanelActions;
}) {
  if (item.kind === 'mission')
    return (
      <InboxCard
        entry={item.entry}
        categoryLabel={label}
        fixture={fixture}
        index={index}
        count={count}
        rows={rows}
        onJump={onJump}
        onHide={onHide}
        itemHideKey={item.hideKey}
        onFocusKey={onFocusKey}
        actions={actions}
        twin={item.panel}
        panel={panel}
        panelActions={panelActions}
      />
    );
  if (item.kind === 'delivery')
    return (
      <DeliveryCard
        entry={item.entry}
        label={label}
        index={index}
        count={count}
        rows={rows}
        onJump={onJump}
        onHide={onHide}
        onFocusKey={onFocusKey}
        actions={actions}
      />
    );
  return (
    <PanelCard
      entry={item.entry}
      label={label}
      index={index}
      count={count}
      rows={rows}
      onJump={onJump}
      onHide={onHide}
      onFocusKey={onFocusKey}
      actions={actions}
      panelActions={panelActions}
    />
  );
}
