/**
 * Round 58's correction of M8: A REASON NAMES ITS AUTHOR. Each displayed reason says WHO established it.
 *
 * Blocker 1 (diScanModel.ts linkedDeliverySteps): three different parties can be why a step is not offered:
 *  - the ADAPTER (its answer says no step is eligible),
 *  - the ANSWER's usability (this window could not use what the answer says about eligibility),
 *  - THIS WINDOW (the answer names a step eligible but carries no local-confirmation note, so this window offers no confirmation).
 * They are kept apart in the model (DlView.noStepBy, DlView.namedStep) and in Mission details' markup, and only the adapter's own verdict
 * is worded as the adapter's.
 *
 * Blocker 2 (DiDeliveryEntry.tsx, the waiting line): a pending request names ITS delivery. "This delivery is pending" and "this delivery's
 * read is blocked because a request about a DIFFERENT delivery is in flight" are different facts, and are said differently; in Mission
 * details the other delivery's id is not shown (another Mission's delivery is not shown in this one).
 *
 * Markup is static (renderToStaticMarkup); the session is the real model's state.
 */
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';

import type { DiDeliveryReply, DiMission, DiOfficeState } from '../../core/src/messages.ts';
import { DiDeliveryEntry } from '../src/components/DiDeliveryEntry.tsx';
import * as model from '../src/components/diDeliveryModel.ts';
import { type DiScanRow, scanRowsFromOffice } from '../src/components/diScanModel.ts';
import { DiScanPanel } from '../src/components/DiScanPanel.tsx';
import type { DiDeliveryControl } from '../src/hooks/useDiDelivery.ts';

type Any = Record<string, any>; // eslint-disable-line @typescript-eslint/no-explicit-any
const m = model as unknown as Any;

const A = `prd-${'a'.repeat(24)}`;
const B = `prd-${'b'.repeat(24)}`;
const DIGEST = 'c'.repeat(64);
const LOCAL_NOTE = 'This confirmation is a local check. This confirmation is not recorded in the delivery record.';
const PERFORMS_NOTE = 'This office performs ONE confirmed step at a time.';

// ── the words, copied here, never read from the code under test ─────────────────────────────────────────────────────────────────
const BY_ADAPTER = 'Not eligible now, as the adapter’s answer states it.';
const NOT_NAMED = 'Not the step eligible now, as the adapter’s answer states it.';
const BY_ANSWER = 'This window could not use the adapter’s answer about which step is eligible, so it offers no step here.';
const BY_WINDOW = 'The adapter’s answer names this step eligible, but carries no local-confirmation note, so this window offers no confirmation for it.';
const READY = 'Eligible now, as the record states it. Confirm it below.';
const UNREADABLE_ELIGIBILITY = 'This window could not read the adapter’s answer about which step is eligible, so no step can be confirmed from here.';
/** b3's words for a step the answer DOES name (a readable step word) that this window cannot use: kept for exactly that case. */
const NAMED_UNUSABLE =
  'This window could not read the eligible step the adapter named as one of this office’s steps for this delivery, so no step can be confirmed from here.';
/** The wired rows, asserted before any per-row assertion (round 59: an iteration is a claim only if what it iterates over is asserted). */
const WIRED = ['commit', 'push', 'pr', 'accept_ci'];
const WAIT_SHOWN_READ = (id: string) => `Waiting for the delivery adapter’s answer to the status read of this delivery, ${id}.`;
const WAIT_SHOWN_STEP = (id: string) => `Waiting for the delivery adapter’s answer to the step request for this delivery, ${id}.`;
const WAIT_OTHER_READ = 'This delivery’s status cannot be read yet: this window is waiting for the adapter’s answer to a status read of a different delivery.';
const WAIT_OTHER_STEP =
  'This delivery’s status cannot be read yet: this window is waiting for the adapter’s answer to a step request for a different delivery, whose step may still be running.';
const WAIT_ENTERED_OTHER = (id: string) =>
  `The delivery entered now cannot be read yet: this window is waiting for the delivery adapter’s answer to the status read of delivery ${id}, entered before.`;

// ── the adapter's answer and the session ─────────────────────────────────────────────────────────────────────────────────────────
const binding = (id = B, over: Any = {}): Any => ({ delivery_id: id, revision: 4, candidate_identity_digest_sha256: DIGEST, step: 'COMMIT', ...over });
const office = (id = B, over: Any = {}): Any => ({
  eligible: { step: 'COMMIT', binding: binding(id) },
  not_eligible: null,
  action_steps: ['COMMIT', 'PUSH', 'PR_CREATE'],
  local_confirmation_note: LOCAL_NOTE,
  performs_steps: true,
  performs_steps_note: PERFORMS_NOTE,
  ...over,
});
const delivery = (id = B): Any => ({
  delivery_id: id,
  revision: 4,
  phase: 'base_refreshed',
  authorization: { valid: true, problem: null, human_identity: 'travis', allowed_actions: ['commit', 'push', 'pr_create'], mode: 'pr_create' },
  base_refresh: { state: 'succeeded', receipt_id: 'rcpt-base-1', receipt_state: 'succeeded', attempts: 1, observed: null },
  commit: { state: 'pending', receipt_id: null, receipt_state: null, attempts: 0, observed: null, commit_oid: null },
  push: { state: 'pending', receipt_id: null, receipt_state: null, attempts: 0, observed: null, remote_oid: null },
  pr_url: null,
  blocker: null,
  next_action: { action: 'run_step', step: 'COMMIT' },
});
const answer = (o: Any, id = B): Any => ({ ok: true, status: { delivery: delivery(id), office: o } });
const reply = (seq: number, op: 'status' | 'advanceOnce', state: DiDeliveryReply['state'], body: Any | null): DiDeliveryReply =>
  ({ type: 'diDeliveryReply', seq, op, state, problem: null, reason: null, answer: body }) as DiDeliveryReply;
const run = (start: Any, ...events: Any[]): Any => events.reduce((s, e) => m.reduce(s, e), start);
const linkedStart = (): Any => ({ ...m.INITIAL_DL_STATE, subject: 'linked' });
/** Mission B's session after ONE status read of B answered with `o`. */
const readB = (o: Any): Any =>
  run(linkedStart(), { type: 'linked', deliveryId: B }, { type: 'sent', seq: 3, op: 'status', deliveryId: B }, { type: 'reply', reply: reply(3, 'status', 'ok', answer(o)), nowMs: 1 });

const noop = () => undefined;
const control = (state: Any): DiDeliveryControl =>
  ({ state, open: noop, close: noop, setEntered: noop, setLinked: noop, setConfirmed: noop, readStatus: noop, askForStep: noop, dismiss: noop }) as unknown as DiDeliveryControl;

// ── Mission B: the real projection of an emitted office state, the herd Mission made a workflow Mission linked to B ────────────────
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const historical = JSON.parse(
  fs.readFileSync(path.join(root, 'test/fixtures/di-office-state-evidence-historical-v2.FIXTURE.json'), 'utf-8'),
) as { states: Record<'live', DiOfficeState> };
const HERD = 'herd:20261014-000000-aaaaaa@f69ae73eadfe';
function missionLinkedTo(id: string): DiScanRow {
  const state = structuredClone(historical.states.live);
  for (const mission of state.missions.missions as DiMission[]) {
    if (mission.missionRef !== HERD) continue;
    mission.lifecycleModel = 'workflow_authority.workflow_phase/2';
    mission.v2 = { ...mission.v2, deliveryLink: { state: 'linked', deliveryId: id, basis: 'record_mission_binding' } };
  }
  return scanRowsFromOffice(state).find((r) => r.id === HERD)!;
}
const section = (state: Any, row = missionLinkedTo(B)) => {
  const html = renderToStaticMarkup(
    createElement(DiScanPanel, { rows: [row], initialSelectedId: row.id, initialView: 'mission', onMinimize: noop, missionDelivery: control(state) }),
  );
  return /<section[^>]*data-testid="di-scan-delivery"[\s\S]*?<\/section>/.exec(html)?.[0] ?? '';
};
const text = (markup: string) => markup.replace(/<[^>]+>/g, ' ').replace(/&#x27;/g, "'").replace(/&amp;/g, '&').replace(/\s+/g, ' ');
const rows = (d: string) =>
  [...d.matchAll(/<li[^>]*data-testid="di-scan-step" data-step="([a-z_]+)" data-gate="([a-z]+)"[^>]*>([\s\S]*?)<\/li>/g)].map((x) => ({
    id: x[1]!,
    gate: x[2]!,
    reason: text(x[3]!),
  }));

describe('BLOCKER 1, the model: who established that no step is offered', () => {
  it('an offered step: named, offered, and nobody withheld it', () => {
    const v = readB(office()).view;
    expect([v.namedStep, v.noStepBy, v.eligible?.step]).toEqual(['COMMIT', null, 'COMMIT']);
  });
  it('the ADAPTER: its answer says no step is eligible', () => {
    const v = readB(office(B, { eligible: null, not_eligible: { reason: 'base_refresh_unmet', step: 'BASE_REFRESH', detail: 'The base refresh comes first.' } })).view;
    expect([v.namedStep, v.noStepBy]).toEqual([null, 'adapter']);
  });
  it('THIS WINDOW: the answer names COMMIT eligible but carries no local-confirmation note', () => {
    const v = readB(office(B, { local_confirmation_note: undefined })).view;
    expect([v.namedStep, v.noStepBy, v.eligible]).toEqual(['COMMIT', 'window', null]);
  });
  it('THE ANSWER: an eligible step this window cannot use, or an eligibility it cannot read', () => {
    for (const o of [
      office(B, { eligible: { step: 'COMMIT', binding: binding(B, { revision: '4' }) } }),
      office(B, { eligible: { step: 'BASE_REFRESH', binding: binding(B, { step: 'BASE_REFRESH' }) } }),
      office(B, { eligible: null, not_eligible: 'garbage' }),
    ]) {
      const v = readB(o).view;
      expect([v.namedStep, v.noStepBy], JSON.stringify(o.eligible)).toEqual([null, 'answer']);
    }
  });
  it('an eligibility this window cannot read is not worded as a step the adapter named', () => {
    const v = readB(office(B, { eligible: null, not_eligible: 'garbage' })).view;
    expect(v.notEligible.detail).toBe(UNREADABLE_ELIGIBILITY);
    expect(v.notEligible.detail).not.toMatch(/the adapter named/);
  });

  // ROUND 59: presence and shape are not content. A claim needs READABLE evidence; a partial object gets the unusable-answer words.
  it('a PARTIAL eligible object names no step: never "the eligible step the adapter named", the unusable-answer words instead', () => {
    for (const eligible of [{}, { step: 7 }, { step: '' }, 'COMMIT', { binding: binding(B) }]) {
      const v = readB(office(B, { eligible })).view;
      expect([v.namedStep, v.noStepBy, v.eligible], JSON.stringify(eligible)).toEqual([null, 'answer', null]);
      expect(v.notEligible.detail, JSON.stringify(eligible)).toBe(UNREADABLE_ELIGIBILITY);
    }
  });
  it('a PARTIAL not_eligible object is NO adapter verdict: no readable reason, so the unusable-answer words', () => {
    for (const notEligible of [{}, { reason: 5 }, { reason: '' }, { step: 'COMMIT', detail: 'x' }]) {
      const v = readB(office(B, { eligible: null, not_eligible: notEligible })).view;
      expect([v.namedStep, v.noStepBy], JSON.stringify(notEligible)).toEqual([null, 'answer']);
      expect(v.notEligible.detail, JSON.stringify(notEligible)).toBe(UNREADABLE_ELIGIBILITY);
    }
  });
  it('kept: a readable reason is the adapter\'s verdict, and a readable step word this window cannot use keeps b3\'s named-step words', () => {
    const verdict = readB(office(B, { eligible: null, not_eligible: { reason: 'step_not_pending' } })).view;
    expect([verdict.noStepBy, verdict.notEligible.reason]).toEqual(['adapter', 'step_not_pending']);
    const named = readB(office(B, { eligible: { step: 'MERGE', binding: binding(B, { step: 'MERGE' }) } })).view;
    expect([named.noStepBy, named.notEligible.detail]).toEqual(['answer', NAMED_UNUSABLE]);
  });
});

describe('BLOCKER 1, through Mission markup: each row says who established it', () => {
  it('the adapter\'s verdict: every wired row is the ADAPTER\'s "not eligible", and none says another step is eligible', () => {
    const d = section(readB(office(B, { eligible: null, not_eligible: { reason: 'base_refresh_unmet', step: 'BASE_REFRESH', detail: 'The base refresh comes first.' } })));
    expect(rows(d).map((r) => r.id)).toEqual(WIRED);
    for (const r of rows(d).slice(0, 3)) {
      expect(r.reason, r.id).toContain(BY_ADAPTER);
      expect(r.reason, r.id).not.toContain(NOT_NAMED);
    }
  });

  it('no local-confirmation note: COMMIT says THIS WINDOW offers no confirmation; the others are not the named step; nothing to confirm', () => {
    const d = section(readB(office(B, { local_confirmation_note: undefined })));
    const [commit, push, pr] = rows(d);
    expect([commit!.gate, push!.gate, pr!.gate]).toEqual(['locked', 'locked', 'locked']);
    expect(commit!.reason).toContain(BY_WINDOW);
    expect(commit!.reason).not.toContain('as the adapter’s answer states it');
    expect(push!.reason).toContain(NOT_NAMED);
    expect(pr!.reason).toContain(NOT_NAMED);
    expect(d).not.toContain('data-testid="di-dl-confirm"');
    expect(d).not.toContain('data-testid="di-dl-advance"');
  });

  it('an answer this window cannot use: every wired row says so, as THIS WINDOW\'s reading, never as the adapter\'s verdict', () => {
    for (const o of [
      office(B, { eligible: { step: 'COMMIT', binding: binding(B, { revision: '4' }) } }),
      office(B, { eligible: { step: 'BASE_REFRESH', binding: binding(B, { step: 'BASE_REFRESH' }) } }),
      office(B, { eligible: null, not_eligible: 'garbage' }),
    ]) {
      const d = section(readB(o));
      expect(rows(d).map((r) => r.id), JSON.stringify(o.eligible)).toEqual(WIRED);
      for (const r of rows(d).slice(0, 3)) {
        expect(r.reason, `${r.id} ${JSON.stringify(o.eligible)}`).toContain(BY_ANSWER);
        expect(r.reason, r.id).not.toContain('as the adapter’s answer states it');
      }
      expect(d).not.toContain('data-testid="di-dl-advance"');
    }
  });

  it('ROUND 59, both partial objects through Mission markup: the unusable-answer words in every wired row and in the detail, no adapter verdict', () => {
    for (const o of [office(B, { eligible: {} }), office(B, { eligible: null, not_eligible: {} })]) {
      const name = JSON.stringify([o.eligible, o.not_eligible]);
      const d = section(readB(o));
      expect(rows(d).map((r) => r.id), name).toEqual(WIRED);
      for (const r of rows(d).slice(0, 3)) {
        expect(r.reason, `${r.id} ${name}`).toContain(BY_ANSWER);
        expect(r.reason, `${r.id} ${name}`).not.toContain(BY_ADAPTER);
      }
      expect(text(d), name).toContain(UNREADABLE_ELIGIBILITY);
      expect(text(d), name).not.toContain('the adapter named');
      expect(d, name).not.toContain('data-testid="di-dl-advance"');
    }
  });

  it('an offered step still reads as before: COMMIT ready, the others not the named step', () => {
    const [commit, push, pr] = rows(section(readB(office())));
    expect([commit!.gate, commit!.reason.includes(READY)]).toEqual(['ready', true]);
    expect(push!.reason).toContain(NOT_NAMED);
    expect(pr!.reason).toContain(NOT_NAMED);
  });
});

describe('BLOCKER 2, the model: the waiting line names ITS delivery', () => {
  it('pending for the shown delivery: it says so, and names it', () => {
    expect(typeof m.pendingLine).toBe('function');
    const read = run(linkedStart(), { type: 'linked', deliveryId: B }, { type: 'sent', seq: 4, op: 'status', deliveryId: B });
    expect(m.pendingLine(read)).toBe(WAIT_SHOWN_READ(B));
    const step = run(readB(office()), { type: 'confirm', on: true }, { type: 'sent', seq: 5, op: 'advanceOnce', deliveryId: B });
    expect(m.pendingLine(step)).toBe(WAIT_SHOWN_STEP(B));
  });
  it('pending for ANOTHER delivery: the shown delivery is blocked, and the other is not named in a linked session', () => {
    expect(typeof m.pendingLine).toBe('function');
    const read = run(linkedStart(), { type: 'linked', deliveryId: A }, { type: 'sent', seq: 4, op: 'status', deliveryId: A }, { type: 'linked', deliveryId: B });
    expect(m.pendingLine(read)).toBe(WAIT_OTHER_READ);
    const stepA = { ...run(linkedStart(), { type: 'linked', deliveryId: A }), pending: { seq: 5, op: 'advanceOnce', deliveryId: A } };
    expect(m.pendingLine(run(stepA, { type: 'linked', deliveryId: B }))).toBe(WAIT_OTHER_STEP);
  });
  it('nothing pending: no line', () => {
    expect(typeof m.pendingLine).toBe('function');
    expect(m.pendingLine(run(linkedStart(), { type: 'linked', deliveryId: B }))).toBeNull();
  });
});

describe('BLOCKER 2, through markup: the transition from A to B', () => {
  it('Mission B shown while A\'s read is in flight: B\'s read is BLOCKED by another delivery, said so; A is not named; nothing unattributed', () => {
    const s = run(linkedStart(), { type: 'linked', deliveryId: A }, { type: 'sent', seq: 4, op: 'status', deliveryId: A }, { type: 'linked', deliveryId: B });
    const d = section(s);
    expect(text(d)).toContain(WAIT_OTHER_READ);
    expect(d).not.toContain(A);
    expect(text(d)).not.toContain('Waiting for the delivery adapter’s answer to the status read.');
    expect((d.match(/<button[^>]*data-testid="di-scan-delivery-read"[^>]*>/) ?? [''])[0]).toContain('disabled');
  });

  it('Mission B shown while A\'s STEP request is in flight: its step may still be running, said so; A is not named', () => {
    const s = { ...run(linkedStart(), { type: 'linked', deliveryId: B }), pending: { seq: 5, op: 'advanceOnce', deliveryId: A } };
    const d = section(s);
    expect(text(d)).toContain(WAIT_OTHER_STEP);
    expect(d).not.toContain(A);
  });

  it('Mission B\'s own read in flight: B is pending, named', () => {
    const d = section(run(linkedStart(), { type: 'linked', deliveryId: B }, { type: 'sent', seq: 4, op: 'status', deliveryId: B }));
    expect(text(d)).toContain(WAIT_SHOWN_READ(B));
  });

  it('the standalone panel: the id typed now is not the one waiting, and the line says which is', () => {
    const s = run(m.INITIAL_DL_STATE, { type: 'open' }, { type: 'entered', text: A }, { type: 'sent', seq: 4, op: 'status', deliveryId: A }, { type: 'entered', text: B });
    expect(typeof m.pendingLine).toBe('function');
    expect(m.pendingLine(s)).toBe(WAIT_ENTERED_OTHER(A));
    const html = renderToStaticMarkup(createElement(DiDeliveryEntry, { control: control(s) }));
    expect(text(html)).toContain(WAIT_ENTERED_OTHER(A));
  });
});
