/**
 * The webview's DESIGN.md and PRODUCT.md are COPIES of the repository root's files of the same names, which are canonical. They exist because
 * Impeccable finds both by walking up from the scanned or targeted file, and it stops at the first directory holding a project marker (here
 * `webview-ui/package.json`), so the root files alone are never applied to webview files (docs/design-workflow.md). A symlink was rejected: with
 * `core.symlinks=false` Git checks it out as a text file holding the target path. One mechanism for both files: edit the root file, then copy
 * it here byte for byte.
 */
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

import { test } from 'vitest';

const webview = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');

for (const name of ['DESIGN.md', 'PRODUCT.md']) {
  const canonical = path.resolve(webview, '../../..', name);
  const copy = path.join(webview, name);
  test(`webview-ui/${name} is a regular file, byte-identical to the canonical root ${name}`, () => {
    assert.equal(fs.lstatSync(copy).isSymbolicLink(), false, `webview-ui/${name} must be a regular file, not a symlink`);
    assert.ok(
      fs.readFileSync(copy).equals(fs.readFileSync(canonical)),
      `webview-ui/${name} has drifted from the root ${name}: copy the root file over it (the root file is canonical)`,
    );
  });
}
