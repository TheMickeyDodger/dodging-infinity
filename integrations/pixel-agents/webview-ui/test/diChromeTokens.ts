/**
 * The ONE reader of the shared DI chrome tokens (`--di-*`, defined once in `src/index.css` `:root`). Every chrome
 * contrast test resolves tokens through this file, so they all resolve identically.
 *
 * A chrome stylesheet names its palette as aliases (`--op-text: var(--di-text);`). `resolveShared(css)` returns the
 * stylesheet with every `var(--di-*)` replaced by its `:root` value, following chains, so each test's existing reader
 * and pins (solid `#rrggbb` tokens, font stacks, px sizes) run on the values the browser uses.
 *
 * It FAILS LOUDLY and never degrades: a `var(--di-*)` that `:root` does not define, a chain that loops, a token
 * defined twice, and a `var(--di-*, fallback)` (a fallback could hide a missing token) all throw. There is no
 * default colour.
 */
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');

/** index.css as written. */
export const INDEX_CSS = fs.readFileSync(path.join(root, 'src/index.css'), 'utf-8');

/** Every `--di-*` declaration of the `:root` rule of index.css, raw (comments removed). Throws if none, or on a repeat. */
export function sharedTokens(indexCss: string = INDEX_CSS): Record<string, string> {
  const code = indexCss.replace(/\/\*[\s\S]*?\*\//g, '');
  const rule = /(?:^|\n):root \{([\s\S]*?)\n\}/.exec(code);
  if (!rule) throw new Error('index.css has no :root rule');
  const found: Record<string, string> = {};
  for (const m of rule[1].matchAll(/--di-([a-z0-9-]+):\s*([^;]+);/g)) {
    if (m[1] in found) throw new Error(`--di-${m[1]} is defined twice in :root`);
    found[m[1]] = m[2].replace(/\s+/g, ' ').trim();
  }
  if (Object.keys(found).length === 0) throw new Error('index.css :root defines no --di-* token');
  const elsewhere = code.replace(rule[0], '').match(/--di-[a-z0-9-]+\s*:/g);
  if (elsewhere) throw new Error(`--di-* defined outside :root: ${elsewhere.join(', ')}`);
  return found;
}

const TOKENS = sharedTokens();

function substitute(text: string, seen: readonly string[]): string {
  const fallback = /var\(\s*--di-[a-z0-9-]+\s*,/.exec(text);
  if (fallback) throw new Error(`a var(--di-*) fallback could hide a missing token: ${fallback[0]}`);
  return text.replace(/var\(\s*--di-([a-z0-9-]+)\s*\)/g, (_m, name: string) => sharedValue(name, seen));
}

/** One `--di-*` token's final value, every `var(--di-*)` in it resolved. Throws on a missing or looping token. */
export function sharedValue(name: string, seen: readonly string[] = []): string {
  if (!(name in TOKENS)) throw new Error(`--di-${name} is not defined in index.css :root`);
  if (seen.includes(name)) throw new Error(`--di-* chain loops: ${[...seen, name].join(' -> ')}`);
  return substitute(TOKENS[name], [...seen, name]);
}

/** The stylesheet with every `var(--di-*)` replaced by its resolved value; throws if any cannot be resolved. */
export function resolveShared(css: string): string {
  return substitute(css, []);
}

/** The `--di-*` tokens a stylesheet names directly, sorted. */
export function sharedTokensUsedBy(css: string): string[] {
  return [...new Set([...css.matchAll(/var\(\s*--di-([a-z0-9-]+)\s*\)/g)].map((m) => m[1]))].sort();
}

/** The `--di-*` tokens a palette rule (`.di-op-theme { ... }` and the like) aliases, sorted. */
export function sharedTokensAliasedBy(css: string, ruleSelector: string): string[] {
  const escaped = ruleSelector.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  const rule = new RegExp(`\\n${escaped} \\{([\\s\\S]*?)\\n\\}`).exec(css);
  if (!rule) throw new Error(`the ${ruleSelector} rule was not found`);
  return sharedTokensUsedBy(rule[1]);
}

/** A solid colour: exactly `#rrggbb`. */
export const SOLID = /^#[0-9a-fA-F]{6}$/;
/** What a test could not check: an alpha colour function or an 8-digit (or 4-digit) hex. */
export const ALPHA = /\b(?:rgba?|hsla?)\(|#[0-9a-fA-F]{8}\b|#[0-9a-fA-F]{4}\b/;
