/**
 * LAYOUT INTAKE (DOM-free, unit-tested): the webview's one gate for a layout arriving from the
 * server (layoutLoaded), a saved string (deserializeLayout) or a user's file (Import Layout).
 * Same bounds as the server — the ONE definition in core/src/layoutLimits.ts — in the order
 * legacy support needs: bytes first (before reading), then the raw layout in LEGACY mode, then
 * migration, then the migrated layout in STRICT mode. Nothing is built from a refused layout.
 */
import {
  admitLayout,
  checkLayout,
  checkLayoutTextBytes,
  LAYOUT_MAX_FILE_BYTES,
  LAYOUT_MAX_FRAME_BYTES,
} from '../../../../core/src/layoutLimits.js';
import type { OfficeLayout } from '../types.js';

export type LayoutIntake = { layout: OfficeLayout } | { refused: string };

/** A raw layout object: the shared admission (legacy-mode bounds on the raw form, then the
 *  migrated form the office will hold), returning that migrated form. */
export function acceptLayout(raw: unknown): LayoutIntake {
  const admitted = admitLayout(raw, { legacy: true });
  return 'refused' in admitted ? admitted : { layout: admitted.layout as unknown as OfficeLayout };
}

/** Checked against File.size BEFORE the file is read. */
export function importFileTooLarge(sizeBytes: number): boolean {
  return sizeBytes > LAYOUT_MAX_FILE_BYTES;
}

/** Bytes of the compact saveLayout frame this layout would be sent as. */
export function saveFrameBytes(layout: OfficeLayout): number {
  return new TextEncoder().encode(JSON.stringify({ type: 'saveLayout', layout })).length;
}

/** An imported file's text: refused by its byte size BEFORE parsing (the browser Import also checks
 *  File.size before reading), then parse, accept, and its save must fit the /ws frame ceiling. */
export function prepareImport(text: string): LayoutIntake {
  const tooLarge = checkLayoutTextBytes(text);
  if (tooLarge !== null) return { refused: tooLarge };
  let parsed: unknown;
  try {
    parsed = JSON.parse(text);
  } catch {
    return { refused: 'not valid JSON' };
  }
  const result = acceptLayout(parsed);
  if ('refused' in result) return result;
  const bytes = saveFrameBytes(result.layout);
  if (bytes > LAYOUT_MAX_FRAME_BYTES)
    return { refused: `save frame ${bytes} bytes exceeds ${LAYOUT_MAX_FRAME_BYTES}` };
  return result;
}

/**
 * An editor save (debounced autosave or explicit Save): the layout must pass the SAME strict check
 * the server applies — structure and the UTF-8 frame/file byte budgets — BEFORE it is sent, so a
 * save is never sent that the transport would drop (1009) or the server would refuse.
 */
export function prepareSave(layout: OfficeLayout): LayoutIntake {
  const refused = checkLayout(layout);
  return refused === null ? { layout } : { refused };
}

export interface ImportEffects {
  rebuild: (layout: OfficeLayout) => void;
  setLastSaved: (layout: OfficeLayout) => void;
  send: (layout: OfficeLayout) => void;
  markClean: () => void;
  refuse: (reason: string) => void;
}

/**
 * Apply an imported file's text. A refused import changes NOTHING: it is not rendered, not
 * sent, not recorded as saved and the editor is not marked clean — so the office can never show
 * a layout the server would refuse while reporting it saved. (The client applies exactly the
 * server's rules first; a DI save the server still refuses re-sends the persisted layout.)
 * Returns whether the import was applied.
 */
export function applyImport(text: string, effects: ImportEffects): boolean {
  const result = prepareImport(text);
  if ('refused' in result) {
    effects.refuse(result.refused);
    return false;
  }
  effects.rebuild(result.layout);
  effects.setLastSaved(result.layout);
  effects.send(result.layout);
  effects.markClean();
  return true;
}
