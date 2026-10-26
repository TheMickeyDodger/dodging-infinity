/**
 * Item 3 (original 20): the live code review through the ACTUAL App, in a real browser.
 *
 * The page (`test/harness/di-app.html`) is the real app entry (`App`, `useExtensionMessages`, the
 * canvas, the panel's selection polling), fed over a REAL WebSocket this test serves at `/ws`
 * with office states and review answers that the real server code produced
 * (`test/fixtures/di-office-state-code-review-v2.FIXTURE.json`, from the backend's exported
 * answers over a fixture git workspace). The agent is selected exactly as a canvas click selects
 * it; the entry is the real button in the real inspector; the modal is the real component.
 *
 * Proves: the entry appears only where a review is offered (and says why where it is not); a read
 * names a reference and never a path; the modal opens large, goes full screen, is a focus trap that
 * restores focus, closes on Escape, and keeps every key away from the office; the overview states
 * the comparison and shares no authorship; files, folders, search, unified and side-by-side, text
 * size, collapse and section navigation work by keyboard; every file state is explained; truncated
 * and clipped text says how much; hostile text is inert and control characters are visible; a
 * moved workspace is labelled out of date; and a lost connection, an expired office, an inactive
 * assignment, a replaced task or a vanished agent CLEARS the content and keeps it cleared.
 *
 * Browser: the installed Chrome (playwright-core, channel `chrome`), else Playwright's Chromium;
 * with neither, every test is skipped with an explicit message. Set DI_REVIEW_SHOTS=<dir> to also
 * save the screenshots (fixture data: never live acceptance).
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

import { checkDiSeatPreferences } from '../../core/src/diSeatPreferences.ts';
import type { DiCodeReview, DiOfficeState } from '../../core/src/messages.ts';
import { reviewNotOfferedConsequence } from '../src/components/diCodeReviewModel.ts';
import type { A11ySweep } from './diChromeA11y.ts';
import { a11ySweep } from './diChromeA11y.ts';
import { renderedCopy } from './diRenderedCopy.ts';
import type { TextFitControl } from './diTextFit.ts';
import { textFit } from './diTextFit.ts';
import type { TokenScopeAudit } from './diTokenScope.ts';
import { tokenScopeAudit } from './diTokenScope.ts';

const STEPS_MS = 90_000;

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const doc = JSON.parse(
  fs.readFileSync(path.join(root, 'test/fixtures/di-office-state-code-review-v2.FIXTURE.json'), 'utf-8'),
) as {
  reference: { missionRef: string; memberRef: string; taskId: string; listing: string };
  states: Record<'live' | 'stale' | 'inactive' | 'moved' | 'agentGone' | 'unavailable', DiOfficeState>;
  replies: { overview: DiCodeReview; files: Record<string, DiCodeReview> };
};
const S = doc.states;
const REF = doc.reference;
const member = S.live.missions.missions[0].members.members[0];
const CHARACTER = member.characterId!;
const SHOTS = process.env['DI_REVIEW_SHOTS'] ?? null;

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
let readyOnOpen = false;
/** Every diCodeReviewRead the page sent, exactly as received. */
let requests: Array<Record<string, unknown>> = [];
/** Edits a test makes to the reply the "office" sends (a labelled, synthetic variation). */
let replyHook: ((request: Record<string, unknown>, reply: DiCodeReview) => DiCodeReview | null) | null = null;
let replyDelayMs = 0;
let otherMessages: string[] = [];
/** The identity-keyed seat saves the page sent (an allowed LOCAL-persistence message, classified in the websocket handler and validated in the session test). */
let seatPreferenceSaves: unknown[] = [];
/** The overview pings the Mission request entry sends (item 4); allowed only in exactly one shape. */
let overviewPings: Array<Record<string, unknown>> = [];

/**
 * The ONE message besides code review reads that this view's session may carry: the Mission request
 * entry's read of its overview (`op: 'refresh'`), sent from the office panel whether or not any review
 * is open. It is a READ to render the entry's summary; it advances nothing. Exactly `{type, seq, op}`:
 * a refresh that carries anything more, and every other `diMissionRequest` op (request, offer,
 * conversation, turn, submit, decide, withdraw, run...), is NOT allowed and stays in `otherMessages`,
 * where the assertion at the end of the session fails on it. In particular nothing carrying `reply`,
 * `offerDigest`, `expiresAt`, `scope` or `deliveryTargets` can leave this view unnoticed.
 */
function isOverviewPing(msg: Record<string, unknown>): boolean {
  const keys = Object.keys(msg).sort();
  return (
    msg.type === 'diMissionRequest' &&
    msg.op === 'refresh' &&
    keys.length === 3 &&
    keys[0] === 'op' &&
    keys[1] === 'seq' &&
    keys[2] === 'type' &&
    typeof msg.seq === 'number'
  );
}

const expectConnection = (reconnect = false) => {
  readyOnOpen = reconnect;
  connected = new Promise((resolve) => (resolveConnected = resolve));
};

/** The reply the real office relays for a request (looked up; a moved snapshot is a refusal). */
function replyFor(request: Record<string, unknown>): DiCodeReview | null {
  const stored =
    request.fileId === undefined
      ? doc.replies.overview
      : Object.values(doc.replies.files).find((r) => r.fileId === request.fileId);
  if (stored === undefined) return null;
  return replyHook ? replyHook(request, structuredClone(stored)) : structuredClone(stored);
}

function serve(ws: WebSocket) {
  ws.on('message', (data) => {
    const msg = JSON.parse(String(data)) as Record<string, unknown>;
    if (msg.type === 'diCodeReviewRead') {
      requests.push(msg);
      const reply = replyFor(msg);
      if (reply === null) return;
      const send = () => ws.readyState === ws.OPEN && ws.send(JSON.stringify(reply));
      if (replyDelayMs > 0) setTimeout(send, replyDelayMs);
      else send();
    } else if (isOverviewPing(msg)) {
      overviewPings.push(msg);
    } else if (msg.type === 'saveDiSeatPreferences') {
      // An ALLOWED LOCAL-PERSISTENCE message (P2-C), like `saveAgentSeats`: the OFFICE saves where each identity sits (a seating preference — no Mission
      // state, no request, nothing canonical) the first time it is seated and afterwards only when the map changes. It is not the code-review view's
      // traffic; it is recorded with its payload and validated in the session test, and `otherMessages` stays strict for everything else.
      seatPreferenceSaves.push(msg.state);
    } else if (msg.type !== 'webviewReady' && msg.type !== 'saveAgentSeats') {
      otherMessages.push(msg.type === 'diMissionRequest' ? `diMissionRequest:${String(msg.op)}` : String(msg.type));
    }
  });
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
    wss!.handleUpgrade(req, sock, head, (ws) => {
      serve(ws);
      if (readyOnOpen) {
        socket = ws;
        resolveConnected(ws);
        return;
      }
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
  // Headless Chromium is launched with --hide-scrollbars by default, which would make every scrollbar
  // 0px wide and every measurement of "the sideways scrollbar is drawn" vacuous: a real browser draws them.
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
  await browser?.close();
  wss?.close();
  await server?.close();
}, STEPS_MS);

let dialogs = 0;
let outsideRequests: string[] = [];
const VIEW = { width: 1600, height: 1000 };

beforeEach(async (ctx) => {
  if (unavailable) {
    console.warn(`SKIPPED (code review): ${unavailable}`);
    ctx.skip();
    return;
  }
  await page?.close();
  requests = [];
  otherMessages = [];
  seatPreferenceSaves = [];
  overviewPings = [];
  replyHook = null;
  replyDelayMs = 0;
  dialogs = 0;
  outsideRequests = [];
  expectConnection();
  page = await browser!.newPage({ viewport: VIEW });
  page.on('dialog', (d) => {
    dialogs += 1;
    void d.dismiss();
  });
  page.on('request', (r) => {
    const u = new URL(r.url());
    if (u.hostname !== 'localhost' && u.protocol.startsWith('http')) outsideRequests.push(r.url());
  });
  await page.goto(url, { waitUntil: 'domcontentloaded' });
  await connected;
}, STEPS_MS);

const p = () => page!;
const send = (state: DiOfficeState) => socket!.send(JSON.stringify(state));
const selectOnCanvas = (id: number | null) =>
  p().evaluate((i) => {
    const office = (window as unknown as { __diHarnessOffice: Office }).__diHarnessOffice;
    office.selectedAgentId = i;
    office.cameraFollowId = i;
  }, id);
const selection = () =>
  p().evaluate(
    () => (window as unknown as { __diHarnessOffice: Office }).__diHarnessOffice.selectedAgentId,
  );
async function waitForCharacter(id: number) {
  await p().waitForFunction(
    (i) => {
      const office = (window as unknown as { __diHarnessOffice?: Office }).__diHarnessOffice;
      const c = office?.characters.get(i);
      return c !== undefined && c.matrixEffect !== 'despawn';
    },
    id,
    { timeout: 30_000 },
  );
}
async function openPanel() {
  await p().waitForSelector('[data-testid="di-office-panel"], [data-testid="di-office-panel-collapsed"]', {
    timeout: 30_000,
  });
  const collapsed = await p().$('[data-testid="di-office-panel-collapsed"]');
  if (collapsed) await collapsed.click();
}
const T = (id: string) => `[data-testid="${id}"]`;
const text = (sel: string) => p().$eval(sel, (el) => (el as HTMLElement).innerText);
const exists = async (sel: string) => (await p().$(sel)) !== null;

/** The agent is live and selected; the inspector shows it. */
async function selectAgent(state: DiOfficeState = S.live) {
  send(state);
  await openPanel();
  await waitForCharacter(CHARACTER);
  await selectOnCanvas(CHARACTER);
  await p().waitForSelector(T('di-selected-member'), { timeout: 15_000 });
}
async function openReview() {
  await selectAgent();
  await p().waitForSelector(T('di-open-code-review'));
  await p().click(T('di-open-code-review'));
  await p().waitForSelector(T('di-review-dialog'));
  await p().waitForSelector(T('di-review-tree'), { timeout: 20_000 });
}
const rowFor = (file: string) => `${T('di-review-row')}[data-path="${file}"]`;
async function openFile(file: string) {
  await p().click(rowFor(file));
  // the heading follows the selection at once; the diff follows the office's answer
  await p().waitForFunction(
    (f) => {
      // the unified region is named "Changes in <file>, unified"; the side-by-side ones name their side
      const label = document.querySelector('[data-testid="di-review-code"]')?.getAttribute('aria-label') ?? '';
      return label.startsWith(`Changes in ${f},`) || label.endsWith(` of ${f}, side by side`);
    },
    file,
    { timeout: 20_000 },
  );
}
async function shot(name: string) {
  if (SHOTS === null) return;
  fs.mkdirSync(SHOTS, { recursive: true });
  await p().screenshot({ path: path.join(SHOTS, `${name}.png`) });
}
const focusInsideDialog = () =>
  p().evaluate(() => {
    const d = document.querySelector('[data-testid="di-review-dialog"]');
    return d !== null && d.contains(document.activeElement);
  });
type Containment = {
  frames: number;
  outside: string[];
  dispatchedOutside: string[];
  reached: string[];
  focusDropped: string[];
};
/**
 * Watches, IN THE PAGE and ACROSS a transition, whether focus and keys stay inside the open dialog: every animation frame
 * samples where focus is, every key is recorded where it was dispatched and whether it reached the window (the office), and
 * every time the browser drops focus is recorded. A check made once after the press cannot see a gap between two presses.
 */
const watchContainment = () =>
  p().evaluate(() => {
    const state: Containment = { frames: 0, outside: [], dispatchedOutside: [], reached: [], focusDropped: [] };
    (window as unknown as { __contain: Containment }).__contain = state;
    const dialog = () => document.querySelector('[data-testid="di-review-dialog"]');
    const name = (n: Element | null) =>
      n === null ? 'nothing' : n === document.body ? 'body' : `${n.tagName.toLowerCase()}[${n.getAttribute('data-testid') ?? ''}]`;
    const sample = () => {
      state.frames += 1;
      const d = dialog();
      if (d !== null && !d.contains(document.activeElement)) state.outside.push(`frame ${state.frames}: ${name(document.activeElement)}`);
      if (d !== null || state.frames < 3) requestAnimationFrame(sample);
    };
    requestAnimationFrame(sample);
    window.addEventListener(
      'keydown',
      (e) => {
        const d = dialog();
        if (d !== null && !d.contains(e.target as Node)) state.dispatchedOutside.push(`${e.key} -> ${name(e.target as Element)}`);
      },
      true,
    );
    window.addEventListener('keydown', (e) => state.reached.push(e.key));
    document.addEventListener(
      'focusout',
      (e) => {
        if ((e as FocusEvent).relatedTarget === null) state.focusDropped.push(name(e.target as Element));
      },
      true,
    );
  });
const containment = () => p().evaluate(() => (window as unknown as { __contain: Containment }).__contain);
/** Lets the browser take n rendering turns (a focus the browser drops when a control disables is settled in one). */
const frames = (n: number) =>
  p().evaluate(
    (count) =>
      new Promise<void>((resolve) => {
        let k = 0;
        const step = () => (++k >= count ? resolve() : requestAnimationFrame(step));
        requestAnimationFrame(step);
      }),
    n,
  );
/** What a transition did to focus and keys, as one value: a failure shows all of it, not only the first thing that was wrong. */
async function containmentSummary(whilePending: boolean[]) {
  const seen = await containment();
  return {
    focusInsideDialogWhileTheControlWasDisabled: whilePending,
    framesWithFocusOutsideTheDialog: seen.outside,
    keysDispatchedOutsideTheDialog: seen.dispatchedOutside,
    keysThatReachedTheOffice: seen.reached,
  };
}
const activeInfo = () =>
  p().evaluate(() => {
    const a = document.activeElement as HTMLElement | null;
    return a === null
      ? 'nothing'
      : `${a.tagName.toLowerCase()}[${a.getAttribute('data-testid') ?? a.getAttribute('aria-label') ?? a.textContent?.slice(0, 20)}]`;
  });

test(
  'the entry appears for a live member of an active assignment — and says why it does not elsewhere',
  async () => {
    await selectAgent();
    const entry = await text(T('di-code-review-entry'));
    assert.match(entry, /Open code review/);
    assert.match(entry, /View only/);
    assert.match(entry, /team.s changes/);
    // feature13: the entry lives in the Selected DOCK, which never scrolls out of the panel (the old
    // pinned bar's invariant: the entry stays in reach however far the panel has scrolled). Proven
    // by scrolling the Missions list to its end and back: the entry's box does not move, in view.
    const inView = async () => {
      const box = await p().locator(T('di-open-code-review')).boundingBox();
      assert.ok(box !== null, 'the entry is offered in the dock while an agent is selected');
      assert.ok(box.y >= 0 && box.y + box.height <= VIEW.height, `in view: y=${box.y}`);
      return box;
    };
    const resting = await inView();
    await p().$eval(T('di-op-main'), (el) => {
      el.scrollTop = el.scrollHeight;
    });
    assert.deepEqual(await inView(), resting, 'scrolled to the end: the entry has not moved');
    await p().$eval(T('di-op-main'), (el) => {
      el.scrollTop = 0;
    });
    assert.deepEqual(await inView(), resting, 'scrolled back: the entry has not moved');
    assert.equal(await exists(T('di-review-dialog')), false, 'nothing opens by itself');
    assert.deepEqual(requests, [], 'selecting an agent reads nothing');
    // the same agent, its assignment no longer active: no button, the fact on the dock, and the
    // reason in words one step away (the inspector)
    send(S.inactive);
    await p().waitForSelector(T('di-dock-code-review-unavailable'), { timeout: 15_000 });
    assert.match(await text(T('di-dock-code-review-unavailable')), /not offered/);
    assert.equal(await exists(T('di-open-code-review')), false);
    await p().click(T('di-open-inspector-selection'));
    await p().waitForSelector(T('di-code-review-unavailable'), { timeout: 15_000 });
    const why = await text(T('di-code-review-unavailable'));
    assert.match(why, /not offered/);
    assert.match(why, /not active work/);
    await shot('entry-not-offered');
  },
  STEPS_MS,
);

test(
  'a member whose code review is not offered: its details say so ONCE, in its own summary (a single-member Mission)',
  async () => {
    const inactive = S.inactive.missions.missions[0].members.members;
    assert.equal(inactive.length, 1, 'the case: a single-member Mission');
    const sentence = reviewNotOfferedConsequence(inactive[0].codeReview!.reason);
    await selectAgent();
    send(S.inactive);
    await p().waitForSelector(T('di-dock-code-review-unavailable'), { timeout: 15_000 });
    await p().click(T('di-open-inspector-selection'));
    await p().waitForSelector(T('di-inspector-selected'), { timeout: 15_000 });
    // Counted in the VISIBLE text: the popup states it once, and the one kept is the summary's.
    const seen = await p().evaluate((s) => {
      const visible = (sel: string) =>
        (document.querySelector<HTMLElement>(sel)?.innerText ?? '').split(s).length - 1;
      return {
        popup: visible('[role="dialog"]'),
        summary: visible('[data-testid="di-inspector-selected"]'),
      };
    }, sentence);
    assert.deepEqual(seen, { popup: 1, summary: 1 });
  },
  STEPS_MS,
);

test(
  'opening reads the overview by reference only, and the overview states what is compared',
  async () => {
    await selectAgent();
    await shot('entry-offered');
    await p().click(T('di-open-code-review'));
    await p().waitForSelector(T('di-review-dialog'));
    await p().waitForSelector(T('di-review-tree'), { timeout: 20_000 });
    assert.equal(requests.length, 1);
    assert.deepEqual(Object.keys(requests[0]).sort(), ['listing', 'memberRef', 'missionRef', 'taskId', 'type']);
    assert.equal(requests[0].type, 'diCodeReviewRead');
    assert.equal(requests[0].missionRef, REF.missionRef);
    assert.equal(requests[0].listing, REF.listing);
    const dialog = p().locator(T('di-review-dialog'));
    assert.equal(await dialog.getAttribute('role'), 'dialog');
    assert.equal(await dialog.getAttribute('aria-modal'), 'true');
    assert.ok(await focusInsideDialog(), 'focus moved into the dialog');
    assert.match(await text(T('di-review-worktree')), /di-fixture-root\/workspace/);
    assert.match(await text(T('di-review-branch')), /main/);
    assert.match(await text(T('di-review-base')), /^[0-9a-f]{10}$/);
    assert.match(await text(T('di-review-totals')), /23 files changed · \+7025 · −110 · 6 withheld · 5 not shown as text/);
    const compare = await text(T('di-review-compare-line'));
    assert.match(compare, /compared with commit [0-9a-f]{10}/);
    assert.match(compare, /staged, unstaged and new files/);
    assert.match(compare, /Nothing here says who made a change/);
    assert.doesNotMatch(compare, /\b(author|authored by|committed by)\b/i);
    await p().click(T('di-review-details'));
    const details = await text(T('di-review-details-body'));
    assert.match(details, /Covered: staged changes[\s\S]*the commit a submodule points to/);
    assert.match(details, /Not covered: ignored files, what is inside a submodule/);
    assert.match(details, /A rename is shown when git pairs/);
    assert.match(details, /Whether a tracked one changed is judged from metadata the repository recorded for it[\s\S]*the second it was recorded[\s\S]*same size within that second may not be seen/);
    assert.equal(await p().locator(rowFor('src/app.py')).count(), 1);
    assert.equal(await p().locator(T('di-review-row')).count(), 23);
    await shot('overview-desktop');
  },
  STEPS_MS,
);

test(
  'a file opens by snapshot and file id; the diff is line-numbered, marked and syntax-coloured',
  async () => {
    await openReview();
    await openFile('src/app.py');
    const file = requests.find((r) => r.fileId !== undefined)!;
    assert.deepEqual(Object.keys(file).sort(), ['fileId', 'listing', 'memberRef', 'missionRef', 'snapshot', 'taskId', 'type']);
    assert.equal(file.snapshot, doc.replies.overview.snapshot);
    await p().waitForSelector(T('di-review-line'));
    const lines = await p().$$eval(T('di-review-line'), (els) =>
      els.map((el) => ({
        kind: el.getAttribute('data-kind'),
        text: (el as HTMLElement).innerText.replace(/\s+/g, ' ').trim(),
      })),
    );
    assert.deepEqual(
      lines.map((l) => l.kind),
      ['context', 'del', 'add'],
    );
    // a deleted line has only its OLD number, an added one only its NEW number
    assert.match(lines[0].text, /^1 1 def main\(\):$/);
    assert.match(lines[1].text, /^2 − return 1$/);
    assert.match(lines[2].text, /^2 \+ return 3$/);
    assert.match(await text(T('di-review-file-facts')), /1 section · 3 lines/);
    // keywords are coloured by token class, not by anything in the text
    const kinds = await p().$$eval('.di-rv-text span[class^="tok-"]', (els) => els.map((e) => e.className));
    assert.ok(kinds.includes('tok-keyword'), `def/return are keywords: ${kinds.join()}`);
    await shot('diff-unified');
  },
  STEPS_MS,
);

test(
  'side by side pairs the old line with its replacement; sections collapse; text size changes',
  async () => {
    await openReview();
    await openFile('src/syntax_sample.ts');
    await p().waitForSelector(T('di-review-hunk'));
    assert.equal(await p().locator(T('di-review-hunk')).count(), 2);
    await p().click(T('di-review-layout-split'));
    await p().waitForSelector(T('di-review-code-old'));
    assert.equal(await p().getAttribute(T('di-review-code'), 'data-layout'), 'split');
    const sides = await p().evaluate(() => {
      const kinds = (side: string) =>
        [...document.querySelectorAll(`[data-testid="di-review-line"][data-side="${side}"]`)].map((e) => e.getAttribute('data-kind'));
      return { old: kinds('old'), new: kinds('new') };
    });
    assert.equal(sides.old.length, sides.new.length, 'the two sides have the same rows');
    const firstChange = sides.old.map((kind, i) => [kind, sides.new[i]]);
    assert.ok(firstChange.some(([l, r]) => l === 'del' && r === 'add'), 'a changed line sits opposite its replacement');
    assert.ok(firstChange.some(([l, r]) => l === 'none' && r === 'add'), 'an addition has a blank opposite side');
    await shot('diff-side-by-side');
    // collapsing a section leaves its header alone; the other stays
    const before = Number(await p().getAttribute(T('di-review-code'), 'data-rows'));
    await p().click(`${T('di-review-hunk')}[data-hunk="0"]`);
    const after = Number(await p().getAttribute(T('di-review-code'), 'data-rows'));
    assert.ok(after < before, `collapsed: ${before} -> ${after} rows`);
    assert.equal(await p().getAttribute(`${T('di-review-hunk')}[data-hunk="0"]`, 'aria-expanded'), 'false');
    await p().click(T('di-review-expand-all'));
    assert.equal(Number(await p().getAttribute(T('di-review-code'), 'data-rows')), before);
    await p().click(T('di-review-collapse-all'));
    assert.equal(await p().locator(T('di-review-line')).count(), 0);
    await p().click(T('di-review-expand-all'));
    // section navigation scrolls to the next header
    await p().click(T('di-review-next-section'));
    // text size
    const size = async () => p().$eval(T('di-review-code'), (el) => getComputedStyle(el).fontSize);
    assert.equal(await size(), '14px');
    await p().click(T('di-review-size-up'));
    assert.equal(await size(), '16px');
    assert.equal(await text(T('di-review-size')), '16px');
    await p().click(T('di-review-size-down'));
    await p().click(T('di-review-size-down'));
    assert.equal(await size(), '13px');
    // the choice is remembered for the viewer
    assert.equal(await p().evaluate(() => localStorage.getItem('di.review.textSize')), '2');
    assert.equal(await p().evaluate(() => localStorage.getItem('di.review.layout')), 'split');
  },
  STEPS_MS,
);

/** Everything the side-by-side acceptance is measured from, read out of the DOM in one go. */
const measureSplit = () =>
  p().evaluate(() => {
    const rect = (el: Element | null) => (el ? (el as HTMLElement).getBoundingClientRect().toJSON() : null);
    const q = (selector: string) => document.querySelector(selector) as HTMLElement | null;
    const dialog = q('[data-testid="di-review-dialog"]')!;
    const pane = { old: q('[data-testid="di-review-code-old"]'), new: q('[data-testid="di-review-code"]') };
    const head = { old: q('[data-testid="di-review-pane-head-old"]'), new: q('[data-testid="di-review-pane-head-new"]') };
    const lines = (side: 'old' | 'new') =>
      [...document.querySelectorAll(`[data-testid="di-review-line"][data-side="${side}"]`)] as HTMLElement[];
    const words = (side: 'old' | 'new') => lines(side).map((l) => l.innerText.replace(/\s+/g, ' ').trim());
    const scroll = (el: HTMLElement | null) =>
      el === null
        ? null
        : {
            scrollWidth: el.scrollWidth,
            clientWidth: el.clientWidth,
            scrollLeft: el.scrollLeft,
            scrollTop: el.scrollTop,
            horizontalBar: el.offsetHeight - el.clientHeight,
          };
    const firstText = (side: 'old' | 'new') => {
      const line = lines(side).find((l) => l.getAttribute('data-kind') !== 'none' && l.getAttribute('data-kind') !== 'note');
      return rect(line?.querySelector('.di-rv-text') ?? null);
    };
    const charWidth = (() => {
      const probe = document.createElement('span');
      probe.textContent = 'x'.repeat(100);
      probe.style.cssText = 'position:absolute;visibility:hidden;white-space:pre;font:inherit';
      pane.new?.appendChild(probe);
      const w = probe.getBoundingClientRect().width / 100;
      probe.remove();
      return w;
    })();
    return {
      viewport: { width: window.innerWidth, height: window.innerHeight },
      layout: pane.new?.getAttribute('data-layout') ?? null,
      dialog: rect(dialog),
      dialogOverflow: dialog.scrollWidth > dialog.clientWidth + 1,
      pageOverflow: document.documentElement.scrollWidth > document.documentElement.clientWidth,
      pane: { old: rect(pane.old), new: rect(pane.new) },
      head: { old: rect(head.old), new: rect(head.new) },
      headText: { old: head.old?.innerText ?? null, new: head.new?.innerText ?? null },
      hint: { old: q('[data-testid="di-review-pane-hint-old"]') !== null, new: q('[data-testid="di-review-pane-hint-new"]') !== null },
      scroll: { old: scroll(pane.old), new: scroll(pane.new) },
      words: { old: words('old'), new: words('new') },
      firstText: { old: firstText('old'), new: firstText('new') },
      charWidth,
      splitDisabled: (q('[data-testid="di-review-layout-split"]') as HTMLButtonElement | null)?.disabled ?? null,
      splitTitle: q('[data-testid="di-review-layout-split"]')?.getAttribute('title') ?? null,
      controls: ['di-review-layout-unified', 'di-review-layout-split', 'di-review-size-down', 'di-review-size-up'].map((id) => {
        const el = q(`[data-testid="${id}"]`);
        const r = el?.getBoundingClientRect();
        return { id, shown: !!r && r.width > 0 && r.height > 0, inside: !!r && r.left >= dialog.getBoundingClientRect().left - 1 && r.right <= dialog.getBoundingClientRect().right + 1 };
      }),
    };
  });

function assertBothSidesUsable(m: Awaited<ReturnType<typeof measureSplit>>, name: string, minPane: number) {
  assert.equal(m.layout, 'split', `${name}: side by side is showing`);
  const { dialog, pane, head } = m;
  assert.ok(dialog && pane.old && pane.new && head.old && head.new, `${name}: both panes and both headings exist`);
  // simultaneously inside the dialog, never pushed past its edge
  assert.ok(pane.old!.left >= dialog!.left - 1 && pane.new!.right <= dialog!.right + 1, `${name}: both panes are inside the dialog`);
  assert.ok(pane.old!.right <= pane.new!.left + 1, `${name}: the old side is left of the new side, not under it`);
  // balanced, and each wide enough to read
  assert.ok(Math.abs(pane.old!.width - pane.new!.width) <= 2, `${name}: unequal panes ${pane.old!.width} / ${pane.new!.width}`);
  assert.ok(pane.old!.width >= minPane && pane.new!.width >= minPane, `${name}: panes ${pane.old!.width} / ${pane.new!.width}px, want at least ${minPane}`);
  // each says what it is, over the pane it names
  assert.ok(Math.abs(head.old!.left - pane.old!.left) <= 1 && Math.abs(head.old!.width - pane.old!.width) <= 1, `${name}: the left heading is over the left pane`);
  assert.ok(Math.abs(head.new!.left - pane.new!.left) <= 1 && Math.abs(head.new!.width - pane.new!.width) <= 1, `${name}: the right heading is over the right pane`);
  assert.match(m.headText.old ?? '', /Base \(old\)[\s\S]*commit [0-9a-f]{10}/, `${name}: the left heading names the base commit`);
  assert.match(m.headText.new ?? '', /Working tree \(new\)[\s\S]*as observed \d\d:\d\d:\d\d/, `${name}: the right heading names the working tree`);
  assert.equal(m.pageOverflow, false, `${name}: the page does not scroll sideways`);
  assert.equal(m.dialogOverflow, false, `${name}: nothing is hidden past the dialog's edge`);
  // the first code of each side starts inside its own pane, on screen
  for (const side of ['old', 'new'] as const) {
    if ((m.scroll[side]?.scrollLeft ?? 0) > 0) continue; // a side the reader scrolled sideways starts off to the left, by design
    const t = m.firstText[side];
    const r = pane[side]!;
    assert.ok(t && t.left >= r.left - 1 && t.left < r.right - 40, `${name}: the ${side} side's code starts inside its pane`);
  }
  // no control is hidden
  for (const c of m.controls) assert.ok(c.shown && c.inside, `${name}: ${c.id} is shown inside the dialog`);
}

test(
  'side by side: both sides are readable at once, equally wide, and each says what it is',
  async () => {
    await openReview();
    await openFile('src/syntax_sample.ts');
    await p().click(T('di-review-layout-split'));
    await p().waitForSelector(T('di-review-code-old'));
    const cases: Array<[string, number, number, number]> = [
      ['desktop-1600x1000', 1600, 1000, 560],
      ['laptop-1280x720', 1280, 720, 440],
      ['narrowest-offered-1100x800', 1100, 800, 330],
    ];
    for (const [name, width, height, minPane] of cases) {
      await p().setViewportSize({ width, height });
      await p().waitForTimeout(500);
      const m = await measureSplit();
      assert.ok((m.dialog?.width ?? 0) >= 1040, `${name}: the dialog (${m.dialog?.width}px) is wide enough for two columns`);
      assertBothSidesUsable(m, name, minPane);
      // MEANINGFUL code on BOTH sides: the shared context, the removed line against the line that replaced it
      for (const side of ['old', 'new'] as const) {
        assert.ok(m.words[side].some((l) => /export interface Config/.test(l)), `${name}: the ${side} side shows the shared code`);
      }
      assert.ok(m.words.old.some((l) => /return JSON\.parse\(text\) as Config;/.test(l)), `${name}: the old side shows the removed line`);
      assert.ok(m.words.new.some((l) => /const config = JSON\.parse\(text\) as Config;/.test(l)), `${name}: the new side shows its replacement`);
      assert.ok(m.words.new.some((l) => /timeoutSeconds: number;/.test(l)), `${name}: the new side shows what was added`);
      await shot(`side-by-side-${name}`);
    }
    // full screen: still two readable sides, now at the window's width
    await p().setViewportSize({ width: 1280, height: 720 });
    await p().waitForTimeout(400);
    const before = await measureSplit();
    await p().click(T('di-review-fullscreen'));
    await p().waitForTimeout(500);
    const full = await measureSplit();
    assert.equal(Math.round(full.dialog!.width), 1280, 'full screen is the window wide');
    assertBothSidesUsable(full, 'fullscreen-1280x720', 470);
    assert.ok(full.pane.new!.width > before.pane.new!.width, 'full screen gives the panes more room');
    await shot('side-by-side-fullscreen-1280x720');
    await p().click(T('di-review-fullscreen'));
    await p().waitForTimeout(400);
    assert.equal(Math.round((await measureSplit()).pane.new!.width), Math.round(before.pane.new!.width), 'and gives it back');
  },
  STEPS_MS * 3,
);

test(
  'side by side: a long line scrolls on its own side, within a bound, and neither side is pushed away',
  async () => {
    await openReview();
    await openFile('web/long_line.js');
    await p().click(T('di-review-layout-split'));
    await p().waitForSelector(T('di-review-code-old'));
    for (const [name, width, height, minPane] of [
      ['desktop-1600x1000', 1600, 1000, 560],
      ['laptop-1280x720', 1280, 720, 440],
    ] as Array<[string, number, number, number]>) {
      await p().setViewportSize({ width, height });
      await p().waitForTimeout(500);
      const m = await measureSplit();
      assertBothSidesUsable(m, name, minPane);
      // the NEW side holds the very long line; the old side's short line fits
      const oldS = m.scroll.old!;
      const newS = m.scroll.new!;
      assert.ok(newS.scrollWidth > newS.clientWidth + 100, `${name}: the long side needs the sideways scroll`);
      assert.ok(oldS.scrollWidth <= oldS.clientWidth + 1, `${name}: the short side does not`);
      assert.equal(m.hint.new, true, `${name}: the long side says to scroll it sideways`);
      assert.equal(m.hint.old, false, `${name}: the short side does not`);
      assert.ok(newS.horizontalBar >= 6, `${name}: its sideways scrollbar is drawn (${newS.horizontalBar}px), not hidden until used`);
      // bounded: the scrollable distance is the clipped line (1,200 characters) plus its tag, no more
      const reach = (newS.scrollWidth - newS.clientWidth) / m.charWidth;
      assert.ok(reach < 1200 + 80, `${name}: the sideways reach is bounded (${Math.round(reach)} characters)`);
      // moving the long side leaves the other where it was, and keeps both inside the dialog
      await p().evaluate(() => {
        (document.querySelector('[data-testid="di-review-code"]') as HTMLElement).scrollLeft = 600;
      });
      await p().waitForTimeout(150);
      const after = await measureSplit();
      assert.equal(after.scroll.new!.scrollLeft, 600, `${name}: the long side scrolled`);
      assert.equal(after.scroll.old!.scrollLeft, 0, `${name}: the other side stayed`);
      assertBothSidesUsable(after, `${name} scrolled`, minPane);
      // scrolled 600px sideways, the line number and the + mark are still at the pane's left edge
      const pinned = await p().evaluate(() => {
        const paneEl = document.querySelector('[data-testid="di-review-code"]') as HTMLElement;
        const row = paneEl.querySelector('[data-testid="di-review-line"][data-kind="add"]') as HTMLElement;
        const gutter = row.querySelector('.di-rv-gutter')!.getBoundingClientRect();
        const mark = row.querySelector('.di-rv-mark')!.getBoundingClientRect();
        const box = paneEl.getBoundingClientRect();
        const text = (row.querySelector('.di-rv-text') as HTMLElement).getBoundingClientRect();
        return { gutterLeft: gutter.left - box.left, markLeft: mark.left - gutter.right, textLeft: text.left - box.left };
      });
      assert.ok(Math.abs(pinned.gutterLeft) <= 1, `${name}: the line number is pinned (${pinned.gutterLeft}px from the edge)`);
      assert.ok(Math.abs(pinned.markLeft) <= 1, `${name}: the mark sits beside it (${pinned.markLeft}px)`);
      assert.ok(pinned.textLeft < 0, `${name}: while the line itself has scrolled away (${pinned.textLeft}px)`);
      await shot(`side-by-side-long-line-${name}`);
      await p().evaluate(() => {
        (document.querySelector('[data-testid="di-review-code"]') as HTMLElement).scrollLeft = 0;
      });
    }
    // by keyboard: the pane takes focus and the arrow keys move it (and only it) sideways
    await p().focus(T('di-review-code'));
    for (let i = 0; i < 6; i += 1) await p().keyboard.press('ArrowRight');
    await p().waitForTimeout(200);
    const keyed = await measureSplit();
    assert.ok(keyed.scroll.new!.scrollLeft > 0, 'ArrowRight scrolls the focused long side');
    assert.equal(keyed.scroll.old!.scrollLeft, 0, 'and not the other');
    // another file starts at the left on both sides, not where the last one was left. (Both files must be
    // WIDER than their panes, or the browser clamps the position by itself and nothing is shown: the
    // sample's one long comment line is wider than a pane at this width, the long line far wider.)
    await openFile('src/syntax_sample.ts');
    await p().waitForTimeout(300);
    await p().evaluate(() => {
      for (const id of ['di-review-code', 'di-review-code-old']) {
        (document.querySelector(`[data-testid="${id}"]`) as HTMLElement).scrollLeft = 40;
      }
    });
    const moved = await measureSplit();
    assert.ok(moved.scroll.new!.scrollLeft > 0 && moved.scroll.old!.scrollLeft > 0, 'both sides of the sample were scrolled sideways');
    await openFile('web/long_line.js');
    await p().waitForTimeout(300);
    const next = await measureSplit();
    assert.equal(next.scroll.new!.scrollLeft, 0, 'the next file starts at the left of the new side');
    assert.equal(next.scroll.old!.scrollLeft, 0, 'and of the old side');
    assert.equal(next.scroll.new!.scrollTop, 0, 'and at the top');
  },
  STEPS_MS * 3,
);

test(
  'side by side: the two sides move together vertically, row for row',
  async () => {
    await openReview();
    await openFile('data/large.txt');
    await p().click(T('di-review-layout-split'));
    await p().waitForSelector(T('di-review-code-old'));
    for (const top of [1500, 22000, 90000]) {
      await p().evaluate((t) => {
        (document.querySelector('[data-testid="di-review-code"]') as HTMLElement).scrollTop = t;
      }, top);
      await p().waitForFunction(
        (t) => {
          const o = document.querySelector('[data-testid="di-review-code-old"]') as HTMLElement;
          const n = document.querySelector('[data-testid="di-review-code"]') as HTMLElement;
          return Math.abs(o.scrollTop - n.scrollTop) < 1 && n.scrollTop >= Math.min(t, n.scrollHeight - n.clientHeight) - 1;
        },
        top,
        { timeout: 10_000 },
      );
      // the same rows are drawn opposite each other (a deletion against the addition that replaced it)
      await p().waitForTimeout(150);
      const aligned = await p().evaluate(() => {
        const tops = (side: string) =>
          [...document.querySelectorAll(`[data-testid="di-review-line"][data-side="${side}"]`)].map((e) => Math.round(e.getBoundingClientRect().top));
        const o = tops('old');
        const n = tops('new');
        return { same: o.length === n.length && o.every((v, i) => v === n[i]), count: o.length };
      });
      assert.ok(aligned.same && aligned.count > 10, `scrollTop ${top}: the drawn rows line up across the two sides (${aligned.count} rows)`);
    }
    // the OTHER side can be the one that is scrolled
    await p().evaluate(() => {
      (document.querySelector('[data-testid="di-review-code-old"]') as HTMLElement).scrollTop = 600;
    });
    await p().waitForFunction(() => {
      const n = document.querySelector('[data-testid="di-review-code"]') as HTMLElement;
      return Math.abs(n.scrollTop - 600) < 1;
    });
  },
  STEPS_MS * 2,
);

/**
 * The explanation of why a window shows one column, read out of the DOM as a READER meets it: real text in the
 * flow of the dialog, painted (not clipped away or overlapped), at a readable size and contrast — never merely a
 * `title` attribute, which a keyboard or touch reader is never shown.
 */
const readSplitNote = () =>
  p().evaluate(() => {
    const el = document.querySelector('[data-testid="di-review-split-unavailable"]') as HTMLElement | null;
    if (el === null) return null;
    const rect = el.getBoundingClientRect();
    const dialog = document.querySelector('[data-testid="di-review-dialog"]') as HTMLElement;
    const box = dialog.getBoundingClientRect();
    const style = getComputedStyle(el);
    const channels = (css: string): number[] => (css.match(/[\d.]+/g) ?? []).slice(0, 3).map(Number);
    const luminance = ([r, g, b]: number[]): number => {
      const lin = (v: number) => {
        const s = v / 255;
        return s <= 0.03928 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4;
      };
      return 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b);
    };
    let node: HTMLElement | null = el;
    let background = ''; // none found: the contrast below is then NaN and the assertion fails
    while (node !== null) {
      const bg = getComputedStyle(node).backgroundColor;
      if (!/rgba\(\s*0,\s*0,\s*0,\s*0\s*\)|transparent/.test(bg)) {
        background = bg;
        break;
      }
      node = node.parentElement;
    }
    const a = luminance(channels(style.color));
    const b = luminance(channels(background));
    const hit = document.elementFromPoint(rect.left + rect.width / 2, rect.top + rect.height / 2);
    return {
      text: el.innerText.replace(/\s+/g, ' ').trim(),
      dialogText: dialog.innerText.replace(/\s+/g, ' '),
      width: rect.width,
      height: rect.height,
      display: style.display,
      visibility: style.visibility,
      opacity: style.opacity,
      fontSize: parseFloat(style.fontSize),
      contrast: (Math.max(a, b) + 0.05) / (Math.min(a, b) + 0.05),
      hiddenFromReaders: el.closest('[aria-hidden="true"], [hidden], .di-rv-visually-hidden') !== null,
      inDialog: rect.left >= box.left - 1 && rect.right <= box.right + 1 && rect.top >= box.top - 1 && rect.bottom <= box.bottom + 1,
      paintedOnTop: hit !== null && (el === hit || el.contains(hit)),
      title: el.getAttribute('title'),
    };
  });

function assertSplitNoteVisible(note: Awaited<ReturnType<typeof readSplitNote>>, pattern: RegExp, name: string) {
  assert.ok(note !== null, `${name}: the explanation is in the page`);
  const n = note!;
  assert.match(n.text, pattern, `${name}: it says why`);
  assert.ok(n.width > 100 && n.height >= 12, `${name}: it takes room on the page (${n.width}x${n.height}px)`);
  assert.notEqual(n.display, 'none', `${name}: displayed`);
  assert.equal(n.visibility, 'visible', `${name}: visible`);
  assert.equal(n.opacity, '1', `${name}: opaque`);
  assert.equal(n.hiddenFromReaders, false, `${name}: not aria-hidden, hidden or visually-hidden-only`);
  assert.ok(n.inDialog, `${name}: inside the dialog, not clipped off its edge`);
  assert.ok(n.paintedOnTop, `${name}: nothing paints over it`);
  assert.ok(n.fontSize >= 12, `${name}: readable size (${n.fontSize}px)`);
  assert.ok(n.contrast >= 4.5, `${name}: readable contrast as rendered (${n.contrast.toFixed(2)}:1)`);
  assert.match(n.dialogText, pattern, `${name}: it is part of what the dialog shows (its visible text), not a tooltip`);
  assert.equal(n.title, null, `${name}: it does not depend on a title attribute`);
}

test(
  'side by side: below the width that can hold two readable columns the dialog SAYS SO IN VISIBLE TEXT, shows one, and comes back when widened',
  async () => {
    await openReview();
    await openFile('src/syntax_sample.ts');
    await p().click(T('di-review-layout-split'));
    await p().waitForSelector(T('di-review-code-old'));
    await p().setViewportSize({ width: 1024, height: 768 });
    await p().waitForTimeout(500);
    const narrow = await measureSplit();
    assert.ok((narrow.dialog?.width ?? 9999) < 1040, `1024: the dialog (${narrow.dialog?.width}px) is below the two-column width`);
    assert.equal(narrow.splitDisabled, true, '1024: two columns are disabled, not silently dropped');
    assert.match(narrow.splitTitle ?? '', /Too narrow for two columns/);
    // the reason is TEXT in the dialog, not only the tooltip above
    assertSplitNoteVisible(await readSplitNote(), /Side by side needs a dialog at least 1,040 px wide: widen the window or use full screen\./, '1024');
    assert.equal(narrow.layout, 'unified', '1024: one column is shown');
    assert.equal(narrow.head.old, null, '1024: no per-side headings without two sides');
    assert.equal(narrow.pageOverflow, false);
    assert.equal(narrow.dialogOverflow, false);
    for (const c of narrow.controls) assert.ok(c.shown && c.inside, `1024: ${c.id} is shown inside the dialog`);
    assert.equal(await p().evaluate(() => localStorage.getItem('di.review.layout')), 'split', 'the reader\u2019s choice is kept');
    await shot('side-by-side-1024-one-column');
    // a phone-width window has no layout choice and no full-screen button: it says what it shows, and does not
    // send the reader to a control that is not there
    await p().setViewportSize({ width: 390, height: 844 });
    await p().waitForTimeout(500);
    await p().click(T('di-review-tab-diff'));
    await p().waitForSelector(T('di-review-code'));
    const phone = await readSplitNote();
    assertSplitNoteVisible(phone, /One column at this width: side by side needs a wider window\./, '390');
    assert.doesNotMatch(phone!.text, /full screen/i, '390: it does not mention a control the window does not have');
    assert.equal(await p().evaluate(() => document.querySelector('[data-testid="di-review-layout-split"]') !== null), false, '390: and there is no toggle to explain');
    await shot('side-by-side-390-one-column');
    // widened, there is nothing left to explain, so nothing stale is shown
    await p().setViewportSize({ width: 1280, height: 720 });
    await p().waitForTimeout(500);
    assertBothSidesUsable(await measureSplit(), 'widened-1280x720', 440);
    assert.equal(await readSplitNote(), null, '1280: no explanation once two columns are available');
  },
  STEPS_MS * 2,
);

test(
  'an empty listing is not presented as a clean tree: it says what it cannot see, as visible text',
  async () => {
    // a labelled SYNTHETIC variation: the office relays an overview that lists nothing
    replyHook = (request, reply) =>
      request.fileId !== undefined || reply.overview === null
        ? reply
        : {
            ...reply,
            overview: { files: [], totals: { files: 0, additions: 0, deletions: 0, withheld: 0, unshown: 0 } },
          };
    await selectAgent();
    await p().waitForSelector(T('di-open-code-review'));
    await p().click(T('di-open-code-review'));
    await p().waitForSelector(T('di-review-dialog'));
    await p().waitForSelector(T('di-review-empty'), { timeout: 20_000 });
    const box = await p().$eval(T('di-review-empty'), (el) => {
      const r = el.getBoundingClientRect();
      const s = getComputedStyle(el);
      return {
        text: (el as HTMLElement).innerText.replace(/\s+/g, ' '),
        width: r.width,
        height: r.height,
        visible: s.visibility === 'visible' && s.display !== 'none' && s.opacity !== '0',
      };
    });
    assert.ok(box.visible && box.width > 100 && box.height > 20, 'the empty-state notice is on the page and visible');
    // it does not claim the tree matches: it says what the listing could not see
    assert.match(box.text, /as far as this view can tell/, 'a finding, not a verdict');
    assert.match(box.text, /not proof the working tree matches/i);
    assert.match(box.text, /edit of the same size within that second may not be seen/);
    assert.doesNotMatch(box.text, /The working tree matches commit/, 'the unqualified clean claim is gone');
    // and the header count beside it is a finding too, not "0 files changed"
    const totals = await text(T('di-review-totals'));
    assert.match(totals, /no changes found/);
    assert.doesNotMatch(totals, /0 files changed/);
    await shot('state-empty-qualified');
  },
  STEPS_MS,
);

test(
  'by keyboard alone: arrows through the tree, Enter opens, [ and ] move between files, Tab stays in the dialog, Escape closes and focus returns',
  async () => {
    await openReview();
    // the page behind the dialog is inert: it is not just covered, it cannot be reached
    assert.equal(await p().evaluate(() => document.getElementById('root')?.hasAttribute('inert')), true, 'the page behind is inert while the dialog is open');
    const behind = await p().evaluate(() => {
      const control = document.querySelector('#root button') as HTMLElement | null;
      control?.focus();
      return { found: control !== null, focused: control !== null && document.activeElement === control };
    });
    assert.deepEqual(behind, { found: true, focused: false }, 'a control behind the dialog cannot take focus');
    // and no key typed in the dialog reaches the office: a listener on the window counts them
    await p().evaluate(() => {
      const seen: string[] = [];
      (window as unknown as { __seenKeys: string[] }).__seenKeys = seen;
      window.addEventListener('keydown', (e) => seen.push(e.key));
    });
    await p().focus(`${T('di-review-tree')} [tabindex="0"]`);
    await p().keyboard.press('End');
    const last = await p().evaluate(() => document.activeElement?.getAttribute('data-path'));
    await p().keyboard.press('Home');
    const first = await p().evaluate(() => document.activeElement?.getAttribute('data-path'));
    assert.ok(first && last && first !== last, `Home and End move through the tree (${first} .. ${last})`);
    await p().keyboard.press('ArrowDown');
    const second = await p().evaluate(() => document.activeElement?.getAttribute('data-path'));
    assert.notEqual(second, first, 'ArrowDown moves to the next row');
    // Enter opens the focused file
    await p().focus(rowFor('src/util.py'));
    await p().keyboard.press('Enter');
    await p().waitForFunction(() => document.querySelector('[data-testid="di-review-path"]')?.textContent?.startsWith('src/util.py'));
    // ] and [ move to the next and previous file in tree order
    await p().keyboard.press(']');
    await p().waitForFunction(() => !document.querySelector('[data-testid="di-review-path"]')?.textContent?.startsWith('src/util.py'));
    await p().keyboard.press('[');
    await p().waitForFunction(() => document.querySelector('[data-testid="di-review-path"]')?.textContent?.startsWith('src/util.py'));
    // Tab never leaves the dialog, forward or back
    assert.ok(await focusInsideDialog(), `before Tab, focus is on ${await activeInfo()}`);
    for (let i = 0; i < 40; i += 1) {
      const from = await activeInfo();
      await p().keyboard.press('Tab');
      assert.ok(await focusInsideDialog(), `Tab ${i} left the dialog from ${from} for ${await activeInfo()}`);
    }
    for (let i = 0; i < 40; i += 1) {
      const from = await activeInfo();
      await p().keyboard.press('Shift+Tab');
      assert.ok(await focusInsideDialog(), `Shift+Tab ${i} left the dialog from ${from} for ${await activeInfo()}`);
    }
    // keys the office uses are not seen by it: nothing typed above (arrows, Home/End, Enter, [ ], Tab,
    // Shift+Tab) reached the window, and the selection is unchanged after Escape too
    const seenInside = await p().evaluate(() => (window as unknown as { __seenKeys: string[] }).__seenKeys.slice());
    assert.deepEqual(seenInside, [], 'no key typed in the dialog reached the office');
    const selectedBefore = await selection();
    await p().keyboard.press('Escape');
    await p().waitForSelector(T('di-review-dialog'), { state: 'detached' });
    assert.deepEqual(
      await p().evaluate(() => (window as unknown as { __seenKeys: string[] }).__seenKeys.slice()),
      [],
      'not even the Escape that closed it',
    );
    // control: the same listener does see a key once the dialog is gone
    await p().keyboard.press('Shift');
    assert.deepEqual(await p().evaluate(() => (window as unknown as { __seenKeys: string[] }).__seenKeys.slice()), ['Shift']);
    assert.equal(await selection(), selectedBefore, 'closing did not touch the office selection');
    const back = await p().evaluate(() => document.activeElement?.getAttribute('data-testid'));
    assert.equal(back, 'di-open-code-review', 'focus returned to the entry that opened it');
    assert.equal(await p().evaluate(() => document.getElementById('root')?.hasAttribute('inert')), false);
  },
  STEPS_MS,
);

test(
  'focus and keys stay inside the dialog across a read: Refresh, used from the keyboard, disables itself while it holds focus',
  async () => {
    await openReview();
    replyDelayMs = 400; // the office answers a moment later, over the real socket
    await watchContainment();
    await p().focus(T('di-review-refresh'));
    assert.equal(await p().evaluate(() => (document.activeElement as HTMLElement | null)?.dataset.testid), 'di-review-refresh');
    await p().keyboard.press('Enter');
    // the control is disabled for as long as the read is in flight, while it holds focus
    await p().waitForFunction(
      () => (document.querySelector('[data-testid="di-review-refresh"]') as HTMLButtonElement | null)?.disabled === true,
    );
    await frames(3);
    const whilePending = [await focusInsideDialog()];
    // keys typed while the read is in flight: the dialog handles them, the office sees nothing
    await p().keyboard.press('x');
    await p().keyboard.press('Tab');
    await p().keyboard.press('Shift+Tab');
    whilePending.push(await focusInsideDialog());
    await p().waitForFunction(
      () => (document.querySelector('[data-testid="di-review-refresh"]') as HTMLButtonElement | null)?.disabled === false,
    );
    await frames(2);
    assert.deepEqual(
      await containmentSummary(whilePending),
      {
        focusInsideDialogWhileTheControlWasDisabled: [true, true],
        framesWithFocusOutsideTheDialog: [],
        keysDispatchedOutsideTheDialog: [],
        keysThatReachedTheOffice: [],
      },
      'from Enter to the answer: focus never left the dialog and no key (Enter, x, Tab, Shift+Tab) reached the office',
    );
  },
  STEPS_MS,
);

test(
  'focus and keys stay inside the dialog when the answer for a file no longer selected settles and the session asks again',
  async () => {
    await openReview();
    await openFile('src/util.py');
    replyDelayMs = 300;
    await watchContainment();
    // The ONE thing made deterministic: the reader's Tab lands on Refresh at the instant the settled stale answer enables it.
    // The stale answer, the enabling, the session's next read and the disabling that follows are all the real path.
    await p().evaluate(() => {
      const w = window as unknown as { __landed: boolean };
      w.__landed = false;
      let sawDisabled = false;
      const observer = new MutationObserver(() => {
        const refresh = document.querySelector('[data-testid="di-review-refresh"]') as HTMLButtonElement | null;
        if (refresh === null) return;
        if (refresh.disabled) sawDisabled = true;
        else if (sawDisabled) {
          refresh.focus();
          w.__landed = true;
          observer.disconnect();
        }
      });
      observer.observe(document.body, { attributes: true, attributeFilter: ['disabled'], subtree: true });
    });
    await p().focus(rowFor('src/util.py'));
    await p().keyboard.press(']');
    await p().keyboard.press('[');
    await p().waitForFunction(() => (window as unknown as { __landed: boolean }).__landed === true, undefined, { timeout: 20_000 });
    // the session now asks for the file that IS selected: Refresh disables again, while it holds focus
    await p().waitForFunction(
      () => (document.querySelector('[data-testid="di-review-refresh"]') as HTMLButtonElement | null)?.disabled === true,
    );
    await frames(3);
    const whilePending = [await focusInsideDialog()];
    await p().keyboard.press('x');
    await p().keyboard.press('Tab');
    whilePending.push(await focusInsideDialog());
    await p().waitForFunction(
      () => (document.querySelector('[data-testid="di-review-refresh"]') as HTMLButtonElement | null)?.disabled === false,
      undefined,
      { timeout: 20_000 },
    );
    await frames(2);
    assert.deepEqual(
      await containmentSummary(whilePending),
      {
        focusInsideDialogWhileTheControlWasDisabled: [true, true],
        framesWithFocusOutsideTheDialog: [],
        keysDispatchedOutsideTheDialog: [],
        keysThatReachedTheOffice: [],
      },
      'across the stale answer, the session asking again and the answer: focus never left the dialog and no key reached the office',
    );
  },
  STEPS_MS,
);

test(
  'focus and keys stay inside the dialog when the control in use reaches its limit: "Smaller text" disables itself under the keyboard',
  async () => {
    await openReview();
    await openFile('src/util.py'); // the size controls belong to the file's header
    await watchContainment();
    await p().focus(T('di-review-size-down'));
    for (let i = 0; i < 20; i += 1) {
      if (await p().$eval(T('di-review-size-down'), (el) => (el as HTMLButtonElement).disabled)) break;
      await p().keyboard.press('Enter');
    }
    assert.equal(await p().$eval(T('di-review-size-down'), (el) => (el as HTMLButtonElement).disabled), true, 'the smallest size was reached');
    await frames(3);
    const whilePending = [await focusInsideDialog()];
    await p().keyboard.press('x');
    await p().keyboard.press('Tab');
    whilePending.push(await focusInsideDialog());
    assert.deepEqual(
      await containmentSummary(whilePending),
      {
        focusInsideDialogWhileTheControlWasDisabled: [true, true],
        framesWithFocusOutsideTheDialog: [],
        keysDispatchedOutsideTheDialog: [],
        keysThatReachedTheOffice: [],
      },
      'once the control disabled itself: focus never left the dialog and no key reached the office',
    );
  },
  STEPS_MS,
);

test(
  'search filters the files and opens the folders that hold a match; Escape clears it before it closes anything',
  async () => {
    await openReview();
    await p().fill(T('di-review-search'), 'LONG');
    assert.equal(await p().locator(T('di-review-row')).count(), 1);
    assert.match(await text(T('di-review-count')), /1 of 23 match/);
    await p().fill(T('di-review-search'), 'zzz-nothing');
    await p().waitForSelector(T('di-review-no-match'));
    await p().fill(T('di-review-search'), 'src');
    assert.ok((await p().locator(T('di-review-row')).count()) >= 4);
    await p().keyboard.press('Escape');
    assert.equal(await p().inputValue(T('di-review-search')), '', 'the first Escape clears the filter');
    assert.equal(await exists(T('di-review-dialog')), true, 'and does not close the dialog');
    // a folder collapses and expands from the keyboard
    await p().focus(`${T('di-review-folder')}[data-path="src"]`);
    await p().keyboard.press('ArrowLeft');
    assert.equal(await p().locator(rowFor('src/app.py')).count(), 0);
    await p().keyboard.press('ArrowRight');
    assert.equal(await p().locator(rowFor('src/app.py')).count(), 1);
  },
  STEPS_MS,
);

test(
  'full screen fills the window and returns; the dialog never overflows the page',
  async () => {
    await openReview();
    const box = async () => p().$eval(T('di-review-dialog'), (el) => el.getBoundingClientRect().toJSON());
    const normal = await box();
    assert.ok(normal.width < VIEW.width && normal.height < VIEW.height, 'a large dialog, not the whole window');
    assert.ok(normal.width >= VIEW.width * 0.85, `spacious: ${normal.width}px of ${VIEW.width}px`);
    await shot('modal-large');
    await p().click(T('di-review-fullscreen'));
    const full = await box();
    assert.equal(Math.round(full.width), VIEW.width);
    assert.equal(Math.round(full.height), VIEW.height);
    assert.equal(await p().getAttribute(T('di-review-fullscreen'), 'aria-pressed'), 'true');
    await shot('modal-fullscreen');
    await p().click(T('di-review-fullscreen'));
    assert.equal(Math.round((await box()).width), Math.round(normal.width));
  },
  STEPS_MS,
);

test(
  'every file state says what it is and shows no content it must not',
  async () => {
    await openReview();
    const cases: Array<[string, RegExp]> = [
      ['bin/blob.bin', /Binary file/],
      ['empty.txt', /Empty file/],
      ['secrets/server.pem', /Withheld[\s\S]*never read or shown[\s\S]*an edit of the same size within that second may not be seen/],
      ['.env', /Withheld[\s\S]*private location/],
      ['generated/bundle.min.js', /Too large to read here/],
      ['legacy/latin1.txt', /Not valid UTF-8 text/],
      ['vendor/clone', /Not shown[\s\S]*separate git repository[\s\S]*not read/],
    ];
    for (const [file, pattern] of cases) {
      const before = requests.length;
      await p().click(rowFor(file));
      await p().waitForSelector(`${T('di-review-notice')}`);
      const body = await p().$eval(T('di-review-dialog'), (el) => (el as HTMLElement).innerText);
      assert.match(body, pattern, file);
      assert.equal(requests.length, before, `${file}: nothing is read for a file that carries no text`);
      assert.equal(await exists(T('di-review-code')), false, `${file}: no diff area`);
    }
    // a withheld file that is NEW has no recorded metadata to be judged from, so it does not say so
    await p().click(rowFor('.env'));
    const untrackedBody = await p().$eval(T('di-review-dialog'), (el) => (el as HTMLElement).innerText);
    assert.match(untrackedBody, /Withheld[\s\S]*private location/);
    assert.doesNotMatch(untrackedBody, /same size within that second/);
    // the content of the withheld files appears nowhere in the page
    const page_ = await p().content();
    assert.doesNotMatch(page_, /S3CR3T-KEY-MATERIAL|hunter2/);
    await p().click(rowFor('secrets/server.pem'));
    await shot('state-withheld');
    // a submodule is shown by the commit it points to — never by what is inside it
    await openFile('vendor/lib');
    const pointer = await p().$eval(T('di-review-dialog'), (el) => (el as HTMLElement).innerText);
    assert.match(pointer, /Submodule[\s\S]*only the pointer is compared/i);
    assert.match(pointer, /What is inside the submodule is not read/);
    assert.doesNotMatch(pointer, /dirty\.txt|uncommitted inside the submodule/);
    const pointerLines = await p().$$eval(T('di-review-line'), (els) =>
      els.map((e) => [e.getAttribute('data-kind'), (e as HTMLElement).innerText.replace(/\s+/g, ' ').trim()]),
    );
    assert.deepEqual(
      pointerLines.map(([kind]) => kind),
      ['del', 'add'],
    );
    for (const [, line] of pointerLines) assert.match(line as string, /Subproject commit [0-9a-f]{40}$/);
    assert.notEqual(pointerLines[0][1], pointerLines[1][1], 'the old and the new commit differ');
    await shot('state-submodule');
    // a rename shows both names
    await openFile('src/moved.py');
    assert.match(await text(T('di-review-path')), /src\/moved\.py[\s\S]*renamed from src\/mover\.py/);
    // a deletion shows what was removed
    await openFile('docs/gone.md');
    const kinds = await p().$$eval(T('di-review-line'), (els) => els.map((e) => e.getAttribute('data-kind')));
    assert.deepEqual(kinds, ['del']);
  },
  STEPS_MS,
);

test(
  'a truncated diff and a clipped line say exactly how much is not shown, and stay navigable',
  async () => {
    await openReview();
    await openFile('data/large.txt');
    const banner = await text(T('di-review-truncated'));
    assert.match(banner, /first 6,000 of 7,100/);
    assert.match(banner, /whole diff/);
    // one section header and the 6,000 retained lines: nothing more, nothing less
    assert.equal(Number(await p().getAttribute(T('di-review-code'), 'data-rows')), 6001);
    // the window renders a bounded number of rows however large the diff
    const rendered = await p().locator(T('di-review-line')).count();
    assert.ok(rendered > 20 && rendered < 200, `windowed: ${rendered} rows in the DOM`);
    // scroll to the end: the last retained row appears, still bounded
    await p().$eval(T('di-review-code'), (el) => (el.scrollTop = el.scrollHeight));
    // the window follows the scroll on the next frame: wait for the end of the diff to be drawn
    // (100 deleted lines come first, so the 6,000th retained line is the 5,900th added: row 5899)
    await p().waitForFunction(() => {
      const rows = document.querySelectorAll('[data-testid="di-review-line"]');
      return (rows[rows.length - 1] as HTMLElement | undefined)?.innerText.includes('row 5899 changed') === true;
    });
    assert.ok((await p().locator(T('di-review-line')).count()) < 200);
    await shot('diff-truncated');
    // a very long line is clipped, says by how many characters, and scrolls INSIDE the pane
    await openFile('web/long_line.js');
    const clip = await text(T('di-review-clipped'));
    assert.match(clip, /3,819 more characters not shown/);
    const overflow = await p().evaluate(() => ({
      page: document.documentElement.scrollWidth <= document.documentElement.clientWidth,
      pane: (() => {
        const el = document.querySelector('[data-testid="di-review-code"]') as HTMLElement;
        return el.scrollWidth > el.clientWidth;
      })(),
    }));
    assert.ok(overflow.page, 'the page itself never scrolls sideways');
    assert.ok(overflow.pane, 'the long line scrolls inside the code pane');
  },
  STEPS_MS,
);

test(
  'patch text is inert: markup is text, nothing runs or is fetched, and hidden characters are visible',
  async () => {
    replyHook = (request, reply) => {
      const file = reply.file;
      if (!file || request.fileId !== doc.replies.files['src/util.py'].fileId) return reply;
      const lines = file.hunks[0].lines;
      lines[1].text = 'x = "<img src=https://example.invalid/p.png onerror=alert(1)><script>alert(2)</script>"';
      lines[2].text = 'if is_admin‮) {  # trojan​\u0000';
      lines[3].text = '<a href="javascript:alert(3)">click</a>';
      return reply;
    };
    await openReview();
    await openFile('src/util.py');
    await p().waitForSelector(T('di-review-line'));
    const dom = await p().$eval(T('di-review-code'), (el) => ({
      images: el.querySelectorAll('img').length,
      scripts: el.querySelectorAll('script').length,
      anchors: el.querySelectorAll('a').length,
      text: (el as HTMLElement).innerText,
    }));
    assert.equal(dom.images + dom.scripts + dom.anchors, 0, 'no element was created from patch text');
    assert.match(dom.text, /<script>alert\(2\)<\/script>/);
    assert.match(dom.text, /<a href="javascript:alert\(3\)">click<\/a>/);
    const controls = await p().$$eval(T('di-review-control'), (els) => els.map((e) => (e as HTMLElement).innerText));
    assert.ok(controls.includes('⟦U+202E⟧'), `bidirectional override shown: ${controls.join()}`);
    assert.ok(controls.includes('⟦U+200B⟧') && controls.includes('⟦U+0000⟧'));
    const title = await p().$eval(`${T('di-review-control')}`, (el) => el.getAttribute('title'));
    assert.match(title ?? '', /U\+/);
    await p().waitForTimeout(500);
    assert.equal(dialogs, 0, 'no alert/confirm/prompt fired');
    assert.deepEqual(outsideRequests, [], 'nothing was fetched from outside the page');
    await shot('state-hostile-text');
  },
  STEPS_MS,
);

test(
  'while the office is read the dialog says so; a refusal is shown in words with its code and a way forward',
  async () => {
    replyDelayMs = 1500;
    await selectAgent();
    await p().click(T('di-open-code-review'));
    await p().waitForSelector(T('di-review-loading'));
    assert.match(await text(T('di-review-loading')), /Reading the workspace/);
    await shot('state-loading');
    await p().waitForSelector(T('di-review-tree'), { timeout: 20_000 });
    replyDelayMs = 0;
    replyHook = (request, reply) => {
      if (request.fileId === undefined) return reply;
      return {
        ...reply,
        state: 'refused',
        decidedBy: 'backend',
        problem: 'git_read_deadline',
        detail: 'x',
        target: null,
        comparison: null,
        file: null,
        overview: null,
      };
    };
    await p().click(rowFor('src/app.py'));
    await p().waitForSelector(`${T('di-review-notice')}[data-state="refused"]`);
    const words = await text(`${T('di-review-notice')}[data-state="refused"]`);
    assert.match(words, /git took too long/);
    assert.match(words, /code: git_read_deadline/);
    assert.equal(await exists(T('di-review-retry')), true);
    await shot('state-refused');
  },
  STEPS_MS,
);

test(
  'a moved workspace is labelled out of date, the old state stays visible and dimmed, and Refresh reads again',
  async () => {
    await openReview();
    replyHook = (request, reply) =>
      request.fileId === undefined
        ? reply
        : {
            ...reply,
            snapshot: String(request.snapshot),
            state: 'refused',
            decidedBy: 'backend',
            problem: 'snapshot_moved',
            detail: 'moved',
            target: null,
            comparison: null,
            file: null,
            overview: null,
          };
    await p().click(rowFor('src/app.py'));
    await p().waitForSelector(T('di-review-stale'));
    assert.match(await text(T('di-review-stale')), /Out of date\./);
    assert.match(await text(T('di-review-stale')), /old state, not the current one/);
    assert.equal(await p().locator(T('di-review-row')).count(), 23, 'the old overview stays, labelled');
    assert.equal(await p().getAttribute('.di-rv-tree-pane', 'data-stale'), 'true');
    await shot('state-stale');
    replyHook = null;
    const before = requests.length;
    await p().click(T('di-review-refresh'));
    await p().waitForFunction(() => document.querySelector('[data-testid="di-review-stale"]') === null);
    assert.equal(requests[before].fileId, undefined, 'Refresh asks for a new overview');
    assert.equal(await exists(T('di-review-stale')), false);
    // the file that was selected is read again, against the new snapshot
    await p().waitForSelector(T('di-review-code'), { timeout: 15_000 });
  },
  STEPS_MS,
);

test(
  'when the client cannot vouch for what it shows, the content is CLEARED and stays cleared',
  async () => {
    const cases: Array<[string, () => Promise<void> | void, RegExp]> = [
      ['expired office', () => send(S.stale), /The office is not live: the review was cleared/],
      ['assignment inactive', () => send(S.inactive), /The assignment changed: the review was cleared/],
      ['task replaced', () => send(S.moved), /The assignment changed: the review was cleared/],
      ['agent gone', () => send(S.agentGone), /The agent is gone: the review was cleared/],
      ['backend down', () => send(S.unavailable), /The office is not live: the review was cleared/],
    ];
    for (const [label, cause, pattern] of cases) {
      await p().close();
      requests = [];
      expectConnection();
      page = await browser!.newPage({ viewport: VIEW });
      await page.goto(url, { waitUntil: 'domcontentloaded' });
      await connected;
      await openReview();
      await openFile('src/app.py');
      await p().waitForSelector(T('di-review-code'));
      await cause();
      await p().waitForSelector(T('di-review-cleared'), { timeout: 15_000 });
      const dialog = await p().$eval(T('di-review-dialog'), (el) => (el as HTMLElement).innerText);
      assert.match(dialog, /cleared/i, label);
      assert.match(dialog, pattern, label);
      assert.doesNotMatch(dialog, /src\/app\.py|return 3|di-fixture-root|compared with commit/, `${label}: nothing of the review remains`);
      // AC7: the cleared state is all our own copy (nothing of the review remains), so none of it carries an em or en dash
      assert.deepEqual((await renderedCopy(p(), T('di-review-dialog'))).authored, [], `${label}: em or en dash in the cleared copy`);
      assert.equal(await p().locator(T('di-review-row')).count(), 0, label);
      assert.equal(await exists(T('di-review-code')), false, label);
      assert.equal(await p().$eval(T('di-review-refresh'), (el) => (el as HTMLButtonElement).disabled), true, label);
      // and it does not come back by itself when the office is live again
      send(S.live);
      await p().waitForTimeout(1_500);
      assert.equal(await p().locator(T('di-review-row')).count(), 0, `${label}: not resurrected`);
      assert.equal(await exists(T('di-review-cleared')), true, label);
      // an answer that arrives late is not shown either
      socket!.send(JSON.stringify(doc.replies.overview));
      await p().waitForTimeout(500);
      assert.equal(await p().locator(T('di-review-row')).count(), 0, `${label}: a late answer is not shown`);
      await shot(`cleared-${label.replace(/\s+/g, '-')}`);
    }
  },
  STEPS_MS * 2,
);

test(
  'a dropped connection clears the review; reopening after the reconnect reads it afresh',
  async () => {
    await openReview();
    await openFile('src/app.py');
    expectConnection(true);
    socket!.close();
    await p().waitForSelector(T('di-review-cleared'), { timeout: 15_000 });
    assert.match(await text(T('di-review-dialog')), /Connection lost/);
    assert.equal(await p().locator(T('di-review-row')).count(), 0);
    await connected;
    send(S.live);
    await p().waitForTimeout(1_000);
    assert.equal(await exists(T('di-review-cleared')), true, 'still cleared after the reconnect');
    await p().click(T('di-review-cleared-close'));
    await p().waitForSelector(T('di-review-dialog'), { state: 'detached' });
    const before = requests.length;
    await selectOnCanvas(CHARACTER);
    await p().waitForSelector(T('di-open-code-review'), { timeout: 15_000 });
    await p().click(T('di-open-code-review'));
    await p().waitForSelector(T('di-review-tree'), { timeout: 20_000 });
    assert.equal(requests.length, before + 1, 'a new read, not the old content');
  },
  STEPS_MS,
);

test(
  'viewports: no sideways page scroll, nothing clipped, a real reading area from desktop to phone',
  async () => {
    const sizes: Array<[string, number, number, number]> = [
      ['desktop-1600x1000', 1600, 1000, 360],
      ['laptop-1280x720', 1280, 720, 230],
      ['small-laptop-1024x700', 1024, 700, 200],
      ['tablet-768x1024', 768, 1024, 300],
      ['phone-390x844', 390, 844, 260],
    ];
    await openReview();
    await openFile('src/syntax_sample.ts');
    for (const [name, width, height, minReading] of sizes) {
      await p().setViewportSize({ width, height });
      await p().waitForTimeout(400);
      if (width < 820) {
        await p().click(T('di-review-tab-diff'));
        await p().waitForSelector(T('di-review-code'));
      }
      const m = await p().evaluate(() => {
        const d = document.querySelector('[data-testid="di-review-dialog"]') as HTMLElement;
        const dr = d.getBoundingClientRect();
        const code = document.querySelector('[data-testid="di-review-code"]') as HTMLElement | null;
        const scrolling = '[data-testid="di-review-tree"], [data-testid="di-review-code"], .di-rv-overview-body';
        const clipped = [...d.querySelectorAll('button, input, summary')]
          .filter((e) => (e as HTMLElement).getClientRects().length > 0 && e.closest(scrolling) === null)
          .map((e) => ({ el: e as HTMLElement, r: e.getBoundingClientRect() }))
          .filter(({ r }) => r.right > dr.right + 1 || r.left < dr.left - 1 || r.top < dr.top - 1 || r.bottom > dr.bottom + 1)
          .map(({ el }) => el.getAttribute('data-testid') ?? el.textContent);
        return {
          pageSideways: document.documentElement.scrollWidth > document.documentElement.clientWidth,
          dialogW: Math.round(dr.width),
          dialogH: Math.round(dr.height),
          dialogInside: dr.left >= 0 && dr.top >= 0 && dr.right <= window.innerWidth && dr.bottom <= window.innerHeight,
          reading: code ? Math.round(code.clientHeight) : 0,
          readingW: code ? Math.round(code.clientWidth) : 0,
          clipped,
          splitDisabled: (document.querySelector('[data-testid="di-review-layout-split"]') as HTMLButtonElement | null)?.disabled ?? null,
          fonts: getComputedStyle(code ?? d).fontSize,
        };
      });
      assert.equal(m.pageSideways, false, `${name}: the page scrolls sideways`);
      assert.ok(m.dialogInside, `${name}: the dialog is inside the window`);
      assert.deepEqual(m.clipped, [], `${name}: clipped controls`);
      assert.ok(m.reading >= minReading, `${name}: only ${m.reading}px of code area (want ${minReading})`);
      // two columns: offered from 1040px; explained but disabled below; not shown at phone width
      if (width >= 1040) assert.equal(m.splitDisabled, false, `${name}: two columns are offered`);
      else if (width >= 820) assert.equal(m.splitDisabled, true, `${name}: two columns are disabled`);
      else assert.equal(m.splitDisabled, null, `${name}: no layout choice at phone width`);
      await shot(`viewport-${name}`);
      if (width < 820) await p().click(T('di-review-tab-files'));
      if (width < 820) await shot(`viewport-${name}-files`);
      if (width < 820) await p().click(T('di-review-tab-diff'));
    }
  },
  STEPS_MS * 2,
);

test(
  'the whole session sends only reads by reference and nothing else',
  async () => {
    await openReview();
    await openFile('src/app.py');
    await openFile('src/util.py');
    await p().click(T('di-review-refresh'));
    await p().waitForTimeout(800);
    assert.ok(requests.length >= 4, `${requests.length} reads were made`);
    for (const r of requests) {
      assert.equal(r.type, 'diCodeReviewRead');
      for (const [key, value] of Object.entries(r)) {
        assert.ok(['type', 'missionRef', 'memberRef', 'taskId', 'listing', 'snapshot', 'fileId'].includes(key), `key ${key}`);
        assert.equal(typeof value, 'string');
        // an identifier, never a path, a root, a ref or an option
        assert.doesNotMatch(String(value), /[/\\]|\.\.|^-/, `${key} looks like a path or option`);
      }
    }
    // The local identity-keyed seat save, if the office sent one, is bounded and PREFERENCE-ONLY: the closed {v, entries} shape within the contract's
    // bounds, each entry exactly an identity tuple and a seat id. (Sent on the first real change; a second send with nothing changed would be churn.)
    assert.equal(seatPreferenceSaves.length, 1, 'exactly one identity-keyed seat save: the first persistence of the fixture identity, none after');
    for (const state of seatPreferenceSaves) {
      const payload = state as { v: unknown; entries: Array<Record<string, unknown>> };
      assert.deepEqual(Object.keys(payload).sort(), ['entries', 'v']);
      assert.equal(checkDiSeatPreferences(payload), null);
      for (const entry of payload.entries) {
        assert.deepEqual(Object.keys(entry).sort(), ['i', 's']);
        assert.ok(Array.isArray(entry.i) && entry.i.length === 5 && typeof entry.s === 'string');
      }
    }
    assert.deepEqual(otherMessages, [], `no other message type was sent: ${otherMessages.join()}`);
    // The only other traffic is the entry's read-only overview ping, in exactly its one shape.
    for (const ping of overviewPings) assert.ok(isOverviewPing(ping), JSON.stringify(ping));
  },
  STEPS_MS,
);

test(
  'the rendered review dialog carries no em or en dash (AC7, AC-UX-16)',
  async () => {
    await openReview();
    await openFile('src/app.py');
    await p().waitForSelector(T('di-review-line'));
    const copy = await renderedCopy(p(), T('di-review-dialog'));
    // the dialog's own words carry no dash; the repository's text (code, paths, names) is content, kept as it is
    assert.deepEqual(copy.authored, [], 'em or en dash in the rendered review dialog');
    console.log(`[copy] review dialog: authored dashes ${copy.authored.length}; content lines with a dash, preserved: ${copy.content.length}`);
    // the repository's text is marked as content, so it is never read as the dialog's copy
    assert.ok((await p().locator('[data-testid="di-review-dialog"] [data-copy="content"]').count()) > 0);
  },
  STEPS_MS,
);

/** The diff's own scroll region: the nearest element at or above the code that actually scrolls. */
const diffScroller = () =>
  p().evaluate(() => {
    let el = document.querySelector<HTMLElement>('[data-testid="di-review-code"]');
    while (el && el.scrollHeight <= el.clientHeight) el = el.parentElement;
    if (!el) return null;
    el.setAttribute('data-probe-scroller', 'true');
    return { scrollHeight: el.scrollHeight, clientHeight: el.clientHeight };
  });
const scrollerTop = () =>
  p().$eval('[data-probe-scroller="true"]', (el) => (el as HTMLElement).scrollTop);
const rootInert = () => p().evaluate(() => document.getElementById('root')?.hasAttribute('inert') ?? null);

test(
  'minimize keeps the review as it was and restore gives it back (AC3)',
  async () => {
    await openReview();
    await openFile('data/large.txt');
    await p().waitForSelector(T('di-review-line'));
    // A search that keeps the open file listed, a selected file, and the diff scrolled to a DISTINCT place:
    // 1234 is neither the top a reset gives (0) nor anything a jump to the file or a section would give.
    await p().fill(T('di-review-search'), 'data');
    const scroller = await diffScroller();
    assert.ok(scroller !== null && scroller.scrollHeight - scroller.clientHeight > 2000, `the diff scrolls: ${JSON.stringify(scroller)}`);
    await p().$eval('[data-probe-scroller="true"]', (el) => {
      (el as HTMLElement).scrollTop = 1234;
    });
    assert.equal(await scrollerTop(), 1234);
    assert.equal(await rootInert(), true, 'the open review is modal');
    assert.ok(await exists(T('di-review-minimize')), 'the review offers Minimize');
    // The reader works in the content (the search box), then reaches Minimize BY KEYBOARD (Shift+Tab leaves the
    // content for the dialog's own controls) and presses it. Restore must give focus back to that working
    // control, not park it on the Minimize toggle.
    await p().focus(T('di-review-search'));
    let presses = 0;
    while ((await activeInfo()) !== 'button[di-review-minimize]' && presses < 40) {
      await p().keyboard.press('Shift+Tab');
      presses += 1;
    }
    assert.equal(await activeInfo(), 'button[di-review-minimize]', `Minimize reached by keyboard (${presses} Shift+Tab)`);
    await p().keyboard.press('Enter');

    // minimized: hidden but kept, the office usable, focus on the named control that restores it
    await p().waitForSelector(T('di-review-restore'));
    assert.equal(await p().$eval(T('di-review-backdrop'), (el) => (el as HTMLElement).hidden), true, 'hidden while minimized');
    assert.ok(await exists(T('di-review-dialog')), 'still mounted while minimized');
    assert.equal(await rootInert(), false, 'the app behind is usable while minimized');
    assert.equal(await activeInfo(), 'button[di-review-restore]', 'focus is on Restore, never the page');
    assert.equal(await text(T('di-review-restore')), 'Restore code review');
    assert.match((await p().getAttribute(T('di-review-restore'), 'aria-label')) ?? '', /Restore code review/, 'its name contains its words');
    assert.deepEqual((await renderedCopy(p(), T('di-review-restore'))).authored, [], 'em or en dash on the restore control');
    await p().fill(T('di-find-input'), 'zz');
    assert.equal(await activeInfo(), 'input[di-find-input]', 'the office panel takes focus and typing');
    assert.equal(await p().$eval(T('di-find-input'), (el) => (el as HTMLInputElement).value), 'zz');
    await p().fill(T('di-find-input'), '');
    // the minimized strip in a NARROW window (Checkpoint 2 evidence), then the window back to its size
    await p().setViewportSize({ width: 640, height: 800 });
    await frames(3);
    await shot('minimized-review-narrow-640');
    await p().setViewportSize(VIEW);
    await frames(3);

    // restore by keyboard: exactly as it was, modal again, focus back on the control that had it
    await p().focus(T('di-review-restore'));
    await p().keyboard.press('Enter');
    await p().waitForSelector(T('di-review-restore'), { state: 'detached' });
    assert.equal(await p().$eval(T('di-review-backdrop'), (el) => (el as HTMLElement).hidden), false);
    assert.equal(await scrollerTop(), 1234, 'the diff is where the reader left it');
    assert.equal(await p().$eval(T('di-review-search'), (el) => (el as HTMLInputElement).value), 'data', 'the search is kept');
    assert.equal(await p().getAttribute(rowFor('data/large.txt'), 'aria-selected'), 'true', 'the selected file is kept');
    assert.match(await text(T('di-review-path')), /^data\/large\.txt/);
    assert.equal(await rootInert(), true, 'modal again once restored');
    assert.equal(await activeInfo(), 'input[di-review-search]', 'focus is back on the working control, not the Minimize toggle');
    assert.ok(await focusInsideDialog());

    // Escape clears the search first (as before), then closes, and focus goes back to the entry that opened it
    await p().keyboard.press('Escape');
    assert.equal(await p().$eval(T('di-review-search'), (el) => (el as HTMLInputElement).value), '');
    assert.ok(await exists(T('di-review-dialog')), 'the first Escape cleared the search and closed nothing');
    await p().keyboard.press('Escape');
    await p().waitForSelector(T('di-review-dialog'), { state: 'detached' });
    assert.equal(await activeInfo(), 'button[di-open-code-review]');
    assert.equal(await rootInert(), false);
  },
  STEPS_MS,
);

test(
  'minimize and restore by mouse give focus back to the search box with its text (AC3 review, mouse)',
  async () => {
    await openReview();
    // The reader clicks into the search and types; then uses the MOUSE for Minimize and Restore. A click on Minimize
    // focuses the toggle, so restore must hand focus back to the control the reader was working in.
    await p().click(T('di-review-search'));
    await p().keyboard.type('large.txt');
    assert.equal(await activeInfo(), 'input[di-review-search]');
    await p().click(T('di-review-minimize'));
    await p().waitForSelector(T('di-review-restore'));
    assert.equal(await activeInfo(), 'button[di-review-restore]', 'minimized: focus is on Restore, never the page');
    await p().click(T('di-review-restore'));
    await p().waitForSelector(T('di-review-restore'), { state: 'detached' });
    assert.equal(
      await activeInfo(),
      'input[di-review-search]',
      'restored: focus is back in the search box, not on Minimize and never the page',
    );
    assert.equal(await p().$eval(T('di-review-search'), (el) => (el as HTMLInputElement).value), 'large.txt', 'the typed text is intact');
    assert.equal(await rootInert(), true);
  },
  STEPS_MS,
);

test(
  '[reachable runtime path] a review cleared while minimized behind Reception stays mounted, and every guard is owned',
  async () => {
    await openReview();
    await p().click(T('di-review-minimize'));
    await p().waitForSelector(T('di-review-restore'));
    assert.equal(await rootInert(), false);
    // Reception opens in front of the hidden review
    await p().click(T('di-open-reception'));
    await p().waitForSelector(T('di-reception-inbox'));
    assert.equal(await rootInert(), true, 'Reception is modal');
    // the review is invalidated WHILE HIDDEN: the office no longer vouches for the agent
    send(S.agentGone);
    await p().waitForSelector(T('di-review-cleared'), { state: 'attached', timeout: 15_000 });
    assert.equal(await rootInert(), true, "Reception's guard holds while the hidden review is cleared");
    assert.ok(
      await p().evaluate(() => document.querySelector('[data-testid="di-reception-inbox"]')?.contains(document.activeElement) ?? false),
      'focus stays inside Reception',
    );
    assert.ok(await exists(T('di-review-dialog')), 'the review is cleared, not closed: it is still mounted');
    assert.equal(await p().$eval(T('di-review-backdrop'), (el) => (el as HTMLElement).hidden), true, 'and still hidden');
    // Escape closes Reception and gives focus back to its opener; the hidden review holds no guard
    await p().keyboard.press('Escape');
    await p().waitForSelector(T('di-reception-inbox'), { state: 'detached' });
    assert.equal(await rootInert(), false, 'nothing in front: nothing is inert');
    assert.equal(await activeInfo(), 'button[di-open-reception]');
    // restoring the review shows the cleared state, modal again
    await p().click(T('di-review-restore'));
    await p().waitForSelector(T('di-review-restore'), { state: 'detached' });
    assert.equal(await rootInert(), true, 'the restored review is modal');
    await p().waitForSelector(T('di-review-cleared'));
    await p().click(T('di-review-cleared-close'));
    await p().waitForSelector(T('di-review-dialog'), { state: 'detached' });
    assert.equal(await rootInert(), false, 'closed: nothing is inert');
    // Close-time focus (pre-existing at 109e096c, fixed as the twin of the restore fallback): the opener was the
    // dock's "Open code review" button, which left with the agent. Focus must not fall to the page body; it goes to
    // the office Find box, which is labelled and always present with the office panel.
    assert.equal(await activeInfo(), 'input[di-find-input]', 'the opener is gone: focus goes to the office Find box, never the page');
  },
  STEPS_MS,
);

test(
  'AC3 review fallbacks: ABSENT, nothing in the content ever had focus, so restore lands on Minimize',
  async () => {
    await openReview();
    // Minimized by mouse before any content control (search, file list, diff) ever had focus.
    assert.ok(
      !(await p().evaluate(() => document.querySelector('.di-rv-content')?.contains(document.activeElement) ?? false)),
      'no content control has focus',
    );
    await p().click(T('di-review-minimize'));
    await p().waitForSelector(T('di-review-restore'));
    await p().click(T('di-review-restore'));
    await p().waitForSelector(T('di-review-restore'), { state: 'detached' });
    assert.equal(await activeInfo(), 'button[di-review-minimize]', 'the first fallback, never the page or the restore control');
    assert.equal(await rootInert(), true);
  },
  STEPS_MS,
);

test(
  'AC3 review fallbacks: REMOVED, the focused file row is gone after a clear while minimized, so restore lands on a named fallback',
  async () => {
    await openReview();
    // The reader works in the file list: a file row has focus.
    await p().click(rowFor('src/app.py'));
    assert.match(await activeInfo(), /\[di-review-row\]$/, 'a file row has focus');
    await p().click(T('di-review-minimize'));
    await p().waitForSelector(T('di-review-restore'));
    // Reachable without new code: while hidden the office stops vouching for the agent, the review is CLEARED (not
    // closed) and the cleared notice takes the file list's place, so the row that had focus is removed.
    send(S.agentGone);
    await p().waitForSelector(T('di-review-cleared'), { state: 'attached', timeout: 15_000 });
    assert.equal(await p().locator(T('di-review-row')).count(), 0, 'the focused row is removed');
    await p().click(T('di-review-restore'));
    await p().waitForSelector(T('di-review-restore'), { state: 'detached' });
    const focused = await activeInfo();
    assert.ok(
      ['button[di-review-minimize]', 'div[di-review-dialog]'].includes(focused),
      `a named fallback (Minimize, else the dialog), never the page: ${focused}`,
    );
    assert.equal(focused, 'button[di-review-minimize]', 'Minimize is there, so it is the one');
    assert.equal(await rootInert(), true);
  },
  STEPS_MS,
);

/** Where a restore chip is, whether it is in the minimized tray, whether it is inert, and its two names. */
const chipInfo = (testid: string) =>
  p().evaluate((id) => {
    const el = document.querySelector<HTMLElement>(`[data-testid="${id}"]`);
    if (el === null) return null;
    const r = el.getBoundingClientRect();
    const mid = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2);
    return {
      inTray: el.closest('[data-testid="di-minimized-tray"]') !== null,
      inert: el.closest('[inert]') !== null,
      visible: el.innerText.replace(/\s+/g, ' ').trim(),
      name: el.getAttribute('aria-label') ?? el.textContent ?? '',
      rect: { top: r.top, bottom: r.bottom, left: r.left, right: r.right, width: r.width, height: r.height },
      hitIsChip: mid !== null && el.contains(mid),
    };
  }, testid);

test(
  'AC3 cross-surface, review and Reception: two dialogs minimized at once, a restore inside another modal is impossible, inert and focus stay right',
  async () => {
    await openReview();
    // the reader works in the review (search), then minimizes it by mouse
    await p().click(T('di-review-search'));
    await p().keyboard.type('src');
    assert.equal(await rootInert(), true, 'the open review is modal');
    await p().click(T('di-review-minimize'));
    await p().waitForSelector(T('di-review-restore'));
    assert.equal(await rootInert(), false);
    await shot('minimized-review-strip');

    // Reception opens in front of the hidden review; the reader works in it, then minimizes it by mouse too
    await p().click(T('di-open-reception'));
    await p().waitForSelector(T('di-reception-inbox'));
    assert.equal(await rootInert(), true, 'Reception is modal');
    await p().click(T('di-reception-search'));
    await p().keyboard.type('SYNTHETIC');
    await p().click(T('di-reception-minimize'));
    await p().waitForSelector(T('di-reception-restore'));

    // both dialogs minimized: two separate slots in the one tray, the app behind usable
    const review = (await chipInfo('di-review-restore'))!;
    const reception = (await chipInfo('di-reception-restore'))!;
    assert.ok(review.inTray && reception.inTray, 'both restore chips are in the minimized tray');
    const apart = review.rect.bottom <= reception.rect.top || reception.rect.bottom <= review.rect.top;
    assert.ok(apart, `two stacked slots, no overlap: ${JSON.stringify([review.rect, reception.rect])}`);
    assert.ok(review.rect.height > 0 && reception.rect.height > 0, 'each slot has a real size');
    assert.match(review.name, /Restore code review/);
    assert.ok(review.name.includes('Restore code review') && review.visible.includes('Restore code review'), `review chip name contains its label: ${review.name} | ${review.visible}`);
    assert.ok(reception.name.includes('Restore reception') && reception.visible.includes('Restore reception'), `Reception chip name contains its label: ${reception.name} | ${reception.visible}`);
    assert.equal(review.inert || reception.inert, false, 'neither chip is inert while nothing is in front');
    assert.equal(await rootInert(), false, 'nothing in front: the app behind is usable');
    await p().fill(T('di-find-input'), 'zz');
    assert.equal(await activeInfo(), 'input[di-find-input]', 'the office Find box takes focus');
    assert.equal(await p().$eval(T('di-find-input'), (el) => (el as HTMLInputElement).value), 'zz', 'and typing');
    await p().fill(T('di-find-input'), '');
    await shot('minimized-two-slots-review-reception');

    // a Mission's details open in front of both: everything behind is inert, no chip can be reached
    await p().locator(T('di-open-inspector-mission')).first().click();
    await p().waitForSelector(T('di-inspector'));
    assert.equal(await rootInert(), true, 'the details dialog is modal');
    const before = await activeInfo();
    for (const id of ['di-review-restore', 'di-reception-restore']) {
      const chip = (await chipInfo(id))!;
      assert.ok(chip.inTray, `${id} stays in the tray`);
      assert.equal(chip.inert, true, `${id} is inert under the details dialog`);
      assert.equal(chip.hitIsChip, false, `a click at the centre of ${id} cannot reach it`);
      await p().evaluate((t) => document.querySelector<HTMLElement>(`[data-testid="${t}"]`)?.focus(), id);
      assert.equal(await activeInfo(), before, `a focus attempt on ${id} changes nothing`);
    }
    assert.ok(await exists(T('di-review-dialog')) && (await p().$eval(T('di-review-backdrop'), (el) => (el as HTMLElement).hidden)), 'the review stays hidden');
    assert.ok(await exists(T('di-reception-inbox')) && (await p().$eval(T('di-reception-inbox-backdrop'), (el) => (el as HTMLElement).hidden)), 'Reception stays hidden');
    await shot('minimized-strip-inert-under-details');
    for (let i = 1; i <= 25; i += 1) {
      await p().keyboard.press('Tab');
      assert.ok(
        await p().evaluate(() => document.querySelector('[data-testid="di-inspector"]')?.contains(document.activeElement) ?? false),
        `Tab ${i} stays inside the details dialog (${await activeInfo()})`,
      );
    }
    await p().keyboard.press('Escape');
    await p().waitForSelector(T('di-inspector'), { state: 'detached' });
    assert.equal(await rootInert(), false, 'details closed with both dialogs still minimized: nothing is inert');
    assert.equal(await activeInfo(), 'button[di-open-inspector-mission]', 'focus returns to the control that opened details');

    // restore the review by mouse: modal again, the Reception chip is still in the tray and now inert
    await p().click(T('di-review-restore'));
    await p().waitForSelector(T('di-review-restore'), { state: 'detached' });
    assert.equal(await p().$eval(T('di-review-backdrop'), (el) => (el as HTMLElement).hidden), false);
    assert.equal(await rootInert(), true, 'the restored review is modal');
    const rc = (await chipInfo('di-reception-restore'))!;
    assert.ok(rc.inTray && rc.inert && rc.hitIsChip === false, `the Reception chip stays in the tray, inert and unreachable: ${JSON.stringify(rc)}`);
    assert.equal(await activeInfo(), 'input[di-review-search]', "focus is in the review's last content control");
    assert.equal(await p().$eval(T('di-review-search'), (el) => (el as HTMLInputElement).value), 'src', 'its text is intact');
    await shot('restored-review-after-strip');

    // minimize the review again, then restore Reception: its own state, with the review chip inert behind it
    await p().click(T('di-review-minimize'));
    await p().waitForSelector(T('di-review-restore'));
    assert.equal(await rootInert(), false);
    await p().click(T('di-reception-restore'));
    await p().waitForSelector(T('di-reception-restore'), { state: 'detached' });
    assert.equal(await p().$eval(T('di-reception-inbox-backdrop'), (el) => (el as HTMLElement).hidden), false);
    assert.equal(await rootInert(), true, 'the restored Reception is modal');
    assert.equal(await activeInfo(), 'input[di-reception-search]', "focus is in Reception's last content control");
    assert.equal(await p().$eval(T('di-reception-search'), (el) => (el as HTMLInputElement).value), 'SYNTHETIC', 'its text is intact');
    const rv = (await chipInfo('di-review-restore'))!;
    assert.ok(rv.inTray && rv.inert && rv.hitIsChip === false, `the review chip stays in the tray, inert and unreachable: ${JSON.stringify(rv)}`);
  },
  STEPS_MS,
);

/**
 * What the browser DRAWS for the dialog frame, measured with getComputedStyle (no source matching). The expected values
 * are resolved by probe elements that use the shared tokens themselves (`var(--di-*)`), so no colour literal is needed
 * and the comparison is the browser's own.
 */
const measureFrame = () =>
  p().evaluate(() => {
    const resolve = (property: 'background-color' | 'color' | 'box-shadow', value: string) => {
      const probe = document.createElement('div');
      probe.style.setProperty(property === 'background-color' ? 'background' : property, value);
      document.body.appendChild(probe);
      const computed = getComputedStyle(probe).getPropertyValue(property);
      probe.remove();
      return computed;
    };
    const backdrop = getComputedStyle(document.querySelector('[data-testid="di-review-backdrop"]')!);
    const dialogEl = document.querySelector<HTMLElement>('[data-testid="di-review-dialog"]')!;
    const dialog = getComputedStyle(dialogEl);
    return {
      fullscreen: dialogEl.getAttribute('data-fullscreen'),
      backdrop: backdrop.backgroundColor,
      scrim: resolve('background-color', 'var(--di-scrim)'),
      transparent: resolve('background-color', 'transparent'),
      borderWidth: dialog.borderTopWidth,
      borderStyle: dialog.borderTopStyle,
      borderColor: dialog.borderTopColor,
      line: resolve('color', 'var(--di-line)'),
      radius: dialog.borderTopLeftRadius,
      shadow: dialog.boxShadow,
      popShadow: resolve('box-shadow', 'var(--di-shadow-pop)'),
    };
  });

test(
  'review dialog frame, COMPUTED: the scrim, the 1px line, the 16px radius and the soft shadow are what the browser draws',
  async () => {
    await openReview();
    await openFile('src/app.py');
    await shot('review-frame-scrim-after-fix');
    const f = await measureFrame();
    // the expected values are real: the shared scrim is a visible colour, not the browser's transparent default
    assert.notEqual(f.scrim, f.transparent, `the shared scrim token resolves to a visible colour (${f.scrim})`);
    assert.notEqual(f.popShadow, 'none', 'the shared pop shadow token resolves to a shadow');
    assert.equal(f.backdrop, f.scrim, `the backdrop is drawn in the shared scrim, not transparent (backdrop ${f.backdrop}, scrim ${f.scrim})`);
    assert.notEqual(f.backdrop, f.transparent, 'the backdrop is not transparent');
    assert.equal(f.borderWidth, '1px');
    assert.equal(f.borderStyle, 'solid');
    assert.equal(f.borderColor, f.line, 'the dialog line is the shared line colour');
    assert.equal(f.radius, '16px');
    assert.equal(f.shadow, f.popShadow, 'the dialog shadow is the shared soft shadow');
    assert.notEqual(f.shadow, 'none');
    // full screen is square
    await p().click(T('di-review-fullscreen'));
    await p().waitForSelector('[data-testid="di-review-dialog"][data-fullscreen="true"]');
    const full = await measureFrame();
    assert.equal(full.fullscreen, 'true');
    assert.equal(full.radius, '0px', 'full screen keeps square corners');
    assert.equal(full.backdrop, full.scrim, 'the scrim is the same in full screen');
  },
  STEPS_MS,
);

/** The `.di-rv` consuming rules that matched NO element in any state audited so far (the audit could not check these). */
let neverRendered: Set<string> | null = null;
/** One audit, asserted: no token a rule consumes is missing on an element it matches, and the audit really looked. */
function assertScopeClean(state: string, audit: TokenScopeAudit) {
  const unmatchedHere = new Set(audit.unmatched.filter((selector) => selector.includes('.di-rv')));
  neverRendered = neverRendered === null ? unmatchedHere : new Set([...neverRendered].filter((x) => unmatchedHere.has(x)));
  console.log(
    `[scope] ${state}: ${audit.matchedRules}/${audit.consumingRules} consuming rules matched, ${audit.pairsChecked} element-token pairs, gaps ${audit.gaps.length}`,
  );
  assert.ok(audit.consumingRules > 20, `${state}: the audit saw a real set of consuming rules (${audit.consumingRules})`);
  assert.ok(audit.pairsChecked > 100, `${state}: the audit checked a real number of element-token pairs (${audit.pairsChecked})`);
  assert.deepEqual(
    audit.gaps.map((g) => `${g.selector} consumes ${g.token}: no value on ${g.elements} matched element(s), e.g. ${g.example}`),
    [],
    `${state}: every token a rule consumes has a value where the rule applies`,
  );
}

test(
  'review dialog token scope, AUDITED in the rendered states: no rule consumes a custom property that is out of its scope',
  async () => {
    // open and settled with a file selected
    await openReview();
    await openFile('src/app.py');
    assertScopeClean('dialog open, settled, file selected', await tokenScopeAudit(p()));
    // side by side, and full screen
    await p().click(T('di-review-layout-split'));
    await p().waitForSelector(T('di-review-code-old'));
    assertScopeClean('side by side', await tokenScopeAudit(p()));
    await p().click(T('di-review-fullscreen'));
    await p().waitForSelector('[data-testid="di-review-dialog"][data-fullscreen="true"]');
    assertScopeClean('full screen', await tokenScopeAudit(p()));
    await p().click(T('di-review-fullscreen'));
    // minimized: the tray holds the restore chip
    await p().click(T('di-review-minimize'));
    await p().waitForSelector(T('di-review-restore'));
    assertScopeClean('minimized, tray shown', await tokenScopeAudit(p()));
    await p().click(T('di-review-restore'));
    await p().waitForSelector(T('di-review-restore'), { state: 'detached' });
    await p().keyboard.press('Escape');
    await p().waitForSelector(T('di-review-dialog'), { state: 'detached' });
    // loading: the overview is answered late (the existing delayed-reply helper), so the loading notice is on screen
    replyDelayMs = 3000;
    await p().click(T('di-open-code-review'));
    await p().waitForSelector(T('di-review-loading'));
    assertScopeClean('loading', await tokenScopeAudit(p()));
    await p().keyboard.press('Escape');
    await p().waitForSelector(T('di-review-dialog'), { state: 'detached' });
    replyDelayMs = 0;
    // cleared: the office stops vouching for the agent while the review is open
    await p().click(T('di-open-code-review'));
    await p().waitForSelector(T('di-review-tree'), { timeout: 20_000 });
    send(S.agentGone);
    await p().waitForSelector(T('di-review-cleared'), { timeout: 15_000 });
    assertScopeClean('cleared', await tokenScopeAudit(p()));
    await p().click(T('di-review-cleared-close'));
    await p().waitForSelector(T('di-review-dialog'), { state: 'detached' });
    // for the record: review rules no audited state rendered (not checked here; the structural scan covers them)
    console.log(`[scope] .di-rv consuming rules rendered in none of the audited states: ${neverRendered?.size ?? 0}: ${JSON.stringify([...(neverRendered ?? [])])}`);
  },
  STEPS_MS,
);

test(
  'review entry token scope, AUDITED when the review is not offered (the assignment is no longer active)',
  async () => {
    // a state of its own: once the assignment is inactive the agent is no longer drawn, so no dialog follows it
    await selectAgent();
    send(S.inactive);
    await p().waitForSelector(T('di-dock-code-review-unavailable'), { timeout: 15_000 });
    assertScopeClean('entry not offered', await tokenScopeAudit(p()));
  },
  STEPS_MS,
);

/*
 * The strip's Restore labels, RENDERED. A programmatic pass (name, focus, a bounding box) is not a visual pass: a label can have
 * a name and a focus order and be laid out OUTSIDE its 32 px button, clipped away. This measures where each visible text node is
 * DRAWN against the button that carries it (diTextFit.ts), with the review chip alone and with Reception minimized beside it,
 * at the sizes the owner will use, each on a fresh page after the layout has settled.
 */
const STRIP_VIEWS = [
  { name: '1440 x 900', width: 1440, height: 900, scale: 1 },
  { name: '900 x 700', width: 900, height: 700, scale: 1 },
  { name: 'narrow 640 x 800', width: 640, height: 800, scale: 1 },
  { name: 'zoom-equivalent (720 x 450 at deviceScaleFactor 2; a scaled viewport, NOT a native Command-plus test)', width: 720, height: 450, scale: 2 },
] as const;
const STRIP_LABEL: Record<string, string> = {
  'di-review-restore': 'Restore code review',
  'di-reception-restore': 'Restore reception',
};

/** A fresh page at this size, set up exactly as beforeEach does (connection awaited, dialogs dismissed). */
async function freshPageAt(view: { width: number; height: number; scale: number }) {
  await page?.close();
  requests = [];
  otherMessages = [];
  overviewPings = [];
  replyHook = null;
  replyDelayMs = 0;
  expectConnection();
  page = await browser!.newPage({ viewport: { width: view.width, height: view.height }, deviceScaleFactor: view.scale });
  page.on('dialog', (d) => void d.dismiss());
  await page.goto(url, { waitUntil: 'domcontentloaded' });
  await connected;
}

function assertChipGeometry(at: string, c: TextFitControl) {
  const full = STRIP_LABEL[c.id];
  assert.ok(full !== undefined, `${at}: ${c.id} is a restore control this test knows`);
  const tol = 0.75;
  const px = (n: number) => Math.round(n * 10) / 10;
  // rendered text outside the button's VISIBLE, UNCLIPPED region (the button's padding box, narrowed by every ancestor that
  // really clips it, and the window): pixels per side
  assert.deepEqual(c.outside, [], `${at}: ${c.id}: rendered text outside the button's visible, unclipped region (pixels per side)`);
  assert.deepEqual(c.textNotHit, [], `${at}: ${c.id}: text whose centre is not painted by the button (clipped or covered)`);
  // EVERY drawn piece of text (the label, and a glyph beside it) lies inside the button's own box: pixels outside, per side
  const escaping = c.texts
    .map((t) => ({
      text: t.text,
      left: px(Math.max(0, c.box.left - t.left)),
      right: px(Math.max(0, t.right - c.box.right)),
      top: px(Math.max(0, c.box.top - t.top)),
      bottom: px(Math.max(0, t.bottom - c.box.bottom)),
    }))
    .filter((o) => o.left > tol || o.right > tol || o.top > tol || o.bottom > tol);
  assert.deepEqual(escaping, [], `${at}: ${c.id}: rendered text outside the button's own box (pixels per side)`);
  assert.equal(c.clipsContent, false, `${at}: ${c.id}: the button clips its own content`);
  assert.equal(c.controlWritingMode, 'horizontal-tb', `${at}: ${c.id}: the flex container keeps a horizontal writing mode (only the label is vertical)`);
  assert.deepEqual(c.controlOffWindow, { left: 0, right: 0, top: 0, bottom: 0 }, `${at}: ${c.id}: the button is inside the window`);
  assert.equal(c.hitsItself, true, `${at}: ${c.id}: a click at its centre reaches it`);
  // the FULL words, never shortened to fit
  const label = c.texts.find((t) => t.text === full);
  assert.ok(label, `${at}: ${c.id}: the full label "${full}" is drawn (not shortened); drawn text: ${JSON.stringify(c.texts.map((t) => t.text))}`);
  assert.ok(label.width >= 10 && label.height >= 40, `${at}: ${c.id}: the label is drawn with a readable box (${label.width} x ${label.height})`);
  assert.ok(
    label.left >= c.box.left - tol && label.right <= c.box.right + tol,
    `${at}: ${c.id}: the rendered label text (${label.left} to ${label.right}) lies between the button's left (${c.box.left}) and right (${c.box.right}) edges`,
  );
  assert.ok(
    label.top >= c.box.top - tol && label.bottom <= c.box.bottom + tol,
    `${at}: ${c.id}: the rendered label text (${label.top} to ${label.bottom}) lies between the button's top (${c.box.top}) and bottom (${c.box.bottom}) edges`,
  );
}

test(
  'AC3 strip label, review chip, RENDERED GEOMETRY: the Restore label is drawn inside its button, unclipped and in full, alone and beside Reception, at normal, narrow and zoom-equivalent sizes (720 x 450 at deviceScaleFactor 2, a scaled viewport, not a native Command-plus test)',
  async () => {
    for (const view of STRIP_VIEWS) {
      await freshPageAt(view);
      // the review chip ALONE
      await openReview();
      await p().click(T('di-review-minimize'));
      await p().waitForSelector(T('di-review-restore'));
      await p().waitForTimeout(1200); // settled, as an owner sees it
      const alone = await textFit(p(), T('di-minimized-tray'));
      assert.deepEqual(alone.controls.map((c) => c.id), ['di-review-restore'], `${view.name}: the strip holds exactly the review chip`);
      for (const c of alone.controls) assertChipGeometry(`${view.name}, review alone`, c);

      // Reception minimized beside it: two stacked slots, neither overlapping the other
      await p().click(T('di-open-reception'));
      await p().waitForSelector(T('di-reception-inbox'));
      await p().click(T('di-reception-minimize'));
      await p().waitForSelector(T('di-reception-restore'));
      await p().waitForTimeout(1200);
      const both = await textFit(p(), T('di-minimized-tray'));
      assert.deepEqual(both.controls.map((c) => c.id).sort(), ['di-reception-restore', 'di-review-restore'], `${view.name}: the strip holds both restore controls`);
      for (const c of both.controls) assertChipGeometry(`${view.name}, review + Reception`, c);
      const [a, b] = [...both.controls].sort((x, y) => x.box.top - y.box.top);
      assert.ok(a.box.bottom <= b.box.top + 0.75, `${view.name}: the two slots are stacked (${a.id} ends ${a.box.bottom}, ${b.id} starts ${b.box.top}), not overlapping`);
    }
  },
  STEPS_MS * 2,
);

/*
 * AC-UX-13 / WCAG 2.2 SC 1.4.10 (Reflow): the review dialog at 320 CSS px wide. SOURCE-MOUNTED SEAM, NOT THE PRODUCTION BUNDLE: the real App
 * mounted from source by the Vite dev server over the test WebSocket office. It is not integrated production proof.
 *
 * Reading and control content (header, search, file tree, tabs, notices) must reflow without horizontal scrolling; the code and diff area is
 * different in nature (a source line is as long as it is), so its horizontal scrolling is reported separately and never merged with the first.
 */
const REFLOW_320 = { name: 'SC 1.4.10 reflow, 320 x 568', width: 320, height: 568, scale: 1 } as const;

/** What scrolls, clips or spills sideways under `rootSelector`, split into reading and control content versus the code and diff area. */
const horizontalReport = (rootSelector: string) =>
  p().evaluate((selector) => {
    const root = document.querySelector<HTMLElement>(selector)!;
    const box = root.getBoundingClientRect();
    const codeArea = '[data-testid="di-review-code"], [data-testid="di-review-split"], .di-rv-code-scroll';
    const idOf = (el: Element) =>
      el.getAttribute('data-testid') ?? `${el.tagName.toLowerCase()}.${String(el.getAttribute('class') ?? '').split(' ')[0]}`;
    // the screen-reader-only status line is 1 px wide by design (never visible reading content): not part of this report
    const shown = (el: Element) =>
      (el as HTMLElement).checkVisibility({ checkVisibilityCSS: true }) && (el as HTMLElement).getClientRects().length > 0 && el.closest('.di-rv-visually-hidden') === null;
    const round = (n: number) => Math.round(n * 10) / 10;
    type Row = { id: string; area: 'reading and control' | 'code and diff'; overflowX: string; scrollWidth: number; clientWidth: number; excessPx: number };
    const containers: Row[] = [];
    for (const el of [root, ...Array.from(root.querySelectorAll<HTMLElement>('*'))]) {
      if (!shown(el)) continue;
      const overflowX = getComputedStyle(el).overflowX;
      const excess = el.scrollWidth - el.clientWidth;
      if (overflowX !== 'visible' && excess > 1)
        containers.push({
          id: idOf(el),
          area: el.closest(codeArea) !== null ? 'code and diff' : 'reading and control',
          overflowX,
          scrollWidth: el.scrollWidth,
          clientWidth: el.clientWidth,
          excessPx: excess,
        });
    }
    // reading and control TEXT drawn beyond the dialog's own box (the dialog clips it) or off the window
    const textOut: Array<{ text: string; id: string; pastDialogRightPx: number; pastDialogLeftPx: number; offWindowRightPx: number; offWindowLeftPx: number }> = [];
    const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
    for (let n = walker.nextNode(); n; n = walker.nextNode()) {
      const parent = n.parentElement;
      const content = (n.textContent ?? '').replace(/\s+/g, ' ').trim();
      if (!parent || content === '' || !shown(parent) || parent.closest(codeArea) !== null) continue;
      const range = document.createRange();
      range.selectNodeContents(n);
      for (const r of Array.from(range.getClientRects())) {
        if (r.width === 0) continue;
        const row = {
          text: content.slice(0, 50),
          id: idOf(parent),
          pastDialogRightPx: round(Math.max(0, r.right - box.right)),
          pastDialogLeftPx: round(Math.max(0, box.left - r.left)),
          offWindowRightPx: round(Math.max(0, r.right - window.innerWidth)),
          offWindowLeftPx: round(Math.max(0, 0 - r.left)),
        };
        if (row.pastDialogRightPx > 0.75 || row.pastDialogLeftPx > 0.75 || row.offWindowRightPx > 0.75 || row.offWindowLeftPx > 0.75) textOut.push(row);
      }
    }
    const doc = document.documentElement;
    return {
      window: { width: window.innerWidth, height: window.innerHeight },
      box: { left: round(box.left), right: round(box.right), top: round(box.top), bottom: round(box.bottom), width: round(box.width) },
      pageHorizontalScrollPx: Math.max(doc.scrollWidth - window.innerWidth, document.body.scrollWidth - window.innerWidth, 0),
      narrowLayout: document.querySelector('[data-testid="di-review-body"]')?.getAttribute('data-narrow') ?? null,
      containers,
      textOut,
    };
  }, rootSelector);

type Dialog320 = Awaited<ReturnType<typeof measureDialogAt320>>;

/** One measured state of the dialog at 320: the sideways report, and every control but the sticky diff headers through the bounded helper. */
async function measureDialogAt320(state: string) {
  const report = await horizontalReport(T('di-review-dialog'));
  // The diff's section-header buttons are position: sticky, which the bounded helper REFUSES (textFit UNSUPPORTED geometry: position: sticky on
  // di-review-hunk). That is a finding, reported: the helper cannot measure them, so they are scoped out here, never measured by a local workaround.
  const CONTROLS = 'button:not([data-testid="di-review-hunk"]), a[href], summary, [role="button"], [role="treeitem"], input, select, textarea';
  const fit = await textFit(p(), T('di-review-dialog'), CONTROLS);
  const hunks = await p().locator(T('di-review-hunk')).count();
  console.log(
    `[reflow320] ${state}: dialog ${report.box.left}..${report.box.right} of ${report.window.width}, page sideways scroll ${report.pageHorizontalScrollPx} px, ` +
      `${fit.controls.length} controls measured (${hunks} sticky section headers not measurable by the helper), ` +
      `reading and control containers scrolling or clipping sideways: ${JSON.stringify(report.containers.filter((c) => c.area === 'reading and control'))}, ` +
      `code and diff containers: ${JSON.stringify(report.containers.filter((c) => c.area === 'code and diff'))}, reading text past the dialog or window: ${JSON.stringify(report.textOut)}`,
  );
  return { report, fit, hunks };
}

const TREE_ROWS = ['di-review-row', 'di-review-folder'];
function assertDialogAt320(state: string, m: Dialog320, window320 = REFLOW_320.width) {
  const { report, fit } = m;
  assert.ok(report.box.left >= 0 && report.box.right <= window320 && report.box.width <= window320, `${state}: the dialog box (${report.box.left}..${report.box.right}) is inside 0..${window320}`);
  assert.equal(report.pageHorizontalScrollPx, 0, `${state}: the page does not scroll sideways (px)`);
  assert.deepEqual(report.containers.filter((c) => c.area === 'reading and control'), [], `${state}: no reading or control container scrolls or clips sideways (id, px)`);
  assert.deepEqual(report.textOut, [], `${state}: no reading or control text is drawn past the dialog or off the window`);
  assert.ok(fit.controls.length > 0, `${state}: controls were measured`);
  for (const c of fit.controls) {
    assert.equal(c.clipsContent, false, `${state}: ${c.id}: the control clips its own content`);
    assert.deepEqual(
      c.outside.filter((o) => o.left > 0 || o.right > 0),
      [],
      `${state}: ${c.id}: rendered text outside the control's visible region, sideways (px)`,
    );
    assert.deepEqual([c.controlOffWindow.left, c.controlOffWindow.right], [0, 0], `${state}: ${c.id}: the control is off the window sideways (px left, right)`);
    if (TREE_ROWS.includes(c.id)) continue; // file rows below the fold are scrolled out of view VERTICALLY by design: reflow is about the sideways axis
    assert.deepEqual(c.outside, [], `${state}: ${c.id}: rendered text outside the control's visible region (px per side)`);
    assert.deepEqual(c.textNotHit, [], `${state}: ${c.id}: text whose centre is not painted by the control`);
    assert.deepEqual(c.controlOffWindow, { left: 0, right: 0, top: 0, bottom: 0 }, `${state}: ${c.id}: the control is inside the window`);
    assert.equal(c.hitsItself, true, `${state}: ${c.id}: a click at its centre reaches it`);
  }
}

test(
  'AC-UX-13 / SC 1.4.10, review dialog at 320, SOURCE-MOUNTED SEAM (the real App mounted from source over the test WebSocket office, not the production bundle)',
  async () => {
    await freshPageAt(REFLOW_320);
    p().setDefaultTimeout(15_000);
    await selectAgent();
    await p().click(T('di-open-code-review'));
    await p().waitForSelector(T('di-review-dialog'));
    await p().waitForSelector(T('di-review-tree'), { timeout: 20_000 });
    await p().waitForTimeout(1200); // settled

    // A. the Files pane: header, overview with its details open, search, the file tree
    await p().click(T('di-review-details'));
    const a = await measureDialogAt320('A files pane, details open');
    assertDialogAt320('A files pane, details open', a);
    assert.ok(a.fit.controls.some((c) => c.id === 'di-review-search'), 'A: the search box is measured');
    assert.ok(a.fit.controls.filter((c) => c.id === 'di-review-row').length >= 20, 'A: the file rows are measured');
    await p().click(T('di-review-details'));

    // B. the Diff pane of a two-section file; Minimize and Close are reachable by keyboard as well
    await openFile('src/syntax_sample.ts');
    await p().waitForSelector(T('di-review-hunk'));
    const b = await measureDialogAt320('B diff pane, two sections (src/syntax_sample.ts)');
    assertDialogAt320('B diff pane, two sections', b);
    for (const id of ['di-review-minimize', 'di-review-close']) {
      assert.ok(b.fit.controls.some((c) => c.id === id), `B: ${id} is measured`);
      await p().focus(T(id));
      assert.equal(await activeInfo(), `button[${id}]`, `B: ${id} takes keyboard focus`);
    }
    // The code and diff area scrolls sideways by the nature of its content: it is a real scroll region a person can use.
    const codeB = b.report.containers.filter((c) => c.area === 'code and diff' && c.id === 'di-review-code');
    assert.equal(codeB.length, 1, 'B: the code region is the one code container that scrolls sideways');
    assert.equal(codeB[0].overflowX, 'auto', 'B: it scrolls (it is not clipped)');
    assert.ok(codeB[0].excessPx > 0, 'B: its content is wider than it');
    const scrolled = await p().$eval(T('di-review-code'), (el) => {
      (el as HTMLElement).scrollLeft = 100;
      return (el as HTMLElement).scrollLeft;
    });
    assert.equal(scrolled, 100, 'B: the code region really scrolls sideways');
    // FINDING (reported, not asserted): in the diff area the sticky section headers show the repository's section name through
    // `overflow: hidden; text-overflow: ellipsis`, so at 320 the name is cut off with no other way to read it.
    const clippedNames = b.report.containers.filter((c) => c.id === 'span.di-rv-hunk-section');
    console.log(`[reflow320] FINDING (not asserted): ${clippedNames.length} section-header names are clipped sideways at 320: ${JSON.stringify(clippedNames)}`);

    // C. the Diff pane of a file with one very long source line
    await p().click(T('di-review-tab-files'));
    await openFile('web/long_line.js');
    const c3 = await measureDialogAt320('C diff pane, a very long source line (web/long_line.js)');
    assertDialogAt320('C diff pane, a long source line', c3);
    const codeC = c3.report.containers.filter((c) => c.area === 'code and diff' && c.id === 'di-review-code');
    assert.equal(codeC.length, 1);
    assert.ok(codeC[0].excessPx > 1000, `C: a long source line is as long as it is, in a region that scrolls (${codeC[0].excessPx} px more than its width)`);

    // the section names against a wide window, for the record (not asserted)
    await p().click(T('di-review-tab-files'));
    await openFile('src/syntax_sample.ts');
    const hunkNames = () =>
      p().$$eval('.di-rv-hunk-section', (els) => els.map((e) => ({ scrollWidth: (e as HTMLElement).scrollWidth, clientWidth: (e as HTMLElement).clientWidth })));
    const names320 = await hunkNames();
    await p().setViewportSize({ width: 1600, height: 1000 });
    await frames(3);
    const names1600 = await hunkNames();
    await p().setViewportSize({ width: REFLOW_320.width, height: REFLOW_320.height });
    await frames(3);
    console.log(`[reflow320] section-header names (scrollWidth, clientWidth): at 320 ${JSON.stringify(names320)}; at 1600 x 1000 ${JSON.stringify(names1600)}`);

    // E. minimized: the review chip alone in the strip
    await p().click(T('di-review-minimize'));
    await p().waitForSelector(T('di-review-restore'));
    await p().waitForTimeout(1200);
    const strip = async (state: string, expected: string[]) => {
      const fit = await textFit(p(), T('di-minimized-tray'));
      const tray = await p().evaluate(() => {
        const r = document.querySelector<HTMLElement>('[data-testid="di-minimized-tray"]')!.getBoundingClientRect();
        return { left: r.left, right: r.right, top: r.top, bottom: r.bottom, scrollPx: Math.max(document.documentElement.scrollWidth - window.innerWidth, 0) };
      });
      console.log(`[reflow320] ${state}: tray ${Math.round(tray.left)}..${Math.round(tray.right)} of ${REFLOW_320.width}, chips ${JSON.stringify(fit.controls.map((c) => `${c.id} ${Math.round(c.box.left)}..${Math.round(c.box.right)} x ${Math.round(c.box.top)}..${Math.round(c.box.bottom)}`))}`);
      assert.ok(tray.left >= 0 && tray.right <= REFLOW_320.width, `${state}: the strip (${tray.left}..${tray.right}) is inside the window`);
      assert.equal(tray.scrollPx, 0, `${state}: the page does not scroll sideways (px)`);
      assert.deepEqual(fit.controls.map((c) => c.id).sort(), [...expected].sort(), `${state}: the strip holds exactly these chips`);
      for (const c of fit.controls) assertChipGeometry(state, c); // the strip contract: outside, textNotHit, clips, writing mode, window, hit, full label
      return fit;
    };
    await strip('E strip at 320, review chip alone', ['di-review-restore']);
    await shot('reflow320-review-chip-alone');

    // F. Reception minimized beside it: two stacked slots in the window
    await p().click(T('di-open-reception'));
    await p().waitForSelector(T('di-reception-inbox'));
    await p().click(T('di-reception-minimize'));
    await p().waitForSelector(T('di-reception-restore'));
    await p().waitForTimeout(1200);
    const f = await strip('F strip at 320, review chip and Reception chip together', ['di-review-restore', 'di-reception-restore']);
    const [top, bottom] = [...f.controls].sort((x, y) => x.box.top - y.box.top);
    assert.ok(top.box.bottom <= bottom.box.top + 0.75, `F: the two chips are stacked (${top.id} ends ${top.box.bottom}, ${bottom.id} starts ${bottom.box.top}), not overlapping`);
    await shot('reflow320-two-chips');

    // D. restored, then the cleared notice (reading content only): Minimize, Close and the notice's own Close are reachable
    await p().click(T('di-review-restore'));
    await p().waitForSelector(T('di-review-restore'), { state: 'detached' });
    send(S.agentGone);
    await p().waitForSelector(T('di-review-cleared'), { timeout: 15_000 });
    const d = await measureDialogAt320('D cleared notice');
    assertDialogAt320('D cleared notice', d);
    for (const id of ['di-review-minimize', 'di-review-close', 'di-review-cleared-close'])
      assert.ok(d.fit.controls.some((c) => c.id === id), `D: ${id} is measured and reachable`);
  },
  STEPS_MS,
);

/*
 * A diff section's NAME (the repository's own context text beside "@@ -a,b +c,d @@") is the only readable identifier of that section. It was
 * cut off by an ellipsis at 320 with no other way to read it (measured: 81 px visible of 266 and 344). At narrow widths it now wraps onto
 * rows of its own under the section bar, every character of it, while a wide window keeps the single-line bar. SOURCE-MOUNTED SEAM, NOT THE
 * PRODUCTION BUNDLE: direct measurements, because the bounded helper refuses position: sticky.
 */
const sectionBars = () =>
  p().evaluate(() => {
    const code = document.querySelector<HTMLElement>('[data-testid="di-review-code"]')!;
    const region = code.getBoundingClientRect();
    return {
      region: { left: Math.round(region.left * 10) / 10, right: Math.round(region.right * 10) / 10, clientWidth: code.clientWidth },
      rows: code.getAttribute('data-rows'),
      bars: [...code.querySelectorAll<HTMLElement>('[data-testid="di-review-hunk"]')].map((btn) => {
        const index = btn.getAttribute('data-hunk');
        const span = btn.querySelector<HTMLElement>('.di-rv-hunk-section');
        const bar = btn.getBoundingClientRect();
        const rects = (el: Element) => {
          const out: Array<{ left: number; right: number; top: number; bottom: number }> = [];
          const walker = document.createTreeWalker(el, NodeFilter.SHOW_TEXT);
          for (let n = walker.nextNode(); n; n = walker.nextNode()) {
            const range = document.createRange();
            range.selectNodeContents(n);
            for (const r of Array.from(range.getClientRects())) if (r.width > 0) out.push({ left: r.left, right: r.right, top: r.top, bottom: r.bottom });
          }
          return out;
        };
        const nameRows = [...code.querySelectorAll<HTMLElement>(`[data-testid="di-review-hunk-name"][data-hunk="${index}"]`)];
        return {
          hunk: index,
          fullText: span?.textContent ?? '',
          barHeight: Math.round(bar.height * 10) / 10,
          spanScrollWidth: span?.scrollWidth ?? 0,
          spanClientWidth: span?.clientWidth ?? 0,
          spanTextOverflow: span ? getComputedStyle(span).textOverflow : '',
          spanWidth: span ? Math.round(span.getBoundingClientRect().width * 10) / 10 : 0,
          nameRows: nameRows.map((row) => ({
            text: row.textContent ?? '',
            ariaHidden: row.getAttribute('aria-hidden'),
            shown: row.checkVisibility({ checkVisibilityCSS: true }),
            scrollWidth: row.scrollWidth,
            clientWidth: row.clientWidth,
            textRects: rects(row),
          })),
        };
      }),
    };
  });

test(
  'AC-UX-13 / SC 1.4.10, diff section names at 320 are READABLE IN FULL and the collapse control works by keyboard; a wide window keeps the single-line bar (SOURCE-MOUNTED SEAM, not the production bundle)',
  async () => {
    await freshPageAt({ width: 1600, height: 1000, scale: 1 });
    p().setDefaultTimeout(15_000);
    await selectAgent();
    await p().click(T('di-open-code-review'));
    await p().waitForSelector(T('di-review-dialog'));
    await p().waitForSelector(T('di-review-tree'), { timeout: 20_000 });
    await openFile('src/syntax_sample.ts');
    await p().waitForSelector(T('di-review-hunk'));
    await p().waitForTimeout(1200); // settled

    // 0. a wide window FIRST: the single-line bar with the name on it, no rows of its own (the desktop baseline this correction must not change)
    const wide = await sectionBars();
    // measured on the unchanged product: 30 rows, each bar 22 px (one row) with its name on it, 287 and 371 px wide, fully shown
    assert.equal(wide.rows, '30', '1600: the diff has 30 rows (no rows added for names)');
    assert.deepEqual(
      wide.bars.map((b) => [b.hunk, b.barHeight, b.nameRows.length, b.spanScrollWidth === b.spanClientWidth, b.spanWidth > 200]),
      [['0', 22, 0, true, true], ['1', 22, 0, true, true]],
      '1600: the bars are one row high, carry the name themselves (it fits) and have no name rows of their own',
    );
    const nameWidths = wide.bars.map((b) => b.spanClientWidth);
    assert.ok(
      nameWidths.length === 2 && nameWidths.every((w, i) => Math.abs(w - [287, 371][i]) <= 1),
      `1600: the names are drawn at their full widths (px): ${nameWidths.join(', ')} (287 and 371, 1 px of font rounding)`,
    );
    await p().setViewportSize({ width: 320, height: 568 });
    await frames(3);
    await p().waitForTimeout(1200);

    // 1. at 320, section by section (each brought to the top of the code region by the review's own "Next section" control)
    const expectedNames = ["import { readFileSync } from 'fs';", 'export function load(path: string): Config {'];
    const checkNames = async (label: string, barHeight: number | null) => {
      await p().click(T('di-review-tab-files'));
      await openFile('src/syntax_sample.ts');
      for (let k = 0; k < expectedNames.length; k += 1) {
        if (k > 0) await p().click(T('di-review-next-section'));
        await p().waitForTimeout(150);
        const m = await sectionBars();
        const region = m.region;
        const bar = m.bars.find((b) => b.hunk === String(k));
        assert.ok(bar, `${label}: section ${k} is drawn once it is scrolled to`);
        const at = `${label}: section ${k}`;
        assert.equal(bar.fullText, expectedNames[k], `${at}: the section's own name is the one in the diff`);
        // the name, every character of it, is VISIBLE: its rows carry exactly that text, are shown, are not clipped, and sit inside the region
        assert.ok(bar.nameRows.length >= 1, `${at}: the name is drawn on rows of its own (${bar.nameRows.length} rows)`);
        // (a line break stands in for the blank it wraps at, so the comparison ignores blanks: no other character may be missing or added)
        const unblanked = (t: string) => t.replace(/\s+/g, '');
        assert.equal(unblanked(bar.nameRows.map((r) => r.text).join('')), unblanked(expectedNames[k]), `${at}: the rows together carry the whole name, nothing dropped`);
        for (const row of bar.nameRows) {
          assert.equal(row.shown, true, `${at}: a name row is shown`);
          assert.equal(row.ariaHidden, 'true', `${at}: the visible copy is hidden from assistive technology (the button's own name already carries the text)`);
          assert.ok(row.scrollWidth <= row.clientWidth, `${at}: a name row is not clipped (${row.scrollWidth} of ${row.clientWidth} px)`);
          for (const r of row.textRects)
            assert.ok(r.left >= region.left - 0.75 && r.right <= region.right + 0.75, `${at}: name text ${Math.round(r.left)}..${Math.round(r.right)} lies inside the code region ${region.left}..${region.right}`);
        }
        // and the bar itself fits its row (a narrow-only rule had made its button 30 px tall in a 22 px row, over its neighbours)
        if (barHeight !== null) assert.equal(bar.barHeight, barHeight, `${at}: the section bar is one row high, as it is in a wide window (px)`);
        console.log(`[sectionnames] ${at}: ${bar.nameRows.length} name rows ${JSON.stringify(bar.nameRows.map((r) => `${r.text} (${r.scrollWidth}/${r.clientWidth})`))}, bar ${bar.barHeight} px`);
      }
    };
    await checkNames('320', 22);

    // 2. the existing collapse control still works by REAL keyboard: Tab to it, Enter collapses, Space expands
    await p().click(T('di-review-tab-files'));
    await openFile('src/syntax_sample.ts');
    await p().focus(T('di-review-tab-diff'));
    let reached = false;
    for (let presses = 0; presses < 40 && !reached; presses += 1) {
      await p().keyboard.press('Tab');
      reached = (await activeInfo()) === 'button[di-review-hunk]';
    }
    assert.ok(reached, '320: Tab reaches a section button');
    const rowsOpen = await p().$eval(T('di-review-code'), (el) => Number(el.getAttribute('data-rows')));
    await p().keyboard.press('Enter');
    assert.equal(await p().getAttribute(`${T('di-review-hunk')}:focus`, 'aria-expanded'), 'false', '320: Enter collapses the section');
    const rowsCollapsed = await p().$eval(T('di-review-code'), (el) => Number(el.getAttribute('data-rows')));
    assert.ok(rowsCollapsed < rowsOpen, `320: collapsing removes the section's lines (${rowsOpen} to ${rowsCollapsed} rows)`);
    await p().keyboard.press('Space');
    assert.equal(await p().getAttribute(`${T('di-review-hunk')}:focus`, 'aria-expanded'), 'true', '320: Space expands it again');
    assert.equal(await p().$eval(T('di-review-code'), (el) => Number(el.getAttribute('data-rows'))), rowsOpen, '320: the rows are back as they were');
    // the accessible name still carries the section's own text (assistive technology reads it once, from the button)
    assert.match(await p().locator(T('di-review-hunk')).first().ariaSnapshot(), /import \{ readFileSync \} from ''fs''/, "320: the button's accessible name carries the section name");

    // 3. the largest-but-one text sizes wrap the same names onto more rows and still never clip them (text size 20 px: three steps up)
    for (let i = 0; i < 3; i += 1) await p().click(T('di-review-size-up'));
    assert.equal(await text(T('di-review-size')), '20px');
    await checkNames('320 at text size 20px', null);

  },
  STEPS_MS,
);

/*
 * AC-UX-13 usability (NOT a WCAG height threshold: no such threshold exists): at a phone's height the fixed chrome above the reading area
 * (title, overview, comparison notice, tabs, the file's own bar) is about 500 px, and the code region used to be left with 12 px of visible code,
 * no whole row. The bar this holds the review to, at 320 x 568: the dialog scrolls as a WHOLE under its title bar, the code region keeps at least
 * 8 whole rows (the first section's bar and its name rows among them) and a person gets to them with the keyboard alone, while every piece of
 * metadata, every notice and every control stays in reach with a visible focus ring. Roomy and wide windows are exactly what they were.
 * SOURCE-MOUNTED SEAM, NOT THE PRODUCTION BUNDLE: direct measurements (the bounded helper refuses position: sticky).
 */
const readingLayout = () =>
  p().evaluate(() => {
    const q = (sel: string) => document.querySelector<HTMLElement>(sel);
    const f = (n: number) => Math.round(n * 10) / 10;
    const R = (el: Element | null) => {
      if (!el) return null;
      const r = el.getBoundingClientRect();
      return { top: f(r.top), bottom: f(r.bottom), left: f(r.left), right: f(r.right), height: f(r.height), width: f(r.width) };
    };
    const dialog = q('[data-testid="di-review-dialog"]')!;
    // the part that scrolls as a whole: the wrapper under the title bar in a narrow window, the dialog itself (which clips) otherwise
    const wrap = q('.di-rv-scroll');
    const scroller = wrap && getComputedStyle(wrap).display !== 'contents' ? wrap : dialog;
    const code = q('[data-testid="di-review-code"]');
    const tree = q('[data-testid="di-review-tree"]');
    const win = window.innerHeight;
    const titleBottom = q('.di-rv-top')?.getBoundingClientRect().bottom ?? 0;
    const inside = (r: DOMRect, box: { top: number; bottom: number }) => r.top >= Math.max(box.top, titleBottom) - 0.5 && r.bottom <= Math.min(box.bottom, win) + 0.5;
    let codeInfo = null;
    if (code) {
      const box = code.getBoundingClientRect();
      const client = { top: box.top, bottom: box.top + code.clientHeight };
      const rows = [...code.querySelectorAll('.di-rv-line, .di-rv-hunk-button, .di-rv-hunk-name, .di-rv-note-row')];
      const whole = rows.filter((el) => inside(el.getBoundingClientRect(), client));
      const bar0 = code.querySelector('[data-testid="di-review-hunk"][data-hunk="0"]');
      const names0 = [...code.querySelectorAll('[data-testid="di-review-hunk-name"][data-hunk="0"]')];
      codeInfo = {
        rect: R(code),
        clientHeight: code.clientHeight,
        rowPx: parseFloat(getComputedStyle(code).getPropertyValue('--rv-row')),
        wholeRows: whole.length,
        firstSectionWhole: bar0 !== null && inside(bar0.getBoundingClientRect(), client) && names0.every((n) => inside(n.getBoundingClientRect(), client)),
        firstSectionNameRows: names0.length,
      };
    }
    let treeInfo = null;
    if (tree) {
      const box = tree.getBoundingClientRect();
      const client = { top: box.top, bottom: box.top + tree.clientHeight };
      treeInfo = {
        rect: R(tree),
        clientHeight: tree.clientHeight,
        wholeRows: [...tree.querySelectorAll('[role="treeitem"]')].filter((el) => inside(el.getBoundingClientRect(), client)).length,
      };
    }
    return {
      window: { w: window.innerWidth, h: win },
      dialog: { height: f(dialog.getBoundingClientRect().height) },
      scroller: { overflowY: getComputedStyle(scroller).overflowY, scrollTop: scroller.scrollTop, scrollHeight: scroller.scrollHeight, clientHeight: scroller.clientHeight },
      top: R(q('.di-rv-top')),
      overview: R(q('.di-rv-overview')),
      body: R(q('.di-rv-body')),
      diffHead: R(q('.di-rv-diff-head')),
      treeTools: R(q('.di-rv-tree-tools')),
      hints: R(q('[data-testid="di-review-hints"]')),
      code: codeInfo,
      tree: treeInfo,
    };
  });

/** What a control shows when it is focused by keyboard, and where it is. */
const focusedControl = () =>
  p().evaluate(() => {
    const el = document.activeElement as HTMLElement;
    const probe = document.createElement('div');
    probe.style.color = 'var(--di-focus)';
    document.body.append(probe);
    const focusColour = getComputedStyle(probe).color;
    probe.remove();
    const cs = getComputedStyle(el);
    const r = el.getBoundingClientRect();
    const titleBottom = document.querySelector('.di-rv-top')?.getBoundingClientRect().bottom ?? 0;
    return {
      id: el.getAttribute('data-testid'),
      focusVisible: el.matches(':focus-visible'),
      outlineStyle: cs.outlineStyle,
      outlineWidth: cs.outlineWidth,
      outlineColourIsTheSharedFocus: cs.outlineColor === focusColour,
      rect: { top: Math.round(r.top * 10) / 10, bottom: Math.round(r.bottom * 10) / 10 },
      titleBottom: Math.round(titleBottom * 10) / 10,
      windowHeight: window.innerHeight,
      inTitleBar: el.closest('.di-rv-top') !== null,
    };
  });

test(
  'AC-UX-13 usability, review reading area at 320 x 568: the dialog scrolls as a whole, the code keeps 8 whole rows, every control stays in reach by keyboard (SOURCE-MOUNTED SEAM, not the production bundle)',
  async () => {
    await freshPageAt({ width: 320, height: 568, scale: 1 });
    p().setDefaultTimeout(15_000);
    await selectAgent();
    await p().click(T('di-open-code-review'));
    await p().waitForSelector(T('di-review-tree'), { timeout: 20_000 });
    await openFile('src/syntax_sample.ts');
    await p().waitForSelector(T('di-review-hunk'));
    await p().waitForTimeout(1200); // settled

    // 1. at rest: the chrome scrolls with the dialog, and the code region has room for 8 whole rows of its own
    const rest = await readingLayout();
    console.log(`[reading320] at rest: dialog ${rest.dialog.height} px, scrolls ${rest.scroller.scrollHeight} of ${rest.scroller.clientHeight} (${rest.scroller.overflowY}); code region ${rest.code?.rect?.height} px, ${rest.code?.clientHeight} px of visible code, ${rest.code?.wholeRows} whole rows in the window at rest`);
    assert.equal(rest.scroller.overflowY, 'auto', 'everything under the title bar scrolls as a whole in a narrow window');
    assert.ok(rest.scroller.scrollHeight > rest.scroller.clientHeight, `the chrome and the reading area together are taller than the window (${rest.scroller.scrollHeight} of ${rest.scroller.clientHeight} px)`);
    assert.ok(rest.code && rest.code.clientHeight >= 8 * rest.code.rowPx, `the code region keeps room for 8 whole rows (${rest.code?.clientHeight} px of visible code, a row is ${rest.code?.rowPx} px)`);

    // 2. by REAL keyboard: Tab from the Diff tab to the code region; the browser scrolls the dialog, not the page
    await p().focus(T('di-review-tab-diff'));
    let reached = false;
    for (let presses = 0; presses < 20 && !reached; presses += 1) {
      await p().keyboard.press('Tab');
      reached = (await activeInfo()) === 'div[di-review-code]';
    }
    assert.ok(reached, 'Tab reaches the code region');
    await p().waitForTimeout(300);
    const reading = await readingLayout();
    console.log(`[reading320] after Tab to the code region: dialog scrollTop ${reading.scroller.scrollTop}; code region ${reading.code?.rect?.top}..${reading.code?.rect?.bottom} of a ${reading.window.h} px window under a title bar ending at ${reading.top?.bottom}; ${reading.code?.wholeRows} whole rows visible; first section bar and ${reading.code?.firstSectionNameRows} name rows whole: ${reading.code?.firstSectionWhole}`);
    assert.ok(reading.scroller.scrollTop > 0, 'the dialog scrolled to bring the code region into view');
    assert.ok(reading.code && reading.code.wholeRows >= 8, `at least 8 whole code rows are visible at once (${reading.code?.wholeRows})`);
    assert.equal(reading.code?.firstSectionWhole, true, "the first section's bar and its name rows are among them, whole and not under the title bar");
    assert.ok((reading.code?.firstSectionNameRows ?? 0) >= 1);
    assert.ok(reading.code!.rect!.top >= reading.top!.bottom - 0.5 && reading.code!.rect!.bottom <= reading.window.h + 0.5, 'the code region lies between the title bar and the bottom of the window');

    // 3. metadata, notices and controls stay in reach: at the top of the dialog they are where they were, each reached by Tab with the shared focus ring
    await p().$eval('.di-rv-scroll', (el) => {
      el.scrollTop = 0;
    });
    await p().waitForTimeout(150);
    const wanted = ['di-review-refresh', 'di-review-minimize', 'di-review-close', 'di-review-details', 'di-review-tab-files', 'di-review-tab-diff'];
    await p().focus(T('di-review-refresh'));
    const seen = new Map<string, Awaited<ReturnType<typeof focusedControl>>>();
    for (let presses = 0; presses < 12 && seen.size < wanted.length; presses += 1) {
      const c = await focusedControl();
      if (c.id !== null && wanted.includes(c.id)) seen.set(c.id, c);
      await p().keyboard.press('Tab');
    }
    assert.deepEqual([...seen.keys()], wanted, 'Tab reaches Refresh, Minimize, Close, the details toggle and both tabs, in that order');
    for (const [id, c] of seen) {
      assert.equal(c.focusVisible, true, `${id}: keyboard focus is focus-visible`);
      assert.ok(c.outlineStyle !== 'none' && parseFloat(c.outlineWidth) >= 3, `${id}: a visible focus ring (${c.outlineStyle} ${c.outlineWidth})`);
      assert.equal(c.outlineColourIsTheSharedFocus, true, `${id}: the ring is the shared focus colour`);
      assert.ok(c.rect.top >= (c.inTitleBar ? 0 : c.titleBottom) - 0.5 && c.rect.bottom <= c.windowHeight + 0.5, `${id}: it is in view (${c.rect.top}..${c.rect.bottom}, title bar ends ${c.titleBottom}, window ${c.windowHeight})`);
    }
    // the overview, the comparison notice and the details disclosure are all there, and the disclosure opens
    for (const id of ['di-review-overview', 'di-review-compare-line', 'di-review-details']) {
      const hit = await p().$eval(T(id), (el) => {
        const r = el.getBoundingClientRect();
        const at = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2);
        return { inWindow: r.top >= 0 && r.bottom <= window.innerHeight && r.left >= 0 && r.right <= window.innerWidth, hit: at !== null && (el === at || el.contains(at)) };
      });
      assert.deepEqual(hit, { inWindow: true, hit: true }, `${id} is in the window at the top of the dialog and not covered`);
    }
    await p().click(T('di-review-details'));
    assert.equal(await p().getAttribute(T('di-review-details'), 'aria-expanded'), 'true');
    await p().waitForSelector(T('di-review-details-body'));
    await p().click(T('di-review-details'));
  },
  STEPS_MS,
);

test(
  'AC-UX-13 usability, the reading areas of a roomy phone, the Files pane and a wide window are not squeezed or moved by the narrow layout (SOURCE-MOUNTED SEAM, not the production bundle)',
  async () => {
    const near = (actual: number | undefined, expected: number, what: string) =>
      assert.ok(actual !== undefined && Math.abs(actual - expected) <= 1, `${what}: ${actual} px, expected ${expected} px`);
    // Measured on the product before either change (viewport-BASELINE-metrics log) and after both (viewport-WIP2 log). The narrow-layout rules alone
    // moved nothing at any of these sizes (viewport-WIP1 log). The chrome controls' 1 px borders (AC1) make a content-sized button 1.75 px shorter
    // (33.75 px to the 32 px minimum) and the search field 1.8 px: so the overview is 85.5 -> 83.8 px, the search tools 78.6 -> 76.8 px, and the
    // code region and the file tree of a wide window take that space (669.7 -> 673.2 px and 702 -> 705.5 px).
    const open = async (size: { width: number; height: number }, file: boolean) => {
      await freshPageAt({ ...size, scale: 1 });
      p().setDefaultTimeout(15_000);
      await selectAgent();
      await p().click(T('di-open-code-review'));
      await p().waitForSelector(T('di-review-tree'), { timeout: 20_000 });
      await p().waitForTimeout(800);
      if (file) {
        await openFile('src/syntax_sample.ts');
        await p().waitForSelector(T('di-review-hunk'));
        await p().waitForTimeout(800);
      }
      return readingLayout();
    };
    // 1600 x 1000, the diff
    const wide = await open({ width: 1600, height: 1000 }, true);
    assert.equal(wide.scroller.overflowY, 'hidden', '1600: the dialog does not scroll as a whole');
    near(wide.dialog.height, 956, '1600: the dialog (was 956)');
    near(wide.top?.height, 56.5, '1600: the title bar (was 56.5)');
    near(wide.overview?.height, 83.8, '1600: the overview (was 85.5)');
    near(wide.body?.height, 782.4, '1600: the body (was 780.6)');
    near(wide.diffHead?.height, 76.3, '1600: the file bar (was 78)');
    near(wide.treeTools?.height, 76.8, '1600: the search tools (was 78.6)');
    near(wide.hints?.height, 31.4, '1600: the key hints (was 31.4)');
    near(wide.code?.rect?.height, 673.2, '1600: the code region (was 669.7)');
    assert.equal(wide.code?.clientHeight, 673, '1600: the visible code (was 670)');
    assert.equal(wide.code?.wholeRows, 30, '1600: the whole rows visible (was 30)');
    near(wide.tree?.rect?.height, 705.5, '1600: the file tree (was 702)');
    assert.equal(wide.tree?.wholeRows, 23, '1600: the whole file rows visible (was 23)');
    // 390 x 844, a roomy phone: the regions fill the window as before and the dialog does not scroll
    const phone = await open({ width: 390, height: 844 }, true);
    assert.equal(phone.scroller.scrollHeight, phone.scroller.clientHeight, '390 x 844: the dialog does not scroll');
    near(phone.code?.rect?.height, 358.2, '390 x 844: the code region (was 358.2)');
    assert.equal(phone.code?.wholeRows, 15, '390 x 844: the whole code rows visible (was 15)');
    await p().click(T('di-review-tab-files'));
    const phoneFiles = await readingLayout();
    near(phoneFiles.tree?.rect?.height, 478.8, '390 x 844: the file tree (was 477)');
    assert.equal(phoneFiles.tree?.wholeRows, 15, '390 x 844: the whole file rows visible (was 15)');
    // 320 x 568, the Files pane: 5 whole file rows and no scroll of the dialog, as before
    const files = await open({ width: 320, height: 568 }, false);
    assert.equal(files.scroller.scrollHeight, files.scroller.clientHeight, '320 x 568 Files pane: the dialog does not scroll');
    near(files.tree?.rect?.height, 165.2, '320 x 568 Files pane: the file tree (was 163.4)');
    assert.equal(files.tree?.wholeRows, 5, '320 x 568 Files pane: the whole file rows visible (was 5)');
  },
  STEPS_MS * 2,
);

/*
 * AC1 / brief 2.1: the review's chrome controls (buttons, toggles, tabs, the search field) are the shared control, not a pixel-style one: an 8 px
 * radius, a 1 px token border, and token colours at rest, on hover, when pressed and when expanded. COMPUTED in the browser, compared with what
 * the SHARED tokens resolve to (a probe element that uses var(--di-...) itself, so no colour literal is needed here). The code rows keep their
 * own geometry: the section bars and the file rows stay square. SOURCE-MOUNTED SEAM, NOT THE PRODUCTION BUNDLE.
 */
const controlLook = (testid: string) =>
  p().evaluate((id) => {
    const el = document.querySelector<HTMLElement>(`[data-testid="${id}"]`)!;
    const cs = getComputedStyle(el);
    const resolve = (property: 'background-color' | 'color', value: string) => {
      const probe = document.createElement('div');
      probe.style.setProperty(property === 'background-color' ? 'background' : 'color', value);
      document.body.append(probe);
      const out = getComputedStyle(probe).getPropertyValue(property);
      probe.remove();
      return out;
    };
    const tokens = {
      control: resolve('background-color', 'var(--di-control)'),
      controlHover: resolve('background-color', 'var(--di-control-hover)'),
      selected: resolve('background-color', 'var(--di-selected)'),
      bg: resolve('background-color', 'var(--di-bg)'),
      edge: resolve('color', 'var(--di-edge)'),
      muted: resolve('color', 'var(--di-muted)'),
      link: resolve('color', 'var(--di-link)'),
      focus: resolve('color', 'var(--di-focus)'),
      text: resolve('color', 'var(--di-text)'),
    };
    return {
      radius: [cs.borderTopLeftRadius, cs.borderTopRightRadius, cs.borderBottomRightRadius, cs.borderBottomLeftRadius],
      borderWidths: [cs.borderTopWidth, cs.borderRightWidth, cs.borderBottomWidth, cs.borderLeftWidth],
      borderStyle: cs.borderTopStyle,
      borderColor: cs.borderTopColor,
      leftBorderColor: cs.borderLeftColor,
      background: cs.backgroundColor,
      color: cs.color,
      tokens,
    };
  }, testid);

test(
  'AC1 review chrome controls, COMPUTED: 8 px radius, a 1 px token border and token colours at rest, hover, pressed and expanded; the code rows keep their geometry (SOURCE-MOUNTED SEAM, not the production bundle)',
  async () => {
    await freshPageAt({ width: 1600, height: 1000, scale: 1 });
    p().setDefaultTimeout(15_000);
    await selectAgent();
    await p().click(T('di-open-code-review'));
    await p().waitForSelector(T('di-review-tree'), { timeout: 20_000 });
    await openFile('src/syntax_sample.ts');
    await p().waitForSelector(T('di-review-hunk'));
    await p().waitForTimeout(1000);
    const radius8 = ['8px', '8px', '8px', '8px'];
    const width1 = ['1px', '1px', '1px', '1px'];
    const log = (state: string, id: string, c: Awaited<ReturnType<typeof controlLook>>) =>
      console.log(`[chrome] ${state} ${id}: radius ${c.radius[0]}, border ${c.borderWidths[0]} ${c.borderStyle} ${c.borderColor}, background ${c.background}`);

    // at rest: the shared control look
    for (const id of ['di-review-refresh', 'di-review-minimize', 'di-review-close', 'di-review-details']) {
      const c = await controlLook(id);
      log('rest', id, c);
      assert.deepEqual(c.radius, radius8, `${id}: an 8 px radius`);
      assert.deepEqual(c.borderWidths, width1, `${id}: a 1 px border on every side (no 2 px pixel border)`);
      assert.equal(c.borderStyle, 'solid', `${id}: a solid border`);
      assert.equal(c.borderColor, c.tokens.edge, `${id}: the border is the shared edge colour`);
      assert.equal(c.background, c.tokens.control, `${id}: the background is the shared control colour`);
      assert.equal(c.color, c.tokens.text, `${id}: the text is the shared text colour`);
    }
    // a joined group (layout, text size, sections) is rounded only at its two outer ends: first segment [TL, TR, BR, BL] = 8 0 0 8, last = 0 8 8 0, a middle one square
    const outer: Array<[string, string[]]> = [
      ['di-review-layout-unified', ['8px', '0px', '0px', '8px']],
      ['di-review-layout-split', ['0px', '8px', '8px', '0px']],
      ['di-review-size-down', ['8px', '0px', '0px', '8px']],
      ['di-review-size-up', ['0px', '8px', '8px', '0px']],
      ['di-review-prev-section', ['8px', '0px', '0px', '8px']],
      ['di-review-next-section', ['0px', '0px', '0px', '0px']],
      ['di-review-expand-all', ['0px', '8px', '8px', '0px']],
    ];
    for (const [id, expected] of outer) {
      const c = await controlLook(id);
      assert.deepEqual(c.radius, expected, `${id}: rounded at the group's outer ends only`);
      assert.deepEqual(c.borderWidths, width1, `${id}: a 1 px border`);
      assert.equal(c.borderColor === c.tokens.edge || c.borderColor === c.tokens.link, true, `${id}: a shared border colour`);
    }
    // the search field
    const search = await controlLook('di-review-search');
    log('rest', 'di-review-search', search);
    assert.deepEqual(search.radius, radius8, 'the search field: an 8 px radius');
    assert.deepEqual(search.borderWidths, width1, 'the search field: a 1 px border');
    assert.equal(search.borderColor, search.tokens.edge, 'the search field: the shared edge colour');
    assert.equal(search.background, search.tokens.bg, 'the search field: the shared field background');
    // hover
    await p().hover(T('di-review-refresh'));
    const hover = await controlLook('di-review-refresh');
    log('hover', 'di-review-refresh', hover);
    assert.equal(hover.background, hover.tokens.controlHover, 'hover: the shared hover colour');
    assert.equal(hover.borderColor, hover.tokens.muted, 'hover: the border is the shared muted colour');
    assert.deepEqual(hover.radius, radius8, 'hover: still an 8 px radius');
    assert.deepEqual(hover.borderWidths, width1, 'hover: still a 1 px border');
    await p().mouse.move(0, 0);
    // pressed: the layout toggle that is on
    const pressed = await controlLook('di-review-layout-unified');
    log('pressed', 'di-review-layout-unified', pressed);
    assert.equal(await p().getAttribute(T('di-review-layout-unified'), 'aria-pressed'), 'true');
    assert.equal(pressed.background, pressed.tokens.selected, 'pressed: the shared selected colour');
    assert.equal(pressed.borderColor, pressed.tokens.link, 'pressed: the border is the shared link colour');
    assert.deepEqual(pressed.radius, ['8px', '0px', '0px', '8px'], 'pressed: the first segment of its group, rounded at its outer end');
    assert.deepEqual(pressed.borderWidths, width1, 'pressed: a 1 px border');
    // expanded: the details toggle opened
    await p().click(T('di-review-details'));
    await p().mouse.move(0, 0);
    const expanded = await controlLook('di-review-details');
    log('expanded', 'di-review-details', expanded);
    assert.equal(await p().getAttribute(T('di-review-details'), 'aria-expanded'), 'true');
    assert.equal(expanded.background, expanded.tokens.selected, 'expanded: the shared selected colour');
    assert.equal(expanded.borderColor, expanded.tokens.link, 'expanded: the border is the shared link colour');
    assert.deepEqual(expanded.radius, radius8, 'expanded: an 8 px radius');
    await p().click(T('di-review-details'));
    // the code rows keep their own geometry: the section bar and a file row stay square; their hover and selected colours are the shared ones
    const bar = await controlLook('di-review-hunk');
    assert.deepEqual(bar.radius, ['0px', '0px', '0px', '0px'], 'the section bar stays a square full-width bar');
    await p().hover(T('di-review-hunk'));
    const barHover = await controlLook('di-review-hunk');
    assert.equal(barHover.background, barHover.tokens.selected, 'the section bar on hover: the shared selected colour');
    await p().mouse.move(0, 0);
    const row = await controlLook('di-review-row');
    assert.deepEqual(row.radius, ['0px', '0px', '0px', '0px'], 'a file row stays square (it has an accent bar)');
    const selectedRow = await p().$eval(`${T('di-review-row')}[aria-selected="true"]`, () => 'present');
    assert.equal(selectedRow, 'present');
    const selected = await p().evaluate(() => {
      const el = document.querySelector<HTMLElement>('[data-testid="di-review-row"][aria-selected="true"]')!;
      const probe = document.createElement('div');
      probe.style.background = 'var(--di-selected)';
      document.body.append(probe);
      const want = getComputedStyle(probe).backgroundColor;
      probe.remove();
      return { background: getComputedStyle(el).backgroundColor, want };
    });
    assert.equal(selected.background, selected.want, 'the selected file row: the shared selected colour');

    // tabs, in a narrow window: the pressed tab and the other one
    await p().setViewportSize({ width: 320, height: 568 });
    await frames(3);
    await p().waitForSelector(T('di-review-tab-diff'));
    const tabDiff = await controlLook('di-review-tab-diff');
    const tabFiles = await controlLook('di-review-tab-files');
    assert.deepEqual([tabDiff.radius, tabFiles.radius], [radius8, radius8], 'the tabs: an 8 px radius');
    assert.deepEqual([tabDiff.borderWidths, tabFiles.borderWidths], [width1, width1], 'the tabs: a 1 px border');
    assert.equal(tabDiff.background, tabDiff.tokens.selected, 'the pressed tab: the shared selected colour');
    assert.equal(tabFiles.background, tabFiles.tokens.control, 'the other tab: the shared control colour');
  },
  STEPS_MS,
);

/** The names, label in name and state-in-words checks of one sweep, each a list that must be empty. */
function assertNamedAndStated(where: string, sweep: A11ySweep) {
  assert.ok(sweep.controls.length > 0, `${where}: the sweep read some controls`);
  assert.deepEqual(sweep.controls.filter((c) => c.source === 'none').map((c) => c.id), [], `${where}: every control has a name`);
  assert.deepEqual(
    sweep.controls.filter((c) => c.source === 'title' || c.source === 'placeholder').map((c) => `${c.id} (named by its ${c.source} alone)`),
    [],
    `${where}: no control is named by a title or placeholder alone`,
  );
  assert.deepEqual(sweep.controls.filter((c) => c.labelInName === false).map((c) => `${c.id}: "${c.name}"`), [], `${where}: the accessible name contains the visible label`);
  assert.deepEqual(sweep.stateMarks.filter((m) => m.words === '').map((m) => m.id), [], `${where}: every state mark says its state in words`);
}
/** Nothing runs by itself: no animated or transitioning element, no running animation. */
function assertQuiet(where: string, sweep: A11ySweep) {
  assert.deepEqual(sweep.motion, [], `${where}: no element animates or transitions`);
  assert.equal(sweep.running, 0, `${where}: no running animation`);
}
const undersized = (sweep: A11ySweep, only?: string[]) =>
  sweep.controls.filter((c) => (only === undefined || only.includes(c.id)) && (c.width < 24 || c.height < 24)).map((c) => `${c.id} ${c.width}x${c.height}`);

test(
  'AC6 sweep, review dialog: names, label in name, state in words, no motion once settled, 24px targets for the new controls, under both Reduce Motion settings',
  async () => {
    const sweep = (selector: string) => a11ySweep(p(), selector);
    const settle = async () => {
      await openFile('src/app.py'); // the overview is loaded and a file is selected
      // the sliding "Reading ..." indicator is gone: nothing is loading
      await p().waitForFunction(() => document.querySelector('.di-rv-progress') === null, undefined, { timeout: 20_000 });
      await frames(3);
    };
    for (const motionSetting of ['no-preference', 'reduce'] as const) {
      await p().emulateMedia({ reducedMotion: motionSetting });
      assert.equal(await p().evaluate(() => matchMedia('(prefers-reduced-motion: reduce)').matches), motionSetting === 'reduce');
      if (motionSetting === 'no-preference') await openReview();
      else {
        await p().click(T('di-open-code-review'));
        await p().waitForSelector(T('di-review-dialog'));
        await p().waitForSelector(T('di-review-tree'), { timeout: 20_000 });
      }
      await settle();

      // the dialog, settled
      const dialog = await sweep(T('di-review-dialog'));
      const where = `dialog (${motionSetting})`;
      assert.ok(dialog.controls.length >= 10, `${where}: a real set of controls was read (${dialog.controls.length})`);
      assertNamedAndStated(where, dialog);
      assertQuiet(where, dialog);
      const minimize = dialog.controls.find((c) => c.id === 'di-review-minimize');
      assert.ok(minimize, `${where}: the Minimize control is in the sweep`);
      assert.deepEqual(undersized(dialog, ['di-review-minimize']), [], `${where}: the new Minimize control is at least 24 x 24 (${minimize.width}x${minimize.height})`);
      if (motionSetting === 'no-preference') {
        // listed once for the report; NOT asserted (these controls pre-date this slice)
        console.log(`[a11y] review dialog controls under 24px, pre-existing, not asserted: ${JSON.stringify(undersized({ ...dialog, controls: dialog.controls.filter((c) => c.id !== 'di-review-minimize') }))}`);
        console.log(`[a11y] review dialog: ${dialog.controls.length} controls, ${dialog.stateMarks.length} state marks, new Minimize ${minimize.width}x${minimize.height}`);
      }

      // minimized: the tray holds the review chip alone
      await p().click(T('di-review-minimize'));
      await p().waitForSelector(T('di-review-restore'));
      await frames(3);
      const alone = await sweep(T('di-minimized-tray'));
      assert.deepEqual(alone.controls.map((c) => c.id).sort(), ['di-review-restore'], `tray (${motionSetting}, review alone): exactly the review chip`);
      assertNamedAndStated(`tray, review alone (${motionSetting})`, alone);
      assertQuiet(`tray, review alone (${motionSetting})`, alone);
      assert.deepEqual(undersized(alone), [], `tray, review alone (${motionSetting}): every control is at least 24 x 24`);

      // Reception minimized as well: two controls, by id
      await p().click(T('di-open-reception'));
      await p().waitForSelector(T('di-reception-inbox'));
      await p().click(T('di-reception-minimize'));
      await p().waitForSelector(T('di-reception-restore'));
      await frames(3);
      const both = await sweep(T('di-minimized-tray'));
      assert.deepEqual(both.controls.map((c) => c.id).sort(), ['di-reception-restore', 'di-review-restore'], `tray (${motionSetting}, both): exactly the two chips`);
      assertNamedAndStated(`tray, both (${motionSetting})`, both);
      assertQuiet(`tray, both (${motionSetting})`, both);
      assert.deepEqual(undersized(both), [], `tray, both (${motionSetting}): every control is at least 24 x 24`);
      assert.ok(both.stateMarks.length >= 1, 'Reception waits on the person: its chip carries the attention mark, in words');

      // put both away for the next setting: Reception, then the review
      await p().click(T('di-reception-restore'));
      await p().waitForSelector(T('di-reception-restore'), { state: 'detached' });
      await p().keyboard.press('Escape');
      await p().waitForSelector(T('di-reception-inbox'), { state: 'detached' });
      await p().click(T('di-review-restore'));
      await p().waitForSelector(T('di-review-restore'), { state: 'detached' });
      await p().keyboard.press('Escape');
      await p().waitForSelector(T('di-review-dialog'), { state: 'detached' });
    }

    // The loading state, REACHABLE with the existing delayed-reply helper: the overview is answered 3 s late, so
    // "Reading the workspace" shows its sliding indicator. Read-only: nothing is forced beyond the delay.
    const loading: Record<string, { running: number; pseudo: string }> = {};
    for (const motionSetting of ['no-preference', 'reduce'] as const) {
      await p().emulateMedia({ reducedMotion: motionSetting });
      replyDelayMs = 3000;
      await p().click(T('di-open-code-review'));
      await p().waitForSelector(T('di-review-loading'));
      const swept = await sweep(T('di-review-loading'));
      const pseudo = await p().evaluate(() => {
        const bar = document.querySelector('.di-rv-progress');
        return bar === null ? 'no indicator' : getComputedStyle(bar, '::after').animationName;
      });
      loading[motionSetting] = { running: swept.running, pseudo };
      await p().keyboard.press('Escape');
      await p().waitForSelector(T('di-review-dialog'), { state: 'detached' });
      replyDelayMs = 0;
    }
    console.log(`[a11y] loading indicator: ${JSON.stringify(loading)}`);
    // the detector can see the slide when motion is allowed (so "0" means something), and it is off under Reduce Motion
    assert.ok(loading['no-preference'].running >= 1, `the loading indicator slides when motion is allowed (${JSON.stringify(loading['no-preference'])})`);
    assert.equal(loading['no-preference'].pseudo, 'di-rv-slide');
    assert.equal(loading['reduce'].running, 0, `the loading indicator does not animate under Reduce Motion (${JSON.stringify(loading['reduce'])})`);
    assert.equal(loading['reduce'].pseudo, 'none');
  },
  STEPS_MS,
);

test('the code-review view allows exactly one Mission request message: a bare overview refresh', () => {
  assert.equal(isOverviewPing({ type: 'diMissionRequest', seq: 4, op: 'refresh' }), true);
  // Anything that does something, or carries anything, is not the ping.
  const refused: Array<Record<string, unknown>> = [
    { type: 'diMissionRequest', seq: 4, op: 'decide', requestRef: 'lr-x', reply: 'approve' },
    { type: 'diMissionRequest', seq: 4, op: 'refresh', reply: 'approve' },
    { type: 'diMissionRequest', seq: 4, op: 'refresh', offerDigest: 'a'.repeat(64) },
    { type: 'diMissionRequest', seq: 4, op: 'refresh', expiresAt: 1 },
    { type: 'diMissionRequest', seq: 4, op: 'refresh', scope: ['engineering_change'] },
    { type: 'diMissionRequest', seq: 4, op: 'refresh', deliveryTargets: [] },
    { type: 'diMissionRequest', seq: 4, op: 'refresh', text: 'hello' },
    { type: 'diMissionRequest', seq: 4, op: 'request', requestRef: 'lr-x' },
    { type: 'diMissionRequest', seq: 4, op: 'offer', requestRef: 'lr-x' },
    { type: 'diMissionRequest', seq: 4, op: 'conversation', conversationId: 'cv-x' },
    { type: 'diMissionRequest', seq: 4, op: 'run', requestRef: 'lr-x', command: 'dispatch' },
    { type: 'diMissionRequest', op: 'refresh' },
    { type: 'diMissionRequest', seq: '4', op: 'refresh' },
    { type: 'diCodeReviewRead', seq: 4, op: 'refresh' },
    // An extra key of ANY name, including ones that sort after `type`, is not the ping.
    { type: 'diMissionRequest', seq: 4, op: 'refresh', unexpected: 1 },
    { type: 'diMissionRequest', seq: 4, op: 'refresh', zzz: 'x' },
  ];
  for (const msg of refused) assert.equal(isOverviewPing(msg), false, JSON.stringify(msg));
  // Every OTHER operation is refused even when it carries nothing else: only `refresh` is a ping.
  for (const op of [
    'conversation',
    'request',
    'offer',
    'result',
    'turn',
    'submit',
    'decide',
    'checkApproval',
    'withdraw',
    'run',
    'not-an-op',
    '',
  ]) {
    assert.equal(isOverviewPing({ type: 'diMissionRequest', seq: 4, op }), false, `bare op ${op}`);
  }
});
