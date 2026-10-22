/**
 * The reception inbox's ONE open/closed state. The sidebar entry and the in-world reception desk are
 * two entrances to the same dialog, so they share this state (owned by App, read by the panel and by
 * the desk target) instead of each holding a copy. Opening records the control that opened it: focus
 * returns there when the dialog closes (diModal.ts reads `returnFocusTo` once, when it mounts).
 *
 * Presentation only: opening or closing the inbox reads, writes and sends nothing.
 */
import { useCallback, useMemo, useRef, useState } from 'react';

import type { InboxFilter } from './diReceptionItems.js';
import { EMPTY_FILTER } from './diReceptionItems.js';

export interface DiReceptionInboxAccess {
  open: boolean;
  /** Open the inbox; `opener` (when given) is where focus returns on close. */
  openFrom: (opener: HTMLElement | null) => void;
  close: () => void;
  /** The control that opened the inbox. */
  returnFocusTo: { current: HTMLElement | null };
  /**
   * The search and category filter. Held here, not in the dialog, so it is still in place when the
   * inbox is opened again after a Mission's details, its evidence or the request panel (presentation
   * only: it narrows what is shown and changes no outcome).
   */
  filter: InboxFilter;
  setFilter: (filter: InboxFilter) => void;
  /** The entry that last had focus: focus returns to it when the inbox is opened again. */
  lastKey: string | null;
  setLastKey: (key: string) => void;
}

export function useDiReceptionInbox(): DiReceptionInboxAccess {
  const [open, setOpen] = useState(false);
  const returnFocusTo = useRef<HTMLElement | null>(null);
  const [filter, setFilter] = useState<InboxFilter>(EMPTY_FILTER);
  const [lastKey, setLastKey] = useState<string | null>(null);
  const openFrom = useCallback((opener: HTMLElement | null) => {
    returnFocusTo.current = opener;
    setOpen(true);
  }, []);
  const close = useCallback(() => setOpen(false), []);
  return useMemo(
    () => ({ open, openFrom, close, returnFocusTo, filter, setFilter, lastKey, setLastKey }),
    [open, openFrom, close, filter, lastKey],
  );
}
