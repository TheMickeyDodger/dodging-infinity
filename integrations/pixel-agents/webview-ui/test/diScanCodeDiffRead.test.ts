/**
 * Stage 4, increment 1: the scan face's CODE DIFF on real data, through the ACTUAL App, in a real browser.
 *
 * The page (`test/harness/di-app-scan.html`) is the real App on the scan face (the rail, the panel, the nested Code Diff editor),
 * fed over a REAL WebSocket this test serves at `/ws` with an office state and review answers that the real server code produced
 * from the backend's exported answers over a fixture git workspace (`test/fixtures/di-office-state-code-review-v2.FIXTURE.json`,
 * hash-pinned by the server's own tests). Nothing here is live.
 *
 * What it proves (the behaviours this increment claims):
 *  - opening Code Diff for the SELECTED Mission reads it by an admitted REFERENCE (five keys, seven for one file, each plus the one opaque id the read names itself with) and never a path,
 *    and the file id it sends is one the overview LISTED;
 *  - the real tree, the real marks, the real lines and the real status facts are drawn in the existing nested editor, with NO modal;
 *  - what the route did not determine says so (encoding, line ending, indentation), and a count that is only a floor says it is.
 *
 * Browser: the installed Chrome (playwright-core, channel `chrome`), else Playwright's Chromium; with neither, every test is
 * skipped with an explicit message.
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
// the server side's socket type (ws), named apart from the DOM WebSocket that the code run INSIDE the page (addInitScript, evaluate) is written against
import type { WebSocket as ServerSocket } from 'ws';
import { WebSocketServer } from 'ws';

import type { DiCodeReview, DiOfficeState } from '../../core/src/messages.ts';
import { problemWords, UNAVAILABLE_WORDS } from '../src/components/diCodeReviewModel.ts';
import { DI_DIFF_NOT_READ } from '../src/components/diScanModel.ts';

const STEPS_MS = 120_000;
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const doc = JSON.parse(
  fs.readFileSync(path.join(root, 'test/fixtures/di-office-state-code-review-v2.FIXTURE.json'), 'utf-8'),
) as {
  reference: { missionRef: string; memberRef: string; taskId: string; listing: string };
  states: Record<'live' | 'stale' | 'inactive' | 'moved' | 'agentGone' | 'unavailable', DiOfficeState>;
  replies: { overview: DiCodeReview; files: Record<string, DiCodeReview> };
};
const VERSION = (JSON.parse(fs.readFileSync(path.join(root, '..', 'package.json'), 'utf-8')) as { version: string }).version;

let server: ViteDevServer | null = null;
let wss: WebSocketServer | null = null;
let browser: Browser | null = null;
let page: Page | null = null;
let url = '';
let unavailable: string | null = null;
let socket: ServerSocket | null = null;
let resolveConnected: (ws: ServerSocket) => void = () => undefined;
/** Every diCodeReviewRead the page sent, exactly as received. */
let requests: Array<Record<string, unknown>> = [];
/** A labelled, synthetic variation a test makes to the reply the "office" sends (null: send it as recorded). */
let replyHook: ((request: Record<string, unknown>, reply: DiCodeReview) => DiCodeReview | null) | null = null;
/** Replies the test holds back until it releases them (a read that is "in flight"). */
let holdWhen: ((request: Record<string, unknown>) => boolean) | null = null;
let held: Array<() => void> = [];
/** The exact text of each held reply, in the order the requests ARRIVED (so a test can deliver them to the page itself, in an order and a task of its choosing). */
let heldFrames: string[] = [];
const releaseHeld = () => {
  const now = held;
  held = [];
  heldFrames = [];
  for (const send of now) send();
};

// ---- a SECOND Mission, built from the recorded one by replacing its identity throughout (a labelled synthetic variation)
const A = doc.reference;
const A_HEX = /mn-([0-9a-f]{32})/.exec(A.missionRef)![1]!;
const A_TASK = A.taskId;
const B_HEX = 'f00dcafef00dcafef00dcafef00dcafe';
const B_TASK = '20261014-000000-bbbbbb';
const B = {
  missionRef: A.missionRef.replace(A_HEX, B_HEX),
  memberRef: A.memberRef.replaceAll(A_HEX, B_HEX).replace(A_TASK, B_TASK),
  taskId: B_TASK,
  listing: 'b'.repeat(64),
};
/** The files Mission B's listing has (a subset of A's, so a leak of A's other files is visible). */
const B_PATHS = ['src/util.py', 'styles/theme.css', 'notes/new.txt'];
const asB = (reply: DiCodeReview): DiCodeReview => {
  const text = JSON.stringify(reply).replaceAll(A_HEX, B_HEX).replaceAll(A_TASK, B_TASK).replaceAll(A.listing, B.listing);
  return JSON.parse(text) as DiCodeReview;
};
const twoMissions = (): DiOfficeState => {
  const state = structuredClone(doc.states.live);
  const first = state.missions.missions[0]!;
  const text = JSON.stringify(first).replaceAll(A_HEX, B_HEX).replaceAll(A_TASK, B_TASK).replaceAll(A.listing, B.listing);
  const second = JSON.parse(text) as typeof first;
  for (const member of second.members.members) member.characterId = null;
  state.missions.missions.push(second);
  return state;
};

/**
 * I3: the recorded answer re-addressed to the reference a MISSION-LEVEL read (a FINISHED Mission) asked by: the Mission, task and listing it named, and an EMPTY member field (that
 * reference names no member). A labelled, synthetic variation: the bytes of the answer are the recorded ones; only the reference it echoes changes, as the real office echoes it.
 */
const readdressed = (reply: DiCodeReview, request: Record<string, unknown>): DiCodeReview => ({
  ...reply,
  missionRef: String(request.missionRef),
  memberRef: '',
  taskId: String(request.taskId),
  listing: String(request.listing),
});

/** The reply the (recorded, real) office relays for a request; null when none is recorded for it. */
function replyFor(request: Record<string, unknown>): DiCodeReview | null {
  const forB = request.missionRef === B.missionRef;
  let stored: DiCodeReview | undefined;
  if (request.fileId === undefined) {
    stored = structuredClone(doc.replies.overview);
    if (forB) {
      stored.overview!.files = stored.overview!.files.filter((f) => B_PATHS.includes(f.path));
      const files = stored.overview!.files;
      stored.overview!.totals = {
        files: files.length,
        additions: files.reduce((n, f) => n + (f.additions ?? 0), 0),
        deletions: files.reduce((n, f) => n + (f.deletions ?? 0), 0),
        withheld: 0,
        unshown: 0,
      };
    }
  } else {
    stored = Object.values(doc.replies.files).find((r) => r.fileId === request.fileId);
    stored = stored === undefined ? undefined : structuredClone(stored);
  }
  if (stored === undefined) return null;
  const reply = request.type === 'diCodeReviewMissionRead' ? readdressed(stored, request) : forB ? asB(stored) : stored;
  return replyHook ? replyHook(request, reply) : reply;
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
        } else if (msg.type === 'diCodeReviewRead' || msg.type === 'diCodeReviewMissionRead') {
          requests.push(msg);
          const found = replyFor(msg);
          if (found === null) return;
          // the real office ECHOES the request's opaque id in its answer (absent when the request carried none)
          const reply = typeof msg.requestId === 'string' ? { ...found, requestId: msg.requestId } : found;
          const send = () => ws.readyState === ws.OPEN && ws.send(JSON.stringify(reply));
          if (holdWhen?.(msg)) {
            held.push(send);
            heldFrames.push(JSON.stringify(reply));
          } else send();
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
    console.warn(`SKIPPED (scan code diff): ${unavailable}`);
    ctx.skip();
    return;
  }
  await page?.close();
  page = null;
  requests = [];
  replyHook = null;
  holdWhen = null;
  held = [];
  heldFrames = [];
}, STEPS_MS);

const p = () => page!;

/** Open the App on the scan face at 1440 x 900 and hand it the office state, as the real server does after webviewReady. */
async function boot(
  state: DiOfficeState = doc.states.live,
  viewport = { width: 1440, height: 900 },
  opts: { tapSocket?: boolean; railSelector?: string } = {},
): Promise<void> {
  const connected = new Promise<ServerSocket>((resolve) => (resolveConnected = resolve));
  page = await browser!.newPage({ viewport });
  await page.emulateMedia({ reducedMotion: 'reduce' });
  if (opts.tapSocket) {
    // Keep a handle on the page's own WebSocket (the one the app's transport opened), so a test can hand it frames inside ONE task.
    await page.addInitScript(() => {
      const tapped: unknown[] = [];
      (window as unknown as { __tappedSockets: unknown[] }).__tappedSockets = tapped;
      const Native = window.WebSocket;
      window.WebSocket = class extends Native {
        constructor(...args: ConstructorParameters<typeof WebSocket>) {
          super(...args);
          tapped.push(this);
        }
      };
    });
  }
  await page.goto(url, { waitUntil: 'domcontentloaded' });
  await connected;
  socket!.send(
    JSON.stringify({
      type: 'providerCapabilities',
      readingTools: ['Read', 'Grep', 'Glob', 'WebFetch', 'WebSearch'],
      subagentToolNames: ['Task', 'Agent'],
    }),
  );
  socket!.send(JSON.stringify({ type: 'settingsLoaded', soundEnabled: false, lastSeenVersion: VERSION, extensionVersion: VERSION }));
  socket!.send(JSON.stringify(state));
  // the rail lists an orchestrator per running Mission; a state with none (a finished Mission alone) is waited for on the rail's Missions entry (opts.railSelector)
  await p().waitForSelector(opts.railSelector ?? '[data-testid="di-rail-orchestrator"]');
}

/** Open the Mission details of the Mission `ref` (the first when none is named), through the rail, as a person does. */
async function openMissionDetails(ref: string | null = null): Promise<string> {
  const count = await p().locator('[data-testid="di-rail-orchestrator"]').count();
  for (let i = 0; i < count; i++) {
    await p().click(`[data-testid="di-rail-orchestrator"] >> nth=${i}`);
    await p().waitForSelector('[data-testid="di-scan-stage"][data-view="mission"]');
    const shown = await p().getAttribute('[data-testid="di-scan-stage"]', 'data-mission-ref');
    if (ref === null || shown === ref) return shown ?? '';
  }
  assert.fail(`no rail entry opens the Mission ${ref}`);
}
/** Open Code Diff for the Mission `ref` (the first when none is named). */
async function openCodeDiff(ref: string | null = null): Promise<void> {
  await openMissionDetails(ref);
  await p().click('[data-testid="di-scan-code-diff"]');
  await p().waitForSelector('[data-testid="di-scan-stage"][data-view="diff"]');
}
const source = () => p().getAttribute('[data-testid="di-scan-diff"]', 'data-source');
const treeIds = () =>
  p().locator('[data-testid="di-scan-tree-file"]').evaluateAll((els) => els.map((e) => e.getAttribute('data-file-id')));
/** Wait until the Code Diff pane says `kind`. */
async function waitForSource(kind: string): Promise<void> {
  await p().waitForSelector(`[data-testid="di-scan-diff"][data-source="${kind}"]`, { timeout: 15_000 });
}

/** Wait for `n` reads to have been sent (or fail with what was seen). */
async function waitForReads(n: number, what: string): Promise<void> {
  const deadline = Date.now() + 10_000;
  while (requests.length < n && Date.now() < deadline) await p().waitForTimeout(50);
  assert.ok(requests.length >= n, `${what}: expected ${n} diCodeReviewRead, the page sent ${requests.length}`);
}

const overview = doc.replies.overview.overview!;
const entryOf = (filePath: string) => {
  const found = overview.files.find((f) => f.path === filePath);
  assert.ok(found, `the recorded overview lists ${filePath}`);
  return found;
};

test(
  'CODE DIFF READS THE SELECTED MISSION: opening it names an admitted reference (never a path), and the real tree, marks, lines and status facts are drawn in the nested editor with no modal',
  async () => {
    await boot();
    await openCodeDiff();

    // 1. the read names a REFERENCE: the five reference keys (the Mission, member, task and listing the office offered) plus the one opaque request id; no path anywhere
    await waitForReads(1, 'Code Diff opened');
    // the closed overview shape: the five reference keys plus the ONE opaque id the read names itself with (an exact key set, not a subset check)
    assert.deepEqual(Object.keys(requests[0]!).sort(), ['listing', 'memberRef', 'missionRef', 'requestId', 'taskId', 'type']);
    // everything but the id is EXACTLY the admitted reference; the id is checked on its own (it is a name, so there is no value to compare it with)
    const { requestId: overviewId, ...overviewAsked } = requests[0]!;
    assert.match(String(overviewId), /^[A-Za-z0-9._-]{1,64}$/);
    assert.deepEqual(overviewAsked, { type: 'diCodeReviewRead', ...doc.reference });

    // 2. the real tree: every listed file, by the id the route gave it, drawn in the existing nested editor
    await p().waitForSelector('[data-testid="di-scan-diff"][data-source="read"]');
    const ids = await p().locator('[data-testid="di-scan-tree-file"]').evaluateAll((els) => els.map((e) => e.getAttribute('data-file-id')));
    assert.deepEqual([...ids].sort(), overview.files.map((f) => f.fileId).sort());

    // 3. the mark letters keep the kinds DISTINCT, with a plain accessible label (the letter is not the only carrier)
    const mark = async (filePath: string) => {
      const row = p().locator(`[data-testid="di-scan-tree-file"][data-file-id="${entryOf(filePath).fileId}"] .di-scan-mark-badge`);
      return { letter: (await row.textContent())?.trim(), label: await row.getAttribute('aria-label') };
    };
    assert.deepEqual(await mark('src/app.py'), { letter: 'M', label: 'modified' });
    assert.deepEqual(await mark('docs/gone.md'), { letter: 'D', label: 'deleted' });
    assert.deepEqual(await mark('src/moved.py'), { letter: 'R', label: 'renamed' });
    assert.deepEqual(await mark('docs/new-guide.md'), { letter: 'U', label: 'new file, not yet tracked by git' });

    // 4. choosing a file reads THAT file: the seven reference keys (the snapshot the overview carried and the file id it LISTED) plus the one opaque request id
    const app = entryOf('src/app.py');
    await p().click(`[data-testid="di-scan-tree-file"][data-file-id="${app.fileId}"]`);
    await waitForReads(2, 'a file chosen');
    const fileRead = requests[requests.length - 1]!;
    assert.deepEqual(Object.keys(fileRead).sort(), ['fileId', 'listing', 'memberRef', 'missionRef', 'requestId', 'snapshot', 'taskId', 'type']);
    assert.match(String(fileRead.requestId), /^[A-Za-z0-9._-]{1,64}$/);
    assert.equal(fileRead.fileId, app.fileId);
    assert.equal(fileRead.snapshot, doc.replies.overview.snapshot);

    // 5. the real lines of that file
    const reply = Object.values(doc.replies.files).find((r) => r.fileId === app.fileId)!;
    const expectedLines = reply.file!.hunks.flatMap((h) => h.lines).filter((l) => l.kind === 'add').map((l) => l.text);
    await p().waitForSelector('[data-testid="di-scan-editor"] .di-scan-diff-line[data-kind="add"]');
    const shown = await p().locator('[data-testid="di-scan-editor"] .di-scan-diff-line[data-kind="add"]').evaluateAll((els) => els.map((e) => e.textContent ?? ''));
    for (const text of expectedLines) assert.ok(shown.some((s) => s.includes(text)), `the added line ${JSON.stringify(text)} is drawn`);

    // 6. the status facts: the real branch; a FLOOR for the counts (eight listed files carry no counts, the backend sums them as zero);
    //    nothing the route did not determine is stated as a fact
    const bar = (await p().locator('[data-testid="di-scan-statusbar"]').textContent()) ?? '';
    assert.match(bar, /main/);
    assert.match(bar, new RegExp(`base ${doc.replies.overview.comparison!.baseOid.slice(0, 10)}`), `the base is stated in the ordinary view: ${bar}`);
    assert.match(bar, /23 files changed/);
    assert.match(bar, /\+7,?025\+/, `the added total is a floor: ${bar}`);
    assert.match(bar, /[−-]110\+/, `the removed total is a floor: ${bar}`);
    assert.doesNotMatch(bar, /UTF-8|\bLF\b|CRLF|Spaces|Tabs/, `no encoding, line ending or indentation is claimed: ${bar}`);
    // ONE VOCABULARY: what the review cannot state is "unknown" (the longer "not determined" did not fit the approved box at 900 px)
    assert.match(bar, /Format unknown/, `the file facts the route does not carry are one phrase: ${bar}`);
    assert.doesNotMatch(bar, /not determined/i, `the real bar never says "not determined": ${bar}`);

    // 7. the ONE note carries both facts: the SHARED worktree (the team's changes, not the member's) and what is NOT covered, from the answer's own list
    const note = (await p().locator('.di-scan-tree-note').first().textContent()) ?? '';
    assert.match(note, /shared worktree/i);
    assert.match(note, /Not covered/);
    for (const part of [/ignored/, /submodule/, /binary/, /bound/, /private/]) assert.match(note, part, `the note names ${part}`);

    // 8. the nested editor plus Back: NO duplicate modal
    assert.equal(await p().locator('[role="dialog"]').count(), 0, 'no modal opens over the scan Code Diff');
    assert.equal(await p().locator('[data-testid="di-scan-open-review"]').count(), 0, 'the transitional bridge is gone from the scan face');
  },
  STEPS_MS,
);

/** The refusal the office relays for an overview (a labelled, synthetic variation of the recorded answer). */
const refusedOverview = (reply: DiCodeReview, problem: string, detail: string): DiCodeReview => ({
  ...reply,
  state: 'refused',
  problem,
  detail,
  snapshot: null,
  target: null,
  comparison: null,
  overview: null,
  observedAt: null,
});

test(
  'THE MISSION SWITCH (browser): after reading Mission A, Mission B\'s pane never shows A\'s files while B\'s answer is in flight, and shows only B\'s own when it arrives',
  async () => {
    await boot(twoMissions());
    await openCodeDiff(A.missionRef);
    await waitForSource('read');
    const aIds = (await treeIds()) as string[];
    assert.equal(aIds.length, overview.files.length, 'Mission A drew its whole listing');
    const bIds = B_PATHS.map((pp) => entryOf(pp).fileId);
    const aOnly = aIds.filter((id) => !bIds.includes(id));
    assert.ok(aOnly.length > 10, 'A has many files that B does not');

    // from here on, watch EVERY change to the page: if the pane of Mission B ever holds a file only A has, it is a leak
    await p().evaluate(
      ([bRef, only]) => {
        const w = window as unknown as { __leaks: string[] };
        w.__leaks = [];
        new MutationObserver(() => {
          const stage = document.querySelector('[data-testid="di-scan-stage"]');
          if (stage?.getAttribute('data-mission-ref') !== bRef) return;
          for (const el of document.querySelectorAll('[data-testid="di-scan-tree-file"]')) {
            const id = el.getAttribute('data-file-id') ?? '';
            if ((only as string[]).includes(id)) w.__leaks.push(id);
          }
        }).observe(document.body, { subtree: true, childList: true, attributes: true, characterData: true });
      },
      [B.missionRef, aOnly] as const,
    );

    // B's overview is held: its read is in flight
    holdWhen = (r) => r.missionRef === B.missionRef && r.fileId === undefined;
    await openCodeDiff(B.missionRef);
    const deadline = Date.now() + 10_000;
    while (!requests.some((r) => r.missionRef === B.missionRef) && Date.now() < deadline) await p().waitForTimeout(50);
    assert.ok(requests.some((r) => r.missionRef === B.missionRef), 'Mission B was read, by its own reference');
    // ...and while it is in flight the pane says so, and holds NOTHING of A's, at every sample
    for (let i = 0; i < 8; i++) {
      assert.equal(await source(), 'reading', `sample ${i}: B's pane is reading, not showing a diff`);
      assert.deepEqual(await treeIds(), [], `sample ${i}: no file is drawn under Mission B before its answer`);
      await p().waitForTimeout(100);
    }
    assert.equal(await p().getAttribute('[data-testid="di-scan-stage"]', 'data-mission-ref'), B.missionRef);

    releaseHeld();
    await waitForSource('read');
    assert.deepEqual(((await treeIds()) as string[]).sort(), [...bIds].sort(), 'Mission B draws exactly its own listing');
    const bar = (await p().locator('[data-testid="di-scan-statusbar"]').textContent()) ?? '';
    assert.match(bar, /3 files changed/);
    assert.doesNotMatch(bar, /\d\+/, `every listed file of B carries its counts, so its totals are EXACT: ${bar}`);
    assert.deepEqual(await p().evaluate(() => (window as unknown as { __leaks: string[] }).__leaks), [], 'A\'s files never appeared under Mission B');
  },
  STEPS_MS,
);

test(
  'A LATE ANSWER for Mission A, arriving after the reader has moved on to Mission B, is ignored: B\'s pane is not changed by it',
  async () => {
    await boot(twoMissions());
    holdWhen = (r) => r.missionRef === A.missionRef && r.fileId === undefined; // A's overview stays in flight
    await openCodeDiff(A.missionRef);
    const deadline = Date.now() + 10_000;
    while (!requests.some((r) => r.missionRef === A.missionRef) && Date.now() < deadline) await p().waitForTimeout(50);
    assert.equal(await source(), 'reading', 'A is still being read');
    await openCodeDiff(B.missionRef); // the reader moves on
    await waitForSource('read');
    const bIds = B_PATHS.map((pp) => entryOf(pp).fileId).sort();
    assert.deepEqual(((await treeIds()) as string[]).sort(), bIds);

    releaseHeld(); // A's answer arrives LATE
    await p().waitForTimeout(800);
    assert.equal(await p().getAttribute('[data-testid="di-scan-stage"]', 'data-mission-ref'), B.missionRef);
    assert.equal(await source(), 'read');
    assert.deepEqual(((await treeIds()) as string[]).sort(), bIds, 'a late answer for A did not change B\'s pane');
  },
  STEPS_MS,
);

test(
  'WHERE NO REVIEW IS OFFERED the pane is "unavailable" with the office\'s own reason, or "not read": nothing is asked, nothing is empty, no modal',
  async () => {
    for (const [name, state, reason] of [
      ['member_not_live', doc.states.stale, 'member_not_live'],
      ['assignment_not_active (a finished Mission)', doc.states.inactive, 'assignment_not_active'],
    ] as const) {
      await boot(state);
      await openCodeDiff();
      await waitForSource('unavailable');
      const text = (await p().locator('[data-testid="di-scan-diff-unavailable"]').textContent()) ?? '';
      assert.ok(text.includes(UNAVAILABLE_WORDS[reason]!), `${name}: the office's own reason is in the pane: ${text}`);
      assert.deepEqual(await treeIds(), [], `${name}: no file is drawn`);
      await p().waitForTimeout(500);
      assert.equal(requests.length, 0, `${name}: nothing was asked of the office`);
      assert.equal(await p().locator('[role="dialog"]').count(), 0, `${name}: no modal`);
      await page!.close();
      page = null;
    }
    // a Mission whose members say nothing about a review: NOT CLAIMED, the existing not-read words, nothing asked
    await boot(doc.states.agentGone);
    await openCodeDiff();
    assert.equal(await source(), 'not_read');
    assert.equal(((await p().locator('[data-testid="di-scan-diff-not-read"]').textContent()) ?? '').trim(), DI_DIFF_NOT_READ);
    await p().waitForTimeout(400);
    assert.equal(requests.length, 0);
  },
  STEPS_MS,
);

test(
  'A REFUSED read says so in the route\'s own words (never an empty diff, never "reading" forever); a "not this instant" refusal is asked again by itself',
  async () => {
    // refused, and not retryable: one read, stated
    replyHook = (request, reply) => (request.fileId === undefined ? refusedOverview(reply, 'listing_moved', 'the assignment moved since this listing') : reply);
    await boot();
    await openCodeDiff();
    await waitForSource('refused');
    const said = problemWords('listing_moved', 'the assignment moved since this listing');
    const text = (await p().locator('[data-testid="di-scan-diff-refused"]').textContent()) ?? '';
    assert.ok(text.includes(said.title), `the route's problem is stated: ${text}`);
    assert.deepEqual(await treeIds(), []);
    await p().waitForTimeout(900);
    assert.equal(requests.length, 1, 'a refusal that will not change by waiting is not retried');
    await page!.close();
    page = null;

    // "not this instant": asked again by itself, then read
    requests = [];
    let first = true;
    replyHook = (request, reply) => {
      if (request.fileId !== undefined || !first) return reply;
      first = false;
      return refusedOverview(reply, 'read_rate_limited', 'one read at a time');
    };
    await boot();
    await openCodeDiff();
    await waitForSource('read');
    assert.ok(requests.filter((r) => r.fileId === undefined).length >= 2, 'the overview was asked again after the rate limit');
  },
  STEPS_MS,
);

test(
  'A LISTING THAT MOVES under an open Code Diff: the old files are not shown under the new reference, and the read is made again under the NEW listing',
  async () => {
    await boot();
    await openCodeDiff();
    await waitForSource('read');
    const movedOffer = doc.states.moved.missions.missions[0]!.members.members[0]!.codeReview!;
    assert.notEqual(movedOffer.listing, doc.reference.listing, 'the recorded "moved" state carries a different listing');
    socket!.send(JSON.stringify(doc.states.moved));
    await p().waitForSelector('[data-testid="di-scan-diff"][data-source="reading"]', { timeout: 15_000 });
    assert.deepEqual(await treeIds(), [], 'the old listing\'s files are gone the moment the reference moved');
    const deadline = Date.now() + 10_000;
    while (requests[requests.length - 1]?.listing !== movedOffer.listing && Date.now() < deadline) await p().waitForTimeout(50);
    assert.equal(requests[requests.length - 1]?.listing, movedOffer.listing, 'the new read names the NEW listing');
    // the recorded answer still carries the OLD listing, so it answers a different reference and is never shown
    await p().waitForTimeout(600);
    assert.equal(await source(), 'reading');
    assert.deepEqual(await treeIds(), []);
  },
  STEPS_MS,
);

test(
  'EVERY FILE STATE says what it is in the existing editor: no lines for a file the route could not show, and NO READ is made for one',
  async () => {
    await boot();
    await openCodeDiff();
    await waitForSource('read');
    await p().waitForSelector('[data-testid="di-scan-editor"] .di-scan-diff-line');
    const choose = (pp: string) => p().click(`[data-testid="di-scan-tree-file"][data-file-id="${entryOf(pp).fileId}"]`);
    const settled = requests.length;
    for (const [pp, state] of [
      ['.env', 'withheld'],
      ['bin/blob.bin', 'binary'],
      ['legacy/latin1.txt', 'not_utf8'],
      ['generated/bundle.min.js', 'oversized'],
      ['vendor/clone', 'unsupported'],
      ['empty.txt', 'empty'],
    ] as const) {
      await choose(pp);
      await p().waitForSelector(`[data-testid="di-scan-file-notice"][data-state="${state}"]`);
      assert.equal(await p().locator('[data-testid="di-scan-editor"] .di-scan-diff-line').count(), 0, `${pp}: no lines are drawn`);
      assert.equal(await p().locator('[data-testid="di-scan-editor-unchanged"]').count(), 0, `${pp}: never "No changes in this file"`);
    }
    assert.equal(requests.length, settled, 'no read was made for a file the route cannot show');
    // a withheld file names no content
    await choose('secrets/server.pem');
    const withheld = (await p().locator('[data-testid="di-scan-file-notice"]').textContent()) ?? '';
    assert.match(withheld, /never read or shown/);

    // a submodule shows its pointer lines and says what a submodule change is
    await choose('vendor/lib');
    await p().waitForSelector('[data-testid="di-scan-file-foot"]');
    assert.match((await p().locator('[data-testid="di-scan-file-foot"]').textContent()) ?? '', /submodule/i);
    assert.ok((await p().locator('[data-testid="di-scan-editor"] .di-scan-diff-line').count()) > 0);
    // a renamed file shows old and new; a deleted file shows its removed lines
    await choose('src/moved.py');
    await p().waitForSelector('[data-testid="di-scan-editor"] .di-scan-diff-line');
    assert.equal(((await p().locator('[data-testid="di-scan-editor-path"]').textContent()) ?? '').trim(), 'src/mover.py → src/moved.py');
    await choose('docs/gone.md');
    await p().waitForSelector('[data-testid="di-scan-editor"] .di-scan-diff-line[data-kind="del"]');
  },
  STEPS_MS,
);

test(
  'NO PATH EVER CROSSES THE BOUNDARY: every message is one of two closed shapes, and every file id sent is one the overview LISTED',
  async () => {
    await boot();
    await openCodeDiff();
    await waitForSource('read');
    for (const pp of ['src/app.py', 'docs/gone.md', 'vendor/lib', 'crlf.txt', 'src/util.py']) {
      await p().click(`[data-testid="di-scan-tree-file"][data-file-id="${entryOf(pp).fileId}"]`);
      await p().waitForTimeout(150);
    }
    assert.ok(requests.length >= 6, `several reads were made (${requests.length})`);
    const listedIds = new Set(overview.files.map((f) => f.fileId));
    const paths = overview.files.flatMap((f) => [f.path, ...(f.oldPath === null ? [] : [f.oldPath])]);
    const seenIds = new Set<string>();
    for (const request of requests) {
      const keys = Object.keys(request).sort().join(',');
      assert.ok(
        keys === 'listing,memberRef,missionRef,requestId,taskId,type' || keys === 'fileId,listing,memberRef,missionRef,requestId,snapshot,taskId,type',
        `a closed shape (the two shapes plus the one opaque id), got ${keys}`,
      );
      assert.match(String(request.requestId), /^[A-Za-z0-9._-]{1,64}$/, 'the id is a bounded opaque token');
      assert.ok(!seenIds.has(String(request.requestId)), 'every read names itself with an id of its own');
      seenIds.add(String(request.requestId));
      if (request.fileId !== undefined) {
        assert.ok(listedIds.has(String(request.fileId)), 'the file id sent is one the overview listed');
        assert.equal(request.snapshot, doc.replies.overview.snapshot);
      }
      const text = JSON.stringify(request);
      assert.doesNotMatch(text, /\\|\.\.|\//, `no path separator in a request: ${text}`);
      for (const pathName of paths) assert.ok(!text.includes(pathName), `a request never carries the path ${pathName}`);
    }
  },
  STEPS_MS,
);

test(
  'BACK / CLOSE ends the read: nothing more is asked, and opening Code Diff again makes a FRESH read; no modal either time',
  async () => {
    await boot();
    await openCodeDiff();
    await waitForSource('read');
    await p().waitForTimeout(300);
    const before = requests.filter((r) => r.fileId === undefined).length;
    await p().click('[aria-label="Close the diff"]');
    await p().waitForSelector('[data-testid="di-scan-stage"][data-view="mission"]');
    await p().waitForTimeout(500);
    assert.equal(requests.filter((r) => r.fileId === undefined).length, before, 'leaving Code Diff asks for nothing');
    await p().click('[data-testid="di-scan-code-diff"]');
    await waitForSource('read');
    assert.equal(requests.filter((r) => r.fileId === undefined).length, before + 1, 'opening it again reads the working tree afresh');
    assert.equal(await p().locator('[role="dialog"]').count(), 0);
  },
  STEPS_MS,
);

// ─────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────
// THE RENDERED FIT: every real state in the owner-approved geometry, measured against the specimen at the same size.
// Asserted (must always hold): the approved boxes (the tree, the editor bar, the status bar) are EXACTLY the specimen's, nothing in the
// status bar or the editor bar overlaps, the page does not scroll sideways. RECORDED, not asserted (a decision for the Lead): whether the
// status bar's content fits its own box. Set DI_STAGE4_FIT_OUT=<dir> to save the measurements and a screenshot of every state.
const FIT_OUT = process.env['DI_STAGE4_FIT_OUT'] ?? null;
const FIT_SIZES = [
  { width: 900, height: 700 },
  { width: 1280, height: 720 },
  { width: 1672, height: 941 },
] as const;
const LONG_BRANCH = 'mission/free-ui-1to1-os-terminal-20261026-a-deliberately-long-real-branch-name';
const lockUrl = () => url.replace('di-app-scan.html', 'di-scan-lock.html');

/** Every box of the Code Diff composition that the approved geometry fixes, plus the content that must fit them. */
const measureDiff = () =>
  p().evaluate(() => {
    const q = (sel: string) => document.querySelector(sel);
    const box = (el: Element | null) => {
      if (!el) return null;
      const r = el.getBoundingClientRect();
      return { x: Math.round(r.x * 10) / 10, y: Math.round(r.y * 10) / 10, w: Math.round(r.width * 10) / 10, h: Math.round(r.height * 10) / 10, right: Math.round(r.right * 10) / 10 };
    };
    const kids = (el: Element | null) =>
      el === null ? [] : [...el.children].map((c) => ({ text: (c.textContent ?? '').trim().slice(0, 70), ...box(c)! }));
    const overlaps = (list: Array<{ x: number; right: number; text: string }>) =>
      list.flatMap((a, i) => (i === 0 ? [] : list[i - 1]!.right > a.x + 0.5 ? [`${list[i - 1]!.text} | ${a.text}`] : []));
    const bar = q('[data-testid="di-scan-statusbar"]') as HTMLElement | null;
    const editorBar = q('.di-scan-editor-bar') as HTMLElement | null;
    const tree = q('.di-scan-tree') as HTMLElement | null;
    const barKids = kids(bar);
    const editorKids = kids(editorBar);
    return {
      viewport: { w: innerWidth, h: innerHeight },
      tree: box(tree),
      editorBar: box(editorBar),
      statusBar: box(bar),
      statusBarContent: bar === null ? null : { scrollWidth: bar.scrollWidth, clientWidth: bar.clientWidth },
      statusKids: barKids,
      statusOverlaps: overlaps(barKids),
      editorKids,
      editorOverlaps: overlaps(editorKids),
      editorBarContent: editorBar === null ? null : { scrollWidth: editorBar.scrollWidth, clientWidth: editorBar.clientWidth },
      note: box(q('.di-scan-tree-note')),
      treeScroll: tree === null ? null : { scrollHeight: tree.scrollHeight, clientHeight: tree.clientHeight },
      pageScroll: { scrollWidth: document.documentElement.scrollWidth, clientWidth: document.documentElement.clientWidth },
      // RECORDED: each status-bar part's own box, its natural width (scrollWidth) against what it was given (clientWidth), whether its text is
      // CUT, and the width a visible copy of the base revision would cost in the bar's own font. THE INSTRUMENT (corrected): `scrollWidth` and
      // `clientWidth` are integer-rounded, so a sub-pixel overflow is invisible to them, and the browser draws an ellipsis for ANY overflow. The
      // overflow is therefore measured on FRACTIONAL boxes: the width of the text's own range against the part's content box (its width minus
      // padding and border). `ellipsisDrawn` is that overflow above 0.01 px. (An earlier flag, `scrollWidth > clientWidth + 0.5`, hid exactly this.)
      parts: Object.fromEntries(
        (
          [
            ['leftGroup', '.di-scan-status-real'],
            ['branch', '.di-scan-status-branch'],
            ['base', '.di-scan-status-base'],
            ['counts', '.di-scan-status-counts'],
            ['format', '[data-testid="di-scan-status-format"]'],
            ['language', '[data-testid="di-scan-editor-language"]'],
          ] as const
        ).map(([name, selector]) => {
          const el = bar?.querySelector(selector) as HTMLElement | null | undefined;
          if (!el) return [name, null];
          const r = el.getBoundingClientRect();
          const range = document.createRange();
          range.selectNodeContents(el);
          const textWidth = range.getBoundingClientRect().width;
          const css = getComputedStyle(el);
          const contentWidth = r.width - parseFloat(css.paddingLeft) - parseFloat(css.paddingRight) - parseFloat(css.borderLeftWidth) - parseFloat(css.borderRightWidth);
          const overflowPx = Math.round((textWidth - contentWidth) * 1000) / 1000;
          return [
            name,
            {
              text: (el.textContent ?? '').trim().slice(0, 90),
              w: Math.round(r.width * 10) / 10,
              h: Math.round(r.height * 10) / 10,
              naturalWidth: el.scrollWidth,
              givenWidth: el.clientWidth,
              textWidth: Math.round(textWidth * 1000) / 1000,
              contentWidth: Math.round(contentWidth * 1000) / 1000,
              overflowPx,
              ellipsisDrawn: overflowPx > 0.01,
            },
          ];
        }),
      ),
      revisionProbe: (() => {
        const chip = bar?.querySelector('.di-scan-status-base') as HTMLElement | null | undefined;
        const group = bar?.querySelector('.di-scan-status-real') as HTMLElement | null | undefined;
        if (!chip || !group) return null;
        const revision = (chip.textContent ?? '').replace(/^base\s*/, '').trim();
        const probe = document.createElement('span');
        probe.style.cssText = 'position:absolute;visibility:hidden;white-space:nowrap;';
        probe.style.font = getComputedStyle(chip).font;
        probe.textContent = revision;
        document.body.appendChild(probe);
        const revisionWidth = Math.round(probe.getBoundingClientRect().width * 10) / 10;
        probe.remove();
        const gap = parseFloat(getComputedStyle(group).columnGap) || 0;
        return { revision, fontSize: getComputedStyle(chip).fontSize, revisionWidth, gap, extraIfDrawnAfterTheBranch: Math.round((revisionWidth + gap) * 10) / 10 };
      })(),
    };
  });

/**
 * A PIXEL instrument for "is this part's text cut?", independent of every layout metric: the part is screenshotted as drawn, then again with
 * `text-overflow: clip; overflow: visible` (the browser then draws the real characters where it would have drawn an ellipsis, inside the same box),
 * and the two images are compared byte for byte. Equal images: nothing was cut. Different images: the browser drew an ellipsis. (Two readings of
 * ONE instrument are not two pieces of evidence; this one reads pixels.) The element's inline style is restored afterwards.
 */
const cutInPixels = async (selector: string): Promise<boolean> => {
  const part = p().locator(`[data-testid="di-scan-statusbar"] ${selector}`);
  const asDrawn = await part.screenshot();
  await part.evaluate((el) => {
    (el as HTMLElement).style.textOverflow = 'clip';
    (el as HTMLElement).style.overflow = 'visible';
  });
  const unclipped = await part.screenshot();
  await part.evaluate((el) => {
    (el as HTMLElement).style.textOverflow = '';
    (el as HTMLElement).style.overflow = '';
  });
  return !asDrawn.equals(unclipped);
};

test(
  'THE RENDERED FIT: every real state sits in the approved geometry (the specimen\'s own boxes), nothing overlaps, and what does not fit its box is MEASURED',
  async () => {
    const variants: Array<{ name: string; hook: (request: Record<string, unknown>, reply: DiCodeReview) => DiCodeReview; pick?: string }> = [
      { name: 'ordinary', hook: (_r, reply) => reply },
      {
        name: 'long-branch',
        hook: (_r, reply) => (reply.view === 'overview' && reply.target !== null ? { ...reply, target: { ...reply.target, branch: LONG_BRANCH } } : reply),
      },
      {
        name: 'detached',
        hook: (_r, reply) => (reply.view === 'overview' && reply.target !== null ? { ...reply, target: { ...reply.target, branch: null, detached: true } } : reply),
      },
      {
        name: 'filters-not-run',
        hook: (_r, reply) => (reply.comparison === null ? reply : { ...reply, comparison: { ...reply.comparison, filtersNotRun: ['lfs', 'crlf-smudge'] } }),
      },
      {
        name: 'empty-listing',
        hook: (_r, reply) =>
          reply.view === 'overview' && reply.overview !== null
            ? { ...reply, overview: { files: [], totals: { files: 0, additions: 0, deletions: 0, withheld: 0, unshown: 0 } } }
            : reply,
      },
      { name: 'withheld-file', hook: (_r, reply) => reply, pick: '.env' },
      { name: 'binary-file', hook: (_r, reply) => reply, pick: 'bin/blob.bin' },
    ];
    const recorded: Record<string, unknown> = {};
    const problems: string[] = [];
    if (FIT_OUT !== null) fs.mkdirSync(FIT_OUT, { recursive: true });

    for (const size of FIT_SIZES) {
      // the specimen at this size: the approved geometry every real state must equal
      const lock = await browser!.newPage({ viewport: size });
      await lock.goto(`${lockUrl()}?di-scan-view=diff`, { waitUntil: 'domcontentloaded' });
      await lock.waitForSelector('[data-testid="di-scan-statusbar"]');
      page = lock;
      const specimen = await measureDiff();
      if (FIT_OUT !== null) await lock.screenshot({ path: path.join(FIT_OUT, `specimen-${size.width}x${size.height}.png`) });
      await lock.close();
      page = null;
      recorded[`specimen-${size.width}`] = specimen;

      for (const variant of variants) {
        requests = [];
        replyHook = variant.hook;
        await boot(doc.states.live, size);
        await openCodeDiff();
        await waitForSource('read');
        await p().waitForSelector(variant.name === 'empty-listing' ? '[data-testid="di-scan-diff-empty-listing"]' : '[data-testid="di-scan-editor"] .di-scan-diff-line');
        if (variant.pick !== undefined) {
          await p().click(`[data-testid="di-scan-tree-file"][data-file-id="${entryOf(variant.pick).fileId}"]`);
          await p().waitForSelector('[data-testid="di-scan-file-notice"]');
        }
        await p().waitForTimeout(250);
        const m = await measureDiff();
        recorded[`${variant.name}-${size.width}`] = m;
        if (FIT_OUT !== null) await p().screenshot({ path: path.join(FIT_OUT, `${variant.name}-${size.width}x${size.height}.png`) });
        const where = `${variant.name} at ${size.width}`;
        // the second, pixel instrument: the base chip and the branch name, cut or not, as DRAWN
        const pixelCut = { base: await cutInPixels('.di-scan-status-base'), branch: await cutInPixels('.di-scan-status-branch') };
        recorded[`${variant.name}-${size.width}-pixelCut`] = pixelCut;
        // the approved boxes are the specimen's, to the pixel
        for (const key of ['tree', 'editorBar', 'statusBar'] as const) {
          const a = m[key];
          const b = specimen[key];
          if (a === null || b === null) {
            problems.push(`${where}: ${key} missing (real ${JSON.stringify(a)}, specimen ${JSON.stringify(b)})`);
          } else if (Math.abs(a.w - b.w) > 0.5 || Math.abs(a.h - b.h) > 0.5) {
            problems.push(`${where}: ${key} is ${a.w}x${a.h}, the specimen's is ${b.w}x${b.h}`);
          }
        }
        if (m.statusOverlaps.length > 0) problems.push(`${where}: status bar items overlap: ${m.statusOverlaps.join('; ')}`);
        if (m.editorOverlaps.length > 0) problems.push(`${where}: editor bar items overlap: ${m.editorOverlaps.join('; ')}`);
        if (m.pageScroll.scrollWidth > m.pageScroll.clientWidth + 1) problems.push(`${where}: the page scrolls sideways`);
        // THE BASE REVISION IS THE CHECKOUT-COMPARISON IDENTIFIER: it is never cut, in any real state (a cut one is a shorter identifier, drawn with an
        // ellipsis for even a sub-pixel overflow). Only the branch NAME gives way.
        const base = m.parts.base;
        if (base === null) problems.push(`${where}: the base chip is missing`);
        else if (base.ellipsisDrawn) problems.push(`${where}: the base revision is cut by an ellipsis (it overflows its box by ${base.overflowPx} px)`);
        if (pixelCut.base) problems.push(`${where}: the base revision is cut IN THE PIXELS (the screenshot as drawn differs from the unclipped one)`);
        // the two instruments must AGREE, and the pixel one must see the known cut (positive control) and the known whole name (negative control)
        if (base !== null && pixelCut.base !== base.ellipsisDrawn) problems.push(`${where}: the layout instrument (ellipsisDrawn ${base.ellipsisDrawn}) and the pixel instrument (${pixelCut.base}) disagree about the base chip`);
        if (variant.name === 'long-branch' && size.width !== 1280 && !pixelCut.branch) problems.push(`${where}: the pixel instrument did not see the known cut of a 77-character branch name`);
        if ((variant.name === 'ordinary' || variant.name === 'detached') && pixelCut.branch) problems.push(`${where}: the pixel instrument saw a cut in a branch label that is drawn whole`);
        // THE INSTRUMENT SEES WHAT IT CLAIMS TO: where the branch name is cut by hundreds of pixels (integer properties agree), the fractional overflow must agree with them
        const branch = m.parts.branch;
        if (branch !== null && branch.naturalWidth - branch.givenWidth > 100 && Math.abs(branch.overflowPx - (branch.naturalWidth - branch.givenWidth)) > 1.5) {
          problems.push(`${where}: the fractional overflow (${branch.overflowPx}) does not agree with the integer one (${branch.naturalWidth - branch.givenWidth}): the instrument cannot be trusted`);
        }
        await page!.close();
        page = null;
      }
    }
    if (FIT_OUT !== null) fs.writeFileSync(path.join(FIT_OUT, 'measurements.json'), `${JSON.stringify(recorded, null, 1)}\n`);
    replyHook = null;
    assert.deepEqual(problems, [], 'the approved geometry holds for every real state');
  },
  STEPS_MS * 3,
);

// ─────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────
// THE ACCESSIBILITY TREE. Which instrument answers which question: THIS test asks "what does assistive technology get?", so it reads the browser's own
// accessibility tree (Chrome DevTools Protocol, `Accessibility.getFullAXTree`), which is exactly the instrument that sees visually hidden text and
// `title` attributes. It does NOT use a layout measure: a layout box includes visually hidden children and so reports an "overflow" for them that is not
// there in the pixels (the fit test's range measure did exactly that for the detached branch). For each element: its OWN accessibility node (role, name,
// description, whether it is ignored) is recorded, and the facts it EXPOSES are every name and description of the accessibility nodes whose DOM node is
// the element or lies below it, so the check does not depend on whether the browser kept the element's own node.
type AxExposure = { present: boolean; ignored: boolean; role: string; name: string; description: string; exposed: string[] };

const accessibilityOf = async (selectors: Record<string, string>): Promise<Record<string, AxExposure>> => {
  const cdp = await p().context().newCDPSession(p());
  try {
    await cdp.send('DOM.enable');
    await cdp.send('Accessibility.enable');
    const { root } = await cdp.send('DOM.getDocument', { depth: 0 });
    const { nodes } = await cdp.send('Accessibility.getFullAXTree');
    const str = (v: { value?: unknown } | undefined): string => (typeof v?.value === 'string' ? v.value : '');
    const out: Record<string, AxExposure> = {};
    for (const [key, selector] of Object.entries(selectors)) {
      const { nodeId } = await cdp.send('DOM.querySelector', { nodeId: root.nodeId, selector });
      const { node } = await cdp.send('DOM.describeNode', { nodeId, depth: -1 });
      const below = new Set<number>();
      const collect = (n: { backendNodeId: number; children?: Array<{ backendNodeId: number; children?: unknown[] }> }): void => {
        below.add(n.backendNodeId);
        for (const c of n.children ?? []) collect(c as Parameters<typeof collect>[0]);
      };
      collect(node as Parameters<typeof collect>[0]);
      const own = nodes.find((n) => n.backendDOMNodeId === node.backendNodeId);
      const inside = nodes.filter((n) => n.backendDOMNodeId !== undefined && below.has(n.backendDOMNodeId) && n.ignored !== true);
      out[key] = {
        present: own !== undefined,
        ignored: own?.ignored === true,
        role: str(own?.role),
        name: str(own?.name),
        description: str(own?.description),
        exposed: inside.flatMap((n) => [str(n.name), str(n.description)]).filter((s) => s !== ''),
      };
    }
    return out;
  } finally {
    await cdp.detach();
  }
};

/** What the element exposes, as one string with whitespace collapsed (the hidden " HEAD" starts with a space). */
const exposedText = (e: AxExposure): string => [e.name, e.description, ...e.exposed].join(' ').replace(/\s+/g, ' ');

test(
  'THE ACCESSIBILITY TREE (the browser\'s own): every fact the status bar\'s visible text drops is exposed, by name or description, on the right element',
  async () => {
    const head = doc.replies.overview.target!.head;
    const baseOid = doc.replies.overview.comparison!.baseOid;
    const SELECTORS = {
      footer: '[data-testid="di-scan-statusbar"]',
      branch: '[data-testid="di-scan-statusbar"] .di-scan-status-branch',
      base: '[data-testid="di-scan-statusbar"] .di-scan-status-base',
      format: '[data-testid="di-scan-status-format"]',
    };
    const states: Array<{ name: string; hook: (request: Record<string, unknown>, reply: DiCodeReview) => DiCodeReview }> = [
      { name: 'ordinary', hook: (_r, reply) => reply },
      { name: 'detached', hook: (_r, reply) => (reply.view === 'overview' && reply.target !== null ? { ...reply, target: { ...reply.target, branch: null, detached: true } } : reply) },
      { name: 'long-branch', hook: (_r, reply) => (reply.view === 'overview' && reply.target !== null ? { ...reply, target: { ...reply.target, branch: LONG_BRANCH } } : reply) },
    ];
    const recorded: Record<string, Record<string, AxExposure>> = {};
    for (const state of states) {
      requests = [];
      replyHook = state.hook;
      await boot(doc.states.live, { width: 1280, height: 720 });
      await openCodeDiff();
      await waitForSource('read');
      await p().waitForSelector('[data-testid="di-scan-editor"] .di-scan-diff-line');
      await p().waitForTimeout(250);
      const ax = await accessibilityOf(SELECTORS);
      recorded[state.name] = ax;
      const at = (name: keyof typeof SELECTORS) => exposedText(ax[name]!);
      for (const name of ['footer', 'branch', 'base', 'format'] as const) assert.ok(ax[name]!.present || ax[name]!.exposed.length > 0, `${state.name}: ${name} is in the accessibility tree`);

      // the base chip: the FULL revision (the chip draws ten characters) and what it is, on the chip itself
      assert.ok(at('base').includes(baseOid), `${state.name}: the base chip exposes the full revision ${baseOid}: ${at('base')}`);
      assert.match(at('base'), /Compared with revision/, `${state.name}: the base chip says what the revision is`);
      // OBSERVED in the first run and now pinned: the browser exposes these elements as role "generic" (which carries no accessible name), and a title that
      // ADDS something to the element's own text (the base chip's, the format element's, a detached branch's) becomes its accessible DESCRIPTION, so those
      // facts are asserted on the description itself, exactly. A title that merely repeats the text (an ordinary branch) gets NO description (the browser drops
      // the duplicate); that branch's full name is asserted in the TEXT below.
      assert.equal(ax['base']!.description, `Compared with revision ${baseOid}`, `${state.name}: the base chip's accessible description`);
      assert.equal(
        ax['format']!.description,
        'Encoding: unknown to this review. Line ending: unknown to this review. Indentation: unknown to this review',
        `${state.name}: the format element's accessible description enumerates the three facts`,
      );
      // the three facts the route does not carry, enumerated, on the format element, in the new vocabulary
      for (const fact of ['Encoding: unknown to this review', 'Line ending: unknown to this review', 'Indentation: unknown to this review']) {
        assert.ok(at('format').includes(fact), `${state.name}: the format element exposes "${fact}": ${at('format')}`);
      }
      // nothing in the bar says "not determined", and nothing claims a format the route does not carry
      assert.doesNotMatch(at('footer'), /not determined/i, `${state.name}: ONE VOCABULARY in the tree too`);
      assert.doesNotMatch(at('footer'), /UTF-8|\bLF\b|CRLF|Spaces|Tabs/, `${state.name}: no encoding, line ending or indentation is claimed`);
      assert.doesNotMatch(at('format'), /UTF-8|\bLF\b|CRLF|Spaces|Tabs/, `${state.name}: (negative control) the format element claims none either`);

      if (state.name === 'ordinary') {
        assert.ok(at('branch').includes('main'), `ordinary: the branch exposes its name: ${at('branch')}`);
      }
      if (state.name === 'detached') {
        // the visible word is "Detached"; the hidden " HEAD" completes the name; the title carries the CHECKOUT's own identity, the only place it is stated
        assert.match(at('branch'), /Detached HEAD/, `detached: the branch's accessible text completes "Detached HEAD": ${at('branch')}`);
        assert.ok(at('branch').includes(`Detached HEAD at ${head}`), `detached: the checkout's identity (target.head) is exposed on the branch: ${at('branch')}`);
        assert.equal(ax['branch']!.description, `Detached HEAD at ${head}`, 'detached: the checkout identity is the branch\'s accessible DESCRIPTION, always present');
        // the hidden word is its own text in the tree, so the READ text is "Detached HEAD" even without the description
        assert.ok(ax['branch']!.exposed.some((e) => e.trim() === 'HEAD'), `detached: the hidden " HEAD" completes the word in the tree: ${JSON.stringify(ax['branch']!.exposed)}`);
        assert.ok(ax['branch']!.exposed.some((e) => e.trim() === 'Detached'), 'detached: the visible word is in the tree');
        assert.ok(!at('base').includes(`Detached HEAD at`), 'detached: the checkout is not stated as the base');
      }
      if (state.name === 'long-branch') {
        assert.ok(at('branch').includes(LONG_BRANCH), `long-branch: the FULL name is exposed though the bar draws a truncated one: ${at('branch')}`);
      }
      await page!.close();
      page = null;
    }
    replyHook = null;
    if (FIT_OUT !== null) {
      fs.mkdirSync(FIT_OUT, { recursive: true });
      fs.writeFileSync(path.join(FIT_OUT, 'a11y-tree.json'), `${JSON.stringify(recorded, null, 1)}\n`);
    }
  },
  STEPS_MS * 2,
);

// ─────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────
// CANONICAL ROUND 15 (browser halves of the two blockers; the pure halves are in diScanDiffRead.test.ts).
test(
  'A BETTER OBSERVATION ARRIVES (browser): the overview says text, the file read says not UTF-8: the panel says what the read observed, never the overview\'s bare "Text"',
  async () => {
    const target = entryOf('src/util.py'); // the overview lists it as text
    assert.equal(target.state, 'text');
    replyHook = (request, reply) =>
      request.fileId === target.fileId && reply.file !== null
        ? { ...reply, file: { ...reply.file, state: 'not_utf8', reason: null, hunks: [], additions: null, deletions: null, linesTotal: 0, linesRetained: 0, truncated: false, bytesTotal: 0, digestSha256: null } }
        : reply;
    await boot();
    await openCodeDiff();
    await waitForSource('read');
    await p().click(`[data-testid="di-scan-tree-file"][data-file-id="${target.fileId}"]`);
    await p().waitForSelector('[data-testid="di-scan-file-notice"][data-state="not_utf8"]', { timeout: 6000 });
    assert.ok(requests.some((r) => r.fileId === target.fileId), 'the file WAS read (the notice is the read\'s, not the overview\'s)');
    const notice = (await p().locator('[data-testid="di-scan-file-notice"]').textContent()) ?? '';
    assert.match(notice, /Not valid UTF-8 text/, `the read's own observation is stated: ${notice}`);
    assert.match(notice, /cannot be shown faithfully/, `and why there is no content: ${notice}`);
    assert.equal(await p().locator('[data-testid="di-scan-editor"] .di-scan-diff-line').count(), 0, 'no lines are drawn');
  },
  STEPS_MS,
);

test(
  'A STALE REFUSAL (browser): a non-retryable refusal for file A that arrives after the reader selected file B is discarded, B IS READ, and B\'s lines are drawn (the pane is never left saying "reading" with nothing in flight)',
  async () => {
    const a = entryOf('src/util.py');
    const b = entryOf('styles/theme.css');
    replyHook = (request, reply) =>
      request.fileId === a.fileId
        ? { ...reply, state: 'refused', problem: 'file_diff_over_bound', detail: 'a synthetic refusal for this test', target: null, comparison: null, overview: null, file: null, observedAt: null }
        : reply;
    holdWhen = (r) => r.fileId === a.fileId; // A's read is in flight until the test releases it
    await boot();
    await openCodeDiff();
    await waitForSource('read');
    const choose = (e: { fileId: string }) => p().click(`[data-testid="di-scan-tree-file"][data-file-id="${e.fileId}"]`);
    await choose(a);
    const askedA = Date.now() + 8000;
    while (!requests.some((r) => r.fileId === a.fileId) && Date.now() < askedA) await p().waitForTimeout(50);
    assert.ok(requests.some((r) => r.fileId === a.fileId), 'A\'s read is in flight');
    await choose(b); // the reader moves on while A is in flight
    releaseHeld(); // A's refusal arrives now, for a selection that is no longer selected
    const askedB = Date.now() + 6000;
    while (!requests.some((r) => r.fileId === b.fileId) && Date.now() < askedB) await p().waitForTimeout(50);
    assert.ok(requests.some((r) => r.fileId === b.fileId), 'B WAS READ: the stale refusal did not block it');
    await p().waitForSelector('[data-testid="di-scan-editor"] .di-scan-diff-line', { timeout: 6000 });
    assert.equal(((await p().locator('[data-testid="di-scan-editor-path"]').textContent()) ?? '').trim(), 'styles/theme.css', 'B\'s own lines are drawn');
    assert.equal(await p().locator('[data-testid="di-scan-file-notice"][data-state="reading"]').count(), 0, 'nothing says "reading"');
    assert.equal(await p().locator('[data-testid="di-scan-file-notice"][data-state="refused"]').count(), 0, 'and A\'s refusal is not shown under B');
  },
  STEPS_MS,
);

test(
  'A MODE-ONLY CHANGE (browser, the real emission): the route lists it as no_content_diff and the read agrees; the panel says no line differs and why, with no lines drawn',
  async () => {
    const target = entryOf('src/util.py');
    replyHook = (request, reply) => {
      if (request.fileId === undefined && reply.overview !== null) {
        const files = reply.overview.files.map((f) => (f.fileId === target.fileId ? { ...f, state: 'no_content_diff', additions: 0, deletions: 0 } : f));
        return { ...reply, overview: { ...reply.overview, files } };
      }
      if (request.fileId === target.fileId && reply.file !== null) {
        return {
          ...reply,
          file: { ...reply.file, state: 'no_content_diff', reason: null, hunks: [], additions: 0, deletions: 0, truncated: false, linesTotal: 0, linesRetained: 0, bytesTotal: 0, digestSha256: null, modeChange: { old: '100644', new: '100755' } },
        };
      }
      return reply;
    };
    await boot();
    await openCodeDiff();
    await waitForSource('read');
    await p().click(`[data-testid="di-scan-tree-file"][data-file-id="${target.fileId}"]`);
    await p().waitForSelector('[data-testid="di-scan-file-notice"][data-state="no_content_diff"]', { timeout: 6000 });
    assert.ok(requests.some((r) => r.fileId === target.fileId), 'the file was read (a mode-only change is opened)');
    const notice = (await p().locator('[data-testid="di-scan-file-notice"]').textContent()) ?? '';
    assert.match(notice, /No change to the text/, notice);
    assert.match(notice, /no line differs/, notice);
    assert.equal(await p().locator('[data-testid="di-scan-editor"] .di-scan-diff-line').count(), 0, 'no lines are drawn');
  },
  STEPS_MS,
);

// ─────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────
// CANONICAL ROUND 16 (browser halves).
test(
  'A READ BELONGS TO THE REQUEST THAT ASKED FOR IT (browser): an overview is held, the Code Diff is closed and reopened, the OLD overview arrives FIRST and then the fresh one: the reader sees the FRESH reading, never the old one taken for it',
  async () => {
    let overviews = 0;
    replyHook = (request, reply) => {
      if (request.fileId !== undefined || reply.target === null) return reply;
      overviews += 1;
      return overviews === 1 ? { ...reply, target: { ...reply.target, branch: 'old-branch' } } : reply; // the first read is distinguishable
    };
    holdWhen = (r) => r.fileId === undefined; // every overview is held until released
    await boot();
    await openCodeDiff();
    const deadline = Date.now() + 8000;
    while (requests.filter((r) => r.fileId === undefined).length < 1 && Date.now() < deadline) await p().waitForTimeout(50);
    assert.equal(requests.filter((r) => r.fileId === undefined).length, 1, 'the first overview read is in flight (held)');
    await p().click('[data-testid="di-scan-back"]'); // close the Code Diff while its read is in flight
    await p().waitForSelector('[data-testid="di-scan-stage"][data-view="mission"]');
    await p().click('[data-testid="di-scan-code-diff"]'); // reopen the SAME offer
    const again = Date.now() + 8000;
    while (requests.filter((r) => r.fileId === undefined).length < 2 && Date.now() < again) await p().waitForTimeout(50);
    assert.equal(requests.filter((r) => r.fileId === undefined).length, 2, 'the reopened session made its own read');
    const ids = requests.filter((r) => r.fileId === undefined).map((r) => String(r.requestId));
    assert.equal(new Set(ids).size, 2, `the two reads name themselves differently: ${ids}`);
    releaseHeld(); // the OLD overview is delivered FIRST, then the fresh one
    await waitForSource('read');
    await p().waitForTimeout(500);
    const bar = (await p().locator('[data-testid="di-scan-statusbar"]').textContent()) ?? '';
    assert.match(bar, /main/, `the fresh reading is what is shown: ${bar}`);
    assert.doesNotMatch(bar, /old-branch/, `the old reading is not taken for the fresh read: ${bar}`);
  },
  STEPS_MS,
);

test(
  'A STALE REFUSAL THAT SAID THE WORKSPACE MOVED (browser): file A is read, the reader selects a file that needs NO read, A\'s snapshot_moved refusal arrives: the listing is still marked as changed since it was read (no later refusal can restore it)',
  async () => {
    const a = entryOf('src/util.py');
    const b = entryOf('bin/blob.bin'); // binary: B needs no read
    replyHook = (request, reply) =>
      request.fileId === a.fileId
        ? { ...reply, state: 'refused', problem: 'snapshot_moved', detail: 'a synthetic refusal for this test', target: null, comparison: null, overview: null, file: null, observedAt: null }
        : reply;
    holdWhen = (r) => r.fileId === a.fileId;
    await boot();
    await openCodeDiff();
    await waitForSource('read');
    const choose = (e: { fileId: string }) => p().click(`[data-testid="di-scan-tree-file"][data-file-id="${e.fileId}"]`);
    await choose(a);
    const askedA = Date.now() + 8000;
    while (!requests.some((r) => r.fileId === a.fileId) && Date.now() < askedA) await p().waitForTimeout(50);
    await choose(b);
    await p().waitForSelector('[data-testid="di-scan-file-notice"][data-state="binary"]', { timeout: 6000 });
    releaseHeld(); // A's snapshot_moved arrives for a selection that is no longer selected
    await p().waitForFunction(() => /changed since it was read/.test(document.querySelector('[data-testid="di-scan-stage"]')?.textContent ?? ''), null, { timeout: 6000 });
    assert.equal(await p().locator('[data-testid="di-scan-file-notice"][data-state="binary"]').count(), 1, 'B is still what B is');
    assert.equal(await p().locator('[data-testid="di-scan-file-notice"][data-state="reading"]').count(), 0, 'and nothing says "reading"');
    assert.equal(requests.filter((r) => r.fileId === b.fileId).length, 0, 'no read was made for a file that needs none');
  },
  STEPS_MS,
);

// ─────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────
// CANONICAL ROUND 17: ANSWER DELIVERY. The app kept the latest `diCodeReview` answer in ONE React state slot, so two answers handled before a render kept only the
// last. When the reopened session's read is refused AT ONCE (read_in_progress) and the OLD read's answer follows, the damaging order is CURRENT FIRST, STALE LAST:
// the slot ends up holding the stale answer, the id refuses it, and the refusal that mattered is already overwritten and gone: the read stays pending with nothing in
// flight, the face says "reading" for ever and no retry is ever sent. The opposite order is a COMFORTABLE order: the slot ends holding the GOOD answer and the defect
// cannot show, so a test written in it would pass on broken code. Both frames are handed to the app's own WebSocket inside ONE task, which makes the batch happen BY
// CONSTRUCTION rather than by winning a race with the browser's task scheduling.
/** The reopen sequence with BOTH overview replies held: the first read's reply is the OLD reading, the second's is the office's read_in_progress refusal; returns once both are held. */
async function reopenWithTwoHeldReplies(): Promise<{ stale: string; current: string }> {
  let overviews = 0;
  replyHook = (request, reply) => {
    if (request.fileId !== undefined || reply.target === null) return reply;
    overviews += 1;
    if (overviews === 1) return { ...reply, target: { ...reply.target, branch: 'old-branch' } };
    if (overviews === 2) {
      return { ...reply, state: 'refused', problem: 'read_in_progress', detail: 'another review read is in progress', snapshot: null, target: null, comparison: null, overview: null, file: null, observedAt: null };
    }
    return reply; // the retry is answered with the fresh reading at once
  };
  let heldSoFar = 0;
  holdWhen = (r) => r.fileId === undefined && ++heldSoFar <= 2;
  await boot(doc.states.live, undefined, { tapSocket: true });
  await openCodeDiff();
  await waitUntil(() => overviewReads() >= 1, 'the first overview read was sent');
  await p().click('[data-testid="di-scan-back"]'); // close while the first read is in flight
  await p().waitForSelector('[data-testid="di-scan-stage"][data-view="mission"]');
  await p().click('[data-testid="di-scan-code-diff"]'); // reopen the SAME offer
  await waitUntil(() => overviewReads() >= 2, 'the reopened session sent its own read');
  assert.equal(heldFrames.length, 2, 'both replies are held');
  assert.equal(await source(), 'reading', 'both reads are in flight: the face says reading');
  return { stale: heldFrames[0]!, current: heldFrames[1]! };
}
const overviewReads = () => requests.filter((r) => r.fileId === undefined).length;
async function waitUntil(cond: () => boolean, what: string, ms = 8000): Promise<void> {
  const deadline = Date.now() + ms;
  while (!cond() && Date.now() < deadline) await p().waitForTimeout(50);
  assert.ok(cond(), what);
}
/** Hand the page's own WebSocket these frames inside ONE task: the app's transport handler runs for each, with no render between them. */
async function deliverInOneTask(frames: string[]): Promise<void> {
  await p().evaluate((texts) => {
    const sockets = (window as unknown as { __tappedSockets: WebSocket[] }).__tappedSockets;
    const ws = sockets[sockets.length - 1]!;
    for (const data of texts) ws.dispatchEvent(new MessageEvent('message', { data }));
  }, frames);
}
/** The end state that matters: the refusal was NOT lost, so the read is asked again (exactly once), and the face then SHOWS the fresh reading, never the old one. */
async function expectTheReadRecovered(): Promise<void> {
  await waitUntil(() => overviewReads() >= 3, `the refused read was asked again (a retry): the refusal reached the session. Reads sent: ${overviewReads()}; the face says "${await source()}"`, 6000);
  await waitForSource('read');
  const bar = (await p().locator('[data-testid="di-scan-statusbar"]').textContent()) ?? '';
  assert.match(bar, /main/, `the fresh reading is what is shown: ${bar}`);
  assert.doesNotMatch(bar, /old-branch/, `the old reading is not shown: ${bar}`);
  await p().waitForTimeout(800);
  assert.equal(overviewReads(), 3, 'exactly one retry was sent (a stale answer is not a reason to ask again)');
  const ids = requests.filter((r) => r.fileId === undefined).map((r) => String(r.requestId));
  assert.equal(new Set(ids).size, 3, `the three reads name themselves differently: ${ids}`);
}

test(
  'ANSWER DELIVERY, THE DAMAGING ORDER (app level): the refusal for the NEW read arrives FIRST and the OLD read\'s answer LAST, in one batch: the refusal is not lost, the read is asked again, and the fresh reading shows',
  async () => {
    const { stale, current } = await reopenWithTwoHeldReplies();
    await deliverInOneTask([current, stale]); // CURRENT first, STALE last, one task
    await expectTheReadRecovered();
  },
  STEPS_MS,
);

// ─────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────
// I3: A FINISHED (COMPLETED) MISSION, through the actual App. The office state is the REAL projection of the emitted COMPLETED Mission
// (`test/fixtures/di-office-state-evidence-historical-v2.FIXTURE.json`); the "office" here is this test's own socket, which answers a Mission-level read with the
// recorded overview RE-ADDRESSED to the reference asked by (a labelled, synthetic variation: it shows the page's behaviour, never that a backend read a finished
// Mission's real worktree).
const finishedDoc = JSON.parse(
  fs.readFileSync(path.join(root, 'test/fixtures/di-office-state-evidence-historical-v2.FIXTURE.json'), 'utf-8'),
) as { states: Record<'live' | 'moved' | 'stale', DiOfficeState> };
const finishedMission = finishedDoc.states.live.missions.missions.find((m) => m.sourceKind === 'mission_core')!;
const FINISHED = {
  missionRef: finishedMission.missionRef,
  taskId: finishedMission.codeReview!.taskId!,
  listing: finishedMission.codeReview!.listing!,
};

/**
 * The rail lists an orchestrator per RUNNING Mission ("No Orchestrators are running." otherwise: read from the page when the first version of this test waited for one), so a state
 * with only a finished Mission is waited for on the rail's Missions entry. The helper then follows the route a person does: the Missions entry, the Mission's row, Code Diff.
 */
const MISSIONS_ENTRY = { railSelector: '[data-testid="di-rail-missions"]' };
async function openFinishedCodeDiff(ref: string): Promise<void> {
  try {
    // The Missions page (its default status is All) lists the Mission as a row with an open button.
    await p().click('[data-testid="di-rail-missions"]', { timeout: 10_000 });
    await p().click(`[data-testid="di-missions-row"][data-mission-ref="${ref}"] [data-testid="di-missions-open"]`, { timeout: 10_000 });
    await p().waitForSelector('[data-testid="di-scan-stage"][data-view="mission"]');
    assert.equal(await p().getAttribute('[data-testid="di-scan-stage"]', 'data-mission-ref'), ref);
    await p().click('[data-testid="di-scan-code-diff"]');
    await p().waitForSelector('[data-testid="di-scan-stage"][data-view="diff"]');
  } catch (e) {
    // What the page shows when the route a person follows does not open (the way the request-panel tests report their own state), so a failure here is read, not guessed at.
    const shown = await p()
      .evaluate(() => ({
        testids: Array.from(document.querySelectorAll('[data-testid]'), (el) => el.getAttribute('data-testid')).slice(0, 60),
        text: document.body.innerText.slice(0, 500),
      }))
      .catch(() => null);
    throw new Error(`${String(e).split('\n')[0]} -- ${JSON.stringify(shown)}`, { cause: e });
  }
}

test(
  'A FINISHED MISSION READS BY ITS OWN REFERENCE (browser): the read names the Mission, the task and the listing and NO member, and the real tree and lines are drawn in the nested editor with no modal',
  async () => {
    await boot(finishedDoc.states.live, undefined, MISSIONS_ENTRY);
    await openFinishedCodeDiff(FINISHED.missionRef);

    // the overview read: the four reference keys plus the one opaque request id; no member key, no path
    await waitForReads(1, 'Code Diff opened for a finished Mission');
    assert.deepEqual(Object.keys(requests[0]!).sort(), ['listing', 'missionRef', 'requestId', 'taskId', 'type']);
    const { requestId, ...asked } = requests[0]!;
    assert.match(String(requestId), /^[A-Za-z0-9._-]{1,64}$/);
    assert.deepEqual(asked, { type: 'diCodeReviewMissionRead', ...FINISHED });

    // the answer (member field empty) is applied: every listed file, by the id the route gave it
    await waitForSource('read');
    assert.deepEqual([...((await treeIds()) as string[])].sort(), overview.files.map((f) => f.fileId).sort());

    // choosing a file reads THAT file by the same kind of reference: six keys (the snapshot the overview carried and the file id it listed), still no member
    const app = entryOf('src/app.py');
    await p().click(`[data-testid="di-scan-tree-file"][data-file-id="${app.fileId}"]`);
    await waitForReads(2, 'a file chosen');
    const fileRead = requests[requests.length - 1]!;
    assert.deepEqual(Object.keys(fileRead).sort(), ['fileId', 'listing', 'missionRef', 'requestId', 'snapshot', 'taskId', 'type']);
    assert.equal(fileRead.type, 'diCodeReviewMissionRead');
    assert.equal(fileRead.fileId, app.fileId);
    assert.equal(fileRead.snapshot, doc.replies.overview.snapshot);
    await p().waitForSelector('[data-testid="di-scan-editor"] .di-scan-diff-line[data-kind="add"]');
    assert.equal(await p().locator('[role="dialog"]').count(), 0, 'no modal');
    assert.equal(requests.filter((r) => r.type === 'diCodeReviewRead').length, 0, 'no member-bound read was made');
  },
  STEPS_MS,
);

test(
  'A FINISHED MISSION THE OFFICE DOES NOT LIST AS ESTABLISHED (browser, the stale office): the pane says so in the office\'s own words, nothing is asked, no modal',
  async () => {
    await boot(finishedDoc.states.stale, undefined, MISSIONS_ENTRY);
    await openFinishedCodeDiff(FINISHED.missionRef);
    await waitForSource('unavailable');
    const text = (await p().locator('[data-testid="di-scan-diff-unavailable"]').textContent()) ?? '';
    assert.ok(text.includes(UNAVAILABLE_WORDS.finished_execution_not_listed!), `the office's own reason is in the pane: ${text}`);
    assert.deepEqual(await treeIds(), [], 'no file is drawn');
    await p().waitForTimeout(500);
    assert.equal(requests.length, 0, 'nothing was asked of the office');
    assert.equal(await p().locator('[role="dialog"]').count(), 0, 'no modal');
  },
  STEPS_MS,
);

test(
  'A FINISHED MISSION THE BACKEND WILL NOT TIE TO ITS WORKSPACE (browser): the refusal is stated in the route\'s own words, once; never an empty diff, never "reading" forever',
  async () => {
    replyHook = (request, reply) =>
      request.fileId === undefined ? refusedOverview(reply, 'finished_workspace_not_corroborated', 'the workspace\'s own task record does not name this run') : reply;
    await boot(finishedDoc.states.live, undefined, MISSIONS_ENTRY);
    await openFinishedCodeDiff(FINISHED.missionRef);
    await waitForSource('refused');
    const said = problemWords('finished_workspace_not_corroborated', null);
    const text = (await p().locator('[data-testid="di-scan-diff-refused"]').textContent()) ?? '';
    assert.ok(text.includes(said.title), `the route's problem is stated: ${text}`);
    assert.deepEqual(await treeIds(), []);
    await p().waitForTimeout(900);
    assert.equal(requests.length, 1, 'a refusal that will not change by waiting is not retried');
    assert.equal(await p().locator('[role="dialog"]').count(), 0, 'no modal');
  },
  STEPS_MS,
);

test(
  'ANSWER DELIVERY, CONTROL (the comfortable order): STALE first and CURRENT last in one batch recovers too; this order alone could not show the defect',
  async () => {
    const { stale, current } = await reopenWithTwoHeldReplies();
    await deliverInOneTask([stale, current]);
    await expectTheReadRecovered();
  },
  STEPS_MS,
);

test(
  'ANSWER DELIVERY, CONTROL (separate tasks): the refusal and then, 100 ms later, the old answer recover too; the batch is what the damaging order adds',
  async () => {
    const { stale, current } = await reopenWithTwoHeldReplies();
    await deliverInOneTask([current]);
    await p().waitForTimeout(100);
    await deliverInOneTask([stale]);
    await expectTheReadRecovered();
  },
  STEPS_MS,
);
