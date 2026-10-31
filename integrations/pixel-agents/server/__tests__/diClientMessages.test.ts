/**
 * A4 — inert BY CONSTRUCTION, tested at the HANDLER (not by a hidden button):
 * 1. every protocol ClientMessage type outside the DI allowlist does nothing — no
 *    reply, no adapter write, no file write;
 * 2. the DI handshake carries no hooks status, no consent ask, no provider tool list;
 * 3. the DI module graph imports no hook provider, runtime, scanner or consent module.
 */
import * as fs from 'fs';
import * as os from 'os';
import * as path from 'path';
import * as ts from 'typescript';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import type { Shape } from './helpers/layoutCaps.js';
import {
  AREA_COLOUR,
  CAP,
  cellCapReason,
  DIM_SQUARED,
  heavyLabel,
  JUST_OVER_CELLS,
  largestAdmitted,
  LONG_COLOUR,
  maximalLegitimate,
  metropolisShape,
  overBytes,
  TALLEST,
  tilesOnly,
  WIDEST,
} from './helpers/layoutCaps.js';

let sandbox: string;
let fakeHome: string;

vi.mock('os', async () => {
  const actual = await vi.importActual<typeof import('os')>('os');
  return { ...actual, homedir: () => fakeHome };
});

const { AgentStateStore } = await import('../src/agentStateStore.js');
const { resetStateRootForTests, setStateRoot } = await import('../src/stateRoot.js');
const { FileStateAdapter } = await import('../src/fileStateAdapter.js');
const { DI_HANDLED_MESSAGE_TYPES, DI_INERT_MESSAGE_TYPES, handleDiClientMessage, initDiDefaults } =
  await import('../src/di/diClientMessages.js');
const { validateDiLayout } = await import('../src/di/diInputLimits.js');
const {
  DI_ID_MAX_LENGTH,
  DI_LAYOUT_MAX_CELLS,
  DI_LAYOUT_MAX_DIM,
  DI_LAYOUT_MAX_FURNITURE,
  DI_WS_MAX_PAYLOAD_BYTES,
} = await import('../src/constants.js');
/** Near-cap layouts take ~0.3-0.6 s per check (structure + two UTF-8 byte counts). */
const HEAVY_MS = 120_000;

const SRC = path.join(__dirname, '../src');

const EMPTY_CACHE = {
  characters: null,
  pets: null,
  floorTiles: null,
  wallTiles: null,
  carpetTiles: null,
  furniture: null,
  defaultLayout: null,
};

/** Representative payloads for the inert types (as a real client would send). */
const INERT_PAYLOADS: Record<string, Record<string, unknown>> = {
  launchAgent: { type: 'launchAgent', folderPath: '/tmp', bypassPermissions: true },
  focusAgent: { type: 'focusAgent', id: 1 },
  closeAgent: { type: 'closeAgent', id: 1 },
  setHooksEnabled: { type: 'setHooksEnabled', providerId: 'claude', enabled: true },
  hooksConsentResponse: { type: 'hooksConsentResponse', providerId: 'claude', choice: 'install' },
  setHooksInfoShown: { type: 'setHooksInfoShown' },
  setWatchAllSessions: { type: 'setWatchAllSessions', enabled: true },
  openSessionsFolder: { type: 'openSessionsFolder' },
  addExternalAssetDirectory: { type: 'addExternalAssetDirectory', path: '/etc' },
  removeExternalAssetDirectory: { type: 'removeExternalAssetDirectory', path: '/etc' },
  saveAreaMappings: { type: 'saveAreaMappings', mappings: { a: ['b'] } },
  exportLayout: { type: 'exportLayout' },
  importLayout: { type: 'importLayout' },
  requestDiagnostics: { type: 'requestDiagnostics' },
};

function treeOf(root: string): string[] {
  const out: string[] = [];
  const walk = (dir: string): void => {
    for (const e of fs.readdirSync(dir, { withFileTypes: true })) {
      const full = path.join(dir, e.name);
      out.push(path.relative(root, full));
      if (e.isDirectory()) walk(full);
    }
  };
  if (fs.existsSync(root)) walk(root);
  return out.sort();
}

beforeEach(() => {
  sandbox = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), 'di-handler-')));
  fakeHome = path.join(sandbox, 'home');
  fs.mkdirSync(fakeHome);
  resetStateRootForTests();
  setStateRoot(path.join(sandbox, 'di-root'));
});

afterEach(() => {
  resetStateRootForTests();
  fs.rmSync(sandbox, { recursive: true, force: true });
});

describe('DI handler: inert types do nothing at all', () => {
  it('the inert list covers every protocol ClientMessage the DI office does not act on', () => {
    const messagesTs = fs.readFileSync(path.join(__dirname, '../../core/src/messages.ts'), 'utf-8');
    const union = /export type ClientMessage =([^;]*);/.exec(messagesTs)?.[1] ?? '';
    const variants = union
      .split('|')
      .map((v) => v.trim())
      .filter(Boolean);
    // + saveAmbientState (M3 S4, DI only) + diEvidenceRead (item 2, DI only, a READ)
    // + diCodeReviewRead (item 3, DI only, a READ)
    // + diCodeReviewMissionRead (I3, DI only, a READ: the same review asked by a Mission-level reference, for a FINISHED Mission)
    // + diMissionRequest (item 4, DI only: the ONE deliberate, bounded non-read; fourteen closed ops since (c))
    // + saveCityPresentation (living city 6-9, DI only: the viewer's lighting/weather/traffic choice,
    //   checked against core/src/cityPresentation.ts and written under the DI state root)
    // + saveDiSeatPreferences (P2-C, DI only: the identity-keyed seat preferences, checked against
    //   core/src/diSeatPreferences.ts and written under the DI state root)
    // CHANGED IN I3 (was 28 and 28): diCodeReviewMissionRead is the 29th protocol variant and the 29th declared type, handled.
    // CHANGED IN b2b (was 29 and 29): diDeliveryRequest (the delivery bridge, DI only: two closed ops, relayed to the office
    //   delivery adapter, at most one step per advanceOnce) is the 30th protocol variant and the 30th declared type, HANDLED.
    //   It was declared only after its dispatch case and its relay existed.
    // CHANGED IN Package A (was 30 and 30): diHerdrRequest (the Herdr terminal route, DI only: two closed ops, `status` with no effect
    //   and `launch`) is the 31st protocol variant and the 31st declared type, HANDLED. Declared after its dispatch case and its relay existed.
    expect(variants).toHaveLength(31);
    const declared = new Set<string>([...DI_HANDLED_MESSAGE_TYPES, ...DI_INERT_MESSAGE_TYPES]);
    expect(declared.size).toBe(31);
    // every protocol variant is declared exactly one way: handled or inert, never both, never neither
    for (const variant of variants) {
      const camel = variant.replace(/^./, (c) => c.toLowerCase());
      expect(declared.has(camel), camel).toBe(true);
    }
    expect(
      DI_HANDLED_MESSAGE_TYPES.filter((t) => (DI_INERT_MESSAGE_TYPES as readonly string[]).includes(t)),
    ).toEqual([]);
    for (const type of Object.keys(INERT_PAYLOADS)) expect(DI_INERT_MESSAGE_TYPES).toContain(type);
  });

  // The generated protocol names every type: each enum in core/asyncapi.yaml is a NAMED schema. A generator-numbered
  // AnonymousSchema_<n> name depends on how many anonymous schemas the generator meets, so an unrelated contract
  // change elsewhere renumbers it, and anything importing it breaks for a reason far from the edit that caused it.
  // This FAILS WHEN THE GENERATED FILE CONTAINS SUCH A NAME. It does not stop anyone writing an inline enum in the
  // yaml; if a contract ever legitimately needs an anonymous schema, this fails and a person decides.
  it('the generated protocol names no generator-numbered AnonymousSchema type', () => {
    const messagesTs = fs.readFileSync(path.join(__dirname, '../../core/src/messages.ts'), 'utf-8');
    expect(messagesTs.match(/\bAnonymousSchema_\d+\b/g) ?? []).toEqual([]);
  });

  for (const [type, payload] of Object.entries(INERT_PAYLOADS)) {
    it(`${type}: no reply, no adapter call, nothing written anywhere`, () => {
      const store = new AgentStateStore();
      const adapter = new FileStateAdapter({ namespace: 'standalone' });
      const spies = [
        vi.spyOn(adapter, 'setSetting'),
        vi.spyOn(adapter, 'saveSeats'),
        vi.spyOn(adapter, 'saveAgents'),
      ];
      store.setAdapter(adapter);
      const send = vi.fn();
      const disposition = handleDiClientMessage(payload, send, {
        store,
        cache: EMPTY_CACHE,
        extensionVersion: 'test',
      });
      expect(disposition).toBe('inert');
      expect(send).not.toHaveBeenCalled();
      for (const spy of spies) expect(spy).not.toHaveBeenCalled();
      expect(treeOf(sandbox)).toEqual(['home']); // no DI root file, nothing in the fake HOME
    });
  }

  it('an unknown type is dropped the same way', () => {
    const send = vi.fn();
    const store = new AgentStateStore();
    expect(
      handleDiClientMessage({ type: 'spawnEverything' }, send, {
        store,
        cache: EMPTY_CACHE,
        extensionVersion: '',
      }),
    ).toBe('unknown');
    expect(send).not.toHaveBeenCalled();
  });
});

describe('DI handshake carries no Claude-shaped capability', () => {
  it('no hooksStatus, no hooksConsentRequest, no provider tool lists, hooks reported off', () => {
    const store = new AgentStateStore();
    store.setAdapter(new FileStateAdapter({ namespace: 'standalone' }));
    const sent: Array<Record<string, unknown>> = [];
    handleDiClientMessage({ type: 'webviewReady' }, (m) => sent.push(m), {
      store,
      cache: EMPTY_CACHE,
      extensionVersion: 'x',
    });
    const types = sent.map((m) => m.type);
    expect(types).not.toContain('hooksStatus');
    expect(types).not.toContain('hooksConsentRequest');
    expect(sent.find((m) => m.type === 'providerCapabilities')).toEqual({
      type: 'providerCapabilities',
      readingTools: [],
      subagentToolNames: [],
    });
    const settings = sent.find((m) => m.type === 'settingsLoaded');
    expect(settings).toMatchObject({
      hooksEnabled: false,
      watchAllSessions: false,
      externalAssetDirectories: [],
    });
    expect(types).toEqual([
      'providerCapabilities',
      'settingsLoaded',
      'areaMappingsLoaded',
      'existingAgents',
      'diSeatPreferencesLoaded',
      'ambientStateLoaded',
      'cityPresentationLoaded',
      'layoutLoaded',
    ]);
  });
});

describe('DI module graph is free of provider / runtime / scanner / consent code (by construction)', () => {
  /** Follow every VALUE import (type-only imports are erased) from the DI entry. */
  function valueImportClosure(entry: string): string[] {
    const seen = new Set<string>();
    const queue = [entry];
    while (queue.length > 0) {
      const file = queue.pop()!;
      if (seen.has(file)) continue;
      seen.add(file);
      const source = fs.readFileSync(file, 'utf-8');
      for (const match of source.matchAll(/^import\s+(?!type\b)[^;]*?from\s+'(\.[^']+)';/gms)) {
        const target = path.resolve(path.dirname(file), match[1].replace(/\.js$/, '.ts'));
        if (fs.existsSync(target)) queue.push(target);
      }
    }
    return [...seen].map((f) => path.relative(SRC, f)).sort();
  }

  it('the DI entry reaches none of the forbidden modules', () => {
    const closure = valueImportClosure(path.join(SRC, 'di/diCli.ts'));
    const forbidden = closure.filter(
      (f) =>
        f.startsWith('providers/') ||
        /^(agentRuntime|fileWatcher|hookEventHandler|transcriptParser|timerManager|clientMessageHandler|server|httpServer|cli)\.ts$/.test(
          f,
        ),
    );
    expect(forbidden).toEqual([]);
    expect(closure).toContain('di/diHttpServer.ts');
    expect(closure).toContain('socketPipe.ts');
  });

  // This pin used to say the office composes "no terminal or launch path", by construction (diCli.ts's header and
  // docs/pixel-agents-office.md said so), and FAILED ON THE CODE CHANGE that composed a launch into the office's own
  // closure, which was the occasion to correct those statements (done in Package A: both now describe the Herdr
  // terminal route). The pin below is the tightened successor. It reads MODULE SPECIFIERS from the PARSED source (the TypeScript parser), so line
  // placement, comments and strings can neither hide an import nor fake one (round 46: a static import
  // after another statement on the same line was invisible to the line-anchored patterns this replaces).
  // Its closure follows every relative VALUE form: static, side-effect, re-export, import-equals, dynamic
  // import() and require() with a literal specifier. Type-only declarations compose no module and are not
  // followed. The process check counts type-only imports too, which is conservative. NOT covered:
  // computed specifiers.
  type Specifier = { specifier: string; typeOnly: boolean };

  /** Every module specifier a source names, in source order, read from its syntax tree. */
  function moduleSpecifiers(source: string): Specifier[] {
    const file = ts.createSourceFile('module.ts', source, ts.ScriptTarget.Latest, true, ts.ScriptKind.TS);
    const found: Specifier[] = [];
    const visit = (node: ts.Node): void => {
      if (ts.isImportDeclaration(node) && ts.isStringLiteral(node.moduleSpecifier)) {
        found.push({ specifier: node.moduleSpecifier.text, typeOnly: node.importClause?.isTypeOnly === true });
      } else if (ts.isExportDeclaration(node) && node.moduleSpecifier !== undefined && ts.isStringLiteral(node.moduleSpecifier)) {
        found.push({ specifier: node.moduleSpecifier.text, typeOnly: node.isTypeOnly });
      } else if (
        ts.isImportEqualsDeclaration(node) &&
        ts.isExternalModuleReference(node.moduleReference) &&
        ts.isStringLiteralLike(node.moduleReference.expression)
      ) {
        found.push({ specifier: node.moduleReference.expression.text, typeOnly: node.isTypeOnly });
      } else if (
        ts.isCallExpression(node) &&
        node.arguments.length > 0 &&
        ts.isStringLiteralLike(node.arguments[0]) &&
        (node.expression.kind === ts.SyntaxKind.ImportKeyword || (ts.isIdentifier(node.expression) && node.expression.text === 'require'))
      ) {
        found.push({ specifier: node.arguments[0].text, typeOnly: false });
      }
      ts.forEachChild(node, visit);
    };
    visit(file);
    return found;
  }

  const PROCESS_MODULES = new Set(['child_process', 'node:child_process', 'cluster', 'node:cluster']);

  /** The relative VALUE specifiers a source imports, type-only declarations excluded: what the closure walk follows. */
  function relativeImports(source: string): string[] {
    return moduleSpecifiers(source)
      .filter((s) => !s.typeOnly && /^\.{1,2}\//.test(s.specifier))
      .map((s) => s.specifier);
  }

  /** Whether a source imports a process-starting module: child_process or cluster, with or without node:, type-only included. */
  function importsProcessModule(source: string): boolean {
    return moduleSpecifiers(source).some((s) => PROCESS_MODULES.has(s.specifier));
  }

  function moduleClosure(entry: string, root: string = SRC): string[] {
    const seen = new Set<string>();
    const queue = [entry];
    while (queue.length > 0) {
      const file = queue.pop()!;
      if (seen.has(file)) continue;
      seen.add(file);
      for (const specifier of relativeImports(fs.readFileSync(file, 'utf-8'))) {
        const base = path.resolve(path.dirname(file), specifier);
        const target = [base.replace(/\.js$/, '.ts'), base, `${base}.ts`, path.join(base, 'index.ts')].find(
          (candidate) => fs.existsSync(candidate) && fs.statSync(candidate).isFile(),
        );
        if (target !== undefined) queue.push(target);
      }
    }
    return [...seen].map((f) => path.relative(root, f)).sort();
  }

  it('the matchers detect every import form, and neither a type-only relative import nor a label string', () => {
    expect(
      relativeImports(
        [
          "import { a } from './herdrLaunch.js';",
          "import './herdrLaunch.js';",
          'import {\n  b,\n  type C,\n} from "./herdrLaunch.js";',
          "export * from './herdrLaunch.js';",
          "export { d } from '../x/herdrLaunch.js';",
          "const e = () => import('./herdrLaunch.js');",
          'const f = () => require(`./herdrLaunch.js`);',
          "import type { G } from './typesOnly.js';",
          "export type { H } from './typesOnly.js';",
        ].join('\n'),
      ),
    ).toEqual([
      './herdrLaunch.js',
      './herdrLaunch.js',
      './herdrLaunch.js',
      './herdrLaunch.js',
      '../x/herdrLaunch.js',
      './herdrLaunch.js',
      './herdrLaunch.js',
    ]);
    for (const form of [
      "import { spawn } from 'child_process';",
      'import * as cp from "node:child_process";',
      "import 'node:child_process';",
      "export { spawn } from 'child_process';",
      "const m = () => import('node:child_process');",
      'const r = () => require(`child_process`);',
      "import cluster from 'node:cluster';",
      "import type { ChildProcess } from 'child_process';",
    ]) {
      expect(importsProcessModule(form), form).toBe(true);
    }
    for (const notAnImport of [
      "    wrap(targets.childProcess, 'child_process', name, (_self, args) => emit('process', `child_process.${name}`, processDetail(name, args)));",
      "const label = 'node:child_process';",
      "import { x } from './child_process_notes.js';",
    ]) {
      expect(importsProcessModule(notAnImport), notAnImport).toBe(false);
    }
  });

  // Round 46: a valid static import placed AFTER ANOTHER STATEMENT on the same line must be seen, by the closure walk AND by the process check.
  it('ROUND 46: a static launch import after another statement on the same line is followed by the closure walk', () => {
    expect(
      relativeImports("import { DiOffice } from './diOffice.js'; import { nodeHerdrLaunchEffects } from './herdrLaunch.js';"),
    ).toEqual(['./diOffice.js', './herdrLaunch.js']);
    expect(relativeImports("/* note */ import { nodeHerdrLaunchEffects } from './herdrLaunch.js';")).toEqual(['./herdrLaunch.js']);
  });

  it('ROUND 46: a process import after another statement on the same line is detected', () => {
    expect(importsProcessModule("import { DiOffice } from './diOffice.js'; import { spawn } from 'node:child_process';")).toBe(true);
    expect(importsProcessModule("export const one = 1; import 'node:child_process';")).toBe(true);
    expect(importsProcessModule("/* note */ import cluster from 'node:cluster';")).toBe(true);
  });

  it('same-line survivors: a type-only launch import is not followed, and neither a path in a string nor a label is a process import', () => {
    expect(
      relativeImports("import { DiOffice } from './diOffice.js'; import type { HerdrRoleBinding } from './herdrLaunch.js';"),
    ).toEqual(['./diOffice.js']);
    expect(relativeImports("import { a } from './a.js'; const p = './herdrLaunch.js';")).toEqual(['./a.js']);
    expect(
      importsProcessModule("import { a } from './a.js'; const label = 'node:child_process'; import { r } from './child_process_notes.js';"),
    ).toBe(false);
  });

  it('ROUND 46, through a real module graph: same-line imports are reached and detected, and same-line survivors are not', () => {
    const root = fs.mkdtempSync(path.join(os.tmpdir(), 'launch-pin-'));
    try {
      const files: Record<string, string> = {
        'entry.ts':
          "export const marker = 1; import { one } from './one.js'; import { launch } from './herdrLaunch.js'; import { q } from './quiet.js'; import type { T } from './typeOnly.js'; const p = './lookalike.js';\nexport { one, launch, q, p };\nexport type { T };\n",
        'one.ts': "export const one = 1; import { spawn } from 'node:child_process';\nexport { spawn };\n",
        'herdrLaunch.ts': 'export const launch = 0;\n',
        'quiet.ts': "export const q = 1; const label = 'child_process'; import { r } from './child_process_notes.js';\nexport { label, r };\n",
        'child_process_notes.ts': 'export const r = 0;\n',
        'typeOnly.ts': 'export type T = number;\n',
        'lookalike.ts': 'export const x = 0;\n',
      };
      for (const [name, text] of Object.entries(files)) fs.writeFileSync(path.join(root, name), text);
      const closure = moduleClosure(path.join(root, 'entry.ts'), root);
      expect(closure).toEqual(['child_process_notes.ts', 'entry.ts', 'herdrLaunch.ts', 'one.ts', 'quiet.ts']);
      expect(closure.filter((f) => importsProcessModule(fs.readFileSync(path.join(root, f), 'utf-8')))).toEqual(['one.ts']);
    } finally {
      fs.rmSync(root, { recursive: true, force: true });
    }
  });

  // THE ENTRY'S PROCESS BOUNDARY, TIGHTENED (Package A, A4). The Herdr terminal route is the approved exception to "the office starts no process", and the exception is EXACTLY this and nothing wider. It
  // replaces the earlier blanket pin (no process-starting module anywhere in the closure, and herdrLaunch never reached) with a pin that is STRICTER about everything except the two named things:
  //   1. ONE module may import a process-starting module, and it is named literally below. A second importer anywhere in the closure fails, under any specifier form `moduleSpecifiers` reads.
  //   2. The launch route (herdrLaunch.ts) is reached by exactly the two named modules, so a new path to it fails.
  //   3. The one process module is imported by exactly the composition, so nothing else can obtain process APIs through it.
  //   4. No escape hatch that would hide a process import from 1 exists ANYWHERE in the closure, the permitted importer included: getBuiltinModule, createRequire (and importing `module`), process.binding,
  //      process.dlopen, and the module.constructor idiom. Each reaches child_process without an import specifier this scan reads, so a scan that only reads specifiers would pass them.
  // The allowlist is not "the whole closure"; it is the process-starting module and the launch route. Unrelated modules may come and go without touching it.
  const PERMITTED_PROCESS_IMPORTER = 'di/herdrProcess.ts';
  const LAUNCH_ROUTE = 'di/herdrLaunch.ts';
  const LAUNCH_ROUTE_IMPORTERS = ['di/herdrRequests.ts', 'di/herdrTarget.ts'];
  const PROCESS_MODULE_IMPORTERS = ['di/diCli.ts'];
  const ESCAPE_HATCH_NAMES = new Set(['getBuiltinModule', 'createRequire']);
  const ESCAPE_HATCH_PROCESS_MEMBERS = new Set(['binding', 'dlopen']);
  const ESCAPE_HATCH_MODULES = new Set(['module', 'node:module']);
  const GLOBAL_OBJECTS = ['globalThis', 'global'];

  /**
   * THE PERMITTED IMPORTER'S EXCEPTION IS NOT "THE WHOLE FILE" (review round 3, finding 3). The one file allowed to import a process-starting module is allowed to import exactly what the launch route
   * needs: ONE static, non-type import of `execFile` and `spawn` (and no other name) from `child_process`. Every other process-module reference in that file, whatever its form (another named import, a
   * default or namespace import, a side-effect import, a re-export, `import()`, `require()`, `import x = require()`, `cluster`, `node:cluster`, a second `child_process` statement) is a violation, so adding
   * `import cluster from 'node:cluster'` to it, or widening its import to `exec`, fails.
   */
  const REQUIRED_PROCESS_IMPORT = { specifier: 'child_process', form: 'named', names: ['execFile', 'spawn'], typeOnly: false } as const;

  interface ProcessImport {
    specifier: string;
    form: 'named' | 'default' | 'namespace' | 'side-effect' | 'reexport' | 'import-equals' | 'dynamic' | 'require';
    names: string[];
    typeOnly: boolean;
  }

  /** Every reference `source` makes to a process-starting module, in every form, read from its syntax tree (comments are not part of it). Type-only forms are reported too, conservatively. */
  function processImportsIn(source: string): ProcessImport[] {
    const file = ts.createSourceFile('module.ts', source, ts.ScriptTarget.Latest, true, ts.ScriptKind.TS);
    const found: ProcessImport[] = [];
    const visit = (node: ts.Node): void => {
      if (ts.isImportDeclaration(node) && ts.isStringLiteral(node.moduleSpecifier) && PROCESS_MODULES.has(node.moduleSpecifier.text)) {
        const specifier = node.moduleSpecifier.text;
        const clause = node.importClause;
        if (clause === undefined) found.push({ specifier, form: 'side-effect', names: [], typeOnly: false });
        else {
          const typeOnly = clause.isTypeOnly;
          if (clause.name !== undefined) found.push({ specifier, form: 'default', names: [clause.name.text], typeOnly });
          const bindings = clause.namedBindings;
          if (bindings !== undefined && ts.isNamespaceImport(bindings)) found.push({ specifier, form: 'namespace', names: [bindings.name.text], typeOnly });
          if (bindings !== undefined && ts.isNamedImports(bindings))
            found.push({ specifier, form: 'named', names: bindings.elements.map((e) => (e.propertyName ?? e.name).text), typeOnly });
        }
      } else if (ts.isExportDeclaration(node) && node.moduleSpecifier !== undefined && ts.isStringLiteral(node.moduleSpecifier) && PROCESS_MODULES.has(node.moduleSpecifier.text)) {
        const clause = node.exportClause;
        found.push({
          specifier: node.moduleSpecifier.text,
          form: 'reexport',
          names: clause !== undefined && ts.isNamedExports(clause) ? clause.elements.map((e) => (e.propertyName ?? e.name).text) : ['*'],
          typeOnly: node.isTypeOnly,
        });
      } else if (
        ts.isImportEqualsDeclaration(node) &&
        ts.isExternalModuleReference(node.moduleReference) &&
        ts.isStringLiteralLike(node.moduleReference.expression) &&
        PROCESS_MODULES.has(node.moduleReference.expression.text)
      ) {
        found.push({ specifier: node.moduleReference.expression.text, form: 'import-equals', names: [node.name.text], typeOnly: node.isTypeOnly });
      } else if (ts.isCallExpression(node) && node.arguments.length > 0 && ts.isStringLiteralLike(node.arguments[0]) && PROCESS_MODULES.has(node.arguments[0].text)) {
        if (node.expression.kind === ts.SyntaxKind.ImportKeyword) found.push({ specifier: node.arguments[0].text, form: 'dynamic', names: [], typeOnly: false });
        else if (ts.isIdentifier(node.expression) && node.expression.text === 'require') found.push({ specifier: node.arguments[0].text, form: 'require', names: [], typeOnly: false });
      }
      ts.forEachChild(node, visit);
    };
    visit(file);
    return found;
  }

  /** Whether a process import is exactly the required one: a static, non-type named import of nothing but execFile and spawn from child_process. */
  const isRequiredProcessImport = (i: ProcessImport): boolean =>
    i.specifier === REQUIRED_PROCESS_IMPORT.specifier &&
    i.form === REQUIRED_PROCESS_IMPORT.form &&
    i.typeOnly === REQUIRED_PROCESS_IMPORT.typeOnly &&
    i.names.length > 0 &&
    i.names.every((name) => (REQUIRED_PROCESS_IMPORT.names as readonly string[]).includes(name));

  /**
   * Every way `source` can reach a process-starting capability WITHOUT a process-module import specifier, as "what", read from its syntax tree (comments are not part of it). It follows ALIASES: `const p =
   * process` and `p = process` (and aliases of aliases, to a fixed point), `globalThis`/`global` and their aliases and `.process` / `['process']` of any of them, `module` and its aliases; and it reads
   * DESTRUCTURING (`const { binding } = process`, `{ binding: b }`, the assignment form, and over an alias), and `Reflect.get(process, 'binding')`-style calls. So `const p = process; p.binding(...)` is as
   * visible as `process.binding(...)`. NOT covered: a name assembled at run time (`process['bind' + 'ing']`), which no static scan can see; the pin says so rather than imply otherwise.
   */
  function escapeHatches(source: string): string[] {
    const file = ts.createSourceFile('module.ts', source, ts.ScriptTarget.Latest, true, ts.ScriptKind.TS);
    const unwrap = (n: ts.Node): ts.Node => {
      let cur = n;
      while (ts.isParenthesizedExpression(cur) || ts.isNonNullExpression(cur) || ts.isAsExpression(cur) || ts.isTypeAssertionExpression(cur) || ts.isSatisfiesExpression(cur)) cur = cur.expression;
      return cur;
    };
    const globals = new Set(GLOBAL_OBJECTS);
    const processes = new Set(['process']);
    const modules = new Set(['module']);
    const memberName = (n: ts.Node): string | null =>
      ts.isPropertyAccessExpression(n) ? n.name.text : ts.isElementAccessExpression(n) && ts.isStringLiteralLike(n.argumentExpression) ? n.argumentExpression.text : null;
    const isGlobal = (n: ts.Node): boolean => {
      const u = unwrap(n);
      return ts.isIdentifier(u) && globals.has(u.text);
    };
    const isProcess = (n: ts.Node): boolean => {
      const u = unwrap(n);
      if (ts.isIdentifier(u)) return processes.has(u.text);
      if (ts.isPropertyAccessExpression(u) || ts.isElementAccessExpression(u)) return memberName(u) === 'process' && isGlobal(u.expression);
      return false;
    };
    const isModule = (n: ts.Node): boolean => {
      const u = unwrap(n);
      return ts.isIdentifier(u) && modules.has(u.text);
    };
    // aliases, to a fixed point
    let changed = true;
    const note = (set: Set<string>, name: string): void => {
      if (!set.has(name)) {
        set.add(name);
        changed = true;
      }
    };
    const alias = (name: string, init: ts.Node): void => {
      if (isGlobal(init)) note(globals, name);
      if (isProcess(init)) note(processes, name);
      if (isModule(init)) note(modules, name);
    };
    const collect = (node: ts.Node): void => {
      if (ts.isVariableDeclaration(node) && ts.isIdentifier(node.name) && node.initializer) alias(node.name.text, node.initializer);
      if (ts.isBinaryExpression(node) && node.operatorToken.kind === ts.SyntaxKind.EqualsToken && ts.isIdentifier(node.left)) alias(node.left.text, node.right);
      ts.forEachChild(node, collect);
    };
    while (changed) {
      changed = false;
      collect(file);
    }

    const found: string[] = [];
    const hatchMember = (owner: ts.Node, name: string | null): void => {
      if (name === null) return;
      if (isProcess(owner) && ESCAPE_HATCH_PROCESS_MEMBERS.has(name)) found.push(`process.${name}`);
      if (isModule(owner) && name === 'constructor') found.push('module.constructor');
    };
    const keyText = (key: ts.Node): string | null => (ts.isIdentifier(key) || ts.isStringLiteralLike(key) ? key.text : null);
    const visit = (node: ts.Node): void => {
      if (ts.isIdentifier(node) && ESCAPE_HATCH_NAMES.has(node.text)) found.push(node.text);
      if (ts.isPropertyAccessExpression(node) || ts.isElementAccessExpression(node)) {
        const name = memberName(node);
        hatchMember(node.expression, name);
        if (name !== null && ESCAPE_HATCH_NAMES.has(name)) found.push(name);
      }
      // const { binding } = process;  const { binding: b } = p;  const { dlopen } = globalThis.process;
      if (ts.isVariableDeclaration(node) && ts.isObjectBindingPattern(node.name) && node.initializer) {
        for (const element of node.name.elements) hatchMember(node.initializer, keyText(element.propertyName ?? element.name));
      }
      // ({ binding } = process);  ({ binding: b } = process);
      if (ts.isBinaryExpression(node) && node.operatorToken.kind === ts.SyntaxKind.EqualsToken && ts.isObjectLiteralExpression(node.left)) {
        for (const property of node.left.properties) {
          if (ts.isShorthandPropertyAssignment(property)) hatchMember(node.right, property.name.text);
          else if (ts.isPropertyAssignment(property)) hatchMember(node.right, keyText(property.name));
        }
      }
      // Reflect.get(process, 'binding'), Object.getOwnPropertyDescriptor(process, 'dlopen')
      if (ts.isCallExpression(node) && node.arguments.length > 1) {
        const owners = node.arguments.filter((a) => isProcess(a) || isModule(a));
        for (const owner of owners) for (const arg of node.arguments) if (ts.isStringLiteralLike(arg)) hatchMember(owner, arg.text);
      }
      ts.forEachChild(node, visit);
    };
    visit(file);
    for (const s of moduleSpecifiers(source)) if (ESCAPE_HATCH_MODULES.has(s.specifier)) found.push(`import of ${s.specifier}`);
    // the same hatch seen by two of the checks above is one finding
    return [...new Set(found)];
  }

  /** The closure-relative paths of the closure's modules that VALUE-import `target` (type-only imports are erased and do not count). */
  function valueImportersOf(target: string, closure: string[], root: string): string[] {
    return closure.filter((file) =>
      relativeImports(fs.readFileSync(path.join(root, file), 'utf-8')).some((specifier) => {
        const base = path.resolve(path.dirname(path.join(root, file)), specifier);
        return [base.replace(/\.js$/, '.ts'), base, `${base}.ts`, path.join(base, 'index.ts')].some((candidate) => path.relative(root, candidate) === target);
      }),
    );
  }

  /** Everything the process boundary forbids in the closure of `entry` under `root`; empty when it holds. */
  function processBoundaryViolations(entry: string, root: string): string[] {
    const closure = moduleClosure(entry, root);
    const read = (file: string): string => fs.readFileSync(path.join(root, file), 'utf-8');
    const violations: string[] = [];
    for (const file of closure) {
      const imports = processImportsIn(read(file));
      if (file !== PERMITTED_PROCESS_IMPORTER) {
        if (imports.length > 0) violations.push(`${file} imports a process-starting module`);
      } else {
        // the exception is the ONE required import, not the file: anything beyond it is named
        for (const i of imports.filter((entry) => !isRequiredProcessImport(entry))) {
          const what = i.form === 'named' ? `{ ${i.names.join(', ')} } from ${i.specifier}` : `${i.specifier} (${i.form}${i.typeOnly ? ', type-only' : ''})`;
          violations.push(`${file} imports ${what}, beyond the required child_process { execFile, spawn }`);
        }
      }
      for (const hatch of escapeHatches(read(file))) violations.push(`${file} uses ${hatch}`);
    }
    if (closure.includes(LAUNCH_ROUTE)) {
      for (const importer of valueImportersOf(LAUNCH_ROUTE, closure, root))
        if (!LAUNCH_ROUTE_IMPORTERS.includes(importer)) violations.push(`${importer} reaches the launch route`);
    }
    if (closure.includes(PERMITTED_PROCESS_IMPORTER)) {
      for (const importer of valueImportersOf(PERMITTED_PROCESS_IMPORTER, closure, root))
        if (!PROCESS_MODULE_IMPORTERS.includes(importer)) violations.push(`${importer} reaches the process module`);
    }
    return violations.sort();
  }

  it('the DI entry\'s process boundary holds: the ONE named process-starting module, the launch route reached by exactly its two named importers, the process module reached only by the composition, and no escape hatch', () => {
    const entry = path.join(SRC, 'di/diCli.ts');
    const closure = moduleClosure(entry);
    expect(valueImportClosure(entry).filter((f) => !closure.includes(f))).toEqual([]);
    // the earlier anchors, unchanged
    expect(closure).toContain('di/diHttpServer.ts');
    expect(closure).toContain('socketPipe.ts');
    // the pin is not vacuous: the two named things are really in the closure, and ARE what imports a process module
    expect(closure).toContain(LAUNCH_ROUTE);
    expect(closure).toContain(PERMITTED_PROCESS_IMPORTER);
    expect(closure.filter((f) => importsProcessModule(fs.readFileSync(path.join(SRC, f), 'utf-8')))).toEqual([PERMITTED_PROCESS_IMPORTER]);
    // and what the permitted importer imports is EXACTLY the required import: one static named import of execFile and spawn from child_process, nothing more
    expect(processImportsIn(fs.readFileSync(path.join(SRC, PERMITTED_PROCESS_IMPORTER), 'utf-8'))).toEqual([
      { specifier: 'child_process', form: 'named', names: ['execFile', 'spawn'], typeOnly: false },
    ]);
    expect(valueImportersOf(LAUNCH_ROUTE, closure, SRC).sort()).toEqual([...LAUNCH_ROUTE_IMPORTERS].sort());
    expect(valueImportersOf(PERMITTED_PROCESS_IMPORTER, closure, SRC)).toEqual(PROCESS_MODULE_IMPORTERS);
    // the route itself imports no process module and no escape hatch
    expect(importsProcessModule(fs.readFileSync(path.join(SRC, LAUNCH_ROUTE), 'utf-8'))).toBe(false);
    expect(closure.flatMap((f) => escapeHatches(fs.readFileSync(path.join(SRC, f), 'utf-8')).map((h) => `${f} uses ${h}`))).toEqual([]);
    expect(processBoundaryViolations(entry, SRC)).toEqual([]);
  });

  describe('the process boundary is not vacuous: each way past it FAILS, on a synthetic module graph', () => {
    // the shape of the real file's one required import: execFile and spawn, named, static, from child_process
    const permitted = "import { execFile, spawn } from 'child_process';\nexport const start = [execFile, spawn];\n";
    /** A graph shaped like the real one (di/diCli.ts composing the route and its one process module), plus `extra` files; returns what the boundary says. */
    function boundary(extra: Record<string, string>, over: Record<string, string> = {}): string[] {
      const root = fs.mkdtempSync(path.join(os.tmpdir(), 'process-boundary-'));
      try {
        const files: Record<string, string> = {
          'di/diCli.ts': "import { start } from './herdrProcess.js';\nimport { route } from './herdrRequests.js';\nimport { other } from './other.js';\nexport { start, route, other };\n",
          'di/herdrProcess.ts': permitted,
          'di/herdrRequests.ts': "import { launch } from './herdrLaunch.js';\nexport const route = launch;\n",
          'di/herdrTarget.ts': "import { launch } from './herdrLaunch.js';\nexport const target = launch;\n",
          'di/herdrLaunch.ts': 'export const launch = 1;\n',
          'di/other.ts': 'export const other = 2;\n',
          ...extra,
          ...over,
        };
        for (const [name, text] of Object.entries(files)) {
          fs.mkdirSync(path.dirname(path.join(root, name)), { recursive: true });
          fs.writeFileSync(path.join(root, name), text);
        }
        return processBoundaryViolations(path.join(root, 'di/diCli.ts'), root);
      } finally {
        fs.rmSync(root, { recursive: true, force: true });
      }
    }

    it('the clean graph has no violation (so the cases below fail for their own reason)', () => {
      expect(boundary({})).toEqual([]);
    });

    it('a SECOND module importing child_process fails, wherever in the closure it sits, under every specifier form', () => {
      for (const importText of [
        "import { exec } from 'child_process';",
        "import * as cp from 'node:child_process';",
        "import 'node:child_process';",
        "export { spawn } from 'child_process';",
        "const run = () => import('node:child_process');",
        'const r = () => require(`child_process`);',
        "import cluster from 'node:cluster';",
      ]) {
        expect(boundary({ 'di/other.ts': `${importText}\nexport const other = 2;\n` }), importText).toEqual(['di/other.ts imports a process-starting module']);
        expect(boundary({ 'deep/probe.ts': `${importText}\nexport const probe = 3;\n`, 'di/other.ts': "import { probe } from '../deep/probe.js';\nexport const other = probe;\n" }), importText).toEqual([
          'deep/probe.ts imports a process-starting module',
        ]);
      }
    });

    it('the permitted importer\'s NAME is not enough: the same name in another directory is a second importer', () => {
      // the lookalike IS in the closure (di/other.ts imports it), so it is scanned, and it is not THE permitted file
      expect(
        boundary({ 'di/other.ts': "import { start } from '../elsewhere/di/herdrProcess.js';\nexport const other = start;\n", 'elsewhere/di/herdrProcess.ts': permitted }),
      ).toEqual(['elsewhere/di/herdrProcess.ts imports a process-starting module']);
    });

    // REVIEW ROUND 3, FINDING 3: the permitted importer's exception is the ONE required import, not the file. These are the overlays that used to pass.
    describe('the permitted importer is allowed ONLY its required import: any other process reference in that same file FAILS', () => {
      const START = "export const start = [execFile, spawn];\n";
      const overlay = (text: string, importLine = "import { execFile, spawn } from 'child_process';\n") => boundary({}, { 'di/herdrProcess.ts': `${importLine}${text}\n${START}` });

      it('the clean required import has no violation', () => {
        expect(overlay('')).toEqual([]);
        expect(overlay('', "import { spawn as run, execFile as ef } from 'child_process';\nconst execFile = ef; const spawn = run;\n")).toEqual([]);
      });

      it("the Reviewer's overlay: `import cluster from 'node:cluster'` added to the permitted file FAILS", () => {
        expect(overlay("import cluster from 'node:cluster';\nexport const c = cluster;")).toEqual([
          'di/herdrProcess.ts imports node:cluster (default), beyond the required child_process { execFile, spawn }',
        ]);
      });

      it.each([
        ['cluster, plain', "import cluster from 'cluster';", 'cluster (default)'],
        ['a namespace import of child_process', "import * as cp from 'child_process';", 'child_process (namespace)'],
        ['a default import of child_process', "import cp from 'child_process';", 'child_process (default)'],
        ['a side-effect import of child_process', "import 'child_process';", 'child_process (side-effect)'],
        ['a second child_process statement, same names', "import { spawn as again } from 'node:child_process';", 'node:child_process'],
        ['a type-only import of a process module', "import type { ChildProcess } from 'child_process';", 'child_process'],
        ['a re-export from child_process', "export { fork } from 'child_process';", 'child_process (reexport'],
        ['a dynamic import()', "export const d = () => import('child_process');", 'child_process (dynamic)'],
        ['a require()', "export const r = () => require('child_process');", 'child_process (require)'],
        ['an import-equals require', "import cpq = require('child_process');", 'child_process (import-equals)'],
      ])('%s FAILS', (_name, line, words) => {
        const violations = overlay(`${line}\nexport const extra = 1;`);
        expect(violations.length).toBeGreaterThan(0);
        expect(violations.every((v) => v.startsWith('di/herdrProcess.ts imports '))).toBe(true);
        expect(violations.join('\n')).toContain(words);
      });

      it.each([
        ['exec', "import { execFile, spawn, exec } from 'child_process';"],
        ['fork', "import { execFile, spawn, fork } from 'child_process';"],
        ['execSync', "import { execFile, spawn, execSync } from 'child_process';"],
        ['spawnSync alone, without the required names', "import { spawnSync } from 'child_process';"],
      ])('widening the required import with %s FAILS, naming the extra name', (_name, importLine) => {
        const violations = overlay('', importLine);
        expect(violations).toHaveLength(1);
        expect(violations[0]).toMatch(/^di\/herdrProcess\.ts imports \{ .* \} from child_process, beyond the required child_process \{ execFile, spawn \}$/);
      });

      it('the required import is still checked for its NAMES, not just its specifier', () => {
        expect(overlay('', "import { execFile } from 'child_process';\nconst spawn = execFile;\n")).toEqual([]);
        expect(overlay('', "import { exec } from 'child_process';\nconst execFile = exec; const spawn = exec;\n")).toHaveLength(1);
      });
    });

    // REVIEW ROUND 3, FINDING 3: aliased and destructured access used to evade the scan.
    describe('aliased and destructured access to the escape hatches FAILS, as direct access does', () => {
      it.each([
        ['a plain alias: const p = process; p.binding()', "const p = process;\nexport const other = p.binding('spawn_sync');\n", 'process.binding'],
        ['an alias of an alias', "const a = process;\nconst b = a;\nexport const other = b.dlopen;\n", 'process.dlopen'],
        ['a later assignment alias', "let q: any;\nq = process;\nexport const other = q.binding('x');\n", 'process.binding'],
        ['an element-access on an alias', "const p = process;\nexport const other = p['binding']('x');\n", 'process.binding'],
        ['an alias via globalThis', "const g = globalThis;\nexport const other = g.process.binding('x');\n", 'process.binding'],
        ['globalThis.process directly', "export const other = globalThis.process.dlopen;\n", 'process.dlopen'],
        ["globalThis['process']", "export const other = globalThis['process'].binding('x');\n", 'process.binding'],
        ['a parenthesised / cast alias', "const p = (process as any);\nexport const other = p.binding('x');\n", 'process.binding'],
        ['destructuring: const { binding } = process', "const { binding } = process;\nexport const other = binding('x');\n", 'process.binding'],
        ['renamed destructuring: { binding: b }', "const { binding: b } = process;\nexport const other = b('x');\n", 'process.binding'],
        ['destructuring dlopen from globalThis.process', "const { dlopen } = globalThis.process;\nexport const other = dlopen;\n", 'process.dlopen'],
        ['destructuring from an alias', "const p = process;\nconst { binding } = p;\nexport const other = binding;\n", 'process.binding'],
        ['assignment destructuring', "let b: any;\n({ binding: b } = process);\nexport const other = b;\n", 'process.binding'],
        ['shorthand assignment destructuring', "let binding: any;\n({ binding } = process);\nexport const other = binding;\n", 'process.binding'],
        ['a module alias: m.constructor', "const m = module;\nexport const other = m.constructor._load('child_process');\n", 'module.constructor'],
        ['destructuring constructor from module', "const { constructor: c } = module;\nexport const other = c;\n", 'module.constructor'],
        ['Reflect.get on process', "export const other = Reflect.get(process, 'binding');\n", 'process.binding'],
        ['getOwnPropertyDescriptor on an alias', "const p = process;\nexport const other = Object.getOwnPropertyDescriptor(p, 'dlopen');\n", 'process.dlopen'],
        ['destructuring getBuiltinModule under another name', "const { getBuiltinModule: g } = process;\nexport const other = g;\n", 'getBuiltinModule'],
      ])('%s', (_name, text, hatch) => {
        const violations = boundary({ 'di/other.ts': text });
        expect(violations, text).toContain(`di/other.ts uses ${hatch}`);
        expect(violations.every((v) => v.startsWith('di/other.ts')), text).toBe(true);
      });

      it('the same aliased access in the permitted importer fails too', () => {
        const violations = boundary({}, { 'di/herdrProcess.ts': `${permitted}const p = process;\nexport const sneaky = p.binding('spawn_sync');\n` });
        expect(violations).toEqual(['di/herdrProcess.ts uses process.binding']);
      });

      it('ordinary use of process is NOT a hatch (the real closure uses process.env, process.exit and process.on)', () => {
        expect(
          boundary({
            'di/other.ts': "const p = process;\nconst { env, argv } = process;\nexport const other = [p.env.HOME, env, argv, process.exit, process.on, process.getuid?.(), globalThis.process.pid, module.exports];\n",
          }),
        ).toEqual([]);
      });
    });

    it('a process import that moves into the route itself, or into any other herdr module, fails', () => {
      expect(boundary({ 'di/herdrLaunch.ts': "import { spawn } from 'child_process';\nexport const launch = spawn;\n" })).toEqual(['di/herdrLaunch.ts imports a process-starting module']);
      expect(boundary({ 'di/herdrRequests.ts': "import { spawn } from 'node:child_process';\nexport const route = spawn;\n" })).toEqual(['di/herdrRequests.ts imports a process-starting module']);
    });

    it('a new path to the launch route fails; a type-only import of it does not (it is erased)', () => {
      expect(boundary({ 'di/other.ts': "import { launch } from './herdrLaunch.js';\nexport const other = launch;\n" })).toEqual(['di/other.ts reaches the launch route']);
      expect(boundary({ 'di/other.ts': "import type { launch } from './herdrLaunch.js';\nexport type Other = typeof launch;\nexport const other = 2;\n" })).toEqual([]);
    });

    it('a new path to the process module fails: only the composition may import it', () => {
      expect(boundary({ 'di/other.ts': "import { start } from './herdrProcess.js';\nexport const other = start;\n" })).toEqual(['di/other.ts reaches the process module']);
      expect(boundary({ 'di/herdrRequests.ts': "import { start } from './herdrProcess.js';\nexport const route = start;\n" })).toEqual(['di/herdrRequests.ts reaches the process module']);
    });

    it.each([
      ['getBuiltinModule', "export const other = process.getBuiltinModule('node:child_process');\n", 'getBuiltinModule'],
      ['a bare getBuiltinModule reference', 'const g = process.getBuiltinModule;\nexport const other = g;\n', 'getBuiltinModule'],
      ['an element-access getBuiltinModule', "export const other = process['getBuiltinModule']('child_process');\n", 'getBuiltinModule'],
      ['createRequire', "import { createRequire } from 'node:module';\nexport const other = createRequire(import.meta.url)('child_process');\n", 'createRequire'],
      ['importing the module module at all', "import * as m from 'module';\nexport const other = m;\n", 'import of module'],
      ['process.binding', "export const other = process.binding('spawn_sync');\n", 'process.binding'],
      ['process.dlopen', 'export const other = process.dlopen;\n', 'process.dlopen'],
      ['an element-access process.binding', "export const other = process['binding']('spawn_sync');\n", 'process.binding'],
      ['module.constructor', "export const other = module.constructor._load('child_process');\n", 'module.constructor'],
    ])('the escape hatch %s fails, even in a module with no process import specifier', (_name, text, hatch) => {
      const violations = boundary({ 'di/other.ts': text });
      expect(violations).toContain(`di/other.ts uses ${hatch}`);
      expect(violations.every((v) => v.startsWith('di/other.ts'))).toBe(true);
    });

    it('an escape hatch fails in the PERMITTED importer too: its one exception is the import, nothing else', () => {
      expect(boundary({}, { 'di/herdrProcess.ts': `${permitted}export const sneaky = process.getBuiltinModule('node:child_process');\n` })).toEqual(['di/herdrProcess.ts uses getBuiltinModule']);
    });

    it('comments and strings that merely name an escape hatch are not hatches (the scan reads syntax, not text)', () => {
      expect(boundary({ 'di/other.ts': "// process.getBuiltinModule, createRequire, process.binding, module.constructor\nexport const other = 'process.binding createRequire';\n" })).toEqual([]);
    });
  });
});

describe('write-bearing DI messages: malformed or oversized input is REFUSED, nothing written', () => {
  const VALID = { version: 1, cols: 2, rows: 1, tiles: [1, 1], furniture: [] };
  const handle = (msg: Record<string, unknown>) => {
    const store = new AgentStateStore();
    const adapter = new FileStateAdapter({ namespace: 'standalone' });
    const spies = [vi.spyOn(adapter, 'setSetting'), vi.spyOn(adapter, 'saveSeats')];
    store.setAdapter(adapter);
    const send = vi.fn();
    const disposition = handleDiClientMessage(msg, send, {
      store,
      cache: EMPTY_CACHE,
      extensionVersion: 'test',
    });
    return { disposition, send, spies };
  };

  it('a valid layout is written under the DI root', () => {
    expect(handle({ type: 'saveLayout', layout: VALID }).disposition).toBe('handled');
    expect(treeOf(sandbox)).toContain(path.join('di-root', 'layout.json'));
  });

  const badLayouts: Record<string, unknown> = {
    'not an object': [1, 2],
    'unknown field': { ...VALID, extra: true },
    'version 2': { ...VALID, version: 2 },
    'cols 65 with a short tiles array': { ...VALID, cols: 65, tiles: new Array(64).fill(1) },
    [`cols ${DI_LAYOUT_MAX_DIM + 1} (over the per-axis world ceiling)`]: {
      ...VALID,
      cols: DI_LAYOUT_MAX_DIM + 1,
      rows: 1,
      tiles: new Array(DI_LAYOUT_MAX_DIM + 1).fill(1),
    },
    [`rows ${DI_LAYOUT_MAX_DIM + 1} (over the per-axis world ceiling)`]: {
      ...VALID,
      cols: 1,
      rows: DI_LAYOUT_MAX_DIM + 1,
      tiles: new Array(DI_LAYOUT_MAX_DIM + 1).fill(1),
    },
    [`${JUST_OVER_CELLS.cols} x ${JUST_OVER_CELLS.rows} (each axis within the ceiling, cells over the total-cell cap)`]:
      tilesOnly(JUST_OVER_CELLS),
    'cols 0': { ...VALID, cols: 0, tiles: [] },
    'large world, tiles one short': {
      ...VALID,
      cols: 176,
      rows: 112,
      tiles: new Array(176 * 112 - 1).fill(1),
    },
    'large world, tileColors one long': {
      ...VALID,
      cols: 176,
      rows: 112,
      tiles: new Array(176 * 112).fill(1),
      tileColors: new Array(176 * 112 + 1).fill(null),
    },
    'large world, carpetTiles wrong length': {
      ...VALID,
      cols: 176,
      rows: 112,
      tiles: new Array(176 * 112).fill(1),
      carpetTiles: new Array(64 * 64).fill(null),
    },
    'large world, areaTiles wrong length': {
      ...VALID,
      cols: 176,
      rows: 112,
      tiles: new Array(176 * 112).fill(1),
      areas: [],
      areaTiles: new Array(176 * 111).fill(null),
    },
    'furniture col beyond twice the world ceiling': {
      ...VALID,
      furniture: [{ uid: 'a', type: 'X', col: 2 * DI_LAYOUT_MAX_DIM + 1, row: 0 }],
    },
    'pet with an unknown key': { ...VALID, pets: [{ id: 'p', petType: 0, name: 'x' }] },
    'pet type not an integer': { ...VALID, pets: [{ id: 'p', petType: 0.5 }] },
    'tiles length mismatch': { ...VALID, tiles: [1] },
    'tile value 999': { ...VALID, tiles: [1, 999] },
    'furniture item extra key': {
      ...VALID,
      furniture: [{ uid: 'a', type: 'DESK_FRONT', col: 0, row: 0, script: 'x' }],
    },
    'furniture uid too long': {
      ...VALID,
      furniture: [{ uid: 'u'.repeat(DI_ID_MAX_LENGTH + 1), type: 'DESK_FRONT', col: 0, row: 0 }],
    },
    'too much furniture': {
      ...VALID,
      furniture: Array.from({ length: DI_LAYOUT_MAX_FURNITURE + 1 }, (_, i) => ({
        uid: `f${i}`,
        type: 'X',
        col: 0,
        row: 0,
      })),
    },
    'tileColors wrong length': { ...VALID, tileColors: [null] },
    'colour component not finite': {
      ...VALID,
      tileColors: [null, { h: 1, s: 1, b: 1, c: Number.NaN }],
    },
    'area colour not hex': { ...VALID, areas: [{ label: 'A', color: 'red' }] },
    'areaTiles label not defined': { ...VALID, areas: [], areaTiles: ['A', null] },
  };
  for (const [name, layout] of Object.entries(badLayouts)) {
    it(`saveLayout REFUSED: ${name}`, () => {
      const { disposition } = handle({ type: 'saveLayout', layout });
      expect(typeof disposition).toBe('object');
      expect(treeOf(sandbox)).toEqual(['home']); // nothing written, anywhere
    });
  }

  it('saveAgentSeats: valid accepted; malformed REFUSED with no adapter write', () => {
    expect(
      handle({ type: 'saveAgentSeats', seats: { 7: { palette: 2, hueShift: 45, seatId: 's' } } })
        .disposition,
    ).toBe('handled');
    for (const seats of [
      [1],
      { abc: {} },
      { 7: { palette: 65 } },
      { 7: { seatId: 'x'.repeat(200) } },
      { 7: { palette: 1, extra: 1 } },
      Object.fromEntries(Array.from({ length: 1_025 }, (_, i) => [String(i), {}])),
    ]) {
      const { disposition, spies } = handle({ type: 'saveAgentSeats', seats });
      expect(typeof disposition).toBe('object');
      for (const spy of spies) expect(spy).not.toHaveBeenCalled();
    }
  });

  it('saveAgentSeats as getPersistableSeats builds it — an UNSEATED character (seatId null) — is ACCEPTED and round-trips', () => {
    // OfficeState.getPersistableSeats(): every non-subagent character, seatId string | null.
    const seats = {
      1: { palette: 0, hueShift: 0, seatId: 'di-band-0-pod-0-seat-a' },
      2: { palette: 3, hueShift: 135, seatId: null },
    };
    const store = new AgentStateStore();
    const adapter = new FileStateAdapter({ namespace: 'standalone' });
    store.setAdapter(adapter);
    const disposition = handleDiClientMessage({ type: 'saveAgentSeats', seats }, vi.fn(), {
      store,
      cache: EMPTY_CACHE,
      extensionVersion: 'test',
    });
    expect(disposition).toBe('handled'); // the WHOLE message, not refused
    expect(adapter.loadSeats()).toEqual({
      '1': { palette: 0, hueShift: 0, seatId: 'di-band-0-pod-0-seat-a' },
      '2': { palette: 3, hueShift: 135, seatId: null },
    });
  });

  it('preferences: a non-boolean flag or an oversized version is REFUSED, no setting written', () => {
    for (const msg of [
      { type: 'setSoundEnabled', enabled: 'yes' },
      { type: 'setShowAreas', enabled: 1 },
      { type: 'setLastSeenVersion', version: 'v'.repeat(1_000) },
      { type: 'setLastSeenVersion', version: 3 },
    ]) {
      const { disposition, spies } = handle(msg);
      expect(typeof disposition).toBe('object');
      for (const spy of spies) expect(spy).not.toHaveBeenCalled();
    }
  });

  it('a layout wider than the former 64 ceiling is now ACCEPTED (65 x 1)', () => {
    expect(
      validateDiLayout({ ...VALID, cols: 65, rows: 1, tiles: new Array(65).fill(1) }),
    ).toBeNull();
  });

  it('the per-axis ceiling is 384 and the total-cell cap 110,400 (independent): DIM x DIM is REFUSED for its cells, cap-size layouts validate', () => {
    expect(DI_LAYOUT_MAX_DIM).toBe(384);
    expect(DI_LAYOUT_MAX_CELLS).toBe(110_400);
    // independent of the axis bound: DIM x DIM is NOT admitted
    expect(DI_LAYOUT_MAX_CELLS).toBeLessThan(DI_LAYOUT_MAX_DIM * DI_LAYOUT_MAX_DIM);
    const full = ({ cols, rows }: Shape) => {
      const cells = cols * rows;
      return {
        version: 1,
        cols,
        rows,
        tiles: new Array(cells).fill(1),
        furniture: [{ uid: 'wall-item', type: 'CLOCK', col: 24, row: -1 }], // negative rows stay legal
        tileColors: new Array(cells).fill(null),
        carpetTiles: new Array(cells).fill(null),
        areas: [],
        areaTiles: new Array(cells).fill(null),
      };
    };
    expect(validateDiLayout(full(DIM_SQUARED))).toBe(
      `layout cols x rows is ${DI_LAYOUT_MAX_DIM * DI_LAYOUT_MAX_DIM} cells, over ${DI_LAYOUT_MAX_CELLS}`,
    );
    expect(validateDiLayout(full(JUST_OVER_CELLS))).toBe(cellCapReason(JUST_OVER_CELLS));
    // exactly the cell cap, and the widest / tallest grids within it, all validate
    expect(CAP.cols * CAP.rows).toBe(DI_LAYOUT_MAX_CELLS);
    for (const shape of [CAP, WIDEST, TALLEST, metropolisShape()])
      expect(validateDiLayout(full(shape)), `${shape.cols} x ${shape.rows}`).toBeNull();
  });

  for (const shape of [CAP, WIDEST, metropolisShape()]) {
    it(
      `the bounds are consistent: a MAXIMAL legitimate ${shape.cols}x${shape.rows} layout (every structural cap at once) validates AND fits the WS payload cap with ~2x headroom`,
      () => {
        const layout = maximalLegitimate(shape);
        expect(validateDiLayout(layout)).toBeNull();
        const frame = Buffer.byteLength(JSON.stringify({ type: 'saveLayout', layout }));
        expect(frame).toBeLessThan(DI_WS_MAX_PAYLOAD_BYTES);
        // ~2x headroom over the maximal legitimate frame, not an accidental fit.
        expect(frame * 2).toBeLessThanOrEqual(DI_WS_MAX_PAYLOAD_BYTES * 1.05);
      },
      HEAVY_MS,
    );
  }

  // F2 (review round 1): ids and labels are bounded in CHARACTERS and areaTiles carries a label per
  // cell, so UTF-8 text can make a structurally valid layout too big to TRANSPORT. Admission must
  // measure UTF-8 bytes, so such a layout is refused BY ADMISSION, never reaching the transport
  // ceiling (1009). At the raised caps a 3-byte label on every cell no longer crosses the ceiling on
  // its own (asserted below), and since the carpeted-cell cap (LAYOUT_MAX_CARPET_CELLS) even every
  // colour at its longest spelling is not enough: the furniture ids are also made of characters
  // JSON escapes to six bytes. The three-byte labels still make the CHARACTER count fit while the
  // UTF-8 BYTE count does not — the property under test.
  const FRAME_OVER = new RegExp(
    `save frame is \\d+ bytes \\(UTF-8\\), over ${DI_WS_MAX_PAYLOAD_BYTES}$`,
  );
  /** 128 three-byte characters (384 UTF-8 bytes), within the 128-char id bound; unique per k. */
  const cjkLabel = (k: number) =>
    String.fromCharCode(0x4e00 + k) + '界'.repeat(DI_ID_MAX_LENGTH - 1);
  it(
    'a UTF-8 maximal layout (structurally valid) is REFUSED by admission for its BYTES',
    () => {
      const base = maximalLegitimate(CAP, { label: cjkLabel, colour: LONG_COLOUR });
      const layout = {
        ...base,
        furniture: (base.furniture as Array<Record<string, unknown>>).map((f, k) => ({
          ...f,
          uid: heavyLabel(k),
          type: heavyLabel(k),
        })),
      };
      const text = JSON.stringify({ type: 'saveLayout', layout });
      // it would FIT if characters were counted; in UTF-8 bytes the transport would close 1009
      expect(text.length).toBeLessThanOrEqual(DI_WS_MAX_PAYLOAD_BYTES);
      expect(Buffer.byteLength(text)).toBeGreaterThan(DI_WS_MAX_PAYLOAD_BYTES);
      expect(validateDiLayout(layout)).toMatch(FRAME_OVER);
      // and the six-byte-escaped fixture every entry path refuses is refused here too
      const escaped = overBytes();
      expect(
        Buffer.byteLength(JSON.stringify({ type: 'saveLayout', layout: escaped })),
      ).toBeGreaterThan(DI_WS_MAX_PAYLOAD_BYTES);
      expect(validateDiLayout(escaped)).toMatch(FRAME_OVER);
    },
    HEAVY_MS,
  );

  it(
    'a 3-byte label on every cell of the maximal layout is no longer enough to cross the raised ceiling: ADMITTED, and it fits',
    () => {
      const layout = maximalLegitimate(CAP, { label: cjkLabel });
      expect(validateDiLayout(layout)).toBeNull();
      const frame = Buffer.byteLength(JSON.stringify({ type: 'saveLayout', layout }));
      expect(frame).toBeLessThanOrEqual(DI_WS_MAX_PAYLOAD_BYTES);
    },
    HEAVY_MS,
  );

  it(
    'admission == transport: at the byte boundary, admitted layouts fit and the next one is refused',
    () => {
      // Grow a heavy (six-byte-escaped) area label over the cells of the maximal cap-size layout
      // until the frame crosses the ceiling: the last admitted layout's frame fits, the first
      // refused one's does not (bytes, not structure). Binary search, so no count is hand-tuned.
      const label = heavyLabel();
      const base = maximalLegitimate(CAP);
      const cells = CAP.cols * CAP.rows;
      const labelled = (n: number) => ({
        ...base,
        areas: [
          { label, color: AREA_COLOUR },
          { label: 'a', color: AREA_COLOUR },
        ],
        areaTiles: Array.from({ length: cells }, (_, i) => (i < n ? label : 'a')),
      });
      const lo = largestAdmitted(0, cells, (n) => validateDiLayout(labelled(n)) === null);
      const hi = lo + 1;
      const fits = Buffer.byteLength(JSON.stringify({ type: 'saveLayout', layout: labelled(lo) }));
      const over = Buffer.byteLength(JSON.stringify({ type: 'saveLayout', layout: labelled(hi) }));
      expect(validateDiLayout(labelled(lo))).toBeNull();
      expect(fits).toBeLessThanOrEqual(DI_WS_MAX_PAYLOAD_BYTES);
      expect(validateDiLayout(labelled(hi))).toMatch(FRAME_OVER);
      expect(over).toBeGreaterThan(DI_WS_MAX_PAYLOAD_BYTES);
    },
    HEAVY_MS,
  );

  it('the bounds are consistent: a MAXIMAL legitimate 64x64 layout (the former ceiling, every other cap at its maximum) validates AND fits the WS payload cap', () => {
    const layout = maximalLegitimate({ cols: 64, rows: 64 });
    expect((layout.furniture as unknown[]).length).toBe(DI_LAYOUT_MAX_FURNITURE);
    expect(validateDiLayout(layout)).toBeNull();
    const frame = JSON.stringify({ type: 'saveLayout', layout });
    expect(Buffer.byteLength(frame)).toBeLessThan(DI_WS_MAX_PAYLOAD_BYTES);
  });
});

describe('a FRESH DI office shows its Mission zones by default; the user choice persists after', () => {
  it('no config yet -> Show Areas recorded ON once; an existing config is never touched', () => {
    const fresh = { setSetting: vi.fn() };
    initDiDefaults(fresh, false);
    expect(fresh.setSetting).toHaveBeenCalledWith('pixel-agents.showAreas', true);
    const existing = { setSetting: vi.fn() };
    initDiDefaults(existing, true);
    expect(existing.setSetting).not.toHaveBeenCalled();
  });

  it('OBSERVED CAUSE of the log-14 failure: on a fresh root getSetting IGNORES its default (the config default is false)', () => {
    const adapter = new FileStateAdapter({ namespace: 'standalone' });
    expect(adapter.getSetting('pixel-agents.showAreas', true)).toBe(false); // why a handshake default could not work
    initDiDefaults(adapter, false);
    expect(adapter.getSetting('pixel-agents.showAreas', false)).toBe(true); // recorded once at creation
  });
});

describe('diDeliveryRequest: checked here, then handed ONCE to the delivery relay, never acted on here (b2b)', () => {
  const ID = 'prd-0123456789abcdef01234567';
  const msg = { type: 'diDeliveryRequest', seq: 3, op: 'status', deliveryId: ID };
  const ctxWith = (deliveryRequest?: (m: Record<string, unknown>, s: (x: Record<string, unknown>) => void) => void) => ({
    store: new AgentStateStore(),
    cache: EMPTY_CACHE,
    extensionVersion: 'test',
    ...(deliveryRequest ? { deliveryRequest } : {}),
  });

  it('a valid diDeliveryRequest is HANDLED by handing it, exactly once, to the delivery relay', () => {
    const seen: unknown[] = [];
    const send = vi.fn();
    const disposition = handleDiClientMessage(msg, send, ctxWith((m, s) => seen.push([m, s])));
    expect(disposition).toBe('handled');
    expect(seen).toEqual([[msg, send]]);
    expect(send).not.toHaveBeenCalled(); // the relay answers, not the dispatcher
  });

  // ROUND 48: this test used to assert SILENCE here ("nothing is sent"), pinning a violation of the contract's EXACTLY ONE reply. It now
  // asserts the requirement: exactly one reply, correlated by seq and op, saying nothing was changed.
  it('with no delivery relay in this office it is refused, and EXACTLY ONE correlated unavailable reply is sent', () => {
    const send = vi.fn();
    expect(handleDiClientMessage(msg, send, ctxWith())).toEqual({ refused: 'no delivery adapter in this office' });
    expect(send.mock.calls).toEqual([
      [
        {
          type: 'diDeliveryReply',
          seq: 3,
          op: 'status',
          state: 'unavailable',
          problem: 'delivery_not_configured',
          reason: 'This office was started without a delivery adapter.',
          answer: null,
        },
      ],
    ]);
  });

  // OWED ITEM 1: this test used to send { op: 'advance' } and expect a reply naming op 'status', an op the message never carried. The
  // correlation domain now holds here as it does for diMissionRequest: a malformed message whose type, seq and op are valid gets EXACTLY
  // ONE refusal carrying ITS OWN seq and op; one whose seq or op is not valid cannot be correlated, and gets no reply.
  it('a malformed diDeliveryRequest inside the correlation domain is refused before the relay, with EXACTLY ONE refusal carrying its own seq and op', () => {
    const toRelay = vi.fn();
    const send = vi.fn();
    const disposition = handleDiClientMessage(
      { ...msg, deliveryId: 'prd-0123' },
      send,
      ctxWith(toRelay),
    );
    expect(
      typeof disposition === 'object' && disposition !== null && 'refused' in disposition,
    ).toBe(true);
    expect(toRelay).not.toHaveBeenCalled();
    expect(send).toHaveBeenCalledTimes(1);
    expect(send.mock.calls[0][0]).toMatchObject({
      type: 'diDeliveryReply',
      seq: 3,
      op: 'status',
      state: 'refused',
      problem: 'request_invalid',
      answer: null,
    });
  });

  it('a diDeliveryRequest whose op is not one of the two cannot be correlated: it is refused, with NO reply and nothing invented', () => {
    const toRelay = vi.fn();
    const send = vi.fn();
    const disposition = handleDiClientMessage({ ...msg, op: 'advance' }, send, ctxWith(toRelay));
    expect(
      typeof disposition === 'object' && disposition !== null && 'refused' in disposition,
    ).toBe(true);
    expect(toRelay).not.toHaveBeenCalled();
    expect(send).not.toHaveBeenCalled();
  });
});

describe('diHerdrRequest: checked here, then handed ONCE to the Herdr relay, never acted on here (Package A)', () => {
  const msg = { type: 'diHerdrRequest', seq: 8, op: 'launch', missionRef: 'di_herdr_tasks:task-1@abc123' };
  const ctxWith = (herdrRequest?: (m: Record<string, unknown>, s: (x: Record<string, unknown>) => void) => void) => ({
    store: new AgentStateStore(),
    cache: EMPTY_CACHE,
    extensionVersion: 'test',
    ...(herdrRequest ? { herdrRequest } : {}),
  });

  it('a valid diHerdrRequest is HANDLED by handing it, exactly once, to the Herdr relay; the dispatcher answers nothing itself', () => {
    const seen: unknown[] = [];
    const send = vi.fn();
    expect(handleDiClientMessage(msg, send, ctxWith((m, s) => seen.push([m, s])))).toBe('handled');
    expect(seen).toEqual([[msg, send]]);
    expect(send).not.toHaveBeenCalled();
  });

  it('with no Herdr relay in this office it is refused, and EXACTLY ONE correlated "not configured" reply is sent, naming the flags it needs', () => {
    const send = vi.fn();
    expect(handleDiClientMessage(msg, send, ctxWith())).toEqual({ refused: 'no Herdr route in this office' });
    expect(send).toHaveBeenCalledTimes(1);
    expect(send.mock.calls[0][0]).toMatchObject({ type: 'diHerdrReply', seq: 8, op: 'launch', state: 'unavailable', problem: 'herdr_not_configured', answer: null });
    for (const flag of ['--herdr-bin', '--herdr-home', '--herdr-socket']) expect(String(send.mock.calls[0][0].reason)).toContain(flag);
  });

  it('a malformed diHerdrRequest inside the correlation domain is refused before the relay, with EXACTLY ONE refusal carrying its own seq and op', () => {
    const toRelay = vi.fn();
    const send = vi.fn();
    const disposition = handleDiClientMessage({ ...msg, workspaceId: 'w6V' }, send, ctxWith(toRelay));
    expect(typeof disposition === 'object' && 'refused' in disposition).toBe(true);
    expect(toRelay).not.toHaveBeenCalled();
    expect(send).toHaveBeenCalledTimes(1);
    expect(send.mock.calls[0][0]).toMatchObject({ type: 'diHerdrReply', seq: 8, op: 'launch', state: 'refused', problem: 'request_invalid', answer: null });
  });

  it('a diHerdrRequest whose op is not one of the two (there is no op that sends a key, a prompt or text) is refused with NO reply and nothing invented', () => {
    for (const op of ['sendKeys', 'prompt', 'focusPane', 'attach']) {
      const toRelay = vi.fn();
      const send = vi.fn();
      const disposition = handleDiClientMessage({ ...msg, op }, send, ctxWith(toRelay));
      expect(typeof disposition === 'object' && 'refused' in disposition, op).toBe(true);
      expect(toRelay).not.toHaveBeenCalled();
      expect(send).not.toHaveBeenCalled();
    }
  });
});
