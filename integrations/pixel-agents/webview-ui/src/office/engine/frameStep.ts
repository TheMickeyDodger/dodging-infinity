/**
 * The game loop's per-frame decision, DOM-free so it is testable as is (gameLoop.ts calls exactly
 * this): whether a frame may advance the simulation and by how much.
 *
 * HIDDEN GUARD: while the document is hidden, NO frame advances anything, whatever the platform does
 * with animation frames (a throttled tab, a headless or capture context can still deliver them). The
 * first frame after the view returns starts a fresh delta (0), so no hidden time leaks in through the
 * loop; the bounded resume catch-up (OfficeState.catchUpAmbient) is the only path that covers it.
 *
 * TICK-RATE BOUND (a credit counter): visible frame time earns tick credit at one tick per
 * `minInterval` seconds, held at most `burst`; a frame ticks only by spending one whole credit, and a
 * frame that cannot neither advances nor redraws (nothing changed, so there is nothing new to draw).
 * So in ANY window of T seconds there are at most T / minInterval + burst ticks, whatever the display's
 * refresh rate or how often visibility flips, and every per-tick budget (path searches, updates)
 * becomes a per-second bound. Below that rate every frame ticks, early-stamped frames included (the
 * held credit absorbs timestamp jitter). A skipped frame's time is carried into the next tick's delta
 * (capped at `maxDt`), never lost.
 */
export interface FrameClock {
  /** Timestamp of the last frame that advanced (0 = none yet, or reset by hiding). */
  lastTime: number;
  /** Timestamp of the last visible frame (credit is earned from here); absent = none yet. */
  lastSeen?: number;
  /** Tick credit held, 0..burst. */
  credit?: number;
}

/** Whether the document is hidden (no document = never hidden). */
export function documentHidden(): boolean {
  const doc = (globalThis as { document?: { visibilityState?: string } }).document;
  return doc?.visibilityState === 'hidden';
}

/** The seconds this frame advances, or null when it must neither advance nor redraw (hidden, or no
 *  whole tick credit). `minInterval` 0 = no rate bound. */
export function nextFrameDelta(clock: FrameClock, time: number, hidden: boolean, maxDt: number, minInterval = 0, burst = 2): number | null {
  if (hidden) {
    clock.lastTime = 0;
    return null;
  }
  if (minInterval > 0) {
    // the very first frame holds one credit; afterwards credit is earned by elapsed time only
    const earned = clock.lastSeen === undefined ? 1 : (time - clock.lastSeen) / 1000 / minInterval;
    const credit = Math.min(burst, (clock.credit ?? 0) + Math.max(0, earned));
    clock.lastSeen = time;
    if (credit < 1) {
      clock.credit = credit;
      return null;
    }
    clock.credit = credit - 1;
  }
  const dt = clock.lastTime === 0 ? 0 : Math.min((time - clock.lastTime) / 1000, maxDt);
  clock.lastTime = time;
  return dt;
}
