import './diScanPanel.css';

import {
  type ReactNode,
  type Ref,
  useCallback,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
} from 'react';

import { DI_RAIL_WIDTH_PX } from '../constants.js';
import type { DiDeliveryControl } from '../hooks/useDiDelivery.js';
import type { ReviewSession, ReviewTargetRef } from './diCodeReviewModel.js';
import { DiDeliveryStatus } from './DiDeliveryEntry.js';
import { enteredId } from './diDeliveryModel.js';
import { DI_FOCUS_RING } from './diNavigation.js';
import { scanDiffFor } from './diScanDiffRead.js';
import type {
  DiDeliveryStepView,
  DiScanAvatar,
  DiScanDiffLine,
  DiScanFact,
  DiScanMissionsRead,
  DiScanPill,
  DiScanRow,
  DiScanTab,
  DiScanView,
} from './diScanModel.js';
import {
  decideSeveral,
  deliverySteps,
  DI_COMMENTS_PAID,
  DI_DECIDE_UNAVAILABLE,
  DI_DECIDE_WHAT,
  DI_DELIVERY_END,
  DI_EVIDENCE_LABEL,
  DI_EVIDENCE_NOT_AVAILABLE,
  DI_EVIDENCE_NOT_LISTED,
  DI_MISSIONS_READ,
  DI_SCAN_MISSION_VIEWS,
  DI_SCAN_PAGE_VIEWS,
  DI_SCAN_TABS,
  knownTrue,
  linkedDeliverySteps,
  openAttentionCount,
  railOrchestrators,
  scanThread,
  stateActions,
  trashGate,
  unreadStateFacts,
  visibleScanRows,
} from './diScanModel.js';
import { MissionsPage, NewMissionPage, ProposalPage, ReceptionPage } from './DiScanPages.js';
import type {
  DiNewMissionSource,
  DiReceptionCard,
  DiScanCountDoubt,
  DiScanReception,
} from './diScanPagesModel.js';
import {
  countBadge,
  DI_LAYOUT_NOTE,
  DI_NEW_MISSION_OFFICE,
  receptionHeadline,
} from './diScanPagesModel.js';

export interface DiScanPanelProps {
  rows: readonly DiScanRow[];
  /** Select this row on first paint (the locked mock opens on its first mission). */
  initialSelectedId?: string | null;
  /** Open on this screen. The product starts on the list. */
  initialView?: DiScanView;
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
   * Opens the EXISTING Mission request workspace, from the one plainly labelled link on the New Mission page (lead Q1).
   * The rail's + New Orchestrator and Cmd N open the drawn New Mission page instead. Two New Mission surfaces coexisting
   * is TRANSITIONAL, owed convergence at Stage 4. Absent: the link stays, disabled, and says why.
   */
  onOpenRequestWorkspace?: (opener: HTMLElement) => void;
  /** Opens the existing Settings dialog (the panel's sliders and the rail's Settings). Absent: Settings says why. */
  onOpenSettings?: () => void;
  /**
   * Stage 4: the ONE review session (the operational modal's own hook and reducer) and its controls. The scan Code Diff reads the
   * selected Mission through it (an admitted reference, never a path) and draws the answer in the nested editor: no modal.
   */
  reviewSession?: ReviewSession | null;
  onReviewBegin?: (target: ReviewTargetRef) => void;
  onReviewEnd?: () => void;
  onReviewChoose?: (path: string | null) => void;
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
  /** G1/G2: the request workspace's unsent Ask draft, as the workspace holds it (New Mission never replaces a different one). */
  askDraft?: string;
  /**
   * G1/G2: hands New Mission's ask to the EXISTING workspace's Ask (`transfer`: the text to put there, or null to change nothing) and opens it there; nothing is sent. Absent: no workspace; New
   * Mission's control is drawn disabled and says why.
   */
  onContinueAsk?: (transfer: string | null, opener: HTMLElement) => void;
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
}

/** The list hides nothing (there is no hide gesture; V-6). */
const NOTHING_HIDDEN: ReadonlySet<string> = new Set();

/** Every screen the panel has; anything else (a stale 'herdr' request) opens on the list. */
const VIEWS: readonly DiScanView[] = ['list', ...DI_SCAN_MISSION_VIEWS, ...DI_SCAN_PAGE_VIEWS];

/** Said beside every action drawn at this stage (Stage 2 is layout only; Stage 4 wires the routes). */
const LAYOUT_NOTE = DI_LAYOUT_NOTE;
/** Herdr opens the machine's own terminal on the Mission's herd, where the person types (owner decision 1). */
const HERDR_REASON =
  'Herdr opens in your own terminal. That is not connected in this layout preview.';
/**
 * The trash's scope, true at FULL scope (owner decision 2): DI's own Mission data, the herd tools' own terminal history,
 * and the model providers' own session transcripts. The summary and the approval record survive, and a partial delete
 * is said as partial (the eventual control reports per owner; nothing is deleted at this stage).
 */
const TRASH_KEEPS =
  'Deletes this Mission’s reasoning, transcripts and logs: DI’s copies, the herd’s own terminal history, and the providers’ own session transcripts. Keeps the summary and the approval record. If any part cannot be deleted, it says what remains.';
/** Said where this build does not read a Mission's approval, repository, stall and log facts (owed by Stage 4). */
const STATE_NOT_READ =
  'Approvals, repository requests, stalls and logs are not read in this layout preview.';
const TRASH_NOT_CONNECTED = 'Deleting a Mission is not connected in this layout preview.';
const TRASH_LABEL = 'Hold to delete this Mission’s data';
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
};
const GATE_TONE: Record<DiDeliveryStepView['gate'], string> = {
  ready: 'needs',
  locked: 'idle',
  done: 'pass',
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
          <span className="di-scan-dot" data-tone={pill.tone} aria-hidden="true" />
          {pill.label}
        </span>
      ))}
    </span>
  );
}

function Avatar({ member, large = false }: { member: DiScanAvatar; large?: boolean }) {
  const art = assetUrl(member.palette);
  return (
    <span
      className={large ? 'di-scan-avatar di-scan-avatar-lg' : 'di-scan-avatar'}
      data-bust={art.bust ? 'true' : 'sheet'}
      style={{ backgroundImage: `url("${art.url}")` }}
      role="img"
      aria-label={member.name}
    />
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
function GearOutlineIcon() {
  return (
    <svg width="18" height="18" viewBox="0 0 24 24" aria-hidden="true">
      <path
        fill="none"
        stroke="currentColor"
        strokeWidth="1.3"
        strokeLinejoin="round"
        d="M9.6 1.8h4.8l.4 2.3c.7.2 1.3.5 1.9.9l2.1-1.1 2.4 2.4-1.1 2.1c.4.6.7 1.2.9 1.9l2.3.4v4.8l-2.3.4c-.2.7-.5 1.3-.9 1.9l1.1 2.1-2.4 2.4-2.1-1.1c-.6.4-1.2.7-1.9.9l-.4 2.3H9.6l-.4-2.3a7 7 0 0 1-1.9-.9l-2.1 1.1-2.4-2.4 1.1-2.1a7 7 0 0 1-.9-1.9l-2.3-.4V9.7l2.3-.4c.2-.7.5-1.3.9-1.9L2.8 5.3 5.2 2.9l2.1 1.1c.6-.4 1.2-.7 1.9-.9l.4-2.3ZM12 8.6a3.4 3.4 0 1 0 .01 0Z"
      />
    </svg>
  );
}

function SlidersIcon() {
  return (
    <svg width="22" height="22" viewBox="0 0 24 24" aria-hidden="true">
      <path
        d="M3.5 6.5h17M3.5 12h17M3.5 17.5h17"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.7"
        strokeLinecap="round"
      />
      <circle
        cx="16.5"
        cy="6.5"
        r="2.15"
        fill="var(--di-scan-bg)"
        stroke="currentColor"
        strokeWidth="1.7"
      />
      <circle
        cx="7.2"
        cy="12"
        r="2.15"
        fill="var(--di-scan-bg)"
        stroke="currentColor"
        strokeWidth="1.7"
      />
      <circle
        cx="12"
        cy="17.5"
        r="2.15"
        fill="var(--di-scan-bg)"
        stroke="currentColor"
        strokeWidth="1.7"
      />
    </svg>
  );
}

function FilterIcon() {
  return (
    <svg width="20" height="20" viewBox="0 0 24 24" aria-hidden="true">
      <path
        d="M3 4.2h18L14.4 12.4V20h-4.8v-7.6L3 4.2Z"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.7"
        strokeLinejoin="round"
      />
    </svg>
  );
}

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
function Delivery({ row, session }: { row: DiScanRow; session: DiDeliveryControl | null }) {
  const link = row.deliveryLink;
  const real = link !== undefined;
  const target = link?.kind === 'linked' ? link.deliveryId : null;
  const setLinked = session?.setLinked;
  // Mission details' session follows the shown Mission's link: its ONE id is this Mission's linked delivery, or none
  useEffect(() => {
    if (real && setLinked !== undefined) setLinked(target);
  }, [real, setLinked, target]);
  const wired = target !== null && session !== null;
  // the session acts for THIS Mission only while its one id is this Mission's link
  const current = session !== null && target !== null && enteredId(session.state) === target;
  const blocked = session === null || session.state.pending !== null || session.state.unvouched !== null;
  const steps = wired
    ? linkedDeliverySteps(current && session !== null ? session.state.view : null)
    : deliverySteps(row.delivery);
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
      {link !== undefined && (
        <p className="di-scan-stage-note" data-testid="di-scan-delivery-link">
          {link.kind === 'linked' ? `The delivery linked to this Mission: ${link.deliveryId}. ${link.words}` : link.words}
        </p>
      )}
      {target !== null && session === null && (
        <p className="di-scan-stage-note" data-testid="di-scan-delivery-no-session">
          {DI_DELIVERY_NO_SESSION}
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
              <span className="di-scan-step-reason">{step.reason}</span>
            )}
          </li>
        ))}
      </ol>
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
          {current && (
            <DiDeliveryStatus
              control={session}
              onlyAbout={target}
              buttonClass="di-scan-step-action"
            />
          )}
        </>
      )}
      {/* what delivery produces, as text: there is no merge step and no control for one (owner ruling, 2026-10-28) */}
      <p className="di-scan-stage-note" data-testid="di-scan-delivery-end">
        {DI_DELIVERY_END}
      </p>
      {/* the layout note describes layout-only controls: a wired Delivery draws none */}
      {!wired && (
        <p className="di-scan-stage-note" id="di-scan-delivery-note">
          {LAYOUT_NOTE}
        </p>
      )}
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
const DI_DELIVERY_NO_SESSION = 'This window has no delivery session, so this delivery is not read here.';

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
  const decideLive = decideTarget !== null && onOpenDecision !== undefined;
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
  return (
    <section
      className="di-scan-state"
      data-testid="di-scan-state-actions"
      aria-label="Actions for this Mission"
    >
      {/* Unread is not absent: where a fact is not read, say so (and what Reception says), never "nothing is waiting". */}
      {!row.state.running.known && (
        <p className="di-scan-stage-note" data-testid="di-scan-state-running-unknown">
          {`Whether this Mission is running is not known: ${row.state.running.why.text}.`}
        </p>
      )}
      {unread.length > 0 ? (
        <p className="di-scan-stage-note" data-testid="di-scan-state-unread">
          {knownTrue(row.needsYou) ? 'Reception lists this Mission as needing you. ' : ''}
          {STATE_NOT_READ}
        </p>
      ) : (
        actions.length === 0 && (
          <p className="di-scan-stage-note">Nothing is waiting on you here.</p>
        )
      )}
      {actions.length > 0 && (
        <>
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
                  aria-describedby={action.navigate || action.reason !== undefined ? 'di-scan-state-reasons' : 'di-scan-state-note'}
                  onClick={live ? (event) => onOpenDecision!(decideTarget!, event.currentTarget) : undefined}
                >
                  {action.label}
                </button>
              );
            })}
          </div>
          {notes.length > 0 && (
            <ul className="di-scan-stage-note" id="di-scan-state-reasons" data-testid="di-scan-state-reasons">
              {notes.map((note) => (
                <li key={note}>{note}</li>
              ))}
            </ul>
          )}
          {(anyWithoutOwnWords || row.state.source === 'fixture') && (
            <p className="di-scan-stage-note" id="di-scan-state-note">
              {row.state.source === 'fixture'
                ? `${LAYOUT_NOTE} Sample state (fixture).`
                : LAYOUT_NOTE}
            </p>
          )}
        </>
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
}: {
  view: 'diff' | 'comments';
  onView: (view: 'diff' | 'comments') => void;
}) {
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
        // Herdr leaves the app for the machine's own terminal; until that launch is wired it opens nothing
        disabled
        title={HERDR_REASON}
        aria-describedby="di-scan-sibling-herdr-reason"
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
        {HERDR_REASON}
      </span>
    </nav>
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
  minimizeRef,
  onOpenSettings,
  reviewSession,
  onReviewBegin,
  onReviewEnd,
  onReviewChoose,
  onOpenDecision,
  renderEvidence,
  fixtureBanner,
  missionDelivery,
}: {
  view: 'mission' | 'diff' | 'comments' | 'evidence';
  row: DiScanRow;
  /** Where Back from Mission details goes (M1). */
  origin: DiScanOrigin;
  /** Opened from Reception's Open Mission: bring Delivery into view and focus (R11). */
  focusDelivery: boolean;
  onDeliveryFocused: () => void;
  onList: () => void;
  onBack: () => void;
  onMission: () => void;
  onView: (view: 'mission' | 'diff' | 'comments' | 'evidence') => void;
  minimizeRef?: Ref<HTMLButtonElement>;
  onOpenSettings?: () => void;
  reviewSession?: ReviewSession | null;
  onReviewBegin?: (target: ReviewTargetRef) => void;
  onReviewEnd?: () => void;
  onReviewChoose?: (path: string | null) => void;
  onOpenDecision?: (requestRef: string, opener: HTMLElement) => void;
  renderEvidence?: (missionRef: string) => ReactNode;
  fixtureBanner: string | null;
  /** M8: Mission details' own delivery session (null: none). */
  missionDelivery: DiDeliveryControl | null;
}) {
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
  const [findOpen, setFindOpen] = useState(false);
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

  const copyDiff = () => {
    if (!file) return;
    const text = file.lines.map((line) => line.text).join('\n');
    void navigator.clipboard?.writeText(text).catch(() => undefined);
  };

  return (
    <div
      className="di-scan-stage"
      data-testid="di-scan-stage"
      data-view={view}
      data-mission-ref={row.id}
      // the stage starts at the rail's edge (the world yields the same width), so the rail stays beside it
      style={{ left: DI_RAIL_WIDTH_PX }}
    >
      <StageFixture text={fixtureBanner} />
      <header className="di-scan-stage-bar">
        <div className="di-scan-header-lead">
          <button
            type="button"
            ref={minimizeRef}
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
        {view === 'comments' && <Siblings view={view} onView={onView} />}
        {/* Code Diff: the tabs and the tools are ONE wrapping unit, so a header too narrow for them beside the crumb moves them TOGETHER to their own row (layout only: no role, no
            label, DOM order unchanged; see the rules at "the stage header wraps by content" in diScanPages.css) */}
        {view === 'diff' && (
          <div className="di-scan-header-controls">
            <Siblings view={view} onView={onView} />
            <div className="di-scan-header-tools">
              {diff && (
                <>
                  <button
                    type="button"
                    className="di-scan-icon-button"
                    aria-label="Find a file"
                    aria-pressed={findOpen}
                    data-testid="di-scan-diff-find"
                    onClick={() => setFindOpen((open) => !open)}
                  >
                    <SearchIcon />
                  </button>
                  <button
                    type="button"
                    className="di-scan-icon-button"
                    aria-label="Copy the diff"
                    onClick={copyDiff}
                  >
                    <CopyIcon />
                  </button>
                </>
              )}
              <button
                type="button"
                className="di-scan-icon-button"
                aria-label="Settings"
                onClick={() => onOpenSettings?.()}
              >
                <GearOutlineIcon />
              </button>
              <button
                type="button"
                className="di-scan-icon-button"
                aria-label="More"
                disabled
                title="More options are not in this layout preview."
              >
                <MoreIcon />
              </button>
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
            aria-labelledby="di-scan-details-heading"
          >
            <h2 id="di-scan-details-heading">Mission details</h2>
            {row.description !== '' && <p>{row.description}</p>}
            {row.handoff !== '' &&
              row.handoff.split('\n').map((line) => (
                <p key={line} className="di-scan-handoff">
                  {line}
                </p>
              ))}
            <div className="di-scan-card-foot">
              <MetaLine text={row.footer} />
              {row.updated !== '' && <p className="di-scan-updated">{row.updated}</p>}
            </div>
          </section>
          <h2 className="di-scan-swarm-heading">Swarm</h2>
          <div className="di-scan-swarm-box">
            {row.swarm.length === 0 ? (
              <p className="di-scan-stage-note">No swarm is recorded for this Mission.</p>
            ) : (
              <ul className="di-scan-cards" data-testid="di-scan-swarm">
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
                      {member.note !== '' && <span className="di-scan-note">{member.note}</span>}
                      <Fact fact={member.model} testid="di-scan-model" />
                      <Fact fact={member.provider} testid="di-scan-provider" />
                    </span>
                  </li>
                ))}
              </ul>
            )}
          </div>
          <Delivery row={row} session={missionDelivery} />
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
            <button
              type="button"
              className="di-scan-action di-scan-action-secondary"
              data-testid="di-scan-herdr"
              // Herdr leaves the app for the machine's own terminal; until that launch is wired it says so, and opens nothing
              disabled
              title={HERDR_REASON}
              aria-describedby="di-scan-herdr-reason"
            >
              Herdr
            </button>
            <button
              type="button"
              className="di-scan-action di-scan-action-secondary"
              data-testid="di-scan-comments"
              onClick={() => onView('comments')}
            >
              Comments
            </button>
            <button
              type="button"
              className="di-scan-action di-scan-action-secondary"
              data-testid="di-scan-evidence"
              disabled={!evidenceLive}
              aria-describedby={evidenceReason === null ? undefined : 'di-scan-evidence-reason'}
              onClick={() => onView('evidence')}
            >
              {DI_EVIDENCE_LABEL}
            </button>
            <button
              type="button"
              className="di-scan-trash di-scan-trash-boxed"
              aria-label={TRASH_LABEL}
              title={TRASH_LABEL}
              data-testid="di-scan-trash"
              disabled
              aria-describedby="di-scan-trash-note di-scan-trash-reason"
            >
              <TrashIcon />
            </button>
          </div>
          <div className="di-scan-actions-notes">
            <p
              className="di-scan-stage-note"
              id="di-scan-herdr-reason"
              data-testid="di-scan-herdr-reason"
            >
              {HERDR_REASON}
            </p>
            {evidenceReason !== null && (
              <p
                className="di-scan-stage-note"
                id="di-scan-evidence-reason"
                data-testid="di-scan-evidence-reason"
              >
                {evidenceReason}
              </p>
            )}
            <div className="di-scan-trash-notes">
              <p className="di-scan-stage-note" id="di-scan-trash-note">
                {TRASH_KEEPS}
              </p>
              <p
                className="di-scan-stage-note"
                id="di-scan-trash-reason"
                data-testid="di-scan-trash-reason"
              >
                {trashGate(row).open ? TRASH_NOT_CONNECTED : trashGate(row).reason}
              </p>
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
                  ? 'Files are not read in this layout preview.'
                  : placeholder.kind === 'reading'
                    ? 'Reading the files.'
                    : 'No files are shown.'}
              </p>
            </aside>
            <div className="di-scan-editor">
              <div className="di-scan-editor-body" data-testid="di-scan-editor">
                <div
                  className="di-scan-editor-empty"
                  data-testid={placeholder.kind === 'not_read' ? 'di-scan-diff-not-read' : `di-scan-diff-${placeholder.kind}`}
                  role={placeholder.kind === 'not_read' ? undefined : 'status'}
                >
                  {placeholder.kind === 'unavailable' || placeholder.kind === 'refused' ? (
                    <>
                      <p>
                        <strong>{placeholder.title}</strong>
                      </p>
                      <p>{placeholder.detail}</p>
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
            <aside className="di-scan-tree" aria-label="Files">
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
                <span className="di-scan-editor-path" data-testid="di-scan-editor-path">
                  {file?.path ?? entry?.name ?? ''}
                </span>
                <span className="di-scan-fixture-tag">{diff.tag ?? 'Sample diff (fixture)'}</span>
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
              <div className="di-scan-editor-body" data-testid="di-scan-editor">
                {file?.notice !== undefined ? (
                  // real data: the plain words for why a file has no lines (binary, withheld, not UTF-8, still being read ...)
                  <div className="di-scan-editor-empty" data-testid="di-scan-file-notice" data-state={file.notice.state} role="status">
                    <p>
                      <strong>{file.notice.title}</strong>
                    </p>
                    <p>{file.notice.body}</p>
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
          {thread.kind === 'sample' && (
            <>
              <span className="di-scan-fixture-tag">Sample thread (fixture)</span>
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
          {/* X3 (gap 10, D-6, D106): Free has no composer, not even a disabled one, and no claim about a history it
              cannot read; the label is the whole statement */}
          <p className="di-scan-comments-paid" data-testid="di-scan-comments-paid">
            <span className="di-scan-comments-paid-tag">Paid</span>
            {DI_COMMENTS_PAID}
          </p>
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
}: {
  rows: readonly DiScanRow[];
  hidden: ReadonlySet<string>;
  view: DiScanView;
  selectedId: string | null;
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
}) {
  const orchestrators = railOrchestrators(rows, hidden);
  const count = receptionCount ?? 0;
  // the badge and its spoken words express the SAME state as the Reception header: a count, a floor, or not known
  const badgeText = receptionCount === null ? null : countBadge(count, receptionDoubt);
  const spoken = badgeText === null ? undefined : `Reception, ${receptionHeadline(count, receptionDoubt)}`;
  const here = (page: DiScanView[]) => (page.includes(view) ? 'page' : undefined);
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
        aria-current={here(['new', 'proposal'])}
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
            const pill = row.pills[0];
            return (
              <li key={row.id}>
                <button
                  type="button"
                  className="di-rail-item di-rail-orchestrator"
                  data-testid="di-rail-orchestrator"
                  aria-current={
                    DI_SCAN_MISSION_VIEWS.includes(view) && selectedId === row.id
                      ? 'page'
                      : undefined
                  }
                  onClick={() => onOpenMission(row.id)}
                >
                  {pill && (
                    <span className="di-scan-dot" data-tone={pill.tone} aria-hidden="true" />
                  )}
                  <span className="di-rail-orchestrator-title">{row.title}</span>
                  {pill && (
                    <span
                      className="di-rail-orchestrator-status"
                      data-testid="di-rail-orchestrator-status"
                    >
                      {pill.label}
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
          <RailUnavailable
            testid="di-rail-herdr"
            icon={<HerdIcon />}
            label="Herdr"
            reason={HERDR_REASON}
          />
        </li>
        <li>
          <RailUnavailable
            testid="di-rail-connections"
            icon={<LinkIcon />}
            label="Connections"
            reason="Connections are not part of this build."
          />
        </li>
      </ul>

      <div className="di-rail-foot">
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

export function DiScanPanel({
  rows,
  initialSelectedId = null,
  initialView = 'list',
  minimizeRef,
  onMinimize,
  onSelect,
  onOpenRequestWorkspace,
  onOpenSettings,
  reviewSession = null,
  onReviewBegin,
  onReviewEnd,
  onReviewChoose,
  reception = null,
  onOpenRequestItem,
  onOpenDecision,
  renderEvidence,
  askDraft = '',
  onContinueAsk,
  newMission = DI_NEW_MISSION_OFFICE,
  specimen = false,
  missionsRead = DI_MISSIONS_READ,
  fixtureBanner = null,
  openRequest = null,
  missionDelivery = null,
}: DiScanPanelProps) {
  const [tab, setTab] = useState<DiScanTab>('open');
  const [query, setQuery] = useState('');
  const [needsOnly, setNeedsOnly] = useState(false);
  // Nothing in the panel hides a Mission: the only delete affordance is the per-Mission trash (V-6, CONTROL-MAP
  // "trash = Mission delete only"), so the list's hidden set is always empty.
  const hidden = NOTHING_HIDDEN;
  const [selectedId, setSelectedId] = useState<string | null>(initialSelectedId);
  const [view, setView] = useState<DiScanView>(() =>
    VIEWS.includes(initialView) ? initialView : 'list',
  );
  const rootRef = useRef<HTMLDivElement>(null);
  const newRef = useRef<HTMLButtonElement>(null);
  // M1: where Mission details goes Back to (the page it was opened from)
  const [origin, setOrigin] = useState<DiScanOrigin>('list');
  // R11: Reception's Open Mission lands on Mission details with Delivery in view and focus
  const [focusDelivery, setFocusDelivery] = useState(false);
  const deliveryFocused = useCallback(() => setFocusDelivery(false), []);
  // the rail entry that opened the current page: Esc returns focus there
  const pageOpener = useRef<HTMLElement | null>(null);

  const openPage = (page: 'missions' | 'reception' | 'new' | 'proposal', opener?: HTMLElement) => {
    if (opener) pageOpener.current = opener;
    setView(page);
  };

  // Cmd N opens New Mission, as the rail's + New Orchestrator does (AC-UX-02), wherever the host delivers the key.
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (!event.metaKey || event.ctrlKey || event.altKey || event.shiftKey) return;
      if (event.key.toLowerCase() !== 'n') return;
      event.preventDefault();
      pageOpener.current = newRef.current;
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
    return () => aside.removeAttribute('data-stage');
  }, [view]);

  // Esc closes the screen the person is on: a nested screen to its Mission, Mission details to where it was opened from,
  // the proposal to New Mission, and a rail page to Home, with focus back on the rail entry that opened it.
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key !== 'Escape' || view === 'list') return;
      if (view === 'diff' || view === 'comments' || view === 'evidence') setView('mission');
      else if (view === 'mission') setView(origin);
      else if (view === 'proposal') setView('new');
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
    const lost = document.activeElement === null || document.activeElement === document.body;
    if (!lost || view === 'list') return;
    const target = rootRef.current?.querySelector<HTMLElement>(
      '[data-page-heading="true"], [data-testid="di-scan-back"]',
    );
    target?.focus();
  }, [view]);

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
  const shownSelected = shown.find((row) => row.id === selectedId) ?? null;

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

  const openMission = (id: string, options: { delivery?: boolean } = {}) => {
    choose(id);
    // Back returns to the page the Mission was opened from; opening from a Mission's own screens keeps that origin
    if (view === 'list' || view === 'missions' || view === 'reception') setOrigin(view);
    setFocusDelivery(options.delivery === true);
    setView('mission');
  };

  const shownGate = shownSelected ? trashGate(shownSelected) : null;

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
      />
      <div ref={rootRef} className="di-scan-body" data-testid="di-scan-panel" data-view={view}>
        {view === 'list' && (
          <>
            <header className="di-scan-header">
              <div className="di-scan-header-lead">
                <button
                  type="button"
                  ref={minimizeRef}
                  className="di-scan-mark"
                  onClick={onMinimize}
                  title="Minimize the office panel"
                  aria-label="Minimize the office panel"
                  data-testid="di-office-panel-minimize"
                >
                  <DiMark />
                </button>
                {/* the labelled SAMPLE specimen says so on Home too, as every specimen page does (lead's labelling condition) */}
                {specimen && (
                  <span className="di-scan-fixture-tag" data-testid="di-home-sample-tag">
                    Sample Missions (fixture)
                  </span>
                )}
              </div>
              <div className="di-scan-header-tools">
                <button
                  type="button"
                  className="di-scan-icon-button"
                  aria-label="Settings"
                  data-testid="di-scan-settings"
                  onClick={() => onOpenSettings?.()}
                >
                  <SlidersIcon />
                </button>
                <button
                  type="button"
                  className="di-scan-icon-button"
                  aria-label="Filter missions"
                  aria-pressed={needsOnly}
                  data-testid="di-scan-filter"
                  onClick={() => setNeedsOnly((value) => !value)}
                >
                  <FilterIcon />
                </button>
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
                    aria-pressed={selectedId === row.id}
                    onClick={() => (selectedId === row.id ? openMission(row.id) : choose(row.id))}
                  >
                    <span className="di-scan-row-copy">
                      {/* H-2 (pass C, 01-home): the status pills sit on the title row, at its right */}
                      <span className="di-scan-row-top">
                        <span className="di-scan-title">{row.title}</span>
                        <Pills pills={row.pills} />
                      </span>
                      <span className="di-scan-context">{row.context}</span>
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

            {shownSelected && (
              <section
                className="di-scan-detail"
                data-testid="di-scan-detail"
                aria-label={shownSelected.title}
              >
                <button
                  type="button"
                  className="di-scan-detail-open"
                  data-testid="di-scan-open-mission"
                  onClick={() => openMission(shownSelected.id)}
                >
                  <span className="di-scan-detail-top">
                    <span className="di-scan-title">{shownSelected.title}</span>
                    <Pills pills={shownSelected.pills} />
                  </span>
                  {shownSelected.description !== '' && (
                    <p className="di-scan-desc" title={shownSelected.description}>
                      {shownSelected.description}
                    </p>
                  )}
                  {shownSelected.swarm.length > 0 && (
                    <ul className="di-scan-swarm" data-testid="di-scan-swarm">
                      {shownSelected.swarm.map((member) => (
                        <li key={member.role} className="di-scan-member">
                          <span className="di-scan-role">{member.role}</span>
                          {/* H-3 (pass C, 01-home): a small avatar with the name beside it */}
                          <span className="di-scan-member-who">
                            <Avatar member={member} />
                            <span className="di-scan-name">{displayName(member)}</span>
                          </span>
                        </li>
                      ))}
                    </ul>
                  )}
                </button>
                <div className="di-scan-detail-foot">
                  <MetaLine text={shownSelected.footer} />
                  <button
                    type="button"
                    className="di-scan-trash"
                    aria-label={TRASH_LABEL}
                    title={`${TRASH_LABEL}. ${shownGate?.open ? TRASH_NOT_CONNECTED : shownGate?.reason}`}
                    data-testid="di-scan-trash"
                    disabled
                    aria-describedby="di-scan-detail-trash-reason"
                  >
                    <TrashIcon />
                  </button>
                  <span className="di-scan-visually-hidden" id="di-scan-detail-trash-reason">
                    {TRASH_KEEPS} {shownGate?.open ? TRASH_NOT_CONNECTED : shownGate?.reason}
                  </span>
                </div>
              </section>
            )}
          </>
        )}

        {(view === 'mission' || view === 'diff' || view === 'comments' || view === 'evidence') && selected && (
          <Stage
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
            minimizeRef={minimizeRef}
            onOpenSettings={onOpenSettings}
            reviewSession={reviewSession}
            onReviewBegin={onReviewBegin}
            onReviewEnd={onReviewEnd}
            onReviewChoose={onReviewChoose}
            onOpenDecision={onOpenDecision}
            renderEvidence={renderEvidence}
            fixtureBanner={fixtureBanner}
            missionDelivery={missionDelivery}
          />
        )}

        {DI_SCAN_PAGE_VIEWS.includes(view) && (
          <div
            className="di-scan-stage di-scan-page-stage"
            data-testid="di-scan-stage"
            data-view={view}
            // a rail page starts at the rail's edge, like a Mission's screens
            style={{ left: DI_RAIL_WIDTH_PX }}
          >
            <StageFixture text={fixtureBanner} />
            {view === 'missions' && (
              <MissionsPage
                rows={rows}
                specimen={specimen}
                read={missionsRead}
                onOpenMission={openMission}
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
                onOpenRequestWorkspace={onOpenRequestWorkspace}
                askDraft={askDraft}
                onContinueAsk={onContinueAsk}
                onOpenMissions={() => setView('missions')}
                onOpenProposal={() => setView('proposal')}
              />
            )}
            {view === 'proposal' &&
              (newMission.proposal === null ? (
                <p className="di-scan-stage-note">No proposal is drawn here.</p>
              ) : (
                <ProposalPage proposal={newMission.proposal} onBack={() => setView('new')} />
              ))}
          </div>
        )}
      </div>
    </>
  );
}
