/**
 * The CLI flag that wires the office delivery adapter: its argument, and the relay the office composes with it.
 *
 * The client MODULE is mocked here, so no request can open a socket. What this shows is that the composed relay hands the
 * discovered endpoint and the call to the client's `requestDelivery`, and answers when nothing is configured or the
 * discovery file is absent. It does NOT show the client reaching a real adapter. main()'s one dispatcher-context line is
 * not executed here, because main starts a server.
 */
import * as fs from 'fs';
import * as os from 'os';
import * as path from 'path';
import { afterEach, describe, expect, it, vi } from 'vitest';

import type { DiCliArgs } from '../src/di/diCli.js';
import * as diCli from '../src/di/diCli.js';

const { calls } = vi.hoisted(() => ({ calls: [] as Array<{ endpoint: unknown; call: unknown }> }));
vi.mock('../src/di/deliveryClient.js', () => ({
  requestDelivery: async (endpoint: unknown, call: unknown) => {
    calls.push({ endpoint, call });
    return { kind: 'http', status: 200, body: { ok: true, status: { step: 'COMMIT' } } };
  },
}));

const PORT = 47124;
const TOKEN = `office-delivery-token-${'e'.repeat(40)}`;
const ID = `prd-${'b'.repeat(24)}`;
const STATUS_MESSAGE = { type: 'diDeliveryRequest', seq: 3, op: 'status', deliveryId: ID };
const made: string[] = [];

type Relay = { handle: (msg: Record<string, unknown>, send: (m: Record<string, unknown>) => void) => Promise<void> };
const factory = (): ((discovery: string | null, log?: (line: string) => void) => Relay) | undefined =>
  (diCli as Record<string, unknown>).deliveryRelay as ((discovery: string | null, log?: (line: string) => void) => Relay) | undefined;

afterEach(() => {
  calls.length = 0;
  // Only the directories this file made, by their exact paths, directly under the temp root.
  for (const dir of made.splice(0)) {
    if (path.dirname(dir) === os.tmpdir() && /^di-delivery-wiring-[A-Za-z0-9]{6}$/.test(path.basename(dir))) {
      fs.rmSync(dir, { recursive: true, force: true });
    }
  }
});

describe('--delivery-adapter-discovery', () => {
  it('takes an absolute file, normalized, with the backend or with a fixture source; absent, it is undefined', () => {
    const backendArgv = [
      '--state-dir',
      '/abs/state',
      '--backend-discovery',
      '/abs/d.json',
      '--delivery-adapter-discovery',
      '/abs/run/../run/delivery.json',
    ];
    const fixtureArgv = [
      '--state-dir',
      '/abs/state',
      '--fixture-source',
      '/abs/f.json',
      '--delivery-adapter-discovery',
      '/abs/delivery.json',
    ];
    expect(() => diCli.parseDiArgs(backendArgv)).not.toThrow();
    expect(() => diCli.parseDiArgs(fixtureArgv)).not.toThrow();
    expect(diCli.parseDiArgs(backendArgv)).toMatchObject({ deliveryAdapterDiscovery: '/abs/run/delivery.json' });
    expect(diCli.parseDiArgs(fixtureArgv)).toMatchObject({
      deliveryAdapterDiscovery: '/abs/delivery.json',
      fixtureSource: '/abs/f.json',
    });
    const without = diCli.parseDiArgs(['--state-dir', '/abs/state', '--backend-discovery', '/abs/d.json']) as DiCliArgs;
    expect(without.deliveryAdapterDiscovery).toBeUndefined();
  });

  it('refuses a relative path and a missing value, by name', () => {
    const base = ['--state-dir', '/abs/state', '--backend-discovery', '/abs/d.json'];
    expect(() => diCli.parseDiArgs([...base, '--delivery-adapter-discovery', 'relative.json'])).toThrow(
      /--delivery-adapter-discovery must be an absolute path/,
    );
    expect(() => diCli.parseDiArgs([...base, '--delivery-adapter-discovery'])).toThrow(
      /Missing value for --delivery-adapter-discovery/,
    );
  });
});

describe('the relay the office composes with it', () => {
  it('with no discovery file configured, it answers "not configured" and makes no request', async () => {
    const make = factory();
    expect(typeof make).toBe('function');
    const sent: Array<Record<string, unknown>> = [];
    await make!(null, () => undefined).handle(STATUS_MESSAGE, (m) => void sent.push(m));
    expect(sent).toEqual([
      expect.objectContaining({ seq: 3, op: 'status', state: 'unavailable', problem: 'delivery_not_configured' }),
    ]);
    expect(calls).toEqual([]);
  });

  it('with its discovery file absent, it answers "discovery unavailable" and makes no request', async () => {
    const make = factory();
    expect(typeof make).toBe('function');
    const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'di-delivery-wiring-'));
    made.push(dir);
    const sent: Array<Record<string, unknown>> = [];
    await make!(path.join(dir, 'missing.json'), () => undefined).handle(STATUS_MESSAGE, (m) => void sent.push(m));
    expect(sent).toEqual([
      expect.objectContaining({ seq: 3, op: 'status', state: 'unavailable', problem: 'delivery_discovery_unavailable' }),
    ]);
    expect(calls).toEqual([]);
  });

  it("with a valid discovery file, it hands the client's requestDelivery exactly the discovered endpoint and the call; no reply or log carries the token", async () => {
    const make = factory();
    expect(typeof make).toBe('function');
    const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'di-delivery-wiring-'));
    made.push(dir);
    const file = path.join(dir, 'delivery.json');
    fs.writeFileSync(file, JSON.stringify({ url: `http://127.0.0.1:${PORT}`, port: PORT, token: TOKEN }), { mode: 0o600 });
    const sent: Array<Record<string, unknown>> = [];
    const logs: string[] = [];
    await make!(file, (line) => void logs.push(line)).handle(STATUS_MESSAGE, (m) => void sent.push(m));
    expect(calls).toEqual([
      { endpoint: { port: PORT, token: TOKEN }, call: { method: 'GET', path: `/deliveries/${ID}/status`, body: null } },
    ]);
    expect(sent).toEqual([expect.objectContaining({ seq: 3, op: 'status', state: 'ok' })]);
    expect(JSON.stringify(sent) + logs.join('\n')).not.toContain(TOKEN);
  });
});
