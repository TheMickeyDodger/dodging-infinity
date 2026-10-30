/**
 * R10, the established part, through the actual App in a real browser: "Evidence and review" on Mission details opens the EXISTING evidence library for that Mission on its own nested screen, listing
 * exactly the artifacts the office lists for it, and opening it sends nothing (a content read happens only when a person chooses an artifact, the library's own rule).
 *
 * The office state is the evidence v2 fixture's `live` state (the backend's artifact-content-v1 as one real DiOffice projects it). Nothing is live. This is NOT evidence about logs or checks as such: no
 * log or check kind exists, and an artifact whose contract key reads like one is listed under that key, never labelled (inventory row R10).
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

const STEPS_MS = 120_000;
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
/** The evidence library of artifact-content-v1 as one real DiOffice projects it (its `live` state), and the content answers that office relayed (`replies`). */
const evidence = JSON.parse(fs.readFileSync(path.join(root, 'test/fixtures/di-office-state-evidence-v2.FIXTURE.json'), 'utf-8')) as {
  states: { live: DiOfficeState };
  replies: Record<string, { missionRef: string; artifactId: string; cursor: string; artifact: { key: string } | null; content: { text: string } | null }>;
};
const office = evidence.states.live;
/** The relayed answer for the VERIFICATION artifact its contract keyed `test_log` (a JUnit file): the content R10's corrected position says is reachable. */
const testLog = evidence.replies['admitted_verification_log']!;
/** The Mission that lists it. */
const listed = office.missions.missions.find((m) => m.missionRef === testLog.missionRef);
const VERSION = (JSON.parse(fs.readFileSync(path.join(root, '..', 'package.json'), 'utf-8')) as { version: string }).version;

let server: ViteDevServer | null = null;
let wss: WebSocketServer | null = null;
let browser: Browser | null = null;
let page: Page | null = null;
let url = '';
let unavailable: string | null = null;
let socket: ServerSocket | null = null;
let resolveConnected: (ws: ServerSocket) => void = () => undefined;
/** Every message the page sent after the handshake, exactly as received. */
let sentByPage: Array<Record<string, unknown>> = [];

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
        sentByPage.push(msg);
        // a content read is answered with the answer the office RELAYED for that artifact and cursor (recorded in the fixture), and nothing else
        if (msg.type === 'diEvidenceRead') {
          const reply = Object.values(evidence.replies).find((r) => r.artifactId === msg.artifactId && r.cursor === msg.cursor);
          if (reply) ws.send(JSON.stringify(reply));
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
  await page?.close();
  await browser?.close();
  wss?.close();
  await server?.close();
}, STEPS_MS);

beforeEach(async (ctx) => {
  if (unavailable) {
    console.warn(`SKIPPED (evidence navigation): ${unavailable}`);
    ctx.skip();
    return;
  }
  await page?.close();
  page = null;
  sentByPage = [];
}, STEPS_MS);

const p = () => page!;
const sel = (id: string) => `[data-testid="${id}"]`;

test(
  'EVIDENCE AND REVIEW (browser): opens the existing library for THAT Mission on its own screen, listing exactly its listed artifacts; opening it reads nothing; choosing the artifact keyed test_log renders its content, under its key, never called a log',
  async () => {
    assert.ok(listed, 'the fixture lists the Mission that holds the test_log artifact');
    assert.equal(testLog.artifact?.key, 'test_log');
    assert.ok((testLog.content?.text ?? '') !== '', 'the relayed answer carries content (a non-empty guard)');
    const connected = new Promise<ServerSocket>((resolve) => (resolveConnected = resolve));
    page = await browser!.newPage({ viewport: { width: 1440, height: 900 } });
    await page.emulateMedia({ reducedMotion: 'reduce' });
    await page.goto(url, { waitUntil: 'domcontentloaded' });
    await connected;
    socket!.send(JSON.stringify({ type: 'providerCapabilities', readingTools: ['Read', 'Grep', 'Glob', 'WebFetch', 'WebSearch'], subagentToolNames: ['Task', 'Agent'] }));
    socket!.send(JSON.stringify({ type: 'settingsLoaded', soundEnabled: false, lastSeenVersion: VERSION, extensionVersion: VERSION }));
    socket!.send(JSON.stringify(office));
    const row = `${sel('di-scan-row')}[data-mission-ref="${listed.missionRef}"]`;
    await p().waitForSelector(row);
    await p().click(row);
    await p().click(sel('di-scan-open-mission'));
    const open = p().locator(sel('di-scan-evidence'));
    await open.waitFor();
    assert.equal(((await open.textContent()) ?? '').trim(), 'Evidence and review');

    sentByPage = [];
    await open.click();
    await p().waitForSelector(sel('di-evidence-library'));
    assert.equal(await p().locator('[data-testid="di-scan-stage"]').getAttribute('data-view'), 'evidence');
    assert.equal(
      await p().locator(sel('di-evidence-artifact-row')).count(),
      listed.evidence!.artifacts.items.length,
      'the library lists exactly the artifacts the office lists for this Mission',
    );
    await p().waitForTimeout(400);
    assert.deepEqual(
      sentByPage.filter((m) => m.type === 'diEvidenceRead'),
      [],
      'opening the screen read no content',
    );

    // REACHABILITY, watched: the artifact its contract keyed test_log is listed, and choosing it renders the relayed content
    const testLogRow = p().locator(`[data-testid="di-evidence-artifact-row"][data-artifact-id="${testLog.artifactId}"]`);
    assert.equal(await testLogRow.count(), 1, 'the test_log artifact is listed');
    await testLogRow.click();
    await p().waitForSelector(sel('di-evidence-text'));
    assert.equal(await p().locator(sel('di-evidence-text')).textContent(), testLog.content!.text);
    const reads = sentByPage.filter((m) => m.type === 'diEvidenceRead');
    assert.equal(reads.length, 1, 'exactly one content read, for the chosen artifact');
    assert.equal(reads[0]!.artifactId, testLog.artifactId);
    // the office never calls it a log: no control or heading on this screen names a log or checks
    const screen = (await p().locator('[data-testid="di-scan-stage"]').innerText()).replace(/test_log/g, '');
    assert.doesNotMatch(screen, /\blogs?\b|\bchecks?\b/i, 'nothing on the screen calls the artifact a log or checks, beyond its own key');
  },
  STEPS_MS,
);
