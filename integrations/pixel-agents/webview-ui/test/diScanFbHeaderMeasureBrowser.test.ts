/**
 * MEASUREMENT ONLY, for F-b (B2b, OPEN INTRODUCED): the Code Diff header took two rows at 1480 and 1500 docked because its SCALE was keyed to the
 * WINDOW (a `@media (max-width: 1499px)` rule) while its ROOM is the STAGE. Since F-b (T3) the scale is keyed to the panes state and the stage's
 * width (diScanPages.css); this instrument measures what that gives, at one pane and docked.
 * Chrome, the labelled SAMPLE specimen (test/harness/di-scan-lock.html, Code Diff), two panes at 1480, 1500 and 1672, the second crumb's label varied
 * as diScanHeaderRegion varies it (Open, Merged, Closed, Archived: synthetic variations of the page's own text).
 *
 * Method rule 5: THREE numbers per case, each measured on its own, never one inferred from another:
 *  - the CHANNEL: the bar's content box (its width less its padding), with the bar PROVEN to fill the stage (its width equals the stage's inner width);
 *    all three come from diHeaderRegion's ONE shared instrument (oneRowNeed), which reads the scale where it is CONSUMED and THROWS if it is absent;
 *  - the CONTENT: the header's MINIMUM one-row width (a measurement-only probe, removed after reading: no wrapping, the bar at `min-content`, so the
 *    title sits at its own floor, minmax(1.5em, ...), exactly the minimum the wrap rule protects), and its max-content one-row width (the title whole);
 *  - the ROWS the header actually takes (diHeaderRegion's own instrument), so "it wraps exactly when the channel is less than the minimum content" is
 *    CHECKED against what is drawn, not assumed.
 * ASSERTED, only to know the state is the named one: Code Diff, one pane below 1440 and two from 1440. Everything else is RECORDED ("FB ...") and asserted not at all.
 */
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

import type { Browser, Page } from 'playwright-core';
import { chromium } from 'playwright-core';
import type { ViteDevServer } from 'vite';
import { createServer } from 'vite';
import { afterAll, beforeAll, expect, test } from 'vitest';

import { measureHeader, oneRowNeed } from './diHeaderRegion.ts';

const STEPS_MS = 180_000;
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const SHOTS = process.env.DI_FB_SHOTS ?? null;
/** One pane (1280, 1400: that nothing one-pane moves) and docked (1480, 1500, 1672: F-b's widths). */
const WIDTHS = [1280, 1400, 1480, 1500, 1672];
const LABELS = ['Open', 'Merged', 'Closed', 'Archived'];
const heightFor = (w: number) => (w >= 1500 ? 941 : 720);

/** Method rule 4: never overwrite a measurement artifact. */
function freshPath(dir: string, name: string): string {
  const ext = path.extname(name);
  const stem = name.slice(0, name.length - ext.length);
  let candidate = path.join(dir, name);
  for (let n = 2; fs.existsSync(candidate); n++) candidate = path.join(dir, `${stem}.${n}${ext}`);
  return candidate;
}

let server: ViteDevServer | null = null;
let browser: Browser | null = null;
let origin = '';
let unavailable: string | null = null;
const measured: Record<string, unknown> = {};

beforeAll(async () => {
  server = await createServer({ configFile: path.resolve(root, 'vite.config.ts'), server: { port: 0, strictPort: false, hmr: false, ws: false }, logLevel: 'silent' });
  await server.listen();
  const addr = server.httpServer?.address();
  origin = `http://localhost:${typeof addr === 'object' && addr !== null ? addr.port : 0}`;
  if (SHOTS !== null) fs.mkdirSync(SHOTS, { recursive: true });
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
  if (SHOTS !== null) fs.writeFileSync(freshPath(SHOTS, 'fb-measured.json'), JSON.stringify(measured, null, 2), { flag: 'wx' });
  await browser?.close();
  await server?.close();
}, STEPS_MS);

/** One case: the drawn ROWS (diHeaderRegion's boxes) and the NEED (its oneRowNeed probe, which throws rather than report a default scale). */
async function measureOne(page: Page): Promise<Record<string, unknown>> {
  const rows = (await measureHeader(page)).rows;
  const need = await oneRowNeed(page);
  const panes = await page.locator('[data-testid="di-scan-panel"]').getAttribute('data-panes');
  return { ...need, panes, rows, predictedRows: need.channel < need.minOneRow ? 2 : 1 };
}

test(
  'F-b MEASURE: per width and label, the CHANNEL (the bar, proven to fill), the CONTENT (min and max one-row widths), and the ROWS drawn',
  async () => {
    if (unavailable) {
      console.warn(`SKIPPED (F-b measurement): ${unavailable}`);
      return;
    }
    for (const w of WIDTHS) {
      const page = await browser!.newPage({ viewport: { width: w, height: heightFor(w) } });
      await page.emulateMedia({ reducedMotion: 'reduce' });
      await page.goto(`${origin}/test/harness/di-scan-lock.html?di-scan-view=diff`, { waitUntil: 'load' });
      await page.waitForSelector('[data-testid="di-scan-stage"][data-view="diff"] .di-scan-stage-bar');
      await page.evaluate(async () => {
        await document.fonts.ready;
      });
      expect(await page.locator('[data-testid="di-scan-panel"]').getAttribute('data-panes'), `${w}: the panes this width has`).toBe(w >= 1440 ? 'two' : 'one');
      for (const label of LABELS) {
        await page.evaluate((text) => {
          const crumb = document.querySelector('.di-scan-crumb');
          if (crumb !== null) ((crumb.children[2] as HTMLElement).textContent = text);
        }, label);
        await page.waitForTimeout(60);
        const m = await measureOne(page);
        measured[`${w}/${label}`] = m;
        console.log(`FB ${w}/${label}: ${JSON.stringify(m)}`);
      }
      await page.close();
    }
  },
  STEPS_MS,
);
