/**
 * M8: Delivery under Swarm, wired. Mission details reads the ONE delivery the catalog links to the shown Mission (its declared
 * `deliveryLink`), through its OWN delivery session (useDiDelivery in linked mode), and offers the one eligible step with the b3 local
 * confirmation. Nothing here renders a browser; markup is static (renderToStaticMarkup), the session is the real model's state.
 *
 * What it pins:
 *  - ONLY AN ESTABLISHED UNIQUE WORKFLOW MATCH ENABLES ANYTHING: a `linked` Mission gets the read control and, after a read, the
 *    eligible step; every unlinked state, Mission Core's `unlinked_model_not_recorded`, an unreported link and a linked Mission without
 *    a session get NO read, NO confirmation and NO step, only the producer's reason in words.
 *  - NO TYPED ID AND NO OTHER DELIVERY: a linked session ignores typed input, sends only the linked id, and shows only notices about
 *    that delivery. Its scope sentence says so, and the behaviour is pinned beside it.
 *  - THE SURFACES, ESTABLISHED: the Missions list carries no delivery id and no link words for any Mission; the standalone panel's
 *    "There is no list to choose from" sentence speaks of its own window and session, which Mission details never writes.
 *  - STORE IDENTITY, a limit: a linked id the adapter's store has no record of arrives as the adapter's own refusal, with its code,
 *    and no step; the section says the two stores are not shown to be one.
 *  - Specimen rows are unchanged; Accept CI stays unconnected; there is no merge or hold word anywhere in Delivery.
 */
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';

import type { DiDeliveryReply, DiMission, DiOfficeState } from '../../core/src/messages.ts';
import * as model from '../src/components/diDeliveryModel.ts';
import { DI_SCAN_LOCK, type DiScanRow, scanRowsFromOffice } from '../src/components/diScanModel.ts';
import { DiScanPanel } from '../src/components/DiScanPanel.tsx';
import type { DiDeliveryControl } from '../src/hooks/useDiDelivery.ts';

type Any = Record<string, any>; // eslint-disable-line @typescript-eslint/no-explicit-any
const m = model as unknown as Any;

const ID = `prd-${'a'.repeat(24)}`;
const OTHER = `prd-${'b'.repeat(24)}`;
const DIGEST = 'c'.repeat(64);
const LOCAL_NOTE =
  'This confirmation is a local check. The delivery was authorized once, by the human ceremony, for these steps. This confirmation is not recorded in the delivery record.';
const PERFORMS_NOTE = 'This office performs ONE confirmed step at a time (COMMIT, PUSH or PR_CREATE; never the base refresh, never every step).';
const WORKFLOW_MODEL = 'workflow_authority.workflow_phase/2';

// ── the words, copied here, never read from the code under test ─────────────────────────────────────────────────────────────────
const SCOPE =
  'This Mission’s delivery is the one record linked to it. No delivery id is typed here, and no other delivery can be chosen. Only notices about this delivery are shown here.';
const STORE =
  'The catalog’s delivery store is not shown to be the one the delivery adapter reads. If the adapter has no record of this delivery, its refusal is shown as it answered.';
const LINKED_WORDS = 'Linked by the delivery record’s own Mission binding. A link names which delivery is read here; it authorizes nothing.';
const WORDS: Record<string, string> = {
  unlinked_model_not_recorded: 'Not known: delivery records name only workflow Missions, so no delivery is linked to a Mission of this kind.',
  unlinked_store_not_registered: 'Not known: the catalog has no delivery store registered.',
  unlinked_store_unavailable: 'Not known: the catalog’s delivery store could not be read, or a record in it did not validate.',
  unlinked_reader_failed: 'Not known: the catalog’s delivery link reader failed.',
  unlinked_no_record: 'No delivery record in the catalog’s delivery store names this Mission.',
  unlinked_ambiguous: 'More than one delivery record names this Mission, or another Mission has its id; none is chosen.',
  unlinked_sources_incomplete: 'Not known: a workflow source was not read, so this Mission’s id is not known to be unique.',
  unlinked_id_not_addressable: 'The one record that names this Mission has a delivery id of a shape this office cannot address.',
};
const NOT_REPORTED = 'Not known: this office’s catalog does not report a delivery link for this Mission.';
const NO_SESSION = 'This window has no delivery session, so this delivery is not read here.';
const STEP_READY = 'Eligible now, as the record states it. Confirm it below.';
const STEP_OTHER = 'Not the step eligible now, as the adapter’s answer states it.';
const STEP_UNREAD = 'Read this delivery’s status below to see which step is eligible.';
const ACCEPT_CI = 'Accepting CI results is not connected in this office.';
const READ_LABEL = 'Read this delivery’s status';

// ── the adapter's answer and the session state ──────────────────────────────────────────────────────────────────────────────────
const binding = (over: Any = {}): Any => ({ delivery_id: ID, revision: 4, candidate_identity_digest_sha256: DIGEST, step: 'COMMIT', ...over });
const office = (over: Any = {}): Any => ({
  eligible: { step: 'COMMIT', binding: binding() },
  not_eligible: null,
  action_steps: ['COMMIT', 'PUSH', 'PR_CREATE'],
  local_confirmation_note: LOCAL_NOTE,
  performs_steps: true,
  performs_steps_note: PERFORMS_NOTE,
  ...over,
});
const delivery = (id = ID): Any => ({
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
const statusAnswer = (o: Any = office(), id = ID): Any => ({ ok: true, status: { delivery: delivery(id), office: o } });
const reply = (seq: number, op: 'status' | 'advanceOnce', state: DiDeliveryReply['state'], problem: string | null, reason: string | null, answer: Any | null): DiDeliveryReply =>
  ({ type: 'diDeliveryReply', seq, op, state, problem, reason, answer }) as DiDeliveryReply;
const LINKED_START = (): Any => ({ ...m.INITIAL_DL_STATE, subject: 'linked' });
const run = (start: Any, ...events: Any[]): Any => events.reduce((s, e) => m.reduce(s, e), start);
/** The real model's state after a status read of the linked id answered with `answer`. */
const readWith = (answer: DiDeliveryReply, id = ID): Any =>
  run(LINKED_START(), { type: 'linked', deliveryId: id }, { type: 'sent', seq: 7, op: 'status', deliveryId: id }, { type: 'reply', reply: answer, nowMs: 1 });

const noop = () => undefined;
const control = (state: Any): DiDeliveryControl =>
  ({ state, open: noop, close: noop, setEntered: noop, setLinked: noop, setConfirmed: noop, readStatus: noop, askForStep: noop, dismiss: noop }) as unknown as DiDeliveryControl;

// ── rows: the real projection of an emitted office state, its Missions' links edited (labelled synthetic variations) ─────────────
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const historical = JSON.parse(
  fs.readFileSync(path.join(root, 'test/fixtures/di-office-state-evidence-historical-v2.FIXTURE.json'), 'utf-8'),
) as { states: Record<'live', DiOfficeState> };
const HERD = 'herd:20261014-000000-aaaaaa@f69ae73eadfe';
const CORE = 'mc1:mn-04be029a813f736fe6877a02ecb5a9b3';
type Link = NonNullable<DiMission['v2']['deliveryLink']>;
/** `workflow`: the herd Mission, made a workflow Mission (the producer links only those), with `link`; Mission Core with `core`. */
function rows(link: Link | null, core: Link | null = { state: 'unlinked_model_not_recorded', deliveryId: null, basis: null }): DiScanRow[] {
  const state = structuredClone(historical.states.live);
  for (const mission of state.missions.missions) {
    if (mission.missionRef === HERD) {
      mission.lifecycleModel = WORKFLOW_MODEL;
      if (link !== null) mission.v2 = { ...mission.v2, deliveryLink: link };
    } else if (core !== null) {
      mission.v2 = { ...mission.v2, deliveryLink: core };
    }
  }
  return scanRowsFromOffice(state);
}
const linkedRow = (id = ID) => rows({ state: 'linked', deliveryId: id, basis: 'record_mission_binding' }).find((r) => r.id === HERD)!;
const render = (row: DiScanRow, session: DiDeliveryControl | null, view: 'mission' | 'missions' = 'mission', all: DiScanRow[] = [row]) =>
  renderToStaticMarkup(
    createElement(DiScanPanel, { rows: all, initialSelectedId: row.id, initialView: view, onMinimize: noop, missionDelivery: session }),
  );
const section = (html: string) => /<section[^>]*data-testid="di-scan-delivery"[\s\S]*?<\/section>/.exec(html)?.[0] ?? '';
const text = (markup: string) =>
  markup.replace(/<[^>]+>/g, ' ').replace(/&#x27;/g, "'").replace(/&quot;/g, '"').replace(/&amp;/g, '&').replace(/\s+/g, ' ');
const stepsOf = (d: string) =>
  [...d.matchAll(/<li[^>]*data-testid="di-scan-step" data-step="([a-z_]+)" data-gate="([a-z]+)"[^>]*>([\s\S]*?)<\/li>/g)].map((x) => ({
    id: x[1]!,
    gate: x[2]!,
    body: x[3]!,
  }));

describe('the model, linked mode: the ONE id is the Mission\'s link, never typed', () => {
  it('a linked session reads exactly the linked id', () => {
    const s = run(LINKED_START(), { type: 'linked', deliveryId: ID });
    expect(m.statusMessage(s, 3)).toEqual({ type: 'diDeliveryRequest', seq: 3, op: 'status', deliveryId: ID });
  });

  it('typed input is ignored in a linked session: no manual id inside a Mission', () => {
    const s = run(LINKED_START(), { type: 'linked', deliveryId: ID }, { type: 'entered', text: OTHER });
    expect(m.statusMessage(s, 3)?.deliveryId).toBe(ID);
    expect(JSON.stringify(s)).not.toContain(OTHER);
  });

  it('a Mission with no link reads nothing and offers no step', () => {
    const s = run(LINKED_START(), { type: 'linked', deliveryId: ID }, { type: 'linked', deliveryId: null });
    expect(m.statusMessage(s, 3)).toBeNull();
    expect(m.advanceMessage(run(s, { type: 'confirm', on: true }), 4)).toBeNull();
  });

  it('a link that changes while a read is on its way: the answer is not shown, worded as the LINK, and nothing of it is kept', () => {
    const s = run(
      LINKED_START(),
      { type: 'linked', deliveryId: ID },
      { type: 'sent', seq: 1, op: 'status', deliveryId: ID },
      { type: 'linked', deliveryId: OTHER },
      { type: 'reply', reply: reply(1, 'status', 'ok', null, null, statusAnswer()), nowMs: 1 },
    );
    expect(s.view).toBeNull();
    expect(s.notices).toHaveLength(1);
    expect(s.notices[0].deliveryId).toBeNull();
    expect(s.notices[0].text).toBe(
      'It answers the read of the delivery linked to the Mission shown before, and a different delivery, or none, is linked to the Mission shown now, so it is not shown. Nothing of that answer is kept.',
    );
    expect(s.notices[0].text).not.toMatch(/entered/);
    expect(JSON.stringify(s)).not.toContain(ID);
  });

  it('the standalone session is unchanged: its id is typed, and its discard wording still names the typed id', () => {
    expect(m.INITIAL_DL_STATE.subject).toBe('entered');
    const s = run(
      m.INITIAL_DL_STATE,
      { type: 'entered', text: ID },
      { type: 'sent', seq: 1, op: 'status', deliveryId: ID },
      { type: 'entered', text: OTHER },
      { type: 'reply', reply: reply(1, 'status', 'ok', null, null, statusAnswer()), nowMs: 1 },
    );
    expect(s.notices[0].text).toBe(
      'It answers the read of the delivery id entered before, and a different id is entered now, so it is not shown. Nothing of that answer is kept.',
    );
    // a link never reaches the standalone session: it is a different session, and `linked` does nothing to an entered one
    expect(run(m.INITIAL_DL_STATE, { type: 'linked', deliveryId: ID }).entered).toBe('');
  });

  it('every request of every session takes a distinct sequence number (replies are matched by seq and op only)', () => {
    expect(typeof m.takeDeliverySeq).toBe('function');
    const seen = new Set<number>();
    for (let i = 0; i < 5; i += 1) seen.add(m.takeDeliverySeq());
    expect(seen.size).toBe(5);
  });
});

describe('the row carries the producer\'s link, in words', () => {
  it('linked: the one id, with the basis in words', () => {
    expect(linkedRow().deliveryLink).toEqual({ kind: 'linked', deliveryId: ID, words: LINKED_WORDS });
  });

  it('every unlinked state is its own reason; Mission Core is not known', () => {
    for (const [state, words] of Object.entries(WORDS)) {
      const r = state === 'unlinked_model_not_recorded'
        ? rows(null).find((x) => x.id === CORE)!
        : rows({ state: state as Link['state'], deliveryId: null, basis: null }).find((x) => x.id === HERD)!;
      expect(r.deliveryLink, state).toEqual({ kind: 'unlinked', state, words });
    }
  });

  it('an office whose answer carries no link says it is not reported: unknown, never "none"', () => {
    const r = rows(null, null);
    // the rows iterated are asserted first: a loop over nothing asserts nothing (round 59)
    expect(r.map((row) => row.id).sort()).toEqual([CORE, HERD].sort());
    for (const row of r) expect(row.deliveryLink, row.id).toEqual({ kind: 'not_reported', words: NOT_REPORTED });
  });

  it('the specimen has no link: its Delivery stays the labelled layout', () => {
    expect(DI_SCAN_LOCK.length).toBeGreaterThan(0);
    for (const row of DI_SCAN_LOCK) expect(row.deliveryLink, row.id).toBeUndefined();
  });
});

describe('Delivery under Swarm, linked: the one delivery, read and offered here', () => {
  it('before a read: the link, the scope and store sentences, ONE read control, the steps waiting on the read, Accept CI not connected', () => {
    const d = section(render(linkedRow(), control(run(LINKED_START(), { type: 'linked', deliveryId: ID }))));
    expect(d).not.toBe('');
    expect(text(d)).toContain(`The delivery linked to this Mission: ${ID}. ${LINKED_WORDS}`);
    expect(text(d)).toContain(SCOPE);
    expect(text(d)).toContain(STORE);
    const read = d.match(/<button[^>]*data-testid="di-scan-delivery-read"[^>]*>[\s\S]*?<\/button>/g) ?? [];
    expect(read).toHaveLength(1);
    expect(read[0]).not.toContain('disabled');
    expect(text(read[0]!)).toContain(READ_LABEL);
    expect(stepsOf(d).map((s) => `${s.id}:${s.gate}`)).toEqual(['commit:locked', 'push:locked', 'pr:locked', 'accept_ci:locked']);
    for (const s of stepsOf(d).slice(0, 3)) expect(text(s.body), s.id).toContain(STEP_UNREAD);
    expect(text(stepsOf(d)[3]!.body)).toContain(ACCEPT_CI);
    // no layout-only claim on the steps that are wired, and no layout control drawn
    expect(d).not.toContain('Layout preview');
    expect(d).not.toContain('Delivery state is not read in this layout preview.');
    expect(d).toContain('Delivery ends with an open pull request.');
  });

  it('after a read with an eligible step: that step ready, the others not, the b3 LOCAL CHECK with both notes verbatim, one ask control', () => {
    const s = readWith(reply(7, 'status', 'ok', null, null, statusAnswer()));
    const d = section(render(linkedRow(), control(s)));
    expect(stepsOf(d).map((x) => `${x.id}:${x.gate}`)).toEqual(['commit:ready', 'push:locked', 'pr:locked', 'accept_ci:locked']);
    expect(text(stepsOf(d)[0]!.body)).toContain(STEP_READY);
    for (const x of stepsOf(d).slice(1, 3)) expect(text(x.body), x.id).toContain(STEP_OTHER);
    // the one action lives in the confirmation block, not in a step row
    for (const x of stepsOf(d)) expect(x.body, x.id).not.toMatch(/<button/);
    expect(d).toContain('data-testid="di-dl-eligible"');
    expect(text(d)).toContain('LOCAL CHECK.');
    expect(d).toContain(`<p data-testid="di-dl-local-note">${LOCAL_NOTE}</p>`);
    expect(d).toContain(`<p data-testid="di-dl-performs-note">${PERFORMS_NOTE}</p>`);
    const ask = d.match(/<button[^>]*data-testid="di-dl-advance"[^>]*>/g) ?? [];
    expect(ask).toHaveLength(1);
    expect(ask[0]).toContain('disabled');
    expect(d).toMatch(/<input type="checkbox" data-testid="di-dl-confirm"/);
  });

  it('confirmed: the ask control is enabled, and it carries exactly the displayed binding', () => {
    const s = run(readWith(reply(7, 'status', 'ok', null, null, statusAnswer())), { type: 'confirm', on: true });
    const d = section(render(linkedRow(), control(s)));
    expect((d.match(/<button[^>]*data-testid="di-dl-advance"[^>]*>/) ?? [''])[0]).not.toContain('disabled');
    expect(m.advanceMessage(s, 8)).toEqual({ type: 'diDeliveryRequest', seq: 8, op: 'advanceOnce', deliveryId: ID, confirmation: binding() });
  });

  it('no typed id: no text field, no textarea, and no merge or hold word anywhere in Delivery', () => {
    for (const s of [run(LINKED_START(), { type: 'linked', deliveryId: ID }), readWith(reply(7, 'status', 'ok', null, null, statusAnswer()))]) {
      const d = section(render(linkedRow(), control(s)));
      expect(d).not.toMatch(/<input(?![^>]*type="checkbox")[^>]*>/);
      expect(d).not.toMatch(/<textarea\b/);
      expect(text(d)).not.toMatch(/merg|\bhold/i);
    }
  });

  it('only notices about THIS delivery are shown here; one about another delivery stays out of this Mission', () => {
    const about = (deliveryId: string | null, heading: string) => ({ id: heading.length, kind: 'info', op: 'advanceOnce', heading, text: '', deliveryId });
    const s = { ...run(LINKED_START(), { type: 'linked', deliveryId: ID }), notices: [about(ID, 'About this one'), about(OTHER, 'About another one'), about(null, 'About none')] };
    const d = section(render(linkedRow(), control(s)));
    expect(d).toContain('About this one');
    expect(d).toContain('About none');
    expect(d).not.toContain('About another one');
    expect(d).not.toContain(OTHER);
  });

  it('STORE IDENTITY: an id the adapter\'s store has no record of arrives as the adapter\'s OWN refusal, with its code, and no step', () => {
    // the relay's reply to the adapter's {ok: false, problem: "pr_delivery_missing_record"} (server: diDeliveryLinkedRefusal.test.ts)
    const s = readWith(reply(7, 'status', 'refused', 'pr_delivery_missing_record', null, { ok: false, problem: 'pr_delivery_missing_record' }));
    expect(s.view).toBeNull();
    expect(s.notices).toHaveLength(1);
    expect(s.notices[0]).toMatchObject({ kind: 'refusal', heading: 'The adapter refused to read this delivery', deliveryId: ID });
    expect(s.notices[0].text).toContain('pr_delivery_missing_record');
    const d = section(render(linkedRow(), control(s)));
    expect(d).toContain('The adapter refused to read this delivery');
    expect(d).toContain('pr_delivery_missing_record');
    expect(text(d)).toContain(STORE);
    expect(d).not.toContain('data-testid="di-dl-advance"');
    expect(d).not.toContain('data-testid="di-dl-confirm"');
    expect(text(d)).not.toMatch(/no such delivery|does not exist|nothing (happened|changed)|safe to retry/i);
  });
});

describe('Delivery under Swarm, NOT linked: the reason, and nothing to read or confirm', () => {
  const cases = (): Array<[string, DiScanRow, DiDeliveryControl | null, string]> => [
    ...Object.entries(WORDS)
      .filter(([state]) => state !== 'unlinked_model_not_recorded')
      .map(([state, words]): [string, DiScanRow, DiDeliveryControl | null, string] => [
        state,
        rows({ state: state as Link['state'], deliveryId: null, basis: null }).find((x) => x.id === HERD)!,
        control(LINKED_START()),
        words,
      ]),
    ['Mission Core', rows(null).find((x) => x.id === CORE)!, control(LINKED_START()), WORDS.unlinked_model_not_recorded!],
    ['not reported', rows(null, null).find((x) => x.id === HERD)!, control(LINKED_START()), NOT_REPORTED],
    ['linked, but no session', linkedRow(), null, NO_SESSION],
  ];

  it('each says its reason, and draws no read, no confirmation and no step control', () => {
    for (const [name, row, session, words] of cases()) {
      const d = section(render(row, session));
      expect(text(d), name).toContain(words);
      expect(d, name).not.toContain('data-testid="di-scan-delivery-read"');
      expect(d, name).not.toContain('data-testid="di-dl-confirm"');
      expect(d, name).not.toContain('data-testid="di-dl-advance"');
      expect(d, name).not.toMatch(/<input\b/);
      expect(text(d), name).not.toMatch(/merg|\bhold/i);
    }
  });

  it('its steps stay as they were: not read, every one locked', () => {
    for (const [name, row, session] of cases()) {
      const d = section(render(row, session));
      expect(stepsOf(d).map((s) => s.gate), name).toEqual(['locked', 'locked', 'locked', 'locked']);
      for (const s of stepsOf(d)) expect(text(s.body), `${name} ${s.id}`).toContain('Delivery state is not read in this layout preview.');
    }
  });
});

describe('the surfaces, established', () => {
  it('the Missions list surfaces no delivery: no id and no link words for any Mission', () => {
    const all = rows({ state: 'linked', deliveryId: ID, basis: 'record_mission_binding' });
    const list = render(all[0]!, control(readWith(reply(7, 'status', 'ok', null, null, statusAnswer()))), 'missions', all);
    expect(list).not.toContain(ID);
    expect(text(list)).not.toContain(LINKED_WORDS);
    expect(text(list)).not.toContain(WORDS.unlinked_model_not_recorded);
    expect(list).not.toContain('di-scan-delivery-read');
  });

  it('the specimen\'s Delivery is unchanged by a session: the same markup with or without one', () => {
    expect(DI_SCAN_LOCK.length).toBeGreaterThan(0);
    for (const row of DI_SCAN_LOCK) {
      expect(section(render(row, null)), row.id).not.toBe('');
      expect(section(render(row, control(readWith(reply(7, 'status', 'ok', null, null, statusAnswer()))))), row.id).toBe(section(render(row, null)));
    }
  });
});
