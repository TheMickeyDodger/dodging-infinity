/**
 * The rail's pages on the scan face (Stage 2, pass B): the Missions table (04-missions.png), the full-page Reception
 * (03-reception.png with gap 8), New Mission (Figure 4.3a) and the proposal (Figure 4.3b).
 *
 * Presentation and navigation work: filters, tabs, opening a Mission, Clear info, Show, typing an ask, picking a recent
 * ask. Every authority-dependent control is drawn DISABLED with its reason (a visible title and a spoken description);
 * Stage 4 wires it to an existing human-gated route. Nothing here sends a message.
 */
import './diScanPages.css';

import { type ReactNode, useId, useMemo, useState } from 'react';

import type {
  DiScanDeclaredPhase,
  DiScanMissionsRead,
  DiScanPhase,
  DiScanRow,
  DiScanTone,
} from './diScanModel.js';
import {
  declaredPhaseWords,
  DI_DECIDE_LABEL,
  DI_DECIDE_WHAT,
  DI_REJECT_IN_PANEL,
  DI_REQUEST_CHANGES_IN_PANEL,
} from './diScanModel.js';
import type {
  DiMissionsStatus,
  DiNewMissionSource,
  DiProposalSpecimen,
  DiReceptionCard,
  DiScanReception,
} from './diScanPagesModel.js';
import {
  askHandoff,
  continueAskReason,
  DI_ASK_CONTINUE_HINT,
  DI_ASK_CONTINUE_LABEL,
  DI_ASK_REFUSED,
  DI_EARLIER_NOT_READ,
  DI_HARNESS_FULL,
  DI_LAYOUT_NOTE,
  DI_MISSIONS_EMPTY,
  DI_MISSIONS_FOOTER,
  DI_MISSIONS_STATUSES,
  DI_ORCHESTRATOR_LIMIT,
  DI_RECENT_NOT_READ,
  DI_TEAM_UNAVAILABLE,
  missionsShown,
  missionsUnknown,
  receptionHeadline,
  runningCount,
} from './diScanPagesModel.js';

const NOT_RECORDED = 'Not recorded';
/** What an unknown Mission may do, per filter (only these filters read a fact that can be unknown). */
const MAY: Readonly<Record<DiMissionsStatus, string>> = {
  all: 'match',
  needs: 'need you',
  stalled: 'be stalled',
  running: 'be running',
  done: 'be done',
  archived: 'be archived',
};
/** A reason as a sentence: capitalised, with its full stop. */
const sentence = (text: string) => {
  const s = text.charAt(0).toUpperCase() + text.slice(1);
  return /[.!?]$/.test(s) ? s : `${s}.`;
};
const SAMPLE_DATA = 'Sample data (fixture)';
/** Reasons for the controls drawn but not connected in this layout stage (each owed by Stage 4). */
const REASON = {
  decide: 'Deciding a proposal is not connected in this layout preview.',
  answer: 'Answering a Mission is not connected in this layout preview.',
  recover:
    'Recover is not connected in this layout preview. Recover restarts the work only; it never approves, dispatches or delivers.',
  acceptCi: 'Accepting CI results is not connected in this layout preview.',
  logs: 'Logs and checks are not read in this layout preview.',
  addRepo: 'Choosing repos is not connected in this layout preview.',
  addFiles: 'Attaching files is not connected in this layout preview.',
  notListed: 'This Mission is not listed in this office.',
  noWorkspace: 'Mission requests are not available in this office',
} as const;

/** A status said as a round dot AND its word (V-1, AC-UX-10). */
export function StatusTag({
  label,
  tone,
  testid,
}: {
  label: string;
  tone: DiScanTone;
  testid?: string;
}) {
  return (
    <span className="di-page-tag" data-tone={tone} data-testid={testid}>
      <span className="di-scan-dot" data-tone={tone} aria-hidden="true" />
      {label}
    </span>
  );
}

/** A control drawn but not connected: disabled, its reason on hover AND spoken (aria-describedby), never a stub that looks live. */
function Off({
  testid,
  label,
  reason,
  className = 'di-page-button',
}: {
  testid: string;
  label: string;
  reason: string;
  className?: string;
}) {
  const id = useId();
  return (
    <>
      <button
        type="button"
        className={className}
        data-testid={testid}
        disabled
        title={reason}
        aria-describedby={id}
      >
        {label}
      </button>
      <span className="di-scan-visually-hidden" id={id}>
        {reason}
      </span>
    </>
  );
}

function LockIcon() {
  return (
    <svg width="12" height="12" viewBox="0 0 12 12" aria-hidden="true" className="di-page-lock">
      <rect x="2" y="5.5" width="8" height="5.5" rx="1" fill="none" stroke="currentColor" />
      <path d="M4 5.5V4a2 2 0 0 1 4 0v1.5" fill="none" stroke="currentColor" />
    </svg>
  );
}

function BackIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 16 16" aria-hidden="true">
      <path d="M10 3 5 8l5 5" fill="none" stroke="currentColor" strokeWidth="1.6" />
    </svg>
  );
}

function PageHead({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <header className="di-page-head">
      <h1 className="di-page-title" tabIndex={-1} data-page-heading="true">
        {title}
      </h1>
      {children}
    </header>
  );
}

function FixtureTag({ text = SAMPLE_DATA }: { text?: string }) {
  return <span className="di-scan-fixture-tag">{text}</span>;
}

// ── 04 Missions ──────────────────────────────────────────────────────────────────────────────────

function Phase({ phase }: { phase: DiScanPhase }) {
  const state = (i: number) =>
    phase.finished || i < phase.step ? 'done' : i === phase.step ? 'current' : 'todo';
  return (
    <span className="di-phase">
      <span className="di-phase-word">{phase.word}</span>
      <span className="di-phase-bar" aria-hidden="true">
        {[1, 2, 3, 4].map((i) => (
          <span key={i} className="di-phase-seg" data-state={state(i)} />
        ))}
      </span>
      <span className="di-scan-visually-hidden">
        {phase.finished ? 'finished' : `step ${phase.step} of 4`}
      </span>
    </span>
  );
}

/**
 * A real row's Phase: the producer's DECLARED phase as its own word, with NO step marks (no document maps its ten phases onto the four-step bar). The kinds that are not a phase say what they are, each in its own
 * visible words and with the sentence that says what it means in text a person who cannot hover can read: this kind of record has no phase, the producer reported none, or this office does not provide one.
 */
function DeclaredPhase({ declared }: { declared: DiScanDeclaredPhase }) {
  const { text, why } = declaredPhaseWords(declared);
  return (
    <span className="di-phase-declared" data-testid="di-phase-declared" data-kind={declared.kind} title={why ?? undefined}>
      <span className="di-phase-word">{text}</span>
      {why !== null && <span className="di-scan-visually-hidden">{` ${why}`}</span>}
    </span>
  );
}

function reposText(row: DiScanRow): string {
  const repos = row.table?.repos ?? null;
  if (repos === null) return NOT_RECORDED;
  return repos.targets > 0 ? `${repos.harness} + ${repos.targets}` : repos.harness;
}

/**
 * 04 Missions: the ownership SCOPE (Mine; Team is Paid and unavailable), the STATUS filters with their counts, the table
 * Mission / Owner / Repos / Phase / Status / Last, and the archive note. A row opens Mission details.
 */
export function MissionsPage({
  rows,
  specimen,
  read,
  onOpenMission,
}: {
  rows: readonly DiScanRow[];
  specimen: boolean;
  /** Whether the office read its Missions: unread or unavailable is said as such, never as "No Missions yet". */
  read: DiScanMissionsRead;
  onOpenMission: (id: string) => void;
}) {
  const [status, setStatus] = useState<DiMissionsStatus>('all');
  const teamReason = useId();
  // Per-filter uncertainty (round 5): a filter shows the Missions KNOWN to match, and says how many more MAY match and why.
  // Its chip is a count only when nothing is unknown; "N+" when some are known and more may be; otherwise the reason's
  // own word. An unread list has no counts at all (the same words as the list: 'not read' or 'unavailable').
  const countWord = (id: DiMissionsStatus): string | number => {
    if (read.kind !== 'read') return read.kind === 'not_read' ? 'not read' : 'unavailable';
    const knownMatches = missionsShown(rows, id).length;
    const unknown = missionsUnknown(rows, id);
    if (unknown.count === 0) return knownMatches;
    if (knownMatches > 0) return `${knownMatches}+`;
    return unknown.kind === 'not_read' ? 'not read' : 'not known';
  };
  const shown = useMemo(() => missionsShown(rows, status), [rows, status]);
  const unknown = useMemo(() => missionsUnknown(rows, status), [rows, status]);
  return (
    <div className="di-page di-missions" data-testid="di-missions-page">
      <PageHead title="Missions">
        {specimen && <FixtureTag text="Sample Missions (fixture)" />}
      </PageHead>
      <div className="di-missions-filters">
        <div className="di-chip-group" role="group" aria-label="Whose Missions">
          <button
            type="button"
            className="di-chip"
            data-testid="di-missions-scope-mine"
            aria-pressed="true"
          >
            Mine
          </button>
          <button
            type="button"
            className="di-chip"
            data-testid="di-missions-scope-team"
            disabled
            title={DI_TEAM_UNAVAILABLE}
            aria-describedby={teamReason}
          >
            Team
          </button>
          <span className="di-scan-visually-hidden" id={teamReason}>
            {DI_TEAM_UNAVAILABLE}
          </span>
        </div>
        <div className="di-chip-group" role="group" aria-label="Status">
          {DI_MISSIONS_STATUSES.map((item) => (
            <button
              key={item.id}
              type="button"
              className="di-chip"
              data-testid={`di-missions-status-${item.id}`}
              aria-pressed={status === item.id}
              onClick={() => setStatus(item.id)}
            >
              {item.label}
              <span className="di-chip-count" data-testid="di-missions-count">
                {countWord(item.id)}
              </span>
            </button>
          ))}
        </div>
      </div>
      {read.kind !== 'read' ? (
        <p className="di-page-empty" data-testid="di-missions-not-read" data-read={read.kind}>
          {read.reason}
        </p>
      ) : rows.length === 0 ? (
        <p className="di-page-empty" data-testid="di-missions-empty">
          {DI_MISSIONS_EMPTY}
        </p>
      ) : shown.length === 0 && unknown.count === 0 ? (
        // the one case that may say "none": every Mission's fact is KNOWN and none matches
        <p className="di-page-empty">No Missions match this filter.</p>
      ) : shown.length === 0 ? null : (
        <table className="di-missions-table">
          <thead>
            <tr>
              <th scope="col">Mission</th>
              <th scope="col">Owner</th>
              <th scope="col">Repos</th>
              <th scope="col">Phase</th>
              <th scope="col">Status</th>
              <th scope="col">Last</th>
            </tr>
          </thead>
          <tbody>
            {shown.map((row) => {
              const pill = row.pills[0];
              const phase = row.table?.phase ?? null;
              const declared = row.table?.declared ?? null;
              return (
                <tr
                  key={row.id}
                  className="di-missions-row"
                  data-testid="di-missions-row"
                  data-mission-ref={row.id}
                  onClick={() => onOpenMission(row.id)}
                >
                  <td>
                    <button
                      type="button"
                      className="di-missions-open"
                      data-testid="di-missions-open"
                      onClick={(event) => {
                        event.stopPropagation();
                        onOpenMission(row.id);
                      }}
                    >
                      <span className="di-missions-title">{row.title}</span>
                      <span className="di-missions-step">{row.context}</span>
                    </button>
                  </td>
                  <td>{row.table?.owner ?? NOT_RECORDED}</td>
                  <td>{reposText(row)}</td>
                  <td>
                    {phase !== null ? (
                      <Phase phase={phase} />
                    ) : declared !== null ? (
                      <DeclaredPhase declared={declared} />
                    ) : (
                      NOT_RECORDED
                    )}
                  </td>
                  <td>
                    {pill ? (
                      <StatusTag label={pill.label} tone={pill.tone} />
                    ) : (
                      <StatusTag label="Status not recorded" tone="idle" />
                    )}
                  </td>
                  <td className="di-missions-last" data-testid="di-missions-last">
                    {row.table?.last ?? NOT_RECORDED}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      )}
      {read.kind === 'read' && unknown.count > 0 && (
        <p
          className="di-page-empty"
          data-testid="di-missions-unknown"
          data-kind={unknown.kind}
          // the server's reason codes, kept for diagnostics; the text says it in plain words
          data-reason-codes={unknown.codes.join(' ')}
        >
          {`${unknown.count} ${shown.length > 0 ? 'more ' : ''}${unknown.count === 1 ? 'Mission' : 'Missions'} may ${MAY[status]}. ${unknown.reasons.map(sentence).join(' ')}`}
        </p>
      )}
      <p className="di-page-foot" data-testid="di-missions-footer">
        {DI_MISSIONS_FOOTER}
      </p>
    </div>
  );
}

// ── 03 Reception ─────────────────────────────────────────────────────────────────────────────────

function CardControls({
  card,
  listed,
  onOpenDelivery,
  onOpenRequest,
}: {
  card: DiReceptionCard;
  listed: boolean;
  onOpenDelivery: (id: string) => void;
  onOpenRequest?: (card: DiReceptionCard, opener: HTMLElement) => void;
}) {
  const replyReason = useId();
  switch (card.kind) {
    case 'stalled':
      return (
        <>
          <Off testid="di-reception-view-log" label="View log" reason={REASON.logs} />
          <Off
            testid="di-reception-recover"
            label="Recover"
            reason={REASON.recover}
            className="di-page-button di-page-primary"
          />
        </>
      );
    case 'proposal':
      // R4: a request-panel proposal, where its workspace can be opened, offers NAVIGATION into the panel's own decision (the exact approval or, by owner decision (c), a decline, rejection or
      // change request); Request changes and Reject are taken there and say so. A proposal with no request-panel request has nothing to navigate to and is drawn as before.
      if (card.request?.requestRef && onOpenRequest)
        return (
          <>
            <Off testid="di-reception-reject" label="Reject" reason={DI_REJECT_IN_PANEL} />
            <Off
              testid="di-reception-request-changes"
              label="Request changes"
              reason={DI_REQUEST_CHANGES_IN_PANEL}
            />
            <button
              type="button"
              className="di-page-button di-page-primary"
              data-testid="di-reception-decide"
              title={DI_DECIDE_WHAT}
              onClick={(event) => onOpenRequest(card, event.currentTarget)}
            >
              {DI_DECIDE_LABEL}
            </button>
          </>
        );
      return (
        <>
          <Off
            testid="di-reception-reject"
            label="Reject"
            reason={`${REASON.decide} Reject asks you to confirm, and ends the Mission.`}
          />
          <Off
            testid="di-reception-request-changes"
            label="Request changes"
            reason={`${REASON.decide} Request changes asks for a note. The old proposal can no longer be approved.`}
          />
          <Off
            testid="di-reception-approve"
            label="Approve"
            reason={`${REASON.decide} Approve starts the work exactly as proposed.`}
            className="di-page-button di-page-primary"
          />
        </>
      );
    case 'question':
      return (
        <div className="di-reception-answer">
          {card.suggestions.length > 0 && (
            <span className="di-reception-suggestions">
              {card.suggestions.map((answer) => (
                <Off
                  key={answer}
                  testid="di-reception-suggestion"
                  label={answer}
                  reason={REASON.answer}
                />
              ))}
            </span>
          )}
          <span className="di-reception-reply">
            <input
              type="text"
              className="di-page-input"
              data-testid="di-reception-reply"
              aria-label="Reply"
              placeholder="Reply"
              disabled
              title={REASON.answer}
              aria-describedby={replyReason}
            />
            <Off
              testid="di-reception-send"
              label="Send"
              reason={REASON.answer}
              className="di-page-button di-page-primary"
            />
            <span className="di-scan-visually-hidden" id={replyReason}>
              {REASON.answer}
            </span>
          </span>
        </div>
      );
    case 'ci':
      return (
        <>
          <Off testid="di-reception-view-checks" label="View checks" reason={REASON.logs} />
          <Off
            testid="di-reception-accept-ci"
            label="Accept CI results"
            reason={REASON.acceptCi}
            className="di-page-button di-page-primary"
          />
        </>
      );
    case 'delivery':
      // Reception never merges or delivers (AC-4.7-05/-27): it opens the Mission, where Delivery lives (gap 2).
      return listed && card.missionRef !== null ? (
        <button
          type="button"
          className="di-page-button di-page-primary"
          data-testid="di-reception-open-delivery"
          data-mission-ref={card.missionRef}
          onClick={() => onOpenDelivery(card.missionRef as string)}
        >
          Open Mission
        </button>
      ) : (
        <Off testid="di-reception-open-delivery" label="Open Mission" reason={REASON.notListed} />
      );
    case 'other':
      return card.request !== null && onOpenRequest ? (
        <button
          type="button"
          className="di-page-button"
          data-testid="di-reception-open-request"
          title="Opens the existing request workspace on this item. Nothing is sent or decided by opening it."
          onClick={(event) => onOpenRequest(card, event.currentTarget)}
        >
          Open the request workspace
        </button>
      ) : null;
    case 'info':
      return null;
  }
}

function ReceptionCard({
  card,
  rows,
  readOnly = false,
  onOpenMission,
  onOpenDelivery,
  onOpenRequest,
}: {
  card: DiReceptionCard;
  rows: readonly DiScanRow[];
  readOnly?: boolean;
  onOpenMission: (id: string) => void;
  onOpenDelivery: (id: string) => void;
  onOpenRequest?: (card: DiReceptionCard, opener: HTMLElement) => void;
}) {
  const listed = card.missionRef !== null && rows.some((row) => row.id === card.missionRef);
  return (
    <li
      className="di-reception-card"
      data-testid="di-reception-card"
      data-kind={card.kind}
      data-entry-key={card.key}
    >
      <div className="di-reception-card-main">
        <div className="di-reception-card-top">
          <StatusTag label={card.tag.label} tone={card.tag.tone} />
          {listed && !readOnly ? (
            <button
              type="button"
              className="di-reception-title"
              data-testid="di-reception-open-mission"
              data-mission-ref={card.missionRef ?? undefined}
              onClick={() => onOpenMission(card.missionRef as string)}
            >
              {card.title}
            </button>
          ) : (
            <span className="di-reception-title">{card.title}</span>
          )}
        </div>
        <p className="di-reception-need">{card.need}</p>
        <p className="di-reception-meta">{card.meta}</p>
        {card.note !== '' && <p className="di-reception-note">{card.note}</p>}
        {/* R4: what the navigation opens, and which decisions can be recorded there, as visible text (not only a title) */}
        {!readOnly && card.kind === 'proposal' && card.request?.requestRef && onOpenRequest && (
          <p className="di-reception-note" data-testid="di-reception-decide-note">
            {DI_DECIDE_WHAT}
          </p>
        )}
      </div>
      {!readOnly && (
        <div className="di-reception-actions">
          <CardControls
            card={card}
            listed={listed}
            onOpenDelivery={onOpenDelivery}
            onOpenRequest={onOpenRequest}
          />
        </div>
      )}
    </li>
  );
}

/**
 * 03 Reception, the scan face's ONE Reception (lead Q2, D-10): Needs you / All, the count the rail badge also reads, the
 * items with the controls drawn for their type, Clear info for information items only, and Earlier today.
 */
export function ReceptionPage({
  reception,
  rows,
  onOpenMission,
  onOpenDelivery,
  onOpenRequest,
}: {
  reception: DiScanReception;
  rows: readonly DiScanRow[];
  onOpenMission: (id: string) => void;
  onOpenDelivery: (id: string) => void;
  onOpenRequest?: (card: DiReceptionCard, opener: HTMLElement) => void;
}) {
  const [filter, setFilter] = useState<'needs' | 'all'>('needs');
  // Clear info: a preference of THIS view only, for information items only. Never persisted, never sent; an action item
  // has no clear control at all (AC-4.7-06), so the count the header and the badge read never moves.
  const [cleared, setCleared] = useState<ReadonlySet<string>>(() => new Set());
  const [showEarlier, setShowEarlier] = useState(false);
  const info = reception.information.filter((card) => !cleared.has(card.key));
  const fixture = reception.source === 'fixture';
  const infoHeading = useId();
  const shared = { rows, onOpenMission, onOpenDelivery, onOpenRequest };
  return (
    <div className="di-page di-reception" data-testid="di-reception-page">
      <PageHead title="Reception">
        <span className="di-page-count" data-testid="di-reception-count">
          <span
            className="di-scan-dot"
            data-tone={reception.attention > 0 ? 'needs' : 'idle'}
            aria-hidden="true"
          />
          {receptionHeadline(reception.attention, reception.attentionDoubt)}
        </span>
        {fixture && <FixtureTag />}
      </PageHead>
      {reception.attentionDoubt !== null && (
        <p
          className="di-page-note"
          data-testid="di-reception-doubt"
          // the server's reason codes, kept for diagnostics; the text says it in plain words
          data-reason-codes={reception.attentionDoubt.codes.join(' ')}
        >
          {reception.attentionDoubt.text}
        </p>
      )}
      <div className="di-chip-group" role="group" aria-label="Show">
        <button
          type="button"
          className="di-chip"
          data-testid="di-reception-filter-needs"
          aria-pressed={filter === 'needs'}
          onClick={() => setFilter('needs')}
        >
          Needs you
        </button>
        <button
          type="button"
          className="di-chip"
          data-testid="di-reception-filter-all"
          aria-pressed={filter === 'all'}
          onClick={() => setFilter('all')}
        >
          All
        </button>
      </div>
      <p className="di-page-note">{DI_LAYOUT_NOTE}</p>
      {reception.cards.length === 0 ? (
        <div className="di-page-empty" data-testid="di-reception-empty">
          {reception.empty.map((line) => (
            <p key={line}>{line}</p>
          ))}
        </div>
      ) : (
        <ul className="di-reception-list" aria-label="Waiting on you">
          {reception.cards.map((card) => (
            <ReceptionCard key={card.key} card={card} {...shared} />
          ))}
        </ul>
      )}
      {reception.notListed > 0 && (
        <p className="di-page-note" data-testid="di-reception-not-listed">
          {`${reception.notListed} more ${reception.notListed === 1 ? 'needs' : 'need'} you but ${reception.notListed === 1 ? 'is' : 'are'} not listed here.`}
        </p>
      )}
      {filter === 'all' && (
        <section
          className="di-reception-info"
          aria-labelledby={infoHeading}
          data-testid="di-reception-info"
        >
          <div className="di-reception-section-head">
            <h2 id={infoHeading}>Information</h2>
            {info.length > 0 && (
              <button
                type="button"
                className="di-page-button"
                data-testid="di-reception-clear-info"
                title="Clears information items from this view only. Items waiting on you stay until they are handled."
                onClick={() => setCleared(new Set(reception.information.map((card) => card.key)))}
              >
                Clear info
              </button>
            )}
          </div>
          {info.length > 0 ? (
            <ul className="di-reception-list">
              {info.map((card) => (
                <ReceptionCard key={card.key} card={card} {...shared} />
              ))}
            </ul>
          ) : (
            <p className="di-page-note" data-testid="di-reception-info-none">
              {cleared.size > 0
                ? 'Information cleared in this view only.'
                : 'No information items are listed.'}
            </p>
          )}
        </section>
      )}
      <section className="di-reception-earlier" data-testid="di-reception-earlier">
        {reception.earlier === null ? (
          <p className="di-page-note" data-testid="di-reception-earlier-unread">
            {reception.earlierUnread ?? DI_EARLIER_NOT_READ}
          </p>
        ) : (
          <>
            <div className="di-reception-section-head">
              <p className="di-reception-earlier-line" data-testid="di-reception-earlier-heading">
                {reception.earlier.heading}
              </p>
              {reception.earlier.items.length > 0 && (
                <button
                  type="button"
                  className="di-page-button"
                  data-testid="di-reception-earlier-show"
                  aria-expanded={showEarlier}
                  onClick={() => setShowEarlier((open) => !open)}
                >
                  {showEarlier ? 'Show less' : 'Show'}
                </button>
              )}
            </div>
            {reception.earlier.notes.map((note) => (
              <p key={note} className="di-page-note" data-testid="di-reception-earlier-note">
                {note}
              </p>
            ))}
            {showEarlier && (
              <ul className="di-reception-list" data-testid="di-reception-earlier-list">
                {reception.earlier.items.map((card) => (
                  <ReceptionCard key={card.key} card={card} readOnly {...shared} />
                ))}
              </ul>
            )}
          </>
        )}
      </section>
      {reception.checked.length > 0 && (
        <details className="di-reception-checked">
          <summary>What was checked</summary>
          <ul>
            {reception.checked.map((line) => (
              <li key={line.label}>{`${line.label}: ${line.headline}`}</li>
            ))}
          </ul>
        </details>
      )}
    </div>
  );
}

// ── New Mission and the proposal ─────────────────────────────────────────────────────────────────

function HarnessChip({ testid }: { testid?: string }) {
  return (
    <div className="di-new-harness" data-testid={testid}>
      <LockIcon />
      <span className="di-new-harness-repo">{DI_HARNESS_FULL}</span>
      <span className="di-new-harness-tag">Harness</span>
    </div>
  );
}

/**
 * New Mission (Figure 4.3a), reached from the rail only (D-8). The ask box and Recent work. G1/G2: "Continue in the request workspace" HANDS the typed ask to the existing request workspace's Ask
 * (its one driver; the human gate stays there) and opens it; nothing is sent from here. An empty Ask draft takes the text in full; an identical one changes nothing; a different unsent draft refuses the
 * handoff VISIBLY and neither text is touched. The page's own text is never cleared. The existing workspace link stays (lead Q1).
 */
export function NewMissionPage({
  rows,
  source,
  specimen,
  onOpenRequestWorkspace,
  askDraft = '',
  onContinueAsk,
  onOpenMissions,
  onOpenProposal,
}: {
  rows: readonly DiScanRow[];
  source: DiNewMissionSource;
  specimen: boolean;
  onOpenRequestWorkspace?: (opener: HTMLElement) => void;
  /** The request workspace's unsent Ask draft, as the workspace holds it. */
  askDraft?: string;
  /** Hand the ask to the workspace's Ask (`transfer`: the text to put there, or null to change nothing) and open it there. Absent: no workspace; the control says why. */
  onContinueAsk?: (transfer: string | null, opener: HTMLElement) => void;
  onOpenMissions: () => void;
  onOpenProposal: () => void;
}) {
  const [ask, setAsk] = useState('');
  // Round 35: the refusal notice is never a latch. What is kept is the ATTEMPT, as the exact pair of texts that was refused (a real event); the notice shows only while the LIVE texts are still
  // that pair and still conflict. Any change to either text withdraws it at once, and with no attempt there is nothing to show (a conflict nobody tried to hand over is not a refusal).
  const [refusedAt, setRefusedAt] = useState<{ ask: string; draft: string } | null>(null);
  const askId = useId();
  const sendReason = useId();
  const running = runningCount(rows);
  const atLimit = running >= DI_ORCHESTRATOR_LIMIT;
  const reason = continueAskReason(ask, running, onContinueAsk !== undefined);
  const showRefusal =
    refusedAt !== null &&
    refusedAt.ask === ask &&
    refusedAt.draft === askDraft &&
    askHandoff(ask, askDraft) === 'refused';
  const continueAsk = (opener: HTMLElement) => {
    if (reason !== null || onContinueAsk === undefined) return;
    const outcome = askHandoff(ask, askDraft);
    if (outcome === 'refused') {
      // non-destructive AND legible: neither text is touched, and the page says so while the conflict lasts
      setRefusedAt({ ask, draft: askDraft });
      return;
    }
    setRefusedAt(null);
    onContinueAsk(outcome === 'transfer' ? ask : null, opener);
  };
  return (
    <div className="di-page di-new" data-testid="di-new-mission">
      <PageHead title="New Mission">{specimen && <FixtureTag />}</PageHead>
      <p className="di-page-sub">DI proposes a team and repos before anything runs.</p>
      {atLimit && (
        <div className="di-new-limit" role="status" data-testid="di-new-limit">
          <span className="di-new-limit-copy">
            <strong>{`${DI_ORCHESTRATOR_LIMIT} of ${DI_ORCHESTRATOR_LIMIT} Orchestrators running`}</strong>
            <span>Finish or stop one to start another.</span>
          </span>
          <button
            type="button"
            className="di-page-button"
            data-testid="di-new-open-missions"
            onClick={onOpenMissions}
          >
            Open Missions
          </button>
        </div>
      )}
      <section className="di-new-section">
        <h2 className="di-new-label">Always included</h2>
        <HarnessChip testid="di-new-harness" />
        <p className="di-page-note">
          Every Mission runs from here. DI adds the other repos it needs.
        </p>
      </section>
      <section className="di-new-section">
        <h2 className="di-new-label">
          Limit to repos <span className="di-new-optional">Optional</span>
        </h2>
        <div className="di-new-box">
          <span className="di-new-placeholder">Leave empty to let DI choose</span>
          <Off testid="di-new-add-repo" label="+ Add repo" reason={REASON.addRepo} />
        </div>
      </section>
      <section className="di-new-section">
        <label className="di-new-label" htmlFor={askId}>
          Ask
        </label>
        <textarea
          id={askId}
          className="di-new-ask"
          data-testid="di-new-ask"
          rows={4}
          value={ask}
          placeholder="Say what you want done, in plain words."
          // editing the ask needs no clearing step: the notice follows the live texts
          onChange={(event) => setAsk(event.target.value)}
          onKeyDown={(event) => {
            // Cmd Return (Ctrl Return) does what the control does, and adds no line break
            if (event.key === 'Enter' && (event.metaKey || event.ctrlKey)) {
              event.preventDefault();
              continueAsk(event.currentTarget);
            }
          }}
        />
      </section>
      <section className="di-new-section">
        <h2 className="di-new-label">
          Attachments <span className="di-new-optional">Optional</span>
        </h2>
        <Off testid="di-new-add-files" label="+ Add files" reason={REASON.addFiles} />
      </section>
      <section className="di-new-section" data-testid="di-new-recent">
        <h2 className="di-new-label">Recent</h2>
        {source.recent === null ? (
          <p className="di-page-note">{DI_RECENT_NOT_READ}</p>
        ) : (
          <ul className="di-new-recent">
            {source.recent.map((item) => (
              <li key={item}>
                <button
                  type="button"
                  className="di-chip"
                  data-testid="di-new-recent-ask"
                  title="Puts this ask in the box. Nothing is sent."
                  onClick={() => setAsk(item)}
                >
                  {item}
                </button>
              </li>
            ))}
          </ul>
        )}
      </section>
      <aside className="di-new-existing" aria-label="The existing request workspace">
        <p className="di-page-note">
          This page is a layout preview. Today a Mission starts in the existing request workspace.
        </p>
        {onOpenRequestWorkspace === undefined ? (
          <Off
            testid="di-new-open-workspace"
            label="Open the existing request workspace"
            reason={REASON.noWorkspace}
          />
        ) : (
          <button
            type="button"
            className="di-page-button"
            data-testid="di-new-open-workspace"
            onClick={(event) => onOpenRequestWorkspace(event.currentTarget)}
          >
            Open the existing request workspace
          </button>
        )}
        {source.proposal !== null && (
          <button
            type="button"
            className="di-page-button"
            data-testid="di-new-sample-proposal"
            onClick={onOpenProposal}
          >
            See the sample proposal
          </button>
        )}
      </aside>
      {/* a persistent live region, so the refusal is announced when it appears */}
      <div role="status" data-testid="di-new-continue-status">
        {showRefusal && (
          <p className="di-page-note" data-testid="di-new-continue-refused">
            {DI_ASK_REFUSED}
          </p>
        )}
      </div>
      <footer className="di-new-foot">
        <span className="di-page-note">
          <kbd>Cmd Return</kbd> {DI_ASK_CONTINUE_HINT}
        </span>
        <button
          type="button"
          className="di-page-button di-page-primary"
          data-testid="di-new-continue"
          disabled={reason !== null}
          title={reason ?? undefined}
          aria-describedby={reason === null ? undefined : sendReason}
          onClick={(event) => continueAsk(event.currentTarget)}
        >
          {DI_ASK_CONTINUE_LABEL}
        </button>
        {reason !== null && (
          <span className="di-scan-visually-hidden" id={sendReason}>
            {reason}
          </span>
        )}
      </footer>
    </div>
  );
}

/**
 * The proposal (Figure 4.3b), drawn from SAMPLE data: the harness, the repos DI chose, each role's exact model with NO
 * control to change it (AC-4.3-34), and Approve / Request changes / Reject, disabled with their reason.
 */
export function ProposalPage({
  proposal,
  onBack,
}: {
  proposal: DiProposalSpecimen;
  onBack: () => void;
}) {
  const heading = useId();
  return (
    <div className="di-page di-proposal" data-testid="di-proposal">
      <PageHead title={proposal.title}>
        <FixtureTag text="Sample proposal (fixture)" />
      </PageHead>
      <button
        type="button"
        className="di-page-back"
        data-testid="di-proposal-back"
        aria-label="Back to New Mission"
        onClick={onBack}
      >
        <BackIcon />
        Back
      </button>
      <p className="di-proposal-sub">
        <StatusTag label="Needs approval" tone="needs" />
        <span>{`You · ${proposal.askedAt}`}</span>
      </p>
      <blockquote className="di-proposal-ask">{proposal.ask}</blockquote>
      <section className="di-proposal-card" aria-labelledby={heading}>
        <div className="di-proposal-card-head">
          <h2 id={heading}>Proposal</h2>
          <span className="di-page-note">{proposal.proposedAt}</span>
          <StatusTag label="Needs approval" tone="needs" />
        </div>
        <p>{proposal.summary}</p>
        <h3 className="di-new-label">Team</h3>
        <table className="di-proposal-team" data-testid="di-proposal-team">
          <tbody>
            {proposal.roles.map((role) => (
              <tr key={role.role}>
                <th scope="row">{role.role}</th>
                <td>
                  <code>{role.model}</code>
                </td>
                <td className="di-proposal-provider">{`${role.provider} · ${role.effort}`}</td>
              </tr>
            ))}
          </tbody>
        </table>
        <h3 className="di-new-label">Repos</h3>
        <div className="di-proposal-repos" data-testid="di-proposal-repos">
          <span className="di-new-harness">
            <LockIcon />
            <span className="di-new-harness-repo">{proposal.harness}</span>
            <span className="di-new-harness-tag">Harness</span>
          </span>
          {proposal.targets.map((repo) => (
            <span key={repo} className="di-proposal-repo">
              <code>{repo}</code>
              <span className="di-proposal-chose">DI chose</span>
            </span>
          ))}
        </div>
        <dl className="di-proposal-scope">
          <dt>In</dt>
          <dd>{proposal.scopeIn}</dd>
          <dt>Out</dt>
          <dd>{proposal.scopeOut}</dd>
        </dl>
        <div className="di-proposal-actions">
          <Off
            testid="di-proposal-approve"
            label="Approve"
            reason={`${REASON.decide} Approve starts the work exactly as proposed.`}
            className="di-page-button di-page-primary"
          />
          <Off
            testid="di-proposal-request-changes"
            label="Request changes"
            reason={`${REASON.decide} Request changes asks for a note. The old proposal can no longer be approved.`}
          />
          <Off
            testid="di-proposal-reject"
            label="Reject"
            reason={`${REASON.decide} Reject asks you to confirm, and ends the Mission.`}
          />
        </div>
        <p className="di-page-note">{DI_LAYOUT_NOTE}</p>
      </section>
    </div>
  );
}
