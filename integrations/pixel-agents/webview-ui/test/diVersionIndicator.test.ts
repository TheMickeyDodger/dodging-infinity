/*
 * THE VERSION INDICATOR'S ACCESSIBILITY, on a surface this slice touches (AC-UX-08 accessible names, AC-UX-07 full keyboard access). BEFORE: the dismiss was a button carrying only the text "x", and the update
 * card, the version label and the hover tooltip were onClick-only divs, which a keyboard cannot operate (and a screen reader does not announce as controls). The card also held the dismiss inside the clickable
 * div. NOW: the dismiss has an accessible name; the card face, the label and the tooltip are real buttons (the SAME callbacks and actions); the dismiss is the card's sibling, never nested in a button.
 * TARGET SIZE AND SPACING are MEASURED here from actual geometry and PRINTED, not asserted: the stated limit of the sweep is new targets and restore slots, not the pre-existing sub-24 population, so a
 * pre-existing small target is characterised, not silently resized and not silently ignored.
 * LIMITS: system Chrome, a 1100 x 700 window with the office open (the notice) and the notice dismissed (the label); the accessible name is read from the browser's own accessibility tree where stated.
 */
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

import type { Browser, Page } from 'playwright-core';
import { chromium } from 'playwright-core';
import type { ViteDevServer } from 'vite';
import { createServer } from 'vite';
import { afterAll, beforeAll, beforeEach, test } from 'vitest';
import type { WebSocket } from 'ws';
import { WebSocketServer } from 'ws';

import type { DiOfficeState } from '../../core/src/messages.ts';
import { tabProblems, tabTraversal } from './diTabTraversal.ts';

const STEPS_MS = 300_000;
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const LIVE = (
  JSON.parse(
    fs.readFileSync(
      path.join(root, 'test/fixtures/di-office-state-transitions-v2.FIXTURE.json'),
      'utf-8',
    ),
  ) as { states: Record<'live', DiOfficeState> }
).states.live;
const metropolis = fs.readFileSync(
  path.join(root, '..', 'packaging/di/worlds/living-metropolis.json'),
  'utf-8',
);

let server: ViteDevServer | null = null;
let wss: WebSocketServer | null = null;
let browser: Browser | null = null;
let page: Page | null = null;
let url = '';
let unavailable: string | null = null;
let socket: WebSocket | null = null;
let connected: Promise<WebSocket> = Promise.resolve(null as unknown as WebSocket);
let resolveConnected: (ws: WebSocket) => void = () => undefined;

function serve(ws: WebSocket) {
  ws.on('message', (data) => {
    const msg = JSON.parse(String(data)) as Record<string, unknown>;
    if (msg.type === 'webviewReady') {
      socket = ws;
      resolveConnected(ws);
    }
  });
}

beforeAll(async () => {
  server = await createServer({
    configFile: path.resolve(root, 'vite.config.ts'),
    server: { port: 0, strictPort: false, hmr: false, ws: false },
    logLevel: 'silent',
  });
  await server.listen();
  wss = new WebSocketServer({ noServer: true });
  server.httpServer!.on('upgrade', (req, sock, head) => {
    if (!req.url?.startsWith('/ws')) {
      sock.destroy();
      return;
    }
    wss!.handleUpgrade(req, sock, head, (ws) => serve(ws));
  });
  const addr = server.httpServer?.address();
  const port = typeof addr === 'object' && addr !== null ? addr.port : 0;
  url = `http://localhost:${port}/test/harness/di-app.html`;
  try {
    browser = await chromium.launch({ channel: 'chrome', headless: true });
  } catch {
    try {
      browser = await chromium.launch({ headless: true });
    } catch (e) {
      unavailable = `no Chrome or Playwright Chromium to drive: ${String(e).split('\n')[0]}`;
    }
  }
}, 120_000);

afterAll(async () => {
  await browser?.close();
  wss?.close();
  await server?.close();
}, STEPS_MS);

beforeEach(async (ctx) => {
  if (unavailable) {
    console.warn(`SKIPPED (world yield): ${unavailable}`);
    ctx.skip();
    return;
  }
  await page?.close();
  page = null;
}, STEPS_MS);

const p = () => page!;

/** Open the App at a window size, hand it the shipped world and the fixture's agents, and wait until it is drawn. */
async function boot(
  width: number,
  height: number,
  opts: { fixture?: boolean; scale?: number } = {},
): Promise<void> {
  connected = new Promise((resolve) => (resolveConnected = resolve));
  page = await browser!.newPage({
    viewport: { width, height },
    deviceScaleFactor: opts.scale ?? 1,
  });
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await page.goto(url, { waitUntil: 'domcontentloaded' });
  await connected;
  // an EXPLICIT fixture preview (the floating fixture banner and the in-panel note exist only then)
  socket!.send(JSON.stringify(opts.fixture ? { ...LIVE, fixtureMode: true } : LIVE));
  socket!.send(JSON.stringify({ type: 'layoutLoaded', layout: JSON.parse(metropolis) }));
  await page.waitForFunction(
    () => {
      const office = (
        window as unknown as {
          __diHarnessOffice?: { characters: Map<number, unknown>; layout: { cols: number } };
        }
      ).__diHarnessOffice;
      return office !== undefined && office.characters.size >= 4 && office.layout.cols === 352;
    },
    undefined,
    { timeout: 60_000 },
  );
  await p().waitForSelector('[data-testid="di-office-panel"]', { timeout: 30_000 });
}

/** Show the update notice (it auto-closes after 20 s, so this is sent right before it is needed). */
const pause = (ms: number) => p().waitForTimeout(ms);
/** A fixture label is PAINTED when it is CSS-visible, its whole box is inside the window, and every hit-test point over it (pointer-events neutralized) lands on it. */

const names = (selector: string) =>
  p().evaluate(
    (sel) =>
      Array.from(document.querySelectorAll(sel)).map((e) => ({
        tag: e.tagName.toLowerCase(),
        name: (e.getAttribute('aria-label') ?? e.textContent ?? '').replace(/\s+/g, ' ').trim(),
        role: e.getAttribute('role'),
      })),
    selector,
  );
const rectOf = (sel: string) =>
  p().evaluate((s) => {
    const e = document.querySelector(s);
    if (!e) return null;
    const b = e.getBoundingClientRect();
    return {
      left: Math.round(b.left * 10) / 10,
      top: Math.round(b.top * 10) / 10,
      width: Math.round(b.width * 10) / 10,
      height: Math.round(b.height * 10) / 10,
    };
  }, sel);

test(
  'THE VERSION INDICATOR is made of real, named, keyboard-operable controls: the dismiss has an accessible name, the card face and the label are buttons that open the changelog from the keyboard, nothing is a button inside a button, and every one is reached by Tab and drawn',
  async () => {
    await boot(1100, 700, { fixture: false });
    socket!.send(
      JSON.stringify({
        type: 'settingsLoaded',
        soundEnabled: false,
        showAreas: false,
        extensionVersion: '9.9.0',
        lastSeenVersion: '1.0',
      }),
    );
    await p().waitForSelector('.di-version-notice', { timeout: 10_000 });
    await pause(400);
    const failures: string[] = [];
    const rows: string[] = [];
    // 1. the structure: no interactive control inside another, the notice's controls are buttons
    const structure = await p().evaluate(() => ({
      nested: document.querySelectorAll('button button, a button, button a').length,
      cardButtons: document.querySelectorAll('.di-version-notice button').length,
      clickDivs: Array.from(
        document.querySelectorAll('.di-version-notice, .di-version-label'),
      ).filter((e) => e.tagName === 'DIV').length,
      labelTag: document.querySelector('.di-version-label')?.tagName ?? null,
    }));
    rows.push(`structure: ${JSON.stringify(structure)}`);
    if (structure.nested > 0)
      failures.push(`a button is nested inside another interactive control (${structure.nested})`);
    if (structure.labelTag !== 'BUTTON')
      failures.push(`the version label is a ${structure.labelTag}, not a button`);
    const buttons = await names('.di-version-notice button, .di-version-label');
    rows.push(`notice and label controls: ${JSON.stringify(buttons)}`);
    const dismiss = buttons.find((b) => b.name === 'Dismiss the update notice');
    if (!dismiss) failures.push('the dismiss has no accessible name (it was the bare text "x")');
    if (
      !buttons.some(
        (b) => b.tag === 'button' && /^Updated to v[\d.]+!\s*See what's new$/.test(b.name),
      )
    )
      failures.push(`no button carries the card's message as its name: ${JSON.stringify(buttons)}`);
    // 2. the browser's own accessibility tree: role and name of the dismiss, the card and the label
    const cdp = await p().context().newCDPSession(p());
    const ax = async (selector: string) => {
      const doc = await cdp.send('DOM.getDocument');
      const { nodeId } = await cdp.send('DOM.querySelector', { nodeId: doc.root.nodeId, selector });
      if (!nodeId) return null;
      const r = await cdp.send('Accessibility.getPartialAXTree', { nodeId, fetchRelatives: false });
      const n = r.nodes[0];
      return n ? { role: n.role?.value, name: n.name?.value } : null;
    };
    const axDismiss = await ax('[data-testid="version-notice-dismiss"]');
    const axCard = await ax('.di-version-notice button:not([data-testid])');
    rows.push(
      `the browser's own computed role and name: dismiss ${JSON.stringify(axDismiss)}; card ${JSON.stringify(axCard)}`,
    );
    if (axDismiss?.role !== 'button' || axDismiss?.name !== 'Dismiss the update notice')
      failures.push(`the dismiss's computed role and name are ${JSON.stringify(axDismiss)}`);
    if (axCard?.role !== 'button' || !/Updated to v/.test(axCard?.name ?? ''))
      failures.push(`the card face's computed role and name are ${JSON.stringify(axCard)}`);
    // 3. target geometry, MEASURED and printed (not asserted)
    const geo = {
      card: await rectOf('.di-version-notice button:not([data-testid])'),
      dismiss: await rectOf('[data-testid="version-notice-dismiss"]'),
      label: await rectOf('.di-version-label'),
    };
    rows.push(
      `TARGET GEOMETRY, measured, CSS px (WCAG 2.5.8 asks 24 x 24 unless spacing offsets): ${JSON.stringify(geo)}`,
    );
    // 4. the real keyboard reaches them, and each is drawn where it sits
    const t = await tabTraversal(p(), {
      max: 220,
      regions: { notice: '.di-version-notice', label: '.di-version-label' },
    });
    const stops = t.stops.filter((s) => s.region === 'notice' || s.region === 'label');
    rows.push(
      `tab stops in the notice and label: ${JSON.stringify(stops.map((s) => ({ id: s.id, region: s.region, hit: `${s.hitSamples}/${s.sampled}`, indicator: s.indicator.pixelsChanged })))}`,
    );
    for (const want of ['version-notice-dismiss'])
      if (!stops.some((s) => s.id === want)) failures.push(`${want} is not reached by Tab`);
    if (!stops.some((s) => s.region === 'notice' && s.id !== 'version-notice-dismiss'))
      failures.push('the card face is not reached by Tab');
    if (!stops.some((s) => s.region === 'label'))
      failures.push('the version label is not reached by Tab');
    for (const x of tabProblems({ stops, ended: t.ended }, 1))
      failures.push(`${x.id}: ${x.why.join('; ')}`);
    // 5. OPERABLE BY KEYBOARD: Enter on the card face opens the changelog (the callback is unchanged), and Enter on the label opens it again once the notice is gone
    await p().focus('.di-version-notice button:not([data-testid])');
    await p().keyboard.press('Enter');
    const opened1 = await p()
      .waitForSelector("text=What's New in v", { timeout: 5000 })
      .then(() => true)
      .catch(() => false);
    rows.push(`Enter on the card face opens the changelog: ${opened1}`);
    if (!opened1) failures.push('Enter on the card face did not open the changelog');
    // the shared Modal (Settings, Changelog) has NO Escape handler: it closes through its own x button or its backdrop, so the backdrop is clicked here (a real pointer path). That is a
    // pre-existing property of a surface this slice does not touch; it is recorded, not changed.
    await p().mouse.click(4, 4);
    // CLOSED before the next check, stated and asserted: if it stayed open the label's Enter below could pass without doing anything (a silent skip is not allowed)
    const closed = await p()
      .waitForSelector("text=What's New in v", { state: 'detached', timeout: 5000 })
      .then(() => true)
      .catch(() => false);
    rows.push(`the backdrop click closes the changelog: ${closed}`);
    if (!closed)
      failures.push(
        'the changelog did not close on a backdrop click, so the next check could not be a fresh open',
      );
    await p().focus('.di-version-label');
    await p().keyboard.press('Enter');
    const opened2 = await p()
      .waitForSelector("text=What's New in v", { timeout: 5000 })
      .then(() => true)
      .catch(() => false);
    rows.push(`Enter on the version label opens the changelog: ${opened2}`);
    if (!opened2) failures.push('Enter on the version label did not open the changelog');
    console.log(`VERSION INDICATOR\n${rows.join('\n')}`);
    assert.deepEqual(
      failures,
      [],
      'the version indicator is made of real, named, keyboard-operable controls (see the rows above)',
    );
  },
  STEPS_MS,
);
