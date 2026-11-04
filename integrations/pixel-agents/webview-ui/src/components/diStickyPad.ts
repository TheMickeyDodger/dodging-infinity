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

/**
 * Deliverable 3 (narrow windows, scan face): two measured insets the scan face's FIXED panes need, set as custom properties on the panel (`panelRef`).
 *
 * - `--scan-fixture-inset`: fixture mode's in-panel label (`noteRef`) is its own strip across the top of the window, so the fixed rail and stage start
 *   below it instead of under it (where it painted over the rail's wordmark); its height wraps with the width, so it is measured.
 * - `--scan-rail-overlap`: where the window is too narrow for the rail AND the panel side by side, the rail (fixed at the window's left) covers the
 *   panel's left edge; the panel's own list starts past it instead of under it.
 *
 * Presentation only, like the scroll padding above: it reads two boxes and sets two style properties; it reads, writes and sends nothing else.
 */
export function useScanFaceInsets(
  panelRef: RefObject<HTMLElement | null>,
  noteRef: RefObject<HTMLElement | null>,
  active: boolean,
  fixtureNote: boolean,
): void {
  useEffect(() => {
    const panel = panelRef.current;
    if (!active || !panel) return;
    const apply = () => {
      const note = noteRef.current;
      panel.style.setProperty(
        '--scan-fixture-inset',
        fixtureNote && note ? `${Math.ceil(note.getBoundingClientRect().height)}px` : '0px',
      );
      const rail = panel.querySelector('.di-rail');
      const overlap = rail ? rail.getBoundingClientRect().right - panel.getBoundingClientRect().left : 0;
      panel.style.setProperty('--scan-rail-overlap', `${Math.max(0, Math.ceil(overlap))}px`);
    };
    apply();
    const observer = new ResizeObserver(apply);
    observer.observe(panel);
    if (noteRef.current) observer.observe(noteRef.current);
    window.addEventListener('resize', apply);
    return () => {
      observer.disconnect();
      window.removeEventListener('resize', apply);
      panel.style.removeProperty('--scan-fixture-inset');
      panel.style.removeProperty('--scan-rail-overlap');
    };
  }, [panelRef, noteRef, active, fixtureNote]);
}
