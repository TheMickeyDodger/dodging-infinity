/**
 * B2e, MEASUREMENT 3 (read-only DIAGNOSIS of the regression set's F4 failure): the request workspace opened from New Mission ("Review requests"), then where
 * "Bring the ask back here" is drawn and what covers it, by the CALIBRATED exposure helper of diB2eSplitBrowser (containment along the clip chain plus
 * hit-tests; calibrated against ground truth there). It records boxes; it asserts only states. Not part of the product. Origin header of measurement 2:
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
const SHOTS = process.env.DI_B2EM3_SHOTS ?? null;

const A = SYNTHETIC_CONVERSATION_ID;
const B = SYNTHETIC_OTHER_CONVERSATION_ID;
const ASK_A = 'SYNTHETIC recent ask A: the conversation that reads';
const ASK_B = 'SYNTHETIC recent ask B: the conversation picked in each state';

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
    console.warn(`SKIPPED (B2e measurement 3): ${unavailable}`);
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
const record = (name: string, r: unknown) => console.log(`B2EM3 ${name}: ${JSON.stringify(r)}`);

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

/**
 * CONTAINMENT, at the level of the claim: the element's whole box lies inside the viewport AND inside every ancestor IN ITS CLIP CHAIN (overflow other
 * than visible: its padding box), AND elementFromPoint at its centre, at points 1px inside its four corners and at its four edge midpoints lands inside it.
 * THE MODEL (split run 1's defect corrected: it intersected EVERY overflow ancestor): a position: fixed box is clipped by the viewport and by an ancestor
 * that establishes its containing block (transform, perspective, filter, contain: paint / layout / strict / content, a size container, or will-change of
 * those), never by an ordinary scrolling ancestor above it; an absolute box escapes non-positioned ancestors the same way.
 * ITS BOUND: clip-path, masks, and an overlay covering only an element's interior between the nine points are not modelled. A rectangular clip is convex,
 * so a box whose four corners (inset by 1 + 0.3 x its corner radius, to stay inside a rounded corner) and centre are hit is not clipped by one. The
 * containing-block list is CALIBRATED in Chrome 156 (a size container is NOT one there), by the first test below; another engine may differ.
 */
const exposure = (selectors: Record<string, string>) =>
  p().evaluate(async (sels) => {
    await new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(() => r(null))));
    // an ancestor establishes the containing block of a position: fixed descendant (and of an absolute one) when it is transformed, filtered, contained
    // or a size container; otherwise a fixed box ESCAPES its overflow clip (and an absolute box escapes non-positioned ancestors)
    // CALIBRATED (split run 2's ground truth, Chrome 156): a SIZE CONTAINER (container-type: inline-size) is NOT a fixed child's containing block in
    // Chrome (a fixed child of one sat at its viewport position and was hit there), so container-type is not in this list
    const fixedCB = (cs: CSSStyleDeclaration) =>
      cs.transform !== 'none' || cs.perspective !== 'none' || cs.filter !== 'none' || /paint|layout|strict|content/.test(cs.contain) ||
      /transform|perspective|filter/.test(cs.willChange);
    const contained = (el: HTMLElement) => {
      const b = el.getBoundingClientRect();
      if (b.width === 0 || b.height === 0) return { ok: false, why: 'no box' };
      if (b.left < -0.5 || b.top < -0.5 || b.right > innerWidth + 0.5 || b.bottom > innerHeight + 0.5) return { ok: false, why: 'outside the viewport' };
      let mode = getComputedStyle(el).position;
      for (let a = el.parentElement; a !== null; a = a.parentElement) {
        const cs = getComputedStyle(a);
        // walk the CONTAINING-BLOCK chain: skip the ancestors a fixed (or absolute) box escapes
        if (mode === 'fixed' && !fixedCB(cs)) continue;
        if (mode === 'absolute' && cs.position === 'static' && !fixedCB(cs)) continue;
        mode = cs.position === 'fixed' || cs.position === 'absolute' ? cs.position : 'static';
        if (cs.overflowX === 'visible' && cs.overflowY === 'visible') continue;
        const ab = a.getBoundingClientRect();
        const left = ab.left + a.clientLeft;
        const top = ab.top + a.clientTop;
        if (b.left < left - 0.5 || b.top < top - 0.5 || b.right > left + a.clientWidth + 0.5 || b.bottom > top + a.clientHeight + 0.5)
          return { ok: false, why: `clipped by ${a.getAttribute('data-testid') ?? a.className}` };
      }
      // corner points stay inside a ROUNDED corner (split run 2: a 1px inset fell outside a button's radius and hit its parent): inset by 1 + 0.3 r
      const ecs = getComputedStyle(el);
      const radius = Math.max(
        ...[ecs.borderTopLeftRadius, ecs.borderTopRightRadius, ecs.borderBottomLeftRadius, ecs.borderBottomRightRadius].map((v) =>
          v.endsWith('%') ? (parseFloat(v) / 100) * Math.min(b.width, b.height) : parseFloat(v) || 0,
        ),
      );
      const k = Math.min(1 + 0.3 * radius, b.width / 2 - 0.5, b.height / 2 - 0.5);
      const pts = [
        [b.left + b.width / 2, b.top + b.height / 2],
        [b.left + k, b.top + k],
        [b.right - k, b.top + k],
        [b.left + k, b.bottom - k],
        [b.right - k, b.bottom - k],
        [b.left + b.width / 2, b.top + 1],
        [b.left + b.width / 2, b.bottom - 1],
        [b.left + 1, b.top + b.height / 2],
        [b.right - 1, b.top + b.height / 2],
      ];
      for (const [x, y] of pts) {
        const at = document.elementFromPoint(x, y);
        if (at === null || !el.contains(at)) return { ok: false, why: `covered at (${Math.round(x)},${Math.round(y)}) by ${at?.getAttribute('data-testid') ?? at?.className ?? 'nothing'}` };
      }
      return { ok: true, why: '' };
    };
    const out: Record<string, { ok: boolean; why: string }> = {};
    for (const [name, s] of Object.entries(sels)) {
      const el = document.querySelector<HTMLElement>(s);
      out[name] = el === null ? { ok: false, why: 'not drawn' } : contained(el);
    }
    const active = document.activeElement as HTMLElement | null;
    out.focus = active === null || active === document.body ? { ok: false, why: 'focus on the page body' } : contained(active);
    return { exposed: out, focusTarget: active?.getAttribute('data-testid') ?? active?.tagName ?? null, focusConversation: active?.getAttribute('data-conversation') ?? null };
  }, selectors);


for (const vp of [
  { width: 1280, height: 800 },
  { width: 1440, height: 900 },
  { width: 1600, height: 1000 },
])
  test(`F4 DIAGNOSIS at ${vp.width}x${vp.height}: the request workspace open from New Mission; where "Bring the ask back here" is, and what covers it`, async () => {
    await boot(vp, [
      { id: A, ask: ASK_A },
      { id: B, ask: ASK_B },
    ]);
    await p().locator(sel('di-new-ask')).fill('SYNTHETIC an ask typed before the workspace opens');
    await p().click(sel('di-new-open-workspace'));
    await p().waitForSelector(sel('di-mr'), { timeout: 20_000 });
    await p().waitForTimeout(500);
    const boxes = await p().evaluate(() => {
      const box = (s: string) => {
        const el = document.querySelector<HTMLElement>(s);
        if (el === null) return null;
        const b = el.getBoundingClientRect();
        return { left: Math.round(b.left), top: Math.round(b.top), right: Math.round(b.right), bottom: Math.round(b.bottom), width: Math.round(b.width) };
      };
      const mr = document.querySelector<HTMLElement>('[data-testid="di-mr"]');
      return {
        mr: box('[data-testid="di-mr"]'),
        mrAttrs: mr ? { covering: mr.getAttribute('data-covering'), wide: mr.getAttribute('data-wide'), split: mr.getAttribute('data-split'), position: getComputedStyle(mr).position } : null,
        stage: box('[data-testid="di-scan-stage"]'),
        page: box('[data-testid="di-new-mission"]'),
        main: box('[data-testid="di-new-main"]'),
        side: box('[data-testid="di-new-side"]'),
        bringBack: box('[data-testid="di-new-ask-bring-back"]'),
        elsewhereNote: box('[data-testid="di-new-ask-in-workspace"]'),
        missionList: box('[data-testid="di-missions"]'),
      };
    });
    const ex = await exposure({ bringBack: '[data-testid="di-new-ask-bring-back"]', elsewhereNote: '[data-testid="di-new-ask-in-workspace"]', side: '[data-testid="di-new-side"]', send: '[data-testid="di-new-send"]' });
    await shot(`b2em3-${vp.width}x${vp.height}-workspace-open.png`);
    record(`F4 ${vp.width}x${vp.height}`, { boxes, exposed: ex.exposed });
    assert.notEqual(boxes.mr, null, 'STATE: the request workspace is open');
  }, STEPS_MS);
