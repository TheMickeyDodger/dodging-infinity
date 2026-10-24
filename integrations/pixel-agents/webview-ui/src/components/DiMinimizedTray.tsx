/**
 * AC3: the ONE place a minimized surface's restore control is drawn. App renders the tray (top right of the world,
 * beside the docked panel) and provides it here; each minimized surface portals its restore control into it, so
 * several minimized surfaces line up instead of covering one another. Where no tray is provided (a harness that
 * mounts one surface alone) the control is drawn in place.
 *
 * Presentation only: the tray holds controls; it reads, writes and sends nothing.
 */
import type { ReactNode } from 'react';
import { useContext } from 'react';
import { createPortal } from 'react-dom';

import { DiMinimizedTrayContext } from './diMinimizedTrayContext.js';

/** Draw `children` (a restore control) in the minimized tray, or in place when there is none. */
export function DiMinimizedChip({ children }: { children: ReactNode }) {
  const tray = useContext(DiMinimizedTrayContext);
  return tray ? createPortal(children, tray) : <>{children}</>;
}
