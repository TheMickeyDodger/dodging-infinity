/**
 * The rail's pages on the scan face (Stage 2, pass B): the Missions table (04-missions.png), the full-page Reception
 * (03-reception.png with gap 8), and New Mission (Figure 4.3a), whose proposal (Figure 4.3b) is drawn IN PLACE.
 *
 * Presentation and navigation work: filters, tabs, opening a Mission, Clear info, Show, typing an ask, picking a recent
 * ask. Every authority-dependent control is drawn DISABLED with its reason (a visible title and a spoken description);
 * Stage 4 wires it to an existing human-gated route. The ONE message this file sends is New Mission's Send: the request
 * workspace's own read-only drafting turn, through the workspace's seam. Nothing here submits, approves, starts or delivers.
 */
import './diScanPages.css';

import { type ReactNode, type RefObject, useEffect, useId, useLayoutEffect, useMemo, useRef, useState } from 'react';

import type { DiRetainedSource } from '../../../core/src/messages.js';
import { TurnWords } from './DiMissionRequest.js';
import type { MrNotice, MrState, MrTurn } from './diMissionRequestModel.js';
import { isPending, MR_DRAFTING_NOTE, omittedWords } from './diMissionRequestModel.js';
import { Line, ProposalView } from './DiMissionRequestParts.js';
import type {
  DiScanDeclaredPhase,
  DiScanMissionsRead,
  DiScanPhase,
  DiScanRow,
  DiScanTone,
  DiScanWhy,
} from './diScanModel.js';
import {
  declaredPhaseWords,
  DI_DECIDE_LABEL,
  DI_DECIDE_WHAT,
  DI_REJECT_IN_PANEL,
  DI_REQUEST_CHANGES_IN_PANEL,
  knownTrue,
  rowContextLine,
  statusPillOf,
} from './diScanModel.js';
import type { DiArchiveReading, DiDraftStanding } from './diScanPagesModel.js';
import type {
  DiAskPanel,
  DiMissionsStatus,
  DiNewMissionSource,
  DiProposalSpecimen,
  DiReceptionCard,
  DiScanReception,
} from './diScanPagesModel.js';
import {
  archiveCount,
  archiveReading,
  atOrchestratorLimit,
  DI_ASK_BRING_BACK,
  DI_ASK_IN_WORKSPACE_NOTE,
  DI_ASK_LEAVE_UNREAD,
  DI_ASK_SEND_HINT,
  DI_ASK_SEND_LABEL,
  DI_ASK_UNAVAILABLE,
  DI_DRAFT_OPEN_LABEL,
  DI_DRAFT_SOURCE_SUBMITTED,
  DI_DRAFT_SOURCE_UNKNOWN,
  DI_EARLIER_NOT_READ,
  DI_HARNESS_FULL,
  DI_LAYOUT_NOTE,
  DI_MISSIONS_FOOTER,
  DI_MISSIONS_STATUSES,
  DI_NEW_CLOSE_CONVERSATION,
  DI_NEW_CLOSE_CONVERSATION_NOTE,
  DI_NEW_MISSION_SUB,
  DI_NEW_READ_AGAIN,
  DI_READ_EMPTY,
  DI_READ_LAST_HAD,
  DI_READ_LAST_READ,
  DI_READ_NOTHING_SHOWN,
  DI_RECENT_NONE,
  DI_TEAM_UNAVAILABLE,
  draftNote,
  draftRunnableWords,
  draftStanding,
  missionsEmptyState,
  missionsEmptyWords,
  missionsShown,
  missionsUnknown,
  orchestratorLimitWords,
  orchestratorUse,
  pageOutcomes,
  recentAsks,
  receptionHeadline,
  sendAskReason,
  sendOutcomeWords,
} from './diScanPagesModel.js';
import { useStickyScrollPad } from './diStickyPad.js';

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
  decide: 'Deciding a proposal needs the request workspace. This office does not decide it here.',
  answer:
    'Answering a Mission needs the request workspace. This office does not send an answer from here.',
  recover:
    'Recover needs a restart route that is not connected in this office. Recover restarts the work only; it never approves, dispatches or delivers.',
  acceptCi: 'Accepting CI results is not connected in this office.',
  logs: 'Logs and checks are not reported by this catalog.',
  addRepo: 'Choosing repos is not connected in this office.',
  addFiles: 'Attaching files is not connected in this office.',
  notListed: 'This Mission is not listed in this office.',
  noWorkspace: 'Mission requests are not available in this office',
  // review round 2 sweep (F1's neighbour): configured, but not available NOW (Send's hold says the same)
  workspaceNotNow: 'Mission requests are not available now',
} as const;

/** A status said as a round dot AND its word (V-1, AC-UX-10). */
/**
 * A status's mark beside its word: a Mission status's canonical GLYPH (its non-colour mark, the same on every surface), else the tone's dot (attention,
 * and marks of other vocabularies). The glyph is aria-hidden on purpose: the word beside it is the status's name for assistive technology, and the
 * glyph adds nothing to it but its shape (a screen reader would only read the shape's Unicode name ahead of the word).
 */
export function StatusMark({ tone, glyph }: { tone: DiScanTone; glyph?: string }) {
  return glyph !== undefined ? (
    <span className="di-scan-glyph" data-tone={tone} data-testid="di-status-glyph" aria-hidden="true">
      {glyph}
    </span>
  ) : (
    <span className="di-scan-dot" data-tone={tone} aria-hidden="true" />
  );
}

export function StatusTag({
  label,
  tone,
  glyph,
  testid,
}: {
  label: string;
  tone: DiScanTone;
  /** A Mission status's canonical glyph (DiScanPill.glyph); without it, the tone's dot. */
  glyph?: string;
  testid?: string;
}) {
  return (
    <span className="di-page-tag" data-tone={tone} data-testid={testid}>
      <StatusMark tone={tone} glyph={glyph} />
      {label}
    </span>
  );
}

/** Why "Read this conversation again" is held: a read of THAT conversation is already in flight. A press sends nothing until it settles. */
const READ_AGAIN_IN_FLIGHT_REASON =
  'This conversation is already being read. Read this conversation again once that answer arrives.';

function conversationReadPending(
  pending: MrState['pending'] | undefined,
  conversationId: string,
): boolean {
  return pending?.some((item) => item.op === 'conversation' && item.conversationId === conversationId) === true;
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

function PageHead({ title, children }: { title: string; children?: ReactNode }) {
  // R22: the head is sticky inside the stage, which is the scroller wherever the page scrolls as one column. Its strip is reserved as the stage's
  // scroll-padding-top (diStickyPad.ts, the one home for it), so an arrival or a focused control is scrolled CLEAR of the head, never under it.
  const headRef = useRef<HTMLElement | null>(null);
  const stageRef = useRef<HTMLElement | null>(null);
  useLayoutEffect(() => {
    stageRef.current = headRef.current?.closest<HTMLElement>('.di-scan-stage') ?? null;
  });
  useStickyScrollPad(headRef, stageRef, true);
  return (
    <header ref={headRef} className="di-page-head">
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
    <span
      className="di-phase-declared"
      data-testid="di-phase-declared"
      data-kind={declared.kind}
      title={why ?? undefined}
    >
      <span className="di-phase-word">{text}</span>
      {why !== null && <span className="di-scan-visually-hidden">{` ${why}`}</span>}
    </span>
  );
}

/** A count chip's word when none is known, one per kind of reason (kinds are never merged: stale is not "not known"). */
const WHY_WORD: Readonly<Record<DiScanWhy['kind'], string>> = {
  not_read: 'not read',
  not_known: 'not known',
  stale: 'stale',
  unrecognised: 'unrecognised',
  unavailable: 'unavailable',
};
/** Said in the Repos cell where a row's target-repo count is not known (AC-4.4-13): never a zero. */
export const DI_TARGETS_NOT_REPORTED = 'targets not reported';

/** The harness repo and how many target repos the Mission adds (AC-4.4-13). An unknown count is said as such, in text a person who cannot hover can read. */
function reposText(row: DiScanRow): string {
  const repos = row.table?.repos ?? null;
  if (repos === null) return NOT_RECORDED;
  if (!repos.targets.known) return `${repos.harness} + ${DI_TARGETS_NOT_REPORTED}`;
  return repos.targets.value > 0 ? `${repos.harness} + ${repos.targets.value}` : repos.harness;
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
  retained = null,
  initialStatus = 'all',
}: {
  rows: readonly DiScanRow[];
  specimen: boolean;
  /** Whether the office read its Missions: unread or unavailable is said as such, never as "No Missions yet". */
  read: DiScanMissionsRead;
  onOpenMission: (id: string) => void;
  /** Deliverable 2: one archive reading per retention source (null: not reported). */
  retained?: readonly DiRetainedSource[] | null;
  /** The filter shown first (the product starts on All). */
  initialStatus?: DiMissionsStatus;
}) {
  const [status, setStatus] = useState<DiMissionsStatus>(initialStatus);
  const teamReason = useId();
  // Per-filter uncertainty (round 5): a filter shows the Missions KNOWN to match, and says how many more MAY match and why.
  // Its chip is a count only when nothing is unknown; "N+" when some are known and more may be; otherwise the reason's
  // own word. An unread list has no counts at all (the same words as the list: 'not read' or 'unavailable').
  // Deliverable 2: the archive, read once: retained-only Missions (never counted twice), exact counts, and what could not be read
  const archive = useMemo(() => archiveReading(retained, rows), [retained, rows]);
  const countWord = (id: DiMissionsStatus): string | number => {
    if (read.kind !== 'read') return read.kind === 'not_read' ? 'not read' : 'unavailable';
    const unknown = missionsUnknown(rows, id);
    if (id === 'archived') {
      // the listed archived Missions plus those gone from their store; an archive not read in full never reads as a confident number
      const count = archiveCount(archive);
      // a floor (`at_least`) is added only to be said with a plus; an unreported archive adds nothing and is said as not reported
      const knownMatches = missionsShown(rows, id).length + (count.kind === 'not_reported' ? 0 : count.count);
      const uncertain = unknown.count > 0 || count.kind !== 'exact';
      if (!uncertain) return knownMatches;
      if (knownMatches > 0) return `${knownMatches}+`;
      return unknown.count > 0 ? WHY_WORD[unknown.kind] : count.kind === 'at_least' ? 'unavailable' : 'not reported';
    }
    const knownMatches = missionsShown(rows, id).length;
    if (unknown.count === 0) return knownMatches;
    if (knownMatches > 0) return `${knownMatches}+`;
    return WHY_WORD[unknown.kind];
  };
  const archiveTally = archiveCount(archive);
  // the archive may hold Missions: a count above 0, or a source that could not be read (what it holds is not known)
  const archiveHolds = archiveTally.kind === 'at_least' || (archiveTally.kind === 'exact' && archiveTally.count > 0);
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
      ) : rows.length === 0 && !(status === 'archived' && missionsEmptyState(archive).kind === 'archive_only') ? (
        // round 10: an EMPTY listed store says what the archive's own state allows: "No Missions yet" only when it was read in full and holds none
        <p className="di-page-empty" data-testid="di-missions-empty" data-kind={missionsEmptyState(archive).kind}>
          {missionsEmptyWords(missionsEmptyState(archive))}
        </p>
      ) : shown.length === 0 && unknown.count === 0 && (status !== 'archived' || (archive.rows.length === 0 && archive.reported && !archiveHolds)) ? (
        // the one case that may say "none": every Mission's fact is KNOWN and none matches (for Archived: the archive was read in full, and holds none)
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
              // the canonical LIFECYCLE status, never Reception's attention in its place (round 3, finding 2); attention is said beside the title
              const pill = statusPillOf(row);
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
                      <span className="di-missions-step">{rowContextLine(row)}</span>
                    </button>
                    {knownTrue(row.needsYou) && (
                      <StatusTag label="Needs you" tone="needs" testid="di-missions-attention" />
                    )}
                  </td>
                  {/* data-label: a narrow stage stacks each row, and names each fact with its column's own word (diScanPages.css) */}
                  <td data-label="Owner">{row.table?.owner ?? NOT_RECORDED}</td>
                  <td data-label="Repos">{reposText(row)}</td>
                  <td data-label="Phase">
                    {phase !== null ? (
                      <Phase phase={phase} />
                    ) : declared !== null ? (
                      <DeclaredPhase declared={declared} />
                    ) : (
                      NOT_RECORDED
                    )}
                  </td>
                  <td data-label="Status">
                    {pill ? (
                      <StatusTag label={pill.label} tone={pill.tone} glyph={pill.glyph} />
                    ) : (
                      <StatusTag label="Status not recorded" tone="idle" />
                    )}
                  </td>
                  <td className="di-missions-last" data-testid="di-missions-last" data-label="Last">
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
      {status === 'archived' && read.kind === 'read' && <RetainedArchive archive={archive} />}
      <p className="di-page-foot" data-testid="di-missions-footer">
        {DI_MISSIONS_FOOTER}
      </p>
    </div>
  );
}

/**
 * Deliverable 2: the retained-only archive, READ-ONLY. Each Mission gone from its store shows its final status in the same closed vocabulary (chip, glyph and
 * meaning) it had while listed, its recorded final state as provenance, and its verdict, CI results and comments each with its honest state. Every control a listed
 * Mission has is drawn disabled with why. What could not be read is said, per source, with its reason; nothing here is ever a confident zero over an unknown.
 */
function RetainedArchive({ archive }: { archive: DiArchiveReading }) {
  return (
    <section className="di-missions-retained" data-testid="di-missions-retained" aria-label="Missions no longer in their store">
      {!archive.reported && (
        <p className="di-page-note" data-testid="di-missions-archive-not-reported">
          This office does not report Missions gone from their store, so any it keeps are not shown or counted here.
        </p>
      )}
      {archive.unavailable.map((source) => (
        <p key={source.sourceId} className="di-page-note" data-testid="di-missions-archive-unavailable" data-source={source.sourceId}>
          The archive of {source.sourceId} could not be read ({source.problem}), so the Missions gone from that store are not known here: not none.
        </p>
      ))}
      {archive.awaiting > 0 && (
        <p className="di-page-note" data-testid="di-missions-archive-awaiting">
          {archive.awaiting} {archive.awaiting === 1 ? 'Mission was' : 'Missions were'} removed from {archive.awaiting === 1 ? 'its store' : 'their stores'} and{' '}
          {archive.awaiting === 1 ? 'awaits' : 'await'} {archive.awaiting === 1 ? 'its' : 'their'} summary; capture is retried.
        </p>
      )}
      {archive.unlisted > 0 && (
        <p className="di-page-note" data-testid="di-missions-archive-unlisted">
          {archive.unlisted} more {archive.unlisted === 1 ? 'is' : 'are'} kept but not listed here (the newest of each store are).
        </p>
      )}
      {archive.rows.length > 0 && (
        <ul className="di-missions-retained-list">
          {archive.rows.map((row) => (
            <li key={row.ref} data-testid="di-missions-retained-item" data-mission-ref={row.ref} data-phase={row.phase}>
              <details>
                <summary>
                  {row.status !== null ? (
                    <span className="di-status" data-testid="di-missions-retained-status" data-status={row.status.id} title={row.status.meaning}>
                      <span aria-hidden="true">{row.status.glyph}</span> {row.status.label}
                    </span>
                  ) : (
                    <span className="di-status" data-testid="di-missions-retained-status" data-status="none">
                      No summary kept
                    </span>
                  )}{' '}
                  <span className="di-missions-title">{row.nativeId}</span>{' '}
                  <span className="di-missions-step" data-testid="di-missions-retained-phase">
                    {row.phaseWords}
                  </span>
                </summary>
                <div className="di-missions-retained-detail" data-testid="di-missions-retained-detail">
                  <p className="di-page-note">{row.ref}</p>
                  {row.statusReason !== null && (
                    <p className="di-page-note" data-testid="di-missions-retained-status-reason">
                      {row.statusReason}
                    </p>
                  )}
                  {row.provenance !== null && (
                    <p className="di-page-note" data-testid="di-missions-retained-provenance">
                      {row.provenance}
                    </p>
                  )}
                  {row.facts.length > 0 && (
                    <dl className="di-missions-retained-facts">
                      {row.facts.map((fact) => (
                        <div key={fact.key} data-testid="di-missions-retained-fact" data-fact={fact.key} data-state={fact.state}>
                          <dt>{fact.name}</dt>
                          <dd>
                            {fact.words} <span className="di-page-note">({fact.source})</span>
                          </dd>
                        </div>
                      ))}
                    </dl>
                  )}
                  <div className="di-missions-retained-actions">
                    {row.actions.map((action) => (
                      <button
                        key={action.id}
                        type="button"
                        className="di-chip"
                        disabled
                        data-testid="di-missions-retained-action"
                        data-action={action.id}
                        title={action.reason}
                        aria-describedby={`di-retained-${row.ref}-${action.id}`.replace(/[^A-Za-z0-9_-]/g, '-')}
                      >
                        {action.label}
                      </button>
                    ))}
                    {row.actions.map((action) => (
                      <p
                        key={action.id}
                        className="di-page-note"
                        id={`di-retained-${row.ref}-${action.id}`.replace(/[^A-Za-z0-9_-]/g, '-')}
                        data-testid="di-missions-retained-action-reason"
                        data-action={action.id}
                      >
                        {action.label}: {action.reason}
                      </p>
                    ))}
                  </div>
                </div>
              </details>
            </li>
          ))}
        </ul>
      )}
    </section>
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
          {/* the Mission's status, the same pill and words its row wears (the one status contract) */}
          {card.missionStatus && (
            <StatusTag
              label={card.missionStatus.label}
              tone={card.missionStatus.tone}
              glyph={card.missionStatus.glyph}
              testid="di-reception-mission-status"
            />
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
  // F2: Clear info succeeds by ceasing to exist (it is drawn only while information is listed), so it owes a SURVIVING destination BEFORE it goes: this heading.
  const infoHeadingRef = useRef<HTMLHeadingElement>(null);
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
            <h2 id={infoHeading} ref={infoHeadingRef} tabIndex={-1} data-testid="di-reception-info-heading">
              Information
            </h2>
            {info.length > 0 && (
              <button
                type="button"
                className="di-page-button"
                data-testid="di-reception-clear-info"
                title="Clears information items from this view only. Items waiting on you stay until they are handled."
                onClick={() => {
                  infoHeadingRef.current?.focus();
                  setCleared(new Set(reception.information.map((card) => card.key)));
                }}
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

// ── New Mission: one screen, one composer, the proposal in place ─────────────────────────────────

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
 * The request workspace's seam, as New Mission uses it: the workspace's OWN Ask draft (its one composer), its open conversation, and its own operations.
 * New Mission adds no state and no route of its own: Send is the workspace's drafting `turn`; submitting the draft, approving it and pressing Start stay
 * the workspace's gates (`openAsk`, `openRequest` only navigate there).
 */
export interface DiNewMissionRequest {
  state: MrState;
  setComposer: (text: string) => void;
  /** Send the composer's words as ONE read-only drafting turn of the conversation shown here. */
  sendTurn: () => void;
  /** Clear the composer and the shown conversation (nothing is sent). */
  newConversation: () => void;
  /** D1: close the shown conversation, KEEPING the typed ask as the same input (nothing is sent or cleared). */
  closeConversation: () => void;
  /** READ one conversation and show it here (view only; nothing is sent). */
  readConversation: (conversationId: string) => void;
  /** While this page shows the conversation it is read as the open workspace reads it (reads only). */
  watchConversation: (on: boolean) => void;
  /** Open the request workspace on Ask, on this SAME conversation, where its draft is submitted (a navigation). */
  openAsk: (opener: HTMLElement) => void;
  /** Open the request workspace on Requests, on this request (a navigation). */
  openRequest: (requestRef: string, opener: HTMLElement) => void;
  /** B8: the workspace is open and showing, so ITS Ask composer is on screen (holding the same draft): New Mission then draws no second box. */
  workspaceShown: boolean;
  /** Close the workspace (nothing is sent; the draft and the conversation are kept); focus goes back to `returnTo` when given. */
  closeWorkspace: (returnTo: HTMLElement | null) => void;
  /** Dismiss one of the workspace's notices (the same list the workspace shows; nothing is sent). */
  dismiss: (id: number) => void;
}

/** What happened to a draft after it was shown here, in the workspace's own words (view only: nothing on this page submits it). */
function DraftSubmission({ turn, standing, request }: { turn: MrTurn; standing: DiDraftStanding; request: DiNewMissionRequest }) {
  const submission = turn.submission;
  if (submission === null || standing === 'draft') return null;
  if (standing === 'submitted' && submission.requestRef !== null) {
    const ref = submission.requestRef;
    return (
      <p className="di-page-note" data-testid="di-new-draft-submitted">
        Submitted as <code>{ref}</code>, a proposal of record. Approving it and pressing Start happen in Requests.{' '}
        <button
          type="button"
          className="di-page-button"
          data-testid="di-new-draft-open-request"
          onClick={(event) => request.openRequest(ref, event.currentTarget)}
        >
          Open it in Requests
        </button>
      </p>
    );
  }
  return (
    <p className="di-page-note" data-testid="di-new-draft-submission" data-state={submission.state}>
      {standing === 'submitting'
        ? 'This draft is being submitted now. It will appear under Requests when it is recorded.'
        : standing === 'refused'
          ? `This draft was not recorded (${submission.problem ?? 'refused'}). Nothing was changed.`
          : 'Whether this draft was recorded is not known. It was not submitted again. Look under Requests.'}
    </p>
  );
}

/**
 * 1A: whether a drafted turn's own control ("Open it in the request workspace to submit") is offered. ONE rule, read by the control's presence AND by Send's rank, so the page can
 * never draw the open control and Send as primaries at once, nor draw neither while the proposal is waiting to be opened.
 */
function draftIsOpenable(turn: MrTurn, latest: boolean): boolean {
  const standing = draftStanding(turn.submission?.state ?? null, turn.submission?.requestRef ?? null);
  return latest && (standing === 'draft' || standing === 'refused');
}

/**
 * The drafted proposal, IN PLACE under the conversation that produced it: exactly as drafted (the workspace's own ProposalView), said to be a draft, with the
 * gates named. Its only control navigates to the workspace's Ask on this same conversation, where Submit is; nothing here submits, approves or starts.
 */
function DraftInPlace({
  turn,
  latest,
  meanings,
  request,
}: {
  turn: MrTurn;
  latest: boolean;
  meanings: Record<string, string>;
  request: DiNewMissionRequest;
}) {
  const draft = turn.draft!;
  const submission = turn.submission;
  // F5: ONE reading of where the draft stands, and every statement on this card written from it (the head, the Source line, the not-runnable line, the
  // submission line and the onward control), so no line says another state than the others
  const standing = draftStanding(submission?.state ?? null, submission?.requestRef ?? null);
  const canOpen = draftIsOpenable(turn, latest);
  const note = draftNote(standing, latest);
  const sourceStanding =
    standing === 'submitted' && submission?.requestRef ? (
      <>
        {DI_DRAFT_SOURCE_SUBMITTED} <code data-copy="content">{submission.requestRef}</code>.
      </>
    ) : standing === 'not_known' ? (
      DI_DRAFT_SOURCE_UNKNOWN
    ) : undefined;
  return (
    <section className="di-new-draft" data-testid="di-new-draft" data-latest={latest ? 'true' : 'false'} aria-label="The drafted proposal">
      <p className="di-new-draft-note" data-testid="di-new-draft-note">
        <strong>{note.head}</strong> {note.body}
      </p>
      <ProposalView
        proposal={draft.proposal}
        source={{ requestRef: null, missionId: null, revision: null, digest: draft.digest }}
        meanings={meanings}
        heading="The proposal, exactly as drafted"
        level={3}
        testId="di-new-draft-proposal"
        sourceStanding={sourceStanding}
      />
      {draft.runnableProblem !== null && (
        <p className="di-page-note" data-testid="di-new-draft-not-runnable">
          This proposal could not start a run: {draft.runnableProblem}. {draftRunnableWords(standing)}
        </p>
      )}
      <DraftSubmission turn={turn} standing={standing} request={request} />
      {canOpen && (
        <div className="di-new-draft-actions">
          <button
            type="button"
            className="di-page-button di-page-primary"
            data-testid="di-new-draft-open"
            onClick={(event) => request.openAsk(event.currentTarget)}
          >
            {DI_DRAFT_OPEN_LABEL}
          </button>
        </div>
      )}
    </section>
  );
}

/**
 * The conversation, in place: every turn's words as the workspace draws them, and the drafted proposal under the turn that drafted it. `onNewConversation` is
 * the thread's own reset (it clears the box, as it always has); the page moves focus out of the thread first, since the thread unmounts in that update.
 */
/** R22 (d): what New Mission last DREW of the conversation shown: its id, how many turns, and whether that was a READ (NewMissionThread reads it on mount). */
interface DiNewMissionDrawn {
  id: string | null;
  count: number;
  read: boolean;
}

function NewMissionThread({
  state,
  request,
  onNewConversation,
  drawnBefore,
}: {
  state: MrState;
  request: DiNewMissionRequest;
  onNewConversation: () => void;
  drawnBefore: { readonly current: DiNewMissionDrawn | null };
}) {
  const heading = useId();
  const turns = state.conversation.turns;
  const meanings = state.overview?.capabilities.scopeMeanings ?? {};
  const latestDraftTurn = [...turns].reverse().find((t) => t.draft !== null)?.turnId ?? null;
  const omitted = omittedWords(state.conversation);
  // the newest turn, and a draft as it lands, come into view where they appear (the page does not move for anything else).
  // B2e: WHY the turns grew decides it, never "is this the first mount" (the thread mounts only with turns), in FOUR cases:
  //  (a) a FRESH SEND: the conversation the person just started by sending, never read (`readSeq` null), drawn the first time: it comes into view;
  //  (b) a LATER ARRIVAL in the conversation already drawn (a turn sent, its answer, a draft landing, a newer turn a re-read brings): it comes into view;
  //  (c) the FIRST DRAW of a conversation that was READ (a Recent pick, or a switch to another): what was already there is not an arrival, so the page
  //      does not move. A pick grows the count from 0 to all its turns, and scrolling then hid the way out under the title at 1280.
  //  (d) R22: the FIRST TURN of a conversation the page had ALREADY DRAWN AS READ AND EMPTY (a send into it, or a turn a re-read brings): an ARRIVAL, so it
  //      comes into view. The thread cannot remember that empty draw (it was not mounted), so it reads the page's record, on mount only. A first mount of
  //      a conversation NOT drawn as read (a pick, a switch, a read landing after "not read" or "reading", as the retry's does) stays (c).
  const lastRef = useRef<HTMLLIElement | null>(null);
  const conversationId = state.conversation.id;
  const wasRead = (state.conversation.readSeq ?? null) !== null;
  const seen = useRef<{ id: string | null; count: number; draft: string | null } | null>(null);
  useEffect(() => {
    const before = seen.current;
    const drawn = drawnBefore.current;
    const emptyReadBefore = before === null && drawn !== null && drawn.id === conversationId && drawn.read && drawn.count === 0; // (d)
    const firstDraw = !emptyReadBefore && (before === null || before.id !== conversationId);
    const arrived =
      emptyReadBefore || // (d) the first turn into a conversation drawn as read and empty
      (firstDraw
        ? !wasRead // (a) a fresh send comes into view; (c) a read conversation's first draw does not
        : before !== null && (turns.length > before.count || latestDraftTurn !== before.draft)); // (b)
    if (arrived) lastRef.current?.scrollIntoView?.({ block: 'nearest' });
    seen.current = { id: conversationId, count: turns.length, draft: latestDraftTurn };
  }, [conversationId, wasRead, turns.length, latestDraftTurn, drawnBefore]);
  return (
    <section className="di-new-section di-new-thread" data-testid="di-new-thread" aria-labelledby={heading}>
      <h2 className="di-new-label" id={heading}>
        This conversation
      </h2>
      <p className="di-page-note" id="di-new-drafting-note" data-testid="di-new-drafting-note">
        {MR_DRAFTING_NOTE}
      </p>
      {omitted !== null && (
        <p className="di-page-note" role="note" data-testid="di-new-turns-omitted">
          {omitted}
        </p>
      )}
      <ol className="di-new-turns" aria-label="The conversation">
        {turns.map((turn, index) => (
          <li
            key={turn.turnId}
            ref={index === turns.length - 1 ? lastRef : undefined}
            className="di-new-turn"
            data-testid={`di-new-turn-${turn.sequence}`}
          >
            <TurnWords turn={turn} prefix="di-new" describedBy="di-new-drafting-note" />
            {turn.draft !== null && (
              <DraftInPlace turn={turn} latest={turn.turnId === latestDraftTurn} meanings={meanings} request={request} />
            )}
          </li>
        ))}
      </ol>
      <div className="di-new-thread-actions">
        <button type="button" className="di-page-button" data-testid="di-new-new-conversation" onClick={onNewConversation}>
          Start a new conversation
        </button>
      </div>
    </section>
  );
}

/**
 * New Mission (Figure 4.3a), reached from the rail only (D-8): ONE screen with ONE composer. The Ask box is the request workspace's own Ask draft, so the ask
 * is entered once; Send sends it as one read-only drafting turn of the workspace's conversation, and the reply (questions, or a drafted proposal) appears here,
 * in place. Nothing here submits, approves or starts: the draft names where those gates are and navigates there. Without a request panel the box can be
 * typed in, and Send says why nothing can be sent.
 */
export function NewMissionPage({
  rows,
  source,
  specimen,
  request,
  onOpenRequestWorkspace,
  onOpenMissions,
  escapeStep,
}: {
  rows: readonly DiScanRow[];
  source: DiNewMissionSource;
  specimen: boolean;
  /** The request workspace's seam (absent: no request panel in this office; Send says so). */
  request?: DiNewMissionRequest;
  /** Opens the request workspace's Requests (absent: the control is drawn disabled with its reason). */
  onOpenRequestWorkspace?: (opener: HTMLElement) => void;
  onOpenMissions: () => void;
  /**
   * D1: the scan face's Escape ladder asks this page for ONE step before it goes Home. While a conversation is shown, the page sets it to a step that closes
   * that conversation (as "Close this conversation" does) and answers true; with none, it is null and Escape returns Home.
   */
  escapeStep?: RefObject<(() => boolean) | null>;
}) {
  // without a request panel the box is the page's own (nothing can be sent from it); with one, it IS the workspace's Ask draft
  const [localAsk, setLocalAsk] = useState('');
  const [sampleOpen, setSampleOpen] = useState(false);
  // Review round 2, blocker 1. A READ of the conversation this page shows can fail on two routes: a Recent PICK (the reducer's openConversation clears the
  // thread at once, before any answer) and the WATCH while the page shows it (the last-read thread stays). Attribution is by IDENTITY, never by arrival:
  //  - READ: `conversation.readSeq` is the seq of the read of THIS conversation that succeeded (never inferred from turns: an empty conversation can be read);
  //  - NOT READ: a failure notice ABOUT this conversation (`notice.about`: the request it answers), from a request LATER than that success (by seq), raised
  //    since this conversation became the one shown (`readSince`, adjusted while rendering, React's derived-state pattern). A late failure of another
  //    conversation is never said here, whenever it lands, and a later success withdraws an earlier failure.
  const shownId = request?.state.conversation.id ?? null;
  const [readSince, setReadSince] = useState({ id: shownId, at: request?.state.nextNotice ?? 0 });
  if (request !== undefined && readSince.id !== shownId) setReadSince({ id: shownId, at: request.state.nextNotice });
  const readSeq = request?.state.conversation.readSeq ?? null;
  const isCurrentReadFailure = (notice: MrNotice) =>
    notice.op === 'conversation' &&
    shownId !== null &&
    notice.about?.conversationId === shownId &&
    notice.id >= readSince.at &&
    (readSeq === null || notice.about.seq > readSeq);
  // a PICKED conversation not yet read has nothing of it shown (a turn's answer would have drawn turns): Send waits while a read OF IT is in flight, and holds,
  // with the reason, once its read was refused or not answered; it is released by a successful read of it, not by dismissing a notice
  const notShown = request !== undefined && shownId !== null && readSeq === null && request.state.conversation.turns.length === 0;
  const reading = notShown && request.state.pending.some((p) => p.op === 'conversation' && p.conversationId === shownId);
  const unread = notShown && !reading;
  // R22 (d): what this page last DREW of the conversation shown (NewMissionThread mounts only with turns, so it cannot remember an empty read itself). Written
  // after EVERY commit; the thread reads it on mount only, and a child's effects run before its parent's, so it reads the previous commit's record.
  const drawnRef = useRef<DiNewMissionDrawn | null>(null);
  useEffect(() => {
    drawnRef.current = { id: shownId, count: request?.state.conversation.turns.length ?? 0, read: readSeq !== null };
  });
  // B2e / DESIGN.md: "Read this conversation again" is its own labelled control (the selected Recent ask CLOSES the conversation, so a click there no
  // longer re-reads). The LIVE control is drawn only while this page says the read of the conversation shown FAILED (unread, or a current read-failure
  // notice) AND no read of THAT conversation is in flight. While one is in flight the same label stays in the row, held and disabled, with the reason:
  // removing it detached the control under a press. A successful read clears the failure, and the control goes with it.
  const offerReadAgain = request !== undefined && (unread || request.state.notices.some(isCurrentReadFailure));
  const readAgainInFlight =
    shownId !== null &&
    conversationReadPending(request?.state.pending, shownId);
  const askId = useId();
  const hintId = useId();
  const reasonId = useId();
  const sampleId = useId();
  const state = request?.state ?? null;
  const ask = state === null ? localAsk : state.composer;
  const setAsk = request?.setComposer ?? setLocalAsk;
  // the capacity GUARD's inputs, separate from the display count: an unknown is never spare capacity
  const use = orchestratorUse(rows);
  const atLimit = atOrchestratorLimit(use);
  const busy = state !== null && (state.conversation.turns.some((t) => t.state === 'RESERVED') || isPending(state, 'turn'));
  // B8: while the workspace shows its own Ask composer, that is the ONE box (the same draft): this page draws none, and says where it is
  const elsewhere = request?.workspaceShown === true;
  const panel: DiAskPanel =
    state === null
      ? 'none'
      : state.unvouched !== null
        ? 'unvouched'
        : state.availability.kind === 'unavailable'
          ? 'unavailable'
          : unread
            ? 'unread'
            : reading
              ? 'reading'
              : elsewhere
            ? 'elsewhere'
            : busy
              ? 'busy'
              : 'ready';
  // "Bring the ask back here" closes the workspace; focus then goes to the box, once it is drawn again
  const askRef = useRef<HTMLTextAreaElement | null>(null);
  const bringBackRef = useRef<HTMLButtonElement | null>(null);
  const closeRef = useRef<HTMLButtonElement | null>(null);
  const focusAskNext = useRef(false);
  useEffect(() => {
    if (elsewhere || !focusAskNext.current) return;
    focusAskNext.current = false;
    askRef.current?.focus();
  }, [elsewhere]);
  // D1: leaving the conversation shown, back to the blank start, KEEPING the typed ask: ONE atomic transition (the reducer's closeConversation), so the ask
  // stays the SAME input (never a clear and a retype). Local only: nothing is sent, submitted, approved or started. Focus moves FIRST to the box that keeps the ask (it stays mounted
  // through the reset), or, while the workspace shows its own box, to the control that brings it back: whatever had focus may unmount in this update.
  const focusTheAskFirst = () => (askRef.current ?? bringBackRef.current)?.focus();
  const leaveConversation = () => {
    if (request === undefined) return;
    focusTheAskFirst();
    // one transition: the conversation closes and the ask stays the SAME input (never a clear and a retype, which would make it a new draft)
    request.closeConversation();
  };
  // the thread's own "Start a new conversation" clears the box, as it always has; only where focus goes is decided here (it was left on <body>)
  const startNewConversation = () => {
    if (request === undefined) return;
    focusTheAskFirst();
    request.newConversation();
  };
  // B2e correction: focus moves FIRST to "Close this conversation", this control's neighbour in the same pinned row, so it may leave (it does once the read is
  // in flight) without dropping focus on <body>. Not the box, as Close's own step does: Close REMOVES the conversation, a read DRAWS it, above the box, which
  // then leaves the view (split-4, measured at every size), and the first draw must not scroll (case (c)). A second Enter there closes the conversation, as
  // a second Enter on a picked Recent ask deselects it: disclosed, not hidden.
  const readAgain = () => {
    if (request === undefined || shownId === null || readAgainInFlight) return;
    // focus moves FIRST to "Close this conversation", this control's neighbour, so the live button may become the held control without dropping focus
    closeRef.current?.focus();
    request.readConversation(shownId);
  };
  const conversationShown = request !== undefined && shownId !== null;
  // the Escape ladder's step for this page, kept current with the conversation shown and the ask as typed
  useEffect(() => {
    if (escapeStep === undefined) return;
    escapeStep.current = conversationShown
      ? () => {
          leaveConversation();
          return true;
        }
      : null;
    return () => {
      escapeStep.current = null;
    };
  });
  const held = sendAskReason(ask, use, panel);
  // not available now: the hold says why, in the office's own words
  const reason =
    held === DI_ASK_UNAVAILABLE && state !== null && state.availability.kind === 'unavailable' ? `${held} ${state.availability.reason}` : held;
  // F2 and blocker 1: what came of this page's OWN requests, said HERE: Send (refused, not known, not sent) and the conversation read (not read, with what is
  // shown of it now); a refusal the watch repeats on every poll is said once
  const outcomes = state !== null ? pageOutcomes(state.notices, isCurrentReadFailure) : [];
  const readTail = readSeq !== null ? DI_READ_LAST_READ : state !== null && state.conversation.turns.length > 0 ? DI_READ_LAST_HAD : DI_READ_NOTHING_SHOWN;
  const send = () => {
    if (reason !== null || request === undefined) return;
    request.sendTurn();
  };
  // while this page is on screen it shows the conversation, so it is read (reads only, as the open workspace reads it)
  const watch = request?.watchConversation;
  useEffect(() => {
    if (watch === undefined) return;
    watch(true);
    return () => watch(false);
  }, [watch]);
  const recent = recentAsks(source, state?.overview ?? null);
  // 1A: the page's ONE primary. The open control (inside the thread) is the primary while it is offered on the latest drafted turn; Send is then a secondary. The thread mounts under the
  // same condition as at its call site below, so this reads the drawn state and never a hidden one.
  const draftedTurn =
    state !== null && request !== undefined && state.conversation.turns.length > 0 ? [...state.conversation.turns].reverse().find((t) => t.draft !== null) : undefined;
  const openIsPrimary = draftedTurn !== undefined && draftIsOpenable(draftedTurn, true);
  return (
    <div className="di-page di-new" data-testid="di-new-mission">
      <PageHead title="New Mission">{specimen && <FixtureTag />}</PageHead>
      <p className="di-page-sub">{DI_NEW_MISSION_SUB}</p>
      {atLimit && (
        <div className="di-new-limit" role="status" data-testid="di-new-limit">
          <span className="di-new-limit-copy">
            <strong>{orchestratorLimitWords(use)}</strong>
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
      {/* B2e (Direction B, the selected handoff): Recent in a LEFT pane, its pick marked, the conversation docked beside it. Each pane scrolls
          inside itself, so the title, the way out, the Recent pane and Send stay on screen while a long conversation scrolls. The DOM order is
          unchanged (the main content, then Recent and the request workspace), so Tab order is exactly as before; the grid places Recent left. */}
      {/* B2e correction (F4): while the request workspace holds the ask, the panes swap columns (diScanPages.css), DOM order unchanged */}
      <div className="di-new-body" data-testid="di-new-body" data-ask={elsewhere ? 'elsewhere' : undefined}>
        <div className="di-new-main" data-testid="di-new-main">
          {conversationShown && (
            // D1: ONE labelled way out of the conversation shown, in every state of a Recent pick (read with turns, read and empty, still being read, not read):
            // back to the blank start, the typed ask kept. The first Escape does the same (DiScanPanel's ladder asks this page for that step).
            <div className="di-new-thread-actions">
              <button
                ref={closeRef}
                type="button"
                className="di-page-button"
                data-testid="di-new-close-conversation"
                aria-keyshortcuts="Escape"
                title={DI_NEW_CLOSE_CONVERSATION_NOTE}
                onClick={leaveConversation}
              >
                {DI_NEW_CLOSE_CONVERSATION}
              </button>
              {offerReadAgain &&
                (readAgainInFlight ? (
                  <Off testid="di-new-read-again" label={DI_NEW_READ_AGAIN} reason={READ_AGAIN_IN_FLIGHT_REASON} />
                ) : (
                  <button type="button" className="di-page-button" data-testid="di-new-read-again" onClick={readAgain}>
                    {DI_NEW_READ_AGAIN}
                  </button>
                ))}
            </div>
          )}
          <div className="di-new-main-scroll" data-testid="di-new-main-scroll">
            {state !== null && request !== undefined && state.conversation.turns.length > 0 && (
              <NewMissionThread state={state} request={request} onNewConversation={startNewConversation} drawnBefore={drawnRef} />
            )}
            {/* read, and empty: said as that (never as "not read", and never left as a blank page) */}
            {state !== null && shownId !== null && readSeq !== null && state.conversation.turns.length === 0 && (
              <p className="di-page-note" data-testid="di-new-thread-empty">
                {DI_READ_EMPTY}
              </p>
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
              {elsewhere && request !== undefined ? (
                <div className="di-new-elsewhere" role="note" data-testid="di-new-ask-in-workspace">
                  <p className="di-page-note">{DI_ASK_IN_WORKSPACE_NOTE}</p>
                  <button
                    ref={bringBackRef}
                    type="button"
                    className="di-page-button"
                    data-testid="di-new-ask-bring-back"
                    onClick={() => {
                      focusAskNext.current = true;
                      request.closeWorkspace(null);
                    }}
                  >
                    {DI_ASK_BRING_BACK}
                  </button>
                </div>
              ) : (
                <textarea
                  ref={askRef}
                  id={askId}
                  className="di-new-ask"
                  data-testid="di-new-ask"
                  rows={4}
                  value={ask}
                  placeholder="Say what you want done, in plain words."
                  aria-describedby={hintId}
                  onChange={(event) => setAsk(event.target.value)}
                  onKeyDown={(event) => {
                    // Cmd Return (Ctrl Return) does what Send does, and adds no line break
                    if (event.key === 'Enter' && (event.metaKey || event.ctrlKey)) {
                      event.preventDefault();
                      send();
                    }
                  }}
                />
              )}
              {request !== undefined && (
                // a live region that is always present, so an outcome that arrives is announced; each outcome is the workspace's own notice, dismissed there too
                <div className="di-new-outcomes" role="status" data-testid="di-new-send-outcomes">
                  {outcomes.length > 0 && (
                    <ul aria-label="What came of Send and of reading this conversation">
                      {outcomes.map(({ notice, ids }) => (
                        <li key={notice.id} className="di-new-outcome" data-kind={notice.kind} data-op={notice.op} data-testid="di-new-send-outcome">
                          <span className="di-page-note">
                            <strong>{sendOutcomeWords(notice.kind, notice.op)}</strong> {notice.text}
                            {notice.op === 'conversation' ? ` ${readTail}` : ''}
                          </span>
                          <button
                            type="button"
                            className="di-page-button"
                            aria-label="Dismiss this notice"
                            data-testid="di-new-send-outcome-dismiss"
                            onClick={() => {
                              // the control succeeds by ceasing to exist, so it owes a SURVIVING destination BEFORE it goes: the page's own landing, the ask box
                              focusTheAskFirst();
                              ids.forEach((id) => request.dismiss(id));
                            }}
                          >
                            Dismiss
                          </button>
                        </li>
                      ))}
                    </ul>
                  )}
                </div>
              )}
              {unread && request !== undefined && (
                // R3-REC: the way out, on the page in exactly the state that needs it, named word for word by Send's hold (DI_ASK_UNREAD). It is the same
                // keeping reset as "Close this conversation" (leaveConversation: the reducer's atomic closeConversation, the ask kept as the same input; local only). R3-REC focus
                // (measured, logs/95: this control unmounts in the update it causes, which left focus on <body>): leaveConversation moves it to the box first.
                <div className="di-new-thread-actions">
                  <button
                    type="button"
                    className="di-page-button"
                    data-testid="di-new-leave-unread"
                    onClick={leaveConversation}
                  >
                    {DI_ASK_LEAVE_UNREAD}
                  </button>
                </div>
              )}
            </section>
            <section className="di-new-section">
              <h2 className="di-new-label">
                Attachments <span className="di-new-optional">Optional</span>
              </h2>
              <Off testid="di-new-add-files" label="+ Add files" reason={REASON.addFiles} />
            </section>
            {source.proposal !== null && (
              <section className="di-new-section" data-testid="di-new-sample">
                <button
                  type="button"
                  className="di-page-button"
                  data-testid="di-new-sample-proposal"
                  aria-expanded={sampleOpen}
                  aria-controls={sampleId}
                  onClick={() => setSampleOpen((open) => !open)}
                >
                  {sampleOpen ? 'Hide the sample proposal' : 'See the sample proposal'}
                </button>
                <div id={sampleId} hidden={!sampleOpen}>
                  <SampleProposal proposal={source.proposal} />
                </div>
              </section>
            )}
          </div>
        </div>
        <div className="di-new-side" data-testid="di-new-side">
          <section className="di-new-section" data-testid="di-new-recent">
            <h2 className="di-new-label">Recent</h2>
            {recent.kind === 'not_read' ? (
              <p className="di-page-note" data-testid="di-new-recent-not-read">
                {recent.words}
              </p>
            ) : recent.items.length === 0 ? (
              <p className="di-page-note" data-testid="di-new-recent-none">
                {DI_RECENT_NONE}
              </p>
            ) : (
              <ul className="di-new-recent">
                {recent.items.map((item) => (
                  <li key={item.conversationId ?? item.text}>
                    <button
                      type="button"
                      className="di-chip"
                      data-testid="di-new-recent-ask"
                      data-conversation={item.conversationId ?? undefined}
                      aria-current={item.conversationId !== null && item.conversationId === state?.conversation.id ? 'true' : undefined}
                      title={
                        item.conversationId === null
                          ? 'Puts this ask in the box. Nothing is sent.'
                          : item.conversationId === state?.conversation.id
                            ? DI_NEW_CLOSE_CONVERSATION_NOTE
                            : 'Shows this conversation here. Nothing is sent.'
                      }
                      onClick={() => {
                        const id = item.conversationId;
                        // B2e: the pick DESELECTS from the list. The selected ask, clicked again, closes the conversation by the SAME transition as "Close
                        // this conversation" (D1: back to the blank start, the typed ask kept, focus in the box); it is never read again
                        if (id !== null && id === state?.conversation.id) leaveConversation();
                        else if (id !== null && request !== undefined) request.readConversation(id);
                        else setAsk(item.text);
                      }}
                    >
                      <Line text={item.text} />
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </section>
          <section className="di-new-section di-new-requests">
            {onOpenRequestWorkspace === undefined ? (
              <Off testid="di-new-open-workspace" label="Review requests" reason={panel === 'unavailable' ? REASON.workspaceNotNow : REASON.noWorkspace} />
            ) : (
              <button
                type="button"
                className="di-page-button"
                data-testid="di-new-open-workspace"
                onClick={(event) => onOpenRequestWorkspace(event.currentTarget)}
              >
                Review requests
              </button>
            )}
          </section>
        </div>
      </div>
      <footer className="di-new-foot">
        <span className="di-page-note" id={hintId} data-testid="di-new-send-hint">
          <kbd>Cmd Return</kbd> {DI_ASK_SEND_HINT}
        </span>
        <button
          type="button"
          className={openIsPrimary ? 'di-page-button' : 'di-page-button di-page-primary'}
          data-rank={openIsPrimary ? 'secondary' : 'primary'}
          data-testid="di-new-send"
          // held, never `disabled`: a Send that becomes unavailable while it has focus (its message is being answered) keeps that focus
          aria-disabled={reason !== null ? 'true' : undefined}
          title={reason ?? undefined}
          aria-describedby={reason === null ? hintId : reasonId}
          onClick={send}
        >
          {DI_ASK_SEND_LABEL}
        </button>
        {reason !== null && (
          <span className="di-scan-visually-hidden" id={reasonId} data-testid="di-new-send-reason">
            {reason}
          </span>
        )}
      </footer>
    </div>
  );
}

/**
 * The proposal of Figure 4.3b, drawn from SAMPLE data IN PLACE on the specimen's New Mission (it is never a screen of its own): the harness, the repos DI
 * chose, each role's exact model with NO control to change it (AC-4.3-34), and Approve / Request changes / Reject, disabled with their reason.
 */
function SampleProposal({ proposal }: { proposal: DiProposalSpecimen }) {
  const heading = useId();
  return (
    <div className="di-proposal" data-testid="di-proposal">
      <h2 className="di-proposal-title">
        {proposal.title} <FixtureTag text="Sample proposal (fixture)" />
      </h2>
      <p className="di-proposal-sub">
        <StatusTag label="Needs approval" tone="needs" />
        <span>{`You · ${proposal.askedAt}`}</span>
      </p>
      <blockquote className="di-proposal-ask">{proposal.ask}</blockquote>
      <section className="di-proposal-card" aria-labelledby={heading}>
        <div className="di-proposal-card-head">
          <h3 id={heading}>Proposal</h3>
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
