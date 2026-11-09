/**
 * B2c, the Lead's ruling (C), ALONGSIDE (A): a GUARD, NOT PROOF. It flags the commonest authoring mistake early, an AnchoredPopover used DIRECTLY in a
 * source file that itself states a modal dialog (aria-modal="true"). It cannot see a popover rendered by a CHILD of a modal (any depth), a dialog that
 * does not state that literal, or anything decided at runtime, so passing it proves NOTHING about runtime focus. The behaviour itself is (A), in
 * diPopover.ts, rendered on a test-only surface in diPopoverInModalBrowser.test.ts. The label travels with the test: it is in the failure message below.
 */
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

import { describe, expect, it } from 'vitest';

const src = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../src');

const GUARD_MESSAGE =
  'GUARD, NOT PROOF. This check sees only a DIRECT use of AnchoredPopover in a source file that itself states aria-modal="true". It cannot see a ' +
  'popover rendered by a child of a modal (at any depth), a dialog that does not state that literal, or anything decided at runtime: PASSING IT ' +
  'PROVES NOTHING ABOUT RUNTIME FOCUS. A popover inside a modal is handled by diPopover.ts (ruling (A)): a focus-returning close whose trigger cannot ' +
  'take focus returns focus to the popover\'s own modal, EXCEPT a popover portalled out of its modal\'s subtree and a dialog that cannot itself take ' +
  'focus. To use one here: give that surface its own rendered test of those cases, then list the file in ACKNOWLEDGED with that test.';

/** Files allowed to use a popover directly in a modal, each with the rendered test that covers it there. None today. */
const ACKNOWLEDGED: Record<string, string> = {};

function walk(dir: string, base = ''): string[] {
  const found: string[] = [];
  for (const entry of fs.readdirSync(path.join(dir, base), { withFileTypes: true })) {
    const rel = base === '' ? entry.name : `${base}/${entry.name}`;
    if (entry.isDirectory()) found.push(...walk(dir, rel));
    else if (/\.tsx?$/.test(entry.name)) found.push(rel);
  }
  return found.sort();
}
const withoutComments = (text: string) => text.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^[ \t]*\/\/.*$/gm, '');
/** The guard's whole detector: a modal stated, and a popover used, in the same file's code (comments stripped). */
function directUseInModal(text: string): boolean {
  const code = withoutComments(text);
  return /aria-modal=(?:"true"|\{true\}|\{'true'\})/.test(code) && /<AnchoredPopover\b|createElement\(\s*AnchoredPopover\b/.test(code);
}

describe('a GUARD (not proof): no anchored popover used DIRECTLY in a source file that states a modal dialog', () => {
  it('positive control: the detector flags a direct use, and only a direct use in code (so a pass is not vacuous)', () => {
    expect(directUseInModal('<div role="dialog" aria-modal="true"><AnchoredPopover label="x" /></div>')).toBe(true);
    expect(directUseInModal('<div aria-modal={true}>{createElement(AnchoredPopover, {})}</div>')).toBe(true);
    expect(directUseInModal('<AnchoredPopover label="x" />')).toBe(false);
    expect(directUseInModal('<div aria-modal="true" />')).toBe(false);
    expect(directUseInModal('/* <AnchoredPopover /> */ <div aria-modal="true" />')).toBe(false);
  });

  it('GUARD: no source file states aria-modal="true" and uses AnchoredPopover directly, unless ACKNOWLEDGED with its rendered test', () => {
    const files = walk(src);
    expect(files.length, 'the sweep read the source tree').toBeGreaterThan(50);
    const offenders = files.filter((f) => !(f in ACKNOWLEDGED) && directUseInModal(fs.readFileSync(path.join(src, f), 'utf-8')));
    expect(offenders, GUARD_MESSAGE).toEqual([]);
    for (const [file, renderedTest] of Object.entries(ACKNOWLEDGED))
      expect(fs.existsSync(path.join(src, '..', 'test', renderedTest)), `${file}: its rendered test ${renderedTest} exists`).toBe(true);
  });
});
