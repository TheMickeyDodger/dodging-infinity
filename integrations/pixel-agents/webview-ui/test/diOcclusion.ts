/**
 * OCCLUSION, measured directly: is the content a person is meant to read or operate actually the topmost thing drawn where it sits?
 *
 * Containment tests (a box inside the window, text inside its box) cannot see obstruction: every one of them passes while another element
 * is drawn on top of the text. This takes sample points across the VISIBLE text and the controls under a root and asks the browser what is
 * painted there (`document.elementFromPoint`), so an overlay over the content is reported with its identity, not guessed from rects.
 *
 * `pointer-events: none` overlays (the fixture banner, the ambient legend) are INVISIBLE to a plain hit test even while they cover text, so
 * for the duration of the probe every element is made hit-testable (a probe style sets `pointer-events: auto !important`), then removed.
 * `visibility: hidden` and `display: none` elements are not painted and are correctly never reported.
 *
 * CLIPPED-AWAY CONTENT IS NOT SAMPLED. `getClientRects` and `getBoundingClientRect` return geometry whatever an ancestor's `overflow` clips, so text
 * scrolled out of a scroll region still reports rects at coordinates where it is not painted, and a hit test there correctly returns whatever really is
 * painted there (a banner, say). Sampling those rects reported a banner "covering" text that was never drawn. So every sample is taken inside the part of
 * the rect that is actually VISIBLE: it is intersected, PER AXIS, with the padding box of every clipping element along the CONTAINING-BLOCK chain
 * (`overflow-x` clips the horizontal axis only, `overflow-y` the vertical only; an absolutely positioned box is clipped by its containing block's chain and a
 * `position: fixed` box escapes ancestors that are not a containing block for it, unless they carry a transform, filter, perspective or paint containment).
 * What is clipped away entirely is COUNTED and NAMED in the report, never silently dropped, so an omission is visible. The overlay detection is unchanged: every
 * element is made hit-testable, so a `pointer-events: none` overlay over VISIBLE content is still caught, and the callers' sample floors still apply (an empty
 * `covered` is not a visibility pass; it needs a floor and a positive companion). NOT modelled: `clip-path`, `mask`, `overflow-clip-margin`, and clipping by the window
 * itself (the window edge is handled separately below).
 *
 * AN ELEMENT THAT PAINTS NOTHING IS LOOKED THROUGH. Making every element hit-testable also returns transparent layout containers (the left strip's rail, grid and first-row cell:
 * pointer-events none, no background, no border), which cover nothing a person can see and let a real click through; a forced hit is not painted obstruction. So the stack at each
 * point is walked from the top and the sample is judged by the first element that is inside the root or that actually PAINTS something there (diPaintsAt.ts: its own background,
 * image, inset shadow, border band, text, replaced content or pseudo-element). A transparent container is never permission to ignore its painted subtree, and INERT content (excluded from
 * hit-testing whatever pointer-events says) is made hit-testable for the instant of sampling and restored. A banner or legend that ignores the pointer but paints is still caught, and the
 * callers' sample floors still apply. THE LIMIT, stated: this is a hit-stack model, not a paint oracle: see diPaintsAt.ts for what it does not model (outlines, outer shadows, clip-path, mask,
 * filters, canvas contents) and for the independent measure, decoded pixels, which it never replaces.
 *
 * It reports; the test asserts. A custom measurement, not an auditor. Points outside the window are not sampled (content that is cut off the
 * window is a different finding, measured by textFit).
 */
import type { Page } from 'playwright-core';

import { PAINTS_AT_SOURCE } from './diPaintsAt.ts';

export interface CoveredPoint {
  kind: 'text' | 'control';
  /** The words at that point (first 50 characters). */
  text: string;
  at: { x: number; y: number };
  /** What is painted on top there: its data-testid, else tag and first class. */
  by: string;
}

export interface OcclusionReport {
  /** Sample points taken (text rects and controls under the root that lie inside the window AND inside every clip that applies to them). */
  sampled: number;
  covered: CoveredPoint[];
  /** Items skipped because NONE of them is visible (clipped away by an ancestor, or only a sliver left): counted, never silently dropped. */
  clippedAway: number;
  /** The first 30 of them, named (a text's words or a control's identity), so the omission can be read. */
  clippedAwayItems: Array<{ kind: 'text' | 'control'; text: string }>;
}

/** Sample every visible text rect (left, centre, right) and every control centre under each element matching `rootSelector`. */
export async function occlusion(page: Page, rootSelector: string): Promise<OcclusionReport> {
  return page.evaluate(
    ({ selector, paintsAtSource }) => {
      const paintsAt = new Function(`return (${paintsAtSource})`)() as (
        e: Element,
        x: number,
        y: number,
      ) => boolean;
      const probe = document.createElement('style');
      probe.setAttribute('data-occlusion-probe', 'true');
      probe.textContent = '*, *::before, *::after { pointer-events: auto !important; }';
      document.head.append(probe);
      // INERT content is excluded from hit-testing whatever pointer-events says, so inert but PAINTED content (the camera controls while the City panel is open) would be invisible to the
      // hit stack: make it hit-testable for this synchronous probe and restore each attribute exactly afterwards
      const lifted = Array.from(document.querySelectorAll('[inert]')).map(
        (e) => [e, e.getAttribute('inert') ?? ''] as const,
      );
      try {
        for (const [e] of lifted) e.removeAttribute('inert');
        const out: {
          sampled: number;
          covered: Array<{
            kind: 'text' | 'control';
            text: string;
            at: { x: number; y: number };
            by: string;
          }>;
          clippedAway: number;
          clippedAwayItems: Array<{ kind: 'text' | 'control'; text: string }>;
        } = { sampled: 0, covered: [], clippedAway: 0, clippedAwayItems: [] };
        type Box = { left: number; top: number; right: number; bottom: number };
        const makesContainingBlock = (cs: CSSStyleDeclaration) =>
          cs.transform !== 'none' ||
          cs.perspective !== 'none' ||
          cs.filter !== 'none' ||
          /paint|layout|strict|content/.test(cs.contain) ||
          /transform|perspective|filter/.test(cs.willChange);
        // the element whose box contains this one for layout and clipping: the parent for in-flow boxes, the nearest positioned (or transformed) ancestor for an
        // absolutely positioned one, the nearest transformed ancestor or nothing (the viewport) for a fixed one
        const containingParent = (e: Element): Element | null => {
          const cs = getComputedStyle(e);
          if (cs.position === 'fixed') {
            for (let a = e.parentElement; a; a = a.parentElement)
              if (makesContainingBlock(getComputedStyle(a))) return a;
            return null;
          }
          if (cs.position === 'absolute') {
            for (let a = e.parentElement; a; a = a.parentElement) {
              const s = getComputedStyle(a);
              if (s.position !== 'static' || makesContainingBlock(s)) return a;
            }
            return null;
          }
          return e.parentElement;
        };
        // the part of `r` that is VISIBLE: intersected PER AXIS with the padding box of every clipping element from `start` up the containing-block chain
        const visiblePart = (start: Element | null, r: Box): Box | null => {
          let { left, top, right, bottom } = r;
          for (
            let a: Element | null = start;
            a && a !== document.documentElement;
            a = containingParent(a)
          ) {
            const cs = getComputedStyle(a);
            if (cs.display === 'contents') continue;
            const b = a.getBoundingClientRect();
            const cl = b.left + a.clientLeft;
            const ct = b.top + a.clientTop;
            if (cs.overflowX !== 'visible') {
              left = Math.max(left, cl);
              right = Math.min(right, cl + a.clientWidth);
            }
            if (cs.overflowY !== 'visible') {
              top = Math.max(top, ct);
              bottom = Math.min(bottom, ct + a.clientHeight);
            }
          }
          return right - left >= 4 && bottom - top >= 4 ? { left, top, right, bottom } : null;
        };
        const clippedAway = (kind: 'text' | 'control', text: string) => {
          out.clippedAway += 1;
          if (out.clippedAwayItems.length < 30)
            out.clippedAwayItems.push({ kind, text: text.slice(0, 50) });
        };
        const idOf = (el: Element) =>
          el.getAttribute('data-testid') ??
          `${el.tagName.toLowerCase()}.${String(el.getAttribute('class') ?? '').split(' ')[0]}`;
        const vw = window.innerWidth;
        const vh = window.innerHeight;
        const roots = Array.from(document.querySelectorAll<HTMLElement>(selector));
        const sample = (
          root: HTMLElement,
          kind: 'text' | 'control',
          label: string,
          x: number,
          y: number,
        ) => {
          // strictly INSIDE the page: a point on the window's right or bottom edge is outside it (elementFromPoint returns null there), which is not occlusion
          if (x < 0 || y < 0 || x >= vw || y >= vh) return;
          out.sampled += 1;
          // the first element of the stack (top first) that is inside the root, or that paints something at this point: a transparent container above is looked through
          let hit: Element | null = null;
          for (const e of document.elementsFromPoint(x, y)) {
            if (root.contains(e) || paintsAt(e, x, y)) {
              hit = e;
              break;
            }
          }
          if (hit !== null && root.contains(hit)) return;
          out.covered.push({
            kind,
            text: label.slice(0, 50),
            at: { x: Math.round(x), y: Math.round(y) },
            by:
              hit === null
                ? document.elementsFromPoint(x, y).length === 0
                  ? 'nothing (outside the page)'
                  : 'nothing that paints here'
                : idOf(hit),
          });
        };
        for (const root of roots) {
          const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
          for (let n = walker.nextNode(); n; n = walker.nextNode()) {
            const text = (n.textContent ?? '').replace(/\s+/g, ' ').trim();
            const parent = n.parentElement;
            if (text === '' || !parent || !parent.checkVisibility({ checkVisibilityCSS: true }))
              continue;
            const range = document.createRange();
            range.selectNodeContents(n);
            for (const r of Array.from(range.getClientRects())) {
              if (r.width < 4 || r.height < 4) continue;
              // a text's content is clipped by its own parent's overflow as well as by the containing-block chain above it
              const v = visiblePart(parent, r);
              if (v === null) {
                clippedAway('text', text);
                continue;
              }
              const w = v.right - v.left;
              const y = (v.top + v.bottom) / 2;
              for (const x of [
                v.left + Math.min(3, w / 4),
                v.left + w / 2,
                v.right - Math.min(3, w / 4),
              ])
                sample(root, 'text', text, x, y);
            }
          }
          for (const el of Array.from(
            root.querySelectorAll<HTMLElement>(
              'button, a[href], input, select, textarea, summary, [role="button"], [role="tab"]',
            ),
          )) {
            if (
              !el.checkVisibility({ checkVisibilityCSS: true }) ||
              el.getClientRects().length === 0
            )
              continue;
            const b = el.getBoundingClientRect();
            if (b.width < 4 || b.height < 4) continue;
            // a control is clipped by its ancestors, not by its own overflow; its centre is taken from the VISIBLE part
            const v = visiblePart(containingParent(el), b);
            if (v === null) {
              clippedAway('control', idOf(el));
              continue;
            }
            sample(root, 'control', idOf(el), (v.left + v.right) / 2, (v.top + v.bottom) / 2);
          }
        }
        return out;
      } finally {
        probe.remove();
        for (const [e, v] of lifted) e.setAttribute('inert', v);
      }
    },
    { selector: rootSelector, paintsAtSource: PAINTS_AT_SOURCE },
  );
}
