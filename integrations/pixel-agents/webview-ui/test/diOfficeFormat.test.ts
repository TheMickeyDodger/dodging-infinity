import assert from 'node:assert/strict';

import { test } from 'vitest';

import type { DiMember, DiObservedValue, DiOfficeState } from '../../core/src/messages.ts';
import {
  formatEpoch,
  formatListState,
  formatLive,
  formatMaybe,
  formatObserved,
  memberForCharacter,
} from '../src/components/diOfficeFormat.ts';

const STATES: DiObservedValue[] = [
  { state: 'observed', value: 'x', reason: null },
  { state: 'known_empty', value: null, reason: null },
  { state: 'not_recorded', value: null, reason: null },
  { state: 'projected_reason', value: null, reason: 'binding_disappeared:task_not_in_snapshot' },
  { state: 'not_applicable', value: null, reason: 'updated_at_not_applicable_to_workflow_store' },
  {
    state: 'unavailable',
    value: null,
    reason: 'runtime_snapshot_unavailable:capture_slot_occupied',
  },
];

test('every observation state renders distinctly and never blank', () => {
  const rendered = STATES.map(formatObserved);
  assert.equal(new Set(rendered).size, STATES.length);
  for (const text of rendered) assert.ok(text.trim().length > 0);
});

test('not_applicable is never rendered as empty/none, and unavailable is never rendered as none', () => {
  assert.doesNotMatch(formatObserved(STATES[4]), /none/);
  assert.doesNotMatch(formatObserved(STATES[5]), /none/);
  assert.match(formatObserved(STATES[1]), /none \(read succeeded\)/);
  assert.match(formatObserved(STATES[5]), /capture_slot_occupied/);
});

test('nothing unknown or missing renders as healthy, active, waiting or zero', () => {
  for (const v of STATES.slice(1)) {
    assert.doesNotMatch(formatObserved(v), /healthy|active|waiting|^0$/i);
  }
  assert.equal(formatMaybe(null), 'not reported');
  assert.equal(formatEpoch(null), 'not reported');
  assert.match(
    formatLive(false, 'binding_unverified:workspace_not_observed'),
    /^NOT LIVE: binding_unverified/,
  );
  assert.equal(formatLive(true, null), 'LIVE');
  assert.equal(formatListState('known_empty', null, 0), 'none (read succeeded)');
  assert.match(
    formatListState('unavailable', 'discovery:discovery_absent', 0),
    /^unavailable: discovery/,
  );
});

test('epoch times render as UTC with the raw value', () => {
  assert.equal(formatEpoch(1800000060), '2027-01-15T08:01:00Z (1800000060)');
});

test('the selected character resolves to its member by the explicit characterId — never by a ref', () => {
  const member = (characterId: number | null, memberRef: string): DiMember =>
    ({ characterId, memberRef }) as unknown as DiMember;
  const state = {
    missions: {
      state: 'observed',
      reason: null,
      missions: [
        {
          missionRef: 'a:1',
          members: {
            state: 'observed',
            reason: null,
            // Same logical-looking refs; only characterId decides.
            members: [member(7, 'a:1#executor1'), member(null, 'a:1#executor1')],
          },
        },
      ],
    },
  } as unknown as DiOfficeState;
  assert.equal(memberForCharacter(state, 7)?.member.characterId, 7);
  assert.equal(memberForCharacter(state, 8), null);
  assert.equal(memberForCharacter(state, null), null);
});

test('at a glance: unavailable / projected / not-applicable never style like a healthy value', async () => {
  const { observedClass, freshnessClass } = await import('../src/components/diOfficeFormat.ts');
  const healthy = observedClass(STATES[0]);
  for (const v of [STATES[2], STATES[3], STATES[4], STATES[5]])
    assert.notEqual(observedClass(v), healthy);
  assert.notEqual(observedClass(STATES[5]), observedClass(STATES[3])); // unavailable != projected
  assert.notEqual(freshnessClass(true), freshnessClass(false));
});

test('throttled / slot-occupied / wait-elapsed are NORMAL waiting states, carried with their reason, never error-toned', async () => {
  const { isNormalWaiting, formatWaiting, WAITING_CLASS, observedClass } =
    await import('../src/components/diOfficeFormat.ts');
  for (const p of [
    'catalog_refresh_throttled',
    'catalog_slot_occupied',
    'catalog_query_not_finished_within_wait',
  ])
    assert.equal(isNormalWaiting(p), true);
  for (const p of ['catalog_owner_shut_down', 'catalog_query_failed', 'catalog_bad_request', null])
    assert.equal(isNormalWaiting(p), false);
  assert.equal(
    formatWaiting('catalog_refresh_throttled', 3),
    'waiting: catalog_refresh_throttled, retry after 3 s',
  );
  assert.doesNotMatch(formatWaiting('catalog_refresh_throttled', null), /error|fail|problem/i);
  assert.notEqual(WAITING_CLASS, observedClass(STATES[5])); // not the unavailable/danger tone
  assert.doesNotMatch(WAITING_CLASS, /danger|warning/);
});

test('the live count never includes replaced/unconfirmed members', async () => {
  const { formatMemberCount } = await import('../src/components/diOfficeFormat.ts');
  assert.equal(formatMemberCount([{ live: true }, { live: false }, { live: true }]), '3 (2 live)');
  assert.equal(formatMemberCount([]), '0 (0 live)');
});

test('client invalidation: transport loss, bare reconnect and silent expiry each have their own reason', async () => {
  const { clientInvalidationReason } = await import('../src/components/diOfficeFormat.ts');
  const base = {
    transport: 'connected' as const,
    freshSinceConnect: true,
    nowSeconds: 100,
    validUntil: 160,
  };
  assert.equal(clientInvalidationReason(base), null);
  assert.equal(
    clientInvalidationReason({ ...base, transport: 'reconnecting' }),
    'transport_reconnecting',
  );
  assert.equal(
    clientInvalidationReason({ ...base, transport: 'disconnected' }),
    'transport_disconnected',
  );
  // connected is NOT live: a bare reconnect restores nothing until a new office message
  assert.equal(
    clientInvalidationReason({ ...base, freshSinceConnect: false }),
    'transport_reconnected_awaiting_office_state',
  );
  assert.equal(clientInvalidationReason({ ...base, nowSeconds: 160 }), null); // inclusive
  assert.equal(
    clientInvalidationReason({ ...base, nowSeconds: 161 }),
    'client_expired:no_office_state_before_valid_until',
  );
  assert.equal(clientInvalidationReason({ ...base, validUntil: null, nowSeconds: 10_000 }), null);
});

test('client invalidation takes liveness and animation away and infers nothing else', async () => {
  const { withClientInvalidation } = await import('../src/components/diOfficeFormat.ts');
  const member = {
    memberRef: 'a:1#executor1',
    characterId: 7,
    live: true,
    notLiveReason: null,
    animateActive: true,
    classification: 'present',
    reason: 'session_confirmed',
  };
  const state = {
    live: true,
    notLiveReason: null,
    envelope: { fresh: true, reason: null, served: 'refreshed' },
    runtime: { fresh: true, reason: null },
    times: { validUntil: 160 },
    missions: {
      state: 'observed',
      reason: null,
      missions: [
        {
          missionRef: 'a:1',
          live: true,
          notLiveReason: null,
          standing: 'active',
          members: { state: 'observed', reason: null, members: [member] },
        },
      ],
    },
  } as unknown as DiOfficeState;
  const out = withClientInvalidation(state, 'transport_reconnecting');
  assert.equal(out.live, false);
  assert.equal(out.notLiveReason, 'transport_reconnecting');
  assert.equal(out.envelope.fresh, false);
  assert.equal(out.runtime.reason, 'transport_reconnecting');
  const [mission] = out.missions.missions;
  assert.equal(mission.live, false);
  assert.equal(mission.standing, 'active'); // no lifecycle reconstructed
  const [m] = mission.members.members;
  assert.equal(m.live, false);
  assert.equal(m.animateActive, false);
  assert.equal(m.characterId, 7); // the character stays: unknown, not gone
  assert.equal(m.classification, 'present'); // backend values verbatim
  assert.equal(m.reason, 'session_confirmed');
  assert.equal(mission.members.members.length, 1);
  assert.doesNotMatch(JSON.stringify(out), /disappeared|terminal|dead|gone|ended/);
  assert.equal(state.live, true); // the received state is not mutated
});

test('the canvas label is the member activity verbatim while live, and says NOT live otherwise', async () => {
  const { characterLabel } = await import('../src/components/diOfficeFormat.ts');
  assert.equal(characterLabel({ live: true, activity: 'busy' }), 'busy');
  assert.equal(characterLabel({ live: false, activity: 'busy' }), 'not live · last reported busy');
  assert.doesNotMatch(characterLabel({ live: true, activity: 'busy' }), /Idle/);
});

// ── FINDING 2: reconnect membership reconciliation on the canonical identity tuple ──
function diState(
  members: Array<{ id: number | null; ref: string; task?: string; mission?: string }>,
): DiOfficeState {
  const byMission = new Map<string, Array<(typeof members)[number]>>();
  for (const m of members) {
    const key = m.mission ?? 'src:mn-1';
    byMission.set(key, [...(byMission.get(key) ?? []), m]);
  }
  return {
    missions: {
      state: 'observed',
      reason: null,
      missions: [...byMission.entries()].map(([missionRef, ms]) => ({
        missionRef,
        sourceId: missionRef.split(':')[0],
        members: {
          state: 'observed',
          reason: null,
          members: ms.map((m) => ({
            characterId: m.id,
            coverage: 'direct',
            memberRef: `${missionRef}#${m.ref}`,
            taskId: { state: 'observed', value: m.task ?? 'task-1', reason: null },
          })),
        },
      })),
    },
  } as unknown as DiOfficeState;
}

test('reconcile: removals, additions and unchanged members after a reconnect', async () => {
  const { reconcileDiCharacters } = await import('../src/components/diOfficeFormat.ts');
  const before = reconcileDiCharacters(
    new Map(),
    diState([
      { id: 1, ref: 'lead1' },
      { id: 2, ref: 'executor1' },
    ]),
    [1, 2], // already drawn by agentCreated
  );
  assert.deepEqual(before.add, []); // recorded only
  assert.deepEqual(before.remove, []);
  // While disconnected: executor1 left (agentClosed missed), reviewer1 joined (agentCreated missed).
  const after = reconcileDiCharacters(
    before.next,
    diState([
      { id: 1, ref: 'lead1' },
      { id: 3, ref: 'reviewer1' },
    ]),
    [1, 2],
  );
  assert.deepEqual(after.remove, [2]);
  assert.deepEqual(after.add, [{ id: 3, folderName: 'src:mn-1' }]);
  assert.deepEqual([...after.next.keys()].sort(), [1, 3]);
});

test('BLOCKER 1: an UNCONFIRMED incremental creation (drawn by agentCreated, never in any office message) participates — absent from the next message => REMOVED', async () => {
  const { reconcileDiCharacters } = await import('../src/components/diOfficeFormat.ts');
  const confirmed = reconcileDiCharacters(new Map(), diState([{ id: 1, ref: 'lead1' }]), [1]);
  // agentCreated(B=9) drew B; the connection dropped before B's office message; B was removed
  // on the server while disconnected; the next message is an UNAVAILABLE/starved answer.
  const unavailable = diState([]); // no characterIds at all
  const plan = reconcileDiCharacters(confirmed.next, unavailable, [1, 9]);
  assert.deepEqual(plan.remove.sort(), [1, 9]); // B is NOT left drawn
  assert.deepEqual(plan.add, []);
  assert.equal(plan.next.size, 0);
});

test('BLOCKER 1: an unconfirmed creation that the next message DOES list is recorded, not redrawn', async () => {
  const { reconcileDiCharacters } = await import('../src/components/diOfficeFormat.ts');
  const plan = reconcileDiCharacters(
    new Map(), // nothing confirmed yet: both 1 and 9 were drawn by agentCreated only
    diState([
      { id: 1, ref: 'lead1' },
      { id: 9, ref: 'reviewer3' },
    ]),
    [1, 9],
  );
  assert.deepEqual(plan.remove, []);
  assert.deepEqual(plan.add, []);
  assert.deepEqual([...plan.next.keys()].sort(), [1, 9]);
});

test('reconcile: a number that now names a DIFFERENT identity is removed and redrawn, never conflated', async () => {
  const { reconcileDiCharacters, diIdentityKey } =
    await import('../src/components/diOfficeFormat.ts');
  const first = reconcileDiCharacters(new Map(), diState([{ id: 7, ref: 'executor1' }]), [7]);
  const changed = diState([{ id: 7, ref: 'executor1', task: 'task-2' }]); // same number, other task
  const plan = reconcileDiCharacters(first.next, changed, [7]);
  assert.deepEqual(plan.remove, [7]);
  assert.deepEqual(plan.add, [{ id: 7, folderName: 'src:mn-1' }]);
  const mission = changed.missions.missions[0];
  assert.equal(plan.next.get(7), diIdentityKey(mission, mission.members.members[0]));
  assert.notEqual(plan.next.get(7), first.next.get(7));
});

test('reconcile composes with replaced-is-not-live: the character goes, the roster keeps the member', async () => {
  const { reconcileDiCharacters } = await import('../src/components/diOfficeFormat.ts');
  const first = reconcileDiCharacters(new Map(), diState([{ id: 4, ref: 'reviewer2' }]), [4]);
  const replaced = diState([{ id: null, ref: 'reviewer2' }]); // returned as replaced: not live
  const plan = reconcileDiCharacters(first.next, replaced, [4]);
  assert.deepEqual(plan.remove, [4]);
  assert.deepEqual(plan.add, []);
  assert.equal(replaced.missions.missions[0].members.members.length, 1); // still visible
});

test('reconcile: an unchanged identity is a no-op; a missing character for it is redrawn', async () => {
  const { reconcileDiCharacters } = await import('../src/components/diOfficeFormat.ts');
  const s = diState([{ id: 5, ref: 'supervisor' }]);
  const first = reconcileDiCharacters(new Map(), s, [5]);
  assert.deepEqual(reconcileDiCharacters(first.next, s, [5]), {
    remove: [],
    add: [],
    next: first.next,
  });
  assert.deepEqual(reconcileDiCharacters(first.next, s, []).add, [
    { id: 5, folderName: 'src:mn-1' },
  ]);
});

test('BLOCKER 1: the animation authority — an unconfirmed BRAND-NEW id never animates; invalidation stops EVERY canvas character, listed or not', async () => {
  const { diAnimationPlan, diIdentityKey, withClientInvalidation } =
    await import('../src/components/diOfficeFormat.ts');
  const presented = diState([{ id: 1, ref: 'lead1' }]);
  const m = presented.missions.missions[0];
  (m.members.members[0] as { animateActive?: boolean }).animateActive = true;
  const recorded = new Map([[1, diIdentityKey(m, m.members.members[0])]]);
  // 1 is confirmed (recorded identity matches) and busy; 9 was drawn by agentCreated, never confirmed.
  assert.deepEqual(diAnimationPlan(presented, [1, 9], recorded), [
    [1, true],
    [9, false],
  ]);
  // Before any office message (or in non-DI): nothing animates by this authority.
  assert.deepEqual(diAnimationPlan(null, [1, 9], recorded), [
    [1, false],
    [9, false],
  ]);
  // Transport loss: the presented state is invalidated — EVERY canvas character is still,
  // including 9 which the previous state never listed (the reviewer's typing-under-banner case).
  const invalidated = withClientInvalidation(
    {
      ...presented,
      live: true,
      notLiveReason: null,
      envelope: {},
      runtime: {},
    } as unknown as DiOfficeState,
    'transport_reconnecting',
  );
  assert.deepEqual(diAnimationPlan(invalidated, [1, 9], recorded), [
    [1, false],
    [9, false],
  ]);
});

// DEFENSIVE / SYNTHETIC — NOT a proven production path. In-process reuse is UNREACHABLE (identityMap.ts:
// no delete/clear/remove; monotonic `next`); the restart variant is GATED by the per-launch token
// (diCli.ts: crypto.randomUUID() per launch; diHttpServer.ts: authenticate before any listener/data).
// The property is held anyway: a shared integer must never carry animation across identities.
test("BLOCKER 1 (integer reuse, DEFENSIVE / SYNTHETIC): a REUSED id must not inherit a prior identity's animation merely by sharing its number", async () => {
  const { diAnimationPlan, diIdentityKey } = await import('../src/components/diOfficeFormat.ts');
  // Number 5 was confirmed for identity X (executor1 on task-1).
  const priorX = diState([{ id: 5, ref: 'executor1', task: 'task-1' }]);
  const mx = priorX.missions.missions[0];
  const recorded = new Map([[5, diIdentityKey(mx, mx.members.members[0])]]);
  // A presented state now lists number 5 for a DIFFERENT identity Y (reviewer9 on task-2), busy.
  const nowY = diState([{ id: 5, ref: 'reviewer9', task: 'task-2' }]);
  (nowY.missions.missions[0].members.members[0] as { animateActive?: boolean }).animateActive =
    true;
  // Canvas character 5 still carries X's recorded identity: it must NOT animate as Y.
  assert.deepEqual(diAnimationPlan(nowY, [5], recorded), [[5, false]]);
  // Only after reconciliation re-keys (and redraws) 5 as Y does Y animate.
  const my = nowY.missions.missions[0];
  const rekeyed = new Map([[5, diIdentityKey(my, my.members.members[0])]]);
  assert.deepEqual(diAnimationPlan(nowY, [5], rekeyed), [[5, true]]);
});

// ── Connection-state correctness (behaviour): both halves, each its own named case ──
type FakeState = 'connecting' | 'connected' | 'reconnecting' | 'disconnected';
function fakeTransport(initial: FakeState) {
  const handlers: Array<(s: FakeState) => void> = [];
  const log: string[] = [];
  return {
    log,
    state: initial as FakeState,
    onStateChange(handler: (s: FakeState) => void) {
      log.push('subscribed');
      handlers.push(handler);
      return () => undefined;
    },
    emit(s: FakeState) {
      this.state = s;
      for (const h of handlers) h(s);
    },
  };
}

test('(a) STALE INITIAL TRANSPORT STATE: the socket opened before the subscription attached — LIVE once a fresh response exists, never falsely transport_connecting', async () => {
  const { watchTransportState, nextDiConnection, clientInvalidationReason } =
    await import('../src/components/diOfficeFormat.ts');
  const now = { nowSeconds: 100, validUntil: 160 };
  // First render captured 'connecting'; the socket then OPENED and a fresh office message
  // arrived — all before the subscribing effect ran (the 'connected' transition is missed).
  let conn = { transport: 'connecting' as FakeState, freshSinceConnect: false };
  conn = nextDiConnection(conn, { type: 'officeMessage' });
  const t = fakeTransport('connected'); // already open; will never emit 'connected' again
  // The OLD subscribe-only pattern would leave the stale state — the bug, reproduced:
  assert.equal(clientInvalidationReason({ ...conn, ...now }), 'transport_connecting');
  // The fix: re-read synchronously, THEN subscribe.
  watchTransportState(t, (state) => {
    t.log.push(`state:${state}`);
    conn = nextDiConnection(conn, { type: 'transport', state });
  });
  assert.deepEqual(t.log, ['state:connected', 'subscribed']); // re-read happens BEFORE subscribing
  assert.equal(conn.transport, 'connected');
  assert.equal(clientInvalidationReason({ ...conn, ...now }), null); // LIVE
});

test('(b) GENUINE DISCONNECT AND RECONNECT STILL INVALIDATE: transport reason; bare reconnect restores nothing; only a new message does; expiry still stops the live claim', async () => {
  const { watchTransportState, nextDiConnection, clientInvalidationReason } =
    await import('../src/components/diOfficeFormat.ts');
  const t = fakeTransport('connected');
  let conn = { transport: 'connecting' as FakeState, freshSinceConnect: false };
  watchTransportState(t, (state) => {
    conn = nextDiConnection(conn, { type: 'transport', state });
  });
  conn = nextDiConnection(conn, { type: 'officeMessage' });
  const at = (nowSeconds: number) =>
    clientInvalidationReason({ ...conn, nowSeconds, validUntil: 160 });
  assert.equal(at(100), null); // LIVE
  t.emit('reconnecting'); // a real transition away from connected
  assert.equal(at(100), 'transport_reconnecting');
  assert.equal(conn.freshSinceConnect, false);
  t.emit('disconnected');
  assert.equal(at(100), 'transport_disconnected');
  t.emit('connected'); // BARE reconnect
  assert.equal(at(100), 'transport_reconnected_awaiting_office_state'); // still NOT live
  conn = nextDiConnection(conn, { type: 'officeMessage' }); // a NEW valid office message
  assert.equal(at(100), null); // only now LIVE again
  assert.equal(at(160), null); // inclusive boundary
  assert.equal(at(161), 'client_expired:no_office_state_before_valid_until'); // connected but silent
});

// ── Bootstrap recovery (pre-round-3): webviewReady is never replayed on reconnect ──
test('BOOTSTRAP: a normal first load does NOT duplicate the handshake (the mount-time webviewReady covers the first connection)', async () => {
  const { shouldRequestDiBootstrap, readyTrackerAfterTransport, DI_READY_TRACKER_AT_MOUNT } =
    await import('../src/components/diOfficeFormat.ts');
  let t = readyTrackerAfterTransport(DI_READY_TRACKER_AT_MOUNT, 'connecting');
  t = readyTrackerAfterTransport(t, 'connected');
  // the first snapshot arrives before layoutLoaded (layout not ready yet): no extra request
  assert.equal(shouldRequestDiBootstrap(false, t.readySent), false);
});

test('BOOTSTRAP: a first attempt that NEVER OPENED does not reset — the queued webviewReady goes out on the next open (no duplicate)', async () => {
  const { shouldRequestDiBootstrap, readyTrackerAfterTransport, DI_READY_TRACKER_AT_MOUNT } =
    await import('../src/components/diOfficeFormat.ts');
  let t = readyTrackerAfterTransport(DI_READY_TRACKER_AT_MOUNT, 'connecting');
  t = readyTrackerAfterTransport(t, 'reconnecting'); // closed before onopen: nothing was sent
  t = readyTrackerAfterTransport(t, 'connected');
  assert.equal(shouldRequestDiBootstrap(false, t.readySent), false);
});

test('BOOTSTRAP: cut after webviewReady, before layoutLoaded, then reconnect — requested ONCE because the layout is unready', async () => {
  const { shouldRequestDiBootstrap, readyTrackerAfterTransport, DI_READY_TRACKER_AT_MOUNT } =
    await import('../src/components/diOfficeFormat.ts');
  let t = readyTrackerAfterTransport(DI_READY_TRACKER_AT_MOUNT, 'connecting');
  t = readyTrackerAfterTransport(t, 'connected'); // webviewReady carried by this connection
  t = readyTrackerAfterTransport(t, 'reconnecting'); // ...which was lost before layoutLoaded
  t = readyTrackerAfterTransport(t, 'reconnecting'); // refused retries change nothing
  t = readyTrackerAfterTransport(t, 'connected');
  assert.equal(shouldRequestDiBootstrap(false, t.readySent), true); // first office message on it
  t = { ...t, readySent: true }; // requested
  assert.equal(shouldRequestDiBootstrap(false, t.readySent), false); // never twice on one connection
  // and if THAT connection is lost before layoutLoaded too, the next one asks again
  t = readyTrackerAfterTransport(t, 'reconnecting');
  t = readyTrackerAfterTransport(t, 'connected');
  assert.equal(shouldRequestDiBootstrap(false, t.readySent), true);
});

test('BOOTSTRAP: with the layout READY a reconnect requests nothing — an in-progress layout edit cannot be clobbered', async () => {
  const { shouldRequestDiBootstrap, readyTrackerAfterTransport, DI_READY_TRACKER_AT_MOUNT } =
    await import('../src/components/diOfficeFormat.ts');
  let t = readyTrackerAfterTransport(DI_READY_TRACKER_AT_MOUNT, 'connected');
  t = readyTrackerAfterTransport(t, 'reconnecting');
  t = readyTrackerAfterTransport(t, 'connected');
  assert.equal(t.readySent, false);
  assert.equal(shouldRequestDiBootstrap(true, t.readySent), false);
});

// ── P2: a drawn character no office state lists is UNCONFIRMED, never ordinary "Idle" ──
test('P2: diCharacterLabel — a failed lookup is an explicit UNCONFIRMED, not-live label with no activity', async () => {
  const { diCharacterLabel, DI_UNCONFIRMED_LABEL, diZoneLabel } =
    await import('../src/components/diOfficeFormat.ts');
  const state = {
    missions: { missions: [] },
  } as unknown as DiOfficeState;
  const label = diCharacterLabel(state, 7);
  assert.equal(label, DI_UNCONFIRMED_LABEL);
  assert.match(label, /NOT LIVE/);
  assert.match(label, /unconfirmed/);
  assert.doesNotMatch(label, /Idle|typing|reading|running/i);
  assert.equal(diZoneLabel({ nativeId: 'mn-5c491184cb8337a4', missionRef: 'x' }), 'mn-5c491184');
});

test('READABILITY: a Mission maps to its labelled office zone (Areas + areaMappings), or none', async () => {
  const { missionZone } = await import('../src/components/diOfficeFormat.ts');
  const areas = [
    { label: 'mn-aaaa', color: 'c1' },
    { label: 'mn-bbbb', color: 'c2' },
  ];
  const mappings = { A: ['mn-aaaa'], B: ['mn-bbbb'] };
  assert.deepEqual(missionZone('B', mappings, areas), { label: 'mn-bbbb', color: 'c2' });
  assert.equal(missionZone('C', mappings, areas), null); // no drawn characters -> no zone
  assert.equal(missionZone('A', mappings, []), null); // custom layout without DI zones
});

// ── contract v2 presentation helpers ──
test('v2: a child chain renders DEPTH-LIMITED with its count (or not recorded), never "no children"', async () => {
  const { formatTaskChain, formatDepthLimit } = await import('../src/components/diOfficeFormat.ts');
  const child = {
    chain: 'child',
    chainTaskId: 'T-child',
    parentTaskId: 'T-direct',
    bindingState: 'not_applicable',
    bindingReason: 'chain_membership_ended',
    rootObservation: 'observed',
    rootProblem: null,
    membershipEnded: true,
    membershipStatus: 'FAILED',
    membershipRule: 'herdr.vintage.is_terminal_status',
    dependencyRecorded: true,
    dependencyResolution: 'unsatisfied',
    dependencyRule: 'herdr.dependencies: satisfied only by status COMPLETE',
    descendantsNotFollowed: 0,
    declaredAgents: [],
  };
  const line = formatTaskChain(child);
  assert.match(line, /depth-limited \(depth 1\): 0 descendant\(s\) not followed/);
  assert.match(line, /membership ENDED \(FAILED\)/);
  assert.match(line, /dependency unsatisfied/);
  assert.doesNotMatch(line, /no children/i);
  assert.equal(formatDepthLimit(null), 'depth-limited (depth 1): descendants not recorded');
});

test('v2: an UNPARSED review gate (MISSING/MALFORMED) is a PARSE OUTCOME, never a verdict', async () => {
  const { formatReviewPreamble } = await import('../src/components/diOfficeFormat.ts');
  const base = {
    state: 'admitted',
    problem: null,
    currency: 'current',
    integrity: 'linkage_corroborated',
    taskStatus: 'ACTIVE',
    candidateApplicability: 'unproven',
    round: 3,
    reviewerLogical: 'reviewer1',
    reviewerAgent: 'agent-r',
  };
  const missing = formatReviewPreamble({
    ...base,
    gate: 'unparsed',
    verdict: null,
    protocolToken: 'MISSING',
  });
  assert.equal(missing.verdict, 'null (no verdict: the gate parsed no token)');
  assert.doesNotMatch(missing.verdict, /none|no issues|pass|fail/i);
  assert.match(missing.parseOutcome ?? '', /NOT a rejection/);
  const approve = formatReviewPreamble({
    ...base,
    gate: 'parsed',
    verdict: 'APPROVE',
    protocolToken: 'APPROVE',
  });
  assert.equal(approve.verdict, 'APPROVE');
  assert.equal(approve.gate, 'parsed');
  assert.equal(approve.parseOutcome, null);
});

test('v2: fact states stay distinct; no activation contract is not applicable; caps are honest', async () => {
  const { formatFactState, formatBoundedList, formatLinkedChildCoverage } =
    await import('../src/components/diOfficeFormat.ts');
  const fact = (state: string, reason: string | null) =>
    ({
      name: 'proof',
      state,
      reason,
      standing: null,
      source: null,
      freshness: null,
      detail: null,
      observedAt: null,
      revision: null,
      position: null,
      entries: [],
    }) as never;
  const rendered = [
    formatFactState(fact('not_applicable', 'no_activation_contract')),
    formatFactState(fact('known_empty', null)),
    formatFactState(fact('not_recorded', null)),
    formatFactState(fact('unavailable', 'x')),
  ];
  assert.equal(new Set(rendered).size, 4);
  assert.equal(rendered[0], 'not applicable: no activation contract');
  assert.doesNotMatch(rendered[0], /none/);
  assert.equal(formatBoundedList([], null), 'unknown (not a list)');
  assert.equal(formatBoundedList([], 0), 'none (observed)');
  assert.match(formatBoundedList(['a', 'b'], 5), /3 omitted by the display cap/);
  assert.deepEqual(
    formatLinkedChildCoverage({
      scope: 's',
      statement: 's',
      linkedChildRoles: 'not_covered',
      linkedChildStatement: 'x',
    }),
    { text: 'Linked child-task roles: NOT COVERED.', warning: true },
  );
  assert.equal(
    formatLinkedChildCoverage({
      scope: 's',
      statement: 's',
      linkedChildRoles: 'covered_depth_1',
      linkedChildStatement: 'covered at depth 1',
    }).warning,
    false,
  );
});

test('NAVIGATION (view only): agents and Missions in order, wrapping; nothing when none drawn', async () => {
  const { navigationOrder, navigateStep, nextMissionCharacter } =
    await import('../src/components/diOfficeFormat.ts');
  const mission = (ids: Array<number | null>) => ({
    members: { members: ids.map((characterId) => ({ characterId })) },
  });
  const state = {
    missions: { missions: [mission([1, 2]), mission([null]), mission([5])] },
  } as unknown as DiOfficeState;
  const order = navigationOrder(state);
  assert.deepEqual(order, [1, 2, 5]);
  assert.equal(navigateStep(order, 2, 1), 5);
  assert.equal(navigateStep(order, 5, 1), 1);
  assert.equal(navigateStep(order, null, -1), 5);
  assert.equal(navigateStep([], null, 1), null);
  assert.equal(nextMissionCharacter(state, 2, 1), 5); // skips the Mission with no character
  assert.equal(nextMissionCharacter(state, 5, 1), 1);
  assert.equal(nextMissionCharacter(state, null, 1), 1);
});

test('"done" is labelled AGENT/TERMINAL activity — never task or Mission completion, nothing substituted', async () => {
  const { characterLabel, formatActivity } = await import('../src/components/diOfficeFormat.ts');
  const live = characterLabel({ live: true, activity: 'done' });
  assert.match(live, /^done \(agent activity, not completion\)$/);
  assert.match(
    formatActivity('done'),
    /agent\/terminal activity; not task or Mission completion, verification or success/,
  );
  for (const text of [live, formatActivity('done')]) {
    assert.doesNotMatch(text, /idle/i); // nothing substituted
    assert.match(text, /not (task or Mission )?completion/); // explicitly NOT completion
  }
  assert.equal(formatActivity('busy'), 'busy');
});

test('READABLE: a Mission headline leads with its objective; the picker filters to one Mission or live only', async () => {
  const { missionHeadline, filterMissions } = await import('../src/components/diOfficeFormat.ts');
  const m = (ref: string, live: boolean, objective: string | null) => ({
    missionRef: ref,
    nativeId: `mn-${ref}00000000`,
    live,
    objective:
      objective === null
        ? { state: 'not_recorded', value: null, reason: null }
        : { state: 'observed', value: objective, reason: null },
  });
  assert.equal(
    missionHeadline(m('a', true, 'Ship the office') as never),
    'Ship the office — mn-a0000000',
  );
  assert.match(missionHeadline(m('b', true, null) as never), /objective not recorded/);
  const list = [m('a', true, 'x'), m('b', false, 'y'), m('c', true, 'z')];
  assert.deepEqual(
    filterMissions(list, null, false).map((x) => x.missionRef),
    ['a', 'b', 'c'],
  );
  assert.deepEqual(
    filterMissions(list, null, true).map((x) => x.missionRef),
    ['a', 'c'],
  );
  assert.deepEqual(
    filterMissions(list, 'b', true).map((x) => x.missionRef),
    ['b'],
  );
});

test('LIVE SHAPE (S5): direct and linked-child members are grouped apart; a child role is never a direct role', async () => {
  const { groupMembersByChain, formatMemberChain, characterLabel } =
    await import('../src/components/diOfficeFormat.ts');
  const m = (chain: string, chainTaskId: string | null, logical: string) => ({
    chain,
    chainTaskId,
    logical,
  });
  const groups = groupMembersByChain([
    m('child', 'T-child', 'executor1'),
    m('direct', 'T-direct', 'lead1'),
    m('direct', 'T-direct', 'executor1'),
    m('child', 'T-child', 'lead1'),
  ]);
  assert.equal(groups.length, 2);
  assert.equal(groups[0].heading, 'Direct task');
  assert.deepEqual(
    groups[0].members.map((x) => x.logical),
    ['lead1', 'executor1'],
  );
  // v2: the child roles ARE recorded Mission-associated roles, distinguished by task/chain —
  // never labelled NOT COVERED (a v1-only limitation).
  assert.match(
    groups[1].heading,
    /Linked child task T-child — recorded roles of this Mission's linked child task/,
  );
  assert.doesNotMatch(groups[1].heading, /NOT COVERED|not this Mission/);
  assert.match(
    formatMemberChain({ chain: 'child', chainTaskId: 'T-child' }),
    /a recorded role of this Mission's linked child task/,
  );
  assert.equal(
    characterLabel({ live: true, activity: 'busy', chain: 'child' }),
    'busy · linked child task',
  );
  assert.equal(characterLabel({ live: true, activity: 'busy', chain: 'direct' }), 'busy');
});

test('CANVAS TITLE (DI): compact logical · provider, never the raw Mission ref; the status literal is unchanged', async () => {
  const { characterTitle, characterLabel } = await import('../src/components/diOfficeFormat.ts');
  const missionRef = 'pixel_live_office:mn-5c491184cb8337a491d686bc8d1a0b8b';
  const title = characterTitle({ logical: 'reviewer1', provider: 'codex' });
  assert.equal(title, 'reviewer1 · codex');
  assert.ok(!title.includes(missionRef) && !title.includes('mn-'));
  // the status line is the existing literal, untouched by the title
  assert.equal(
    characterLabel({ live: true, activity: 'done' }),
    'done (agent activity, not completion)',
  );
});

test('round 15 F2: client invalidation also makes each evidence library STALE with reads closed', async () => {
  const { invalidateEvidence, withClientInvalidation } =
    await import('../src/components/diOfficeFormat.ts');
  const evidence = {
    cursor: 'c'.repeat(64),
    currency: 'current',
    currencyReason: null,
    contentReads: { state: 'available', reason: null },
    artifacts: { state: 'observed', reason: null, total: 1, items: [{ artifactId: 'mf-1' }] },
  };
  const state = {
    live: true,
    notLiveReason: null,
    envelope: { fresh: true, reason: null, served: 'refreshed' },
    runtime: { fresh: true, reason: null },
    times: { validUntil: 160 },
    missions: {
      state: 'observed',
      reason: null,
      missions: [
        {
          missionRef: 'a:1',
          live: true,
          notLiveReason: null,
          evidence,
          members: { state: 'observed', reason: null, members: [] },
        },
      ],
    },
  } as unknown as DiOfficeState;
  const out = withClientInvalidation(state, 'transport_disconnected');
  const library = out.missions.missions[0].evidence!;
  assert.equal(library.currency, 'stale');
  assert.equal(library.currencyReason, 'transport_disconnected');
  assert.deepEqual(library.contentReads, {
    state: 'unavailable',
    reason: 'transport_disconnected',
  });
  assert.equal(library.cursor, evidence.cursor); // the listing stays as last received
  assert.deepEqual(library.artifacts, evidence.artifacts);
  assert.equal(evidence.currency, 'current'); // the received state is not mutated
  assert.deepEqual(invalidateEvidence(evidence as never, 'client_expired:x').contentReads, {
    state: 'unavailable',
    reason: 'client_expired:x',
  });
});
