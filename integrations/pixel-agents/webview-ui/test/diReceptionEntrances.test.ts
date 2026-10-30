/**
 * The reception inbox's ENTRANCES and its keyboard ownership through the ACTUAL App, in a real
 * browser, over the SHIPPED living metropolis (the world with the existing lobby desk), with a labelled
 * FIXTURE office state (`di-office-state-transitions-v2.FIXTURE.json`, one blocker entry). Nothing here is
 * live Mission data and this is integration over source (the App from Vite's dev server), not the
 * production package.
 *
 * Proves: the inbox has ONE entrance, in the panel chrome (here the operational list's Reception entry; on the
 * product face it is the rail's, diScanFace.test.ts), and NOTHING in the world opens it: no desk target is drawn,
 * no control outside the chrome names Reception, and real clicks across the whole visible world, the desk and the
 * receptionist included, never open it (D-10, CONTROL-MAP "Reception left-rail only"; this SUPERSEDES the former
 * three-entrance test, in which the desk, its keyboard target and the sidebar entry all opened the inbox). The
 * receptionist is still drawn beside real agents without being one; the one entrance stays the only one before,
 * during and after the Map; and a Reception opened while the Map is already open OWNS the keyboard (arrows go to
 * its cards, Escape closes Reception and not the Map, focus is restored), the Map's isolation resuming once the
 * dialog closes.
 *
 * Browser: the installed Chrome (playwright-core, channel `chrome`), else Playwright's Chromium; with
 * neither, every test is skipped with an explicit message.
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
import { clickAcrossWorld } from './helpers/worldClickProbe.ts';

const STEPS_MS = 120_000;
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const LIVE = (
  JSON.parse(
    fs.readFileSync(path.join(root, 'test/fixtures/di-office-state-transitions-v2.FIXTURE.json'), 'utf-8'),
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
    console.warn(`SKIPPED (reception entrances): ${unavailable}`);
    ctx.skip();
    return;
  }
  await page?.close();
  page = null;
}, STEPS_MS);

const p = () => page!;
const INBOX = '[data-testid="di-reception-inbox"]';
const OPEN = '[data-testid="di-open-reception"]';
const MAP = '[data-testid="world-map"]';

/** Open the App, hand it the shipped world and the fixture's agents, and wait until it is drawn. */
async function boot(): Promise<void> {
  connected = new Promise((resolve) => (resolveConnected = resolve));
  page = await browser!.newPage({ viewport: { width: 1600, height: 1000 } });
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await page.goto(url, { waitUntil: 'domcontentloaded' });
  await connected;
  socket!.send(JSON.stringify(LIVE));
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
  await p().waitForSelector(OPEN, { timeout: 30_000 });
}
const active = (selector: string) =>
  p().evaluate((s) => document.activeElement?.matches(s) ?? false, selector);
const dialog = () =>
  p().evaluate(() => {
    const d = document.querySelector('[data-testid="di-reception-inbox"]');
    return { count: document.querySelectorAll('[data-testid="di-reception-inbox"]').length, html: d?.innerHTML ?? null };
  });
async function openMap() {
  await p().click('[data-testid="world-view-map"]');
  await p().waitForSelector(MAP);
}
/** Press Enter on a focused control the way a keyboard user does (works under the Map's key guard: only Escape is consumed). */
async function pressEnterOn(selector: string) {
  await p().focus(selector);
  await p().keyboard.press('Enter');
}

/** Every control OUTSIDE the panel chrome (and outside the open inbox itself) whose words or label name Reception. */
const receptionOpenersOutsideChrome = () =>
  p().evaluate(() =>
    [...document.querySelectorAll<HTMLElement>('button, [role="button"], a[href]')]
      .filter(
        (el) =>
          el.closest('[data-testid="di-office-panel"]') === null &&
          el.closest('[data-testid="di-reception-inbox"]') === null &&
          /reception/i.test(
            `${el.innerText} ${el.getAttribute('aria-label') ?? ''} ${el.getAttribute('title') ?? ''}`,
          ),
      )
      .map((el) => el.getAttribute('data-testid') ?? el.tagName),
  );

// Superseded and replaced (D-10, CONTROL-MAP locked rules "Reception left-rail only", Mission text "Reception is
// left-rail navigation only"): the former test proved THREE entrances (the desk in the world, its keyboard target and
// the sidebar entry). The rule is now ONE entrance in the chrome, and none in the world.
test(
  'SINGLE ENTRANCE (D-10): the inbox opens from the panel chrome entry only; nothing in the world opens it, the desk and the receptionist included; the receptionist still stands beside real agents without being one',
  async () => {
    await boot();
    const state = await p().evaluate(() => {
      const o = (
        window as unknown as {
          __diHarnessOffice: {
            characters: Map<number, { isReceptionist?: boolean }>;
            receptionist: { isReceptionist: boolean; id: number } | null;
          };
        }
      ).__diHarnessOffice;
      return {
        agents: o.characters.size,
        receptionist: o.receptionist?.isReceptionist === true,
        receptionistInAgentMap: o.receptionist ? o.characters.has(o.receptionist.id) : null,
      };
    });
    assert.ok(state.agents >= 4, 'real fixture agents are present');
    assert.equal(state.receptionist, true, 'and the receptionist is drawn');
    assert.equal(state.receptionistInAgentMap, false, 'but it is not one of them');

    // the one entrance opens the inbox, and closing it returns focus there
    await pressEnterOn(OPEN);
    await p().waitForSelector(INBOX);
    assert.equal((await dialog()).count, 1);
    await p().keyboard.press('Escape');
    await p().waitForSelector(INBOX, { state: 'detached' });
    assert.ok(await active(OPEN), 'focus returns to the entrance');

    // nothing in the world offers to open it
    assert.equal(
      await p().locator('[data-testid="di-reception-target"]').count(),
      0,
      'no desk target is drawn',
    );
    assert.deepEqual(
      await receptionOpenersOutsideChrome(),
      [],
      'no control outside the chrome names Reception',
    );

    // and real clicks across the whole visible world (left of the docked panel) never open it
    const panel = (await p().locator('[data-testid="di-office-panel"]').boundingBox())!;
    const probe = await clickAcrossWorld(
      p(),
      { left: 0, top: 0, right: panel.x, bottom: 1000 },
      24,
    );
    assert.ok(probe.clicks > 500, `the world was probed (${probe.clicks} clicks)`);
    // every click probed the WORLD (pass C: a Mission opened by a click is undone before the next one)
    assert.equal(probe.worldClicks, probe.clicks, `every probe click landed on the world (${probe.worldClicks} of ${probe.clicks})`);
    console.info(`WORLD PROBE (%s): %d clicks, %d on the world, %d opened a Mission, the first at click %s`, 'operational face', probe.clicks, probe.worldClicks, probe.missionsOpened, String(probe.firstMissionAt));
    assert.equal(probe.inboxOpened, false, 'no click in the world opened the inbox');
  },
  STEPS_MS,
);

// Superseded and replaced (D-10): the former test kept the world desk target off the open Map. There is no desk target;
// what must hold through the Map is that the chrome entrance stays the only one.
test(
  'SINGLE ENTRANCE (D-10): the chrome entrance is the only Reception opener before, during and after the Map',
  async () => {
    await boot();
    const entrances = () => p().locator(OPEN).count();
    assert.equal(await entrances(), 1);
    assert.deepEqual(await receptionOpenersOutsideChrome(), []);
    await openMap();
    assert.equal(await entrances(), 1, 'still reachable while the Map is open');
    assert.deepEqual(await receptionOpenersOutsideChrome(), [], 'the Map adds no opener');
    await p().keyboard.press('Escape');
    await p().waitForSelector(MAP, { state: 'detached' });
    assert.equal(await entrances(), 1);
    assert.deepEqual(await receptionOpenersOutsideChrome(), []);
  },
  STEPS_MS,
);

test(
  'Reception opened while the Map is open owns the keyboard: arrows reach its cards, Escape closes Reception and not the Map, focus is restored',
  async () => {
    await boot();
    await openMap();
    // the sidebar entry is reachable by keyboard while the Map is open (the Map only consumes Escape)
    await pressEnterOn(OPEN);
    await p().waitForSelector(INBOX);
    assert.equal((await dialog()).count, 1);
    assert.equal(await p().locator(MAP).count(), 1, 'the Map is still open behind it');

    // the dialog's own keys work: ArrowDown from the search box lands on the first card, then Home/End rove
    await p().focus('[data-testid="di-reception-search"]');
    await p().keyboard.press('ArrowDown');
    assert.ok(await active('[data-testid="di-reception-entry"]'), 'ArrowDown reached the first card');
    await p().keyboard.press('End');
    assert.ok(await active('[data-testid="di-reception-entry"]'));

    // Escape closes Reception ONLY
    await p().keyboard.press('Escape');
    await p().waitForSelector(INBOX, { state: 'detached' });
    assert.equal(await p().locator(MAP).count(), 1, 'the Map did NOT close');
    assert.ok(await active(OPEN), 'focus returned to the control that opened Reception');
    assert.equal(
      await p().evaluate(() => document.getElementById('root')!.hasAttribute('inert')),
      false,
    );

    // the Map's isolation resumes: the next Escape closes the Map
    await p().keyboard.press('Escape');
    await p().waitForSelector(MAP, { state: 'detached' });
  },
  STEPS_MS,
);

test(
  'Tab stays inside a Reception opened over the Map (the Map does not take the keys)',
  async () => {
    await boot();
    await openMap();
    await pressEnterOn(OPEN);
    await p().waitForSelector(INBOX);
    for (let i = 0; i < 25; i++) {
      await p().keyboard.press('Tab');
      assert.ok(
        await p().evaluate(() =>
          document.querySelector('[data-testid="di-reception-inbox"]')!.contains(document.activeElement),
        ),
        `Tab ${i + 1} stayed inside the dialog`,
      );
    }
    await p().click('[data-testid="di-reception-inbox-close"]');
    await p().waitForSelector(INBOX, { state: 'detached' });
    assert.equal(await p().locator(MAP).count(), 1, 'closing Reception leaves the Map as it was');
  },
  STEPS_MS,
);
