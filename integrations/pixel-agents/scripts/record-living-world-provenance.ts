/**
 * LIVING-WORLD ART PROVENANCE (M3 S6): one record per delivered source sheet, written to
 * packaging/di/LIVING-WORLD-PROVENANCE.json from the SUPPLIED inputs (read only; never modified):
 * - provenance: generator, the original generated file, generation time when recorded, the full
 *   prompt and its sha256, and the sheet's sha256 RE-HASHED here from the bytes (it must equal the
 *   supplied record, or this script fails);
 * - inspection: the art-QA record for the same sheet (LIVING-WORLD-ART-QA.json): segmentation,
 *   alpha census, cells extracted and alpha-verified, and (character and vehicle sheets) the visual
 *   facing adjudication on a named cue;
 * - runtime: what the sheet became (an ambient character sheet id, or catalogue asset ids).
 *   npx tsx scripts/record-living-world-provenance.ts <supplied assets dir>
 */
import { createHash } from 'node:crypto';
import fs from 'node:fs';
import path from 'node:path';

const dir = process.argv[2];
if (!dir || !path.isAbsolute(dir)) throw new Error('usage: record-living-world-provenance.ts <absolute supplied assets dir>');
const ROOT = path.join(__dirname, '..');
const sources = JSON.parse(fs.readFileSync(path.join(dir, 'LIVING-WORLD-ASSET-SOURCES.json'), 'utf-8')) as Array<Record<string, unknown>>;
const qa = JSON.parse(fs.readFileSync(path.join(ROOT, 'packaging/di/LIVING-WORLD-ART-QA.json'), 'utf-8')) as { sheets: Array<Record<string, unknown>> };
const sha = (b: Buffer | string) => createHash('sha256').update(b).digest('hex');

const records = sources.map((src) => {
  const file = path.basename(String(src.file));
  const bytes = fs.readFileSync(path.join(dir, file));
  const rehashed = sha(bytes);
  if (rehashed !== src.sha256) throw new Error(`${file}: re-hashed ${rehashed} differs from the supplied ${String(src.sha256)}`);
  const q = qa.sheets.find((s) => s.file === file);
  if (!q) throw new Error(`${file}: no art-QA (inspection) record`);
  const prompt = typeof src.prompt === 'string' ? src.prompt : null;
  const facing = q.facing as Record<string, unknown> | undefined;
  const assets = (q.assets as Array<{ id: string }> | undefined) ?? [];
  return {
    sheet: String(src.sheet_id ?? q.id),
    pack: src.pack,
    file,
    sha256: rehashed,
    bytes: bytes.length,
    provenance: {
      generator: src.generator,
      original: src.source_original ?? src.source ?? null,
      generatedAt: src.generated_at ?? null,
      prompt,
      promptSha256: prompt ? sha(prompt) : null,
      contract: src.contract ?? null,
      subject: src.subject ?? null,
    },
    inspection: {
      record: `LIVING-WORLD-ART-QA.json#${String(q.id)}`,
      segmentation: q.segmentation,
      cells: q.cells ?? null,
      alphaCutoff: q.alphaCutoff,
      zeroedHiddenRgb: q.zeroedHiddenRgb,
      facing: facing ? { basis: facing.basis, cue: facing.cue ?? null, rows: facing.adjudicatedSourceRowOrder ?? null } : null,
    },
    runtime: q.runtimeId ? { ambientSheet: q.runtimeId } : { assets: assets.map((a) => a.id) },
  };
});
const out = {
  kind: 'living-world art provenance (M3 S6)',
  generatedBy: 'scripts/record-living-world-provenance.ts',
  note: 'Supplied inputs are read only. Every sheet hash below was re-hashed from the bytes and equals the supplied record. Original, fan-inspired generations: no third-party pack, no affiliation.',
  sheets: records.length,
  uninspected: records.filter((r) => !r.inspection.segmentation).length,
  records,
};
fs.writeFileSync(path.join(ROOT, 'packaging/di/LIVING-WORLD-PROVENANCE.json'), `${JSON.stringify(out, null, 2)}\n`);
console.log(JSON.stringify({ sheets: out.sheets, uninspected: out.uninspected }));
