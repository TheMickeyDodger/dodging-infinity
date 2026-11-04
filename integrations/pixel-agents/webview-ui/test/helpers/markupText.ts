/**
 * Readers for React `renderToStaticMarkup` output in tests.
 *
 * `String.replace` of `<[^>]+>` is an incomplete sanitizer: CodeQL reports
 * that `<script` can survive it. A `replace` that builds a regular expression
 * and does not escape backslash is reported the same way. These two functions
 * are what the tests use instead. They read text. They are not a sanitizer
 * for anything the product renders.
 */

const NAMED: Record<string, string> = {
  amp: '&',
  lt: '<',
  gt: '>',
  quot: '"',
  apos: "'",
};

function decodeEntity(body: string): string | null {
  const named = NAMED[body];
  if (named !== undefined) return named;
  const hex = /^#x([0-9a-f]+)$/i.exec(body);
  const dec = hex === null ? /^#([0-9]+)$/.exec(body) : null;
  const raw = hex?.[1] ?? dec?.[1];
  if (raw === undefined) return null;
  const codePoint = Number.parseInt(raw, hex === null ? 10 : 16);
  if (!Number.isInteger(codePoint) || codePoint < 0 || codePoint > 0x10ffff) return null;
  return String.fromCodePoint(codePoint);
}

/**
 * Tags removed, character entities decoded. `separator` is written where a tag
 * was (a space keeps words from joining; `''` keeps the old strip-and-trim
 * reading of a button label). A `>` inside a quoted attribute does not end
 * the tag.
 */
export function markupText(html: string, separator = ''): string {
  let out = '';
  let i = 0;
  while (i < html.length) {
    const ch = html[i]!;
    if (ch === '<') {
      if (html.startsWith('<!--', i)) {
        const end = html.indexOf('-->', i + 4);
        if (end === -1) {
          out += html.slice(i);
          break;
        }
        i = end + 3;
        out += separator;
        continue;
      }
      let j = i + 1;
      let quote: '"' | "'" | null = null;
      for (; j < html.length; j++) {
        const c = html[j]!;
        if (quote !== null) {
          if (c === quote) quote = null;
          continue;
        }
        if (c === '"' || c === "'") {
          quote = c;
          continue;
        }
        if (c === '>') break;
      }
      if (j >= html.length || html[j] !== '>') {
        out += html.slice(i);
        break;
      }
      i = j + 1;
      out += separator;
      continue;
    }
    if (ch === '&') {
      const semi = html.indexOf(';', i + 1);
      if (semi !== -1 && semi - i <= 32) {
        const decoded = decodeEntity(html.slice(i + 1, semi));
        if (decoded !== null) {
          out += decoded;
          i = semi + 1;
          continue;
        }
      }
    }
    out += ch;
    i += 1;
  }
  return out;
}

/** Tags become spaces, then runs of whitespace collapse to one space. Not trimmed. */
export function markupWords(html: string): string {
  return markupText(html, ' ').replace(/\s+/g, ' ');
}

/** A complete RegExp escape, backslash included. */
export function escapeRegExp(value: string): string {
  return value.replace(/[\\^$.*+?()[\]{}|]/g, '\\$&');
}
