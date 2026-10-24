/**
 * Minimize and restore, shared by every side panel and dialog (AC3). A minimized surface stays MOUNTED and is
 * only hidden, so its React state, its open disclosures and the text in its fields are kept as they were. What
 * hiding the DOM can lose is captured here and given back on restore: the scroll position of each scroll region,
 * and keyboard focus.
 *
 * The focus contract (the same for every surface, for mouse and keyboard): restore puts focus back on the LAST
 * CONTENT CONTROL that had focus in the surface, never on the minimize or restore toggles. When that control is
 * absent, removed, disabled or hidden by then, focus goes to the surface's own Minimize toggle, and failing that to
 * the surface itself; it never falls to the page body.
 *
 * Presentation only: minimizing or restoring reads, writes and sends nothing, and changes no decision.
 */
import type { RefObject } from 'react';
import { useEffect, useRef } from 'react';

/** What a surface keeps across minimize: each scrolled element's offsets, and the content control to give focus back to. */
export interface DiKeptView {
  scroll: ReadonlyArray<{ el: HTMLElement; top: number; left: number }>;
  /** The last content control that had focus (never a toggle), or null when there was none. */
  focus: HTMLElement | null;
  /** Evidence only: what actually had focus at the moment the surface was hidden (normally the Minimize toggle). */
  focusAtHide: string;
  /** Evidence only: the content control focus is to return to, described. */
  focusReturn: string;
}

/** A short, stable description of an element for evidence (its test id, else its tag and first words). */
export function describeFocus(el: Element | null): string {
  if (el === null || el === document.body) return 'BODY';
  const id = el.getAttribute('data-testid');
  return id ?? `${el.tagName.toLowerCase()} "${(el.textContent ?? '').trim().slice(0, 40)}"`;
}

/**
 * Remembers the last control inside `rootRef` that had focus, other than the surface's minimize toggle (and anything
 * inside it). Mouse and keyboard alike: whatever moved focus there.
 */
export function useLastContentFocus(
  rootRef: RefObject<HTMLElement | null>,
  toggleRef: RefObject<HTMLElement | null>,
): RefObject<HTMLElement | null> {
  const last = useRef<HTMLElement | null>(null);
  useEffect(() => {
    const root = rootRef.current;
    if (!root) return;
    const onFocusIn = (event: FocusEvent) => {
      const target = event.target;
      if (!(target instanceof HTMLElement) || target === root) return;
      const toggle = toggleRef.current;
      if (toggle && (target === toggle || toggle.contains(target))) return;
      last.current = target;
    };
    root.addEventListener('focusin', onFocusIn);
    return () => root.removeEventListener('focusin', onFocusIn);
  }, [rootRef, toggleRef]);
  return last;
}

/** Take what hiding could lose, from `root` (call it BEFORE the surface is hidden). */
export function keepView(root: HTMLElement, lastContent: HTMLElement | null = null): DiKeptView {
  const scroll = [root, ...root.querySelectorAll<HTMLElement>('*')]
    .filter((el) => el.scrollTop > 0 || el.scrollLeft > 0)
    .map((el) => ({ el, top: el.scrollTop, left: el.scrollLeft }));
  const focus = lastContent !== null && root.contains(lastContent) ? lastContent : null;
  return {
    scroll,
    focus,
    focusAtHide: describeFocus(document.activeElement),
    focusReturn: focus === null ? 'none (the Minimize toggle)' : describeFocus(focus),
  };
}

/** Give each kept scroll region its offsets back (call it once the surface is shown again). */
export function restoreScroll(kept: DiKeptView | null): void {
  if (kept === null) return;
  for (const s of kept.scroll) {
    if (!s.el.isConnected) continue;
    s.el.scrollTop = s.top;
    s.el.scrollLeft = s.left;
  }
}

/** Can focus be given to `el` now: still in the page, shown, enabled and outside any inert subtree? */
export function canTakeFocus(el: HTMLElement | null): el is HTMLElement {
  if (el === null || !el.isConnected) return false;
  if (el.closest('[inert]') !== null) return false;
  if (el.matches(':disabled')) return false;
  if (el.getClientRects().length === 0) return false;
  return (
    typeof el.checkVisibility !== 'function' || el.checkVisibility({ checkVisibilityCSS: true })
  );
}

/**
 * Hand focus to a RESTORE CHIP in the minimized tray and make sure it is EXPOSED (call it when a surface is minimized). The landing is a state of its own: the tray is a scroller, and where it holds more chips
 * than the window has room for (320 x 225) a chip focused without scrolling lands partly or wholly out of view, so focus is on a control nobody can see; a later Tab or wheel recovers, but recovery does not
 * excuse the landing. `preventScroll` keeps focus() itself from scrolling anything it chooses; the explicit `nearest` scroll then moves only what must move (the tray), and does nothing where the chip is
 * already in view.
 */
export function focusExposed(el: HTMLElement | null | undefined): void {
  if (!el) return;
  el.focus({ preventScroll: true });
  el.scrollIntoView({ block: 'nearest', inline: 'nearest' });
}

/**
 * Give focus back (call it once the surface is shown again): the kept content control when it can take focus,
 * else each fallback in order (the surface's Minimize toggle, then the surface itself). Returns what took focus.
 */
export function restoreFocus(
  kept: DiKeptView | null,
  ...fallbacks: ReadonlyArray<HTMLElement | null>
): HTMLElement | null {
  for (const target of [kept?.focus ?? null, ...fallbacks]) {
    if (!canTakeFocus(target)) continue;
    target.focus({ preventScroll: true });
    if (document.activeElement === target) return target;
  }
  return null;
}
