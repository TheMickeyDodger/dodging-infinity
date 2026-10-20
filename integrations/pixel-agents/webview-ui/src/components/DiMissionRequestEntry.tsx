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
import { countsOf, entryWords, MR_NAME, MR_TAGLINE } from './diMissionRequestModel.js';
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
  const button = `cursor-pointer border-2 border-accent bg-active-bg text-left px-4 py-2 text-sm ${DI_FOCUS_RING}`;
  return (
    <section
      className="flex flex-col gap-2 shrink-0 border-2 border-accent p-4"
      aria-labelledby="di-mr-entry-title"
      data-testid="di-mr-entry"
    >
      <h2 id="di-mr-entry-title" className="text-sm">
        {MR_NAME}
      </h2>
      <p className="text-text-muted" data-testid="di-mr-entry-tagline">
        {MR_TAGLINE}
      </p>
      <p aria-live="polite" data-testid="di-mr-entry-summary" className="text-text-muted">
        {entryWords(state)}
      </p>
      <div className="flex flex-wrap gap-4">
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
