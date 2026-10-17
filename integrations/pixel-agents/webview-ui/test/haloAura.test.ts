/**
 * THE HALO AS A GLOW (the human: "when I said halos for agents, I meant they should just glow"). The
 * DRAW changed; the gate (office/halo.ts eligibility, intensity) did not. This checks:
 * - the aura sprite hugs the silhouette: bounded radius, fading by distance, never on the body's own
 *   pixels, around the head and both sides, and NOTHING below the lowest opaque row (no feet or floor glow);
 * - the real renderScene: an eligible agent gets exactly one aura image, drawn BEFORE its sprite and
 *   aligned to it, at the halo intensity unmodified (so reduced motion stays steady); an ineligible agent
 *   gets none; and nothing ring- or ellipse-shaped is drawn at all.
 */
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';

import { afterAll, beforeAll, test } from 'vitest';

import { decodeCharacterPng } from '../../core/src/assets/pngDecoder.ts';
import { HALO_AURA_ALPHAS, HALO_AURA_CACHE_MAX_ENTRIES, HALO_AURA_RADIUS_PX, HALO_AURA_RGB } from '../src/constants.ts';
import { OfficeState } from '../src/office/engine/officeState.ts';
import { renderScene } from '../src/office/engine/renderer.ts';
import { haloIntensity } from '../src/office/halo.ts';
import { auraCacheSize, getAuraSprite } from '../src/office/sprites/spriteCache.ts';
import { getCharacterSprites, setCharacterTemplates } from '../src/office/sprites/spriteData.ts';
import type { SpriteData } from '../src/office/types.ts';
import { Direction } from '../src/office/types.ts';

const R = HALO_AURA_RADIUS_PX;

// a minimal canvas for getCachedSprite (the suite runs in Node: no DOM)
interface FakeCanvas {
  width: number;
  height: number;
  getContext: () => unknown;
}
const g = globalThis as { document?: unknown };
const hadDocument = 'document' in g;
const saved = g.document;
beforeAll(() => {
  // the six real character sheets, decoded as the product decodes them
  const dir = path.join(__dirname, '..', 'public', 'assets', 'characters');
  setCharacterTemplates([0, 1, 2, 3, 4, 5].map((i) => decodeCharacterPng(fs.readFileSync(path.join(dir, `char_${i}.png`)))));
  g.document = {
    createElement: (): FakeCanvas => ({ width: 0, height: 0, getContext: () => ({ imageSmoothingEnabled: false, fillStyle: '', fillRect() {} }) }),
  };
});
afterAll(() => {
  if (hadDocument) g.document = saved;
  else delete g.document;
});

const opaque = (s: SpriteData, r: number, c: number) => r >= 0 && c >= 0 && r < s.length && c < s[0].length && s[r][c] !== '';

test('the aura sprite hugs the silhouette: bounded, fading, off the body, around head and sides, nothing below the feet', () => {
  const sprite = getCharacterSprites(0).walk[Direction.DOWN][0];
  const rows = sprite.length;
  const cols = sprite[0].length;
  let lowest = -1;
  let top = rows;
  for (let r = 0; r < rows; r++) {
    if (!sprite[r].some((p) => p !== '')) continue;
    lowest = r;
    top = Math.min(top, r);
  }
  assert.ok(lowest > top, 'precondition: a real character silhouette');
  const aura = getAuraSprite(sprite);
  assert.equal(aura.length, rows + 2 * R);
  assert.equal(aura[0].length, cols + 2 * R);
  const alphaOf: Record<string, number> = {};
  HALO_AURA_ALPHAS.forEach((a, i) => (alphaOf[HALO_AURA_RGB + a.toString(16).padStart(2, '0')] = i + 1));
  let painted = 0;
  let aboveHead = 0;
  let leftSide = 0;
  let rightSide = 0;
  const mid = Math.round((top + lowest) / 2);
  for (let er = 0; er < aura.length; er++) {
    for (let ec = 0; ec < aura[0].length; ec++) {
      const px = aura[er][ec];
      if (px === '') continue;
      painted++;
      const sr = er - R;
      const sc = ec - R;
      assert.ok(!opaque(sprite, sr, sc), 'never on the body');
      assert.ok(sr <= lowest, `nothing below the lowest opaque row (row ${sr} > ${lowest})`);
      const band = alphaOf[px];
      assert.ok(band, `an aura colour: ${px}`);
      // its band is its true distance to the silhouette, so alpha falls with distance
      let best = Infinity;
      for (let r = 0; r < rows; r++) for (let c = 0; c < cols; c++) if (opaque(sprite, r, c)) best = Math.min(best, Math.hypot(r - sr, c - sc));
      assert.equal(band, Math.ceil(best - 1e-9), 'band = distance to the silhouette');
      if (sr < top) aboveHead++;
      if (sr === mid && sc < cols / 2) leftSide++;
      if (sr === mid && sc >= cols / 2) rightSide++;
    }
  }
  assert.ok(painted > 0 && aboveHead > 0 && leftSide > 0 && rightSide > 0, `around the body: ${JSON.stringify({ painted, aboveHead, leftSide, rightSide })}`);
  assert.ok(HALO_AURA_ALPHAS[0] > HALO_AURA_ALPHAS[1] && HALO_AURA_ALPHAS[1] > HALO_AURA_ALPHAS[2], 'it fades outward');
  console.log(`[glow] ${JSON.stringify({ sprite: `${cols}x${rows}`, aura: `${aura[0].length}x${aura.length}`, painted, aboveHead, leftSide, rightSide, lowestOpaqueRow: lowest })}`);
});

/** A recording 2D context: every call, with the globalAlpha in force at the time. */
function recordingContext() {
  const calls: Array<{ op: string; args: unknown[]; alpha: number }> = [];
  const state: Record<string, unknown> = { globalAlpha: 1, canvas: { width: 4000, height: 4000 } };
  const ctx = new Proxy(state, {
    get(t, k: string) {
      if (k in t) return t[k];
      return (...args: unknown[]) => calls.push({ op: k, args, alpha: t.globalAlpha as number });
    },
    set(t, k: string, v) {
      t[k] = v;
      return true;
    },
  });
  return { ctx: ctx as unknown as CanvasRenderingContext2D, calls };
}

function scene(intensity: number) {
  const o = new OfficeState();
  o.addAgent(1, undefined, undefined, undefined, true);
  o.addAgent(2, undefined, undefined, undefined, true);
  const chars = o.getCharacters();
  assert.equal(chars.length, 2, 'precondition: two real agents');
  const { ctx, calls } = recordingContext();
  const zoom = 4;
  renderScene(ctx, [], chars, 0, 0, zoom, null, null, [], [], [], { ids: new Set([1]), intensity });
  return { calls, chars, zoom };
}

test('renderScene: the eligible agent gets ONE aura image behind and aligned to its body; the other none; no ring or ellipse anywhere', () => {
  const { calls, zoom } = scene(0.6);
  for (const op of ['ellipse', 'arc', 'stroke', 'fill']) assert.ok(!calls.some((c) => c.op === op), `no ${op}: nothing ring- or floor-shaped`);
  const images = calls.filter((c) => c.op === 'drawImage');
  const auraW = (16 + 2 * R) * zoom;
  const auras = images.filter((c) => (c.args[0] as FakeCanvas).width === auraW);
  const bodies = images.filter((c) => (c.args[0] as FakeCanvas).width === 16 * zoom);
  assert.equal(bodies.length, 2, 'both agents drawn');
  assert.equal(auras.length, 1, 'exactly one aura, for the eligible agent only');
  const a = auras[0];
  const ai = images.indexOf(a);
  // the body it belongs to: the one it is aligned to (offset by the radius in each axis)
  const body = bodies.find((b) => (b.args[1] as number) - R * zoom === a.args[1] && (b.args[2] as number) - R * zoom === a.args[2]);
  assert.ok(body, 'the aura is aligned to a body: offset by the radius on both axes');
  assert.ok(ai < images.indexOf(body), 'drawn BEFORE (behind) that body');
  assert.equal(a.alpha, 0.6, 'at the halo intensity, unmodified');
});

test('reduced motion: the aura is steady (haloIntensity is constant, the draw uses it as is); normal motion pulses', () => {
  const alphaAt = (t: number, reduced: boolean) => {
    const { calls, zoom } = scene(haloIntensity(t, reduced));
    return calls.find((c) => c.op === 'drawImage' && (c.args[0] as FakeCanvas).width === (16 + 2 * R) * zoom)!.alpha;
  };
  const steady = [0, 0.25, 0.5, 1.3].map((t) => alphaAt(t, true));
  assert.ok(steady.every((x) => x === steady[0]) && steady[0] > 0, `steady and never off: ${steady}`);
  const pulsing = [0, 0.5].map((t) => alphaAt(t, false));
  assert.notEqual(pulsing[0], pulsing[1], 'normal motion pulses');
});

test('the aura-mask cache is HARD-capped: past the cap the least-recently-used mask is evicted, a recently used one survives', () => {
  const synth = (k: number): SpriteData => Array.from({ length: 8 }, (_, r) => Array.from({ length: 4 }, (_, c) => (r === k % 8 && c === 1 ? HALO_AURA_RGB : '')));
  const sprites = Array.from({ length: HALO_AURA_CACHE_MAX_ENTRIES + 40 }, (_, k) => synth(k));
  const first = getAuraSprite(sprites[0]);
  const keep = getAuraSprite(sprites[1]);
  for (let k = 2; k < sprites.length; k++) {
    getAuraSprite(sprites[k]);
    if (k % 50 === 0) assert.equal(getAuraSprite(sprites[1]), keep, 'a recently used mask is a cache hit (same object)');
    assert.ok(auraCacheSize() <= HALO_AURA_CACHE_MAX_ENTRIES, `size ${auraCacheSize()} within the cap`);
  }
  assert.equal(auraCacheSize(), HALO_AURA_CACHE_MAX_ENTRIES, 'filled exactly to the cap, never beyond');
  assert.notEqual(getAuraSprite(sprites[0]), first, 'the least-recently-used mask was evicted (rebuilt as a new object)');
  assert.equal(getAuraSprite(sprites[1]), keep, 'the recently used one survived');
});
