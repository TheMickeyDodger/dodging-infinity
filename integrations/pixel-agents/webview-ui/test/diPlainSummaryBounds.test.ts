/**
 * Product experience: the plain first layer's bounds, PINNED to their exact values — the
 * repository's convention (ambientDuty.test.ts, "the chosen bounds are pinned to their exact
 * values"). A change to any of them fails here, loudly, so it can only be a deliberate decision.
 * The RELATIVE checks — that the output respects the bound, whatever it is — stay in
 * diPlainSummary.test.ts; the two check different things.
 */
import assert from 'node:assert/strict';

import { test } from 'vitest';

import {
  DI_MISSION_TITLE_MAX_CHARS,
  DI_SHORT_REF_FULL_MAX_CHARS,
  DI_SHORT_REF_MIN_CHARS,
} from '../src/constants.ts';

const report = (k: string, v: unknown) => console.log(`[bounds] ${JSON.stringify({ [k]: v })}`);

test('the plain first layer’s chosen bounds are pinned to their exact values', () => {
  report('di.plainFirstLayer', {
    DI_MISSION_TITLE_MAX_CHARS,
    DI_SHORT_REF_FULL_MAX_CHARS,
    DI_SHORT_REF_MIN_CHARS,
  });
  // A Mission title: at most 90 characters, the ellipsis included.
  assert.equal(DI_MISSION_TITLE_MAX_CHARS, 90);
  // A native id is shown whole up to 24 characters, else as its shortest unique prefix...
  assert.equal(DI_SHORT_REF_FULL_MAX_CHARS, 24);
  // ...which never keeps fewer than 11 (the office zone label's length).
  assert.equal(DI_SHORT_REF_MIN_CHARS, 11);
});
