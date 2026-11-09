/**
 * Pass C Home convergence (passA-visual-findings.md "PASS-C HOME CONVERGENCE REQUIREMENTS"), measured in a real browser on
 * the scan specimen (test/harness/di-scan-lock.html, the labelled DI_SCAN_LOCK sample Missions) at 1280x720, the size of
 * the locked Home reference (reconciled-free-ui-v3/01-home.jpg):
 *
 *  H-1  every collapsed card carries its OWN border, selected or not: the reference's SUBTLE grouping edge (sampled from
 *       01-home.jpg: edge about #121621 on a card fill about #0e121d; drawn here with the existing line token, the
 *       grouping edge of Mission details' outlined Swarm cards). It does not identify the control, the card's text does
 *       (WCAG 2.2 SC 1.4.11, Understanding: boundaries), so its contrast is MEASURED and logged, not held to 3:1. What
 *       carries state is held to 3:1 against BOTH neighbours (card fill and panel): the selected yellow edge and the
 *       focus ring.
 *  H-2  a collapsed card's status pills sit ON the title row, at its right; the title is compact (about 14px) and
 *       regular weight; the context line sits below. The expanded card's title takes the same compact regular scale.
 *  H-3  the expanded card's Swarm is, per role, the role's word above a SMALL avatar with the name BESIDE it.
 *
 * Mission details is governed by the pixel-lock composition (D-2) and must NOT take Home's compact treatment; that is
 * held, unchanged, by diScanComposition.test.ts (1672x941 and 1280x720). Presentation only. Browser: the installed Chrome
 * (playwright-core, channel `chrome`), else Playwright's Chromium; with neither, every test is skipped with a message.
 */
import path from 'node:path';
import { fileURLToPath } from 'node:url';

import type { Browser, Page } from 'playwright-core';
import { chromium } from 'playwright-core';
import type { ViteDevServer } from 'vite';
import { createServer } from 'vite';
import { afterAll, beforeAll, beforeEach, expect, test } from 'vitest';

const STEPS_MS = 120_000;
const HOME = { width: 1280, height: 720 };
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');

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
    console.warn(`SKIPPED (Home convergence): ${unavailable}`);
    ctx.skip();
  }
});

async function home(): Promise<Page> {
  const page = await browser!.newPage({ viewport: HOME });
  await page.goto(`${origin}/test/harness/di-scan-lock.html?di-scan-view=list`, { waitUntil: 'load' });
  await page.waitForSelector('[data-testid="di-scan-row"]');
  await page.evaluate(async () => {
    await document.fonts.ready;
  });
  return page;
}

type Box = { top: number; bottom: number; left: number; right: number };
/** Every collapsed card: its box, border, and the boxes of its title, pills and context line. */
const cards = (page: Page) =>
  page.evaluate(() => {
    const b = (el: Element | null): Box | null => {
      if (!el) return null;
      const r = el.getBoundingClientRect();
      return { top: r.top, bottom: r.bottom, left: r.left, right: r.right };
    };
    return [...document.querySelectorAll<HTMLElement>('[data-testid="di-scan-row"]')].map((row) => {
      const s = getComputedStyle(row);
      const title = row.querySelector('.di-scan-title');
      const t = title ? getComputedStyle(title) : null;
      return {
        id: row.getAttribute('data-mission-ref'),
        selected: row.getAttribute('aria-current') === 'true',
        box: b(row)!,
        border: { width: parseFloat(s.borderTopWidth), color: s.borderTopColor, style: s.borderTopStyle },
        title: b(title),
        titlePx: t ? parseFloat(t.fontSize) : null,
        titleWeight: t ? Number(t.fontWeight) : null,
        pills: b(row.querySelector('.di-scan-pills')),
        context: b(row.querySelector('.di-scan-context')),
      };
    });
  });
/** A token's painted colour, read the way the browser resolves it. */
const tokenColour = (page: Page, token: string) =>
  page.evaluate((t) => {
    const probe = document.createElement('div');
    probe.style.color = `var(${t})`;
    document.body.appendChild(probe);
    const value = getComputedStyle(probe).color;
    probe.remove();
    return value;
  }, token);
const centre = (b: Box) => (b.top + b.bottom) / 2;

test(
  // CHANGED IN B2a (Q8 = (a), Q4): a row is marked only while its Mission is DOCKED (aria-current, the same state as the rail's mark). The name said "the
  // selected one"; Home had one preselected (the specimen harness's first row, drawn with the removed inline card).
  'H-1: every collapsed Home card carries its OWN border, as separate cards, none marked with nothing docked; the DOCKED Mission\'s row keeps the yellow state edge',
  async () => {
    const page = await home();
    const all = await cards(page);
    expect(all.length).toBeGreaterThanOrEqual(3);
    const grouping = await tokenColour(page, '--di-scan-well-edge');
    const yellow = await tokenColour(page, '--di-scan-yellow');
    for (const card of all) {
      expect(card.border.style, card.id!).toBe('solid');
      expect(card.border.width, card.id!).toBeGreaterThanOrEqual(1);
      // unselected: the reference's subtle grouping edge; selected: its yellow state edge
      expect(card.border.color, card.id!).toBe(card.selected ? yellow : grouping);
    }
    expect(all.filter((c) => !c.selected).length).toBeGreaterThanOrEqual(2);
    expect(all.filter((c) => c.selected).map((c) => c.id), 'B2a: with nothing docked, no Home row is marked').toEqual([]);
    // what IDENTIFIES each card as a control (why its edge need not): a button with a non-empty text name
    const named = await page.evaluate(() =>
      [...document.querySelectorAll<HTMLElement>('[data-testid="di-scan-row"]')].map((r) => ({
        tag: r.tagName,
        name: (r.querySelector('.di-scan-title')?.textContent ?? '').trim(),
      })),
    );
    for (const n of named) {
      expect(n.tag).toBe('BUTTON');
      expect(n.name.length).toBeGreaterThan(0);
    }
    const lum = (rgb: string) => {
      const [r, g, b] = (rgb.match(/[\d.]+/g) ?? []).slice(0, 3).map((v) => {
        const c = Number(v) / 255;
        return c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
      });
      return 0.2126 * r! + 0.7152 * g! + 0.0722 * b!;
    };
    const contrast = (x: string, y: string) => {
      const [hi, lo] = [lum(x), lum(y)].sort((m, n) => n - m);
      return (hi! + 0.05) / (lo! + 0.05);
    };
    // per STATE, each edge against BOTH of its neighbours, the card's own fill inside and the panel outside; the LOWER governs
    const panel = await page.evaluate(() => getComputedStyle(document.querySelector('.di-scan')!).backgroundColor);
    const paint = (selected: boolean) =>
      page.evaluate((sel) => {
        const row = [...document.querySelectorAll<HTMLElement>('[data-testid="di-scan-row"]')].find(
          (r) => (r.getAttribute('aria-current') === 'true') === sel,
        )!;
        const s = getComputedStyle(row);
        return { edge: s.borderTopColor, fill: s.backgroundColor };
      }, selected);
    const report = (what: string, edge: string, fill: string) => {
      const onFill = contrast(edge, fill);
      const onPanel = contrast(edge, panel);
      const lower = Math.min(onFill, onPanel);
      console.info(
        `H-1 EDGE CONTRAST (${what}): ${edge} is ${onFill.toFixed(2)}:1 on the card fill ${fill} and ${onPanel.toFixed(2)}:1 on the panel ${panel}; lower ${lower.toFixed(2)}:1`,
      );
      return lower;
    };
    const unselected = await paint(false);
    // MEASURED, not held: a grouping edge (see the header)
    report('unselected, grouping edge, not held to 3:1', unselected.edge, unselected.fill);
    // the focus ring on a card, reached by the keyboard
    await page.focus('[data-testid="di-scan-row"]:not([aria-current])');
    await page.keyboard.press('Shift+Tab');
    await page.keyboard.press('Tab');
    const ring = await page.evaluate(() => {
      const el = document.activeElement as HTMLElement;
      const s = getComputedStyle(el);
      return { testid: el.getAttribute('data-testid'), style: s.outlineStyle, width: parseFloat(s.outlineWidth), color: s.outlineColor, fill: s.backgroundColor };
    });
    expect(ring.testid).toBe('di-scan-row');
    expect(ring.style).toBe('solid');
    expect(ring.width).toBeGreaterThanOrEqual(2);
    expect(report('focus ring, held to 3:1', ring.color, ring.fill)).toBeGreaterThanOrEqual(3);
    // separate cards: each ends before the next begins
    for (let i = 1; i < all.length; i++) expect(all[i]!.box.top).toBeGreaterThan(all[i - 1]!.box.bottom);
    // CHANGED IN B2a: the yellow state edge marks the DOCKED Mission's row. In B2a Home is COVERED while a Mission is docked (B2b docks the stage beside it at
    // 1440 and wider), so this reads the docked row's COMPUTED edge and fill, the colours it is drawn with when shown; it is not a capture of it shown.
    await page.click('[data-testid="di-scan-row"]');
    await page.waitForSelector('[data-testid="di-scan-row"][aria-current="true"]', { state: 'attached' });
    const docked = await paint(true);
    expect(docked.edge).toBe(yellow);
    expect(report('docked, yellow state edge, held to 3:1 (computed; Home covered in B2a)', docked.edge, docked.fill)).toBeGreaterThanOrEqual(3);
    await page.close();
  },
  STEPS_MS,
);

test(
  'H-2: a collapsed card\'s status pills sit ON the title row at its right; the title is compact (13 to 15px) and regular; the context line is below',
  async () => {
    const page = await home();
    for (const card of await cards(page)) {
      const { title, pills, context, box } = card;
      expect(title && pills && context, card.id!).toBeTruthy();
      // on the title row: the pills' middle lies within the title line
      expect(centre(pills!), card.id!).toBeGreaterThanOrEqual(title!.top - 2);
      expect(centre(pills!), card.id!).toBeLessThanOrEqual(title!.bottom + 2);
      // at its right: beside the title, ending at the card's right padding
      expect(pills!.left, card.id!).toBeGreaterThanOrEqual(title!.right - 0.5);
      expect(box.right - pills!.right, card.id!).toBeLessThanOrEqual(20);
      // compact and regular
      expect(card.titlePx!, card.id!).toBeGreaterThanOrEqual(13);
      expect(card.titlePx!, card.id!).toBeLessThanOrEqual(15);
      expect(card.titleWeight!, card.id!).toBeLessThanOrEqual(500);
      // the context line below the title row
      expect(context!.top, card.id!).toBeGreaterThanOrEqual(Math.max(title!.bottom, pills!.bottom) - 1);
    }
    // CHANGED IN B2a (Q8 = (a)): this also held Home's EXPANDED card's title to the same compact scale, its pills on its row. The card is removed (one click
    // docks), so that half has no subject; the docked title is Mission details' own (pixel-lock 02 scale, diScanComposition). The collapsed cards above are
    // unchanged. And Home now draws no expanded card at all:
    expect(await page.locator('[data-testid="di-scan-detail"]').count()).toBe(0);
    await page.close();
  },
  STEPS_MS,
);

// CHANGED IN B2a (Q8 = (a)): H-3 held Home's EXPANDED card's Swarm to reference 01-home (per role, its word above a SMALL avatar, the name beside it, the
// four in one row). The card is removed, so that layout has no subject. The four roles are not lost: they are in the docked Mission's roster, one toggle
// under its summary line (A9), drawn as Mission details' own four cards. This checks them THERE, rendered, on the same specimen.
test(
  'H-3 (B2a): Home draws no expanded card; the docked Mission\'s four roles, each with its word, avatar and name, are in its roster, one labelled toggle away',
  async () => {
    const page = await home();
    expect(await page.locator('[data-testid="di-scan-detail"]').count()).toBe(0);
    await page.click('[data-testid="di-scan-row"]');
    await page.waitForSelector('[data-testid="di-scan-mission-title"]');
    const toggle = page.locator('[data-testid="di-scan-swarm-toggle"]');
    expect(await toggle.getAttribute('aria-expanded')).toBe('false');
    await toggle.click();
    const members = page.locator('[data-testid="di-scan-swarm-member"]');
    expect(await members.count()).toBe(4);
    for (let i = 0; i < 4; i++) {
      const m = members.nth(i);
      expect(await m.isVisible(), String(i)).toBe(true);
      for (const part of ['.di-scan-role', '.di-scan-avatar', '.di-scan-name']) expect(await m.locator(part).isVisible(), `${i} ${part}`).toBe(true);
      expect((await m.locator('.di-scan-role').innerText()).trim().length, String(i)).toBeGreaterThan(0);
    }
    await page.close();
  },
  STEPS_MS,
);

// ── C-V1 (pass C, Supervisor visual review): Home against 01-home.jpg, MEASURED (the reference sampled in a browser):
// the panel's EDGE at x 852 (its own edge rule; the dark lead-in from x 847 is not the edge, and measuring it would read
// 433), so the panel is 428 wide at 1280; its first (selected) card's top edge at y 93; and the full "Search missions..."
// placeholder legible in its field. The first card's LEFT follows the panel (864 = 852 + 12, the same 12 px padding).
test(
  'C-V1 (1, 2): the panel edge sits where 01-home.jpg draws its edge rule (x 852, 428 wide) and the first Home card starts where the reference draws it (top y 93, within 3 px)',
  async () => {
    const page = await home();
    const firstTop = await page.evaluate(
      () => document.querySelector('[data-testid="di-scan-row"]')!.getBoundingClientRect().top,
    );
    expect(Math.abs(firstTop - 93), `first card top ${firstTop}`).toBeLessThanOrEqual(3);
    // the panel's edge at the reference's edge rule (x 852), so the panel is 428 wide; the first card's left follows it
    const panel = await page.evaluate(() => {
      const r = document.querySelector('.di-scan')!.getBoundingClientRect();
      const row = document.querySelector('[data-testid="di-scan-row"]')!.getBoundingClientRect();
      return { left: r.left, width: r.width, rowLeft: row.left };
    });
    expect(Math.round(panel.left), 'the panel edge at the reference edge rule').toBe(852);
    expect(Math.round(panel.width)).toBe(428);
    // the edge, its 1 px border, then the list's 12 px padding: 865 (the reference's first card measures 864, within 1 px)
    expect(Math.round(panel.rowLeft), 'the first card left follows the panel edge').toBe(852 + 1 + 12);
    await page.close();
  },
  STEPS_MS,
);

test(
  'C-V1 (3): the search placeholder is never clipped: "Search missions..." fits its field at every desktop width the panel shows, with a one-digit and a "?" Open badge',
  async () => {
    const results: string[] = [];
    // each case's INTENDED badge: the specimen's one known need ("1"), and the unknown case's "?" (never no badge)
    const BADGE: Record<string, string> = { 'di-scan-view=list': '1', 'di-scan-case=unknown&di-scan-view=list': '?' };
    for (const query of Object.keys(BADGE))
      for (const width of [900, 1100, 1200, 1280, 1440, 1600, 1672]) {
        const page = await browser!.newPage({ viewport: { width, height: 800 } });
        await page.goto(`${origin}/test/harness/di-scan-lock.html?${query}`, { waitUntil: 'load' });
        await page.waitForSelector('[data-testid="di-scan-search"]');
        await page.evaluate(async () => {
          await document.fonts.ready;
        });
        const fit = await page.evaluate(() => {
          const input = document.querySelector<HTMLInputElement>('[data-testid="di-scan-search"]')!;
          const s = getComputedStyle(input);
          const c = document.createElement('canvas').getContext('2d')!;
          c.font = `${s.fontStyle} ${s.fontWeight} ${s.fontSize} ${s.fontFamily}`;
          return {
            placeholder: input.placeholder,
            text: c.measureText(input.placeholder).width,
            room: input.clientWidth - parseFloat(s.paddingLeft) - parseFloat(s.paddingRight),
            badge: document.querySelector('[data-testid="di-scan-attention"]')?.textContent ?? 'none',
            // the field never overlaps the tabs, and stays inside the panel
            lastTabRight: Math.max(...[...document.querySelectorAll('.di-scan-tab')].map((t) => t.getBoundingClientRect().right)),
            fieldLeft: input.getBoundingClientRect().left,
            fieldRight: input.getBoundingClientRect().right,
            panelRight: document.querySelector('.di-scan')!.getBoundingClientRect().right,
          };
        });
        results.push(`${query} @${width}: badge ${fit.badge}, text ${fit.text.toFixed(1)} in ${fit.room.toFixed(1)}`);
        expect(fit.placeholder).toBe('Search missions...');
        expect(fit.badge, `${query} at ${width}: the Open badge is the intended one`).toBe(BADGE[query]);
        expect(fit.text, `${query} at ${width}: "${fit.placeholder}" is ${fit.text.toFixed(1)} px in ${fit.room} px`).toBeLessThanOrEqual(fit.room);
        expect(fit.fieldLeft, `${query} at ${width}: the field starts after the last tab`).toBeGreaterThanOrEqual(fit.lastTabRight);
        expect(fit.fieldRight, `${query} at ${width}: the field ends inside the panel`).toBeLessThanOrEqual(fit.panelRight);
        await page.close();
      }
    console.info(`C-V1 PLACEHOLDER FIT:\n${results.join('\n')}`);
  },
  STEPS_MS,
);
