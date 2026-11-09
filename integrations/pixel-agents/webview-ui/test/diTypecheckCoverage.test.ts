/**
 * THE TYPE-CHECK COVERAGE GUARD (B2d; the Supervisor's requirement). FAILS when an owned test source (every .ts / .tsx under webview-ui/test/) belongs to
 * NONE of the programs `tsc -b` builds. WHY a guard and not a comment: tsconfig.node.json's comment already stated the convention (a browser test is
 * EXCLUDED there, because that program has no DOM lib, AND INCLUDED in a program that has it), and 15 test files still got the exclusion and never the
 * include. `tsc -b` stayed green, because a file in no program is checked by nothing; a written convention that is not
 * enforced gets half-applied.
 *
 * MEMBERSHIP IS TYPESCRIPT'S OWN, never a reading of the globs: each project tsconfig.json references is parsed by `ts.getParsedCommandLineOfConfigFile`
 * (its include / exclude / files, resolved by the compiler) and built into a program by `ts.createProgram`. A program's source files include every file
 * its roots IMPORT, transitively, so a file reached only by an import counts as checked, as it is. No type check runs here; this reads membership only.
 * NO EXEMPTIONS. WHICH PROGRAM, by what the configs themselves constrain (see WHICH_PROGRAM below, the text a failure prints).
 * ITS LIMIT, stated so it is not read as covering more: it catches an ORPHAN (a test source no program builds). It CANNOT catch a MIS-FILING between
 * tsconfig.test-mission.json and tsconfig.test-city.json: the two are compiler-identical, so a file in the wrong one compiles identically and nothing
 * goes red. Which of the two is a convention (WHICH_PROGRAM), kept by review, not by this guard.
 *
 * RULE 7, ON THE GUARD ITSELF: a guard that passes proves nothing until it is shown to fail. The CONTROLS build a throwaway project in a fresh temp
 * directory (never the repo, so they cannot leave an orphan behind), recorded at creation and removed by that path: a deliberately ORPHANED file must be
 * reported; a file excluded from every program but IMPORTED by a checked one must NOT be (the transitive case); an included file must not be.
 */
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

import ts from 'typescript';
import { afterAll, test } from 'vitest';

const webviewRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');

/** The rule a failure prints, derived from the configs (their resolved options compared, and each one's own text), never from precedent. */
const WHICH_PROGRAM =
  'Which program, by what the configs constrain: (1) a test source that IMPORTS the server\'s sources (server/src, written with parameter ' +
  'properties, which erasableSyntaxOnly forbids) goes in tsconfig.test-mission.json or tsconfig.test-city.json, the only programs with that flag off. ' +
  'The two are compiler-identical (test-dom\'s options with erasableSyntaxOnly off), so the choice between them is a CONVENTION, not a technical ' +
  'constraint: test-city holds the living-city tests (they import the city and ambient persistence modules), test-mission every other test that ' +
  'imports the server. (2) Any other ' +
  'test source that needs the DOM lib (a browser test\'s in-page callbacks touch document and window) goes in tsconfig.test-dom.json. In both cases it ' +
  'is ALSO EXCLUDED from tsconfig.node.json (no DOM lib there): the exclusion alone leaves it checked by nothing. (3) A test that needs neither stays ' +
  'in tsconfig.node.json.';
/** What the guard prints on failure (shared, so the controls render the very text a failing run would show). */
const failureText = (found: string[]): string => `test sources in NO program tsc -b builds (checked by nothing): ${found.join(', ')}. ${WHICH_PROGRAM}`;

/** Every source file of every program `tsc -b` builds from `solution` (a tsconfig.json whose `references` name the projects), as absolute paths. */
function checkedFiles(solution: string): { projects: string[]; files: Set<string> } {
  const read = ts.readConfigFile(solution, (p) => ts.sys.readFile(p));
  if (read.error) throw new Error(ts.flattenDiagnosticMessageText(read.error.messageText, '\n'));
  const refs = ((read.config as { references?: Array<{ path: string }> }).references ?? []).map((r) => {
    const target = path.resolve(path.dirname(solution), r.path);
    return ts.sys.directoryExists(target) ? path.join(target, 'tsconfig.json') : target;
  });
  const files = new Set<string>();
  const host: ts.ParseConfigFileHost = { ...ts.sys, onUnRecoverableConfigFileDiagnostic: (d) => {
    throw new Error(ts.flattenDiagnosticMessageText(d.messageText, '\n'));
  } };
  for (const project of refs) {
    const parsed = ts.getParsedCommandLineOfConfigFile(project, {}, host);
    if (!parsed) throw new Error(`could not parse ${project}`);
    const program = ts.createProgram({ rootNames: parsed.fileNames, options: parsed.options, projectReferences: parsed.projectReferences });
    for (const sf of program.getSourceFiles()) files.add(path.resolve(sf.fileName));
  }
  return { projects: refs, files };
}

/** Every .ts / .tsx under `dir`, recursively (node_modules never walked). */
function ownedSources(dir: string): string[] {
  const out: string[] = [];
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) {
      if (entry.name !== 'node_modules') out.push(...ownedSources(full));
    } else if (/\.tsx?$/.test(entry.name)) out.push(path.resolve(full));
  }
  return out.sort();
}

/** The owned sources under `testDir` that no program of `solution` contains. */
function orphans(solution: string, testDir: string): string[] {
  const { files } = checkedFiles(solution);
  return ownedSources(testDir).filter((f) => !files.has(f));
}

// ---- RULE 7: the controls, on a throwaway project in a fresh temp directory ----
const made: string[] = [];
afterAll(() => {
  for (const dir of made) fs.rmSync(dir, { recursive: true, force: true });
});

test('CONTROLS (rule 7): a deliberately orphaned file IS reported; an excluded file a checked file imports is NOT; an included file is NOT', () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'di-tscov-'));
  made.push(dir);
  const write = (rel: string, text: string) => {
    fs.mkdirSync(path.dirname(path.join(dir, rel)), { recursive: true });
    fs.writeFileSync(path.join(dir, rel), text);
  };
  write('tsconfig.json', JSON.stringify({ files: [], references: [{ path: './tsconfig.a.json' }] }));
  write('tsconfig.a.json', JSON.stringify({ compilerOptions: { noEmit: true, types: [], module: 'esnext', moduleResolution: 'bundler' }, include: ['test/**/*.ts'], exclude: ['test/orphan.test.ts', 'test/imported.ts'] }));
  write('test/included.test.ts', "import { x } from './imported';\nexport const y = x;\n");
  write('test/imported.ts', 'export const x = 1;\n');
  write('test/orphan.test.ts', 'export const z = 2;\n');
  const found = orphans(path.join(dir, 'tsconfig.json'), path.join(dir, 'test')).map((f) => path.relative(dir, f));
  assert.deepEqual(found, ['test/orphan.test.ts'], 'NEGATIVE CONTROL: exactly the orphan is reported (the imported file is checked through its importer)');
  // RULE 7 on the failure TEXT (what a person reads at the moment they hit the guard): rendered here, on the control's own orphan, every run
  const text = failureText(found);
  console.log(`TSCOV-CONTROL failure text: ${text}`);
  assert.ok(text.includes('test/orphan.test.ts') && text.includes(WHICH_PROGRAM), 'the failure text names the orphan and prints the rule');
}, 60_000);

// ---- THE GUARD, on the real configuration ----
test("GUARD: every owned test source under webview-ui/test/ belongs to a program tsc -b builds (tsconfig.json's references)", () => {
  const solution = path.join(webviewRoot, 'tsconfig.json');
  const { projects } = checkedFiles(solution);
  assert.ok(projects.length >= 2, `SETUP: the solution names its projects (${projects.map((p) => path.basename(p)).join(', ')})`);
  const found = orphans(solution, path.join(webviewRoot, 'test')).map((f) => path.relative(webviewRoot, f));
  // RECORDED before the assertion (rule 7: every outcome is read), so a failing run still names exactly what it found
  console.log(`TSCOV ${JSON.stringify({ projects: projects.map((p) => path.basename(p)), owned: ownedSources(path.join(webviewRoot, 'test')).length, orphans: found })}`);
  assert.deepEqual(
    found,
    [],
    failureText(found),
  );
}, 180_000);
