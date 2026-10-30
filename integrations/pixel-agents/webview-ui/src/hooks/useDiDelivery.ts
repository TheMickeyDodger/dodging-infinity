/**
 * b3 — a delivery session. It has two kinds (M8): the standalone panel's (`entered`: the person types the id) and Mission details'
 * (`linked`: the id is the shown Mission's delivery link, set through `setLinked`). App starts ONLY the linked one: the standalone
 * panel is kept for test harnesses only, with no product entry (owner decision, 2026-10-29). Sessions never share a sequence number
 * (the model's takeDeliverySeq), so where two run, a reply, matched by seq and op only, applies to the one session that asked.
 *
 * It sends ONLY what the model builds (diDeliveryModel.ts: statusMessage, advanceMessage, followUpMessage), and applies each
 * `diDeliveryReply` through the model's reducer. It holds no token and decides nothing: every rule about what may be sent is the model's.
 *
 * IT SENDS NOTHING BY ITSELF. There is no polling, no timer and no read on load. A message leaves only when the person reads a status or
 * asks for the one confirmed step, and, after any advance reply, as the ONE follow-up status READ the model asks for (never an advance).
 * This hook is not executed by any test (that needs a DOM renderer, which this webview's test route does not have); its decisions are
 * the model's, which are.
 */
import { useCallback, useEffect, useReducer, useRef } from 'react';

import type { DiDeliveryRequest } from '../../../core/src/messages.js';
import type { DlEvent, DlState, DlSubject } from '../components/diDeliveryModel.js';
import {
  advanceMessage,
  followUpMessage,
  INITIAL_DL_LINKED_STATE,
  INITIAL_DL_STATE,
  reduce,
  statusMessage,
  takeDeliverySeq,
} from '../components/diDeliveryModel.js';
import { transport } from '../transport/index.js';

export interface DiDeliveryControl {
  state: DlState;
  open: () => void;
  close: () => void;
  setEntered: (text: string) => void;
  /** M8: the ONE delivery linked to the shown Mission (null: none), for a session in linked mode. */
  setLinked: (deliveryId: string | null) => void;
  setConfirmed: (on: boolean) => void;
  /** Read the entered delivery's status. */
  readStatus: () => void;
  /** Ask the adapter for the ONE confirmed step on display. */
  askForStep: () => void;
  dismiss: (id: number) => void;
}

export function useDiDelivery(clientReason: string | null, subject: DlSubject = 'entered'): DiDeliveryControl {
  const [state, dispatch] = useReducer(reduce, subject === 'linked' ? INITIAL_DL_LINKED_STATE : INITIAL_DL_STATE);
  const stateRef = useRef(state);
  stateRef.current = state;

  /** Send what the model builds for this state, or nothing when it builds nothing. */
  const send = useCallback((build: (s: DlState, seq: number) => DiDeliveryRequest | null): void => {
    // a seq the model builds nothing for is simply never used: what matters is that no two requests share one
    const seq = takeDeliverySeq();
    const message = build(stateRef.current, seq);
    if (message === null) return;
    const sent: DlEvent = { type: 'sent', seq, op: message.op, deliveryId: message.deliveryId };
    // The ref takes the sent request at once, so a second action before the next render finds a request waiting, and the
    // model's builders build nothing for it.
    stateRef.current = reduce(stateRef.current, sent);
    dispatch(sent);
    transport.send(message);
  }, []);

  useEffect(() => {
    return transport.onMessage((msg) => {
      if (msg.type !== 'diDeliveryReply') return;
      dispatch({ type: 'reply', reply: msg, nowMs: Date.now() });
    });
  }, []);

  // The client stops vouching for the office (disconnected, expired): nothing stays on screen as current.
  useEffect(() => {
    dispatch({ type: 'unvouched', reason: clientReason });
  }, [clientReason]);

  // After an advance reply, the model asks for ONE status read of that delivery: a READ, never an advance.
  useEffect(() => {
    if (state.followUpRead !== null && state.pending === null && state.unvouched === null) send(followUpMessage);
  }, [state.followUpRead, state.pending, state.unvouched, send]);

  return {
    state,
    open: useCallback(() => dispatch({ type: 'open' }), []),
    close: useCallback(() => dispatch({ type: 'close' }), []),
    setEntered: useCallback((text: string) => dispatch({ type: 'entered', text }), []),
    setLinked: useCallback((deliveryId: string | null) => dispatch({ type: 'linked', deliveryId }), []),
    setConfirmed: useCallback((on: boolean) => dispatch({ type: 'confirm', on }), []),
    readStatus: useCallback(() => send(statusMessage), [send]),
    askForStep: useCallback(() => send(advanceMessage), [send]),
    dismiss: useCallback((id: number) => dispatch({ type: 'dismiss', id }), []),
  };
}
