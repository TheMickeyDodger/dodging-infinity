/**
 * AC6 sweep, measured in the page (one shared checker for every surface that is tested in a browser).
 *
 * This is a CUSTOM MEASURED SWEEP. It is NOT a full accessibility auditor and NOT manual VoiceOver proof, and it must be
 * described that way wherever its results appear. A caller that asserts a minimum target size says which controls it
 * covers; the sweep does not turn a baseline count of small controls into a compliance claim. It measures five things on the chrome
 * under a root selector, each as a plain value a test can assert on:
 *   1. every visible control has an accessible name, and where it came from (aria-labelledby, aria-label, a label,
 *      its own visible text, a title, a placeholder, or nothing);
 *   2. label in name (WCAG 2.5.3): when a control has visible text and an aria-label, the label contains that text;
 *   3. target size: the control's box (WCAG 2.5.8 asks for 24 by 24 CSS px);
 *   4. motion: any running animation or non-zero transition on the root's elements;
 *   5. state that is not carried by colour alone: every element that marks a state (tone, attention, outcome, status)
 *      says it in words or in a shape the page exposes.
 */
import type { Page } from 'playwright-core';

export interface ControlReport {
  /** data-testid, else tag plus first words of its text. */
  id: string;
  name: string;
  source: 'aria-labelledby' | 'aria-label' | 'label' | 'content' | 'title' | 'placeholder' | 'none';
  /** null when there is nothing to compare (no visible text or no aria-label). */
  labelInName: boolean | null;
  width: number;
  height: number;
}

export interface StateMarkReport {
  id: string;
  /** The words the element carries (visible text or accessible name) or '' when it is colour alone. */
  words: string;
}

export interface MotionReport {
  id: string;
  animationName: string;
  transitionDuration: string;
}

export interface A11ySweep {
  controls: ControlReport[];
  stateMarks: StateMarkReport[];
  motion: MotionReport[];
  /** Running CSS animations and transitions on elements under the root (document.getAnimations). */
  running: number;
}

/** Measure the chrome under every element matching `rootSelector` (all matches are swept). */
export async function a11ySweep(page: Page, rootSelector: string): Promise<A11ySweep> {
  return page.evaluate((selector) => {
    const roots = [...document.querySelectorAll<HTMLElement>(selector)];
    const shown = (el: Element) =>
      (el as HTMLElement).checkVisibility({ checkVisibilityCSS: true }) &&
      (el as HTMLElement).getClientRects().length > 0;
    /** Text a person sees: skips aria-hidden and hidden subtrees (glyphs marked decorative). */
    const text = (node: Node): string => {
      if (node.nodeType === Node.TEXT_NODE) return node.textContent ?? '';
      if (!(node instanceof HTMLElement)) return '';
      if (node.getAttribute('aria-hidden') === 'true' || node.hidden) return '';
      // text hidden by CSS (display none, visibility hidden) is not seen and not announced
      if (!node.checkVisibility({ checkVisibilityCSS: true })) return '';
      return [...node.childNodes].map(text).join('');
    };
    const flat = (s: string) =>
      s
        .normalize('NFKC')
        .toLowerCase()
        .replace(/[^\p{L}\p{N}]+/gu, ' ')
        .trim();
    const idOf = (el: Element) =>
      el.getAttribute('data-testid') ??
      `${el.tagName.toLowerCase()} "${text(el).trim().replace(/\s+/g, ' ').slice(0, 40)}"`;
    const nameOf = (el: HTMLElement): { name: string; source: ControlReport['source'] } => {
      const by = el.getAttribute('aria-labelledby');
      if (by) {
        const t = by
          .split(/\s+/)
          .map((i) => document.getElementById(i))
          .filter((n): n is HTMLElement => n !== null)
          .map((n) => text(n))
          .join(' ')
          .trim();
        if (t) return { name: t, source: 'aria-labelledby' };
      }
      const label = el.getAttribute('aria-label')?.trim();
      if (label) return { name: label, source: 'aria-label' };
      const labels = (el as HTMLInputElement).labels;
      if (labels && labels.length > 0) {
        const t = [...labels].map((l) => text(l)).join(' ').trim();
        if (t) return { name: t, source: 'label' };
      }
      const own = text(el).replace(/\s+/g, ' ').trim();
      if (own) return { name: own, source: 'content' };
      const title = el.getAttribute('title')?.trim();
      if (title) return { name: title, source: 'title' };
      const placeholder = el.getAttribute('placeholder')?.trim();
      if (placeholder) return { name: placeholder, source: 'placeholder' };
      return { name: '', source: 'none' };
    };

    const controls: ControlReport[] = [];
    const stateMarks: StateMarkReport[] = [];
    const motion: MotionReport[] = [];
    const seen = new Set<Element>();
    for (const root of roots) {
      const all = [root, ...root.querySelectorAll<HTMLElement>('*')];
      for (const el of all) {
        if (seen.has(el)) continue;
        seen.add(el);
        if (!shown(el)) continue;
        if (
          el.matches(
            'button, a[href], input:not([type="hidden"]), select, textarea, summary, [role="button"], [role="tab"], [role="option"]',
          )
        ) {
          const { name, source } = nameOf(el);
          const visible = text(el).replace(/\s+/g, ' ').trim();
          const labelled = el.getAttribute('aria-label');
          const rect = el.getBoundingClientRect();
          controls.push({
            id: idOf(el),
            name,
            source,
            // a select or field shows its VALUE, not its label: label in name does not apply to them
            labelInName:
              !el.matches('select, input, textarea') && visible !== '' && labelled !== null && labelled.trim() !== ''
                ? flat(labelled).includes(flat(visible))
                : null,
            width: Math.round(rect.width * 10) / 10,
            height: Math.round(rect.height * 10) / 10,
          });
        }
        if (el.matches('[data-tone], [data-attention="true"], [data-outcome], [data-status]')) {
          const words = (text(el).replace(/\s+/g, ' ').trim() || el.getAttribute('aria-label') || '').trim();
          stateMarks.push({ id: idOf(el), words });
        }
        const cs = getComputedStyle(el);
        const animating = cs.animationName !== 'none' && parseFloat(cs.animationDuration) > 0;
        const transitioning = cs.transitionDuration.split(',').some((d) => parseFloat(d) > 0);
        if (animating || transitioning)
          motion.push({ id: idOf(el), animationName: cs.animationName, transitionDuration: cs.transitionDuration });
      }
    }
    const running = document
      .getAnimations()
      .filter((a) => {
        const target = (a.effect as KeyframeEffect | null)?.target;
        return target instanceof Element && roots.some((r) => r.contains(target));
      }).length;
    return { controls, stateMarks, motion, running };
  }, rootSelector);
}
