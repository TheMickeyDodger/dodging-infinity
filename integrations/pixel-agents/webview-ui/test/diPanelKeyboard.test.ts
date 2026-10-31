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
import { a11ySweep } from './diChromeA11y.ts';
import { occlusion } from './diOcclusion.ts';
import { reflowReach } from './diReflow.ts';
import { renderedCopy } from './diRenderedCopy.ts';
import { tabProblems, tabTraversal } from './diTabTraversal.ts';
import { textFit } from './diTextFit.ts';
import { tokenScopeAudit } from './diTokenScope.ts';

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

/**
 * Wait until the inspector's RENDERED text matches `pattern`, then return it. `inspectorShows` waits only for a name that is on screen both before and
 * after an update, so it cannot tell a render that has caught up from one that has not; a state change is awaited by the text it must produce.
 */
async function inspectorMatches(testid: string, pattern: RegExp): Promise<string> {
  await p().waitForFunction(
    ([t, source, flags]) =>
      new RegExp(source, flags).test(
        document.querySelector(`[data-testid="${t}"]`)?.textContent ?? '',
      ),
    [testid, pattern.source, pattern.flags] as const,
    { timeout: 5_000 },
  );
  return (await text(testid)) ?? '';
}

/**
 * The ONE runtime record a row names: its first-layer name is "<logical> · <root alias>" (source-
 * qualified only when the list holds several sources; this fixture holds one). Exactly one record
 * must match — an ambiguous row is a failure, not a pick.
 */
function rowAgent<A extends { logical: string; rootAliases: string[] }>(
  records: readonly A[],
  label: string,
): A | undefined {
  const hits = records.filter((a) => label.startsWith(`${a.logical} · ${a.rootAliases[0]}`));
  assert.ok(hits.length <= 1, `the row names one record, not ${hits.length}: ${label}`);
  return hits[0];
}

// -- item 04: the runtime row (the gap the live proof found) ------------------------------

test('Tab reaches a runtime agent row; Enter selects it on the map and in the inspector', async () => {
  const presses = await tabTo('[data-testid="di-runtime-row"]');
  assert.notEqual(presses, null, 'Tab never reached a runtime agent row');
  const label = await activeText();
  const agent = rowAgent(verified, label);
  assert.ok(agent, `the focused row names a verified runtime agent: ${label}`);
  await p().keyboard.press('Enter');
  const shown = await inspectorShows('di-selected-runtime-agent', agent.agent);
  assert.match(shown, /Not shown as a team member/);
  assert.doesNotMatch(shown, /\bnot a team member\b/i);
  assert.equal(await selectedId(), agent.characterId);
  assert.equal(await cameraId(), agent.characterId); // the Map route: the camera follows it
  // The inspector's heading names the kind with the same strength: not SHOWN as a team member.
  await p().click('[data-testid="di-open-inspector-selection"]');
  await p().waitForSelector('[data-testid="di-inspector-kind"]');
  const kind = (await text('di-inspector-kind')) ?? '';
  assert.match(kind, /Agent, not shown as a team member/);
  assert.doesNotMatch(kind, /\bnot a team member\b/i);
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
  const agent = rowAgent(verified, last);
  assert.ok(agent, `the last runtime row names a verified agent: ${last}`);
  await inspectorShows('di-selected-runtime-agent', agent.agent);
  assert.equal(await selectedId(), agent.characterId);
});

/**
 * feature13: a Mission card's team roster is a CLOSED disclosure ("Team members (n)"), off the card
 * face by default. Reach it as a person does: Tab to its summary, check it is closed and its rows
 * are not shown, open it with Enter. Returns the Tab presses it took.
 */
async function openFirstRoster(): Promise<number> {
  const presses = await tabTo('[data-testid="di-mission-roster"] > summary');
  assert.notEqual(presses, null, 'Tab never reached a team roster disclosure');
  const before = await p().evaluate(() => {
    const d = document.activeElement!.parentElement as HTMLDetailsElement;
    const row = d.querySelector('[data-testid="di-member-row"]');
    // checkVisibility(): false inside a closed disclosure (its content is not rendered).
    return { open: d.open, rowShown: row ? row.checkVisibility() : null };
  });
  assert.deepEqual(before, { open: false, rowShown: false }, 'the roster is off the card face');
  assert.match(
    await activeText(),
    /^Team \(\d+\): \S.*$/,
    'the disclosure names itself and its size',
  );
  await p().keyboard.press('Enter');
  assert.equal(
    await p().evaluate(() => (document.activeElement!.parentElement as HTMLDetailsElement).open),
    true,
    'Enter opens the roster',
  );
  return presses!;
}

test('keyboard focus on a row is VISIBLE: the same 2px solid accent outline on every list control', async () => {
  await openFirstRoster();
  const summary = await focusOutline();
  assert.equal(summary.style, 'solid');
  assert.equal(summary.width, '2px');
  assert.notEqual(await tabTo('[data-testid="di-member-row"]'), null);
  const member = await focusOutline();
  assert.deepEqual(member, summary);
  // The reception entries live in the ONE reception inbox (a dialog) rather than inline in the panel:
  // open it from the keyboard at the sidebar entry; an entry there carries the same outline.
  await p().focus('[data-testid="di-open-reception"]');
  await p().keyboard.press('Enter');
  await p().waitForSelector('[data-testid="di-reception-inbox"]');
  assert.notEqual(await tabTo('[data-testid="di-reception-entry"]'), null);
  assert.deepEqual(await focusOutline(), member);
  await p().keyboard.press('Escape');
  await p().waitForSelector('[data-testid="di-reception-inbox"]', { state: 'detached' });
  assert.ok(
    await active('[data-testid="di-open-reception"]'),
    'focus returns to the sidebar entry',
  );
  assert.notEqual(await tabTo('[data-testid="di-runtime-row"]'), null);
  assert.deepEqual(await focusOutline(), member);
});

test('Tab reaches a team member row (once its roster is opened); Enter selects that member', async () => {
  await openFirstRoster();
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
  assert.match(listing, /can’t be safely linked to its recorded role/);
  // round 11: the observation is qualified by its freshness (its time, said once for the section).
  assert.match(listing, /An agent with each name was found running/);
  // The four-agent gap stays explicit: names, panes and timestamps do not make them verified.
  assert.match(listing, /session_replaced|recorded session/);
  assert.notEqual(await tabTo('[data-testid="di-unverified-row"]'), null);
  const label = await activeText();
  await p().keyboard.press('Enter');
  const shown = await inspectorShows('di-selected-unverified', 'can’t be safely linked');
  assert.match(shown, /Not drawn/);
  assert.match(shown, /can’t be safely linked to its recorded role/);
  // The dock states the link is not established and then claims no more than that: not SHOWN as a team member.
  assert.match(shown, /Not drawn; not shown as a team member\./);
  assert.doesNotMatch(shown, /\bnot a team member\b/i);
  assert.equal(await selectedId(), null, 'an unverified record never becomes a drawn selection');
  const record = rowAgent(unverified, label);
  assert.ok(record);
  // Provenance (the raw reason, the recorded session) is behind a labelled disclosure. feature13:
  // the dock face carries NONE of it (stronger than before); the inspector holds it, after the
  // plain outcome and behind its disclosure.
  const dockFace = (await text('di-selected-unverified')) ?? '';
  assert.ok(!dockFace.includes(record.identityReason), 'no raw reason code on the dock face');
  if (record.session) assert.ok(!dockFace.includes(record.session), 'no raw session on the face');
  await p().click('[data-testid="di-open-inspector-selection"]');
  await p().waitForSelector('[data-testid="di-inspector-selected"]');
  assert.match((await text('di-inspector-now')) ?? '', /can’t be safely linked/);
  const inspectorFace = (await text('di-inspector-selected')) ?? '';
  assert.match(inspectorFace, /Not drawn; not shown as a team member\./);
  assert.doesNotMatch(inspectorFace, /\bnot a team member\b/i);
  await p().click('[data-testid="di-inspector-record"] > summary');
  await p().waitForSelector('[data-testid="di-inspector-unverified"]');
  // The labelled disclosure is "Everything recorded" (one step): the record is inside it, and
  // within the record the plain outcome precedes the raw provenance.
  assert.ok(
    await p().$('[data-testid="di-inspector-record"] [data-testid="di-inspector-unverified"]'),
    'the record is behind its labelled disclosure',
  );
  const html = await p().evaluate(
    () => document.querySelector('[data-testid="di-inspector-unverified"]')?.innerHTML ?? '',
  );
  const details = html.indexOf('data-testid="di-runtime-provenance"');
  assert.ok(details > 0);
  assert.ok(html.indexOf('Not established') < details, 'the plain outcome precedes the provenance');
  assert.ok(html.indexOf(record.identityReason) > details);
  if (record.session) assert.ok(html.indexOf(record.session) > details);
  await p().keyboard.press('Escape');
  await p().waitForSelector('[role="dialog"]', { state: 'detached' });
  assert.equal(await selectedId(), null, 'closing the inspector selects nothing');
});

/**
 * Criterion 6: every recorded identifier is reachable in ONE deliberate step. From a runtime row:
 * select it, open its details, open "Everything recorded" ONCE; the provenance must then be
 * VISIBLE (rendered, not merely in the markup), with this record's own values in its rendered text.
 */
async function provenanceAfterOneActivation(
  rowTestid: string,
  records: readonly (typeof runtimeAgents)[number][],
) {
  assert.notEqual(await tabTo(`[data-testid="${rowTestid}"]`), null);
  const label = await activeText();
  const agent = rowAgent(records, label);
  assert.ok(agent, `the focused row names one record: ${label}`);
  await p().keyboard.press('Enter');
  await p().click('[data-testid="di-open-inspector-selection"]');
  await p().waitForSelector('[data-testid="di-inspector-selected"]');
  await p().click('[data-testid="di-inspector-record"] > summary');
  // Its CONTENT must be rendered (a closed <details> is itself visible: its summary shows).
  const seen = await p().evaluate(() => ({
    visible:
      document
        .querySelector('[data-testid="di-runtime-provenance"] .di-op-detail-body')
        ?.checkVisibility() ?? false,
    rendered:
      document.querySelector<HTMLElement>('[data-testid="di-inspector-record"]')?.innerText ?? '',
  }));
  assert.equal(seen.visible, true, 'the provenance is visible after one activation');
  const values = [
    agent.agentRef,
    agent.sourceId,
    agent.rootRef,
    agent.identityReason,
    agent.assignmentReason,
    agent.session,
    agent.taskRef,
    agent.paneId,
    agent.workspaceId,
  ].filter((v): v is string => typeof v === 'string' && v !== '');
  for (const value of values)
    assert.ok(seen.rendered.includes(value), `"${value}" is rendered after one activation`);
}

test('runtime provenance after ONE disclosure: a verified record', async () => {
  await provenanceAfterOneActivation('di-runtime-row', verified);
});

test('runtime provenance after ONE disclosure: a not-verified record', async () => {
  await provenanceAfterOneActivation('di-unverified-row', unverified);
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
  const agent = rowAgent(verified, label)!;
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
  // await the render this state change must produce (the name alone is on screen before and after it), then assert the exact words
  const now = await inspectorMatches('di-selected-runtime-agent', /not drawn/i);
  assert.match(now, /not drawn/i);
  assert.ok(now.includes(agent.agent), 'still the same selected agent');
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

// -- Mission 1 UI alignment: durable coverage (brief 6.3) --------------------------------------
// Each test below was first run against the 109e096c code and FAILED there (recorded in the task evidence);
// each asserts on distinct values, so a reset or a jump cannot pass for the thing kept.

/** Press Shift+Tab until the focused element matches (the real keyboard path back to a header control). */
async function shiftTabTo(
  selector: string,
  max = 400,
): Promise<{ presses: number; lastBefore: string } | null> {
  let lastBefore = 'BODY';
  for (let i = 1; i <= max; i++) {
    const now = await p().evaluate(() => {
      const a = document.activeElement;
      return a === null || a === document.body
        ? 'BODY'
        : (a.getAttribute('data-testid') ??
            `${a.tagName}:${(a.textContent ?? '').trim().slice(0, 30)}`);
    });
    await p().keyboard.press('Shift+Tab');
    if (await active(selector)) return { presses: i, lastBefore: now };
    lastBefore = now;
  }
  void lastBefore;
  return null;
}
/** The focus fields a restore control records (evidence: what had focus when it hid, and where focus is to return). */
const recorded = (selector: string) =>
  p().evaluate((sel) => {
    const el = document.querySelector(sel);
    return {
      atHide: el?.getAttribute('data-focus-at-hide') ?? null,
      returnTo: el?.getAttribute('data-focus-return') ?? null,
    };
  }, selector);

/** Reload the harness with the edge strip of minimized surfaces mounted as App mounts it (`?strip=1`). */
async function withStrip(extra = '') {
  await p().goto(`${url}?strip=1${extra}`, { waitUntil: 'domcontentloaded' });
  await p().waitForSelector('[data-testid="di-office-panel"]', { timeout: 30_000 });
}

/** What the office panel shows of itself and of its focus: the fields an AC3 probe compares. */
const panelView = () =>
  p().evaluate(() => {
    const panel = document.querySelector<HTMLElement>('[data-testid="di-office-panel"]');
    const main = document.querySelector<HTMLElement>('[data-testid="di-op-main"]');
    const roster = document.querySelector<HTMLDetailsElement>('[data-testid="di-mission-roster"]');
    const a = document.activeElement;
    return {
      panelShown: !!panel && panel.getClientRects().length > 0,
      scrollTop: main && main.getClientRects().length > 0 ? main.scrollTop : null,
      rosterOpen: roster ? roster.open : null,
      selected: window.__diHarness.office.selectedAgentId,
      find:
        document.querySelector<HTMLInputElement>('[data-testid="di-find-input"]')?.value ?? null,
      focus:
        a === null || a === document.body ? 'BODY' : (a.getAttribute('data-testid') ?? a.tagName),
      focusName: (a?.textContent ?? '').trim(),
    };
  });

test('AC3 office panel: minimize keeps scroll, the open roster, the selection and the typed find; focus goes to Restore and back, never to the page', async () => {
  await withStrip();
  // a selection, an opened roster, typed find text and a scrolled list, then focus on the panel's own minimize control
  await openFirstRoster();
  await p().evaluate(() => {
    const d = document.querySelector<HTMLDetailsElement>('[data-testid="di-mission-roster"]');
    if (d) d.open = true;
  });
  const { member } = drawnMembers[0];
  await p().fill('[data-testid="di-find-input"]', member.logical);
  await p().waitForSelector('[data-testid="di-find-result"]');
  await p().focus('[data-testid="di-find-result"]');
  await p().keyboard.press('Enter');
  await p().waitForFunction(
    (id) => window.__diHarness.office.selectedAgentId === id,
    member.characterId,
    { timeout: 5_000 },
  );
  await p().waitForTimeout(400); // the list settles (a held selection keeps its Mission's card at the top on a resize)
  const target = await p().evaluate(() => {
    const main = document.querySelector<HTMLElement>('[data-testid="di-op-main"]')!;
    const card = main.querySelector<HTMLElement>('[data-selected-mission="true"]');
    const cardTop = card ? card.offsetTop - main.offsetTop : -1;
    const max = main.scrollHeight - main.clientHeight;
    let want = Math.min(max, 173);
    if (Math.abs(want - cardTop) < 8) want = Math.max(1, want - 37);
    main.scrollTop = want;
    return { want: main.scrollTop, cardTop, max };
  });
  assert.ok(
    target.want > 0 && target.want !== target.cardTop,
    `a distinct scroll position: ${JSON.stringify(target)}`,
  );
  // the person is in the Find box, then takes the real keyboard path to Minimize (Shift+Tab, then Enter)
  await p().focus('[data-testid="di-find-input"]');
  const path = await shiftTabTo('[data-testid="di-office-panel"] header button');
  assert.ok(path, 'Shift+Tab reaches Minimize');
  const before = await panelView();
  assert.equal(before.scrollTop, target.want);
  assert.equal(before.rosterOpen, true);
  assert.equal(before.find, member.logical);

  await p().keyboard.press('Enter');
  await p().waitForSelector('[data-testid="di-office-panel-collapsed"]');
  const minimized = await panelView();
  assert.equal(minimized.panelShown, false, 'minimized: the panel is not shown');
  assert.equal(
    minimized.focus,
    'di-office-panel-collapsed',
    `minimized: focus is on the control that restores the panel, not ${minimized.focus}`,
  );
  assert.deepEqual(
    await recorded('[data-testid="di-office-panel-collapsed"]'),
    { atHide: 'di-office-panel-minimize', returnTo: path!.lastBefore },
    'recorded: Minimize had focus at hide; the last panel control before it is where focus returns',
  );

  await p().keyboard.press('Enter');
  await p().waitForFunction(() => {
    const panel = document.querySelector('[data-testid="di-office-panel"]');
    return !!panel && panel.getClientRects().length > 0;
  });
  await p().waitForTimeout(300);
  const after = await panelView();
  assert.equal(
    after.scrollTop,
    before.scrollTop,
    `restored: the list is where it was (${before.scrollTop}), not reset or at the card (${target.cardTop})`,
  );
  assert.equal(after.rosterOpen, true, 'restored: the opened roster is still open');
  assert.equal(after.selected, before.selected, 'restored: the same selection');
  assert.equal(after.find, before.find, 'restored: the typed find text');
  assert.notEqual(after.focus, 'BODY', 'restored: focus is on a control, never the page');
  // contract (b): back on the last panel control that had focus before the toggle, not parked on the toggle
  assert.notEqual(path!.lastBefore, 'di-office-panel-minimize');
  assert.equal(
    after.focus,
    path!.lastBefore,
    `restored: focus is back on ${path!.lastBefore}, not ${after.focus} "${after.focusName}"`,
  );
});

test('AC3 office panel, mouse: typed in Find, clicked Minimize, clicked Restore: focus is back in the Find box', async () => {
  await withStrip();
  await p().click('[data-testid="di-find-input"]');
  await p().keyboard.type('lead');
  await p().click('[data-testid="di-office-panel-minimize"]');
  await p().waitForSelector('[data-testid="di-office-panel-collapsed"]');
  assert.deepEqual(await recorded('[data-testid="di-office-panel-collapsed"]'), {
    atHide: 'di-office-panel-minimize',
    returnTo: 'di-find-input',
  });
  await p().click('[data-testid="di-office-panel-collapsed"]');
  await p().waitForSelector('[data-testid="di-office-panel-minimize"]');
  const after = await panelView();
  assert.equal(
    after.focus,
    'di-find-input',
    `restored: focus is back in the Find box, not ${after.focus}`,
  );
  assert.equal(after.find, 'lead');
});

/** Minimize with the mouse, run `meanwhile` while the panel is minimized, restore with the mouse; where focus lands. */
async function officeRoundTrip(meanwhile: () => Promise<void>) {
  await p().click('[data-testid="di-office-panel-minimize"]');
  await p().waitForSelector('[data-testid="di-office-panel-collapsed"]');
  const kept = await recorded('[data-testid="di-office-panel-collapsed"]');
  await meanwhile();
  await p().click('[data-testid="di-office-panel-collapsed"]');
  await p().waitForSelector('[data-testid="di-office-panel-minimize"]');
  return { kept, after: await panelView() };
}

test('AC3 focus fallbacks, office panel: when the remembered control is absent, removed, disabled or hidden, focus lands on Minimize, never the page', async () => {
  await withStrip();
  // absent: nothing in the panel ever had focus
  {
    const { kept, after } = await officeRoundTrip(async () => {});
    assert.equal(kept.returnTo, 'none (the Minimize toggle)');
    assert.equal(after.focus, 'di-office-panel-minimize', `absent: ${after.focus}`);
  }
  // disabled: the remembered control is disabled while the panel is minimized
  {
    await p().focus('[data-testid="di-live-only"]');
    const { kept, after } = await officeRoundTrip(async () => {
      await p().evaluate(() =>
        document.querySelector('[data-testid="di-live-only"]')!.setAttribute('disabled', ''),
      );
    });
    assert.equal(kept.returnTo, 'di-live-only');
    assert.equal(after.focus, 'di-office-panel-minimize', `disabled: ${after.focus}`);
    await p().evaluate(() =>
      document.querySelector('[data-testid="di-live-only"]')!.removeAttribute('disabled'),
    );
  }
  // hidden: the remembered control sits in a disclosure that is closed while the panel is minimized
  {
    await openFirstRoster();
    await p().focus('[data-testid="di-mission-roster"] [data-testid="di-member-row"]');
    const { kept, after } = await officeRoundTrip(async () => {
      await p().evaluate(() => {
        document.querySelector<HTMLDetailsElement>('[data-testid="di-mission-roster"]')!.open =
          false;
      });
    });
    assert.equal(kept.returnTo, 'di-member-row');
    assert.equal(after.focus, 'di-office-panel-minimize', `hidden: ${after.focus}`);
  }
  // removed: the remembered control's Mission leaves the office state while the panel is minimized
  {
    const ref = await p().evaluate(() =>
      document
        .querySelector('[data-testid="di-op-main"] article[data-mission-ref]')!
        .getAttribute('data-mission-ref')!,
    );
    await p().focus(`article[data-mission-ref="${ref}"] [data-testid="di-open-inspector-mission"]`);
    const { kept, after } = await officeRoundTrip(async () => {
      await p().evaluate(
        (r) =>
          window.__diHarness.update((st) => ({
            ...st,
            missions: {
              ...st.missions,
              missions: st.missions.missions.filter((m) => m.missionRef !== r),
            },
          })),
        ref,
      );
      await p().waitForFunction(
        (r) => !document.querySelector(`article[data-mission-ref="${r}"]`),
        ref,
      );
    });
    assert.equal(kept.returnTo, 'di-open-inspector-mission');
    assert.equal(after.focus, 'di-office-panel-minimize', `removed: ${after.focus}`);
  }
});

/** What the Reception dialog shows of itself and of its focus. */
const receptionView = () =>
  p().evaluate(() => {
    const dlg = document.querySelector<HTMLElement>('[data-testid="di-reception-inbox"]');
    const body = document.querySelector<HTMLElement>('[data-testid="di-reception-inbox-body"]');
    const coverage = document.querySelector<HTMLDetailsElement>(
      '[data-testid="di-reception-coverage"]',
    );
    const a = document.activeElement;
    return {
      inDom: !!dlg,
      shown: !!dlg && dlg.getClientRects().length > 0,
      rootInert: document.getElementById('root')?.hasAttribute('inert') ?? null,
      scrollTop: body && body.getClientRects().length > 0 ? body.scrollTop : null,
      coverageOpen: coverage ? coverage.open : null,
      search:
        document.querySelector<HTMLInputElement>('[data-testid="di-reception-search"]')?.value ??
        null,
      pressed: [
        ...document.querySelectorAll(
          '[data-testid="di-reception-chips"] button[aria-pressed="true"]',
        ),
      ].map((b) => b.textContent?.trim()),
      focus:
        a === null || a === document.body ? 'BODY' : (a.getAttribute('data-testid') ?? a.tagName),
    };
  });

test('AC3 Reception: minimize releases the app and keeps search, category, open disclosure and scroll; restore gives them back and Escape still closes', async () => {
  await withStrip('&p2a=1'); // the labelled synthetic entries give Reception more than one category
  await p().click('[data-testid="di-open-reception"]');
  await p().waitForSelector('[data-testid="di-reception-inbox"]');
  const chip = p().locator('[data-testid="di-reception-chips"] button').nth(1);
  const chipLabel = ((await chip.textContent()) ?? '').trim();
  await chip.click();
  await p().fill('[data-testid="di-reception-search"]', 'SYNTHETIC');
  await p().evaluate(() => {
    const d = document.querySelector<HTMLDetailsElement>('[data-testid="di-reception-coverage"]');
    if (d) d.open = true;
  });
  const want = await p().evaluate(() => {
    const body = document.querySelector<HTMLElement>('[data-testid="di-reception-inbox-body"]')!;
    body.scrollTop = Math.min(body.scrollHeight - body.clientHeight, 157);
    return body.scrollTop;
  });
  assert.ok(want > 0, 'the inbox body scrolls to a distinct position');
  const before = await receptionView();
  assert.deepEqual(before.pressed, [chipLabel]);

  // the person is in the search box, then takes the real keyboard path to Minimize (Shift+Tab, then Enter)
  await p().focus('[data-testid="di-reception-search"]');
  const path = await shiftTabTo('[data-testid="di-reception-minimize"]', 20);
  assert.ok(path, "Shift+Tab reaches Reception's Minimize");
  await p().keyboard.press('Enter');
  await p().waitForSelector('[data-testid="di-reception-restore"]');
  const minimized = await receptionView();
  assert.equal(minimized.inDom, true, 'minimized: the dialog is kept, not unmounted');
  assert.equal(minimized.shown, false, 'minimized: the dialog is not shown');
  assert.equal(minimized.rootInert, false, 'minimized: the app behind is usable again');
  assert.equal(
    minimized.focus,
    'di-reception-restore',
    `minimized: focus is on Restore, not ${minimized.focus}`,
  );
  assert.deepEqual(
    await recorded('[data-testid="di-reception-restore"]'),
    { atHide: 'di-reception-minimize', returnTo: path!.lastBefore },
    'recorded: Minimize had focus at hide; the last dialog control before it is where focus returns',
  );
  await p().fill('[data-testid="di-find-input"]', 'lead');
  assert.equal(
    await active('[data-testid="di-find-input"]'),
    true,
    'minimized: the panel Find box takes focus and typing',
  );
  await p().fill('[data-testid="di-find-input"]', '');

  await p().focus('[data-testid="di-reception-restore"]');
  await p().keyboard.press('Enter');
  await p().waitForFunction(() => {
    const d = document.querySelector('[data-testid="di-reception-inbox"]');
    return !!d && d.getClientRects().length > 0;
  });
  await p().waitForTimeout(200);
  const after = await receptionView();
  assert.equal(after.rootInert, true, 'restored: the dialog is modal again');
  assert.equal(after.scrollTop, want, `restored: the body is where it was (${want})`);
  assert.equal(after.coverageOpen, true, 'restored: the opened disclosure is still open');
  assert.equal(after.search, 'SYNTHETIC', 'restored: the typed search');
  assert.deepEqual(after.pressed, [chipLabel], 'restored: the chosen category');
  // contract (b): back on the last dialog control that had focus before the toggle, not parked on the toggle
  assert.notEqual(path!.lastBefore, 'di-reception-minimize');
  assert.equal(
    after.focus,
    path!.lastBefore,
    `restored: focus is back on ${path!.lastBefore}, not ${after.focus}`,
  );

  await p().keyboard.press('Escape');
  await p().waitForSelector('[data-testid="di-reception-inbox"]', { state: 'detached' });
  assert.equal(
    await active('[data-testid="di-open-reception"]'),
    true,
    'Escape closes, and focus returns to the opener',
  );
});

test('AC3 Reception, mouse: typed in search, clicked Minimize, clicked Restore: focus is back in the search box with its text', async () => {
  await withStrip();
  await p().click('[data-testid="di-open-reception"]');
  await p().waitForSelector('[data-testid="di-reception-inbox"]');
  await p().click('[data-testid="di-reception-search"]');
  await p().keyboard.type('fixture');
  await p().click('[data-testid="di-reception-minimize"]');
  await p().waitForSelector('[data-testid="di-reception-restore"]');
  // the mouse moved focus to Minimize before the hide: only the remembered working control can bring the search back
  assert.deepEqual(await recorded('[data-testid="di-reception-restore"]'), {
    atHide: 'di-reception-minimize',
    returnTo: 'di-reception-search',
  });
  await p().click('[data-testid="di-reception-restore"]');
  await p().waitForFunction(() => {
    const d = document.querySelector('[data-testid="di-reception-inbox"]');
    return !!d && d.getClientRects().length > 0;
  });
  const after = await receptionView();
  assert.equal(after.rootInert, true, 'restored: modal again');
  assert.equal(
    after.focus,
    'di-reception-search',
    `restored: focus is back in the search box, not ${after.focus}`,
  );
  assert.equal(after.search, 'fixture', 'restored: the typed text is intact');
});

test('AC3 focus fallbacks, Reception: with no remembered control (absent) or with it removed while minimized, focus lands on Minimize, never the page', async () => {
  // ABSENT: opened and minimized by mouse before any Reception control had focus (the dialog itself held it)
  await withStrip();
  await p().click('[data-testid="di-open-reception"]');
  await p().waitForSelector('[data-testid="di-reception-inbox"]');
  await p().click('[data-testid="di-reception-minimize"]');
  await p().waitForSelector('[data-testid="di-reception-restore"]');
  assert.deepEqual(await recorded('[data-testid="di-reception-restore"]'), {
    atHide: 'di-reception-minimize',
    returnTo: 'none (the Minimize toggle)',
  });
  await p().click('[data-testid="di-reception-restore"]');
  await p().waitForSelector('[data-testid="di-reception-minimize"]');
  await p().waitForTimeout(100);
  assert.equal(
    (await receptionView()).focus,
    'di-reception-minimize',
    'absent: Minimize, never BODY or the restore control',
  );

  // REMOVED: the person clicked into an entry card; while minimized that entry leaves the office state
  await withStrip();
  await p().click('[data-testid="di-open-reception"]');
  await p().waitForSelector('[data-testid="di-reception-entry"][data-entry-kind="mission"]');
  const card = p().locator('[data-testid="di-reception-entry"][data-entry-kind="mission"]').first();
  const key = (await card.getAttribute('data-entry-key'))!;
  await card.locator('.di-op-objective').click();
  assert.equal(
    (await receptionView()).focus,
    'di-reception-entry',
    'the click put focus on the entry card',
  );
  await p().click('[data-testid="di-reception-minimize"]');
  await p().waitForSelector('[data-testid="di-reception-restore"]');
  assert.deepEqual(await recorded('[data-testid="di-reception-restore"]'), {
    atHide: 'di-reception-minimize',
    returnTo: 'di-reception-entry',
  });
  await p().evaluate(
    (gone) =>
      window.__diHarness.update((st) => ({
        ...st,
        reception: {
          ...st.reception!,
          entries: st.reception!.entries.filter((e) => e.key !== gone),
        },
      })),
    key,
  );
  await p().waitForFunction(
    (gone) =>
      ![...document.querySelectorAll('[data-testid="di-reception-entry"]')].some(
        (e) => e.getAttribute('data-entry-key') === gone,
      ),
    key,
  );
  await p().click('[data-testid="di-reception-restore"]');
  await p().waitForSelector('[data-testid="di-reception-minimize"]');
  await p().waitForTimeout(100);
  assert.equal(
    (await receptionView()).focus,
    'di-reception-minimize',
    'removed: Minimize, never BODY',
  );
  // DISABLED and HIDDEN cannot arise in Reception today: no office update disables or hides a Reception control
  // while the dialog is minimized. The shared restoreFocus treats all four the same; the office panel test above
  // proves disabled and hidden on real controls.
});

test('AC4 owner attention: a still glow, a shape and words on the controls that wait for the owner; reduced motion keeps it still; acting stays view only', async () => {
  for (const motion of ['no-preference', 'reduce'] as const) {
    await p().emulateMedia({ reducedMotion: motion });
    const open = await p().evaluate(() => {
      const b = document.querySelector<HTMLElement>('[data-testid="di-open-reception"]')!;
      const s = getComputedStyle(b);
      return {
        attention: b.getAttribute('data-attention'),
        glow: s.boxShadow,
        animation: s.animationName,
        glyph: b.querySelector('.di-op-attn-glyph')?.textContent?.trim() ?? null,
        glyphHidden: b.querySelector('.di-op-attn-glyph')?.getAttribute('aria-hidden') ?? null,
        words: b.textContent?.replace(/\s+/g, ' ').trim(),
        title: b.getAttribute('title'),
        line: document.querySelector('[data-testid="di-reception-summary"]')?.textContent ?? '',
      };
    });
    assert.equal(
      open.attention,
      'true',
      `${motion}: Open reception is marked as waiting for the owner`,
    );
    assert.notEqual(open.glow, 'none', `${motion}: it glows`);
    assert.equal(open.animation, 'none', `${motion}: the glow is still, never animated`);
    assert.equal(open.glyph, '▲', `${motion}: a shape that needs no colour`);
    assert.equal(
      open.glyphHidden,
      'true',
      'the shape is decorative to VoiceOver; the words carry the meaning',
    );
    assert.match(open.words ?? '', /Open reception/);
    assert.match(open.line, /need/, 'the count is said in words beside it');
    assert.match(
      open.title ?? '',
      /^View only/,
      'who may act is unchanged: this control only opens a view',
    );
  }
  await p().emulateMedia({ reducedMotion: 'no-preference' });
  // a control that waits for nobody carries none of it
  const quiet = await p().evaluate(() => {
    const b = document.querySelector<HTMLElement>('[data-testid="di-office-panel"] header button')!;
    return { attention: b.getAttribute('data-attention'), glow: getComputedStyle(b).boxShadow };
  });
  assert.equal(quiet.attention, null);
  assert.equal(quiet.glow, 'none');
});

test('AC6 keyboard: Tab reaches Minimize, Enter minimizes and restores, and the focus ring is visible on both controls', async () => {
  assert.notEqual(
    await tabTo('[data-testid="di-office-panel-minimize"]'),
    null,
    'Tab reaches Minimize',
  );
  const ringOnMinimize = await focusOutline();
  assert.equal(ringOnMinimize.style, 'solid');
  assert.equal(ringOnMinimize.width, '2px');
  await p().keyboard.press('Enter');
  await p().waitForSelector('[data-testid="di-office-panel-collapsed"]');
  assert.equal(await active('[data-testid="di-office-panel-collapsed"]'), true);
  const ringOnRestore = await p().evaluate(() => {
    const el = document.activeElement as HTMLElement;
    return {
      visible: el.matches(':focus-visible'),
      style: getComputedStyle(el).outlineStyle,
      width: getComputedStyle(el).outlineWidth,
    };
  });
  assert.deepEqual(ringOnRestore, { visible: true, style: 'solid', width: '2px' });
  await p().keyboard.press('Enter');
  await p().waitForSelector('[data-testid="di-office-panel-minimize"]');
  assert.equal(await active('[data-testid="di-office-panel-minimize"]'), true);
});

test('AC7 copy: no em or en dash in the rendered office panel, its details dialog or Reception (read as rendered, so data is checked too)', async () => {
  const found: string[] = [];
  const preserved: string[] = [];
  const read = async (where: string, selector: string) => {
    const copy = await renderedCopy(p(), selector);
    for (const line of copy.authored) found.push(`${where} | ${line}`);
    for (const line of copy.content) preserved.push(`content | ${where} | ${line}`);
  };
  await read('office panel at rest', '[data-testid="di-office-panel"]');
  await openFirstRoster();
  await p().evaluate(() =>
    document
      .querySelectorAll<HTMLDetailsElement>('[data-testid="di-office-panel"] details')
      .forEach((d) => (d.open = true)),
  );
  await read('office panel, every disclosure open', '[data-testid="di-office-panel"]');
  const { member } = drawnMembers[0];
  await p().fill('[data-testid="di-find-input"]', member.logical.slice(0, 3));
  await p().waitForSelector('[data-testid="di-find-result"]');
  await read('office panel, find results', '[data-testid="di-office-panel"]');
  await p().fill('[data-testid="di-find-input"]', member.logical);
  await p().waitForSelector('[data-testid="di-find-result"]');
  await p().focus('[data-testid="di-find-result"]');
  await p().keyboard.press('Enter');
  await p().waitForFunction(
    (id) => window.__diHarness.office.selectedAgentId === id,
    member.characterId,
    { timeout: 5_000 },
  );
  await read('office panel, a member selected', '[data-testid="di-office-panel"]');
  for (const opener of [
    '[data-testid="di-open-inspector-selection"]',
    '[data-testid="di-open-inspector-mission"]',
  ]) {
    await p().locator(opener).first().click();
    await p().waitForSelector('[data-testid="di-inspector"]');
    await p().evaluate(() =>
      document
        .querySelectorAll<HTMLDetailsElement>('[data-testid="di-inspector"] details')
        .forEach((d) => (d.open = true)),
    );
    await read(`details dialog from ${opener}`, '[data-testid="di-inspector"]');
    await p().keyboard.press('Escape');
    await p().waitForSelector('[data-testid="di-inspector"]', { state: 'detached' });
  }
  await p().click('[data-testid="di-open-reception"]');
  await p().waitForSelector('[data-testid="di-reception-inbox"]');
  await p().evaluate(() =>
    document
      .querySelectorAll<HTMLDetailsElement>('[data-testid="di-reception-inbox"] details')
      .forEach((d) => (d.open = true)),
  );
  await read('Reception, every disclosure open', '[data-testid="di-reception-inbox"]');
  // evidence: content kept byte for byte, and server-authored statements reported for a later server change
  console.log(
    `[AC7 copy] authored dashes: ${found.length}; content outside the rule, preserved: ${preserved.length}\n${preserved.join('\n')}`,
  );
  assert.deepEqual(found, [], `rendered em or en dashes:\n${found.join('\n')}`);
});

test('AC3 and AC6 across surfaces: two surfaces minimized at once in separate slots; a restore cannot happen inside another modal; inert and focus stay right throughout', async () => {
  await withStrip();
  const inert = () =>
    p().evaluate(() => document.getElementById('root')?.hasAttribute('inert') ?? null);
  const focusId = () =>
    p().evaluate(() => {
      const a = document.activeElement;
      return a === null || a === document.body
        ? 'BODY'
        : (a.getAttribute('data-testid') ?? a.tagName);
    });
  // 1. Reception minimized (after working in its search box), then the office panel minimized: two slots, one strip
  await p().click('[data-testid="di-open-reception"]');
  await p().waitForSelector('[data-testid="di-reception-inbox"]');
  await p().fill('[data-testid="di-reception-search"]', 'SYNTHETIC');
  await p().click('[data-testid="di-reception-minimize"]');
  await p().waitForSelector('[data-testid="di-reception-restore"]');
  assert.equal(await inert(), false, 'Reception minimized: the app is usable');
  await p().click('[data-testid="di-office-panel-minimize"]');
  await p().waitForSelector('[data-testid="di-office-panel-collapsed"]');
  const slots = await p().evaluate(() =>
    [...document.querySelectorAll<HTMLElement>('[data-testid="di-minimized-tray"] > *')].map(
      (el) => {
        const r = el.getBoundingClientRect();
        return {
          id: el.getAttribute('data-testid'),
          name: el.getAttribute('aria-label'),
          text: el.textContent?.trim(),
          top: r.top,
          bottom: r.bottom,
          left: r.left,
          right: r.right,
        };
      },
    ),
  );
  assert.deepEqual(slots.map((s) => s.id).sort(), [
    'di-office-panel-collapsed',
    'di-reception-restore',
  ]);
  const [a, b] = [...slots].sort((x, y) => x.top - y.top);
  assert.ok(
    a.bottom <= b.top,
    `the two slots are stacked, not overlapping: ${JSON.stringify(slots)}`,
  );
  const label: Record<string, string> = {
    'di-office-panel-collapsed': 'Restore office panel',
    'di-reception-restore': 'Restore reception',
  };
  for (const slot of slots) {
    const visible = label[slot.id ?? ''];
    assert.ok(
      (slot.text ?? '').includes(visible),
      `a short visible label "${visible}": ${slot.text}`,
    );
    assert.ok(
      (slot.name ?? '').startsWith(visible),
      `the accessible name contains the visible label: ${slot.name}`,
    );
  }
  // 2. restore the office panel while Reception stays minimized: still no modal; focus is back on the last panel
  //    control that had it (Open reception, which opened Reception), not on the toggle
  await p().focus('[data-testid="di-office-panel-collapsed"]');
  await p().keyboard.press('Enter');
  await p().waitForSelector('[data-testid="di-office-panel-minimize"]');
  assert.equal(await inert(), false);
  assert.equal(await focusId(), 'di-open-reception');
  // 3. another modal opens (a Mission's details) while Reception is minimized: the strip is inert with the app,
  //    so Reception cannot be restored into it, and Tab stays inside the open dialog
  await p().locator('[data-testid="di-open-inspector-mission"]').first().click();
  await p().waitForSelector('[data-testid="di-inspector"]');
  assert.equal(await inert(), true, 'the details dialog is modal');
  const restoreBlocked = await p().evaluate(() => {
    const r = document.querySelector<HTMLElement>('[data-testid="di-reception-restore"]')!;
    r.focus();
    return { insideInert: r.closest('[inert]') !== null, tookFocus: document.activeElement === r };
  });
  assert.deepEqual(
    restoreBlocked,
    { insideInert: true, tookFocus: false },
    'Restore cannot be reached while another dialog is modal',
  );
  for (let i = 0; i < 25; i++) {
    await p().keyboard.press('Tab');
    const inside = await p().evaluate(
      () =>
        document.querySelector('[data-testid="di-inspector"]')?.contains(document.activeElement) ??
        false,
    );
    assert.equal(inside, true, `Tab ${i + 1} stays inside the open dialog`);
  }
  // 4. the dialog closes: the app is NOT left inert (Reception is still minimized), focus is on a control
  await p().keyboard.press('Escape');
  await p().waitForSelector('[data-testid="di-inspector"]', { state: 'detached' });
  assert.equal(await inert(), false, 'closing the dialog leaves the app usable, not inert');
  assert.notEqual(await focusId(), 'BODY');
  // 5. now Reception restores: modal again, focus inside it; Tab stays in it; Escape closes and leaves nothing inert
  await p().focus('[data-testid="di-reception-restore"]');
  await p().keyboard.press('Enter');
  await p().waitForFunction(() => {
    const d = document.querySelector('[data-testid="di-reception-inbox"]');
    return !!d && d.getClientRects().length > 0;
  });
  assert.equal(await inert(), true, 'restored Reception is modal');
  assert.equal(
    await focusId(),
    'di-reception-search',
    'contract (b): back in the search box where the person was working',
  );
  for (let i = 0; i < 25; i++) {
    await p().keyboard.press('Tab');
    const inside = await p().evaluate(
      () =>
        document
          .querySelector('[data-testid="di-reception-inbox"]')
          ?.contains(document.activeElement) ?? false,
    );
    assert.equal(inside, true, `Tab ${i + 1} stays inside Reception`);
  }
  await p().keyboard.press('Escape');
  await p().waitForSelector('[data-testid="di-reception-inbox"]', { state: 'detached' });
  assert.equal(await inert(), false, 'after the last dialog closes nothing is inert');
  assert.equal(await focusId(), 'di-open-reception');
  assert.equal(
    await p().evaluate(
      () => document.querySelectorAll('[data-testid="di-minimized-tray"] > *').length,
    ),
    0,
    'the strip is empty',
  );
});

/*
 * DEFENSIVE HARNESS EXERCISE, NOT A REPRODUCED USER FLOW. Today a minimized dialog cannot close while hidden through the
 * product UI: the review's invalidation dispatches 'clear' (never 'close'), every close route of the review and of
 * Reception starts from a control inside the hidden dialog, and the office panel never unmounts after its first
 * office state. This test therefore closes a hidden Reception through `__diHarness.closeReception()`, a TEST-ONLY seam
 * in test/harness (never shipped, inert in real sessions, calling the existing close). It exists so the ownership
 * holds if such a path ever appears. The module-level proof is test/diModalStack.test.ts; the reachable review
 * sequence is the "[reachable runtime path]" test in diCodeReview.test.ts.
 */
test('[DEFENSIVE HARNESS EXERCISE] dialog lifecycle: a HIDDEN dialog that closes never clears the guard of the dialog in front, and never moves its focus (both orders, several surfaces)', async () => {
  await withStrip();
  const state = () =>
    p().evaluate(() => {
      const a = document.activeElement;
      return {
        inert: document.getElementById('root')?.hasAttribute('inert') ?? null,
        focus:
          a === null || a === document.body ? 'BODY' : (a.getAttribute('data-testid') ?? a.tagName),
        focusInDetails:
          document.querySelector('[data-testid="di-inspector"]')?.contains(a) ?? false,
        details: !!document.querySelector('[data-testid="di-inspector"]'),
        reception: !!document.querySelector('[data-testid="di-reception-inbox"]'),
      };
    });
  const minimizeReception = async () => {
    await p().click('[data-testid="di-open-reception"]');
    await p().waitForSelector('[data-testid="di-reception-inbox"]');
    await p().click('[data-testid="di-reception-minimize"]');
    await p().waitForSelector('[data-testid="di-reception-restore"]');
  };
  const openDetails = async () => {
    await p().locator('[data-testid="di-open-inspector-mission"]').first().click();
    await p().waitForSelector('[data-testid="di-inspector"]');
  };

  // ORDER 1: Reception minimized, the details dialog in front, then the hidden Reception closes
  await minimizeReception();
  await openDetails();
  const front = await state();
  assert.deepEqual(
    [front.inert, front.focusInDetails],
    [true, true],
    'the details dialog is modal and holds focus',
  );
  await p().evaluate(() => window.__diHarness.closeReception());
  await p().waitForSelector('[data-testid="di-reception-inbox"]', { state: 'detached' });
  const afterHiddenClose = await state();
  assert.equal(
    afterHiddenClose.inert,
    true,
    'ORDER 1: the hidden dialog closing left the dialog in front unguarded (#root not inert)',
  );
  assert.equal(
    afterHiddenClose.focusInDetails,
    true,
    `ORDER 1: the hidden dialog closing moved focus out of the dialog in front, to ${afterHiddenClose.focus}`,
  );
  await p().keyboard.press('Escape');
  await p().waitForSelector('[data-testid="di-inspector"]', { state: 'detached' });
  const afterFrontClose = await state();
  assert.equal(afterFrontClose.inert, false, 'ORDER 1: with no dialog in front nothing is inert');
  assert.equal(
    afterFrontClose.focus,
    'di-open-inspector-mission',
    'ORDER 1: the dialog in front returns focus to its own opener',
  );

  // ORDER 2: Reception minimized, the details dialog opens and closes first, then the hidden Reception closes
  await minimizeReception();
  await openDetails();
  await p().keyboard.press('Escape');
  await p().waitForSelector('[data-testid="di-inspector"]', { state: 'detached' });
  const beforeHiddenClose = await state();
  assert.equal(beforeHiddenClose.inert, false);
  await p().evaluate(() => window.__diHarness.closeReception());
  await p().waitForSelector('[data-testid="di-reception-inbox"]', { state: 'detached' });
  const order2 = await state();
  assert.equal(order2.inert, false, 'ORDER 2: closing the hidden dialog leaves nothing inert');
  assert.equal(
    order2.focus,
    beforeHiddenClose.focus,
    `ORDER 2: a hidden dialog closing never moves focus (was ${beforeHiddenClose.focus}, now ${order2.focus})`,
  );

  // SEVERAL SURFACES: Reception and the office panel both minimized, then the hidden Reception closes
  await minimizeReception();
  await p().click('[data-testid="di-office-panel-minimize"]');
  await p().waitForSelector('[data-testid="di-office-panel-collapsed"]');
  await p().focus('[data-testid="di-office-panel-collapsed"]');
  await p().evaluate(() => window.__diHarness.closeReception());
  await p().waitForSelector('[data-testid="di-reception-restore"]', { state: 'detached' });
  const teardown = await state();
  assert.equal(teardown.inert, false, 'several surfaces: nothing is inert with no dialog in front');
  assert.equal(
    teardown.focus,
    'di-office-panel-collapsed',
    `several surfaces: focus stays on the remaining restore control, not ${teardown.focus}`,
  );
  assert.equal(
    await p().evaluate(
      () => document.querySelectorAll('[data-testid="di-minimized-tray"] > *').length,
    ),
    1,
    'one slot remains',
  );
});

test('close-time focus [reachable]: the details dialog closes after its opener has left the page: focus goes to the office Find box, never the page', async () => {
  await withStrip();
  const opener = p().locator('[data-testid="di-open-inspector-mission"]').first();
  const missionRef = await opener.evaluate(
    (el) => el.closest('[data-mission-ref]')?.getAttribute('data-mission-ref') ?? null,
  );
  const handle = await opener.elementHandle(); // taken BEFORE the Mission leaves, so it is this very button
  await opener.click();
  await p().waitForSelector('[data-testid="di-inspector"]');
  assert.equal(
    await p().evaluate(() => document.getElementById('root')?.hasAttribute('inert')),
    true,
    'the details dialog is modal',
  );
  // The opener leaves the page while the dialog is open (its Mission leaves the office state; nothing else is touched).
  await p().evaluate(
    (ref) =>
      window.__diHarness.update((st) => ({
        ...st,
        missions: {
          ...st.missions,
          missions: st.missions.missions.filter((m) => m.missionRef !== ref),
        },
      })),
    missionRef,
  );
  await p().waitForFunction((el) => !el?.isConnected, handle, { timeout: 10_000 });
  await p().keyboard.press('Escape');
  await p().waitForSelector('[data-testid="di-inspector"]', { state: 'detached' });
  assert.equal(
    await p().evaluate(() => document.getElementById('root')?.hasAttribute('inert')),
    false,
    'closed: nothing is inert',
  );
  const focus = await p().evaluate(() => {
    const a = document.activeElement;
    return a === null || a === document.body
      ? 'BODY'
      : (a.getAttribute('data-testid') ?? a.tagName);
  });
  assert.equal(focus, 'di-find-input', `focus is on the office Find box, not ${focus}`);
});

/*
 * SCOPE OF THIS SWEEP, stated plainly. It is a CUSTOM MEASURED SWEEP (test/diChromeA11y.ts), NOT a full accessibility auditor
 * and NOT manual VoiceOver proof. What it measures, on the roots it is pointed at: that each visible control has an accessible
 * name and where the name came from; that an aria-label holds the control's visible words; that nothing animates or
 * transitions; and that elements marking a state carry words. The TARGET-SIZE assertion (WCAG 2.5.8, 24 by 24 CSS px) covers
 * only the NEW controls and the restore slots listed in NEW_TARGETS below. It does NOT cover the pre-existing population of
 * smaller controls: a measured baseline of 75 controls under 24 px in the office panel and 12 in Reception exists at 109e096c
 * and is unchanged by this slice. That population is OUTSIDE this sweep's scope and is not rewritten; a count that matches
 * the baseline is not a claim of general target-size compliance.
 */
test('AC6 sweep (measured, scoped): every control found has an accessible name that holds its visible words, the new targets are at least 24 px, nothing moves, and no state is carried by colour alone', async () => {
  const NEW_TARGETS = [
    'di-office-panel-minimize',
    'di-office-panel-collapsed',
    'di-reception-minimize',
    'di-reception-restore',
    'di-open-reception',
    'di-mission-samples-title',
  ];
  for (const motion of ['no-preference', 'reduce'] as const) {
    await p().emulateMedia({ reducedMotion: motion });
    await withStrip('&p2a=1');
    const sweeps = {
      'office panel, strip and the sample section': await a11ySweep(
        p(),
        '[data-testid="di-office-panel"], [data-testid="di-minimized-tray"]',
      ),
    };
    await p().click('[data-testid="di-open-reception"]');
    await p().waitForSelector('[data-testid="di-reception-inbox"]');
    (sweeps as Record<string, Awaited<ReturnType<typeof a11ySweep>>>)['Reception'] =
      await a11ySweep(p(), '[data-testid="di-reception-inbox"]');
    await p().click('[data-testid="di-reception-minimize"]');
    await p().waitForSelector('[data-testid="di-reception-restore"]');
    await p().click('[data-testid="di-office-panel-minimize"]');
    await p().waitForSelector('[data-testid="di-office-panel-collapsed"]');
    const strip = await a11ySweep(p(), '[data-testid="di-minimized-tray"]');
    (sweeps as Record<string, Awaited<ReturnType<typeof a11ySweep>>>)[
      'the strip with two minimized surfaces'
    ] = strip;

    assert.deepEqual(
      strip.controls.map((c) => c.id).sort(),
      ['di-office-panel-collapsed', 'di-reception-restore'],
      `${motion}: the strip holds exactly the two restore controls`,
    );
    for (const [where, sweep] of Object.entries(sweeps)) {
      const at = `${motion}, ${where}`;
      assert.ok(sweep.controls.length > 0, `${at}: controls were found`);
      assert.deepEqual(
        sweep.controls.filter((c) => c.source === 'none').map((c) => c.id),
        [],
        `${at}: every control has an accessible name`,
      );
      assert.deepEqual(
        sweep.controls
          .filter((c) => c.source === 'title' || c.source === 'placeholder')
          .map((c) => `${c.id} (${c.source})`),
        [],
        `${at}: no control is named by a title or a placeholder alone`,
      );
      assert.deepEqual(
        sweep.controls.filter((c) => c.labelInName === false).map((c) => `${c.id}: "${c.name}"`),
        [],
        `${at}: an aria-label always holds the words shown on the control`,
      );
      assert.deepEqual(
        sweep.stateMarks.filter((m) => m.words === '').map((m) => m.id),
        [],
        `${at}: no state is carried by colour alone`,
      );
      assert.deepEqual(sweep.motion, [], `${at}: nothing on the chrome animates or transitions`);
      assert.equal(sweep.running, 0, `${at}: no animation is running`);
      for (const c of sweep.controls.filter((x) => NEW_TARGETS.includes(x.id)))
        assert.ok(
          Math.min(c.width, c.height) >= 24,
          `${at}: ${c.id} is ${c.width} x ${c.height}, at least 24 x 24 CSS px`,
        );
    }
    // the two strip slots read by name: the visible words, then what waits
    const names = Object.fromEntries(strip.controls.map((c) => [c.id, c.name]));
    assert.match(names['di-office-panel-collapsed'], /^Restore office panel/);
    assert.match(names['di-reception-restore'], /^Restore reception/);
    for (const c of strip.controls)
      assert.ok(
        Math.min(c.width, c.height) >= 24,
        `${motion}: strip slot ${c.id} is ${c.width} x ${c.height}`,
      );
  }
  await p().emulateMedia({ reducedMotion: 'no-preference' });
});

test('token scope, COMPUTED in the browser: every custom property a chrome rule consumes has a value on the elements that rule matches (office panel, samples, strip, Reception, details dialog)', async () => {
  await withStrip('&p2a=1');
  const audits: Array<[string, Awaited<ReturnType<typeof tokenScopeAudit>>]> = [];
  audits.push(['office panel with samples', await tokenScopeAudit(p())]);
  await p().locator('[data-testid="di-open-inspector-mission"]').first().click();
  await p().waitForSelector('[data-testid="di-inspector"]');
  audits.push(['details dialog open', await tokenScopeAudit(p())]);
  await p().keyboard.press('Escape');
  await p().waitForSelector('[data-testid="di-inspector"]', { state: 'detached' });
  await p().click('[data-testid="di-open-reception"]');
  await p().waitForSelector('[data-testid="di-reception-inbox"]');
  audits.push(['Reception open', await tokenScopeAudit(p())]);
  await p().click('[data-testid="di-reception-minimize"]');
  await p().waitForSelector('[data-testid="di-reception-restore"]');
  await p().click('[data-testid="di-office-panel-minimize"]');
  await p().waitForSelector('[data-testid="di-office-panel-collapsed"]');
  audits.push(['strip with two minimized surfaces', await tokenScopeAudit(p())]);
  for (const [state, audit] of audits) {
    assert.ok(
      audit.consumingRules > 20 && audit.matchedRules > 0 && audit.pairsChecked > 100,
      `${state}: the audit really looked (${JSON.stringify({ r: audit.consumingRules, m: audit.matchedRules, p: audit.pairsChecked })})`,
    );
    console.log(
      `[token scope] ${state}: ${audit.consumingRules} rules consume a token, ${audit.matchedRules} matched an element here, ${audit.pairsChecked} element-token pairs checked`,
    );
    assert.deepEqual(
      audit.gaps,
      [],
      `${state}: a rule consumes a token that is not in scope on the element it styles`,
    );
  }
});

test('token scope audit has TEETH: it reports a backdrop that consumes a token defined only on its child, and nothing for a correct pair', async () => {
  await withStrip();
  await p().evaluate(() => {
    const style = document.createElement('style');
    // the exact shape of the review frame defect: the parent consumes a token its child defines
    style.textContent = `
      .di-probe-bad-backdrop { background: var(--probe-scrim); }
      .di-probe-bad { --probe-scrim: white; }
      .di-probe-ok-backdrop { --probe-ok: white; background: var(--probe-ok); }
      .di-probe-ok { color: var(--probe-ok); }
      .di-probe-fallback { background: var(--probe-never-defined, white); }`;
    document.head.appendChild(style);
    const bad = document.createElement('div');
    bad.className = 'di-probe-bad-backdrop';
    bad.innerHTML = '<div class="di-probe-bad"></div>';
    const ok = document.createElement('div');
    ok.className = 'di-probe-ok-backdrop';
    ok.innerHTML = '<div class="di-probe-ok"></div>';
    const fb = document.createElement('div');
    fb.className = 'di-probe-fallback';
    document.body.append(bad, ok, fb);
  });
  const audit = await tokenScopeAudit(p());
  assert.deepEqual(
    audit.gaps
      .filter((g) => g.selector.includes('probe'))
      .map((g) => ({ selector: g.selector, token: g.token, elements: g.elements })),
    [{ selector: '.di-probe-bad-backdrop', token: '--probe-scrim', elements: 1 }],
    'only the backdrop that consumes a child-defined token is reported: the correct pair and the rule with a fallback are not',
  );
  assert.deepEqual(
    await p()
      .evaluate(() => {
        const transparent = document.createElement('div');
        transparent.style.background = 'transparent';
        document.body.append(transparent);
        return {
          brokenBackdrop: getComputedStyle(document.querySelector('.di-probe-bad-backdrop')!)
            .backgroundColor,
          knownTransparent: getComputedStyle(transparent).backgroundColor,
          correctPair: getComputedStyle(document.querySelector('.di-probe-ok-backdrop')!)
            .backgroundColor,
        };
      })
      .then((v) => ({
        brokenIsTransparent: v.brokenBackdrop === v.knownTransparent,
        correctIsPainted: v.correctPair !== v.knownTransparent,
      })),
    { brokenIsTransparent: true, correctIsPainted: true },
    'and in the browser that backdrop really computes transparent while the correct pair is painted',
  );
});

/*
 * The strip's labels, RENDERED. A programmatic pass (name, focus, a bounding box) is not a visual pass: at 1440 and 900 the
 * Restore labels were laid out 9.4 px OUTSIDE their 32 px buttons and clipped away while every name and focus assertion
 * passed. This measures where each visible text node is DRAWN against the button that carries it, with several surfaces
 * minimized at once, at the sizes the owner will use (1440 x 900, 900 x 700, narrow 640 x 800, and a ZOOM-EQUIVALENT 720 x 450
 * at deviceScaleFactor 2: a scaled viewport, NOT a native Command-plus browser zoom, which headless automation cannot press), after the layout has settled.
 */
const STRIP_VIEWS = [
  { name: '1440 x 900', width: 1440, height: 900, scale: 1 },
  { name: '900 x 700', width: 900, height: 700, scale: 1 },
  { name: 'narrow 640 x 800', width: 640, height: 800, scale: 1 },
  {
    name: 'zoom-equivalent 200% (720 x 450 at 2x; a scaled viewport, NOT a native Command-plus test)',
    width: 720,
    height: 450,
    scale: 2,
  },
] as const;

const FULL_LABEL: Record<string, string> = {
  'di-office-panel-collapsed': 'Restore office panel',
  'di-reception-restore': 'Restore reception',
};

test('AC3 strip labels, RENDERED GEOMETRY: every Restore label is drawn inside its button, unclipped, readable, at normal, narrow and zoom-equivalent sizes with two surfaces minimized at once', async () => {
  for (const view of STRIP_VIEWS) {
    const pg = await browser!.newPage({
      viewport: { width: view.width, height: view.height },
      deviceScaleFactor: view.scale,
    });
    try {
      await pg.goto(`${url}?strip=1&p2a=1`, { waitUntil: 'domcontentloaded' });
      await pg.waitForSelector('[data-testid="di-office-panel"]', { timeout: 30_000 });
      await pg.click('[data-testid="di-open-reception"]');
      await pg.waitForSelector('[data-testid="di-reception-inbox"]');
      await pg.click('[data-testid="di-reception-minimize"]');
      await pg.waitForSelector('[data-testid="di-reception-restore"]');
      await pg.click('[data-testid="di-office-panel-minimize"]');
      await pg.waitForSelector('[data-testid="di-office-panel-collapsed"]');
      await pg.waitForTimeout(1200); // settled, as an owner sees it
      const fit = await textFit(pg, '[data-testid="di-minimized-tray"]');
      const at = view.name;
      // optional evidence capture (no-op unless a folder is given): what the owner sees at this size
      if (process.env.DI_STRIP_SHOTS) {
        fs.mkdirSync(process.env.DI_STRIP_SHOTS, { recursive: true });
        await pg.screenshot({
          path: path.join(
            process.env.DI_STRIP_SHOTS,
            `strip-two-surfaces-${view.width}x${view.height}@${view.scale}x.png`,
          ),
        });
      }
      assert.deepEqual(
        fit.controls.map((c) => c.id).sort(),
        ['di-office-panel-collapsed', 'di-reception-restore'],
        `${at}: the strip holds both restore controls`,
      );
      for (const c of fit.controls) {
        assert.deepEqual(
          c.outside,
          [],
          `${at}: ${c.id}: rendered text outside the button's visible, unclipped region (pixels per side)`,
        );
        assert.deepEqual(
          c.textNotHit,
          [],
          `${at}: ${c.id}: text whose centre is not painted by the button (clipped or covered)`,
        );
        assert.equal(c.clipsContent, false, `${at}: ${c.id}: the button clips its own content`);
        assert.equal(
          c.controlWritingMode,
          'horizontal-tb',
          `${at}: ${c.id}: the flex container keeps a horizontal writing mode (only the label is vertical)`,
        );
        assert.deepEqual(
          c.controlOffWindow,
          { left: 0, right: 0, top: 0, bottom: 0 },
          `${at}: ${c.id}: the button is inside the window`,
        );
        assert.equal(c.hitsItself, true, `${at}: ${c.id}: a click at its centre reaches it`);
        // the FULL words, never shortened to fit
        assert.equal(
          c.texts.some((t) => t.text === FULL_LABEL[c.id]),
          true,
          `${at}: ${c.id}: the full label "${FULL_LABEL[c.id]}" is drawn (not shortened)`,
        );
        const label = c.texts.find((t) => t.text === FULL_LABEL[c.id]);
        assert.ok(label, `${at}: ${c.id}: the visible Restore label exists`);
        assert.ok(
          label!.width >= 10 && label!.height >= 40,
          `${at}: ${c.id}: the label is drawn with a readable box (${label!.width} x ${label!.height})`,
        );
        assert.ok(
          label!.left >= c.box.left - 0.75 && label!.right <= c.box.right + 0.75,
          `${at}: ${c.id}: the rendered label text (${label!.left} to ${label!.right}) lies between the button's left (${c.box.left}) and right (${c.box.right}) edges`,
        );
        assert.ok(
          label!.top >= c.box.top - 0.75 && label!.bottom <= c.box.bottom + 0.75,
          `${at}: ${c.id}: the rendered label text lies between the button's top and bottom edges`,
        );
      }
      const [a, b] = [...fit.controls].sort((x, y) => x.box.top - y.box.top);
      assert.ok(
        a.box.bottom <= b.box.top + 0.75,
        `${at}: the two slots are stacked, not overlapping`,
      );
    } finally {
      await pg.close();
    }
  }
});

test('textFit CONTRACT has TEETH: it measures the supported geometry exactly as the browser draws it (including the fixed-in-overflow-clip shape that was a false positive), and it THROWS for everything it cannot reason about', async () => {
  await withStrip();
  await p().evaluate(() => {
    const host = document.createElement('div');
    host.id = 'probe-host';
    host.style.cssText = 'position:absolute;left:10px;top:10px;z-index:99999;background:white';
    const wrap = (id: string, wrapStyle: string, buttonStyle: string, label: string) =>
      `<div id="${id}" style="overflow:hidden;width:100px;height:40px;${wrapStyle}"><button style="${buttonStyle}">${label}</button></div>`;
    host.innerHTML = [
      // SUPPORTED
      wrap('probe-fixed-free', '', 'position:fixed;left:300px;top:300px', 'fixed free'),
      // the exact shape that was a false positive: a fixed control inside App's `relative overflow-clip` world wrapper
      wrap(
        'probe-fixed-in-clip',
        'position:relative;overflow:clip',
        'position:fixed;left:300px;top:300px',
        'fixed clip',
      ),
      wrap('probe-abs-free', '', 'position:absolute;left:300px;top:300px', 'abs free'),
      wrap(
        'probe-abs-held',
        'position:relative',
        'position:absolute;left:300px;top:300px',
        'abs held',
      ),
      wrap('probe-static', '', 'margin-left:200px', 'static out'),
      wrap('probe-inside', '', 'margin:0', 'ok'),
      // UNSUPPORTED: each must be refused, never measured
      wrap(
        'probe-u-transform',
        'transform:translateZ(0)',
        'position:fixed;left:300px;top:300px',
        'u transform',
      ),
      wrap('probe-u-filter', 'filter:blur(0px)', 'margin:0', 'u filter'),
      wrap('probe-u-willchange', 'will-change:transform', 'margin:0', 'u will'),
      wrap('probe-u-contain', 'contain:paint', 'margin:0', 'u contain'),
      wrap('probe-u-clippath', 'clip-path:inset(0)', 'margin:0', 'u clip'),
      wrap('probe-u-mask', 'mask-image:linear-gradient(black, black)', 'margin:0', 'u mask'),
      wrap('probe-u-sticky', '', 'position:sticky;top:0', 'u sticky'),
      wrap(
        'probe-u-rounded',
        'border-radius:20px',
        'margin:0;position:static',
        'u rounded corner text',
      ),
    ].join('');
    document.body.append(host);
  });
  const state = async (id: string) => {
    const c = (await textFit(p(), `#${id}`)).controls;
    assert.equal(c.length, 1, `${id}: one control measured`);
    return c[0].outside.length > 0 ? 'clipped' : 'visible';
  };
  assert.deepEqual(
    {
      fixedFree: await state('probe-fixed-free'),
      fixedInOverflowClip: await state('probe-fixed-in-clip'),
      absFree: await state('probe-abs-free'),
      absHeld: await state('probe-abs-held'),
      staticOut: await state('probe-static'),
      inside: await state('probe-inside'),
    },
    {
      fixedFree: 'visible',
      fixedInOverflowClip: 'visible',
      absFree: 'visible',
      absHeld: 'clipped',
      staticOut: 'clipped',
      inside: 'visible',
    },
    'each supported case is reported exactly as the browser draws it',
  );
  const refused: Array<[string, string]> = [
    ['probe-u-transform', 'transform'],
    ['probe-u-filter', 'filter'],
    ['probe-u-willchange', 'will-change'],
    ['probe-u-contain', 'contain'],
    ['probe-u-clippath', 'clip-path'],
    ['probe-u-mask', 'mask'],
    ['probe-u-sticky', 'position: sticky'],
    ['probe-u-rounded', 'a rounded clip'],
  ];
  for (const [id, why] of refused)
    await assert.rejects(
      textFit(p(), `#${id}`),
      new RegExp(`textFit UNSUPPORTED geometry: ${why}`),
      `${id}: refused, not measured`,
    );
});

test('occlusion measure has TEETH: it reports an element drawn over text even when that element has pointer-events none (which a plain hit test cannot see), and nothing when nothing covers it', async () => {
  await withStrip();
  await p().evaluate(() => {
    document.body.insertAdjacentHTML(
      'beforeend',
      `<div id="occ-host" style="position:absolute;left:10px;top:10px;width:300px;height:120px;z-index:99998;background:white">
         <div id="occ-clear"><p style="margin:0">clear reading text</p><button>clear control</button></div>
       </div>
       <div id="occ-host2" style="position:absolute;left:10px;top:200px;width:300px;height:120px;z-index:99998;background:white">
         <p id="occ-covered-text" style="margin:0">covered reading text</p>
       </div>
       <div id="occ-overlay" data-testid="occ-overlay" style="position:fixed;left:0;top:190px;width:400px;height:60px;z-index:99999;pointer-events:none;background:black"></div>
       <div id="occ-host3" style="position:absolute;left:10px;top:400px;width:300px;height:60px;z-index:99998;background:white">
         <p style="margin:0">hidden-overlay reading text</p>
       </div>
       <div id="occ-hidden-overlay" style="position:fixed;left:0;top:390px;width:400px;height:60px;z-index:99999;visibility:hidden;background:black"></div>`,
    );
  });
  const plain = await p().evaluate(() => {
    const r = document.getElementById('occ-covered-text')!.getBoundingClientRect();
    return document.elementFromPoint(r.left + 4, r.top + r.height / 2)?.id ?? null;
  });
  assert.equal(
    plain,
    'occ-covered-text',
    'a PLAIN hit test cannot see the pointer-events:none overlay: it reports the text as topmost (why the probe neutralizes pointer-events)',
  );
  const covered = await occlusion(p(), '#occ-host2');
  assert.deepEqual(
    covered.covered.map((c) => ({ kind: c.kind, text: c.text, by: c.by })),
    [
      { kind: 'text', text: 'covered reading text', by: 'occ-overlay' },
      { kind: 'text', text: 'covered reading text', by: 'occ-overlay' },
      { kind: 'text', text: 'covered reading text', by: 'occ-overlay' },
    ],
    'the overlay drawn over the text is reported, with what covers it',
  );
  const clear = await occlusion(p(), '#occ-host');
  assert.ok(clear.sampled >= 4, 'it really sampled the clear text and control');
  assert.deepEqual(clear.covered, [], 'nothing covers the clear content');
  // a control whose CENTRE lies exactly on the window's bottom edge: that point is outside the page (elementFromPoint returns null there). It is a control half below
  // the fold (ordinary scroll), not a control covered by 'nothing', so it must not be reported as covered
  await p().evaluate(() => {
    document.body.insertAdjacentHTML(
      'beforeend',
      '<div id="occ-host-edge" style="position:fixed;left:20px;bottom:-15px;width:80px;height:30px;z-index:99998"><button style="width:80px;height:30px;margin:0">edge control</button></div>',
    );
  });
  const edge = await occlusion(p(), '#occ-host-edge');
  assert.deepEqual(
    edge.covered,
    [],
    'a point on the window edge is outside the page, not occlusion',
  );
  const hidden = await occlusion(p(), '#occ-host3');
  assert.ok(
    hidden.sampled >= 3 && hidden.covered.length === 0,
    'a visibility:hidden element paints nothing and is never reported',
  );
  assert.equal(
    await p().evaluate(() => document.querySelectorAll('[data-occlusion-probe]').length),
    0,
    'the probe style is removed afterwards',
  );
});

test('reflowReach CONTRACT has TEETH: content cut off the window edges, sideways scrollers and clippers are reported, screen-reader-only text and a vertical fade are not, and everything it cannot reason about is REFUSED', async () => {
  await withStrip();
  const vw = await p().evaluate(() => window.innerWidth);
  await p().evaluate((width) => {
    const card = (id: string, wrap: string, inner: string) =>
      `<div id="${id}" style="${wrap}">${inner}</div>`;
    const item = '<p style="margin:0">reflow words</p><button>reflow control</button>';
    document.body.insertAdjacentHTML(
      'beforeend',
      [
        card('rf-left', 'position:fixed;left:-50px;top:10px;z-index:99998;background:white', item),
        card(
          'rf-right',
          `position:fixed;left:${width - 60}px;top:70px;z-index:99998;background:white`,
          item,
        ),
        card(
          'rf-inside',
          'position:fixed;left:20px;top:130px;z-index:99998;background:white',
          item,
        ),
        card(
          'rf-scroll',
          'position:fixed;left:20px;top:190px;width:100px;overflow-x:auto;z-index:99998;background:white',
          '<div style="width:400px">wide scrolling content</div>',
        ),
        card(
          'rf-clip',
          'position:fixed;left:20px;top:230px;width:100px;overflow-x:hidden;z-index:99998;background:white',
          '<div style="width:400px">wide clipped content</div>',
        ),
        card(
          'rf-sronly',
          'position:fixed;left:20px;top:270px;z-index:99998;background:white',
          '<span style="position:absolute;width:1px;height:1px;overflow:hidden;white-space:nowrap">screen reader only words that are very wide</span>',
        ),
        card(
          'rf-fade',
          'position:fixed;left:20px;top:310px;z-index:99998;background:white;mask-image:linear-gradient(to bottom, black 80%, transparent)',
          item,
        ),
        card(
          'rf-u-transform',
          'position:fixed;left:20px;top:370px;z-index:99998;background:white;transform:translateX(0)',
          item,
        ),
        card(
          'rf-u-clip',
          'position:fixed;left:20px;top:430px;z-index:99998;background:white;clip-path:inset(0)',
          item,
        ),
        card(
          'rf-u-mask',
          'position:fixed;left:20px;top:490px;z-index:99998;background:white;mask-image:linear-gradient(to right, black 80%, transparent)',
          item,
        ),
        card(
          'rf-u-persp',
          'position:fixed;left:20px;top:550px;z-index:99998;background:white;perspective:500px',
          item,
        ),
      ].join(''),
    );
  }, vw);
  const r = async (id: string) => reflowReach(p(), `#${id}`);
  const left = await r('rf-left');
  assert.deepEqual(
    {
      controls: left.controlsOut.map((c) => c.id),
      leftOut: left.controlsOut[0]?.leftOutPx,
      textLeft: left.textsOut.length > 0,
    },
    { controls: ['button.'], leftOut: 50, textLeft: true },
    "content cut off the window's LEFT edge is reported with how far",
  );
  const right = await r('rf-right');
  assert.ok(
    right.controlsOut.length === 1 &&
      right.controlsOut[0].rightOutPx > 0 &&
      right.textsOut.length > 0,
    'content beyond the RIGHT edge is reported',
  );
  const inside = await r('rf-inside');
  assert.deepEqual(
    {
      c: inside.controlsOut,
      t: inside.textsOut,
      h: inside.horizontalContainers,
      controls: inside.controls,
    },
    { c: [], t: [], h: [], controls: 1 },
    'content inside the window reports nothing (and was really measured)',
  );
  const scroll = await r('rf-scroll');
  assert.deepEqual(
    scroll.horizontalContainers.map((h) => ({ kind: h.kind, byPx: h.byPx })),
    [{ kind: 'scrolls', byPx: 300 }],
    'a sideways scroller is reported with how much it needs',
  );
  const clip = await r('rf-clip');
  assert.deepEqual(
    clip.horizontalContainers.map((h) => ({ kind: h.kind, byPx: h.byPx })),
    [{ kind: 'clips', byPx: 300 }],
    'a sideways clipper is reported',
  );
  const sr = await r('rf-sronly');
  assert.deepEqual(
    { h: sr.horizontalContainers, t: sr.textsOut, texts: sr.texts },
    { h: [], t: [], texts: 0 },
    'screen-reader-only text (a 1 px box) is not visible content and is never reported',
  );
  const fade = await r('rf-fade');
  assert.deepEqual(
    { c: fade.controlsOut, t: fade.textsOut, controls: fade.controls },
    { c: [], t: [], controls: 1 },
    'a VERTICAL fade mask is supported: it cannot move or hide content sideways',
  );
  for (const [id, why] of [
    ['rf-u-transform', 'transform'],
    ['rf-u-clip', 'clip-path'],
    ['rf-u-mask', 'a mask that is not a vertical linear gradient'],
    ['rf-u-persp', 'perspective'],
  ] as const)
    await assert.rejects(
      r(id),
      new RegExp(`reflowReach UNSUPPORTED geometry: ${why}`),
      `${id}: refused, not measured`,
    );
});

/*
 * AC-UX-13 / WCAG 2.2 SC 1.4.10 Reflow, on the REAL office panel mounted in the panel harness with the SAME dock arithmetic as the App
 * (src/diPanelLayout.ts): the harness has no world, no fixture banner and no ambient legend, so this proves the panel's own content and
 * dock widths only. Occlusion by world overlays and the world's yielding are proved in the real-App suites (diMissionRequest.test.ts).
 * The widths: 320 (the standard's), 360 and 414 (common phones) and 640; with and without a minimized surface (which brings the 44 px strip).
 */
test('AC-UX-13 / SC 1.4.10, office panel reflow [harness: the real panel and the real dock arithmetic]: at 320, 360, 414 and 640 the panel and the strip both fit, and no reading or control content is cut off or needs sideways scrolling', async () => {
  // INDEPENDENT pin: the design width itself (pass C, C-V1: measured from 01-home.jpg to the panel's own EDGE RULE at x 852 in the
  // 1280 frame, 1280 - 852 = 428; not the dark lead-in from x 847, which would read 433)
  const DESIGN = 428;
  const STRIP = 44;
  const findings: string[] = [];
  for (const width of [320, 360, 414, 640]) {
    for (const withChip of [false, true]) {
      const pg = await browser!.newPage({ viewport: { width, height: 800 } });
      try {
        await pg.goto(`${url}?strip=1&p2a=1`, { waitUntil: 'domcontentloaded' });
        await pg.waitForSelector('[data-testid="di-office-panel"]', { timeout: 30_000 });
        if (withChip) {
          await pg.click('[data-testid="di-open-reception"]');
          await pg.waitForSelector('[data-testid="di-reception-inbox"]');
          await pg.click('[data-testid="di-reception-minimize"]');
          await pg.waitForSelector('[data-testid="di-reception-restore"]');
        }
        await pg.waitForTimeout(600);
        const at = `${width} px wide, ${withChip ? 'a surface minimized (strip 44 px)' : 'nothing minimized'}`;
        const reach = await reflowReach(pg, '[data-testid="di-office-panel"]');
        const expected = Math.min(DESIGN, width - (withChip ? STRIP : 0));
        assert.equal(
          reach.root?.width,
          expected,
          `${at}: the panel is ${expected} px (${DESIGN}, or what the window leaves beside the strip)`,
        );
        assert.ok(
          reach.root!.left >= 0 && reach.root!.right <= width,
          `${at}: the panel box ${reach.root!.left}..${reach.root!.right} lies inside 0..${width}`,
        );
        if (withChip) {
          const tray = await pg.evaluate(() => {
            const r = document
              .querySelector('[data-testid="di-minimized-tray"]')!
              .getBoundingClientRect();
            return { left: r.left, right: r.right };
          });
          assert.ok(
            tray.left >= 0 && tray.right <= width,
            `${at}: the strip ${tray.left}..${tray.right} lies inside the window`,
          );
          assert.ok(
            tray.right <= reach.root!.left + 0.75 && Math.abs(tray.right - reach.root!.left) <= 1,
            `${at}: the strip sits at the panel's left edge, covering none of it`,
          );
        }
        assert.equal(reach.pageHorizontalScroll, false, `${at}: the page does not scroll sideways`);
        if (reach.controlsOut.length || reach.textsOut.length || reach.horizontalContainers.length)
          findings.push(
            `${at}: ${reach.controlsOut.length} controls and ${reach.textsOut.length} text rects reach past an edge; ${reach.horizontalContainers.length} containers scroll or clip sideways ${JSON.stringify(reach.horizontalContainers.slice(0, 4))}`,
          );
      } finally {
        await pg.close();
      }
    }
  }
  assert.deepEqual(
    findings,
    [],
    'no reading or control content is cut off the window or needs sideways scrolling at any width',
  );
});

test('tabTraversal CONTRACT has TEETH: the invisible tab stops (clipped, zero-size container, transparent, no indicator, off-screen, covered) are reported, the controls really OUT of the sequence (display none, visibility hidden, inert) are absent, and ordinary controls are clean', async () => {
  const pg = await browser!.newPage({ viewport: { width: 400, height: 500 } });
  try {
    await pg.setContent(`<!doctype html><html><head><style>
      body { margin: 0; background: white; }
      button { display: block; width: 90px; height: 30px; margin: 4px; }
    </style></head><body>
      <button>fine</button>
      <div style="width:0;height:0;overflow:hidden"><button>zero container</button></div>
      <div style="width:40px;height:20px;overflow:hidden"><button style="margin-left:-200px">clipped away</button></div>
      <button style="opacity:0">transparent</button>
      <button style="outline:none">no ring</button>
      <button style="position:fixed;left:-300px;top:10px">off screen</button>
      <button style="display:none">display none</button>
      <button style="visibility:hidden">visibility hidden</button>
      <div inert><button>inert wrapper</button></div>
      <button id="under">covered</button>
      <div style="position:fixed;left:0;top:0;width:400px;height:500px;pointer-events:none;background:white;z-index:5;opacity:0.0" id="veil"></div>
      <button style="position:relative;z-index:9">last</button>
    </body></html>`);
    // the veil is not painted yet; cover ONLY the "covered" button, with an opaque element that ignores the pointer
    await pg.evaluate(() => {
      const under = document.getElementById('under')!.getBoundingClientRect();
      const veil = document.getElementById('veil')!;
      Object.assign(veil.style, {
        left: `${under.left - 4}px`,
        top: `${under.top - 4}px`,
        width: `${under.width + 8}px`,
        height: `${under.height + 8}px`,
        opacity: '1',
      });
    });
    // focus starts at the END of the document (as after clicking a control that then unmounts): the traversal must still start at the document's start
    await pg.evaluate(() =>
      (document.querySelector('button[style*="z-index:9"]') as HTMLElement).focus(),
    );
    const t = await tabTraversal(pg, { regions: { clipwrap: 'div[style*="overflow:hidden"]' } });
    const back = await tabTraversal(pg, { backward: true });
    assert.notEqual(
      back.ended,
      'max',
      'the backward walk reached the START of the sequence (it left the document or wrapped), not the cut-off maximum',
    );
    assert.notEqual(
      t.ended,
      'max',
      'the traversal reached the END of the sequence (focus left the document after the last stop), not the cut-off maximum',
    );
    assert.equal(t.ended, 'left-document');
    assert.deepEqual(
      t.stops.map((s) => s.id),
      [
        'fine',
        'zero container',
        'clipped away',
        'transparent',
        'no ring',
        'off screen',
        'covered',
        'last',
      ],
      'the sequence has exactly the focusable controls: display none, visibility hidden and an inert wrapper are OUT of it',
    );
    assert.deepEqual(
      tabProblems(t).map((x) => x.id),
      ['zero container', 'clipped away', 'transparent', 'no ring', 'off screen', 'covered'],
      'every invisible tab stop is reported, and the ordinary controls are not',
    );
    const why = (id: string) =>
      tabProblems(t)
        .find((x) => x.id === id)!
        .why.join(' | ');
    assert.match(
      why('zero container'),
      /nothing of it is drawn where it sits/,
      'a control clipped by a zero-size container is in the sequence yet drawn nowhere',
    );
    assert.match(
      why('transparent'),
      /no visible focus indicator/,
      'opacity:0 passes a hit test but paints nothing: only the pixel comparison sees it',
    );
    assert.match(why('no ring'), /no visible focus indicator/);
    assert.match(why('off screen'), /none of its box is inside the window/);
    assert.match(
      why('covered'),
      /nothing of it is drawn where it sits/,
      'a pointer-events:none cover is seen because the probe neutralizes pointer-events',
    );
    assert.deepEqual(
      t.stops.filter((x) => x.region === 'clipwrap').map((x) => x.id),
      ['zero container', 'clipped away'],
      'a stop reports the named region that contains it',
    );
    assert.ok(
      tabProblems(t, { clipwrap: 0, default: 0.8 }).every(
        (x) => x.id !== 'clipped away' || !x.why.some((w) => /drawn where it sits/.test(w)),
      ),
      'a per-region bar applies only to that region',
    );
    assert.deepEqual(
      back.stops.map((x) => x.id),
      [...t.stops.map((x) => x.id)].reverse(),
      'Shift+Tab visits exactly the forward stops in the opposite order (the same invisible stops are reachable backwards)',
    );
    assert.deepEqual(
      tabProblems(back)
        .map((x) => x.id)
        .sort(),
      tabProblems(t)
        .map((x) => x.id)
        .sort(),
      'and the same stops are reported invisible going backwards',
    );
    const ok = t.stops.find((s) => s.id === 'fine')!;
    assert.ok(
      ok.hitSamples === ok.sampled && ok.sampled === 25 && ok.indicator.pixelsChanged === true,
      'an ordinary control is fully hit, and focusing it changes pixels',
    );
    assert.equal(
      await pg.evaluate(
        () => document.querySelectorAll('[data-tab-probe], [data-tab-seen]').length,
      ),
      0,
      'the probe leaves nothing behind',
    );
  } finally {
    await pg.close();
  }
});

test('Reception dialog header reflows at 320 (SC 1.4.10): the title gets the whole row and the actions their own row; wider windows keep one row [harness: the real Reception dialog]', async () => {
  const measure = async (width: number) => {
    const pg = await browser!.newPage({ viewport: { width, height: 800 } });
    try {
      await pg.goto(`${url}?strip=1&p2a=1`, { waitUntil: 'domcontentloaded' });
      await pg.waitForSelector('[data-testid="di-office-panel"]', { timeout: 30_000 });
      await pg.click('[data-testid="di-open-reception"]');
      await pg.waitForSelector('[data-testid="di-reception-inbox"]');
      await pg.waitForTimeout(400);
      return await pg.evaluate(() => {
        const box = (sel: string) => {
          const r = document.querySelector(sel)!.getBoundingClientRect();
          return { left: r.left, right: r.right, top: r.top, bottom: r.bottom, width: r.width };
        };
        return {
          vw: window.innerWidth,
          head: box('[data-testid="di-reception-inbox"] .di-op-inspector-head'),
          heading: box('[data-testid="di-reception-inbox"] .di-op-inspector-heading'),
          tools: box('[data-testid="di-reception-inbox"] .di-op-inspector-tools'),
          minimize: box('[data-testid="di-reception-minimize"]'),
          close: box('[data-testid="di-reception-inbox-close"]'),
        };
      });
    } finally {
      await pg.close();
    }
  };
  const narrow = await measure(320);
  assert.ok(
    narrow.heading.width >= narrow.head.width - 40,
    `320: the title column is the dialog's whole row (${narrow.heading.width} of ${narrow.head.width} px), not a squeezed column`,
  );
  assert.ok(
    narrow.tools.top >= narrow.heading.bottom - 1,
    '320: the actions sit on a row of their own below the title',
  );
  for (const b of [narrow.minimize, narrow.close])
    assert.ok(b.left >= 0 && b.right <= narrow.vw, '320: both actions are inside the window');
  for (const width of [640, 1440]) {
    const wide = await measure(width);
    assert.ok(
      wide.tools.left >= wide.heading.right - 1 && wide.tools.top < wide.heading.bottom,
      `${width}: the title and the actions still share one row`,
    );
  }
});

test('AC5 samples [reachable: an explicit fixture preview]: drawn only through fixtureMode, every sample labelled FIXTURE, no controls; a live state shows none', async () => {
  await p().waitForSelector('[data-testid="di-mission-samples"]');
  const view = await p().evaluate(() => {
    const section = document.querySelector('[data-testid="di-mission-samples"]')!;
    return {
      title: section.querySelector('[data-testid="di-mission-samples-title"]')?.textContent?.trim(),
      cards: [...section.querySelectorAll('[data-testid="di-mission-sample"]')].map((c) => ({
        title:
          c.querySelector('[data-testid="di-mission-sample-title"]')?.textContent?.trim() ?? '',
        state:
          c.querySelector('[data-testid="di-mission-sample-state"]')?.textContent?.trim() ?? '',
        needs:
          c.querySelector('[data-testid="di-mission-sample-needs-you"]')?.textContent?.trim() ??
          null,
      })),
      controls: section.querySelectorAll('button, a[href], input, select, textarea, [tabindex]')
        .length,
    };
  });
  assert.match(view.title ?? '', /FIXTURE DATA/);
  assert.deepEqual(
    view.cards.map((c) => c.state),
    ['Proposal ready', 'Building', 'Question for you', 'Stalled', 'Ready to deliver', 'Merged'],
  );
  for (const c of view.cards)
    assert.match(c.title, /^FIXTURE · \S/, `every sample is labelled: ${c.title}`);
  assert.deepEqual(
    view.cards.filter((c) => c.needs !== null).map((c) => c.needs),
    ['▲ Needs you', '▲ Needs you', '▲ Needs you', '▲ Needs you'],
    'needing the owner is said with a shape and words',
  );
  assert.equal(view.controls, 0, 'samples carry no control: nothing here can be acted on');
  // a state that is not a fixture preview never shows them (the office says so itself: fixtureMode false)
  await p().evaluate(() => window.__diHarness.update((st) => ({ ...st, fixtureMode: false })));
  await p().waitForSelector('[data-testid="di-mission-samples"]', { state: 'detached' });
});
