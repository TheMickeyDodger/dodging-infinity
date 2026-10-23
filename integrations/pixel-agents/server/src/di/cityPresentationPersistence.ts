/**
 * DI office: persistence of the viewer's CITY PRESENTATION PREFERENCES (living city 6–9, item 18) under the
 * injected DI state root: lighting and traffic, and the season and regional-weather state that lets a reload
 * continue where the city was (contract v2). Bounded both ways with the shared contract
 * (core/src/cityPresentation.ts):
 * - a save is checked against the strict v2 contract BEFORE anything is written, and is written in the
 *   contract's COMPACT STORED form (small integer codes, packed per-region arrays) so the file stays inside
 *   its 256-byte budget with every field kept; the write is atomic (temp, then rename) and guarded by the
 *   root's write containment;
 * - a read is bounded by its BYTE SIZE before it is read or parsed, then goes through the shared reader:
 *   a valid stored v2 record is unpacked to the named record the wire carries, a valid v1 record (lighting,
 *   weather, traffic) is migrated to v2 by THE shared migration
 *   (its weather becomes the same MANUAL choice, the seasons start Automatic from summer), and anything
 *   else is ignored WHOLE with a logged reason. A migration is a read: it writes nothing, and the old file
 *   stays as it was until the next save. No other store is touched either way.
 * Nothing here is Mission-, agent- or canonical-shaped, and no value comes from a location, a weather
 * service or the backend: it is the viewer's own decoration choice.
 */
import * as fs from 'fs';
import * as path from 'path';

import {
  CITY_PRESENTATION_V2_MAX_BYTES,
  encodeStoredCityPresentationV2,
  readStoredCityPresentation,
} from '../../../core/src/cityPresentation.js';
import { CITY_PRESENTATION_FILE_NAME } from '../constants.js';
import { getStateRoot, guardStateWrite, writeStateTemp } from '../stateRoot.js';

function filePath(): string {
  return path.join(getStateRoot(), CITY_PRESENTATION_FILE_NAME);
}

/**
 * The saved preferences as a v2 record (a v1 file is migrated, see the header), or null when there are none or they fail their bounds
 * (the reason is logged and nothing else is touched).
 */
export function readCityPresentation(): Record<string, unknown> | null {
  const file = filePath();
  try {
    const st = fs.statSync(file);
    if (!st.isFile() || st.size > CITY_PRESENTATION_V2_MAX_BYTES) {
      console.error(
        `[Pixel Agents] Ignoring city presentation: ${st.isFile() ? `${st.size} bytes over ${CITY_PRESENTATION_V2_MAX_BYTES}` : 'not a file'}`,
      );
      return null;
    }
    const parsed: unknown = JSON.parse(fs.readFileSync(file, 'utf-8'));
    const migration = readStoredCityPresentation(parsed);
    if (!migration.ok) {
      console.error(`[Pixel Agents] Ignoring city presentation: ${migration.reason}`);
      return null;
    }
    if (migration.from === 1)
      console.log('[Pixel Agents] City presentation migrated from v1 to v2 (nothing written)');
    return migration.state as unknown as Record<string, unknown>;
  } catch (err) {
    if ((err as NodeJS.ErrnoException).code !== 'ENOENT')
      console.error('[Pixel Agents] City presentation unreadable:', err);
    return null;
  }
}

/** Write the preferences atomically. Returns null when written, else the reason it was refused (only a strict v2 record is written). */
export function writeCityPresentation(state: unknown): string | null {
  const encoded = encodeStoredCityPresentationV2(state);
  if (!encoded.ok) return encoded.reason;
  const file = filePath();
  try {
    guardStateWrite(file);
    fs.mkdirSync(path.dirname(file), { recursive: true });
    const tmp = file + '.tmp';
    writeStateTemp(tmp, encoded.text);
    guardStateWrite(file);
    fs.renameSync(tmp, file);
    return null;
  } catch (err) {
    console.error('[Pixel Agents] Failed to write city presentation:', err);
    return err instanceof Error ? err.message : String(err);
  }
}
