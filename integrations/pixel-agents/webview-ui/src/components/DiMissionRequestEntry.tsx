/**
 * Item 4 - the ENTRY to the Mission request workspace, in the office panel.
 *
 * The panel is only where requests are offered and counted; reading, deciding and following them
 * happens in the workspace docked beside it (DiMissionRequest), which leaves the world usable.
 * What the entry says is the model's own sentence about the records: how many need the person,
 * how many are in progress, or exactly why requests cannot be read. It announces changes politely
 * and never invents a count.
 */
import type { MrState, MrTab } from './diMissionRequestModel.js';
import {
  countsOf,
  entryWords,
  MR_NAME,
  MR_TAGLINE,
  requestsUnavailable,
} from './diMissionRequestModel.js';
import { DI_FOCUS_RING } from './diNavigation.js';

/** The workspace seam, owned by App (null in a surface that cannot open one). */
export interface DiMissionRequestAccess {
  state: MrState;
  /** Open the workspace; `opener` is where focus returns when it closes. */
  open: (tab: MrTab, opener: HTMLElement) => void;
}

export function DiMissionRequestEntry({ access }: { access: DiMissionRequestAccess | null }) {
  if (access === null) return null;
  const { state } = access;
  const counts = countsOf(state.overview);
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
        <p
          aria-live="polite"
          data-testid="di-mr-entry-summary"
          className="di-op-entry-note di-op-clamp-1"
        >
          {entryWords(state)}
        </p>
      </div>
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
          className={button}
          aria-expanded={state.open && state.tab === 'ask'}
          data-testid="di-mr-open"
          onClick={(event) => access.open('ask', event.currentTarget)}
        >
          Ask the {MR_NAME}
        </button>
        <button
          type="button"
          className={button}
          aria-expanded={state.open && state.tab === 'requests'}
          data-testid="di-mr-open-requests"
          onClick={(event) => access.open('requests', event.currentTarget)}
        >
          Review requests{counts.needsYou > 0 ? ` (${counts.needsYou} need you)` : ''}
        </button>
      </div>
    </section>
  );
}
