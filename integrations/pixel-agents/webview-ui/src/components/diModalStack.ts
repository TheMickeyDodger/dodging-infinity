/**
 * The ONE owner of the app root's `inert` guard, shared by every modal dialog (the office's details dialog, the
 * reception inbox, the live code review). Now that a dialog can be minimized (AC3: still mounted, hidden), several
 * dialogs can exist at once, so the guard cannot belong to whichever dialog wrote it last.
 *
 * Invariants:
 *  - #root is inert if and only if at least one dialog is in FRONT (claimed and not released).
 *  - A dialog changes the guard only through its own claim and release; a hidden or background dialog never clears
 *    a guard another dialog holds.
 *  - `release` reports whether the dialog was the one in front at that moment, so only that dialog gives focus back
 *    to its opener on close; a hidden dialog closing moves no focus.
 *  - Close-time focus never falls to the page body (`returnFocusOnClose`): the twin of the restore fallback in
 *    diMinimize.ts. If the recorded opener can no longer take focus, focus goes to a fixed, named, labelled control.
 *
 * Presentation only: this decides what is inert and where focus may return; it reads, writes and sends nothing.
 */

import { canTakeFocus } from './diMinimize.js';

/** Dialogs in front, the last one foremost. */
const inFront: object[] = [];

function applyGuard(): void {
  const root = document.getElementById('root');
  if (root === null) return;
  if (inFront.length > 0) root.setAttribute('inert', '');
  else root.removeAttribute('inert');
}

/** `owner` is shown in front (on open, or on restore from minimized). */
export function claimFront(owner: object): void {
  const at = inFront.indexOf(owner);
  if (at >= 0) inFront.splice(at, 1);
  inFront.push(owner);
  applyGuard();
}

/**
 * `owner` stops being in front (it is minimized, or it closes). Returns true only when it WAS the foremost dialog,
 * which is the one case in which its close may give focus back to its opener.
 */
export function releaseFront(owner: object): boolean {
  const at = inFront.indexOf(owner);
  if (at < 0) return false;
  const wasForemost = at === inFront.length - 1;
  inFront.splice(at, 1);
  applyGuard();
  return wasForemost;
}

/** Whether `owner` is the foremost dialog now (its key and focus guards act only then). */
export function isForemost(owner: object): boolean {
  return inFront.length > 0 && inFront[inFront.length - 1] === owner;
}

/**
 * Where focus goes when a closing dialog's opener can no longer take it (absent, disconnected, disabled, hidden, or
 * inside an inert subtree), in order: the office Find box (always present with the office panel, labelled "Find a
 * Mission or agent"), then the office panel's restore control (labelled "Restore office panel", present when the panel
 * is minimized and the Find box is hidden). Fixed here once, the same for every surface.
 */
export const CLOSE_FOCUS_FALLBACKS = [
  '[data-testid="di-find-input"]',
  '[data-testid="di-office-panel-collapsed"]',
] as const;

/**
 * Close-time focus, called AFTER `releaseFront` (so this dialog's guard is already gone): the dialog that was
 * foremost gives focus back to its opener; if the opener can no longer take focus it goes to the first fallback that
 * can, never to the page body. A dialog that was not foremost moves no focus. Returns the control that took focus,
 * or null when none was moved. Close-time focus only: what closes a dialog, and when, is not decided here.
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

/** How many dialogs are in front (for tests and evidence). */
export function frontCount(): number {
  return inFront.length;
}
