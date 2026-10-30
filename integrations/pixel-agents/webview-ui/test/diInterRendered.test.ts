/**
 * A1 (amended): the BUNDLED Inter is the face that actually PAINTS the DI chrome, measured in a real browser, not read
 * from the CSS.
 *
 * How: the scan face (the rail and Mission details, the locked specimen) is served by Vite's dev server from this
 * source tree and opened in Chrome. Chrome reports, per element, the platform fonts it actually used to draw that
 * element's glyphs (DevTools protocol CSS.getPlatformFontsForNode: family, whether it is a web font, glyph count).
 *
 * Proves:
 *  1. the page fetched /fonts/InterVariable.woff2 from its own origin (200), and the served bytes ARE the vendored file;
 *  2. nothing was fetched from any other origin;
 *  3. the rail's text and the Mission title are drawn with the bundled file AS A WEB FONT (isCustomFont): Chrome names
 *     the face by the family inside the file, "Inter Variable", and every glyph it reports is from it;
 *  4. CONTROL (the measurement can tell faces apart): an element set in the system face on the same page is drawn
 *     with a font that is not Inter;
 *  5. CONTROL (no installed Inter is masking the result): with the font file's request blocked, the same rail text is
 *     NOT drawn with Inter.
 * Does NOT prove: rendering inside the VS Code webview or the sealed DI package (this is the dev server over source),
 * other operating systems' rasterisation, or that the result matches the design pixel for pixel. Run headless Chrome.
 *
 * Browser: the installed Chrome (playwright-core, channel `chrome`), else Playwright's Chromium; with neither, every test
 * is skipped with an explicit message.
 */
import crypto from 'node:crypto';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

import type { Browser, CDPSession, Page } from 'playwright-core';
import { chromium } from 'playwright-core';
import type { ViteDevServer } from 'vite';
import { createServer } from 'vite';
import { afterAll, beforeAll, beforeEach, expect, test } from 'vitest';

const STEPS_MS = 120_000;
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const VENDORED_SHA256 = crypto
  .createHash('sha256')
  .update(fs.readFileSync(path.join(root, 'public/fonts/InterVariable.woff2')))
  .digest('hex');

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
  const port = typeof addr === 'object' && addr !== null ? addr.port : 0;
  origin = `http://localhost:${port}`;
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
    console.warn(`SKIPPED (Inter rendered face): ${unavailable}`);
    ctx.skip();
  }
});

interface PlatformFont {
  familyName: string;
  postScriptName?: string;
  isCustomFont: boolean;
  glyphCount: number;
}

/** The fonts Chrome actually drew `selector`'s text with (its own report of the paint, not the CSS). */
async function paintedFonts(cdp: CDPSession, selector: string): Promise<PlatformFont[]> {
  const { root: doc } = (await cdp.send('DOM.getDocument', { depth: -1 })) as {
    root: { nodeId: number };
  };
  const { nodeId } = (await cdp.send('DOM.querySelector', { nodeId: doc.nodeId, selector })) as {
    nodeId: number;
  };
  if (!nodeId) throw new Error(`no element ${selector}`);
  const { fonts } = (await cdp.send('CSS.getPlatformFontsForNode', { nodeId })) as {
    fonts: PlatformFont[];
  };
  return fonts;
}

async function openMission(page: Page): Promise<CDPSession> {
  await page.goto(`${origin}/test/harness/di-scan-lock.html?di-scan-view=mission`, {
    waitUntil: 'load',
  });
  await page.waitForSelector('[data-testid="di-rail-new-orchestrator"]');
  await page.waitForSelector('[data-testid="di-scan-mission-title"]');
  await page.evaluate(async () => {
    await document.fonts.ready;
  });
  const cdp = await page.context().newCDPSession(page);
  await cdp.send('DOM.enable');
  await cdp.send('CSS.enable');
  return cdp;
}

test(
  'the bundled Inter is fetched from the app itself, is the vendored file, and paints the rail and the Mission title',
  async () => {
    const page = await browser!.newPage({ viewport: { width: 1440, height: 900 } });
    const requests: string[] = [];
    const fontResponses: Array<{ url: string; status: number }> = [];
    page.on('request', (r) => requests.push(r.url()));
    page.on('response', (r) => {
      if (r.url().includes('/fonts/')) fontResponses.push({ url: r.url(), status: r.status() });
    });
    const cdp = await openMission(page);

    // 1. fetched from its own origin, and the served bytes are the vendored file
    const inter = fontResponses.filter((r) => r.url === `${origin}/fonts/InterVariable.woff2`);
    expect(inter.map((r) => r.status)).toContain(200);
    const servedSha = await page.evaluate(async () => {
      const bytes = await (await fetch('/fonts/InterVariable.woff2')).arrayBuffer();
      // the PAGE's Web Crypto (the name `crypto` in this file is the test's node import)
      const digest = await window.crypto.subtle.digest('SHA-256', bytes);
      return [...new Uint8Array(digest)].map((b) => b.toString(16).padStart(2, '0')).join('');
    });
    expect(servedSha).toBe(VENDORED_SHA256);
    const faces = await page.evaluate(() =>
      [...document.fonts]
        .filter((f) => f.family.replace(/["']/g, '') === 'Inter')
        .map((f) => f.status),
    );
    expect(faces).toEqual(['loaded']);

    // 2. nothing from another origin (data: and blob: URLs are in-page)
    const foreign = requests.filter((u) => !u.startsWith(origin) && !/^(data|blob):/.test(u));
    expect(foreign).toEqual([]);

    // 3. the paint: every glyph of the rail's primary action and of the Mission title is drawn with Inter, as a web font
    for (const selector of [
      '[data-testid="di-rail-new-orchestrator"]',
      '[data-testid="di-scan-mission-title"]',
      '[data-testid="di-open-reception"]',
    ]) {
      const fonts = await paintedFonts(cdp, selector);
      expect(fonts.length, selector).toBeGreaterThan(0);
      for (const f of fonts) {
        // Chrome names the face it drew by the family IN THE FILE (InterVariable.woff2 calls itself "Inter Variable"),
        // not by the CSS alias, so this is the bundled file's own name
        expect(f.familyName, `${selector} painted with ${JSON.stringify(fonts)}`).toBe(
          'Inter Variable',
        );
        expect(f.postScriptName ?? '', selector).toMatch(/^InterVariable/);
        expect(f.isCustomFont, `${selector} uses the web font`).toBe(true);
      }
    }

    // 4. CONTROL: the same text in the system face is drawn with something else, so the measurement tells faces apart.
    // The control must be GENUINELY PAINTED before it is measured: Chrome reports the fonts a node was laid out and painted
    // with, and does not lay a node out to answer (measured: a node appended a moment earlier reported no font on 38 of 40
    // immediate queries, and every one of those reported its font two frames later). So the control is placed inside the
    // window, on top of everything, with a real size; it is laid out, proven topmost at its centre, and two frames pass.
    await page.evaluate(async () => {
      const span = document.createElement('span');
      span.id = 'di-face-control';
      span.textContent = '+ New Orchestrator';
      Object.assign(span.style, {
        position: 'fixed',
        left: '260px',
        top: '12px',
        zIndex: '2147483647',
        fontSize: '16px',
        whiteSpace: 'nowrap',
      });
      // the system face, set by assignment (the control is the one element here that must NOT use the app's faces)
      span.style.fontFamily = 'system-ui, sans-serif';
      document.body.append(span);
      const box = span.getBoundingClientRect();
      if (box.width === 0 || box.height === 0) throw new Error('the control span has no size');
      if (box.right > innerWidth || box.bottom > innerHeight)
        throw new Error('the control span is outside the window');
      await new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve)));
      const top = document.elementFromPoint(box.left + box.width / 2, box.top + box.height / 2);
      if (top !== span)
        throw new Error(
          `the control span is covered at its centre by ${top?.tagName ?? 'nothing'}`,
        );
    });
    const control = await paintedFonts(cdp, '#di-face-control');
    expect(control.length).toBeGreaterThan(0);
    for (const f of control) {
      expect(f.familyName, JSON.stringify(control)).not.toMatch(/^Inter\b/);
      expect(f.isCustomFont).toBe(false);
    }
    await page.close();
  },
  STEPS_MS,
);

test(
  'CONTROL: with the bundled file blocked, the rail is NOT drawn with Inter (no installed copy stands in for it)',
  async () => {
    const page = await browser!.newPage({ viewport: { width: 1440, height: 900 } });
    await page.route('**/fonts/InterVariable.woff2', (route) => route.abort());
    const cdp = await openMission(page);
    const fonts = await paintedFonts(cdp, '[data-testid="di-rail-new-orchestrator"]');
    expect(fonts.length).toBeGreaterThan(0);
    for (const f of fonts) {
      expect(f.familyName, JSON.stringify(fonts)).not.toMatch(/^Inter\b/);
      expect(f.isCustomFont, JSON.stringify(fonts)).toBe(false);
    }
    await page.close();
  },
  STEPS_MS,
);
