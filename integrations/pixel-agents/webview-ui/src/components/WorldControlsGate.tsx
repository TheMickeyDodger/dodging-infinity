import type { ReactNode } from 'react';

/**
 * The world's controls, offered only while the world can hold them whole (diPanelLayout.ts, DI_WORLD_CONTROLS_MIN_PX).
 *
 * Where it cannot (a narrow window with the office open) the controls are REMOVED, not clipped. Clipping is not removal: `overflow: hidden`
 * hides a control and leaves it focusable, and so does a zero-size container or `opacity: 0`, which leaves a keyboard user tabbing into
 * nothing. Only `display: none`, `visibility: hidden`, `inert` or not rendering take a control out of the tab sequence. This uses `inert`
 * (out of the tab order, the pointer and the accessibility tree) together with `visibility: hidden` (index.css, so nothing is left drawn).
 * It renders no box of its own (`display: contents`), so its children position exactly as before. It holds and changes no state: the
 * children's owners keep theirs (the Map's open state survives a yield: App.tsx, mapShown). Whether the controls are back after the office
 * is minimized is decided by the dock arithmetic (diPanelLayout.ts) and checked in the rendered tests (diWorldYield.test.ts), not asserted here.
 *
 * Dialogs (Settings, Changelog, the request workspace, the review dialog) are deliberately NOT inside it: a dialog a person opened stays
 * operable. The world's own content (the canvas, the character labels) is not a control and is not inside it either.
 */
export function WorldControlsGate({
  available,
  narrow,
  suspended = false,
  children,
}: {
  available: boolean;
  /** The world is too narrow for the corner furniture to keep its usual places (DI_WORLD_ROOMY_PX); index.css moves the version label. */
  narrow: boolean;
  /**
   * A non-modal popover (the City panel) is open over the world: these controls stay DRAWN but are made inert, so keyboard focus cannot land on a
   * control the popover hides. Covering is not removal (z-index, clipping and overlap leave a control in the tab order); `inert` is. Not hidden:
   * the controls are still visible, and they return the moment the popover closes.
   */
  suspended?: boolean;
  children: ReactNode;
}) {
  return (
    <div
      className="di-world-controls contents"
      data-di-world-controls={!available ? 'yielded' : suspended ? 'suspended' : 'available'}
      data-di-world-width={narrow ? 'narrow' : 'roomy'}
      inert={!available || suspended}
    >
      {children}
    </div>
  );
}
