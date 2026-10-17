import { MAX_DELTA_TIME_SEC, SIM_MIN_TICK_INTERVAL_SEC, SIM_TICK_BURST } from '../../constants.js';
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
  };
}
