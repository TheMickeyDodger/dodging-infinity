import './diScanPanel.css';

import {
  Fragment,
  type KeyboardEvent as ReactKeyboardEvent,
  type ReactNode,
  type Ref,
  useCallback,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  useSyncExternalStore,
} from 'react';

import type { DiRetainedSource } from '../../../core/src/messages.js';
import { DI_DELETE_HOLD_MS, DI_PANEL_WIDTH_PX, DI_RAIL_WIDTH_PX, DI_SCAN_TWO_PANE_MIN_PX } from '../constants.js';
import type { DiDeliveryControl } from '../hooks/useDiDelivery.js';
import type { ReviewSession, ReviewTargetRef } from './diCodeReviewModel.js';
import { useCoverIsolation } from './diCoverIsolation.js';
import { DiDeliveryStatus } from './DiDeliveryEntry.js';
import { enteredId, pendingLine } from './diDeliveryModel.js';
import type { DiHerdrControl, HerdrControlView } from './diHerdrModel.js';
import { herdrControlView } from './diHerdrModel.js';
import type { MrDeletion } from './diMissionRequestModel.js';
import { frontCount } from './diModalStack.js';
import { DI_FOCUS_RING } from './diNavigation.js';
import { scanDiffFor } from './diScanDiffRead.js';
import type {
  DiDeliveryStepView,
  DiScanAvatar,
  DiScanDeliveryHandoff,
  DiScanDiffLine,
  DiScanFact,
  DiScanMissionsRead,
  DiScanPill,
  DiScanRow,
  DiScanTab,
  DiScanUnresolvedAgent,
  DiScanView,
} from './diScanModel.js';
import {
  archivedDeliverySteps,
  archivedLinkedDeliverySteps,
  decideSeveral,
  deliveryActionTarget,
  deliveryStatusLine,
  deliverySteps,
  DI_COMMENTS_PAID,
  DI_DECIDE_UNAVAILABLE,
  DI_DECIDE_WHAT,
  DI_DELIVERY_ARCHIVED,
  DI_DELIVERY_ARCHIVED_ACT,
  DI_DELIVERY_ARCHIVED_HANDOFF,
  DI_DELIVERY_ARCHIVED_LINKED,
  DI_DELIVERY_END,
  DI_DELIVERY_HANDOFF_RUNS_NOTHING,
  DI_DELIVERY_MORE,
  DI_DELIVERY_NO_SESSION_WORDS,
  DI_EVIDENCE_LABEL,
  DI_EVIDENCE_NOT_AVAILABLE,
  DI_EVIDENCE_NOT_LISTED,
  DI_MISSIONS_READ,
  DI_NEEDS_YOU,
  DI_SCAN_MISSION_VIEWS,
  DI_SCAN_PAGE_VIEWS,
  DI_SCAN_TABS,
  knownTrue,
  linkedDeliverySteps,
  openAttentionCount,
  railOrchestrators,
  rowContextLine,
  scanThread,
  sharedStepReason,
  stateActions,
  statusPillOf,
  swarmSummary,
  trashGate,
  unlinkedDeliverySteps,
  unreadStateFacts,
  visibleScanRows,
} from './diScanModel.js';
import type { DiNewMissionRequest } from './DiScanPages.js';
import { MissionsPage, NewMissionPage, ReceptionPage, StatusMark } from './DiScanPages.js';
import type {
  DiMissionsStatus,
  DiNewMissionSource,
  DiReceptionCard,
  DiScanCountDoubt,
  DiScanReception,
} from './diScanPagesModel.js';
import {
  countBadge,
  DI_LAYOUT_NOTE,
  DI_NEW_MISSION_OFFICE,
  LISTED_RETENTION_PHASE,
  receptionHeadline,
  retentionFactWords,
} from './diScanPagesModel.js';
import { AnchoredPopover, PopoverItem } from './ui/AnchoredPopover.js';

export interface DiScanPanelProps {
  rows: readonly DiScanRow[];
  /** Select this row on first paint (the locked mock opens on its first mission). */
  initialSelectedId?: string | null;
  /** Open on this screen. The product starts on the list. */
  initialView?: DiScanView;
  /** B2b: the list dock's width, which a docked screen's right edge leaves free in two-pane mode (the office panel's own width). */
  dockWidthPx?: number;
  /**
   * B2c: Mission details opens with "More actions" already open. For the labelled specimen and for static-markup tests only (a closed popover's items
   * are not in the markup, so a test that pins an item's state renders it open); the product starts closed. Like `initialView`, an initial state only.
   */
  initialMoreActionsOpen?: boolean;
  /** The Missions page opens on this filter. The product starts on All. */
  initialMissionsStatus?: DiMissionsStatus;
  minimizeRef?: Ref<HTMLButtonElement>;
  onMinimize: () => void;
  /**
   * AC-4.4-26: open THIS Mission's details because its agent or Mission label was clicked in the world; Home is its origin,
   * so Back returns there. A new `seq` is a new click (the same Mission clicked twice opens it twice).
   */
  openRequest?: { ref: string; seq: number } | null;
  /** View only: the parent may move the camera to a drawn member. */
  onSelect?: (id: string) => void;
  /**
   * Opens the Mission request workspace on its Requests, from New Mission's "Review requests" (a navigation: the scan face's one general entrance to the
   * proposals of record and their decisions). Absent: the control stays, disabled, and says why.
   */
  onOpenRequestWorkspace?: (opener: HTMLElement) => void;
  /** Opens the existing Settings dialog (the panel's sliders and the rail's Settings). Absent: Settings says why. */
  onOpenSettings?: () => void;
  /** B3 routes: a keyboard route from Home to the Map (absent where the Map cannot be reached) and to the minimized surfaces (shown only while some are). Each acts or is not drawn. */
  onJumpToWorld?: () => void;
  minimizedCount?: number;
  onJumpToMinimized?: () => void;
  /**
   * Stage 4: the ONE review session (the operational modal's own hook and reducer) and its controls. The scan Code Diff reads the
   * selected Mission through it (an admitted reference, never a path) and draws the answer in the nested editor: no modal.
   */
  reviewSession?: ReviewSession | null;
  onReviewBegin?: (target: ReviewTargetRef) => void;
  onReviewEnd?: () => void;
  onReviewChoose?: (path: string | null) => void;
  onReviewRefresh?: () => void;
  /**
   * The scan face's ONE Reception: the full page beside the rail, and the rail badge, both read from this source, so the
   * badge and the header are one number by construction (AC-4.7-16). Null or absent: this office has no reception desk;
   * the rail entry is drawn disabled with its reason, and no badge is drawn. Zero: no badge (AC-4.7-15).
   */
  reception?: DiScanReception | null;
  /** Opens the existing request workspace on a Reception item its source names (view only; nothing is sent). */
  onOpenRequestItem?: (card: DiReceptionCard, opener: HTMLElement) => void;
  /**
   * M9 / R4: opens the existing request panel on ONE proposal waiting for a decision, from Mission details' "Decide in the request panel" (a navigation: nothing is decided here; the panel records only
   * the exact approval). Absent: the navigation is drawn disabled and says why.
   */
  onOpenDecision?: (requestRef: string, opener: HTMLElement) => void;
  /**
   * R10: draws the EXISTING evidence library for one Mission on its "Evidence and review" screen, exactly as the office lists it (the office holds the record and the content-read seam). Absent: the
   * screen's entrances are drawn disabled and say why.
   */
  renderEvidence?: (missionRef: string) => ReactNode;
  /**
   * New Mission's ONE composer: the request workspace's own Ask draft, its drafting turn (Send) and its conversation, shown in place. Submitting, approving and
   * starting stay the workspace's gates. Absent: no request panel in this office; New Mission's Send is drawn unavailable and says why.
   */
  newMissionRequest?: DiNewMissionRequest | null;
  /** What New Mission is drawn against. Absent: the office's own (recent asks not read, no sample proposal). */
  newMission?: DiNewMissionSource;
  /** The rows are the labelled sample specimen: every page says so (lead's labelling condition). */
  specimen?: boolean;
  /**
   * Whether the office READ its Missions (B-H1). Not read and unavailable are said as themselves, never as an empty list.
   * Absent: read.
   */
  missionsRead?: DiScanMissionsRead;
  /** Fixture mode's persistent label, drawn on every page and stage so no screen hides it (B-E1). Absent: not fixture mode. */
  fixtureBanner?: string | null;
  /**
   * M8: Mission details' OWN delivery session (a second session of useDiDelivery, linked mode). Delivery under Swarm reads only the ONE
   * delivery the catalog links to the shown Mission. Absent: no delivery is read here, and Delivery says why.
   */
  missionDelivery?: DiDeliveryControl | null;
  /**
   * The Herdr control (useDiHerdr): the office server opens a terminal on the shown Mission's herd when the person clicks Herdr. Every Herdr button reads its enabled state and its words from this control's
   * state for the Mission, and a click asks the control to launch. Absent: the Herdr buttons are drawn disabled and say that no control is connected to this view; they open nothing.
   */
  herdr?: DiHerdrControl | null;
  /** Deliverable 2: the owner's "Delete local data", relayed to the request panel (absent: no panel). */
  onDeleteLocalData?: (missionRef: string, missionId: string, displayedState: string, storeIdentity: string | null) => void;
  /** Deliverable 2: one archive reading per retention source (null: not reported). */
  retained?: readonly DiRetainedSource[] | null;
  /** Deliverable 2, round 9: each Mission's latest owner delete, by Mission id, shown at that Mission (absent: none). */
  deleteResults?: Readonly<Record<string, MrDeletion>>;
}

/** The list hides nothing (there is no hide gesture; V-6). */
const NOTHING_HIDDEN: ReadonlySet<string> = new Set();

/** Every screen the panel has; anything else (a stale 'herdr' or 'proposal' request) opens on the list. */
const VIEWS: readonly DiScanView[] = ['list', ...DI_SCAN_MISSION_VIEWS, ...DI_SCAN_PAGE_VIEWS];

/** Said beside every action drawn at this stage (Stage 2 is layout only; Stage 4 wires the routes). */
const LAYOUT_NOTE = DI_LAYOUT_NOTE;
/**
 * Herdr opens the machine's own terminal on the Mission's herd, where the person types (owner decision 1). The words and the enabled state of every Herdr button come from the control's state for the Mission
 * (diHerdrModel.ts herdrControlView), not from a constant: this office DOES start that terminal, on a click, when the server says a launch is available.
 */
function herdrViewFor(herdr: DiHerdrControl | null | undefined, missionRef: string | null): HerdrControlView {
  return herdrControlView(herdr?.state ?? null, missionRef);
}
const CONNECTIONS_REASON = 'Connections are not in this build.';
/** Deliverable 2: an archived Mission is read-only; every control that cannot apply is drawn disabled with this reason, never hidden. */
const ARCHIVED_READ_ONLY =
  'Archived: this Mission is read-only. Nothing can be started, restarted, run or delivered from it.';
/**
 * The trash's scope, true at FULL scope (owner decision 2): DI's own Mission data, the herd tools' own terminal history,
 * and the model providers' own session transcripts. The summary and the approval record survive, and a partial delete
 * is said as partial (the eventual control reports per owner; nothing is deleted at this stage).
 */
const TRASH_KEEPS =
  'Holding deletes this Mission’s local data from this office: its conversation, its request and its logs, AND its retained summary (final status, verdict, CI results and comments). It cannot be undone. What remains: Mission Core’s own record of the request and its authority (authority history is never erased) and a record that it was deleted. Not reached from here: the providers’ own session transcripts, the target herd’s own state, and PR delivery receipts. Without this, its reasoning and logs are reclaimed automatically 30 days after it finished, and its summary is kept.';
/**
 * The trash's scope while a deletion of this Mission is NOT finished (its deletion started and is retried, or a delete was asked and its answer is not back, or
 * was accepted and is still finishing): the same facts as TRASH_KEEPS in the tense that is true then. It never says the Mission "was deleted" (it is not, yet),
 * on any surface, drawn or spoken (diRetentionHonesty.test.ts: an interrupted deletion is never said as done).
 */
const TRASH_KEEPS_UNFINISHED =
  'A deletion of this Mission is not finished. When it finishes, it has removed this Mission’s local data from this office: its conversation, its request and its logs, AND its retained summary (final status, verdict, CI results and comments). It cannot be undone. What remains then: Mission Core’s own record of the request and its authority (authority history is never erased) and a record of the deletion. Not reached from here: the providers’ own session transcripts, the target herd’s own state, and PR delivery receipts.';
/** Whether a deletion of this Mission is under way and not finished: the retention reading says it started, or this window's own delete is sent or accepted and finishing. */
function deletionUnfinished(row: DiScanRow, deletion: MrDeletion | undefined): boolean {
  return row.local?.archive.reduced === 'deleting' || deletion?.state === 'in_flight' || deletion?.state === 'pending';
}
/** Said where this build does not read a Mission's approval, repository, stall and log facts (owed by Stage 4). */
const STATE_NOT_READ =
  'Approvals, repository requests, stalls and logs are not reported for this Mission.';
/** Mission details' one Herdr line for an archived Mission: the reason, once, in Herdr's own words (the full read-only sentence is said once, above Delivery). */
const HERDR_ARCHIVED = 'Herdr is not offered: this Mission is archived, so it is read-only.';
/** A8: the labels of the details text's and the roster's on-demand detail (each a button that says what it shows, its state in aria-expanded). */
const DETAILS_MORE = 'Show the whole description';
const DETAILS_MORE_HANDOFF = 'Show the whole description and the handoff';
const DETAILS_LESS = 'Show the description as one line';
const ROSTER_MORE = 'Show the roster: each role’s agent, model, provider and notes';
const ROSTER_LESS = 'Hide the roster';
/** The labels of Herdr's on-demand detail: what a click does, the office's reason in its own words, or (archived) the office's answer about Herdr. */
const HERDR_WHAT_MORE = 'What Herdr does here';
const HERDR_REASON_MORE = 'The office’s reason, in its own words';
const HERDR_ANSWER_MORE = 'The office’s answer about Herdr';
/** The label of the Trash's on-demand detail: the one thing a person must know before holding it, said in the label itself. */
const TRASH_MORE = 'Deleting cannot be undone: what it removes, and what remains';
const TRASH_NOT_CONNECTED =
  'This office has no request panel to delete through, so nothing can be deleted here.';
const TRASH_LABEL = 'Hold to delete this Mission’s local data';

/** Round 9: the hold is withdrawn while this Mission's delete is in flight, and once one was accepted (a second press is impossible, not merely harmless). */
function deletionWithdraws(deletion: MrDeletion | undefined): boolean {
  return deletion !== undefined && (deletion.state === 'in_flight' || deletion.state === 'pending' || deletion.state === 'completed');
}

/**
 * A section's detail, ON DEMAND: a native disclosure, closed by default. Its summary is its accessible name and is reached and operated by keyboard (Tab, then
 * Enter or Space); its state is the element's own `open`. What it holds is never removed from the page, only folded: commands, policy text, store-path
 * explanations and the irreversible Trash details stay in the markup, and a control that is described by them is still described by them.
 */
function More({ testid, summary, children }: { testid: string; summary: string; children: ReactNode }) {
  return (
    <details className="di-scan-more" data-testid={testid}>
      <summary className="di-scan-more-summary" data-testid={`${testid}-summary`}>
        {summary}
      </summary>
      <div className="di-scan-more-body">{children}</div>
    </details>
  );
}

/**
 * Deliverable 2: what local retention has done to this Mission's data, said as it is: reclaimed (the reasoning and logs gone, the summary kept, each of its facts
 * recorded or not recorded locally with where it was looked for), deleted by its owner (the summary gone too), or intact. A reclaimed Mission never reads as
 * "no reasoning recorded" and never as an intact record.
 */
function LocalRetention({ row }: { row: DiScanRow }) {
  const retention = row.local?.retention ?? null;
  const reduced = row.local?.archive.reduced ?? null;
  if (retention === null || reduced === null) return null;
  // round 10: each reduced phase says whether it FINISHED (an interrupted reclamation or deletion is never said as done), from the one shared table.
  // The phase is the status line; its retained facts, each with where it was looked for, are one disclosure away (RetentionFacts).
  const phase = LISTED_RETENTION_PHASE[reduced];
  return (
    <p className="di-scan-stage-note" data-testid="di-scan-retention" data-phase={reduced} data-finished={String(phase.finished)}>
      {phase.words}
    </p>
  );
}

/** The retained summary's facts, each recorded or not recorded locally, with where it was looked for (the store each names): the detail behind the phase line. */
function RetentionFacts({ row }: { row: DiScanRow }) {
  const reduced = row.local?.archive.reduced ?? null;
  const summary = row.local?.retention?.summary ?? null;
  if (reduced === null || reduced === 'deleted' || reduced === 'deleting' || !summary) return null;
  return (
    <ul className="di-scan-stage-note" data-testid="di-scan-retention-summary">
      <li data-testid="di-scan-retention-provenance">
        Recorded final state: {summary.finalState} ({summary.finalStateSource})
      </li>
      {(['verdict', 'ci', 'comments'] as const).map((name) => {
        const fact = summary[name];
        const label = name === 'ci' ? 'CI results' : name === 'verdict' ? 'Verdict' : 'Comments';
        return (
          <li key={name} title={fact.source} data-testid="di-scan-retention-fact" data-fact={name} data-state={fact.state}>
            {label}: {retentionFactWords(fact)} ({fact.source})
          </li>
        );
      })}
    </ul>
  );
}

/**
 * Deliverable 2: the owner's "Delete local data", HOLD-TO-CONFIRM. A single click does nothing: the control fires only after it is held, unbroken, for
 * `DI_DELETE_HOLD_MS` (pointer, or Space or Enter held on the focused control); releasing, leaving or cancelling before then sends nothing. What it sends is
 * rechecked by the panel at the moment of action and refused there with its own reason: this is the button, never the gate.
 */
function HoldToDelete({ onHeld }: { onHeld: () => void }) {
  const timer = useRef<number | null>(null);
  const [holding, setHolding] = useState(false);
  const cancel = useCallback(() => {
    if (timer.current !== null) window.clearTimeout(timer.current);
    timer.current = null;
    setHolding(false);
  }, []);
  const start = useCallback(() => {
    if (timer.current !== null) return;
    setHolding(true);
    timer.current = window.setTimeout(() => {
      timer.current = null;
      setHolding(false);
      onHeld();
    }, DI_DELETE_HOLD_MS);
  }, [onHeld]);
  useEffect(() => cancel, [cancel]);
  const isHoldKey = (key: string) => key === ' ' || key === 'Enter';
  return (
    <button
      type="button"
      className="di-scan-trash di-scan-trash-boxed"
      aria-label={TRASH_LABEL}
      title={TRASH_LABEL}
      data-testid="di-scan-trash"
      data-hold="true"
      data-holding={holding ? 'true' : undefined}
      aria-describedby="di-scan-trash-desc di-scan-trash-reason"
      onClick={(event) => event.preventDefault()}
      onPointerDown={start}
      onPointerUp={cancel}
      onPointerLeave={cancel}
      onPointerCancel={cancel}
      onBlur={cancel}
      onKeyDown={(event) => {
        if (!isHoldKey(event.key)) return;
        event.preventDefault();
        if (!event.repeat) start();
      }}
      onKeyUp={(event) => {
        if (isHoldKey(event.key)) cancel();
      }}
    >
      <TrashIcon />
    </button>
  );
}
/** Where Mission details goes Back to: the page it was opened from (CONTROL-MAP M1). */
type DiScanOrigin = 'list' | 'missions' | 'reception';
const BACK_TO: Record<DiScanOrigin, string> = {
  list: 'Back to Home',
  missions: 'Back to Missions',
  reception: 'Back to Reception',
};
const GATE_WORD: Record<DiDeliveryStepView['gate'], string> = {
  ready: 'Ready',
  locked: 'Locked',
  done: 'Done',
  unlinked: 'No delivery linked',
  withheld: 'Not offered',
};
const GATE_TONE: Record<DiDeliveryStepView['gate'], string> = {
  ready: 'needs',
  locked: 'idle',
  done: 'pass',
  unlinked: 'idle',
  withheld: 'idle',
};

const CODE_KEYS = new Set(['export', 'function', 'const', 'let', 'while', 'return']);
const CODE_TYPES = new Set(['string', 'DiffChange']);

function assetUrl(palette: number): { url: string; bust: boolean } {
  const base = import.meta.env.BASE_URL ?? '/';
  if (palette >= 0 && palette <= 3) {
    return { url: `${base}assets/characters/busts/bust_${palette}.png`, bust: true };
  }
  const index = ((palette % 6) + 6) % 6;
  return { url: `${base}assets/characters/char_${index}.png`, bust: false };
}

/** B2d (a separately named delta): the Code Diff editor is a SCROLLER, and its arrows are its own. The large world's arrow pan (App.tsx) listens on the
 * window and cancels every unmodified arrow that reaches it, so in the metropolis it ate the editor's (an inherited interaction, measured identical at
 * HEAD). The NARROW form of the review dialog's guard (DiCodeReview `handleKey`, which traps far more): stop ONLY the four unmodified arrows the pan takes,
 * ONLY while focus is in the editor, and never prevent their default, so the native scroll runs. Tab, Escape and Cmd+N still reach their listeners. */
function keepArrowsInEditor(event: ReactKeyboardEvent<HTMLElement>): void {
  if (event.ctrlKey || event.metaKey || event.altKey || event.shiftKey) return;
  if (event.key === 'ArrowLeft' || event.key === 'ArrowRight' || event.key === 'ArrowUp' || event.key === 'ArrowDown') event.stopPropagation();
}

function codeTokens(text: string): { text: string; kind: string }[] {
  if (text.trimStart().startsWith('//')) return [{ text, kind: 'note' }];
  const out: { text: string; kind: string }[] = [];
  // The escaped-char alternative and the ordinary-char class are disjoint, so a long run of
  // backslashes cannot make this scan exponential.
  const re = /([A-Za-z_][A-Za-z0-9_]*)|('(?:\\.|[^'\\])*'|"(?:\\.|[^"\\])*")|(\s+)|(.)/g;
  let match: RegExpExecArray | null;
  while ((match = re.exec(text))) {
    const word = match[1];
    if (word) {
      const call = /^\s*\(/.test(text.slice(re.lastIndex));
      const kind = CODE_KEYS.has(word) ? 'key' : call || CODE_TYPES.has(word) ? 'fn' : 'plain';
      out.push({ text: word, kind });
    } else if (match[2]) {
      out.push({ text: match[2], kind: 'str' });
    } else {
      out.push({ text: match[3] ?? match[4] ?? '', kind: 'plain' });
    }
  }
  return out;
}

function Pills({ pills, fill = false }: { pills: readonly DiScanPill[]; fill?: boolean }) {
  return (
    <span className="di-scan-pills">
      {pills.map((pill) => (
        <span
          key={pill.id}
          className="di-scan-pill"
          data-tone={pill.tone}
          data-fill={fill ? 'solid' : 'chip'}
          data-testid="di-scan-pill"
        >
          <StatusMark tone={pill.tone} glyph={pill.glyph} />
          {pill.label}
        </span>
      ))}
    </span>
  );
}

function Avatar({ member, large = false }: { member: DiScanAvatar; large?: boolean }) {
  const art = assetUrl(member.palette);
  const unresolved = member.identity !== undefined && member.identity.kind === 'unresolved';
  const size = large ? 'di-scan-avatar di-scan-avatar-lg' : 'di-scan-avatar';
  return (
    <span
      className={unresolved ? `${size} di-scan-avatar-unresolved` : size}
      data-identity={unresolved ? 'unresolved' : undefined}
      data-bust={art.bust ? 'true' : 'sheet'}
      style={{ backgroundImage: `url("${art.url}")` }}
      role="img"
      aria-label={member.name}
    />
  );
}

/** The label of an UNRESOLVED identity, beside the role. A confirmed member gets no label (nothing to flag); the states stay distinct by `data-identity`. */
function IdentityLabel({ member }: { member: DiScanAvatar }) {
  const identity = member.identity;
  if (identity === undefined || identity.kind === 'confirmed') return null;
  return (
    <>
      <span className="di-scan-identity" data-testid="di-scan-identity" data-identity={identity.state} title={identity.words}>
        {identity.label}
      </span>
      {identity.reason !== null && (
        <span className="di-scan-identity-reason" data-testid="di-scan-identity-reason">
          ({identity.reason})
        </span>
      )}
    </>
  );
}

/**
 * Running agents recorded for a Mission whose identity is NOT resolved and who are not roster members: LISTED, by name, each with its own state
 * label (unverified, ambiguous and could-not-be-checked stay three different facts). Not drawn: no avatar, no activity, no live look.
 */
function UnresolvedAgents({
  agents,
  compact = false,
  folded = false,
  inner = false,
}: {
  agents: readonly DiScanUnresolvedAgent[];
  compact?: boolean;
  folded?: boolean;
  /** Drawn inside the folded variant's detail: the same list, without a second section id. */
  inner?: boolean;
}) {
  if (agents.length === 0) return null;
  // A8 (Mission details): ONE line says how many runtime records are NOT linked to a role, and that they are not team members; the list, each with its own
  // state and reason, is on demand. The line never names them as members and never counts them as the team.
  if (folded)
    return (
      <div className="di-scan-unresolved" data-testid="di-scan-unresolved-agents">
        <p className="di-scan-stage-note di-scan-status-line" data-testid="di-scan-unresolved-status">
          {agents.length === 1
            ? '1 runtime record under this Mission’s workspace is not linked to a role: it is not shown as a team member.'
            : `${agents.length} runtime records under this Mission’s workspace are not linked to a role: they are not shown as team members.`}
        </p>
        <More testid="di-scan-unresolved-more" summary="Each record, with its state and reason">
          <UnresolvedAgents agents={agents} inner />
        </More>
      </div>
    );
  return (
    <div className="di-scan-unresolved" data-testid={inner ? 'di-scan-unresolved-list' : 'di-scan-unresolved-agents'}>
      {!compact && <p className="di-scan-stage-note">Unresolved runtime records found under this Mission’s workspace (not shown as team members, not drawn):</p>}
      <ul className="di-scan-unresolved-list">
        {agents.map((agent) => (
          <li key={agent.agentRef} className="di-scan-unresolved-agent" data-testid="di-scan-unresolved-agent" data-runtime-identity={agent.state}>
            <span className="di-scan-name">{agent.name}</span>
            <span className="di-scan-identity" data-testid="di-scan-unresolved-label">
              {agent.label}
            </span>
            <span className="di-scan-identity-reason" data-testid="di-scan-unresolved-reason">
              ({agent.reasonCode})
            </span>
            {!compact && (
              <span className="di-scan-note" data-testid="di-scan-unresolved-words">
                {agent.words} {agent.reason}.
              </span>
            )}
          </li>
        ))}
      </ul>
    </div>
  );
}

/** A reason as a sentence of its own: capitalized, with its full stop (C-U1: lead-ins and reasons are separate sentences). */
function asSentence(text: string): string {
  const s = text.charAt(0).toUpperCase() + text.slice(1);
  return /[.!?]$/.test(s) ? s : `${s}.`;
}

function displayName(member: DiScanAvatar): string {
  const name = member.name.trim();
  return name !== '' ? name : member.role;
}

function BackIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 16 16" aria-hidden="true">
      <path
        d="M10.5 3.2 5.8 8l4.7 4.8"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.6"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

/** The Comments view's Back arrow (v3 07): a thin stroked arrow in place of the text glyph "←", which this face draws heavier and wider than the reference. Used by the Comments Back link only; the colour is the link's own (currentColor). */
function ArrowLeftIcon() {
  return (
    <svg width="8" height="6" viewBox="0 0 8 6" aria-hidden="true">
      <path
        d="M7.6 3.5H0.4M1.7 2.3 0.4 3.5l1.3 1.2"
        fill="none"
        stroke="currentColor"
        strokeWidth="0.6"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

function DiMark() {
  return (
    <svg viewBox="0 0 45 29" aria-hidden="true" shapeRendering="crispEdges">
      <path
        fill="currentColor"
        d="M0 0h21v1h-21zM32 0h12v1h-12zM0 1h21v1h-21zM32 1h12v1h-12zM0 2h23v1h-23zM32 2h12v1h-12zM0 3h23v1h-23zM32 3h12v1h-12zM0 4h24v1h-24zM32 4h12v1h-12zM0 5h26v1h-26zM32 5h12v1h-12zM0 6h26v1h-26zM34 6h8v1h-8zM0 7h8v1h-8zM19 7h7v1h-7zM35 7h7v1h-7zM0 8h7v1h-7zM19 8h7v1h-7zM35 8h7v1h-7zM0 9h7v1h-7zM19 9h7v1h-7zM35 9h7v1h-7zM0 10h8v1h-8zM19 10h7v1h-7zM35 10h7v1h-7zM0 11h8v1h-8zM19 11h7v1h-7zM35 11h7v1h-7zM0 12h8v1h-8zM19 12h7v1h-7zM35 12h7v1h-7zM0 13h8v1h-8zM19 13h7v1h-7zM35 13h7v1h-7zM0 14h8v1h-8zM19 14h7v1h-7zM35 14h7v1h-7zM0 15h7v1h-7zM19 15h7v1h-7zM35 15h7v1h-7zM0 16h7v1h-7zM19 16h7v1h-7zM35 16h7v1h-7zM0 17h7v1h-7zM19 17h7v1h-7zM35 17h7v1h-7zM0 18h7v1h-7zM19 18h7v1h-7zM35 18h7v1h-7zM0 19h7v1h-7zM19 19h7v1h-7zM35 19h7v1h-7zM0 20h7v1h-7zM19 20h7v1h-7zM35 20h7v1h-7zM0 21h8v1h-8zM19 21h7v1h-7zM35 21h7v1h-7zM0 22h26v1h-26zM34 22h8v1h-8zM0 23h25v1h-25zM32 23h13v1h-13zM0 24h23v1h-23zM32 24h13v1h-13zM0 25h23v1h-23zM32 25h13v1h-13zM0 26h22v1h-22zM32 26h13v1h-13zM0 27h21v1h-21zM32 27h13v1h-13zM0 28h21v1h-21zM32 28h12v1h-12z"
      />
    </svg>
  );
}

/** Mission details' mark (pixel-lock 02): its OWN drawing, traced from the lock's solid violet shape (45 x 35). The stage header chooses it on the Mission view alone; the 45 x 29 asset above is never stretched to fit. */
function DiMarkMission() {
  return (
    <svg viewBox="0 0 45 35" aria-hidden="true" shapeRendering="crispEdges">
      <path
        fill="currentColor"
        d="M0 0h17v1h-17zM29 0h16v1h-16zM0 1h17v1h-17zM29 1h16v1h-16zM0 2h17v1h-17zM29 2h16v1h-16zM0 3h17v1h-17zM29 3h16v1h-16zM0 4h22v1h-22zM29 4h16v1h-16zM0 5h22v1h-22zM29 5h16v1h-16zM0 6h23v1h-23zM29 6h16v1h-16zM0 7h23v1h-23zM29 7h16v1h-16zM0 8h23v1h-23zM24 8h2v1h-2zM30 8h14v1h-14zM1 9h6v1h-6zM16 9h11v1h-11zM33 9h7v1h-7zM1 10h6v1h-6zM16 10h11v1h-11zM33 10h7v1h-7zM1 11h6v1h-6zM16 11h11v1h-11zM33 11h7v1h-7zM0 12h7v1h-7zM17 12h11v1h-11zM33 12h7v1h-7zM0 13h7v1h-7zM19 13h9v1h-9zM33 13h7v1h-7zM0 14h7v1h-7zM20 14h7v1h-7zM33 14h7v1h-7zM0 15h7v1h-7zM19 15h8v1h-8zM33 15h7v1h-7zM0 16h7v1h-7zM19 16h8v1h-8zM33 16h8v1h-8zM0 17h7v1h-7zM19 17h8v1h-8zM33 17h8v1h-8zM0 18h7v1h-7zM19 18h8v1h-8zM33 18h8v1h-8zM0 19h7v1h-7zM19 19h8v1h-8zM33 19h8v1h-8zM0 20h7v1h-7zM19 20h8v1h-8zM33 20h8v1h-8zM0 21h7v1h-7zM19 21h8v1h-8zM33 21h7v1h-7zM1 22h6v1h-6zM19 22h8v1h-8zM33 22h7v1h-7zM1 23h6v1h-6zM17 23h10v1h-10zM33 23h7v1h-7zM1 24h6v1h-6zM14 24h13v1h-13zM33 24h7v1h-7zM1 25h6v1h-6zM13 25h14v1h-14zM33 25h8v1h-8zM0 26h23v1h-23zM29 26h16v1h-16zM0 27h23v1h-23zM29 27h16v1h-16zM0 28h23v1h-23zM29 28h16v1h-16zM0 29h23v1h-23zM29 29h16v1h-16zM0 30h17v1h-17zM29 30h16v1h-16zM0 31h17v1h-17zM29 31h16v1h-16zM0 32h17v1h-17zM29 32h16v1h-16zM0 33h17v1h-17zM29 33h16v1h-16zM1 34h7v1h-7zM14 34h2v1h-2zM30 34h14v1h-14z"
      />
    </svg>
  );
}

function GearIcon() {
  return (
    <svg width="18" height="18" viewBox="0 0 24 24" aria-hidden="true">
      <path
        fill="currentColor"
        fillRule="evenodd"
        d="M9.6 1.8h4.8l.4 2.3c.7.2 1.3.5 1.9.9l2.1-1.1 2.4 2.4-1.1 2.1c.4.6.7 1.2.9 1.9l2.3.4v4.8l-2.3.4c-.2.7-.5 1.3-.9 1.9l1.1 2.1-2.4 2.4-2.1-1.1c-.6.4-1.2.7-1.9.9l-.4 2.3H9.6l-.4-2.3a7 7 0 0 1-1.9-.9l-2.1 1.1-2.4-2.4 1.1-2.1a7 7 0 0 1-.9-1.9l-2.3-.4V9.7l2.3-.4c.2-.7.5-1.3.9-1.9L2.8 5.3 5.2 2.9l2.1 1.1c.6-.4 1.2-.7 1.9-.9l.4-2.3ZM12 8.6a3.4 3.4 0 1 0 .01 0Z"
      />
    </svg>
  );
}

/** The Code Diff header's Settings gear (pixel-lock 03): the SAME gear path as GearIcon drawn as a thin OUTLINE. A separate component on purpose: GearIcon (solid) is also the left rail's Settings icon and stays as it is. */



function SearchIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 16 16" aria-hidden="true">
      <circle cx="7" cy="7" r="4.2" fill="none" stroke="currentColor" strokeWidth="1.5" />
      <path
        d="M10.2 10.2 13.5 13.5"
        stroke="currentColor"
        strokeWidth="1.5"
        strokeLinecap="round"
      />
    </svg>
  );
}

function TrashIcon() {
  return (
    <svg width="15" height="15" viewBox="0 0 24 24" aria-hidden="true">
      <path
        d="M4 7h16M9.2 7V4.6h5.6V7M6.4 7.4l.9 13h9.4l.9-13"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.8"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
      <path
        d="M10 11v6M14 11v6"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.8"
        strokeLinecap="round"
      />
    </svg>
  );
}

function InboxIcon() {
  return (
    <svg width="18" height="18" viewBox="0 0 24 24" aria-hidden="true">
      <path
        d="M3.5 13.5 6 5h12l2.5 8.5V19h-17v-5.5Zm0 0h5l1.4 2.5h4.2l1.4-2.5h5"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.7"
        strokeLinejoin="round"
      />
    </svg>
  );
}

function ListIcon() {
  return (
    <svg width="18" height="18" viewBox="0 0 24 24" aria-hidden="true">
      <rect
        x="4"
        y="4"
        width="16"
        height="16"
        rx="2.5"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.7"
      />
      <path d="M8 9.5h8M8 14.5h8" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" />
    </svg>
  );
}

function HerdIcon() {
  return (
    <svg width="18" height="18" viewBox="0 0 24 24" aria-hidden="true">
      <circle cx="9" cy="9" r="3.2" fill="none" stroke="currentColor" strokeWidth="1.7" />
      <circle cx="16.5" cy="10" r="2.6" fill="none" stroke="currentColor" strokeWidth="1.7" />
      <path
        d="M3.5 19c.6-3 2.8-4.6 5.5-4.6s4.9 1.6 5.5 4.6M14.2 15c2.8-.6 5.4.8 6.3 4"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.7"
        strokeLinecap="round"
      />
    </svg>
  );
}

function LinkIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 16 16" aria-hidden="true">
      <path
        d="M6.6 9.4a3.2 3.2 0 0 0 4.5 0l2-2a3.2 3.2 0 0 0-4.5-4.5l-1.1 1.1"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.4"
        strokeLinecap="round"
      />
      <path
        d="M9.4 6.6a3.2 3.2 0 0 0-4.5 0l-2 2a3.2 3.2 0 0 0 4.5 4.5l1.1-1.1"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.4"
        strokeLinecap="round"
      />
    </svg>
  );
}

function CopyIcon() {
  return (
    <svg width="15" height="15" viewBox="0 0 16 16" aria-hidden="true">
      <rect
        x="5.2"
        y="5.2"
        width="8"
        height="8"
        rx="1.2"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.4"
      />
      <path
        d="M10.6 5.2V3.4A1.2 1.2 0 0 0 9.4 2.2H3.4A1.2 1.2 0 0 0 2.2 3.4v6A1.2 1.2 0 0 0 3.4 10.6H5.2"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.4"
      />
    </svg>
  );
}

function MoreIcon() {
  return (
    <svg width="15" height="15" viewBox="0 0 16 16" aria-hidden="true">
      <circle cx="8" cy="3.2" r="1.1" fill="currentColor" />
      <circle cx="8" cy="8" r="1.1" fill="currentColor" />
      <circle cx="8" cy="12.8" r="1.1" fill="currentColor" />
    </svg>
  );
}

function TwistIcon() {
  return (
    <svg width="12" height="12" viewBox="0 0 12 12" aria-hidden="true">
      <path
        d="M2.6 4.2 6 7.8 9.4 4.2"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.4"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

function FolderIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 16 16" aria-hidden="true">
      <path d="M2 4.2h4l1.2 1.4H14V13H2Z" fill="none" stroke="currentColor" strokeWidth="1.3" />
    </svg>
  );
}

function FileIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 16 16" aria-hidden="true">
      <path d="M4 2.2h5.2L12.8 6V13.8H4Z" fill="none" stroke="currentColor" strokeWidth="1.3" />
    </svg>
  );
}

function BranchIcon() {
  return (
    <svg width="13" height="13" viewBox="0 0 16 16" aria-hidden="true">
      <circle cx="4" cy="3.2" r="1.5" fill="none" stroke="currentColor" strokeWidth="1.3" />
      <circle cx="4" cy="12.8" r="1.5" fill="none" stroke="currentColor" strokeWidth="1.3" />
      <circle cx="12" cy="8" r="1.5" fill="none" stroke="currentColor" strokeWidth="1.3" />
      <path d="M4 4.7v6.6M4 8h6.4" fill="none" stroke="currentColor" strokeWidth="1.3" />
    </svg>
  );
}

function tabLabel(tab: DiScanTab): string {
  return DI_SCAN_TABS.find((item) => item.id === tab)?.label ?? 'Open';
}

function Crumb({
  row,
  view,
  onList,
  onMission,
}: {
  row: DiScanRow;
  view: DiScanView;
  onList: () => void;
  onMission: () => void;
}) {
  const tail =
    view === 'diff'
      ? 'Code Diff'
      : view === 'comments'
        ? 'Comments'
        : view === 'evidence'
          ? DI_EVIDENCE_LABEL
          : '';
  return (
    <nav className="di-scan-crumb" aria-label="Location" data-testid="di-scan-crumb">
      <button type="button" onClick={onList}>
        World
      </button>
      <span aria-hidden="true">/</span>
      <button type="button" onClick={onList}>
        {tabLabel(row.tab)}
      </button>
      <span aria-hidden="true">/</span>
      {/* only the Mission title shrinks for a long name; World, the tab and the screen name stay whole (B-V2) */}
      {tail === '' ? (
        <span className="di-scan-crumb-current di-scan-crumb-title">{row.title}</span>
      ) : (
        <button type="button" className="di-scan-crumb-title" onClick={onMission}>
          {row.title}
        </button>
      )}
      {tail !== '' && (
        <>
          <span aria-hidden="true">/</span>
          <span className="di-scan-crumb-current">{tail}</span>
        </>
      )}
    </nav>
  );
}

function MetaLine({ text }: { text: string }) {
  return (
    <p className="di-scan-meta di-scan-meta-line" data-testid="di-scan-meta">
      <LinkIcon />
      <span>{text}</span>
    </p>
  );
}

/** One swarm fact (a model or a provider), with how it is known as data for the stylesheet. */
function Fact({ fact, testid }: { fact: DiScanFact; testid: string }) {
  return (
    <span className="di-scan-fact" data-testid={testid} data-fact={fact.kind}>
      {fact.text}
    </span>
  );
}

/**
 * Delivery, directly below Swarm (gap 2). There is no merge step (owner ruling, 2026-10-28): the steps end with a statement of what
 * delivery produces (DI_DELIVERY_END), text and never a control.
 * - The specimen, and a real Mission with no linked delivery: each step offered only when the canonical state matches (gap 3),
 *   otherwise locked with its plain reason; a ready step's control is drawn DISABLED, with the layout note. A real Mission also says
 *   why no delivery is read for it (the producer's reason, in words), and offers nothing to read or confirm.
 * - M8, a real Mission whose ONE delivery the catalog links, with Mission details' session: that delivery's status is read on the
 *   person's request, and its eligibility is the adapter's; the one eligible COMMIT, PUSH or PR_CREATE is offered through the b3
 *   LOCAL CHECK (both of the adapter's notes verbatim, the binding, one confirmation and one ask). Accept CI stays unconnected. The read
 *   and the status act only while the session's id is THIS Mission's link, so another Mission's delivery is never read from here.
 */
function Delivery({ row, session, archived }: { row: DiScanRow; session: DiDeliveryControl | null; archived: boolean }) {
  const link = row.deliveryLink;
  const real = link !== undefined;
  const target = deliveryActionTarget(link);
  const setLinked = session?.setLinked;
  // Mission details' session follows the shown Mission's link: its ONE id is this Mission's linked delivery, or none
  useEffect(() => {
    if (real && setLinked !== undefined) setLinked(target);
  }, [real, setLinked, target]);
  const wired = target !== null && session !== null;
  // an origin-only (legacy) link is NOT the session's id (`target` is null for it above): nothing is read or offered on it
  const linkWords = link !== undefined && link.kind !== 'linked' ? link.words : null;
  const withheld = link?.kind === 'origin_only';
  // the session acts for THIS Mission only while its one id is this Mission's link
  const current = session !== null && target !== null && enteredId(session.state) === target;
  const blocked = session === null || session.state.pending !== null || session.state.unvouched !== null;
  // round 10 (lead ruling): archived is READ-ONLY, NOT INFORMATION-ERASING: a linked delivery's status is still read; nothing that acts is offered
  const steps = wired
    ? (archived ? archivedLinkedDeliverySteps : linkedDeliverySteps)(current && session !== null ? session.state.view : null)
    : archived
      ? archivedDeliverySteps()
      : linkWords !== null
      ? unlinkedDeliverySteps(linkWords, withheld)
      : deliverySteps(row.delivery);
  // ONE short status line first; a reason every step shares is said once, in the detail, not in each row (an archived Mission's four rows said it four times)
  const shared = sharedStepReason(steps);
  const line = deliveryStatusLine({ link, archived, target, session: session !== null, steps });
  return (
    <section
      className="di-scan-delivery"
      data-testid="di-scan-delivery"
      aria-labelledby="di-scan-delivery-heading"
    >
      <div className="di-scan-section-head">
        <h2 id="di-scan-delivery-heading" className="di-scan-section-heading" tabIndex={-1}>
          Delivery
        </h2>
        {row.delivery.source === 'fixture' && (
          <span className="di-scan-fixture-tag">Sample state (fixture)</span>
        )}
      </div>
      <p className="di-scan-stage-note di-scan-status-line" data-testid="di-scan-delivery-status">
        {line}
      </p>
      <ol className="di-scan-steps">
        {steps.map((step) => (
          <li
            key={step.id}
            className="di-scan-step"
            data-testid="di-scan-step"
            data-step={step.id}
            data-gate={step.gate}
          >
            <span className="di-scan-step-name">{step.label}</span>
            <span className="di-scan-step-gate">
              <span className="di-scan-dot" data-tone={GATE_TONE[step.gate]} aria-hidden="true" />
              {GATE_WORD[step.gate]}
            </span>
            {step.gate === 'ready' && !wired ? (
              <button
                type="button"
                className="di-scan-step-action"
                disabled
                aria-describedby="di-scan-delivery-note"
              >
                {step.label}
              </button>
            ) : (
              // wired: the one action is the confirmation below, never a control in a step row
              shared === null && <span className="di-scan-step-reason">{step.reason}</span>
            )}
          </li>
        ))}
      </ol>
      <More testid="di-scan-delivery-more" summary={DI_DELIVERY_MORE}>
        {link !== undefined && (
          <p className="di-scan-stage-note" data-testid="di-scan-delivery-link">
            {link.kind === 'linked'
              ? `The delivery linked to this Mission: ${link.deliveryId}. ${link.words}`
              : link.kind === 'origin_only'
                ? `A delivery matched by this workspace’s origin only: ${link.deliveryId}. ${link.words}`
                : link.words}
          </p>
        )}
        {archived && (
          <p className="di-scan-stage-note" data-testid="di-scan-delivery-archived">
            {target !== null ? DI_DELIVERY_ARCHIVED_LINKED : DI_DELIVERY_ARCHIVED}
          </p>
        )}
        {target !== null && session === null && (
          <p className="di-scan-stage-note" data-testid="di-scan-delivery-no-session">
            {DI_DELIVERY_NO_SESSION_WORDS}
          </p>
        )}
        {wired && (
          <>
            <p className="di-scan-stage-note" data-testid="di-scan-delivery-scope">
              {DI_MISSION_DELIVERY_SCOPE}
            </p>
            <p className="di-scan-stage-note" data-testid="di-scan-delivery-store">
              {DI_MISSION_DELIVERY_STORE}
            </p>
          </>
        )}
        {/* the reason every step states alike, said once (it is never one of the words above: an archived step's reason is said by the archived note) */}
        {shared !== null && !(archived && shared === DI_DELIVERY_ARCHIVED) && shared !== linkWords && (
          <p className="di-scan-stage-note" data-testid="di-scan-steps-reason">
            {shared}
          </p>
        )}
        {/* A Herdr task with no delivery this window acts on: how a record is created through the EXISTING human ceremony. The office runs
            nothing and approves nothing; it shows status facts and the exact command to run in the person's own terminal. */}
        {row.deliveryHandoff !== undefined && !wired && <DeliveryHandoff handoff={row.deliveryHandoff} archived={archived} />}
        {/* what delivery produces, as text: there is no merge step and no control for one (owner ruling, 2026-10-28). */}
        <p className="di-scan-stage-note" data-testid="di-scan-delivery-end">
          {DI_DELIVERY_END}
        </p>
      </More>
      {/* the layout note describes the sample's layout-only step controls (a real Mission draws none: its reason is the link note), so it stays drawn, outside the fold */}
      {link === undefined && !wired && (
        <p className="di-scan-stage-note" id="di-scan-delivery-note">
          {archived ? DI_DELIVERY_ARCHIVED : LAYOUT_NOTE}
        </p>
      )}
      {session !== null && target !== null && (
        <>
          <div className="di-op-actions">
            {/* the scan face's own control, the class of the step controls beside it: its edge is the boundary a control needs to be seen.
                The operational face's di-op-button drew here with no edge at all (measured: diMissionDeliveryComposition.test.ts) */}
            <button
              type="button"
              className={`di-scan-step-action ${DI_FOCUS_RING}`}
              data-testid="di-scan-delivery-read"
              disabled={!current || blocked}
              onClick={session.readStatus}
            >
              {DI_MISSION_DELIVERY_READ}
            </button>
          </div>
          {/* the status view's own controls (a notice's Dismiss, the LOCAL CHECK's step) take the scan face's control class too */}
          {current &&
            (archived ? (
              <ArchivedDeliveryStatus control={session} onlyAbout={target} />
            ) : (
              <DiDeliveryStatus control={session} onlyAbout={target} buttonClass="di-scan-step-action" />
            ))}
        </>
      )}
    </section>
  );
}

/**
 * Deliverable 2, round 10 (lead ruling): an ARCHIVED Mission's delivery status, READ-ONLY. It shows what the session's status answer carries (the facts, the step
 * the record names eligible and its binding, a "not eligible" reason) and the notices about this delivery, as text. The two controls that ACT (the LOCAL CHECK
 * confirmation and its one step button) are drawn DISABLED with the archived reason and wired to NOTHING: no handler is attached, so no path can call
 * `setConfirmed` or `askForStep`. The shared `DiDeliveryStatus` (and the standalone panel that uses it) is untouched.
 */
function ArchivedDeliveryStatus({ control, onlyAbout }: { control: DiDeliveryControl; onlyAbout: string }) {
  const { state } = control;
  const view = state.view;
  const waiting = pendingLine(state);
  const notices = state.notices.filter((n) => n.deliveryId === null || n.deliveryId === onlyAbout);
  return (
    <div data-testid="di-scan-delivery-archived-status">
      {waiting !== null && (
        <p className="di-op-entry-note" aria-live="polite" data-testid="di-dl-waiting">
          {waiting}
        </p>
      )}
      {state.unvouched !== null && (
        <p className="di-op-entry-note" data-testid="di-dl-unvouched">
          This window cannot vouch for the office right now ({state.unvouched}), so nothing is shown as current.
        </p>
      )}
      {notices.map((notice) => (
        <div key={notice.id} className="di-op-entry" data-testid="di-dl-notice" data-kind={notice.kind}>
          <p>
            <strong>{notice.heading}</strong>
          </p>
          {notice.deliveryId !== null && <p data-testid="di-dl-notice-subject">About delivery {notice.deliveryId}</p>}
          {notice.text !== '' && <p>{notice.text}</p>}
        </div>
      ))}
      {view !== null && (
        <section data-testid="di-dl-view" aria-label="The delivery status, as the adapter answered it">
          <dl>
            {view.facts.map((fact, index) => (
              <div key={`${index}-${fact.label}`}>
                <dt>{fact.label}</dt>
                <dd>{fact.text}</dd>
              </div>
            ))}
          </dl>
          {view.eligible !== null ? (
            <div data-testid="di-dl-eligible" data-archived="true">
              <p>Eligible now, as the record states it: {view.eligible.step}.</p>
              <dl data-testid="di-dl-binding">
                {(
                  [
                    ['Delivery', view.eligible.delivery_id],
                    ['Revision', String(view.eligible.revision)],
                    ['Candidate digest', view.eligible.candidate_identity_digest_sha256],
                    ['Step', view.eligible.step],
                  ] as const
                ).map(([label, text]) => (
                  <Fragment key={label}>
                    <dt>{label}</dt>
                    <dd>{text}</dd>
                  </Fragment>
                ))}
              </dl>
              <label>
                <input type="checkbox" data-testid="di-dl-confirm" checked={false} readOnly disabled aria-describedby="di-scan-delivery-archived-act" />{' '}
                I confirm this ONE step, {view.eligible.step}, for this displayed delivery (a local check, not new authority).
              </label>
              <div className="di-op-actions">
                <button type="button" className="di-scan-step-action" data-testid="di-dl-advance" disabled aria-describedby="di-scan-delivery-archived-act">
                  Ask the adapter for this one step: {view.eligible.step}
                </button>
              </div>
              <p className="di-scan-stage-note" id="di-scan-delivery-archived-act" data-testid="di-scan-delivery-archived-act">
                {DI_DELIVERY_ARCHIVED_ACT}
              </p>
            </div>
          ) : (
            view.notEligible !== null && (
              <div data-testid="di-dl-not-eligible">
                <p>No step can be confirmed here now ({view.notEligible.reason}).</p>
                <p>{view.notEligible.detail}</p>
              </div>
            )
          )}
        </section>
      )}
    </div>
  );
}

/**
 * A Herdr task's delivery HANDOFF: the observed facts (each with its reason), what stands in the way as things stand, the exact command for the
 * person's OWN terminal, and where a record made from this task would come back. Status only: it confers nothing and is never "ready".
 */
function DeliveryHandoff({ handoff, archived }: { handoff: DiScanDeliveryHandoff; archived: boolean }) {
  // round 10 (lead ruling): an ARCHIVED task keeps its recorded facts and is offered NO creation: no command, no blockers to clear, no return path
  if (archived)
    return (
      <section className="di-scan-delivery-handoff" data-testid="di-scan-delivery-handoff" data-archived="true" aria-labelledby="di-scan-delivery-handoff-heading">
        <h3 id="di-scan-delivery-handoff-heading" className="di-scan-section-heading">
          This task’s recorded delivery facts
        </h3>
        {handoff.facts.length > 0 && (
          <dl className="di-scan-delivery-handoff-facts" data-testid="di-scan-delivery-handoff-facts">
            {handoff.facts.map((fact) => (
              <div key={fact.id} className="di-scan-delivery-handoff-fact" data-fact={fact.id}>
                <dt>{fact.label}</dt>
                <dd>{fact.value}</dd>
              </div>
            ))}
          </dl>
        )}
        <p className="di-scan-stage-note" data-testid="di-scan-delivery-handoff-archived">
          {DI_DELIVERY_ARCHIVED_HANDOFF}
        </p>
      </section>
    );
  return (
    <section className="di-scan-delivery-handoff" data-testid="di-scan-delivery-handoff" aria-labelledby="di-scan-delivery-handoff-heading">
      <h3 id="di-scan-delivery-handoff-heading" className="di-scan-section-heading">
        Creating a delivery record for this task
      </h3>
      {handoff.facts.length > 0 && (
        <dl className="di-scan-delivery-handoff-facts" data-testid="di-scan-delivery-handoff-facts">
          {handoff.facts.map((fact) => (
            <div key={fact.id} className="di-scan-delivery-handoff-fact" data-fact={fact.id}>
              <dt>{fact.label}</dt>
              <dd>{fact.value}</dd>
            </div>
          ))}
        </dl>
      )}
      <p className="di-scan-stage-note" data-testid="di-scan-delivery-handoff-blockers">
        {handoff.blockers}
      </p>
      {handoff.command !== null && (
        <>
          {handoff.guards !== null && (
            <p className="di-scan-stage-note" data-testid="di-scan-delivery-handoff-guards">
              {handoff.guards}
            </p>
          )}
          <pre className="di-scan-delivery-handoff-command" data-testid="di-scan-delivery-handoff-command" tabIndex={0}>
            {handoff.command}
          </pre>
          <p className="di-scan-stage-note" data-testid="di-scan-delivery-handoff-runs-nothing">
            {DI_DELIVERY_HANDOFF_RUNS_NOTHING}
          </p>
        </>
      )}
      <p className="di-scan-stage-note" data-testid="di-scan-delivery-handoff-return" data-return-path={handoff.returnPath.kind}>
        {handoff.returnPath.words}
      </p>
    </section>
  );
}

/** M8: Mission details' Delivery, wired. Its own scope, stated beside the behaviour that keeps it true. */
const DI_MISSION_DELIVERY_SCOPE =
  'This Mission’s delivery is the one record linked to it. No delivery id is typed here, and no other delivery can be chosen. Only notices about this delivery are shown here.';
/** M8, STORE IDENTITY (a limit): the catalog's store and the adapter's are not shown to be one. */
const DI_MISSION_DELIVERY_STORE =
  'The catalog’s delivery store is not shown to be the one the delivery adapter reads. If the adapter has no record of this delivery, its refusal is shown as it answered.';
const DI_MISSION_DELIVERY_READ = 'Read this delivery’s status';

/**
 * The state actions the Mission's state offers, and only those (gap 3). Each is DISABLED except "Decide in the request panel" on a real row, a NAVIGATION into the request panel's own decision, enabled
 * only where that panel can be opened on exactly one proposal; nothing is decided here. Every disabled action on a real row says why in visible text.
 */
function StateActions({
  row,
  onOpenDecision,
}: {
  row: DiScanRow;
  onOpenDecision?: (requestRef: string, opener: HTMLElement) => void;
}) {
  const actions = stateActions(row.state);
  const unread = unreadStateFacts(row.state);
  const decideTarget = row.decision?.count === 1 ? row.decision.requestRef : null;
  const archived = row.tab === 'archived';
  const decideLive = decideTarget !== null && onOpenDecision !== undefined && !archived;
  /** What each real-row action that has its own words says, in the order drawn. */
  const notes = actions.flatMap((action) => {
    if (action.navigate)
      return [
        decideLive
          ? DI_DECIDE_WHAT
          : (row.decision?.count ?? 0) > 1
            ? decideSeveral(row.decision!.count)
            : DI_DECIDE_UNAVAILABLE,
      ];
    return action.reason !== undefined ? [action.reason] : [];
  });
  const anyWithoutOwnWords = actions.some((action) => !action.navigate && action.reason === undefined);
  // ONE short status line first, the rule that holds first: running not known, then Reception's attention, then what is not read, then nothing waiting.
  // Unread is not absent: where a fact is not read it is said (in the line or, behind it, in the detail), never "nothing is waiting".
  const runningUnknown = row.state.running.known
    ? null
    : `Whether this Mission is running is not known: ${row.state.running.why.text}.`;
  // as before: Reception's attention is said beside what is not read (where every fact is read, the actions themselves say what waits)
  const needsYou = unread.length > 0 && knownTrue(row.needsYou) ? 'Reception lists this Mission as needing you.' : null;
  const unreadWords = unread.length > 0 ? STATE_NOT_READ : null;
  const nothingWaits = unread.length === 0 && actions.length === 0 ? 'Nothing is waiting on you here.' : null;
  const line = runningUnknown ?? needsYou ?? unreadWords ?? nothingWaits;
  // everything else this section says, on demand (each said once)
  const rest = [runningUnknown, needsYou, unreadWords].filter((words): words is string => words !== null && words !== line);
  const reasons = actions.length > 0 && (notes.length > 0 || archived) ? (archived ? [ARCHIVED_READ_ONLY, ...notes] : notes) : [];
  const layoutNote =
    actions.length > 0 && (anyWithoutOwnWords || row.state.source === 'fixture')
      ? row.state.source === 'fixture'
        ? `${LAYOUT_NOTE} Sample state (fixture).`
        : LAYOUT_NOTE
      : null;
  return (
    <section
      className="di-scan-state"
      data-testid="di-scan-state-actions"
      aria-label="Actions for this Mission"
    >
      {line !== null && (
        <p
          className="di-scan-stage-note di-scan-status-line"
          data-testid={line === runningUnknown ? 'di-scan-state-running-unknown' : line === unreadWords ? 'di-scan-state-unread' : 'di-scan-state-status'}
        >
          {line}
        </p>
      )}
      {actions.length > 0 && (
        <div className="di-scan-state-row">
          {actions.map((action) => {
            const live = action.navigate === true && decideLive;
            return (
              <button
                key={action.id}
                type="button"
                className="di-scan-state-action"
                data-action={action.id}
                data-hold={action.hold ? 'true' : undefined}
                disabled={!live}
                aria-describedby={action.navigate || action.reason !== undefined ? 'di-scan-state-reasons-desc' : 'di-scan-state-note-desc'}
                onClick={live ? (event) => onOpenDecision!(decideTarget!, event.currentTarget) : undefined}
              >
                {action.label}
              </button>
            );
          })}
        </div>
      )}
      {/* each action is DESCRIBED by an always-present copy of its reasons (a closed disclosure's content is computed into no description) */}
      {reasons.length > 0 && (
        <span className="di-scan-visually-hidden" id="di-scan-state-reasons-desc">
          {reasons.join(' ')}
        </span>
      )}
      {layoutNote !== null && (
        <span className="di-scan-visually-hidden" id="di-scan-state-note-desc">
          {layoutNote}
        </span>
      )}
      {(rest.length > 0 || reasons.length > 0 || layoutNote !== null) && (
        <More testid="di-scan-state-more" summary="Why, and what is not reported">
          {rest.map((words) => (
            <p key={words} className="di-scan-stage-note" data-testid={words === runningUnknown ? 'di-scan-state-running-unknown' : words === unreadWords ? 'di-scan-state-unread' : 'di-scan-state-attention'}>
              {words}
            </p>
          ))}
          {reasons.length > 0 && (
            <ul className="di-scan-stage-note" id="di-scan-state-reasons" data-testid="di-scan-state-reasons">
              {reasons.map((note) => (
                <li key={note}>{note}</li>
              ))}
            </ul>
          )}
          {layoutNote !== null && (
            <p className="di-scan-stage-note" id="di-scan-state-note">
              {layoutNote}
            </p>
          )}
        </More>
      )}
    </section>
  );
}

/** Fixture mode's label at the top of a stage or page: the screens cover the panel's own banner, so each carries it (B-E1). */
function StageFixture({ text }: { text: string | null }) {
  if (text === null) return null;
  return (
    <p className="di-scan-stage-fixture" role="note" data-testid="di-fixture-banner-in-stage">
      {text}
    </p>
  );
}

/** Code Diff, Herdr and Comments, side by side on a Mission's nested screens (CONTROL-MAP C2, v3 06-code-diff.png). */
function Siblings({
  view,
  onView,
  herdr,
  missionRef,
}: {
  view: 'diff' | 'comments';
  onView: (view: 'diff' | 'comments') => void;
  herdr: DiHerdrControl | null | undefined;
  missionRef: string;
}) {
  const hv = herdrViewFor(herdr, missionRef);
  return (
    <nav className="di-scan-siblings" aria-label="Mission screens" data-testid="di-scan-siblings">
      <button
        type="button"
        className="di-scan-sibling"
        data-testid="di-scan-sibling-diff"
        aria-current={view === 'diff' ? 'page' : undefined}
        onClick={() => onView('diff')}
      >
        Code Diff
      </button>
      <button
        type="button"
        className="di-scan-sibling"
        data-testid="di-scan-sibling-herdr"
        title={hv.note}
        aria-describedby="di-scan-sibling-herdr-reason"
        aria-busy={hv.busy}
        disabled={!hv.enabled}
        onClick={() => herdr?.launch(missionRef)}
      >
        Herdr
      </button>
      <button
        type="button"
        className="di-scan-sibling"
        data-testid="di-scan-sibling-comments"
        aria-current={view === 'comments' ? 'page' : undefined}
        onClick={() => onView('comments')}
      >
        Comments
      </button>
      <span className="di-scan-visually-hidden" id="di-scan-sibling-herdr-reason">
        {hv.note}
      </span>
      {/* the header is too narrow for a note that is always there: the result of a click is shown here, and the reason a disabled Herdr is disabled is its title and its description */}
      {hv.outcome && (
        <p className="di-scan-stage-note" data-testid="di-scan-herdr-open" data-tone={hv.tone}>
          {hv.line}
        </p>
      )}
    </nav>
  );
}

/**
 * B2b (the Lead, 2026-11-05): with a screen docked beside the list, the list must be cheap to reach by keyboard WITHOUT dismissing the screen (Escape
 * and Back both close it). This is the screen's FIRST stop (one Shift+Tab from Back), shown only while focused, like a skip link; Enter moves focus to
 * the docked Mission's row. Two panes only: in one pane the list is covered.
 */
function ToListLink({ onToList }: { onToList: () => void }) {
  return (
    <button type="button" className="di-scan-to-list" data-testid="di-scan-to-list" onClick={onToList} aria-label="Go to the Mission list; this Mission stays open">
      Go to the Mission list
    </button>
  );
}

function Stage({
  view,
  row,
  origin,
  focusDelivery,
  onDeliveryFocused,
  onList,
  onBack,
  onMission,
  onView,
  onOpenSettings,
  reviewSession,
  onReviewBegin,
  onReviewEnd,
  onReviewChoose,
  onReviewRefresh,
  onOpenDecision,
  renderEvidence,
  fixtureBanner,
  missionDelivery,
  herdr,
  onDeleteLocalData,
  deleteResults,
  rightPx,
  onToList,
  initialMoreActionsOpen = false,
  filesOpen,
  onToggleFiles,
  wrapCode,
  onToggleWrap,
}: {
  view: 'mission' | 'diff' | 'comments' | 'evidence';
  row: DiScanRow;
  /** B2b: the docked right edge in two-pane mode (undefined: one pane, the stage covers the list). */
  rightPx?: number;
  /** B2c: "More actions" starts open (the specimen and static-markup tests; DiScanPanelProps.initialMoreActionsOpen). */
  initialMoreActionsOpen?: boolean;
  /** B2b: two panes only, the cheap keyboard path to the list that keeps this Mission docked. */
  /** B2d: whether the Code Diff's file tree is shown (the panel remembers it for the session), and the labelled control that changes it. */
  filesOpen: boolean;
  onToggleFiles: () => void;
  /** B3: the optional soft wrap of the code (a display preference; off by default; kept for the life of the panel). */
  wrapCode: boolean;
  onToggleWrap: () => void;
  onToList?: () => void;
  /** Where Back from Mission details goes (M1). */
  origin: DiScanOrigin;
  /** Opened from Reception's Open Mission: bring Delivery into view and focus (R11). */
  focusDelivery: boolean;
  onDeliveryFocused: () => void;
  onList: () => void;
  onBack: () => void;
  onMission: () => void;
  onView: (view: 'mission' | 'diff' | 'comments' | 'evidence') => void;
  onOpenSettings?: () => void;
  reviewSession?: ReviewSession | null;
  onReviewBegin?: (target: ReviewTargetRef) => void;
  onReviewEnd?: () => void;
  onReviewChoose?: (path: string | null) => void;
  onReviewRefresh?: () => void;
  onOpenDecision?: (requestRef: string, opener: HTMLElement) => void;
  renderEvidence?: (missionRef: string) => ReactNode;
  fixtureBanner: string | null;
  /** M8: Mission details' own delivery session (null: none). */
  missionDelivery: DiDeliveryControl | null;
  /** Deliverable 2: the owner's "Delete local data" (absent: no request panel to delete through). */
  onDeleteLocalData?: (missionRef: string, missionId: string, displayedState: string, storeIdentity: string | null) => void;
  /** Deliverable 2, round 9: each Mission's latest owner delete, by Mission id. */
  deleteResults?: Readonly<Record<string, MrDeletion>>;
  /** The Herdr control (null: none is wired, and the Herdr buttons say so). */
  herdr: DiHerdrControl | null;
}) {
  // The Herdr buttons are enabled only by the office's own answer for THIS Mission, so that answer is asked for when the Mission is opened (the stage is keyed per Mission), and again when the office
  // becomes heard from after being lost. It is a READ: it runs the route's admission and opens, focuses and writes nothing. Nothing polls it.
  const requestHerdrStatus = herdr?.requestStatus;
  const herdrUnvouched = herdr?.state.unvouched ?? null;
  useEffect(() => {
    if (herdrUnvouched === null) requestHerdrStatus?.(row.id);
  }, [requestHerdrStatus, herdrUnvouched, row.id]);
  const herdrView = herdrViewFor(herdr, row.id);
  // A8: the details text and the roster's detail, each shown in full on demand (the stage is keyed per Mission: each Mission opens folded)
  const [detailsOpen, setDetailsOpen] = useState(false);
  const [rosterOpen, setRosterOpen] = useState(false);
  // A7: the office's full words are offered on demand wherever the line does not already say them all. On an archived Mission the line is the archived
  // reason, so the office's own answer (still read: archived is read-only, not information-erasing) is always one disclosure away.
  const herdrMore = row.tab === 'archived' || herdrView.note !== herdrView.line;
  const herdrMoreLabel = row.tab === 'archived' ? HERDR_ANSWER_MORE : herdrView.tone === 'refused' ? HERDR_REASON_MORE : HERDR_WHAT_MORE;
  // B2c: the Herdr item is held exactly where the button was disabled, and its reason is the status line's own words
  const herdrHeld = row.tab === 'archived' ? HERDR_ARCHIVED : herdrView.enabled ? null : herdrView.line;
  // Round 9: this Mission's own latest owner delete, by its SOURCE-QUALIFIED ref (never shown on another row that shares a native id)
  const deletion = row.local ? deleteResults?.[row.id] : undefined;
  // the trash's scope, in the tense that holds now: an unfinished deletion is never said as done, drawn or spoken
  const trashWords = deletionUnfinished(row, deletion) ? TRASH_KEEPS_UNFINISHED : TRASH_KEEPS;
  // R10: the evidence screen opens only where the office lists a library for this Mission AND the office can draw it here
  const evidenceLive = row.evidenceListed === true && renderEvidence !== undefined;
  const evidenceReason = evidenceLive
    ? null
    : row.evidenceListed === true
      ? DI_EVIDENCE_NOT_AVAILABLE
      : DI_EVIDENCE_NOT_LISTED;
  // a diff nobody has read is NOT an empty diff, a Free thread is unavailable, not empty (B-H1), and (Stage 4) the working tree the
  // office READ for THIS Mission is the only real data here: the specimen first, then the session only if it is this Mission's
  const source = scanDiffFor(row, reviewSession ?? null);
  const diff = source.kind === 'sample' || source.kind === 'read' ? source.diff : null;
  const placeholder = source.kind === 'sample' || source.kind === 'read' ? null : source;
  const thread = scanThread(row);
  const [fileQuery, setFileQuery] = useState('');
  // 1C G3: a control that succeeds by ceasing to exist owes a SURVIVING destination BEFORE it goes. Refresh's status block is replaced by the refreshed diff, so focus lands on the editor bar's PATH label (the refreshed
  // content's own name), which persists through the refusal, the reading and the content. tabIndex -1 there ADDS a capability and leaves the Tab order as it was. GUARDED: the landing happens only while focus is still
  // where Refresh left it (inside the editor) or on nothing; a deliberate move elsewhere (the Home list, the tree) drops the intent, so a late read never steals it.
  const landOnPath = useRef(false);
  // what the intent was made against: the refusal showing when Refresh was used (an interim render can still show it)
  const landRefusal = useRef<unknown>(null);
  // NO DEADLINE (the review hook has no answer timeout) and NO key or pointer listener (a modifier or an Arrow pressed on the reading placeholder relocates nothing and must not cancel the only handoff). The handoff waits for as long as the read takes and ends when it lands, when focus is NOT where Refresh left it or on nothing (a direct test of a deliberate move), when a NEW refusal arrives, or when this view unmounts
  const wantLanding = () => {
    landOnPath.current = true;
    landRefusal.current = reviewSession?.refusal ?? null;
  };
  const pathRef = useRef<HTMLSpanElement>(null);
  useEffect(() => {
    if (!landOnPath.current) return;
    const active = document.activeElement;
    const mine = active === null || active === document.body || (active instanceof HTMLElement && active.closest('.di-scan-editor') !== null);
    // a refusal is a reason to give up only if it is a NEW one: the first render after the click can still show the OLD refusal
    if (!mine) landOnPath.current = false;
    else if (pathRef.current !== null) {
      landOnPath.current = false;
      pathRef.current.focus();
    } else if (placeholder !== null && placeholder.kind !== 'reading' && !(placeholder.kind === 'refused' && (reviewSession?.refusal ?? null) === landRefusal.current)) landOnPath.current = false; // a new refusal, or nothing to land on
  });
  const [findOpen, setFindOpen] = useState(false);
  // 1B: Find a file lives in the More popover. When it OPENS the box, focus goes to the search input (its intentional destination); closing it returns to the trigger.
  const findInputRef = useRef<HTMLInputElement>(null);
  const focusFindOnOpen = useRef(false);
  useEffect(() => {
    if (!findOpen || !focusFindOnOpen.current) return;
    focusFindOnOpen.current = false;
    findInputRef.current?.focus();
  }, [findOpen, filesOpen]);
  // C3: the file shown in the diff pane; picking another file in the tree shows its changes (read only). The specimen keeps its own
  // choice; a real listing's choice lives in the review session (by path, so it survives a new snapshot)
  const [sampleFileId, setSampleFileId] = useState<string | null>(() => (source.kind === 'sample' ? source.diff.initial : null));
  const fileId = source.kind === 'read' ? (source.diff.initial === '' ? null : source.diff.initial) : sampleFileId;
  const chooseFile = (item: { id: string; path?: string }) => {
    if (source.kind === 'read') onReviewChoose?.(item.path ?? null);
    else setSampleFileId(item.id);
  };

  // The review session follows THIS stage: opened for the offered reference when Code Diff opens, closed when the reader leaves it,
  // switches Mission (the stage is keyed per Mission) or the office's offer moves. Primitives only, so a new office state with the
  // same offer never restarts a read.
  const offer = row.reviewOffer;
  const refMission = offer.kind === 'offered' ? offer.target.missionRef : null;
  const refMember = offer.kind === 'offered' ? offer.target.memberRef : null;
  const refTask = offer.kind === 'offered' ? offer.target.taskId : null;
  const refListing = offer.kind === 'offered' ? offer.target.listing : null;
  const refAgent = offer.kind === 'offered' ? offer.target.agentName : null;
  // the reference kind: a finished Mission is read by a Mission-level reference (no member), an active one by a member-bound reference
  const refLevel = offer.kind === 'offered' ? (offer.target.level ?? null) : null;
  useEffect(() => {
    if (view !== 'diff' || refMission === null || refMember === null || refTask === null || refListing === null) return;
    if (!onReviewBegin) return;
    onReviewBegin({
      missionRef: refMission,
      memberRef: refMember,
      taskId: refTask,
      listing: refListing,
      agentName: refAgent ?? '',
      ...(refLevel === null ? {} : { level: refLevel }),
    });
    return () => onReviewEnd?.();
  }, [view, refMission, refMember, refTask, refListing, refAgent, refLevel, onReviewBegin, onReviewEnd]);
  // the first changed file the route can show opens first, as the composition draws its first file open
  const firstToChoose = source.kind === 'read' && source.selectedPath === null ? source.firstOpenablePath : null;
  useEffect(() => {
    if (firstToChoose !== null) onReviewChoose?.(firstToChoose);
  }, [firstToChoose, onReviewChoose]);
  const tree = useMemo(() => {
    if (!diff) return [];
    const q = fileQuery.trim().toLowerCase();
    if (q === '') return diff.tree;
    return diff.tree.filter(
      (entry) => entry.kind === 'dir' || entry.name.toLowerCase().includes(q),
    );
  }, [diff, fileQuery]);
  const entry = diff?.tree.find((item) => item.id === fileId) ?? null;
  const file = diff && fileId !== null ? (diff.files[fileId] ?? null) : null;

  useEffect(() => {
    if (!focusDelivery || view !== 'mission') return;
    const heading = document.getElementById('di-scan-delivery-heading');
    heading?.scrollIntoView({ block: 'center' });
    heading?.focus();
    onDeliveryFocused();
  }, [focusDelivery, view, onDeliveryFocused]);

  // 1B (instance 6 of the silent-no-op class): Copy's OUTCOME is said, locally, in all three cases: copied; the clipboard ABSENT; the clipboard REFUSED. The text copied is UNCHANGED (the same lines joined by newlines).
  const [copyResult, setCopyResult] = useState<{ path: string; ok: boolean; reason: string | null } | null>(null);
  // HELD COPY: while the held control has FOCUS, its reason is DRAWN in the result slot (a title and screen-reader text are not drawn). The status carrier is untouched: nothing was copied, so nothing is announced as copied.
  const [copyFocused, setCopyFocused] = useState(false);
  const copyDiff = () => {
    if (!file || file.lines.length === 0) return; // a held Copy does nothing: a file object is not content (see copyReason), so nothing is written and no success is said
    const path = file.path;
    const text = file.lines.map((line) => line.text).join('\n');
    const clip: Clipboard | undefined = navigator.clipboard;
    if (clip === undefined || typeof clip.writeText !== 'function') {
      setCopyResult({ path, ok: false, reason: 'the clipboard is not available here' });
      return;
    }
    void clip.writeText(text).then(
      () => setCopyResult({ path, ok: true, reason: null }),
      (error: unknown) => setCopyResult({ path, ok: false, reason: error instanceof Error && error.name === 'NotAllowedError' ? 'the clipboard refused (permission was not given)' : 'the clipboard refused' }),
    );
  };
  // 1B: a control that is drawn available IS available, and a FILE OBJECT IS NOT CONTENT: a selected file can carry no lines at all (withheld, binary, too large, not UTF-8, unsupported, still reading, refused,
  // or a real file with nothing to show), so Copy is held there too, with that notice's own reason. KNOWN-EMPTY (a real file with no changed lines) and UNREAD (its changes were not or could not be read) do not share words.
  const copyReason: string | null = (() => {
    if (!file) return 'Choose a file in the tree first; Copy then copies the lines of that file.';
    if (file.lines.length > 0) return null;
    const notice = file.notice;
    if (notice === undefined) return 'There are no lines to copy.';
    // the notice's OWN reason: the first sentence of the words the pane already shows for this state (its title when it has no body)
    const own = (notice.body.split('. ')[0] ?? '').replace(/\.$/, '') || notice.title;
    if (notice.state === 'empty' || notice.state === 'no_content_diff') return `This file has no changed lines, so there is nothing to copy. ${own}.`;
    if (notice.state === 'text' || notice.state === 'submodule') return 'The read returned no lines for this file, so there is nothing to copy.';
    if (notice.state === 'reading') return 'Its changes are still being read. Copy is available once they arrive.';
    if (notice.state === 'refused') return `Its changes could not be read, so there is nothing to copy. ${own}.`;
    return `Its changes are not shown, so there is nothing to copy. ${own}.`;
  })();
  // What is copied is the lines as SHOWN, joined by newlines: hunk headers and the \ No newline marks included, a clipped line with its clipping note, and NOT the + and − marks or the line numbers. It is not a patch, so no word here says it is.
  const copyWords =
    copyResult === null || file === null || copyResult.path !== file.path
      ? ''
      : copyResult.ok
        ? 'Copied the lines shown, without the + and − marks or the line numbers.'
        : `Could not copy: ${copyResult.reason}. Select the lines in the diff and copy them yourself.`;
  const heldWords = copyReason !== null && copyFocused ? copyReason : '';

  return (
    <div
      className="di-scan-stage"
      data-testid="di-scan-stage"
      // K1: this screen covers the world while it is drawn (WorldMap.tsx reads it at the key, with its rendered box)
      data-di-covers-world=""
      data-view={view}
      data-mission-ref={row.id}
      // the stage starts at the rail's edge (the world yields the same width), so the rail stays beside it; in two-pane mode it ends at the list (B2b)
      style={{ left: DI_RAIL_WIDTH_PX, ...(rightPx !== undefined ? { right: `calc(${rightPx}px + var(--scan-tray-w, 0px))` } : {}) }}
    >
      {onToList && <ToListLink onToList={onToList} />}
      <StageFixture text={fixtureBanner} />
      <header className="di-scan-stage-bar">
        <div className="di-scan-header-lead">
          <button
            type="button"
            className="di-scan-back"
            onClick={view === 'mission' ? onBack : onMission}
            aria-label={view === 'mission' ? BACK_TO[origin] : 'Back to mission details'}
            data-testid="di-scan-back"
          >
            {view === 'comments' ? (
              <span className="di-scan-back-text" aria-hidden="true">
                <ArrowLeftIcon />
                Back
              </span>
            ) : (
              <BackIcon />
            )}
          </button>
          {view !== 'comments' && (
            <span className="di-scan-mark" aria-hidden="true">
              {view === 'mission' ? <DiMarkMission /> : <DiMark />}
            </span>
          )}
          {view !== 'comments' && (
            <Crumb row={row} view={view} onList={onList} onMission={onMission} />
          )}
        </div>
        {/* the locked three siblings (Code Diff, Herdr, Comments) stay three: a fourth wraps the Code Diff header to two rows at the locked widths (diScanHeaderRegion), so
            "Evidence and review" is entered from Mission details and returns there */}
        {view === 'comments' && <Siblings view={view} onView={onView} herdr={herdr} missionRef={row.id} />}
        {/* Code Diff: the tabs and the tools are ONE wrapping unit, so a header too narrow for them beside the crumb moves them TOGETHER to their own row (layout only: no role, no
            label, DOM order unchanged; see the rules at "the stage header wraps by content" in diScanPages.css) */}
        {view === 'diff' && (
          <div className="di-scan-header-controls">
            <Siblings view={view} onView={onView} herdr={herdr} missionRef={row.id} />
            <div className="di-scan-header-tools">
              {diff && (
                <>
                  <button
                    type="button"
                    className="di-scan-icon-button"
                    aria-label="Copy the lines shown"
                    data-testid="di-scan-diff-copy"
                    aria-disabled={copyReason !== null ? 'true' : undefined}
                    aria-describedby={copyReason !== null ? 'di-scan-diff-copy-reason' : undefined}
                    title={copyReason ?? undefined}
                    onFocus={() => setCopyFocused(true)}
                    onBlur={() => setCopyFocused(false)}
                    onClick={copyDiff}
                  >
                    <CopyIcon />
                  </button>
                  {copyReason !== null && (
                    <span className="di-scan-visually-hidden" id="di-scan-diff-copy-reason" data-testid="di-scan-diff-copy-reason">
                      {copyReason}
                    </span>
                  )}
                </>
              )}
              {/* 1B: the Code Diff utilities are secondaries: ONE anchored, non-modal popover in the place of the disabled More. Copy the diff stays direct. */}
              <AnchoredPopover label="More" dialogLabel="Code Diff options" triggerClassName="di-page-button" testid="di-scan-diff-more">
                {(dismiss) => (
                  <>
                    {diff && (
                      <>
                        <PopoverItem
                          label="Find a file"
                          testid="di-scan-diff-find"
                          pressed={findOpen && filesOpen}
                          onSelect={() => {
                            // B2d (round 20): Find searches the FILE TREE, so with the tree collapsed it OPENS the tree and its box. Opening goes to the search input; closing, to the trigger.
                            const opening = !(findOpen && filesOpen);
                            if (!filesOpen) {
                              onToggleFiles();
                              setFindOpen(true);
                            } else setFindOpen((open) => !open);
                            focusFindOnOpen.current = opening;
                            dismiss(opening ? 'in-place' : 'to-trigger');
                          }}
                        />
                      </>
                    )}
                    <PopoverItem
                      label="Settings"
                      testid="di-scan-diff-settings"
                      held={onOpenSettings === undefined ? 'Settings are not available in this office' : null}
                      onSelect={() => {
                        dismiss('to-trigger');
                        onOpenSettings?.();
                      }}
                    />
                  </>
                )}
              </AnchoredPopover>
            </div>
          </div>
        )}
      </header>

      {view === 'mission' && (
        <div className="di-scan-mission" data-testid="di-scan-mission">
          <h1 className="di-scan-mission-title" data-testid="di-scan-mission-title">
            {row.title}
          </h1>
          <Pills pills={row.pills} fill />
          <section
            className="di-scan-card"
            data-testid="di-scan-details"
            data-expanded={detailsOpen ? 'true' : 'false'}
            aria-labelledby="di-scan-details-heading"
          >
            <h2 id="di-scan-details-heading">Mission details</h2>
            {/* A8/A9: the description leads as ONE line (the whole text is in the document, drawn to one line); it, and the handoff paragraphs, are shown in full on
                demand. Folded, the card is sized by what it shows (no reserved height); shown in full it keeps its earlier frame, the body scrolling inside it. */}
            <div
              className="di-scan-details-body"
              data-testid="di-scan-details-body"
              data-expanded={detailsOpen ? 'true' : 'false'}
              id="di-scan-details-body"
              tabIndex={0}
              role="region"
              aria-label="Mission details text"
            >
              {row.description !== '' && <p className="di-scan-details-text" data-testid="di-scan-details-text">{row.description}</p>}
              {row.handoff !== '' &&
                row.handoff.split('\n').map((line) => (
                  <p key={line} className="di-scan-delivery-handoff" data-testid="di-scan-details-handoff">
                    {line}
                  </p>
                ))}
            </div>
            {(row.description !== '' || row.handoff !== '') && (
              <button
                type="button"
                className="di-scan-more-toggle"
                data-testid="di-scan-details-toggle"
                aria-expanded={detailsOpen}
                aria-controls="di-scan-details-body"
                onClick={() => setDetailsOpen((open) => !open)}
              >
                {detailsOpen ? DETAILS_LESS : row.handoff !== '' ? DETAILS_MORE_HANDOFF : DETAILS_MORE}
              </button>
            )}
            <div className="di-scan-card-foot">
              <MetaLine text={row.footer} />
              {row.updated !== '' && <p className="di-scan-updated">{row.updated}</p>}
            </div>
          </section>
          <h2 className="di-scan-swarm-heading">Swarm</h2>
          {/* A9: the Swarm leads with ONE summary line (how many roles are listed, and how many identities are NOT resolved: it never reads as all resolved); the
              roster, every card exactly as before, is shown on demand */}
          {row.swarm.length > 0 && (
            <p className="di-scan-stage-note di-scan-status-line" data-testid="di-scan-swarm-status">
              {swarmSummary(row.swarm)}
            </p>
          )}
          {row.swarm.length > 0 && (
            <button
              type="button"
              className="di-scan-more-toggle"
              data-testid="di-scan-swarm-toggle"
              aria-expanded={rosterOpen}
              aria-controls="di-scan-swarm-roster"
              onClick={() => setRosterOpen((open) => !open)}
            >
              {rosterOpen ? ROSTER_LESS : ROSTER_MORE}
            </button>
          )}
          <div className="di-scan-swarm-box" data-expanded={rosterOpen || row.swarm.length === 0 ? 'true' : 'false'}>
            {row.swarm.length === 0 ? (
              <p className="di-scan-stage-note">No swarm is recorded for this Mission.</p>
            ) : (
              <ul className="di-scan-cards" data-testid="di-scan-swarm" id="di-scan-swarm-roster">
                {row.swarm.map((member) => (
                  <li
                    key={member.role}
                    className="di-scan-card-member"
                    data-testid="di-scan-swarm-member"
                  >
                    <Avatar member={member} large />
                    <span className="di-scan-card-copy">
                      <span className="di-scan-role">{member.role}</span>
                      <span className="di-scan-name">{displayName(member)}</span>
                      <IdentityLabel member={member} />
                      {member.identity !== undefined && member.identity.kind === 'unresolved' && (
                        <span className="di-scan-note" data-testid="di-scan-identity-words">
                          {member.identity.words}
                        </span>
                      )}
                      {member.note !== '' && <span className="di-scan-note">{member.note}</span>}
                      <Fact fact={member.model} testid="di-scan-model" />
                      <Fact fact={member.provider} testid="di-scan-provider" />
                    </span>
                  </li>
                ))}
              </ul>
            )}
          </div>
          <UnresolvedAgents agents={row.unresolvedAgents ?? []} folded />
          {row.tab === 'archived' && (
            <p className="di-scan-stage-note" data-testid="di-scan-archived-note">
              {ARCHIVED_READ_ONLY}
            </p>
          )}
          <Delivery row={row} session={missionDelivery} archived={row.tab === 'archived'} />
          <StateActions row={row} onOpenDecision={onOpenDecision} />
          <div className="di-scan-actions">
            <button
              type="button"
              className="di-scan-action di-scan-action-diff"
              data-testid="di-scan-code-diff"
              onClick={() => onView('diff')}
            >
              Code Diff
            </button>
            {/* B2c: Code Diff stays the one primary; the secondaries are in ONE anchored, non-modal popover (diPopover.ts). A held item says why, in
                the item, and stays focusable. Herdr's status line, its disclosure and Check again stay in the notes row below (the Lead's ruling (i):
                A1, a section leads with its status line). */}
            <AnchoredPopover
              label="More actions"
              dialogLabel="More actions"
              triggerClassName="di-scan-action di-scan-action-secondary"
              testid="di-scan-more-actions"
              initialOpen={initialMoreActionsOpen}
            >
              {(dismiss) => (
                // Comments and Evidence NAVIGATE: Mission details unmounts with the popover in it, and the unmount releases the claim (diPopover.ts), so the
                // next Escape peels the new screen as usual. Herdr does not navigate: it closes the popover with focus back on the trigger.
                <>
                  <PopoverItem
                    label="Comments"
                    testid="di-scan-comments"
                    onSelect={() => onView('comments')}
                  />
                  <PopoverItem
                    label={DI_EVIDENCE_LABEL}
                    testid="di-scan-evidence"
                    held={evidenceReason}
                    heldId="di-scan-evidence-reason"
                    onSelect={() => onView('evidence')}
                  />
                  <PopoverItem
                    label="Herdr"
                    testid="di-scan-herdr"
                    title={herdrView.note}
                    held={herdrHeld}
                    heldId="di-scan-herdr-held"
                    describedBy={
                      herdrHeld !== null
                        ? herdrMore
                          ? 'di-scan-herdr-desc'
                          : undefined
                        : herdrMore
                          ? 'di-scan-herdr-reason di-scan-herdr-desc'
                          : 'di-scan-herdr-reason'
                    }
                    busy={herdrView.busy}
                    onSelect={() => {
                      dismiss('to-trigger');
                      herdr?.launch(row.id);
                    }}
                  />
                </>
              )}
            </AnchoredPopover>
            {trashGate(row).open && onDeleteLocalData && row.local && !deletionWithdraws(deletion) ? (
              <HoldToDelete
                onHeld={() =>
                  // the SOURCE-QUALIFIED Mission and its store, never a bare native id (target identity)
                  onDeleteLocalData(row.id, row.local!.nativeId, row.local!.displayedState, row.local!.storeIdentity)
                }
              />
            ) : (
              <button
                type="button"
                className="di-scan-trash di-scan-trash-boxed"
                aria-label={TRASH_LABEL}
                title={TRASH_LABEL}
                data-testid="di-scan-trash"
                disabled
                aria-describedby="di-scan-trash-desc di-scan-trash-reason"
              >
                <TrashIcon />
              </button>
            )}
          </div>
          <div className="di-scan-actions-notes">
            {/* Herdr's ONE line (the rail does not repeat it while this screen is open); what a click does, in full, is one disclosure away */}
            <div className="di-scan-herdr-notes">
              <p
                className="di-scan-stage-note"
                id="di-scan-herdr-reason"
                data-testid="di-scan-herdr-reason"
                data-tone={herdrView.tone}
                aria-live="polite"
              >
                {row.tab === 'archived' ? HERDR_ARCHIVED : herdrView.line}
                {herdrView.canRecheck && herdr && (
                  <>
                    {' '}
                    <button
                      type="button"
                      className="di-scan-link"
                      data-testid="di-scan-herdr-recheck"
                      onClick={() => herdr.requestStatus(row.id)}
                    >
                      Check again
                    </button>
                  </>
                )}
              </p>
              {herdrMore && (
                // the Herdr button is described by the full words through this always-present copy (a closed disclosure is computed into no description)
                <span className="di-scan-visually-hidden" id="di-scan-herdr-desc">
                  {herdrView.note}
                </span>
              )}
              {herdrMore && (
                <More testid="di-scan-herdr-more" summary={herdrMoreLabel}>
                  <p className="di-scan-stage-note" id="di-scan-herdr-note" data-testid="di-scan-herdr-note">
                    {herdrView.note}
                  </p>
                </More>
              )}
            </div>
            <div className="di-scan-trash-notes">
              <LocalRetention row={row} />
              <p
                className="di-scan-stage-note"
                id="di-scan-trash-reason"
                data-testid="di-scan-trash-reason"
              >
                {trashGate(row).open
                  ? deletionWithdraws(deletion)
                    ? deletion!.state === 'in_flight'
                      ? 'A deletion of this Mission is waiting for its answer, so it is not offered again.'
                      : 'Its deletion was accepted, so it is not offered again.'
                    : onDeleteLocalData
                      ? 'Hold for a moment to delete.'
                      : TRASH_NOT_CONNECTED
                  : trashGate(row).reason}
              </p>
              {deletion !== undefined && (
                // the owner's delete, answered HERE, at the Mission it was asked for (round 9), whether or not the request workspace is open
                <p
                  className="di-scan-stage-note"
                  data-testid="di-scan-delete-result"
                  data-state={deletion.state}
                  data-mission-ref={row.id}
                  role="status"
                  aria-live="polite"
                >
                  {deletion.text}
                </p>
              )}
              {/* the irreversible details, folded, never removed: the folded copy is what a person opens; the trash control is DESCRIBED by an always-present copy
                  (a browser computes no description from a closed disclosure's content: measured, diScanDetailsDisclosureBrowser.test.ts), and its label says they matter.
                  Both copies say it in the tense that holds: while a deletion is NOT finished, never that the Mission "was deleted" (diRetentionHonesty.test.ts) */}
              <span className="di-scan-visually-hidden" id="di-scan-trash-desc">
                {trashWords}
              </span>
              <More testid="di-scan-trash-more" summary={TRASH_MORE}>
                <RetentionFacts row={row} />
                <p className="di-scan-stage-note" id="di-scan-trash-note">
                  {trashWords}
                </p>
              </More>
            </div>
          </div>
        </div>
      )}

      {view === 'diff' && placeholder !== null && (
        // not read, reading, unavailable and refused share this pane and differ in their words and data-source, never collapse
        <div className="di-scan-ide" data-testid="di-scan-diff" data-source={placeholder.kind}>
          <div className="di-scan-ide-main">
            <aside className="di-scan-tree" aria-label="Files">
              <div className="di-scan-tree-head">
                <span>FILES</span>
              </div>
              <p className="di-scan-stage-note di-scan-tree-note">
                {placeholder.kind === 'not_read'
                  ? 'Files are not listed until a code review is offered for this Mission.'
                  : placeholder.kind === 'reading'
                    ? 'Reading the files.'
                    : 'No files are shown.'}
              </p>
            </aside>
            <div className="di-scan-editor">
              <div className="di-scan-editor-body" data-testid="di-scan-editor">
                <div
                  className="di-scan-editor-empty"
                  tabIndex={-1}
                  data-testid={placeholder.kind === 'not_read' ? 'di-scan-diff-not-read' : `di-scan-diff-${placeholder.kind}`}
                  role={placeholder.kind === 'not_read' ? undefined : 'status'}
                >
                  {placeholder.kind === 'unavailable' || placeholder.kind === 'refused' ? (
                    <>
                      <p>
                        <strong>{placeholder.title}</strong>
                      </p>
                      <p>{placeholder.detail}</p>
                      {/* 1C: the error's advice ("Refresh") names a control; the scan face now HAS it. The status block outlives the click, so focus stays in it. */}
                      {placeholder.kind === 'refused' && placeholder.retry === true && onReviewRefresh !== undefined && (
                        <button
                          type="button"
                          className="di-page-button"
                          data-testid="di-scan-diff-refresh"
                          onClick={(event) => {
                            const block = event.currentTarget.closest<HTMLElement>('.di-scan-editor-empty');
                            wantLanding();
                            onReviewRefresh();
                            block?.focus();
                          }}
                        >
                          Refresh
                        </button>
                      )}
                    </>
                  ) : (
                    <p>{placeholder.reason}</p>
                  )}
                </div>
              </div>
            </div>
          </div>
        </div>
      )}

      {view === 'diff' && diff && (
        <div className="di-scan-ide" data-testid="di-scan-diff" data-source={source.kind}>
          <div className="di-scan-ide-main">
            {/* B2d: collapsed, the tree is HIDDEN, not unmounted, so its scroll and its find box survive. The `hidden` attribute alone hides it: Tailwind's
                preflight draws [hidden] as `display: none !important` in its base layer, which no unlayered author rule can override (Rule 7: verified). */}
            <aside className="di-scan-tree" aria-label="Files" id="di-scan-files" hidden={!filesOpen}>
              {/* B3 routes: every file is a Tab stop between the header and the editor, so the cost of reaching the changes grew with the collection. This is the first stop in the tree and lands on the editor bar's path label. */}
              <button type="button" className="di-jump" data-testid="di-scan-skip-files" onClick={() => pathRef.current?.focus()}>
                Skip the file list
              </button>
              <div className="di-scan-tree-head">
                <span>FILES</span>
                <MoreIcon />
              </div>
              {findOpen && (
                <label className="di-scan-tree-find">
                  <SearchIcon />
                  <input
                    type="search"
                    value={fileQuery}
                    placeholder="Find a file"
                    aria-label="Find a file"
                    data-testid="di-scan-diff-search"
                    ref={findInputRef}
                    onChange={(event) => setFileQuery(event.target.value)}
                  />
                </label>
              )}
              <ul>
                {tree.map((item) => (
                  <li key={item.id}>
                    {item.kind === 'file' ? (
                      <button
                        type="button"
                        className="di-scan-tree-row"
                        data-testid="di-scan-tree-file"
                        data-file-id={item.id}
                        aria-label={item.rowLabel}
                        aria-current={fileId === item.id ? 'true' : undefined}
                        data-selected={fileId === item.id ? 'true' : undefined}
                        style={{ paddingLeft: `${10 + item.depth * 16}px` }}
                        onClick={() => chooseFile(item)}
                      >
                        <span className="di-scan-twist-spacer" aria-hidden="true" />
                        <FileIcon />
                        <span>{item.name}</span>
                        {item.mark && (
                          <span
                            className="di-scan-mark-badge"
                            {...(item.markLabel === undefined ? {} : { role: 'img', 'aria-label': item.markLabel })}
                          >
                            {item.mark}
                          </span>
                        )}
                      </button>
                    ) : (
                      <span
                        className="di-scan-tree-row"
                        data-testid="di-scan-tree-dir"
                        style={{ paddingLeft: `${10 + item.depth * 16}px` }}
                      >
                        <TwistIcon />
                        <FolderIcon />
                        <span>{item.name}</span>
                      </span>
                    )}
                  </li>
                ))}
              </ul>
              {/* real data only: the ONE note (whose changes these are, and what the listing does not cover), in the tree-note slot */}
              {diff.note !== undefined && (
                <p className="di-scan-stage-note di-scan-tree-note" data-testid="di-scan-tree-note">
                  {diff.note}
                </p>
              )}
            </aside>
            <div className="di-scan-editor">
              <div className="di-scan-editor-bar">
                {/* B2d: the tree's labelled, reversible collapse. It lives OUTSIDE the tree, so collapsing cannot strand focus and leaves no strip in the
                    code channel; it adds no Escape layer. */}
                <button
                  type="button"
                  className="di-scan-files-toggle"
                  data-testid="di-scan-files-toggle"
                  aria-expanded={filesOpen}
                  aria-controls="di-scan-files"
                  onClick={onToggleFiles}
                >
                  {filesOpen ? 'Hide files' : 'Show files'}
                </button>
                <span className="di-scan-editor-path" data-testid="di-scan-editor-path" ref={pathRef} tabIndex={-1}>
                  {file?.path ?? entry?.name ?? ''}
                </span>
                <span className="di-scan-visually-hidden" role="status" data-testid="di-scan-diff-copy-status">
                  {copyWords}
                </span>
                {copyWords !== '' && (
                  <span className="di-scan-copy-result" data-testid="di-scan-diff-copy-result" aria-hidden="true" data-ok={copyResult?.ok === true ? 'true' : 'false'}>
                    {copyWords}
                  </span>
                )}
                {heldWords !== '' && (
                  <span className="di-scan-copy-result" data-testid="di-scan-diff-copy-held" aria-hidden="true" data-ok="held">
                    {heldWords}
                  </span>
                )}
                <span className="di-scan-fixture-tag">{diff.tag ?? 'Sample diff (fixture)'}</span>
                {/* B3: the optional soft wrap, with the local reading controls. A display preference only: the text, its indentation and its line numbers are the same either way. */}
                <button
                  type="button"
                  className="di-scan-wrap-toggle"
                  data-testid="di-scan-wrap-toggle"
                  aria-pressed={wrapCode}
                  onClick={onToggleWrap}
                >
                  Wrap lines
                </button>
                <label className="di-scan-unified">
                  <span className="di-scan-visually-hidden">Diff layout</span>
                  <select aria-label="Diff layout" defaultValue="unified">
                    <option value="unified">Unified</option>
                  </select>
                </label>
                <button
                  type="button"
                  className="di-scan-icon-button"
                  aria-label="Close the diff"
                  onClick={onMission}
                >
                  ×
                </button>
              </div>
              <div className="di-scan-editor-body" data-testid="di-scan-editor" data-wrap={wrapCode ? 'true' : undefined} onKeyDown={keepArrowsInEditor}>
                {file?.notice !== undefined ? (
                  // real data: the plain words for why a file has no lines (binary, withheld, not UTF-8, still being read ...)
                  <div className="di-scan-editor-empty" data-testid="di-scan-file-notice" data-state={file.notice.state} role="status" tabIndex={-1}>
                    <p>
                      <strong>{file.notice.title}</strong>
                    </p>
                    <p>{file.notice.body}</p>
                    {file.notice.retry === true && onReviewRefresh !== undefined && (
                      <button
                        type="button"
                        className="di-page-button"
                        data-testid="di-scan-file-refresh"
                        onClick={(event) => {
                          const block = event.currentTarget.closest<HTMLElement>('.di-scan-editor-empty');
                          wantLanding();
                          onReviewRefresh();
                          block?.focus();
                        }}
                      >
                        Refresh
                      </button>
                    )}
                  </div>
                ) : file ? (
                  <>
                    {/* the first hunk header shares the Old/New row, as pixel-lock 03 draws it */}
                    <div className="di-scan-gutter-head">
                      <span>Old</span>
                      <span>New</span>
                      {file.lines[0]?.kind === 'hunk' && (
                        <span className="di-scan-hunk">{file.lines[0].text}</span>
                      )}
                    </div>
                    {file.lines.slice(file.lines[0]?.kind === 'hunk' ? 1 : 0).map((line, index) => (
                      <DiffRow key={`${fileId}-${line.kind}-${index}`} line={line} />
                    ))}
                    {file.foot !== undefined && (
                      <p className="di-scan-stage-note di-scan-file-foot" data-testid="di-scan-file-foot">
                        {file.foot}
                      </p>
                    )}
                  </>
                ) : source.kind === 'read' ? (
                  // a real listing with no file open: an empty listing is a FINDING (its words say what it does not prove), and
                  // otherwise the reader is told to choose, never shown "No changes in this file"
                  <div
                    className="di-scan-editor-empty"
                    data-testid={source.emptyNotice === undefined ? 'di-scan-diff-choose' : 'di-scan-diff-empty-listing'}
                  >
                    {source.emptyNotice === undefined ? (
                      <p>Choose a file in the tree to see its changes, or what is known about it.</p>
                    ) : (
                      <>
                        <p>
                          <strong>{source.emptyNotice.title}</strong>
                        </p>
                        <p>{source.emptyNotice.body}</p>
                      </>
                    )}
                  </div>
                ) : (
                  <div className="di-scan-editor-empty" data-testid="di-scan-editor-unchanged">
                    <p>No changes in this file.</p>
                  </div>
                )}
              </div>
            </div>
          </div>
          {/* the status bar spans the whole Mission content, beneath the file tree too (pixel-lock 03) */}
          {/* every fact here is the sample diff's own; nothing is defaulted, and no success mark is drawn */}
          <footer
            className={diff.base === undefined ? 'di-scan-statusbar' : 'di-scan-statusbar di-scan-statusbar-real'}
            data-testid="di-scan-statusbar"
          >
            <span className={diff.base === undefined ? 'di-scan-status-group' : 'di-scan-status-group di-scan-status-real'}>
              <BranchIcon />
              {diff.base === undefined ? (
                diff.branch
              ) : (
                <>
                  {/* real data only. A real branch NAME is the one variable field the bar may truncate (CSS; its full value stays in the text and
                      the title); a fixed LABEL ("Detached", "Branch unknown") is never marked as a value. For a detached HEAD the title is
                      the CHECKOUT's own identity (`target.head`), which is drawn nowhere else: it is always present here. */}
                  <span
                    className={diff.branchFixed === true ? 'di-scan-status-branch' : 'di-scan-status-branch di-scan-status-value'}
                    title={diff.branchTitle}
                  >
                    {diff.branch}
                    {diff.branchHidden !== undefined && <span className="di-scan-visually-hidden">{diff.branchHidden}</span>}
                  </span>
                  {/* real data only: the revision the working tree was compared with (`comparison.baseOid`), labelled base. It is NOT the checkout's
                      identity; the two are separate fields and are never drawn as one. */}
                  <span className="di-scan-status-base di-scan-status-value" title={`Compared with revision ${diff.base.full}`}>
                    base {diff.base.short}
                  </span>
                </>
              )}
            </span>
            <span>{diff.filesChanged}</span>
            <span className="di-scan-status-counts" title={diff.countsNote}>
              <span className="di-scan-add">+{diff.added}</span>
              <span className="di-scan-del">−{diff.removed}</span>
              {/* real data only: a total that is only a floor says so to assistive technology too */}
              {diff.countsNote !== undefined && <span className="di-scan-visually-hidden"> ({diff.countsNote})</span>}
            </span>
            <span className="di-scan-status-group di-scan-status-end">
              {diff.format === undefined ? (
                <>
                  <span>{diff.encoding}</span>
                  <span>{diff.lineEnding}</span>
                  {file && <span data-testid="di-scan-editor-language">{file.language}</span>}
                  <span>{diff.indent}</span>
                </>
              ) : (
                <>
                  {/* real data only: ONE collective phrase for the three file facts the route does not carry. The three are ENUMERATED in the
                      hidden text (the accessible name) and the title (the hover), so none is lost. */}
                  <span data-testid="di-scan-status-format" title={diff.format.accessible}>
                    {diff.format.visible}
                    <span className="di-scan-visually-hidden"> ({diff.format.accessible})</span>
                  </span>
                  {file && <span data-testid="di-scan-editor-language">{file.language}</span>}
                </>
              )}
            </span>
          </footer>
        </div>
      )}

      {view === 'evidence' && (
        // R10: the EXISTING evidence library for this Mission, exactly as the office lists it (drawn by the office, which holds the record and the read seam)
        <div className="di-scan-evidence-view" data-testid="di-scan-evidence-view">
          {evidenceLive ? (
            renderEvidence!(row.id)
          ) : (
            <p className="di-scan-stage-note">{evidenceReason}</p>
          )}
        </div>
      )}

      {view === 'comments' && (
        <div className="di-scan-comments-view" data-testid="di-scan-comments-view">
          <h1 className="di-scan-comments-title">Comments</h1>
          <p className="di-scan-comments-sub">{row.title}</p>
          {(thread.kind === 'sample' || thread.kind === 'read') && (
            <>
              {thread.kind === 'sample' && (
                <span className="di-scan-fixture-tag">Sample thread (fixture)</span>
              )}
              <ol className="di-scan-thread" data-testid="di-scan-thread">
                {thread.thread.map((comment) => (
                  <li key={comment.id} className="di-scan-comment">
                    {/* a plain placeholder, as v3 07 draws it: the name beside it says who; no character is invented */}
                    <span className="di-scan-comment-avatar" aria-hidden="true" />
                    <div className="di-scan-comment-body">
                      <p className="di-scan-comment-head">
                        <span className="di-scan-comment-author">{comment.author}</span>
                        <span className="di-scan-comment-when">{comment.when}</span>
                      </p>
                      <p className="di-scan-comment-text">{comment.text}</p>
                    </div>
                  </li>
                ))}
              </ol>
            </>
          )}
          {thread.kind === 'unavailable' && row.sourceKind === 'herdr_task' && (
            <p className="di-scan-stage-note" data-testid="di-scan-comments-herdr">
              {thread.reason}
            </p>
          )}
          {/* X3 (gap 10, D-6, D106): Free has no composer, not even a disabled one, and no claim about a history it
              cannot read; the label is the whole statement. A Herdr task's comments are its recorded review. */}
          {row.sourceKind !== 'herdr_task' && (
            <p className="di-scan-comments-paid" data-testid="di-scan-comments-paid">
              <span className="di-scan-comments-paid-tag">Paid</span>
              {DI_COMMENTS_PAID}
            </p>
          )}
        </div>
      )}
    </div>
  );
}

function DiffRow({ line }: { line: DiScanDiffLine }) {
  if (line.kind === 'hunk') {
    return (
      <div className="di-scan-diff-line" data-kind="hunk">
        <span />
        <span />
        <span className="di-scan-hunk">{line.text}</span>
      </div>
    );
  }
  const mark = line.kind === 'del' ? '−' : line.kind === 'add' ? '+' : '';
  return (
    <div className="di-scan-diff-line" data-kind={line.kind}>
      <span className="di-scan-ln">{line.oldNo}</span>
      <span className="di-scan-ln">{line.newNo}</span>
      <span className="di-scan-code">
        {mark !== '' && <span className="di-scan-sign">{mark}</span>}
        {codeTokens(line.text).map((token, index) => (
          <span
            key={index}
            className={token.kind === 'plain' ? undefined : `di-scan-tok-${token.kind}`}
          >
            {token.text}
          </span>
        ))}
      </span>
    </div>
  );
}

/** A rail entry whose screen is not in this build: drawn, disabled, and its plain reason said (never a stub that looks live). */
function RailUnavailable({
  testid,
  icon,
  label,
  reason,
}: {
  testid: string;
  icon: ReactNode;
  label: string;
  reason: string;
}) {
  const reasonId = `${testid}-reason`;
  return (
    <>
      <button
        type="button"
        className="di-rail-item"
        data-testid={testid}
        disabled
        title={reason}
        aria-describedby={reasonId}
      >
        {icon}
        {label}
      </button>
      <span className="di-scan-visually-hidden" id={reasonId}>
        {reason}
      </span>
    </>
  );
}

/**
 * THE DI NAVIGATION RAIL (01-home.jpg, CONTROL-MAP "Navigation"): DI World (Home), + New Orchestrator (Cmd N),
 * Reception with its count, the Orchestrators (Missions in progress), Workspace (Missions, Herdr, Connections), and
 * Settings at the foot. It is the ONLY home of New Orchestrator and Reception in the chrome (locked single-homes rule).
 * It reuses navigation that already exists and adds no action route: the screens it cannot reach in this build say so.
 */
function Rail({
  rows,
  hidden,
  view,
  selectedId,
  receptionCount,
  receptionDoubt = null,
  newRef,
  onHome,
  onPage,
  onOpenMission,
  onOpenSettings,
  missionsRead,
  minimizeRef,
  onMinimize,
  herdr,
  herdrMissionRef,
}: {
  rows: readonly DiScanRow[];
  hidden: ReadonlySet<string>;
  view: DiScanView;
  selectedId: string | null;
  /** The Herdr control, and the ONE Mission the rail's Herdr acts on: the Mission whose screen is open (null: none is, and the entry says so). */
  herdr: DiHerdrControl | null | undefined;
  herdrMissionRef: string | null;
  /** The reception source's own count; null: no reception desk (the entry says why). */
  receptionCount: number | null;
  /** Why that count is only a floor (the SAME doubt the Reception header reads, AC-4.7-16); null: whole. */
  receptionDoubt?: DiScanCountDoubt | null;
  newRef: Ref<HTMLButtonElement>;
  onHome: () => void;
  /** Open one of the rail's pages; `opener` is where focus returns when it closes with Esc. */
  onPage: (page: 'missions' | 'reception' | 'new', opener: HTMLElement) => void;
  onOpenMission: (id: string) => void;
  onOpenSettings?: () => void;
  /** Whether the Missions were read: an unread list is said as unread, never as "none running". */
  missionsRead: DiScanMissionsRead;
  /**
   * The office Minimize control. It lives on the rail because a Mission stage or a rail page is
   * `position: fixed` from this rail's right edge to the window's right edge and covers the Home
   * header, which used to be the only place the control was drawn.
   */
  minimizeRef?: Ref<HTMLButtonElement>;
  onMinimize: () => void;
}) {
  const orchestrators = railOrchestrators(rows, hidden);
  const count = receptionCount ?? 0;
  // the badge and its spoken words express the SAME state as the Reception header: a count, a floor, or not known
  const badgeText = receptionCount === null ? null : countBadge(count, receptionDoubt);
  const spoken = badgeText === null ? undefined : `Reception, ${receptionHeadline(count, receptionDoubt)}`;
  const here = (page: DiScanView[]) => (page.includes(view) ? 'page' : undefined);
  const hv = herdrViewFor(herdr, herdrMissionRef);
  const stageSaysHerdr = view === 'mission' || ((view === 'diff' || view === 'comments') && hv.outcome);
  return (
    <nav
      className="di-rail"
      aria-label="DI World"
      data-testid="di-rail"
      style={{ width: DI_RAIL_WIDTH_PX }}
    >
      <button
        type="button"
        className="di-rail-wordmark"
        data-testid="di-rail-home"
        aria-label="DI World, Home"
        aria-current={view === 'list' ? 'page' : undefined}
        onClick={onHome}
      >
        <span className="di-rail-wordmark-di" aria-hidden="true">
          <DiMark />
        </span>
        <span className="di-rail-wordmark-world" aria-hidden="true">
          World
        </span>
      </button>
      <button
        type="button"
        ref={newRef}
        className="di-rail-new"
        data-testid="di-rail-new-orchestrator"
        aria-keyshortcuts="Meta+N"
        aria-current={here(['new'])}
        title="Start a new Mission (Cmd N)"
        onClick={(event) => onPage('new', event.currentTarget)}
      >
        + New Orchestrator
      </button>
      <button
        type="button"
        className="di-rail-item di-rail-reception"
        data-testid="di-open-reception"
        aria-label={spoken}
        aria-current={here(['reception'])}
        disabled={receptionCount === null}
        title={
          receptionCount === null
            ? 'Reception is not available in this office'
            : receptionDoubt !== null
              ? `${receptionHeadline(count, receptionDoubt)}. ${receptionDoubt.text}`
              : undefined
        }
        onClick={(event) => onPage('reception', event.currentTarget)}
      >
        <InboxIcon />
        Reception
        {badgeText !== null && (
          <span className="di-rail-count" data-testid="di-rail-reception-count" aria-hidden="true">
            {badgeText}
          </span>
        )}
      </button>

      <h2 className="di-rail-label" id="di-rail-orchestrators-label">
        Orchestrators
      </h2>
      {missionsRead.kind !== 'read' ? (
        <p
          className="di-rail-empty di-rail-orchestrators"
          data-testid="di-rail-missions-not-read"
          data-read={missionsRead.kind}
        >
          {missionsRead.reason}
        </p>
      ) : orchestrators.length === 0 ? (
        <p className="di-rail-empty di-rail-orchestrators">No Orchestrators are running.</p>
      ) : (
        <ul
          className="di-rail-list di-rail-orchestrators"
          aria-labelledby="di-rail-orchestrators-label"
          data-testid="di-rail-orchestrators"
        >
          {orchestrators.map((row) => {
            // the canonical lifecycle status, never Reception's attention in its place (round 3, finding 2)
            const pill = statusPillOf(row);
            return (
              <li key={row.id}>
                <button
                  type="button"
                  className="di-rail-item di-rail-orchestrator"
                  data-testid="di-rail-orchestrator"
                  data-mission-ref={row.id}
                  aria-current={
                    DI_SCAN_MISSION_VIEWS.includes(view) && selectedId === row.id
                      ? 'page'
                      : undefined
                  }
                  onClick={() => onOpenMission(row.id)}
                >
                  {pill && <StatusMark tone={pill.tone} glyph={pill.glyph} />}
                  <span className="di-rail-orchestrator-title">{row.title}</span>
                  {pill && (
                    <span
                      className="di-rail-orchestrator-status"
                      data-testid="di-rail-orchestrator-status"
                    >
                      {pill.label}
                    </span>
                  )}
                  {/* which Mission needs the person, said apart from its status (round 4, finding 4): a dot AND the words */}
                  {knownTrue(row.needsYou) && (
                    <span
                      className="di-rail-orchestrator-attention"
                      data-testid="di-rail-orchestrator-attention"
                    >
                      <span className="di-scan-dot" data-tone="needs" aria-hidden="true" />
                      Needs you
                    </span>
                  )}
                </button>
              </li>
            );
          })}
        </ul>
      )}

      <h2 className="di-rail-label" id="di-rail-workspace-label">
        Workspace
      </h2>
      <ul className="di-rail-list" aria-labelledby="di-rail-workspace-label">
        <li>
          <button
            type="button"
            className="di-rail-item"
            data-testid="di-rail-missions"
            aria-current={here(['missions'])}
            onClick={(event) => onPage('missions', event.currentTarget)}
          >
            <ListIcon />
            Missions
          </button>
        </li>
        <li>
          <button
            type="button"
            className="di-rail-item"
            data-testid="di-rail-herdr"
            title={hv.note}
            aria-describedby="di-rail-herdr-reason"
            aria-busy={hv.busy}
            disabled={!hv.enabled}
            onClick={() => herdr && herdrMissionRef !== null && herdr.launch(herdrMissionRef)}
          >
            <HerdIcon />
            Herdr
          </button>
          <span className="di-scan-visually-hidden" id="di-rail-herdr-reason">
            {hv.note}
          </span>
          {/* shown when a click has a result, and when a Mission is open but Herdr cannot open for it (the reason matters then); the "open a Mission" hint stays in the title
              and the description. Said ONCE per screen: not here where the open screen says it itself (Mission details' Herdr line always; the Code Diff and Comments
              header after a click), so the rail never repeats a note across a Mission's screens */}
          {(hv.outcome || (!hv.enabled && herdrMissionRef !== null)) && !stageSaysHerdr && (
            <p className="di-rail-empty" data-testid="di-rail-herdr-note" data-tone={hv.tone}>
              {hv.line}
            </p>
          )}
        </li>
        <li>
          <RailUnavailable
            testid="di-rail-connections"
            icon={<LinkIcon />}
            label="Connections"
            reason={CONNECTIONS_REASON}
          />
        </li>
      </ul>

      <div className="di-rail-foot">
        <button
          type="button"
          ref={minimizeRef}
          className="di-rail-item"
          data-testid="di-office-panel-minimize"
          title="Minimize the office panel; Restore brings it back as you left it"
          onClick={onMinimize}
        >
          <span aria-hidden="true">▸</span>
          Minimize
        </button>
        <button
          type="button"
          className="di-rail-item"
          data-testid="di-rail-settings"
          disabled={onOpenSettings === undefined}
          title={
            onOpenSettings === undefined ? 'Settings are not available in this office' : undefined
          }
          onClick={() => onOpenSettings?.()}
        >
          <GearIcon />
          Settings
        </button>
      </div>
    </nav>
  );
}

/**
 * B2b (Q9): TWO panes in a window DI_SCAN_TWO_PANE_MIN_PX wide or wider (a screen docked between the rail and the list, the list beside it), ONE pane
 * below (the screen covers the list). One state drives both the docked screen's right edge and whether Home is covered, so they never disagree. With
 * no window (server rendering) it is one pane.
 */
const TWO_PANE_QUERY = `(min-width: ${DI_SCAN_TWO_PANE_MIN_PX}px)`;
const subscribeTwoPane = (onChange: () => void) => {
  if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') return () => undefined;
  const query = window.matchMedia(TWO_PANE_QUERY);
  query.addEventListener('change', onChange);
  return () => query.removeEventListener('change', onChange);
};
const twoPaneNow = () => typeof window !== 'undefined' && typeof window.matchMedia === 'function' && window.matchMedia(TWO_PANE_QUERY).matches;
function useTwoPane(): boolean {
  return useSyncExternalStore(subscribeTwoPane, twoPaneNow, () => false);
}

export function DiScanPanel({
  rows,
  initialSelectedId = null,
  initialView = 'list',
  dockWidthPx = DI_PANEL_WIDTH_PX,
  initialMoreActionsOpen = false,
  initialMissionsStatus = 'all',
  minimizeRef,
  onMinimize,
  onSelect,
  onOpenRequestWorkspace,
  onOpenSettings,
  onJumpToWorld,
  minimizedCount = 0,
  onJumpToMinimized,
  reviewSession = null,
  onReviewBegin,
  onReviewEnd,
  onReviewChoose,
  onReviewRefresh,
  reception = null,
  onOpenRequestItem,
  onOpenDecision,
  renderEvidence,
  newMissionRequest = null,
  newMission = DI_NEW_MISSION_OFFICE,
  specimen = false,
  missionsRead = DI_MISSIONS_READ,
  fixtureBanner = null,
  openRequest = null,
  missionDelivery = null,
  herdr = null,
  onDeleteLocalData,
  retained = null,
  deleteResults,
}: DiScanPanelProps) {
  const [tab, setTab] = useState<DiScanTab>('open');
  const [query, setQuery] = useState('');
  const [needsOnly, setNeedsOnly] = useState(false);
  // Nothing in the panel hides a Mission: the only delete affordance is the per-Mission trash (V-6, CONTROL-MAP
  // "trash = Mission delete only"), so the list's hidden set is always empty.
  const hidden = NOTHING_HIDDEN;
  // B2a ("selected" means "docked"): an initial selection is taken only when the panel opens on a Mission's own screen. On any other screen it would be a
  // selection that nothing shows (Home's inline card, which drew it, is removed), so it is not taken.
  const [selectedId, setSelectedId] = useState<string | null>(DI_SCAN_MISSION_VIEWS.includes(initialView) ? initialSelectedId : null);
  // B2d: the Code Diff's file tree, OPEN by default (the Lead's ruling (a): the tree is the file navigation, so it is never hidden on the default path).
  // Kept HERE, above the Stage (which remounts per Mission and per view), so the person's choice is remembered for the session, across navigation and
  // resize.
  const [filesOpen, setFilesOpen] = useState(true);
  const [wrapCode, setWrapCode] = useState(false);
  const [view, setView] = useState<DiScanView>(() =>
    VIEWS.includes(initialView) ? initialView : 'list',
  );
  // B2b (Q9): in two-pane mode a screen docks BESIDE Home (its right edge at the list dock's width) and Home is not covered
  const twoPane = useTwoPane();
  const dockRightPx = twoPane ? dockWidthPx : undefined;
  const rootRef = useRef<HTMLDivElement>(null);
  // R22 / 1A-R: while the request workspace is shown, the stage controls it covers are ISOLATED (inert, still drawn), never focusable unseen
  // (diCoverIsolation.ts: the trade, including the screen-reader cost, is stated there)
  useCoverIsolation(rootRef, newMissionRequest?.workspaceShown === true);
  const newRef = useRef<HTMLButtonElement>(null);
  // M1: where Mission details goes Back to (the page it was opened from)
  const [origin, setOrigin] = useState<DiScanOrigin>('list');
  // R11: Reception's Open Mission lands on Mission details with Delivery in view and focus
  const [focusDelivery, setFocusDelivery] = useState(false);
  const deliveryFocused = useCallback(() => setFocusDelivery(false), []);
  // the rail entry that opened the current page: Esc returns focus there
  const pageOpener = useRef<HTMLElement | null>(null);

  // B8: New Mission IS the Ask (one screen, one composer). Entering it closes a request workspace that is showing its own Ask composer over or beside it; closing sends
  // nothing and keeps the draft and the conversation, and focus goes back to the entry the person used
  const newMissionRequestRef = useRef(newMissionRequest);
  newMissionRequestRef.current = newMissionRequest;
  const enterNewMission = (opener: HTMLElement | null) => {
    const request = newMissionRequestRef.current;
    if (request?.workspaceShown) request.closeWorkspace(opener);
  };
  const openPage = (page: 'missions' | 'reception' | 'new', opener?: HTMLElement) => {
    if (opener) pageOpener.current = opener;
    if (page === 'new') enterNewMission(opener ?? newRef.current);
    setView(page);
  };

  // K1 (the Lead's ruling on the defect K1 exposed): hidden, minimized or unmounted content claims NO keys. The panel's own window key handlers
  // act only while the panel is VISIBLE, read AT THE KEY: a minimized panel keeps this component mounted inside a hidden aside, and its view (and
  // the aside's data-stage) survive the minimize, so neither can say whether the person can see it. Without this, a key the open Map no longer
  // swallows dismissed a docked Mission nobody could see. The code review's own handler is off while minimized for the same reason (DiCodeReview.tsx).
  const panelShown = () => rootRef.current?.checkVisibility({ checkVisibilityCSS: true }) === true;

  // Cmd N opens New Mission, as the rail's + New Orchestrator does (AC-UX-02), wherever the host delivers the key, while the panel is shown.
  // Hidden, it is a NO-OP (K1, point 6): the app still takes the key from the browser (as before), and switches nothing nobody can see.
  const enterNewMissionRef = useRef(enterNewMission);
  enterNewMissionRef.current = enterNewMission;
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (!event.metaKey || event.ctrlKey || event.altKey || event.shiftKey) return;
      if (event.key.toLowerCase() !== 'n') return;
      event.preventDefault();
      if (!panelShown()) return;
      pageOpener.current = newRef.current;
      enterNewMissionRef.current(newRef.current);
      setView('new');
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, []);

  useLayoutEffect(() => {
    const aside = rootRef.current?.closest('.di-scan');
    if (!(aside instanceof HTMLElement)) return;
    if (view === 'list') aside.removeAttribute('data-stage');
    else aside.dataset.stage = 'open';
    // B3: which layout the open screen is in, for the minimized-surface strip (diScanPanel.css): two panes leave the strip beside the dock, one pane moves it to the window's edge
    aside.dataset.twoPane = String(twoPane);
    return () => {
      aside.removeAttribute('data-stage');
      aside.removeAttribute('data-two-pane');
    };
  }, [view, twoPane]);

  // Esc closes the screen the person is on, ONE layer per press: a nested screen to its Mission, Mission details to where it was opened from,
  // and a rail page (New Mission is one screen, its proposal drawn in place) to Home, with focus back on the rail entry that opened it.
  // D1: New Mission peels its own layer first: while it shows a conversation, Esc closes that (the typed ask kept) and only a blank New Mission goes Home.
  // B1: the ladder acts only while NOTHING is in front (diModalStack). A dialog or a non-modal popover in front owns Escape; without this gate one press
  // would close that surface AND pop the view (the class of the Settings leak).
  const newMissionEscape = useRef<(() => boolean) | null>(null);
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key !== 'Escape' || view === 'list') return;
      if (frontCount() > 0) return;
      // K1: a hidden panel's ladder peels nothing (see panelShown)
      if (!panelShown()) return;
      if (view === 'diff' || view === 'comments' || view === 'evidence') setView('mission');
      else if (view === 'mission') setView(origin);
      else if (view === 'new' && newMissionEscape.current?.() === true) return;
      else {
        setView('list');
        const back = pageOpener.current;
        if (back?.isConnected) back.focus();
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [view, origin]);

  // A screen change that removed the focused control (it unmounted with the old screen) hands focus to the new screen's
  // heading or Back, never to the page body (AC-UX-07). Focus that is still in place is left alone, and the first paint
  // moves nothing (no screen change has happened yet).
  const viewChanged = useRef(false);
  useEffect(() => {
    if (!viewChanged.current) {
      viewChanged.current = true;
      return;
    }
    // B2a-PA2 (post-acceptance, AC-UX-07): Home is COVERED (visibility: hidden) rather than unmounted, so a control in it that had focus has NOT
    // lost it yet when this runs, and the browser drops it to the page body just after. Focus inside a covered Home counts as lost, as an
    // unmounted control did at HEAD.
    const active = document.activeElement;
    // B2b, the Lead's ruling (B): activating a Home row IS a navigation in BOTH modes, so focus inside Home (covered in one pane, beside the screen in two)
    // goes to the docked screen too: one key, one focus result, at every width. Focus a person put elsewhere (the rail, the world) is left alone.
    const lost =
      active === null ||
      active === document.body ||
      rootRef.current?.querySelector('[data-testid="di-scan-home"]')?.contains(active) === true;
    if (!lost || view === 'list') return;
    const target = rootRef.current?.querySelector<HTMLElement>(
      '[data-page-heading="true"], [data-testid="di-scan-back"]',
    );
    target?.focus();
    // selectedId: in two panes another row can dock a different Mission without the view changing, and that is a navigation too.
    // twoPane (B2b round 13, the Reviewer's finding 1): crossing 1440 changes neither the view nor the docked Mission, yet narrowing can take focus
    // away: it COVERS Home (a focused row there is hidden) and UNMOUNTS the list link (a focused link is removed, focus falls to the body). Both
    // count as lost above, so focus goes to the docked screen; the Mission stays docked, and focus anywhere else is left where it is.
  }, [view, selectedId, twoPane]);

  // B2a (Q4): "selected" means "docked". Leaving a Mission's screens clears the pick, so the list never shows a selected row with nothing docked. When
  // that left focus NOWHERE (the control that had it unmounted with the screen, as Escape and Back do), focus goes to the dismissed Mission's own row in
  // the list, or, if that row is not listed now, to the list's current tab: a reachable, intentional, visible target. Focus a person put somewhere
  // themselves (the rail's Home, say) is left where it is.
  const docked = DI_SCAN_MISSION_VIEWS.includes(view);
  const dockedId = useRef<string | null>(null);
  useEffect(() => {
    if (docked) {
      dockedId.current = selectedId;
      return;
    }
    const left = dockedId.current;
    dockedId.current = null;
    if (left === null) return;
    setSelectedId(null);
    if (view !== 'list') return;
    if (document.activeElement !== null && document.activeElement !== document.body) return;
    const home = rootRef.current;
    const row = home?.querySelector<HTMLElement>(`[data-testid="di-scan-row"][data-mission-ref="${CSS.escape(left)}"]`);
    const target = row ?? home?.querySelector<HTMLElement>(`[data-testid="di-scan-tab-${tab}"]`);
    // the list keeps the scroll the person left it at: focus moves WITHOUT scrolling, and the row is brought into view (the nearest edge) only when it
    // is not wholly in view
    target?.focus({ preventScroll: true });
    const list = row?.closest<HTMLElement>('[data-testid="di-missions"]');
    if (row && list) {
      const r = row.getBoundingClientRect();
      const l = list.getBoundingClientRect();
      if (r.top < l.top || r.bottom > l.bottom) row.scrollIntoView({ block: 'nearest' });
    }
  }, [docked, view, selectedId, tab]);

  const shown = useMemo(
    () => visibleScanRows(rows, tab, query, needsOnly, hidden),
    [rows, tab, query, needsOnly, hidden],
  );
  // with the Needs-you filter on, the Missions whose need is NOT known are not hidden silently: they are counted and said
  const needsUnknown = needsOnly
    ? visibleScanRows(rows, tab, query, false, hidden).filter((row) => !row.needsYou.known)
    : [];
  const attention = openAttentionCount(rows, hidden);
  // the Open count is a FLOOR while any open Mission's need is NOT known: "N+", or "?" when none is known (never no badge)
  const openUnknown = rows.filter(
    (row) => !hidden.has(row.id) && row.tab === 'open' && !row.needsYou.known,
  );
  const openReasons = [
    ...new Set(
      openUnknown.flatMap((row) => (row.needsYou.known ? [] : [asSentence(row.needsYou.why.text)])),
    ),
  ].join(' ');
  const openBadge = countBadge(
    attention,
    openUnknown.length === 0 ? null : { text: openReasons, codes: [] },
  );
  const openSpoken =
    openUnknown.length === 0
      ? null
      : attention > 0
        ? `${attention} ${attention === 1 ? 'needs' : 'need'} you, and more may. ${openReasons}`
        : `Some Missions may need you. ${openReasons}`;
  const selected = rows.find((row) => row.id === selectedId && !hidden.has(row.id)) ?? null;

  const choose = (id: string) => {
    setSelectedId(id);
    onSelect?.(id);
  };

  // AC-4.4-26: a Mission opened from the WORLD (its agent or Mission label clicked) opens with Home as its origin, so Back
  // returns Home and the world, which nothing here touches, is as it was; each new `seq` is one click
  const handledOpen = useRef(openRequest?.seq ?? 0);
  useEffect(() => {
    if (openRequest === null || openRequest.seq === handledOpen.current) return;
    handledOpen.current = openRequest.seq;
    if (!rows.some((row) => row.id === openRequest.ref)) return;
    // the row is chosen WITHOUT onSelect: a chosen row moves the world's selection to the Mission's first drawn member,
    // and here the world already holds the person's own selection (the agent they clicked), which must stay
    setSelectedId(openRequest.ref);
    setOrigin('list');
    setFocusDelivery(false);
    setView('mission');
  }, [openRequest, rows]);

  // B2b, the cheap keyboard paths while a Mission is DOCKED (the Lead, 2026-11-05: returning to the list must not dismiss the Mission). To the list: the
  // docked Mission's own row, else the list's current tab. Back to the screen: its heading or Back, the same named target as opening.
  const focusDockedRow = () => {
    const home = rootRef.current?.querySelector<HTMLElement>('[data-testid="di-scan-home"]');
    const row = selectedId === null ? null : home?.querySelector<HTMLElement>(`[data-testid="di-scan-row"][data-mission-ref="${CSS.escape(selectedId)}"]`);
    (row ?? home?.querySelector<HTMLElement>(`[data-testid="di-scan-tab-${tab}"]`))?.focus();
  };
  const focusDockedScreen = () =>
    rootRef.current
      ?.querySelector<HTMLElement>('[data-testid="di-scan-stage"] [data-page-heading="true"], [data-testid="di-scan-stage"] [data-testid="di-scan-back"]')
      ?.focus();
  const openMission = (id: string, options: { delivery?: boolean } = {}) => {
    // activating the row of the Mission ALREADY docked on its details moves focus to that screen, keeping it docked (one key back from the list)
    if (view === 'mission' && selectedId === id && options.delivery !== true) {
      focusDockedScreen();
      return;
    }
    choose(id);
    // Back returns to the page the Mission was opened from; opening from a Mission's own screens keeps that origin
    if (view === 'list' || view === 'missions' || view === 'reception') setOrigin(view);
    setFocusDelivery(options.delivery === true);
    setView('mission');
  };


  return (
    <>
      <Rail
        rows={rows}
        hidden={hidden}
        view={view}
        selectedId={selectedId}
        receptionCount={reception === null ? null : reception.attention}
        receptionDoubt={reception === null ? null : reception.attentionDoubt}
        newRef={newRef}
        onHome={() => setView('list')}
        onPage={openPage}
        onOpenMission={openMission}
        onOpenSettings={onOpenSettings}
        missionsRead={missionsRead}
        minimizeRef={minimizeRef}
        onMinimize={onMinimize}
        herdr={herdr}
        herdrMissionRef={
          (view === 'mission' || view === 'diff' || view === 'comments' || view === 'evidence') && selected ? selected.id : null
        }
      />
      <div ref={rootRef} className="di-scan-body" data-testid="di-scan-panel" data-view={view} data-panes={twoPane ? 'two' : 'one'}>

        {(view === 'mission' || view === 'diff' || view === 'comments' || view === 'evidence') && selected && (
          <Stage
            initialMoreActionsOpen={initialMoreActionsOpen}
            rightPx={dockRightPx}
            onToList={twoPane ? focusDockedRow : undefined}
            // one Stage per Mission: its file selection and find state start fresh for each Mission (C3/C4 round 5)
            key={selected.id}
            view={view}
            row={selected}
            origin={origin}
            focusDelivery={focusDelivery}
            onDeliveryFocused={deliveryFocused}
            onList={() => setView('list')}
            onBack={() => setView(origin)}
            onMission={() => setView('mission')}
            onView={setView}
            onOpenSettings={onOpenSettings}
            reviewSession={reviewSession}
            onReviewBegin={onReviewBegin}
            onReviewEnd={onReviewEnd}
            onReviewChoose={onReviewChoose}
            onReviewRefresh={onReviewRefresh}
            onOpenDecision={onOpenDecision}
            renderEvidence={renderEvidence}
            fixtureBanner={fixtureBanner}
            missionDelivery={missionDelivery}
            herdr={herdr}
            onDeleteLocalData={onDeleteLocalData}
            deleteResults={deleteResults}
            filesOpen={filesOpen}
            onToggleFiles={() => setFilesOpen((open) => !open)}
            wrapCode={wrapCode}
            onToggleWrap={() => setWrapCode((on) => !on)}
          />
        )}

        {DI_SCAN_PAGE_VIEWS.includes(view) && (
          <div
            className="di-scan-stage di-scan-page-stage"
            data-testid="di-scan-stage"
            // K1: this screen covers the world while it is drawn (WorldMap.tsx reads it at the key, with its rendered box)
            data-di-covers-world=""
            data-view={view}
            // a rail page starts at the rail's edge, like a Mission's screens; in two-pane mode it ends at the list (B2b, Q3: one geometry)
            style={{ left: DI_RAIL_WIDTH_PX, ...(dockRightPx !== undefined ? { right: `calc(${dockRightPx}px + var(--scan-tray-w, 0px))` } : {}) }}
          >
            {twoPane && <ToListLink onToList={focusDockedRow} />}
            <StageFixture text={fixtureBanner} />
            {view === 'missions' && (
              <MissionsPage
                rows={rows}
                specimen={specimen}
                read={missionsRead}
                onOpenMission={openMission}
                retained={retained}
                initialStatus={initialMissionsStatus}
              />
            )}
            {view === 'reception' &&
              (reception === null ? (
                <p className="di-scan-stage-note">This office provides no reception desk.</p>
              ) : (
                <ReceptionPage
                  reception={reception}
                  rows={rows}
                  onOpenMission={openMission}
                  onOpenDelivery={(id) => openMission(id, { delivery: true })}
                  onOpenRequest={onOpenRequestItem}
                />
              ))}
            {view === 'new' && (
              <NewMissionPage
                rows={rows}
                source={newMission}
                specimen={specimen}
                request={newMissionRequest ?? undefined}
                onOpenRequestWorkspace={onOpenRequestWorkspace}
                onOpenMissions={() => setView('missions')}
                escapeStep={newMissionEscape}
              />
            )}
          </div>
        )}
        {/* B2a: Home (the Mission list) stays MOUNTED in every view, so its tab, scroll and filter are kept. While a screen covers it, it is hidden from
            sight, from Tab and from the accessibility tree (diScanPanel.css); B2b docks the screen BESIDE it at 1440 px and wider. B2b: Home comes
            AFTER the screens in the document, so sequential Tab follows the order the window shows: rail | screen | list (the Lead's ruling (B),
            condition 2). It stays one fixed child, so moving it here never remounts it. */}
        <div className="di-scan-home" data-testid="di-scan-home" data-covered={view === 'list' || twoPane ? undefined : 'true'}>
            <header className="di-scan-header">
              <div className="di-scan-header-lead">
                <span className="di-scan-mark" aria-hidden="true">
                  <DiMark />
                </span>
                {/* the labelled SAMPLE specimen says so on Home too, as every specimen page does (lead's labelling condition) */}
                {specimen && (
                  <span className="di-scan-fixture-tag" data-testid="di-home-sample-tag">
                    Sample Missions (fixture)
                  </span>
                )}
              </div>
              <div className="di-scan-header-tools">
                {/* 1B: Settings and the Filter missions toggle are secondaries of the Mission list: ONE anchored, non-modal popover (diPopover.ts), as Mission details' More actions. The trigger
                    keeps the filter's only cue (the icon's pressed look) in its words. Settings is held, with why, where the office has no settings route (it used to do nothing, silently). */}
                <AnchoredPopover
                  label={needsOnly ? `Options (${DI_NEEDS_YOU.toLowerCase()} only)` : 'Options'}
                  dialogLabel="Mission list options"
                  triggerClassName="di-page-button"
                  testid="di-scan-options"
                >
                  {(dismiss) => (
                    <>
                      <PopoverItem
                        label="Settings"
                        testid="di-scan-settings"
                        held={onOpenSettings === undefined ? 'Settings are not available in this office' : null}
                        onSelect={() => {
                          dismiss('to-trigger');
                          onOpenSettings?.();
                        }}
                      />
                      <PopoverItem
                        label={`${DI_NEEDS_YOU} only`}
                        testid="di-scan-filter"
                        pressed={needsOnly}
                        onSelect={() => {
                          setNeedsOnly((value) => !value);
                          dismiss('to-trigger');
                        }}
                      />
                    </>
                  )}
                </AnchoredPopover>
              </div>
            </header>

            <div className="di-scan-tabs">
              <div className="di-scan-tablist" role="tablist" aria-label="Missions">
                {DI_SCAN_TABS.map((item) => (
                  <button
                    key={item.id}
                    type="button"
                    role="tab"
                    className="di-scan-tab"
                    aria-selected={tab === item.id}
                    data-testid={`di-scan-tab-${item.id}`}
                    onClick={() => setTab(item.id)}
                  >
                    {item.label}
                    {item.id === 'open' && openBadge !== null && openSpoken === null && (
                      <span className="di-scan-badge" data-testid="di-scan-attention">
                        {openBadge}
                      </span>
                    )}
                    {item.id === 'open' && openBadge !== null && openSpoken !== null && (
                      <>
                        <span
                          className="di-scan-badge"
                          data-testid="di-scan-attention"
                          title={openSpoken}
                          aria-hidden="true"
                        >
                          {openBadge}
                        </span>
                        <span
                          className="di-scan-visually-hidden"
                          data-testid="di-scan-attention-reason"
                        >
                          {openSpoken}
                        </span>
                      </>
                    )}
                  </button>
                ))}
              </div>
              <label className="di-scan-search">
                <SearchIcon />
                <input
                  type="search"
                  value={query}
                  placeholder="Search missions..."
                  aria-label="Search missions"
                  data-testid="di-scan-search"
                  onChange={(event) => setQuery(event.target.value)}
                />
              </label>
            </div>
            {/* B3 routes: between the search and the list, so the route to the world controls and to the tray does not run through every Mission row. Each is drawn only where it can act. */}
            {(onJumpToWorld !== undefined || (minimizedCount > 0 && onJumpToMinimized !== undefined)) && (
              <div className="di-jump-row" data-testid="di-jump-row">
                {onJumpToWorld !== undefined && (
                  <button type="button" className="di-jump" data-testid="di-jump-world" onClick={onJumpToWorld}>
                    Go to the Map
                  </button>
                )}
                {minimizedCount > 0 && onJumpToMinimized !== undefined && (
                  <button type="button" className="di-jump" data-testid="di-jump-minimized" onClick={onJumpToMinimized}>
                    {`Go to minimized surfaces (${minimizedCount})`}
                  </button>
                )}
              </div>
            )}

            <div className="di-scan-list" data-testid="di-missions">
              {missionsRead.kind !== 'read' ? (
                <p
                  className="di-scan-empty"
                  data-testid="di-missions-not-read"
                  data-read={missionsRead.kind}
                >
                  {missionsRead.reason}
                </p>
              ) : shown.length === 0 && needsUnknown.length === 0 ? (
                <p className="di-scan-empty">No missions in this list.</p>
              ) : (
                shown.map((row) => (
                  <button
                    key={row.id}
                    type="button"
                    className="di-scan-row"
                    data-testid="di-scan-row"
                    data-mission-ref={row.id}
                    // B2a (Q8): ONE click docks the Mission. "Selected" means "docked": the same state the rail marks, cleared when it is dismissed
                    aria-current={DI_SCAN_MISSION_VIEWS.includes(view) && selectedId === row.id ? 'true' : undefined}
                    onClick={() => openMission(row.id)}
                  >
                    <span className="di-scan-row-copy">
                      {/* H-2 (pass C, 01-home): the status pills sit on the title row, at its right */}
                      <span className="di-scan-row-top">
                        <span className="di-scan-title">{row.title}</span>
                        <Pills pills={row.pills} />
                      </span>
                      <span className="di-scan-context">{rowContextLine(row)}</span>
                    </span>
                  </button>
                ))
              )}
            </div>

            {needsUnknown.length > 0 && (
              <p
                className="di-scan-empty"
                data-testid="di-missions-needs-unknown"
                // the server's reason codes, kept for diagnostics; the text says it in plain words
                data-reason-codes={[
                  ...new Set(
                    needsUnknown.flatMap((row) => (row.needsYou.known ? [] : (row.needsYou.why.codes ?? []))),
                  ),
                ].join(' ')}
              >
                {`${needsUnknown.length} more ${needsUnknown.length === 1 ? 'Mission' : 'Missions'} may need you. ${[
                  ...new Set(
                    needsUnknown.flatMap((row) =>
                      row.needsYou.known ? [] : [asSentence(row.needsYou.why.text)],
                    ),
                  ),
                ].join(' ')}`}
              </p>
            )}

        </div>
      </div>
    </>
  );
}
