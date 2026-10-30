import type { ReactNode } from 'react';

import {
  DI_WORLD_COLUMN_LEFT_PX,
  DI_WORLD_COLUMN_MAX_PX,
  WORLD_RAIL_BOTTOM_PX,
  WORLD_RAIL_TOP_PX,
  WORLD_VIEW_CONTROL_MIN_PX,
  WORLD_VIEW_CONTROLS_TOP_PX,
} from '../constants.js';

/** The first row never gets less than the zoom buttons and their gap (the controls' offset inside it) plus one camera stop, so one stop is always shown. */
const FIRST_ROW_MIN_PX = WORLD_VIEW_CONTROLS_TOP_PX - WORLD_RAIL_TOP_PX + WORLD_VIEW_CONTROL_MIN_PX;
/** The strip is as wide as the column and its left offset (the toolbar's right edge), so its own scroll bar (if any) never sits at the far side of the world. */
const RAIL_WIDTH_PX = DI_WORLD_COLUMN_LEFT_PX + DI_WORLD_COLUMN_MAX_PX;

/**
 * THE WORLD'S LEFT STRIP IN ONE FLOW: the zoom buttons and the camera-view controls above, the bottom-left column (WorldStack: toolbar, fixture label, ambient legend) below, and
 * the City popover over the controls' row.
 *
 * They used to be positioned independently (the view controls from the top with a fixed bottom gap sized for the toolbar alone, the column from the bottom), so a
 * column that grew taller (the label and the legend above the toolbar) met the controls: in a short window the lower stops (Atrium, Observatory, Map, Districts at
 * 720 x 450 at 2x; Districts at 320 x 568) and the open Districts list were painted under the label and the legend. Two groups at one corner, each placed by hand, find
 * each other's content whenever either grows; ONE GRID with two rows makes that impossible by construction. ROW 2 is the column, as tall as it is. ROW 1 is whatever
 * it leaves, and never less than FIRST_ROW_MIN_PX (the zoom buttons, their gap, and one camera stop): the zoom buttons sit at its top, as they always did, and the camera controls
 * below them scroll INSIDE what is left (every option stays, and a stop scrolls into view when it is focused); the City popover sits at the bottom of the row, over the controls as
 * it always did (they are suspended and inert while it is open), growing upward and shrinking with its own scroll region. It is absolutely positioned in the grid, in row 1's own area, so
 * it never sizes the row (its content once did, which inflated the grid to 891 + 106 + 6 px at every window) and cannot spill into row 2. Nothing in row 1 can reach row 2, and the zoom buttons are
 * IN the flow, not a separate group the rest has to avoid: a strip that scrolls would otherwise carry the controls over them.
 *
 * WHERE EVEN THAT DOES NOT FIT (a window shorter than the column plus FIRST_ROW_MIN_PX: 320 x 225 is 400% zoom on a 1280 x 900 window, with or without the fixture label), THE
 * STRIP ITSELF SCROLLS, anchored to its bottom (column-reverse) so the toolbar is in view at rest; the zoom buttons scroll out of view first, and everything stays reachable by wheel,
 * by touch and by keyboard. The camera controls are NEVER REMOVED for want of height: removal would be permanent navigation loss for a person who is zoomed for a reason, and telling
 * them to zoom out or enlarge the window to reach navigation defeats the accommodation. Reached and visible, or neither: a stop scrolled out of view scrolls into view when it takes
 * focus, and anything not rendered is not in the tab order (the width rule at 320 with the office open removes the controls, and they return when the office is minimized).
 *
 * THE FRAME, THE SCROLLER AND THE PINNED FOOTER (candidate 7). The strip is a FRAME (a flex column) holding the SCROLLER (everything above, which scrolls where the window is short) and a FOOTER that
 * does NOT scroll: the PERSISTENT REQUIRED NOTICE (the fixture label) and, in a narrow world, the version label. A persistent notice that lives inside scrolling content is exposed only while the
 * scroll position happens to show it: in candidate 6 the City's reveal, and a focus on any camera control, scrolled the strip and carried the label out of a 225 px window while synthetic content stayed
 * on screen. A notice outside the scrollport cannot be scrolled out BY CONSTRUCTION, and it never overlays content (it is a flex sibling, not a sticky layer), so no focus scroll can land under it.
 * Every control still scrolls (recoverably reachable); only the notice is pinned. The scroller is NOT a tab stop (tabIndex -1): Chrome makes a scroller with no tabbable content keyboard-focusable,
 * and with the office open at 320 x 225 (the world yielded, its controls inert) that was a stop wholly outside the window. Its focusable children still scroll it when they take focus.
 *
 * DOM order is the tab order and is unchanged in spirit: the zoom buttons and the view controls, the column (toolbar first), then the popover, which a person has just opened from
 * the toolbar. The strip takes no pointer (its children that are controls take it back, and a wheel over one scrolls it), is as wide as the column, and holds the one layer (30)
 * the column and the controls each used to state, below the covering request workspace (diMissionRequestStacking.test.ts).
 */
export function WorldLeftRail({
  top,
  popover,
  notice,
  onFooter,
  children,
}: {
  /** The zoom buttons and the camera-view controls (already inside their controls gate), or nothing. */
  top: ReactNode;
  /** The City popover (already inside its own controls gate), or nothing. */
  popover: ReactNode;
  /** The persistent required notice (the fixture label), pinned below the scrolling strip, or nothing. */
  notice: ReactNode;
  /** The footer element, for whatever else must be pinned with the notice (the version label in a narrow world, portalled in), or null on unmount. */
  onFooter: (el: HTMLDivElement | null) => void;
  /** The bottom-left column (WorldStack). */
  children: ReactNode;
}) {
  return (
    <div
      className="di-world-rail-frame absolute left-0 z-30 flex flex-col gap-6 pointer-events-none"
      style={{
        top: WORLD_RAIL_TOP_PX,
        bottom: WORLD_RAIL_BOTTOM_PX,
        width: `min(100%, ${RAIL_WIDTH_PX}px)`,
      }}
      data-testid="di-world-rail-frame"
      // what the world's disclosure (scan face) shows and hides
      id="di-world-controls"
    >
      <div
        className="di-world-rail min-h-0 flex-1 flex flex-col-reverse overflow-y-auto overflow-x-hidden pointer-events-none"
        // not a tab stop: see the doc comment (its focusable children still scroll it)
        tabIndex={-1}
        data-testid="di-world-rail"
      >
        <div
          className="di-world-rail-grid relative grid shrink-0 grid-cols-[minmax(0,1fr)] gap-y-6 pointer-events-none"
          // a DEFINITE height (the scroller's) so the first row can be what the column leaves, and a content minimum so that, where that is less than FIRST_ROW_MIN_PX plus the column, the
          // grid grows past the scroller and the scroller scrolls
          style={{
            height: '100%',
            minHeight: 'min-content',
            gridTemplateRows: `minmax(${FIRST_ROW_MIN_PX}px, 1fr) auto`,
          }}
        >
          {/* relative: the camera controls are positioned inside this cell (absolutely, so their height never sizes the row) */}
          <div className="di-rail-top relative row-start-1 col-start-1 min-h-0 pointer-events-none">
            {top}
          </div>
          {children}
          {popover}
        </div>
      </div>
      <div
        ref={onFooter}
        className="di-world-rail-footer relative shrink-0 flex items-end justify-between gap-6 pointer-events-none empty:hidden"
        style={{
          marginLeft: DI_WORLD_COLUMN_LEFT_PX,
          maxWidth: `min(calc(100% - ${DI_WORLD_COLUMN_LEFT_PX}px), ${DI_WORLD_COLUMN_MAX_PX}px)`,
        }}
        data-testid="di-world-rail-footer"
      >
        {notice}
      </div>
    </div>
  );
}
