/**
 * Deliverable 2, round 10, ruling 4: the archive's raw count (`DiArchiveReading.countedFromReadSources`, `number | null`) is guarded by ONE reader, not by
 * convention. Seven defects in this work were a third state folded into a confident one by a reader who missed a convention; this pin fails the moment any
 * source file other than the declaration, its producer (`archiveReading`) and its accessor (`archiveCount`) names the raw field, so the next unguarded use is
 * caught by a test rather than by review. Consumers read `archiveCount`, which makes them say which of exact, at least, or not reported they have.
 */
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

import { describe, expect, it } from 'vitest';

import type { DiRetainedSource } from '../../core/src/messages.ts';
import { archiveCount, archiveReading } from '../src/components/diScanPagesModel.ts';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const FIELD = 'countedFromReadSources';
const MODEL = 'src/components/diScanPagesModel.ts';
/** Where the raw field may be named: its declaration, its producer and its one reader. */
const ALLOWED = ['export interface DiArchiveReading', 'export function archiveReading(', 'export function archiveCount('];

/** Comments blanked to spaces (offsets kept), so a doc comment naming the field is not a use. */
const code = (text: string) => text.replace(/\/\*[\s\S]*?\*\//g, (m) => m.replace(/[^\n]/g, ' ')).replace(/\/\/[^\n]*/g, (m) => ' '.repeat(m.length));

/** The [start, end) span of the block a header opens: from the header to its matching closing brace. */
function blockSpan(text: string, header: string): [number, number] | null {
  const at = text.indexOf(header);
  if (at < 0) return null;
  let depth = 0;
  for (let i = text.indexOf('{', at); i >= 0 && i < text.length; i += 1) {
    if (text[i] === '{') depth += 1;
    else if (text[i] === '}' && (depth -= 1) === 0) return [at, i + 1];
  }
  return null;
}

/** Every place a source names the raw field outside the allowed blocks, as `file:line`. */
export function unguardedUses(files: ReadonlyArray<{ rel: string; text: string }>): string[] {
  const out: string[] = [];
  for (const { rel, text } of files) {
    const stripped = code(text);
    const spans = rel === MODEL ? ALLOWED.map((header) => blockSpan(stripped, header)).filter((s): s is [number, number] => s !== null) : [];
    for (const match of stripped.matchAll(new RegExp(`\\b${FIELD}\\b`, 'g'))) {
      const at = match.index;
      if (spans.some(([start, end]) => at >= start && at < end)) continue;
      out.push(`${rel}:${stripped.slice(0, at).split('\n').length}`);
    }
  }
  return out;
}

function sources(dir: string): Array<{ rel: string; text: string }> {
  const out: Array<{ rel: string; text: string }> = [];
  for (const entry of fs.readdirSync(path.join(root, dir), { withFileTypes: true })) {
    const rel = `${dir}/${entry.name}`;
    if (entry.isDirectory()) out.push(...sources(rel));
    else if (/\.(ts|tsx)$/.test(entry.name)) out.push({ rel, text: fs.readFileSync(path.join(root, rel), 'utf-8') });
  }
  return out;
}

describe('the archive count has one reader: no unguarded use of the raw field anywhere in the webview source', () => {
  it('every source file names the raw field only inside its declaration, producer and accessor', () => {
    const all = sources('src');
    expect(all.length).toBeGreaterThan(50);
    expect(unguardedUses(all)).toEqual([]);
    // and the three allowed blocks are really there (a rename cannot make the pin vacuous)
    const model = code(fs.readFileSync(path.join(root, MODEL), 'utf-8'));
    for (const header of ALLOWED) expect(blockSpan(model, header), header).not.toBeNull();
  });

  it('the pin bites: a consumer reading the raw field, or a use outside the allowed blocks, is caught', () => {
    expect(unguardedUses([{ rel: 'src/components/DiScanPages.tsx', text: 'const n = archive.countedFromReadSources ?? 0;' }])).toEqual([
      'src/components/DiScanPages.tsx:1',
    ]);
    const model = [
      'export interface DiArchiveReading {\n  countedFromReadSources: number | null;\n}',
      'export function archiveCount(a: DiArchiveReading) {\n  return a.countedFromReadSources;\n}',
      '// a comment naming countedFromReadSources is not a use',
      'export function missionsEmptyState(a: DiArchiveReading) {\n  return (a.countedFromReadSources ?? 0) > 0;\n}',
    ].join('\n');
    expect(unguardedUses([{ rel: MODEL, text: model }])).toEqual([`${MODEL}:9`]); // the return line inside missionsEmptyState
  });
});

describe('archiveCount says which count it has: exact, at least, or not reported', () => {
  const observed = (total: number): DiRetainedSource => ({ sourceId: 'a', state: 'observed', problem: null, total, awaiting: 0, missions: [] });
  const unreadable: DiRetainedSource = { sourceId: 'b', state: 'unavailable', problem: 'mission_retention_unreadable', total: null, awaiting: null, missions: [] };
  it('not reported: no archive at all is never a 0', () => {
    expect(archiveCount(archiveReading(null, []))).toEqual({ kind: 'not_reported' });
  });
  it('exact: every source read, including a genuine 0', () => {
    expect(archiveCount(archiveReading([observed(2)], []))).toEqual({ kind: 'exact', count: 2 });
    expect(archiveCount(archiveReading([observed(0)], []))).toEqual({ kind: 'exact', count: 0 });
  });
  it('at least: a source that could not be read makes the count a floor, with that source named', () => {
    expect(archiveCount(archiveReading([observed(1), unreadable], []))).toEqual({
      kind: 'at_least',
      count: 1,
      unavailable: [{ sourceId: 'b', problem: 'mission_retention_unreadable' }],
    });
  });
});
