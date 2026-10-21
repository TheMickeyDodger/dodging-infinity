/**
 * DI office: persistence of the viewer's CITY PRESENTATION PREFERENCES (living city 6–9) under the
 * injected DI state root: lighting, weather and traffic choices, nothing else. Bounded both ways with
 * the shared contract (core/src/cityPresentation.ts):
 * - a save is checked BEFORE anything is written; the write is atomic (temp, then rename) and guarded
 *   by the root's write containment;
 * - a read is bounded by its BYTE SIZE before it is read or parsed, and checked after parsing.
 * Nothing here is Mission-, agent- or canonical-shaped, and no value comes from a location, a weather
 * service or the backend: it is the viewer's own decoration choice.
 */
import * as fs from 'fs';
import * as path from 'path';

import {
  checkCityPresentation,
  CITY_PRESENTATION_MAX_BYTES,
} from '../../../core/src/cityPresentation.js';
import { CITY_PRESENTATION_FILE_NAME } from '../constants.js';
import { getStateRoot, guardStateWrite, writeStateTemp } from '../stateRoot.js';

function filePath(): string {
  return path.join(getStateRoot(), CITY_PRESENTATION_FILE_NAME);
}

/** The saved preferences, or null when there are none or they fail their bounds (reason logged). */
export function readCityPresentation(): Record<string, unknown> | null {
  const file = filePath();
  try {
    const st = fs.statSync(file);
    if (!st.isFile() || st.size > CITY_PRESENTATION_MAX_BYTES) {
      console.error(
        `[Pixel Agents] Ignoring city presentation: ${st.size} bytes over ${CITY_PRESENTATION_MAX_BYTES}`,
      );
      return null;
    }
    const parsed: unknown = JSON.parse(fs.readFileSync(file, 'utf-8'));
    const refused = checkCityPresentation(parsed);
    if (refused !== null) {
      console.error(`[Pixel Agents] Ignoring city presentation: ${refused}`);
      return null;
    }
    return parsed as Record<string, unknown>;
  } catch (err) {
    if ((err as NodeJS.ErrnoException).code !== 'ENOENT')
      console.error('[Pixel Agents] City presentation unreadable:', err);
    return null;
  }
}

/** Write the preferences atomically. Returns null when written, else the reason it was refused. */
export function writeCityPresentation(state: unknown): string | null {
  const refused = checkCityPresentation(state);
  if (refused !== null) return refused;
  const file = filePath();
  try {
    guardStateWrite(file);
    fs.mkdirSync(path.dirname(file), { recursive: true });
    const tmp = file + '.tmp';
    writeStateTemp(tmp, JSON.stringify(state));
    guardStateWrite(file);
    fs.renameSync(tmp, file);
    return null;
  } catch (err) {
    console.error('[Pixel Agents] Failed to write city presentation:', err);
    return err instanceof Error ? err.message : String(err);
  }
}
