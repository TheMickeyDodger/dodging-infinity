/**
 * Item 4 — the Mission request workspace's presentational parts.
 *
 * Every word that came from a provider, a person or a team is UNTRUSTED data and is drawn only as
 * text: React text nodes inside plain elements, never markup, never a link, never an image, never
 * an attribute. A character that could change what a reader sees (a bidirectional override, a
 * zero-width character, a control byte) is shown as a visible placeholder, never performed.
 */
import { useId, useState } from 'react';

import { visibleSegments } from './diCodeReviewModel.js';
import type { MrProofRules } from './diMissionRequestContract.js';
import { proofRulesAbsentWords, proofRuleSections } from './diMissionRequestContract.js';
import type { MrProposal, MrResult } from './diMissionRequestModel.js';
import {
  JOURNEY,
  journeyIndex,
  linesOf,
  MR_READING_LINES,
  MR_READING_LINES_WIDE,
  scopeLine,
  shortDigest,
} from './diMissionRequestModel.js';
import type { MrStartCause } from './diMissionRequestStartCause.js';
import { startCauseWords } from './diMissionRequestStartCause.js';

/** One line of untrusted text with every hidden character made visible. */
function Line({ text }: { text: string }) {
  return (
    <span className="di-mr-line">
      {visibleSegments(text).map((segment, index) =>
        segment.control ? (
          <span key={index} className="di-mr-ctl" data-testid="di-mr-control">
            {segment.text}
          </span>
        ) : (
          <span key={index}>{segment.text}</span>
        ),
      )}
    </span>
  );
}

/** Untrusted text, all of it, inert. */
export function Untrusted({ text, testId }: { text: string; testId?: string }) {
  return (
    <div className="di-mr-text" data-testid={testId}>
      {linesOf(text).map((line, index) => (
        <Line key={index} text={line} />
      ))}
    </div>
  );
}

/**
 * A long text read in place. The first lines are shown; "Show the rest" opens the whole text
 * WITHOUT moving focus or leaving the page: the button stays where it is and says what it did.
 */
export function ReadingBlock({
  text,
  wide,
  label,
  testId,
}: {
  text: string;
  wide: boolean;
  label: string;
  testId?: string;
}) {
  const [open, setOpen] = useState(false);
  const id = useId();
  const all = linesOf(text);
  const limit = wide ? MR_READING_LINES_WIDE : MR_READING_LINES;
  const long = all.length > limit;
  const shown = open || !long ? all : all.slice(0, limit);
  return (
    <div className="di-mr-reading-wrap" data-testid={testId}>
      <div
        id={id}
        className="di-mr-reading"
        data-open={open ? 'true' : 'false'}
        // A scrollable region must be reachable by keyboard and named.
        tabIndex={0}
        role="region"
        aria-label={label}
      >
        <div className="di-mr-text">
          {shown.map((line, index) => (
            <Line key={index} text={line} />
          ))}
        </div>
      </div>
      {long && (
        <button
          type="button"
          aria-expanded={open}
          aria-controls={id}
          onClick={() => setOpen((v) => !v)}
          data-testid={testId ? `${testId}-toggle` : undefined}
        >
          {open
            ? `Show less of ${label.toLowerCase()}`
            : `Show the rest of ${label.toLowerCase()} (${all.length - limit} more lines)`}
        </button>
      )}
    </div>
  );
}

/** A digest, short for reading with the whole value one disclosure away. */
export function Digest({ digest }: { digest: string }) {
  const [open, setOpen] = useState(false);
  const id = useId();
  return (
    <span>
      <code>{shortDigest(digest)}</code>{' '}
      <button
        type="button"
        aria-expanded={open}
        aria-controls={id}
        onClick={() => setOpen((v) => !v)}
        data-testid="di-mr-digest-toggle"
      >
        {open ? 'Hide the whole digest' : 'Show the whole digest'}
      </button>
      {open && (
        <code id={id} className="di-mr-digest-full" data-testid="di-mr-digest-full">
          {digest}
        </code>
      )}
    </span>
  );
}

export interface SourceIdentity {
  requestRef: string | null;
  missionId: string | null;
  revision: number | null;
  digest: string;
}

/**
 * The proposal EXACTLY as it is held: what is asked, where, what the team may do, what counts as
 * done, and the identity a decision is bound to. A draft shows no Mission yet, and says so.
 */
export function ProposalView({
  proposal,
  source,
  meanings,
  heading,
  testId = 'di-mr-proposal',
}: {
  proposal: MrProposal;
  source: SourceIdentity;
  meanings: Record<string, string>;
  heading: string;
  testId?: string;
}) {
  return (
    <section className="di-mr-section" aria-label={heading} data-testid={testId}>
      <h4>{heading}</h4>
      <dl className="di-mr-fields">
        <div>
          <dt>What you asked for</dt>
          <dd>
            <Untrusted text={proposal.objective} testId={`${testId}-objective`} />
          </dd>
        </div>
        {proposal.targetContext !== '' && (
          <div>
            <dt>Where it applies</dt>
            <dd>
              <Untrusted text={proposal.targetContext} />
            </dd>
          </div>
        )}
        <div>
          <dt>What is in scope, and what is not</dt>
          <dd>
            <Untrusted text={proposal.requestedScope} testId={`${testId}-scope`} />
          </dd>
        </div>
        <div>
          <dt>What the team may do</dt>
          <dd>
            <ul>
              {proposal.actionScope.map((scope) => (
                <li key={scope}>{scopeLine(scope, meanings)}</li>
              ))}
            </ul>
          </dd>
        </div>
        <div>
          <dt>Repository</dt>
          <dd>
            {proposal.repositoryUrl === null
              ? 'None is configured, so this proposal cannot start a run.'
              : proposal.repositoryUrl}
          </dd>
        </div>
        <div>
          <dt>Delivery</dt>
          <dd>
            {proposal.deliveryTarget === null
              ? 'None. Nothing is pushed, merged or released from here; delivery stays a separate step you control.'
              : proposal.deliveryTarget}
          </dd>
        </div>
        <div>
          <dt>What counts as done (the proof rules)</dt>
          <dd>
            <ProofRules proof={proposal.proof} testId={`${testId}-rules`} />
          </dd>
        </div>
        <div>
          <dt>Source</dt>
          <dd data-testid={`${testId}-source`}>
            {source.requestRef === null ? (
              <p>A draft in this conversation. It is not a proposal of record yet.</p>
            ) : (
              <p>
                Request <code>{source.requestRef}</code>, Mission <code>{source.missionId}</code>,
                revision {source.revision}
              </p>
            )}
            <p>
              Digest <Digest digest={source.digest} />
            </p>
          </dd>
        </div>
      </dl>
    </section>
  );
}

/**
 * Why an in-doubt Start is in doubt: what the panel could bind to this exact attempt, in sentences, or plainly that
 * the cause is not known. Inert text only; nothing in it is followed, opened or acted on.
 */
export function StartCauseView({ cause, closed }: { cause: MrStartCause; closed: boolean }) {
  const words = startCauseWords(cause, closed);
  return (
    <div
      className="di-mr-box"
      data-tone="warn"
      data-testid="di-mr-start-cause"
      data-state={cause.state}
      data-closed={closed ? 'true' : 'false'}
    >
      <p>
        <strong>{words.heading}</strong>
      </p>
      <ul>
        {words.lines.map((line, index) => (
          <li key={index}>
            <Untrusted text={line} />
          </li>
        ))}
      </ul>
      <p className="di-mr-hint" data-testid="di-mr-start-cause-note">
        {words.note}
      </p>
    </div>
  );
}

/**
 * The proof rules, every one, as plain sentences in sections; or, when they cannot be shown in full, the
 * plain statement that approving is refused. Never a summary that could read the same for two different
 * contracts, and never silence.
 */
export function ProofRules({ proof, testId }: { proof: MrProofRules; testId: string }) {
  if (proof.contract === null) {
    return (
      <div
        className="di-mr-rules"
        data-testid={`${testId}-absent`}
        data-represented={proof.unrepresented === null ? 'true' : 'false'}
      >
        <Untrusted text={proofRulesAbsentWords(proof)} />
      </div>
    );
  }
  return (
    <div className="di-mr-rules" data-testid={testId} data-represented="true">
      {proofRuleSections(proof.contract).map((section) => (
        <div key={section.title} className="di-mr-rule-section">
          <h5>{section.title}</h5>
          <ul>
            {section.lines.map((line, index) => (
              <li key={index}>
                <Untrusted text={line} />
              </li>
            ))}
          </ul>
        </div>
      ))}
    </div>
  );
}

/** Where a request is on the journey, in words as well as marks. */
export function Journey({ stage }: { stage: string }) {
  const at = journeyIndex(stage);
  if (at === null) return null;
  return (
    <ol className="di-mr-journey" aria-label="Where this request is" data-testid="di-mr-journey">
      {JOURNEY.map((step, index) => {
        const state = index < at ? 'done' : index === at ? 'now' : 'next';
        return (
          <li
            key={step.key}
            className="di-mr-step"
            data-state={state}
            aria-current={state === 'now' ? 'step' : undefined}
          >
            {state === 'done' ? 'Done: ' : state === 'now' ? 'Now: ' : 'Next: '}
            {step.label}
          </li>
        );
      })}
    </ol>
  );
}

/** The retrievable verified result, with what it is and is not. */
export function ResultView({ result, wide }: { result: MrResult; wide: boolean }) {
  if (!result.recoverable) {
    return (
      <div className="di-mr-box" data-tone="block" data-testid="di-mr-result-gone">
        <p>
          <strong>The result could not be recovered.</strong>{' '}
          {result.reason ?? 'Its record is gone or has changed.'} A digest alone is never a verified
          result, so none is shown.
        </p>
      </div>
    );
  }
  return (
    <section className="di-mr-section" aria-label="Verified result" data-testid="di-mr-result">
      <h4>Verified result</h4>
      <p className="di-mr-hint">
        DI verified this run from a fresh read of the team&apos;s own files. The review is evidence
        the team produced, not independent verification. Nothing is delivered
        {result.delivered ? '' : ': it has not been pushed, merged or released'}.
      </p>
      {result.resultText !== null && (
        <>
          <h4>What the team wrote</h4>
          <ReadingBlock
            text={result.resultText}
            wide={wide}
            label="The team's result"
            testId="di-mr-result-text"
          />
          {result.resultTruncated && (
            <p className="di-mr-hint" data-testid="di-mr-result-cut">
              This is the start of a longer result; the rest is cut for display.
            </p>
          )}
        </>
      )}
      {result.reviewText !== null && (
        <>
          <h4>The team&apos;s review</h4>
          <ReadingBlock
            text={result.reviewText}
            wide={wide}
            label="The team's review"
            testId="di-mr-review-text"
          />
          {result.reviewTruncated && (
            <p className="di-mr-hint" data-testid="di-mr-review-cut">
              This is the start of a longer review; the rest is cut for display.
            </p>
          )}
        </>
      )}
    </section>
  );
}
