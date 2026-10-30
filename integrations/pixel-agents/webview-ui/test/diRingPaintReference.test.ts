/**
 * The painted-ring rule of the Mission details Back box, measured on the REFERENCE (pixel-lock 02) at its NATIVE size, BEFORE any candidate result is used. The reference is a private
 * design input, so this test runs only when GEO_REF_MISSION_PNG names it (otherwise vitest reports it SKIPPED, not passed). It records the reference's sha256, measures the four sides
 * of the hand-measured Back box with test/diRingPaint.ts, measures the same image with its ring removed (the negative control), and derives the minimum a side must reach: half of the
 * weakest reference side. If the reference cannot be told from its ring-removed copy, this test FAILS and that concrete limitation is the result; the product is never redefined to satisfy it.
 * Once the rule is recorded in the fixture (views.mission.paint), this test also checks that the recorded rule is what the reference gives and that the reference passes it.
 */
import assert from 'node:assert/strict';
import crypto from 'node:crypto';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

import { PNG } from 'pngjs';
import { test } from 'vitest';

import { ringPeaks, ruleFromReference, SIDES, sidesBelow, withRingRemoved } from './diRingPaint.ts';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
interface Target {
  id: string;
  lock: number;
}
const fixture = JSON.parse(fs.readFileSync(path.join(root, 'test/fixtures/stage-header-geometry-targets.FIXTURE.json'), 'utf-8')) as {
  views: {
    mission: {
      reference: { file: string; sha256: string; size: [number, number]; stageLeftEdge: number };
      targets: Target[];
      paint?: { referenceSha256: string; minPeak: number };
    };
  };
};
const file = process.env.GEO_REF_MISSION_PNG;

test.skipIf(file === undefined || file === '')(
  'REFERENCE (native size): the painted-ring rule measured on the locked Mission reference, and on a copy of it with the ring removed',
  () => {
    const spec = fixture.views.mission;
    const bytes = fs.readFileSync(file!);
    const sha = crypto.createHash('sha256').update(bytes).digest('hex');
    assert.equal(sha, spec.reference.sha256, 'the file is the locked reference recorded in the fixture (sha256)');
    const png = PNG.sync.read(bytes);
    assert.deepEqual([png.width, png.height], spec.reference.size, 'the reference is read at its native size');
    const lock = (id: string) => spec.targets.find((t) => t.id === id)!.lock;
    const box = { x: spec.reference.stageLeftEdge + lock('back.left'), y: lock('back.top'), w: lock('back.w'), h: lock('back.h') };
    const peaks = ringPeaks(png, box);
    const removed = ringPeaks(withRingRemoved(png, box), box);
    const rule = ruleFromReference(peaks, removed);
    console.info(
      `REFERENCE sha256 ${sha} native ${png.width}x${png.height} box ${JSON.stringify(box)}: peaks ${SIDES.map((s) => `${s} ${peaks[s].peak}@${peaks[s].at}`).join(', ')}; ` +
        `ring removed ${SIDES.map((s) => `${s} ${removed[s].peak}`).join(', ')}; weakest ${rule.weakest}, strongest removed ${rule.strongestRemoved}, minPeak ${rule.minPeak}`,
    );
    assert.ok(rule.distinguishes, 'the paint rule can tell the reference from the reference with its ring removed (the weakest reference side above the strongest removed side, and the minimum above it)');
    if (spec.paint !== undefined) {
      assert.equal(spec.paint.referenceSha256, sha, 'the recorded rule was derived from this reference');
      assert.equal(spec.paint.minPeak, rule.minPeak, 'the recorded minimum is what the reference gives');
      assert.deepEqual(sidesBelow(peaks, spec.paint.minPeak), [], 'the reference itself passes the recorded rule');
    }
  },
);
