/**
 * The Herdr control's session: the office's Herdr buttons read one of these (DiScanPanel.tsx).
 *
 * It sends ONLY what the model builds (diHerdrModel.ts: statusMessage, launchMessage) and applies each `diHerdrReply` through the model's reducer. It holds no path, token or id of its own, and decides
 * nothing: every rule about what may be sent, and every word about what happened, is the model's. It sends no key, prompt or text to any pane or terminal; there is no message for that.
 *
 * IT SENDS NOTHING BY ITSELF. There is no polling, no timer and no read on load: a status request leaves when a Mission's screen asks for one (it opens, or the person presses "Check again"), and a launch
 * leaves only on the person's click. A status asked for while a request is in flight is held (one, the latest) and sent when that request answers, so a Mission opened a moment after another is not left unchecked.
 * This hook is not executed by any test (that needs a DOM renderer, which this webview's test route does not have); its decisions are the model's, which are.
 */
import { useCallback, useEffect, useReducer, useRef } from 'react';

import type { DiHerdrControl, HerdrState } from '../components/diHerdrModel.js';
import { INITIAL_HERDR_STATE, launchMessage, reduce, statusInFlight, statusMessage, takeHerdrSeq } from '../components/diHerdrModel.js';
import { transport } from '../transport/index.js';

export function useDiHerdr(clientReason: string | null): DiHerdrControl {
  const [state, dispatch] = useReducer(reduce, INITIAL_HERDR_STATE);
  const stateRef = useRef(state);
  stateRef.current = state;
  /** The latest Mission whose status was asked for while a request was in flight. */
  const held = useRef<string | null>(null);

  const send = useCallback((build: (s: HerdrState, seq: number) => ReturnType<typeof statusMessage>): boolean => {
    const seq = takeHerdrSeq();
    const message = build(stateRef.current, seq);
    if (message === null) return false;
    const sent = { type: 'sent' as const, seq, op: message.op, missionRef: message.missionRef };
    // The ref takes the sent request at once, so a second action before the next render finds one in flight and builds nothing.
    stateRef.current = reduce(stateRef.current, sent);
    dispatch(sent);
    transport.send(message);
    return true;
  }, []);

  const requestStatus = useCallback(
    (missionRef: string): void => {
      // The same Mission's status is already on its way: this ask is answered by that one (see statusInFlight). It is neither sent again nor held to be replayed.
      if (statusInFlight(stateRef.current, missionRef)) return;
      if (!send((s, seq) => statusMessage(s, missionRef, seq)) && stateRef.current.unvouched === null) held.current = missionRef;
    },
    [send],
  );

  const launch = useCallback((missionRef: string): void => void send((s, seq) => launchMessage(s, missionRef, seq)), [send]);

  useEffect(() => {
    return transport.onMessage((msg) => {
      if (msg.type !== 'diHerdrReply') return;
      dispatch({ type: 'reply', reply: msg });
    });
  }, []);

  // The client stops vouching for the office (disconnected, expired): nothing stays on screen as current, and nothing waits to be sent.
  useEffect(() => {
    if (clientReason !== null) held.current = null;
    dispatch({ type: 'unvouched', reason: clientReason });
  }, [clientReason]);

  // A status that was asked for while another request was in flight goes out when that request has answered.
  useEffect(() => {
    if (state.pending !== null || state.unvouched !== null || held.current === null) return;
    const missionRef = held.current;
    held.current = null;
    send((s, seq) => statusMessage(s, missionRef, seq));
  }, [state.pending, state.unvouched, send]);

  return { state, requestStatus, launch };
}
