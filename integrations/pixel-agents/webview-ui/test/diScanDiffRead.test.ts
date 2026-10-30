/**
 * Stage 4, increment 1: the scan Code Diff's mapper (`diScanDiffRead.ts`) over the REAL recorded emission
 * (`test/fixtures/di-office-state-code-review-v2.FIXTURE.json`: office states and review answers that the real server code produced from
 * the backend's exported answers over a fixture git workspace, hash-pinned by the server's own tests). Pure: no browser.
 *
 * It proves the behaviours the increment claims and the boundary failures around them:
 *  - THE SPECIMEN COMES FIRST, whatever the session holds;
 *  - THE MISSION SWITCH: a session that holds another Mission's diff is never shown under this Mission's name, including the case where
 *    everything but the Mission ref collides (only the Mission equality can tell them apart);
 *  - an answer for another reference is never applied; a refusal, a cleared session and every "not offered" reason are stated states, and
 *    none is an empty diff;
 *  - UNKNOWN IS NOT ABSENT, both directions: what the route read appears; what it did not determine says so (encoding, line ending,
 *    indentation, a count that is only a floor, a detached HEAD, "no changes found" only when a listing was READ and was empty);
 *  - every file state and every kind of change the route distinguishes stays distinct, and patch text is shown inert.
 */
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

import { describe, expect, it } from 'vitest';

import type { DiCodeReview, DiCodeReviewEntry, DiOfficeState } from '../../core/src/messages.ts';
import type { ReviewSession, ReviewTargetRef } from '../src/components/diCodeReviewModel.ts';
import {
  changeGlyph,
  CLOSED_SESSION,
  createDiCodeReviewFeed,
  createRequestIds,
  isRetryable,
  nextRequest,
  reduceSession,
  UNAVAILABLE_WORDS,
} from '../src/components/diCodeReviewModel.ts';
import {
  DI_DIFF_NOT_COVERED_WORDS,
  DI_DIFF_STATUS_WORDS,
  languageWords,
  noteOf,
  scanDiffFor,
  treeOf,
} from '../src/components/diScanDiffRead.ts';
import type { DiScanDiffSource, DiScanRow } from '../src/components/diScanModel.ts';
import {
  DI_DIFF_NOT_READ,
  DI_DIFF_READING,
  DI_SCAN_LOCK,
  reviewOfferOf,
  scanDiff,
  scanRowsFromOffice,
} from '../src/components/diScanModel.ts';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const doc = JSON.parse(
  fs.readFileSync(
    path.join(root, 'test/fixtures/di-office-state-code-review-v2.FIXTURE.json'),
    'utf-8',
  ),
) as {
  reference: { missionRef: string; memberRef: string; taskId: string; listing: string };
  states: Record<
    'live' | 'stale' | 'inactive' | 'moved' | 'agentGone' | 'unavailable',
    DiOfficeState
  >;
  replies: { overview: DiCodeReview; files: Record<string, DiCodeReview> };
};
const clone = <T>(value: T): T => structuredClone(value);
const OVERVIEW = doc.replies.overview;
const LISTING = OVERVIEW.overview!;
const entry = (p: string): DiCodeReviewEntry => {
  const found = LISTING.files.find((f) => f.path === p);
  if (!found) throw new Error(`the recorded overview lists ${p}`);
  return found;
};
const fileReply = (p: string): DiCodeReview => {
  const e = entry(p);
  const found = Object.values(doc.replies.files).find((r) => r.fileId === e.fileId);
  if (!found) throw new Error(`a recorded file answer for ${p}`);
  return clone(found);
};

/** The row the scan panel builds for the recorded Mission, and the office's own admitted reference for it. */
const liveRows = scanRowsFromOffice(doc.states.live);
const ROW: DiScanRow = liveRows[0]!;
const TARGET = (ROW.reviewOffer as { kind: 'offered'; target: ReviewTargetRef }).target;

/** A session opened for `target`, asked for the overview, and (optionally) answered. */
function openSession(
  target: ReviewTargetRef,
  overview: DiCodeReview | null = OVERVIEW,
  selected: string | null = null,
  file: DiCodeReview | null = null,
): ReviewSession {
  let s = reduceSession(CLOSED_SESSION, { type: 'open', target });
  s = reduceSession(s, { type: 'asked', request: { kind: 'overview' } });
  if (overview !== null) s = reduceSession(s, { type: 'answer', answer: overview, nowMs: 0 });
  if (selected !== null) {
    s = reduceSession(s, { type: 'select', path: selected });
    const e = entry(selected);
    s = reduceSession(s, {
      type: 'asked',
      request: { kind: 'file', fileId: e.fileId, snapshot: OVERVIEW.snapshot!, path: selected },
    });
    if (file !== null) s = reduceSession(s, { type: 'answer', answer: file, nowMs: 0 });
  }
  return s;
}
const kindOf = (source: DiScanDiffSource): string => source.kind;
const asRead = (source: DiScanDiffSource) => {
  if (source.kind !== 'read') throw new Error(`expected a read source, got ${source.kind}`);
  return source;
};

describe("the row carries the office's own statement about a review", () => {
  it('an offered review is the admitted reference: Mission, member, task and listing, and no path', () => {
    expect(ROW.reviewOffer.kind).toBe('offered');
    expect(TARGET).toEqual({ ...doc.reference, agentName: expect.any(String) });
    expect(Object.keys(TARGET).sort()).toEqual([
      'agentName',
      'listing',
      'memberRef',
      'missionRef',
      'taskId',
    ]);
    expect(ROW.id).toBe(doc.reference.missionRef);
  });

  it('each way a review is not offered keeps its own reason; a Mission-level reason outranks a member-level one', () => {
    expect(scanRowsFromOffice(doc.states.stale)[0]!.reviewOffer).toEqual({
      kind: 'unavailable',
      reason: 'member_not_live',
    });
    expect(scanRowsFromOffice(doc.states.inactive)[0]!.reviewOffer).toEqual({
      kind: 'unavailable',
      reason: 'assignment_not_active',
    });
    // two members: one says member_not_live, one says assignment_not_active: the Mission-level reason is used
    const both = clone(doc.states.live);
    const mission = both.missions.missions[0]!;
    const second = clone(mission.members.members[0]!);
    second.memberRef = `${second.memberRef}-2`;
    mission.members.members[0]!.codeReview = {
      state: 'unavailable',
      reason: 'member_not_live',
      taskId: null,
      listing: null,
    };
    second.codeReview = {
      state: 'unavailable',
      reason: 'assignment_not_active',
      taskId: null,
      listing: null,
    };
    mission.members.members.push(second);
    expect(reviewOfferOf(mission)).toEqual({
      kind: 'unavailable',
      reason: 'assignment_not_active',
    });
  });

  it('a Mission whose members say nothing about a review (or that lists no member) is NOT CLAIMED: nothing is read, nothing is offered', () => {
    expect(scanRowsFromOffice(doc.states.agentGone)[0]!.reviewOffer).toEqual({ kind: 'unknown' });
    const silent = clone(doc.states.live);
    delete silent.missions.missions[0]!.members.members[0]!.codeReview;
    expect(scanRowsFromOffice(silent)[0]!.reviewOffer).toEqual({ kind: 'unknown' });
  });
});

describe('the specimen comes first; an offered review is the real path, not the layout-preview stub', () => {
  const lock = DI_SCAN_LOCK[0]!;
  it('the owner-approved specimen is returned untouched, whatever the session holds', () => {
    const holding = openSession(TARGET);
    const source = scanDiffFor(lock, holding);
    expect(source.kind).toBe('sample');
    expect(scanDiffFor(lock, null).kind).toBe('sample');
    expect(scanDiff(lock).kind).toBe('sample');
  });
  it('a row the office said nothing about keeps the existing not-read words', () => {
    const silent = { ...ROW, reviewOffer: { kind: 'unknown' } as const };
    expect(scanDiff(silent)).toEqual({ kind: 'not_read', reason: DI_DIFF_NOT_READ });
    expect(scanDiffFor(silent, openSession(TARGET))).toEqual({
      kind: 'not_read',
      reason: DI_DIFF_NOT_READ,
    });
  });
  it('an offered review, including with no session yet, is a read of the working tree, not a layout preview', () => {
    expect(scanDiff(ROW)).toEqual({ kind: 'reading', reason: DI_DIFF_READING });
    expect(scanDiffFor(ROW, null)).toEqual({ kind: 'reading', reason: DI_DIFF_READING });
    expect(JSON.stringify(scanDiff(ROW))).not.toContain('layout preview');
    const herdr = {
      ...ROW,
      id: 'di_herdr_tasks:20261014-000000-aaaaaa@abcdefabcdef',
      sourceKind: 'herdr_task',
      reviewOffer: {
        kind: 'offered' as const,
        target: {
          ...TARGET,
          missionRef: 'di_herdr_tasks:20261014-000000-aaaaaa@abcdefabcdef',
        },
      },
    };
    expect(scanDiff(herdr).kind).toBe('reading');
    expect(scanDiffFor(herdr, null).kind).toBe('reading');
  });
  it('an offer the office refused is that reason, for a Herdr task whose workspace is not bound as well', () => {
    const inactive = scanRowsFromOffice(doc.states.inactive)[0]!;
    expect(scanDiff(inactive).kind).toBe('unavailable');
    expect(scanDiffFor(inactive, null).kind).toBe('unavailable');
    const unbound = {
      ...ROW,
      sourceKind: 'herdr_task',
      reviewOffer: { kind: 'unavailable' as const, reason: 'assignment_not_bound' },
    };
    const source = scanDiff(unbound);
    expect(source.kind).toBe('unavailable');
    if (source.kind === 'unavailable') {
      expect(source.detail).toContain('not confirmed');
      expect(source.reason).not.toContain('layout preview');
    }
  });
});

describe("THE MISSION SWITCH: a session that holds another Mission's diff is never shown under this Mission's name", () => {
  const OTHER_REF = 'mc1:mn-00000000000000000000000000000002';
  const rowFor = (missionRef: string, target: ReviewTargetRef): DiScanRow => ({
    ...ROW,
    id: missionRef,
    reviewOffer: { kind: 'offered', target },
  });

  it("the steady state: this Mission's own session shows this Mission's tree", () => {
    const source = scanDiffFor(ROW, openSession(TARGET));
    expect(kindOf(source)).toBe('read');
    expect(asRead(source).diff.tree.filter((t) => t.kind === 'file')).toHaveLength(
      LISTING.files.length,
    );
  });

  it('the SWITCH render: the row is Mission B while the session still holds Mission A: B is "reading", and not one of A\'s files appears', () => {
    const b: ReviewTargetRef = {
      ...TARGET,
      missionRef: OTHER_REF,
      memberRef: `${OTHER_REF}#t#executor1`,
      taskId: 'task-b',
      listing: 'b'.repeat(64),
    };
    const source = scanDiffFor(rowFor(OTHER_REF, b), openSession(TARGET));
    expect(kindOf(source)).toBe('reading');
    expect(JSON.stringify(source)).not.toContain('app.py');
    expect(JSON.stringify(source)).not.toContain(LISTING.files[0]!.fileId);
  });

  it('EVEN WHEN EVERYTHING BUT THE MISSION COLLIDES (same member, task and listing): only the Mission equality tells them apart, and it holds', () => {
    // a deliberately hostile collision: Mission B's offer carries A's member, task and listing verbatim
    const collide: ReviewTargetRef = { ...TARGET, missionRef: OTHER_REF };
    const heldForA: ReviewTargetRef = { ...TARGET };
    const source = scanDiffFor(rowFor(OTHER_REF, collide), openSession(heldForA));
    expect(kindOf(source)).toBe('reading');
    expect(JSON.stringify(source)).not.toContain('app.py');
  });

  it("the listing moved: the row's offer is a NEW reference, so the old session is not this row's", () => {
    const moved: ReviewTargetRef = { ...TARGET, listing: 'c'.repeat(64) };
    expect(kindOf(scanDiffFor(rowFor(ROW.id, moved), openSession(TARGET)))).toBe('reading');
    const otherTask: ReviewTargetRef = { ...TARGET, taskId: 'another-task' };
    expect(kindOf(scanDiffFor(rowFor(ROW.id, otherTask), openSession(TARGET)))).toBe('reading');
    const otherMember: ReviewTargetRef = { ...TARGET, memberRef: `${TARGET.memberRef}-x` };
    expect(kindOf(scanDiffFor(rowFor(ROW.id, otherMember), openSession(TARGET)))).toBe('reading');
  });

  it('a closed session is "reading" for an offered row, never a diff and never empty', () => {
    expect(kindOf(scanDiffFor(ROW, CLOSED_SESSION))).toBe('reading');
  });

  it('an answer for ANOTHER reference is never applied to the session, so it is never shown', () => {
    const wrong = clone(OVERVIEW);
    wrong.missionRef = OTHER_REF;
    const session = openSession(TARGET, wrong);
    expect(session.overview).toBeNull();
    expect(kindOf(scanDiffFor(ROW, session))).toBe('reading');
    const wrongListing = clone(OVERVIEW);
    wrongListing.listing = 'd'.repeat(64);
    expect(openSession(TARGET, wrongListing).overview).toBeNull();
    const wrongTask = clone(OVERVIEW);
    wrongTask.taskId = 'not-the-task';
    expect(openSession(TARGET, wrongTask).overview).toBeNull();
  });
});

describe("the states that are NOT a diff, each in the route's own words, none an empty diff", () => {
  it('each way a review is not offered: "unavailable" with the office\'s own reason in words (all seven)', () => {
    for (const reason of Object.keys(UNAVAILABLE_WORDS)) {
      const row: DiScanRow = { ...ROW, reviewOffer: { kind: 'unavailable', reason } };
      const source = scanDiffFor(row, openSession(TARGET));
      expect(source.kind, reason).toBe('unavailable');
      if (source.kind !== 'unavailable') continue;
      expect(source.detail, reason).toBe(UNAVAILABLE_WORDS[reason]);
      expect(source.title).toBe('Code review is not offered');
      expect(source.reason.length).toBeGreaterThan(20);
    }
  });

  it('a finished Mission (the assignment is not active) is unavailable with that reason, never an empty diff', () => {
    const finished: DiScanRow = {
      ...ROW,
      reviewOffer: { kind: 'unavailable', reason: 'assignment_not_active' },
    };
    const source = scanDiffFor(finished, null);
    expect(source).toMatchObject({
      kind: 'unavailable',
      detail: 'The assignment is not active work now.',
    });
  });

  it('a cleared session says why and keeps NO content (connection lost, office expired, assignment changed, agent gone ...)', () => {
    for (const reason of [
      'connection_lost',
      'reconnected_awaiting_state',
      'office_expired',
      'office_not_live',
      'assignment_changed',
      'agent_not_live',
      'agent_gone',
    ] as const) {
      const cleared = reduceSession(openSession(TARGET), { type: 'clear', reason });
      const source = scanDiffFor(ROW, cleared);
      expect(source.kind, reason).toBe('unavailable');
      expect(JSON.stringify(source), reason).not.toContain('app.py');
    }
  });

  it('a refused overview is "refused" with the route\'s problem in words, not "reading" and not empty', () => {
    const refused = clone(OVERVIEW);
    refused.state = 'refused';
    refused.problem = 'listing_moved';
    refused.detail = 'the assignment moved since this listing';
    refused.overview = null;
    refused.target = null;
    refused.comparison = null;
    refused.snapshot = null;
    const source = scanDiffFor(ROW, openSession(TARGET, refused));
    expect(kindOf(source)).toBe('refused');
    if (source.kind === 'refused') expect(source.title.length).toBeGreaterThan(5);
  });

  it('an overview that was asked and has not arrived is "reading": not read, and not empty', () => {
    expect(kindOf(scanDiffFor(ROW, openSession(TARGET, null)))).toBe('reading');
  });
});

describe('UNKNOWN IS NOT ABSENT: what the route read appears, what it did not determine says so', () => {
  const read = asRead(scanDiffFor(ROW, openSession(TARGET)));

  it('the real facts appear: the branch, the exact listing count and every listed file by the id the route gave it', () => {
    expect(read.diff.branch).toBe('main');
    expect(read.diff.filesChanged).toBe('23 files changed');
    expect(
      read.diff.tree
        .filter((t) => t.kind === 'file')
        .map((t) => t.id)
        .sort(),
    ).toEqual(LISTING.files.map((f) => f.fileId).sort());
  });

  it('encoding, line ending and indentation are UNKNOWN on every real file, and "UTF-8" is never said', () => {
    expect(read.diff.encoding).toBe(DI_DIFF_STATUS_WORDS.encoding.visible);
    expect(read.diff.lineEnding).toBe(DI_DIFF_STATUS_WORDS.lineEnding.visible);
    expect(read.diff.indent).toBe(DI_DIFF_STATUS_WORDS.indent.visible);
    // spelled out, so the expectation does not come from the constants under test
    expect([read.diff.encoding, read.diff.lineEnding, read.diff.indent]).toEqual([
      'Encoding unknown',
      'Line ending unknown',
      'Indentation unknown',
    ]);
    for (const word of [read.diff.encoding, read.diff.lineEnding, read.diff.indent]) {
      expect(word).toMatch(/unknown/i);
      expect(word).not.toMatch(/UTF|LF|CRLF|spaces|tabs/i);
    }
    // a text file the route showed (its DIFF TEXT was strict UTF-8) STILL does not make the three facts determined. (The note's own
    // "not covered: ... non-UTF-8 files" is a statement about what the listing excludes, not a claim about any file's encoding.)
    const withFile = asRead(
      scanDiffFor(ROW, openSession(TARGET, OVERVIEW, 'src/app.py', fileReply('src/app.py'))),
    );
    for (const word of [withFile.diff.encoding, withFile.diff.lineEnding, withFile.diff.indent]) {
      expect(word).toMatch(/unknown/i);
      expect(word).not.toMatch(/UTF|LF|CRLF|spaces|tabs/i);
    }
    expect(Object.values(withFile.diff.files)[0]!.language).toBe('Python');
  });

  it('the totals are a FLOOR when any listed file carries no count (the backend sums a missing count as zero)', () => {
    expect(LISTING.files.some((f) => f.additions === null)).toBe(true);
    expect(read.diff.added).toBe(`${LISTING.totals.additions}+`);
    expect(read.diff.removed).toBe(`${LISTING.totals.deletions}+`);
    expect(read.diff.countsNote).toMatch(/floor/);
  });

  it('the totals are EXACT when every listed file carries its counts', () => {
    const counted = clone(OVERVIEW);
    counted.overview!.files = counted.overview!.files.filter(
      (f) => f.additions !== null && f.deletions !== null,
    );
    counted.overview!.totals = {
      files: counted.overview!.files.length,
      additions: counted.overview!.files.reduce((n, f) => n + (f.additions ?? 0), 0),
      deletions: counted.overview!.files.reduce((n, f) => n + (f.deletions ?? 0), 0),
      withheld: 0,
      unshown: counted.overview!.files.filter((f) => f.state !== 'text' && f.state !== 'submodule')
        .length,
    };
    const exact = asRead(scanDiffFor(ROW, openSession(TARGET, counted)));
    expect(exact.diff.added).toBe(String(counted.overview!.totals.additions));
    expect(exact.diff.removed).toBe(String(counted.overview!.totals.deletions));
    expect(exact.diff.countsNote).toBeUndefined();
  });

  it('an all-binary change is never "+0 -0": the floor at zero is a question mark', () => {
    const binary = clone(OVERVIEW);
    binary.overview!.files = binary.overview!.files.filter((f) => f.state === 'binary');
    binary.overview!.totals = { files: 1, additions: 0, deletions: 0, withheld: 0, unshown: 1 };
    const source = asRead(scanDiffFor(ROW, openSession(TARGET, binary)));
    expect(source.diff.added).toBe('?');
    expect(source.diff.removed).toBe('?');
    expect(source.diff.filesChanged).toBe('1 file changed');
  });

  it('"no changes found" is said ONLY when a listing was READ and was empty, as a finding whose limits are in its NOTICE (never "0 files changed")', () => {
    const empty = clone(OVERVIEW);
    empty.overview!.files = [];
    empty.overview!.totals = { files: 0, additions: 0, deletions: 0, withheld: 0, unshown: 0 };
    const source = asRead(scanDiffFor(ROW, openSession(TARGET, empty)));
    expect(source.diff.filesChanged).toBe('no changes found');
    expect(source.diff.filesChanged).not.toMatch(/\b0 files/);
    expect(source.emptyNotice?.title).toBe('No changes found');
    expect(source.emptyNotice?.body).toMatch(/not proof/);
    // an overview that was NOT read (refused, still reading, cleared) is never "no changes found"
    const notRead = [
      openSession(TARGET, null),
      reduceSession(openSession(TARGET), { type: 'clear', reason: 'agent_gone' }),
    ];
    for (const s of notRead)
      expect(JSON.stringify(scanDiffFor(ROW, s))).not.toMatch(/no changes found/i);
  });

  it('THE BASE is stated in the ORDINARY (non-empty) view: the revision the working tree was compared with, short for the bar and whole for its title', () => {
    expect(LISTING.files.length).toBeGreaterThan(0);
    expect(read.diff.base).toEqual({
      short: OVERVIEW.comparison!.baseOid.slice(0, 10),
      full: OVERVIEW.comparison!.baseOid,
    });
    expect(read.diff.base!.short).toHaveLength(10);
  });

  it('an OBSERVED EMPTY listing has KNOWN totals: exactly zero, no floor, and no sentence about files that carry no counts (certain is not shown as uncertain)', () => {
    const empty = clone(OVERVIEW);
    empty.overview!.files = [];
    empty.overview!.totals = { files: 0, additions: 0, deletions: 0, withheld: 0, unshown: 0 };
    const source = asRead(scanDiffFor(ROW, openSession(TARGET, empty)));
    expect(source.diff.added).toBe('0');
    expect(source.diff.removed).toBe('0');
    expect(source.diff.countsNote).toBeUndefined();
    expect(JSON.stringify(source)).not.toMatch(/floor|carry no line counts/);
    // the base is stated here too, and the notice's limits are the notice's own
    expect(source.diff.base?.full).toBe(OVERVIEW.comparison!.baseOid);
  });

  it('ONE listed file without counts is enough to make the totals a floor, with the reason that is TRUE for it', () => {
    const oneMissing = clone(OVERVIEW);
    oneMissing.overview!.files = oneMissing.overview!.files.filter(
      (f) => f.path === 'src/app.py' || f.state === 'binary',
    );
    oneMissing.overview!.totals = { files: 2, additions: 1, deletions: 1, withheld: 0, unshown: 1 };
    const source = asRead(scanDiffFor(ROW, openSession(TARGET, oneMissing)));
    expect(source.diff.added).toBe('1+');
    expect(source.diff.removed).toBe('1+');
    expect(source.diff.countsNote).toMatch(/Some listed files carry no line counts/);
  });

  it('a stale listing is labelled as of when it was read, and its count is "when read"', () => {
    const stale = reduceSession(openSession(TARGET), { type: 'moved' });
    const source = asRead(scanDiffFor(ROW, stale));
    expect(source.diff.filesChanged).toBe('23 files changed when read');
    expect(source.diff.tag).toMatch(/changed since it was read/);
  });

  it('a detached HEAD says detached; a branch that is neither named nor detached says it is unknown', () => {
    const detached = clone(OVERVIEW);
    detached.target!.branch = null;
    detached.target!.detached = true;
    const detachedRead = asRead(scanDiffFor(ROW, openSession(TARGET, detached)));
    // the visible word is the shortest TRUE one; "HEAD" completes the accessible name and the title (nothing is removed from what is read out)
    expect(detachedRead.diff.branch).toBe('Detached');
    expect(detachedRead.diff.branchHidden).toBe(' HEAD');
    expect(detachedRead.diff.branch + detachedRead.diff.branchHidden).toBe('Detached HEAD');
    // "Detached" is a fixed LABEL, not a variable value: the bar must never truncate it the way it truncates a real branch name
    expect(detachedRead.diff.branchFixed).toBe(true);
    expect(detachedRead.diff.base).toEqual({
      short: detached.comparison!.baseOid.slice(0, 10),
      full: detached.comparison!.baseOid,
    });
    const unknown = clone(OVERVIEW);
    unknown.target!.branch = null;
    unknown.target!.detached = false;
    const unknownRead = asRead(scanDiffFor(ROW, openSession(TARGET, unknown)));
    expect(unknownRead.diff.branch).toBe('Branch unknown');
    expect(unknownRead.diff.branchFixed).toBe(true); // a fixed phrase, never truncated like a name
  });

  // THE CHECKOUT AND THE COMPARISON BASE ARE TWO FACTS. The route carries them in two fields: `target.head` (the revision the checkout is
  // sitting at) and `comparison.baseOid` (the revision the diff is computed against). The office refuses an answer in which they differ
  // (server/src/di/codeReview.ts, parseComparison: `base.oid !== head`), but this mapper does not RELY on that: each is shown from its
  // own field under its own label, so an answer in which they differed would still be shown truthfully, never as one fact.
  it('the detached HEAD identity is read from target.head, the base from comparison.baseOid, and they are never swapped or merged', () => {
    const detached = clone(OVERVIEW);
    detached.target!.branch = null;
    detached.target!.detached = true;
    detached.target!.head = 'a'.repeat(40);
    detached.comparison!.baseOid = 'b'.repeat(40);
    const read = asRead(scanDiffFor(ROW, openSession(TARGET, detached)));
    expect(read.diff.branchTitle).toBe(`Detached HEAD at ${'a'.repeat(40)}`);
    expect(read.diff.branchTitle).not.toContain('b'.repeat(10));
    expect(read.diff.base).toEqual({ short: 'b'.repeat(10), full: 'b'.repeat(40) });
    expect(read.diff.base!.full).not.toContain('a');
    // the visible words never state a revision as the checkout's: the only revision on the bar is the one labelled base
    expect(read.diff.branch).toBe('Detached');
  });

  it('on the answers the office admits (head equal to the base) the same holds, and the title names the checkout in full', () => {
    const detached = clone(OVERVIEW);
    detached.target!.branch = null;
    detached.target!.detached = true;
    expect(detached.target!.head).toBe(detached.comparison!.baseOid); // the fixture is a real office answer: equal
    const read = asRead(scanDiffFor(ROW, openSession(TARGET, detached)));
    expect(read.diff.branchTitle).toBe(`Detached HEAD at ${detached.target!.head}`);
  });

  it('the base is present in EVERY read state (a branch, a detached HEAD, a branch that is unknown): a detached HEAD never loses its revision', () => {
    for (const [branch, isDetached] of [
      ['main', false],
      [null, true],
      [null, false],
    ] as const) {
      const answer = clone(OVERVIEW);
      answer.target!.branch = branch;
      answer.target!.detached = isDetached;
      const read = asRead(scanDiffFor(ROW, openSession(TARGET, answer)));
      expect(read.diff.base, `${branch}/${isDetached}`).toEqual({
        short: answer.comparison!.baseOid.slice(0, 10),
        full: answer.comparison!.baseOid,
      });
    }
  });

  it('a detached answer without its comparison draws NO bar at all: "Detached" is never shown without the base beside it', () => {
    const answer = clone(OVERVIEW);
    answer.target!.branch = null;
    answer.target!.detached = true;
    answer.comparison = null;
    const source = scanDiffFor(ROW, openSession(TARGET, answer));
    expect(source.kind).not.toBe('read');
  });

  it('a named branch keeps its title as the branch itself, in full, and has no hidden words', () => {
    const named = clone(OVERVIEW);
    named.target!.branch = 'feature/' + 'x'.repeat(60);
    const read = asRead(scanDiffFor(ROW, openSession(TARGET, named)));
    expect(read.diff.branch).toBe(named.target!.branch);
    expect(read.diff.branchTitle).toBe(named.target!.branch);
    expect(read.diff.branchHidden).toBeUndefined();
    expect(read.diff.branchFixed).toBeUndefined(); // a real name is the one VARIABLE field the bar may truncate
  });

  it('the file facts the route does not carry are ONE collective phrase in the bar, and the accessible text ENUMERATES all three', () => {
    const read = asRead(scanDiffFor(ROW, openSession(TARGET)));
    expect(read.diff.format?.visible).toBe('Format unknown');
    expect(read.diff.format?.accessible).toBe(
      [
        DI_DIFF_STATUS_WORDS.encoding.full,
        DI_DIFF_STATUS_WORDS.lineEnding.full,
        DI_DIFF_STATUS_WORDS.indent.full,
      ].join('. '),
    );
    // the same enumeration spelled out, so the expectation does not come from the constants under test; "to this review" stays (a limit of the
    // review's reading, not a property of the file)
    expect(read.diff.format?.accessible).toBe(
      'Encoding: unknown to this review. Line ending: unknown to this review. Indentation: unknown to this review',
    );
    for (const fact of [/Encoding/i, /Line ending/i, /Indentation/i])
      expect(read.diff.format?.accessible).toMatch(fact);
    expect(read.diff.format?.accessible).not.toMatch(/UTF|\bLF\b|CRLF|spaces|tabs/i);
  });

  it('ONE VOCABULARY: nothing the mapper hands the bar to draw or to read out says "not determined"', () => {
    // The format phrase, its enumeration, the title and the label of a branch that is neither named nor detached, the language of an unknown
    // extension, AND the three per-fact fields (`encoding`, `lineEnding`, `indent`) that every real read still produces though the bar does not
    // draw them: no route is left by which the old words could reach the real path.
    const unknownBranch = clone(OVERVIEW);
    unknownBranch.target!.branch = null;
    unknownBranch.target!.detached = false;
    const words = (read: ReturnType<typeof asRead>) => [
      read.diff.format?.visible ?? '',
      read.diff.format?.accessible ?? '',
      read.diff.branch,
      read.diff.branchTitle,
      read.diff.encoding,
      read.diff.lineEnding,
      read.diff.indent,
    ];
    const reads = [
      asRead(scanDiffFor(ROW, openSession(TARGET))),
      asRead(scanDiffFor(ROW, openSession(TARGET, unknownBranch))),
    ];
    for (const text of [
      ...reads.flatMap(words),
      languageWords('bin/blob.bin'),
      languageWords('Makefile.weird'),
    ]) {
      expect(text).not.toMatch(/not determined/i);
    }
    // not vacuous: each of those places does state the unknown
    expect(reads[1] ? words(reads[1]).join(' ') : '').toMatch(/Branch unknown/);
    expect(words(reads[0]!).join(' ')).toMatch(/Format unknown/);
    expect(languageWords('bin/blob.bin')).toMatch(/unknown/);
    expect(words(reads[0]!).join(' ')).toMatch(
      /Encoding unknown.*Line ending unknown.*Indentation unknown/,
    );
  });

  it('the language is by extension; an unknown extension is "unknown", never "plain text"', () => {
    expect(languageWords('src/app.py')).toBe('Python');
    expect(languageWords('notes/readme.txt')).toBe('plain text');
    expect(languageWords('bin/blob.bin')).toBe('language unknown');
    expect(languageWords('Makefile.weird')).toBe('language unknown');
  });
});

describe("the ONE note: whose changes these are, and what the listing does not cover, FROM THE ANSWER'S OWN LIST", () => {
  it('carries the shared-worktree fact and every item the answer says it does not cover', () => {
    const note = noteOf(OVERVIEW.comparison!);
    expect(note).toMatch(/shared worktree/i);
    expect(note).toMatch(/Nothing here says who made a change/);
    for (const id of OVERVIEW.comparison!.doesNotCover)
      expect(note).toContain(DI_DIFF_NOT_COVERED_WORDS[id]!);
  });
  it('cannot drift from the answer: an item the answer adds is named, an item it drops is not', () => {
    const more = clone(OVERVIEW.comparison!);
    more.doesNotCover = ['ignored', 'a_new_exclusion'];
    const note = noteOf(more);
    expect(note).toContain('ignored files');
    expect(note).toContain('a_new_exclusion');
    expect(note).not.toContain('private locations');
  });
  it('names a content filter that was not run, when the answer says so', () => {
    const filtered = clone(OVERVIEW.comparison!);
    filtered.filtersNotRun = ['lfs'];
    expect(noteOf(filtered)).toMatch(/content filters \(lfs\)/);
  });
});

describe('every kind of change and every file state stays DISTINCT, in the mark and in words', () => {
  it('the seven kinds of change have seven distinct letters and seven distinct labels', () => {
    const kinds = [
      'added',
      'modified',
      'deleted',
      'renamed',
      'type_changed',
      'untracked',
      'conflicted',
    ];
    const glyphs = kinds.map((k) => changeGlyph(k));
    expect(new Set(glyphs.map((g) => g.letter)).size).toBe(7);
    expect(new Set(glyphs.map((g) => g.label)).size).toBe(7);
  });

  const { tree } = treeOf(LISTING.files);
  const rowOf = (p: string) => {
    const id = entry(p).fileId;
    return tree.find((t) => t.id === id)!;
  };

  it("the recorded tree carries the real marks with plain labels, and a row's name says the file's state when it is not plain text", () => {
    expect(rowOf('src/app.py')).toMatchObject({
      mark: 'M',
      markLabel: 'modified',
      rowLabel: 'app.py, modified',
    });
    expect(rowOf('docs/gone.md')).toMatchObject({ mark: 'D', markLabel: 'deleted' });
    expect(rowOf('src/moved.py')).toMatchObject({ mark: 'R', markLabel: 'renamed' });
    expect(rowOf('docs/new-guide.md')).toMatchObject({ mark: 'U' });
    expect(rowOf('bin/blob.bin').rowLabel).toBe('blob.bin, modified, binary');
    expect(rowOf('secrets/server.pem').rowLabel).toBe('server.pem, modified, withheld');
    expect(rowOf('legacy/latin1.txt').rowLabel).toBe(
      'latin1.txt, new file, not yet tracked by git, not UTF-8',
    );
    expect(rowOf('generated/bundle.min.js').rowLabel).toContain('too large');
    expect(rowOf('vendor/clone').rowLabel).toContain('not shown');
  });

  it('directories come before files at every level, one row per level', () => {
    const names = tree.filter((t) => t.depth === 0).map((t) => `${t.kind}:${t.name}`);
    const firstFile = names.findIndex((n) => n.startsWith('file:'));
    const lastDir = names.map((n) => n.startsWith('dir:')).lastIndexOf(true);
    expect(lastDir).toBeLessThan(firstFile === -1 ? Infinity : firstFile);
  });

  const withFile = (p: string) =>
    asRead(
      scanDiffFor(
        ROW,
        openSession(
          TARGET,
          OVERVIEW,
          p,
          entry(p).state === 'text' || entry(p).state === 'submodule' ? fileReply(p) : null,
        ),
      ),
    );
  const recordOf = (p: string) => Object.values(withFile(p).diff.files)[0]!;

  it('a non-text file says WHAT it is, with no lines: binary, not UTF-8, too large, withheld, not shown, empty', () => {
    for (const [p, state] of [
      ['bin/blob.bin', 'binary'],
      ['legacy/latin1.txt', 'not_utf8'],
      ['generated/bundle.min.js', 'oversized'],
      ['secrets/server.pem', 'withheld'],
      ['.env', 'withheld'],
      ['vendor/clone', 'unsupported'],
      ['empty.txt', 'empty'],
    ] as const) {
      const record = recordOf(p);
      expect(record.notice?.state, p).toBe(state);
      expect(record.lines, p).toHaveLength(0);
      expect(record.notice?.title.length, p).toBeGreaterThan(3);
    }
  });

  it('a withheld file shows its name and kind of change and NO counts or content', () => {
    const record = recordOf('secrets/server.pem');
    expect(record.lines).toHaveLength(0);
    expect(record.notice?.body).toMatch(/never read or shown/);
  });

  it('a renamed file shows the old and the new path; a deleted file shows its removed lines', () => {
    expect(recordOf('src/moved.py').path).toBe('src/mover.py → src/moved.py');
    const gone = recordOf('docs/gone.md');
    expect(gone.lines.some((l) => l.kind === 'del')).toBe(true);
  });

  it('a submodule shows its pointer lines AND says what a submodule change is', () => {
    const record = recordOf('vendor/lib');
    expect(record.lines.length).toBeGreaterThan(0);
    expect(record.foot).toMatch(/submodule/i);
  });

  it('a text file that was cut short says by how much', () => {
    const record = recordOf('data/large.txt');
    expect(record.foot).toMatch(/Showing the first/);
  });

  it('a file asked for and not yet answered is "reading", not "no changes"', () => {
    const record = Object.values(
      asRead(scanDiffFor(ROW, openSession(TARGET, OVERVIEW, 'src/app.py', null))).diff.files,
    )[0]!;
    expect(record.notice?.state).toBe('reading');
    expect(record.lines).toHaveLength(0);
  });

  it('an answer for a DIFFERENT file than the one selected is not shown under the selected file', () => {
    // select app.py but hand it util.py's answer: the reducer must not apply it, and the mapper must not show it
    const session = openSession(TARGET, OVERVIEW, 'src/app.py', fileReply('src/util.py'));
    const record = Object.values(asRead(scanDiffFor(ROW, session)).diff.files)[0]!;
    expect(record.lines).toHaveLength(0);
    expect(record.notice?.state).toBe('reading');
  });

  it('patch text is INERT: controls and bidirectional overrides are shown as visible placeholders, tabs expanded, clipped text says how much', () => {
    const reply = fileReply('src/app.py');
    const line = reply.file!.hunks[0]!.lines.find((l) => l.kind === 'add')!;
    line.text = 'let a = "x‮y"; \u0007\tend';
    line.clipped = 7;
    const record = Object.values(
      asRead(scanDiffFor(ROW, openSession(TARGET, OVERVIEW, 'src/app.py', reply))).diff.files,
    )[0]!;
    const shown = record.lines.find((l) => l.text.includes('let a'))!;
    expect(shown.text).toContain('⟦U+202E⟧');
    expect(shown.text).toContain('⟦U+0007⟧');
    expect(shown.text).not.toContain('‮');
    expect(shown.text).not.toContain('\t');
    expect(shown.text).toMatch(/7 more characters not shown/);
  });
});

describe("THE SESSION RACE: a verdict decided for the OLD session never lands on the NEW one (a Mission switch closes one and opens another in the same commit, and a parent's effect runs after a child's)", () => {
  const NEW: ReviewTargetRef = { ...TARGET, taskId: 'a-replaced-task', listing: 'e'.repeat(64) };
  /** The old session, closed and a new one opened for NEW, as the stage's effect does. */
  const swapped = (): ReviewSession =>
    reduceSession(reduceSession(openSession(TARGET), { type: 'close' }), {
      type: 'open',
      target: NEW,
    });

  it('a clear decided for the OLD target does not clear the new session (the moved-listing defect)', () => {
    const after = reduceSession(swapped(), {
      type: 'clear',
      reason: 'assignment_changed',
      target: TARGET,
    });
    expect(after.cleared).toBeNull();
    expect(after.target).toEqual(NEW);
  });

  it('an asked decided for the OLD target does not mark the new session as waiting for an answer that will never come', () => {
    const after = reduceSession(swapped(), {
      type: 'asked',
      request: { kind: 'overview' },
      target: TARGET,
    });
    expect(after.pending).toBeNull();
  });

  it('the same events for the CURRENT target still apply, and an event that names no target applies to the current session, as before', () => {
    const current = swapped();
    expect(
      reduceSession(current, { type: 'clear', reason: 'agent_gone', target: NEW }).cleared,
    ).toBe('agent_gone');
    expect(reduceSession(current, { type: 'clear', reason: 'agent_gone' }).cleared).toBe(
      'agent_gone',
    );
    expect(
      reduceSession(current, { type: 'asked', request: { kind: 'overview' }, target: NEW }).pending,
    ).toEqual({ kind: 'overview' });
    expect(
      reduceSession(current, { type: 'asked', request: { kind: 'overview' } }).pending,
    ).toEqual({ kind: 'overview' });
  });

  it('an event decided for a session that no longer exists changes nothing at all (a closed session stays closed)', () => {
    const closed = reduceSession(openSession(TARGET), { type: 'close' });
    expect(reduceSession(closed, { type: 'clear', reason: 'agent_gone', target: TARGET })).toEqual(
      closed,
    );
    expect(
      reduceSession(closed, { type: 'asked', request: { kind: 'overview' }, target: TARGET }),
    ).toEqual(closed);
  });
});

// ─────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────
// CANONICAL ROUND 15, blocker 1: THE FILE READ IS THE LATER OBSERVATION. The overview lists each file with a state it GUESSED from the inventory;
// the file read, made afterwards, observes the file itself. When the two disagree the read wins, and the panel says what the read observed and why
// there is no content: it never keeps drawing the overview's confident word ("Text") over a read that learned something that contradicts it.
describe("A BETTER OBSERVATION ARRIVES: what the file read reports beats the overview's earlier guess, and the panel says it", () => {
  const TEXT_PATH = 'src/util.py'; // listed as text in the overview
  /** The recorded read of TEXT_PATH, with the file's OWN state replaced by what a later read observed (and nothing to draw). */
  const readThatObserved = (state: string, reason: string | null = null): DiCodeReview => {
    const reply = fileReply(TEXT_PATH);
    reply.file = {
      ...reply.file!,
      state,
      reason,
      hunks: [],
      additions: null,
      deletions: null,
      linesTotal: 0,
      linesRetained: 0,
      truncated: false,
      bytesTotal: 0,
      digestSha256: null,
    };
    return reply;
  };
  const drawn = (reply: DiCodeReview) => {
    const read = asRead(scanDiffFor(ROW, openSession(TARGET, OVERVIEW, TEXT_PATH, reply)));
    return Object.values(read.diff.files)[0]!;
  };

  it('the overview says TEXT, the read says NOT UTF-8: the notice says what the read observed and why there is no content', () => {
    expect(entry(TEXT_PATH).state).toBe('text'); // the guess
    const file = drawn(readThatObserved('not_utf8'));
    expect(file.lines).toEqual([]);
    expect(file.notice?.state).toBe('not_utf8');
    expect(file.notice?.title).toBe('Not valid UTF-8 text');
    expect(file.notice?.body).toMatch(/not valid UTF-8/);
    expect(file.notice?.title).not.toBe('Text'); // never the unexplained word over a read that contradicts it
  });

  it.each([
    ['binary', null, 'Binary file'],
    ['oversized', null, 'Too large to read here'],
    ['withheld', 'private_location', 'Withheld'],
    ['no_content_diff', null, 'No change to the text'],
  ] as const)(
    "the overview says TEXT, the read says %s: the notice is the read's",
    (state, reason, title) => {
      const file = drawn(readThatObserved(state, reason));
      expect(file.lines).toEqual([]);
      expect(file.notice?.state).toBe(state);
      expect(file.notice?.title).toBe(title);
    },
  );

  it("the read's own state decides the foot too: a read that observed a SUBMODULE says what a submodule change is", () => {
    const reply = fileReply(TEXT_PATH);
    reply.file = { ...reply.file!, state: 'submodule' };
    const file = drawn(reply);
    expect(file.lines.length).toBeGreaterThan(0); // its pointer lines
    expect(file.foot).toMatch(/submodule/i);
  });

  it('CONTROL: a read that agrees with the overview (text, with lines) still draws its lines and no notice', () => {
    const file = drawn(fileReply(TEXT_PATH));
    expect(file.lines.length).toBeGreaterThan(0);
    expect(file.notice).toBeUndefined();
  });
});

// CANONICAL ROUND 15, blocker 2: A REFUSAL BELONGS TO THE SELECTION IT ANSWERED. A refusal for selection A that arrives after the reader moved
// to B used to be STORED before the selection was checked; the stored refusal then blocked B's request and B's face said "reading" with nothing
// in flight, a word that was false and never cleared. The selection is checked BEFORE a refusal is stored, and the test proves what that
// unblocks: B is asked, and B LOADS (a fix that only discarded the refusal but left B idle would pass a test that stopped earlier).
describe('A REFUSAL for a selection that is no longer selected is discarded, and the selection that is selected LOADS', () => {
  const A_PATH = 'src/util.py';
  const B_PATH = 'styles/theme.css';
  /** The overview read; A selected and asked (its read in flight); then the reader selects B. */
  const movedOnWhileAIsInFlight = (): ReviewSession => {
    let s = openSession(TARGET, OVERVIEW);
    s = reduceSession(s, { type: 'select', path: A_PATH });
    const asked = nextRequest(s);
    expect(asked).toEqual({
      kind: 'file',
      fileId: entry(A_PATH).fileId,
      snapshot: OVERVIEW.snapshot,
      path: A_PATH,
    });
    s = reduceSession(s, { type: 'asked', request: asked! });
    s = reduceSession(s, { type: 'select', path: B_PATH });
    expect(nextRequest(s)).toBeNull(); // A is still in flight: nothing more is asked yet
    return s;
  };
  const refusalFor = (p: string, problem: string): DiCodeReview => ({
    ...fileReply(p),
    state: 'refused',
    problem,
    detail: 'a synthetic refusal for this test',
    target: null,
    comparison: null,
    overview: null,
    file: null,
    observedAt: null,
  });

  it('a NON-RETRYABLE refusal for A, arriving after B was selected, is discarded: nothing is stored, nothing is pending, and B is the next request', () => {
    const after = reduceSession(movedOnWhileAIsInFlight(), {
      type: 'answer',
      answer: refusalFor(A_PATH, 'file_diff_over_bound'),
      nowMs: 0,
    });
    expect(after.refusal).toBeNull();
    expect(after.pending).toBeNull();
    expect(after.selectedPath).toBe(B_PATH);
    expect(nextRequest(after)).toEqual({
      kind: 'file',
      fileId: entry(B_PATH).fileId,
      snapshot: OVERVIEW.snapshot,
      path: B_PATH,
    });
  });

  it('AND B LOADS: after the discard, B is asked, B\'s answer arrives, and B\'s own lines are drawn (the face never says "reading" with nothing in flight)', () => {
    let s = reduceSession(movedOnWhileAIsInFlight(), {
      type: 'answer',
      answer: refusalFor(A_PATH, 'file_diff_over_bound'),
      nowMs: 0,
    });
    const asked = nextRequest(s);
    expect(asked).not.toBeNull(); // the stuck state was: reading, with no request pending and none to make
    s = reduceSession(s, { type: 'asked', request: asked! });
    s = reduceSession(s, { type: 'answer', answer: fileReply(B_PATH), nowMs: 0 });
    expect(s.file?.answer.fileId).toBe(entry(B_PATH).fileId);
    expect(s.refusal).toBeNull();
    const file = Object.values(asRead(scanDiffFor(ROW, s)).diff.files)[0]!;
    expect(file.path).toBe(B_PATH);
    expect(file.lines.length).toBeGreaterThan(0);
    expect(file.notice).toBeUndefined();
  });

  it('a RETRYABLE refusal for A arriving after B was selected is discarded the same way (B is asked; nothing waits on a retry of A)', () => {
    const after = reduceSession(movedOnWhileAIsInFlight(), {
      type: 'answer',
      answer: refusalFor(A_PATH, 'read_rate_limited'),
      nowMs: 0,
    });
    expect(after.refusal).toBeNull();
    expect(nextRequest(after)).toEqual({
      kind: 'file',
      fileId: entry(B_PATH).fileId,
      snapshot: OVERVIEW.snapshot,
      path: B_PATH,
    });
  });

  // ROUND 16, blocker 1. "This snapshot has moved" is a property of the SNAPSHOT; "this request was refused" is a property of a REQUEST that no longer
  // exists. A dead selection's refusal must not be STORED (it would block the live selection's request), but what it says about the snapshot must be
  // KEPT AT ONCE. The earlier version of this describe argued the staleness would be learned again from the next selection's own refusal; that holds only
  // where the next selection is READ, and a selection that needs no read (binary, withheld, not UTF-8, oversized, unsupported, empty, or none) never
  // produces a refusal, so the warning was lost. The old assertion `stale === false` encoded that mistake and is replaced by `stale === true`.
  it('a snapshot_moved refusal for the dead selection marks the SNAPSHOT stale at once and stores nothing: B, when openable, is still asked and still loads', () => {
    let s = reduceSession(movedOnWhileAIsInFlight(), {
      type: 'answer',
      answer: refusalFor(A_PATH, 'snapshot_moved'),
      nowMs: 0,
    });
    expect(s.refusal).toBeNull(); // nothing that blocks B is stored
    expect(s.stale).toBe(true); // what the refusal said about the snapshot is kept
    const asked = nextRequest(s);
    expect(asked).toEqual({
      kind: 'file',
      fileId: entry(B_PATH).fileId,
      snapshot: OVERVIEW.snapshot,
      path: B_PATH,
    });
    s = reduceSession(s, { type: 'asked', request: asked! });
    s = reduceSession(s, {
      type: 'answer',
      answer: refusalFor(B_PATH, 'snapshot_moved'),
      nowMs: 0,
    });
    expect(s.refusal?.problem).toBe('snapshot_moved'); // B's own refusal is for the selection that IS selected: stored
    expect(s.stale).toBe(true);
  });

  // THE CASES WHERE A LATER REFUSAL CAN NEVER ARRIVE, enumerated (the states `canOpen` refuses, plus no selection at all): the staleness must survive each.
  describe.each([['snapshot_moved'], ['workspace_moved_during_read']] as const)(
    '%s for the dead selection, when the selection that replaced it needs NO read',
    (problem) => {
      const NON_OPENABLE: Array<[string, string | null]> = [
        ['withheld', '.env'],
        ['binary', 'bin/blob.bin'],
        ['not_utf8', 'legacy/latin1.txt'],
        ['oversized', 'generated/bundle.min.js'],
        ['unsupported', 'vendor/clone'],
        ['empty', 'empty.txt'],
        ['no selection at all', null],
      ];
      it.each(NON_OPENABLE)(
        'B is %s: the listing is marked stale, nothing is stored, nothing is asked, and the face says what B is (never "reading")',
        (_what, bPath) => {
          let s = openSession(TARGET, OVERVIEW);
          s = reduceSession(s, { type: 'select', path: A_PATH });
          s = reduceSession(s, { type: 'asked', request: nextRequest(s)! });
          s = reduceSession(s, { type: 'select', path: bPath });
          const after = reduceSession(s, {
            type: 'answer',
            answer: refusalFor(A_PATH, problem),
            nowMs: 0,
          });
          expect(after.stale).toBe(true);
          expect(after.refusal).toBeNull();
          expect(after.pending).toBeNull();
          expect(nextRequest(after)).toBeNull(); // B needs no read, and none is owed
          const read = asRead(scanDiffFor(ROW, after));
          expect(read.diff.tag).toMatch(/changed since it was read/);
          if (bPath !== null) {
            const file = Object.values(read.diff.files)[0]!;
            expect(file.notice?.state).toBe(entry(bPath).state); // the overview's own state: B was never read
            expect(file.notice?.state).not.toBe('reading');
          }
        },
      );
    },
  );

  it("CONTROL: a refusal for the file that is STILL selected is stored, stated in the route's words, and blocks a request for nothing else", () => {
    let s = openSession(TARGET, OVERVIEW);
    s = reduceSession(s, { type: 'select', path: A_PATH });
    s = reduceSession(s, { type: 'asked', request: nextRequest(s)! });
    s = reduceSession(s, {
      type: 'answer',
      answer: refusalFor(A_PATH, 'file_diff_over_bound'),
      nowMs: 0,
    });
    expect(s.refusal?.problem).toBe('file_diff_over_bound');
    expect(nextRequest(s)).toBeNull(); // not retried: it will not change by waiting
    const file = Object.values(asRead(scanDiffFor(ROW, s)).diff.files)[0]!;
    expect(file.notice?.state).toBe('refused');
  });
});

// WHERE THE READ HAS NO LINES TO DRAW (the lead's reachability question after canonical round 15). TRACED in the real route (target_runtime/code_review.py):
// a TRACKED change whose counts are 0 and 0 (a mode-only change, a pure rename) is listed as `no_content_diff` in the OVERVIEW itself (`_tracked_state`), and the
// file read returns that same state with no hunks (`state=STATE_NO_CONTENT`, with the mode change); a read that finds no hunks and no truncation is `no_content_diff`
// too. The route's own test pins ("no_content_diff", [], {"old": "100644", "new": "100755"}). So the REAL mode-only emission is covered below, and a `text` read
// that returns NO lines is a state the route cannot emit: it is pinned as a DEFENSIVE PATH (like "Branch unknown"), in words that say what was observed.
describe('A READ WITH NO LINES TO DRAW: the real mode-only emission says why there is no body; a text read with no lines (a state the route cannot emit) says so', () => {
  const PATH = 'src/util.py';
  const modeOnlyRead = (): DiCodeReview => {
    const reply = fileReply(PATH);
    reply.file = {
      ...reply.file!,
      state: 'no_content_diff',
      reason: null,
      hunks: [],
      additions: 0,
      deletions: 0,
      truncated: false,
      linesTotal: 0,
      linesRetained: 0,
      bytesTotal: 0,
      digestSha256: null,
      modeChange: { old: '100644', new: '100755' },
    };
    return reply;
  };
  const drawn = (overview: DiCodeReview, reply: DiCodeReview) =>
    Object.values(
      asRead(scanDiffFor(ROW, openSession(TARGET, overview, PATH, reply))).diff.files,
    )[0]!;

  it('the REAL emission: the overview lists the mode-only change as no_content_diff and the read agrees: no line differs, and it says so (never the bare "Text")', () => {
    const overview = clone(OVERVIEW);
    const listed = overview.overview!.files.find((f) => f.path === PATH)!;
    Object.assign(listed, { state: 'no_content_diff', additions: 0, deletions: 0 });
    const file = drawn(overview, modeOnlyRead());
    expect(file.lines).toEqual([]);
    expect(file.notice?.state).toBe('no_content_diff');
    expect(file.notice?.title).toBe('No change to the text');
    expect(file.notice?.body).toMatch(/no line differs/);
    expect(file.notice?.body).toMatch(/permissions/);
  });

  it('DEFENSIVE PATH: a read that observed TEXT and returned NO lines (the route cannot emit this) says exactly that, and is never the bare, unexplained "Text"', () => {
    const reply = fileReply(PATH);
    reply.file = {
      ...reply.file!,
      state: 'text',
      hunks: [],
      additions: 0,
      deletions: 0,
      truncated: false,
      linesTotal: 0,
      linesRetained: 0,
    };
    const file = drawn(OVERVIEW, reply);
    expect(file.lines).toEqual([]);
    expect(file.notice?.state).toBe('text');
    expect(file.notice?.title).toBe('No lines to show');
    expect(file.notice?.body).toMatch(/no changed lines/);
    expect(file.notice?.title).not.toBe('Text');
  });
});

// ─────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────
// CANONICAL ROUND 16, blocker 2: A READ BELONGS TO THE REQUEST THAT ASKED FOR IT. IDENTICAL REFERENCES ARE A DESCRIPTION, NOT AN IDENTITY. Close a Code
// Diff while its read is in flight and reopen the SAME offer: the old and the new reads name the same Mission, member, task and listing, so the reducer could
// not tell them apart, and the OLD overview arriving first fulfilled the NEW request while the fresh result was thrown away: the reader saw an older reading of
// the workspace as the current one. Every read now NAMES itself (an opaque `requestId`, chosen by the client and echoed by the office), and an answer fulfils
// a request only if it names that request. THE RULE, stated and pinned: if a request CARRIED an id, an answer LACKING one (or naming another) must NOT fulfil
// it; absence never falls back to matching on the reference, or the defect would return by the back door while every test still passed.
describe('A READ BELONGS TO THE REQUEST THAT ASKED FOR IT: identical references are not an identity', () => {
  const withId = (reply: DiCodeReview, requestId?: string): DiCodeReview =>
    requestId === undefined ? reply : { ...reply, requestId };
  const overviewOf = (branch: string): DiCodeReview => {
    const o = clone(OVERVIEW);
    o.target!.branch = branch;
    return o;
  };
  /** Ask for what the session needs next, naming the request (or not: a client that sends no id). */
  const askedAs = (s: ReviewSession, id?: string): ReviewSession =>
    reduceSession(s, {
      type: 'asked',
      request: nextRequest(s)!,
      ...(id === undefined ? {} : { id }),
    });
  const fresh = (): ReviewSession =>
    reduceSession(CLOSED_SESSION, { type: 'open', target: TARGET });

  it('THE REQUIRED SEQUENCE: an overview is held, the Code Diff is closed and reopened (the SAME offer), the OLD overview arrives FIRST, then the fresh result: the old one is not taken for the fresh read, and the fresh one is the one shown', () => {
    let s = askedAs(fresh(), 'r1'); // the first session's read is in flight
    s = reduceSession(s, { type: 'close' });
    s = reduceSession(s, { type: 'open', target: TARGET }); // the same offer, reopened
    s = askedAs(s, 'r2'); // the new session's read
    expect(s.pending).toEqual({ kind: 'overview' });
    s = reduceSession(s, {
      type: 'answer',
      answer: withId(overviewOf('old-branch'), 'r1'),
      nowMs: 0,
    }); // the OLD one first
    expect(s.overview).toBeNull(); // it did not fulfil the fresh request
    expect(s.pending).toEqual({ kind: 'overview' }); // the fresh request is still waiting for its own answer
    expect(scanDiffFor(ROW, s).kind).toBe('reading'); // never drawn as a read
    s = reduceSession(s, { type: 'answer', answer: withId(overviewOf('main'), 'r2'), nowMs: 0 }); // then the fresh result
    expect(s.overview?.answer.target?.branch).toBe('main');
    expect(asRead(scanDiffFor(ROW, s)).diff.branch).toBe('main'); // the fresh one is what the reader sees
  });

  it("THE TRACED REAL FLOW: the reopened session's read is refused AT ONCE (read_in_progress, naming its OWN id), it is asked again, and the OLD read's answer arrives while that retry is in flight: it is not taken for the retry, and the retry's own answer is the one shown", () => {
    let s = askedAs(fresh(), 'r1'); // the first session's read: outstanding at the office
    s = reduceSession(s, { type: 'close' });
    s = reduceSession(s, { type: 'open', target: TARGET }); // the same offer, reopened
    s = askedAs(s, 'r2');
    const busy: DiCodeReview = {
      ...OVERVIEW,
      state: 'refused',
      problem: 'read_in_progress',
      detail: 'another review read is in progress',
      target: null,
      comparison: null,
      overview: null,
      file: null,
      observedAt: null,
      snapshot: null,
    };
    s = reduceSession(s, { type: 'answer', answer: withId(busy, 'r2'), nowMs: 0 }); // the refusal arrives FIRST and names the read it refused
    expect(isRetryable(s.refusal)).toBe(true);
    s = reduceSession(s, { type: 'retry' });
    s = askedAs(s, 'r3'); // the retry, with an id of its own
    expect(s.pendingId).toBe('r3');
    s = reduceSession(s, {
      type: 'answer',
      answer: withId(overviewOf('old-branch'), 'r1'),
      nowMs: 0,
    }); // the old read's answer, during the retry's flight
    expect(s.overview).toBeNull();
    expect(s.pending).toEqual({ kind: 'overview' });
    s = reduceSession(s, { type: 'answer', answer: withId(overviewOf('main'), 'r3'), nowMs: 0 });
    expect(asRead(scanDiffFor(ROW, s)).diff.branch).toBe('main');
  });

  it('a refusal that names ANOTHER read is not taken for the refusal of the request in flight (it would start a retry of nothing and hide the real wait)', () => {
    const s = askedAs(fresh(), 'r3');
    const busy: DiCodeReview = {
      ...OVERVIEW,
      state: 'refused',
      problem: 'read_in_progress',
      detail: 'another review read is in progress',
      target: null,
      comparison: null,
      overview: null,
      file: null,
      observedAt: null,
      snapshot: null,
    };
    const after = reduceSession(s, { type: 'answer', answer: withId(busy, 'r2'), nowMs: 0 });
    expect(after.refusal).toBeNull();
    expect(after.pending).toEqual({ kind: 'overview' });
  });

  it('an answer that names ANOTHER request is rejected (a positive test of a mismatched id, not only of a missing one)', () => {
    const s = askedAs(fresh(), 'r2');
    const after = reduceSession(s, { type: 'answer', answer: withId(OVERVIEW, 'r9'), nowMs: 0 });
    expect(after.overview).toBeNull();
    expect(after.pending).toEqual({ kind: 'overview' });
  });

  it('THE RULE: a request that CARRIED an id is NOT fulfilled by an answer that LACKS one (absent never falls back to matching on the reference)', () => {
    const s = askedAs(fresh(), 'r2');
    const after = reduceSession(s, {
      type: 'answer',
      answer: OVERVIEW /* recorded: no requestId */,
      nowMs: 0,
    });
    expect(after.overview).toBeNull();
    expect(after.pending).toEqual({ kind: 'overview' });
  });

  it('and the converse: a request that carried NO id is not fulfilled by an answer that names one (it answers some other request)', () => {
    const s = askedAs(fresh());
    const after = reduceSession(s, { type: 'answer', answer: withId(OVERVIEW, 'r1'), nowMs: 0 });
    expect(after.overview).toBeNull();
    expect(after.pending).toEqual({ kind: 'overview' });
  });

  it('REPLAYING THE RECORDED FIXTURE, which has no id anywhere, still works end to end when the requests carry none (a client that sends no id is served as before)', () => {
    let s = askedAs(fresh());
    s = reduceSession(s, { type: 'answer', answer: OVERVIEW, nowMs: 0 });
    expect(s.overview?.answer.snapshot).toBe(OVERVIEW.snapshot);
    s = reduceSession(s, { type: 'select', path: 'src/util.py' });
    s = askedAs(s);
    s = reduceSession(s, { type: 'answer', answer: fileReply('src/util.py'), nowMs: 0 });
    expect(s.file?.answer.fileId).toBe(entry('src/util.py').fileId);
    expect(Object.values(asRead(scanDiffFor(ROW, s)).diff.files)[0]!.lines.length).toBeGreaterThan(
      0,
    );
  });

  it("a FILE read is correlated the same way: a held file answer of a closed session does not fulfil the reopened session's file request", () => {
    const open = (): ReviewSession => {
      let s = askedAs(fresh(), 'r1');
      s = reduceSession(s, { type: 'answer', answer: withId(OVERVIEW, 'r1'), nowMs: 0 });
      return reduceSession(s, { type: 'select', path: 'src/util.py' });
    };
    let s = askedAs(open(), 'r2'); // the file read is in flight
    s = reduceSession(s, { type: 'close' });
    s = reduceSession(s, { type: 'open', target: TARGET });
    s = askedAs(s, 'r3');
    s = reduceSession(s, { type: 'answer', answer: withId(OVERVIEW, 'r3'), nowMs: 0 });
    s = reduceSession(s, { type: 'select', path: 'src/util.py' });
    s = askedAs(s, 'r4'); // the reopened session's file request
    s = reduceSession(s, {
      type: 'answer',
      answer: withId(fileReply('src/util.py'), 'r2'),
      nowMs: 0,
    }); // the OLD file answer first
    expect(s.file).toBeNull();
    expect(s.pending?.kind).toBe('file');
    s = reduceSession(s, {
      type: 'answer',
      answer: withId(fileReply('src/util.py'), 'r4'),
      nowMs: 0,
    });
    expect(s.file?.answer.fileId).toBe(entry('src/util.py').fileId);
  });
});

// Supervisor additions to round 16. (1) The id is checked IN ADDITION to the reference, never instead of it: the right id on the WRONG reference is refused.
// (2) The ids are DISTINCT ACROSS THE WHOLE LIFETIME (close, reopen, an outstanding read): if they restarted when a session reopened, a new read could be
// stamped with an id that a still-outstanding read of the previous session already carries, and the held old answer would match the new request perfectly:
// the same defect, moved from the reference layer to the id layer.
describe('THE ID IS CHECKED IN ADDITION TO THE REFERENCE, AND IDS ARE UNIQUE ACROSS THE WHOLE LIFETIME', () => {
  const open = (): ReviewSession => reduceSession(CLOSED_SESSION, { type: 'open', target: TARGET });
  const askedAs = (s: ReviewSession, id: string): ReviewSession =>
    reduceSession(s, { type: 'asked', request: nextRequest(s)!, id });

  it('the RIGHT id on the WRONG reference is refused (every existing reference check still holds on top of the id)', () => {
    const s = askedAs(open(), 'r2');
    for (const wrong of [
      { taskId: 'a-replaced-task' },
      { listing: 'e'.repeat(64) },
      { missionRef: 'mc-1:mn-' + 'f'.repeat(32) },
      { memberRef: 'another-member' },
    ]) {
      const after = reduceSession(s, {
        type: 'answer',
        answer: { ...OVERVIEW, ...wrong, requestId: 'r2' },
        nowMs: 0,
      });
      expect(after.overview, JSON.stringify(wrong)).toBeNull();
      expect(after.pending).toEqual({ kind: 'overview' });
    }
  });

  it('CONTROL: the right id on the right reference is accepted (so the refusals above are about the reference, not about every answer being refused)', () => {
    const after = reduceSession(askedAs(open(), 'r2'), {
      type: 'answer',
      answer: { ...OVERVIEW, requestId: 'r2' },
      nowMs: 0,
    });
    expect(after.overview).not.toBeNull();
    expect(after.pending).toBeNull();
  });

  it('an allocator never repeats an id, and two allocators with different tags never collide', () => {
    const next = createRequestIds('a');
    const seen = new Set<string>();
    for (let i = 0; i < 500; i++) seen.add(next());
    expect(seen.size).toBe(500);
    for (const id of seen) expect(id).toMatch(/^[A-Za-z0-9._-]{1,64}$/);
    const other = createRequestIds('b');
    for (let i = 0; i < 50; i++) expect(seen.has(other())).toBe(false);
  });

  it("REOPEN WITH A READ STILL OUTSTANDING: the new session's id differs from the outstanding one, so the held old answer cannot match the new request", () => {
    const next = createRequestIds('t');
    const oldId = next();
    let s = askedAs(open(), oldId); // the first session's read: outstanding
    s = reduceSession(s, { type: 'close' });
    s = reduceSession(s, { type: 'open', target: TARGET });
    const newId = next(); // what the hook allocates for the reopened session's read
    expect(newId).not.toBe(oldId);
    s = askedAs(s, newId);
    s = reduceSession(s, { type: 'answer', answer: { ...OVERVIEW, requestId: oldId }, nowMs: 0 }); // the held old answer, same reference
    expect(s.overview).toBeNull();
    expect(s.pending).toEqual({ kind: 'overview' });
  });
});

// ─────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────
// CANONICAL ROUND 17: the answers reach the session through a FEED that keeps nothing and delivers every answer, in arrival order, to whoever is subscribed. It
// replaces a latest-value state slot, in which two answers handled before a render kept only the last (the refusal that mattered could be overwritten by a stale
// answer that the id then refused). The feed's own correctness does not depend on how a render is batched.
describe('THE ANSWER FEED delivers every answer, in order, and keeps nothing', () => {
  const answerNamed = (requestId: string): DiCodeReview => ({ ...OVERVIEW, requestId });

  it('two answers published in the same tick BOTH reach the subscriber, in the order they arrived (a latest-value slot would keep only the second)', () => {
    const feed = createDiCodeReviewFeed();
    const seen: Array<string | undefined> = [];
    feed.subscribe((a) => seen.push(a.requestId));
    feed.publish(answerNamed('current'));
    feed.publish(answerNamed('stale'));
    expect(seen).toEqual(['current', 'stale']);
  });

  it('a subscriber that has unsubscribed receives nothing more', () => {
    const feed = createDiCodeReviewFeed();
    const seen: string[] = [];
    const off = feed.subscribe((a) => seen.push(String(a.requestId)));
    feed.publish(answerNamed('one'));
    off();
    feed.publish(answerNamed('two'));
    expect(seen).toEqual(['one']);
  });

  it('every subscriber receives every answer, and one that unsubscribes while an answer is being delivered does not make another miss it', () => {
    const feed = createDiCodeReviewFeed();
    const first: string[] = [];
    const second: string[] = [];
    const offFirst = feed.subscribe((a) => {
      first.push(String(a.requestId));
      offFirst();
    });
    feed.subscribe((a) => second.push(String(a.requestId)));
    feed.publish(answerNamed('a'));
    feed.publish(answerNamed('b'));
    expect(first).toEqual(['a']);
    expect(second).toEqual(['a', 'b']);
  });

  it('a subscriber that an EARLIER subscriber of the same delivery unsubscribed receives nothing from that delivery', () => {
    const feed = createDiCodeReviewFeed();
    const late: string[] = [];
    let offLate: () => void = () => undefined;
    feed.subscribe(() => offLate());
    offLate = feed.subscribe((a) => late.push(String(a.requestId)));
    feed.publish(answerNamed('a'));
    expect(late).toEqual([]);
  });

  it('the feed keeps NOTHING: an answer published before anyone subscribed is never replayed to a later subscriber', () => {
    const feed = createDiCodeReviewFeed();
    feed.publish(answerNamed('before'));
    const seen: string[] = [];
    feed.subscribe((a) => seen.push(String(a.requestId)));
    expect(seen).toEqual([]);
  });
});
