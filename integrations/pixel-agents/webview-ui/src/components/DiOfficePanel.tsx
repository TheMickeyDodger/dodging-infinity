import './diOperationalPanel.css';

import { createContext, useContext, useEffect, useLayoutEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';

import type {
  DiHerdrTask,
  DiMember,
  DiMission,
  DiMissionV2,
  DiObservedValue,
  DiOfficeState,
  DiReportFact,
  DiRuntimeAgent,
  DiRuntimeAgents,
} from '../../../core/src/messages.js';
import { DI_PANEL_WIDTH_PX, DI_SELECTION_POLL_MS } from '../constants.js';
import type { OfficeState } from '../office/engine/officeState.js';
import type { DiCodeReviewAccess } from './DiCodeReviewEntry.js';
import { DiCodeReviewEntry } from './DiCodeReviewEntry.js';
import {
  reviewNotOfferedConsequence,
  statesReviewNotOffered,
  unavailableWords,
} from './diCodeReviewModel.js';
import type { DiEvidenceAccess } from './DiEvidenceLibrary.js';
import { DiEvidenceLibrary } from './DiEvidenceLibrary.js';
import type { DiKeptView } from './diMinimize.js';
import {
  focusExposed,
  keepView,
  restoreFocus,
  restoreScroll,
  useLastContentFocus,
} from './diMinimize.js';
import { DiMinimizedChip } from './DiMinimizedTray.js';
import type { DiMissionRequestAccess } from './DiMissionRequestEntry.js';
import { DiMissionRequestEntry } from './DiMissionRequestEntry.js';
import { requestsUnavailable } from './diMissionRequestModel.js';
import { DiMissionStateSamples } from './DiMissionStateSamples.js';
import { useDiModal } from './diModal.js';
import type { DiFreshness, DiRecord, DiRecordTarget, DiSelectionMemory } from './diNavigation.js';
import {
  DI_FOCUS_RING,
  findRecords,
  missingRecordReason,
  officeFreshness,
  plainBinding,
  plainIdentityReason,
  plainIdentityState,
  plainMemberIdentity,
  plainMissionOutcomes,
  plainNotLive,
  plainStanding,
  plainUnreported,
  recordByKey,
  recordForCharacter,
  rootLabel,
} from './diNavigation.js';
import {
  DI_FIXTURE_BANNER,
  DI_UNCONFIRMED_LABEL,
  diZoneLabel,
  filterMissions,
  formatActivity,
  formatBoundedList,
  formatEpoch,
  formatFactState,
  formatLinkedChildCoverage,
  formatListState,
  formatLive,
  formatLivePlain,
  formatMaybe,
  formatMemberChain,
  formatObserved,
  formatReviewPreamble,
  formatRuntimeAgentsStatement,
  formatTaskChain,
  formatWaiting,
  freshnessClass,
  groupMembersByChain,
  missionZone,
  navigateStep,
  navigationOrder,
  nextMissionCharacter,
  observedClass,
  WAITING_CLASS,
} from './diOfficeFormat.js';
import type { DiOpStatusResult } from './diOperationalStatus.js';
import {
  backendNormalWait,
  memberTerminalNotes,
  missionLabel,
  missionObjective,
  missionStatus,
} from './diOperationalStatus.js';
import type { DiAgentPhrase, DiRuntimeGroup } from './diPlainSummary.js';
import {
  agentNow,
  agentPhrase,
  firstLayerName,
  missionResults,
  missionTitle,
  navigationOrientation,
  plainObserved,
  runtimeAgentMeaning,
  runtimeGroups,
  shortMissionRefs,
  teamSummary,
  viewLimits,
} from './diPlainSummary.js';
import { DiReceptionInbox } from './DiReceptionInbox.js';
import type { DiReceptionInboxAccess } from './diReceptionInboxState.js';
import { attentionLine, buildInbox, panelSourceOf } from './diReceptionItems.js';
import { useRovingFocus } from './diRovingFocus.js';
import { useStickyScrollPad } from './diStickyPad.js';

interface DiOfficePanelProps {
  state: DiOfficeState;
  officeState: OfficeState;
  /** Owned by App, which narrows the office canvas while the panel is docked open. */
  collapsed: boolean;
  onCollapsedChange: (collapsed: boolean) => void;
  /**
   * The panel's width while open (App owns the docked layout, diPanelLayout.ts: its design width, or what the window leaves it
   * beside the strip, so its content is never pushed off the window). Absent: the design width.
   */
  widthPx?: number;
  /**
   * Show the fixture preview's words INSIDE the panel (App decides, diPanelLayout.ts): used where the world has no room for the floating
   * banner with the panel open, so fixture labelling stays visible and nothing floats over the panel. Absent: not shown here.
   */
  fixtureNote?: boolean;
  /** Server-announced characters no office snapshot has confirmed: reported, never drawn. */
  announced?: ReadonlyArray<{ id: number; missionRef: string | null }>;
  /** item 2: the evidence library's content seam (absent: the library lists, never reads). */
  evidence?: DiEvidenceAccess | null;
  /** item 3: the live code review seam (absent: the offer is stated, nothing can be opened). */
  review?: DiCodeReviewAccess | null;
  /** item 4: the Mission request workspace seam (absent: no entry is drawn). */
  mission?: DiMissionRequestAccess | null;
  /**
   * The reception inbox seam (owned by App): the sidebar's attention entry and the in-world desk are
   * two entrances to ONE dialog. Absent: the attention line is words only and no inbox exists.
   */
  receptionInbox?: DiReceptionInboxAccess | null;
}

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="di-op-row">
      <span>{label}</span>
      <span>{children}</span>
    </div>
  );
}

/** feature13: an observed status — a shape and words (never colour alone), then its sentence. */
function StatusBadge({
  result,
  withSentence = true,
}: {
  result: DiOpStatusResult;
  withSentence?: boolean;
}) {
  return (
    <>
      <span className="di-op-status" data-status={result.status} data-testid="di-op-status">
        <span className="di-op-status-glyph" aria-hidden="true">
          {result.glyph}
        </span>
        {result.label}
      </span>
      {withSentence && (
        <span className="di-op-status-sentence" data-testid="di-op-status-sentence">
          {result.sentence}
        </span>
      )}
    </>
  );
}

function Observed({ value }: { value: DiObservedValue }) {
  return <span className={observedClass(value)}>{formatObserved(value)}</span>;
}

/** True only in an EXPLICIT fixture preview: every LIVE in the panel is then qualified. */
const FixtureMode = createContext(false);

function LiveBadge({ live, reason }: { live: boolean; reason: string | null }) {
  const fixture = useContext(FixtureMode);
  return <span className={freshnessClass(live)}>{formatLive(live, reason, fixture)}</span>;
}

/** item 07: LIVE / NOT LIVE in plain words; the reason is shown in the disclosure instead. */
function PlainLiveBadge({ live }: { live: boolean }) {
  const fixture = useContext(FixtureMode);
  return <span className={freshnessClass(live)}>{formatLivePlain(live, fixture)}</span>;
}

/** item 09: the facts no supported source reports for a runtime agent — unknown, never guessed. */
function UnreportedFacts({
  model,
  usage,
  cost,
  progress,
}: {
  model: string;
  usage: string;
  cost: string;
  progress: string;
}) {
  return (
    <>
      <Row label="model requested / configured">
        not reported (no supported source reports a requested model)
      </Row>
      <Row label="model confirmed running">{model}</Row>
      <Row label="usage · cost · progress · ETA">
        {usage} · {cost} · {progress} · unknown (not reported)
      </Row>
    </>
  );
}

const RUNTIME_UNREPORTED = 'unknown (not reported)';

/** Round 11: rendered without the office's own freshness, nothing is presented as current. */
const FRESHNESS_NOT_GIVEN: DiFreshness = {
  current: false,
  reason: null,
  statement: 'Observation time and freshness not given to this view: not shown as current.',
};

function Freshness({ freshness }: { freshness: DiFreshness }) {
  return (
    <div className={freshness.current ? '' : 'text-warning'} data-testid="di-observation-freshness">
      {freshness.statement}
    </div>
  );
}

/**
 * The first layer's freshness: a plain age on the office clock and, when not current, why. The
 * exact ISO time and epoch are the element's title, and in full in the evidence and the inspector.
 */
function PlainFreshness({
  freshness,
  observedAt,
  now,
}: {
  freshness: DiFreshness;
  observedAt: number | null;
  now: number;
}) {
  return (
    <div
      className={freshness.current ? 'di-op-muted' : 'text-warning'}
      data-testid="di-observation-freshness"
      title={freshness.statement}
    >
      {plainObserved(freshness, observedAt, now)}
    </div>
  );
}

/** One short, true phrase for an agent, with a shape so it never rests on colour alone. */
function AgentPhraseView({ phrase }: { phrase: DiAgentPhrase }) {
  return (
    <span className="di-op-phrase" data-tone={phrase.tone} data-testid="di-agent-phrase">
      <span className="di-op-status-glyph" aria-hidden="true">
        {phrase.glyph}
      </span>
      {phrase.text}
    </span>
  );
}

/** item 09: where a record is on the map — a view-only route there, or why it is not drawn. */
function OnTheMap({
  characterId,
  live,
  notLiveReason,
  onShow,
}: {
  characterId: number | null;
  live: boolean;
  notLiveReason: string | null;
  onShow?: (id: number) => void;
}) {
  if (characterId === null)
    return <div data-testid="di-on-the-map">Not drawn now: {plainNotLive(notLiveReason)}.</div>;
  return (
    <div className="di-op-card-meta" data-testid="di-on-the-map">
      <span>On the map{live ? '' : ' (not live)'}.</span>
      {onShow && (
        <button
          type="button"
          className={`di-op-link ${DI_FOCUS_RING}`}
          data-testid="di-show-in-office"
          onClick={() => onShow(characterId)}
          title="View only: moves the selection and camera; it controls nothing"
        >
          Show in the office
        </button>
      )}
    </div>
  );
}

/**
 * item 07/09: a selected RUNTIME agent (verified identity), in FULL — plain outcome first (what is
 * verified, what is not), then that it is not a team member, where it is, and the facts no source
 * reports; the raw identifiers and reasons behind a labelled disclosure. feature13: rendered in the
 * inspector; the dock shows the concise summary (DockRuntimeAgent).
 */
export function SelectedRuntimeAgent({
  agent,
  freshness = FRESHNESS_NOT_GIVEN,
  onShow,
  testid = 'di-selected-runtime-agent',
}: {
  agent: DiRuntimeAgent;
  freshness?: DiFreshness;
  onShow?: (id: number) => void;
  testid?: string;
}) {
  return (
    <div className="di-op-selected-body" data-testid={testid}>
      <div className="di-op-selected-title">
        {agent.role ?? agent.logical} · {agent.logical} · {rootLabel(agent)}
      </div>
      <div className="di-op-card-meta">
        <AgentPhraseView phrase={agentPhrase(agent)} />
      </div>
      <div>{runtimeAgentMeaning(agent)}</div>
      <div data-testid="di-selected-runtime-plain">{agent.plainText}</div>
      <div className="text-text-muted">
        No Mission or team roster includes this agent: what it does is not shown as any Mission's
        work.
      </div>
      {agent.assignmentState === 'unverified_reference' && (
        <div data-testid="di-selected-runtime-reference">
          Recorded task reference (not verified; not membership): {agent.taskId ?? 'not recorded'} ·
          record status {agent.taskStatus ?? 'not recorded'}
        </div>
      )}
      <OnTheMap
        characterId={agent.characterId}
        live={agent.live}
        notLiveReason={agent.notLiveReason}
        onShow={onShow}
      />
      <Row label="agent / provider">
        {agent.agent} / {agent.provider ?? 'not recorded'}
      </Row>
      <Row label="terminal state">
        {agent.activity === null
          ? 'not recorded'
          : `${formatActivity(agent.activity)}: the terminal's own report; not task progress, completion, review, verification or delivery`}
      </Row>
      <UnreportedFacts
        model={RUNTIME_UNREPORTED}
        usage={RUNTIME_UNREPORTED}
        cost={RUNTIME_UNREPORTED}
        progress={RUNTIME_UNREPORTED}
      />
      <Freshness freshness={freshness} />
      <PlainLiveBadge live={agent.live} />
      <RuntimeAgentProvenance agent={agent} />
    </div>
  );
}

/**
 * C1: a selected UNVERIFIED runtime record, in FULL — what IS observed (a live native agent carries
 * this name) kept apart from what is NOT established (the link to the recorded role binding, so any
 * role or task membership). Never drawn; nothing here makes it verified. feature13: rendered in the
 * inspector; the dock shows the concise summary (DockUnverified).
 */
export function SelectedUnverified({
  agent,
  freshness = FRESHNESS_NOT_GIVEN,
  testid = 'di-selected-unverified',
}: {
  agent: DiRuntimeAgent;
  freshness?: DiFreshness;
  testid?: string;
}) {
  const provider = agent.provider ? `provider ${agent.provider}` : 'provider not recorded';
  return (
    <div className="di-op-selected-body" data-testid={testid}>
      <div className="di-op-selected-title">
        {agent.role ?? agent.logical} · {agent.logical} · {rootLabel(agent)}
      </div>
      <div>
        Runtime identity {plainIdentityState(agent.identityState)}. Not drawn: it is not shown as a
        verified character.
      </div>
      <UnverifiedObserved agent={agent} provider={provider} freshness={freshness} />
      <div data-testid="di-unverified-not-established">
        Not established: its link to the recorded role binding, and so any role or task membership.{' '}
        {plainIdentityReason(agent.identityReason)}.
      </div>
      <div className="text-text-muted">
        A name, pane, workspace or timestamp does not establish that link. Not a team member.
      </div>
      <UnreportedFacts
        model={RUNTIME_UNREPORTED}
        usage={RUNTIME_UNREPORTED}
        cost={RUNTIME_UNREPORTED}
        progress={RUNTIME_UNREPORTED}
      />
      <Freshness freshness={freshness} />
      <RuntimeAgentProvenance agent={agent} />
    </div>
  );
}

function UnverifiedObserved({
  agent,
  provider,
  freshness,
}: {
  agent: DiRuntimeAgent;
  provider: string;
  freshness: DiFreshness;
}) {
  return (
    <div data-testid="di-unverified-observed">
      {freshness.current
        ? `Observed, current: a live native agent named ${agent.agent} is running (${provider}; ${
            agent.activity
              ? `that live agent's terminal reports ${formatActivity(agent.activity)}`
              : 'its terminal state is not recorded'
          }).`
        : `Last observed: a native agent named ${agent.agent} was running (${provider}; ${
            agent.activity
              ? `that agent's terminal reported ${formatActivity(agent.activity)}`
              : 'its terminal state was not recorded'
          }); that observation is NOT current.`}{' '}
      This record&apos;s role binding is not shown to be that agent.
    </div>
  );
}

/** feature13: the dock's "Show details" — one obvious, labelled entry to the full record. */
function OpenInspectorButton({
  onOpen,
  testid,
  describedBy,
}: {
  onOpen: (opener: HTMLElement) => void;
  testid: string;
  describedBy?: string;
}) {
  return (
    <button
      type="button"
      className={`di-op-button di-op-button-primary ${DI_FOCUS_RING}`}
      data-testid={testid}
      aria-describedby={describedBy}
      aria-haspopup="dialog"
      onClick={(event) => onOpen(event.currentTarget)}
    >
      Show details
    </button>
  );
}

/**
 * feature13 — the Selected dock's CONCISE runtime agent: who it is, that it is not a team member,
 * its observed state and where it is; everything else is in the inspector.
 */
function DockRuntimeAgent({
  agent,
  name,
  freshness,
  observedAt,
  now,
  onShow,
  onOpenInspector,
}: {
  agent: DiRuntimeAgent;
  /** Its first-layer name (firstLayerName): the same on the list, here and in its details. */
  name: string;
  freshness: DiFreshness;
  observedAt: number | null;
  now: number;
  onShow: (id: number) => void;
  onOpenInspector: (opener: HTMLElement) => void;
}) {
  return (
    <div className="di-op-selected-body" data-testid="di-selected-runtime-agent">
      <div className="di-op-row-line">
        <div className="di-op-selected-title" data-testid="di-selected-name">
          {name}
        </div>
        <AgentPhraseView phrase={agentPhrase(agent)} />
      </div>
      {/* The agent's own name (its terminal's), exactly: which agent this record is. */}
      <div className="di-op-ref di-op-muted" data-testid="di-selected-agent-name">
        {agent.agent}
      </div>
      {/* The doorways come BEFORE the descriptive lines: the dock scrolls on its own and never grows past a
          share of the panel, so an action placed last can be out of sight on initial selection. */}
      <div className="di-op-actions">
        <OnTheMap
          characterId={agent.characterId}
          live={agent.live}
          notLiveReason={agent.notLiveReason}
          onShow={onShow}
        />
        <OpenInspectorButton onOpen={onOpenInspector} testid="di-open-inspector-selection" />
      </div>
      <div className="di-op-muted" data-testid="di-selected-runtime-plain">
        {runtimeAgentMeaning(agent)}
      </div>
      <PlainFreshness freshness={freshness} observedAt={observedAt} now={now} />
    </div>
  );
}

/** feature13 — the Selected dock's CONCISE record that cannot be linked to its role (full in the inspector). */
function DockUnverified({
  agent,
  name,
  freshness,
  observedAt,
  now,
  onOpenInspector,
}: {
  agent: DiRuntimeAgent;
  /** Its first-layer name (firstLayerName): the same on the list, here and in its details. */
  name: string;
  freshness: DiFreshness;
  observedAt: number | null;
  now: number;
  onOpenInspector: (opener: HTMLElement) => void;
}) {
  return (
    <div className="di-op-selected-body" data-testid="di-selected-unverified">
      <div className="di-op-selected-title" data-testid="di-selected-name">
        {name}
      </div>
      {/* The agent's own name (its terminal's), exactly: which agent this record is. */}
      <div className="di-op-ref di-op-muted" data-testid="di-selected-agent-name">
        {agent.agent}
      </div>
      <div className="text-warning">
        {freshness.current
          ? 'An agent with this name is running'
          : 'An agent with this name was running at the last observation'}
        , but it can’t be safely linked to its recorded role. Not drawn; not a team member.
      </div>
      {/* The doorway comes right after the caveat and BEFORE the details: the dock scrolls on its
          own, so an action placed last can be out of sight on initial selection. */}
      <div className="di-op-actions">
        <OpenInspectorButton onOpen={onOpenInspector} testid="di-open-inspector-selection" />
      </div>
      <div data-testid="di-unverified-not-established">
        Why: {plainIdentityReason(agent.identityReason)}.
      </div>
      {/* The observation's time and whether it is current, always (as for every dock record). */}
      <PlainFreshness freshness={freshness} observedAt={observedAt} now={now} />
    </div>
  );
}

/**
 * A runtime record's raw identifiers and reasons. Rendered open: it only ever appears inside the
 * inspector's "Everything recorded" disclosure, so one deliberate step reaches all of it.
 */
function RuntimeAgentProvenance({ agent }: { agent: DiRuntimeAgent }) {
  return (
    <section className="di-op-selected-body" data-testid="di-runtime-provenance">
      <h4 className="di-op-subhead">Runtime identity provenance (raw identifiers and reasons)</h4>
      <div className="di-op-detail-body di-op-detail-open">
        <Row label="agent">{agent.agent}</Row>
        <Row label="drawn">
          {agent.live ? 'yes' : `no (${agent.notLiveReason ?? 'reason not given'})`}
        </Row>
        <Row label="agent ref">{agent.agentRef}</Row>
        <Row label="source">{agent.sourceId}</Row>
        <Row label="root record">{agent.rootRef}</Row>
        <Row label="registry aliases">
          {agent.rootAliases.length > 0 ? agent.rootAliases.join(', ') : 'none recorded'}
        </Row>
        <Row label="identity">
          {agent.identityState} ({agent.identityReason})
        </Row>
        <Row label="assignment">
          {agent.assignmentState} ({agent.assignmentReason})
        </Row>
        <Row label="task record ref">{agent.taskRef ?? 'none'}</Row>
        <Row label="session (recorded by the role binding)">{agent.session ?? 'not recorded'}</Row>
        <Row label="pane / workspace (recorded)">
          {agent.paneId ?? 'not recorded'} / {agent.workspaceId ?? 'not recorded'}
        </Row>
        <Row label="provider (live listing)">{agent.provider ?? 'not recorded'}</Row>
        <Row label="reported activity">
          {agent.activity === null
            ? 'not recorded'
            : `${formatActivity(agent.activity)} (the agent's own activity; not task work)`}
        </Row>
      </div>
    </section>
  );
}

/**
 * One runtime group's rows: ONE keyboard list (arrow keys, Home and End move within the group, as
 * the verified and not-verified lists always did). Each row: its first-layer name (logical and
 * herd), its activity only where it differs from the group's, and its own reason only where it
 * differs.
 */
function RuntimeGroupRows({
  group,
  selectedKey,
  onSelect,
}: {
  group: DiRuntimeGroup;
  selectedKey: string | null;
  onSelect: (key: string) => void;
}) {
  const rows = useRovingFocus<HTMLButtonElement>();
  return (
    <ul className="di-op-members" aria-label={`${group.heading} (arrow keys move between agents)`}>
      {group.rows.map((row, i) => {
        const a = row.agent;
        const key = `runtime:${a.agentRef}`;
        return (
          <ListRow
            key={a.agentRef}
            testid={a.identityVerified ? 'di-runtime-row' : 'di-unverified-row'}
            selected={selectedKey === key}
            onSelect={() => onSelect(key)}
            refAt={rows.refAt(i)}
            onKeyDown={rows.onKeyDown(i, group.rows.length)}
            title={
              !a.identityVerified
                ? 'Select: see its details (it is never drawn)'
                : a.characterId !== null
                  ? 'Select: show it in the office and see its details'
                  : 'Select: see its details (not drawn now)'
            }
          >
            <span className="di-op-row-line">
              <span>
                {a.logical}
                <span className="di-op-sub" title={a.rootRef}>
                  {' '}
                  · {row.herd}
                </span>
                {row.disambiguator && <span className="di-op-sub"> · {row.disambiguator}</span>}
              </span>
              {row.phrase && <AgentPhraseView phrase={row.phrase} />}
            </span>
            {row.exception && <span className="di-op-row-exception">{row.exception}</span>}
          </ListRow>
        );
      })}
    </ul>
  );
}

/** One selectable row of a keyboard list: a real button (Enter and Space select it). */
function ListRow({
  testid,
  selected,
  onSelect,
  refAt,
  onKeyDown,
  title,
  children,
}: {
  testid: string;
  selected: boolean;
  onSelect: () => void;
  refAt: (el: HTMLButtonElement | null) => void;
  onKeyDown: (event: React.KeyboardEvent<HTMLButtonElement>) => void;
  title: string;
  children: React.ReactNode;
}) {
  return (
    <li>
      <button
        type="button"
        ref={refAt}
        className={`di-op-list-row ${DI_FOCUS_RING}`}
        data-testid={testid}
        aria-current={selected ? 'true' : undefined}
        aria-keyshortcuts="ArrowUp ArrowDown Home End"
        onClick={onSelect}
        onKeyDown={onKeyDown}
        title={title}
      >
        {children}
      </button>
    </li>
  );
}

/**
 * item 07 / C1: runtime agents — live agents identified by a task-native role record that no team
 * represents — then, separately, the records whose runtime identity is NOT verified. The plain
 * outcome first; counts, capture capacity, the registry account and the two-pass limit behind a
 * labelled disclosure. Rows select (view only): no control of any kind.
 */
export function RuntimeAgentsSection({
  runtime,
  selectedKey,
  onSelect,
  freshness = FRESHNESS_NOT_GIVEN,
  now = null,
}: {
  runtime: DiRuntimeAgents | undefined;
  selectedKey: string | null;
  onSelect: (key: string) => void;
  freshness?: DiFreshness;
  /** The office clock (`times.evaluatedAt`): with it, time reads as a plain age. */
  now?: number | null;
}) {
  if (!runtime) {
    return (
      <section className="di-op-secondary text-text-muted" data-testid="di-runtime-absent">
        Runtime agents: not provided by this office.
      </section>
    );
  }
  const capture = runtime.capture;
  const groups = runtimeGroups(runtime);
  const omitted = runtime.agentsTotal - runtime.agents.length;
  const unverifiedListed = runtime.agents.filter((a) => !a.identityVerified).length;
  const unverifiedCount = runtime.counts
    .filter((c) => c.name === 'unverified' || c.name === 'ambiguous' || c.name === 'unavailable')
    .reduce((n, c) => n + c.value, 0);
  return (
    <section
      className="di-op-secondary"
      data-testid="di-runtime-agents"
      aria-labelledby="di-runtime-title"
    >
      <div className="di-op-section-title" id="di-runtime-title">
        Other agents
      </div>
      <div className="di-op-muted" data-testid="di-runtime-statement">
        {runtime.agents.length === 0
          ? formatRuntimeAgentsStatement(runtime)
          : 'Agents found running that no Mission lists as a team member.'}
        {omitted > 0 ? ` ${omitted} more counted, not listed (display cap).` : ''}
      </div>
      {runtime.agents.length > 0 &&
        // When everything below was observed, said ONCE for the section.
        (now === null ? (
          <Freshness freshness={freshness} />
        ) : (
          <PlainFreshness freshness={freshness} observedAt={runtime.observedAt} now={now} />
        ))}
      {groups.map((group) => (
        <section
          key={group.key}
          className="di-op-runtime-group"
          data-testid={group.key === 'not-linked' ? 'di-unverified' : 'di-runtime-group'}
          data-group={group.key}
        >
          <div className="di-op-group-title">
            {group.heading} <span className="di-op-muted">· {group.count}</span>
          </div>
          <div className="di-op-group-sentence">
            {group.sentence}
            {group.key === 'not-linked' && unverifiedCount > unverifiedListed
              ? ` ${unverifiedCount - unverifiedListed} more counted, not listed (display cap).`
              : ''}
          </div>
          {!freshness.current && (
            // A material caveat stays WITH what it qualifies: while the observation is not current,
            // every group says so itself (when current, the time is said once for the section).
            <div className="text-warning" data-testid="di-runtime-group-not-current">
              Not current: {plainNotLive(freshness.reason)}.
            </div>
          )}
          {(group.activity !== null || group.notes.length > 0) && (
            <ul className="di-op-group-notes" data-testid="di-runtime-group-notes">
              {group.activity !== null && <li>{group.activity}</li>}
              {group.notes.map((note) => (
                <li key={note}>{note}</li>
              ))}
            </ul>
          )}
          <RuntimeGroupRows group={group} selectedKey={selectedKey} onSelect={onSelect} />
        </section>
      ))}
      <details data-testid="di-runtime-detail">
        <summary className={DI_FOCUS_RING}>
          Full evidence: counts, capture, registry account, limits and every agent
        </summary>
        <div className="di-op-detail-body">
          <Row label="statement">{formatRuntimeAgentsStatement(runtime)}</Row>
          <Row label="state">
            {runtime.state ?? 'not reported'}
            {runtime.problem ? ` (${runtime.problem})` : ''}
          </Row>
          {runtime.counts.map((c) => (
            <Row key={c.name} label={c.name}>
              {c.value}
            </Row>
          ))}
          <Row label="capture">
            requested {formatMaybe(capture.discovered)} · captured {formatMaybe(capture.captured)} ·
            unavailable {formatMaybe(capture.unavailable)} · over capacity, not observed{' '}
            {formatMaybe(capture.overCapacity)}
            {capture.listingAnchor ? ' · one root observed only to anchor the listing' : ''}
          </Row>
          {runtime.locators && (
            <Row label="registry">
              {runtime.locators.registeredAliases} aliases · {runtime.locators.physicalRoots}{' '}
              physical roots · {runtime.locators.sharedRootGroups} shared-path groups ·{' '}
              {runtime.locators.aliasesWithoutHerdState} without herd state ·{' '}
              {runtime.locators.excludedTotal} excluded
              {runtime.locators.excludedByReason.map((e) => ` · ${e.name} ${e.value}`).join('')}
            </Row>
          )}
          <Row label="limit">
            {runtime.caveat === 'two_pass_agreement_not_atomic_aba_not_detected'
              ? 'Two source passes bracket the runtime observation: their agreement is not atomic, and a change and change-back between them is not detected.'
              : (runtime.caveat ?? 'none stated')}
          </Row>
          <Row label="observed / passes">
            {formatEpoch(runtime.observedAt)} · first pass {formatEpoch(runtime.firstPassAt)} ·
            second pass {formatEpoch(runtime.secondPassAt)}
          </Row>
          {runtime.agentsOverBound > 0 && (
            <Row label="not listed by the producer">{runtime.agentsOverBound}</Row>
          )}
          {runtime.agents.map((a) => (
            <Row key={a.agentRef} label={a.agentRef}>
              {a.agent} · {rootLabel(a)} · {a.plainText} · identity {a.identityState} (
              {a.identityReason}) · assignment {a.assignmentState} ({a.assignmentReason})
              {a.live ? '' : ` · not drawn: ${a.notLiveReason ?? 'reason not given'}`}
            </Row>
          ))}
        </div>
      </details>
    </section>
  );
}
/**
 * item 09: a selected TEAM MEMBER in FULL (feature13: the inspector) — plain outcome first: who it
 * is, the Mission it is a member of, whether its running identity is confirmed, where it is, its
 * terminal state (kept apart from every task/Mission outcome), the model and the facts no source
 * reports; then the code review entry with its explanation, and the complete canonical refs and
 * every reason code in a labelled provenance section (never shortened).
 */
function MemberDetail({
  member,
  mission,
  freshness,
  onShow,
  review,
}: {
  member: DiMember;
  mission: DiMission;
  freshness: DiFreshness;
  /** Absent inside the record disclosure: the first layer already offers the locate action. */
  onShow?: (id: number) => void;
  review: DiCodeReviewAccess | null;
}) {
  const outcomes = plainMissionOutcomes(mission);
  return (
    <div className="di-op-selected-body" data-testid="di-inspector-member">
      <div>Team member of this Mission.</div>
      <Row label="identity">{plainMemberIdentity(member.classification)}</Row>
      <OnTheMap
        characterId={member.characterId}
        live={member.live}
        notLiveReason={member.notLiveReason}
        onShow={onShow}
      />
      <Row label="agent / provider">
        {member.agent} / {member.provider}
      </Row>
      <Row label="task">
        <Observed value={member.taskId} />
        {mission.v2.projected ? ` · ${formatMemberChain(member)}` : ''}
      </Row>
      <Row label="terminal state">
        {formatActivity(member.activity)}: the terminal&apos;s own report; not task progress,
        completion, review, verification or delivery
      </Row>
      <Row label="Mission completion (reported)">{outcomes.completion}</Row>
      <Row label="independent verification">{outcomes.verification}</Row>
      <Row label="review">{outcomes.review}</Row>
      <Row label="delivery">{outcomes.delivery}</Row>
      <UnreportedFacts
        model={plainUnreported(member.model)}
        usage={plainUnreported(member.tokens)}
        cost={plainUnreported(member.cost)}
        progress={plainUnreported(member.progress)}
      />
      <Freshness freshness={freshness} />
      <LiveBadge live={member.live} reason={null} />
      <DiCodeReviewEntry member={member} missionRef={mission.missionRef} access={review} />
      <section className="di-op-selected-body" data-testid="di-canonical-identity">
        <h4 className="di-op-subhead">Provenance: complete refs, sessions and reason codes</h4>
        <div className="di-op-detail-body di-op-detail-open">
          <Row label="member_ref">
            <span className="di-op-ref">{member.memberRef}</span>
          </Row>
          <Row label="mission_ref">
            <span className="di-op-ref">{mission.missionRef}</span>
          </Row>
          <Row label="source">{mission.sourceId}</Row>
          <Row label="task_id">
            <Observed value={member.taskId} />
          </Row>
          <Row label="classification">
            {member.classification} ({member.reason})
          </Row>
          <Row label="drawn">
            {member.live ? 'yes' : `no (${member.notLiveReason ?? 'reason not given'})`}
          </Row>
          <Row label="not live">
            {member.live ? 'no' : `NOT LIVE: ${member.notLiveReason ?? 'reason not given'}`}
          </Row>
          <Row label="session recorded">
            <Observed value={member.sessionRecorded} />
          </Row>
          <Row label="session live">
            <Observed value={member.sessionLive} />
          </Row>
          <Row label="pane">
            <Observed value={member.paneId} />
          </Row>
          <Row label="workspace">
            <Observed value={member.workspaceId} />
          </Row>
          <Row label="producer (declared)">{mission.producerDeclared}</Row>
          <Row label="lifecycle model">{mission.lifecycleModel}</Row>
          <Row label="standing / state">
            {mission.standing} / {mission.lifecycleState}
          </Row>
          <Row label="binding">
            {mission.bindingState} ({mission.bindingReason})
          </Row>
          {mission.v2.projected && (
            <Row label="completion record">
              {mission.v2.missionState.completion.state}
              {mission.v2.missionState.completion.reason
                ? ` (${mission.v2.missionState.completion.reason})`
                : ''}
            </Row>
          )}
          <Row label="code review (exact)">
            {member.codeReview === undefined
              ? 'not reported by this office'
              : `${member.codeReview.state}${member.codeReview.reason ? ` (${member.codeReview.reason})` : ''}${
                  member.codeReview.state === 'offered'
                    ? ''
                    : `: ${unavailableWords(member.codeReview.reason)}`
                }`}
          </Row>
          <Row label="model / cost / tokens / progress (raw)">
            {formatObserved(member.model)} / {formatObserved(member.cost)} /{' '}
            {formatObserved(member.tokens)} / {formatObserved(member.progress)}
          </Row>
        </div>
      </section>
    </div>
  );
}

/**
 * feature13 — the Selected dock's CONCISE team member: who it is and its role; one line of
 * identity and observed state; THEN the doorways (the view-only routes Show in the office and Show
 * this Mission in the list, the code review entry, and one obvious "Show details"); THEN the
 * freshness of that observation and ONE short line naming its Mission by its complete
 * source-qualified ref (the objective is on the card). The doorways come before the descriptive
 * lines on purpose: the dock scrolls on its own, so an action placed last was below its visible
 * area on initial selection at a constrained window. Everything else — the code review explanation,
 * outcomes, what no source reports and the provenance — is in the inspector.
 */
function DockMember({
  member,
  name,
  mission,
  shortRef,
  freshness,
  observedAt,
  now,
  onShow,
  onShowMission,
  onOpenInspector,
  review,
}: {
  member: DiMember;
  /** Its first-layer name (firstLayerName): qualified by its chain only where a name repeats. */
  name: string;
  mission: DiMission;
  shortRef: string;
  freshness: DiFreshness;
  observedAt: number | null;
  now: number;
  onShow: (id: number) => void;
  onShowMission: (missionRef: string) => void;
  onOpenInspector: (opener: HTMLElement) => void;
  review: DiCodeReviewAccess | null;
}) {
  return (
    <div className="di-op-selected-body" data-testid="di-selected-member">
      {/* Its name and its one phrase on ONE line (as the list's rows): the dock is tight. */}
      <div className="di-op-row-line">
        <div className="di-op-selected-title" data-testid="di-selected-name">
          {name}
        </div>
        <AgentPhraseView phrase={agentPhrase(member)} />
      </div>
      {/* The doorways come BEFORE the descriptive lines. The dock scrolls on its own and never grows past a
          share of the panel; at a constrained window an action placed after the freshness sentence and the
          Mission ref was below the dock's visible area on initial selection. */}
      {/* The primary action FIRST ("Show details"), then the view-only routes, then the code review
          entry: the dock scrolls on its own and never grows past a share of the panel, so an action
          placed later can sit in its bottom fade when the panel is tight (a constrained window with
          Find's results open). */}
      <div className="di-op-actions">
        <OpenInspectorButton onOpen={onOpenInspector} testid="di-open-inspector-selection" />
        <OnTheMap
          characterId={member.characterId}
          live={member.live}
          notLiveReason={member.notLiveReason}
          onShow={onShow}
        />
      </div>
      <div className="di-op-actions">
        <button
          type="button"
          className={`di-op-link ${DI_FOCUS_RING}`}
          data-testid="di-show-mission"
          onClick={() => onShowMission(mission.missionRef)}
          title="View only: shows this Mission in the list"
        >
          Show this Mission in the list
        </button>
        <DiCodeReviewEntry
          member={member}
          missionRef={mission.missionRef}
          access={review}
          variant="dock"
        />
      </div>
      <PlainFreshness freshness={freshness} observedAt={observedAt} now={now} />
      <div data-testid="di-selected-member-mission" title={mission.missionRef}>
        Team member of “{missionTitle(mission).text}”{' '}
        <span className="di-op-ref di-op-muted">{shortRef}</span>
      </div>
    </div>
  );
}

/** item 07: a plain Herdr task, said plainly first; its provenance one step behind. */
export function HerdrTaskNote({ herdrTask }: { herdrTask: DiHerdrTask }) {
  return (
    <div className="flex flex-col gap-1" data-testid="di-herdr-task">
      <div>
        Herdr task: a plain Herdr task record, not a Mission Core Mission. Mission Core facts
        (proposal, authorization, revision, proof) are not supported for it.
      </div>
      <details>
        <summary className="cursor-pointer text-text-muted">Herdr task provenance</summary>
        <div className="flex flex-col gap-1 pt-2">
          <Row label="registry aliases">
            {herdrTask.rootAliases.length > 0 ? herdrTask.rootAliases.join(', ') : 'none recorded'}
          </Row>
          <Row label="membership">{herdrTask.membership ?? 'not recorded'}</Row>
          <Row label="schema">
            {herdrTask.schema ?? 'not recorded'} v{formatMaybe(herdrTask.schemaVersion)}
          </Row>
          <Row label="task record version">{formatMaybe(herdrTask.taskRecordVersion)}</Row>
          <Row label="started">{formatEpoch(herdrTask.startedAt)}</Row>
        </div>
      </details>
    </div>
  );
}

/**
 * feature13 — ONE uniform, collapsed Mission card. By default it carries only what decides how the
 * list is read: the objective (or, when the source gave no goal, that limitation, source-qualified),
 * the observed status (shape and word), ONE team line, the office zone and one obvious "Open
 * inspector". A status other than running also says why, on the face. The per-member roster is one
 * step away in a closed "Team members" disclosure (a keyboard list: select a member to see it in
 * the office and the dock), and in full in the inspector.
 */
function MissionCard({
  mission,
  status,
  shortRef,
  zone,
  selectedKey,
  onSelect,
  onLocate,
  onOpenInspector,
}: {
  mission: DiMission;
  status: DiOpStatusResult;
  /** The short source-qualified ref (shortMissionRefs): lookalike objectives stay apart. */
  shortRef: string;
  zone: { label: string; color: string } | null;
  selectedKey: string | null;
  onSelect: (key: string) => void;
  /** View only: select a drawn character and move the camera to it. */
  onLocate: (id: number) => void;
  onOpenInspector: (missionRef: string, opener: HTMLElement, section?: InspectorSection) => void;
}) {
  const holdsSelection = mission.members.members.some(
    (m) => selectedKey === `member:${m.memberRef}`,
  );
  const cardRef = useRef<HTMLElement>(null);
  const rows = useRovingFocus<HTMLButtonElement>();
  const [teamOpen, setTeamOpen] = useState(false);
  // The selected agent's Mission is brought into view, its roster opened on the selected member.
  useEffect(() => {
    if (!holdsSelection) return;
    setTeamOpen(true);
    cardRef.current?.scrollIntoView({ block: 'nearest' });
  }, [holdsSelection]);
  // feature13: the list gives up height to Find's results and to the Selected dock (see
  // diOperationalPanel.css). While this Mission holds the selection, a change of the list's own height
  // brings the Mission back to the TOP of it, so its objective, status and team stay in view.
  useEffect(() => {
    if (!holdsSelection || typeof ResizeObserver === 'undefined') return;
    const card = cardRef.current;
    const region = card?.closest<HTMLElement>('[data-testid="di-op-main"]');
    if (!card || !region) return;
    let height = region.clientHeight;
    let hidden = false;
    const observer = new ResizeObserver(() => {
      // A minimized panel is hidden (height 0), and its first height after that is a RESTORE, not a change of
      // the list's own height: restoring gives back the scroll position the person left (AC3) instead of
      // jumping to this card.
      if (region.clientHeight === 0) {
        hidden = true;
        return;
      }
      if (hidden) {
        hidden = false;
        height = region.clientHeight;
        return;
      }
      if (region.clientHeight === height) return;
      height = region.clientHeight;
      card.scrollIntoView({ block: 'start' });
    });
    observer.observe(region);
    return () => observer.disconnect();
  }, [holdsSelection]);
  const groups = mission.v2.projected
    ? groupMembersByChain(mission.members.members)
    : [{ heading: null as string | null, members: mission.members.members }];
  const flat = groups.flatMap((g) => g.members);
  const objective = missionObjective(mission);
  const title = missionTitle(mission);
  const team = teamSummary(mission);
  const members = mission.members.members;
  const objectiveId = `di-op-objective-${mission.missionRef}`;
  const firstDrawn = members.find((m) => m.characterId !== null)?.characterId ?? null;
  return (
    <article
      ref={cardRef}
      className="di-op-card"
      data-selected-mission={holdsSelection ? 'true' : undefined}
      data-mission-ref={mission.missionRef}
      aria-labelledby={objectiveId}
    >
      {/* What it is for, in plain words: the objective's own first meaningful sentence, an honest
          placeholder when it opens with boilerplate only, or — when the source gives no goal — that
          limitation, source-qualified. The complete objective is the title tooltip and in the
          inspector, unaltered. */}
      <p
        id={objectiveId}
        className="di-op-objective di-op-clamp-2"
        data-goal={objective.kind === 'goal' ? 'goal' : 'none'}
        data-title={title.kind}
        data-testid="di-mission-objective"
        title={objective.text}
      >
        {title.text}
      </p>
      {/* Its observed status, and how many of its team are observed running — process observation,
          never work; "working" only where a terminal reports busy now. */}
      <div className="di-op-card-meta">
        <StatusBadge result={status} withSentence={false} />
        <span className="di-op-team" data-testid="di-mission-team">
          {mission.members.state === 'observed' ? (
            members.length === 0 ? (
              'No team members listed'
            ) : (
              <>
                {team.running}
                {team.activity && <span className="di-op-team-roles"> · {team.activity}</span>}
              </>
            )
          ) : (
            // No roster is observed: said in words on the face; the projected code is in the
            // inspector's provenance.
            <span className={observedClass({ ...mission.members, value: null })}>
              {mission.members.state === 'projected_reason'
                ? `Team not listed: ${plainBinding(mission.bindingState, mission.bindingReason)}`
                : `Team ${mission.members.state === 'unavailable' ? 'unavailable' : 'not recorded'} (the reason is in the inspector)`}
            </span>
          )}
        </span>
        {/* Which record this is, last in reading order: short and source-qualified (the complete ref
            is its tooltip and in the details) — it tells lookalikes apart without outranking the
            goal. Shown at the end of the status line, where that line has room. */}
        <div className="di-op-ref-line" data-testid="di-mission-ref" title={mission.missionRef}>
          <span className="di-op-ref">{shortRef}</span>
          {mission.herdrTask && <span data-testid="di-herdr-task-face"> · Herdr task record</span>}
        </div>
      </div>
      {status.status !== 'running' && (
        // Why it is not running is uncertainty that changes the reading: on the face.
        <div className="di-op-status-sentence" data-testid="di-op-status-sentence">
          {status.sentence}
        </div>
      )}
      {memberTerminalNotes(mission).map((note) => (
        <div key={note} className="di-op-status-sentence" data-testid="di-op-terminal-note">
          {note}
        </div>
      ))}
      {/* What the user can do, named by what they will see: the Mission's people on the map (where
          any are drawn), its evidence, and its full details. */}
      <div className="di-op-card-foot">
        <span className="di-op-actions">
          {firstDrawn !== null && (
            <button
              type="button"
              className={`di-op-link ${DI_FOCUS_RING}`}
              data-testid="di-mission-locate"
              aria-describedby={objectiveId}
              onClick={() => onLocate(firstDrawn)}
              title="View only: selects its first agent on the map and moves the camera there"
            >
              {zone && (
                <span
                  className="di-op-swatch"
                  style={{ background: zone.color }}
                  aria-hidden="true"
                />
              )}
              Show in the office
              {zone && (
                // Its office zone, for assistive tech (the label itself is in the details).
                <span className="sr-only" data-testid="di-mission-zone">
                  office zone {zone.label}
                </span>
              )}
            </button>
          )}
          {mission.evidence && (
            // The evidence library is in the details; this is its direct, labelled doorway.
            <button
              type="button"
              className={`di-op-link ${DI_FOCUS_RING}`}
              data-testid="di-open-evidence"
              aria-describedby={objectiveId}
              aria-haspopup="dialog"
              onClick={(event) =>
                onOpenInspector(mission.missionRef, event.currentTarget, 'evidence')
              }
            >
              Show evidence
            </button>
          )}
        </span>
        <button
          type="button"
          className={`di-op-button ${DI_FOCUS_RING}`}
          data-testid="di-open-inspector-mission"
          aria-describedby={objectiveId}
          aria-haspopup="dialog"
          onClick={(event) => onOpenInspector(mission.missionRef, event.currentTarget)}
        >
          Show details
        </button>
      </div>
      {members.length > 0 && (
        <details
          className="di-op-team-details"
          data-testid="di-mission-roster"
          open={teamOpen}
          onToggle={(event) => setTeamOpen(event.currentTarget.open)}
        >
          <summary className={DI_FOCUS_RING}>
            Team ({members.length}): {team.roles.join(', ')}
          </summary>
          {groups.map((group, gi) => (
            <div key={gi} data-testid="di-member-group">
              {group.heading !== null && (
                <div className="di-op-member-group-head">
                  {group.heading} ({group.members.length})
                </div>
              )}
              <ul
                className="di-op-members"
                aria-label="Members of this Mission (arrow keys move between them)"
              >
                {group.members.map((member) => {
                  const i = flat.indexOf(member);
                  return (
                    <ListRow
                      key={member.memberRef}
                      testid="di-member-row"
                      selected={selectedKey === `member:${member.memberRef}`}
                      onSelect={() => onSelect(`member:${member.memberRef}`)}
                      refAt={rows.refAt(i)}
                      onKeyDown={rows.onKeyDown(i, flat.length)}
                      title={
                        member.characterId !== null
                          ? 'Select: show this agent in the office and the dock (view only)'
                          : 'Select: inspect it (not drawn now)'
                      }
                    >
                      <span className="di-op-row-line">
                        <span>
                          {member.logical} <span className="di-op-sub">· {member.role}</span>
                          {mission.v2.projected && (
                            <span className="di-op-sub">
                              {' '}
                              · {member.chain}
                              {member.chain === 'child' && member.chainTaskId
                                ? ` ${member.chainTaskId}`
                                : ''}
                            </span>
                          )}
                        </span>
                        <AgentPhraseView phrase={agentPhrase(member)} />
                      </span>
                    </ListRow>
                  );
                })}
              </ul>
            </div>
          ))}
        </details>
      )}
    </article>
  );
}

/**
 * feature13 — the Mission sections of the INSPECTOR: the full objective and observed status (with
 * the exact fields it rests on), the team in full, checkpoints and dependencies, evidence and
 * review, provenance and raw codes. The full-size reading surface for everything the card leaves
 * out; read-only, controls nothing.
 */
function MissionInspectorSections({
  mission,
  status,
  evidence,
  review,
  onShow,
  reviewStatedFor,
}: {
  mission: DiMission;
  status: DiOpStatusResult;
  evidence: DiEvidenceAccess | null;
  review: DiCodeReviewAccess | null;
  onShow: (id: number) => void;
  /** The member whose not-offered code review this popup's summary already states: its own card
   *  does not repeat it (said once per popup). */
  reviewStatedFor?: string;
}) {
  const objective = missionObjective(mission);
  const team = teamSummary(mission);
  const sharedReviewReason = sharedReviewUnavailable(mission);
  const groups = mission.v2.projected
    ? groupMembersByChain(mission.members.members)
    : [{ heading: null as string | null, members: mission.members.members }];
  return (
    <>
      <section className="di-op-inspector-section" data-testid="di-inspector-objective">
        <h3>Objective and observed status</h3>
        <p
          className="di-op-inspector-objective"
          data-goal={objective.kind === 'goal' ? 'goal' : 'none'}
        >
          {objective.text}
        </p>
        {objective.kind === 'no-goal' && objective.raw !== null && (
          <Row label="objective as exported">“{objective.raw}”</Row>
        )}
        <div className="di-op-card-meta">
          <StatusBadge result={status} withSentence={status.status !== 'running'} />
        </div>
        <div data-testid="di-mission-results">{missionResults(mission)}</div>
        {memberTerminalNotes(mission).map((note) => (
          <div key={note} className="di-op-status-sentence">
            {note}
          </div>
        ))}
        {mission.herdrTask && <HerdrTaskNote herdrTask={mission.herdrTask} />}
      </section>
      <section className="di-op-inspector-section" data-testid="di-inspector-team">
        <h3>
          Team{' '}
          <span className="di-op-muted">
            ·{' '}
            {mission.members.state === 'observed'
              ? [team.running, team.activity].filter(Boolean).join(' · ')
              : formatListState(
                  mission.members.state,
                  mission.members.reason,
                  mission.members.members.length,
                )}
          </span>
        </h3>
        {mission.members.members.length === 0 && (
          <div className="di-op-muted">No team member is listed for this Mission record.</div>
        )}
        {sharedReviewReason !== undefined && (
          <div className="di-op-muted" data-testid="di-team-code-review-unavailable">
            {reviewNotOfferedConsequence(sharedReviewReason)}
          </div>
        )}
        {mission.members.members.some((m) => m.codeReview?.state === 'offered') && (
          // What every offered review route does, said ONCE for the team (each card keeps its button).
          <div className="di-op-muted" data-testid="di-team-code-review-hint">
            &ldquo;Open code review&rdquo; shows what is changed in that member&rsquo;s assignment
            worktree, in a large window. View only: it changes nothing.
          </div>
        )}
        {groups.map((group, gi) => (
          <div key={gi} className="di-op-selected-body">
            {group.heading !== null && (
              <div className="di-op-member-group-head">
                {group.heading} ({group.members.length})
              </div>
            )}
            {group.members.map((member) => (
              <div
                key={member.memberRef}
                className="di-op-inspector-member"
                data-testid="di-inspector-team-member"
                data-member-ref={member.memberRef}
              >
                <div className="di-op-row-line">
                  <span className="di-op-selected-title">
                    {member.logical} <span className="di-op-muted">· {member.role}</span>
                  </span>
                  <AgentPhraseView phrase={agentPhrase(member)} />
                </div>
                <OnTheMap
                  characterId={member.characterId}
                  live={member.live}
                  notLiveReason={member.notLiveReason}
                  onShow={onShow}
                />
                {sharedReviewReason === undefined && member.memberRef !== reviewStatedFor && (
                  <DiCodeReviewEntry
                    member={member}
                    missionRef={mission.missionRef}
                    access={review}
                    variant="row"
                  />
                )}
              </div>
            ))}
          </div>
        ))}
      </section>
      <section className="di-op-inspector-section" data-testid="di-inspector-checkpoints">
        <h3>Checkpoints and dependencies</h3>
        {mission.v2.projected ? (
          <MissionV2Blocks v2={mission.v2} />
        ) : (
          <div className="di-op-muted" data-testid="di-checkpoints-not-reported">
            This office doesn&apos;t report checkpoints or dependencies for its Missions. Why is in
            &ldquo;What this office does not report&rdquo; below.
          </div>
        )}
      </section>
      <section
        className="di-op-inspector-section"
        data-testid="di-inspector-evidence"
        id="di-inspector-evidence"
        tabIndex={-1}
        aria-label="Evidence and review"
      >
        <h3>Evidence and review</h3>
        {mission.evidence ? (
          <DiEvidenceLibrary mission={mission} access={evidence} />
        ) : (
          <div className="di-op-muted">
            No evidence library is projected for this Mission record.
          </div>
        )}
        <div className="di-op-muted" data-testid="di-evidence-review-note">
          {mission.members.members.some((m) => m.codeReview?.state === 'offered')
            ? 'Code review, where it is offered, is with each team member in the Team section above.'
            : mission.members.members.some((m) => m.codeReview !== undefined)
              ? 'Code review is not offered for any member of this team: the Team section above says why.'
              : 'This office says nothing about code review for this Mission.'}
        </div>
      </section>
      <details className="di-op-inspector-section" data-testid="di-inspector-provenance">
        <summary className={DI_FOCUS_RING}>Exact identity, provenance and raw codes</summary>
        <div className="di-op-detail-body di-op-detail-open">
          <Row label="mission_ref">
            <span className="di-op-ref" data-testid="di-mission-qualifier">
              {mission.missionRef}
            </span>
          </Row>
          <Row label="in words">
            <span data-testid="di-mission-plain">
              {plainStanding(mission.standing, mission.lifecycleState)} ·{' '}
              {plainBinding(mission.bindingState, mission.bindingReason)} · source{' '}
              {mission.sourceId}
            </span>
          </Row>
          <Row label="office zone (Areas overlay)">{diZoneLabel(mission)}</Row>
          <Row label="source">
            {mission.sourceId} ({mission.sourceKind}) · native {mission.nativeId}
          </Row>
          <Row label="producer (declared)">{mission.producerDeclared}</Row>
          <Row label="lifecycle">
            {mission.lifecycleModel} · {mission.standing} / {mission.lifecycleState}
          </Row>
          <Row label="binding">
            {mission.bindingState} ({mission.bindingReason})
          </Row>
          <Row label="live">
            {mission.live ? 'yes' : `NOT LIVE: ${mission.notLiveReason ?? 'reason not given'}`}
          </Row>
          <Row label="team roster">
            {mission.members.state}
            {mission.members.reason ? ` (${mission.members.reason})` : ''}
          </Row>
          <Row label="status derived from">{status.sources.join(' · ')}</Row>
          {mission.members.members.map((m) => (
            <Row key={m.memberRef} label={m.logical}>
              <span className="di-op-ref">{m.memberRef}</span> · {m.classification} ({m.reason}) ·{' '}
              {m.live ? 'drawn' : `NOT LIVE: ${m.notLiveReason ?? 'reason not given'}`} · session
              recorded {formatObserved(m.sessionRecorded)} · session live{' '}
              {formatObserved(m.sessionLive)}
            </Row>
          ))}
        </div>
      </details>
    </>
  );
}

/**
 * Code review not offered to ANY member of a Mission, for ONE reason (e.g. the office's older query
 * contract): that reason, said ONCE for the team in its details; undefined when members differ or a
 * review is offered. Never implies a review is available.
 */
function sharedReviewUnavailable(mission: DiMission): string | null | undefined {
  const offers = mission.members.members.map((m) => m.codeReview);
  return offers.length > 1 &&
    offers.every((o) => o !== undefined && o.state !== 'offered') &&
    new Set(offers.map((o) => o?.reason ?? null)).size === 1
    ? (offers[0]?.reason ?? null)
    : undefined;
}

/** The office data rows: the two freshness verdicts and the coverage statement. */
function OfficeDataRows({ state }: { state: DiOfficeState }) {
  const { envelope, runtime, coverage } = state;
  const waiting = backendNormalWait(state);
  return (
    <>
      <Row label="envelope">
        {!envelope.fresh && waiting ? (
          <span className={WAITING_CLASS}>
            {formatWaiting(waiting, envelope.retryAfterSeconds)}
          </span>
        ) : (
          <span className={freshnessClass(envelope.fresh)}>
            {envelope.fresh ? 'fresh' : `not fresh: ${envelope.reason ?? 'reason not given'}`}
          </span>
        )}{' '}
        (served {formatMaybe(envelope.served)})
      </Row>
      <Row label="runtime">
        {/* With no response (e.g. throttled) the runtime is not fresh for the SAME normal reason. */}
        <span className={!runtime.fresh && waiting ? WAITING_CLASS : freshnessClass(runtime.fresh)}>
          {runtime.fresh ? 'fresh' : `not fresh: ${runtime.reason ?? 'reason not given'}`}
        </span>
      </Row>
      <Row label="coverage">{coverage.statement}</Row>
    </>
  );
}

/** The freshness detail and its distinct times. */
function FreshnessDetailRows({ state }: { state: DiOfficeState }) {
  const { envelope, runtime, times } = state;
  const waiting = backendNormalWait(state);
  return (
    <>
      <Row label="serve-time verdict">
        {formatMaybe(envelope.verdictHealth)}
        {envelope.verdictProblem ? ` (${envelope.verdictProblem})` : ''}
      </Row>
      {envelope.envelopeProblem && (
        <Row label="catalog answer">
          <span className={waiting ? WAITING_CLASS : 'text-warning'}>
            {envelope.envelopeProblem}
            {envelope.envelopeDetail ? `: ${envelope.envelopeDetail}` : ''}
            {envelope.retryAfterSeconds === null
              ? ''
              : ` (retry after ${envelope.retryAfterSeconds} s)`}
          </span>
        </Row>
      )}
      <Row label="registry health">{formatMaybe(envelope.registryHealth)}</Row>
      <Row label="capture">
        configured {formatMaybe(runtime.configured)}, available {formatMaybe(runtime.available)}
        {runtime.captureProblem ? ` (${runtime.captureProblem})` : ''}
      </Row>
      <Row label="snapshot">
        {formatMaybe(runtime.snapshotHealth)}
        {runtime.snapshotProblem ? ` (${runtime.snapshotProblem})` : ''}
      </Row>
      <Row label="requested_at">{formatEpoch(times.requestedAt)}</Row>
      <Row label="generated_at">{formatEpoch(times.generatedAt)}</Row>
      <Row label="valid_until">{formatEpoch(times.validUntil)}</Row>
      <Row label="snapshot observed_at">{formatEpoch(times.snapshotObservedAt)}</Row>
      <Row label="evaluated (office clock)">{formatEpoch(times.evaluatedAt)}</Row>
    </>
  );
}

/** What the inspector opens on: one Mission (from its card), or the current selection (the dock). */
/** A section the inspector opens AT (scrolled into view and focused). */
type InspectorSection = 'evidence';
/** What the inspector opens on: one Mission (from its card), or the current selection (the dock). */
type InspectorTarget =
  { kind: 'mission'; missionRef: string; section?: InspectorSection } | { kind: 'selection' };

/**
 * feature13 — the full-size INSPECTOR. A real modal dialog (portal to <body>) with the code review
 * dialog's mechanics (diModal.ts): #root inert, focus in on open and returned to the control that
 * opened it, Tab trapped with wrap, Escape closes, and the focusout / capture-phase key guards so no
 * key reaches the office. Every scroll is inside it. Read-only: closing it controls nothing, and it
 * changes no Mission execution. The world stays behind it, unchanged.
 */
function OfficeInspector({
  target,
  state,
  keyed,
  snapshotFreshness,
  runtimeFreshness,
  evidence,
  review,
  onShow,
  onClose,
  returnFocusTo,
}: {
  target: InspectorTarget;
  state: DiOfficeState;
  keyed: DiRecord | null;
  snapshotFreshness: DiFreshness;
  runtimeFreshness: DiFreshness;
  evidence: DiEvidenceAccess | null;
  review: DiCodeReviewAccess | null;
  onShow: (id: number) => void;
  onClose: () => void;
  returnFocusTo: { current: HTMLElement | null };
}) {
  const dialogRef = useRef<HTMLDivElement>(null);
  const onKeyDown = useDiModal(dialogRef, onClose, returnFocusTo);
  // Opened AT a section (the card's "Evidence library"): that section is scrolled into view and
  // takes focus, after the dialog itself took it on open.
  const section = target.kind === 'mission' ? target.section : undefined;
  useEffect(() => {
    if (section !== 'evidence') return;
    const el = dialogRef.current?.querySelector<HTMLElement>('#di-inspector-evidence');
    el?.scrollIntoView({ block: 'start' });
    el?.focus({ preventScroll: true });
  }, [section]);
  const mission =
    target.kind === 'mission'
      ? (state.missions.missions.find((m) => m.missionRef === target.missionRef) ?? null)
      : keyed?.target.kind === 'member'
        ? keyed.target.mission
        : null;
  const status = mission ? missionStatus(mission, state) : null;
  // The code review opens its own large dialog: this one closes first (never two modals at once),
  // and focus returns to the control that opened the inspector.
  const reviewHere: DiCodeReviewAccess | null = review && {
    open: (reviewTarget, opener) => {
      const back = returnFocusTo.current ?? opener;
      onClose();
      review.open(reviewTarget, back);
    },
  };
  const record = target.kind === 'selection' ? keyed : null;
  // A member's summary shows its code review entry when offered, or not offered for a reason of its
  // OWN (a reason the whole team shares is said once, in the Team section).
  const summaryShowsReview =
    record?.target.kind === 'member' &&
    (record.target.member.codeReview?.state === 'offered' ||
      sharedReviewUnavailable(record.target.mission) === undefined);
  const reviewStatedFor =
    summaryShowsReview &&
    record?.target.kind === 'member' &&
    statesReviewNotOffered(record.target.member)
      ? record.target.member.memberRef
      : undefined;
  const heading =
    record?.target.kind === 'member'
      ? firstLayerName(state, {
          kind: 'member',
          mission: record.target.mission,
          member: record.target.member,
        })
      : record?.target.kind === 'runtime' || record?.target.kind === 'unverified'
        ? firstLayerName(state, { kind: 'runtime', agent: record.target.agent })
        : mission
          ? missionTitle(mission).kind === 'no-goal'
            ? missionLabel(mission)
            : missionTitle(mission).text
          : 'This record is no longer in the office state';
  /** A Mission's short source-qualified ref: lookalikes never share a heading. */
  const missionShortRef =
    record === null && mission
      ? (shortMissionRefs(state.missions.missions).get(mission.missionRef) ?? mission.missionRef)
      : null;
  // The complete source-qualified ref, for the details. Lookalikes are told apart on the FIRST layer
  // by the heading itself (firstLayerName: herd and source, or chain, wherever a name collides), the
  // agent's own name under it, or a Mission's short ref under its title.
  const qualifier =
    record?.target.kind === 'member'
      ? record.target.member.memberRef
      : record?.target.kind === 'runtime' || record?.target.kind === 'unverified'
        ? record.target.agent.agentRef
        : (mission?.missionRef ?? null);
  const kindLabel =
    record?.target.kind === 'member'
      ? 'Team member'
      : record?.target.kind === 'runtime'
        ? 'Agent, not a team member'
        : record?.target.kind === 'unverified'
          ? 'Agent that can’t be linked to a role'
          : 'Mission';
  const agent =
    record?.target.kind === 'member'
      ? record.target.member
      : record?.target.kind === 'runtime' || record?.target.kind === 'unverified'
        ? record.target.agent
        : null;
  const now = state.times.evaluatedAt;
  /** Everything recorded about the selected agent, unshortened, one deliberate step away. */
  const recordDetails = (children: React.ReactNode) => (
    <details className="di-op-record" data-testid="di-inspector-record">
      <summary className={DI_FOCUS_RING}>
        Everything recorded about this agent: identity, exact times and codes
      </summary>
      <div className="di-op-selected-body">
        <div className="di-op-ref di-op-muted" data-testid="di-inspector-qualifier">
          {qualifier}
        </div>
        {children}
      </div>
    </details>
  );
  return createPortal(
    <div
      className="di-op-theme di-op-inspector-backdrop"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
    >
      <div
        ref={dialogRef}
        className="di-op-inspector"
        role="dialog"
        aria-modal="true"
        aria-labelledby="di-inspector-title"
        tabIndex={-1}
        onKeyDown={onKeyDown}
        data-testid="di-inspector"
      >
        <header className="di-op-inspector-head">
          <div className="di-op-inspector-heading">
            <div className="di-op-muted" data-testid="di-inspector-kind">
              {kindLabel} · details (view only)
              {state.fixtureMode ? ' · FIXTURE DATA' : ''}
            </div>
            {/* Who or what this is, once: a short name (the agent and its role or herd, or the
                Mission's title); the complete source-qualified ref is in the details below. */}
            <h2
              id="di-inspector-title"
              className="di-op-inspector-title di-op-clamp-2"
              title={heading}
            >
              {heading}
            </h2>
            {(record?.target.kind === 'runtime' || record?.target.kind === 'unverified') && (
              <div className="di-op-ref di-op-muted" data-testid="di-inspector-agent-name">
                {record.target.agent.agent}
              </div>
            )}
            {missionShortRef !== null && (
              <div
                className="di-op-ref di-op-muted"
                data-testid="di-inspector-short-ref"
                title={mission?.missionRef}
              >
                {missionShortRef}
              </div>
            )}
            <div className="di-op-card-meta">
              {agent ? (
                <AgentPhraseView phrase={agentPhrase(agent)} />
              ) : (
                status && <StatusBadge result={status} withSentence={false} />
              )}
            </div>
          </div>
          <button
            type="button"
            className={`di-op-button ${DI_FOCUS_RING}`}
            data-testid="di-inspector-close"
            onClick={onClose}
          >
            Close
          </button>
        </header>
        <div className="di-op-inspector-body">
          {record?.target.kind === 'member' && (
            // The first layer: what it is doing, its Mission, the obvious actions; then the record.
            <section className="di-op-inspector-section" data-testid="di-inspector-selected">
              <p className="di-op-now" data-testid="di-inspector-now">
                {agentNow(record.target.member)}
              </p>
              <div className="di-op-card-meta">
                <span
                  data-testid="di-inspector-member-mission"
                  title={record.target.mission.missionRef}
                >
                  Team member of “{missionTitle(record.target.mission).text}”{' '}
                  <span className="di-op-ref di-op-muted">
                    {shortMissionRefs(state.missions.missions).get(
                      record.target.mission.missionRef,
                    ) ?? record.target.mission.missionRef}
                  </span>
                </span>
                {status && <StatusBadge result={status} withSentence={false} />}
              </div>
              <PlainFreshness
                freshness={snapshotFreshness}
                observedAt={state.times.snapshotObservedAt}
                now={now}
              />
              <div className="di-op-actions">
                <OnTheMap
                  characterId={record.target.member.characterId}
                  live={record.target.member.live}
                  notLiveReason={record.target.member.notLiveReason}
                  onShow={onShow}
                />
                {/* Offered, or not offered for a reason of ITS OWN: said here. A reason the whole team
                    shares is said once, in the Team section below. */}
                {summaryShowsReview && (
                  <DiCodeReviewEntry
                    member={record.target.member}
                    missionRef={record.target.mission.missionRef}
                    access={reviewHere}
                  />
                )}
              </div>
              {recordDetails(
                <MemberDetail
                  member={record.target.member}
                  mission={record.target.mission}
                  freshness={snapshotFreshness}
                  review={null}
                />,
              )}
            </section>
          )}
          {record?.target.kind === 'runtime' && (
            <section className="di-op-inspector-section" data-testid="di-inspector-selected">
              <p className="di-op-now" data-testid="di-inspector-now">
                {agentNow(record.target.agent)}
              </p>
              <div>{runtimeAgentMeaning(record.target.agent)}</div>
              <PlainFreshness
                freshness={runtimeFreshness}
                observedAt={state.runtimeAgents?.observedAt ?? null}
                now={now}
              />
              <div className="di-op-actions">
                <OnTheMap
                  characterId={record.target.agent.characterId}
                  live={record.target.agent.live}
                  notLiveReason={record.target.agent.notLiveReason}
                  onShow={onShow}
                />
              </div>
              {recordDetails(
                <SelectedRuntimeAgent
                  agent={record.target.agent}
                  freshness={runtimeFreshness}
                  testid="di-inspector-runtime-agent"
                />,
              )}
            </section>
          )}
          {record?.target.kind === 'unverified' && (
            <section className="di-op-inspector-section" data-testid="di-inspector-selected">
              <p className="di-op-now" data-testid="di-inspector-now">
                {agentNow(record.target.agent)}
              </p>
              <div>
                Why: {plainIdentityReason(record.target.agent.identityReason)}. Not drawn; not a
                team member.
              </div>
              <PlainFreshness
                freshness={runtimeFreshness}
                observedAt={state.runtimeAgents?.observedAt ?? null}
                now={now}
              />
              {recordDetails(
                <SelectedUnverified
                  agent={record.target.agent}
                  freshness={runtimeFreshness}
                  testid="di-inspector-unverified"
                />,
              )}
            </section>
          )}
          {target.kind === 'selection' && record === null && (
            <section className="di-op-inspector-section">
              <div className="text-warning">
                The selected record is no longer in the office state. Nothing else was selected in
                its place.
              </div>
            </section>
          )}
          {mission && status && (
            <MissionInspectorSections
              mission={mission}
              status={status}
              evidence={evidence}
              review={reviewHere}
              onShow={onShow}
              reviewStatedFor={reviewStatedFor}
            />
          )}
          <details className="di-op-inspector-section" data-testid="di-inspector-freshness">
            <summary className={DI_FOCUS_RING}>
              Exact times and freshness ·{' '}
              {plainObserved(snapshotFreshness, state.times.snapshotObservedAt, now)}
            </summary>
            <div className="di-op-detail-body di-op-detail-open">
              <OfficeDataRows state={state} />
              <FreshnessDetailRows state={state} />
            </div>
          </details>
          <details className="di-op-inspector-section" data-testid="di-inspector-limitations">
            <summary className={DI_FOCUS_RING}>
              What this office does not report (model, usage, cost, progress, ETA and more)
            </summary>
            <div className="di-op-detail-body di-op-detail-open">
              <Row label="coverage">{state.coverage.statement}</Row>
              <Row label="linked child-task roles">
                {formatLinkedChildCoverage(state.coverage).text}
              </Row>
              <Row label="not reported by any supported source">
                model requested / configured, model confirmed running, usage, cost, progress and
                ETA: {RUNTIME_UNREPORTED} for every agent here
              </Row>
              <Row label="query contract">
                version {state.contractVersion}
                {state.contractVersion < 2
                  ? ': task chains, the children record, the Mission State (completion, verification) and canonical review are contract version 2 blocks, so they are not reported here.'
                  : ''}
              </Row>
              <Row label="runtime agents">{formatRuntimeAgentsStatement(state.runtimeAgents)}</Row>
              <Row label="this view">
                Read-only. Nothing here approves, starts, stops, retries or delivers anything.
              </Row>
            </div>
          </details>
        </div>
      </div>
    </div>,
    document.body,
  );
}

function FactLine({ fact }: { fact: DiReportFact }) {
  return (
    <div className="pl-8 break-all" data-testid="di-report-fact">
      {fact.name}: {formatFactState(fact)}
      {fact.standing ? ` · ${fact.standing}` : ''}
      {/* A null freshness is not a definite one: said explicitly. */}
      {` · freshness ${fact.freshness ?? 'not recorded'}`}
      {fact.source ? ` · source ${fact.source}` : ''}
      {fact.entries.map((e, i) => (
        <div key={i} className="pl-8 text-text-muted">
          {e.label}: {e.value ?? 'not recorded'}
        </div>
      ))}
    </div>
  );
}

/** contract v2 blocks of one Mission: chains, children record, Mission State, review. */
function MissionV2Blocks({ v2 }: { v2: DiMissionV2 }) {
  const review = v2.canonicalReview;
  const preamble = formatReviewPreamble(review);
  const c = v2.missionState.completion;
  const p = v2.missionState.provenance;
  const yn = (v: boolean | null) => (v === null ? 'not recorded' : v ? 'yes' : 'no');
  return (
    <div className="flex flex-col gap-1 pl-8" data-testid="di-mission-v2">
      <details>
        <summary className="cursor-pointer text-text-muted">
          Task chains ({v2.taskChains.length})
        </summary>
        {v2.taskChains.map((chain, i) => (
          <div key={i} className="pl-8 break-all" data-testid="di-task-chain">
            {formatTaskChain(chain)}
            {chain.declaredAgents.length > 0 && (
              <div className="pl-8 text-text-muted">
                declared: {chain.declaredAgents.map((a) => `${a.logical} (${a.agent})`).join(', ')}
              </div>
            )}
          </div>
        ))}
      </details>
      <div className="break-all" data-testid="di-children-record">
        children record: {v2.childrenRecord.state ?? 'not recorded'}
        {v2.childrenRecord.problem ? ` (${v2.childrenRecord.problem})` : ''}
        {v2.childrenRecord.caveat ? ` · caveat: ${v2.childrenRecord.caveat}` : ''}
        {v2.childrenRecord.recordCount !== null
          ? ` · ${v2.childrenRecord.recordCount} record(s)`
          : ''}
        {v2.childrenRecord.otherParentCount !== null
          ? ` · ${v2.childrenRecord.otherParentCount} for other parents`
          : ''}
      </div>
      <details data-testid="di-mission-state">
        <summary className="cursor-pointer text-text-muted">
          Mission State: {v2.missionState.state ?? 'not recorded'}
          {v2.missionState.problem ? ` (${v2.missionState.problem})` : ''}
        </summary>
        {v2.missionState.kinds.map((k) => (
          <div key={k.kind} className="pl-8 text-text-muted break-all">
            input {k.kind}: {k.supplied ?? 'not recorded'}
            {k.reason ? ` (${k.reason})` : ''}
          </div>
        ))}
        {v2.missionState.facts.map((fact) => (
          <FactLine key={fact.name} fact={fact} />
        ))}
        <div className="pl-8 break-all" data-testid="di-completion">
          completion:{' '}
          {c.state === 'observed' ? 'observed' : `${c.state}${c.reason ? ` (${c.reason})` : ''}`}
          {c.state === 'observed' && (
            <div className="pl-8 text-text-muted">
              {/* reported_* and verified_* are DIFFERENT facts: never merged. */}
              <div>reported complete: {yn(c.reportedComplete)}</div>
              <div>reported success: {yn(c.reportedSuccess)}</div>
              <div>verified success: {yn(c.verifiedSuccess)}</div>
              <div>closure verified: {yn(c.closureVerified)}</div>
              <div>contradicted: {yn(c.contradicted)}</div>
              <div>holds: {formatBoundedList(c.holds, c.holdsTotal)}</div>
              <div>
                limits: identity {c.limitsIdentity ?? 'not recorded'} · separation of duties{' '}
                {c.limitsSeparationOfDuties ?? 'not recorded'} · non-verifying evidence{' '}
                {formatBoundedList(
                  c.limitsNonVerifyingEvidenceKinds,
                  c.limitsNonVerifyingEvidenceKindsTotal,
                )}
              </div>
            </div>
          )}
        </div>
        <div className="pl-8 break-all text-text-muted" data-testid="di-provenance">
          provenance:{' '}
          {p.state === 'observed'
            ? `${p.recordSource ?? 'source not recorded'} · collection ${p.collectionStatus ?? 'not recorded'} · position ${p.position ?? 'not recorded'} (head ${p.headPosition ?? 'not recorded'}, cursor ${p.cursorPosition ?? 'not recorded'}) · drift ${formatBoundedList(p.drift, p.driftTotal)}`
            : `${p.state}${p.reason ? ` (${p.reason})` : ''}`}
        </div>
      </details>
      <section className="flex flex-col gap-1 break-all" data-testid="di-review-preamble">
        <div>Canonical review: READ-ONLY preamble context</div>
        {preamble.admitted ? (
          <div className="pl-8 flex flex-col gap-1">
            <div>
              round {review.round ?? 'not recorded'} · reviewer{' '}
              {review.reviewerLogical ?? 'not recorded'} ({review.reviewerAgent ?? 'not recorded'})
              · protocol token {review.protocolToken ?? 'not recorded'}
            </div>
            <div>gate: {preamble.gate}</div>
            <div>verdict: {preamble.verdict}</div>
            {preamble.parseOutcome && <div className="text-warning">{preamble.parseOutcome}</div>}
            <div className="text-text-muted">
              integrity: linkage-corroborated, NOT digest-anchored · candidate applicability:
              UNPROVEN ({review.candidateApplicability ?? 'unproven'}) · currency{' '}
              {review.currency ?? 'not recorded'} · preamble only, no transcript content
            </div>
          </div>
        ) : (
          <div className="pl-8 text-text-muted">
            {review.state ?? 'not recorded'}
            {review.problem ? ` (${review.problem})` : ''}
          </div>
        )}
      </section>
    </div>
  );
}

/**
 * DI office panel: a sidebar docked to the right edge. While it is open the office canvas
 * narrows by its width (App.tsx), so the office re-centres beside it and stays clickable;
 * collapsed, it is one small button and the office takes the full width.
 * Top to bottom, without scrolling: overall status, the two freshness verdicts with
 * reasons, the coverage label, the SELECTED agent (one click on a character) with its
 * Mission's identity. Below: a bounded, independently scrolling Missions list, then the
 * full freshness detail (collapsed). Everything is rendered as projected; nothing derived.
 */
export function DiOfficePanel(props: DiOfficePanelProps) {
  return (
    <FixtureMode.Provider value={props.state.fixtureMode === true}>
      <DiOfficePanelBody {...props} />
    </FixtureMode.Provider>
  );
}

/** What a selection keeps of its record: enough to say truthfully that it has gone. */
function remember(record: DiRecord | null): DiSelectionMemory | null {
  return record === null
    ? null
    : {
        key: record.key,
        label: record.title,
        kind: record.target.kind,
        missionRef: record.missionRef,
      };
}

/**
 * Round 11 (F2): the selected record is no longer in the office state. Said plainly — what was
 * selected, that it is gone, why when the state says (its canonical reason in the disclosure) and
 * how current the office state is — and kept until the user selects or clears something.
 */
function SelectionGone({
  memory,
  state,
  freshness,
  onClear,
}: {
  memory: DiSelectionMemory;
  state: DiOfficeState;
  freshness: DiFreshness;
  onClear: () => void;
}) {
  const why = missingRecordReason(state, memory);
  return (
    <div className="di-op-selected-body" data-testid="di-selection-gone">
      <div className="text-warning">
        The selected record ({memory.label}) is no longer in the office state. Nothing else was
        selected in its place.
      </div>
      <div data-testid="di-selection-gone-why">Why: {why.plain}.</div>
      <PlainFreshness
        freshness={freshness}
        observedAt={
          memory.kind === 'member' || memory.kind === 'mission'
            ? state.times.snapshotObservedAt
            : (state.runtimeAgents?.observedAt ?? null)
        }
        now={state.times.evaluatedAt}
      />
      <details>
        <summary className={DI_FOCUS_RING}>Canonical reason and the last selection (raw)</summary>
        <div className="di-op-detail-body">
          <Row label="canonical reason">{why.canonical ?? 'none recorded by the office state'}</Row>
          <Row label="observation (exact)">{freshness.statement}</Row>
          <Row label="office not-live reason">{freshness.reason ?? 'none (live)'}</Row>
          <Row label="selected record">{memory.key}</Row>
          <Row label="kind">{memory.kind}</Row>
          {memory.missionRef !== null && <Row label="its Mission">{memory.missionRef}</Row>}
        </div>
      </details>
      <button type="button" className={`di-op-link ${DI_FOCUS_RING}`} onClick={onClear}>
        Clear the selection
      </button>
    </div>
  );
}

const KIND_LABEL: Record<DiRecordTarget['kind'], string> = {
  mission: 'Mission',
  member: 'Team member',
  runtime: 'Runtime agent (not a team member)',
  unverified: 'Not verified (not drawn)',
};

/**
 * item 08: find Missions and agents by objective, identifier, role or identity fields. Every match
 * is its own source-qualified record (never merged); no match and several matches are said
 * plainly. Selecting a result is view only.
 */
function FindSection({
  state,
  query,
  onQuery,
  onSelect,
}: {
  state: DiOfficeState;
  query: string;
  onQuery: (q: string) => void;
  onSelect: (record: DiRecord) => void;
}) {
  const rows = useRovingFocus<HTMLButtonElement>();
  const outcome = findRecords(state, query);
  // feature13 — progressive disclosure under vertical pressure. Choosing a result keeps the query and the
  // statement of what matched, but folds the LIST into a one-line toggle: a window shared with the request
  // entry and the Selected dock cannot also hold up to 220px of results without crushing the Missions.
  // Typing again, the toggle, or Arrow Down from the box brings the results back; the rows stay
  // keyboard-reachable (roving focus) and bounded (they scroll, and give way before the panel overflows).
  const [shown, setShown] = useState(true);
  const toggleRef = useRef<HTMLButtonElement>(null);
  const focusFirstOnOpen = useRef(false);
  useEffect(() => {
    if (shown && focusFirstOnOpen.current) {
      focusFirstOnOpen.current = false;
      rows.focusFirst();
    }
  });
  const hasResults = outcome !== null && outcome.results.length > 0;
  return (
    <section className="di-op-find" data-testid="di-find">
      <label htmlFor="di-find-input" className="sr-only">
        Find a Mission or agent
      </label>
      <input
        id="di-find-input"
        type="search"
        autoComplete="off"
        spellCheck={false}
        placeholder="Find a Mission or agent: objective, name, role, provider, id"
        className={DI_FOCUS_RING}
        data-testid="di-find-input"
        aria-describedby="di-find-outcome di-find-hint"
        aria-controls="di-find-results"
        aria-keyshortcuts="ArrowDown Escape"
        value={query}
        onChange={(e) => {
          setShown(true);
          onQuery(e.target.value);
        }}
        onKeyDown={(e) => {
          if (e.key === 'ArrowDown' && hasResults) {
            e.preventDefault();
            if (shown) rows.focusFirst();
            else {
              focusFirstOnOpen.current = true;
              setShown(true);
            }
          } else if (e.key === 'Escape') {
            setShown(true);
            onQuery('');
          }
        }}
      />
      <span id="di-find-hint" className="sr-only">
        Searches this office state. Arrow Down moves into the results; selecting one is view only.
      </span>
      <div className="di-op-find-head">
        <div
          id="di-find-outcome"
          data-testid="di-find-outcome"
          role="status"
          className="di-op-find-outcome"
        >
          {outcome ? outcome.statement : ''}
        </div>
        {outcome && hasResults && (
          <button
            type="button"
            ref={toggleRef}
            className={`di-op-link ${DI_FOCUS_RING}`}
            data-testid="di-find-toggle"
            aria-expanded={shown}
            aria-controls={shown ? 'di-find-results' : undefined}
            onClick={() => setShown((s) => !s)}
          >
            {shown
              ? 'Hide results'
              : `Show ${outcome.results.length} ${outcome.results.length === 1 ? 'result' : 'results'}`}
          </button>
        )}
      </div>
      {outcome && hasResults && shown && (
        <ul
          id="di-find-results"
          className="di-op-find-results"
          data-testid="di-find-results"
          aria-label="Matching records (arrow keys move between them)"
        >
          {outcome.results.map((r, i) => (
            <ListRow
              key={r.key}
              testid="di-find-result"
              selected={false}
              onSelect={() => {
                onSelect(r);
                // The list folds away: focus moves to the toggle first, never to nowhere.
                toggleRef.current?.focus();
                setShown(false);
              }}
              refAt={rows.refAt(i)}
              onKeyDown={rows.onKeyDown(i, outcome.results.length)}
              title="Select: view only"
            >
              {KIND_LABEL[r.target.kind]}: {r.title} <span className="di-op-sub">· {r.detail}</span>
            </ListRow>
          ))}
        </ul>
      )}
    </section>
  );
}

function DiOfficePanelBody({
  state,
  officeState,
  collapsed,
  onCollapsedChange,
  widthPx,
  fixtureNote = false,
  announced = [],
  evidence = null,
  review = null,
  mission = null,
  receptionInbox = null,
}: DiOfficePanelProps) {
  const [selectedId, setSelectedId] = useState<number | null>(officeState.selectedAgentId);
  useEffect(() => {
    const timer = setInterval(
      () => setSelectedId(officeState.selectedAgentId),
      DI_SELECTION_POLL_MS,
    );
    return () => clearInterval(timer);
  }, [officeState]);

  /**
   * C1 / round 11: the selection is held by the record's OWN complete ref (never its character
   * number) together with what it was, so a later office state that reorders records keeps it,
   * one that takes it off the map keeps it (qualified), and one that drops it keeps a truthful
   * missing-record notice — nothing else is ever selected in its place. Only the user replaces
   * or clears it: a canvas click, a row, or "Clear the selection".
   */
  const [memory, setMemory] = useState<DiSelectionMemory | null>(() =>
    remember(recordForCharacter(state, officeState.selectedAgentId)),
  );
  const selectedKey = memory?.key ?? null;
  const seenId = useRef<number | null>(officeState.selectedAgentId);
  useEffect(() => {
    // A selection made on the office canvas (or by the office itself) re-keys the panel.
    if (selectedId === seenId.current) return;
    const previous = seenId.current;
    seenId.current = selectedId;
    if (selectedId !== null) {
      setMemory(remember(recordForCharacter(state, selectedId)));
      return;
    }
    // The office selection went null. Canvas reconciliation does that when it REMOVES the
    // selected character (removeAgent: gone from the canvas, or despawning) — then the selection
    // is kept: inspected and qualified if still listed, the missing-record notice if not. Only a
    // character still standing on the canvas was deliberately deselected by the user.
    const character = previous === null ? undefined : officeState.characters.get(previous);
    const removed = !character || character.matrixEffect === 'despawn';
    if (!removed) setMemory(null);
  }, [selectedId, state, officeState]);

  /** The Mission picker (one Mission, or all) and the live-only filter: inspector only. */
  const [focusMission, setFocusMission] = useState<string | null>(null);
  const [liveOnly, setLiveOnly] = useState(false);
  const [query, setQuery] = useState('');
  /**
   * Reception entries hidden in THIS view only: React state, never persisted and never sent, so
   * a reload shows every entry again and a hide resolves or approves nothing.
   */
  const [hiddenReception, setHiddenReception] = useState<ReadonlySet<string>>(() => new Set());
  const missionsRef = useRef<HTMLElement>(null);
  /** feature13: the full-size inspector — what it is open on, and the control that opened it. */
  const [inspector, setInspector] = useState<InspectorTarget | null>(null);
  const inspectorOpener = useRef<HTMLElement | null>(null);
  /**
   * A locate ("Show in the office") from inside the inspector closes it so the located agent and the
   * world are actually SEEN; focus then lands, predictably, on the Selected dock's "Show details"
   * for that same record (the dialog's own return-focus runs first and is superseded here).
   */
  const focusDockAfterLocate = useRef(false);
  useEffect(() => {
    if (inspector !== null || !focusDockAfterLocate.current) return;
    focusDockAfterLocate.current = false;
    const dock = document.querySelector<HTMLElement>(
      '[data-testid="di-selected-agent"] [data-testid="di-open-inspector-selection"]',
    );
    dock?.focus();
  });
  /**
   * "Show this Mission" from the reception inbox closes the inbox first (the world must be SEEN, and
   * the modal keeps #root inert until it unmounts), then does the same view-only jump the inline list
   * always did. The dialog's own return-focus runs first; the jump then lands focus on the Missions.
   */
  const afterReceptionClose = useRef<(() => void) | null>(null);
  const receptionOpen = receptionInbox?.open === true;
  useEffect(() => {
    if (receptionOpen || afterReceptionClose.current === null) return;
    const run = afterReceptionClose.current;
    afterReceptionClose.current = null;
    run();
  });
  /**
   * A destination the inbox sends the person to (a Mission in the list, a Mission's details) lives in
   * the EXPANDED sidebar. With the sidebar collapsed it is expanded first, and the action runs once the
   * panel is drawn (its Missions list and inspector exist only then), so the jump always lands on
   * something visible — also for a Mission with no drawn agent, which has no camera destination.
   */
  const afterExpand = useRef<(() => void) | null>(null);
  useEffect(() => {
    if (collapsed || afterExpand.current === null) return;
    const run = afterExpand.current;
    afterExpand.current = null;
    run();
  });
  const whenExpanded = (action: () => void) => {
    if (!collapsed) {
      action();
      return;
    }
    afterExpand.current = action;
    onCollapsedChange(false);
  };
  /**
   * AC3: minimize KEEPS the panel. It stays mounted and is only hidden, so its selection, filters, typed text
   * and open disclosures are kept as they were; what hiding loses (each region's scroll) is taken when it
   * minimizes and given back when it is restored. Focus moves to the control that restores the panel, and on
   * restore comes back to the last panel control that had it (never the toggles; diMinimize.ts), falling back to
   * Minimize; a deferred action, above, decides for itself.
   */
  const asideRef = useRef<HTMLElement>(null);
  // the sticky fixture note covers the top of the panel's scrollport: its height is reserved as scroll-padding so a focused control is never scrolled under it (diStickyPad.ts)
  const noteRef = useRef<HTMLDivElement>(null);
  useStickyScrollPad(noteRef, asideRef, fixtureNote);
  const restoreRef = useRef<HTMLButtonElement>(null);
  const minimizeRef = useRef<HTMLButtonElement>(null);
  const keptView = useRef<DiKeptView | null>(null);
  const lastContentFocus = useLastContentFocus(asideRef, minimizeRef);
  const minimizePanel = () => {
    if (asideRef.current) keptView.current = keepView(asideRef.current, lastContentFocus.current);
    onCollapsedChange(true);
  };
  const shownCollapsed = useRef(collapsed);
  useLayoutEffect(() => {
    if (shownCollapsed.current === collapsed) return;
    shownCollapsed.current = collapsed;
    if (collapsed) {
      const active = document.activeElement;
      if (active === null || active === document.body || asideRef.current?.contains(active))
        focusExposed(restoreRef.current);
      return;
    }
    const kept = keptView.current;
    keptView.current = null;
    restoreScroll(kept);
    if (afterExpand.current === null) restoreFocus(kept, minimizeRef.current, asideRef.current);
  });
  /** VIEW-ONLY navigation: moves the office selection and camera, nothing else. */
  const navigateTo = (id: number | null) => {
    if (id === null || !officeState.characters.has(id)) return;
    officeState.selectedAgentId = id;
    officeState.cameraFollowId = id;
    seenId.current = id;
    setSelectedId(id);
    setMemory(remember(recordForCharacter(state, id)));
  };
  /** VIEW-ONLY: nothing is selected, as the person's own deselection would leave it (canvas, camera, panel). */
  const clearSelection = () => {
    officeState.selectedAgentId = null;
    officeState.cameraFollowId = null;
    seenId.current = null;
    setSelectedId(null);
    setMemory(null);
  };
  const { envelope, times, coverage, missions } = state;
  /** VIEW-ONLY: show one Mission in the list (and nothing else). */
  const showMission = (missionRef: string) => {
    setLiveOnly(false);
    setFocusMission(missionRef);
    missionsRef.current?.focus();
  };
  /** VIEW-ONLY: show one reception entry's Mission in the list and select its first drawn agent. */
  const jumpToMission = (missionRef: string) => {
    showMission(missionRef);
    const picked = missions.missions.find((m) => m.missionRef === missionRef);
    const id = picked?.members.members.find((m) => m.characterId !== null)?.characterId ?? null;
    if (id !== null && officeState.characters.has(id)) navigateTo(id);
    // A Mission with nothing drawn has no agent to select and no camera destination. Showing it must
    // not leave the PREVIOUS agent selected beside it (the dock and the details would then speak of
    // the wrong Mission): the selection is replaced by "nothing", and the requested Mission, alone in
    // the list, is the destination. (navigateTo itself still ignores null for every other caller.)
    else clearSelection();
  };
  /** VIEW-ONLY: select a record — on the map when it is drawn, in the inspector always. */
  const selectRecord = (record: DiRecord) => {
    if (record.target.kind === 'mission') {
      jumpToMission(record.target.mission.missionRef);
      return;
    }
    if (record.characterId !== null && officeState.characters.has(record.characterId)) {
      navigateTo(record.characterId);
      return;
    }
    // Not drawn (unverified, or not live now): inspected only; the canvas selects nothing.
    officeState.selectedAgentId = null;
    seenId.current = null;
    setSelectedId(null);
    setMemory(remember(record));
  };
  const selectKey = (key: string) => {
    const record = recordByKey(state, key);
    if (record) selectRecord(record);
  };
  const keyed = recordByKey(state, selectedKey);
  /** A normal operating state (e.g. catalog_refresh_throttled): neutral tone, never an error. */
  const waiting = backendNormalWait(state);
  /** Round 11: freshness from the PRESENTED state (server freshness + this view's connection). */
  const snapshotFreshness = officeFreshness(state, times.snapshotObservedAt);
  const runtimeFreshness = officeFreshness(state, state.runtimeAgents?.observedAt ?? null);

  /** What this window knows about the request panel (the inbox lists what it reports needs the person). */
  const panelSource = panelSourceOf(mission === null ? null : mission.state);
  /**
   * The inbox's routes into the request panel. The inbox closes first (its modal keeps #root inert),
   * then the panel opens on that one conversation or request and READS it; nothing is sent from here.
   */
  const panelActions =
    mission?.openConversation && mission.openRequest
      ? {
          openConversation: (conversationId: string) => {
            const opener = receptionInbox?.returnFocusTo.current ?? null;
            afterReceptionClose.current = () =>
              mission.openConversation?.(
                conversationId,
                opener?.isConnected === true
                  ? opener
                  : (document.querySelector<HTMLElement>('[data-testid="di-open-reception"]') ??
                      document.body),
              );
            receptionInbox?.close();
          },
          openRequest: (requestRef: string) => {
            const opener = receptionInbox?.returnFocusTo.current ?? null;
            afterReceptionClose.current = () =>
              mission.openRequest?.(
                requestRef,
                opener?.isConnected === true
                  ? opener
                  : (document.querySelector<HTMLElement>('[data-testid="di-open-reception"]') ??
                      document.body),
              );
            receptionInbox?.close();
          },
        }
      : undefined;

  /** The reception inbox: ONE dialog for both entrances, drawn even while the panel is collapsed. */
  const receptionDialog =
    receptionInbox?.open === true
      ? createPortal(
          <DiReceptionInbox
            state={state}
            panel={panelSource}
            panelActions={panelActions}
            filter={receptionInbox.filter}
            onFilterChange={receptionInbox.setFilter}
            lastKey={receptionInbox.lastKey}
            onFocusedKey={receptionInbox.setLastKey}
            hidden={hiddenReception}
            onHide={(key) => setHiddenReception((prev) => new Set(prev).add(key))}
            onShowHidden={() => setHiddenReception(new Set())}
            onJump={(missionRef) => {
              afterReceptionClose.current = () => whenExpanded(() => jumpToMission(missionRef));
              receptionInbox.close();
            }}
            // Details and evidence need the panel's inspector, which exists in the expanded sidebar:
            // a collapsed sidebar is expanded first (whenExpanded), never left to do nothing.
            missionActions={{
              hasMission: (ref) => missions.missions.some((m) => m.missionRef === ref),
              hasEvidence: (ref) =>
                missions.missions.some((m) => m.missionRef === ref && m.evidence),
              openDetails: (ref, section) => {
                const back = receptionInbox.returnFocusTo.current;
                afterReceptionClose.current = () =>
                  whenExpanded(() => {
                    inspectorOpener.current =
                      back?.isConnected === true
                        ? back
                        : document.querySelector<HTMLElement>('[data-testid="di-open-reception"]');
                    setInspector({ kind: 'mission', missionRef: ref, section });
                  });
                receptionInbox.close();
              },
            }}
            onClose={receptionInbox.close}
            returnFocusTo={receptionInbox.returnFocusTo}
            minimized={receptionInbox.minimized}
            onMinimize={receptionInbox.minimize}
            onRestore={receptionInbox.restore}
          />,
          document.body,
        )
      : null;

  const restoreControl = collapsed ? (
    <DiMinimizedChip>
      <button
        type="button"
        ref={restoreRef}
        className={`di-op-theme di-op-collapsed ${DI_FOCUS_RING}`}
        onClick={() => onCollapsedChange(false)}
        title="Restore the office panel as you left it"
        aria-label={`Restore office panel, ${state.live ? 'live' : 'not live'}${state.fixtureMode ? ', fixture data' : ''}`}
        data-testid="di-office-panel-collapsed"
        data-focus-at-hide={keptView.current?.focusAtHide}
        data-focus-return={keptView.current?.focusReturn}
      >
        <span aria-hidden="true">◂</span>{' '}
        <span className="di-op-collapsed-label">Restore office panel</span>{' '}
        <span className="di-op-collapsed-state">
          <LiveBadge live={state.live} reason={state.notLiveReason} />
        </span>
      </button>
    </DiMinimizedChip>
  ) : null;

  /** The ONE attention entry's words (null: this office provides no reception desk). */
  const inboxModel = buildInbox(
    state.reception,
    panelSource,
    state.missions?.missions ?? [],
    hiddenReception,
  );
  const attention = state.reception
    ? { count: inboxModel.attention, text: attentionLine(inboxModel.attention) }
    : null;
  /** Each Mission's short source-qualified ref, unique across the whole list. */
  const shortRefs = shortMissionRefs(missions.missions);
  const shown = filterMissions(missions.missions, focusMission, liveOnly);
  /** The office clock: ages are measured on it, never on this browser's. */
  const now = times.evaluatedAt;
  /** What this view may not show, said once (null: nothing is limited). */
  const limits = viewLimits(state);
  /** Open the inspector; focus returns to `opener` when it closes. */
  const openInspector = (target: InspectorTarget, opener: HTMLElement) => {
    inspectorOpener.current = opener;
    setInspector(target);
  };
  const openSelectionInspector = (opener: HTMLElement) =>
    openInspector({ kind: 'selection' }, opener);
  /**
   * The inspector's locate: offered only for a DRAWN record (a not-drawn one keeps its plain "Not
   * drawn now" and no locate). It selects exactly that character's record — the same complete,
   * source-qualified ref — closes the dialog and leaves the world visible.
   */
  const locateFromInspector = (id: number) => {
    if (!officeState.characters.has(id)) return;
    focusDockAfterLocate.current = true;
    setInspector(null);
    navigateTo(id);
  };

  return (
    <>
      {restoreControl}
      <aside
        // feature13: a calm operational list (diOperationalPanel.css). The Missions lead; the
        // ancillary entries above them are compact; detail is in the full-size inspector. The panel
        // still scrolls when a short window cannot hold it, so everything stays REACHABLE.
        ref={asideRef}
        hidden={collapsed}
        // the last-resort focus target on restore (not in the Tab order)
        tabIndex={-1}
        className={`di-op di-op-theme${
          keyed?.target.kind === 'member' ||
          keyed?.target.kind === 'runtime' ||
          keyed?.target.kind === 'unverified'
            ? ' di-op-has-selection'
            : ''
        }`}
        style={{ width: widthPx ?? DI_PANEL_WIDTH_PX }}
        data-testid="di-office-panel"
        data-minimized={collapsed ? 'true' : 'false'}
      >
        {fixtureNote && (
          <div
            ref={noteRef}
            className="di-op-fixture-note"
            role="status"
            data-testid="di-fixture-banner-in-panel"
          >
            {DI_FIXTURE_BANNER}
          </div>
        )}
        <header className="di-op-header">
          <div className="di-op-title">
            DI Mission office:{' '}
            <span className="di-op-title-status">
              {!state.live && waiting ? (
                <span className={WAITING_CLASS}>
                  NOT LIVE{state.fixtureMode ? ' (FIXTURE DATA)' : ''},{' '}
                  {formatWaiting(waiting, envelope.retryAfterSeconds)}
                </span>
              ) : (
                <LiveBadge live={state.live} reason={state.notLiveReason} />
              )}
            </span>
          </div>
          <button
            type="button"
            ref={minimizeRef}
            className={`di-op-quiet-button ${DI_FOCUS_RING}`}
            onClick={minimizePanel}
            title="Minimize the office panel; Restore brings it back as you left it"
            data-testid="di-office-panel-minimize"
          >
            Minimize <span aria-hidden="true">▸</span>
          </button>
        </header>

        {/* The orientation strip, in a FIXED order whatever the counts: the ONE attention entry, then
          the Mission request entry, then Find. Starting a Mission never depends on, or sits inside,
          the attention entry; an unavailable request capability is quieter, never removed. */}
        <div className="di-op-entries" data-testid="di-orientation">
          <section
            className="di-op-attention"
            data-testid="di-attention"
            aria-label="Needs attention"
          >
            {attention ? (
              <>
                <div className="di-op-attention-line" data-testid="di-reception-summary">
                  <span className={attention.count > 0 ? 'di-op-attention-count' : 'di-op-muted'}>
                    {attention.count > 0 && (
                      <span className="di-op-status-glyph" aria-hidden="true">
                        ▲{' '}
                      </span>
                    )}
                    {attention.text}
                  </span>
                  {receptionInbox && (
                    <button
                      type="button"
                      // AC4: entries wait for the person: a still glow, the ▲ shape and the count's words beside it
                      className={`di-op-link ${attention.count > 0 ? 'di-op-attn ' : ''}${DI_FOCUS_RING}`}
                      data-testid="di-open-reception"
                      data-attention={attention.count > 0 ? 'true' : undefined}
                      onClick={(event) => receptionInbox.openFrom(event.currentTarget)}
                      title="View only: opens the reception inbox, the same one as the reception desk in the office"
                    >
                      {attention.count > 0 && (
                        <span className="di-op-attn-glyph" aria-hidden="true">
                          ▲{' '}
                        </span>
                      )}
                      Open reception
                    </button>
                  )}
                </div>
              </>
            ) : (
              <div className="di-op-muted">Attention: this office provides no reception desk.</div>
            )}
            {limits && (
              // The view's material limits: ONE plain sentence with its consequence; the exact limits
              // one step away (the categories' outcomes and the coverage statement stay in full below).
              <div className="di-op-limits" data-testid="di-view-limits">
                {limits.summary}
              </div>
            )}
          </section>
          {/* item 4: the entry to the Mission request workspace, CONFIGURED: always here, right after
            the attention entry. It opens a pane BESIDE this panel, so the world stays usable. */}
          {!(mission !== null && requestsUnavailable(mission.state)) && (
            <DiMissionRequestEntry access={mission} />
          )}
        </div>

        <FindSection state={state} query={query} onQuery={setQuery} onSelect={selectRecord} />
        {/* Not available in this office: demoted to one quiet line below Find, never removed. */}
        {mission !== null && requestsUnavailable(mission.state) && (
          <DiMissionRequestEntry access={mission} />
        )}

        {announced.length > 0 && (
          // The office canvas draws ONLY snapshot-confirmed members; an announcement the office
          // state has not confirmed is reported here, as exactly that.
          <section className="di-op-entry" data-testid="di-unconfirmed">
            <div className="text-warning">
              Announced, not confirmed by an office snapshot (not drawn): {announced.length}
            </div>
            {announced.map((a) => (
              <div key={a.id} className="di-op-muted">
                character #{a.id} · {a.missionRef ?? 'Mission unknown'} · unbound until the office
                state lists it
              </div>
            ))}
          </section>
        )}

        {/* ONE scrolling region — the Missions first, then the reception desk, the runtime agents
          and the office data. The compact top and the Selected dock stay put. */}
        <div className="di-op-main" data-testid="di-op-main">
          <section
            // The PRIMARY list: what is running, its observed status and who works on it.
            className="di-op-missions"
            data-testid="di-missions"
            ref={missionsRef}
            // Focus target for "Show this Mission" (keyboard users land here).
            tabIndex={-1}
            aria-label="Missions"
          >
            <div className="di-op-section-head">
              <div className="di-op-section-title">
                Missions:{' '}
                <span
                  className={
                    missions.state === 'observed'
                      ? ''
                      : waiting
                        ? WAITING_CLASS
                        : observedClass({ ...missions, value: null })
                  }
                >
                  {formatListState(missions.state, missions.reason, missions.missions.length)}
                </span>
              </div>
              {missions.missions.length > 1 && (
                <div className="di-op-filter" data-testid="di-mission-filter">
                  <select
                    className={DI_FOCUS_RING}
                    data-testid="di-mission-select"
                    aria-label="Show one Mission, or all"
                    value={focusMission ?? ''}
                    onChange={(e) => {
                      const ref = e.target.value === '' ? null : e.target.value;
                      setFocusMission(ref);
                      // View only: bring the picked Mission's first drawn agent into focus.
                      const picked = missions.missions.find((m) => m.missionRef === ref);
                      navigateTo(
                        picked?.members.members.find((m) => m.characterId !== null)?.characterId ??
                          null,
                      );
                    }}
                  >
                    <option value="">All Missions ({missions.missions.length})</option>
                    {missions.missions.map((m) => (
                      <option key={m.missionRef} value={m.missionRef}>
                        {missionTitle(m).kind === 'no-goal'
                          ? missionLabel(m)
                          : `${missionTitle(m).text} (${shortRefs.get(m.missionRef)})`}
                      </option>
                    ))}
                  </select>
                  <button
                    type="button"
                    className={`di-op-button ${DI_FOCUS_RING}`}
                    data-testid="di-live-only"
                    aria-pressed={liveOnly}
                    onClick={() => setLiveOnly((v) => !v)}
                  >
                    <span aria-hidden="true">{liveOnly ? '☑' : '☐'}</span> Live only
                  </button>
                </div>
              )}
            </div>
            {shown.length < missions.missions.length && (
              // Nothing silently disappears: a view filter says what it leaves out, and how to undo it.
              <div className="di-op-filter-note" data-testid="di-mission-filter-note" role="status">
                Showing {shown.length} of {missions.missions.length}:{' '}
                {missions.missions.length - shown.length} not shown by{' '}
                {focusMission !== null ? 'the Mission picker' : '“Live only”'}.{' '}
                <button
                  type="button"
                  className={`di-op-link ${DI_FOCUS_RING}`}
                  onClick={() => {
                    setFocusMission(null);
                    setLiveOnly(false);
                  }}
                >
                  Show all
                </button>
              </div>
            )}
            {(() => {
              // A coverage limit changes how a team is read: a gap stays on the face; the ordinary
              // statement (and what a Herdr task record is) is said once, one step away.
              const linked = formatLinkedChildCoverage(coverage);
              const herdr = missions.missions.some((m) => m.herdrTask);
              return (
                <details className="di-op-about" data-testid="di-missions-about">
                  <summary className={DI_FOCUS_RING}>About this list</summary>
                  <div className="di-op-detail-body">
                    <div data-testid="di-coverage">{linked.text}</div>
                    {herdr && (
                      <div data-testid="di-herdr-task-about">
                        A Herdr task record is a plain Herdr task, not a Mission Core Mission:
                        proposal, authorization, revision and proof are not supported for it.
                      </div>
                    )}
                  </div>
                </details>
              );
            })()}
            {shown.map((m) => (
              <MissionCard
                key={m.missionRef}
                mission={m}
                status={missionStatus(m, state)}
                shortRef={shortRefs.get(m.missionRef) ?? m.missionRef}
                onLocate={navigateTo}
                selectedKey={selectedKey}
                onSelect={selectKey}
                onOpenInspector={(missionRef, opener, section) =>
                  openInspector({ kind: 'mission', missionRef, section }, opener)
                }
                zone={missionZone(
                  m.missionRef,
                  officeState.areaMappings,
                  officeState.getLayout().areas ?? [],
                )}
              />
            ))}
          </section>

          {/* AC5: design samples from the one labelled fixture file, in a fixture preview only (FIXTURE labelled) */}
          <DiMissionStateSamples fixtureMode={state.fixtureMode === true} />

          <RuntimeAgentsSection
            runtime={state.runtimeAgents}
            selectedKey={selectedKey}
            onSelect={selectKey}
            freshness={runtimeFreshness}
            now={now}
          />

          <section className="di-op-secondary" aria-label="Office data">
            <div className="di-op-section-title">Office data</div>
            <section className="di-op-selected-body" data-testid="di-freshness-summary">
              <OfficeDataRows state={state} />
            </section>
            <details data-testid="di-freshness-detail">
              <summary className={DI_FOCUS_RING}>Freshness detail and times</summary>
              <div className="di-op-detail-body">
                <FreshnessDetailRows state={state} />
              </div>
            </details>
          </section>
        </div>

        {/* The Selected dock: always in view; its own scroll when a selection holds more. It is
          concise — who, its identity and observed state, its Mission, the view-only routes, the
          code review entry and one obvious "Show details" — and the inspector holds the rest. */}
        <section
          className={`di-op-selected${
            keyed?.target.kind === 'member' ||
            keyed?.target.kind === 'runtime' ||
            keyed?.target.kind === 'unverified'
              ? ' di-op-selected-actions'
              : ''
          }`}
          data-testid="di-selected-agent"
        >
          {/* Where the selection is, in words, then the view-only stepping: each pair has a visible
            label and visible words; each button's accessible name begins with those words. */}
          <div className="di-op-section-head">
            <div className="di-op-section-title">
              Selected{' '}
              <span className="di-op-muted di-op-nav-position" data-testid="di-navigation-position">
                · {navigationOrientation(state, selectedId)}
              </span>
            </div>
          </div>
          <div
            className="di-op-nav"
            data-testid="di-navigation"
            title="View only: moves the selection and camera; it controls nothing"
          >
            <span
              className="di-op-nav-group"
              role="group"
              aria-label="Step through agents on the map"
            >
              <span className="di-op-nav-label" aria-hidden="true">
                Agents
              </span>
              <button
                type="button"
                className={DI_FOCUS_RING}
                aria-label="Previous agent"
                onClick={() => navigateTo(navigateStep(navigationOrder(state), selectedId, -1))}
              >
                ‹ Previous
              </button>
              <button
                type="button"
                className={DI_FOCUS_RING}
                aria-label="Next agent"
                onClick={() => navigateTo(navigateStep(navigationOrder(state), selectedId, 1))}
              >
                Next ›
              </button>
            </span>
            <span className="di-op-nav-group" role="group" aria-label="Step through Missions">
              <span className="di-op-nav-label" aria-hidden="true">
                Missions
              </span>
              <button
                type="button"
                className={DI_FOCUS_RING}
                aria-label="Previous Mission"
                onClick={() => navigateTo(nextMissionCharacter(state, selectedId, -1))}
              >
                ‹ Previous
              </button>
              <button
                type="button"
                className={DI_FOCUS_RING}
                aria-label="Next Mission"
                onClick={() => navigateTo(nextMissionCharacter(state, selectedId, 1))}
              >
                Next ›
              </button>
            </span>
          </div>
          {keyed?.target.kind === 'member' ? (
            <DockMember
              member={keyed.target.member}
              name={firstLayerName(state, {
                kind: 'member',
                mission: keyed.target.mission,
                member: keyed.target.member,
              })}
              mission={keyed.target.mission}
              shortRef={
                shortRefs.get(keyed.target.mission.missionRef) ?? keyed.target.mission.missionRef
              }
              freshness={snapshotFreshness}
              observedAt={times.snapshotObservedAt}
              now={now}
              onShow={navigateTo}
              onShowMission={showMission}
              onOpenInspector={openSelectionInspector}
              review={review}
            />
          ) : keyed?.target.kind === 'runtime' ? (
            <DockRuntimeAgent
              agent={keyed.target.agent}
              name={firstLayerName(state, { kind: 'runtime', agent: keyed.target.agent })}
              freshness={runtimeFreshness}
              observedAt={state.runtimeAgents?.observedAt ?? null}
              now={now}
              onShow={navigateTo}
              onOpenInspector={openSelectionInspector}
            />
          ) : keyed?.target.kind === 'unverified' ? (
            <DockUnverified
              agent={keyed.target.agent}
              name={firstLayerName(state, { kind: 'runtime', agent: keyed.target.agent })}
              freshness={runtimeFreshness}
              observedAt={state.runtimeAgents?.observedAt ?? null}
              now={now}
              onOpenInspector={openSelectionInspector}
            />
          ) : memory !== null ? (
            // The selected record left the office state: said so, with why; nothing replaces it.
            <SelectionGone
              memory={memory}
              state={state}
              freshness={
                memory.kind === 'member' || memory.kind === 'mission'
                  ? snapshotFreshness
                  : runtimeFreshness
              }
              onClear={() => setMemory(null)}
            />
          ) : selectedId !== null ? (
            // A drawn character NO office state lists: say exactly that, invent nothing.
            <div className="text-warning" data-testid="di-selected-unconfirmed">
              {DI_UNCONFIRMED_LABEL}. No canonical identity or activity is shown for it.
            </div>
          ) : (
            <div className="di-op-muted">
              Select a character in the office, or a row in the lists (Enter or Space).
            </div>
          )}
        </section>

        {receptionDialog}

        {inspector && (
          <OfficeInspector
            target={inspector}
            state={state}
            keyed={keyed}
            snapshotFreshness={snapshotFreshness}
            runtimeFreshness={runtimeFreshness}
            evidence={evidence ?? null}
            review={review ?? null}
            onShow={locateFromInspector}
            onClose={() => setInspector(null)}
            returnFocusTo={inspectorOpener}
          />
        )}
      </aside>
    </>
  );
}
