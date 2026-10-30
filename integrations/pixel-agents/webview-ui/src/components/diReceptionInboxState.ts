/**
 * The reception inbox's ONE open/closed state, owned by App and read by the panel. The inbox has ONE
 * entrance in the chrome: the rail's Reception on the scan face (the operational list's attention entry
 * in the harness face). Nothing in the world opens it (D-10: Reception is left-rail navigation only).
 * Opening records the control that opened it: focus returns there when the dialog closes (diModal.ts
 * reads `returnFocusTo` once, when it mounts).
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
  /**
   * AC3: minimized, the inbox stays open and mounted but hidden, and the rest of the app is usable; Restore (or
   * the entrance) brings it back as it was. Presentation only.
   */
  minimized: boolean;
  minimize: () => void;
  restore: () => void;
}

export function useDiReceptionInbox(): DiReceptionInboxAccess {
  const [open, setOpen] = useState(false);
  const returnFocusTo = useRef<HTMLElement | null>(null);
  const [filter, setFilter] = useState<InboxFilter>(EMPTY_FILTER);
  const [lastKey, setLastKey] = useState<string | null>(null);
  const [minimized, setMinimized] = useState(false);
  const minimizedNow = useRef(false);
  const setMinimizedNow = useCallback((value: boolean) => {
    minimizedNow.current = value;
    setMinimized(value);
  }, []);
  const openFrom = useCallback(
    (opener: HTMLElement | null) => {
      // The entrance used while the inbox is minimized restores it as it was; focus still returns, on close, to
      // the control that first opened it.
      if (!minimizedNow.current) returnFocusTo.current = opener;
      setMinimizedNow(false);
      setOpen(true);
    },
    [setMinimizedNow],
  );
  const close = useCallback(() => {
    setMinimizedNow(false);
    setOpen(false);
  }, [setMinimizedNow]);
  const minimize = useCallback(() => setMinimizedNow(true), [setMinimizedNow]);
  const restore = useCallback(() => setMinimizedNow(false), [setMinimizedNow]);
  return useMemo(
    () => ({
      open,
      openFrom,
      close,
      returnFocusTo,
      filter,
      setFilter,
      lastKey,
      setLastKey,
      minimized: open && minimized,
      minimize,
      restore,
    }),
    [open, openFrom, close, filter, lastKey, minimized, minimize, restore],
  );
}
