/**
 * The DI office hands its per-launch browser credential over through ONE owner-only file in
 * the isolated state root — never stdout — and owns that file PER LAUNCH: exclusive
 * create (a second launch at the same root is refused), removal only while it is still
 * ours, and a drain-first, idempotent shutdown. Sandbox + fake HOME only.
 */
import type { ChildProcessByStdio } from 'child_process';
import { spawn } from 'child_process';
import * as crypto from 'crypto';
import * as fs from 'fs';
import * as http from 'http';
import * as net from 'net';
import * as os from 'os';
import * as path from 'path';
import type { Readable } from 'stream';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { WebSocket } from 'ws';

import { DI_SHUTDOWN_DRAIN_TIMEOUT_MS } from '../src/constants.js';
import {
  BrowserAccessExists,
  browserAccessPath,
  publishBrowserAccess,
  unpublishBrowserAccess,
} from '../src/di/browserAccess.js';

const DI_BUNDLE = path.join(__dirname, '../../dist/di-cli.js');
const TOKEN = '0b5c1f9e-7a44-4d0e-9f1a-1c2d3e4f5a6b';
const START_TIMEOUT_MS = 15_000;

let sandbox: string;
type OfficeProcess = ChildProcessByStdio<null, Readable, Readable>;
const running: OfficeProcess[] = [];

beforeEach(() => {
  sandbox = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), 'di-access-')));
});

afterEach(() => {
  for (const child of running.splice(0)) if (child.exitCode === null) child.kill('SIGKILL');
  fs.rmSync(sandbox, { recursive: true, force: true });
});

describe('browser access file (module)', () => {
  it('is 0600 in a 0700 directory, holds exactly url/port/token, and leaves no temp file', () => {
    const root = path.join(sandbox, 'di-state');
    const published = publishBrowserAccess(root, {
      url: `http://127.0.0.1:4242/?token=${TOKEN}`,
      port: 4242,
      token: TOKEN,
    });
    const file = published.path;
    expect(file).toBe(browserAccessPath(root));
    expect(fs.statSync(file).mode & 0o777).toBe(0o600);
    expect(fs.statSync(path.dirname(file)).mode & 0o777).toBe(0o700);
    expect(JSON.parse(fs.readFileSync(file, 'utf-8'))).toEqual({
      url: `http://127.0.0.1:4242/?token=${TOKEN}`,
      port: 4242,
      token: TOKEN,
    });
    expect(fs.readdirSync(path.dirname(file))).toEqual([path.basename(file)]);
  });

  it('tightens a pre-existing looser directory', () => {
    const root = path.join(sandbox, 'di-state');
    fs.mkdirSync(path.join(root, 'run'), { recursive: true, mode: 0o755 });
    fs.chmodSync(path.join(root, 'run'), 0o755);
    const { path: file } = publishBrowserAccess(root, { url: 'u', port: 1, token: 'x' });
    expect(fs.statSync(path.dirname(file)).mode & 0o777).toBe(0o700);
    expect(fs.statSync(file).mode & 0o777).toBe(0o600);
  });

  it('REFUSES an existing file (no clobber), naming it, and leaves that file untouched', () => {
    const root = path.join(sandbox, 'di-state');
    fs.mkdirSync(path.join(root, 'run'), { recursive: true });
    fs.writeFileSync(browserAccessPath(root), 'SOMEONE ELSE', { mode: 0o600 });
    expect(() => publishBrowserAccess(root, { url: 'u', port: 1, token: 'x' })).toThrow(
      BrowserAccessExists,
    );
    expect(() => publishBrowserAccess(root, { url: 'u', port: 1, token: 'x' })).toThrow(
      /already exists at .*browser-access\.json/,
    );
    // The message names the stale file and how to clear it (a killed run must not brick the next).
    expect(() => publishBrowserAccess(root, { url: 'u', port: 1, token: 'x' })).toThrow(
      new RegExp(
        `is STALE .*delete ${browserAccessPath(root).replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}`,
      ),
    );
    expect(fs.readFileSync(browserAccessPath(root), 'utf-8')).toBe('SOMEONE ELSE');
    expect(fs.readdirSync(path.join(root, 'run'))).toEqual(['browser-access.json']);
  });

  it('removes ONLY its own file: removed, then absent; a replaced file or another token is not ours', () => {
    const root = path.join(sandbox, 'di-state');
    const mine = publishBrowserAccess(root, { url: 'u', port: 1, token: 'mine' });
    expect(unpublishBrowserAccess(mine)).toBe('removed');
    expect(unpublishBrowserAccess(mine)).toBe('absent'); // idempotent

    const again = publishBrowserAccess(root, { url: 'u', port: 1, token: 'mine' });
    // Another launch's file now sits at the same path (different inode).
    fs.rmSync(again.path);
    fs.writeFileSync(again.path, JSON.stringify({ url: 'v', port: 2, token: 'theirs' }));
    expect(unpublishBrowserAccess(again)).toBe('not-ours');
    expect(JSON.parse(fs.readFileSync(again.path, 'utf-8')).token).toBe('theirs');

    // Same inode, but the content is no longer ours: still not ours.
    fs.rmSync(again.path);
    const third = publishBrowserAccess(root, { url: 'u', port: 1, token: 'mine' });
    fs.writeFileSync(third.path, JSON.stringify({ url: 'w', port: 3, token: 'other' }));
    expect(unpublishBrowserAccess(third)).toBe('not-ours');
    expect(fs.existsSync(third.path)).toBe(true);
  });
});

interface Office {
  child: OfficeProcess;
  output: () => string;
  exited: Promise<number | null>;
}

function spawnOffice(stateDir: string, home: string): Office {
  if (!fs.existsSync(DI_BUNDLE)) throw new Error('dist/di-cli.js missing: run `node esbuild.js`');
  const child = spawn(
    process.execPath,
    [
      DI_BUNDLE,
      '--state-dir',
      stateDir,
      '--backend-discovery',
      path.join(sandbox, 'runtime', 'backend-discovery.json'), // absent: honest unavailable
    ],
    {
      env: { ...process.env, HOME: home, USERPROFILE: home, PIXEL_AGENTS_DEBUG_LOG: '' },
      stdio: ['ignore', 'pipe', 'pipe'],
    },
  );
  running.push(child);
  let out = '';
  child.stdout.on('data', (c: Buffer) => (out += c.toString()));
  child.stderr.on('data', (c: Buffer) => (out += c.toString()));
  const exited = new Promise<number | null>((r) => child.once('exit', (code) => r(code)));
  return { child, output: () => out, exited };
}

async function waitUntil(predicate: () => boolean, what: string, office?: Office): Promise<void> {
  const deadline = Date.now() + START_TIMEOUT_MS;
  while (!predicate()) {
    if (Date.now() > deadline) throw new Error(`timed out waiting for ${what}`);
    if (office && office.child.exitCode !== null) throw new Error(`exited before ${what}`);
    await new Promise((r) => setTimeout(r, 50));
  }
}

async function startOffice(stateDir: string): Promise<Office & { file: string; port: number }> {
  const home = path.join(sandbox, `home-${path.basename(stateDir)}`);
  fs.mkdirSync(home, { recursive: true });
  const office = spawnOffice(stateDir, home);
  const file = browserAccessPath(stateDir);
  await waitUntil(
    () => /Open the URL stored in/.test(office.output()) && fs.existsSync(file),
    'the access file',
    office,
  );
  const { port } = JSON.parse(fs.readFileSync(file, 'utf-8')) as { port: number };
  return { ...office, file, port };
}

function httpStatus(port: number): Promise<number> {
  return new Promise((resolve, reject) => {
    http
      .get({ host: '127.0.0.1', port, path: '/' }, (res) => {
        res.resume();
        resolve(res.statusCode ?? 0);
      })
      .on('error', reject);
  });
}

describe('browser access file (BUILT office lifecycle)', () => {
  it('exists while the office runs, never reaches stdout, and is removed on shutdown in drain-first order', async () => {
    const stateDir = path.join(sandbox, 'di-state');
    const office = await startOffice(stateDir);
    const { token } = JSON.parse(fs.readFileSync(office.file, 'utf-8')) as { token: string };
    expect(token).toMatch(/^[0-9a-f-]{36}$/);
    expect(office.output()).toContain(`Open the URL stored in ${office.file}`);
    expect(office.output()).not.toContain(token);
    office.child.kill('SIGTERM');
    expect(await office.exited).toBe(0);
    expect(fs.existsSync(office.file)).toBe(false);
    const out = office.output();
    const closed = out.indexOf('shutdown: server closed, in-flight requests drained');
    const stopped = out.indexOf('shutdown: office stopped');
    const removed = out.indexOf('shutdown: browser access file removed');
    expect(closed).toBeGreaterThan(-1);
    expect(stopped).toBeGreaterThan(closed);
    expect(removed).toBeGreaterThan(stopped);
    expect(out).not.toContain('FORCE-TERMINATED'); // the normal path DRAINED
    expect(out).not.toContain(token);
  });

  it(
    'a client that never finishes: the drain is BOUNDED, reports FORCE-TERMINATED (not drained), exits, and still removes the access file',
    async () => {
      const stateDir = path.join(sandbox, 'di-state');
      const office = await startOffice(stateDir);
      // A NON-COOPERATING WebSocket peer: it completes the authenticated upgrade, then never
      // answers the server's close frame, so its socket stays open and close() alone would
      // wait. (Observed: a merely half-sent HTTP request does NOT hold close() — Fastify's
      // 'idle' handling destroyed it and the drain completed — log 20261015T002148Z/08.)
      const { token } = JSON.parse(fs.readFileSync(office.file, 'utf-8')) as { token: string };
      const stuck = net.connect(office.port, '127.0.0.1');
      await new Promise<void>((resolve) => stuck.once('connect', () => resolve()));
      stuck.on('error', () => undefined);
      const upgraded = new Promise<void>((resolve) => {
        let head = '';
        stuck.on('data', (d: Buffer) => {
          head += d.toString('latin1');
          if (head.startsWith('HTTP/1.1 101')) resolve();
        });
      });
      stuck.write(
        [
          `GET /ws?token=${token} HTTP/1.1`,
          `Host: 127.0.0.1:${office.port}`,
          'Upgrade: websocket',
          'Connection: Upgrade',
          `Sec-WebSocket-Key: ${crypto.randomBytes(16).toString('base64')}`,
          'Sec-WebSocket-Version: 13',
          '',
          '',
        ].join('\r\n'),
      );
      await upgraded; // from here on the peer ignores every frame, the close frame included
      const signalledAt = Date.now();
      office.child.kill('SIGTERM');
      const code = await office.exited;
      const elapsed = Date.now() - signalledAt;
      stuck.destroy();
      expect(code).toBe(1); // not drained: visible in the exit status too
      expect(elapsed).toBeGreaterThanOrEqual(DI_SHUTDOWN_DRAIN_TIMEOUT_MS - 100);
      expect(elapsed).toBeLessThan(DI_SHUTDOWN_DRAIN_TIMEOUT_MS + 4_000);
      const out = office.output();
      expect(out).toContain(
        `FORCE-TERMINATED after ${DI_SHUTDOWN_DRAIN_TIMEOUT_MS / 1000} s — NOT drained; in-flight work may have been cut`,
      );
      expect(out).not.toContain('in-flight requests drained');
      expect(out).toContain('shutdown: office stopped');
      expect(out).toContain('shutdown: browser access file removed'); // cleanup on the forced path too
      expect(fs.existsSync(office.file)).toBe(false); // the next launch is not bricked
    },
    DI_SHUTDOWN_DRAIN_TIMEOUT_MS + 15_000,
  );

  it('a SECOND launch at the same root is REFUSED while the first still serves, and the first file is intact', async () => {
    const stateDir = path.join(sandbox, 'di-state');
    const first = await startOffice(stateDir);
    const before = fs.readFileSync(first.file, 'utf-8');
    fs.mkdirSync(path.join(sandbox, 'home-second'), { recursive: true });
    const second = spawnOffice(stateDir, path.join(sandbox, 'home-second'));
    expect(await second.exited).toBe(2);
    expect(second.output()).toMatch(/refusing to start: a browser access file already exists at /);
    expect(second.output()).toContain(first.file);
    expect(fs.readFileSync(first.file, 'utf-8')).toBe(before);
    expect(await httpStatus(first.port)).toBe(200);
    first.child.kill('SIGTERM');
    expect(await first.exited).toBe(0);
    expect(fs.existsSync(first.file)).toBe(false);
  });

  it("one launch's shutdown leaves ANOTHER launch's file in place", async () => {
    const stateDir = path.join(sandbox, 'di-state');
    const first = await startOffice(stateDir);
    fs.rmSync(first.file); // first's file went away; a new launch publishes at the same path
    const second = await startOffice(stateDir);
    const secondContent = fs.readFileSync(second.file, 'utf-8');
    first.child.kill('SIGTERM');
    expect(await first.exited).toBe(0);
    expect(first.output()).toContain("is no longer this launch's file; left in place");
    expect(fs.readFileSync(second.file, 'utf-8')).toBe(secondContent);
    expect(await httpStatus(second.port)).toBe(200);
    second.child.kill('SIGTERM');
    expect(await second.exited).toBe(0);
    expect(fs.existsSync(second.file)).toBe(false);
  });

  it('repeated signals are a no-op: one drain, one stop, one removal, exit 0', async () => {
    const stateDir = path.join(sandbox, 'di-state');
    const office = await startOffice(stateDir);
    office.child.kill('SIGTERM');
    office.child.kill('SIGINT');
    office.child.kill('SIGTERM');
    expect(await office.exited).toBe(0);
    const out = office.output();
    const count = (s: string): number => out.split(s).length - 1;
    expect(count('shutdown: server closed, in-flight requests drained')).toBe(1);
    expect(count('shutdown: office stopped')).toBe(1);
    expect(count('shutdown: browser access file removed')).toBe(1);
  });

  it('an open authenticated socket is closed by the drain while the access file still exists', async () => {
    const stateDir = path.join(sandbox, 'di-state');
    const office = await startOffice(stateDir);
    const { token } = JSON.parse(fs.readFileSync(office.file, 'utf-8')) as { token: string };
    const socket = new WebSocket(`ws://127.0.0.1:${office.port}/ws?token=${token}`);
    const firstState = new Promise<Record<string, unknown>>((resolve) => {
      socket.on('message', (d) => {
        const m = JSON.parse(d.toString()) as Record<string, unknown>;
        if (m.type === 'diOfficeState') resolve(m);
      });
    });
    socket.on('open', () => socket.send(JSON.stringify({ type: 'webviewReady' })));
    const answered = await firstState; // answered by a LIVE office
    expect(answered.notLiveReason).toBe('discovery:discovery_absent');
    const fileAtClose = new Promise<boolean>((resolve) =>
      socket.on('close', () => resolve(fs.existsSync(office.file))),
    );
    office.child.kill('SIGTERM');
    expect(await fileAtClose).toBe(true); // drained BEFORE unpublish: the file outlived the socket
    expect(await office.exited).toBe(0);
    expect(fs.existsSync(office.file)).toBe(false);
  });
});
