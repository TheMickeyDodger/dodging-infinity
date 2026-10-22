import { MAX_DELTA_TIME_SEC, SIM_MIN_TICK_INTERVAL_SEC, SIM_TICK_BURST } from '../../constants.js';
import { resetRenderCaches } from '../renderCaches.js';
import type { FrameClock } from './frameStep.js';
import { documentHidden, nextFrameDelta } from './frameStep.js';

/** @internal */
export interface GameLoopCallbacks {
  update: (dt: number) => void;
  render: (ctx: CanvasRenderingContext2D) => void;
}

export function startGameLoop(canvas: HTMLCanvasElement, callbacks: GameLoopCallbacks): () => void {
  const ctx = canvas.getContext('2d')!;
  ctx.imageSmoothingEnabled = false;

  // When the browser loses and restores a 2D canvas context (the trigger is not determined here) it comes back EMPTY: every bitmap
  // drawn once and kept (the sprite canvases, the carpet layer, the city's light sprites) is blank, and nothing would redraw it.
  // Forget them all through the render-cache lifecycle (renderCaches.ts) so the next draw rebuilds them: on the loss, and again on
  // the restore (what was built in between is blank too). Events run between frames, never inside one. SUPPORTED: these two events
  // on this canvas (and on the carpet layer's, renderer.ts).
  canvas.addEventListener('contextlost', resetRenderCaches);
  canvas.addEventListener('contextrestored', resetRenderCaches);

  const clock: FrameClock = { lastTime: 0 };
  let rafId = 0;
  let stopped = false;

  const frame = (time: number) => {
    if (stopped) return;
    // HIDDEN GUARD and TICK-RATE BOUND (frameStep.ts): nothing advances while hidden, and simulation
    // plus render run at most T / SIM_MIN_TICK_INTERVAL_SEC + SIM_TICK_BURST times in any T seconds,
    // whatever the display's refresh rate
    const dt = nextFrameDelta(clock, time, documentHidden(), MAX_DELTA_TIME_SEC, SIM_MIN_TICK_INTERVAL_SEC, SIM_TICK_BURST);
    if (dt === null) {
      rafId = requestAnimationFrame(frame);
      return;
    }

    callbacks.update(dt);

    ctx.imageSmoothingEnabled = false;
    callbacks.render(ctx);

    rafId = requestAnimationFrame(frame);
  };

  rafId = requestAnimationFrame(frame);

  return () => {
    stopped = true;
    cancelAnimationFrame(rafId);
    canvas.removeEventListener('contextlost', resetRenderCaches);
    canvas.removeEventListener('contextrestored', resetRenderCaches);
  };
}
