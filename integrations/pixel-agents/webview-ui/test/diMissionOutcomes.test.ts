/**
 * (c), owner decision: in the request workspace the person may DECLINE, REJECT or REQUEST CHANGES on the proposal shown, beside
 * approving it. What is pinned here, without a browser:
 *
 *  - each of the three is sent ONLY with exactly the offer on display (the same binding a decision sends, without the typed word),
 *    and a change request carries the person's reason UNCHANGED, and only when it is readable;
 *  - nothing is sent while the display is stale, the window cannot vouch for what it holds, no decision is offered, the offer is
 *    missing or expired, or any decision on that request is already waiting for its answer;
 *  - the three are WRITES (an answer that never came back is "not known", never a refusal);
 *  - the reason the person typed is kept until it is sent, and cleared when the answer comes back or another request is chosen;
 *  - a refusal caused by a stale or expired offer says nothing was RECORDED (not "approved"), and the two new refusal codes have words;
 *  - the decision block shows the three controls, each available exactly when it may be sent, with Reject needing a second step;
 *  - a recorded change request's reason is shown back as the person's words, as inert text.
 *
 * Markup is static (renderToStaticMarkup); the state is the real model's.
 */
import type { FunctionComponent } from 'react';
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';

import type { DiMissionRequestReply } from '../../core/src/messages.ts';
import * as component from '../src/components/DiMissionRequest.tsx';
import * as model from '../src/components/diMissionRequestModel.ts';
import type { DiMissionRequestControl } from '../src/hooks/useDiMissionRequest.ts';

type Any = Record<string, any>; // eslint-disable-line @typescript-eslint/no-explicit-any
const rawModel = model as unknown as Any;
const rawComponent = component as unknown as Any;
type Fn = (...args: any[]) => any; // eslint-disable-line @typescript-eslint/no-explicit-any
/** A named export that (c) adds: its absence FAILS (an assertion), never errors. */
const need = (from: Any, name: string): Fn => {
  expect(typeof from[name], `${name} is exported`).toBe('function');
  return from[name] as Fn;
};
/** The model, with each function (c) adds reached through `need`. */
const m: Any = new Proxy(rawModel, {
  get: (t, k) =>
    typeof k === 'string' &&
    ['outcomeFields', 'outcomeRequest', 'changeReasonProblem', 'decisionPending'].includes(k)
      ? need(t, k)
      : t[k as string],
});
/** A component (c) adds, reached through `need`. */
const needComponent = (name: string): FunctionComponent<Any> =>
  need(rawComponent, name) as unknown as FunctionComponent<Any>;

const REF = `lr-${'a'.repeat(32)}`;
const REF2 = `lr-${'b'.repeat(32)}`;
const MISSION = `mn-${'c'.repeat(32)}`;
const DIGEST = 'd'.repeat(64);
const OFFER_DIGEST = 'f'.repeat(64);
const REASON = 'Narrow the scope to the readiness probe.\nAdd a regression test for the flake.';
const OUTCOME_OPS = ['decline', 'reject', 'requestChanges'] as const;

// ── the words, copied here, never read from the code under test ───────────────────────────────────────────────────────────────
const NOTHING_RECORDED_STALE =
  'The offer you were shown is no longer the offer of record: the proposal changed, was withdrawn or replaced, or the offer was not the one issued. Nothing was recorded. Read the current proposal, then decide on that.';
const NOTHING_RECORDED_EXPIRED =
  'The window you were shown has passed. Nothing about the proposal changed and nothing was recorded. Read the offer again for a new window, then decide.';
const APPROVAL_STALE =
  'The offer you were shown is no longer the offer of record: the proposal changed, was withdrawn or replaced, or the offer was not the one issued. Nothing was approved. Read the current proposal, then decide on that.';
const REASON_REFUSED =
  'A change request needs a reason: write what should change, in at most 2000 characters. Nothing was recorded.';
const CONFLICT_REFUSED =
  'This request already holds a different decision, so this one was not recorded. Read the request again to see what is of record.';
const ATTESTED_NOTE =
  'Each of these is recorded the way an approval is: as an operator attestation of your choice, not independently verified.';
const REASON_LABEL = 'What should change (recorded exactly as you write it)';

const proposal = (): Record<string, unknown> => ({
  objective: 'Make the probe stable',
  target_context: 'readiness',
  repository_url: 'https://github.com/Example/Repo',
  requested_scope: 'the probe only',
  requested_action_scope: ['engineering_change', 'repository_read'],
  requested_delivery_target: null,
  proof_contract: {
    requirements: [
      {
        key: 'run_result',
        description: 'The result is bound to the run.',
        evidence_kinds: ['verification_record'],
        required_artifact_keys: [],
        max_evidence_age_seconds: 3600,
      },
    ],
    required_artifacts: [],
    required_dependencies: [],
    required_resource_readiness: [],
    degradation_policy: { permitted_blocker_keys: [] },
    continuation_budget: { max_attempts: 1, max_checkpoints: 2 },
  },
});
const lifecycle = (over: Record<string, unknown> = {}): Record<string, unknown> => ({
  stage: 'of_record',
  label: 'Waiting for your decision',
  detail: 'Nothing has been approved.',
  terminal: false,
  actions: ['decide', 'withdraw'],
  notice: null,
  approval_expires_at: null,
  provenance: null,
  residual_risk: null,
  ...over,
});
const wireRequest = (over: Record<string, unknown> = {}): Record<string, unknown> => ({
  ok: true,
  request_ref: REF,
  mission_id: MISSION,
  revision: 1,
  proposal: proposal(),
  proposal_digest_sha256: DIGEST,
  created_at: 100,
  lifecycle: lifecycle(),
  lineage: { supersedes: null, superseded_by: null, turn_id: null },
  cancellation: null,
  attestation: null,
  outcome: null,
  run: null,
  ...over,
});
const wireOffer = (): Record<string, unknown> => ({
  ok: true,
  request_ref: REF,
  mission_id: MISSION,
  revision: 1,
  proposal_digest_sha256: DIGEST,
  offer_digest_sha256: OFFER_DIGEST,
  expires_at: 1900,
  issued_at: 1000,
  approved_action_scope: ['engineering_change', 'repository_read'],
  approved_delivery_targets: [],
  repository_url: 'https://github.com/Example/Repo',
  approval_valid_seconds: 900,
  server_now: 1000,
});
const reply = (
  op: string,
  seq: number,
  state: DiMissionRequestReply['state'],
  answer: Record<string, unknown> | null,
  problem: string | null = null,
  reason: string | null = null,
): DiMissionRequestReply =>
  ({ type: 'diMissionRequestReply', seq, op, state, problem, reason, answer }) as DiMissionRequestReply;
const apply = (state: Any, ...events: Any[]): Any => events.reduce((s, e) => m.reduce(s, e), state);
/** REF read once, selected, with its frozen offer read. */
const offered = (wire: Record<string, unknown> = wireRequest()): Any =>
  apply(
    m.INITIAL_STATE,
    { type: 'select', requestRef: REF },
    { type: 'sent', seq: 1, op: 'request', requestRef: REF, turnId: null },
    { type: 'reply', nowMs: 1000, reply: reply('request', 1, 'ok', wire) },
    { type: 'sent', seq: 2, op: 'offer', requestRef: REF, turnId: null },
    { type: 'reply', nowMs: 1100, reply: reply('offer', 2, 'ok', wireOffer()) },
  );
const binding = {
  requestRef: REF,
  missionId: MISSION,
  revision: 1,
  digest: DIGEST,
  offerDigest: OFFER_DIGEST,
  expiresAt: 1900,
  scope: ['engineering_change', 'repository_read'],
  deliveryTargets: [],
  repositoryUrl: 'https://github.com/Example/Repo',
};

describe('the three outcomes are sent only on exactly the offer shown', () => {
  it('the outcome operations are exactly three, and each is a WRITE', () => {
    expect(m.OUTCOME_OPS).toEqual([...OUTCOME_OPS]);
    for (const op of OUTCOME_OPS) expect(m.isWrite(op), op).toBe(true);
    expect(m.MR_MAX_CHANGE_REASON_CHARS).toBe(2000);
  });

  it("each sends the decision's binding without a typed word; a change request adds the reason, unchanged", () => {
    const view = offered().views[REF];
    expect(m.outcomeFields(view, 'decline', '')).toEqual(binding);
    expect(m.outcomeFields(view, 'reject', '')).toEqual(binding);
    expect(m.outcomeFields(view, 'requestChanges', REASON)).toEqual({ ...binding, reason: REASON });
    const spaced = `  ${REASON}  `;
    expect(m.outcomeFields(view, 'requestChanges', spaced)).toEqual({ ...binding, reason: spaced });
    for (const op of OUTCOME_OPS) expect(m.outcomeFields(view, op, REASON), op).not.toHaveProperty('reply');
  });

  it('a change request is never sent without a readable reason', () => {
    const view = offered().views[REF];
    const bad = ['', '   ', '\n\t', 'x'.repeat(2001), 'a\u0000b', 'a\u001bb'];
    expect(bad).toHaveLength(6);
    for (const reason of bad) {
      expect(m.outcomeFields(view, 'requestChanges', reason), JSON.stringify(reason)).toBeNull();
      expect(m.changeReasonProblem(reason), JSON.stringify(reason)).not.toBeNull();
    }
    expect(m.changeReasonProblem('y'.repeat(2000))).toBeNull();
    expect(m.outcomeFields(view, 'requestChanges', 'y'.repeat(2000))).toMatchObject({ reason: 'y'.repeat(2000) });
  });

  it('with no offer on display, or an offer for another proposal, there is nothing to send', () => {
    const noOffer = apply(
      m.INITIAL_STATE,
      { type: 'select', requestRef: REF },
      { type: 'sent', seq: 1, op: 'request', requestRef: REF, turnId: null },
      { type: 'reply', nowMs: 1000, reply: reply('request', 1, 'ok', wireRequest()) },
    );
    for (const op of OUTCOME_OPS) expect(m.outcomeFields(noOffer.views[REF], op, REASON), op).toBeNull();
    const view = offered().views[REF];
    const other = { ...view, offer: { ...view.offer, revision: 2 } };
    for (const op of OUTCOME_OPS) expect(m.outcomeFields(other, op, REASON), op).toBeNull();
  });

  it('outcomeRequest holds every one back whenever the window may not decide', () => {
    const s: Any = { ...offered(), changeReason: REASON };
    // Round 61: Reject also needs its own first step (the tests below), so only these two are sendable from here.
    for (const op of ['decline', 'requestChanges'] as const) {
      expect(m.outcomeRequest(s, op, 1000), op).toEqual(m.outcomeFields(s.views[REF], op, REASON));
    }
    const holds: Array<[string, Any, number | null]> = [
      ['stale', { ...s, views: { [REF]: { ...s.views[REF], stale: true } } }, 1000],
      ['unvouched', { ...s, unvouched: 'disconnected' }, 1000],
      ['nothing selected', { ...s, selectedRef: null }, 1000],
      [
        'no decision offered',
        { ...offered(wireRequest({ lifecycle: lifecycle({ actions: ['withdraw'] }) })), changeReason: REASON },
        1000,
      ],
      ['offer expired', s, 1900],
      ['a decision pending', apply(s, { type: 'sent', seq: 9, op: 'decide', requestRef: REF, turnId: null }), 1000],
      ['a decline pending', apply(s, { type: 'sent', seq: 9, op: 'decline', requestRef: REF, turnId: null }), 1000],
    ];
    expect(holds).toHaveLength(7);
    for (const [why, state, now] of holds) {
      for (const op of OUTCOME_OPS) expect(m.outcomeRequest(state, op, now), `${why}: ${op}`).toBeNull();
    }
    // and a pending outcome holds the approval too
    const pending = apply(s, { type: 'sent', seq: 9, op: 'reject', requestRef: REF, turnId: null });
    expect(m.decisionPending(pending, REF)).toBe(true);
    expect(m.decisionPending(s, REF)).toBe(false);
  });
});

// ── Round 61: what an input or a confirmation belongs to ────────────────────────────────────────────────────────────────────────
const REF_B = REF2;
const MISSION_B = `mn-${'9'.repeat(32)}`;
const DIGEST_B = 'b'.repeat(64);
const OFFER_DIGEST_B = 'a'.repeat(64);
/** One reducer step that must yield a state: an event the reducer does not know FAILS here, by assertion. */
const step = (state: Any, event: Any): Any => {
  const next = rawModel.reduce(state, event);
  expect(next, `the reducer knows "${event.type}"`).toBeDefined();
  return next;
};
/** A and B both read, each with its own frozen offer; `selected` is chosen last. */
const twoOffered = (selected: string): Any =>
  apply(
    m.INITIAL_STATE,
    { type: 'sent', seq: 1, op: 'request', requestRef: REF, turnId: null },
    { type: 'reply', nowMs: 1000, reply: reply('request', 1, 'ok', wireRequest()) },
    { type: 'sent', seq: 2, op: 'offer', requestRef: REF, turnId: null },
    { type: 'reply', nowMs: 1100, reply: reply('offer', 2, 'ok', wireOffer()) },
    { type: 'sent', seq: 3, op: 'request', requestRef: REF_B, turnId: null },
    {
      type: 'reply',
      nowMs: 1000,
      reply: reply('request', 3, 'ok', wireRequest({ request_ref: REF_B, mission_id: MISSION_B, proposal_digest_sha256: DIGEST_B })),
    },
    { type: 'sent', seq: 4, op: 'offer', requestRef: REF_B, turnId: null },
    {
      type: 'reply',
      nowMs: 1100,
      reply: reply('offer', 4, 'ok', {
        ...wireOffer(),
        request_ref: REF_B,
        mission_id: MISSION_B,
        proposal_digest_sha256: DIGEST_B,
        offer_digest_sha256: OFFER_DIGEST_B,
      }),
    },
    { type: 'select', requestRef: selected },
  );
const ARM_A = { requestRef: REF, missionId: MISSION, revision: 1, digest: DIGEST, offerDigest: OFFER_DIGEST, expiresAt: 1900 };

describe('round 61: Reject is confirmed for exactly what it rejects', () => {
  it('Reject is sendable only after a first step armed it for exactly this request, proposal and offer', () => {
    const s0: Any = { ...offered(), changeReason: REASON };
    expect(need(rawModel, 'outcomeAvailable')(s0, 'reject', 1000), 'the first step is available').toBe(true);
    expect(m.outcomeRequest(s0, 'reject', 1000), 'unarmed, nothing is sent').toBeNull();
    expect(need(rawModel, 'rejectArmFor')(s0.views[REF])).toEqual(ARM_A);
    const armed = step(s0, { type: 'armReject', arm: ARM_A });
    expect(need(rawModel, 'rejectArmed')(armed, armed.views[REF])).toBe(true);
    expect(m.outcomeRequest(armed, 'reject', 1000)).toEqual(binding);
    // stepping back disarms it
    const kept = step(armed, { type: 'disarmReject' });
    expect(m.outcomeRequest(kept, 'reject', 1000)).toBeNull();
  });

  it('an arm formed on one request never authorizes another, and does not survive a new offer', () => {
    const onA = step(twoOffered(REF), { type: 'armReject', arm: ARM_A });
    // choosing B: the arm does not follow, and B's Reject is not sendable
    const onB = step(onA, { type: 'select', requestRef: REF_B });
    expect(onB.rejectArm, 'selecting another request drops the arm').toBeNull();
    expect(m.outcomeRequest(onB, 'reject', 1000)).toBeNull();
    // even an arm still held for A authorizes nothing on B
    const heldForA: Any = { ...onB, rejectArm: ARM_A };
    expect(need(rawModel, 'rejectArmed')(heldForA, heldForA.views[REF_B])).toBe(false);
    expect(m.outcomeRequest(heldForA, 'reject', 1000)).toBeNull();
    // and on A itself, a different offer (the same proposal, a new offer digest) is not the one armed
    const reoffered: Any = { ...onA, views: { ...onA.views, [REF]: { ...onA.views[REF], offer: { ...onA.views[REF].offer, offerDigest: '7'.repeat(64) } } } };
    expect(need(rawModel, 'rejectArmed')(reoffered, reoffered.views[REF])).toBe(false);
    expect(m.outcomeRequest(reoffered, 'reject', 1000)).toBeNull();
  });

  it('the confirmation is on screen only for the request, proposal and offer it was armed for', () => {
    const noop = (): void => undefined;
    const control = (state: Any): DiMissionRequestControl =>
      new Proxy({ state } as Any, {
        get: (t, k) => (typeof k === 'string' && k in t ? t[k] : noop),
      }) as unknown as DiMissionRequestControl;
    const render = (state: Any, ref: string): string =>
      renderToStaticMarkup(createElement(needComponent('DecisionBlock'), { view: state.views[ref], control: control(state), nowMs: 0 }));
    const confirm = (html: string): string | null => /<button[^>]*data-testid="di-mr-reject-confirm"[^>]*>/.exec(html)?.[0] ?? null;
    const onB: Any = { ...twoOffered(REF_B), rejectArm: ARM_A };
    expect(confirm(render(onB, REF_B)), 'an arm for A shows no confirmation on B').toBeNull();
    const armedA = step(twoOffered(REF), { type: 'armReject', arm: ARM_A });
    const shown = confirm(render(armedA, REF));
    expect(shown, 'armed for A, A shows its confirmation').not.toBeNull();
    expect(shown).not.toMatch(/aria-disabled="true"/);
  });
});

describe('round 61: a late answer clears only its own request’s input', () => {
  const answerFor = (op: string, seq: number, ref: string, mission: string, digest: string): Any => ({
    type: 'reply',
    nowMs: 1300,
    reply: reply(op, seq, 'ok', {
      ok: true,
      outcome: { kind: 'request_changes', resulting_state: 'CHANGES_REQUESTED', idempotent: false },
      request: wireRequest({
        request_ref: ref,
        mission_id: mission,
        proposal_digest_sha256: digest,
        lifecycle: lifecycle({ stage: 'changes_requested', label: 'Changes requested', actions: [] }),
        outcome: { kind: 'request_changes', state: 'APPLIED', reason: REASON },
      }),
    }),
  });
  const B_REASON = 'B: keep the probe, drop the retry.';

  it("A's answer arriving while B is chosen leaves B's unsent reason; B's own answer clears it", () => {
    let s = apply(
      { ...twoOffered(REF), changeReason: REASON },
      { type: 'sent', seq: 20, op: 'requestChanges', requestRef: REF, turnId: null },
      { type: 'select', requestRef: REF_B },
      { type: 'changeReason', text: B_REASON },
      answerFor('requestChanges', 20, REF, MISSION, DIGEST),
    );
    expect(s.changeReason, "A's answer leaves B's unsent reason").toBe(B_REASON);
    expect(s.views[REF].latest.lifecycle.stage).toBe('changes_requested');
    s = apply(
      s,
      { type: 'sent', seq: 21, op: 'requestChanges', requestRef: REF_B, turnId: null },
      answerFor('requestChanges', 21, REF_B, MISSION_B, DIGEST_B),
    );
    expect(s.changeReason, "B's own answer clears B's reason").toBe('');
  });

  it("A's answer leaves an arm formed on B; B's own answer drops it", () => {
    const ARM_B = { requestRef: REF_B, missionId: MISSION_B, revision: 1, digest: DIGEST_B, offerDigest: OFFER_DIGEST_B, expiresAt: 1900 };
    let s = apply(twoOffered(REF), { type: 'sent', seq: 20, op: 'requestChanges', requestRef: REF, turnId: null }, { type: 'select', requestRef: REF_B });
    s = step(s, { type: 'armReject', arm: ARM_B });
    s = apply(s, answerFor('requestChanges', 20, REF, MISSION, DIGEST));
    expect(s.rejectArm, "A's answer leaves B's arm").toEqual(ARM_B);
    s = apply(
      s,
      { type: 'sent', seq: 21, op: 'requestChanges', requestRef: REF_B, turnId: null },
      answerFor('requestChanges', 21, REF_B, MISSION_B, DIGEST_B),
    );
    expect(s.rejectArm, "B's own answer drops B's arm").toBeNull();
  });
});

describe('the reason, the answers and the words', () => {
  it('the reason is kept as typed, and cleared when another request is chosen or the answer comes back', () => {
    const typed = apply(offered(), { type: 'changeReason', text: REASON });
    // The reducer must know the event: today an unknown event yields no state at all.
    expect(typed, 'the reducer keeps a typed reason').toMatchObject({ changeReason: REASON });
    expect(apply(typed, { type: 'select', requestRef: REF2 }).changeReason).toBe('');
    const answered = apply(
      typed,
      { type: 'sent', seq: 5, op: 'requestChanges', requestRef: REF, turnId: null },
      {
        type: 'reply',
        nowMs: 1200,
        reply: reply('requestChanges', 5, 'ok', {
          ok: true,
          outcome: { kind: 'request_changes', resulting_state: 'CHANGES_REQUESTED', idempotent: false },
          request: wireRequest({
            lifecycle: lifecycle({ stage: 'changes_requested', label: 'Changes requested', actions: [] }),
            outcome: { kind: 'request_changes', state: 'APPLIED', reason: REASON },
          }),
        }),
      },
    );
    expect(answered.changeReason).toBe('');
    expect(answered.pending).toEqual([]);
    expect(answered.views[REF].latest.lifecycle.stage).toBe('changes_requested');
    expect(answered.views[REF].latest.outcome).toEqual({ kind: 'request_changes', state: 'APPLIED', reason: REASON });
  });

  it('an answer that never came back is NOT KNOWN for each outcome, never a refusal', () => {
    for (const op of OUTCOME_OPS) {
      const s = apply(
        offered(),
        { type: 'sent', seq: 6, op, requestRef: REF, turnId: null },
        { type: 'reply', nowMs: 1200, reply: reply(op, 6, 'unknown', null, 'panel_timeout', 'Whether it applied this request is not known; read the record again before acting.') },
      );
      expect(s.notices[0], op).toMatchObject({ kind: 'unknown', op });
    }
  });

  it('a stale or expired offer refusing an outcome says nothing was recorded; for an approval it still says nothing was approved', () => {
    for (const op of OUTCOME_OPS) {
      expect(m.problemWords('mission_panel_display_stale', null, op), op).toBe(NOTHING_RECORDED_STALE);
      expect(m.problemWords('mission_panel_offer_expired', null, op), op).toBe(NOTHING_RECORDED_EXPIRED);
    }
    expect(m.problemWords('mission_panel_display_stale', null, 'decide')).toBe(APPROVAL_STALE);
    expect(m.problemWords('mission_panel_display_stale', null)).toBe(APPROVAL_STALE);
    const refused = apply(
      offered(),
      { type: 'sent', seq: 7, op: 'decline', requestRef: REF, turnId: null },
      { type: 'reply', nowMs: 1200, reply: reply('decline', 7, 'refused', {}, 'mission_panel_display_stale', 'x') },
    );
    expect(refused.notices[0]).toMatchObject({ kind: 'refusal', op: 'decline', text: NOTHING_RECORDED_STALE });
    expect(refused.views[REF].stale).toBe(true);
  });

  it('the two refusals (c) adds have words of their own', () => {
    expect(m.problemWords('local_request_change_request_reason', 'the reason is blank')).toBe(REASON_REFUSED);
    expect(m.problemWords('local_request_attestation_conflict', 'request already holds')).toBe(CONFLICT_REFUSED);
  });
});

describe('the decision block shows the three controls', () => {
  const noop = (): void => undefined;
  const control = (state: Any): DiMissionRequestControl =>
    new Proxy({ state } as Any, {
      get: (t, k) => (typeof k === 'string' && k in t ? t[k] : noop),
    }) as unknown as DiMissionRequestControl;
  const render = (state: Any): string =>
    renderToStaticMarkup(
      createElement(needComponent('DecisionBlock'), { view: state.views[REF], control: control(state), nowMs: 0 }),
    );
  const tag = (html: string, id: string): string | null =>
    new RegExp(`<(button|textarea)[^>]*data-testid="${id}"[^>]*>`).exec(html)?.[0] ?? null;
  const off = (html: string, id: string): boolean => /aria-disabled="true"/.test(tag(html, id) ?? '');
  const CONTROLS = ['di-mr-decline', 'di-mr-reject', 'di-mr-request-changes'];
  const clock = (state: Any, serverNow: number): Any => ({
    ...state,
    overview: m.parseOverview({ server_now: serverNow, capabilities: { decision: {}, run_available: false }, requests: [] }),
    overviewAtMs: 0,
  });

  it('with a live offer: decline and reject are available, ask-for-changes waits for a reason, and the provenance is said', () => {
    const html = render(clock(offered(), 1000));
    expect(CONTROLS).toHaveLength(3);
    for (const id of CONTROLS) expect(tag(html, id), id).not.toBeNull();
    expect(off(html, 'di-mr-decline')).toBe(false);
    expect(off(html, 'di-mr-reject')).toBe(false);
    expect(off(html, 'di-mr-request-changes')).toBe(true);
    expect(tag(html, 'di-mr-change-reason')).toMatch(/maxLength="2000"|maxlength="2000"/);
    expect(html).toContain(REASON_LABEL);
    expect(html).toContain(ATTESTED_NOTE);
    // Reject needs a second, explicit step: its confirmation is not on screen until asked for.
    expect(tag(html, 'di-mr-reject-confirm')).toBeNull();
  });

  it('with a readable reason, ask-for-changes is available', () => {
    const html = render(clock({ ...offered(), changeReason: REASON }, 1000));
    expect(off(html, 'di-mr-request-changes')).toBe(false);
  });

  it('with the offer expired, or a decision waiting for its answer, none of the three is available', () => {
    const expired = render(clock({ ...offered(), changeReason: REASON }, 1900));
    const waiting = render(
      clock(apply({ ...offered(), changeReason: REASON }, { type: 'sent', seq: 9, op: 'decline', requestRef: REF, turnId: null }), 1000),
    );
    for (const id of CONTROLS) {
      expect(off(expired, id), `expired ${id}`).toBe(true);
      expect(off(waiting, id), `waiting ${id}`).toBe(true);
    }
  });

  it("a recorded change request's reason is shown as the person's words, inert", () => {
    const s = offered(
      wireRequest({
        lifecycle: lifecycle({ stage: 'changes_requested', label: 'Changes requested', actions: [] }),
        outcome: { kind: 'request_changes', state: 'APPLIED', reason: '<b>not markup</b>' },
      }),
    );
    const reasonView = needComponent('ChangeRequestReason');
    const html = renderToStaticMarkup(createElement(reasonView, { request: s.views[REF].latest }));
    expect(html).toContain('data-testid="di-mr-change-reason-recorded"');
    expect(html).toContain('&lt;b&gt;not markup&lt;/b&gt;');
    expect(html).not.toContain('<b>');
    const none = renderToStaticMarkup(createElement(reasonView, { request: offered().views[REF].latest }));
    expect(none).toBe('');
  });
});

// ── Round 61 sweep: the same two shapes, found elsewhere in this workspace (they predate (c)) ─────────────────────────────────────
describe('round 61 sweep: Withdraw is confirmed for exactly what it withdraws', () => {
  const BIND_A = { requestRef: REF, missionId: MISSION, revision: 1, digest: DIGEST };
  const withdrawable = (selected: string): Any => twoOffered(selected); // both offer `decide` and `withdraw`

  it('Withdraw is sent only after its first step armed it for exactly this request and proposal', () => {
    const s0 = withdrawable(REF);
    expect(need(rawModel, 'withdrawRequest')(s0), 'unarmed, nothing is sent').toBeNull();
    expect(need(rawModel, 'withdrawArmFor')(s0.views[REF])).toEqual(BIND_A);
    const armed = step(s0, { type: 'armWithdraw', arm: BIND_A });
    expect(need(rawModel, 'withdrawArmed')(armed, armed.views[REF])).toBe(true);
    expect(need(rawModel, 'withdrawRequest')(armed)).toEqual(BIND_A);
    expect(need(rawModel, 'withdrawRequest')(step(armed, { type: 'disarmWithdraw' }))).toBeNull();
  });

  it('an arm formed on one request never authorizes another', () => {
    const onA = step(withdrawable(REF), { type: 'armWithdraw', arm: BIND_A });
    const onB = step(onA, { type: 'select', requestRef: REF_B });
    expect(onB.withdrawArm, 'selecting another request drops the arm').toBeNull();
    const heldForA: Any = { ...onB, withdrawArm: BIND_A };
    expect(need(rawModel, 'withdrawArmed')(heldForA, heldForA.views[REF_B])).toBe(false);
    expect(need(rawModel, 'withdrawRequest')(heldForA)).toBeNull();
  });

  it('the confirmation is on screen only for the request and proposal it was armed for', () => {
    const noop = (): void => undefined;
    const control = (state: Any): DiMissionRequestControl =>
      new Proxy({ state } as Any, {
        get: (t, k) => (typeof k === 'string' && k in t ? t[k] : noop),
      }) as unknown as DiMissionRequestControl;
    const render = (state: Any, ref: string): string =>
      renderToStaticMarkup(
        createElement(needComponent('WithdrawBlock'), { view: state.views[ref], control: control(state), canRevise: true }),
      );
    const box = (html: string): boolean => html.includes('data-testid="di-mr-withdraw-confirm-box"');
    expect(box(render({ ...withdrawable(REF_B), withdrawArm: BIND_A }, REF_B)), 'an arm for A shows nothing on B').toBe(false);
    expect(box(render(step(withdrawable(REF), { type: 'armWithdraw', arm: BIND_A }), REF)), 'armed for A, A shows it').toBe(true);
  });
});

describe("round 61 sweep: an approval's late answer clears only its own request's word", () => {
  it("A's approval answer arriving while B is chosen leaves B's typed word", () => {
    const s = apply(
      twoOffered(REF),
      { type: 'sent', seq: 30, op: 'decide', requestRef: REF, turnId: null },
      { type: 'select', requestRef: REF_B },
      { type: 'typed', text: 'approve' },
      {
        type: 'reply',
        nowMs: 1300,
        reply: reply('decide', 30, 'ok', {
          ok: true,
          decision: { authorization_id: null, idempotent: false },
          request: wireRequest({ lifecycle: lifecycle({ stage: 'approved', label: 'Approved', actions: [] }) }),
        }),
      },
    );
    expect(s.typed, "A's answer leaves B's typed word").toBe('approve');
    expect(s.views[REF].latest.lifecycle.stage).toBe('approved');
  });
});

describe("round 62: a refusal or an offer clears only its own request's word, and still updates that request's view", () => {
  const WRITE_OPS = ['decide', ...OUTCOME_OPS];
  const refusal = (op: string, seq: number, problem: string): Any => ({
    type: 'reply',
    nowMs: 1300,
    reply: reply(op, seq, 'refused', null, problem),
  });
  const offerReply = (seq: number, over: Record<string, unknown>): Any => ({
    type: 'reply',
    nowMs: 1300,
    reply: reply('offer', seq, 'ok', { ...wireOffer(), ...over }),
  });
  const OTHER_PROPOSAL = 'e'.repeat(64);

  it("A's stale-display refusal while B is chosen marks A stale and leaves B's typed word; B's own clears it", () => {
    expect(WRITE_OPS).toEqual(['decide', 'decline', 'reject', 'requestChanges']);
    for (const op of WRITE_OPS) {
      let s = apply(
        twoOffered(REF),
        { type: 'sent', seq: 40, op, requestRef: REF, turnId: null },
        { type: 'select', requestRef: REF_B },
        { type: 'typed', text: 'approve' },
        refusal(op, 40, 'mission_panel_display_stale'),
      );
      expect(s.typed, `${op}: A's refusal leaves B's typed word`).toBe('approve');
      expect(s.views[REF].stale, `${op}: A's view is marked stale`).toBe(true);
      expect(s.views[REF_B].stale, `${op}: B's view is not`).toBe(false);
      s = apply(s, { type: 'sent', seq: 41, op, requestRef: REF_B, turnId: null }, refusal(op, 41, 'mission_panel_display_stale'));
      expect(s.typed, `${op}: B's own refusal clears B's word`).toBe('');
      expect(s.views[REF_B].stale, `${op}: B's view is marked stale`).toBe(true);
    }
  });

  it("A's expired-offer refusal while B is chosen drops only A's offer and leaves B's typed word; B's own clears it", () => {
    for (const op of WRITE_OPS) {
      let s = apply(
        twoOffered(REF),
        { type: 'sent', seq: 42, op, requestRef: REF, turnId: null },
        { type: 'select', requestRef: REF_B },
        { type: 'typed', text: 'approve' },
        refusal(op, 42, 'mission_panel_offer_expired'),
      );
      expect(s.typed, `${op}: A's refusal leaves B's typed word`).toBe('approve');
      expect(s.views[REF].offer, `${op}: A's offer is dropped`).toBeNull();
      expect(s.views[REF_B].offer?.offerDigest, `${op}: B's offer stays`).toBe(OFFER_DIGEST_B);
      s = apply(s, { type: 'sent', seq: 43, op, requestRef: REF_B, turnId: null }, refusal(op, 43, 'mission_panel_offer_expired'));
      expect(s.typed, `${op}: B's own refusal clears B's word`).toBe('');
      expect(s.views[REF_B].offer, `${op}: B's offer is dropped`).toBeNull();
    }
  });

  it("an offer for another proposal of A, arriving while B is chosen, marks A stale and leaves B's typed word; B's own clears it", () => {
    let s = apply(
      twoOffered(REF),
      { type: 'sent', seq: 44, op: 'offer', requestRef: REF, turnId: null },
      { type: 'select', requestRef: REF_B },
      { type: 'typed', text: 'approve' },
      offerReply(44, { proposal_digest_sha256: OTHER_PROPOSAL }),
    );
    expect(s.typed, "A's offer leaves B's typed word").toBe('approve');
    expect(s.views[REF].stale, "A's view is marked stale").toBe(true);
    expect(s.views[REF_B].stale, "B's view is not").toBe(false);
    s = apply(
      s,
      { type: 'sent', seq: 45, op: 'offer', requestRef: REF_B, turnId: null },
      offerReply(45, {
        request_ref: REF_B,
        mission_id: MISSION_B,
        proposal_digest_sha256: OTHER_PROPOSAL,
        offer_digest_sha256: OFFER_DIGEST_B,
      }),
    );
    expect(s.typed, "B's own offer for another proposal clears B's word").toBe('');
    expect(s.views[REF_B].stale, "B's view is marked stale").toBe(true);
  });

  it('a submission that moves the choice to the new request carries no reason or arm from the request chosen before', () => {
    const REF_NEW = `lr-${'7'.repeat(32)}`;
    let s = apply({ ...twoOffered(REF), changeReason: REASON }, { type: 'typed', text: 'approve' });
    s = step(s, { type: 'armReject', arm: ARM_A });
    s = step(s, { type: 'armWithdraw', arm: { requestRef: REF, missionId: MISSION, revision: 1, digest: DIGEST } });
    s = apply(
      s,
      { type: 'sent', seq: 46, op: 'submit', requestRef: null, turnId: null },
      {
        type: 'reply',
        nowMs: 1300,
        reply: reply('submit', 46, 'ok', {
          created: true,
          submission: { state: 'SUBMITTED' },
          request: wireRequest({ request_ref: REF_NEW }),
        }),
      },
    );
    expect(s.selectedRef, 'the new request is chosen').toBe(REF_NEW);
    expect(s.typed, 'the typed word does not follow').toBe('');
    expect(s.changeReason, "A's reason does not follow the choice to the new request").toBe('');
    expect(s.rejectArm, "A's armed Reject does not follow").toBeNull();
    expect(s.withdrawArm, "A's armed Withdraw does not follow").toBeNull();
  });

  it("reading the offer again, or taking the latest, for a request not chosen leaves the chosen request's word; for the chosen one it clears it", () => {
    let s = apply(twoOffered(REF_B), { type: 'typed', text: 'approve' }, { type: 'dropOffer', requestRef: REF });
    expect(s.typed, "dropping A's offer leaves B's word").toBe('approve');
    expect(s.views[REF].offer, "A's offer is dropped").toBeNull();
    s = apply(s, { type: 'acknowledge', requestRef: REF });
    expect(s.typed, "taking A's latest leaves B's word").toBe('approve');
    s = apply(s, { type: 'dropOffer', requestRef: REF_B });
    expect(s.typed, "dropping B's own offer clears B's word").toBe('');
    s = apply(s, { type: 'typed', text: 'approve' }, { type: 'acknowledge', requestRef: REF_B });
    expect(s.typed, "taking B's own latest clears B's word").toBe('');
  });
});
