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
/**
 * feature13: the evidence library lives in the full-size inspector. Reach it the way a person does:
 * the Mission card's labelled "Evidence library" doorway, by keyboard (focus, Enter).
 */
async function openEvidence(owner?: { state: DiOfficeState; artifactId: string }) {
  // The Mission that LISTS the artifact owns the doorway (a state can hold several libraries).
  const ownerRef = owner
    ? owner.state.missions.missions.find((m) =>
        m.evidence?.artifacts.items.some((a) => a.artifactId === owner.artifactId),
      )?.missionRef
    : undefined;
  if (owner && ownerRef === undefined) assert.fail(`no Mission lists ${owner.artifactId}`);
  const door = ownerRef
    ? `[data-mission-ref="${ownerRef}"] [data-testid="di-open-evidence"]`
    : '[data-testid="di-open-evidence"]';
  await p().waitForSelector(door, { timeout: 30_000 });
  await p().focus(door);
  await p().keyboard.press('Enter');
  await p().waitForSelector('[role="dialog"] [data-testid="di-evidence-library"]', {
    timeout: 30_000,
  });
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

/**
 * Item 2 (evidence library) through the ACTUAL App path: the office state arrives over the real
 * socket, a key press on an artifact row makes the App SEND `diEvidenceRead` (exactly the
 * admitted reference — four keys, no path), this test answers with the diEvidenceContent the real
 * office relayed for it (`di-office-state-evidence-v2.FIXTURE.json`), and `useExtensionMessages`
 * delivers it to the panel, which renders it inert.
 */
test(
  'item 2: an evidence read leaves the App as a reference only; the answer renders as inert text',
  async () => {
    const evidence = JSON.parse(
      fs.readFileSync(
        path.join(root, 'test/fixtures/di-office-state-evidence-v2.FIXTURE.json'),
        'utf-8',
      ),
    ) as {
      states: { live: DiOfficeState };
      replies: Record<
        string,
        { artifactId: string; cursor: string; content: { text: string } | null }
      >;
    };
    const html = evidence.replies.admitted_html;
    const received: Array<Record<string, unknown>> = [];
    socket!.on('message', (data) => {
      const msg = JSON.parse(String(data)) as Record<string, unknown>;
      if (msg.type !== 'diEvidenceRead') return;
      received.push(msg);
      const reply = Object.values(evidence.replies).find(
        (r) => r.artifactId === msg.artifactId && r.cursor === msg.cursor,
      );
      if (reply) socket!.send(JSON.stringify(reply));
    });
    send(evidence.states.live);
    await openPanel();
    await openEvidence();
    const row = `[data-testid="di-evidence-artifact-row"][data-artifact-id="${html.artifactId}"]`;
    await p().waitForSelector(row, { timeout: 30_000 });
    await p().focus(row);
    await p().keyboard.press('Enter');
    await p().waitForSelector('[data-testid="di-evidence-text"]', { timeout: 10_000 });
    const pre = await p().$eval('[data-testid="di-evidence-text"]', (e) => ({
      text: e.textContent ?? '',
      elements: e.querySelectorAll('*').length,
    }));
    assert.equal(pre.text, html.content!.text);
    assert.equal(pre.elements, 0, 'no element is parsed out of artifact text');
    assert.equal(received.length, 1);
    assert.deepEqual(Object.keys(received[0]).sort(), [
      'artifactId',
      'cursor',
      'missionRef',
      'type',
    ]);
    assert.equal(received[0].artifactId, html.artifactId);
    assert.equal(received[0].cursor, html.cursor);
  },
  STEPS_MS,
);

/** The evidence fixture's office states and relayed answers (one real DiOffice). */
interface EvidenceFixture {
  states: Record<'live' | 'stale' | 'links64' | 'links70', DiOfficeState>;
  replies: Record<string, { artifactId: string; cursor: string; content: { text: string } | null }>;
}
const evidenceFixture = (): EvidenceFixture =>
  JSON.parse(
    fs.readFileSync(
      path.join(root, 'test/fixtures/di-office-state-evidence-v2.FIXTURE.json'),
      'utf-8',
    ),
  ) as EvidenceFixture;

/** Every link entry a verification record renders: listed buttons and unlisted ids. */
const linkEntries = () =>
  p().$$eval(
    '[data-testid="di-evidence-record"] [data-testid="di-evidence-linked-artifact"], ' +
      '[data-testid="di-evidence-record"] [data-testid="di-evidence-unlisted-link"]',
    (els) => els.map((e) => (e as HTMLElement).innerText),
  );
const linkCount = () =>
  p().$eval('[data-testid="di-evidence-link-count"]', (e) => (e as HTMLElement).innerText);

/**
 * Round 15 F4 through the ACTUAL App path: the office states a real DiOffice broadcast for a
 * record linking 64 artifacts (Mission Core's per-record bound) and 70, sent over the real
 * socket. All 64 render, the last included, under their TRUE count; past the cap the count
 * says so — a cut is never silent.
 */
test(
  'item 2 (round 15 F4): every recorded link renders under its true count; a cap is announced',
  async () => {
    const evidence = evidenceFixture();
    const ids =
      evidence.states.links64.missions.missions[0].evidence!.verification.items[0].artifactIds;
    send(evidence.states.links64);
    await openPanel();
    await openEvidence();
    await p().waitForSelector('[data-testid="di-evidence-link-count"]', { timeout: 30_000 });
    assert.equal(ids.length, 64);
    let entries = await linkEntries();
    assert.equal(entries.length, 64, 'every one of the 64 recorded links renders');
    assert.ok(
      entries.some((t) => t.includes(ids[63])),
      'the 64th link is there',
    );
    assert.ok(
      entries.some((t) => t.includes(ids[40])),
      'a later link is there',
    );
    let count = await linkCount();
    assert.match(count, /64 recorded links/);
    assert.doesNotMatch(count, /showing/);
    send(evidence.states.links70);
    await p().waitForFunction(
      () =>
        /70 recorded links/.test(
          (document.querySelector('[data-testid="di-evidence-link-count"]') as HTMLElement | null)
            ?.innerText ?? '',
        ),
      undefined,
      { timeout: 10_000 },
    );
    count = await linkCount();
    assert.match(count, /showing the first 64/);
    assert.match(count, /6 not shown/);
    entries = await linkEntries();
    assert.equal(entries.length, 64);
  },
  STEPS_MS,
);

/** Answer every diEvidenceRead on the CURRENT socket with the fixture's relayed reply. */
function answerReads(evidence: EvidenceFixture, received: Array<Record<string, unknown>>) {
  socket!.on('message', (data) => {
    const msg = JSON.parse(String(data)) as Record<string, unknown>;
    if (msg.type !== 'diEvidenceRead') return;
    received.push(msg);
    const reply = Object.values(evidence.replies).find(
      (r) => r.artifactId === msg.artifactId && r.cursor === msg.cursor,
    );
    if (reply) socket!.send(JSON.stringify(reply));
  });
}
const rowOf = (artifactId: string) =>
  `[data-testid="di-evidence-artifact-row"][data-artifact-id="${artifactId}"]`;
async function readRow(artifactId: string) {
  await p().waitForSelector(rowOf(artifactId), { timeout: 30_000 });
  await p().focus(rowOf(artifactId));
  await p().keyboard.press('Enter');
}
const shownText = () => p().$eval('[data-testid="di-evidence-text"]', (e) => e.textContent ?? '');
const staleBanner = () =>
  p().$eval('[data-testid="di-evidence-stale"]', (e) => (e as HTMLElement).innerText);
const readsLine = () =>
  p().$eval('[data-testid="di-evidence-reads"]', (e) => (e as HTMLElement).innerText);
/** How many content viewers show text (a COUNT: never an element handle in an assertion). */
const textCount = () => p().$$eval('[data-testid="di-evidence-text"]', (els) => els.length);
async function textGone(timeout = 10_000) {
  await p().waitForSelector('[data-testid="di-evidence-text"]', { state: 'detached', timeout });
}

/**
 * Round 15 F2 through the ACTUAL App path (real socket, real useExtensionMessages, real panel):
 * a socket that drops leaves the client unable to vouch for anything it showed. The listing
 * must read STALE in the past tense, reads must close, read content must be HIDDEN and
 * CLEARED — and a bare reconnect, or even a new office state, must not bring the old answer
 * back: only a new read does.
 */
test(
  'item 2 (round 15 F2): a disconnect hides and clears read content; reconnecting restores nothing until a new read',
  async () => {
    const evidence = evidenceFixture();
    const text = evidence.replies.admitted_text;
    const received: Array<Record<string, unknown>> = [];
    answerReads(evidence, received);
    send(evidence.states.live);
    await openPanel();
    await openEvidence();
    await readRow(text.artifactId);
    await p().waitForSelector('[data-testid="di-evidence-text"]', { timeout: 10_000 });
    assert.equal(await shownText(), text.content!.text);
    expectConnection(true);
    socket!.close();
    await textGone();
    let banner = await staleBanner();
    assert.match(banner, /as last received/);
    assert.match(banner, /transport_/);
    assert.match(await readsLine(), /closed/);
    await connected; // a bare reconnect: no office state yet
    await settle();
    assert.equal(await textCount(), 0);
    banner = await staleBanner();
    assert.match(banner, /transport_reconnected_awaiting_office_state/);
    // A late answer (e.g. a read queued across the reconnect) arrives BEFORE any new office
    // state: the client cannot vouch, so it is dropped — it must never surface later.
    socket!.send(JSON.stringify(text));
    await settle();
    answerReads(evidence, received);
    send(evidence.states.live); // current again, at the SAME cursor
    await p().waitForSelector('[data-testid="di-evidence-stale"]', {
      state: 'detached',
      timeout: 10_000,
    });
    await settle();
    assert.equal(
      await textCount(),
      0,
      'the cleared answer is not resurrected by a new office state',
    );
    await readRow(text.artifactId); // only a NEW read shows content again
    await p().waitForSelector('[data-testid="di-evidence-text"]', { timeout: 10_000 });
    assert.equal(await shownText(), text.content!.text);
    assert.equal(received.length, 2);
  },
  STEPS_MS,
);

/** Round 15 F2, expiry: the same, when the socket stays up but the state passes `validUntil`. */
test(
  'item 2 (round 15 F2): expiry hides and clears read content; a new state does not resurrect it',
  async () => {
    const evidence = evidenceFixture();
    const text = evidence.replies.admitted_text;
    const received: Array<Record<string, unknown>> = [];
    answerReads(evidence, received);
    const expiring = JSON.parse(JSON.stringify(evidence.states.live)) as DiOfficeState;
    expiring.times.validUntil = Math.floor(Date.now() / 1000) + 3;
    send(expiring);
    await openPanel();
    await openEvidence();
    await readRow(text.artifactId);
    await p().waitForSelector('[data-testid="di-evidence-text"]', { timeout: 10_000 });
    assert.equal(await shownText(), text.content!.text);
    await textGone(15_000);
    const banner = await staleBanner();
    assert.match(banner, /as last received/);
    assert.match(banner, /client_expired/);
    assert.match(await readsLine(), /closed/);
    send(evidence.states.live);
    await p().waitForSelector('[data-testid="di-evidence-stale"]', {
      state: 'detached',
      timeout: 10_000,
    });
    await settle();
    assert.equal(await textCount(), 0);
    await readRow(text.artifactId);
    await p().waitForSelector('[data-testid="di-evidence-text"]', { timeout: 10_000 });
    assert.equal(received.length, 2);
  },
  STEPS_MS,
);

/** The HISTORICAL evidence fixture: a COMPLETED Mission, one real DiOffice (test data only). */
const historicalFixture = (): EvidenceFixture & {
  states: Record<'live' | 'moved' | 'stale', DiOfficeState>;
} =>
  JSON.parse(
    fs.readFileSync(
      path.join(root, 'test/fixtures/di-office-state-evidence-historical-v2.FIXTURE.json'),
      'utf-8',
    ),
  ) as EvidenceFixture & { states: Record<'live' | 'moved' | 'stale', DiOfficeState> };
const historicalLibrary = () =>
  p().$$eval('[data-testid="di-evidence-execution"]', (els) =>
    els.map((e) => ({
      basis: e.getAttribute('data-execution-basis'),
      text: (e as HTMLElement).innerText,
    })),
  );

/**
 * Item 2 (completed-Mission artifacts) through the ACTUAL App path: the office state ONE real
 * DiOffice projected from the historical backend export (a COMPLETED Mission, observed through the
 * task-native producer), over the real socket. The recorded execution and the listing's freshness
 * show apart; a keyboard read leaves as a reference and renders inert; the identity moving, and a
 * disconnect, hide the content. TEST DATA: no real execution, verification or authorization.
 */
test(
  'item 2 (completed Mission): a keyboard read under the historical execution; moved and disconnected listings hide it',
  async () => {
    const h = historicalFixture();
    const text = h.replies.admitted_text;
    const html = h.replies.admitted_html;
    const received: Array<Record<string, unknown>> = [];
    answerReads(h, received);
    send(h.states.live);
    await openPanel();
    await openEvidence({ state: h.states.live, artifactId: text.artifactId });
    await p().waitForSelector(rowOf(text.artifactId), { timeout: 30_000 });
    const execution = (await historicalLibrary()).find(
      (e) => e.basis === 'historical_completed_execution',
    );
    assert.ok(execution, 'the completed Mission lists its historical recorded execution');
    assert.match(execution!.text, /HISTORICAL recorded execution/);
    assert.match(execution!.text, /No live team/);
    assert.match(execution!.text, /nothing here speaks for the candidate as a whole/);
    const freshness = await p().$$eval('[data-testid="di-evidence-freshness"]', (els) =>
      els.map((e) => (e as HTMLElement).innerText),
    );
    assert.ok(freshness.some((t) => /listing freshness: current/.test(t)));
    await readRow(text.artifactId);
    await p().waitForSelector('[data-testid="di-evidence-text"]', { timeout: 10_000 });
    assert.equal(await shownText(), text.content!.text);
    const meta = await p().$eval(
      '[data-testid="di-evidence-meta"]',
      (e) => (e as HTMLElement).innerText,
    );
    assert.match(meta, /matches the\s+record/);
    assert.match(meta, /historical recorded execution \(not a live team\)/);
    assert.deepEqual(Object.keys(received[0]).sort(), [
      'artifactId',
      'cursor',
      'missionRef',
      'type',
    ]);
    // HTML of the same completed Mission renders as text: nothing parsed out of it.
    await settle();
    await readRow(html.artifactId);
    await p().waitForFunction(
      (want) => document.querySelector('[data-testid="di-evidence-text"]')?.textContent === want,
      html.content!.text,
      { timeout: 10_000 },
    );
    const elements = await p().$eval(
      '[data-testid="di-evidence-text"]',
      (e) => e.querySelectorAll('*').length,
    );
    assert.equal(elements, 0, 'no element is parsed out of artifact text');
    // The identity MOVES (same journal, another recorded execution): the read content is hidden.
    send(h.states.moved);
    await textGone();
    const hidden = await p().$eval(
      '[data-testid="di-evidence-hidden"]',
      (e) => (e as HTMLElement).innerText,
    );
    assert.match(hidden, /moved after this was read/);
    // Back to the listed identity; read again; then the socket drops: hidden and cleared.
    send(h.states.live);
    await settle();
    await readRow(text.artifactId);
    await p().waitForSelector('[data-testid="di-evidence-text"]', { timeout: 10_000 });
    expectConnection(true);
    socket!.close();
    await textGone();
    assert.match(await staleBanner(), /as last received/);
    await connected;
    answerReads(h, received);
    send(h.states.live);
    await p().waitForSelector('[data-testid="di-evidence-stale"]', {
      state: 'detached',
      timeout: 10_000,
    });
    await settle();
    assert.equal(await textCount(), 0, 'a reconnect resurrects nothing');
  },
  STEPS_MS,
);

/** Item 2 (completed Mission): an expired listing is STALE and closes reads; nothing is shown. */
test(
  'item 2 (completed Mission): an expired historical listing is stale, reads closed, content hidden',
  async () => {
    const h = historicalFixture();
    const text = h.replies.admitted_text;
    answerReads(h, []);
    send(h.states.live);
    await openPanel();
    await openEvidence({ state: h.states.live, artifactId: text.artifactId });
    await readRow(text.artifactId);
    await p().waitForSelector('[data-testid="di-evidence-text"]', { timeout: 10_000 });
    send(h.states.stale);
    await textGone();
    assert.match(await staleBanner(), /as last received/);
    const reads = await p().$$eval('[data-testid="di-evidence-reads"]', (els) =>
      els.map((e) => (e as HTMLElement).innerText),
    );
    assert.ok(reads.some((t) => /closed \(office_not_live\)/.test(t)));
  },
  STEPS_MS,
);
