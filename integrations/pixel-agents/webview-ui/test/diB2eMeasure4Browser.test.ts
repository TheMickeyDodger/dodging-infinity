/**
 * B2e MEASUREMENT 4 (the Lead's attribution standard: measured, not inferred): the Tab walk STOP BY STOP on New Mission, with the request workspace holding the
 * ask (opened from "Review requests" with an ask typed, a Recent ask picked) and with it closed, at 1280x800, 1440x900 and 1600x1000. Each stop: its test id,
 * its conversation, its x, and whether focus there is SEEN by the CALIBRATED exposure helper's own focus entry (containment along the clip chain plus
 * hit-tests). The SAME file runs on the live bytes and on the PRE-SPLIT export, so each unseen stop can be attributed. It uses no test id the split added.
 * It records; it asserts only states. Not part of the product. Setup (relay, boot, the helper) is the split test's own (diB2eSplitBrowser.test.ts).
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
  syntheticConversation,
  syntheticConversationSummary,
  syntheticDraftedTurn,
  syntheticOverview,
  syntheticTurn,
} from './diNewMissionSynthetic.ts';

const STEPS_MS = 120_000;
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const office = availableCapacityOffice(
  (JSON.parse(fs.readFileSync(path.join(root, 'test/fixtures/di-office-state-session-mismatch-v2.FIXTURE.json'), 'utf-8')) as { state: DiOfficeState }).state,
);
const VERSION = (JSON.parse(fs.readFileSync(path.join(root, '..', 'package.json'), 'utf-8')) as { version: string }).version;
const SHOTS = process.env.DI_B2EM4_SHOTS ?? null;
/** The ruled allocation (the Lead's). */
/** The panel's own cap on first words (mission_panel/service.py:70). */
const FIRST_WORDS_CHARS = 120;
const LONG: Array<{ id: string; ask: string }> = [16, 28, 40, 52, 64, 76, 88, 96, 104, 112, 118, 120].map((n, i) => {
  const base = `SYNTHETIC ask ${String(i + 1).padStart(2, '0')}: make the readiness probe stable under load and report what changed in the subsystem`;
  const cut = base.padEnd(FIRST_WORDS_CHARS, ' and its tests').slice(0, n);
  return { id: `cv-${'0123456789ef'[i].repeat(32)}`, ask: cut.endsWith(' ') ? `${cut.slice(0, -1)}s` : cut };
});
const PICK = LONG[6];

/** Each conversation's turns as the synthetic panel holds them: a read returns them all, a turn sent joins them. */
let turnsOf: Record<string, Array<ReturnType<typeof syntheticTurn>>> = {};
/** Every request the page sent, in order (to show that a deselection reads nothing again and sends nothing). */
let sent: Array<{ op: string; conversationId: string | null }> = [];
/** B2e correction: the conversations whose reads the synthetic panel REFUSES (the states test's own refusal reply). */
let refuse = new Set<string>();
/** The conversation a fresh send (from the blank page) starts. */
const FRESH = `cv-${'d'.repeat(32)}`;
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
        sent.push({ op: String(msg.op), conversationId: msg.conversationId === undefined || msg.conversationId === null ? null : String(msg.conversationId) });
        const send = (m: Record<string, unknown>) => ws.send(JSON.stringify(m));
        if (msg.op === 'refresh') send(relayReply(msg.seq, 'refresh', syntheticOverview(LONG.map((c) => syntheticConversationSummary(c.id, c.ask)))));
        if (msg.op === 'conversation') {
          const id = String(msg.conversationId);
          if (refuse.has(id)) {
            send({ type: 'diMissionRequestReply', seq: msg.seq, op: 'conversation', state: 'refused', problem: 'mission_panel_conversation_unreadable', reason: 'SYNTHETIC: that conversation could not be read.', answer: null });
            return;
          }
          if (turnsOf[id] === undefined) turnsOf[id] = [syntheticDraftedTurn(LONG.find((c) => c.id === id)?.ask ?? 'SYNTHETIC')];
          send(relayReply(msg.seq, 'conversation', syntheticConversation(turnsOf[id], id)));
        }
        if (msg.op === 'turn') {
          const into = msg.conversationId === undefined || msg.conversationId === null ? FRESH : String(msg.conversationId);
          const turn = syntheticTurn(String(msg.text), { conversation_id: into, turn_id: `ct-${'7'.repeat(32)}`, sequence: (turnsOf[into]?.length ?? 0) + 1 });
          turnsOf[into] = [...(turnsOf[into] ?? []), turn];
          send(relayReply(msg.seq, 'turn', { ok: true, turn }));
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
    console.warn(`SKIPPED (B2e measurement 4): ${unavailable}`);
    ctx.skip();
    return;
  }
  await page?.close();
  page = null;
  turnsOf = {};
  sent = [];
  refuse = new Set();
}, STEPS_MS);

const p = () => page!;
const sel = (id: string) => `[data-testid="${id}"]`;
const chipFor = (id: string) => p().locator(`[data-testid="di-new-recent-ask"][data-conversation="${id}"]`);
const record = (name: string, r: unknown) => console.log(`B2EM4 ${name}: ${JSON.stringify(r)}`);

async function shot(name: string): Promise<void> {
  if (SHOTS === null) return;
  const file = path.join(SHOTS, name);
  assert.ok(!fs.existsSync(file), `refusing to overwrite ${file}`);
  await p().screenshot({ path: file });
}

/** The PASS-THROUGH probe (measurement only: each call goes on to the original): every scrollIntoView from now on, by target. */
const probe = () =>
  p().evaluate(() => {
    const w = window as unknown as { __siv: string[]; __sivInstalled?: boolean };
    w.__siv = [];
    if (w.__sivInstalled) return;
    w.__sivInstalled = true;
    const orig = Element.prototype.scrollIntoView;
    Element.prototype.scrollIntoView = function (arg?: boolean | ScrollIntoViewOptions) {
      w.__siv.push(this.getAttribute('data-testid') ?? this.className);
      return orig.call(this, arg);
    };
  });

async function boot(viewport: { width: number; height: number }): Promise<void> {
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
  for (const c of LONG) await chipFor(c.id).waitFor({ timeout: 20_000 });
}
async function bootPicked(viewport: { width: number; height: number }): Promise<void> {
  await boot(viewport);
  await probe();
  await chipFor(PICK.id).click();
  await p().waitForSelector(sel('di-new-thread'), { timeout: 20_000 });
  await p().waitForTimeout(400);
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


/** Every Tab stop from the start of New Mission until focus leaves it: id, conversation, x, and SEEN (the calibrated helper's focus entry). */
async function walk(max = 120): Promise<Array<{ id: string | null; conversation: string | null; left: number | null; seen: { ok: boolean; why: string } }>> {
  await p().evaluate(() => {
    const page = document.querySelector<HTMLElement>('[data-testid="di-new-mission"]')!;
    page.setAttribute('tabindex', '-1');
    page.focus();
  });
  const stops: Array<{ id: string | null; conversation: string | null; left: number | null; seen: { ok: boolean; why: string } }> = [];
  for (let i = 0; i < max; i += 1) {
    await p().keyboard.press('Tab');
    const step = await p().evaluate(() => {
      const a = document.activeElement as HTMLElement | null;
      const inPage = a !== null && a !== document.body && a.closest('[data-testid="di-new-mission"]') !== null;
      return {
        inPage,
        id: a?.getAttribute('data-testid') ?? a?.tagName ?? null,
        conversation: a?.getAttribute('data-conversation') ?? null,
        left: a === null ? null : Math.round(a.getBoundingClientRect().left),
      };
    });
    if (!step.inPage) break;
    stops.push({ id: step.id, conversation: step.conversation, left: step.left, seen: (await exposure({})).exposed.focus });
  }
  await p().evaluate(() => document.querySelector('[data-testid="di-new-mission"]')?.removeAttribute('tabindex'));
  return stops;
}

for (const vp of [
  { width: 1280, height: 800 },
  { width: 1440, height: 900 },
  { width: 1600, height: 1000 },
])
  test(`WALK at ${vp.width}x${vp.height}: every Tab stop of New Mission, SEEN or not, with the request workspace CLOSED and then HOLDING THE ASK (a Recent ask picked)`, async () => {
    await bootPicked(vp);
    const closed = await walk();
    await p().locator(sel('di-new-ask')).fill('SYNTHETIC an ask typed before the request workspace opens');
    await p().click(sel('di-new-open-workspace'));
    await p().waitForSelector(sel('di-new-ask-bring-back'), { timeout: 20_000 });
    await p().waitForTimeout(400);
    const held = await walk();
    // the isolation, as rendered (absent on trees without it): what was marked while the workspace holds the ask
    const isolated = await p().evaluate(() =>
      [...document.querySelectorAll<HTMLElement>('[data-di-covered]')].map((el) => ({ id: el.getAttribute('data-testid') ?? el.tagName, conversation: el.getAttribute('data-conversation') })),
    );
    await shot(`b2em4-${vp.width}x${vp.height}-held.png`);
    record(`WALK ${vp.width}x${vp.height}`, { closed, held, isolated });
    assert.ok(closed.length > 0 && held.length > 0, 'STATE: both walks reached New Mission');
    assert.equal(await p().locator(sel('di-mr')).count(), 1, 'STATE: the request workspace is open for the held walk');
  }, STEPS_MS);
