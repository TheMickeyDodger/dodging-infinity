/**
 * ADDENDUM 1 — DI observation data itself requires authentication, proven against the
 * ACTUAL transport: the built `dist/di-cli.js` is spawned with a FAKE HOME (sandbox)
 * and probed over real HTTP and WebSocket connections. Run `node esbuild.js` first.
 *
 * The backend discovery path points into the sandbox and does not exist, so the office
 * runs in its honest "unavailable" state; nothing here starts or talks to a backend.
 */
import type { ChildProcess } from 'child_process';
import { spawn } from 'child_process';
import * as fs from 'fs';
import * as http from 'http';
import * as os from 'os';
import * as path from 'path';
import { afterAll, beforeAll, describe, expect, it } from 'vitest';
import { WebSocket } from 'ws';

import {
  DI_CATALOG_WAIT_SECONDS,
  DI_WS_MAX_PAYLOAD_BYTES,
  WS_CLOSE_UNAUTHORIZED,
} from '../src/constants.js';
import { browserAccessPath } from '../src/di/browserAccess.js';

const DI_BUNDLE = path.join(__dirname, '../../dist/di-cli.js');
const START_TIMEOUT_MS = 10_000;
const SETTLE_MS = 600;

let sandbox: string;
let fakeHome: string;
let stateDir: string;
let child: ChildProcess;
let output = '';
let port = 0;
let token = '';
let accessFile = '';

function treeOf(root: string): string[] {
  const out: string[] = [];
  const walk = (dir: string): void => {
    for (const e of fs.readdirSync(dir, { withFileTypes: true })) {
      const full = path.join(dir, e.name);
      out.push(path.relative(root, full));
      if (e.isDirectory()) walk(full);
    }
  };
  if (fs.existsSync(root)) walk(root);
  return out.sort();
}

interface Probe {
  /** The server kept the socket open past the settle window. */
  accepted: boolean;
  closeCode?: number;
  /** HTTP status when the upgrade itself was refused. */
  upgradeStatus?: number;
  messages: Array<Record<string, unknown>>;
}

/** Connect, send webviewReady once open, and collect EVERY message the server sends. */
function probe(query: string, headers: Record<string, string> = {}): Promise<Probe> {
  return new Promise((resolve) => {
    const result: Probe = { accepted: false, messages: [] };
    const socket = new WebSocket(`ws://127.0.0.1:${port}/ws${query}`, { headers });
    socket.on('unexpected-response', (_req, res) => {
      result.upgradeStatus = res.statusCode;
      resolve(result);
    });
    socket.on('error', () => resolve(result));
    socket.on('message', (data) => {
      try {
        result.messages.push(JSON.parse(data.toString()) as Record<string, unknown>);
      } catch {
        /* ignore */
      }
    });
    socket.on('open', () => {
      socket.send(JSON.stringify({ type: 'webviewReady' }));
    });
    socket.on('close', (code) => {
      result.closeCode = code;
    });
    setTimeout(() => {
      result.accepted = socket.readyState === WebSocket.OPEN;
      socket.terminate();
      resolve(result);
    }, SETTLE_MS);
  });
}

function httpGet(
  pathname: string,
  headers: Record<string, string> = {},
  method = 'GET',
): Promise<{ status: number; headers: http.IncomingHttpHeaders; body: string }> {
  return new Promise((resolve, reject) => {
    const req = http.request(
      {
        host: '127.0.0.1',
        port,
        path: pathname,
        method,
        headers: { Host: `127.0.0.1:${port}`, ...headers },
      },
      (res) => {
        let body = '';
        res.on('data', (c: Buffer) => (body += c.toString()));
        res.on('end', () => resolve({ status: res.statusCode ?? 0, headers: res.headers, body }));
      },
    );
    req.on('error', reject);
    req.end();
  });
}

function runToExit(args: string[], home: string): Promise<{ code: number | null; out: string }> {
  return new Promise((resolve) => {
    const proc = spawn(process.execPath, [DI_BUNDLE, ...args], {
      env: { ...process.env, HOME: home, USERPROFILE: home, PIXEL_AGENTS_DEBUG_LOG: '' },
      stdio: ['ignore', 'pipe', 'pipe'],
    });
    let out = '';
    proc.stdout.on('data', (c: Buffer) => (out += c.toString()));
    proc.stderr.on('data', (c: Buffer) => (out += c.toString()));
    proc.on('exit', (code) => resolve({ code, out }));
  });
}

beforeAll(async () => {
  if (!fs.existsSync(DI_BUNDLE))
    throw new Error('dist/di-cli.js missing: run `node esbuild.js` first');
  sandbox = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), 'di-transport-')));
  fakeHome = path.join(sandbox, 'home');
  fs.mkdirSync(fakeHome);
  stateDir = path.join(sandbox, 'di-state');
  const discovery = path.join(sandbox, 'runtime', 'backend-discovery.json'); // absent on purpose
  child = spawn(
    process.execPath,
    [DI_BUNDLE, '--state-dir', stateDir, '--backend-discovery', discovery],
    {
      env: { ...process.env, HOME: fakeHome, USERPROFILE: fakeHome, PIXEL_AGENTS_DEBUG_LOG: '' },
      stdio: ['ignore', 'pipe', 'pipe'],
    },
  );
  child.stdout?.on('data', (c: Buffer) => (output += c.toString()));
  child.stderr?.on('data', (c: Buffer) => (output += c.toString()));
  const deadline = Date.now() + START_TIMEOUT_MS;
  while (Date.now() < deadline) {
    const listening = /DI office listening on http:\/\/127\.0\.0\.1:(\d+)\/ /.exec(output);
    const stored = /Open the URL stored in (\S+)/.exec(output);
    if (listening && stored && fs.existsSync(stored[1])) {
      const access = JSON.parse(fs.readFileSync(stored[1], 'utf-8')) as {
        port: number;
        token: string;
      };
      port = Number(listening[1]);
      token = access.token;
      accessFile = stored[1];
      return;
    }
    if (child.exitCode !== null) throw new Error(`DI office exited:\n${output}`);
    await new Promise((r) => setTimeout(r, 50));
  }
  throw new Error(`DI office did not start:\n${output}`);
});

afterAll(async () => {
  if (child && child.exitCode === null) {
    child.kill('SIGTERM');
    await new Promise((r) => setTimeout(r, 300));
  }
  if (sandbox) fs.rmSync(sandbox, { recursive: true, force: true });
});

describe('FIRST-CONNECTION SNAPSHOT GUARANTEE (round-2 blocker 1)', () => {
  it('the FIRST message on every authenticated connection is the complete office state, before any broadcast and before webviewReady', async () => {
    const first = await new Promise<Record<string, unknown>>((resolve, reject) => {
      const socket = new WebSocket(`ws://127.0.0.1:${port}/ws?token=${token}`);
      const timer = setTimeout(() => reject(new Error('no first message')), 5_000);
      socket.once('message', (d) => {
        clearTimeout(timer);
        socket.terminate();
        resolve(JSON.parse(d.toString()) as Record<string, unknown>);
      });
      socket.on('error', reject);
    });
    expect(first.type).toBe('diOfficeState');
    expect(first).toHaveProperty('missions');
    expect(first).toHaveProperty('envelope');
    expect(first).toHaveProperty('runtime');
  });
});

describe('ADDENDUM 1: the WebSocket data path requires the token — unauthenticated sockets get NOTHING', () => {
  it('no token: closed unauthorized, zero messages (not even after sending webviewReady)', async () => {
    const r = await probe('');
    expect(r.accepted).toBe(false);
    expect(r.closeCode).toBe(WS_CLOSE_UNAUTHORIZED);
    expect(r.messages).toEqual([]);
  });

  it('wrong token: closed unauthorized, zero messages', async () => {
    const r = await probe('?token=not-the-token');
    expect(r.accepted).toBe(false);
    expect(r.closeCode).toBe(WS_CLOSE_UNAUTHORIZED);
    expect(r.messages).toEqual([]);
  });

  it('right token + WRONG Host: refused at the HTTP layer (403), zero messages', async () => {
    const r = await probe(`?token=${token}`, { Host: `evil.example:${port}` });
    expect(r.accepted).toBe(false);
    expect(r.upgradeStatus).toBe(403);
    expect(r.messages).toEqual([]);
  });

  it('right token + DNS-rebound Host/Origin pair: refused (403), zero messages', async () => {
    const forged = `evil.example:${port}`;
    const r = await probe(`?token=${token}`, { Host: forged, Origin: `http://${forged}` });
    expect(r.upgradeStatus).toBe(403);
    expect(r.messages).toEqual([]);
  });

  it('right token + FOREIGN Origin: refused (403), zero messages', async () => {
    const r = await probe(`?token=${token}`, { Origin: 'http://evil.example' });
    expect(r.upgradeStatus).toBe(403);
    expect(r.messages).toEqual([]);
  });

  it('right token, loopback Host, no Origin: accepted and served office state — no hooks, no consent', async () => {
    const r = await probe(`?token=${token}`);
    expect(r.accepted).toBe(true);
    const types = r.messages.map((m) => m.type);
    expect(types).toContain('settingsLoaded');
    expect(types).toContain('layoutLoaded');
    expect(types).not.toContain('hooksStatus');
    expect(types).not.toContain('hooksConsentRequest');
    // The additive DI office state reaches an AUTHENTICATED client only.
    const office = r.messages.find((m) => m.type === 'diOfficeState');
    expect(office).toMatchObject({
      type: 'diOfficeState',
      live: false,
      notLiveReason: 'discovery:discovery_absent',
      coverage: { scope: 'direct_receipt_task', linkedChildRoles: 'not_covered' },
      missions: { state: 'unavailable', reason: 'discovery:discovery_absent', missions: [] },
    });
  });

  it('right token, localhost Host + same-origin Origin: accepted', async () => {
    const r = await probe(`?token=${token}`, {
      Host: `localhost:${port}`,
      Origin: `http://localhost:${port}`,
    });
    expect(r.accepted).toBe(true);
  });
});

describe('HTTP surface: one enumerated static exception, no data routes, no CORS', () => {
  it('GET / (loopback Host) serves the static SPA shell — the same bytes as dist/webview/index.html', async () => {
    const res = await httpGet('/');
    expect(res.status).toBe(200);
    expect(res.body).toBe(
      fs.readFileSync(path.join(__dirname, '../../dist/webview/index.html'), 'utf-8'),
    );
    expect(res.headers['referrer-policy']).toBe('no-referrer');
  });

  it('wrong Host on a static path: 403', async () => {
    expect((await httpGet('/', { Host: `evil.example:${port}` })).status).toBe(403);
  });

  it('no hook ingress, no health route, no history fallback', async () => {
    expect((await httpGet('/api/hooks/claude', {}, 'POST')).status).toBe(404);
    expect((await httpGet('/api/health')).status).toBe(404);
    expect((await httpGet('/some/spa/route')).status).toBe(404);
  });

  it('no CORS headers, ever', async () => {
    const same = await httpGet('/', { Origin: `http://127.0.0.1:${port}` });
    expect(same.headers['access-control-allow-origin']).toBeUndefined();
    const preflight = await httpGet('/', { Origin: 'http://evil.example' }, 'OPTIONS');
    expect(preflight.status).toBe(403);
    expect(preflight.headers['access-control-allow-origin']).toBeUndefined();
  });
});

describe('inert by construction over the wire, and no writes outside the DI root', () => {
  it('forbidden messages from an AUTHENTICATED socket do nothing; office prefs land only under the DI root', async () => {
    await new Promise<void>((resolve) => {
      const socket = new WebSocket(`ws://127.0.0.1:${port}/ws?token=${token}`);
      socket.on('open', () => {
        for (const msg of [
          { type: 'setHooksEnabled', providerId: 'claude', enabled: true },
          { type: 'hooksConsentResponse', providerId: 'claude', choice: 'install' },
          { type: 'launchAgent', bypassPermissions: true },
          { type: 'closeAgent', id: 1 },
          { type: 'focusAgent', id: 1 },
          { type: 'setWatchAllSessions', enabled: true },
          { type: 'openSessionsFolder' },
          { type: 'addExternalAssetDirectory', path: fakeHome },
          {
            type: 'saveLayout',
            layout: { version: 1, cols: 1, rows: 1, tiles: [0], furniture: [] },
          },
          { type: 'setSoundEnabled', enabled: false },
        ]) {
          socket.send(JSON.stringify(msg));
        }
        setTimeout(() => {
          socket.close();
          resolve();
        }, SETTLE_MS);
      });
    });
    expect(treeOf(fakeHome)).toEqual([]); // no ~/.pixel-agents, no ~/.claude — nothing
    // config + layout from the office, plus the owner-only browser access file (run/).
    expect(treeOf(stateDir)).toEqual([
      'config.json',
      'layout.json',
      'run',
      path.join('run', 'browser-access.json'),
    ]);
    const config = JSON.parse(
      fs.readFileSync(path.join(stateDir, 'config.json'), 'utf-8'),
    ) as Record<string, unknown>;
    expect(config.externalAssetDirectories).toEqual([]); // the add was inert
    expect(config.hooksConsent).toEqual({});
  });

  it('an OVERSIZED inbound frame closes the socket (1009) unparsed: the layout on disk is unchanged', async () => {
    const layoutPath = path.join(stateDir, 'layout.json');
    const before = fs.existsSync(layoutPath) ? fs.readFileSync(layoutPath, 'utf-8') : null;
    const code = await new Promise<number>((resolve) => {
      const socket = new WebSocket(`ws://127.0.0.1:${port}/ws?token=${token}`);
      socket.on('open', () => {
        socket.send(
          JSON.stringify({
            type: 'saveLayout',
            layout: {
              version: 1,
              cols: 1,
              rows: 1,
              tiles: [1],
              furniture: [],
              pad: 'x'.repeat(DI_WS_MAX_PAYLOAD_BYTES),
            },
          }),
        );
      });
      socket.on('close', (closeCode) => resolve(closeCode));
    });
    expect(code).toBe(1009);
    await new Promise((r) => setTimeout(r, SETTLE_MS));
    const after = fs.existsSync(layoutPath) ? fs.readFileSync(layoutPath, 'utf-8') : null;
    expect(after).toBe(before);
  });

  it('the office token NEVER appears in captured stdout/stderr, even after tokened requests', async () => {
    await httpGet(`/?token=${token}`);
    await probe(`?token=${token}`);
    expect(token).toMatch(/^[0-9a-f-]{36}$/); // a real value, so the next line is meaningful
    expect(output).not.toContain(token); // matched on the VALUE, not on a URL shape
    expect(output).toContain(`listening on http://127.0.0.1:${port}/ (loopback only)`);
    expect(output).toContain(`Open the URL stored in ${accessFile}`);
  });

  it('publishes the openable URL owner-only in the isolated state root (0600 file, 0700 dir)', () => {
    expect(accessFile).toBe(browserAccessPath(stateDir));
    expect(fs.statSync(accessFile).mode & 0o777).toBe(0o600);
    expect(fs.statSync(path.dirname(accessFile)).mode & 0o777).toBe(0o700);
    const access = JSON.parse(fs.readFileSync(accessFile, 'utf-8')) as Record<string, unknown>;
    expect(Object.keys(access).sort()).toEqual(['port', 'token', 'url']);
    expect(access).toEqual({ url: `http://127.0.0.1:${port}/?token=${token}`, port, token });
  });

  it('reports the absent backend discovery file as an honest unavailable state', () => {
    expect(output).toContain('not live (discovery:discovery_absent)');
    expect(output).toContain('coverage=direct_receipt_task');
  });
});

describe('the state-root guard fails closed at the CLI (fake HOME, sandbox only)', () => {
  it('refuses the shared profile, an alias of it, and an ancestor of it — creating nothing', async () => {
    const home = path.join(sandbox, 'guard-home');
    fs.mkdirSync(path.join(home, '.pixel-agents'), { recursive: true });
    const alias = path.join(sandbox, 'guard-alias');
    fs.symlinkSync(path.join(home, '.pixel-agents'), alias);
    const discovery = path.join(sandbox, 'runtime', 'backend-discovery.json');
    for (const candidate of [
      path.join(home, '.pixel-agents'),
      alias,
      home,
      path.join(alias, 'nested'),
    ]) {
      const { code, out } = await runToExit(
        ['--state-dir', candidate, '--backend-discovery', discovery],
        home,
      );
      expect(code).toBe(2);
      expect(out).toMatch(/refusing to start: the DI state root resolves/);
    }
    expect(treeOf(path.join(home, '.pixel-agents'))).toEqual([]);
  });

  it('refuses --host, and refuses --catalog-wait-seconds because the wait is pinned policy', async () => {
    const discovery = path.join(sandbox, 'runtime', 'backend-discovery.json');
    const base = ['--state-dir', path.join(sandbox, 'x'), '--backend-discovery', discovery];
    expect((await runToExit([...base, '--host', '0.0.0.0'], fakeHome)).code).toBe(2);
    const waitOverride = await runToExit(
      [...base, '--catalog-wait-seconds', String(DI_CATALOG_WAIT_SECONDS)],
      fakeHome,
    );
    expect(waitOverride.code).toBe(2);
    expect(waitOverride.out).toMatch(/pinned consumer policy/);
  });
});
