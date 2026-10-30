/**
 * b3: the office delivery panel's MODEL, without a browser. Pure functions and a reducer; nothing here sends, renders or opens anything.
 *
 * What it pins (b3-trace-and-design.txt; b3-obligations-addendum-1.txt):
 *  - ONE id, entered by the person, pattern-checked before anything is sent, with NO SELECTABLE HISTORY and no enumeration (scope and
 *    minimization, not authentication: a pattern-valid id shows only that an identifier was supplied). A notice that names its
 *    delivery can keep an earlier id visible; that is not a list to choose from.
 *  - The adapter's eligibility, from the record: only COMMIT, PUSH or PR_CREATE, with a binding naming the displayed delivery and step.
 *    Never BASE_REFRESH. The adapter's two notes VERBATIM.
 *  - The LOCAL confirmation: only with an eligible step; cleared on any reply, any newer read and any change of id. The advance carries
 *    exactly the displayed binding. (The UI's freshness is hygiene; the SERVER guard is the control, adapter.py:112-132.)
 *  - After ANY advance reply: the next action is a status READ, never another advance; and nothing resends an advance.
 *  - `refused` is never worded as nothing happened, nothing changed, or safe to retry. office_busy is TWO codes, and the heading follows
 *    the CODE, never the prose.
 */
import { describe, expect, it } from 'vitest';

import type { DiDeliveryReply } from '../../core/src/messages.ts';
import * as model from '../src/components/diDeliveryModel.ts';

type Any = Record<string, any>; // eslint-disable-line @typescript-eslint/no-explicit-any
const m = model as unknown as Any;

const ID = `prd-${'a'.repeat(24)}`;
const OTHER = `prd-${'b'.repeat(24)}`;
const DIGEST = 'c'.repeat(64);
const LOCAL_NOTE =
  'This confirmation is a local check. The delivery was authorized once, by the human ceremony, for these steps. This confirmation is not recorded in the delivery record.';
const PERFORMS_NOTE =
  'This office performs ONE confirmed step at a time (COMMIT, PUSH or PR_CREATE; never the base refresh, never every step).  Spacing  and “quotes” stay.';
const binding = (over: Any = {}): Any => ({
  delivery_id: ID,
  revision: 4,
  candidate_identity_digest_sha256: DIGEST,
  step: 'COMMIT',
  ...over,
});
const office = (over: Any = {}): Any => ({
  eligible: { step: 'COMMIT', binding: binding() },
  not_eligible: null,
  action_steps: ['COMMIT', 'PUSH', 'PR_CREATE'],
  local_confirmation_note: LOCAL_NOTE,
  performs_steps: true,
  performs_steps_note: PERFORMS_NOTE,
  ...over,
});
const delivery = (over: Any = {}): Any => ({
  delivery_id: ID,
  revision: 4,
  phase: 'base_refreshed',
  authorization: { valid: true, problem: null, human_identity: 'travis', allowed_actions: ['commit', 'push', 'pr_create'], mode: 'pr_create' },
  base_refresh: { state: 'succeeded', receipt_id: 'rcpt-base-1', receipt_state: 'succeeded', attempts: 1, observed: null },
  commit: { state: 'pending', receipt_id: null, receipt_state: null, attempts: 0, observed: null, commit_oid: null },
  push: { state: 'pending', receipt_id: null, receipt_state: null, attempts: 0, observed: null, remote_oid: null },
  pr_url: null,
  blocker: null,
  next_action: { action: 'run_step', step: 'COMMIT' },
  ...over,
});
const statusAnswer = (d: Any = delivery(), o: Any = office()): Any => ({ ok: true, status: { delivery: d, office: o } });
const reply = (seq: number, op: 'status' | 'advanceOnce', state: DiDeliveryReply['state'], problem: string | null, reason: string | null, answer: Any | null): DiDeliveryReply =>
  ({ type: 'diDeliveryReply', seq, op, state, problem, reason, answer }) as DiDeliveryReply;

const fns = ['reduce', 'statusMessage', 'advanceMessage', 'followUpMessage', 'parseStatusView', 'busyHeading'];
const ready = (): boolean => fns.every((f) => typeof m[f] === 'function') && typeof m.INITIAL_DL_STATE === 'object' && m.INITIAL_DL_STATE !== null;

function run(...events: Any[]): Any {
  return events.reduce((s, e) => m.reduce(s, e), m.INITIAL_DL_STATE);
}
/** A state showing ID's status, read at seq 1. */
function shown(d: Any = delivery(), o: Any = office()): Any {
  return run(
    { type: 'entered', text: ID },
    { type: 'sent', seq: 1, op: 'status', deliveryId: ID },
    { type: 'reply', reply: reply(1, 'status', 'ok', null, null, statusAnswer(d, o)), nowMs: 1000 },
  );
}
// A claim that nothing happened, changed or ran is forbidden; the negation "does not establish that nothing ran" is required, and allowed.
const FORBIDDEN = /(?<!establish that )nothing (happened|changed|ran)|safe to (retry|try again)|try again|merge|release|deploy/i;

describe('the model module exists with its parts', () => {
  it('exports the reducer, the message builders, the parser and the busy heading', () => {
    expect(ready()).toBe(true);
  });
});

describe('one entered id, pattern-checked, and no selectable history', () => {
  it('builds a status read only for a pattern-valid id (surrounding spaces trimmed); never for anything else', () => {
    expect(ready()).toBe(true);
    const at = (text: string) => m.statusMessage(run({ type: 'entered', text }), 5);
    expect(at(`  ${ID} `)).toEqual({ type: 'diDeliveryRequest', seq: 5, op: 'status', deliveryId: ID });
    for (const bad of ['', 'prd-', `PRD-${'a'.repeat(24)}`, `prd-${'a'.repeat(23)}`, `prd-${'g'.repeat(24)}`, `${ID}x`, `${ID} ${OTHER}`]) {
      expect(at(bad), JSON.stringify(bad)).toBeNull();
    }
  });

  it('builds nothing while a request is waiting, or while the office cannot be vouched for', () => {
    expect(ready()).toBe(true);
    expect(m.statusMessage(run({ type: 'entered', text: ID }, { type: 'sent', seq: 1, op: 'status', deliveryId: ID }), 2)).toBeNull();
    expect(m.statusMessage(run({ type: 'entered', text: ID }, { type: 'unvouched', reason: 'disconnected' }), 2)).toBeNull();
  });

  it('with no notice naming it, nothing of a first id remains in the state once a second id is read', () => {
    expect(ready()).toBe(true);
    const s = run(
      { type: 'entered', text: ID },
      { type: 'sent', seq: 1, op: 'status', deliveryId: ID },
      { type: 'reply', reply: reply(1, 'status', 'ok', null, null, statusAnswer()), nowMs: 1 },
      { type: 'entered', text: OTHER },
      { type: 'sent', seq: 2, op: 'status', deliveryId: OTHER },
      { type: 'reply', reply: reply(2, 'status', 'ok', null, null, statusAnswer(delivery({ delivery_id: OTHER }), office({ eligible: null, not_eligible: { reason: 'complete', step: null, detail: 'The delivery is complete; there is no next step.' } }))), nowMs: 2 },
    );
    expect(s.view?.deliveryId).toBe(OTHER);
    expect(JSON.stringify(s)).not.toContain(ID);
  });
});

describe("the adapter's status, read from the record", () => {
  it('carries the two notes VERBATIM, the eligible step with its exact binding, and the steps as recorded', () => {
    expect(ready()).toBe(true);
    const view = m.parseStatusView(ID, statusAnswer(), 1000);
    expect(view).not.toBeNull();
    expect(view.localConfirmationNote).toBe(LOCAL_NOTE);
    expect(view.performsStepsNote).toBe(PERFORMS_NOTE);
    expect(view.eligible).toEqual(binding());
    const facts = JSON.stringify(view.facts);
    expect(facts).toContain('rcpt-base-1');
    expect(facts).toContain('pending');
  });

  it('offers no step but COMMIT, PUSH or PR_CREATE: a BASE_REFRESH the answer names as eligible is not confirmable', () => {
    expect(ready()).toBe(true);
    for (const step of ['COMMIT', 'PUSH', 'PR_CREATE']) {
      expect(m.parseStatusView(ID, statusAnswer(delivery(), office({ eligible: { step, binding: binding({ step }) } })), 1)?.eligible, step).toEqual(binding({ step }));
    }
    for (const step of ['BASE_REFRESH', 'MERGE', 'commit', '']) {
      const view = m.parseStatusView(ID, statusAnswer(delivery(), office({ eligible: { step, binding: binding({ step }) } })), 1);
      expect(view?.eligible, step).toBeNull();
      expect(view?.notEligible, step).toBeTruthy();
    }
  });

  it('offers no step whose binding names another delivery or another step, has a key too many or too few, or comes without the local note', () => {
    expect(ready()).toBe(true);
    const cases: Any[] = [
      office({ eligible: { step: 'COMMIT', binding: binding({ delivery_id: OTHER }) } }),
      office({ eligible: { step: 'COMMIT', binding: binding({ step: 'PUSH' }) } }),
      office({ eligible: { step: 'COMMIT', binding: { ...binding(), extra: 1 } } }),
      office({ eligible: { step: 'COMMIT', binding: { delivery_id: ID, revision: 4, step: 'COMMIT' } } }),
      office({ eligible: { step: 'COMMIT', binding: binding({ revision: '4' }) } }),
      office({ local_confirmation_note: undefined }),
    ];
    for (const [i, o] of cases.entries()) {
      const view = m.parseStatusView(ID, statusAnswer(delivery(), o), 1);
      expect(view, String(i)).not.toBeNull();
      expect(view?.eligible, String(i)).toBeNull();
    }
  });

  it("not eligible: the adapter's own reason and detail, and no step", () => {
    expect(ready()).toBe(true);
    const ne = { reason: 'base_refresh_unmet', step: 'BASE_REFRESH', detail: 'The base refresh comes first. This office does not perform it.' };
    const view = m.parseStatusView(ID, statusAnswer(delivery(), office({ eligible: null, not_eligible: ne })), 1);
    expect(view?.notEligible).toEqual(ne);
    expect(view?.eligible).toBeNull();
  });

  it('an answer for a different delivery than the one asked for is not shown', () => {
    expect(ready()).toBe(true);
    expect(m.parseStatusView(ID, statusAnswer(delivery({ delivery_id: OTHER })), 1)).toBeNull();
    expect(m.parseStatusView(ID, { ok: true }, 1)).toBeNull();
  });
});

describe('the local confirmation, and the one advance it allows', () => {
  it('cannot be set without an eligible step; with one, the advance carries EXACTLY the displayed binding', () => {
    expect(ready()).toBe(true);
    const none = m.reduce(shown(delivery(), office({ eligible: null, not_eligible: { reason: 'complete', step: null, detail: 'x' } })), { type: 'confirm', on: true });
    expect(none.confirmed).toBe(false);
    expect(m.advanceMessage(none, 9)).toBeNull();
    const s = shown();
    expect(m.advanceMessage(s, 9)).toBeNull();
    const c = m.reduce(s, { type: 'confirm', on: true });
    expect(c.confirmed).toBe(true);
    expect(m.advanceMessage(c, 9)).toEqual({ type: 'diDeliveryRequest', seq: 9, op: 'advanceOnce', deliveryId: ID, confirmation: binding() });
  });

  it('clears on any reply, on any newer read, and on any change of id', () => {
    expect(ready()).toBe(true);
    const c = m.reduce(shown(), { type: 'confirm', on: true });
    expect(c.confirmed).toBe(true);
    expect(m.reduce(c, { type: 'entered', text: OTHER }).confirmed).toBe(false);
    expect(m.reduce(c, { type: 'entered', text: OTHER }).view).toBeNull();
    // ROUND 54 (advisory): the newer read clears the confirmation at the moment it is SENT, not only once it answers.
    expect(m.reduce(c, { type: 'sent', seq: 2, op: 'status', deliveryId: ID }).confirmed).toBe(false);
    const reread = [
      { type: 'sent', seq: 2, op: 'status', deliveryId: ID },
      { type: 'reply', reply: reply(2, 'status', 'ok', null, null, statusAnswer()), nowMs: 2 },
    ];
    expect(reread.reduce<Any>((s: Any, e: Any) => m.reduce(s, e), c).confirmed).toBe(false);
    const failed = [
      { type: 'sent', seq: 2, op: 'status', deliveryId: ID },
      { type: 'reply', reply: reply(2, 'status', 'unavailable', 'delivery_unreachable', 'Nothing answered.', null), nowMs: 2 },
    ];
    const after = failed.reduce<Any>((s: Any, e: Any) => m.reduce(s, e), c);
    expect(after.confirmed).toBe(false);
    expect(after.view).toBeNull();
  });

  it('no advance is built while a request is waiting', () => {
    expect(ready()).toBe(true);
    const confirmed = m.reduce(shown(), { type: 'confirm', on: true });
    expect(m.advanceMessage(confirmed, 4)).not.toBeNull();
    const c = m.reduce(confirmed, { type: 'sent', seq: 3, op: 'advanceOnce', deliveryId: ID });
    expect(m.advanceMessage(c, 4)).toBeNull();
  });
});

describe('after ANY advance reply, the next action is a status READ, never another advance', () => {
  const advanced = (r: DiDeliveryReply): Any =>
    [{ type: 'confirm', on: true }, { type: 'sent', seq: 3, op: 'advanceOnce', deliveryId: ID }, { type: 'reply', reply: r, nowMs: 5 }].reduce(
      (s: Any, e: Any) => m.reduce(s, e),
      shown(),
    );
  const replies: Array<[string, DiDeliveryReply]> = [
    ['ok', reply(3, 'advanceOnce', 'ok', null, null, { ok: true, result: { advanced: true, outcome: 'advanced', step: 'COMMIT', receipt: { receipt_id: 'rcpt-commit-1', state: 'succeeded' } } })],
    ['refused', reply(3, 'advanceOnce', 'refused', 'guard_refused', 'Nothing ran: the record no longer selects the confirmed step.', { ok: false })],
    ['unknown', reply(3, 'advanceOnce', 'unknown', 'delivery_timeout', 'Whether it took a step is not known.', null)],
    ['unavailable', reply(3, 'advanceOnce', 'unavailable', 'delivery_unreachable', 'Nothing was sent.', null)],
    ['busy', reply(3, 'advanceOnce', 'refused', 'office_busy_status_in_flight', 'A status read is still waiting.', null)],
  ];

  it('each clears the displayed eligibility and the confirmation, and asks for a status read of the same delivery', () => {
    expect(ready()).toBe(true);
    for (const [what, r] of replies) {
      const s = advanced(r);
      expect(s.view, what).toBeNull();
      expect(s.confirmed, what).toBe(false);
      expect(m.advanceMessage(s, 6), what).toBeNull();
      expect(m.followUpMessage(s, 6), what).toEqual({ type: 'diDeliveryRequest', seq: 6, op: 'status', deliveryId: ID });
    }
  });

  it('once the follow-up read is sent, it is not asked for again, while it waits or after it answers', () => {
    expect(ready()).toBe(true);
    const before = advanced(replies[0][1]);
    expect(m.followUpMessage(before, 6)).not.toBeNull();
    const s = m.reduce(before, { type: 'sent', seq: 6, op: 'status', deliveryId: ID });
    expect(m.followUpMessage(s, 7)).toBeNull();
    // After that read ANSWERS, nothing is waiting, so the waiting guard cannot be what holds a second read back.
    const answered = m.reduce(s, { type: 'reply', reply: reply(6, 'status', 'ok', null, null, statusAnswer()), nowMs: 6 });
    expect(answered.pending).toBeNull();
    expect(m.followUpMessage(answered, 7)).toBeNull();
  });

  it("an ok advance's notice carries the adapter's own result as inert text", () => {
    expect(ready()).toBe(true);
    const s = advanced(replies[0][1]);
    expect(s.notices).toHaveLength(1);
    const text = JSON.stringify(s.notices[0]);
    expect(text).toContain('rcpt-commit-1');
    expect(text).toContain('succeeded');
    expect(text).toContain('COMMIT');
  });
});

describe('the words: refused is not a no-effect guarantee; busy follows its CODE', () => {
  it("an adapter's refusal is never worded as nothing happened, nothing changed or safe to retry, and says a refusal does not establish that nothing ran", () => {
    expect(ready()).toBe(true);
    for (const op of ['advanceOnce', 'status'] as const) {
      const s = run({ type: 'entered', text: ID }, { type: 'sent', seq: 1, op, deliveryId: ID }, { type: 'reply', reply: reply(1, op, 'refused', 'adapter_error', 'The adapter failed.', { ok: false }), nowMs: 1 });
      expect(s.notices, op).toHaveLength(1);
      const n = s.notices[0];
      expect(n.kind, op).toBe('refusal');
      const own = `${n.heading} ${n.text.replace('The adapter failed.', '')}`;
      expect(own, op).not.toMatch(FORBIDDEN);
      if (op === 'advanceOnce') expect(n.text).toMatch(/does not establish that nothing ran/);
    }
  });

  it('an unknown outcome says so, and that the next read may still show the step executing or unresolved', () => {
    expect(ready()).toBe(true);
    const s = run({ type: 'entered', text: ID }, { type: 'sent', seq: 1, op: 'advanceOnce', deliveryId: ID }, { type: 'reply', reply: reply(1, 'advanceOnce', 'unknown', 'delivery_timeout', 'The adapter did not answer in time.', null), nowMs: 1 });
    expect(s.notices).toHaveLength(1);
    expect(s.notices[0].kind).toBe('unknown');
    expect(`${s.notices[0].heading} ${s.notices[0].text}`).toMatch(/not known/);
    expect(s.notices[0].text).toMatch(/executing or unresolved/);
  });

  it('the busy heading follows the CODE, whatever the prose says', () => {
    expect(ready()).toBe(true);
    const prose = 'Identical words for both.';
    const busy = (problem: string): Any[] =>
      run({ type: 'entered', text: ID }, { type: 'sent', seq: 1, op: 'status', deliveryId: ID }, { type: 'reply', reply: reply(1, 'status', 'refused', problem, prose, null), nowMs: 1 }).notices;
    const advanceNotices = busy('office_busy_advance_in_flight');
    const statusNotices = busy('office_busy_status_in_flight');
    expect(advanceNotices).toHaveLength(1);
    expect(statusNotices).toHaveLength(1);
    const [a] = advanceNotices;
    const [b] = statusNotices;
    expect(a.kind).toBe('busy');
    expect(b.kind).toBe('busy');
    expect(a.heading).not.toBe(b.heading);
    expect(a.heading).toBe(m.busyHeading('office_busy_advance_in_flight'));
    expect(m.busyHeading('office_busy_advance_in_flight')).toMatch(/may still be running/);
    expect(m.busyHeading('office_busy_status_in_flight')).not.toMatch(/step|advance/i);
    expect(a.text).toContain(prose);
  });

  it('unavailable carries the relay reason as it is', () => {
    expect(ready()).toBe(true);
    const s = run({ type: 'entered', text: ID }, { type: 'sent', seq: 1, op: 'status', deliveryId: ID }, { type: 'reply', reply: reply(1, 'status', 'unavailable', 'delivery_not_configured', 'This office was started without a delivery adapter.', null), nowMs: 1 });
    expect(s.notices).toHaveLength(1);
    expect(s.notices[0].kind).toBe('unavailable');
    expect(s.notices[0].text).toContain('This office was started without a delivery adapter.');
  });
});

describe('a dropped connection and a stray reply', () => {
  it('with an advance waiting, losing the office makes it UNKNOWN, clears the display and the confirmation, and asks for a read', () => {
    expect(ready()).toBe(true);
    const s = [{ type: 'confirm', on: true }, { type: 'sent', seq: 3, op: 'advanceOnce', deliveryId: ID }, { type: 'unvouched', reason: 'disconnected' }].reduce(
      (st: Any, e: Any) => m.reduce(st, e),
      shown(),
    );
    expect(s.notices).toHaveLength(1);
    expect(s.notices[0].kind).toBe('unknown');
    expect(s.view).toBeNull();
    expect(s.confirmed).toBe(false);
    expect(s.pending).toBeNull();
    expect(m.followUpMessage(m.reduce(s, { type: 'unvouched', reason: null }), 4)).toEqual({ type: 'diDeliveryRequest', seq: 4, op: 'status', deliveryId: ID });
  });

  it('a reply that is not the one waiting is ignored', () => {
    expect(ready()).toBe(true);
    const s = run({ type: 'entered', text: ID }, { type: 'sent', seq: 1, op: 'status', deliveryId: ID }, { type: 'reply', reply: reply(99, 'status', 'ok', null, null, statusAnswer()), nowMs: 1 });
    expect(s.view).toBeNull();
    expect(s.pending).not.toBeNull();
  });
});

// ROUND 54, BLOCKER 1: a reply can be the right answer to the right request and still be the wrong answer for the current screen.
// Request identity (seq and op) is not selection identity: a status answer counts only for the id entered at the moment it arrives.
describe('the selection: a status answer counts only for the id entered NOW', () => {
  const eligibleFor = (id: string): Any =>
    statusAnswer(delivery({ delivery_id: id }), office({ eligible: { step: 'COMMIT', binding: binding({ delivery_id: id }) } }));
  const advanceOk = (seq: number): DiDeliveryReply =>
    reply(seq, 'advanceOnce', 'ok', null, null, { ok: true, result: { advanced: true, outcome: 'advanced', step: 'COMMIT', receipt: { receipt_id: 'rcpt-commit-1', state: 'succeeded' } } });

  it('an answer for an id changed while its read was PENDING is not shown, cannot be confirmed, and leaves nothing of that id', () => {
    expect(ready()).toBe(true);
    const answers: Array<[string, DiDeliveryReply]> = [
      ['ok', reply(1, 'status', 'ok', null, null, eligibleFor(ID))],
      ['refused', reply(1, 'status', 'refused', 'adapter_error', 'The adapter failed.', { ok: false })],
      ['unavailable', reply(1, 'status', 'unavailable', 'delivery_unreachable', 'Nothing answered.', null)],
      ['busy', reply(1, 'status', 'refused', 'office_busy_advance_in_flight', 'An advance is waiting.', null)],
    ];
    for (const [what, answer] of answers) {
      const s = run(
        { type: 'entered', text: ID },
        { type: 'sent', seq: 1, op: 'status', deliveryId: ID },
        { type: 'entered', text: OTHER },
        { type: 'reply', reply: answer, nowMs: 1 },
      );
      expect(s.pending, what).toBeNull();
      expect(s.view, what).toBeNull();
      const c = m.reduce(s, { type: 'confirm', on: true });
      expect(c.confirmed, what).toBe(false);
      expect(m.advanceMessage(c, 2), what).toBeNull();
      expect(s.notices, what).toHaveLength(1);
      expect(s.notices[0].text, what).toMatch(/not shown/);
      expect(JSON.stringify(s), what).not.toContain(ID);
      expect(m.statusMessage(s, 2), what).toEqual({ type: 'diDeliveryRequest', seq: 2, op: 'status', deliveryId: OTHER });
    }
  });

  it("the FOLLOW-UP read after an advance: when the id was changed meanwhile, its answer is not shown and cannot be confirmed", () => {
    expect(ready()).toBe(true);
    const s0 = [
      { type: 'confirm', on: true },
      { type: 'sent', seq: 3, op: 'advanceOnce', deliveryId: ID },
      { type: 'entered', text: OTHER },
      { type: 'reply', reply: advanceOk(3), nowMs: 5 },
    ].reduce((s: Any, e: Any) => m.reduce(s, e), shown());
    // the advanced delivery's status is still read (a read changes nothing), but its answer is not the current screen's
    expect(m.followUpMessage(s0, 4)).toEqual({ type: 'diDeliveryRequest', seq: 4, op: 'status', deliveryId: ID });
    const s1 = m.reduce(s0, { type: 'sent', seq: 4, op: 'status', deliveryId: ID });
    const s2 = m.reduce(s1, { type: 'reply', reply: reply(4, 'status', 'ok', null, null, eligibleFor(ID)), nowMs: 6 });
    expect(s2.pending).toBeNull();
    expect(s2.view).toBeNull();
    const c = m.reduce(s2, { type: 'confirm', on: true });
    expect(c.confirmed).toBe(false);
    expect(m.advanceMessage(c, 5)).toBeNull();
  });

  it('an answer for an id entered AGAIN by the time it arrives (spaces around it allowed) is shown and can be confirmed', () => {
    expect(ready()).toBe(true);
    const s = run(
      { type: 'entered', text: ID },
      { type: 'sent', seq: 1, op: 'status', deliveryId: ID },
      { type: 'entered', text: OTHER },
      { type: 'entered', text: `  ${ID} ` },
      { type: 'reply', reply: reply(1, 'status', 'ok', null, null, eligibleFor(ID)), nowMs: 1 },
    );
    expect(s.view?.deliveryId).toBe(ID);
    const c = m.reduce(s, { type: 'confirm', on: true });
    expect(c.confirmed).toBe(true);
    expect(m.advanceMessage(c, 2)?.deliveryId).toBe(ID);
  });

  // ROUND 54 FOLLOW-UP (ii): an answer the panel cannot discard must state what it is about. A reader binds a notice to what is displayed
  // beside it, so a RETAINED answer names its delivery; a DISCARDED one (above) names nothing. Complementary rules, not a conflict.
  it('an ADVANCE answer that arrives after the id was changed names the delivery it is about, not the one entered now', () => {
    expect(ready()).toBe(true);
    const outcomes: Array<[string, DiDeliveryReply]> = [
      ['ok', advanceOk(3)],
      ['refused', reply(3, 'advanceOnce', 'refused', 'guard_refused', 'The record no longer selects the confirmed step.', { ok: false })],
      ['unknown', reply(3, 'advanceOnce', 'unknown', 'delivery_timeout', 'The adapter did not answer in time.', null)],
      ['unavailable', reply(3, 'advanceOnce', 'unavailable', 'delivery_unreachable', 'Nothing answered.', null)],
      ['busy', reply(3, 'advanceOnce', 'refused', 'office_busy_status_in_flight', 'A status read is still waiting.', null)],
    ];
    for (const [what, outcome] of outcomes) {
      const s = [
        { type: 'confirm', on: true },
        { type: 'sent', seq: 3, op: 'advanceOnce', deliveryId: ID },
        { type: 'entered', text: OTHER },
        { type: 'reply', reply: outcome, nowMs: 5 },
      ].reduce((st: Any, e: Any) => m.reduce(st, e), shown());
      expect(s.notices, what).toHaveLength(1);
      expect(JSON.stringify(s.notices[0]), what).toContain(ID);
      expect(JSON.stringify(s.notices[0]), what).not.toContain(OTHER);
    }
  });

  it('a lost connection while an advance waits names the delivery whose step is not known', () => {
    expect(ready()).toBe(true);
    const s = [
      { type: 'confirm', on: true },
      { type: 'sent', seq: 3, op: 'advanceOnce', deliveryId: ID },
      { type: 'entered', text: OTHER },
      { type: 'unvouched', reason: 'disconnected' },
    ].reduce((st: Any, e: Any) => m.reduce(st, e), shown());
    expect(s.notices).toHaveLength(1);
    expect(s.notices[0].kind).toBe('unknown');
    expect(JSON.stringify(s.notices[0])).toContain(ID);
  });

  it('a status answer kept as a notice still names its delivery after the id is changed', () => {
    expect(ready()).toBe(true);
    const s = run(
      { type: 'entered', text: ID },
      { type: 'sent', seq: 1, op: 'status', deliveryId: ID },
      { type: 'reply', reply: reply(1, 'status', 'refused', 'adapter_error', 'The adapter failed.', { ok: false }), nowMs: 1 },
      { type: 'entered', text: OTHER },
    );
    expect(s.notices).toHaveLength(1);
    expect(JSON.stringify(s.notices[0])).toContain(ID);
  });

  it("an answer that is not the asked-for delivery's status is kept as a notice naming the delivery that was asked for", () => {
    expect(ready()).toBe(true);
    const s = run(
      { type: 'entered', text: ID },
      { type: 'sent', seq: 1, op: 'status', deliveryId: ID },
      { type: 'reply', reply: reply(1, 'status', 'ok', null, null, statusAnswer(delivery({ delivery_id: OTHER }))), nowMs: 1 },
      { type: 'entered', text: OTHER },
    );
    expect(s.view).toBeNull();
    expect(s.notices).toHaveLength(1);
    expect(JSON.stringify(s.notices[0])).toContain(ID);
  });

  it('no confirmation and no advance for a view whose id is not the entered one (a state the reducer does not produce, built directly)', () => {
    expect(ready()).toBe(true);
    const diverged = { ...shown(), entered: OTHER };
    expect(m.reduce(diverged, { type: 'confirm', on: true }).confirmed).toBe(false);
    expect(m.advanceMessage({ ...diverged, confirmed: true }, 9)).toBeNull();
  });
});

// ROUND 54, BLOCKER 2: absence of data is not data about absence. A field the answer does not carry is NOT "none recorded"; a part that
// is there but cannot be read is NOT either; only a field the record gives as null is "none recorded".
describe('a partial answer: missing and unreadable fields are not read as "none recorded"', () => {
  const textOf = (view: Any, label: string): string | undefined => view.facts.find((f: Any) => f.label === label)?.text;

  it('a field the answer leaves out is shown as missing from the answer; a field the record gives as null is "none recorded"', () => {
    expect(ready()).toBe(true);
    const d = delivery({ blocker: null });
    delete d.authorization;
    delete d.pr_url;
    delete d.commit;
    const view = m.parseStatusView(ID, statusAnswer(d), 1);
    expect(view).not.toBeNull();
    for (const label of ['Authorization valid (as recorded)', 'Authorized by', 'Pull request']) {
      expect(textOf(view, label), label).toBeDefined();
      expect(textOf(view, label), label).not.toMatch(/none recorded/);
      expect(textOf(view, label), label).toMatch(/not in the adapter.s answer/);
    }
    expect(textOf(view, 'Blocker')).toBe('none recorded');
    const commit = view.facts.filter((f: Any) => String(f.label).startsWith('COMMIT'));
    expect(commit).toHaveLength(1);
    expect(commit[0].text).toMatch(/not in the adapter.s answer/);
    expect(commit[0].text).not.toMatch(/none recorded/);
  });

  it('a part that is there but not readable is shown as unreadable: not "none recorded", not missing', () => {
    expect(ready()).toBe(true);
    const view = m.parseStatusView(ID, statusAnswer(delivery({ authorization: 'yes', commit: 7 })), 1);
    expect(view).not.toBeNull();
    const auth = textOf(view, 'Authorization valid (as recorded)');
    expect(auth).toMatch(/unreadable/);
    expect(auth).not.toMatch(/none recorded|not in the adapter/);
    const commit = view.facts.filter((f: Any) => String(f.label).startsWith('COMMIT'));
    expect(commit).toHaveLength(1);
    expect(commit[0].text).toMatch(/unreadable/);
    expect(commit[0].text).not.toMatch(/none recorded/);
  });

  it("an ok advance whose result leaves out its receipt says the receipt is missing from the answer, not none recorded", () => {
    expect(ready()).toBe(true);
    const s = run(
      { type: 'entered', text: ID },
      { type: 'sent', seq: 1, op: 'advanceOnce', deliveryId: ID },
      { type: 'reply', reply: reply(1, 'advanceOnce', 'ok', null, null, { ok: true, result: { advanced: true, outcome: 'advanced', step: 'COMMIT' } }), nowMs: 1 },
    );
    expect(s.notices).toHaveLength(1);
    expect(s.notices[0].text).toMatch(/receipt: not in the adapter.s answer/);
    expect(s.notices[0].text).not.toMatch(/receipt: none recorded/);
  });
});
