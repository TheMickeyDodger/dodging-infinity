/**
 * ITEM 18, MILESTONE 1: the season recolours and their caches. Summer is the original art (the same sprite, by identity); the other
 * seasons re-tone ONLY outdoor ground and outdoor foliage; the caches are capped, registered with the render-cache lifecycle, and a
 * cache restored from BLANK rebuilds exactly the sprites it held.
 */
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';

import { beforeAll, test } from 'vitest';

import type { FurnitureManifest, InheritedProps, ManifestGroup } from '../../core/src/assets/manifestUtils.ts';
import { flattenManifest } from '../../core/src/assets/manifestUtils.ts';
import * as C from '../src/constants.ts';
import * as A from '../src/office/city/citySeasonArt.ts';
import { resetRenderCaches } from '../src/office/renderCaches.ts';
import type { SpriteData } from '../src/office/types.ts';
import { TileType } from '../src/office/types.ts';

function hsl(hex: string): { h: number; s: number; l: number } {
  const r = parseInt(hex.slice(1, 3), 16) / 255;
  const g = parseInt(hex.slice(3, 5), 16) / 255;
  const b = parseInt(hex.slice(5, 7), 16) / 255;
  const max = Math.max(r, g, b);
  const min = Math.min(r, g, b);
  const l = (max + min) / 2;
  if (max === min) return { h: 0, s: 0, l };
  const d = max - min;
  const s = l > 0.5 ? d / (2 - max - min) : d / (max + min);
  const h = max === r ? (g - b) / d + (g < b ? 6 : 0) : max === g ? (b - r) / d + 2 : (r - g) / d + 4;
  return { h: h * 60, s, l };
}

const sprite = (...rows: string[][]): SpriteData => rows;
/** A pixel string from channels, in the spelling asked for (the lint rule keeps colour literals in constants.ts). */
const px = (r: number, g: number, b: number, opts: { upper?: boolean; alpha?: number } = {}): string => {
  const h = (v: number) => v.toString(16).padStart(2, '0');
  const body = `#${h(r)}${h(g)}${h(b)}${opts.alpha === undefined ? '' : h(opts.alpha)}`;
  return opts.upper ? body.toUpperCase() : body;
};
const GRASS = px(0x46, 0x70, 0x2a); // meadow green, hue ~100
const LEAF = px(0x3f, 0x8f, 0x3a); // leaf green
const WARM_LEAF = px(0xc8, 0x64, 0x1e); // an autumn-orange leaf (the authored Autumn Wildwood palette)
const TRUNK = px(0x5a, 0x3d, 0x22); // dark brown trunk
const ROCK = px(0x7a, 0x7a, 0x7a); // grey

test('the bounds and tone limits are exactly these values', () => {
  assert.equal(C.CITY_SEASON_CACHE_MAX_ENTRIES, 2_048);
  assert.equal(C.CITY_SEASON_GREEN_HUE_MIN, 60);
  assert.equal(C.CITY_SEASON_GREEN_HUE_MAX, 175);
  assert.equal(C.CITY_SEASON_GREY_SATURATION, 0.1);
  assert.equal(C.CITY_SEASON_TRUNK_LIGHTNESS, 0.32);
});

/** A tone, in the order the table writes it. */
const T = (hueTo: number, hueBlend: number, satMul: number, satMax: number, lightMul: number, lightAdd: number) => ({ hueTo, hueBlend, satMul, satMax, lightMul, lightAdd });
const SAME = T(0, 0, 1, 1, 1, 0);
/** A tone caps saturation in HSL, but a recoloured pixel is stored as 8-bit RGB: read back, a saturation near a pale colour can exceed its cap by the rounding (0.0115 at lightness 0.83). */
const QUANTISATION = 0.02;
/** THE EXACT TONE INVENTORY, written out in full: 6 kinds x 3 seasons x 2 bands. A tone changes only by a deliberate edit of this table. */
const EXPECTED_TONES = {
  ground: {
    spring: { green: T(108, 0.5, 1.12, 0.8, 1.1, 0.07), warm: SAME },
    autumn: { green: T(38, 0.8, 1.05, 0.62, 1, 0.03), warm: T(32, 0.45, 1.12, 0.6, 0.9, 0) },
    winter: { green: T(212, 1, 0.3, 0.12, 0.42, 0.66), warm: T(212, 1, 0.3, 0.12, 0.42, 0.66) },
  },
  soil: {
    spring: { green: SAME, warm: T(28, 0.1, 1.05, 0.6, 0.96, 0) },
    autumn: { green: SAME, warm: T(24, 0.2, 1.1, 0.6, 0.97, 0) },
    winter: { green: T(30, 0, 0.3, 0.16, 0.55, 0.36), warm: T(30, 0, 0.3, 0.16, 0.55, 0.36) },
  },
  hard: {
    spring: { green: SAME, warm: SAME },
    autumn: { green: T(30, 0.12, 1.1, 0.5, 0.98, 0), warm: T(30, 0.12, 1.1, 0.5, 0.98, 0) },
    winter: { green: T(30, 0, 0.5, 0.3, 1, 0.12), warm: T(30, 0, 0.5, 0.3, 1, 0.12) },
  },
  canopy: {
    spring: { green: T(112, 0.3, 1.12, 0.85, 1.06, 0.05), warm: T(104, 0.85, 0.85, 0.7, 1.1, 0.08) },
    autumn: { green: T(30, 0.85, 1.1, 0.78, 1.05, 0.02), warm: T(28, 0.45, 0.62, 0.7, 0.86, 0) },
    winter: { green: T(204, 0.7, 0.3, 0.22, 0.72, 0.2), warm: T(30, 0, 0.32, 0.28, 0.92, 0.05) },
  },
  conifer: {
    spring: { green: T(112, 0.25, 1.12, 0.8, 1.1, 0.05), warm: SAME },
    autumn: { green: T(80, 0.2, 0.92, 0.7, 0.94, 0), warm: SAME },
    winter: { green: T(192, 0.4, 0.45, 0.3, 0.75, 0.2), warm: SAME },
  },
  crop: {
    spring: { green: T(100, 0.45, 1.1, 0.75, 1.06, 0.04), warm: T(100, 0.45, 1.1, 0.75, 1.06, 0.04) },
    autumn: { green: T(40, 0.3, 1.1, 0.75, 1, 0), warm: T(40, 0.3, 1.1, 0.75, 1, 0) },
    winter: { green: T(66, 1, 0.5, 0.26, 0.5, 0.42), warm: T(66, 1, 0.5, 0.26, 0.5, 0.42) },
  },
} as const;

test('THE TONE TABLE is exactly this: every tone of every kind, season and band, and no summer entry (summer is the original art)', () => {
  assert.deepEqual(Object.keys(C.CITY_SEASON_TONES), ['ground', 'soil', 'hard', 'canopy', 'conifer', 'crop']);
  for (const kind of Object.keys(C.CITY_SEASON_TONES) as Array<keyof typeof C.CITY_SEASON_TONES>) {
    assert.deepEqual(Object.keys(C.CITY_SEASON_TONES[kind]), ['spring', 'autumn', 'winter'], `${kind}: no summer entry (summer is the original art)`);
  }
  assert.deepEqual(C.CITY_SEASON_TONES, EXPECTED_TONES);
});

test('THE WINTER HUE RULE: no winter tone blends a hue only part of the way (that runs the short way round the wheel through magenta and paints brown mauve); each keeps the source hue or imposes the target', () => {
  for (const kind of Object.keys(C.CITY_SEASON_TONES) as Array<keyof typeof C.CITY_SEASON_TONES>) {
    for (const band of ['green', 'warm'] as const) {
      const tone = C.CITY_SEASON_TONES[kind].winter[band];
      // the conifer and canopy GREEN bands blend green toward blue, which is the short way through cyan (no magenta on that arc): they are exempt
      const greenToBlue = (kind === 'canopy' || kind === 'conifer') && band === 'green';
      if (!greenToBlue) assert.ok(tone.hueBlend === 0 || tone.hueBlend === 1, `${kind}/winter/${band}: hueBlend ${tone.hueBlend} is neither 0 nor 1`);
    }
  }
});

test('WINTER IS NEVER LILAC: warm brown, tan, red, orange and olive pixels come out of every winter tone in a warm, grey, olive or cool-white hue, never in the pink-to-purple range', () => {
  const warmSources = [px(150, 100, 60), px(160, 130, 100), px(200, 50, 50), px(200, 100, 30), px(217, 164, 65), px(120, 80, 70), px(170, 140, 120), px(200, 120, 90)];
  for (const kind of ['ground', 'soil', 'hard', 'canopy', 'crop'] as const) {
    for (const source of warmSources) {
      const out = hsl(A.seasonalSprite('winter', kind, sprite([source]))[0][0]);
      const lilac = out.s > 0.04 && out.h >= 245 && out.h <= 345; // violet, purple, mauve, pink
      assert.ok(!lilac, `${kind}: ${source} comes out ${JSON.stringify(out)}, which is lilac`);
    }
  }
  // the specific cases of the first pass: a brown plank, a tan cobble and an autumn-red leaf (all warm) keep their warm hue (hard, soil, canopy) or take the frost hue (ground)
  for (const kind of ['hard', 'soil'] as const) {
    const out = hsl(A.seasonalSprite('winter', kind, sprite([px(150, 100, 60)]))[0][0]);
    assert.ok(out.h > 20 && out.h < 50, `${kind}: a brown plank stays brown (hue ${out.h})`);
  }
  const ground = hsl(A.seasonalSprite('winter', 'ground', sprite([px(150, 100, 60)]))[0][0]);
  assert.ok(Math.abs(ground.h - 212) < 1.5 && ground.s <= 0.12 + QUANTISATION && ground.l > 0.7, `ground: cool white (${JSON.stringify(ground)})`);
});

test('WINTER KEEPS THE WORLD READABLE: leaves and conifers stay clearly darker than the snow beneath them, and a winter trail is darker than the snow beside it', () => {
  const snow = hsl(A.seasonalSprite('winter', 'ground', sprite([GRASS]))[0][0]);
  for (const [kind, source] of [['canopy', LEAF], ['canopy', WARM_LEAF], ['conifer', LEAF]] as const) {
    const out = hsl(A.seasonalSprite('winter', kind, sprite([source]))[0][0]);
    assert.ok(out.l < snow.l - 0.15, `${kind}: ${source} is ${out.l.toFixed(2)} against snow ${snow.l.toFixed(2)}`);
  }
  // a crop field is PALE olive by design, so it is told from the snow by its hue and its colour, not by being darker
  const stubble = hsl(A.seasonalSprite('winter', 'crop', sprite([px(217, 164, 65)]))[0][0]);
  assert.ok(Math.abs(stubble.h - snow.h) > 100 && stubble.s > snow.s + 0.1, `crop stubble ${JSON.stringify(stubble)} against snow ${JSON.stringify(snow)}`);
  const trail = hsl(A.seasonalSprite('winter', 'soil', sprite([px(160, 110, 70)]))[0][0]);
  assert.ok(trail.l < snow.l - 0.1, `a trail (${trail.l.toFixed(2)}) is darker than the snow (${snow.l.toFixed(2)})`);
});

test('CROPS: winter fields are pale olive (hue 66, low saturation, light), spring fresh and autumn harvest gold', () => {
  const crop = px(217, 164, 65);
  const winter = hsl(A.seasonalSprite('winter', 'crop', sprite([crop]))[0][0]);
  assert.ok(Math.abs(winter.h - 66) < 1.5 && winter.s <= 0.26 + QUANTISATION && winter.l > 0.55 && winter.l < 0.8, `winter crop ${JSON.stringify(winter)}`);
  const spring = hsl(A.seasonalSprite('spring', 'crop', sprite([crop]))[0][0]);
  assert.ok(spring.h > hsl(crop).h, 'spring: toward fresh green');
  const autumn = hsl(A.seasonalSprite('autumn', 'crop', sprite([crop]))[0][0]);
  assert.ok(autumn.h > 30 && autumn.h < 50, `autumn: harvest gold (${autumn.h})`);
});

test('AUTUMN IS VISIBLY DIFFERENT FROM SUMMER even over foliage and ground that is already autumn: a warm leaf and the warm forest floor turn browner and duller (later autumn)', () => {
  // WARM_LEAF is the authored Autumn Wildwood palette: in SUMMER it is the original art (the very same string); in AUTUMN it must change visibly
  const leaf = hsl(WARM_LEAF);
  const autumn = hsl(A.seasonalSprite('autumn', 'canopy', sprite([WARM_LEAF]))[0][0]);
  assert.ok(leaf.s - autumn.s > 0.2, `duller: saturation ${leaf.s.toFixed(2)} to ${autumn.s.toFixed(2)}`);
  assert.ok(leaf.l - autumn.l > 0.04, `darker: lightness ${leaf.l.toFixed(2)} to ${autumn.l.toFixed(2)}`);
  // a RED leaf (the authored red oaks and bushes) moves toward brown-orange; an orange leaf is already near that hue and just dulls and darkens
  const red = px(200, 50, 50);
  const redAutumn = hsl(A.seasonalSprite('autumn', 'canopy', sprite([red]))[0][0]);
  assert.ok(redAutumn.h - hsl(red).h > 8, `browner: a red leaf's hue ${hsl(red).h.toFixed(0)} to ${redAutumn.h.toFixed(0)}`);
  // the forest floor (warm brown ground) takes a darker, redder leaf-litter tone
  const floor = px(110, 80, 40);
  const litter = hsl(A.seasonalSprite('autumn', 'ground', sprite([floor]))[0][0]);
  // leaf litter: about a tenth darker (the tone's lightMul is 0.9) and a little more saturated
  assert.ok(litter.l <= hsl(floor).l * 0.92, `the forest floor is darker in autumn (${hsl(floor).l.toFixed(2)} to ${litter.l.toFixed(2)})`);
  assert.ok(litter.s > hsl(floor).s, `and richer (${hsl(floor).s.toFixed(2)} to ${litter.s.toFixed(2)})`);
  assert.notEqual(A.seasonalSprite('autumn', 'ground', sprite([floor]))[0][0].toUpperCase(), floor.toUpperCase());
  // pavement has a light autumn warmth, and an evergreen is dulled; summer returns each of them untouched (by identity)
  for (const [kind, source] of [['hard', px(150, 140, 130)], ['conifer', LEAF]] as const) {
    assert.notEqual(A.seasonalSprite('autumn', kind, sprite([source]))[0][0].toUpperCase(), source.toUpperCase(), `${kind}: autumn changes it`);
    const s = sprite([source]);
    assert.ok(A.seasonalSprite('summer', kind, s) === s);
  }
});

test('SUMMER is the original art: the very same sprite, so it is pixel-identical and costs nothing', () => {
  A.resetSeasonCaches();
  const s = sprite([GRASS, GRASS], ['', GRASS]);
  for (const kind of ['ground', 'soil', 'hard', 'canopy', 'conifer', 'crop'] as const) {
    assert.ok(A.seasonalSprite('summer', kind, s) === s, `${kind}: summer hands back the source sprite`);
  }
  assert.deepEqual(A.seasonCacheEntries(), { sprites: 0, pixels: 0, types: 0 }); // and filled no cache
});

test('spring is fresher, autumn is golden, winter is snow: the ground reads as its season', () => {
  const grass = hsl(GRASS);
  assert.ok(grass.h > 90 && grass.h < 110);
  const at = (season: 'spring' | 'autumn' | 'winter') => hsl(A.seasonalSprite(season, 'ground', sprite([GRASS]))[0][0]);
  const spring = at('spring');
  assert.ok(spring.l > grass.l && spring.h > 90 && spring.h < 125, `spring: lighter green (${JSON.stringify(spring)})`);
  const autumn = at('autumn');
  assert.ok(autumn.h > 25 && autumn.h < 65, `autumn: golden hue (${autumn.h})`);
  const winter = at('winter');
  assert.ok(winter.l > 0.7 && winter.s < 0.2, `winter: snow-white ground (${JSON.stringify(winter)})`);
  assert.ok(winter.h > 190 && winter.h < 230, `winter: cool white (${winter.h})`);
});

test('foliage: leaves take the season, but trunks, rock and empty pixels are left exactly as they are', () => {
  const tree = sprite([LEAF, WARM_LEAF, TRUNK, ROCK, '']);
  for (const season of ['spring', 'autumn', 'winter'] as const) {
    const out = A.seasonalSprite(season, 'canopy', tree)[0];
    assert.equal(out[2], TRUNK, `${season}: the trunk is not foliage`);
    assert.equal(out[3], ROCK, `${season}: grey is not foliage`);
    assert.equal(out[4], '', `${season}: transparent stays transparent`);
    assert.notEqual(out[0], LEAF, `${season}: green leaves change`);
  }
  // a warm (authored autumn) leaf turns green in spring and pale in winter; it is the authored palette in summer
  const spring = hsl(A.seasonalSprite('spring', 'canopy', tree)[0][1]);
  assert.ok(spring.h > 70 && spring.h < 140, `spring: a warm leaf greens (${spring.h})`);
  // winter leaves are FROSTED (lighter and far less saturated than summer's) but stay clearly darker than the snow beneath them, so a tree still
  // reads (the first pass lifted them to +0.2 and they washed out into the ground; the test 'WINTER KEEPS THE WORLD READABLE' holds the contrast)
  const winterLeaf = hsl(A.seasonalSprite('winter', 'canopy', tree)[0][0]);
  assert.ok(winterLeaf.l > hsl(LEAF).l + 0.05 && winterLeaf.s < 0.25, 'winter: frosted leaves');
  // a warm (authored autumn) leaf turns grey-taupe in winter, in its own warm hue
  const winterWarm = hsl(A.seasonalSprite('winter', 'canopy', tree)[0][1]);
  assert.ok(winterWarm.s < 0.3 && winterWarm.h < 60, `winter: a warm leaf goes taupe (${JSON.stringify(winterWarm)})`);
  // an evergreen stays GREEN in autumn (a duller, darker green, so a conifer district reads as autumn too) and is frosted in winter
  const pine = sprite([LEAF]);
  const duller = hsl(A.seasonalSprite('autumn', 'conifer', pine)[0][0]);
  assert.notEqual(A.seasonalSprite('autumn', 'conifer', pine)[0][0].toUpperCase(), LEAF.toUpperCase());
  assert.ok(duller.h > 70 && duller.h < 130 && duller.l < hsl(LEAF).l && duller.s < hsl(LEAF).s, `autumn: a duller green (${JSON.stringify(duller)})`);
  const frosted = hsl(A.seasonalSprite('winter', 'conifer', pine)[0][0]);
  assert.ok(frosted.l > hsl(LEAF).l && frosted.h > 120 && frosted.h < 220);
});

type Band = 'green' | 'warm';
const SEASONS3 = ['spring', 'autumn', 'winter'] as const;
const KINDS = ['ground', 'soil', 'hard', 'canopy', 'conifer', 'crop'] as const;
/** The tones that are authored as identity (a season that leaves that kind and band exactly as the original art). */
const AUTHORED_IDENTITY = [
  'spring/ground/warm',
  'spring/soil/green', 'autumn/soil/green',
  'spring/hard/green', 'spring/hard/warm',
  'spring/conifer/warm', 'autumn/conifer/warm', 'winter/conifer/warm',
].sort();
const isIdentityTone = (t: { hueBlend: number; satMul: number; satMax: number; lightMul: number; lightAdd: number }) =>
  t.hueBlend === 0 && t.satMul === 1 && t.satMax === 1 && t.lightMul === 1 && t.lightAdd === 0;
/** Test pixels in each band: brighter than the trunk limit and more saturated than the grey limit, so only the tone decides. In both
 *  letter cases and with an alpha byte, so a change of spelling would show. */
const BAND_PIXELS: Record<Band, string[]> = {
  green: [
    px(63, 143, 58, { upper: true }),
    px(63, 143, 58),
    px(70, 112, 42),
    px(70, 112, 42, { upper: true, alpha: 0x80 }),
    px(90, 168, 76),
  ],
  warm: [
    px(200, 100, 30, { upper: true }),
    px(200, 100, 30),
    px(217, 164, 65),
    px(217, 164, 65, { alpha: 0x80 }),
    px(200, 50, 100),
  ],
};

test('IDENTITY CASES: every season x kind x band whose tone is identity returns the INPUT PIXEL STRING unchanged, byte for byte (spelling and alpha included)', () => {
  const found: string[] = [];
  for (const season of SEASONS3) {
    for (const kind of KINDS) {
      for (const band of ['green', 'warm'] as Band[]) {
        if (isIdentityTone(C.CITY_SEASON_TONES[kind][season][band])) found.push(`${season}/${kind}/${band}`);
      }
    }
  }
  assert.deepEqual(found.sort(), AUTHORED_IDENTITY, 'the authored identity tones are exactly these');
  for (const key of AUTHORED_IDENTITY) {
    const [season, kind, band] = key.split('/') as [(typeof SEASONS3)[number], (typeof KINDS)[number], Band];
    for (const pixel of BAND_PIXELS[band]) {
      const out = A.seasonalSprite(season, kind, sprite([pixel, ''], [pixel, pixel]));
      assert.deepEqual(out, [[pixel, ''], [pixel, pixel]], `${key}: ${pixel} is returned exactly as given`);
      // not merely equal: the very same strings, in the same case
      assert.ok(out[0][0] === pixel && out[1][1] === pixel);
    }
  }
});

test('every NON-identity tone really recolours its band, and every recoloured pixel is in the repo uppercase hex convention (colorize.ts)', () => {
  const HEX = /^#[0-9A-F]{6}([0-9A-F]{2})?$/;
  let changed = 0;
  for (const season of SEASONS3) {
    for (const kind of KINDS) {
      for (const band of ['green', 'warm'] as Band[]) {
        if (AUTHORED_IDENTITY.includes(`${season}/${kind}/${band}`)) continue;
        for (const pixel of BAND_PIXELS[band]) {
          const out = A.seasonalSprite(season, kind, sprite([pixel]))[0][0];
          assert.notEqual(out.toUpperCase(), pixel.toUpperCase(), `${season}/${kind}/${band}: ${pixel} is recoloured`);
          assert.match(out, HEX, `${season}/${kind}/${band}: ${out}`);
          if (pixel.length === 9) assert.equal(out.slice(7), pixel.slice(7).toUpperCase(), 'the alpha byte is kept');
          changed++;
        }
      }
    }
  }
  // 3 seasons x 6 kinds x 2 bands = 36 tones, 8 of them identity, 28 recolouring; 5 test pixels in each band
  // (the identity tones are the ones whose season has nothing to change: later autumn recolours ground, pavement, warm foliage and evergreens)
  assert.equal(changed, (36 - AUTHORED_IDENTITY.length) * 5);
  assert.equal(AUTHORED_IDENTITY.length, 8);
});

test('summer is identity for every kind and every pixel, by sprite identity', () => {
  for (const kind of KINDS) {
    for (const pixel of [...BAND_PIXELS.green, ...BAND_PIXELS.warm, TRUNK, ROCK, '']) {
      const s = sprite([pixel]);
      assert.ok(A.seasonalSprite('summer', kind, s) === s, `${kind}: ${pixel}`);
    }
  }
});

test('an alpha byte survives a recolour, and the shape of the sprite is untouched', () => {
  const s = sprite([`${GRASS}80`, GRASS], ['', GRASS]);
  const out = A.seasonalSprite('winter', 'ground', s);
  assert.equal(out.length, 2);
  assert.equal(out[0].length, 2);
  assert.ok(out[0][0].length === 9 && out[0][0].endsWith('80'));
  assert.equal(out[1][0], '');
  assert.ok(out !== s && s[0][0] === `${GRASS}80`, 'the source sprite is never mutated');
});

test('a recolour is a pure function: the same input gives the same sprite, cached by the source sprite', () => {
  const s = sprite([GRASS, LEAF]);
  const a = A.seasonalSprite('autumn', 'canopy', s);
  assert.ok(A.seasonalSprite('autumn', 'canopy', s) === a, 'the second call is the cached sprite itself');
  assert.ok(A.seasonalSprite('winter', 'canopy', s) !== a, 'each season has its own cache');
  assert.ok(A.seasonalSprite('autumn', 'conifer', s) !== a, 'and each kind');
  const fresh = A.seasonalSprite('autumn', 'canopy', sprite([GRASS, LEAF]));
  assert.deepEqual(fresh, a);
});

test('RESTORATION FROM BLANK: a reset (the render-cache lifecycle) empties the caches, and the next use rebuilds identical sprites', () => {
  const s = sprite([GRASS, LEAF, WARM_LEAF], [TRUNK, ROCK, '']);
  const before = (['spring', 'autumn', 'winter'] as const).map((season) => structuredClone(A.seasonalSprite(season, 'canopy', s)));
  assert.ok(A.seasonCacheEntries().sprites >= 3 && A.seasonCacheEntries().pixels > 0, 'the caches are warm');
  // the SAME entry point a layout load and a restored canvas context use
  resetRenderCaches();
  assert.deepEqual(A.seasonCacheEntries(), { sprites: 0, pixels: 0, types: 0 }, 'blank: nothing survived the lifecycle reset');
  const after = (['spring', 'autumn', 'winter'] as const).map((season) => A.seasonalSprite(season, 'canopy', s));
  assert.deepEqual(after, before, 'rebuilt from the source art, identical to what was lost');
  assert.ok(A.seasonCacheEntries().sprites >= 3, 'and warm again');
});

test('THE CAPS: neither the sprite cache nor the colour memo ever holds more than CITY_SEASON_CACHE_MAX_ENTRIES', () => {
  A.resetSeasonCaches();
  const cap = C.CITY_SEASON_CACHE_MAX_ENTRIES;
  // more distinct sprites than the cap, each with a distinct colour
  for (let i = 0; i < cap + 300; i++) {
    const hex = `#${(0x102030 + i * 7).toString(16).padStart(6, '0')}`;
    A.seasonalSprite('winter', 'canopy', sprite([hex]));
    const held = A.seasonCacheEntries();
    assert.ok(held.sprites <= cap, `sprites ${held.sprites} at ${i}`);
    assert.ok(held.pixels <= cap, `pixels ${held.pixels} at ${i}`);
  }
  assert.ok(A.seasonCacheEntries().sprites > 0);
  A.resetSeasonCaches();
});

test('THE TYPE MEMO IS CAPPED TOO: a layout with more distinct furniture types than the cap never makes it hold more than the cap, and a reset empties it', () => {
  A.resetSeasonCaches();
  const cap = C.CITY_SEASON_CACHE_MAX_ENTRIES;
  for (let i = 0; i < cap + 500; i++) {
    assert.equal(A.foliageKindOf(`IMPORTED_TYPE_${i}`), null);
    assert.ok(A.seasonCacheEntries().types <= cap, `types ${A.seasonCacheEntries().types} at ${i}`);
  }
  // the answers do not change when the memo was cleared under them
  assert.equal(A.foliageKindOf('FOREST_OAK'), 'canopy');
  assert.equal(A.foliageKindOf('IMPORTED_TYPE_0'), null);
  assert.ok(A.seasonCacheEntries().types > 0);
  resetRenderCaches(); // the render-cache lifecycle
  assert.equal(A.seasonCacheEntries().types, 0);
});

test('only outdoor ground is seasonal: roads, water, walls and every indoor floor are left alone', () => {
  const seasonal = new Map<number, string | null>();
  for (const tile of Object.values(TileType)) seasonal.set(tile, A.groundKindOf(tile));
  const kinds = (k: string) => [...seasonal].filter(([, v]) => v === k).map(([t]) => t).sort((a, b) => a - b);
  assert.deepEqual(kinds('ground'), [TileType.FLOOR_10, TileType.FLOOR_11]);
  assert.deepEqual(kinds('crop'), [TileType.FLOOR_28]); // the crop field rows are their own kind (pale olive stubble in winter)
  assert.deepEqual(kinds('soil'), [TileType.FLOOR_12, TileType.FLOOR_20, TileType.FLOOR_21]);
  assert.deepEqual(kinds('hard'), [TileType.FLOOR_13, TileType.FLOOR_18, TileType.FLOOR_19, TileType.FLOOR_24]);
  for (const t of [
    TileType.WALL, TileType.FLOOR_1, TileType.FLOOR_2, TileType.FLOOR_3, TileType.FLOOR_4, TileType.FLOOR_5, TileType.FLOOR_6, TileType.FLOOR_7,
    TileType.FLOOR_8, TileType.FLOOR_9, TileType.FLOOR_25, TileType.FLOOR_26, // indoors
    TileType.FLOOR_14, TileType.FLOOR_22, TileType.FLOOR_23, // water
    TileType.FLOOR_15, TileType.FLOOR_16, TileType.FLOOR_17, TileType.FLOOR_27, // roads
    TileType.VOID,
  ]) {
    assert.equal(A.groundKindOf(t), null, `tile type ${t} is not seasonal`);
  }
});

/**
 * THE EXACT FOLIAGE SET. Authored here, in full: every furniture type the seasons recolour, with its kind. The shipped world's
 * inventory (378 distinct types) and the bundled catalogue's (every furniture id) are both checked against it, so a new tree, bush or
 * crop family can only become seasonal by a deliberate edit of this table.
 *
 * Borderline types, and the decision for each:
 *   - crop: FARM_PUMPKINS, FARM_WHEAT, FARM_CORN (field crops standing outdoors in the Farmland: fresh shoots, harvest gold, pale olive stubble);
 *   - canopy: COAST_REEDS, COAST_CATTAILS (marsh plants); FARM_APPLE_TREE (a deciduous tree); FOREST_FLOWERS, FOREST_FERN (ground foliage).
 *   - canopy, catalogue only: AUTUMN_PINE_RUSSET is a russet pine in the authored autumn palette. It is not placed in the shipped world.
 *     Its needles are warm-band pixels, so it greens in spring and frosts in winter like every other authored-autumn piece.
 *   - NOT seasonal: PLANT, PLANT_2, CACTUS, POT (potted plants stand in offices, indoors); DETAIL_PLANTER and DETAIL_GARDEN_ARCH_* (built
 *     garden structures); DETAIL_MUSHROOMS, DETAIL_LILY_POND (decor props); AQUA_SEAGRASS, AQUA_KELP, AQUA_LILY_PADS (aquarium exhibits);
 *     FARM_HAY_STACK, FARM_HAY_ROUND (dried hay does not change); FOREST_ROCK, BOULDER, STUMP, LOG and COAST_ROCKS, DRIFTWOOD (not plants).
 */
const FOLIAGE_SET: Readonly<Record<string, 'canopy' | 'conifer' | 'crop'>> = {
  AUTUMN_BIRCH_AMBER: 'canopy',
  AUTUMN_BIRCH_GOLD: 'canopy',
  AUTUMN_BUSH_AMBER: 'canopy',
  AUTUMN_BUSH_GOLD: 'canopy',
  AUTUMN_BUSH_RED: 'canopy',
  AUTUMN_BUSH_RUSSET: 'canopy',
  AUTUMN_FERN_AMBER: 'canopy',
  AUTUMN_FERN_GOLD: 'canopy',
  AUTUMN_OAK_AMBER: 'canopy',
  AUTUMN_OAK_RED: 'canopy',
  AUTUMN_OAK_RUSSET: 'canopy',
  AUTUMN_OAK_SMALL_AMBER: 'canopy',
  AUTUMN_OAK_SMALL_GOLD: 'canopy',
  AUTUMN_OAK_SMALL_RED: 'canopy',
  AUTUMN_PINE_RUSSET: 'canopy',
  COAST_CATTAILS: 'canopy',
  COAST_REEDS: 'canopy',
  FARM_APPLE_TREE: 'canopy',
  FARM_CORN: 'crop',
  FARM_PUMPKINS: 'crop',
  FARM_WHEAT: 'crop',
  FOREST_BIRCH: 'canopy',
  FOREST_BUSH: 'canopy',
  FOREST_FERN: 'canopy',
  FOREST_FLOWERS: 'canopy',
  FOREST_OAK: 'canopy',
  FOREST_OAK_SMALL: 'canopy',
  FOREST_PINE: 'conifer',
  FOREST_PINE_SMALL: 'conifer',
  FOREST_PINE_TALL: 'conifer',
  HIGHLAND_BUSH_FIR: 'conifer',
  HIGHLAND_PINE_FIR: 'conifer',
  HIGHLAND_PINE_LARCH: 'conifer',
  HIGHLAND_PINE_SMALL_FIR: 'conifer',
  HIGHLAND_PINE_SMALL_LARCH: 'conifer',
  HIGHLAND_PINE_SMALL_SPRUCE: 'conifer',
  HIGHLAND_PINE_SPRUCE: 'conifer',
  HIGHLAND_PINE_TALL_FIR: 'conifer',
  HIGHLAND_PINE_TALL_SPRUCE: 'conifer',
};
const NEVER_SEASONAL = [
  'PLANT', 'PLANT_2', 'CACTUS', 'POT', // indoor potted plants
  'DETAIL_PLANTER', 'DETAIL_GARDEN_ARCH_P1', 'DETAIL_GARDEN_ARCH_P2', 'DETAIL_GARDEN_ARCH_P3', 'DETAIL_MUSHROOMS', 'DETAIL_LILY_POND',
  'AQUA_SEAGRASS', 'AQUA_KELP', 'AQUA_LILY_PADS', 'FARM_HAY_STACK', 'FARM_HAY_ROUND',
  'FOREST_ROCK', 'FOREST_BOULDER', 'FOREST_STUMP', 'FOREST_LOG', 'COAST_ROCKS', 'COAST_DRIFTWOOD',
  // buildings, desks, vehicles and props
  'FARM_BARN', 'FARM_FARMHOUSE', 'FARM_SILO', 'FARM_WINDMILL', 'COAST_LIGHTHOUSE', 'COAST_ROWBOAT', 'CITY_TOWNHOUSE', 'CITY_TOWER_V01',
  'DETAIL_LAMP_ORNATE', 'DETAIL_TAXI', 'DETAIL_PARK_BENCH', 'DETAIL_FENCE', 'DESK_FRONT', 'SUBWAY_TRAIN', 'ANIMAL_COW_BROWN', 'AQUA_KOI',
];

let furnitureTypes: string[] = [];
let catalogIds: string[] = [];
beforeAll(() => {
  const root = path.join(__dirname, '..', '..');
  const world = JSON.parse(
    fs.readFileSync(path.join(root, 'packaging/di/worlds/living-metropolis.json'), 'utf-8'),
  ) as { furniture: Array<{ type: string }> };
  furnitureTypes = [...new Set(world.furniture.map((f) => f.type))].sort();
  // every furniture id of the bundled catalogue, read the way the catalogue loader reads the manifests
  const dir = path.join(root, 'webview-ui/public/assets/furniture');
  const ids: string[] = [];
  for (const d of fs.readdirSync(dir)) {
    const f = path.join(dir, d, 'manifest.json');
    if (!fs.existsSync(f)) continue;
    const m = JSON.parse(fs.readFileSync(f, 'utf-8')) as FurnitureManifest;
    if (m.type === 'asset') {
      ids.push(m.id);
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
    const group: ManifestGroup = {
      type: 'group',
      groupType: m.groupType as ManifestGroup['groupType'],
      rotationScheme: m.rotationScheme,
      members: m.members!,
    };
    for (const a of flattenManifest(group, inherited)) ids.push(a.id);
  }
  catalogIds = [...new Set(ids)].sort();
}, 60_000);

test('THE EXACT FOLIAGE SET: the shipped world classifies exactly these 38 types, and the whole catalogue exactly these 39; nothing else is ever seasonal', () => {
  const classified = (types: string[]) => Object.fromEntries(types.filter((t) => A.foliageKindOf(t) !== null).map((t) => [t, A.foliageKindOf(t)]));
  // the world: 378 distinct types placed, 38 of them foliage (the catalogue-only AUTUMN_PINE_RUSSET is not placed)
  assert.equal(furnitureTypes.length, 378);
  const expectedInWorld = Object.fromEntries(Object.entries(FOLIAGE_SET).filter(([t]) => t !== 'AUTUMN_PINE_RUSSET'));
  assert.deepEqual(classified(furnitureTypes), expectedInWorld);
  assert.equal(Object.keys(expectedInWorld).length, 38);
  // the catalogue: every id, 443 of them; exactly the authored set is foliage, all the rest is not
  assert.equal(catalogIds.length, 443);
  assert.deepEqual(classified(catalogIds), FOLIAGE_SET);
  assert.equal(Object.keys(FOLIAGE_SET).length, 39);
  for (const id of Object.keys(FOLIAGE_SET)) assert.ok(catalogIds.includes(id), `${id} is a real catalogue id`);
  // the placed types the world uses are all real catalogue ids too (a mirrored side variant is placed as "<id>:left")
  for (const t of furnitureTypes) assert.ok(catalogIds.includes(t.split(':')[0]), `${t} is a catalogue id`);
});

test('EXACT NEGATIVES: indoor plants, built structures, buildings, desks, vehicles, animals and props are never seasonal, and neither is anything outside the authored set', () => {
  for (const t of NEVER_SEASONAL) {
    assert.equal(A.foliageKindOf(t), null, `${t} is not foliage`);
    if (catalogIds.includes(t)) assert.ok(!(t in FOLIAGE_SET), `${t} is not in the authored set`);
  }
  // every catalogue id outside the authored set is null: nothing is seasonal by accident of its prefix
  const others = catalogIds.filter((t) => !(t in FOLIAGE_SET));
  assert.equal(others.length, 443 - 39);
  for (const t of others) assert.equal(A.foliageKindOf(t), null, `${t}`);
  // and the families a building or indoor room is made of never match at all
  for (const t of catalogIds.filter((t) => /^(CITY_|CATHEDRAL_|DESK|PC_|SUBWAY_|VEHICLE_|CUSHIONED|COFFEE|LOUNGE|BOOKSHELF|SOFA|WHITEBOARD|MUSEUM|AQUA_|ANIMAL_|DECK_|GATEWAY_|PLANT|POT|CACTUS)/.test(t)))
    assert.equal(A.foliageKindOf(t), null, `${t} is not foliage`);
});

// ── the district wash over winter ground ──────────────────────────────────────────────────────────────────────────────────────────

const washes = (): Map<string, string> => {
  const root = path.join(__dirname, '..', '..');
  const world = JSON.parse(fs.readFileSync(path.join(root, 'packaging/di/worlds/living-metropolis.json'), 'utf-8')) as {
    areas: Array<{ label: string; color: string }>;
  };
  return new Map(world.areas.map((a) => [a.label, a.color]));
};
const channels = (hex: string): number[] => [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16));

test('WINTER WEIGHT: 1 in a settled winter, the cross-fade ramp into and out of it, 0 in every other season and with no season layer', () => {
  assert.equal(A.winterWeight(null), 0);
  assert.equal(A.winterWeight({ season: 'winter', from: null, t: 1 }), 1);
  assert.equal(A.winterWeight({ season: 'winter', from: 'autumn', t: 0.25 }), 0.25); // fading in
  assert.equal(A.winterWeight({ season: 'spring', from: 'winter', t: 0.25 }), 0.75); // fading out
  for (const season of ['spring', 'summer', 'autumn'] as const) assert.equal(A.winterWeight({ season, from: null, t: 1 }), 0, season);
  assert.equal(A.winterWeight({ season: 'summer', from: 'autumn', t: 0.5 }), 0);
});

test('THE WINTER WASH, hand-computed on the shipped districts: a warm wash turns the cool snow colour, a cool, green or grey wash is the very same string, summer (weight 0) is untouched', () => {
  const area = washes();
  const snow = channels(C.CITY_WINTER_AREA_WASH_COLOR); // hand: #B8C4D2 = (184, 196, 210)
  assert.deepEqual(snow, [184, 196, 210]);
  // WARM (within 60 degrees of red): the Autumn Wildwood (hue 22), the Metropolis (30), the Deco Observatory (42), the Market Boulevard (8), the Cathedral Quarter (40)
  for (const label of ['Autumn Wildwood', 'Metropolis', 'Deco Observatory', 'Market Boulevard', 'Cathedral Quarter']) {
    const c = area.get(label)!;
    assert.deepEqual(channels(A.winterAreaWash(c, 1)), snow, `${label} at full winter is the snow colour`);
    assert.equal(A.winterAreaWash(c, 0), c, `${label} in any other season is untouched (the same string)`);
  }
  // half winter, by hand: the Autumn Wildwood #D06020 = (208, 96, 32); halfway to (184, 196, 210) is (196, 146, 121)
  assert.deepEqual(channels(A.winterAreaWash(area.get('Autumn Wildwood')!, 0.5)), [196, 146, 121]);
  // a quarter: (208 - 6, 96 + 25, 32 + 44.5 = 76.5 -> 77 on rounding half up) = (202, 121, 77)
  assert.deepEqual(channels(A.winterAreaWash(area.get('Autumn Wildwood')!, 0.25)), [202, 121, 77]);
  // NOT warm: cool, green and grey washes are the same string at full winter
  for (const label of ['Silver Pine Highlands', 'Mirror Lakes', 'North Shore', 'Harbour', 'Botanical Atrium', 'River Valley Farmland', 'Grand Boulevard']) {
    const c = area.get(label)!;
    assert.equal(A.winterAreaWash(c, 1), c, `${label} keeps its wash`);
  }
  // not a six-digit hex colour (an rgba, a three-digit hex, a word, nothing): returned as it came
  for (const odd of [C.cityRgba(10, 20, 30, 0.5), C.CITY_WINTER_AREA_WASH_COLOR.slice(0, 4), 'red', '']) assert.equal(A.winterAreaWash(odd, 1), odd);
  // a weight above 1 is held at 1
  assert.deepEqual(channels(A.winterAreaWash(area.get('Autumn Wildwood')!, 3)), snow);
});

// ── round 3: the colour memo never exceeds its cap, at any insertion ───────────────────────────────────────────────────────────

/** A source sprite of `n` DISTINCT colours (one row), each a six-digit hex colour no other call uses when `base` differs. */
const distinctSprite = (base: number, n: number): SpriteData => [Array.from({ length: n }, (_, i) => `#${(0x100000 + base + i).toString(16).toUpperCase().padStart(6, '0')}`)];

test('THE COLOUR MEMO NEVER EXCEEDS ITS CAP: starting one below the cap, recolouring a sprite with several new colours leaves it at or below the cap, and the output equals an uncapped recolour', () => {
  const CAP = C.CITY_SEASON_CACHE_MAX_ENTRIES;
  const season = 'autumn' as const;
  const kind = 'ground' as const;
  resetRenderCaches();
  // fill the (autumn, ground) colour memo to exactly CAP - 1 with distinct colours, in sprites of at most 256 pixels
  let have = 0;
  while (have < CAP - 1) {
    const n = Math.min(256, CAP - 1 - have);
    A.seasonalSprite(season, kind, distinctSprite(have, n));
    have += n;
  }
  assert.equal(A.seasonCacheEntries().pixels, CAP - 1, 'the memo starts one below the cap');
  // one sprite with FIVE new colours would take it to CAP + 4 if only the start of the sprite were checked (the round-3 defect: 2,047 -> 2,049 with two)
  const multi = distinctSprite(0x80000, 5);
  const capped = A.seasonalSprite(season, kind, multi);
  assert.ok(A.seasonCacheEntries().pixels <= CAP, `the memo holds ${A.seasonCacheEntries().pixels}, above the cap ${CAP}`);
  // the output is exactly what an UNCAPPED recolour gives (a fresh cache, so nothing is ever evicted)
  resetRenderCaches();
  const uncapped = A.seasonalSprite(season, kind, multi);
  assert.deepEqual(capped, uncapped);
  // the invariant holds after EVERY call across many multi-colour sprites, the memo wrapping around its cap more than once
  resetRenderCaches();
  let worst = 0;
  for (let i = 0; i < 40; i++) {
    A.seasonalSprite(season, kind, distinctSprite(0x10000 + i * 300, 200));
    worst = Math.max(worst, A.seasonCacheEntries().pixels);
  }
  assert.ok(worst <= CAP, `the largest the memo ever stood after a call was ${worst} (cap ${CAP})`);
  // and the two-colour case of the review, exactly: from CAP - 1, two new colours
  resetRenderCaches();
  for (let have2 = 0; have2 < CAP - 1; ) {
    const n = Math.min(256, CAP - 1 - have2);
    A.seasonalSprite(season, kind, distinctSprite(0x200000 + have2, n));
    have2 += n;
  }
  A.seasonalSprite(season, kind, distinctSprite(0x300000, 2));
  assert.ok(A.seasonCacheEntries().pixels <= CAP);
});
