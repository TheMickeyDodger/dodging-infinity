import type { KeyboardEvent } from 'react';
import { useRef } from 'react';

import { nextEntryIndex } from './diReceptionFormat.js';

/**
 * C1 (item 04): the reception desk's proven keyboard mechanism — ArrowUp/ArrowDown/Home/End
 * move focus between the rows of ONE list (`nextEntryIndex`), never past either end — for any
 * list of focusable rows. Rows are real buttons, so Enter and Space select them natively.
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
      refs.current[next]?.focus();
    },
    focusFirst: () => refs.current[0]?.focus(),
  };
}
