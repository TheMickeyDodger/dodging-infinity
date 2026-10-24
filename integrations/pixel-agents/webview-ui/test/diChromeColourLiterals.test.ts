import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

import { test } from 'vitest';

import { rawColourLiterals } from './diTokenScope.ts';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const read = (file: string) => fs.readFileSync(path.join(root, file), 'utf-8');

/*
 * The chrome stylesheets must style controls with the shared design tokens, never with a raw colour (AC1, brief 2.1). Two guards were blind to this:
 * pixel-agents/no-inline-colors reads TS and TSX only, and the computed token-scope audit (diTokenScope.ts) checks tokens a rule CONSUMES, so a hex
 * written straight into a declaration consumed no token and passed both. This reads the stylesheets themselves.
 *
 * SCOPE, stated plainly: the three DI chrome stylesheets. index.css holds the token definitions and the world's own pixel language and is not
 * scanned here. The content palette of the review dialog (diff add/delete colours, syntax highlighting, statuses) is DEFINED with raw colours as
 * custom properties; those are pinned by name below, so a new raw palette entry needs a deliberate change here.
 */
const CHROME_STYLESHEETS = [
  'src/components/diOperationalPanel.css',
  'src/components/diMissionRequest.css',
  'src/components/diCodeReview.css',
];

/** The custom properties allowed to hold a raw colour, and why: the review dialog's CONTENT palette (diff semantics and syntax colours), contrast-tested. */
const RAW_PALETTE_DEFINITIONS = [
  '--rv-add-bg',
  '--rv-add-gutter',
  '--rv-add-mark',
  '--rv-del-bg',
  '--rv-del-gutter',
  '--rv-del-mark',
  '--rv-ctx-bg',
  '--rv-ctx-gutter',
  '--rv-hunk-bg',
  '--rv-hunk-text',
  '--rv-empty-bg',
  '--rv-st-added',
  '--rv-st-removed',
  '--rv-st-changed',
  '--rv-st-renamed',
  '--rv-st-new',
  '--rv-st-conflict',
  '--rv-tok-keyword',
  '--rv-tok-string',
  '--rv-tok-comment',
  '--rv-tok-number',
  '--rv-tok-type',
  '--rv-tok-function',
  '--rv-tok-punct',
  '--rv-tok-property',
  '--rv-tok-tag',
  '--rv-tok-attr',
  '--rv-ctl-bg',
  '--rv-ctl-text',
  '--rv-scroll-thumb',
  '--rv-scroll-track',
];

test('rawColourLiterals has TEETH: a raw colour written into a declaration is flagged in every form, tokens and keywords are not, a palette definition is reported separately, an alpha mask and a comment are exempt', () => {
  // the sample stylesheet and the expected rows live in fixtures (not TS): the project's own colour rule would object to colour text in test source
  const sample = read('test/fixtures/colour-literals.FIXTURE.css');
  const expected = JSON.parse(read('test/fixtures/colour-literals.FIXTURE.EXPECTED.json')) as string[];
  const found = rawColourLiterals(sample).map(
    (x) => `${x.kind}|${x.selector}|${x.property}|${x.literal}|line ${x.line}`,
  );
  assert.deepEqual(found, expected);
});

test('AC1 / brief 2.1: no chrome stylesheet styles an element with a raw colour literal (controls consume the shared tokens)', () => {
  const violations: string[] = [];
  for (const file of CHROME_STYLESHEETS)
    for (const x of rawColourLiterals(read(file)))
      if (x.kind === 'direct')
        violations.push(`${file}:${x.line} ${x.selector} { ${x.property}: ... ${x.literal} }`);
  assert.deepEqual(
    violations,
    [],
    'every colour in a chrome rule is a token (var(--...)), not a raw literal',
  );
});

test("the raw colours defined as custom properties are exactly the review dialog's content palette: a new raw palette entry needs a deliberate change here", () => {
  const defined = new Set<string>();
  for (const file of CHROME_STYLESHEETS)
    for (const x of rawColourLiterals(read(file)))
      if (x.kind === 'definition') defined.add(x.property);
  assert.deepEqual([...defined].sort(), [...RAW_PALETTE_DEFINITIONS].sort());
});
