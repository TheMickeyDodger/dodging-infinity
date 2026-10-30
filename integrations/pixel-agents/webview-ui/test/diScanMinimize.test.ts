/**
 * The scan face's Minimize control stays reachable after you leave Home, and Restore brings that
 * screen back.
 *
 * Free UI draws Mission details and the rail pages from the navigation rail's right edge to the
 * window's right edge, which covers the Home header. Minimize used to be the Home logo, so it
 * vanished on those screens. It now lives on the rail. This drives the real office panel
 * (`?face=scan` on test/harness/di-panel.html) with real clicks.
 *
 * Browser: the installed Chrome (playwright-core, channel `chrome`), else Playwright's Chromium.
 * With neither, the test is skipped and says why.
 */
import assert from 'node:assert/strict';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

import type { Browser, Page } from 'playwright-core';
import { chromium } from 'playwright-core';
import type { ViteDevServer } from 'vite';
import { createServer } from 'vite';
import { afterAll, beforeAll, beforeEach, expect, test } from 'vitest';

const STEPS_MS = 120_000;
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');

let server: ViteDevServer | null = null;
let browser: Browser | null = null;
let origin = '';
let unavailable: string | null = null;

beforeAll(async () => {
  server = await createServer({
    configFile: path.resolve(root, 'vite.config.ts'),
    server: { port: 0, strictPort: false, hmr: false, ws: false },
    logLevel: 'silent',
  });
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
  await browser?.close();
  await server?.close();
}, STEPS_MS);

beforeEach((ctx) => {
  if (unavailable) {
    console.warn(`SKIPPED (scan minimize): ${unavailable}`);
    ctx.skip();
  }
});

async function open(): Promise<Page> {
  const page = await browser!.newPage({ viewport: { width: 1440, height: 900 } });
  await page.goto(`${origin}/test/harness/di-panel.html?face=scan`, {
    waitUntil: 'domcontentloaded',
  });
  await page.waitForSelector('[data-testid="di-rail"]');
  await page.waitForSelector('[data-testid="di-office-panel-minimize"]');
  return page;
}

const shown = (page: Page, testid: string) =>
  page.locator(`[data-testid="${testid}"]`).evaluate((el) => {
    const box = el.getBoundingClientRect();
    if (box.width < 2 || box.height < 2) return false;
    const hit = document.elementFromPoint(box.left + box.width / 2, box.top + box.height / 2);
    return hit !== null && (hit === el || el.contains(hit));
  });

test(
  'Minimize on the rail collapses the office from Mission details and from a rail page, and Restore brings that same screen back',
  async () => {
    const page = await open();
    try {
      expect(await page.locator('[data-testid="di-office-panel-minimize"]').count()).toBe(1);
      expect(await shown(page, 'di-office-panel-minimize')).toBe(true);

      await page.locator('[data-testid="di-rail-orchestrator"]').first().click();
      await page.waitForSelector('[data-testid="di-scan-stage"][data-view="mission"]');
      // the stage covers the Home header; Minimize is still the control under the pointer
      expect(await shown(page, 'di-office-panel-minimize')).toBe(true);
      await page.click('[data-testid="di-office-panel-minimize"]');
      await page.waitForSelector('[data-testid="di-office-panel-collapsed"]');
      assert.equal(await shown(page, 'di-office-panel'), false, 'Mission details: the panel is not shown');
      assert.equal(await shown(page, 'di-rail'), false, 'Mission details: the rail minimizes with the panel');
      assert.equal(await shown(page, 'di-office-panel-collapsed'), true, 'Mission details: Restore is on screen');

      await page.click('[data-testid="di-office-panel-collapsed"]');
      await page.waitForSelector('[data-testid="di-scan-stage"][data-view="mission"]');
      assert.equal(await shown(page, 'di-office-panel'), true, 'restored: Mission details is back');
      assert.equal(await shown(page, 'di-office-panel-minimize'), true, 'restored: Minimize is back on the rail');

      await page.click('[data-testid="di-rail-missions"]');
      await page.waitForSelector('[data-testid="di-scan-stage"][data-view="missions"]');
      assert.equal(await shown(page, 'di-office-panel-minimize'), true, 'Missions page: Minimize is still under the pointer');
      await page.click('[data-testid="di-office-panel-minimize"]');
      await page.waitForSelector('[data-testid="di-office-panel-collapsed"]');
      assert.equal(await shown(page, 'di-office-panel'), false, 'Missions page: the panel is not shown');
      assert.equal(await shown(page, 'di-office-panel-collapsed'), true, 'Missions page: Restore is on screen');

      await page.click('[data-testid="di-office-panel-collapsed"]');
      await page.waitForSelector('[data-testid="di-scan-stage"][data-view="missions"]');
      assert.equal(await shown(page, 'di-office-panel-minimize'), true, 'restored: the Missions page and Minimize are back');
    } finally {
      await page.close();
    }
  },
  STEPS_MS,
);
