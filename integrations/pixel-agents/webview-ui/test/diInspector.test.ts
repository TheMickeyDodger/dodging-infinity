/**
 * feature13: the full-size INSPECTOR, driven by REAL key presses in a real browser.
 *
 * The page (`test/harness/di-panel.html`) mounts the real `DiOfficePanel` over the office state the
 * real server projects from the labelled packaged fixture `herdr-task-session-mismatch-v2` (FIXTURE,
 * not live). Every test reaches the inspector the way a person does — Tab to a labelled "Open
 * inspector" on a Mission card or in the Selected dock, then Enter — and reads what the browser did:
 * the dialog's role and name, where focus is, whether #root is inert, where Tab goes, which keys
 * reached the office, and where focus lands when Escape closes it.
 *
 * Browser: the installed Chrome (playwright-core, channel `chrome`), else Playwright's own
 * Chromium. These are REQUIRED regressions: with neither browser launchable every test FAILS, naming
 * both launch errors. They never skip (a missing browser must not turn the regression into a pass).
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

import type { DiOfficeState } from '../../core/src/messages.ts';
import { shortMissionRefs } from '../src/components/diPlainSummary.ts';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const fixture = JSON.parse(
  fs.readFileSync(
    path.join(root, 'test/fixtures/di-office-state-session-mismatch-v2.FIXTURE.json'),
    'utf-8',
  ),
) as { state: DiOfficeState };
const firstMission = fixture.state.missions.missions[0];
const shortRefs = shortMissionRefs(fixture.state.missions.missions);
const drawnMember = fixture.state.missions.missions
  .flatMap((m) => m.members.members.map((x) => ({ member: x, mission: m })))
  .find(({ member }) => member.characterId !== null)!;

let server: ViteDevServer | null = null;
let browser: Browser | null = null;
let page: Page | null = null;
let url = '';
let unavailable: string | null = null;

beforeAll(async () => {
  server = await createServer({
    configFile: path.resolve(root, 'vite.config.ts'),
    server: { port: 0, strictPort: false },
    logLevel: 'silent',
  });
  await server.listen();
  const addr = server.httpServer?.address();
  const port = typeof addr === 'object' && addr !== null ? addr.port : 0;
  url = `http://localhost:${port}/test/harness/di-panel.html`;
  const firstLine = (e: unknown) => String(e).split('\n')[0];
  try {
    browser = await chromium.launch({ channel: 'chrome', headless: true });
  } catch (chromeError) {
    try {
      browser = await chromium.launch({ headless: true });
    } catch (chromiumError) {
      unavailable = `neither browser could be launched. Installed Chrome (channel "chrome"): ${firstLine(chromeError)} | Playwright's Chromium: ${firstLine(chromiumError)}`;
    }
  }
}, 120_000);

afterAll(async () => {
  await browser?.close();
  await server?.close();
});

beforeEach(async () => {
  // A REQUIRED regression: no browser is a FAILURE, never a skip.
  if (unavailable) throw new Error(`required browser regression cannot run: ${unavailable}`);
  await page?.close();
  page = await browser!.newPage({ viewport: { width: 1440, height: 900 } });
  await page.goto(url, { waitUntil: 'domcontentloaded' });
  await page.waitForSelector('[data-testid="di-office-panel"]', { timeout: 30_000 });
  // Every key that reaches the window in the bubble phase reached "the office".
  await page.evaluate(() => {
    const w = window as unknown as { __reached: string[] };
    w.__reached = [];
    window.addEventListener('keydown', (e) => w.__reached.push(e.key));
  });
});

const p = () => page!;
const T = (id: string) => `[data-testid="${id}"]`;
const active = (selector: string) =>
  p().evaluate((s) => document.activeElement?.matches(s) ?? false, selector);
const activeInfo = () =>
  p().evaluate(() => {
    const a = document.activeElement as HTMLElement | null;
    return {
      testid: a?.getAttribute('data-testid') ?? null,
      mission: a?.closest('[data-mission-ref]')?.getAttribute('data-mission-ref') ?? null,
      insideDialog: document.querySelector('[role="dialog"]')?.contains(a) ?? false,
    };
  });
const reached = () => p().evaluate(() => (window as unknown as { __reached: string[] }).__reached);
/** Start recording keys that reach the office from now on (keys pressed before the dialog opened
 *  legitimately went to the page). */
const recordFromNow = () =>
  p().evaluate(() => {
    (window as unknown as { __reached: string[] }).__reached = [];
  });

async function tabTo(selector: string, max = 400): Promise<number | null> {
  for (let i = 1; i <= max; i++) {
    await p().keyboard.press('Tab');
    if (await active(selector)) return i;
  }
  return null;
}

/** The dialog as the browser exposes it, and the application root's inert state. */
const dialogFacts = () =>
  p().evaluate(() => {
    const d = document.querySelector<HTMLElement>('[role="dialog"]');
    const labelId = d?.getAttribute('aria-labelledby') ?? null;
    const label = labelId ? document.getElementById(labelId) : null;
    return {
      open: d !== null,
      modal: d?.getAttribute('aria-modal') ?? null,
      name: label?.textContent?.trim() ?? null,
      headingTag: label?.tagName ?? null,
      rootInert: document.getElementById('root')?.hasAttribute('inert') ?? false,
      inBody: d?.closest('#root') === null,
      pageScrollsX: document.documentElement.scrollWidth > window.innerWidth,
    };
  });

test('from a Mission card: Enter opens a real modal dialog named by its heading; Escape closes and returns focus', async () => {
  const presses = await tabTo(T('di-open-inspector-mission'));
  assert.notEqual(presses, null, 'Tab never reached a card\x27s "Show details"');
  const opener = await activeInfo();
  assert.equal(opener.mission, firstMission.missionRef, 'the first card\x27s entry comes first');
  assert.equal(
    await p().evaluate(() => document.activeElement?.textContent),
    'Show details',
    'the entry is labelled in words',
  );
  await p().keyboard.press('Enter');
  await p().waitForSelector('[role="dialog"]');
  await recordFromNow();
  const facts = await dialogFacts();
  assert.equal(facts.modal, 'true');
  assert.equal(facts.headingTag, 'H2');
  assert.ok(facts.name && facts.name.length > 0, 'named by a real heading');
  assert.equal(facts.rootInert, true, '#root is inert while the inspector is open');
  assert.equal(facts.inBody, true, 'a portal outside #root');
  assert.equal((await activeInfo()).insideDialog, true, 'focus moved into the dialog');
  // The Mission's full record is there: its complete ref and the status it rests on.
  const body = await p().$eval(T('di-inspector'), (el) => el.textContent ?? '');
  assert.ok(body.includes(firstMission.missionRef));
  assert.match(body, /status derived from/);
  await p().keyboard.press('Escape');
  await p().waitForSelector('[role="dialog"]', { state: 'detached' });
  assert.equal((await dialogFacts()).rootInert, false, '#root is usable again');
  const back = await activeInfo();
  assert.deepEqual(
    { testid: back.testid, mission: back.mission },
    { testid: 'di-open-inspector-mission', mission: firstMission.missionRef },
    'focus returned to the control that opened it',
  );
  assert.deepEqual(await reached(), [], 'no key reached the office');
});

test('Tab is trapped in the dialog and wraps both ways; no key reaches the office', async () => {
  assert.notEqual(await tabTo(T('di-open-inspector-mission')), null);
  await p().keyboard.press('Enter');
  await p().waitForSelector('[role="dialog"]');
  await recordFromNow();
  const visited: string[] = [];
  for (let i = 0; i < 60; i++) {
    await p().keyboard.press('Tab');
    const info = await activeInfo();
    assert.equal(info.insideDialog, true, `Tab ${i + 1} left the dialog`);
    visited.push(info.testid ?? '?');
  }
  // It wrapped: the Close button (the first stop) came round again.
  assert.ok(visited.filter((v) => v === 'di-inspector-close').length >= 2, visited.join(','));
  // Shift+Tab from the first stop goes to the last, still inside.
  await p().focus(T('di-inspector-close'));
  await p().keyboard.press('Shift+Tab');
  assert.equal((await activeInfo()).insideDialog, true);
  for (const key of ['a', 'ArrowDown', 'Home', ' ']) await p().keyboard.press(key);
  assert.deepEqual(await reached(), [], 'no key reached the office');
});

test('from the Selected dock: select a member by keyboard, open its inspector, read its raw fields, return', async () => {
  const { member } = drawnMember;
  assert.notEqual(await tabTo(T('di-find-input')), null);
  await p().keyboard.type(member.memberRef);
  await p().keyboard.press('ArrowDown');
  await p().keyboard.press('Enter');
  await p().waitForFunction(
    (id) =>
      (window as unknown as { __diHarness: { office: { selectedAgentId: number | null } } })
        .__diHarness.office.selectedAgentId === id,
    member.characterId,
    { timeout: 5_000 },
  );
  await p().waitForSelector(T('di-selected-member'));
  // The dock is concise and in the viewport, with one obvious entry to the inspector.
  const dock = await p().$eval(T('di-selected-agent'), (el) => {
    const r = el.getBoundingClientRect();
    return { top: r.top, bottom: r.bottom, text: (el as HTMLElement).innerText };
  });
  assert.ok(dock.top >= 0 && dock.bottom <= 900, `dock in view: ${dock.top}..${dock.bottom}`);
  assert.ok(dock.text.includes(member.logical));
  assert.equal(
    await p().$eval(T('di-selected-member-mission'), (el) => el.getAttribute('title')),
    drawnMember.mission.missionRef,
    'names its Mission by its complete ref',
  );
  assert.ok(
    dock.text.includes(shortRefs.get(drawnMember.mission.missionRef)!),
    'shows its short source-qualified ref',
  );
  for (const other of fixture.state.missions.missions)
    if (other.missionRef !== drawnMember.mission.missionRef)
      assert.ok(
        !dock.text.includes(shortRefs.get(other.missionRef)!),
        `...and not another Mission's (${other.missionRef})`,
      );
  assert.notEqual(await tabTo(T('di-open-inspector-selection')), null);
  await p().keyboard.press('Enter');
  await p().waitForSelector(T('di-inspector-selected'));
  await p().click(`${T('di-inspector-record')} > summary`);
  await p().waitForSelector(T('di-inspector-member'));
  assert.equal((await dialogFacts()).rootInert, true);
  const detail = await p().$eval(T('di-inspector-member'), (el) => el.textContent ?? '');
  // The raw identity fields the dock leaves out are here, exactly.
  assert.ok(detail.includes(member.memberRef));
  assert.ok(detail.includes(`${member.classification} (${member.reason})`));
  assert.match(detail, /model requested \/ configured/);
  await p().keyboard.press('Escape');
  await p().waitForSelector('[role="dialog"]', { state: 'detached' });
  assert.equal((await activeInfo()).testid, 'di-open-inspector-selection');
});

test('at a constrained window every scroll is inside the dialog: the page never scrolls sideways', async () => {
  await p().setViewportSize({ width: 1024, height: 680 });
  assert.notEqual(await tabTo(T('di-open-inspector-mission')), null);
  await p().keyboard.press('Enter');
  await p().waitForSelector('[role="dialog"]');
  const facts = await dialogFacts();
  assert.equal(facts.pageScrollsX, false);
  const box = await p().$eval('[role="dialog"]', (el) => {
    const r = el.getBoundingClientRect();
    return { left: r.left, right: r.right, top: r.top, bottom: r.bottom };
  });
  assert.ok(
    box.left >= 0 && box.right <= 1024 && box.top >= 0 && box.bottom <= 680,
    JSON.stringify(box),
  );
});

// -- Root's early finding: a locate from inside the inspector must SHOW the located agent -------

type HarnessOffice = { selectedAgentId: number | null; cameraFollowId: number | null };
const officeSel = () =>
  p().evaluate(() => {
    const o = (window as unknown as { __diHarness: { office: HarnessOffice } }).__diHarness.office;
    return { selected: o.selectedAgentId, camera: o.cameraFollowId };
  });

/** After a locate: no dialog, #root usable, exactly that record selected, focus on the dock. */
async function expectLocated(memberRef: string, characterId: number, missionRef: string) {
  await p().waitForSelector('[role="dialog"]', { state: 'detached', timeout: 5_000 });
  const facts = await dialogFacts();
  assert.equal(facts.open, false, 'the inspector closed: the world is not covered or dimmed');
  assert.equal(facts.rootInert, false, '#root is usable again');
  assert.equal(
    await p().evaluate(() => document.querySelector('.di-op-inspector-backdrop') === null),
    true,
    'no backdrop is left over the world',
  );
  assert.deepEqual(await officeSel(), { selected: characterId, camera: characterId });
  // The SAME source-qualified record is selected: the dock names it and its complete Mission ref.
  const dock = await p().$eval(T('di-selected-member'), (el) => (el as HTMLElement).innerText);
  assert.equal(
    await p().$eval(T('di-selected-member-mission'), (el) => el.getAttribute('title')),
    missionRef,
    `dock names ${missionRef}`,
  );
  assert.ok(dock.includes(memberRef.split('#').at(-1)!), 'the dock names the member');
  await p().waitForFunction(
    () =>
      document.activeElement?.getAttribute('data-testid') === 'di-open-inspector-selection' &&
      document.querySelector('[data-testid="di-selected-agent"]')?.contains(document.activeElement),
    undefined,
    { timeout: 5_000 },
  );
}

test('locate from the dock\x27s inspector: "Show in the office" closes it and shows that agent, focus on the dock', async () => {
  const { member, mission } = drawnMember;
  assert.notEqual(await tabTo(T('di-find-input')), null);
  await p().keyboard.type(member.memberRef);
  await p().keyboard.press('ArrowDown');
  await p().keyboard.press('Enter');
  await p().waitForSelector(T('di-selected-member'));
  await p().click(T('di-open-inspector-selection'));
  await p().waitForSelector(T('di-inspector-selected'));
  await p().click(`${T('di-inspector-selected')} ${T('di-show-in-office')}`);
  await expectLocated(member.memberRef, member.characterId!, mission.missionRef);
});

test('locate from a Mission card\x27s inspector: a team member\x27s "Show in the office" shows THAT member', async () => {
  const { member, mission } = drawnMember;
  await p().focus(`[data-mission-ref="${mission.missionRef}"] ${T('di-open-inspector-mission')}`);
  await p().keyboard.press('Enter');
  await p().waitForSelector('[role="dialog"]');
  assert.deepEqual(await officeSel(), { selected: null, camera: null }, 'nothing selected yet');
  await p().click(
    `${T('di-inspector-team-member')}[data-member-ref="${member.memberRef}"] ${T('di-show-in-office')}`,
  );
  await expectLocated(member.memberRef, member.characterId!, mission.missionRef);
});

test('a NOT-DRAWN record is never offered a locate in the inspector; its plain reason stays', async () => {
  const { member, mission } = drawnMember;
  // A later projection: the member is not drawn now (the office cannot vouch for it).
  await p().evaluate(
    (ref) =>
      (
        window as unknown as {
          __diHarness: { update: (f: (s: DiOfficeState) => DiOfficeState) => void };
        }
      ).__diHarness.update((s) => {
        for (const m of s.missions.missions)
          for (const x of m.members.members)
            if (x.memberRef === ref) {
              x.characterId = null;
              x.live = false;
              x.notLiveReason = 'transport_reconnecting';
            }
        return s;
      }),
    member.memberRef,
  );
  await p().focus(`[data-mission-ref="${mission.missionRef}"] ${T('di-open-inspector-mission')}`);
  await p().keyboard.press('Enter');
  await p().waitForSelector('[role="dialog"]');
  const card = `${T('di-inspector-team-member')}[data-member-ref="${member.memberRef}"]`;
  assert.equal(await p().$(`${card} ${T('di-show-in-office')}`), null, 'no locate offered');
  assert.match(await p().$eval(card, (el) => el.textContent ?? ''), /Not drawn now:/);
  await p().keyboard.press('Escape');
  // Selected through Find (inspected only, never drawn): its inspector offers no locate either.
  assert.notEqual(await tabTo(T('di-find-input')), null);
  await p().keyboard.type(member.memberRef);
  await p().keyboard.press('ArrowDown');
  await p().keyboard.press('Enter');
  await p().waitForSelector(T('di-selected-member'));
  await p().click(T('di-open-inspector-selection'));
  await p().waitForSelector(T('di-inspector-selected'));
  assert.equal(await p().$(`${T('di-inspector-selected')} ${T('di-show-in-office')}`), null);
  assert.match(
    await p().$eval(T('di-inspector-selected'), (el) => el.textContent ?? ''),
    /Not drawn now:/,
  );
});

// -- the positive OFFERED routes (owned FIXTURE; real user paths; seams recorded by the harness) ----

type Call = {
  review: Array<{
    missionRef: string;
    memberRef: string;
    taskId: string;
    listing: string;
    openerTestId: string | null;
  }>;
  mission: Array<{ tab: string; openerTestId: string | null }>;
};
const calls = () =>
  p().evaluate(() => (window as unknown as { __diHarness: { calls: Call } }).__diHarness.calls);

/** The office now OFFERS a code review for the drawn member (a later projection, as the office sends). */
async function offerReview(memberRef: string, taskId: string, listing: string) {
  await p().evaluate(
    ([ref, task, list]) =>
      (
        window as unknown as {
          __diHarness: { update: (f: (s: DiOfficeState) => DiOfficeState) => void };
        }
      ).__diHarness.update((s) => {
        for (const m of s.missions.missions)
          for (const x of m.members.members)
            if (x.memberRef === ref)
              x.codeReview = { state: 'offered', reason: null, taskId: task, listing: list };
        return s;
      }),
    [memberRef, taskId, listing] as const,
  );
}

/** A later projection: the drawn member's review is no longer offered (assignment not active). */
async function withholdReview(memberRef: string, taskId: string) {
  await p().evaluate(
    ([ref, task]) =>
      (
        window as unknown as {
          __diHarness: { update: (f: (s: DiOfficeState) => DiOfficeState) => void };
        }
      ).__diHarness.update((s) => {
        for (const m of s.missions.missions)
          for (const x of m.members.members)
            if (x.memberRef === ref)
              x.codeReview = {
                state: 'unavailable',
                reason: 'assignment_not_active',
                taskId: task,
                listing: null,
              };
        return s;
      }),
    [memberRef, taskId] as const,
  );
}

async function selectDrawnMemberByFind() {
  const { member } = drawnMember;
  assert.notEqual(await tabTo(T('di-find-input')), null);
  await p().keyboard.type(member.memberRef);
  await p().keyboard.press('ArrowDown');
  await p().keyboard.press('Enter');
  await p().waitForSelector(T('di-selected-member'));
}

test('an OFFERED code review: select a member, reach "Open code review" in the dock, open it; the opener is the dock entry', async () => {
  const { member, mission } = drawnMember;
  const taskId = member.taskId.value!;
  await offerReview(member.memberRef, taskId, 'FIXTURE-listing-0001');
  await selectDrawnMemberByFind();
  // The entry is in the dock, in the viewport, labelled; the explanation is one line.
  const box = await p().locator(T('di-open-code-review')).boundingBox();
  assert.ok(box !== null && box.y >= 0 && box.y + box.height <= 900, 'in the viewport');
  assert.match(
    await p().$eval(T('di-code-review-entry'), (el) => (el as HTMLElement).innerText),
    /View only/,
  );
  await p().focus(T('di-open-code-review'));
  await p().keyboard.press('Enter');
  const recorded = await calls();
  assert.deepEqual(recorded.review, [
    {
      missionRef: mission.missionRef,
      memberRef: member.memberRef,
      taskId,
      listing: 'FIXTURE-listing-0001',
      openerTestId: 'di-open-code-review',
    },
  ]);
});

test('an OFFERED code review from the inspector: the inspector closes first; focus is owed to the control that opened it', async () => {
  const { member, mission } = drawnMember;
  const taskId = member.taskId.value!;
  await offerReview(member.memberRef, taskId, 'FIXTURE-listing-0002');
  await selectDrawnMemberByFind();
  await p().click(T('di-open-inspector-selection'));
  await p().waitForSelector(T('di-inspector-selected'));
  await p().click(`${T('di-inspector-selected')} ${T('di-inspector-open-code-review')}`);
  await p().waitForSelector('[role="dialog"]', { state: 'detached' });
  assert.equal((await dialogFacts()).rootInert, false, 'never two modals: #root is usable again');
  const [only] = (await calls()).review;
  assert.equal(only.missionRef, mission.missionRef);
  assert.equal(only.memberRef, member.memberRef);
  assert.equal(only.listing, 'FIXTURE-listing-0002');
  assert.equal(
    only.openerTestId,
    'di-open-inspector-selection',
    'returns to what opened the inspector',
  );
});

test('a CONFIGURED Mission request entry: both actions are reachable by keyboard and reach their seam', async () => {
  const ask = await tabTo(T('di-mr-open'));
  assert.notEqual(ask, null, 'Tab never reached "Ask the Codex Orchestrator"');
  assert.match(
    await p().evaluate(() => document.activeElement?.textContent ?? ''),
    /^Ask the Codex Orchestrator$/,
  );
  await p().keyboard.press('Enter');
  assert.notEqual(await tabTo(T('di-mr-open-requests')), null);
  assert.match(
    await p().evaluate(() => document.activeElement?.textContent ?? ''),
    /^Review requests/,
  );
  await p().keyboard.press('Enter');
  assert.deepEqual((await calls()).mission, [
    { tab: 'ask', openerTestId: 'di-mr-open' },
    { tab: 'requests', openerTestId: 'di-mr-open-requests' },
  ]);
  // Configured: the entry is the full one (two actions), never the compact "not available" row.
  assert.equal(await p().$(T('di-mr-entry-why')), null);
});

// -- a cached server throttle never masks a lost connection ON THE FACE (header, rows, Mission cards) ----

test('a cached throttle never masks a lost connection: header, envelope row and every Mission card say so', async () => {
  await p().evaluate(() =>
    (
      window as unknown as {
        __diHarness: { update: (f: (s: DiOfficeState) => DiOfficeState) => void };
      }
    ).__diHarness.update((s) => {
      // The client's own reason (the socket is reconnecting) with an OLDER, cached throttled envelope.
      s.live = false;
      s.notLiveReason = 'transport_reconnecting';
      s.envelope.fresh = false;
      s.envelope.reason = 'transport_reconnecting';
      s.envelope.envelopeProblem = 'catalog_refresh_throttled';
      s.envelope.retryAfterSeconds = 3;
      return s;
    }),
  );
  // The update was applied: the header no longer says LIVE (whatever it says instead is asserted below).
  await p().waitForFunction(
    () =>
      /NOT LIVE/.test(
        (document.querySelector('[data-testid="di-office-panel"]') as HTMLElement).innerText,
      ),
    undefined,
    { timeout: 5_000 },
  );
  const header = await p().$eval(
    T('di-office-panel'),
    (el) => (el as HTMLElement).innerText.split('\n')[0],
  );
  assert.match(header, /transport_reconnecting/);
  assert.doesNotMatch(
    header,
    /waiting: catalog_refresh_throttled/,
    'the header is not a calm backend wait',
  );
  const envelopeRow = await p().$eval(
    T('di-freshness-summary'),
    (el) => (el as HTMLElement).innerText,
  );
  assert.match(envelopeRow, /not fresh: transport_reconnecting/);
  assert.doesNotMatch(envelopeRow, /waiting: catalog_refresh_throttled/);
  const statuses = await p().$$eval('[data-mission-ref] [data-testid="di-op-status"]', (els) =>
    els.map((e) => e.getAttribute('data-status')),
  );
  assert.ok(statuses.length > 1);
  assert.deepEqual(
    [...new Set(statuses)],
    ['disconnected'],
    'every card reads Disconnected, never Stale',
  );
});

// -- the dock's doorways are VISIBLE on INITIAL selection (real geometry; no scroll, click or hover first) ----
//
// A bounding box of the DOCK cannot see an action hidden inside the dock's OWN scroll area, and a Playwright
// click() scrolls its target into view first, so a click-based test passes with the doorway hidden. Each action
// is therefore measured as laid out, straight after the selection, and compared with the dock's VISIBLE client
// area (its bottom fades), the viewport, and what elementFromPoint finds at its centre.

type DockGeometry = {
  scrollTop: number;
  clientHeight: number;
  scrollHeight: number;
  visible: { top: number; bottom: number };
  fadePx: number;
  /** The panel the dock sits in: whether it fits the window (the dock cannot be in view if it does not). */
  panel: { top: number; bottom: number; clientHeight: number; scrollHeight: number } | null;
  mainBottom: number | null;
  /** The panel's direct children and their heights: what the window's height is spent on. */
  panelChildren: string[];
  actions: Record<
    string,
    {
      rect: { top: number; bottom: number };
      insideViewport: boolean;
      insideDockVisible: boolean;
      clearOfFade: boolean;
      hit: string | null;
      hitIsAction: boolean;
    } | null
  >;
};

const dockGeometry = (testIds: string[]) =>
  p().evaluate((ids): DockGeometry | null => {
    const dock = document.querySelector<HTMLElement>('[data-testid="di-selected-agent"]');
    if (!dock) return null;
    const box = dock.getBoundingClientRect();
    const visible = {
      top: box.top + dock.clientTop,
      left: box.left + dock.clientLeft,
      bottom: box.top + dock.clientTop + dock.clientHeight,
      right: box.left + dock.clientLeft + dock.clientWidth,
    };
    const style = getComputedStyle(dock);
    const mask = style.maskImage || style.getPropertyValue('-webkit-mask-image');
    const fadePx = Number(/calc\(100% - ([\d.]+)px\)/.exec(mask)?.[1] ?? 0);
    const actions: DockGeometry['actions'] = {};
    for (const id of ids) {
      const el = dock.querySelector<HTMLElement>(`[data-testid="${id}"]`);
      if (!el) {
        actions[id] = null;
        continue;
      }
      const r = el.getBoundingClientRect();
      const hit = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2);
      actions[id] = {
        rect: { top: Math.round(r.top * 10) / 10, bottom: Math.round(r.bottom * 10) / 10 },
        insideViewport:
          r.top >= 0 &&
          r.left >= 0 &&
          r.bottom <= window.innerHeight &&
          r.right <= window.innerWidth &&
          r.height > 0,
        insideDockVisible:
          r.top >= visible.top - 0.5 &&
          r.left >= visible.left - 0.5 &&
          r.bottom <= visible.bottom + 0.5 &&
          r.right <= visible.right + 0.5,
        clearOfFade: r.bottom <= visible.bottom - fadePx + 0.5,
        hit: hit ? `${hit.tagName.toLowerCase()}[${hit.getAttribute('data-testid') ?? ''}]` : null,
        hitIsAction: hit !== null && (hit === el || el.contains(hit)),
      };
    }
    const panel = document.querySelector<HTMLElement>('[data-testid="di-office-panel"]');
    const pr = panel?.getBoundingClientRect() ?? null;
    const main = document.querySelector<HTMLElement>('[data-testid="di-op-main"]');
    return {
      scrollTop: dock.scrollTop,
      clientHeight: dock.clientHeight,
      scrollHeight: dock.scrollHeight,
      visible: { top: visible.top, bottom: visible.bottom },
      fadePx,
      panel:
        panel && pr
          ? {
              top: Math.round(pr.top),
              bottom: Math.round(pr.bottom),
              clientHeight: panel.clientHeight,
              scrollHeight: panel.scrollHeight,
            }
          : null,
      mainBottom: main ? Math.round(main.getBoundingClientRect().bottom) : null,
      panelChildren: Array.from(panel?.children ?? []).map(
        (c) =>
          `${c.getAttribute('data-testid') ?? c.className.toString().slice(0, 24)}=${Math.round(c.getBoundingClientRect().height)}`,
      ),
      actions,
    };
  }, testIds);

/** Every named action is whole in the viewport and in the dock's visible area, clear of its fade, and hit at its centre. */
async function assertDockDoorways(label: string, testIds: string[]) {
  const g = await dockGeometry(testIds);
  assert.ok(g !== null, `${label}: no Selected dock`);
  assert.equal(
    g.scrollTop,
    0,
    `${label}: measured on INITIAL selection (the dock has not scrolled)`,
  );
  for (const id of testIds) {
    const a: DockGeometry['actions'][string] | undefined = g.actions[id];
    const where: string = `${label}: ${id} ${JSON.stringify(a?.rect)} in dock ${g.visible.top}..${g.visible.bottom} (fade ${g.fadePx}px), content ${g.scrollHeight} in ${g.clientHeight}; panel ${JSON.stringify(g.panel)}, list ends ${g.mainBottom}; panel children ${g.panelChildren.join(' ')}`;
    assert.ok(a !== null && a !== undefined, `${label}: ${id} is not in the dock`);
    assert.ok(a.insideViewport, `${where}: not whole inside the viewport`);
    assert.ok(a.insideDockVisible, `${where}: not whole inside the dock's visible area`);
    assert.ok(a.clearOfFade, `${where}: inside the dock's bottom fade`);
    assert.ok(a.hitIsAction, `${where}: elementFromPoint at its centre is ${a.hit}`);
  }
}

const DOCK_VIEWPORTS = [
  { width: 1440, height: 900 },
  { width: 1280, height: 800 },
  { width: 1024, height: 768 },
  { width: 1100, height: 680 },
  { width: 1024, height: 680 },
] as const;

test('the dock offers its doorways in view on INITIAL selection at every viewport: a member, with a code review OFFERED', async () => {
  const { member } = drawnMember;
  for (const vp of DOCK_VIEWPORTS) {
    await p().setViewportSize(vp);
    await p().goto(url, { waitUntil: 'domcontentloaded' });
    await p().waitForSelector(T('di-office-panel'), { timeout: 30_000 });
    await offerReview(member.memberRef, member.taskId.value!, 'FIXTURE-listing-dock');
    await selectDrawnMemberByFind();
    await assertDockDoorways(`${vp.width}x${vp.height} member`, [
      'di-open-inspector-selection',
      'di-show-in-office',
      'di-show-mission',
      'di-open-code-review',
    ]);
  }
});

test('the dock offers its doorways in view on INITIAL selection at every viewport: a member whose code review is NOT offered', async () => {
  // The fixture's first drawn member is a bound, live Herdr task, so the office offers a review.
  // This case is the dock after a later projection withdraws that offer.
  const { member } = drawnMember;
  for (const vp of DOCK_VIEWPORTS) {
    await p().setViewportSize(vp);
    await p().goto(url, { waitUntil: 'domcontentloaded' });
    await p().waitForSelector(T('di-office-panel'), { timeout: 30_000 });
    await withholdReview(member.memberRef, member.taskId.value!);
    await selectDrawnMemberByFind();
    await assertDockDoorways(`${vp.width}x${vp.height} member, review not offered`, [
      'di-open-inspector-selection',
      'di-show-in-office',
      'di-show-mission',
    ]);
    // the fact that no review is offered is still on the dock (nothing was dropped)...
    assert.match(
      await p().$eval(T('di-dock-code-review-unavailable'), (el) => (el as HTMLElement).innerText),
      /not offered/,
    );
  }
});

test('the dock offers its doorways in view on INITIAL selection at every viewport: a runtime agent and a not-verified record', async () => {
  for (const vp of DOCK_VIEWPORTS) {
    await p().setViewportSize(vp);
    await p().goto(url, { waitUntil: 'domcontentloaded' });
    await p().waitForSelector(T('di-office-panel'), { timeout: 30_000 });
    await p().focus(T('di-runtime-row'));
    await p().keyboard.press('Enter');
    await p().waitForSelector(T('di-selected-runtime-agent'));
    await assertDockDoorways(`${vp.width}x${vp.height} runtime agent`, [
      'di-open-inspector-selection',
      'di-show-in-office',
    ]);
    await p().goto(url, { waitUntil: 'domcontentloaded' });
    await p().waitForSelector(T('di-office-panel'), { timeout: 30_000 });
    await p().focus(T('di-unverified-row'));
    await p().keyboard.press('Enter');
    await p().waitForSelector(T('di-selected-unverified'));
    await assertDockDoorways(`${vp.width}x${vp.height} not-verified record`, [
      'di-open-inspector-selection',
    ]);
  }
});

// -- the Missions LIST keeps a usable height under the worst supported combination (round-1 review, finding A) ----
//
// The worst supported pile-up on a short window: the Mission request entry CONFIGURED (its full row of buttons),
// a member selected whose code review is OFFERED, and a RETAINED multi-result Find (the query and its results stay
// after a result is selected). Everything above and below the Missions region has a share of the window; the
// Missions must not be what gives way. Measured as laid out in a real browser, straight after the selection,
// with no scroll or click first: whole Mission cards inside the region's visible area (clear of its bottom
// fade), the panel not overflowing, Find's results reachable, the salient caveats on the face.

type ListGeometry = {
  viewport: { width: number; height: number };
  panel: { overflowPx: number; children: string[] } | null;
  main: {
    top: number;
    visibleBottom: number;
    usableBottom: number;
    fadePx: number;
    scrollTop: number;
  } | null;
  cards: Array<{ ref: string; whole: boolean; teamBottomFromListTop: number | null }>;
  /** The card of the selected Mission (null: nothing selected): where it sits in the list. */
  selectedCard: { whole: boolean; top: number; bottom: number; listScrollTop: number } | null;
  find: {
    inputInViewport: boolean;
    resultCount: number;
    toggle: string | null;
    results: { top: number; bottom: number; clientHeight: number; scrollHeight: number } | null;
    resultsInViewport: boolean;
  };
  caveats: { headerInView: boolean; headerText: string; dockIdentityRowInDock: boolean };
};

/** Let layout and resize-observer callbacks finish (two animation frames): no scroll, no click, no hover. */
const settleLayout = () =>
  p().evaluate(
    () => new Promise<void>((r) => requestAnimationFrame(() => requestAnimationFrame(() => r()))),
  );

const listGeometry = async () => {
  await settleLayout();
  return listGeometryNow();
};

const listGeometryNow = () =>
  p().evaluate((): ListGeometry => {
    const round = (n: number) => Math.round(n * 10) / 10;
    const panel = document.querySelector<HTMLElement>('[data-testid="di-office-panel"]');
    const main = document.querySelector<HTMLElement>('[data-testid="di-op-main"]');
    const dock = document.querySelector<HTMLElement>('[data-testid="di-selected-agent"]');
    const style = main ? getComputedStyle(main) : null;
    const mask = style ? style.maskImage || style.getPropertyValue('-webkit-mask-image') : '';
    const fadePx = Number(/calc\(100% - ([\d.]+)px\)/.exec(mask)?.[1] ?? 0);
    let mainFacts: ListGeometry['main'] = null;
    if (main) {
      const r = main.getBoundingClientRect();
      const visibleBottom = r.top + main.clientTop + main.clientHeight;
      mainFacts = {
        top: round(r.top + main.clientTop),
        visibleBottom: round(visibleBottom),
        usableBottom: round(visibleBottom - fadePx),
        fadePx,
        scrollTop: main.scrollTop,
      };
    }
    const whole = (el: Element | null) => {
      if (!el || !mainFacts) return false;
      const r = el.getBoundingClientRect();
      return (
        r.height > 0 &&
        r.top >= mainFacts.top - 0.5 &&
        r.bottom <= mainFacts.usableBottom + 0.5 &&
        r.bottom <= window.innerHeight &&
        r.left >= 0 &&
        r.right <= window.innerWidth
      );
    };
    const cards = Array.from(document.querySelectorAll('[data-mission-ref]')).map((card) => ({
      ref: card.getAttribute('data-mission-ref') ?? '',
      teamBottomFromListTop: (() => {
        const t = card.querySelector('[data-testid="di-mission-team"]');
        return t && mainFacts
          ? Math.round((t.getBoundingClientRect().bottom - mainFacts.top) * 10) / 10
          : null;
      })(),
      whole:
        whole(card.querySelector('[data-testid="di-mission-objective"]')) &&
        whole(card.querySelector('[data-testid="di-op-status"]')) &&
        whole(card.querySelector('[data-testid="di-mission-team"]')),
    }));
    const selCard = document.querySelector('[data-selected-mission="true"]');
    const selectedCard = selCard
      ? {
          whole:
            whole(selCard.querySelector('[data-testid="di-mission-objective"]')) &&
            whole(selCard.querySelector('[data-testid="di-op-status"]')) &&
            whole(selCard.querySelector('[data-testid="di-mission-team"]')),
          top: Math.round(selCard.getBoundingClientRect().top * 10) / 10,
          bottom: Math.round(selCard.getBoundingClientRect().bottom * 10) / 10,
          listScrollTop: main?.scrollTop ?? 0,
        }
      : null;
    const inViewport = (el: Element | null) => {
      if (!el) return false;
      const r = el.getBoundingClientRect();
      return (
        r.height > 0 && r.top >= 0 && r.bottom <= window.innerHeight && r.right <= window.innerWidth
      );
    };
    const results = document.querySelector<HTMLElement>('[data-testid="di-find-results"]');
    const rr = results?.getBoundingClientRect();
    const header =
      panel?.querySelector<HTMLElement>('.di-op-header, [data-testid="di-op-header"]') ??
      panel?.firstElementChild ??
      null;
    const identity =
      dock?.querySelector('[data-testid="di-selected-member"] .di-op-card-meta') ?? null;
    let identityInDock = false;
    if (identity && dock) {
      const ir = identity.getBoundingClientRect();
      const dr = dock.getBoundingClientRect();
      identityInDock =
        ir.height > 0 &&
        ir.top >= dr.top + dock.clientTop - 0.5 &&
        ir.bottom <= dr.top + dock.clientTop + dock.clientHeight - 28 + 0.5 &&
        ir.bottom <= window.innerHeight;
    }
    return {
      viewport: { width: window.innerWidth, height: window.innerHeight },
      panel: panel
        ? {
            overflowPx: panel.scrollHeight - panel.clientHeight,
            children: Array.from(panel.children).map(
              (c) =>
                `${c.getAttribute('data-testid') ?? c.className.toString().slice(0, 24)}=${Math.round(c.getBoundingClientRect().height)}`,
            ),
          }
        : null,
      main: mainFacts,
      cards,
      selectedCard,
      find: {
        inputInViewport: inViewport(document.querySelector('[data-testid="di-find-input"]')),
        resultCount: document.querySelectorAll('[data-testid="di-find-result"]').length,
        toggle: document.querySelector('[data-testid="di-find-toggle"]')?.textContent ?? null,
        results:
          results && rr
            ? {
                top: round(rr.top),
                bottom: round(rr.bottom),
                clientHeight: results.clientHeight,
                scrollHeight: results.scrollHeight,
              }
            : null,
        resultsInViewport: inViewport(results),
      },
      caveats: {
        headerInView: inViewport(header),
        headerText:
          (header as HTMLElement | null)?.innerText.replace(/\s+/g, ' ').slice(0, 60) ?? '',
        dockIdentityRowInDock: identityInDock,
      },
    };
  });

/** Type a multi-result query into Find by keyboard; the results are OPEN (nothing is selected yet). */
async function typeMultiResultQuery(query: string) {
  assert.notEqual(await tabTo(T('di-find-input')), null);
  await p().keyboard.type(query);
  await p().waitForFunction(
    () => document.querySelectorAll('[data-testid="di-find-result"]').length >= 3,
    undefined,
    { timeout: 5_000 },
  );
}

/** Choose the drawn member's OWN row from the open results by keyboard; the query stays, the list folds. */
async function chooseDrawnMemberRow(query: string) {
  const { member } = drawnMember;
  const rows = await p().$$eval(T('di-find-result'), (els) =>
    els.map((e) => (e as HTMLElement).innerText),
  );
  const index = rows.findIndex(
    (t) =>
      t.includes(member.logical) &&
      t.includes(drawnMember.mission.missionRef.split(':').pop()!.slice(0, 11)),
  );
  assert.ok(
    index >= 0,
    `the drawn member's row is in the results of "${query}": ${JSON.stringify(rows)}`,
  );
  await p().keyboard.press('ArrowDown'); // into the results
  for (let i = 0; i < index; i++) await p().keyboard.press('ArrowDown');
  await p().keyboard.press('Enter');
  await p().waitForSelector(T('di-selected-member'));
  assert.equal(
    await p().$eval(T('di-find-input'), (el) => (el as HTMLInputElement).value),
    query,
    'the query is retained',
  );
  // The results are retained as a folded toggle that says how many (the list itself is one keystroke away).
  const toggle = (await p().textContent(T('di-find-toggle'))) ?? '';
  assert.match(toggle, /^Show \d+ results$/, 'the retained results are offered by a toggle');
  assert.ok(Number(/\d+/.exec(toggle)![0]) >= 3, 'the retained results are the multi-result Find');
}

/**
 * The usable-list invariants for one geometry, in ONE universal form for every state: at least one whole
 * Mission card (objective, status AND team) clear of the Missions region's fade; the panel never overflows;
 * the salient caveats are on the face. `find` says which state Find is in: "open" (the rows are there, and a
 * results region of at least one whole row is visible) or "folded" (a toggle that says how many).
 */
function listProblems(label: string, g: ListGeometry, find: 'open' | 'folded'): string[] {
  const out: string[] = [];
  if (g.panel === null || g.panel.overflowPx !== 0)
    out.push(`${label}: the panel overflows by ${g.panel?.overflowPx}px`);
  if (g.main === null) out.push(`${label}: no Missions region`);
  const wholeCards = g.cards.filter((c) => c.whole).length;
  if (g.selectedCard !== null && !g.selectedCard.whole)
    out.push(
      `${label}: the SELECTED Mission is not whole in the list (card ${g.selectedCard.top}..${g.selectedCard.bottom}, list scrollTop ${g.selectedCard.listScrollTop}, list ${g.main?.top}..${g.main?.usableBottom} usable)`,
    );
  if (wholeCards < 1)
    out.push(
      `${label}: no Mission card has its objective, status and team whole inside the Missions region's visible area (${g.main?.top}..${g.main?.usableBottom} usable, ${g.main?.visibleBottom} with the fade; the first card's team ends ${g.cards[0]?.teamBottomFromListTop}px below the list top)`,
    );
  if (!g.find.inputInViewport) out.push(`${label}: the Find input is not in the viewport`);
  if (find === 'open') {
    if (g.find.resultCount < 3)
      out.push(`${label}: the open Find results are gone (${g.find.resultCount} rows)`);
    if (g.find.results === null || g.find.results.clientHeight < 40 || !g.find.resultsInViewport)
      out.push(
        `${label}: the open Find results region is not usable (${JSON.stringify(g.find.results)}, in viewport ${g.find.resultsInViewport})`,
      );
  } else {
    const retained = /^Show (\d+) results$/.exec(g.find.toggle ?? '');
    if (!(retained && Number(retained[1]) >= 3))
      out.push(
        `${label}: Find's retained results are gone (${g.find.toggle ?? 'no toggle'}, ${g.find.resultCount} rows)`,
      );
  }
  if (!g.caveats.headerInView)
    out.push(`${label}: the office state header is not in view (${g.caveats.headerText})`);
  return out;
}

test('the Missions list keeps a usable height in EVERY state: a CONFIGURED request, an OFFERED review and a multi-result Find, open and folded', async () => {
  const { member } = drawnMember;
  const query = member.role;
  const problems: string[] = [];
  const measured: unknown[] = [];
  const allTraversal: unknown[] = [];
  const dockActions = [
    'di-open-inspector-selection',
    'di-show-in-office',
    'di-show-mission',
    'di-open-code-review',
  ];
  for (const vp of DOCK_VIEWPORTS) {
    const label = `${vp.width}x${vp.height}`;
    await p().setViewportSize(vp);
    await p().goto(url, { waitUntil: 'domcontentloaded' });
    await p().waitForSelector(T('di-office-panel'), { timeout: 30_000 });
    await offerReview(member.memberRef, member.taskId.value!, 'FIXTURE-listing-list');
    // configured request: the full entry (two actions) is what the harness mounts
    assert.ok(
      await p().$(T('di-mr-open')),
      'the Mission request entry is configured (its buttons are there)',
    );

    // STATE 1: the results are OPEN and nothing is selected yet.
    await typeMultiResultQuery(query);
    const g1 = await listGeometry();
    measured.push({ label: `${label} [1 open, nothing selected]`, ...g1 });
    problems.push(...listProblems(`${label} [open, nothing selected]`, g1, 'open'));

    // STATE 2: a result is chosen: the query stays, the list folds to its toggle.
    await chooseDrawnMemberRow(query);
    const g2 = await listGeometry();
    measured.push({ label: `${label} [2 selected, folded]`, ...g2 });
    problems.push(...listProblems(`${label} [selected, folded]`, g2, 'folded'));
    if (!g2.caveats.dockIdentityRowInDock)
      problems.push(
        `${label} [selected, folded]: the dock's identity / LIVE row is not whole in the dock's visible area`,
      );
    try {
      await assertDockDoorways(`${label} [selected, folded]`, dockActions);
    } catch (e) {
      problems.push(String((e as Error).message));
    }

    // STATE 3: the results are brought back (Arrow Down from the box) WITH a selection held: the worst case.
    await p().focus(T('di-find-input'));
    await p().keyboard.press('ArrowDown');
    await p().waitForFunction(
      () => document.activeElement?.getAttribute('data-testid') === 'di-find-result',
      undefined,
      { timeout: 5_000 },
    );
    const g3 = await listGeometry();
    measured.push({ label: `${label} [3 selected, results open]`, ...g3 });
    problems.push(...listProblems(`${label} [selected, results open]`, g3, 'open'));
    if (!g3.caveats.dockIdentityRowInDock)
      problems.push(
        `${label} [selected, results open]: the dock's identity / LIVE row is not whole in the dock's visible area`,
      );
    try {
      await assertDockDoorways(`${label} [selected, results open]`, dockActions);
    } catch (e) {
      problems.push(String((e as Error).message));
    }
    // Every result stays reachable by keyboard, each focused row scrolled into view WITHIN the results region.
    const count = g3.find.resultCount;
    const traversal = allTraversal;
    for (let i = 0; i < count; i++) {
      if (i > 0) await p().keyboard.press('ArrowDown');
      const at = await p().evaluate(() => {
        const a = document.activeElement as HTMLElement | null;
        const list = document.querySelector<HTMLElement>('[data-testid="di-find-results"]');
        if (!a || !list) return null;
        const r = a.getBoundingClientRect();
        const l = list.getBoundingClientRect();
        const r1 = (n: number) => Math.round(n * 100) / 100;
        // The list's CLIPPED scrolling client area (inside its border, without a scrollbar), as the dock
        // check does with clientTop and clientHeight, not its border box.
        const clientTop = l.top + list.clientTop;
        const clientBottom = clientTop + list.clientHeight;
        return {
          isResult: a.getAttribute('data-testid') === 'di-find-result',
          row: [r1(r.top), r1(r.bottom)],
          listClient: [r1(clientTop), r1(clientBottom)],
          listBorderBox: [r1(l.top), r1(l.bottom)],
          scroll: {
            top: list.scrollTop,
            height: list.scrollHeight,
            client: list.clientHeight,
            max: list.scrollHeight - list.clientHeight,
          },
          // how far the row extends past each edge of the client area (positive = clipped)
          pastTop: r1(clientTop - r.top),
          pastBottom: r1(r.bottom - clientBottom),
          inList: r.top >= clientTop - 0.5 && r.bottom <= clientBottom + 0.5,
          inViewport: r.top >= 0 && r.bottom <= window.innerHeight,
        };
      });
      traversal.push({ label, result: i + 1, ...at });
      if (!at || !at.isResult || !at.inList || !at.inViewport)
        problems.push(
          `${label}: result ${i + 1} of ${count}, focused by Arrow Down, is not scrolled into view inside the results region (${JSON.stringify(at)})`,
        );
    }
  }
  console.log(`[list geometry] ${JSON.stringify(measured)}`);
  console.log(`[results traversal] ${JSON.stringify(allTraversal)}`);
  assert.deepEqual(
    problems,
    [],
    `the Missions list is not usable in every state:\n${problems.join('\n')}`,
  );
});

test('Find: choosing a result folds its list into a toggle; the query and the statement stay; every way back works by keyboard', async () => {
  const { member } = drawnMember;
  assert.notEqual(await tabTo(T('di-find-input')), null);
  await p().keyboard.type(member.role);
  await p().waitForFunction(
    () => document.querySelectorAll('[data-testid="di-find-result"]').length >= 3,
    undefined,
    { timeout: 5_000 },
  );
  const count = (await p().$$(T('di-find-result'))).length;
  const statement = await p().$eval(T('di-find-outcome'), (el) => el.textContent ?? '');
  assert.equal(await p().getAttribute(T('di-find-toggle'), 'aria-expanded'), 'true');
  assert.equal(await p().textContent(T('di-find-toggle')), 'Hide results');
  // Arrow Down enters the results; Enter selects the first row.
  await p().keyboard.press('ArrowDown');
  assert.ok(await active(T('di-find-result')));
  await p().keyboard.press('Enter');
  await p().waitForSelector(
    '[data-testid="di-selected-member"], [data-testid="di-selected-runtime-agent"], [data-testid="di-selected-unverified"]',
  );
  // Folded: no list, one toggle that says how many; the query and the statement are retained; focus is on the toggle.
  assert.equal((await p().$$(T('di-find-result'))).length, 0, 'the list is folded away');
  assert.equal(await p().getAttribute(T('di-find-toggle'), 'aria-expanded'), 'false');
  assert.equal(await p().textContent(T('di-find-toggle')), `Show ${count} results`);
  assert.equal(
    await p().$eval(T('di-find-input'), (el) => (el as HTMLInputElement).value),
    member.role,
  );
  assert.equal(await p().$eval(T('di-find-outcome'), (el) => el.textContent ?? ''), statement);
  assert.ok(await active(T('di-find-toggle')), 'focus moved to the toggle, never to nowhere');
  // Way back 1: Enter on the toggle.
  await p().keyboard.press('Enter');
  assert.equal((await p().$$(T('di-find-result'))).length, count);
  assert.equal(await p().getAttribute(T('di-find-toggle'), 'aria-expanded'), 'true');
  // Fold again with the toggle, then way back 2: Arrow Down from the box reopens AND lands on the first result.
  await p().keyboard.press('Enter');
  assert.equal((await p().$$(T('di-find-result'))).length, 0);
  await p().focus(T('di-find-input'));
  await p().keyboard.press('ArrowDown');
  await p().waitForFunction(
    () => document.activeElement?.getAttribute('data-testid') === 'di-find-result',
    undefined,
    { timeout: 5_000 },
  );
  assert.equal((await p().$$(T('di-find-result'))).length, count);
  // Way back 3: changing the query shows its results again (select, then type one more character).
  await p().keyboard.press('Enter');
  await p().waitForFunction(
    () => document.querySelectorAll('[data-testid="di-find-result"]').length === 0,
  );
  await p().focus(T('di-find-input'));
  await p().keyboard.press('End');
  await p().keyboard.type('1');
  await p().waitForFunction(
    () => document.querySelector('[data-testid="di-find-outcome"]') !== null,
    undefined,
    { timeout: 5_000 },
  );
  const refined = await p().$$(T('di-find-result'));
  const noMatch = /No Mission or agent matches/.test(
    await p().$eval(T('di-find-outcome'), (el) => el.textContent ?? ''),
  );
  assert.ok(
    refined.length > 0 || noMatch,
    'a changed query shows its own results or says none match',
  );
});
