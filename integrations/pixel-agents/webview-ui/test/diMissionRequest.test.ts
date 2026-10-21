/**
 * Item 4 (original 19): the Mission request workspace through the ACTUAL App, in a real browser, over
 * the REAL stack, with progression done by the REAL runtime.
 *
 * WHAT THIS EVIDENCE IS, EXACTLY: real-App INTEGRATION OVER SOURCE. The App is mounted from source by
 * Vite's dev server (`createServer`, `test/harness/di-app.html` -> `src/main.tsx`); it is NOT the production
 * package and this file is NOT production-package browser proof. It exercises the real seams listed below and
 * demonstrates no-polling progression. The production package is a separate artifact, linked to the frozen
 * source by `scripts/di-candidate-manifest.mjs`, and is opened separately by the final acceptance.
 *
 * What is REAL: the App (`test/harness/di-app.html` -> `src/main.tsx`), the workspace and its hook, the
 * WebSocket protocol, the server's `handleDiClientMessage` and `DiMissionRequests` relay (the bearer token
 * stays in this Node process; the page never sees it), the loopback Python adapter, the panel controller,
 * the journal, the local_request surface, Mission Core, the operator-attested approval, the MissionBridge,
 * the per-Mission run guard, and `dirun`'s MissionCore mode (`target_runtime.cli.main`: its lock, pass
 * loop, status file and exit) running in the adapter process through `tests/_panel_e2e_server.py`.
 *
 * What is CONTROLLED (the only substitutions, the same ones the bridge and conversation suites make; none of
 * this is evidence of a live provider or Herdr): the Codex process's replies, the process the bridge would
 * spawn, and the team's observed state and files. The office state the panel sits in is a labelled FIXTURE.
 *
 * The no-polling gate. A started Mission must reach a retrievable verified result because the RUNTIME
 * advanced it, not because the page asked. The relay is closed (the page's reads are held, unanswered) while
 * the team works and finishes; the adapter's call counter must not move while the Mission goes AUTHORIZED ->
 * RUNNING -> COMPLETED; only then are the held reads let through and the page, unprompted, shows the result.
 *
 * The UI lessons carried from item 3: focus is sampled on EVERY animation frame across every async
 * transition (including a control that stops being available mid-flow), keyboard drives the flow, focus is
 * visible, and the words on screen are the honest ones (WITHDRAWN never "denied", revised, expired,
 * attested-not-verified, uncertain shown as uncertain).
 *
 * Browser: the installed Chrome (playwright-core, channel `chrome`), else Playwright's Chromium; with neither,
 * every test is skipped with an explicit message.
 */
import { type ChildProcessWithoutNullStreams, spawn } from 'node:child_process';
import { createHash } from 'node:crypto';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

import type { Browser, Page } from 'playwright-core';
import { chromium } from 'playwright-core';
import type { ViteDevServer } from 'vite';
import { createServer } from 'vite';
import { afterAll, afterEach, beforeAll, beforeEach, expect, test } from 'vitest';
import type { WebSocket } from 'ws';
import { WebSocketServer } from 'ws';

import type { DiOfficeState } from '../../core/src/messages.ts';
import { AgentStateStore } from '../../server/src/agentStateStore.ts';
import { handleDiClientMessage } from '../../server/src/di/diClientMessages.ts';
import { DiMissionRequests } from '../../server/src/di/missionRequests.ts';

const STEPS_MS = 120_000;
/** The EXISTING screenshot seam (item 3's): set DI_REVIEW_SHOTS=<dir> to also save the views. FIXTURE data only. */
const SHOTS = process.env['DI_REVIEW_SHOTS'] ?? null;
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const REPO_ROOT = path.resolve(root, '../../..');
const HARNESS = path.join(REPO_ROOT, 'tests', '_panel_e2e_server.py');
const office = JSON.parse(
  fs.readFileSync(
    path.join(root, 'test/fixtures/di-office-state-code-review-v2.FIXTURE.json'),
    'utf-8',
  ),
) as { states: Record<'live', DiOfficeState> };

/**
 * The identity of the bytes a preview was taken from: sha256 over the sorted relative path and bytes of every
 * source file this stack is made of (the three packages' sources, the protocol, the Python stack, the runtime
 * and this test). A different tree gives a different value; it is not a signature, only a name for the bytes.
 */
function sourceIdentity(): { digest: string; files: number } {
  const repo = REPO_ROOT;
  const include = [
    'integrations/pixel-agents/core/src',
    'integrations/pixel-agents/core/asyncapi.yaml',
    'integrations/pixel-agents/server/src',
    'integrations/pixel-agents/webview-ui/src',
    'integrations/pixel-agents/webview-ui/test/diMissionRequest.test.ts',
    'mission_panel',
    'mission',
    'local_request',
    'target_runtime',
    'dimission_panel.py',
    'dirun.py',
    'tests/_panel_e2e_server.py',
  ];
  const files: string[] = [];
  const walk = (rel: string) => {
    const abs = path.join(repo, rel);
    const stat = fs.statSync(abs);
    if (stat.isDirectory()) {
      for (const name of fs.readdirSync(abs).sort()) {
        if (name === '__pycache__' || name === 'node_modules' || name.endsWith('.pyc')) continue;
        walk(path.join(rel, name));
      }
    } else files.push(rel);
  };
  for (const rel of include) walk(rel);
  const hash = createHash('sha256');
  for (const rel of files.sort()) {
    hash.update(`${rel}\0`);
    hash.update(fs.readFileSync(path.join(repo, rel)));
    hash.update('\0');
  }
  return { digest: hash.digest('hex'), files: files.length };
}
const shotsTaken: string[] = [];
/** Save a view (when the seam is on). Scroll `into` to the middle first so the part being shown is in the frame. */
async function shot(page: Page, name: string, into?: string) {
  if (SHOTS === null) return;
  fs.mkdirSync(SHOTS, { recursive: true });
  if (into)
    await page.$eval(into, (el) => el.scrollIntoView({ block: 'center' })).catch(() => undefined);
  await sleep(150);
  await page.screenshot({ path: path.join(SHOTS, `${name}.png`) });
  shotsTaken.push(`${name}.png`);
}

// ── the real adapter process ────────────────────────────────────────────────────────────────────

type Json = Record<string, any>; // eslint-disable-line @typescript-eslint/no-explicit-any

class Harness {
  private child: ChildProcessWithoutNullStreams | null = null;
  private lines: string[] = [];
  private waiters: Array<(line: string) => void> = [];
  private buffer = '';
  readonly tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'di-mr-browser-'));
  readonly discovery = path.join(this.tmp, 'disc', 'panel.json');
  stderr = '';

  async start(): Promise<void> {
    this.child = spawn(
      'python3',
      [HARNESS, '--work-dir', path.join(this.tmp, 'w'), '--discovery-file', this.discovery],
      {
        env: { ...process.env, PYTHONPATH: REPO_ROOT, PYTHONDONTWRITEBYTECODE: '1' },
        stdio: ['pipe', 'pipe', 'pipe'],
      },
    );
    this.child.stdout.on('data', (d: Buffer) => {
      this.buffer += d.toString('utf-8');
      let i: number;
      while ((i = this.buffer.indexOf('\n')) >= 0) {
        const line = this.buffer.slice(0, i);
        this.buffer = this.buffer.slice(i + 1);
        const waiter = this.waiters.shift();
        if (waiter) waiter(line);
        else this.lines.push(line);
      }
    });
    this.child.stderr.on('data', (d: Buffer) => {
      this.stderr += d.toString('utf-8');
    });
    const hello = JSON.parse(await this.line(30_000)) as { endpoint: string };
    if (hello.endpoint !== 'mission_panel_e2e')
      throw new Error(`unexpected startup: ${JSON.stringify(hello)}`);
  }

  private line(timeoutMs: number): Promise<string> {
    const queued = this.lines.shift();
    if (queued !== undefined) return Promise.resolve(queued);
    return new Promise((resolve, reject) => {
      const timer = setTimeout(
        () =>
          reject(
            new Error(`harness silent for ${timeoutMs} ms; stderr: ${this.stderr.slice(0, 1500)}`),
          ),
        timeoutMs,
      );
      this.waiters.push((l) => {
        clearTimeout(timer);
        resolve(l);
      });
    });
  }

  async control(command: Record<string, unknown>): Promise<Json> {
    this.child?.stdin.write(`${JSON.stringify(command)}\n`);
    return JSON.parse(await this.line(30_000)) as Json;
  }

  async stop(): Promise<void> {
    const child = this.child;
    if (!child) return;
    const exited = new Promise<void>((resolve) => child.once('exit', () => resolve()));
    if (child.exitCode === null) {
      try {
        child.stdin.write('{"op":"stop"}\n');
      } catch {
        /* already gone */
      }
      const timer = setTimeout(() => child.kill('SIGTERM'), 15_000);
      await exited;
      clearTimeout(timer);
    }
    this.child = null;
  }

  secrets(): { token: string; port: number } {
    const found = JSON.parse(fs.readFileSync(this.discovery, 'utf-8')) as {
      token: string;
      port: number;
    };
    return { token: found.token, port: found.port };
  }
}

// ── the real server relay, between the page and the adapter ────────────────────────────────────

let server: ViteDevServer | null = null;
let wss: WebSocketServer | null = null;
let browser: Browser | null = null;
let unavailable: string | null = null;
let url = '';

let harness: Harness;
let panel: DiMissionRequests;
let pages: Page[] = [];
let sockets: WebSocket[] = [];
/** Every diMissionRequest the page sent, exactly as received. */
let sent: Array<Record<string, unknown>> = [];
let otherMessages: string[] = [];
/** While true the relay HOLDS the page's reads: nothing reaches the adapter and nothing is answered. */
let closed = false;
let held: Array<() => void> = [];
/** ONE window's hold: while its socket is here the relay holds that window's READS; its writes still pass. */
const READ_OPS = new Set(['refresh', 'conversation', 'request', 'offer', 'result']);
let readHolds = new Map<WebSocket, Array<() => void>>();
/** Every request the relay handed to the owner, by window, with the owner's reply once it came. */
type Relayed = {
  ws: WebSocket;
  msg: Record<string, unknown>;
  reply: Record<string, unknown> | null;
};
let relayed: Relayed[] = [];
let socketOf = new Map<Page, WebSocket>();
let dialogs = 0;
/**
 * A labelled SYNTHETIC seam: when set, every reply the real relay would send to the page passes through it
 * first. It stands in for a panel this window has never met (version skew), which no real panel here can be.
 * Null in every test but the one that says otherwise.
 */
let mutateReply: ((message: Record<string, unknown>) => Record<string, unknown>) | null = null;
let outsideRequests: string[] = [];
let pageRequests: string[] = [];
let connected: Promise<WebSocket> = Promise.resolve(null as unknown as WebSocket);
let resolveConnected: (ws: WebSocket) => void = () => undefined;

const ctxBase = { store: new AgentStateStore(), cache: {} as never, extensionVersion: 'test' };

function serve(ws: WebSocket) {
  ws.on('message', (data) => {
    const msg = JSON.parse(String(data)) as Record<string, unknown>;
    if (msg.type === 'diMissionRequest') {
      sent.push(msg);
      const entry: Relayed = { ws, msg, reply: null };
      const send = (m: Record<string, unknown>) => {
        entry.reply = m;
        return (
          ws.readyState === ws.OPEN && ws.send(JSON.stringify(mutateReply ? mutateReply(m) : m))
        );
      };
      const deliver = () => {
        relayed.push(entry);
        handleDiClientMessage(msg, send, {
          ...ctxBase,
          missionRequest: (m, reply) => void panel.handle(m, reply),
        });
      };
      const windowHold = readHolds.get(ws);
      if (closed) held.push(deliver);
      else if (windowHold && READ_OPS.has(String(msg.op))) windowHold.push(deliver);
      else deliver();
    } else if (msg.type === 'webviewReady') {
      resolveConnected(ws);
    } else if (msg.type !== 'saveAgentSeats') {
      otherMessages.push(String(msg.type));
    }
  });
}

const expectConnection = () => {
  connected = new Promise((resolve) => (resolveConnected = resolve));
};
const openRelay = () => {
  closed = false;
  const flush = held;
  held = [];
  for (const deliver of flush) deliver();
};
/**
 * Hold ONE window's reads from here, once none of its reads is still with the owner: every reply it gets after
 * this was computed before this call, so what it shows can change only through its own writes.
 */
async function holdReads(page: Page) {
  const ws = socketOf.get(page)!;
  readHolds.set(ws, []);
  await waitFor('the window’s reads already sent to be answered', async () =>
    relayed.every((e) => e.ws !== ws || e.reply !== null),
  );
}
const releaseReads = (page: Page) => {
  const ws = socketOf.get(page)!;
  const flush = readHolds.get(ws) ?? [];
  readHolds.delete(ws);
  for (const deliver of flush) deliver();
};
/**
 * Close a window that has nothing more to show, and wait until nothing it sent can still hold one of the office's
 * concurrent-call slots (`MAX_CONCURRENT_PANEL_CALLS`): none of its reads is held at the relay, its connection is
 * closed (no message of it can arrive after that), and every call of it the relay delivered has been answered (the
 * office frees a call's slot before it answers it).
 */
async function closeWindow(page: Page) {
  const ws = socketOf.get(page)!;
  expect(closed || readHolds.has(ws), 'the window still has reads held at the relay').toBe(false);
  await page.close();
  await waitFor('the closed window’s connection to close', async () => ws.readyState === ws.CLOSED);
  await waitFor('every call of the closed window to be answered', async () =>
    relayed.every((e) => e.ws !== ws || e.reply !== null),
  );
}
/** The window's decisions that reached the owner, each with the owner's reply (null until it answers). */
const decisionsFrom = (page: Page) =>
  relayed.filter((e) => e.ws === socketOf.get(page) && e.msg.op === 'decide');

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
    wss!.handleUpgrade(req, sock, head, (ws) => {
      sockets.push(ws);
      serve(ws);
    });
  });
  const addr = server.httpServer?.address();
  const port = typeof addr === 'object' && addr !== null ? addr.port : 0;
  url = `http://localhost:${port}/test/harness/di-app.html`;
  const launch = { headless: true, ignoreDefaultArgs: ['--hide-scrollbars'] };
  try {
    browser = await chromium.launch({ channel: 'chrome', ...launch });
  } catch {
    try {
      browser = await chromium.launch(launch);
    } catch (e) {
      unavailable = `no Chrome or Playwright Chromium to drive: ${String(e).split('\n')[0]}`;
    }
  }
}, 120_000);

afterAll(async () => {
  if (SHOTS !== null && shotsTaken.length > 0) {
    const identity = sourceIdentity();
    fs.writeFileSync(
      path.join(SHOTS, 'MANIFEST.json'),
      JSON.stringify(
        {
          what: "FIXTURE / VISUAL TEST screenshots. Synthetic conversation, proposals and results; nothing here is a real request awaiting a real decision, and no approval was given on anyone's behalf.",
          builtFrom:
            'the real App mounted from SOURCE by the Vite dev server (a fixture source build). NOT the production package.',
          sourceIdentity: identity.digest,
          sourceIdentityMethod:
            'sha256 over, for each file in sorted relative-path order: path, NUL, bytes, NUL; over the include list in sourceIdentity() of webview-ui/test/diMissionRequest.test.ts',
          sourceFiles: identity.files,
          controlled:
            "the Codex process replies, the process the bridge would spawn, and the team's observed state and files",
          liveDispatch: false,
          approval: "NOT_GRANTED for any child Mission; none was clicked on anyone's behalf",
          screenshots: shotsTaken,
        },
        null,
        2,
      ),
    );
  }
  await browser?.close();
  wss?.close();
  await server?.close();
}, STEPS_MS);

const VIEW = { width: 1600, height: 1000 };

beforeEach(async (ctx) => {
  if (unavailable) {
    console.warn(`SKIPPED (mission requests): ${unavailable}`);
    ctx.skip();
    return;
  }
  harness = new Harness();
  await harness.start();
  panel = new DiMissionRequests(harness.discovery, { log: () => undefined });
  sent = [];
  otherMessages = [];
  closed = false;
  held = [];
  readHolds = new Map();
  relayed = [];
  socketOf = new Map();
  dialogs = 0;
  mutateReply = null;
  outsideRequests = [];
  pageRequests = [];
  pages = [];
  sockets = [];
}, STEPS_MS);

afterEach(async () => {
  for (const page of pages) await page.close().catch(() => undefined);
  for (const ws of sockets) ws.terminate();
  await harness?.stop();
  fs.rmSync(harness?.tmp ?? '', { recursive: true, force: true });
}, STEPS_MS);

// ── driving the page ───────────────────────────────────────────────────────────────────────────

const T = (id: string) => `[data-testid="${id}"]`;
const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

async function waitFor<V>(
  what: string,
  fn: () => Promise<V | false | null | undefined>,
  ms = 30_000,
): Promise<V> {
  const deadline = Date.now() + ms;
  for (;;) {
    const got = await fn();
    if (got) return got;
    if (Date.now() > deadline) throw new Error(`timed out waiting for ${what}`);
    await sleep(100);
  }
}

async function openWindow(): Promise<Page> {
  expectConnection();
  const page = await browser!.newPage({ viewport: VIEW });
  pages.push(page);
  page.on('dialog', (d) => {
    dialogs += 1;
    void d.dismiss();
  });
  page.on('request', (r) => {
    const u = new URL(r.url());
    pageRequests.push(r.url());
    if (u.hostname !== 'localhost' && u.protocol.startsWith('http')) outsideRequests.push(r.url());
  });
  await page.goto(url, { waitUntil: 'domcontentloaded' });
  const ws = await connected;
  socketOf.set(page, ws);
  // The office is a labelled FIXTURE (the existing explicit `fixtureMode`): the whole window says so.
  ws.send(JSON.stringify({ ...office.states.live, fixtureMode: true }));
  await page.waitForSelector(`${T('di-office-panel')}, ${T('di-office-panel-collapsed')}`, {
    timeout: 30_000,
  });
  const collapsed = await page.$(T('di-office-panel-collapsed'));
  if (collapsed) await collapsed.click();
  await page.waitForSelector(T('di-mr-entry'), { timeout: 30_000 });
  return page;
}

type Watch = {
  frames: number;
  bodyFrames: number;
  dropped: string[];
  keysReachedWorld: string[];
  trace: string[];
};
/** Samples focus on EVERY animation frame and records every focus drop and every key that escaped the workspace. */
const watchFocus = (page: Page) =>
  page.evaluate(() => {
    const state: Watch = { frames: 0, bodyFrames: 0, dropped: [], keysReachedWorld: [], trace: [] };
    (window as unknown as { __watch: Watch }).__watch = state;
    const open = () => document.querySelector('[data-testid="di-mr"]') !== null;
    const name = (n: Element | null) =>
      n === null
        ? 'nothing'
        : n === document.body
          ? 'body'
          : `${n.tagName.toLowerCase()}[${n.getAttribute('data-testid') ?? ''}]`;
    let lastActive = '';
    const sample = () => {
      state.frames += 1;
      if (open() && document.activeElement === document.body) state.bodyFrames += 1;
      const now = name(document.activeElement);
      if (now !== lastActive) {
        state.trace.push(`frame ${state.frames}: focus is on ${now}`);
        lastActive = now;
      }
      requestAnimationFrame(sample);
    };
    requestAnimationFrame(sample);
    // A focus change with no destination is only a DROP if focus has not landed somewhere in the workspace by
    // the time the page has rendered: when the focused control is removed by a state change the browser reports
    // the blur at once, and the workspace's own focus rescue moves focus on in the same turn. What must never
    // happen is focus left on nothing.
    document.addEventListener(
      'focusout',
      (e) => {
        if (!open() || (e as FocusEvent).relatedTarget !== null) return;
        const who = name(e.target as Element);
        requestAnimationFrame(() =>
          requestAnimationFrame(() => {
            const ws = document.querySelector('[data-testid="di-mr"]');
            if (ws !== null && !ws.contains(document.activeElement)) state.dropped.push(who);
          }),
        );
      },
      true,
    );
    window.addEventListener('keydown', (e) => {
      const inside = (e.target as Element | null)?.closest?.('[data-testid="di-mr"]') != null;
      if (inside) state.keysReachedWorld.push(e.key);
    });
  });
const frames = (page: Page, n: number) =>
  page.evaluate(
    (count) =>
      new Promise<void>((resolve) => {
        let k = 0;
        const step = () => (++k >= count ? resolve() : requestAnimationFrame(step));
        requestAnimationFrame(step);
      }),
    n,
  );
/** After a transition: focus never fell to the document body, was never dropped, and no key escaped to the office. */
async function expectFocusKept(page: Page, after: string) {
  await frames(page, 6);
  const seen = await page.evaluate(() => (window as unknown as { __watch: Watch }).__watch);
  const inside = await page.evaluate(() => {
    const ws = document.querySelector('[data-testid="di-mr"]');
    return ws !== null && ws.contains(document.activeElement);
  });
  const summary = {
    after,
    bodyFrames: seen.bodyFrames,
    dropped: seen.dropped,
    keysEscaped: seen.keysReachedWorld,
    focusInsideWorkspace: inside,
  };
  expect(
    summary,
    `focus after: ${after}; where focus went: ${JSON.stringify(seen.trace.slice(-8))}`,
  ).toEqual({ after, bodyFrames: 0, dropped: [], keysEscaped: [], focusInsideWorkspace: true });
}

const text = (page: Page, sel: string) => page.$eval(sel, (el) => (el as HTMLElement).innerText);
const exists = async (page: Page, sel: string) => (await page.$(sel)) !== null;
const available = (page: Page, sel: string) =>
  page.$eval(sel, (el) => el.getAttribute('aria-disabled') !== 'true');
async function pressKey(page: Page, sel: string, key: string) {
  await page.focus(sel);
  await page.keyboard.press(key);
}
async function whenText(page: Page, sel: string, pattern: RegExp, ms = 30_000) {
  await page.waitForFunction(
    ([s, source, flags]) => {
      const el = document.querySelector(s as string);
      return (
        el !== null &&
        new RegExp(source as string, flags as string).test((el as HTMLElement).innerText)
      );
    },
    [sel, pattern.source, pattern.flags],
    { timeout: ms },
  );
}
const state = () => harness.control({ op: 'state' });
/** For a failing step: what the page said and what crossed the relay, so a failure explains itself. */
async function whatHappened(page: Page): Promise<string> {
  const body = await page
    .$eval(T('di-mr-body'), (el) => (el as HTMLElement).innerText)
    .catch(() => '<no body>');
  return JSON.stringify({
    ops: sent.map((m) => `${m.op}`).slice(-12),
    writes: sent.filter((m) => m.op !== 'refresh' && m.op !== 'conversation' && m.op !== 'request'),
    stage: await page
      .$eval(T('di-mr-stage'), (el) => (el as HTMLElement).innerText)
      .catch(() => null),
    staleBanner: await exists(page, T('di-mr-stale')),
    bodyHead: body.slice(0, 700),
    served: await state().catch(() => null),
    notices: await page
      .$eval(T('di-mr-notices'), (el) => (el as HTMLElement).innerText)
      .catch(() => null),
    status: await page
      .$eval(T('di-mr-status'), (el) => (el as HTMLElement).innerText)
      .catch(() => null),
    bodyTail: body.slice(-600),
    harness: harness.stderr.slice(-300),
  });
}

const PROPOSAL = {
  objective:
    'FIXTURE (a synthetic request, not a real one): make the readiness probe stable under load',
  target_context: 'the readiness subsystem',
  requested_scope: 'the probe and its tests; no schema change',
  requested_action_scope: ['repository_read', 'engineering_change'],
};
const reply = (words: string, proposal: Record<string, unknown> = PROPOSAL): string =>
  `${words}\n\n\`\`\`mission-proposal\n${JSON.stringify(proposal)}\n\`\`\`\n`;

async function openWorkspace(page: Page) {
  await page.click(T('di-mr-open'));
  await page.waitForSelector(T('di-mr'), { timeout: 15_000 });
  await watchFocus(page);
  await waitFor(
    'the panel to be read',
    async () => !(await exists(page, T('di-mr-status'))),
    20_000,
  );
}
/** Ordinary words in, by keyboard: Ctrl+Enter sends. Returns once the draft is on screen. */
async function ask(page: Page, words: string, assistant: string, proposal = PROPOSAL) {
  await harness.control({ op: 'provider', reply: reply(assistant, proposal) });
  if (!(await exists(page, T('di-mr-composer')))) await page.click(T('di-mr-tab-ask'));
  await page.fill(T('di-mr-composer'), words);
  await pressKey(page, T('di-mr-composer'), 'Control+Enter');
  await page.waitForSelector(T('di-mr-draft'), { timeout: 30_000 });
}
/** The draft becomes a proposal of record; the window opens it. */
async function submitDraft(page: Page): Promise<string> {
  await pressKey(page, T('di-mr-submit'), 'Enter');
  // Recording the proposal opens it: the person is taken to the proposal of record (and its decision),
  // not left looking at the draft.
  try {
    await page.waitForSelector(T('di-mr-decision'), { timeout: 30_000 });
  } catch (e) {
    throw new Error(`${String(e).split('\n')[0]} -- ${await whatHappened(page)}`, { cause: e });
  }
  // The reference of THE request now open, as the proposal's own source line states it.
  const source = await text(page, T('di-mr-proposal-source'));
  const found = /lr-[0-9a-f]{32}/.exec(source);
  if (found === null) throw new Error(`the open proposal names no request reference: ${source}`);
  return found[0];
}
/** The person types the exact word and presses Approve, by keyboard, after reading the exact offer. */
async function approve(page: Page) {
  await page.waitForSelector(T('di-mr-offer-expiry'), { timeout: 30_000 });
  await page.fill(T('di-mr-word'), 'approve');
  await waitFor('Approve to become available', () => available(page, T('di-mr-approve')));
  await pressKey(page, T('di-mr-approve'), 'Enter');
  try {
    await page.waitForSelector(T('di-mr-dispatch'), { timeout: 30_000 });
  } catch (e) {
    throw new Error(`${String(e).split('\n')[0]} -- ${await whatHappened(page)}`, { cause: e });
  }
}
async function start(page: Page) {
  await pressKey(page, T('di-mr-dispatch'), 'Enter');
  await waitFor('the start to be recorded', async () => !(await exists(page, T('di-mr-dispatch'))));
}
const missionState = async (ref: string) => {
  const s = await state();
  const entries = Object.values(s.mission_states as Record<string, string>);
  return { all: entries, count: entries.length, ref };
};

/**
 * Reach a control the way a keyboard user does: start in `from` and press `key` until `target` has focus
 * (so `:focus-visible` applies, which a script's `element.focus()` does not always trigger).
 */
async function tabTo(page: Page, target: string, key: string, from: string) {
  await page.focus(from);
  for (let i = 0; i < 60; i++) {
    await page.keyboard.press(key);
    if (await page.evaluate((sel) => document.activeElement?.matches(sel) === true, target)) return;
  }
  throw new Error(`${target} was not reached with ${key} from ${from}`);
}

/** Every visible title or control the office's fixture watermark overlaps (it must overlap none). */
const coveredByWatermark = (page: Page) =>
  page.evaluate(() => {
    const banner = document.querySelector('[data-testid="di-fixture-banner"]');
    if (banner === null || getComputedStyle(banner).visibility === 'hidden') return [];
    const b = banner.getBoundingClientRect();
    const covered: string[] = [];
    const selector =
      'button, input, textarea, select, summary, a[href], [role="tab"], h1, h2, h3, h4, canvas';
    for (const el of Array.from(document.querySelectorAll(selector))) {
      if (banner.contains(el)) continue;
      const r = el.getBoundingClientRect();
      if (r.width === 0 || r.height === 0) continue;
      // The world canvas fills the window behind everything; only titles and controls matter here.
      if (el.tagName === 'CANVAS') continue;
      const hit = !(
        r.right <= b.left ||
        r.left >= b.right ||
        r.bottom <= b.top ||
        r.top >= b.bottom
      );
      const box = (q: DOMRect) =>
        `${Math.round(q.left)},${Math.round(q.top)} ${Math.round(q.width)}x${Math.round(q.height)}`;
      if (hit)
        covered.push(
          `${el.tagName.toLowerCase()}[${el.getAttribute('data-testid') ?? (el as HTMLElement).innerText?.slice(0, 24)}] at ${box(r)} under the watermark at ${box(b)}`,
        );
    }
    return covered;
  });

// ── tests ───────────────────────────────────────────────────────────────────────────────────────

test(
  'A. After an explicit Start the RUNTIME advances the Mission to a verified, retrievable result while the page cannot read anything',
  async () => {
    await harness.control({ op: 'runtime', start: true });
    await waitFor('the runtime to be passing', async () => (await state()).runtime_alive === true);
    const page = await openWindow();
    await openWorkspace(page);
    await waitFor('the status file to show a pass', async () => {
      const line = await text(page, T('di-mr-progression-line')).catch(() => '');
      return /Automatic progress is ON/.test(line) ? line : false;
    });
    // SHORT: the state and what Start does. Counters and diagnostics are in the disclosure, closed.
    const line = await text(page, T('di-mr-progression-line'));
    expect(line).toBe(
      'Automatic progress is ON. Start begins the work; after that it advances by itself.',
    );
    expect(await text(page, T('di-mr-progression'))).not.toMatch(/Passes so far/);
    // The window says, twice and in words, that none of this is real: the office's own fixture label and the
    // workspace's. A screenshot of either part carries it.
    expect(await text(page, T('di-fixture-banner'))).toMatch(/FIXTURE \/ VISUAL TEST/);
    expect(await text(page, T('di-mr-fixture-banner'))).toMatch(
      /Nothing here is a real request awaiting a real decision/,
    );
    // The office's fixture watermark is preview furniture and must cover NO title and NO control (the first
    // preview showed it lettered over "Mission requests" and "Wider reading").
    expect(
      await coveredByWatermark(page),
      'the fixture watermark covers a title or a control',
    ).toEqual([]);
    await shot(page, '01-fixture-workspace-progression-automatic');
    await expectFocusKept(page, 'the workspace opening');

    await ask(page, 'make the readiness probe stable', 'The readiness probe.');
    await expectFocusKept(page, 'sending a message and reading the draft');
    expect(await text(page, T('di-mr-draft'))).toMatch(/A draft, not yet a proposal of record/);
    await shot(page, '02-draft-not-yet-a-proposal', T('di-mr-draft'));
    const ref = await submitDraft(page);
    await expectFocusKept(page, 'submitting the draft and opening the proposal of record');
    expect(await text(page, T('di-mr-provenance'))).toMatch(
      /operator attestation, not independently verified/,
    );
    await shot(
      page,
      '03-proposal-of-record-exact-offer-and-attested-provenance',
      T('di-mr-provenance'),
    );
    await approve(page);
    await shot(page, '04-approved-start-is-a-separate-act', T('di-mr-run'));
    await expectFocusKept(page, 'approving (the Approve control stops being available mid-flow)');
    expect(await text(page, T('di-mr-stage-provenance'))).toMatch(
      /operator attested not independently verified/,
    );

    // Approval started nothing: the person must press Start, and until then no runtime moves the Mission.
    await harness.control({ op: 'team', status: 'RUNNING' });
    await sleep(1_200);
    expect((await state()).spawn_calls).toBe(0);
    await start(page);
    await expectFocusKept(page, 'pressing Start (the Start control disappears)');
    // With the runtime passing, observing is not a button.
    expect(await exists(page, T('di-mr-observe'))).toBe(false);
    expect(await exists(page, T('di-mr-reconcile'))).toBe(false);
    await shot(page, '05-started-the-runtime-advances-no-observe-button', T('di-mr-run'));

    // THE GATE: close the relay. The page can read nothing and the adapter hears nothing.
    closed = true;
    const closedAt = Date.now() / 1000;
    await sleep(800);
    // Read at a PASS BOUNDARY: no runtime pass is in flight while this is read. Every pass it counts has
    // finished before the team is told it is COMPLETE below, so the pass that writes COMPLETED begins after
    // it, and the harness counts a pass before any of its work: one pass is enough to move the count.
    const before = await harness.control({ op: 'state', at_pass_boundary: true });
    expect(before.ok, `no runtime pass boundary: ${JSON.stringify(before)}`).toBe(true);
    const beforeAt = Date.now() / 1000;
    const calls = before.service_calls as number;
    await waitFor('the runtime to observe the team running', async () => {
      const s = await state();
      return Object.values(s.mission_states as Record<string, string>).includes('RUNNING');
    });
    await harness.control({ op: 'artifacts', request_ref: ref });
    await harness.control({ op: 'team', status: 'COMPLETE' });
    await waitFor('the runtime to verify the Mission', async () => {
      const s = await state();
      return Object.values(s.mission_states as Record<string, string>).includes('COMPLETED');
    });
    const after = await state();
    expect(
      after.service_calls,
      `the adapter was asked something while the Mission advanced: ${JSON.stringify(after.service_log)} (relay closed at ${closedAt.toFixed(2)}, counter read at ${beforeAt.toFixed(2)}, held reads: ${held.length})`,
    ).toBe(calls);
    expect(after.spawn_calls, 'progression spawned again').toBe(1);
    expect(after.runtime_passes as number).toBeGreaterThan(before.runtime_passes as number);
    // The page, which could not read, honestly still shows what it last read: it did not cause this.
    expect(await text(page, T('di-mr-stage-label'))).not.toMatch(/Verified result/);
    await shot(page, '06-runtime-finished-the-page-has-read-nothing-yet', T('di-mr-stage'));

    // Let the page's own ordinary reads through. It shows the result unprompted; it clicks nothing.
    openRelay();
    await whenText(page, T('di-mr-stage-label'), /Verified result/);
    await expectFocusKept(page, 'the record changing under the open request');
    await pressKey(page, T('di-mr-result-load'), 'Enter');
    await whenText(page, T('di-mr-result-text'), /readiness probe fix landed/);
    const result = await text(page, T('di-mr-result'));
    expect(result).toMatch(/not independent verification/);
    expect(result).toMatch(/not been pushed, merged or released/);
    await shot(page, '07-verified-result-retrieved-not-delivered', T('di-mr-result'));
    expect(otherMessages).toEqual([]);
    expect(dialogs).toBe(0);
    expect(outsideRequests).toEqual([]);
    await expectFocusKept(page, 'loading the result');
  },
  STEPS_MS,
);

test(
  'B. With no runtime passing the screen says nothing advances by itself and offers the manual fallback; starting the runtime takes the buttons away',
  async () => {
    const page = await openWindow();
    await openWorkspace(page);
    await waitFor('the progression line', async () =>
      /Automatic progress is OFF/.test(
        await text(page, T('di-mr-progression-line')).catch(() => ''),
      ),
    );
    expect(await text(page, T('di-mr-progression-line'))).toBe(
      'Automatic progress is OFF. After Start nothing advances the work unless you do it by hand or start the runtime.',
    );
    // The why (the runtime's own words) is in the disclosure, which a keyboard opens.
    await pressKey(page, T('di-mr-progression-summary'), 'Enter');
    expect(await text(page, T('di-mr-progression'))).toMatch(
      /Runtime status: no progression runtime has reported here/,
    );
    await ask(page, 'make the readiness probe stable', 'The readiness probe.');
    const ref = await submitDraft(page);
    await approve(page);
    await harness.control({ op: 'team', status: 'RUNNING' });
    await start(page);
    expect(await text(page, T('di-mr-stage-notice'))).toMatch(
      /Nothing is advancing this Mission automatically/,
    );
    await page.waitForSelector(T('di-mr-observe'));
    await shot(page, '10-no-runtime-nothing-advances-by-itself-manual-fallback', T('di-mr-stage'));
    await sleep(1_200);
    expect((await missionState(ref)).all, 'nothing moved the Mission by itself').toEqual([
      'AUTHORIZED',
    ]);

    // Now the real runtime starts. The screen follows its status file; the observe button goes away; the
    // Mission is observed running with no click on it.
    await harness.control({ op: 'runtime', start: true });
    await whenText(page, T('di-mr-progression-line'), /Automatic progress is ON/, 30_000);
    await waitFor(
      'observe to leave the screen',
      async () => !(await exists(page, T('di-mr-observe'))),
    );
    await waitFor('the runtime to observe the team', async () =>
      (await missionState(ref)).all.includes('RUNNING'),
    );
    await whenText(page, T('di-mr-stage-label'), /Team working/);
    expect((await state()).spawn_calls).toBe(1);
    await shot(page, '11-runtime-started-the-buttons-are-gone', T('di-mr-stage'));
    await expectFocusKept(page, 'the runtime taking over from the manual buttons');
  },
  STEPS_MS,
);

test(
  'C. An unknown start is shown as unknown, is never started again, and is reconciled by the runtime without a second spawn',
  async () => {
    const page = await openWindow();
    await openWorkspace(page);
    await ask(page, 'make the readiness probe stable', 'The readiness probe.');
    await submitDraft(page);
    await approve(page);
    await harness.control({ op: 'spawn', raises: 'after_child' });
    await start(page);
    await whenText(page, T('di-mr-stage-label'), /Start outcome unknown/);
    const detail = await text(page, T('di-mr-stage-detail'));
    expect(detail).toMatch(/not started again/);
    expect(detail).toMatch(/not a guarantee/);
    expect(await exists(page, T('di-mr-dispatch'))).toBe(false);
    await page.waitForSelector(T('di-mr-reconcile'));
    await shot(page, '20-start-outcome-unknown-never-started-again', T('di-mr-stage'));
    expect((await state()).spawn_calls).toBe(1);

    await harness.control({ op: 'spawn', raises: null });
    await harness.control({ op: 'runtime', start: true });
    await whenText(page, T('di-mr-stage-label'), /Team working/, 45_000);
    expect((await state()).spawn_calls, 'reconciliation respawned').toBe(1);
    await shot(page, '21-reconciled-by-the-runtime-without-a-second-start', T('di-mr-stage'));
    await expectFocusKept(page, 'an uncertain start being reconciled');
  },
  STEPS_MS,
);

test(
  'D. The words are honest: attested-not-verified, WITHDRAWN never denied, revised, expired, and the owner sees one decision from two windows',
  async () => {
    const first = await openWindow();
    await openWorkspace(first);
    // — a proposal of record, seen from two windows: the SAME frozen offer —
    await ask(first, 'make the readiness probe stable', 'The readiness probe.');
    const ref = await submitDraft(first);
    await first.waitForSelector(T('di-mr-offer-expiry'));
    const second = await openWindow();
    await openWorkspace(second);
    await second.click(T('di-mr-tab-requests'));
    await second.click(T(`di-mr-request-${ref}`));
    await second.waitForSelector(T('di-mr-offer-expiry'));
    const digestOf = (page: Page) =>
      page.evaluate(async () => {
        const toggle = document.querySelector(
          '[data-testid="di-mr-offer"] [data-testid="di-mr-digest-toggle"]',
        );
        (toggle as HTMLElement).click();
        await new Promise((r) => requestAnimationFrame(() => r(null)));
        return (
          document.querySelector('[data-testid="di-mr-offer"] [data-testid="di-mr-digest-full"]')
            ?.textContent ?? null
        );
      });
    const digest1 = await digestOf(first);
    const digest2 = await digestOf(second);
    expect(digest1).toMatch(/^[0-9a-f]{64}$/);
    expect(digest2, 'two windows were shown different offers').toBe(digest1);
    // The same absolute expiry (the countdown beside it is each window's own clock).
    const clockTime = async (page: Page) =>
      (await text(page, T('di-mr-offer-expiry'))).split(' (')[0];
    expect(await clockTime(first)).toBe(await clockTime(second));

    // — the first window decides; the second still holds the older display —
    // The second window's reads are held at the relay, so its own refresh cannot take the live offer off its
    // screen while the first window decides. Its writes still reach the owner.
    await holdReads(second);
    await approve(first);
    // Start can show (the first window's own poll read the approved record) before the owner's answer to that
    // window's decide is back: wait for the answer before reading it.
    await waitFor('the owner to answer the first window’s decision', async () => {
      const got = decisionsFrom(first);
      return got.length > 0 && got.every((e) => e.reply !== null) ? got : false;
    });
    const [firstDecision] = decisionsFrom(first);
    expect(
      decisionsFrom(first),
      'the first window’s decision did not reach the owner',
    ).toHaveLength(1);
    expect(firstDecision.reply, 'the first window’s decision was not recorded').toMatchObject({
      state: 'ok',
      answer: { decision: { idempotent: false } },
    });
    const authorizationId = (firstDecision.reply!.answer as Json).decision
      .authorization_id as string;
    expect(authorizationId).toMatch(/^ma-[0-9a-f]{32}$/);
    // The second window, still showing the live offer, decides too: the attempt is always made.
    expect(await exists(second, T('di-mr-word')), 'the second window lost the live offer').toBe(
      true,
    );
    await second.fill(T('di-mr-word'), 'approve');
    await waitFor('Approve to be available in the second window', () =>
      available(second, T('di-mr-approve')),
    );
    await pressKey(second, T('di-mr-approve'), 'Enter');
    const secondDecisions = await waitFor(
      'the owner to answer the second window’s decision',
      async () => {
        const got = decisionsFrom(second);
        return got.length > 0 && got.every((e) => e.reply !== null) ? got : false;
      },
    );
    // It reached the owner, which found the first decision: no second authority.
    expect(
      secondDecisions,
      'the second window’s decision did not reach the owner once',
    ).toHaveLength(1);
    expect(secondDecisions[0].reply, 'the second window made a second decision').toMatchObject({
      state: 'ok',
      answer: { decision: { idempotent: true, authorization_id: authorizationId } },
    });
    expect((await state()).authorizations, 'two windows made two approvals').toBe(1);
    releaseReads(second);
    await whenText(second, T('di-mr-stage-label'), /Approved|approved/);

    // — a request withdrawn is WITHDRAWN, never "denied" —
    await ask(first, 'a second thing', 'A second probe.', {
      ...PROPOSAL,
      objective: 'A different, withdrawn request',
    });
    await submitDraft(first);
    await first.click(T('di-mr-withdraw'));
    await first.waitForSelector(T('di-mr-withdraw-confirm-box'));
    await pressKey(first, T('di-mr-withdraw-confirm'), 'Enter');
    await whenText(first, T('di-mr-stage-label'), /Withdrawn/);
    expect(await text(first, T('di-mr-stage-detail'))).toMatch(/not a decision/);
    expect(await text(first, T('di-mr'))).not.toMatch(/denied/i);
    await shot(first, '30-withdrawn-never-denied', T('di-mr-stage'));
    await expectFocusKept(first, 'withdrawing a proposal');

    // — a revision is a separate record, never an edit; the old one reads "Revised" —
    await ask(first, 'a third thing', 'A third probe.', {
      ...PROPOSAL,
      objective: 'A request that will be revised',
    });
    const toRevise = await submitDraft(first);
    await first.click(T('di-mr-revise'));
    await first.waitForSelector(T('di-mr-revising'));
    await ask(first, 'make it smaller', 'A smaller one.', {
      ...PROPOSAL,
      objective: 'The revised, smaller request',
    });
    await pressKey(first, T('di-mr-submit'), 'Enter');
    await first.waitForSelector(T('di-mr-decision'), { timeout: 30_000 });
    await first.click(T('di-mr-tab-requests'));
    await first.click(T(`di-mr-request-${toRevise}`));
    await whenText(first, T('di-mr-stage-label'), /Revised/);
    expect(await text(first, T('di-mr-stage-detail'))).toMatch(/nothing was edited in place/);
    expect(await text(first, T('di-mr'))).not.toMatch(/denied/i);
    await shot(first, '31-revised-a-separate-record-nothing-edited-in-place', T('di-mr-stage'));

    // — an approval that lapsed before Start is EXPIRED, not quietly re-offered —
    await harness.control({ op: 'clock', advance: 1_000 });
    await first.click(T('di-mr-tab-requests'));
    await first.click(T(`di-mr-request-${ref}`));
    await whenText(first, T('di-mr-stage-label'), /expired/i, 30_000);
    expect(await exists(first, T('di-mr-dispatch'))).toBe(false);
    expect(await exists(first, T('di-mr-approve'))).toBe(false);
    await shot(first, '32-approval-expired-not-quietly-re-offered', T('di-mr-stage'));
  },
  STEPS_MS,
);

test(
  'E. Hostile text is inert and visible, nothing secret reaches the page, and the world stays usable',
  async () => {
    const page = await openWindow();
    await openWorkspace(page);
    const hostile = {
      objective: '<img src=x onerror="window.__pwned=1"> make it stable‮reversed​zero',
      target_context: '<script>window.__pwned=2</script>',
      requested_scope: 'no schema change\nline two <b>not bold</b>',
      requested_action_scope: ['repository_read', 'engineering_change'],
    };
    await ask(page, 'do the thing', 'Here <a href="javascript:alert(1)">a link</a>.', hostile);
    expect(
      await page.evaluate(() => (window as unknown as { __pwned?: number }).__pwned),
    ).toBeUndefined();
    expect(await page.$('[data-testid="di-mr"] img')).toBeNull();
    expect(await page.$('[data-testid="di-mr"] script')).toBeNull();
    expect(await page.$('[data-testid="di-mr"] a')).toBeNull();
    expect(await page.$$eval(T('di-mr-control'), (els) => els.length)).toBeGreaterThan(0);
    expect(await text(page, T('di-mr-draft-proposal'))).toContain('<img src=x');
    // What the page can see: no token, no adapter port, no path of the state directory.
    const { token, port } = harness.secrets();
    const html = await page.content();
    expect(html).not.toContain(token);
    expect(html).not.toContain(harness.tmp);
    expect(pageRequests.filter((u) => u.includes(`:${port}`))).toEqual([]);
    expect(JSON.stringify(sent)).not.toContain(token);
    expect(
      await page.evaluate(() => JSON.stringify({ ...localStorage, ...sessionStorage })),
    ).not.toContain(token);
    expect(dialogs).toBe(0);
    expect(outsideRequests).toEqual([]);
    // The world is still there and still usable: the workspace docks beside it, it does not cover it.
    const canvas = await page.$eval('canvas', (el) => el.getBoundingClientRect().width);
    expect(canvas).toBeGreaterThanOrEqual(240);
    await expectFocusKept(page, 'reading a hostile draft');
  },
  STEPS_MS,
);

test(
  'F. The flow is keyboard-operable with visible focus, and a narrow window explains why it covers the world in words',
  async () => {
    const page = await openWindow();
    await page.focus(T('di-mr-open'));
    await page.keyboard.press('Enter');
    await page.waitForSelector(T('di-mr'));
    await watchFocus(page);
    // Every control reached by Tab shows focus by something other than colour alone.
    const visible: string[] = [];
    for (let i = 0; i < 8; i++) {
      await page.keyboard.press('Tab');
      const ring = await page.evaluate(() => {
        const el = document.activeElement as HTMLElement | null;
        if (el === null || el === document.body) return 'body';
        const style = getComputedStyle(el);
        const shown =
          (style.outlineStyle !== 'none' && parseFloat(style.outlineWidth) > 0) ||
          style.boxShadow !== 'none';
        return shown ? 'ring' : `NO RING on ${el.tagName}[${el.getAttribute('data-testid') ?? ''}]`;
      });
      visible.push(ring);
    }
    expect(
      visible.filter((v) => v !== 'ring'),
      'a focused control showed no focus indication',
    ).toEqual([]);
    // Tab is allowed to leave a docked, non-modal workspace; Escape closes it while focus is inside.
    await page.focus(T('di-mr-composer'));
    await page.keyboard.press('Escape');
    await page.waitForSelector(T('di-mr'), { state: 'detached' });
    // Focus returns to where the person was, never the void.
    expect(await page.evaluate(() => document.activeElement?.getAttribute('data-testid'))).toBe(
      'di-mr-open',
    );
    // A narrow window: the workspace must cover the world, and says so in text (not a tooltip).
    await page.setViewportSize({ width: 560, height: 900 });
    await page.click(T('di-mr-open'));
    await page.waitForSelector(T('di-mr-covering-note'));
    expect(await text(page, T('di-mr-covering-note'))).toMatch(
      /narrow, so the requests cover the office and its panel\. Close the requests to use the world again/,
    );
    await page.setViewportSize(VIEW);
  },
  STEPS_MS,
);

test(
  'G. The request is named, the runtime disclosure is keyboard-operable, and the headers and controls are clear in wider-reading and narrow layouts',
  async () => {
    await harness.control({ op: 'runtime', start: true });
    const page = await openWindow();
    // — what is being requested has a name, in the entry and in the workspace —
    expect(await text(page, T('di-mr-entry'))).toMatch(/Codex Orchestrator/);
    expect(await text(page, T('di-mr-open'))).toBe('Ask the Codex Orchestrator');
    expect(await text(page, T('di-mr-entry-tagline'))).toMatch(
      /Ask Codex for engineering work in ordinary words/,
    );
    await openWorkspace(page);
    expect(await text(page, T('di-mr-heading'))).toBe('Codex Orchestrator');
    expect(await text(page, T('di-mr-tagline'))).toMatch(/you decide/);
    expect(await page.getAttribute(T('di-mr'), 'aria-label')).toBe('Codex Orchestrator requests');
    expect(await text(page, 'label[for="di-mr-composer"]')).toMatch(/Tell the Codex Orchestrator/);
    await whenText(page, T('di-mr-progression-line'), /Automatic progress is ON/);

    // — the disclosure: closed by default, opened and closed by the KEYBOARD, focus visible, nothing title-only —
    const disclosure = () =>
      page.$eval(T('di-mr-progression-details'), (el) => (el as HTMLDetailsElement).open);
    expect(await disclosure()).toBe(false);
    expect(await text(page, T('di-mr-progression'))).not.toMatch(
      /Passes so far|never starts a Mission/,
    );
    await tabTo(page, T('di-mr-progression-summary'), 'Shift+Tab', T('di-mr-composer'));
    const ring = await page.evaluate(() => {
      const style = getComputedStyle(document.activeElement as Element);
      return style.outlineStyle !== 'none' && parseFloat(style.outlineWidth) > 0;
    });
    expect(ring, 'the disclosure shows no focus indication').toBe(true);
    await page.keyboard.press('Enter');
    expect(await disclosure()).toBe(true);
    const opened = await text(page, T('di-mr-progression'));
    expect(opened).toMatch(/Runtime status: the progression runtime is passing/);
    expect(opened).toMatch(/Passes so far: \d+/);
    expect(opened).toMatch(/never starts a Mission/);
    expect(opened).toMatch(/never records a failure as a success/);
    await page.keyboard.press('Space');
    expect(await disclosure()).toBe(false);
    expect(
      await page.$$eval(
        `${T('di-mr-progression')} [title], ${T('di-mr-progression')} [aria-describedby]`,
        (els) => els.length,
      ),
      'a title or hover-only explanation',
    ).toBe(0);
    await expectFocusKept(page, 'opening and closing the disclosure by keyboard');

    // — a request is open, so the headers and controls being inspected are the real ones —
    await ask(page, 'make the readiness probe stable', 'The readiness probe.');
    await submitDraft(page);
    await page.waitForSelector(T('di-mr-offer-expiry'));

    // wider reading
    const docked = await page.$eval(T('di-mr'), (el) => el.getBoundingClientRect().width);
    await pressKey(page, T('di-mr-wide'), 'Enter');
    await waitFor(
      'the wider reading mode',
      async () => (await page.getAttribute(T('di-mr'), 'data-wide')) === 'true',
    );
    expect(await page.getAttribute(T('di-mr-wide'), 'aria-pressed')).toBe('true');
    const wide = await page.$eval(T('di-mr'), (el) => el.getBoundingClientRect().width);
    expect(wide, 'wider reading did not widen the pane').toBeGreaterThan(docked);
    expect(
      await coveredByWatermark(page),
      'the watermark covers a title or control in wider reading',
    ).toEqual([]);
    await page.$eval(T('di-mr-body'), (el) => (el.scrollTop = 0));
    await shot(page, '40-wider-reading-mode-headers-and-controls');

    // narrow: the workspace covers the window, says so in words, and carries its own FIXTURE label
    await page.setViewportSize({ width: 560, height: 900 });
    await page.waitForSelector(T('di-mr-covering-note'));
    expect(await page.getAttribute(T('di-mr'), 'data-covering')).toBe('true');
    expect(await page.$eval(T('di-fixture-banner'), (el) => getComputedStyle(el).visibility)).toBe(
      'hidden',
    );
    expect(await text(page, T('di-mr-fixture-banner'))).toMatch(
      /Nothing here is a real request awaiting a real decision/,
    );
    const inView = await page.evaluate(() => {
      const w = window.innerWidth;
      return [
        'di-mr-heading',
        'di-mr-wide',
        'di-mr-close',
        'di-mr-tab-ask',
        'di-mr-tab-requests',
      ].map((id) => {
        const r = document.querySelector(`[data-testid="${id}"]`)!.getBoundingClientRect();
        return { id, inside: r.left >= 0 && r.right <= w && r.width > 0 && r.height > 0 };
      });
    });
    expect(
      inView.filter((v) => !v.inside),
      'a header control is clipped or off-screen at the narrow width',
    ).toEqual([]);
    expect(await coveredByWatermark(page)).toEqual([]);
    // Nothing is clipped or scrolls sideways at the narrow width: the list stacks above the detail.
    const sideways = await page.evaluate(() => {
      const body = document.querySelector('[data-testid="di-mr-body"]') as HTMLElement;
      const root = document.querySelector('[data-testid="di-mr"]') as HTMLElement;
      return {
        body: body.scrollWidth - body.clientWidth,
        workspace: root.scrollWidth - root.clientWidth,
      };
    });
    expect(sideways, 'the narrow workspace scrolls sideways').toEqual({ body: 0, workspace: 0 });
    await page.$eval(T('di-mr-body'), (el) => (el.scrollTop = 0));
    await shot(page, '41-narrow-viewport-headers-and-controls');
    await expectFocusKept(page, 'resizing to a narrow window');
    await page.setViewportSize(VIEW);
  },
  STEPS_MS,
);

// ── Round 27 ────────────────────────────────────────────────────────────────────────────────────

/** The proof rules as the offer box shows them, and the offer's own digest. */
const offerRules = (page: Page) => text(page, T('di-mr-offer-proof'));

test(
  'H. The proof rules are shown inside the offer that binds them, and a revision that changes ONLY the rules is refused as stale until the new rules are read',
  async () => {
    const page = await openWindow();
    await openWorkspace(page);
    await ask(page, 'make the readiness probe stable', 'The readiness probe.');
    const ref = await submitDraft(page);
    await page.waitForSelector(T('di-mr-offer-expiry'), { timeout: 30_000 });

    // The rules are IN the offer box (not only in the proposal above it), in words, with the exact numbers.
    const before = await offerRules(page);
    // ... and the same words are in the proposal itself, so the offer is not the only place they appear.
    const proposalRulesBefore = await text(page, T('di-mr-proposal-rules'));
    expect(proposalRulesBefore).toBe(before);
    expect(await text(page, T('di-mr-offer'))).toMatch(/What counts as done/);
    expect(before).toMatch(/run_result/);
    expect(before).toMatch(/bound to this run and can be recovered/);
    expect(before).toMatch(/VERIFICATION_RECORD/);
    expect(before).toMatch(/1 hour \(3600 s\)/);
    expect(before).toMatch(/At most 1 attempt and 2 checkpoints/);
    expect(await text(page, T('di-mr-decision'))).not.toMatch(/cannot show everything/);
    await shot(page, '40-proof-rules-inside-the-offer', T('di-mr-offer'));

    // A revision of record that changes the proof rules and NOTHING ELSE.
    const edited = await harness.control({ op: 'edit_contract', request_ref: ref });
    expect(edited.new_digest).not.toBe(edited.old_digest);
    expect(edited.revision).toBe(2);
    await page.waitForSelector(T('di-mr-stale'), { timeout: 30_000 });

    // The frozen display still shows the OLD rules (never the unseen new ones), and the whole decision (the
    // offer, the word, Approve) is taken away until the current proposal is read.
    expect(await text(page, T('di-mr-proposal-rules'))).toBe(proposalRulesBefore);
    expect(await exists(page, T('di-mr-decision')), 'a decision was offered over changed rules').toBe(false);
    expect(await exists(page, T('di-mr-approve'))).toBe(false);
    expect(await exists(page, T('di-mr-word'))).toBe(false);
    await sleep(500);
    expect(sent.filter((m) => m.op === 'decide')).toEqual([]);
    expect((await state()).authorizations).toBe(0);
    await shot(page, '41-rules-changed-display-is-stale', T('di-mr-stale'));

    // Reading the current proposal shows the NEW rules, and the next approval binds to them.
    await page.click(T('di-mr-acknowledge'));
    await page.waitForSelector(T('di-mr-offer-expiry'), { timeout: 30_000 });
    const after = await offerRules(page);
    expect(after).not.toBe(before);
    expect(after).toMatch(/At most 6 attempts and 2 checkpoints/);
    expect(after).toMatch(/1 minute \(60 s\)/);
    await approve(page);
    const decides = sent.filter((m) => m.op === 'decide');
    expect(decides).toHaveLength(1);
    expect(decides[0]).toMatchObject({ revision: 2, digest: edited.new_digest });
    expect((await state()).authorizations).toBe(1);
    await expectFocusKept(page, 'reading the changed rules and approving them');
  },
  STEPS_MS,
);

test(
  'I. SYNTHETIC version skew: rules this window cannot represent are stated, and approval is refused with the offer on display',
  async () => {
    const page = await openWindow();
    await openWorkspace(page);
    // A panel that sends a rule family this window has never met, in every proposal it relays.
    const addUnknownRule = (value: unknown): unknown => {
      if (Array.isArray(value)) return value.map(addUnknownRule);
      if (typeof value !== 'object' || value === null) return value;
      const out: Record<string, unknown> = {};
      for (const [key, inner] of Object.entries(value)) {
        out[key] =
          key === 'proof_contract' && typeof inner === 'object' && inner !== null
            ? { ...(inner as Record<string, unknown>), unmodelled_rule_family: { anything: true } }
            : addUnknownRule(inner);
      }
      return out;
    };
    mutateReply = (m) => addUnknownRule(m) as Record<string, unknown>;
    await ask(page, 'make the readiness probe stable', 'The readiness probe.');
    await submitDraft(page);
    await page.waitForSelector(T('di-mr-offer-expiry'), { timeout: 30_000 });

    // The window says, in words, what it cannot show and that approving is refused: in the decision, and
    // in the offer box where the rules would have been.
    const blocked = await text(page, T('di-mr-decision-blocked'));
    expect(blocked).toMatch(/cannot be approved from this window/);
    expect(blocked).toMatch(/cannot show everything this proposal binds/);
    expect(blocked).toMatch(/unmodelled_rule_family/);
    expect(await page.getAttribute(T('di-mr-offer-proof-absent'), 'data-represented')).toBe('false');
    // The exact offer is on display, and the word is typed — and still nothing can be approved.
    await page.fill(T('di-mr-word'), 'approve');
    expect(await available(page, T('di-mr-approve'))).toBe(false);
    await pressKey(page, T('di-mr-approve'), 'Enter');
    await sleep(800);
    expect(sent.filter((m) => m.op === 'decide'), 'a decision left this window').toEqual([]);
    expect((await state()).authorizations).toBe(0);
    // Not approving is not a trap: the person can still withdraw it.
    expect(await exists(page, T('di-mr-withdraw'))).toBe(true);
    await shot(page, '42-synthetic-unrepresented-rules-approval-refused', T('di-mr-decision'));
    await expectFocusKept(page, 'being told approval is refused');
  },
  STEPS_MS,
);

test(
  'J. A conversation the panel cut to fit is shown as cut: how many of how many, on the screen',
  async () => {
    const page = await openWindow();
    await openWorkspace(page);
    await ask(page, 'make the readiness probe stable', 'The readiness probe.');
    expect(await exists(page, T('di-mr-turns-omitted'))).toBe(false);
    // 49 more turns, at the accepted maximum of astral text, in the REAL journal of the conversation open here.
    const seeded = await harness.control({
      op: 'seed_turns',
      turns: 49,
      character: 'astral',
      append_to_latest: true,
    });
    expect(seeded.ok).toBe(true);
    await page.waitForSelector(T('di-mr-turns-omitted'), { timeout: 30_000 });
    const note = await text(page, T('di-mr-turns-omitted'));
    const shown = Number(/most recent (\d+) of 50 turns/.exec(note)?.[1]);
    expect(shown, note).toBeGreaterThan(0);
    expect(shown, note).toBeLessThan(50);
    expect(note).toMatch(new RegExp(`${50 - shown} earlier turns are kept in the record and not shown here`));
    // At the accepted maximum it is the BYTE budget that cut, and the screen says that, not a generic bound.
    expect(note).toMatch(/the turns shown already reach the about 3 MB of text this window receives at once/);
    expect(note).not.toMatch(/at most 50 turns/);
    // What is on screen is exactly what the note says: that many turns, the newest ones, none past them.
    const rendered = await page.$$eval('[data-testid^="di-mr-turn-"]', (els) =>
      els
        .map((el) => el.getAttribute('data-testid') ?? '')
        .filter((id) => /^di-mr-turn-\d+$/.test(id))
        .map((id) => Number(id.slice('di-mr-turn-'.length))),
    );
    expect(rendered).toHaveLength(shown);
    expect(rendered.at(-1)).toBe(50);
    expect(rendered[0]).toBe(50 - shown + 1);
    // The note is a visible note, reachable by a screen reader, not a tooltip.
    expect(await page.getAttribute(T('di-mr-turns-omitted'), 'role')).toBe('note');
    await shot(page, '43-conversation-cut-and-said-so', T('di-mr-turns-omitted'));
    await expectFocusKept(page, 'a conversation being cut to fit');
  },
  STEPS_MS,
);

// ── startup diagnostic delta ────────────────────────────────────────────────────────────────────

test(
  'K. A Start that fails after the roles were contracted says why, bound to this exact attempt; it stays in doubt and is never replayed',
  async () => {
    const page = await openWindow();
    await openWorkspace(page);
    await ask(page, 'make the readiness probe stable', 'The readiness probe.');
    const ref = await submitDraft(page);
    await approve(page);
    // The REAL role-binding code and retention helper run into the child workspace (one role with no session),
    // then the typed failure is raised, exactly as real startup does. Only Herdr's answers are scripted.
    await harness.control({ op: 'spawn', raises: 'binding_failure' });
    await start(page);
    await page.waitForSelector(T('di-mr-start-cause'), { timeout: 30_000 });
    const cause = await text(page, T('di-mr-start-cause'));
    expect(await page.getAttribute(T('di-mr-start-cause'), 'data-state')).toBe('BOUND');
    expect(cause).toMatch(/Why this Start is in doubt/);
    expect(cause).toMatch(/binding the team.s roles to their live sessions/);
    expect(cause).toMatch(/1 role could not be bound to a live session within the settle window/);
    expect(cause).toMatch(/lead1: the recorded binding carries no session/);
    expect(cause).toMatch(/Diagnostic file: startup-binding-failure-[0-9TZ]+-[0-9a-f]{8}\.json/);
    expect(cause).toMatch(/this window cannot open it/);
    expect(cause).toMatch(/a retry is not implied to be safe/);
    // The Start is the unchanged HOLD: in doubt, not offered again, spawned exactly once.
    expect(await text(page, T('di-mr-stage-label'))).toMatch(/Start outcome unknown/);
    expect(await exists(page, T('di-mr-dispatch'))).toBe(false);
    expect((await state()).spawn_calls).toBe(1);
    // No path, session id or pane text reaches the page.
    const body = await text(page, T('di-mr-body'));
    expect(body).not.toContain(harness.tmp);
    expect(body).not.toMatch(/session-h-|pane-lead|\/repo/);
    await shot(page, '50-start-in-doubt-with-its-bound-cause', T('di-mr-start-cause'));
    await expectFocusKept(page, 'a Start whose bound cause is then shown');

    // A second window sees the same cause: it is derived from the retained file, not from any window's state.
    const second = await openWindow();
    await openWorkspace(second);
    await second.click(T('di-mr-tab-requests'));
    await second.click(T(`di-mr-request-${ref}`));
    await second.waitForSelector(T('di-mr-start-cause'), { timeout: 30_000 });
    expect(await text(second, T('di-mr-start-cause'))).toBe(cause);
    // That was the second window's one claim. It is closed and drained, so its polling holds none of the office's
    // call slots while the first window goes on with another request below.
    await closeWindow(second);

    // The run is then stopped durably by a reconcile that can prove nothing. It is no longer \u201cin doubt\u201d: the
    // window stops using that stage's words (and its Reconcile advice), while the retained cause remains the honest
    // report of a Start that never confirmed. No receipt was ever recorded, and nothing was spawned again.
    await pressKey(page, T('di-mr-reconcile'), 'Enter');
    await page.waitForSelector(`${T('di-mr-start-cause')}[data-closed="true"]`, { timeout: 30_000 });
    const closed = await text(page, T('di-mr-start-cause'));
    expect(closed).toMatch(/Why this Start never confirmed/);
    expect(closed).toMatch(/lead1: the recorded binding carries no session/);
    expect(closed).toMatch(/closed and cannot be started again/);
    expect(closed).not.toMatch(/in doubt|Reconcile only|retry/i);
    expect(await text(page, T('di-mr-stage-label'))).toMatch(/Run stopped/);
    expect((await state()).spawn_calls).toBe(1);

    // A second request whose start fails before ANY diagnostic exists: the window says the cause is NOT known,
    // and never shows the first request's diagnostic for it.
    await ask(page, 'another thing', 'A second probe.', {
      ...PROPOSAL,
      objective: 'A second request, whose start fails before startup begins',
    });
    await submitDraft(page);
    await approve(page);
    await harness.control({ op: 'spawn', raises: 'before_child' });
    await start(page);
    await page.waitForSelector(`${T('di-mr-start-cause')}[data-state="NONE"]`, { timeout: 30_000 });
    const unknown = await text(page, T('di-mr-start-cause'));
    expect(unknown).toMatch(/not known/);
    expect(unknown).toMatch(/No retained startup diagnostic is bound to this attempt\./);
    expect(unknown).not.toMatch(/startup-binding-failure|lead1/);
    expect((await state()).spawn_calls).toBe(2);
    await shot(page, '51-start-in-doubt-cause-not-known', T('di-mr-start-cause'));
  },
  STEPS_MS,
);
