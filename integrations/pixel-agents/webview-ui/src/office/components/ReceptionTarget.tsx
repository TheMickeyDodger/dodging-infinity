import { useEffect, useState } from 'react';

import {
  CHARACTER_HIT_HEIGHT,
  DI_RECEPTION_LABEL,
  DI_RECEPTION_TARGET_ESTIMATE_PX,
  DI_RECEPTION_TARGET_Z_INDEX,
} from '../../constants.js';
import type { OfficeState } from '../engine/officeState.js';
import { overlayProjection } from '../projection.js';
import { TILE_SIZE } from '../types.js';

interface ReceptionTargetProps {
  officeState: OfficeState;
  containerRef: React.RefObject<HTMLDivElement | null>;
  zoom: number;
  panRef: React.RefObject<{ x: number; y: number }>;
  /** The button itself: a click on the desk in the world opens the inbox 'from' it, so focus returns here. */
  buttonRef: React.RefObject<HTMLButtonElement | null>;
  /** Open the reception inbox from this control (focus returns to it when the inbox closes). */
  onOpen: (opener: HTMLElement) => void;
  /** Layout editing hides the target: it would sit over the furniture being edited. */
  hidden?: boolean;
}

/**
 * The in-world entrance to the reception inbox: a permanent, labelled button floating above the
 * reception desk's receptionist. It is the keyboard and assistive-technology route (Tab, Enter or
 * Space) and the visible affordance; a click on the desk or the receptionist in the world itself
 * opens the same inbox through OfficeCanvas. Both end at ONE dialog (diReceptionInboxState.ts).
 *
 * Rendered only where the loaded layout has a reception desk. Static: reduced motion changes
 * nothing about it. It reads no Mission data and sends nothing.
 */
export function ReceptionTarget({
  officeState,
  containerRef,
  zoom,
  panRef,
  buttonRef,
  onOpen,
  hidden = false,
}: ReceptionTargetProps) {
  const [, setTick] = useState(0);
  useEffect(() => {
    let rafId = 0;
    const tick = () => {
      setTick((n) => n + 1);
      rafId = requestAnimationFrame(tick);
    };
    rafId = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(rafId);
  }, []);

  const post = officeState.receptionPost;
  const ch = officeState.receptionist;
  const el = containerRef.current;
  if (hidden || !post || !el) return null;
  const project = overlayProjection(
    officeState.getLayout(),
    el.getBoundingClientRect(),
    zoom,
    panRef.current,
    window.devicePixelRatio || 1,
  );
  // Above the receptionist's head when there is one, else above the desk.
  const anchorX = ch ? ch.x : (post.deskCol + post.deskW / 2) * TILE_SIZE;
  const anchorY = ch ? ch.y - CHARACTER_HIT_HEIGHT : post.deskRow * TILE_SIZE;
  const x = project.toScreenX(anchorX);
  const y = project.toScreenY(anchorY) - 4;
  // The button is drawn centred on x and ending at y (translate(-50%, -100%)). Where the desk is outside the world's box (a large world, a narrow
  // world, a panned camera) it would be a focusable control that nobody can see, so it is NOT rendered then: the sidebar entry opens the same inbox.
  // Its own size comes from the last frame once it has been drawn (an estimate before that).
  const world = el.getBoundingClientRect();
  const drawn = buttonRef.current?.getBoundingClientRect();
  const w = drawn?.width ?? DI_RECEPTION_TARGET_ESTIMATE_PX.w;
  const h = drawn?.height ?? DI_RECEPTION_TARGET_ESTIMATE_PX.h;
  if (x - w / 2 < 0 || x + w / 2 > world.width || y - h < 0 || y > world.height) return null;
  return (
    <button
      ref={buttonRef}
      type="button"
      className="absolute cursor-pointer whitespace-nowrap border border-border bg-bg/80 px-6 py-2 text-2xs text-text-muted hover:bg-active-bg hover:text-text focus-visible:text-text focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
      style={{
        left: x,
        top: y,
        transform: 'translate(-50%, -100%)',
        zIndex: DI_RECEPTION_TARGET_Z_INDEX,
      }}
      aria-label={`${DI_RECEPTION_LABEL}: open what needs you`}
      title="View only: opens the reception inbox, the same one as the sidebar entry"
      data-testid="di-reception-target"
      onClick={(event) => onOpen(event.currentTarget)}
    >
      <span aria-hidden="true">▸ </span>
      {DI_RECEPTION_LABEL}
    </button>
  );
}
