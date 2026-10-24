/**
 * AC7 and AC-UX-16: no em or en dash in the copy THIS PROJECT AUTHORS (labels, help text, statements, sample
 * copy), read as rendered: visible text and the text announced beside it (title, aria-label, aria-description,
 * alt, placeholder, and a select's options).
 *
 * The rule does not apply to content we do not author, and that content is never altered to satisfy it: what a
 * person wrote, record and identifier values from the office state, and code-review source and diff bytes. A
 * component marks such content with `data-copy="content"` on the element that renders it; everything under that
 * element is reported separately as CONTENT (preserved byte for byte, outside the rule).
 *
 * Explanatory copy that arrives over the wire from the office (for example what a reception category covers) is
 * OUR copy and is read as authored: where it carries a dash, the UI's presentation layer puts it in plain words.
 * Source comments are never rendered, so they are never read.
 */
import type { Page } from 'playwright-core';

/** The two dashes the copy rule forbids: en dash U+2013 and em dash U+2014. */
export const FORBIDDEN_DASHES = /[–—]/;

/** The marker on an element that renders content this project does not author. */
export const CONTENT_MARKER = '[data-copy="content"]';

export interface RenderedCopy {
  /** Copy this UI authors carrying a forbidden dash: must be empty. */
  authored: string[];
  /** Content (marked) carrying a dash: preserved as received, reported for the record only. */
  content: string[];
}

/** Every visible line and announced attribute under `rootSelector` that carries a forbidden dash, by category. */
export async function renderedCopy(page: Page, rootSelector: string): Promise<RenderedCopy> {
  return page.evaluate(
    ([selector, source, marker]) => {
      const dash = new RegExp(source);
      const out = { authored: [] as string[], content: [] as string[] };
      const roots = [...document.querySelectorAll<HTMLElement>(selector)];
      if (roots.length === 0) {
        out.authored.push(`no element matches ${selector}: nothing was read`);
        return out;
      }
      const bucket = (el: Element) => (el.closest(marker) ? out.content : out.authored);
      for (const root of roots) {
        const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
        for (let node = walker.nextNode(); node; node = walker.nextNode()) {
          const text = node.textContent ?? '';
          const parent = node.parentElement;
          if (!parent || !dash.test(text)) continue;
          if (parent.closest('option')) continue; // options are read below, open or not
          if (!parent.checkVisibility({ checkVisibilityCSS: true })) continue;
          bucket(parent).push(`text: ${text.trim()}`);
        }
        for (const option of root.querySelectorAll('option'))
          if (dash.test(option.textContent ?? ''))
            bucket(option).push(`option: ${option.textContent?.trim()}`);
        for (const el of [root, ...root.querySelectorAll('*')])
          for (const attr of ['title', 'aria-label', 'aria-description', 'alt', 'placeholder']) {
            const value = el.getAttribute(attr);
            if (value !== null && dash.test(value)) bucket(el).push(`${attr}: ${value}`);
          }
      }
      return out;
    },
    [rootSelector, FORBIDDEN_DASHES.source, CONTENT_MARKER] as const,
  );
}

/** Authored-copy dashes only (the assertion); content is left as it is. */
export async function renderedDashes(page: Page, rootSelector: string): Promise<string[]> {
  return (await renderedCopy(page, rootSelector)).authored;
}
