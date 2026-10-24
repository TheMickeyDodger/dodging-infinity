/**
 * AC7, boundary A: the office server's own explanatory wording (reception category statements and cause labels)
 * is THIS PROJECT'S copy. Arriving over the wire does not exempt it from the copy rule (no em or en dash), so the
 * webview's presentation layer shows it in plain punctuation. The server's files and behaviour are unchanged: the
 * wire and the state keep the office's wording exactly as sent.
 *
 * Only what is out of the rule is left alone, byte for byte: record values and identifiers (cause details, refs),
 * what a person wrote, and code-review source and diff bytes. Those never pass through the wording map.
 */
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';

import type { DiReceptionEntry } from '../../core/src/messages.ts';
import { EntryDetail } from '../src/components/DiReceptionEntryDetail.tsx';
import {
  firstLayerHappened,
  PLAIN_SERVER_WORDING,
  plainCauses,
  plainServerWording,
} from '../src/components/diReceptionPlain.ts';

const here = path.dirname(fileURLToPath(import.meta.url));
const DESK = path.resolve(here, '../../server/src/di/receptionDesk.ts');
const DASH = /[–—]/;

/** Every string literal in the reception desk that carries a long dash, with `'a' + 'b'` joined (comments removed). */
function deskDashLiterals(): string[] {
  const src = fs
    .readFileSync(DESK, 'utf-8')
    .replace(/\/\*[\s\S]*?\*\//g, '')
    .replace(/^\s*\/\/.*$/gm, '')
    .replace(/'\s*\+\s*'/g, '');
  const out: string[] = [];
  for (const m of src.matchAll(/'((?:[^'\\\n]|\\.)*)'/g)) if (DASH.test(m[1])) out.push(m[1]);
  return out;
}

const fixtureEntry = (): DiReceptionEntry => {
  const doc = JSON.parse(
    fs.readFileSync(path.join(here, 'fixtures/di-office-state-evidence-historical-v2.FIXTURE.json'), 'utf-8'),
  ) as { states: { live: { reception: { entries: DiReceptionEntry[] } } } };
  const entry = doc.states.live.reception.entries.find((e) =>
    e.causes.some((c) => c.cause === 'reported_complete_not_verified'),
  );
  if (!entry) throw new Error('the fixture no longer carries a reported_complete_not_verified entry');
  return entry;
};

describe('the office server explanatory wording, as shown', () => {
  it('every dash-carrying sentence the reception desk sends has a reviewed plain wording, and no reviewed wording is stale', () => {
    const literals = deskDashLiterals();
    expect(literals.length, 'the desk sends dash-carrying wording today').toBe(4);
    expect([...literals].sort()).toEqual(Object.keys(PLAIN_SERVER_WORDING).sort());
  });

  it('each reviewed wording has no long dash and keeps every term of the original (identifiers and capitalised words)', () => {
    for (const [sent, shown] of Object.entries(PLAIN_SERVER_WORDING)) {
      expect(shown, sent).not.toMatch(DASH);
      for (const term of sent.match(/[a-z]+_[a-z_]+|[A-Z]{3,}|Mission Core|DI/g) ?? [])
        expect(shown, `${term} kept`).toContain(term);
    }
  });

  it('known sentences are shown in their reviewed wording; any other dash becomes a colon; dash-free wording is returned unchanged', () => {
    for (const [sent, shown] of Object.entries(PLAIN_SERVER_WORDING)) expect(plainServerWording(sent)).toBe(shown);
    expect(plainServerWording('A new statement — not reviewed yet')).toBe('A new statement: not reviewed yet');
    expect(plainServerWording('from one–two')).toBe('from one: two');
    const plain = 'No catalog answer: nothing here may be read as "none".';
    expect(plainServerWording(plain)).toBe(plain);
  });

  it('a cause the plain map does not name falls back to its label, shown without a long dash', () => {
    const entry = {
      ...fixtureEntry(),
      causes: [{ cause: 'a_cause_not_mapped', label: 'Something new — to look at', details: [], staleReasons: [] }],
    } as unknown as DiReceptionEntry;
    expect(plainCauses(entry).map((c) => c.happened)).toEqual(['Something new: to look at']);
    expect(firstLayerHappened(entry).lines).toEqual(['Something new: to look at']);
  });

  it('the technical disclosure shows the cause label without a long dash, and the record values exactly as received', () => {
    const entry = fixtureEntry();
    const cause = entry.causes.find((c) => c.cause === 'reported_complete_not_verified')!;
    expect(cause.label, 'the state keeps the office wording as sent').toMatch(DASH);
    const html = renderToStaticMarkup(createElement(EntryDetail, { entry, fixture: true }));
    expect(html).not.toMatch(DASH);
    expect(html).toContain(PLAIN_SERVER_WORDING[cause.label]);
    for (const d of cause.details) expect(html).toContain(`${d.label}: ${d.value ?? 'not recorded'}`);
  });
});
