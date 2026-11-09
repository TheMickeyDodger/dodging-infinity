/**
 * Direction B, stage B2c: the ANCHORED, NON-MODAL popover's behaviour (the "More actions" menu first). Drawn by `ui/AnchoredPopover.tsx`, which
 * renders it ADJACENT to its trigger in the DOM, so sequential Tab and Shift+Tab pass through it in the window's order.
 *
 * The contract (b-plan.md, "The anchored popover"):
 *  - open: a NON-MODAL claim in front (`claimFront(owner, { modal: false })`: it owns Escape, it inerts nothing), focus on its FIRST control;
 *  - Escape: a capture-phase `window` listener, acting only while this popover is FOREMOST. It CONSUMES the press (`stopPropagation`), so one
 *    press closes the popover and nothing else: the scan face's Escape ladder and the Map's key guard never see it, and they yield to any claim
 *    in front anyway (the claim is released only after the close has rendered);
 *  - Escape, Close, or an item that does not navigate: close, focus back to the trigger (`returnFocusOnClose`, after the release);
 *  - a pointer pressed outside (not the popover, not the trigger): close, focus NOT moved (it goes where the person pressed);
 *  - focus leaving the popover for an element outside it: close, focus not moved (it is already where the person sent it);
 *  - the popover unmounting with its trigger (an item navigated, or the screen changed): the claim is RELEASED, focus not moved (the screen's own
 *    focus rule hands it on). A popover that unmounted without releasing would leave `frontCount()` above 0, and every later Escape would seem dead.
 *  - (A), the Lead's ruling, DEFENCE IN DEPTH (not a fix for a live defect): a popover drawn INSIDE a modal dialog whose focus-returning close finds its
 *    trigger unable to take focus gives focus to THAT dialog, rather than to the page fallbacks (all inside #root, inert under the modal, so none
 *    can take it). Every modal surface today (useDiModal) already lands there in Chrome through its own focusout retake, which depends on the browser
 *    firing focusout when the focused element is removed (inferred, never isolated). (A) makes the guarantee explicit here and depends on no such
 *    event. Verified by construction on a TEST-ONLY surface (test/harness/di-popover-in-modal.html, `?surface=bare`): no product surface puts a popover
 *    in a modal. NOT covered: (1) a popover portalled OUT of its modal's subtree (`closest` finds no dialog: the page fallbacks apply, so nothing is
 *    moved); (2) a dialog that cannot itself take focus (the same); (3) a close that returns no focus (an item that navigates, an outside pointer,
 *    focus leaving), where the surrounding surface's own rule applies; (4) a trigger hidden by the SAME commit as the close, because the focus decision
 *    below runs before that commit's own attribute changes apply, so focus can return to a control that is hidden a moment later.
 *
 * Presentation only: it reads, writes and sends nothing.
 */

import type { RefObject } from 'react';
import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react';

import { canTakeFocus } from './diMinimize.js';
import { dialogTabStops } from './diModal.js';
import { claimFront, isForemost, releaseFront, returnFocusOnClose } from './diModalStack.js';

/** How a close treats focus: back to the trigger, or left where the person put it. */
export type PopoverDismissal = 'to-trigger' | 'in-place';

/** The visible box the popover must stay inside: the window, cut by every clipping ancestor (the scrolling screen it sits in). */
export interface PopoverFrame {
  readonly top: number;
  readonly left: number;
  readonly right: number;
  readonly bottom: number;
}

export interface PopoverPlacement {
  readonly side: 'below' | 'above';
  readonly align: 'end' | 'start';
  /** The room on the chosen side: the popover scrolls inside this height rather than leave the frame. */
  readonly maxHeight: number;
  readonly maxWidth: number;
}

/** The space between the trigger and the popover, and the least space kept between the popover and the frame's edge. */
export const POPOVER_GAP_PX = 4;
export const POPOVER_MARGIN_PX = 8;

/**
 * Where the popover goes: BELOW its trigger, END-aligned (its right edge on the trigger's right edge), unless that leaves the frame. It flips
 * ABOVE when it does not fit below and there is more room above, and to START alignment when end alignment would cross the frame's left edge
 * and start alignment would not cross its right one. Pure: the numbers in, the placement out.
 */
export function placePopover(
  trigger: { top: number; bottom: number; left: number; right: number },
  size: { width: number; height: number },
  frame: PopoverFrame,
): PopoverPlacement {
  const below = frame.bottom - POPOVER_MARGIN_PX - (trigger.bottom + POPOVER_GAP_PX);
  const above = trigger.top - POPOVER_GAP_PX - (frame.top + POPOVER_MARGIN_PX);
  const side = size.height <= below || below >= above ? 'below' : 'above';
  const endFits = trigger.right - size.width >= frame.left + POPOVER_MARGIN_PX;
  const startFits = trigger.left + size.width <= frame.right - POPOVER_MARGIN_PX;
  const align = !endFits && startFits ? 'start' : 'end';
  const room = align === 'end' ? trigger.right - (frame.left + POPOVER_MARGIN_PX) : frame.right - POPOVER_MARGIN_PX - trigger.left;
  return {
    side,
    align,
    maxHeight: Math.max(0, Math.floor(side === 'below' ? below : above)),
    maxWidth: Math.max(0, Math.floor(room)),
  };
}

/**
 * Does this element establish the containing block for its position:fixed descendants (so that it, and not the window, bounds them)?
 * THE LIST IS AN EMPIRICAL CLAIM ABOUT THE ENGINE WE SHIP AGAINST and was set from measurement, not from the specification's wording: for each of these (and each one deliberately left out,
 * container-type among them) a position:fixed descendant was measured inside an ancestor carrying it, in the installed engine(s); see .herd/state/verification/b/b3-handoff/engine-controls-*.json.
 */
function boundsFixedDescendants(style: CSSStyleDeclaration): boolean {
  return (
    style.backdropFilter !== 'none' ||
    style.contentVisibility === 'auto' ||
    style.contentVisibility === 'hidden' ||
    style.filter !== 'none' ||
    style.perspective !== 'none' ||
    style.rotate !== 'none' ||
    style.scale !== 'none' ||
    style.transformStyle === 'preserve-3d' ||
    style.transform !== 'none' ||
    style.translate !== 'none' ||
    /\b(filter)\b/.test(style.willChange) ||
    /\b(perspective)\b/.test(style.willChange) ||
    /\b(transform)\b/.test(style.willChange) ||
    /\b(content|layout|paint|strict)\b/.test(style.contain)
  );
}

/**
 * The window, cut by every ancestor of `el` that clips its content (a scrolling screen clips what overflows it).
 * A position:fixed element is bounded by the window, NOT by the overflow of its other DOM ancestors, unless one of them establishes a containing block for
 * fixed descendants (B3: the Code Diff stage is fixed inside the office panel's overflow:hidden aside; ignoring that left a zero-width frame at the two-pane
 * sizes and collapsed an end-aligned popover to max-width 0).
 */
export function popoverFrame(el: HTMLElement): PopoverFrame {
  let top = 0;
  let left = 0;
  let right = window.innerWidth;
  let bottom = window.innerHeight;
  let escaped = false;
  for (let node = el.parentElement; node !== null; node = node.parentElement) {
    const style = getComputedStyle(node);
    if (escaped && boundsFixedDescendants(style)) escaped = false;
    const clips = !escaped && /(auto|scroll|hidden|clip)/.test(`${style.overflowX} ${style.overflowY}`);
    if (style.position === 'fixed') escaped = true;
    if (!clips) continue;
    const box = node.getBoundingClientRect();
    top = Math.max(top, box.top);
    left = Math.max(left, box.left);
    right = Math.min(right, box.right);
    bottom = Math.min(bottom, box.bottom);
  }
  return { top, left, right, bottom };
}

export interface AnchoredPopoverState {
  /** Close, treating focus as `how` says. */
  readonly dismiss: (how: PopoverDismissal) => void;
  /** The trigger's click: open it, or close it with focus kept on the trigger. */
  readonly toggle: () => void;
  /** Null until the open popover has been measured. */
  readonly placement: PopoverPlacement | null;
}

export function useAnchoredPopover(
  open: boolean,
  setOpen: (open: boolean) => void,
  triggerRef: RefObject<HTMLElement | null>,
  popoverRef: RefObject<HTMLElement | null>,
): AnchoredPopoverState {
  const owner = useRef<object>({});
  const how = useRef<PopoverDismissal>('in-place');
  /** A pointer is down on the trigger: the focus moving onto it is that press, and the click that follows closes the popover. */
  const pressingTrigger = useRef(false);
  const [placement, setPlacement] = useState<PopoverPlacement | null>(null);

  const dismiss = useCallback(
    (next: PopoverDismissal) => {
      how.current = next;
      setOpen(false);
    },
    [setOpen],
  );
  const toggle = useCallback(() => {
    pressingTrigger.current = false;
    if (open) dismiss('to-trigger');
    else setOpen(true);
  }, [open, dismiss, setOpen]);

  // Placement, measured before paint and again when the window changes size. Declared BEFORE the claim, so it is in place when focus lands.
  useLayoutEffect(() => {
    if (!open) return;
    const place = () => {
      const trigger = triggerRef.current;
      const popover = popoverRef.current;
      if (trigger === null || popover === null) return;
      setPlacement(
        placePopover(trigger.getBoundingClientRect(), { width: popover.scrollWidth, height: popover.scrollHeight }, popoverFrame(trigger)),
      );
    };
    place();
    window.addEventListener('resize', place);
    return () => {
      window.removeEventListener('resize', place);
      setPlacement(null);
    };
  }, [open, triggerRef, popoverRef]);

  // The claim: in front while open, RELEASED when it closes OR unmounts (both run this cleanup). Focus goes back to the trigger only for a close
  // that asked for it, and only if this popover was the one in front.
  useLayoutEffect(() => {
    if (!open) return;
    const me = owner.current;
    const trigger = triggerRef.current;
    // (A): the modal dialog this popover is drawn in, if any (read at open, while the trigger is certainly in place)
    const ownModal = trigger?.closest<HTMLElement>('[aria-modal="true"]') ?? null;
    how.current = 'in-place';
    claimFront(me, { modal: false });
    const popover = popoverRef.current;
    if (popover !== null) (dialogTabStops(popover)[0] ?? popover).focus({ preventScroll: true });
    return () => {
      const wasForemost = releaseFront(me);
      if (how.current !== 'to-trigger') return;
      // (A): the trigger cannot take focus and this popover sits in a modal that can: focus that modal (see the header for what this does NOT cover)
      if (wasForemost && !canTakeFocus(trigger) && canTakeFocus(ownModal)) {
        ownModal.focus({ preventScroll: true });
        return;
      }
      returnFocusOnClose(wasForemost, trigger);
    };
  }, [open, triggerRef, popoverRef]);

  useEffect(() => {
    const popover = popoverRef.current;
    if (!open || popover === null) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key !== 'Escape' || !isForemost(owner.current)) return;
      event.preventDefault();
      event.stopPropagation(); // consumed: this press closes the popover and ONLY the popover
      dismiss('to-trigger');
    };
    const onPointerDown = (event: PointerEvent) => {
      const target = event.target;
      if (!(target instanceof Node) || popover.contains(target)) return;
      if (triggerRef.current?.contains(target) === true) {
        pressingTrigger.current = true;
        return;
      }
      dismiss('in-place');
    };
    const onFocusOut = (event: FocusEvent) => {
      const to = event.relatedTarget;
      if (!(to instanceof Node) || popover.contains(to)) return; // nowhere (the window lost focus), or still inside
      if (pressingTrigger.current && triggerRef.current?.contains(to) === true) return; // the trigger's own click closes it
      dismiss('in-place');
    };
    window.addEventListener('keydown', onKey, true);
    document.addEventListener('pointerdown', onPointerDown, true);
    popover.addEventListener('focusout', onFocusOut);
    return () => {
      window.removeEventListener('keydown', onKey, true);
      document.removeEventListener('pointerdown', onPointerDown, true);
      popover.removeEventListener('focusout', onFocusOut);
    };
  }, [open, dismiss, triggerRef, popoverRef]);

  return { dismiss, toggle, placement };
}
