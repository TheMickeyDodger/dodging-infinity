/**
 * Deliverable 2, round 9 finding 1, the last layer: the words a person reads after "Delete local data", from the reply the office's REAL relay emits for the panel's
 * REAL answer (test/fixtures/di-mission-request-delete-replies.FIXTURE.json, exported and drift-checked by server/__tests__/diMissionPanelDeleteRelay.test.ts from the
 * answers tests/test_mission_retention_http.py records over the real HTTP route). A deletion that started and did not finish reads as started, never as refused and
 * never as finished; a completed one says what it removed (a kept summary, when reclamation had kept one); a refusal reads as a refusal.
 */
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

import { describe, expect, it } from 'vitest';

import type { DiMissionRequestReply } from '../../core/src/messages.ts';
import {
  INITIAL_STATE,
  MR_DELETE_COMPLETED_WORDS,
  MR_DELETE_PENDING_WORDS,
  MR_DELETE_RECLAIMED_WORDS,
  MR_DELETE_UNSTATED_WORDS,
  reduce,
} from '../src/components/diMissionRequestModel.ts';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const replies = (
  JSON.parse(fs.readFileSync(path.join(root, 'test/fixtures/di-mission-request-delete-replies.FIXTURE.json'), 'utf-8')) as {
    replies: Record<string, DiMissionRequestReply>;
  }
).replies;

const after = (reply: DiMissionRequestReply) => {
  const sent = reduce(INITIAL_STATE, { type: 'sent', seq: reply.seq, op: reply.op, requestRef: null, turnId: null });
  return reduce(sent, { type: 'reply', nowMs: 1000, reply });
};

describe('what a person reads after "Delete local data", on the replies the relay really emits', () => {
  it('a completed deletion of an automatically RECLAIMED Mission says its kept summary was deleted', () => {
    const notice = after(replies.completed_after_reclamation!).notices[0]!;
    expect(notice).toMatchObject({ kind: 'info', op: 'deleteLocalData', text: MR_DELETE_RECLAIMED_WORDS });
    expect(notice.text).toMatch(/kept summary \(final status, verdict, CI results and comments\) was deleted/);
  });
  it('a deletion that started and did not finish reads as started: never refused, never finished', () => {
    const notice = after(replies.pending_started_not_finished!).notices[0]!;
    expect(notice).toMatchObject({ kind: 'info', text: MR_DELETE_PENDING_WORDS });
    expect(notice.kind).not.toBe('refusal');
    expect(notice.text).not.toMatch(/was deleted/);
    expect(notice.text).toMatch(/has started and is not finished/);
  });
  it('a refusal reads as a refusal, in the panel’s own words', () => {
    for (const name of ['refused_already_deleted', 'refused_busy']) {
      const notice = after(replies[name]!).notices[0]!;
      expect(notice.kind, name).toBe('refusal');
    }
  });
  it('an accepted answer that states neither outcome is never read as finished', () => {
    const base = replies.completed_after_reclamation!;
    const silent = { ...base, answer: { ok: true } } as DiMissionRequestReply;
    expect(after(silent).notices[0]).toMatchObject({ kind: 'unknown', text: MR_DELETE_UNSTATED_WORDS });
    const plain = { ...base, answer: { ok: true, deletion: 'completed', deleted: { previously: 'intact' } } } as DiMissionRequestReply;
    expect(after(plain).notices[0]).toMatchObject({ kind: 'info', text: MR_DELETE_COMPLETED_WORDS });
  });
  it('a delete in flight when the office stops vouching for what it shows becomes NOT KNOWN, with why: never still waiting, never done', () => {
    const ref = 'mc:mn-' + '9'.repeat(32);
    const sent = reduce(INITIAL_STATE, { type: 'sent', seq: 3, op: 'deleteLocalData', requestRef: null, turnId: null, missionRef: ref });
    expect(sent.deletions[ref]?.state).toBe('in_flight');
    const lost = reduce(sent, { type: 'unvouched', reason: 'the connection was lost' });
    const deletion = lost.deletions[ref]!;
    expect(deletion.state).toBe('unknown');
    expect(deletion.text).toMatch(/^It is not known whether the deletion happened: .*the connection was lost/);
  });
  it('the pending words never promise completion: they say the next pass RETRIES, and read differently from the completed words', () => {
    expect(MR_DELETE_PENDING_WORDS).not.toBe(MR_DELETE_COMPLETED_WORDS);
    expect(MR_DELETE_PENDING_WORDS).not.toBe(MR_DELETE_RECLAIMED_WORDS);
    expect(MR_DELETE_PENDING_WORDS).toMatch(/^The deletion has started and is not finished: this Mission’s summary is already removed\./);
    expect(MR_DELETE_PENDING_WORDS).toMatch(/the next retention pass retries that, and it may take more than one attempt\./);
    expect(MR_DELETE_PENDING_WORDS).toMatch(/It was not refused\.$/);
    expect(MR_DELETE_PENDING_WORDS).not.toMatch(/\b(finishes|completes|will be removed|is removed by)\b/);
    expect(MR_DELETE_PENDING_WORDS).not.toMatch(/was deleted/);
    // the unstated words claim neither outcome either
    expect(MR_DELETE_UNSTATED_WORDS).toMatch(/did not say whether it finished/);
    expect(MR_DELETE_UNSTATED_WORDS).not.toMatch(/\b(was deleted|finishes|completes)\b/);
  });
  it('the panel\u2019s own pending detail, as the route sends it, promises a retry, never a completion', () => {
    const detail = String((replies.pending_started_not_finished!.answer as { deleted: { detail: string } }).deleted.detail);
    expect(detail).toMatch(/the next retention pass retries the remaining cleanup$/);
    expect(detail).not.toMatch(/\bfinishes\b/);
  });
});
