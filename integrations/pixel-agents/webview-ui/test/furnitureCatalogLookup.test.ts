/**
 * The furniture catalog lookup is indexed by type (it runs for every placed piece on every furniture animation frame,
 * so it replaced a scan of the catalog per piece). The index must answer EXACTLY as the scan did: the first entry of a
 * type, undefined for an unknown type, and the shipped world's every placed type resolved to an entry of that type.
 */
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';

import { test } from 'vitest';

import type { LoadedAssetData } from '../src/office/layout/furnitureCatalog.ts';
import { buildDynamicCatalog, getCatalogEntry } from '../src/office/layout/furnitureCatalog.ts';
import type { OfficeLayout } from '../src/office/types.ts';
import { loadBundledCatalog } from './helpers/bundledCatalog.ts';

const ROOT = path.join(__dirname, '..', '..');

const asset = (
  id: string,
  label: string,
  footprintW: number,
): LoadedAssetData['catalog'][number] => ({
  id,
  label,
  category: 'decor',
  width: 16,
  height: 16,
  footprintW,
  footprintH: 1,
  isDesk: false,
});

test('the first entry of a type wins (as the scan it replaced returned it), an unknown type is undefined, and a type with no sprite is not found', () => {
  const sprite = [['']]; // one transparent pixel: the lookup never renders it
  const ok = buildDynamicCatalog({
    catalog: [
      asset('LAMP', 'first lamp', 1),
      asset('SOFA', 'sofa', 3),
      asset('LAMP', 'second lamp, never reachable by type', 2),
      asset('NO_SPRITE', 'has no sprite', 1),
    ],
    sprites: { LAMP: sprite, SOFA: sprite },
  });
  assert.equal(ok, true);
  assert.equal(getCatalogEntry('LAMP')?.label, 'first lamp');
  assert.equal(getCatalogEntry('LAMP')?.footprintW, 1);
  assert.equal(getCatalogEntry('SOFA')?.footprintW, 3);
  assert.equal(
    getCatalogEntry('NO_SPRITE'),
    undefined,
    'an asset without sprite data never enters the catalog',
  );
  assert.equal(getCatalogEntry('NOT_A_TYPE'), undefined);
  assert.equal(getCatalogEntry(''), undefined);
  assert.equal(
    getCatalogEntry('LAMP'),
    getCatalogEntry('LAMP'),
    'the same entry object every time, as the scan returned',
  );
  // a rebuild replaces the index whole: nothing of the earlier catalog is answered
  buildDynamicCatalog({ catalog: [asset('SOFA', 'only sofa', 2)], sprites: { SOFA: sprite } });
  assert.equal(
    getCatalogEntry('LAMP'),
    undefined,
    'a type absent from the rebuilt catalog is gone',
  );
  assert.equal(getCatalogEntry('SOFA')?.footprintW, 2);
});

test('every type the shipped living metropolis places resolves to an entry of that type in the bundled catalog', () => {
  loadBundledCatalog();
  const world = JSON.parse(
    fs.readFileSync(path.join(ROOT, 'packaging/di/worlds/living-metropolis.json'), 'utf-8'),
  ) as OfficeLayout;
  const types = new Set(world.furniture.map((f) => f.type));
  assert.ok(types.size > 20, `${types.size} placed types`);
  for (const type of types) {
    const entry = getCatalogEntry(type);
    assert.ok(entry, `${type} is in the catalog`);
    assert.equal(entry.type, type);
    assert.ok(entry.footprintW >= 1 && entry.footprintH >= 1, `${type} has a footprint`);
  }
});
