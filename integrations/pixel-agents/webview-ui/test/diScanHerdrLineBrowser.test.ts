/**
 * A7: every Herdr state on Mission details leads with a SHORT, TRUTHFUL line, and the office's exact words are one keyboard disclosure away (Free UI streamline, 2026-11-04, approved
 * Mission mn-92b246d…, Lead1 addendum 3). Through the actual App in a real browser.
 *
 * The office's words here are the SERVER'S OWN: the unconfigured reply is built by `notConfiguredReply` (server/src/di/herdrRequests.ts, imported, never copied), and the refused launch by
 * `replyFromLaunch` with a route refusal of the shape the route writes (focus not established, naming the launcher it wrote). The page is checked for: the default line (short, one line,
 * saying THAT the office refused or did not offer, nothing more); the disclosure (closed, named, opened by Tab and Enter); the exact reason recovered from it character for character; an
 * ARCHIVED Mission (its line is the archived reason, and the office's answer is still one disclosure away); and the rail on a screen where it is the one Herdr note (the short line drawn,
 * the exact reason kept in the control's title and accessible description). Launch admission is unchanged: what enables the control, its busy state and Check again.
 *
 * Office states (FIXTURE): the session-mismatch v2 projection (an active Mission) and the lifecycle export's finished, archived Mission. Only the Herdr control is answered, and only as
 * above; nothing else the page sends is answered. Nothing is live.
 *
 * Browser: the installed Chrome (playwright-core, channel `chrome`), else Playwright's Chromium; with neither, every test is skipped with an explicit message.
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
import { notConfiguredReply, replyFromCheck, replyFromLaunch } from '../../server/src/di/herdrRequests.ts';

const STEPS_MS = 120_000;
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const VERSION = (JSON.parse(fs.readFileSync(path.join(root, '..', 'package.json'), 'utf-8')) as { version: string }).version;
const read = (file: string) => JSON.parse(fs.readFileSync(path.join(root, file), 'utf-8')) as Record<string, unknown>;
const ACTIVE = (read('test/fixtures/di-office-state-session-mismatch-v2.FIXTURE.json') as { state: DiOfficeState }).state;
const lifecycle = read('test/fixtures/di-office-state-mission-lifecycle-v2.FIXTURE.json') as { mission_ref: string; states: Record<string, DiOfficeState> };
const ARCHIVED: DiOfficeState = { ...structuredClone(lifecycle.states.completed_ended_archived!), fixtureMode: true };

/** The short lines, copied here (never read from the code under test). */
const NOT_SET_UP = 'Herdr is not set up in this office.';
const LAUNCH_REFUSED = 'The office refused this launch.';
const HERDR_ARCHIVED = 'Herdr is not offered: this Mission is archived, so it is read-only.';
/** A route refusal of the shape the launch route writes (herdrLaunch.ts, focus not established): long, with the launcher's path. */
const ROUTE_DETAIL =
  "Nothing was opened: Workspace focus was not established, and Herdr's call failed, so nothing is known to have moved. The launcher was written at /x/herdr-attach-a1b2.command and was not used.";

type Mode = 'not_configured' | 'available_then_refused';
let mode: Mode = 'not_configured';
let server: ViteDevServer | null = null;
let wss: WebSocketServer | null = null;
let browser: Browser | null = null;
let page: Page | null = null;
let url = '';
let unavailable: string | null = null;
let socket: ServerSocket | null = null;
let resolveConnected: (ws: ServerSocket) => void = () => undefined;
let herdrSent: Array<Record<string, unknown>> = [];
/** The exact reasons the server's own functions wrote into the replies this test sent. */
let serverReasons: string[] = [];

beforeAll(async () => {
  server = await createServer({
    configFile: path.resolve(root, 'vite.config.ts'),
    server: { port: 0, strictPort: false, hmr: false, ws: false },
    logLevel: 'silent',
  });
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
        if (msg.type !== 'diHerdrRequest') return;
        herdrSent.push(msg);
        const parsed = { ok: true as const, seq: Number(msg.seq), op: msg.op as 'status' | 'launch', missionRef: String(msg.missionRef) };
        const answer =
          mode === 'not_configured'
            ? notConfiguredReply(parsed)
            : parsed.op === 'status'
              ? replyFromCheck(parsed, {
                  available: true,
                  missionRef: parsed.missionRef,
                  agent: 'he-exec1',
                  identityBasis: 'team_roster_session_confirmed',
                  workspaceId: 'w6V',
                  window: { cols: 190, rows: 49 },
                  matchedPaneId: 'w6V:p3',
                  terminal: 'terminal',
                  label: 'Terminal',
                } as unknown as Parameters<typeof replyFromCheck>[1])
              : replyFromLaunch(parsed, { launched: false, refusal: 'focus_not_established', detail: ROUTE_DETAIL });
        if (answer.reason !== null) serverReasons.push(answer.reason);
        ws.send(JSON.stringify(answer));
      }),
    );
  });
  const addr = server.httpServer?.address();
  const port = typeof addr === 'object' && addr !== null ? addr.port : 0;
  url = `http://localhost:${port}/test/harness/di-app-scan.html`;
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
    console.warn(`SKIPPED (Herdr line): ${unavailable}`);
    ctx.skip();
    return;
  }
  await page?.close();
  page = null;
  herdrSent = [];
  serverReasons = [];
}, STEPS_MS);

const p = () => page!;
const sel = (id: string) => `[data-testid="${id}"]`;

async function boot(state: DiOfficeState): Promise<void> {
  const connected = new Promise<ServerSocket>((resolve) => (resolveConnected = resolve));
  page = await browser!.newPage({ viewport: { width: 1440, height: 900 } });
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await page.addInitScript((at: number) => {
    const RealDate = Date;
    function PinnedDate(this: unknown, ...args: unknown[]) {
      return args.length === 0 ? new RealDate(at) : new (RealDate as unknown as new (...a: unknown[]) => Date)(...args);
    }
    PinnedDate.prototype = RealDate.prototype;
    PinnedDate.now = () => at;
    PinnedDate.parse = RealDate.parse;
    PinnedDate.UTC = RealDate.UTC;
    (window as unknown as { Date: unknown }).Date = PinnedDate;
  }, state.times.evaluatedAt! * 1000);
  await page.goto(url, { waitUntil: 'domcontentloaded' });
  await connected;
  socket!.send(JSON.stringify({ type: 'providerCapabilities', readingTools: ['Read', 'Grep', 'Glob', 'WebFetch', 'WebSearch'], subagentToolNames: ['Task', 'Agent'] }));
  socket!.send(JSON.stringify({ type: 'settingsLoaded', soundEnabled: false, lastSeenVersion: VERSION, extensionVersion: VERSION }));
  socket!.send(JSON.stringify(state));
  await p().waitForSelector(sel('di-rail-missions'));
}
async function openMission(ref: string | null, filter: 'archived' | 'all'): Promise<void> {
  await p().click(sel('di-rail-missions'));
  await p().click(sel(`di-missions-status-${filter}`));
  await p().click(ref === null ? `${sel('di-missions-row')} ${sel('di-missions-open')}` : `[data-testid="di-missions-row"][data-mission-ref="${ref}"] ${sel('di-missions-open')}`);
  await p().waitForSelector('[data-testid="di-scan-stage"][data-view="mission"]');
}
/** How many lines the line's OWN words are drawn on (its text, not its Check again control, which may sit beside it or wrap under it in a narrow column). */
const lineCount = (selector: string) =>
  p().locator(selector).evaluate((el) => {
    const tops = new Set<number>();
    for (const node of el.childNodes) {
      if (node.nodeType !== Node.TEXT_NODE || (node.textContent ?? '').trim() === '') continue;
      const range = document.createRange();
      range.selectNodeContents(node);
      for (const r of range.getClientRects()) if (r.width > 0 && r.height > 0) tops.add(Math.round(r.top));
    }
    return tops.size;
  });
/** The line's own words (without its Check again control). */
const ownWords = (selector: string) =>
  p().locator(selector).evaluate((el) => {
    const copy = el.cloneNode(true) as HTMLElement;
    copy.querySelectorAll('button').forEach((b) => b.remove());
    return (copy.textContent ?? '').trim();
  });
/** Review round 1, CARRY1: the control's description as Chrome's accessibility tree computes it (by CDP), not as the markup says it. */
async function axDescription(selector: string): Promise<string> {
  const cdp = await p().context().newCDPSession(p());
  const { root: doc } = (await cdp.send('DOM.getDocument', { depth: 0 })) as { root: { nodeId: number } };
  const { nodeId } = (await cdp.send('DOM.querySelector', { nodeId: doc.nodeId, selector })) as { nodeId: number };
  const { nodes } = (await cdp.send('Accessibility.getPartialAXTree', { nodeId, fetchRelatives: false })) as { nodes: Array<{ description?: { value: string } }> };
  await cdp.detach();
  return nodes[0]?.description?.value ?? '';
}
/** Open the Herdr detail BY KEYBOARD (Tab to its summary, Enter) and return the exact words it holds. */
async function openHerdrDetailByKeyboard(): Promise<string> {
  const detail = p().locator(sel('di-scan-herdr-more'));
  assert.equal(await detail.evaluate((d) => (d as HTMLDetailsElement).open), false, 'the detail is closed by default');
  assert.equal(await p().locator(sel('di-scan-herdr-note')).isVisible(), false, 'its words are folded by default');
  await p().locator(sel('di-scan-back')).focus();
  for (let i = 0; i < 80 && (await p().evaluate(() => document.activeElement?.getAttribute('data-testid'))) !== 'di-scan-herdr-more-summary'; i++)
    await p().keyboard.press('Tab');
  assert.equal(await p().evaluate(() => document.activeElement?.getAttribute('data-testid')), 'di-scan-herdr-more-summary', 'the detail is reached by Tab');
  await p().keyboard.press('Enter');
  assert.equal(await detail.evaluate((d) => (d as HTMLDetailsElement).open), true, 'Enter opens it');
  assert.ok(await p().locator(sel('di-scan-herdr-note')).isVisible());
  return (await p().locator(sel('di-scan-herdr-note')).textContent()) ?? '';
}

/** B2c: Comments, Evidence and review and Herdr are items of Mission details' "More actions" (an anchored, non-modal popover); open it, unless it is
 *  open, to read or use one. A held item is aria-disabled (and stays focusable), never natively disabled. */
async function openMore(): Promise<void> {
  if ((await p().locator('[data-testid="di-scan-more-actions-popover"]').count()) === 0) await p().click('[data-testid="di-scan-more-actions"]');
  await p().waitForSelector('[data-testid="di-scan-more-actions-popover"]');
}
const heldNow = async (selector: string) => (await p().locator(selector).getAttribute('aria-disabled')) === 'true';

test(
  'A7, NOT SET UP: the default line is the short, truthful "Herdr is not set up in this office."; the server\'s exact reason (its flags and paths) is one keyboard disclosure away, character for character; the rail keeps it in the control',
  async () => {
    mode = 'not_configured';
    await boot(ACTIVE);
    await openMission(null, 'all');
    await p().waitForFunction(() => document.querySelector('[data-testid="di-scan-herdr-reason"]')?.textContent?.includes('not set up') === true);
    const reason = serverReasons.at(-1)!;
    assert.match(reason, /--herdr-bin/, 'the server reason is the long one, with the flags');
    assert.equal(await ownWords(sel('di-scan-herdr-reason')), NOT_SET_UP, 'the default line: short, and only what the server said');
    assert.equal(await lineCount(sel('di-scan-herdr-reason')), 1, 'one line');
    assert.doesNotMatch(await p().locator(sel('di-scan-herdr-reason')).innerText(), /--herdr|absolute paths|HOME/, 'no flag or path in the default line');
    assert.equal(((await p().locator(sel('di-scan-herdr-more-summary')).textContent()) ?? '').trim(), 'The office’s reason, in its own words');
    // CARRY1: with the detail still closed, the accessibility tree describes the control by the server's exact reason (through the always-present copy)
    // CHANGED IN B2c: the control is an item of "More actions"; the walk to the detail below starts at Back, so focus leaving closes the popover
    await openMore();
    const described = await axDescription(sel('di-scan-herdr'));
    assert.ok(described.includes(reason), `the control is described by the exact reason, in the accessibility tree: ${described}`);
    assert.equal(await openHerdrDetailByKeyboard(), reason, 'the server reason, character for character');
    // admission unchanged: held (CHANGED IN B2c: aria-disabled, BEFORE natively disabled), with Check again
    await openMore();
    assert.equal(await heldNow(sel('di-scan-herdr')), true);
    assert.equal(await p().locator(sel('di-scan-herdr-recheck')).count(), 1);
    // the control is described by the exact reason too, and titled with it
    assert.equal(await p().locator(sel('di-scan-herdr')).getAttribute('title'), reason);
    // the rail, on a screen where it is the one Herdr note (Comments): the short line drawn; the exact reason kept in its control's title and description
    await p().click(sel('di-scan-comments'));
    await p().waitForSelector('[data-testid="di-scan-stage"][data-view="comments"]');
    assert.equal(((await p().locator(sel('di-rail-herdr-note')).textContent()) ?? '').trim(), NOT_SET_UP);
    assert.equal(await p().locator(sel('di-rail-herdr')).getAttribute('title'), reason);
    assert.equal(((await p().locator('#di-rail-herdr-reason').textContent()) ?? '').trim(), reason);
    // CARRY1 (review round 2): the rail's and the Code Diff / Comments header's Herdr controls, in Chrome's accessibility tree (by CDP): each is described by the
    // server's exact reason, as Mission details' control is (parity measured, not read from markup)
    for (const control of ['di-rail-herdr', 'di-scan-sibling-herdr'] as const) {
      assert.equal(await p().locator(sel(control)).count(), 1, `${control} is drawn on this screen`);
      const described = await axDescription(sel(control));
      assert.equal(described, reason, `${control} is described by the exact reason in the accessibility tree: ${described}`);
    }
  },
  STEPS_MS,
);

test(
  'A7, A LONG LAUNCH REFUSAL: after a refused launch the default line is "The office refused this launch."; the route\'s exact words (its launcher path) are one keyboard disclosure away; admission and the next click are unchanged',
  async () => {
    mode = 'available_then_refused';
    await boot(ACTIVE);
    await openMission(null, 'all');
    // CHANGED IN B2c: Herdr is an item of "More actions"; its click closes the popover (focus back on the trigger)
    await openMore();
    await p().waitForFunction(() => {
      const item = document.querySelector('[data-testid="di-scan-herdr"]');
      return item !== null && item.getAttribute('aria-disabled') !== 'true';
    });
    await p().click(sel('di-scan-herdr'));
    await p().waitForFunction((words) => document.querySelector('[data-testid="di-scan-herdr-reason"]')?.textContent?.trim() === words, LAUNCH_REFUSED);
    const reason = serverReasons.at(-1)!;
    assert.equal(reason, ROUTE_DETAIL, 'the reply carried the route\'s detail unchanged');
    assert.equal(await lineCount(sel('di-scan-herdr-reason')), 1);
    assert.doesNotMatch(await p().locator(sel('di-scan-herdr-reason')).innerText(), /herdr-attach|launcher was written/, 'no path in the default line');
    // CARRY1: with the detail still closed, the accessibility tree describes the control by the route's exact words (CHANGED IN B2c: read with More actions open)
    await openMore();
    const described = await axDescription(sel('di-scan-herdr'));
    assert.ok(described.includes(ROUTE_DETAIL), `the control is described by the route's words, in the accessibility tree: ${described}`);
    assert.equal(await openHerdrDetailByKeyboard(), ROUTE_DETAIL, 'the route\'s words, character for character');
    // admission unchanged: a refused launch leaves the control enabled (another click may be made), with no Check again (CHANGED IN B2c: read in More actions)
    await openMore();
    assert.equal(await heldNow(sel('di-scan-herdr')), false);
    assert.equal(await p().locator(sel('di-scan-herdr-recheck')).count(), 0);
    assert.deepEqual([...new Set(herdrSent.map((m) => m.op))].sort(), ['launch', 'status']);
  },
  STEPS_MS,
);

test(
  'A7, ARCHIVED: the line is the archived reason in Herdr\'s words, and the office\'s own answer (here: not set up) is still one keyboard disclosure away, exactly',
  async () => {
    mode = 'not_configured';
    await boot(ARCHIVED);
    await openMission(lifecycle.mission_ref, 'archived');
    await p().waitForFunction(() => document.querySelector('[data-testid="di-scan-herdr-more"]') !== null);
    await p().waitForFunction(() => document.querySelector('[data-testid="di-scan-herdr-note"]')?.textContent?.includes('not set up') === true);
    assert.equal(await ownWords(sel('di-scan-herdr-reason')), HERDR_ARCHIVED);
    assert.equal(await lineCount(sel('di-scan-herdr-reason')), 1);
    assert.equal(((await p().locator(sel('di-scan-herdr-more-summary')).textContent()) ?? '').trim(), 'The office’s answer about Herdr');
    assert.equal(await openHerdrDetailByKeyboard(), serverReasons.at(-1), 'the office\'s answer, character for character');
    // CHANGED IN B2c: held (aria-disabled), in More actions
    await openMore();
    assert.equal(await heldNow(sel('di-scan-herdr')), true, 'archived: Herdr stays held');
  },
  STEPS_MS,
);
