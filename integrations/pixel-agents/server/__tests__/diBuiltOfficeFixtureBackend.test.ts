/**
 * END-TO-END through the PRODUCTION-BUILT office (`dist/di-cli.js`), with a FIXTURE
 * backend: an in-process HTTP stand-in that serves the CONTRACT-v3 canonical fixture
 * (hash-pinned) with its timestamps shifted to "now" — a labelled FIXTURE VARIATION.
 * This proves the built office's data path and projection; it is NOT live acceptance
 * and NOT the backend. The stand-in enforces the bearer token and Host like the real
 * adapter contract says. Everything runs in a sandbox with a FAKE HOME.
 */
import type { ChildProcess } from 'child_process';
import { spawn } from 'child_process';
import * as fs from 'fs';
import * as http from 'http';
import type { AddressInfo } from 'net';
import * as os from 'os';
import * as path from 'path';
import { afterAll, beforeAll, describe, expect, it } from 'vitest';
import { WebSocket } from 'ws';

import { DI_CATALOG_WAIT_SECONDS } from '../src/constants.js';
import { clone, loadFixture } from './helpers/diFixture.js';

const DI_BUNDLE = path.join(__dirname, '../../dist/di-cli.js');
const BACKEND_TOKEN = 'fixture-backend-token-0123456789';

let sandbox: string;
let fakeHome: string;
let backend: http.Server;
let backendPort = 0;
let backendRequests: http.IncomingMessage[] = [];
let child: ChildProcess;
let output = '';
let officePort = 0;
let officeToken = '';
let accessFileText = '';

/** FIXTURE VARIATION: the pinned fixture with every timestamp moved to `now`. */
function fixtureAtNow(): Record<string, unknown> {
  const now = Math.floor(Date.now() / 1000);
  const f = clone(loadFixture());
  f.requested_at = now;
  const response = f.response as Record<string, unknown>;
  response.generated_at = now;
  response.valid_until = now + (response.max_age_seconds as number);
  (response.runtime_snapshot as Record<string, unknown>).observed_at = now;
  (f.runtime_capture as Record<string, unknown>).observed_at = now;
  for (const source of response.sources as Array<Record<string, unknown>>) source.observed_at = now;
  for (const mission of response.missions as Array<Record<string, unknown>>) {
    for (const member of mission.members as Array<Record<string, unknown>>)
      member.observed_at = now;
  }
  return f;
}

beforeAll(async () => {
  if (!fs.existsSync(DI_BUNDLE))
    throw new Error('dist/di-cli.js missing: run `node esbuild.js` first');
  sandbox = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), 'di-built-')));
  fakeHome = path.join(sandbox, 'home');
  fs.mkdirSync(fakeHome);

  backend = http.createServer((req, res) => {
    backendRequests.push(req);
    const authorized =
      req.headers.authorization === `Bearer ${BACKEND_TOKEN}` &&
      req.headers.host === `127.0.0.1:${backendPort}`;
    if (!authorized) {
      res.writeHead(401);
      res.end();
      return;
    }
    res.writeHead(200, {
      'content-type': 'application/json; charset=utf-8',
      'x-di-freshness-health': 'fresh',
    });
    res.end(JSON.stringify(fixtureAtNow()));
  });
  await new Promise<void>((r) => backend.listen(0, '127.0.0.1', () => r()));
  backendPort = (backend.address() as AddressInfo).port;

  // The discovery file the backend's runtime would write (0700 dir, 0600 file).
  const runtimeDir = path.join(sandbox, 'runtime');
  fs.mkdirSync(runtimeDir, { mode: 0o700 });
  fs.chmodSync(runtimeDir, 0o700);
  const discovery = path.join(runtimeDir, 'backend-discovery.json');
  fs.writeFileSync(
    discovery,
    JSON.stringify({
      url: `http://127.0.0.1:${backendPort}`,
      port: backendPort,
      token: BACKEND_TOKEN,
    }),
    { mode: 0o600 },
  );

  child = spawn(
    process.execPath,
    [DI_BUNDLE, '--state-dir', path.join(sandbox, 'di-state'), '--backend-discovery', discovery],
    {
      env: { ...process.env, HOME: fakeHome, USERPROFILE: fakeHome, PIXEL_AGENTS_DEBUG_LOG: '' },
      stdio: ['ignore', 'pipe', 'pipe'],
    },
  );
  child.stdout?.on('data', (c: Buffer) => (output += c.toString()));
  child.stderr?.on('data', (c: Buffer) => (output += c.toString()));
  const deadline = Date.now() + 10_000;
  while (Date.now() < deadline) {
    const listening = /DI office listening on http:\/\/127\.0\.0\.1:(\d+)\/ /.exec(output);
    const stored = /Open the URL stored in (\S+)/.exec(output);
    if (listening && stored && fs.existsSync(stored[1]) && /office: LIVE/.test(output)) {
      officePort = Number(listening[1]);
      accessFileText = fs.readFileSync(stored[1], 'utf-8');
      officeToken = (JSON.parse(accessFileText) as { token: string }).token;
      return;
    }
    if (child.exitCode !== null) throw new Error(`DI office exited:\n${output}`);
    await new Promise((r) => setTimeout(r, 50));
  }
  throw new Error(`DI office did not reach LIVE against the fixture backend:\n${output}`);
});

afterAll(async () => {
  if (child && child.exitCode === null) {
    child.kill('SIGTERM');
    await new Promise((r) => setTimeout(r, 300));
  }
  await new Promise<void>((r) => backend.close(() => r()));
  if (sandbox) fs.rmSync(sandbox, { recursive: true, force: true });
});

function collect(ms: number): Promise<Array<Record<string, unknown>>> {
  return new Promise((resolve) => {
    const messages: Array<Record<string, unknown>> = [];
    const socket = new WebSocket(`ws://127.0.0.1:${officePort}/ws?token=${officeToken}`);
    socket.on('message', (d) => messages.push(JSON.parse(d.toString()) as Record<string, unknown>));
    socket.on('open', () => socket.send(JSON.stringify({ type: 'webviewReady' })));
    setTimeout(() => {
      socket.terminate();
      resolve(messages);
    }, ms);
  });
}

describe('FIXTURE backend -> production-built DI office', () => {
  it('reads the discovery file and calls the backend with bearer + Host, GET /catalog?wait_seconds=<pinned policy>', () => {
    expect(backendRequests.length).toBeGreaterThan(0);
    const req = backendRequests[0];
    expect(req.method).toBe('GET');
    expect(req.url).toBe(`/catalog?wait_seconds=${DI_CATALOG_WAIT_SECONDS}`);
    expect(output).not.toContain(BACKEND_TOKEN); // the backend token is never echoed
  });

  it('keeps the two secrets on their own surfaces: ours never printed nor sent to the backend, theirs never in our file', () => {
    expect(officeToken).toMatch(/^[0-9a-f-]{36}$/);
    expect(output).not.toContain(officeToken);
    expect(accessFileText).not.toContain(BACKEND_TOKEN);
    for (const req of backendRequests) {
      expect(JSON.stringify({ url: req.url, headers: req.headers })).not.toContain(officeToken);
      expect(req.headers.authorization).toBe(`Bearer ${BACKEND_TOKEN}`);
    }
  });

  it('an authenticated client receives a LIVE diOfficeState and the live member as a character', async () => {
    const messages = await collect(800);
    const state = messages.find((m) => m.type === 'diOfficeState') as Record<string, unknown> & {
      missions: { missions: Array<Record<string, unknown>> };
    };
    expect(state).toBeDefined();
    expect(state.live).toBe(true);
    const [core, legacy] = state.missions.missions;
    expect(core.missionRef).toBe('synthetic-core:mn-1714199978b50a9d93349749406a85a7');
    expect(core.live).toBe(true);
    expect(legacy.live).toBe(false);
    const members = (core.members as { members: Array<Record<string, unknown>> }).members;
    const characterId = members[0].characterId as number;
    expect(typeof characterId).toBe('number');
    const created = messages.filter((m) => m.type === 'existingAgents')[0] as { agents: number[] };
    expect(created.agents).toContain(characterId);
  });

  it('wrote nothing outside the DI root (fake HOME stays empty)', () => {
    expect(fs.readdirSync(fakeHome)).toEqual([]);
  });
});
