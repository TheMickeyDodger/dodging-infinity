import assert from 'node:assert/strict';

import { test } from 'vitest';

import {
  DI_MINIMIZED_STRIP_WIDTH_PX,
  DI_PANEL_WIDTH_PX,
  DI_WORLD_COLUMN_LEFT_PX,
  DI_WORLD_COLUMN_MAX_PX,
  DI_WORLD_CONTROLS_MIN_PX,
  DI_WORLD_ROOMY_PX,
  WORLD_RAIL_TOP_PX,
  WORLD_VIEW_CONTROL_MIN_PX,
  WORLD_VIEW_CONTROLS_TOP_PX,
} from '../src/constants.ts';
import { diDockFor } from '../src/diPanelLayout.ts';

/*
 * The docked layout arithmetic, pinned. It is what the real App and the panel harness both use, so these numbers are what a person sees: the
 * panel is 420 wide where the window can hold it and otherwise exactly what the strip leaves, the strip counts only while it holds a restore
 * control, the world gets what is left (and yields entirely at 0, a recorded behaviour), the fixture words float or sit in the panel, and the
 * world's controls are offered only while the world can hold them whole.
 *
 * THESE TESTS STATE THE REQUIREMENT, NOT THE IMPLEMENTATION. The requirement that matters most is the one a previous version of this file got
 * backwards: at 320 (the narrowest width supported, SC 1.4.10), once the office is MINIMIZED, the existing world navigation is available again.
 * Minimizing puts the office's own restore control into the 44 px strip, so the world is 276 wide there, and the controls must be offered at
 * that width. The proof that they are really drawn and reachable is rendered (diWorldYield.test.ts, which never consults this function); this
 * file only guards the arithmetic from making that outcome impossible.
 */

/** The narrowest width supported, and the strip that always exists once the office is minimized (the office's own restore control lives in it). */
const NARROWEST_PX = 320;

test('desktop is unchanged: 1440 wide, panel 420, strip beside it only while it holds a control, controls and floating fixture words offered', () => {
  assert.deepEqual(diDockFor(1440, false, false), {
    panelPx: 420,
    stripPx: 0,
    trayRightPx: 420,
    insetPx: 420,
    worldPx: 1020,
    worldControls: true,
    worldNarrow: false,
    fixtureWords: 'floating',
  });
  assert.deepEqual(diDockFor(1440, false, true), {
    panelPx: 420,
    stripPx: 44,
    trayRightPx: 420,
    insetPx: 464,
    worldPx: 976,
    worldControls: true,
    worldNarrow: false,
    fixtureWords: 'floating',
  });
  assert.deepEqual(diDockFor(1440, true, true), {
    panelPx: 420,
    stripPx: 44,
    trayRightPx: 0,
    insetPx: 44,
    worldPx: 1396,
    worldControls: true,
    worldNarrow: false,
    fixtureWords: 'floating',
  });
});

test('at 320 with the office OPEN (SC 1.4.10): the panel takes what the window leaves, the strip stays inside the window beside it, and the world yields entirely', () => {
  const noStrip = diDockFor(NARROWEST_PX, false, false);
  assert.deepEqual(noStrip, {
    panelPx: 320,
    stripPx: 0,
    trayRightPx: 320,
    insetPx: 320,
    worldPx: 0,
    worldControls: false,
    worldNarrow: true,
    fixtureWords: 'in-panel',
  });
  const strip = diDockFor(NARROWEST_PX, false, true);
  assert.deepEqual(strip, {
    panelPx: 276,
    stripPx: 44,
    trayRightPx: 276,
    insetPx: 320,
    worldPx: 0,
    worldControls: false,
    worldNarrow: true,
    fixtureWords: 'in-panel',
  });
  assert.equal(
    strip.panelPx + strip.stripPx,
    NARROWEST_PX,
    'panel and strip together fill the window and neither is pushed off it',
  );
});

test('REQUIREMENT: at 320, once the office is MINIMIZED (the strip holds its restore control, so the world is 276 wide), the world, its CONTROLS and the floating fixture words are back', () => {
  const minimized = diDockFor(NARROWEST_PX, true, true);
  assert.equal(
    minimized.worldPx,
    NARROWEST_PX - DI_MINIMIZED_STRIP_WIDTH_PX,
    'the world is the window minus the strip',
  );
  assert.equal(minimized.worldPx, 276);
  assert.equal(
    minimized.worldControls,
    true,
    'the existing world navigation is offered again at 320 after Minimize',
  );
  assert.equal(
    minimized.fixtureWords,
    'floating',
    'and the fixture words float in the world again',
  );
  assert.equal(
    minimized.trayRightPx,
    0,
    'the strip sits at the window edge once the panel is gone',
  );
});

test('REQUIREMENT: at EVERY width from 320 up, minimizing the office offers the world controls (with the strip it always has, and without)', () => {
  for (let width = NARROWEST_PX; width <= 2000; width += 1) {
    for (const strip of [true, false]) {
      const d = diDockFor(width, true, strip);
      assert.equal(
        d.worldControls,
        true,
        `${width} wide, office minimized${strip ? ' with the strip' : ''}: world ${d.worldPx}px must offer the controls`,
      );
    }
  }
});

test('STRUCTURAL GUARD: the controls threshold can never exceed the world that exists at 320 after Minimize (a larger one makes the requirement unsatisfiable by construction)', () => {
  assert.ok(
    DI_WORLD_CONTROLS_MIN_PX <= NARROWEST_PX - DI_MINIMIZED_STRIP_WIDTH_PX,
    `a threshold of ${DI_WORLD_CONTROLS_MIN_PX} px exceeds the ${NARROWEST_PX - DI_MINIMIZED_STRIP_WIDTH_PX} px world at ${NARROWEST_PX} after Minimize: no state transition could restore the controls there`,
  );
  assert.ok(DI_WORLD_CONTROLS_MIN_PX > 0);
  assert.ok(
    DI_WORLD_ROOMY_PX >= DI_WORLD_CONTROLS_MIN_PX,
    'the roomy width is never below the controls width',
  );
});

test('boundaries: the panel is 420 exactly where the window holds it with the strip, and every threshold flips at exactly one pixel (stated through the constants)', () => {
  assert.equal(diDockFor(464, false, true).panelPx, 420);
  assert.equal(diDockFor(463, false, true).panelPx, 419);
  assert.equal(diDockFor(464, false, true).worldPx, 0);
  assert.equal(diDockFor(465, false, true).worldPx, 1);
  assert.equal(diDockFor(420, false, false).worldPx, 0);
  assert.equal(
    diDockFor(DI_PANEL_WIDTH_PX + DI_WORLD_CONTROLS_MIN_PX, false, false).worldControls,
    true,
    'at the controls width the controls are offered',
  );
  assert.equal(
    diDockFor(DI_PANEL_WIDTH_PX + DI_WORLD_CONTROLS_MIN_PX - 1, false, false).worldControls,
    false,
    'one pixel less: removed',
  );
  assert.equal(diDockFor(DI_PANEL_WIDTH_PX + DI_WORLD_ROOMY_PX, false, false).worldNarrow, false);
  assert.equal(
    diDockFor(DI_PANEL_WIDTH_PX + DI_WORLD_ROOMY_PX - 1, false, false).worldNarrow,
    true,
  );
  // the fixture label lives in the world's bottom-left column with the toolbar, so it is shown there exactly where the controls are offered (or the office is minimized)
  assert.equal(
    diDockFor(DI_PANEL_WIDTH_PX + DI_WORLD_CONTROLS_MIN_PX, false, false).fixtureWords,
    'floating',
    'the world has room for the controls and the label beside them',
  );
  assert.equal(
    diDockFor(DI_PANEL_WIDTH_PX + DI_WORLD_CONTROLS_MIN_PX - 1, false, false).fixtureWords,
    'in-panel',
    'one pixel less: the words sit in the panel instead',
  );
});

test('THE LEFT STRIP derives from the one measured figure (the toolbar, 10 to 276 px), and never gives the camera controls less than one stop', () => {
  // the column starts 10 px in and may be as wide as the toolbar's right edge allows, so the strip is exactly the toolbar's extent wide: nothing in it reaches further
  assert.equal(DI_WORLD_COLUMN_LEFT_PX + DI_WORLD_COLUMN_MAX_PX, DI_WORLD_CONTROLS_MIN_PX);
  // the first row's floor: the controls' own offset inside the strip plus one stop (96 + 36), so one stop is always shown and the controls are never removed for want of height
  assert.equal(WORLD_VIEW_CONTROLS_TOP_PX - WORLD_RAIL_TOP_PX + WORLD_VIEW_CONTROL_MIN_PX, 132);
  assert.ok(WORLD_VIEW_CONTROL_MIN_PX > 0 && WORLD_VIEW_CONTROLS_TOP_PX > WORLD_RAIL_TOP_PX);
});

test('a window narrower than the strip is non-negative everywhere', () => {
  const d = diDockFor(10, false, true);
  assert.ok(d.panelPx >= 0 && d.worldPx >= 0 && d.insetPx >= 0 && d.trayRightPx >= 0);
  assert.equal(diDockFor(0, false, false).worldPx, 0);
});

test('fixture words are never both, never neither: exactly one of floating and in-panel, for every width, strip and collapse state', () => {
  for (let w = 0; w <= 1600; w += 7)
    for (const collapsed of [false, true])
      for (const strip of [false, true]) {
        const d = diDockFor(w, collapsed, strip);
        assert.ok(d.fixtureWords === 'floating' || d.fixtureWords === 'in-panel');
        if (collapsed)
          assert.equal(
            d.fixtureWords,
            'floating',
            'a minimized office has nothing to host them: they float',
          );
      }
});
