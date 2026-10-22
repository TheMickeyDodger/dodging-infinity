/**
 * DI office: persistence of the office's SEAT PREFERENCES — where each real character (by canonical identity, never by the process-local number) prefers to
 * sit — under the injected DI state root. Bounded both ways with the shared contract (core/src/diSeatPreferences.ts):
 * - a RECEIVED save goes through the cheap, bounded EXTERNAL INTAKE first: too many entries, any field outside its bound, a repeated identity or an estimated
 *   size over the intake bound refuses the WHOLE save with a closed, non-echoing reason and writes NOTHING (the saved preferences stay exactly as they were);
 *   an accepted save over the byte budget has its OLDEST entries evicted until it fits (logged); the write is atomic (temp, then rename) and guarded by the
 *   root's write containment;
 * - a read is bounded by its BYTE SIZE before it is read or parsed (a larger file is ignored), then sanitised: an entry that breaks its own bounds is refused
 *   for that entry only (logged), and the oldest are evicted if the file held more than the budget.
 * It is local seating preference: it holds no Mission state, no position and nothing canonical.
 */
import * as fs from 'fs';
import * as path from 'path';

import {
  DI_SEAT_PREFERENCES_MAX_BYTES,
  DI_SEAT_PREFERENCES_VERSION,
  intakeDiSeatPreferences,
  sanitizeDiSeatPreferences,
} from '../../../core/src/diSeatPreferences.js';
import { DI_SEAT_PREFERENCES_FILE_NAME } from '../constants.js';
import { getStateRoot, guardStateWrite, writeStateTemp } from '../stateRoot.js';

function filePath(): string {
  return path.join(getStateRoot(), DI_SEAT_PREFERENCES_FILE_NAME);
}

/** The saved preferences, or null when there are none or the file fails its bounds (reason logged). */
export function readDiSeatPreferences(): Record<string, unknown> | null {
  const file = filePath();
  try {
    const st = fs.statSync(file);
    if (!st.isFile() || st.size > DI_SEAT_PREFERENCES_MAX_BYTES) {
      console.error(
        `[Pixel Agents] Ignoring seat preferences: ${st.size} bytes over ${DI_SEAT_PREFERENCES_MAX_BYTES}`,
      );
      return null;
    }
    const parsed: unknown = JSON.parse(fs.readFileSync(file, 'utf-8'));
    const clean = sanitizeDiSeatPreferences(parsed);
    if (clean === null) {
      console.error('[Pixel Agents] Ignoring seat preferences: not a record of the contract');
      return null;
    }
    for (const why of clean.refused) console.error(`[Pixel Agents] Seat preference refused (that entry only): ${why}`);
    if (clean.evicted > 0)
      console.error(`[Pixel Agents] Seat preferences over budget: the ${clean.evicted} oldest entries are ignored`);
    return clean.state as unknown as Record<string, unknown>;
  } catch (err) {
    if ((err as NodeJS.ErrnoException).code !== 'ENOENT')
      console.error('[Pixel Agents] Seat preferences unreadable:', err);
    return null;
  }
}

/** Write a received save atomically. Returns null when written, else the closed reason it was refused (and then NOTHING was written). */
export function writeDiSeatPreferences(state: unknown): string | null {
  const intake = intakeDiSeatPreferences(state);
  if (!intake.ok) {
    console.error(`[Pixel Agents] Seat preferences refused whole: ${intake.reason}`);
    return intake.reason;
  }
  if (intake.evicted > 0)
    console.error(`[Pixel Agents] Seat preferences over budget: the ${intake.evicted} oldest entries were evicted`);
  const file = filePath();
  try {
    guardStateWrite(file);
    fs.mkdirSync(path.dirname(file), { recursive: true });
    const tmp = file + '.tmp';
    writeStateTemp(tmp, JSON.stringify({ v: DI_SEAT_PREFERENCES_VERSION, entries: intake.entries }));
    guardStateWrite(file);
    fs.renameSync(tmp, file);
    return null;
  } catch (err) {
    console.error('[Pixel Agents] Failed to write seat preferences:', err);
    return err instanceof Error ? err.message : String(err);
  }
}
