/**
 * THE STAGE HEADER, measured two ways, plus the controls that prove the measurement can be wrong. ONE bounded contract, shared by the header regression.
 *
 * WHY TWO INSTRUMENTS, and neither is asked the other's question:
 *  - LAYOUT BOXES answer "what are the boxes": every header item's border box, which pairs of boxes overlap, which item's OWN text is drawn outside its box
 *    (a visually hidden child can neither inflate nor hide that), which items are not rendered (`display: none`) or only visually hidden (1 px), which rows the
 *    items sit on, and the accessible tree of the Location landmark (what a screen reader reaches).
 *  - PIXELS answer "what is drawn": each item is drawn ALONE on the otherwise blank bar and the drawn masks are intersected, so an overlap is observed in pixels
 *    and not inferred from boxes. A pixel counts as drawn only if some channel differs from the blank bar by more than TOLERANCE levels: the controls found 4-level
 *    raster noise on single pixels (items that merely touch read as "overdrawing" without it) and a 20-level positive control proves a faint real overdraw is
 *    still found above it. A tolerance is only safe when something smaller than the effect proves the instrument still sees it.
 *
 * THE CONTROLS (`headerControls`) run the SAME measure and pixel code on a synthetic header and each carries an expectation the instrument must meet: negative
 * (no overlap with a visually hidden description present; an overflow:hidden item with a visually hidden LONG-TEXT child is NOT clipped; items that only touch
 * are not an overlap; a normal Location tree lists every crumb) and positive (own text wider than the box; text drawn beyond an overflow:visible box; a leaf over a
 * tab by boxes AND pixels, also with hidden text on the overlapped item; a FAINT overdraw; a display:none leaf is OMITTED, never silent, and absent from the
 * Location tree; a visually hidden leaf is reported as such and STAYS in the tree). A test that uses this instrument runs the controls first.
 * It reports; the test asserts. A custom measurement, not an auditor.
 */
import type { Browser, Page } from 'playwright-core';
import { PNG } from 'pngjs';

/** A pixel is DRAWN only if some channel differs from the blank bar by more than this. */
export const TOLERANCE = 12;

export interface HeaderBox {
  name: string;
  x: number;
  right: number;
  y: number;
  bottom: number;
  w: number;
  h: number;
}
export interface HeaderMeasure {
  boxes: HeaderBox[];
  overlaps: Array<{ a: string; b: string; px: number }>;
  /** Items whose OWN text is drawn beyond their box (the title is the one item allowed to give way). */
  clipped: string[];
  /** Items present but not rendered (display: none): an omission, reported, never silent. */
  omitted: string[];
  /** Items reduced to a visually hidden 1 px box: still in the accessibility tree. */
  visuallyHidden: string[];
  /** Items beyond the bar's content box. */
  offBar: string[];
  rows: number;
  barH: number;
  titleShown: number | null;
  docScrollW: number;
  innerW: number;
  /** Header controls in DOM order (what Tab reaches, in order). */
  domOrder: string[];
  /** The Location landmark's accessible tree, one line per item. */
  locationAria: string;
  /** The three REQUIRED sibling tabs collected BY TEST ID, whatever their box: the item list skips tabs of 1.5 px or less and cannot report an omitted tab. */
  requiredTabs: RequiredTab[];
}
export interface RequiredTab {
  id: string;
  present: boolean;
  rendered: boolean;
  w: number;
  h: number;
}
/** The REQUIRED sibling tabs (by test id) that cannot be seen and pressed: absent, not rendered (display: none), or no larger than 1.5 px. Independent of the item list's size filter. */
export const tabsNotReachable = (m: { requiredTabs: RequiredTab[] }): string[] =>
  m.requiredTabs.filter((t) => !t.present || !t.rendered || t.w <= 1.5 || t.h <= 1.5).map((t) => t.id);

export interface DrawnOverlap {
  a: string;
  b: string;
  sharedDrawnPixels: number;
}

/* Both evaluate bodies below collect the SAME items: back, mark, the crumb's children, the sibling tabs (skipping a visually hidden description), the tools.
 *
 * LIMIT OF THIS ITEM LIST (recorded at the header increment's acceptance; CLOSED for the REQUIRED tabs by `requiredTabs` and `tabsNotReachable` below, with negative controls N5, P8, P9, P10): the
 * sibling tabs are collected only when their box is larger than 1.5 px in width or height (that is how the 1 px visually hidden description is skipped).
 * A REQUIRED tab that is `display: none`, or collapsed to 1.5 px or less, therefore never enters the item list and can NOT be reported as `omitted` or
 * `visuallyHidden`: omission of a required sibling tab is not reliably detected here. The controls (N1 to N4, P1 to P7) have no negative control for it.
 * What closes it: an INDEPENDENT collection of the required tabs (by their known test ids, whatever their box) and negative controls for it. */

export async function measureHeader(page: Page): Promise<HeaderMeasure> {
  const m = await page.evaluate(() => {
    const bar = document.querySelector('.di-scan-stage-bar')!;
    const items: Array<[string, Element]> = [];
    const add = (name: string, el: Element | null) => {
      if (el) items.push([name, el]);
    };
    add('back', bar.querySelector('.di-scan-back'));
    add('mark', bar.querySelector('.di-scan-mark'));
    const crumbNames = ['crumb-world', 'sep1', 'crumb-tab', 'sep2', 'crumb-title', 'sep3', 'crumb-leaf'];
    // a header WITHOUT a crumb (Comments, drawn to its reference) simply has no crumb items; the Location landmark's absence is reported by `locationAria` being empty
    [...(bar.querySelector('.di-scan-crumb')?.children ?? [])].forEach((c, i) => add(crumbNames[i] ?? `crumb-${i}`, c));
    const sib = bar.querySelector('.di-scan-siblings');
    if (sib) {
      for (const c of sib.children) {
        const b = c.getBoundingClientRect();
        if (b.width > 1.5 || b.height > 1.5) add('tab-' + (c.getAttribute('data-testid') ?? '').replace('di-scan-sibling-', ''), c);
      }
    }
    const tools = bar.querySelector('.di-scan-header-tools');
    if (tools) [...tools.children].forEach((c, i) => add(`tool-${i}`, c));

    const rect = (el: Element) => {
      const b = el.getBoundingClientRect();
      return { x: +b.x.toFixed(1), right: +b.right.toFixed(1), y: +b.y.toFixed(1), bottom: +b.bottom.toFixed(1), w: +b.width.toFixed(1), h: +b.height.toFixed(1) };
    };
    const rendered = items.filter(([, el]) => el.getClientRects().length > 0);
    const omitted = items.filter(([, el]) => el.getClientRects().length === 0).map(([n]) => n);
    const visuallyHidden = rendered
      .filter(([, el]) => {
        const b = el.getBoundingClientRect();
        return b.width <= 1.5 && b.height <= 1.5;
      })
      .map(([n]) => n);
    const shown = rendered.filter(([n]) => !visuallyHidden.includes(n));
    const boxes = shown.map(([name, el]) => ({ name, ...rect(el) }));
    const overlaps: Array<{ a: string; b: string; px: number }> = [];
    for (let i = 0; i < boxes.length; i++) {
      for (let j = i + 1; j < boxes.length; j++) {
        const a = boxes[i]!;
        const b = boxes[j]!;
        const ox = Math.min(a.right, b.right) - Math.max(a.x, b.x);
        const oy = Math.min(a.bottom, b.bottom) - Math.max(a.y, b.y);
        if (ox > 0.5 && oy > 0.5) overlaps.push({ a: a.name, b: b.name, px: +ox.toFixed(1) });
      }
    }
    const ownTextRight = (el: Element) => {
      let right = -1;
      for (const n of el.childNodes) {
        if (n.nodeType === 3 && (n.textContent ?? '').trim()) {
          const rg = document.createRange();
          rg.selectNodeContents(n);
          right = Math.max(right, rg.getBoundingClientRect().right);
        }
      }
      return right;
    };
    const clipped = shown.filter(([name, el]) => name !== 'crumb-title' && ownTextRight(el) > el.getBoundingClientRect().right + 0.5).map(([n]) => n);
    const bb = bar.getBoundingClientRect();
    const cs = getComputedStyle(bar);
    const padL = parseFloat(cs.paddingLeft);
    const padR = parseFloat(cs.paddingRight);
    const offBar = boxes.filter((b) => b.right > bb.right - padR + 0.5 || b.x < bb.x + padL - 0.5).map((b) => b.name);
    const centres = boxes.filter((b) => b.h > 0).map((b) => Math.round(b.y + b.h / 2)).sort((a, b) => a - b);
    const rows = centres.reduce<number[]>((acc, y) => (acc.length && y - acc[acc.length - 1]! <= 8 ? acc : [...acc, y]), []).length;
    const title = shown.find(([n]) => n === 'crumb-title');
    const domOrder = [...bar.querySelectorAll('button')].map((b) => (b.getAttribute('aria-label') ?? b.textContent ?? '').trim());
    // the REQUIRED sibling tabs, collected BY TEST ID whatever their box (the item list above skips tabs of 1.5 px or less, so it cannot report an omitted tab)
    const requiredTabs = ['diff', 'herdr', 'comments'].map((id) => {
      const el = bar.querySelector(`[data-testid="di-scan-sibling-${id}"]`);
      const b = el === null ? null : el.getBoundingClientRect();
      return { id, present: el !== null, rendered: el !== null && el.getClientRects().length > 0, w: b === null ? 0 : +b.width.toFixed(1), h: b === null ? 0 : +b.height.toFixed(1) };
    });
    return {
      boxes,
      overlaps,
      clipped,
      omitted,
      visuallyHidden,
      offBar,
      rows,
      barH: +bb.height.toFixed(1),
      titleShown: title ? title[1].getBoundingClientRect().width : null,
      docScrollW: document.documentElement.scrollWidth,
      innerW: window.innerWidth,
      domOrder,
      requiredTabs,
    };
  });
  // no Location landmark (a header without a crumb): reported as an empty tree at once, never waited on
  const locationAria =
    (await page.locator('nav[aria-label="Location"]').count()) === 0
      ? ''
      : await page
          .locator('nav[aria-label="Location"]')
          .first()
          .ariaSnapshot()
          .catch((e: unknown) => `ERROR ${String(e).split('\n')[0]}`);
  return { ...m, locationAria };
}

export async function drawnOverlaps(page: Page): Promise<{ drawn: Record<string, number>; overdrawPairs: DrawnOverlap[] }> {
  const { clip, names } = await page.evaluate(() => {
    const bar = document.querySelector('.di-scan-stage-bar')!;
    const names: string[] = [];
    const els: Element[] = [];
    const add = (name: string, el: Element | null) => {
      if (el) {
        names.push(name);
        els.push(el);
      }
    };
    add('back', bar.querySelector('.di-scan-back'));
    add('mark', bar.querySelector('.di-scan-mark'));
    const crumbNames = ['crumb-world', 'sep1', 'crumb-tab', 'sep2', 'crumb-title', 'sep3', 'crumb-leaf'];
    // a header WITHOUT a crumb (Comments, drawn to its reference) simply has no crumb items; the Location landmark's absence is reported by `locationAria` being empty
    [...(bar.querySelector('.di-scan-crumb')?.children ?? [])].forEach((c, i) => add(crumbNames[i] ?? `crumb-${i}`, c));
    const sib = bar.querySelector('.di-scan-siblings');
    if (sib) {
      for (const c of sib.children) {
        const b = c.getBoundingClientRect();
        if (b.width > 1.5 || b.height > 1.5) add('tab-' + (c.getAttribute('data-testid') ?? '').replace('di-scan-sibling-', ''), c);
      }
    }
    const tools = bar.querySelector('.di-scan-header-tools');
    if (tools) [...tools.children].forEach((c, i) => add(`tool-${i}`, c));
    (window as unknown as { __hdrItems: Element[] }).__hdrItems = els;
    const b = bar.getBoundingClientRect();
    return { clip: { x: Math.floor(b.x), y: Math.floor(b.y), width: Math.ceil(b.width), height: Math.ceil(b.height) }, names };
  });
  const show = (which: number) =>
    page.evaluate((w) => {
      (window as unknown as { __hdrItems: HTMLElement[] }).__hdrItems.forEach((el, i) => {
        el.style.visibility = w === i ? 'visible' : 'hidden';
      });
    }, which);
  const shot = async () => PNG.sync.read(await page.screenshot({ clip }));
  await show(-1);
  const blank = await shot();
  const masks: Array<{ m: Uint8Array; n: number }> = [];
  for (let i = 0; i < names.length; i++) {
    await show(i);
    const img = await shot();
    const m = new Uint8Array(img.width * img.height);
    let n = 0;
    for (let p = 0; p < m.length; p++) {
      const k = p * 4;
      const d = Math.max(Math.abs(img.data[k]! - blank.data[k]!), Math.abs(img.data[k + 1]! - blank.data[k + 1]!), Math.abs(img.data[k + 2]! - blank.data[k + 2]!));
      if (d > TOLERANCE) {
        m[p] = 1;
        n++;
      }
    }
    masks.push({ m, n });
  }
  await page.evaluate(() => {
    (window as unknown as { __hdrItems: HTMLElement[] }).__hdrItems.forEach((el) => {
      el.style.visibility = '';
    });
  });
  const overdrawPairs: DrawnOverlap[] = [];
  for (let i = 0; i < masks.length; i++) {
    for (let j = i + 1; j < masks.length; j++) {
      let c = 0;
      const a = masks[i]!.m;
      const b = masks[j]!.m;
      for (let p = 0; p < a.length; p++) if (a[p] && b[p]) c++;
      if (c > 0) overdrawPairs.push({ a: names[i]!, b: names[j]!, sharedDrawnPixels: c });
    }
  }
  return { drawn: Object.fromEntries(names.map((n, i) => [n, masks[i]!.n])), overdrawPairs };
}

// ---------------------------------------------------------------------------------------------------------------------------------------------------------
// The controls: a synthetic header with the real class names, in variants that each carry an expectation the instrument must meet.

/* eslint-disable pixel-agents/no-inline-colors -- the controls need pixels that DIFFER: synthetic swatches on a synthetic page, not product colours */
const HDR = (parts: { crumb?: string; tabs?: string }): string => `<!doctype html><meta charset="utf-8"><style>
  body{margin:0;font:14px/1.2 sans-serif;background:#fff;color:#000} .di-scan-stage{position:relative;width:900px;height:200px}
  .di-scan-stage-bar{display:flex;align-items:center;gap:12px;padding:10px 12px;width:876px;position:relative}
  .di-scan-back{width:30px;height:24px;flex-shrink:0} .di-scan-mark{display:inline-block;width:30px;height:24px;background:#789;flex-shrink:0}
  .di-scan-crumb{display:flex;align-items:center;gap:8px;min-width:0} .di-scan-crumb>*{flex-shrink:0;white-space:nowrap}
  .di-scan-crumb>button{border:0;background:#cde;padding:0;font:inherit} .di-scan-siblings{display:flex;gap:4px;margin-left:auto;flex-shrink:0}
  .di-scan-sibling{white-space:nowrap;height:24px;padding:0 8px;border:0;background:#dec;font:inherit}
  .sr{position:absolute;width:1px;height:1px;overflow:hidden;clip:rect(0,0,0,0);white-space:nowrap}
  .srx{position:absolute;clip:rect(0,0,0,0);white-space:nowrap}
  .di-scan-header-tools{display:flex;gap:6px;flex-shrink:0} .tool{width:24px;height:24px;border:0;background:#ecd}
</style><div class="di-scan-stage"><header class="di-scan-stage-bar">
  <button class="di-scan-back">B</button><span class="di-scan-mark"></span>
  <nav class="di-scan-crumb" aria-label="Location">${parts.crumb ?? '<button>World</button><span>/</span><button>Open</button><span>/</span><button class="di-scan-crumb-title">Title</button><span>/</span><span class="di-scan-crumb-current">Leaf</span>'}</nav>
  <nav class="di-scan-siblings">${parts.tabs ?? '<button class="di-scan-sibling" data-testid="di-scan-sibling-diff">Code Diff</button><button class="di-scan-sibling" data-testid="di-scan-sibling-herdr">Herdr</button><span class="sr">Herdr opens in your own terminal. That is not connected in this layout preview.</span><button class="di-scan-sibling" data-testid="di-scan-sibling-comments">Comments</button>'}</nav>
  <div class="di-scan-header-tools"><button class="tool"></button><button class="tool"></button></div>
</header></div>`;

const CRUMB_DEFAULT_TAIL = '<span>/</span><button>Open</button><span>/</span><button class="di-scan-crumb-title">Title</button><span>/</span><span class="di-scan-crumb-current">Leaf</span>';
const LEAF_FAR = '.di-scan-crumb-current{position:relative;left:400px;background:#fcc}';

export interface Control {
  id: string;
  html: string;
  pixel?: boolean;
  expect: (m: HeaderMeasure, p: { overdrawPairs: DrawnOverlap[] }) => boolean;
}
export const headerControls: Control[] = [
  {
    id: 'N1 no overlap, a visually hidden sibling description present',
    html: HDR({}),
    pixel: true,
    expect: (m, p) => m.overlaps.length === 0 && m.clipped.length === 0 && m.boxes.every((b) => b.w > 1.5) && p.overdrawPairs.length === 0,
  },
  {
    id: 'N2 an overflow:hidden item with a visually hidden LONG-TEXT child that is NOT clipped reads as not clipped',
    html: HDR({
      crumb: `<button style="position:relative;overflow:hidden;text-overflow:ellipsis;width:60px">World<span class="srx">a very long visually hidden description that is wider than the button itself and must not count</span></button>${CRUMB_DEFAULT_TAIL}`,
    }),
    expect: (m) => !m.clipped.includes('crumb-world'),
  },
  {
    id: 'P1 an overflow:hidden item whose OWN text is wider than its box reads as clipped',
    html: HDR({ crumb: `<button style="width:20px;overflow:hidden;text-overflow:ellipsis">World</button>${CRUMB_DEFAULT_TAIL}` }),
    expect: (m) => m.clipped.includes('crumb-world'),
  },
  {
    id: 'P2 an overflow:visible tab whose text is drawn beyond its box reads as clipped',
    html: HDR({
      tabs: '<button class="di-scan-sibling" data-testid="di-scan-sibling-diff" style="width:30px;padding:0;overflow:visible">Code Diff</button><button class="di-scan-sibling" data-testid="di-scan-sibling-herdr">Herdr</button><button class="di-scan-sibling" data-testid="di-scan-sibling-comments">Comments</button>',
    }),
    expect: (m) => m.clipped.includes('tab-diff'),
  },
  {
    id: 'P3 a leaf that runs over a tab reads as overlapping by boxes AND by drawn pixels',
    html: HDR({}).replace('</style>', `${LEAF_FAR}</style>`),
    pixel: true,
    expect: (m, p) =>
      m.overlaps.some((o) => (o.a === 'crumb-leaf' && o.b.startsWith('tab-')) || (o.b === 'crumb-leaf' && o.a.startsWith('tab-'))) &&
      p.overdrawPairs.some((o) => o.a === 'crumb-leaf' || o.b === 'crumb-leaf'),
  },
  {
    id: 'P4 an overlap is still reported (boxes and pixels) when one of the two items also carries hidden text',
    html: HDR({
      tabs: '<button class="di-scan-sibling" data-testid="di-scan-sibling-diff" style="position:relative">Code Diff<span class="srx">hidden words on the overlapped tab</span></button><button class="di-scan-sibling" data-testid="di-scan-sibling-comments">Comments</button>',
    }).replace('</style>', `${LEAF_FAR}</style>`),
    pixel: true,
    expect: (m, p) => m.overlaps.some((o) => o.a === 'crumb-leaf' || o.b === 'crumb-leaf') && p.overdrawPairs.some((o) => o.a === 'crumb-leaf' || o.b === 'crumb-leaf'),
  },
  {
    id: 'P5 a FAINT but real overdraw (20 levels, above the tolerance) is still found by pixels',
    html: HDR({}).replace('</style>', '.di-scan-crumb-current{position:relative;left:400px;background:#ebebeb}.di-scan-sibling{background:#ebebeb}</style>'),
    pixel: true,
    expect: (_m, p) => p.overdrawPairs.some((o) => o.a === 'crumb-leaf' || o.b === 'crumb-leaf'),
  },
  {
    id: 'P6 a display:none leaf is reported as OMITTED (never silent), is not in the boxes or off-bar, and is NOT in the Location landmark',
    html: HDR({}).replace('</style>', '.di-scan-crumb-current,.di-scan-crumb>span:nth-child(6){display:none}</style>'),
    expect: (m) => m.omitted.includes('crumb-leaf') && !m.boxes.some((b) => b.name === 'crumb-leaf') && m.offBar.length === 0 && !/Leaf/.test(m.locationAria) && /Title/.test(m.locationAria),
  },
  {
    id: 'P7 a visually hidden (1px, clipped) leaf is reported as VISUALLY HIDDEN, not omitted, and its text IS still in the Location landmark',
    html: HDR({}).replace('</style>', '.di-scan-crumb>:nth-child(n+6){position:absolute;width:1px;height:1px;margin:-1px;overflow:hidden;clip:rect(0,0,0,0);white-space:nowrap}</style>'),
    expect: (m) => m.visuallyHidden.includes('crumb-leaf') && !m.omitted.includes('crumb-leaf') && /Leaf/.test(m.locationAria),
  },
  {
    id: "N4 the Location landmark's accessible tree of a normal header lists every crumb, the leaf included",
    html: HDR({}),
    expect: (m) => /World/.test(m.locationAria) && /Open/.test(m.locationAria) && /Title/.test(m.locationAria) && /Leaf/.test(m.locationAria) && m.omitted.length === 0 && m.visuallyHidden.length === 0,
  },
  {
    id: 'N5 REQUIRED TABS: a normal header has all three required sibling tabs present, rendered and larger than 1.5 px',
    html: HDR({}),
    expect: (m) => tabsNotReachable(m).length === 0 && m.requiredTabs.length === 3,
  },
  {
    id: 'P8 REQUIRED TABS: a display:none Herdr tab is reported as not reachable by the INDEPENDENT collection (the item list, which filters tabs by box size, does NOT report it as omitted: the blind spot this control exists to show)',
    html: HDR({}).replace('</style>', '[data-testid="di-scan-sibling-herdr"]{display:none}</style>'),
    expect: (m) => tabsNotReachable(m).join() === 'herdr' && !m.omitted.includes('tab-herdr'),
  },
  {
    id: 'P9 REQUIRED TABS: a Comments tab collapsed to a visually hidden 1 px box is reported as not reachable (it is still in the accessibility tree, but nobody can see or press it)',
    html: HDR({}).replace('</style>', '[data-testid="di-scan-sibling-comments"]{position:absolute;width:1px;height:1px;overflow:hidden;clip:rect(0,0,0,0);padding:0}</style>'),
    expect: (m) => tabsNotReachable(m).join() === 'comments',
  },
  {
    id: 'P10 REQUIRED TABS: a Code Diff tab removed from the page is reported as not present',
    html: HDR({ tabs: '<button class="di-scan-sibling" data-testid="di-scan-sibling-herdr">Herdr</button><button class="di-scan-sibling" data-testid="di-scan-sibling-comments">Comments</button>' }),
    expect: (m) => tabsNotReachable(m).join() === 'diff' && m.requiredTabs.find((t) => t.id === 'diff')?.present === false,
  },
  {
    id: 'N3 items that only touch (integer widths, no gap, no negative margin) are not an overlap, by boxes or by pixels',
    html: HDR({
      crumb:
        '<button style="width:40px;height:20px">World</button><span style="display:inline-block;width:10px;height:20px;background:#aaa">/</span><button style="width:40px;height:20px">Open</button><span style="display:inline-block;width:10px;height:20px;background:#aaa">/</span><button class="di-scan-crumb-title" style="width:40px;height:20px">Title</button><span style="display:inline-block;width:10px;height:20px;background:#aaa">/</span><span class="di-scan-crumb-current" style="display:inline-block;width:40px;height:20px;background:#bbb">Leaf</span>',
    }).replace('.di-scan-crumb{display:flex;align-items:center;gap:8px', '.di-scan-crumb{display:flex;align-items:center;gap:0'),
    pixel: true,
    expect: (m, p) => m.overlaps.length === 0 && p.overdrawPairs.length === 0,
  },
];

/* eslint-enable pixel-agents/no-inline-colors */

export interface ControlResult {
  control: string;
  ok: boolean;
  why: string;
}
/** Run every control on a synthetic page; the instrument is trusted only if all of them read as expected. */
export async function runHeaderControls(browser: Browser): Promise<ControlResult[]> {
  const results: ControlResult[] = [];
  for (const c of headerControls) {
    const page = await browser.newPage({ viewport: { width: 1000, height: 300 } });
    try {
      await page.setContent(c.html);
      await page.evaluate(async () => {
        await document.fonts.ready;
      });
      const m = await measureHeader(page);
      const p = c.pixel ? await drawnOverlaps(page) : { drawn: {}, overdrawPairs: [] };
      results.push({ control: c.id, ok: c.expect(m, p), why: c.expect(m, p) ? '' : JSON.stringify({ overlaps: m.overlaps.length, clipped: m.clipped, omitted: m.omitted, visuallyHidden: m.visuallyHidden, pairs: p.overdrawPairs.length }) });
    } catch (e) {
      results.push({ control: c.id, ok: false, why: String(e).split('\n')[0] ?? '' });
    } finally {
      await page.close();
    }
  }
  return results;
}
