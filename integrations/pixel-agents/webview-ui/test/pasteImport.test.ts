/**
 * THE PASTE IMPORT ROUTE (M3, DI office in a browser): a NEW entry path for externally supplied JSON,
 * held to the same admission discipline as every other path. Pasted text is bounded BEFORE it is
 * parsed, then goes through exactly the validator and atomic apply a file's text goes through; a
 * refusal (malformed, over the bound, another world or resident set) is shown and changes nothing.
 * (The layout text route itself — prepareImport/applyImport — is covered in layoutIntake.test.ts;
 * the server end of both routes in server/__tests__/layoutEntryPaths.test.ts and diAmbientState.)
 */
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';

import { afterEach, beforeAll, test, vi } from 'vitest';

import { AMBIENT_SAVE_MAX_BYTES } from '../../core/src/ambientSave.ts';
import { LAYOUT_MAX_FILE_BYTES } from '../../core/src/layoutLimits.ts';
import { prepareAmbientText, utf8Bytes } from '../src/office/ambient/ambientIntake.ts';
import { OfficeState } from '../src/office/engine/officeState.ts';
import { prepareImport } from '../src/office/layout/layoutIntake.ts';
import type { OfficeLayout } from '../src/office/types.ts';
import { loadBundledCatalog } from './helpers/bundledCatalog.ts';

const ROOT = path.join(__dirname, '..', '..');
const living = (): OfficeLayout => JSON.parse(fs.readFileSync(path.join(ROOT, 'packaging/di/worlds/living-metropolis.json'), 'utf-8'));
let source: OfficeState;
let snapshotText: string;
beforeAll(() => {
  loadBundledCatalog();
  source = new OfficeState(living());
  source.setAmbientEnabled(true);
  // run until riders are aboard: the pasted snapshot must carry an active journey
  for (let k = 0; k < 6000; k++) {
    source.update(0.1);
    if (k > 600 && source.getAmbientResidents().filter((r) => r.state === 'riding').length >= 2) break;
  }
  snapshotText = JSON.stringify(source.serializeAmbient(Date.now()));
}, 600_000);
afterEach(() => vi.restoreAllMocks());

test('PRECONDITION (asserted): the pasted snapshot carries riders aboard', () => {
  assert.ok(JSON.parse(snapshotText).residents.filter((r: unknown[]) => r[4] === 3).length >= 2);
});

test('ambient: text over the snapshot bound is refused BEFORE it is parsed (a control shows the spy sees parses)', () => {
  const parse = vi.spyOn(JSON, 'parse');
  const over = `{"pad":"${'x'.repeat(AMBIENT_SAVE_MAX_BYTES)}"}`;
  assert.ok(utf8Bytes(over) > AMBIENT_SAVE_MAX_BYTES);
  const r = prepareAmbientText(over);
  assert.ok('refused' in r && /over the 65536-byte snapshot bound \(not parsed\)/.test(r.refused));
  assert.equal(parse.mock.calls.length, 0, 'not parsed');
  assert.ok('snapshot' in prepareAmbientText('{"a":1}'));
  assert.equal(parse.mock.calls.length, 1, 'the control parse is seen');
});

test('ambient: the bound counts UTF-8 BYTES, not characters', () => {
  const text = `"${'é'.repeat(AMBIENT_SAVE_MAX_BYTES / 2)}"`; // 2 bytes per character
  assert.ok(text.length < AMBIENT_SAVE_MAX_BYTES && utf8Bytes(text) > AMBIENT_SAVE_MAX_BYTES);
  assert.ok('refused' in prepareAmbientText(text));
});

test('ambient: malformed text is refused with its reason', () => {
  const r = prepareAmbientText('{ not json');
  assert.ok('refused' in r && r.refused === 'not valid JSON');
});

test('ambient: a pasted SAME-world snapshot is applied through the one validator, restoring the journey state; a mismatched set is refused whole and changes nothing', () => {
  const target = new OfficeState(living());
  target.setAmbientEnabled(true);
  for (let k = 0; k < 300; k++) target.update(0.1); // the target has moved on
  const intake = prepareAmbientText(snapshotText);
  assert.ok('snapshot' in intake);
  const applied = target.importAmbientSnapshot(intake.snapshot);
  assert.equal(applied.outcome, 'applied', applied.reason ?? '');
  assert.equal(applied.exact, true);
  const saved = JSON.parse(snapshotText);
  assert.equal(applied.aboardRestored, saved.residents.filter((r: unknown[]) => r[4] === 3).length);
  assert.deepEqual(target.serializeAmbient(saved.savedAt)!.trains, saved.trains);
  // mismatched resident set (another world fingerprint): refused whole, nothing changes
  const before = JSON.stringify(target.serializeAmbient(0));
  const other = prepareAmbientText(JSON.stringify({ ...saved, world: `X${saved.world.slice(1)}` }));
  assert.ok('snapshot' in other);
  const refused = target.importAmbientSnapshot(other.snapshot);
  assert.equal(refused.outcome, 'other-world');
  assert.ok(refused.reason);
  assert.equal(JSON.stringify(target.serializeAmbient(0)), before);
});

test('layout: pasted text over the file bound is refused BEFORE it is parsed (the same intake as a file)', () => {
  const parse = vi.spyOn(JSON, 'parse');
  const over = 'x'.repeat(LAYOUT_MAX_FILE_BYTES + 1);
  const r = prepareImport(over);
  assert.ok('refused' in r);
  assert.equal(parse.mock.calls.length, 0, 'not parsed');
}, 120_000);

test('WIRING: both paste kinds go through the SAME text routes the file imports use (one validator each, no parallel path)', () => {
  const src = fs.readFileSync(path.join(__dirname, '..', 'src', 'App.tsx'), 'utf-8');
  assert.match(src, /const importAmbientText = useCallback\(\(text: string\): PasteImportResult => \{\s*const intake = prepareAmbientText\(text\);/);
  assert.match(src, /os\.importAmbientSnapshot\(intake\.snapshot\)/);
  assert.match(src, /const importLayoutText = useCallback\(\s*\(text: string\): PasteImportResult => \{[\s\S]*?applyImport\(text, \{/);
  assert.match(src, /if \(kind === 'ambient'\) return importAmbientText\(text\);/);
  assert.match(src, /return importLayoutText\(text\);/);
  // the file route reads text and calls the same layout text route
  assert.match(src, /const result = importLayoutText\(String\(reader\.result\)\);/);
  // the DI browser settings offer no file chooser for imports
  const settings = fs.readFileSync(path.join(__dirname, '..', 'src', 'components', 'SettingsModal.tsx'), 'utf-8');
  assert.match(settings, /\{isBrowserRuntime && !pasteImports && \(/);
  assert.equal(/di-import-ambient-file/.test(settings), false);
});
