/**
 * Round 11 (C1): selection and freshness through the ACTUAL reconciliation path — the real app
 * entry (`App`, `useExtensionMessages`, `OfficeState` canvas reconciliation with `removeAgent`,
 * the panel's selection polling) in a real browser, fed over a REAL WebSocket that this test
 * serves at `/ws`, with office states the real server (`DiOffice`) produced for each transition
 * (`test/fixtures/di-office-state-transitions-v2.FIXTURE.json`). Nothing here re-renders the
 * panel with a selection the test holds itself: the office selection is the App's own
 * `OfficeState` (recorded by the harness probe `test/harness/diAppProbe.ts`, which changes no
 * behaviour), observed as `removeAgent` leaves it, and changed only exactly as the canvas click
 * handler changes it (`OfficeCanvas.tsx`: a character click sets selection and camera follow;
 * an empty-floor click clears both).
 *
 * Browser: installed Chrome (playwright-core channel `chrome`), else Playwright's Chromium; with
 * neither, every test is skipped with an explicit message.
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
import type { WebSocket } from 'ws';
import { WebSocketServer } from 'ws';

import type { DiOfficeState, DiRuntimeAgent } from '../../core/src/messages.ts';

/** Each test walks several transitions with real polls: its own budget. */
const STEPS_MS = 90_000;

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const doc = JSON.parse(
  fs.readFileSync(
    path.join(root, 'test/fixtures/di-office-state-transitions-v2.FIXTURE.json'),
    'utf-8',
  ),
) as {
  removedRuntimeAgentRef: string;
  removedMemberRef: string;
  states: Record<'live' | 'removed' | 'liveAgain' | 'stale' | 'unavailable', DiOfficeState>;
};
const S = doc.states;
const runtimeOf = (ref: string): DiRuntimeAgent =>
  S.live.runtimeAgents!.agents.find((a) => a.agentRef === ref)!;
const goneAgent = runtimeOf(doc.removedRuntimeAgentRef);
const keptAgent = S.live.runtimeAgents!.agents.find(
  (a) => a.identityVerified && a.agentRef !== goneAgent.agentRef,
)!;
const goneMember = S.live.missions.missions
  .flatMap((m) => m.members.members)
  .find((m) => m.memberRef === doc.removedMemberRef)!;

/** The fields of the App's own OfficeState that the test observes (via the harness probe). */
interface Office {
  selectedAgentId: number | null;
  cameraFollowId: number | null;
  characters: Map<number, { matrixEffect: string | null }>;
}

let server: ViteDevServer | null = null;
let wss: WebSocketServer | null = null;
let browser: Browser | null = null;
let page: Page | null = null;
let url = '';
let unavailable: string | null = null;
let socket: WebSocket | null = null;
let connected: Promise<WebSocket> = Promise.resolve(null as unknown as WebSocket);
let resolveConnected: (ws: WebSocket) => void = () => undefined;
/**
 * A FIRST connection is ready when the client says so (`webviewReady`); a RECONNECT is ready on
 * open — a reconnecting client sends no new webviewReady, and the real office server pushes the
 * office state to it unprompted.
 */
let readyOnOpen = false;
const expectConnection = (reconnect = false) => {
  readyOnOpen = reconnect;
  connected = new Promise((resolve) => (resolveConnected = resolve));
};

beforeAll(async () => {
  server = await createServer({
    configFile: path.resolve(root, 'vite.config.ts'),
    // Vite's own WebSocket (HMR) is off: this test IS the office server at /ws.
    server: { port: 0, strictPort: false, hmr: false, ws: false },
    logLevel: 'silent',
  });
  await server.listen();
  wss = new WebSocketServer({ noServer: true });
  server.httpServer!.on('upgrade', (req, sock, head) => {
    // Anything else (the Vite client's own HMR socket) is refused at once: Chrome lets only ONE
    // WebSocket handshake per host be pending, so an unanswered one would starve the app's /ws.
    if (!req.url?.startsWith('/ws')) {
      sock.destroy();
      return;
    }
    wss!.handleUpgrade(req, sock, head, (ws) => {
      if (readyOnOpen) {
        socket = ws;
        resolveConnected(ws);
        return;
      }
      // Like the real office server: the office state goes to a client that said it is READY
      // (its message handlers are mounted), never into a socket nobody listens to yet.
      ws.on('message', (data) => {
        const msg = JSON.parse(String(data)) as { type?: string };
        if (msg.type === 'webviewReady' && socket !== ws) {
          socket = ws;
          resolveConnected(ws);
        }
      });
    });
  });
  const addr = server.httpServer?.address();
  const port = typeof addr === 'object' && addr !== null ? addr.port : 0;
  url = `http://localhost:${port}/test/harness/di-app.html`;
  try {
    browser = await chromium.launch({ channel: 'chrome', headless: true });
  } catch {
    try {
      browser = await chromium.launch({ headless: true });
    } catch (e) {
      unavailable = `no Chrome or Playwright Chromium to drive: ${String(e).split('\n')[0]}`;
    }
  }
}, 120_000);

afterAll(async () => {
  await browser?.close();
  wss?.close();
  await server?.close();
}, STEPS_MS);

beforeEach(async (ctx) => {
  if (unavailable) {
    console.warn(`SKIPPED (reconciliation regression): ${unavailable}`);
    ctx.skip();
    return;
  }
  await page?.close();
  expectConnection();
  page = await browser!.newPage({ viewport: { width: 1600, height: 1000 } });
  await page.goto(url, { waitUntil: 'domcontentloaded' });
  await connected;
}, STEPS_MS);

const p = () => page!;
const send = (state: DiOfficeState) => socket!.send(JSON.stringify(state));
const selection = () =>
  p().evaluate(() => {
    const office = (window as unknown as { __diHarnessOffice: Office }).__diHarnessOffice;
    return { selectedAgentId: office.selectedAgentId, cameraFollowId: office.cameraFollowId };
  });
/** Exactly what the canvas click handler does: select (and follow) a character, or clear both. */
const selectOnCanvas = (id: number | null) =>
  p().evaluate((i) => {
    const office = (window as unknown as { __diHarnessOffice: Office }).__diHarnessOffice;
    office.selectedAgentId = i;
    office.cameraFollowId = i;
  }, id);
async function waitForCharacter(id: number) {
  await p().waitForFunction(
    (i) => {
      const office = (window as unknown as { __diHarnessOffice?: Office }).__diHarnessOffice;
      const character = office?.characters.get(i);
      return character !== undefined && character.matrixEffect !== 'despawn';
    },
    id,
    { timeout: 30_000 },
  );
}
async function openPanel() {
  await p().waitForSelector(
    '[data-testid="di-office-panel"], [data-testid="di-office-panel-collapsed"]',
    { timeout: 30_000 },
  );
  const collapsed = await p().$('[data-testid="di-office-panel-collapsed"]');
  if (collapsed) await collapsed.click();
  await p().waitForSelector('[data-testid="di-selected-agent"]', { timeout: 30_000 });
}
const inspector = () =>
  p().$eval('[data-testid="di-selected-agent"]', (el) => (el as HTMLElement).innerText);
const inspectorHtml = () => p().$eval('[data-testid="di-selected-agent"]', (el) => el.innerHTML);
/** Wait until the inspector's text matches (the panel polls the office selection). */
async function inspectorMatches(re: RegExp, timeout = 6_000): Promise<string> {
  try {
    await p().waitForFunction(
      ([src, flags]) =>
        new RegExp(src, flags).test(
          (document.querySelector('[data-testid="di-selected-agent"]') as HTMLElement | null)
            ?.innerText ?? '',
        ),
      [re.source, re.flags] as const,
      { timeout },
    );
  } catch {
    assert.fail(`the inspector never matched ${re}; it reads:\n${await inspector()}`);
  }
  return inspector();
}
async function selectionBecomes(id: number | null) {
  await p().waitForFunction(
    (i) =>
      (window as unknown as { __diHarnessOffice: Office }).__diHarnessOffice.selectedAgentId === i,
    id,
    { timeout: 10_000 },
  );
}
/** Several selection polls (250 ms) and client checks (1 s) pass. */
const settle = () => p().waitForTimeout(1_500);

async function boot() {
  send(S.live);
  await openPanel();
  await waitForCharacter(goneAgent.characterId!);
}

test(
  'record removal: removeAgent clears the office selection; the missing-record notice survives later polls and updates',
  async () => {
    await boot();
    await selectOnCanvas(goneAgent.characterId);
    await inspectorMatches(new RegExp(goneAgent.agent));
    send(S.removed);
    // The ACTUAL path: canvas reconciliation removed the character and cleared the selection.
    await selectionBecomes(null);
    await settle();
    let text = await inspectorMatches(/no longer in the office state/);
    assert.match(text, new RegExp(goneAgent.logical));
    assert.match(text, /current/i); // freshness of the evidence the notice rests on
    assert.equal((await selection()).selectedAgentId, null); // nothing selected in its place
    // A later update that still lacks it: the notice stays, now with the backend's reason.
    send(S.unavailable);
    await settle();
    text = await inspectorMatches(/no longer in the office state/);
    assert.match(text, /could not reach its backend/);
    assert.ok((await inspectorHtml()).includes('transport:backend_unreachable'));
    assert.match(text, /NOT current/);
    // The record is listed again: the same record is shown, and the canvas selects nothing.
    send(S.liveAgain);
    await settle();
    text = await inspectorMatches(new RegExp(`${goneAgent.agent}`));
    assert.doesNotMatch(text, /no longer in the office state/);
    assert.equal((await selection()).selectedAgentId, null);
  },
  STEPS_MS,
);

test(
  'live → unavailable: a selected member leaves with the backend; the notice carries the canonical reason',
  async () => {
    await boot();
    await waitForCharacter(goneMember.characterId!);
    await selectOnCanvas(goneMember.characterId);
    let text = await inspectorMatches(new RegExp(goneMember.logical));
    assert.match(text, /Team member/);
    assert.match(text, /observation is current/);
    send(S.unavailable);
    await selectionBecomes(null);
    await settle();
    text = await inspectorMatches(/no longer in the office state/);
    assert.match(text, new RegExp(goneMember.logical));
    assert.match(text, /Missions list/);
    assert.match(text, /could not reach its backend/);
    assert.ok((await inspectorHtml()).includes('transport:backend_unreachable'));
  },
  STEPS_MS,
);

test(
  'live → stale (expired): selected records are kept and qualified NOT current; nothing still claims "is running"',
  async () => {
    await boot();
    await p().focus('[data-testid="di-unverified-row"]');
    await p().keyboard.press('Enter');
    let text = await inspectorMatches(/not established/i);
    assert.match(text, /observation is current/);
    send(S.stale);
    await settle();
    text = await inspectorMatches(/NOT current/);
    assert.match(text, /not established/i); // the same record, still
    assert.match(text, /expired/);
    assert.doesNotMatch(text, /is running/);
    const listing = await p().$eval(
      '[data-testid="di-unverified"]',
      (el) => (el as HTMLElement).innerText,
    );
    assert.doesNotMatch(listing, /is running/);
    assert.match(listing, /NOT current/);
    // A drawn runtime agent selected while live, then the office expires: its character is
    // removed (selection cleared by the canvas) and the record stays, qualified.
    send(S.live);
    await waitForCharacter(keptAgent.characterId!);
    await selectOnCanvas(keptAgent.characterId);
    // The panel polls the office selection (250 ms): wait until it shows THIS agent first.
    await inspectorMatches(new RegExp(keptAgent.agent));
    await inspectorMatches(/observation is current/);
    send(S.stale);
    await selectionBecomes(null);
    await settle();
    text = await inspectorMatches(/NOT current/);
    assert.match(text, new RegExp(keptAgent.agent));
    assert.match(text, /Not drawn now/);
  },
  STEPS_MS,
);

test(
  'client stale: the socket drops; the drawn selection stays and is qualified NOT current; a new office state makes it current',
  async () => {
    await boot();
    await selectOnCanvas(keptAgent.characterId);
    await inspectorMatches(new RegExp(keptAgent.agent));
    await inspectorMatches(/observation is current/);
    expectConnection(true);
    socket!.close();
    const text = await inspectorMatches(/NOT current/);
    assert.match(text, new RegExp(keptAgent.agent));
    assert.match(text, /not connected/);
    assert.equal((await selection()).selectedAgentId, keptAgent.characterId);
    await connected; // the transport reconnects by itself
    send(S.live);
    await inspectorMatches(/observation is current/);
  },
  STEPS_MS,
);

test(
  'deliberate deselection and reselection: only the user replaces the selection; the notice survives updates',
  async () => {
    await boot();
    await selectOnCanvas(goneAgent.characterId);
    await inspectorMatches(new RegExp(goneAgent.agent));
    await selectOnCanvas(null); // the empty-floor click
    await inspectorMatches(/Select a character/);
    send(S.liveAgain); // an update must not resurrect a deliberate deselection
    await settle();
    await inspectorMatches(/Select a character/);
    await selectOnCanvas(goneAgent.characterId); // reselection
    await inspectorMatches(new RegExp(goneAgent.agent));
    send(S.removed);
    await selectionBecomes(null);
    await settle();
    await inspectorMatches(/no longer in the office state/);
    send(S.removed); // a subsequent update: still the notice, still nothing else
    await settle();
    const text = await inspectorMatches(/no longer in the office state/);
    assert.match(text, new RegExp(goneAgent.logical));
    assert.equal((await selection()).selectedAgentId, null);
    await selectOnCanvas(keptAgent.characterId); // the user's own new choice replaces it
    await inspectorMatches(new RegExp(keptAgent.agent));
    assert.doesNotMatch(await inspector(), /no longer in the office state/);
    await selectOnCanvas(null);
    await inspectorMatches(/Select a character/);
  },
  STEPS_MS,
);
