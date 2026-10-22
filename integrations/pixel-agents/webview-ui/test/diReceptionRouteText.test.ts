/**
 * `routeTextOf` (helpers/receptionRouteText.ts) reads a reception category's route text out of the server projection's own source, for the final-copy test.
 * It replaced a regular expression that backtracked exponentially when a literal was followed by a long run of whitespace and no valid continuation
 * (CodeQL js/redos), and it reads the source with the TypeScript parser so that text which only LOOKS like a property is never taken for one. Asserted here:
 *   1. ordinary input — one literal, several joined across lines, both quote kinds, an empty literal, decoded escapes, the right category's own route;
 *   2. the four silent misreads the first review reproduced — each is refused or read correctly, never the decoy;
 *   3. the REAL server source — every literal-route category parses, the identifier-valued one is refused, the mixed-quote route matches the fixture;
 *   4. malformed input is refused with an error that names the category;
 *   5. the shape that was catastrophic (a literal, then a very long whitespace run, nothing valid after it) is refused, and the same runs ended properly are read.
 * Every input here is synthetic or the real source. The pattern that was replaced is never run on hostile input.
 */
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

import { describe, expect, it } from 'vitest';

import type { DiOfficeState } from '../../core/src/messages.ts';
import { routeTextOf } from './helpers/receptionRouteText.ts';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');

/** A category object as the server source writes it; `route` is the text after `route:`, `before` the properties ahead of it. */
const category = (id: string, route: string, before = '') =>
  `  {\n    id: '${id}',\n    label: 'Label',\n${before}    route:${route}\n    v2Only: false,\n  },\n`;
/** A module holding these category objects, as the server source holds its list. */
const moduleOf = (...categories: string[]) => `const CATEGORIES = [\n${categories.join('')}];\n`;
const routeOfSingle = (route: string, before = '') =>
  routeTextOf(moduleOf(category('c', route, before)), 'c');

describe('1. ordinary input', () => {
  it('reads one literal, in either quote kind, and an empty one', () => {
    expect(routeOfSingle(" 'Outside this office: act.',")).toBe('Outside this office: act.');
    expect(routeOfSingle(' "Outside this office: act.",')).toBe('Outside this office: act.');
    expect(routeOfSingle(" '',")).toBe('');
    expect(routeOfSingle(' "",')).toBe('');
  });

  it('joins several literals across newlines, with the + at either end of a line, with no spacing, or with a comment between', () => {
    expect(routeOfSingle("\n      'one ' +\n      'two ' +\n      'three',")).toBe('one two three');
    expect(routeOfSingle("\n      'one '\n      + 'two',")).toBe('one two');
    expect(routeOfSingle(" 'a'+'b'\t+\t'c' ,")).toBe('abc');
    expect(routeOfSingle(" 'a' /* note */ + 'b',")).toBe('ab');
  });

  it('mixes quote kinds, as the real blockers route does (a double-quoted literal holding an apostrophe)', () => {
    expect(routeOfSingle(" \"the Mission's operator \" + 'and its team',")).toBe(
      "the Mission's operator and its team",
    );
    expect(routeOfSingle(' \'say "hi" \' + "it\'s",')).toBe('say "hi" it\'s');
  });

  it('decodes escapes as JavaScript does', () => {
    expect(routeOfSingle(" 'it\\'s a\\\\b\\nc \\u0041',")).toBe("it's a\\b\nc A");
  });

  it('reads a long chain of literals (collected in one pass down the left side of the + chain)', () => {
    const parts = Array.from({ length: 5_000 }, (_, n) => `part${n} `);
    const chain = `\n${parts.map((p) => `      '${p}'`).join(' +\n')},`;
    expect(routeOfSingle(chain)).toBe(parts.join(''));
  });

  it('takes the route of the category asked for, and reads a last property written without a trailing comma', () => {
    const source = moduleOf(
      category('first', " 'first route',"),
      category('second', " 'second route',"),
      category('third', " '',"),
    );
    expect(routeTextOf(source, 'first')).toBe('first route');
    expect(routeTextOf(source, 'second')).toBe('second route');
    expect(routeTextOf(source, 'third')).toBe('');
    expect(routeTextOf("const C = [{ id: 'last', route: 'no trailing comma' }];", 'last')).toBe(
      'no trailing comma',
    );
  });
});

describe('2. the four silent misreads the first review reproduced', () => {
  it('an id that appears only in a comment is not a category', () => {
    const source = "const C = [{ id: 'b', /* id: 'a' */ route: 'b route', }];";
    expect(() => routeTextOf(source, 'a')).toThrow(
      /route of category 'a': no category with id: 'a' in the source/,
    );
    expect(routeTextOf(source, 'b')).toBe('b route');
  });

  it("an id that is only the start of an expression ('a' + 'b') is not a category", () => {
    const source = "const C = [{ id: 'a' + 'b', route: 'ab route', }];";
    expect(() => routeTextOf(source, 'a')).toThrow(/no category with id: 'a'/);
    expect(() => routeTextOf(source, 'ab')).toThrow(/no category with id: 'ab'/);
  });

  it('a route that appears inside a conditional expression is not the property: the real route is read', () => {
    expect(
      routeTextOf("const C = [{ id: 'a', label: true ? route : 'fake', route: 'real', }];", 'a'),
    ).toBe('real');
  });

  it('a route inside a regular-expression literal is not the property: the real route is read', () => {
    expect(
      routeTextOf("const C = [{ id: 'a', label: /route: 'fake',/, route: 'real', }];", 'a'),
    ).toBe('real');
  });

  it('a string or a template that holds the id or the route is not a property either', () => {
    const decoy =
      "    statement: \"id: 'a', route: 'fake',\",\n    backing: `id: 'a', route: 'fake',`,\n";
    expect(routeOfSingle(" 'real',", decoy)).toBe('real');
    expect(() => routeTextOf(moduleOf(category('b', " 'b route',", decoy)), 'a')).toThrow(
      /no category with id: 'a'/,
    );
  });

  it('a route that is only in a nested object, or is shorthand, is not the category’s own route', () => {
    expect(() => routeTextOf("const C = [{ id: 'a', v1: { route: 'nested' } }];", 'a')).toThrow(
      /the category has no route property/,
    );
    expect(() => routeTextOf("const route = 'x'; const C = [{ id: 'a', route }];", 'a')).toThrow(
      /route is not a plain/,
    );
  });

  it('a quoted key is the same property: it is read, and a second route in either spelling is refused rather than the later one assumed', () => {
    expect(routeTextOf("const C = [{ id: 'a', 'route': 'quoted' }];", 'a')).toBe('quoted');
    expect(() =>
      routeTextOf("const C = [{ id: 'a', route: 'one', 'route': 'two' }];", 'a'),
    ).toThrow(/the category has more than one route property/);
  });

  it('a computed key or a spread could replace the route after it is written, so that category is refused rather than read', () => {
    expect(() =>
      routeTextOf("const C = [{ id: 'a', route: 'first', ['route']: 'actual' }];", 'a'),
    ).toThrow(/route of category 'a': the category has a spread or computed property/);
    expect(() =>
      routeTextOf("const C = [{ id: 'a', route: 'first', ...{ route: 'actual' } }];", 'a'),
    ).toThrow(/route of category 'a': the category has a spread or computed property/);
  });
});

describe('3. the real server source', () => {
  const source = fs.readFileSync(path.join(root, '..', 'server/src/di/receptionDesk.ts'), 'utf-8');
  const fixture = (
    JSON.parse(
      fs.readFileSync(
        path.join(root, 'test/fixtures/di-office-state-session-mismatch-v2.FIXTURE.json'),
        'utf-8',
      ),
    ) as { state: DiOfficeState }
  ).state.reception!;

  it('reads the concatenated authorization route as one sentence group, with no literal delimiter or + left in it', () => {
    const route = routeTextOf(source, 'authorization');
    expect(route).toMatch(/^Outside this office: /);
    expect(route.length).toBeGreaterThan(100);
    expect(route).not.toMatch(/['"]|\s\+\s/);
  });

  it('reads the mixed-quote blockers route and the other literal routes', () => {
    // the blockers route opens with a double-quoted literal that holds an apostrophe
    expect(routeTextOf(source, 'blockers')).toContain("the Mission's operator");
    for (const id of ['blockers', 'authorization', 'failed_verification', 'ready_for_review']) {
      const route = routeTextOf(source, id);
      expect(route, id).toMatch(/^Outside this office: /);
      expect(route.endsWith('.'), id).toBe(true);
      expect(route, id).toContain('This desk is read-only');
    }
  });

  it('reads the clarification route as the empty literal it is', () => {
    expect(routeTextOf(source, 'clarification')).toBe('');
  });

  it('agrees with the route the projection itself put on its blockers entry (an independent source of the same text)', () => {
    const entry = fixture.entries.find((e) => e.category === 'blockers')!;
    expect(routeTextOf(source, 'blockers')).toBe(entry.route);
  });

  it('refuses the delivery category, whose route is an identifier, rather than read a neighbour’s or guess', () => {
    expect(() => routeTextOf(source, 'delivery_decision')).toThrow(
      /route of category 'delivery_decision': not a string literal, or string literals joined by \+ \(found "DELIVERY_ROUTE"\)/,
    );
  });

  it('names a category that is not there', () => {
    expect(() => routeTextOf(source, 'no_such_category')).toThrow(
      /route of category 'no_such_category': no category with id: 'no_such_category' in the source/,
    );
  });
});

describe('4. malformed input is refused with a clear error', () => {
  it('a missing id, and an id that two categories share', () => {
    expect(() => routeTextOf(moduleOf(category('a', " 'x',")), 'b')).toThrow(
      /no category with id: 'b'/,
    );
    expect(() =>
      routeTextOf(moduleOf(category('a', " 'x',"), category('a', " 'y',")), 'a'),
    ).toThrow(/2 categories have id: 'a'/);
  });

  it('a category with no route does not borrow the next category’s', () => {
    const without = "  {\n    id: 'a',\n    label: 'L',\n    v2Only: false,\n  },\n";
    expect(() => routeTextOf(moduleOf(without, category('b', " 'b route',")), 'a')).toThrow(
      /route of category 'a': the category has no route property/,
    );
    expect(routeTextOf(moduleOf(without, category('b', " 'b route',")), 'b')).toBe('b route');
  });

  it('a value that is not a string literal or a + chain of them is refused', () => {
    for (const bad of [
      ' DELIVERY_ROUTE,',
      ' def.route,',
      ' `template`,',
      " ('parenthesised'),",
      " true ? 'a' : 'b',",
      " 'a' + other,",
      " 'a' + + 'b',",
      " + 'a',",
      " 'a' + ('b' + 'c'),",
      " String('a'),",
      ' 42,',
    ]) {
      expect(() => routeOfSingle(bad), bad).toThrow(
        /route of category 'c': not a string literal, or string literals joined by \+/,
      );
    }
  });

  it('a source with a syntax error — an unterminated literal, a dangling or doubled +, a missing , — is refused, never read', () => {
    for (const bad of [
      " 'never closed,",
      ' "never closed,',
      " 'a' +,",
      " 'a' ++ 'b',",
      " 'a'\n    v2Only: false,",
    ]) {
      expect(() => routeOfSingle(bad), bad).toThrow(
        /route of category 'c': the source has a syntax error/,
      );
    }
    expect(() => routeTextOf("const C = [{ id: 'c', route: 'a' +", 'c')).toThrow(
      /the source has a syntax error/,
    );
  });
});

describe('5. the shape that backtracked: a literal, a very long whitespace run, nothing valid after it', () => {
  // These inputs are far longer than any real source line, and nothing here depends on time: each is refused, or read in full.
  const SPACES = ' '.repeat(100_000);
  const MIXED = ' \n\t\r'.repeat(25_000);

  it('a literal, then the whitespace run, then something that is neither + nor ,', () => {
    expect(() => routeOfSingle(` 'x'${SPACES}`)).toThrow(/the source has a syntax error/);
    expect(() => routeOfSingle(` 'x'${MIXED}`)).toThrow(/the source has a syntax error/);
    expect(() => routeOfSingle(` 'x'${SPACES};`)).toThrow(/the source has a syntax error/);
  });

  it('a literal and its +, then the whitespace run, then no literal', () => {
    expect(() => routeOfSingle(` 'x' +${SPACES}`)).toThrow(/the source has a syntax error/);
    expect(() => routeOfSingle(` 'x'${SPACES}+${MIXED},`)).toThrow(/the source has a syntax error/);
    expect(() => routeTextOf(`const C = [{ id: 'c', route: 'x' +${SPACES}`, 'c')).toThrow(
      /the source has a syntax error/,
    );
  });

  it('the same runs, ended properly, are read in full', () => {
    expect(routeOfSingle(` 'x'${SPACES},`)).toBe('x');
    expect(routeOfSingle(` 'x'${MIXED}+${MIXED}'y'${SPACES},`)).toBe('xy');
  });
});
