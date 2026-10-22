/**
 * Item 4 — the Mission request workspace.
 *
 * ordinary-language request -> real clarification -> an exact, readable proposal -> the separate
 * human decision -> validation and dispatch -> the team working, with progress recorded ->
 * independent verification -> a retrievable verified result.
 *
 * What this component is and is not:
 *
 *  - It is docked BESIDE the office panel and is not modal: the world stays visible and usable
 *    while a request is read, decided and run. Where there is no room it covers the window and
 *    says so in words.
 *  - It offers only what the records support (`lifecycle.actions`) and only to the proposal that
 *    was SHOWN. If the records move to a different proposal, the display is invalidated and
 *    decisions are withheld until the person reads the current one.
 *  - A decision is the word the person TYPES. Nothing the assistant wrote, and nothing on this
 *    page, can produce it. The approval is shown as an operator attestation that is not
 *    independently verified, because that is what it is.
 *  - A control that is not available now keeps its place and its focus (`aria-disabled`, never
 *    `disabled`): a disabled control that holds focus drops it to the page, and the next key
 *    would reach the office. Every transition that REMOVES the focused control hands focus to a
 *    named place, and a safety net catches any that slips past.
 *  - It holds no token and can name no path, shell or principal; it only asks the closed
 *    `diMissionRequest` messages, which the server relays.
 */
import './diMissionRequest.css';

import type { KeyboardEvent as ReactKeyboardEvent, RefObject } from 'react';
import { useEffect, useLayoutEffect, useRef, useState } from 'react';

import { DI_PANEL_WIDTH_PX } from '../constants.js';
import type { DiMissionRequestControl } from '../hooks/useDiMissionRequest.js';
import type { MrTab, MrTurn, MrView } from './diMissionRequestModel.js';
import {
  actionPending,
  agoWords,
  composerProblem,
  countdownWords,
  COVERING_WORDS,
  decisionBlockedWords,
  isDecisionWord,
  isPending,
  layoutFor,
  MR_DECISION_WORD,
  MR_DRAFTING_NOTE,
  MR_FIXTURE_SHORT,
  MR_FIXTURE_WORDS,
  MR_NAME,
  MR_TAGLINE,
  offeredActions,
  offerState,
  omittedWords,
  plainTitle,
  progressionWords,
  remainingSeconds,
  replyWithoutFencedBlocks,
  replyWithoutQuestionsBlock,
  serverNowSeconds,
  shortDigest,
  splitRecordSentence,
  technicalBlockNote,
  turnStateWords,
  twoColumnReading,
} from './diMissionRequestModel.js';
import {
  Digest,
  Journey,
  Line,
  ProofRules,
  ProposalView,
  ResultView,
  ScopeLine,
  SourceFacts,
  StartCauseView,
  Untrusted,
} from './DiMissionRequestParts.js';

// ---------------------------------------------------------------------------------------------
// small hooks

function useViewportWidth(): number {
  const [width, setWidth] = useState(() => window.innerWidth);
  useEffect(() => {
    const onResize = (): void => setWidth(window.innerWidth);
    window.addEventListener('resize', onResize);
    return () => window.removeEventListener('resize', onResize);
  }, []);
  return width;
}

function useNowMs(): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, []);
  return now;
}

/**
 * The safety net: if the control that HELD focus is removed from the page (a stage changed, a
 * block was replaced), focus goes to `fallback` instead of dropping to the page. A person who
 * deliberately clicks the world leaves their control in place, and is never pulled back.
 */
function useFocusRescue(
  rootRef: RefObject<HTMLElement | null>,
  fallbackRef: RefObject<HTMLElement | null>,
): void {
  useEffect(() => {
    const root = rootRef.current;
    if (!root) return;
    let last: HTMLElement | null = null;
    let holds = false;
    const onFocusIn = (event: FocusEvent): void => {
      if (event.target instanceof HTMLElement) {
        last = event.target;
        holds = true;
      }
    };
    const onFocusOut = (event: FocusEvent): void => {
      const left = event.target;
      if (!(left instanceof HTMLElement)) return;
      // Moving focus inside, or deliberately elsewhere, is not a loss. A removal is: the
      // element is no longer connected by the time the browser has settled. The flag is cleared
      // only if the element that LOST focus is still the one being tracked: when focus is moved by
      // script (element.focus()), the new element's focusin has already run by the time this
      // microtask does, and clearing the flag then would leave the NEW element unprotected, so
      // removing it would drop focus to the page with nobody to catch it.
      queueMicrotask(() => {
        if (last === left && left.isConnected && left.getClientRects().length > 0) holds = false;
      });
    };
    const check = (): void => {
      if (!holds || last === null) return;
      const gone = !last.isConnected || last.getClientRects().length === 0;
      const dropped = document.activeElement === null || document.activeElement === document.body;
      if (gone && dropped) {
        holds = false;
        fallbackRef.current?.focus({ preventScroll: true });
      } else if (gone) {
        holds = false;
      }
    };
    root.addEventListener('focusin', onFocusIn);
    root.addEventListener('focusout', onFocusOut);
    const observer = new MutationObserver(() => queueMicrotask(check));
    observer.observe(root, {
      childList: true,
      subtree: true,
      attributes: true,
      attributeFilter: ['hidden', 'style', 'class'],
    });
    return () => {
      root.removeEventListener('focusin', onFocusIn);
      root.removeEventListener('focusout', onFocusOut);
      observer.disconnect();
    };
  }, [rootRef, fallbackRef]);
}

/** An action button: held (not disabled) while unavailable or waiting for its answer. */
function Act({
  testId,
  onAct,
  available = true,
  held = false,
  primary = false,
  danger = false,
  children,
  describedBy,
}: {
  testId: string;
  onAct: () => void;
  available?: boolean;
  held?: boolean;
  primary?: boolean;
  danger?: boolean;
  children: React.ReactNode;
  describedBy?: string;
}) {
  const off = !available || held;
  return (
    <button
      type="button"
      data-testid={testId}
      data-primary={primary && !off ? 'true' : undefined}
      data-danger={danger ? 'true' : undefined}
      aria-disabled={off ? 'true' : undefined}
      aria-describedby={describedBy}
      onClick={() => {
        if (!off) onAct();
      }}
    >
      {held ? `${children} (waiting for the answer…)` : children}
    </button>
  );
}

// ---------------------------------------------------------------------------------------------
// the conversation

function DraftCard({
  turn,
  latest,
  control,
  meanings,
}: {
  turn: MrTurn;
  latest: boolean;
  control: DiMissionRequestControl;
  meanings: Record<string, string>;
}) {
  const { state } = control;
  const draft = turn.draft!;
  const submission = turn.submission;
  const held = isPending(state, 'submit') || state.unvouched !== null;
  const canSubmit = latest && (submission === null || submission.state === 'REFUSED');
  const revising = state.revising;
  return (
    <div className="di-mr-section" data-testid="di-mr-draft" aria-label="A draft proposal">
      <p>
        <strong>A draft, not yet a proposal of record.</strong>{' '}
        {latest
          ? 'Read it exactly as written. Submitting it makes it a proposal of record: Mission Core then holds it with a revision and a digest. Submitting approves nothing and starts nothing.'
          : 'A later draft replaced this one in the conversation.'}
      </p>
      <ProposalView
        proposal={draft.proposal}
        source={{ requestRef: null, missionId: null, revision: null, digest: draft.digest }}
        meanings={meanings}
        heading="The proposal, exactly as drafted"
        testId="di-mr-draft-proposal"
      />
      {draft.runnableProblem !== null && (
        <div className="di-mr-box" data-tone="warn" data-testid="di-mr-draft-not-runnable">
          <p>
            <strong>This proposal could not start a run:</strong> {draft.runnableProblem}. You can
            still record it, but a run would be refused.
          </p>
        </div>
      )}
      {submission?.state === 'SUBMITTED' && submission.requestRef !== null && (
        <div className="di-mr-box" data-tone="ok" data-testid="di-mr-draft-submitted">
          <p>
            Submitted as <code>{submission.requestRef}</code>.{' '}
            <button
              type="button"
              onClick={() => control.select(submission.requestRef)}
              data-testid="di-mr-open-submitted"
            >
              Open it in Requests
            </button>
          </p>
        </div>
      )}
      {submission?.state === 'RESERVED' && (
        <div className="di-mr-box" data-tone="warn" data-testid="di-mr-draft-submitting">
          <p>
            This draft is being submitted now. It will appear under Requests when it is recorded.
          </p>
        </div>
      )}
      {submission?.state === 'UNCERTAIN' && (
        <div className="di-mr-box" data-tone="warn" data-testid="di-mr-draft-uncertain">
          <p>
            <strong>Whether this draft was recorded is not known.</strong> It was not submitted
            again. Look under Requests: if it is there, nothing more is needed; if it is not,
            continue the conversation and submit a new draft.
          </p>
          {submission.possibleMatches.length > 0 && (
            <>
              <p>A proposal of record with exactly this digest exists:</p>
              <ul>
                {submission.possibleMatches.map((ref) => (
                  <li key={ref}>
                    <button
                      type="button"
                      onClick={() => control.select(ref)}
                      data-testid="di-mr-open-match"
                    >
                      Open <code>{ref}</code>
                    </button>
                  </li>
                ))}
              </ul>
            </>
          )}
        </div>
      )}
      {submission?.state === 'REFUSED' && (
        <div className="di-mr-box" data-tone="block" data-testid="di-mr-draft-refused">
          <p>
            This draft was not recorded ({submission.problem ?? 'refused'}). Nothing was changed.
            You can submit it again.
          </p>
        </div>
      )}
      {canSubmit && (
        <div className="di-mr-actions">
          <Act testId="di-mr-submit" onAct={() => control.submitDraft(turn)} held={held} primary>
            {revising !== null
              ? 'Submit as the revision of the pending proposal'
              : 'Submit as a proposal of record'}
          </Act>
          {revising !== null && (
            <span className="di-mr-hint" data-testid="di-mr-revising-note">
              This replaces <code>{revising}</code>: it is withdrawn and permanently closed, and
              this becomes a separate, new proposal.
            </span>
          )}
        </div>
      )}
    </div>
  );
}

export function TurnView({
  turn,
  latestDraftTurn,
  control,
  meanings,
}: {
  turn: MrTurn;
  latestDraftTurn: string | null;
  control: DiMissionRequestControl;
  meanings: Record<string, string>;
}) {
  const stateWords = turnStateWords(turn);
  // The assistant's declared questions read as questions for a person; the exact reply (fence and all) is one disclosure away and is never rewritten.
  // Only when the panel itself read questions from this reply AND exactly one closed block is found; any other reply is shown exactly as written.
  const prose =
    turn.assistantText !== null && turn.declaredQuestions.length > 0
      ? replyWithoutQuestionsBlock(turn.assistantText)
      : null;
  // An OLDER reply with technical blocks (the panel declared no questions in it, and did not refuse a block): its prose is shown without the blocks, with a note, and the
  // exact original is one disclosure away. VIEW ONLY: nothing in a block is parsed, listed or counted, and an unclosed or malformed fence, or a reply the panel refused, is
  // shown exactly as written.
  const earlier =
    turn.assistantText !== null &&
    turn.declaredQuestions.length === 0 &&
    (turn.questionsStatus === 'not_reported' || turn.questionsStatus === 'none_declared')
      ? replyWithoutFencedBlocks(turn.assistantText)
      : null;
  return (
    <li className="di-mr-turn" data-testid={`di-mr-turn-${turn.sequence}`}>
      <div className="di-mr-said" data-who="you">
        <span className="di-mr-who">You</span>
        <Untrusted text={turn.userText} />
      </div>
      {turn.assistantText !== null && (
        <div
          className="di-mr-said"
          data-who="assistant"
          data-testid={`di-mr-assistant-${turn.sequence}`}
          // The meaning of an assistant reply (drafting only; not a decision; no authority) is said once above the transcript and offered here, not repeated as a long label.
          aria-describedby="di-mr-drafting-note"
        >
          <span className="di-mr-who">Assistant</span>
          {prose === null && earlier !== null ? (
            <>
              {earlier.prose !== '' && <Untrusted text={earlier.prose} />}
              <p
                className="di-mr-technical-note"
                data-testid={`di-mr-technical-note-${turn.sequence}`}
              >
                {technicalBlockNote(earlier.blocks)}
              </p>
              <details data-testid={`di-mr-earlier-reply-${turn.sequence}`}>
                <summary>The exact original reply</summary>
                <Untrusted text={turn.assistantText} />
              </details>
            </>
          ) : prose === null ? (
            <Untrusted text={turn.assistantText} />
          ) : (
            <>
              {prose !== '' && <Untrusted text={prose} />}
              <div data-testid={`di-mr-questions-${turn.sequence}`}>
                <p>
                  {turn.declaredQuestions.length === 1
                    ? 'The assistant asked a question:'
                    : `The assistant asked ${turn.declaredQuestions.length} questions:`}
                </p>
                <ol className="di-mr-questions">
                  {turn.declaredQuestions.map((q) => (
                    <li key={q} data-testid="di-mr-question">
                      <Untrusted text={q} />
                    </li>
                  ))}
                </ol>
              </div>
              <details data-testid={`di-mr-raw-reply-${turn.sequence}`}>
                <summary>The assistant’s exact reply (technical detail)</summary>
                <Untrusted text={turn.assistantText} />
              </details>
            </>
          )}
        </div>
      )}
      {stateWords !== null && (
        <div
          className="di-mr-box"
          data-tone={turn.state === 'RESERVED' ? 'info' : 'warn'}
          data-testid={`di-mr-turn-state-${turn.sequence}`}
        >
          <p>{stateWords}</p>
          {turn.outcomeDetail !== null && turn.state !== 'RESERVED' && (
            <p className="di-mr-hint">
              <Untrusted text={turn.outcomeDetail} />
            </p>
          )}
        </div>
      )}
      {turn.draftProblem !== null && (
        <div className="di-mr-box" data-tone="warn" data-testid="di-mr-draft-problem">
          <p>
            <strong>The assistant tried to propose a Mission, but it was not valid.</strong> Nothing
            was recorded.
          </p>
          <Untrusted text={turn.draftProblem} />
        </div>
      )}
      {turn.draft !== null && (
        <DraftCard
          turn={turn}
          latest={turn.turnId === latestDraftTurn}
          control={control}
          meanings={meanings}
        />
      )}
    </li>
  );
}

function AskTab({
  control,
  meanings,
  composerRef,
}: {
  control: DiMissionRequestControl;
  meanings: Record<string, string>;
  composerRef: RefObject<HTMLTextAreaElement | null>;
}) {
  const { state } = control;
  const turns = state.conversation.turns;
  const busy = turns.some((t) => t.state === 'RESERVED');
  const problem = composerProblem(state.composer);
  const held = busy || isPending(state, 'turn') || state.unvouched !== null;
  const sendable = problem === null && !held;
  const latestDraftTurn = [...turns].reverse().find((t) => t.draft !== null)?.turnId ?? null;
  const lastRef = useRef<HTMLLIElement | null>(null);
  const seen = useRef(0);
  useEffect(() => {
    if (turns.length > seen.current) lastRef.current?.scrollIntoView({ block: 'nearest' });
    seen.current = turns.length;
  }, [turns.length]);
  const hint = sendable
    ? 'Press Send, or Ctrl+Enter. The assistant can ask questions and draft a proposal; it cannot approve or start anything.'
    : busy || isPending(state, 'turn')
      ? 'Waiting for the assistant to answer your last message.'
      : state.unvouched !== null
        ? 'Not connected to the office, so nothing can be sent now.'
        : (problem ?? '');
  return (
    <div
      className="di-mr-body-section"
      role="tabpanel"
      id="di-mr-panel-ask"
      aria-labelledby="di-mr-tab-ask"
    >
      <p className="di-mr-note" id="di-mr-drafting-note" data-testid="di-mr-drafting-note">
        {MR_DRAFTING_NOTE}
      </p>
      {state.revising !== null && (
        <div className="di-mr-box" data-tone="warn" data-testid="di-mr-revising">
          <p>
            <strong>
              Revising <code>{state.revising}</code>.
            </strong>{' '}
            Describe what should change. When you submit the new draft, the pending proposal is
            withdrawn and permanently closed, and the new draft becomes a separate proposal. Nothing
            is edited in place.
          </p>
          <div className="di-mr-actions">
            <button
              type="button"
              onClick={() => control.revise(null)}
              data-testid="di-mr-cancel-revision"
            >
              Stop revising and keep that proposal
            </button>
          </div>
        </div>
      )}
      {omittedWords(state.conversation) !== null && (
        <p className="di-mr-box" data-tone="warn" role="note" data-testid="di-mr-turns-omitted">
          {omittedWords(state.conversation)}
        </p>
      )}
      <ul className="di-mr-log" aria-label="The conversation" data-testid="di-mr-log">
        {turns.length === 0 && (
          <li className="di-mr-hint" data-testid="di-mr-empty">
            Nothing has been said yet. Describe the work you want done in ordinary words. The
            assistant may ask you questions first, and will draft a proposal when the request is
            clear. A draft is only a draft until you submit it.
          </li>
        )}
        {turns.map((turn, index) => (
          <div
            key={turn.turnId}
            ref={
              index === turns.length - 1
                ? (lastRef as unknown as RefObject<HTMLDivElement>)
                : undefined
            }
            style={{ display: 'contents' }}
          >
            <TurnView
              turn={turn}
              latestDraftTurn={latestDraftTurn}
              control={control}
              meanings={meanings}
            />
          </div>
        ))}
      </ul>
      <div className="di-mr-section">
        <label htmlFor="di-mr-composer">Tell the {MR_NAME} the work you want done</label>
        <textarea
          id="di-mr-composer"
          ref={composerRef}
          data-testid="di-mr-composer"
          value={state.composer}
          aria-describedby="di-mr-send-hint"
          onChange={(event) => control.setComposer(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === 'Enter' && (event.ctrlKey || event.metaKey)) {
              event.preventDefault();
              if (sendable) control.sendTurn();
            }
          }}
        />
        <p id="di-mr-send-hint" className="di-mr-hint" data-testid="di-mr-send-hint">
          {hint}
        </p>
        <div className="di-mr-actions">
          <Act
            testId="di-mr-send"
            onAct={control.sendTurn}
            available={problem === null}
            held={held}
            primary
            describedBy="di-mr-send-hint"
          >
            Send
          </Act>
          <button
            type="button"
            onClick={control.newConversation}
            data-testid="di-mr-new-conversation"
          >
            Start a new conversation
          </button>
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------------------------
// requests

function stageTone(stage: string): 'info' | 'ok' | 'warn' | 'block' {
  switch (stage) {
    case 'verified':
      return 'ok';
    case 'approval_uncertain':
    case 'dispatch_uncertain':
    case 'withdrawal_incomplete':
    case 'pending_proof':
      return 'warn';
    case 'withdrawn':
    case 'revised':
    case 'expired':
    case 'blocked':
    case 'run_cancelled':
    case 'closed_elsewhere':
    case 'unreadable':
      return 'block';
    default:
      return 'info';
  }
}

const ACTION_WORDS: Record<string, string> = {
  dispatch: 'Start the run',
  observe: 'Check whether the team started',
  reconcile: "Check Herdr's records (this starts nothing)",
  verify: "Record DI's verification",
  check_approval: "Check the approval against Mission Core's record",
};
const ACTION_HINTS: Record<string, string> = {
  dispatch:
    'The start is recorded first (durable intent), then requested. That is not a guarantee it happens exactly once: if the outcome is ever unknown it is checked, never repeated.',
  observe: 'Reads whether the team is running. Starts nothing.',
  reconcile:
    "Looks for exactly one team that this request's start produced. It never starts another.",
  verify:
    "DI decides from a fresh read of the team's own files. Once the team has stopped, recording this decision is final for this run.",
  check_approval: 'Reads Mission Core. It never applies an approval again.',
};

function DecisionBlock({
  view,
  control,
  nowMs,
}: {
  view: MrView;
  control: DiMissionRequestControl;
  nowMs: number;
}) {
  const { state } = control;
  const ref = view.displayed.requestRef;
  const meanings = state.overview?.capabilities.scopeMeanings ?? {};
  const server = serverNowSeconds(state.overview, state.overviewAtMs, nowMs);
  const offer = view.offer;
  const status = offerState(view, server);
  const exact = isDecisionWord(state.typed);
  const held = actionPending(state, 'decide', ref);
  const reading = isPending(state, 'offer', ref);
  // A proposal whose rules cannot be shown in full is never approved from here (round 27 F1).
  const blocked = decisionBlockedWords(view);
  const ready = exact && status === 'live' && !held && blocked === null;
  const left = offer === null ? null : remainingSeconds(offer.expiresAt, server);
  const hintId = 'di-mr-approve-hint';
  const hint = held
    ? 'Sending your decision…'
    : blocked !== null
      ? 'Approval is refused for this proposal; the reason is stated above.'
      : status === 'missing'
      ? 'Reading the exact offer that approving would bind…'
      : status === 'expired'
        ? 'This offer has expired, so it cannot be approved. Read the offer again to get a new window.'
        : exact
          ? 'Ready. Pressing Approve sends this word, bound to exactly the proposal and the offer shown here.'
          : `Type exactly the word "${MR_DECISION_WORD}" to enable Approve. Anything else is not a decision.`;
  return (
    <section className="di-mr-section" aria-label="Your decision" data-testid="di-mr-decision">
      <h4>Your decision</h4>
      <p data-testid="di-mr-approval-binding">
        <strong>
          You are approving revision {view.displayed.revision} of this proposal (digest{' '}
          <code>{shortDigest(view.displayed.digest)}</code>).
        </strong>
      </p>
      <p>
        Approving lets DI prepare an isolated copy of the repository and start an engineering team
        on exactly the proposal above, with only the permissions listed in the offer below.
      </p>
      <div
        className="di-mr-box"
        data-tone={status === 'expired' ? 'block' : 'info'}
        data-testid="di-mr-offer"
      >
        <h4>The offer you would be approving</h4>
        {offer === null ? (
          <p data-testid="di-mr-offer-reading">Reading the offer…</p>
        ) : (
          <dl className="di-mr-fields">
            <div>
              <dt>Valid until</dt>
              <dd data-testid="di-mr-offer-expiry">
                {new Date(offer.expiresAt * 1000).toLocaleTimeString()}
                {left !== null &&
                  (left > 0 ? ` (${countdownLeft(left)} left)` : ' (it has expired)')}
              </dd>
            </div>
            <div>
              <dt>What it permits</dt>
              <dd>
                <ul>
                  {offer.scope.map((scope) => (
                    <li key={scope}>
                      <ScopeLine scope={scope} meanings={meanings} />
                    </li>
                  ))}
                </ul>
              </dd>
            </div>
            <div>
              <dt>Repository</dt>
              <dd>{offer.repositoryUrl ?? 'None is configured.'}</dd>
            </div>
            <div>
              <dt>Delivery</dt>
              <dd>
                {offer.deliveryTargets.length === 0
                  ? 'None. Nothing is pushed, merged or released.'
                  : offer.deliveryTargets.join(', ')}
              </dd>
            </div>
            <div>
              <dt>What counts as done: the proof rules this offer binds</dt>
              <dd>
                <ProofRules
                  proof={
                    view.displayed.proposal?.proof ?? {
                      unrepresented: 'the proposal itself is not shown',
                      contract: null,
                    }
                  }
                  testId="di-mr-offer-proof"
                />
              </dd>
            </div>
            <div>
              <dt>Offer digest</dt>
              <dd>
                <Digest digest={offer.offerDigest} />
              </dd>
            </div>
          </dl>
        )}
        <p className="di-mr-hint">
          The approval binds exactly this expiry: it cannot be used after it, so start the run
          before then. Reading for as long as you need is fine while the offer is valid. While it is
          valid, every window is shown this same offer; reading it again does not extend it.
        </p>
        <div className="di-mr-actions">
          <Act testId="di-mr-read-offer" onAct={() => control.readOffer(ref)} held={reading}>
            Read the offer again
          </Act>
        </div>
      </div>
      {blocked !== null && (
        <div className="di-mr-box" data-tone="block" data-testid="di-mr-decision-blocked">
          <p>
            <strong>This proposal cannot be approved from this window.</strong>{' '}
            <Untrusted text={blocked} />
          </p>
        </div>
      )}
      <div className="di-mr-box" data-tone="warn" data-testid="di-mr-provenance">
        <p>
          <strong>
            How this approval is recorded: operator attestation, not independently verified.
          </strong>{' '}
          This panel relays the word you type. DI cannot confirm who typed it, and a mistaken or
          malicious process running as you could fabricate it. Nothing here is a verified human
          signature.
        </p>
      </div>
      <label htmlFor="di-mr-word">Type the word {MR_DECISION_WORD} to approve this proposal</label>
      <input
        id="di-mr-word"
        data-testid="di-mr-word"
        type="text"
        autoComplete="off"
        autoCorrect="off"
        autoCapitalize="off"
        spellCheck={false}
        value={state.typed}
        aria-describedby={hintId}
        onChange={(event) => control.setTyped(event.target.value)}
        onKeyDown={(event) => {
          if (event.key === 'Enter') {
            event.preventDefault();
            if (ready) control.decide();
          }
        }}
      />
      <p id={hintId} className="di-mr-hint" data-testid="di-mr-approve-hint">
        {hint}
      </p>
      <div className="di-mr-actions">
        <Act
          testId="di-mr-approve"
          onAct={control.decide}
          available={exact && status === 'live' && blocked === null}
          held={held}
          primary
          describedBy={hintId}
        >
          Approve
        </Act>
      </div>
    </section>
  );
}

/** "12 min 5 s" / "40 s": how long a valid offer has left. */
function countdownLeft(seconds: number): string {
  const minutes = Math.floor(seconds / 60);
  return minutes > 0 ? `${minutes} min ${seconds % 60} s` : `${seconds} s`;
}

function WithdrawBlock({
  view,
  control,
  canRevise,
}: {
  view: MrView;
  control: DiMissionRequestControl;
  canRevise: boolean;
}) {
  const { state } = control;
  const ref = view.displayed.requestRef;
  const [confirming, setConfirming] = useState(false);
  const askRef = useRef<HTMLButtonElement | null>(null);
  const confirmRef = useRef<HTMLButtonElement | null>(null);
  const keepRef = useRef<HTMLButtonElement | null>(null);
  const held = actionPending(state, 'withdraw', ref);
  const wasConfirming = useRef(false);
  useEffect(() => {
    // Focus is handed over deliberately: the button that was pressed is replaced by the
    // confirmation, and back again when it is declined.
    if (confirming) confirmRef.current?.focus();
    else if (wasConfirming.current) askRef.current?.focus();
    wasConfirming.current = confirming;
  }, [confirming]);
  return (
    <section
      className="di-mr-section"
      aria-label="Withdraw or revise"
      data-testid="di-mr-withdraw-block"
    >
      <h4>Withdraw or change this proposal</h4>
      <p className="di-mr-hint">
        Withdrawing takes a pending proposal back. It is permanently closed and nothing is approved.
        It is not a recorded decision: nobody with authenticated authority is involved. To change
        it, revise: you describe the change and a separate new proposal replaces this one. Nothing
        is edited in place.
      </p>
      {confirming ? (
        <div
          className="di-mr-box"
          data-tone="warn"
          role="group"
          aria-label="Confirm withdrawal"
          data-testid="di-mr-withdraw-confirm-box"
          onKeyDown={(event) => {
            if (event.key === 'Escape') {
              event.stopPropagation();
              event.preventDefault();
              setConfirming(false);
            }
          }}
        >
          <p>
            <strong>Withdraw this proposal?</strong> It is permanently closed. This cannot be
            undone.
          </p>
          <div className="di-mr-actions">
            <button
              type="button"
              ref={confirmRef}
              data-danger="true"
              data-testid="di-mr-withdraw-confirm"
              aria-disabled={held ? 'true' : undefined}
              onClick={() => {
                if (held) return;
                control.withdraw();
              }}
            >
              {held ? 'Withdrawing… (waiting for the answer)' : 'Yes, withdraw it'}
            </button>
            <button
              type="button"
              ref={keepRef}
              data-testid="di-mr-withdraw-keep"
              onClick={() => setConfirming(false)}
            >
              No, keep it
            </button>
          </div>
        </div>
      ) : (
        <div className="di-mr-actions">
          <button
            type="button"
            ref={askRef}
            data-danger="true"
            data-testid="di-mr-withdraw"
            onClick={() => setConfirming(true)}
          >
            Withdraw this proposal
          </button>
          {canRevise && (
            <button type="button" data-testid="di-mr-revise" onClick={() => control.revise(ref)}>
              Ask for changes (replace this proposal)
            </button>
          )}
        </div>
      )}
    </section>
  );
}

function RunBlock({
  view,
  control,
  nowMs,
}: {
  view: MrView;
  control: DiMissionRequestControl;
  nowMs: number;
}) {
  const { state } = control;
  const ref = view.displayed.requestRef;
  const actions = offeredActions(state, ref);
  const lifecycle = view.latest.lifecycle;
  const run = view.latest.run;
  const server = serverNowSeconds(state.overview, state.overviewAtMs, nowMs);
  const left = remainingSeconds(lifecycle.approvalExpiresAt, server);
  const asked = useRef(false);
  useEffect(() => {
    if (left !== null && left <= 0 && lifecycle.stage === 'approved' && !asked.current) {
      asked.current = true;
      control.refresh();
    }
  }, [left, lifecycle.stage, control]);
  const runActions = (['dispatch', 'observe', 'reconcile', 'verify'] as const).filter((a) =>
    actions.includes(a),
  );
  return (
    <section className="di-mr-section" aria-label="The run" data-testid="di-mr-run">
      <h4>Progress</h4>
      <Journey stage={lifecycle.stage} />
      {lifecycle.stage === 'approved' && left !== null && (
        <p className="di-mr-hint" data-testid="di-mr-countdown">
          {countdownWords(left)}
        </p>
      )}
      {runActions.length > 0 && (
        <div className="di-mr-actions">
          {runActions.map((command) => (
            <div key={command} style={{ display: 'contents' }}>
              <Act
                testId={`di-mr-${command}`}
                onAct={() => control.run(command)}
                held={actionPending(state, command, ref)}
                primary={command === 'dispatch'}
              >
                {ACTION_WORDS[command]}
              </Act>
            </div>
          ))}
        </div>
      )}
      {runActions.map((command) => (
        <p key={command} className="di-mr-hint">
          <strong>{ACTION_WORDS[command]}:</strong> {ACTION_HINTS[command]}
        </p>
      ))}
      {actions.includes('check_approval') && (
        <div className="di-mr-actions">
          <Act
            testId="di-mr-check-approval"
            onAct={control.checkApproval}
            held={actionPending(state, 'check_approval', ref)}
            primary
          >
            {ACTION_WORDS.check_approval}
          </Act>
          <span className="di-mr-hint">{ACTION_HINTS.check_approval}</span>
        </div>
      )}
      {run !== null && (
        <details className="di-mr-facts" data-testid="di-mr-run-facts">
          <summary>What DI has recorded about the run</summary>
          <ul>
            <li>Phase: {run.phase}</li>
            {run.firstObservation?.at != null && (
              <li>
                First observed running at{' '}
                {new Date(run.firstObservation.at * 1000).toLocaleTimeString()} (a past observation,
                not a claim about now)
              </li>
            )}
            {run.latestObservation?.at != null && (
              <li>
                Latest recorded observation at{' '}
                {new Date(run.latestObservation.at * 1000).toLocaleTimeString()}
              </li>
            )}
            {run.holdStatement !== null && <li data-testid="di-mr-hold">{run.holdStatement}</li>}
            {run.paused && (
              <li>Paused: only DI&apos;s own progression is paused; the team is not suspended.</li>
            )}
            {run.blockers.map((b) => (
              <li key={b.code}>
                Waiting on proof ({b.code}): <Untrusted text={b.detail} />
              </li>
            ))}
            {run.progressNote !== null && <li>{run.progressNote}</li>}
            <li>
              Delivered:{' '}
              {run.delivered ? 'yes' : 'no. Nothing has been pushed, merged or released.'}
            </li>
          </ul>
        </details>
      )}
    </section>
  );
}

/** The word is already typed on the offer this window read. Another window's approval drops
 *  `decide` from the latest actions; the controls stay so this window can still submit that offer. */
function holdingDecision(view: MrView, state: { typed: string }): boolean {
  return view.offer !== null && isDecisionWord(state.typed);
}

function Detail({
  view,
  control,
  nowMs,
  headingRef,
  wide,
}: {
  view: MrView;
  control: DiMissionRequestControl;
  nowMs: number;
  headingRef: RefObject<HTMLHeadingElement | null>;
  wide: boolean;
}) {
  const { state } = control;
  const request = view.displayed;
  const latest = view.latest;
  const ref = request.requestRef;
  const actions = offeredActions(state, ref);
  const lifecycle = view.stale ? request.lifecycle : latest.lifecycle;
  const meanings = state.overview?.capabilities.scopeMeanings ?? {};
  const tone = stageTone(lifecycle.stage);
  const readAt = agoWords(view.readAtMs, nowMs);
  const title = plainTitle(request.proposal?.objective);
  // The panel's words may open with one sentence of provenance. It is exact and stays, in the references disclosure; the rest is the status.
  const words = splitRecordSentence(lifecycle.detail);
  return (
    <div
      data-testid="di-mr-detail"
      className="di-mr-detail"
      style={{ display: 'flex', flexDirection: 'column', gap: 12 }}
    >
      <h3 ref={headingRef} tabIndex={-1} data-testid="di-mr-detail-heading">
        {title === null ? 'This request' : <Line text={title} />}
      </h3>
      {view.stale && (
        <div className="di-mr-box" data-tone="block" role="alert" data-testid="di-mr-stale">
          <p>
            <strong>This display is out of date.</strong> The proposal of record is no longer the
            one shown below (it is now revision {latest.revision}, digest{' '}
            <code>{latest.digest.slice(0, 12)}…</code>). Nothing here can be approved until you read
            the current proposal. What you typed was cleared.
          </p>
          <div className="di-mr-actions">
            <button
              type="button"
              data-primary="true"
              onClick={() => control.acknowledge(ref)}
              data-testid="di-mr-acknowledge"
            >
              Read the current proposal
            </button>
          </div>
        </div>
      )}
      <div className="di-mr-box" data-tone={tone} data-testid="di-mr-stage">
        <p>
          <strong data-testid="di-mr-stage-label">{lifecycle.label}</strong>
        </p>
        <p data-testid="di-mr-stage-detail">{words.rest}</p>
        {lifecycle.notice !== null && (
          <p data-testid="di-mr-stage-notice">
            <strong>Note:</strong> {lifecycle.notice}
          </p>
        )}
        {lifecycle.provenanceProof !== null && (
          <p data-testid="di-mr-stage-provenance">
            Recorded as: {lifecycle.provenanceProof.replace(/_/g, ' ')}. {lifecycle.residualRisk}
          </p>
        )}
      </div>
      <details className="di-mr-refs" data-testid="di-mr-refs">
        <summary data-testid="di-mr-refs-summary">Exact references (technical)</summary>
        <div className="di-mr-refs-body">
          <p className="di-mr-hint" data-testid="di-mr-read-at">
            Read from the record {readAt}.
          </p>
          {words.record !== null && <p data-testid="di-mr-stage-record">{words.record}</p>}
          <div data-testid="di-mr-proposal-source">
            <SourceFacts
              source={{
                requestRef: ref,
                missionId: request.missionId,
                revision: request.revision,
                digest: request.digest,
              }}
            />
          </div>
        </div>
      </details>
      {request.startCause !== null && (
        <StartCauseView cause={request.startCause} closed={lifecycle.terminal} />
      )}
      {request.proposal !== null ? (
        <ProposalView
          proposal={request.proposal}
          source={{
            requestRef: ref,
            missionId: request.missionId,
            revision: request.revision,
            digest: request.digest,
          }}
          meanings={meanings}
          heading="The proposal"
          showSource={false}
        />
      ) : (
        <div className="di-mr-box" data-tone="warn">
          <p>
            The text of this proposal could not be shown. Its identity: {request.missionId},
            revision {request.revision}.
          </p>
        </div>
      )}
      {request.supersedes !== null && (
        <p data-testid="di-mr-supersedes">
          This replaces <code>{request.supersedes}</code>, a separate record that was withdrawn.
        </p>
      )}
      {request.supersededBy !== null && (
        <p data-testid="di-mr-superseded-by">
          Replaced by{' '}
          <button
            type="button"
            onClick={() => control.select(request.supersededBy)}
            data-testid="di-mr-open-replacement"
          >
            <code>{request.supersededBy}</code>
          </button>
          , a separate record.
        </p>
      )}
      {!view.stale && (actions.includes('decide') || holdingDecision(view, control.state)) && (
        <DecisionBlock view={view} control={control} nowMs={nowMs} />
      )}
      {!view.stale && actions.includes('withdraw') && (
        <WithdrawBlock view={view} control={control} canRevise />
      )}
      {!view.stale && <RunBlock view={view} control={control} nowMs={nowMs} />}
      {!view.stale && actions.includes('result') && (
        <section className="di-mr-section" aria-label="The result">
          {view.result === null ? (
            <div className="di-mr-actions">
              <Act
                testId="di-mr-result-load"
                onAct={control.loadResult}
                held={actionPending(state, 'result', ref)}
                primary
              >
                Read the verified result
              </Act>
            </div>
          ) : (
            <ResultView result={view.result} wide={wide} />
          )}
        </section>
      )}
    </div>
  );
}

function RequestsTab({
  control,
  nowMs,
  detailHeadingRef,
}: {
  control: DiMissionRequestControl;
  nowMs: number;
  detailHeadingRef: RefObject<HTMLHeadingElement | null>;
}) {
  const { state } = control;
  const requests = state.overview?.requests ?? [];
  const selected = state.selectedRef !== null ? state.views[state.selectedRef] : undefined;
  return (
    <div
      className="di-mr-body-section"
      role="tabpanel"
      id="di-mr-panel-requests"
      aria-labelledby="di-mr-tab-requests"
    >
      <div className="di-mr-requests">
        <div>
          <h3 id="di-mr-list-heading">Proposals and their progress</h3>
          {state.overview === null ? (
            <p className="di-mr-hint" data-testid="di-mr-list-empty">
              {state.availability.kind === 'unavailable'
                ? 'No requests can be read now.'
                : 'Reading requests…'}
            </p>
          ) : requests.length === 0 ? (
            <p className="di-mr-hint" data-testid="di-mr-list-empty">
              No requests yet. Describe the work you want done under Ask, and submit the proposal it
              drafts.
            </p>
          ) : (
            <ul
              className="di-mr-list"
              aria-labelledby="di-mr-list-heading"
              data-testid="di-mr-list"
            >
              {requests.map((r) => (
                <li key={r.requestRef}>
                  <button
                    type="button"
                    aria-current={state.selectedRef === r.requestRef ? 'true' : undefined}
                    data-testid={`di-mr-request-${r.requestRef}`}
                    onClick={() => control.select(r.requestRef)}
                  >
                    <span className="di-mr-row-objective">{r.objective}</span>
                    <span className="di-mr-row-stage">
                      {r.label}
                      {r.actions.some(
                        (a) => a === 'decide' || a === 'check_approval' || a === 'verify',
                      )
                        ? ' · needs you'
                        : ''}
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          )}
          {state.overview?.requestsTruncated === true && (
            <p className="di-mr-hint">Only the newest requests are listed.</p>
          )}
        </div>
        <div>
          {state.selectedRef === null ? (
            <p className="di-mr-hint" data-testid="di-mr-nothing-selected">
              Choose a request to read it exactly, and to decide or follow it.
            </p>
          ) : selected === undefined ? (
            <p className="di-mr-hint" data-testid="di-mr-reading-request">
              Reading this request…
            </p>
          ) : (
            <Detail
              view={selected}
              control={control}
              nowMs={nowMs}
              headingRef={detailHeadingRef}
              wide={state.wide}
            />
          )}
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------------------------
// the workspace

export interface DiMissionRequestProps {
  control: DiMissionRequestControl;
  /** Whether the office panel is hidden (the workspace then docks to the window's edge). */
  panelCollapsed: boolean;
  /** Where focus returns when the workspace closes. */
  returnFocusTo: RefObject<HTMLElement | null>;
  /** The office is an explicit fixture preview: the workspace says so at its top. */
  fixture?: boolean;
}

function Workspace({
  control,
  panelCollapsed,
  returnFocusTo,
  fixture = false,
}: DiMissionRequestProps) {
  const { state } = control;
  const rootRef = useRef<HTMLElement | null>(null);
  const headingRef = useRef<HTMLHeadingElement | null>(null);
  const detailHeadingRef = useRef<HTMLHeadingElement | null>(null);
  const composerRef = useRef<HTMLTextAreaElement | null>(null);
  const tabRefs = useRef<Record<MrTab, HTMLButtonElement | null>>({ ask: null, requests: null });
  const viewport = useViewportWidth();
  const nowMs = useNowMs();
  const layout = layoutFor(viewport, panelCollapsed, state.wide, DI_PANEL_WIDTH_PX);

  useFocusRescue(rootRef, headingRef);

  // The workspace takes focus when it opens, and gives it back when it closes.
  useLayoutEffect(() => {
    const back = returnFocusTo.current;
    headingRef.current?.focus();
    return () => {
      // The opener can be gone: the sidebar was hidden while the workspace was open. The control that brings the sidebar back is
      // then the sensible, visible place for focus, never <body>.
      const target =
        back && back.isConnected
          ? back
          : document.querySelector<HTMLElement>('[data-testid="di-office-panel-collapsed"]');
      target?.focus();
    };
  }, [returnFocusTo]);

  // A transition that removes what had focus hands it to a named place:
  //  - submitting a draft moves to the new request's heading;
  //  - starting a revision moves to the composer.
  const lastSelected = useRef<string | null>(state.selectedRef);
  const selectedHasView =
    state.selectedRef !== null && state.views[state.selectedRef] !== undefined;
  useEffect(() => {
    if (state.selectedRef !== lastSelected.current && selectedHasView && state.tab === 'requests') {
      detailHeadingRef.current?.focus({ preventScroll: false });
    }
    lastSelected.current = state.selectedRef;
  }, [state.selectedRef, selectedHasView, state.tab]);
  const lastRevising = useRef(state.revising);
  useEffect(() => {
    if (state.revising !== null && lastRevising.current === null) composerRef.current?.focus();
    lastRevising.current = state.revising;
  }, [state.revising]);

  const needing =
    state.overview?.requests.filter((r) =>
      r.actions.some((a) => a === 'decide' || a === 'check_approval' || a === 'verify'),
    ).length ?? 0;
  const meanings = state.overview?.capabilities.scopeMeanings ?? {};
  // The overview's claim about progression is shown only while this window can vouch for the panel;
  // when it cannot, the line says UNAVAILABLE rather than showing the last thing it read as current.
  const reachable =
    state.unvouched === null &&
    state.availability.kind !== 'unavailable' &&
    state.overview !== null;
  const progression =
    state.availability.kind === 'unknown' && state.overview === null
      ? null
      : progressionWords(
          state.overview?.capabilities.progression ?? null,
          state.overview?.capabilities.runAvailable ?? true,
          reachable,
        );

  const onTabKey = (event: ReactKeyboardEvent<HTMLButtonElement>): void => {
    const order: MrTab[] = ['ask', 'requests'];
    const at = order.indexOf(state.tab);
    let next: MrTab | null = null;
    if (event.key === 'ArrowRight') next = order[(at + 1) % order.length];
    else if (event.key === 'ArrowLeft') next = order[(at + order.length - 1) % order.length];
    else if (event.key === 'Home') next = order[0];
    else if (event.key === 'End') next = order[order.length - 1];
    if (next !== null) {
      event.preventDefault();
      control.setTab(next);
      tabRefs.current[next]?.focus();
    }
  };

  const status =
    state.unvouched !== null
      ? {
          tone: 'block',
          text: 'This window cannot vouch for the requests below right now (it is not connected to a current office). Nothing is shown as current and nothing can be sent.',
        }
      : state.availability.kind === 'unavailable'
        ? { tone: 'block', text: state.availability.reason }
        : state.availability.kind === 'unknown'
          ? { tone: 'info', text: 'Reading the request panel…' }
          : null;

  const style = layout.covering
    ? { right: 0, left: 0 }
    : { right: layout.rightPx, width: layout.widthPx };
  return (
    <section
      ref={rootRef}
      className="di-mr"
      role="complementary"
      aria-label={`${MR_NAME} requests`}
      data-testid="di-mr"
      data-wide={state.wide ? 'true' : 'false'}
      data-covering={layout.covering ? 'true' : 'false'}
      data-split={twoColumnReading(layout, state.wide) ? 'true' : 'false'}
      style={style}
      // Keys typed in the workspace belong to it: none reaches the office behind.
      onKeyDown={(event) => {
        event.stopPropagation();
        if (event.key === 'Escape' && !event.defaultPrevented) {
          event.preventDefault();
          control.close();
        }
      }}
      onKeyUp={(event) => event.stopPropagation()}
    >
      <header className="di-mr-head">
        <div className="di-mr-titles">
          <h2 className="di-mr-title" ref={headingRef} tabIndex={-1} data-testid="di-mr-heading">
            {MR_NAME}
          </h2>
          <p className="di-mr-tagline" data-testid="di-mr-tagline">
            {MR_TAGLINE}
          </p>
        </div>
        <div className="di-mr-head-tools">
          <button
            type="button"
            aria-pressed={state.wide}
            onClick={() => control.setWide(!state.wide)}
            data-testid="di-mr-wide"
          >
            {state.wide ? 'Narrower reading' : 'Wider reading'}
          </button>
          <button type="button" onClick={control.close} data-testid="di-mr-close">
            Close requests
          </button>
        </div>
      </header>
      {layout.covering && (
        <p className="di-mr-covering" data-testid="di-mr-covering-note">
          {COVERING_WORDS}
        </p>
      )}
      {status !== null && (
        <p
          className="di-mr-status"
          data-tone={status.tone}
          data-testid="di-mr-status"
          role="status"
        >
          {status.text}
        </p>
      )}
      <div role="tablist" aria-label="Mission requests sections" className="di-mr-tabs">
        <button
          type="button"
          role="tab"
          id="di-mr-tab-ask"
          aria-selected={state.tab === 'ask'}
          aria-controls="di-mr-panel-ask"
          tabIndex={state.tab === 'ask' ? 0 : -1}
          ref={(el) => {
            tabRefs.current.ask = el;
          }}
          onClick={() => control.setTab('ask')}
          onKeyDown={onTabKey}
          className="di-mr-tab"
          data-testid="di-mr-tab-ask"
        >
          Ask
        </button>
        <button
          type="button"
          role="tab"
          id="di-mr-tab-requests"
          aria-selected={state.tab === 'requests'}
          aria-controls="di-mr-panel-requests"
          tabIndex={state.tab === 'requests' ? 0 : -1}
          ref={(el) => {
            tabRefs.current.requests = el;
          }}
          onClick={() => control.setTab('requests')}
          onKeyDown={onTabKey}
          className="di-mr-tab"
          data-testid="di-mr-tab-requests"
        >
          Requests
          {needing > 0 && (
            <span className="di-mr-badge" data-testid="di-mr-needs-you">
              {needing} {needing === 1 ? 'request needs' : 'requests need'} you
            </span>
          )}
        </button>
      </div>
      {/* ONE compact, persistent context area (never dismissable, never hidden): what is synthetic here, and what automatic progress does. Each part has its own
          row and its own disclosure; the full wording is one step away. */}
      {(fixture || progression !== null) && (
        <div
          className="di-mr-context"
          role="group"
          aria-label="About this window"
          data-testid="di-mr-context"
        >
          {fixture && (
            <div className="di-mr-ctx" data-tone="warn" role="note" data-testid="di-mr-fixture-banner">
              <span className="di-mr-chip">Synthetic</span>
              <p data-testid="di-mr-fixture-line">{MR_FIXTURE_SHORT}</p>
              <details data-testid="di-mr-fixture-details">
                <summary data-testid="di-mr-fixture-summary">The full notice</summary>
                <p>{MR_FIXTURE_WORDS}</p>
              </details>
            </div>
          )}
          {progression !== null && (
            <div
              className="di-mr-ctx"
              data-tone={progression.tone}
              data-state={progression.label}
              data-testid="di-mr-progression"
            >
              <span className="di-mr-chip">Progress {progression.label}</span>
              <p data-testid="di-mr-progression-line">{progression.text}</p>
              {/* A native disclosure: Enter or Space on the summary opens it, focus is visible, and nothing
                  here is hover- or title-only. The summary never carries the state; the line above does. */}
              <details data-testid="di-mr-progression-details">
                <summary data-testid="di-mr-progression-summary">
                  Details about automatic progress
                </summary>
                <ul>
                  {progression.details.map((line) => (
                    <li key={line}>{line}</li>
                  ))}
                </ul>
              </details>
            </div>
          )}
        </div>
      )}
      <div className="di-mr-body" data-testid="di-mr-body">
        {state.notices.length > 0 && (
          <ul className="di-mr-notices" aria-label="Notices" data-testid="di-mr-notices">
            {state.notices.map((notice) => (
              <li
                key={notice.id}
                className="di-mr-notice"
                data-kind={notice.kind}
                data-testid={`di-mr-notice-${notice.kind}`}
              >
                <span>
                  <span className="di-mr-notice-kind">
                    {notice.kind === 'unknown'
                      ? 'Outcome not known. '
                      : notice.kind === 'refusal'
                        ? 'Refused. '
                        : notice.kind === 'unavailable'
                          ? 'Not sent. '
                          : ''}
                  </span>
                  {notice.text}
                </span>
                <button
                  type="button"
                  onClick={() => control.dismiss(notice.id)}
                  aria-label="Dismiss this notice"
                  data-testid="di-mr-dismiss"
                >
                  Dismiss
                </button>
              </li>
            ))}
          </ul>
        )}
        {state.tab === 'ask' ? (
          <AskTab control={control} meanings={meanings} composerRef={composerRef} />
        ) : (
          <RequestsTab control={control} nowMs={nowMs} detailHeadingRef={detailHeadingRef} />
        )}
      </div>
      {/* Changes the person cannot see are announced: the stage of the request on display. */}
      <div
        className="di-mr-visually-hidden"
        role="status"
        aria-live="polite"
        data-testid="di-mr-live"
      >
        {state.selectedRef !== null && state.views[state.selectedRef]
          ? state.views[state.selectedRef].latest.lifecycle.label
          : ''}
      </div>
    </section>
  );
}

/** The workspace, when open. */
export function DiMissionRequest(props: DiMissionRequestProps) {
  if (!props.control.state.open) return null;
  return <Workspace {...props} />;
}
