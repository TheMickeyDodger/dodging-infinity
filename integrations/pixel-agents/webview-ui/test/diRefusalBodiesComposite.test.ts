/**
 * THE SIBLING INCREMENT: the four older refusal bodies that said "Nothing was read.", read WITH THE REST OF THE SCREEN PRESENT.
 *
 * The finding (round 30, applied to its siblings). A body printed beside a retained overview makes claims about everything visible around it. "Nothing was read." is true of the refused REQUEST for the codes the office
 * decides before it asks the backend, and false of the SCREEN, which still draws what an earlier request read; and for the codes the backend can raise at its end-of-read re-check it is false of the request too.
 * The reducer attaches any refusal to the settled overview with no filter on the code, so the defect belongs to every body that can arrive there. The increment is however many bodies the composite shape actually
 * REACHES, and each is demonstrated, not argued (the ledger below names the instrument for each).
 *
 * What this file checks, per body: (1) the composite state is genuine (the refusal is attached beside a retained overview that is drawn); (2) the notice's cause clause, title and retry flag are EXACTLY what they were
 * (this is a wording change to one trailing sentence, nothing else); (3) the trailing sentence is the request-scoped, phase-free one; (4) no read-history claim in either direction and no claim whose referent is the
 * review or the view.
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
const repoFile = (...parts: string[]): string => fs.readFileSync(path.resolve(root, '../../..', ...parts), 'utf-8');

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

const readdressed = (answer: DiCodeReview, requestId: string): DiCodeReview => ({
  ...clone(answer),
  missionRef: TARGET.missionRef,
  memberRef: '',
  taskId: TARGET.taskId,
  listing: TARGET.listing,
  requestId,
});

/** Overview observed -> a file selected and asked for -> that request refused with `problem`: the real reducer, in the order the App applies it. */
function compositeSession(problem: string): { session: ReviewSession; path: string } {
  const overviewAnswer = readdressed(active.replies.overview, 'w.1');
  let s = model.reduceSession(model.CLOSED_SESSION, { type: 'open', target: TARGET });
  s = model.reduceSession(s, { type: 'asked', request: { kind: 'overview' }, target: TARGET, id: 'w.1' });
  s = model.reduceSession(s, { type: 'answer', answer: overviewAnswer, nowMs: 0 });
  const entry = overviewAnswer.overview!.files.find((f) => model.canOpen(f))!;
  s = model.reduceSession(s, { type: 'select', path: entry.path });
  s = model.reduceSession(s, { type: 'asked', request: { kind: 'file', fileId: entry.fileId!, snapshot: overviewAnswer.snapshot!, path: entry.path }, target: TARGET, id: 'w.2' });
  const fileReply = Object.values(active.replies.files).find((r) => r.fileId === entry.fileId)!;
  const refused: DiCodeReview = { ...readdressed(fileReply, 'w.2'), state: 'refused', problem, detail: null, target: null, comparison: null, overview: null, file: null, observedAt: null };
  s = model.reduceSession(s, { type: 'answer', answer: refused, nowMs: 1 });
  return { session: s, path: entry.path };
}
function screen(problem: string) {
  const { session, path: selected } = compositeSession(problem);
  const source = scanDiffFor(ROW, session);
  if (source.kind !== 'read') throw new Error(`the composite state must be drawn as a read, not ${source.kind}`);
  const record = Object.values(source.diff.files).find((f) => f.path === selected)!;
  return { session, diff: source.diff, notice: record.notice! };
}

/** The sentence every one of these bodies now ends with: about THE REFUSED REQUEST, true at admission and at the end-of-read re-check, a claim about no screen. */
const TAIL = 'The refused request returned nothing from a working tree.';

/**
 * The four bodies, with what must NOT change (title, cause clause, retry) and where each code was DEMONSTRATED reachable as a file refusal beside an observed overview.
 * `where` names files that must exist and mention the code: a cross-reference to the instrument, not itself a proof of behaviour (the instruments run in their own routes).
 */
const BODIES = [
  {
    code: 'mission_listed_more_than_once',
    title: 'The assignment is listed more than once',
    cause: 'The office lists this Mission twice, so which workspace is meant cannot be told.',
    retry: false,
    where: ['integrations/pixel-agents/server/__tests__/diRefusalCompositeReach.test.ts', 'tests/test_refusal_file_view_reach.py'],
  },
  {
    code: 'member_listed_more_than_once',
    title: 'The agent is listed more than once',
    cause: 'The roster lists this agent twice, so the review cannot tell which one is meant.',
    retry: false,
    where: ['integrations/pixel-agents/server/__tests__/diRefusalCompositeReach.test.ts'],
  },
  {
    code: 'backend_discovery_unavailable',
    title: 'The backend cannot be found',
    cause: 'The office could not find the catalog backend to ask.',
    retry: true,
    where: ['integrations/pixel-agents/server/__tests__/diRefusalCompositeReach.test.ts'],
  },
  {
    code: 'no_fresh_catalog_answer',
    title: 'The backend has no current picture of the assignment',
    cause: 'Its last catalog answer is missing or too old to show the assignment is active.',
    retry: true,
    where: ['integrations/pixel-agents/server/__tests__/diRefusalCompositeReach.test.ts', 'tests/test_refusal_file_view_reach.py'],
  },
] as const;

/** The read-history claims (in either direction) a refusal sentence must not make; the same family diFinishedCodeReviewModel.test.ts pins for the finished reasons. */
const READ_HISTORY: RegExp[] = [
  /nothing (was|has been|is|had been) read/i,
  /(was|were|has been|have been|had been) (not |never )?read\b/i,
  /discard/i,
  /no (file|files|read|content) (was|were|is) (made|read|opened|fetched)/i,
  /nothing was (opened|fetched|looked at|touched|loaded)/i,
];

describe('each body\'s code is reached as a file refusal beside an observed overview (the ledger)', () => {
  for (const b of BODIES) {
    it(`${b.code}: its instruments exist and name it`, () => {
      expect(b.where.length).toBeGreaterThan(0);
      for (const file of b.where) expect(repoFile(file), `${file} must mention ${b.code}`).toContain(b.code);
    });
  }
  it('the ledger covers exactly the four bodies that say "Nothing was read." at the start of this increment', () => {
    expect(BODIES.map((b) => b.code).sort()).toEqual(['backend_discovery_unavailable', 'member_listed_more_than_once', 'mission_listed_more_than_once', 'no_fresh_catalog_answer']);
  });
});

describe('the four bodies, read WITH the retained overview present', () => {
  for (const b of BODIES) {
    describe(b.code, () => {
      it('the state is genuinely composite: the refusal is beside a retained, drawn overview', () => {
        const { session, diff, notice } = screen(b.code);
        expect(session.refusal?.problem).toBe(b.code);
        expect(session.overview).not.toBeNull();
        expect(diff.tree.length, 'the tree is drawn').toBeGreaterThan(0);
        expect(diff.branch.length, 'the branch is drawn').toBeGreaterThan(0);
        expect(notice.state).toBe('refused');
      });
      it('claims nothing about the review, the view or what was read, in either direction', () => {
        const { notice } = screen(b.code);
        for (const claim of [...READ_HISTORY, ...OVERBROAD_REFERENT]) {
          expect(notice.body, `${b.code} body must not match ${claim}`).not.toMatch(claim);
          expect(notice.title, `${b.code} title must not match ${claim}`).not.toMatch(claim);
        }
      });
      it('says what it said before (title, cause clause, retry) and ends with the request-scoped sentence', () => {
        const { notice } = screen(b.code);
        const said = model.problemWords(b.code, null);
        expect(notice.title).toBe(b.title);
        expect(said.title).toBe(b.title);
        expect(notice.body).toBe(`${b.cause} ${TAIL}`);
        expect(said.retry).toBe(b.retry);
      });
    });
  }

  it('the read-history set can fail: it flags the old sentence and its mirror, and passes the request-scoped one', () => {
    for (const bad of ['Nothing was read.', 'What was read was discarded.', 'The workspace was not read.', 'No file was opened.']) {
      expect(READ_HISTORY.some((claim) => claim.test(bad)), bad).toBe(true);
    }
    expect(READ_HISTORY.some((claim) => claim.test(TAIL))).toBe(false);
    expect(OVERBROAD_REFERENT.some((claim) => claim.test(TAIL))).toBe(false);
  });
});

describe('only the four are changed by this increment', () => {
  it('no other body gained the sentence, and none of the four kept "Nothing was read."', () => {
    const keys = model.PROBLEM_WORD_KEYS as readonly string[];
    const carrying = keys.filter((k) => model.problemWords(k, null).body.includes(TAIL)).sort();
    expect(carrying).toEqual(BODIES.map((b) => b.code).sort());
    for (const k of keys) expect(model.problemWords(k, null).body, k).not.toContain('Nothing was read');
  });
});
