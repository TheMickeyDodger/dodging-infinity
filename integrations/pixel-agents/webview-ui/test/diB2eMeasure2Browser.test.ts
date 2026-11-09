/**
 * B2e, MEASUREMENT 2 (the Lead's grant). Chrome, headless; not Brave evidence; nothing here changes the product. The same real App composition and LABELLED
 * SYNTHETIC request relay as measurement 1 (diB2eMeasureBrowser), extended. Nothing is live.
 *
 * A. WHAT SCROLLS THE STAGE AFTER A PICK, in each of the FOUR pick states (read with turns, read and empty, still being read, not read), at 1280x800,
 *    1440x900 and 1600x1000. A PASS-THROUGH PROBE (measurement only) is installed BEFORE the pick: it records every focus() (its target and
 *    preventScroll), every scrollIntoView / scrollTo / scrollBy, and every scroll event of the stage (scrollTop, and what had focus then), each in order.
 *    Read after the page settles: the focused element; scrollTop; every region's OCCLUSION-AWARE visibility.
 * B. A LONGER RECENT: twelve synthetic asks spread up to 120 characters, the panel's own cap (mission_panel/service.py FIRST_WORDS_CHARS = 120), one
 *    picked from the middle, at the three sizes: every chip's box, whether its text overflows its 32px box, the rows it takes, and the pick's place.
 * C. WHAT RECENT WOULD NEED AS A PANE, against the measured slack: each chip's ONE-LINE (max-content) and LONGEST-WORD (min-content) width, the thread
 *    column's min-content and max-content widths, and the page's own width rules. The fits are COMPUTED in the record, never here.
 *
 * VISIBILITY IS WHAT A PERSON CAN SEE (measurement 1's two instrument defects, corrected): a region is hit-tested on a 5 x 3 grid of points inside its box,
 * and a point counts only when elementFromPoint lands inside the region. checkVisibility() answers "is it rendered", never "can it be seen", and a pinned
 * bar over a region is an occluder.
 * ASSERTED: only the states (method rule 9). Recorded "B2EM2 ...". Captures, never overwritten, go to DI_B2EM2_SHOTS when it names a directory.
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
import type { WebSocket as ServerSocket } from 'ws';
import { WebSocketServer } from 'ws';

import type { DiOfficeState } from '../../core/src/messages.ts';
import { availableCapacityOffice } from './diAvailableCapacity.ts';
import {
  relayReply,
  SYNTHETIC_CONVERSATION_ID,
  SYNTHETIC_OTHER_CONVERSATION_ID,
  syntheticConversation,
  syntheticConversationSummary,
  syntheticDraftedTurn,
  syntheticOverview,
} from './diNewMissionSynthetic.ts';

const STEPS_MS = 120_000;
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const office = availableCapacityOffice(
  (JSON.parse(fs.readFileSync(path.join(root, 'test/fixtures/di-office-state-session-mismatch-v2.FIXTURE.json'), 'utf-8')) as { state: DiOfficeState }).state,
);
const VERSION = (JSON.parse(fs.readFileSync(path.join(root, '..', 'package.json'), 'utf-8')) as { version: string }).version;
const SHOTS = process.env.DI_B2EM2_SHOTS ?? null;
const VIEWPORTS = [
  { width: 1280, height: 800 },
  { width: 1440, height: 900 },
  { width: 1600, height: 1000 },
];

const A = SYNTHETIC_CONVERSATION_ID;
const B = SYNTHETIC_OTHER_CONVERSATION_ID;
const ASK_A = 'SYNTHETIC recent ask A: the conversation that reads';
const ASK_B = 'SYNTHETIC recent ask B: the conversation picked in each state';
/** The panel's own cap on first words (mission_panel/service.py:70). */
const FIRST_WORDS_CHARS = 120;
/** Twelve synthetic asks, lengths spread from short to EXACTLY the cap (each labelled SYNTHETIC). */
const LONG: Array<{ id: string; ask: string }> = [16, 28, 40, 52, 64, 76, 88, 96, 104, 112, 118, 120].map((n, i) => {
  const base = `SYNTHETIC ask ${String(i + 1).padStart(2, '0')}: make the readiness probe stable under load and report what changed in the subsystem`;
  const cut = base.padEnd(FIRST_WORDS_CHARS, ' and its tests').slice(0, n);
  // exactly n characters, never ending in a space (the chip's text is its trimmed first words)
  return { id: `cv-${'0123456789ef'[i].repeat(32)}`, ask: cut.endsWith(' ') ? `${cut.slice(0, -1)}s` : cut };
});

type How = 'ok' | 'empty' | 'refused' | 'hold';
let how: Record<string, How> = {};
let overview: Array<{ id: string; ask: string }> = [];
let server: ViteDevServer | null = null;
let wss: WebSocketServer | null = null;
let browser: Browser | null = null;
let page: Page | null = null;
let url = '';
let unavailable: string | null = null;
let socket: ServerSocket | null = null;
let resolveConnected: (ws: ServerSocket) => void = () => undefined;

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
          return;
        }
        if (msg.type !== 'diMissionRequest') return;
        const send = (m: Record<string, unknown>) => ws.send(JSON.stringify(m));
        if (msg.op === 'refresh') send(relayReply(msg.seq, 'refresh', syntheticOverview(overview.map((c) => syntheticConversationSummary(c.id, c.ask)))));
        if (msg.op === 'conversation') {
          const id = String(msg.conversationId);
          const h = how[id] ?? 'ok';
          const ask = overview.find((c) => c.id === id)?.ask ?? 'SYNTHETIC';
          if (h === 'ok') send(relayReply(msg.seq, 'conversation', syntheticConversation([syntheticDraftedTurn(ask)], id)));
          else if (h === 'empty') send(relayReply(msg.seq, 'conversation', syntheticConversation([], id)));
          else if (h === 'refused')
            send({ type: 'diMissionRequestReply', seq: msg.seq, op: 'conversation', state: 'refused', problem: 'mission_panel_conversation_unreadable', reason: 'SYNTHETIC: that conversation could not be read' });
          // 'hold': never answered while measured (the page shows the conversation as still being read)
        }
      }),
    );
  });
  const addr = server.httpServer?.address();
  url = `http://localhost:${typeof addr === 'object' && addr !== null ? addr.port : 0}/test/harness/di-app-scan.html`;
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
    console.warn(`SKIPPED (B2e measurement 2): ${unavailable}`);
    ctx.skip();
    return;
  }
  await page?.close();
  page = null;
  how = {};
  overview = [];
}, STEPS_MS);

const p = () => page!;
const sel = (id: string) => `[data-testid="${id}"]`;
const chipFor = (id: string) => p().locator(`[data-testid="di-new-recent-ask"][data-conversation="${id}"]`);
const record = (name: string, r: unknown) => console.log(`B2EM2 ${name}: ${JSON.stringify(r)}`);

async function shot(name: string): Promise<void> {
  if (SHOTS === null) return;
  const file = path.join(SHOTS, name);
  assert.ok(!fs.existsSync(file), `refusing to overwrite ${file}`);
  await p().screenshot({ path: file });
}

async function boot(viewport: { width: number; height: number }, conversations: Array<{ id: string; ask: string }>): Promise<void> {
  overview = conversations;
  const connected = new Promise<ServerSocket>((resolve) => (resolveConnected = resolve));
  page = await browser!.newPage({ viewport });
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await page.goto(url, { waitUntil: 'domcontentloaded' });
  await connected;
  socket!.send(JSON.stringify({ type: 'providerCapabilities', readingTools: ['Read', 'Grep', 'Glob', 'WebFetch', 'WebSearch'], subagentToolNames: ['Task', 'Agent'] }));
  socket!.send(JSON.stringify({ type: 'settingsLoaded', soundEnabled: false, lastSeenVersion: VERSION, extensionVersion: VERSION }));
  socket!.send(JSON.stringify(office));
  await p().waitForSelector(sel('di-rail-new-orchestrator'));
  await p().click(sel('di-rail-new-orchestrator'));
  await p().waitForSelector(sel('di-new-mission'));
  for (const c of conversations) await chipFor(c.id).waitFor({ timeout: 20_000 });
}

/** The PASS-THROUGH PROBE (measurement only; every wrapped call goes on to the original): installed BEFORE the pick. */
const installProbe = () =>
  p().evaluate(() => {
    type Ev = Record<string, unknown>;
    const w = window as unknown as { __b2e: Ev[] };
    w.__b2e = [];
    const t0 = performance.now();
    const who = (el: Element | null) => (el === null ? null : (el.getAttribute('data-testid') ?? `${el.tagName}${el.className ? '.' + String(el.className).split(' ')[0] : ''}`));
    const push = (e: Ev) => w.__b2e.push({ t: Math.round(performance.now() - t0), ...e });
    const focus0 = HTMLElement.prototype.focus;
    HTMLElement.prototype.focus = function (opts?: FocusOptions) {
      push({ call: 'focus', target: who(this), preventScroll: opts?.preventScroll === true });
      return focus0.call(this, opts);
    };
    const siv0 = Element.prototype.scrollIntoView;
    Element.prototype.scrollIntoView = function (arg?: boolean | ScrollIntoViewOptions) {
      push({ call: 'scrollIntoView', target: who(this), arg: JSON.stringify(arg ?? null) });
      return siv0.call(this, arg);
    };
    for (const name of ['scrollTo', 'scrollBy'] as const) {
      const orig = Element.prototype[name] as (...a: unknown[]) => void;
      (Element.prototype as unknown as Record<string, unknown>)[name] = function (this: Element, ...a: unknown[]) {
        push({ call: name, target: who(this), arg: JSON.stringify(a) });
        return orig.apply(this, a);
      };
    }
    const stage = document.querySelector<HTMLElement>('[data-testid="di-scan-stage"]');
    stage?.addEventListener('scroll', () => push({ call: 'stage scroll', scrollTop: Math.round(stage.scrollTop), focusThen: who(document.activeElement) }), { passive: true });
    document.addEventListener('focusin', (e) => push({ call: 'focusin', target: who(e.target as Element) }), true);
  });

/** Everything, read after the page settles (two frames and a short wait). Visibility by HIT-TEST: what a person can see. */
const measure = () =>
  p().evaluate(async () => {
    await new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(() => r(null))));
    const q = (s: string) => document.querySelector<HTMLElement>(s);
    const who = (el: Element | null) => (el === null ? null : (el.getAttribute('data-testid') ?? el.tagName));
    const seen = (el: HTMLElement | null) => {
      if (el === null || !el.checkVisibility()) return { drawn: false, seen: 'not drawn', box: null };
      const b = el.getBoundingClientRect();
      let hits = 0;
      let tried = 0;
      for (const fx of [0.1, 0.3, 0.5, 0.7, 0.9])
        for (const fy of [0.2, 0.5, 0.8]) {
          const x = b.left + b.width * fx;
          const y = b.top + b.height * fy;
          if (x < 0 || y < 0 || x >= innerWidth || y >= innerHeight) {
            tried++;
            continue;
          }
          tried++;
          const at = document.elementFromPoint(x, y);
          if (at !== null && el.contains(at)) hits++;
        }
      return {
        drawn: true,
        seen: hits === 0 ? 'NOT SEEN' : hits === tried ? 'whole' : `part (${hits}/${tried} points)`,
        box: { left: Math.round(b.left), top: Math.round(b.top), width: Math.round(b.width), height: Math.round(b.height) },
      };
    };
    const stage = q('[data-testid="di-scan-stage"]');
    const active = document.activeElement as HTMLElement | null;
    return {
      scrollTop: stage ? Math.round(stage.scrollTop) : null,
      scrollHeight: stage?.scrollHeight ?? null,
      clientHeight: stage?.clientHeight ?? null,
      focus: { target: who(active), text: (active?.textContent ?? '').trim().slice(0, 60), ...seen(active === document.body ? null : active) },
      regions: {
        heading: seen(q('[data-testid="di-new-mission"] .di-page-head, [data-testid="di-new-mission"] h1')),
        closeControl: seen(q('[data-testid="di-new-close-conversation"]')),
        thread: seen(q('[data-testid="di-new-thread"]')),
        threadEmpty: seen(q('[data-testid="di-new-thread-empty"]')),
        leaveUnread: seen(q('[data-testid="di-new-leave-unread"]')),
        askBox: seen(q('[data-testid="di-new-ask"]')),
        recent: seen(q('[data-testid="di-new-recent"]')),
        pickedChip: seen(q('[data-testid="di-new-recent-ask"][aria-current="true"]')),
        sendBar: seen(q('[data-testid="di-new-send"]')),
      },
      events: (window as unknown as { __b2e?: unknown[] }).__b2e ?? [],
    };
  });

// ---- A. what scrolls the stage after a pick, in each of the four pick states ----
const STATES: Array<{ name: string; how: How; marker: string | null }> = [
  { name: 'read with turns', how: 'ok', marker: 'di-new-thread' },
  { name: 'read and empty', how: 'empty', marker: 'di-new-thread-empty' },
  { name: 'still being read', how: 'hold', marker: null },
  { name: 'not read', how: 'refused', marker: 'di-new-leave-unread' },
];
for (const vp of VIEWPORTS)
  for (const st of STATES)
    test(`A. AFTER THE PICK at ${vp.width}x${vp.height}, "${st.name}": the focus, the scroll and what is seen`, async () => {
      await boot(vp, [
        { id: A, ask: ASK_A },
        { id: B, ask: ASK_B },
      ]);
      how[B] = st.how;
      await installProbe();
      const before = await measure();
      await chipFor(B).click();
      if (st.marker !== null) await p().waitForSelector(sel(st.marker), { timeout: 20_000 });
      await p().waitForTimeout(500);
      const after = await measure();
      await shot(`b2em2-A-${vp.width}x${vp.height}-${st.how}.png`);
      record(`A ${vp.width}x${vp.height} ${st.name}`, { before: { scrollTop: before.scrollTop }, after });
      // STATES only
      assert.equal(await p().locator(sel('di-scan-panel')).getAttribute('data-view'), 'new', 'STATE: New Mission is open');
      assert.equal(await chipFor(B).getAttribute('aria-current'), 'true', `STATE: B is picked (${st.name})`);
      if (st.marker !== null) assert.equal(after.regions[st.marker === 'di-new-thread' ? 'thread' : st.marker === 'di-new-thread-empty' ? 'threadEmpty' : 'leaveUnread'].drawn, true, `STATE: ${st.marker} is drawn`);
    }, STEPS_MS);

// ---- B + C. a longer Recent, and what Recent would need as a pane ----
for (const vp of VIEWPORTS)
  test(`B+C. A LONGER RECENT at ${vp.width}x${vp.height}: twelve asks up to the 120-character cap, one picked; and the widths a Recent pane would need`, async () => {
    await boot(vp, LONG);
    const pick = LONG[6];
    how[pick.id] = 'ok';
    await chipFor(pick.id).click();
    await p().waitForSelector(sel('di-new-thread'), { timeout: 20_000 });
    await p().waitForTimeout(500);
    const after = await measure();
    const widths = await p().evaluate(() => {
      const intrinsic = (el: HTMLElement, mode: 'max-content' | 'min-content') => {
        const was = el.style.cssText;
        el.style.setProperty('width', mode, 'important');
        el.style.setProperty('flex', 'none', 'important');
        const w = Math.round(el.getBoundingClientRect().width);
        el.style.cssText = was;
        return w;
      };
      const chips = [...document.querySelectorAll<HTMLElement>('[data-testid="di-new-recent-ask"]')];
      const rowsTop = new Set(chips.map((c) => Math.round(c.getBoundingClientRect().top)));
      const pageEl = document.querySelector<HTMLElement>('[data-testid="di-new-mission"]')!;
      const thread = document.querySelector<HTMLElement>('[data-testid="di-new-thread"]')!;
      const stage = document.querySelector<HTMLElement>('[data-testid="di-scan-stage"]')!;
      const pcs = getComputedStyle(pageEl);
      const scs = getComputedStyle(stage);
      return {
        chips: chips.map((c) => ({
          chars: (c.textContent ?? '').trim().length,
          drawnWidth: Math.round(c.getBoundingClientRect().width),
          drawnHeight: Math.round(c.getBoundingClientRect().height),
          textOverflowsBoxX: c.scrollWidth > c.clientWidth + 1,
          textOverflowsBoxY: c.scrollHeight > c.clientHeight + 1,
          oneLineWidth: intrinsic(c, 'max-content'),
          longestWordWidth: intrinsic(c, 'min-content'),
        })),
        recentRows: rowsTop.size,
        recentBox: (() => {
          const b = document.querySelector<HTMLElement>('[data-testid="di-new-recent"]')!.getBoundingClientRect();
          return { width: Math.round(b.width), height: Math.round(b.height) };
        })(),
        threadMinContent: intrinsic(thread, 'min-content'),
        threadMaxContent: intrinsic(thread, 'max-content'),
        threadDrawnWidth: Math.round(thread.getBoundingClientRect().width),
        page: { width: Math.round(pageEl.getBoundingClientRect().width), maxWidth: pcs.maxWidth, paddingLeft: pcs.paddingLeft, paddingRight: pcs.paddingRight, marginLeft: pcs.marginLeft },
        stage: { width: Math.round(stage.getBoundingClientRect().width), paddingLeft: scs.paddingLeft, paddingRight: scs.paddingRight, contentWidth: stage.clientWidth - parseFloat(scs.paddingLeft) - parseFloat(scs.paddingRight) },
      };
    });
    await shot(`b2em2-B-${vp.width}x${vp.height}-first-view.png`);
    await p().locator(sel('di-new-recent')).scrollIntoViewIfNeeded();
    await p().waitForTimeout(200);
    const atRecent = await measure();
    await shot(`b2em2-B-${vp.width}x${vp.height}-at-recent.png`);
    record(`B ${vp.width}x${vp.height}`, { after, atRecent: { regions: { recent: atRecent.regions.recent, pickedChip: atRecent.regions.pickedChip, thread: atRecent.regions.thread } }, widths });
    assert.equal(await p().locator(sel('di-scan-panel')).getAttribute('data-view'), 'new', 'STATE: New Mission is open');
    assert.equal(widths.chips.length, LONG.length, `STATE: all ${LONG.length} asks are drawn in Recent`);
    assert.equal(await chipFor(pick.id).getAttribute('aria-current'), 'true', 'STATE: the seventh ask is picked');
    assert.equal(Math.max(...widths.chips.map((c) => c.chars)), FIRST_WORDS_CHARS, 'STATE: the longest ask is exactly the panel cap');
  }, STEPS_MS);
