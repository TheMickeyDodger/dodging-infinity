/**
 * B2d, ATTRIBUTION MEASUREMENT (the Lead: attribute the member-avatar squeeze BEFORE it is routed). Chrome, headless; not Brave evidence; nothing changes
 * the product. ONE probe, run BYTE-IDENTICAL in TWO trees: the verified HEAD export (`git archive` of 5a1585b, every blob hash-checked) and the live tree.
 * It is tree-AGNOSTIC on purpose: it asserts no pane state (HEAD has no panes), only that every avatar is drawn.
 *
 * The page: the labelled POPULATED sample on the scan lock harness (test/harness/di-scan-lock.html?di-scan-case=populated&di-scan-view=mission), the
 * roster opened when it is collapsed. Widths: 1280 (one pane in both trees), 1440, 1480, 1500, 1600 and 1672.
 *
 * Per-element IDENTITY (rule 6c, a requirement): EVERY member card and avatar, by index, role and name; never the first match read as the set. For each:
 * the card's content width; the avatar's USED width and height (getComputedStyle on a laid-out box is the used value, so it is named so); its DRAWN box and
 * aspect; its flex-shrink; the text's drawn width and min-content width; whether the card overflows. CAUSE PROBE (measurement only, removed after reading):
 * `flex-shrink: 0` on the avatars. ASSERTED: only that every avatar is drawn. Everything else is RECORDED ("AVATTR ...") and compared across the trees.
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
  { width: 1280, height: 800 },
  { width: 1440, height: 900 },
  { width: 1480, height: 900 },
  { width: 1500, height: 941 },
  { width: 1600, height: 1000 },
  { width: 1672, height: 941 },
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
    console.warn(`SKIPPED (avatar attribution): ${unavailable}`);
    ctx.skip();
    return;
  }
  await page?.close();
  page = null;
}, STEPS_MS);

const p = () => page!;

const everyCard = () =>
  p().evaluate(() =>
    [...document.querySelectorAll<HTMLElement>('[data-testid="di-scan-swarm"] .di-scan-card-member')].map((card, index) => {
      const avatar = card.querySelector<HTMLElement>('.di-scan-avatar-lg');
      const copy = card.querySelector<HTMLElement>('.di-scan-card-copy');
      const cb = card.getBoundingClientRect();
      const ccs = getComputedStyle(card);
      const content = cb.width - parseFloat(ccs.paddingLeft) - parseFloat(ccs.paddingRight) - parseFloat(ccs.borderLeftWidth) - parseFloat(ccs.borderRightWidth);
      const ab = avatar?.getBoundingClientRect();
      const acs = avatar ? getComputedStyle(avatar) : null;
      let copyMinContent: number | null = null;
      if (copy) {
        const was = copy.style.cssText;
        copy.style.setProperty('width', 'min-content', 'important');
        copy.style.setProperty('flex', 'none', 'important');
        copyMinContent = Math.round(copy.getBoundingClientRect().width);
        copy.style.cssText = was;
      }
      return {
        card: index,
        role: card.querySelector('.di-scan-role')?.textContent ?? '',
        name: card.querySelector('.di-scan-name')?.textContent ?? '',
        cardWidth: Math.round(cb.width),
        cardContentWidth: Math.round(content),
        avatarUsed: acs ? { width: acs.width, height: acs.height } : null,
        avatarDrawn: ab ? { width: Number(ab.width.toFixed(1)), height: Number(ab.height.toFixed(1)), aspect: Number((ab.width / ab.height).toFixed(3)) } : null,
        avatarFlexShrink: acs?.flexShrink ?? null,
        copyDrawnWidth: copy ? Math.round(copy.getBoundingClientRect().width) : null,
        copyMinContent,
        cardOverflows: card.scrollWidth > card.clientWidth + 1,
      };
    }),
  );

for (const vp of VIEWPORTS)
  test(`AVATAR ATTRIBUTION at ${vp.width}x${vp.height}: every member card and avatar, named, with the cause probe`, async () => {
    page = await browser!.newPage({ viewport: vp });
    await page.emulateMedia({ reducedMotion: 'reduce' });
    await page.goto(`${origin}/test/harness/di-scan-lock.html?di-scan-case=populated&di-scan-view=mission`, { waitUntil: 'load' });
    await page.waitForSelector('[data-testid="di-scan-swarm-toggle"]');
    if ((await p().locator('.di-scan-swarm-box[data-expanded="false"]').count()) > 0) {
      await p().click('[data-testid="di-scan-swarm-toggle"]');
      await p().waitForSelector('.di-scan-swarm-box[data-expanded="true"]');
    }
    await p().waitForTimeout(200);
    const stage = await p().evaluate(() => {
      const s = document.querySelector<HTMLElement>('[data-testid="di-scan-stage"]');
      return {
        panes: document.querySelector('[data-testid="di-scan-panel"]')?.getAttribute('data-panes') ?? null,
        stageWidth: s ? Math.round(s.getBoundingClientRect().width) : null,
        cardsWidth: Math.round(document.querySelector<HTMLElement>('[data-testid="di-scan-swarm"]')?.getBoundingClientRect().width ?? 0),
      };
    });
    const cards = await everyCard();
    await p().evaluate(() => {
      const s = document.createElement('style');
      s.id = 'avattr-probe';
      s.textContent = '[data-testid="di-scan-swarm"] .di-scan-avatar-lg { flex-shrink: 0 !important; }';
      document.head.append(s);
    });
    await p().waitForTimeout(100);
    const held = await everyCard();
    await p().evaluate(() => document.getElementById('avattr-probe')?.remove());
    console.log(`AVATTR ${vp.width}x${vp.height}: ${JSON.stringify({ ...stage, everyCard: cards, PROBE_flexShrink0: held })}`);
    assert.ok(cards.length >= 2 && cards.every((c) => c.avatarDrawn !== null && c.avatarDrawn.width > 0), 'STATE: every avatar is drawn (the roster open)');
  }, STEPS_MS);
