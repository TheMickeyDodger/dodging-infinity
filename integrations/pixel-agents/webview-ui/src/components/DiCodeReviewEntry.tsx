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
import { reviewNotOfferedConsequence } from './diCodeReviewModel.js';
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
   * feature13. `dock`: in the office panel's Selected dock, which never leaves the viewport — the
   * entry a person reaches by selecting a member (the button, one line of what it is; where it is
   * not offered, that fact, with its reason one step away in the inspector). `full`: in the
   * inspector, with the whole explanation and the reason in words. `row`: one member's card in a
   * team list, where the explanation every offered row shares is said ONCE by the list (the button
   * keeps it as its title); a reason that is the row's own is still said on the row.
   */
  variant?: 'full' | 'dock' | 'row';
}) {
  const offer = member.codeReview;
  const dock = variant === 'dock';
  // An office that predates the review says nothing about it: nothing is invented.
  if (offer === undefined) return null;
  // Styled by the calm panel's stylesheet (diOperationalPanel.css). The same rule as
  // statesReviewNotOffered (diCodeReviewModel.ts), kept inline so the offer narrows below.
  if (offer.state !== 'offered' || offer.listing === null || offer.taskId === null) {
    return dock ? (
      <div className="di-op-selected-body" data-testid="di-dock-code-review-unavailable">
        <span className="text-text-muted">
          Code review is not offered for {member.agent} now. The reason is in its details.
        </span>
      </div>
    ) : (
      <div className="di-op-selected-body" data-testid="di-code-review-unavailable">
        {/* The plain consequence; the exact reason is in the agent's recorded details. */}
        <span className="text-text-muted">{reviewNotOfferedConsequence(offer.reason)}</span>
      </div>
    );
  }
  const { listing, taskId } = offer;
  return (
    <div
      className="di-op-selected-body"
      data-testid={dock ? 'di-code-review-entry' : 'di-inspector-code-review'}
    >
      <button
        type="button"
        className={`di-op-button ${DI_FOCUS_RING}`}
        data-testid={dock ? 'di-open-code-review' : 'di-inspector-open-code-review'}
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
      {variant !== 'row' && (
        <span className={dock ? 'text-text-muted di-op-review-hint' : 'text-text-muted'}>
          {dock
            ? 'View only: the team’s changes, in a large window; it changes nothing.'
            : 'Read what is changed in this assignment’s worktree, in a large window. View only: it shows the team’s changes and changes nothing.'}
        </span>
      )}
    </div>
  );
}
