import '../diPopover.css';

import type { ReactNode } from 'react';
import { useId, useRef, useState } from 'react';

import type { PopoverDismissal } from '../diPopover.js';
import { useAnchoredPopover } from '../diPopover.js';

export interface AnchoredPopoverProps {
  /** The trigger's words, e.g. "More actions". */
  label: string;
  /** The popover's accessible name. */
  dialogLabel: string;
  /** The trigger's classes, so it matches the controls beside it. */
  triggerClassName?: string;
  /** The trigger's test id; the popover is `<testid>-popover`, its Close `<testid>-close`. */
  testid: string;
  /** Starts open (an initial state only, like a screen's `initialView`: the specimen and static-markup tests). */
  initialOpen?: boolean;
  /** The items. Each is handed `dismiss`: an item that does not navigate calls `dismiss('to-trigger')`. */
  children: (dismiss: (how: PopoverDismissal) => void) => ReactNode;
}

/**
 * Direction B (B2c): a quiet trigger and its anchored, NON-MODAL popover (behaviour in `diPopover.ts`). The popover is rendered right after its
 * trigger in the DOM, never portalled, so Shift+Tab from its first control lands on the trigger and Tab past its last control goes on to whatever
 * follows the trigger. Its items come first and a labelled Close last. It has no primary action of its own.
 */
export function AnchoredPopover({ label, dialogLabel, triggerClassName, testid, initialOpen = false, children }: AnchoredPopoverProps) {
  const [open, setOpen] = useState(initialOpen);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const popoverRef = useRef<HTMLDivElement>(null);
  const id = useId();
  const { dismiss, toggle, placement } = useAnchoredPopover(open, setOpen, triggerRef, popoverRef);
  return (
    <span className="di-popover-anchor">
      <button
        ref={triggerRef}
        type="button"
        className={triggerClassName}
        data-testid={testid}
        aria-haspopup="dialog"
        aria-expanded={open}
        aria-controls={open ? id : undefined}
        onClick={toggle}
      >
        {label}
      </button>
      {open && (
        <div
          ref={popoverRef}
          id={id}
          role="dialog"
          aria-label={dialogLabel}
          className="di-popover"
          data-testid={`${testid}-popover`}
          data-side={placement?.side ?? 'below'}
          data-align={placement?.align ?? 'end'}
          style={placement === null ? undefined : { maxHeight: placement.maxHeight, maxWidth: placement.maxWidth }}
        >
          <div className="di-popover-items">{children(dismiss)}</div>
          <button type="button" className="di-popover-close" data-testid={`${testid}-close`} onClick={() => dismiss('to-trigger')}>
            Close
          </button>
        </div>
      )}
    </span>
  );
}

export interface PopoverItemProps {
  /** The item's words: its accessible name, and nothing else is. */
  label: ReactNode;
  testid: string;
  /** When the action is held: WHY, drawn in the item. A held item is `aria-disabled`, stays focusable (so the reason is reachable) and does nothing. */
  held?: string | null;
  /** The reason's id, for anything else it describes (defaults to a generated one). */
  heldId?: string;
  /** Further descriptions, by id (e.g. the full words of a status the item belongs to). */
  describedBy?: string;
  busy?: boolean;
  /** A TOGGLE's state (aria-pressed), for an item that switches something on and off; omit it for an action. */
  pressed?: boolean;
  /** A pointer hint (kept where the action it replaces had one). */
  title?: string;
  onSelect: () => void;
}

/** One action in an anchored popover: the button, and, when it is held, its reason beside it, inside the item. */
export function PopoverItem({ label, testid, held = null, heldId, describedBy, busy, pressed, title, onSelect }: PopoverItemProps) {
  const generated = useId();
  const reasonId = heldId ?? generated;
  const describes = [held === null ? null : reasonId, describedBy ?? null].filter((part) => part !== null).join(' ');
  return (
    <div className="di-popover-item" data-held={held === null ? undefined : 'true'}>
      <button
        type="button"
        className="di-popover-action"
        data-testid={testid}
        aria-disabled={held === null ? undefined : 'true'}
        aria-describedby={describes === '' ? undefined : describes}
        aria-busy={busy}
        aria-pressed={pressed}
        title={title}
        onClick={held === null ? onSelect : undefined}
      >
        {label}
      </button>
      {held !== null && (
        <p className="di-popover-reason" id={reasonId} data-testid={`${testid}-held`}>
          {held}
        </p>
      )}
    </div>
  );
}
