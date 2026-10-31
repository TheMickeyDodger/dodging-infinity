/**
 * THE APP SENDS NO TERMINAL INPUT: a static pin over the app's own code (the owner decision on Herdr, 2026-10-26, and the Stage 4 design section 8: "the app never calls pane.send_input,
 * pane.send_keys, pane.send_text, agent.send_keys or agent.prompt. The human types; the app only opens the terminal").
 *
 * WHAT IT ENFORCES, when it runs: in the files it scans (every .ts/.tsx/.mts/.cts file under server/src, declaration files excluded), none of these SOURCE FORMS occurs:
 *   - a member access named send_input, send_keys, send_text, sendInput, sendKeys or sendText (`x.send_keys(...)`, `x['send_text']`);
 *   - `agent.prompt` where the object is an identifier or property named `agent`;
 *   - a string or template literal CONTAINING pane.send_input, pane.send_keys, pane.send_text, agent.send_keys, agent.prompt, send-keys, send-input, send-text or "agent prompt";
 *   - an array literal with the adjacent string elements 'agent', 'prompt' (a CLI argument list).
 * Comments are not part of the syntax tree, so a comment that quotes the rule is not reported. A self-check below shows the detector flags each of these forms and ignores a comment.
 *
 * WHAT IT DOES NOT ESTABLISH. It does not prove that the app sends no input on every path. Computed or aliased calls (a name assembled at run time, a method reached through a variable or a re-export),
 * strings split across expressions, and subprocess or network routes in general are NOT covered; it guards only the forms above, in the scanned files, on the runs where it runs. It observes no
 * OS-terminal attach, and the runtime proof that a person can type while THIS APP sends no input remains owed after this passes (it is the Supervisor's native gate, produced nowhere in this code).
 *
 * THE HERDR TERMINAL ROUTE IS INSIDE THIS SCAN (Package A). herdrLaunch.ts, herdrProcess.ts, herdrRequests.ts and herdrTarget.ts are under server/src/di and are scanned like every other file; the scan was not
 * narrowed to excuse them, and the tests below name them so a change to SCANNED_DIRS that dropped them would fail. The route runs three Herdr commands, `agent list`, `workspace focus <id>` and `workspace get <id>`.
 * `workspace focus` is a socket API call that moves which workspace Herdr shows; it is not terminal input, is none of the forms above, and the detector is shown below NOT to flag it. That is a fact about
 * what the detector looks for, not an exemption: every input form it does look for is still flagged, in the route's files included.
 *
 * SCOPE: the server (`server/src`, the runtime both surfaces compose). Recorded, not hidden: the same scan over the VS Code editor host finds ONE call, `adapters/vscode/agentManager.ts`
 * `terminal.sendText(...)`, the extension's own agent launcher typing the `claude` launch command into a fresh VS Code terminal it has just created. That is not a herd pane and not the attached OS
 * terminal the invariant governs; whether it belongs inside this pin is a scope decision left with the Lead, so the editor host is outside this scan rather than silently excused inside it.
 */
import * as fs from 'fs';
import * as path from 'path';
import * as ts from 'typescript';
import { describe, expect, it } from 'vitest';

const ROOT = path.join(__dirname, '..', '..');
/** The server: the runtime both surfaces compose (see SCOPE above for the editor host). */
const SCANNED_DIRS = ['server/src'];

/** The input method names, as called on an object (pane.send_input(...), agent.sendKeys(...)). */
const INPUT_METHODS = new Set(['send_input', 'send_keys', 'send_text', 'sendInput', 'sendKeys', 'sendText']);
/** The same five as socket method names, and the herdr CLI's input verbs. */
const INPUT_STRINGS = [
  'pane.send_input',
  'pane.send_keys',
  'pane.send_text',
  'agent.send_keys',
  'agent.prompt',
  'send-keys',
  'send-input',
  'send-text',
  'agent prompt',
];

function sourceFiles(): string[] {
  const out: string[] = [];
  const walk = (dir: string) => {
    for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
      if (entry.name === 'node_modules' || entry.name === 'dist') continue;
      const full = path.join(dir, entry.name);
      if (entry.isDirectory()) walk(full);
      else if (/\.(ts|tsx|mts|cts)$/.test(entry.name) && !entry.name.endsWith('.d.ts')) out.push(full);
    }
  };
  for (const dir of SCANNED_DIRS) walk(path.join(ROOT, dir));
  return out.sort();
}

/** Every terminal-input call site in `text`, as "line: what". Comments are not part of the tree and are never reported. */
export function inputCallSites(text: string, fileName = 'probe.ts'): string[] {
  const file = ts.createSourceFile(fileName, text, ts.ScriptTarget.Latest, true);
  const found: string[] = [];
  const at = (node: ts.Node) => file.getLineAndCharacterOfPosition(node.getStart(file)).line + 1;
  const memberName = (node: ts.Node): string | null => {
    if (ts.isPropertyAccessExpression(node)) return node.name.text;
    if (ts.isElementAccessExpression(node) && ts.isStringLiteralLike(node.argumentExpression)) return node.argumentExpression.text;
    return null;
  };
  const ownerName = (node: ts.Node): string | null => {
    const target = ts.isPropertyAccessExpression(node) || ts.isElementAccessExpression(node) ? node.expression : null;
    if (target === null) return null;
    if (ts.isIdentifier(target)) return target.text;
    if (ts.isPropertyAccessExpression(target)) return target.name.text;
    return null;
  };
  const visit = (node: ts.Node) => {
    const name = memberName(node);
    if (name !== null && INPUT_METHODS.has(name)) found.push(`${at(node)}: .${name}`);
    if (name === 'prompt' && ownerName(node) === 'agent') found.push(`${at(node)}: agent.prompt`);
    if (ts.isStringLiteralLike(node) || ts.isTemplateLiteral(node)) {
      const literal = ts.isTemplateExpression(node) ? node.getText(file) : (node as ts.StringLiteralLike).text;
      for (const s of INPUT_STRINGS) if (literal.includes(s)) found.push(`${at(node)}: "${s}"`);
    }
    // an argument list built element by element: ['agent', 'prompt', ...] or ['pane', 'send-keys', ...]
    if (ts.isArrayLiteralExpression(node)) {
      const words = node.elements.map((e) => (ts.isStringLiteralLike(e) ? e.text : null));
      for (let i = 0; i + 1 < words.length; i++) {
        if (words[i] === 'agent' && words[i + 1] === 'prompt') found.push(`${at(node)}: ['agent', 'prompt']`);
      }
    }
    ts.forEachChild(node, visit);
  };
  visit(file);
  return found;
}

describe('the detector is not vacuous: it finds each forbidden form and ignores a comment', () => {
  it.each([
    ['a socket method name', "client.call('pane.send_input', { text })"],
    ['a property call', 'pane.send_keys(id, keys)'],
    ['a camel-cased property call', 'session.sendText(text)'],
    ['an element access', "pane['send_text'](t)"],
    ['agent.prompt as a call', 'agent.prompt(words)'],
    ['agent.send_keys as a method name', "rpc('agent.send_keys')"],
    ['a herdr CLI argument list', "spawn('herdr', ['agent', 'prompt', name, text])"],
    ['a herdr CLI verb in one string', 'exec(`herdr pane send-keys ${id} ${keys}`)'],
  ])('flags %s', (_name, code) => {
    expect(inputCallSites(code).length).toBeGreaterThan(0);
  });

  it('flags every input form when it is written the way the Herdr route would write a Herdr command (an argument list, a template, a member call)', () => {
    for (const code of [
      "effects.runWorkspaceCommand(bin, ['pane', 'send-keys', paneId, text], env)",
      "effects.runWorkspaceCommand(bin, ['agent', 'prompt', name, text], env)",
      'effects.runWorkspaceCommand(bin, [`pane send-text ${id}`], env)',
      'effects.sendKeys(paneId, text)',
      "effects.runWorkspaceCommand(bin, ['pane', 'send-input', id, text], env)",
    ]) {
      expect(inputCallSites(code).length, code).toBeGreaterThan(0);
    }
  });

  it('does NOT flag the Herdr commands the route runs (a read, a workspace focus, a read-back): they are not terminal input, and that is the detector\'s scope, not an exemption', () => {
    for (const code of [
      "const LISTING_ARGS = ['agent', 'list'] as const;",
      "await effects.runWorkspaceCommand(target.herdrBin, ['workspace', 'focus', workspaceId], env);",
      "await effects.runWorkspaceCommand(target.herdrBin, ['workspace', 'get', workspaceId], env);",
    ]) {
      expect(inputCallSites(code), code).toEqual([]);
    }
  });

  it('does NOT flag a comment that quotes the rule, nor an unrelated "prompt"', () => {
    expect(inputCallSites('// the app never calls pane.send_input or agent.prompt\nconst x = 1;')).toEqual([]);
    expect(inputCallSites('/* pane.send_keys, agent.send_keys */ const consent = { prompt: true }; const p = window.prompt;')).toEqual([]);
  });
});

describe('THE SERVER HAS NO TERMINAL-INPUT CALL SITES', () => {
  const files = sourceFiles();

  it('the scan covers the server (it is not an empty scan)', () => {
    const rel = files.map((f) => path.relative(ROOT, f));
    expect(rel.length).toBeGreaterThan(40);
    for (const known of ['server/src/di/diOffice.ts', 'server/src/di/missionRequests.ts', 'server/src/clientMessageHandler.ts', 'server/src/agentRuntime.ts'])
      expect(rel, `the scan includes ${known}`).toContain(known);
  });

  it('the scan includes every module of the Herdr terminal route: it was not narrowed to excuse the new path', () => {
    const rel = files.map((f) => path.relative(ROOT, f));
    for (const route of [
      'server/src/di/herdrLaunch.ts',
      'server/src/di/herdrProcess.ts',
      'server/src/di/herdrRequests.ts',
      'server/src/di/herdrTarget.ts',
    ])
      expect(rel, `the scan includes ${route}`).toContain(route);
    expect(SCANNED_DIRS).toEqual(['server/src']);
  });

  it('no Herdr route module has an input call site, in any form, named on its own (so a failure points at the route)', () => {
    for (const route of ['herdrLaunch.ts', 'herdrProcess.ts', 'herdrRequests.ts', 'herdrTarget.ts']) {
      const file = path.join(ROOT, 'server/src/di', route);
      expect(inputCallSites(fs.readFileSync(file, 'utf-8'), file), route).toEqual([]);
    }
  });

  it('no scanned file calls pane.send_input, send_keys, send_text, agent.send_keys or agent.prompt, in any form', () => {
    const hits = files.flatMap((f) => inputCallSites(fs.readFileSync(f, 'utf-8'), f).map((h) => `${path.relative(ROOT, f)}:${h}`));
    expect(hits).toEqual([]);
  });
});
