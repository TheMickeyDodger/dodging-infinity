/**
 * C6: navigation across the three grand offices. Quick views for the detected offices (the Atrium and the Observatory) — present exactly when offices are
 * detected, each the same camera jump the Districts list does — and the engine side of locate / focus / glow / selection for a character in ANY office:
 * hit testing finds it, the working halo reads it by its server id, and selection and camera follow are plain id fields that do not care which office
 * it sits in. (The real click on Locate in each office is in the built-UI smoke.)
 */
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';

import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { beforeAll, test } from 'vitest';

import type { DiOfficeState } from '../../core/src/messages.ts';
import { WorldViewControls } from '../src/components/WorldViewControls.tsx';
import { DI_OFFICE_DISTRICT_PATTERNS } from '../src/constants.ts';
import { OfficeState } from '../src/office/engine/officeState.ts';
import { districtsOf, frameDistrict } from '../src/office/layout/worldViews.ts';
import type { OfficeLayout } from '../src/office/types.ts';
import { loadBundledCatalog } from './helpers/bundledCatalog.ts';

const ROOT = path.join(__dirname, '..', '..');
beforeAll(() => loadBundledCatalog());
const LAYOUT = JSON.parse(fs.readFileSync(path.join(ROOT, 'packaging/di/worlds/living-metropolis.json'), 'utf8')) as OfficeLayout;
const office = () => {
  const o = new OfficeState(LAYOUT);
  o.setReceptionistEnabled(true);
  return o;
};
const quickViews = (o: OfficeState) =>
  (o.officeDetection?.offices ?? [])
    .filter((x) => x.kind === 'district')
    .map((x) => ({ label: x.label, short: DI_OFFICE_DISTRICT_PATTERNS.find((p) => p.match.test(x.label))?.short ?? x.label }));
const controls = (offices?: Array<{ label: string; short: string }>) =>
  renderToStaticMarkup(
    createElement(WorldViewControls, {
      views: ['office', 'deck', 'woods'],
      active: null,
      onSelect: () => undefined,
      mapOpen: false,
      mapAvailable: true,
      onToggleMap: () => undefined,
      districts: districtsOf(LAYOUT).map((d) => d.label),
      onSelectDistrict: () => undefined,
      districtStatus: null,
      ...(offices ? { offices } : {}),
    }),
  );

test('the Atrium and Observatory quick views exist exactly when the offices are detected, labelled by the district they jump to', () => {
  const views = quickViews(office());
  assert.deepEqual(views, [
    { label: 'Botanical Atrium', short: 'Atrium' },
    { label: 'Deco Observatory', short: 'Observatory' },
  ]);
  const html = controls(views);
  assert.match(html, /data-testid="world-view-office-atrium"[^>]*>Atrium</);
  assert.match(html, /data-testid="world-view-office-observatory"[^>]*>Observatory</);
  assert.match(html, /title="Camera: go to the Botanical Atrium \(view only\)"/);
  // the existing controls are all still there, in the same group
  for (const id of ['world-view-office', 'world-view-deck', 'world-view-woods', 'world-view-map', 'world-view-districts']) assert.ok(html.includes(`data-testid="${id}"`), id);
  // no offices, no buttons: Generic Pixel / a world without detected offices / the prop omitted
  assert.ok(!controls([]).includes('world-view-office-'));
  assert.ok(!controls().includes('world-view-office-'));
  const metropolis = new OfficeState(JSON.parse(fs.readFileSync(path.join(ROOT, 'packaging/di/worlds/metropolis.json'), 'utf8')));
  metropolis.setReceptionistEnabled(true);
  assert.deepEqual(quickViews(metropolis), []);
});

test('each quick view\'s district is a real district the camera can frame, centred, at an ordinary and at a constrained viewport', () => {
  const districts = new Map(districtsOf(LAYOUT).map((d) => [d.label, d]));
  for (const { label } of quickViews(office())) {
    const d = districts.get(label)!;
    assert.ok(d, `${label} is a district of the layout`);
    for (const [w, h, dpr] of [[1600, 1000, 1], [1280, 720, 1], [1200, 800, 2]] as const) {
      const f = frameDistrict(LAYOUT, d, w, h, dpr);
      assert.ok(f, `${label} frames at ${w}x${h}@${dpr}`);
      assert.equal(f!.centred, true, `${label} centres at ${w}x${h}@${dpr}`);
    }
  }
});

test('a character in ANY office is found by hit testing, read by the halo by its id, and selectable/followable like any other', () => {
  const o = office();
  const det = o.officeDetection!;
  const ids = [11, 12, 13];
  det.offices.forEach((off, i) => o.addAgent(ids[i], 0, 0, off.workstationIds[5], true));
  for (const [i, id] of ids.entries()) {
    const ch = o.characters.get(id)!;
    assert.equal(det.officeIndexBySeat.get(ch.seatId!), i, `character ${id} sits in ${det.offices[i].label}`);
    assert.equal(o.getCharacterAt(ch.x, ch.y - 4), id, `hit testing finds the character in ${det.offices[i].label}`);
  }
  // the working halo reads server ids and does not care where the character sits
  o.diOffice = {
    live: true,
    times: { validUntil: 4_000_000_000 },
    missions: {
      state: 'observed',
      missions: [{ live: true, members: { state: 'observed', members: ids.map((id) => ({ live: true, animateActive: true, characterId: id })) } }],
    },
  } as unknown as DiOfficeState;
  assert.deepEqual([...o.haloCharacterIds(1)].sort(), ids);
  // locate = select + follow, by id (what the panel's Locate does), in every office
  for (const id of ids) {
    o.selectedAgentId = id;
    o.cameraFollowId = id;
    assert.equal(o.characters.get(o.selectedAgentId)!.id, id);
    assert.ok(o.characters.has(o.cameraFollowId));
  }
  o.selectedAgentId = null;
  o.cameraFollowId = null;
});
