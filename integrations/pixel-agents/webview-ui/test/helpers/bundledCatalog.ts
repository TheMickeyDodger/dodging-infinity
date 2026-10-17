/** The bundled furniture catalog (manifests only, empty sprite rows), for world-level tests. */
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';

import type {
  FurnitureAsset,
  FurnitureManifest,
  InheritedProps,
  ManifestGroup,
} from '../../../core/src/assets/manifestUtils.ts';
import { flattenManifest } from '../../../core/src/assets/manifestUtils.ts';
import { buildDynamicCatalog } from '../../src/office/layout/furnitureCatalog.ts';

const ROOT = path.join(__dirname, '..', '..', '..');

export function loadBundledCatalog(): void {
  const dir = path.join(ROOT, 'webview-ui/public/assets/furniture');
  const catalog: FurnitureAsset[] = [];
  for (const d of fs.readdirSync(dir)) {
    const f = path.join(dir, d, 'manifest.json');
    if (!fs.existsSync(f)) continue;
    const m = JSON.parse(fs.readFileSync(f, 'utf-8')) as FurnitureManifest;
    if (m.type === 'asset') {
      catalog.push({
        id: m.id,
        name: m.name,
        label: m.name,
        category: m.category,
        file: m.file ?? `${m.id}.png`,
        width: m.width!,
        height: m.height!,
        footprintW: m.footprintW!,
        footprintH: m.footprintH!,
        isDesk: m.category === 'desks',
        canPlaceOnWalls: m.canPlaceOnWalls,
        canPlaceOnSurfaces: m.canPlaceOnSurfaces,
        backgroundTiles: m.backgroundTiles,
        groupId: m.id,
      });
      continue;
    }
    const inherited: InheritedProps = {
      groupId: m.id,
      name: m.name,
      category: m.category,
      canPlaceOnWalls: m.canPlaceOnWalls,
      canPlaceOnSurfaces: m.canPlaceOnSurfaces,
      backgroundTiles: m.backgroundTiles,
      ...(m.rotationScheme ? { rotationScheme: m.rotationScheme } : {}),
    };
    const root: ManifestGroup = {
      type: 'group',
      groupType: m.groupType as ManifestGroup['groupType'],
      rotationScheme: m.rotationScheme,
      members: m.members!,
    };
    catalog.push(...flattenManifest(root, inherited));
  }
  const sprites = Object.fromEntries(
    catalog.map((a) => [a.id, Array.from({ length: a.height }, () => new Array<string>(a.width).fill(''))]),
  );
  assert.ok(buildDynamicCatalog({ catalog, sprites }));
}
