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

import { checkDiSeatPreferences } from '../../core/src/diSeatPreferences.ts';
import type { DiOfficeState } from '../../core/src/messages.ts';
import { AgentStateStore } from '../../server/src/agentStateStore.ts';
import { handleDiClientMessage } from '../../server/src/di/diClientMessages.ts';
import { DiMissionRequests } from '../../server/src/di/missionRequests.ts';
import { MR_MIN_BESIDE_PX, MR_WORLD_KEEP_PX } from '../src/components/diMissionRequestModel.ts';
import {
  DI_MINIMIZED_STRIP_WIDTH_PX,
  DI_PANEL_WIDTH_PX,
  DI_WORLD_CONTROLS_MIN_PX,
  DI_WORLD_ROOMY_PX,
} from '../src/constants.ts';
import type { A11ySweep } from './diChromeA11y.ts';
import { a11ySweep } from './diChromeA11y.ts';
import { occlusion } from './diOcclusion.ts';
import { reflowReach } from './diReflow.ts';
import { renderedCopy } from './diRenderedCopy.ts';
import type { TabStop, TabTraversal } from './diTabTraversal.ts';
import { tabProblems, tabTraversal } from './diTabTraversal.ts';
import { textFit } from './diTextFit.ts';
import { tokenScopeAudit } from './diTokenScope.ts';

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
/** The identity-keyed seat saves the page sent, with the payload and the phase each was sent in (see the classification in the websocket handler). */
let seatPreferenceSaves: Array<{ relayClosed: boolean; payload: unknown }> = [];
/** While true the relay HOLDS the page's reads: nothing reaches the adapter and nothing is answered. */
let closed = false;
let held: Array<() => void> = [];
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
      if (closed) held.push(deliver);
      else deliver();
    } else if (msg.type === 'webviewReady') {
      resolveConnected(ws);
    } else if (msg.type === 'saveDiSeatPreferences') {
      // An ALLOWED LOCAL-PERSISTENCE message (P2-C), like `saveAgentSeats`: the page saves WHERE each identity sits — a seating preference, no Mission
      // state, no request, nothing canonical — the first time an identity is seated and afterwards only when the map changes. `closed` here means only
      // that the diMissionRequest relay is HELD; the websocket and the local save channel stay connected, so this is an ordinary save on a connected
      // channel, not a message sent offline. It is recorded WITH its payload and validated below; `otherMessages` stays strict for everything else.
      seatPreferenceSaves.push({ relayClosed: closed, payload: msg.state });
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
 * Close a window that has nothing more to show, and wait until nothing it sent can still hold one of the office's
 * concurrent-call slots (`MAX_CONCURRENT_PANEL_CALLS`): the relay is not holding reads, its connection is
 * closed (no message of it can arrive after that), and every call of it the relay delivered has been answered (the
 * office frees a call's slot before it answers it).
 */
async function closeWindow(page: Page) {
  const ws = socketOf.get(page)!;
  expect(closed, 'the relay is still holding reads').toBe(false);
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
  seatPreferenceSaves = [];
  closed = false;
  held = [];
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

async function openWindow(
  view: { width: number; height: number } = VIEW,
  deviceScaleFactor = 1,
): Promise<Page> {
  expectConnection();
  const page = await browser!.newPage({ viewport: view, deviceScaleFactor });
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
  // The reference of THE request now open, as the exact references state it. They sit in one closed disclosure, so
  // they are read from the document's text, not from what is currently drawn.
  const source = await page.$eval(T('di-mr-proposal-source'), (el) => el.textContent ?? '');
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
  // The wait, its condition and its timeout are unchanged; a timeout now also says what the page and the harness show (as the two waits above do).
  try {
    await waitFor('the start to be recorded', async () => !(await exists(page, T('di-mr-dispatch'))));
  } catch (e) {
    throw new Error(`${String(e).split('\n')[0]} -- ${await whatHappened(page)}`, { cause: e });
  }
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
    // The workspace says it in ONE compact context area: the short statement is visible, and the full wording is one disclosure away.
    expect(await text(page, T('di-mr-fixture-banner'))).toMatch(
      /synthetic; no approval given here is real/,
    );
    expect(
      await page.$eval(T('di-mr-fixture-banner'), (el) => el.textContent ?? ''),
      'the full fixture wording is not one disclosure away',
    ).toMatch(/Nothing here is a real request awaiting a real decision/);
    // the context area is persistent: it holds the fixture row and the progress row, and offers no way to dismiss either
    expect(await exists(page, T('di-mr-context'))).toBe(true);
    expect(
      await page.$$eval(`${T('di-mr-context')} button`, (els) => els.length),
      'a dismiss control inside the context area',
    ).toBe(0);
    expect(await page.$$eval(`${T('di-mr-context')} [data-testid]`, (els) => els.map((e) => e.getAttribute('data-testid')))).toEqual(
      expect.arrayContaining(['di-mr-fixture-banner', 'di-mr-progression']),
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
    // The first layer of the open request reads: its plain title, its status in words, its next step. Every exact
    // reference (the request, "of record", Mission Core, the revision, the digest) is in ONE labelled disclosure.
    const heading = await text(page, T('di-mr-detail-heading'));
    expect(heading).not.toMatch(/lr-[0-9a-f]{32}/);
    const objectiveLine = (await text(page, T('di-mr-proposal-objective')))
      .split(/\r?\n/)
      .find((l) => l.trim() !== '')!
      .trim();
    expect(objectiveLine.startsWith(heading.replace(/\u2026$/, ''))).toBe(true);
    expect(await text(page, T('di-mr-stage-label'))).toBe('Waiting for your decision');
    const stageWords = await text(page, T('di-mr-stage-detail'));
    expect(stageWords).toMatch(/Nothing has been approved and nothing has started\./);
    expect(stageWords).toMatch(/typing the word approve/);
    expect(stageWords).not.toMatch(/Mission Core|digest|revision|of record/);
    expect(await text(page, T('di-mr-refs'))).toBe('Exact references (technical)');
    const refsText = await page.$eval(T('di-mr-refs'), (el) => el.textContent ?? '');
    expect(refsText).toContain(ref);
    expect(refsText).toMatch(
      /This proposal is of record: Mission Core holds it as revision 1 with digest [0-9a-f]+\./,
    );
    expect(refsText).toMatch(/Read from the record/);
    expect(refsText).toMatch(/Mission mn-[0-9a-f]+/);
    expect(await page.locator(T('di-mr-proposal-source')).count()).toBe(1);
    expect(await page.locator(`${T('di-mr-proposal')} ${T('di-mr-proposal-source')}`).count()).toBe(0);
    // the proposal's section is plainly named, and its raw keys come after their plain words, muted, never dropped
    expect(await page.locator('section[aria-label="The proposal"]').count()).toBe(1);
    expect(await page.locator('section[aria-label="The proposal of record"]').count()).toBe(0);
    const scopeKeys = await page.$$eval(`${T('di-mr-proposal')} li .di-mr-key`, (els) =>
      els.map((e) => e.textContent),
    );
    expect(scopeKeys).toEqual(expect.arrayContaining(['(engineering_change)', '(repository_read)']));
    const ruleKeys = await page.$$eval(`${T('di-mr-proposal-rules')} ${T('di-mr-key')}`, (els) =>
      els.map((e) => e.textContent),
    );
    expect(ruleKeys.join(' ')).toMatch(/run_result/);
    expect(ruleKeys.join(' ')).toMatch(/VERIFICATION_RECORD/);
    expect(
      await page.$eval(`${T('di-mr-proposal-rules')} ${T('di-mr-key')}`, (el) => getComputedStyle(el).color !== getComputedStyle(el.parentElement!).color),
      'a raw key is drawn secondary (a different, muted colour)',
    ).toBe(true);
    // The decision section still states exactly what approving binds to.
    expect(await text(page, T('di-mr-approval-binding'))).toMatch(
      /^You are approving revision 1 of this proposal \(digest [0-9a-f]{12}\u2026\)\.$/,
    );
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
    // The identity-keyed seat save is sent ONCE, on the first real change (measured: 668 ms after the window opened, one identity, 2.2 s before the
    // relay was held; a later reconcile that changes nothing sends nothing — `sameSeatPrefs`). A second send would be churn.
    expect(seatPreferenceSaves.length, 'the identity-keyed seat save is sent once, on the first real change').toBe(1);
    // Its payload is bounded and PREFERENCE-ONLY: the closed {v, entries} shape within the contract's bounds, each entry exactly an identity tuple and a seat id.
    for (const save of seatPreferenceSaves) {
      const payload = save.payload as { v: unknown; entries: Array<Record<string, unknown>> };
      expect(Object.keys(payload).sort()).toEqual(['entries', 'v']);
      expect(checkDiSeatPreferences(payload)).toBeNull();
      expect(payload.entries.length).toBeGreaterThanOrEqual(1);
      for (const entry of payload.entries) {
        expect(Object.keys(entry).sort()).toEqual(['i', 's']);
        expect(Array.isArray(entry.i) && (entry.i as unknown[]).length === 5).toBe(true);
        expect(typeof entry.s).toBe('string');
      }
    }
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

    // — one decision from two windows. The second has the word typed on the offer it was shown,
    // so its own refresh can read the first window's approval without taking that offer off the
    // screen. The second then submits that same offer. The record keeps one authorization, and the
    // second submit is an idempotent find of it. —
    await second.fill(T('di-mr-word'), 'approve');
    await waitFor('Approve to be available in the second window', () =>
      available(second, T('di-mr-approve')),
    );
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
    // The word was typed before the first window decided. The controls stay after the record moves,
    // and the second window submits the offer it already holds.
    expect(
      await exists(second, T('di-mr-word')),
      'the second window lost the offer it had already typed',
    ).toBe(true);
    const decides = () => sent.filter((m) => m.op === 'decide').length;
    const before = decides();
    await pressKey(second, T('di-mr-approve'), 'Enter');
    const secondDecisions = await waitFor(
      'the owner to answer the second window’s decision',
      async () => {
        const got = decisionsFrom(second);
        return got.length > 0 && got.every((e) => e.reply !== null) ? got : false;
      },
    );
    expect(decides(), 'the second window submitted a decision of its own').toBe(before + 1);
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

    // Wider reading with LITTLE room beside the sidebar is never worse than ordinary reading at the same width (it once left the open
    // request a 23px sliver, scrolled the pane sideways and clipped the title behind "Narrower reading"): the list stacks above the
    // request, nothing overflows, the title is whole. Only where the pane really is wider than the ordinary pane do the two columns return.
    // BEFORE (reserve 240): 1020, 1100 and 1309 px were the panes 360 (the narrowest that docks beside), 440 (wider, still stacked) and 649 (split in two columns).
    // AFTER (reserve MR_WORLD_KEEP_PX = 276): the SAME three panes are 1056, 1136 and 1345 px, derived as the sidebar + the reserve + the pane. The assertions below are unchanged.
    for (const [width, split] of [
      [DI_PANEL_WIDTH_PX + MR_WORLD_KEEP_PX + 360, false],
      [DI_PANEL_WIDTH_PX + MR_WORLD_KEEP_PX + 440, false],
      [DI_PANEL_WIDTH_PX + MR_WORLD_KEEP_PX + 649, true],
    ] as const) {
      await page.setViewportSize({ width, height: 900 });
      await waitFor(
        `the pane to settle at ${width}px`,
        async () => (await page.getAttribute(T('di-mr'), 'data-split')) === String(split),
      );
      const now = await page.evaluate(() => {
        const q = (id: string) => document.querySelector(`[data-testid="${id}"]`) as HTMLElement;
        const body = q('di-mr-body');
        const heading = q('di-mr-heading');
        const grid = document.querySelector('.di-mr-requests') as HTMLElement;
        return {
          covering: q('di-mr').getAttribute('data-covering'),
          wide: q('di-mr').getAttribute('data-wide'),
          columns: getComputedStyle(grid).gridTemplateColumns.split(' ').length,
          bodyOverflow: body.scrollWidth - body.clientWidth,
          titleClipped: heading.scrollWidth > heading.clientWidth + 1,
          detailWidth: Math.round(q('di-mr-detail').getBoundingClientRect().width),
        };
      });
      expect(now.covering, `${width}px is beside the sidebar`).toBe('false');
      expect(now.wide, `wider reading stays chosen at ${width}px`).toBe('true');
      expect(now.columns, `${width}px: list/detail columns`).toBe(split ? 2 : 1);
      expect(now.bodyOverflow, `${width}px: the pane scrolls sideways`).toBe(0);
      expect(now.titleClipped, `${width}px: the title is clipped`).toBe(false);
      expect(now.detailWidth, `${width}px: the open request is a sliver`).toBeGreaterThanOrEqual(280);
    }
    await page.setViewportSize(VIEW);

    // narrow: the workspace covers the window, says so in words, and carries its own FIXTURE label
    await page.setViewportSize({ width: 560, height: 900 });
    await page.waitForSelector(T('di-mr-covering-note'));
    expect(await page.getAttribute(T('di-mr'), 'data-covering')).toBe('true');
    // The floating banner is hidden under the covering workspace (which carries its own FIXTURE label), or it is not rendered at all
    // because the world beside the open office has no room for it, in which case the same words are in the panel instead.
    if ((await page.$(T('di-fixture-banner'))) !== null)
      expect(await page.$eval(T('di-fixture-banner'), (el) => getComputedStyle(el).visibility)).toBe(
        'hidden',
      );
    else
      expect(
        await exists(page, '[data-testid="di-fixture-banner-in-panel"]'),
        'the floating banner is not rendered here: its words are in the panel',
      ).toBe(true);
    expect(await text(page, T('di-mr-fixture-banner'))).toMatch(
      /synthetic; no approval given here is real/,
    );
    expect(await page.$eval(T('di-mr-fixture-banner'), (el) => el.textContent ?? '')).toMatch(
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

    // Hiding the sidebar while the workspace is open takes its opener away. Escape then returns focus to the control that shows the
    // sidebar again (visible, and named for what it does), never to <body>.
    await page.click('[data-testid="di-office-panel-minimize"]');
    await page.waitForSelector(T('di-office-panel-collapsed'));
    await page.focus(T('di-mr-heading'));
    await page.keyboard.press('Escape');
    await page.waitForSelector(T('di-mr'), { state: 'detached' });
    expect(
      await page.evaluate(
        () => document.activeElement?.getAttribute('data-testid') ?? document.activeElement?.tagName,
      ),
      'after Escape with the sidebar hidden, focus is lost',
    ).toBe('di-office-panel-collapsed');
  },
  STEPS_MS,
);

// ── AC3: minimize and restore keep the workspace as it was (brief 6.3: durable coverage) ─────────────────────


test(
  'G2. THE RESERVE: with the office open the workspace covers below the panel + 276 + 360 px (1064 at the measured 428) and docks a 360 px pane beside from there, in a FRESH window at each width (no resize, so there is no earlier state to mistake for the answer)',
  async () => {
    // THE RACE IS REMOVED BY CONSTRUCTION, NOT DETECTED. Each width gets its own freshly opened window of exactly that size: the page loads at the width and the layout's first render
    // takes it from the window, so there is never a previous width whose answer a wait could accept. (The same boundary stepped by resizing one window needed a stale-state handshake
    // that two attempts got wrong: waiting for the expected value accepted the previous step's value, rendered pane geometry is already true before the app re-renders, and the
    // frame count and innerWidth ideas were rejected as a timer and as the value the test had just set.) G keeps its own responsive resizing loop, which tests something different.
    // THE BOUNDARY: the RESERVE (276) and the pane (360) PINNED BY LITERALS, independent of the constants; the PANEL term DERIVES from DI_PANEL_WIDTH_PX
    // (pass C: it was the literal 420, and the panel is now 428): panel + 276 + 360. At the old reserve of 240 the workspace docked beside from
    // panel + 240 + 360, so from there to the boundary it now covers.
    const SIDE = DI_PANEL_WIDTH_PX;
    for (const [width, covering] of [
      [SIDE + 240 + 360 - 1, true],
      [SIDE + 240 + 360, true],
      [SIDE + 276 + 360 - 1, true],
      [SIDE + 276 + 360, false],
    ] as const) {
      const page = await openWindow({ width, height: 900 });
      await openWorkspace(page);
      expect(
        await page.getAttribute(T('di-mr'), 'data-covering'),
        `${width}px: with the office open the workspace ${covering ? 'covers the window' : 'docks beside the office'}`,
      ).toBe(String(covering));
      if (!covering)
        expect(
          await page.$eval(T('di-mr'), (el) => Math.round(el.getBoundingClientRect().width)),
          `${width}px: the docked pane is the narrowest beside pane`,
        ).toBe(360);
      // an idle window keeps polling the one shared office, and the office admits only a few calls at once: close and drain this window before the next one opens
      await closeWindow(page);
    }
  },
  STEPS_MS,
);

test(
  'minimize keeps the request workspace as it was and restore gives it back',
  async () => {
    const page = await openWindow();
    await openWorkspace(page);
    // AC7: no em or en dash in what the Ask tab renders with a conversation on it.
    await ask(page, 'make the readiness probe stable', 'The readiness probe.');
    // (AC7 covers the copy WE author; what a person or the assistant wrote is content, kept as received.)
    const askCopy = await renderedCopy(page, T('di-mr'));
    console.log(
      `[AC7 request workspace, Ask] content lines with a dash (kept as received): ${askCopy.content.length}`,
    );
    expect(
      askCopy.authored,
      'em or en dash in the authored copy of the request workspace (Ask, with a conversation)',
    ).toEqual([]);
    const ref = await submitDraft(page);
    await page.waitForSelector(T('di-mr-offer-expiry'), { timeout: 30_000 });
    // ...and in what the Requests tab renders with an open request and its decision section.
    const requestCopy = await renderedCopy(page, T('di-mr'));
    console.log(
      `[AC7 request workspace, Requests] content lines with a dash (kept as received): ${requestCopy.content.length}`,
    );
    expect(
      requestCopy.authored,
      'em or en dash in the authored copy of the request workspace (Requests, an open request and its decision)',
    ).toEqual([]);

    // What a person leaves behind: a DISTINCT state in every kind of place the workspace keeps things.
    const kept = async () =>
      page.evaluate(() => {
        const q = (id: string) => document.querySelector<HTMLElement>(`[data-testid="${id}"]`);
        return {
          shown: (q('di-mr')?.getClientRects().length ?? 0) > 0,
          inDom: q('di-mr') !== null,
          scroll: q('di-mr-body')?.scrollTop ?? null,
          tab: q('di-mr-tab-requests')?.getAttribute('aria-selected') ?? null,
          wide: q('di-mr')?.getAttribute('data-wide') ?? null,
          request: q('di-mr-proposal-source')?.textContent ?? null,
          word: (q('di-mr-word') as HTMLInputElement | null)?.value ?? null,
          fixtureNotice: (q('di-mr-fixture-details') as HTMLDetailsElement | null)?.open ?? null,
          references: (q('di-mr-refs') as HTMLDetailsElement | null)?.open ?? null,
          focus:
            document.activeElement?.getAttribute('data-testid') ??
            document.activeElement?.tagName ??
            null,
        };
      });
    const WORD = 'appr'; // typed, and not yet the decision word: nothing is approved
    const SCROLL = 137;
    /**
     * The real keyboard path. The person's last working control is "Wider reading" (they switch it on with Enter),
     * the control right before Minimize in the tab order; Tab moves to Minimize and Enter minimizes. Focus at hide is
     * therefore the toggle, and the control they were working in is "Wider reading". (From deeper in the workspace,
     * Tab passes every control on the way, and the last one passed is what focus returns to: diMinimize.ts.)
     * Disclosures are opened and the body set to a distinct position once focus is on the toggle (moving focus
     * through the workspace scrolls it, so the position is set last and not by the person's tabbing).
     */
    const leaveAndMinimize = async (switchWide: boolean) => {
      await page.focus(T('di-mr-wide'));
      if (switchWide) await page.keyboard.press('Enter');
      await page.keyboard.press('Tab');
      expect(
        await page.evaluate(() => document.activeElement?.getAttribute('data-testid')),
        'Tab from "Wider reading" reaches Minimize',
      ).toBe('di-mr-minimize');
      await page.$eval(
        T('di-mr-fixture-details'),
        (el) => ((el as HTMLDetailsElement).open = true),
      );
      await page.$eval(T('di-mr-refs'), (el) => ((el as HTMLDetailsElement).open = true));
      const scrolled = await page.$eval(
        T('di-mr-body'),
        (el, to) => {
          el.scrollTop = to;
          return el.scrollTop;
        },
        SCROLL,
      );
      expect(scrolled, 'the open request is tall enough to scroll to a distinct position').toBe(
        SCROLL,
      );
      const atHide = await kept();
      expect(atHide).toMatchObject({
        shown: true,
        scroll: SCROLL,
        tab: 'true',
        wide: 'true',
        word: WORD,
        fixtureNotice: true,
        references: true,
        focus: 'di-mr-minimize',
      });
      expect(atHide.request).toContain(ref);
      await page.keyboard.press('Enter');
      await page.waitForSelector(T('di-mr-restore'), { timeout: 5_000 });
      return atHide;
    };
    /** Restored exactly as left, with focus back on the control the person was working in (never the toggle or the page). */
    const asLeft = (atHide: Awaited<ReturnType<typeof kept>>) => ({
      ...atHide,
      shown: true,
      inDom: true,
      focus: 'di-mr-wide',
    });

    await page.fill(T('di-mr-word'), WORD);
    expect(await exists(page, T('di-mr-minimize')), 'the workspace offers Minimize').toBe(true);
    const atHide = await leaveAndMinimize(true);
    const minimized = await kept();
    expect(minimized.inDom, 'minimized, the workspace is kept in the page').toBe(true);
    expect(minimized.shown, 'minimized, the workspace is hidden').toBe(false);
    expect(
      minimized.focus,
      'minimized, focus is on the control that restores it (never the page)',
    ).toBe('di-mr-restore');
    // Its words (the ▲ beside them is a shape, hidden from assistive technology) and its name, which carries them.
    const restoreWords = await page.$eval(T('di-mr-restore'), (el) =>
      [...el.childNodes]
        .filter((n) => !(n instanceof Element && n.getAttribute('aria-hidden') === 'true'))
        .map((n) => n.textContent ?? '')
        .join('')
        .trim(),
    );
    expect(restoreWords).toBe('Restore requests');
    // The open proposal waits for the owner's decision: AC4, a glow AND a shape AND words, on the restore control too.
    expect(await page.getAttribute(T('di-mr-restore'), 'aria-label')).toBe(
      'Restore requests, 1 request needs you',
    );
    expect(await page.getAttribute(T('di-mr-restore'), 'data-attention')).toBe('true');
    expect(await page.$eval(T('di-mr-restore'), (el) => getComputedStyle(el).boxShadow)).not.toBe(
      'none',
    );
    expect(
      await page.$eval(
        T('di-mr-restore'),
        (el) => el.querySelector('[aria-hidden="true"]')?.textContent,
      ),
    ).toBe('▲');
    expect(
      (await renderedCopy(page, T('di-mr-restore'))).authored,
      'em or en dash on the minimized restore control',
    ).toEqual([]);
    // The world and the office panel are usable while it is minimized.
    await page.fill(T('di-find-input'), 'lead');
    expect(
      await page.evaluate(() => [
        document.activeElement?.getAttribute('data-testid'),
        (document.activeElement as HTMLInputElement | null)?.value,
      ]),
    ).toEqual(['di-find-input', 'lead']);
    await page.fill(T('di-find-input'), '');

    // Restore, by keyboard, from the restore control.
    await pressKey(page, T('di-mr-restore'), 'Enter');
    await page.waitForSelector(T('di-mr-restore'), { state: 'detached', timeout: 5_000 });
    expect(await kept(), 'restore gives back the workspace exactly as it was left').toEqual(
      asLeft(atHide),
    );

    // An entrance used while minimized restores the same workspace too.
    const again = await leaveAndMinimize(false);
    await page.click(T('di-mr-open-requests'));
    await page.waitForSelector(T('di-mr-restore'), { state: 'detached', timeout: 5_000 });
    expect(await kept(), 'the entrance restores the workspace as it was left').toEqual(
      asLeft(again),
    );

    // Escape still closes the restored workspace; nothing was decided along the way.
    await page.keyboard.press('Escape');
    await page.waitForSelector(T('di-mr'), { state: 'detached', timeout: 5_000 });
    expect(decisionsFrom(page), 'minimize and restore sent a decision').toEqual([]);
  },
  STEPS_MS,
);

test(
  'AC3 request panel, mouse: restore puts focus back in the text field the person typed into, with the text intact',
  async () => {
    const page = await openWindow();
    await openWorkspace(page);
    const where = () =>
      page.evaluate(() => ({
        focus:
          document.activeElement?.getAttribute('data-testid') ??
          document.activeElement?.tagName ??
          null,
        value:
          (document.activeElement as HTMLInputElement | HTMLTextAreaElement | null)?.value ?? null,
      }));
    /** Mouse only: click into the field, type, click Minimize, click Restore; focus comes back to the field. */
    const mouseRoundTrip = async (field: string, typed: string) => {
      await page.click(T(field));
      await page.keyboard.type(typed);
      expect(await where()).toEqual({ focus: field, value: typed });
      await page.click(T('di-mr-minimize'));
      await page.waitForSelector(T('di-mr-restore'), { timeout: 5_000 });
      expect(
        await page.$eval(T('di-mr'), (el) => el.getClientRects().length),
        'minimized, the workspace is hidden',
      ).toBe(0);
      await page.click(T('di-mr-restore'));
      await page.waitForSelector(T('di-mr-restore'), { state: 'detached', timeout: 5_000 });
      const back = await where();
      expect(back.focus, 'restore never leaves focus on Minimize').not.toBe('di-mr-minimize');
      expect(back.focus, 'restore never leaves focus on the page').not.toBe('BODY');
      expect(back, 'restore puts focus back in the field typed into, its text intact').toEqual({
        focus: field,
        value: typed,
      });
    };

    // The Ask tab's composer.
    await mouseRoundTrip('di-mr-composer', 'kept by the mouse 4f2b: make the probe steady');
    // The decision field of an open request (typed, not the decision word: nothing is approved).
    await page.fill(T('di-mr-composer'), '');
    await ask(page, 'make the readiness probe stable', 'The readiness probe.');
    await submitDraft(page);
    await page.waitForSelector(T('di-mr-offer-expiry'), { timeout: 30_000 });
    await mouseRoundTrip('di-mr-word', 'appro');
    expect(decisionsFrom(page), 'minimize and restore sent a decision').toEqual([]);
  },
  STEPS_MS,
);

test(
  'AC3 request panel fallbacks: with no content control to return to, restore puts focus on Minimize, never the page',
  async () => {
    const page = await openWindow();
    const focusNow = () =>
      page.evaluate(
        () =>
          document.activeElement?.getAttribute('data-testid') ??
          document.activeElement?.tagName ??
          null,
      );

    // (1) ABSENT: minimized by mouse before any content control had focus (opening focuses the workspace's heading,
    // which is the panel's own focus target, not a control the person used). The first fallback is Minimize.
    await openWorkspace(page);
    await page.click(T('di-mr-minimize'));
    await page.waitForSelector(T('di-mr-restore'), { timeout: 5_000 });
    await page.click(T('di-mr-restore'));
    await page.waitForSelector(T('di-mr-restore'), { state: 'detached', timeout: 5_000 });
    const absent = await focusNow();
    expect(absent, 'absent: focus never falls to the page').not.toBe('BODY');
    expect(absent, 'absent: focus is never left on the gone restore control').not.toBe(
      'di-mr-restore',
    );
    expect(absent, 'absent: the first fallback, the panel’s Minimize').toBe('di-mr-minimize');

    // (2) REMOVED: the person typed in the composer, minimized, then used the "Review requests" entrance, which restores
    // the workspace on the Requests tab: the composer is gone, so focus takes the first fallback.
    await page.click(T('di-mr-composer'));
    await page.keyboard.type('removed by the tab change');
    await page.click(T('di-mr-minimize'));
    await page.waitForSelector(T('di-mr-restore'), { timeout: 5_000 });
    await page.click(T('di-mr-open-requests'));
    await page.waitForSelector(T('di-mr-restore'), { state: 'detached', timeout: 5_000 });
    expect(await exists(page, T('di-mr-composer')), 'the kept control really is removed').toBe(
      false,
    );
    const removed = await focusNow();
    expect(removed, 'removed: focus never falls to the page').not.toBe('BODY');
    expect(removed, 'removed: the first fallback, the panel’s Minimize').toBe('di-mr-minimize');

    // Not reachable through this flow without new product code, so not forced here (and not covered by this test): a
    // kept control that becomes DISABLED or HIDDEN while the workspace is minimized (the decision controls stay mounted
    // and use aria-disabled, and nothing hides a control while the workspace is hidden), and Minimize itself being
    // unable to take focus (the second fallback, the heading).
    expect(decisionsFrom(page), 'minimize and restore sent a decision').toEqual([]);
  },
  STEPS_MS,
);

test(
  'AC3 cross-surface, request panel and Reception: both minimized at once, a restore inside another modal is impossible, inert and focus stay right',
  async () => {
    const page = await openWindow();
    const CHIPS = ['di-mr-restore', 'di-reception-restore'] as const;
    // What the person sees of a control (text that is shown and not aria-hidden) and what it is called for assistive technology.
    const chipFacts = (id: string) =>
      page.evaluate((testid) => {
        const el = document.querySelector<HTMLElement>(`[data-testid="${testid}"]`);
        const tray = document.querySelector<HTMLElement>('[data-testid="di-minimized-tray"]');
        if (!el) return null;
        const parts: string[] = [];
        const walker = document.createTreeWalker(el, NodeFilter.SHOW_TEXT);
        for (let n = walker.nextNode(); n; n = walker.nextNode()) {
          const p = n.parentElement;
          if (!p || p.closest('[aria-hidden="true"]')) continue;
          if (!p.checkVisibility({ checkVisibilityCSS: true })) continue;
          parts.push(n.textContent ?? '');
        }
        const r = el.getBoundingClientRect();
        const t = tray?.getBoundingClientRect() ?? null;
        return {
          visible: parts.join('').replace(/\s+/g, ' ').trim(),
          name: el.getAttribute('aria-label'),
          inTray: tray?.contains(el) ?? false,
          rect: { left: r.left, top: r.top, right: r.right, bottom: r.bottom },
          tray: t && { left: t.left, top: t.top, right: t.right, bottom: t.bottom },
          inert: el.closest('[inert]') !== null,
        };
      }, id);
    const rootInert = () =>
      page.evaluate(() => document.getElementById('root')!.hasAttribute('inert'));
    /** Try to move focus to the control: an inert or hidden control cannot take it. */
    const canTakeFocus = (id: string) =>
      page.evaluate((testid) => {
        const el = document.querySelector<HTMLElement>(`[data-testid="${testid}"]`)!;
        el.focus();
        return document.activeElement === el;
      }, id);
    const focusNow = () =>
      page.evaluate(
        () =>
          document.activeElement?.getAttribute('data-testid') ??
          document.activeElement?.tagName ??
          null,
      );
    const insideDialog = (dialog: string) =>
      page.evaluate((testid) => {
        const d = document.querySelector(`[data-testid="${testid}"]`);
        return d !== null && d.contains(document.activeElement);
      }, dialog);

    // 1. The request workspace, minimized alone: a slot in the strip.
    await openWorkspace(page);
    const WORDS = 'cross-surface 7c1e: kept';
    await page.click(T('di-mr-composer'));
    await page.keyboard.type(WORDS);
    await page.click(T('di-mr-minimize'));
    await page.waitForSelector(T('di-mr-restore'), { timeout: 5_000 });
    await shot(page, 'minimized-request-strip');
    // ...and the same strip in a narrow window (resized for this one view, then set back).
    await page.setViewportSize({ width: 640, height: 800 });
    await page.waitForSelector(T('di-mr-restore'), { timeout: 5_000 });
    await shot(page, 'minimized-request-narrow-640');
    await page.setViewportSize(VIEW);

    // 2. Reception, minimized by mouse too: both surfaces minimized at once.
    await page.click(T('di-open-reception'));
    await page.waitForSelector(T('di-reception-inbox'), { timeout: 15_000 });
    expect(await rootInert(), 'Reception open: the app behind it is inert (it is a modal)').toBe(
      true,
    );
    await page.click(T('di-reception-minimize'));
    await page.waitForSelector(T('di-reception-restore'), { timeout: 5_000 });
    await shot(page, 'minimized-two-slots-request-reception');

    const request = await chipFacts('di-mr-restore');
    const reception = await chipFacts('di-reception-restore');
    expect(request?.inTray, 'the request chip is in the minimized tray').toBe(true);
    expect(reception?.inTray, 'the Reception chip is in the minimized tray').toBe(true);
    for (const [who, c] of [
      ['request', request],
      ['Reception', reception],
    ] as const) {
      expect(c!.visible, `${who}: a visible label`).toMatch(/^Restore (requests|reception)$/);
      expect(c!.name, `${who}: the accessible name contains the visible label`).toContain(
        c!.visible,
      );
      expect(c!.tray, `${who}: the tray has a size`).not.toBeNull();
      const t = c!.tray!;
      expect(
        c!.rect.left >= t.left - 1 &&
          c!.rect.right <= t.right + 1 &&
          c!.rect.top >= t.top - 1 &&
          c!.rect.bottom <= t.bottom + 1,
        `${who}: the slot lies inside the tray ${JSON.stringify([c!.rect, t])}`,
      ).toBe(true);
    }
    // Two slots, stacked down the strip: one above the other, overlapping on neither axis.
    const [a, b] = [request!.rect, reception!.rect];
    expect(
      a.bottom <= b.top || b.bottom <= a.top,
      `the two slots are stacked and do not overlap ${JSON.stringify([a, b])}`,
    ).toBe(true);
    expect(await rootInert(), 'both minimized: nothing is modal, the app is not inert').toBe(false);
    for (const id of CHIPS)
      expect(await canTakeFocus(id), `${id} can take focus with no modal`).toBe(true);
    await page.fill(T('di-find-input'), 'lead');
    expect(
      await page.evaluate(() => [
        document.activeElement?.getAttribute('data-testid'),
        (document.activeElement as HTMLInputElement | null)?.value,
      ]),
      'the office panel takes typing while both are minimized',
    ).toEqual(['di-find-input', 'lead']);
    await page.fill(T('di-find-input'), '');

    // 3. Another modal in front: the Mission details dialog. Neither strip slot can be used.
    await page.click(T('di-open-inspector-mission'));
    await page.waitForSelector(T('di-inspector'), { timeout: 15_000 });
    await shot(page, 'minimized-strip-inert-under-details-request-app');
    expect(await rootInert(), 'details open: the app (and the strip) is inert').toBe(true);
    for (const id of CHIPS) {
      const facts = await chipFacts(id);
      expect(facts!.inert, `${id} is inert under the details dialog`).toBe(true);
      expect(await canTakeFocus(id), `${id} cannot take focus under the details dialog`).toBe(
        false,
      );
      expect(await insideDialog('di-inspector'), `${id}: focus stayed in the dialog`).toBe(true);
    }
    const visited = new Set<string | null>();
    for (let i = 0; i < 25; i++) {
      await page.keyboard.press('Tab');
      expect(
        await insideDialog('di-inspector'),
        `Tab press ${i + 1} stays inside the details dialog`,
      ).toBe(true);
      visited.add(await focusNow());
    }
    expect(visited.size, 'Tab really moved through the dialog (it is not stuck)').toBeGreaterThan(
      1,
    );
    await page.keyboard.press('Escape');
    await page.waitForSelector(T('di-inspector'), { state: 'detached', timeout: 5_000 });
    expect(await focusNow(), 'Escape: focus is back on the control that opened the dialog').toBe(
      'di-open-inspector-mission',
    );
    expect(await rootInert(), 'details closed: nothing is inert').toBe(false);
    for (const id of CHIPS) {
      expect((await chipFacts(id))!.inert, `${id} is usable again`).toBe(false);
      expect(await canTakeFocus(id), `${id} takes focus again`).toBe(true);
    }

    // 4. Restore the request workspace: it is a docked pane, NOT a modal (it never claims the modal stack), so the
    // app is not made inert and the Reception slot stays in the strip.
    await page.click(T('di-mr-restore'));
    await page.waitForSelector(T('di-mr-restore'), { state: 'detached', timeout: 5_000 });
    expect(
      await page.$eval(T('di-mr'), (el) => el.getClientRects().length > 0),
      'the request workspace is shown again',
    ).toBe(true);
    expect(await page.$eval(T('di-mr-composer'), (el) => (el as HTMLTextAreaElement).value)).toBe(
      WORDS,
    );
    expect(await rootInert(), 'the request workspace is non-modal: the app is not inert').toBe(
      false,
    );
    expect(
      (await chipFacts('di-reception-restore'))?.inTray,
      'the Reception slot is still in the strip',
    ).toBe(true);
    await shot(page, 'restored-request-after-strip');

    // 5. Minimize it again, then restore Reception: it is a modal, so the app is inert and the request slot is not usable.
    await page.click(T('di-mr-minimize'));
    await page.waitForSelector(T('di-mr-restore'), { timeout: 5_000 });
    await page.click(T('di-reception-restore'));
    await page.waitForSelector(T('di-reception-restore'), { state: 'detached', timeout: 5_000 });
    expect(await rootInert(), 'Reception restored: the app is inert again').toBe(true);
    expect(await insideDialog('di-reception-inbox'), 'Reception restored: focus is inside it').toBe(
      true,
    );
    expect(await focusNow(), 'Reception restored: focus is on a named control, not the page').toBe(
      'di-reception-minimize',
    );
    expect(
      (await chipFacts('di-mr-restore'))!.inert,
      'the request slot is inert under Reception',
    ).toBe(true);
    expect(
      await canTakeFocus('di-mr-restore'),
      'the request slot cannot take focus under Reception',
    ).toBe(false);
    expect(await insideDialog('di-reception-inbox'), 'focus stayed in Reception').toBe(true);
    await page.keyboard.press('Escape');
    await page.waitForSelector(T('di-reception-inbox'), { state: 'detached', timeout: 5_000 });
    expect(await rootInert(), 'Reception closed: nothing is inert').toBe(false);
    expect(await focusNow(), 'Reception closed: focus is back on the control that opened it').toBe(
      'di-open-reception',
    );
    expect(await canTakeFocus('di-mr-restore'), 'the request slot takes focus again').toBe(true);

    // Nothing was sent that acts: reads only (refresh, conversation, request, offer, result).
    const acting = new Set(['turn', 'submit', 'decide', 'checkApproval', 'withdraw', 'run']);
    expect(
      relayed
        .filter((e) => e.ws === socketOf.get(page) && acting.has(String(e.msg.op)))
        .map((e) => e.msg.op),
      'minimizing and restoring sent something that acts',
    ).toEqual([]);
    expect(decisionsFrom(page), 'a decision was sent').toEqual([]);
  },
  STEPS_MS,
);

test(
  'AC6 sweep, request panel: names, label in name, state words, motion and target size, with and without Reduce Motion',
  async () => {
    const page = await openWindow();
    await openWorkspace(page);
    // An open request with its decision section, so the Requests tab and the owner-attention marks are real.
    await ask(page, 'make the readiness probe stable', 'The readiness probe.');
    await submitDraft(page);
    await page.waitForSelector(T('di-mr-offer-expiry'), { timeout: 30_000 });

    const MIN_TARGET = 24; // WCAG 2.5.8, CSS px
    const small = new Map<string, string>(); // pre-existing controls under the minimum, listed once, never asserted on
    const sweepChecks = (sweep: A11ySweep, where: string, isNew: (id: string) => boolean): void => {
      const ids = (pick: (c: A11ySweep['controls'][number]) => boolean) =>
        sweep.controls.filter(pick).map((c) => c.id);
      expect(sweep.controls.length, `${where}: the sweep found controls`).toBeGreaterThan(0);
      expect(
        ids((c) => c.source === 'none'),
        `${where}: controls with no accessible name`,
      ).toEqual([]);
      expect(
        ids((c) => c.source === 'title' || c.source === 'placeholder'),
        `${where}: controls named only by a title or a placeholder`,
      ).toEqual([]);
      expect(
        ids((c) => c.labelInName === false),
        `${where}: controls whose accessible name does not contain their visible text`,
      ).toEqual([]);
      expect(
        sweep.stateMarks.filter((m) => m.words === '').map((m) => m.id),
        `${where}: state marks that say nothing in words (colour alone)`,
      ).toEqual([]);
      expect(sweep.motion, `${where}: elements with a transition or animation`).toEqual([]);
      expect(sweep.running, `${where}: running animations or transitions`).toBe(0);
      for (const c of sweep.controls) {
        const least = Math.min(c.width, c.height);
        if (isNew(c.id))
          expect(
            least,
            `${where}: ${c.id} is ${c.width} x ${c.height}, under ${MIN_TARGET} x ${MIN_TARGET}`,
          ).toBeGreaterThanOrEqual(MIN_TARGET);
        else if (least < MIN_TARGET) small.set(c.id, `${c.width} x ${c.height}`);
      }
    };
    const workspace = async (where: string) => {
      const sweep = await a11ySweep(page, T('di-mr'));
      expect(
        sweep.controls.map((c) => c.id),
        `${where}: the workspace offers Minimize`,
      ).toContain('di-mr-minimize');
      sweepChecks(sweep, where, (id) => id === 'di-mr-minimize');
    };
    const tray = async (where: string, expected: string[]) => {
      const sweep = await a11ySweep(page, T('di-minimized-tray'));
      expect(
        sweep.controls.map((c) => c.id).sort(),
        `${where}: the tray holds exactly these controls`,
      ).toEqual([...expected].sort());
      sweepChecks(sweep, where, () => true);
    };

    for (const reducedMotion of ['no-preference', 'reduce'] as const) {
      await page.emulateMedia({ reducedMotion });
      const at = (what: string) => `${reducedMotion}, ${what}`;
      await page.click(T('di-mr-tab-ask'));
      await page.waitForSelector(T('di-mr-composer'), { timeout: 5_000 });
      await workspace(at('workspace, Ask tab'));
      await page.click(T('di-mr-tab-requests'));
      await page.waitForSelector(T('di-mr-decision'), { timeout: 5_000 });
      await workspace(at('workspace, Requests tab'));

      // The tray with the request chip alone, then with Reception minimized beside it.
      await page.click(T('di-mr-minimize'));
      await page.waitForSelector(T('di-mr-restore'), { timeout: 5_000 });
      await tray(at('tray, request chip alone'), ['di-mr-restore']);
      await page.click(T('di-open-reception'));
      await page.waitForSelector(T('di-reception-inbox'), { timeout: 15_000 });
      await page.click(T('di-reception-minimize'));
      await page.waitForSelector(T('di-reception-restore'), { timeout: 5_000 });
      await tray(at('tray, request and Reception chips'), [
        'di-mr-restore',
        'di-reception-restore',
      ]);

      // Put both back for the next setting: Reception is a modal over the strip, so it is restored and closed first.
      await page.click(T('di-reception-restore'));
      await page.waitForSelector(T('di-reception-restore'), { state: 'detached', timeout: 5_000 });
      await page.keyboard.press('Escape');
      await page.waitForSelector(T('di-reception-inbox'), { state: 'detached', timeout: 5_000 });
      await page.click(T('di-mr-restore'));
      await page.waitForSelector(T('di-mr-restore'), { state: 'detached', timeout: 5_000 });
    }
    console.log(
      `[AC6 request panel] pre-existing controls under ${MIN_TARGET} x ${MIN_TARGET} (listed, not asserted): ${JSON.stringify(Object.fromEntries(small))}`,
    );
    expect(decisionsFrom(page), 'the sweep sent a decision').toEqual([]);
  },
  STEPS_MS,
);

test(
  'token scope, COMPUTED: request panel: every custom property a chrome rule consumes has a value where the rule applies',
  async () => {
    const page = await openWindow();
    const counts: Record<string, string> = {};
    let neverReached = null as Set<string> | null; // rules no rendered state matched (not audited by this test)
    /** The audit reads the RENDERED page (not the source): for every `.di-` rule, does each token it consumes compute on the elements it matches? */
    const audit = async (state: string) => {
      const a = await tokenScopeAudit(page);
      counts[state] =
        `consumingRules=${a.consumingRules} matchedRules=${a.matchedRules} pairsChecked=${a.pairsChecked} unmatched=${a.unmatched.length} gaps=${a.gaps.length}`;
      console.log(`[token scope, request panel] ${state}: ${counts[state]}`);
      const now = new Set(a.unmatched);
      neverReached =
        neverReached === null ? now : new Set([...neverReached].filter((r) => now.has(r)));
      if (a.gaps.length > 0)
        console.log(`[token scope, request panel] ${state}: GAPS ${JSON.stringify(a.gaps)}`);
      expect(
        a.gaps,
        `${state}: a consumed custom property has no computed value on an element its rule matches`,
      ).toEqual([]);
      expect(a.consumingRules, `${state}: the audit looked at the chrome rules`).toBeGreaterThan(
        20,
      );
      expect(
        a.pairsChecked,
        `${state}: the audit checked rendered element and token pairs`,
      ).toBeGreaterThan(100);
    };

    // The entrance in the office panel, with nothing waiting.
    expect(await page.getAttribute(T('di-mr-open-requests'), 'data-attention')).toBeNull();
    await audit('entrance, nothing waiting');

    // The workspace on the Ask tab, empty.
    await openWorkspace(page);
    await audit('workspace, Ask tab, empty conversation');

    // A real request with its decision: the Requests tab, and the Ask tab with a conversation on it.
    await ask(page, 'make the readiness probe stable', 'The readiness probe.');
    const ref = await submitDraft(page);
    await page.waitForSelector(T('di-mr-offer-expiry'), { timeout: 30_000 });
    expect(await exists(page, T('di-mr-decision'))).toBe(true);
    await audit('workspace, Requests tab, open request and its decision');
    await page.click(T('di-mr-tab-ask'));
    await page.waitForSelector(T('di-mr-composer'), { timeout: 5_000 });
    expect(await exists(page, T('di-mr-log'))).toBe(true);
    await audit('workspace, Ask tab, a conversation');
    await page.click(T('di-mr-tab-requests'));
    await page.waitForSelector(T('di-mr-decision'), { timeout: 5_000 });
    // A disclosure opened (the exact references), and in it the whole digest shown in full.
    await page.$eval(T('di-mr-refs'), (el) => ((el as HTMLDetailsElement).open = true));
    await page.click(T('di-mr-digest-toggle'));
    await page.waitForSelector(T('di-mr-digest-full'), { timeout: 5_000 });
    await audit('workspace, Requests tab, the whole digest shown');
    // A narrow window: the workspace covers the world and says why.
    await page.setViewportSize({ width: 599, height: 700 });
    await page.waitForSelector(`${T('di-mr')}[data-covering="true"]`, { timeout: 5_000 });
    await audit('workspace, covering a narrow window (599 x 700)');
    await page.setViewportSize(VIEW);
    await page.waitForSelector(`${T('di-mr')}[data-covering="false"]`, { timeout: 5_000 });

    // Stale: a revision that changes only the proof rules takes the decision away until it is read again.
    await harness.control({ op: 'edit_contract', request_ref: ref });
    await page.waitForSelector(T('di-mr-stale'), { timeout: 30_000 });
    expect(await exists(page, T('di-mr-decision')), 'stale: the decision is withdrawn').toBe(false);
    await audit('workspace, Requests tab, stale request (rules changed)');
    await page.click(T('di-mr-acknowledge'));
    await page.waitForSelector(T('di-mr-offer-expiry'), { timeout: 30_000 });

    // Minimized: the strip with the restore chip, which carries the owner-attention treatment (a request waits).
    await page.click(T('di-mr-minimize'));
    await page.waitForSelector(T('di-mr-restore'), { timeout: 5_000 });
    expect(await page.getAttribute(T('di-mr-restore'), 'data-attention')).toBe('true');
    await audit('minimized strip, request chip with attention');
    await page.click(T('di-mr-restore'));
    await page.waitForSelector(T('di-mr-restore'), { state: 'detached', timeout: 5_000 });

    // Closed again: the entrance with its attention glow, because something waits.
    await page.click(T('di-mr-close'));
    await page.waitForSelector(T('di-mr'), { state: 'detached', timeout: 5_000 });
    expect(await page.getAttribute(T('di-mr-open-requests'), 'data-attention')).toBe('true');
    await audit('entrance, a request waits (attention glow)');

    // Hidden content made visible: a draft whose text carries control characters.
    await page.click(T('di-mr-open'));
    await page.waitForSelector(T('di-mr'), { timeout: 15_000 });
    await ask(page, 'do the thing', 'Here <a href="javascript:alert(1)">a link</a>.', {
      objective: '<img src=x> make it stable‮reversed​zero',
      target_context: '<script>1</script>',
      requested_scope: 'no schema change\nline two <b>not bold</b>',
      requested_action_scope: ['repository_read', 'engineering_change'],
    });
    // (the first request's draft is still in the conversation, so wait for the NEW draft's control marks to be drawn)
    await page.waitForSelector(T('di-mr-control'), { state: 'attached', timeout: 30_000 });
    expect(await page.$$eval(T('di-mr-control'), (els) => els.length)).toBeGreaterThan(0);
    await audit('workspace, Ask tab, a draft with hidden characters made visible');

    expect(decisionsFrom(page), 'the audit sent a decision').toEqual([]);
    console.log(`[token scope, request panel] counts per state: ${JSON.stringify(counts)}`);
    // Informational: the request panel's own rules that NO state above rendered an element for (so none were audited).
    const unaudited = [...(neverReached ?? [])].filter((r) => r.includes('.di-mr'));
    console.log(
      `[token scope, request panel] .di-mr rules never matched in any state (${unaudited.length}): ${JSON.stringify(unaudited)}`,
    );
  },
  STEPS_MS,
);

/*
 * The strip's restore labels, RENDERED. A programmatic pass (a name, a focus order, a bounding box) is not a visual pass:
 * a label can be laid out outside its button and clipped away while every name and focus assertion holds. This measures
 * where each piece of visible text is DRAWN (the client rects of its text nodes) against the part of the button that is
 * actually visible, with the request chip alone and with Reception minimized beside it, at the sizes the owner uses
 * (1440 x 900, 900 x 700, narrow 640 x 800, and a zoom-equivalent: 720 x 450 at deviceScaleFactor 2, a scaled viewport and
 * NOT a native Command-plus test), after the layout has settled.
 *
 * This runs in the REAL App, where the strip is a fixed element inside the world's `overflow: clip` container. The
 * helper clips text only by what really clips it (the containing-block chain), so the strip is not clipped by that
 * container and the helper's `outside` is asserted directly.
 */
const STRIP_VIEWS = [
  { name: '1440 x 900', width: 1440, height: 900, scale: 1, attention: true },
  { name: '900 x 700', width: 900, height: 700, scale: 1, attention: false },
  { name: 'narrow 640 x 800', width: 640, height: 800, scale: 1, attention: false },
  {
    name: 'zoom-equivalent (720 x 450 at deviceScaleFactor 2; a scaled viewport, NOT a native Command-plus test)',
    width: 720,
    height: 450,
    scale: 2,
    attention: true,
  },
] as const;
const STRIP_LABEL: Record<string, string> = {
  'di-mr-restore': 'Restore requests',
  'di-reception-restore': 'Restore reception',
};

test(
  'AC3 strip label, request chip, RENDERED GEOMETRY: the Restore label is drawn inside its button, unclipped and readable, alone and beside Reception, at normal and narrow sizes and at the zoom-equivalent size',
  async () => {
    const measure = async (page: Page, at: string, expected: string[], waits: boolean) => {
      await page.waitForTimeout(1200); // settled, as an owner sees it
      const fit = await textFit(page, T('di-minimized-tray'));
      expect(
        fit.controls.map((c) => c.id).sort(),
        `${at}: the strip holds exactly these controls`,
      ).toEqual([...expected].sort());
      // the request chip first, then the others
      const controls = [...fit.controls].sort(
        (x, y) => Number(y.id === 'di-mr-restore') - Number(x.id === 'di-mr-restore'),
      );
      for (const c of controls) {
        const who = `${at}: ${c.id}`;
        expect(
          c.outside,
          `${who}: rendered text outside the visible, unclipped region of the button (pixels per side)`,
        ).toEqual([]);
        expect(
          c.textNotHit,
          `${who}: text whose centre the button does not paint (clipped or covered)`,
        ).toEqual([]);
        expect(c.clipsContent, `${who}: the button clips its own content`).toBe(false);
        expect(
          c.controlWritingMode,
          `${who}: the flex container keeps a horizontal writing mode (only the label is vertical)`,
        ).toBe('horizontal-tb');
        expect(c.controlOffWindow, `${who}: the button lies inside the window`).toEqual({
          left: 0,
          right: 0,
          top: 0,
          bottom: 0,
        });
        expect(c.hitsItself, `${who}: a click at its centre reaches it`).toBe(true);
        // the FULL words, never shortened to fit
        const label = c.texts.find((t) => t.text === STRIP_LABEL[c.id]);
        expect(
          label,
          `${who}: the full label "${STRIP_LABEL[c.id]}" is drawn (not shortened)`,
        ).toBeDefined();
        expect(
          label!.width >= 10 && label!.height >= 40,
          `${who}: the label is drawn with a readable box (${label!.width} x ${label!.height})`,
        ).toBe(true);
        expect(
          label!.left >= c.box.left - 0.75 && label!.right <= c.box.right + 0.75,
          `${who}: the label (${label!.left} to ${label!.right}) lies between the button's left (${c.box.left}) and right (${c.box.right}) edges`,
        ).toBe(true);
        expect(
          label!.top >= c.box.top - 0.75 && label!.bottom <= c.box.bottom + 0.75,
          `${who}: the label (${label!.top} to ${label!.bottom}) lies between the button's top (${c.box.top}) and bottom (${c.box.bottom}) edges`,
        ).toBe(true);
        if (c.id === 'di-mr-restore') {
          // the glyph is drawn when, and only when, a request waits for the owner (a shape, not colour alone)
          expect(
            c.texts.some((t) => t.text === '▲'),
            `${who}: the ▲ shape is drawn exactly when something waits (${waits})`,
          ).toBe(waits);
        }
      }
      // the slots are stacked down the strip, none on top of another
      const stacked = [...fit.controls].sort((x, y) => x.box.top - y.box.top);
      for (let i = 1; i < stacked.length; i++)
        expect(
          stacked[i - 1].box.bottom <= stacked[i].box.top + 0.75,
          `${at}: ${stacked[i - 1].id} (to ${stacked[i - 1].box.bottom}) and ${stacked[i].id} (from ${stacked[i].box.top}) are stacked, not overlapping`,
        ).toBe(true);
    };

    // One window per size and per state. Every NOTHING-WAITS window runs before any window that creates a request:
    // the adapter keeps the request, so a later window would otherwise find one already waiting for the owner.
    const cases = [
      ...STRIP_VIEWS.map((view) => ({ view, waits: false })),
      ...STRIP_VIEWS.filter((view) => view.attention).map((view) => ({ view, waits: true })),
    ];
    for (const { view, waits } of cases) {
      const page = await openWindow({ width: view.width, height: view.height }, view.scale);
      const at = `${view.name}, ${waits ? 'a request waits' : 'nothing waits'}`;
      await openWorkspace(page);
      if (waits) {
        await ask(page, 'make the readiness probe stable', 'The readiness probe.');
        await submitDraft(page);
        await page.waitForSelector(T('di-mr-offer-expiry'), { timeout: 30_000 });
      }
      await page.click(T('di-mr-minimize'));
      await page.waitForSelector(T('di-mr-restore'), { timeout: 5_000 });
      expect(
        await page.getAttribute(T('di-mr-restore'), 'data-attention'),
        `${at}: the owner-attention treatment`,
      ).toBe(waits ? 'true' : null);
      await measure(page, `${at}, request chip alone`, ['di-mr-restore'], waits);
      // Reception minimized beside it: two slots at once.
      await page.click(T('di-open-reception'));
      await page.waitForSelector(T('di-reception-inbox'), { timeout: 15_000 });
      await page.click(T('di-reception-minimize'));
      await page.waitForSelector(T('di-reception-restore'), { timeout: 5_000 });
      await measure(
        page,
        `${at}, request and Reception chips`,
        ['di-mr-restore', 'di-reception-restore'],
        waits,
      );
      await page.close();
    }
    expect(
      relayed.filter((e) => e.msg.op === 'decide').map((e) => e.msg.op),
      'the geometry check sent a decision',
    ).toEqual([]);
  },
  STEPS_MS,
);

test(
  'AC3 strip vs covering workspace, RENDERED: beside the panel the workspace is inset and never overlaps the strip; when it covers, the strip is below it and is reachable again once the workspace is minimized',
  async () => {
    const page = await openWindow();
    // Reception minimized into the strip.
    await page.click(T('di-open-reception'));
    await page.waitForSelector(T('di-reception-inbox'), { timeout: 15_000 });
    await page.click(T('di-reception-minimize'));
    await page.waitForSelector(T('di-reception-restore'), { timeout: 5_000 });

    const rect = (sel: string) =>
      page.$eval(sel, (el) => {
        const r = el.getBoundingClientRect();
        return {
          left: r.left,
          right: r.right,
          top: r.top,
          bottom: r.bottom,
          x: r.left + r.width / 2,
          y: r.top + r.height / 2,
        };
      });
    /** What a click at (x, y) reaches, said in terms of the strip, its chips and the workspace. */
    const reached = (x: number, y: number) =>
      page.evaluate(
        ([px, py]) => {
          const el = document.elementFromPoint(px, py);
          const inside = (sel: string) =>
            el !== null && document.querySelector(sel)?.contains(el) === true;
          return {
            inWorkspace: inside('[data-testid="di-mr"]'),
            inStrip: inside('[data-testid="di-minimized-tray"]'),
            chip: el?.closest('[data-testid$="-restore"]')?.getAttribute('data-testid') ?? null,
            what: el === null ? null : (el.getAttribute('data-testid') ?? el.tagName.toLowerCase()),
          };
        },
        [x, y] as const,
      );
    const stacking = () =>
      page.evaluate(() => {
        const z = (sel: string) => getComputedStyle(document.querySelector(sel)!).zIndex;
        return {
          strip: z('[data-testid="di-minimized-tray"]'),
          workspace: z('[data-testid="di-mr"]'),
        };
      });

    // (a) WIDE window: the workspace opens BESIDE the panel and is inset clear of the strip.
    await openWorkspace(page);
    expect(
      await page.getAttribute(T('di-mr'), 'data-covering'),
      'wide: the workspace sits beside, it does not cover',
    ).toBe('false');
    const wsWide = await rect(T('di-mr'));
    const stripWide = await rect(T('di-minimized-tray'));
    const chipWide = await rect(T('di-reception-restore'));
    console.log(
      `[strip vs workspace, wide ${VIEW.width} x ${VIEW.height}] workspace right edge ${wsWide.right}, strip left edge ${stripWide.left}, chip centre (${chipWide.x}, ${chipWide.y})`,
    );
    expect(
      wsWide.right <= stripWide.left + 0.75,
      `wide: the workspace (right edge ${wsWide.right}) ends before the strip (left edge ${stripWide.left})`,
    ).toBe(true);
    expect(
      await reached(chipWide.x, chipWide.y),
      'wide: a click at the strip chip reaches the chip',
    ).toMatchObject({
      inStrip: true,
      inWorkspace: false,
      chip: 'di-reception-restore',
    });

    // (b) NARROW window: the workspace COVERS the world; the strip is classified below it.
    await page.setViewportSize({ width: 599, height: 700 });
    await page.waitForSelector(`${T('di-mr')}[data-covering="true"]`, { timeout: 5_000 });
    await page.waitForTimeout(300);
    const chipNarrow = await rect(T('di-reception-restore'));
    expect(
      chipNarrow.x > 0 && chipNarrow.x < 599 && chipNarrow.y > 0 && chipNarrow.y < 700,
      `narrow: the strip chip centre (${chipNarrow.x}, ${chipNarrow.y}) lies inside the window`,
    ).toBe(true);
    const under = await reached(chipNarrow.x, chipNarrow.y);
    expect(
      under.inWorkspace,
      `narrow: a click where the strip chip is reaches the covering workspace (it reached ${under.what})`,
    ).toBe(true);
    expect(under.inStrip, 'narrow: the strip is below the covering workspace').toBe(false);
    const z = await stacking();
    console.log(
      `[strip vs workspace, narrow 599 x 700] chip centre (${chipNarrow.x}, ${chipNarrow.y}) reaches ${under.what}; z-index strip ${z.strip}, workspace ${z.workspace}`,
    );
    expect(
      Number(z.strip) < Number(z.workspace),
      `narrow: the strip's computed z-index (${z.strip}) is below the covering workspace's (${z.workspace})`,
    ).toBe(true);

    // Minimize the workspace: the same point now reaches a strip chip again.
    await page.click(T('di-mr-minimize'));
    await page.waitForSelector(T('di-mr-restore'), { timeout: 5_000 });
    expect(
      await page.$eval(T('di-mr'), (el) => el.getClientRects().length),
      'the minimized workspace is hidden',
    ).toBe(0);
    const after = await reached(chipNarrow.x, chipNarrow.y);
    console.log(
      `[strip vs workspace, narrow, workspace minimized] the same point reaches ${after.chip} (${after.what})`,
    );
    expect(
      after.inStrip,
      `narrow, workspace minimized: the same click reaches the strip (it reached ${after.what})`,
    ).toBe(true);
    expect(
      ['di-mr-restore', 'di-reception-restore'],
      'narrow, workspace minimized: it reaches a strip chip',
    ).toContain(after.chip);
    expect(
      (
        await page.$$eval(`${T('di-minimized-tray')} button`, (els) =>
          els.map((e) => e.getAttribute('data-testid')),
        )
      ).sort(),
      'the strip holds the Reception chip and the request chip',
    ).toEqual(['di-mr-restore', 'di-reception-restore']);
    expect(decisionsFrom(page), 'a decision was sent').toEqual([]);
  },
  STEPS_MS,
);

/*
 * AC-UX-13 / WCAG 2.2 SC 1.4.10 (Reflow) at 320 CSS px, the office panel in the REAL App. Reading and control chrome gets
 * no canvas exception: at 320 x 568 every control and every line of text of the office panel must lie inside the window,
 * with no horizontal scrolling of the page or of the panel.
 *
 * The design under test: the panel's width is min(428, window width - strip width), where the strip of minimized surfaces
 * counts (44 px) ONLY while it holds at least one restore control. With the panel open the strip sits at the panel's left
 * edge, so the two sit side by side, both inside the window. The world canvas yields (at 320 with the office expanded its
 * width is 0, deliberately); it returns when the office is minimized. The request workspace keeps covering at narrow widths.
 * The numbers are restated here on purpose (not imported from the product) so a change to them is noticed.
 */
// INDEPENDENT pin (pass C, C-V1): measured from 01-home.jpg to the panel's own EDGE RULE at x 852 in the 1280 frame (1280 - 852 = 428);
// not the dark lead-in from x 847, which would read 433. Was 420.
const OFFICE_PANEL_MAX_PX = 428;
const OFFICE_STRIP_PX = 44;
const OFFICE_NAMED = {
  'di-find-input': 'the Find box',
  'di-mr-open': 'the request entrance',
  'di-office-panel-minimize': 'the office Minimize control',
} as const;

const rectOf = (page: Page, sel: string) =>
  page.$eval(sel, (el) => {
    const r = el.getBoundingClientRect();
    return { left: r.left, right: r.right, top: r.top, bottom: r.bottom, width: r.width };
  });
const worldWidth = (page: Page) =>
  page.evaluate(() => document.querySelector('canvas')?.getBoundingClientRect().width ?? null);
/** Setup only: where the panel's edge lies must not decide whether the SETUP works; reachability is asserted by hit tests. */
const activate = (page: Page, id: string) => page.$eval(T(id), (el) => (el as HTMLElement).click());
const NO_OFFSET = { left: 0, right: 0, top: 0, bottom: 0 };

async function windowAt(width: number, height: number): Promise<Page> {
  const page = await openWindow({ width, height });
  await page.waitForSelector(T('di-office-panel'), { timeout: 30_000 });
  await page.waitForTimeout(1200); // settled, as an owner sees it
  return page;
}
async function minimizeRequestWorkspace(page: Page) {
  await activate(page, 'di-mr-open');
  await page.waitForSelector(T('di-mr'), { timeout: 15_000 });
  await waitFor(
    'the panel to be read',
    async () => !(await exists(page, T('di-mr-status'))),
    20_000,
  );
  await activate(page, 'di-mr-minimize');
  await page.waitForSelector(T('di-mr-restore'), { timeout: 5_000 });
}
async function minimizeReception(page: Page) {
  await activate(page, 'di-open-reception');
  await page.waitForSelector(T('di-reception-inbox'), { timeout: 15_000 });
  await activate(page, 'di-reception-minimize');
  await page.waitForSelector(T('di-reception-restore'), { timeout: 5_000 });
}

/**
 * The office panel fits a window `w` CSS px wide: box, then (reflowReach, which supports the panel's vertical fade mask where
 * textFit refuses it) every control and every line of text, containers that scroll or clip sideways, and the page's own scroll.
 * The counts have floors taken from the real numbers, so a root that matched nothing cannot pass vacuously.
 */
const OFFICE_REACH_FLOOR = { controls: 12, texts: 60 };
async function officeFits(page: Page, at: string, w: number, panelPx: number) {
  await page.waitForTimeout(1200); // settled
  const panel = await rectOf(page, T('di-office-panel'));
  const reach = await reflowReach(page, T('di-office-panel'));
  console.log(
    `[office at ${w}] ${at}: panel box ${panel.left} to ${panel.right} (width ${panel.width}); reflowReach: ${reach.controls} controls (${reach.controlsOut.length} out), ${reach.texts} text rects (${reach.textsOut.length} out), ${reach.horizontalContainers.length} horizontal containers, page scroll ${reach.pageHorizontalScroll}`,
  );
  expect(
    panel.left >= -0.75 && panel.right <= w + 0.75,
    `${at}: the panel box (${panel.left} to ${panel.right}) lies inside 0 to ${w}`,
  ).toBe(true);
  expect(
    Math.abs(panel.width - panelPx) <= 0.75,
    `${at}: the panel is ${panelPx} px wide (it is ${panel.width})`,
  ).toBe(true);
  expect(
    reach.controls >= OFFICE_REACH_FLOOR.controls && reach.texts >= OFFICE_REACH_FLOOR.texts,
    `${at}: the measure looked at ${reach.controls} controls and ${reach.texts} text rects (floors ${OFFICE_REACH_FLOOR.controls} and ${OFFICE_REACH_FLOOR.texts})`,
  ).toBe(true);
  expect(
    reach.controlsOut,
    `${at}: controls outside the window sideways (${reach.controlsOut.length} of ${reach.controls})`,
  ).toEqual([]);
  expect(
    reach.textsOut,
    `${at}: lines of text outside the window sideways (${reach.textsOut.length} of ${reach.texts})`,
  ).toEqual([]);
  expect(reach.horizontalContainers, `${at}: containers that scroll or clip sideways`).toEqual([]);
  expect(reach.pageHorizontalScroll, `${at}: the page scrolls sideways`).toBe(false);
  // the Find box, the request entrance and the office Minimize control: fully inside the window, and a click at the middle reaches each
  const named = await page.evaluate((ids) => {
    return ids.map((id) => {
      const el = document.querySelector<HTMLElement>(`[data-testid="${id}"]`);
      if (el === null) return { id, found: false, left: 0, right: 0, hits: false };
      const r = el.getBoundingClientRect();
      const hit = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2);
      return {
        id,
        found: true,
        left: r.left,
        right: r.right,
        hits: hit !== null && el.contains(hit),
      };
    });
  }, Object.keys(OFFICE_NAMED));
  for (const c of named) {
    const what = OFFICE_NAMED[c.id as keyof typeof OFFICE_NAMED];
    expect(c.found, `${at}: ${what} (${c.id}) is on the page`).toBe(true);
    expect(
      c.left >= -0.75 && c.right <= w + 0.75,
      `${at}: ${what} (${c.left} to ${c.right}) is fully inside 0 to ${w}`,
    ).toBe(true);
    expect(c.hits, `${at}: a click at the middle of ${what} reaches it`).toBe(true);
  }
}

/** The strip on its own: inside the window, 44 wide, holding exactly these chips, drawn and reachable. */
async function stripFits(page: Page, at: string, w: number, chips: string[]) {
  const tray = await rectOf(page, T('di-minimized-tray'));
  expect(
    tray.left >= -0.75 && tray.right <= w + 0.75,
    `${at}: the strip box (${tray.left} to ${tray.right}) lies inside 0 to ${w}`,
  ).toBe(true);
  expect(
    Math.abs(tray.width - OFFICE_STRIP_PX) <= 0.75,
    `${at}: the strip is ${OFFICE_STRIP_PX} px wide (it is ${tray.width})`,
  ).toBe(true);
  const fit = await textFit(page, T('di-minimized-tray'));
  expect(
    fit.controls.map((c) => c.id).sort(),
    `${at}: the strip holds exactly these chips`,
  ).toEqual([...chips].sort());
  for (const c of fit.controls) {
    const who = `${at}: ${c.id}`;
    expect(c.outside, `${who}: text outside the visible region`).toEqual([]);
    expect(c.textNotHit, `${who}: text whose centre the chip does not paint`).toEqual([]);
    expect(c.clipsContent, `${who}: the chip clips its own content`).toBe(false);
    expect(c.controlWritingMode, `${who}: the chip keeps a horizontal writing mode`).toBe(
      'horizontal-tb',
    );
    expect(c.controlOffWindow, `${who}: the chip lies inside the window`).toEqual(NO_OFFSET);
    expect(c.hitsItself, `${who}: a click at the chip's centre reaches it`).toBe(true);
  }
}

/** The open panel beside the strip: not overlapping, and together no wider than the window. */
async function panelBesideStrip(page: Page, at: string, w: number) {
  const tray = await rectOf(page, T('di-minimized-tray'));
  const panel = await rectOf(page, T('di-office-panel'));
  expect(
    panel.left >= tray.right - 0.75,
    `${at}: the panel (from ${panel.left}) does not overlap the strip (to ${tray.right})`,
  ).toBe(true);
  expect(
    panel.width + tray.width <= w + 0.75,
    `${at}: panel (${panel.width}) and strip (${tray.width}) together do not exceed ${w}`,
  ).toBe(true);
}

test(
  'AC-UX-13 / SC 1.4.10, office panel at 320, REAL APP: case 1, the office expanded with nothing minimized fits inside 320 x 568',
  async () => {
    const page = await windowAt(320, 568);
    expect(
      (await exists(page, T('di-minimized-tray')))
        ? await page.$eval(T('di-minimized-tray'), (el) => el.children.length)
        : 0,
      'nothing is minimized',
    ).toBe(0);
    // no strip: the panel takes the whole 320
    await officeFits(page, '320 x 568, office expanded, nothing minimized', 320, 320);
    await notOccluded(page, '320 x 568, office expanded, nothing minimized', T('di-office-panel'));
  },
  STEPS_MS,
);

test(
  'AC-UX-13 / SC 1.4.10, office panel at 320, REAL APP: case 2, other surfaces minimized: the panel and the strip sit side by side, both inside 320',
  async () => {
    const page = await windowAt(320, 568);
    await minimizeRequestWorkspace(page);
    await minimizeReception(page);
    const at = '320 x 568, office expanded, request workspace and Reception minimized';
    await stripFits(page, at, 320, ['di-mr-restore', 'di-reception-restore']);
    await panelBesideStrip(page, at, 320);
    await officeFits(page, at, 320, 320 - OFFICE_STRIP_PX);
    await notOccluded(page, at, T('di-office-panel'));
    expect(
      relayed
        .filter((e) =>
          ['turn', 'submit', 'decide', 'checkApproval', 'withdraw', 'run'].includes(
            String(e.msg.op),
          ),
        )
        .map((e) => e.msg.op),
      'laying out and minimizing sent something that acts',
    ).toEqual([]);
  },
  STEPS_MS,
);

test(
  'AC-UX-13 / SC 1.4.10, office panel at 320, REAL APP: case 3, the world yields deliberately (width 0) and returns when the office is minimized; state is kept',
  async () => {
    const page = await windowAt(320, 568);
    await minimizeRequestWorkspace(page);
    await page.waitForTimeout(1200);
    expect(
      await worldWidth(page),
      'RECORDED BEHAVIOUR: with the office expanded and the strip present at 320 the world canvas yields completely (width 0); it returns when the office is minimized',
    ).toBe(0);
    // state to keep: text typed in the office's Find box
    await page.fill(T('di-find-input'), 'lead');
    const before = await rectOf(page, T('di-office-panel'));
    // The office's own Minimize control must be reachable (case 1 measures it with the helper too): its box is inside
    // the window and a click at its middle reaches it.
    const minimize = await page.evaluate((sel) => {
      const el = document.querySelector<HTMLElement>(sel)!;
      const r = el.getBoundingClientRect();
      const hit = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2);
      return { left: r.left, right: r.right, hits: hit !== null && el.contains(hit) };
    }, T('di-office-panel-minimize'));
    expect(
      minimize.left >= -0.75 && minimize.right <= 320 + 0.75 && minimize.hits,
      `the office Minimize control (${minimize.left} to ${minimize.right}) is inside the window and reachable by a click`,
    ).toBe(true);
    await page.click(T('di-office-panel-minimize'), { timeout: 5_000 });
    await page.waitForSelector(T('di-office-panel-collapsed'), { timeout: 5_000 });
    await page.waitForTimeout(1200);
    const world = await worldWidth(page);
    expect(
      world,
      `the world returns when the office is minimized: 320 - ${OFFICE_STRIP_PX} = ${320 - OFFICE_STRIP_PX} (it is ${world})`,
    ).not.toBeNull();
    expect(
      Math.abs(world! - (320 - OFFICE_STRIP_PX)) <= 1 && world! > 0,
      `the world canvas is ${320 - OFFICE_STRIP_PX} wide (it is ${world})`,
    ).toBe(true);
    await stripFits(page, '320 x 568, office minimized', 320, [
      'di-mr-restore',
      'di-office-panel-collapsed',
    ]);
    // restore: the same layout, and the Find text is still there
    await page.click(T('di-office-panel-collapsed'), { timeout: 5_000 });
    await page.waitForSelector(T('di-office-panel') + ':not([hidden])', { timeout: 5_000 });
    await page.waitForTimeout(1200);
    const after = await rectOf(page, T('di-office-panel'));
    expect(
      [after.left, after.right].map(Math.round),
      'restoring returns the panel to the same place',
    ).toEqual([before.left, before.right].map(Math.round));
    expect(
      await worldWidth(page),
      'restoring returns the world to its width (0) beside the open office',
    ).toBe(0);
    expect(
      await page.$eval(T('di-find-input'), (el) => (el as HTMLInputElement).value),
      'the Find text typed before minimizing is still there',
    ).toBe('lead');
  },
  STEPS_MS,
);

test(
  'AC-UX-13 / SC 1.4.10, office panel at 320, REAL APP: case 4, the desktop layout at 1440 x 900 is unchanged',
  async () => {
    const page = await windowAt(1440, 900);
    const panel = await rectOf(page, T('di-office-panel'));
    expect(
      [panel.left, panel.right, panel.width],
      `the panel is exactly ${OFFICE_PANEL_MAX_PX} wide at the right edge`,
    ).toEqual([1440 - OFFICE_PANEL_MAX_PX, 1440, OFFICE_PANEL_MAX_PX]);
    expect(await worldWidth(page), `the world is 1440 - ${OFFICE_PANEL_MAX_PX}`).toBe(1440 - OFFICE_PANEL_MAX_PX);
    await minimizeRequestWorkspace(page);
    await page.waitForTimeout(1200);
    const tray = await rectOf(page, T('di-minimized-tray'));
    expect(tray.right, `the strip sits at the panel’s left edge: its right edge is ${1440 - OFFICE_PANEL_MAX_PX}`).toBe(
      1440 - OFFICE_PANEL_MAX_PX,
    );
    expect(tray.width, 'the strip is 44 wide').toBe(OFFICE_STRIP_PX);
    expect(
      await worldWidth(page),
      `the world is 1440 - ${OFFICE_PANEL_MAX_PX} - 44 = ${1440 - OFFICE_PANEL_MAX_PX - OFFICE_STRIP_PX}`,
    ).toBe(1440 - OFFICE_PANEL_MAX_PX - OFFICE_STRIP_PX);
    expect(
      (await rectOf(page, T('di-office-panel'))).width,
      `the panel is still ${OFFICE_PANEL_MAX_PX} wide with a minimized surface`,
    ).toBe(OFFICE_PANEL_MAX_PX);
    await notOccluded(
      page,
      '1440 x 900, office expanded, a surface minimized',
      T('di-office-panel'),
    );
  },
  STEPS_MS,
);

test(
  'AC-UX-13 / SC 1.4.10, office panel at 320, REAL APP: case 5, intermediate widths 360 x 640 and 414 x 800 (panel = window - strip, never wider than 428)',
  async () => {
    for (const [w, h] of [
      [360, 640],
      [414, 800],
    ] as const) {
      const page = await windowAt(w, h);
      await minimizeRequestWorkspace(page);
      await minimizeReception(page);
      const at = `${w} x ${h}, office expanded, request workspace and Reception minimized`;
      const panelPx = Math.min(OFFICE_PANEL_MAX_PX, w - OFFICE_STRIP_PX);
      await stripFits(page, at, w, ['di-mr-restore', 'di-reception-restore']);
      await panelBesideStrip(page, at, w);
      await officeFits(page, at, w, panelPx);
      await page.close();
    }
  },
  STEPS_MS,
);

/*
 * Occlusion and the fixture label at the same widths. Containment tests cannot see something drawn ON TOP of content: the
 * floating fixture banner and the ambient legend are fixed, `pointer-events: none` overlays, and a plain hit test cannot see
 * them. The design under test: both overlays live INSIDE the world's clipped container (cut at the world's edge, never the
 * panel's), so they are never drawn over the panel; and where the world has no room for the floating banner with the panel
 * OPEN (world narrower than 240 px) the same fixture words are shown INSIDE the panel as its first element, and the floating
 * banner is not rendered. The fixture label stays visible in every state.
 */
const FIXTURE_WORDS =
  'FIXTURE / VISUAL TEST - synthetic hash-pinned fixture data, not live Missions';
const NOTE_IN_PANEL = '[data-testid="di-fixture-banner-in-panel"]';
const BANNER_FLOATING = '[data-testid="di-fixture-banner"]';

/** Nothing else is painted over the content under `rootSelector` (every visible text rect and control is sampled). */
async function notOccluded(page: Page, at: string, rootSelector: string, floor = 50) {
  const o = await occlusion(page, rootSelector);
  const by = new Map<string, number>();
  for (const c of o.covered) by.set(c.by, (by.get(c.by) ?? 0) + 1);
  console.log(
    `[occlusion ${at}] ${rootSelector}: sampled ${o.sampled}, covered ${o.covered.length} by ${JSON.stringify(Object.fromEntries(by))}; covered list: ${JSON.stringify(o.covered)}`,
  );
  expect(
    o.sampled > floor,
    `${at}: the probe sampled ${o.sampled} points (more than ${floor})`,
  ).toBe(true);
  expect(
    o.covered,
    `${at}: content of ${rootSelector} with something else painted on top (by ${JSON.stringify(Object.fromEntries(by))})`,
  ).toEqual([]);
}
const fixtureNote = (page: Page) =>
  page.evaluate(
    ([note, panel]) => {
      const el = document.querySelector<HTMLElement>(note);
      const root = document.querySelector<HTMLElement>(panel);
      return el === null
        ? null
        : {
            visible:
              el.checkVisibility({ checkVisibilityCSS: true }) && el.getClientRects().length > 0,
            text: el.innerText.replace(/\s+/g, ' ').trim(),
            firstInPanel: root !== null && root.firstElementChild === el,
          };
    },
    [NOTE_IN_PANEL, T('di-office-panel')] as const,
  );
/** The fixture words are in the panel (and the floating banner is not rendered): the world has no room for it beside an open panel. */
async function fixtureLabelInPanel(page: Page, at: string) {
  const note = await fixtureNote(page);
  expect(note, `${at}: the in-panel fixture note ${NOTE_IN_PANEL} exists`).not.toBeNull();
  expect(note!.visible, `${at}: the in-panel fixture note is visible`).toBe(true);
  expect(note!.text, `${at}: the in-panel fixture note carries the fixture words`).toBe(
    FIXTURE_WORDS,
  );
  expect(
    note!.firstInPanel,
    `${at}: the in-panel fixture note is the first element of the panel`,
  ).toBe(true);
  expect(await exists(page, BANNER_FLOATING), `${at}: the floating banner is not rendered`).toBe(
    false,
  );
}
/** The floating banner is the label (the in-panel note is absent): shown, inside the window and not covered. */
async function fixtureLabelFloating(page: Page, at: string, w: number, h: number) {
  expect(await exists(page, NOTE_IN_PANEL), `${at}: the in-panel fixture note is absent`).toBe(
    false,
  );
  expect(await exists(page, BANNER_FLOATING), `${at}: the floating banner is present`).toBe(true);
  const banner = await page.evaluate((sel) => {
    const el = document.querySelector<HTMLElement>(sel)!;
    const r = el.getBoundingClientRect();
    return {
      visible: el.checkVisibility({ checkVisibilityCSS: true }) && el.getClientRects().length > 0,
      text: el.innerText.replace(/\s+/g, ' ').trim(),
      left: r.left,
      right: r.right,
      top: r.top,
      bottom: r.bottom,
    };
  }, BANNER_FLOATING);
  expect(banner.visible, `${at}: the floating banner is visible`).toBe(true);
  expect(banner.text, `${at}: the floating banner carries the fixture words`).toContain(
    FIXTURE_WORDS,
  );
  expect(
    banner.left >= -0.75 &&
      banner.right <= w + 0.75 &&
      banner.top >= -0.75 &&
      banner.bottom <= h + 0.75,
    `${at}: the floating banner (${banner.left},${banner.top} to ${banner.right},${banner.bottom}) lies inside ${w} x ${h}`,
  ).toBe(true);
  // a short banner offers few sample points (three per line of text): at least one full line is required
  await notOccluded(page, `${at}, floating banner`, BANNER_FLOATING, 2);
}

for (const [w, h] of [
  [320, 568],
  [640, 800],
] as const) {
  test(
    `AC-UX-13 / SC 1.4.10, office panel at 320, REAL APP: case 6, nothing is drawn over the office panel at ${w} x ${h} (occlusion)`,
    async () => {
      const page = await windowAt(w, h);
      await notOccluded(page, `${w} x ${h}, office expanded`, T('di-office-panel'));
    },
    STEPS_MS,
  );
  test(
    `AC-UX-13 / SC 1.4.10, office panel at 320, REAL APP: case 7, the fixture label is shown inside the panel at ${w} x ${h} (the world has no room for the floating banner)`,
    async () => {
      const page = await windowAt(w, h);
      await fixtureLabelInPanel(page, `${w} x ${h}, office expanded`);
    },
    STEPS_MS,
  );
}

test(
  'AC-UX-13 / SC 1.4.10, office panel at 320, REAL APP: case 6, nothing is drawn over the office panel at 320 x 568 with other surfaces minimized (occlusion)',
  async () => {
    const page = await windowAt(320, 568);
    await minimizeRequestWorkspace(page);
    await minimizeReception(page);
    await page.waitForTimeout(1200);
    await notOccluded(
      page,
      '320 x 568, office expanded, request workspace and Reception minimized',
      T('di-office-panel'),
    );
  },
  STEPS_MS,
);

test(
  'AC-UX-13 / SC 1.4.10, office panel at 320, REAL APP: case 7, with the office minimized at 320 x 568 the floating banner is the label (world 276 wide), shown and not covered',
  async () => {
    const page = await windowAt(320, 568);
    await activate(page, 'di-office-panel-minimize');
    await page.waitForSelector(T('di-office-panel-collapsed'), { timeout: 5_000 });
    await page.waitForTimeout(1200);
    expect(
      await worldWidth(page),
      'the world is 320 - 44 = 276 wide, which has room (240 or more) for the floating banner',
    ).toBe(320 - OFFICE_STRIP_PX);
    await fixtureLabelFloating(page, '320 x 568, office minimized', 320, 568);
  },
  STEPS_MS,
);

test(
  'AC-UX-13 / SC 1.4.10, office panel at 320, REAL APP: case 8, the ordinary desktop case at 1440 x 900 is preserved: floating banner, legend and toolbar are in the world, left of the panel, and nothing covers the panel',
  async () => {
    const page = await windowAt(1440, 900);
    const at = '1440 x 900, office expanded';
    await fixtureLabelFloating(page, at, 1440, 900);
    const panel = await rectOf(page, T('di-office-panel'));
    // the independent DESIGN assertion, kept (the 428 restated in this file on purpose: 1440 - 428 = 1012)
    const PANEL_LEFT = 1440 - OFFICE_PANEL_MAX_PX;
    expect(panel.left, `${at}: the panel starts at ${PANEL_LEFT}`).toBe(PANEL_LEFT);
    const world = await page.evaluate(() => {
      const box = (el: Element | null | undefined) => {
        if (!el) return null;
        const r = el.getBoundingClientRect();
        return {
          visible:
            (el as HTMLElement).checkVisibility({ checkVisibilityCSS: true }) &&
            el.getClientRects().length > 0,
          left: r.left,
          right: r.right,
        };
      };
      return {
        legend: box(document.querySelector('[data-testid="ambient-legend"]')),
        // the bottom toolbar has no id of its own: it is the panel that holds the City button
        toolbar: box(
          document.querySelector('[data-testid="city-button"]')?.closest('.pixel-panel'),
        ),
      };
    });
    for (const [what, b] of [
      ['ambient legend', world.legend],
      ['bottom toolbar', world.toolbar],
    ] as const) {
      expect(b, `${at}: the ${what} is on the page`).not.toBeNull();
      expect(b!.visible, `${at}: the ${what} is visible`).toBe(true);
      expect(
        // DERIVED from the RENDERED panel edge, never a literal: no world content may cross into the panel (a stale 1020 admitted 8 px)
        b!.left >= -0.75 && b!.right <= panel.left + 0.75,
        `${at}: the ${what} (${b!.left} to ${b!.right}) lies in the world, left of the panel (right edge <= ${panel.left})`,
      ).toBe(true);
    }
    await notOccluded(page, at, T('di-office-panel'));
  },
  STEPS_MS,
);

/*
 * TAB TRAVERSAL of the office PANEL and the minimized STRIP, by the REAL keyboard. (The world's own controls, and their traversal
 * at every window size, are asserted in test/diWorldYield.test.ts and not here.) Every stop in the panel and in the strip must be
 * fully drawn where it sits and show a focus indicator that paints something; and nothing may be a tab stop outside the panel, the
 * strip and the world's controls.
 */
const TAB_REGIONS = {
  // The world's controls: the gate that holds them, and the ONE control of it that a NARROW world (under DI_WORLD_ROOMY_PX) draws in the strip's pinned footer instead: the version label, which
  // VersionIndicator portals out of the gate, so `closest('.di-world-controls')` no longer finds it. The label alone is admitted, not the footer: any other stop there is still in no region.
  world: '.di-world-controls, [data-testid="di-world-rail-footer"] .di-version-label',
  panel: '[data-testid="di-office-panel"]',
  strip: '[data-testid="di-minimized-tray"]',
};
/** The keyboard order at 1440 x 900 with the office open, recorded from the frozen candidate-4 (before the narrow-width work). */
const DESKTOP_TAB_ORDER_CANDIDATE_4: string[] = [
  'button',
  'button',
  'di-office-panel-minimize',
  'di-open-reception',
  'di-mr-open',
  'di-mr-open-requests',
  'di-find-input',
  'About this list',
  'di-mission-locate',
  'di-open-evidence',
  'di-open-inspector-mission',
  'Team (1): executor',
  'di-mission-samples-title',
  'Full evidence: counts, capture, registry',
  'Freshness detail and times',
  'Previous agent',
  'Next agent',
  'Previous Mission',
  'Next Mission',
  'Layout',
  'city-button',
  'Settings',
  'x',
];
/** ...and its PANEL stops alone (the world's controls, the part that was reworked, left out). */
const DESKTOP_PANEL_ORDER_CANDIDATE_4: string[] = [
  'di-office-panel-minimize',
  'di-open-reception',
  'di-mr-open',
  'di-mr-open-requests',
  'di-find-input',
  'About this list',
  'di-mission-locate',
  'di-open-evidence',
  'di-open-inspector-mission',
  'Team (1): executor',
  'di-mission-samples-title',
  'Full evidence: counts, capture, registry',
  'Freshness detail and times',
  'Previous agent',
  'Next agent',
  'Previous Mission',
  'Next Mission',
];

const tabLabel = (t: TabTraversal) => t.stops.map((s) => s.id);
const inPanelOrStrip = (s: TabStop) => s.region === 'panel' || s.region === 'strip';
const panelAndStrip = (t: TabTraversal): TabTraversal => ({
  ...t,
  stops: t.stops.filter(inPanelOrStrip),
});
async function traverse(page: Page, at: string): Promise<TabTraversal> {
  const t = await tabTraversal(page, { max: 150, regions: TAB_REGIONS });
  console.log(
    `[tab ${at}] ${t.stops.length} stops, ended ${t.ended}: ${JSON.stringify(t.stops.map((s) => `${s.region ?? '-'}:${s.id} ${s.hitSamples}/${s.sampled}${s.indicator.pixelsChanged === false ? ' NOFOCUSPIXELS' : ''}`))}`,
  );
  // said here, before any assertion, so a red run carries the numbers
  const problems = tabProblems(panelAndStrip(t), { panel: 1, strip: 1, default: 1 });
  console.log(`[tab problems ${at}] ${problems.length}: ${JSON.stringify(problems)}`);
  return t;
}
/** The traversal ended, every stop belongs somewhere, and every panel and strip stop is fully drawn with a focus indicator. */
function expectPanelAndStripSeen(t: TabTraversal, at: string) {
  expect(t.ended, `${at}: the traversal reached its end (it did not stop at the limit)`).not.toBe(
    'max',
  );
  expect(
    t.stops.filter((s) => s.region === null).map((s) => s.id),
    `${at}: tab stops that are in neither the panel, the strip nor the world's controls`,
  ).toEqual([]);
  expect(
    tabProblems(panelAndStrip(t), { panel: 1, strip: 1, default: 1 }),
    `${at}: panel or strip tab stops that are not fully drawn or show no focus indicator`,
  ).toEqual([]);
}
/** The Find box is a tab stop that is drawn at every one of the 25 hit-test points (nothing is painted over it). */
function expectFindDrawn(t: TabTraversal, at: string) {
  const find = t.stops.find((s) => s.id === 'di-find-input');
  expect(find, `${at}: the Find box is a tab stop`).toBeDefined();
  expect(
    [find!.hitSamples, find!.sampled],
    `${at}: the Find box is drawn at all 25 points (drawn over it instead: ${find!.coveredBy.join(', ') || 'nothing'})`,
  ).toEqual([25, 25]);
  expect(find!.indicator.pixelsChanged, `${at}: focusing the Find box paints a pixel`).toBe(true);
}
/**
 * Focus a control by pressing the REAL Tab key from the start of the document until it holds focus (a labelled stop: test id,
 * else aria-label, else its first words, as the traversal labels them). Returns how many presses it took.
 */
async function focusByTab(page: Page, target: string, max = 150): Promise<number> {
  await page.evaluate(() => {
    const sentinel = document.createElement('span');
    sentinel.setAttribute('data-tab-sentinel', 'true');
    sentinel.tabIndex = -1;
    document.body.prepend(sentinel);
    sentinel.focus();
  });
  try {
    for (let i = 0; i < max; i += 1) {
      await page.keyboard.press('Tab');
      const label = await page.evaluate(() => {
        const el = document.activeElement as HTMLElement | null;
        if (el === null || el === document.body) return null;
        const l =
          el.getAttribute('data-testid') ??
          el.getAttribute('aria-label') ??
          (el.textContent ?? '').replace(/\s+/g, ' ').trim().slice(0, 40);
        return l !== '' ? l : el.tagName.toLowerCase();
      });
      if (label === target) return i + 1;
    }
    throw new Error(`${target} was not reached by Tab in ${max} presses`);
  } finally {
    await page.evaluate(() =>
      document.querySelectorAll('[data-tab-sentinel]').forEach((e) => e.remove()),
    );
  }
}

test(
  'AC-UX-13 / SC 1.4.10, office panel at 320, REAL APP: TT1, 320 x 568 with the office open and nothing minimized: every panel stop is fully drawn with a focus indicator, and the Find box is not covered',
  async () => {
    const page = await windowAt(320, 568);
    const at = '320 x 568, office open, nothing minimized';
    const t = await traverse(page, at);
    expectPanelAndStripSeen(t, at);
    expectFindDrawn(t, at);
    expect(tabLabel(t), `${at}: the office Minimize control is a tab stop`).toContain(
      'di-office-panel-minimize',
    );
    expect(
      t.stops.filter((s) => s.region === 'strip'),
      `${at}: nothing is minimized, so the strip has no stops`,
    ).toEqual([]);
  },
  STEPS_MS,
);

test(
  'AC-UX-13 / SC 1.4.10, office panel at 320, REAL APP: TT2, 320 x 568 with the request workspace and Reception minimized: the strip chips are fully drawn with a focus indicator, in a stable order after the panel',
  async () => {
    const page = await windowAt(320, 568);
    await minimizeRequestWorkspace(page);
    await minimizeReception(page);
    await page.waitForTimeout(1200);
    const at = '320 x 568, office open, request workspace and Reception minimized';
    const t = await traverse(page, at);
    expectPanelAndStripSeen(t, at);
    expectFindDrawn(t, at);
    expect(tabLabel(t), `${at}: the office Minimize control is a tab stop`).toContain(
      'di-office-panel-minimize',
    );
    for (const id of ['di-mr-restore', 'di-reception-restore']) {
      const stop = t.stops.find((s) => s.id === id);
      expect(stop, `${at}: ${id} is a tab stop`).toBeDefined();
      expect(stop!.region, `${at}: ${id} is in the strip`).toBe('strip');
      expect(
        [stop!.hitSamples, stop!.sampled],
        `${at}: ${id} is drawn at all 25 of 25 points`,
      ).toEqual([25, 25]);
      expect(stop!.indicator.pixelsChanged, `${at}: focusing ${id} paints a pixel`).toBe(true);
    }
    // the order is stable and recorded: the strip's two stops are consecutive and FOLLOW every panel stop
    const strip = t.stops.filter((s) => s.region === 'strip').map((s) => s.index);
    const panel = t.stops.filter((s) => s.region === 'panel').map((s) => s.index);
    console.log(
      `[tab order ${at}] strip stops at ${JSON.stringify(strip)}, panel stops ${panel[0]}..${panel[panel.length - 1]} (${panel.length}); the strip comes ${strip[0] < panel[0] ? 'BEFORE' : 'AFTER'} the panel`,
    );
    expect(strip.length, `${at}: the strip holds both chips`).toBe(2);
    expect(strip[1] - strip[0], `${at}: the strip's stops are consecutive`).toBe(1);
    expect(
      strip[0],
      `${at}: the strip's stops (${strip}) follow every panel stop (last at ${panel[panel.length - 1]})`,
    ).toBeGreaterThan(panel[panel.length - 1]);
  },
  STEPS_MS,
);

/**
 * The office's own Minimize and Restore, BY KEYBOARD, at a window `w` wide: what the world canvas does is stated from the width the
 * canvas is RENDERED at (open, minimized, restored), not from any threshold; the Find text and the panel's place survive the round trip.
 */
async function officeKeyboardRoundTrip(page: Page, w: number) {
  const at = `${w} x ${w === 320 ? 568 : 800}, office minimized and restored by keyboard`;
  await page.fill(T('di-find-input'), 'lead');
  const before = await rectOf(page, T('di-office-panel'));
  const open = await worldWidth(page);
  expect(
    open,
    `${at}: with the office open the world is ${w} - ${OFFICE_PANEL_MAX_PX} wide at most (it is ${open})`,
  ).toBe(Math.max(0, w - OFFICE_PANEL_MAX_PX));
  await focusByTab(page, 'di-office-panel-minimize');
  await page.keyboard.press('Enter');
  await page.waitForSelector(T('di-office-panel-collapsed'), { timeout: 5_000 });
  await page.waitForTimeout(1200);
  const minimized = await worldWidth(page);
  console.log(
    `[round trip ${at}] world canvas rendered ${open} wide open, ${minimized} wide minimized`,
  );
  expect(
    minimized,
    `${at}: minimizing gives the world the room: ${open} -> ${minimized} (the strip takes ${OFFICE_STRIP_PX})`,
  ).toBe(w - OFFICE_STRIP_PX);
  expect(
    minimized! > open!,
    `${at}: the world is wider minimized (${minimized}) than open (${open})`,
  ).toBe(true);
  await focusByTab(page, 'di-office-panel-collapsed');
  await page.keyboard.press('Enter');
  await page.waitForSelector(`${T('di-office-panel')}:not([hidden])`, { timeout: 5_000 });
  await page.waitForTimeout(1200);
  expect(await worldWidth(page), `${at}: restoring gives the world back its open width`).toBe(open);
  const after = await rectOf(page, T('di-office-panel'));
  expect(
    [after.left, after.right].map(Math.round),
    `${at}: the panel is back where it was`,
  ).toEqual([before.left, before.right].map(Math.round));
  expect(
    await page.$eval(T('di-find-input'), (el) => (el as HTMLInputElement).value),
    `${at}: the Find text typed before the round trip survived it`,
  ).toBe('lead');
}

test(
  'AC-UX-13 / SC 1.4.10, office panel at 320, REAL APP: TT3, 320 x 568 the office is minimized and restored by keyboard: the world canvas grows and shrinks back, the Find text survives',
  async () => {
    const page = await windowAt(320, 568);
    await officeKeyboardRoundTrip(page, 320);
  },
  STEPS_MS,
);

test(
  'AC-UX-13 / SC 1.4.10, office panel at 320, REAL APP: TT3b, 640 x 800 the office is minimized and restored by keyboard: the world canvas grows and shrinks back, the Find text survives',
  async () => {
    const page = await windowAt(640, 800);
    await officeKeyboardRoundTrip(page, 640);
  },
  STEPS_MS,
);

for (const [w, h] of [
  [360, 640],
  [414, 800],
  [640, 800],
  [900, 700],
] as const) {
  test(
    `AC-UX-13 / SC 1.4.10, office panel at 320, REAL APP: TT4, ${w} x ${h} with the office open and two surfaces minimized: panel and strip stops are fully drawn, the Find box is not covered`,
    async () => {
      const page = await windowAt(w, h);
      await minimizeRequestWorkspace(page);
      await minimizeReception(page);
      await page.waitForTimeout(1200);
      const at = `${w} x ${h}, office open, request workspace and Reception minimized`;
      const t = await traverse(page, at);
      expectPanelAndStripSeen(t, at);
      expectFindDrawn(t, at);
      // The world is what the window leaves beside the panel and the strip. Where it can hold the controls but is narrow, the version label is drawn in the strip's footer instead of the gate:
      // it must then still be exactly ONE tab stop, and a world one. (Where the controls yield, the label stays in the inert gate and is not in the order; where the world is roomy it stays in the gate.)
      const worldPx = w - DI_PANEL_WIDTH_PX - DI_MINIMIZED_STRIP_WIDTH_PX;
      if (worldPx >= DI_WORLD_CONTROLS_MIN_PX && worldPx < DI_WORLD_ROOMY_PX)
        expect(
          t.stops.filter((s) => /^v\d+\.\d+, see what's new$/.test(s.id)).map((s) => s.region),
          `${at}: the version label (a ${worldPx} px world, narrow) is drawn in the footer and is one tab stop, in the world's controls`,
        ).toEqual(['world']);
      expect(
        t.stops.filter((s) => s.region === 'strip').map((s) => s.id),
        `${at}: the strip's stops`,
      ).toEqual(['di-mr-restore', 'di-reception-restore']);
    },
    STEPS_MS,
  );
}

test(
  'AC-UX-13 / SC 1.4.10, office panel at 320, REAL APP: TT5, 1440 x 900 with the office open: every panel stop is drawn, and the panel keeps the keyboard order candidate-4 had',
  async () => {
    const page = await windowAt(1440, 900);
    const at = '1440 x 900, office open';
    const t = await traverse(page, at);
    expectPanelAndStripSeen(t, at);
    expectFindDrawn(t, at);
    // the PANEL's order is the one candidate-4 had; the world's controls (reworked) are compared separately, as a record
    expect(
      t.stops.filter(inPanelOrStrip).map((s) => s.id),
      `${at}: the panel's keyboard order is the order recorded from candidate-4`,
    ).toEqual(DESKTOP_PANEL_ORDER_CANDIDATE_4);
    const now = tabLabel(t);
    const was = DESKTOP_TAB_ORDER_CANDIDATE_4;
    console.log(
      `[tab order ${at}] full sequence now: ${JSON.stringify(now)}; added since candidate-4: ${JSON.stringify(now.filter((x) => !was.includes(x)))}; gone since candidate-4: ${JSON.stringify(was.filter((x) => !now.includes(x)))}`,
    );
  },
  STEPS_MS,
);

/*
 * THE FIXTURE LABEL IS PAINTED, in every state. The office is an explicit fixture preview, and the label must be on the screen
 * wherever a person looks. A "visible" flag is NOT painting: a smoke run saw `visible=true` with `drawn 0/9` (the label was in
 * the document and CSS-visible but scrolled out of the window, or hidden by a rule that had lost its reason).
 *
 * THE MEASURE (stated here, not reused from a flag): a label is PAINTED when
 *   1. it is CSS-visible (`checkVisibility` with visibility, so `visibility: hidden` and `display: none` ancestors count), AND
 *   2. its whole box lies inside the window, AND
 *   3. every one of a 3 x 3 grid of points over it, hit-tested with `pointer-events` neutralized (so an overlay that ignores
 *      the pointer is still seen), lands on the label or inside it.
 * The candidates: the world's label (`di-fixture-banner`, in the column at the world's bottom-left; it stands down while the
 * workspace is SHOWING), the note inside the office panel (`di-fixture-banner-in-panel`), and the request workspace's own label
 * (`di-mr-fixture-banner`, which counts only while the workspace is shown, since a minimized workspace stays mounted and
 * `hidden`). The requirement: in EVERY state at least one candidate is painted.
 */
const FIXTURE_LABEL_IDS = [
  'di-fixture-banner',
  'di-fixture-banner-in-panel',
  'di-mr-fixture-banner',
] as const;
interface LabelFacts {
  id: string;
  present: boolean;
  cssVisible: boolean;
  inside: boolean;
  hits: number;
  sampled: number;
  painted: boolean;
  top: number;
}
async function fixtureLabelFacts(
  page: Page,
  ids: readonly string[] = FIXTURE_LABEL_IDS,
): Promise<LabelFacts[]> {
  return page.evaluate(
    (labelIds) => {
      const probe = document.createElement('style');
      probe.setAttribute('data-label-probe', 'true');
      probe.textContent = '*, *::before, *::after { pointer-events: auto !important; }';
      document.head.append(probe);
      try {
        return labelIds.map((id) => {
          const el = document.querySelector<HTMLElement>(`[data-testid="${id}"]`);
          if (el === null)
            return {
              id,
              present: false,
              cssVisible: false,
              inside: false,
              hits: 0,
              sampled: 0,
              painted: false,
              top: 0,
            };
          const cssVisible =
            el.checkVisibility({ checkVisibilityCSS: true }) && el.getClientRects().length > 0;
          const r = el.getBoundingClientRect();
          const inside =
            r.width > 0 &&
            r.height > 0 &&
            r.left >= -0.5 &&
            r.top >= -0.5 &&
            r.right <= window.innerWidth + 0.5 &&
            r.bottom <= window.innerHeight + 0.5;
          let hits = 0;
          let sampled = 0;
          for (const fy of [0.15, 0.5, 0.85])
            for (const fx of [0.15, 0.5, 0.85]) {
              sampled += 1;
              const top = document.elementFromPoint(r.left + r.width * fx, r.top + r.height * fy);
              if (top !== null && el.contains(top)) hits += 1;
            }
          return {
            id,
            present: true,
            cssVisible,
            inside,
            hits,
            sampled,
            painted: cssVisible && inside && hits === sampled,
            top: Math.round(r.top),
          };
        });
      } finally {
        probe.remove();
      }
    },
    [...ids],
  );
}
const labelLine = (l: LabelFacts) =>
  l.present
    ? `${l.id}: visible=${l.cssVisible} inside=${l.inside} drawn ${l.hits}/${l.sampled} top=${l.top}`
    : `${l.id}: not rendered`;

for (const [w, h] of [
  [320, 568],
  [360, 640],
  [599, 700],
  [640, 800],
  [900, 700],
  [1440, 900],
] as const) {
  test(
    `AC-UX-13 / fixture label, REAL APP: at ${w} x ${h} a fixture label is painted whether the request workspace was never opened, is open, is minimized, or everything is minimized`,
    async () => {
      const page = await windowAt(w, h);
      const settle = () => page.waitForTimeout(1200);
      const states: Array<{ state: string; labels: LabelFacts[] }> = [];
      const record = async (state: string) => {
        await settle();
        const labels = await fixtureLabelFacts(page);
        console.log(`[fixture label ${w} x ${h}] ${state}: ${labels.map(labelLine).join(' | ')}`);
        states.push({ state, labels });
      };
      // 1. the workspace was never opened
      await record('workspace never opened, office open');
      // 2. the workspace is open
      await activate(page, 'di-mr-open');
      await page.waitForSelector(T('di-mr'), { timeout: 15_000 });
      await waitFor(
        'the panel to be read',
        async () => !(await exists(page, T('di-mr-status'))),
        20_000,
      );
      await record('workspace open, office open');
      // 3. the workspace is minimized (it stays mounted, hidden), the office still open
      await activate(page, 'di-mr-minimize');
      await page.waitForSelector(T('di-mr-restore'), { timeout: 5_000 });
      await record('workspace minimized, office open');
      // 4. everything is minimized: the strip only
      await activate(page, 'di-office-panel-minimize');
      await page.waitForSelector(T('di-office-panel-collapsed'), { timeout: 5_000 });
      await record('workspace minimized, office minimized (strip only)');

      const unlabelled = states.filter((s) => !s.labels.some((l) => l.painted));
      expect(
        unlabelled.map((s) => ({ state: s.state, labels: s.labels.map(labelLine) })),
        `${w} x ${h}: states in which NO fixture label is painted (${unlabelled.length} of ${states.length})`,
      ).toEqual([]);
    },
    STEPS_MS,
  );
}

// short windows, so that the office panel really scrolls at every one of them (at 360 x 640 it scrolls by 1 px and at 414 x 568 by 21,
// which would let the check pass without exercising anything: the test requires at least 100 px of scroll range)
for (const [w, h] of [
  [320, 568],
  [360, 500],
  [414, 480],
  [640, 400],
] as const) {
  test(
    `AC-UX-13 / fixture label, REAL APP: the in-panel note stays painted while the office panel scrolls (top, middle, maximum, and after focus moves to its last control), ${w} x ${h}`,
    async () => {
      const page = await windowAt(w, h);
      const aside = T('di-office-panel');
      const dims = await page.$eval(aside, (el) => ({
        scrollHeight: el.scrollHeight,
        clientHeight: el.clientHeight,
      }));
      const max = dims.scrollHeight - dims.clientHeight;
      console.log(
        `[sticky note ${w} x ${h}] the office panel scrolls: scrollHeight ${dims.scrollHeight}, clientHeight ${dims.clientHeight} (range ${max} px)`,
      );
      // the check must have something to do: the panel really scrolls, by enough that top, middle and maximum differ
      expect(
        max >= 100,
        `${w} x ${h}: the office panel scrolls by ${max} px (scrollHeight ${dims.scrollHeight}, clientHeight ${dims.clientHeight}); at least 100 are needed`,
      ).toBe(true);
      const results: Array<{ at: string; scrollTop: number; note: LabelFacts }> = [];
      const measure = async (at: string) => {
        await page.waitForTimeout(250);
        const scrollTop = await page.$eval(aside, (el) => el.scrollTop);
        const [note] = await fixtureLabelFacts(page, ['di-fixture-banner-in-panel']);
        console.log(`[sticky note ${w} x ${h}] ${at} (scrollTop ${scrollTop}): ${labelLine(note)}`);
        results.push({ at, scrollTop, note });
      };
      for (const [at, y] of [
        ['top', 0],
        ['middle', Math.floor(max / 2)],
        ['maximum', max],
      ] as const) {
        await page.$eval(
          aside,
          (el, to) => {
            el.scrollTop = to;
          },
          y,
        );
        await measure(`scrolled to the ${at}`);
      }
      // the realistic way it happens: focus moves to the last control of the panel (as on a focus restore) and the panel scrolls to it
      await page.$eval(aside, (el) => {
        el.scrollTop = 0;
      });
      await page.evaluate((sel) => {
        const items = Array.from(
          document
            .querySelector(sel)!
            .querySelectorAll<HTMLElement>('button, a[href], input, summary'),
        ).filter((e) => e.checkVisibility({ checkVisibilityCSS: true }) && !e.closest('[inert]'));
        items[items.length - 1].focus();
      }, aside);
      await measure('after focus moved to the last control');
      expect(
        results
          .map((r) => r.scrollTop)
          .slice(1)
          .some((t) => t > 0),
        `${w} x ${h}: the panel really did scroll in at least one step`,
      ).toBe(true);
      const notPainted = results.filter((r) => !r.note.painted);
      expect(
        notPainted.map((r) => ({ at: r.at, scrollTop: r.scrollTop, note: labelLine(r.note) })),
        `${w} x ${h}: positions at which the in-panel fixture note is not painted (${notPainted.length} of ${results.length})`,
      ).toEqual([]);
    },
    STEPS_MS,
  );
}

/*
 * Is anything of the world's bottom-left COLUMN (the toolbar, the fixture label, the ambient legend, the City popover) PAINTED
 * OVER the docked request workspace? Tests A and G pin "the fixture watermark covers no title and no control", but they compare
 * RECTANGLES: a rectangle overlap is not painting. This measures what is actually painted on top, two ways:
 *   1. `occlusion()` (diOcclusion.ts): every visible text rect (left / centre / right) and every control's centre under the
 *      workspace is hit-tested with `pointer-events` neutralized, and whatever is painted there that is not inside the workspace
 *      is named. Nothing named `di-fixture-banner` may appear.
 *   2. a direct column probe: each item of `[data-testid="di-world-stack"]` is intersected with the workspace's box and the
 *      intersection hit-tested (5 x 5, pointer-events neutralized) for which of the two is on top; and each of the workspace's
 *      titles, controls and wording is hit-tested (3 x 3) for whether the COLUMN is on top of it. The column is layer 30 and the
 *      docked workspace layer 21, so where they overlap the COLUMN would win. The per-item intersections are LOGGED (they show
 *      whether the column's boxes meet the workspace's box at all); what is ASSERTED is that no title, control or wording of
 *      the workspace is under the column. A box overlap over blank workspace is not asserted against (see the comment at the
 *      assertions: the strict box assertion was stricter than the contract and is replaced by the contract test below).
 * The label must really be there to be checked: the test requires the world's label to be PAINTED in each state (it stands down
 * only while the workspace is covering), so it cannot pass by the label being hidden.
 * The states: the workspace DOCKED beside the office at 1600 x 1000 (the A state), the same in WIDER READING (the G state), and
 * the 1019 x 720 BESIDE window of test L (the office minimized, the world narrower).
 *
 * THE STANDING LIMIT of the sampled measure: an EMPTY covered list is NOT proof that the workspace is fully visible. It says
 * only that none of the sampled points (three per line of text, one per control) had something else painted on it; it is not a
 * pixel comparison. That is why the rectangles and a hit-tested intersection are reported too, and why the positive companion
 * is asserted: in the same state a fixture label IS painted (the measure defined above).
 */
interface ColumnFacts {
  present: boolean;
  zStack: string;
  zWorkspace: string;
  items: Array<{ id: string; box: string; overlapWithWorkspace: string; columnOnTop: string }>;
  workspaceElementsUnderColumn: Array<{ element: string; pointsUnderColumn: string }>;
}
const columnOverWorkspace = (page: Page): Promise<ColumnFacts> =>
  page.evaluate(() => {
    const probe = document.createElement('style');
    probe.textContent = '*, *::before, *::after { pointer-events: auto !important; }';
    document.head.append(probe);
    try {
      const stack = document.querySelector<HTMLElement>('[data-testid="di-world-stack"]');
      const ws = document.querySelector<HTMLElement>('[data-testid="di-mr"]');
      if (!stack || !ws)
        return {
          present: false,
          zStack: '',
          zWorkspace: '',
          items: [],
          workspaceElementsUnderColumn: [],
        };
      const idOf = (el: Element) =>
        el.getAttribute('data-testid') ??
        `${el.tagName.toLowerCase()}.${String(el.getAttribute('class') ?? '').split(' ')[0]}`;
      const box = (r: DOMRect) =>
        `${Math.round(r.left)},${Math.round(r.top)} ${Math.round(r.width)}x${Math.round(r.height)}`;
      const w = ws.getBoundingClientRect();
      // the column's items, looking THROUGH `display: contents` wrappers (the world's controls gate has no box of its own)
      const leaves = (parent: Element): Element[] =>
        Array.from(parent.children).flatMap((c) =>
          getComputedStyle(c).display === 'contents' ? leaves(c) : [c],
        );
      const items = leaves(stack)
        .filter(
          (c): c is HTMLElement =>
            c instanceof HTMLElement &&
            c.checkVisibility({ checkVisibilityCSS: true }) &&
            c.getClientRects().length > 0,
        )
        .map((item) => {
          const r = item.getBoundingClientRect();
          const left = Math.max(r.left, w.left);
          const right = Math.min(r.right, w.right);
          const top = Math.max(r.top, w.top);
          const bottom = Math.min(r.bottom, w.bottom);
          if (right <= left || bottom <= top)
            return { id: idOf(item), box: box(r), overlapWithWorkspace: 'none', columnOnTop: '-' };
          let onTop = 0;
          let total = 0;
          for (const fy of [0.1, 0.3, 0.5, 0.7, 0.9])
            for (const fx of [0.1, 0.3, 0.5, 0.7, 0.9]) {
              total += 1;
              const hit = document.elementFromPoint(
                left + (right - left) * fx,
                top + (bottom - top) * fy,
              );
              if (hit !== null && stack.contains(hit)) onTop += 1;
            }
          return {
            id: idOf(item),
            box: box(r),
            overlapWithWorkspace: `${Math.round(right - left)}x${Math.round(bottom - top)}`,
            columnOnTop: `${onTop}/${total}`,
          };
        });
      const under: Array<{ element: string; pointsUnderColumn: string }> = [];
      const selector =
        'button, summary, input, textarea, select, a[href], [role="tab"], h1, h2, h3, h4, [data-testid="di-mr-fixture-banner"], [data-testid="di-mr-progression-line"]';
      for (const el of Array.from(ws.querySelectorAll<HTMLElement>(selector))) {
        if (!el.checkVisibility({ checkVisibilityCSS: true })) continue;
        const r = el.getBoundingClientRect();
        if (r.width < 2 || r.height < 2) continue;
        let n = 0;
        let total = 0;
        for (const fy of [0.15, 0.5, 0.85])
          for (const fx of [0.15, 0.5, 0.85]) {
            total += 1;
            const hit = document.elementFromPoint(r.left + r.width * fx, r.top + r.height * fy);
            if (hit !== null && stack.contains(hit)) n += 1;
          }
        if (n > 0) under.push({ element: idOf(el), pointsUnderColumn: `${n}/${total}` });
      }
      return {
        present: true,
        zStack: getComputedStyle(stack).zIndex,
        zWorkspace: getComputedStyle(ws).zIndex,
        items,
        workspaceElementsUnderColumn: under,
      };
    } finally {
      probe.remove();
    }
  });

test(
  "AC-UX-13 / fixture label, REAL APP: the fixture label and the world's column leave the docked request workspace's titles, controls and wording uncovered at 1600 x 1000 (open, and in wider reading) and at 1019 x 720 (beside the minimized office), the world's label is there to be checked, and a fixture label is painted",
  async () => {
    const results: Array<{
      state: string;
      sampled: number;
      byBanner: Array<{ kind: string; text: string; at: { x: number; y: number }; by: string }>;
      others: string[];
      column: ColumnFacts;
      labels: LabelFacts[];
    }> = [];
    const measure = async (page: Page, state: string) => {
      // the legend mounts once the ambient roster has a summary, so the column is complete only after that
      await waitFor(
        `${state}: the ambient legend`,
        async () => (await page.$(T('ambient-legend'))) !== null,
        30_000,
      );
      await page.waitForTimeout(1200); // settled
      const o = await occlusion(page, T('di-mr'));
      const column = await columnOverWorkspace(page);
      const labels = await fixtureLabelFacts(page);
      const byBanner = o.covered.filter((c) => c.by === 'di-fixture-banner');
      const others = o.covered
        .filter((c) => c.by !== 'di-fixture-banner')
        .map((c) => `${c.by} over ${c.text} at ${c.at.x},${c.at.y}`);
      console.log(
        `[column over workspace ${state}] sampled ${o.sampled}; covered by the banner ${byBanner.length}: ${JSON.stringify(byBanner)}; covered by anything else ${others.length}: ${JSON.stringify(others)}; column z ${column.zStack}, workspace z ${column.zWorkspace}; column items against the workspace: ${JSON.stringify(column.items)}; workspace titles, controls and wording with the column on top: ${JSON.stringify(column.workspaceElementsUnderColumn)}; labels: ${labels.map(labelLine).join(' | ')}`,
      );
      results.push({ state, sampled: o.sampled, byBanner, others, column, labels });
    };

    // 1600 x 1000: the workspace docked beside the office, then wider reading
    const wide = await openWindow();
    await openWorkspace(wide);
    await measure(wide, 'workspace docked, 1600 x 1000');
    await wide.click(T('di-mr-wide'));
    await waitFor(
      'wider reading',
      async () => (await wide.getAttribute(T('di-mr-wide'), 'aria-pressed')) === 'true',
      5_000,
    );
    await measure(wide, 'wider reading, 1600 x 1000');

    // 1019 x 720 BESIDE (test L's window): the workspace opened at the ordinary size, the office minimized, then the window narrowed
    const narrow = await openWindow();
    await openWorkspace(narrow);
    await narrow.click(T('di-office-panel-minimize'));
    await narrow.waitForSelector(T('di-office-panel-collapsed'), { timeout: 5_000 });
    await narrow.setViewportSize({ width: 1019, height: 720 });
    await waitFor(
      'the workspace to sit beside, not cover',
      async () => (await narrow.getAttribute(T('di-mr'), 'data-covering')) === 'false',
      5_000,
    );
    await measure(narrow, 'workspace beside the minimized office, 1019 x 720');

    expect(
      results.filter((r) => r.sampled < 40).map((r) => ({ state: r.state, sampled: r.sampled })),
      'the probe sampled fewer than 40 points of the workspace (it must have something to look at)',
    ).toEqual([]);
    // the world's label must be PAINTED here (it stands down only while the workspace covers), or this would pass by hiding it
    expect(
      results
        .filter((r) => !r.labels.find((l) => l.id === 'di-fixture-banner')?.painted)
        .map((r) => ({ state: r.state, banner: labelLine(r.labels[0]) })),
      "states in which the world's fixture label is not painted (it is the thing being checked)",
    ).toEqual([]);
    expect(
      results
        .map((r) => ({ state: r.state, coveredByTheBanner: r.byBanner }))
        .filter((r) => r.coveredByTheBanner.length > 0),
      'points of the request workspace with the fixture label painted on top of them',
    ).toEqual([]);
    expect(
      results.filter((r) => !r.column.present).map((r) => r.state),
      'states in which the column or the workspace was not found (the column probe needs both)',
    ).toEqual([]);
    // NOT asserted here, on purpose: that no box of the column (toolbar, legend, label) meets the workspace's box. That assertion was
    // STRICTER THAN THE CONTRACT ("nothing a user must read or reach is hidden or covered"): a blank area of the workspace under the
    // column is not a defect. It sat near a real defect (text and the Send button under the column at the end of scroll in the Ask tab
    // with a draft) and did not identify it: it failed in the right place for the wrong reason. The gate for the contract is the
    // content-and-exposure test below ("nothing the user must read or reach ... is hidden or covered by the world's column"). This is a
    // correction to the contract, not a weakening; the original failed run (diMissionRequest-whole.RUN.033558.log) is retained.
    // What stays here: the world's label painted, nothing painted by the label over the workspace, and no title, control or wording of
    // the workspace under the column (the column probe's per-element check), with the sampling floors and the positive companion.
    expect(
      results
        .map((r) => ({ state: r.state, covered: r.column.workspaceElementsUnderColumn }))
        .filter((r) => r.covered.length > 0),
      "titles, controls and wording of the docked workspace with the world's column painted on top of them",
    ).toEqual([]);
    expect(
      results
        .filter((r) => !r.labels.some((l) => l.painted))
        .map((r) => ({ state: r.state, labels: r.labels.map(labelLine) })),
      'states in which no fixture label is painted (the positive companion)',
    ).toEqual([]);
  },
  STEPS_MS,
);

/*
 * THE CONTRACT: nothing a user must READ or REACH in the request workspace is hidden or covered by the world's bottom-left column
 * (the toolbar, the fixture label, the ambient legend, and the City popover when it is open). It is NOT "zero box overlap": a blank
 * area of the workspace under the column is allowed. What is not allowed is content, or part of a control, under it.
 *
 * WHAT THE DEFECT IS: a VISUAL EXPOSURE defect, not a keyboard failure. A real Tab DOES land on Send (that is asserted, and must hold
 * before and after any fix); what the column does is leave part of it, and of the text beside it, covered at the end of scroll.
 *
 * Rendered and EXHAUSTIVE over the listed states, not sampled. In the Ask tab WITH A DRAFT (the state whose content reaches the
 * corner), in wider reading, in three real windows with the office open (1600 x 1000, and the squeezed 1100 x 800 and 1200 x 800:
 * the layout is the product's, the sizes are only the windows), at scroll top, middle and END (the end decides whether a user can
 * scroll the content clear):
 *   (a) every text-node rect (Range client rects of every visible text node) and every focusable control's box in the workspace,
 *       taken as the part actually visible (clipped by its scroll containers and the window), is intersected with EVERY box of the
 *       column; none may meet one. At the END the three things found covered are also asserted BY NAME: the start of the composer's
 *       label ("Tell the ..."), the helper sentence ("Write what you want done first."), and the Send button;
 *   (b) every focusable control of the workspace is hit-tested on a grid over its own visible box with the REAL pointer rules.
 *       Controls are rounded, so a point at a corner can legitimately miss one; each point is therefore classified as on the control,
 *       on a column element, on some other element, or a corner point OUTSIDE the control's rounded shape (modelled from its computed
 *       border radius). A point within 1 px of a rounded edge that lands on something other than the control or the column is
 *       ambiguous (anti-aliased hit testing at a curve) and is reported separately, not asserted. Required: ZERO points on any
 *       column element, and every other point inside the rounded shape lands on the control.
 *       The totals are reported. A floor on the controls and points examined and the Send button's presence are the positive companion;
 *   (c) a real Tab lands on Send (document.activeElement is Send), the browser scrolls it into view, and then Send's box does not
 *       meet the column and no point inside Send's rounded shape lands on a column element.
 * LIMITS (the numbers hold for these states only): exhaustive over the three windows, the Ask tab with a draft, and scroll top,
 * middle and END. NOT visited: long conversations, error / start / result states, scroll positions between top, middle and END,
 * other window heights, the City popover open (it is enumerated if present, not opened here), the painted focus ring (boxes and
 * hit points are measured, not ring pixels), and screen readers. The Requests tab states were found clear in the classification
 * and are not re-proved here.
 */
interface Box4 {
  left: number;
  top: number;
  right: number;
  bottom: number;
}
interface WorkspaceScan {
  workspace: Box4 | null;
  texts: Array<Box4 & { text: string }>;
  controls: Array<{ id: string; visible: Box4 }>;
  column: Array<Box4 & { id: string }>;
}
interface ControlReach {
  id: string;
  total: number;
  onControl: number;
  onColumn: number;
  onOther: number;
  cornerOutside: number;
  /** points within 1 px of a rounded edge that land on a non-column, non-control element: ambiguous (anti-aliased hit testing), reported, not asserted */
  edgeBand: number;
  coveredBy: string[];
}
const COLUMN_CONTROL_SELECTOR =
  'button, a[href], input, select, textarea, summary, [role="tab"], [role="button"], [tabindex]:not([tabindex="-1"])';

const scanWorkspace = (page: Page): Promise<WorkspaceScan> =>
  page.evaluate((controlSelector) => {
    const toBox = (r: DOMRect): Box4 => ({
      left: r.left,
      top: r.top,
      right: r.right,
      bottom: r.bottom,
    });
    const meet = (a: Box4, b: Box4): Box4 | null => {
      const left = Math.max(a.left, b.left);
      const top = Math.max(a.top, b.top);
      const right = Math.min(a.right, b.right);
      const bottom = Math.min(a.bottom, b.bottom);
      return right > left && bottom > top ? { left, top, right, bottom } : null;
    };
    const ws = document.querySelector<HTMLElement>('[data-testid="di-mr"]');
    const stack = document.querySelector<HTMLElement>('[data-testid="di-world-stack"]');
    const win: Box4 = { left: 0, top: 0, right: window.innerWidth, bottom: window.innerHeight };
    const visiblePart = (box: Box4, from: Element | null): Box4 | null => {
      let b: Box4 | null = box;
      for (let a: Element | null = from; a && b; a = a.parentElement) {
        const cs = getComputedStyle(a);
        if (cs.overflowX !== 'visible' || cs.overflowY !== 'visible') {
          const ar = a.getBoundingClientRect();
          b = meet(b, {
            left: ar.left + a.clientLeft,
            top: ar.top + a.clientTop,
            right: ar.left + a.clientLeft + a.clientWidth,
            bottom: ar.top + a.clientTop + a.clientHeight,
          });
        }
        if (a === ws) break;
      }
      return b ? meet(b, win) : null;
    };
    const idOf = (el: Element): string =>
      el.getAttribute('data-testid') ??
      el.getAttribute('aria-label') ??
      ((el.textContent ?? '').replace(/\s+/g, ' ').trim().slice(0, 30) ||
        `${el.tagName.toLowerCase()}.${String(el.getAttribute('class') ?? '').split(' ')[0]}`);
    const out: WorkspaceScan = { workspace: null, texts: [], controls: [], column: [] };
    if (ws && ws.getClientRects().length > 0) {
      out.workspace = toBox(ws.getBoundingClientRect());
      const walker = document.createTreeWalker(ws, NodeFilter.SHOW_TEXT);
      for (let n = walker.nextNode(); n; n = walker.nextNode()) {
        const text = (n.textContent ?? '').replace(/\s+/g, ' ').trim();
        const parent = n.parentElement;
        if (text === '' || !parent || !parent.checkVisibility({ checkVisibilityCSS: true }))
          continue;
        const range = document.createRange();
        range.selectNodeContents(n);
        for (const r of Array.from(range.getClientRects())) {
          if (r.width < 0.5 || r.height < 0.5) continue;
          const v = visiblePart(toBox(r), parent);
          if (v) out.texts.push({ text: text.slice(0, 44), ...v });
        }
      }
      for (const el of Array.from(ws.querySelectorAll<HTMLElement>(controlSelector))) {
        if (!el.checkVisibility({ checkVisibilityCSS: true }) || el.getClientRects().length === 0)
          continue;
        const r = el.getBoundingClientRect();
        if (r.width < 0.5 || r.height < 0.5) continue;
        const v = visiblePart(toBox(r), el);
        if (v) out.controls.push({ id: idOf(el), visible: v });
      }
    }
    // The column's boxes, looking THROUGH `display: contents` wrappers (the world's controls gate has no box of its own). The column is the stack (the toolbar and the legend) AND the left strip's pinned
    // footer: the fixture label lives there since candidate 7 (it left the stack), and it is still a box at the world's bottom-left, so it is still measured against the workspace. An empty footer has no box.
    const footer = document.querySelector<HTMLElement>('[data-testid="di-world-rail-footer"]');
    const leaves = (parent: Element): Element[] =>
      Array.from(parent.children).flatMap((c) =>
        getComputedStyle(c).display === 'contents' ? leaves(c) : [c],
      );
    for (const holder of [stack, footer]) {
      if (!holder || holder.getClientRects().length === 0) continue;
      for (const item of leaves(holder)) {
        if (
          !(item as HTMLElement).checkVisibility({ checkVisibilityCSS: true }) ||
          item.getClientRects().length === 0
        )
          continue;
        out.column.push({ id: idOf(item), ...toBox(item.getBoundingClientRect()) });
      }
    }
    return out;
  }, COLUMN_CONTROL_SELECTOR);

/**
 * A grid of REAL hit tests over each focusable control's visible box. Each point is on the control, on a column element, on some other
 * element, or a corner point outside the control's rounded shape (its computed border radii model the shape).
 */
const controlReach = (page: Page): Promise<ControlReach[]> =>
  page.evaluate((controlSelector) => {
    const ws = document.querySelector<HTMLElement>('[data-testid="di-mr"]');
    const stack = document.querySelector<HTMLElement>('[data-testid="di-world-stack"]');
    if (!ws) return [];
    const win = { left: 0, top: 0, right: window.innerWidth, bottom: window.innerHeight };
    const out: ControlReach[] = [];
    for (const el of Array.from(ws.querySelectorAll<HTMLElement>(controlSelector))) {
      if (!el.checkVisibility({ checkVisibilityCSS: true }) || el.getClientRects().length === 0)
        continue;
      const r = el.getBoundingClientRect();
      if (r.width < 0.5 || r.height < 0.5) continue;
      // the part of the control that is visible: inside every scroll container up to the workspace, and the window
      let box = { left: r.left, top: r.top, right: r.right, bottom: r.bottom };
      for (let a: Element | null = el.parentElement; a; a = a.parentElement) {
        const cs = getComputedStyle(a);
        if (cs.overflowX !== 'visible' || cs.overflowY !== 'visible') {
          const ar = a.getBoundingClientRect();
          box = {
            left: Math.max(box.left, ar.left + a.clientLeft),
            top: Math.max(box.top, ar.top + a.clientTop),
            right: Math.min(box.right, ar.left + a.clientLeft + a.clientWidth),
            bottom: Math.min(box.bottom, ar.top + a.clientTop + a.clientHeight),
          };
        }
        if (a === ws) break;
      }
      box = {
        left: Math.max(box.left, win.left),
        top: Math.max(box.top, win.top),
        right: Math.min(box.right, win.right),
        bottom: Math.min(box.bottom, win.bottom),
      };
      if (box.right <= box.left || box.bottom <= box.top) continue;
      // the control's painted shape: its box with its computed corner radii
      const cs = getComputedStyle(el);
      const radius = (value: string, dim: number): number => {
        const first = value.split(' ')[0];
        const n = parseFloat(first);
        if (Number.isNaN(n)) return 0;
        return Math.min(first.endsWith('%') ? (n / 100) * dim : n, Math.min(r.width, r.height) / 2);
      };
      const rtl = radius(cs.borderTopLeftRadius, r.width);
      const rtr = radius(cs.borderTopRightRadius, r.width);
      const rbr = radius(cs.borderBottomRightRadius, r.width);
      const rbl = radius(cs.borderBottomLeftRadius, r.width);
      // inside the painted shape; `margin` shrinks the rounded corners by that many px (a point inside by less than the margin is on the edge)
      const insideShape = (x: number, y: number, margin = 0): boolean => {
        const corner = (cx: number, cy: number, rad: number, inCorner: boolean): boolean =>
          !inCorner || rad === 0 || (x - cx) ** 2 + (y - cy) ** 2 <= Math.max(0, rad - margin) ** 2;
        return (
          corner(r.left + rtl, r.top + rtl, rtl, x < r.left + rtl && y < r.top + rtl) &&
          corner(r.right - rtr, r.top + rtr, rtr, x > r.right - rtr && y < r.top + rtr) &&
          corner(r.right - rbr, r.bottom - rbr, rbr, x > r.right - rbr && y > r.bottom - rbr) &&
          corner(r.left + rbl, r.bottom - rbl, rbl, x < r.left + rbl && y > r.bottom - rbl)
        );
      };
      const cols = Math.min(28, Math.max(3, Math.round((box.right - box.left) / 4)));
      const rows = Math.min(6, Math.max(2, Math.round((box.bottom - box.top) / 6)));
      const tally = { onControl: 0, onColumn: 0, onOther: 0, cornerOutside: 0, edgeBand: 0 };
      const coveredBy = new Set<string>();
      for (let iy = 0; iy < rows; iy += 1)
        for (let ix = 0; ix < cols; ix += 1) {
          const x = box.left + ((ix + 0.5) / cols) * (box.right - box.left);
          const y = box.top + ((iy + 0.5) / rows) * (box.bottom - box.top);
          const top = document.elementFromPoint(x, y);
          const who =
            top === null
              ? 'nothing'
              : (top.getAttribute('data-testid') ??
                `${top.tagName.toLowerCase()}.${String(top.getAttribute('class') ?? '').split(' ')[0]}`);
          if (top !== null && el.contains(top)) tally.onControl += 1;
          else if (top !== null && stack !== null && (stack === top || stack.contains(top))) {
            tally.onColumn += 1;
            if (coveredBy.size < 3) coveredBy.add(`column: ${who}`);
          } else if (!insideShape(x, y)) tally.cornerOutside += 1;
          else if (!insideShape(x, y, 1)) tally.edgeBand += 1;
          else {
            tally.onOther += 1;
            if (coveredBy.size < 3)
              coveredBy.add(
                `${who} at +${Math.round((x - r.left) * 10) / 10},+${Math.round((y - r.top) * 10) / 10} of a ${Math.round(r.width * 10) / 10} x ${Math.round(r.height * 10) / 10} box, radii ${Math.round(rtl * 10) / 10}/${Math.round(rtr * 10) / 10}/${Math.round(rbr * 10) / 10}/${Math.round(rbl * 10) / 10}`,
              );
          }
        }
      out.push({
        id:
          el.getAttribute('data-testid') ??
          el.getAttribute('aria-label') ??
          ((el.textContent ?? '').trim().slice(0, 30) || el.tagName.toLowerCase()),
        total: rows * cols,
        ...tally,
        coveredBy: [...coveredBy],
      });
    }
    return out;
  }, COLUMN_CONTROL_SELECTOR);

const boxMeet = (a: Box4, b: Box4) => {
  const w = Math.min(a.right, b.right) - Math.max(a.left, b.left);
  const h = Math.min(a.bottom, b.bottom) - Math.max(a.top, b.top);
  return w > 0.5 && h > 0.5 ? { w: Math.round(w * 10) / 10, h: Math.round(h * 10) / 10 } : null;
};
const setBodyScroll = (page: Page, mode: 'top' | 'middle' | 'end') =>
  page.evaluate((m) => {
    const body = document.querySelector<HTMLElement>('[data-testid="di-mr-body"]')!;
    const max = body.scrollHeight - body.clientHeight;
    body.scrollTop = m === 'top' ? 0 : m === 'middle' ? Math.round(max / 2) : max;
    return { scrollTop: body.scrollTop, max };
  }, mode);

test(
  "AC-UX-13 / world column, REAL APP: nothing the user must read or reach in the request workspace (Ask tab with a draft) is hidden or covered by the world's column, at 1600 x 1000, 1100 x 800 and 1200 x 800, scroll top, middle and end",
  async () => {
    const intersections: Array<{
      window: string;
      at: string;
      kind: string;
      what: string;
      column: string;
      px: string;
    }> = [];
    const covered: Array<{
      window: string;
      at: string;
      control: string;
      onControl: number;
      onColumn: number;
      onOther: number;
      cornerOutside: number;
      edgeBand: number;
      of: number;
      coveredBy: string[];
    }> = [];
    const thin: Array<{
      window: string;
      at: string;
      texts: number;
      controls: number;
      points: number;
      columnBoxes: number;
    }> = [];
    // the end of scroll, by name: what the classification found under the column
    const NAMED_AT_END: Array<{
      item: string;
      kind: 'text' | 'control';
      match: (what: string) => boolean;
    }> = [
      {
        item: "the start of the composer's label (Tell the ...)",
        kind: 'text',
        match: (what) => what.startsWith('Tell the'),
      },
      {
        item: 'the helper sentence (Write what you want done first.)',
        kind: 'text',
        match: (what) => what === 'Write what you want done first.',
      },
      { item: 'the Send button', kind: 'control', match: (what) => what === 'di-mr-send' },
    ];
    const namedAtEnd: Array<{
      window: string;
      item: string;
      found: boolean;
      meetsColumn: string[];
    }> = [];
    const tabToSend: Array<{
      window: string;
      landedOnSend: boolean;
      box: string;
      meetsColumn: string[];
      points: string;
    }> = [];
    for (const [w, h] of [
      [1600, 1000],
      [1100, 800],
      [1200, 800],
    ] as const) {
      const window = `${w} x ${h}`;
      const page = await openWindow({ width: w, height: h });
      await openWorkspace(page);
      await page.click(T('di-mr-wide'));
      await waitFor(
        'wider reading',
        async () => (await page.getAttribute(T('di-mr-wide'), 'aria-pressed')) === 'true',
        5_000,
      );
      await waitFor(
        'the ambient legend',
        async () => (await page.$(T('ambient-legend'))) !== null,
        30_000,
      );
      await ask(page, 'make the readiness probe stable', 'The readiness probe.');
      await page.waitForTimeout(1200);
      for (const mode of ['top', 'middle', 'end'] as const) {
        const { scrollTop, max } = await setBodyScroll(page, mode);
        expect(
          max >= 100,
          `${window}: the draft makes the workspace scroll by ${max} px (it must scroll, or top, middle and end are one place)`,
        ).toBe(true);
        await page.waitForTimeout(300);
        const at = `${mode} (scrollTop ${scrollTop} of ${max})`;
        const scan = await scanWorkspace(page);
        const reach = await controlReach(page);
        const points = reach.reduce((n, c) => n + c.total, 0);
        thin.push({
          window,
          at,
          texts: scan.texts.length,
          controls: reach.length,
          points,
          columnBoxes: scan.column.length,
        });
        for (const col of scan.column) {
          for (const t of scan.texts) {
            const m = boxMeet(t, col);
            if (m)
              intersections.push({
                window,
                at,
                kind: 'text',
                what: t.text,
                column: col.id,
                px: `${m.w} x ${m.h}`,
              });
          }
          for (const c of scan.controls) {
            const m = boxMeet(c.visible, col);
            if (m)
              intersections.push({
                window,
                at,
                kind: 'control',
                what: c.id,
                column: col.id,
                px: `${m.w} x ${m.h}`,
              });
          }
        }
        for (const c of reach)
          if (c.onColumn > 0 || c.onOther > 0)
            covered.push({
              window,
              at,
              control: c.id,
              onControl: c.onControl,
              onColumn: c.onColumn,
              onOther: c.onOther,
              cornerOutside: c.cornerOutside,
              edgeBand: c.edgeBand,
              of: c.total,
              coveredBy: c.coveredBy,
            });
        const sum = reach.reduce(
          (a, c) => ({
            onControl: a.onControl + c.onControl,
            onColumn: a.onColumn + c.onColumn,
            onOther: a.onOther + c.onOther,
            cornerOutside: a.cornerOutside + c.cornerOutside,
            edgeBand: a.edgeBand + c.edgeBand,
          }),
          { onControl: 0, onColumn: 0, onOther: 0, cornerOutside: 0, edgeBand: 0 },
        );
        console.log(
          `[column contract ${window} ${at}] workspace ${scan.workspace ? Math.round(scan.workspace.left) + '..' + Math.round(scan.workspace.right) : 'n/a'}; column boxes ${scan.column.map((c) => `${c.id} ${Math.round(c.left)}..${Math.round(c.right)} x ${Math.round(c.top)}..${Math.round(c.bottom)}`).join(' | ')}; ${scan.texts.length} text rects and ${scan.controls.length} control boxes examined; ${points} real hit points over ${reach.length} controls (on the control ${sum.onControl}, on a column element ${sum.onColumn}, on another element ${sum.onOther}, corner points outside the rounded shape ${sum.cornerOutside}, within 1 px of a rounded edge on another element ${sum.edgeBand})`,
        );
        if (mode === 'end')
          for (const n of NAMED_AT_END) {
            const hits =
              n.kind === 'text'
                ? scan.texts.filter((t) => n.match(t.text))
                : scan.controls
                    .filter((c) => n.match(c.id))
                    .map((c) => ({ ...c.visible, text: c.id }));
            namedAtEnd.push({
              window,
              item: n.item,
              found: hits.length > 0,
              meetsColumn: hits.flatMap((hit) =>
                scan.column
                  .map((col) => ({ col, m: boxMeet(hit, col) }))
                  .filter((x) => x.m)
                  .map((x) => `${x.col.id}: ${x.m!.w} x ${x.m!.h}`),
              ),
            });
          }
      }
      // (c) a real Tab to Send, then where the browser leaves it
      await page.evaluate(() => {
        const sentinel = document.createElement('span');
        sentinel.setAttribute('data-tab-sentinel', 'true');
        sentinel.tabIndex = -1;
        document.body.prepend(sentinel);
        sentinel.focus();
      });
      let landedOnSend = false;
      for (let i = 0; i < 200 && !landedOnSend; i += 1) {
        await page.keyboard.press('Tab');
        landedOnSend =
          (await page.evaluate(() => document.activeElement?.getAttribute('data-testid'))) ===
          'di-mr-send';
      }
      await page.evaluate(() =>
        document.querySelectorAll('[data-tab-sentinel]').forEach((e) => e.remove()),
      );
      await page.waitForTimeout(300);
      const scanAfterTab = await scanWorkspace(page);
      const send = scanAfterTab.controls.find((c) => c.id === 'di-mr-send');
      const sendReach = (await controlReach(page)).find((c) => c.id === 'di-mr-send');
      tabToSend.push({
        window,
        landedOnSend,
        box: send
          ? `${Math.round(send.visible.left)},${Math.round(send.visible.top)}..${Math.round(send.visible.right)},${Math.round(send.visible.bottom)}`
          : 'not found',
        meetsColumn: send
          ? scanAfterTab.column
              .map((col) => ({ col, m: boxMeet(send.visible, col) }))
              .filter((x) => x.m)
              .map((x) => `${x.col.id}: ${x.m!.w} x ${x.m!.h}`)
          : ['Send not found'],
        points: sendReach
          ? `on Send ${sendReach.onControl}, on a column element ${sendReach.onColumn}, on another element ${sendReach.onOther}, corner points outside the shape ${sendReach.cornerOutside}, within 1 px of a rounded edge on another element ${sendReach.edgeBand}, of ${sendReach.total}`
          : 'no grid',
      });
      await page.close();
    }
    console.log(
      `[column contract] intersections ${intersections.length}: ${JSON.stringify(intersections)}; controls with points on a column element or on another element ${covered.length}: ${JSON.stringify(covered)}; the three named items at the end of scroll: ${JSON.stringify(namedAtEnd)}; real Tab to Send: ${JSON.stringify(tabToSend)}`,
    );
    // positive companions: the checks must have something to do (a floor on what was examined), or an empty list would prove nothing
    expect(
      thin.filter((s) => s.texts < 30 || s.controls < 6 || s.points < 500 || s.columnBoxes < 3),
      'scans that examined too little: fewer than 30 text rects, 6 controls, 500 hit points or 3 column boxes (the lowest values seen were 36, 7, 879 and 3)',
    ).toEqual([]);
    expect(
      tabToSend.filter((t) => !t.landedOnSend),
      'a real Tab did not land on Send (it must, before and after any fix)',
    ).toEqual([]);
    expect(
      namedAtEnd.filter((n) => !n.found),
      'items the end-of-scroll check looks for that were not found (the check must be able to find them)',
    ).toEqual([]);
    // the contract
    expect(
      namedAtEnd.filter((n) => n.meetsColumn.length > 0),
      "at the END of scroll, the composer label's start, the helper sentence or the Send button is under a box of the world's column",
    ).toEqual([]);
    expect(
      intersections,
      "workspace text or control boxes that meet a box of the world's column (what is under it is hidden or covered)",
    ).toEqual([]);
    expect(
      covered,
      'workspace controls with real hit-test points on a column element, or inside their rounded shape on something other than the control',
    ).toEqual([]);
    expect(
      tabToSend.filter(
        (t) =>
          t.meetsColumn.length > 0 ||
          !/on a column element 0, on another element 0,/.test(t.points),
      ),
      'after a real Tab to Send the browser leaves it with its box meeting the column, or with points inside its rounded shape on a column element or on something other than Send',
    ).toEqual([]);
  },
  STEPS_MS,
);

/*
 * THE CAP: the column's width is capped at the toolbar's right edge (276 px from the window's left edge: the column's 10 px left
 * offset plus 266), so the legend, whose width depends on live counts, WRAPS instead of growing past it, and the world keeps what
 * its own column needs.
 * What this proves: with the legend as the app renders it (its natural, unwrapped width is measured here and must exceed the cap,
 * so the check has something to do) its right edge is at most 276, it wraps to more than one line (its height is more than one
 * line's), its text stays inside its box, and nothing in the column reaches past 276. The same with its text made three times as
 * long. LIMITS: the long text is set in the DOM of the page (the roster counts are inside the app and are not reachable from
 * outside), so the second case proves the CSS cap holds for longer text, not that the app produces such text; and it is measured
 * in one window (1600 x 1000), the cap being independent of the window.
 */
test(
  "AC-UX-13 / world column, REAL APP: the world column is capped at 276 px from the window's left edge and the ambient legend wraps instead of growing past it (as rendered, and with longer text)",
  async () => {
    const CAP_RIGHT_PX = 276;
    const page = await openWindow();
    await waitFor(
      'the ambient legend',
      async () => (await page.$(T('ambient-legend'))) !== null,
      30_000,
    );
    await page.waitForTimeout(1500);
    const measure = (longer: boolean) =>
      page.evaluate(
        ([makeLonger, cap]) => {
          const legend = document.querySelector<HTMLElement>('[data-testid="ambient-legend"]')!;
          const stack = document.querySelector<HTMLElement>('[data-testid="di-world-stack"]')!;
          if (makeLonger)
            legend.textContent = `${legend.textContent ?? ''} · ${legend.textContent ?? ''} · ${legend.textContent ?? ''}`;
          // its NATURAL width and one line's height: a hidden clone laid out without a wrap
          const clone = legend.cloneNode(true) as HTMLElement;
          clone.removeAttribute('data-testid');
          clone.style.cssText =
            'position:fixed;left:0;top:0;visibility:hidden;width:max-content;max-width:none;white-space:nowrap;';
          document.body.append(clone);
          const natural = clone.getBoundingClientRect();
          clone.remove();
          const r = legend.getBoundingClientRect();
          const walker = document.createTreeWalker(legend, NodeFilter.SHOW_TEXT);
          let textRight = 0;
          for (let n = walker.nextNode(); n; n = walker.nextNode()) {
            const range = document.createRange();
            range.selectNodeContents(n);
            for (const t of Array.from(range.getClientRects()))
              textRight = Math.max(textRight, t.right);
          }
          const items = (function leaves(parent: Element): Element[] {
            return Array.from(parent.children).flatMap((c) =>
              getComputedStyle(c).display === 'contents' ? leaves(c) : [c],
            );
          })(stack).filter(
            (c) =>
              (c as HTMLElement).checkVisibility({ checkVisibilityCSS: true }) &&
              c.getClientRects().length > 0,
          );
          return {
            naturalWidth: Math.round(natural.width * 10) / 10,
            oneLineHeight: Math.round(natural.height * 10) / 10,
            right: Math.round(r.right * 10) / 10,
            width: Math.round(r.width * 10) / 10,
            height: Math.round(r.height * 10) / 10,
            textRight: Math.round(textRight * 10) / 10,
            overflowsSideways: legend.scrollWidth > legend.clientWidth + 1,
            columnRights: items.map(
              (c) =>
                `${c.getAttribute('data-testid') ?? c.tagName.toLowerCase()} ${Math.round(c.getBoundingClientRect().right * 10) / 10}`,
            ),
            widestColumnItemRight: Math.max(...items.map((c) => c.getBoundingClientRect().right)),
            cap,
          };
        },
        [longer, CAP_RIGHT_PX] as const,
      );
    const asRendered = await measure(false);
    const longer = await measure(true);
    console.log(
      `[column cap] as rendered: ${JSON.stringify(asRendered)}; with three times the text: ${JSON.stringify(longer)}`,
    );
    expect(
      asRendered.naturalWidth > CAP_RIGHT_PX - 10,
      `the legend as rendered has a natural width of ${asRendered.naturalWidth} px, which must exceed the cap of ${CAP_RIGHT_PX - 10} px (otherwise this proves nothing)`,
    ).toBe(true);
    // all four checks for both cases are collected and asserted together, so a red run shows every failing number at once
    const failures: Array<{ case: string; check: string; measured: string }> = [];
    for (const [name, m] of [
      ['as rendered', asRendered],
      ['with three times the text', longer],
    ] as const) {
      if (!(m.right <= CAP_RIGHT_PX + 0.75))
        failures.push({
          case: name,
          check: `the legend ends at or before x = ${CAP_RIGHT_PX}`,
          measured: `it ends at x = ${m.right} (natural width ${m.naturalWidth})`,
        });
      if (!(m.height > m.oneLineHeight + 4))
        failures.push({
          case: name,
          check: 'the legend wraps (taller than one line)',
          measured: `height ${m.height} px against ${m.oneLineHeight} px for one line`,
        });
      if (!(m.textRight <= m.right + 0.75 && !m.overflowsSideways))
        failures.push({
          case: name,
          check: "the legend's text stays inside its box without sideways overflow",
          measured: `text to x = ${m.textRight}, box to ${m.right}, overflows sideways ${m.overflowsSideways}`,
        });
      if (!(m.widestColumnItemRight <= CAP_RIGHT_PX + 0.75))
        failures.push({
          case: name,
          check: `nothing in the column reaches past x = ${CAP_RIGHT_PX}`,
          measured: `the widest ends at x = ${m.widestColumnItemRight} (${m.columnRights.join(', ')})`,
        });
    }
    expect(failures, `column cap checks that fail (${failures.length} of 8)`).toEqual([]);
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

// ── The covering workspace and the world's own overlays ──────────────────────────────────────────

/** What a world overlay or a dialog does to the workspace, from the real stacking order on screen (a hit test with pointer events forced on, so a click-through overlay counts). */
async function stackingAboveWorkspace(page: Page) {
  return page.evaluate(() => {
    const style = document.createElement('style');
    style.id = 'test-force-pointer-events';
    style.textContent = '* { pointer-events: auto !important; }';
    document.head.appendChild(style);
    try {
      // the workspace may be CLOSED (the legend is checked after it closes): then nothing is compared against it
      const ws = document.querySelector('[data-testid="di-mr"]') as HTMLElement | null;
      const describe = (el: Element) =>
        `${el.tagName.toLowerCase()}${el.getAttribute('data-testid') ? `[${el.getAttribute('data-testid')}]` : ''}.${String(el.className).slice(0, 40)} z=${getComputedStyle(el).zIndex}`;
      const occluders: string[] = [];
      for (const el of ws ? document.querySelectorAll<HTMLElement>('body *') : []) {
        if (!ws) break;
        if (ws.contains(el) || el.contains(ws)) continue;
        const cs = getComputedStyle(el);
        if (cs.position !== 'fixed' && cs.position !== 'absolute') continue;
        if (cs.visibility === 'hidden' || cs.display === 'none' || Number(cs.opacity) === 0) continue;
        const r = el.getBoundingClientRect();
        if (r.width < 1 || r.height < 1) continue;
        if (r.right <= 0 || r.bottom <= 0 || r.left >= innerWidth || r.top >= innerHeight) continue;
        const x = Math.min(Math.max(r.left + r.width / 2, 0), innerWidth - 1);
        const y = Math.min(Math.max(r.top + r.height / 2, 0), innerHeight - 1);
        const stack = document.elementsFromPoint(x, y);
        const own = stack.findIndex((e) => el.contains(e));
        const inWs = stack.findIndex((e) => ws.contains(e));
        if (own !== -1 && inWs !== -1 && own < inWs) occluders.push(describe(el));
      }
      const legend = document.querySelector('[data-testid="ambient-legend"]') as HTMLElement | null;
      let legendFacts: {
        present: boolean;
        visible: boolean;
        yielded: boolean;
        /** The COMPUTED z-index of the bottom-left column that holds the legend (what the browser applies, not the class written in the source). */
        z: string;
        /**
         * The rendered width of the WORLD'S OWN BOX: the canvas fills it (the file's worldWidth helper), -1 when there is no canvas. NOT the legend's gate parent: the
         * gate is display: contents (no box of its own), so its parent is whatever sits beneath it, which is the bottom-left column (266 px since it was bounded).
         */
        worldWidth: number;
        belowWorkspace: boolean;
        aboveWorld: boolean;
      } = {
        present: false,
        visible: false,
        yielded: false,
        z: '',
        worldWidth: -1,
        belowWorkspace: false,
        aboveWorld: false,
      };
      if (legend) {
        const r = legend.getBoundingClientRect();
        const stack = document.elementsFromPoint(r.left + r.width / 2, r.top + r.height / 2);
        const own = stack.findIndex((e) => legend.contains(e));
        const inWs = ws ? stack.findIndex((e) => ws.contains(e)) : -1;
        const cs = getComputedStyle(legend);
        legendFacts = {
          present: true,
          visible: r.width > 0 && r.height > 0 && cs.visibility !== 'hidden' && Number(cs.opacity) > 0,
          // the legend is one of the world's controls: where the world has no room for them they are inert and hidden
          yielded: legend.closest('[data-di-world-controls="yielded"]') !== null,
          // The layer is the left strip's FRAME's: the frame (absolute, layer 30) holds the scroller and the pinned footer, and the scroller (.di-world-rail, the legend's nearest strip ancestor) states none.
          // (Before candidate 7 the strip was one element, and before that the bottom-left column.) With no frame the fallback reads the legend itself, which states none, and the assertion fails.
          z: getComputedStyle(legend.closest('[data-testid="di-world-rail-frame"]') ?? legend)
            .zIndex,
          worldWidth: Math.round(document.querySelector('canvas')?.getBoundingClientRect().width ?? -1),
          belowWorkspace: own !== -1 && inWs !== -1 && inWs < own,
          aboveWorld: own === 0,
        };
      }
      const heading = ws ? document.querySelector('[data-testid="di-mr-heading"]') : null;
      const h = heading?.getBoundingClientRect();
      const headingTop = h ? document.elementsFromPoint(h.left + h.width / 2, h.top + h.height / 2)[0] : null;
      const banner = document.querySelector('.di-fixture-banner') as HTMLElement | null;
      return {
        occluders,
        legend: legendFacts,
        headingOnTop: !!ws && !!headingTop && ws.contains(headingTop),
        bannerVisibility: banner ? getComputedStyle(banner).visibility : null,
      };
    } finally {
      document.getElementById('test-force-pointer-events')?.remove();
    }
  });
}

test(
  'L. A covering workspace sits above every world overlay and below the dialogs: nothing letters over its text, a dialog opened before the window narrowed stays above it, the legend returns when it closes, and focus, Escape and Close behave (1019x720 and 599x700, sidebar shown and hidden; the hidden 1019x720 window is beside, not covering)',
  async () => {
    for (const collapsed of [false, true]) {
      for (const [width, height, closeWith] of [
        [1019, 720, 'Escape'],
        [599, 700, 'Close'],
      ] as const) {
        // the REAL rule (layoutFor): the workspace covers iff the window minus the side panel (none when hidden) minus the world's reserve (MR_WORLD_KEEP_PX, 276)
        // leaves under 360 px (MR_MIN_BESIDE_PX): below the panel + 276 + 360 px with the side panel shown (1064 at 428), below 636 px with it hidden. So 1019x720 covers only with the side panel shown.
        // BEFORE this was written with the literals 420, 240 and 360 (1020 and 600): it agreed with the real rule at the two sampled widths only because both lie below
        // BOTH the old and the new boundary, and would have gone on agreeing while the reserve moved. It is now derived, and the boundaries are pinned independently, by
        // literals for the reserve and the pane (the panel term derived from DI_PANEL_WIDTH_PX since pass C), in G2 and in diMissionRequestModel.test.ts (THE RESERVE).
        const covers = width - (collapsed ? 0 : DI_PANEL_WIDTH_PX) - MR_WORLD_KEEP_PX < MR_MIN_BESIDE_PX;
        const where = `${collapsed ? 'sidebar hidden' : 'sidebar shown'} · ${width}x${height} (${covers ? 'covering' : 'beside'})`;
        const page = await openWindow();
        // the legend mounts once the ambient roster has a summary (it re-reads every second), so wait for it before anything is measured
        await waitFor(`${where}: the ambient legend`, async () => (await page.$(T('ambient-legend'))) !== null, 30_000);
        // the workspace opens beside the side panel in the ordinary window; the sidebar is hidden FIRST where that is the state under test
        await openWorkspace(page);
        if (collapsed) {
          await page.click('[data-testid="di-office-panel-minimize"]');
          await page.waitForSelector(T('di-office-panel-collapsed'));
        }
        // A dialog must stay above the workspace once it covers the window. The toolbar is covered then, so the Settings modal is opened FIRST
        // (the toolbar is visible beside the workspace) and the window is narrowed afterwards.
        await page.getByRole('button', { name: 'Settings', exact: true }).click();
        const stamped = await waitFor(`${where}: the Settings modal`, async () => {
          const found = await page.evaluate(() => {
            const fixed = [...document.querySelectorAll<HTMLElement>('body *')].filter((e) => {
              const cs = getComputedStyle(e);
              return (
                cs.position === 'fixed' &&
                (cs.zIndex === '50' || cs.zIndex === '51') &&
                !e.classList.contains('di-fixture-banner')
              );
            });
            const backdrop = fixed.find((e) => getComputedStyle(e).zIndex === '50');
            const panel = fixed.find((e) => getComputedStyle(e).zIndex === '51');
            backdrop?.setAttribute('data-test-modal-backdrop', '1');
            panel?.setAttribute('data-test-modal', '1');
            return !!backdrop && !!panel;
          });
          return found;
        });
        expect(stamped, `${where}: the Settings modal opened`).toBe(true);
        await page.setViewportSize({ width, height });
        await waitFor(
          `${where}: the layout to settle`,
          async () => (await page.getAttribute(T('di-mr'), 'data-covering')) === String(covers),
        );
        const modal = await page.evaluate(() => {
          const panel = document.querySelector('[data-test-modal]') as HTMLElement;
          const backdrop = document.querySelector('[data-test-modal-backdrop]') as HTMLElement;
          const r = panel.getBoundingClientRect();
          const mid = document.elementsFromPoint(r.left + r.width / 2, r.top + r.height / 2)[0];
          const corner = document.elementsFromPoint(2, 2)[0];
          return { panelOnTop: !!mid && panel.contains(mid), backdropOnTop: corner === backdrop };
        });
        expect(modal.panelOnTop, `${where}: the Settings modal is under the covering workspace`).toBe(true);
        expect(modal.backdropOnTop, `${where}: the modal backdrop is under the covering workspace`).toBe(true);
        await page.click('[data-test-modal] button');
        await page.waitForSelector('[data-test-modal]', { state: 'detached' });

        const layers = await stackingAboveWorkspace(page);
        expect(layers.legend.present, `${where}: the legend is on the page (the check has something to check)`).toBe(true);
        // drawn, unless the world has no room for its controls (the legend among them), which are then neither drawn nor focusable
        expect(
          layers.legend.visible || layers.legend.yielded,
          `${where}: the legend is drawn, or has yielded with the world's controls`,
        ).toBe(true);
        // the legend's layer as the BROWSER applies it: the stacking table pins the class in the source, this pins what is actually drawn
        expect(layers.legend.z, `${where}: the computed z-index of the column that holds the legend`).toBe('30');
        // the legend yields EXACTLY when the world, as rendered, is narrower than the toolbar's right edge (276 px): not for any other reason
        // COVERAGE LIMIT of this guard, stated here so it is not read as broader than it is: it establishes only that a canvas existed and had a measured width, so the yield
        // comparison below used a real number and not the -1 'no canvas' (which reads as narrower than 276 and would pass a legend that yielded for the wrong reason). It does NOT
        // establish that the canvas width IS the world's true width (a wrongly sized canvas would pass it), and no behavioural regression was shown to fail it on its own.
        expect(layers.legend.worldWidth, `${where}: the world's own box was measured (-1 means no canvas, which would read as narrower than 276)`).toBeGreaterThanOrEqual(0);
        expect(
          layers.legend.yielded,
          `${where}: the legend has yielded ${layers.legend.yielded} with a world ${layers.legend.worldWidth}px wide (it yields exactly below 276)`,
        ).toBe(layers.legend.worldWidth < 276);
        const noteInPanel = await exists(page, '[data-testid="di-fixture-banner-in-panel"]');
        expect(layers.headingOnTop, `${where}: the workspace title is covered`).toBe(true);
        if (covers) {
          // No world overlay may letter over the covering workspace's text; the legend is the one that did.
          if (layers.legend.visible)
            expect(layers.legend.belowWorkspace, `${where}: the ambient legend letters over the covering workspace`).toBe(true);
          expect(layers.occluders, `${where}: world overlays over the covering workspace`).toEqual([]);
          // hidden under the covering workspace, or not rendered because the open office leaves the world no room (its words are in the panel)
          expect(
            layers.bannerVisibility === 'hidden' || (layers.bannerVisibility === null && noteInPanel),
            `${where}: the fixture banner is neither hidden nor replaced by the in-panel note while covering (banner ${layers.bannerVisibility}, note ${noteInPanel})`,
          ).toBe(true);
        } else {
          // Beside the side panel the workspace covers nothing: the legend stays above the world, untouched, and the fixture banner stays drawn.
          expect(layers.legend.aboveWorld, `${where}: something covers the legend beside the workspace`).toBe(true);
          expect(layers.bannerVisibility, `${where}: the fixture banner is hidden although the workspace does not cover`).toBe('visible');
        }

        // Close it by the route under test; focus goes to the opener, or to the control that shows the side panel again when it was hidden.
        if (closeWith === 'Escape') {
          await page.focus(T('di-mr-heading'));
          await page.keyboard.press('Escape');
        } else {
          await page.click(T('di-mr-close'));
        }
        await page.waitForSelector(T('di-mr'), { state: 'detached' });
        expect(
          await page.evaluate(() => document.activeElement?.getAttribute('data-testid') ?? document.activeElement?.tagName),
          `${where}: after ${closeWith}, focus is not on the control that opened the workspace`,
        ).toBe(collapsed ? 'di-office-panel-collapsed' : 'di-mr-open');

        // The legend and the fixture banner are back exactly as before, above the world.
        const closed = await stackingAboveWorkspace(page);
        expect(
          closed.legend.present && (closed.legend.visible || closed.legend.yielded),
          `${where}: the legend is back after close (drawn, or yielded with the world's controls)`,
        ).toBe(true);
        if (closed.legend.visible)
          expect(closed.legend.aboveWorld, `${where}: something covers the legend after close`).toBe(true);
        expect(closed.legend.worldWidth, `${where}: after close the world's own box was measured`).toBeGreaterThanOrEqual(0);
        expect(
          closed.legend.yielded,
          `${where}: after close the legend has yielded ${closed.legend.yielded} with a world ${closed.legend.worldWidth}px wide (it yields exactly below 276)`,
        ).toBe(closed.legend.worldWidth < 276);
        const noteAfter = await exists(page, '[data-testid="di-fixture-banner-in-panel"]');
        expect(
          closed.bannerVisibility === 'visible' || (closed.bannerVisibility === null && noteAfter),
          `${where}: the fixture label is back after close (banner ${closed.bannerVisibility}, in-panel note ${noteAfter})`,
        ).toBe(true);
        await closeWindow(page);
      }
    }
  },
  STEPS_MS,
);
/*
 * F6 (candidate 7): THE REQUEST WORKSPACE IN A SHORT WINDOW, Ask with a draft, at 320 x 225, 320 x 568 and 360 x 640. Measured failing on candidate 6 (diag-c6-findings): the workspace's fixed chrome is about 470 px
 * at 320 wide, so the scrolling body was 24 px at 225 and 101 px at 568, SMALLER THAN THE TEXTAREA (96 px) AND SEND (29 px): a scrollport smaller than what it must expose cannot expose it by any scroll position;
 * the other scroller was the overflow-hidden workspace itself (focus moved it, no pointer could); the composer was clipped after a Tab at 568 as well as 225. Asserted here, in the three tiers:
 *   (1) the SYNTHETIC notice (a persistent required notice) is EXPOSED (all of it inside the window and every clipping ancestor, with something of its own at its centre), at the landing, after a wheel, and after a
 *       real Tab to Send; the Progress notice and both notices' words are still there (nothing removed, reworded or made dismissable);
 *   (2) after a REAL Tab (from the document start, nothing arranged) the focused TEXTAREA and the focused SEND are each EXPOSED inside their ACTUAL scrollport (clipped by every ancestor, not by the window alone);
 *   (3) POINTER recovery: a real wheel over SCROLLED CONTENT (a point verified not to be over the pinned notice: the earlier wheel probe sat inside a covering element and could not say anything) moves a scroller, and
 *       by wheel alone the textarea and then Send become exposed. A key probe that pre-arranges its own state is not used.
 * LIMITS: Ask with a draft only; the three windows; the Requests tab and decision states are the existing tests'; system Chrome.
 */
const exposureOf = (page: Page, selector: string) =>
  page.evaluate((sel) => {
    const el = document.querySelector(sel) as HTMLElement | null;
    if (!el) return null;
    const r = el.getBoundingClientRect();
    let l = 0;
    let t = 0;
    let rr = innerWidth;
    let b = innerHeight;
    // WHAT THIS MODELS, AND WHAT IT DOES NOT. It clips the element by the overflow of its ancestors along the containing-block chain for position static, relative, absolute and fixed (a fixed box is not
    // clipped by its ancestors' overflow, an absolute box is clipped from its nearest positioned ancestor upward). That is only valid where NO ancestor establishes a containing block by other means: a
    // transform, filter, backdrop-filter, perspective, will-change (of those) or contain on an ancestor makes it the containing block for fixed and absolute descendants. Those cases are NOT modelled:
    // they are DETECTED and returned as `unsupported`, and a test that meets one fails instead of trusting a clip it did not compute. (container-type is not checked either.) The browser's own hit
    // test at the visible centre (pointerHitsIt) is the independent ground truth that is reported beside the model.
    const unsupported: string[] = [];
    for (let u: HTMLElement | null = el.parentElement; u; u = u.parentElement) {
      const ucs = getComputedStyle(u);
      const why: string[] = [];
      if (ucs.transform !== 'none') why.push('transform');
      if (ucs.filter !== 'none') why.push('filter');
      if (ucs.backdropFilter !== 'none' && ucs.backdropFilter !== '') why.push('backdrop-filter');
      if (ucs.perspective !== 'none') why.push('perspective');
      if (/transform|perspective|filter/.test(ucs.willChange)) why.push('will-change');
      if (/paint|layout|strict|content/.test(ucs.contain)) why.push('contain');
      if (why.length > 0) unsupported.push(`${u.tagName.toLowerCase()}${u.getAttribute('data-testid') ? `[${u.getAttribute('data-testid')}]` : ''}: ${why.join(', ')}`);
    }
    let c: HTMLElement = el;
    let a: HTMLElement | null = el.parentElement;
    while (a) {
      const pos = getComputedStyle(c).position;
      if (pos === 'fixed') break;
      if (pos === 'absolute') {
        while (a && getComputedStyle(a).position === 'static') a = a.parentElement;
        if (!a) break;
      }
      const cs = getComputedStyle(a);
      const ar = a.getBoundingClientRect();
      if (cs.overflowX !== 'visible') {
        l = Math.max(l, ar.left);
        rr = Math.min(rr, ar.right);
      }
      if (cs.overflowY !== 'visible') {
        t = Math.max(t, ar.top);
        b = Math.min(b, ar.bottom);
      }
      c = a;
      a = a.parentElement;
    }
    const vw = Math.max(0, Math.min(r.right, rr) - Math.max(r.left, l));
    const vh = Math.max(0, Math.min(r.bottom, b) - Math.max(r.top, t));
    const area = r.width * r.height;
    const cx = (Math.max(r.left, l) + Math.min(r.right, rr)) / 2;
    const cy = (Math.max(r.top, t) + Math.min(r.bottom, b)) / 2;
    const top = vw > 0 && vh > 0 ? document.elementFromPoint(cx, cy) : null;
    return {
      box: [Math.round(r.left), Math.round(r.top), Math.round(r.right), Math.round(r.bottom)],
      fraction: area > 0 ? Math.round(((vw * vh) / area) * 1000) / 1000 : 0,
      unsupported,
      pointerHitsIt: !!top && el.contains(top),
    };
  }, selector);
const workspaceScrolls = (page: Page) =>
  page.evaluate(() => {
    const out: Record<string, number> = {};
    for (const e of document.querySelectorAll('*'))
      if (e.scrollTop !== 0)
        out[e.getAttribute('data-testid') ?? e.className.toString().split(' ')[0] ?? e.tagName] =
          Math.round(e.scrollTop);
    return out;
  });
/** The actual scrollports at a moment, for the log (what the one scroller is doing and where the pieces sit), so a failure is read from numbers and not guessed. */
const scrollportReport = (page: Page) =>
  page.evaluate(() => {
    const ws = document.querySelector('[data-testid="di-mr"]') as HTMLElement;
    const box = (sel: string) => {
      const e = document.querySelector(sel);
      if (!e) return null;
      const r = e.getBoundingClientRect();
      return [Math.round(r.left), Math.round(r.top), Math.round(r.right), Math.round(r.bottom)];
    };
    return {
      window: [innerWidth, innerHeight],
      workspace: { scrollTop: Math.round(ws.scrollTop), scrollHeight: ws.scrollHeight, clientHeight: ws.clientHeight, scrollPaddingTop: ws.style.scrollPaddingTop },
      notice: box('[data-testid="di-mr-fixture-banner"]'),
      head: box('.di-mr-head'),
      tabs: box('.di-mr-tabs'),
      composer: box('[data-testid="di-mr-composer"]'),
      send: box('[data-testid="di-mr-send"]'),
    };
  });
const realTabTo = async (page: Page, testid: string): Promise<boolean> => {
  await page.evaluate(() => {
    (document.activeElement as HTMLElement | null)?.blur?.();
    const s = document.createElement('span');
    s.tabIndex = -1;
    s.setAttribute('data-probe', 'true');
    document.body.prepend(s);
    s.focus();
  });
  let reached = false;
  for (let i = 0; i < 120 && !reached; i += 1) {
    await page.keyboard.press('Tab');
    reached = await page.evaluate(
      (id) => (document.activeElement as HTMLElement | null)?.getAttribute('data-testid') === id,
      testid,
    );
  }
  await page.evaluate(() => document.querySelectorAll('[data-probe]').forEach((e) => e.remove()));
  return reached;
};
for (const [w, h] of [
  [320, 225],
  [320, 568],
  [360, 640],
] as const) {
  test(
    `F6 / REAL APP: the Ask draft in a short window, ${w} x ${h}: the synthetic notice stays exposed, the focused textarea and Send are exposed inside their actual scrollport after a REAL Tab, and a REAL wheel over scrolled content exposes both`,
    async () => {
      const page = await openWindow({ width: w, height: h });
      await openWorkspace(page);
      await ask(page, 'make the readiness probe stable', 'The readiness probe.');
      await page.waitForTimeout(1000);
      const failures: string[] = [];
      const notice = T('di-mr-fixture-banner');
      const checkNotice = async (when: string) => {
        const e = await exposureOf(page, notice);
        if (e === null)
          return void failures.push(`${when}: the synthetic notice is not in the page`);
        if (e.unsupported.length > 0)
          return void failures.push(
            `${when}: the exposure model does not cover the notice's ancestors (${e.unsupported.join('; ')})`,
          );
        if (e.fraction < 1)
          failures.push(
            `${when}: only ${Math.round(e.fraction * 100)} percent of the synthetic notice is exposed (box ${JSON.stringify(e.box)})`,
          );
        if (!e.pointerHitsIt)
          failures.push(
            `${when}: the pointer at the centre of the synthetic notice does not reach it`,
          );
      };
      // the words are preserved: both notices are in the page with text
      const words = await page.evaluate(() => ({
        fixture:
          document.querySelector('[data-testid="di-mr-fixture-line"]')?.textContent?.trim() ?? '',
        progress:
          document.querySelector('[data-testid="di-mr-progression-line"]')?.textContent?.trim() ??
          '',
        fixtureDisclosure: !!document.querySelector('[data-testid="di-mr-fixture-details"]'),
        progressDisclosure: !!document.querySelector('[data-testid="di-mr-progression-details"]'),
      }));
      if (!/synthetic/i.test(words.fixture))
        failures.push(`the synthetic notice's words are missing or changed: "${words.fixture}"`);
      if (words.progress.length < 10)
        failures.push(`the progress notice's words are missing: "${words.progress}"`);
      if (!words.fixtureDisclosure || !words.progressDisclosure)
        failures.push('a notice lost its disclosure');
      await checkNotice('at the landing (the draft just appeared)');
      // (2) a REAL Tab to the composer's textarea and to Send, from the document start, nothing arranged
      for (const id of ['di-mr-composer', 'di-mr-send']) {
        if (!(await realTabTo(page, id))) {
          failures.push(`a real Tab never reached ${id}`);
          continue;
        }
        await page.waitForTimeout(250);
        const e = await exposureOf(page, T(id));
        console.log(`[F6 scrollport ${w} x ${h}, after a real Tab to ${id}] ${JSON.stringify(await scrollportReport(page))}`);
        if (e !== null && e.unsupported.length > 0)
          failures.push(
            `after a real Tab, the exposure model does not cover ${id}'s ancestors (${e.unsupported.join('; ')})`,
          );
        if (e === null || e.fraction < 0.99)
          failures.push(
            `after a real Tab, the focused ${id} is only ${e === null ? 'absent' : `${Math.round(e.fraction * 100)} percent`} exposed in its actual scrollport (box ${e === null ? 'n/a' : JSON.stringify(e.box)})`,
          );
        else if (!e.pointerHitsIt)
          failures.push(`after a real Tab, the pointer at the centre of ${id} does not reach it`);
        await checkNotice(`after a real Tab to ${id}`);
      }
      // (3) POINTER recovery. The wheel point must BELONG TO SCROLLABLE CONTENT: it is found by SEARCH (never defaulted: the earlier probe fell back to the window's middle, which was inside the covering band and
      // could say nothing), and the hit element and its scroll-container ancestry are RECORDED. If NO such point exists, that is itself the finding (no pointer can scroll the workspace) and it is reported as one.
      await page.evaluate(() => {
        (document.activeElement as HTMLElement | null)?.blur?.();
        for (const e of document.querySelectorAll('*')) if (e.scrollTop !== 0) e.scrollTop = 0;
      });
      await page.waitForTimeout(300);
      const probe = await page.evaluate(() => {
        const ws = document.querySelector('[data-testid="di-mr"]') as HTMLElement;
        const desc = (e: Element | null) =>
          e
            ? `${e.tagName.toLowerCase()}${e.getAttribute('data-testid') ? `[${e.getAttribute('data-testid')}]` : e.className ? `.${String(e.className).split(' ')[0]}` : ''}`
            : 'none';
        const ancestry = (e: Element | null) => {
          const out: string[] = [];
          for (let a = e; a && out.length < 8; a = a.parentElement) {
            const cs = getComputedStyle(a);
            out.push(`${desc(a)}(${cs.overflowY}${cs.position === 'sticky' ? ', sticky' : ''})`);
          }
          return out;
        };
        // the container a wheel at that point would act on: the nearest ancestor (the element included) with overflow-y auto or scroll that really has more to scroll
        const scrollerOf = (el: Element | null) => {
          for (let a = el; a; a = a.parentElement) {
            const cs = getComputedStyle(a);
            if (
              (cs.overflowY === 'auto' || cs.overflowY === 'scroll') &&
              a.scrollHeight > a.clientHeight + 1
            )
              return a as HTMLElement;
          }
          return null;
        };
        const rejected: string[] = [];
        let scanned = 0;
        for (let y = innerHeight - 4; y > 4; y -= 8)
          for (const fx of [0.5, 0.25, 0.75]) {
            scanned += 1;
            const x = Math.round(innerWidth * fx);
            const top = document.elementFromPoint(x, y);
            if (!top || !top.closest('[data-testid="di-mr"]')) continue;
            if (top.closest('[data-testid="di-mr-fixture-banner"]')) continue;
            const sc = scrollerOf(top);
            if (sc === null || !(sc === ws || ws.contains(sc))) {
              if (rejected.length < 4)
                rejected.push(
                  `(${x},${y}) hit ${desc(top)}: ancestry ${ancestry(top).join(' > ')}: no scroller of the workspace to act on`,
                );
              continue;
            }
            if (sc.scrollTop >= sc.scrollHeight - sc.clientHeight - 1) continue;
            return {
              found: true as const,
              x,
              y,
              hit: desc(top),
              ancestry: ancestry(top),
              scroller: desc(sc),
              scrollerRange: sc.scrollHeight - sc.clientHeight,
              scanned,
              rejected,
            };
          }
        return { found: false as const, scanned, rejected };
      });
      console.log(`[F6 wheel probe ${w} x ${h}] ${JSON.stringify(probe)}`);
      if (!probe.found) {
        failures.push(
          `NO USABLE WHEEL POINT: of ${probe.scanned} points scanned none is over content that a wheel scrolls inside the workspace (${probe.rejected.join(' | ')}): no pointer can scroll the workspace. That is a product finding, not a probe to be tuned`,
        );
      } else {
        await page.mouse.move(probe.x, probe.y);
        // RESOLUTION TARGETED TO THE INTERVAL: the scroll positions at which the textarea is fully exposed span (scrollport - textarea) px, as few as 5 in a 101 px scrollport, so a coarse step jumps over it.
        // Coarse steps only while the element is more than 250 px outside the window, 3 px steps near it; the claim is bounded to that sampling.
        const STEP_FINE = 3;
        const wheelUntilExposed = async (id: string) => {
          let best = 0;
          for (let i = 0; i < 600; i += 1) {
            const e = await exposureOf(page, T(id));
            if (e === null) return { found: false, best, steps: i };
            best = Math.max(best, e.fraction);
            if (e.fraction >= 0.99) return { found: true, best, steps: i };
            const dist = e.box[1] > h ? e.box[1] - h : 0;
            const step = dist > 250 ? 200 : STEP_FINE;
            await page.mouse.wheel(0, step);
            await page.waitForTimeout(step === STEP_FINE ? 35 : 70);
          }
          return { found: false, best, steps: 600 };
        };
        const before = JSON.stringify(await workspaceScrolls(page));
        for (const id of ['di-mr-composer', 'di-mr-send']) {
          const r = await wheelUntilExposed(id);
          console.log(`[F6 wheel ${w} x ${h}] ${id}: ${JSON.stringify(r)}`);
          if (!r.found)
            failures.push(
              `by a real wheel alone ${id} was not found fully exposed at ${STEP_FINE} px resolution (best ${r.best} in ${r.steps} steps): a SAMPLED result, not proof it cannot be exposed`,
            );
        }
        if (JSON.stringify(await workspaceScrolls(page)) === before)
          failures.push('a real wheel over scrollable content moved no scroller');
      }
      await checkNotice('after the wheel recovery');
      expect(failures, `the Ask draft in a short window at ${w} x ${h}`).toEqual([]);
    },
    STEPS_MS,
  );
}
/*
 * F6, THE PAIR THAT SAYS WHAT CHANGED AND WHAT DID NOT (a green on the fixed workspace needs both):
 *   POSITIVE: the fixed workspace ESCAPES the zero-width overflow-clip ancestor above it (the app container is 0 px wide while the office panel covers the window, and a position: fixed box is not clipped by it),
 *   so the model's 'exposed' is not an artefact of that ancestor: the model says the synthetic notice is fully exposed AND the browser's own hit test agrees at every point of a grid over it.
 *   NEGATIVE: the workspace's OWN scrollport STILL clips what is scrolled partly out of it: with the composer, then Send, deliberately half out of the scrollport, the model reports less than full exposure AND the
 *   browser's hit test agrees (no point below the scrollport edge reaches the element, some above it do). Escaping the ancestor must not read as the clip having been solved: they are two separate things.
 * LIMITS: this fixture only (the ancestors of the workspace are overflow, position and no transform, filter, perspective, will-change or contain: the helper detects the others and these tests fail on them).
 */
test(
  'F6 HELPER, POSITIVE: the fixed workspace escapes its zero-width overflow-clip ancestor, and the model and the browser agree the synthetic notice is fully exposed at 320 x 568',
  async () => {
    const page = await openWindow({ width: 320, height: 568 });
    await openWorkspace(page);
    const premise = await page.evaluate(() => {
      const out: Array<{ el: string; width: number }> = [];
      for (
        let a = document.querySelector('[data-testid="di-mr"]')?.parentElement ?? null;
        a;
        a = a.parentElement
      ) {
        const cs = getComputedStyle(a);
        if (cs.overflowX !== 'visible')
          out.push({
            el: `${a.tagName.toLowerCase()}.${String(a.className).split(' ')[0]}`,
            width: Math.round(a.getBoundingClientRect().width),
          });
      }
      return out;
    });
    expect(
      premise.some((x) => x.width === 0),
      `the premise of the positive companion: a clipping ancestor of the workspace is zero-width (found ${JSON.stringify(premise)})`,
    ).toBe(true);
    const e = await exposureOf(page, T('di-mr-fixture-banner'));
    expect(e).not.toBeNull();
    expect(e!.unsupported, 'no ancestor the model does not cover').toEqual([]);
    expect(
      e!.fraction,
      `the model: the notice is fully exposed (box ${JSON.stringify(e!.box)})`,
    ).toBe(1);
    const grid = await page.evaluate(() => {
      const el = document.querySelector('[data-testid="di-mr-fixture-banner"]') as HTMLElement;
      const r = el.getBoundingClientRect();
      let hits = 0;
      let n = 0;
      for (const fy of [0.2, 0.5, 0.8])
        for (const fx of [0.1, 0.5, 0.9]) {
          n += 1;
          const top = document.elementFromPoint(r.left + r.width * fx, r.top + r.height * fy);
          if (top && el.contains(top)) hits += 1;
        }
      return { hits, n };
    });
    expect(grid.hits, `the browser's hit test over the notice (${grid.hits} of ${grid.n})`).toBe(
      grid.n,
    );
  },
  STEPS_MS,
);
test(
  "F6 HELPER, NEGATIVE: the workspace's own scrollport still clips the textarea and Send when they are scrolled partly out of it, and the model and the browser agree, at 320 x 568",
  async () => {
    const page = await openWindow({ width: 320, height: 568 });
    await openWorkspace(page);
    await ask(page, 'make the readiness probe stable', 'The readiness probe.');
    await page.waitForTimeout(800);
    for (const [id, beyond] of [
      ['di-mr-composer', 40],
      ['di-mr-send', 12],
    ] as const) {
      // scroll the one scroller so that the element's bottom is `beyond` px below the scrollport's bottom edge
      await page.evaluate(
        ({ sel, by }) => {
          const ws = document.querySelector('[data-testid="di-mr"]') as HTMLElement;
          const el = document.querySelector(sel) as HTMLElement;
          const r = el.getBoundingClientRect();
          ws.scrollTop += r.bottom - (innerHeight + by);
        },
        { sel: T(id), by: beyond },
      );
      await page.waitForTimeout(250);
      const e = await exposureOf(page, T(id));
      expect(e, `${id} is in the page`).not.toBeNull();
      expect(e!.unsupported, 'no ancestor the model does not cover').toEqual([]);
      expect(
        e!.fraction,
        `the model: ${id} is clipped by the scrollport (${e!.fraction}, box ${JSON.stringify(e!.box)})`,
      ).toBeLessThan(0.97);
      const agree = await page.evaluate((sel) => {
        const el = document.querySelector(sel) as HTMLElement;
        const r = el.getBoundingClientRect();
        let inside = 0;
        let insideHits = 0;
        let outsideHits = 0;
        for (const fy of [0.1, 0.3, 0.5, 0.7, 0.9])
          for (const fx of [0.1, 0.3, 0.5, 0.7, 0.9]) {
            const x = r.left + r.width * fx;
            const y = r.top + r.height * fy;
            const hit = (() => {
              const top = y >= 0 && y < innerHeight ? document.elementFromPoint(x, y) : null;
              return !!top && el.contains(top);
            })();
            if (y < innerHeight) {
              inside += 1;
              if (hit) insideHits += 1;
            } else if (hit) outsideHits += 1;
          }
        return { inside, insideHits, outsideHits };
      }, T(id));
      expect(
        agree.outsideHits,
        `the browser: no point of ${id} below the window's bottom edge reaches it`,
      ).toBe(0);
      expect(
        agree.insideHits,
        `the browser: the part of ${id} inside the window is reached (${agree.insideHits} of ${agree.inside} points)`,
      ).toBeGreaterThan(0);
    }
  },
  STEPS_MS,
);
