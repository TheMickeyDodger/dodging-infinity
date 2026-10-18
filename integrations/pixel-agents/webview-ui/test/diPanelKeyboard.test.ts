/**
 * C1 (items 08/09 with 04 and 12): the DI panel driven by REAL key presses in a real browser.
 *
 * The page (`test/harness/di-panel.html`) mounts the real `DiOfficePanel` over the office state
 * the real server projects from the labelled packaged fixture `herdr-task-session-mismatch-v2`.
 * Nothing here asserts that a handler exists: every test presses keys and reads what the browser
 * focused, what got selected, what the inspector shows and the COMPUTED focus outline.
 *
 * Browser: the installed Chrome (playwright-core, channel `chrome`), else Playwright's own
 * Chromium; with neither, every test is skipped with an explicit message.
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

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const fixture = JSON.parse(
  fs.readFileSync(
    path.join(root, 'test/fixtures/di-office-state-session-mismatch-v2.FIXTURE.json'),
    'utf-8',
  ),
) as { state: DiOfficeState };
const runtimeAgents = fixture.state.runtimeAgents!.agents;
const verified = runtimeAgents.filter((a) => a.characterId !== null);
const unverified = runtimeAgents.filter((a) => !a.identityVerified);
const drawnMembers = fixture.state.missions.missions.flatMap((m) =>
  m.members.members.filter((x) => x.characterId !== null).map((x) => ({ member: x, mission: m })),
);

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
  try {
    browser = await chromium.launch({ channel: 'chrome', headless: true });
  } catch {
    try {
      browser = await chromium.launch({ headless: true });
    } catch (e) {
      unavailable = `no Chrome or Playwright Chromium to drive: ${String(e).split('\n')[0]}`;
    }
  }
}, 120_000);

afterAll(async () => {
  await browser?.close();
  await server?.close();
});

beforeEach(async (ctx) => {
  if (unavailable) {
    console.warn(`SKIPPED (browser regression): ${unavailable}`);
    ctx.skip();
    return;
  }
  await page?.close();
  page = await browser!.newPage({ viewport: { width: 1600, height: 1000 } });
  await page.goto(url, { waitUntil: 'domcontentloaded' });
  await page.waitForSelector('[data-testid="di-office-panel"]', { timeout: 30_000 });
});

const p = () => page!;
const active = (selector: string) =>
  p().evaluate((s) => document.activeElement?.matches(s) ?? false, selector);
const activeText = () => p().evaluate(() => document.activeElement?.textContent ?? '');

/**
 * Press Tab until the focused element matches; the number of presses (logged as evidence of the
 * Tab burden in this crowded fixture), or null.
 */
async function tabTo(selector: string, max = 1000): Promise<number | null> {
  for (let i = 1; i <= max; i++) {
    await p().keyboard.press('Tab');
    if (await active(selector)) {
      console.log(`[C1 Tab presses] ${selector}: ${i}`);
      return i;
    }
  }
  return null;
}

async function focusOutline() {
  return p().evaluate(() => {
    const s = getComputedStyle(document.activeElement as Element);
    return { style: s.outlineStyle, width: s.outlineWidth, color: s.outlineColor };
  });
}

const selectedId = () => p().evaluate(() => window.__diHarness.office.selectedAgentId);
const cameraId = () => p().evaluate(() => window.__diHarness.office.cameraFollowId);
const text = (testid: string) =>
  p().evaluate((t) => document.querySelector(`[data-testid="${t}"]`)?.textContent ?? null, testid);
/** The panel polls the office selection: wait until the inspector shows `needle`. */
async function inspectorShows(testid: string, needle: string): Promise<string> {
  await p().waitForFunction(
    ([t, n]) => (document.querySelector(`[data-testid="${t}"]`)?.textContent ?? '').includes(n),
    [testid, needle] as const,
    { timeout: 5_000 },
  );
  return (await text(testid)) ?? '';
}

// -- item 04: the runtime row (the gap the live proof found) ------------------------------

test('Tab reaches a runtime agent row; Enter selects it on the map and in the inspector', async () => {
  const presses = await tabTo('[data-testid="di-runtime-row"]');
  assert.notEqual(presses, null, 'Tab never reached a runtime agent row');
  const label = await activeText();
  const agent = verified.find((a) => label.includes(a.agent) && label.includes(a.logical));
  assert.ok(agent, `the focused row names a verified runtime agent: ${label}`);
  await p().keyboard.press('Enter');
  const shown = await inspectorShows('di-selected-runtime-agent', agent.agent);
  assert.match(shown, /Not a team member/);
  assert.equal(await selectedId(), agent.characterId);
  assert.equal(await cameraId(), agent.characterId); // the Map route: the camera follows it
});

test('arrow keys, Home and End move between runtime rows; Space selects', async () => {
  assert.notEqual(await tabTo('[data-testid="di-runtime-row"]'), null);
  const first = await activeText();
  await p().keyboard.press('ArrowDown');
  assert.ok(await active('[data-testid="di-runtime-row"]'), 'ArrowDown stays on a runtime row');
  const second = await activeText();
  assert.notEqual(second, first, 'ArrowDown moved focus to another row');
  await p().keyboard.press('ArrowUp');
  assert.equal(await activeText(), first);
  await p().keyboard.press('End');
  const last = await activeText();
  await p().keyboard.press('Home');
  assert.equal(await activeText(), first);
  await p().keyboard.press('End');
  assert.equal(await activeText(), last);
  await p().keyboard.press(' ');
  const agent = verified.find((a) => last.includes(a.agent) && last.includes(a.logical));
  assert.ok(agent, `the last runtime row names a verified agent: ${last}`);
  await inspectorShows('di-selected-runtime-agent', agent.agent);
  assert.equal(await selectedId(), agent.characterId);
});

test('keyboard focus on a row is VISIBLE: the same 2px solid accent outline as a reception entry', async () => {
  assert.notEqual(await tabTo('[data-testid="di-reception-entry"]'), null);
  const reception = await focusOutline();
  assert.equal(reception.style, 'solid');
  assert.equal(reception.width, '2px');
  assert.notEqual(await tabTo('[data-testid="di-runtime-row"]'), null);
  assert.deepEqual(await focusOutline(), reception);
  assert.notEqual(await tabTo('[data-testid="di-member-row"]'), null);
  assert.deepEqual(await focusOutline(), reception);
});

test('Tab reaches a team member row; Enter selects that member', async () => {
  assert.notEqual(
    await tabTo('[data-testid="di-member-row"]'),
    null,
    'Tab never reached a member row',
  );
  const label = await activeText();
  const hit = drawnMembers.find(({ member }) => label.includes(member.logical));
  assert.ok(hit, `the focused row names a drawn member: ${label}`);
  await p().keyboard.press('Enter');
  await p().waitForFunction(
    (id) => window.__diHarness.office.selectedAgentId === id,
    hit.member.characterId,
    { timeout: 5_000 },
  );
  const shown = await inspectorShows('di-selected-member', hit.member.logical);
  assert.match(shown, /Team member/);
});

test('the practical keyboard route to a team member: find its identifier, Arrow Down, Enter', async () => {
  const { member } = drawnMembers[0];
  const presses = await tabTo('[data-testid="di-find-input"]');
  assert.ok(presses !== null && presses <= 5, `the find box is near the top (${presses} presses)`);
  await p().keyboard.type(member.memberRef);
  assert.match((await text('di-find-outcome')) ?? '', /^1 record matches/);
  await p().keyboard.press('ArrowDown');
  await p().keyboard.press('Enter');
  await inspectorShows('di-selected-member', member.logical);
  assert.equal(await selectedId(), member.characterId);
  assert.equal(await cameraId(), member.characterId);
});

// -- the unverified listing ------------------------------------------------------------------

test('the unverified listing: every record listed, reachable by keyboard, opened in the inspector, never drawn', async () => {
  assert.equal(unverified.length, 8);
  const rows = await p().$$('[data-testid="di-unverified-row"]');
  assert.equal(rows.length, unverified.length);
  // Rows that share a name stay visibly distinct records (each names its root).
  const rowTexts = await p().$$eval('[data-testid="di-unverified-row"]', (els) =>
    els.map((e) => e.textContent ?? ''),
  );
  assert.equal(new Set(rowTexts).size, rowTexts.length);
  const listing = (await text('di-unverified')) ?? '';
  assert.match(listing, /not verified/i);
  // round 11: the observation is qualified by its freshness ('A live native agent named …').
  assert.match(listing, /native agent named/);
  // The four-agent gap stays explicit: names, panes and timestamps do not make them verified.
  assert.match(listing, /session_replaced|recorded session/);
  assert.notEqual(await tabTo('[data-testid="di-unverified-row"]'), null);
  const label = await activeText();
  await p().keyboard.press('Enter');
  const shown = await inspectorShows('di-selected-unverified', 'not verified');
  assert.match(shown, /Not drawn/);
  assert.match(shown, /not established/i);
  assert.equal(await selectedId(), null, 'an unverified record never becomes a drawn selection');
  const record = unverified.find((a) => label.includes(a.agent));
  assert.ok(record);
  // Provenance (the raw reason, the recorded session) is behind a labelled disclosure.
  const html = await p().evaluate(
    () => document.querySelector('[data-testid="di-selected-unverified"]')?.innerHTML ?? '',
  );
  const details = html.indexOf('<details');
  assert.ok(details > 0);
  assert.ok(html.indexOf(record.identityReason) > details);
});

// -- item 08: find ----------------------------------------------------------------------------

test('find: a role name lists every matching record separately; arrow keys reach them; Enter selects', async () => {
  const input = await tabTo('[data-testid="di-find-input"]');
  assert.notEqual(input, null, 'Tab never reached the find box');
  await p().keyboard.type('lead1');
  const outcome = (await text('di-find-outcome')) ?? '';
  const expected =
    runtimeAgents.filter((a) => a.logical === 'lead1').length +
    fixture.state.missions.missions.reduce(
      (n, m) => n + m.members.members.filter((x) => x.logical === 'lead1').length,
      0,
    );
  assert.match(outcome, new RegExp(`${expected} `));
  assert.match(outcome, /different records/i); // ambiguous: never merged
  await p().keyboard.press('ArrowDown');
  assert.ok(await active('[data-testid="di-find-result"]'), 'ArrowDown enters the results');
  await p().keyboard.press('End');
  await p().keyboard.press('Home');
  assert.ok(await active('[data-testid="di-find-result"]'));
  await p().keyboard.press('Enter');
  await p().waitForFunction(
    () =>
      document.querySelector(
        '[data-testid="di-selected-member"], [data-testid="di-selected-runtime-agent"], [data-testid="di-selected-unverified"]',
      ) !== null,
    undefined,
    { timeout: 5_000 },
  );
});

test('find: no match says so; a Mission objective finds that Mission', async () => {
  assert.notEqual(await tabTo('[data-testid="di-find-input"]'), null);
  await p().keyboard.type('zz-no-such-record-zz');
  assert.match((await text('di-find-outcome')) ?? '', /No Mission or agent matches/);
  assert.equal((await p().$$('[data-testid="di-find-result"]')).length, 0);
  const objective = fixture.state.missions.missions.find(
    (m) => m.objective.state === 'observed' && m.objective.value,
  )!.objective.value!;
  await p().fill('[data-testid="di-find-input"]', objective.slice(0, 24));
  const results = await p().$$eval('[data-testid="di-find-result"]', (els) =>
    els.map((e) => e.textContent ?? ''),
  );
  assert.ok(results.some((r) => r.includes('Mission')));
});

// -- selection across updates -------------------------------------------------------------

test('selection survives a reordering update and says plainly when its agent leaves the map or the state', async () => {
  assert.notEqual(await tabTo('[data-testid="di-runtime-row"]'), null);
  const label = await activeText();
  const agent = verified.find((a) => label.includes(a.agent) && label.includes(a.logical))!;
  await p().keyboard.press('Enter');
  await inspectorShows('di-selected-runtime-agent', agent.agent);
  // 1. A later projection lists the agents in another order: the same agent stays selected.
  await p().evaluate(() =>
    window.__diHarness.update((s) => {
      s.runtimeAgents!.agents.reverse();
      return s;
    }),
  );
  await inspectorShows('di-selected-runtime-agent', agent.agent);
  assert.equal(await selectedId(), agent.characterId);
  // 2. The agent is no longer drawn (office not live): still selected, and said so.
  await p().evaluate(
    (ref) =>
      window.__diHarness.update((s) => {
        const a = s.runtimeAgents!.agents.find((x) => x.agentRef === ref)!;
        a.characterId = null;
        a.live = false;
        a.notLiveReason = 'transport_reconnecting';
        return s;
      }),
    agent.agentRef,
  );
  const now = await inspectorShows('di-selected-runtime-agent', agent.agent);
  assert.match(now, /not drawn/i);
  // 3. The agent leaves the office state: the inspector says so, and selects nothing else.
  await p().evaluate(
    (ref) =>
      window.__diHarness.update((s) => {
        s.runtimeAgents!.agents = s.runtimeAgents!.agents.filter((x) => x.agentRef !== ref);
        return s;
      }),
    agent.agentRef,
  );
  const gone = await inspectorShows('di-selection-gone', agent.logical);
  assert.match(gone, /no longer in the office state/);
});
