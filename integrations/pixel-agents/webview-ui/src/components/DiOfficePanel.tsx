import './diOperationalPanel.css';

import { createContext, useContext, useEffect, useRef, useState } from 'react';
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
import type { DiEvidenceAccess } from './DiEvidenceLibrary.js';
import { DiEvidenceLibrary } from './DiEvidenceLibrary.js';
import type { DiMissionRequestAccess } from './DiMissionRequestEntry.js';
import { DiMissionRequestEntry } from './DiMissionRequestEntry.js';
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
  DI_UNCONFIRMED_LABEL,
  filterMissions,
  formatActivity,
  formatActivityShort,
  formatBoundedList,
  formatEpoch,
  formatFactState,
  formatLinkedChildCoverage,
  formatListState,
  formatLive,
  formatLivePlain,
  formatMaybe,
  formatMemberChain,
  formatMemberCount,
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
  shortMemberIdentity,
  teamRoles,
} from './diOperationalStatus.js';
import { DiReceptionDesk } from './DiReceptionDesk.js';
import { useRovingFocus } from './diRovingFocus.js';

interface DiOfficePanelProps {
  state: DiOfficeState;
  officeState: OfficeState;
  /** Owned by App, which narrows the office canvas while the panel is docked open. */
  collapsed: boolean;
  onCollapsedChange: (collapsed: boolean) => void;
  /** Server-announced characters no office snapshot has confirmed: reported, never drawn. */
  announced?: ReadonlyArray<{ id: number; missionRef: string | null }>;
  /** item 2: the evidence library's content seam (absent: the library lists, never reads). */
  evidence?: DiEvidenceAccess | null;
  /** item 3: the live code review seam (absent: the offer is stated, nothing can be opened). */
  review?: DiCodeReviewAccess | null;
  /** item 4: the Mission request workspace seam (absent: no entry is drawn). */
  mission?: DiMissionRequestAccess | null;
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
      <div data-testid="di-selected-runtime-plain">{agent.plainText}</div>
      <div className="text-text-muted">
        Not a team member: no Mission or team roster includes this agent, and nothing here shows it
        working on a task.
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
          : `${formatActivity(agent.activity)} — the terminal's own report; not task progress, completion, review, verification or delivery`}
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
        Not established: its link to the recorded role binding, and so any role or task membership —{' '}
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

/** feature13: the dock's "Open inspector" — one obvious, labelled entry to the full record. */
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
      Open inspector
    </button>
  );
}

/**
 * feature13 — the Selected dock's CONCISE runtime agent: who it is, that it is not a team member,
 * its observed state and where it is; everything else is in the inspector.
 */
function DockRuntimeAgent({
  agent,
  freshness,
  onShow,
  onOpenInspector,
}: {
  agent: DiRuntimeAgent;
  freshness: DiFreshness;
  onShow: (id: number) => void;
  onOpenInspector: (opener: HTMLElement) => void;
}) {
  return (
    <div className="di-op-selected-body" data-testid="di-selected-runtime-agent">
      <div className="di-op-selected-title">
        {agent.logical}{' '}
        <span className="di-op-muted">
          · {agent.agent} · {rootLabel(agent)}
        </span>
      </div>
      <div className="di-op-card-meta">
        <span data-testid="di-selected-runtime-plain">{agent.plainText}</span>
        <PlainLiveBadge live={agent.live} />
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
      <div className="text-text-muted">Not a team member.</div>
      <Freshness freshness={freshness} />
    </div>
  );
}

/** feature13 — the Selected dock's CONCISE not-verified record (full in the inspector). */
function DockUnverified({
  agent,
  freshness,
  onOpenInspector,
}: {
  agent: DiRuntimeAgent;
  freshness: DiFreshness;
  onOpenInspector: (opener: HTMLElement) => void;
}) {
  const provider = agent.provider ? `provider ${agent.provider}` : 'provider not recorded';
  return (
    <div className="di-op-selected-body" data-testid="di-selected-unverified">
      <div className="di-op-selected-title">
        {agent.logical}{' '}
        <span className="di-op-muted">
          · {agent.agent} · {rootLabel(agent)}
        </span>
      </div>
      <div className="text-warning">
        Runtime identity {plainIdentityState(agent.identityState)}. Not drawn; not a team member.
      </div>
      {/* The doorway comes right after the caveat and BEFORE the observed details: the dock scrolls on its
          own, so an action placed last can be out of sight on initial selection. */}
      <div className="di-op-actions">
        <OpenInspectorButton onOpen={onOpenInspector} testid="di-open-inspector-selection" />
      </div>
      <UnverifiedObserved agent={agent} provider={provider} freshness={freshness} />
      <div data-testid="di-unverified-not-established">
        Not established: {plainIdentityReason(agent.identityReason)}.
      </div>
      {/* The observation's time and whether it is current, always (as for every dock record). */}
      <Freshness freshness={freshness} />
    </div>
  );
}

function RuntimeAgentProvenance({ agent }: { agent: DiRuntimeAgent }) {
  return (
    <details data-testid="di-runtime-provenance">
      <summary className={DI_FOCUS_RING}>
        Runtime identity provenance (raw identifiers and reasons)
      </summary>
      <div className="di-op-detail-body">
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
    </details>
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
}: {
  runtime: DiRuntimeAgents | undefined;
  selectedKey: string | null;
  onSelect: (key: string) => void;
  freshness?: DiFreshness;
}) {
  const verifiedRows = useRovingFocus<HTMLButtonElement>();
  const unverifiedRows = useRovingFocus<HTMLButtonElement>();
  if (!runtime) {
    return (
      <section className="di-op-secondary text-text-muted" data-testid="di-runtime-absent">
        Runtime agents: not provided by this office.
      </section>
    );
  }
  const capture = runtime.capture;
  const verified = runtime.agents.filter((a) => a.identityVerified);
  const unverified = runtime.agents.filter((a) => !a.identityVerified);
  const unverifiedCount = runtime.counts
    .filter((c) => c.name === 'unverified' || c.name === 'ambiguous' || c.name === 'unavailable')
    .reduce((n, c) => n + c.value, 0);
  return (
    <section className="di-op-secondary" data-testid="di-runtime-agents">
      <div className="di-op-section-title">Runtime agents (not team members)</div>
      <div className="di-op-muted" data-testid="di-runtime-statement">
        {formatRuntimeAgentsStatement(runtime)}
      </div>
      {verified.length > 0 && (
        <ul
          className="di-op-members"
          aria-label="Runtime agents with a verified identity (arrow keys move between them)"
          data-testid="di-runtime-rows"
        >
          {verified.map((agent, i) => (
            <ListRow
              key={agent.agentRef}
              testid="di-runtime-row"
              selected={selectedKey === `runtime:${agent.agentRef}`}
              onSelect={() => onSelect(`runtime:${agent.agentRef}`)}
              refAt={verifiedRows.refAt(i)}
              onKeyDown={verifiedRows.onKeyDown(i, verified.length)}
              title={
                agent.characterId !== null
                  ? 'Select: show this agent in the office and the inspector (view only)'
                  : 'Select: inspect it (not drawn now)'
              }
            >
              {agent.logical} · {agent.agent} · {rootLabel(agent)}: {agent.plainText}{' '}
              <PlainLiveBadge live={agent.live} />
            </ListRow>
          ))}
        </ul>
      )}
      <section className="di-op-selected-body" data-testid="di-unverified">
        <div className="di-op-section-title">Not verified (not drawn): {unverified.length}</div>
        <div className="text-text-muted">
          {unverified.length === 0
            ? 'Every listed runtime record has a verified identity.'
            : `${
                freshness.current
                  ? 'A live native agent named in each row is running (current observation)'
                  : 'In the last observation a native agent named in each row was running; that observation is NOT current'
              }, but its link to the recorded role binding is not established, so no role or task membership is established and it is not drawn as a verified character. A name, pane, workspace or timestamp does not establish that link.`}
          {unverifiedCount > unverified.length
            ? ` ${unverifiedCount - unverified.length} more not-verified record(s) are counted but not listed (display cap).`
            : ''}
        </div>
        {unverified.length > 0 && <Freshness freshness={freshness} />}
        {unverified.length > 0 && (
          <ul
            className="di-op-members"
            aria-label="Runtime records whose identity is not verified (arrow keys move between them)"
          >
            {unverified.map((agent, i) => (
              <ListRow
                key={agent.agentRef}
                testid="di-unverified-row"
                selected={selectedKey === `runtime:${agent.agentRef}`}
                onSelect={() => onSelect(`runtime:${agent.agentRef}`)}
                refAt={unverifiedRows.refAt(i)}
                onKeyDown={unverifiedRows.onKeyDown(i, unverified.length)}
                title="Select: inspect this record (it is never drawn)"
              >
                {agent.logical} · {agent.agent} · {rootLabel(agent)}: {agent.plainText}.{' '}
                {freshness.current
                  ? 'Observed, current: a live native agent with this name is running.'
                  : 'Last observed: a native agent with this name was running (NOT current).'}{' '}
                Not established: {plainIdentityReason(agent.identityReason)}.{' '}
                <PlainLiveBadge live={agent.live} />
              </ListRow>
            ))}
          </ul>
        )}
      </section>
      <details data-testid="di-runtime-detail">
        <summary className={DI_FOCUS_RING}>
          Runtime evidence: counts, capture capacity, registry account and limits
        </summary>
        <div className="di-op-detail-body">
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
              identity {a.identityState} ({a.identityReason}) · assignment {a.assignmentState} (
              {a.assignmentReason})
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
  onShow: (id: number) => void;
  review: DiCodeReviewAccess | null;
}) {
  const outcomes = plainMissionOutcomes(mission);
  return (
    <div className="di-op-selected-body" data-testid="di-inspector-member">
      <div className="di-op-selected-title">
        {member.logical} <span className="di-op-muted">· {member.role}</span>
      </div>
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
        {formatActivity(member.activity)} — the terminal&apos;s own report; not task progress,
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
 * this Mission in the list, the code review entry, and one obvious "Open inspector"); THEN the
 * freshness of that observation and ONE short line naming its Mission by its complete
 * source-qualified ref (the objective is on the card). The doorways come before the descriptive
 * lines on purpose: the dock scrolls on its own, so an action placed last was below its visible
 * area on initial selection at a constrained window. Everything else — the code review explanation,
 * outcomes, what no source reports and the provenance — is in the inspector.
 */
function DockMember({
  member,
  mission,
  freshness,
  onShow,
  onShowMission,
  onOpenInspector,
  review,
}: {
  member: DiMember;
  mission: DiMission;
  freshness: DiFreshness;
  onShow: (id: number) => void;
  onShowMission: (missionRef: string) => void;
  onOpenInspector: (opener: HTMLElement) => void;
  review: DiCodeReviewAccess | null;
}) {
  return (
    <div className="di-op-selected-body" data-testid="di-selected-member">
      <div className="di-op-selected-title">
        {member.logical} <span className="di-op-muted">· {member.role}</span>
      </div>
      <div className="di-op-card-meta">
        <span>{shortMemberIdentity(member.classification)}</span>
        <span className="di-op-muted">terminal {formatActivityShort(member.activity)}</span>
        <LiveBadge live={member.live} reason={null} />
      </div>
      {/* The doorways come BEFORE the descriptive lines. The dock scrolls on its own and never grows past a
          share of the panel; at a constrained window an action placed after the freshness sentence and the
          Mission ref was below the dock's visible area on initial selection. */}
      <div className="di-op-actions">
        <OnTheMap
          characterId={member.characterId}
          live={member.live}
          notLiveReason={member.notLiveReason}
          onShow={onShow}
        />
        <button
          type="button"
          className={`di-op-link ${DI_FOCUS_RING}`}
          data-testid="di-show-mission"
          onClick={() => onShowMission(mission.missionRef)}
          title="View only: shows this Mission in the list"
        >
          Show this Mission in the list
        </button>
      </div>
      <div className="di-op-actions">
        <DiCodeReviewEntry
          member={member}
          missionRef={mission.missionRef}
          access={review}
          variant="dock"
        />
        <OpenInspectorButton onOpen={onOpenInspector} testid="di-open-inspector-selection" />
      </div>
      <Freshness freshness={freshness} />
      <div>
        Team member of Mission <span className="di-op-ref">{mission.missionRef}</span>
      </div>
    </div>
  );
}

/** item 07: a plain Herdr task, said plainly first; its provenance one step behind. */
export function HerdrTaskNote({ herdrTask }: { herdrTask: DiHerdrTask }) {
  return (
    <div className="flex flex-col gap-1" data-testid="di-herdr-task">
      <div>
        Herdr task — a plain Herdr task record, not a Mission Core Mission. Mission Core facts
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
  zone,
  selectedKey,
  onSelect,
  onOpenInspector,
}: {
  mission: DiMission;
  status: DiOpStatusResult;
  zone: { label: string; color: string } | null;
  selectedKey: string | null;
  onSelect: (key: string) => void;
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
    const observer = new ResizeObserver(() => {
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
  const roles = teamRoles(mission);
  const members = mission.members.members;
  const objectiveId = `di-op-objective-${mission.missionRef}`;
  return (
    <article
      ref={cardRef}
      className="di-op-card"
      data-selected-mission={holdsSelection ? 'true' : undefined}
      data-mission-ref={mission.missionRef}
      aria-labelledby={objectiveId}
    >
      {/* What is running: the objective, in plain words — or, when the source gives no goal, that
          limitation said plainly and source-qualified (nothing is ever substituted for a goal). */}
      <p
        id={objectiveId}
        className="di-op-objective di-op-clamp-2"
        data-goal={objective.kind === 'goal' ? 'goal' : 'none'}
        data-testid="di-mission-objective"
        title={objective.text}
      >
        {objective.text}
      </p>
      {/* Its observed status and who is working on it: one line each. */}
      <div className="di-op-card-meta">
        <StatusBadge result={status} withSentence={false} />
        <span className="di-op-team" data-testid="di-mission-team">
          {mission.members.state === 'observed' ? (
            <>
              Team {members.length} · {members.filter((m) => m.live).length} live
              {roles.length > 0 && <span className="di-op-team-roles"> · {roles.join(', ')}</span>}
            </>
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
      {mission.herdrTask && (
        <div className="di-op-status-sentence" data-testid="di-herdr-task-face">
          Herdr task: a plain Herdr task record, not a Mission Core Mission.
        </div>
      )}
      <div className="di-op-card-foot">
        {zone ? (
          // Ties this Mission to its labelled zone on the office floor (Areas overlay).
          <span className="di-op-zone" data-testid="di-mission-zone">
            <span className="di-op-swatch" style={{ background: zone.color }} />
            <span>office zone {zone.label}</span>
          </span>
        ) : (
          <span />
        )}
        <span className="di-op-actions">
          {mission.evidence && (
            // The evidence library is in the inspector; this is its direct, labelled doorway.
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
              Evidence library
            </button>
          )}
          <button
            type="button"
            className={`di-op-button ${DI_FOCUS_RING}`}
            data-testid="di-open-inspector-mission"
            aria-describedby={objectiveId}
            aria-haspopup="dialog"
            onClick={(event) => onOpenInspector(mission.missionRef, event.currentTarget)}
          >
            Open inspector
          </button>
        </span>
      </div>
      {members.length > 0 && (
        <details
          className="di-op-team-details"
          data-testid="di-mission-roster"
          open={teamOpen}
          onToggle={(event) => setTeamOpen(event.currentTarget.open)}
        >
          <summary className={DI_FOCUS_RING}>Team members ({members.length})</summary>
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
                      {member.logical} <span className="di-op-sub">({member.role})</span>
                      {mission.v2.projected && (
                        <span className="di-op-sub">
                          {' '}
                          · {member.chain}
                          {member.chain === 'child' && member.chainTaskId
                            ? ` ${member.chainTaskId}`
                            : ''}
                        </span>
                      )}{' '}
                      <span className="di-op-sub">
                        · {shortMemberIdentity(member.classification)} · terminal{' '}
                        {formatActivityShort(member.activity)} ·{' '}
                      </span>
                      <PlainLiveBadge live={member.live} />
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
  state,
  evidence,
  review,
  onShow,
}: {
  mission: DiMission;
  status: DiOpStatusResult;
  state: DiOfficeState;
  evidence: DiEvidenceAccess | null;
  review: DiCodeReviewAccess | null;
  onShow: (id: number) => void;
}) {
  const objective = missionObjective(mission);
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
          <StatusBadge result={status} />
        </div>
        <div data-testid="di-mission-plain">
          {plainStanding(mission.standing, mission.lifecycleState)} ·{' '}
          {plainBinding(mission.bindingState, mission.bindingReason)} · source {mission.sourceId}
        </div>
        <PlainLiveBadge live={mission.live} />
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
            {mission.v2.projected
              ? '(direct + linked child tasks, depth 1)'
              : '(the direct task only)'}
            {' · '}
            {mission.members.state === 'observed'
              ? formatMemberCount(mission.members.members)
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
                <div className="di-op-selected-title">
                  {member.logical} <span className="di-op-muted">· {member.role}</span>
                </div>
                <div>{plainMemberIdentity(member.classification)}</div>
                <div className="di-op-card-meta">
                  <span className="di-op-muted">
                    terminal {formatActivity(member.activity)} (the terminal&apos;s own report)
                  </span>
                  <PlainLiveBadge live={member.live} />
                </div>
                <OnTheMap
                  characterId={member.characterId}
                  live={member.live}
                  notLiveReason={member.notLiveReason}
                  onShow={onShow}
                />
                <DiCodeReviewEntry
                  member={member}
                  missionRef={mission.missionRef}
                  access={review}
                />
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
          <div className="di-op-muted">
            Not reported at contract version {state.contractVersion}: task chains, the children
            record, the Mission State and canonical review are contract v2 blocks.
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
        <div className="di-op-muted">
          Code review is offered per team member, in the Team section above.
        </div>
      </section>
      <section className="di-op-inspector-section" data-testid="di-inspector-provenance">
        <h3>Provenance and raw codes</h3>
        <div className="di-op-detail-body di-op-detail-open">
          <Row label="mission_ref">
            <span className="di-op-ref">{mission.missionRef}</span>
          </Row>
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
      </section>
    </>
  );
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
  const heading =
    record?.target.kind === 'member'
      ? `${record.target.member.logical} · ${record.target.member.role}`
      : record?.target.kind === 'runtime' || record?.target.kind === 'unverified'
        ? `${record.target.agent.logical} · ${record.target.agent.agent}`
        : mission
          ? missionLabel(mission)
          : 'This record is no longer in the office state';
  // Which record this is, by its complete source-qualified ref: two lookalikes never share a heading.
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
        ? 'Runtime agent (not a team member)'
        : record?.target.kind === 'unverified'
          ? 'Runtime record, not verified'
          : 'Mission';
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
              {kindLabel} · inspector (view only)
              {state.fixtureMode ? ' · FIXTURE DATA' : ''}
            </div>
            {/* A COMPACT, BOUNDED, VERBATIM heading (missionHeadline's rule; at most two lines),
                then the complete ref. The full objective appears ONCE, in the body. */}
            <h2
              id="di-inspector-title"
              className="di-op-inspector-title di-op-clamp-2"
              title={heading}
            >
              {heading}
            </h2>
            {qualifier !== null && (
              <div className="di-op-ref di-op-muted" data-testid="di-inspector-qualifier">
                {qualifier}
              </div>
            )}
            {status && (
              <div className="di-op-card-meta">
                <StatusBadge result={status} withSentence={false} />
                <LiveBadge live={state.live} reason={state.notLiveReason} />
              </div>
            )}
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
            <section className="di-op-inspector-section" data-testid="di-inspector-selected">
              <h3>Selected team member</h3>
              <MemberDetail
                member={record.target.member}
                mission={record.target.mission}
                freshness={snapshotFreshness}
                onShow={onShow}
                review={reviewHere}
              />
            </section>
          )}
          {record?.target.kind === 'runtime' && (
            <section className="di-op-inspector-section" data-testid="di-inspector-selected">
              <h3>Selected runtime agent</h3>
              <SelectedRuntimeAgent
                agent={record.target.agent}
                freshness={runtimeFreshness}
                onShow={onShow}
                testid="di-inspector-runtime-agent"
              />
            </section>
          )}
          {record?.target.kind === 'unverified' && (
            <section className="di-op-inspector-section" data-testid="di-inspector-selected">
              <h3>Selected runtime record (identity not verified)</h3>
              <SelectedUnverified
                agent={record.target.agent}
                freshness={runtimeFreshness}
                testid="di-inspector-unverified"
              />
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
              state={state}
              evidence={evidence}
              review={reviewHere}
              onShow={onShow}
            />
          )}
          <section className="di-op-inspector-section" data-testid="di-inspector-freshness">
            <h3>Freshness and times</h3>
            <div className="di-op-detail-body di-op-detail-open">
              <OfficeDataRows state={state} />
              <FreshnessDetailRows state={state} />
            </div>
          </section>
          <section className="di-op-inspector-section" data-testid="di-inspector-limitations">
            <h3>Limitations: what is not reported or not supported</h3>
            <div className="di-op-detail-body di-op-detail-open">
              <Row label="coverage">{state.coverage.statement}</Row>
              <Row label="linked child-task roles">
                {formatLinkedChildCoverage(state.coverage).text}
              </Row>
              <Row label="not reported by any supported source">
                model requested / configured, model confirmed running, usage, cost, progress and ETA
                — {RUNTIME_UNREPORTED} for every agent here
              </Row>
              <Row label="runtime agents">{formatRuntimeAgentsStatement(state.runtimeAgents)}</Row>
              <Row label="this view">
                Read-only. Nothing here approves, starts, stops, retries or delivers anything.
              </Row>
            </div>
          </section>
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
            ? `${p.recordSource ?? 'source not recorded'} · collection ${p.collectionStatus ?? 'not recorded'} · position ${p.position ?? '—'} (head ${p.headPosition ?? '—'}, cursor ${p.cursorPosition ?? '—'}) · drift ${formatBoundedList(p.drift, p.driftTotal)}`
            : `${p.state}${p.reason ? ` (${p.reason})` : ''}`}
        </div>
      </details>
      <section className="flex flex-col gap-1 break-all" data-testid="di-review-preamble">
        <div>Canonical review: READ-ONLY preamble context</div>
        {preamble.admitted ? (
          <div className="pl-8 flex flex-col gap-1">
            <div>
              round {review.round ?? '—'} · reviewer {review.reviewerLogical ?? '—'} (
              {review.reviewerAgent ?? '—'}) · protocol token {review.protocolToken ?? '—'}
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
      <Freshness freshness={freshness} />
      <details>
        <summary className={DI_FOCUS_RING}>Canonical reason and the last selection (raw)</summary>
        <div className="di-op-detail-body">
          <Row label="canonical reason">{why.canonical ?? 'none recorded by the office state'}</Row>
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
              {KIND_LABEL[r.target.kind]}: {r.title} <span className="di-op-sub">— {r.detail}</span>
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
  announced = [],
  evidence = null,
  review = null,
  mission = null,
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
  /** The reception desk below the Missions: "Go to the desk" scrolls it into view (view only). */
  const receptionRef = useRef<HTMLDivElement>(null);
  /** feature13: the full-size inspector — what it is open on, and the control that opened it. */
  const [inspector, setInspector] = useState<InspectorTarget | null>(null);
  const inspectorOpener = useRef<HTMLElement | null>(null);
  /**
   * A locate ("Show in the office") from inside the inspector closes it so the located agent and the
   * world are actually SEEN; focus then lands, predictably, on the Selected dock's "Open inspector"
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
  /** VIEW-ONLY navigation: moves the office selection and camera, nothing else. */
  const navigateTo = (id: number | null) => {
    if (id === null || !officeState.characters.has(id)) return;
    officeState.selectedAgentId = id;
    officeState.cameraFollowId = id;
    seenId.current = id;
    setSelectedId(id);
    setMemory(remember(recordForCharacter(state, id)));
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
    navigateTo(picked?.members.members.find((m) => m.characterId !== null)?.characterId ?? null);
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

  if (collapsed) {
    return (
      <button
        type="button"
        className={`di-op-theme di-op-collapsed ${DI_FOCUS_RING}`}
        onClick={() => onCollapsedChange(false)}
        data-testid="di-office-panel-collapsed"
      >
        DI office: <LiveBadge live={state.live} reason={state.notLiveReason} /> ▸ show
      </button>
    );
  }

  const receptionEntries = state.reception
    ? state.reception.entries.filter((e) => !hiddenReception.has(e.key)).length
    : 0;
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
    <aside
      // feature13: a calm operational list (diOperationalPanel.css). The Missions lead; the
      // ancillary entries above them are compact; detail is in the full-size inspector. The panel
      // still scrolls when a short window cannot hold it, so everything stays REACHABLE.
      className={`di-op di-op-theme${
        keyed?.target.kind === 'member' ||
        keyed?.target.kind === 'runtime' ||
        keyed?.target.kind === 'unverified'
          ? ' di-op-has-selection'
          : ''
      }`}
      style={{ width: DI_PANEL_WIDTH_PX }}
      data-testid="di-office-panel"
    >
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
          className={`di-op-quiet-button ${DI_FOCUS_RING}`}
          onClick={() => onCollapsedChange(true)}
        >
          ◂ hide
        </button>
      </header>

      <div className="di-op-entries">
        {/* item 4: the entry to the Mission request workspace (compact). It opens a pane BESIDE
            this panel, so the world stays visible and usable while a request is followed. */}
        <DiMissionRequestEntry access={mission} />
        {state.reception && (
          // The reception desk lives below the Missions; its attention count stays on the face.
          <div className="di-op-card-meta" data-testid="di-reception-summary">
            <span className={receptionEntries > 0 ? 'text-warning' : 'text-text-muted'}>
              Reception desk:{' '}
              {receptionEntries > 0
                ? `${receptionEntries} ${receptionEntries === 1 ? 'entry needs' : 'entries need'} attention`
                : 'no entries listed (read its categories)'}
            </span>
            <button
              type="button"
              className={`di-op-link ${DI_FOCUS_RING}`}
              onClick={() =>
                receptionRef.current?.scrollIntoView({ block: 'start', behavior: 'auto' })
              }
              title="View only: scrolls to the reception desk in this panel"
            >
              Go to the desk
            </button>
          </div>
        )}
      </div>

      <FindSection state={state} query={query} onQuery={setQuery} onSelect={selectRecord} />

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
                      {missionLabel(m)}
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
                  {liveOnly ? '[x]' : '[ ]'} live only
                </button>
              </div>
            )}
          </div>
          {(() => {
            // A coverage limit changes how a team is read: kept on the face, in one line.
            const linked = formatLinkedChildCoverage(coverage);
            return (
              <div
                className={linked.warning ? 'di-op-caveat' : 'di-op-muted'}
                data-testid="di-coverage"
              >
                {linked.text}
              </div>
            );
          })()}
          {filterMissions(missions.missions, focusMission, liveOnly).map((m) => (
            <MissionCard
              key={m.missionRef}
              mission={m}
              status={missionStatus(m, state)}
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

        <div ref={receptionRef} className="di-op-secondary">
          {state.reception ? (
            <DiReceptionDesk
              reception={state.reception}
              hidden={hiddenReception}
              onHide={(key) => setHiddenReception((prev) => new Set(prev).add(key))}
              onShowHidden={() => setHiddenReception(new Set())}
              onJump={jumpToMission}
              fixture={state.fixtureMode === true}
              compact
            />
          ) : (
            // An office state from a server without the desk: said so, never an empty desk.
            <section className="text-text-muted" data-testid="di-reception-absent">
              Reception desk: not provided by this office.
            </section>
          )}
        </div>

        <RuntimeAgentsSection
          runtime={state.runtimeAgents}
          selectedKey={selectedKey}
          onSelect={selectKey}
          freshness={runtimeFreshness}
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
          code review entry and one obvious "Open inspector" — and the inspector holds the rest. */}
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
        <div className="di-op-section-head">
          <div className="di-op-section-title">Selected</div>
          <div
            className="di-op-nav"
            data-testid="di-navigation"
            title="View only: moves the selection and camera; it controls nothing"
          >
            <button
              type="button"
              className={DI_FOCUS_RING}
              onClick={() => navigateTo(nextMissionCharacter(state, selectedId, -1))}
            >
              ◂ Mission
            </button>
            <button
              type="button"
              className={DI_FOCUS_RING}
              onClick={() => navigateTo(navigateStep(navigationOrder(state), selectedId, -1))}
            >
              ◂ agent
            </button>
            <button
              type="button"
              className={DI_FOCUS_RING}
              onClick={() => navigateTo(navigateStep(navigationOrder(state), selectedId, 1))}
            >
              agent ▸
            </button>
            <button
              type="button"
              className={DI_FOCUS_RING}
              onClick={() => navigateTo(nextMissionCharacter(state, selectedId, 1))}
            >
              Mission ▸
            </button>
          </div>
        </div>
        {keyed?.target.kind === 'member' ? (
          <DockMember
            member={keyed.target.member}
            mission={keyed.target.mission}
            freshness={snapshotFreshness}
            onShow={navigateTo}
            onShowMission={showMission}
            onOpenInspector={openSelectionInspector}
            review={review}
          />
        ) : keyed?.target.kind === 'runtime' ? (
          <DockRuntimeAgent
            agent={keyed.target.agent}
            freshness={runtimeFreshness}
            onShow={navigateTo}
            onOpenInspector={openSelectionInspector}
          />
        ) : keyed?.target.kind === 'unverified' ? (
          <DockUnverified
            agent={keyed.target.agent}
            freshness={runtimeFreshness}
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
  );
}
