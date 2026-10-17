import { createContext, useContext, useEffect, useRef, useState } from 'react';

import type {
  DiMember,
  DiMission,
  DiMissionV2,
  DiObservedValue,
  DiOfficeState,
  DiReportFact,
} from '../../../core/src/messages.js';
import { DI_PANEL_WIDTH_PX, DI_SELECTION_POLL_MS } from '../constants.js';
import type { OfficeState } from '../office/engine/officeState.js';
import {
  DI_UNCONFIRMED_LABEL,
  filterMissions,
  formatActivity,
  formatBoundedList,
  formatEpoch,
  formatFactState,
  formatLinkedChildCoverage,
  formatListState,
  formatLive,
  formatMaybe,
  formatMemberChain,
  formatMemberCount,
  formatObserved,
  formatReviewPreamble,
  formatTaskChain,
  formatWaiting,
  freshnessClass,
  groupMembersByChain,
  isNormalWaiting,
  memberForCharacter,
  missionHeadline,
  missionZone,
  navigateStep,
  navigationOrder,
  nextMissionCharacter,
  observedClass,
  WAITING_CLASS,
} from './diOfficeFormat.js';

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

/** The selected character's canonical identity, with its own Mission's identity. */
function SelectedAgent({ member, mission }: { member: DiMember; mission: DiMission }) {
  return (
    <div className="flex flex-col gap-2">
      {/* Plain English first; the complete canonical refs one step behind (never shortened). */}
      <div className="text-sm">
        {member.role} · {member.logical}
      </div>
      <Row label="agent / provider">
        {member.agent} / {member.provider}
      </Row>
      {mission.v2.projected && <Row label="chain">{formatMemberChain(member)}</Row>}
      <Row label="Mission objective">
        <span className="line-clamp-3">
          <Observed value={mission.objective} />
        </span>
      </Row>
      {/* Verbatim from the projection: a literal "unknown" reads unknown, never a guess. */}
      <Row label="running model">
        <Observed value={member.model} />
      </Row>
      <Row label="classification">
        {member.classification} ({member.reason})
      </Row>
      <Row label="activity">{formatActivity(member.activity)}</Row>
      <LiveBadge live={member.live} reason={member.notLiveReason} />
      <details open data-testid="di-canonical-identity">
        <summary className="cursor-pointer text-text-muted">
          Canonical identity (complete refs)
        </summary>
        <div className="flex flex-col gap-2 pt-2">
          <Row label="member_ref">{member.memberRef}</Row>
          <Row label="mission_ref">{mission.missionRef}</Row>
          <Row label="task_id">
            <Observed value={member.taskId} />
          </Row>
          <Row label="producer (declared)">{mission.producerDeclared}</Row>
          <Row label="lifecycle model">{mission.lifecycleModel}</Row>
          <Row label="standing / state">
            {mission.standing} / {mission.lifecycleState}
          </Row>
        </div>
      </details>
      <details>
        <summary className="cursor-pointer text-text-muted">
          Evidence (identity fields, never liveness)
        </summary>
        <div className="flex flex-col gap-2 pt-2">
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
          <Row label="cost">
            <Observed value={member.cost} />
          </Row>
          <Row label="tokens / progress">
            <Observed value={member.tokens} /> / <Observed value={member.progress} />
          </Row>
        </div>
      </details>
    </div>
  );
}

/** One compact Mission row: full source-qualified identity, objective clamped to two lines. */
function MissionCard({
  mission,
  zone,
  selectedId,
  onNavigate,
}: {
  mission: DiMission;
  zone: { label: string; color: string } | null;
  selectedId: number | null;
  onNavigate: (id: number | null) => void;
}) {
  const holdsSelection = mission.members.members.some(
    (m) => m.characterId !== null && m.characterId === selectedId,
  );
  const cardRef = useRef<HTMLDivElement>(null);
  // The selected agent's Mission is brought into view: which Mission an agent belongs to.
  useEffect(() => {
    if (holdsSelection) cardRef.current?.scrollIntoView({ block: 'nearest' });
  }, [holdsSelection]);
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
      <details>
        <summary className="cursor-pointer text-text-muted">canonical ref</summary>
        <div className="break-all">{mission.missionRef}</div>
      </details>
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
      <LiveBadge live={mission.live} reason={mission.notLiveReason} />
      <div className="text-text-muted break-all">
        {mission.producerDeclared} · {mission.lifecycleModel} · {mission.standing} /{' '}
        {mission.lifecycleState} · binding {mission.bindingState} ({mission.bindingReason})
      </div>
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
      {(mission.v2.projected
        ? groupMembersByChain(mission.members.members)
        : [{ heading: null as string | null, members: mission.members.members }]
      ).map((group, gi) => (
        <div key={gi} className="flex flex-col gap-1" data-testid="di-member-group">
          {group.heading !== null && (
            <div className="pl-4 text-text-muted break-all">
              {group.heading} ({group.members.length})
            </div>
          )}
          {group.members.map((member) => (
            <div
              key={member.memberRef}
              className={`pl-8 break-all ${member.characterId !== null ? 'cursor-pointer' : ''}`}
              data-testid="di-member-row"
              // View-only navigation to this agent's character (members without one are not drawn).
              onClick={
                member.characterId !== null ? () => onNavigate(member.characterId) : undefined
              }
              title={
                member.characterId !== null
                  ? 'Show this agent in the office (view only)'
                  : 'Not drawn: not live'
              }
            >
              {member.logical} ({member.role})
              {mission.v2.projected && (
                <span className="text-text-muted">
                  {' '}
                  · {member.chain}
                  {member.chain === 'child' && member.chainTaskId ? ` ${member.chainTaskId}` : ''}
                </span>
              )}{' '}
              · {member.classification} ({member.reason}) · {formatActivity(member.activity)} ·{' '}
              <LiveBadge live={member.live} reason={member.notLiveReason} />
              {!member.live && (
                <div className="pl-8 text-text-muted">
                  session recorded <Observed value={member.sessionRecorded} /> · observed{' '}
                  <Observed value={member.sessionLive} />
                </div>
              )}
            </div>
          ))}
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

  const selected = memberForCharacter(state, selectedId);
  /** The Mission picker (one Mission, or all) and the live-only filter: inspector only. */
  const [focusMission, setFocusMission] = useState<string | null>(null);
  const [liveOnly, setLiveOnly] = useState(false);
  /** VIEW-ONLY navigation: moves the office selection and camera, nothing else. */
  const navigateTo = (id: number | null) => {
    if (id === null || !officeState.characters.has(id)) return;
    officeState.selectedAgentId = id;
    officeState.cameraFollowId = id;
    setSelectedId(id);
  };
  const { envelope, runtime, times, coverage, missions } = state;
  /** A normal operating state (e.g. catalog_refresh_throttled): neutral tone, never an error. */
  const waiting = isNormalWaiting(envelope.envelopeProblem) ? envelope.envelopeProblem : null;

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
          <div className="text-sm">Selected agent</div>
          <div
            className="flex gap-4 text-text-muted"
            data-testid="di-navigation"
            title="View only: moves the selection and camera; it controls nothing"
          >
            <button
              type="button"
              className="cursor-pointer"
              onClick={() => navigateTo(nextMissionCharacter(state, selectedId, -1))}
            >
              ◂ Mission
            </button>
            <button
              type="button"
              className="cursor-pointer"
              onClick={() => navigateTo(navigateStep(navigationOrder(state), selectedId, -1))}
            >
              ◂ agent
            </button>
            <button
              type="button"
              className="cursor-pointer"
              onClick={() => navigateTo(navigateStep(navigationOrder(state), selectedId, 1))}
            >
              agent ▸
            </button>
            <button
              type="button"
              className="cursor-pointer"
              onClick={() => navigateTo(nextMissionCharacter(state, selectedId, 1))}
            >
              Mission ▸
            </button>
          </div>
        </div>
        {selected ? (
          <SelectedAgent member={selected.member} mission={selected.mission} />
        ) : selectedId !== null ? (
          // A drawn character NO office state lists: say exactly that, invent nothing.
          <div className="text-warning" data-testid="di-selected-unconfirmed">
            {DI_UNCONFIRMED_LABEL}. No canonical identity or activity is shown for it.
          </div>
        ) : (
          <div className="text-text-muted">
            Click a character in the office to see its canonical identity.
          </div>
        )}
      </section>

      <section
        // min-h-160: the bounded Missions scroll keeps a usable height instead of collapsing to
        // a sliver when the sections above grow (measured 4 px before, R6 state).
        className="flex flex-col gap-4 min-h-160 flex-1 overflow-y-auto border-t-2 border-border pt-4"
        data-testid="di-missions"
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
              className="flex-1 min-w-0 text-xs py-2 px-4 bg-bg border-2 border-border rounded-none text-text"
              data-testid="di-mission-select"
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
              className="cursor-pointer shrink-0"
              data-testid="di-live-only"
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
            selectedId={selectedId}
            onNavigate={navigateTo}
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
