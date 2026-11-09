/**
 * B2e MEASUREMENT 5 (the Lead's S1: the sticky-head defect ATTRIBUTED by measurement, then fixed either way). Where the PAGE is the scroller (760x700: the
 * stacked layout on the live bytes, the one column on the pre-split bytes), a FRESH SEND from the blank page, the scroller OVERFLOWING (the person drags the
 * ask box to the scroller's height, CASE (d)'s instrument) and scrolled down. Recorded: whether scrollIntoView was called, where the first turn landed, and
 * the page's sticky head. The SAME file runs on both trees (no test id the split added). It records; it asserts only states. Not part of the product.
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
const SHOTS = process.env.DI_B2EM5_SHOTS ?? null;
/** The ruled allocation (the Lead's). */
/** The panel's own cap on first words (mission_panel/service.py:70). */
const FIRST_WORDS_CHARS = 120;
const LONG: Array<{ id: string; ask: string }> = [16, 28, 40, 52, 64, 76, 88, 96, 104, 112, 118, 120].map((n, i) => {
  const base = `SYNTHETIC ask ${String(i + 1).padStart(2, '0')}: make the readiness probe stable under load and report what changed in the subsystem`;
  const cut = base.padEnd(FIRST_WORDS_CHARS, ' and its tests').slice(0, n);
  return { id: `cv-${'0123456789ef'[i].repeat(32)}`, ask: cut.endsWith(' ') ? `${cut.slice(0, -1)}s` : cut };
});

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
    console.warn(`SKIPPED (B2e measurement 5): ${unavailable}`);
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
const record = (name: string, r: unknown) => console.log(`B2EM5 ${name}: ${JSON.stringify(r)}`);

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
const probed = () => p().evaluate(() => (window as unknown as { __siv: string[] }).__siv);

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


test('STICKY HEAD at 760x700, the page as the scroller: a FRESH SEND from the blank page, the scroller overflowing and scrolled down; where the first turn lands against the sticky head', async () => {
  await boot({ width: 760, height: 700 });
  await probe();
  const SCROLLER = '[data-testid="di-scan-stage"]';
  await p().locator(sel('di-new-ask')).fill('SYNTHETIC a first ask, sent from the blank page');
  const box = await p().evaluate((s) => {
    const ta = document.querySelector<HTMLTextAreaElement>('[data-testid="di-new-ask"]')!;
    ta.style.height = `${document.querySelector<HTMLElement>(s)!.clientHeight}px`;
    return { resize: getComputedStyle(ta).resize, height: Math.round(ta.getBoundingClientRect().height) };
  }, SCROLLER);
  await p().waitForTimeout(200);
  await p().evaluate((s) => {
    const m = document.querySelector<HTMLElement>(s)!;
    m.scrollTop = m.scrollHeight;
  }, SCROLLER);
  await p().waitForTimeout(200);
  const pre = await p().evaluate((s) => {
    const m = document.querySelector<HTMLElement>(s)!;
    return { turnsDrawn: document.querySelectorAll('.di-new-turns > li').length, scrollHeight: m.scrollHeight, clientHeight: m.clientHeight, scrollTop: Math.round(m.scrollTop), scrollPaddingTop: getComputedStyle(m).scrollPaddingTop };
  }, SCROLLER);
  await probe();
  await p().click(sel('di-new-send'));
  await p().waitForFunction(() => document.querySelectorAll('.di-new-turns > li').length >= 1, undefined, { timeout: 20_000 });
  await p().waitForTimeout(400);
  const calls = await probed();
  const landed = await p().evaluate((s) => {
    const m = document.querySelector<HTMLElement>(s)!.getBoundingClientRect();
    const last = [...document.querySelectorAll<HTMLElement>('.di-new-turns > li')].pop()!.getBoundingClientRect();
    const headEl = document.querySelector<HTMLElement>('[data-testid="di-new-mission"] .di-page-head');
    const head = headEl?.getBoundingClientRect() ?? null;
    const headSticky = headEl ? getComputedStyle(headEl).position : null;
    return {
      newest: { top: Math.round(last.top), bottom: Math.round(last.bottom) },
      head: head === null ? null : { top: Math.round(head.top), bottom: Math.round(head.bottom), position: headSticky },
      scroller: { top: Math.round(m.top), bottom: Math.round(m.bottom) },
      underHeadPx: head === null ? 0 : Math.max(0, Math.round(head.bottom - last.top)),
      clearOfHead: head === null || last.top >= head.bottom - 0.5,
      inScroller: last.top >= m.top - 0.5 && last.bottom <= m.bottom + 0.5,
    };
  }, SCROLLER);
  await shot('b2em5-760x700-fresh-send.png');
  record('STICKY 760x700', { box, pre, calls, landed });
  assert.equal(pre.turnsDrawn, 0, 'STATE: nothing was drawn before the send (the blank page)');
  assert.ok(pre.scrollHeight > pre.clientHeight + 40 && pre.scrollTop > 0, `STATE: the page scroller OVERFLOWS and is scrolled down (${JSON.stringify(pre)})`);
  assert.ok(landed.head !== null && landed.head.position === 'sticky', 'STATE: the page has a sticky head');
}, STEPS_MS);
