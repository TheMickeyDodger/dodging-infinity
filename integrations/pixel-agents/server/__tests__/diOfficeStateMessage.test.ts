/**
 * The additive `diOfficeState` ServerMessage, built from the CONTRACT-v3 canonical
 * FIXTURE (synthetic, hash-pinned) — fixture evidence only, never live acceptance.
 */
import { describe, expect, it } from 'vitest';

import type { DiOfficeState } from '../../core/src/messages.js';
import { parseEnvelope } from '../src/di/catalogProjection.js';
import { buildDiOfficeStateMessage } from '../src/di/officeStateMessage.js';
import { buildOfficeView } from '../src/di/officeView.js';
import type { ServeTimeVerdict } from '../src/di/serveTimeVerdict.js';
import { clone, loadFixture } from './helpers/diFixture.js';

const NOW = 1800000000;
const FRESH: ServeTimeVerdict = { health: { known: true, value: 'fresh' }, problem: null };
type Raw = Record<string, unknown>;

function message(edit?: (f: Raw) => void, now = NOW, characterId = 41): DiOfficeState {
  const fixture = clone(loadFixture());
  edit?.(fixture);
  const parsed = parseEnvelope(fixture);
  if (!parsed.ok) throw new Error('fixture variation did not parse');
  return buildDiOfficeStateMessage(buildOfficeView(parsed.envelope, FRESH, now), () => characterId);
}

describe('diOfficeState (FIXTURE)', () => {
  it('identifies each source-qualified Mission visibly, verbatim', () => {
    const m = message();
    expect(m.type).toBe('diOfficeState');
    const [core, legacy] = m.missions.missions;
    expect(core).toMatchObject({
      missionRef: 'synthetic-core:mn-1714199978b50a9d93349749406a85a7',
      sourceId: 'synthetic-core',
      nativeId: 'mn-1714199978b50a9d93349749406a85a7',
      producerDeclared: 'synthetic-producer-core',
      lifecycleModel: 'mission_core.mission_state/1',
      standing: 'active',
      lifecycleState: 'RUNNING',
      bindingState: 'bound',
      live: true,
    });
    expect(legacy).toMatchObject({
      lifecycleModel: 'workflow_authority.workflow_phase/2',
      lifecycleState: 'DISPATCHED',
      standing: 'active',
      bindingState: 'disappeared',
      bindingReason: 'task_not_in_snapshot',
      live: false,
      notLiveReason: 'binding_disappeared:task_not_in_snapshot',
    });
    expect(legacy.members).toEqual({
      state: 'projected_reason',
      reason: 'binding_disappeared:task_not_in_snapshot',
      members: [],
    });
  });

  it("carries the live member's canonical identity and evidence fields, and its character id", () => {
    const [core] = message().missions.missions;
    const [member] = core.members.members;
    expect(member).toMatchObject({
      characterId: 41,
      coverage: 'direct',
      memberRef: 'synthetic-core:mn-1714199978b50a9d93349749406a85a7#executor1',
      logical: 'executor1',
      role: 'executor',
      live: true,
      animateActive: true,
      activity: 'busy',
    });
    expect(member.taskId).toEqual({
      state: 'observed',
      value: 'synthetic-task-00000000-000000-aaaaaa',
      reason: null,
    });
    expect(member.sessionLive).toEqual({ state: 'observed', value: 'sess-exec-1', reason: null });
    expect(member.model).toEqual({ state: 'observed', value: 'unknown', reason: null });
  });

  it('a not-live member has NO character and does not animate, even with session.live present', () => {
    const [core] = message((f) => {
      const m = ((f.response as Raw).missions as Raw[])[0];
      (m.lifecycle as Raw).standing = 'terminal';
    }).missions.missions;
    const [member] = core.members.members;
    expect(member.sessionLive.value).toBe('sess-exec-1');
    expect(member).toMatchObject({ live: false, characterId: null, animateActive: false });
  });

  it('only a live BUSY member animates (idle / unknown / unknown_future do not)', () => {
    for (const activity of ['idle', 'done', 'blocked', 'unknown', 'unknown_future']) {
      const [core] = message((f) => {
        (((f.response as Raw).missions as Raw[])[0].members as Raw[])[0].activity = activity;
      }).missions.missions;
      expect(core.members.members[0].animateActive).toBe(false);
      expect(core.members.members[0].activity).toBe(activity);
    }
  });

  it('labels coverage, both freshness levels and the distinct times', () => {
    const m = message(undefined, NOW + 5);
    expect(m.coverage).toMatchObject({
      scope: 'direct_receipt_task',
      linkedChildRoles: 'not_covered',
    });
    expect(m.envelope).toMatchObject({
      fresh: true,
      served: 'refreshed',
      verdictHealth: 'fresh',
      registryHealth: 'fresh',
    });
    expect(m.runtime).toMatchObject({
      fresh: true,
      configured: true,
      available: true,
      snapshotHealth: 'fresh',
    });
    expect(m.times).toEqual({
      requestedAt: NOW,
      generatedAt: NOW,
      validUntil: NOW + 60,
      snapshotObservedAt: NOW,
      evaluatedAt: NOW + 5,
    });
  });

  it('a fresh envelope with an unavailable runtime: no live member, nested reason shown, roster unavailable', () => {
    const m = message((f) => {
      (f.runtime_capture as Raw).available = false;
      (f.runtime_capture as Raw).problem = 'capture_not_finished_within_wait';
      ((f.response as Raw).runtime_snapshot as Raw).health = 'unavailable';
      const core = ((f.response as Raw).missions as Raw[])[0];
      core.binding = { state: 'unverified', reason: 'runtime_snapshot_not_fresh', task_id: null };
      core.members = [];
    });
    expect(m.envelope.fresh).toBe(true);
    expect(m.runtime).toMatchObject({
      fresh: false,
      available: false,
      captureProblem: 'capture_not_finished_within_wait',
      reason: 'runtime_snapshot_unavailable:capture_not_finished_within_wait',
    });
    expect(m.live).toBe(false);
    expect(m.missions.missions[0].members.state).toBe('unavailable');
  });

  it('not_applicable is its own state, never an empty value', () => {
    const [, legacy] = message().missions.missions;
    expect(legacy.updatedAt).toEqual({
      state: 'not_applicable',
      value: null,
      reason: 'updated_at_not_applicable_to_workflow_store',
    });
  });

  it('an absent unreported-fact key is unavailable (field_absent), distinct from the literal "unknown"', () => {
    const [core] = message((f) => {
      delete (((f.response as Raw).missions as Raw[])[0].members as Raw[])[0].tokens;
    }).missions.missions;
    expect(core.members.members[0].tokens).toEqual({
      state: 'unavailable',
      value: null,
      reason: 'field_absent',
    });
    expect(core.members.members[0].cost).toEqual({
      state: 'observed',
      value: 'unknown',
      reason: null,
    });
  });
});

describe('diOfficeState: replaced/session_changed is visible but unconfirmed (Supervisor ruling, FIXTURE)', () => {
  const replaced = (f: Raw): void => {
    const core = (f.response as Raw).missions as Raw[];
    const member = (core[0].members as Raw[])[0];
    member.classification = 'replaced';
    member.reason = 'session_changed';
    (member.session as Raw).live = 'sess-exec-2';
  };

  it('a BUSY replaced member is not live, gets no character and does NOT animate', () => {
    const [core] = message(replaced).missions.missions;
    expect(core.live).toBe(true);
    expect(core.members.members).toHaveLength(1);
    const [member] = core.members.members;
    expect(member.activity).toBe('busy');
    expect(member.live).toBe(false);
    expect(member.notLiveReason).toBe('member_replaced:session_changed');
    expect(member.characterId).toBeNull();
    expect(member.animateActive).toBe(false);
  });

  it('carries the backend values verbatim and both sessions; never relabelled as an ending', () => {
    const [member] = message(replaced).missions.missions[0].members.members;
    expect(member.classification).toBe('replaced');
    expect(member.reason).toBe('session_changed');
    expect(member.sessionRecorded).toEqual({
      state: 'observed',
      value: 'sess-exec-1',
      reason: null,
    });
    expect(member.sessionLive).toEqual({ state: 'observed', value: 'sess-exec-2', reason: null });
    expect(JSON.stringify(member)).not.toMatch(/disappeared|terminal|dead|gone|ended/i);
  });

  it('the same member as present (control) is live and animates', () => {
    const [member] = message().missions.missions[0].members.members;
    expect(member.classification).toBe('present');
    expect(member.live).toBe(true);
    expect(member.animateActive).toBe(true);
  });
});
