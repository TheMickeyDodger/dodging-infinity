/**
 * The docked layout at the window's right edge: the DI office panel, the strip of restore controls beside it, and what is left for the world.
 * One place states the arithmetic, so the real App and the test harness cannot disagree.
 *
 * RESPONSIVE WIDTH (WCAG 2.2 SC 1.4.10 Reflow): the panel is its design width (420 px) wherever the window can hold it, and otherwise
 * exactly as wide as the window leaves it, so its reading and control content is never pushed off the window's edge. The strip's width
 * counts only while the strip holds a restore control, and it sits at the panel's left edge, so the panel and the strip are BOTH
 * inside the window and neither covers the other: with a strip, the panel never takes more than the window minus the strip.
 *
 * RECORDED, DELIBERATE BEHAVIOUR: where the window cannot hold the panel, the strip and a useful world (at 320 px with the office
 * expanded and a strip present: 276 + 44), THE WORLD YIELDS ENTIRELY (its width is 0). The world is content that needs two-dimensional
 * layout, so it is exempt from reflow. After the office is minimized the world is the window minus the strip (276 px at 320, because the
 * office's own restore control lives in the strip) and is drawn again; the office's Minimize control is inside the panel, inside the
 * window. Restore controls are inside the window with the office expanded as well as minimized, because the strip is never pushed off it.
 * These are claims about rendered behaviour and are PROVED there (diWorldYield.test.ts, diMissionRequest.test.ts), not by this comment.
 *
 * THE WORLD'S CONTROLS follow the same rule and are part of the same composition: they are offered only while the world is at least
 * DI_WORLD_CONTROLS_MIN_PX wide, a value chosen so that they ARE offered after Minimize at 320 (a larger one would make that unsatisfiable by
 * construction: guarded structurally in diPanelLayout.test.ts, and proved rendered, with the real keyboard, in diWorldYield.test.ts). Where
 * the world is narrower they are REMOVED (inert and hidden, WorldControlsGate), never merely clipped: a clipped control is still a tab stop,
 * and a keyboard user would tab into nothing. The world CANVAS carries the standard's two-dimensional exception; the buttons do not, which is
 * why they are removed rather than exempted.
 *
 * Presentation only: it reads a width and returns widths; it reads, writes and sends nothing.
 */
import {
  DI_MINIMIZED_STRIP_WIDTH_PX,
  DI_PANEL_WIDTH_PX,
  DI_WORLD_CONTROLS_MIN_PX,
  DI_WORLD_ROOMY_PX,
} from './constants.js';

export interface DiDock {
  /** The office panel's width while it is open (it is also what it would be if minimized: only `insetPx` and `trayRightPx` change). */
  panelPx: number;
  /** The strip's width: 0 while it holds no restore control. */
  stripPx: number;
  /** Where the strip's right edge sits: the window's right edge while the panel is minimized, else the panel's left edge. */
  trayRightPx: number;
  /** The whole width taken at the window's right, which the world yields (never below 0). */
  insetPx: number;
  /** The width left for the world (never below 0; 0 is the recorded "world yields entirely" case). */
  worldPx: number;
  /** Whether the world can hold its controls whole (they are removed, not clipped, where it cannot). */
  worldControls: boolean;
  /** Whether the world is too narrow for the corner furniture to keep its usual places (the version label moves off the toolbar). */
  worldNarrow: boolean;
  /**
   * Where the fixture words are shown for an explicit fixture preview: FLOATING in the world, or INSIDE the panel where the world has no
   * room for the floating banner while the panel is open. Never both, never neither: labelling stays visible, and nothing floats over the panel.
   */
  fixtureWords: 'floating' | 'in-panel';
}

/** The dock for a window `viewportPx` wide. */
export function diDockFor(
  viewportPx: number,
  panelCollapsed: boolean,
  stripShown: boolean,
): DiDock {
  const stripPx = stripShown ? DI_MINIMIZED_STRIP_WIDTH_PX : 0;
  const panelPx = Math.max(0, Math.min(DI_PANEL_WIDTH_PX, viewportPx - stripPx));
  const openPx = panelCollapsed ? 0 : panelPx;
  const insetPx = openPx + stripPx;
  const worldPx = Math.max(0, viewportPx - insetPx);
  const worldControls = worldPx >= DI_WORLD_CONTROLS_MIN_PX;
  return {
    panelPx,
    stripPx,
    trayRightPx: openPx,
    insetPx,
    worldPx,
    worldControls,
    worldNarrow: worldPx < DI_WORLD_ROOMY_PX,
    fixtureWords: worldControls || panelCollapsed ? 'floating' : 'in-panel',
  };
}
