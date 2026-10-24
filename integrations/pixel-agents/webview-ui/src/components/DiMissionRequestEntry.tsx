/**
 * Item 4 - the ENTRY to the Mission request workspace, in the office panel.
 *
 * The panel is only where requests are offered and counted; reading, deciding and following them
 * happens in the workspace docked beside it (DiMissionRequest), which leaves the world usable.
 * What the entry says is the model's own sentence about the records: how many need the person,
 * how many are in progress, or exactly why requests cannot be read. It announces changes politely
 * and never invents a count.
 */
import { Fragment } from 'react';

import type { MrState, MrTab } from './diMissionRequestModel.js';
import {
  countsOf,
  entryWords,
  MR_NAME,
  MR_TAGLINE,
  requestsUnavailable,
  waitingOf,
} from './diMissionRequestModel.js';
import { DI_FOCUS_RING } from './diNavigation.js';

/** The workspace seam, owned by App (null in a surface that cannot open one). */
export interface DiMissionRequestAccess {
  state: MrState;
  /** Open the workspace; `opener` is where focus returns when it closes. */
  open: (tab: MrTab, opener: HTMLElement) => void;
  /** Open the workspace on the Ask tab with this conversation read (a view-only read). */
  openConversation?: (conversationId: string, opener: HTMLElement) => void;
  /** Open the workspace on the Requests tab with this request read (a view-only read). */
  openRequest?: (requestRef: string, opener: HTMLElement) => void;
}

/**
 * The count words with each "number noun" pair kept together on one line: an ellipsis never cuts the line, and a wrap never parts a number from the noun it
 * counts. The text content is exactly `entryWords`; only the pairs are wrapped.
 */
function CountWords({ text }: { text: string }) {
  return (
    <>
      {text.split(' \u00b7 ').map((part, index) => {
        const pair = /^(\d+ [a-z]+)(.*)$/.exec(part);
        return (
          <Fragment key={`${index}-${part}`}>
            {index > 0 && ' \u00b7 '}
            {pair === null ? (
              part
            ) : (
              <>
                <span style={{ whiteSpace: 'nowrap' }}>{pair[1]}</span>
                {pair[2]}
              </>
            )}
          </Fragment>
        );
      })}
    </>
  );
}

export function DiMissionRequestEntry({ access }: { access: DiMissionRequestAccess | null }) {
  if (access === null) return null;
  const { state } = access;
  const counts = countsOf(state.overview);
  const waiting = waitingOf(state.overview);
  const button = `di-op-button ${DI_FOCUS_RING}`;
  // Not available here: ONE quiet line — the name and that fact — with the model's own sentence
  // (why, and how to set it up) one step behind. Demoted in prominence, never removed.
  if (requestsUnavailable(access.state))
    return (
      <section
        className="di-op-mr-quiet"
        aria-labelledby="di-mr-entry-title"
        data-testid="di-mr-entry"
      >
        <details data-testid="di-mr-entry-why">
          <summary className={DI_FOCUS_RING}>
            <span id="di-mr-entry-title">{MR_NAME}</span>:{' '}
            <span aria-live="polite" data-testid="di-mr-entry-summary">
              not available in this office
            </span>{' '}
            · why, and how to set it up
          </summary>
          <div className="di-op-detail-body">
            <p data-testid="di-mr-entry-tagline">{MR_TAGLINE}</p>
            <p>{entryWords(state)}</p>
          </div>
        </details>
      </section>
    );
  return (
    <section className="di-op-entry" aria-labelledby="di-mr-entry-title" data-testid="di-mr-entry">
      <div className="di-op-entry-head">
        <h2 id="di-mr-entry-title" className="di-op-entry-title">
          {MR_NAME}
        </h2>
      </div>
      {/* The counts get a line of their own and are NEVER clamped: an ellipsis must not cut a number from the noun it counts. */}
      <p aria-live="polite" data-testid="di-mr-entry-summary" className="di-op-entry-note">
        <CountWords text={entryWords(state)} />
      </p>
      <p
        className="di-op-entry-note di-op-entry-tagline di-op-clamp-1"
        data-testid="di-mr-entry-tagline"
        title={MR_TAGLINE}
      >
        {MR_TAGLINE}
      </p>
      <div className="di-op-actions">
        <button
          type="button"
          // AC4: a conversation waits for the person's answer: a still glow, the ▲ shape and the words below
          className={waiting.conversations > 0 ? `${button} di-op-attn` : button}
          aria-expanded={state.open && state.tab === 'ask'}
          data-testid="di-mr-open"
          data-attention={waiting.conversations > 0 ? 'true' : undefined}
          onClick={(event) => access.open('ask', event.currentTarget)}
        >
          {waiting.conversations > 0 && (
            <span className="di-op-attn-glyph" aria-hidden="true">
              ▲{' '}
            </span>
          )}
          Ask the {MR_NAME}
          {waiting.conversations > 0
            ? ` (${waiting.conversations} ${waiting.conversations === 1 ? 'conversation' : 'conversations'} waiting for your answer)`
            : ''}
        </button>
        <button
          type="button"
          // AC4: a request waits for the person's decision: a still glow, the ▲ shape and the words below
          className={counts.needsYou > 0 ? `${button} di-op-attn` : button}
          aria-expanded={state.open && state.tab === 'requests'}
          data-testid="di-mr-open-requests"
          data-attention={counts.needsYou > 0 ? 'true' : undefined}
          onClick={(event) => access.open('requests', event.currentTarget)}
        >
          {counts.needsYou > 0 && (
            <span className="di-op-attn-glyph" aria-hidden="true">
              ▲{' '}
            </span>
          )}
          Review requests
          {counts.needsYou > 0
            ? ` (${counts.needsYou} ${counts.needsYou === 1 ? 'request needs' : 'requests need'} you)`
            : ''}
        </button>
      </div>
    </section>
  );
}
