/**
 * Item 3 — the ENTRY to the live code review, in the selected agent's inspector.
 *
 * The panel is only where a review is offered and opened; the reading happens in the modal
 * (DiCodeReview). The offer is the office's own decision from the typed facts of the listing (a
 * Mission Core assignment, active, bound, this agent a live member of the direct task). Where it
 * is not offered, the reason is said in words — an absent button never leaves the reader guessing.
 */
import type { DiMember } from '../../../core/src/messages.js';
import type { ReviewTargetRef } from './diCodeReviewModel.js';
import { unavailableWords } from './diCodeReviewModel.js';
import { DI_FOCUS_RING } from './diNavigation.js';

/** The review seam, owned by App (null in a surface that cannot open one). */
export interface DiCodeReviewAccess {
  /** Open a review; `opener` is where focus returns when it closes. */
  open: (target: ReviewTargetRef, opener: HTMLElement) => void;
}

export function DiCodeReviewEntry({
  member,
  missionRef,
  access,
  variant = 'full',
}: {
  member: DiMember;
  missionRef: string;
  access: DiCodeReviewAccess | null;
  /**
   * `full`: in the inspector, with the explanation. `bar`: the compact entry pinned to the top of
   * the panel while this agent is selected (so it cannot scroll out of reach).
   */
  variant?: 'full' | 'bar';
}) {
  const offer = member.codeReview;
  const bar = variant === 'bar';
  // An office that predates the review says nothing about it: nothing is invented.
  if (offer === undefined) return null;
  const shell = bar
    ? 'sticky top-0 z-10 -mx-8 px-8 py-3 flex items-center gap-4 flex-wrap bg-bg border-y-2 border-accent'
    : 'flex flex-col gap-1';
  if (offer.state !== 'offered' || offer.listing === null || offer.taskId === null) {
    return (
      <div
        className={shell}
        data-testid={bar ? 'di-code-review-bar-unavailable' : 'di-code-review-unavailable'}
      >
        <span className="text-text-muted">
          Code review is not offered for {member.agent} now.
          {bar ? ' Reason below.' : ` ${unavailableWords(offer.reason)}`}
        </span>
      </div>
    );
  }
  const { listing, taskId } = offer;
  return (
    <div className={shell} data-testid={bar ? 'di-code-review-bar' : 'di-code-review-entry'}>
      {bar && <span className="text-text-muted">{member.agent} selected</span>}
      <button
        type="button"
        className={`self-start cursor-pointer border-2 border-accent bg-active-bg text-left ${
          bar ? 'px-4 py-2 text-sm' : 'px-3 py-2'
        } ${DI_FOCUS_RING}`}
        data-testid={bar ? 'di-open-code-review-bar' : 'di-open-code-review'}
        disabled={access === null}
        onClick={(event) =>
          access?.open(
            { missionRef, memberRef: member.memberRef, taskId, listing, agentName: member.agent },
            event.currentTarget,
          )
        }
        title="Opens a large, read-only view of what is changed in this assignment's worktree"
      >
        Open code review
      </button>
      {!bar && (
        <span className="text-text-muted">
          Read what is changed in this assignment&apos;s worktree, in a large window. View only: it
          shows the team&apos;s changes and changes nothing.
        </span>
      )}
    </div>
  );
}
