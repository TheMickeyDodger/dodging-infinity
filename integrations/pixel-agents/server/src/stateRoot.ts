import * as fs from 'fs';
import * as os from 'os';
import * as path from 'path';

import { LAYOUT_FILE_DIR } from './constants.js';

/**
 * The ONE directory every persisted Pixel Agents file resolves under: config.json,
 * layout.json, <namespace>-state.json, server.json and servers/.
 *
 * Default: `~/.pixel-agents`, recomputed on every call so an unset root behaves
 * exactly as before this seam existed (tests that point HOME or os.homedir() at a
 * temp dir keep working). A separate surface — the DI office — injects its own
 * root with setStateRoot() as the first thing it does, so nothing it persists can
 * land in the shared `~/.pixel-agents`. This is a root, not a HOME rewrite: the
 * Claude provider's `~/.claude` paths are untouched (DI mode never reaches them).
 */
let injectedRoot: string | null = null;
let resolved = false;

export function getStateRoot(): string {
  resolved = true;
  return injectedRoot ?? path.join(os.homedir(), LAYOUT_FILE_DIR);
}

/**
 * Inject the state root. Accepted only as an absolute path and only BEFORE the
 * root was first resolved: a process that already read or wrote state under one
 * root must never continue under another (that would split its state silently).
 */
export function setStateRoot(absDir: string): void {
  if (typeof absDir !== 'string' || !path.isAbsolute(absDir)) {
    throw new Error('State root must be an absolute path');
  }
  if (resolved) {
    throw new Error('State root was already resolved; inject it before any state access');
  }
  injectedRoot = path.normalize(absDir);
}

/**
 * Write containment for an INJECTED root. Validating the root does not contain the writes
 * beneath it: a component inside it can be a symlink pointing elsewhere. The surface that
 * injects a root also installs a guard; every persisted write calls guardStateWrite() on
 * its target AT WRITE TIME (before creating directories and again just before the
 * rename), and the guard throws — naming the escape — when the target would land outside
 * the validated physical root. With no guard installed (the default `~/.pixel-agents`
 * surfaces) every call here is a no-op and writes behave exactly as before.
 */
let writeGuard: ((target: string) => void) | null = null;

export function setStateWriteGuard(guard: (target: string) => void): void {
  writeGuard = guard;
}

export function guardStateWrite(target: string): void {
  writeGuard?.(target);
}

/**
 * Write the temp half of an atomic replace. Default root: unchanged
 * (`writeFileSync(tmp, data, 'utf-8')`). Guarded root: the temp path is checked like any
 * other target, any stale temp entry is removed (rm never follows a symlink), and the file
 * is created EXCLUSIVELY — O_EXCL never follows a symlink planted at the temp path.
 */
export function writeStateTemp(tmpPath: string, data: string): void {
  if (!writeGuard) {
    fs.writeFileSync(tmpPath, data, 'utf-8');
    return;
  }
  writeGuard(tmpPath);
  fs.rmSync(tmpPath, { force: true });
  fs.writeFileSync(tmpPath, data, { encoding: 'utf-8', flag: 'wx' });
}

/** Test-only: forget an injected root, its write guard and the resolution latch. */
export function resetStateRootForTests(): void {
  injectedRoot = null;
  writeGuard = null;
  resolved = false;
}
