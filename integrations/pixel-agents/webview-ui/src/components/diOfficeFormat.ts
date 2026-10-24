/**
 * Pure display helpers for the DI office panel. They render the server-projected
 * `diOfficeState` verbatim: no state is derived, no ref is parsed, and every
 * observation state has its own visible wording — nothing renders as blank, and
 * nothing unknown or missing renders as healthy, active or waiting. The ONE client-side
 * judgment is clientInvalidationReason / withClientInvalidation below: it can only take
 * liveness away, never grant it.
 */

import { DI_RUNTIME_ZONE_REF } from '../../../core/src/constants.js';
import type {
  DiCanonicalReview,
  DiCoverage,
  DiEvidenceLibrary,
  DiMember,
  DiMission,
  DiObservedValue,
  DiOfficeState,
  DiReportFact,
  DiRuntimeAgent,
  DiRuntimeAgents,
  DiTaskChain,
} from '../../../core/src/messages.js';
import type { TransportState } from '../../../core/src/transport.js';
import { invalidateReception } from './diReceptionFormat.js';

/** One observation as text. Each state is distinct and never empty. */
export function formatObserved(v: DiObservedValue): string {
  switch (v.state) {
    case 'observed':
      return v.value ?? '(observed, no value)';
    case 'known_empty':
      return 'none (read succeeded)';
    case 'not_recorded':
      return 'not recorded';
    case 'projected_reason':
      return `not projected: ${v.reason ?? 'reason not given'}`;
    case 'not_applicable':
      return `not applicable to this source kind`;
    case 'unavailable':
      return `unavailable: ${v.reason ?? 'reason not given'}`;
  }
}

/** An epoch-seconds value as UTC, or an explicit "not reported". */
export function formatEpoch(seconds: number | null): string {
  if (seconds === null) return 'not reported';
  return `${new Date(seconds * 1000).toISOString().replace('.000Z', 'Z')} (${seconds})`;
}

export function formatLive(live: boolean, reason: string | null, fixture = false): string {
  // FIXTURE PREVIEW (explicit --fixture-preview): LIVE never stands unqualified over fixture data.
  if (fixture)
    return live
      ? 'LIVE - FIXTURE DATA'
      : `NOT LIVE (FIXTURE DATA): ${reason ?? 'reason not given'}`;
  return live ? 'LIVE' : `NOT LIVE: ${reason ?? 'reason not given'}`;
}

/**
 * item 07: LIVE / NOT LIVE in plain words only (still qualified over fixture data): on the new
 * runtime surfaces the reason code stays behind the labelled disclosure.
 */
export function formatLivePlain(live: boolean, fixture = false): string {
  if (fixture) return live ? 'LIVE - FIXTURE DATA' : 'NOT LIVE (FIXTURE DATA)';
  return live ? 'LIVE' : 'NOT LIVE';
}

/** The persistent label of an EXPLICIT fixture preview (diOfficeState.fixtureMode). */
export const DI_FIXTURE_BANNER =
  'FIXTURE / VISUAL TEST - synthetic hash-pinned fixture data, not live Missions';

export function formatMaybe(value: string | number | boolean | null): string {
  return value === null ? 'not reported' : String(value);
}

/** The member whose office character is `characterId` — matched by the explicit id, never by a ref. */
export function memberForCharacter(
  state: DiOfficeState,
  characterId: number | null,
): { mission: DiMission; member: DiMember } | null {
  if (characterId === null) return null;
  for (const mission of state.missions.missions) {
    for (const member of mission.members.members) {
      if (member.characterId === characterId) return { mission, member };
    }
  }
  return null;
}

/**
 * item 07: the runtime agent a drawn character is, or null. A runtime agent is NEVER a team
 * member: it is looked up only after every Mission roster, and found only by its own characterId.
 */
export function runtimeAgentForCharacter(
  state: DiOfficeState,
  characterId: number | null,
): DiRuntimeAgent | null {
  if (characterId === null) return null;
  return state.runtimeAgents?.agents.find((a) => a.characterId === characterId) ?? null;
}

/** item 07: the canonical identity of a drawn runtime agent (never its character number). */
export function diRuntimeIdentityKey(
  agent: Pick<DiRuntimeAgent, 'sourceId' | 'rootRef' | 'agentRef'>,
): string {
  return JSON.stringify(['runtime', agent.sourceId, agent.rootRef, null, agent.agentRef]);
}

/**
 * item 07: the canvas label of a runtime agent — what is verified and what is not, and NEVER an
 * activity: an unassigned agent, or one whose membership is unverified, is never shown as working.
 */
export function runtimeCharacterLabel(
  agent: Pick<DiRuntimeAgent, 'live' | 'assignmentState'>,
): string {
  const what =
    agent.assignmentState === 'unassigned'
      ? 'runtime · unassigned (no current task)'
      : 'runtime · task membership not verified';
  return agent.live ? what : `not live · ${what}`;
}

/** item 07: the plain outcome line of the runtime agents block (never "none" when unknown). */
export function formatRuntimeAgentsStatement(runtime: DiRuntimeAgents | undefined): string {
  if (!runtime) return 'Runtime agents: not provided by this office.';
  if (!runtime.reported) return runtime.plainStatement;
  const omitted = runtime.agentsTotal - runtime.agents.length;
  return omitted > 0
    ? `${runtime.plainStatement} (showing ${runtime.agents.length} of ${runtime.agentsTotal}; ${omitted} omitted by the display cap)`
    : runtime.plainStatement;
}

/** A list header: observed lists show their count; other states say why there is no list. */
export function formatListState(
  state: DiObservedValue['state'],
  reason: string | null,
  count: number,
): string {
  if (state === 'observed') return `${count}`;
  return formatObserved({ state, value: null, reason });
}

/**
 * At-a-glance styling: a non-healthy state never looks like a healthy one. Returns
 * theme classes only (colors come from the CSS tokens, never inline).
 */
export function observedClass(v: DiObservedValue): string {
  switch (v.state) {
    case 'observed':
    case 'known_empty':
      return 'text-text';
    case 'not_recorded':
    case 'not_applicable':
      return 'text-text-muted italic';
    case 'projected_reason':
      return 'text-warning';
    case 'unavailable':
      return 'text-danger';
  }
}

export function freshnessClass(fresh: boolean): string {
  return fresh ? 'text-status-success' : 'text-warning';
}

/**
 * Envelope answers that are NORMAL operating states of a working backend — its refresh
 * rate limit, another query holding the one slot, the configured wait elapsing. Not live
 * (there is no fresh answer), but they carry their reason in a neutral WAITING tone and
 * never read as an error. The remaining envelope problems (owner shut down, query
 * failed, bad request) keep the warning tone. `served: "cached"` is neither a problem nor a
 * grant: cached data is live only when every check permits it (envelope, nested runtime,
 * membership).
 */
export const NORMAL_WAITING_ENVELOPE_PROBLEMS: readonly string[] = [
  'catalog_refresh_throttled',
  'catalog_slot_occupied',
  'catalog_query_not_finished_within_wait',
];

export const WAITING_CLASS = 'text-text-muted';

export function isNormalWaiting(envelopeProblem: string | null): boolean {
  return envelopeProblem !== null && NORMAL_WAITING_ENVELOPE_PROBLEMS.includes(envelopeProblem);
}

export function formatWaiting(envelopeProblem: string, retryAfterSeconds: number | null): string {
  return `waiting: ${envelopeProblem}${retryAfterSeconds === null ? '' : `, retry after ${retryAfterSeconds} s`}`;
}

/** Roster total plus the server-decided live count; replaced/unconfirmed members are never counted live. */
export function formatMemberCount(members: ReadonlyArray<{ live: boolean }>): string {
  return `${members.length} (${members.filter((m) => m.live).length} live)`;
}

/**
 * SERVER-SIDE FRESHNESS CANNOT INVALIDATE A DISCONNECTED BROWSER. Every freshness rule runs
 * on the server and reaches only a client that is still receiving; a client that is not has
 * the freshest-looking state it ever saw, frozen. This is the client's OWN invalidation —
 * the only place that lie can be caught. Returns why the client can no longer vouch for
 * the last office state, or null. Each reason is distinct from every server-reported one:
 * - `transport_<state>`: the socket is not connected (state carried verbatim);
 * - `transport_reconnected_awaiting_office_state`: connected again, but no NEW office
 *   message yet — a reconnected socket by itself restores nothing;
 * - `client_expired:no_office_state_before_valid_until`: connected but silent past the
 *   last state's `valid_until` (inclusive: `now <= valid_until` is still current).
 */
export function clientInvalidationReason(input: {
  transport: TransportState;
  /** A diOfficeState has arrived since the transport last became connected. */
  freshSinceConnect: boolean;
  nowSeconds: number;
  validUntil: number | null;
}): string | null {
  if (input.transport !== 'connected') return `transport_${input.transport}`;
  if (!input.freshSinceConnect) return 'transport_reconnected_awaiting_office_state';
  if (input.validUntil !== null && input.nowSeconds > input.validUntil)
    return 'client_expired:no_office_state_before_valid_until';
  return null;
}

/**
 * A Mission's evidence library once the client cannot vouch for it (round 15 F2): the listing
 * is STALE with the client's reason and content reads are CLOSED, so read content is hidden
 * (the panel shows content only against a current listing) and no new read can leave. The
 * listing itself stays exactly as last received.
 */
export function invalidateEvidence(evidence: DiEvidenceLibrary, reason: string): DiEvidenceLibrary {
  return {
    ...evidence,
    currency: 'stale',
    currencyReason: reason,
    contentReads: { state: 'unavailable', reason },
  };
}

/**
 * The last office state as the client may honestly show it once it can no longer vouch
 * for it: nothing is live, nothing animates, and every freshness verdict carries the
 * client's reason — the evidence library's currency and content reads included. Nothing
 * else changes — characters, identities, classifications and every backend value stay
 * exactly as last received. The agents are UNKNOWN, not gone.
 */
export function withClientInvalidation(state: DiOfficeState, reason: string): DiOfficeState {
  return {
    ...state,
    live: false,
    notLiveReason: reason,
    envelope: { ...state.envelope, fresh: false, reason },
    runtime: { ...state.runtime, fresh: false, reason },
    missions: {
      ...state.missions,
      missions: state.missions.missions.map((mission) => ({
        ...mission,
        live: false,
        notLiveReason: reason,
        ...(mission.evidence ? { evidence: invalidateEvidence(mission.evidence, reason) } : {}),
        members: {
          ...mission.members,
          members: mission.members.members.map((member) => ({
            ...member,
            live: false,
            notLiveReason: reason,
            animateActive: false,
          })),
        },
      })),
    },
    // An office state from a server without the desk carries none; there is nothing to invalidate.
    ...(state.reception ? { reception: invalidateReception(state.reception, reason) } : {}),
    ...(state.runtimeAgents
      ? {
          runtimeAgents: {
            ...state.runtimeAgents,
            agents: state.runtimeAgents.agents.map((a) => ({
              ...a,
              live: false,
              notLiveReason: reason,
            })),
          },
        }
      : {}),
  };
}

/**
 * The PRESENTED office state: the server projection, or — whenever the client cannot vouch for it
 * (clientInvalidationReason) — that projection with nothing live and nothing animating. This ONE
 * state feeds labels, animation AND the working halo (useExtensionMessages assigns it to
 * OfficeState.diOffice, the halo's only input), so the surfaces cannot disagree.
 */
export function presentDiOffice(
  server: DiOfficeState | null,
  clientReason: string | null,
): DiOfficeState | null {
  return server && clientReason ? withClientInvalidation(server, clientReason) : server;
}

/**
 * The canvas label for a DI character: the member's activity verbatim while live; once not
 * live (by the server or by the client's own invalidation) it says so and marks the
 * activity as only the last report. Never "Idle" from tool state DI does not have.
 */
/**
 * An agent's observed ACTIVITY, labelled as exactly that. `done` is the AGENT/TERMINAL
 * activity — it is NOT task completion, Mission completion, verification or success (a live
 * capture shows agents `done` while their task is plainly not complete). Nothing is
 * substituted for it.
 */
export function formatActivity(activity: string): string {
  return activity === 'done'
    ? 'done (agent/terminal activity; not task or Mission completion, verification or success)'
    : activity;
}

/** The short form, for the label above a character. */
export function formatActivityShort(activity: string): string {
  return activity === 'done' ? 'done (agent activity, not completion)' : activity;
}

export function characterLabel(
  member: Pick<DiMember, 'live' | 'activity'> & Partial<Pick<DiMember, 'chain'>>,
): string {
  const label = member.live
    ? formatActivityShort(member.activity)
    : `not live · last reported ${formatActivityShort(member.activity)}`;
  // A linked child task's role is shown as exactly that: distinguished by task/chain.
  return member.chain === 'child' ? `${label} · linked child task` : label;
}

/** Where a member sits in its Mission (contract v2): the direct task, or a linked child task. */
export function formatMemberChain(member: Pick<DiMember, 'chain' | 'chainTaskId'>): string {
  if (member.chain === 'direct') return "direct task (a recorded role of this Mission's own task)";
  if (member.chain === 'child')
    return `linked child task ${member.chainTaskId ?? '(task not recorded)'} (a recorded role of this Mission's linked child task)`;
  return `unrecognized chain: ${member.chain}`;
}

/**
 * Members grouped by chain (contract v2): the direct task first, then each linked child task
 * on its own — never one undifferentiated roster.
 */
export function groupMembersByChain<M extends Pick<DiMember, 'chain' | 'chainTaskId'>>(
  members: readonly M[],
): Array<{ heading: string; members: M[] }> {
  const groups = new Map<string, { heading: string; members: M[] }>();
  for (const m of members) {
    const key = m.chain === 'direct' ? 'direct' : `${m.chain}:${m.chainTaskId ?? ''}`;
    const heading =
      m.chain === 'direct'
        ? 'Direct task'
        : m.chain === 'child'
          ? `Linked child task ${m.chainTaskId ?? '(task not recorded)'}: recorded roles of this Mission's linked child task`
          : `Unrecognized chain ${m.chain}`;
    if (!groups.has(key)) groups.set(key, { heading, members: [] });
    groups.get(key)!.members.push(m);
  }
  const all = [...groups.entries()];
  all.sort(([a], [b]) => (a === 'direct' ? -1 : b === 'direct' ? 1 : a.localeCompare(b)));
  return all.map(([, g]) => g);
}

/**
 * The office ZONE a Mission's characters sit in (the existing Areas + areaMappings), so the
 * inspector can tie each Mission to its place on the floor. Null when it has none (no drawn
 * characters, or a custom layout without DI zones).
 */
export function missionZone(
  missionRef: string,
  mappings: Readonly<Record<string, string[]>>,
  areas: ReadonlyArray<{ label: string; color: string }>,
): { label: string; color: string } | null {
  const label = mappings[missionRef]?.[0];
  if (!label) return null;
  return areas.find((a) => a.label === label) ?? null;
}

/** Zone labels: a Mission's native id prefix (e.g. "mn-5c491184"), long enough to tell apart. */
const DI_ZONE_LABEL_MAX = 11;

/** A short, distinguishable label for a Mission's office zone (the Areas overlay). */
export function diZoneLabel(mission: Pick<DiMission, 'nativeId' | 'missionRef'>): string {
  const id = mission.nativeId || mission.missionRef;
  return id.length > DI_ZONE_LABEL_MAX ? id.slice(0, DI_ZONE_LABEL_MAX) : id;
}

/** The label of a drawn character NO office state lists (e.g. an agentCreated whose office
 *  message was lost): explicitly non-live and unconfirmed, with no activity of any kind. */
export const DI_UNCONFIRMED_LABEL = 'NOT LIVE · unconfirmed: no office state lists this character';

/**
 * The DI label of a drawn character. DI MODE is not the same as a SUCCESSFUL member lookup:
 * a failed lookup is UNCONFIRMED, never the ordinary Pixel fallback ("Idle") — stopping the
 * animation does not make "Idle" true, and the header's NOT LIVE does not make an invented
 * per-character fact truthful.
 */
export function diCharacterLabel(state: DiOfficeState, characterId: number): string {
  const found = memberForCharacter(state, characterId);
  if (found) return characterLabel(found.member);
  const runtime = runtimeAgentForCharacter(state, characterId);
  return runtime ? runtimeCharacterLabel(runtime) : DI_UNCONFIRMED_LABEL;
}

/**
 * The COMPACT canvas title of a DI member: who it is (logical name) and its provider. The full
 * canonical refs (source-qualified Mission ref, member ref, task) stay one step behind, in full,
 * in the inspector — never truncated here, simply not repeated on the canvas.
 */
export function characterTitle(member: Pick<DiMember, 'logical' | 'provider'>): string {
  return `${member.logical} · ${member.provider}`;
}

/** The compact title for a drawn character, or null when no snapshot confirms it. */
export function diCharacterTitle(state: DiOfficeState, characterId: number): string | null {
  const found = memberForCharacter(state, characterId);
  if (found) return characterTitle(found.member);
  const runtime = runtimeAgentForCharacter(state, characterId);
  return runtime ? `${runtime.logical} · ${runtime.provider ?? 'provider not recorded'}` : null;
}

/**
 * The CANONICAL identity of a drawn DI member: coverage, source, Mission, task and member
 * ref — never the character number, which is only a local rendering handle.
 */
export function diIdentityKey(
  mission: Pick<DiMission, 'sourceId' | 'missionRef'>,
  member: Pick<DiMember, 'coverage' | 'memberRef' | 'taskId'>,
): string {
  const taskId = member.taskId.state === 'observed' ? member.taskId.value : null;
  return JSON.stringify([
    member.coverage,
    mission.sourceId,
    mission.missionRef,
    taskId,
    member.memberRef,
  ]);
}

export interface DiCharacterPlan {
  /** Characters to remove: on the canvas but absent from the new message (confirmed OR never confirmed), or whose number now names a DIFFERENT identity. */
  remove: number[];
  /** Live members to draw: not on the canvas (never drawn, or missed while disconnected), or re-drawn after an identity change. */
  add: Array<{ id: number; folderName: string }>;
  /** characterId -> canonical identity, for every live member in the new message. */
  next: Map<number, string>;
}

/**
 * Reconcile EVERY DI character on the canvas with a NEW office message (round-2 blocker 1).
 * The canvas, not the identity map, is the input: a character drawn by an incremental
 * agentCreated that no office message ever confirmed participates exactly like a confirmed
 * one — absent from the message => removed. Matching is on the canonical identity tuple; a
 * character number is kept only while it still names the SAME identity. Composes with
 * replaced-is-not-live: a `replaced` member has no characterId, so its character is removed
 * while the roster keeps showing it.
 */
export function reconcileDiCharacters(
  drawn: ReadonlyMap<number, string>,
  state: DiOfficeState,
  canvasIds: Iterable<number>,
): DiCharacterPlan {
  const wanted = new Map<number, { key: string; folderName: string }>();
  for (const mission of state.missions.missions) {
    for (const member of mission.members.members) {
      if (member.characterId === null) continue;
      wanted.set(member.characterId, {
        key: diIdentityKey(mission, member),
        folderName: mission.missionRef,
      });
    }
  }
  // item 07: verified runtime agents are characters too, seated in their own zone (never a
  // Mission's), keyed by their own canonical identity.
  for (const agent of state.runtimeAgents?.agents ?? []) {
    if (agent.characterId === null || wanted.has(agent.characterId)) continue;
    wanted.set(agent.characterId, {
      key: diRuntimeIdentityKey(agent),
      folderName: DI_RUNTIME_ZONE_REF,
    });
  }
  const onCanvas = new Set(canvasIds);
  const remove: number[] = [];
  for (const id of onCanvas) {
    const w = wanted.get(id);
    const recorded = drawn.get(id);
    if (!w || (recorded !== undefined && recorded !== w.key)) remove.push(id);
  }
  const add: DiCharacterPlan['add'] = [];
  const next = new Map<number, string>();
  for (const [id, w] of wanted) {
    if (remove.includes(id) || !onCanvas.has(id)) add.push({ id, folderName: w.folderName });
    next.set(id, w.key);
  }
  return { remove, add, next };
}

/**
 * The ONLY animation authority in DI mode. A canvas character animates exactly when the
 * PRESENTED office state (after the client's own invalidation) lists a member with that
 * characterId and animateActive AND the character's RECORDED canonical identity is that
 * member's identity. The number alone never carries animation: an unconfirmed incremental
 * creation (no recorded identity), a number recorded for a DIFFERENT identity, a character
 * missing from the state, and every character while the client cannot vouch are all still.
 * Returns [id, animate] per canvas id.
 */
export function diAnimationPlan(
  presented: DiOfficeState | null,
  canvasIds: Iterable<number>,
  recorded: ReadonlyMap<number, string>,
): Array<[number, boolean]> {
  const animate = new Map<number, string>(); // characterId -> identity of a member allowed to animate
  for (const mission of presented?.missions.missions ?? []) {
    for (const member of mission.members.members) {
      if (member.characterId !== null && member.animateActive)
        animate.set(member.characterId, diIdentityKey(mission, member));
    }
  }
  return [...canvasIds].map((id) => {
    const key = animate.get(id);
    return [id, key !== undefined && recorded.get(id) === key];
  });
}

/** The client's view of its connection, for its OWN invalidation (BEHAVIOUR, not presentation). */
export interface DiConnection {
  transport: TransportState;
  /** A diOfficeState arrived since the transport last became connected. */
  freshSinceConnect: boolean;
}

export type DiConnectionEvent =
  { type: 'transport'; state: TransportState } | { type: 'officeMessage' };

/**
 * The connection transition rule. Any state other than `connected` drops freshness; a
 * (bare) reconnect to `connected` restores NOTHING; only a NEW office message does.
 */
export function nextDiConnection(prev: DiConnection, event: DiConnectionEvent): DiConnection {
  if (event.type === 'officeMessage') return { ...prev, freshSinceConnect: true };
  return {
    transport: event.state,
    freshSinceConnect: event.state === 'connected' ? prev.freshSinceConnect : false,
  };
}

/**
 * Report the transport's CURRENT state, then follow its changes. The re-read and the
 * subscription are adjacent SYNCHRONOUS calls with nothing asynchronous between them, so
 * a transition that happened before this call (the socket opening between the first
 * render and the subscribing effect) cannot be missed. Introducing anything asynchronous
 * between the two lines reopens that gap (a stale 'connecting' -> office falsely NOT LIVE).
 */
export function watchTransportState(
  transport: {
    readonly state: TransportState;
    onStateChange(handler: (state: TransportState) => void): () => void;
  },
  onState: (state: TransportState) => void,
): () => void {
  onState(transport.state);
  return transport.onStateChange(onState);
}

/**
 * DI bootstrap recovery (pre-round-3 fix). webviewReady is sent once at mount and is NOT
 * replayed on reconnect; layoutLoaded only comes in its response. A connection lost after
 * webviewReady but before layoutLoaded would leave the layout missing forever while fresh
 * office messages keep arriving. So, keyed on the UNREADY condition: request the bootstrap
 * (webviewReady) when an office message arrives on a connection that has NOT yet carried a
 * webviewReady and the layout is still not ready. With the layout ready nothing is requested,
 * so a reconnect can never clobber an in-progress layout edit.
 */
export function shouldRequestDiBootstrap(
  layoutReady: boolean,
  readySentOnThisConnection: boolean,
): boolean {
  return !layoutReady && !readySentOnThisConnection;
}

/** Per-connection bookkeeping for the bootstrap request. */
export interface DiReadyTracker {
  /** Has the CURRENT (or next-to-open) connection carried a webviewReady? */
  readySent: boolean;
  /** The last transport state seen (null before the first). */
  transport: TransportState | null;
}

/** At mount: the mount-time webviewReady is sent, or queued for the first open connection. */
export const DI_READY_TRACKER_AT_MOUNT: DiReadyTracker = { readySent: true, transport: null };

/**
 * Only losing a connection that was OPEN resets the bookkeeping: whatever it carried is gone
 * and the transport never replays it. An attempt that never opened did not consume anything —
 * the transport keeps its send queue across failed attempts and flushes it on the next open —
 * so resetting there would request a DUPLICATE handshake.
 */
export function readyTrackerAfterTransport(
  prev: DiReadyTracker,
  state: TransportState,
): DiReadyTracker {
  const lostAnOpenConnection = prev.transport === 'connected' && state !== 'connected';
  return { readySent: lostAnOpenConnection ? false : prev.readySent, transport: state };
}

// ── contract v2 presentation (pure; the server projected every fact) ─────────────────────

/** Linked-child coverage as PROJECTED (v1: not covered; v2: covered at depth 1). */
export function formatLinkedChildCoverage(coverage: DiCoverage): {
  text: string;
  warning: boolean;
} {
  return coverage.linkedChildRoles === 'not_covered'
    ? { text: 'Linked child-task roles: NOT COVERED.', warning: true }
    : { text: coverage.linkedChildStatement, warning: false };
}

/**
 * A task chain, one line. A CHILD chain's own children are never followed: rendered as
 * DEPTH-LIMITED with the count (or "not recorded"), never as "no children" or an empty set.
 */
export function formatTaskChain(chain: DiTaskChain): string {
  const task = chain.chainTaskId ?? 'task not recorded';
  const root = chain.rootProblem
    ? `${chain.rootObservation} (${chain.rootProblem})`
    : chain.rootObservation;
  const membership =
    chain.membershipEnded === null
      ? 'membership not determined'
      : chain.membershipEnded
        ? `membership ENDED (${chain.membershipStatus ?? 'status not recorded'})`
        : `membership current (${chain.membershipStatus ?? 'status not recorded'})`;
  const parts = [
    `${chain.chain} · ${task}`,
    `binding ${chain.bindingState} (${chain.bindingReason})`,
    `root ${root}`,
    membership,
  ];
  if (chain.dependencyResolution !== null) parts.push(`dependency ${chain.dependencyResolution}`);
  if (chain.chain === 'child') parts.push(formatDepthLimit(chain.descendantsNotFollowed));
  return parts.join(' · ');
}

export function formatDepthLimit(descendantsNotFollowed: number | null): string {
  return descendantsNotFollowed === null
    ? 'depth-limited (depth 1): descendants not recorded'
    : `depth-limited (depth 1): ${descendantsNotFollowed} descendant(s) not followed`;
}

/**
 * The ADMITTED canonical review as READ-ONLY preamble context: the gate (parsed | unparsed)
 * and the verdict (APPROVE | REJECT | none) are separate facts. An UNPARSED gate (token
 * MISSING, task MALFORMED) is a PARSE OUTCOME — never a rejection, never a verdict.
 */
export function formatReviewPreamble(review: DiCanonicalReview): {
  admitted: boolean;
  gate: string;
  verdict: string;
  parseOutcome: string | null;
} {
  const admitted = review.state === 'admitted';
  const unparsed = review.gate === 'unparsed' || review.protocolToken === 'MISSING';
  return {
    admitted,
    gate: review.gate ?? 'not recorded',
    // A NULL verdict is shown as null: never "none", never "no issues", never a quality call.
    verdict: unparsed
      ? 'null (no verdict: the gate parsed no token)'
      : review.verdict === null
        ? 'null'
        : review.verdict,
    parseOutcome: unparsed
      ? 'no valid token was parsed (MISSING / MALFORMED): NOT a rejection and NOT a review result about quality'
      : null,
  };
}

/** One report fact's state, distinct for null / empty / unavailable / not applicable. */
export function formatFactState(fact: DiReportFact): string {
  switch (fact.state) {
    case 'observed':
      return 'observed';
    case 'known_empty':
      return 'empty (observed, none)';
    case 'not_recorded':
      return 'not recorded (null)';
    case 'not_applicable':
      return fact.reason === 'no_activation_contract'
        ? 'not applicable: no activation contract'
        : `not applicable (${fact.reason ?? 'no reason'})`;
    case 'unavailable':
      return `unavailable (${fact.reason ?? 'no reason'})`;
    case 'projected_reason':
      return `projected: ${fact.reason ?? 'no reason'}`;
  }
}

/**
 * NAVIGATION order: every drawn (live) character, Mission by Mission in office-state order.
 * View-only — it moves the selection and camera, never anything else.
 */
export function navigationOrder(state: DiOfficeState): number[] {
  const ids: number[] = [];
  for (const mission of state.missions.missions)
    for (const member of mission.members.members)
      if (member.characterId !== null) ids.push(member.characterId);
  // item 07: drawn runtime agents after every Mission roster (never inside one).
  for (const agent of state.runtimeAgents?.agents ?? [])
    if (agent.characterId !== null && !ids.includes(agent.characterId)) ids.push(agent.characterId);
  return ids;
}

/** The next (or previous) navigable character after `current` (wraps; null when none). */
export function navigateStep(order: number[], current: number | null, step: 1 | -1): number | null {
  if (order.length === 0) return null;
  const at = current === null ? -1 : order.indexOf(current);
  if (at < 0) return step === 1 ? order[0] : order[order.length - 1];
  return order[(at + step + order.length) % order.length];
}

/** The first drawn character of the next Mission that has one (Mission-level navigation). */
export function nextMissionCharacter(
  state: DiOfficeState,
  current: number | null,
  step: 1 | -1,
): number | null {
  const firsts: number[] = [];
  let currentMission = -1;
  state.missions.missions.forEach((mission) => {
    const drawn = mission.members.members.filter((m) => m.characterId !== null);
    if (drawn.length === 0) return;
    if (drawn.some((m) => m.characterId === current)) currentMission = firsts.length;
    firsts.push(drawn[0].characterId!);
  });
  if (firsts.length === 0) return null;
  if (currentMission < 0) return step === 1 ? firsts[0] : firsts[firsts.length - 1];
  return firsts[(currentMission + step + firsts.length) % firsts.length];
}

/** A projected capped list: "unknown" when not a list, its real total, and what was omitted. */
export function formatBoundedList(items: string[], total: number | null): string {
  if (total === null) return 'unknown (not a list)';
  if (total === 0) return 'none (observed)';
  const omitted = total - items.length;
  return omitted > 0
    ? `${total}: ${items.join(', ')} … (${omitted} omitted by the display cap)`
    : `${total}: ${items.join(', ')}`;
}

const HEADLINE_MAX = 56;

/** A readable one-line label for a Mission (its objective first), for pickers and headings. */
export function missionHeadline(
  mission: Pick<DiMission, 'objective' | 'nativeId' | 'missionRef'>,
): string {
  const objective =
    mission.objective.state === 'observed' && mission.objective.value
      ? mission.objective.value
      : null;
  const id = diZoneLabel(mission);
  if (!objective) return `Mission ${id} (objective not recorded)`;
  const short =
    objective.length > HEADLINE_MAX ? `${objective.slice(0, HEADLINE_MAX - 1)}…` : objective;
  return `${short} (${id})`;
}

/** The Missions the panel lists: one picked Mission, or all (optionally live only). */
export function filterMissions<M extends { missionRef: string; live: boolean }>(
  missions: readonly M[],
  focus: string | null,
  liveOnly: boolean,
): M[] {
  if (focus !== null) return missions.filter((m) => m.missionRef === focus);
  return liveOnly ? missions.filter((m) => m.live) : [...missions];
}
