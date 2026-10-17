/**
 * S7 BOUNDED RESOURCE METRICS on the SHIPPED living metropolis (M3): each test both MEASURES a
 * figure (printed as one `[S7]` JSON line for the budget register) and FAILS when its bound is
 * exceeded. What each measures and cannot attribute is stated per test.
 */
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';

import { PNG } from 'pngjs';
import { afterEach, beforeAll, test, vi } from 'vitest';

import { AMBIENT_SAVE_MAX_BYTES, ambientSaveBytes, checkAmbientSave } from '../../core/src/ambientSave.ts';
import { AMBIENT_PATH_SEARCHES_PER_TICK, AMBIENT_POPULATION_CAP, AMBIENT_ROUTINE_MAX_STOPS, WORLD_MAP_REFRESH_MS, WORLD_MAP_REFRESH_REDUCED_MS } from '../src/constants.ts';
import { serializeAmbient } from '../src/office/ambient/ambientSave.ts';
import { buildAmbientWorld } from '../src/office/ambient/ambientWorld.ts';
import { OfficeState } from '../src/office/engine/officeState.ts';
import { getCatalogEntry } from '../src/office/layout/furnitureCatalog.ts';
import type { FootprintOf } from '../src/office/layout/worldViews.ts';
import { livingRegions } from '../src/office/layout/worldViews.ts';
import { startMapTicker } from '../src/office/mapCadence.ts';
import type { OfficeLayout } from '../src/office/types.ts';
import { loadBundledCatalog } from './helpers/bundledCatalog.ts';

const ROOT = path.join(__dirname, '..', '..');
const report = (k: string, v: unknown) => console.log(`[S7] ${JSON.stringify({ [k]: v })}`);
const footprintOf: FootprintOf = (type) => {
  const e = getCatalogEntry(type);
  return e ? { w: e.footprintW, h: e.footprintH, seat: e.category === 'chairs' } : null;
};
let os: OfficeState;
beforeAll(() => {
  loadBundledCatalog();
  os = new OfficeState(JSON.parse(fs.readFileSync(path.join(ROOT, 'packaging/di/worlds/living-metropolis.json'), 'utf-8')) as OfficeLayout);
  os.setAmbientEnabled(true);
}, 300_000);
afterEach(() => vi.useRealTimers());

test('MAP CADENCE: one draw at mount, then one per 500 ms (2,000 ms reduced motion), never faster; WorldMap draws only through this ticker', () => {
  vi.useFakeTimers();
  for (const [reduced, ms] of [[false, WORLD_MAP_REFRESH_MS], [true, WORLD_MAP_REFRESH_REDUCED_MS]] as const) {
    let draws = 0;
    const stop = startMapTicker(() => draws++, reduced);
    vi.advanceTimersByTime(10_000);
    assert.equal(draws, 1 + 10_000 / ms, `reduced=${reduced}`);
    stop();
    vi.advanceTimersByTime(10_000);
    assert.equal(draws, 1 + 10_000 / ms, 'stopped means stopped');
  }
  // WIRING (a source check: it shows the Map has no OTHER redraw timer, not a runtime frame count)
  const src = fs.readFileSync(path.join(__dirname, '..', 'src', 'components', 'WorldMap.tsx'), 'utf-8');
  assert.equal(src.match(/startMapTicker\(/g)?.length, 1);
  assert.equal(/setInterval\(|requestAnimationFrame\(/.test(src), false);
  report('map.cadence', { normalMs: WORLD_MAP_REFRESH_MS, reducedMs: WORLD_MAP_REFRESH_REDUCED_MS, drawsPer10s: { normal: 21, reduced: 6 } });
});

test('GRAPH ROUTE WORK: at most 4 searches per tick; hubs settled per query never exceed the hub count (measured over 300 simulated s)', () => {
  const w = os.ambient!;
  const hubs = w.graph.stats.hubs;
  let maxSearchesPerTick = 0;
  for (let k = 0; k < 3000; k++) {
    const before = w.stats.searches;
    os.update(0.1);
    maxSearchesPerTick = Math.max(maxSearchesPerTick, w.stats.searches - before);
  }
  assert.ok(maxSearchesPerTick <= AMBIENT_PATH_SEARCHES_PER_TICK, `searches per tick ${maxSearchesPerTick}`);
  assert.ok(w.stats.maxHubsSettled <= hubs, `hubs settled ${w.stats.maxHubsSettled} of ${hubs}`);
  assert.ok(w.stats.graphRoutes > 0, 'graph routes were exercised');
  report('path.graph_route', {
    hubs,
    edges: w.graph.stats.edges,
    graphRoutes: w.stats.graphRoutes,
    maxHubsSettledPerQuery: w.stats.maxHubsSettled,
    maxSearchesPerTick,
    perQueryScratchBytes: hubs * (8 + 4 + 4 + 1),
    worstHubsSettledPerTick: AMBIENT_PATH_SEARCHES_PER_TICK * hubs,
  });
}, 180_000);

test('ROUTINE LENGTHS (actual): every routine is within the 10-stop bound; the distribution is measured', () => {
  const lengths = os.getAmbientResidents().map((r) => r.routine.length);
  assert.ok(Math.max(...lengths) <= AMBIENT_ROUTINE_MAX_STOPS);
  report('routine.max_stops', { residents: lengths.length, longest: Math.max(...lengths), shortest: Math.min(...lengths), mean: Math.round((lengths.reduce((a, b) => a + b, 0) / lengths.length) * 100) / 100 });
});

test('SAVE BYTES of the REAL world: ordinary and at the population cap (with its real ids), both admissible and under the ceiling', () => {
  const ordinary = serializeAmbient(os.layout, os.ambient!, os.transit, 1_800_000_000_000);
  assert.equal(checkAmbientSave(ordinary), null);
  const cap = buildAmbientWorld({ layout: os.layout, tileMap: os.tileMap, blockedTiles: os.blockedTiles, homeRegions: livingRegions(os.layout, footprintOf), seats: [...os.seats.values()], population: AMBIENT_POPULATION_CAP * 10 });
  assert.equal(cap.residents.length, AMBIENT_POPULATION_CAP);
  // the cap world has no trains of its own here: carry the ordinary snapshot's two train tuples with
  // EMPTY onboard lists (their riders belong to the other resident set, which the contract refuses)
  const atCap = serializeAmbient(os.layout, cap, null, 1_800_000_000_000);
  atCap.trains = ordinary.trains.map((t) => [t[0], t[1], t[2], t[3], t[4], t[5], t[6], t[7], []]);
  assert.equal(checkAmbientSave(atCap), null);
  assert.ok(ambientSaveBytes(atCap) <= AMBIENT_SAVE_MAX_BYTES);
  report('save.ambient_state_bytes', { ordinaryResidents: ordinary.residents.length, ordinaryBytes: ambientSaveBytes(ordinary), capResidents: atCap.residents.length, capBytes: ambientSaveBytes(atCap), longestId: Math.max(...atCap.residents.map((r) => r[0].length)) });
});

test('AMBIENT SHEETS MESSAGE: the handshake payload of the 10 bundled sheets is measured', () => {
  const dir = path.join(ROOT, 'webview-ui/public/assets/ambient');
  let pngBytes = 0;
  let cells = 0;
  for (const id of fs.readdirSync(dir)) {
    const m = JSON.parse(fs.readFileSync(path.join(dir, id, 'manifest.json'), 'utf-8'));
    const buf = fs.readFileSync(path.join(dir, id, `${id}.png`));
    pngBytes += buf.length;
    const png = PNG.sync.read(buf);
    assert.equal(png.width, m.columns.length * m.frameWidth);
    cells += png.width * png.height;
  }
  // each pixel serializes as '' (2 chars + comma) or "#rrggbb"/"#rrggbbaa" (9-11 chars + comma)
  report('assets.ambient_sheets', { sheets: fs.readdirSync(dir).length, pngBytes, decodedPixels: cells, decodedRgbaBytes: cells * 4, messageBytesUpperBound: cells * 12 });
});
