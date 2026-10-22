/**
 * A category's route text, read from the reception desk's own TypeScript source (`server/src/di/receptionDesk.ts`), so a test asserts on the REAL copy the
 * projection serves instead of a sentence copied into the test.
 *
 * The source is read with the TypeScript compiler's own lexer and parser (a declared devDependency), never with a regular expression, so a comment, a string,
 * a template or a regex literal that merely contains `id: 'x'` or `route: 'y'` is not mistaken for a property. A source with a syntax error is refused. The
 * category is the one object literal whose own `id` property is the string literal asked for (none, or more than one, is an error); its route is that same
 * object's own `route` property, and its value must be a string literal or a `+` chain of string literals, read as the parser decodes them. Anything else
 * (an identifier such as `DELIVERY_ROUTE`, a template, a call, a conditional, a shorthand property) throws, naming the category, and so does a category object
 * that has a spread or a computed key, which could replace its `id` or `route` at run time and are not evaluated here.
 */
import ts from 'typescript';

const FILE = 'receptionDesk.ts';
const TARGET = ts.ScriptTarget.ES2023;

/** The properties of an object literal written with exactly this name (as an identifier or a quoted key). */
const named = (o: ts.ObjectLiteralExpression, name: string) =>
  o.properties.filter(
    (p) =>
      p.name && (ts.isIdentifier(p.name) || ts.isStringLiteral(p.name)) && p.name.text === name,
  );

/** The decoded route text of the category with this `id` in `source`. Throws, saying why, for anything it cannot read exactly. */
export function routeTextOf(source: string, id: string): string {
  function fail(why: string): never {
    throw new Error(`route of category '${id}': ${why}`);
  }

  const { diagnostics } = ts.transpileModule(source, {
    fileName: FILE,
    reportDiagnostics: true,
    compilerOptions: { target: TARGET },
  });
  if (diagnostics?.length) {
    fail(
      `the source has a syntax error (${ts.flattenDiagnosticMessageText(diagnostics[0].messageText, ' ')})`,
    );
  }

  const file = ts.createSourceFile(FILE, source, TARGET, false, ts.ScriptKind.TS);
  const categories: ts.ObjectLiteralExpression[] = [];
  // An explicit stack, not recursion: a `a + b + …` chain nests as deeply as it is long.
  const pending: ts.Node[] = [file];
  for (let node = pending.pop(); node; node = pending.pop()) {
    if (ts.isObjectLiteralExpression(node)) {
      const ids = named(node, 'id');
      const [only] = ids;
      if (
        ids.length === 1 &&
        ts.isPropertyAssignment(only) &&
        ts.isStringLiteral(only.initializer) &&
        only.initializer.text === id
      ) {
        categories.push(node);
      }
    }
    ts.forEachChild(node, (child) => {
      pending.push(child);
    });
  }
  if (categories.length !== 1) {
    fail(
      categories.length === 0
        ? `no category with id: '${id}' in the source`
        : `${categories.length} categories have id: '${id}'`,
    );
  }

  // A spread or a computed key can replace `id` or `route` at run time and this reader does not evaluate them, so it will not guess.
  if (
    categories[0].properties.some(
      (p) => ts.isSpreadAssignment(p) || (p.name && ts.isComputedPropertyName(p.name)),
    )
  ) {
    fail('the category has a spread or computed property, which this reader does not evaluate');
  }

  const routes = named(categories[0], 'route');
  if (routes.length !== 1)
    fail(
      routes.length === 0
        ? 'the category has no route property'
        : 'the category has more than one route property',
    );
  const route = routes[0];
  if (!ts.isPropertyAssignment(route)) fail('route is not a plain `route: value` property');

  function notLiteral(e: ts.Expression): never {
    return fail(
      `not a string literal, or string literals joined by + (found ${JSON.stringify(e.getText(file).slice(0, 40))})`,
    );
  }
  // The parser nests `a + b + c` as `(a + b) + c`: walk that left spine once, collecting each right operand, then the leftmost literal. One pass, no recursion, no copying.
  const texts: string[] = [];
  let value = route.initializer;
  while (ts.isBinaryExpression(value) && value.operatorToken.kind === ts.SyntaxKind.PlusToken) {
    if (!ts.isStringLiteral(value.right)) notLiteral(value.right);
    texts.push(value.right.text);
    value = value.left;
  }
  if (!ts.isStringLiteral(value)) notLiteral(value);
  texts.push(value.text);
  return texts.reverse().join('');
}
