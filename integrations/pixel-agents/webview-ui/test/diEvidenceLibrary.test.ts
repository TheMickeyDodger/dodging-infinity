/**
 * Item 2 (original 10): the evidence library driven by REAL key presses in a real browser.
 *
 * The page (`test/harness/di-evidence.html`) mounts the real `DiOfficePanel` over the evidence
 * library ONE real DiOffice projected from the backend's exported answers, and answers each read
 * with the exact diEvidenceContent that office relayed. Proves: keyboard reach and visible focus;
 * a read names a reference, never a path; admitted text is shown verbatim and INERT (no element is
 * parsed from it, no script or handler runs, nothing is fetched); refusals show their code, never
 * content; a stale or moved listing hides an earlier answer; the four types stay distinct.
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

import type { DiEvidenceContent } from '../../core/src/messages.ts';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const fixture = JSON.parse(
  fs.readFileSync(
    path.join(root, 'test/fixtures/di-office-state-evidence-v2.FIXTURE.json'),
    'utf-8',
  ),
) as { replies: Record<string, DiEvidenceContent> };
const R = fixture.replies;

let server: ViteDevServer | null = null;
let browser: Browser | null = null;
let page: Page | null = null;
let url = '';
let unavailable: string | null = null;
let requested: string[] = [];
let dialogs = 0;

beforeAll(async () => {
  server = await createServer({
    configFile: path.resolve(root, 'vite.config.ts'),
    server: { port: 0, strictPort: false },
    logLevel: 'silent',
  });
  await server.listen();
  const addr = server.httpServer?.address();
  const port = typeof addr === 'object' && addr !== null ? addr.port : 0;
  url = `http://localhost:${port}/test/harness/di-evidence.html`;
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
  requested = [];
  dialogs = 0;
  page.on('request', (r) => requested.push(r.url()));
  page.on('dialog', (d) => {
    dialogs += 1;
    void d.dismiss();
  });
  await page.goto(url, { waitUntil: 'domcontentloaded' });
  // feature13: the library lives in the full-size inspector. Reach it the way a person does: the
  // Mission card's labelled "Evidence library" doorway, by keyboard (focus, Enter).
  await page.waitForSelector('[data-testid="di-open-evidence"]', { timeout: 30_000 });
  await page.focus('[data-testid="di-open-evidence"]');
  await page.keyboard.press('Enter');
  await page.waitForSelector('[role="dialog"] [data-testid="di-evidence-library"]', {
    timeout: 30_000,
  });
  // Trip-wires an executed artifact would fire.
  await page.evaluate(() => {
    const w = window as unknown as Record<string, unknown>;
    w.__tripped = [];
    w.steal = () => (w.__tripped as string[]).push('steal');
    w.run = () => (w.__tripped as string[]).push('run');
  });
});

const p = () => page!;
const active = (selector: string) =>
  p().evaluate((s) => document.activeElement?.matches(s) ?? false, selector);

async function tabTo(selector: string, max = 1000): Promise<number | null> {
  for (let i = 1; i <= max; i++) {
    await p().keyboard.press('Tab');
    if (await active(selector)) return i;
  }
  return null;
}

const rowFor = (name: string) =>
  `[data-testid="di-evidence-artifact-row"][data-artifact-id="${R[name].artifactId}"]`;

/** Focus an artifact row and READ it with a real key press. */
async function readRow(name: string, key: 'Enter' | 'Space' = 'Enter') {
  await p().focus(rowFor(name));
  await p().keyboard.press(key);
}

const viewerText = () =>
  p().evaluate(
    () => document.querySelector('[data-testid="di-evidence-viewer"]')?.textContent ?? '',
  );

async function viewerShows(needle: string) {
  await p().waitForFunction(
    (n) =>
      (document.querySelector('[data-testid="di-evidence-viewer"]')?.textContent ?? '').includes(n),
    needle,
    { timeout: 5_000 },
  );
}

const requests = () => p().evaluate(() => window.__diEvidence.requests);

test('Tab reaches an artifact row with VISIBLE focus; arrows, Home and End move between rows', async () => {
  const presses = await tabTo('[data-testid="di-evidence-artifact-row"]');
  assert.notEqual(presses, null, 'Tab never reached an artifact row');
  console.log(`[item 2 Tab presses] first artifact row: ${presses}`);
  const outline = await p().evaluate(() => {
    const s = getComputedStyle(document.activeElement as Element);
    return { style: s.outlineStyle, width: s.outlineWidth };
  });
  assert.deepEqual(outline, { style: 'solid', width: '2px' });
  const ids = await p().$$eval('[data-testid="di-evidence-artifact-row"]', (rows) =>
    rows.map((r) => r.getAttribute('data-artifact-id')),
  );
  const focusedId = () =>
    p().evaluate(() => document.activeElement?.getAttribute('data-artifact-id') ?? null);
  assert.equal(await focusedId(), ids[0]);
  await p().keyboard.press('ArrowDown');
  assert.equal(await focusedId(), ids[1]);
  await p().keyboard.press('End');
  assert.equal(await focusedId(), ids[ids.length - 1]);
  await p().keyboard.press('Home');
  assert.equal(await focusedId(), ids[0]);
});

test('Enter reads a contract-pinned result: its exact text, locator, digest and basis', async () => {
  await readRow('admitted_text');
  const want = R.admitted_text.content!.text;
  await p().waitForSelector('[data-testid="di-evidence-text"]');
  const shown = await p().$eval('[data-testid="di-evidence-text"]', (e) => e.textContent);
  assert.equal(shown, want);
  const meta = await p().$eval('[data-testid="di-evidence-meta"]', (e) => e.textContent ?? '');
  assert.match(meta, /reports\/result\.txt/);
  assert.match(meta, /a deliverable the approved contract pins/);
  // What was ASKED: exactly the admitted reference, nothing path-like.
  const asked = await requests();
  assert.deepEqual(asked, [
    {
      missionRef: R.admitted_text.missionRef,
      artifactId: R.admitted_text.artifactId,
      cursor: R.admitted_text.cursor,
    },
  ]);
  // The text is a keyboard-reachable scroll region.
  assert.notEqual(await tabTo('[data-testid="di-evidence-text"]', 20), null);
});

test('HTML is shown as its source and is INERT: nothing parsed, run or fetched', async () => {
  await readRow('admitted_html', 'Space');
  await p().waitForSelector('[data-testid="di-evidence-text"]');
  const pre = await p().$eval('[data-testid="di-evidence-text"]', (e) => ({
    text: e.textContent ?? '',
    elements: e.querySelectorAll('*').length,
  }));
  assert.equal(pre.text, R.admitted_html.content!.text);
  assert.match(pre.text, /<script>alert\('x'\)<\/script>/);
  assert.equal(pre.elements, 0, 'no element is parsed out of artifact text');
  await p().waitForTimeout(300);
  assert.equal(dialogs, 0, 'no script ran');
  assert.deepEqual(
    await p().evaluate(() => (window as unknown as Record<string, unknown>).__tripped),
    [],
  );
  assert.equal(
    requested.filter((u) => u.includes('example.invalid')).length,
    0,
    'nothing is fetched from artifact content',
  );
  assert.equal(
    await p().$$eval('img[src*="example.invalid"], a[href^="javascript:"]', (n) => n.length),
    0,
  );
  const warning = await p().$eval(
    '[data-testid="di-evidence-untrusted"]',
    (e) => e.textContent ?? '',
  );
  assert.match(warning, /never rendered, run or fetched/);
});

test('refusals show their code and NEVER content', async () => {
  for (const [name, code] of [
    ['refused_class_not_declared', 'artifact_class_not_declared'],
    ['refused_private_location', 'locator_in_private_location'],
    ['refused_digest_mismatch', 'content_changed_since_recorded'],
    ['refused_not_regular', 'file_not_regular'],
  ] as const) {
    await readRow(name);
    await viewerShows(code);
    assert.equal(await p().$('[data-testid="di-evidence-text"]'), null, name);
    assert.match(await viewerText(), /refused by the backend/);
  }
});

test('a row whose record has no content never asks', async () => {
  await readRow('refused_locator_kind');
  await viewerShows('its locator names no workspace file');
  assert.deepEqual(await requests(), []);
});

test('a stale listing closes reads and hides an earlier answer', async () => {
  await readRow('admitted_text');
  await p().waitForSelector('[data-testid="di-evidence-text"]');
  await p().evaluate(() => window.__diEvidence.showStale());
  await p().waitForSelector('[data-testid="di-evidence-stale"]');
  assert.equal(await p().$('[data-testid="di-evidence-text"]'), null);
  assert.match(await viewerText(), /closed/);
});

test('a moved listing hides an earlier answer — never re-resolved against the new one', async () => {
  await readRow('admitted_text');
  await p().waitForSelector('[data-testid="di-evidence-text"]');
  await p().evaluate(() => window.__diEvidence.showMoved());
  await viewerShows('the Mission moved after this was read');
  assert.equal(await p().$('[data-testid="di-evidence-text"]'), null);
  assert.equal((await requests()).length, 1, 'nothing is asked again by itself');
});

test('the four types stay distinct; a recorded link reads the artifact it names', async () => {
  const section = (id: string) => p().$eval(`[data-testid="${id}"]`, (e) => e.textContent ?? '');
  assert.match(await section('di-evidence-receipts'), /Delivery receipts: none recorded/);
  const review = await section('di-evidence-reviews');
  assert.match(review, /verdict APPROVE/);
  assert.match(review, /NOT digest-anchored/);
  const verification = await section('di-evidence-verification');
  assert.match(verification, /ACCEPTED/);
  // The link to the ADMITTED test log, by its recorded key (Mission Core keeps a record's links
  // sorted by their random ids, so their order is not the test's to assume).
  const links = await p().$$('[data-testid="di-evidence-linked-artifact"]');
  const keys = await Promise.all(links.map((l) => l.evaluate((e) => e.textContent ?? '')));
  const index = keys.indexOf('test_log');
  assert.ok(index >= 0, `the test log is linked: ${keys.join(', ')}`);
  await links[index].focus();
  await p().keyboard.press('Enter');
  await p().waitForSelector('[data-testid="di-evidence-text"]');
  const meta = await p().$eval('[data-testid="di-evidence-meta"]', (e) => e.textContent ?? '');
  assert.match(meta, /named by an accepted verification record/);
});

test('reviewer-declared findings render as INERT text with their provenance ceiling', async () => {
  const state = await p().$eval('[data-testid="di-evidence-findings"]', (e) =>
    e.getAttribute('data-findings-state'),
  );
  assert.equal(state, 'present');
  const items = await p().$$eval('[data-testid="di-evidence-finding"]', (rows) =>
    rows.map((r) => ({ text: r.textContent ?? '', elements: r.querySelectorAll('*').length })),
  );
  assert.equal(items.length, 2);
  assert.match(items[0].text, /^BLOCKER · target_runtime\/artifact_content\.py:381 · /);
  assert.match(items[1].text, /<script>alert\(1\)<\/script>/);
  assert.match(items[1].text, /<b>bold<\/b>/);
  for (const item of items) assert.equal(item.elements, 0, 'no element parsed from a finding');
  await p().waitForTimeout(300);
  assert.equal(dialogs, 0, 'no script ran');
  const ceiling = await p().$eval(
    '[data-testid="di-evidence-findings-provenance"]',
    (e) => e.textContent ?? '',
  );
  assert.match(ceiling, /not a cryptographic proof of reviewer identity/);
  assert.match(ceiling, /not a verdict and not approval authority/);
  const page = await p().evaluate(() => document.body.textContent ?? '');
  assert.equal(page.includes('TRANSCRIPT-CANARY'), false);
  assert.equal(page.includes('private tail'), false);
});

test('every other findings state is said plainly and lists nothing', async () => {
  const blank = {
    count: null,
    items: [],
    candidateSha256: null,
    routedAt: null,
    provenance: null,
  };
  for (const [findings, words] of [
    [{ state: 'not_reported', problem: null, ...blank }, /not reported by this backend/],
    [
      { state: 'not_recorded', problem: 'findings_legacy_record_predates_field', ...blank },
      /not recorded: this round was recorded before declared findings existed/,
    ],
    [
      { state: 'refused', problem: 'findings_declaration_count_mismatch', ...blank },
      /refused: the declared count did not add up \(a finding is missing\)/,
    ],
    [{ state: 'unexpected', problem: null, ...blank }, /did not check out; nothing is shown/],
    [
      {
        state: 'empty',
        problem: null,
        count: 0,
        items: [],
        candidateSha256: 'a'.repeat(64),
        routedAt: 1,
        provenance: 'ceiling',
      },
      /the reviewer declared none for this round/,
    ],
  ] as const) {
    await p().evaluate((f) => window.__diEvidence.showFindings(f), findings);
    await p().waitForFunction(
      (st) =>
        document
          .querySelector('[data-testid="di-evidence-findings"]')
          ?.getAttribute('data-findings-state') === st,
      findings.state,
    );
    const text = await p().$eval(
      '[data-testid="di-evidence-findings"]',
      (e) => e.textContent ?? '',
    );
    assert.match(text, words);
    assert.equal(await p().$$eval('[data-testid="di-evidence-finding"]', (r) => r.length), 0);
  }
});
