/**
 * B3, 1B: THE CODE DIFF UTILITIES IN MORE (Find a file, Settings), THE FILES TOGGLE KEPT DIRECT, AND COPY THE DIFF MADE HONEST (instance 6 of the silent-no-op class). Records with the prefix B3CD. Version 8 (compose-8: the icon-size cell read the scale on the stage instead of on the icon [INSTRUMENT]; the drawn width is 41 x 0.72 at every size). Version 7 (the Lead replaced the lexical word ban with TRUTHFUL CLAIMS ABOUT WHAT WAS COPIED, judged semantically; the product wording stays: 'Select the lines in the diff' names the view). Version 6 (compose-4 triage: a locator made on the first page measured the first page after the second boot [INSTRUMENT]; the failure-sentence assertion is unchanged and the PRODUCT wording moved to candidate 4). Version 5 (compose-3 triage: the no-file state is entered in a collection with no openable file [SETUP]; the Copy-words reader no longer waits on an absent element [INSTRUMENT]; the rendered oracle takes the first hunk header from the column strip [EXPECTATION]; a Copy-icon size cell guards the label rename). Version 4 (plus the Code Diff ROUTES: a skip control whose cost is invariant across collection sizes, and the shared SEEN helper). Version 3 (Copy HELD wherever there is no content, with the notice's own reason; known-empty vs unread; the payload verified against two independent expectations; truthful words). Version 2: the Lead's D-files ruling
 * (Hide/Show files stays a DIRECT, labelled control in the editor bar: B2d's accepted place and focus pin) and the clipboard OUTCOMES verified across success / clipboard ABSENT / clipboard REFUSED.
 *
 * THE CLAIM, per viewport, each in its own test: (1) MORE is a live anchored popover (it was a disabled icon) holding exactly Find a file and Settings, NOT Hide/Show files; non-modal; Escape closes ONLY the popover and
 * returns focus to the trigger. (2) FILES stays DIRECT: the toggle is in the editor bar, outside the popover, still collapses the tree, its focus stays on it. (3) FIND, when it opens the box, puts focus on the search input; with
 * the tree collapsed it opens the tree and the box (B2d round 20). (4) SETTINGS, closed with Escape, returns focus to the trigger. (5) COPY, with NO file chosen, is HELD (aria-disabled, a stated reason, dimmed); with a file it is available.
 * (5b) COPY HOLD: a file OBJECT is not content: withheld, binary, too large, not UTF-8, unsupported, still reading and refused files, and a known-empty file, all have no lines; Copy is held with the notice's own reason, writes nothing, announces nothing.
 * (6) COPY OUTCOME (three tests): a stubbed clipboard that succeeds, one that is ABSENT, one that REFUSES (NotAllowedError): each says what happened locally (a polite status region and a visible line beside the path), the failures name
 * the recovery (select the lines and copy them yourself), focus stays on Copy, the statement clears when another file is chosen, and the text handed to the clipboard is RECORDED (length, sha256) so canonical and candidate bytes can be compared.
 * HONEST BOUNDS: Chrome, not Brave; the clipboard is a STUB in these cells (a real clipboard write needs a granted permission this headless run does not have), so they prove the page's handling of each outcome, not the OS clipboard;
 * the sample read of the review fixture; the keyboard-route lengths are RECORDED, not asserted. NEGATIVE CONTROL (on the CURRENT bytes): the MORE/FIND/COPY tests fail at their first CONTRACT line (no `di-scan-diff-more` live
 * trigger; Copy has no aria-disabled; no outcome statement), after their STATE lines pass.
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
const SHOTS = process.env.DI_B3CD_SHOTS ?? null;


let server: ViteDevServer | null = null;
let wss: WebSocketServer | null = null;
let browser: Browser | null = null;
let page: Page | null = null;
let origin = '';
let unavailable: string | null = null;
let socket: ServerSocket | null = null;
let resolveConnected: (ws: ServerSocket) => void = () => undefined;
/** B3 1B: refuse file reads (snapshot_moved), or never answer them, so the refused and reading states are drawn. */
let refuseFiles = false;
let holdFiles = false;
/** B3 routes: the number of files the overview lists (null: the fixture's own 23). Fewer: the first N; more: synthetic text files appended. The totals are recomputed so the listing stays consistent. */
let collectionSize: number | null = null;

function replyFor(request: Record<string, unknown>): DiCodeReview | null {
  const stored =
    request.fileId === undefined ? doc.replies.overview : Object.values(doc.replies.files).find((r) => r.fileId === request.fileId);
  if (stored === undefined) return null;
  const reply = structuredClone(stored);
  if (request.fileId === undefined && collectionSize !== null && reply.overview !== null) {
    const base = reply.overview.files;
    const files = collectionSize <= base.length ? base.slice(0, collectionSize) : [...base, ...Array.from({ length: collectionSize - base.length }, (_, i) => ({ ...base[base.length - 1]!, fileId: `syn-${i}`, path: `synthetic/set-${i % 9}/file-${i}.txt`, oldPath: null, change: 'modified', state: 'text', reason: null, additions: 1, deletions: 0 }))];
    reply.overview = { files, totals: { files: files.length, additions: files.reduce((n, f) => n + (f.additions ?? 0), 0), deletions: files.reduce((n, f) => n + (f.deletions ?? 0), 0), withheld: files.filter((f) => f.state === 'withheld').length, unshown: files.filter((f) => f.state !== 'text' && f.state !== 'submodule').length } };
  }
  return reply;
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
          if (holdFiles && msg.fileId !== undefined) return;
          const reply =
            refuseFiles && msg.fileId !== undefined
              ? { ...found, snapshot: String(msg.snapshot), state: 'refused', decidedBy: 'backend', problem: 'snapshot_moved', detail: 'moved', target: null, comparison: null, file: null, overview: null }
              : found;
          ws.send(JSON.stringify(typeof msg.requestId === 'string' ? { ...reply, requestId: msg.requestId } : reply));
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
    console.warn(`SKIPPED (B3 code diff menus): ${unavailable}`);
    ctx.skip();
    return;
  }
  await page?.close();
  page = null;
  refuseFiles = false;
  holdFiles = false;
  collectionSize = null;
}, STEPS_MS);

const p = () => page!;
const record = (name: string, r: unknown) => console.log(`B3CD ${name}: ${JSON.stringify(r)}`);

/** Method rule 4: never overwrite a measurement artifact. */
async function shot(name: string): Promise<void> {
  if (SHOTS === null) return;
  fs.mkdirSync(SHOTS, { recursive: true });
  const file = path.join(SHOTS, name);
  assert.equal(fs.existsSync(file), false, `refusing to overwrite ${file}`);
  await p().screenshot({ path: file });
  console.log(`B3CD SHOT ${name} sha256 ${crypto.createHash('sha256').update(fs.readFileSync(file)).digest('hex')}`);
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


// ── B3 1B: Code Diff More, the Files toggle kept direct, and Copy the diff ───────────────────────────────────────────────────────────────────────────
const SIZES = [
  { width: 1280, height: 800, role: 'a robustness size' },
  { width: 1440, height: 900, role: 'a shipping size' },
  { width: 1600, height: 1000, role: 'a shipping size' },
] as const;
const sel = (id: string) => `[data-testid="${id}"]`;
const focusKey = () => p().evaluate(() => document.activeElement?.getAttribute('data-testid') ?? document.activeElement?.tagName ?? 'none');
const viewKey = () => p().evaluate(() => document.querySelector('[data-testid="di-scan-stage"]')?.getAttribute('data-view') ?? null);
const rootInert = () =>
  p().evaluate(() => {
    const r = document.getElementById('root');
    return r === null ? null : r.inert;
  });
const settingsDialog = () => p().getByRole('dialog', { name: 'Settings' });
async function openMore(): Promise<void> {
  await p().click(sel('di-scan-diff-more'));
  await p().waitForSelector(sel('di-scan-diff-more-popover'));
}
async function atDiff(vp: { width: number; height: number }): Promise<void> {
  await boot(vp);
  await openCodeDiff();
  assert.equal(await viewKey(), 'diff', 'STATE: the Code Diff is open');
  assert.equal(await p().locator(sel('di-scan-diff-more-popover')).count(), 0, 'STATE: the popover starts closed');
}
type ClipboardMode = 'ok' | 'absent' | 'refused';
/** The page's clipboard, replaced by a stub that records what it was asked to write. */
async function stubClipboard(mode: ClipboardMode): Promise<void> {
  await p().evaluate((m) => {
    const w = window as unknown as { __copied: string[] };
    w.__copied = [];
    const stub =
      m === 'absent'
        ? undefined
        : {
            writeText: (text: string) => {
              w.__copied.push(text);
              return m === 'ok' ? Promise.resolve() : Promise.reject(new DOMException('denied', 'NotAllowedError'));
            },
          };
    Object.defineProperty(navigator, 'clipboard', { value: stub, configurable: true });
  }, mode);
}
/**
 * The claims a Copy outcome message makes about WHAT WAS COPIED, each judged against the payload we know (the lines' text joined by newlines: no + or minus marks, no line numbers, not a patch, not the whole file's bytes).
 * Success: it must say it copied the LINES SHOWN and must say the marks and the line numbers are NOT in them; it must not claim a patch, a complete or whole or entire or full file/diff/change, or validity.
 * Failure: it must say it could NOT copy and must not affirm a copy ("Copied ...") of anything. What it names elsewhere (the view, the control) is not a claim about the payload and is not judged.
 */
function messageClaims(message: string, mode: 'ok' | 'absent' | 'refused'): Array<{ what: string; holds: boolean }> {
  const overclaim = /\b(patch|complete|whole|entire|full|valid|every line|all (?:of )?the (?:lines|changes))\b/i.test(message);
  if (mode === 'ok')
    return [
      { what: 'a success says it copied the lines shown', holds: /\bcopied the lines shown\b/i.test(message) },
      { what: 'and says the + and minus marks are not in them', holds: /without the [^.]*marks/i.test(message) && /[+]/.test(message) },
      { what: 'and says the line numbers are not in them', holds: /line numbers/i.test(message) },
      { what: 'and claims no patch, complete, whole, entire, full or valid payload', holds: !overclaim },
    ];
  return [
    { what: 'a failure says it could not copy', holds: /^could not copy\b/i.test(message) },
    { what: 'and affirms no copy of anything', holds: !/\bcopied\b/i.test(message) },
    { what: 'and claims no payload at all', holds: !overclaim },
  ];
}
/** Choose a tree row by its name and wait for the editor bar to name it (for files that have NO lines to wait for). */
async function chooseFile(name: string): Promise<void> {
  await p().locator('[data-testid="di-scan-tree-file"]', { hasText: name }).first().click();
  await p().waitForFunction((n) => (document.querySelector('[data-testid="di-scan-editor-path"]')?.textContent ?? '').includes(n), name, { timeout: 15_000 });
  await p().waitForTimeout(150);
}
/** A control character or a bidirectional override: what the page's display transform would change. (Written without a control-character regular expression, which the lint forbids.) */
function hasControl(text: string): boolean {
  return [...text].some((ch) => {
    const c = ch.codePointAt(0)!;
    return c < 0x20 || (c >= 0x7f && c <= 0x9f) || (c >= 0x202a && c <= 0x202e) || (c >= 0x2066 && c <= 0x2069);
  });
}
/** The expected copy text, from the RAW fixture (hunks and lines), not from the code under test. Preconditions: no clipped line and no control characters, so the page's display transform is the identity. */
function fixtureExpectation(filePath: string): string {
  const reply = Object.values(doc.replies.files).find((r) => r.file?.path === filePath);
  assert.ok(reply?.file, `fixture: ${filePath} has a file reply`);
  const out: string[] = [];
  for (const hunk of reply.file.hunks) {
    out.push(`@@ -${hunk.oldStart},${hunk.oldLines} +${hunk.newStart},${hunk.newLines} @@${hunk.section === null ? '' : ` ${hunk.section}`}`);
    for (const line of hunk.lines) {
      assert.ok(line.clipped === 0 && !hasControl(line.text), 'fixture precondition: the chosen file has no clipped line and no control characters');
      out.push(line.kind === 'no_newline' ? '\\ No newline at end of file' : line.text);
    }
  }
  return out.join('\n');
}
/** The code as the editor DRAWS it, line by line: hunk headers, then each line's text without its sign span and without the line numbers. */
const renderedLines = () =>
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
const copiedTexts = () => p().evaluate(() => (window as unknown as { __copied?: string[] }).__copied ?? []);
const copyWords = () =>
  p().evaluate(() => {
    const status = document.querySelector('[data-testid="di-scan-diff-copy-status"]');
    const shown = document.querySelector('[data-testid="di-scan-diff-copy-result"]');
    return { status: status?.textContent ?? null, visible: shown === null ? null : (shown.textContent ?? '').trim(), ok: shown?.getAttribute('data-ok') ?? null };
  });

// ── SEEN: the calibrated visibility helper (shared by the B3 inputs). VERSION 2 (version 1 reported false negatives, found by the first composed run: it treated every overflow ancestor as clipping, but an overflow ancestor clips only the descendants whose
// CONTAINING BLOCK chain it is in, so a fixed or absolutely positioned control outside it is NOT clipped; and it sampled the rounded CORNERS of a box, where the element is not hit). "Seen" means a person could see it: it has a drawn box, is not hidden by CSS,
// lies inside every ancestor that actually CLIPS it (along its containing-block chain) and the window, and is the topmost element at its centre and the middles of its four edges (not covered). It returns the reasons it is not seen. It is CALIBRATED before it is trusted (calibrateSeen): it must say yes to a plain
// control and no to one clipped by an overflow ancestor, one covered by an overlay and one hidden by a 1px clip; a helper that cannot say no proves nothing.
interface SeenInfo {
  seen: boolean;
  why: string[];
  width: number;
  height: number;
}
const seenIn = (selector: string): SeenInfo => {
  const el = document.querySelector<HTMLElement>(selector);
  if (el === null) return { seen: false, why: ['absent'], width: 0, height: 0 };
  const why: string[] = [];
  const cs = getComputedStyle(el);
  if (cs.display === 'none') why.push('display:none');
  if (cs.visibility !== 'visible') why.push(`visibility:${cs.visibility}`);
  if (Number(cs.opacity) === 0) why.push('opacity:0');
  if (cs.clipPath !== 'none') why.push(`clip-path:${cs.clipPath}`);
  const r = el.getBoundingClientRect();
  if (r.width < 2 || r.height < 2) why.push(`no box (${r.width.toFixed(1)}x${r.height.toFixed(1)})`);
  if (r.right <= 0 || r.bottom <= 0 || r.left >= window.innerWidth || r.top >= window.innerHeight) why.push('outside the window');
  const escapes = (a: Element) => {
    const c = getComputedStyle(a);
    return c.transform !== 'none' || c.filter !== 'none' || c.perspective !== 'none' || /paint|layout|strict|content/.test(c.contain) || c.willChange.includes('transform');
  };
  /** The box that bounds this element's painting: the viewport for a fixed element (unless a transformed ancestor holds it), the nearest positioned ancestor for an absolute one, else the parent. */
  const containingBlock = (node: Element): Element | null => {
    const pos = getComputedStyle(node).position;
    let a = node.parentElement;
    if (pos === 'fixed') {
      while (a !== null && !escapes(a)) a = a.parentElement;
      return a;
    }
    if (pos === 'absolute') {
      while (a !== null && getComputedStyle(a).position === 'static' && !escapes(a)) a = a.parentElement;
      return a;
    }
    return a;
  };
  for (let a = containingBlock(el); a !== null; a = containingBlock(a)) {
    const s = getComputedStyle(a);
    if (s.overflowX === 'visible' && s.overflowY === 'visible' && s.clipPath === 'none') continue;
    const ar = a.getBoundingClientRect();
    const ix = Math.min(r.right, ar.right) - Math.max(r.left, ar.left);
    const iy = Math.min(r.bottom, ar.bottom) - Math.max(r.top, ar.top);
    if (ix < r.width - 0.5 || iy < r.height - 0.5) why.push(`clipped by ${a.getAttribute('data-testid') ?? (typeof a.className === 'string' ? a.className.split(' ')[0] : a.tagName)}`);
  }
  const cx = r.left + r.width / 2;
  const cy = r.top + r.height / 2;
  const points: Array<[number, number]> = [
    [cx, cy],
    [r.left + 2, cy],
    [r.right - 2, cy],
    [cx, r.top + 2],
    [cx, r.bottom - 2],
  ];
  for (const [x, y] of points) {
    if (x < 0 || y < 0 || x >= window.innerWidth || y >= window.innerHeight) continue;
    const top = document.elementFromPoint(x, y);
    if (top === null || !(top === el || el.contains(top))) {
      why.push(`covered at ${Math.round(x)},${Math.round(y)} by ${top?.getAttribute('data-testid') ?? top?.tagName ?? 'nothing'}`);
      break;
    }
  }
  return { seen: why.length === 0, why, width: r.width, height: r.height };
};
const seen = (selector: string): Promise<SeenInfo> => p().evaluate(seenIn, selector);
/** Inject four known controls, check the helper's verdict on each, then remove them. */
async function calibrateSeen(): Promise<void> {
  await p().evaluate(() => {
    const host = document.createElement('div');
    host.id = 'seen-cal';
    host.style.cssText = 'position:fixed;left:8px;top:8px;z-index:2147483000';
    host.innerHTML =
      '<button id="cal-visible" style="display:block;width:120px;height:28px">visible</button>' +
      '<div style="width:20px;height:20px;overflow:hidden;position:relative"><button id="cal-clipped" style="position:absolute;left:60px;top:0;width:80px;height:20px">clipped</button></div>' +
      '<button id="cal-covered" style="display:block;width:120px;height:28px">covered</button>' +
      '<button id="cal-hidden" style="position:absolute;width:1px;height:1px;overflow:hidden;clip-path:inset(50%)">hidden</button>' +
      '<button id="cal-round" style="display:block;width:120px;height:28px;border-radius:14px">rounded</button>' +
      '<div style="width:20px;height:20px;overflow:hidden;position:relative"><button id="cal-fixed" style="position:fixed;left:200px;top:8px;width:80px;height:20px">fixed escapee</button></div>';
    document.body.appendChild(host);
    const covered = document.getElementById('cal-covered')!.getBoundingClientRect();
    const cover = document.createElement('div');
    cover.id = 'cal-cover';
    cover.style.cssText = `position:fixed;left:${covered.left - 2}px;top:${covered.top - 2}px;width:${covered.width + 4}px;height:${covered.height + 4}px;z-index:2147483647`;
    document.body.appendChild(cover);
  });
  const verdicts = { visible: (await seen('#cal-visible')).seen, clipped: (await seen('#cal-clipped')).seen, covered: (await seen('#cal-covered')).seen, hidden: (await seen('#cal-hidden')).seen, rounded: (await seen('#cal-round')).seen, fixedEscapee: (await seen('#cal-fixed')).seen };
  await p().evaluate(() => {
    document.getElementById('seen-cal')?.remove();
    document.getElementById('cal-cover')?.remove();
  });
  assert.deepEqual(verdicts, { visible: true, clipped: false, covered: false, hidden: false, rounded: true, fixedEscapee: true }, 'CALIBRATION: the SEEN helper says yes to a plain, a rounded and a fixed-position control that an overflow ancestor does not clip, and no to a clipped, a covered and a 1px-hidden one');
}

for (const vp of SIZES) {
  const where = `${vp.width}x${vp.height} (${vp.role})`;
  test(`B3 CODE DIFF, at ${where}, MORE (DRAWN AND REACHABLE): the popover is as wide as its content, each item is SEEN and hit-testable at its own centre, each label is legible, and a click at an item's centre activates THAT item`, async () => {
    // The 140 green asserted the items by DOM and the popover's role, not drawn geometry; at 1440 and 1600 the popover was 14px wide (inline max-width 0px) and a click where Find appeared hit the editor behind it.
    await atDiff(vp);
    await openMore();
    const problems: string[] = [];
    const ids = ['di-scan-diff-find', 'di-scan-diff-settings'];
    const facts = await p().evaluate((itemIds) => {
      const round = (n: number) => Math.round(n * 10) / 10;
      const pop = document.querySelector<HTMLElement>('[data-testid="di-scan-diff-more-popover"]');
      const popBox = pop?.getBoundingClientRect();
      return {
        popover: pop === null || popBox === undefined ? null : { w: round(popBox.width), l: round(popBox.left), r: round(popBox.right), scrollW: pop.scrollWidth, clientW: pop.clientWidth },
        items: itemIds.map((id) => {
          const el = document.querySelector<HTMLElement>(`[data-testid="${id}"]`);
          if (el === null) return { id, present: false as const };
          const box = el.getBoundingClientRect();
          // the label's own text metrics: the natural width of its text, read from a Range over its contents
          const range = document.createRange();
          range.selectNodeContents(el);
          const text = range.getBoundingClientRect().width;
          return { id, present: true as const, w: round(box.width), l: round(box.left), r: round(box.right), textW: round(text), scrollW: el.scrollWidth, clientW: el.clientWidth };
        }),
      };
    }, ids);
    record(`MORE-DRAWN ${vp.width}x${vp.height}`, facts);
    if (facts.popover === null) problems.push('the popover is not mounted');
    else if (facts.popover.scrollW > facts.popover.clientW + 1) problems.push(`the popover is ${facts.popover.w}px wide and its content is ${facts.popover.scrollW}px: the content is clipped by its own box`);
    for (const it of facts.items) {
      if (!it.present) { problems.push(`${it.id} is not present`); continue; }
      if (it.w < it.textW - 0.5) problems.push(`${it.id}: its box is ${it.w}px wide but its label's own text is ${it.textW}px: the label is not legible`);
      if (it.scrollW > it.clientW + 1) problems.push(`${it.id}: its content (${it.scrollW}px) is wider than its box (${it.clientW}px)`);
      if (facts.popover !== null && (it.r > facts.popover.r + 0.5 || it.l < facts.popover.l - 0.5)) problems.push(`${it.id}: it lies outside the popover's box (${it.l}..${it.r} against ${facts.popover.l}..${facts.popover.r})`);
      const v = await seen(sel(it.id));
      if (!v.seen) problems.push(`${it.id} is NOT SEEN (${v.why.join('; ')})`);
      const hit = await p().evaluate((id) => { const el = document.querySelector<HTMLElement>(`[data-testid="${id}"]`); if (el === null) return null; const r = el.getBoundingClientRect(); const top = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2); return { onItem: top === el || el.contains(top), topmost: top === null ? null : `${top.tagName.toLowerCase()}${top.getAttribute('data-testid') !== null ? `[${top.getAttribute('data-testid')}]` : ''}` }; }, it.id);
      if (hit === null || !hit.onItem) problems.push(`${it.id}: the element at its own centre is ${hit?.topmost ?? 'nothing'}, not the item`);
    }
    await shot(`b3cd-more-drawn-${vp.width}x${vp.height}.png`);
    // POINTER REACH, performed and recorded BEFORE the assertion so a red run reaches it: a click at the item's centre activates THAT item.
    const centre = async (id: string) => p().evaluate((tid) => { const el = document.querySelector<HTMLElement>(`[data-testid="${tid}"]`); if (el === null) return null; const r = el.getBoundingClientRect(); return { x: r.left + r.width / 2, y: r.top + r.height / 2 }; }, id);
    const findAt = await centre('di-scan-diff-find');
    if (findAt === null) problems.push('POINTER: Find a file is not present to click');
    else {
      await p().mouse.click(findAt.x, findAt.y);
      await p().waitForTimeout(250);
      const opened = await p().evaluate(() => ({ search: document.querySelector('[data-testid="di-scan-diff-search"]') !== null, focus: document.activeElement?.getAttribute('data-testid') ?? null }));
      if (!opened.search || opened.focus !== 'di-scan-diff-search') problems.push(`POINTER: a click at the centre of Find a file did not activate it (the Find box ${opened.search ? 'is open' : 'is not open'}; focus is on ${opened.focus})`);
    }
    if ((await p().locator(sel('di-scan-diff-more-popover')).count()) === 0) await openMore();
    const settingsAt = await centre('di-scan-diff-settings');
    if (settingsAt === null) problems.push('POINTER: Settings is not present to click');
    else {
      await p().mouse.click(settingsAt.x, settingsAt.y);
      await p().waitForTimeout(400);
      const dialogs = await settingsDialog().count();
      if (dialogs === 0) problems.push('POINTER: a click at the centre of Settings did not activate it (no Settings dialog)');
      else await p().keyboard.press('Escape');
    }
    record(`MORE-DRAWN-PROBLEMS ${vp.width}x${vp.height}`, { problems });
    assert.deepEqual(problems, [], `CONTRACT (MORE, DRAWN AND REACHABLE): at ${vp.width}x${vp.height} the popover is drawn at a usable width, each item is seen, legible and hit-testable at its own centre, and a click there activates that item`);
  });
  test(`B3 CODE DIFF, at ${where}, MORE: a live, non-modal popover holding Find a file and Settings only; Escape is consumed`, async () => {
    await atDiff(vp);
    const trigger = p().locator(sel('di-scan-diff-more'));
    assert.equal(await trigger.getAttribute('aria-haspopup'), 'dialog', 'CONTRACT (MORE): the trigger announces a popup');
    assert.equal(await trigger.isEnabled(), true, 'CONTRACT (MORE): More is no longer a disabled control');
    await openMore();
    const items = await p().locator(`${sel('di-scan-diff-more-popover')} .di-popover-action`).evaluateAll((els) => els.map((e) => e.getAttribute('data-testid')));
    record(`MORE ${vp.width}x${vp.height}`, { focus: await focusKey(), items });
    await shot(`b3cd-more-${vp.width}x${vp.height}.png`);
    assert.equal(await rootInert(), false, 'CONTRACT (MORE): a non-modal popover leaves #root live');
    assert.deepEqual(items, ['di-scan-diff-find', 'di-scan-diff-settings'], 'CONTRACT (MORE): the popover holds Find a file and Settings, and NOT the Files toggle (it stays direct)');
    await p().keyboard.press('Escape');
    assert.equal(await p().locator(sel('di-scan-diff-more-popover')).count(), 0, 'CONTRACT (ESCAPE): the popover closed');
    assert.equal(await viewKey(), 'diff', 'CONTRACT (ESCAPE): the Code Diff is KEPT (the press was consumed)');
    assert.equal(await focusKey(), 'di-scan-diff-more', 'CONTRACT (ESCAPE): focus is on the trigger');
  });
  test(`B3 CODE DIFF, at ${where}, FILES (direct): the toggle is outside the popover, collapses and restores the tree, and keeps its own focus`, async () => {
    await atDiff(vp);
    const toggle = p().locator(sel('di-scan-files-toggle'));
    assert.equal(await toggle.count(), 1, 'CONTRACT (FILES): the Files toggle is drawn without opening any menu');
    assert.equal(await toggle.evaluate((el) => el.closest('[data-testid="di-scan-diff-more-popover"]') === null), true, 'CONTRACT (FILES): it is not inside the popover');
    assert.equal(await toggle.getAttribute('aria-controls'), 'di-scan-files', 'STATE: B2d\'s control still names the tree it controls');
    assert.equal((await toggle.textContent())?.trim(), 'Hide files', 'STATE: it says Hide files while the tree is shown');
    await toggle.click();
    assert.equal(await p().locator('#di-scan-files').isVisible(), false, 'CONTRACT (FILES): the tree is collapsed');
    assert.equal(await focusKey(), 'di-scan-files-toggle', 'CONTRACT (FILES): focus stays on the toggle');
    assert.equal((await toggle.textContent())?.trim(), 'Show files', 'CONTRACT (FILES): the label flips');
    await toggle.click();
    assert.equal(await p().locator('#di-scan-files').isVisible(), true, 'CONTRACT (FILES): the tree is back');
  });
  test(`B3 CODE DIFF, at ${where}, FIND: opening the box puts focus on the search input, also with the tree collapsed; closing returns to the trigger`, async () => {
    await atDiff(vp);
    assert.equal(await p().locator(sel('di-scan-diff-search')).count(), 0, 'STATE: the search box starts closed');
    await openMore();
    assert.equal(await p().locator(sel('di-scan-diff-find')).getAttribute('aria-pressed'), 'false', 'STATE: Find reads off while the box is closed');
    await p().locator(sel('di-scan-diff-find')).click();
    assert.equal(await p().locator(sel('di-scan-diff-more-popover')).count(), 0, 'CONTRACT (FIND): selecting closes the popover');
    assert.equal(await p().locator(sel('di-scan-diff-search')).count(), 1, 'CONTRACT (FIND): the search box is open');
    record(`FIND-OPEN ${vp.width}x${vp.height}`, { focus: await focusKey() });
    assert.equal(await focusKey(), 'di-scan-diff-search', 'CONTRACT (FIND): opening the box puts focus on the search input, its intentional destination');
    await openMore();
    assert.equal(await p().locator(sel('di-scan-diff-find')).getAttribute('aria-pressed'), 'true', 'CONTRACT (FIND): Find announces the box is open');
    await p().locator(sel('di-scan-diff-find')).click();
    assert.equal(await p().locator(sel('di-scan-diff-search')).count(), 0, 'CONTRACT (FIND): the box is closed again');
    assert.equal(await focusKey(), 'di-scan-diff-more', 'CONTRACT (FIND): closing returns focus to the trigger');
    // B2d round 20: with the tree collapsed, Find OPENS the tree it searches and its box, and lands on the input
    await p().locator(sel('di-scan-files-toggle')).click();
    assert.equal(await p().locator('#di-scan-files').isVisible(), false, 'STATE: the tree is collapsed');
    await openMore();
    await p().locator(sel('di-scan-diff-find')).click();
    assert.equal(await p().locator('#di-scan-files').isVisible(), true, 'CONTRACT (FIND): Find opens the collapsed tree it searches');
    assert.equal(await focusKey(), 'di-scan-diff-search', 'CONTRACT (FIND): and lands on the search input');
  });
  test(`B3 CODE DIFF, at ${where}, SETTINGS: Escape closes the dialog and focus goes back to the trigger`, async () => {
    await atDiff(vp);
    await openMore();
    assert.equal(await p().locator(sel('di-scan-diff-settings')).getAttribute('aria-disabled'), null, 'STATE: Settings is available in this office');
    await p().locator(sel('di-scan-diff-settings')).click();
    await settingsDialog().waitFor({ timeout: 5_000 });
    await p().keyboard.press('Escape');
    assert.equal(await settingsDialog().count(), 0, 'CONTRACT (SETTINGS): Escape closed the dialog');
    record(`SETTINGS ${vp.width}x${vp.height}`, { focus: await focusKey(), view: await viewKey() });
    assert.equal(await focusKey(), 'di-scan-diff-more', 'CONTRACT (SETTINGS): focus returns to the More trigger');
  });
  test(`B3 CODE DIFF, at ${where}, COPY (no file): held with its reason while no file is chosen; available once one is`, async () => {
    // SETUP (compose-3, a SETUP FAILURE of the first version): Code Diff AUTO-CHOOSES the first openable file (data/large.txt), so a default boot never has no file. The no-file state is entered in a collection with NO openable file: the first four listed files are all withheld.
    collectionSize = 4;
    await atDiff(vp);
    collectionSize = null;
    const copy = p().locator(sel('di-scan-diff-copy'));
    assert.equal(await p().locator(sel('di-scan-tree-file')).count(), 4, 'STATE: the tree lists four files');
    assert.equal(await p().locator(sel('di-scan-editor-path')).textContent(), '', 'STATE: no file is chosen (none of the four is openable, so none is auto-chosen)');
    const before = await copy.evaluate((el) => ({ held: el.getAttribute('aria-disabled'), opacity: Number(getComputedStyle(el).opacity), title: el.getAttribute('title'), described: el.getAttribute('aria-describedby') }));
    record(`COPY-NOFILE ${vp.width}x${vp.height}`, before);
    assert.equal(before.held, 'true', 'CONTRACT (COPY): with no file chosen Copy the lines shown is held (aria-disabled), not a silent no-op');
    assert.ok((before.title ?? '').startsWith('Choose a file in the tree first'), `CONTRACT (COPY): the reason is stated (${before.title})`);
    assert.ok(before.opacity < 1, `CONTRACT (COPY): a held Copy is drawn dimmed (${before.opacity})`);
    await copy.focus();
    assert.equal(await focusKey(), 'di-scan-diff-copy', 'CONTRACT (COPY): a held Copy keeps its focus');
    await atDiff(vp);
    assert.ok((await p().locator(sel('di-scan-editor-path')).textContent())?.includes('large.txt'), 'STATE: the default collection auto-chose its first openable file');
    const copyNow = p().locator(sel('di-scan-diff-copy'));   // INSTRUMENT (compose-4): `copy` above is bound to the FIRST page; after the second boot the measurement must use a locator made on the second
    await selectFile('syntax_sample.ts');
    const after = await copyNow.evaluate((el) => ({ held: el.getAttribute('aria-disabled'), opacity: Number(getComputedStyle(el).opacity) }));
    record(`COPY-FILE ${vp.width}x${vp.height}`, after);
    assert.equal(after.held, null, 'CONTRACT (COPY): with a file chosen Copy the diff is available');
    assert.equal(after.opacity, 1, 'CONTRACT (COPY): and drawn at full strength');
    // the icon-size rule selected the OLD label ([aria-label='Copy the diff']); renamed, it must still apply: the icon's drawn width is 41px times the header scale the icon itself resolves (read from the icon)
    const icon = await p().evaluate(() => {
      const svg = document.querySelector('[data-testid="di-scan-diff-copy"] svg');
      // INSTRUMENT (compose-8): a custom property resolves where it is USED. The first version read --stage-hdr-k on the stage (1 at 1440, 0.72 at 1280) while the rule's calc() resolves it on the icon itself, which the header scopes differently (0.72 at every size: the drawn 29.515625 is 41 x 0.72 exactly). Read it from the icon.
      return { width: svg === null ? null : svg.getBoundingClientRect().width, k: svg === null ? null : parseFloat(getComputedStyle(svg).getPropertyValue('--stage-hdr-k')) };
    });
    record(`COPY-ICON ${vp.width}x${vp.height}`, icon);
    assert.ok(icon.width !== null && icon.k !== null && !Number.isNaN(icon.k), 'STATE: the Copy icon and the stage scale are read');
    assert.ok(Math.abs((icon.width ?? 0) - 41 * (icon.k ?? 0)) < 0.6, `CONTRACT (LABEL FOLLOW-THROUGH): the Copy icon is drawn at 41px x the scale the icon resolves (${icon.width} vs ${41 * (icon.k ?? 0)}): the size rule still applies after the label rename`);
  });
  test(`B3 CODE DIFF, at ${where}, COPY (held): the reason is DRAWN while the held Copy has focus, not only a title and screen-reader text (no file chosen; a withheld file; a known-empty file)`, async () => {
    // RED FIRST: this cell IS the rendered measurement. What is on the screen when a held Copy has focus: an element whose own text is the reason and which is SEEN (the calibrated helper: drawn, unclipped, hit-testable).
    // The Copy's own title does not count (not an element), and neither does a visually-hidden span (the helper says no to a 1px clip).
    const drawnReason = async (reason: string) => {
      const first = reason.split('. ')[0] ?? reason;
      const count = await p().evaluate((needle) => {
        let n = 0;
        document.querySelectorAll('[data-b3-reason-probe]').forEach((e) => e.removeAttribute('data-b3-reason-probe'));
        document.querySelectorAll<HTMLElement>('body *').forEach((e) => {
          if (e.children.length === 0 && (e.textContent ?? '').includes(needle) && e.getAttribute('data-testid') !== 'di-scan-diff-copy') {
            e.setAttribute('data-b3-reason-probe', String(n));
            n += 1;
          }
        });
        return n;
      }, first);
      const verdicts: Array<{ i: number; seen: boolean; why: string[] }> = [];
      for (let i = 0; i < count; i += 1) {
        const v = await seen(`[data-b3-reason-probe="${i}"]`);
        verdicts.push({ i, seen: v.seen, why: v.why });
      }
      return { carriers: count, drawn: verdicts.some((v) => v.seen), verdicts };
    };
    const problems: string[] = [];   // collected across ALL THREE states and asserted once, so a red run REACHES every state (a run that dies at the first proves only the first)
    const check = async (state: string) => {
      const copy = p().locator(sel('di-scan-diff-copy'));
      const reason = (await copy.getAttribute('title')) ?? '';
      assert.ok(reason.length > 0, `STATE: ${state}: Copy is held and has a reason`);
      await copy.focus();
      assert.equal(await focusKey(), 'di-scan-diff-copy', `STATE: ${state}: the held Copy has focus`);
      const atFocus = await drawnReason(reason);
      await p().keyboard.press('Enter');
      await p().waitForTimeout(150);
      const afterUse = await drawnReason(reason);
      record(`COPY-HELD-DRAWN ${vp.width}x${vp.height} ${state}`, { atFocus, afterUse });
      if (!atFocus.drawn) problems.push(`${state}: while the held Copy has focus its reason is NOT drawn (carriers of the words: ${atFocus.carriers}, ${JSON.stringify(atFocus.verdicts)})`);
      if (!afterUse.drawn) problems.push(`${state}: after the held Copy is activated its reason is NOT drawn (carriers: ${afterUse.carriers}, ${JSON.stringify(afterUse.verdicts)})`);
    };
    collectionSize = 4;
    await atDiff(vp);
    collectionSize = null;
    assert.equal(await p().locator(sel('di-scan-editor-path')).textContent(), '', 'STATE: no file is chosen');
    await check('no file chosen');
    await atDiff(vp);
    await chooseFile('.env');
    await check('a withheld file');
    await chooseFile('empty.txt');
    await check('a known-empty file');
    await shot(`b3cd-copy-held-drawn-${vp.width}x${vp.height}.png`);
    assert.deepEqual(problems, [], 'CONTRACT (COPY HELD, DRAWN): while a held Copy has focus its reason is DRAWN (an element holding it is SEEN), and stays drawn after it is activated, in every held state');
    // and it goes when focus leaves (it is a statement about the control that has focus, not a permanent banner)
    await p().locator(sel('di-scan-editor-path')).focus();
    assert.equal(await p().locator(sel('di-scan-diff-copy-held')).count(), 0, 'CONTRACT (COPY HELD, DRAWN): the drawn reason goes when focus leaves the held Copy');
  });
  test(`B3 CODE DIFF, at ${where}, COPY (nothing to copy): a file object is not content; Copy is held with the NOTICE'S OWN reason, writes nothing and says nothing, and known-empty does not share words with unread`, async () => {
    await atDiff(vp);
    await stubClipboard('ok');
    const rows: Array<{ file: string; state: string | null; held: string | null; reason: string; noticeText: string; writes: number; said: string }> = [];
    // [row text, kind]: UNREAD = its changes were not (or could not be) read; EMPTY = a real file with nothing to show
    const cases: Array<[string, 'unread' | 'empty']> = [
      ['.env', 'unread'],
      ['blob.bin', 'unread'],
      ['bundle.min.js', 'unread'],
      ['latin1.txt', 'unread'],
      ['clone', 'unread'],
      ['empty.txt', 'empty'],
    ];
    for (const [name] of cases) {
      await chooseFile(name);
      const copy = p().locator(sel('di-scan-diff-copy'));
      await copy.focus();
      await p().keyboard.press('Enter');
      await p().waitForTimeout(150);
      rows.push({
        file: name,
        state: await p().locator(sel('di-scan-file-notice')).getAttribute('data-state'),
        held: await copy.getAttribute('aria-disabled'),
        reason: (await copy.getAttribute('title')) ?? '',
        noticeText: ((await p().locator(sel('di-scan-file-notice')).textContent()) ?? '').trim(),
        writes: (await copiedTexts()).length,
        said: (await copyWords()).status ?? '',
      });
    }
    record(`COPY-HOLD ${vp.width}x${vp.height}`, rows);
    await shot(`b3cd-copy-held-${vp.width}x${vp.height}.png`);
    for (const [i, [name, kind]] of cases.entries()) {
      const r = rows[i]!;
      assert.equal(r.held, 'true', `CONTRACT (COPY HOLD): ${name} (${r.state}) has no lines, so Copy is HELD (a file object is not content)`);
      assert.equal(r.writes, 0, `CONTRACT (COPY HOLD): ${name}: activating a held Copy writes NOTHING (it used to hand over an empty string)`);
      assert.equal(r.said, '', `CONTRACT (COPY HOLD): ${name}: and does not announce a copy that did not happen`);
      const own = r.reason.split('. ').slice(1).join('. ').replace(/\.$/, '');
      assert.ok(own.length > 0 && r.noticeText.includes(own), `CONTRACT (COPY HOLD): ${name}: the reason carries the notice's OWN words (${JSON.stringify(own)} in the pane: ${JSON.stringify(r.noticeText.slice(0, 90))})`);
      if (kind === 'unread') assert.ok(/Its changes are not shown/.test(r.reason) && !/no changed lines/.test(r.reason), `CONTRACT (COPY HOLD): ${name}: an UNREAD file is said as unread, not as empty (${r.reason})`);
      else assert.ok(/no changed lines/.test(r.reason) && !/not shown|could not be read|still being read/.test(r.reason), `CONTRACT (COPY HOLD): ${name}: a KNOWN-EMPTY file is said as having no changed lines, not as unread (${r.reason})`);
    }
  });
  test(`B3 CODE DIFF, at ${where}, COPY (reading and refused): the two read-failure states hold Copy with their own words`, async () => {
    await atDiff(vp);
    holdFiles = true;
    await p().locator(sel('di-scan-tree-file'), { hasText: 'syntax_sample.ts' }).first().click();
    await p().waitForSelector(`${sel('di-scan-file-notice')}[data-state="reading"], ${sel('di-scan-diff-reading')}`, { timeout: 15_000 }).catch(() => undefined);
    const reading = await p().locator(sel('di-scan-diff-copy')).evaluate((el) => ({ held: el.getAttribute('aria-disabled'), reason: el.getAttribute('title') ?? '' }));
    holdFiles = false;
    await boot(vp);
    await openCodeDiff();
    refuseFiles = true;
    await p().locator(sel('di-scan-tree-file'), { hasText: 'syntax_sample.ts' }).first().click();
    await p().waitForSelector(`${sel('di-scan-file-notice')}[data-state="refused"]`, { timeout: 15_000 });
    const refused = await p().locator(sel('di-scan-diff-copy')).evaluate((el) => ({ held: el.getAttribute('aria-disabled'), reason: el.getAttribute('title') ?? '' }));
    record(`COPY-HOLD-READ ${vp.width}x${vp.height}`, { reading, refused });
    assert.equal(reading.held, 'true', 'CONTRACT (COPY HOLD): while the file is still being read Copy is held');
    assert.ok(/still being read/.test(reading.reason), `CONTRACT (COPY HOLD): and says so (${reading.reason})`);
    assert.equal(refused.held, 'true', 'CONTRACT (COPY HOLD): a refused read holds Copy');
    assert.ok(/could not be read/.test(refused.reason), `CONTRACT (COPY HOLD): and says it could not be read (${refused.reason})`);
  });
  for (const mode of ['ok', 'absent', 'refused'] as const) {
    test(`B3 CODE DIFF, at ${where}, COPY OUTCOME (${mode}): the result is said locally with its recovery, the payload is the lines shown (two independent expectations), focus stays on Copy, the statement clears with the file`, async () => {
      await atDiff(vp);
      await selectFile('syntax_sample.ts');
      await stubClipboard(mode);
      assert.deepEqual(await copyWords(), { status: '', visible: null, ok: null }, 'STATE: nothing is said before Copy is used');
      await p().locator(sel('di-scan-diff-copy')).focus();
      await p().keyboard.press('Enter');
      await p().waitForTimeout(400);
      const copied = await copiedTexts();
      const words = await copyWords();
      const text = copied[0] ?? '';
      record(`COPY-${mode.toUpperCase()} ${vp.width}x${vp.height}`, { writes: copied.length, textLength: text.length, textSha256: crypto.createHash('sha256').update(text).digest('hex').slice(0, 16), words, focus: await focusKey() });
      await shot(`b3cd-copy-${mode}-${vp.width}x${vp.height}.png`);
      if (mode === 'absent') assert.equal(copied.length, 0, 'STATE: with no clipboard nothing was written');
      else {
        assert.equal(copied.length, 1, 'STATE: the page asked the clipboard exactly once');
        // THE PAYLOAD, against TWO expectations computed apart from the code under test: (A) the raw fixture's hunks and lines; (B) the lines as RENDERED in the editor (without the + / minus marks and the numbers)
        assert.equal(text, fixtureExpectation('src/syntax_sample.ts'), 'CONTRACT (PAYLOAD A): the text is the fixture\'s hunk headers and lines joined by newlines, with no +/- marks and no line numbers');
        assert.equal(text, (await renderedLines()).join('\n'), 'CONTRACT (PAYLOAD B): the text is exactly what the editor shows as code, line for line');
      }
      assert.ok((words.status ?? '').length > 0, 'CONTRACT (COPY OUTCOME): the outcome is said in the polite status region, so it is not silent for assistive technology');
      assert.equal(words.visible, words.status, 'CONTRACT (COPY OUTCOME): the visible line says the same thing');
      // TRUTHFUL CLAIMS ABOUT WHAT WAS COPIED (semantic, not a word list; the first version banned the nouns diff and patch, which rejected a true sentence that merely NAMES THE VIEW): the payload is the lines' text without the marks and the line numbers (checked above against two independent expectations), so a success message may not claim more than that
      for (const claim of messageClaims(words.visible ?? '', mode)) assert.ok(claim.holds, `CONTRACT (TRUTHFUL CLAIMS): ${claim.what} (${words.visible})`);
      if (mode === 'ok') {
        assert.equal(words.ok, 'true', 'CONTRACT (COPY OUTCOME): success is marked as success');
        assert.ok(/copied the lines shown/i.test(words.visible ?? '') && /without the .* marks/i.test(words.visible ?? ''), `CONTRACT (COPY OUTCOME): it says it copied the lines shown, without the marks (${words.visible})`);
      } else {
        assert.equal(words.ok, 'false', 'CONTRACT (COPY OUTCOME): a failure is marked as a failure');
        assert.ok(/could not copy/i.test(words.visible ?? ''), `CONTRACT (COPY OUTCOME): it says it could not copy (${words.visible})`);
        assert.ok(/select the lines/i.test(words.visible ?? ''), `CONTRACT (COPY OUTCOME): and names the recovery (${words.visible})`);
        assert.ok(mode === 'absent' ? /not available/i.test(words.visible ?? '') : /refused/i.test(words.visible ?? ''), `CONTRACT (COPY OUTCOME): and says WHY, which differs between absent and refused (${words.visible})`);
      }
      assert.equal(await focusKey(), 'di-scan-diff-copy', 'CONTRACT (COPY OUTCOME): focus stays on Copy (the control did not go anywhere)');
      await selectFile('long_line');
      assert.deepEqual(await copyWords(), { status: '', visible: null, ok: null }, 'CONTRACT (COPY OUTCOME): the statement is about the file that was copied; choosing another file clears it');
    });
  }
  test(`B3 CODE DIFF, at ${where}, COPY PAYLOAD (clipped and control characters): the text is the rendered code of the file, clipping note included (one independent expectation: the editor itself)`, async () => {
    await atDiff(vp);
    await selectFile('long_line');
    await stubClipboard('ok');
    await p().locator(sel('di-scan-diff-copy')).focus();
    await p().keyboard.press('Enter');
    await p().waitForTimeout(300);
    const [text] = await copiedTexts();
    const rendered = (await renderedLines()).join('\n');
    record(`COPY-CLIPPED ${vp.width}x${vp.height}`, { length: (text ?? '').length, sha256: crypto.createHash('sha256').update(text ?? '').digest('hex').slice(0, 16), clipNote: /more characters not shown/.test(text ?? '') });
    assert.equal(text, rendered, 'CONTRACT (PAYLOAD B): the text is what the editor shows, line for line');
    assert.ok(/more characters not shown/.test(text ?? ''), 'STATE: the clipped line carries its clipping note into the copy, as it is drawn');
  });
}

// ── B3 ROUTES, Code Diff: the cost of reaching the Files toggle and the changes must not grow with the file collection. The SAME route is measured at TWO collection sizes (4 and 103 files) and must cost the same presses. ──
async function pressesTo(target: string, limit = 400, key: 'Tab' | 'Shift+Tab' = 'Tab'): Promise<number | null> {
  for (let i = 1; i <= limit; i += 1) {
    await p().keyboard.press(key);
    if ((await focusKey()) === target) return i;
  }
  return null;
}
async function routeCosts(vp: { width: number; height: number }, size: number | null): Promise<{ files: number; toSkip: number | null; afterEnter: string; toggleAfterSkip: number | null; plainToToggle: number | null }> {
  collectionSize = size;
  await atDiff(vp);
  const files = await p().locator(sel('di-scan-tree-file')).count();
  // THE SKIP ROUTE: from Copy, Tab to the skip control, press Enter (it lands on the editor bar's path label), Shift+Tab to the Files toggle (it precedes the path)
  await p().locator(sel('di-scan-diff-copy')).focus();
  const toSkip = await pressesTo('di-scan-skip-files');
  if (toSkip !== null) await p().keyboard.press('Enter');
  const afterEnter = await focusKey();
  const toggleAfterSkip = toSkip === null ? null : await pressesTo('di-scan-files-toggle', 12, 'Shift+Tab');   // the Files toggle PRECEDES the path in the bar: one Shift+Tab back
  // THE PLAIN ROUTE (what the page cost before): from Copy, Tab on through the tree rows to the Files toggle
  await p().locator(sel('di-scan-diff-copy')).focus();
  const plainToToggle = await pressesTo('di-scan-files-toggle');
  return { files, toSkip, afterEnter, toggleAfterSkip, plainToToggle };
}
for (const vp of SIZES.filter((s) => s.width >= 1440)) {
  const where = `${vp.width}x${vp.height} (${vp.role})`;
  test(`B3 ROUTES, Code Diff, at ${where}, SKIP THE FILE LIST: the route to the changes costs the same presses with 4 files and with 103 (invariance), and the control is genuinely seen when focused`, async () => {
    await atDiff(vp);
    await calibrateSeen();
    const small = await routeCosts(vp, 4);
    const base = await routeCosts(vp, null);
    const large = await routeCosts(vp, 103);
    record(`ROUTE-SKIP ${vp.width}x${vp.height}`, { small, base, large });
    for (const r of [small, base, large]) assert.ok(r.toSkip !== null, `CONTRACT (SKIP): the skip control is reached by sequential Tab (${r.files} files)`);
    assert.ok(small.files < base.files && base.files < large.files, `CALIBRATION: the three collections really differ (${small.files}, ${base.files}, ${large.files} files)`);
    assert.ok(large.plainToToggle !== null && small.plainToToggle !== null && large.plainToToggle > small.plainToToggle, `CALIBRATION: the plain route DOES grow with the collection (${small.plainToToggle} to ${large.plainToToggle} presses), so an invariant skip route is a property, not an accident`);
    for (const r of [small, base, large]) {
      assert.equal(r.afterEnter, 'di-scan-editor-path', `CONTRACT (SKIP): Enter lands on the editor bar's path label (${r.files} files)`);
      assert.ok(r.toggleAfterSkip !== null, `CONTRACT (SKIP): and the Files toggle is reached by Shift+Tab (${r.files} files)`);
    }
    const cost = (r: typeof small) => (r.toSkip ?? -1) + 1 + (r.toggleAfterSkip ?? -1);
    assert.equal(cost(small), cost(large), `CONTRACT (INVARIANT): the skip route costs the same presses with ${small.files} files (${cost(small)}) and ${large.files} (${cost(large)})`);
    assert.equal(cost(base), cost(large), `CONTRACT (INVARIANT): and with the fixture's own ${base.files} (${cost(base)})`);
    // Three diffs and a plain tab through every file. The 30s vitest budget killed this on CI
    // before any assertion (Pixel UI shard 4).
  }, STEPS_MS);
  test(`B3 ROUTES, Code Diff, at ${where}: the skip control is hidden until focused, then SEEN (not clipped, not covered), and the Escape contract is untouched`, async () => {
    await atDiff(vp);
    await calibrateSeen();
    const before = await seen(sel('di-scan-skip-files'));
    await p().locator(sel('di-scan-diff-copy')).focus();
    assert.ok((await pressesTo('di-scan-skip-files', 12)) !== null, 'STATE: the skip control is reached by Tab');
    const focused = await seen(sel('di-scan-skip-files'));
    record(`SKIP-SEEN ${vp.width}x${vp.height}`, { before, focused });
    await shot(`b3rt-skip-${vp.width}x${vp.height}.png`);
    assert.equal(before.seen, false, 'CONTRACT (HIDDEN): unfocused, the control is not drawn');
    assert.equal(focused.seen, true, `CONTRACT (SEEN): focused, it is genuinely visible (${focused.why.join('; ') || 'ok'}; ${focused.width.toFixed(0)}x${focused.height.toFixed(0)})`);
    assert.equal((await p().locator(sel('di-scan-skip-files')).textContent())?.trim(), 'Skip the file list', 'CONTRACT (LABEL): it is labelled');
    await p().keyboard.press('Escape');
    assert.equal(await viewKey(), 'mission', 'CONTRACT (ESCAPE): Escape from the Code Diff still peels one layer to Mission details');
  });
}
