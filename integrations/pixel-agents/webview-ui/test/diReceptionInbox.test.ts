/**
 * The reception INBOX driven by real clicks and key presses in a real browser: the panel's attention
 * entry opens ONE dialog (PR69's full-size details pattern): headline, search and filter, what could
 * not be checked, then cards that say what happened, why and the next step, in ONE scroll region. The
 * page (`test/harness/di-panel.html`) mounts the real `DiOfficePanel` over the office state the real
 * server projects from the labelled packaged fixture `herdr-task-session-mismatch-v2`. The in-world
 * desk entrance to the SAME dialog is covered in diReceptionEntrances.test.ts.
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
import { PLAIN_CAUSES, plainReasons } from '../src/components/diReceptionPlain.ts';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const fixture = JSON.parse(
  fs.readFileSync(
    path.join(root, 'test/fixtures/di-office-state-session-mismatch-v2.FIXTURE.json'),
    'utf-8',
  ),
) as { state: DiOfficeState };
const reception = fixture.state.reception!;
const entry = reception.entries[0];
const mission = fixture.state.missions.missions.find((m) => m.missionRef === entry.missionRef)!;
const firstDrawn = mission.members.members.find((m) => m.characterId !== null)?.characterId ?? null;

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
const INBOX = '[data-testid="di-reception-inbox"]';
const OPEN = '[data-testid="di-open-reception"]';
const ENTRY = `${INBOX} [data-testid="di-reception-entry"]`;
const active = (selector: string) =>
  p().evaluate((s) => document.activeElement?.matches(s) ?? false, selector);

async function openByKeyboard() {
  await p().focus(OPEN);
  await p().keyboard.press('Enter');
  await p().waitForSelector(INBOX);
}

/** More synthetic entries on the labelled fixture state (distinct identities, two categories). */
async function addEntries(n: number, category?: string) {
  await p().evaluate(
    ([count, cat]) => {
      window.__diHarness.update((state) => {
        const base = state.reception!.entries[0];
        for (let i = 0; i < count; i++)
          state.reception!.entries.push({
            ...base,
            key: `${base.key}#extra${cat ?? ''}${i}`,
            signature: `${base.signature}#extra${cat ?? ''}${i}`,
            nativeId: `${base.nativeId}-extra${cat ?? ''}${i}`,
            category: (cat ?? base.category) as typeof base.category,
          });
        state.reception!.entriesTotal = state.reception!.entries.length;
        return state;
      });
    },
    [n, category ?? null] as const,
  );
}

test('the attention entry opens ONE modal dialog: focus inside, the world inert, Escape returns focus', async () => {
  assert.equal(await p().locator(INBOX).count(), 0, 'closed to begin with');
  await openByKeyboard();
  assert.equal(await p().locator(INBOX).count(), 1, 'exactly one inbox');
  const dialog = await p().evaluate(() => {
    const d = document.querySelector('[data-testid="di-reception-inbox"]')!;
    return {
      role: d.getAttribute('role'),
      modal: d.getAttribute('aria-modal'),
      name: document.getElementById(d.getAttribute('aria-labelledby')!)?.textContent,
      focusInside: d.contains(document.activeElement) || d === document.activeElement,
      rootInert: document.getElementById('root')!.hasAttribute('inert'),
    };
  });
  assert.deepEqual(dialog, {
    role: 'dialog',
    modal: 'true',
    name: 'Reception',
    focusInside: true,
    rootInert: true,
  });
  await p().keyboard.press('Escape');
  await p().waitForSelector(INBOX, { state: 'detached' });
  assert.ok(await active(OPEN), 'focus returns to the control that opened it');
  assert.equal(
    await p().evaluate(() => document.getElementById('root')!.hasAttribute('inert')),
    false,
  );
});

test('Tab stays inside the inbox and wraps; Close returns focus to the entry', async () => {
  await openByKeyboard();
  const inside = () =>
    p().evaluate(() =>
      document.querySelector('[data-testid="di-reception-inbox"]')!.contains(document.activeElement),
    );
  for (let i = 0; i < 40; i++) {
    await p().keyboard.press('Tab');
    assert.ok(await inside(), `Tab ${i + 1} stayed inside the dialog`);
  }
  for (let i = 0; i < 6; i++) {
    await p().keyboard.press('Shift+Tab');
    assert.ok(await inside(), 'Shift+Tab stayed inside the dialog');
  }
  await p().click('[data-testid="di-reception-inbox-close"]');
  await p().waitForSelector(INBOX, { state: 'detached' });
  assert.ok(await active(OPEN));
});

test('attention first: the headline, then search and filter, then the entries; the policy is one quiet line', async () => {
  await addEntries(2, 'ready_for_review');
  await openByKeyboard();
  const y = (sel: string) =>
    p().evaluate((s) => document.querySelector(s)!.getBoundingClientRect().top, `${INBOX} ${sel}`);
  const headline = await y('[data-testid="di-reception-inbox-summary"]');
  const search = await y('[data-testid="di-reception-search"]');
  const firstEntry = await y('[data-testid="di-reception-entry"]');
  assert.ok(headline < search && search < firstEntry, `order ${headline} < ${search} < ${firstEntry}`);
  assert.match((await p().textContent('[data-testid="di-reception-inbox-summary"]'))!, /^\d+ needs? attention$/);
  // the policy paragraph is one quiet line; the full text is behind a closed disclosure
  const quiet = (await p().textContent('[data-testid="di-reception-inbox-quiet"]'))!;
  assert.ok(quiet.length < 80, quiet);
  const about = await p().evaluate(() => {
    const d = document.querySelector('[data-testid="di-reception-about"]') as HTMLDetailsElement;
    return { open: d.open, text: d.textContent ?? '' };
  });
  assert.equal(about.open, false);
  assert.match(about.text, /cannot approve, start, retry or deliver anything/);
  assert.equal(
    await p().evaluate(() => {
      const inbox = document.querySelector('[data-testid="di-reception-inbox"]')!;
      return [...inbox.querySelectorAll('p, div')].filter(
        (e) => e.closest('details') === null && /cannot approve, start, retry or deliver anything/.test(e.textContent ?? '') && e.children.length === 0,
      ).length;
    }),
    0,
    'the long policy text is not in the first layer',
  );
});

test('what could not be checked is visible in the first layer in plain words; the catalog does not report clarification, and each source says what it covers', async () => {
  await openByKeyboard();
  const box = p().locator(`${INBOX} [data-testid="di-reception-unknowns"]`);
  assert.equal(await box.isVisible(), true, 'visible without opening anything');
  const lines = await p().$$eval(`${INBOX} [data-testid="di-reception-unknown"]`, (els) =>
    els.map((e) => e.textContent ?? ''),
  );
  assert.ok(lines.length > 0, 'the fixture has unchecked Missions');
  for (const l of lines) {
    assert.doesNotMatch(l, /can[’']t say/i);
    assert.match(l, /couldn’t be checked/);
    assert.doesNotMatch(l, /[a-z]+_[a-z]+/, `no raw code in: ${l}`);
  }
  const notReported = (await p().textContent('[data-testid="di-reception-not-reported"]'))!;
  // The pinned answer predates the delivery block, so the catalog reports no clarification AND no delivery
  // proposals: but only clarification is a kind the catalog "does not carry". Delivery proposals are a source
  // of their own, whose "not reported by this catalog" is the coverage line below, never a "none".
  assert.match(notReported, /^\S* ?Not reported by the catalog: Clarification\.$/);
  assert.doesNotMatch(notReported, /Delivery awaiting a separate decision/);
  const sources = await p().$$eval(`${INBOX} [data-testid="di-reception-sources"] li[data-source]`, (els) =>
    els.map((e) => [e.getAttribute('data-source'), e.getAttribute('data-outcome'), e.textContent ?? '']),
  );
  assert.deepEqual(
    sources.map((x) => x[0]),
    ['grok_delivery_presentations', 'request_panel'],
  );
  assert.match(String(sources[0][2]), /This catalog does not report delivery presentations/, 'a catalog that sends no block is unknown, not none');
  assert.equal(sources[0][1], 'unsupported');
  // the first-layer line of every source never says "none" (the explanations may say it is NOT none)
  const summaries = await p().$$eval(`${INBOX} [data-testid="di-reception-sources"] li[data-source] > span`, (els) =>
    els.map((e) => e.textContent ?? ''),
  );
  assert.equal(summaries.length, 2);
  assert.match(summaries[0], /not reported here/);
  assert.doesNotMatch(summaries.join(' '), /\bnone\b/i);
  // the category-by-category stack is one disclosure BEHIND the entries, with the exact reasons inside
  const coverage = await p().evaluate(() => {
    const d = document.querySelector('[data-testid="di-reception-coverage"]') as HTMLDetailsElement;
    return d.open;
  });
  assert.equal(coverage, false, 'the coverage list does not lead');
  await p().click(`${INBOX} [data-testid="di-reception-coverage"] > summary`);
  const rows = await p().$$eval('[data-testid="di-reception-categories"] > li', (lis) =>
    lis.map((li) => ({
      category: li.getAttribute('data-category'),
      outcome: li.getAttribute('data-outcome'),
      line: li.querySelector('summary')!.textContent ?? '',
    })),
  );
  assert.deepEqual(
    rows.map((r) => r.category),
    reception.categories.map((c) => c.category),
    'all of the desk’s categories, in its order',
  );
  for (const r of rows) {
    assert.doesNotMatch(r.line, /can[’']t say/i);
    if (r.outcome === 'unsupported')
      assert.match(r.line, /not reported by this source/, `${r.category} says it is not reported`);
    if (r.outcome === 'unsupported' || r.outcome === 'unavailable' || r.outcome === 'partial')
      assert.doesNotMatch(r.line, /nothing reported|none|clear|healthy/i, `${r.category} never reads as empty`);
  }
});

test('each card says what happened, why it needs the person and the next supported step; the exact record is a closed disclosure', async () => {
  await openByKeyboard();
  assert.equal(await p().locator(ENTRY).count(), reception.entries.length);
  const first = p().locator(ENTRY).first();
  assert.equal(await first.getAttribute('data-entry-key'), entry.key);
  const happened = await first.locator('[data-testid="di-reception-happened"]').textContent();
  const why = await first.locator('[data-testid="di-reception-why"]').textContent();
  const next = await first.locator('[data-testid="di-reception-next"]').textContent();
  for (const c of entry.causes) {
    assert.ok(happened!.includes(PLAIN_CAUSES[c.cause].happened), `what happened: ${c.cause}`);
    assert.ok(why!.includes(PLAIN_CAUSES[c.cause].why), `why: ${c.cause}`);
  }
  // a blockers entry has no request-panel route: the card says so, and only then names the Operator route (from the same route data that decides the buttons)
  assert.match(next!, /^This kind of item has no request-panel route\. Outside this office: act through the Mission['’]s operator and its Herdr team\.?$/);
  assert.equal(await first.locator('[data-testid="di-reception-open-panel"]').count(), 0, 'and no request-panel button');
  assert.doesNotMatch(next!, /read-only/i, 'the shared read-only sentence is said once, at the top');
  const labels = await first.locator('dt').allTextContents();
  assert.deepEqual(labels, ['What happened', 'Why it needs you', 'What you can do']);
  const closed = await first.evaluate((li) => {
    const d = li.querySelector('details')!;
    return { open: d.open, text: d.querySelector('summary')!.textContent };
  });
  assert.deepEqual(closed, { open: false, text: 'Identity, causes and the route to act' });
  // the full route text and identity are inside it, deliberately one click away
  const record = first.locator('details').first();
  await record.locator('> summary').click();
  assert.match((await record.textContent())!, /Route to act/);
});

test('the list fills the dialog: no 220px cap and no nested scroller, at ordinary and at 1280x720', async () => {
  await addEntries(14);
  for (const viewport of [
    { width: 1600, height: 1000 },
    { width: 1280, height: 720 },
  ]) {
    await p().setViewportSize(viewport);
    await openByKeyboard();
    const m = await p().evaluate(() => {
      const dlg = document.querySelector('[data-testid="di-reception-inbox"]')!;
      const list = document.querySelector('[data-testid="di-reception-entries"]') as HTMLElement;
      const body = document.querySelector('[data-testid="di-reception-inbox-body"]') as HTMLElement;
      const cs = getComputedStyle(list);
      const scrollers = [...dlg.querySelectorAll('*')]
        .filter((el) => {
          const c = getComputedStyle(el);
          return (c.overflowY === 'auto' || c.overflowY === 'scroll') && el.scrollHeight > el.clientHeight + 1;
        })
        .map((el) => el.getAttribute('data-testid') ?? el.tagName);
      return {
        maxHeight: cs.maxHeight,
        overflowY: cs.overflowY,
        listScrolls: list.scrollHeight > list.clientHeight + 1,
        listHeight: list.getBoundingClientRect().height,
        bodyHeight: body.clientHeight,
        scrollers,
        dialogHeight: dlg.getBoundingClientRect().height,
      };
    });
    const at = `${viewport.width}x${viewport.height}`;
    assert.equal(m.maxHeight, 'none', `${at}: the list is not capped`);
    assert.equal(m.overflowY, 'visible', `${at}: the list is not its own scroller`);
    assert.equal(m.listScrolls, false, `${at}: the list does not scroll inside itself`);
    assert.ok(m.listHeight > 600, `${at}: the list takes the height its cards need (${m.listHeight})`);
    assert.deepEqual(m.scrollers, ['di-reception-inbox-body'], `${at}: ONE scroll region`);
    assert.ok(m.dialogHeight <= viewport.height, `${at}: the dialog fits the window`);
    // the last card is reachable by scrolling that one region
    await p().evaluate(() => {
      const body = document.querySelector('[data-testid="di-reception-inbox-body"]')!;
      body.scrollTop = body.scrollHeight;
    });
    const lastVisible = await p().evaluate(() => {
      const cards = document.querySelectorAll('[data-testid="di-reception-entry"]');
      const last = cards[cards.length - 1].getBoundingClientRect();
      const dlg = document.querySelector('[data-testid="di-reception-inbox"]')!.getBoundingClientRect();
      return last.bottom <= dlg.bottom + 1 && last.top >= dlg.top;
    });
    assert.ok(lastVisible, `${at}: the last card scrolls fully into view`);
    await p().keyboard.press('Escape');
    await p().waitForSelector(INBOX, { state: 'detached' });
  }
});

test('constrained 1280x720: the headline, the search and the first card are in view without scrolling', async () => {
  await p().setViewportSize({ width: 1280, height: 720 });
  await openByKeyboard();
  const fits = await p().evaluate(() => {
    const within = (sel: string) => {
      const r = document.querySelector(sel)!.getBoundingClientRect();
      return r.top >= 0 && r.bottom <= window.innerHeight;
    };
    return {
      headline: within('[data-testid="di-reception-inbox-summary"]'),
      search: within('[data-testid="di-reception-search"]'),
      firstCardTop:
        document.querySelector('[data-testid="di-reception-entry"]')!.getBoundingClientRect().top <
        window.innerHeight,
    };
  });
  assert.deepEqual(fits, { headline: true, search: true, firstCardTop: true });
});

test('search narrows the cards; an empty result says it is a filter result; keyboard moves through them', async () => {
  await addEntries(3);
  await openByKeyboard();
  const total = await p().locator(ENTRY).count();
  assert.equal(total, reception.entries.length + 3);
  const search = '[data-testid="di-reception-search"]';
  await p().fill(search, 'extra1');
  assert.equal(await p().locator(ENTRY).count(), 1);
  assert.match((await p().textContent('[data-testid="di-reception-filter-status"]'))!, /^Showing 1 of 4 entries\.$/);
  await p().fill(search, 'zzz-nothing-matches');
  assert.equal(await p().locator(ENTRY).count(), 0);
  assert.match((await p().textContent('[data-testid="di-reception-no-entries"]'))!, /^No entries match this search or filter\./);
  await p().click(`${INBOX} [data-testid="di-reception-no-entries"] button`);
  assert.equal(await p().locator(ENTRY).count(), total, 'Clear the filter restores every entry');
  assert.equal(await p().inputValue(search), '');
  // keyboard: ArrowDown from the search box lands on the first card; arrows/Home/End rove; Escape closes
  await p().focus(search);
  await p().keyboard.press('ArrowDown');
  assert.ok(await active('[data-testid="di-reception-entry"]'), 'ArrowDown reaches the first card');
  const keyOf = () => p().evaluate(() => document.activeElement?.getAttribute('data-entry-key'));
  const first = await keyOf();
  await p().keyboard.press('ArrowDown');
  assert.notEqual(await keyOf(), first);
  await p().keyboard.press('End');
  const last = await keyOf();
  await p().keyboard.press('ArrowDown');
  assert.equal(await keyOf(), last, 'never past the end');
  await p().keyboard.press('Home');
  assert.equal(await keyOf(), first);
  await p().keyboard.press('Escape');
  await p().waitForSelector(INBOX, { state: 'detached' });
  assert.ok(await active(OPEN), 'focus returns to the entry that opened the inbox');
});

test('category chips filter the cards, say what is pressed and never change a canonical outcome', async () => {
  await addEntries(2, 'ready_for_review');
  await openByKeyboard();
  const chips = p().locator(`${INBOX} [data-testid="di-reception-chips"] button`);
  assert.ok((await chips.count()) >= 3, 'All plus one chip per category with entries');
  assert.equal(await chips.first().getAttribute('aria-pressed'), 'true');
  const labels = await chips.allTextContents();
  assert.match(labels[0], /^All \(3\)$/);
  await p().click(`${INBOX} [data-testid="di-reception-chips"] button[data-category="ready_for_review"]`);
  assert.equal(await p().locator(ENTRY).count(), 2);
  assert.equal(
    await p().getAttribute(`${INBOX} [data-testid="di-reception-chips"] button[data-category="ready_for_review"]`, 'aria-pressed'),
    'true',
  );
  await p().click(`${INBOX} [data-testid="di-reception-chips"] button[data-category="ready_for_review"]`);
  assert.equal(await p().locator(ENTRY).count(), 3, 'pressing it again shows everything');
  // the desk's own outcome for blockers is untouched by any filter
  await p().click(`${INBOX} [data-testid="di-reception-coverage"] > summary`);
  assert.equal(
    await p().getAttribute(`${INBOX} li[data-category="blockers"]`, 'data-outcome'),
    reception.categories.find((c) => c.category === 'blockers')!.outcome,
  );
});

test('hiding an entry is a view preference shared by the inbox and the sidebar; it resolves nothing and CHANGES NO COUNT', async () => {
  // The requirement this states: "Hide in this view" resolves, approves and dismisses nothing (docs/pixel-agents-office.md, Hiding), so it cannot change how many things need the person, and the
  // sidebar and the dialog must keep saying the same true number (AC-4.7-16). The entry leaves the LIST; the number does not move, and the notice says the entry is hidden here and still counted.
  const sidebar = () => p().textContent('[data-testid="di-reception-summary"]');
  assert.match((await sidebar())!, /1 needs attention/);
  await openByKeyboard();
  await p().click(`${ENTRY} button:has-text("Hide in this view")`);
  assert.equal(await p().locator(ENTRY).count(), 0);
  const headline = (await p().textContent('[data-testid="di-reception-inbox-summary"]'))!;
  assert.match(headline, /1 needs attention/);
  assert.doesNotMatch(headline, /Nothing listed/);
  assert.match((await p().textContent('[data-testid="di-reception-hidden"]'))!, /1 hidden in this view only, and still counted in the number above/);
  await p().keyboard.press('Escape');
  await p().waitForSelector(INBOX, { state: 'detached' });
  assert.match((await sidebar())!, /1 needs attention/);
  assert.doesNotMatch((await sidebar())!, /Nothing listed/);
  // the category still says what it reports: hiding changed no canonical outcome
  await openByKeyboard();
  assert.equal(
    await p().getAttribute(`${INBOX} li[data-category="blockers"]`, 'data-outcome'),
    'present',
  );
  await p().click(`${INBOX} [data-testid="di-reception-hidden"] button`);
  assert.equal(await p().locator(ENTRY).count(), 1);
});

test('the inbox carries no control that approves, resolves, starts or delivers anything', async () => {
  await addEntries(2, 'ready_for_review');
  await openByKeyboard();
  const labels = await p().$$eval(`${INBOX} button`, (bs) => bs.map((b) => b.textContent?.trim() ?? ''));
  for (const l of labels)
    assert.match(
      l,
      // Minimize (AC3) hides the inbox and acts on nothing; the deny list below still applies to every label
      /^(Close|Minimize|Show this Mission|Show details|Show evidence|Hide in this view|Show \d+ hidden|Clear the filter|All \(\d+\)|[A-Za-z /]+ \(\d+\))$/,
    );
  assert.doesNotMatch(labels.join(' '), /approve|resolve|retry|dispatch|deliver|authori[sz]e|start/i);
  assert.equal(await p().locator(`${INBOX} a[href]`).count(), 0, 'nothing links out');
});

// ── Phase 1c: the collapsed sidebar, reasons, the reauthorization copy, the empty state ──────────

/** A Mission of the fixture with NO drawn member: it has no camera destination. */
const undrawn = fixture.state.missions.missions.find((m) =>
  m.members.members.every((x) => x.characterId === null),
)!;

/** Open the harness with the sidebar collapsed (the panel then draws only its "show" button). */
async function loadCollapsed() {
  await page?.close();
  page = await browser!.newPage({ viewport: { width: 1600, height: 1000 } });
  await page.goto(`${url}?collapsed=1`, { waitUntil: 'domcontentloaded' });
  await page.waitForSelector('[data-testid="di-office-panel-collapsed"]', { timeout: 30_000 });
  assert.equal(await p().evaluate(() => window.__diHarness.isCollapsed()), true);
}

/** A labelled synthetic entry for `missionRef` (the fixture's own entry, re-pointed), added to the state. */
async function addEntryFor(missionRef: string, key: string) {
  await p().evaluate(
    ([ref, k]) => {
      window.__diHarness.update((state) => {
        const base = state.reception!.entries[0];
        state.reception!.entries.push({
          ...base,
          key: `${base.key}#${k}`,
          signature: `${base.signature}#${k}`,
          missionRef: ref,
          nativeId: `${base.nativeId}-${k}`,
        });
        state.reception!.entriesTotal = state.reception!.entries.length;
        return state;
      });
    },
    [missionRef, key] as const,
  );
}

const openFromHarness = async () => {
  await p().evaluate(() => window.__diHarness.openReception());
  await p().waitForSelector(INBOX);
};

test('a blocker or hold card says the source-supported reason in plain words; a code stays in the details', async () => {
  await openByKeyboard();
  const first = p().locator(ENTRY).first();
  const reasons = await first.locator('[data-testid="di-reception-reason"]').allTextContents();
  const expected = plainReasons(entry);
  assert.ok(expected.length > 0, 'the fixture entry carries holds or blocker text');
  assert.deepEqual(reasons, expected);
  for (const r of reasons) assert.doesNotMatch(r, /^[a-z]+_[a-z_]+$/, 'a plain sentence, not a bare code');
  // the raw hold codes are in the closed disclosure
  const details = first.locator('details').first();
  assert.equal(await details.evaluate((d) => (d as HTMLDetailsElement).open), false);
  await details.locator('> summary').click();
  const text = (await details.textContent())!;
  for (const c of entry.causes)
    for (const d of c.details)
      if (d.label === 'hold' && d.value !== null) assert.ok(text.includes(d.value), `code ${d.value} is behind the disclosure`);
});

test('a reauthorization card leads with its plain meaning; NEEDS_REAUTHORIZATION is only in the details', async () => {
  await p().evaluate(() => {
    window.__diHarness.update((state) => {
      const base = state.reception!.entries[0];
      state.reception!.entries.push({
        ...base,
        key: `${base.key}#reauth`,
        signature: `${base.signature}#reauth`,
        nativeId: `${base.nativeId}-reauth`,
        category: 'authorization',
        causes: [
          {
            cause: 'workflow_needs_reauthorization',
            label: 'Workflow authority: this workflow needs reauthorization (phase NEEDS_REAUTHORIZATION)',
            details: [
              { label: 'workflow phase', value: 'NEEDS_REAUTHORIZATION' },
              { label: 'lifecycle model', value: 'workflow_authority' },
            ],
            staleReasons: [],
          },
        ],
      });
      state.reception!.entriesTotal = state.reception!.entries.length;
      return state;
    });
  });
  await openByKeyboard();
  const card = p().locator(`${ENTRY}[data-entry-key$="#reauth"]`);
  assert.equal(await card.count(), 1);
  const firstLayer = await card.evaluate((li) => {
    const clone = li.cloneNode(true) as HTMLElement;
    clone.querySelectorAll('details').forEach((d) => d.remove());
    return clone.textContent ?? '';
  });
  assert.match(firstLayer, /needs a fresh authorization before it can continue/);
  assert.doesNotMatch(firstLayer, /NEEDS_REAUTHORIZATION/, 'no raw phase on the first layer');
  assert.doesNotMatch(await card.getAttribute('aria-label') ?? '', /NEEDS_REAUTHORIZATION/);
  await card.locator('details').first().locator('> summary').click();
  assert.match((await card.locator('details').first().textContent())!, /NEEDS_REAUTHORIZATION/, 'the literal is one disclosure away');
});

test('the empty state says it in ordinary words, and says what could not be checked', async () => {
  await p().evaluate(() => {
    window.__diHarness.update((state) => {
      state.reception!.entries = [];
      state.reception!.entriesTotal = 0;
      return state;
    });
  });
  await openByKeyboard();
  const text = (await p().textContent('[data-testid="di-reception-no-entries"]'))!;
  assert.match(text, /^Nothing is listed as needing you right now\./);
  assert.match(text, /Some places could not be checked, so this is not a guarantee\./, 'the fixture has unchecked sources');
  assert.doesNotMatch(text, /No items were reported/);
  assert.doesNotMatch(text, /empty list|not a “none”|“none”/);
  assert.equal(await p().locator('[data-testid="di-reception-unknown"]').count() > 0, true, 'and they are listed above it');
});

// ── Phase 1c: exact source-qualified identity for every Mission route ───────────────────────────
// A Mission's `missionRef` IS its source id and native id ("<sourceId>:<nativeId>"), so comparing the
// full ref in the DOM compares both; the fixture is checked for that below, not assumed.

type FixtureMission = (typeof fixture.state.missions.missions)[number];
const drawnMission = mission; // the reception entry's Mission: has drawn members and an evidence library
const qualified = (m: FixtureMission) => `${m.sourceId}:${m.nativeId}`;

/** The Missions list as it is NOW: its cards' full refs, the Mission picker's value, and the focused region. */
const listIdentity = () =>
  p().evaluate(() => ({
    cards: [...document.querySelectorAll('[data-testid="di-missions"] [data-mission-ref]')].map((e) =>
      e.getAttribute('data-mission-ref'),
    ),
    picker: (document.querySelector('[data-testid="di-mission-select"]') as HTMLSelectElement | null)?.value ?? null,
    focus: document.activeElement?.getAttribute('data-testid') ?? null,
  }));

/** What is selected NOW, on the canvas and in the dock: nothing stale may be left over. */
const selectionIdentity = () =>
  p().evaluate(() => ({
    selected: window.__diHarness.office.selectedAgentId,
    follow: window.__diHarness.office.cameraFollowId,
    dockMissionRef:
      document.querySelector('[data-testid="di-selected-member-mission"]')?.getAttribute('title') ?? null,
    gone: document.querySelectorAll('[data-testid="di-selection-gone"]').length,
    asideHasSelection: document.querySelector('[data-testid="di-office-panel"]')?.classList.contains('di-op-has-selection') ?? false,
    position: document.querySelector('[data-testid="di-navigation-position"]')?.textContent ?? '',
  }));

/** The full-size details dialog's Mission identity: its heading ref, the source line and the focused element. */
async function detailsIdentity() {
  await p().waitForSelector('[data-testid="di-inspector"]');
  return p().evaluate(() => ({
    kind: document.querySelector('[data-testid="di-inspector-kind"]')?.textContent ?? '',
    shortRefTitle: document.querySelector('[data-testid="di-inspector-short-ref"]')?.getAttribute('title') ?? null,
    provenance: document.querySelector('[data-testid="di-inspector-provenance"]')?.textContent ?? '',
    focusId: document.activeElement?.id ?? null,
  }));
}

/**
 * The list AFTER "Show this Mission" has finished. The product's design is a multi-render sequence: it closes the inbox FIRST, runs the jump in a passive effect after the render in which the inbox
 * unmounts (DiOfficePanel.tsx `afterReceptionClose`), and the jump's focus takes one render more. A single read taken right after the inbox detaches therefore reads MID-SEQUENCE; it passed by timing
 * until larger office states (S3's regenerated fixtures) made it read the pre-jump list every time. So this waits for the product's own signal that the jump happened (the Mission picker holding the
 * target). A wait that times out is SWALLOWED, so the unchanged exact assertions report whatever the page shows: if the product never reaches the destination, the test stays red with the same
 * messages. This corrects WHEN the test reads, never what it claims. (How the larger fixtures widen the window is not established by this; only that the read was mid-sequence.)
 */
async function listAfterJump(ref: string) {
  await p()
    .waitForFunction(
      (r) => (document.querySelector('[data-testid="di-mission-select"]') as HTMLSelectElement | null)?.value === r,
      ref,
      { timeout: 5_000 },
    )
    .catch(() => undefined);
  return listIdentity();
}

function assertExactDestination(
  list: Awaited<ReturnType<typeof listIdentity>>,
  m: FixtureMission,
  label: string,
) {
  assert.equal(m.missionRef, qualified(m), `${label}: the full ref is the source id and the native id`);
  assert.deepEqual(list.cards, [m.missionRef], `${label}: the list shows exactly this Mission (source ${m.sourceId}, native ${m.nativeId})`);
  assert.equal(list.picker, m.missionRef, `${label}: the Mission picker holds exactly this ref`);
  assert.equal(list.focus, 'di-missions', `${label}: focus is on the Missions list`);
}

function assertNothingSelected(sel: Awaited<ReturnType<typeof selectionIdentity>>, label: string) {
  assert.deepEqual(
    { selected: sel.selected, follow: sel.follow, dock: sel.dockMissionRef, gone: sel.gone, aside: sel.asideHasSelection },
    { selected: null, follow: null, dock: null, gone: 0, aside: false },
    `${label}: no agent selection, camera follow, dock Mission or "selection gone" notice is left over`,
  );
  assert.match(sel.position, /agents? on the map$/, `${label}: the dock says where nothing is selected`);
}

/** Step the dock to its first agent (a real user route), so ANOTHER Mission's agent is selected. */
async function selectAnotherMissionsAgent() {
  await p().click('[aria-label="Next agent"]');
  await p().waitForFunction(() => window.__diHarness.office.selectedAgentId !== null);
  const sel = await selectionIdentity();
  assert.notEqual(sel.selected, null, 'precondition: an agent is selected');
  assert.notEqual(sel.follow, null, 'precondition: the camera follows it');
  assert.notEqual(sel.dockMissionRef, null, 'precondition: the dock names its Mission');
  return sel;
}

/** "Show this Mission" on the inbox card of `ref` (a card is added for it when the fixture has none). */
async function showThisMission(ref: string, key: string) {
  if (ref !== entry.missionRef) await addEntryFor(ref, key);
  const card = p().locator(ref === entry.missionRef ? ENTRY : `${ENTRY}[data-entry-key$="#${key}"]`).first();
  await card.waitFor({ state: 'visible' }); // the synthetic entry is rendered by the next React commit
  assert.equal(await card.count(), 1);
  await card.locator('button:has-text("Show this Mission")').click();
  await p().waitForSelector(INBOX, { state: 'detached' });
}

for (const collapsedStart of [false, true]) {
  const where = collapsedStart ? 'collapsed sidebar' : 'expanded sidebar';
  const openInbox = async () => (collapsedStart ? openFromHarness() : openByKeyboard());
  const start = async () => (collapsedStart ? loadCollapsed() : undefined);

  test(`${where}: "Show this Mission" on a DRAWN Mission selects a character of exactly that Mission and shows only it`, async () => {
    assert.notEqual(firstDrawn, null);
    const memberIds = drawnMission.members.members.map((x) => x.characterId);
    assert.ok(memberIds.includes(firstDrawn), 'the expected character is a member of that Mission');
    for (const other of fixture.state.missions.missions)
      if (other.missionRef !== drawnMission.missionRef)
        assert.ok(!other.members.members.some((x) => x.characterId === firstDrawn), 'and of no other Mission');
    await start();
    await openInbox();
    await showThisMission(drawnMission.missionRef, 'x');
    await p().waitForSelector('[data-testid="di-office-panel"]');
    assertExactDestination(await listAfterJump(drawnMission.missionRef), drawnMission, where);
    const sel = await selectionIdentity();
    assert.equal(sel.selected, firstDrawn, `${where}: the first drawn member is selected`);
    assert.equal(sel.follow, firstDrawn, `${where}: and followed`);
    assert.equal(sel.dockMissionRef, drawnMission.missionRef, `${where}: the dock names exactly that Mission (source + native id)`);
    // and its details say who it is, source-qualified
    await p().click('[data-testid="di-selected-agent"] [data-testid="di-open-inspector-selection"]');
    await p().waitForSelector('[data-testid="di-inspector"]');
    const memberRef = drawnMission.members.members.find((x) => x.characterId === firstDrawn)!.memberRef;
    assert.equal(await p().textContent('[data-testid="di-inspector-qualifier"]'), memberRef, `${where}: the selected member's exact source-qualified ref`);
    assert.equal(await p().getAttribute('[data-testid="di-inspector-member-mission"]', 'title'), drawnMission.missionRef);
  });

  test(`${where}: "Show this Mission" on an UNDRAWN Mission, with nothing selected, shows exactly that Mission and selects nothing`, async () => {
    await start();
    if (!collapsedStart) assertNothingSelected(await selectionIdentity(), 'before');
    await openInbox();
    await showThisMission(undrawn.missionRef, 'undrawn');
    await p().waitForSelector('[data-testid="di-office-panel"]');
    assertExactDestination(await listAfterJump(undrawn.missionRef), undrawn, where);
    assertNothingSelected(await selectionIdentity(), where);
  });

  test(`${where}: another Mission's agent is selected, then "Show this Mission" on an UNDRAWN Mission leaves NOTHING stale`, async () => {
    const before = await selectAnotherMissionsAgent();
    assert.notEqual(before.dockMissionRef, undrawn.missionRef, 'the previous agent belongs to a different Mission');
    if (collapsedStart) {
      await p().click('[data-testid="di-office-panel-minimize"]');
      await p().waitForSelector('[data-testid="di-office-panel-collapsed"]');
      await openFromHarness();
    } else {
      await openByKeyboard();
    }
    await showThisMission(undrawn.missionRef, 'undrawn');
    await p().waitForSelector('[data-testid="di-office-panel"]');
    assertExactDestination(await listAfterJump(undrawn.missionRef), undrawn, where);
    assertNothingSelected(await selectionIdentity(), where);
    // the Selected dock's Mission steps start from nothing, not from the previous agent
    assert.equal(
      await p().locator('[data-testid="di-selected-agent"] [data-testid="di-open-inspector-selection"]').count(),
      0,
      `${where}: no "Show details" for the previous agent is left in the dock`,
    );
  });

  for (const route of ['details', 'evidence'] as const) {
    for (const target of [drawnMission, undrawn]) {
      const kind = target === drawnMission ? 'drawn' : 'undrawn';
      test(`${where}: "Show ${route}" on a ${kind} Mission opens exactly that Mission's source-qualified details`, async () => {
        assert.ok(target.evidence, 'precondition: the Mission has an evidence library');
        await start();
        if (target !== drawnMission) await addEntryFor(target.missionRef, 'route');
        await openInbox();
        const card = p()
          .locator(target === drawnMission ? ENTRY : `${ENTRY}[data-entry-key$="#route"]`)
          .first();
        await card.locator(`button:has-text("Show ${route}")`).click();
        await p().waitForSelector(INBOX, { state: 'detached' });
        const d = await detailsIdentity();
        assert.match(d.kind, /^Mission · details/);
        assert.equal(d.shortRefTitle, target.missionRef, `${where}/${route}: the heading's full ref`);
        assert.equal(target.missionRef, qualified(target));
        assert.ok(d.provenance.includes(target.missionRef), `${where}/${route}: mission_ref ${target.missionRef} in the source rows`);
        assert.ok(d.provenance.includes(`${target.sourceId} (`) && d.provenance.includes(`native ${target.nativeId}`), `${where}/${route}: source ${target.sourceId} and native ${target.nativeId}`);
        if (route === 'evidence')
          assert.equal(d.focusId, 'di-inspector-evidence', `${where}: the evidence section is opened at and focused`);
        await p().keyboard.press('Escape');
        await p().waitForSelector('[data-testid="di-inspector"]', { state: 'detached' });
      });
    }
  }
}

// ── Phase 1c-4: Reception → evidence, with DISTINGUISHABLE Mission evidence ─────────────────────
// The harness (`?evidence=1`) gives the reception entry's Mission (A) and the first undrawn Mission (B)
// different, labelled SYNTHETIC artifact lists, and an evidence reader that records every content
// request and answers it with text naming exactly what was asked. A heading above the WRONG Mission's
// evidence cannot pass these checks.

async function loadWithEvidence(collapsed: boolean) {
  await page?.close();
  page = await browser!.newPage({ viewport: { width: 1600, height: 1000 } });
  await page.goto(`${url}?evidence=1${collapsed ? '&collapsed=1' : ''}`, { waitUntil: 'domcontentloaded' });
  await page.waitForSelector(
    collapsed ? '[data-testid="di-office-panel-collapsed"]' : '[data-testid="di-office-panel"]',
    { timeout: 30_000 },
  );
}
const evidenceSpec = () => p().evaluate(() => window.__diHarness.evidence.missions!);
const evidenceRequests = () => p().evaluate(() => window.__diHarness.evidence.requests());
/** The artifact rows the evidence listing shows NOW, in order. */
const listedArtifacts = () =>
  p().evaluate(() =>
    [...document.querySelectorAll('[data-testid="di-evidence-library"] [data-testid="di-evidence-artifact-row"]')].map(
      (e) => ({ id: e.getAttribute('data-artifact-id'), text: e.textContent ?? '' }),
    ),
  );
const viewerText = () =>
  p().evaluate(() => document.querySelector('[data-testid="di-evidence-library"] [data-testid="di-evidence-text"]')?.textContent ?? null);

for (const collapsedStart of [false, true]) {
  const where = collapsedStart ? 'collapsed sidebar' : 'expanded sidebar';
  test(`${where}: Reception → "Show evidence" reaches exactly the requested Mission's evidence listing, and a read asks with the exact (missionRef, artifactId, cursor) tuple`, async () => {
    await loadWithEvidence(collapsedStart);
    const { A, B } = await evidenceSpec();
    // preconditions: the two Missions' evidence is genuinely distinguishable, and A/B are the Missions asked for
    assert.equal(A.missionRef, entry.missionRef);
    assert.equal(B.missionRef, undrawn.missionRef);
    assert.notEqual(A.cursor, B.cursor);
    const idsA = A.artifacts.map((x) => x.artifactId);
    const idsB = B.artifacts.map((x) => x.artifactId);
    assert.equal(idsA.filter((x) => idsB.includes(x)).length, 0, 'no artifact id is shared');
    await addEntryFor(B.missionRef, 'ev');
    const open = () => (collapsedStart ? openFromHarness() : openByKeyboard());

    // ── Mission A, through the Reception card of the entry
    await open();
    await p().locator(ENTRY).first().locator('button:has-text("Show evidence")').click();
    await p().waitForSelector(INBOX, { state: 'detached' });
    await p().waitForSelector('[data-testid="di-evidence-library"]');
    assert.equal(await p().evaluate(() => document.activeElement?.id), 'di-inspector-evidence', `${where}: opened at the evidence section`);
    const listedA = await listedArtifacts();
    assert.deepEqual(listedA.map((x) => x.id), idsA, `${where}: the listing is exactly Mission A's artifacts, in order`);
    A.artifacts.forEach((x, i) => assert.ok(listedA[i].text.includes(x.key), `the row shows its labelled key ${x.key}`));
    for (const other of idsB) assert.ok(!listedA.some((x) => x.id === other), `${where}: none of Mission B's artifacts is listed under A`);
    assert.deepEqual(await evidenceRequests(), [], 'nothing was read before a person asks');
    // read the first, then the second artifact: the exact request tuple and the answer shown are A's
    for (const [i, art] of A.artifacts.entries()) {
      await p().locator(`[data-testid="di-evidence-library"] [data-testid="di-evidence-artifact-row"][data-artifact-id="${art.artifactId}"]`).click();
      await p().waitForSelector('[data-testid="di-evidence-library"] [data-testid="di-evidence-text"]');
      const asked = await evidenceRequests();
      assert.equal(asked.length, i + 1);
      assert.deepEqual(asked[i], { missionRef: A.missionRef, artifactId: art.artifactId, cursor: A.cursor }, `${where}: the exact content-request tuple`);
      assert.equal(
        await viewerText(),
        `SYNTHETIC evidence text | mission=${A.missionRef} | artifact=${art.artifactId} | cursor=${A.cursor}`,
        `${where}: the content shown belongs to exactly that Mission, artifact and cursor`,
      );
    }
    await p().keyboard.press('Escape');
    await p().waitForSelector('[data-testid="di-inspector"]', { state: 'detached' });

    // ── Mission B (no drawn agent), through its own Reception card: a DIFFERENT listing and tuple
    await open();
    await p().locator(`${ENTRY}[data-entry-key$="#ev"]`).first().locator('button:has-text("Show evidence")').click();
    await p().waitForSelector(INBOX, { state: 'detached' });
    await p().waitForSelector('[data-testid="di-evidence-library"]');
    const listedB = await listedArtifacts();
    assert.deepEqual(listedB.map((x) => x.id), idsB, `${where}: the listing is exactly Mission B's artifacts`);
    for (const other of idsA) assert.ok(!listedB.some((x) => x.id === other), `${where}: none of Mission A's artifacts is listed under B`);
    B.artifacts.forEach((x, i) => assert.ok(listedB[i].text.includes(x.key)));
    await p().locator(`[data-testid="di-evidence-library"] [data-testid="di-evidence-artifact-row"][data-artifact-id="${B.artifacts[0].artifactId}"]`).click();
    await p().waitForSelector('[data-testid="di-evidence-library"] [data-testid="di-evidence-text"]');
    const all = await evidenceRequests();
    assert.equal(all.length, A.artifacts.length + 1);
    assert.deepEqual(all[all.length - 1], { missionRef: B.missionRef, artifactId: B.artifacts[0].artifactId, cursor: B.cursor }, `${where}: the exact tuple for Mission B`);
    assert.equal(
      await viewerText(),
      `SYNTHETIC evidence text | mission=${B.missionRef} | artifact=${B.artifacts[0].artifactId} | cursor=${B.cursor}`,
    );
    // across the whole route no request ever mixed one Mission's ref with the other's cursor or artifact
    for (const r of all) {
      const owner = r.missionRef === A.missionRef ? A : B;
      assert.equal(r.cursor, owner.cursor);
      assert.ok(owner.artifacts.some((x) => x.artifactId === r.artifactId));
    }
  });
}
