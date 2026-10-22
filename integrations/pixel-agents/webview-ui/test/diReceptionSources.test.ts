/**
 * P2-A in a real browser: the reception inbox with delivery proposals, request-panel entries and the
 * Mission entries they may duplicate. The page is the panel harness with its opt-in `?p2a=1` seam: the
 * labelled office state PLUS labelled SYNTHETIC additions (a Mission Core pending decision, Grok delivery
 * proposals in each state, a request-panel overview). Nothing here is a live record.
 *
 * Browser: the installed Chrome (playwright-core, channel `chrome`), else Playwright's own Chromium; with
 * neither, every test is skipped with an explicit message.
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
const originalEntries = fixture.state.reception!.entries.length;

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
  url = `http://localhost:${port}/test/harness/di-panel.html?p2a=1`;
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
const CARD = `${INBOX} [data-testid="di-reception-entry"]`;

async function openInbox() {
  await p().click(OPEN);
  await p().waitForSelector(INBOX);
}

const ids = () => p().evaluate(() => window.__diHarness.p2a!);

/**
 * Every card: its kind, key, the text of its FIRST LAYER (the title and the labelled facts), and the
 * whole text including the disclosures behind it.
 */
const cards = () =>
  p().$$eval(CARD, (els) =>
    els.map((e) => ({
      kind: e.getAttribute('data-entry-kind') ?? '',
      key: e.getAttribute('data-entry-key') ?? '',
      first: [e.querySelector('.di-op-objective'), e.querySelector('[data-testid="di-reception-facts"]')]
        .map((n) => n?.textContent ?? '')
        .join(' '),
      text: e.textContent ?? '',
    })),
  );

test('the seam is on, and the sidebar and the dialog say the same number', async () => {
  const seam = await ids();
  assert.ok(seam, 'the p2a seam is mounted');
  // original Mission entries + the one decision (its panel request folded in) + two delivery proposals
  // waiting on the person + a conversation + an approval check + a result to verify
  const expected = originalEntries + 1 + 2 + 3;
  const sidebar = (await p().textContent('[data-testid="di-reception-summary"]'))!;
  assert.match(sidebar, new RegExp(`${expected} need attention`));
  await openInbox();
  const headline = (await p().textContent('[data-testid="di-reception-inbox-summary"]'))!;
  assert.equal(headline, `${expected} need attention`);
  assert.equal((await cards()).length, expected);
});

test('one decision observed by Mission Core and by the request panel is ONE card with both facts', async () => {
  const seam = (await ids())!;
  await openInbox();
  const all = await cards();
  // the request panel's request for that decision has no card of its own...
  assert.equal(
    all.filter((c) => c.kind === 'panel' && c.key.includes(seam.decisionRequest)).length,
    0,
  );
  // ...it is folded into the Mission's authorization card
  const merged = all.filter((c) => c.kind === 'mission' && c.key.includes(seam.coreNativeId) && c.key.includes('authorization'));
  assert.equal(merged.length, 1);
  // FIRST LAYER: plain words — no request ref, stage literal or digest above the disclosure — and the next step is the button's own route
  assert.match(merged[0].first, /The request panel shows the same decision\./);
  assert.doesNotMatch(merged[0].first, /\blr-|\bcv-|of_record|[0-9a-f]{16,}|pending_decisions/i, 'no ids, stage literals or digests above the disclosure');
  assert.match(merged[0].first, /Decide in the request panel \(it records your decision; this inbox approves nothing\)\./);
  assert.doesNotMatch(merged[0].first, /Operator decision route/, 'the Operator route is not named where an in-office route exists');
  const card = p().locator(`${CARD}[data-entry-key*="authorization"][data-entry-key*="${seam.coreNativeId}"]`);
  assert.equal(await card.locator('[data-testid="di-reception-open-panel"]').count(), 1);
  // both source facts, behind the card
  await card.locator(':scope > details > summary').click();
  const detail = (await card.locator('[data-testid="di-reception-twin-detail"]').textContent())!;
  assert.match(detail, /Mission Core pending_decisions \(revision 2\)/);
  assert.match(detail, new RegExp(`request ${seam.decisionRequest} \\(revision 2, stage of_record, digest b{64}\\)`));
  assert.match(detail, new RegExp(`The request panel lists this same decision \\(request ${seam.decisionRequest}, stage of_record\\)`), 'the exact ref and stage are INSIDE the disclosure');
});

test('delivery cards say the supported fact, never an approval is usable, and link only when evidenced', async () => {
  const seam = (await ids())!;
  await openInbox();
  const deliveries = (await cards()).filter((c) => c.kind === 'delivery');
  assert.equal(deliveries.length, 2, 'only the two waiting on the person are in the list');
  for (const d of deliveries) {
    assert.match(d.first, /A delivery proposal was presented \(a pull request for https:\/\/github\.com\/synthetic\/repo-\d\) and no matching authorization is recorded\./);
    assert.match(d.first, /presented \d{4}-\d\d-\d\d \d\d:\d\d UTC; no recorded authorization; approval window until \d{4}-\d\d-\d\d \d\d:\d\d UTC/);
    assert.match(d.first, /decide in the Grok Bot conversation where it was presented/i);
    // the first layer states the supported fact only; the limits are said once, behind the card
    assert.doesNotMatch(d.first, /not been decided|declin|reject|can still be approved|attest|untrusted|operator/i);
    assert.match(d.text, /cannot see a decision or decline made elsewhere/);
  }
  // the unlinked one names its reason; the linked one offers the Mission routes
  const unlinked = deliveries.find((d) => d.key.includes(seam.deliveryDigests[0]))!;
  assert.match(unlinked.first, /Not tied to a Mission: the workflow it names has no registered Mission/);
  assert.doesNotMatch(unlinked.first, /wf-synthetic/, 'the workflow id is not above the disclosure');
  assert.match(unlinked.text, /workflow wf-synthetic/, 'the exact workflow is inside it');
  const linkedCard = p().locator(`${CARD}[data-entry-key*="${seam.deliveryDigests[1]}"]`);
  const linkedButtons = await linkedCard.locator('button').allTextContents();
  assert.equal(linkedButtons[0], 'Show this Mission');
  assert.equal(linkedButtons[linkedButtons.length - 1], 'Hide in this view');
  assert.equal(await linkedCard.locator('button', { hasText: 'Show this Mission' }).count(), 1);
  const unlinkedCard = p().locator(`${CARD}[data-entry-key*="${seam.deliveryDigests[0]}"]`);
  assert.equal(await unlinkedCard.locator('button', { hasText: 'Show this Mission' }).count(), 0);
  // nothing on either card can approve, relay or deliver
  for (const card of [linkedCard, unlinkedCard])
    assert.doesNotMatch((await card.locator('button').allTextContents()).join(' '), /approve|relay|deliver|authori[sz]e/i);
});

test('delivery proposals that do not wait on the person are information, in their own disclosure', async () => {
  await openInbox();
  const states = await p().$$eval('[data-testid="di-reception-information-entry"]', (els) =>
    els.map((e) => e.getAttribute('data-state')),
  );
  assert.deepEqual(states.sort(), ['authorized', 'expired_without_authorization', 'unknown']);
  const text = (await p().textContent('[data-testid="di-reception-information"]'))!;
  assert.match(text, /Nothing here says an approval is usable/);
  assert.match(text, /this view could not check whether an authorization is recorded/);
});

test('every source has its own coverage line, and none says “none”', async () => {
  await openInbox();
  const lines = await p().$$eval(`${INBOX} [data-testid="di-reception-sources"] li[data-source]`, (els) =>
    els.map((e) => ({
      id: e.getAttribute('data-source'),
      outcome: e.getAttribute('data-outcome'),
      first: e.querySelector(':scope > span')?.textContent ?? '',
      covers: e.querySelector(':scope > div')?.textContent ?? '',
    })),
  );
  assert.deepEqual(lines.map((l) => l.id), ['grok_delivery_presentations', 'request_panel']);
  assert.equal(lines[0].outcome, 'present');
  assert.match(lines[0].first, /5 presented · 2 waiting for you · 1 not determined/);
  assert.match(lines[0].covers, /Grok Bot route only/);
  assert.match(lines[0].covers, /present-dots/);
  assert.equal(lines[1].outcome, 'present');
  // the source reports four (the decision request included); the list shows it once, folded into the
  // Mission's card, so the headline counts it once
  assert.match(lines[1].first, /read · 4 waiting for you \(3 requests, 1 conversation\) · 1 older conversation not reported/);
  assert.match(lines[1].covers, /10 most recent conversations/);
  assert.doesNotMatch(lines.map((l) => l.first).join(' '), /\bnone\b/i);
});

test('the coverage above the cards is one or two plain lines; ALL the detail is behind ONE closed disclosure', async () => {
  await openInbox();
  // the headline is visible (in no disclosure) and keeps the material uncertainty in plain words
  const headline = p().locator(`${INBOX} [data-testid="di-reception-uncertainty"]`);
  assert.equal(await headline.count(), 1);
  assert.equal(await headline.evaluate((e) => e.closest('details') === null), true, 'the uncertainty headline is not inside a disclosure');
  assert.match((await headline.textContent())!, /couldn’t be (fully )?checked|isn’t reported by the catalog|couldn’t be checked for a recorded authorization/);
  assert.match((await headline.textContent())!, /Clarification isn’t reported by the catalog/);
  // the per-source summary line: each source in its own words, and no source that was not read says “none”
  const summary = (await p().textContent(`${INBOX} [data-testid="di-reception-source-summary"]`))!;
  assert.equal(summary, 'Delivery proposals: 2 waiting · Request panel: 4 waiting');
  // ONE coverage disclosure, closed, holding the sources, what could not be checked and the categories
  const coverage = p().locator(`${INBOX} [data-testid="di-reception-coverage"]`);
  assert.equal(await coverage.count(), 1);
  assert.equal(await coverage.evaluate((d) => (d as HTMLDetailsElement).open), false);
  for (const inside of ['di-reception-sources', 'di-reception-unknown', 'di-reception-categories'])
    assert.ok((await coverage.locator(`[data-testid="${inside}"]`).count()) >= 1, `${inside} is inside the one disclosure`);
  // the old stacked blocks are gone from the first layer: no second coverage disclosure and no per-source rows outside it
  assert.equal(await p().locator(`${INBOX} [data-testid="di-reception-sources"]`).evaluate((e) => e.closest('[data-testid="di-reception-coverage"]') !== null), true);
  assert.equal(await p().locator(`${INBOX} [data-testid="di-reception-categories"]`).evaluate((e) => e.closest('[data-testid="di-reception-coverage"]') !== null), true);
});

test('the request panel’s own card, the inbox cards and the coverage line say the same number for the same source', async () => {
  // the panel's card in the sidebar: requests that need you, and conversations waiting for an answer, kept apart
  const card = (await p().textContent('[data-testid="di-mr-entry-summary"]'))!;
  const requests = Number(/(\d+) requests? needs? you/.exec(card)?.[1] ?? 0);
  const conversations = Number(/(\d+) conversations? waiting for your answer/.exec(card)?.[1] ?? 0);
  assert.equal(requests + conversations > 0, true, card);
  await openInbox();
  // the coverage line, from the same count
  const line = await p().locator(`${INBOX} [data-testid="di-reception-sources"] li[data-source="request_panel"] > span`).textContent();
  assert.match(line!, new RegExp(`read · ${requests + conversations} waiting for you \\(${requests} requests?, ${conversations} conversations?\\)`));
  assert.match((await p().textContent(`${INBOX} [data-testid="di-reception-source-summary"]`))!, new RegExp(`Request panel: ${requests + conversations} waiting`));
  // the inbox lists exactly that many things from this source: its own cards plus the request folded into a Mission's card (one decision, two sources)
  const panelCards = await p().locator(`${CARD}[data-entry-kind="panel"]`).count();
  const folded = await p().locator(`${CARD}[data-entry-kind="mission"] [data-testid="di-reception-twin"]`).count();
  assert.equal(panelCards + folded, requests + conversations);
  assert.equal(await p().locator(`${CARD}[data-entry-kind="panel"][data-entry-key*="clarification"]`).count(), conversations);
});

test('first layer versus disclosure for a conversation: no conversation ref above it; the ref is inside', async () => {
  const seam = (await ids())!;
  await openInbox();
  const card = p().locator(`${CARD}[data-entry-kind="panel"][data-entry-key*="${seam.conversationId}"]`);
  const first = (await card.locator('[data-testid="di-reception-facts"]').textContent())!;
  assert.doesNotMatch(first, /\bcv-|\bct-|[0-9a-f]{24,}/, 'no conversation or turn ref above the disclosure');
  await card.locator(':scope > details > summary').click();
  assert.match((await card.locator('[data-testid="di-reception-detail"]').textContent())!, new RegExp(`conversation ${seam.conversationId}`), 'the ref is inside');
});

test('a conversation awaiting an answer shows the assistant’s questions as quoted text and opens the panel’s Ask tab for THAT conversation', async () => {
  const seam = (await ids())!;
  await openInbox();
  const card = p().locator(`${CARD}[data-entry-kind="panel"][data-entry-key*="${seam.conversationId}"]`);
  const text = (await card.textContent())!;
  assert.match(text, /The assistant asked 2 questions in this conversation and is waiting for your reply\./);
  assert.match(text, /Asked: “Which branch should this change\?”/);
  assert.match(text, /Asked: “Which tests prove it\?”/);
  assert.doesNotMatch(text, /cannot become a proposal|until its questions are answered/);
  await card.locator('[data-testid="di-reception-open-panel"]').click();
  await p().waitForSelector(INBOX, { state: 'detached' });
  const calls = await p().evaluate(() => window.__diHarness.calls.panel);
  assert.deepEqual(
    calls.map((c) => [c.route, c.id]),
    [['conversation', seam.conversationId]],
  );
  // the route READS that one conversation; it opened nothing else and sent nothing
  assert.equal((await p().evaluate(() => window.__diHarness.calls.mission)).length, 0);
});

test('the merged decision opens the request panel on THAT request', async () => {
  const seam = (await ids())!;
  await openInbox();
  const card = p().locator(`${CARD}[data-entry-kind="mission"][data-entry-key*="authorization"][data-entry-key*="${seam.coreNativeId}"]`);
  await card.locator('[data-testid="di-reception-open-panel"]').click();
  await p().waitForSelector(INBOX, { state: 'detached' });
  const calls = await p().evaluate(() => window.__diHarness.calls.panel);
  assert.deepEqual(calls.map((c) => [c.route, c.id]), [['request', seam.decisionRequest]]);
});

test('an approval check and a result to verify are their own entries, in authorization and results-ready', async () => {
  const seam = (await ids())!;
  await openInbox();
  const all = await cards();
  const check = all.find((c) => c.key.includes(seam.checkRequest))!;
  const verify = all.find((c) => c.key.includes(seam.verifyRequest))!;
  assert.ok(check && verify);
  assert.match(check.key, /"authorization"/);
  assert.match(verify.key, /"ready_for_review"/);
  assert.match(check.first, /An approval was recorded for this request/);
  assert.match(verify.first, /The team’s result is ready for you to verify/);
  for (const c of [check, verify]) assert.doesNotMatch(c.first, /attest|untrusted|operator|decides nothing/i);
});

test('the search and category filter survive opening a Mission’s details and coming back, with focus on the card left', async () => {
  const seam = (await ids())!;
  await openInbox();
  // narrow by words and by category chip
  await p().fill('[data-testid="di-reception-search"]', 'synthetic');
  await p().click(`${INBOX} [data-testid="di-reception-chips"] button[data-category="delivery_decision"]`);
  const status = (await p().textContent('[data-testid="di-reception-filter-status"]'))!;
  assert.match(status, /^Showing 2 of \d+ entries\.$/);
  // act on the linked delivery card: its Mission's details
  const linked = p().locator(`${CARD}[data-entry-key*="${seam.deliveryDigests[1]}"]`);
  await linked.locator('button', { hasText: 'Show details' }).click();
  await p().waitForSelector(INBOX, { state: 'detached' });
  await p().waitForSelector('[data-testid="di-inspector"], [role="dialog"]');
  await p().keyboard.press('Escape');
  await p().waitForSelector('[role="dialog"]', { state: 'detached' });
  // back in the inbox: the same filter, the same chip, focus on the card that was left
  await openInbox();
  assert.equal(await p().inputValue('[data-testid="di-reception-search"]'), 'synthetic');
  assert.equal(
    await p().getAttribute(`${INBOX} [data-testid="di-reception-chips"] button[data-category="delivery_decision"]`, 'aria-pressed'),
    'true',
  );
  assert.match((await p().textContent('[data-testid="di-reception-filter-status"]'))!, /^Showing 2 of \d+ entries\.$/);
  const focusedKey = await p().evaluate(() => document.activeElement?.getAttribute('data-entry-key') ?? null);
  assert.ok(focusedKey?.includes(seam.deliveryDigests[1]), `focus is on the card that was left, not ${focusedKey}`);
});

test('hiding is local to the view and returns on a reload', async () => {
  await openInbox();
  const before = (await cards()).length;
  await p().locator(`${CARD}[data-entry-kind="delivery"]`).first().locator('button', { hasText: 'Hide in this view' }).click();
  assert.equal((await cards()).length, before - 1);
  assert.match((await p().textContent('[data-testid="di-reception-hidden"]'))!, /1 hidden in this view only/);
  await p().reload({ waitUntil: 'domcontentloaded' });
  await p().waitForSelector('[data-testid="di-office-panel"]');
  await openInbox();
  assert.equal((await cards()).length, before);
});

test('hiding a MERGED decision card (Mission + request panel) hides it, counts it, resurfaces on new content, and changes no source', async () => {
  const seam = (await ids())!;
  const MERGED = `${CARD}[data-entry-kind="mission"][data-entry-key*="authorization"][data-entry-key*="${seam.coreNativeId}"]`;
  const hideIt = () => p().locator(MERGED).locator('button', { hasText: 'Hide in this view' }).click();
  const headline = () => p().textContent('[data-testid="di-reception-inbox-summary"]');
  const sidebar = () => p().textContent('[data-testid="di-reception-summary"]');
  const expected = originalEntries + 1 + 2 + 3;
  await openInbox();
  assert.equal(await p().locator(MERGED).count(), 1, 'the merged card is listed once');
  assert.equal(await headline(), `${expected} need attention`);
  const sourcesBefore = await p().evaluate(() => JSON.stringify(window.__diHarness.current()));
  await hideIt();
  // the card is gone, the request panel's request did not come back as a card of its own, and the counts moved by one
  assert.equal(await p().locator(MERGED).count(), 0, 'Hide removes the merged card');
  const afterHide = await cards();
  assert.equal(afterHide.length, expected - 1);
  assert.equal(afterHide.filter((c) => c.kind === 'panel' && c.key.includes(seam.decisionRequest)).length, 0);
  assert.match((await p().textContent('[data-testid="di-reception-hidden"]'))!, /1 hidden in this view only/);
  assert.equal(await headline(), `${expected - 1} need attention`, 'attention is reduced');
  assert.match((await sidebar())!, new RegExp(`${expected - 1} need attention`), 'and so is the sidebar count');
  // hiding is a view preference: no canonical source changed
  assert.equal(await p().evaluate(() => JSON.stringify(window.__diHarness.current())), sourcesBefore);
  // Show hidden reveals it again
  await p().click(`${INBOX} [data-testid="di-reception-hidden"] button`);
  assert.equal(await p().locator(MERGED).count(), 1);
  assert.equal(await headline(), `${expected} need attention`);
  // it RESURFACES when its Mission content changes (a later projection with a new signature)
  await hideIt();
  assert.equal(await p().locator(MERGED).count(), 0);
  await p().evaluate((nativeId) => {
    window.__diHarness.update((s) => {
      for (const e of s.reception!.entries)
        if (e.key.includes('authorization') && e.key.includes(nativeId)) e.signature = 'p2a-synthetic-decision-changed';
      return s;
    });
  }, seam.coreNativeId);
  await p().waitForSelector(MERGED);
  assert.equal(await headline(), `${expected} need attention`, 'new content resurfaces the card');
});

test('after the local expiry the same cached delivery record is worded as last read — and the old claim is gone', async () => {
  const seam = (await ids())!;
  await openInbox();
  const card = p().locator(`${CARD}[data-entry-kind="delivery"][data-entry-key*="${seam.deliveryDigests[0]}"]`);
  const facts = () => card.locator('[data-testid="di-reception-facts"]').textContent();
  const objective = () => card.locator('.di-op-objective').textContent();
  // before: current
  assert.match((await objective())!, /and no matching authorization is recorded\.$/);
  assert.match((await facts())!, /approval window until \d{4}-\d\d-\d\d \d\d:\d\d UTC/);
  assert.match((await facts())!, /its approval window closes \d{4}-/);
  assert.equal(await card.locator('[data-testid="di-reception-currency"]').textContent(), 'current');
  // the window passes: the server marks the SAME record not current (state, digest and expiry unchanged)
  await p().evaluate((digest) => {
    window.__diHarness.update((state) => {
      const e = state.reception!.sourceEntries!.find((x) => x.proposalDigest === digest)!;
      e.current = false;
      e.staleReasons = ['approval_window_ended_since_catalog_read'];
      return state;
    });
  }, seam.deliveryDigests[0]);
  await card.locator('[data-testid="di-reception-currency"]').filter({ hasText: 'last read, now out of date' }).waitFor();
  assert.match((await objective())!, /When last read, no matching authorization was recorded\.$/);
  const after = (await facts())!;
  assert.match(after, /when last read, no recorded authorization; its approval window has now passed \(\d{4}-/);
  assert.match(after, /its current status needs checking/);
  assert.doesNotMatch(after, /closes|has not closed|approval window until/);
  assert.doesNotMatch(after, /expired without|ended without|declin|reject/i);
  // still listed as waiting on the person: the cached canonical state is unchanged
  assert.equal(await card.count(), 1);
});
