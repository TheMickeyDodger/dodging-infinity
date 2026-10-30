/**
 * ROUND 30 — a notice is read WITH THE REST OF THE SCREEN PRESENT.
 *
 * The finding. A FINISHED Mission's review is read: the overview succeeds, and the tree, the branch and the change counts are drawn. The reader selects a file, and that request is refused with one of the four finished
 * reasons. The reducer KEEPS the settled overview and attaches the refusal beside it (`{ ...settled, refusal }`, by design: a refusal never erases what is still current), so the file pane's notice is printed INSIDE a view that
 * still shows a working tree. The outcome sentence said "This review could not establish a link ... and nothing from one is shown": true of the refused request, FALSE of the screen it sat in.
 *
 * The rule. A sentence shown in a composite view is a claim about everything visible around it, not only about the thing that produced it. So the outcome is scoped to the REFUSED REQUEST, and the retained overview is NOT cleared to
 * make a sentence true (that would destroy good information to satisfy a wording constraint). This file builds the composite state through the real reducer and the real mapper (no new seam, no Git fixture, no protocol, no
 * authority) and reads the notice with the overview present.
 *
 * Named by what it checks: (1) the state really IS composite (the overview is retained and drawn), (2) the notice still states the supported outcome about the refused request, (3) no sentence in the notice makes a claim
 * whose referent is the review as a whole or the view, (4) the retained overview survives the refusal (the wrong fix fails here).
 */
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

import { describe, expect, it } from 'vitest';

import type { DiCodeReview, DiMission, DiOfficeState } from '../../core/src/messages.ts';
import type { ReviewSession, ReviewTargetRef } from '../src/components/diCodeReviewModel.ts';
import * as model from '../src/components/diCodeReviewModel.ts';
import { scanDiffFor } from '../src/components/diScanDiffRead.ts';
import { scanRowsFromOffice } from '../src/components/diScanModel.ts';
import { OVERBROAD_REFERENT } from './diFinishedWordingClaims.ts';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const readJson = <T,>(file: string): T => JSON.parse(fs.readFileSync(path.join(root, 'test/fixtures', file), 'utf-8')) as T;
const clone = <T,>(value: T): T => structuredClone(value);

const historical = readJson<{ states: Record<'live', DiOfficeState> }>('di-office-state-evidence-historical-v2.FIXTURE.json');
const active = readJson<{ replies: { overview: DiCodeReview; files: Record<string, DiCodeReview> } }>('di-office-state-code-review-v2.FIXTURE.json');

const FINISHED: DiMission = historical.states.live.missions.missions.find((m) => m.sourceKind === 'mission_core')!;
const OFFER = FINISHED.codeReview!;
const TARGET: ReviewTargetRef = {
  missionRef: FINISHED.missionRef,
  memberRef: '',
  taskId: OFFER.taskId!,
  listing: OFFER.listing!,
  agentName: 'this Mission',
  level: 'mission',
};
const ROW = scanRowsFromOffice(historical.states.live).find((r) => r.id === FINISHED.missionRef)!;

/** A recorded answer re-addressed to the Mission-level reference, naming the request it answers. */
const readdressed = (answer: DiCodeReview, requestId: string): DiCodeReview => ({
  ...clone(answer),
  missionRef: TARGET.missionRef,
  memberRef: '',
  taskId: TARGET.taskId,
  listing: TARGET.listing,
  requestId,
});

const FINISHED_CODES = [
  'finished_execution_not_established',
  'finished_ownership_unverifiable',
  'finished_ownership_contested',
  'finished_workspace_not_corroborated',
] as const;

/** Overview observed -> a file selected and asked for -> that request refused with `problem`. The real reducer, in the order the App applies it. */
function compositeSession(problem: string): { session: ReviewSession; path: string } {
  const overviewAnswer = readdressed(active.replies.overview, 'w.1');
  let s = model.reduceSession(model.CLOSED_SESSION, { type: 'open', target: TARGET });
  s = model.reduceSession(s, { type: 'asked', request: { kind: 'overview' }, target: TARGET, id: 'w.1' });
  s = model.reduceSession(s, { type: 'answer', answer: overviewAnswer, nowMs: 0 });
  const entry = overviewAnswer.overview!.files.find((f) => model.canOpen(f))!;
  s = model.reduceSession(s, { type: 'select', path: entry.path });
  s = model.reduceSession(s, {
    type: 'asked',
    request: { kind: 'file', fileId: entry.fileId!, snapshot: overviewAnswer.snapshot!, path: entry.path },
    target: TARGET,
    id: 'w.2',
  });
  const fileReply = Object.values(active.replies.files).find((r) => r.fileId === entry.fileId)!;
  const refused: DiCodeReview = {
    ...readdressed(fileReply, 'w.2'),
    state: 'refused',
    problem,
    detail: null,
    target: null,
    comparison: null,
    overview: null,
    file: null,
    observedAt: null,
  };
  s = model.reduceSession(s, { type: 'answer', answer: refused, nowMs: 1 });
  return { session: s, path: entry.path };
}

/** What is on the screen for the composite state: the drawn tree, branch and counts, and the file pane's notice. */
function screen(problem: string) {
  const { session, path: selected } = compositeSession(problem);
  const source = scanDiffFor(ROW, session);
  if (source.kind !== 'read') throw new Error(`the composite state must be drawn as a read, not ${source.kind}`);
  const record = Object.values(source.diff.files).find((f) => f.path === selected)!;
  return { session, source, diff: source.diff, notice: record.notice! };
}

describe('the state is genuinely composite: the overview is retained and drawn beside the refusal', () => {
  for (const code of FINISHED_CODES) {
    it(`${code}: the tree, the branch and the change counts are on screen, and the file notice is the refusal`, () => {
      const { session, diff, notice } = screen(code);
      // the refusal is attached BESIDE the settled overview, which is kept
      expect(session.refusal?.problem).toBe(code);
      expect(session.overview).not.toBeNull();
      // and what that overview read is drawn
      expect(diff.tree.length, 'the tree is drawn').toBeGreaterThan(0);
      expect(diff.branch.length, 'the branch is drawn').toBeGreaterThan(0);
      expect(diff.filesChanged, 'the change count is drawn').toMatch(/\d+ files? changed|no changes found/);
      expect(notice.state).toBe('refused');
      expect(notice.body).toBe(model.problemWords(code, null).body);
    });
  }
});

describe('the notice, read WITH that screen present', () => {
  for (const code of FINISHED_CODES) {
    it(`${code}: states the supported outcome about THE REFUSED REQUEST, and makes no claim about the review or the view`, () => {
      const { notice } = screen(code);
      // first: no claim whose referent is the review or the view (the defect itself), so a failure names it
      for (const claim of OVERBROAD_REFERENT) {
        expect(notice.body, `${code} body must not match ${claim}`).not.toMatch(claim);
        expect(notice.title, `${code} title must not match ${claim}`).not.toMatch(claim);
      }
      // then: the supported outcome, about the request that was refused
      expect(notice.body).toContain('The refused request could not establish a link between this Mission and a working tree, and returned nothing from one.');
    });
  }

  it('the over-broad-referent set can fail: it flags the sentence rejected in round 30 and each paraphrase, and passes the request-scoped outcome', () => {
    for (const overbroad of [
      'This review could not establish a link between this Mission and a working tree, and nothing from one is shown.',
      'Nothing from a working tree is shown.',
      'Nothing is displayed for this Mission.',
      'This review found no working tree.',
      'The view has nothing to show.',
    ]) {
      expect(OVERBROAD_REFERENT.some((claim) => claim.test(overbroad)), overbroad).toBe(true);
    }
    expect(OVERBROAD_REFERENT.some((claim) => claim.test('The refused request could not establish a link between this Mission and a working tree, and returned nothing from one.'))).toBe(false);
  });
});

describe('the retained overview is NOT cleared to make the sentence true', () => {
  it('after the refusal the settled overview, the selection and the drawn tree are exactly those of the successful read', () => {
    const before = (() => {
      const overviewAnswer = readdressed(active.replies.overview, 'w.1');
      let s = model.reduceSession(model.CLOSED_SESSION, { type: 'open', target: TARGET });
      s = model.reduceSession(s, { type: 'asked', request: { kind: 'overview' }, target: TARGET, id: 'w.1' });
      return model.reduceSession(s, { type: 'answer', answer: overviewAnswer, nowMs: 0 });
    })();
    const { session } = compositeSession('finished_ownership_unverifiable');
    expect(session.overview?.answer).toEqual(before.overview?.answer);
    expect(session.cleared).toBeNull();
    expect(session.stale, 'a finished refusal is not a moved workspace').toBe(false);
    const drawn = scanDiffFor(ROW, before);
    const after = scanDiffFor(ROW, session);
    expect(drawn.kind).toBe('read');
    expect(after.kind).toBe('read');
    expect((after as { diff: { tree: unknown } }).diff.tree).toEqual((drawn as { diff: { tree: unknown } }).diff.tree);
  });
});
