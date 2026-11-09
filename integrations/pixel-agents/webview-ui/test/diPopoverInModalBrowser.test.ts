/**
 * B2c, the Lead's ruling (A), RENDERED in Chrome on TEST-ONLY SURFACES (test/harness/di-popover-in-modal.html). No product surface puts a popover in a
 * modal: this proves the MECHANISM, not that it is wired anywhere a person can reach. Verified by construction, not in use, until a real surface uses it.
 *  - `bare`: an aria-modal dialog with a modal claim on the shared front stack and NO focus handling of its own. It ISOLATES (A): without (A) nothing
 *    returns focus there.
 *  - `modal`: the app's own Modal (useDiModal), an INTEGRATION check. Its own focusout retake already lands focus on the dialog in Chrome without (A)
 *    (b2c-A-inmodal-BEFORE-chrome.log), so it cannot isolate (A); it shows the two agree.
 *
 * Each claim names the assertion that carries it (method rule 2):
 *  - CONTROL: with its trigger available, Escape closes the popover and focus goes back to the trigger, inside the dialog;
 *  - THE CASE: with the trigger made unable to take focus FIRST (hidden), a focus-returning close (a non-navigating item, or Escape) puts focus on
 *    the popover's OWN MODAL dialog, never the page body; the dialog stays open and is again the one claim in front.
 * NOT covered (recorded in diPopover.ts and b2c-record.md): a popover portalled OUT of its modal's subtree; a dialog that cannot itself take focus;
 * a close that returns no focus (an item that navigates, an outside pointer, focus leaving), where the surrounding surface's own rule applies; and a
 * trigger hidden by the SAME commit as the close, which is hidden only after the focus decision.
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
const STACK_URL = '/src/components/diModalStack.ts';

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
    console.warn(`SKIPPED (popover in a modal): ${unavailable}`);
    ctx.skip();
    return;
  }
  await page?.close();
  page = null;
}, STEPS_MS);

const p = () => page!;
const sel = (id: string) => `[data-testid="${id}"]`;
// a STRING, as B1's tests pass it (a function containing import() is rewritten by vitest's transform)
const frontCount = () => p().evaluate(`(async () => (await import('${STACK_URL}')).frontCount())()`) as Promise<number>;
const where = () =>
  p().evaluate(() => {
    const a = document.activeElement;
    return {
      id: a === document.body ? 'BODY' : (a?.getAttribute('data-testid') === 'h-pop' ? 'h-pop' : a?.getAttribute('aria-modal') === 'true' ? 'THE MODAL' : (a?.getAttribute('data-testid') ?? a?.tagName ?? null)),
      inModal: a !== null && a.closest('[aria-modal="true"]') !== null,
    };
  });
async function boot(surface: 'modal' | 'bare'): Promise<void> {
  page = await browser!.newPage({ viewport: { width: 1280, height: 800 } });
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await page.goto(`${origin}/test/harness/di-popover-in-modal.html?surface=${surface}`, { waitUntil: 'domcontentloaded' });
  await page.waitForSelector('[role="dialog"][aria-modal="true"]');
}
async function openPopover(): Promise<void> {
  await p().click(sel('h-pop'));
  await p().waitForSelector(sel('h-pop-popover'));
  assert.equal(await frontCount(), 2, 'the dialog and the popover are in front');
}
async function makeTriggerUnavailable(): Promise<void> {
  await p().evaluate(() => (window as unknown as { __makeTriggerUnavailable?: () => void }).__makeTriggerUnavailable?.());
  await p().waitForFunction(() => document.querySelector('[data-testid="h-body"]')?.getAttribute('data-trigger-gone') === 'true');
  assert.equal(
    await p().locator(sel('h-pop')).evaluate((t) => (t as HTMLElement).checkVisibility({ checkVisibilityCSS: true })),
    false,
    'the trigger can no longer take focus BEFORE the close (the precondition)',
  );
}
async function closedOnTheModal(how: string, surface: string): Promise<void> {
  assert.equal(await p().locator(sel('h-pop-popover')).count(), 0, 'the popover closed');
  const after = await where();
  console.log(`IN-MODAL ${surface} ${how}: focus after ${JSON.stringify(after)}`);
  assert.deepEqual(after, { id: 'THE MODAL', inModal: true }, "focus is on the popover's own modal dialog");
  assert.equal(await p().locator('[role="dialog"][aria-modal="true"]').count(), 1, 'the dialog is still open');
  assert.equal(await frontCount(), 1, 'the dialog is again the one claim in front');
}

for (const surface of ['bare', 'modal'] as const) {
  test(`${surface}: CONTROL: with its trigger available, Escape closes the popover and focus goes back to the trigger, inside the dialog`, async () => {
    await boot(surface);
    await openPopover();
    await p().keyboard.press('Escape');
    assert.equal(await p().locator(sel('h-pop-popover')).count(), 0, 'the popover closed');
    assert.deepEqual(await where(), { id: 'h-pop', inModal: true });
    assert.equal(await frontCount(), 1, 'the dialog is the one claim in front');
  }, STEPS_MS);

  test(`${surface}: THE CASE, a non-navigating item, the trigger unavailable first: focus goes to the popover's own modal, never the body`, async () => {
    await boot(surface);
    await openPopover();
    await makeTriggerUnavailable();
    await p().locator(sel('h-item')).focus();
    await p().keyboard.press('Enter');
    await closedOnTheModal('item', surface);
  }, STEPS_MS);

  test(`${surface}: THE CASE, Escape, the trigger unavailable first: focus goes to the popover's own modal, never the body`, async () => {
    await boot(surface);
    await openPopover();
    await makeTriggerUnavailable();
    await p().keyboard.press('Escape');
    await closedOnTheModal('Escape', surface);
  }, STEPS_MS);
}
