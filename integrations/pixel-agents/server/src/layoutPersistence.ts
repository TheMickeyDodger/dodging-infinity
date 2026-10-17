import * as fs from 'fs';
import * as path from 'path';

import {
  checkLayout,
  LAYOUT_MAX_FILE_BYTES,
  parseLayoutText,
} from '../../core/src/layoutLimits.js';
import {
  LAYOUT_FILE_NAME,
  LAYOUT_FILE_POLL_INTERVAL_MS,
  LAYOUT_REVISION_KEY,
} from './constants.js';
import { getStateRoot, guardStateWrite, writeStateTemp } from './stateRoot.js';

export interface LayoutWatcher {
  markOwnWrite(): void;
  dispose(): void;
}

function getLayoutFilePath(): string {
  return path.join(getStateRoot(), LAYOUT_FILE_NAME);
}

/**
 * Read + bound-check the layout file: its BYTE SIZE before it is read or parsed, then its shape
 * against the shared bounds (core/src/layoutLimits.ts, legacy mode — the webview migrates).
 * Returns the raw layout, or null with the reason logged (missing, oversized or malformed).
 */
function readBoundedLayout(filePath: string): Record<string, unknown> | null {
  const size = fs.statSync(filePath).size;
  if (size > LAYOUT_MAX_FILE_BYTES) {
    console.error(
      `[Pixel Agents] Refusing layout file: ${size} bytes exceeds ${LAYOUT_MAX_FILE_BYTES}`,
    );
    return null;
  }
  const result = parseLayoutText(fs.readFileSync(filePath, 'utf-8'));
  if ('refused' in result) {
    console.error(`[Pixel Agents] Refusing layout file: ${result.refused}`);
    return null;
  }
  return result.layout;
}

/** Is there a layout file at all? Decisions that must never overwrite a user's file key on THIS,
 *  not on readLayoutFromFile() — a present but refused (oversized / malformed) file reads null. */
export function layoutFileExists(): boolean {
  return fs.existsSync(getLayoutFilePath());
}

export function readLayoutFromFile(): Record<string, unknown> | null {
  const filePath = getLayoutFilePath();
  try {
    if (!fs.existsSync(filePath)) return null;
    return readBoundedLayout(filePath);
  } catch (err) {
    console.error('[Pixel Agents] Failed to read layout file:', err);
    return null;
  }
}

/**
 * Persist a layout — the ONE sink every path writes through (DI and standalone saves, the VS Code
 * save and import, the bundled default, the VS Code state migration). Nothing the bounded reader
 * would refuse is ever written: the shared check (core/src/layoutLimits.ts; structure, then the
 * UTF-8 frame and file byte budgets) runs first, so a refused layout leaves the existing file
 * untouched. Returns null when written, else the reason it was not (also logged).
 */
export function writeLayoutToFile(layout: Record<string, unknown>): string | null {
  const refused = checkLayout(layout, { legacy: true });
  if (refused !== null) {
    console.error(`[Pixel Agents] Refusing to write layout file: ${refused}`);
    return refused;
  }
  const filePath = getLayoutFilePath();
  const dir = path.dirname(filePath);
  try {
    guardStateWrite(filePath); // no-op unless an injected root installed a guard
    if (!fs.existsSync(dir)) {
      fs.mkdirSync(dir, { recursive: true });
    }
    const json = JSON.stringify(layout, null, 2);
    const tmpPath = filePath + '.tmp';
    writeStateTemp(tmpPath, json);
    guardStateWrite(filePath); // re-checked at write time
    fs.renameSync(tmpPath, filePath);
    return null;
  } catch (err) {
    console.error('[Pixel Agents] Failed to write layout file:', err);
    return err instanceof Error ? err.message : String(err);
  }
}

/**
 * Import a layout FILE chosen by the user (the VS Code Import Layout path): its byte size BEFORE it
 * is read, then the shared check, then the write — so a refused import never replaces the saved
 * layout and is never reported as imported. Returns the imported layout, or the refusal reason.
 */
export function importLayoutFile(
  sourcePath: string,
  /** Called only once the import is accepted, right before the write (e.g. a watcher's
   *  markOwnWrite, so a refused import never swallows the next genuine external change). */
  beforeWrite?: () => void,
): { layout: Record<string, unknown> } | { refused: string } {
  let size: number;
  try {
    size = fs.statSync(sourcePath).size;
  } catch (err) {
    return { refused: err instanceof Error ? err.message : String(err) };
  }
  if (size > LAYOUT_MAX_FILE_BYTES)
    return { refused: `file is ${size} bytes, over ${LAYOUT_MAX_FILE_BYTES}` };
  const parsed = parseLayoutText(fs.readFileSync(sourcePath, 'utf-8'));
  if ('refused' in parsed) return parsed;
  beforeWrite?.();
  const notWritten = writeLayoutToFile(parsed.layout);
  return notWritten === null ? parsed : { refused: notWritten };
}

interface LayoutLoadResult {
  layout: Record<string, unknown>;
  /** True when the user's saved layout was replaced by a newer bundled default */
  wasReset: boolean;
}

/**
 * Load layout from file. Falls back to bundled default if the file is empty.
 * Resets to the bundled default when a newer revision is bundled.
 *
 * Migration from VS Code workspaceState happens earlier, in
 * adapters/vscode/migrateVsCodeState.ts (run on extension activate). By the
 * time this is called, the file is the only source of truth.
 *
 * 1. If file exists → return it (reset if bundled default has a newer revision)
 * 2. Else if defaultLayout provided → write to file, return it
 * 3. Else → return null
 */
export function loadLayout(
  defaultLayout?: Record<string, unknown> | null,
): LayoutLoadResult | null {
  const fromFile = readLayoutFromFile();
  if (fromFile) {
    const fileRevision = (fromFile[LAYOUT_REVISION_KEY] as number) ?? 0;
    const defaultRevision = (defaultLayout?.[LAYOUT_REVISION_KEY] as number) ?? 0;
    if (defaultRevision > fileRevision) {
      console.log(
        `[Pixel Agents] Layout revision outdated (${fileRevision} < ${defaultRevision}), resetting to bundled default`,
      );
      writeLayoutToFile(defaultLayout!);
      return { layout: defaultLayout!, wasReset: true };
    }
    console.log('[Pixel Agents] Layout loaded from file');
    return { layout: fromFile, wasReset: false };
  }

  if (layoutFileExists()) {
    // Present but refused (oversized, malformed or unreadable — logged by the read): show the
    // bundled default WITHOUT writing it, so the user's file is never overwritten.
    return defaultLayout ? { layout: defaultLayout, wasReset: false } : null;
  }

  if (defaultLayout) {
    console.log('[Pixel Agents] Writing bundled default layout to file');
    writeLayoutToFile(defaultLayout);
    return { layout: defaultLayout, wasReset: false };
  }

  return null;
}

/**
 * Watch ~/.pixel-agents/layout.json for external changes (other VS Code windows).
 * Uses hybrid fs.watch + polling (same pattern as JSONL watching).
 */
export function watchLayoutFile(
  onExternalChange: (layout: Record<string, unknown>) => void,
): LayoutWatcher {
  const filePath = getLayoutFilePath();
  let skipNextChange = false;
  let lastMtime = 0;
  let fsWatcher: fs.FSWatcher | null = null;
  let pollTimer: ReturnType<typeof setInterval> | null = null;
  let disposed = false;

  // Initialize lastMtime
  try {
    if (fs.existsSync(filePath)) {
      lastMtime = fs.statSync(filePath).mtimeMs;
    }
  } catch {
    /* ignore */
  }

  function checkForChange(): void {
    if (disposed) return;
    try {
      if (!fs.existsSync(filePath)) return;
      const stat = fs.statSync(filePath);
      if (stat.mtimeMs <= lastMtime) return;
      lastMtime = stat.mtimeMs;

      if (skipNextChange) {
        skipNextChange = false;
        return;
      }

      const layout = readBoundedLayout(filePath);
      if (!layout) return; // oversized or malformed: refused (logged), never forwarded
      console.log('[Pixel Agents] External layout change detected');
      onExternalChange(layout);
    } catch (err) {
      console.error('[Pixel Agents] Error checking layout file:', err);
    }
  }

  function startFsWatch(): void {
    if (disposed || fsWatcher) return;
    try {
      if (!fs.existsSync(filePath)) return;
      fsWatcher = fs.watch(filePath, () => {
        checkForChange();
      });
      fsWatcher.on('error', (err) => {
        // fs.watch can be unreliable on macOS (kqueue) and may hit inotify limits on Linux
        console.log(`[Pixel Agents] Layout: fs.watch error: ${err.message}`);
        fsWatcher?.close();
        fsWatcher = null;
      });
    } catch {
      // File may not exist yet — polling will retry
    }
  }

  // Start fs.watch if file exists
  startFsWatch();

  // Polling backup (also starts fs.watch if file appears)
  pollTimer = setInterval(() => {
    if (disposed) return;
    if (!fsWatcher) {
      startFsWatch();
    }
    checkForChange();
  }, LAYOUT_FILE_POLL_INTERVAL_MS);

  return {
    markOwnWrite(): void {
      skipNextChange = true;
      // Update lastMtime preemptively so a near-instant poll doesn't miss the flag
      try {
        if (fs.existsSync(filePath)) {
          lastMtime = fs.statSync(filePath).mtimeMs;
        }
      } catch {
        /* ignore */
      }
    },
    dispose(): void {
      disposed = true;
      fsWatcher?.close();
      fsWatcher = null;
      if (pollTimer) {
        clearInterval(pollTimer);
        pollTimer = null;
      }
    },
  };
}
