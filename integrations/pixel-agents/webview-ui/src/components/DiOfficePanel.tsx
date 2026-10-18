import { createContext, useContext, useEffect, useRef, useState } from 'react';

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
  isNormalWaiting,
  missionHeadline,
  missionZone,
  navigateStep,
  navigationOrder,
  nextMissionCharacter,
  observedClass,
  WAITING_CLASS,
} from './diOfficeFormat.js';
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
}

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex gap-6">
      <span className="text-text-muted shrink-0">{label}</span>
      <span className="break-all min-w-0">{children}</span>
    </div>
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
    <div className="flex items-center gap-6" data-testid="di-on-the-map">
      <span>On the map{live ? '' : ' (not live)'}.</span>
      {onShow && (
        <button
          type="button"
          className={`cursor-pointer underline ${DI_FOCUS_RING}`}
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
 * item 07/09: a selected RUNTIME agent (verified identity) — plain outcome first (what is
 * verified, what is not), then that it is not a team member, where it is, and the facts no source
 * reports; the raw identifiers and reasons behind a labelled disclosure.
 */
export function SelectedRuntimeAgent({
  agent,
  freshness = FRESHNESS_NOT_GIVEN,
  onShow,
}: {
  agent: DiRuntimeAgent;
  freshness?: DiFreshness;
  onShow?: (id: number) => void;
}) {
  return (
    <div className="flex flex-col gap-2" data-testid="di-selected-runtime-agent">
      <div className="text-sm break-all">
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
 * C1: a selected UNVERIFIED runtime record — what IS observed (a live native agent carries this
 * name) kept apart from what is NOT established (the link to the recorded role binding, so any
 * role or task membership). Never drawn; nothing here makes it verified.
 */
export function SelectedUnverified({
  agent,
  freshness = FRESHNESS_NOT_GIVEN,
}: {
  agent: DiRuntimeAgent;
  freshness?: DiFreshness;
}) {
  const provider = agent.provider ? `provider ${agent.provider}` : 'provider not recorded';
  return (
    <div className="flex flex-col gap-2" data-testid="di-selected-unverified">
      <div className="text-sm break-all">
        {agent.role ?? agent.logical} · {agent.logical} · {rootLabel(agent)}
      </div>
      <div>
        Runtime identity {plainIdentityState(agent.identityState)}. Not drawn: it is not shown as a
        verified character.
      </div>
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

function RuntimeAgentProvenance({ agent }: { agent: DiRuntimeAgent }) {
  return (
    <details data-testid="di-runtime-provenance">
      <summary className={`cursor-pointer text-text-muted ${DI_FOCUS_RING}`}>
        Runtime identity provenance (raw identifiers and reasons)
      </summary>
      <div className="flex flex-col gap-1 pt-2">
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
        // break-words: prose wraps between words; only an over-long identifier is split.
        className={`w-full text-left pl-8 break-words cursor-pointer ${DI_FOCUS_RING} ${
          selected ? 'text-accent' : ''
        }`}
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
      <section className="text-text-muted shrink-0" data-testid="di-runtime-absent">
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
    <section
      className="flex flex-col gap-2 border-t-2 border-border pt-4 shrink-0"
      data-testid="di-runtime-agents"
    >
      <div className="text-sm">Runtime agents (not team members)</div>
      <div data-testid="di-runtime-statement">{formatRuntimeAgentsStatement(runtime)}</div>
      {verified.length > 0 && (
        <ul
          className="flex flex-col gap-1"
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
      <section className="flex flex-col gap-2 pt-2" data-testid="di-unverified">
        <div className="text-sm">Not verified (not drawn): {unverified.length}</div>
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
            className="flex flex-col gap-1"
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
        <summary className={`cursor-pointer text-text-muted ${DI_FOCUS_RING}`}>
          Runtime evidence: counts, capture capacity, registry account and limits
        </summary>
        <div className="flex flex-col gap-1 pt-2">
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
 * item 09: a selected TEAM MEMBER — plain outcome first: who it is, the Mission it is a member
 * of, whether its running identity is confirmed, where it is, its terminal state (kept apart from
 * every task/Mission outcome), the model and the facts no source reports; the complete canonical
 * refs and every reason code behind a labelled disclosure (never shortened).
 */
function SelectedMember({
  member,
  mission,
  freshness,
  onShow,
  onShowMission,
}: {
  member: DiMember;
  mission: DiMission;
  freshness: DiFreshness;
  onShow: (id: number) => void;
  onShowMission: (missionRef: string) => void;
}) {
  const outcomes = plainMissionOutcomes(mission);
  return (
    <div className="flex flex-col gap-2" data-testid="di-selected-member">
      <div className="text-sm">
        {member.role} · {member.logical}
      </div>
      <div className="flex flex-col gap-1">
        <span>
          Team member of the Mission: <Observed value={mission.objective} />
        </span>
        <button
          type="button"
          className={`self-start cursor-pointer underline ${DI_FOCUS_RING}`}
          data-testid="di-show-mission"
          onClick={() => onShowMission(mission.missionRef)}
          title="View only: shows this Mission in the list"
        >
          Show this Mission in the list
        </button>
      </div>
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
      <details data-testid="di-canonical-identity">
        <summary className={`cursor-pointer text-text-muted ${DI_FOCUS_RING}`}>
          Provenance: complete refs, sessions and reason codes
        </summary>
        <div className="flex flex-col gap-2 pt-2">
          <Row label="member_ref">{member.memberRef}</Row>
          <Row label="mission_ref">{mission.missionRef}</Row>
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
      </details>
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
 * One compact Mission card: the objective and its plain standing and team binding first; the
 * complete source-qualified ref and every raw code one step behind. Members are a keyboard list
 * (C1): select one to see it in the office and the inspector (view only).
 */
function MissionCard({
  mission,
  zone,
  selectedKey,
  onSelect,
}: {
  mission: DiMission;
  zone: { label: string; color: string } | null;
  selectedKey: string | null;
  onSelect: (key: string) => void;
}) {
  const holdsSelection = mission.members.members.some(
    (m) => selectedKey === `member:${m.memberRef}`,
  );
  const cardRef = useRef<HTMLDivElement>(null);
  const rows = useRovingFocus<HTMLButtonElement>();
  // The selected agent's Mission is brought into view: which Mission an agent belongs to.
  useEffect(() => {
    if (holdsSelection) cardRef.current?.scrollIntoView({ block: 'nearest' });
  }, [holdsSelection]);
  const groups = mission.v2.projected
    ? groupMembersByChain(mission.members.members)
    : [{ heading: null as string | null, members: mission.members.members }];
  const flat = groups.flatMap((g) => g.members);
  return (
    <div
      ref={cardRef}
      className={`flex flex-col gap-1 border-t-2 pt-3 ${holdsSelection ? 'border-accent' : 'border-border'}`}
      data-selected-mission={holdsSelection ? 'true' : undefined}
      data-mission-ref={mission.missionRef}
    >
      {/* Plain English first: the objective; the complete source-qualified ref one step behind. */}
      <div className="line-clamp-2 break-words text-sm">
        <Observed value={mission.objective} />
      </div>
      {mission.herdrTask && <HerdrTaskNote herdrTask={mission.herdrTask} />}
      {zone && (
        // Ties this Mission to its labelled zone on the office floor (Areas overlay).
        <div className="flex items-center gap-4" data-testid="di-mission-zone">
          <span
            className="inline-block w-8 h-8 border-2 border-border shrink-0"
            style={{ background: zone.color }}
          />
          <span>office zone {zone.label}</span>
        </div>
      )}
      <PlainLiveBadge live={mission.live} />
      <div data-testid="di-mission-plain">
        {plainStanding(mission.standing, mission.lifecycleState)} ·{' '}
        {plainBinding(mission.bindingState, mission.bindingReason)} · source {mission.sourceId}
      </div>
      <details>
        <summary className={`cursor-pointer text-text-muted ${DI_FOCUS_RING}`}>
          Record provenance: complete ref and codes
        </summary>
        <div className="flex flex-col gap-1 pt-2">
          <div className="break-all">{mission.missionRef}</div>
          <div className="text-text-muted break-all">
            {mission.producerDeclared} · {mission.lifecycleModel} · {mission.standing} /{' '}
            {mission.lifecycleState} · binding {mission.bindingState} ({mission.bindingReason})
          </div>
          {!mission.live && (
            <div className="text-text-muted break-all">
              not live: {mission.notLiveReason ?? 'reason not given'}
            </div>
          )}
        </div>
      </details>
      <div className="text-text-muted">
        {mission.v2.projected
          ? 'Members (direct + linked child tasks, depth 1):'
          : 'Direct-task members:'}{' '}
        <span
          className={
            mission.members.state === 'observed'
              ? ''
              : observedClass({ ...mission.members, value: null })
          }
        >
          {mission.members.state === 'observed'
            ? formatMemberCount(mission.members.members)
            : formatListState(
                mission.members.state,
                mission.members.reason,
                mission.members.members.length,
              )}
        </span>
      </div>
      {groups.map((group, gi) => (
        <div key={gi} className="flex flex-col gap-1" data-testid="di-member-group">
          {group.heading !== null && (
            <div className="pl-4 text-text-muted break-all">
              {group.heading} ({group.members.length})
            </div>
          )}
          <ul
            className="flex flex-col gap-1"
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
                      ? 'Select: show this agent in the office and the inspector (view only)'
                      : 'Select: inspect it (not drawn now)'
                  }
                >
                  {member.logical} ({member.role})
                  {mission.v2.projected && (
                    <span className="text-text-muted">
                      {' '}
                      · {member.chain}
                      {member.chain === 'child' && member.chainTaskId
                        ? ` ${member.chainTaskId}`
                        : ''}
                    </span>
                  )}{' '}
                  · {plainMemberIdentity(member.classification)} · terminal{' '}
                  {formatActivityShort(member.activity)} · <PlainLiveBadge live={member.live} />
                </ListRow>
              );
            })}
          </ul>
        </div>
      ))}
      {mission.v2.projected && <MissionV2Blocks v2={mission.v2} />}
    </div>
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
    <div className="flex flex-col gap-2" data-testid="di-selection-gone">
      <div className="text-warning">
        The selected record ({memory.label}) is no longer in the office state. Nothing else was
        selected in its place.
      </div>
      <div data-testid="di-selection-gone-why">Why: {why.plain}.</div>
      <Freshness freshness={freshness} />
      <details>
        <summary className={`cursor-pointer text-text-muted ${DI_FOCUS_RING}`}>
          Canonical reason and the last selection (raw)
        </summary>
        <div className="flex flex-col gap-1 pt-2">
          <Row label="canonical reason">{why.canonical ?? 'none recorded by the office state'}</Row>
          <Row label="office not-live reason">{freshness.reason ?? 'none (live)'}</Row>
          <Row label="selected record">{memory.key}</Row>
          <Row label="kind">{memory.kind}</Row>
          {memory.missionRef !== null && <Row label="its Mission">{memory.missionRef}</Row>}
        </div>
      </details>
      <button
        type="button"
        className={`self-start cursor-pointer underline ${DI_FOCUS_RING}`}
        onClick={onClear}
      >
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
  return (
    <section className="flex flex-col gap-2 shrink-0" data-testid="di-find">
      <label htmlFor="di-find-input">
        Find a Mission or agent (objective, name, role, provider or identifier)
      </label>
      <input
        id="di-find-input"
        type="search"
        autoComplete="off"
        spellCheck={false}
        className={`text-xs py-2 px-4 bg-bg border-2 border-border rounded-none text-text ${DI_FOCUS_RING}`}
        data-testid="di-find-input"
        aria-describedby="di-find-outcome"
        aria-controls="di-find-results"
        aria-keyshortcuts="ArrowDown Escape"
        value={query}
        onChange={(e) => onQuery(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === 'ArrowDown' && outcome && outcome.results.length > 0) {
            e.preventDefault();
            rows.focusFirst();
          } else if (e.key === 'Escape') {
            onQuery('');
          }
        }}
      />
      <div
        id="di-find-outcome"
        data-testid="di-find-outcome"
        role="status"
        className="text-text-muted"
      >
        {outcome
          ? outcome.statement
          : 'Type to search this office state. Arrow Down moves into the results; selecting one is view only.'}
      </div>
      {outcome && outcome.results.length > 0 && (
        <ul
          id="di-find-results"
          className="flex flex-col gap-1 max-h-260 overflow-y-auto"
          data-testid="di-find-results"
          aria-label="Matching records (arrow keys move between them)"
        >
          {outcome.results.map((r, i) => (
            <ListRow
              key={r.key}
              testid="di-find-result"
              selected={false}
              onSelect={() => onSelect(r)}
              refAt={rows.refAt(i)}
              onKeyDown={rows.onKeyDown(i, outcome.results.length)}
              title="Select: view only"
            >
              {KIND_LABEL[r.target.kind]}: {r.title}{' '}
              <span className="text-text-muted">— {r.detail}</span>
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
  /** VIEW-ONLY navigation: moves the office selection and camera, nothing else. */
  const navigateTo = (id: number | null) => {
    if (id === null || !officeState.characters.has(id)) return;
    officeState.selectedAgentId = id;
    officeState.cameraFollowId = id;
    seenId.current = id;
    setSelectedId(id);
    setMemory(remember(recordForCharacter(state, id)));
  };
  const { envelope, runtime, times, coverage, missions } = state;
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
  const waiting = isNormalWaiting(envelope.envelopeProblem) ? envelope.envelopeProblem : null;
  /** Round 11: freshness from the PRESENTED state (server freshness + this view's connection). */
  const snapshotFreshness = officeFreshness(state, times.snapshotObservedAt);
  const runtimeFreshness = officeFreshness(state, state.runtimeAgents?.observedAt ?? null);

  if (collapsed) {
    return (
      <button
        type="button"
        className="fixed top-8 right-8 z-20 pixel-panel py-4 px-8 text-xs cursor-pointer"
        onClick={() => onCollapsedChange(false)}
        data-testid="di-office-panel-collapsed"
      >
        DI office: <LiveBadge live={state.live} reason={state.notLiveReason} /> ▸ show
      </button>
    );
  }

  return (
    <aside
      // overflow-y-auto: content taller than the panel (an expanded freshness detail under a
      // selected agent) must stay REACHABLE — measured inaccessible before (R6).
      className="fixed top-0 right-0 h-full z-20 pixel-panel p-8 text-xs flex flex-col gap-6 overflow-y-auto"
      style={{ width: DI_PANEL_WIDTH_PX }}
      data-testid="di-office-panel"
    >
      <div className="flex items-start justify-between gap-6 shrink-0">
        <div className="text-sm">
          DI Mission office:{' '}
          {!state.live && waiting ? (
            <span className={WAITING_CLASS}>
              NOT LIVE{state.fixtureMode ? ' (FIXTURE DATA)' : ''},{' '}
              {formatWaiting(waiting, envelope.retryAfterSeconds)}
            </span>
          ) : (
            <LiveBadge live={state.live} reason={state.notLiveReason} />
          )}
        </div>
        <button
          type="button"
          className="cursor-pointer text-text-muted shrink-0"
          onClick={() => onCollapsedChange(true)}
        >
          ◂ hide
        </button>
      </div>

      <section className="flex flex-col gap-1 shrink-0" data-testid="di-freshness-summary">
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
          <span
            className={!runtime.fresh && waiting ? WAITING_CLASS : freshnessClass(runtime.fresh)}
          >
            {runtime.fresh ? 'fresh' : `not fresh: ${runtime.reason ?? 'reason not given'}`}
          </span>
        </Row>
      </section>

      <section className="flex flex-col gap-1 shrink-0" data-testid="di-coverage">
        <div>Coverage: {coverage.statement}</div>
        {(() => {
          const linked = formatLinkedChildCoverage(coverage);
          return <div className={linked.warning ? 'text-warning' : ''}>{linked.text}</div>;
        })()}
      </section>

      <FindSection state={state} query={query} onQuery={setQuery} onSelect={selectRecord} />

      {state.reception ? (
        <DiReceptionDesk
          reception={state.reception}
          hidden={hiddenReception}
          onHide={(key) => setHiddenReception((prev) => new Set(prev).add(key))}
          onShowHidden={() => setHiddenReception(new Set())}
          onJump={jumpToMission}
          fixture={state.fixtureMode === true}
        />
      ) : (
        // An office state from a server without the desk: said so, never an empty desk.
        <section className="text-text-muted shrink-0" data-testid="di-reception-absent">
          Reception desk: not provided by this office.
        </section>
      )}

      {announced.length > 0 && (
        // The office canvas draws ONLY snapshot-confirmed members; an announcement the office
        // state has not confirmed is reported here, as exactly that.
        <section
          className="flex flex-col gap-2 border-t-2 border-border pt-4 shrink-0"
          data-testid="di-unconfirmed"
        >
          <div className="text-sm text-warning">
            Announced, not confirmed by an office snapshot (not drawn): {announced.length}
          </div>
          {announced.map((a) => (
            <div key={a.id} className="text-text-muted break-all">
              character #{a.id} · {a.missionRef ?? 'Mission unknown'} · unbound until the office
              state lists it
            </div>
          ))}
        </section>
      )}

      <section
        className="flex flex-col gap-2 border-t-2 border-border pt-4 shrink-0"
        data-testid="di-selected-agent"
      >
        <div className="flex items-center justify-between gap-4">
          <div className="text-sm">Selected</div>
          <div
            className="flex gap-4 text-text-muted"
            data-testid="di-navigation"
            title="View only: moves the selection and camera; it controls nothing"
          >
            <button
              type="button"
              className={`cursor-pointer ${DI_FOCUS_RING}`}
              onClick={() => navigateTo(nextMissionCharacter(state, selectedId, -1))}
            >
              ◂ Mission
            </button>
            <button
              type="button"
              className={`cursor-pointer ${DI_FOCUS_RING}`}
              onClick={() => navigateTo(navigateStep(navigationOrder(state), selectedId, -1))}
            >
              ◂ agent
            </button>
            <button
              type="button"
              className={`cursor-pointer ${DI_FOCUS_RING}`}
              onClick={() => navigateTo(navigateStep(navigationOrder(state), selectedId, 1))}
            >
              agent ▸
            </button>
            <button
              type="button"
              className={`cursor-pointer ${DI_FOCUS_RING}`}
              onClick={() => navigateTo(nextMissionCharacter(state, selectedId, 1))}
            >
              Mission ▸
            </button>
          </div>
        </div>
        {keyed?.target.kind === 'member' ? (
          <SelectedMember
            member={keyed.target.member}
            mission={keyed.target.mission}
            freshness={snapshotFreshness}
            onShow={navigateTo}
            onShowMission={showMission}
          />
        ) : keyed?.target.kind === 'runtime' ? (
          <SelectedRuntimeAgent
            agent={keyed.target.agent}
            freshness={runtimeFreshness}
            onShow={navigateTo}
          />
        ) : keyed?.target.kind === 'unverified' ? (
          <SelectedUnverified agent={keyed.target.agent} freshness={runtimeFreshness} />
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
          <div className="text-text-muted">
            Select a character in the office, or a row below (Enter or Space), to inspect it.
          </div>
        )}
      </section>

      <RuntimeAgentsSection
        runtime={state.runtimeAgents}
        selectedKey={selectedKey}
        onSelect={selectKey}
        freshness={runtimeFreshness}
      />

      <section
        // min-h-160: the bounded Missions scroll keeps a usable height instead of collapsing to
        // a sliver when the sections above grow (measured 4 px before, R6 state).
        className="flex flex-col gap-4 min-h-160 flex-1 overflow-y-auto border-t-2 border-border pt-4 focus-visible:outline-2 focus-visible:outline-accent"
        data-testid="di-missions"
        ref={missionsRef}
        // Focus target for "Show this Mission" (keyboard users land here).
        tabIndex={-1}
        aria-label="Missions"
      >
        <div className="text-sm">
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
          <div className="flex items-center gap-4" data-testid="di-mission-filter">
            <select
              className={`flex-1 min-w-0 text-xs py-2 px-4 bg-bg border-2 border-border rounded-none text-text ${DI_FOCUS_RING}`}
              data-testid="di-mission-select"
              aria-label="Show one Mission, or all"
              value={focusMission ?? ''}
              onChange={(e) => {
                const ref = e.target.value === '' ? null : e.target.value;
                setFocusMission(ref);
                // View only: bring the picked Mission's first drawn agent into focus.
                const picked = missions.missions.find((m) => m.missionRef === ref);
                navigateTo(
                  picked?.members.members.find((m) => m.characterId !== null)?.characterId ?? null,
                );
              }}
            >
              <option value="">All Missions ({missions.missions.length})</option>
              {missions.missions.map((m) => (
                <option key={m.missionRef} value={m.missionRef}>
                  {missionHeadline(m)}
                </option>
              ))}
            </select>
            <button
              type="button"
              className={`cursor-pointer shrink-0 ${DI_FOCUS_RING}`}
              data-testid="di-live-only"
              aria-pressed={liveOnly}
              onClick={() => setLiveOnly((v) => !v)}
            >
              {liveOnly ? '[x]' : '[ ]'} live only
            </button>
          </div>
        )}
        {filterMissions(missions.missions, focusMission, liveOnly).map((mission) => (
          <MissionCard
            key={mission.missionRef}
            mission={mission}
            selectedKey={selectedKey}
            onSelect={selectKey}
            zone={missionZone(
              mission.missionRef,
              officeState.areaMappings,
              officeState.getLayout().areas ?? [],
            )}
          />
        ))}
      </section>

      <details className="shrink-0 border-t-2 border-border pt-4" data-testid="di-freshness-detail">
        <summary className="cursor-pointer">Freshness detail and times</summary>
        <div className="flex flex-col gap-1 pt-2">
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
        </div>
      </details>
    </aside>
  );
}
