#!/usr/bin/env node
/**
 * LOUNGE SEATING: DECOR copies of the bundled Pixel Agents seating (SOFA, CUSHIONED_CHAIR,
 * WOODEN_CHAIR, CUSHIONED_BENCH) for the metropolis world's decorated offices and gathering rooms.
 *
 *   node scripts/generate-lounge-seating.mjs            # (re)write
 *   node scripts/generate-lounge-seating.mjs --check    # verify the committed bytes match
 *
 * WHY: a 'chairs' item creates a SEAT, and seats are where bound agents sit and what anchors the
 * palace home (worldViews.officeRegion). Seating drawn in a satellite office must therefore NOT be a
 * chair: each LOUNGE_<X> type is the SAME art (PNG bytes copied unchanged) under a new id in the
 * 'decor' category — furniture to look at, never a seat. Same licence and credits as the originals
 * (bundled Pixel Agents assets); rotation groups keep their mirrored ':left' sides.
 */
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const DIR = path.join(ROOT, 'webview-ui/public/assets/furniture');
const CHECK = process.argv.includes('--check');
const SOURCES = ['SOFA', 'CUSHIONED_CHAIR', 'WOODEN_CHAIR', 'CUSHIONED_BENCH'];

const rename = (id) => `LOUNGE_${id}`;
function convert(node) {
  const out = { ...node };
  if (out.id) out.id = rename(out.id);
  if (out.file) out.file = rename(out.file.replace(/\.png$/, '')) + '.png';
  if (out.category) out.category = 'decor';
  if (out.name) out.name = `Lounge ${out.name}`;
  if (out.members) out.members = out.members.map(convert);
  return out;
}

let mismatches = 0;
const write = (file, bytes) => {
  if (CHECK) {
    if (!fs.existsSync(file) || Buffer.compare(fs.readFileSync(file), bytes) !== 0) {
      console.error(`MISMATCH ${path.relative(ROOT, file)}`);
      mismatches++;
    }
  } else fs.writeFileSync(file, bytes);
};
for (const id of SOURCES) {
  const src = path.join(DIR, id);
  const manifest = JSON.parse(fs.readFileSync(path.join(src, 'manifest.json'), 'utf-8'));
  const dst = path.join(DIR, rename(id));
  if (!CHECK) fs.mkdirSync(dst, { recursive: true });
  write(path.join(dst, 'manifest.json'), Buffer.from(JSON.stringify(convert(manifest), null, 2) + '\n'));
  for (const f of fs.readdirSync(src).filter((x) => x.endsWith('.png')))
    write(path.join(dst, rename(f.replace(/\.png$/, '')) + '.png'), fs.readFileSync(path.join(src, f)));
}
if (CHECK) {
  console.log(mismatches ? `${mismatches} mismatch(es)` : 'lounge seating matches');
  process.exit(mismatches ? 1 : 0);
}
console.log(`wrote ${SOURCES.map(rename).join(', ')} (decor; art copied unchanged)`);
