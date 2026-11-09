/**
 * B2c round 15, RENDERED in Chrome on a TEST-ONLY SURFACE (test/harness/di-popover-frame.html): the anchored popover inside CONSTRAINED frames. The
 * Reviewer's blocker, from source: the stylesheet's `min-width: 240px` overrides the placement helper's smaller `maxWidth`, so the box can be drawn
 * outside its frame while the pure-function test passes. This test asserts the RENDERED box, never the helper's numbers.
 *
 * Each claim names the assertion that carries it (method rule 2). For each case (`neither`, the Reviewer's; `narrow`, a frame under 240; `roomy`, the
 * control), the popover opened by a real pointer:
 *  - GEOMETRY: the COMPLETE box (all four edges) is inside the frame (the frame element's box cut by the window);
 *  - REACHABLE (fitting is not reaching): every item and Close is what the window draws at its centre, inside the frame, and Tab from the first item
 *    reaches each in order.
 * MEASURED and printed BEFORE any assertion ("FRAME <case>: {...}"): the box, the frame, the placement, and the popover's own min-content and max-content
 * widths (whether 240 px protects anything a narrower box would lose).
 */
import assert from 'node:assert/strict';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

import type { Browser, Page } from 'playwright-core';
import { chromium } from 'playwright-core';
import type { ViteDevServer } from 'vite';
import { createServer } from 'vite';
import { afterAll, beforeAll, beforeEach, test } from 'vitest';

const STEPS_MS = 120_000;
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const ITEMS = ['h-item-1', 'h-item-2', 'h-item-3', 'h-pop-close'] as const;

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
    console.warn(`SKIPPED (popover frame): ${unavailable}`);
    ctx.skip();
    return;
  }
  await page?.close();
  page = null;
}, STEPS_MS);

const p = () => page!;
const sel = (id: string) => `[data-testid="${id}"]`;

// B3 (the More sliver): this primitive's whole job is viewport-dependent placement and it was verified at ONE viewport, 1280x800, where the defect that hid the Code Diff menu at the two shipping sizes does not exist. All three sizes now.
for (const vp of [{ width: 1280, height: 800 }, { width: 1440, height: 900 }, { width: 1600, height: 1000 }] as const)
  for (const name of ['neither', 'narrow', 'roomy'] as const)
  test(`${name} at ${vp.width}x${vp.height}: the COMPLETE rendered box stays inside its frame, and every item stays REACHABLE (pointer and Tab)`, async () => {
    page = await browser!.newPage({ viewport: { width: vp.width, height: vp.height } });
    await page.emulateMedia({ reducedMotion: 'reduce' });
    await page.goto(`${origin}/test/harness/di-popover-frame.html?case=${name}`, { waitUntil: 'domcontentloaded' });
    await page.waitForSelector(sel('h-pop'));
    await p().click(sel('h-pop'));
    await p().waitForSelector(sel('h-pop-popover'));
    const m = await p().evaluate(() => {
      const pop = document.querySelector<HTMLElement>('[data-testid="h-pop-popover"]')!;
      const f = document.querySelector<HTMLElement>('[data-testid="h-frame"]')!.getBoundingClientRect();
      const frame = { top: Math.max(0, f.top), left: Math.max(0, f.left), right: Math.min(innerWidth, f.right), bottom: Math.min(innerHeight, f.bottom) };
      const r = pop.getBoundingClientRect();
      const trigger = document.querySelector<HTMLElement>('[data-testid="h-pop"]')!.getBoundingClientRect();
      // the popover's own content widths, by a measurement-only probe (restored after reading)
      const was = pop.style.cssText;
      pop.style.setProperty('min-width', '0', 'important');
      pop.style.setProperty('max-width', 'none', 'important');
      pop.style.setProperty('width', 'min-content', 'important');
      const minContent = pop.getBoundingClientRect().width;
      pop.style.setProperty('width', 'max-content', 'important');
      const maxContent = pop.getBoundingClientRect().width;
      pop.style.cssText = was;
      const round = (b: { top: number; left: number; right: number; bottom: number }) => ({ top: Math.round(b.top), left: Math.round(b.left), right: Math.round(b.right), bottom: Math.round(b.bottom) });
      return {
        box: { ...round(r), width: Math.round(r.width) },
        frame: round(frame),
        trigger: { left: Math.round(trigger.left), right: Math.round(trigger.right) },
        placement: { side: pop.getAttribute('data-side'), align: pop.getAttribute('data-align'), inlineMaxWidth: pop.style.maxWidth, cssMinWidth: getComputedStyle(pop).minWidth },
        minContent: Math.round(minContent),
        maxContent: Math.round(maxContent),
        inside: r.top >= frame.top - 0.5 && r.left >= frame.left - 0.5 && r.right <= frame.right + 0.5 && r.bottom <= frame.bottom + 0.5,
      };
    });
    console.log(`FRAME ${name}: ${JSON.stringify(m)}`);
    assert.equal(m.inside, true, `${name}: the complete box is inside its frame: ${JSON.stringify(m)}`);
    // REACHABLE: each item and Close drawn at its centre, inside the frame
    for (const id of ITEMS) {
      const shown = await p().evaluate((testid) => {
        const el = document.querySelector(`[data-testid="${testid}"]`)!;
        const b = el.getBoundingClientRect();
        const f = document.querySelector('[data-testid="h-frame"]')!.getBoundingClientRect();
        const x = (b.left + b.right) / 2;
        const y = (b.top + b.bottom) / 2;
        const hit = document.elementFromPoint(x, y);
        return { inFrame: x >= f.left && x <= f.right && y >= f.top && y <= f.bottom, onTop: hit !== null && (el === hit || el.contains(hit)) };
      }, id);
      assert.deepEqual(shown, { inFrame: true, onTop: true }, `${name}: ${id} is reachable by a pointer`);
    }
    // and by Tab, in order, from the first item (where focus lands on open)
    for (const [i, id] of ITEMS.entries()) {
      assert.equal(await p().evaluate(() => document.activeElement?.getAttribute('data-testid') ?? null), id, `${name}: Tab reaches ${id} (${i} after the first)`);
      if (i < ITEMS.length - 1) await p().keyboard.press('Tab');
    }
  }, STEPS_MS);
