/**
 * DI office: persistence of the DECORATIVE ambient snapshot (M3 S4) under the injected DI state root.
 * Bounded both ways with the shared contract (core/src/ambientSave.ts):
 * - a save is checked BEFORE anything is written; the write is atomic (temp, then rename) and guarded
 *   by the root's write containment;
 * - a read is bounded by its BYTE SIZE before it is read or parsed, and checked after parsing.
 * Nothing here is Mission-, agent- or canonical-shaped; the file holds positions of decorative
 * residents and trains only.
 */
import * as fs from 'fs';
import * as path from 'path';

import { AMBIENT_SAVE_MAX_BYTES, checkAmbientSave } from '../../../core/src/ambientSave.js';
import { AMBIENT_STATE_FILE_NAME } from '../constants.js';
import { getStateRoot, guardStateWrite, writeStateTemp } from '../stateRoot.js';

function filePath(): string {
  return path.join(getStateRoot(), AMBIENT_STATE_FILE_NAME);
}

/** The saved snapshot, or null when there is none or it fails its bounds (reason logged). */
export function readAmbientState(): Record<string, unknown> | null {
  const file = filePath();
  try {
    const st = fs.statSync(file);
    if (!st.isFile() || st.size > AMBIENT_SAVE_MAX_BYTES) {
      console.error(`[Pixel Agents] Ignoring ambient snapshot: ${st.size} bytes over ${AMBIENT_SAVE_MAX_BYTES}`);
      return null;
    }
    const parsed: unknown = JSON.parse(fs.readFileSync(file, 'utf-8'));
    const refused = checkAmbientSave(parsed);
    if (refused !== null) {
      console.error(`[Pixel Agents] Ignoring ambient snapshot: ${refused}`);
      return null;
    }
    return parsed as Record<string, unknown>;
  } catch (err) {
    if ((err as NodeJS.ErrnoException).code !== 'ENOENT') console.error('[Pixel Agents] Ambient snapshot unreadable:', err);
    return null;
  }
}

/** Write the snapshot atomically. Returns null when written, else the reason it was refused. */
export function writeAmbientState(state: unknown): string | null {
  const refused = checkAmbientSave(state);
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
    console.error('[Pixel Agents] Failed to write ambient snapshot:', err);
    return err instanceof Error ? err.message : String(err);
  }
}
