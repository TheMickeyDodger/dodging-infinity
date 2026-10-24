/*
 * THE THREE TIERS OF EXPOSURE, rendered, in the states in which candidate 6 FAILED them (found by its smoke and the diagnostic that followed; every state below is one that was measured failing):
 *   (1) a PERSISTENT REQUIRED NOTICE stays exposed (the fixture label);
 *   (2) ACTIVE FOCUS stays exposed, INCLUDING AT THE MOMENT OF A STATE CHANGE (a transition is a state measured in its own right, with the element that holds focus named, not the gap between two
 *       measured states: reachable by a LATER Tab is not exposed at the LANDING);
 *   (3) every CONTROL stays RECOVERABLY reachable (by scroll, focus or keyboard), which is NOT simultaneous visibility: an unfocused control that is out of view is not a failure here.
 * Explicitly NOT asserted: that every previously visible unfocused item stays visible (that would forbid ordinary scrolling).
 * TWO DIFFERENT QUESTIONS, never merged: what RECEIVES THE POINTER (document.elementFromPoint with the real pointer rules) and what is PAINTED there (the paint-aware occlusion helper, which looks through
 * what paints nothing). Each assertion says which it is.
 * LIMITS: system Chrome, the dev server; 320 x 225 is 400 percent zoom on a 1280 x 900 window as a SCALED VIEWPORT (a zoom-equivalent, not a native zoom test); the version label is shown with a version
 * the harness sends; the sizes listed are the sizes tested.
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
import { occlusion } from './diOcclusion.ts';
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
const minimizeOffice = async () => {
  await p().click('[data-testid="di-office-panel-minimize"]');
  await p().waitForSelector('[data-testid="di-office-panel-collapsed"]');
  await p().waitForTimeout(400);
};
const pause = (ms: number) => p().waitForTimeout(ms);

const setVersion = async (lastSeen: string) => {
  socket!.send(
    JSON.stringify({
      type: 'settingsLoaded',
      soundEnabled: false,
      showAreas: false,
      extensionVersion: '9.9.0',
      lastSeenVersion: lastSeen,
    }),
  );
  await pause(400);
};
const BANNER = '[data-testid="di-fixture-banner"]';

/** How much of an element is exposed: its box clipped by every scrolling or clipping ancestor and the window, and what receives the POINTER at the centre of what is left (real pointer rules). */
const exposure = (selector: string) =>
  p().evaluate((sel) => {
    const el = document.querySelector(sel) as HTMLElement | null;
    if (!el) return null;
    const r = el.getBoundingClientRect();
    let l = 0;
    let t = 0;
    let rr = innerWidth;
    let b = innerHeight;
    // WHAT THIS MODELS, AND WHAT IT DOES NOT. It clips the element by the overflow of its ancestors along the containing-block chain for position static, relative, absolute and fixed (a fixed box is not
    // clipped by its ancestors' overflow, an absolute box is clipped from its nearest positioned ancestor upward). That is only valid where NO ancestor establishes a containing block by other means: a
    // transform, filter, backdrop-filter, perspective, will-change (of those) or contain on an ancestor makes it the containing block for fixed and absolute descendants. Those cases are NOT modelled:
    // they are DETECTED and returned as `unsupported`, and a test that meets one fails instead of trusting a clip it did not compute. (container-type is not checked either.) The browser's own hit
    // test at the visible centre (pointerHitsIt) is the independent ground truth that is reported beside the model.
    const unsupported: string[] = [];
    for (let u: HTMLElement | null = el.parentElement; u; u = u.parentElement) {
      const ucs = getComputedStyle(u);
      const why: string[] = [];
      if (ucs.transform !== 'none') why.push('transform');
      if (ucs.filter !== 'none') why.push('filter');
      if (ucs.backdropFilter !== 'none' && ucs.backdropFilter !== '') why.push('backdrop-filter');
      if (ucs.perspective !== 'none') why.push('perspective');
      if (/transform|perspective|filter/.test(ucs.willChange)) why.push('will-change');
      if (/paint|layout|strict|content/.test(ucs.contain)) why.push('contain');
      if (why.length > 0)
        unsupported.push(
          `${u.tagName.toLowerCase()}${u.getAttribute('data-testid') ? `[${u.getAttribute('data-testid')}]` : ''}: ${why.join(', ')}`,
        );
    }
    let c: HTMLElement = el;
    let a: HTMLElement | null = el.parentElement;
    while (a) {
      const pos = getComputedStyle(c).position;
      if (pos === 'fixed') break;
      if (pos === 'absolute') {
        while (a && getComputedStyle(a).position === 'static') a = a.parentElement;
        if (!a) break;
      }
      const cs = getComputedStyle(a);
      const ar = a.getBoundingClientRect();
      if (cs.overflowX !== 'visible') {
        l = Math.max(l, ar.left);
        rr = Math.min(rr, ar.right);
      }
      if (cs.overflowY !== 'visible') {
        t = Math.max(t, ar.top);
        b = Math.min(b, ar.bottom);
      }
      c = a;
      a = a.parentElement;
    }
    const vw = Math.max(0, Math.min(r.right, rr) - Math.max(r.left, l));
    const vh = Math.max(0, Math.min(r.bottom, b) - Math.max(r.top, t));
    const area = r.width * r.height;
    const cx = (Math.max(r.left, l) + Math.min(r.right, rr)) / 2;
    const cy = (Math.max(r.top, t) + Math.min(r.bottom, b)) / 2;
    const top = vw > 0 && vh > 0 ? document.elementFromPoint(cx, cy) : null;
    return {
      box: [Math.round(r.left), Math.round(r.top), Math.round(r.right), Math.round(r.bottom)],
      visibleFraction: area > 0 ? Math.round(((vw * vh) / area) * 1000) / 1000 : 0,
      unsupported,
      pointerTop: top ? (top.getAttribute('data-testid') ?? top.tagName.toLowerCase()) : 'nothing',
      pointerHitsIt: !!top && el.contains(top),
    };
  }, selector);

/** A persistent notice is EXPOSED when all of its box is in the window (and inside every clipping ancestor) and nothing PAINTS over its words (paint, not pointer: the banner takes no pointer by design). */
const noticeProblem = async (selector: string): Promise<string | null> => {
  const e = await exposure(selector);
  if (e === null) return `${selector} is not in the page`;
  if (e.unsupported.length > 0)
    return `the exposure model does not cover this element's ancestors (${e.unsupported.join('; ')}), so a clip it did not compute is not trusted`;
  if (e.visibleFraction < 1)
    return `only ${Math.round(e.visibleFraction * 100)} percent of it is inside the window (box ${JSON.stringify(e.box)})`;
  const o = await occlusion(p(), selector);
  if (o.sampled < 2) return `too few of its words were sampled to judge paint (${o.sampled})`;
  if (o.covered.length > 0)
    return `something is PAINTED over its words (${[...new Set(o.covered.map((c) => c.by))].join(', ')})`;
  return null;
};

/** The label of a restore chip: how much of its words lies inside the window and the tray's scrollport, and whether the pointer reaches the chip at the centre of its visible part. */
const chipExposure = (testid: string) =>
  p().evaluate((id) => {
    const chip = document.querySelector(`[data-testid="${id}"]`) as HTMLElement | null;
    if (!chip) return null;
    const tray = chip.closest('[data-testid="di-minimized-tray"]') as HTMLElement | null;
    const tr = tray ? tray.getBoundingClientRect() : { top: 0, bottom: innerHeight };
    const range = document.createRange();
    range.selectNodeContents(chip);
    const rects = [...range.getClientRects()].filter((q) => q.width > 0 && q.height > 0);
    let area = 0;
    let vis = 0;
    for (const q of rects) {
      area += q.width * q.height;
      const tt = Math.max(q.top, 0, tr.top);
      const bb = Math.min(q.bottom, innerHeight, tr.bottom);
      const ll = Math.max(q.left, 0);
      const rr = Math.min(q.right, innerWidth);
      vis += Math.max(0, bb - tt) * Math.max(0, rr - ll);
    }
    const cr = chip.getBoundingClientRect();
    const cx = (Math.max(cr.left, 0) + Math.min(cr.right, innerWidth)) / 2;
    const cy = (Math.max(cr.top, 0, tr.top) + Math.min(cr.bottom, innerHeight, tr.bottom)) / 2;
    const top = document.elementFromPoint(cx, cy);
    return {
      box: [Math.round(cr.left), Math.round(cr.top), Math.round(cr.right), Math.round(cr.bottom)],
      labelVisibleFraction: area > 0 ? Math.round((vis / area) * 100) / 100 : 0,
      pointerHitsChip: !!top && chip.contains(top),
      focusedTestId:
        (document.activeElement as HTMLElement | null)?.getAttribute('data-testid') ?? null,
    };
  }, testid);

const openReception = async () => {
  await p().click('[data-testid="di-open-reception"]');
  await p().waitForSelector('[data-testid="di-reception-inbox"]');
  await pause(300);
};
const minimizeReception = async () => {
  await p().click('[data-testid="di-reception-minimize"]');
  await p().waitForSelector('[data-testid="di-reception-restore"]');
  await pause(300);
};

// ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------
// F1: the strip's scroll container is NOT a tab stop. With the office open at 320 x 225 the world yields (its controls are inert and hidden) and the strip still overflows, so Chrome made the
// scroller itself keyboard-focusable: a tab stop wholly outside the window (tier 2: a focus nobody can see). COMPANION: the stops that SHOULD exist are still reached in the same traversals, so
// excluding the scroller is not achieved by excluding everything.
for (const fixture of [true, false]) {
  test(
    `F1: the world strip's scroll container is not a tab stop at 320 x 225 with the office open (${fixture ? 'with' : 'without'} the fixture label), forward and backward, and the office panel's own stops are still reached`,
    async () => {
      await boot(320, 225, { fixture });
      const failures: string[] = [];
      for (const backward of [false, true]) {
        const t = await tabTraversal(p(), {
          max: 400,
          regions: {
            panel: '[data-testid="di-office-panel"]',
            strip: '[data-testid="di-minimized-tray"]',
          },
          backward,
        });
        const dir = backward ? 'backward' : 'forward';
        if (t.stops.some((s) => s.id === 'di-world-rail'))
          failures.push(`${dir}: di-world-rail (the strip's scroller) is a tab stop`);
        const panel = t.stops.filter((s) => s.region === 'panel');
        if (panel.length < 15)
          failures.push(
            `${dir}: only ${panel.length} office-panel stops were reached (the companion floor is 15)`,
          );
        for (const x of tabProblems(t, 0.0001).filter((q) =>
          q.why.some((w) => w.includes('none of its box is inside the window')),
        ))
          failures.push(`${dir}: ${x.id} is a tab stop with none of its box in the window`);
      }
      assert.deepEqual(failures, [], 'no invisible tab stop at 320 x 225 with the office open');
    },
    STEPS_MS,
  );
}

// ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------
// F2: THE FIXTURE LABEL (a persistent required notice, tier 1) stays exposed at 320 x 225 in EVERY state: at rest, with the City open by REAL keyboard and by REAL mouse (the reveal scrolled the strip
// and carried the label out), and while Tabbing the camera controls, with the district list open and with the Map open (focus-driven scroll of the strip did the same on the large world, measured: the
// banner at 191 to 293 in a 225 window). Synthetic content rendering unlabelled is the failure this ranks above a layout defect.
const F2_STATES: Array<{ name: string; arrange: () => Promise<void> }> = [
  {
    name: 'at rest (office minimized, focus on the Restore-office chip)',
    arrange: async () => undefined,
  },
  {
    name: 'the City opened by a REAL Enter on the City button (focus on the button was ASSIGNED by script, not reached by Tab)',
    arrange: async () => {
      await p().focus('[data-testid="city-button"]');
      await p().keyboard.press('Enter');
      await p().waitForSelector('#city-controls');
      await pause(500);
    },
  },
  {
    name: 'the City opened by a REAL mouse click on the City button',
    arrange: async () => {
      const b = await p().locator('[data-testid="city-button"]').boundingBox();
      assert.ok(
        b !== null && b.y >= 0 && b.y + b.height <= 225,
        `the City button is on screen to be pressed (${JSON.stringify(b)})`,
      );
      await p().mouse.click(b.x + b.width / 2, b.y + b.height / 2);
      await p().waitForSelector('#city-controls');
      await pause(500);
    },
  },
  {
    name: 'the district list opened by a REAL Enter on the districts control (focus ASSIGNED by script; that Tab reaches these controls is the every-stop test below)',
    arrange: async () => {
      await p().focus('[data-testid="world-view-districts"]');
      await p().keyboard.press('Enter');
      await p().waitForSelector('[data-testid="world-district-list"]');
      await pause(400);
    },
  },
  {
    name: 'the Map opened by a REAL Enter on the Map control (focus ASSIGNED by script; Tab reachability is the every-stop test below)',
    arrange: async () => {
      await p().focus('[data-testid="world-view-map"]');
      await p().keyboard.press('Enter');
      await p().waitForSelector('[data-testid="world-map"]');
      await pause(500);
    },
  },
];
for (const state of F2_STATES) {
  test(
    `F2: the fixture label stays EXPOSED at 320 x 225: ${state.name}`,
    async () => {
      await boot(320, 225, { fixture: true });
      await minimizeOffice();
      await p().waitForSelector('[data-testid="ambient-legend"]', {
        state: 'attached',
        timeout: 30_000,
      });
      await pause(400);
      await state.arrange();
      const problem = await noticeProblem(BANNER);
      assert.equal(problem, null, `the fixture label (${state.name}): ${problem}`);
    },
    STEPS_MS,
  );
}
test(
  'F2: the fixture label stays EXPOSED at 320 x 225 at EVERY stop of a Tab walk through the camera controls (the first zoom button is focused by SCRIPT, every later stop by a REAL Tab; the strip scrolls with focus, the label does not go with it), and the focused stop is itself exposed',
  async () => {
    await boot(320, 225, { fixture: true });
    await minimizeOffice();
    await p().waitForSelector('[data-testid="ambient-legend"]', {
      state: 'attached',
      timeout: 30_000,
    });
    await pause(400);
    await p().focus('[data-testid="world-zoom-buttons"] button:not([disabled])');
    const problems: string[] = [];
    let visited = 0;
    for (let i = 0; i < 12; i += 1) {
      const id = await p().evaluate(
        () =>
          (document.activeElement as HTMLElement | null)?.getAttribute('data-testid') ??
          document.activeElement?.tagName ??
          'BODY',
      );
      if (id === 'BODY') break;
      visited += 1;
      const np = await noticeProblem(BANNER);
      if (np) problems.push(`stop ${i + 1} (${id}): the label: ${np}`);
      const fe = await p().evaluate(() => {
        const a = document.activeElement as HTMLElement | null;
        if (!a) return 0;
        const r = a.getBoundingClientRect();
        const w = Math.max(0, Math.min(r.right, innerWidth) - Math.max(r.left, 0));
        const h = Math.max(0, Math.min(r.bottom, innerHeight) - Math.max(r.top, 0));
        return r.width * r.height > 0 ? (w * h) / (r.width * r.height) : 0;
      });
      if (fe < 0.99)
        problems.push(
          `stop ${i + 1} (${id}): the FOCUSED control is only ${Math.round(fe * 100)} percent inside the window`,
        );
      await p().keyboard.press('Tab');
    }
    assert.ok(
      visited >= 8,
      `the traversal visited the zoom buttons and the seven camera stops (${visited} stops)`,
    );
    assert.deepEqual(problems, [], 'the label and the focused stop are exposed at every stop');
  },
  STEPS_MS,
);

// ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------
// F3: THE VERSION LABEL (a control, and since the activation fix a TAB STOP, which it was not before this slice) is exposed, and its two failures are kept apart:
//   POINTER INTERCEPTION (what receives the pointer): at 320 x 568 and 360 x 640 an INVISIBLE full-width wrapper of the zoom buttons took the pointer over the label (0 of 20 and 8 of 20 hits);
//   VISIBLE PAINT (what is drawn there): at 320 x 225 the ambient legend was painted over the label (the paint-aware traversal: 5 or 17 of 25 points on it).
// NOT restricted to 225. The roomy size (720 x 450 at 2x) is the companion that already passed.
for (const [w, h, scale] of [
  [320, 568, 1],
  [360, 640, 1],
  [320, 225, 1],
  [720, 450, 2],
] as const) {
  test(
    `F3: the version label is EXPOSED at ${w} x ${h}${scale === 2 ? ' at 2x (zoom-equivalent)' : ''}: the POINTER reaches it at the centre, and NOTHING IS PAINTED over its words`,
    async () => {
      await boot(w, h, { fixture: true, scale });
      await setVersion('9.9');
      await minimizeOffice();
      await p().waitForSelector('.di-version-label', { timeout: 15_000 });
      await pause(400);
      const e = await exposure('.di-version-label');
      assert.ok(e !== null, 'the version label is in the page');
      const failures: string[] = [];
      if (e.unsupported.length > 0)
        failures.push(`the exposure model does not cover the label's ancestors (${e.unsupported.join('; ')})`);
      if (e.visibleFraction < 1)
        failures.push(
          `only ${Math.round(e.visibleFraction * 100)} percent of the label is inside the window (box ${JSON.stringify(e.box)})`,
        );
      if (!e.pointerHitsIt)
        failures.push(
          `POINTER: what receives the pointer at its centre is ${e.pointerTop}, not the label`,
        );
      const o = await occlusion(p(), '.di-version-label');
      if (o.sampled < 1) failures.push(`PAINT: too few words sampled (${o.sampled})`);
      if (o.covered.length > 0)
        failures.push(
          `PAINT: ${[...new Set(o.covered.map((c) => c.by))].join(', ')} is painted over its words`,
        );
      assert.deepEqual(failures, [], `the version label at ${w} x ${h}`);
    },
    STEPS_MS,
  );
}
test(
  'F3: an invisible wrapper intercepts NO pointer over the world: at 320 x 568 a point in the empty top-right of the strip (right of the zoom buttons) reaches what is under it, not the zoom buttons wrapper',
  async () => {
    await boot(320, 568, { fixture: true });
    await minimizeOffice();
    await pause(400);
    const r = await p().evaluate(() => {
      const wrap = document.querySelector('[data-testid="world-zoom-buttons"]') as HTMLElement;
      const buttons = [...wrap.querySelectorAll('button')].map((b) => b.getBoundingClientRect());
      const right = Math.max(...buttons.map((b) => b.right));
      const wr = wrap.getBoundingClientRect();
      const x = right + 40;
      const y = wr.top + wr.height / 2;
      const top = document.elementFromPoint(x, y);
      return {
        x: Math.round(x),
        y: Math.round(y),
        wrapperRight: Math.round(wr.right),
        buttonsRight: Math.round(right),
        top: top ? (top.getAttribute('data-testid') ?? top.tagName.toLowerCase()) : 'nothing',
        intercepted: !!top && wrap.contains(top),
      };
    });
    assert.equal(
      r.intercepted,
      false,
      `a point ${r.x - r.buttonsRight} px right of the zoom buttons is intercepted by the wrapper (wrapper right edge ${r.wrapperRight}, buttons end ${r.buttonsRight}; the top element is ${r.top})`,
    );
  },
  STEPS_MS,
);

// ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------
// F4: no FOCUSED control of the open office panel is under the sticky in-panel note. Measured failing: the panel's two header controls (Minimize, Reception) at the top of the panel, covered by the note
// (PAINT, forced-pointer paint-aware traversal) in the state 'office open, a minimized surface in the strip' at 720 x 450 at 2x, 320 x 568 and 360 x 640, and many more at 320 x 225 going BACKWARD.
for (const [w, h, scale] of [
  [720, 450, 2],
  [360, 640, 1],
  [320, 568, 1],
  [320, 225, 1],
] as const) {
  test(
    `F4: with the office OPEN and the reception MINIMIZED into the strip, no focused control is covered at ${w} x ${h}${scale === 2 ? ' at 2x (zoom-equivalent)' : ''}, forward and backward`,
    async () => {
      await boot(w, h, { fixture: true, scale });
      await openReception();
      await minimizeReception();
      const failures: string[] = [];
      for (const backward of [false, true]) {
        const t = await tabTraversal(p(), {
          max: 400,
          regions: {
            panel: '[data-testid="di-office-panel"]',
            strip: '[data-testid="di-minimized-tray"]',
          },
          backward,
        });
        const dir = backward ? 'backward' : 'forward';
        const probs = tabProblems(t, 1);
        for (const x of probs) failures.push(`${dir}: ${x.id}: ${x.why[0]}`);
        if (t.stops.filter((s) => s.region === 'panel').length < 15)
          failures.push(
            `${dir}: fewer than 15 office-panel stops were reached (the companion floor)`,
          );
      }
      assert.deepEqual(failures, [], `the open office panel at ${w} x ${h}`);
    },
    STEPS_MS,
  );
}

// ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------
// F5, THE LANDING (tier 2 at a STATE CHANGE): minimizing a surface hands focus to its restore chip in the tray. The state the diagnostic first named 'at rest' was this LANDING (focus on the Restore-office
// chip, the tray not scrolled), and at 320 x 225 the focused chip was 26 percent visible (0 percent and no pointer hit with three chips): focus invisible. Each minimize transition is measured as a state of its
// own, naming the element that holds focus. RECOVERY (a later Tab, a wheel) works and is asserted separately below; it does not excuse the landing.
for (const [w, h, how] of [
  [320, 225, 'mouse'],
  [320, 225, 'keyboard'],
  [320, 568, 'mouse'],
  [320, 568, 'keyboard'],
] as const) {
  test(
    `F5 LANDING: after each MINIMIZE by a REAL ${how === 'mouse' ? 'mouse click' : 'Enter (focus on the Minimize button ASSIGNED by script)'} the restore chip that holds focus is fully exposed (its words inside the window and the tray's scrollport, the pointer reaching it), at ${w} x ${h}`,
    async () => {
      await boot(w, h, { fixture: true });
      const press = async (selector: string) => {
        if (how === 'mouse') await p().click(selector);
        else {
          await p().focus(selector);
          await p().keyboard.press('Enter');
        }
      };
      const failures: string[] = [];
      const land = async (transition: string, chip: string) => {
        const e = await chipExposure(chip);
        if (e === null) return void failures.push(`${transition}: ${chip} is not in the page`);
        if (e.focusedTestId !== chip)
          failures.push(`${transition}: focus is on ${String(e.focusedTestId)}, not on ${chip}`);
        if (e.labelVisibleFraction < 1)
          failures.push(
            `${transition}: the FOCUSED ${chip} shows only ${Math.round(e.labelVisibleFraction * 100)} percent of its label (box ${JSON.stringify(e.box)})`,
          );
        if (!e.pointerHitsChip)
          failures.push(
            `${transition}: the pointer at the centre of the visible part of ${chip} does not reach it`,
          );
      };
      await openReception();
      await press('[data-testid="di-reception-minimize"]');
      await p().waitForSelector('[data-testid="di-reception-restore"]');
      await pause(300);
      await land('after minimizing the reception', 'di-reception-restore');
      await press('[data-testid="di-office-panel-minimize"]');
      await p().waitForSelector('[data-testid="di-office-panel-collapsed"]');
      await pause(400);
      await land('after minimizing the office', 'di-office-panel-collapsed');
      assert.deepEqual(failures, [], `the minimize landings at ${w} x ${h}`);
    },
    STEPS_MS,
  );
}
test(
  'F5 RECOVERY (separate from the landing): at 320 x 225 with two chips in the tray, a REAL Tab to each chip exposes its label and a REAL wheel over the tray scrolls it, whatever the focused chip was a moment ago',
  async () => {
    await boot(320, 225, { fixture: true });
    await openReception();
    await minimizeReception();
    await p().click('[data-testid="di-office-panel-minimize"]');
    await p().waitForSelector('[data-testid="di-office-panel-collapsed"]');
    await pause(400);
    const failures: string[] = [];
    // a fresh real Tab to each chip (from a sentinel at the document start, so no focus is pre-arranged)
    for (const chip of ['di-reception-restore', 'di-office-panel-collapsed']) {
      await p().evaluate(() => {
        (document.activeElement as HTMLElement | null)?.blur?.();
        const s = document.createElement('span');
        s.tabIndex = -1;
        s.setAttribute('data-probe', 'true');
        document.body.prepend(s);
        s.focus();
      });
      let reached = false;
      for (let i = 0; i < 60 && !reached; i += 1) {
        await p().keyboard.press('Tab');
        reached = await p().evaluate(
          (id) =>
            (document.activeElement as HTMLElement | null)?.getAttribute('data-testid') === id,
          chip,
        );
      }
      await p().evaluate(() =>
        document.querySelectorAll('[data-probe]').forEach((e) => e.remove()),
      );
      if (!reached) {
        failures.push(`a real Tab never reached ${chip}`);
        continue;
      }
      const e = await chipExposure(chip);
      if (e === null || e.labelVisibleFraction < 1 || !e.pointerHitsChip)
        failures.push(`after a real Tab, ${chip} is not fully exposed (${JSON.stringify(e)})`);
    }
    // a real wheel over the tray, from the top
    await p().evaluate(() => {
      (document.querySelector('[data-testid="di-minimized-tray"]') as HTMLElement).scrollTop = 0;
    });
    await p().mouse.move(299, 112);
    await p().mouse.wheel(0, 400);
    await pause(300);
    const moved = await p().evaluate(
      () => (document.querySelector('[data-testid="di-minimized-tray"]') as HTMLElement).scrollTop,
    );
    if (moved <= 0) failures.push('a real wheel over the tray did not scroll it');
    assert.deepEqual(failures, [], 'recovery of the minimized tray at 320 x 225');
  },
  STEPS_MS,
);
test(
  'F5 RESTORE LANDING: restoring the office from its chip puts focus on a control that is fully inside the window (the control the surface kept, never the page body), at 320 x 225',
  async () => {
    await boot(320, 225, { fixture: true });
    await p().click('[data-testid="di-office-panel-minimize"]');
    await p().waitForSelector('[data-testid="di-office-panel-collapsed"]');
    await pause(400);
    await p().click('[data-testid="di-office-panel-collapsed"]');
    await p().waitForSelector('[data-testid="di-office-panel-collapsed"]', { state: 'detached' });
    await pause(500);
    const f = await p().evaluate(() => {
      const a = document.activeElement as HTMLElement | null;
      if (!a || a === document.body) return { body: true as const };
      const r = a.getBoundingClientRect();
      const w = Math.max(0, Math.min(r.right, innerWidth) - Math.max(r.left, 0));
      const hh = Math.max(0, Math.min(r.bottom, innerHeight) - Math.max(r.top, 0));
      return {
        body: false as const,
        id: a.getAttribute('data-testid') ?? a.tagName.toLowerCase(),
        fraction: r.width * r.height > 0 ? (w * hh) / (r.width * r.height) : 0,
      };
    });
    assert.equal(f.body, false, 'focus is on a control after the restore, not on the page body');
    if (!f.body)
      assert.ok(
        f.fraction >= 0.99,
        `the focused ${f.id} is only ${Math.round(f.fraction * 100)} percent inside the window after the restore`,
      );
  },
  STEPS_MS,
);
