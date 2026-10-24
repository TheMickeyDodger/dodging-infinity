/**
 * TAB TRAVERSAL, driven by the real keyboard: every stop in the order the keyboard reaches it, and for each stop whether a person can SEE where
 * focus is.
 *
 * A box check or a screenshot cannot establish this. `overflow: hidden` clips a control visually and leaves it focusable; so does a
 * zero-size container, and so does `opacity: 0`. Only `display: none`, `visibility: hidden`, `inert` or not rendering it take a control out of
 * the tab sequence. A control that is clipped, covered or transparent but still in the sequence is an INVISIBLE TAB STOP: focus lands on
 * something nobody can see. So this presses Tab, reads what holds focus, and measures that stop three ways:
 *   1. ON-SCREEN AREA: how much of its box lies inside the window.
 *   2. DRAWN WHERE IT SITS: a 5 x 5 grid of points over the on-screen part is hit-tested (`document.elementFromPoint`, with `pointer-events`
 *      neutralized for the probe so a pointer-events:none overlay is seen too); the fraction that lands on the control (or inside it) is
 *      reported. Clipping by an ancestor and covering by another element both show up as a low fraction.
 *   3. THE INDICATOR PAINTS PIXELS: a screenshot of the control's surroundings while it holds focus is compared with one after it is blurred.
 *      Identical pixels mean that nothing was drawn to show focus (outline:none with no replacement, an indicator in the same colour as
 *      its background, or the whole control transparent). The world canvas animates, so it is hidden for the comparison: this proves that
 *      the indicator paints something on screen, NOT that it has enough contrast (contrast is the AC6 sweep's concern).
 * It reports; the test asserts. A custom measurement, not an auditor and not a screen reader.
 * The `id` it reports is a TRAVERSAL IDENTIFIER (data-testid, else aria-label, else the first words of the text, else the tag). It is NOT the
 * accessible-name algorithm and it IGNORES `title`, so an id that falls through to the tag (for example 'button') says nothing about whether the
 * control has an accessible name; the browser's own computed name must be asked for separately (CDP Accessibility tree).
 *
 * `backward: true` walks the sequence the other way with Shift+Tab from the END of the document (containment usually leaks backwards first: wrapping
 * off the FIRST element is the case people forget). Stops are numbered in the order they were visited.
 *
 * Traversal ends when focus leaves the document after the last stop (`left-document`, the usual complete end), returns to a stop already seen
 * (`cycled`, also complete), or `max` stops were taken (`max`: INCOMPLETE, reported, never silently treated as complete). Focus is restored to each stop after its pixel comparison so the sequence is the
 * real one.
 */
import type { Page } from 'playwright-core';

import { PAINTS_AT_SOURCE } from './diPaintsAt.ts';

export interface TabStop {
  index: number;
  /** The first named region (see `regions`) that contains this stop, else null. */
  region: string | null;
  /** data-testid, else aria-label, else the first words of its text, else the tag. */
  id: string;
  box: { left: number; top: number; right: number; bottom: number };
  /** Area (px squared) of the box that lies inside the window. */
  onScreenPx: number;
  /** Points hit-tested over the on-screen part (0 when none of it is on screen). */
  sampled: number;
  /** Of those, how many land on the control itself or inside it. */
  hitSamples: number;
  /** What is drawn there instead (data-testid, else tag and first class), at most three distinct, for the points that miss. */
  coveredBy: string[];
  indicator: {
    outline: string;
    boxShadow: string;
    /** Whether blurring the control changed any pixel around it; null when none of it is on screen (nothing to compare). */
    pixelsChanged: boolean | null;
  };
}

export interface TabTraversal {
  stops: TabStop[];
  ended: 'cycled' | 'left-document' | 'max';
}

export interface TabProblem {
  index: number;
  id: string;
  why: string[];
}

/**
 * What is wrong with each stop that a person could not see: an invisible tab stop has at least one reason.
 * `minHitFraction` is the least fraction of the hit-test grid that must land on the control: one number, or a number per region (key `default`
 * for the rest). WCAG 2.2 SC 2.4.11 (AA) asks that a focused control is not ENTIRELY hidden; 1 is the strict bar for controls this work owns.
 */
export function tabProblems(
  traversal: TabTraversal,
  minHitFraction: number | Record<string, number> = 0.8,
): TabProblem[] {
  const out: TabProblem[] = [];
  for (const s of traversal.stops) {
    const need =
      typeof minHitFraction === 'number'
        ? minHitFraction
        : (minHitFraction[s.region ?? 'default'] ?? minHitFraction.default ?? 0.8);
    const why: string[] = [];
    if (s.onScreenPx <= 0) why.push('none of its box is inside the window');
    else if (s.sampled > 0 && s.hitSamples === 0)
      why.push('nothing of it is drawn where it sits (clipped away or covered)');
    else if (s.sampled > 0 && s.hitSamples / s.sampled < need)
      why.push(
        `only ${s.hitSamples} of ${s.sampled} points over it land on it (partly clipped or covered; drawn there instead: ${s.coveredBy.join(', ') || 'nothing'})`,
      );
    if (s.indicator.pixelsChanged === false)
      why.push('focusing it paints no pixel around it (no visible focus indicator)');
    if (why.length > 0) out.push({ index: s.index, id: s.id, why });
  }
  return out;
}

/** Press Tab from the start of the document and measure every stop. */
export async function tabTraversal(
  page: Page,
  opts: { max?: number; regions?: Record<string, string>; backward?: boolean } = {},
): Promise<TabTraversal> {
  const max = opts.max ?? 120;
  const regions = Object.entries(opts.regions ?? {});
  const backward = opts.backward === true;
  await page.evaluate((fromEnd) => {
    const probe = document.createElement('style');
    probe.setAttribute('data-tab-probe', 'true');
    // the canvas animates (it would fake a pixel change); pointer-events is neutralized so an overlay that ignores the pointer is still seen
    probe.textContent =
      'canvas { visibility: hidden !important; } *, *::before, *::after { pointer-events: auto !important; }';
    document.head.append(probe);
    document.querySelectorAll('[data-tab-seen]').forEach((e) => e.removeAttribute('data-tab-seen'));
    // Start from the START OF THE DOCUMENT, wherever focus was: the browser's sequential-focus starting point stays where the last focus
    // or click was (after clicking a control that then unmounts it sits at the end), and Tab from there would find nothing. A focusable-by-
    // script sentinel at the very start of the body puts the starting point there; it is not a tab stop (tabindex -1) and is removed afterwards.
    const sentinel = document.createElement('span');
    sentinel.setAttribute('data-tab-probe', 'true');
    sentinel.tabIndex = -1;
    if (fromEnd) document.body.append(sentinel);
    else document.body.prepend(sentinel);
    sentinel.focus();
  }, backward);
  const stops: TabStop[] = [];
  let ended: TabTraversal['ended'] = 'max';
  try {
    for (let i = 0; i < max; i += 1) {
      await page.keyboard.press(backward ? 'Shift+Tab' : 'Tab');
      const d = await page.evaluate(
        ({ named, paintsAtSource }) => {
          // an element that paints nothing at a point (a transparent layout container, forced hit-testable by the probe style) is looked THROUGH: a forced hit is not painted obstruction
          const paintsAt = new Function(`return (${paintsAtSource})`)() as (
            e: Element,
            x: number,
            y: number,
          ) => boolean;
          const el = document.activeElement as HTMLElement | null;
          if (el === null || el === document.body || el === document.documentElement)
            return { left: true as const };
          if (el.hasAttribute('data-tab-seen')) return { cycled: true as const };
          el.setAttribute('data-tab-seen', 'true');
          const vw = window.innerWidth;
          const vh = window.innerHeight;
          const r = el.getBoundingClientRect();
          const x0 = Math.max(0, r.left);
          const x1 = Math.min(vw, r.right);
          const y0 = Math.max(0, r.top);
          const y1 = Math.min(vh, r.bottom);
          const onScreen = x1 > x0 && y1 > y0;
          let sampled = 0;
          let hit = 0;
          const covers = new Set<string>();
          // INERT content is excluded from hit-testing whatever pointer-events says, so inert but painted content would be invisible to the stack: make it hit-testable ONLY for this
          // synchronous sampling (no Tab happens inside it, so the sequential focus order is never affected) and restore each attribute exactly. The restoration is in a finally in THIS
          // SAME evaluate, so a throw in the sampling (paintsAt, elementsFromPoint) cannot leave the page un-inert for the rest of the run and every later keyboard event. The focused stop is
          // never inside inert content.
          const lifted = Array.from(document.querySelectorAll('[inert]')).map(
            (n) => [n, n.getAttribute('inert') ?? ''] as const,
          );
          try {
            for (const [n] of lifted) n.removeAttribute('inert');
            if (onScreen) {
              for (const fy of [0.1, 0.3, 0.5, 0.7, 0.9])
                for (const fx of [0.1, 0.3, 0.5, 0.7, 0.9]) {
                  sampled += 1;
                  const px = x0 + (x1 - x0) * fx;
                  const py = y0 + (y1 - y0) * fy;
                  // the stack at the point, top first: the first element that is the control (or inside it) is a hit; the first that PAINTS something there and is not the control covers it
                  let top: Element | null = null;
                  for (const e of document.elementsFromPoint(px, py)) {
                    if (el.contains(e) || paintsAt(e, px, py)) {
                      top = e;
                      break;
                    }
                  }
                  if (top !== null && el.contains(top)) hit += 1;
                  else if (top !== null && covers.size < 3)
                    covers.add(
                      top.getAttribute('data-testid') ??
                        `${top.tagName.toLowerCase()}.${String(top.getAttribute('class') ?? '').split(' ')[0]}`,
                    );
                }
            }
          } finally {
            for (const [n, v] of lifted) n.setAttribute('inert', v);
          }
          const c = getComputedStyle(el);
          const region = named.find(([, selector]) => el.closest(selector) !== null)?.[0] ?? null;
          const label =
            el.getAttribute('data-testid') ??
            el.getAttribute('aria-label') ??
            (el.textContent ?? '').replace(/\s+/g, ' ').trim().slice(0, 40);
          return {
            region,
            id: label !== '' ? label : el.tagName.toLowerCase(),
            box: { left: r.left, top: r.top, right: r.right, bottom: r.bottom },
            onScreenPx: onScreen ? (x1 - x0) * (y1 - y0) : 0,
            sampled,
            hit,
            coveredBy: [...covers],
            outline: `${c.outlineStyle} ${c.outlineWidth} ${c.outlineColor}`,
            boxShadow: c.boxShadow,
          };
        },
        { named: regions, paintsAtSource: PAINTS_AT_SOURCE },
      );
      if ('left' in d) {
        ended = 'left-document';
        break;
      }
      if ('cycled' in d) {
        ended = 'cycled';
        break;
      }
      // the focus indicator must paint something on screen: compare the surroundings focused and blurred
      let pixelsChanged: boolean | null = null;
      if (d.onScreenPx > 0) {
        const pad = 6;
        const vp = page.viewportSize() ?? { width: 0, height: 0 };
        const x = Math.max(0, Math.floor(d.box.left - pad));
        const y = Math.max(0, Math.floor(d.box.top - pad));
        const w = Math.min(vp.width, Math.ceil(d.box.right + pad)) - x;
        const h = Math.min(vp.height, Math.ceil(d.box.bottom + pad)) - y;
        if (w > 0 && h > 0) {
          const clip = { x, y, width: w, height: h };
          const focused = await page.screenshot({ clip, animations: 'disabled' });
          const handle = await page.evaluateHandle(() => document.activeElement);
          await handle.evaluate((el) => (el as HTMLElement).blur());
          const blurred = await page.screenshot({ clip, animations: 'disabled' });
          await handle.evaluate((el) => (el as HTMLElement).focus());
          await handle.dispose();
          pixelsChanged = Buffer.compare(focused, blurred) !== 0;
        }
      }
      stops.push({
        index: stops.length + 1,
        region: d.region,
        id: d.id,
        box: d.box,
        onScreenPx: Math.round(d.onScreenPx),
        sampled: d.sampled,
        hitSamples: d.hit,
        coveredBy: d.coveredBy,
        indicator: { outline: d.outline, boxShadow: d.boxShadow, pixelsChanged },
      });
    }
  } finally {
    await page.evaluate(() => {
      document.querySelectorAll('[data-tab-probe]').forEach((e) => e.remove());
      document
        .querySelectorAll('[data-tab-seen]')
        .forEach((e) => e.removeAttribute('data-tab-seen'));
    });
  }
  return { stops, ended };
}
