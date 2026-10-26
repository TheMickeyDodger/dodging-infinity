/**
 * THE FIXTURE LABEL, rendered. Through the ACTUAL App, over the SHIPPED living metropolis, in an EXPLICIT fixture preview (fixtureMode on: the floating
 * banner, the in-panel note and the City popover's note exist only then). Nothing here is live Mission data; integration over source, not the production package.
 *
 *  1. THE STICKY NOTE IS OPAQUE. Its background is a tint; a shorthand later in the rule once reset the opaque backing, so content scrolling beneath it would show
 *     through. A source check cannot see a shorthand reset, so this measures the RENDERED note: the pixels of the note's own box with the content beneath it
 *     visible must be IDENTICAL to the pixels with that content hidden. A deliberately transparent control must differ, so the check cannot pass vacuously.
 *  2. THE BANNER OBSTRUCTS NOTHING, in the states where it used to: the update notice up, the City popover open, the districts list open, at the sizes we
 *     capture. Measured by hit-test over the chrome (diOcclusion.ts): no sampled point of any chrome root is covered BY the banner. THE LIMIT: an empty covered list
 *     says only that none of the SAMPLED points was covered; it is not proof of visibility or reachability, so each state also needs a positive companion (a minimum
 *     sample count, and a fixture label that is actually PAINTED: CSS-visible, inside the window, every hit-test point on it landing on it).
 */
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

import type { Browser, Page } from 'playwright-core';
import { chromium } from 'playwright-core';
import { PNG } from 'pngjs';
import type { ViteDevServer } from 'vite';
import { createServer } from 'vite';
import { afterAll, beforeAll, beforeEach, test } from 'vitest';
import type { WebSocket } from 'ws';
import { WebSocketServer } from 'ws';

import type { DiOfficeState } from '../../core/src/messages.ts';
import { occlusion } from './diOcclusion.ts';
import { tabTraversal } from './diTabTraversal.ts';

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
async function showUpdateNotice(): Promise<void> {
  socket!.send(
    JSON.stringify({
      type: 'settingsLoaded',
      soundEnabled: false,
      showAreas: false,
      extensionVersion: '9.9.0',
      lastSeenVersion: '1.0',
    }),
  );
  await p().waitForTimeout(400);
}
const minimizeOffice = async () => {
  await p().click('[data-testid="di-office-panel-minimize"]');
  await p().waitForSelector('[data-testid="di-office-panel-collapsed"]');
  await p().waitForTimeout(400);
};
const pause = (ms: number) => p().waitForTimeout(ms);
/** A fixture label is PAINTED when it is CSS-visible, its whole box is inside the window, and every hit-test point over it (pointer-events neutralized) lands on it. */
const paintedLabels = () =>
  p().evaluate(() => {
    const probe = document.createElement('style');
    probe.textContent = '*, *::before, *::after { pointer-events: auto !important; }';
    document.head.append(probe);
    const out: Record<
      string,
      { cssVisible: boolean; insideWindow: boolean; hit: string; painted: boolean }
    > = {};
    for (const id of ['di-fixture-banner', 'di-fixture-banner-in-panel', 'di-mr-fixture-banner']) {
      const el = document.querySelector(`[data-testid="${id}"]`) as HTMLElement | null;
      if (!el) continue;
      const r = el.getBoundingClientRect();
      const cssVisible =
        el.checkVisibility({ checkVisibilityCSS: true }) && r.width > 0 && r.height > 0;
      const insideWindow =
        r.left >= -0.5 &&
        r.right <= innerWidth + 0.5 &&
        r.top >= -0.5 &&
        r.bottom <= innerHeight + 0.5;
      let hit = 0;
      let n = 0;
      if (cssVisible && insideWindow)
        for (const fy of [0.15, 0.5, 0.85])
          for (const fx of [0.1, 0.5, 0.9]) {
            n += 1;
            const top = document.elementFromPoint(r.left + r.width * fx, r.top + r.height * fy);
            if (top && el.contains(top)) hit += 1;
          }
      out[id] = {
        cssVisible,
        insideWindow,
        hit: `${hit}/${n}`,
        painted: cssVisible && insideWindow && n > 0 && hit === n,
      };
    }
    probe.remove();
    return out;
  });

/** The difference between two decoded images of the note, LOCATED against the note's explicit rounded shape (border-radius), pixel by pixel. */
function locateDifferences(
  a: PNG,
  b: PNG,
  g: { left: number; top: number; right: number; bottom: number },
  radius: number,
) {
  const x0 = Math.floor(g.left);
  const y0 = Math.floor(g.top);
  const L = g.left - x0;
  const T = g.top - y0;
  const R = g.right - x0;
  const B = g.bottom - y0;
  const cx = (L + R) / 2;
  const cy = (T + B) / 2;
  const hw = (R - L) / 2;
  const hh = (B - T) / 2;
  const inside: Array<{ x: number; y: number; delta: number; depth: number }> = [];
  let outside = 0;
  let edge = 0;
  for (let y = 0; y < a.height; y += 1)
    for (let x = 0; x < a.width; x += 1) {
      const i = (y * a.width + x) * 4;
      let delta = 0;
      for (let c = 0; c < 4; c += 1)
        delta = Math.max(delta, Math.abs(a.data[i + c] - b.data[i + c]));
      if (delta === 0) continue;
      // signed distance from the pixel CENTRE to the rounded rectangle's edge: negative = inside the painted shape
      const dx = Math.max(Math.abs(x + 0.5 - cx) - (hw - radius), 0);
      const dy = Math.max(Math.abs(y + 0.5 - cy) - (hh - radius), 0);
      const depth = Math.hypot(dx, dy) - radius;
      if (depth > 0.5)
        outside += 1; // beyond the shape: the corner gap, where the panel legitimately shows
      else if (depth > -1.5)
        edge += 1; // the antialiased edge band
      else inside.push({ x, y, delta, depth: Math.round(depth * 100) / 100 }); // deeper than 1.5 px inside the painted shape: a real see-through pixel
    }
  return { inside, outside, edge };
}

test(
  'THE STICKY IN-PANEL NOTE IS OPAQUE: no DECODED pixel deeper than 1.5 px inside its rounded shape depends on the content scrolled beneath it, at every offset, and a transparent control proves content really lies beneath each time',
  async () => {
    await boot(320, 568, { fixture: true }); // the office is open at 320: the note is in the panel
    await pause(1200);
    const probe = await p().evaluate(() => {
      const aside = document.querySelector('[data-testid="di-office-panel"]') as HTMLElement;
      const note = document.querySelector(
        '[data-testid="di-fixture-banner-in-panel"]',
      ) as HTMLElement | null;
      return {
        scrollable: aside.scrollHeight - aside.clientHeight,
        noteThere: note !== null,
        radius: note ? parseFloat(getComputedStyle(note).borderTopLeftRadius) : 0,
      };
    });
    assert.ok(probe.noteThere, 'the in-panel note is rendered at 320 with the office open');
    assert.ok(
      probe.scrollable >= 60,
      `the panel really scrolls here (${probe.scrollable} px of range), so content can pass beneath the sticky note`,
    );
    assert.ok(
      probe.radius >= 4,
      `the note is rounded (${probe.radius} px): a bounding-box comparison would include pixels OUTSIDE its painted shape`,
    );
    const geometry = () =>
      p().evaluate(() => {
        const b = (
          document.querySelector('[data-testid="di-fixture-banner-in-panel"]') as HTMLElement
        ).getBoundingClientRect();
        return { left: b.left, top: b.top, right: b.right, bottom: b.bottom };
      });
    const hideBeneath = (hidden: boolean) =>
      p().evaluate((h) => {
        const aside = document.querySelector('[data-testid="di-office-panel"]') as HTMLElement;
        for (const child of Array.from(aside.children))
          if (!child.matches('[data-testid="di-fixture-banner-in-panel"]'))
            (child as HTMLElement).style.visibility = h ? 'hidden' : '';
      }, hidden);
    const shootNote = async () => {
      const g = await geometry();
      const x0 = Math.floor(g.left);
      const y0 = Math.floor(g.top);
      return PNG.sync.read(
        await p().screenshot({
          clip: { x: x0, y: y0, width: Math.ceil(g.right) - x0, height: Math.ceil(g.bottom) - y0 },
          animations: 'disabled',
        }),
      );
    };
    const setTransparent = (on: boolean) =>
      p().evaluate((t) => {
        const note = document.querySelector(
          '[data-testid="di-fixture-banner-in-panel"]',
        ) as HTMLElement;
        if (t) note.style.setProperty('background', 'transparent', 'important');
        else note.style.removeProperty('background');
      }, on);
    const failures: string[] = [];
    const report: string[] = [];
    for (const offset of [40, 80, probe.scrollable]) {
      await p().evaluate(
        (o) =>
          ((document.querySelector('[data-testid="di-office-panel"]') as HTMLElement).scrollTop =
            o),
        offset,
      );
      await pause(300);
      const g = await geometry();
      // THE MEASURE on the real note: decoded pixels with the content beneath visible and hidden, differences LOCATED against the rounded shape
      await hideBeneath(false);
      const withContent = await shootNote();
      await hideBeneath(true);
      const withoutContent = await shootNote();
      await hideBeneath(false);
      const real = locateDifferences(withContent, withoutContent, g, probe.radius);
      report.push(
        `offset ${offset}: real note, differing pixels inside the shape ${real.inside.length}, on the antialiased edge ${real.edge}, outside the shape ${real.outside}`,
      );
      if (real.inside.length > 0)
        failures.push(
          `offset ${offset}: ${real.inside.length} decoded pixels deeper than 1.5 px INSIDE the shape depend on what is beneath it (first: ${JSON.stringify(real.inside.slice(0, 3))})`,
        );
      // THE NEGATIVE CONTROL at the SAME offset: a transparent note must show the content in its interior (and this is the positive evidence that content lies beneath here)
      await setTransparent(true);
      await hideBeneath(false);
      const controlWith = await shootNote();
      await hideBeneath(true);
      const controlWithout = await shootNote();
      await hideBeneath(false);
      await setTransparent(false);
      const control = locateDifferences(controlWith, controlWithout, g, probe.radius);
      report.push(
        `offset ${offset}: transparent CONTROL, differing pixels inside the shape ${control.inside.length}`,
      );
      if (control.inside.length < 30)
        failures.push(
          `offset ${offset}: UNINFORMATIVE, a transparent note shows only ${control.inside.length} content pixels in its interior here (the measure could pass vacuously)`,
        );
    }
    console.log(
      `STICKY NOTE OPACITY (decoded pixels, located against the rounded shape, radius ${probe.radius}px)\n${report.join('\n')}`,
    );
    assert.deepEqual(
      failures,
      [],
      'inside its rounded shape the note is opaque at every offset, and every offset was informative (the transparent control saw content beneath the interior)',
    );
  },
  STEPS_MS,
);

/*
 * THE OCCLUSION HELPER'S CLIP CORRECTION, shown with TEETH. The helper used to sample text rects as the browser reports them, which ignore ancestor clipping, so text scrolled out of a
 * scroll region was "covered" by whatever really was painted at those coordinates (a banner), although the text was never drawn there. It now samples only the VISIBLE part of each rect
 * (per axis, along the containing-block chain) and reports what it set aside. Without the demonstrations below, excluding clipped points could silently blind it (a false positive fixed by
 * making a false negative); each test pairs a positive companion (visible content IS sampled, a floor) with the case that must change. Transparent overlays, no colours: a hit test
 * returns the overlay wherever it is drawn.
 */
const injectClipFixture = (html: string) =>
  p().evaluate((markup) => {
    const host = document.createElement('div');
    host.id = 'zz-clip-host';
    host.innerHTML = markup;
    document.body.append(host);
  }, html);

test('OCCLUSION HELPER (1): text scrolled out of a scroll region is NOT sampled and is REPORTED; the visible text beside it IS sampled (a floor), so it cannot pass by sampling nothing', async () => {
  await boot(1100, 700, { fixture: true });
  await injectClipFixture(`
    <div id="zz-root" style="z-index:99998;position:fixed;left:340px;top:200px;width:220px;height:60px;overflow:auto;">
      <div style="position:relative;height:400px;">
        <span style="position:absolute;left:6px;top:8px">visible words in the scrollport</span>
        <span style="position:absolute;left:6px;top:250px">scrolled out words</span>
      </div>
    </div>
    <div id="zz-over" data-testid="zz-over" style="position:fixed;left:340px;top:440px;width:220px;height:60px;pointer-events:none;z-index:99999">an overlay where the scrolled-out text would be</div>`);
  const o = await occlusion(p(), '#zz-root');
  assert.deepEqual(
    o.covered,
    [],
    'the overlay sits only over text that is not drawn there: nothing is covered',
  );
  assert.ok(
    o.sampled >= 3,
    `the visible text is sampled (${o.sampled} points): the floor that stops a vacuous pass`,
  );
  assert.ok(
    o.clippedAway >= 1 && o.clippedAwayItems.some((x) => x.text.startsWith('scrolled out')),
    `the scrolled-out text is counted and NAMED (${JSON.stringify(o.clippedAwayItems)})`,
  );
});

test('OCCLUSION HELPER (2): a REAL overlay over text that IS inside the scrollport still FAILS (the false positive is gone without creating a false negative)', async () => {
  await boot(1100, 700, { fixture: true });
  await injectClipFixture(`
    <div id="zz-root" style="z-index:99998;position:fixed;left:340px;top:200px;width:220px;height:60px;overflow:auto;">
      <div style="position:relative;height:400px;">
        <span style="position:absolute;left:6px;top:8px">visible words in the scrollport</span>
        <span style="position:absolute;left:6px;top:250px">scrolled out words</span>
      </div>
    </div>
    <div id="zz-over" data-testid="zz-over" style="position:fixed;left:340px;top:200px;width:220px;height:40px;pointer-events:none;z-index:99999">an overlay over the visible words</div>`);
  const o = await occlusion(p(), '#zz-root');
  assert.ok(o.sampled >= 3, `the visible text is sampled (${o.sampled} points)`);
  assert.ok(
    o.covered.length >= 1 &&
      o.covered.every((x) => x.by === 'zz-over') &&
      o.covered.some((x) => x.text.startsWith('visible words')),
    `the overlay (pointer-events: none, over the visible words) is caught: ${JSON.stringify(o.covered)}`,
  );
  assert.ok(o.clippedAway >= 1, 'the scrolled-out text is still set aside and counted');
});

test('OCCLUSION HELPER (3): the clip is PER AXIS: with overflow-y: clip and overflow-x visible, text beyond the right edge IS sampled (and a covering overlay is caught) while text below is set aside', async () => {
  await boot(1100, 700, { fixture: true });
  await injectClipFixture(`
    <div id="zz-root" style="z-index:99998;position:fixed;left:340px;top:200px;width:100px;height:40px;overflow-x:visible;overflow-y:clip;">
      <div style="position:relative;height:200px;">
        <span style="position:absolute;left:6px;top:8px">inside</span>
        <span style="position:absolute;left:160px;top:8px">wide to the right</span>
        <span style="position:absolute;left:6px;top:120px">below the clip</span>
      </div>
    </div>
    <div id="zz-over" data-testid="zz-over" style="position:fixed;left:490px;top:200px;width:200px;height:40px;pointer-events:none;z-index:99999">overlay to the right of the box</div>`);
  const o = await occlusion(p(), '#zz-root');
  assert.ok(
    o.sampled >= 6,
    `both the inside text and the text past the right edge are sampled (${o.sampled} points)`,
  );
  assert.ok(
    o.covered.some((x) => x.text.startsWith('wide to the right') && x.by === 'zz-over'),
    `the overlay over the text past the right edge IS caught (x is not clipped): ${JSON.stringify(o.covered)}`,
  );
  assert.ok(
    o.clippedAwayItems.some((x) => x.text.startsWith('below the clip')),
    `the text below the clip (y is clipped) is set aside and named: ${JSON.stringify(o.clippedAwayItems)}`,
  );
});

test('OCCLUSION HELPER (4): a position: fixed child is NOT clipped by an overflow: hidden ancestor that is not its containing block, so it is still sampled and a covering overlay is caught', async () => {
  await boot(1100, 700, { fixture: true });
  await injectClipFixture(`
    <div id="zz-root" style="z-index:99998;position:fixed;left:340px;top:200px;width:100px;height:30px;overflow:hidden;">
      <span style="position:fixed;left:560px;top:320px">fixed child outside the root box</span>
    </div>
    <div id="zz-over" data-testid="zz-over" style="position:fixed;left:550px;top:312px;width:260px;height:40px;pointer-events:none;z-index:99999">overlay over the fixed child</div>`);
  const o = await occlusion(p(), '#zz-root');
  assert.ok(
    o.sampled >= 3,
    `the fixed child is sampled (${o.sampled} points): not wrongly set aside by the ancestor's overflow`,
  );
  assert.ok(
    o.covered.some((x) => x.text.startsWith('fixed child') && x.by === 'zz-over'),
    `the overlay over the fixed child IS caught: ${JSON.stringify(o.covered)}`,
  );
  assert.equal(o.clippedAway, 0, 'nothing is set aside');
});

/*
 * THE PAINT-AWARE HIT, with TEETH. The probes make every element hit-testable so an overlay that ignores the pointer is seen; that also returns TRANSPARENT layout containers (the left strip's
 * rail, grid and first-row cell paint nothing and let a click through), which made a visible, clickable control look "covered". They now look through an element that paints nothing at the point
 * (diPaintsAt.ts). The pairs below show it cannot turn into blindness: the SAME overlay, transparent, is looked through, and with a background, or with only text, is still reported.
 */
test('OCCLUSION HELPER (5): a TRANSPARENT overlay over text is looked through; the SAME overlay with a background IS reported; and with only its own TEXT IS reported', async () => {
  await boot(1100, 700, { fixture: true });
  await injectClipFixture(`
    <div id="zz-root" style="z-index:99998;position:fixed;left:340px;top:200px;width:240px;height:40px;">
      <span style="position:absolute;left:6px;top:8px">words under the overlay</span>
    </div>
    <div id="zz-over" data-testid="zz-over" style="position:fixed;left:340px;top:200px;width:240px;height:40px;pointer-events:none;z-index:99999"></div>`);
  const over = (css: string, text = '') =>
    p().evaluate(
      ([c, t]) => {
        const e = document.getElementById('zz-over') as HTMLElement;
        e.style.cssText = `position:fixed;left:340px;top:200px;width:240px;height:40px;pointer-events:none;z-index:99999;${c}`;
        e.textContent = t;
      },
      [css, text] as const,
    );
  const transparent = await occlusion(p(), '#zz-root');
  assert.ok(
    transparent.sampled >= 3,
    `the words are sampled (${transparent.sampled} points): the floor`,
  );
  assert.deepEqual(
    transparent.covered,
    [],
    'a transparent overlay paints nothing: it is looked through',
  );
  await over('background-color:white;');
  const painted = await occlusion(p(), '#zz-root');
  assert.ok(
    painted.covered.length >= 1 && painted.covered.every((x) => x.by === 'zz-over'),
    `the same overlay with a background IS reported: ${JSON.stringify(painted.covered)}`,
  );
  await over('', 'text of the overlay itself, wide enough to lie over the words and more text');
  const texty = await occlusion(p(), '#zz-root');
  assert.ok(
    texty.covered.length >= 1 && texty.covered.every((x) => x.by === 'zz-over'),
    `an overlay with only its own text over the words IS reported: ${JSON.stringify(texty.covered)}`,
  );
});

test('TAB TRAVERSAL (paint-aware hit): a stop under a TRANSPARENT overlay is fully drawn, a stop under a PAINTED overlay is reported covered by it', async () => {
  await boot(1100, 700, { fixture: true });
  await injectClipFixture(`
    <button type="button" data-testid="zz-btn-a" style="position:fixed;left:340px;top:200px;width:120px;height:40px;z-index:99998">stop A</button>
    <div data-testid="zz-over-a" style="position:fixed;left:330px;top:190px;width:200px;height:70px;pointer-events:none;z-index:99999"></div>
    <button type="button" data-testid="zz-btn-b" style="position:fixed;left:340px;top:300px;width:120px;height:40px;z-index:99998">stop B</button>
    <div data-testid="zz-over-b" style="position:fixed;left:330px;top:290px;width:200px;height:70px;pointer-events:none;z-index:99999;background-color:white"></div>`);
  const t = await tabTraversal(p(), { max: 220, regions: { zz: '#zz-clip-host' } });
  const a = t.stops.find((x) => x.id === 'zz-btn-a');
  const b = t.stops.find((x) => x.id === 'zz-btn-b');
  assert.ok(
    a && b,
    `both injected stops are in the tab sequence (${t.stops.map((x) => x.id).join(', ')})`,
  );
  assert.ok(
    a.sampled > 0 && a.hitSamples === a.sampled,
    `A, under a transparent overlay, is fully drawn (${a.hitSamples} of ${a.sampled}, covered by ${a.coveredBy.join(', ') || 'nothing'})`,
  );
  assert.ok(
    b.sampled > 0 && b.hitSamples < b.sampled && b.coveredBy.includes('zz-over-b'),
    `B, under a PAINTED overlay, is reported covered by it (${b.hitSamples} of ${b.sampled}, covered by ${b.coveredBy.join(', ') || 'nothing'})`,
  );
});

/*
 * THE PAIR THAT SEPARATES 'A TRANSPARENT CONTAINER WITH A PAINTED SUBTREE' FROM 'A GENUINELY EMPTY TRANSPARENT CONTAINER'. A hit stack answers what would receive a pointer event, which is not what is
 * painted: `inert` content is excluded from hit-testing whatever `pointer-events` says, so inert but painted content (the camera controls while the City panel is open) is absent from the stack even
 * with pointer events forced. The transparency of a container says nothing about what its children paint. Each probe therefore makes inert content hit-testable for the instant it samples (and
 * restores it), and these pairs hold it to that: the SAME transparent inert parent with a painted child over the target IS reported, empty it is NOT, and a painted child that is not inert IS reported.
 */
const PAIRED_FIXTURE = `
  <div id="zz-root" style="z-index:99998;position:fixed;left:340px;top:200px;width:240px;height:60px;">
    <span style="position:absolute;left:6px;top:8px">words under the container</span>
  </div>
  <div id="zz-parent" data-testid="zz-parent" inert style="position:fixed;left:340px;top:200px;width:240px;height:60px;pointer-events:none;z-index:99999">
    <div data-testid="zz-child" style="position:absolute;left:0;top:0;width:240px;height:60px;background-color:white"></div>
  </div>`;
test('OCCLUSION HELPER (6), PAIRED: a transparent INERT parent with a PAINTED child over the words IS reported; the SAME parent EMPTY is not; a painted child that is NOT inert IS reported', async () => {
  await boot(1100, 700, { fixture: true });
  await injectClipFixture(PAIRED_FIXTURE);
  const withChild = await occlusion(p(), '#zz-root');
  assert.ok(
    withChild.sampled >= 3,
    `the words are sampled (${withChild.sampled} points): the floor`,
  );
  assert.ok(
    withChild.covered.length >= 1 && withChild.covered.every((x) => x.by === 'zz-child'),
    `a painted child inside a transparent INERT parent, over the words, IS reported (the hit stack alone cannot see inert content): ${JSON.stringify(withChild.covered)}`,
  );
  await p().evaluate(() => {
    document.querySelector('[data-testid="zz-child"]')!.remove();
  });
  const empty = await occlusion(p(), '#zz-root');
  assert.ok(empty.sampled >= 3, 'the words are still sampled');
  assert.deepEqual(empty.covered, [], 'the SAME inert parent, EMPTY, covers nothing');
  await p().evaluate(() => {
    const parent = document.getElementById('zz-parent')!;
    parent.removeAttribute('inert');
    const child = document.createElement('div');
    child.setAttribute('data-testid', 'zz-child');
    child.style.cssText =
      'position:absolute;left:0;top:0;width:240px;height:60px;background-color:white';
    parent.append(child);
  });
  const notInert = await occlusion(p(), '#zz-root');
  assert.ok(
    notInert.covered.length >= 1 && notInert.covered.every((x) => x.by === 'zz-child'),
    `the same painted child, not inert, IS reported: ${JSON.stringify(notInert.covered)}`,
  );
  // the probe restores what it lifted: the parent is NOT inert now (as set above), and an inert one stays inert after a probe
  await p().evaluate(() => document.getElementById('zz-parent')!.setAttribute('inert', ''));
  await occlusion(p(), '#zz-root');
  assert.equal(
    await p().evaluate(() => document.getElementById('zz-parent')!.hasAttribute('inert')),
    true,
    'a probe leaves inert content inert',
  );
});

test('TAB TRAVERSAL, PAIRED: a stop under a transparent INERT parent with a PAINTED child is reported covered by that child; under the SAME parent EMPTY it is fully drawn; a REAL focusable control inside the inert subtree is EXCLUDED from the tab order while the outside stops are still reached; and the probe leaves everything inert', async () => {
  await boot(1100, 700, { fixture: true });
  // the inert subtree holds a REAL, otherwise-focusable control (a button), so the subject is CAPABLE of being a tab stop: a div without tabindex can never be one, and asserting its absence tests nothing
  await injectClipFixture(`
    <button type="button" data-testid="zz-btn-a" style="position:fixed;left:340px;top:200px;width:120px;height:40px;z-index:99998">stop A</button>
    <div data-testid="zz-parent-a" inert style="position:fixed;left:330px;top:190px;width:200px;height:70px;pointer-events:none;z-index:99999">
      <div data-testid="zz-child-a" style="position:absolute;left:0;top:0;width:200px;height:70px;background-color:white">
        <button type="button" data-testid="zz-inert-btn" style="position:absolute;left:150px;top:30px;width:40px;height:30px">in</button>
      </div>
    </div>
    <button type="button" data-testid="zz-btn-b" style="position:fixed;left:340px;top:300px;width:120px;height:40px;z-index:99998">stop B</button>
    <div data-testid="zz-parent-b" inert style="position:fixed;left:330px;top:290px;width:200px;height:70px;pointer-events:none;z-index:99999"></div>`);
  const inertIds = () =>
    p().evaluate(() =>
      Array.from(document.querySelectorAll('[inert]')).map(
        (e) => e.getAttribute('data-testid') ?? e.className.toString().slice(0, 30),
      ),
    );
  const before = await inertIds();
  assert.ok(
    before.includes('zz-parent-a') && before.includes('zz-parent-b'),
    `the fixture starts with both parents inert (${before.join(', ')})`,
  );
  const t = await tabTraversal(p(), { max: 220, regions: { zz: '#zz-clip-host' } });
  const a = t.stops.find((x) => x.id === 'zz-btn-a');
  const b = t.stops.find((x) => x.id === 'zz-btn-b');
  assert.ok(
    a && b,
    `both injected stops are in the tab sequence (${t.stops.map((x) => x.id).join(', ')})`,
  );
  assert.ok(
    a.sampled > 0 && a.hitSamples < a.sampled && a.coveredBy.includes('zz-child-a'),
    `A, under an inert parent whose child is painted, is reported covered by it (${a.hitSamples} of ${a.sampled}, covered by ${a.coveredBy.join(', ') || 'nothing'})`,
  );
  assert.ok(
    b.sampled > 0 && b.hitSamples === b.sampled,
    `B, under the same kind of inert parent EMPTY, is fully drawn (${b.hitSamples} of ${b.sampled})`,
  );
  // EXCLUSION of the right thing: the real button inside the inert subtree is not a stop
  assert.ok(
    !t.stops.some((x) => x.id === 'zz-inert-btn' || x.id === 'in'),
    `a real focusable control inside an inert subtree is NOT a tab stop (${t.stops.map((x) => x.id).join(', ')})`,
  );
  // ...PAIRED WITH continued reach of everything else: excluding the inert control must not be achieved by excluding everything. Both injected stops AND stops outside the injected host (the
  // application's own controls) are reached in the same traversal
  const outside = t.stops.filter((x) => x.region !== 'zz');
  assert.ok(
    outside.length >= 3,
    `stops outside the injected fixture are still reached in the same traversal (${outside.length}: ${outside.map((x) => x.id).join(', ')})`,
  );
  assert.deepEqual(
    await inertIds(),
    before,
    'everything that was inert before the traversal is inert after it',
  );
  // THE SUBJECT IS CAPABLE: once the parent is not inert, the very same button IS a tab stop, so its absence above is the inert subtree's doing and not the control's inability to take focus
  await p().evaluate(() =>
    document.querySelector('[data-testid="zz-parent-a"]')!.removeAttribute('inert'),
  );
  const t2 = await tabTraversal(p(), { max: 220, regions: { zz: '#zz-clip-host' } });
  assert.ok(
    t2.stops.some((x) => x.id === 'zz-inert-btn' || x.id === 'in'),
    `the same button, no longer inert, IS a tab stop (${t2.stops.map((x) => x.id).join(', ')})`,
  );
}, 120_000);

interface Ctx {
  label: string;
  width: number;
  height: number;
  scale: number;
  minimized: boolean;
}
const CONTEXTS: Ctx[] = [
  { label: '320 x 568, office minimized', width: 320, height: 568, scale: 1, minimized: true },
  { label: '360 x 640, office minimized', width: 360, height: 640, scale: 1, minimized: true },
  { label: '640 x 800, office minimized', width: 640, height: 800, scale: 1, minimized: true },
  { label: '900 x 700, office minimized', width: 900, height: 700, scale: 1, minimized: true },
  { label: '900 x 700, office open', width: 900, height: 700, scale: 1, minimized: false },
  {
    label: '720 x 450 at 2x (ZOOM-EQUIVALENT, not a native Command-plus test), office minimized',
    width: 720,
    height: 450,
    scale: 2,
    minimized: true,
  },
  { label: '1440 x 900, office minimized', width: 1440, height: 900, scale: 1, minimized: true },
];
// roots whose content the banner must never paint over: the world's controls, the office panel, the City popover and the strip
const CHROME_ROOTS =
  '.di-world-controls, [data-testid="di-office-panel"], #city-controls, [data-testid="di-minimized-tray"]';

for (const c of CONTEXTS) {
  test(
    `THE FIXTURE BANNER OBSTRUCTS NOTHING and a fixture label is PAINTED, at ${c.label}: notice up, districts list open, City popover open`,
    async () => {
      await boot(c.width, c.height, { fixture: true, scale: c.scale });
      if (c.minimized) await minimizeOffice();
      await showUpdateNotice();
      const failures: string[] = [];
      const rows: string[] = [];
      const check = async (state: string) => {
        const o = await occlusion(p(), CHROME_ROOTS);
        const byBanner = o.covered.filter((x) => x.by === 'di-fixture-banner');
        const labels = await paintedLabels();
        const painted = Object.entries(labels)
          .filter(([, v]) => v.painted)
          .map(([k]) => k);
        rows.push(
          `${state}: sampled ${o.sampled}, set aside as clipped away ${o.clippedAway}, covered ${o.covered.length} (by the banner ${byBanner.length}; by others ${JSON.stringify([...new Set(o.covered.filter((x) => x.by !== 'di-fixture-banner').map((x) => x.by))])}); painted labels ${JSON.stringify(painted)}; candidates ${JSON.stringify(labels)}`,
        );
        if (byBanner.length > 0)
          failures.push(
            `${state}: the banner covers ${byBanner.length} sampled points of the chrome (${JSON.stringify(byBanner.slice(0, 3))})`,
          );
        if (o.sampled < 15)
          failures.push(
            `${state}: only ${o.sampled} points were sampled, a covered=0 would prove nothing (the limit of this measure)`,
          );
        if (painted.length === 0)
          failures.push(`${state}: NO fixture label is painted (${JSON.stringify(labels)})`);
      };
      await check('notice up');
      // the districts list (a user-opened list beside the view buttons): the click is a REAL action and the list MUST open (waitForSelector throws if the control is missing, covered,
      // inert or the list does not open); nothing here swallows an error, so a failed open can never be mistaken for a checked open state
      await p().click('[data-testid="world-view-districts"]');
      await p().waitForSelector('[data-testid="world-district-list"]', { timeout: 5000 });
      await pause(300);
      await check('districts list open');
      await p().click('[data-testid="world-view-districts"]');
      await p().waitForSelector('[data-testid="world-district-list"]', {
        state: 'detached',
        timeout: 5000,
      });
      await pause(300);
      // the City popover, opened by keyboard
      await p().focus('[data-testid="city-button"]');
      await p().keyboard.press('Enter');
      await p().waitForSelector('#city-controls');
      await pause(400);
      await check('City popover open');
      console.log(`FIXTURE LABEL ROWS ${c.label}\n${rows.join('\n')}`);
      assert.deepEqual(
        failures,
        [],
        `${c.label}: the banner obstructs nothing and a fixture label is painted in every state (an empty covered list is NOT a visibility pass; each state also met a sampling floor and a painted label)`,
      );
    },
    STEPS_MS,
  );
}
