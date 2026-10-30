/**
 * I3 — the live code review of a FINISHED (COMPLETED) Mission, webview model.
 *
 * Over the REAL office states the server projects for the emitted COMPLETED Mission
 * (`test/fixtures/di-office-state-evidence-historical-v2.FIXTURE.json`, written by `server/__tests__/diOfficeStateExport.test.ts` from the backend's
 * hash-pinned historical export) and the recorded active-review answers (`di-office-state-code-review-v2.FIXTURE.json`). Pure: no browser.
 *
 * Proves: the Mission-level offer reaches the scan row as a DISTINCT reference kind (no member, `level: 'mission'`) and the member-bound one keeps
 * exactly the keys it always had; a member's offer still wins; the wire message of each kind is exactly its own; an answer is applied only to the read
 * it answers (a member's answer never lands on a Mission-level read, nor the reverse); the session is cleared in its own words when the Mission's offer
 * moves or goes; every new reason and refusal code has words; and a Mission-level session is shown only for the Mission and the reference the office
 * offers now.
 */
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

import { describe, expect, it } from 'vitest';

import type { DiCodeReview, DiMission, DiOfficeState } from '../../core/src/messages.ts';
import type { ReviewTargetRef } from '../src/components/diCodeReviewModel.ts';
import * as model from '../src/components/diCodeReviewModel.ts';
import { scanDiffFor } from '../src/components/diScanDiffRead.ts';
import { reviewOfferOf, scanRowsFromOffice } from '../src/components/diScanModel.ts';
import { OVERBROAD_REFERENT } from './diFinishedWordingClaims.ts';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const readJson = <T,>(file: string): T => JSON.parse(fs.readFileSync(path.join(root, 'test/fixtures', file), 'utf-8')) as T;
const clone = <T,>(value: T): T => structuredClone(value);

const historical = readJson<{ states: Record<'live' | 'moved' | 'stale', DiOfficeState> }>('di-office-state-evidence-historical-v2.FIXTURE.json');
const active = readJson<{
  reference: { missionRef: string; memberRef: string; taskId: string; listing: string };
  states: Record<string, DiOfficeState>;
  replies: { overview: DiCodeReview };
}>('di-office-state-code-review-v2.FIXTURE.json');

const finishedOf = (state: DiOfficeState): DiMission => state.missions.missions.find((m) => m.sourceKind === 'mission_core')!;
const FINISHED = finishedOf(historical.states.live);
const OFFER = FINISHED.codeReview!;
const MISSION_TARGET: ReviewTargetRef = {
  missionRef: FINISHED.missionRef,
  memberRef: '',
  taskId: OFFER.taskId!,
  listing: OFFER.listing!,
  agentName: 'this Mission',
  level: 'mission',
};
const MEMBER_TARGET: ReviewTargetRef = { ...active.reference, agentName: 'Sam' };

/** The recorded overview answer re-addressed to a target (the bytes of the answer are the fixture's; only the reference it echoes is replaced). */
const answerFor = (target: ReviewTargetRef, requestId?: string): DiCodeReview => {
  const answer = clone(active.replies.overview);
  Object.assign(answer, { missionRef: target.missionRef, memberRef: target.memberRef, taskId: target.taskId, listing: target.listing });
  if (requestId !== undefined) answer.requestId = requestId;
  return answer;
};

function sessionFor(target: ReviewTargetRef, answer: DiCodeReview | null, id = 'w.1') {
  let s = model.reduceSession(model.CLOSED_SESSION, { type: 'open', target });
  s = model.reduceSession(s, { type: 'asked', request: { kind: 'overview' }, target, id });
  if (answer !== null) s = model.reduceSession(s, { type: 'answer', answer, nowMs: 0 });
  return s;
}

describe('the Mission-level offer reaches the scan row as a distinct reference kind', () => {
  it('the emitted COMPLETED Mission is offered by its own reference: no member, level mission, and the task and listing the office gave', () => {
    expect(OFFER).toMatchObject({ state: 'offered', reason: null });
    const row = scanRowsFromOffice(historical.states.live).find((r) => r.id === FINISHED.missionRef)!;
    expect(row.reviewOffer).toEqual({ kind: 'offered', target: MISSION_TARGET });
    expect(row.reviewOffered).toBe(true);
  });

  it('the member-bound reference keeps exactly the keys it always had (no level key at all)', () => {
    const row = scanRowsFromOffice(active.states.live)[0]!;
    expect(row.reviewOffer.kind).toBe('offered');
    const target = (row.reviewOffer as { kind: 'offered'; target: ReviewTargetRef }).target;
    expect(Object.keys(target).sort()).toEqual(['agentName', 'listing', 'memberRef', 'missionRef', 'taskId']);
    expect(target.memberRef).not.toBe('');
  });

  it('a member that is offered wins over the Mission\'s own offer', () => {
    const both = clone(active.states.live);
    const mission = both.missions.missions[0]!;
    mission.codeReview = { state: 'offered', reason: null, taskId: mission.members.members[0]!.codeReview!.taskId, listing: 'f'.repeat(64) };
    const offer = reviewOfferOf(mission);
    expect(offer.kind).toBe('offered');
    expect((offer as { target: ReviewTargetRef }).target.memberRef).toBe(mission.members.members[0]!.memberRef);
    expect('level' in (offer as { target: ReviewTargetRef }).target).toBe(false);
  });

  it('a finished Mission whose execution the office does not list is unavailable in its own reason; the stale office says so too', () => {
    const stale = scanRowsFromOffice(historical.states.stale).find((r) => r.id === FINISHED.missionRef)!;
    expect(stale.reviewOffer).toEqual({ kind: 'unavailable', reason: 'finished_execution_not_listed' });
    expect(stale.reviewOffered).toBe(false);
  });

  it('says nothing the office did not say: an unavailable Mission-level reason that members already carry (or that does not apply) changes nothing', () => {
    // work in progress: the Mission-level reason is "not finished", which says nothing the row should show
    const inactive = scanRowsFromOffice(active.states.inactive)[0]!;
    expect(inactive.reviewOffer).toEqual({ kind: 'unavailable', reason: 'assignment_not_active' });
    // a plain Herdr task: no members, and its Mission-level reason is the shared one: nothing is claimed, as before
    const plain = scanRowsFromOffice(historical.states.live).find((r) => r.id !== FINISHED.missionRef)!;
    expect(plain.reviewOffer).toEqual({ kind: 'unknown' });
  });

  it('a finished-specific reason outranks a member-level one', () => {
    const state = clone(historical.states.live);
    const mission = finishedOf(state);
    mission.codeReview = { state: 'unavailable', reason: 'finished_execution_not_listed', taskId: null, listing: null };
    mission.members = { state: 'observed', reason: null, members: [clone(active.states.live.missions.missions[0]!.members.members[0]!)] };
    mission.members.members[0]!.codeReview = { state: 'unavailable', reason: 'assignment_not_active', taskId: null, listing: null };
    expect(reviewOfferOf(mission)).toEqual({ kind: 'unavailable', reason: 'finished_execution_not_listed' });
  });
});

describe('the wire message of each kind is exactly its own', () => {
  it('a Mission-level read names the Mission, the task and the listing: no member key', () => {
    const overview = model.reviewReadMessage(MISSION_TARGET, { kind: 'overview' }, 'w.1');
    expect(overview).toEqual({
      type: 'diCodeReviewMissionRead',
      missionRef: MISSION_TARGET.missionRef,
      taskId: MISSION_TARGET.taskId,
      listing: MISSION_TARGET.listing,
      requestId: 'w.1',
    });
    expect('memberRef' in overview).toBe(false);
    const file = model.reviewReadMessage(MISSION_TARGET, { kind: 'file', fileId: 'rf-x', snapshot: 's', path: 'p' });
    expect(file).toEqual({
      type: 'diCodeReviewMissionRead',
      missionRef: MISSION_TARGET.missionRef,
      taskId: MISSION_TARGET.taskId,
      listing: MISSION_TARGET.listing,
      snapshot: 's',
      fileId: 'rf-x',
    });
  });

  it('a member-bound read is byte-identical to what it always was', () => {
    expect(model.reviewReadMessage(MEMBER_TARGET, { kind: 'overview' }, 'w.2')).toEqual({
      type: 'diCodeReviewRead',
      missionRef: MEMBER_TARGET.missionRef,
      memberRef: MEMBER_TARGET.memberRef,
      taskId: MEMBER_TARGET.taskId,
      listing: MEMBER_TARGET.listing,
      requestId: 'w.2',
    });
    expect(model.reviewReadMessage(MEMBER_TARGET, { kind: 'file', fileId: 'rf-x', snapshot: 's', path: 'p' })).toEqual({
      type: 'diCodeReviewRead',
      missionRef: MEMBER_TARGET.missionRef,
      memberRef: MEMBER_TARGET.memberRef,
      taskId: MEMBER_TARGET.taskId,
      listing: MEMBER_TARGET.listing,
      snapshot: 's',
      fileId: 'rf-x',
    });
  });
});

describe('an answer is applied only to the read it answers', () => {
  it('a Mission-level answer (member empty, its own request id) lands on a Mission-level read', () => {
    const session = sessionFor(MISSION_TARGET, answerFor(MISSION_TARGET, 'w.1'));
    expect(session.overview).not.toBeNull();
  });

  it('a member\'s answer never lands on a Mission-level read, nor the reverse, even for the same Mission, task, listing and request id', () => {
    const asMember = { ...answerFor(MISSION_TARGET, 'w.1'), memberRef: 'some-member' };
    expect(sessionFor(MISSION_TARGET, asMember).overview).toBeNull();
    const member = { ...MEMBER_TARGET, missionRef: MISSION_TARGET.missionRef, taskId: MISSION_TARGET.taskId, listing: MISSION_TARGET.listing };
    expect(sessionFor(member, { ...answerFor(member, 'w.1'), memberRef: '' }).overview).toBeNull();
  });

  it('an answer for another request id, or with none, is not applied', () => {
    expect(sessionFor(MISSION_TARGET, answerFor(MISSION_TARGET, 'w.9')).overview).toBeNull();
    expect(sessionFor(MISSION_TARGET, answerFor(MISSION_TARGET)).overview).toBeNull();
  });
});

describe('the session is cleared in its own words when the Mission\'s offer no longer holds', () => {
  it('holds while the office lists the same offer', () => {
    expect(model.offerFor(historical.states.live, MISSION_TARGET)).toEqual(OFFER);
    expect(model.offerStillHolds(MISSION_TARGET, model.offerFor(historical.states.live, MISSION_TARGET))).toBe(true);
    expect(model.clearedReasonFor(null, historical.states.live, MISSION_TARGET)).toBeNull();
  });

  it('a moved listing, a Mission that is gone and a withdrawn offer each clear it as the Mission changing; the order of causes is unchanged', () => {
    const moved = clone(historical.states.live);
    finishedOf(moved).codeReview!.listing = '0'.repeat(64);
    expect(model.clearedReasonFor(null, moved, MISSION_TARGET)).toBe('mission_changed');
    const gone = clone(historical.states.live);
    gone.missions.missions = gone.missions.missions.filter((m) => m.missionRef !== MISSION_TARGET.missionRef);
    expect(model.clearedReasonFor(null, gone, MISSION_TARGET)).toBe('mission_changed');
    const withdrawn = clone(historical.states.live);
    finishedOf(withdrawn).codeReview = { state: 'unavailable', reason: 'finished_execution_not_listed', taskId: null, listing: null };
    expect(model.clearedReasonFor(null, withdrawn, MISSION_TARGET)).toBe('mission_changed');
    // an office that is not live, and a lost link, are still said first
    expect(model.clearedReasonFor(null, historical.states.stale, MISSION_TARGET)).toBe('office_not_live');
    expect(model.clearedReasonFor('transport_down', historical.states.live, MISSION_TARGET)).toBe('connection_lost');
    // the member-bound causes are unchanged
    expect(model.clearedReasonFor(null, active.states.agentGone, MEMBER_TARGET)).toBe('agent_gone');
  });

  it('has words of its own, and they do not speak of an agent', () => {
    const said = model.clearedWords('mission_changed');
    expect(said.title.length).toBeGreaterThan(10);
    expect(said.body.length).toBeGreaterThan(40);
    expect(`${said.title} ${said.body}`).not.toMatch(/\bagent\b/i);
  });
});

describe('the Mission switch: a session is shown only for the Mission and the reference the office offers now', () => {
  const rowOf = () => scanRowsFromOffice(historical.states.live).find((r) => r.id === FINISHED.missionRef)!;

  it('reads under its own session', () => {
    const source = scanDiffFor(rowOf(), sessionFor(MISSION_TARGET, answerFor(MISSION_TARGET, 'w.1')));
    expect(source.kind).toBe('read');
  });

  it('is not shown for a session opened by a member of the same Mission, or by another listing', () => {
    const member = { ...MEMBER_TARGET, missionRef: MISSION_TARGET.missionRef, taskId: MISSION_TARGET.taskId, listing: MISSION_TARGET.listing };
    expect(scanDiffFor(rowOf(), sessionFor(member, answerFor(member, 'w.1'))).kind).toBe('reading');
    const other = { ...MISSION_TARGET, listing: '0'.repeat(64) };
    expect(scanDiffFor(rowOf(), sessionFor(other, answerFor(other, 'w.1'))).kind).toBe('reading');
  });

  it('says each way a finished Mission is not offered in its own words, never as an empty diff', () => {
    const stale = scanRowsFromOffice(historical.states.stale).find((r) => r.id === FINISHED.missionRef)!;
    const source = scanDiffFor(stale, null);
    expect(source.kind).toBe('unavailable');
    if (source.kind !== 'unavailable') return;
    expect(source.detail).toBe(model.UNAVAILABLE_WORDS.finished_execution_not_listed);
  });
});

describe('plain words for every new reason and refusal code', () => {
  const FINISHED_CODES = [
    'finished_execution_not_established',
    'finished_ownership_unverifiable',
    'finished_ownership_contested',
    'finished_workspace_not_corroborated',
  ];

  // CHANGED IN ROUND 29 (the Reviewer's finding). The previous assertion, /nothing (was )?(read|guessed|shown)|not (shown|read)/i, ACCEPTED "Nothing was read", which is false for a refusal that arises at the
  // end-of-read re-check (code_review.py `_revalidated`), after the workspace was read and the answer was discarded; the opposite sentence ("what was read was discarded") would be false for a refusal at
  // admission, before any read. The wire answer carries no phase, and none is added, so the only true wording is PHASE-FREE: it states the outcome that holds at both points and asserts no read history.
  // What this checks now: every finished refusal (1) states the supported outcome, in these exact words, (2) makes NO claim about whether, or what, the workspace was read, in either direction, and
  // (3) makes NO claim about what EXISTS. Dimension (3) was found in the same round: "No working tree is tied to this Mission" is an ontological claim where only an epistemic one is supported (for the
  // contested and unverifiable codes a tie may well exist; the review could not establish it), and in `finished_ownership_unverifiable` it contradicted its own cause clause ("the records that tie this
  // finished Mission to its workspace could not be read"). The outcome is therefore stated about what could be established, not about what exists.
  //
  // CHANGED IN ROUND 30 (the Reviewer's finding; a THIRD rejected wording). "This review could not establish a link ..., and nothing from one is shown" was true of the REFUSED REQUEST and false of the SCREEN it
  // was printed in: the reducer keeps the settled overview and attaches the refusal beside it, so after a successful overview and a refused file read the tree, the branch and the change counts are still drawn. The
  // sentence's REFERENT was wrong ("this review", "is shown"), not its content. It is now scoped to the refused request ("The refused request ... returned nothing from one"), and the retained overview is NOT cleared
  // to make a sentence true. What this checks now adds (4): no sentence has the review or the view as its referent (OVERBROAD_REFERENT, shared with diFinishedRefusalComposite.test.ts, which reads the same
  // sentence WITH the overview present, the condition under which it failed).
  const OUTCOME = 'The refused request could not establish a link between this Mission and a working tree, and returned nothing from one.';
  const ABSENCE: RegExp[] = [
    /\bno working tree is tied\b/i, // "No working tree is tied to this Mission": says the tie does not exist
    /\bno (working tree|workspace|link) (exists|was ever|is there|is tied)\b/i,
    /\b(is|are|was) not tied to (this|any) Mission\b/i,
    /\bhas no (working tree|workspace)\b/i,
  ];
  const READ_HISTORY: RegExp[] = [
    /nothing (was|has been|is|had been) read/i, // the denial ("Nothing was read") — false after the read
    /(was|were|has been|have been|had been) (not |never )?read\b/i, // any other claim that something was, or was not, read
    /discard/i, // the opposite claim ("what was read was discarded") — false before the read
    /no (file|files|read|content) (was|were|is) (made|read|opened|fetched)/i,
    /nothing was (opened|fetched|looked at|touched|loaded)/i,
  ];

  it('each finished refusal has its own title and body, states the supported outcome, and asserts NO read history in either direction (it must be true at admission and at release)', () => {
    for (const code of FINISHED_CODES) {
      const said = model.problemWords(code, 'a detail that must not be the words');
      expect(model.PROBLEM_WORD_KEYS, code).toContain(code);
      expect(said.title.length, code).toBeGreaterThan(10);
      expect(said.body, code).not.toContain('a detail that must not be the words');
      for (const claim of [...READ_HISTORY, ...ABSENCE, ...OVERBROAD_REFERENT]) {
        expect(said.body, `${code} must not match ${claim}`).not.toMatch(claim);
        expect(said.title, `${code} title must not match ${claim}`).not.toMatch(claim);
      }
      expect(said.body, code).toContain(OUTCOME);
    }
    expect(model.problemWords('finished_ownership_unverifiable', null).retry).toBe(true);
    expect(model.problemWords('finished_ownership_contested', null).retry).toBe(false);
    expect(model.problemWords('finished_workspace_not_corroborated', null).retry).toBe(false);
    expect(model.problemWords('finished_execution_not_established', null).retry).toBe(false);
  });

  it('the read-history denylist can fail: it flags each false sentence it exists to reject, and passes the phase-free outcome', () => {
    // the sentences the Reviewer found (the denial), its mirror image, and two paraphrases; and the supported outcome, which must pass all of them
    for (const false_ of [
      'Nothing was read.',
      'No working tree is tied to it. Nothing has been read.',
      'What was read was discarded.',
      'The workspace was not read.',
      'No file was opened.',
    ]) {
      expect(READ_HISTORY.some((claim) => claim.test(false_)), false_).toBe(true);
    }
    expect(READ_HISTORY.some((claim) => claim.test(OUTCOME))).toBe(false);
    expect(READ_HISTORY.some((claim) => claim.test('The records could not be read, or did not agree with the listing, so nothing is guessed.'))).toBe(false);
  });

  it('the absence denylist can fail too: it flags the over-claim that was shipped for an hour, and passes the epistemic outcome and each cause clause', () => {
    for (const overclaim of [
      'No working tree is tied to this Mission, and nothing from one is shown.',
      'No workspace exists for this Mission.',
      'This Mission is not tied to any Mission record.',
      'The Mission has no working tree.',
    ]) {
      expect(ABSENCE.some((claim) => claim.test(overclaim)), overclaim).toBe(true);
    }
    expect(ABSENCE.some((claim) => claim.test(OUTCOME))).toBe(false);
    // a cause clause that says a tie may exist and cannot be shown is not an absence claim
    expect(ABSENCE.some((claim) => claim.test('The records that tie this finished Mission to its workspace could not be read, or did not agree with the listing, so nothing is guessed.'))).toBe(false);
    expect(ABSENCE.some((claim) => claim.test('Another Mission record also names this task and workspace, so which Mission any changes there belong to cannot be told.'))).toBe(false);
    // and no shipped body asserts the contradiction: a cause that says a tie may exist next to a sentence that says it does not
    for (const code of FINISHED_CODES) expect(ABSENCE.some((claim) => claim.test(model.problemWords(code, null).body)), code).toBe(false);
  });

  it('the two new "not offered" reasons have exact words and a plain consequence that never implies a review is available', () => {
    for (const reason of ['assignment_not_finished', 'finished_execution_not_listed']) {
      expect(model.UNAVAILABLE_WORDS[reason], reason).toBeTruthy();
      const plain = model.reviewNotOfferedConsequence(reason);
      expect(plain, reason).toMatch(/code review is not offered/i);
      expect(plain, reason).not.toBe(model.unavailableWords(reason));
    }
  });

  it('"the assignment is not active" no longer says a completed assignment is never read', () => {
    const said = model.problemWords('assignment_not_active', null);
    expect(`${said.title} ${said.body}`).not.toMatch(/completed/i);
  });
});
