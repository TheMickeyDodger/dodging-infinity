/**
 * B2e R22, finding 1, and B3's first boundary (1A-R): TRUTHFUL FOCUS ISOLATION of what the shown request workspace COVERS.
 *
 * The docked request workspace is drawn over the open scan stage BY DESIGN (z 44 over the stage's 43: diMissionRequest.css, pinned by
 * diMissionRequestStacking.test.ts), so a stage control it overlaps is covered. The overlap is HORIZONTAL and may be PARTIAL: a control can be covered
 * across part of its box and EXPOSED across the rest, a sliver past the workspace's edge that a pointer can reach. Before this, Tab could also land
 * focus on a covered control where nobody could see it. While the workspace is shown, every control in a stage's Tab order that the workspace's column
 * overlaps is marked `inert` (with DI_COVERED_MARK beside it), which also takes it out of hit-testing: a pointer on an exposed sliver does nothing.
 * Nothing is hidden, nothing moves, nothing changes on focus. So that an exposed sliver is never left looking available (B2e R22, X1, measured), a
 * covered CONTROL is drawn with the stage's own UNAVAILABLE treatment (diScanPanel.css reads the mark), and the docked workspace states the reason and
 * its Close route in text (diMissionRequest.css reads the mark).
 *  - The marks follow the geometry and the content (a ResizeObserver on the panel and the workspace, a MutationObserver on the panel, the window's resize),
 *    and ONLY marks set here are ever cleared: no control is left inert by a stale attribute, and an `inert` set elsewhere is never touched.
 *  - The workspace is a full-height column (fixed, top 0, bottom 0), so a control is covered when its box overlaps the column ACROSS (left and right),
 *    wherever it is scrolled to: a control scrolled out of view would come into view under the column.
 *  - A covered SCROLL CONTAINER that would itself be a Tab stop is isolated too: Chrome makes a scroller with no focusable children focusable, so once
 *    its covered children are isolated it would become a stop of its own, under the workspace. It counts only when it is scrollable and no focusable
 *    descendant is left live (one that can REALLY take focus: not disabled, rendered); a scroller that still holds such a control is never marked.
 *  - CLOSING releases it FIRST. The workspace returns focus to its opener in a layout-effect cleanup, which runs BEFORE this hook's passive cleanup, so
 *    it calls releaseCoverIsolation() immediately before it chooses its target: the opener it returns to may be one this module isolated, and focus()
 *    on an inert control is a silent no-op that drops focus to the page body (R22, measured: b2e-r22-opener-red-1).
 *  - A FOCUSED control that becomes covered (the window narrows, wider reading widens the workspace) hands focus to the workspace's own heading (its
 *    title, tabindex -1): a labelled landing beside its exits (Wider reading, Minimize, Close).
 *
 * THE TRADE, on the record. It resolves Focus Not Obscured WITHOUT a change of context on focus. Its costs:
 *  - a SMALLER Tab ring while the workspace covers the stage, every covered action keeping a labelled keyboard route (the workspace's own Close and
 *    Minimize everywhere; "Bring the ask back here" where it is seen);
 *  - `inert` also removes the covered content from the accessibility tree, so a screen-reader user cannot read the covered stage while the workspace is
 *    shown. That is the standard behaviour behind a covering surface, but the workspace is NON-MODAL, so someone could reasonably expect to read
 *    behind it: a considered trade, not parity and not a silent consequence.
 *
 * Presentation only: it reads boxes and sets one attribute and its mark; it reads, writes and sends nothing else.
 */
import type { RefObject } from 'react';
import { useEffect } from 'react';

/** The mark beside each `inert` this module set, and the only `inert` it ever clears. */
export const DI_COVERED_MARK = 'data-di-covered';
/** The covering surface: the request workspace while it is shown (a minimized workspace is `hidden`). */
const COVER = '.di-mr:not([hidden])';
/** Where focus goes when the control it was on becomes covered: the workspace's own heading. */
const RESCUE = '.di-mr-title';
const TABBABLE = 'a[href], button, input, select, textarea, summary, [tabindex], [contenteditable="true"]';

const inTabOrder = (el: HTMLElement) => {
  const t = el.getAttribute('tabindex');
  return t === null || Number(t) >= 0;
};
/** Can `el` REALLY take focus from the keyboard: in the Tab order, not disabled, rendered, not visibility: hidden. */
const focusable = (el: HTMLElement) =>
  inTabOrder(el) && (el as HTMLButtonElement).disabled !== true && el.getClientRects().length > 0 && getComputedStyle(el).visibility !== 'hidden';

/** Inside `scope`, is `el` out of the Tab order already: within an element this pass isolates, or within an `inert` set elsewhere (not ours, not `scope`)? */
function isolatedOrForeign(el: HTMLElement, scope: HTMLElement, want: ReadonlySet<HTMLElement>): boolean {
  for (let a: HTMLElement | null = el.parentElement; a !== null && a !== scope; a = a.parentElement) {
    if (want.has(a)) return true;
    if (a.hasAttribute('inert') && !a.hasAttribute(DI_COVERED_MARK)) return true;
  }
  return false;
}

/**
 * Clear EVERY mark this module set inside `scope` (the document by default), with its `inert`, NOW: the one home of the clear. The request workspace
 * calls it as it closes, immediately before it returns focus (see above); the hook calls it when the workspace is no longer shown. An `inert` set
 * elsewhere carries no mark and is never touched.
 */
export function releaseCoverIsolation(scope: ParentNode = document): void {
  for (const el of scope.querySelectorAll<HTMLElement>(`[${DI_COVERED_MARK}]`)) {
    el.removeAttribute('inert');
    el.removeAttribute(DI_COVERED_MARK);
  }
}

/** One pass over `root`'s stages: mark what the cover overlaps, unmark (only marks set here) what it no longer overlaps, rescue a focus that got covered. */
export function isolateCovered(root: HTMLElement, cover: HTMLElement | null): void {
  const c = cover?.getBoundingClientRect() ?? null;
  const want = new Set<HTMLElement>();
  if (c !== null && c.width > 0) {
    for (const stage of root.querySelectorAll<HTMLElement>('.di-scan-stage')) {
      for (const el of stage.querySelectorAll<HTMLElement>(TABBABLE)) {
        if (!inTabOrder(el)) continue;
        const r = el.getBoundingClientRect();
        if (r.width > 0 && r.height > 0 && r.left < c.right && r.right > c.left) want.add(el);
      }
    }
  }
  // a covered scroller that would be a Tab stop of its own once its covered children are isolated (innermost first)
  if (c !== null && c.width > 0) {
    for (const stage of root.querySelectorAll<HTMLElement>('.di-scan-stage')) {
      const scrollers = [stage, ...stage.querySelectorAll<HTMLElement>('*')].filter((el) => {
        const st = getComputedStyle(el);
        const scrolls = /(auto|scroll)/.test(st.overflowY) || /(auto|scroll)/.test(st.overflowX);
        return scrolls && (el.scrollHeight > el.clientHeight + 1 || el.scrollWidth > el.clientWidth + 1);
      });
      for (const sc of scrollers.reverse()) {
        const r = sc.getBoundingClientRect();
        if (!(r.width > 0 && r.height > 0 && r.left < c.right && r.right > c.left)) continue;
        const live = [...sc.querySelectorAll<HTMLElement>(TABBABLE)].some((el) => focusable(el) && !want.has(el) && !isolatedOrForeign(el, sc, want));
        const liveScroller = scrollers.some((inner) => inner !== sc && sc.contains(inner) && !want.has(inner));
        if (!live && !liveScroller) want.add(sc);
      }
    }
  }
  for (const el of root.querySelectorAll<HTMLElement>(`[${DI_COVERED_MARK}]`)) {
    if (want.has(el)) continue;
    el.removeAttribute('inert');
    el.removeAttribute(DI_COVERED_MARK);
  }
  const focused = document.activeElement;
  let rescue = false;
  for (const el of want) {
    // already ours, or another's inert: never touched
    if (el.hasAttribute(DI_COVERED_MARK) || el.hasAttribute('inert')) continue;
    if (focused !== null && el.contains(focused)) rescue = true;
    el.setAttribute(DI_COVERED_MARK, '');
    el.setAttribute('inert', '');
  }
  if (rescue) cover?.querySelector<HTMLElement>(RESCUE)?.focus();
}

/** While `active` (the request workspace is shown), keep `rootRef`'s stages isolated where the workspace covers them; clear every mark otherwise. */
export function useCoverIsolation(rootRef: RefObject<HTMLElement | null>, active: boolean): void {
  useEffect(() => {
    const root = rootRef.current;
    if (!root) return;
    const clear = () => releaseCoverIsolation(root);
    if (!active) {
      clear();
      return;
    }
    let frame = 0;
    const apply = () => {
      frame = 0;
      isolateCovered(root, document.querySelector<HTMLElement>(COVER));
    };
    const schedule = () => {
      if (frame === 0) frame = requestAnimationFrame(apply);
    };
    apply();
    const mutations = new MutationObserver(schedule);
    mutations.observe(root, {
      childList: true,
      subtree: true,
      attributes: true,
      attributeFilter: ['class', 'hidden', 'open', 'data-ask', 'disabled', 'tabindex', 'aria-expanded'],
    });
    const sizes = new ResizeObserver(schedule);
    sizes.observe(root);
    const cover = document.querySelector<HTMLElement>(COVER);
    if (cover) sizes.observe(cover);
    window.addEventListener('resize', schedule);
    return () => {
      mutations.disconnect();
      sizes.disconnect();
      window.removeEventListener('resize', schedule);
      if (frame !== 0) cancelAnimationFrame(frame);
      clear();
    };
  }, [rootRef, active]);
}
