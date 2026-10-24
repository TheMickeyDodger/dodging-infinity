/**
 * HORIZONTAL REACH of reading and control content, measured in the browser (WCAG 2.2 SC 1.4.10 Reflow: no loss of information or
 * two-dimensional scrolling at 320 CSS px). Its own bounded contract, separate from the strip's textFit contract, with the divergence
 * stated: reflow concerns only the HORIZONTAL axis, so this measures where content sits against the window's left and right edges and
 * which containers scroll or clip sideways; it makes no claim about vertical clipping (vertical scrolling is allowed by the standard).
 * That is why it can support something textFit refuses: a VERTICAL fade mask (`mask-image: linear-gradient(to bottom, ...)`, as the
 * office panel's scroll region has) fades the last band of a scroll region and can neither move content sideways nor hide it sideways.
 *
 * It reports; the test asserts. A custom measurement, not an auditor. It does not look at occlusion (see diOcclusion.ts) or at the world
 * canvas (which is exempt from reflow as content needing two-dimensional layout; report it separately).
 *
 * MEASURED, for visible content under a root: each control's box and each visible text rect against 0 and the window width (the part
 * that is off the window's left edge can never be scrolled to); containers that scroll sideways (overflow-x auto or scroll) or clip
 * sideways (overflow-x hidden or clip) with content wider than their box; whether the page itself scrolls sideways. Screen-reader-only
 * text (visually hidden: a box of at most 1 px) is excluded; it is not visible content.
 * UNSUPPORTED (throws `reflowReach UNSUPPORTED geometry: ...`) on the root or any ancestor: transform, perspective, clip-path, a mask that
 * is not a single vertical linear gradient (Chrome's computed value omits the default direction, which is vertical), and a root inside a shadow root.
 */
import type { Page } from 'playwright-core';

export interface ReflowOut {
  id: string;
  leftOutPx: number;
  rightOutPx: number;
}

export interface ReflowReport {
  viewport: { width: number; height: number };
  root: { left: number; right: number; width: number } | null;
  pageHorizontalScroll: boolean;
  controls: number;
  controlsOut: ReflowOut[];
  texts: number;
  textsOut: ReflowOut[];
  /** Containers under the root whose visible content needs sideways scrolling, or is clipped sideways. */
  horizontalContainers: Array<{ id: string; kind: 'scrolls' | 'clips'; byPx: number }>;
}

/** Measure the horizontal reach of everything visible under the first element matching `rootSelector`. */
export async function reflowReach(page: Page, rootSelector: string, tolerance = 0.75): Promise<ReflowReport> {
  return page.evaluate(
    ([selector, tol]) => {
      const vw = window.innerWidth;
      const idOf = (el: Element) => el.getAttribute('data-testid') ?? `${el.tagName.toLowerCase()}.${String(el.getAttribute('class') ?? '').split(' ')[0]}`;
      const root = document.querySelector<HTMLElement>(selector as string);
      const empty: ReflowReport = { viewport: { width: vw, height: window.innerHeight }, root: null, pageHorizontalScroll: false, controls: 0, controlsOut: [], texts: 0, textsOut: [], horizontalContainers: [] };
      if (!root) return empty;
      /**
       * A single linear gradient that runs along the vertical axis. Chrome's COMPUTED value drops the default direction (to bottom), so a
       * gradient with no direction is vertical; an explicit direction must be to bottom, to top, 0deg or 180deg. Several layers, radial,
       * conic and image masks are not vertical fades and are refused.
       */
      const verticalFade = (m: string): boolean => {
        if (!m.startsWith('linear-gradient(') || /radial-gradient|conic-gradient|url\(|\), *(repeating-)?linear-gradient\(/.test(m)) return false;
        const first = m.slice('linear-gradient('.length).trimStart();
        if (/^to\s/.test(first)) return /^to\s+(bottom|top)\b/.test(first);
        const angle = /^(-?[\d.]+)(deg|turn|rad|grad)\b/.exec(first);
        if (angle) return angle[2] === 'deg' && [0, 180, -180, 360].includes(Number(angle[1]));
        return true;
      };
      const refuse = (reason: string, at: Element): never => {
        throw new Error(`reflowReach UNSUPPORTED geometry: ${reason} on ${idOf(at)}; this measurement refuses what it cannot reason about`);
      };
      for (let n: HTMLElement | null = root; n; n = n.parentElement) {
        const c = getComputedStyle(n);
        if (c.display === 'contents') continue;
        if (c.transform !== 'none') refuse('transform', n);
        if (c.perspective !== 'none') refuse('perspective', n);
        if (c.clipPath !== 'none') refuse('clip-path', n);
        const mask = c.maskImage;
        if (mask !== undefined && mask !== 'none' && !verticalFade(mask)) refuse('a mask that is not a vertical linear gradient', n);
      }
      if (root.getRootNode() !== document) refuse('a root inside a shadow root', root);
      const hidden = (e: HTMLElement) => e.clientWidth <= 1 && e.clientHeight <= 1; // visually hidden (screen-reader-only) box
      const inHidden = (e: Element) => {
        for (let a: Element | null = e; a && a !== root.parentElement; a = a.parentElement) if (a instanceof HTMLElement && hidden(a)) return true;
        return false;
      };
      const shown = (e: HTMLElement) => e.checkVisibility({ checkVisibilityCSS: true }) && e.getClientRects().length > 0 && !inHidden(e);
      const over = (a: number) => (a > (tol as number) ? Math.round(a * 10) / 10 : 0);
      const doc = document.scrollingElement as Element;
      const rb = root.getBoundingClientRect();
      const out: ReflowReport = { ...empty, root: { left: Math.round(rb.left), right: Math.round(rb.right), width: Math.round(rb.width) }, pageHorizontalScroll: doc.scrollWidth > doc.clientWidth + 1 };
      const controls = Array.from(root.querySelectorAll<HTMLElement>('button, a[href], input, select, textarea, summary, [role="button"], [role="tab"]')).filter(shown);
      out.controls = controls.length;
      for (const c of controls) {
        const r = c.getBoundingClientRect();
        const l = over(0 - r.left);
        const rt = over(r.right - vw);
        if (l || rt) out.controlsOut.push({ id: idOf(c), leftOutPx: l, rightOutPx: rt });
      }
      const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
      for (let n = walker.nextNode(); n; n = walker.nextNode()) {
        const p = n.parentElement;
        const text = (n.textContent ?? '').replace(/\s+/g, ' ').trim();
        if (!p || text === '' || !shown(p)) continue;
        const range = document.createRange();
        range.selectNodeContents(n);
        for (const r of Array.from(range.getClientRects())) {
          if (r.width === 0) continue;
          out.texts += 1;
          const l = over(0 - r.left);
          const rt = over(r.right - vw);
          if (l || rt) out.textsOut.push({ id: text.slice(0, 40), leftOutPx: l, rightOutPx: rt });
        }
      }
      for (const e of [root, ...Array.from(root.querySelectorAll<HTMLElement>('*'))]) {
        if (!shown(e)) continue;
        const c = getComputedStyle(e);
        if (e.scrollWidth <= e.clientWidth + 1) continue;
        if (c.overflowX === 'auto' || c.overflowX === 'scroll') out.horizontalContainers.push({ id: idOf(e), kind: 'scrolls', byPx: e.scrollWidth - e.clientWidth });
        else if (c.overflowX === 'hidden' || c.overflowX === 'clip') out.horizontalContainers.push({ id: idOf(e), kind: 'clips', byPx: e.scrollWidth - e.clientWidth });
      }
      return out;
    },
    [rootSelector, tolerance] as const,
  );
}
