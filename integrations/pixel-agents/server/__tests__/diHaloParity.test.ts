/**
 * THE WORKING HALO (M3 S5) against the SERVER's own projection of every labelled fixture: the
 * webview's single eligibility function (webview-ui/src/office/halo.ts), fed the exact
 * diOfficeState message the office would send, glows for exactly the live busy members and for
 * nothing else — crowded, zero-agent, stale, malformed, reconnect and replacement — and expires on
 * the client's clock when no newer payload arrives.
 */
import * as path from 'path';
import { describe, expect, it, vi } from 'vitest';

import type { DiOfficeState } from '../../core/src/messages.js';
import { haloEligibleCharacterIds } from '../../webview-ui/src/office/halo.js';

vi.mock('../src/di/backendDiscovery.js', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../src/di/backendDiscovery.js')>();
  return { ...actual, readBackendDiscovery: vi.fn() };
});
vi.mock('../src/di/backendClient.js', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../src/di/backendClient.js')>();
  return { ...actual, requestBackend: vi.fn() };
});

const { AgentStateStore } = await import('../src/agentStateStore.js');
const { DiOffice } = await import('../src/di/diOffice.js');
const { createFixtureSource, readFixtureScript } = await import('../src/di/fixtureSource.js');

const FIXTURE_DIR = path.join(__dirname, '../../packaging/di/fixtures');
const NOW_MS = 1_900_000_000_000;
const NOW_S = NOW_MS / 1000;

function office(id: string) {
  const source = createFixtureSource(readFixtureScript(path.join(FIXTURE_DIR, `${id}.FIXTURE.json`)), () => NOW_MS);
  return new DiOffice({ store: new AgentStateStore(), discoveryPath: `fixture-source:${id}`, fixturePreview: true, deps: { ...source.deps, nowMs: () => NOW_MS, log: () => {} } });
}
const halo = (o: InstanceType<typeof DiOffice>) => haloEligibleCharacterIds(o.officeStateMessage() as unknown as DiOfficeState, NOW_S);

describe('halo parity with the server projection, per fixture', () => {
  it('crowded: every live busy member glows (48), exactly the live characters', async () => {
    const o = office('crowded');
    await o.poll();
    await o.poll();
    expect([...halo(o)].sort()).toEqual([...o.liveCharacters().keys()].sort());
    expect(halo(o).size).toBe(48);
  });

  it('zero-agent, stale, malformed: nothing glows', async () => {
    for (const id of ['zero-agent', 'stale', 'malformed']) {
      const o = office(id);
      for (let k = 0; k < 3; k++) await o.poll();
      expect(halo(o).size, id).toBe(0);
    }
  });

  it('reconnect: off while unreachable, back for the SAME identities after', async () => {
    const o = office('reconnect');
    await o.poll();
    await o.poll();
    const before = [...halo(o)].sort();
    expect(before.length).toBe(2);
    await o.poll();
    await o.poll();
    expect(halo(o).size).toBe(0);
    await o.poll();
    expect([...halo(o)].sort()).toEqual(before);
  });

  it('replacement: the replaced identity loses it, the new identity gains it, the kept one keeps it', async () => {
    const o = office('replacement');
    for (let k = 0; k < 3; k++) await o.poll();
    const before = new Map(o.liveCharacters());
    const glowBefore = halo(o);
    await o.poll();
    const after = new Map(o.liveCharacters());
    const glowAfter = halo(o);
    const ex1 = [...before].find(([, r]) => r.endsWith('#executor1'))![0];
    const ex2 = [...after].find(([, r]) => r.endsWith('#executor2'))![0];
    const lead = [...before].find(([, r]) => r.endsWith('#lead1'))![0];
    expect(glowBefore.has(ex1)).toBe(true);
    expect(glowAfter.has(ex1)).toBe(false);
    expect(glowAfter.has(ex2)).toBe(true);
    expect(glowAfter.has(lead)).toBe(true);
  });

  it('expiry without a newer payload: past validUntil nothing glows, on the client clock', async () => {
    const o = office('crowded');
    await o.poll();
    const msg = o.officeStateMessage() as unknown as DiOfficeState;
    const validUntil = msg.times.validUntil;
    expect(typeof validUntil).toBe('number');
    expect(haloEligibleCharacterIds(msg, validUntil as number).size).toBe(48);
    expect(haloEligibleCharacterIds(msg, (validUntil as number) + 1).size).toBe(0);
  });
});
