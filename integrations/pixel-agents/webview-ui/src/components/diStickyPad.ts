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

/**
 * Reserve the strip `noteRef` covers while it is position: sticky (its sticky offset, `top`, plus its height: a note stuck BELOW another sticky strip, as the
 * scan stage's page head is below fixture mode's label, covers both) as scroll-padding-top on `scrollerRef`. Inactive: nothing is reserved.
 * Re-measured when the strip RESIZES and when the scroller's OWN children change: the offset can change with no change of size, through the cascade, when
 * a strip mounts or leaves beside it (B2e R22: fixture mode's label mounting before a scan page moves its head's top from 0 to 30px).
 */
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
      const style = getComputedStyle(note);
      const sticky = style.position === 'sticky';
      const offset = Math.max(0, parseFloat(style.top) || 0);
      scroller.style.scrollPaddingTop = sticky
        ? `${Math.ceil(offset + note.getBoundingClientRect().height)}px`
        : '';
    };
    apply();
    const observer = new ResizeObserver(apply);
    observer.observe(note);
    const children = new MutationObserver(apply);
    children.observe(scroller, { childList: true });
    return () => {
      observer.disconnect();
      children.disconnect();
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
  /** B3: the minimized-surface strip's element (App provides it through the tray context): it starts below the fixture label too, as the rail and the screen do. */
  tray: HTMLElement | null = null,
): void {
  useEffect(() => {
    const panel = panelRef.current;
    if (!active || !panel) return;
    const apply = () => {
      const note = noteRef.current;
      const inset = fixtureNote && note ? `${Math.ceil(note.getBoundingClientRect().height)}px` : '0px';
      panel.style.setProperty('--scan-fixture-inset', inset);
      // the strip is outside this panel, so it takes the same measured height on its own element (diOperationalPanel.css: top)
      tray?.style.setProperty('--scan-tray-top', inset);
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
      tray?.style.removeProperty('--scan-tray-top');
    };
  }, [panelRef, noteRef, active, fixtureNote, tray]);
}
