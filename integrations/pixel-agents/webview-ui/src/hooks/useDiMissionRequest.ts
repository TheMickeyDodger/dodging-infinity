/**
 * Item 4 — the Mission request panel's session, owned by App.
 *
 * It sends only the closed `diMissionRequest` messages the model builds, one answer per ask, and
 * applies each `diMissionRequestReply` through the model's reducer. It holds no token and cannot
 * name a path, a shell or a principal: the server relays to the loopback adapter.
 *
 * Reading is cheap and repeated; writing is deliberate and never replayed. After ANY write (its
 * answer may be an unknown outcome) the record is read again rather than trusted, and the window
 * never sends the same write twice by itself. Polling pauses while the page is hidden and stops
 * entirely while the client cannot vouch for the office.
 */
import { useCallback, useEffect, useReducer, useRef } from 'react';

import type { DiMissionRequestOp, DiMissionRequestReply } from '../../../core/src/messages.js';
import type { MrFields, MrState, MrTab, MrTurn } from '../components/diMissionRequestModel.js';
import {
  actionPending,
  buildMessage,
  composerProblem,
  decisionFields,
  INITIAL_STATE,
  isDecisionWord,
  isPending,
  isWrite,
  MR_POLL_CLOSED_MS,
  MR_POLL_OPEN_MS,
  MR_POLL_TURN_MS,
  offeredActions,
  reduce,
  withdrawFields,
} from '../components/diMissionRequestModel.js';
import { transport } from '../transport/index.js';

export type MrRunCommand = 'dispatch' | 'observe' | 'reconcile' | 'verify';

export interface DiMissionRequestControl {
  state: MrState;
  open: (tab?: MrTab) => void;
  setTab: (tab: MrTab) => void;
  close: () => void;
  setWide: (on: boolean) => void;
  setComposer: (text: string) => void;
  setTyped: (text: string) => void;
  /** Send the composer's words as one turn of the restricted, read-only conversation. */
  sendTurn: () => void;
  newConversation: () => void;
  openConversation: (conversationId: string) => void;
  /** Bind a draft: submit it to the request surface as a proposal of record. */
  submitDraft: (turn: MrTurn) => void;
  select: (requestRef: string | null) => void;
  /** Read the CURRENT proposal after the one on display was invalidated. */
  acknowledge: (requestRef: string) => void;
  /** Start a revision: the next draft that is submitted replaces this pending proposal. */
  revise: (requestRef: string | null) => void;
  /** Send the typed word as the decision on the proposal AND offer on display. */
  decide: () => void;
  /** Read the decision offer again (after it expired): an explicit act, never a silent swap. */
  readOffer: (requestRef: string) => void;
  withdraw: () => void;
  checkApproval: () => void;
  run: (command: MrRunCommand) => void;
  loadResult: () => void;
  dismiss: (id: number) => void;
  refresh: () => void;
}

export function useDiMissionRequest(
  clientReason: string | null,
  officePresent: boolean,
): DiMissionRequestControl {
  const [state, dispatch] = useReducer(reduce, INITIAL_STATE);
  const stateRef = useRef(state);
  stateRef.current = state;
  const seqRef = useRef(1);

  const ask = useCallback(
    (
      op: DiMissionRequestOp,
      fields: MrFields = {},
      meta: { requestRef?: string | null; turnId?: string | null } = {},
    ): void => {
      const seq = seqRef.current++;
      dispatch({
        type: 'sent',
        seq,
        op,
        requestRef: meta.requestRef ?? null,
        turnId: meta.turnId ?? null,
      });
      transport.send(buildMessage(op, seq, fields));
    },
    [],
  );

  /** One read per kind at a time: a slow answer is never queued behind itself. */
  const read = useCallback(
    (op: DiMissionRequestOp, fields: MrFields = {}, requestRef: string | null = null): void => {
      if (isPending(stateRef.current, op, requestRef)) return;
      ask(op, fields, { requestRef });
    },
    [ask],
  );

  const readSelected = useCallback((): void => {
    const s = stateRef.current;
    if (s.selectedRef !== null) read('request', { requestRef: s.selectedRef }, s.selectedRef);
  }, [read]);

  const readConversation = useCallback((): void => {
    const s = stateRef.current;
    if (s.conversation.id !== null) read('conversation', { conversationId: s.conversation.id });
  }, [read]);

  // Replies: every one is applied, in order. After a write, the record is read again.
  useEffect(() => {
    return transport.onMessage((msg) => {
      if (msg.type !== 'diMissionRequestReply') return;
      const reply: DiMissionRequestReply = msg;
      dispatch({ type: 'reply', reply, nowMs: Date.now() });
      if (isWrite(reply.op)) {
        queueMicrotask(() => {
          read('refresh');
          readSelected();
          readConversation();
        });
      }
    });
  }, [read, readSelected, readConversation]);

  // The client stops vouching for the office (disconnected, expired): nothing stays on screen.
  useEffect(() => {
    dispatch({ type: 'unvouched', reason: clientReason });
  }, [clientReason]);

  // Polling: the overview for the entry; the open request and conversation for the workspace.
  // These are READS, made to RENDER what the records say now. They drive nothing: a started Mission
  // is advanced by the progression runtime (dirun, MissionCore mode), not by this window asking. The
  // overview read (`refresh`) is the only message this hook sends while no workspace is open, and it
  // carries no field beyond `type`, `seq` and `op`; the code review's session guard allows exactly that.
  const lastPoll = useRef({ overview: 0, selected: 0, conversation: 0 });
  useEffect(() => {
    if (!officePresent) return;
    const tick = (): void => {
      const s = stateRef.current;
      if (s.unvouched !== null || (typeof document !== 'undefined' && document.hidden)) return;
      const now = Date.now();
      const every = s.open ? MR_POLL_OPEN_MS : MR_POLL_CLOSED_MS;
      if (now - lastPoll.current.overview >= every) {
        lastPoll.current.overview = now;
        read('refresh');
      }
      if (!s.open) return;
      if (s.selectedRef !== null && now - lastPoll.current.selected >= every) {
        lastPoll.current.selected = now;
        readSelected();
      }
      const turnPending = s.conversation.turns.some((t) => t.state === 'RESERVED');
      if (
        s.conversation.id !== null &&
        now - lastPoll.current.conversation >= (turnPending ? MR_POLL_TURN_MS : every)
      ) {
        lastPoll.current.conversation = now;
        readConversation();
      }
    };
    tick();
    const timer = setInterval(tick, 400);
    return () => clearInterval(timer);
  }, [officePresent, read, readSelected, readConversation]);

  // Reading the offer once per displayed proposal that can be decided: the server's own statement
  // of what a decision would bind, checked against what is on display BEFORE the person types.
  const offerKey = useRef<string | null>(null);
  useEffect(() => {
    const ref = state.selectedRef;
    const view = ref === null ? undefined : state.views[ref];
    if (!ref || !view || view.stale || state.unvouched !== null) return;
    if (!view.latest.lifecycle.actions.includes('decide') || view.offer !== null) return;
    const key = `${ref}:${view.displayed.revision}:${view.displayed.digest}:${view.offerEpoch}`;
    if (offerKey.current === key) return;
    offerKey.current = key;
    read('offer', { requestRef: ref }, ref);
  }, [state.selectedRef, state.views, state.unvouched, read]);

  const open = useCallback((tab?: MrTab) => {
    dispatch({ type: 'open', tab });
    lastPoll.current = { overview: 0, selected: 0, conversation: 0 };
  }, []);
  const setTab = useCallback((tab: MrTab) => dispatch({ type: 'tab', tab }), []);
  const close = useCallback(() => dispatch({ type: 'close' }), []);
  const setWide = useCallback((on: boolean) => dispatch({ type: 'wide', on }), []);
  const setComposer = useCallback((text: string) => dispatch({ type: 'composer', text }), []);
  const setTyped = useCallback((text: string) => dispatch({ type: 'typed', text }), []);

  const sendTurn = useCallback(() => {
    const s = stateRef.current;
    if (s.unvouched !== null || isPending(s, 'turn') || composerProblem(s.composer) !== null)
      return;
    // The conversation is busy while the assistant is still answering its last message.
    if (s.conversation.turns.some((t) => t.state === 'RESERVED')) return;
    ask('turn', { conversationId: s.conversation.id, text: s.composer });
  }, [ask]);

  const newConversation = useCallback(() => dispatch({ type: 'newConversation' }), []);
  const openConversation = useCallback(
    (conversationId: string) => {
      dispatch({ type: 'openConversation', conversationId });
      ask('conversation', { conversationId });
    },
    [ask],
  );

  const submitDraft = useCallback(
    (turn: MrTurn) => {
      const s = stateRef.current;
      if (turn.draft === null || s.unvouched !== null || isPending(s, 'submit')) return;
      ask(
        'submit',
        { turnId: turn.turnId, digest: turn.draft.digest, supersedes: s.revising },
        { turnId: turn.turnId },
      );
    },
    [ask],
  );

  const select = useCallback(
    (requestRef: string | null) => {
      dispatch({ type: 'select', requestRef });
      if (requestRef !== null) ask('request', { requestRef }, { requestRef });
    },
    [ask],
  );
  const acknowledge = useCallback(
    (requestRef: string) => {
      dispatch({ type: 'acknowledge', requestRef });
      offerKey.current = null;
      ask('request', { requestRef }, { requestRef });
    },
    [ask],
  );
  const revise = useCallback(
    (requestRef: string | null) => dispatch({ type: 'revise', requestRef }),
    [],
  );

  const decide = useCallback(() => {
    const s = stateRef.current;
    const ref = s.selectedRef;
    const view = ref === null ? undefined : s.views[ref];
    if (!ref || !view || view.stale || s.unvouched !== null) return;
    if (
      !offeredActions(s, ref).includes('decide') ||
      !isDecisionWord(s.typed) ||
      actionPending(s, 'decide', ref)
    )
      return;
    const fields = decisionFields(view, s.typed);
    if (fields === null) return; // no offer on display: nothing to decide on
    ask('decide', fields, { requestRef: ref });
  }, [ask]);

  const readOffer = useCallback(
    (requestRef: string) => {
      dispatch({ type: 'dropOffer', requestRef });
      offerKey.current = null;
      ask('offer', { requestRef }, { requestRef });
    },
    [ask],
  );

  const withdraw = useCallback(() => {
    const s = stateRef.current;
    const ref = s.selectedRef;
    const view = ref === null ? undefined : s.views[ref];
    if (
      !ref ||
      !view ||
      !offeredActions(s, ref).includes('withdraw') ||
      actionPending(s, 'withdraw', ref)
    )
      return;
    ask('withdraw', withdrawFields(view), { requestRef: ref });
  }, [ask]);

  const checkApproval = useCallback(() => {
    const s = stateRef.current;
    const ref = s.selectedRef;
    if (
      !ref ||
      !offeredActions(s, ref).includes('check_approval') ||
      actionPending(s, 'check_approval', ref)
    )
      return;
    ask('checkApproval', { requestRef: ref }, { requestRef: ref });
  }, [ask]);

  const run = useCallback(
    (command: MrRunCommand) => {
      const s = stateRef.current;
      const ref = s.selectedRef;
      if (!ref || !offeredActions(s, ref).includes(command) || actionPending(s, command, ref))
        return;
      ask('run', { requestRef: ref, command }, { requestRef: ref });
    },
    [ask],
  );

  const loadResult = useCallback(() => {
    const s = stateRef.current;
    const ref = s.selectedRef;
    if (!ref || !offeredActions(s, ref).includes('result') || actionPending(s, 'result', ref))
      return;
    ask('result', { requestRef: ref }, { requestRef: ref });
  }, [ask]);

  const dismiss = useCallback((id: number) => dispatch({ type: 'dismiss', id }), []);
  const refresh = useCallback(() => {
    read('refresh');
    readSelected();
    readConversation();
  }, [read, readSelected, readConversation]);

  return {
    state,
    open,
    setTab,
    close,
    setWide,
    setComposer,
    setTyped,
    sendTurn,
    newConversation,
    openConversation,
    submitDraft,
    select,
    acknowledge,
    revise,
    decide,
    readOffer,
    withdraw,
    checkApproval,
    run,
    loadResult,
    dismiss,
    refresh,
  };
}
