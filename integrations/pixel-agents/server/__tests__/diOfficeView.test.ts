/**
 * Office presentation rules. Built on the CONTRACT-v3 canonical FIXTURE (synthetic,
 * hash-pinned in helpers/diFixture.ts) and variations of it constructed in each test.
 * FIXTURE evidence only — never live acceptance.
 */
import { describe, expect, it } from 'vitest';

import type { CatalogEnvelope } from '../src/di/catalogProjection.js';
import { parseEnvelope } from '../src/di/catalogProjection.js';
import type { MissionPresentation, OfficeView } from '../src/di/officeView.js';
import {
  buildOfficeView,
  DI_COVERAGE,
  unavailableView,
  withinValidUntil,
} from '../src/di/officeView.js';
import type { ServeTimeVerdict } from '../src/di/serveTimeVerdict.js';
import { readServeTimeVerdict } from '../src/di/serveTimeVerdict.js';
import { clone, loadFixture } from './helpers/diFixture.js';

const GENERATED_AT = 1800000000;
const VALID_UNTIL = 1800000060;
const FRESH: ServeTimeVerdict = { health: { known: true, value: 'fresh' }, problem: null };

type Raw = Record<string, unknown>;

function envelopeFrom(raw: Raw): CatalogEnvelope {
  const parsed = parseEnvelope(raw);
  if (!parsed.ok) throw new Error(`fixture variation did not parse: ${parsed.verdict.detail}`);
  return parsed.envelope;
}

function fixtureVariant(edit?: (fixture: Raw) => void): CatalogEnvelope {
  const fixture = clone(loadFixture());
  edit?.(fixture);
  return envelopeFrom(fixture);
}

function missions(fixture: Raw): Raw[] {
  return (fixture.response as Raw).missions as Raw[];
}

function missionsOf(view: OfficeView): MissionPresentation[] {
  if (view.missions.state !== 'observed') throw new Error(`missions are ${view.missions.state}`);
  return view.missions.value;
}

describe('expiry boundary: now <= valid_until is FRESH (inclusive, integer epoch seconds)', () => {
  it('the floor itself', () => {
    expect(withinValidUntil(GENERATED_AT, VALID_UNTIL)).toBe(true);
    expect(withinValidUntil(VALID_UNTIL, VALID_UNTIL)).toBe(true);
    expect(withinValidUntil(VALID_UNTIL + 1, VALID_UNTIL)).toBe(false);
  });

  it('at generated_at: live', () => {
    expect(buildOfficeView(fixtureVariant(), FRESH, GENERATED_AT).live).toBe(true);
  });

  it('at exactly valid_until: still live', () => {
    expect(buildOfficeView(fixtureVariant(), FRESH, VALID_UNTIL).live).toBe(true);
  });

  it('at valid_until + 1: not live, expired locally', () => {
    const view = buildOfficeView(fixtureVariant(), FRESH, VALID_UNTIL + 1);
    expect(view.live).toBe(false);
    expect(view.notLiveReason).toBe('expired_locally:response_expired');
    // Data read successfully is shown as NOT live, never as live.
    for (const m of missionsOf(view)) expect(m.presence.live).toBe(false);
  });
});

describe('the serve-time verdict is an INPUT; there is no healthy fallback', () => {
  it('a fresh verdict + inside the floor is live', () => {
    const view = buildOfficeView(fixtureVariant(), FRESH, GENERATED_AT);
    expect(view.live).toBe(true);
    expect(view.serveTimeVerdict).toEqual({ health: 'fresh', problem: null });
  });

  it('an absent health header is NOT fresh', () => {
    const verdict = readServeTimeVerdict({});
    expect(verdict).toEqual({ health: null, problem: null });
    const view = buildOfficeView(fixtureVariant(), verdict, GENERATED_AT);
    expect(view.live).toBe(false);
    expect(view.notLiveReason).toBe('no_serve_time_verdict');
  });

  it('header convention: an omitted problem header means problem null; a present one is verbatim', () => {
    expect(readServeTimeVerdict({ 'x-di-freshness-health': 'fresh' })).toEqual({
      health: { known: true, value: 'fresh' },
      problem: null,
    });
    expect(
      readServeTimeVerdict({
        'x-di-freshness-health': 'stale',
        'x-di-freshness-problem': 'response_expired',
      }),
    ).toEqual({ health: { known: true, value: 'stale' }, problem: 'response_expired' });
  });

  it('a non-fresh or unrecognized verdict is not live, with its reason', () => {
    const stale = buildOfficeView(
      fixtureVariant(),
      readServeTimeVerdict({
        'x-di-freshness-health': 'stale',
        'x-di-freshness-problem': 'response_expired',
      }),
      GENERATED_AT,
    );
    expect(stale.live).toBe(false);
    expect(stale.notLiveReason).toBe('serve_time_stale:response_expired');
    const odd = buildOfficeView(
      fixtureVariant(),
      readServeTimeVerdict({ 'x-di-freshness-health': 'FRESH' }),
      GENERATED_AT,
    );
    expect(odd.live).toBe(false);
    expect(odd.notLiveReason).toBe('serve_time_unrecognized:FRESH');
  });

  it('an unreachable backend / absent discovery is unavailable — never zero, never healthy', () => {
    const view = unavailableView('discovery:discovery_absent', GENERATED_AT);
    expect(view.live).toBe(false);
    expect(view.missions).toEqual({ state: 'unavailable', reason: 'discovery:discovery_absent' });
    expect(view.coverage).toBe(DI_COVERAGE);
  });

  it('the three times stay distinct and are never restamped', () => {
    const view = buildOfficeView(
      fixtureVariant((f) => (f.requested_at = 1799999990)),
      FRESH,
      GENERATED_AT + 3,
    );
    expect(view.times).toEqual({
      requestedAt: 1799999990,
      generatedAt: GENERATED_AT,
      validUntil: VALID_UNTIL,
      snapshotObservedAt: GENERATED_AT,
      evaluatedAt: GENERATED_AT + 3,
    });
  });
});

describe('mission and member liveness (v1 DIRECT roster)', () => {
  it('THE TRAP: active standing + disappeared binding renders NOT LIVE, carrying the reason', () => {
    const [, legacy] = missionsOf(buildOfficeView(fixtureVariant(), FRESH, GENERATED_AT));
    expect(legacy.standing).toBe('active');
    expect(legacy.lifecycleState).toBe('DISPATCHED');
    expect(legacy.presence).toEqual({
      live: false,
      reason: 'binding_disappeared:task_not_in_snapshot',
    });
    // task_not_in_snapshot is a specific projected reason, not "unavailable", not "none".
    expect(legacy.members).toEqual({
      state: 'projected_reason',
      reason: 'binding_disappeared:task_not_in_snapshot',
    });
  });

  it('the bound, active mission and its present member are live', () => {
    const [core] = missionsOf(buildOfficeView(fixtureVariant(), FRESH, GENERATED_AT));
    expect(core.presence).toEqual({ live: true, reason: null });
    if (core.members.state !== 'observed') throw new Error('members not observed');
    expect(core.members.value[0].presence).toEqual({ live: true, reason: null });
    expect(core.members.value[0].coverage).toBe('direct');
  });

  for (const standing of ['terminal', 'inactive'] as const) {
    it(`a member WITH session.live present under a ${standing} standing is NOT live`, () => {
      const env = fixtureVariant((f) => {
        const m = missions(f)[0];
        (m.lifecycle as Raw).standing = standing;
        const member = (m.members as Raw[])[0];
        expect((member.session as Raw).live).toBe('sess-exec-1'); // the lingering session
      });
      const [core] = missionsOf(buildOfficeView(env, FRESH, GENERATED_AT));
      expect(core.presence).toEqual({ live: false, reason: `standing_${standing}` });
      if (core.members.state !== 'observed') throw new Error('members not observed');
      expect(core.members.value[0].presence.live).toBe(false);
    });
  }

  it('standing unknown_future is handled explicitly — not folded into unknown or a default', () => {
    const env = fixtureVariant(
      (f) => ((missions(f)[0].lifecycle as Raw).standing = 'unknown_future'),
    );
    const [core] = missionsOf(buildOfficeView(env, FRESH, GENERATED_AT));
    expect(core.presence).toEqual({ live: false, reason: 'standing_unknown_future' });
    expect(core.standingKnown).toBe(true);
  });

  it('activity unknown_future is shown verbatim and never implies busy', () => {
    const env = fixtureVariant(
      (f) => ((missions(f)[0].members as Raw[])[0].activity = 'unknown_future'),
    );
    const [core] = missionsOf(buildOfficeView(env, FRESH, GENERATED_AT));
    if (core.members.state !== 'observed') throw new Error('members not observed');
    expect(core.members.value[0].activity).toBe('unknown_future');
    expect(core.members.value[0].activityKnown).toBe(true);
  });

  it('a disappeared / ambiguous member is not live, with its reason', () => {
    const env = fixtureVariant((f) => {
      const member = (missions(f)[0].members as Raw[])[0];
      member.classification = 'disappeared';
      member.reason = 'agent_not_live';
    });
    const [core] = missionsOf(buildOfficeView(env, FRESH, GENERATED_AT));
    if (core.members.state !== 'observed') throw new Error('members not observed');
    expect(core.members.value[0].presence).toEqual({
      live: false,
      reason: 'member_disappeared:agent_not_live',
    });
  });
});

describe('observation states are distinct: known_empty / not_recorded / projected_reason / not_applicable / unavailable', () => {
  it('KNOWN EMPTY: a successful read of zero missions is "none", not unavailable', () => {
    const env = fixtureVariant((f) => ((f.response as Raw).missions = []));
    const view = buildOfficeView(env, FRESH, GENERATED_AT);
    expect(view.missions).toEqual({ state: 'known_empty' });
  });

  it('KNOWN EMPTY: a bound mission whose task has no bindings has no members — observed none', () => {
    const env = fixtureVariant((f) => (missions(f)[0].members = []));
    const [core] = missionsOf(buildOfficeView(env, FRESH, GENERATED_AT));
    expect(core.members).toEqual({ state: 'known_empty' });
  });

  it('NOT RECORDED: a null field from a successful read', () => {
    const env = fixtureVariant((f) => ((missions(f)[0].execution as Raw).identity_source = null));
    const [core] = missionsOf(buildOfficeView(env, FRESH, GENERATED_AT));
    expect(core.identitySource).toEqual({ state: 'not_recorded' });
  });

  it('NOT APPLICABLE: workflow_store fields that do not exist for that kind are never "empty"', () => {
    const [core, legacy] = missionsOf(buildOfficeView(fixtureVariant(), FRESH, GENERATED_AT));
    expect(legacy.updatedAt).toEqual({
      state: 'not_applicable',
      reason: 'updated_at_not_applicable_to_workflow_store',
    });
    expect(legacy.identitySource.state).toBe('not_applicable');
    expect(legacy.paused.state).toBe('not_applicable');
    expect(legacy.ambiguity).toEqual({ state: 'observed', value: 'none' });
    expect(core.updatedAt).toEqual({ state: 'observed', value: 1000000 });
    expect(core.ambiguity.state).toBe('not_applicable');
    expect(core.paused).toEqual({ state: 'observed', value: false });
  });

  it('PROJECTED REASON: task_not_in_snapshot is its own state (see the trap test)', () => {
    const [, legacy] = missionsOf(buildOfficeView(fixtureVariant(), FRESH, GENERATED_AT));
    expect(legacy.members.state).toBe('projected_reason');
  });

  it('UNAVAILABLE: binding unverified (the backend could not look) is unavailable, not none', () => {
    const env = fixtureVariant((f) => {
      const binding = missions(f)[0].binding as Raw;
      binding.state = 'unverified';
      binding.reason = 'workspace_not_observed';
      missions(f)[0].members = [];
    });
    const [core] = missionsOf(buildOfficeView(env, FRESH, GENERATED_AT));
    expect(core.members).toEqual({
      state: 'unavailable',
      reason: 'binding_unverified:workspace_not_observed',
    });
    expect(core.presence).toEqual({
      live: false,
      reason: 'binding_unverified:workspace_not_observed',
    });
  });
});

describe('a starved or absent runtime capture never renders as zero members', () => {
  function starved(problem: string): CatalogEnvelope {
    return fixtureVariant((f) => {
      const response = f.response as Raw;
      const snapshot = response.runtime_snapshot as Raw;
      snapshot.health = 'unavailable';
      snapshot.problem = 'snapshot_absent';
      snapshot.observed_at = null;
      (f.runtime_capture as Raw).available = false;
      (f.runtime_capture as Raw).problem = problem;
      const core = missions(f)[0];
      core.binding = { state: 'unverified', reason: 'runtime_snapshot_not_fresh', task_id: null };
      core.members = [];
    });
  }

  for (const problem of [
    'capture_not_finished_within_wait',
    'capture_boundary_timed_out',
    'capture_slot_occupied',
    'agent_listing_timed_out',
  ]) {
    it(`${problem}: not live, roster UNAVAILABLE with the capture reason`, () => {
      const view = buildOfficeView(starved(problem), FRESH, GENERATED_AT);
      expect(view.live).toBe(false);
      expect(view.notLiveReason).toBe(`runtime_snapshot_unavailable:${problem}`);
      const [core] = missionsOf(view);
      expect(core.members).toEqual({
        state: 'unavailable',
        reason: `runtime_snapshot_unavailable:${problem}`,
      });
      expect(core.members.state).not.toBe('known_empty');
    });
  }

  it('even a (contradictory) bound binding with [] members is unavailable when the snapshot is not fresh', () => {
    const env = fixtureVariant((f) => {
      ((f.response as Raw).runtime_snapshot as Raw).health = 'stale';
      missions(f)[0].members = [];
    });
    const [core] = missionsOf(buildOfficeView(env, FRESH, GENERATED_AT));
    expect(core.members.state).toBe('unavailable');
  });

  it('catalog_no_runtime_root rides runtime_capture.problem WITH a response — not an envelope failure', () => {
    const env = fixtureVariant((f) => {
      f.runtime_capture = {
        configured: true,
        available: false,
        problem: 'catalog_no_runtime_root',
        detail: 'x',
      };
      const snapshot = (f.response as Raw).runtime_snapshot as Raw;
      snapshot.health = 'unavailable';
      snapshot.problem = 'snapshot_absent';
    });
    expect(env.problem).toBeNull();
    expect(env.served).toEqual({ known: true, value: 'refreshed' });
    const view = buildOfficeView(env, FRESH, GENERATED_AT);
    expect(view.envelopeProblem).toBeNull();
    expect(view.missions.state).toBe('observed'); // the catalog still answered
    expect(view.notLiveReason).toBe('runtime_snapshot_unavailable:catalog_no_runtime_root');
  });

  it('a capture that was never configured is unavailable, not "no members"', () => {
    const env = fixtureVariant((f) => {
      f.runtime_capture = { configured: false };
      const snapshot = (f.response as Raw).runtime_snapshot as Raw;
      snapshot.health = 'unavailable';
      snapshot.problem = 'snapshot_absent';
    });
    const view = buildOfficeView(env, FRESH, GENERATED_AT);
    expect(view.live).toBe(false);
    expect(view.notLiveReason).toBe('runtime_snapshot_unavailable:snapshot_absent');
    expect(missionsOf(view)[0].members.state).toBe('unavailable');
  });
});

describe('envelope problems are expected operating states, never live', () => {
  for (const problem of [
    'catalog_slot_occupied',
    'catalog_refresh_throttled',
    'catalog_owner_shut_down',
    'catalog_query_failed',
    'catalog_query_not_finished_within_wait',
    'catalog_bad_request',
  ]) {
    it(`${problem} with response null: not live, missions unavailable with the reason`, () => {
      const env = fixtureVariant((f) => {
        f.response = null;
        f.problem = problem;
        f.detail = 'd';
        f.served = 'none';
        f.runtime_capture = null;
      });
      const view = buildOfficeView(env, readServeTimeVerdict({}), GENERATED_AT);
      expect(view.live).toBe(false);
      expect(view.missions).toEqual({ state: 'unavailable', reason: `no_response:${problem}` });
    });
  }
});

describe('coverage is labelled explicitly', () => {
  it('direct receipt task only; linked-child roles NOT covered', () => {
    const view = buildOfficeView(fixtureVariant(), FRESH, GENERATED_AT);
    expect(view.coverage.scope).toBe('direct_receipt_task');
    expect(view.coverage.linkedChildRoles).toBe('not_covered');
  });
});

describe('TWO-LEVEL freshness: a fresh envelope does NOT license live members', () => {
  /** FIXTURE-driven: fresh envelope header, but the nested runtime is unavailable. */
  function freshEnvelopeUnavailableRuntime(problem: string): CatalogEnvelope {
    return fixtureVariant((f) => {
      const capture = f.runtime_capture as Raw;
      capture.available = false;
      capture.problem = problem;
      const snapshot = (f.response as Raw).runtime_snapshot as Raw;
      snapshot.health = 'unavailable';
      snapshot.problem = 'snapshot_absent';
      for (const m of missions(f)) {
        m.binding = { state: 'unverified', reason: 'runtime_snapshot_not_fresh', task_id: null };
        m.members = [];
      }
    });
  }

  it('fresh envelope + unavailable runtime + zero members -> NOT live, nested problem surfaced, roster UNAVAILABLE', () => {
    const view = buildOfficeView(
      freshEnvelopeUnavailableRuntime('capture_not_finished_within_wait'),
      FRESH,
      GENERATED_AT,
    );
    expect(view.freshness?.envelope).toEqual({ fresh: true, reason: null });
    expect(view.freshness?.runtime).toEqual({
      fresh: false,
      reason: 'runtime_snapshot_unavailable:capture_not_finished_within_wait',
    });
    expect(view.live).toBe(false);
    for (const m of missionsOf(view)) {
      expect(m.presence.live).toBe(false);
      expect(m.members.state).toBe('unavailable');
    }
  });

  it('the INVERSE: a genuinely empty roster with a healthy capture is LIVE and KNOWN EMPTY', () => {
    const env = fixtureVariant((f) => (missions(f)[0].members = []));
    const view = buildOfficeView(env, FRESH, GENERATED_AT);
    expect(view.freshness?.runtime).toEqual({ fresh: true, reason: null });
    expect(view.live).toBe(true);
    const [core] = missionsOf(view);
    expect(core.presence.live).toBe(true);
    expect(core.members).toEqual({ state: 'known_empty' });
  });

  it('the two zero-member cases are distinguishable', () => {
    const starved = missionsOf(
      buildOfficeView(
        freshEnvelopeUnavailableRuntime('capture_slot_occupied'),
        FRESH,
        GENERATED_AT,
      ),
    )[0];
    const empty = missionsOf(
      buildOfficeView(
        fixtureVariant((f) => (missions(f)[0].members = [])),
        FRESH,
        GENERATED_AT,
      ),
    )[0];
    expect(starved.members).not.toEqual(empty.members);
    expect(starved.members.state).toBe('unavailable');
    expect(empty.members.state).toBe('known_empty');
  });

  it('snapshot fresh but capture reporting available:false is still not live', () => {
    const env = fixtureVariant((f) => {
      (f.runtime_capture as Raw).available = false;
      (f.runtime_capture as Raw).problem = 'capture_cleanup_unestablished';
    });
    const view = buildOfficeView(env, FRESH, GENERATED_AT);
    expect(view.live).toBe(false);
    expect(view.notLiveReason).toBe('runtime_capture_unavailable:capture_cleanup_unestablished');
  });

  it('a capture that does not report availability is not assumed available (no healthy fallback)', () => {
    const env = fixtureVariant((f) => delete (f.runtime_capture as Raw).available);
    const view = buildOfficeView(env, FRESH, GENERATED_AT);
    expect(view.live).toBe(false);
    expect(view.notLiveReason).toBe('runtime_capture_not_reported:unspecified');
  });

  // FIXTURE-driven, deterministic: the runtime_snapshot.py capture problem codes (W1 trace,
  // runtime_snapshot.py:306-324). These are fixture variations, NOT live observations.
  for (const code of [
    'capture_not_finished_within_wait',
    'capture_boundary_timed_out',
    'capture_slot_occupied',
    'agent_listing_timed_out',
    'agent_listing_unavailable',
    'agent_listing_truncated',
    'agent_listing_malformed',
    'capture_no_root_observable',
    'capture_boundary_failed',
    'capture_input_exceeds_bound',
    'capture_cleanup_unestablished',
    'capture_owner_shut_down',
    'snapshot_exceeds_bound',
  ]) {
    it(`FIXTURE starvation ${code}: never zero members, never live`, () => {
      const view = buildOfficeView(freshEnvelopeUnavailableRuntime(code), FRESH, GENERATED_AT);
      expect(view.live).toBe(false);
      expect(view.notLiveReason).toBe(`runtime_snapshot_unavailable:${code}`);
      for (const m of missionsOf(view)) expect(m.members.state).not.toBe('known_empty');
    });
  }
});

describe('NAMED DECISION (Supervisor ruling): only present is live; replaced is a visible third state', () => {
  it('the live classifications are exactly [present], in one named constant', async () => {
    const { LIVE_MEMBER_CLASSIFICATIONS } = await import('../src/di/officeView.js');
    expect([...LIVE_MEMBER_CLASSIFICATIONS]).toEqual(['present']);
  });

  it('replaced/session_changed is NOT live, stays in the roster verbatim with both sessions, and leaves the mission live', () => {
    const env = fixtureVariant((f) => {
      const member = (missions(f)[0].members as Raw[])[0];
      member.classification = 'replaced';
      member.reason = 'session_changed';
      (member.session as Raw).live = 'sess-exec-2';
    });
    const [core] = missionsOf(buildOfficeView(env, FRESH, GENERATED_AT));
    expect(core.presence).toEqual({ live: true, reason: null }); // the Mission itself is untouched
    if (core.members.state !== 'observed') throw new Error('members not observed');
    expect(core.members.value).toHaveLength(1); // visible: never filtered out
    const member = core.members.value[0];
    expect(member.presence).toEqual({ live: false, reason: 'member_replaced:session_changed' });
    expect(member.classification).toBe('replaced'); // verbatim, never relabelled
    expect(member.reason).toBe('session_changed');
    expect(member.sessionRecorded).toEqual({ state: 'observed', value: 'sess-exec-1' });
    expect(member.sessionLive).toEqual({ state: 'observed', value: 'sess-exec-2' });
  });

  it('ambiguous is never live, whatever its session evidence says', () => {
    const env = fixtureVariant((f) => {
      const member = (missions(f)[0].members as Raw[])[0];
      member.classification = 'ambiguous';
      member.reason = 'stable_identity_conflict';
    });
    const [core] = missionsOf(buildOfficeView(env, FRESH, GENERATED_AT));
    if (core.members.state !== 'observed') throw new Error('members not observed');
    expect(core.members.value[0].presence).toEqual({
      live: false,
      reason: 'member_ambiguous:stable_identity_conflict',
    });
  });
});
