/**
 * B2b CORRECTION, THE CONTRACT (the Lead's ruling: an avatar may change SIZE, never SHAPE). Chrome, headless; not Brave evidence. A RULED outcome is a
 * contract, so it is ASSERTED (rule 9's boundary). On the labelled POPULATED sample (test/harness/di-scan-lock.html, the roster opened), at narrow
 * mode (1280: one pane), both shipping sizes (1440, 1600), the avatar size switch (1500) and the pixel-lock reference (1672), for EVERY member card
 * (rule 6c), by name:
 *   - the avatar is drawn SQUARE at its declared size (drawn width = drawn height = its computed height: never squeezed);
 *   - the copy is VISIBLE: no text inside it is clipped, and it sits inside the card's content box;
 *   - the card overflows in neither direction, and the stage does not overflow horizontally.
 * Recorded ("AVSQ ..."): each card's sizes and whether its copy wrapped below the avatar. IDENTITY FIRST: the complete four-member set, by name, is
 * asserted before any geometry (a smaller set never passes). THE COUNTERFACTUAL'S SCOPE: the 5 / 5 BEFORE red at "square" was produced by the EARLIER
 * version of this test (its ">= 2 cards" state); it establishes the SQUARE defect only. The identity assertion is a STATE precondition, true before the
 * fix and after it alike, so no product BEFORE can falsify it; its falsification is the negative control above.
 */
import assert from 'node:assert/strict';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

import type { Browser, Page } from 'playwright-core';
import { chromium } from 'playwright-core';
import type { ViteDevServer } from 'vite';
import { createServer } from 'vite';
import { afterAll, beforeAll, beforeEach, test } from 'vitest';

const STEPS_MS = 180_000;
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const VIEWPORTS = [
  { width: 1280, height: 800, label: 'narrow mode (one pane)' },
  { width: 1440, height: 900, label: 'shipping size' },
  { width: 1500, height: 941, label: 'the avatar size switch' },
  { width: 1600, height: 1000, label: 'shipping size' },
  { width: 1672, height: 941, label: 'the pixel-lock reference' },
];

let server: ViteDevServer | null = null;
let browser: Browser | null = null;
let page: Page | null = null;
let origin = '';
let unavailable: string | null = null;

beforeAll(async () => {
  server = await createServer({ configFile: path.resolve(root, 'vite.config.ts'), server: { port: 0, strictPort: false, hmr: false, ws: false }, logLevel: 'silent' });
  await server.listen();
  const addr = server.httpServer?.address();
  origin = `http://localhost:${typeof addr === 'object' && addr !== null ? addr.port : 0}`;
  try {
    browser = await chromium.launch({ channel: 'chrome', headless: true });
  } catch {
    try {
      browser = await chromium.launch({ headless: true });
    } catch (e) {
      unavailable = `no Chrome or Playwright Chromium to drive: ${String(e).split('\n')[0]}`;
    }
  }
}, STEPS_MS);

afterAll(async () => {
  await page?.close();
  await browser?.close();
  await server?.close();
}, STEPS_MS);

beforeEach(async (ctx) => {
  if (unavailable) {
    console.warn(`SKIPPED (B2b avatar square): ${unavailable}`);
    ctx.skip();
    return;
  }
  await page?.close();
  page = null;
}, STEPS_MS);

const p = () => page!;

/** The populated sample's COMPLETE member set, by role and name, exactly as it draws them at every size (b2b-avatar-before-1 / -after-1). IDENTITY is
 * asserted BEFORE any geometry, so a missing member fails AS a missing member rather than silently shrinking the sample (rule 6c, inverted: never accept
 * a smaller set). Mia is the hardest case: the inherited sliver, her 111px copy drove the derivation, and hers is the only card that stacks at 1440,
 * 1600 and 1672. */
const EXPECTED_MEMBERS = ['Executor Mia', 'Lead Leo', 'Reviewer Sam', 'Supervisor Ken'];
/** null when the drawn set is EXACTLY the expected set; otherwise the difference, by name. */
function identityGap(cards: Array<{ role: string; name: string }>): string | null {
  const got = cards.map((c) => `${c.role} ${c.name}`).sort();
  const want = [...EXPECTED_MEMBERS].sort();
  if (JSON.stringify(got) === JSON.stringify(want)) return null;
  return `missing [${want.filter((w) => !got.includes(w)).join(', ')}]; unexpected [${got.filter((g) => !want.includes(g)).join(', ')}]`;
}

test('CONTROL (identity, rule 7): the identity check FAILS, naming her, when Mia is absent; the complete set passes', () => {
  const all = EXPECTED_MEMBERS.map((m) => ({ role: m.split(' ')[0], name: m.split(' ')[1] }));
  assert.equal(identityGap(all), null, 'SETUP: the complete four-member set passes');
  const gap = identityGap(all.filter((c) => c.name !== 'Mia'));
  assert.ok(gap !== null && gap.includes('Executor Mia'), `NEGATIVE CONTROL: without Mia the check fails and names her (${gap})`);
});

const everyCard = () =>
  p().evaluate(() => {
    const stage = document.querySelector<HTMLElement>('[data-testid="di-scan-stage"]');
    const cards = [...document.querySelectorAll<HTMLElement>('[data-testid="di-scan-swarm"] .di-scan-card-member')].map((card, index) => {
      const avatar = card.querySelector<HTMLElement>('.di-scan-avatar-lg')!;
      const copy = card.querySelector<HTMLElement>('.di-scan-card-copy')!;
      const cb = card.getBoundingClientRect();
      const cs = getComputedStyle(card);
      const contentLeft = cb.left + parseFloat(cs.borderLeftWidth) + parseFloat(cs.paddingLeft);
      const contentRight = cb.right - parseFloat(cs.borderRightWidth) - parseFloat(cs.paddingRight);
      const ab = avatar.getBoundingClientRect();
      const kb = copy.getBoundingClientRect();
      const clippedText = [...copy.querySelectorAll<HTMLElement>('*')].filter((el) => el.scrollWidth > el.clientWidth + 1 && el.clientWidth > 0).map((el) => el.className);
      return {
        card: index,
        role: card.querySelector('.di-scan-role')?.textContent ?? '',
        name: card.querySelector('.di-scan-name')?.textContent ?? '',
        avatarDrawn: { width: Number(ab.width.toFixed(1)), height: Number(ab.height.toFixed(1)) },
        avatarComputedHeight: parseFloat(getComputedStyle(avatar).height),
        copyWrappedBelow: kb.top >= ab.bottom - 0.5,
        copyInside: kb.left >= contentLeft - 0.5 && kb.right <= contentRight + 0.5,
        copyClipped: copy.scrollWidth > copy.clientWidth + 1 || clippedText.length > 0,
        clippedText,
        cardOverflowsX: card.scrollWidth > card.clientWidth + 1,
        cardOverflowsY: card.scrollHeight > card.clientHeight + 1,
      };
    });
    return {
      panes: document.querySelector('[data-testid="di-scan-panel"]')?.getAttribute('data-panes') ?? null,
      stageOverflowsX: stage !== null && stage.scrollWidth > stage.clientWidth + 1,
      cards,
    };
  });

for (const vp of VIEWPORTS)
  test(`AVATAR SQUARE at ${vp.width}x${vp.height} (${vp.label}): every member face square at its size, copy visible, nothing overflows`, async () => {
    page = await browser!.newPage({ viewport: { width: vp.width, height: vp.height } });
    await page.emulateMedia({ reducedMotion: 'reduce' });
    await page.goto(`${origin}/test/harness/di-scan-lock.html?di-scan-case=populated&di-scan-view=mission`, { waitUntil: 'load' });
    await page.waitForSelector('[data-testid="di-scan-swarm-toggle"]');
    if ((await p().locator('.di-scan-swarm-box[data-expanded="false"]').count()) > 0) {
      await p().click('[data-testid="di-scan-swarm-toggle"]');
      await p().waitForSelector('.di-scan-swarm-box[data-expanded="true"]');
    }
    await p().waitForTimeout(200);
    const r = await everyCard();
    console.log(`AVSQ ${vp.width}x${vp.height}: ${JSON.stringify(r)}`);
    const gap = identityGap(r.cards);
    assert.equal(gap, null, `STATE, IDENTITY FIRST: the roster is open with the COMPLETE four-member set, by name (${gap})`);
    for (const c of r.cards) {
      const who = `${c.role} ${c.name} at ${vp.width}`;
      assert.ok(Math.abs(c.avatarDrawn.width - c.avatarDrawn.height) <= 0.5, `CONTRACT (${who}): the face is SQUARE (${c.avatarDrawn.width} x ${c.avatarDrawn.height})`);
      assert.ok(Math.abs(c.avatarDrawn.height - c.avatarComputedHeight) <= 0.5, `CONTRACT (${who}): drawn at its declared size (${c.avatarComputedHeight}px)`);
      assert.equal(c.copyClipped, false, `CONTRACT (${who}): the copy is visible, nothing clipped (${c.clippedText.join(', ')})`);
      assert.equal(c.copyInside, true, `CONTRACT (${who}): the copy sits inside the card`);
      assert.equal(c.cardOverflowsX || c.cardOverflowsY, false, `CONTRACT (${who}): the card does not overflow`);
    }
    assert.equal(r.stageOverflowsX, false, `CONTRACT (${vp.width}): the stage does not overflow horizontally`);
  }, STEPS_MS);
