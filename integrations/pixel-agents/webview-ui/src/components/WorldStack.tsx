import type { ReactNode } from 'react';

import { DI_WORLD_COLUMN_LEFT_PX, DI_WORLD_COLUMN_MAX_PX } from '../constants.js';

/**
 * THE WORLD'S BOTTOM-LEFT COLUMN: the toolbar and the ambient legend in ONE flow (ROW 2 of the left rail, WorldLeftRail). The fixture label is NOT in it any more (candidate 7): a persistent required notice must
 * stay exposed, so it is pinned in the rail's footer, outside the strip that scrolls (WorldLeftRail).
 *
 * These used to be separate absolutely-positioned boxes with hand-chosen offsets, and a single always-on-top banner moved from place to place found a new
 * thing to paint over each time (the office content, the City popover, the notice's dismiss control, the docked request workspace). One flex column, anchored at
 * the bottom and growing upward, makes overlap among them impossible BY CONSTRUCTION: each sits in the flow above the one below, whatever their heights are
 * (a label that wraps to more lines). Order from the bottom is set by the children's `order`: the toolbar (0), the legend (2).
 * The City popover is NOT in this column any more: it sits over the view controls' row of the rail, so it can use the height the column does not.
 *
 * The column itself takes no pointer (children that are controls take it back), so the world beside and above it stays reachable. It is as tall as its content, and
 * the rail gives whatever height is left above it to the view controls and the popover: that is what keeps the column from meeting them.
 *
 * ITS WIDTH IS BOUNDED (DI_WORLD_COLUMN_MAX_PX): the request workspace keeps exactly that much world beside it (MR_WORLD_KEEP_PX), so nothing in the column,
 * whose legend text depends on live counts, can reach over the workspace's content. A legend that would be wider wraps.
 */
export function WorldStack({ children }: { children: ReactNode }) {
  return (
    <div
      className="di-world-stack row-start-2 col-start-1 justify-self-start flex flex-col-reverse items-start gap-6 pointer-events-none"
      style={{
        marginLeft: DI_WORLD_COLUMN_LEFT_PX,
        maxWidth: `min(calc(100% - ${DI_WORLD_COLUMN_LEFT_PX}px), ${DI_WORLD_COLUMN_MAX_PX}px)`,
      }}
      data-testid="di-world-stack"
    >
      {children}
    </div>
  );
}
