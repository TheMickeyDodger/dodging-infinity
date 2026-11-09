/**
 * The ONE owner of the app root's `inert` guard, and of which surface is in FRONT, shared by every modal dialog (the
 * office's details dialog, the reception inbox, the live code review, the shared Modal) and (Direction B) every non-modal
 * anchored popover. Now that a dialog can be minimized (AC3: still mounted, hidden), several dialogs can exist at once,
 * so the guard cannot belong to whichever dialog wrote it last.
 *
 * A claim is MODAL (the default, and every dialog) or NON-MODAL (a popover). Front-ness and modal inertness are two
 * different things: a popover is in front, so it owns Escape (`isForemost`), but it inerts nothing, so the app behind it
 * stays usable and a popover drawn inside #root does not disable itself.
 *
 * Invariants:
 *  - #root is inert if and only if at least one MODAL claim is in front (claimed and not released).
 *  - `isForemost` and `releaseFront`'s answer span ALL claims, modal and non-modal: the last claim is the foremost one.
 *  - A surface changes the guard only through its own claim and release; a hidden or background surface never clears
 *    a guard another surface holds.
 *  - `release` reports whether the surface was the one in front at that moment, so only that surface gives focus back
 *    to its opener on close; a hidden or background surface closing moves no focus.
 *  - Close-time focus is never MOVED to the page body (`returnFocusOnClose`): the twin of the restore fallback in diMinimize.ts. If the recorded
 *    opener can no longer take focus, focus goes to a fixed, named, labelled control. If NO candidate can take focus, nothing is moved (null is
 *    returned) and focus stays where it was; with a non-modal surface closing over a modal one, every page fallback can be inside the modal's inert
 *    root, and this helper keeps no host target to restore into (a B2 integration case).
 *
 * Presentation only: this decides what is inert, what is in front and where focus may return; it reads, writes and
 * sends nothing.
 */

import { canTakeFocus } from './diMinimize.js';

/** How a claim behaves while it is in front. */
export interface FrontOptions {
  /**
   * true (the DEFAULT): a modal dialog, so #root is inert while it is in front. false: a non-modal popover, which owns
   * the keys while it is foremost and inerts nothing. Every caller that passes no options is a modal claim, as before.
   */
  readonly modal?: boolean;
}

interface Claim {
  readonly owner: object;
  readonly modal: boolean;
}

/** Surfaces in front, the last one foremost, modal or not. */
const inFront: Claim[] = [];

function applyGuard(): void {
  const root = document.getElementById('root');
  if (root === null) return;
  if (inFront.some((claim) => claim.modal)) root.setAttribute('inert', '');
  else root.removeAttribute('inert');
}

function indexOfOwner(owner: object): number {
  return inFront.findIndex((claim) => claim.owner === owner);
}

/**
 * `owner` is shown in front (on open, or on restore from minimized). A second claim by the same owner moves it to the
 * front and re-states its modality.
 */
export function claimFront(owner: object, options: FrontOptions = {}): void {
  const at = indexOfOwner(owner);
  if (at >= 0) inFront.splice(at, 1);
  inFront.push({ owner, modal: options.modal ?? true });
  applyGuard();
}

/**
 * `owner` stops being in front (it is minimized, or it closes). Returns true only when it WAS the foremost claim of all,
 * modal or not, which is the one case in which its close may give focus back to its opener.
 */
export function releaseFront(owner: object): boolean {
  const at = indexOfOwner(owner);
  if (at < 0) return false;
  const wasForemost = at === inFront.length - 1;
  inFront.splice(at, 1);
  applyGuard();
  return wasForemost;
}

/** Whether `owner` is the foremost claim now, modal or not (its key and focus guards act only then). */
export function isForemost(owner: object): boolean {
  return inFront.length > 0 && inFront[inFront.length - 1].owner === owner;
}

/**
 * Where focus goes when a closing dialog's opener can no longer take it (absent, disconnected, disabled, hidden, or
 * inside an inert subtree), in order: the office Find box (always present with the office panel, labelled "Find a
 * Mission or agent"), then the office panel's restore control (labelled "Restore office panel", present when the panel
 * is minimized and the Find box is hidden), then (D1b) the scan face's rail Home control (labelled "DI World, Home",
 * always drawn with the scan face's panel, which has no Find box: measured, a dialog whose opener was gone or could not
 * take focus, such as the hooks notice opened from a non-focusable link, left focus on the page body there). Fixed here
 * once, the same for every surface.
 */
export const CLOSE_FOCUS_FALLBACKS = [
  '[data-testid="di-find-input"]',
  '[data-testid="di-office-panel-collapsed"]',
  '[data-testid="di-rail-home"]',
] as const;

/**
 * Close-time focus, called AFTER `releaseFront` (so this surface's claim is already gone): the surface that was
 * foremost gives focus back to its opener; if the opener can no longer take focus it goes to the first fallback that
 * can; it never MOVES focus to the page body. If no candidate can take focus, it moves nothing and returns null (focus stays where it was). A
 * surface that was not foremost moves no focus. Returns the control that took focus, or null when none was moved. Close-time focus only: what closes a surface, and when, is not decided here.
 */
export function returnFocusOnClose(
  wasForemost: boolean,
  opener: HTMLElement | null,
): HTMLElement | null {
  if (!wasForemost) return null;
  const candidates = [
    opener,
    ...CLOSE_FOCUS_FALLBACKS.map((selector) => document.querySelector<HTMLElement>(selector)),
  ];
  for (const target of candidates) {
    if (!canTakeFocus(target)) continue;
    target.focus();
    if (document.activeElement === target) return target;
  }
  return null;
}

/** How many surfaces are in front, modal or not (for tests, evidence, and the scan face's Escape ladder gate). */
export function frontCount(): number {
  return inFront.length;
}

/** How many MODAL claims are in front (for tests and evidence): #root is inert exactly while this is above 0. */
export function modalFrontCount(): number {
  return inFront.filter((claim) => claim.modal).length;
}
