/**
 * FINDING 3: validating the DI root does not contain the writes BENEATH it. Every DI
 * persistence write (config, layout, state) and the browser access file is checked AT
 * WRITE TIME: the target's parent must resolve physically inside the validated root, and
 * no write goes through a symlink. Sandbox + fake roots only; nothing outside the sandbox
 * is touched, and the "outside" victims below live inside the sandbox too.
 */
import * as fs from 'fs';
import * as os from 'os';
import * as path from 'path';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { readConfig, writeConfig } from '../src/configPersistence.js';
import {
  BrowserAccessExists,
  browserAccessPath,
  publishBrowserAccess,
} from '../src/di/browserAccess.js';
import {
  DiStateWriteEscape,
  isInsidePhysicalRoot,
  makeDiStateWriteGuard,
  validateDiStateRoot,
} from '../src/di/diStateRoot.js';
import { FileStateAdapter } from '../src/fileStateAdapter.js';
import { writeLayoutToFile } from '../src/layoutPersistence.js';

/** A layout the shared check admits, so these tests reach the WRITE GUARD (the sink refuses a
 *  malformed layout before any path is touched). */
const MINIMAL_LAYOUT = { version: 1, cols: 1, rows: 1, tiles: [1], furniture: [] };
import {
  guardStateWrite,
  resetStateRootForTests,
  setStateRoot,
  setStateWriteGuard,
  writeStateTemp,
} from '../src/stateRoot.js';

let sandbox: string;
let root: string;
let outside: string;
let errors: string[];

beforeEach(() => {
  resetStateRootForTests();
  sandbox = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), 'di-contain-')));
  root = path.join(sandbox, 'di-root');
  outside = path.join(sandbox, 'outside');
  fs.mkdirSync(root);
  fs.mkdirSync(outside);
  errors = [];
  vi.spyOn(console, 'error').mockImplementation((...args: unknown[]) => {
    errors.push(args.map(String).join(' '));
  });
});

afterEach(() => {
  vi.restoreAllMocks();
  resetStateRootForTests();
  fs.rmSync(sandbox, { recursive: true, force: true });
});

function injectGuardedRoot(): void {
  setStateRoot(root);
  setStateWriteGuard(makeDiStateWriteGuard(root));
}

describe('the DI write guard (unit)', () => {
  it('allows ordinary writes inside the root, nested or not yet existing', () => {
    const guard = makeDiStateWriteGuard(root);
    expect(() => guard(path.join(root, 'config.json'))).not.toThrow();
    expect(() => guard(path.join(root, 'run', 'browser-access.json'))).not.toThrow();
  });

  it('refuses a target under a SYMLINKED DESCENDANT directory, naming the escape', () => {
    fs.symlinkSync(outside, path.join(root, 'run'));
    const guard = makeDiStateWriteGuard(root);
    const target = path.join(root, 'run', 'browser-access.json');
    expect(() => guard(target)).toThrow(DiStateWriteEscape);
    expect(() => guard(target)).toThrow(/outside the DI state root/);
    expect(() => guard(target)).toThrow(new RegExp(outside));
    // not-yet-existing nested path under the symlinked descendant: refused too
    expect(() => guard(path.join(root, 'run', 'deeper', 'x.json'))).toThrow(DiStateWriteEscape);
  });

  it('refuses a target that is itself a symlink (even one pointing inside the root)', () => {
    fs.writeFileSync(path.join(root, 'real.json'), '{}');
    fs.symlinkSync(path.join(root, 'real.json'), path.join(root, 'layout.json'));
    expect(() => makeDiStateWriteGuard(root)(path.join(root, 'layout.json'))).toThrow(
      /is a symbolic link/,
    );
  });

  it('refuses a relative target', () => {
    expect(() => makeDiStateWriteGuard(root)('config.json')).toThrow(/not an absolute path/);
  });
});

describe('every DI persistence write goes through the guard (shared writers)', () => {
  it('config: a temp-path symlink planted to an outside file is refused; the victim is untouched', () => {
    injectGuardedRoot(); // inject FIRST: reading resolves the root, after which it is latched
    const cfg = readConfig();
    const victim = path.join(outside, 'victim.json');
    fs.writeFileSync(victim, 'VICTIM');
    fs.symlinkSync(victim, path.join(root, 'config.json.tmp'));
    writeConfig(cfg);
    expect(fs.readFileSync(victim, 'utf-8')).toBe('VICTIM');
    expect(fs.existsSync(path.join(root, 'config.json'))).toBe(false);
    expect(errors.join('\n')).toMatch(
      /DI state write refused: .*config\.json\.tmp is a symbolic link/,
    );
  });

  it('layout: a symlinked target is refused; the victim is untouched', () => {
    injectGuardedRoot();
    const victim = path.join(outside, 'layout-victim.json');
    fs.writeFileSync(victim, 'VICTIM');
    fs.symlinkSync(victim, path.join(root, 'layout.json'));
    writeLayoutToFile(MINIMAL_LAYOUT);
    expect(fs.readFileSync(victim, 'utf-8')).toBe('VICTIM');
    expect(errors.join('\n')).toMatch(/DI state write refused: .*layout\.json is a symbolic link/);
  });

  it('state: the adapter write is contained the same way', () => {
    injectGuardedRoot();
    const adapter = new FileStateAdapter({ namespace: 'standalone' });
    const victim = path.join(outside, 'state-victim.json');
    fs.writeFileSync(victim, 'VICTIM');
    fs.symlinkSync(victim, path.join(root, 'standalone-state.json.tmp'));
    adapter.saveSeats({});
    expect(fs.readFileSync(victim, 'utf-8')).toBe('VICTIM');
    expect(errors.join('\n')).toMatch(/DI state write refused/);
  });

  it('ordinary DI writes still land inside the root', () => {
    injectGuardedRoot();
    writeConfig(readConfig());
    writeLayoutToFile(MINIMAL_LAYOUT);
    new FileStateAdapter({ namespace: 'standalone' }).saveSeats({});
    expect(fs.readdirSync(root).sort()).toEqual([
      'config.json',
      'layout.json',
      'standalone-state.json',
    ]);
    expect(errors).toEqual([]);
  });

  it('browser access: a symlinked run/ descendant is refused before anything is written', () => {
    injectGuardedRoot();
    fs.symlinkSync(outside, path.join(root, 'run'));
    expect(() =>
      publishBrowserAccess(root, { url: 'http://127.0.0.1:1/?token=t', port: 1, token: 't' }),
    ).toThrow(DiStateWriteEscape);
    expect(fs.readdirSync(outside)).toEqual([]);
  });

  it('the default root (no guard installed) is unchanged: no check, plain write', () => {
    // No injected root, no guard: the non-DI surfaces behave exactly as before.
    const target = path.join(sandbox, 'plain.json');
    expect(() => guardStateWrite(target)).not.toThrow();
    writeStateTemp(target, 'x');
    expect(fs.readFileSync(target, 'utf-8')).toBe('x');
  });
});

describe('browser access ownership (module)', () => {
  it('publish is EXCLUSIVE: a second publish at the same root is refused and the first file is intact', () => {
    injectGuardedRoot();
    const first = publishBrowserAccess(root, { url: 'u1', port: 1, token: 'token-one' });
    expect(() => publishBrowserAccess(root, { url: 'u2', port: 2, token: 'token-two' })).toThrow(
      BrowserAccessExists,
    );
    expect(() => publishBrowserAccess(root, { url: 'u2', port: 2, token: 'token-two' })).toThrow(
      new RegExp(browserAccessPath(root).replace(/[.*+?^${}()|[\]\\]/g, '\\$&')),
    );
    expect(JSON.parse(fs.readFileSync(first.path, 'utf-8')).token).toBe('token-one');
    expect(fs.readdirSync(path.dirname(first.path))).toEqual(['browser-access.json']);
  });
});

// ── Round-2 blocker 2: PHYSICAL, CASE-PRESERVING identity authorizes writes; folding only rejects ──

/** Is the volume holding the OS temp dir case-SENSITIVE? (A distinct DI-ROOT/di-root pair needs one.) */
function tempVolumeIsCaseSensitive(): boolean {
  const probe = fs.mkdtempSync(path.join(os.tmpdir(), 'di-case-probe-'));
  try {
    fs.writeFileSync(path.join(probe, 'case-probe'), '');
    return !fs.existsSync(path.join(probe, 'CASE-PROBE'));
  } finally {
    fs.rmSync(probe, { recursive: true, force: true });
  }
}
const CASE_SENSITIVE = tempVolumeIsCaseSensitive();

describe('write authorization is case-PRESERVING (folding is for rejection only)', () => {
  it('a differently-cased SIBLING is outside the root; a real descendant is inside (pure comparison, every platform)', () => {
    expect(isInsidePhysicalRoot('/sandbox/DI-ROOT/run', '/sandbox/di-root')).toBe(false);
    expect(isInsidePhysicalRoot('/sandbox/DI-ROOT', '/sandbox/di-root')).toBe(false);
    expect(isInsidePhysicalRoot('/sandbox/di-root/run', '/sandbox/di-root')).toBe(true);
    expect(isInsidePhysicalRoot('/sandbox/di-root', '/sandbox/di-root/')).toBe(true);
    expect(isInsidePhysicalRoot('/sandbox/di-rootX/run', '/sandbox/di-root')).toBe(false); // prefix trap
  });

  it('REJECTION still folds case: a differently-cased spelling of the shared profile is refused at startup (N1 unchanged)', () => {
    const shared = path.join(sandbox, 'home', '.pixel-agents');
    fs.mkdirSync(shared, { recursive: true });
    expect(() => validateDiStateRoot(path.join(sandbox, 'home', '.PIXEL-AGENTS'), shared)).toThrow(
      /shared Pixel Agents profile/,
    );
  });

  it.skipIf(!CASE_SENSITIVE)(
    'SIBLING ALIAS on a case-sensitive volume: di-root/run -> DISTINCT DI-ROOT is REFUSED; DI-ROOT contents and mode are UNCHANGED',
    () => {
      // PLATFORM LIMITATION: only constructible on a case-SENSITIVE volume (e.g. typical Linux,
      // case-sensitive APFS). On case-insensitive volumes (default macOS/Windows) DI-ROOT and
      // di-root are the SAME directory and this test is skipped — the pure comparison above
      // covers the decision there.
      const accepted = root; // sandbox/di-root, created by beforeEach
      const external = path.join(sandbox, 'DI-ROOT'); // a DISTINCT directory on a case-sensitive volume
      fs.mkdirSync(external);
      fs.chmodSync(external, 0o755);
      fs.writeFileSync(path.join(external, 'keep.txt'), 'EXTERNAL');
      const before = {
        mode: fs.statSync(external).mode & 0o777,
        entries: fs.readdirSync(external).sort(),
        keep: fs.readFileSync(path.join(external, 'keep.txt'), 'utf-8'),
      };
      fs.symlinkSync(external, path.join(accepted, 'run'));
      setStateRoot(accepted);
      setStateWriteGuard(makeDiStateWriteGuard(accepted));
      expect(() =>
        publishBrowserAccess(accepted, { url: 'http://127.0.0.1:1/?token=t', port: 1, token: 't' }),
      ).toThrow(DiStateWriteEscape);
      expect(() =>
        publishBrowserAccess(accepted, { url: 'http://127.0.0.1:1/?token=t', port: 1, token: 't' }),
      ).toThrow(/outside the DI state root/);
      // Nothing chmodded or written outside — not even while discovering the escape.
      expect({
        mode: fs.statSync(external).mode & 0o777,
        entries: fs.readdirSync(external).sort(),
        keep: fs.readFileSync(path.join(external, 'keep.txt'), 'utf-8'),
      }).toEqual(before);
    },
  );

  it('records which branch ran on this machine (the sibling-alias case needs a case-sensitive volume)', () => {
    console.log(
      `[diWriteContainment] temp volume case-sensitive: ${CASE_SENSITIVE}; sibling-alias integration case ${CASE_SENSITIVE ? 'RAN' : 'SKIPPED (case-insensitive volume)'}`,
    );
    expect(typeof CASE_SENSITIVE).toBe('boolean');
  });
});
