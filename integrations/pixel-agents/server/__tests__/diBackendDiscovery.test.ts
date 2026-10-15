/**
 * The backend discovery file is READ-ONLY input created by the backend's runtime.
 * Every case is constructed in a sandbox temp dir; nothing here touches the real
 * runtime discovery path, and no token value ever appears in a result's problem.
 */
import * as fs from 'fs';
import * as os from 'os';
import * as path from 'path';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';

import { parseDiscoveryDocument, readBackendDiscovery } from '../src/di/backendDiscovery.js';

const SECRET = 'test-secret-token-value-0123456789';

let sandbox: string;
let dir: string;
let file: string;

function write(content: unknown, mode = 0o600): void {
  fs.writeFileSync(file, typeof content === 'string' ? content : JSON.stringify(content), { mode });
  fs.chmodSync(file, mode);
}

beforeEach(() => {
  sandbox = fs.mkdtempSync(path.join(os.tmpdir(), 'di-discovery-'));
  dir = path.join(sandbox, 'runtime');
  fs.mkdirSync(dir, { mode: 0o700 });
  fs.chmodSync(dir, 0o700);
  file = path.join(dir, 'backend-discovery.json');
});

afterEach(() => {
  fs.rmSync(sandbox, { recursive: true, force: true });
});

describe('readBackendDiscovery (read-only)', () => {
  it('reads a private loopback endpoint', () => {
    write({ url: 'http://127.0.0.1:53421', port: 53421, token: SECRET });
    expect(readBackendDiscovery(file)).toEqual({
      ok: true,
      endpoint: { port: 53421, token: SECRET },
    });
  });

  it('absent file -> discovery_absent (an honest unavailable state; nothing is created)', () => {
    expect(readBackendDiscovery(file)).toEqual({ ok: false, problem: 'discovery_absent' });
    expect(fs.existsSync(file)).toBe(false);
  });

  it('absent directory -> discovery_absent, and the directory is not created', () => {
    const missing = path.join(sandbox, 'nope', 'backend-discovery.json');
    expect(readBackendDiscovery(missing)).toEqual({ ok: false, problem: 'discovery_absent' });
    expect(fs.existsSync(path.join(sandbox, 'nope'))).toBe(false);
  });

  it('group/other-readable file -> discovery_permissions_open', () => {
    write({ url: 'http://127.0.0.1:1', port: 1, token: SECRET }, 0o644);
    expect(readBackendDiscovery(file)).toEqual({
      ok: false,
      problem: 'discovery_permissions_open',
    });
  });

  it('a non-private directory -> discovery_directory_untrusted', () => {
    write({ url: 'http://127.0.0.1:1', port: 1, token: SECRET });
    fs.chmodSync(dir, 0o755);
    expect(readBackendDiscovery(file)).toEqual({
      ok: false,
      problem: 'discovery_directory_untrusted',
    });
  });

  it('a symlink -> discovery_symlink (never followed)', () => {
    const real = path.join(dir, 'real.json');
    fs.writeFileSync(real, JSON.stringify({ url: 'http://127.0.0.1:1', port: 1, token: SECRET }), {
      mode: 0o600,
    });
    fs.symlinkSync(real, file);
    expect(readBackendDiscovery(file)).toEqual({ ok: false, problem: 'discovery_symlink' });
  });

  it('not JSON / wrong shape -> discovery_malformed', () => {
    write('not json');
    expect(readBackendDiscovery(file)).toEqual({ ok: false, problem: 'discovery_malformed' });
    write({ url: 'http://127.0.0.1:1', port: '1', token: SECRET });
    expect(readBackendDiscovery(file)).toEqual({ ok: false, problem: 'discovery_malformed' });
    write({ url: 'http://127.0.0.1:1', port: 1, token: '' });
    expect(readBackendDiscovery(file)).toEqual({ ok: false, problem: 'discovery_malformed' });
  });

  it('a non-loopback or mismatched url -> discovery_not_loopback', () => {
    write({ url: 'http://0.0.0.0:53421', port: 53421, token: SECRET });
    expect(readBackendDiscovery(file)).toEqual({ ok: false, problem: 'discovery_not_loopback' });
    write({ url: 'http://127.0.0.1:1', port: 2, token: SECRET });
    expect(readBackendDiscovery(file)).toEqual({ ok: false, problem: 'discovery_not_loopback' });
    write({ url: 'http://localhost:2', port: 2, token: SECRET });
    expect(readBackendDiscovery(file)).toEqual({ ok: false, problem: 'discovery_not_loopback' });
  });

  it('oversized -> discovery_too_large', () => {
    write({ url: 'http://127.0.0.1:1', port: 1, token: SECRET, pad: 'x'.repeat(5000) });
    expect(readBackendDiscovery(file)).toEqual({ ok: false, problem: 'discovery_too_large' });
  });

  it('problem results never carry the token or file content', () => {
    write({ url: 'http://0.0.0.0:1', port: 1, token: SECRET });
    expect(JSON.stringify(readBackendDiscovery(file))).not.toContain(SECRET);
  });

  it('reading leaves the file byte-identical (read-only)', () => {
    write({ url: 'http://127.0.0.1:9', port: 9, token: SECRET });
    const before = fs.readFileSync(file);
    const statBefore = fs.statSync(file);
    readBackendDiscovery(file);
    expect(fs.readFileSync(file).equals(before)).toBe(true);
    expect(fs.statSync(file).mode).toBe(statBefore.mode);
  });

  it('the shape boundary ignores extra keys', () => {
    expect(
      parseDiscoveryDocument({ url: 'http://127.0.0.1:5', port: 5, token: 't', extra: 1 }),
    ).toEqual({
      port: 5,
      token: 't',
    });
  });
});
