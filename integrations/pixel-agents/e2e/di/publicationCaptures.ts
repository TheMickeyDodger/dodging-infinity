/**
 * feature13 — the TWO README publication images, captured FRESH from the production build.
 *
 * Not a test (no assertions about product behaviour, no skip): a capture TOOL that fails loudly when its
 * own preconditions or checks do not hold. Run from integrations/pixel-agents against a fresh build:
 *
 *   DI_PUBLICATION_DIR=<abs dir> PLAYWRIGHT_BROWSERS_PATH=<cache> npx tsx e2e/di/publicationCaptures.ts
 *
 * What it does, and why it is honest:
 *  - launches the PRODUCTION BUILD (dist/di-cli.js) with `--fixture-preview` over the OWNED publication
 *    FIXTURE (publicationFixture.ts: the realistic fixture with short, generic "sample" goals; the long-text
 *    stress fixture and its assertions are untouched), so the PRODUCT labels itself synthetic: the panel
 *    header reads "LIVE - FIXTURE DATA" and the inspector kind line reads "... · FIXTURE DATA". NO test script
 *    injects a caption into the page.
 *  - the OVERVIEW is a PANEL-ONLY crop: the clip is the panel element's own getBoundingClientRect (read from
 *    the page) down to just below the last WHOLE Mission card; the product's own "LIVE - FIXTURE DATA" header
 *    supplies the labelling. No world is in that image. The INSPECTOR is cropped to the dialog's own bounds.
 *    Both are captured at device scale 2 and are not resized.
 *  - takes a CLIP of the rendered page (a crop, never generated or composited) through the browser's own
 *    page screenshot: no browser chrome, no URL bar. Each clip ends just below the last WHOLE card (the tool
 *    refuses to run if fewer than two whole cards fit), so no card is cut mid-text. The access token is held
 *    in memory only and never printed; before each capture the whole page text and markup are scanned for
 *    the token, a local path, a host and a URL, and the product's fixture label must lie inside the clip.
 *  - normalizes each capture to 8-bit sRGB RGB PNG with exactly IHDR, sRGB, IDAT..., IEND (the layout the
 *    six existing repository images have), drops every other chunk (tEXt/iTXt/zTXt/eXIf and the rest),
 *    PROVES it by re-parsing the written file, and proves the pixels are identical to the raw capture.
 *  - binds the capture to the BUILD it came from (buildIdentity(): sha256 of dist/di-cli.js and every
 *    dist/webview file; it refuses a build older than its sources).
 * Output: <DI_PUBLICATION_DIR>/{operational-panel-overview.png, operational-panel-inspector.png,
 * publication-report.json}. Installing the PNGs into assets/pixel-world/ is a separate, deliberate step.
 */
import crypto from 'node:crypto';
import fs from 'node:fs';
import path from 'node:path';
import zlib from 'node:zlib';

import { chromium, type Page } from '@playwright/test';
import { PNG } from 'pngjs';

import { buildIdentity, delay, startSandboxOffice } from './officeSandbox';
import { buildPublicationFixture } from './publicationFixture';

const OUT_DIR = process.env['DI_PUBLICATION_DIR'];
if (!OUT_DIR || !path.isAbsolute(OUT_DIR))
  throw new Error('set DI_PUBLICATION_DIR to an absolute directory');

const DPR = 2;
const OVERVIEW_VIEWPORT = { width: 1280, height: 900 };
const INSPECTOR_VIEWPORT = { width: 1280, height: 1000 };
/** The panel fades the bottom 28 px of its list (diOperationalPanel.css .di-op-main mask). */
const LIST_FADE_PX = 28;
/** Space kept below the last whole card in the crop (smaller than the gap to the next card). */
const BELOW_LAST_CARD = 6;
const INSPECTOR_MARGIN = 0;
const FORBIDDEN_METADATA = new Set(['tEXt', 'iTXt', 'zTXt', 'eXIf']);

interface Clip {
  x: number;
  y: number;
  width: number;
  height: number;
}

// ── PNG: parse, normalize, prove ─────────────────────────────────────────────────────────────────
function parseChunks(bytes: Buffer): Array<{ type: string; data: Buffer }> {
  if (!bytes.subarray(0, 8).equals(Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a])))
    throw new Error('not a PNG');
  const chunks: Array<{ type: string; data: Buffer }> = [];
  for (let i = 8; i < bytes.length;) {
    const length = bytes.readUInt32BE(i);
    const type = bytes.subarray(i + 4, i + 8).toString('latin1');
    const data = bytes.subarray(i + 8, i + 8 + length);
    if (zlib.crc32(bytes.subarray(i + 4, i + 8 + length)) !== bytes.readUInt32BE(i + 8 + length))
      throw new Error(`bad CRC in ${type}`);
    chunks.push({ type, data });
    i += 12 + length;
    if (type === 'IEND') break;
  }
  return chunks;
}

function chunk(type: string, data: Buffer): Buffer {
  const body = Buffer.concat([Buffer.from(type, 'latin1'), data]);
  const head = Buffer.alloc(4);
  head.writeUInt32BE(data.length);
  const crc = Buffer.alloc(4);
  crc.writeUInt32BE(zlib.crc32(body));
  return Buffer.concat([head, body, crc]);
}

/** RGBA capture -> 8-bit RGB (colour type 2) PNG with only IHDR, sRGB (perceptual), IDAT..., IEND. */
function normalize(raw: Buffer): Buffer {
  const decoded = PNG.sync.read(raw);
  const rgb = Buffer.alloc(decoded.width * decoded.height * 3);
  for (let p = 0, q = 0; p < decoded.data.length; p += 4) {
    if (decoded.data[p + 3] !== 255)
      throw new Error('capture has a non-opaque pixel; refusing to flatten it');
    rgb[q++] = decoded.data[p];
    rgb[q++] = decoded.data[p + 1];
    rgb[q++] = decoded.data[p + 2];
  }
  const encoded = PNG.sync.write(
    Object.assign(new PNG({ width: decoded.width, height: decoded.height }), {
      data: Buffer.concat(
        Array.from({ length: decoded.height }, (_, y) => {
          const row = Buffer.alloc(decoded.width * 4);
          for (let x = 0; x < decoded.width; x++) {
            const s = (y * decoded.width + x) * 3;
            row[x * 4] = rgb[s];
            row[x * 4 + 1] = rgb[s + 1];
            row[x * 4 + 2] = rgb[s + 2];
            row[x * 4 + 3] = 255;
          }
          return row;
        }),
      ),
    }),
    { colorType: 2, bitDepth: 8, inputColorType: 6, inputHasAlpha: true, deflateLevel: 9 },
  );
  const parts = parseChunks(encoded);
  const ihdr = parts.find((c) => c.type === 'IHDR')!;
  const idat = parts.filter((c) => c.type === 'IDAT');
  return Buffer.concat([
    Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]),
    chunk('IHDR', ihdr.data),
    chunk('sRGB', Buffer.from([0])), // rendering intent 0: perceptual
    ...idat.map((c) => chunk('IDAT', c.data)),
    chunk('IEND', Buffer.alloc(0)),
  ]);
}

function proveNormalized(file: string, raw: Buffer): Record<string, unknown> {
  const bytes = fs.readFileSync(file);
  const chunks = parseChunks(bytes); // also verifies every CRC
  const types = chunks.map((c) => c.type);
  const ihdr = chunks[0].data;
  const [width, height, bitDepth, colorType] = [
    ihdr.readUInt32BE(0),
    ihdr.readUInt32BE(4),
    ihdr[8],
    ihdr[9],
  ];
  const metadata = types.filter((t) => FORBIDDEN_METADATA.has(t));
  const idatRun = types.slice(2, -1).every((t) => t === 'IDAT');
  if (metadata.length) throw new Error(`${file}: metadata chunks remain: ${metadata}`);
  if (
    types[0] !== 'IHDR' ||
    types[1] !== 'sRGB' ||
    !idatRun ||
    types.at(-1) !== 'IEND' ||
    types.length < 4
  )
    throw new Error(`${file}: unexpected chunk layout ${types.join(',')}`);
  if (bitDepth !== 8 || colorType !== 2)
    throw new Error(`${file}: not 8-bit RGB (bitDepth ${bitDepth}, colorType ${colorType})`);
  // Pixel identity with the raw capture (alpha was 255 everywhere, so RGB equality is full equality).
  const a = PNG.sync.read(raw);
  const b = PNG.sync.read(bytes);
  let identical = a.width === b.width && a.height === b.height;
  for (let i = 0; identical && i < a.data.length; i += 4)
    if (
      a.data[i] !== b.data[i] ||
      a.data[i + 1] !== b.data[i + 1] ||
      a.data[i + 2] !== b.data[i + 2]
    )
      identical = false;
  if (!identical) throw new Error(`${file}: normalized pixels differ from the raw capture`);
  const counts: Record<string, number> = {};
  for (const t of types) counts[t] = (counts[t] ?? 0) + 1;
  return {
    file: path.basename(file),
    bytes: bytes.length,
    sha256: crypto.createHash('sha256').update(bytes).digest('hex'),
    width,
    height,
    bitDepth,
    colorType: `${colorType} (RGB, no alpha)`,
    chunks: counts,
    metadataChunksPresent: metadata,
    pixelsIdenticalToRawCapture: identical,
  };
}

// ── frame checks ─────────────────────────────────────────────────────────────────────────────────
async function assertCleanPage(
  page: Page,
  token: string,
  label: string,
): Promise<Record<string, unknown>> {
  const text = await page.evaluate(() => document.body.innerText);
  const markup = await page.evaluate(() => document.documentElement.outerHTML);
  const forbidden: Array<[string, string]> = [
    ['the access token', token],
    ['an absolute local path', '/Users/'],
    ['a loopback host', '127.0.0.1'],
    ['localhost', 'localhost'],
    ['a URL', 'http://'],
    ['a URL', 'https://'],
    ['a bearer credential', 'Bearer'],
    ['private evidence', '.herd'],
  ];
  for (const [what, needle] of forbidden)
    if (text.includes(needle) || markup.includes(needle))
      throw new Error(`${label}: the page contains ${what}`);
  return {
    scannedTextChars: text.length,
    scannedMarkupChars: markup.length,
    forbiddenStrings: forbidden.map(([, n]) => (n === token ? '<access token>' : n)),
  };
}

const inside = (inner: Clip, outer: Clip) =>
  inner.x >= outer.x &&
  inner.y >= outer.y &&
  inner.x + inner.width <= outer.x + outer.width &&
  inner.y + inner.height <= outer.y + outer.height;

/**
 * The last card in `boxes` that is WHOLE inside `visibleBottom`, how many whole cards there are, and the
 * crop's bottom edge: just below that card but always above the next card's top (half the gap, at most
 * BELOW_LAST_CARD), so no part of the next card is in the crop.
 */
function lastWholeCard(
  boxes: Clip[],
  visibleBottom: number,
): { bottom: number; count: number; cropBottom: number } {
  const whole = boxes.filter((b) => b.y + b.height <= visibleBottom);
  if (whole.length === 0) throw new Error('no whole card fits above the visible bottom');
  const bottom = Math.max(...whole.map((b) => b.y + b.height));
  const next = boxes.find((b) => b.y + b.height > bottom);
  const gap = next ? next.y - bottom : BELOW_LAST_CARD * 2;
  if (gap < 2) throw new Error(`cards are ${gap}px apart; no clean crop edge exists between them`);
  const pad = Math.min(BELOW_LAST_CARD, Math.floor(gap / 2));
  return { bottom, count: whole.length, cropBottom: Math.ceil(bottom + pad) };
}

async function main(): Promise<void> {
  const identity = buildIdentity(); // refuses a stale build
  fs.mkdirSync(OUT_DIR!, { recursive: true });
  const fixture = buildPublicationFixture();
  const office = await startSandboxOffice({
    prefix: 'di-publication',
    envelopeAt: (now) => fixture.envelopeAt(now),
    fixturePreview: true,
  });
  const browser = await chromium.launch();
  const report: Record<string, unknown> = {
    note: 'SYNTHETIC captures of the feature13 candidate build over the owned short-goal publication fixture, labelled in-frame by the product itself (--fixture-preview). Appearance at capture time, not Mission success or live concurrency, and not a statement about resident density.',
    capturedAt: new Date().toISOString(),
    chromium: browser.version(),
    build: identity,
    fixture: 'e2e/di/publicationFixture.ts (realisticFixture.ts with short sample goals)',
    images: {},
  };
  try {
    const open = async (viewport: { width: number; height: number }) => {
      const context = await browser.newContext({ viewport, deviceScaleFactor: DPR });
      const page = await context.newPage();
      try {
        await page.goto(office.url);
      } catch (err) {
        throw new Error(`navigation failed: ${String(err).split(office.token).join('<REDACTED>')}`);
      }
      await page.getByTestId('di-office-panel').waitFor({ state: 'visible', timeout: 30_000 });
      await page
        .locator('[data-mission-ref]')
        .first()
        .waitFor({ state: 'visible', timeout: 30_000 });
      await delay(3500); // characters seated, spawn effects finished
      // The product's own "Updated to vX" notice is unrelated to this feature: dismiss it as a person would.
      // the notice's dismiss now carries a real accessible name (it was the bare text 'x')
      const dismiss = page.getByRole('button', { name: 'Dismiss the update notice', exact: true });
      if (await dismiss.isVisible().catch(() => false)) {
        await dismiss.click();
        await delay(600);
      }
      return { context, page };
    };

    // ── 1. the calm overview: the PANEL, cropped to its own bounds ─────────────────────────────
    {
      const { context, page } = await open(OVERVIEW_VIEWPORT);
      await page.mouse.move(OVERVIEW_VIEWPORT.width / 2, 4);
      await delay(600);
      const panelElement = page.getByTestId('di-office-panel');
      // The panel's ACTUAL bounds, read from the page (not a guessed rectangle).
      const panel = await panelElement.evaluate((el) => {
        const r = el.getBoundingClientRect();
        return { x: r.x, y: r.y, width: r.width, height: r.height };
      });
      const main = (await page.getByTestId('di-op-main').boundingBox())!;
      const cards = await page.locator('[data-mission-ref]').evaluateAll((els) =>
        els.map((el) => {
          const r = el.getBoundingClientRect();
          return { x: r.x, y: r.y, width: r.width, height: r.height };
        }),
      );
      const whole = lastWholeCard(cards, main.y + main.height - LIST_FADE_PX);
      if (whole.count < 2)
        throw new Error(`overview: only ${whole.count} whole Mission card(s) fit`);
      const x0 = Math.floor(panel.x);
      const clip: Clip = {
        x: x0,
        y: Math.floor(panel.y),
        width: Math.ceil(panel.x + panel.width) - x0,
        height: whole.cropBottom - Math.floor(panel.y),
      };
      const label = panelElement.getByText('LIVE - FIXTURE DATA').first();
      const labelBox = (await label.boundingBox())!;
      if (!inside(labelBox, clip))
        throw new Error('overview: the product fixture label is outside the clip');
      for (const card of cards.slice(0, whole.count))
        if (!inside(card, clip))
          throw new Error('overview: a whole Mission card is outside the clip');
      const checks = await assertCleanPage(page, office.token, 'overview');
      const raw = await page.screenshot({ clip, type: 'png', animations: 'allow' });
      fs.writeFileSync(path.join(OUT_DIR!, 'raw-overview.png'), raw);
      const out = path.join(OUT_DIR!, 'operational-panel-overview.png');
      fs.writeFileSync(out, normalize(raw));
      (report.images as Record<string, unknown>)['operational-panel-overview.png'] = {
        viewportCssPx: OVERVIEW_VIEWPORT,
        deviceScaleFactor: DPR,
        framing:
          "panel only: the panel element's own getBoundingClientRect, down to just below the last whole Mission card",
        panelCssPx: panel,
        clipCssPx: clip,
        productLabelInClip: { text: 'LIVE - FIXTURE DATA', box: labelBox },
        wholeMissionCardsInClip: whole.count,
        lastWholeCardBottom: whole.bottom,
        pageScan: checks,
        ...proveNormalized(out, raw),
      };
      await context.close();
    }

    // ── 2. the expanded full-size inspector ────────────────────────────────────────────────────
    {
      const { context, page } = await open(INSPECTOR_VIEWPORT);
      await page
        .locator('[data-mission-ref]')
        .first()
        .getByTestId('di-open-inspector-mission')
        .click();
      const dialog = page.getByRole('dialog');
      await dialog.waitFor({ state: 'visible' });
      await delay(900);
      await page.mouse.move(4, INSPECTOR_VIEWPORT.height - 4);
      const box = (await dialog.boundingBox())!;
      const members = await dialog.getByTestId('di-inspector-team-member').evaluateAll((els) =>
        els.map((el) => {
          const r = el.getBoundingClientRect();
          return { x: r.x, y: r.y, width: r.width, height: r.height };
        }),
      );
      const whole = lastWholeCard(members, box.y + box.height - 16);
      if (whole.count < 2)
        throw new Error(`inspector: only ${whole.count} whole team member card(s) fit`);
      const bottom = whole.cropBottom;
      const x = Math.max(0, Math.floor(box.x - INSPECTOR_MARGIN));
      const y = Math.max(0, Math.floor(box.y - INSPECTOR_MARGIN));
      const clip: Clip = {
        x,
        y,
        width:
          Math.min(INSPECTOR_VIEWPORT.width, Math.ceil(box.x + box.width + INSPECTOR_MARGIN)) - x,
        height: Math.min(bottom, INSPECTOR_VIEWPORT.height) - y,
      };
      const label = dialog.getByText('FIXTURE DATA').first();
      await label.waitFor({ state: 'visible' });
      const labelBox = (await label.boundingBox())!;
      if (!inside(labelBox, clip))
        throw new Error('inspector: the product fixture label is outside the clip');
      const checks = await assertCleanPage(page, office.token, 'inspector');
      const raw = await page.screenshot({ clip, type: 'png', animations: 'allow' });
      fs.writeFileSync(path.join(OUT_DIR!, 'raw-inspector.png'), raw);
      const out = path.join(OUT_DIR!, 'operational-panel-inspector.png');
      fs.writeFileSync(out, normalize(raw));
      (report.images as Record<string, unknown>)['operational-panel-inspector.png'] = {
        viewportCssPx: INSPECTOR_VIEWPORT,
        deviceScaleFactor: DPR,
        framing: 'Show details on the first Mission',
        clipCssPx: clip,
        dialogCssPx: box,
        productLabelInClip: { text: 'FIXTURE DATA (inspector kind line)', box: labelBox },
        wholeTeamMemberCardsInClip: whole.count,
        pageScan: checks,
        ...proveNormalized(out, raw),
      };
      await context.close();
    }
  } finally {
    await browser.close();
    await office.stop();
  }
  fs.writeFileSync(
    path.join(OUT_DIR!, 'publication-report.json'),
    `${JSON.stringify(report, null, 2)}\n`,
  );
  console.log(`[publication] wrote ${OUT_DIR}`);
}

main().catch((err) => {
  console.error(String(err));
  process.exit(1);
});
