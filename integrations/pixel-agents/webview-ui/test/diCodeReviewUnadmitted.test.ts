/**
 * A CODE REVIEW READ REFUSED UNREAD, both halves joined at the message itself:
 *  - the page's own read for an OFFERED review whose task id the read grammar refuses (the reception-v2 preview's shape);
 *  - the office's own answer to that exact message (server/src/di/codeReview.ts unadmittedReadReply, which the dispatcher sends;
 *    the dispatcher's sending it is pinned in server/__tests__/diCodeReview.test.ts);
 *  - the page's own reducer;
 *  - and what the Code Diff pane is drawn from (scanDiffFor).
 * Before the fix the office sent nothing, and the pane stayed "reading" with no end. The contract promises one answer "on every path, a
 * refusal included" (core/asyncapi.yaml, DiCodeReviewRead.requestId).
 */
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

import { describe, expect, it } from 'vitest';

import type { DiOfficeState } from '../../core/src/messages.ts';
import { parseCodeReviewRead, unadmittedReadReply } from '../../server/src/di/codeReview.ts';
import * as model from '../src/components/diCodeReviewModel.ts';
import { scanDiffFor } from '../src/components/diScanDiffRead.ts';
import { scanRowsFromOffice } from '../src/components/diScanModel.ts';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const doc = JSON.parse(
  fs.readFileSync(
    path.join(root, 'test/fixtures/di-office-state-code-review-v2.FIXTURE.json'),
    'utf-8',
  ),
) as { states: { live: DiOfficeState } };

/** The live office state, with its one offered member's task id replaced: the reception-v2 preview offered "SYNTHETIC-task-direct". */
function offeredWithTask(taskId: string): DiOfficeState {
  const state = structuredClone(doc.states.live);
  const members = state.missions.missions[0].members;
  if (members.state !== 'observed') throw new Error('the fixture lists its members');
  const member = members.members[0];
  if (member.codeReview?.state !== 'offered') throw new Error('the fixture offers a review');
  member.codeReview = { ...member.codeReview, taskId };
  return state;
}

describe('a read refused unread: the pane LEAVES "reading" and says why in plain words', () => {
  it("the office answers the page's own read, the page applies the answer, and the pane is refused with the new code's words", () => {
    const state = offeredWithTask('SYNTHETIC-task-direct');
    const row = scanRowsFromOffice(state).find((r) => r.reviewOffer.kind === 'offered');
    if (row === undefined || row.reviewOffer.kind !== 'offered')
      throw new Error('the row carries the offer');
    const target = row.reviewOffer.target;

    const opened = model.reduceSession(model.CLOSED_SESSION, { type: 'open', target });
    const request = model.nextRequest(opened);
    if (request === null) throw new Error('an open session asks for its overview');
    const asked = model.reduceSession(opened, { type: 'asked', request, target, id: 'w.1' });

    // the page's own wire message, which the office's parser refuses (the task id is outside the read grammar)
    const sent = model.reviewReadMessage(target, request, 'w.1') as Record<string, unknown>;
    expect(parseCodeReviewRead(sent), 'the read is refused unread').toBeNull();
    const answer = unadmittedReadReply(sent);
    if (answer === null)
      throw new Error("the office answers a read whose shape is exactly a read's");
    expect(answer.problem).toBe('reference_not_admitted');

    // THE PAIR, by the same mechanism (the real reducer, then what the pane is drawn from), so "leaves reading" can fail by construction:
    // ABSENT: no answer at all (what the office used to send): the pane STAYS "reading", the state the preview was stuck in
    expect(scanDiffFor(row, asked).kind, 'with no answer the pane stays reading').toBe('reading');
    // ABSENT: the very same refusal, answering ANOTHER read (another request id): not this read's answer, so the pane STAYS "reading"
    const otherRead = model.reduceSession(asked, {
      type: 'answer',
      answer: { ...answer, requestId: 'w.9' },
      nowMs: 0,
    });
    expect(otherRead.refusal, 'an answer to another read is not taken').toBeNull();
    expect(
      scanDiffFor(row, otherRead).kind,
      'an answer to another read leaves the pane reading',
    ).toBe('reading');
    // PRESENT: the office's answer to THIS read: the pane LEAVES "reading"
    const session = model.reduceSession(asked, { type: 'answer', answer, nowMs: 0 });
    expect(session.refusal?.problem, 'the page took the answer as the answer to its read').toBe(
      'reference_not_admitted',
    );
    const pane = scanDiffFor(row, session);
    expect(pane.kind, "with this read's answer the pane leaves reading").toBe('refused');
    expect(pane).toMatchObject({
      title: 'The office could not admit this read',
      detail: 'The office could not accept the request for this review.',
    });
  });
});
