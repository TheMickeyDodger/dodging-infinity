/**
 * feature13: the OBSERVED STATUS derivation (diOperationalStatus.ts) — pure, over fields the office
 * state has already projected. The base data is the office state the real server projects from a
 * labelled packaged fixture (FIXTURE, not live); each case changes only the fields it is about.
 *
 * Pinned here: every status class; `unknown` whenever nothing supported reports it; the native
 * record state is never switched on; an unrecognised value never falls through into a known status;
 * a blocked terminal promotes nothing unless its member is observed, established, live and itself;
 * duplicate names and duplicate objectives stay distinct records; and every status cites its fields.
 */
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

import { test } from 'vitest';

import type { DiMission, DiOfficeState } from '../../core/src/messages.ts';
import {
  DI_OP_GLYPH,
  type DiOpStatus,
  memberTerminalNotes,
  missionObjective,
  missionStatus,
  officeStatus,
  shortMemberIdentity,
} from '../src/components/diOperationalStatus.ts';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const base = (
  JSON.parse(
    fs.readFileSync(
      path.join(root, 'test/fixtures/di-office-state-session-mismatch-v2.FIXTURE.json'),
      'utf-8',
    ),
  ) as { state: DiOfficeState }
).state;

const fresh = (): DiOfficeState => structuredClone(base);
/** The bound, active Mission Core Mission with four live, confirmed members. */
const BOUND_REF = 'mc:mn-1aa604085c36ca55d01b57634ee13f41';
const boundMission = (s: DiOfficeState): DiMission =>
  s.missions.missions.find((m) => m.missionRef === BOUND_REF)!;
const status = (s: DiOfficeState, m: DiMission) => missionStatus(m, s);

test('the fixture holds the cases these tests rely on', () => {
  const m = boundMission(base);
  assert.equal(m.standing, 'active');
  assert.equal(m.bindingState, 'bound');
  assert.equal(m.members.state, 'observed');
  assert.ok(m.members.members.length >= 2);
  assert.ok(m.members.members.every((x) => x.live && x.classification === 'present'));
});

test('running: an observed roster, an established binding and members confirmed live', () => {
  const s = fresh();
  const r = status(s, boundMission(s));
  assert.equal(r.status, 'running');
  assert.equal(r.label, 'Running');
  assert.match(r.sentence, /4 of 4 team member\(s\) observed live/);
  // It cites the exact projected fields that produced it.
  assert.ok(r.sources.includes('mission.standing = "active"'));
  assert.ok(r.sources.includes('mission.bindingState = "bound"'));
  assert.ok(r.sources.some((x) => x.includes('.live = true') && x.includes('"present"')));
});

test('every status class has its own words and its own shape (never colour alone)', () => {
  const all: DiOpStatus[] = [
    'running',
    'waiting',
    'blocked',
    'failed',
    'ended',
    'disconnected',
    'stale',
    'unknown',
  ];
  assert.equal(new Set(all.map((k) => DI_OP_GLYPH[k])).size, all.length);
});

test('the native record state is DISPLAY-ONLY: no lifecycle string changes the status', () => {
  for (const lifecycle of [
    'FAILED',
    'BLOCKED',
    'COMPLETED',
    'PAUSED',
    'NEEDS_REAUTHORIZATION',
    'x',
  ]) {
    const s = fresh();
    const m = boundMission(s);
    m.lifecycleState = lifecycle;
    const r = status(s, m);
    assert.equal(r.status, 'running', lifecycle);
    assert.ok(!r.sources.some((x) => x.startsWith('mission.lifecycleState')), lifecycle);
  }
  // Shown verbatim where it is said, never interpreted.
  const s = fresh();
  const m = boundMission(s);
  m.standing = 'inactive';
  m.lifecycleState = 'SomeNative_State';
  assert.match(status(s, m).sentence, /record state SomeNative_State/);
});

test('standing decides ended / waiting; an unrecognised standing is unknown with its raw value', () => {
  const s = fresh();
  const m = boundMission(s);
  m.standing = 'terminal';
  const ended = status(s, m);
  assert.equal(ended.status, 'ended');
  assert.match(ended.sentence, /Ended is not verified success/);
  m.standing = 'inactive';
  assert.equal(status(s, m).status, 'waiting');
  for (const raw of ['future_standing', 'Active', '']) {
    m.standing = raw;
    const r = status(s, m);
    assert.equal(r.status, 'unknown', JSON.stringify(raw));
    assert.ok(r.sentence.includes(`“${raw}”`));
  }
});

test('reported completion without success is failed (reported, not verified); verified success stays separate', () => {
  const s = fresh();
  const m = boundMission(s);
  assert.equal(m.v2.projected, true);
  const c = m.v2.missionState.completion;
  c.state = 'observed';
  c.reportedComplete = true;
  c.reportedSuccess = false;
  const failed = status(s, m);
  assert.equal(failed.status, 'failed');
  assert.match(failed.sentence, /not verified/);
  // A reported success is not verified success: an ended record says so unless verified.
  c.reportedSuccess = true;
  c.verifiedSuccess = null;
  m.standing = 'terminal';
  assert.match(status(s, m).sentence, /Ended is not verified success/);
  c.verifiedSuccess = true;
  const verified = status(s, m);
  assert.equal(verified.status, 'ended');
  assert.match(verified.sentence, /records verified success/);
  assert.ok(verified.sources.includes('v2.missionState.completion.verifiedSuccess = true'));
});

test('unknown, never guessed: roster not observed, binding not established, nobody confirmed live', () => {
  let s = fresh();
  let m = boundMission(s);
  m.members.state = 'unavailable';
  assert.equal(status(s, m).status, 'unknown');
  s = fresh();
  m = boundMission(s);
  m.bindingState = 'unverified';
  m.bindingReason = 'workspace_not_observed';
  assert.equal(status(s, m).status, 'unknown');
  s = fresh();
  m = boundMission(s);
  for (const x of m.members.members) x.live = false;
  const r = status(s, m);
  assert.equal(r.status, 'unknown');
  assert.match(r.sentence, /no team member is confirmed live/);
  // The fixture's own unverified Missions (no roster) read unknown too — never running, never 0.
  const unverified = base.missions.missions.find((x) => x.bindingState === 'unverified')!;
  assert.equal(missionStatus(unverified, base).status, 'unknown');
});

test('blocked ONLY from a confirmed, live member: every other blocked report promotes nothing', () => {
  // The true positive.
  {
    const s = fresh();
    const m = boundMission(s);
    m.members.members[0].activity = 'blocked';
    const r = status(s, m);
    assert.equal(r.status, 'blocked');
    assert.match(r.sentence, /terminal's own report/);
    assert.ok(r.sources.some((x) => x.includes('.activity = "blocked"')));
    assert.deepEqual(memberTerminalNotes(m), []);
  }
  const notBlocked: Array<[string, (m: DiMission) => void]> = [
    ['a not-live member', (m) => (m.members.members[0].live = false)],
    ['a replaced member', (m) => (m.members.members[0].classification = 'replaced')],
    ['an ambiguous member', (m) => (m.members.members[0].classification = 'ambiguous')],
    ['a disappeared member', (m) => (m.members.members[0].classification = 'disappeared')],
    ['an unrecognised classification', (m) => (m.members.members[0].classification = 'future')],
    [
      'an unverified binding (workspace_not_observed)',
      (m) => {
        m.bindingState = 'unverified';
        m.bindingReason = 'workspace_not_observed';
      },
    ],
    ['a roster that is not observed', (m) => (m.members.state = 'unavailable')],
  ];
  for (const [label, change] of notBlocked) {
    const s = fresh();
    const m = boundMission(s);
    m.members.members[0].activity = 'blocked';
    change(m);
    const r = status(s, m);
    assert.notEqual(r.status, 'blocked', label);
  }
  // The observation is not lost: it stays a qualified, last-known detail.
  const s = fresh();
  const m = boundMission(s);
  m.members.members[0].activity = 'blocked';
  m.members.members[0].live = false;
  const [note] = memberTerminalNotes(m);
  assert.match(note, /last known, not current/);
  assert.match(note, /the terminal's own report/);
});

test('terminal activity "done" is never completion: an active Mission with done terminals is not ended', () => {
  const s = fresh();
  const m = boundMission(s);
  for (const x of m.members.members) x.activity = 'done';
  const r = status(s, m);
  assert.equal(r.status, 'running');
  assert.doesNotMatch(r.sentence, /complete|ended|success/i);
});

test('an office that is not live decides first: disconnected, stale or waiting, never running', () => {
  const at = (live: boolean, reason: string | null, problem: string | null = null) => {
    const s = fresh();
    s.live = live;
    s.notLiveReason = reason;
    s.envelope.envelopeProblem = problem;
    s.envelope.fresh = live;
    return s;
  };
  assert.equal(officeStatus(at(true, null)).status, 'running');
  assert.equal(officeStatus(at(false, 'transport_reconnecting')).status, 'disconnected');
  assert.equal(
    officeStatus(at(false, 'client_expired:no_office_state_before_valid_until')).status,
    'stale',
  );
  assert.equal(
    officeStatus(at(false, 'no_response', 'catalog_refresh_throttled')).status,
    'waiting',
  );
  assert.equal(officeStatus(at(false, null)).status, 'unknown');
  // A Mission under a disconnected office is not current, whatever its own fields say.
  const s = at(false, 'transport_reconnecting');
  const r = missionStatus(boundMission(s), s);
  assert.equal(r.status, 'disconnected');
  assert.match(r.sentence, /not current/);
  const stale = at(false, 'client_expired:no_office_state_before_valid_until');
  assert.equal(missionStatus(boundMission(stale), stale).status, 'stale');
});

test('"disconnected" is claimed only for a socket that is NOT connected; a reconnected, silent one is waiting; unknown transport reasons are stale', () => {
  const at = (reason: string) => {
    const s = fresh();
    s.live = false;
    s.notLiveReason = reason;
    return s;
  };
  for (const reason of ['transport_connecting', 'transport_reconnecting', 'transport_disconnected'])
    assert.equal(officeStatus(at(reason)).status, 'disconnected', reason);
  // Connected again but no new office message yet: NOT disconnected (the socket is up).
  const awaiting = at('transport_reconnected_awaiting_office_state');
  assert.equal(officeStatus(awaiting).status, 'waiting');
  assert.match(officeStatus(awaiting).sentence, /Connected again/);
  assert.equal(missionStatus(boundMission(awaiting), awaiting).status, 'stale');
  assert.doesNotMatch(missionStatus(boundMission(awaiting), awaiting).label, /Disconnected/);
  // A transport reason this module does not recognise never claims "disconnected".
  assert.equal(officeStatus(at('transport_future_state')).status, 'stale');
  assert.equal(officeStatus(at('transport_')).status, 'stale');
});

test('the client own invalidation reason wins over a cached server-reported wait: every client reason, with and without a throttled envelope', () => {
  // A previously throttled envelope (an older answer the client can no longer vouch for) may never
  // mask the client's own reason: SERVER-SIDE FRESHNESS CANNOT INVALIDATE A DISCONNECTED BROWSER.
  const at = (reason: string, throttled: boolean) => {
    const s = fresh();
    s.live = false;
    s.notLiveReason = reason;
    s.envelope.envelopeProblem = throttled ? 'catalog_refresh_throttled' : null;
    s.envelope.retryAfterSeconds = throttled ? 3 : null;
    return s;
  };
  const expectations: Array<[string, string, RegExp]> = [
    ['transport_connecting', 'disconnected', /Disconnected from the office server/],
    ['transport_reconnecting', 'disconnected', /Disconnected from the office server/],
    ['transport_disconnected', 'disconnected', /Disconnected from the office server/],
    [
      'transport_reconnected_awaiting_office_state',
      'waiting',
      /Connected again, waiting for a new office state/,
    ],
    [
      'client_expired:no_office_state_before_valid_until',
      'stale',
      /No new office state arrived before the last one expired/,
    ],
    ['transport_future_state', 'stale', /Not live \(transport_future_state\)/],
  ];
  for (const [reason, status, sentence] of expectations) {
    for (const throttled of [true, false]) {
      const s = at(reason, throttled);
      const r = officeStatus(s);
      const label = `${reason} throttled=${throttled}`;
      assert.equal(r.status, status, label);
      assert.match(r.sentence, sentence, label);
      // ...never the backend-throttle claim, which would be a stale statement about the backend.
      assert.doesNotMatch(r.sentence, /Waiting on the backend|a normal state, not an error/, label);
      // The exact source field the client reason came from is cited.
      assert.ok(r.sources.includes(`state.notLiveReason = "${reason}"`), label);
      // ...and it reaches every Mission card: cards agree with the office, never "stale" for a lost socket.
      const card = missionStatus(boundMission(s), s);
      assert.equal(card.status, status === 'disconnected' ? 'disconnected' : 'stale', label);
    }
  }
});

test('a CONNECTED browser with a genuine server throttle still reads waiting (the backend wait is preserved)', () => {
  const s = fresh();
  s.live = false;
  s.notLiveReason = 'no_response';
  s.envelope.envelopeProblem = 'catalog_refresh_throttled';
  s.envelope.retryAfterSeconds = 3;
  const r = officeStatus(s);
  assert.equal(r.status, 'waiting');
  assert.match(
    r.sentence,
    /Waiting on the backend \(catalog_refresh_throttled, retry after 3 s\); a normal state, not an error/,
  );
  assert.ok(r.sources.includes('envelope.envelopeProblem = "catalog_refresh_throttled"'));
  assert.ok(r.sources.includes('envelope.retryAfterSeconds = 3'));
});

test('duplicate names and duplicate objectives stay distinct records with their own status', () => {
  // Two Missions in the fixture share member names (executor1, lead1, ...) and objectives.
  const withTeams = base.missions.missions.filter((m) => m.members.members.length > 0);
  assert.ok(withTeams.length >= 2);
  const names = withTeams.map((m) =>
    m.members.members
      .map((x) => x.logical)
      .sort()
      .join(),
  );
  assert.equal(new Set(names).size, 1, 'the fixture really holds duplicate names');
  // Change ONE of them: only that one changes. Nothing keys on a name.
  const s = fresh();
  const [a, b] = s.missions.missions.filter((m) => m.members.members.length > 0);
  a.members.members[0].live = false;
  for (const x of a.members.members) x.live = false;
  assert.equal(missionStatus(a, s).status, 'unknown');
  assert.equal(missionStatus(b, s).status, 'running');
  const objectives = base.missions.missions.map((m) => m.objective.value);
  assert.ok(objectives.length > new Set(objectives).size, 'duplicate objectives exist');
});

test('a header-only objective is NOT a goal: said plainly and source-qualified, nothing substituted', () => {
  const s = fresh();
  const m = boundMission(s);
  for (const header of ['OBJECTIVE', 'Objective:', '## OBJECTIVE', '  objective  ']) {
    m.objective = { state: 'observed', value: header, reason: null };
    const o = missionObjective(m);
    assert.equal(o.kind, 'no-goal', header);
    assert.match(o.text, /^No goal reported: mc exported only the section header/);
    assert.ok(o.text.includes(header.trim()));
    assert.equal(o.raw, header);
    // Status and team still carry the row: nothing about them depends on the objective.
    assert.equal(missionStatus(m, s).status, 'running');
  }
  m.objective = { state: 'observed', value: '   ', reason: null };
  assert.match(missionObjective(m).text, /exported an empty objective/);
  m.objective = { state: 'not_recorded', value: null, reason: null };
  assert.match(missionObjective(m).text, /gives no objective \(not_recorded\)/);
  // A real goal that merely STARTS with the word is a goal.
  m.objective = {
    state: 'observed',
    value: 'Objective: migrate the ingestion service',
    reason: null,
  };
  assert.deepEqual(missionObjective(m), {
    kind: 'goal',
    text: 'Objective: migrate the ingestion service',
  });
});

test('member identity in a few words stays distinct per class and recognises nothing it does not know', () => {
  const classes = ['present', 'replaced', 'disappeared', 'ambiguous', 'future_kind'];
  const words = classes.map(shortMemberIdentity);
  assert.equal(new Set(words).size, classes.length);
  assert.equal(shortMemberIdentity('future_kind'), 'identity unrecognised');
});
