/**
 * The world yields the DI navigation rail's width (pass A, A2), and nothing else about the dock changes.
 *
 * The rail sits at the window's LEFT edge on the scan face. The world must give it exactly its width, or the rail would
 * paint over the world's own controls; the docked panel, the strip and the right inset are unchanged; and the rail
 * minimizes with the office, so minimizing hands its width back to the world. The operational face has no rail, and
 * for it the arithmetic is byte-for-byte what it was (diPanelLayout.test.ts pins that and is not changed).
 *
 * Expectations DERIVE from the constants (pass C): 1440 - DI_PANEL_WIDTH_PX - 200 (812 at the measured 428); at 900,
 * 900 - 428 - 200 = 272, which holds the scan face's converged controls (DI_WORLD_SCAN_CONTROLS_MIN_PX, 240, measured) at the
 * narrow end of the desktop matrix. Whole and non-overlapping there is proved RENDERED in diScanFace.test.ts.
 */
import { describe, expect, it } from 'vitest';

import {
  DI_PANEL_WIDTH_PX,
  DI_RAIL_WIDTH_PX,
  DI_WORLD_SCAN_CONTROLS_MIN_PX,
} from '../src/constants.ts';
import { diDockFor } from '../src/diPanelLayout.ts';

describe('the world yields the rail its width on the scan face', () => {
  it('the rail is 200 px, as 01-home.jpg draws it in a 1280 px frame', () => {
    expect(DI_RAIL_WIDTH_PX).toBe(200);
  });
  it('at 1440 the world is the window minus the 200 px rail and the panel; the right side is unchanged', () => {
    const withRail = diDockFor(1440, false, false, DI_RAIL_WIDTH_PX);
    const without = diDockFor(1440, false, false);
    expect(withRail.worldPx).toBe(1440 - DI_PANEL_WIDTH_PX - DI_RAIL_WIDTH_PX);
    expect({ ...withRail, worldPx: 0 }).toEqual({ ...without, worldPx: 0 });
  });
  it('at 900, the narrow end of the desktop matrix, the world still holds its (converged) controls: offered against the scan face\'s measured threshold', () => {
    const dock = diDockFor(900, false, false, DI_RAIL_WIDTH_PX);
    expect(dock.worldPx).toBe(900 - DI_PANEL_WIDTH_PX - DI_RAIL_WIDTH_PX);
    expect(dock.worldPx).toBeGreaterThanOrEqual(DI_WORLD_SCAN_CONTROLS_MIN_PX);
    expect(dock.worldControls).toBe(true);
  });
  it("minimizing the office minimizes the rail with it: the world gets the rail's width back", () => {
    expect(diDockFor(1440, true, true, DI_RAIL_WIDTH_PX).worldPx).toBe(1440 - 44);
    expect(diDockFor(1440, true, true, DI_RAIL_WIDTH_PX)).toEqual(diDockFor(1440, true, true));
  });
  it('the face with no rail is unchanged at every width', () => {
    for (const w of [320, 640, 900, 1280, 1440, 1600])
      for (const collapsed of [false, true])
        for (const strip of [false, true])
          expect(diDockFor(w, collapsed, strip, 0)).toEqual(diDockFor(w, collapsed, strip));
  });
});
