/**
 * feature13 — the modal MECHANICS the office inspector shares with the live code review dialog
 * (DiCodeReview.tsx), extracted so the inspector reuses them rather than inventing its own:
 *
 *  - the application root (#root) is made `inert` while the dialog is open; the dialog takes focus;
 *  - on close, focus returns to the control that opened it (read once, when the dialog mounts);
 *  - Tab is trapped and wraps over the dialog's REAL tab stops (roving-tabindex rows are not stops);
 *  - Escape closes;
 *  - two hardening guards: focus that falls out of the dialog (to the body) is taken back, and a
 *    `window` keydown listener in the CAPTURE phase handles a key dispatched outside the dialog as if
 *    it had arrived inside, so no key ever reaches the office behind it.
 *
 * Nothing here moves or animates.
 */
import type { KeyboardEvent as ReactKeyboardEvent, RefObject } from 'react';
import { useEffect, useLayoutEffect, useRef } from 'react';

import { claimFront, isForemost, releaseFront, returnFocusOnClose } from './diModalStack.js';

/** The dialog's TAB ORDER: what Tab actually visits, in order (as DiCodeReview's tabStops). */
export function dialogTabStops(root: HTMLElement): HTMLElement[] {
  const selector =
    'button:not([disabled]), input:not([disabled]), summary, [href], select:not([disabled]), textarea:not([disabled]), [tabindex]';
  return [...root.querySelectorAll<HTMLElement>(selector)].filter(
    (el) =>
      el.tabIndex >= 0 &&
      el.getAttribute('aria-hidden') !== 'true' &&
      el.getClientRects().length > 0,
  );
}

type KeyLike = Pick<
  KeyboardEvent,
  'key' | 'shiftKey' | 'target' | 'preventDefault' | 'stopPropagation'
>;

/**
 * Owns focus and keys for an open modal. `returnFocusTo` is read once, when the dialog mounts.
 * Returns the dialog's own onKeyDown handler.
 *
 * AC3: `active` is false while the dialog is MINIMIZED (still mounted, hidden): #root is not inert and the
 * two hardening guards stand down, so the world and the panel are usable; when it is restored, #root is
 * made inert again. The caller moves focus on minimize and restore (diMinimize.ts). Default: active.
 */
export function useDiModal(
  dialogRef: RefObject<HTMLElement | null>,
  onClose: () => void,
  returnFocusTo: RefObject<HTMLElement | null>,
  active = true,
): (event: ReactKeyboardEvent<HTMLElement>) => void {
  // The guard on #root is OWNED (diModalStack.ts): this dialog claims it while in front and releases only its own
  // claim. On close it gives focus back to its opener only if it was the foremost dialog; a hidden (minimized) or
  // background dialog closing moves no focus and leaves another dialog's guard in place.
  const owner = useRef<object>({});
  useLayoutEffect(() => {
    const me = owner.current;
    const back = returnFocusTo.current;
    claimFront(me);
    dialogRef.current?.focus();
    return () => {
      returnFocusOnClose(releaseFront(me), back);
    };
  }, [dialogRef, returnFocusTo]);

  // Minimized: this dialog leaves the front (the app is usable unless another dialog is in front); restored: it
  // comes to the front again.
  const shownActive = useRef(active);
  useLayoutEffect(() => {
    if (shownActive.current === active) return;
    shownActive.current = active;
    if (active) claimFront(owner.current);
    else releaseFront(owner.current);
  }, [active]);

  const handleKey = (event: KeyLike) => {
    event.stopPropagation();
    const root = dialogRef.current;
    if (!root) return;
    if (event.key === 'Escape') {
      event.preventDefault();
      onClose();
      return;
    }
    if (event.key === 'Tab') {
      const items = dialogTabStops(root);
      if (items.length === 0) {
        event.preventDefault();
        return;
      }
      const first = items[0];
      const last = items[items.length - 1];
      const active = document.activeElement;
      if (event.shiftKey && (active === first || active === root)) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && active === last) {
        event.preventDefault();
        first.focus();
      } else if (!root.contains(active)) {
        event.preventDefault();
        first.focus();
      }
    }
  };

  const handleKeyRef = useRef(handleKey);
  useEffect(() => {
    handleKeyRef.current = handleKey;
  });
  useEffect(() => {
    const root = dialogRef.current;
    if (!root || !active) return;
    const retake = () => {
      if (!isForemost(owner.current)) return; // only the dialog in front takes focus back
      if (!root.contains(document.activeElement)) root.focus({ preventScroll: true });
    };
    const onFocusOut = (event: FocusEvent) => {
      if (event.relatedTarget === null) queueMicrotask(retake);
    };
    const onWindowKey = (event: KeyboardEvent) => {
      if (!isForemost(owner.current)) return; // only the dialog in front guards keys
      if (event.target instanceof Node && root.contains(event.target)) return; // inside: onKeyDown has it
      retake();
      handleKeyRef.current(event);
    };
    root.addEventListener('focusout', onFocusOut);
    window.addEventListener('keydown', onWindowKey, true);
    return () => {
      root.removeEventListener('focusout', onFocusOut);
      window.removeEventListener('keydown', onWindowKey, true);
    };
  }, [dialogRef, active]);

  return (event) => handleKey(event);
}
