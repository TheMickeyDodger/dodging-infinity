/**
 * Item 3 — the live code review's session, owned by App.
 *
 * It sends exactly the requests `nextRequest` (the model) says the session needs — one at a time,
 * each naming an ADMITTED REFERENCE (never a path) — applies the office's answers through the
 * model's reducer, and CLEARS the session the moment the client can no longer vouch for what it
 * shows: the connection or the office state is not trusted, the assignment moved, or the agent is
 * gone. A diff is never kept on screen under a label that says it is current.
 */
import { useCallback, useEffect, useReducer } from 'react';

import type { DiOfficeState } from '../../../core/src/messages.js';
import type {
  DiCodeReviewFeed,
  ReviewRequest,
  ReviewSession,
  ReviewTargetRef,
} from '../components/diCodeReviewModel.js';
import {
  clearedReasonFor,
  CLOSED_SESSION,
  createRequestIds,
  isRetryable,
  nextRequest,
  reduceSession,
  reviewReadMessage,
} from '../components/diCodeReviewModel.js';

/** How long a "not this instant" refusal waits before the read is asked again. */
const RETRY_AFTER_MS = 400;
import { transport } from '../transport/index.js';

export interface DiCodeReviewControl {
  session: ReviewSession;
  /** Open a review for an agent whose offer the office state carries. */
  open: (target: ReviewTargetRef) => void;
  close: () => void;
  /** Ask for the overview again (a new observation of the workspace). */
  refresh: () => void;
  select: (path: string | null) => void;
}

/**
 * Every read names itself. ONE allocator for the life of the page, shared by every session this hook ever opens: ids never repeat across close, reopen
 * and reads still outstanding. (Per-session counting would hand a reopened session the id of the previous session's outstanding read.)
 */
const nextRequestId = createRequestIds('w');

/** The wire message for a request (a reference only): the model's `reviewReadMessage`, a member-bound read or a Mission-level one by the target's kind. */
export function readMessage(target: ReviewTargetRef, request: ReviewRequest, requestId?: string) {
  return reviewReadMessage(target, request, requestId);
}

export function useDiCodeReview(
  officeState: DiOfficeState | null,
  clientReason: string | null,
  answers: DiCodeReviewFeed,
): DiCodeReviewControl {
  const [session, dispatch] = useReducer(reduceSession, CLOSED_SESSION);

  // The request the session needs next is sent once, and recorded as in flight.
  useEffect(() => {
    const request = nextRequest(session);
    if (request === null || session.target === null) return;
    const id = nextRequestId();
    dispatch({ type: 'asked', request, target: session.target, id });
    transport.send(readMessage(session.target, request, id));
  }, [session]);

  // EVERY answer reaches the reducer, in the order it arrived, whatever a render batches: the feed
  // delivers each one and keeps none, and the reducer ignores any that does not answer the request in
  // flight for THIS target. (A latest-value slot lost one of two answers handled before a render.)
  useEffect(
    () => answers.subscribe((answer) => dispatch({ type: 'answer', answer, nowMs: Date.now() })),
    [answers],
  );

  // A refusal that only means "not this instant" (the office reads at a bounded rate, one at a
  // time) is asked again shortly, by itself: the reader should never have to click for it.
  useEffect(() => {
    if (!isRetryable(session.refusal)) return;
    const timer = setTimeout(() => dispatch({ type: 'retry' }), RETRY_AFTER_MS);
    return () => clearTimeout(timer);
  }, [session.refusal]);

  // Nothing stays on screen once the client cannot vouch for it: the link, the office state as a
  // whole, or this agent's offer. The model decides, with the cause in its own words.
  useEffect(() => {
    if (session.target === null || session.cleared !== null) return;
    const reason = clearedReasonFor(clientReason, officeState, session.target);
    if (reason !== null) dispatch({ type: 'clear', reason, target: session.target });
  }, [session.target, session.cleared, clientReason, officeState]);

  const open = useCallback((target: ReviewTargetRef) => dispatch({ type: 'open', target }), []);
  const close = useCallback(() => dispatch({ type: 'close' }), []);
  const select = useCallback(
    (path: string | null) => dispatch({ type: 'select', path }),
    [],
  );
  const refresh = useCallback(() => {
    dispatch({ type: 'refresh' });
  }, []);
  return { session, open, close, refresh, select };
}
