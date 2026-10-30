/**
 * ROUND 30, in the real App: a FINISHED Mission's overview is read and drawn, then the reader selects a file and THAT request is refused with a finished reason. The notice is read WITH the tree, the branch and the change
 * counts still on screen (the retained overview), which is the condition under which the earlier wording ("This review ... nothing from one is shown") was false.
 *
 * The office here is this test's own socket: it answers the Mission-level overview with the recorded overview RE-ADDRESSED to the reference asked by, and refuses every file read with the finished reason under test (a
 * labelled, synthetic variation: it shows the page's behaviour, never that a backend refused a real read). Nothing is live.
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

import type { DiCodeReview, DiOfficeState } from '../../core/src/messages.ts';
import { problemWords } from '../src/components/diCodeReviewModel.ts';
import { OVERBROAD_REFERENT } from './diFinishedWordingClaims.ts';

const STEPS_MS = 120_000;
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const readJson = <T,>(file: string): T => JSON.parse(fs.readFileSync(path.join(root, 'test/fixtures', file), 'utf-8')) as T;
const active = readJson<{ replies: { overview: DiCodeReview; files: Record<string, DiCodeReview> } }>('di-office-state-code-review-v2.FIXTURE.json');
const finishedDoc = readJson<{ states: Record<'live', DiOfficeState> }>('di-office-state-evidence-historical-v2.FIXTURE.json');
const finishedMission = finishedDoc.states.live.missions.missions.find((m) => m.sourceKind === 'mission_core')!;
const FINISHED = { missionRef: finishedMission.missionRef, taskId: finishedMission.codeReview!.taskId!, listing: finishedMission.codeReview!.listing! };
const VERSION = (JSON.parse(fs.readFileSync(path.join(root, '..', 'package.json'), 'utf-8')) as { version: string }).version;
const overview = active.replies.overview.overview!;

const FINISHED_CODES = [
  'finished_execution_not_established',
  'finished_ownership_unverifiable',
  'finished_ownership_contested',
  'finished_workspace_not_corroborated',
] as const;

let server: ViteDevServer | null = null;
let wss: WebSocketServer | null = null;
let browser: Browser | null = null;
let page: Page | null = null;
let url = '';
let unavailable: string | null = null;
let socket: ServerSocket | null = null;
let resolveConnected: (ws: ServerSocket) => void = () => undefined;
let requests: Array<Record<string, unknown>> = [];
/** The finished reason the next file read is refused with. */
let refuseFilesWith = 'finished_workspace_not_corroborated';

const readdressed = (stored: DiCodeReview, request: Record<string, unknown>): DiCodeReview => ({
  ...structuredClone(stored),
  missionRef: String(request.missionRef),
  memberRef: '',
  taskId: String(request.taskId),
  listing: String(request.listing),
});

function replyFor(request: Record<string, unknown>): DiCodeReview | null {
  if (request.fileId === undefined) return readdressed(active.replies.overview, request);
  const stored = Object.values(active.replies.files).find((r) => r.fileId === request.fileId);
  if (stored === undefined) return null;
  return {
    ...readdressed(stored, request),
    state: 'refused',
    problem: refuseFilesWith,
    detail: null,
    target: null,
    comparison: null,
    overview: null,
    file: null,
    observedAt: null,
  };
}

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
        } else if (msg.type === 'diCodeReviewMissionRead') {
          requests.push(msg);
          const found = replyFor(msg);
          if (found === null) return;
          const reply = typeof msg.requestId === 'string' ? { ...found, requestId: msg.requestId } : found;
          ws.send(JSON.stringify(reply));
        }
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
  await browser?.close();
  wss?.close();
  await server?.close();
}, STEPS_MS);

beforeEach(async (ctx) => {
  if (unavailable) {
    console.warn(`SKIPPED (finished refusal, composite view): ${unavailable}`);
    ctx.skip();
    return;
  }
  await page?.close();
  page = null;
  requests = [];
}, STEPS_MS);

const p = () => page!;

async function bootAndOpenCodeDiff(): Promise<void> {
  const connected = new Promise<ServerSocket>((resolve) => (resolveConnected = resolve));
  page = await browser!.newPage({ viewport: { width: 1440, height: 900 } });
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await page.goto(url, { waitUntil: 'domcontentloaded' });
  await connected;
  socket!.send(JSON.stringify({ type: 'providerCapabilities', readingTools: ['Read', 'Grep', 'Glob', 'WebFetch', 'WebSearch'], subagentToolNames: ['Task', 'Agent'] }));
  socket!.send(JSON.stringify({ type: 'settingsLoaded', soundEnabled: false, lastSeenVersion: VERSION, extensionVersion: VERSION }));
  socket!.send(JSON.stringify(finishedDoc.states.live));
  // a finished Mission alone has no orchestrator on the rail: it is reached the way a person does, through the Missions page
  await p().waitForSelector('[data-testid="di-rail-missions"]');
  await p().click('[data-testid="di-rail-missions"]', { timeout: 10_000 });
  await p().click(`[data-testid="di-missions-row"][data-mission-ref="${FINISHED.missionRef}"] [data-testid="di-missions-open"]`, { timeout: 10_000 });
  await p().waitForSelector('[data-testid="di-scan-stage"][data-view="mission"]');
  await p().click('[data-testid="di-scan-code-diff"]');
  await p().waitForSelector('[data-testid="di-scan-stage"][data-view="diff"]');
  await p().waitForSelector('[data-testid="di-scan-diff"][data-source="read"]', { timeout: 15_000 });
}

/** What the successful OVERVIEW supplied to the status bar: the branch, the base it was compared with, how many files changed, and the added and removed counts. (The bar also carries facts of the SELECTED file, which move with the selection.) */
async function overviewFacts() {
  const text = async (selector: string) => ((await p().locator(`[data-testid="di-scan-statusbar"] ${selector}`).first().textContent()) ?? '').trim();
  const bar = (await p().locator('[data-testid="di-scan-statusbar"]').textContent()) ?? '';
  return {
    branch: await text('.di-scan-status-branch'),
    base: await text('.di-scan-status-base'),
    filesChanged: /(\d+ files? changed|no changes found)[^+−]*/.exec(bar)?.[0] ?? '',
    added: await text('.di-scan-add'),
    removed: await text('.di-scan-del'),
  };
}

for (const code of FINISHED_CODES) {
  test(
    `A FILE READ REFUSED (${code}) WITH THE OVERVIEW STILL ON SCREEN (browser): the notice is about the refused request, and the tree, the branch and the change counts it sits beside are still drawn`,
    async () => {
      refuseFilesWith = code;
      await bootAndOpenCodeDiff();

      // the successful overview is on screen before the refusal: the tree rows, the branch and the change count
      const treeBefore = await p().locator('[data-testid="di-scan-tree-file"]').count();
      assert.equal(treeBefore, overview.files.length, 'every listed file is drawn');
      const barBefore = await overviewFacts();
      assert.ok(barBefore.branch.length > 0, 'the branch is drawn');
      assert.match(barBefore.filesChanged, /\d+ files? changed/, `the change count is drawn: ${barBefore.filesChanged}`);

      // the reader selects a file, and THAT request is refused
      const app = overview.files.find((f) => f.path === 'src/app.py')!;
      await p().click(`[data-testid="di-scan-tree-file"][data-file-id="${app.fileId}"]`);
      await p().waitForSelector('[data-testid="di-scan-file-notice"][data-state="refused"]', { timeout: 8000 });
      // (the page also reads the first openable file on its own once the overview arrives, so more than one file read may have been asked: each is refused by this office)
      assert.ok(requests.some((r) => r.fileId === app.fileId), 'the selected file WAS asked for');
      assert.equal(((await p().locator('[data-testid="di-scan-editor-path"]').textContent()) ?? '').trim(), 'src/app.py', 'the notice is the selected file\'s');

      // read the notice WITH the rest of the screen present
      const notice = (await p().locator('[data-testid="di-scan-file-notice"]').textContent()) ?? '';
      const said = problemWords(code, null);
      assert.ok(notice.includes(said.body), `the notice is the finished body: ${notice}`);
      assert.ok(notice.includes('The refused request could not establish a link between this Mission and a working tree, and returned nothing from one.'), `it states the outcome about the refused request: ${notice}`);
      for (const claim of OVERBROAD_REFERENT) assert.doesNotMatch(notice, claim, `the notice makes no claim about the review or the view (${claim})`);

      // and that screen is intact: the retained overview was not cleared to make the sentence true
      assert.equal(await p().locator('[data-testid="di-scan-tree-file"]').count(), treeBefore, 'the tree is still drawn');
      assert.deepEqual(await overviewFacts(), barBefore, 'the branch, the base and the change counts the overview supplied are unchanged');
      assert.equal(await p().locator('[role="dialog"]').count(), 0, 'no modal');
    },
    STEPS_MS,
  );
}
