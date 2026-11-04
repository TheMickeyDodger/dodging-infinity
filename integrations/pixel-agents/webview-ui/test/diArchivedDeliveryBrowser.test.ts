/**
 * Deliverable 2, round 10 (lead ruling: archived is read-only, NOT information-erasing), in the REAL App in a real browser, on the REAL producer's COMPLETE
 * Herdr task whose delivery the catalog links by its own task and worktree (di-office-state-archived-delivery-v2: tests/test_mission_retention_delivery_answer.py,
 * projected by server/__tests__/diArchivedDeliveryWire.test.ts). It is archived by Deliverable 1's rule.
 *
 * READABLE: its delivery status is read through the existing route (the one `status` request) and its facts are shown. NEVER INVOKED: the confirmation box and
 * the step button are drawn disabled, and every way to reach them (a forced pointer click, a script click, a dispatched click, the keyboard) is tried; the wire
 * must carry NO `advanceOnce` and the box must stay unticked. The harness is shown to BITE: the same task with its record open (a labelled synthetic variation)
 * does send `advanceOnce` on confirm and ask, which also pins that non-archived delivery is unaffected.
 *
 * The office is this test's own socket. The adapter's status answer is SYNTHETIC (the shape diMissionDelivery.test.ts uses); nothing is live, nothing is
 * answered to an advance.
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
import { DI_DELIVERY_ARCHIVED_ACT, DI_DELIVERY_ARCHIVED_LINKED } from '../src/components/diScanModel.ts';

const STEPS_MS = 120_000;
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const VERSION = (JSON.parse(fs.readFileSync(path.join(root, '..', 'package.json'), 'utf-8')) as { version: string }).version;
const archived = (
  JSON.parse(fs.readFileSync(path.join(root, 'test/fixtures/di-office-state-archived-delivery-v2.FIXTURE.json'), 'utf-8')) as { state: DiOfficeState }
).state;
const DELIVERY = 'prd-0a0a0a0a0a0a0a0a0a0a0a0a';
const LINKED = archived.missions.missions.find((m) => m.v2.deliveryLink?.state === 'linked')!.missionRef;
/** SYNTHETIC variation: the same state with the linked task's record open (`standing: 'active'`), so it is not archived. */
const open = (() => {
  const state = structuredClone(archived);
  const mission = state.missions.missions.find((m) => m.missionRef === LINKED)!;
  mission.standing = 'active';
  mission.notLiveReason = null;
  return state;
})();

type Any = Record<string, unknown>;
/** SYNTHETIC: the adapter's status answer for this delivery, COMMIT eligible (the shape diMissionDelivery.test.ts uses). */
const statusAnswer = (): Any => ({
  ok: true,
  status: {
    delivery: {
      delivery_id: DELIVERY,
      revision: 4,
      phase: 'base_refreshed',
      authorization: { valid: true, problem: null, human_identity: 'travis', allowed_actions: ['commit', 'push', 'pr_create'], mode: 'pr_create' },
      base_refresh: { state: 'succeeded', receipt_id: 'rcpt-base-1', receipt_state: 'succeeded', attempts: 1, observed: null },
      commit: { state: 'pending', receipt_id: null, receipt_state: null, attempts: 0, observed: null, commit_oid: null },
      push: { state: 'pending', receipt_id: null, receipt_state: null, attempts: 0, observed: null, remote_oid: null },
      pr_url: null,
      blocker: null,
      next_action: { action: 'run_step', step: 'COMMIT' },
    },
    office: {
      eligible: { step: 'COMMIT', binding: { delivery_id: DELIVERY, revision: 4, candidate_identity_digest_sha256: 'c'.repeat(64), step: 'COMMIT' } },
      not_eligible: null,
      action_steps: ['COMMIT', 'PUSH', 'PR_CREATE'],
      local_confirmation_note:
        'This confirmation is a local check. The delivery was authorized once, by the human ceremony, for these steps. This confirmation is not recorded in the delivery record.',
      performs_steps: true,
      performs_steps_note: 'This office performs ONE confirmed step at a time (COMMIT, PUSH or PR_CREATE; never the base refresh, never every step).',
    },
  },
});

let server: ViteDevServer | null = null;
let wss: WebSocketServer | null = null;
let browser: Browser | null = null;
let page: Page | null = null;
let url = '';
let unavailable: string | null = null;
let socket: ServerSocket | null = null;
let resolveConnected: (ws: ServerSocket) => void = () => undefined;
let deliveryRequests: Any[] = [];

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
        const msg = JSON.parse(String(data)) as Any;
        if (msg.type === 'webviewReady') {
          socket = ws;
          resolveConnected(ws);
        } else if (msg.type === 'diDeliveryRequest') {
          deliveryRequests.push(msg);
          // only a status read is answered; an advance is recorded and never answered
          if (msg.op === 'status')
            ws.send(JSON.stringify({ type: 'diDeliveryReply', seq: msg.seq, op: 'status', state: 'ok', problem: null, reason: null, answer: statusAnswer() }));
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
    console.warn(`SKIPPED (archived delivery, Mission details): ${unavailable}`);
    ctx.skip();
    return;
  }
  await page?.close();
  page = null;
  deliveryRequests = [];
}, STEPS_MS);

const p = () => page!;
const sel = (id: string) => `[data-testid="${id}"]`;
const ops = () => deliveryRequests.map((m) => String(m.op));

/** The task's own details, reached the way a person does (Missions, the status filter, the row), then its delivery status READ through the existing route. */
async function bootAndRead(state: DiOfficeState, filter: 'archived' | null): Promise<void> {
  const connected = new Promise<ServerSocket>((resolve) => (resolveConnected = resolve));
  page = await browser!.newPage({ viewport: { width: 1440, height: 900 } });
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await page.goto(url, { waitUntil: 'domcontentloaded' });
  await connected;
  socket!.send(JSON.stringify({ type: 'providerCapabilities', readingTools: ['Read', 'Grep', 'Glob', 'WebFetch', 'WebSearch'], subagentToolNames: ['Task', 'Agent'] }));
  socket!.send(JSON.stringify({ type: 'settingsLoaded', soundEnabled: false, lastSeenVersion: VERSION, extensionVersion: VERSION }));
  socket!.send(JSON.stringify(state));
  await p().waitForSelector(sel('di-rail-missions'));
  await p().click(sel('di-rail-missions'), { timeout: 10_000 });
  if (filter !== null) await p().click(sel(`di-missions-status-${filter}`), { timeout: 10_000 });
  await p().click(`[data-testid="di-missions-row"][data-mission-ref="${LINKED}"] [data-testid="di-missions-open"]`, { timeout: 10_000 });
  await p().waitForSelector('[data-testid="di-scan-stage"][data-view="mission"]');
  await p().waitForFunction(() => {
    const b = document.querySelector('[data-testid="di-scan-delivery-read"]');
    return b instanceof HTMLButtonElement && !b.disabled;
  });
  await p().click(sel('di-scan-delivery-read'));
  await p().waitForSelector(sel('di-dl-view'));
}

test(
  'ARCHIVED, the real COMPLETE linked task: its status is READ and its facts shown; the confirmation and the step are disabled and NEVER invoked by any path',
  async () => {
    await bootAndRead(archived, 'archived');
    assert.deepEqual(ops(), ['status'], 'the one status read went out, through the existing route');
    assert.equal(deliveryRequests[0]!.deliveryId, DELIVERY);
    assert.match((await p().locator(sel('di-scan-delivery-archived')).textContent()) ?? '', new RegExp(DI_DELIVERY_ARCHIVED_LINKED.slice(0, 40)));
    const view = (await p().locator(sel('di-dl-view')).textContent()) ?? '';
    assert.match(view, /Eligible now, as the record states it: COMMIT\./, 'the record’s eligible step is shown as a fact');
    assert.match(view, new RegExp(DELIVERY), 'the binding names this delivery');

    const box = p().locator(sel('di-dl-confirm'));
    const ask = p().locator(sel('di-dl-advance'));
    assert.equal(await box.isDisabled(), true, 'the confirmation box is disabled');
    assert.equal(await ask.isDisabled(), true, 'the step button is disabled');
    assert.equal(((await p().locator(sel('di-scan-delivery-archived-act')).textContent()) ?? '').trim(), DI_DELIVERY_ARCHIVED_ACT, 'with the archived reason beside them');

    // every way to reach them: a forced pointer click, a script click, a dispatched click, and the keyboard
    await box.click({ force: true });
    await ask.click({ force: true });
    await box.evaluate((el) => (el as HTMLInputElement).click());
    await ask.evaluate((el) => (el as HTMLButtonElement).click());
    await ask.dispatchEvent('click');
    await box.dispatchEvent('click');
    await ask.evaluate((el) => (el as HTMLButtonElement).focus());
    await p().keyboard.press('Enter');
    await p().keyboard.press('Space');
    await p().waitForTimeout(300);

    assert.equal(await box.isChecked(), false, 'the confirmation box was never ticked');
    assert.deepEqual(ops(), ['status'], `no advance was ever asked: ${JSON.stringify(ops())}`);
    assert.equal(await p().locator('[data-testid="di-scan-step"][data-gate="ready"]').count(), 0, 'no step reads Ready');
  },
  STEPS_MS,
);

test(
  'THE HARNESS BITES, and non-archived delivery is unaffected: the same task with its record OPEN confirms and asks, and the one advanceOnce goes out',
  async () => {
    await bootAndRead(open, null);
    assert.equal(await p().locator(sel('di-scan-delivery-archived')).count(), 0, 'nothing says archived');
    const box = p().locator(sel('di-dl-confirm'));
    assert.equal(await box.isDisabled(), false, 'the active confirmation box is enabled');
    await box.check();
    await p().click(sel('di-dl-advance'));
    await p().waitForFunction(() => true);
    for (let i = 0; i < 20 && !ops().includes('advanceOnce'); i += 1) await p().waitForTimeout(100);
    assert.deepEqual(ops(), ['status', 'advanceOnce'], 'the active path asks the adapter for the one step');
    assert.equal(deliveryRequests[1]!.deliveryId, DELIVERY);
  },
  STEPS_MS,
);
