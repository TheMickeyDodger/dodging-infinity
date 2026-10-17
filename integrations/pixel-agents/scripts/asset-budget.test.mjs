// The asset budget and admission checks (scripts/asset-budget.mjs): the shipped tree passes, and
// each ceiling REFUSES when exceeded by one (a ceiling tested only from below bounds nothing).
import assert from 'node:assert/strict';
import path from 'node:path';
import { test } from 'node:test';
import { fileURLToPath } from 'node:url';

import { ASSET_CEILINGS, assetFacts, checkAssetBudget } from './asset-budget.mjs';

const ROOT = path.join(path.dirname(fileURLToPath(import.meta.url)), '..');
const facts = assetFacts(ROOT);

test('the built tree is within every ceiling and every new admission is inspected', () => {
  assert.deepEqual(checkAssetBudget(facts), []);
  assert.ok(facts.ids.length - facts.baselineIds.length >= 1, 'there are admissions since M2 to check');
  console.log(`[assets] ${JSON.stringify({ bytes: facts.bytes, atlases: facts.atlases, catalogEntries: facts.ids.length, admittedSinceM2: facts.ids.filter((i) => !facts.baselineIds.includes(i)).length, ceilings: ASSET_CEILINGS })}`);
});

test('asset bytes over the ceiling by one byte: REFUSED', () => {
  assert.match(checkAssetBudget({ ...facts, bytes: ASSET_CEILINGS.bytes + 1 }).join('\n'), /asset bytes 4194305 exceed 4194304/);
  assert.deepEqual(checkAssetBudget({ ...facts, bytes: ASSET_CEILINGS.bytes }), []);
});

test('source atlases over the ceiling by one: REFUSED', () => {
  assert.match(checkAssetBudget({ ...facts, atlases: ASSET_CEILINGS.atlases + 1 }).join('\n'), /source atlases 25 exceed 24/);
});

test('catalog entries over the ceiling by one: REFUSED', () => {
  const ids = [...facts.ids];
  while (ids.length <= ASSET_CEILINGS.catalogEntries) ids.push(`PADDING_${ids.length}`);
  const baselineIds = [...facts.baselineIds, ...ids.filter((i) => i.startsWith('PADDING_'))];
  assert.match(checkAssetBudget({ ...facts, ids, baselineIds }).join('\n'), /catalog entries 501 exceed 500/);
});

test('one new sprite without an inspection record, or with an empty one: REFUSED', () => {
  assert.match(checkAssetBudget({ ...facts, ids: [...facts.ids, 'BRAND_NEW_SPRITE'] }).join('\n'), /1 admitted sprite\(s\) without an inspection record: BRAND_NEW_SPRITE/);
  const emptied = facts.inspections.map((r, k) => (k === 0 ? { ...r, evidence: '' } : r));
  assert.match(checkAssetBudget({ ...facts, inspections: emptied }).join('\n'), /without an inspection record/);
});
