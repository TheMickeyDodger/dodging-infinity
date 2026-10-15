/**
 * READ-ONLY reader of the DI backend's discovery file.
 *
 * The file is created by the backend's own runtime (directory 0700, file 0600,
 * written atomically, removed on shutdown). This module only reads it: it never
 * creates, repairs or waits for it, and it never starts the backend. Anything other
 * than a private regular file holding a loopback endpoint is an explicit problem
 * code — which the caller renders as an honest "unavailable" state.
 *
 * The token it returns is held in memory only. No problem code, message or log
 * line produced here contains the token or any other file content.
 */

import * as fs from 'fs';
import * as path from 'path';

/** Discovery files are tiny JSON objects; anything larger is refused unread. */
const MAX_DISCOVERY_BYTES = 4096;
const OPEN_MODE_BITS = 0o077;
const LOOPBACK_HOST = '127.0.0.1';

export interface BackendEndpoint {
  port: number;
  /** Per-launch bearer secret. Memory only; never log or echo it. */
  token: string;
}

export type DiscoveryProblem =
  | 'discovery_absent'
  | 'discovery_directory_untrusted'
  | 'discovery_symlink'
  | 'discovery_not_regular_file'
  | 'discovery_permissions_open'
  | 'discovery_not_owned'
  | 'discovery_too_large'
  | 'discovery_unreadable'
  | 'discovery_malformed'
  | 'discovery_not_loopback';

export type DiscoveryResult =
  { ok: true; endpoint: BackendEndpoint } | { ok: false; problem: DiscoveryProblem };

function currentUid(): number | null {
  return typeof process.getuid === 'function' ? process.getuid() : null;
}

/**
 * The ONE place the discovery document's shape is known — retarget here if the
 * backend's confirmed shape changes. v3 shape: { url: "http://127.0.0.1:<port>",
 * port: <int>, token: "<secret>" }. Extra keys are ignored; the url must name the
 * loopback host and the same port.
 */
export function parseDiscoveryDocument(value: unknown): BackendEndpoint | DiscoveryProblem {
  if (typeof value !== 'object' || value === null || Array.isArray(value)) {
    return 'discovery_malformed';
  }
  const doc = value as Record<string, unknown>;
  const { url, port, token } = doc;
  if (typeof port !== 'number' || !Number.isInteger(port) || port < 1 || port > 65535) {
    return 'discovery_malformed';
  }
  if (typeof token !== 'string' || token.length === 0 || token.length > 512 || /\s/.test(token)) {
    return 'discovery_malformed';
  }
  if (typeof url !== 'string') return 'discovery_malformed';
  if (url !== `http://${LOOPBACK_HOST}:${port}`) return 'discovery_not_loopback';
  return { port, token };
}

/** Read the discovery file at `filePath` (absolute). Never throws. */
export function readBackendDiscovery(filePath: string): DiscoveryResult {
  const fail = (problem: DiscoveryProblem): DiscoveryResult => ({ ok: false, problem });
  if (!path.isAbsolute(filePath)) return fail('discovery_unreadable');
  const uid = currentUid();

  // The containing directory must be a private, owned, real directory (0700).
  let dirStat: fs.Stats;
  try {
    dirStat = fs.lstatSync(path.dirname(filePath));
  } catch (err) {
    return fail(
      (err as NodeJS.ErrnoException).code === 'ENOENT'
        ? 'discovery_absent'
        : 'discovery_unreadable',
    );
  }
  if (
    !dirStat.isDirectory() ||
    (dirStat.mode & OPEN_MODE_BITS) !== 0 ||
    (uid !== null && dirStat.uid !== uid)
  ) {
    return fail('discovery_directory_untrusted');
  }

  let before: fs.Stats;
  try {
    before = fs.lstatSync(filePath);
  } catch (err) {
    return fail(
      (err as NodeJS.ErrnoException).code === 'ENOENT'
        ? 'discovery_absent'
        : 'discovery_unreadable',
    );
  }
  if (before.isSymbolicLink()) return fail('discovery_symlink');
  if (!before.isFile()) return fail('discovery_not_regular_file');

  let fd: number;
  try {
    fd = fs.openSync(
      filePath,
      fs.constants.O_RDONLY | fs.constants.O_NOFOLLOW | fs.constants.O_NONBLOCK,
    );
  } catch (err) {
    const code = (err as NodeJS.ErrnoException).code;
    if (code === 'ENOENT') return fail('discovery_absent');
    if (code === 'ELOOP') return fail('discovery_symlink');
    return fail('discovery_unreadable');
  }
  try {
    const after = fs.fstatSync(fd);
    if (!after.isFile()) return fail('discovery_not_regular_file');
    if (after.dev !== before.dev || after.ino !== before.ino) return fail('discovery_unreadable');
    if ((after.mode & OPEN_MODE_BITS) !== 0) return fail('discovery_permissions_open');
    if (uid !== null && after.uid !== uid) return fail('discovery_not_owned');
    if (after.size > MAX_DISCOVERY_BYTES) return fail('discovery_too_large');
    const buffer = Buffer.alloc(MAX_DISCOVERY_BYTES + 1);
    const bytesRead = fs.readSync(fd, buffer, 0, buffer.length, 0);
    if (bytesRead > MAX_DISCOVERY_BYTES) return fail('discovery_too_large');
    let parsed: unknown;
    try {
      parsed = JSON.parse(buffer.subarray(0, bytesRead).toString('utf-8'));
    } catch {
      return fail('discovery_malformed');
    }
    const endpoint = parseDiscoveryDocument(parsed);
    return typeof endpoint === 'string' ? fail(endpoint) : { ok: true, endpoint };
  } catch {
    return fail('discovery_unreadable');
  } finally {
    fs.closeSync(fd);
  }
}
