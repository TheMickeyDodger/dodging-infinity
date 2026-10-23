/**
 * WHEN THE CITY IS SAVED (item 18, contract v2). A pure, DOM-free gate: given the time, the city's persist revision and the running time its
 * saved clocks have ACTUALLY ADVANCED, it says whether a save is due. The webview polls it once per CITY_SAVE_POLL_MS (never per frame).
 *
 *  - A viewer's own CHOICE is saved at once by the caller, not by this gate.
 *  - A TRANSITION (a season change, an automatic weather draw: the persist revision moved) is saved at the next poll, but never sooner than
 *    CITY_SAVE_MIN_GAP_MS after the last save, so a burst of transitions is one save.
 *  - A RUNNING clock is checkpointed at a bounded rate, CITY_SAVE_CHECKPOINT_MS, and only if it ADVANCED since the last save: its time left
 *    moves only inside game-loop steps (CityEffects.runningAdvanceMs), so a hidden view, or a loop that is not stepping, has nothing new to
 *    save however long the wall clock runs.
 *  - A HIDDEN view is saved once more (when anything changed or a clock advanced since the last save), under the same minimum gap.
 * A city that has not changed and has not advanced is never saved again. The gate keeps no state about the record itself.
 */
import {
  CITY_SAVE_CHECKPOINT_MS,
  CITY_SAVE_MIN_GAP_MS,
  CITY_SAVE_POLL_MS,
} from '../../constants.js';

export class CitySaveGate {
  private lastAtMs: number;
  private lastRevision: number;
  private lastAdvanceMs: number;

  /** The arguments are the time, persist revision and running advance of the state the city was loaded with (or started with): nothing is due yet. */
  constructor(nowMs: number, revision: number, advanceMs = 0) {
    this.lastAtMs = nowMs;
    this.lastRevision = revision;
    this.lastAdvanceMs = advanceMs;
  }

  /** Record that a save was sent now. */
  sent(nowMs: number, revision: number, advanceMs = 0): void {
    this.lastAtMs = nowMs;
    this.lastRevision = revision;
    this.lastAdvanceMs = advanceMs;
  }

  /** Whether the poll should send a save now. */
  due(nowMs: number, revision: number, advanceMs: number): boolean {
    const gap = nowMs - this.lastAtMs;
    if (revision !== this.lastRevision && gap >= CITY_SAVE_MIN_GAP_MS) return true;
    return advanceMs > this.lastAdvanceMs && gap >= CITY_SAVE_CHECKPOINT_MS;
  }

  /** Whether hiding the view should send a save now. */
  dueOnHide(nowMs: number, revision: number, advanceMs: number): boolean {
    if (nowMs - this.lastAtMs < CITY_SAVE_MIN_GAP_MS) return false;
    return revision !== this.lastRevision || advanceMs > this.lastAdvanceMs;
  }
}

/** What the saver reads from the city (CityEffects provides both). */
export interface CitySaveSource {
  readonly persistRevision: number;
  readonly runningAdvanceMs: number;
}

/**
 * The saver's decisions, in one place: it owns the gate, `save` sends the whole city (through `send`) and notes it is what is saved, and the
 * poll and the hiding of the view call `save` only when the gate says it is due. The hook wires timers and events to it and nothing else, so
 * a test drives the very same decisions with an injected clock.
 */
export class CitySaver {
  private gate: CitySaveGate | null = null;
  private readonly send: () => void;

  constructor(send: () => void) {
    this.send = send;
  }

  /** The state in `source` is what is saved now (after a load). */
  loaded(nowMs: number, source: CitySaveSource): void {
    this.gate = new CitySaveGate(nowMs, source.persistRevision, source.runningAdvanceMs);
  }

  /** Send the whole city now (a viewer's choice, or a due poll) and note that it is what is saved. */
  save(nowMs: number, source: CitySaveSource): void {
    this.send();
    this.loaded(nowMs, source);
  }

  /** One poll (once per CITY_SAVE_POLL_MS). Returns whether it sent a save. */
  poll(nowMs: number, source: CitySaveSource): boolean {
    this.gate ??= new CitySaveGate(nowMs, source.persistRevision, source.runningAdvanceMs);
    if (!this.gate.due(nowMs, source.persistRevision, source.runningAdvanceMs)) return false;
    this.save(nowMs, source);
    return true;
  }

  /** The view went away (hidden, or the page is leaving). Returns whether it sent a save. */
  onHide(nowMs: number, source: CitySaveSource): boolean {
    this.gate ??= new CitySaveGate(nowMs, source.persistRevision, source.runningAdvanceMs);
    if (!this.gate.dueOnHide(nowMs, source.persistRevision, source.runningAdvanceMs)) return false;
    this.save(nowMs, source);
    return true;
  }
}

/** The two event targets the saver listens on (the page's `document` and `window` satisfy this; a test passes plain event targets). */
export interface CitySaveEnvironment {
  doc: {
    readonly visibilityState: string;
    addEventListener(type: 'visibilitychange', listener: () => void): void;
    removeEventListener(type: 'visibilitychange', listener: () => void): void;
  };
  win: {
    addEventListener(type: 'pagehide', listener: () => void): void;
    removeEventListener(type: 'pagehide', listener: () => void): void;
  };
}

/**
 * THE SAVER'S WIRING, the only timers and listeners it has: one poll per CITY_SAVE_POLL_MS (never per frame), and the view going hidden or
 * the page being left. Nothing is saved until `ready` says the saved record has been loaded (so a slow handshake can never overwrite it with
 * the defaults). Returns the function that stops it. The hook calls exactly this; a test calls it under fake timers.
 */
export function startCitySaving(
  saver: CitySaver,
  source: () => CitySaveSource,
  ready: () => boolean,
  env: CitySaveEnvironment,
): () => void {
  const id = setInterval(() => {
    if (ready()) saver.poll(Date.now(), source());
  }, CITY_SAVE_POLL_MS);
  const leave = () => {
    if (ready()) saver.onHide(Date.now(), source());
  };
  const onVisibility = () => {
    if (env.doc.visibilityState === 'hidden') leave();
  };
  env.doc.addEventListener('visibilitychange', onVisibility);
  env.win.addEventListener('pagehide', leave);
  return () => {
    clearInterval(id);
    env.doc.removeEventListener('visibilitychange', onVisibility);
    env.win.removeEventListener('pagehide', leave);
  };
}
