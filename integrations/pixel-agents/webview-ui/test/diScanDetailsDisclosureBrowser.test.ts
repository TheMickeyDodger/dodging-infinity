/**
 * A FINISHED Mission's details read as one product surface (Free UI streamline, 2026-11-04, approved Mission mn-92b246d…, A1/A3/A5/A6 and C1/C2), through the actual App in a real browser.
 *
 * A1: every section of Mission details leads with ONE short status line, and its detail (commands, policy text, store-path explanations, the irreversible Trash details) is a native
 * disclosure: closed by default, reached and operated by keyboard, named by its summary, its state in the DOM (`open`) and in the accessibility tree (expanded). Folded, never removed: the
 * detail is in the document while closed, and the Trash control is still DESCRIBED by the irreversible-deletion words.
 * A3: a Herdr note is drawn ONCE per screen (Mission details, Code Diff, Comments), and the read-only sentence once on the details.
 * A5: Full Trash stays refuse-safe HOLD: a click or a short key press sends nothing; only an unbroken hold sends ONE `deleteLocalData` for exactly this Mission, after which the hold is
 * withdrawn; a running Mission's Trash is refused with its reason; no other way to delete or clear anything is offered.
 * A6: the fixture label is on every screen.
 *
 * Office states (FIXTURE, labelled by the product's own fixture mode): the lifecycle export's `completed_ended_archived` (a Mission Core Mission, finished and filed into Archived) and
 * the progress-abandoned export (an archived Mission Core Mission with its local data read, and a running one beside it); both real projections of real producer answers. The page clock
 * is held at each state's own evaluation time, as the Mission preview holds it. Nothing answers the request panel or Herdr: every message the page sends is recorded and left unanswered.
 * Nothing is live.
 *
 * Captures: with DI_STREAMLINE_SHOTS=<absolute dir>, the views are written there as PNGs with details-manifest.json. Without it, nothing is written.
 *
 * Browser: the installed Chrome (playwright-core, channel `chrome`), else Playwright's Chromium; with neither, every test is skipped with an explicit message.
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

import type { DiOfficeState } from '../../core/src/messages.ts';
import { DI_DELETE_HOLD_MS } from '../src/constants.ts';

const STEPS_MS = 120_000;
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const VERSION = (JSON.parse(fs.readFileSync(path.join(root, '..', 'package.json'), 'utf-8')) as { version: string }).version;
const SHOTS = process.env['DI_STREAMLINE_SHOTS'] ?? null;
if (SHOTS !== null && !path.isAbsolute(SHOTS)) throw new Error('DI_STREAMLINE_SHOTS must be an absolute directory');

const LIFECYCLE = 'test/fixtures/di-office-state-mission-lifecycle-v2.FIXTURE.json';
const ABANDONED = 'test/fixtures/di-office-state-progress-abandoned-v2.FIXTURE.json';
const read = (file: string) => JSON.parse(fs.readFileSync(path.join(root, file), 'utf-8')) as Record<string, unknown>;
const lifecycle = read(LIFECYCLE) as { mission_ref: string; states: Record<string, DiOfficeState> };
/** FIXTURE: the finished Mission, filed into Archived (fixture mode on, so the product draws its own label). */
const FINISHED: DiOfficeState = { ...structuredClone(lifecycle.states.completed_ended_archived!), fixtureMode: true };
const FINISHED_REF = lifecycle.mission_ref;
const ABANDONED_STATE: DiOfficeState = { ...structuredClone((read(ABANDONED) as { state: DiOfficeState }).state), fixtureMode: true };
const DELETE_REF = 'synthetic_mission_core:mn-00000000000000000000000000000009';
const RUNNING_REF = 'synthetic_mission_core:mn-00000000000000000000000000000002';

/** The words this test holds the page to (copied, never read from the code under test). */
const ARCHIVED_READ_ONLY = 'Archived: this Mission is read-only. Nothing can be started, restarted, run or delivered from it.';
const IRREVERSIBLE = 'It cannot be undone.';

let server: ViteDevServer | null = null;
let wss: WebSocketServer | null = null;
let browser: Browser | null = null;
let page: Page | null = null;
let url = '';
let unavailable: string | null = null;
let socket: ServerSocket | null = null;
let resolveConnected: (ws: ServerSocket) => void = () => undefined;
let sentByPage: Array<Record<string, unknown>> = [];
const shots: Array<Record<string, unknown>> = [];

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
        // recorded, never answered
        sentByPage.push(msg);
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
  if (SHOTS !== null) fs.mkdirSync(SHOTS, { recursive: true });
}, STEPS_MS);

afterAll(async () => {
  await page?.close();
  await browser?.close();
  wss?.close();
  await server?.close();
  if (SHOTS !== null && shots.length > 0) {
    const fixture = (file: string) => ({ path: `webview-ui/${file}`, sha256: crypto.createHash('sha256').update(fs.readFileSync(path.join(root, file))).digest('hex') });
    fs.writeFileSync(
      path.join(SHOTS, 'details-manifest.json'),
      `${JSON.stringify(
        {
          fixture: true,
          label:
            'FIXTURE office states (real projections of real producer answers, fixture mode on) in the real App mounted from source by the Vite dev server (webview-ui/test/diScanDetailsDisclosureBrowser.test.ts). NOT a live backend, NOT the production bundle; nothing the page sent was answered.',
          fixtures: [{ ...fixture(LIFECYCLE), pick: 'states.completed_ended_archived' }, { ...fixture(ABANDONED), pick: 'state' }],
          shots,
        },
        null,
        2,
      )}\n`,
    );
  }
}, STEPS_MS);

beforeEach(async (ctx) => {
  if (unavailable) {
    console.warn(`SKIPPED (details disclosure): ${unavailable}`);
    ctx.skip();
    return;
  }
  await page?.close();
  page = null;
  sentByPage = [];
}, STEPS_MS);

const p = () => page!;
const sel = (id: string) => `[data-testid="${id}"]`;
const rowSel = (ref: string) => `[data-testid="di-missions-row"][data-mission-ref="${ref}"]`;

async function snap(name: string, note: string, into?: string): Promise<void> {
  if (SHOTS === null) return;
  if (into) await p().locator(into).first().scrollIntoViewIfNeeded();
  await p().waitForTimeout(150);
  const file = `${name}.png`;
  await p().screenshot({ path: path.join(SHOTS, file) });
  shots.push({ file, note, viewport: p().viewportSize(), sha256: crypto.createHash('sha256').update(fs.readFileSync(path.join(SHOTS, file))).digest('hex') });
}

/** Boot the real App on `state`, the page clock held at the state's own evaluation time (as the Mission preview holds it). */
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
async function details(ref: string, filter: 'archived' | 'all'): Promise<void> {
  await p().click(sel('di-rail-missions'));
  await p().click(sel(`di-missions-status-${filter}`));
  await p().click(`${rowSel(ref)} ${sel('di-missions-open')}`);
  await p().waitForSelector('[data-testid="di-scan-stage"][data-view="mission"]');
}

/** How many lines of text the element draws (distinct line boxes of its text, measured in the browser). */
const linesOf = (selector: string) =>
  p().locator(selector).first().evaluate((el) => {
    const range = document.createRange();
    range.selectNodeContents(el);
    const tops = new Set<number>();
    // only the line boxes DRAWN inside the element's own box: a line a clamp hides is still laid out (it has a box) but is not drawn
    const box = el.getBoundingClientRect();
    for (const r of range.getClientRects())
      if (r.width > 0 && r.height > 0 && r.top >= box.top - 1 && r.bottom <= box.bottom + 1) tops.add(Math.round(r.top));
    return tops.size;
  });
/** The accessibility tree's view of the element: its role, name, description and expanded state (Chrome's own computation, by CDP). */
async function ax(selector: string): Promise<{ role: string; name: string; description: string; expanded: boolean | null }> {
  const cdp = await p().context().newCDPSession(p());
  const { root: doc } = (await cdp.send('DOM.getDocument', { depth: 0 })) as { root: { nodeId: number } };
  const { nodeId } = (await cdp.send('DOM.querySelector', { nodeId: doc.nodeId, selector })) as { nodeId: number };
  const { nodes } = (await cdp.send('Accessibility.getPartialAXTree', { nodeId, fetchRelatives: false })) as {
    nodes: Array<{ role?: { value: string }; name?: { value: string }; description?: { value: string }; properties?: Array<{ name: string; value: { value: unknown } }> }>;
  };
  await cdp.detach();
  const n = nodes[0]!;
  const expanded = n.properties?.find((prop) => prop.name === 'expanded')?.value.value;
  return { role: n.role?.value ?? '', name: n.name?.value ?? '', description: n.description?.value ?? '', expanded: typeof expanded === 'boolean' ? expanded : null };
}
/** Every Herdr note DRAWN on the current screen (the stage's line, the Code Diff header's, the rail's), with its words. */
const herdrNotesDrawn = () =>
  p().evaluate(() =>
    ['di-scan-herdr-reason', 'di-scan-herdr-open', 'di-rail-herdr-note']
      .flatMap((id) => [...document.querySelectorAll<HTMLElement>(`[data-testid="${id}"]`)])
      .filter((el) => el.getClientRects().length > 0 && el.closest('details:not([open])') === null)
      .map((el) => el.getAttribute('data-testid')),
  );

test(
  'A1, A3, A6 ON A FINISHED MISSION: each section leads with ONE status line; its detail is a closed, named, keyboard disclosure that holds the words; the read-only sentence and the Herdr note are drawn once',
  async () => {
    await boot(FINISHED);
    await details(FINISHED_REF, 'archived');
    await snap('details-finished-01-default', 'A finished Mission (Ended, filed into Archived): each section one status line, details folded.');
    assert.ok(await p().locator(sel('di-fixture-banner-in-stage')).isVisible(), 'A6: the fixture label is on the screen');

    // ONE status line per section, each drawn on one line
    const lines: Record<string, string> = {
      delivery: sel('di-scan-delivery-status'),
      state: `${sel('di-scan-state-actions')} .di-scan-status-line`,
      herdr: sel('di-scan-herdr-reason'),
      trash: sel('di-scan-trash-reason'),
    };
    for (const [section, selector] of Object.entries(lines)) {
      assert.equal(await p().locator(selector).count(), 1, `${section}: one status line`);
      assert.ok(await p().locator(selector).isVisible(), `${section}: its line is drawn`);
      assert.equal(await linesOf(selector), 1, `${section}: its status line is ONE line`);
    }
    assert.match((await p().locator(lines.delivery!).innerText()).trim(), /^Not offered: this Mission is archived/);

    // the disclosures: closed by default, named, and the words they hold are in the document, folded
    const folds = await p()
      .locator('[data-testid="di-scan-stage"] details.di-scan-more')
      .evaluateAll((els) => els.map((d) => ({ id: d.getAttribute('data-testid'), open: (d as HTMLDetailsElement).open, name: d.querySelector('summary')?.textContent?.trim() ?? '' })));
    assert.deepEqual(
      folds.map((f) => f.id),
      ['di-scan-delivery-more', 'di-scan-state-more', 'di-scan-herdr-more', 'di-scan-trash-more'],
      'Delivery, the state actions, Herdr (A7: on an archived Mission the office\'s answer about Herdr) and the Trash each fold their detail',
    );
    for (const f of folds) {
      assert.equal(f.open, false, `${f.id} is closed by default`);
      assert.ok(f.name.length > 0, `${f.id} is named`);
    }
    for (const [id, words] of [
      ['di-scan-delivery-link', /delivery/i],
      ['di-scan-delivery-archived', /archived/],
      ['di-scan-delivery-end', /Delivery ends with an open pull request\./],
      ['#di-scan-trash-note', new RegExp(IRREVERSIBLE.replace('.', '\\.'))],
    ] as const) {
      const at = id.startsWith('#') ? `[id="${id.slice(1)}"]` : sel(id);
      assert.equal(await p().locator(at).count(), 1, `${id} is in the document`);
      assert.match((await p().locator(at).textContent()) ?? '', words, `${id} holds its words`);
      assert.equal(await p().locator(at).isVisible(), false, `${id} is folded by default`);
    }
    assert.equal(await p().locator(`[data-testid="di-scan-stage"] [id="di-scan-trash-note"]`).isVisible(), false);

    // A3: the read-only sentence, drawn once; one Herdr note on this screen (the stage's line; the rail does not repeat it)
    const drawn = await p().locator(sel('di-scan-stage')).innerText();
    assert.equal(drawn.split(ARCHIVED_READ_ONLY).length - 1, 1, 'the read-only sentence is drawn once');
    assert.deepEqual(await herdrNotesDrawn(), ['di-scan-herdr-reason'], 'one Herdr note on Mission details');

    // the accessibility tree: each summary is a named disclosure control, collapsed
    const trashSummary = await ax('[data-testid="di-scan-trash-more-summary"]');
    assert.equal(trashSummary.expanded, false, 'the Trash detail is collapsed in the accessibility tree');
    assert.match(trashSummary.name, /cannot be undone/, `the Trash detail's name says it matters: ${trashSummary.name}`);
    // A5: folded is not hidden from the control: the Trash is DESCRIBED by the irreversible words while they are closed
    const trash = await ax('[data-testid="di-scan-trash"]');
    assert.match(trash.description, new RegExp(IRREVERSIBLE.replace('.', '\\.')), `the Trash control is described by the irreversible words: ${trash.description}`);
    // Review round 1, CARRY1: the Herdr control, measured the same way (Chrome's own description, by CDP), while its fold is closed: it is described by its
    // always-present copy, i.e. the words the closed fold holds
    const herdrNote = ((await p().locator('[id="di-scan-herdr-desc"]').textContent()) ?? '').trim();
    assert.notEqual(herdrNote, '', 'the Herdr fold has words to describe the control with');
    assert.equal(await p().locator(sel('di-scan-herdr-more')).evaluate((d) => (d as HTMLDetailsElement).open), false, 'the Herdr fold is closed');
    const herdrAx = await ax('[data-testid="di-scan-herdr"]');
    assert.ok(herdrAx.description.includes(herdrNote), `the Herdr control is described by the fold's words: ${herdrAx.description}`);
    // (this finished Mission draws no state action: those are measured in diScanDecideNavigationBrowser, CARRY1, where four are drawn)

    // KEYBOARD: Tab reaches the Trash detail's summary; Enter opens it, Space closes it; the state is in the DOM and in the tree
    for (let i = 0; i < 80 && (await p().evaluate(() => document.activeElement?.getAttribute('data-testid'))) !== 'di-scan-trash-more-summary'; i++)
      await p().keyboard.press('Tab');
    assert.equal(await p().evaluate(() => document.activeElement?.getAttribute('data-testid')), 'di-scan-trash-more-summary', 'reached by Tab');
    await p().keyboard.press('Enter');
    assert.equal(await p().locator(sel('di-scan-trash-more')).evaluate((d) => (d as HTMLDetailsElement).open), true, 'Enter opens it');
    assert.equal((await ax('[data-testid="di-scan-trash-more-summary"]')).expanded, true, 'expanded in the accessibility tree');
    assert.ok(await p().locator(`[id="di-scan-trash-note"]`).isVisible(), 'the irreversible details are shown');
    assert.match(await p().locator(`[id="di-scan-trash-note"]`).innerText(), new RegExp(IRREVERSIBLE.replace('.', '\\.')));
    await snap('details-finished-02-trash-expanded', 'The Trash detail opened by keyboard: what deleting removes and what remains (it cannot be undone).', sel('di-scan-trash-more'));
    await p().keyboard.press(' ');
    assert.equal(await p().locator(sel('di-scan-trash-more')).evaluate((d) => (d as HTMLDetailsElement).open), false, 'Space closes it');

    // the other folds open the same way, and hold their words
    for (const id of ['di-scan-delivery-more', 'di-scan-state-more']) {
      await p().locator(sel(`${id}-summary`)).focus();
      await p().keyboard.press('Enter');
      assert.equal(await p().locator(sel(id)).evaluate((d) => (d as HTMLDetailsElement).open), true, `${id} opens by keyboard`);
    }
    assert.ok(await p().locator(sel('di-scan-delivery-link')).isVisible(), 'the delivery link reason is shown on demand');
    await snap('details-finished-03-all-expanded', 'Every fold opened: the Delivery detail and the state reasons on demand.', sel('di-scan-delivery-more'));

    // A3 across the Mission's screens: Code Diff and Comments each draw at most one Herdr note
    for (const view of ['diff', 'comments'] as const) {
      await p().click(sel(view === 'diff' ? 'di-scan-code-diff' : 'di-scan-comments'));
      await p().waitForSelector(`[data-testid="di-scan-stage"][data-view="${view}"]`);
      assert.ok((await herdrNotesDrawn()).length <= 1, `${view}: at most one Herdr note (${JSON.stringify(await herdrNotesDrawn())})`);
      await p().click(sel('di-scan-back'));
      await p().waitForSelector('[data-testid="di-scan-stage"][data-view="mission"]');
    }
    // nothing this view did sent a delete or any other write
    assert.deepEqual(sentByPage.filter((m) => m.type === 'diMissionRequest' && m.op !== 'refresh').map((m) => m.op), []);
  },
  STEPS_MS,
);

test(
  'A5, FULL TRASH STAYS REFUSE-SAFE HOLD: a click or a short key press sends nothing; one unbroken hold sends ONE delete for exactly this Mission and the hold is withdrawn; a running Mission is refused with its reason; nothing else deletes',
  async () => {
    await boot(ABANDONED_STATE);
    await details(DELETE_REF, 'archived');
    const deletes = () => sentByPage.filter((m) => m.type === 'diMissionRequest' && m.op === 'deleteLocalData');
    const hold = p().locator('[data-testid="di-scan-trash"][data-hold="true"]');
    assert.equal(await hold.count(), 1, 'the owner\'s hold is offered on the archived Mission');
    assert.equal((await p().locator(sel('di-scan-trash-reason')).innerText()).trim(), 'Hold for a moment to delete.');
    assert.match((await p().locator(sel('di-scan-trash-more-summary')).innerText()).trim(), /^Deleting cannot be undone/, 'the warning is drawn, not only folded');

    // a click does nothing
    await hold.click();
    // a short key press does nothing
    await hold.focus();
    await p().keyboard.down(' ');
    await p().waitForTimeout(200);
    await p().keyboard.up(' ');
    // a hold broken before its time does nothing
    await hold.hover();
    await p().mouse.down();
    await p().waitForTimeout(DI_DELETE_HOLD_MS / 3);
    await p().mouse.up();
    await p().waitForTimeout(DI_DELETE_HOLD_MS + 300);
    assert.equal(deletes().length, 0, 'no click, short press or broken hold sends a delete');

    // one unbroken hold sends exactly one, for exactly this Mission
    await hold.hover();
    await p().mouse.down();
    await p().waitForTimeout(DI_DELETE_HOLD_MS + 400);
    await p().mouse.up();
    await p().waitForFunction(() => document.querySelector('[data-testid="di-scan-trash"][data-hold="true"]') === null);
    assert.equal(deletes().length, 1, 'one hold, one delete');
    assert.equal(deletes()[0]!.missionRef, DELETE_REF, 'for exactly this Mission (its source-qualified ref)');
    assert.match((await p().locator(sel('di-scan-trash-reason')).innerText()).trim(), /waiting for its answer, so it is not offered again/, 'withdrawn while it waits');
    await snap('details-hold-sent', 'After one unbroken hold: one delete sent (never answered here), the hold withdrawn.', sel('di-scan-trash-reason'));

    // no purge: no control anywhere offers to clear, purge or delete anything else
    const purge = await p().evaluate(() =>
      [...document.querySelectorAll('button, [role="button"], summary')].map((b) => `${b.textContent ?? ''} ${b.getAttribute('aria-label') ?? ''}`).filter((t) => /purge|erase|empty (the )?trash|delete all|clear all/i.test(t)),
    );
    assert.deepEqual(purge, [], 'no purge control');
    // the only write the page ever sent is that one delete
    assert.deepEqual(sentByPage.filter((m) => m.type === 'diMissionRequest' && m.op !== 'refresh').map((m) => m.op), ['deleteLocalData']);

    // a running Mission refuses, with its reason drawn
    await details(RUNNING_REF, 'all');
    assert.equal(await p().locator('[data-testid="di-scan-trash"][data-hold="true"]').count(), 0);
    assert.equal(await p().locator(sel('di-scan-trash')).isDisabled(), true);
    assert.equal((await p().locator(sel('di-scan-trash-reason')).innerText()).trim(), 'A running Mission cannot be deleted.');
    assert.equal(deletes().length, 1, 'nothing more was sent');
  },
  STEPS_MS,
);
