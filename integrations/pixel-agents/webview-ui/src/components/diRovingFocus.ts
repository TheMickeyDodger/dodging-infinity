import type { KeyboardEvent } from 'react';
import { useRef } from 'react';

import { nextEntryIndex } from './diReceptionFormat.js';

/** The nearest ancestor that scrolls vertically and has something to scroll, or null. */
function scrollParent(el: HTMLElement): HTMLElement | null {
  for (let p = el.parentElement; p; p = p.parentElement) {
    const overflowY = getComputedStyle(p).overflowY;
    if ((overflowY === 'auto' || overflowY === 'scroll') && p.scrollHeight > p.clientHeight)
      return p;
  }
  return null;
}

/**
 * The browser's own focus scroll moves its list by whole pixels, so a row laid out at a fractional
 * offset can stay up to a pixel outside the list's visible area. Bring it fully in: scroll its list
 * by the whole-pixel ceiling of what still sticks out (only that list; nothing else moves).
 */
function settleInList(el: HTMLElement): void {
  const list = scrollParent(el);
  if (!list) return;
  const row = el.getBoundingClientRect();
  const top = list.getBoundingClientRect().top + list.clientTop;
  const bottom = top + list.clientHeight;
  if (row.bottom > bottom && row.height <= list.clientHeight)
    list.scrollTop += Math.ceil(row.bottom - bottom);
  else if (row.top < top) list.scrollTop -= Math.ceil(top - row.top);
}

/**
 * C1 (item 04): the reception desk's proven keyboard mechanism — ArrowUp/ArrowDown/Home/End
 * move focus between the rows of ONE list (`nextEntryIndex`), never past either end — for any
 * list of focusable rows. Rows are real buttons, so Enter and Space select them natively. The
 * focused row always lands whole inside its own list.
 */
export function useRovingFocus<E extends HTMLElement>() {
  const refs = useRef<Array<E | null>>([]);
  return {
    refAt: (index: number) => (el: E | null) => {
      refs.current[index] = el;
    },
    onKeyDown: (index: number, count: number) => (event: KeyboardEvent<E>) => {
      // Keys pressed on a control inside the row stay that control's.
      if (event.target !== event.currentTarget) return;
      const next = nextEntryIndex(index, event.key, count);
      if (next === null) return;
      event.preventDefault();
      const row = refs.current[next];
      if (!row) return;
      row.focus();
      settleInList(row);
    },
    focusFirst: () => refs.current[0]?.focus(),
  };
}
