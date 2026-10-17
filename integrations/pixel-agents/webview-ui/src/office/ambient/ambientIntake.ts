/**
 * AMBIENT SNAPSHOT TEXT INTAKE (M3 S4): the ONE bounded entry for externally supplied snapshot text
 * (used by the paste import; any other text route must use it too). The UTF-8 byte length is checked against
 * the snapshot bound BEFORE anything is parsed; then parse; the parsed value then goes to the one
 * snapshot validator and apply path (OfficeState.importAmbientSnapshot -> applyAmbientSnapshot).
 */
import { AMBIENT_SAVE_MAX_BYTES } from '../../../../core/src/ambientSave.js';

export type AmbientTextIntake = { snapshot: unknown } | { refused: string };

export function utf8Bytes(text: string): number {
  return new TextEncoder().encode(text).length;
}

export function prepareAmbientText(text: string): AmbientTextIntake {
  const bytes = utf8Bytes(text);
  if (bytes > AMBIENT_SAVE_MAX_BYTES) return { refused: `${bytes} bytes is over the ${AMBIENT_SAVE_MAX_BYTES}-byte snapshot bound (not parsed)` };
  try {
    return { snapshot: JSON.parse(text) };
  } catch {
    return { refused: 'not valid JSON' };
  }
}
