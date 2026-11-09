/**
 * B3: THE OPTIONAL SOFT WRAP IN THE SCAN CODE DIFF (NOT a canonical test while it is in the evidence tree; PREPARED, NOT RUN). Records with the prefix B3WR.
 *
 * THE CLAIM, per viewport, each in its own test, on the 1200-character line of the selected fixture (`long_line.js`) and a line with leading whitespace (`syntax_sample.ts`):
 *   DEFAULT  the wrap is OFF; the control is labelled, `aria-pressed` false, and the long line overflows horizontally (the baseline: its last character is NOT in the visible box).
 *   ON       by keyboard (focus the control, Space): `aria-pressed` true; NO horizontal overflow; the long line's LAST character is inside the visible box (readable by vertical navigation); focus stays on the
 *            control; the selected file is unchanged.
 *   PRESERVED the text of every code cell, every line number and the number of source rows are IDENTICAL before and after (nothing truncated, no whitespace dropped, no row added); the code cell's computed font size
 *            is unchanged; white-space reads pre-wrap with the leading whitespace kept (the first-row text offset of the indented line is the same).
 *   OFF AGAIN reversible: `pre` again, horizontal overflow again, and the editor still scrolls sideways.
 *   SESSION  the preference survives choosing another file, and leaving the Code Diff and coming back (it lives with the panel; it is not stored).
 * VERSION 3 (compose-3 triage, an EXPECTATION failure of version 2's Copy cell: the drawn-lines oracle read only the diff rows, but the FIRST hunk header is drawn in the editor's column-header strip, so the oracle omitted a line the editor does show; it now takes every hunk header and every row in document order).
 * VERSION 2 (a CONDITIONAL pre-execution review, NOT an observed failure; nothing was measured when it was written): the prepared ON cell centred the long cell and required its LAST CHARACTER vertically visible at once, which may be stronger than the approved bar (normal keyboard vertical reading is allowed; no smaller text, no whole-line-at-once forced). The fixture may fit vertically at some or all sizes, in which case there is nothing to adjust. So the cell now owes what the bar owes: keyboard VERTICAL REACH of the line's last character (PageDown on the scroller where it overflows, otherwise it already fits) and NO HORIZONTAL OVERFLOW, and it RECORDS the measured dimensions (scrollHeight, clientHeight, rows) so the history is settled by the run, not by this note. `.focus()` plus Space proves ACTIVATION only: the real sequential routes are owed by the routes package and the final Brave.
 * HONEST BOUNDS: Chrome, not Brave; the sample read of the review fixture; Copy the diff reads the data and is untouched by construction (the clipboard is NOT asserted here). The rendered long-line reading path
 * in BOTH modes at shipping sizes is what the ruling asks to verify; this is its Chrome measurement, and Brave evidence on the final bytes stays owed.
 * NEGATIVE CONTROL (on the CURRENT bytes): every test fails at its first CONTRACT line (no `di-scan-wrap-toggle`), after its STATE lines pass.
 */
import assert from 'node:assert/strict';
import crypto from 'node:crypto';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

import type { Browser, Page } from 'playwright-core';
import { chromium } from 'playwright-core';
import type { ViteDevServer } from 'vite';
import { createServer } from 'vite';
import { afterAll, beforeAll, beforeEach, test } from 'vitest';
import type { WebSocket as ServerSocket } from 'ws';
import { WebSocketServer } from 'ws';

import type { DiCodeReview, DiOfficeState } from '../../core/src/messages.ts';

const STEPS_MS = 180_000;
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const doc = JSON.parse(fs.readFileSync(path.join(root, 'test/fixtures/di-office-state-code-review-v2.FIXTURE.json'), 'utf-8')) as {
  states: Record<'live', DiOfficeState>;
  replies: { overview: DiCodeReview; files: Record<string, DiCodeReview> };
};
const VERSION = (JSON.parse(fs.readFileSync(path.join(root, '..', 'package.json'), 'utf-8')) as { version: string }).version;
const SHOTS = process.env.DI_B3WR_SHOTS ?? null;


let server: ViteDevServer | null = null;
let wss: WebSocketServer | null = null;
let browser: Browser | null = null;
let page: Page | null = null;
let origin = '';
let unavailable: string | null = null;
let socket: ServerSocket | null = null;
let resolveConnected: (ws: ServerSocket) => void = () => undefined;

function replyFor(request: Record<string, unknown>): DiCodeReview | null {
  const stored =
    request.fileId === undefined ? doc.replies.overview : Object.values(doc.replies.files).find((r) => r.fileId === request.fileId);
  return stored === undefined ? null : structuredClone(stored);
}

beforeAll(async () => {
  server = await createServer({ configFile: path.resolve(root, 'vite.config.ts'), server: { port: 0, strictPort: false, hmr: false, ws: false }, logLevel: 'silent' });
  await server.listen();
  wss = new WebSocketServer({ noServer: true });
  server.httpServer!.on('upgrade', (req, sock, head) => {
    if (!req.url?.startsWith('/ws')) {
      sock.destroy();
      return;
    }
    wss!.handleUpgrade(req, sock, head, (ws) =>
      ws.on('message', (data) => {
        const msg = JSON.parse(String(data)) as Record<string, unknown>;
        if (msg.type === 'webviewReady') {
          socket = ws;
          resolveConnected(ws);
        } else if (msg.type === 'diCodeReviewRead' || msg.type === 'diCodeReviewMissionRead') {
          const found = replyFor(msg);
          if (found === null) return;
          ws.send(JSON.stringify(typeof msg.requestId === 'string' ? { ...found, requestId: msg.requestId } : found));
        }
      }),
    );
  });
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
  wss?.close();
  await server?.close();
}, STEPS_MS);

beforeEach(async (ctx) => {
  if (unavailable) {
    console.warn(`SKIPPED (B3 soft wrap): ${unavailable}`);
    ctx.skip();
    return;
  }
  await page?.close();
  page = null;
}, STEPS_MS);

const p = () => page!;
const record = (name: string, r: unknown) => console.log(`B3WR ${name}: ${JSON.stringify(r)}`);

/** Method rule 4: never overwrite a measurement artifact. */
async function shot(name: string): Promise<void> {
  if (SHOTS === null) return;
  fs.mkdirSync(SHOTS, { recursive: true });
  const file = path.join(SHOTS, name);
  assert.equal(fs.existsSync(file), false, `refusing to overwrite ${file}`);
  await p().screenshot({ path: file });
  console.log(`B3WR SHOT ${name} sha256 ${crypto.createHash('sha256').update(fs.readFileSync(file)).digest('hex')}`);
}

async function boot(viewport: { width: number; height: number }): Promise<void> {
  const connected = new Promise<ServerSocket>((resolve) => (resolveConnected = resolve));
  page = await browser!.newPage({ viewport });
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await page.goto(`${origin}/test/harness/di-app-scan.html`, { waitUntil: 'domcontentloaded' });
  await connected;
  socket!.send(JSON.stringify({ type: 'providerCapabilities', readingTools: ['Read', 'Grep', 'Glob', 'WebFetch', 'WebSearch'], subagentToolNames: ['Task', 'Agent'] }));
  socket!.send(JSON.stringify({ type: 'settingsLoaded', soundEnabled: false, lastSeenVersion: VERSION, extensionVersion: VERSION }));
  socket!.send(JSON.stringify(doc.states.live));
  await p().waitForSelector('[data-testid="di-rail-orchestrator"]');
}

async function openCodeDiff(): Promise<void> {
  await p().click('[data-testid="di-rail-orchestrator"] >> nth=0');
  await p().waitForSelector('[data-testid="di-scan-stage"][data-view="mission"]');
  await p().click('[data-testid="di-scan-code-diff"]');
  await p().waitForSelector('[data-testid="di-scan-stage"][data-view="diff"]');
  await p().waitForSelector('[data-testid="di-scan-diff"][data-source="read"]', { timeout: 15_000 });
  await p().waitForSelector('[data-testid="di-scan-tree-file"]');
}

async function selectFile(name: string): Promise<void> {
  await p().locator('[data-testid="di-scan-tree-file"]', { hasText: name }).first().click();
  await p().waitForFunction((n) => (document.querySelector('[data-testid="di-scan-editor-path"]')?.textContent ?? '').includes(n), name, { timeout: 15_000 });
  await p().waitForSelector('.di-scan-diff-line .di-scan-code', { timeout: 15_000 });
  await p().waitForTimeout(200);
}


// ── B3: the optional soft wrap ──────────────────────────────────────────────────────────────────────────────────────────────────────────────────────
const SIZES = [
  { width: 1280, height: 800, role: 'a robustness size' },
  { width: 1440, height: 900, role: 'a shipping size' },
  { width: 1600, height: 1000, role: 'a shipping size' },
] as const;
const sel = (id: string) => `[data-testid="${id}"]`;
const WRAP = sel('di-scan-wrap-toggle');
const focusKey = () => p().evaluate(() => document.activeElement?.getAttribute('data-testid') ?? document.activeElement?.tagName ?? 'none');
const editorPath = () => p().evaluate(() => document.querySelector('[data-testid="di-scan-editor-path"]')?.textContent ?? '');

interface Reading {
  overflowX: boolean;
  scrollWidth: number;
  clientWidth: number;
  /** The longest code cell's last character lies inside the editor's visible box. */
  lastCharVisible: boolean;
  longRows: number;
  whiteSpace: string;
  fontSize: string;
  texts: string[];
  numbers: string[];
  rows: number;
}
/** The rendered reading path of the longest line, as drawn right now (it is scrolled into view first, so a hidden line cannot read as "not visible"). */
async function reading(): Promise<Reading> {
  return p().evaluate(() => {
    const body = document.querySelector<HTMLElement>('[data-testid="di-scan-editor"]')!;
    const codes = Array.from(body.querySelectorAll<HTMLElement>('.di-scan-code'));
    const long = codes.reduce((a, c) => ((c.textContent ?? '').length > (a.textContent ?? '').length ? c : a));
    long.scrollIntoView({ block: 'center', inline: 'start' });
    const box = body.getBoundingClientRect();
    const walker = document.createTreeWalker(long, NodeFilter.SHOW_TEXT);
    let last: Text | null = null;
    for (let n = walker.nextNode(); n !== null; n = walker.nextNode()) if ((n as Text).length > 0) last = n as Text;
    const range = document.createRange();
    range.setStart(last!, last!.length - 1);
    range.setEnd(last!, last!.length);
    const r = range.getBoundingClientRect();
    const cs = getComputedStyle(long);
    const lineHeight = parseFloat(cs.lineHeight);
    return {
      overflowX: body.scrollWidth > body.clientWidth,
      scrollWidth: body.scrollWidth,
      clientWidth: body.clientWidth,
      lastCharVisible: r.right <= box.right + 0.5 && r.left >= box.left - 0.5 && r.bottom <= box.bottom + 0.5 && r.top >= box.top - 0.5,
      longRows: Math.round(long.getBoundingClientRect().height / lineHeight),
      whiteSpace: cs.whiteSpace,
      fontSize: cs.fontSize,
      texts: codes.map((c) => c.textContent ?? ''),
      numbers: Array.from(body.querySelectorAll('.di-scan-ln')).map((e) => e.textContent ?? ''),
      rows: body.querySelectorAll('.di-scan-diff-line').length,
    };
  });
}
/** KEYBOARD VERTICAL REACH of the longest line's last character: from the top, PageDown on the scroller (where it overflows) until the last character has been inside the visible box, or the scroller cannot move further. */
async function verticalReach(): Promise<{ overflowY: boolean; overflowX: boolean; scrollHeight: number; clientHeight: number; reached: boolean; presses: number }> {
  const lastVisible = () =>
    p().evaluate(() => {
      const body = document.querySelector<HTMLElement>('[data-testid="di-scan-editor"]')!;
      const codes = Array.from(body.querySelectorAll<HTMLElement>('.di-scan-code'));
      const long = codes.reduce((a, c) => ((c.textContent ?? '').length > (a.textContent ?? '').length ? c : a));
      const box = body.getBoundingClientRect();
      const walker = document.createTreeWalker(long, NodeFilter.SHOW_TEXT);
      let last: Text | null = null;
      for (let n = walker.nextNode(); n !== null; n = walker.nextNode()) if ((n as Text).length > 0) last = n as Text;
      const range = document.createRange();
      range.setStart(last!, last!.length - 1);
      range.setEnd(last!, last!.length);
      const r = range.getBoundingClientRect();
      return r.right <= box.right + 0.5 && r.left >= box.left - 0.5 && r.bottom <= box.bottom + 0.5 && r.top >= box.top - 0.5;
    });
  const scrollTop = () => p().evaluate(() => document.querySelector<HTMLElement>('[data-testid="di-scan-editor"]')!.scrollTop);
  const geometry = await p().evaluate(() => {
    const body = document.querySelector<HTMLElement>('[data-testid="di-scan-editor"]')!;
    body.scrollTop = 0;
    body.scrollLeft = 0;
    return { overflowY: body.scrollHeight > body.clientHeight, overflowX: body.scrollWidth > body.clientWidth, scrollHeight: body.scrollHeight, clientHeight: body.clientHeight };
  });
  let reached = await lastVisible();
  let presses = 0;
  if (!reached && geometry.overflowY) {
    await p().evaluate(() => document.querySelector<HTMLElement>('[data-testid="di-scan-editor"]')!.focus());
    let previous = -1;
    while (presses < 80) {
      await p().keyboard.press('PageDown');
      presses += 1;
      reached = await lastVisible();
      if (reached) break;
      const top = await scrollTop();
      if (top === previous) break;
      previous = top;
    }
  }
  return { ...geometry, reached, presses };
}
async function atLongLine(vp: { width: number; height: number }): Promise<void> {
  await boot(vp);
  await openCodeDiff();
  await selectFile('long_line.js');
  assert.equal(await p().locator(WRAP).count(), 1, 'CONTRACT (CONTROL): the editor bar offers the wrap control');
}

for (const vp of SIZES) {
  const where = `${vp.width}x${vp.height} (${vp.role})`;
  test(`B3 WRAP, at ${where}, DEFAULT: off, labelled, and the long line overflows sideways`, async () => {
    await atLongLine(vp);
    const toggle = p().locator(WRAP);
    assert.equal((await toggle.textContent())?.trim(), 'Wrap lines', 'CONTRACT (DEFAULT): the control is labelled');
    assert.equal(await toggle.getAttribute('aria-pressed'), 'false', 'CONTRACT (DEFAULT): the wrap is off');
    const r = await reading();
    record(`DEFAULT ${vp.width}x${vp.height}`, { overflowX: r.overflowX, scrollWidth: r.scrollWidth, clientWidth: r.clientWidth, lastCharVisible: r.lastCharVisible, whiteSpace: r.whiteSpace });
    assert.equal(r.overflowX, true, 'STATE (DEFAULT): the 1200-character line overflows horizontally');
    assert.equal(r.lastCharVisible, false, 'STATE (DEFAULT): its last character is not in the visible box');
    assert.equal(r.whiteSpace, 'pre', 'CONTRACT (DEFAULT): unwrapped code keeps white-space: pre');
  });
  test(`B3 WRAP, at ${where}, ON: by keyboard; no sideways scroll; the line's last character is readable; focus and file kept`, async () => {
    await atLongLine(vp);
    const file = await editorPath();
    await p().locator(WRAP).focus();
    await p().keyboard.press('Space');
    await shot(`b3wr-on-${vp.width}x${vp.height}.png`);
    const r = await reading();
    record(`ON ${vp.width}x${vp.height}`, { overflowX: r.overflowX, scrollWidth: r.scrollWidth, clientWidth: r.clientWidth, lastCharVisible: r.lastCharVisible, longRows: r.longRows, whiteSpace: r.whiteSpace });
    assert.equal(await p().locator(WRAP).getAttribute('aria-pressed'), 'true', 'CONTRACT (ON): the state is exposed');
    assert.equal(r.overflowX, false, `CONTRACT (ON): ${where}: the editor still scrolls sideways (${r.scrollWidth} > ${r.clientWidth})`);
    const reach = await verticalReach();
    record(`ON-REACH ${vp.width}x${vp.height}`, reach);
    assert.equal(reach.overflowX, false, 'CONTRACT (ON): no horizontal overflow once the sideways scroll is gone');
    assert.equal(reach.reached, true, `CONTRACT (ON): the line's last character is reached by keyboard vertical reading (${reach.overflowY ? `PageDown, ${reach.presses} presses` : 'it fits without scrolling'}), without sideways scrolling`);
    assert.ok(r.longRows > 1, `CONTRACT (ON): the long line wraps over several rows (${r.longRows})`);
    assert.equal(await focusKey(), 'di-scan-wrap-toggle', 'CONTRACT (ON): focus stays on the control');
    assert.equal(await editorPath(), file, 'CONTRACT (ON): the selected file is unchanged');
  });
  test(`B3 WRAP, at ${where}, PRESERVED: the same text, numbers and rows; the same size; whitespace kept`, async () => {
    await atLongLine(vp);
    const before = await reading();
    await p().locator(WRAP).click();
    const after = await reading();
    assert.deepEqual(after.texts, before.texts, 'CONTRACT (PRESERVED): every code cell reads the same');
    assert.deepEqual(after.numbers, before.numbers, 'CONTRACT (PRESERVED): every line number is the same');
    assert.equal(after.rows, before.rows, 'CONTRACT (PRESERVED): the same number of source rows');
    assert.equal(after.fontSize, before.fontSize, 'CONTRACT (PRESERVED): the code is not shrunk');
    assert.equal(after.whiteSpace, 'pre-wrap', 'CONTRACT (PRESERVED): whitespace is kept (pre-wrap)');
    await selectFile('syntax_sample.ts');
    const indent = await p().evaluate(() => {
      const code = Array.from(document.querySelectorAll<HTMLElement>('.di-scan-code')).find((c) => /^\s{2,}\S/.test((c.textContent ?? '').replace(/^[+−]/, '')));
      return code === undefined ? null : { text: (code.textContent ?? '').slice(0, 40), leading: ((code.textContent ?? '').replace(/^[+−]/, '').match(/^\s*/) ?? [''])[0].length };
    });
    record(`INDENT ${vp.width}x${vp.height}`, { indent });
    assert.ok(indent !== null && indent.leading >= 2, 'STATE (PRESERVED): the sample has an indented line whose leading whitespace is still in its text');
  });
  test(`B3 WRAP, at ${where}, OFF AGAIN: reversible, and sideways scrolling is back`, async () => {
    await atLongLine(vp);
    await p().locator(WRAP).click();
    await p().locator(WRAP).click();
    const r = await reading();
    const moved = await p().evaluate(() => {
      const body = document.querySelector<HTMLElement>('[data-testid="di-scan-editor"]')!;
      body.scrollLeft = 120;
      return body.scrollLeft;
    });
    record(`OFF ${vp.width}x${vp.height}`, { overflowX: r.overflowX, whiteSpace: r.whiteSpace, scrollLeftAfter: moved });
    assert.equal(await p().locator(WRAP).getAttribute('aria-pressed'), 'false', 'CONTRACT (OFF): the wrap is off');
    assert.equal(r.whiteSpace, 'pre', 'CONTRACT (OFF): white-space is pre again');
    assert.equal(r.overflowX, true, 'CONTRACT (OFF): the long line overflows sideways again');
    assert.ok(moved > 0, 'CONTRACT (OFF): the editor scrolls sideways again');
  });
  test(`B3 WRAP, at ${where}, SESSION: the preference survives another file and leaving the Code Diff`, async () => {
    await atLongLine(vp);
    await p().locator(WRAP).click();
    await selectFile('syntax_sample.ts');
    assert.equal(await p().locator(WRAP).getAttribute('aria-pressed'), 'true', 'CONTRACT (SESSION): still on after choosing another file');
    await p().locator('[data-testid="di-scan-code-diff"]').count();
    await p().keyboard.press('Escape');
    await p().waitForSelector(sel('di-scan-stage') + ':not([data-view="diff"])', { timeout: 10_000 });
    await p().click(sel('di-scan-code-diff'));
    await p().waitForSelector(sel('di-scan-stage') + '[data-view="diff"]');
    await p().waitForSelector(WRAP);
    record(`SESSION ${vp.width}x${vp.height}`, { pressedAfterReturn: await p().locator(WRAP).getAttribute('aria-pressed') });
    assert.equal(await p().locator(WRAP).getAttribute('aria-pressed'), 'true', 'CONTRACT (SESSION): still on after leaving the Code Diff and coming back');
  });
}

// ── COPY under wrap (composed with the Code Diff package): wrap is a DISPLAY preference, so what Copy hands over is the same text either way ─────────────────────────────────────────────────────────────
async function stubClipboardOk(): Promise<void> {
  await p().evaluate(() => {
    const w = window as unknown as { __copied: string[] };
    w.__copied = [];
    Object.defineProperty(navigator, 'clipboard', { value: { writeText: (text: string) => (w.__copied.push(text), Promise.resolve()) }, configurable: true });
  });
}
const copiedNow = () => p().evaluate(() => (window as unknown as { __copied?: string[] }).__copied ?? []);
/** A control character or a bidirectional override: what the page's display transform would change. (Written without a control-character regular expression, which the lint forbids.) */
function hasControl(text: string): boolean {
  return [...text].some((ch) => {
    const c = ch.codePointAt(0)!;
    return c < 0x20 || (c >= 0x7f && c <= 0x9f) || (c >= 0x202a && c <= 0x202e) || (c >= 0x2066 && c <= 0x2069);
  });
}
/** The expected copy text from the RAW fixture (hunk headers and lines), apart from the code under test. Preconditions: no clipped line, no control characters. */
function fixtureText(filePath: string): string {
  const reply = Object.values(doc.replies.files).find((r) => r.file?.path === filePath);
  assert.ok(reply?.file, `fixture: ${filePath} has a file reply`);
  const out: string[] = [];
  for (const hunk of reply.file.hunks) {
    out.push(`@@ -${hunk.oldStart},${hunk.oldLines} +${hunk.newStart},${hunk.newLines} @@${hunk.section === null ? '' : ` ${hunk.section}`}`);
    for (const line of hunk.lines) {
      assert.ok(line.clipped === 0 && !hasControl(line.text), 'fixture precondition: no clipped line and no control characters');
      out.push(line.kind === 'no_newline' ? '\\ No newline at end of file' : line.text);
    }
  }
  return out.join('\n');
}
const drawnLines = () =>
  p().evaluate(() =>
    [...document.querySelectorAll('[data-testid="di-scan-editor"] .di-scan-hunk, [data-testid="di-scan-editor"] .di-scan-diff-line:not([data-kind="hunk"])')].map((el) => {
      if (el.classList.contains('di-scan-hunk')) return el.textContent ?? '';
      let text = '';
      el.querySelector('.di-scan-code')?.childNodes.forEach((node) => {
        if (!(node instanceof HTMLElement && node.classList.contains('di-scan-sign'))) text += node.textContent ?? '';
      });
      return text;
    }),
  );
for (const vp of SIZES.filter((s) => s.width >= 1440)) {
  const where = `${vp.width}x${vp.height} (${vp.role})`;
  test(`B3 WRAP, at ${where}, COPY: the text handed to the clipboard is the same with the wrap on and off, equal to the drawn lines and (syntax_sample.ts) to the raw fixture`, async () => {
    await boot(vp);
    await openCodeDiff();
    await selectFile('syntax_sample.ts');
    await stubClipboardOk();
    const copy = p().locator(sel('di-scan-diff-copy'));
    assert.equal(await copy.count(), 1, 'CONTRACT (COPY): the Code Diff offers Copy (composed with the Code Diff package)');
    const texts: string[] = [];
    for (const wrapOn of [false, true, false]) {
      if ((await p().locator(WRAP).getAttribute('aria-pressed')) !== String(wrapOn)) await p().locator(WRAP).click();
      await copy.focus();
      await p().keyboard.press('Enter');
      await p().waitForTimeout(200);
      texts.push((await copiedNow()).at(-1) ?? '');
      assert.equal(texts.at(-1), (await drawnLines()).join('\n'), `CONTRACT (COPY): wrap ${wrapOn ? 'on' : 'off'}: the text is the drawn lines, line for line`);
    }
    record(`WRAP-COPY ${vp.width}x${vp.height}`, { lengths: texts.map((t) => t.length), sha256: texts.map((t) => crypto.createHash('sha256').update(t).digest('hex').slice(0, 16)) });
    assert.equal(texts[0], fixtureText('src/syntax_sample.ts'), 'CONTRACT (COPY): the text is the raw fixture\'s hunk headers and lines, with no marks and no numbers');
    assert.deepEqual([texts[1], texts[2]], [texts[0], texts[0]], 'CONTRACT (COPY): and it is the same with the wrap on and after turning it off again');
  });
}
