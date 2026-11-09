/**
 * B2e, THE CONTRACT of New Mission's two panes (Direction B, the selected handoff) and its three defects, ASSERTED (the Lead's ruled outcomes:
 * rule 9's boundary), with ONE batched inspection (captures). Chrome, headless; not Brave evidence. The real App composition (di-app-scan.html) over the
 * session-mismatch v2 fixture in its available-capacity variation, the request panel answered by a LABELLED SYNTHETIC relay (diNewMissionSynthetic).
 *
 * At 1280x800 (narrow, one pane: the Mission list covered), 1280x720, 1440x900, 1600x1000, and the existing narrow 900x700 / 900x800:
 *  - THE SPLIT, as ruled: the Recent pane 187px on the LEFT, the 24px gap, the conversation taking the remainder (536px at 1440: 160px less than the old
 *    696px column; 696px at 1600; 804px at 1280; all with NOTHING minimized: a chip in the strip takes 44px of the screen, B3);
 *  - NAVIGATION AND EXITS EXPOSED, by FULL VIEWPORT AND ANCESTOR-CLIP CONTAINMENT plus HIT-TESTING (never sampled points): the title, "Close this
 *    conversation", the picked chip, Send, and what has focus. At the first draw after the pick, and with the conversation scrolled to its END;
 *  - NO SELF-SCROLL at the first draw (defect 2); FOCUS on something SEEN (defect 1); the pick DRAWN; every chip holding its words (defect 3).
 * RULE 7 on the effect fix: a turn ARRIVING in the conversation drawn must still come into view (contained in the conversation's pane).
 * RULE 7 on the fallback: under a 604px stage (760x700: a 560px stage) the panes STACK and the page scrolls as one column.
 * The conversation's own overflow is expected and is never a failure. Recorded "B2ES ..."; captures (never overwritten) go to DI_B2ES_SHOTS.
 * THE NEWEST-TURN EFFECT, its THREE cases each asserted SEPARATELY by a PASS-THROUGH scrollIntoView PROBE (the Supervisor's source check): (a) a FRESH
 * SEND from the blank page comes into view; (b) a LATER ARRIVAL in the conversation drawn comes into view; (c) the FIRST DRAW of a read conversation (a
 * pick) and a SWITCH to another do NOT move the page. A fix that silenced (a) would fail here, not pass.
 * DESELECTION: the selected ask, clicked again, CLOSES the conversation by the same transition as "Close this conversation": the blank start, the typed
 * ask kept EXACTLY, focus in the box, nothing read again and nothing sent (D1's retention).
 * CONTAINMENT follows the CONTAINING-BLOCK chain (split run 1's instrument defect, mine: the stage is position: fixed, so an ancestor that does not
 * establish its containing block does not clip it), and is backed by hit-tests at the corners and edge midpoints.
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
import { contrastRatio } from '../src/components/diCodeReviewModel.ts';
import { COVERS_STAGE_WORDS } from '../src/components/diMissionRequestModel.ts';
import { DI_NEW_READ_AGAIN } from '../src/components/diScanPagesModel.ts';
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
const SHOTS = process.env.DI_B2ES_SHOTS ?? null;
/** The ruled allocation (the Lead's). */
const PANE_PX = 187;
const GAP_PX = 24;
const PAGE_PADDING_PX = 64;
const OLD_COLUMN_PX = 696;
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
    console.warn(`SKIPPED (B2e split): ${unavailable}`);
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
const record = (name: string, r: unknown) => console.log(`B2ES ${name}: ${JSON.stringify(r)}`);

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

const EXPOSED = {
  title: '[data-testid="di-new-mission"] .di-page-head',
  closeConversation: '[data-testid="di-new-close-conversation"]',
  pickedChip: '[data-testid="di-new-recent-ask"][aria-current="true"]',
  send: '[data-testid="di-new-send"]',
};

const geometry = () =>
  p().evaluate(() => {
    const q = (s: string) => document.querySelector<HTMLElement>(s);
    const box = (el: HTMLElement | null) => (el ? (({ left, top, width, height }) => ({ left: Math.round(left), top: Math.round(top), width: Math.round(width), height: Math.round(height) }))(el.getBoundingClientRect()) : null);
    const stage = q('[data-testid="di-scan-stage"]')!;
    const main = q('[data-testid="di-new-main-scroll"]')!;
    const chips = [...document.querySelectorAll<HTMLElement>('[data-testid="di-new-recent-ask"]')];
    const picked = q('[data-testid="di-new-recent-ask"][aria-current="true"]');
    const other = chips.find((c) => c.getAttribute('aria-current') !== 'true') ?? null;
    const style = (el: HTMLElement | null) => (el ? { background: getComputedStyle(el).backgroundColor, border: getComputedStyle(el).borderTopColor } : null);
    return {
      stageContentWidth: stage.clientWidth,
      stageScrollTop: Math.round(stage.scrollTop),
      stageScrolls: stage.scrollHeight > stage.clientHeight + 1,
      side: box(q('[data-testid="di-new-side"]')),
      main: box(q('[data-testid="di-new-main"]')),
      mainScroll: { scrollTop: Math.round(main.scrollTop), scrollHeight: main.scrollHeight, clientHeight: main.clientHeight },
      pickStyle: style(picked),
      otherStyle: style(other),
      chipsSpilling: chips.filter((c) => c.scrollHeight > c.clientHeight + 1 || c.scrollWidth > c.clientWidth + 1).map((c) => (c.textContent ?? '').trim().length),
      chipCount: chips.length,
    };
  });

test('CALIBRATION of the exposure helper, GROUND TRUTH FIRST (at 1440x900): a VISIBLE fixed child of an overflow ancestor PASSES; a clipped child, a fixed child clipped by its transformed containing block, and an occluded element FAIL; the real title PASSES', async () => {
  await boot({ width: 1440, height: 900 });
  // measurement-only controls, no colours (hit-testing does not need them); appended to the live page, never part of the product
  await p().evaluate(() => {
    const add = (parent: Element, css: string, id: string) => {
      const d = document.createElement('div');
      d.setAttribute('data-testid', id);
      d.style.cssText = css;
      parent.append(d);
      return d;
    };
    const panel = document.querySelector('[data-testid="di-office-panel"]') ?? document.body;
    // POSITIVE: a FIXED child of the office panel (the very overflow ancestor run 1 wrongly blamed), drawn in plain view
    add(panel, 'position:fixed;left:300px;top:300px;width:40px;height:20px;z-index:2147483646', 'cal-pos-fixed');
    // POSITIVE: a ROUNDED element in plain view (its corners must not read as covered)
    add(document.body, 'position:fixed;left:300px;top:340px;width:60px;height:28px;border-radius:12px;z-index:2147483646', 'cal-pos-rounded');
    // NEGATIVE 1: an ordinary child of an overflow:hidden box, placed outside it
    const box = add(document.body, 'position:fixed;left:400px;top:400px;width:50px;height:50px;overflow:hidden;z-index:2147483646', 'cal-box');
    add(box, 'position:relative;left:60px;width:20px;height:20px', 'cal-neg-clipped');
    // NEGATIVE 2: a FIXED child of a TRANSFORMED overflow:hidden box (the box is its containing block), placed outside it
    const tbox = add(document.body, 'position:fixed;left:500px;top:400px;width:50px;height:50px;overflow:hidden;transform:translateZ(0);z-index:2147483646', 'cal-tbox');
    add(tbox, 'position:fixed;left:60px;top:0;width:20px;height:20px', 'cal-neg-fixed-in-transform');
    // NEGATIVE 3: an element OCCLUDED by another drawn over it
    add(document.body, 'position:fixed;left:600px;top:400px;width:40px;height:20px;z-index:2147483645', 'cal-neg-occluded');
    add(document.body, 'position:fixed;left:595px;top:395px;width:60px;height:40px;z-index:2147483646', 'cal-cover');
    // GROUND TRUTH for one property the model must get right: a FIXED child of a SIZE CONTAINER (container-type: inline-size) with overflow:hidden. If the
    // container were its containing block, the child would sit at x 760 (the box's 700 + 60) OUTSIDE the box and be clipped; if not, it sits at the
    // viewport's (60, 470) in plain view. The hit-test below decides which, and the model must agree with it.
    const cbox = add(document.body, 'position:fixed;left:700px;top:400px;width:50px;height:50px;overflow:hidden;container-type:inline-size;z-index:2147483646', 'cal-cbox');
    add(cbox, 'position:fixed;left:60px;top:470px;width:20px;height:20px;z-index:2147483646', 'cal-fixed-in-size-container');
  });
  const hitTruth = await p().evaluate(() => {
    const el = document.querySelector<HTMLElement>('[data-testid="cal-fixed-in-size-container"]')!;
    const b = el.getBoundingClientRect();
    const at = document.elementFromPoint(b.left + b.width / 2, b.top + b.height / 2);
    return { left: Math.round(b.left), top: Math.round(b.top), hitItself: at !== null && el.contains(at) };
  });
  // the title's ancestor chain, every property the model reads (to name the ancestor it treats as a containing block)
  const chain = await p().evaluate(() => {
    const out: Array<Record<string, string>> = [];
    for (let a = document.querySelector<HTMLElement>('[data-testid="di-new-mission"] .di-page-head'); a !== null; a = a.parentElement) {
      const cs = getComputedStyle(a);
      out.push({ el: a.getAttribute('data-testid') ?? `${a.tagName}.${String(a.className).split(' ')[0]}`, position: cs.position, overflow: `${cs.overflowX}/${cs.overflowY}`, containerType: cs.containerType, transform: cs.transform, filter: cs.filter, contain: cs.contain, willChange: cs.willChange });
    }
    return out;
  });
  const r = await exposure({
    posFixed: sel('cal-pos-fixed'),
    posRounded: sel('cal-pos-rounded'),
    negClipped: sel('cal-neg-clipped'),
    negFixedInTransform: sel('cal-neg-fixed-in-transform'),
    negOccluded: sel('cal-neg-occluded'),
    realTitle: EXPOSED.title,
    fixedInSizeContainer: sel('cal-fixed-in-size-container'),
  });
  record('calibration', { exposed: r.exposed, hitTruth, chain });
  assert.equal(r.exposed.fixedInSizeContainer.ok, hitTruth.hitItself, `CALIBRATION: on a size container the model AGREES with the browser's hit-test (model ${r.exposed.fixedInSizeContainer.ok}, browser ${hitTruth.hitItself})`);
  assert.equal(r.exposed.posFixed.ok, true, `CALIBRATION: a VISIBLE fixed child of an overflow ancestor PASSES (${r.exposed.posFixed.why})`);
  assert.equal(r.exposed.posRounded.ok, true, `CALIBRATION: a ROUNDED element in plain view PASSES (${r.exposed.posRounded.why})`);
  assert.equal(r.exposed.realTitle.ok, true, `CALIBRATION: the real title, visible in the capture, PASSES (${r.exposed.realTitle.why})`);
  assert.equal(r.exposed.negClipped.ok, false, 'CALIBRATION: a child clipped by its overflow:hidden box FAILS');
  assert.equal(r.exposed.negFixedInTransform.ok, false, 'CALIBRATION: a fixed child clipped by its transformed containing block FAILS');
  assert.equal(r.exposed.negOccluded.ok, false, 'CALIBRATION: an occluded element FAILS');
}, STEPS_MS);

const SPLIT_SIZES = [
  { width: 1280, height: 800 },
  { width: 1280, height: 720 },
  { width: 1440, height: 900 },
  { width: 1600, height: 1000 },
  { width: 900, height: 700 },
  { width: 900, height: 800 },
];

for (const vp of SPLIT_SIZES)
  test(`B2e SPLIT at ${vp.width}x${vp.height}: Recent left (187px, the pick drawn), the conversation beside it; navigation and exits CONTAINED and hit-tested at the first draw and scrolled to the end; no self-scroll; focus seen; chips hold their words; a new turn still comes into view`, async () => {
    await bootPicked(vp);
    const callsAtFirstDraw = await probed();
    const g = await geometry();
    const first = await exposure(EXPOSED);
    await shot(`b2es-${vp.width}x${vp.height}-1-first-draw.png`);
    // the conversation scrolled to its END (its own overflow is expected): navigation and exits must still be exposed
    await p().evaluate(() => {
      const m = document.querySelector<HTMLElement>('[data-testid="di-new-main-scroll"]')!;
      m.scrollTop = m.scrollHeight;
    });
    await p().waitForTimeout(200);
    const atEnd = await exposure(EXPOSED);
    await shot(`b2es-${vp.width}x${vp.height}-2-scrolled-to-end.png`);
    // RULE 7 (the effect fix): a turn ARRIVING in the conversation drawn still comes into view. Back to the top, send one, wait for its answer.
    await p().evaluate(() => {
      document.querySelector<HTMLElement>('[data-testid="di-new-main-scroll"]')!.scrollTop = 0;
    });
    await p().locator(sel('di-new-ask')).fill('SYNTHETIC a follow-up turn, sent into the conversation picked');
    await probe();
    await p().click(sel('di-new-send'));
    await p().waitForFunction(() => document.querySelectorAll('.di-new-turns > li').length >= 2, undefined, { timeout: 20_000 });
    await p().waitForTimeout(400);
    const arrived = await p().evaluate(() => {
      const m = document.querySelector<HTMLElement>('[data-testid="di-new-main-scroll"]')!;
      const last = [...document.querySelectorAll<HTMLElement>('.di-new-turns > li')].pop()!;
      const mb = m.getBoundingClientRect();
      const lb = last.getBoundingClientRect();
      return { scrollTop: Math.round(m.scrollTop), newestInPane: lb.top >= mb.top - 0.5 && lb.bottom <= mb.bottom + 0.5, newestTop: Math.round(lb.top), paneTop: Math.round(mb.top), paneBottom: Math.round(mb.bottom), turns: document.querySelectorAll('.di-new-turns > li').length };
    });
    const callsOnArrival = await probed();
    await shot(`b2es-${vp.width}x${vp.height}-3-a-turn-arrived.png`);
    const expectedMain = g.stageContentWidth - PAGE_PADDING_PX - PANE_PX - GAP_PX;
    record(`${vp.width}x${vp.height}`, {
      geometry: g,
      conversationWidth: g.main?.width,
      conversationWidthVsOld696: (g.main?.width ?? 0) - OLD_COLUMN_PX,
      expectedConversationWidth: expectedMain,
      first,
      atEnd,
      arrived,
      callsAtFirstDraw,
      callsOnArrival,
    });
    // STATES
    assert.equal(await p().locator(sel('di-scan-panel')).getAttribute('data-view'), 'new', 'STATE: New Mission is open');
    assert.equal(g.chipCount, LONG.length, `STATE: all ${LONG.length} asks are in Recent`);
    assert.equal(await chipFor(PICK.id).getAttribute('aria-current'), 'true', 'STATE: the seventh ask is picked');
    // THE CONTRACT (ruled)
    assert.ok(g.side !== null && g.main !== null && g.side.left < g.main.left, 'CONTRACT: Recent is the LEFT pane, the conversation beside it');
    assert.ok(Math.abs((g.side?.width ?? 0) - PANE_PX) <= 1, `CONTRACT: the Recent pane is ${PANE_PX}px (${g.side?.width})`);
    assert.ok(Math.abs((g.main?.width ?? 0) - expectedMain) <= 2, `CONTRACT: the conversation takes the remainder, ${expectedMain}px (${g.main?.width})`);
    assert.equal(g.stageScrollTop, 0, 'CONTRACT (defect 2): the stage did not scroll itself at the first draw');
    assert.equal(g.mainScroll.scrollTop, 0, "CONTRACT (defect 2): the conversation's pane did not scroll itself at the first draw");
    for (const [name, r] of Object.entries(first.exposed)) assert.equal(r.ok, true, `CONTRACT: at the first draw, ${name} is EXPOSED (contained and hit) (${r.why})`);
    assert.equal(first.focusConversation, PICK.id, `CONTRACT (defect 1): focus is on the picked ask (${first.focusTarget})`);
    for (const [name, r] of Object.entries(atEnd.exposed)) assert.equal(r.ok, true, `CONTRACT: scrolled to the end, ${name} is EXPOSED (contained and hit) (${r.why})`);
    assert.ok(g.pickStyle !== null && g.otherStyle !== null && g.pickStyle.background !== g.otherStyle.background && g.pickStyle.border !== g.otherStyle.border, `CONTRACT: the pick is DRAWN (${JSON.stringify(g.pickStyle)} vs ${JSON.stringify(g.otherStyle)})`);
    assert.deepEqual(g.chipsSpilling, [], 'CONTRACT (defect 3): every ask is held inside its chip (none spills)');
    assert.deepEqual(callsAtFirstDraw, [], 'CASE (c): the FIRST DRAW of a read conversation (a pick) does not move the page: no scrollIntoView');
    assert.ok(callsOnArrival.length >= 1, `CASE (b): a LATER ARRIVAL calls scrollIntoView (${JSON.stringify(callsOnArrival)})`);
    assert.ok(arrived.turns >= 2 && arrived.newestInPane, `CASE (b), RULE 7 POSITIVE CONTROL: the arriving turn is IN VIEW in the conversation's pane (${JSON.stringify(arrived)})`);
  }, STEPS_MS);

test('CASE (c), a SWITCH at 1440x900: picking ANOTHER ask draws its conversation without moving the page (no scrollIntoView)', async () => {
  await bootPicked({ width: 1440, height: 900 });
  const other = LONG[7];
  await probe();
  await chipFor(other.id).click();
  await p().waitForFunction((id) => document.querySelector(`[data-testid="di-new-recent-ask"][data-conversation="${id}"]`)?.getAttribute('aria-current') === 'true', other.id, { timeout: 20_000 });
  await p().waitForSelector(sel('di-new-thread'), { timeout: 20_000 });
  await p().waitForTimeout(400);
  const calls = await probed();
  record('switch 1440', { calls });
  assert.equal(await chipFor(other.id).getAttribute('aria-current'), 'true', 'STATE: the other ask is now the one shown');
  assert.deepEqual(calls, [], 'CASE (c): a SWITCH to another read conversation does not move the page');
}, STEPS_MS);

test('CASE (a), a FRESH SEND at 1440x900: from the blank page, the first turn the person sends comes into view (scrollIntoView, and in the pane)', async () => {
  await boot({ width: 1440, height: 900 });
  await p().locator(sel('di-new-ask')).fill('SYNTHETIC a fresh ask, sent from the blank New Mission');
  await probe();
  await p().click(sel('di-new-send'));
  await p().waitForSelector(sel('di-new-thread'), { timeout: 20_000 });
  await p().waitForTimeout(400);
  const calls = await probed();
  const inPane = await p().evaluate(() => {
    const m = document.querySelector<HTMLElement>('[data-testid="di-new-main-scroll"]')!.getBoundingClientRect();
    const last = [...document.querySelectorAll<HTMLElement>('.di-new-turns > li')].pop()!.getBoundingClientRect();
    return last.top >= m.top - 0.5 && last.top < m.bottom;
  });
  record('fresh send 1440', { calls, inPane, sent });
  assert.ok(sent.some((r) => r.op === 'turn' && r.conversationId === null), 'STATE: a fresh turn was sent (no conversation picked)');
  assert.ok(calls.length >= 1, `CASE (a): a FRESH SEND calls scrollIntoView (${JSON.stringify(calls)})`);
  assert.equal(inPane, true, "CASE (a): the person's first turn is in view in the conversation's pane");
}, STEPS_MS);

/** B2e R22, finding 2: the conversation the synthetic panel holds EMPTY (a successful read with no messages). */
const EMPTY = LONG[3];
for (const vp of [
  { width: 1280, height: 800, stacked: false },
  { width: 1440, height: 900, stacked: false },
  { width: 1600, height: 1000, stacked: false },
  { width: 1280, height: 720, stacked: false },
  { width: 760, height: 700, stacked: true },
])
  test(`CASE (d), an EMPTY successful read THEN the FIRST SEND, at ${vp.width}x${vp.height}, ${vp.stacked ? 'STACKED (the page scrolls as one column, Recent below)' : 'two panes (the person has dragged the ask box taller by its resize handle)'}, the scroller OVERFLOWING and scrolled down: the first turn is an ARRIVAL and comes into view (scrollIntoView, and in view)`, async () => {
    await boot({ width: vp.width, height: vp.height });
    turnsOf[EMPTY.id] = [];
    await chipFor(EMPTY.id).click();
    await p().waitForSelector(sel('di-new-thread-empty'), { timeout: 20_000 });
    const emptyWords = await p().locator(sel('di-new-thread-empty')).innerText();
    await p().locator(sel('di-new-ask')).fill('SYNTHETIC the first ask sent into a conversation that was read and is empty');
    const SCROLLER = vp.stacked ? '[data-testid="di-scan-stage"]' : '[data-testid="di-new-main-scroll"]';
    // the instrument, two panes: the person drags the ask box to the pane's own height (resize: vertical; the drag sets exactly this inline height)
    const box = await p().evaluate(
      ({ s, stacked }) => {
        const ta = document.querySelector<HTMLTextAreaElement>('[data-testid="di-new-ask"]')!;
        if (!stacked) ta.style.height = `${document.querySelector<HTMLElement>(s)!.clientHeight}px`;
        return { resize: getComputedStyle(ta).resize, height: Math.round(ta.getBoundingClientRect().height) };
      },
      { s: SCROLLER, stacked: vp.stacked },
    );
    await p().waitForTimeout(200);
    await p().evaluate((s) => {
      const m = document.querySelector<HTMLElement>(s)!;
      m.scrollTop = m.scrollHeight;
    }, SCROLLER);
    await p().waitForTimeout(200);
    const pre = await p().evaluate((s) => {
      const m = document.querySelector<HTMLElement>(s)!;
      const side = document.querySelector<HTMLElement>('[data-testid="di-new-side"]')!.getBoundingClientRect();
      const main = document.querySelector<HTMLElement>('[data-testid="di-new-main"]')!.getBoundingClientRect();
      return {
        threadDrawn: document.querySelector('[data-testid="di-new-thread"]') !== null,
        turnsDrawn: document.querySelectorAll('.di-new-turns > li').length,
        stacked: side.top >= main.bottom - 1,
        scrollHeight: m.scrollHeight,
        clientHeight: m.clientHeight,
        scrollTop: Math.round(m.scrollTop),
      };
    }, SCROLLER);
    // STATE preconditions, each REACHED before the send (a counterfactual that dies here proves only this far)
    assert.match(emptyWords, /was read: it holds no messages/, 'STATE: the conversation was READ and is EMPTY (a successful read, not "not read")');
    assert.equal(pre.threadDrawn, false, 'STATE: no thread is drawn while it is empty (NewMissionThread mounts only with turns)');
    assert.equal(pre.turnsDrawn, 0, 'STATE: no turn is drawn before the send');
    assert.equal(pre.stacked, vp.stacked, `STATE: the layout is ${vp.stacked ? 'STACKED' : 'TWO PANES'}`);
    if (!vp.stacked) {
      assert.equal(box.resize, 'vertical', 'STATE: the ask box is the person\'s to resize (resize: vertical)');
      assert.ok(box.height >= pre.clientHeight - 2, `STATE: the ask box was dragged to the pane's height (${box.height} for ${pre.clientHeight})`);
    }
    assert.ok(pre.scrollHeight > pre.clientHeight + 40, `STATE: the scroller OVERFLOWS (${pre.scrollHeight} > ${pre.clientHeight})`);
    assert.ok(pre.scrollTop > 0, `STATE: the scroller is scrolled down (${pre.scrollTop})`);
    await probe();
    await p().click(sel('di-new-send'));
    await p().waitForFunction(() => document.querySelectorAll('.di-new-turns > li').length >= 1, undefined, { timeout: 20_000 });
    await p().waitForTimeout(400);
    const calls = await probed();
    const arrived = await p().evaluate((s) => {
      const m = document.querySelector<HTMLElement>(s)!;
      const last = [...document.querySelectorAll<HTMLElement>('.di-new-turns > li')].pop()!;
      const mb = m.getBoundingClientRect();
      const lb = last.getBoundingClientRect();
      // what may cover the scroller's edges: the page's sticky head and its sticky foot, where they lie over it
      const head = document.querySelector<HTMLElement>('[data-testid="di-new-mission"] .di-page-head')?.getBoundingClientRect() ?? null;
      const foot = document.querySelector<HTMLElement>('.di-new-foot')?.getBoundingClientRect() ?? null;
      const top = Math.max(mb.top, head !== null && head.bottom > mb.top && head.top <= mb.top + 1 ? head.bottom : mb.top);
      const bottom = Math.min(mb.bottom, innerHeight, foot !== null && foot.top < mb.bottom && foot.bottom >= mb.bottom - 1 ? foot.top : mb.bottom);
      return {
        turns: document.querySelectorAll('.di-new-turns > li').length,
        scrollTop: Math.round(m.scrollTop),
        newestInView: lb.top >= top - 0.5 && lb.bottom <= bottom + 0.5,
        newest: { top: Math.round(lb.top), bottom: Math.round(lb.bottom) },
        view: { top: Math.round(top), bottom: Math.round(bottom) },
      };
    }, SCROLLER);
    await shot(`b2es-${vp.width}x${vp.height}-8-empty-read-first-send.png`);
    record(`case d ${vp.width}x${vp.height}`, { emptyWords, box, pre, calls, arrived });
    // the RESULT: both computed above, before either is asserted
    assert.ok(calls.length >= 1, `CASE (d): the first turn of a conversation drawn as READ and EMPTY is an ARRIVAL: scrollIntoView is called (${JSON.stringify(calls)})`);
    assert.ok(arrived.newestInView, `CASE (d), the OUTCOME: the first turn is IN VIEW, clear of any sticky head or foot (${JSON.stringify(arrived)})`);
  }, STEPS_MS);

for (const vp of [
  { width: 1440, height: 900 },
  { width: 1280, height: 800 },
])
  test(`DESELECTION at ${vp.width}x${vp.height}: the selected ask, clicked again, closes the conversation (the Close transition): blank, the ask kept EXACTLY, focus in the box, nothing read again, nothing sent`, async () => {
    await bootPicked(vp);
    const TYPED = '  An unsent ask, typed after the pick ✓\nsecond line, two  spaces.  ';
    await p().locator(sel('di-new-ask')).fill(TYPED);
    const before = sent.length;
    await chipFor(PICK.id).click();
    await p().waitForTimeout(400);
    const after = sent.slice(before);
    const state = await p().evaluate(() => ({
      current: document.querySelectorAll('[data-testid="di-new-recent-ask"][aria-current="true"]').length,
      drawn: ['di-new-thread', 'di-new-thread-empty', 'di-new-leave-unread', 'di-new-close-conversation'].filter((id) => document.querySelector(`[data-testid="${id}"]`) !== null),
      focus: document.activeElement?.getAttribute('data-testid') ?? document.activeElement?.tagName ?? null,
    }));
    const kept = await p().locator(sel('di-new-ask')).inputValue();
    await shot(`b2es-${vp.width}x${vp.height}-4-deselected.png`);
    record(`deselect ${vp.width}x${vp.height}`, { state, keptExactly: kept === TYPED, after });
    assert.equal(await p().locator(sel('di-scan-panel')).getAttribute('data-view'), 'new', 'STATE: still on New Mission');
    assert.equal(state.current, 0, 'CONTRACT: no ask is selected any more');
    assert.deepEqual(state.drawn, [], 'CONTRACT: nothing of the conversation is drawn (the blank start)');
    assert.equal(kept, TYPED, 'CONTRACT (D1 retention): the typed ask is kept EXACTLY');
    assert.equal(state.focus, 'di-new-ask', 'CONTRACT (D1): focus is in the box');
    assert.deepEqual(after.filter((r) => r.op === 'conversation' || r.op === 'turn'), [], 'CONTRACT: nothing is read again and nothing is sent');
  }, STEPS_MS);

// ── B2e CORRECTION (the Lead's rulings): (1) RETRY is a labelled control of its own, and the selected ask keeps CLOSING in every state; (F4, option A) while
// the request workspace holds the ask the panes swap; (3) the pick's colours as drawn; (condition one) the panes' order, measured.

const readsOf = (id: string) => sent.filter((r) => r.op === 'conversation' && r.conversationId === id).length;
/** The seventh ask picked with its read REFUSED: the page says it could not be read and offers to read it again. */
async function bootPickedRefused(viewport: { width: number; height: number }): Promise<void> {
  await boot(viewport);
  refuse.add(PICK.id);
  await chipFor(PICK.id).click();
  await p().waitForSelector(sel('di-new-read-again'), { timeout: 20_000 });
  await p().waitForTimeout(300);
}
/** The panes' order: as the DOM holds them, and as the grid draws them (measurement). */
const paneOrder = () =>
  p().evaluate(() => {
    const main = document.querySelector<HTMLElement>('[data-testid="di-new-main"]')!;
    const side = document.querySelector<HTMLElement>('[data-testid="di-new-side"]')!;
    const at = (el: HTMLElement) => ({ left: Math.round(el.getBoundingClientRect().left), gridColumn: getComputedStyle(el).gridColumnStart });
    return {
      domConversationFirst: (main.compareDocumentPosition(side) & Node.DOCUMENT_POSITION_FOLLOWING) !== 0,
      conversation: at(main),
      recent: at(side),
      ask: document.querySelector('[data-testid="di-new-body"]')?.getAttribute('data-ask') ?? null,
    };
  });
/** The REAL Tab sequence from the start of New Mission: EVERY stop (its region, test id, conversation and x) and whether focus there is SEEN (the
 *  calibrated exposure helper's own focus entry), plus the stops collapsed into runs by region. Measurement only; the page is made focusable for the start. */
async function tabRuns(max = 120): Promise<{
  runs: Array<{ region: string; count: number; first: string | null; firstLeft: number | null }>;
  stops: Array<{ region: string; id: string | null; conversation: string | null; left: number | null; seen: { ok: boolean; why: string } }>;
}> {
  await p().evaluate(() => {
    const page = document.querySelector<HTMLElement>('[data-testid="di-new-mission"]')!;
    page.setAttribute('tabindex', '-1');
    page.focus();
  });
  const runs: Array<{ region: string; count: number; first: string | null; firstLeft: number | null }> = [];
  const stops: Array<{ region: string; id: string | null; conversation: string | null; left: number | null; seen: { ok: boolean; why: string } }> = [];
  for (let i = 0; i < max; i += 1) {
    await p().keyboard.press('Tab');
    const step = await p().evaluate(() => {
      const a = document.activeElement as HTMLElement | null;
      const region =
        a === null || a === document.body
          ? 'body'
          : a.closest('[data-testid="di-new-main"]')
            ? 'conversation'
            : a.closest('[data-testid="di-new-side"]')
              ? 'recent'
              : a.closest('[data-testid="di-new-mission"]')
                ? 'page head or foot'
                : a.closest('[data-testid="di-mr"]')
                  ? 'workspace'
                  : 'outside New Mission';
      return {
        region,
        id: a?.getAttribute('data-testid') ?? a?.tagName ?? null,
        conversation: a?.getAttribute('data-conversation') ?? null,
        left: a === null ? null : Math.round(a.getBoundingClientRect().left),
      };
    });
    const seen = (await exposure({})).exposed.focus;
    stops.push({ ...step, seen });
    const last = runs[runs.length - 1];
    if (last !== undefined && last.region === step.region) last.count += 1;
    else runs.push({ region: step.region, count: 1, first: step.id, firstLeft: step.left });
    if (step.region === 'outside New Mission' || step.region === 'workspace' || step.region === 'body') break;
  }
  await p().evaluate(() => document.querySelector('[data-testid="di-new-mission"]')?.removeAttribute('tabindex'));
  return { runs, stops };
}
async function openWorkspaceWithTheAsk(): Promise<void> {
  await p().locator(sel('di-new-ask')).fill('SYNTHETIC an ask typed before the request workspace opens');
  await p().click(sel('di-new-open-workspace'));
  await p().waitForSelector(sel('di-new-ask-bring-back'), { timeout: 20_000 });
  await p().waitForTimeout(400);
}

for (const vp of SPLIT_SIZES)
  test(`RETRY at ${vp.width}x${vp.height}: a picked ask whose read was REFUSED offers "${DI_NEW_READ_AGAIN}", EXPOSED in the pinned head row; it reads the conversation again and does NOT close it; focus goes first to "Close this conversation" beside it, and is SEEN; its first draw does not move the page`, async () => {
    await bootPickedRefused(vp);
    const pickedBefore = await chipFor(PICK.id).getAttribute('aria-current');
    const offered = await exposure({ readAgain: sel('di-new-read-again'), closeConversation: EXPOSED.closeConversation, pickedChip: EXPOSED.pickedChip });
    const label = (await p().locator(sel('di-new-read-again')).innerText()).trim();
    await shot(`b2es-${vp.width}x${vp.height}-5-read-refused.png`);
    refuse.delete(PICK.id);
    const readsBefore = readsOf(PICK.id);
    await probe();
    await p().click(sel('di-new-read-again'));
    await p().waitForSelector(sel('di-new-thread'), { timeout: 20_000 });
    await p().waitForTimeout(400);
    const after = await exposure({ closeConversation: EXPOSED.closeConversation, pickedChip: EXPOSED.pickedChip });
    const more = readsOf(PICK.id) - readsBefore;
    const pickedAfter = await chipFor(PICK.id).getAttribute('aria-current');
    const stillOffered = await p().locator(sel('di-new-read-again')).count();
    const calls = await probed();
    await shot(`b2es-${vp.width}x${vp.height}-6-read-again.png`);
    record(`retry ${vp.width}x${vp.height}`, { pickedBefore, offered, label, more, pickedAfter, stillOffered, after, calls });
    assert.equal(pickedBefore, 'true', 'STATE: the seventh ask is picked, its read refused');
    assert.equal(label, DI_NEW_READ_AGAIN, 'STATE: the control says what it does');
    assert.equal(offered.exposed.readAgain.ok, true, `CONTRACT: "${DI_NEW_READ_AGAIN}" is EXPOSED (contained and hit) (${offered.exposed.readAgain.why})`);
    assert.equal(offered.exposed.closeConversation.ok, true, `CONTRACT: "Close this conversation" stays EXPOSED beside it (${offered.exposed.closeConversation.why})`);
    assert.ok(more >= 1, `CONTRACT: it READS the conversation again (${more} more read(s))`);
    assert.equal(pickedAfter, 'true', 'CONTRACT: it does NOT close the conversation: the ask stays picked and its thread is drawn');
    assert.equal(stillOffered, 0, 'CONTRACT: once read, it is no longer offered');
    assert.equal(after.focusTarget, 'di-new-close-conversation', `CONTRACT: focus went first to "Close this conversation", in the same pinned row (${after.focusTarget})`);
    assert.equal(after.exposed.focus.ok, true, `CONTRACT: where focus is, is SEEN (${after.exposed.focus.why})`);
    assert.deepEqual(calls, [], 'CASE (c): the first draw of the conversation, read at last, does not move the page: no scrollIntoView');
  }, STEPS_MS);

for (const vp of [
  { width: 1440, height: 900 },
  { width: 1280, height: 800 },
])
  test(`DESELECTION OF A FAILED PICK at ${vp.width}x${vp.height}: the selected ask whose read was REFUSED, clicked again, CLOSES the conversation (the Close transition): blank, the ask kept EXACTLY, focus in the box, nothing read again, nothing sent`, async () => {
    await bootPickedRefused(vp);
    const TYPED = '  An unsent ask, typed while the pick could not be read ✓\nsecond line.  ';
    await p().locator(sel('di-new-ask')).fill(TYPED);
    const before = sent.length;
    await chipFor(PICK.id).click();
    await p().waitForTimeout(400);
    const after = sent.slice(before);
    const state = await p().evaluate(() => ({
      current: document.querySelectorAll('[data-testid="di-new-recent-ask"][aria-current="true"]').length,
      drawn: ['di-new-thread', 'di-new-thread-empty', 'di-new-leave-unread', 'di-new-close-conversation', 'di-new-read-again'].filter((id) => document.querySelector(`[data-testid="${id}"]`) !== null),
      focus: document.activeElement?.getAttribute('data-testid') ?? document.activeElement?.tagName ?? null,
    }));
    const kept = await p().locator(sel('di-new-ask')).inputValue();
    record(`deselect failed ${vp.width}x${vp.height}`, { state, keptExactly: kept === TYPED, after });
    assert.equal(await p().locator(sel('di-scan-panel')).getAttribute('data-view'), 'new', 'STATE: still on New Mission');
    assert.equal(state.current, 0, 'CONTRACT: no ask is selected any more');
    assert.deepEqual(state.drawn, [], 'CONTRACT: nothing of the conversation is drawn (the blank start), and nothing offers to read it');
    assert.equal(kept, TYPED, 'CONTRACT (D1 retention): the typed ask is kept EXACTLY');
    assert.equal(state.focus, 'di-new-ask', 'CONTRACT (D1): focus is in the box');
    assert.deepEqual(after.filter((r) => r.op === 'conversation' || r.op === 'turn'), [], 'CONTRACT: nothing is read again and nothing is sent');
  }, STEPS_MS);

for (const vp of [
  { width: 1280, height: 800 },
  { width: 1440, height: 900 },
  { width: 1600, height: 1000 },
])
  test(`F4 (option A) at ${vp.width}x${vp.height}: while the request workspace holds the ask the panes SWAP, the conversation back at the page's left edge; "Bring the ask back here" ${vp.width === 1280 ? 'MEASURED (at 1280 it was covered before the split too: the pre-split probe)' : 'EXPOSED (contained and hit), and bringing it back restores the split'}`, async () => {
    await boot(vp);
    await openWorkspaceWithTheAsk();
    const held = await paneOrder();
    const geo = await p().evaluate(() => {
      const page = document.querySelector<HTMLElement>('[data-testid="di-new-mission"]')!;
      const mr = document.querySelector<HTMLElement>('[data-testid="di-mr"]');
      const bb = document.querySelector<HTMLElement>('[data-testid="di-new-ask-bring-back"]')!.getBoundingClientRect();
      return {
        pageContentLeft: Math.round(page.getBoundingClientRect().left + parseFloat(getComputedStyle(page).paddingLeft)),
        workspace: mr === null ? null : { left: Math.round(mr.getBoundingClientRect().left), right: Math.round(mr.getBoundingClientRect().right) },
        bringBack: { left: Math.round(bb.left), right: Math.round(bb.right) },
      };
    });
    const ex = await exposure({ bringBack: sel('di-new-ask-bring-back') });
    await shot(`b2es-${vp.width}x${vp.height}-7-workspace-holds-the-ask.png`);
    let restored: Awaited<ReturnType<typeof paneOrder>> | null = null;
    if (vp.width !== 1280) {
      await p().click(sel('di-new-ask-bring-back'));
      await p().waitForSelector(sel('di-new-ask'), { timeout: 10_000 });
      await p().waitForTimeout(300);
      restored = await paneOrder();
    }
    record(`F4 swap ${vp.width}x${vp.height}`, { held, geo, bringBack: ex.exposed.bringBack, restored });
    assert.equal(held.ask, 'elsewhere', 'STATE: the request workspace holds the ask');
    assert.ok(geo.workspace !== null, 'STATE: the request workspace is open');
    assert.ok(Math.abs(held.conversation.left - geo.pageContentLeft) <= 1, `CONTRACT: the conversation is back at the page's left edge (${held.conversation.left} vs ${geo.pageContentLeft})`);
    assert.ok(held.conversation.left < held.recent.left, 'CONTRACT: the panes SWAP while the workspace holds the ask');
    assert.equal(held.domConversationFirst, true, 'CONTRACT: the DOM (so Tab) order is unchanged');
    if (vp.width !== 1280) {
      assert.equal(ex.exposed.bringBack.ok, true, `CONTRACT: "Bring the ask back here" is EXPOSED (contained and hit) (${ex.exposed.bringBack.why})`);
      assert.ok(restored !== null && restored.ask === null && restored.recent.left < restored.conversation.left, `CONTRACT: bringing the ask back restores the split (${JSON.stringify(restored)})`);
    }
  }, STEPS_MS);

test("THE PICK'S COLOURS, RENDERED at 1440x900 (the contrast guard's pairs, as drawn): its words, fill and edge are the tokens the guard paired, the page behind it is the canvas, the pairs hold, and hovered it keeps its fill", async () => {
  await bootPicked({ width: 1440, height: 900 });
  const c = await p().evaluate(() => {
    const picked = document.querySelector<HTMLElement>('[data-testid="di-new-recent-ask"][aria-current="true"]')!;
    // a token resolved IN THE CHIP'S OWN SCOPE (a probe element beside it), so the comparison is against the token, not a typed value
    const token = (name: string) => {
      const el = document.createElement('span');
      el.style.color = `var(--di-${name})`;
      picked.parentElement!.appendChild(el);
      const v = getComputedStyle(el).color;
      el.remove();
      return v;
    };
    let behind: { colour: string; by: string } | null = null;
    for (let a = picked.parentElement; a !== null && behind === null; a = a.parentElement) {
      const bg = getComputedStyle(a).backgroundColor;
      if (!/^rgba\(\d+, \d+, \d+, 0\)$/.test(bg) && bg !== 'transparent') behind = { colour: bg, by: a.getAttribute('data-testid') ?? a.className };
    }
    const cs = getComputedStyle(picked);
    return { words: cs.color, fill: cs.backgroundColor, edge: cs.borderTopColor, behind, tokens: { text: token('scan-text'), selected: token('scan-selected'), yellow: token('scan-yellow'), canvas: token('scan-canvas') } };
  });
  await p().hover(EXPOSED.pickedChip);
  await p().waitForTimeout(150);
  const hovered = await p().evaluate(() => getComputedStyle(document.querySelector('[data-testid="di-new-recent-ask"][aria-current="true"]')!).backgroundColor);
  const hex = (rgb: string) => {
    const m = /^rgba?\((\d+), (\d+), (\d+)(?:, ([\d.]+))?\)$/.exec(rgb);
    assert.ok(m !== null && (m[4] === undefined || m[4] === '1'), `a SOLID colour (${rgb})`);
    return `#${[m[1], m[2], m[3]].map((x) => Number(x).toString(16).padStart(2, '0')).join('')}`;
  };
  assert.ok(c.behind !== null, 'STATE: something paints the page behind the pick');
  const ratios = { words: contrastRatio(hex(c.words), hex(c.fill)), edgeOnFill: contrastRatio(hex(c.edge), hex(c.fill)), edgeOnPage: contrastRatio(hex(c.edge), hex(c.behind.colour)) };
  record('pick colours 1440x900', { ...c, hovered, ratios });
  assert.equal(c.words, c.tokens.text, 'CONTRACT: its words are drawn in scan-text, the colour the guard paired');
  assert.equal(c.fill, c.tokens.selected, 'CONTRACT: its fill is drawn in scan-selected, the colour the guard paired');
  assert.equal(c.edge, c.tokens.yellow, 'CONTRACT: its edge is drawn in scan-yellow, the colour the guard paired');
  assert.equal(c.behind.colour, c.tokens.canvas, `CONTRACT: the page behind it is the canvas the guard paired the edge against (${c.behind.by})`);
  assert.ok(ratios.words >= 4.5, `CONTRACT: its words read at 4.5:1 as drawn (${ratios.words.toFixed(2)})`);
  assert.ok(ratios.edgeOnFill >= 3 && ratios.edgeOnPage >= 3, `CONTRACT: its edge is 3:1 against its fill and the page, as drawn (${ratios.edgeOnFill.toFixed(2)}, ${ratios.edgeOnPage.toFixed(2)})`);
  assert.equal(hovered, c.fill, 'CONTRACT: hovered, the pick keeps its fill (the pick rule follows the hover rule at equal specificity)');
}, STEPS_MS);

// ── B2e R22, FINDING 1: TRUTHFUL FOCUS ISOLATION of what the shown request workspace covers. While it holds the ask over the open stage, every covered
// stage control is ISOLATED (inert, still drawn); the real Tab ring lands only on SEEN stops; every covered action keeps a labelled keyboard route; the
// exact words survive each route; a pointer inside the workspace changes nothing; a focused control that becomes covered lands on the workspace's heading.

type RingStop = { region: string; id: string | null; conversation: string | null; marked: boolean; seen: { ok: boolean; why: string } | null };
/** Focus the start of New Mission (the page is made focusable for the start alone). */
async function pageStart(): Promise<void> {
  await p().evaluate(() => {
    const page = document.querySelector<HTMLElement>('[data-testid="di-new-mission"]')!;
    page.setAttribute('tabindex', '-1');
    page.focus();
  });
}
async function pageStartDone(): Promise<void> {
  await p().evaluate(() => document.querySelector('[data-testid="di-new-mission"]')?.removeAttribute('tabindex'));
}
/** The REAL ring of `key` stops from the start of New Mission until it comes back round to its first stop (marked) or `max`: region, identity, the
 *  isolation mark, and SEEN (the calibrated helper's own focus entry). The browser's own chrome stop is recorded as `body`. Measurement only. */
async function ringWalk(key: 'Tab' | 'Shift+Tab', max = 160): Promise<RingStop[]> {
  await pageStart();
  const stops: RingStop[] = [];
  for (let i = 0; i < max; i += 1) {
    await p().keyboard.press(key);
    const step = await p().evaluate(() => {
      const a = document.activeElement as HTMLElement | null;
      if (a === null || a === document.body) return { back: false, region: 'body', id: null, conversation: null, marked: false };
      const back = a.hasAttribute('data-b2e-ring-start');
      if (document.querySelector('[data-b2e-ring-start]') === null) a.setAttribute('data-b2e-ring-start', '');
      const region = a.closest('.di-mr') ? 'workspace' : a.closest('.di-scan-stage') ? 'stage' : a.closest('[data-testid="di-scan-panel"]') ? 'scan panel' : 'elsewhere';
      return { back, region, id: a.getAttribute('data-testid') ?? a.tagName, conversation: a.getAttribute('data-conversation'), marked: a.closest('[data-di-covered]') !== null };
    });
    if (step.back) break;
    stops.push({ region: step.region, id: step.id, conversation: step.conversation, marked: step.marked, seen: step.region === 'body' ? null : (await exposure({})).exposed.focus });
  }
  await p().evaluate(() => document.querySelector('[data-b2e-ring-start]')?.removeAttribute('data-b2e-ring-start'));
  await pageStartDone();
  return stops;
}
/** Press `key` from the start of New Mission until `testid` (and `conversation`) has focus; the number of presses, or -1. */
async function keyTo(testid: string, conversation: string | null = null, key: 'Tab' | 'Shift+Tab' = 'Tab', max = 160): Promise<number> {
  await pageStart();
  for (let i = 1; i <= max; i += 1) {
    await p().keyboard.press(key);
    const hit = await p().evaluate(
      ({ t, c }) => {
        const a = document.activeElement;
        return a !== null && a.getAttribute('data-testid') === t && (c === null || a.getAttribute('data-conversation') === c);
      },
      { t: testid, c: conversation },
    );
    if (hit) {
      await pageStartDone();
      return i;
    }
  }
  await pageStartDone();
  return -1;
}
/** The isolation as RENDERED: the marked elements, and, computed independently from boxes, the stage's Tab-order controls the shown workspace overlaps. */
const isolationRendered = () =>
  p().evaluate(() => {
    const ws = document.querySelector<HTMLElement>('.di-mr:not([hidden])');
    const w = ws?.getBoundingClientRect() ?? null;
    const name = (el: Element) => `${el.getAttribute('data-testid') ?? el.tagName}${el.getAttribute('data-conversation') ? `@${el.getAttribute('data-conversation')!.slice(0, 7)}` : ''}`;
    const tabbable = [...document.querySelectorAll<HTMLElement>('.di-scan-stage :is(a[href], button, input, select, textarea, summary, [tabindex], [contenteditable="true"])')].filter((el) => {
      const t = el.getAttribute('tabindex');
      return t === null || Number(t) >= 0;
    });
    const covered = w === null ? [] : tabbable.filter((el) => {
      const r = el.getBoundingClientRect();
      return r.width > 0 && r.height > 0 && r.left < w.right && r.right > w.left;
    });
    const marked = [...document.querySelectorAll<HTMLElement>('[data-di-covered]')];
    // every OTHER mark must be a covered scroll container with no live control inside (one that would be a Tab stop of its own)
    const badExtra = marked
      .filter((el) => !covered.includes(el))
      .filter((el) => {
        const st = getComputedStyle(el);
        const scroller = (/(auto|scroll)/.test(st.overflowY) || /(auto|scroll)/.test(st.overflowX)) && (el.scrollHeight > el.clientHeight + 1 || el.scrollWidth > el.clientWidth + 1);
        const r = el.getBoundingClientRect();
        const overlaps = w !== null && r.left < w.right && r.right > w.left;
        const live = tabbable.some((t) => el.contains(t) && t !== el && t.closest('[data-di-covered]') === el && !t.hasAttribute('data-di-covered') && !covered.includes(t) && (t as HTMLButtonElement).disabled !== true && t.getClientRects().length > 0);
        return !(scroller && overlaps && !live);
      })
      .map(name);
    const scrollers = [...document.querySelectorAll<HTMLElement>('.di-scan-stage, .di-scan-stage *')]
      .filter((el) => {
        const st = getComputedStyle(el);
        const r = el.getBoundingClientRect();
        return (/(auto|scroll)/.test(st.overflowY) || /(auto|scroll)/.test(st.overflowX)) && w !== null && r.left < w.right && r.right > w.left;
      })
      .map((el) => ({
        name: name(el),
        scroll: [el.scrollHeight, el.clientHeight, el.scrollWidth, el.clientWidth],
        marked: el.hasAttribute('data-di-covered'),
        unmarkedTabbables: tabbable
          .filter((t) => el.contains(t) && t !== el && t.closest('[data-di-covered]') === null)
          .map((t) => {
            const r = t.getBoundingClientRect();
            return { name: name(t), box: [Math.round(r.left), Math.round(r.width), Math.round(r.height)], rects: t.getClientRects().length, disabled: (t as HTMLButtonElement).disabled === true, hidden: t.closest('[hidden]') !== null, visibility: getComputedStyle(t).visibility };
          }),
      }));
    return {
      shown: ws !== null,
      covered: covered.map(name).sort(),
      marked: marked.map(name).sort(),
      badExtra,
      scrollers,
      markedNotInert: marked.filter((el) => !el.hasAttribute('inert')).map(name),
      boxes: ['di-new-close-conversation', 'di-new-ask-bring-back'].map((id) => {
        const el = document.querySelector(`[data-testid="${id}"]`);
        const b = el?.getBoundingClientRect();
        return b ? [id, Math.round(b.left), Math.round(b.top), Math.round(b.width), Math.round(b.height)] : [id, null];
      }),
    };
  });
const BEFORE_ASK = 'SYNTHETIC an ask typed before the request workspace opens';
const IN_WORKSPACE = ' ✓ and words typed in the workspace, two  spaces.';
/** The destinations B2e INTRODUCED as covered (measurement 4's table), per shipping size. */
const INTRODUCED: Record<string, Array<{ id: string; conversation: string | null }>> = {
  '1280x800': [],
  '1440x900': [{ id: 'di-new-open-workspace', conversation: null }],
  '1600x1000': [
    { id: 'di-new-recent-ask', conversation: LONG[0].id },
    { id: 'di-new-open-workspace', conversation: null },
  ],
};
for (const vp of [
  { width: 1280, height: 800 },
  { width: 1440, height: 900 },
  { width: 1600, height: 1000 },
])
  test(`ISOLATION at ${vp.width}x${vp.height}: the request workspace holding the ask over the stage: the REAL Tab ring, forward AND backward, lands only on SEEN stage stops; every covered control is ISOLATED (inert, drawn); a pointer in the workspace changes nothing; the routes out and back, by real keys, keep the EXACT words`, async () => {
    const size = `${vp.width}x${vp.height}`;
    await bootPicked(vp);
    await openWorkspaceWithTheAsk();
    // "Review requests" opens the workspace on its Requests tab; its composer is on the Ask tab
    await p().click(sel('di-mr-tab-ask'));
    await p().waitForSelector(sel('di-mr-composer'), { timeout: 10_000 });
    await p().locator(sel('di-mr-composer')).focus();
    // the caret after the last word (the End key does not move a textarea caret on macOS)
    await p().evaluate(() => {
      const ta = document.querySelector<HTMLTextAreaElement>('[data-testid="di-mr-composer"]')!;
      ta.setSelectionRange(ta.value.length, ta.value.length);
    });
    await p().keyboard.insertText(IN_WORKSPACE);
    const EXACT = BEFORE_ASK + IN_WORKSPACE;
    const held = await p().evaluate(() => ({ ask: document.querySelector('[data-testid="di-new-body"]')?.getAttribute('data-ask') ?? null }));
    const composer = await p().locator(sel('di-mr-composer')).inputValue();
    const forward = await ringWalk('Tab');
    const backward = await ringWalk('Shift+Tab');
    const rendered = await isolationRendered();
    record(`isolation ${size}`, { held, composer, forward, backward, rendered });
    // STATE preconditions, each REACHED first
    assert.equal(held.ask, 'elsewhere', 'STATE: the request workspace holds the ask');
    assert.equal(rendered.shown, true, 'STATE: the request workspace is shown over the stage');
    assert.equal(composer, EXACT, 'STATE: the workspace composer holds the exact words typed');
    assert.ok(forward.some((st) => st.region === 'stage') && backward.some((st) => st.region === 'stage'), 'STATE: both walks reached the stage');
    // THE SEEN LINE: the negative control must fail HERE on the bytes before the isolation
    const unseen = [...forward, ...backward].filter((st) => st.region === 'stage' && st.seen !== null && !st.seen.ok);
    assert.deepEqual(unseen, [], `CONTRACT (the SEEN line): every stage stop in the real Tab ring, forward and backward, is SEEN (${unseen.length} not)`);
    // RULE 7, behavioural: the marked controls are OUT of the ring
    assert.deepEqual([...forward, ...backward].filter((st) => st.marked), [], 'RULE 7 (behaviour): no stop of the real ring is an isolated control');
    // RULE 7, rendered: the marks are exactly the covered controls, each inert
    assert.deepEqual(rendered.covered.filter((nm) => !rendered.marked.includes(nm)), [], 'RULE 7 (rendered): every stage control the workspace covers is marked');
    assert.deepEqual(rendered.badExtra, [], 'RULE 7 (rendered): every other mark is a covered scroll container with no live control inside');
    assert.deepEqual(rendered.markedNotInert, [], 'RULE 7 (rendered): every marked control is inert');
    for (const d of INTRODUCED[size]) {
      const nm = `${d.id}${d.conversation ? `@${d.conversation.slice(0, 7)}` : ''}`;
      assert.ok(rendered.marked.includes(nm), `CONTRACT: the INTRODUCED destination ${nm} is ISOLATED while the workspace covers it`);
    }
    // the POINTER control: a real click inside the workspace changes nothing
    await p().locator(sel('di-mr-composer')).click();
    await p().waitForTimeout(300);
    const afterPointer = await isolationRendered();
    assert.equal(afterPointer.shown, true, 'CONTRACT (pointer): a click inside the workspace leaves it shown');
    assert.deepEqual(afterPointer.marked, rendered.marked, 'CONTRACT (pointer): the isolation is unchanged');
    assert.deepEqual(afterPointer.boxes, rendered.boxes, 'CONTRACT (pointer): nothing on the page moved');
    // the route OUT by real keys: "Bring the ask back here" where it is seen; the workspace's own "Close requests" at 1280 (bring-back is covered there)
    if (vp.width === 1280) {
      const n = await keyTo('di-mr-close');
      assert.ok(n > 0, 'CONTRACT (route): the workspace\'s "Close requests" is reached by Tab');
      await p().keyboard.press('Enter');
    } else {
      const n = await keyTo('di-new-ask-bring-back');
      assert.ok(n > 0, 'CONTRACT (route): "Bring the ask back here" is reached by Tab');
      assert.equal((await exposure({})).exposed.focus.ok, true, 'CONTRACT (route): "Bring the ask back here" is SEEN with focus');
      await p().keyboard.press('Enter');
    }
    await p().waitForSelector(sel('di-mr-composer'), { state: 'detached', timeout: 10_000 });
    await p().waitForTimeout(300);
    const out = await exposure({});
    const outWords = await p().locator(sel('di-new-ask')).inputValue();
    const outMarks = await p().evaluate(() => document.querySelectorAll('[data-di-covered]').length);
    record(`isolation route out ${size}`, { focus: out.focusTarget, seen: out.exposed.focus, outWords, outMarks });
    assert.equal(outWords, EXACT, 'CONTRACT (route out): the page box holds the EXACT words');
    assert.equal(out.exposed.focus.ok, true, `CONTRACT (route out): focus lands somewhere SEEN (${out.focusTarget})`);
    assert.ok(out.focusTarget !== null && out.focusTarget !== 'BODY', `CONTRACT (route out): focus lands on a control (${out.focusTarget})`);
    // the DESTINATION by identity: Close returns focus to its opener (K1, F4); bring-back puts it in the box (F4, diScanNewMissionStatesBrowser)
    const destination = vp.width === 1280 ? 'di-new-open-workspace' : 'di-new-ask';
    assert.equal(out.focusTarget, destination, `CONTRACT (route out): focus lands ON ${destination} (${out.focusTarget})`);
    assert.equal(outMarks, 0, 'CONTRACT (K1): no control is left isolated once nothing covers the stage');
    // each INTRODUCED destination, by real keys: SEEN with focus, and its action works; then the route BACK keeps the exact words
    for (const d of INTRODUCED[size].filter((x) => x.id === 'di-new-recent-ask')) {
      const n = await keyTo(d.id, d.conversation);
      assert.ok(n > 0, `CONTRACT: ${d.id} is reached by Tab`);
      assert.equal((await exposure({})).exposed.focus.ok, true, `CONTRACT: ${d.id} is SEEN with focus`);
      await p().keyboard.press('Enter');
      await p().waitForFunction((c) => document.querySelector(`[data-testid="di-new-recent-ask"][data-conversation="${c}"]`)?.getAttribute('aria-current') === 'true', d.conversation, { timeout: 20_000 });
      assert.equal(await p().locator(sel('di-new-ask')).inputValue(), EXACT, 'CONTRACT: the pick keeps the EXACT words in the box');
    }
    const n = await keyTo('di-new-open-workspace');
    assert.ok(n > 0, 'CONTRACT (route back): "Review requests" is reached by Tab');
    assert.equal((await exposure({})).exposed.focus.ok, true, 'CONTRACT (route back): "Review requests" is SEEN with focus');
    await p().keyboard.press('Enter');
    await p().waitForSelector('.di-mr:not([hidden])', { timeout: 10_000 });
    await p().click(sel('di-mr-tab-ask'));
    await p().waitForSelector(sel('di-mr-composer'), { timeout: 10_000 });
    assert.equal(await p().locator(sel('di-mr-composer')).inputValue(), EXACT, 'CONTRACT (route back): the workspace opens again holding the EXACT words');
  }, STEPS_MS);

test("ISOLATION RESCUE at 1440x900, then the window narrows to 1280x800: a FOCUSED stage control that BECOMES covered hands focus to the workspace's own heading, a labelled landing beside its exits, SEEN; never the page body", async () => {
  await bootPicked({ width: 1440, height: 900 });
  await openWorkspaceWithTheAsk();
  const n = await keyTo('di-new-ask-bring-back');
  const before = await exposure({});
  await p().setViewportSize({ width: 1280, height: 800 });
  await p().waitForTimeout(800);
  const after = await exposure({ heading: sel('di-mr-heading') });
  const bringBackCovered = await p().evaluate(() => {
    const b = document.querySelector('[data-testid="di-new-ask-bring-back"]')!.getBoundingClientRect();
    const w = document.querySelector('.di-mr:not([hidden])')!.getBoundingClientRect();
    return b.left < w.right && b.right > w.left;
  });
  record('isolation rescue', { n, before: before.focusTarget, beforeSeen: before.exposed.focus, after: after.focusTarget, afterSeen: after.exposed.focus, bringBackCovered });
  assert.ok(n > 0, 'STATE: "Bring the ask back here" was reached by Tab');
  assert.equal(before.focusTarget, 'di-new-ask-bring-back', 'STATE: it has focus');
  assert.equal(before.exposed.focus.ok, true, 'STATE: it is SEEN at 1440x900');
  assert.equal(bringBackCovered, true, 'STATE: at 1280x800 the workspace covers it');
  assert.equal(after.focusTarget, 'di-mr-heading', `CONTRACT (rescue): focus moved to the workspace's own heading (${after.focusTarget})`);
  assert.equal(after.exposed.focus.ok, true, `CONTRACT (rescue): where focus landed is SEEN (${after.exposed.focus.why})`);
}, STEPS_MS);

test('CLOSE ONTO A COVERED OPENER at 1440x900 (R22, the isolation\'s third defect): "Review requests" opens the request workspace, which then COVERS it; the workspace closed by its own Close, by REAL keys: focus lands ON the opener, SEEN, never the page body', async () => {
  await bootPicked({ width: 1440, height: 900 });
  await openWorkspaceWithTheAsk();
  const held = await p().evaluate(() => {
    const opener = document.querySelector<HTMLElement>('[data-testid="di-new-open-workspace"]');
    const w = document.querySelector<HTMLElement>('.di-mr:not([hidden])')!.getBoundingClientRect();
    const r = opener?.getBoundingClientRect() ?? null;
    return { opener: opener !== null, covered: r !== null && r.left < w.right && r.right > w.left, isolated: opener?.hasAttribute('data-di-covered') === true && opener.hasAttribute('inert') };
  });
  const n = await keyTo('di-mr-close');
  await p().keyboard.press('Enter');
  await p().waitForSelector('.di-mr:not([hidden])', { state: 'detached', timeout: 10_000 }).catch(() => undefined);
  await p().waitForTimeout(500);
  const after = await exposure({});
  const marksLeft = await p().evaluate(() => document.querySelectorAll('[data-di-covered]').length);
  const workspaceShown = await p().evaluate(() => document.querySelector('.di-mr:not([hidden])') !== null);
  record('close onto a covered opener 1440x900', { held, n, focus: after.focusTarget, seen: after.exposed.focus, marksLeft, workspaceShown });
  // STATE preconditions, each REACHED first: the opener exists, the workspace COVERED it, the isolation had marked it, Close was reached by Tab, and it closed
  assert.equal(held.opener, true, 'STATE: "Review requests" (the opener) is on the page');
  assert.equal(held.covered, true, 'STATE: the request workspace COVERS the opener at 1440x900');
  assert.equal(held.isolated, true, 'STATE: the isolation had marked the covered opener inert');
  assert.ok(n > 0, 'STATE: the workspace\'s own Close was reached by Tab');
  assert.equal(workspaceShown, false, 'STATE: the workspace closed');
  // THE CONTRACT: focus back ON the opener, SEEN; never the page body; no mark left behind
  assert.equal(after.focusTarget, 'di-new-open-workspace', `CONTRACT: focus lands ON the opener (${after.focusTarget})`);
  assert.equal(after.exposed.focus.ok, true, `CONTRACT: where focus landed is SEEN (${after.exposed.focus.why})`);
  assert.equal(marksLeft, 0, 'CONTRACT (K1): no control is left isolated once nothing covers the stage');
}, STEPS_MS);

// ── X1 (R22): the SIX covered slivers the X1 check CONFIRMED dead and enabled-looking, PINNED BY NAME (never re-enumerated: a repair can change an
// enumeration's own criterion, and its zero would then mean it stopped looking). 1280x800 is a robustness size; 1440x900 is a shipping size.
const X1_POINTS = [
  { width: 1280, height: 800, id: 'di-new-close-conversation' },
  { width: 1280, height: 800, id: 'di-new-draft-open' },
  { width: 1280, height: 800, id: 'di-new-new-conversation' },
  { width: 1280, height: 800, id: 'di-new-ask-bring-back' },
  { width: 1440, height: 900, id: 'di-new-draft-open' },
  { width: 1440, height: 900, id: 'di-new-new-conversation' },
] as const;
/** The person's state only (the X1 check's signature). */
const x1State = () =>
  p().evaluate(() => ({
    workspaceShown: document.querySelector('.di-mr:not([hidden])') !== null,
    heldAsk: document.querySelector('[data-testid="di-new-body"]')?.getAttribute('data-ask') ?? null,
    conversationShown: document.querySelector('[data-testid="di-new-close-conversation"]') !== null,
    picked: [...document.querySelectorAll('[data-testid="di-new-recent-ask"][aria-current="true"]')].map((el) => el.getAttribute('data-conversation')),
    turns: document.querySelectorAll('.di-new-turns > li').length,
    expanded: [...document.querySelectorAll('[aria-expanded]')].map((el) => `${el.getAttribute('data-testid')}=${el.getAttribute('aria-expanded')}`).sort(),
    workspaceTab: document.querySelector('.di-mr [aria-selected="true"]')?.getAttribute('data-testid') ?? null,
  }));
/** A stage control and its sliver past the shown workspace's edge: its effective opacity, its mark, and what a pointer at the sliver's centre reaches. */
const x1Sliver = (id: string) =>
  p().evaluate((tid) => {
    const el = document.querySelector<HTMLElement>(`.di-scan-stage [data-testid="${tid}"]`);
    if (el === null) return null;
    const cover = document.querySelector<HTMLElement>('.di-mr:not([hidden])');
    const off = document.querySelector<HTMLElement>('.di-scan-stage button:disabled');
    const r = el.getBoundingClientRect();
    let effective = 1;
    for (let a: Element | null = el; a !== null; a = a.parentElement) effective *= Number(getComputedStyle(a).opacity);
    const w = cover?.getBoundingClientRect() ?? null;
    const span =
      w === null ? null : r.left < w.left && r.right > w.left ? [Math.max(r.left, 0), w.left] : r.left < w.right && r.right > w.right ? [w.right, Math.min(r.right, innerWidth)] : null;
    const top = Math.max(r.top, 0);
    const bottom = Math.min(r.bottom, innerHeight);
    const x = span === null ? r.left + r.width / 2 : (span[0] + span[1]) / 2;
    const y = (top + bottom) / 2;
    const hit = document.elementFromPoint(x, y);
    return {
      span,
      point: [x, y],
      label: el.innerText.trim(),
      marked: el.hasAttribute('data-di-covered') && el.hasAttribute('inert'),
      unobstructed: hit !== null && (hit === el || el.contains(hit) || hit.contains(el)),
      hitsItself: hit !== null && (hit === el || el.contains(hit)),
      effective: Math.round(effective * 1000) / 1000,
      unavailable: off === null ? null : Number(getComputedStyle(off).opacity),
      clip:
        span === null
          ? null
          : { x: Math.floor(span[0]), y: Math.floor(top), width: Math.max(1, Math.ceil(span[1]) - Math.floor(span[0])), height: Math.max(1, Math.ceil(bottom) - Math.floor(top)) },
    };
  }, id);
for (const pt of X1_POINTS)
  test(`X1 (R22) at ${pt.width}x${pt.height} (${pt.width === 1280 ? 'a robustness' : 'a shipping'} size): "${pt.id}", BY NAME: its sliver past the request workspace's edge is drawn UNAVAILABLE with its label, the STATED REASON is seen, a real pointer on it changes nothing, and its action WORKS by the route the reason names (Close the requests, then act), the exact words kept`, async () => {
    const size = `${pt.width}x${pt.height}`;
    const control = `.di-scan-stage ${sel(pt.id)}`;
    await bootPicked({ width: pt.width, height: pt.height });
    await openWorkspaceWithTheAsk();
    await p().waitForTimeout(300);
    const held = await x1Sliver(pt.id);
    // RENDERED: the sliver as drawn, against the control forced to full opacity (restored at once to its exact prior style attribute)
    let rendered: boolean | null = null;
    if (held !== null && held.clip !== null) {
      const asDrawn = await p().screenshot({ clip: held.clip, scale: 'css', animations: 'disabled' });
      const prior = await p().evaluate((tid) => {
        const el = document.querySelector<HTMLElement>(`.di-scan-stage [data-testid="${tid}"]`)!;
        const v = el.getAttribute('style');
        el.style.setProperty('opacity', '1', 'important');
        return v;
      }, pt.id);
      const atFull = await p().screenshot({ clip: held.clip, scale: 'css', animations: 'disabled' });
      await p().evaluate(([tid, v]) => {
        const el = document.querySelector<HTMLElement>(`.di-scan-stage [data-testid="${tid}"]`)!;
        if (v === null) el.removeAttribute('style');
        else el.setAttribute('style', v);
      }, [pt.id, prior] as [string, string | null]);
      rendered = !asDrawn.equals(atFull);
    }
    const reason = await exposure({ reason: sel('di-mr-covers-stage-note') });
    const reasonWords = (await p().locator(sel('di-mr-covers-stage-note')).innerText().catch(() => '')).trim();
    const before = await x1State();
    if (held !== null) await p().mouse.click(held.point[0], held.point[1]);
    await p().waitForTimeout(600);
    const afterPointer = await x1State();
    record(`x1 held ${size} ${pt.id}`, { held, rendered, reason: reason.exposed.reason, reasonWords, before, afterPointer });
    // STATE preconditions, each reached first
    assert.ok(held !== null, `STATE: "${pt.id}" is on the stage at ${size}`);
    assert.ok(held.span !== null, `STATE: "${pt.id}" still STRADDLES the workspace edge at ${size}; a layout change that removes the sliver is reviewed, never passed`);
    assert.equal(held.unobstructed, true, `STATE: the sliver centre is not under the workspace (${JSON.stringify(held.point)})`);
    assert.equal(held.marked, true, 'STATE: the isolation marked it');
    assert.ok(held.unavailable !== null, 'STATE: the stage unavailable treatment was read (a disabled stage control)');
    // CONTRACT: honest while covered
    assert.equal(held.effective, held.unavailable, `CONTRACT (drawn): its EFFECTIVE opacity is the stage unavailable treatment (${held.effective})`);
    assert.ok(held.label.length > 0, 'CONTRACT (label): covered, it still reads what it is');
    assert.equal(rendered, true, 'CONTRACT (rendered): the dim is DRAWN on its sliver');
    assert.equal(reason.exposed.reason.ok, true, `CONTRACT (reason): the stated reason is SEEN (${reason.exposed.reason.why})`);
    assert.equal(reasonWords, COVERS_STAGE_WORDS, 'CONTRACT (reason): its exact words, naming the route');
    assert.deepEqual(afterPointer, before, 'CONTRACT (pointer): a real pointer on the covered sliver changes nothing (a covered pointer is never enabled)');
    // INSTRUMENT: the rendered proof forced the control's opacity and put it back; no inline opacity is left (Chrome may keep an EMPTY style attribute)
    const leftover = await p().evaluate((tid) => document.querySelector<HTMLElement>(`.di-scan-stage [data-testid="${tid}"]`)?.style.getPropertyValue('opacity') ?? null, pt.id);
    assert.equal(leftover, '', 'INSTRUMENT: the rendered proof left no inline opacity on the control');
    // THE ROUTE the reason names, by a real pointer: Close the requests (DESIGN.md: the isolated controls' route is to LEAVE the held state by its
    // labelled exit, which restores each of them with the ask's exact words). Each presence below is an IMMEDIATE count, never a wait.
    assert.equal(await p().locator(sel('di-mr-close')).count(), 1, 'STATE: the workspace has its own Close');
    await p().click(sel('di-mr-close'));
    await p().waitForSelector('.di-mr:not([hidden])', { state: 'detached', timeout: 10_000 });
    await p().waitForTimeout(400);
    const back = await p().locator(sel('di-new-ask')).inputValue();
    assert.equal(back, BEFORE_ASK, 'ROUTE (words): closing the requests brings the ask back to the box, word for word');
    if (pt.id === 'di-new-ask-bring-back') {
      // its own job is what the route did: the ask is back here, so the control is (rightly) no longer offered
      const offered = await p().locator(control).count();
      record(`x1 route ${size} ${pt.id}`, { back, offered, after: await x1State() });
      assert.equal(offered, 0, 'ROUTE (acts): the ask is back here, so "Bring the ask back here" is no longer offered');
      return;
    }
    const live = await x1Sliver(pt.id);
    assert.ok(live !== null, 'ROUTE: the control is on the stage once the requests are closed');
    assert.equal(held.label, live.label, 'CONTRACT (label): covered, it reads exactly what it reads uncovered');
    assert.equal(live.marked, false, 'ROUTE: the control is no longer isolated');
    assert.equal(live.hitsItself, true, 'ROUTE: a pointer reaches the control itself');
    assert.equal(live.effective, 1, 'ROUTE: it is drawn available');
    await p().click(control);
    await p().waitForTimeout(800);
    const acted = await x1State();
    if (pt.id === 'di-new-draft-open') {
      const composers = await p().locator(sel('di-mr-composer')).count();
      const words = composers === 1 ? await p().locator(sel('di-mr-composer')).inputValue() : null;
      record(`x1 route ${size} ${pt.id}`, { back, live, acted, composers, words });
      assert.equal(acted.workspaceShown, true, 'ROUTE (acts): the draft opens in the request workspace');
      assert.equal(acted.workspaceTab, 'di-mr-tab-ask', 'ROUTE (acts): on its Ask tab');
      assert.equal(composers, 1, 'ROUTE (acts): its composer is drawn');
      assert.equal(words, BEFORE_ASK, 'ROUTE (words): the exact words open with it');
    } else {
      const words = await p().locator(sel('di-new-ask')).inputValue();
      record(`x1 route ${size} ${pt.id}`, { back, live, acted, words });
      assert.deepEqual(acted.picked, [], 'ROUTE (acts): the picked conversation is let go');
      assert.equal(acted.conversationShown, false, 'ROUTE (acts): the conversation is closed on the page');
      // "Close this conversation" keeps the ask the SAME input; "Start a new conversation" clears the box, as it always has (DiScanPages.tsx), so no
      // words are claimed after it
      if (pt.id === 'di-new-close-conversation') assert.equal(words, BEFORE_ASK, 'ROUTE (words): the ask stays word for word');
    }
  }, STEPS_MS);

// ── CASE B (R22, advisory B): the page head's sticky OFFSET changing with NO change of size. THE SIMULATION, DISCLOSED: no process is restarted; the
// restart is SIMULATED AT THE MESSAGE BOUNDARY (a --fixture-preview office process adds fixtureMode: true to every message and a normal one to none,
// server/src/di/diOffice.ts; the webview replaces its office state with each message, useExtensionMessages.ts). The disconnect and reconnect are NOT exercised.
const caseBHead = () =>
  p().evaluate(() => {
    const h = document.querySelector<HTMLElement>('[data-testid="di-new-mission"] .di-page-head')!;
    const r = h.getBoundingClientRect();
    const st = getComputedStyle(h);
    const stage = document.querySelector<HTMLElement>('[data-testid="di-new-mission"]')!.closest<HTMLElement>('.di-scan-stage')!;
    return {
      width: Math.round(r.width * 100) / 100,
      height: Math.round(r.height * 100) / 100,
      stickyTop: st.top,
      reserve: st.position === 'sticky' ? Math.ceil(Math.max(0, parseFloat(st.top) || 0) + r.height) : null,
      stagePadTop: getComputedStyle(stage).scrollPaddingTop,
      label: document.querySelector('[data-testid="di-fixture-banner-in-stage"]') !== null,
    };
  });
test(`CASE B (R22) at 760x700, STACKED: fixture mode turns ON with the SAME New Mission header mounted (a restart SIMULATED at the message boundary, disclosed): the stage reserves the head's NEW offset plus its height, read from the rendered strip, and a fresh arrival lands clear of the stuck strip`, async () => {
  await boot({ width: 760, height: 700 });
  await p().waitForTimeout(400);
  const before = await caseBHead();
  const mounted = await p().evaluate(() => {
    const h = document.querySelector('[data-testid="di-new-mission"] .di-page-head');
    (window as unknown as { __caseBHead?: Element | null }).__caseBHead = h;
    return h !== null;
  });
  // what a restarted --fixture-preview office process sends: the same office, fixtureMode true (SIMULATED at the message boundary)
  socket!.send(JSON.stringify({ ...office, fixtureMode: true }));
  await p().waitForSelector(sel('di-fixture-banner-in-stage'), { timeout: 20_000 });
  await p().waitForTimeout(600);
  const after = await caseBHead();
  const sameHead = await p().evaluate(() => document.querySelector('[data-testid="di-new-mission"] .di-page-head') === (window as unknown as { __caseBHead?: Element | null }).__caseBHead);
  // a fresh send with the page scrolled down (the ask box dragged to the scroller's height, as in CASE (d))
  const SCROLLER = '[data-testid="di-scan-stage"]';
  await p().locator(sel('di-new-ask')).fill('SYNTHETIC a first ask, sent after fixture mode turned on');
  await p().evaluate((sc) => {
    const ta = document.querySelector<HTMLTextAreaElement>('[data-testid="di-new-ask"]')!;
    ta.style.height = `${document.querySelector<HTMLElement>(sc)!.clientHeight}px`;
  }, SCROLLER);
  await p().waitForTimeout(200);
  await p().evaluate((sc) => {
    const m = document.querySelector<HTMLElement>(sc)!;
    m.scrollTop = m.scrollHeight;
  }, SCROLLER);
  await p().waitForTimeout(200);
  await p().click(sel('di-new-send'));
  await p().waitForFunction(() => document.querySelectorAll('.di-new-turns > li').length >= 1, undefined, { timeout: 20_000 });
  await p().waitForTimeout(400);
  const landed = await p().evaluate(() => {
    const last = [...document.querySelectorAll<HTMLElement>('.di-new-turns > li')].pop()!.getBoundingClientRect();
    const head = document.querySelector<HTMLElement>('[data-testid="di-new-mission"] .di-page-head')!.getBoundingClientRect();
    const label = document.querySelector<HTMLElement>('[data-testid="di-fixture-banner-in-stage"]')?.getBoundingClientRect() ?? null;
    const stuckBottom = Math.max(head.bottom, label?.bottom ?? 0);
    return { newestTop: Math.round(last.top), stuckBottom: Math.round(stuckBottom), underStripPx: Math.max(0, Math.round(stuckBottom - last.top)) };
  });
  await shot('b2es-760x700-case-b-fixture-on.png');
  record('case B 760x700', { simulation: 'SIMULATED at the message boundary; no process restarted; the disconnect and reconnect are not exercised', before, after, mounted, sameHead, landed });
  // STATE preconditions, each reached first: the SAME header, fixture mode OFF then ON, its size UNCHANGED, its sticky offset CHANGED
  assert.equal(mounted && sameHead, true, 'STATE: the SAME New Mission header stayed mounted');
  assert.equal(before.label, false, 'STATE: fixture mode was OFF first');
  assert.equal(after.label, true, 'STATE: fixture mode is ON after the office message');
  assert.deepEqual([after.width, after.height], [before.width, before.height], 'STATE: the head DIMENSIONS are UNCHANGED');
  assert.notEqual(after.stickyTop, before.stickyTop, `STATE: the head sticky OFFSET CHANGED (${before.stickyTop} to ${after.stickyTop})`);
  assert.ok(after.reserve !== null, 'STATE: the head is sticky');
  // THE CONTRACTS
  assert.equal(after.stagePadTop, `${after.reserve}px`, `CONTRACT: the stage reserves the head NEW offset plus its height (${after.stagePadTop}, the strip needs ${after.reserve}px)`);
  assert.equal(landed.underStripPx, 0, `CONTRACT: a fresh arrival lands clear of the stuck strip (${landed.underStripPx}px under it)`);
}, STEPS_MS);

test("ORDER (the Lead's condition one), MEASURED at 1440x900: the panes' DOM order against the order the grid draws them, and the REAL Tab sequence, in the split and while the request workspace holds the ask", async () => {
  await bootPicked({ width: 1440, height: 900 });
  const split = await paneOrder();
  const splitTabs = await tabRuns();
  await openWorkspaceWithTheAsk();
  const held = await paneOrder();
  const heldTabs = await tabRuns();
  record('ORDER 1440x900', { split, splitTabs, held, heldTabs });
  // STATES, the design as built (named, not ruled): the DOM holds the conversation THEN Recent; the split DRAWS Recent first (a visual reorder, by grid placement);
  // while the workspace holds the ask, the drawn order is the DOM's
  assert.equal(split.domConversationFirst, true, 'STATE: in the DOM the conversation comes BEFORE Recent');
  assert.ok(split.recent.left < split.conversation.left, 'STATE: the split draws Recent LEFT of the conversation (a visual reorder)');
  assert.equal(held.ask, 'elsewhere', 'STATE: the request workspace holds the ask');
  assert.ok(held.conversation.left < held.recent.left, 'STATE: while it does, the drawn order is the DOM order');
}, STEPS_MS);

test("B2e FALLBACK (rule 7) at 760x700, a 560px stage under the 604px threshold: the panes STACK (Recent below the conversation) and the page scrolls as one column", async () => {
  await bootPicked({ width: 760, height: 700 });
  const g = await geometry();
  await shot('b2es-760x700-fallback-first-draw.png');
  record('760x700 fallback', { geometry: g });
  assert.ok(g.stageContentWidth < 604, `STATE: the stage is under the threshold (${g.stageContentWidth}px)`);
  assert.ok(g.side !== null && g.main !== null && g.side.top >= g.main.top + g.main.height - 1, 'CONTRACT (fallback): Recent stacks BELOW the conversation');
  assert.equal(g.stageScrolls, true, 'CONTRACT (fallback): the page scrolls as one column');
}, STEPS_MS);
