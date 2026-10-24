/**
 * Rendered text GEOMETRY of controls, measured in the browser. ONE bounded measurement contract, shared by every test that uses it.
 *
 * An accessible name, a focus ring and a bounding box can all be right while the words a person reads are not on the control at all:
 * a label laid out outside a clipped box has a name and a focus order and nothing to see. This measures where each piece of VISIBLE
 * TEXT is actually drawn (the client rects of its text nodes, by Range) against the control that carries it and against the region of
 * that control a person can actually see, so a label pushed outside its button, clipped by an overflow, or drawn off the window is
 * reported as a number, not guessed from the markup. It reports; the test asserts. It is a custom measurement, not an auditor.
 *
 * WHY THE CONTRACT IS SMALL: a measurement that claims more generality than it has fails in both directions. A check that passes
 * while the thing is broken hides a defect; a check that fails while the thing is fine manufactures invalid evidence that can drive a
 * wrong fix (a naive walk that intersected every ancestor's overflow clip reported a fixed strip as clipped by a wrapper that cannot
 * clip it). So this implements a SMALL geometry correctly and REFUSES the rest LOUDLY: it throws, it never returns a number for
 * geometry it cannot reason about.
 *
 * SUPPORTED (measured):
 *  - controls and ancestors that are in-flow (static or relative), or position: absolute, or position: fixed whose containing block is
 *    the viewport (no ancestor creates a containing block for fixed descendants);
 *  - a clip from `overflow` (hidden, clip, auto, scroll) on the control itself or on an ancestor that is in the control's
 *    CONTAINING-BLOCK chain, modelled as the ancestor's padding box (rectangular), then the window;
 *  - text positioned by writing-mode (vertical text is measured by its real rects).
 * UNSUPPORTED (throws `textFit UNSUPPORTED geometry: ...`), on the control or any ancestor:
 *  - transform, perspective, filter, backdrop-filter, contain, container-type, or a will-change of those (they move or clip content
 *    or establish a containing block for fixed descendants, so position and clip can no longer be read from rects alone);
 *  - clip-path and mask (non-rectangular clips);
 *  - position: sticky;
 *  - a control inside a shadow root;
 *  - a rounded clip (border-radius on a clipping box) whose corner a text rect reaches (the corner cut is not modelled).
 */
import type { Page } from 'playwright-core';

export interface TextRect {
  text: string;
  left: number;
  right: number;
  top: number;
  bottom: number;
  width: number;
  height: number;
}

export interface TextFitControl {
  id: string;
  box: { left: number; right: number; top: number; bottom: number; width: number; height: number };
  /** Every visible text node of the control with its drawn rect (aria-hidden glyphs included: they are seen). */
  texts: TextRect[];
  /** True when the control's own overflow clips its content (scroll size beyond client size). */
  clipsContent: boolean;
  /** Computed writing-mode of the control (the invariant: the flex container stays horizontal; only the label is vertical). */
  controlWritingMode: string;
  /**
   * Pixels of each text rect that lie OUTSIDE the control's VISIBLE REGION, per side (0 when inside, within tolerance). The visible
   * region is the control's padding box, narrowed by the padding box of every clipping ancestor in its containing-block chain and by
   * the window.
   */
  outside: Array<{ text: string; left: number; right: number; top: number; bottom: number }>;
  /** Text whose rect centre is NOT painted by the control: a hit test there reaches something that is not the control or part of it. */
  textNotHit: string[];
  /** Pixels of the control's box outside the window. */
  controlOffWindow: { left: number; right: number; top: number; bottom: number };
  /** What a click at the middle of the control reaches: the control itself (or inside it), or something else. */
  hitsItself: boolean;
}

export interface TextFit {
  controls: TextFitControl[];
  viewport: { width: number; height: number };
}

/** Measure every visible control under `rootSelector` that matches `controlSelector` (default: buttons, links and summaries). */
export async function textFit(
  page: Page,
  rootSelector: string,
  controlSelector = 'button, a[href], summary, [role="button"]',
  tolerance = 0.75,
): Promise<TextFit> {
  return page.evaluate(
    ([root, selector, tol]) => {
      const out: TextFit = { controls: [], viewport: { width: window.innerWidth, height: window.innerHeight } };
      const idOf = (el: Element) => el.getAttribute('data-testid') ?? `${el.tagName.toLowerCase()} "${(el.textContent ?? '').trim().slice(0, 30)}"`;
      const refuse = (reason: string, control: Element, at: Element): never => {
        throw new Error(`textFit UNSUPPORTED geometry: ${reason} on ${idOf(at)} (control ${idOf(control)}); this measurement refuses what it cannot reason about`);
      };
      /** Refuse any property on the element or an ancestor that this contract does not model. */
      const checkSupported = (control: HTMLElement) => {
        for (let n: HTMLElement | null = control; n; n = n.parentElement) {
          const c = getComputedStyle(n);
          if (c.display === 'contents') continue;
          if (c.transform !== 'none') refuse('transform', control, n);
          if (c.perspective !== 'none') refuse('perspective', control, n);
          if (c.filter !== 'none') refuse('filter', control, n);
          if (c.backdropFilter !== undefined && c.backdropFilter !== 'none') refuse('backdrop-filter', control, n);
          if (/paint|layout|strict|content/.test(c.contain)) refuse('contain', control, n);
          if (c.containerType !== undefined && c.containerType !== 'normal') refuse('container-type', control, n);
          if (/transform|perspective|filter|contain|clip-path/.test(c.willChange)) refuse('will-change', control, n);
          if (c.clipPath !== 'none') refuse('clip-path', control, n);
          if (c.maskImage !== undefined && c.maskImage !== 'none') refuse('mask', control, n);
          if (c.position === 'sticky') refuse('position: sticky', control, n);
        }
        if (control.getRootNode() !== document) refuse('a control inside a shadow root', control, control);
      };
      const padBox = (e: HTMLElement) => {
        const r = e.getBoundingClientRect();
        return { left: r.left + e.clientLeft, top: r.top + e.clientTop, right: r.left + e.clientLeft + e.clientWidth, bottom: r.top + e.clientTop + e.clientHeight };
      };
      /** The next box in the containing-block chain (display: contents has no box and is skipped). With nothing that establishes a
       *  containing block for fixed descendants (refused above), a fixed element's block is the viewport: the chain ends. */
      const blockOf = (e: HTMLElement): HTMLElement | null => {
        const pos = getComputedStyle(e).position;
        if (pos === 'fixed') return null;
        const skip = (a: HTMLElement) => getComputedStyle(a).display === 'contents';
        if (pos === 'absolute') {
          for (let a = e.parentElement; a; a = a.parentElement) if (!skip(a) && getComputedStyle(a).position !== 'static') return a;
          return null;
        }
        for (let a = e.parentElement; a; a = a.parentElement) if (!skip(a)) return a;
        return null;
      };
      const clips = (e: HTMLElement) => {
        const c = getComputedStyle(e);
        return c.overflowX !== 'visible' || c.overflowY !== 'visible';
      };
      const radiusOf = (e: HTMLElement) => {
        const c = getComputedStyle(e);
        return Math.max(...[c.borderTopLeftRadius, c.borderTopRightRadius, c.borderBottomLeftRadius, c.borderBottomRightRadius].map((v) => parseFloat(v) || 0));
      };

      const roots = Array.from(document.querySelectorAll<HTMLElement>(root as string));
      const seen = new Set<Element>();
      for (const r of roots) {
        const controls = [r, ...Array.from(r.querySelectorAll<HTMLElement>('*'))].filter((el) => el.matches(selector as string));
        for (const el of controls) {
          if (seen.has(el)) continue;
          seen.add(el);
          if (!el.checkVisibility({ checkVisibilityCSS: true }) || el.getClientRects().length === 0) continue;
          checkSupported(el);
          const b = el.getBoundingClientRect();
          const texts: TextRect[] = [];
          const walker = document.createTreeWalker(el, NodeFilter.SHOW_TEXT);
          for (let n = walker.nextNode(); n; n = walker.nextNode()) {
            const content = (n.textContent ?? '').replace(/\s+/g, ' ').trim();
            const parent = n.parentElement;
            if (content === '' || !parent || !parent.checkVisibility({ checkVisibilityCSS: true })) continue;
            const range = document.createRange();
            range.selectNodeContents(n);
            for (const rect of Array.from(range.getClientRects())) {
              if (rect.width === 0 && rect.height === 0) continue;
              texts.push({ text: content, left: rect.left, right: rect.right, top: rect.top, bottom: rect.bottom, width: rect.width, height: rect.height });
            }
          }
          const over = (a: number) => (a > (tol as number) ? Math.round(a * 10) / 10 : 0);
          // the visible region: the control's own padding box if it clips, narrowed by each clipping box in its containing-block chain, and the window
          const region = padBox(el); // the text must lie inside the control's own padding box, whether or not the control itself clips
          const clipBoxes: HTMLElement[] = [];
          if (clips(el)) clipBoxes.push(el); // its rounded corners (if any) matter even though its padding box is already the region
          for (let a = blockOf(el); a; a = blockOf(a)) if (clips(a)) clipBoxes.push(a);
          for (const a of clipBoxes) {
            const p = padBox(a);
            region.left = Math.max(region.left, p.left);
            region.top = Math.max(region.top, p.top);
            region.right = Math.min(region.right, p.right);
            region.bottom = Math.min(region.bottom, p.bottom);
            const radius = radiusOf(a);
            if (radius > 0)
              for (const t of texts) {
                const inX = (x0: number, x1: number) => t.right > x0 && t.left < x1;
                const inY = (y0: number, y1: number) => t.bottom > y0 && t.top < y1;
                if (
                  (inX(p.left, p.left + radius) && inY(p.top, p.top + radius)) ||
                  (inX(p.right - radius, p.right) && inY(p.top, p.top + radius)) ||
                  (inX(p.left, p.left + radius) && inY(p.bottom - radius, p.bottom)) ||
                  (inX(p.right - radius, p.right) && inY(p.bottom - radius, p.bottom))
                )
                  refuse(`a rounded clip (radius ${radius}px) whose corner the text "${t.text}" reaches`, el, a);
              }
          }
          region.left = Math.max(region.left, 0);
          region.top = Math.max(region.top, 0);
          region.right = Math.min(region.right, window.innerWidth);
          region.bottom = Math.min(region.bottom, window.innerHeight);
          const outside = texts
            .map((t) => ({ text: t.text, left: over(region.left - t.left), right: over(t.right - region.right), top: over(region.top - t.top), bottom: over(t.bottom - region.bottom) }))
            .filter((o) => o.left || o.right || o.top || o.bottom);
          const textNotHit = texts
            .filter((t) => {
              const hit = document.elementFromPoint(t.left + t.width / 2, t.top + t.height / 2);
              return !(hit !== null && (hit === el || el.contains(hit)));
            })
            .map((t) => t.text);
          const mid = document.elementFromPoint(b.left + b.width / 2, b.top + b.height / 2);
          out.controls.push({
            id: idOf(el),
            box: { left: b.left, right: b.right, top: b.top, bottom: b.bottom, width: b.width, height: b.height },
            texts,
            clipsContent: el.scrollWidth > el.clientWidth + 1 || el.scrollHeight > el.clientHeight + 1,
            controlWritingMode: getComputedStyle(el).writingMode,
            outside,
            textNotHit,
            controlOffWindow: {
              left: over(0 - b.left),
              right: over(b.right - window.innerWidth),
              top: over(0 - b.top),
              bottom: over(b.bottom - window.innerHeight),
            },
            hitsItself: mid !== null && (mid === el || el.contains(mid)),
          });
        }
      }
      return out;
    },
    [rootSelector, controlSelector, tolerance] as const,
  );
}
