import type { AmbientSaveState } from '../../../../core/src/ambientSave.js';
import { AMBIENT_EXPORT_FILENAME_STEM, LAYOUT_EXPORT_FILENAME, LAYOUT_EXPORT_MIME } from '../../constants.js';
import type { OfficeLayout } from '../types.js';

/**
 * Browser-native layout export (standalone mode, where there is no host save
 * dialog). Serializes a snapshot of the layout and triggers a file download.
 *
 * `getLayout()` returns a live reference, so the caller must pass the layout to
 * snapshot at click time; `JSON.stringify` here captures it immediately, so any
 * later edits cannot mutate the exported blob.
 */
export function exportLayoutToFile(layout: OfficeLayout): void {
  downloadJson(JSON.stringify(layout, null, 2), LAYOUT_EXPORT_FILENAME);
}

/** DI office: the decorative ambient snapshot as its OWN single download, named by the residents it
 *  holds aboard a train, so a native download shows whether it carries an active journey. */
export function exportAmbientToFile(snapshot: AmbientSaveState): void {
  downloadJson(JSON.stringify(snapshot), ambientExportFilename(snapshot));
}

export function ambientExportFilename(snapshot: AmbientSaveState): string {
  const aboard = snapshot.residents.filter((r) => r[4] === 3).length;
  return `${AMBIENT_EXPORT_FILENAME_STEM}-${aboard}-aboard.json`;
}

function downloadJson(json: string, filename: string): void {
  const blob = new Blob([json], { type: LAYOUT_EXPORT_MIME });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement('a');
  anchor.href = url;
  anchor.download = filename;
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  // Defer revocation so the click-driven download has started first.
  setTimeout(() => URL.revokeObjectURL(url), 0);
}
