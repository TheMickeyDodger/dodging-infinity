/* eslint-disable pixel-agents/no-inline-colors --
 * This file tests colour arithmetic (WCAG contrast, blending) and a CSS tokenizer: every colour
 * string in it is TEST DATA to compute or tokenize, never a style applied to anything. */
/**
 * Item 3 (original 20): the live code review's webview MODEL — everything the modal decides, as
 * pure functions, against the REAL answers the backend emitted
 * (`server/__tests__/fixtures/di-backend/v2/code-review-v1.FIXTURE.json`, hash-pinned by the server
 * suite). The reducer clears content when the connection, the office or the assignment cannot be
 * vouched for; the tokenizer never loses a character; control and bidirectional characters are
 * shown, never performed; the row window always covers what is visible.
 */
import { createHash } from 'node:crypto';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

import { describe, expect, it } from 'vitest';

import type {
  DiCodeReview,
  DiCodeReviewEntry,
  DiCodeReviewHunk,
  DiOfficeState,
} from '../../core/src/messages.ts';
import * as model from '../src/components/diCodeReviewModel.ts';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
/**
 * The webview's own fixture: the REAL backend answers as ONE real office relays them and the office
 * states it projects (`server/__tests__/diOfficeStateExport.test.ts` writes it from the backend fixture,
 * which the server suite hash-pins). The webview depends on `core/` only, so nothing here imports the server.
 */
const FIXTURE = path.join(root, 'test/fixtures/di-office-state-code-review-v2.FIXTURE.json');
/** The server's own source, read as TEXT (never imported) for the vocabularies the words must cover. */
const OFFICE_SOURCE = fs.readFileSync(path.join(root, '../server/src/di/codeReview.ts'), 'utf-8');

interface ReviewFixture {
  derived_from: { sha256: string };
  reference: { missionRef: string; memberRef: string; taskId: string; listing: string };
  states: Record<string, DiOfficeState>;
  replies: { overview: DiCodeReview; files: Record<string, DiCodeReview> };
}
const doc = JSON.parse(fs.readFileSync(FIXTURE, 'utf-8')) as ReviewFixture;
const copy = <T,>(value: T): T => JSON.parse(JSON.stringify(value)) as T;

/** The names an exported `as const` list in the office source holds. */
function officeList(name: string): string[] {
  const block = OFFICE_SOURCE.split(`export const ${name} = [`)[1]?.split('] as const;')[0];
  if (block === undefined) throw new Error(`no ${name} in the office source`);
  return [...block.matchAll(/'([a-z0-9_]+)'/g)].map((m) => m[1]);
}
const CODE_REVIEW_PROBLEMS = officeList('CODE_REVIEW_PROBLEMS');
const OFFICE_REVIEW_PROBLEMS = officeList('OFFICE_REVIEW_PROBLEMS');
const REVIEW_UNAVAILABLE_REASONS = officeList('REVIEW_UNAVAILABLE_REASONS');

/** The `diOfficeState` the office projects for the live review (a fresh copy each call). */
const officeStateOf = (): DiOfficeState => copy(doc.states.live);

const TARGET: model.ReviewTargetRef = (() => {
  const member = doc.states.live.missions.missions[0].members.members[0];
  return { ...doc.reference, agentName: member.agent };
})();

const overview = (): DiCodeReview => copy(doc.replies.overview);
const fileOf = (p: string): DiCodeReview => copy(doc.replies.files[p]);
const entries = (): DiCodeReviewEntry[] => overview().overview!.files;

describe('the webview bounds (exact values)', () => {
  it('are what the stylesheet and the component are built on', () => {
    expect(model.TEXT_SIZES).toEqual([11, 12, 13, 14, 16, 18, 20, 24]);
    expect(model.DEFAULT_TEXT_SIZE_INDEX).toBe(3);
    expect(model.ROW_HEIGHT_RATIO).toBe(1.55);
    expect(model.WINDOW_OVERSCAN_ROWS).toBe(24);
    expect(model.REVIEW_AGING_AFTER_SECONDS).toBe(60);
    expect(model.NARROW_VIEWPORT_PX).toBe(820);
    expect(model.SPLIT_MIN_VIEWPORT_PX).toBe(1040);
    expect(model.TAB_WIDTH).toBe(4);
    expect(model.TEXT_SIZES[model.DEFAULT_TEXT_SIZE_INDEX]).toBe(14);
  });
  it('the row height is a whole number of pixels at every size, and grows with it', () => {
    let last = 0;
    for (let i = 0; i < model.TEXT_SIZES.length; i += 1) {
      const px = model.rowPx(i);
      expect(Number.isInteger(px)).toBe(true);
      expect(px).toBeGreaterThan(last);
      expect(px).toBeGreaterThanOrEqual(model.TEXT_SIZES[i]);
      last = px;
    }
    expect(model.clampTextSize(-4)).toBe(0);
    expect(model.clampTextSize(99)).toBe(model.TEXT_SIZES.length - 1);
    expect(model.clampTextSize(2.9)).toBe(2);
  });
});

describe('the session reducer: a diff is only ever current while it can be vouched for', () => {
  const open = (): model.ReviewSession =>
    model.reduceSession(model.CLOSED_SESSION, { type: 'open', target: TARGET });
  const asked = (s: model.ReviewSession, request: model.ReviewRequest) =>
    model.reduceSession(s, { type: 'asked', request });
  const answered = (s: model.ReviewSession, answer: DiCodeReview, nowMs = 1000) =>
    model.reduceSession(s, { type: 'answer', answer, nowMs });

  it('opening starts with nothing and asks for the overview', () => {
    const s = open();
    expect(s.target).toEqual(TARGET);
    expect(s.overview).toBeNull();
    expect(model.nextRequest(s)).toEqual({ kind: 'overview' });
    expect(model.nextRequest(asked(s, { kind: 'overview' }))).toBeNull();
    expect(model.nextRequest(model.CLOSED_SESSION)).toBeNull();
  });

  it('applies an answer only to the request in flight for THIS target', () => {
    let s = asked(open(), { kind: 'overview' });
    // an answer for another listing, another view, or nothing in flight is ignored
    expect(answered(s, { ...overview(), listing: 'f'.repeat(64) })).toBe(s);
    expect(answered(s, { ...overview(), taskId: '20261014-000000-bbbbbb' })).toBe(s);
    expect(answered(s, fileOf('src/app.py'))).toBe(s);
    expect(answered(open(), overview())).toEqual(open());
    s = answered(s, overview(), 5000);
    expect(s.pending).toBeNull();
    expect(s.overview?.receivedAtMs).toBe(5000);
    expect(model.overviewFiles(s)).toHaveLength(23);
    // and a second, late copy of the same answer changes nothing
    expect(answered(s, overview())).toBe(s);
  });

  it('after the overview the selected file is asked for, with the snapshot it belongs to', () => {
    let s = answered(asked(open(), { kind: 'overview' }), overview());
    expect(model.nextRequest(s)).toBeNull();
    s = model.reduceSession(s, { type: 'select', path: 'src/app.py' });
    const request = model.nextRequest(s);
    expect(request).toMatchObject({ kind: 'file', path: 'src/app.py' });
    if (request?.kind !== 'file') return;
    expect(request.snapshot).toBe(overview().snapshot);
    expect(request.fileId).toBe(entries().find((e) => e.path === 'src/app.py')!.fileId);
    s = answered(asked(s, request), fileOf('src/app.py'));
    expect(s.file?.answer.file?.path).toBe('src/app.py');
    expect(model.nextRequest(s)).toBeNull();
  });

  it('files that carry no text are not asked for', () => {
    let s = answered(asked(open(), { kind: 'overview' }), overview());
    for (const p of ['bin/blob.bin', 'secrets/server.pem', '.env', 'generated/bundle.min.js', 'empty.txt']) {
      s = model.reduceSession(s, { type: 'select', path: p });
      expect(model.nextRequest(s), p).toBeNull();
    }
    s = model.reduceSession(s, { type: 'select', path: 'docs/gone.md' });
    expect(model.nextRequest(s)).toMatchObject({ kind: 'file' });
  });

  it('a file answer that is not the one in flight is ignored', () => {
    let s = answered(asked(open(), { kind: 'overview' }), overview());
    s = model.reduceSession(s, { type: 'select', path: 'src/app.py' });
    const request = model.nextRequest(s)!;
    s = asked(s, request);
    expect(answered(s, fileOf('src/util.py'))).toBe(s);
    expect(answered(s, { ...fileOf('src/app.py'), snapshot: 'a'.repeat(64) })).toBe(s);
  });

  it('a snapshot_moved refusal marks the overview stale but keeps it visible and labelled', () => {
    let s = answered(asked(open(), { kind: 'overview' }), overview());
    s = model.reduceSession(s, { type: 'select', path: 'src/app.py' });
    const request = model.nextRequest(s)!;
    const moved: DiCodeReview = {
      ...fileOf('src/app.py'),
      state: 'refused',
      decidedBy: 'backend',
      problem: 'snapshot_moved',
      detail: 'moved',
      target: null,
      comparison: null,
      overview: null,
      file: null,
    };
    s = answered(asked(s, request), moved);
    expect(s.stale).toBe(true);
    expect(s.overview).not.toBeNull();
    expect(s.refusal?.problem).toBe('snapshot_moved');
    expect(model.nextRequest(s)).toBeNull();
    // a fresh overview replaces everything and clears the stale mark
    s = answered(asked(s, { kind: 'overview' }), overview(), 9000);
    expect(s.stale).toBe(false);
    expect(s.refusal).toBeNull();
    expect(s.file).toBeNull();
  });

  it('another refusal does not erase what is still current', () => {
    let s = answered(asked(open(), { kind: 'overview' }), overview());
    s = model.reduceSession(s, { type: 'select', path: 'src/app.py' });
    const request = model.nextRequest(s)!;
    const refusal: DiCodeReview = {
      ...fileOf('src/app.py'),
      state: 'refused',
      decidedBy: 'backend',
      problem: 'git_read_deadline',
      detail: 'slow',
      target: null,
      comparison: null,
      overview: null,
      file: null,
    };
    s = answered(asked(s, request), refusal);
    expect(s.stale).toBe(false);
    expect(s.overview).not.toBeNull();
    expect(s.refusal?.problem).toBe('git_read_deadline');
  });

  it('an unusable first overview is shown as a refusal and is not asked for again by itself', () => {
    const refusal: DiCodeReview = {
      ...overview(),
      state: 'refused',
      decidedBy: 'office',
      problem: 'backend_unavailable',
      detail: 'down',
      target: null,
      comparison: null,
      overview: null,
      file: null,
      snapshot: null,
    };
    const s = answered(asked(open(), { kind: 'overview' }), refusal);
    expect(s.overview).toBeNull();
    expect(s.refusal?.problem).toBe('backend_unavailable');
    expect(model.nextRequest(s)).toBeNull();
  });

  it('an answer for a file the reader has already moved off is dropped, never shown under another file', () => {
    let s = answered(asked(open(), { kind: 'overview' }), overview());
    s = model.reduceSession(s, { type: 'select', path: 'src/app.py' });
    const first = model.nextRequest(s)!;
    s = asked(s, first);
    // the reader moves to another file while the first is being read
    s = model.reduceSession(s, { type: 'select', path: 'src/util.py' });
    expect(s.selectedPath).toBe('src/util.py');
    s = answered(s, fileOf('src/app.py'));
    expect(s.file).toBeNull();
    expect(s.pending).toBeNull();
    // the file now selected is asked for next, and only its answer is shown
    expect(model.nextRequest(s)).toMatchObject({ kind: 'file', path: 'src/util.py' });
    s = answered(asked(s, model.nextRequest(s)!), fileOf('src/util.py'));
    expect(s.file?.answer.file?.path).toBe('src/util.py');
  });

  it('a refusal that only says "too fast" or "busy" is retried by itself, once', () => {
    let s = answered(asked(open(), { kind: 'overview' }), overview());
    s = model.reduceSession(s, { type: 'select', path: 'src/app.py' });
    s = asked(s, model.nextRequest(s)!);
    const limited: DiCodeReview = {
      ...fileOf('src/app.py'),
      state: 'refused',
      decidedBy: 'office',
      problem: 'read_rate_limited',
      detail: 'slow down',
      target: null,
      comparison: null,
      overview: null,
      file: null,
    };
    s = answered(s, limited);
    expect(s.refusal?.problem).toBe('read_rate_limited');
    expect(model.nextRequest(s)).toBeNull();
    expect(model.isRetryable(s.refusal)).toBe(true);
    s = model.reduceSession(s, { type: 'retry' });
    expect(s.refusal).toBeNull();
    expect(model.nextRequest(s)).toMatchObject({ kind: 'file', path: 'src/app.py' });
    // a refusal that needs the cause to change is NOT retried by itself
    for (const problem of ['git_read_deadline', 'snapshot_moved', 'assignment_not_active', 'backend_unavailable']) {
      expect(model.isRetryable({ ...limited, problem } as DiCodeReview), problem).toBe(false);
    }
    expect(model.isRetryable({ ...limited, problem: 'read_in_progress' })).toBe(true);
    expect(model.isRetryable(null)).toBe(false);
    // and a retry with nothing to retry changes nothing
    expect(model.reduceSession(model.CLOSED_SESSION, { type: 'retry' })).toBe(model.CLOSED_SESSION);
  });

  it('a refresh keeps the old content, labelled, until the new overview arrives', () => {
    let s = answered(asked(open(), { kind: 'overview' }), overview(), 1000);
    s = model.reduceSession(s, { type: 'select', path: 'src/app.py' });
    s = answered(asked(s, model.nextRequest(s)!), fileOf('src/app.py'));
    s = model.reduceSession(s, { type: 'moved' });
    expect(s.stale).toBe(true);
    s = model.reduceSession(s, { type: 'refresh' });
    expect(s.refresh).toBe(true);
    expect(s.overview).not.toBeNull();
    expect(s.file).not.toBeNull();
    const request = model.nextRequest(s);
    expect(request).toEqual({ kind: 'overview' });
    s = asked(s, request!);
    expect(s.refresh).toBe(false);
    expect(model.nextRequest(s)).toBeNull();
    expect(s.overview).not.toBeNull();
    // while it is read the old overview is still what is on screen, and still marked stale
    expect(s.stale).toBe(true);
    s = answered(s, overview(), 2000);
    expect(s.overview?.receivedAtMs).toBe(2000);
    expect(s.stale).toBe(false);
    expect(s.file).toBeNull();
    expect(s.selectedPath).toBe('src/app.py');
    // the selected file is then asked for again, against the NEW snapshot
    expect(model.nextRequest(s)).toMatchObject({ kind: 'file', path: 'src/app.py' });
  });

  it('a failed refresh leaves the old overview on screen, stale, with the refusal beside it', () => {
    let s = answered(asked(open(), { kind: 'overview' }), overview());
    s = model.reduceSession(s, { type: 'refresh' });
    s = asked(s, model.nextRequest(s)!);
    const refusal: DiCodeReview = {
      ...overview(),
      state: 'refused',
      decidedBy: 'backend',
      problem: 'git_read_deadline',
      detail: 'slow',
      target: null,
      comparison: null,
      overview: null,
      file: null,
    };
    s = answered(s, refusal);
    expect(s.overview).not.toBeNull();
    expect(s.refusal?.problem).toBe('git_read_deadline');
    expect(model.nextRequest(s)).toBeNull();
  });

  it('a refresh after a clear, or with nothing open, does nothing', () => {
    const cleared = model.reduceSession(open(), { type: 'clear', reason: 'connection_lost' });
    expect(model.reduceSession(cleared, { type: 'refresh' })).toBe(cleared);
    expect(model.reduceSession(model.CLOSED_SESSION, { type: 'refresh' })).toBe(model.CLOSED_SESSION);
  });

  it('clearing drops EVERYTHING and says why — nothing stale stays labelled current', () => {
    let s = answered(asked(open(), { kind: 'overview' }), overview());
    s = model.reduceSession(s, { type: 'select', path: 'src/app.py' });
    s = answered(asked(s, model.nextRequest(s)!), fileOf('src/app.py'));
    expect(s.file).not.toBeNull();
    for (const reason of ['connection_lost', 'reconnected_awaiting_state', 'office_expired', 'office_not_live', 'assignment_changed', 'agent_not_live', 'agent_gone'] as const) {
      const cleared = model.reduceSession(s, { type: 'clear', reason });
      expect(cleared.overview, reason).toBeNull();
      expect(cleared.file, reason).toBeNull();
      expect(cleared.pending, reason).toBeNull();
      expect(cleared.refusal, reason).toBeNull();
      expect(cleared.cleared, reason).toBe(reason);
      expect(cleared.target, reason).toEqual(TARGET);
      expect(model.nextRequest(cleared), reason).toBeNull();
      expect(model.clearedWords(reason).title).toContain('cleared');
    }
    const closed = model.reduceSession(s, { type: 'close' });
    expect(closed).toEqual(model.CLOSED_SESSION);
    expect(model.reduceSession(closed, { type: 'clear', reason: 'connection_lost' })).toBe(closed);
  });

  it('says exactly WHY the review can no longer stand, from the link, the office and the agent', () => {
    const live = officeStateOf;
    const reasonFor = (clientReason: string | null, edit?: (s: DiOfficeState) => void) => {
      const state = live();
      edit?.(state);
      return model.clearedReasonFor(clientReason, state, TARGET);
    };
    const member = (s: DiOfficeState) => s.missions.missions[0].members.members[0];
    expect(reasonFor(null)).toBeNull();
    // the link, in the client's own words, wins over everything else
    expect(reasonFor('transport_disconnected')).toBe('connection_lost');
    expect(reasonFor('transport_connecting')).toBe('connection_lost');
    expect(reasonFor('transport_reconnected_awaiting_office_state')).toBe('reconnected_awaiting_state');
    expect(reasonFor('client_expired:no_office_state_before_valid_until')).toBe('office_expired');
    // then the office as a whole
    expect(model.clearedReasonFor(null, null, TARGET)).toBe('office_not_live');
    expect(reasonFor(null, (s) => (s.live = false))).toBe('office_not_live');
    // then this agent
    expect(reasonFor(null, (s) => (s.missions.missions[0].members.members = []))).toBe('agent_gone');
    expect(reasonFor(null, (s) => (member(s).codeReview!.listing = 'e'.repeat(64)))).toBe('assignment_changed');
    expect(reasonFor(null, (s) => (member(s).codeReview!.taskId = '20261014-000000-bbbbbb'))).toBe('assignment_changed');
    expect(
      reasonFor(null, (s) => (member(s).codeReview = { state: 'unavailable', reason: 'assignment_not_active', taskId: TARGET.taskId, listing: null })),
    ).toBe('assignment_changed');
    expect(
      reasonFor(null, (s) => (member(s).codeReview = { state: 'unavailable', reason: 'member_not_live', taskId: TARGET.taskId, listing: null })),
    ).toBe('agent_not_live');
    expect(reasonFor(null, (s) => delete member(s).codeReview)).toBe('assignment_changed');
    // each cause has its own title (nothing is worded as "connection lost" that is not)
    const titles = new Set(
      (['connection_lost', 'reconnected_awaiting_state', 'office_expired', 'office_not_live', 'assignment_changed', 'agent_not_live', 'agent_gone'] as const).map(
        (r) => model.clearedWords(r).title,
      ),
    );
    expect(titles.size).toBe(7);
    expect(model.clearedWords('office_expired').title).not.toContain('Connection');
  });

  it('an answer that arrives for a request made BEFORE a clear is never shown', () => {
    let s = asked(open(), { kind: 'overview' });
    s = model.reduceSession(s, { type: 'clear', reason: 'connection_lost' });
    // pending was dropped with the clear: the late answer answers nothing
    expect(answered(s, overview())).toBe(s);
  });

  it('the offer holds only for the same task and listing token, and only while offered', () => {
    const offered = { state: 'offered' as const, reason: null, taskId: TARGET.taskId, listing: TARGET.listing };
    expect(model.offerStillHolds(TARGET, offered)).toBe(true);
    expect(model.offerStillHolds(TARGET, { ...offered, listing: 'e'.repeat(64) })).toBe(false);
    expect(model.offerStillHolds(TARGET, { ...offered, taskId: '20261014-000000-bbbbbb' })).toBe(false);
    expect(model.offerStillHolds(TARGET, { ...offered, state: 'unavailable', reason: 'member_not_live', listing: null })).toBe(false);
    expect(model.offerStillHolds(TARGET, null)).toBe(false);
    expect(model.offerStillHolds(TARGET, undefined)).toBe(false);
  });
});

describe('the file tree and its search', () => {
  it('folders first, then files, by name; single-child folders are one line', () => {
    const tree = model.buildTree(entries());
    const names = tree.map((n) => `${n.kind}:${n.name}`);
    expect(names.slice(0, 8)).toEqual([
      'folder:.herd',
      'folder:bin',
      'folder:data',
      'folder:docs',
      'folder:generated',
      'folder:legacy',
      'folder:notes',
      'folder:secrets',
    ]);
    const files = tree.filter((n) => n.kind === 'file').map((n) => n.name);
    expect(files).toEqual(['.env', 'crlf.txt', 'empty.txt']);
    // .herd holds a file AND a folder, so it is not a single-child chain and stays one level
    const herd = tree.find((n) => n.name === '.herd')!;
    expect(herd.kind === 'folder' && herd.children.map((c) => `${c.kind}:${c.name}`)).toEqual([
      'folder:state',
      'file:state.json',
    ]);
    // a chain of single-child folders is one line and keeps the FULL path of its last link
    const chain = model.buildTree([
      { ...entries()[0], path: 'a/b/c/only.txt', oldPath: null, change: 'modified' },
    ]);
    expect(chain).toHaveLength(1);
    expect(chain[0].name).toBe('a/b/c');
    expect(chain[0].kind === 'folder' && chain[0].path).toBe('a/b/c');
  });

  it('folder totals are the sums of the files beneath', () => {
    const tree = model.buildTree(entries());
    const src = tree.find((n) => n.name === 'src')!;
    expect(src.kind).toBe('folder');
    if (src.kind !== 'folder') return;
    expect(src.files).toBe(4);
    expect(src.additions).toBe(1 + 1 + 10 + 2);
    expect(src.deletions).toBe(1 + 0 + 3 + 1);
  });

  it('every listed file appears exactly once in the tree', () => {
    const tree = model.buildTree(entries());
    const rows = model.visibleRows(tree, new Set(), false);
    const leaves = rows.filter((r) => r.node.kind === 'file').map((r) => r.node.path);
    expect(leaves.sort()).toEqual(entries().map((e) => e.path).sort());
  });

  it('search matches path words, case-insensitively, and the former path of a rename', () => {
    const all = entries();
    expect(model.filterFiles(all, '').length).toBe(all.length);
    expect(model.filterFiles(all, 'APP').map((f) => f.path)).toEqual(['src/app.py']);
    expect(model.filterFiles(all, 'src util').map((f) => f.path)).toEqual(['src/util.py']);
    expect(model.filterFiles(all, 'mover').map((f) => f.path)).toEqual(['src/moved.py']);
    expect(model.filterFiles(all, 'no-such-file')).toEqual([]);
    expect(model.filterFiles(all, '   ').length).toBe(all.length);
  });

  it('a search opens every folder; otherwise the reader\u2019s collapses are kept', () => {
    const tree = model.buildTree(entries());
    const collapsed = new Set(['src']);
    const rows = model.visibleRows(tree, collapsed, false);
    expect(rows.find((r) => r.node.path === 'src')?.expanded).toBe(false);
    expect(rows.some((r) => r.node.path === 'src/app.py')).toBe(false);
    const searching = model.visibleRows(tree, collapsed, true);
    expect(searching.some((r) => r.node.path === 'src/app.py')).toBe(true);
  });

  it('[ and ] move to the neighbouring file in tree order and stop at the ends', () => {
    const rows = model.visibleRows(model.buildTree(entries()), new Set(), false);
    const order = rows.filter((r) => r.node.kind === 'file').map((r) => r.node.path);
    expect(model.neighbourPath(rows, null, 1)).toBe(order[0]);
    expect(model.neighbourPath(rows, null, -1)).toBe(order[order.length - 1]);
    expect(model.neighbourPath(rows, order[3], 1)).toBe(order[4]);
    expect(model.neighbourPath(rows, order[3], -1)).toBe(order[2]);
    expect(model.neighbourPath(rows, order[0], -1)).toBe(order[0]);
    expect(model.neighbourPath(rows, order[order.length - 1], 1)).toBe(order[order.length - 1]);
    expect(model.neighbourPath([], 'x', 1)).toBeNull();
  });
});

describe('plain words: every code the system can say has some', () => {
  it('every refusal the backend and the office can give has words of its own', () => {
    for (const problem of [...CODE_REVIEW_PROBLEMS, ...OFFICE_REVIEW_PROBLEMS]) {
      expect(model.PROBLEM_WORD_KEYS, problem).toContain(problem);
    }
    // and nothing is worded that no side can say
    const known = new Set<string>([...CODE_REVIEW_PROBLEMS, ...OFFICE_REVIEW_PROBLEMS]);
    for (const key of model.PROBLEM_WORD_KEYS) expect(known.has(key), key).toBe(true);
  });
  it('every reason a review is not offered has words; an unknown one has the default', () => {
    for (const reason of REVIEW_UNAVAILABLE_REASONS) {
      expect(model.UNAVAILABLE_WORDS[reason], reason).toBeTruthy();
    }
    expect(Object.keys(model.UNAVAILABLE_WORDS).sort()).toEqual([...REVIEW_UNAVAILABLE_REASONS].sort());
    expect(model.unavailableWords(null)).toContain('not offered');
  });
  it('an unknown refusal code still shows the detail it was given', () => {
    expect(model.problemWords('brand_new_code', 'because').body).toBe('because');
    expect(model.problemWords(null, null).title).toContain('could not be read');
  });
  it('every file state and change has a glyph or a badge and says what it is', () => {
    for (const change of ['added', 'deleted', 'modified', 'renamed', 'untracked', 'type_changed', 'conflicted']) {
      const g = model.changeGlyph(change);
      expect(g.letter.length).toBe(1);
      expect(g.label).not.toBe(change === 'untracked' ? '' : 'x');
    }
    for (const state of ['empty', 'binary', 'not_utf8', 'oversized', 'withheld', 'unsupported', 'no_content_diff', 'conflicted']) {
      const w = model.stateWords(state, state === 'withheld' ? 'private_location' : null);
      expect(w.badge).not.toBe('');
      expect(w.body.length).toBeGreaterThan(20);
    }
    expect(model.stateWords('withheld', 'credential_like_name').body).toContain('never read');
    // a withheld TRACKED file says how its change is known, and where that is blind; a new (untracked) one has no such record
    for (const change of ['modified', 'added', 'deleted', 'renamed']) {
      const body = model.stateWords('withheld', 'credential_like_name', change).body;
      expect(body, change).toContain(model.WITHHELD_CHANGE_WORDS);
      expect(body, change).toContain('an edit of the same size within that second may not be seen');
      expect(body, change).toContain('never read or shown');
    }
    expect(model.stateWords('withheld', 'private_location', 'untracked').body).not.toContain('same size');
    expect(model.stateWords('withheld', 'private_location').body).not.toContain('same size');
    expect(model.stateWords('binary', null, 'modified').body).not.toContain('same size');
    expect(model.stateWords('text', null).badge).toBe('');
  });
  it('the comparison is stated in sentences, with the base commit and what is not covered', () => {
    const o = overview();
    const lines = model.comparisonSentences(o.target!, o.comparison!);
    expect(lines.join(' ')).toContain('branch main at commit');
    expect(lines.join(' ')).toContain(o.comparison!.baseOid.slice(0, 10));
    expect(lines.join(' ')).toContain('Not covered');
    expect(lines.join(' ')).toContain('ignored files');
    expect(lines.join(' ')).toContain('recorded for it (its mode, its size and the second it was recorded)');
    expect(lines.join(' ')).toContain('an edit of the same size within that second may not be seen');
    const detached = model.comparisonSentences({ ...o.target!, detached: true, branch: null }, o.comparison!);
    expect(detached[0]).toContain('a detached HEAD at commit');
    const filtered = model.comparisonSentences(o.target!, { ...o.comparison!, filtersNotRun: ['lfs'] });
    expect(filtered.join(' ')).toContain('content filters (lfs)');
    expect(model.ATTRIBUTION_WORDS).toContain('Nothing here says who made a change');
  });
  it('an empty listing says what it found and what it cannot see, never that the tree matches', () => {
    const words = model.emptyListingWords('20a99955e6aa', '03:00:00 (just now)');
    const text = `${words.title} ${words.body}`;
    expect(text).toContain('20a99955e6');
    expect(text).toContain('03:00:00 (just now)');
    expect(text).toContain('as far as this view can tell');
    expect(text).toContain('not proof the working tree matches');
    expect(text).toContain('an edit of the same size within that second may not be seen');
    expect(text).not.toContain('The working tree matches commit');
    expect(words.tone).toBe('info');
  });
  it('totals, ages and clocks', () => {
    expect(model.totalsWords(overview().overview!.totals)).toBe(
      '23 files changed \u00b7 +7025 \u00b7 \u2212110 \u00b7 6 withheld \u00b7 5 not shown as text',
    );
    expect(model.totalsWords({ files: 1, additions: 0, deletions: 0, withheld: 0, unshown: 0 })).toBe(
      '1 file changed \u00b7 +0 \u00b7 \u22120',
    );
    // ROUND 22: an empty listing is a finding, so even the header count does not say "0 files changed" as if it were a verdict
    expect(model.totalsWords({ files: 0, additions: 0, deletions: 0, withheld: 0, unshown: 0 })).toBe(
      'no changes found \u00b7 +0 \u00b7 \u22120',
    );
    const now = 1_800_000_000_000;
    expect(model.ageWords(now, 1_800_000_000 - 2)).toBe('just now');
    expect(model.ageWords(now, 1_800_000_000 - 30)).toBe('30 seconds ago');
    expect(model.ageWords(now, 1_800_000_000 - 60)).toBe('1 minute ago');
    expect(model.ageWords(now, 1_800_000_000 - 600)).toBe('10 minutes ago');
    expect(model.ageWords(now, 1_800_000_000 - 7200)).toBe('2 hours ago');
    expect(model.ageWords(now, null)).toBe('time not recorded');
    expect(model.ageWords(now, 1_800_000_000 + 99)).toBe('just now');
    expect(model.isAging(now, 1_800_000_000 - 59)).toBe(false);
    expect(model.isAging(now, 1_800_000_000 - 60)).toBe(true);
    expect(model.isAging(now, null)).toBe(false);
    expect(model.clockWords(null)).toBe('not recorded');
    expect(model.clockWords(1_800_000_000)).toMatch(/^\d\d:\d\d:\d\d$/);
  });
  it('truncation says the exact numbers; nothing is said when nothing was left out', () => {
    const big = fileOf('data/large.txt').file!;
    const words = model.truncationWords(big)!;
    expect(words).toContain('6,000 of 7,100');
    expect(words).toContain('whole diff');
    expect(model.truncationWords(fileOf('src/app.py').file!)).toBeNull();
    expect(model.clippedWords(3819)).toBe('3,819 more characters not shown');
    expect(model.clippedWords(1)).toBe('1 more character not shown');
  });
});

describe('side by side: a changed line sits opposite its replacement', () => {
  const hunk = (lines: DiCodeReviewHunk['lines']): DiCodeReviewHunk => ({
    oldStart: 1, oldLines: 9, newStart: 1, newLines: 9, section: null, lines,
  });
  const L = (kind: string, oldNo: number | null, newNo: number | null, text: string) => ({
    kind, oldNo, newNo, text, clipped: 0,
  });
  it('pairs deletions with the additions that follow, in order, and blanks the shorter side', () => {
    const rows = model.splitRows(
      [
        hunk([
          L('context', 1, 1, 'a'),
          L('del', 2, null, 'old1'),
          L('del', 3, null, 'old2'),
          L('del', 4, null, 'old3'),
          L('add', null, 2, 'new1'),
          L('add', null, 3, 'new2'),
          L('context', 5, 4, 'z'),
          L('add', null, 5, 'tail'),
        ]),
      ],
      new Set(),
    );
    const pairs = rows.filter((r) => r.kind === 'pair') as Extract<model.SplitRow, { kind: 'pair' }>[];
    expect(pairs.map((p) => [p.left?.text ?? null, p.right?.text ?? null])).toEqual([
      ['a', 'a'],
      ['old1', 'new1'],
      ['old2', 'new2'],
      ['old3', null],
      ['z', 'z'],
      [null, 'tail'],
    ]);
    expect(pairs[1].left).toMatchObject({ no: 2, kind: 'del' });
    expect(pairs[1].right).toMatchObject({ no: 2, kind: 'add' });
    expect(pairs[0].left?.kind).toBe('context');
  });
  it('a lone addition or deletion has a blank opposite side; a no-newline marker is a note', () => {
    const rows = model.splitRows(
      [hunk([L('add', null, 1, 'x'), L('no_newline', null, null, ' No newline at end of file')])],
      new Set(),
    );
    expect(rows[1]).toMatchObject({ kind: 'pair', left: null });
    expect(rows[2]).toMatchObject({ kind: 'note', text: '\\ No newline at end of file' });
  });
  it('a collapsed hunk is its header alone, in both views', () => {
    const hunks = fileOf('src/syntax_sample.ts').file!.hunks;
    expect(hunks.length).toBeGreaterThan(1);
    const collapsed = new Set([0]);
    const unified = model.unifiedRows(hunks, collapsed);
    const split = model.splitRows(hunks, collapsed);
    expect(unified[0]).toMatchObject({ kind: 'hunk', collapsed: true });
    expect(unified[1]).toMatchObject({ kind: 'hunk', hunkIndex: 1 });
    expect(split[0]).toMatchObject({ kind: 'hunk', collapsed: true });
    expect(split[1]).toMatchObject({ kind: 'hunk', hunkIndex: 1 });
  });
  it('over every real file, the split view shows every changed line exactly once', () => {
    for (const p of Object.keys(doc.replies.files)) {
      const file = fileOf(p).file!;
      const unifiedLines = model.unifiedRows(file.hunks, new Set()).filter((r) => r.kind === 'line');
      const rows = model.splitRows(file.hunks, new Set());
      const dels = unifiedLines.filter((r) => r.kind === 'line' && r.line.kind === 'del').length;
      const adds = unifiedLines.filter((r) => r.kind === 'line' && r.line.kind === 'add').length;
      const ctx = unifiedLines.filter((r) => r.kind === 'line' && r.line.kind === 'context').length;
      const pairs = rows.filter((r) => r.kind === 'pair') as Extract<model.SplitRow, { kind: 'pair' }>[];
      expect(pairs.filter((r) => r.left?.kind === 'del').length, p).toBe(dels);
      expect(pairs.filter((r) => r.right?.kind === 'add').length, p).toBe(adds);
      expect(pairs.filter((r) => r.left?.kind === 'context').length, p).toBe(ctx);
      expect(pairs.length, p).toBeGreaterThanOrEqual(Math.max(dels, adds) + ctx > 0 ? ctx : 0);
    }
  });
  it('gutter width and the longest line cover every retained line', () => {
    const file = fileOf('data/large.txt').file!;
    expect(model.gutterDigits(file.hunks)).toBeGreaterThanOrEqual(4);
    expect(model.longestLine(fileOf('web/long_line.js').file!.hunks)).toBe(1200);
    expect(model.longestLine([])).toBe(0);
  });
});

describe('row windowing: what is visible is always rendered, and never more than the bound', () => {
  it('covers the viewport at every scroll position', () => {
    for (const total of [0, 1, 5, 23, 24, 25, 1000, 6100]) {
      for (const px of [14, 22, 37]) {
        for (const viewport of [0, 100, 600, 1300]) {
          for (let scroll = -50; scroll < total * px + 100; scroll += Math.max(1, Math.floor((total * px) / 17) + 7)) {
            const w = model.windowRows(total, px, scroll, viewport);
            expect(w.first).toBeGreaterThanOrEqual(0);
            expect(w.last).toBeLessThanOrEqual(total);
            expect(w.first).toBeLessThanOrEqual(w.last);
            expect(w.offsetPx).toBe(w.first * px);
            expect(w.totalPx).toBe(total * px);
            if (total > 0) {
              const top = Math.min(Math.max(0, scroll), Math.max(0, total * px - 1));
              const firstVisible = Math.floor(top / px);
              const lastVisible = Math.min(total - 1, Math.floor((top + Math.max(viewport, 1) - 1) / px));
              expect(w.first, `${total}/${px}/${viewport}/${scroll}`).toBeLessThanOrEqual(firstVisible);
              expect(w.last, `${total}/${px}/${viewport}/${scroll}`).toBeGreaterThan(lastVisible);
            }
            // never a window larger than the viewport plus both overscans
            expect(w.last - w.first).toBeLessThanOrEqual(Math.ceil(viewport / px) + 2 * model.WINDOW_OVERSCAN_ROWS + 1);
          }
        }
      }
    }
  });
  it('an empty or degenerate list renders nothing', () => {
    expect(model.windowRows(0, 20, 0, 500)).toEqual({ first: 0, last: 0, offsetPx: 0, totalPx: 0 });
    expect(model.windowRows(10, 0, 0, 500)).toEqual({ first: 0, last: 0, offsetPx: 0, totalPx: 0 });
  });
});

describe('control and bidirectional characters are shown, never performed', () => {
  const join = (text: string): string => model.visibleSegments(text).map((s) => s.text).join('');
  it('a bidirectional override is a visible placeholder with its code point and a name', () => {
    const segments = model.visibleSegments('if (isAdmin\u202e) { }');
    expect(segments.map((s) => s.control)).toEqual([false, true, false]);
    expect(segments[1].text).toBe('\u27e6U+202E\u27e7');
    expect(segments[1].title).toBe('right-to-left override (U+202E)');
    expect(join('a\u2066b\u2069c')).toBe('a\u27e6U+2066\u27e7b\u27e6U+2069\u27e7c');
  });
  it('zero-width and control characters are visible; ordinary text is untouched', () => {
    expect(join('x\u200by')).toBe('x\u27e6U+200B\u27e7y');
    expect(join('nul\u0000x')).toBe('nul\u27e6U+0000\u27e7x');
    expect(join('del\u007fx')).toBe('del\u27e6U+007F\u27e7x');
    expect(join('\u0085')).toBe('\u27e6U+0085\u27e7');
    expect(join('plain \u00e9 \u4e2d \ud83d\ude00')).toBe('plain \u00e9 \u4e2d \ud83d\ude00');
    expect(model.hasHiddenCharacters('a\u202eb')).toBe(true);
    expect(model.hasHiddenCharacters('a\tb')).toBe(false);
    expect(model.hasHiddenCharacters('')).toBe(false);
  });
  it('a tab is spaces to the next stop, and the column counts placeholders as one', () => {
    expect(join('\tx')).toBe('    x');
    expect(join('ab\tx')).toBe('ab  x');
    expect(join('abcd\tx')).toBe('abcd    x');
    expect(join('\u200b\tx')).toBe('\u27e6U+200B\u27e7' + '   x');
  });
  it('a placeholder is short and replaces exactly one character', () => {
    for (const ch of ['\u0001', '\u202e', '\u200b', '\u007f', '\u2028']) {
      const [segment] = model.visibleSegments(ch);
      expect(segment.control).toBe(true);
      expect(segment.text.length).toBeLessThanOrEqual(model.MAX_PLACEHOLDER_CHARS);
    }
  });
  it('nothing is dropped: over every real line, only the invisible characters are replaced', () => {
    // eslint-disable-next-line no-control-regex -- the point of the pattern is the control characters
    const invisible = /[\u0000-\u0008\u000a-\u001f\u007f-\u009f\u00ad\u200b-\u200f\u2028-\u202e\u2060\u2066-\u2069\ufeff]/gu;
    let lines = 0;
    let controls = 0;
    for (const p of Object.keys(doc.replies.files)) {
      for (const hunk of fileOf(p).file!.hunks) {
        for (const line of hunk.lines) {
          const segments = model.visibleSegments(line.text);
          const shown = segments.filter((s) => !s.control).map((s) => s.text).join('');
          // the same printable characters, in the same order (tabs and their spaces aside)
          expect(shown.replace(/ /g, ''), p).toBe(line.text.replace(invisible, '').replace(/[ \t]/g, ''));
          // and one placeholder per invisible character
          const hidden = (line.text.match(invisible) ?? []).length;
          expect(segments.filter((s) => s.control).length, p).toBe(hidden);
          lines += 1;
          controls += hidden;
        }
      }
    }
    expect(lines).toBeGreaterThan(6000);
    // the real fixture holds a carriage-return file: its CRs are shown, not lost
    expect(controls).toBeGreaterThan(0);
  });
});

describe('the syntax tokenizer: a reading aid that never alters the text', () => {
  const languages: Record<string, string> = {
    'x.ts': 'ts', 'x.tsx': 'ts', 'x.js': 'ts', 'x.py': 'py', 'x.sh': 'sh', 'Dockerfile': 'sh',
    'x.rs': 'rs', 'x.go': 'go', 'x.cpp': 'c', 'x.java': 'java', 'x.sql': 'sql', 'x.json': 'json',
    'x.yaml': 'yaml', 'x.yml': 'yaml', 'x.css': 'css', 'x.html': 'html', 'x.md': 'md', 'x.toml': 'toml',
    'a/b/.github/workflows/ci.yml': 'yaml',
  };
  it('picks a language by extension or well-known name, and none for the rest', () => {
    for (const [file, key] of Object.entries(languages)) expect(model.languageFor(file), file).toBe(key);
    for (const file of ['README', 'x.txt', 'x.unknown', 'noextension', '.env']) {
      expect(model.languageFor(file), file).toBeNull();
    }
    expect(model.languageName(null)).toBe('plain text');
    expect(model.languageName('ts')).toBe('TypeScript');
  });
  const join = (r: { tokens: model.Token[] }): string => r.tokens.map((t) => t.text).join('');
  it('keeps every character in order, for every language, over adversarial lines', () => {
    const lines = [
      '', ' ', '\t\t', 'const a = `x ${y} \\` z`;', '"unterminated', "'a\\'b'", '/* open', 'close */ x', '// c', '# c',
      'a # not a comment', 'x = 1_000.5e-3', '0xFF + 0b1 + .5', '<div class="a" id=b>text</div>', '</p>', '<br/>', '<',
      '{"k": [1, 2, {"z": null}], "s": "v"}', 'key: value # c', '.cls { color: #fff; --x: 1px; }', '@media (max-width: 1px)',
      '\u202e', 'a\u0000b', '\ud83d\ude00 emoji', 'x'.repeat(5000), '"' + 'y'.repeat(5000), '((((((((((', '\\', '`', '$', 'é = 1',
      '#### heading', '- item', '```ts', '1. one',
    ];
    for (const key of Object.keys(languages).map((f) => languages[f]).concat([null as unknown as string])) {
      let state = false;
      for (const line of lines) {
        const result = model.tokenizeLine(line, key, state);
        expect(join(result), `${key}: ${JSON.stringify(line.slice(0, 40))}`).toBe(line);
        state = result.inBlock;
      }
    }
  });
  it('never splits a character outside the BMP (two UTF-16 units) across tokens, segments or columns', () => {
    // GUARD (round 23, passes on the earlier bytes too): the dialog applies neither character bound and handles
    // every character whole, so what the backend now cuts at a character is also drawn at one
    const astral = '\u{1F600}';
    const wellFormed = (t: string): boolean => !/[\uD800-\uDBFF](?![\uDC00-\uDFFF])|(?<![\uD800-\uDBFF])[\uDC00-\uDFFF]/.test(t);
    const lines = [
      `const s = "${astral}${astral}"; // ${astral}`, `x = ${astral} + 1`, `${astral}${astral}${astral}`,
      `# ${astral}\t${astral}`, `<b>${astral}</b>`, `.${astral} { content: "${astral}" }`, `/* ${astral} */ ${astral}`,
      `a${astral}b${astral}`, `{"${astral}": "${astral}"}`,
    ];
    for (const key of [...new Set(Object.values(languages)), null as unknown as string]) {
      for (const line of lines) {
        const result = model.tokenizeLine(line, key);
        expect(join(result), `${key}: ${line}`).toBe(line);
        for (const token of result.tokens) expect(wellFormed(token.text), `${key}: ${line}`).toBe(true);
      }
    }
    for (const line of lines) {
      for (const segment of model.visibleSegments(line)) expect(wellFormed(segment.text), line).toBe(true);
      expect(wellFormed(model.expandTabs(line)), line).toBe(true);
    }
  });
  it('keeps every real line of every real file exactly, and finishes in time', () => {
    const started = Date.now();
    let count = 0;
    for (const p of Object.keys(doc.replies.files)) {
      const key = model.languageFor(p);
      for (const hunk of fileOf(p).file!.hunks) {
        let inBlock = false;
        for (const line of hunk.lines) {
          const result = model.tokenizeLine(line.text, key, inBlock);
          expect(join(result), p).toBe(line.text);
          inBlock = result.inBlock;
          count += 1;
        }
      }
    }
    expect(count).toBeGreaterThan(6000);
    expect(Date.now() - started).toBeLessThan(5000);
  });
  it('highlights what a reader expects in TypeScript, Python, JSON and CSS', () => {
    const kinds = (text: string, key: string): Array<[string, string]> =>
      model.tokenizeLine(text, key).tokens.filter((t) => t.kind !== 'plain').map((t) => [t.text, t.kind]);
    expect(kinds('export const DEFAULT_RETRIES = 5; // raised', 'ts')).toEqual([
      ['export', 'keyword'], ['const', 'keyword'], ['=', 'punct'], ['5', 'number'], [';', 'punct'], ['// raised', 'comment'],
    ]);
    expect(kinds("throw new RangeError(`bad ${x}`);", 'ts')).toEqual([
      ['throw', 'keyword'], ['new', 'keyword'], ['RangeError', 'type'], ['(', 'punct'], ['`bad ${x}`', 'string'], [')', 'punct'], [';', 'punct'],
    ]);
    expect(kinds('def load(path: str) -> int:  # note', 'py')[0]).toEqual(['def', 'keyword']);
    expect(kinds('def load(path: str) -> int:  # note', 'py').pop()).toEqual(['# note', 'comment']);
    expect(kinds('{"retries": 3, "ok": true}', 'json')).toEqual([
      ['{', 'punct'], ['"retries"', 'property'], [':', 'punct'], ['3', 'number'], [',', 'punct'],
      ['"ok"', 'property'], [':', 'punct'], ['true', 'keyword'], ['}', 'punct'],
    ]);
    expect(kinds('.panel { color: #101010; }', 'css').slice(0, 3)).toEqual([
      ['.panel', 'type'], ['{', 'punct'], ['color', 'property'],
    ]);
  });
  it('a block comment carries across lines, and a hunk starts outside one', () => {
    const first = model.tokenizeLine('/* start', 'ts', false);
    expect(first.inBlock).toBe(true);
    const middle = model.tokenizeLine('still inside', 'ts', first.inBlock);
    expect(middle.tokens).toEqual([{ text: 'still inside', kind: 'comment' }]);
    const last = model.tokenizeLine('end */ const x', 'ts', middle.inBlock);
    expect(last.inBlock).toBe(false);
    expect(last.tokens[0]).toEqual({ text: 'end */', kind: 'comment' });
    expect(model.tokenizeLine('const x', 'ts').inBlock).toBe(false);
  });
  it('a language it does not know is one plain token', () => {
    expect(model.tokenizeLine('anything at all', null).tokens).toEqual([
      { text: 'anything at all', kind: 'plain' },
    ]);
    expect(model.tokenizeLine('', 'ts').tokens).toEqual([{ text: '', kind: 'plain' }]);
  });
});

describe('contrast arithmetic', () => {
  it('matches the WCAG reference values', () => {
    expect(model.contrastRatio('#000000', '#ffffff')).toBeCloseTo(21, 5);
    expect(model.contrastRatio('#ffffff', '#ffffff')).toBeCloseTo(1, 5);
    expect(model.contrastRatio('#777777', '#ffffff')).toBeCloseTo(4.48, 2);
    expect(model.contrastRatio('#767676', '#ffffff')).toBeGreaterThan(4.5);
    expect(model.contrastRatio('#ffffff', '#000000')).toBe(model.contrastRatio('#000000', '#ffffff'));
    expect(model.blend('#ff0000', 0.5, '#000000')).toBe('#800000');
    expect(model.blend('#ffffff', 0, '#123456')).toBe('#123456');
    expect(() => model.luminance('red')).toThrow();
  });
});

describe('side by side: what each pane needs and says', () => {
  const side = (text: string, kind: 'context' | 'add' | 'del' = 'context', clipped = 0) => ({ no: 1, text, clipped, kind });
  const pair = (left: ReturnType<typeof side> | null, right: ReturnType<typeof side> | null) =>
    ({ kind: 'pair', hunkIndex: 0, left, right }) as const;

  it('each side is sized from ITS OWN longest line, tabs expanded, a clipped line with its tag', () => {
    const rows = [
      pair(side('short'), side('x'.repeat(300), 'add')),
      pair(side('y'.repeat(120), 'del'), null),
      pair(null, side('\tindented', 'add')),
      { kind: 'note', hunkIndex: 0, text: '\\ No newline at end of file' } as const,
      { kind: 'hunk', hunkIndex: 0, collapsed: false, hunk: {} as never } as const,
    ];
    expect(model.splitColumns(rows)).toEqual({ old: 120, new: 300 });
    expect(model.splitColumns([])).toEqual({ old: 0, new: 0 });
    // a tab is spaces to the next tab stop, so it is as wide as it will be drawn
    expect(model.splitColumns([pair(null, side('\tx', 'add'))]).new).toBe(model.expandTabs('\tx').length);
    expect(model.expandTabs('\tx').length).toBeGreaterThan(2);
    // a clipped line also needs room for the "N more characters not shown" tag
    expect(model.splitColumns([pair(side('z'.repeat(50), 'del', 9), null)]).old).toBe(50 + model.CLIP_TAG_COLUMNS);
    // and a side that has nothing is 0, whatever the other holds
    expect(model.splitColumns([pair(null, side('only new', 'add'))]).old).toBe(0);
  });

  it('a pane is at least as wide as its text, its line numbers and its chrome', () => {
    expect(model.paneMinWidthCh(300, 4)).toBe(300 + 4 + model.PANE_CHROME_COLUMNS);
    expect(model.paneMinWidthCh(0, 1)).toBe(1 + model.PANE_CHROME_COLUMNS);
  });

  it('the headings name the base commit and the working tree, never old/new alone', () => {
    const heads = model.splitPaneHeads('0123456789abcdef0123456789abcdef01234567', 1_800_000_000);
    expect(heads.old).toEqual({ title: 'Base (old)', detail: 'commit 0123456789' });
    expect(heads.new.title).toBe('Working tree (new)');
    expect(heads.new.detail).toMatch(/^as observed \d\d:\d\d:\d\d$/);
    expect(model.splitPaneHeads('a'.repeat(40), null).new.detail).toBe('as observed');
    expect(model.paneLabel('old', 'src/a.ts')).toBe('Base side (old) of src/a.ts, side by side');
    expect(model.paneLabel('new', 'src/a.ts')).toBe('Working tree side (new) of src/a.ts, side by side');
  });
});

describe('why a window shows one column is said in words', () => {
  it('a dialog narrower than two columns says what it needs and what to do', () => {
    const words = model.splitUnavailableWords(false);
    expect(words).toBe('Side by side needs a dialog at least 1,040 px wide: widen the window or use full screen.');
    expect(words).toContain(model.SPLIT_MIN_VIEWPORT_PX.toLocaleString('en-US'));
  });
  it('a phone-width window has no toggle and no full-screen button, so it says only what it shows', () => {
    const words = model.splitUnavailableWords(true);
    expect(words).toBe('One column at this width: side by side needs a wider window.');
    expect(words).not.toMatch(/full screen/i);
  });
});

describe('which entries can be opened', () => {
  it('only text, submodule and no-content-diff files are read; the rest is explained instead', () => {
    const open = entries().filter(model.canOpen).map((e) => e.state);
    expect(new Set(open)).toEqual(new Set(['text', 'submodule']));
    const states = new Set(entries().map((e) => e.state));
    expect(states).toEqual(
      new Set(['text', 'empty', 'binary', 'not_utf8', 'oversized', 'withheld', 'submodule', 'unsupported']),
    );
    // a no-content change is openable too (its permissions are the content), though no fixture file is one
    expect(model.canOpen({ ...entries()[0], state: 'no_content_diff' })).toBe(true);
    for (const state of ['empty', 'binary', 'not_utf8', 'oversized', 'withheld', 'unsupported', 'conflicted']) {
      expect(model.canOpen({ ...entries()[0], state })).toBe(false);
    }
  });

  it('a submodule shows its pointer as a diff; a nested repository says why it shows nothing', () => {
    expect(model.showsDiff('text')).toBe(true);
    expect(model.showsDiff('submodule')).toBe(true);
    for (const state of ['empty', 'binary', 'no_content_diff', 'unsupported', 'withheld']) {
      expect(model.showsDiff(state)).toBe(false);
    }
    const sub = model.stateWords('submodule', null);
    expect(sub).toMatchObject({ badge: 'submodule', title: 'Submodule', tone: 'info' });
    expect(sub.body).toContain('recorded commit');
    expect(sub.body).toContain('not read');
    const nested = model.stateWords('unsupported', 'nested_repository');
    expect(nested.badge).toBe('not shown');
    expect(nested.body).toContain('separate git repository');
  });

  it('the comparison says that a submodule is compared by its commit, not its contents', () => {
    const a = overview();
    const lines = model.comparisonSentences(
      {
        sourceId: 'x',
        worktree: 'w',
        head: 'a'.repeat(40),
        branch: 'main',
        detached: false,
        attribution: 'shared_worktree',
      } as never,
      {
        mode: 'head_to_worktree',
        baseKind: 'head_commit',
        baseOid: 'a'.repeat(40),
        candidateKind: 'working_tree',
        covers: ['staged', 'unstaged', 'untracked', 'submodule_pointers'],
        doesNotCover: ['ignored', 'submodule_contents'],
        filtersNotRun: [],
      } as never,
    );
    expect(a.state).toBe('observed');
    const text = lines.join(' ');
    expect(text).toContain('the commit a submodule points to');
    expect(text).toContain('what is inside a submodule');
  });
});

describe('the fixture these tests stand on is the one the server suite pinned', () => {
  it('is these exact bytes, and was projected from the backend fixture the server suite pins', () => {
    const digest = createHash('sha256').update(fs.readFileSync(FIXTURE)).digest('hex');
    // P2-A: regenerated through the server suite's own documented mode (DI_EXPORT_OFFICE_STATE=1) after the
    // reception projection gained the delivery source; the ONLY differences from the previous bytes are the
    // intended D1 additive kinds (evidence/p2a/s3-golden-diff-classification-2.log). The backend fixture it is
    // projected from, pinned below, is unchanged.
    expect(digest).toBe('6306f19ba61994a8dddfb1b5398c8d237ad3d98e00854308f4c6a1c5f3a11bdc');
    // the backend fixture's hash is pinned in server/__tests__/diCodeReview.test.ts; the export names it
    expect(doc.derived_from.sha256).toBe('ad29c7dcf1b8b3d7bc764db9ac9f0717efacbed2c98fb98e2786170b8325ab25');
  });
});
