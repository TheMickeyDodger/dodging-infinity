/**
 * DI-mode validation of the DI office state root. DI MODE ONLY: ordinary Pixel Agents
 * defaults and the Claude paths are untouched by this module.
 *
 * Normalization is not validation: a normalized path can still resolve to the shared
 * `~/.pixel-agents` through a symlink, a differently-cased spelling, or containment.
 * So the decision is made on PHYSICAL paths:
 * - a path that does not exist yet is resolved by walking up to its NEAREST EXISTING
 *   ANCESTOR, resolving that with realpath, then re-appending the remaining segments
 *   (a symlinked parent cannot slip through);
 * - the DI root is REJECTED when it equals the shared root, lies INSIDE it, or
 *   CONTAINS it — containment in either direction would sweep the shared profile into
 *   DI writes;
 * - comparison is CASE-INSENSITIVE, unconditionally: on macOS and Windows a
 *   differently-cased path is the same directory (the same lesson as the hook-identity
 *   matching in claudeHookInstaller.ts). On a case-sensitive Linux volume this can
 *   refuse a genuinely distinct path; refusing is the safe direction.
 * A conflict FAILS CLOSED: the caller refuses to start.
 */

import * as fs from 'fs';
import * as os from 'os';
import * as path from 'path';

import { LAYOUT_FILE_DIR } from '../constants.js';

export class DiStateRootConflict extends Error {}

/** The physical path of `p`, resolving through its nearest existing ancestor. */
export function physicalPath(p: string): string {
  const absolute = path.resolve(p);
  const pending: string[] = [];
  let current = absolute;
  for (;;) {
    try {
      const resolved = fs.realpathSync.native(current);
      return pending.length === 0 ? resolved : path.join(resolved, ...pending.reverse());
    } catch (err) {
      const code = (err as NodeJS.ErrnoException).code;
      if (code !== 'ENOENT' && code !== 'ENOTDIR') throw err;
      const parent = path.dirname(current);
      if (parent === current) return absolute; // nothing exists up to the root
      pending.push(path.basename(current));
      current = parent;
    }
  }
}

function comparable(p: string): string {
  const trimmed = p.length > 1 && p.endsWith(path.sep) ? p.slice(0, -1) : p;
  return trimmed.toLowerCase();
}

function isSameOrInside(child: string, parent: string): boolean {
  return (
    child === parent || child.startsWith(parent.endsWith(path.sep) ? parent : parent + path.sep)
  );
}

/** The shared Pixel Agents profile root this process would use by default. */
export function sharedStateRoot(): string {
  return path.join(os.homedir(), LAYOUT_FILE_DIR);
}

/**
 * Validate a DI state root against the shared profile. Returns the physical DI root to
 * use; throws DiStateRootConflict (fail closed) naming the conflict.
 */
export function validateDiStateRoot(candidate: string, shared: string = sharedStateRoot()): string {
  if (!path.isAbsolute(candidate)) {
    throw new DiStateRootConflict('the DI state root must be an absolute path');
  }
  const diPhysical = physicalPath(candidate);
  const sharedPhysical = physicalPath(shared);
  const di = comparable(diPhysical);
  const sh = comparable(sharedPhysical);
  if (di === sh) {
    throw new DiStateRootConflict(
      `the DI state root resolves to the shared Pixel Agents profile (${sharedPhysical}); choose a separate directory`,
    );
  }
  if (isSameOrInside(di, sh)) {
    throw new DiStateRootConflict(
      `the DI state root resolves inside the shared Pixel Agents profile (${sharedPhysical}); choose a separate directory`,
    );
  }
  if (isSameOrInside(sh, di)) {
    throw new DiStateRootConflict(
      `the DI state root resolves to an ancestor of the shared Pixel Agents profile (${sharedPhysical}); choose a separate directory`,
    );
  }
  return diPhysical;
}

export class DiStateWriteEscape extends Error {}

/** Trailing-separator trim WITHOUT case folding (identity is physical and case-preserving). */
function preserving(p: string): string {
  return p.length > 1 && p.endsWith(path.sep) ? p.slice(0, -1) : p;
}

/**
 * WRITE AUTHORIZATION: is `parentPhysical` the physical root or inside it — compared
 * CASE-PRESERVING. The asymmetry is deliberate (round-2 blocker 2): conservative case
 * folding is for REJECTION only (validateDiStateRoot, refusing a conflicting root at
 * startup). Used to AUTHORIZE a write it is unsafe: on a case-sensitive volume a DISTINCT
 * sibling `/x/DI-ROOT` would fold equal to the root `/x/di-root`, and a symlinked
 * descendant pointing there would be accepted. On a case-insensitive volume a symlink that
 * spells the root itself in a different case may be refused here — refusal is the safe
 * direction.
 */
export function isInsidePhysicalRoot(parentPhysical: string, rootPhysical: string): boolean {
  return isSameOrInside(preserving(parentPhysical), preserving(rootPhysical));
}

/**
 * WRITE containment beneath the validated root (validating the root alone does not
 * contain the writes under it). Returns the guard installed with setStateWriteGuard():
 * for every write target, AT WRITE TIME and BEFORE anything is created, chmodded or
 * written, the target's PARENT is resolved physically (through its nearest existing
 * ancestor, so a symlinked descendant directory is followed to where it really points)
 * and must lie inside the validated physical root by CASE-PRESERVING identity
 * (isInsidePhysicalRoot); a target that is itself a symbolic link is refused too. Either
 * case throws DiStateWriteEscape naming the escape — the write does not happen.
 */
export function makeDiStateWriteGuard(physicalRoot: string): (target: string) => void {
  return (target: string): void => {
    if (!path.isAbsolute(target)) {
      throw new DiStateWriteEscape(`DI state write refused: ${target} is not an absolute path`);
    }
    const parent = physicalPath(path.dirname(target));
    if (!isInsidePhysicalRoot(parent, physicalRoot)) {
      throw new DiStateWriteEscape(
        `DI state write refused: ${target} resolves to ${path.join(parent, path.basename(target))}, outside the DI state root ${physicalRoot}`,
      );
    }
    let isLink = false;
    try {
      isLink = fs.lstatSync(target).isSymbolicLink();
    } catch (err) {
      if ((err as NodeJS.ErrnoException).code !== 'ENOENT') throw err;
    }
    if (isLink) {
      throw new DiStateWriteEscape(
        `DI state write refused: ${target} is a symbolic link (to ${fs.readlinkSync(target)}); DI state files are never written through links`,
      );
    }
  };
}
