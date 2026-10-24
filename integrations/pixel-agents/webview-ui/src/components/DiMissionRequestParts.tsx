/**
 * Item 4 — the Mission request workspace's presentational parts.
 *
 * Every word that came from a provider, a person or a team is UNTRUSTED data and is drawn only as
 * text: React text nodes inside plain elements, never markup, never a link, never an image, never
 * an attribute. A character that could change what a reader sees (a bidirectional override, a
 * zero-width character, a control byte) is shown as a visible placeholder, never performed.
 */
import { Fragment, useId, useState } from 'react';

import { visibleSegments } from './diCodeReviewModel.js';
import type { MrProofRules, MrRulePart } from './diMissionRequestContract.js';
import { proofRuleParts, proofRulesAbsentWords } from './diMissionRequestContract.js';
import type { MrProposal, MrResult } from './diMissionRequestModel.js';
import {
  JOURNEY,
  journeyIndex,
  linesOf,
  MR_READING_LINES,
  MR_READING_LINES_WIDE,
  shortDigest,
} from './diMissionRequestModel.js';
import type { MrStartCause } from './diMissionRequestStartCause.js';
import { startCauseWords } from './diMissionRequestStartCause.js';

/** One line of untrusted text with every hidden character made visible. */
export function Line({ text }: { text: string }) {
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

/**
 * A sentence in pieces: the plain words as they are, and each raw key (a requirement key, an artifact key, an evidence kind, a
 * permitted action) secondary: the same text, exactly, in the muted key style. Every piece is drawn as inert text with hidden
 * characters made visible; the key style changes how it looks and nothing else.
 */
export function KeyedText({ parts }: { parts: MrRulePart[] }) {
  const inline = (text: string) =>
    visibleSegments(text).map((segment, index) =>
      segment.control ? (
        <span key={index} className="di-mr-ctl" data-testid="di-mr-control">
          {segment.text}
        </span>
      ) : (
        <span key={index}>{segment.text}</span>
      ),
    );
  return (
    <div className="di-mr-text">
      {parts.map((part, index) => (
        <Fragment key={index}>
          {part.key === true ? (
            <span className="di-mr-key" data-testid="di-mr-key">
              {inline(part.text)}
            </span>
          ) : (
            inline(part.text)
          )}
        </Fragment>
      ))}
    </div>
  );
}

/** A permitted action in words, with its exact key after it and muted: "read the repository (repository_read)". */
export function ScopeLine({ scope, meanings }: { scope: string; meanings: Record<string, string> }) {
  const meaning = meanings[scope];
  // AC7: the scope key is a record value (content, kept as received). The meaning beside it is explanatory wording
  // the panel sends; it is OUR copy even though it arrives over the wire, so it is NOT marked and the guard reads it.
  if (!meaning) return <span data-copy="content">{scope}</span>;
  return (
    <>
      {meaning}{' '}
      <span className="di-mr-key" data-copy="content">
        ({scope})
      </span>
    </>
  );
}

/** Untrusted text, all of it, inert. */
export function Untrusted({
  text,
  testId,
  content = false,
}: {
  text: string;
  testId?: string;
  /** AC7: words this project does not author (what a person wrote, the assistant, a record): kept as received. */
  content?: boolean;
}) {
  return (
    <div className="di-mr-text" data-testid={testId} data-copy={content ? 'content' : undefined}>
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
        <div className="di-mr-text" data-copy="content">
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
      <code data-copy="content">{shortDigest(digest)}</code>{' '}
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
        <code
          id={id}
          className="di-mr-digest-full"
          data-testid="di-mr-digest-full"
          data-copy="content"
        >
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

/** The exact references of a proposal: the request, the Mission, the revision and the digest (the whole digest one control away). */
export function SourceFacts({ source }: { source: SourceIdentity }) {
  return (
    <>
      {source.requestRef === null ? (
        <p>A draft in this conversation. It is not a proposal of record yet.</p>
      ) : (
        <p>
          Request <code data-copy="content">{source.requestRef}</code>, Mission{' '}
          <code data-copy="content">{source.missionId}</code>, revision {source.revision}
        </p>
      )}
      <p>
        Digest <Digest digest={source.digest} />
      </p>
    </>
  );
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
  showSource = true,
}: {
  proposal: MrProposal;
  source: SourceIdentity;
  meanings: Record<string, string>;
  heading: string;
  testId?: string;
  /** False where the exact references are shown in their own disclosure instead of as the last field. */
  showSource?: boolean;
}) {
  return (
    <section className="di-mr-section" aria-label={heading} data-testid={testId}>
      <h4>{heading}</h4>
      <dl className="di-mr-fields">
        <div>
          <dt>What you asked for</dt>
          <dd>
            <Untrusted text={proposal.objective} testId={`${testId}-objective`} content />
          </dd>
        </div>
        {proposal.targetContext !== '' && (
          <div>
            <dt>Where it applies</dt>
            <dd>
              <Untrusted text={proposal.targetContext} content />
            </dd>
          </div>
        )}
        <div>
          <dt>What is in scope, and what is not</dt>
          <dd>
            <Untrusted text={proposal.requestedScope} testId={`${testId}-scope`} content />
          </dd>
        </div>
        <div>
          <dt>What the team may do</dt>
          <dd>
            <ul>
              {proposal.actionScope.map((scope) => (
                <li key={scope}>
                  <ScopeLine scope={scope} meanings={meanings} />
                </li>
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
        {showSource && (
          <div>
            <dt>Source</dt>
            <dd data-testid={`${testId}-source`}>
              <SourceFacts source={source} />
            </dd>
          </div>
        )}
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
      {proofRuleParts(proof.contract).map((section) => (
        <div key={section.title} className="di-mr-rule-section">
          <h5>{section.title}</h5>
          <ul>
            {section.lines.map((line, index) => (
              <li key={index}>
                <KeyedText parts={line} />
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
