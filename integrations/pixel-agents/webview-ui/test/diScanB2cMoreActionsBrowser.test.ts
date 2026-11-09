/**
 * Direction B, stage B2c, RENDERED in Chrome (not Brave evidence): Mission details' "More actions", the first anchored, NON-MODAL popover, through the
 * REAL App (test/harness/di-app-scan.html; the office is the session-mismatch v2 FIXTURE in its labelled available-capacity variation, or the
 * lifecycle export's finished, archived Mission; the request panel SYNTHETIC; Herdr answered with the SERVER'S OWN reply builders). Nothing is live.
 *
 * Each claim names the assertion that carries it (method rule 2). Every popover here is opened by a real pointer or a real key, never injected:
 *  - THE CONTRACT: the trigger names its popover (`aria-haspopup="dialog"`, `aria-expanded`, `aria-controls`); the popover is a named dialog, NOT
 *    `aria-modal`, rendered right after its trigger; the app is NOT inert; focus lands on its FIRST item; Tab and Shift+Tab walk Comments, Evidence and
 *    review, Herdr and Close in order; Shift+Tab from the first item returns to the trigger and closes it; it stays inside the frame it is drawn in;
 *  - THE B1 OBLIGATIONS (b1-stage-handback.md section 3), each from a real event: the closing Escape is CONSUMED (one press closes the popover and
 *    nothing else; the next press peels the screen); RELEASE ON UNMOUNT (an item that navigates unmounts the popover with Mission details, the claim
 *    count returns to 0 and the next Escape peels as usual); with the Map OPEN underneath, closing the popover leaves the Map open;
 *  - DISMISSAL: an outside pointer closes it and moves no focus (focus goes where the person pressed); focus leaving it (Tab past Close) closes it
 *    and moves no focus; Escape and Close return focus to the trigger;
 *  - TRUTHFUL ITEMS: a held item is aria-disabled, focusable, its reason drawn IN the item, and its click sends nothing; an enabled Herdr sends ONE
 *    launch and closes the popover with focus on the trigger;
 *  - REACHABILITY (what closes B2b's open action-row limitation): at 1280, 1440 and 1600 every action (Code Diff, More actions and its three items,
 *    Trash) is reached by a pointer (the control is what the window shows at its centre, inside the frame) AND by sequential Tab from Back, EXCEPT a
 *    natively disabled control, which the platform makes no Tab stop: the held Trash (Full Trash HOLD, unchanged) is recorded as such, with what a
 *    keyboard does reach of it (its drawn reason, its detail's summary) measured;
 *  - F-a (the Lead's ruling (i), method rule 5): at 1440 docked, on the archived Mission whose line wrapped, the Herdr line's own text is ONE line;
 *    reported as THREE numbers, each measured on its own: the lines, the text's own width, and the channel's width (the notes column, proven to fill).
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
import { notConfiguredReply, replyFromCheck } from '../../server/src/di/herdrRequests.ts';
import { availableCapacityOffice } from './diAvailableCapacity.ts';
import { relayReply, syntheticOverview } from './diNewMissionSynthetic.ts';

const STEPS_MS = 180_000;
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const read = (file: string) => JSON.parse(fs.readFileSync(path.join(root, file), 'utf-8')) as Record<string, unknown>;
const ACTIVE = availableCapacityOffice((read('test/fixtures/di-office-state-session-mismatch-v2.FIXTURE.json') as { state: DiOfficeState }).state);
const lifecycle = read('test/fixtures/di-office-state-mission-lifecycle-v2.FIXTURE.json') as { mission_ref: string; states: Record<string, DiOfficeState> };
const ARCHIVED: DiOfficeState = { ...structuredClone(lifecycle.states.completed_ended_archived!), fixtureMode: true };
/** A LARGE world (the Map's controls are drawn only with one: App.tsx, `isDiMode && largeWorld`), as diCityFocus loads it. */
const metropolis = fs.readFileSync(path.join(root, '..', 'packaging/di/worlds/living-metropolis.json'), 'utf-8');
const VERSION = (JSON.parse(fs.readFileSync(path.join(root, '..', 'package.json'), 'utf-8')) as { version: string }).version;
/** The app's own front-stack module, imported by the page at the URL the app imports it from, so it is the SAME instance (as B1's tests read it). */
const STACK_URL = '/src/components/diModalStack.ts';
/** The short lines, copied here (never read from the code under test). */
const NOT_SET_UP = 'Herdr is not set up in this office.';
const HERDR_ARCHIVED = 'Herdr is not offered: this Mission is archived, so it is read-only.';
const SIZES = [
  [1280, 800],
  [1440, 900],
  [1600, 1000],
] as const;
/** Where to write the measured JSON (unset: nothing is written). */
const SHOTS = process.env.DI_B2C_SHOTS ?? null;

/** Method rule 4: a measurement artifact is never overwritten; an existing path gets a fresh numbered name. */
function freshPath(dir: string, name: string): string {
  const ext = path.extname(name);
  const stem = name.slice(0, name.length - ext.length);
  let candidate = path.join(dir, name);
  for (let n = 2; fs.existsSync(candidate); n++) candidate = path.join(dir, `${stem}.${n}${ext}`);
  return candidate;
}

type HerdrMode = 'not_configured' | 'available';
let herdrMode: HerdrMode = 'not_configured';
let herdrSent: Array<Record<string, unknown>> = [];
let server: ViteDevServer | null = null;
let wss: WebSocketServer | null = null;
let browser: Browser | null = null;
let page: Page | null = null;
let origin = '';
let unavailable: string | null = null;
let socket: ServerSocket | null = null;
let resolveConnected: (ws: ServerSocket) => void = () => undefined;
const measured: Record<string, unknown> = {};

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
        if (msg.type === 'diMissionRequest' && msg.op === 'refresh') {
          ws.send(JSON.stringify(relayReply(msg.seq, 'refresh', syntheticOverview())));
          return;
        }
        if (msg.type !== 'diHerdrRequest') return;
        herdrSent.push(msg);
        const parsed = { ok: true as const, seq: Number(msg.seq), op: msg.op as 'status' | 'launch', missionRef: String(msg.missionRef) };
        // a launch is recorded and left UNANSWERED: what is measured is that ONE was sent, never what the office did with it
        if (parsed.op === 'launch') return;
        const answer =
          herdrMode === 'not_configured'
            ? notConfiguredReply(parsed)
            : replyFromCheck(parsed, {
                available: true,
                missionRef: parsed.missionRef,
                agent: 'he-exec1',
                identityBasis: 'team_roster_session_confirmed',
                workspaceId: 'w6V',
                window: { cols: 190, rows: 49 },
                matchedPaneId: 'w6V:p3',
                terminal: 'terminal',
                label: 'Terminal',
              } as unknown as Parameters<typeof replyFromCheck>[1]);
        ws.send(JSON.stringify(answer));
      }),
    );
  });
  const addr = server.httpServer?.address();
  origin = `http://localhost:${typeof addr === 'object' && addr !== null ? addr.port : 0}`;
  if (SHOTS !== null) fs.mkdirSync(SHOTS, { recursive: true });
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
  if (SHOTS !== null) fs.writeFileSync(freshPath(SHOTS, 'b2c-measured.json'), JSON.stringify(measured, null, 2), { flag: 'wx' });
  await page?.close();
  await browser?.close();
  wss?.close();
  await server?.close();
}, STEPS_MS);

beforeEach(async (ctx) => {
  if (unavailable) {
    console.warn(`SKIPPED (B2c More actions): ${unavailable}`);
    ctx.skip();
    return;
  }
  await page?.close();
  page = null;
  herdrMode = 'not_configured';
  herdrSent = [];
}, STEPS_MS);

const p = () => page!;
const sel = (id: string) => `[data-testid="${id}"]`;
const POPOVER = sel('di-scan-more-actions-popover');
const TRIGGER = sel('di-scan-more-actions');
const ITEMS = ['di-scan-comments', 'di-scan-evidence', 'di-scan-herdr', 'di-scan-more-actions-close'] as const;

async function boot(width: number, height: number, state: DiOfficeState): Promise<void> {
  const connected = new Promise<ServerSocket>((resolve) => (resolveConnected = resolve));
  page = await browser!.newPage({ viewport: { width, height } });
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await page.goto(`${origin}/test/harness/di-app-scan.html`, { waitUntil: 'domcontentloaded' });
  await connected;
  socket!.send(JSON.stringify({ type: 'providerCapabilities', readingTools: ['Read'], subagentToolNames: ['Task', 'Agent'] }));
  socket!.send(JSON.stringify({ type: 'settingsLoaded', soundEnabled: false, lastSeenVersion: VERSION, extensionVersion: VERSION }));
  socket!.send(JSON.stringify(state));
  await p().waitForSelector(sel('di-rail-missions'), { timeout: 20_000 });
}
/** Dock the first Home row (one click, B2a) and wait for Mission details and the office's Herdr answer (the line leaves "Checking"). */
async function dockFirst(): Promise<void> {
  await p().waitForSelector(sel('di-scan-row'), { timeout: 20_000 });
  await p().click(sel('di-scan-row'));
  await p().waitForSelector('[data-testid="di-scan-stage"][data-view="mission"]');
  await p().waitForFunction(() => {
    const line = document.querySelector('[data-testid="di-scan-herdr-reason"]')?.textContent ?? '';
    return line !== '' && !line.includes('Checking whether Herdr');
  });
}
/** The archived Mission, from the rail's Missions page (as diScanHerdrLineBrowser opens it). */
async function dockArchived(): Promise<void> {
  await p().click(sel('di-rail-missions'));
  await p().click(sel('di-missions-status-archived'));
  await p().click(`[data-testid="di-missions-row"][data-mission-ref="${lifecycle.mission_ref}"] ${sel('di-missions-open')}`);
  await p().waitForSelector('[data-testid="di-scan-stage"][data-view="mission"]');
}
// a STRING, as B1's tests pass it: a FUNCTION containing import() is rewritten by vitest's transform to __vite_ssr_dynamic_import__, which does not
// exist in the page (cut 3: six tests died here before reaching what they assert)
const frontCount = () => p().evaluate(`(async () => (await import('${STACK_URL}')).frontCount())()`) as Promise<number>;
const focused = () => p().evaluate(() => document.activeElement?.getAttribute('data-testid') ?? document.activeElement?.tagName ?? null);
const popoverCount = () => p().locator(POPOVER).count();
const view = () => p().locator(sel('di-scan-stage')).getAttribute('data-view');
const stageCount = () => p().locator(sel('di-scan-stage')).count();
/** Shown: the control is what the window draws at its centre, and that centre is inside the frame it scrolls in (the screen) and the window. */
const shownAt = (testid: string) =>
  p().evaluate((id) => {
    const el = document.querySelector(`[data-testid="${id}"]`);
    if (el === null) return { present: false, inFrame: false, onTop: false };
    const r = el.getBoundingClientRect();
    const x = (r.left + r.right) / 2;
    const y = (r.top + r.bottom) / 2;
    const frame = el.closest('.di-scan-mission')?.getBoundingClientRect() ?? new DOMRect(0, 0, innerWidth, innerHeight);
    const hit = document.elementFromPoint(x, y);
    return {
      present: true,
      inFrame: x >= Math.max(0, frame.left) && x <= Math.min(innerWidth, frame.right) && y >= Math.max(0, frame.top) && y <= Math.min(innerHeight, frame.bottom),
      onTop: hit !== null && (el === hit || el.contains(hit)),
    };
  }, testid);
/** Tab presses from Back until `testid` has focus (null: not reached within the budget). */
async function tabsFromBack(testid: string, budget = 80): Promise<number | null> {
  await p().locator(sel('di-scan-back')).focus();
  for (let n = 1; n <= budget; n++) {
    await p().keyboard.press('Tab');
    if ((await focused()) === testid) return n;
  }
  return null;
}

for (const [w, h] of SIZES)
  test(
    `B2c THE CONTRACT at ${w}x${h}: a named non-modal dialog right after its trigger, focus on its first item, Tab and Shift+Tab in order, Shift+Tab out closes it, inside its frame; nothing inert`,
    async () => {
      await boot(w, h, ACTIVE);
      await dockFirst();
      const trigger = p().locator(TRIGGER);
      assert.equal(await trigger.getAttribute('aria-haspopup'), 'dialog');
      assert.equal(await trigger.getAttribute('aria-expanded'), 'false');
      assert.equal(await popoverCount(), 0, 'closed by default');
      assert.equal(await frontCount(), 0, 'nothing in front before it opens');

      await trigger.click();
      await p().waitForSelector(POPOVER);
      const shape = await p().evaluate(() => {
        const t = document.querySelector('[data-testid="di-scan-more-actions"]')!;
        const pop = document.querySelector('[data-testid="di-scan-more-actions-popover"]')!;
        return {
          expanded: t.getAttribute('aria-expanded'),
          controls: t.getAttribute('aria-controls'),
          id: pop.id,
          role: pop.getAttribute('role'),
          modal: pop.getAttribute('aria-modal'),
          label: pop.getAttribute('aria-label'),
          adjacent: t.nextElementSibling === pop,
          inert: document.getElementById('root')?.hasAttribute('inert') ?? null,
        };
      });
      assert.deepEqual(
        { ...shape, controlsIt: shape.controls === shape.id },
        { ...shape, expanded: 'true', role: 'dialog', modal: null, label: 'More actions', adjacent: true, inert: false, controlsIt: true },
        'a named, NON-modal dialog, right after its trigger, controlled by it; the app is not inert',
      );
      assert.equal(await frontCount(), 1, 'one claim in front');
      assert.equal(await focused(), 'di-scan-comments', 'focus on the FIRST item');

      // Tab and Shift+Tab walk the items and Close in order, sequentially (no roving tabindex)
      const walk: Array<string | null> = [await focused()];
      for (let i = 1; i < ITEMS.length; i++) {
        await p().keyboard.press('Tab');
        walk.push(await focused());
      }
      assert.deepEqual(walk, [...ITEMS], 'Tab walks Comments, Evidence and review, Herdr, Close');
      for (let i = ITEMS.length - 2; i >= 0; i--) {
        await p().keyboard.press('Shift+Tab');
        assert.equal(await focused(), ITEMS[i], `Shift+Tab back to ${ITEMS[i]}`);
      }

      // geometry: the popover inside the screen's frame and the window; its placement recorded
      const geo = await p().evaluate(() => {
        const pop = document.querySelector('[data-testid="di-scan-more-actions-popover"]')!;
        const r = pop.getBoundingClientRect();
        const f = pop.closest('.di-scan-mission')!.getBoundingClientRect();
        const frame = { top: Math.max(0, f.top), left: Math.max(0, f.left), right: Math.min(innerWidth, f.right), bottom: Math.min(innerHeight, f.bottom) };
        return {
          side: pop.getAttribute('data-side'),
          align: pop.getAttribute('data-align'),
          box: { top: Math.round(r.top), left: Math.round(r.left), right: Math.round(r.right), bottom: Math.round(r.bottom) },
          frame: { top: Math.round(frame.top), left: Math.round(frame.left), right: Math.round(frame.right), bottom: Math.round(frame.bottom) },
          inside: r.top >= frame.top - 0.5 && r.left >= frame.left - 0.5 && r.right <= frame.right + 0.5 && r.bottom <= frame.bottom + 0.5,
        };
      });
      measured[`popover @${w}`] = geo;
      assert.equal(geo.inside, true, `the popover is inside its frame: ${JSON.stringify(geo)}`);

      // Shift+Tab from the FIRST item lands on the trigger (it is right before the popover) and, focus having left it, closes it with no focus move
      await p().keyboard.press('Shift+Tab');
      assert.equal(await focused(), 'di-scan-more-actions', 'Shift+Tab from the first item is the trigger');
      assert.equal(await popoverCount(), 0, 'focus left it: closed');
      assert.equal(await trigger.getAttribute('aria-expanded'), 'false');
      assert.equal(await frontCount(), 0, 'released');
      // and the keyboard opens it too (Enter on the trigger), focus again on the first item
      await p().keyboard.press('Enter');
      await p().waitForSelector(POPOVER);
      assert.equal(await focused(), 'di-scan-comments', 'opened by Enter, focus on the first item');
    },
    STEPS_MS,
  );

test(
  'B2c THE CLOSING ESCAPE IS CONSUMED (B1 obligation): one press closes the popover and ONLY the popover (focus back on its trigger, the screen kept); the next press peels the screen',
  async () => {
    await boot(1440, 900, ACTIVE);
    await dockFirst();
    await p().click(TRIGGER);
    await p().waitForSelector(POPOVER);
    assert.equal(await frontCount(), 1);
    await p().keyboard.press('Escape');
    assert.equal(await popoverCount(), 0, 'the popover closed');
    assert.equal(await view(), 'mission', 'the SAME press did not peel the screen');
    assert.equal(await focused(), 'di-scan-more-actions', 'focus back on the trigger');
    assert.equal(await frontCount(), 0, 'released');
    await p().keyboard.press('Escape');
    assert.equal(await stageCount(), 0, 'the next press peels the screen, as usual');
  },
  STEPS_MS,
);

test(
  'B2c RELEASE ON UNMOUNT (B1 obligation): an item that navigates unmounts the popover with Mission details; the claim is released, and the next Escape peels the new screen as usual',
  async () => {
    await boot(1440, 900, ACTIVE);
    await dockFirst();
    await p().click(TRIGGER);
    await p().waitForSelector(POPOVER);
    assert.equal(await frontCount(), 1);
    await p().keyboard.press('Enter'); // focus is on the first item, Comments
    await p().waitForSelector('[data-testid="di-scan-stage"][data-view="comments"]');
    assert.equal(await popoverCount(), 0, 'gone with Mission details');
    assert.equal(await frontCount(), 0, 'the unmount released the claim');
    await p().keyboard.press('Escape');
    await p().waitForSelector('[data-testid="di-scan-stage"][data-view="mission"]');
    assert.equal(await view(), 'mission', 'Escape still peels one screen (Comments back to Mission details)');
  },
  STEPS_MS,
);

test(
  'B2c DISMISSAL: an outside pointer closes it and moves no focus (two panes: a Home control takes it; one pane: a press on plain text); focus leaving past Close closes it, focus where Tab put it; Close returns focus',
  async () => {
    // two panes: a pointer on a Home control (outside the popover and its trigger)
    await boot(1440, 900, ACTIVE);
    await dockFirst();
    await p().click(TRIGGER);
    await p().waitForSelector(POPOVER);
    await p().click(sel('di-scan-tab-open'));
    assert.equal(await popoverCount(), 0, 'an outside pointer closed it');
    assert.equal(await focused(), 'di-scan-tab-open', 'focus is where the person pressed, not moved to the trigger');
    assert.equal(await frontCount(), 0);

    // focus leaving: Tab past Close goes on to what follows the trigger (Trash), and closes it with no focus move
    await p().click(TRIGGER);
    await p().waitForSelector(POPOVER);
    for (let i = 1; i < ITEMS.length; i++) await p().keyboard.press('Tab');
    assert.equal(await focused(), 'di-scan-more-actions-close');
    await p().keyboard.press('Tab');
    assert.equal(await popoverCount(), 0, 'focus left it: closed');
    // where Tab put it: the next stop AFTER the trigger in the document (not the trigger, not back inside), recorded by name
    const next = await p().evaluate(() => {
      const t = document.querySelector('[data-testid="di-scan-more-actions"]')!;
      const a = document.activeElement;
      return { id: a?.getAttribute('data-testid') ?? a?.tagName ?? null, after: a !== null && a !== t && (t.compareDocumentPosition(a) & Node.DOCUMENT_POSITION_FOLLOWING) !== 0 };
    });
    measured['Tab past Close @1440: focus'] = next.id;
    assert.equal(next.after, true, `focus went on past the trigger, where Tab put it (${next.id})`);
    assert.equal(await frontCount(), 0);

    // Close: closes, focus back on the trigger
    await p().click(TRIGGER);
    await p().waitForSelector(POPOVER);
    await p().click(sel('di-scan-more-actions-close'));
    assert.equal(await popoverCount(), 0);
    assert.equal(await focused(), 'di-scan-more-actions', 'Close gives focus back to the trigger');

    // one pane: a press on the Mission's title (plain text) closes it; the popover moves no focus there (where focus goes is the browser's, recorded)
    await page?.close();
    await boot(1280, 800, ACTIVE);
    await dockFirst();
    await p().click(TRIGGER);
    await p().waitForSelector(POPOVER);
    await p().click(sel('di-scan-mission-title'));
    assert.equal(await popoverCount(), 0, 'an outside pointer on text closed it');
    const after = await focused();
    measured['outside pointer on text @1280: focus'] = after;
    assert.notEqual(after, 'di-scan-more-actions', 'the popover did not move focus to its trigger');
  },
  STEPS_MS,
);

test(
  'B2c WITH THE MAP OPEN (B1 obligation): Escape in the popover closes the popover and leaves the Map OPEN beneath; the next Escape is recorded (measured only)',
  async () => {
    await boot(1440, 900, ACTIVE);
    // the large world (cut 3: with this fixture's small world the Map's controls were never drawn, so the test died before the Map opened)
    socket!.send(JSON.stringify({ type: 'layoutLoaded', layout: JSON.parse(metropolis) }));
    await p().waitForFunction(() => (window as unknown as { __diHarnessOffice?: { layout: { cols: number } } }).__diHarnessOffice?.layout.cols === 352, undefined, {
      timeout: 60_000,
    });
    // the scan face's world controls are COLLAPSIBLE and rendered only while shown (App.tsx: worldChromeCollapsible on the scan face; worldChromeShown
    // = !collapsible || open; the Map's control is inside them), so they are shown first, as a person does (cut 4: the test waited for the control
    // before showing them and died there, before the Map opened)
    if ((await p().locator(sel('world-view-map')).count()) === 0) await p().click(sel('di-world-disclosure'));
    await p().waitForSelector(sel('world-view-map'), { timeout: 30_000 });
    await p().click(sel('world-view-map'));
    await p().waitForSelector(sel('world-map'));
    await dockFirst();
    assert.equal(await p().locator(sel('world-map')).count(), 1, 'the Map is open beneath the docked screen');
    await p().click(TRIGGER);
    await p().waitForSelector(POPOVER);
    await p().keyboard.press('Escape');
    assert.equal(await popoverCount(), 0, 'the popover closed');
    assert.equal(await p().locator(sel('world-map')).count(), 1, 'the Map is still OPEN');
    assert.equal(await view(), 'mission', 'and the screen was not peeled');
    assert.equal(await frontCount(), 0);
    // MEASURED ONLY (not B2c's to decide): what the next Escape does with the Map open beneath a docked screen
    await p().keyboard.press('Escape');
    measured['Map open, the second Escape'] = { mapOpen: (await p().locator(sel('world-map')).count()) === 1, stageOpen: (await stageCount()) === 1 };
  },
  STEPS_MS,
);

test(
  'B2c TRUTHFUL ITEMS: held, Herdr is aria-disabled, focusable, its reason drawn in the item, and its click sends nothing; enabled, ONE launch is sent and the popover closes with focus on the trigger',
  async () => {
    herdrMode = 'not_configured';
    await boot(1440, 900, ACTIVE);
    await dockFirst();
    await p().click(TRIGGER);
    await p().waitForSelector(POPOVER);
    const herdr = p().locator(sel('di-scan-herdr'));
    assert.equal(await herdr.getAttribute('aria-disabled'), 'true', 'held');
    assert.equal(await herdr.evaluate((b) => (b as HTMLButtonElement).disabled), false, 'never natively disabled: it stays focusable');
    assert.equal(((await p().locator(sel('di-scan-herdr-held')).innerText()) ?? '').trim(), NOT_SET_UP, 'its reason, drawn in the item');
    assert.ok(await p().locator(sel('di-scan-herdr-held')).isVisible());
    await p().keyboard.press('Tab');
    await p().keyboard.press('Tab');
    assert.equal(await focused(), 'di-scan-herdr', 'reached by Tab, held');
    const before = herdrSent.filter((m) => m.op === 'launch').length;
    await p().keyboard.press('Enter');
    await herdr.click({ force: true }); // a pointer too; force: Playwright treats aria-disabled as not actionable, and the claim is what a click DOES
    assert.equal(herdrSent.filter((m) => m.op === 'launch').length, before, 'a held item sends nothing');
    assert.equal(await popoverCount(), 1, 'and does not close the popover');
    // Evidence and review: whichever state this Mission is in, it is truthful (held with a drawn reason, or enabled with none)
    const evidenceHeld = (await p().locator(sel('di-scan-evidence')).getAttribute('aria-disabled')) === 'true';
    const evidenceReason = await p().locator(sel('di-scan-evidence-held')).count();
    assert.equal(evidenceReason, evidenceHeld ? 1 : 0, 'a reason is drawn exactly when Evidence and review is held');
    measured['evidence on the first Mission'] = { held: evidenceHeld };

    await page?.close();
    herdrMode = 'available';
    herdrSent = [];
    await boot(1440, 900, ACTIVE);
    await dockFirst();
    await p().click(TRIGGER);
    await p().waitForSelector(POPOVER);
    await p().waitForFunction(() => document.querySelector('[data-testid="di-scan-herdr"]')?.getAttribute('aria-disabled') === null);
    await p().click(sel('di-scan-herdr'));
    await p().waitForFunction(() => document.querySelector('[data-testid="di-scan-more-actions-popover"]') === null);
    assert.equal(herdrSent.filter((m) => m.op === 'launch').length, 1, 'ONE launch sent');
    assert.equal(await focused(), 'di-scan-more-actions', 'focus back on the trigger');
  },
  STEPS_MS,
);

for (const [w, h] of SIZES)
  test(
    `B2c REACHABILITY at ${w}x${h} (closes B2b's open action-row limitation): every action is reached by a pointer (shown at its centre, inside its frame) and by sequential Tab from Back`,
    async () => {
      await boot(w, h, ACTIVE);
      await dockFirst();
      const row: Record<string, unknown> = {};
      for (const id of ['di-scan-code-diff', 'di-scan-more-actions', 'di-scan-trash']) {
        await p().locator(sel(id)).scrollIntoViewIfNeeded();
        const shown = await shownAt(id);
        assert.deepEqual(shown, { present: true, inFrame: true, onTop: true }, `${id}: reached by a pointer`);
        // a NATIVELY disabled control is no Tab stop by the platform's rule: the held Trash (Full Trash HOLD, unchanged by B2c) is one; it is recorded as
        // such, and what a keyboard reaches of it (its reason drawn, its detail's summary) is measured instead of claimed
        const nativelyDisabled = await p().locator(sel(id)).evaluate((b) => (b as HTMLButtonElement).disabled);
        if (nativelyDisabled) {
          assert.ok(await p().locator(sel(`${id}-reason`)).isVisible(), `${id}: held, and its reason is drawn`);
          const summary = (await p().locator(sel(`${id}-more-summary`)).count()) === 1 ? await tabsFromBack(`${id}-more-summary`) : null;
          row[id] = { pointer: shown, tabsFromBack: 'not a Tab stop: natively disabled (held)', reasonDrawn: true, detailSummaryTabsFromBack: summary };
          continue;
        }
        const tabs = await tabsFromBack(id);
        assert.notEqual(tabs, null, `${id}: reached by Tab from Back`);
        row[id] = { pointer: shown, tabsFromBack: tabs };
      }
      // the items: opened by Enter on the trigger, then each shown and reached by Tab
      await p().locator(TRIGGER).focus();
      await p().keyboard.press('Enter');
      await p().waitForSelector(POPOVER);
      for (const [i, id] of ITEMS.entries()) {
        const shown = await shownAt(id);
        assert.equal(shown.onTop && shown.present, true, `${id}: shown at its centre (${JSON.stringify(shown)})`);
        assert.equal(await focused(), id, `${id}: reached by Tab, ${i} press(es) after the first item`);
        row[id] = { pointer: shown, tabsFromFirstItem: i };
        if (i < ITEMS.length - 1) await p().keyboard.press('Tab');
      }
      measured[`reachability @${w}`] = row;
      console.log(`REACHABILITY @${w} MEASURED: ${JSON.stringify(row)}`);
    },
    STEPS_MS,
  );

test(
  "B2c F-a at 1440 docked (ruling (i), method rule 5): the archived Mission's Herdr line, its own words, is ONE line; THREE numbers, each measured: lines, the text's width, the channel's width",
  async () => {
    herdrMode = 'not_configured';
    await boot(1440, 900, ARCHIVED);
    await dockArchived();
    await p().waitForFunction(() => document.querySelector('[data-testid="di-scan-herdr-more"]') !== null);
    const m = await p().evaluate(() => {
      const line = document.querySelector('[data-testid="di-scan-herdr-reason"]')!;
      const tops = new Set<number>();
      let left = Infinity;
      let right = -Infinity;
      let words = '';
      for (const node of line.childNodes) {
        if (node.nodeType !== Node.TEXT_NODE || (node.textContent ?? '').trim() === '') continue;
        words += node.textContent ?? '';
        const range = document.createRange();
        range.selectNodeContents(node);
        for (const r of range.getClientRects())
          if (r.width > 0 && r.height > 0) {
            tops.add(Math.round(r.top));
            left = Math.min(left, r.left);
            right = Math.max(right, r.right);
          }
      }
      const row = document.querySelector('.di-scan-actions-notes')!;
      const cs = getComputedStyle(row);
      const rowBox = row.getBoundingClientRect();
      const column = document.querySelector('.di-scan-herdr-notes')!.getBoundingClientRect();
      return {
        words: words.trim(),
        lines: tops.size,
        textWidth: Math.round(right - left),
        channelWidth: Math.round(rowBox.width - parseFloat(cs.paddingLeft) - parseFloat(cs.paddingRight)),
        columnWidth: Math.round(column.width),
        stage: Math.round(document.querySelector('[data-testid="di-scan-stage"]')!.getBoundingClientRect().width),
        direction: cs.flexDirection,
      };
    });
    measured['F-a @1440 (archived, docked)'] = m;
    // method rule 5 (the Lead): the proof that the column FILLS its channel is written INTO THE LOG, with the three numbers, before anything is asserted,
    // so the log alone shows a channel width rather than a content width
    console.log(
      `F-a @1440 MEASURED: lines=${m.lines}; textWidth=${m.textWidth}px; channelWidth=${m.channelWidth}px (the notes row's content box: its width minus its padding); ` +
        `columnWidth=${m.columnWidth}px (the Herdr column) -> the column ${m.columnWidth === m.channelWidth ? 'FILLS' : 'DOES NOT FILL'} its channel; ` +
        `stage=${m.stage}px; flex-direction=${m.direction}; words="${m.words}"`,
    );
    assert.equal(m.words, HERDR_ARCHIVED, "the line's own words are the failing tests' text");
    assert.equal(m.columnWidth, m.channelWidth, `the Herdr column FILLS its channel (so the channel is its capacity): ${JSON.stringify(m)}`);
    assert.ok(m.textWidth <= m.channelWidth, `the text fits its channel: ${JSON.stringify(m)}`);
    assert.equal(m.lines, 1, `ONE line: ${JSON.stringify(m)}`);
  },
  STEPS_MS,
);
