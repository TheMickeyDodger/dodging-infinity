/**
 * Pass B, FUNCTIONAL (executor brief pass B §0: "the preview must not be dead"): a person can move through the five
 * surfaces in a real browser, on the labelled specimen (test/harness/di-scan-lock.html), at 1280 x 720 (the v3 size).
 *
 *  - 04 Missions: status filters narrow the table and say their counts; a row opens Mission details; Back returns to
 *    the Missions table (S1, S2, M1).
 *  - 03 Reception: Needs you / All; Clear info clears information items only, so the header and the rail badge do not
 *    move; Earlier today's Show lists handled items read only; a Mission name opens Mission details and Back returns to
 *    Reception; Open Mission lands on Delivery; Esc returns to Home with focus on the rail entry (R1, R2, R11, R12,
 *    AC-4.7-06/-14/-16).
 *  - 06 Code Diff: the file tree selects a file and the diff pane follows; the sibling switch goes to Comments and back;
 *    Back and Esc return to Mission details (C1 to C4). 07 Comments has no composer (X3).
 *  - New Mission: Cmd N opens it; "Continue in the request workspace" (CHANGED IN G1/G2: was "Send request") stays off with the reason that holds, here with no workspace in this harness; a recent ask fills the box; the
 *    sample proposal opens and returns; at 15 running, Open Missions works (G1, G2, AC-4.3-14/-18/-49).
 *  - Keyboard: filters work from the keyboard, with a visible focus ring.
 *
 * Presentation and navigation only: nothing here sends, decides or deletes. Browser: the installed Chrome
 * (playwright-core, channel `chrome`), else Playwright's Chromium; with neither, every test is skipped, saying why.
 */
import path from 'node:path';
import { fileURLToPath } from 'node:url';

import type { Browser, Page } from 'playwright-core';
import { chromium } from 'playwright-core';
import type { ViteDevServer } from 'vite';
import { createServer } from 'vite';
import { afterAll, beforeAll, beforeEach, expect, test } from 'vitest';

const STEPS_MS = 120_000;
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const VIEWPORT = { width: 1280, height: 720 };

let server: ViteDevServer | null = null;
let browser: Browser | null = null;
let origin = '';
let unavailable: string | null = null;

beforeAll(async () => {
  server = await createServer({
    configFile: path.resolve(root, 'vite.config.ts'),
    server: { port: 0, strictPort: false, hmr: false, ws: false },
    logLevel: 'silent',
  });
  await server.listen();
  const addr = server.httpServer?.address();
  origin = `http://localhost:${typeof addr === 'object' && addr !== null ? addr.port : 0}`;
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
  await server?.close();
}, STEPS_MS);

beforeEach((ctx) => {
  if (unavailable) {
    console.warn(`SKIPPED (pass B functional): ${unavailable}`);
    ctx.skip();
  }
});

async function open(query: string): Promise<Page> {
  const page = await browser!.newPage({ viewport: VIEWPORT });
  await page.goto(`${origin}/test/harness/di-scan-lock.html?${query}`, { waitUntil: 'load' });
  await page.waitForSelector('[data-testid="di-rail"]');
  return page;
}

const sel = (testid: string) => `[data-testid="${testid}"]`;
const text = async (page: Page, selector: string) =>
  (await page.locator(selector).first().innerText()).trim();
const count = (page: Page, selector: string) => page.locator(selector).count();
const stageView = (page: Page) =>
  page.locator(sel('di-scan-stage')).getAttribute('data-view');
const missionRows = (page: Page) =>
  page.$$eval('[data-testid="di-missions-row"]', (rows) =>
    rows.map((row) => row.getAttribute('data-mission-ref')),
  );
const activeTestid = (page: Page) =>
  page.evaluate(() => document.activeElement?.getAttribute('data-testid') ?? document.activeElement?.id ?? null);

test(
  'S1/S2/M1: the Missions table filters, opens a Mission, and Back returns to it',
  async () => {
    const page = await open('di-scan-cast=v3');
    await page.click(sel('di-rail-missions'));
    expect(await stageView(page)).toBe('missions');
    expect(await page.getAttribute(sel('di-rail-missions'), 'aria-current')).toBe('page');
    expect(await missionRows(page)).toEqual(['v3-inbox', 'v3-probe', 'v3-install', 'v3-settings', 'v3-dark']);

    await page.click(sel('di-missions-status-running'));
    expect(await missionRows(page)).toEqual(['v3-inbox', 'v3-probe']);
    expect(await text(page, `${sel('di-missions-status-running')} ${sel('di-missions-count')}`)).toBe('2');
    await page.click(sel('di-missions-status-stalled'));
    expect(await missionRows(page)).toEqual(['v3-install']);
    await page.click(sel('di-missions-status-archived'));
    expect(await missionRows(page)).toEqual([]);
    expect(await text(page, sel('di-missions-page'))).toContain('No Missions match this filter.');

    // a row opens its Mission details from anywhere in the row (here, its Owner cell)
    await page.click(sel('di-missions-status-stalled'));
    await page.click('[data-testid="di-missions-row"][data-mission-ref="v3-install"] td:nth-child(2)');
    expect(await stageView(page)).toBe('mission');
    expect(await text(page, sel('di-scan-mission-title'))).toBe('Install guide for new users');
    expect(await page.getAttribute(sel('di-scan-back'), 'aria-label')).toBe('Back to Missions');
    await page.click(sel('di-scan-back'));
    expect(await stageView(page)).toBe('missions');
    await page.close();
  },
  STEPS_MS,
);

test(
  'R1/R12/AC-4.7-16: Needs you and All; Clear info clears information only; header and badge stay one number',
  async () => {
    const page = await open('di-scan-cast=v3');
    const badge = await text(page, sel('di-rail-reception-count'));
    await page.click(sel('di-open-reception'));
    expect(await stageView(page)).toBe('reception');
    expect(await text(page, sel('di-reception-count'))).toBe(`${badge} need attention`);
    expect(await count(page, sel('di-reception-card'))).toBe(5);

    await page.click(sel('di-reception-filter-all'));
    expect(await count(page, sel('di-reception-card'))).toBe(7);
    expect(await count(page, '[data-testid="di-reception-card"][data-kind="info"]')).toBe(2);
    await page.click(sel('di-reception-clear-info'));
    expect(await count(page, '[data-testid="di-reception-card"][data-kind="info"]')).toBe(0);
    expect(await count(page, sel('di-reception-card'))).toBe(5);
    expect(await text(page, sel('di-reception-info-none'))).toBe('Information cleared in this view only.');
    // the action items, the header and the badge are untouched by Clear info
    expect(await text(page, sel('di-reception-count'))).toBe(`${badge} need attention`);
    expect(await text(page, sel('di-rail-reception-count'))).toBe(badge);

    // Earlier today: Show lists the handled items, read only (no control inside them)
    await page.click(sel('di-reception-earlier-show'));
    expect(await page.getAttribute(sel('di-reception-earlier-show'), 'aria-expanded')).toBe('true');
    const handled = page.locator(`${sel('di-reception-earlier-list')} ${sel('di-reception-card')}`);
    expect(await handled.count()).toBe(3);
    expect(await page.locator(`${sel('di-reception-earlier-list')} button, ${sel('di-reception-earlier-list')} input`).count()).toBe(0);
    await page.close();
  },
  STEPS_MS,
);

test(
  'R2/R11: a Mission name opens its details and Back returns to Reception; Open Mission lands on Delivery; Esc goes Home with focus on the rail entry',
  async () => {
    const page = await open('di-scan-cast=v3');
    await page.click(sel('di-open-reception'));
    await page.click('[data-testid="di-reception-open-mission"][data-mission-ref="v3-probe"]');
    expect(await stageView(page)).toBe('mission');
    expect(await text(page, sel('di-scan-mission-title'))).toBe('Stabilize readiness probe');
    expect(await page.getAttribute(sel('di-scan-back'), 'aria-label')).toBe('Back to Reception');
    await page.click(sel('di-scan-back'));
    expect(await stageView(page)).toBe('reception');

    await page.click(sel('di-reception-open-delivery'));
    expect(await stageView(page)).toBe('mission');
    expect(await text(page, sel('di-scan-mission-title'))).toBe('Settings screen copy pass');
    await page.waitForFunction(() => document.activeElement?.id === 'di-scan-delivery-heading');
    const inView = await page.evaluate(() => {
      const r = document.getElementById('di-scan-delivery-heading')!.getBoundingClientRect();
      return r.top >= 0 && r.bottom <= window.innerHeight;
    });
    expect(inView).toBe(true);

    // Esc: Mission details back to where it was opened (Reception), then Reception to Home, focus on the rail entry
    await page.keyboard.press('Escape');
    expect(await stageView(page)).toBe('reception');
    await page.keyboard.press('Escape');
    await page.waitForSelector(sel('di-scan-stage'), { state: 'detached' });
    expect(await activeTestid(page)).toBe('di-open-reception');
    await page.close();
  },
  STEPS_MS,
);

test(
  'C1 to C4 and X3: the file tree selects a file, the diff follows, the siblings switch, and Comments has no composer',
  async () => {
    const page = await open('di-scan-view=mission');
    await page.click(sel('di-scan-code-diff'));
    expect(await stageView(page)).toBe('diff');
    expect(await text(page, sel('di-scan-editor-path'))).toBe('src/world/open/next-panel-overhaul/code-diff.ts');

    await page.click('[data-testid="di-scan-tree-file"][data-file-id="next"]');
    expect(await text(page, sel('di-scan-editor-path'))).toBe('src/world/components/NextPanel.tsx');
    expect(await page.getAttribute('[data-file-id="next"]', 'aria-current')).toBe('true');
    expect(await page.getAttribute('[data-file-id="code"]', 'aria-current')).toBeNull();
    expect(await text(page, sel('di-scan-editor-language'))).toBe('TypeScript JSX');
    expect(await text(page, sel('di-scan-editor'))).toContain('useNextPanel(rows)');

    await page.click('[data-testid="di-scan-tree-file"][data-file-id="index"]');
    expect(await count(page, sel('di-scan-editor-unchanged'))).toBe(1);

    await page.click(sel('di-scan-sibling-comments'));
    expect(await stageView(page)).toBe('comments');
    expect(await count(page, `${sel('di-scan-thread')} li`)).toBe(4);
    expect(await page.locator(`${sel('di-scan-comments-view')} textarea, ${sel('di-scan-comments-view')} input`).count()).toBe(0);
    expect(await page.isDisabled(sel('di-scan-sibling-herdr'))).toBe(true);

    await page.click(sel('di-scan-sibling-diff'));
    expect(await stageView(page)).toBe('diff');
    await page.click(sel('di-scan-back'));
    expect(await stageView(page)).toBe('mission');
    await page.click(sel('di-scan-code-diff'));
    await page.keyboard.press('Escape');
    expect(await stageView(page)).toBe('mission');
    await page.close();
  },
  STEPS_MS,
);

test(
  'G1/G2: Cmd N opens New Mission; Send stays off with the reason that holds; Recent fills the box; the sample proposal opens and returns',
  async () => {
    const page = await open('di-scan-view=list');
    await page.keyboard.press('Meta+n');
    expect(await stageView(page)).toBe('new');
    expect(await page.getAttribute(sel('di-rail-new-orchestrator'), 'aria-current')).toBe('page');
    const send = sel('di-new-continue');
    expect(await page.isDisabled(send)).toBe(true);
    expect(await page.getAttribute(send, 'title')).toBe('Type an ask first.');

    await page.fill(sel('di-new-ask'), '   ');
    expect(await page.getAttribute(send, 'title')).toBe('Type an ask first.');
    await page.fill(sel('di-new-ask'), 'Add a dark mode toggle');
    expect(await page.isDisabled(send)).toBe(true);
    // CHANGED IN G1/G2: this harness mounts no request workspace, so the handoff says it cannot carry the ask there (was "not connected in this layout preview")
    expect(await page.getAttribute(send, 'title')).toBe(
      'Mission requests are not available in this office, so the ask cannot be carried to the request workspace.',
    );
    // Cmd Return does nothing here (no workspace): still on New Mission, nothing sent
    await page.focus(sel('di-new-ask'));
    await page.keyboard.press('Meta+Enter');
    expect(await stageView(page)).toBe('new');

    await page.click('[data-testid="di-new-recent-ask"] >> text=Update install guide');
    expect(await page.inputValue(sel('di-new-ask'))).toBe('Update install guide');

    await page.click(sel('di-new-sample-proposal'));
    expect(await stageView(page)).toBe('proposal');
    expect(await text(page, sel('di-proposal-team'))).toContain('claude-opus-5-5');
    expect(await page.locator(`${sel('di-proposal-team')} button, ${sel('di-proposal-team')} select, ${sel('di-proposal-team')} input`).count()).toBe(0);
    await page.click(sel('di-proposal-back'));
    expect(await stageView(page)).toBe('new');
    await page.keyboard.press('Escape');
    await page.waitForSelector(sel('di-scan-stage'), { state: 'detached' });
    expect(await activeTestid(page)).toBe('di-rail-new-orchestrator');
    await page.close();
  },
  STEPS_MS,
);

test(
  'AC-4.3-14/-49: at 15 running, New Mission refuses with a plain reason, and Open Missions opens the table',
  async () => {
    const page = await open('di-scan-case=limit&di-scan-view=new');
    expect(await text(page, sel('di-new-limit'))).toContain('15 of 15 Orchestrators running');
    expect(await page.getAttribute(sel('di-new-continue'), 'title')).toBe(
      '15 of 15 Orchestrators are running. Finish or stop one to start another.',
    );
    await page.click(sel('di-new-open-missions'));
    expect(await stageView(page)).toBe('missions');
    expect(await text(page, `${sel('di-missions-status-running')} ${sel('di-missions-count')}`)).toBe('15');
    await page.close();
  },
  STEPS_MS,
);

test(
  'keyboard: a status filter works from the keyboard, with a visible focus ring',
  async () => {
    const page = await open('di-scan-cast=v3&di-scan-view=missions');
    await page.focus(sel('di-missions-status-all'));
    await page.keyboard.press('Tab'); // keyboard modality: focus moves to Needs you
    expect(await activeTestid(page)).toBe('di-missions-status-needs');
    const ring = await page.evaluate(() => {
      const style = getComputedStyle(document.activeElement as Element);
      return { style: style.outlineStyle, width: style.outlineWidth };
    });
    expect(ring.style).toBe('solid');
    expect(ring.width).toBe('2px');
    await page.keyboard.press('Enter');
    expect(await page.getAttribute(sel('di-missions-status-needs'), 'aria-pressed')).toBe('true');
    expect(await missionRows(page)).toEqual(['v3-inbox', 'v3-probe', 'v3-install', 'v3-settings', 'v3-dark']);
    await page.close();
  },
  STEPS_MS,
);

test(
  'a screen opened directly does not take focus on first paint (focus moves only when a screen change removed it)',
  async () => {
    for (const query of ['di-scan-cast=v3&di-scan-view=missions', 'di-scan-view=mission', 'di-scan-view=proposal']) {
      const page = await open(query);
      expect(await page.evaluate(() => document.activeElement === document.body), query).toBe(true);
      await page.close();
    }
  },
  STEPS_MS,
);

test(
  'B-H1 round 5: with Reception missing and a stale office, Needs you and Running say what they do not know, never "no matches"',
  async () => {
    const page = await open('di-scan-case=unknown&di-scan-view=missions');
    for (const [id, word, reason] of [
      ['needs', 'not read', 'Reception is not read in this office.'],
      ['running', 'not known', 'status is not current while the office is not live'],
    ] as const) {
      expect(await text(page, `${sel(`di-missions-status-${id}`)} ${sel('di-missions-count')}`)).toBe(word);
      await page.click(sel(`di-missions-status-${id}`));
      const pageText = await text(page, sel('di-missions-page'));
      expect(pageText, id).not.toContain('No Missions match this filter.');
      expect(await text(page, sel('di-missions-unknown')), id).toContain(reason);
      expect(await text(page, sel('di-missions-unknown')), id).toMatch(/^2 Missions may /);
    }
    await page.close();
  },
  STEPS_MS,
);

test(
  'C3/C4: after moving from one Mission to another, Code Diff shows the new Mission\'s first file (reviewer sequence: Home, Payment retry engine, rail Next panel overhaul, Code Diff)',
  async () => {
    const page = await open('di-scan-view=list');
    // from Home, open Payment retry engine (select, then open)
    const payments = '[data-testid="di-scan-row"][data-mission-ref="lock-payments"]';
    await page.click(payments);
    await page.click(payments);
    expect(await stageView(page)).toBe('mission');
    expect(await text(page, sel('di-scan-mission-title'))).toBe('Payment retry engine');
    // then Next panel overhaul through the rail
    await page.click('[data-testid="di-rail-orchestrator"] >> text=Next panel overhaul');
    expect(await text(page, sel('di-scan-mission-title'))).toBe('Next panel overhaul');
    // then Code Diff: its own first file, never "No changes in this file."
    await page.click(sel('di-scan-code-diff'));
    expect(await text(page, sel('di-scan-editor-path'))).toBe('src/world/open/next-panel-overhaul/code-diff.ts');
    expect(await count(page, sel('di-scan-editor-unchanged'))).toBe(0);
    expect(await page.getAttribute('[data-file-id="code"]', 'aria-current')).toBe('true');
    await page.close();
  },
  STEPS_MS,
);
