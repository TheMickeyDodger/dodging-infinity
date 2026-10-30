/**
 * M8 COMPOSED (the missing composition evidence): Mission details' ONE delivery session, its hook and the catalog wiring, in the
 * actual App in a real browser. Every piece here is covered on its own elsewhere; this file is where they are put together.
 *
 * WHAT IS REAL: the server's catalog projection and the office-state message it builds (parseEnvelope, buildOfficeView and
 * buildDiOfficeStateMessage, imported from server/src/di), the App (test/harness/di-app-scan.html -> src/main.tsx), its
 * useDiDelivery('linked') session, the scan model's row, Mission details' Delivery section, and the WebSocket protocol.
 *
 * WHAT IS FIXTURE, and only this:
 *  - the catalog answer: the hash-pinned contract-v2 emission server/__tests__/fixtures/di-backend/v2/catalog-answer-v2.fc409bbc
 *    .FIXTURE.json (SYNTHETIC ids), with `delivery_link` declared in the producer's emission shape exactly as
 *    server/__tests__/diMissionDeliveryLink.test.ts declares it: the workflow Mission linked, every other model not recorded;
 *  - the page's clock, FIXED inside that answer's own validity window, so the window can vouch for the office it was given;
 *  - the delivery replies, which THIS file's socket sends. No delivery adapter, no transport and no delivery exist here.
 *
 * WHAT IT ASSERTS, each with its authority:
 *  - the Delivery section comes after the Swarm section in Mission details (design pack: GAP-RESOLUTIONS 2, and CONTROL-MAP's
 *    "Delivery (under Swarm)"). The Swarm section is found by its box's CLASS, `.di-scan-swarm-box`, the one element drawn for
 *    every Mission (the `di-scan-swarm` test id is on the member list, drawn only when a swarm is recorded). LIMIT: a class is a
 *    styling hook, a weaker contract than a test id, so a rename for styling would break this placement check;
 *  - it names the delivery the REAL projection linked to this Mission, and follows that link when the projection changes;
 *  - one read sends EXACTLY ONE status request, carrying the linked id and this window's own seq (the contract's
 *    DiDeliveryRequest); a reply is applied only when its seq and op are the request's (DiDeliveryReply: "correlated by `seq`
 *    and `op`");
 *  - the adapter's answer is shown as INERT TEXT (DiDeliveryReply: "a client shows it as inert text and never renders ...");
 *  - no advanceOnce is sent, since nothing here confirms, and the App has no second delivery session: there is no typed-id field
 *    anywhere (owner decision Q2, 2026-10-29: the standalone delivery panel is kept for test harnesses only).
 *
 * NOT ASSERTED, because the design pack does not say it:
 *  - the link note's words beyond its id;
 *  - how a status is worded;
 *  - whether Delivery must also follow the Code Diff / Herdr / Comments buttons.
 *
 * ABLE TO FAIL: every assertion helper is run once against a wrong target and must throw. The second test also perturbs the
 * fixture's link (a second delivery id, the same Mission) and shows the first id's assertions then fail.
 *
 * Browser: the installed Chrome (playwright-core, channel `chrome`), else Playwright's Chromium; with neither, every test is
 * skipped with an explicit message.
 */
import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
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
import { parseEnvelope } from '../../server/src/di/catalogProjection.ts';
import { buildDiOfficeStateMessage } from '../../server/src/di/officeStateMessage.ts';
import { buildOfficeView } from '../../server/src/di/officeView.ts';
import type { ServeTimeVerdict } from '../../server/src/di/serveTimeVerdict.ts';

const STEPS_MS = 120_000;
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const VERSION = (
  JSON.parse(fs.readFileSync(path.join(root, '..', 'package.json'), 'utf-8')) as { version: string }
).version;

const V2_PATH = path.join(
  root,
  '..',
  'server/__tests__/fixtures/di-backend/v2/catalog-answer-v2.fc409bbc.FIXTURE.json',
);
const V2_SHA256 = 'fc409bbc41a6ee3a17253acebe85092a4d118511954fadf2c457ecc11b891556';
/** The answer's own generated_at. Its valid_until is 1_000_060, so the page's clock is fixed between the two. */
const V2_NOW = 1_000_000;
const PAGE_NOW_MS = (V2_NOW + 30) * 1000;
/** The fixture's workflow Mission, the one Mission whose model a delivery record names. */
const WF = 'synthetic_workflow_store:wf-0005';
const WORKFLOW_MODEL = 'workflow_authority.workflow_phase/2';
const FRESH: ServeTimeVerdict = { health: { known: true, value: 'fresh' }, problem: null };
const ID_A = `prd-${'0a'.repeat(12)}`;
const ID_B = `prd-${'0b'.repeat(12)}`;
/** The one field of the adapter's answer this test reads back: text a renderer could mistake for markup. */
const MARKUP = '<b data-testid="di-injected">phase markup</b>';

type Raw = Record<string, unknown>;

/**
 * The EXISTING screenshot seam (diMissionRequest.test.ts's): set DI_REVIEW_SHOTS=<dir> to also save the linked Delivery view.
 * Off, nothing is saved and the test is unchanged. The shot is labelled IN THE IMAGE as this test's harness page: no packaged
 * fixture carries a delivery link (all 14), so the integrated preview cannot show the linked state, and this page is where it
 * exists. FIXTURE data only; not the integrated preview; not live.
 */
const SHOTS = process.env['DI_REVIEW_SHOTS'] ?? null;
const HARNESS_LABEL =
  'TEST HARNESS PAGE (diMissionDeliveryComposition.test.ts): the real App over the real catalog projection of a FIXTURE answer. NOT the integrated preview. NOT live.';

/** The office state the server sends for the fixture answer, its workflow Mission linked to `deliveryId`: the REAL projection. */
function officeLinkedTo(deliveryId: string): DiOfficeState {
  const bytes = fs.readFileSync(V2_PATH);
  const digest = createHash('sha256').update(bytes).digest('hex');
  if (digest !== V2_SHA256) throw new Error(`v2 FIXTURE hash mismatch: ${digest}`);
  const raw = JSON.parse(bytes.toString('utf-8')) as Raw;
  for (const m of (raw.response as Raw).missions as Raw[]) {
    const model = (m.lifecycle as { model: string }).model;
    m.delivery_link =
      model === WORKFLOW_MODEL
        ? { state: 'linked', delivery_id: deliveryId, basis: 'record_mission_binding' }
        : { state: 'unlinked_model_not_recorded', delivery_id: null, basis: null };
  }
  const parsed = parseEnvelope(raw);
  if (!parsed.ok) throw new Error(`the fixture answer did not parse: ${parsed.verdict.detail}`);
  return buildDiOfficeStateMessage(buildOfficeView(parsed.envelope, FRESH, V2_NOW, 2), () => 41);
}

/** A status answer in the shape the page parses, its one varied field the phase. */
function okReply(seq: number, deliveryId: string, phase: string): string {
  const delivery = { delivery_id: deliveryId, revision: 1, phase };
  return JSON.stringify({
    type: 'diDeliveryReply',
    seq,
    op: 'status',
    state: 'ok',
    problem: null,
    reason: null,
    answer: { status: { delivery, office: {} } },
  });
}

let server: ViteDevServer | null = null;
let wss: WebSocketServer | null = null;
let browser: Browser | null = null;
let page: Page | null = null;
let url = '';
let unavailable: string | null = null;
let socket: ServerSocket | null = null;
let resolveConnected: (ws: ServerSocket) => void = () => undefined;
/** Every message the page sent after the handshake, exactly as received. Nothing is answered unless a test answers it. */
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
    console.warn(`SKIPPED (delivery composition): ${unavailable}`);
    ctx.skip();
    return;
  }
  await page?.close();
  page = null;
  sentByPage = [];
}, STEPS_MS);

const p = () => page!;
const sel = (id: string) => `[data-testid="${id}"]`;
const inDelivery = (id: string) => `${sel('di-scan-delivery')} ${sel(id)}`;
const deliveryRequests = () => sentByPage.filter((m) => m.type === 'diDeliveryRequest');

/** Waits, in this process, for a condition on what the page sent. */
async function until(what: string, ok: () => boolean, ms = 15_000): Promise<void> {
  const end = Date.now() + ms;
  while (!ok()) {
    if (Date.now() > end) throw new Error(`timed out waiting for ${what}`);
    await new Promise((resolve) => setTimeout(resolve, 50));
  }
}

async function boot(deliveryId: string): Promise<void> {
  const connected = new Promise<ServerSocket>((resolve) => (resolveConnected = resolve));
  page = await browser!.newPage({ viewport: { width: 1440, height: 900 } });
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await page.clock.setFixedTime(PAGE_NOW_MS);
  await page.goto(url, { waitUntil: 'domcontentloaded' });
  await connected;
  const capabilities = {
    type: 'providerCapabilities',
    readingTools: ['Read', 'Grep', 'Glob', 'WebFetch', 'WebSearch'],
    subagentToolNames: ['Task', 'Agent'],
  };
  const settings = {
    type: 'settingsLoaded',
    soundEnabled: false,
    lastSeenVersion: VERSION,
    extensionVersion: VERSION,
  };
  socket!.send(JSON.stringify(capabilities));
  socket!.send(JSON.stringify(settings));
  socket!.send(JSON.stringify(officeLinkedTo(deliveryId)));
  await p().waitForSelector(`${sel('di-scan-row')}[data-mission-ref="${WF}"]`);
}

async function openDetails(): Promise<void> {
  await p().click(`${sel('di-scan-row')}[data-mission-ref="${WF}"]`);
  await p().click(sel('di-scan-open-mission'));
  await p().waitForSelector(sel('di-scan-delivery'));
}

/** Whether the element `first` selects comes before the one `second` selects, inside the Delivery section's own parent. */
async function comesBefore(first: string, second: string): Promise<boolean> {
  return p().evaluate(
    ([a, b]) => {
      const column = document.querySelector('[data-testid="di-scan-delivery"]')?.parentElement;
      const x = column?.querySelector(a);
      const y = column?.querySelector(b);
      if (!x || !y) throw new Error(`not both drawn: ${a}, ${b}`);
      return (x.compareDocumentPosition(y) & Node.DOCUMENT_POSITION_FOLLOWING) !== 0;
    },
    [first, second] as const,
  );
}

async function linkNoteNames(deliveryId: string): Promise<void> {
  const text = await p().locator(inDelivery('di-scan-delivery-link')).innerText();
  assert.ok(text.includes(deliveryId), `the link note names ${deliveryId}: ${text}`);
}

async function viewShows(text: string): Promise<void> {
  const shown = await p().locator(inDelivery('di-dl-view')).innerText();
  assert.ok(shown.includes(text), `the status view shows ${text}: ${shown}`);
}

/** With the seam on: label the page as this harness, bring Delivery into view, save it, then remove the label. */
async function harnessShot(name: string): Promise<void> {
  if (SHOTS === null) return;
  fs.mkdirSync(SHOTS, { recursive: true });
  await p().evaluate((text) => {
    const el = document.createElement('div');
    el.setAttribute('data-testid', 'di-harness-shot-label');
    el.textContent = text;
    el.style.cssText =
      'position:fixed;left:0;right:0;bottom:0;z-index:2147483647;padding:6px 12px;font:600 13px sans-serif;' +
      'background:black;color:gold;border-top:2px solid gold;pointer-events:none';
    document.body.appendChild(el);
  }, HARNESS_LABEL);
  await p().locator(sel('di-scan-delivery')).scrollIntoViewIfNeeded();
  await p().screenshot({ path: path.join(SHOTS, `${name}.png`) });
  await p().evaluate(() =>
    document.querySelector('[data-testid="di-harness-shot-label"]')?.remove(),
  );
}

/** The one status request this read sent: exactly the contract's keys, for `deliveryId`, with a seq of the window's own. */
function theOneStatusRequestFor(deliveryId: string, before: number): number {
  const sent = deliveryRequests().slice(before);
  assert.equal(sent.length, 1, `exactly one delivery request: ${JSON.stringify(sent)}`);
  const [req] = sent;
  assert.deepEqual(Object.keys(req).sort(), ['deliveryId', 'op', 'seq', 'type']);
  assert.equal(req.op, 'status');
  assert.equal(req.deliveryId, deliveryId);
  assert.ok(
    Number.isInteger(req.seq) && (req.seq as number) > 0,
    `a seq of the window's own: ${req.seq}`,
  );
  return req.seq as number;
}

test(
  'M8 COMPOSED: Mission details reads the delivery the REAL projection linked, with ONE correlated status request, and shows the answer as inert text',
  async () => {
    await boot(ID_A);
    await openDetails();

    // placement (design pack): Delivery comes after the Swarm section. The control: the reverse order is false.
    assert.equal(await comesBefore('.di-scan-swarm-box', sel('di-scan-delivery')), true);
    assert.equal(await comesBefore(sel('di-scan-delivery'), '.di-scan-swarm-box'), false);

    // the catalog wiring: the note names the id the projection linked; the control: it does not name another
    await linkNoteNames(ID_A);
    await assert.rejects(linkNoteNames(ID_B));

    // one read: exactly one status request, for the linked id; the control: it is not a request for another id
    await p().click(inDelivery('di-scan-delivery-read'));
    await until('the status request', () => deliveryRequests().length >= 1);
    const seq = theOneStatusRequestFor(ID_A, 0);
    assert.throws(() => theOneStatusRequestFor(ID_B, 0));

    // correlation: a reply with another seq is not applied; the reply with this request's seq and op is
    socket!.send(okReply(seq + 1000, ID_A, 'NOT THIS REQUEST'));
    socket!.send(okReply(seq, ID_A, MARKUP));
    await p().waitForSelector(inDelivery('di-dl-view'));
    await viewShows(MARKUP);
    await harnessShot('harness-02-delivery-linked-1440x900');
    await assert.rejects(viewShows('NOT THIS REQUEST'));
    assert.equal(await p().getByText('NOT THIS REQUEST').count(), 0);

    // inert text: the answer's markup is shown as its characters and makes no element
    assert.equal(await p().locator(sel('di-injected')).count(), 0);
    assert.ok(
      (await p().locator(inDelivery('di-dl-view')).count()) === 1,
      'the control: the view is found',
    );

    // no advance without the local confirmation, and no second delivery session (no typed-id field anywhere)
    assert.equal(deliveryRequests().length, 1);
    assert.ok(deliveryRequests().every((m) => m.op === 'status'));
    assert.equal(await p().locator(sel('di-dl-id')).count(), 0);
    assert.ok(
      (await p().locator(inDelivery('di-scan-delivery-read')).count()) === 1,
      'the control: the read is found',
    );
  },
  STEPS_MS,
);

test(
  'THE WIRING FOLLOWS THE PROJECTION: the fixture link perturbed to another delivery, the note and the next read follow it, and the first id no longer passes',
  async () => {
    await boot(ID_A);
    await openDetails();
    await linkNoteNames(ID_A);

    // the perturbation: the same Mission, the REAL projection, now linked to ID_B
    socket!.send(JSON.stringify(officeLinkedTo(ID_B)));
    await p().waitForFunction(
      ([selector, id]) => document.querySelector(selector)?.textContent?.includes(id) === true,
      [inDelivery('di-scan-delivery-link'), ID_B] as const,
    );
    await linkNoteNames(ID_B);
    await assert.rejects(linkNoteNames(ID_A));

    const before = deliveryRequests().length;
    await p().click(inDelivery('di-scan-delivery-read'));
    await until('the status request', () => deliveryRequests().length > before);
    theOneStatusRequestFor(ID_B, before);
    assert.throws(() => theOneStatusRequestFor(ID_A, before));
    assert.ok(deliveryRequests().every((m) => m.op === 'status' && m.deliveryId === ID_B));
  },
  STEPS_MS,
);

test(
  "THE READ CONTROL IS DRAWN AS A CONTROL: its edge, MEASURED, is the scan face's control edge, the same as a peer control's in this frame",
  async () => {
    await boot(ID_A);
    await openDetails();
    const edgeOf = (selector: string) =>
      p().$eval(selector, (el) => {
        const s = getComputedStyle(el);
        return { style: s.borderTopStyle, width: s.borderTopWidth, color: s.borderTopColor };
      });
    // THE PEER, drawn in this same frame: a scan-face control on the same edge token the step control uses (1px solid
    // var(--di-scan-edge)). Its measured edge shows the scan face's control token resolves here.
    // a computed colour serializes with three components when opaque and a fourth, its alpha, otherwise
    const alphaOf = (color: string) => {
      const parts = color.slice(color.indexOf('(') + 1, color.lastIndexOf(')')).split(',');
      assert.ok(
        parts.length === 3 || parts.length === 4,
        `a computed colour of three or four components: ${color}`,
      );
      return parts.length === 4 ? Number(parts[3]) : 1;
    };
    const peer = await edgeOf(sel('di-scan-comments'));
    assert.deepEqual({ style: peer.style, width: peer.width }, { style: 'solid', width: '1px' });
    assert.ok(alphaOf(peer.color) > 0, `the peer edge has a visible colour: ${peer.color}`);
    // THE READ CONTROL: a boundary a control needs to be seen, the same edge as the controls beside it
    const read = await edgeOf(inDelivery('di-scan-delivery-read'));
    assert.deepEqual(read, peer, "the read control's edge is the scan face's control edge");
  },
  STEPS_MS,
);

// ---------------------------------------------------------------------------------------------
// THE STATUS VIEW'S CLOSING BLOCK AND ITS OWN CONTROLS. Added after the cases above, which are unchanged.

/** A status read the adapter REFUSED: the reply a notice about this delivery comes from. SYNTHETIC words. */
function refusedReply(seq: number): string {
  return JSON.stringify({
    type: 'diDeliveryReply',
    seq,
    op: 'status',
    state: 'refused',
    problem: 'SYNTHETIC_refusal',
    reason: 'SYNTHETIC refusal (this test).',
    answer: null,
  });
}

/** A status answer naming ONE eligible step, its exact four-key binding and the adapter's two notes. SYNTHETIC words. */
function eligibleReply(seq: number, deliveryId: string): string {
  const binding = {
    delivery_id: deliveryId,
    revision: 1,
    candidate_identity_digest_sha256: 'c0'.repeat(32),
    step: 'COMMIT',
  };
  const office = {
    eligible: { step: 'COMMIT', binding },
    local_confirmation_note: 'SYNTHETIC local-confirmation note (this test).',
    performs_steps_note: 'SYNTHETIC performs-steps note (this test).',
  };
  const delivery = { delivery_id: deliveryId, revision: 1, phase: 'SYNTHETIC phase' };
  return JSON.stringify({
    type: 'diDeliveryReply',
    seq,
    op: 'status',
    state: 'ok',
    problem: null,
    reason: null,
    answer: { status: { delivery, office } },
  });
}

/** One read of the linked delivery: exactly one status request, answered by `reply` with that request's seq. */
async function readAnsweredBy(reply: (seq: number) => string): Promise<void> {
  const before = deliveryRequests().length;
  await p().click(inDelivery('di-scan-delivery-read'));
  await until('the status request', () => deliveryRequests().length > before);
  socket!.send(reply(theOneStatusRequestFor(ID_A, before)));
}

/** THE NEW STATE: a refused read leaves a notice about this delivery; the next read's answer names one eligible step. */
async function eligibleWithNotice(): Promise<void> {
  await readAnsweredBy(refusedReply);
  await p().waitForSelector(inDelivery('di-dl-notice'));
  await readAnsweredBy((seq) => eligibleReply(seq, ID_A));
  await p().waitForSelector(inDelivery('di-dl-eligible'));
}

/** With the seam on: label the page as this harness, centre `testId` in the frame, save it, then remove the label. */
async function harnessShotCentred(name: string, testId: string): Promise<void> {
  if (SHOTS === null) return;
  fs.mkdirSync(SHOTS, { recursive: true });
  await p().evaluate((text) => {
    const el = document.createElement('div');
    el.setAttribute('data-testid', 'di-harness-shot-label');
    el.textContent = text;
    el.style.cssText =
      'position:fixed;left:0;right:0;bottom:0;z-index:2147483647;padding:6px 12px;font:600 13px sans-serif;' +
      'background:black;color:gold;border-top:2px solid gold;pointer-events:none';
    document.body.appendChild(el);
  }, HARNESS_LABEL);
  const target = p().locator(inDelivery(testId));
  await target.evaluate((el) => el.scrollIntoView({ block: 'center' }));
  await p().screenshot({ path: path.join(SHOTS, `${name}.png`) });
  await p().evaluate(() =>
    document.querySelector('[data-testid="di-harness-shot-label"]')?.remove(),
  );
}

/** A control's top edge (style, width, colour), as the browser computed it in this frame. */
const edgeIn = (selector: string) =>
  p().$eval(selector, (el) => {
    const s = getComputedStyle(el);
    return { style: s.borderTopStyle, width: s.borderTopWidth, color: s.borderTopColor };
  });

/** A computed colour's alpha: three components when opaque, and a fourth, its alpha, otherwise. */
function alphaIn(color: string): number {
  const parts = color.slice(color.indexOf('(') + 1, color.lastIndexOf(')')).split(',');
  assert.ok(
    parts.length === 3 || parts.length === 4,
    `a computed colour of three or four components: ${color}`,
  );
  return parts.length === 4 ? Number(parts[3]) : 1;
}

test(
  "THE STATUS VIEW'S CLOSING BLOCK, BOTH FORMS: with no step named it says why none can be confirmed; with one step eligible it is the LOCAL CHECK; nothing is advanced",
  async () => {
    await boot(ID_A);
    await openDetails();

    // THE EXISTING HARNESS STATE, reproduced here so its own case stays untouched: one read, answered with no office part
    await readAnsweredBy((seq) => okReply(seq, ID_A, MARKUP));
    await p().waitForSelector(inDelivery('di-dl-not-eligible'));
    const why = await p().locator(inDelivery('di-dl-not-eligible')).innerText();
    assert.ok(why.includes('(eligibility_unreadable)'), `the reason is named: ${why}`);
    assert.equal(await p().locator(inDelivery('di-dl-eligible')).count(), 0);
    await harnessShotCentred('harness-03-delivery-not-eligible-1440x900', 'di-dl-not-eligible');

    // THE NEW STATE: a notice about this delivery, then one step eligible
    await eligibleWithNotice();
    assert.equal(await p().locator(inDelivery('di-dl-not-eligible')).count(), 0);
    const local = await p().locator(inDelivery('di-dl-eligible')).innerText();
    assert.ok(local.includes('LOCAL CHECK.'), `the LOCAL CHECK is drawn: ${local}`);
    assert.ok(local.includes('COMMIT'), `it names its one step: ${local}`);
    assert.equal(await p().locator(inDelivery('di-dl-notice')).count(), 1);
    const notice = await p().locator(inDelivery('di-dl-notice')).innerText();
    assert.ok(
      notice.includes('The adapter refused to read this delivery'),
      `the notice: ${notice}`,
    );
    await harnessShotCentred('harness-04-delivery-eligible-1440x900', 'di-dl-eligible');

    // nothing is advanced: three reads and no step request; the box is unticked and the step waits for it
    assert.equal(deliveryRequests().length, 3);
    assert.ok(deliveryRequests().every((m) => m.op === 'status'));
    assert.equal(await p().locator(inDelivery('di-dl-confirm')).isChecked(), false);
    assert.equal(await p().locator(inDelivery('di-dl-advance')).isDisabled(), true);
  },
  STEPS_MS,
);

test(
  "THE STATUS VIEW'S OWN CONTROLS, MEASURED: a notice's Dismiss and the LOCAL CHECK's step button carry the scan face's control edge, the same as a peer control's in this frame",
  async () => {
    await boot(ID_A);
    await openDetails();
    await eligibleWithNotice();
    // THE PEER in this frame, on the scan face's control edge token: the Comments button, as the read control's case measures it
    const peer = await edgeIn(sel('di-scan-comments'));
    assert.deepEqual({ style: peer.style, width: peer.width }, { style: 'solid', width: '1px' });
    assert.ok(alphaIn(peer.color) > 0, `the peer edge has a visible colour: ${peer.color}`);
    // BOTH CONTROLS, measured before either is judged, in one assertion so a failure shows both values
    const dismiss = await edgeIn(`${inDelivery('di-dl-notice')} button`);
    const step = await edgeIn(inDelivery('di-dl-advance'));
    assert.deepEqual(
      { dismiss, step },
      { dismiss: peer, step: peer },
      "the status view's own controls carry the scan face's control edge",
    );
  },
  STEPS_MS,
);
