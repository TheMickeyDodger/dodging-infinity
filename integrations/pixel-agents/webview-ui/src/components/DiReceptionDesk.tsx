import { useRef } from 'react';

import type { DiReception, DiReceptionEntry } from '../../../core/src/messages.js';
import { formatLive, formatObserved } from './diOfficeFormat.js';
import {
  formatCategoryHeadline,
  formatEntryCurrency,
  formatGap,
  formatTruncation,
  hideKey,
  nextEntryIndex,
  outcomeClass,
  RECEPTION_GLYPH,
  visibleEntries,
} from './diReceptionFormat.js';

interface DiReceptionDeskProps {
  reception: DiReception;
  /** Entries hidden in THIS view only (owned by the panel; never persisted, never sent). */
  hidden: ReadonlySet<string>;
  onHide: (hideKey: string) => void;
  onShowHidden: () => void;
  /** View-only navigation to the Mission in the Missions list. */
  onJump: (missionRef: string) => void;
  /** An explicit FIXTURE preview: every LIVE is then qualified as fixture data. */
  fixture?: boolean;
  /**
   * feature13: the office panel's COMPACT desk. The same entries, keyboard list and controls; the
   * categories, contract notes and each entry's identity, causes and route sit behind labelled
   * disclosures instead of inline. Nothing is removed.
   */
  compact?: boolean;
}

/** Inset (negative offset): the scrolling entry list can never clip the focus indicator. */
const FOCUS =
  'focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-accent';

function Entry({
  entry,
  categoryLabel,
  onHide,
  onJump,
  fixture,
  compact,
}: {
  entry: DiReceptionEntry;
  categoryLabel: string;
  onHide: (hideKey: string) => void;
  onJump: (missionRef: string) => void;
  fixture: boolean;
  compact: boolean;
}) {
  const head = (
    <>
      <div className="flex items-start justify-between gap-4">
        <span className="text-warning">{`${RECEPTION_GLYPH.present} ${categoryLabel}`}</span>
        <span className={entry.current ? 'text-text-muted' : 'text-warning'}>
          {formatEntryCurrency(entry)}
        </span>
      </div>
      <div className="line-clamp-2 break-words text-sm">{formatObserved(entry.objective)}</div>
    </>
  );
  const actions = (
    <div className="flex gap-6">
      <button
        type="button"
        className={`cursor-pointer ${FOCUS}`}
        title="View only: shows this Mission in the Missions list and selects its first drawn agent"
        onClick={() => onJump(entry.missionRef)}
      >
        Show this Mission
      </button>
      <button
        type="button"
        className={`cursor-pointer text-text-muted ${FOCUS}`}
        title="A preference of this view only: resolves nothing and approves nothing"
        onClick={() => onHide(hideKey(entry))}
      >
        Hide in this view
      </button>
    </div>
  );
  if (compact)
    return (
      <>
        {head}
        {entry.occurrences > 1 && (
          <div className="text-warning">{`reported ${entry.occurrences} times (one canonical record)`}</div>
        )}
        {entry.identityConflict && (
          <div className="text-warning">
            another record shares this identity with different content: both are shown
          </div>
        )}
        <details>
          <summary className={`cursor-pointer text-text-muted ${FOCUS}`}>
            Identity, causes and the route to act
          </summary>
          <EntryDetail entry={entry} fixture={fixture} />
        </details>
        {actions}
      </>
    );
  return (
    <>
      {head}
      <EntryDetailLines entry={entry} fixture={fixture} />
      {entry.occurrences > 1 && (
        <div className="text-warning">{`reported ${entry.occurrences} times (one canonical record)`}</div>
      )}
      {entry.identityConflict && (
        <div className="text-warning">
          another record shares this identity with different content: both are shown
        </div>
      )}
      <EntryCauses entry={entry} />
      <EntryRoute entry={entry} />
      {actions}
    </>
  );
}

/** The compact desk's per-entry disclosure: the same lines the full desk shows inline. */
function EntryDetail({ entry, fixture }: { entry: DiReceptionEntry; fixture: boolean }) {
  return (
    <div className="flex flex-col gap-1 pl-8">
      <EntryDetailLines entry={entry} fixture={fixture} />
      <EntryCauses entry={entry} />
      <EntryRoute entry={entry} />
    </div>
  );
}

function EntryDetailLines({ entry, fixture }: { entry: DiReceptionEntry; fixture: boolean }) {
  return (
    <>
      <div className="text-text-muted break-all">
        {`${entry.missionRef} · source ${entry.sourceId} (${entry.sourceKind}) · native ${entry.nativeId} · producer (declared) ${entry.producerDeclared}`}
      </div>
      <div className="text-text-muted break-all">
        {`${entry.lifecycleModel} · ${entry.standing} / ${entry.lifecycleState} · binding ${entry.bindingState} (${entry.bindingReason}) · ${formatLive(entry.live, entry.notLiveReason, fixture)}`}
      </div>
    </>
  );
}

function EntryCauses({ entry }: { entry: DiReceptionEntry }) {
  return (
    <>
      <ul className="flex flex-col gap-1 pl-8">
        {entry.causes.map((cause) => (
          <li key={cause.cause} className="break-words">
            {cause.label}
            {cause.details.map((detail, i) => (
              <div key={i} className="pl-8 text-text-muted break-all">
                {`${detail.label}: ${detail.value ?? 'not recorded'}`}
              </div>
            ))}
          </li>
        ))}
      </ul>
      {entry.alsoUnknown.length > 0 && (
        <div className="text-text-muted break-all">
          {`also not known for this Mission: ${entry.alsoUnknown.join('; ')}`}
        </div>
      )}
    </>
  );
}

function EntryRoute({ entry }: { entry: DiReceptionEntry }) {
  return (
    <details>
      <summary className={`cursor-pointer text-text-muted ${FOCUS}`}>
        Route to act (text only; this desk performs nothing)
      </summary>
      <div className="pl-8 break-words">{entry.route}</div>
      {entry.routeIdentifiers.map((id) => (
        <div key={id.label} className="pl-8 text-text-muted break-all">
          {`${id.label}: ${id.value ?? 'not recorded'}`}
        </div>
      ))}
    </details>
  );
}

/** Each category's outcome in words and shape; its statement, backing and gaps one step behind. */
function Categories({ reception }: { reception: DiReception }) {
  return (
    <ul className="flex flex-col gap-1" data-testid="di-reception-categories">
      {reception.categories.map((c) => (
        <li key={c.category} data-category={c.category} data-outcome={c.outcome}>
          <details>
            <summary className={`cursor-pointer break-words ${FOCUS}`}>
              {`${c.label}: `}
              <span className={outcomeClass(c.outcome)}>{formatCategoryHeadline(c)}</span>
            </summary>
            <div className="pl-8 text-text-muted break-words">{c.statement}</div>
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
 * The Mission reception desk: ONE attention view, read-only, as a list (the non-spatial route to
 * every Mission it names). Each category states its outcome in words and shape; each entry carries
 * its full source-qualified identity, its causes verbatim, its freshness and the route to act as
 * TEXT. Nothing here animates, polls, writes or sends.
 */
export function DiReceptionDesk({
  reception,
  hidden,
  onHide,
  onShowHidden,
  onJump,
  fixture = false,
  compact = false,
}: DiReceptionDeskProps) {
  const { visible, hidden: hiddenCount } = visibleEntries(reception.entries, hidden);
  const itemRefs = useRef<Array<HTMLLIElement | null>>([]);
  const truncation = formatTruncation(reception);
  const labelOf = (e: DiReceptionEntry) =>
    reception.categories.find((c) => c.category === e.category)?.label ?? e.category;

  const onKeyDown = (index: number) => (event: React.KeyboardEvent<HTMLLIElement>) => {
    // Keys pressed on a button or summary inside the entry stay theirs.
    if (event.target !== event.currentTarget) return;
    const next = nextEntryIndex(index, event.key, visible.length);
    if (next === null) return;
    event.preventDefault();
    itemRefs.current[next]?.focus();
  };

  return (
    <section
      aria-labelledby="di-reception-heading"
      className="flex flex-col gap-2 border-t-2 border-border pt-4 shrink-0"
      data-testid="di-reception"
    >
      {/* tabIndex -1: "Go to the desk" moves keyboard focus here, not only the scroll. */}
      <h2 id="di-reception-heading" className="text-sm" tabIndex={-1}>
        Reception desk: needs attention
      </h2>
      {!compact && (
        <div className="text-text-muted">
          {`Read-only · contract version ${reception.contractVersion} · ${reception.refreshStatement}`}
        </div>
      )}
      {reception.notices.map((n) => (
        <div key={n.reason} className="text-warning break-all" data-testid="di-reception-notice">
          {`${RECEPTION_GLYPH.unavailable} ${n.statement} (${n.reason})`}
        </div>
      ))}
      {compact ? (
        <details data-testid="di-reception-about">
          <summary className={`cursor-pointer text-text-muted ${FOCUS}`}>
            {`Categories (${reception.categories.length}) and what this desk can read`}
          </summary>
          <div className="flex flex-col gap-2 pl-8">
            <div className="text-text-muted">
              {`Read-only · contract version ${reception.contractVersion} · ${reception.refreshStatement}`}
            </div>
            <Categories reception={reception} />
            <div className="text-text-muted">{reception.dismissalStatement}</div>
          </div>
        </details>
      ) : (
        <Categories reception={reception} />
      )}
      {visible.length === 0 ? (
        <div className="text-text-muted" data-testid="di-reception-no-entries">
          {reception.entries.length === 0
            ? 'No entries to show. Read each category above: an empty list is not a "none".'
            : 'Every entry is hidden in this view.'}
        </div>
      ) : (
        <ul
          className="flex flex-col gap-2 max-h-260 overflow-y-auto"
          aria-label="Attention entries (arrow keys move between entries)"
          data-testid="di-reception-entries"
        >
          {visible.map((entry, i) => (
            <li
              key={hideKey(entry)}
              ref={(el) => {
                itemRefs.current[i] = el;
              }}
              tabIndex={0}
              aria-keyshortcuts="ArrowUp ArrowDown Home End"
              aria-label={`${labelOf(entry)}: ${entry.missionRef}`}
              className={`flex flex-col gap-1 border-2 border-border p-4 ${FOCUS}`}
              onKeyDown={onKeyDown(i)}
              data-testid="di-reception-entry"
              data-entry-key={entry.key}
            >
              <Entry
                entry={entry}
                categoryLabel={labelOf(entry)}
                onHide={onHide}
                onJump={onJump}
                fixture={fixture}
                compact={compact}
              />
            </li>
          ))}
        </ul>
      )}
      {truncation && (
        <div className="text-warning" data-testid="di-reception-truncated">
          {truncation}
        </div>
      )}
      {hiddenCount > 0 && (
        <div className="flex items-center gap-6" data-testid="di-reception-hidden">
          <span className="text-text-muted">{`${hiddenCount} hidden in this view only`}</span>
          <button type="button" className={`cursor-pointer ${FOCUS}`} onClick={onShowHidden}>
            {`Show ${hiddenCount} hidden`}
          </button>
        </div>
      )}
      {!compact && <div className="text-text-muted">{reception.dismissalStatement}</div>}
    </section>
  );
}
