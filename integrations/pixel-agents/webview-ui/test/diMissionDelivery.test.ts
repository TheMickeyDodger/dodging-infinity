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
import { spawnSync } from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';

import type { DiDeliveryReply, DiMission, DiMissionDeliveryHandoff, DiOfficeState } from '../../core/src/messages.ts';
import * as model from '../src/components/diDeliveryModel.ts';
import { deliveryActionTarget, DI_SCAN_LOCK, type DiScanRow, returnPathClaim, scanRowsFromOffice, shellWord } from '../src/components/diScanModel.ts';
import { DiScanPanel } from '../src/components/DiScanPanel.tsx';
import type { DiDeliveryControl } from '../src/hooks/useDiDelivery.ts';

type Any = Record<string, any>; // eslint-disable-line @typescript-eslint/no-explicit-any
const m = model as unknown as Any;

const ID = `prd-${'a'.repeat(24)}`;
const OTHER = `prd-${'b'.repeat(24)}`;
const DIGEST = 'c'.repeat(64);
const LOCAL_NOTE =
  'This confirmation is a local check. The delivery was authorized once, by the human ceremony, for these steps. This confirmation is not recorded in the delivery record.';
const PERFORMS_NOTE =
  'This office performs ONE confirmed step at a time (COMMIT, PUSH or PR_CREATE; never the base refresh, never every step).';
const WORKFLOW_MODEL = 'workflow_authority.workflow_phase/2';

// ── the words, copied here, never read from the code under test ─────────────────────────────────────────────────────────────────
const SCOPE =
  'This Mission’s delivery is the one record linked to it. No delivery id is typed here, and no other delivery can be chosen. Only notices about this delivery are shown here.';
const STORE =
  'The catalog’s delivery store is not shown to be the one the delivery adapter reads. If the adapter has no record of this delivery, its refusal is shown as it answered.';
const LINKED_WORDS =
  'Linked by the delivery record’s own Mission binding. A link names which delivery is read here; it authorizes nothing.';
const WORDS: Record<string, string> = {
  unlinked_model_not_recorded:
    'Not known: delivery records do not name a Mission of this kind, so no delivery is linked.',
  unlinked_store_not_registered: 'Not known: the catalog has no delivery store registered.',
  unlinked_store_unavailable:
    'Not known: the catalog’s delivery store could not be read, or a record in it did not validate.',
  unlinked_reader_failed: 'Not known: the catalog’s delivery link reader failed.',
  unlinked_no_record: 'No delivery record in the catalog’s delivery store names this Mission.',
  unlinked_ambiguous:
    'More than one delivery record names this Mission, or another Mission has its id; none is chosen.',
  unlinked_sources_incomplete:
    'Not known: a workflow source was not read, so this Mission’s id is not known to be unique.',
  unlinked_id_not_addressable:
    'The one record that names this Mission has a delivery id of a shape this office cannot address.',
  unlinked_repository_not_recorded:
    'This Herdr task’s workspace has no origin URL this office could read, so no delivery is linked.',
  unlinked_task_worktree_not_recorded:
    'This Herdr task has no task id, or its worktree has no git directory this office could read, so no delivery is linked. This is not the same as no record.',
};
const TASK_WORKTREE_WORDS =
  'Linked because one delivery record was made for this task, in this task’s own worktree: its sealed task id and its sealed worktree are both this task’s. A link names which delivery is read here; it authorizes nothing.';
const LEGACY_WORDS =
  'Linked because this workspace’s origin matches one delivery record’s repository. A link names which delivery is read here; it authorizes nothing. This older catalog matches by origin only, which every worktree of that origin shares, so this does not show the record was made for this task or this worktree. It is shown for reference only. This window does not read its status or offer Commit, Push or Open pull request on it, because it may belong to another task or another worktree. A catalog that links by task and worktree is needed before this office acts on a delivery for this task.';
const NOT_REPORTED =
  'Not known: this office’s catalog does not report a delivery link for this Mission.';
const NO_SESSION = 'This window has no delivery session, so this delivery is not read here.';
const STEP_READY = 'Eligible now, as the record states it. Confirm it below.';
const STEP_OTHER = 'Not the step eligible now, as the adapter’s answer states it.';
const STEP_UNREAD = 'Read this delivery’s status below to see which step is eligible.';
const ACCEPT_CI = 'Accepting CI results is not connected in this office.';
const READ_LABEL = 'Read this delivery’s status';

// ── the adapter's answer and the session state ──────────────────────────────────────────────────────────────────────────────────
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
const delivery = (id = ID): Any => ({
  delivery_id: id,
  revision: 4,
  phase: 'base_refreshed',
  authorization: {
    valid: true,
    problem: null,
    human_identity: 'travis',
    allowed_actions: ['commit', 'push', 'pr_create'],
    mode: 'pr_create',
  },
  base_refresh: {
    state: 'succeeded',
    receipt_id: 'rcpt-base-1',
    receipt_state: 'succeeded',
    attempts: 1,
    observed: null,
  },
  commit: {
    state: 'pending',
    receipt_id: null,
    receipt_state: null,
    attempts: 0,
    observed: null,
    commit_oid: null,
  },
  push: {
    state: 'pending',
    receipt_id: null,
    receipt_state: null,
    attempts: 0,
    observed: null,
    remote_oid: null,
  },
  pr_url: null,
  blocker: null,
  next_action: { action: 'run_step', step: 'COMMIT' },
});
const statusAnswer = (o: Any = office(), id = ID): Any => ({
  ok: true,
  status: { delivery: delivery(id), office: o },
});
const reply = (
  seq: number,
  op: 'status' | 'advanceOnce',
  state: DiDeliveryReply['state'],
  problem: string | null,
  reason: string | null,
  answer: Any | null,
): DiDeliveryReply =>
  ({ type: 'diDeliveryReply', seq, op, state, problem, reason, answer }) as DiDeliveryReply;
const LINKED_START = (): Any => ({ ...m.INITIAL_DL_STATE, subject: 'linked' });
const run = (start: Any, ...events: Any[]): Any => events.reduce((s, e) => m.reduce(s, e), start);
/** The real model's state after a status read of the linked id answered with `answer`. */
const readWith = (answer: DiDeliveryReply, id = ID): Any =>
  run(
    LINKED_START(),
    { type: 'linked', deliveryId: id },
    { type: 'sent', seq: 7, op: 'status', deliveryId: id },
    { type: 'reply', reply: answer, nowMs: 1 },
  );

const noop = () => undefined;
const control = (state: Any): DiDeliveryControl =>
  ({
    state,
    open: noop,
    close: noop,
    setEntered: noop,
    setLinked: noop,
    setConfirmed: noop,
    readStatus: noop,
    askForStep: noop,
    dismiss: noop,
  }) as unknown as DiDeliveryControl;

// ── rows: the real projection of an emitted office state, its Missions' links edited (labelled synthetic variations) ─────────────
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const historical = JSON.parse(
  fs.readFileSync(
    path.join(root, 'test/fixtures/di-office-state-evidence-historical-v2.FIXTURE.json'),
    'utf-8',
  ),
) as { states: Record<'live', DiOfficeState> };
const HERD = 'herd:20261014-000000-aaaaaa@f69ae73eadfe';
const CORE = 'mc1:mn-04be029a813f736fe6877a02ecb5a9b3';
type Link = NonNullable<DiMission['v2']['deliveryLink']>;
/** `workflow`: the herd Mission, made a workflow Mission (the producer links only those), with `link`; Mission Core with `core`. */
function rows(
  link: Link | null,
  core: Link | null = { state: 'unlinked_model_not_recorded', deliveryId: null, basis: null },
): DiScanRow[] {
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
/** The herd Mission AS a Herdr task (the model the producer pairs the task-and-worktree link with), with `link`. */
function herdrRows(link: Link, handoff?: DiMissionDeliveryHandoff): DiScanRow[] {
  const state = structuredClone(historical.states.live);
  for (const mission of state.missions.missions) {
    if (mission.missionRef === HERD) {
      mission.lifecycleModel = 'herdr.task/1';
      mission.v2 = { ...mission.v2, deliveryLink: link, ...(handoff === undefined ? {} : { deliveryHandoff: handoff }) };
    }
  }
  return scanRowsFromOffice(state);
}
/** An observed handoff block as the producer emits it (a fixture's own state: nothing here is this mission's). */
const HANDOFF = (over: Partial<DiMissionDeliveryHandoff> = {}): DiMissionDeliveryHandoff => ({
  state: 'observed',
  linkBasis: 'record_task_worktree_binding',
  repository: "/work/my wt's tree",
  engineeringTaskId: 'task-a',
  gitDirRealpath: '/main/.git/worktrees/wt',
  branch: { state: 'named', ref: 'refs/heads/feature/p1' },
  evidence: { state: 'collected', reason: null },
  stagedCandidate: 'not_determined',
  workflowId: 'human_supplied',
  blockers: [],
  ...over,
});
const NO_RECORD: Link = { state: 'unlinked_no_record', deliveryId: null, basis: null };
const handoffSection = (html: string) => /<section[^>]*data-testid="di-scan-delivery-handoff"[\s\S]*?<\/section>/.exec(html)?.[0] ?? '';
const herdrRow = (link: Link, handoff?: DiMissionDeliveryHandoff) => herdrRows(link, handoff).find((item) => item.id === HERD)!;
const linkedRow = (id = ID) =>
  rows({ state: 'linked', deliveryId: id, basis: 'record_mission_binding' }).find(
    (r) => r.id === HERD,
  )!;
const render = (
  row: DiScanRow,
  session: DiDeliveryControl | null,
  view: 'mission' | 'missions' = 'mission',
  all: DiScanRow[] = [row],
) =>
  renderToStaticMarkup(
    createElement(DiScanPanel, {
      rows: all,
      initialSelectedId: row.id,
      initialView: view,
      onMinimize: noop,
      missionDelivery: session,
    }),
  );
const section = (html: string) =>
  /<section[^>]*data-testid="di-scan-delivery"[\s\S]*?<\/section>/.exec(html)?.[0] ?? '';
const text = (markup: string) =>
  markup
    .replace(/<[^>]+>/g, ' ')
    .replace(/&#x27;/g, "'")
    .replace(/&quot;/g, '"')
    .replace(/&amp;/g, '&')
    .replace(/\s+/g, ' ');
const stepsOf = (d: string) =>
  [
    ...d.matchAll(
      /<li[^>]*data-testid="di-scan-step" data-step="([a-z_]+)" data-gate="([a-z]+)"[^>]*>([\s\S]*?)<\/li>/g,
    ),
  ].map((x) => ({
    id: x[1]!,
    gate: x[2]!,
    body: x[3]!,
  }));

describe("the model, linked mode: the ONE id is the Mission's link, never typed", () => {
  it('a linked session reads exactly the linked id', () => {
    const s = run(LINKED_START(), { type: 'linked', deliveryId: ID });
    expect(m.statusMessage(s, 3)).toEqual({
      type: 'diDeliveryRequest',
      seq: 3,
      op: 'status',
      deliveryId: ID,
    });
  });

  it('typed input is ignored in a linked session: no manual id inside a Mission', () => {
    const s = run(
      LINKED_START(),
      { type: 'linked', deliveryId: ID },
      { type: 'entered', text: OTHER },
    );
    expect(m.statusMessage(s, 3)?.deliveryId).toBe(ID);
    expect(JSON.stringify(s)).not.toContain(OTHER);
  });

  it('a Mission with no link reads nothing and offers no step', () => {
    const s = run(
      LINKED_START(),
      { type: 'linked', deliveryId: ID },
      { type: 'linked', deliveryId: null },
    );
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

describe("the row carries the producer's link, in words", () => {
  it('linked: the one id, with the basis in words', () => {
    expect(linkedRow().deliveryLink).toEqual({
      kind: 'linked',
      deliveryId: ID,
      words: LINKED_WORDS,
    });
  });

  // The two scenarios below are SEPARATE tests on purpose, never one parameterised case over two bases: a shared body lets the canonical
  // path's expectations leak onto the legacy one, which is exactly how a legacy origin-only link could pass while being wrong.
  it('SCENARIO 1, a CURRENT catalog: a Herdr task linked by task and worktree says so, is read, and is offered its step', () => {
    const row = herdrRows({ state: 'linked', deliveryId: ID, basis: 'record_task_worktree_binding' }).find((item) => item.id === HERD)!;
    expect(row.deliveryLink).toEqual({ kind: 'linked', deliveryId: ID, words: TASK_WORKTREE_WORDS });
    expect(deliveryActionTarget(row.deliveryLink)).toBe(ID);
    const d = section(render(row, control(readWith(reply(7, 'status', 'ok', null, null, statusAnswer())))));
    expect(text(d)).toContain(`The delivery linked to this Mission: ${ID}. ${TASK_WORKTREE_WORDS}`);
    expect(d).toContain('data-testid="di-scan-delivery-read"');
    expect(stepsOf(d).map((x) => x.gate)).not.toContain('withheld');
    expect(stepsOf(d).map((x) => x.gate)).not.toContain('unlinked');
    expect(text(d)).not.toContain('origin only');
    expect(d).not.toContain('Layout preview');
  });

  it('SCENARIO 2, an OLDER catalog with this window: an origin-only link is shown for reference and acted on by NOTHING', () => {
    const row = herdrRows({ state: 'linked', deliveryId: ID, basis: 'record_repository_binding' }).find((item) => item.id === HERD)!;
    // it is its own kind, never `linked`
    expect(row.deliveryLink).toEqual({ kind: 'origin_only', deliveryId: ID, words: LEGACY_WORDS });
    // THE BEHAVIOUR: the delivery session is given no id, so it can read nothing and act on nothing
    expect(deliveryActionTarget(row.deliveryLink)).toBeNull();
    // the most hostile case: the session ALREADY holds this very delivery's status with an eligible step and a confirmation
    const d = section(render(row, control(readWith(reply(7, 'status', 'ok', null, null, statusAnswer())))));
    expect(d).not.toContain('data-testid="di-scan-delivery-read"');
    expect(d).not.toContain('data-testid="di-dl-confirm"');
    expect(d).not.toContain('data-testid="di-dl-advance"');
    expect(d).not.toMatch(/<input\b/);
    expect(d).not.toContain('data-testid="di-scan-delivery-scope"');
    expect(stepsOf(d).map((x) => x.gate)).toEqual(['withheld', 'withheld', 'withheld', 'withheld']);
    for (const step of stepsOf(d)) {
      expect(text(step.body)).toContain('Not offered');
      expect(text(step.body)).not.toContain('Locked');
      expect(text(step.body)).not.toContain('Ready');
    }
    // the words: the id is named as a reference, the legacy wording and the refusal are there, and nothing says it is THIS task's record
    expect(text(d)).toContain(`A delivery matched by this workspace’s origin only: ${ID}.`);
    expect(text(d)).toContain(LEGACY_WORDS);
    expect(text(d)).not.toContain('The delivery linked to this Mission');
    expect(text(d)).not.toContain(TASK_WORKTREE_WORDS);
    expect(text(d)).not.toContain('made for this task, in this task’s own worktree');
    expect(text(d)).not.toContain('Eligible now');
    expect(d).not.toContain('Layout preview');
  });

  it('B4: a Herdr task\'s "no record" and "ambiguous" name its task and worktree; a workflow Mission\'s words are unchanged', () => {
    const HERDR_NO_RECORD =
      'No delivery record in the catalog’s delivery store was made for this task in this worktree. A record made from another worktree, or for another task, is not this task’s.';
    const HERDR_AMBIGUOUS =
      'More than one delivery record was made for this task in this worktree, or the catalog lists this task and worktree more than once; none is chosen.';
    for (const [state, words] of [
      ['unlinked_no_record', HERDR_NO_RECORD],
      ['unlinked_ambiguous', HERDR_AMBIGUOUS],
    ] as const) {
      const herdr = herdrRows({ state, deliveryId: null, basis: null }).find((item) => item.id === HERD)!;
      expect(herdr.deliveryLink, state).toEqual({ kind: 'unlinked', state, words });
      // neither speaks of a Mission's id, a "Mission binding", or a shared origin: those are not this task's causes
      expect(words).not.toMatch(/another Mission has its id|names this Mission|origin/);
      const workflow = rows({ state, deliveryId: null, basis: null }).find((item) => item.id === HERD)!;
      expect(workflow.deliveryLink, state).toEqual({ kind: 'unlinked', state, words: WORDS[state]! });
    }
  });

  it('an unknown basis is NOT guessed at: nothing is linked, nothing is read, and the person is told it is not known', () => {
    const row = herdrRows({
      state: 'linked',
      deliveryId: ID,
      basis: 'record_canonical_worktree_binding' as unknown as Link['basis'],
    }).find((item) => item.id === HERD)!;
    expect(row.deliveryLink).toEqual({
      kind: 'unlinked',
      state: 'linked_basis_not_recognised',
      words: 'Not known: this office was told a delivery is linked by a rule this window does not recognise, so it does not show that delivery as this Mission’s.',
    });
    expect(deliveryActionTarget(row.deliveryLink)).toBeNull();
    const d = section(render(row, control(readWith(reply(7, 'status', 'ok', null, null, statusAnswer())))));
    expect(d).not.toContain('data-testid="di-scan-delivery-read"');
    expect(d).not.toContain('data-testid="di-dl-confirm"');
    expect(text(d)).not.toContain(ID);
  });

  it('only a `linked` link is ever an id the delivery session may act on (the decision point, over every kind)', () => {
    expect(deliveryActionTarget(undefined)).toBeNull();
    expect(deliveryActionTarget({ kind: 'not_reported', words: 'w' })).toBeNull();
    expect(deliveryActionTarget({ kind: 'unlinked', state: 'unlinked_no_record', words: 'w' })).toBeNull();
    expect(deliveryActionTarget({ kind: 'origin_only', deliveryId: ID, words: 'w' })).toBeNull();
    expect(deliveryActionTarget({ kind: 'linked', deliveryId: ID, words: 'w' })).toBe(ID);
  });

  it('every unlinked state is its own reason; Mission Core is not known', () => {
    for (const [state, words] of Object.entries(WORDS)) {
      const r =
        state === 'unlinked_model_not_recorded'
          ? rows(null).find((x) => x.id === CORE)!
          : rows({ state: state as Link['state'], deliveryId: null, basis: null }).find(
              (x) => x.id === HERD,
            )!;
      expect(r.deliveryLink, state).toEqual({ kind: 'unlinked', state, words });
    }
  });

  it('an office whose answer carries no link says it is not reported: unknown, never "none"', () => {
    const r = rows(null, null);
    // the rows iterated are asserted first: a loop over nothing asserts nothing (round 59)
    expect(r.map((row) => row.id).sort()).toEqual([CORE, HERD].sort());
    for (const row of r)
      expect(row.deliveryLink, row.id).toEqual({ kind: 'not_reported', words: NOT_REPORTED });
  });

  it('the specimen has no link: its Delivery stays the labelled layout', () => {
    expect(DI_SCAN_LOCK.length).toBeGreaterThan(0);
    for (const row of DI_SCAN_LOCK) expect(row.deliveryLink, row.id).toBeUndefined();
  });
});

describe('Delivery under Swarm, linked: the one delivery, read and offered here', () => {
  it('before a read: the link, the scope and store sentences, ONE read control, the steps waiting on the read, Accept CI not connected', () => {
    const d = section(
      render(linkedRow(), control(run(LINKED_START(), { type: 'linked', deliveryId: ID }))),
    );
    expect(d).not.toBe('');
    expect(text(d)).toContain(`The delivery linked to this Mission: ${ID}. ${LINKED_WORDS}`);
    expect(text(d)).toContain(SCOPE);
    expect(text(d)).toContain(STORE);
    const read =
      d.match(/<button[^>]*data-testid="di-scan-delivery-read"[^>]*>[\s\S]*?<\/button>/g) ?? [];
    expect(read).toHaveLength(1);
    expect(read[0]).not.toContain('disabled');
    expect(text(read[0]!)).toContain(READ_LABEL);
    expect(stepsOf(d).map((s) => `${s.id}:${s.gate}`)).toEqual([
      'commit:locked',
      'push:locked',
      'pr:locked',
      'accept_ci:locked',
    ]);
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
    expect(stepsOf(d).map((x) => `${x.id}:${x.gate}`)).toEqual([
      'commit:ready',
      'push:locked',
      'pr:locked',
      'accept_ci:locked',
    ]);
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
    const s = run(readWith(reply(7, 'status', 'ok', null, null, statusAnswer())), {
      type: 'confirm',
      on: true,
    });
    const d = section(render(linkedRow(), control(s)));
    expect((d.match(/<button[^>]*data-testid="di-dl-advance"[^>]*>/) ?? [''])[0]).not.toContain(
      'disabled',
    );
    expect(m.advanceMessage(s, 8)).toEqual({
      type: 'diDeliveryRequest',
      seq: 8,
      op: 'advanceOnce',
      deliveryId: ID,
      confirmation: binding(),
    });
  });

  it('no typed id: no text field, no textarea, and no merge or hold word anywhere in Delivery', () => {
    for (const s of [
      run(LINKED_START(), { type: 'linked', deliveryId: ID }),
      readWith(reply(7, 'status', 'ok', null, null, statusAnswer())),
    ]) {
      const d = section(render(linkedRow(), control(s)));
      expect(d).not.toMatch(/<input(?![^>]*type="checkbox")[^>]*>/);
      expect(d).not.toMatch(/<textarea\b/);
      expect(text(d)).not.toMatch(/merg|\bhold/i);
    }
  });

  it('only notices about THIS delivery are shown here; one about another delivery stays out of this Mission', () => {
    const about = (deliveryId: string | null, heading: string) => ({
      id: heading.length,
      kind: 'info',
      op: 'advanceOnce',
      heading,
      text: '',
      deliveryId,
    });
    const s = {
      ...run(LINKED_START(), { type: 'linked', deliveryId: ID }),
      notices: [
        about(ID, 'About this one'),
        about(OTHER, 'About another one'),
        about(null, 'About none'),
      ],
    };
    const d = section(render(linkedRow(), control(s)));
    expect(d).toContain('About this one');
    expect(d).toContain('About none');
    expect(d).not.toContain('About another one');
    expect(d).not.toContain(OTHER);
  });

  it("STORE IDENTITY: an id the adapter's store has no record of arrives as the adapter's OWN refusal, with its code, and no step", () => {
    // the relay's reply to the adapter's {ok: false, problem: "pr_delivery_missing_record"} (server: diDeliveryLinkedRefusal.test.ts)
    const s = readWith(
      reply(7, 'status', 'refused', 'pr_delivery_missing_record', null, {
        ok: false,
        problem: 'pr_delivery_missing_record',
      }),
    );
    expect(s.view).toBeNull();
    expect(s.notices).toHaveLength(1);
    expect(s.notices[0]).toMatchObject({
      kind: 'refusal',
      heading: 'The adapter refused to read this delivery',
      deliveryId: ID,
    });
    expect(s.notices[0].text).toContain('pr_delivery_missing_record');
    const d = section(render(linkedRow(), control(s)));
    expect(d).toContain('The adapter refused to read this delivery');
    expect(d).toContain('pr_delivery_missing_record');
    expect(text(d)).toContain(STORE);
    expect(d).not.toContain('data-testid="di-dl-advance"');
    expect(d).not.toContain('data-testid="di-dl-confirm"');
    expect(text(d)).not.toMatch(
      /no such delivery|does not exist|nothing (happened|changed)|safe to retry/i,
    );
  });
});

describe('Delivery under Swarm, NOT linked: the reason, and nothing to read or confirm', () => {
  const cases = (): Array<[string, DiScanRow, DiDeliveryControl | null, string]> => [
    ...Object.entries(WORDS)
      .filter(([state]) => state !== 'unlinked_model_not_recorded')
      .map(([state, words]): [string, DiScanRow, DiDeliveryControl | null, string] => [
        state,
        rows({ state: state as Link['state'], deliveryId: null, basis: null }).find(
          (x) => x.id === HERD,
        )!,
        control(LINKED_START()),
        words,
      ]),
    [
      'Mission Core',
      rows(null).find((x) => x.id === CORE)!,
      control(LINKED_START()),
      WORDS.unlinked_model_not_recorded!,
    ],
    [
      'not reported',
      rows(null, null).find((x) => x.id === HERD)!,
      control(LINKED_START()),
      NOT_REPORTED,
    ],
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

  it('its steps say no delivery is linked, with the catalog reason (not "Locked": that states a gate); a Herdr task is not shown a layout-preview lock only because it is a Herdr task', () => {
    for (const [name, row, session, words] of cases()) {
      const d = section(render(row, session));
      expect(
        stepsOf(d).map((s) => s.gate),
        name,
      ).toEqual(Array(4).fill(row.deliveryLink?.kind === 'linked' ? 'locked' : 'unlinked'));
      const linkedWords =
        row.deliveryLink && row.deliveryLink.kind !== 'linked' ? row.deliveryLink.words : null;
      for (const s of stepsOf(d)) {
        expect(text(s.body), `${name} ${s.id}`).toContain(
          linkedWords ?? 'Delivery state is not read in this layout preview.',
        );
      }
      if (row.sourceKind === 'herdr_task' && row.deliveryLink?.kind !== 'linked') {
        expect(d, name).not.toContain('Layout preview');
        expect(d, name).not.toContain('Delivery state is not read in this layout preview.');
        expect(d, name).not.toContain('delivery records name only workflow Missions');
        expect(d, name).not.toContain('This Herdr task has no delivery to commit');
        expect(text(d), name).toContain(words);
      }
    }
  });
});

// ── THE DELIVERY HANDOFF: how a record is created for a Herdr task, reachable from the task's own Delivery, never prose ────────────────────
// Each scenario is its OWN test. The return-path statement is a decision made in behaviour (returnPathClaim) from the producer's EXPLICIT
// declaration, so a fresh task with NO record is told how its record comes back; legacy and unknown are refused; silence is not a declaration.
const CANONICAL_CLAIM =
  'If the record is written to the delivery store this catalog reads, a record made from this task, in this task’s worktree, comes back to THIS task: this catalog links a Herdr task by its worktree’s git directory and its task id, and the ceremony seals both into the record.';
const STORE_SETUP =
  ' The ceremony always writes to the per-user default delivery store (~/Library/Application Support/DodgingInfinity/telegram) and has no option to choose another; this catalog reads the store it was started with (--delivery-store). This office has not shown they are the same directory, so it does not promise the link: start the catalog with --delivery-store set to that default store, or the record will not appear here.';
const CANONICAL_RETURN = CANONICAL_CLAIM + STORE_SETUP;
const LEGACY_RETURN =
  'This office cannot promise that a record made from this task comes back to this task: this catalog links a Herdr task by its workspace origin only, which every worktree of that origin shares, so it can show another worktree’s or another task’s record here.';
const NOT_KNOWN_RETURN =
  'Not known: this catalog does not say how it links a Herdr task to a delivery record, so this office does not promise that a record made from this task comes back to this task.';

describe('the delivery handoff: a FRESH task with no record is told how to create one, and how it comes back', () => {
  it("SCENARIO fresh no-record task, current producer: the facts, the exact command in the person's own terminal, and the canonical return path", () => {
    const d = render(herdrRow(NO_RECORD, HANDOFF()), control(readWith(reply(7, 'status', 'ok', null, null, statusAnswer()))));
    const h = handoffSection(d);
    expect(h).not.toBe('');
    expect(text(h)).toContain("Repository /work/my wt's tree");
    expect(text(h)).toContain('Task task-a');
    expect(text(h)).toContain('Worktree git directory /main/.git/worktrees/wt');
    expect(text(h)).toContain('Branch on refs/heads/feature/p1');
    expect(text(h)).toContain('Evidence a COMPLETE task and the latest canonical APPROVE were found');
    expect(text(h)).toContain('Staged candidate not determined here');
    expect(text(h)).toContain('Workflow id you supply it: a Herdr task has no workflow id of its own');
    // the EXACT command, pre-filled with this task's real repository path (one safe shell word), the human's own values left as placeholders
    const command = /<pre[^>]*data-testid="di-scan-delivery-handoff-command"[^>]*>([\s\S]*?)<\/pre>/.exec(h)![1]!.replace(/&#x27;/g, "'").replace(/&quot;/g, '"').replace(/&gt;/g, '>').replace(/&lt;/g, '<').replace(/&amp;/g, '&');
    expect(command).toBe(
      [
        '(',
        '  set -eu',
        "  ENGINE='<path to your Herdr engine checkout: the directory that contains herdctl.py and pr_delivery/>'",
        "  REPO='/work/my wt'\\''s tree'",
        "  TASK='task-a'",
        '  EVIDENCE="$(mktemp -d "${TMPDIR:-/tmp}/herd-evidence-$TASK.XXXXXX")/evidence.json"',
        '  cd "$ENGINE"',
        '  python3 herdctl.py delivery-evidence --repo "$REPO" --expect-task-id "$TASK" > "$EVIDENCE"',
        "  python3 -m pr_delivery authorize --repo \"$REPO\" --workflow-id '<your workflow id>' --herd-evidence \"$EVIDENCE\" \\",
        "    --verification-log '<path to your green verification log>' --verification-command '<the command that produced it>' \\",
        "    --verification-exit-status 0 --title '<pull request title>'",
        ')',
      ].join('\n'),
    );
    expect(text(h)).toContain('Edit the ENGINE line first. The route runs from your Herdr engine checkout');
    expect(text(h)).toContain('stops at the first failure');
    expect(text(h)).toContain('refuses unless the task current in that repository is task-a');
    expect(text(h)).toContain('This office runs nothing, stages nothing, changes no branch and approves nothing');
    expect(text(h)).toContain('that is not a promise it will accept this worktree');
    expect(text(h)).not.toMatch(/\bready\b|may be created|can be created|go ahead|approved/i);
    expect(h).toContain('data-return-path="canonical"');
    expect(text(h)).toContain(CANONICAL_RETURN);
    expect(d).not.toContain('data-testid="di-scan-delivery-read"');
    expect(d).not.toContain('data-testid="di-dl-confirm"');
    expect(deliveryActionTarget(herdrRow(NO_RECORD, HANDOFF()).deliveryLink)).toBeNull();
  });

  it("SCENARIO this mission's own situation, from a fixture: detached HEAD, no COMPLETE and no APPROVE are stated as the real reasons, in the collector's words", () => {
    const reason = "task 20261030-144734-e1b5c9 status is 'ACTIVE', not COMPLETE";
    const h = handoffSection(
      render(
        herdrRow(NO_RECORD, HANDOFF({ branch: { state: 'detached', ref: null }, evidence: { state: 'refused', reason }, blockers: ['head_detached', 'evidence_not_collected'] })),
        control(LINKED_START()),
      ),
    );
    expect(text(h)).toContain('Branch HEAD is detached (no named branch)');
    expect(text(h)).toContain(`Evidence the collector refuses: ${reason}`);
    expect(text(h)).toContain('As things stand the ceremony would stop:');
    expect(text(h)).toContain('HEAD is detached, and the ceremony needs a named branch checked out (it stops with “HEAD is not on a named branch”)');
    expect(text(h)).toContain('the task has no collectable evidence (a COMPLETE task and the latest canonical APPROVE)');
    expect(text(h)).toContain('Putting a named branch in place, and staging the exact candidate for a pull request, are what a delivery you separately approve would require. This window does neither.');
    expect(text(h)).not.toMatch(/\bready\b/i);
    expect(text(h)).not.toContain('that is not a promise');
  });

  it('SCENARIO no store registered, current producer: still the canonical statement, with the honest condition that nothing can link yet', () => {
    const h = handoffSection(render(herdrRow({ state: 'unlinked_store_not_registered', deliveryId: null, basis: null }, HANDOFF()), control(LINKED_START())));
    expect(h).toContain('data-return-path="canonical"');
    expect(text(h)).toContain(CANONICAL_RETURN + ' The catalog has no readable delivery store right now, so nothing can link until it does.');
  });
});

// ── THE PREPARED ROUTE, EXECUTED. The route is a command for the person's own terminal; its guarantees are properties of what it DOES, so it is
// run here under /bin/sh against STAND-IN programs (a python3 that records its arguments and its working directory, and plays the collector's
// refusals). The real collector and the real ceremony are never started: the stand-in directory is the only thing on PATH that is called python3.
describe('the prepared route, executed: it stops on failure, names where it runs, writes task-scoped evidence and refuses a different task', () => {
  const PLACEHOLDER = '<path to your Herdr engine checkout: the directory that contains herdctl.py and pr_delivery/>';
  const REPO_PATH = "/work/my wt's tree";

  function routeOf(): string {
    const row = herdrRow(NO_RECORD, HANDOFF());
    const command = row.deliveryHandoff?.command;
    if (command === null || command === undefined) throw new Error('no route was prepared');
    return command;
  }

  /** A scratch world: an engine directory, a stand-in bin, a log, a TMPDIR, and a directory the person is "in" that is none of those. */
  function world() {
    const root = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), 'herdroute-')));
    const dirs = { root, engine: path.join(root, 'engine'), bin: path.join(root, 'bin'), tmp: path.join(root, 'tmp'), elsewhere: path.join(root, 'elsewhere') };
    for (const d of [dirs.engine, dirs.bin, dirs.tmp, dirs.elsewhere]) fs.mkdirSync(d);
    const log = path.join(root, 'calls.log');
    fs.writeFileSync(
      path.join(dirs.bin, 'python3'),
      [
        '#!/bin/sh',
        'echo "pwd=$(pwd)" >> "$STUB_LOG"',
        'echo "argv=$*" >> "$STUB_LOG"',
        'case "$1" in',
        '  herdctl.py)',
        '    expect=""',
        '    while [ "$#" -gt 0 ]; do [ "$1" = "--expect-task-id" ] && expect="$2"; shift; done',
        '    [ "${STUB_COLLECTOR_FAIL:-0}" = 1 ] && { echo "delivery evidence unavailable: stub failure" >&2; exit 1; }',
        '    if [ "$expect" != "$STUB_CURRENT_TASK" ]; then echo "the current task is $STUB_CURRENT_TASK, not the expected $expect" >&2; exit 1; fi',
        '    echo "{\\"engineering_complete\\":{\\"task_id\\":\\"$expect\\"}}"',
        '    ;;',
        '  -m)',
        '    echo "AUTHORIZE-RAN" >> "$STUB_LOG"',
        '    ;;',
        'esac',
        '',
      ].join('\n'),
      { mode: 0o755 },
    );
    return { ...dirs, log };
  }
  function run(w: ReturnType<typeof world>, command: string, env: Record<string, string> = {}) {
    const edited = command.replace(PLACEHOLDER, w.engine);
    const result = spawnSync('/bin/sh', ['-c', edited], {
      cwd: w.elsewhere,
      encoding: 'utf8',
      timeout: 30_000,
      env: { PATH: `${w.bin}:/usr/bin:/bin`, TMPDIR: w.tmp, STUB_LOG: w.log, STUB_CURRENT_TASK: 'task-a', ...env },
    });
    const calls = fs.existsSync(w.log) ? fs.readFileSync(w.log, 'utf8') : '';
    return { status: result.status, stderr: result.stderr, calls };
  }
  const cleanup = (w: { root: string }) => {
    if (/^herdroute-/.test(path.basename(w.root)) && path.dirname(w.root) === fs.realpathSync(os.tmpdir())) fs.rmSync(w.root, { recursive: true, force: true });
  };

  it('WHERE IT RUNS: from the engine checkout whatever directory the person is in, naming the task\'s own repository on both steps', () => {
    const w = world();
    try {
      const out = run(w, routeOf());
      expect(out.status).toBe(0);
      const pwds = [...out.calls.matchAll(/^pwd=(.*)$/gm)].map((m) => m[1]);
      expect(pwds).toEqual([w.engine, w.engine]); // both steps ran in the engine checkout, not in `elsewhere`
      const argvs = [...out.calls.matchAll(/^argv=(.*)$/gm)].map((m) => m[1]!);
      expect(argvs[0]).toContain(`herdctl.py delivery-evidence --repo ${REPO_PATH} --expect-task-id task-a`);
      expect(argvs[1]).toContain(`-m pr_delivery authorize --repo ${REPO_PATH} --workflow-id <your workflow id>`);
    } finally {
      cleanup(w);
    }
  });

  it('EVIDENCE: a NEW directory made for the run and named for the task, shared by both steps, never a file in the current directory', () => {
    const w = world();
    try {
      const out = run(w, routeOf());
      expect(out.status).toBe(0);
      const collectorTarget = fs.readdirSync(w.tmp).filter((n) => n.startsWith('herd-evidence-task-a.'));
      expect(collectorTarget).toHaveLength(1);
      const evidence = path.join(w.tmp, collectorTarget[0]!, 'evidence.json');
      expect(JSON.parse(fs.readFileSync(evidence, 'utf8'))).toEqual({ engineering_complete: { task_id: 'task-a' } });
      expect(out.calls).toContain(`--herd-evidence ${evidence}`);
      expect(fs.readdirSync(w.elsewhere)).toEqual([]); // nothing was written where the person was
      expect(fs.readdirSync(w.engine)).toEqual([]);
      // two runs never share evidence (no collision, no stale file picked up)
      run(w, routeOf());
      expect(fs.readdirSync(w.tmp).filter((n) => n.startsWith('herd-evidence-task-a.'))).toHaveLength(2);
    } finally {
      cleanup(w);
    }
  });

  it('STOPS ON FAILURE: when the collector fails the ceremony is NEVER run', () => {
    const w = world();
    try {
      const out = run(w, routeOf(), { STUB_COLLECTOR_FAIL: '1' });
      expect(out.status).not.toBe(0);
      expect(out.stderr).toContain('stub failure');
      expect(out.calls).toContain('herdctl.py delivery-evidence');
      expect(out.calls).not.toContain('AUTHORIZE-RAN');
      expect(out.calls).not.toContain('pr_delivery');
    } finally {
      cleanup(w);
    }
  });

  it('THE IDENTITY HAZARD: prepared for task A, run when task B is current, it REFUSES: the ceremony never runs and nothing of B is sealed', () => {
    const w = world();
    try {
      const out = run(w, routeOf(), { STUB_CURRENT_TASK: 'task-b' });
      expect(out.status).not.toBe(0);
      expect(out.stderr).toContain('not the expected task-a');
      expect(out.calls).toContain('--expect-task-id task-a');
      expect(out.calls).not.toContain('AUTHORIZE-RAN');
      const dirs = fs.readdirSync(w.tmp).filter((n) => n.startsWith('herd-evidence-task-a.'));
      for (const d of dirs) expect(fs.readFileSync(path.join(w.tmp, d, 'evidence.json'), 'utf8')).toBe(''); // no evidence for B was written
    } finally {
      cleanup(w);
    }
  });

  it('an unedited ENGINE line stops the route before anything runs', () => {
    const w = world();
    try {
      const result = spawnSync('/bin/sh', ['-c', routeOf()], {
        cwd: w.elsewhere,
        encoding: 'utf8',
        env: { PATH: `${w.bin}:/usr/bin:/bin`, TMPDIR: w.tmp, STUB_LOG: w.log, STUB_CURRENT_TASK: 'task-a' },
      });
      expect(result.status).not.toBe(0);
      expect(fs.existsSync(w.log)).toBe(false); // neither stand-in was called
    } finally {
      cleanup(w);
    }
  });

  it('the route runs in a subshell: it can end the route but never the person\'s own shell', () => {
    const command = routeOf();
    expect(command.startsWith('(\n  set -eu\n')).toBe(true);
    expect(command.endsWith('\n)')).toBe(true);
  });

  it('a mission with NO current task gets no route at all (there is no task to bind the evidence to)', () => {
    const row = herdrRow(NO_RECORD, HANDOFF({ engineeringTaskId: null, blockers: ['task_id_not_recorded'] }));
    expect(row.deliveryHandoff).toMatchObject({ kind: 'observed', command: null, guards: null });
    expect(handoffSection(render(row, control(LINKED_START())))).not.toContain('data-testid="di-scan-delivery-handoff-command"');
  });
});

describe('the canonical return path is CONDITIONED on the store, never promised across two stores nobody showed to be one', () => {
  it('the canonical wording opens with the condition and names the ceremony\'s default store, the catalog\'s flag, and that sameness is not shown', () => {
    const claim = returnPathClaim(undefined, HANDOFF());
    expect(claim.kind).toBe('canonical');
    expect(claim.words.startsWith('If the record is written to the delivery store this catalog reads,')).toBe(true);
    expect(claim.words).toContain('The ceremony always writes to the per-user default delivery store (~/Library/Application Support/DodgingInfinity/telegram) and has no option to choose another');
    expect(claim.words).toContain('this catalog reads the store it was started with (--delivery-store)');
    expect(claim.words).toContain('This office has not shown they are the same directory, so it does not promise the link');
    expect(claim.words).toContain('start the catalog with --delivery-store set to that default store, or the record will not appear here');
    // and it never states the link unconditionally
    expect(claim.words).not.toMatch(/^A record made from this task/);
  });

  it('the condition is on EVERY canonical statement, with or without a readable store', () => {
    for (const state of ['unlinked_no_record', 'unlinked_store_not_registered', 'unlinked_store_unavailable', 'unlinked_reader_failed']) {
      const claim = returnPathClaim({ kind: 'unlinked', state, words: 'w' }, HANDOFF());
      expect(claim.kind, state).toBe('canonical');
      expect(claim.words, state).toContain('This office has not shown they are the same directory');
    }
  });

  it('the legacy and not-known refusals carry no promise and no store condition to mistake for one', () => {
    const legacy = returnPathClaim({ kind: 'origin_only', deliveryId: ID, words: 'w' }, HANDOFF());
    const unknown = returnPathClaim(undefined, undefined);
    for (const claim of [legacy, unknown]) {
      expect(claim.words).not.toContain('comes back to THIS task');
      expect(claim.words).not.toContain('--delivery-store');
    }
  });
});

describe('the delivery handoff: the three return-path behaviours are distinct in BEHAVIOUR, each its own scenario', () => {
  it('SCENARIO canonical-linked: the record is acted on, so there is no creation section', () => {
    const row = herdrRow({ state: 'linked', deliveryId: ID, basis: 'record_task_worktree_binding' }, HANDOFF());
    const d = render(row, control(readWith(reply(7, 'status', 'ok', null, null, statusAnswer()))));
    expect(handoffSection(d)).toBe('');
    expect(d).toContain('data-testid="di-scan-delivery-read"');
    expect(returnPathClaim(row.deliveryLink, HANDOFF()).kind).toBe('canonical');
  });

  it('SCENARIO legacy-linked (origin only): the section says it cannot promise, whatever the producer ALSO declares', () => {
    const row = herdrRow({ state: 'linked', deliveryId: ID, basis: 'record_repository_binding' }, HANDOFF());
    expect(row.deliveryLink?.kind).toBe('origin_only');
    const h = handoffSection(render(row, control(readWith(reply(7, 'status', 'ok', null, null, statusAnswer())))));
    expect(h).toContain('data-return-path="legacy"');
    expect(text(h)).toContain(LEGACY_RETURN);
    expect(text(h)).not.toContain(CANONICAL_RETURN);
    expect(text(h)).not.toContain('comes back to THIS task');
  });

  it('SCENARIO legacy producer, unlinked, which DECLARES the origin-only basis: refused, not canonical', () => {
    const h = handoffSection(render(herdrRow(NO_RECORD, HANDOFF({ linkBasis: 'record_repository_binding' })), control(LINKED_START())));
    expect(h).toContain('data-return-path="legacy"');
    expect(text(h)).toContain(LEGACY_RETURN);
    expect(text(h)).not.toContain(CANONICAL_RETURN);
  });

  it('SCENARIO an older producer that sends NO handoff block at all: nothing is invented, no command, and the claim is NOT KNOWN (silence is not a declaration)', () => {
    const row = herdrRow(NO_RECORD);
    expect(row.deliveryHandoff).toMatchObject({ kind: 'not_reported', command: null });
    const h = handoffSection(render(row, control(LINKED_START())));
    expect(h).toContain('data-return-path="not_known"');
    expect(text(h)).toContain(NOT_KNOWN_RETURN);
    expect(text(h)).toContain('Not known: this catalog did not report the facts needed to prepare a delivery record for this task, so none are shown and no command is prepared.');
    expect(text(h)).not.toContain(CANONICAL_RETURN);
    expect(h).not.toContain('data-testid="di-scan-delivery-handoff-command"');
  });

  it('SCENARIO an unrecognised or empty declaration fails closed: never the stronger reading', () => {
    for (const linkBasis of ['record_something_new', 'record_task_worktree_binding ', 'RECORD_TASK_WORKTREE_BINDING', null]) {
      const h = handoffSection(render(herdrRow(NO_RECORD, HANDOFF({ linkBasis })), control(LINKED_START())));
      expect(h, String(linkBasis)).toContain('data-return-path="not_known"');
      expect(text(h), String(linkBasis)).not.toContain(CANONICAL_RETURN);
    }
  });

  it('the decision itself, over every input: only the exact canonical declaration, with no origin-only evidence, earns the canonical statement', () => {
    const none = undefined;
    const unlinked = { kind: 'unlinked', state: 'unlinked_no_record', words: 'w' } as const;
    const origin = { kind: 'origin_only', deliveryId: ID, words: 'w' } as const;
    const cases: Array<[string, Parameters<typeof returnPathClaim>[0], Parameters<typeof returnPathClaim>[1], string]> = [
      ['declared canonical, no link row', none, HANDOFF(), 'canonical'],
      ['declared canonical, unlinked', unlinked, HANDOFF(), 'canonical'],
      ['declared canonical, linked origin-only', origin, HANDOFF(), 'legacy'],
      ['declared legacy', unlinked, HANDOFF({ linkBasis: 'record_repository_binding' }), 'legacy'],
      ['declared nothing', unlinked, HANDOFF({ linkBasis: null }), 'not_known'],
      ['declared unrecognised', unlinked, HANDOFF({ linkBasis: 'x' }), 'not_known'],
      ['no block', unlinked, none, 'not_known'],
      ['block not reported', unlinked, HANDOFF({ state: 'not_reported', linkBasis: null }), 'not_known'],
      ['block not applicable', unlinked, HANDOFF({ state: 'not_applicable', linkBasis: null }), 'not_known'],
      ['no block, origin-only link', origin, none, 'legacy'],
    ];
    for (const [name, link, block, expected] of cases) expect(returnPathClaim(link, block).kind, name).toBe(expected);
  });

  it('shellWord makes ONE safe shell word of any path: spaces and single quotes are never interpreted', () => {
    expect(shellWord('/a/b')).toBe("'/a/b'");
    expect(shellWord('/a b/c')).toBe("'/a b/c'");
    expect(shellWord("/it's")).toBe("'/it'\\''s'");
    expect(shellWord('/$(rm -rf x)`id`;&')).toBe("'/$(rm -rf x)`id`;&'");
  });

  it('a Mission that is not a Herdr task has no handoff section at all', () => {
    const row = rows({ state: 'unlinked_no_record', deliveryId: null, basis: null }).find((item) => item.id === HERD)!;
    expect(row.deliveryHandoff).toBeUndefined();
    expect(handoffSection(render(row, control(LINKED_START())))).toBe('');
  });
});

// ── THE "READY" GUARD, OVER THE WHOLE PANEL. A claim that a delivery or a record is ready would be just as false anywhere on the page as in the
// handoff section: the staged candidate cannot be determined here, and an empty list of blockers is not a promise. So the WHOLE rendered panel
// (the Mission details page and the list page, with the rail, header, swarm, delivery and notes) is read in every state this change adds, and
// it must carry no ready, mint, go-ahead or approved claim. The one legitimate "Ready" is a step's gate word when the delivery ADAPTER's own
// answer made a step eligible, which only a wired canonical link with a read status can show; none of the states below can.
describe('no state of the whole panel claims that a delivery or a record is ready', () => {
  const withoutCommand = (html: string) => html.replace(/<pre[^>]*data-testid="di-scan-delivery-handoff-command"[\s\S]*?<\/pre>/, '');
  const CASES: Array<[string, () => DiScanRow]> = [
    ['a fresh no-record task, canonical declaration', () => herdrRow(NO_RECORD, HANDOFF())],
    ['this mission\'s own shape: detached, evidence refused', () => herdrRow(NO_RECORD, HANDOFF({ branch: { state: 'detached', ref: null }, evidence: { state: 'refused', reason: "task t status is 'ACTIVE', not COMPLETE" }, blockers: ['head_detached', 'evidence_not_collected'] }))],
    ['nothing stops it (an empty blocker list)', () => herdrRow(NO_RECORD, HANDOFF({ blockers: [] }))],
    ['no readable delivery store', () => herdrRow({ state: 'unlinked_store_not_registered', deliveryId: null, basis: null }, HANDOFF())],
    ['an origin-only link from an older catalog', () => herdrRow({ state: 'linked', deliveryId: ID, basis: 'record_repository_binding' }, HANDOFF())],
    ['a legacy declaration', () => herdrRow(NO_RECORD, HANDOFF({ linkBasis: 'record_repository_binding' }))],
    ['an older catalog with no handoff block', () => herdrRow(NO_RECORD)],
    ['an unrecognised declaration', () => herdrRow(NO_RECORD, HANDOFF({ linkBasis: 'record_something_new' }))],
    ['an unrecognised link basis', () => herdrRow({ state: 'linked', deliveryId: ID, basis: 'record_canonical_worktree_binding' as unknown as Link['basis'] }, HANDOFF())],
    ['a workflow Mission, unlinked', () => rows({ state: 'unlinked_no_record', deliveryId: null, basis: null }).find((item) => item.id === HERD)!],
  ];
  const FORBIDDEN = /\bready\b|\bmint|\bgo ahead\b|\bapproved\b|may be created|can be created|will be created/i;

  for (const view of ['mission', 'list'] as const) {
    it.each(CASES)(`${view} view, %s: no ready, mint or go-ahead claim anywhere on the page, and no step is Ready`, (_name, make) => {
      const row = make();
      const html = renderToStaticMarkup(
        createElement(DiScanPanel, { rows: [row], initialSelectedId: row.id, initialView: view, onMinimize: noop, missionDelivery: control(LINKED_START()) }),
      );
      const all = text(withoutCommand(html));
      expect(all, 'the page must render something').not.toBe('');
      expect(all).not.toMatch(FORBIDDEN);
      expect(html).not.toContain('data-gate="ready"');
    });
  }

  it('the guard bites: a "ready" sentence anywhere on the page would be caught (it is not scoped to one section)', () => {
    expect('Everything is ready to go').toMatch(FORBIDDEN);
    expect('This delivery may be created now').toMatch(FORBIDDEN);
    expect('The record is minted').toMatch(FORBIDDEN);
    expect('Not a promise: nothing listed stops it').not.toMatch(FORBIDDEN);
  });

  it('the one legitimate "Ready" is the adapter\'s own eligible step on a wired canonical link, and the page says where it came from', () => {
    const row = herdrRow({ state: 'linked', deliveryId: ID, basis: 'record_task_worktree_binding' }, HANDOFF());
    const html = renderToStaticMarkup(
      createElement(DiScanPanel, {
        rows: [row],
        initialSelectedId: row.id,
        initialView: 'mission',
        onMinimize: noop,
        missionDelivery: control(readWith(reply(7, 'status', 'ok', null, null, statusAnswer()))),
      }),
    );
    expect(html).toContain('data-gate="ready"');
    expect(text(html)).toContain('Eligible now, as the record states it.');
    expect(handoffSection(html)).toBe(''); // and no creation section beside an acted-on record
  });
});

describe('the surfaces, established', () => {
  it('the Missions list surfaces no delivery: no id and no link words for any Mission', () => {
    const all = rows({ state: 'linked', deliveryId: ID, basis: 'record_mission_binding' });
    const list = render(
      all[0]!,
      control(readWith(reply(7, 'status', 'ok', null, null, statusAnswer()))),
      'missions',
      all,
    );
    expect(list).not.toContain(ID);
    expect(text(list)).not.toContain(LINKED_WORDS);
    expect(text(list)).not.toContain(WORDS.unlinked_model_not_recorded);
    expect(list).not.toContain('di-scan-delivery-read');
  });

  it("the specimen's Delivery is unchanged by a session: the same markup with or without one", () => {
    expect(DI_SCAN_LOCK.length).toBeGreaterThan(0);
    for (const row of DI_SCAN_LOCK) {
      expect(section(render(row, null)), row.id).not.toBe('');
      expect(
        section(
          render(row, control(readWith(reply(7, 'status', 'ok', null, null, statusAnswer())))),
        ),
        row.id,
      ).toBe(section(render(row, null)));
    }
  });
});
