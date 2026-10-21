/**
 * LIVING CITY 6–9, all four together, judged by the product's own engine on the SHIPPED living metropolis (no browser):
 * lighting, weather, traffic and the residents and subway as ONE OfficeState.
 *
 * - DECORATION INVARIANCE: the residents and the subway never depend on lighting, weather or traffic, and the traffic never
 *   depends on lighting or weather: the same steps leave the same residents, trains and vehicles whatever the other
 *   features are set to. (Nothing here reads or writes a Mission, an agent's activity, an assignment or a delivery.)
 * - THE MAP NEVER SHOWS A VEHICLE TWICE: with traffic Off, Low or Normal, a vehicle is drawn on the Map as the parked
 *   furniture of the static layer or as a marker of the living layer, never both. Held still by reduced motion every
 *   vehicle is one or the other (parked + markers = every vehicle the world places). In motion that does NOT hold at every
 *   moment: a vehicle between a stretch's end and its return has neither a parked copy nor a marker (the measured
 *   figures are printed, and the documentation says so).
 * - THE BOUNDS AT THE CAP: the residents' path searches, hub work and routes, at the population cap with every rider
 *   riding, stay inside their declared bounds.
 */
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';

import { beforeAll, test } from 'vitest';

import type { CityPresentation } from '../../core/src/cityPresentation.ts';
import * as C from '../src/constants.ts';
import { buildAmbientWorld } from '../src/office/ambient/ambientWorld.ts';
import { OfficeState } from '../src/office/engine/officeState.ts';
import { getCatalogEntry } from '../src/office/layout/furnitureCatalog.ts';
import { isWalkable } from '../src/office/layout/tileMap.ts';
import { livingRegions } from '../src/office/layout/worldViews.ts';
import { drawLivingLayer } from '../src/office/livingLayer.ts';
import { vehiclePlacement, vehicleSprite } from '../src/office/traffic/trafficRender.ts';
import { deriveTransitLine, TransitWorld } from '../src/office/transit/transitWorld.ts';
import type { OfficeLayout } from '../src/office/types.ts';
import { loadBundledCatalog } from './helpers/bundledCatalog.ts';

const ROOT = path.join(__dirname, '..', '..');
const world = (): OfficeLayout =>
  JSON.parse(
    fs.readFileSync(path.join(ROOT, 'packaging/di/worlds/living-metropolis.json'), 'utf-8'),
  );
const present = (over: Partial<CityPresentation>): CityPresentation => ({
  v: 1,
  lighting: 'day',
  weather: 'off',
  traffic: 'normal',
  ...over,
});
beforeAll(() => {
  loadBundledCatalog();
}, 120_000);

const DT = 0.1;
function make(settings: CityPresentation, reduced = false): OfficeState {
  const o = new OfficeState(world());
  o.setCityPresentation(settings);
  o.setAmbientEnabled(true);
  o.reducedMotion = reduced;
  if (reduced) o.setCityPresentation(settings); // the shown light jumps to its target at once under reduced motion
  return o;
}

// ── decoration invariance ────────────────────────────────────────────────────────────────────────

test('residents and the subway are the same whatever the lighting, weather and traffic are doing; traffic is the same whatever the lighting and weather are', () => {
  const SECONDS = 200;
  const configs: Array<[string, CityPresentation]> = [
    ['day, clear, traffic on', present({})],
    ['night, rain, traffic on', present({ lighting: 'night', weather: 'rain' })],
    ['dusk, snow, traffic low', present({ lighting: 'dusk', weather: 'snow', traffic: 'low' })],
    ['night, rain, traffic off', present({ lighting: 'night', weather: 'rain', traffic: 'off' })],
  ];
  const states = configs.map(([name, c]) => {
    const o = make(c);
    for (let k = 0; k < SECONDS / DT; k++) o.update(DT);
    return {
      name,
      people: JSON.stringify([o.serializeAmbient(0), o.transit!.totals, o.ambient!.stats]),
      vehicles: JSON.stringify(o.getTraffic()?.snapshot() ?? null),
    };
  });
  for (const s of states)
    assert.equal(s.people, states[0].people, `residents and subway: ${s.name}`);
  // the two runs with the SAME traffic setting and different light and weather: identical vehicles
  assert.equal(
    states[1].vehicles,
    states[0].vehicles,
    'traffic does not depend on lighting or weather',
  );
  // and the settings that DO change traffic changed it
  assert.notEqual(states[2].vehicles, states[0].vehicles, 'low traffic is a different street');
  console.log(
    `MEASURED invariance over ${SECONDS} s: four settings, one set of residents and trains (${states[0].people.length} bytes of state compared), two identical streets`,
  );
}, 600_000);

// ── residents among the traffic ──────────────────────────────────────────────────────────────────

test('residents among the vehicles: the street keeps flowing, vehicles give way to the people who step into them, and nothing locks up', () => {
  const SECONDS = 300;
  const run = (withResidents: boolean) => {
    const o = new OfficeState(world());
    o.setCityPresentation(present({ lighting: 'night', weather: 'rain', traffic: 'normal' }));
    o.setAmbientEnabled(true);
    if (!withResidents) o.ambient = null; // the same street with nobody on foot
    for (let k = 0; k < SECONDS / DT; k++) o.update(DT);
    return { ...o.getTraffic()!.counters };
  };
  const crowd = run(true);
  const empty = run(false);
  console.log(
    `MEASURED residents among vehicles over ${SECONDS} s: with 145 residents ${crowd.tilesDriven} tiles driven, ${crowd.yields} vehicles gave way to someone stepping into them, ${crowd.pedestrianDenials} claims refused for a pedestrian, ${crowd.watchdogs} gridlock valves; with nobody on foot ${empty.tilesDriven} tiles driven (the street flows ${((crowd.tilesDriven / empty.tilesDriven) * 100).toFixed(1)} per cent as much among the residents)`,
  );
  assert.equal(empty.yields, 0);
  assert.equal(empty.pedestrianDenials, 0);
  assert.ok(
    crowd.pedestrianDenials > 0,
    'vehicles were held back for residents: they are really among them',
  );
  assert.ok(
    crowd.tilesDriven >= empty.tilesDriven * 0.9,
    'residents slow the street by less than a tenth',
  );
  assert.equal(crowd.watchdogs, 0, 'no vehicle was ever stuck long enough to be removed');
  assert.ok(
    crowd.yields < crowd.spawns * 0.1,
    'someone stepping into a vehicle is the exception, not the rule',
  );
}, 600_000);

// ── the Map accounts for every vehicle ───────────────────────────────────────────────────────────

class Recorder {
  fillStyle: unknown = '';
  rects: Array<{ style: unknown; x: number; y: number; w: number; h: number }> = [];
  fillRect(x: number, y: number, w: number, h: number): void {
    this.rects.push({ style: this.fillStyle, x, y, w, h });
  }
}

test('the Map never shows a vehicle twice: held still every vehicle is parked furniture or a marker; in motion each is one or the other, and a vehicle away between stretches is neither', () => {
  const ref = new OfficeState(world());
  ref.setCityPresentation(present({ traffic: 'off' }));
  ref.setAmbientEnabled(true);
  const total = ref.furniture.length;
  const isVehicle = (type: string) => type.startsWith('VEHICLE_');
  const vehiclesInLayout = ref.layout.furniture.filter((f) => isVehicle(f.type)).length;
  const report: string[] = [];
  for (const reduced of [true, false]) {
    for (const [mode, active] of [
      ['off', 0],
      ['low', C.TRAFFIC_CAP_LOW],
      ['normal', 68],
    ] as const) {
      const o = make(present({ traffic: mode }), reduced);
      // in motion, forty seconds in the vehicles are out on the street; held still, a few ticks put each at its parked place
      for (let k = 0; k < (reduced ? 5 : 400); k++) o.update(DT);
      const hidden = o.getTraffic()?.hiddenUids().size ?? 0;
      // the static layer is the furniture instances: every piece except the parked copy of an ACTIVE vehicle
      assert.equal(
        o.furniture.length,
        total - hidden,
        `${mode} (reduced ${reduced}): the static layer loses exactly the active vehicles' parked copies`,
      );
      assert.equal(hidden, active, `${mode}: ${active} vehicles are allowed`);
      const markers = o.getVehicles();
      if (reduced)
        assert.equal(
          markers.length,
          active,
          `${mode}, held still: every active vehicle is visible, at its parked place`,
        );
      else
        assert.ok(
          markers.length <= active && markers.length >= active * 0.5,
          `${mode}: ${markers.length} of ${active} are on the street`,
        );
      // what the living layer draws: one marker per visible vehicle, at its own footprint, in the one neutral colour
      // at a quarter of the scale, and at a twentieth (a small Map, where the two-pixel minimum is what keeps a vehicle visible)
      for (const scale of [0.25, 0.05]) {
        const rec = new Recorder();
        const sx = scale;
        const sy = scale;
        const counts = drawLivingLayer(rec, [], [], sx, sy, markers);
        assert.equal(counts.vehicles, markers.length);
        assert.equal(rec.rects.length, markers.length);
        markers.forEach((v, i) => {
          const sprite = vehicleSprite(v)!;
          const pl = vehiclePlacement(v, sprite);
          const r = rec.rects[i];
          assert.equal(r.style, C.WORLD_MAP_VEHICLE_COLOR);
          assert.deepEqual(
            [r.x, r.y, r.w, r.h],
            [
              Math.round(pl.x * sx),
              Math.round(pl.y * sy),
              Math.max(C.WORLD_MAP_VEHICLE_MIN_PX, Math.round(pl.w * sx)),
              Math.max(C.WORLD_MAP_VEHICLE_MIN_PX, Math.round(pl.h * sy)),
            ],
          );
          if (scale === 0.05)
            assert.ok(
              r.w >= C.WORLD_MAP_VEHICLE_MIN_PX && r.h >= C.WORLD_MAP_VEHICLE_MIN_PX,
              'never smaller than the minimum',
            );
        });
      }
      // every vehicle of the world is accounted for once: still parked in the static layer, or a marker (reduced motion: no gap at all)
      const parkedStill = ref.layout.furniture.filter((f) => isVehicle(f.type)).length - hidden;
      if (reduced)
        assert.equal(
          parkedStill + markers.length,
          vehiclesInLayout,
          `${mode}: parked + markers = every vehicle of the world`,
        );
      report.push(
        `${mode}${reduced ? ' (held)' : ''}: ${parkedStill} parked + ${markers.length} markers`,
      );
    }
  }
  console.log(
    `MEASURED Map accounting for ${vehiclesInLayout} vehicles in the world: ${report.join('; ')}`,
  );
}, 600_000);

test('with no traffic the living layer draws no vehicle, and a Map without vehicles still draws its residents and trains as before', () => {
  const o = make(present({ traffic: 'off' }));
  for (let k = 0; k < 50; k++) o.update(DT);
  const rec = new Recorder();
  const withNone = drawLivingLayer(
    rec,
    o.getAmbientResidents(),
    o.getTrains(),
    0.25,
    0.25,
    o.getVehicles(),
  );
  const rec2 = new Recorder();
  const before = drawLivingLayer(rec2, o.getAmbientResidents(), o.getTrains(), 0.25, 0.25);
  assert.equal(withNone.vehicles, 0);
  assert.deepEqual(
    rec.rects,
    rec2.rects,
    'the same markers as the layer drew before traffic existed',
  );
  assert.equal(before.vehicles, 0);
});

// ── the bounds at the cap ────────────────────────────────────────────────────────────────────────

test('AT THE POPULATION CAP with every rider riding, searches, hub work, routes and aboard stay inside their bounds over 300 s', () => {
  const o = new OfficeState(world());
  o.setCityPresentation(present({ traffic: 'off' })); // residents never read traffic; this run is about them
  o.setAmbientEnabled(true);
  const line = deriveTransitLine(o.layout, (c, r) => isWalkable(c, r, o.tileMap, o.blockedTiles))!;
  const w = buildAmbientWorld({
    layout: o.layout,
    tileMap: o.tileMap,
    blockedTiles: o.blockedTiles,
    homeRegions: livingRegions(o.layout, (type) => {
      const e = getCatalogEntry(type);
      return e ? { w: e.footprintW, h: e.footprintH, seat: e.category === 'chairs' } : null;
    }),
    seats: [...o.seats.values()],
    transit: line,
    population: C.AMBIENT_POPULATION_CAP,
  });
  o.ambient = w;
  o.transit = new TransitWorld(line, w);
  assert.equal(w.residents.length, C.AMBIENT_POPULATION_CAP);
  assert.equal(
    w.riders.length,
    C.AMBIENT_RIDERS_MAX,
    'the riders are at their bound at the cap too',
  );
  let maxPerTick = 0;
  let maxAboard = 0;
  let maxRoute = 0;
  let maxLocal = 0;
  let longestRoutine = 0;
  for (const r of w.residents) longestRoutine = Math.max(longestRoutine, r.routine.length);
  for (let k = 0; k < 300 / DT; k++) {
    const before = w.stats.searches;
    o.update(DT);
    maxPerTick = Math.max(maxPerTick, w.stats.searches - before);
    maxAboard = Math.max(
      maxAboard,
      o.transit!.trains.reduce((n, t) => n + t.passengers.length, 0),
    );
    for (const r of w.residents) {
      maxRoute = Math.max(maxRoute, r.route.length);
      if (r.state === 'walk' && r.route.length <= C.AMBIENT_PATH_MAX_LENGTH)
        maxLocal = Math.max(maxLocal, r.route.length);
    }
  }
  console.log(
    `MEASURED at the cap over 300 s: ${w.residents.length} residents, ${w.riders.length} riders; searches per tick at most ${maxPerTick} (bound ${C.AMBIENT_PATH_SEARCHES_PER_TICK}); most hubs settled in one query ${w.stats.maxHubsSettled} of ${w.graph.stats.hubs}; most tiles expanded by one local search ${w.stats.maxExpanded} (bound ${C.AMBIENT_PATH_MAX_EXPANSIONS}); longest route walked ${maxRoute} (bound ${C.AMBIENT_ROUTE_MAX_LENGTH}); longest routine ${longestRoutine} (bound ${C.AMBIENT_ROUTINE_MAX_STOPS}); most aboard at once ${maxAboard} (bound ${C.AMBIENT_RIDERS_MAX}); leg caches at most ${C.AMBIENT_LEG_CACHE_PER_RESIDENT} each`,
  );
  assert.ok(maxPerTick <= C.AMBIENT_PATH_SEARCHES_PER_TICK, `${maxPerTick} searches in one tick`);
  assert.ok(w.stats.maxHubsSettled <= w.graph.stats.hubs);
  // a local search expands at most its bound (the count includes the node that ends it)
  assert.ok(
    w.stats.maxExpanded <= C.AMBIENT_PATH_MAX_EXPANSIONS + 1,
    `${w.stats.maxExpanded} nodes expanded`,
  );
  assert.ok(maxRoute <= C.AMBIENT_ROUTE_MAX_LENGTH, `a route of ${maxRoute} tiles`);
  assert.ok(longestRoutine <= C.AMBIENT_ROUTINE_MAX_STOPS);
  assert.ok(maxAboard <= C.AMBIENT_RIDERS_MAX);
  for (const r of w.residents)
    assert.ok(
      r.legCache.size <= C.AMBIENT_LEG_CACHE_PER_RESIDENT,
      `${r.id}: ${r.legCache.size} cached legs`,
    );
  assert.equal(w.stats.failures, 0, 'no route failed');
}, 600_000);
