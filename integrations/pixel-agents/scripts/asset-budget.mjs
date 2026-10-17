/**
 * ASSET BUDGET AND ADMISSION (M3): the checks the DI package build runs BEFORE it seals.
 * Units are kept apart: asset BYTES (file content under dist/assets), source ATLASES (generated
 * sheets sprites were extracted from), CATALOG ENTRIES (admitted runtime furniture sprites: every
 * `type: "asset"` node in the furniture manifests) and ADMISSIONS (catalog entries not in the M2
 * baseline, each of which needs an inspection record).
 * Ceilings (BUDGETS.json): assets.package_asset_bytes_ceiling 4,194,304 B,
 * assets.atlas_count_ceiling 24, assets.admitted_sprite_count_ceiling 500,
 * assets.admission_uninspected_cap 0.
 */
import fs from 'node:fs';
import path from 'node:path';

export const ASSET_CEILINGS = { bytes: 4_194_304, atlases: 24, catalogEntries: 500, uninspected: 0 };

export function assetBytes(dir) {
  let total = 0;
  for (const e of fs.readdirSync(dir, { withFileTypes: true })) {
    const p = path.join(dir, e.name);
    total += e.isDirectory() ? assetBytes(p) : fs.statSync(p).size;
  }
  return total;
}

export function catalogIds(furnitureDir) {
  const ids = [];
  for (const folder of fs.readdirSync(furnitureDir).sort()) {
    const p = path.join(furnitureDir, folder, 'manifest.json');
    if (!fs.existsSync(p)) continue;
    const walk = (n) => {
      if (n?.type === 'asset') ids.push(n.id);
      for (const c of n?.members ?? []) walk(c);
    };
    walk(JSON.parse(fs.readFileSync(p, 'utf-8')));
  }
  return ids;
}

/** Every reason the package must NOT seal (empty = admissible). */
export function checkAssetBudget({ bytes, atlases, ids, baselineIds, inspections }, ceilings = ASSET_CEILINGS) {
  const reasons = [];
  if (!(bytes <= ceilings.bytes)) reasons.push(`asset bytes ${bytes} exceed ${ceilings.bytes}`);
  if (!(atlases <= ceilings.atlases)) reasons.push(`source atlases ${atlases} exceed ${ceilings.atlases}`);
  if (!(ids.length <= ceilings.catalogEntries)) reasons.push(`catalog entries ${ids.length} exceed ${ceilings.catalogEntries}`);
  const base = new Set(baselineIds);
  const inspected = new Map((inspections ?? []).map((r) => [r.id, r]));
  const uninspected = ids.filter((id) => {
    if (base.has(id)) return false;
    const r = inspected.get(id);
    return !(r && typeof r.source === 'string' && r.source && typeof r.inspection === 'string' && r.inspection && typeof r.evidence === 'string' && r.evidence);
  });
  if (uninspected.length > ceilings.uninspected) reasons.push(`${uninspected.length} admitted sprite(s) without an inspection record: ${uninspected.slice(0, 8).join(', ')}`);
  return reasons;
}

/** The facts of a source tree or package: dist/assets plus the packaging records. */
export function assetFacts(root) {
  const assets = path.join(root, 'dist', 'assets');
  const prov = JSON.parse(fs.readFileSync(path.join(root, 'packaging/di/ASSET-PROVENANCE.json'), 'utf-8'));
  const lw = JSON.parse(fs.readFileSync(path.join(root, 'packaging/di/LIVING-WORLD-PROVENANCE.json'), 'utf-8'));
  return {
    bytes: assetBytes(assets),
    atlases: prov.atlases.length + lw.records.length,
    ids: catalogIds(path.join(assets, 'furniture')),
    baselineIds: JSON.parse(fs.readFileSync(path.join(root, 'packaging/di/ASSET-BASELINE-M2.json'), 'utf-8')).ids,
    inspections: JSON.parse(fs.readFileSync(path.join(root, 'packaging/di/ASSET-INSPECTIONS.json'), 'utf-8')).records,
  };
}
