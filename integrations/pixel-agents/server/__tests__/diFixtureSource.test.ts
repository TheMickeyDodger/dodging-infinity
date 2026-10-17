/**
 * --fixture-source: the six labelled FIXTURE scripts in packaging/di/fixtures/, served
 * in-process through the ordinary DiOffice consumer. SYNTHETIC fixture evidence only.
 *
 * Pins: each fixture's projected outcome; that a fixture office reads no discovery file and
 * makes no backend request (the real reader and client are never called); argument rules
 * (--fixture-source is exclusive with --backend-discovery and implies fixture labelling); and
 * that malformed or unlabelled scripts are refused before launch.
 */
import * as fs from 'fs';
import * as os from 'os';
import * as path from 'path';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const realDiscovery = vi.hoisted(() => vi.fn());
const realRequest = vi.hoisted(() => vi.fn());
vi.mock('../src/di/backendDiscovery.js', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../src/di/backendDiscovery.js')>();
  return { ...actual, readBackendDiscovery: realDiscovery };
});
vi.mock('../src/di/backendClient.js', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../src/di/backendClient.js')>();
  return { ...actual, requestBackend: realRequest };
});

import { AgentStateStore } from '../src/agentStateStore.js';
import { DiCliArgsError, parseDiArgs } from '../src/di/diCli.js';
import { DiOffice } from '../src/di/diOffice.js';
import {
  createFixtureSource,
  FixtureSourceError,
  parseFixtureScript,
  readFixtureScript,
} from '../src/di/fixtureSource.js';

const FIXTURE_DIR = path.join(__dirname, '../../packaging/di/fixtures');
const NOW_MS = 1_900_000_000_000;

function fixtureOffice(id: string): {
  office: DiOffice;
  store: AgentStateStore;
  served: () => number;
} {
  const source = createFixtureSource(
    readFixtureScript(path.join(FIXTURE_DIR, `${id}.FIXTURE.json`)),
    () => NOW_MS,
  );
  const store = new AgentStateStore();
  const office = new DiOffice({
    store,
    discoveryPath: `fixture-source:${id}`,
    fixturePreview: true,
    deps: { ...source.deps, nowMs: () => NOW_MS, log: () => {} },
  });
  return { office, store, served: source.served };
}

async function polls(office: DiOffice, n: number): Promise<number[]> {
  const counts: number[] = [];
  for (let i = 0; i < n; i++) {
    await office.poll();
    counts.push(office.liveCharacters().size);
  }
  return counts;
}

beforeEach(() => {
  realDiscovery.mockReset();
  realRequest.mockReset();
});
afterEach(() => {
  expect(realDiscovery).not.toHaveBeenCalled();
  expect(realRequest).not.toHaveBeenCalled();
});

describe('fixture scripts', () => {
  it('all six exist, are labelled FIXTURE, and validate', () => {
    for (const id of ['zero-agent', 'crowded', 'stale', 'malformed', 'replacement', 'reconnect']) {
      const script = readFixtureScript(path.join(FIXTURE_DIR, `${id}.FIXTURE.json`));
      expect(script.fixtureId).toBe(id);
      expect(script.label.startsWith('FIXTURE')).toBe(true);
    }
  });

  it('zero-agent: LIVE, zero missions, zero characters, labelled as fixture', async () => {
    const { office } = fixtureOffice('zero-agent');
    expect(await polls(office, 3)).toEqual([0, 0, 0]);
    expect(office.getView().live).toBe(true);
    const message = office.officeStateMessage();
    expect(message.fixtureMode).toBe(true);
  });

  it('crowded: 48 live synthetic members become 48 characters', async () => {
    const { office } = fixtureOffice('crowded');
    expect(await polls(office, 2)).toEqual([48, 48]);
    expect(office.getView().live).toBe(true);
  });

  it('stale: nothing is live and no character appears', async () => {
    const { office } = fixtureOffice('stale');
    expect(await polls(office, 2)).toEqual([0, 0]);
    expect(office.getView().live).toBe(false);
  });

  it('malformed: refused into an honest unavailable state', async () => {
    const { office } = fixtureOffice('malformed');
    expect(await polls(office, 4)).toEqual([0, 0, 0, 0]);
    expect(office.getView().live).toBe(false);
    expect(office.getView().notLiveReason).toBeTruthy();
  });

  it('replacement: executor1 goes, a NEW identity arrives, lead1 keeps its own', async () => {
    const { office } = fixtureOffice('replacement');
    await polls(office, 3);
    const before = new Map(office.liveCharacters());
    expect(before.size).toBe(2);
    await office.poll();
    const after = new Map(office.liveCharacters());
    expect(after.size).toBe(2);
    const refsBefore = [...before.values()].sort();
    const refsAfter = [...after.values()].sort();
    expect(refsBefore.some((r) => r.endsWith('#executor1'))).toBe(true);
    expect(refsAfter.some((r) => r.endsWith('#executor1'))).toBe(false);
    expect(refsAfter.some((r) => r.endsWith('#executor2'))).toBe(true);
    const lead = [...before].find(([, r]) => r.endsWith('#lead1'));
    expect(lead && after.get(lead[0])).toBe(lead?.[1]);
    const executor2 = [...after].find(([, r]) => r.endsWith('#executor2'));
    expect(executor2 && before.has(executor2[0])).toBe(false);
  });

  it('reconnect: characters vanish while unreachable and the SAME identities return', async () => {
    const { office } = fixtureOffice('reconnect');
    const counts = await polls(office, 2);
    expect(counts).toEqual([2, 2]);
    const ids = [...office.liveCharacters().keys()].sort();
    expect(await polls(office, 2)).toEqual([0, 0]);
    expect(office.getView().live).toBe(false);
    expect(await polls(office, 1)).toEqual([2]);
    expect([...office.liveCharacters().keys()].sort()).toEqual(ids);
  });

  it('a fixture office started through its loop never calls the real reader or client', async () => {
    vi.useFakeTimers();
    try {
      const { office, served } = fixtureOffice('zero-agent');
      office.start();
      await vi.advanceTimersByTimeAsync(20_000);
      office.stop();
      expect(served()).toBeGreaterThan(1);
    } finally {
      vi.useRealTimers();
    }
  });
});

describe('fixture script validation', () => {
  const base = {
    fixture: true,
    fixture_id: 'x',
    label: 'FIXTURE x',
    frames: [{ kind: 'body_not_json' }],
  };
  it('accepts a minimal labelled script', () => {
    expect(parseFixtureScript(base).frames).toHaveLength(1);
  });
  it.each([
    ['not marked as a fixture', { ...base, fixture: false }],
    ['unlabelled', { ...base, label: 'Real data' }],
    ['no frames', { ...base, frames: [] }],
    ['unknown frame kind', { ...base, frames: [{ kind: 'shell' }] }],
    ['unknown transport problem', { ...base, frames: [{ kind: 'transport_error', problem: 'x' }] }],
    ['repeat out of bounds', { ...base, frames: [{ kind: 'body_not_json', repeat: 1_000_000 }] }],
  ])('refuses a script that is %s', (_name, script) => {
    expect(() => parseFixtureScript(script)).toThrow(FixtureSourceError);
  });
  it('refuses an oversized file before parsing it', () => {
    const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'di-fixture-'));
    const file = path.join(dir, 'big.json');
    fs.writeFileSync(file, Buffer.alloc(4 * 1024 * 1024 + 1, 0x20));
    try {
      expect(() => readFixtureScript(file)).toThrow(/exceeds/);
    } finally {
      fs.rmSync(dir, { recursive: true, force: true });
    }
  });
});

describe('--fixture-source arguments', () => {
  it('replaces --backend-discovery and implies fixture labelling', () => {
    const args = parseDiArgs(['--state-dir', '/abs/state', '--fixture-source', '/abs/f.json']);
    expect(args).toMatchObject({ fixtureSource: '/abs/f.json', fixturePreview: true });
    expect(args === 'help' ? null : args.backendDiscovery).toBeUndefined();
  });
  it('cannot be combined with --backend-discovery', () => {
    expect(() =>
      parseDiArgs([
        '--state-dir',
        '/abs/state',
        '--fixture-source',
        '/abs/f.json',
        '--backend-discovery',
        '/abs/d.json',
      ]),
    ).toThrow(DiCliArgsError);
  });
  it('must be absolute', () => {
    expect(() =>
      parseDiArgs(['--state-dir', '/abs/state', '--fixture-source', 'relative.json']),
    ).toThrow(DiCliArgsError);
  });
  it('without it, --backend-discovery is still required', () => {
    expect(() => parseDiArgs(['--state-dir', '/abs/state'])).toThrow(DiCliArgsError);
  });
});
