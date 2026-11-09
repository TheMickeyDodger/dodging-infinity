/**
 * Pass A visual findings V-1, V-2 and V-3, measured in a real browser on the scan specimen (test/harness/di-scan-lock.html,
 * the labelled DI_SCAN_LOCK sample missions) at EXACTLY 1672x941, the pixel-lock 02 reference size.
 *
 *  V-1  Status is a DOT plus its word (Mission text; CONTROL-MAP locked rules "same-shape dot + word"), overriding the
 *       pixel-lock's squares: every status marker on every scan surface is the same round dot, beside a word.
 *  V-2  Swarm is FOUR separately outlined cards with gutters, the pixel-lock 02 composition (D-2: composition, in Home tokens).
 *  V-3  Everything ABOVE Swarm lines up with the pixel-lock 02 reference at 1672x941, and the type is at its scale. The
 *       reference numbers are hand measurements of the image (fixtures/pixel-lock-02-measurements.FIXTURE.json, which
 *       says how); nothing here derives them from the stylesheet under test. Below Swarm, Delivery and the state actions
 *       are required additions the reference does not contain, so positions there legitimately differ and are not held.
 *
 * Presentation only. Browser: the installed Chrome (playwright-core, channel `chrome`), else Playwright's Chromium; with
 * neither, every test is skipped with an explicit message.
 */
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

import type { Browser, Page } from 'playwright-core';
import { chromium } from 'playwright-core';
import type { ViteDevServer } from 'vite';
import { createServer } from 'vite';
import { afterAll, beforeAll, beforeEach, expect, test } from 'vitest';

const STEPS_MS = 120_000;
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const REF = JSON.parse(
  fs.readFileSync(
    path.join(root, 'test/fixtures/pixel-lock-02-measurements.FIXTURE.json'),
    'utf-8',
  ),
) as {
  viewport: { width: number; height: number };
  boxes: {
    backButton: { top: number; bottom: number };
    statusPills: { top: number; bottom: number };
    detailsCard: { top: number; bottom: number };
    detailsCardFootRule: number;
    swarmCards: Array<{ top: number; bottom: number; left: number; right: number }>;
  };
  derivedFontPx: Record<string, number>;
};

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
    console.warn(`SKIPPED (scan composition): ${unavailable}`);
    ctx.skip();
  }
});

async function open(view: 'list' | 'mission' | 'diff'): Promise<Page> {
  const page = await browser!.newPage({ viewport: REF.viewport });
  await page.goto(`${origin}/test/harness/di-scan-lock.html?di-scan-view=${view}`, {
    waitUntil: 'load',
  });
  await page.waitForSelector('[data-testid="di-rail"]');
  await page.evaluate(async () => {
    await document.fonts.ready;
  });
  return page;
}

/**
 * CHANGED IN the Free UI streamline (2026-11-04, approved Mission mn-92b246d…: "default each details section to one short status line; disclose ... on demand"; Lead1's scope ruling,
 * addendum 5: pixel-lock 02 is an inherited pin and does not freeze the Mission DETAILS card or roster geometry). Mission details now opens FOLDED: the description on one line in a card
 * sized by it, the Swarm as one summary line with its roster on demand. The pixel-lock 02 composition is what the details are SHOWN IN FULL, so these tests open the description and the
 * roster (the person's own two controls, by click) before they measure it; the reference numbers are unchanged. With DI_STREAMLINE_SHOTS=<dir> the folded and the shown-in-full views
 * are saved there (before/after evidence).
 */
async function showInFull(page: Page): Promise<void> {
  await page.click('[data-testid="di-scan-details-toggle"]');
  await page.click('[data-testid="di-scan-swarm-toggle"]');
  await page.waitForFunction(() => document.querySelector('[data-testid="di-scan-swarm-member"]')?.getClientRects().length !== 0);
}
const SHOTS = process.env['DI_STREAMLINE_SHOTS'] ?? null;
async function shot(page: Page, name: string): Promise<void> {
  if (SHOTS === null) return;
  fs.mkdirSync(SHOTS, { recursive: true });
  await page.screenshot({ path: path.join(SHOTS, `${name}.png`) });
}

const box = (page: Page, selector: string) =>
  page.evaluate((s) => {
    const el = document.querySelector(s);
    if (!el) return null;
    const r = el.getBoundingClientRect();
    return { top: r.top, bottom: r.bottom, left: r.left, right: r.right };
  }, selector);
const fontPx = (page: Page, selector: string) =>
  page.evaluate((s) => {
    const el = document.querySelector(s);
    return el ? parseFloat(getComputedStyle(el).fontSize) : null;
  }, selector);

test(
  'V-1 DOT + WORD: every status marker on every scan surface is the same round dot, beside its word',
  async () => {
    for (const view of ['mission', 'list'] as const) {
      const page = await open(view);
      const dots = await page.evaluate(() =>
        [...document.querySelectorAll<HTMLElement>('.di-scan-dot')].map((dot) => {
          const s = getComputedStyle(dot);
          const r = dot.getBoundingClientRect();
          const surface =
            dot.closest('[data-testid="di-rail"]') !== null
              ? 'rail'
              : dot.closest('[data-testid="di-scan-delivery"]') !== null
                ? 'delivery'
                : dot.closest('[data-fill="solid"]') !== null
                  ? 'mission pill'
                  : 'panel pill';
          return {
            surface,
            radius: s.borderTopLeftRadius,
            round:
              s.borderTopLeftRadius === '50%' || parseFloat(s.borderTopLeftRadius) >= r.width / 2,
            square: Math.abs(r.width - r.height) < 0.5,
            size: r.width,
            word: (dot.parentElement?.textContent ?? '').trim(),
          };
        }),
      );
      const surfaces = new Set(dots.map((d) => d.surface));
      if (view === 'mission')
        for (const s of ['rail', 'delivery', 'mission pill']) expect(surfaces, view).toContain(s);
      else expect(surfaces, view).toContain('panel pill');
      for (const d of dots) {
        expect(d.round, `${view} ${d.surface}: ${d.radius}`).toBe(true);
        expect(d.square, `${view} ${d.surface}: width equals height`).toBe(true);
        expect(d.size, `${view} ${d.surface}`).toBeGreaterThan(0);
        expect(d.word, `${view} ${d.surface}: a word beside the dot`).toMatch(/[A-Za-z]/);
      }
      await page.close();
    }
  },
  STEPS_MS,
);

test(
  'V-2 Swarm is four separately outlined cards with gutters (pixel-lock 02 composition), not one outlined container, when the roster is shown',
  async () => {
    const page = await open('mission');
    // folded (A9): the roster is not drawn until it is asked for, and its summary line is
    expect(await page.locator('[data-testid="di-scan-swarm-member"]').first().isVisible()).toBe(false);
    expect(await page.locator('[data-testid="di-scan-swarm-status"]').isVisible()).toBe(true);
    await showInFull(page);
    const cards = await page.evaluate(() =>
      [...document.querySelectorAll<HTMLElement>('[data-testid="di-scan-swarm-member"]')].map(
        (card) => {
          const s = getComputedStyle(card);
          const r = card.getBoundingClientRect();
          return {
            left: r.left,
            right: r.right,
            top: r.top,
            outline:
              s.borderTopStyle === 'solid' &&
              parseFloat(s.borderTopWidth) >= 1 &&
              s.borderLeftStyle === 'solid',
          };
        },
      ),
    );
    expect(cards).toHaveLength(4);
    for (const c of cards) expect(c.outline).toBe(true);
    for (let i = 1; i < cards.length; i++) {
      const gutter = cards[i]!.left - cards[i - 1]!.right;
      expect(gutter, `gutter ${i}`).toBeGreaterThanOrEqual(12);
      expect(gutter, `gutter ${i}`).toBeLessThanOrEqual(20);
    }
    const container = await page.evaluate(() => {
      const s = getComputedStyle(document.querySelector('.di-scan-swarm-box')!);
      // the base styles give every element `border: 0 solid`, so "no outline" is a zero width on every side
      return [s.borderTopWidth, s.borderRightWidth, s.borderBottomWidth, s.borderLeftWidth];
    });
    expect(container, 'the container draws no outline of its own').toEqual([
      '0px',
      '0px',
      '0px',
      '0px',
    ]);
    await page.close();
  },
  STEPS_MS,
);

// CHANGED IN B2b (Q1: the ramp wins over pixel-lock 02, the Lead): this test held Mission details to pixel-lock 02's POSITIONS (Back, the pills, the
// card, its foot rule, within 4 px of the reference at 1672 x 941) and to the reference's TYPE SCALE (56 px title, 24 px pills, 30 px headings). B
// retires both: the type is DESIGN.md's ramp, and the composition is docked beside the list. What the composition still owes is kept, as
// RELATIONSHIPS: the order (Back, title, pills, card), folded the card is sized by its line, its foot and its toggle (no reserved space), the foot inside
// the card, the Swarm below it, the roster directly under its toggle; and every text at its ramp step.
const RAMP: Record<string, number> = { breadcrumb: 12, title: 18, statusPill: 12, cardHeading: 14, body: 14, swarmHeading: 14, swarmName: 13 };
test(
  'V-3 (B2b, Q1) at 1672x941: Mission details keeps its ORDER and relationships, every text at its ramp step; folded, the card is sized by its line',
  async () => {
    const page = await open('mission');
    const stagePill = '[data-testid="di-scan-stage"] [data-testid="di-scan-pill"]';
    // FOLDED (A9.1)
    await shot(page, 'composition-1672-01-folded');
    const back0 = await box(page, '[data-testid="di-scan-back"]');
    const title0 = await box(page, '[data-testid="di-scan-mission-title"]');
    const pill0 = await box(page, stagePill);
    const folded = await box(page, '[data-testid="di-scan-details"]');
    expect(back0!.bottom, 'Back is above the title').toBeLessThanOrEqual(title0!.top + 1);
    expect(title0!.bottom, 'the title is above the pills').toBeLessThanOrEqual(pill0!.top + 1);
    expect(pill0!.bottom, 'the pills are above the card').toBeLessThanOrEqual(folded!.top + 1);
    const toggle = await box(page, '[data-testid="di-scan-details-toggle"]');
    const foot = await box(page, '.di-scan-card-foot');
    expect(foot!.top - toggle!.bottom, 'folded: no reserved space between the toggle and the foot').toBeLessThanOrEqual(24);
    expect(foot!.bottom, 'folded: the card holds its foot').toBeLessThanOrEqual(folded!.bottom + 1);
    expect((await box(page, '.di-scan-swarm-heading'))!.top, 'folded: the Swarm starts below the card').toBeGreaterThanOrEqual(folded!.bottom);
    // SHOWN IN FULL
    await showInFull(page);
    await shot(page, 'composition-1672-02-shown-in-full');
    const card = await box(page, '[data-testid="di-scan-details"]');
    const rule = await box(page, '.di-scan-card-foot');
    expect(rule!.top, 'the foot rule is inside the card').toBeGreaterThanOrEqual(card!.top);
    expect(rule!.bottom, 'the foot rule is inside the card').toBeLessThanOrEqual(card!.bottom + 1);
    // CHANGED (A9.2), kept: the cards start directly under the roster toggle, and the Swarm heading follows the card
    const swarm = await box(page, '[data-testid="di-scan-swarm-member"]');
    const rosterToggle = await box(page, '[data-testid="di-scan-swarm-toggle"]');
    const heading = await box(page, '.di-scan-swarm-heading');
    expect(swarm!.top - rosterToggle!.bottom, 'the roster starts directly under its toggle').toBeGreaterThanOrEqual(0);
    expect(swarm!.top - rosterToggle!.bottom, 'the roster starts directly under its toggle').toBeLessThanOrEqual(24);
    // the Swarm heading's own top margin is 14 px (diScanPanel.css, kept, not forced by the ramp): the heading follows the card with no gap beyond it
    expect(heading!.top - card!.bottom, 'the Swarm heading follows the card').toBeGreaterThanOrEqual(0);
    expect(heading!.top - card!.bottom, 'the Swarm heading follows the card by its own margin, no more').toBeLessThanOrEqual(14 + 1);
    // the type, at the RAMP's step for each role (b2b-record.md, section 3)
    const sizes: Array<[string, string]> = [
      ['.di-scan-crumb', 'breadcrumb'],
      ['[data-testid="di-scan-mission-title"]', 'title'],
      [stagePill, 'statusPill'],
      ['#di-scan-details-heading', 'cardHeading'],
      ['.di-scan-card p', 'body'],
      ['.di-scan-swarm-heading', 'swarmHeading'],
      ['.di-scan-card-copy .di-scan-name', 'swarmName'],
    ];
    for (const [selector, key] of sizes) expect(await fontPx(page, selector), `${key} font size`).toBe(RAMP[key]);
    await page.close();
  },
  STEPS_MS,
);

test(
  'below 1500 px wide (the v3 02 twin, 1280x720) Mission details keeps the same composition at a compact scale, with Swarm on the first screen',
  async () => {
    const page = await browser!.newPage({ viewport: { width: 1280, height: 720 } });
    await page.goto(`${origin}/test/harness/di-scan-lock.html?di-scan-view=mission`, {
      waitUntil: 'load',
    });
    await page.waitForSelector('[data-testid="di-rail"]');
    await page.evaluate(async () => {
      await document.fonts.ready;
    });
    // CHANGED IN B2b (Q1): the compact pixel-lock scale (about 0.72 of 02's) is retired for type; the title is the ramp's title step at every width
    const title = await fontPx(page, '[data-testid="di-scan-mission-title"]');
    expect(title).toBe(18);
    // CHANGED (A9.2): the Swarm leads with its summary line on the first screen; its cards are measured shown (a hidden card's box is 0, which would pass for nothing)
    expect((await box(page, '[data-testid="di-scan-swarm-status"]'))!.top, 'the Swarm summary is on the first screen').toBeLessThan(720);
    await page.click('[data-testid="di-scan-swarm-toggle"]');
    const swarm = await box(page, '[data-testid="di-scan-swarm-member"]');
    expect(swarm!.bottom - swarm!.top, 'the cards are drawn').toBeGreaterThan(0);
    expect(swarm!.top, 'the Swarm cards start on the first screen').toBeLessThan(720);
    const cards = await page.evaluate(() =>
      [...document.querySelectorAll<HTMLElement>('[data-testid="di-scan-swarm-member"]')].map(
        (c) => getComputedStyle(c).borderTopStyle,
      ),
    );
    expect(cards).toEqual(['solid', 'solid', 'solid', 'solid']);
    await page.close();
  },
  STEPS_MS,
);

test(
  'B-V1 (pass B review): the Code Diff at 1672x941 keeps the pixel-lock 03 composition: status bar under the tree too, first hunk header at the code column, row and gutter rules, changed comments in their diff colour',
  async () => {
    const page = await open('diff');
    const geo = await page.evaluate(() => {
      const r = (s: string) => document.querySelector(s)!.getBoundingClientRect();
      const bar = r('[data-testid="di-scan-statusbar"]');
      const tree = r('.di-scan-tree');
      const stage = r('[data-testid="di-scan-stage"]');
      const stageRight = stage.right;
      const head = document.querySelector('.di-scan-gutter-head')!;
      // the hunk header's TEXT position (a range), not its box: a right-aligned text sits in a box that starts at the column
      const range = document.createRange();
      range.selectNodeContents(head.querySelector('.di-scan-hunk')!);
      const hunk = range.getBoundingClientRect();
      const codeCol = document.querySelector('.di-scan-diff-line .di-scan-code')!.getBoundingClientRect();
      const line = (kind: string) => document.querySelector(`.di-scan-diff-line[data-kind="${kind}"]`)!;
      const colourOf = (el: Element) => getComputedStyle(el).color;
      const note = (kind: string) => line(kind).querySelector('.di-scan-tok-note')!;
      return {
        bar: { left: bar.left, right: bar.right, top: bar.top },
        stageLeft: stage.left,
        stageRight,
        treeBottom: tree.bottom,
        hunkLeft: hunk.left,
        codeLeft: codeCol.left,
        rowRule: getComputedStyle(line('ctx')).boxShadow,
        gutterRule: getComputedStyle(line('ctx').querySelector('.di-scan-ln')!).boxShadow,
        del: { line: colourOf(line('del')), note: colourOf(note('del')) },
        add: { line: colourOf(line('add')), note: colourOf(note('add')) },
        ctxNote: colourOf(document.querySelectorAll('.di-scan-diff-line[data-kind="ctx"] .di-scan-tok-note')[0]!),
      };
    });
    // the status bar spans the whole Mission content (from the rail's edge), and the tree ends where it starts
    expect(Math.abs(geo.bar.left - geo.stageLeft)).toBeLessThanOrEqual(1);
    // CHANGED IN B2b (Q9): "the whole Mission content" ends at the STAGE's right edge; at 1672 (two panes) that is the list dock's edge, not the window's
    expect(Math.abs(geo.bar.right - geo.stageRight)).toBeLessThanOrEqual(1);
    expect(Math.abs(geo.treeBottom - geo.bar.top)).toBeLessThanOrEqual(1);
    // the first hunk header starts the code column, beside Old and New (never pushed to the far right)
    expect(geo.hunkLeft - geo.codeLeft).toBeGreaterThanOrEqual(0);
    expect(geo.hunkLeft - geo.codeLeft).toBeLessThanOrEqual(24);
    // a rule under every code row and down the gutter columns
    expect(geo.rowRule).toMatch(/inset/);
    expect(geo.gutterRule).toMatch(/inset/);
    // a changed comment reads in its line's diff colour; an unchanged comment keeps its own muted colour
    expect(geo.del.note).toBe(geo.del.line);
    expect(geo.add.note).toBe(geo.add.line);
    expect(geo.ctxNote).not.toBe(geo.add.line);
    expect(geo.ctxNote).not.toBe(geo.del.line);
    await page.close();
  },
  STEPS_MS,
);
