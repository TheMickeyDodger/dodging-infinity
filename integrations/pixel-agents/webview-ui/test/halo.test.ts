/**
 * THE WORKING HALO (M3 S5): one eligibility function for both rendering paths, and it FAILS CLOSED.
 * The happy path (a live, busy, canonically identified member of a live Mission, inside validUntil)
 * glows; every unhappy path does not: idle, unknown or missing activity, a not-live member, Mission or
 * office, an unobserved list, a missing character id, and an absent, null, NaN or infinite expiry or
 * a non-finite client clock. Animation alone never enables it, and ambient residents never have it.
 */
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';

import { test } from 'vitest';

import type { DiOfficeState } from '../../core/src/messages.ts';
import { OfficeState } from '../src/office/engine/officeState.ts';
import { haloEligibleCharacterIds, haloIntensity } from '../src/office/halo.ts';
import type { OfficeLayout } from '../src/office/types.ts';
import { loadBundledCatalog } from './helpers/bundledCatalog.ts';

const NOW = 1_900_000_000;
type Member = Record<string, unknown>;
const member = (over: Member = {}): Member => ({ characterId: 7, live: true, animateActive: true, activity: 'busy', classification: 'present', ...over });
const office = (members: Member[], over: Record<string, unknown> = {}, missionOver: Record<string, unknown> = {}): DiOfficeState =>
  ({
    type: 'diOfficeState',
    live: true,
    times: { requestedAt: NOW - 5, generatedAt: NOW - 5, validUntil: NOW + 30, snapshotObservedAt: NOW - 5, evaluatedAt: NOW },
    missions: { state: 'observed', reason: null, missions: [{ live: true, members: { state: 'observed', reason: null, members }, ...missionOver }] },
    ...over,
  }) as unknown as DiOfficeState;
const glows = (di: DiOfficeState | null, now = NOW) => [...haloEligibleCharacterIds(di, now)];

test('happy path: a live busy identified member of a live Mission, inside validUntil, glows', () => {
  assert.deepEqual(glows(office([member()])), [7]);
  assert.deepEqual(glows(office([member()]), NOW + 30), [7], 'still inside at exactly validUntil');
});

test('activity: idle, unknown or missing activity (animateActive false or absent) never glows', () => {
  assert.deepEqual(glows(office([member({ animateActive: false, activity: 'idle' })])), []);
  assert.deepEqual(glows(office([member({ animateActive: false, activity: 'unknown' })])), []);
  assert.deepEqual(glows(office([member({ animateActive: undefined })])), []);
  assert.deepEqual(glows(office([member({ animateActive: 'true' })])), [], 'a truthy non-boolean is not true');
});

test('identity and liveness: not-live member, Mission or office, unobserved lists, missing id never glow', () => {
  assert.deepEqual(glows(office([member({ live: false })])), []);
  assert.deepEqual(glows(office([member({ characterId: null })])), []);
  assert.deepEqual(glows(office([member({ characterId: 7.5 })])), []);
  assert.deepEqual(glows(office([member()], { live: false })), [], 'stale or disconnected office');
  assert.deepEqual(glows(office([member()], {}, { live: false })), []);
  assert.deepEqual(glows(office([member()], { missions: { state: 'unavailable', reason: 'x', missions: [] } })), []);
  assert.deepEqual(glows(office([member()], {}, { members: { state: 'not_recorded', reason: null, members: [member()] } })), []);
  assert.deepEqual(glows(null), []);
});

test('FAIL CLOSED on expiry and clock: absent, null, NaN or infinite validUntil, and a non-finite clock, never glow', () => {
  const t = (validUntil: unknown) => office([member()], { times: { requestedAt: NOW, generatedAt: NOW, validUntil, snapshotObservedAt: NOW, evaluatedAt: NOW } });
  assert.deepEqual(glows(t(undefined)), [], 'absent');
  assert.deepEqual(glows(t(null)), [], 'null');
  assert.deepEqual(glows(t(Number.NaN)), [], 'NaN');
  assert.deepEqual(glows(t(Number.POSITIVE_INFINITY)), [], 'Infinity');
  assert.deepEqual(glows(t('1900000030')), [], 'a string');
  assert.deepEqual(glows(office([member()], { times: undefined })), [], 'no times at all');
  assert.deepEqual(glows(office([member()]), Number.NaN), [], 'NaN clock');
  assert.deepEqual(glows(office([member()]), Number.POSITIVE_INFINITY), [], 'infinite clock');
  assert.deepEqual(glows(office([member()]), NOW + 31), [], 'expired: no newer payload');
});

const living = (): OfficeLayout => JSON.parse(fs.readFileSync(path.join(__dirname, '..', '..', 'packaging/di/worlds/living-metropolis.json'), 'utf-8'));

test('animation alone never enables it; ambient residents never have it; the canvas and the Map read the same function', () => {
  loadBundledCatalog();
  const o = new OfficeState(living());
  o.setAmbientEnabled(true);
  o.addAgent(7);
  o.setAgentActive(7, true); // animating, typing
  o.diOffice = office([member({ animateActive: false, activity: 'idle' })]);
  assert.deepEqual([...o.haloCharacterIds(NOW)], [], 'an animating character with no fresh working observation does not glow');
  o.diOffice = office([member()]);
  assert.deepEqual([...o.haloCharacterIds(NOW)], [7]);
  o.diOffice = office([member({ characterId: 999 })]);
  assert.deepEqual([...o.haloCharacterIds(NOW)], [], 'an eligible member with no office character draws no halo');
  // ambient residents are strings and not characters: never in the set
  const ids = o.haloCharacterIds(NOW);
  for (const r of o.getAmbientResidents()) assert.equal(ids.has(r.id as unknown as number), false);
});

test('reduced motion: a steady, non-pulsing halo that is never off', () => {
  const steady = [0, 0.3, 1.7, 9].map((t) => haloIntensity(t, true));
  assert.equal(new Set(steady).size, 1);
  assert.ok(steady[0] > 0);
  const pulsing = [0, 0.5, 1].map((t) => haloIntensity(t, false));
  assert.ok(new Set(pulsing).size > 1 && Math.min(...pulsing) > 0);
});

test('PRESENTATION PATH: a socket disconnect darkens the halo IMMEDIATELY (not at validUntil); a bare reconnect keeps it dark until a NEW observation', async () => {
  const { clientInvalidationReason, nextDiConnection, presentDiOffice } = await import('../src/components/diOfficeFormat.ts');
  const server = office([member()]);
  // exactly the hook's composition: connection state -> client reason -> presented state -> halo
  const halo = (conn: { transport: string; freshSinceConnect: boolean }, now = NOW) => {
    const reason = clientInvalidationReason({
      transport: conn.transport as never,
      freshSinceConnect: conn.freshSinceConnect,
      nowSeconds: Math.floor(now),
      validUntil: server.times.validUntil,
    });
    return [...haloEligibleCharacterIds(presentDiOffice(server, reason), now)];
  };
  let conn = nextDiConnection({ transport: 'connecting', freshSinceConnect: false }, { type: 'transport', state: 'connected' });
  assert.deepEqual(halo(conn), [], 'connected but no office message yet: dark');
  conn = nextDiConnection(conn, { type: 'officeMessage' });
  assert.deepEqual(halo(conn), [7], 'a fresh observation: glows');
  conn = nextDiConnection(conn, { type: 'transport', state: 'reconnecting' });
  assert.deepEqual(halo(conn, NOW + 1), [], 'disconnected with a live state in hand: dark at once, long before validUntil');
  conn = nextDiConnection(conn, { type: 'transport', state: 'connected' });
  assert.deepEqual(halo(conn, NOW + 2), [], 'a bare reconnect restores nothing');
  conn = nextDiConnection(conn, { type: 'officeMessage' });
  assert.deepEqual(halo(conn, NOW + 3), [7], 'only a NEW observation restores it');
});

test('WIRING: the halo input (OfficeState.diOffice) is assigned only from the PRESENTED state', () => {
  const src = fs.readFileSync(path.join(__dirname, '..', 'src', 'hooks', 'useExtensionMessages.ts'), 'utf-8');
  const assignments = src.match(/\.diOffice\s*=\s*[^;]+;/g) ?? [];
  assert.deepEqual(assignments, ['.diOffice = diOfficeState;'], 'one assignment, from the presented state');
  assert.match(src, /const diOfficeState = useMemo\(\s*\(\) => presentDiOffice\(diServerState, diClientReason\)/);
});
