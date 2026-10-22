/**
 * THE VISIT RULE SHIFTS NO DRAWS — a SAME-PLAN comparison. The same candidate world is built twice from the same code: once with EVERY roaming office
 * resident making the cross-world visit (AMBIENT_OFFICE_VISIT_EVERY = 1, the constant overridden for this file's module graph only) and once as shipped
 * (every 4th). Apart from the one far stop that a non-visiting roamer leaves out, every resident is identical: id, job, district, palette, hue, initial
 * timer, start tile and every other routine stop. So the rule re-rolls nobody's look or timer; the non-visiting roamers' far-stop search runs with the
 * same draws and only the push is skipped.
 *
 * What this does NOT claim: that office residents equal S4's. Their posts and stops now come from the office's common rooms (a different tile list, hence
 * different rejection draws), so their routines, start tiles and some looks/timers differ from S4 by the P2-C design; those residents are reset FRESH by
 * the exact-predecessor migration (`ambientMigration.test.ts`). The 118 remote residents are exactly S4's (`ambientPlanGolden.test.ts`).
 */
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';

import { expect, test, vi } from 'vitest';

type Row = { id: string; job: string; district: string; palette: number; hue: number; timer: number; col: number; row: number; routine: number[] };
const ROOT = path.join(__dirname, '..', '..');

/** Build the candidate's living-metropolis ambient plan in a FRESH module graph, with the visit constant optionally overridden. */
async function plan(every: number | null): Promise<Row[]> {
  vi.resetModules();
  vi.doUnmock('../src/constants.ts');
  if (every !== null) {
    vi.doMock('../src/constants.ts', async (importOriginal) => ({ ...(await importOriginal<typeof import('../src/constants.ts')>()), AMBIENT_OFFICE_VISIT_EVERY: every }));
  }
  const { loadBundledCatalog } = await import('./helpers/bundledCatalog.ts');
  const { OfficeState } = await import('../src/office/engine/officeState.ts');
  const constants = await import('../src/constants.ts');
  expect(constants.AMBIENT_OFFICE_VISIT_EVERY).toBe(every ?? 4);
  loadBundledCatalog();
  const layout = JSON.parse(fs.readFileSync(path.join(ROOT, 'packaging/di/worlds/living-metropolis.json'), 'utf8'));
  const o = new OfficeState(layout);
  o.setReceptionistEnabled(true);
  o.setAmbientEnabled(true);
  return o.ambient!.residents.map((r) => ({ id: r.id, job: r.job, district: r.district, palette: r.palette, hue: r.hueShift, timer: r.timer, col: r.tileCol, row: r.tileRow, routine: Array.from(r.routine) }));
}

test('SAME PLAN: every-1 vs every-4 differ only by the dropped far stop of the non-visiting office roamers; look, timer and start tile are identical for all 145 residents', async () => {
  const every1 = await plan(1);
  const shipped = await plan(null);
  vi.doUnmock('../src/constants.ts');
  assert.equal(every1.length, 145);
  assert.deepEqual(shipped.map((r) => r.id), every1.map((r) => r.id), 'the same residents in the same order');
  const differing: string[] = [];
  every1.forEach((a, i) => {
    const b = shipped[i];
    assert.equal(b.job, a.job, `${a.id}: job`);
    assert.equal(b.district, a.district, `${a.id}: district`);
    assert.equal(b.palette, a.palette, `${a.id}: palette`);
    assert.equal(b.hue, a.hue, `${a.id}: hue (no reseed)`);
    assert.equal(b.timer, a.timer, `${a.id}: initial timer (no reseed)`);
    assert.equal(b.col, a.col, `${a.id}: start column`);
    assert.equal(b.row, a.row, `${a.id}: start row`);
    if (a.routine.join() !== b.routine.join()) {
      differing.push(a.id);
      assert.deepEqual(a.routine.slice(0, -1), b.routine, `${a.id}: the shipped routine is the every-1 routine without its LAST (far) stop`);
    }
  });
  // the Palace has 8 roamers (2 visit), the Atrium 4 (1 visits), the Observatory 5 (2 visit): 6 + 3 + 3 = 12 roamers stay home
  assert.equal(differing.length, 12);
  assert.ok(differing.every((id) => /^amb-(palace|botanical-atrium|deco-observatory)-/.test(id)), 'only residents of the three offices');
  assert.deepEqual(
    every1.filter((a) => !differing.includes(a.id)).map((a) => a.routine.join()),
    shipped.filter((b) => !differing.includes(b.id)).map((b) => b.routine.join()),
    'every other resident, remote and office, has the identical routine',
  );
}, 300_000);
