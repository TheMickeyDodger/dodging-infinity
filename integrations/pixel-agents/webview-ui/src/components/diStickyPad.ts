/**
 * A STICKY persistent notice inside a scroller overlays the top of the scrollport, and the browser's focus scroll does not know it is there: a control that takes focus near the top (Shift+Tab from the end
 * lands it at the top edge; a focus restore leaves the header partly scrolled) is left UNDER the notice, a focused control nobody can see. The standard remedy is `scroll-padding-top` on the scroller, equal to
 * the notice's height: the browser then treats the strip the notice covers as outside the viewing region and scrolls the focused control clear of it. The height is not a constant (the notice wraps with the
 * panel's width), so it is measured and kept current.
 *
 * Presentation only: it reads a size and sets one style property; it reads, writes and sends nothing else.
 */
import type { RefObject } from 'react';
import { useEffect } from 'react';

/** Reserve the height of `noteRef` (while it is position: sticky) as scroll-padding-top on `scrollerRef`. Inactive: nothing is reserved. */
export function useStickyScrollPad(
  noteRef: RefObject<HTMLElement | null>,
  scrollerRef: RefObject<HTMLElement | null>,
  active: boolean,
): void {
  useEffect(() => {
    const note = noteRef.current;
    const scroller = scrollerRef.current;
    if (!active || !note || !scroller) return;
    const apply = () => {
      const sticky = getComputedStyle(note).position === 'sticky';
      scroller.style.scrollPaddingTop = sticky
        ? `${Math.ceil(note.getBoundingClientRect().height)}px`
        : '';
    };
    apply();
    const observer = new ResizeObserver(apply);
    observer.observe(note);
    return () => {
      observer.disconnect();
      scroller.style.scrollPaddingTop = '';
    };
  }, [noteRef, scrollerRef, active]);
}
