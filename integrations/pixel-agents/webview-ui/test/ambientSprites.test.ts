/**
 * AMBIENT CHARACTER ART (M3 S6) on the SHIPPED living metropolis: the five show-inspired Easter-egg
 * families are placed as residents in the places they belong, labelled as homages and decorative
 * (no affiliation wording); residents whose job has a delivered sheet draw from it, facing by the
 * sheet's own named rows; walking cycles the walk columns and standing cycles idle, idle, gesture,
 * so every drawn state animates (a single-column idle still alternates with its gesture).
 */
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';

import { PNG } from 'pngjs';
import { beforeAll, test } from 'vitest';

import type { AmbientSpriteSheet } from '../../core/src/messages.ts';
import { AMBIENT_EGGS, AMBIENT_JOBS, ambientLabel } from '../src/office/ambient/ambientJobs.ts';
import { ambientFrame, ambientSheetFor, setAmbientSheets, standingCycle } from '../src/office/ambient/ambientSprites.ts';
import { OfficeState } from '../src/office/engine/officeState.ts';
import type { OfficeLayout } from '../src/office/types.ts';
import { loadBundledCatalog } from './helpers/bundledCatalog.ts';

const ROOT = path.join(__dirname, '..', '..');
const AMBIENT_DIR = path.join(ROOT, 'webview-ui/public/assets/ambient');

/** Decode the bundled sheets the way the server does (a test-side reading, not the server code). */
function readSheets(): AmbientSpriteSheet[] {
  return fs
    .readdirSync(AMBIENT_DIR)
    .sort()
    .map((id) => {
      const m = JSON.parse(fs.readFileSync(path.join(AMBIENT_DIR, id, 'manifest.json'), 'utf-8'));
      const png = PNG.sync.read(fs.readFileSync(path.join(AMBIENT_DIR, id, `${id}.png`)));
      const frames = [0, 1, 2, 3].map((row) =>
        m.columns.map((_: string, col: number) =>
          Array.from({ length: m.frameHeight }, (_y, y) =>
            Array.from({ length: m.frameWidth }, (_x, x) => {
              const i = ((row * m.frameHeight + y) * png.width + col * m.frameWidth + x) * 4;
              return png.data[i + 3] === 0 ? '' : `#${[0, 1, 2, 3].map((k) => png.data[i + k].toString(16).padStart(2, '0')).join('')}`;
            }),
          ),
        ),
      );
      return { id, label: m.label, frameWidth: m.frameWidth, frameHeight: m.frameHeight, rows: m.rows, walk: m.walk, idle: m.idle, gesture: m.gesture, frames };
    });
}

let os: OfficeState;
let sheets: AmbientSpriteSheet[];
beforeAll(() => {
  loadBundledCatalog();
  sheets = readSheets();
  setAmbientSheets(sheets);
  os = new OfficeState(JSON.parse(fs.readFileSync(path.join(ROOT, 'packaging/di/worlds/living-metropolis.json'), 'utf-8')) as OfficeLayout);
  os.setAmbientEnabled(true);
}, 300_000);

const EXPECTED_HOMES: Record<string, string> = {
  'egg-pocket-creature': 'Autumn Wildwood',
  'egg-shinobi': 'Silver Pine Highlands',
  'egg-martial-artist': 'Deco Observatory',
  'egg-cul-de-sac-kid': 'Market Boulevard',
  'egg-trickster-hare': 'River Valley Farmland',
};

test('all five Easter-egg families are placed, each in the place it belongs, once', () => {
  const eggs = os.ambient!.eggs;
  assert.equal(eggs.length, AMBIENT_EGGS.length);
  for (const e of eggs) {
    assert.equal(e.home, EXPECTED_HOMES[e.job], `${e.job} home`);
    const r = os.getAmbientResidents().filter((x) => x.job === e.job);
    assert.equal(r.length, 1, `${e.job}: exactly one resident`);
    assert.equal(r[0].id, e.id);
    assert.equal(r[0].district, e.home);
  }
});

test('labels say homage, Ambient and decorative, and never name a franchise', () => {
  const FRANCHISE = /pok[eé]mon|pikachu|naruto|dragon ?ball|goku|ed,? edd|eddy|looney|bugs bunny/i;
  for (const egg of AMBIENT_EGGS) {
    const label = ambientLabel(egg.job);
    assert.match(label, /homage/i, egg.job);
    assert.match(label, /^Ambient · /);
    assert.match(label, / · decorative$/);
    assert.doesNotMatch(label, FRANCHISE, egg.job);
  }
  for (const s of sheets) assert.doesNotMatch(s.label, FRANCHISE, s.id);
});

test('every delivered sheet belongs to a job that has residents in the world (10 sheets, 10 jobs)', () => {
  assert.equal(sheets.length, 10);
  for (const s of sheets) {
    assert.ok(s.id in AMBIENT_JOBS, `${s.id} is a job`);
    assert.ok(os.getAmbientResidents().some((r) => r.job === s.id), `${s.id} has a resident`);
    assert.equal(ambientSheetFor(s.id), s);
  }
});

test("facing comes from the sheet's NAMED rows (incl. the re-ordered trickster-hare sheet)", () => {
  for (const s of sheets) {
    for (const [dir, name] of ['DOWN', 'LEFT', 'RIGHT', 'UP'].entries()) {
      const want = s.frames[s.rows.indexOf(name)];
      assert.ok(want.includes(ambientFrame(s, dir, true, 0) as never), `${s.id} ${name}`);
    }
  }
});

test('every drawn state animates: walk cycles its columns; standing cycles idle, idle, gesture', () => {
  for (const s of sheets) {
    const walk = new Set(s.walk.map((_, k) => ambientFrame(s, 0, true, k)));
    assert.equal(walk.size, new Set(s.walk).size, `${s.id} walk frames distinct`);
    const cycle = standingCycle(s);
    const standing = new Set(cycle.map((_, k) => ambientFrame(s, 0, false, k)));
    assert.ok(standing.size >= 2, `${s.id}: standing shows at least two poses`);
  }
});

test('in the running world a standing sheet-drawn resident changes pose over time (frame advances while dwelling)', () => {
  const r = os.getAmbientResidents().find((x) => ambientSheetFor(x.job) && x.state === 'dwell')!;
  assert.ok(r, 'a dwelling sheet-drawn resident exists');
  const sheet = ambientSheetFor(r.job)!;
  const seen = new Set<unknown>();
  for (let k = 0; k < 40 && r.state === 'dwell'; k++) {
    seen.add(ambientFrame(sheet, r.dir, false, r.frame));
    os.update(0.1);
  }
  assert.ok(seen.size >= 2, `poses seen ${seen.size}`);
});

test('PROVENANCE AND INSPECTION: every delivered sheet has a provenance record (generator, prompt, re-hashed sheet hash) and an inspection record; uninspected admissions = 0', () => {
  const prov = JSON.parse(fs.readFileSync(path.join(ROOT, 'packaging/di/LIVING-WORLD-PROVENANCE.json'), 'utf-8'));
  assert.equal(prov.uninspected, 0);
  for (const s of sheets) {
    const m = JSON.parse(fs.readFileSync(path.join(AMBIENT_DIR, s.id, 'manifest.json'), 'utf-8'));
    const rec = prov.records.find((r: { sha256: string }) => r.sha256 === m.source.sha256);
    assert.ok(rec, `${s.id}: a provenance record for source ${m.source.file}`);
    assert.equal(rec.file, m.source.file);
    assert.equal(rec.runtime.ambientSheet, s.id);
    assert.equal(rec.provenance.generator, 'built-in image_gen');
    assert.ok(rec.provenance.prompt && rec.provenance.promptSha256, `${s.id}: prompt recorded`);
    assert.equal(rec.inspection.segmentation.ok, true, `${s.id}: segmented`);
    assert.match(rec.inspection.facing.basis, /^ADJUDICATED \(visual/, `${s.id}: facing visually adjudicated`);
    assert.ok(rec.inspection.cells.alphaVerified > 0 && rec.inspection.cells.alphaVerified === rec.inspection.cells.extracted, `${s.id}: every extracted cell alpha-verified`);
  }
});
