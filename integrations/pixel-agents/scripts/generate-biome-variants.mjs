#!/usr/bin/env node
/**
 * BIOME VARIANT SPRITES for the metropolis world's added forests: new ids, new files — the shared
 * FOREST_* originals (scripts/generate-forest-assets.mjs, used by the preserved forest) are only
 * READ, never written.
 *
 *   node scripts/generate-biome-variants.mjs            # (re)write
 *   node scripts/generate-biome-variants.mjs --check    # verify the committed bytes match
 *
 * SELECTIVE RECOLOUR (why not a per-item hue override): rotating a whole sprite's hue by -50..-90°
 * turns a green canopy amber but drags the BROWN bark (hue ~20-40°) backwards into magenta and
 * purple. Here only FOLIAGE pixels move: a pixel is foliage when its hue lies in the green band
 * FOLIAGE_HUE (60..175°) with saturation >= FOLIAGE_SAT; bark, trunks, stumps, logs, rocks and the
 * outline keep their exact RGB. Each foliage pixel keeps its own lightness ordering (so the art's
 * shading survives) and takes the scheme's hue, with a small hue spread from its original hue and
 * the scheme's saturation factor.
 *
 * Output: webview-ui/public/assets/furniture/<ID>/{<ID>.png, manifest.json}, 'decor' (no seats),
 * footprint and background rows identical to the source sprite.
 */
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

import { PNG } from 'pngjs';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const DIR = path.join(ROOT, 'webview-ui/public/assets/furniture');
const CHECK = process.argv.includes('--check');
const FOLIAGE_HUE = [60, 175];
const FOLIAGE_SAT = 0.12;

/** [prefix, scheme, target hue, saturation factor, lightness shift, hue spread, sources] */
const SCHEMES = [
  ['AUTUMN', 'RED', 6, 1.25, 0.0, 0.18, ['OAK', 'OAK_SMALL', 'BUSH']],
  ['AUTUMN', 'AMBER', 26, 1.3, 0.03, 0.15, ['OAK', 'OAK_SMALL', 'BIRCH', 'BUSH', 'FERN']],
  ['AUTUMN', 'GOLD', 46, 1.25, 0.06, 0.12, ['BIRCH', 'OAK_SMALL', 'FERN', 'BUSH']],
  ['AUTUMN', 'RUSSET', 16, 0.85, -0.04, 0.15, ['OAK', 'PINE', 'BUSH']],
  ['HIGHLAND', 'SPRUCE', 168, 0.75, -0.02, 0.1, ['PINE_TALL', 'PINE', 'PINE_SMALL']],
  ['HIGHLAND', 'FIR', 150, 0.55, 0.04, 0.1, ['PINE_TALL', 'PINE', 'PINE_SMALL', 'BUSH']],
  ['HIGHLAND', 'LARCH', 58, 0.7, 0.05, 0.1, ['PINE', 'PINE_SMALL']],
];

function rgbToHsl(r, g, b) {
  r /= 255;
  g /= 255;
  b /= 255;
  const max = Math.max(r, g, b);
  const min = Math.min(r, g, b);
  const l = (max + min) / 2;
  if (max === min) return [0, 0, l];
  const d = max - min;
  const s = l > 0.5 ? d / (2 - max - min) : d / (max + min);
  let h;
  if (max === r) h = (g - b) / d + (g < b ? 6 : 0);
  else if (max === g) h = (b - r) / d + 2;
  else h = (r - g) / d + 4;
  return [h * 60, s, l];
}
function hslToRgb(h, s, l) {
  h = ((h % 360) + 360) % 360;
  const c = (1 - Math.abs(2 * l - 1)) * s;
  const x = c * (1 - Math.abs(((h / 60) % 2) - 1));
  const m = l - c / 2;
  const [r, g, b] =
    h < 60 ? [c, x, 0] : h < 120 ? [x, c, 0] : h < 180 ? [0, c, x] : h < 240 ? [0, x, c] : h < 300 ? [x, 0, c] : [c, 0, x];
  return [Math.round((r + m) * 255), Math.round((g + m) * 255), Math.round((b + m) * 255)];
}

let mismatches = 0;
let written = 0;
const out = (file, bytes) => {
  if (CHECK) {
    if (!fs.existsSync(file) || Buffer.compare(fs.readFileSync(file), bytes) !== 0) {
      console.error(`MISMATCH ${path.relative(ROOT, file)}`);
      mismatches++;
    }
  } else fs.writeFileSync(file, bytes);
};
const report = [];
for (const [prefix, scheme, hue, satF, lShift, spread, sources] of SCHEMES)
  for (const base of sources) {
    const srcId = `FOREST_${base}`;
    const id = `${prefix}_${base}_${scheme}`;
    const manifest = JSON.parse(fs.readFileSync(path.join(DIR, srcId, 'manifest.json'), 'utf-8'));
    const png = PNG.sync.read(fs.readFileSync(path.join(DIR, srcId, `${srcId}.png`)));
    let foliage = 0;
    let kept = 0;
    for (let i = 0; i < png.data.length; i += 4) {
      if (!png.data[i + 3]) continue;
      const [h, s, l] = rgbToHsl(png.data[i], png.data[i + 1], png.data[i + 2]);
      if (h >= FOLIAGE_HUE[0] && h <= FOLIAGE_HUE[1] && s >= FOLIAGE_SAT) {
        const [r, g, b] = hslToRgb(hue + (h - 110) * spread, Math.min(1, s * satF), Math.max(0, Math.min(1, l + lShift)));
        png.data[i] = r;
        png.data[i + 1] = g;
        png.data[i + 2] = b;
        foliage++;
      } else kept++;
    }
    const dir = path.join(DIR, id);
    if (!CHECK) fs.mkdirSync(dir, { recursive: true });
    out(path.join(dir, `${id}.png`), PNG.sync.write(png, { colorType: 6 }));
    out(
      path.join(dir, 'manifest.json'),
      Buffer.from(
        JSON.stringify(
          { ...manifest, id, name: `${manifest.name} (${prefix.toLowerCase()} ${scheme.toLowerCase()})`, category: 'decor' },
          null,
          2,
        ) + '\n',
      ),
    );
    report.push(`${id}: foliage ${foliage} px recoloured, ${kept} px kept (bark/outline/rock)`);
    written++;
  }
if (CHECK) {
  console.log(mismatches ? `${mismatches} mismatch(es)` : `${written} biome variants match`);
  process.exit(mismatches ? 1 : 0);
}
console.log(report.join('\n'));
console.log(`wrote ${written} biome variants (new ids; FOREST_* originals only read)`);
