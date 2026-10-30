/**
 * PRESENTATION RULES of Mission details (pass A, Stage 2): which Delivery steps and state actions are shown, which are
 * locked and why, when the trash may be offered, and how a swarm role's model and provider are worded.
 *
 * These are layout-stage rules. Passing them is NOT acceptance of any control: nothing here commits, pushes, opens a
 * pull request, accepts CI, merges, stops, approves or deletes, and Stage 4 must still wire each control to an existing
 * human-gated route. Every expectation is a hand-written literal from the gap resolutions and plan ACs named on it.
 */
import { describe, expect, it } from 'vitest';

import type { DiObservedValue, DiOfficeState } from '../../core/src/messages.ts';
import {
  deliverySteps,
  DI_SCAN_LOCK,
  type DiDeliveryStage,
  type DiScanKnown,
  type DiScanRow,
  modelFact,
  providerFact,
  scanRowsFromOffice,
  stateActions,
  trashGate,
  unreadStateFacts,
} from '../src/components/diScanModel.ts';
import { productionRow } from './helpers/productionRow.ts';

const fixture = (stage: DiDeliveryStage) => deliverySteps({ source: 'fixture', stage });
const gates = (stage: DiDeliveryStage) => fixture(stage).map((s) => `${s.id}:${s.gate}`);

describe('Delivery: each step is offered only when the canonical state matches; otherwise locked with a plain reason (gap 2, gap 3, AC-4.6-16/-17)', () => {
  // CHANGED BY THE OWNER RULING (2026-10-28, owner-decisions.md:93): AC-4.6-48 and AC-4.6-40 (a "Hold to merge" step, never ready before accepted CI) are SUPERSEDED. There is no merge step;
  // pr_delivery's no-merge boundary is pinned (test/diScanDeliveryHold.test.ts states the removal). Every merge entry below is removed, marked here once.
  it('lists the four steps in delivery order; there is no merge step', () => {
    expect(fixture('working').map((s) => s.label)).toEqual([
      'Commit',
      'Push',
      'Open pull request',
      'Accept CI results',
    ]);
  });
  it('nothing is ready while the work is still in progress', () => {
    expect(gates('working')).toEqual([
      'commit:locked',
      'push:locked',
      'pr:locked',
      'accept_ci:locked',
    ]);
  });
  it('each stage readies exactly the next step, and marks the earlier ones done', () => {
    expect(gates('approved')).toEqual([
      'commit:ready',
      'push:locked',
      'pr:locked',
      'accept_ci:locked',
    ]);
    expect(gates('committed')).toEqual([
      'commit:done',
      'push:ready',
      'pr:locked',
      'accept_ci:locked',
    ]);
    expect(gates('pushed')).toEqual([
      'commit:done',
      'push:done',
      'pr:ready',
      'accept_ci:locked',
    ]);
    expect(gates('pr_open')).toEqual([
      'commit:done',
      'push:done',
      'pr:done',
      'accept_ci:locked',
    ]);
    expect(gates('ci_passed')).toEqual([
      'commit:done',
      'push:done',
      'pr:done',
      'accept_ci:ready',
    ]);
    expect(gates('ci_accepted')).toEqual([
      'commit:done',
      'push:done',
      'pr:done',
      'accept_ci:done',
    ]);
    expect(gates('merged')).toEqual([
      'commit:done',
      'push:done',
      'pr:done',
      'accept_ci:done',
    ]);
  });
  it('failed CI offers nothing to accept, and says so', () => {
    const accept = fixture('ci_failed').find((s) => s.id === 'accept_ci')!;
    expect(accept.gate).toBe('locked');
    expect(accept.reason).toBe('CI failed. There are no passing results to accept.');
  });
  it('a locked step names what it waits for, in plain words', () => {
    expect(fixture('working').map((s) => s.reason)).toEqual([
      'Commit waits for the changes to be approved.',
      'Push needs a commit first.',
      'A pull request needs a pushed branch.',
      'CI results need an open pull request.',
    ]);
    expect(fixture('pr_open').find((s) => s.id === 'accept_ci')!.reason).toBe(
      'CI results are not in yet.',
    );
  });
  it("where this build does not read a Mission's delivery state, every step is locked and says so (no guessed state)", () => {
    const steps = deliverySteps({ source: 'not_read', stage: null });
    expect(steps.map((s) => s.gate)).toEqual(['locked', 'locked', 'locked', 'locked']);
    for (const s of steps)
      expect(s.reason).toBe('Delivery state is not read in this layout preview.');
  });
});

describe('state actions appear only when the Mission state matches (gap 3)', () => {
  const none = {
    source: 'fixture' as const,
    // round 5: running is a known-or-not-known fact; here it is KNOWN false
    running: { known: true, value: false } as DiScanKnown<boolean>,
    awaitingApproval: false,
    repoRequest: false,
    stalled: false,
    logRecorded: false,
  };
  const ids = (facts: Partial<typeof none>) => stateActions({ ...none, ...facts }).map((a) => a.id);
  it('no matching state, no action', () => {
    expect(ids({})).toEqual([]);
  });
  it('running offers only Stop, and Stop is a hold, never a single click (AC-4.9-06, AC-UX-18)', () => {
    const actions = stateActions({ ...none, running: { known: true, value: true } });
    expect(actions.map((a) => [a.id, a.label, a.hold])).toEqual([['stop', 'Hold to stop', true]]);
  });
  it('a proposal waiting for approval offers Approve, Request changes and Reject', () => {
    expect(ids({ awaitingApproval: true })).toEqual(['approve', 'request_changes', 'reject']);
  });
  it('a repository request offers Allow and Decline', () => {
    expect(ids({ repoRequest: true })).toEqual(['repo_allow', 'repo_decline']);
  });
  it('a stalled Mission offers Recover, View log and a held Cancel; Recover only restarts work (AC-4.9-03/-08)', () => {
    const actions = stateActions({ ...none, stalled: true });
    expect(actions.map((a) => a.id)).toEqual(['recover', 'view_log', 'cancel']);
    expect(actions.find((a) => a.id === 'cancel')!.hold).toBe(true);
    expect(actions.find((a) => a.id === 'recover')!.label).toBe('Recover');
  });
  it('a recorded log alone offers View log', () => {
    expect(ids({ logRecorded: true })).toEqual(['view_log']);
  });
});

describe('the trash is offered only for a finished or cancelled Mission (gap 5, AC-4.4-29/-30)', () => {
  const row = (partial: Partial<DiScanRow>): DiScanRow => ({ ...DI_SCAN_LOCK[0]!, ...partial });
  it('a running (Building) Mission cannot be deleted, whatever else it shows', () => {
    const g = trashGate(
      row({ tab: 'open', pills: [{ id: 'building', label: 'Building', tone: 'building' }] }),
    );
    expect(g).toEqual({ open: false, reason: 'A running Mission cannot be deleted.' });
    const merged = trashGate(
      row({ tab: 'merged', pills: [{ id: 'building', label: 'Building', tone: 'building' }] }),
    );
    expect(merged.open).toBe(false);
  });
  it('a finished (merged, closed, archived) or cancelled Mission may be offered', () => {
    for (const tab of ['merged', 'closed', 'archived'] as const)
      expect(trashGate(row({ tab, pills: [] })), tab).toEqual({ open: true, reason: '' });
    expect(
      trashGate(row({ tab: 'open', pills: [{ id: 'merged', label: 'Merged', tone: 'merged' }] }))
        .open,
    ).toBe(true);
  });
  it('an open Mission that is not running is still not finished', () => {
    expect(
      trashGate(row({ tab: 'open', pills: [{ id: 'needs', label: 'Needs you', tone: 'needs' }] })),
    ).toEqual({
      open: false,
      reason: 'Only a finished or cancelled Mission can be deleted.',
    });
  });
});

describe('Swarm: the exact model and provider per role, observed apart from configured (gap 4, D23, AC-4.4-19)', () => {
  const obs = (
    state: DiObservedValue['state'],
    value: string | null,
    reason: string | null = null,
  ): DiObservedValue => ({ state, value, reason });
  it('an observed model on a live member is said to be observed', () => {
    expect(modelFact(obs('observed', 'gpt-5.5'), true)).toEqual({
      kind: 'observed',
      value: 'gpt-5.5',
      text: 'Model gpt-5.5 (observed)',
    });
  });
  it('an observed model on a member that is not live is said to be stale, never current', () => {
    expect(modelFact(obs('observed', 'gpt-5.5'), false)).toEqual({
      kind: 'stale',
      value: 'gpt-5.5',
      text: 'Model gpt-5.5 (last seen, not live)',
    });
  });
  it('unknown, missing or unavailable stays so: never a guess, never a blank', () => {
    expect(modelFact(obs('observed', 'unknown'), true).text).toBe('Model not reported');
    expect(modelFact(obs('observed', ''), true).text).toBe('Model not reported');
    expect(modelFact(obs('not_recorded', null), true)).toEqual({
      kind: 'not_recorded',
      value: null,
      text: 'Model not recorded',
    });
    expect(modelFact(obs('known_empty', null), true)).toEqual({
      kind: 'none_reported',
      value: null,
      text: 'Model none reported',
    });
    expect(modelFact(obs('unavailable', null, 'catalog_down'), true)).toEqual({
      kind: 'unavailable',
      value: null,
      text: 'Model unavailable',
    });
    expect(modelFact(obs('projected_reason', null, 'v1'), true).text).toBe('Model unavailable');
    expect(modelFact(obs('not_applicable', null), true).text).toBe(
      'Model does not apply to this source',
    );
  });
  it('the provider is a configured fact, and is said to be configured', () => {
    expect(providerFact('codex')).toEqual({
      kind: 'configured',
      value: 'codex',
      text: 'Provider codex (configured)',
    });
    expect(providerFact('  ')).toEqual({
      kind: 'not_recorded',
      value: null,
      text: 'Provider not recorded',
    });
  });
});

describe('V-4: every real Mission row carries its status as a dot AND a word (CONTROL-MAP "01 Home": Status dot+word; AC-UX-10)', () => {
  const office = (missions: unknown[], attention: string[] = []) =>
    ({
      live: true,
      notLiveReason: null,
      envelope: { envelopeProblem: null, retryAfterSeconds: null, fresh: true },
      missions: { state: 'observed', reason: null, missions },
      reception: { entries: attention.map((missionRef) => ({ missionRef })) },
    }) as unknown as DiOfficeState;
  const base = {
    sourceId: 'herd',
    sourceKind: 'herdr_task',
    lifecycleModel: 'herdr.task/1',
    bindingReason: 'canonical_chain_complete',
    producerDeclared: 'herdctl',
    live: true,
    notLiveReason: null,
    objective: { state: 'observed', value: 'A task', reason: null },
    updatedAt: { state: 'not_recorded', value: null, reason: null },
    identitySource: { state: 'not_recorded', value: null, reason: null },
    paused: { state: 'not_applicable', value: null, reason: null },
    cancelRequested: { state: 'not_applicable', value: null, reason: null },
    ambiguity: { state: 'not_applicable', value: null, reason: null },
    v2: { projected: false },
  };
  const live = {
    characterId: 1,
    coverage: 'direct',
    memberRef: 'm',
    logical: 'lead1',
    role: 'lead',
    agent: 'w',
    provider: 'claude',
    classification: 'present',
    activity: 'busy',
    live: true,
    chain: 'direct',
    chainTaskId: null,
  };
  const row = (id: string, partial: Record<string, unknown>, attention: string[] = []) =>
    scanRowsFromOffice(
      office([{ ...base, missionRef: id, nativeId: id, ...partial }], attention),
    )[0]!;
  const words = (r: { pills: Array<{ label: string }> }) => r.pills.map((p) => p.label);

  it('a Mission with a confirmed live team is Building; with attention it also Needs you', () => {
    const running = {
      lifecycleState: 'ACTIVE',
      standing: 'active',
      bindingState: 'bound',
      members: { state: 'observed', reason: null, members: [live] },
    };
    expect(words(row('r', running))).toEqual(['Building']);
    expect(words(row('r', running, ['r']))).toEqual(['Needs you', 'Building']);
  });
  it('a Mission whose team is not observed says Unknown, never nothing', () => {
    const r = row('u', {
      lifecycleState: 'ACTIVE',
      standing: 'active',
      bindingState: 'bound',
      members: { state: 'observed', reason: null, members: [] },
    });
    expect(words(r)).toEqual(['Unknown']);
  });
  it('an inactive Mission says Waiting', () => {
    expect(
      words(
        row('w', {
          lifecycleState: 'IDLE',
          standing: 'inactive',
          bindingState: 'bound',
          members: { state: 'observed', reason: null, members: [] },
        }),
      ),
    ).toEqual(['Waiting']);
  });
  it('an ended Mission says Ended; a stopped one says Stopped', () => {
    expect(
      words(
        row('e', {
          lifecycleState: 'COMPLETE',
          standing: 'terminal',
          bindingState: 'not_applicable',
          members: { state: 'observed', reason: null, members: [] },
        }),
      ),
    ).toEqual(['Ended']);
    expect(
      words(
        row('s', {
          lifecycleState: 'ABORTED',
          standing: 'terminal',
          bindingState: 'not_applicable',
          members: { state: 'observed', reason: null, members: [] },
        }),
      ),
    ).toEqual(['Stopped']);
  });
  it('every status pill has a tone the stylesheet draws a dot for', () => {
    const tones = ['needs', 'building', 'merged', 'pass', 'failed', 'idle'];
    for (const r of [
      row('a', {
        lifecycleState: 'ACTIVE',
        standing: 'active',
        bindingState: 'bound',
        members: { state: 'observed', reason: null, members: [] },
      }),
      row('b', {
        lifecycleState: 'IDLE',
        standing: 'inactive',
        bindingState: 'bound',
        members: { state: 'observed', reason: null, members: [] },
      }),
      row('c', {
        lifecycleState: 'ABORTED',
        standing: 'terminal',
        bindingState: 'not_applicable',
        members: { state: 'observed', reason: null, members: [] },
      }),
    ])
      for (const p of r.pills) expect(tones, p.label).toContain(p.tone);
  });
});

describe('REVIEW FINDING 1: a state fact this build does not read is UNREAD (null), never false', () => {
  it('a production row reads "running" from the record and leaves approval, repository request, stall and log unread', () => {
    const r = productionRow('p', {}, ['p']);
    expect(r.needsYou).toEqual({ known: true, value: true });
    expect(r.state).toEqual({
      source: 'record',
      running: { known: true, value: false },
      awaitingApproval: null,
      repoRequest: null,
      stalled: null,
      logRecorded: null,
    });
    expect(unreadStateFacts(r.state)).toEqual([
      'approvals',
      'repository requests',
      'stalls',
      'logs',
    ]);
    // nothing unread is turned into an action, and nothing is invented
    expect(stateActions(r.state)).toEqual([]);
  });
  it('labelled sample state reads every fact, so it has nothing unread', () => {
    for (const row of DI_SCAN_LOCK) expect(unreadStateFacts(row.state), row.id).toEqual([]);
  });
});
