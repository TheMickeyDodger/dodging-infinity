/**
 * M8, STORE IDENTITY (a limit, pinned): the catalog links a Mission to a delivery id from ITS registered store, and the office delivery
 * adapter reads ITS own; the office cannot show they are one store. So a linked id the adapter's store has no record of must reach the
 * office as the adapter's OWN refusal, with its own code, and never as "unavailable" (nothing changed) or "unknown".
 *
 * The adapter's answer for such an id is EXACTLY `{ok: false, problem: "pr_delivery_missing_record"}`, HTTP 200: pr_delivery/machine.py
 * `load` raises MachineError with PROBLEM_MISSING_RECORD ("pr_delivery_missing_record"), and office_delivery/http_adapter.py answers any
 * adapter exception with `_problem(_problem_of(exc))`, the exception's own code. This file pins the relay's reading of that answer; the
 * webview's rendering of the reply is pinned in webview-ui/test/diMissionDelivery.test.ts.
 */
import { describe, expect, it } from 'vitest';

import { parseDeliveryRequest, replyFromDelivery } from '../src/di/deliveryRequests.js';

const LINKED = 'prd-0123456789abcdef01234567';
const MISSING = { ok: false, problem: 'pr_delivery_missing_record' };

describe('a linked id the adapter\'s store has no record of', () => {
  it('a status read of it is the adapter\'s own refusal, with its own code: not unavailable, not unknown', () => {
    const parsed = parseDeliveryRequest({ type: 'diDeliveryRequest', seq: 11, op: 'status', deliveryId: LINKED });
    if (!parsed.ok) throw new Error('the status request did not parse');
    const out = replyFromDelivery(parsed, { kind: 'http', status: 200, body: MISSING });
    expect(out).toEqual({
      type: 'diDeliveryReply',
      seq: 11,
      op: 'status',
      state: 'refused',
      problem: 'pr_delivery_missing_record',
      reason: null,
      answer: MISSING,
    });
  });
});
