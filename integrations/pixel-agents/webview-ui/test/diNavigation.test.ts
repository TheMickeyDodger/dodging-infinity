/**
 * C1 (items 08/09, with 12): the navigation model in isolation — every record stays its own
 * source-qualified record, find outcomes are truthful (none / one / several different records /
 * capped), and every distinct state keeps a distinct plain phrase with no raw code in it.
 * The data is the office state the real server projects from a labelled packaged fixture.
 */
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

import { test } from 'vitest';

import type { DiMission, DiOfficeState } from '../../core/src/messages.ts';
import {
  DI_FIND_MAX,
  diRecords,
  findRecords,
  plainBinding,
  plainIdentityReason,
  plainIdentityState,
  plainMemberIdentity,
  plainMissionOutcomes,
  plainStanding,
  plainUnreported,
  recordByKey,
  recordForCharacter,
} from '../src/components/diNavigation.ts';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const state = (
  JSON.parse(
    fs.readFileSync(
      path.join(root, 'test/fixtures/di-office-state-session-mismatch-v2.FIXTURE.json'),
      'utf-8',
    ),
  ) as { state: DiOfficeState }
).state;

const distinct = (values: string[]) => new Set(values).size === values.length;
const noRawCode = (phrase: string) => !/[a-z]+_[a-z]+/.test(phrase);

test('every binding, membership, identity and standing state keeps a DISTINCT plain phrase, no raw code', () => {
  const bindings = ['bound', 'unbound', 'disappeared', 'ambiguous', 'unverified', 'not_applicable'];
  // Round 11: the binding now carries its canonical reason (one known reason for every state here).
  const phrases = bindings.map((b) => plainBinding(b, 'binding_not_confirmed_by_both_passes'));
  assert.ok(distinct(phrases));
  for (const p of phrases) assert.ok(noRawCode(p), p);
  // An unrecognised state is carried verbatim, never folded into a known phrase.
  assert.match(plainBinding('some_future_state', 'mission_not_active'), /some_future_state/);
  const classes = ['present', 'replaced', 'disappeared', 'ambiguous', 'future_kind'];
  assert.ok(distinct(classes.map(plainMemberIdentity)));
  for (const c of classes) assert.ok(noRawCode(plainMemberIdentity(c)));
  const identities = ['verified', 'unverified', 'ambiguous', 'unavailable', 'future'];
  assert.ok(distinct(identities.map(plainIdentityState)));
  const standings = ['active', 'inactive', 'terminal', 'unknown_future'];
  assert.ok(distinct(standings.map((x) => plainStanding(x, 'ACTIVE'))));
  // "ended" is said NOT to be verified success.
  assert.match(plainStanding('terminal', 'COMPLETE'), /not verified success/);
});

test('round 11 (F3): a binding never asserts an absence the record cannot support; it carries its canonical reason', () => {
  // unbound: the record itself supports no lookup; unverified: not observed / not confirmed.
  const unbound = [
    'run_not_started',
    'run_outcome_unknown',
    'task_identity_unresolved',
    'workspace_unrecorded',
  ];
  const unverified = [
    'runtime_snapshot_not_fresh',
    'workspace_not_observed',
    'binding_not_confirmed_by_both_passes',
    'canonical_source_not_fresh',
    'task_record_not_corroborated_by_a_live_session',
  ];
  const absence = /no live agent|was found|not found|no longer live|its agents are/;
  for (const r of unbound) {
    const phrase = plainBinding('unbound', r);
    assert.doesNotMatch(phrase, absence, phrase);
    assert.match(phrase, /not bound/);
  }
  for (const r of unverified) {
    const phrase = plainBinding('unverified', r);
    assert.doesNotMatch(phrase, absence, phrase);
    assert.match(phrase, /not verified/);
  }
  // disappeared: only what the last runtime snapshot showed — never that agents stopped living.
  const gone = plainBinding('disappeared', 'task_not_in_snapshot');
  assert.match(gone, /not listed in the last runtime snapshot/);
  assert.doesNotMatch(gone, absence);
  // bound: the task was matched; members are still checked one by one.
  assert.doesNotMatch(
    plainBinding('bound', 'canonical_chain_complete'),
    /live agents are confirmed/,
  );
  // Every canonical reason keeps its own phrase; an unrecognised one is carried verbatim.
  assert.ok(
    distinct([
      ...unbound.map((r) => plainBinding('unbound', r)),
      ...unverified.map((r) => plainBinding('unverified', r)),
    ]),
  );
  assert.match(plainBinding('unbound', 'a_future_reason'), /a_future_reason/);
  // Membership and runtime identity speak of the LAST observation, not of present liveness.
  for (const c of ['present', 'replaced', 'disappeared', 'ambiguous'])
    assert.match(plainMemberIdentity(c), /last observation|last live listing/, c);
  assert.doesNotMatch(plainIdentityReason('session_replaced'), /\bruns\b/);
});

test('every identity reason the reader emits has its own plain phrase; unknown ones point at provenance', () => {
  const reasons = [
    'session_replaced',
    'native_identity_degraded:identity_stable_identity_mismatch',
    'role_binding_incomplete',
    'role_not_read_in_first_source_pass',
    'role_records_changed_between_source_passes',
    'stable_records_disagree',
    'agent_name_duplicated_in_listing',
    'live_session_unknown',
    'live_session_shared_by_agents',
    'stable_identity_not_requested_within_bound',
    'stable_identity_not_observed',
    'team_binding_ambiguous',
    'identity_recorded_by_multiple_roots',
  ];
  const phrases = reasons.map(plainIdentityReason);
  assert.ok(distinct(phrases));
  for (const p of phrases) assert.ok(noRawCode(p), p);
  assert.match(plainIdentityReason('session_replaced'), /recorded session was replaced/);
  assert.match(plainIdentityReason('a_new_reason'), /provenance/);
});

test('records: every Mission, member and runtime record once, each with its own stable key', () => {
  const records = diRecords(state);
  const members = state.missions.missions.reduce((n, m) => n + m.members.members.length, 0);
  assert.equal(
    records.length,
    state.missions.missions.length + members + state.runtimeAgents!.agents.length,
  );
  assert.ok(distinct(records.map((r) => r.key)));
  const unverified = records.filter((r) => r.target.kind === 'unverified');
  assert.equal(unverified.length, 8);
  for (const r of unverified) {
    assert.equal(r.characterId, null); // never drawn
    assert.match(r.detail, /not established/);
  }
  // A drawn character resolves to its own record; the key resolves it again after a reorder.
  const drawn = records.find((r) => r.target.kind === 'runtime' && r.characterId !== null)!;
  assert.equal(recordForCharacter(state, drawn.characterId)!.key, drawn.key);
  const reordered = structuredClone(state);
  reordered.runtimeAgents!.agents.reverse();
  reordered.missions.missions.reverse();
  assert.equal(recordByKey(reordered, drawn.key)!.characterId, drawn.characterId);
  assert.equal(recordByKey(reordered, 'runtime:no-such-ref'), null);
});

test('records with ONE name from different roots read differently: each names its root', () => {
  const runtime = diRecords(state).filter(
    (r) => r.target.kind === 'runtime' || r.target.kind === 'unverified',
  );
  // act00 and act01 both record w5n-executor1: two records, two visibly different titles.
  const named = runtime.filter((r) => r.title.startsWith('executor1 · w5n-executor1'));
  assert.equal(named.length, 2);
  assert.ok(distinct(named.map((r) => r.title)));
  assert.ok(distinct(runtime.map((r) => r.title)));
});

test('find: blank is no search; no match, one match and several DIFFERENT records are each said plainly', () => {
  assert.equal(findRecords(state, '   '), null);
  const none = findRecords(state, 'zz-no-such-record-zz')!;
  assert.equal(none.total, 0);
  assert.match(none.statement, /No Mission or agent matches/);
  const member = state.missions.missions.find((m) => m.members.members.length > 0)!.members
    .members[0];
  const one = findRecords(state, member.memberRef)!;
  assert.equal(one.total, 1);
  assert.equal(one.ambiguous, false);
  assert.equal(one.statement, `1 record matches “${member.memberRef}”.`);
  // One name, many records: team members, verified runtime agents and unverified records with
  // the same agent name are listed SEPARATELY, never merged.
  const many = findRecords(state, member.agent)!;
  assert.ok(many.total > 1);
  assert.equal(many.ambiguous, true);
  assert.match(many.statement, /different records/);
  assert.ok(distinct(many.results.map((r) => r.key)));
  const kinds = new Set(many.results.map((r) => r.target.kind));
  assert.ok(kinds.has('member'));
  assert.ok(kinds.has('unverified'));
});

test('find: two Missions with ONE objective from two sources stay two results; the cap is said', () => {
  const twin = structuredClone(state);
  // One objective, two sources (unique to this pair, so the display cap is not involved).
  const original = twin.missions.missions[0];
  const objective = 'C1 twin objective recorded by two sources';
  original.objective = { state: 'observed', value: objective, reason: null };
  const copy: DiMission = {
    ...structuredClone(original),
    missionRef: `other:${original.nativeId}@ffffffffffff`,
    sourceId: 'other',
  };
  twin.missions.missions.push(copy);
  const found = findRecords(twin, objective)!;
  const missions = found.results.filter((r) => r.target.kind === 'mission');
  assert.equal(missions.length, 2);
  assert.deepEqual(missions.map((r) => r.sourceId).sort(), ['herd', 'other']);
  assert.notEqual(missions[0].key, missions[1].key);
  assert.match(found.statement, /2 sources/);
  // Everything matches "herd": more than the cap, and the statement says how many more.
  const capped = findRecords(state, 'herd')!;
  assert.ok(capped.total > DI_FIND_MAX);
  assert.equal(capped.results.length, DI_FIND_MAX);
  assert.match(
    capped.statement,
    new RegExp(`Showing the first ${DI_FIND_MAX}; ${capped.total - DI_FIND_MAX} more match`),
  );
});

test('item 09: outcome facts stay separate, unsupported or unreported facts are said, nothing inferred', () => {
  const herdr = state.missions.missions.find((m) => m.herdrTask)!;
  const h = plainMissionOutcomes(herdr);
  assert.match(h.completion, /not supported for a plain Herdr task/);
  assert.match(h.delivery, /not reported/);
  const core = state.missions.missions.find((m) => !m.herdrTask)!;
  const c = plainMissionOutcomes(core);
  for (const fact of [c.completion, c.verification, c.review, c.delivery])
    assert.ok(fact.length > 0);
  assert.notEqual(c.completion, c.verification); // reported completion is never verification
  assert.match(
    plainUnreported({ state: 'observed', value: 'unknown', reason: null }),
    /^unknown \(no supported source/,
  );
  assert.match(plainUnreported({ state: 'not_recorded', value: null, reason: null }), /^unknown/);
});

test('the focus ring is the reception inbox’s own (one mechanism, not a second)', () => {
  const inbox = fs.readFileSync(path.join(root, 'src/components/DiReceptionInbox.tsx'), 'utf-8');
  assert.ok(inbox.includes("import { DI_FOCUS_RING } from './diNavigation.js';"));
  assert.ok(inbox.includes('${DI_FOCUS_RING}'));
  // the desk it replaced is gone, and no second ring was defined in its place
  assert.equal(fs.existsSync(path.join(root, 'src/components/DiReceptionDesk.tsx')), false);
  assert.doesNotMatch(inbox, /const FOCUS\b/);
});
