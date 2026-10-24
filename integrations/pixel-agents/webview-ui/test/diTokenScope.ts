/**
 * Computed token-scope audit, measured in the browser.
 *
 * CSS custom properties inherit DOWNWARD only. A rule that consumes `var(--x)` on an element that is an ancestor or a
 * sibling of the rule that DEFINES `--x` gets no value: the declaration is invalid at computed-value time and the
 * property falls back to its initial value (a backdrop becomes transparent). A test that matches the source text of
 * the rule, or checks that the token is defined somewhere, passes either way. This audit does not read source: for
 * every style rule whose selector names this project's chrome, it finds the elements the rule matches in the RENDERED
 * page and asks the browser whether each token the rule consumes has a computed value on that element.
 *
 * It reports gaps; a test asserts there are none for the states it rendered. It only audits elements that exist in
 * the rendered state: a rule with no matching element is counted as unmatched, not as checked.
 *
 * WHAT THIS AUDIT DOES NOT SEE, and `rawColourLiterals` below covers: it audits tokens a rule CONSUMES. A raw colour written straight into a
 * declaration (`background: #30304f`) consumes no token, so it is invisible here by construction, and the eslint guard
 * (pixel-agents/no-inline-colors) reads TS and TSX only. Both are blind to a hex in a chrome stylesheet; that is how two survived.
 */
import type { Page } from 'playwright-core';

export interface TokenScopeGap {
  /** The consuming rule's selector text. */
  selector: string;
  /** The custom property the rule consumes without a fallback. */
  token: string;
  /** How many matched elements have no computed value for the token. */
  elements: number;
  /** An example element (data-testid, else tag and class). */
  example: string;
}

export interface TokenScopeAudit {
  gaps: TokenScopeGap[];
  /** Rules whose selector matched the filter and that consume a token without a fallback. */
  consumingRules: number;
  /** Of those, how many matched at least one element in this rendered state (the rest were not audited here). */
  matchedRules: number;
  /** Element-by-token pairs that were checked. */
  pairsChecked: number;
  /** Selectors of consuming rules that matched NO element in this rendered state (not audited here; review them by hand). */
  unmatched: string[];
}

/** Audit every rule whose selector contains `selectorFilter` (the chrome's class prefix, `.di-` by default). */
export async function tokenScopeAudit(
  page: Page,
  selectorFilter = '.di-',
): Promise<TokenScopeAudit> {
  return page.evaluate((filter) => {
    const out = {
      gaps: [] as Array<{ selector: string; token: string; elements: number; example: string }>,
      consumingRules: 0,
      matchedRules: 0,
      pairsChecked: 0,
      unmatched: [] as string[],
    };
    const seen = new Map<
      string,
      { selector: string; token: string; elements: number; example: string }
    >();

    /**
     * The selector with DYNAMIC state (hover, focus, active, visited) and pseudo-elements removed, so the originating elements can be
     * found. Static pseudo-classes such as :disabled and :checked are kept: querySelectorAll supports them, and removing them from
     * inside :not(...) would leave an invalid selector.
     */
    const matchable = (selector: string) =>
      selector
        .replace(
          /(?<=\S)::?(?:hover|focus-visible|focus-within|focus|active|visited|before|after|placeholder|selection|-webkit-[\w-]+)\b/g,
          '',
        )
        .replace(
          /(^|[\s>+~,(])::?(?:hover|focus-visible|focus-within|focus|active|visited|before|after|placeholder|selection|-webkit-[\w-]+)\b/g,
          '$1*',
        );

    const visit = (rules: CSSRuleList) => {
      for (const rule of Array.from(rules)) {
        if (rule instanceof CSSMediaRule) {
          if (window.matchMedia(rule.media.mediaText).matches) visit(rule.cssRules);
          continue;
        }
        if (rule instanceof CSSSupportsRule || rule instanceof CSSLayerBlockRule) {
          visit(rule.cssRules);
          continue;
        }
        if (!(rule instanceof CSSStyleRule) || !rule.selectorText.includes(filter)) continue;
        const tokens = new Set<string>();
        for (const m of rule.style.cssText.matchAll(/var\(\s*(--[\w-]+)\s*(,)?/g))
          if (m[2] === undefined) tokens.add(m[1]);
        if (tokens.size === 0) continue;
        out.consumingRules += 1;
        let matchedAny = false;
        for (const part of rule.selectorText.split(',')) {
          const elements = (() => {
            try {
              return Array.from(document.querySelectorAll(matchable(part.trim())));
            } catch {
              return [];
            }
          })();
          if (elements.length === 0) continue;
          matchedAny = true;
          for (const token of tokens) {
            for (const el of elements) {
              out.pairsChecked += 1;
              if (getComputedStyle(el).getPropertyValue(token).trim() !== '') continue;
              const key = `${part.trim()}|${token}`;
              const id =
                el.getAttribute('data-testid') ??
                `${el.tagName.toLowerCase()}.${String(el.getAttribute('class') ?? '').split(' ')[0]}`;
              const hit = seen.get(key);
              if (hit) hit.elements += 1;
              else seen.set(key, { selector: part.trim(), token, elements: 1, example: id });
            }
          }
        }
        if (matchedAny) out.matchedRules += 1;
        else out.unmatched.push(rule.selectorText);
      }
    };
    for (const sheet of Array.from(document.styleSheets)) {
      try {
        visit(sheet.cssRules);
      } catch {
        // a cross-origin sheet cannot be read; the chrome's own sheets are same-origin
      }
    }
    out.gaps = [...seen.values()];
    return out;
  }, selectorFilter);
}

export interface ColourLiteral {
  /** 1-based line of the declaration in the stylesheet. */
  line: number;
  /** The selector (or at-rule prelude) of the rule that holds the declaration. */
  selector: string;
  property: string;
  /** The literal as written (a hex colour, or the colour function's name with its opening parenthesis). */
  literal: string;
  /**
   * 'direct': an element is styled with a raw colour (a violation in chrome CSS: controls consume tokens).
   * 'definition': a custom property is DEFINED with a raw colour (a palette entry; allowed only where a test pins it by name).
   */
  kind: 'direct' | 'definition';
}

const COLOUR_LITERAL =
  /#[0-9a-fA-F]{3,8}\b|\b(?:rgb|rgba|hsl|hsla|hwb|lab|lch|oklab|oklch|color)\(/g;
/** An alpha mask paints nothing in its own colour (only its alpha counts), so a colour there is not a styling colour. */
const ALPHA_ONLY = /^(?:-webkit-)?mask(?:-image)?$/;

/**
 * Every raw colour literal in a stylesheet's source: hex colours and the colour functions (rgb, rgba, hsl, hsla, hwb, lab, lch, oklab, oklch,
 * color). A pure scan of the source (no browser): comments and `url(...)` are ignored, nesting inside at-rules is followed, custom-property
 * definitions are reported as 'definition' and everything else as 'direct'; alpha masks are exempt. Keywords (`transparent`, `currentColor`)
 * and `var(...)` are not literals. It reports; a test asserts.
 */
export function rawColourLiterals(source: string): ColourLiteral[] {
  const blank = (m: string) => m.replace(/[^\n]/g, ' ');
  const css = source
    .replace(/\/\*[\s\S]*?\*\//g, blank)
    .replace(/url\([^)]*\)/g, (m) => `url(${blank(m.slice(4, -1))})`);
  const lineAt = (index: number) => css.slice(0, index).split('\n').length;
  const out: ColourLiteral[] = [];
  const selectors: string[] = [];
  let start = 0;
  const declaration = (text: string, at: number) => {
    const colon = text.indexOf(':');
    if (colon < 0) return;
    const property = text.slice(0, colon).trim();
    const value = text.slice(colon + 1);
    if (property === '' || ALPHA_ONLY.test(property)) return;
    const selector = (selectors[selectors.length - 1] ?? '').replace(/\s+/g, ' ').trim();
    for (const m of value.matchAll(COLOUR_LITERAL))
      out.push({
        line: lineAt(at + text.indexOf(property)),
        selector,
        property,
        literal: m[0],
        kind: property.startsWith('--') ? 'definition' : 'direct',
      });
  };
  for (let i = 0; i < css.length; i += 1) {
    const ch = css[i];
    if (ch === '{') {
      selectors.push(css.slice(start, i));
      start = i + 1;
    } else if (ch === ';') {
      declaration(css.slice(start, i), start);
      start = i + 1;
    } else if (ch === '}') {
      declaration(css.slice(start, i), start);
      selectors.pop();
      start = i + 1;
    }
  }
  return out;
}
