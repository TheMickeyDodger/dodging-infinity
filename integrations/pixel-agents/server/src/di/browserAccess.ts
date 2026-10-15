/**
 * Hand-over of the DI office's per-launch BROWSER credential.
 *
 * The openable URL carries this office's own token, so it is never printed: stdout lands
 * in terminal scrollback, captured gate logs, packaged evidence and screenshots. It is
 * published to ONE private file inside the isolated DI state root instead —
 * `<stateRoot>/run/browser-access.json`, mode 0600 in a 0700 directory.
 *
 * ATOMICITY IS NOT OWNERSHIP. The file is owned per launch, at both ends:
 * - PUBLISH is an EXCLUSIVE create: the content is written to a private temp file, then
 *   hard-linked into place. link() fails if the name exists, so a second launch at the
 *   same root REFUSES TO START (naming the existing file) instead of clobbering the
 *   first launch's still-valid credentials. (Same pattern as the backend's discovery
 *   contract and this repo's hook-script install.)
 * - REMOVE deletes the file ONLY IF IT IS STILL OURS: the same device + inode recorded
 *   at publish time AND our token inside. Anything else is left in place and reported —
 *   a shutdown never deletes another launch's file.
 * Every write is checked by the state-root write guard (physical containment at write
 * time), so a symlinked `run/` cannot carry the file outside the DI root.
 *
 * It holds OUR browser token only. The backend's bearer token is a different secret on a
 * different surface (read from the backend's own discovery file, sent only to the backend)
 * and is never written here.
 */
import * as crypto from 'crypto';
import * as fs from 'fs';
import * as path from 'path';

import { guardStateWrite } from '../stateRoot.js';

export const BROWSER_ACCESS_DIR = 'run';
export const BROWSER_ACCESS_FILE = 'browser-access.json';
const PRIVATE_DIR_MODE = 0o700;
const PRIVATE_FILE_MODE = 0o600;

export interface BrowserAccess {
  /** The openable URL, including the per-launch token. */
  url: string;
  port: number;
  token: string;
}

/** What this launch published: enough to recognise the file as still ours at removal. */
export interface PublishedBrowserAccess {
  path: string;
  dev: bigint;
  ino: bigint;
  token: string;
}

export class BrowserAccessExists extends Error {}

export type UnpublishOutcome = 'removed' | 'absent' | 'not-ours';

export function browserAccessPath(stateRoot: string): string {
  return path.join(stateRoot, BROWSER_ACCESS_DIR, BROWSER_ACCESS_FILE);
}

/**
 * Publish the access details owner-only, EXCLUSIVELY. Throws BrowserAccessExists when the
 * file is already present (another launch at this root, or one that did not shut down).
 */
export function publishBrowserAccess(
  stateRoot: string,
  access: BrowserAccess,
): PublishedBrowserAccess {
  const dir = path.join(stateRoot, BROWSER_ACCESS_DIR);
  const target = path.join(dir, BROWSER_ACCESS_FILE);
  guardStateWrite(target);
  fs.mkdirSync(dir, { recursive: true, mode: PRIVATE_DIR_MODE });
  fs.chmodSync(dir, PRIVATE_DIR_MODE); // exact, whatever the umask or a pre-existing mode
  const temp = path.join(
    dir,
    `.${BROWSER_ACCESS_FILE}.${process.pid}.${crypto.randomBytes(6).toString('hex')}.tmp`,
  );
  guardStateWrite(temp);
  const fd = fs.openSync(temp, 'wx', PRIVATE_FILE_MODE); // O_EXCL: never follows a link
  try {
    fs.fchmodSync(fd, PRIVATE_FILE_MODE);
    fs.writeSync(
      fd,
      JSON.stringify({ url: access.url, port: access.port, token: access.token }, null, 2) + '\n',
    );
    fs.fsyncSync(fd);
  } finally {
    fs.closeSync(fd);
  }
  try {
    guardStateWrite(target); // re-checked at write time, just before publishing
    fs.linkSync(temp, target); // exclusive: fails with EEXIST rather than replacing
  } catch (err) {
    fs.rmSync(temp, { force: true });
    if ((err as NodeJS.ErrnoException).code === 'EEXIST') {
      throw new BrowserAccessExists(
        `a browser access file already exists at ${target}: another DI office may be running from this state root. ` +
          `If no DI office is running from it, the file is STALE (left by a run that was killed): delete ${target} and start again.`,
      );
    }
    throw err;
  }
  const st = fs.lstatSync(target, { bigint: true });
  fs.rmSync(temp, { force: true });
  return { path: target, dev: st.dev, ino: st.ino, token: access.token };
}

/** Remove the access file ONLY IF IT IS STILL OURS (idempotent). */
export function unpublishBrowserAccess(published: PublishedBrowserAccess): UnpublishOutcome {
  let st: fs.BigIntStats;
  try {
    st = fs.lstatSync(published.path, { bigint: true });
  } catch (err) {
    if ((err as NodeJS.ErrnoException).code === 'ENOENT') return 'absent';
    throw err;
  }
  if (st.isSymbolicLink() || st.dev !== published.dev || st.ino !== published.ino) {
    return 'not-ours';
  }
  let token: unknown;
  try {
    token = (JSON.parse(fs.readFileSync(published.path, 'utf-8')) as { token?: unknown }).token;
  } catch {
    return 'not-ours';
  }
  if (token !== published.token) return 'not-ours';
  fs.rmSync(published.path, { force: true });
  return 'removed';
}
