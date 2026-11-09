/**
 * B3, FIRST PRIORITY: THE MINIMIZED-SURFACE STRIP AND ITS RESTORE CONTROLS (NOT a canonical test while it is in the evidence tree; the candidate that
 * would add it as a permanent test is b3-tray/scripts/b3_tray_candidate.py, NOT APPLIED before B2e is accepted).
 *
 * THE CLAIM: for each of the four surfaces the strip serves, the Restore control is reachable by SIGHT, by POINTER and by KEYBOARD, each asserted in its
 * own test; and restoring returns the RETAINED STATE (byte for byte) and puts focus on the control that LAST HELD IT (by identity), never the page.
 *
 * PINNED IDENTITIES, never an enumeration: requests (scan face, New Mission open: the occluded case, measured PRESENT AT HEAD in b2e-restorecheck),
 * the office panel (scan face, at Home and, in two panes, with a screen open), Reception and Code Review (the OPERATIONAL face: the scan face has no
 * inbox dialog, DiOfficePanel.tsx, and renders the review in the Code Diff editor, App.tsx). Three sizes: 1280x800 a robustness size, 1440x900 and
 * 1600x1000 the shipping sizes. Each test STATES its preconditions (the chip is in the strip, which has the product's own width; the surface is hidden;
 * the right face is mounted; for a screen-open case a screen is shown) and FAILS LOUDLY if the geometry moves, so no case can go quietly vacuous.
 *
 * HONEST BOUNDS: (1) SEEN here is a compact nine-point hit-test inside the window, not diB2eSplitBrowser's calibrated helper; (2) the fixture-mode label
 * strip over the strip's top 30px is NOT measured (no fixture mode); (3) a COVERING request workspace over the strip (a narrow window) is the covered-
 * region audit, not this claim; (4) pointer and keyboard restore are asserted on the surface as it is left by a POINTER Minimize.
 * Writes nothing outside the page. Records with the prefix B3TR.
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

import type { DiCodeReview, DiOfficeState } from '../../core/src/messages.ts';
import { DI_MINIMIZED_STRIP_WIDTH_PX, DI_SCAN_TWO_PANE_MIN_PX } from '../src/constants.ts';
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
const REVIEW = JSON.parse(fs.readFileSync(path.join(root, 'test/fixtures/di-office-state-code-review-v2.FIXTURE.json'), 'utf-8')) as {
  states: Record<'live', DiOfficeState>;
  replies: { overview: DiCodeReview; files: Record<string, DiCodeReview> };
};
const REVIEW_CHARACTER = REVIEW.states.live.missions.missions[0].members.members[0].characterId!;
const OP_LIVE = (JSON.parse(fs.readFileSync(path.join(root, 'test/fixtures/di-office-state-transitions-v2.FIXTURE.json'), 'utf-8')) as { states: Record<'live', DiOfficeState> }).states.live;
const METROPOLIS = fs.readFileSync(path.join(root, '..', 'packaging/di/worlds/living-metropolis.json'), 'utf-8');
const VERSION = (JSON.parse(fs.readFileSync(path.join(root, '..', 'package.json'), 'utf-8')) as { version: string }).version;
const SHOTS = process.env.DI_B3TR_SHOTS ?? null;
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
let urlOp = '';
/** I2: the next boot() / bootHome() sends the office as a FIXTURE PREVIEW (the same office, fixtureMode true). */
let fixtureMode = false;
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
        if (msg.type === 'diCodeReviewRead') {
          // the fake office's reply, as diCodeReview.test.ts relays it: the overview for a read with no fileId, else the file's reply, echoing the requestId
          const stored = msg.fileId === undefined ? REVIEW.replies.overview : Object.values(REVIEW.replies.files).find((r) => r.fileId === msg.fileId);
          if (stored !== undefined) ws.send(JSON.stringify(typeof msg.requestId === 'string' ? { ...stored, requestId: msg.requestId } : stored));
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
  urlOp = url.replace('di-app-scan.html', 'di-app.html');
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
    console.warn(`SKIPPED (B3 tray): ${unavailable}`);
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
const record = (name: string, r: unknown) => console.log(`B3TR ${name}: ${JSON.stringify(r)}`);

async function shot(name: string): Promise<void> {
  if (SHOTS === null) return;
  const file = path.join(SHOTS, name);
  assert.ok(!fs.existsSync(file), `refusing to overwrite ${file}`);
  await p().screenshot({ path: file });
}

async function boot(viewport: { width: number; height: number }): Promise<void> {
  const connected = new Promise<ServerSocket>((resolve) => (resolveConnected = resolve));
  page = await browser!.newPage({ viewport });
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await page.goto(url, { waitUntil: 'domcontentloaded' });
  await connected;
  socket!.send(JSON.stringify({ type: 'providerCapabilities', readingTools: ['Read', 'Grep', 'Glob', 'WebFetch', 'WebSearch'], subagentToolNames: ['Task', 'Agent'] }));
  socket!.send(JSON.stringify({ type: 'settingsLoaded', soundEnabled: false, lastSeenVersion: VERSION, extensionVersion: VERSION }));
  socket!.send(JSON.stringify(fixtureMode ? { ...office, fixtureMode: true } : office));
  await p().waitForSelector(sel('di-rail-new-orchestrator'));
  await p().click(sel('di-rail-new-orchestrator'));
  await p().waitForSelector(sel('di-new-mission'));
  for (const c of LONG) await chipFor(c.id).waitFor({ timeout: 20_000 });
}




// ── B3: the strip's Restore controls ─────────────────────────────────────────────────────────────────────────────────────────────────────────────────
const ASK = 'SYNTHETIC an ask typed before the request workspace opens';
const WORDS = 'SYNTHETIC words that must come back byte for byte,  two  spaces, and a tab\there.';
const SIZES = [
  { width: 1280, height: 800, role: 'a robustness size' },
  { width: 1440, height: 900, role: 'a shipping size' },
  { width: 1600, height: 1000, role: 'a shipping size' },
] as const;
type Vp = { width: number; height: number; role: string };

/** The office with no screen open (Home), the same page as boot() without the rail click. */
async function bootHome(viewport: { width: number; height: number }): Promise<void> {
  const connected = new Promise<ServerSocket>((resolve) => (resolveConnected = resolve));
  page = await browser!.newPage({ viewport });
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await page.goto(url, { waitUntil: 'domcontentloaded' });
  await connected;
  socket!.send(JSON.stringify({ type: 'providerCapabilities', readingTools: ['Read', 'Grep', 'Glob', 'WebFetch', 'WebSearch'], subagentToolNames: ['Task', 'Agent'] }));
  socket!.send(JSON.stringify({ type: 'settingsLoaded', soundEnabled: false, lastSeenVersion: VERSION, extensionVersion: VERSION }));
  socket!.send(JSON.stringify(fixtureMode ? { ...office, fixtureMode: true } : office));
  await p().waitForSelector(sel('di-rail-new-orchestrator'));
  await p().waitForSelector(sel('di-office-panel'));
}
/** The OPERATIONAL face (di-app.html), as diConstrainedExposure.test.ts and diCodeReview.test.ts boot it. */
async function bootOp(viewport: { width: number; height: number }, state: DiOfficeState, layout: boolean): Promise<void> {
  const connected = new Promise<ServerSocket>((resolve) => (resolveConnected = resolve));
  page = await browser!.newPage({ viewport });
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await page.goto(urlOp, { waitUntil: 'domcontentloaded' });
  await connected;
  socket!.send(JSON.stringify(state));
  if (layout) {
    socket!.send(JSON.stringify({ type: 'layoutLoaded', layout: JSON.parse(METROPOLIS) }));
    await p().waitForFunction(
      () => {
        const o = (window as unknown as { __diHarnessOffice?: { characters: Map<number, unknown>; layout: { cols: number } } }).__diHarnessOffice;
        return o !== undefined && o.characters.size >= 4 && o.layout.cols === 352;
      },
      undefined,
      { timeout: 60_000 },
    );
  }
  await p().waitForSelector(sel('di-office-panel'), { timeout: 30_000 });
}
async function bootReview(viewport: { width: number; height: number }): Promise<void> {
  await bootOp(viewport, REVIEW.states.live, false);
  await p().waitForFunction(
    (i) => {
      const o = (window as unknown as { __diHarnessOffice?: { characters: Map<number, { matrixEffect: string | null }> } }).__diHarnessOffice;
      const c = o?.characters.get(i);
      return c !== undefined && c.matrixEffect !== 'despawn';
    },
    REVIEW_CHARACTER,
    { timeout: 30_000 },
  );
  await p().evaluate((i) => {
    const o = (window as unknown as { __diHarnessOffice: { selectedAgentId: number | null; cameraFollowId: number | null } }).__diHarnessOffice;
    o.selectedAgentId = i;
    o.cameraFollowId = i;
  }, REVIEW_CHARACTER);
  await p().waitForSelector(sel('di-selected-member'), { timeout: 15_000 });
  await p().click(sel('di-open-code-review'));
  await p().waitForSelector(sel('di-review-dialog'));
  await p().waitForSelector(sel('di-review-tree'), { timeout: 20_000 });
}

/** Who has focus: its testid and, for a row, its path or conversation. BODY when nothing does. */
const activeKey = () =>
  p().evaluate(() => {
    const a = document.activeElement as HTMLElement | null;
    if (a === null || a === document.body) return 'BODY';
    return `${a.getAttribute('data-testid') ?? a.tagName.toLowerCase()}${a.getAttribute('data-path') ?? a.getAttribute('data-conversation') ?? ''}`;
  });
/** The retained state of a surface, as the page holds it (fields, pressed/selected/expanded/current states, scroll offsets). No text: some of it is time-relative. */
const snapshot = (root: string) =>
  p().evaluate((r) => {
    const el = document.querySelector<HTMLElement>(r);
    if (el === null) return null;
    const idOf = (e: Element) => `${e.getAttribute('data-testid') ?? e.tagName.toLowerCase()}${e.getAttribute('data-path') ?? e.getAttribute('data-conversation') ?? ''}`;
    return {
      fields: [...el.querySelectorAll<HTMLInputElement | HTMLTextAreaElement | HTMLSelectElement>('input, textarea, select')].map((f) => `${idOf(f)}=${f.value}`),
      states: [...el.querySelectorAll<HTMLElement>('[aria-pressed], [aria-selected], [aria-expanded], [aria-current], [aria-checked]')].map(
        (e) => `${idOf(e)}:${e.getAttribute('aria-pressed')}|${e.getAttribute('aria-selected')}|${e.getAttribute('aria-expanded')}|${e.getAttribute('aria-current')}|${e.getAttribute('aria-checked')}`,
      ),
      scroll: [el, ...el.querySelectorAll<HTMLElement>('*')].filter((e) => e.scrollTop > 0 || e.scrollLeft > 0).map((e) => `${idOf(e)}:${e.scrollTop},${e.scrollLeft}`),
    };
  }, root);
/** Is the surface drawn: present and visible (a minimized surface stays mounted, hidden). */
const shownNow = (root: string) =>
  p().evaluate((r) => {
    const el = document.querySelector<HTMLElement>(r);
    return el !== null && el.checkVisibility({ checkVisibilityCSS: true });
  }, root);
/** SEEN: the box lies inside the window and nine points inside it all hit the control (or a descendant). What the centre hits is recorded. */
const seenAt = (testid: string) =>
  p().evaluate((tid) => {
    const el = document.querySelector<HTMLElement>(`[data-testid="${tid}"]`);
    if (el === null) return { present: false as const };
    const r = el.getBoundingClientRect();
    const inWindow = r.width > 0 && r.height > 0 && r.left >= 0 && r.top >= 0 && r.right <= innerWidth && r.bottom <= innerHeight;
    const pts: Array<[number, number]> = [];
    for (const fx of [0.15, 0.5, 0.85]) for (const fy of [0.15, 0.5, 0.85]) pts.push([r.left + r.width * fx, r.top + r.height * fy]);
    const hits = pts.map(([x, y]) => {
      const h = document.elementFromPoint(x, y);
      return h !== null && (h === el || el.contains(h));
    });
    const c = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2);
    return {
      present: true as const,
      box: [r.left, r.top, r.right, r.bottom].map((v) => Math.round(v * 100) / 100),
      centre: [r.left + r.width / 2, r.top + r.height / 2],
      inWindow,
      hits: hits.filter(Boolean).length,
      of: pts.length,
      centreHits: c === null ? null : (c.getAttribute('data-testid') ?? c.tagName.toLowerCase()),
      ok: inWindow && hits.every(Boolean),
    };
  }, testid);
/** The strip's own box and width, every SHOWN scan screen's box, and whether any of them overlaps the strip. */
const geometry = () =>
  p().evaluate(() => {
    const tray = document.querySelector<HTMLElement>('[data-testid="di-minimized-tray"]');
    const box = (e: Element) => {
      const r = e.getBoundingClientRect();
      return [Math.round(r.left * 100) / 100, Math.round(r.top * 100) / 100, Math.round(r.right * 100) / 100, Math.round(r.bottom * 100) / 100];
    };
    const tr = tray?.getBoundingClientRect() ?? null;
    const stages = [...document.querySelectorAll<HTMLElement>('.di-scan-stage')].filter((e) => e.getClientRects().length > 0);
    return {
      tray: tray === null ? null : box(tray),
      trayWidth: tr === null ? null : Math.round(tr.width * 100) / 100,
      stages: stages.map(box),
      overlap: tr === null ? null : stages.some((e) => { const r = e.getBoundingClientRect(); return r.left < tr.right && r.right > tr.left && r.top < tr.bottom && r.bottom > tr.top; }),
      scanMounted: document.querySelector('.di-scan') !== null,
      chips: tray === null ? null : tray.childElementCount,
    };
  });

interface Surface {
  id: string;
  label: string;
  face: 'scan' | 'operational';
  chip: string;
  minimize: string;
  root: string;
  /** R28: what the scan screen looks like AFTER the surface is minimized, asserted LOUDLY as a STATE precondition before any occlusion claim: 'drawn' (a screen is open and drawn, so "no screen is under the strip" has an object), 'hidden' (the screen goes with the minimized panel), 'none' (no screen in this case, no claim). */
  stageAfterMinimize: 'drawn' | 'hidden' | 'none';
  /** I2: the office is a fixture preview in this case. */
  fixture?: boolean;
  sizes: readonly Vp[];
  /** Boot the page, open the surface and leave real content focus in it; returns the id of the field the person typed WORDS into, if any. */
  open: (vp: Vp) => Promise<{ typedId: string | null }>;
}
/** R28: the STATE precondition for the stage after minimize. It FAILS LOUDLY at the precondition, never through to the claim: with 'drawn' an empty set of screens would make "no screen is under the strip" true for the wrong reason. Calibrated both ways by the CONTROL test below. */
function assertStageExpectation(expectation: Surface['stageAfterMinimize'], stages: number, who: string): void {
  if (expectation === 'drawn') assert.ok(stages >= 1, `STATE: ${who}: a scan screen is open and drawn (${stages} drawn)`);
  if (expectation === 'hidden') assert.equal(stages, 0, `STATE: ${who}: the screen is hidden with the minimized panel (${stages} drawn)`);
}
const SURFACES: Surface[] = [
  {
    id: 'requests', label: 'the request workspace', face: 'scan', chip: 'di-mr-restore', minimize: 'di-mr-minimize', root: '.di-mr:not([hidden])', stageAfterMinimize: 'drawn', sizes: SIZES,
    open: async (vp) => {
      await boot(vp);
      await p().locator(sel('di-new-ask')).fill(ASK);
      await p().click(sel('di-new-open-workspace'));
      await p().waitForSelector('.di-mr:not([hidden])', { timeout: 20_000 });
      await p().click(sel('di-mr-tab-ask'));
      await p().waitForSelector(sel('di-mr-composer'), { timeout: 10_000 });
      await p().locator(sel('di-mr-composer')).fill(WORDS);
      await p().locator(sel('di-mr-composer')).focus();
      return { typedId: 'di-mr-composer' };
    },
  },
  {
    id: 'office-home', label: 'the office panel, at Home', face: 'scan', chip: 'di-office-panel-collapsed', minimize: 'di-office-panel-minimize', root: sel('di-office-panel'), stageAfterMinimize: 'none', sizes: SIZES,
    open: async (vp) => {
      await bootHome(vp);
      await p().focus(sel('di-rail-new-orchestrator'));
      return { typedId: null };
    },
  },
  {
    id: 'office-stage', label: 'the office panel, with a screen open (two panes)', face: 'scan', chip: 'di-office-panel-collapsed', minimize: 'di-office-panel-minimize', root: sel('di-office-panel'), stageAfterMinimize: 'hidden',
    sizes: SIZES.filter((v) => v.width >= DI_SCAN_TWO_PANE_MIN_PX),
    open: async (vp) => {
      await boot(vp);
      await p().locator(sel('di-new-ask')).fill(WORDS);
      await p().locator(sel('di-new-ask')).focus();
      return { typedId: 'di-new-ask' };
    },
  },
  {
    id: 'reception', label: 'Reception', face: 'operational', chip: 'di-reception-restore', minimize: 'di-reception-minimize', root: sel('di-reception-inbox'), stageAfterMinimize: 'none', sizes: SIZES,
    open: async (vp) => {
      await bootOp(vp, OP_LIVE, true);
      await p().click(sel('di-open-reception'));
      await p().waitForSelector(sel('di-reception-inbox'), { timeout: 20_000 });
      await p().waitForTimeout(300);
      const search = p().locator(sel('di-reception-search'));
      if ((await search.count()) > 0) {
        await search.fill(WORDS);
        await search.focus();
        return { typedId: 'di-reception-search' };
      }
      await p().locator(`${sel('di-reception-inbox')} button:not(${sel('di-reception-minimize')}):not(${sel('di-reception-inbox-close')})`).first().focus();
      return { typedId: null };
    },
  },
  {
    id: 'review', label: 'Code Review', face: 'operational', chip: 'di-review-restore', minimize: 'di-review-minimize', root: sel('di-review-dialog'), stageAfterMinimize: 'none', sizes: SIZES,
    open: async (vp) => {
      await bootReview(vp);
      await p().locator(`${sel('di-review-tree')} ${sel('di-review-row')}`).first().click();
      await p().waitForTimeout(500);
      return { typedId: null };
    },
  },
  {
    id: 'requests-fixture', label: 'the request workspace, in FIXTURE MODE', face: 'scan', chip: 'di-mr-restore', minimize: 'di-mr-minimize', root: '.di-mr:not([hidden])', stageAfterMinimize: 'drawn', fixture: true,
    sizes: [...SIZES, { width: 900, height: 700, role: 'a narrow size: the label strip is drawn only once the chip is in the strip' }],
    open: async (vp) => {
      fixtureMode = true;
      try {
        await boot(vp);
      } finally {
        fixtureMode = false;
      }
      await p().locator(sel('di-new-ask')).fill(ASK);
      await p().click(sel('di-new-open-workspace'));
      await p().waitForSelector('.di-mr:not([hidden])', { timeout: 20_000 });
      await p().click(sel('di-mr-tab-ask'));
      await p().waitForSelector(sel('di-mr-composer'), { timeout: 10_000 });
      await p().locator(sel('di-mr-composer')).fill(WORDS);
      await p().locator(sel('di-mr-composer')).focus();
      return { typedId: 'di-mr-composer' };
    },
  },
];

/** B3 1B: the request workspace's Minimize is an item of its Options menu (the other surfaces keep a direct control): open the menu where there is one, then choose the item. */
async function clickMinimize(testid: string): Promise<void> {
  if (testid === 'di-mr-minimize' && (await p().locator(sel('di-mr-options')).count()) > 0) {
    await p().click(sel('di-mr-options'));
    await p().waitForSelector(sel(testid));
  }
  await p().click(sel(testid));
}

for (const S of SURFACES)
  for (const vp of S.sizes)
    for (const kind of ['sight', 'pointer', 'keyboard'] as const)
      test(`B3 TRAY, ${S.label}, at ${vp.width}x${vp.height} (${vp.role}), ${kind.toUpperCase()}: the Restore control is ${kind === 'sight' ? `SEEN (every one of nine points hits it${S.stageAfterMinimize === 'drawn' ? ', and no screen is under the strip' : ''})` : kind === 'pointer' ? 'restored by a REAL pointer at its centre' : 'reached by a REAL sequential key route, SEEN where focus lands, and restored by Enter'}; the retained state comes back byte for byte and focus lands on the control that last held it`, async () => {
        const size = `${vp.width}x${vp.height}`;
        const { typedId } = await S.open(vp);
        const key = await activeKey();
        const before = await snapshot(S.root);
        await clickMinimize(S.minimize);
        await p().waitForSelector(sel(S.chip), { timeout: 10_000 });
        await p().waitForTimeout(500);
        const geo = await geometry();
        const seen = await seenAt(S.chip);
        const hidden = !(await shownNow(S.root));
        await shot(`b3tr-${S.id}-${size}-${kind}-minimized.png`);
        // I2: where the fixture label is drawn, and whether any part of its text is under another element
        const fx = await p().evaluate(() => {
          const strip = document.querySelector<HTMLElement>('.di-scan[data-fixture-note="strip"] > .di-op-fixture-note');
          let covered = 0;
          let rects = 0;
          if (strip !== null) {
            const range = document.createRange();
            range.selectNodeContents(strip);
            for (const r of range.getClientRects()) {
              if (r.width < 1 || r.height < 1) continue;
              rects += 1;
              const h = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2);
              if (h === null || !(h === strip || strip.contains(h))) covered += 1;
            }
          }
          const box = strip === null ? null : [strip.getBoundingClientRect().left, strip.getBoundingClientRect().top, strip.getBoundingClientRect().right, strip.getBoundingClientRect().bottom];
          return { labels: document.querySelectorAll('[data-testid^="di-fixture-banner"]').length, stripDrawn: strip !== null, stripBox: box, textRects: rects, textCovered: covered };
        });
        record(`TRAY ${S.id} ${size} ${kind} minimized`, { key, geo, seen, hidden, typedId, fx });
        // STATE preconditions, each REACHED first; any of them failing means the geometry or the setup moved, never a pass
        assert.equal(geo.scanMounted, S.face === 'scan', `STATE: the ${S.face} face is the one mounted (the scan panel is ${geo.scanMounted ? '' : 'not '}on the page)`);
        assert.notEqual(key, 'BODY', 'STATE: real content focus was in the surface before it was minimized');
        assert.ok(before !== null, 'STATE: the surface was drawn and read before it was minimized');
        assert.equal(hidden, true, 'STATE: the surface is minimized (mounted, not drawn)');
        assert.equal(seen.present, true, `STATE: the Restore control (${S.chip}) is on the page`);
        assert.ok(seen.present && seen.inWindow, `STATE: the Restore control has a box inside the window (${seen.present ? JSON.stringify(seen.box) : '-'})`);
        assert.equal(geo.trayWidth, DI_MINIMIZED_STRIP_WIDTH_PX, `STATE: the strip has the product's own width (${DI_MINIMIZED_STRIP_WIDTH_PX}px)`);
        assertStageExpectation(S.stageAfterMinimize, geo.stages.length, S.label);
        if (S.fixture) assert.ok(fx.labels >= 1, `STATE: the office is a fixture preview (${fx.labels} fixture label(s) drawn)`);
        if (S.fixture && vp.width === 900) assert.equal(fx.stripDrawn, true, 'STATE: at 900x700 the fixture label is drawn as the panel strip once the chip is in the strip (the world is under 240px)');
        // THE CLAIM, by kind
        if (kind === 'sight') {
          assert.equal(seen.present && seen.ok, true, `CONTRACT (sight): every one of nine points in the Restore control hits it (${seen.present ? `${seen.hits}/${seen.of}; its centre hits ${seen.centreHits}` : '-'})`);
          if (S.stageAfterMinimize === 'drawn') assert.equal(geo.overlap, false, `CONTRACT (sight): no screen is under the strip (strip ${JSON.stringify(geo.tray)}, screens ${JSON.stringify(geo.stages)})`);
          if (S.fixture && fx.stripDrawn) assert.equal(fx.textCovered, 0, `CONTRACT (sight): no part of the fixture label's text is under another element (${fx.textCovered} of ${fx.textRects} text boxes are)`);
          return;
        }
        let route = 'pointer';
        if (kind === 'pointer') {
          assert.ok(seen.present, 'STATE: the control is present');
          await p().mouse.click(seen.centre[0], seen.centre[1]);
        } else {
          // the sequential focus starting point is the document's start; Tab until the control has focus
          await p().evaluate(() => {
            (document.activeElement as HTMLElement | null)?.blur();
            document.body.setAttribute('tabindex', '-1');
            document.body.focus();
            document.body.removeAttribute('tabindex');
          });
          let presses = 0;
          let reached = false;
          for (; presses < 300; presses += 1) {
            await p().keyboard.press('Tab');
            if ((await activeKey()) === S.chip) {
              reached = true;
              break;
            }
          }
          route = `${presses + 1} Tab presses`;
          assert.equal(reached, true, `CONTRACT (keyboard): the Restore control is reached by sequential keys (${presses} presses, then it was not)`);
          const where = await seenAt(S.chip);
          assert.equal(where.present && where.ok, true, `CONTRACT (keyboard): where focus lands is SEEN (${where.present ? `${where.hits}/${where.of}; its centre hits ${where.centreHits}` : '-'})`);
          await p().keyboard.press('Enter');
        }
        await p().waitForTimeout(800);
        const restored = await shownNow(S.root);
        const after = await snapshot(S.root);
        const focusAfter = await activeKey();
        record(`TRAY ${S.id} ${size} ${kind} restored`, { route, restored, after, focusAfter });
        assert.equal(restored, true, `CONTRACT (${kind}): the surface is drawn again`);
        assert.deepEqual(after, before, 'CONTRACT (retained state): fields, states and scroll come back byte for byte');
        if (typedId !== null) assert.ok(after !== null && after.fields.includes(`${typedId}=${WORDS}`), `CONTRACT (retained state): the words typed into ${typedId} come back exactly`);
        assert.equal(focusAfter, key, 'CONTRACT (focus): focus lands on the control that last held it, by identity');
      }, STEPS_MS);

// ── R28: the stage precondition, CALIBRATED BOTH WAYS (a precondition shown able to FAIL, not merely one that passed) ─────────────────────────────────────
test('B3 TRAY CONTROL (the stage precondition, calibrated both ways): zero stages FAIL a "drawn" surface and pass a "hidden" one; one passes "drawn" and FAILS "hidden"; "none" claims nothing', () => {
  assert.throws(() => assertStageExpectation('drawn', 0, 'a control'), /a scan screen is open and drawn \(0 drawn\)/);
  assert.doesNotThrow(() => assertStageExpectation('drawn', 1, 'a control'));
  assert.doesNotThrow(() => assertStageExpectation('hidden', 0, 'a control'));
  assert.throws(() => assertStageExpectation('hidden', 1, 'a control'), /hidden with the minimized panel \(1 drawn\)/);
  assert.doesNotThrow(() => assertStageExpectation('none', 0, 'a control'));
  assert.doesNotThrow(() => assertStageExpectation('none', 3, 'a control'));
});

// ── I3: the OWED allocation measurement: New Mission's panes with the requests MINIMIZED against the same page with NO chip ───────────────────────────────
const SPLIT_THRESHOLD = Number(/@container scanstage \(width < (\d+)px\)/.exec(fs.readFileSync(path.join(root, 'src/components/diScanPages.css'), 'utf-8'))?.[1]);
const panes = () =>
  p().evaluate(() => {
    const box = (e: Element | null) => {
      if (e === null) return null;
      const r = e.getBoundingClientRect();
      return { left: Math.round(r.left * 100) / 100, top: Math.round(r.top * 100) / 100, right: Math.round(r.right * 100) / 100, width: Math.round(r.width * 100) / 100 };
    };
    const stage = [...document.querySelectorAll<HTMLElement>('.di-scan-stage')].find((e) => e.getClientRects().length > 0) ?? null;
    return { stage: box(stage), main: box(document.querySelector('[data-testid="di-new-main"]')), side: box(document.querySelector('[data-testid="di-new-side"]')), chips: document.querySelector('[data-testid="di-minimized-tray"]')?.childElementCount ?? null };
  });
for (const vp of [SIZES[1], SIZES[2]])
  test(`B3 TRAY ALLOCATION at ${vp.width}x${vp.height}: New Mission's panes with the requests MINIMIZED (a chip in the strip) against the same page with NO chip: the panes still sit side by side, the Recent pane keeps its width, the stage stays above the stacking threshold, and the conversation's width is RECORDED`, async () => {
    const size = `${vp.width}x${vp.height}`;
    await boot(vp);
    await p().waitForTimeout(400);
    const none = await panes();
    await p().locator(sel('di-new-ask')).fill(ASK);
    await p().click(sel('di-new-open-workspace'));
    await p().waitForSelector('.di-mr:not([hidden])', { timeout: 20_000 });
    await clickMinimize('di-mr-minimize');
    await p().waitForSelector(sel('di-mr-restore'), { timeout: 10_000 });
    await p().waitForTimeout(600);
    const chip = await panes();
    record(`ALLOCATION ${size}`, { none, chip, threshold: SPLIT_THRESHOLD, conversationDelta: none.main !== null && chip.main !== null ? Math.round((chip.main.width - none.main.width) * 100) / 100 : null });
    assert.ok(Number.isFinite(SPLIT_THRESHOLD), 'STATE: the stacking threshold was read from the stylesheet');
    assert.equal(none.chips, 0, 'STATE: no chip is in the strip in the baseline');
    assert.equal(chip.chips, 1, 'STATE: one chip is in the strip once the requests are minimized');
    assert.ok(none.stage !== null && chip.stage !== null && none.main !== null && chip.main !== null && none.side !== null && chip.side !== null, 'STATE: the stage and both panes are drawn in both measurements');
    const side = (m: NonNullable<typeof none.main>, o: NonNullable<typeof none.side>) => Math.abs(m.top - o.top) < 2 && (m.right <= o.left + 1 || o.right <= m.left + 1);
    assert.equal(side(none.main!, none.side!), true, 'CONTRACT (allocation): with no chip the panes sit side by side');
    assert.equal(side(chip.main!, chip.side!), true, 'CONTRACT (allocation): with the requests minimized the panes still sit side by side');
    assert.equal(chip.side!.width, none.side!.width, `CONTRACT (allocation): the Recent pane keeps its width (${none.side!.width}px without a chip, ${chip.side!.width}px with one)`);
    assert.ok(chip.stage!.width >= SPLIT_THRESHOLD, `CONTRACT (allocation): the stage stays at or above the stacking threshold (${chip.stage!.width}px against ${SPLIT_THRESHOLD}px)`);
  }, STEPS_MS);
