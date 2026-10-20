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

import type { DiCodeReview, DiOfficeState } from '../../core/src/messages.ts';

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
    } else if (msg.type !== 'webviewReady' && msg.type !== 'saveAgentSeats') {
      otherMessages.push(String(msg.type));
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
    // the panel scrolls the selected agent's Mission into view; the entry is ALSO pinned at the top
    // of the panel, so it stays in reach however far the panel has scrolled
    const bar = await p().locator(T('di-open-code-review-bar')).boundingBox();
    assert.ok(bar !== null, 'the entry is pinned while an agent is selected');
    assert.ok(bar.y >= 0 && bar.y + bar.height <= VIEW.height, `in view: y=${bar.y}`);
    assert.equal(await exists(T('di-review-dialog')), false, 'nothing opens by itself');
    assert.deepEqual(requests, [], 'selecting an agent reads nothing');
    // the same agent, its assignment no longer active: no button, and the reason in words
    send(S.inactive);
    await p().waitForSelector(T('di-code-review-unavailable'), { timeout: 15_000 });
    const why = await text(T('di-code-review-unavailable'));
    assert.match(why, /not offered/);
    assert.match(why, /not active work/);
    assert.equal(await exists(T('di-open-code-review')), false);
    await shot('entry-not-offered');
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
      ['expired office', () => send(S.stale), /The office is not live — the review was cleared/],
      ['assignment inactive', () => send(S.inactive), /The assignment changed — the review was cleared/],
      ['task replaced', () => send(S.moved), /The assignment changed — the review was cleared/],
      ['agent gone', () => send(S.agentGone), /The agent is gone — the review was cleared/],
      ['backend down', () => send(S.unavailable), /The office is not live — the review was cleared/],
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
    assert.deepEqual(otherMessages, [], `no other message type was sent: ${otherMessages.join()}`);
  },
  STEPS_MS,
);
