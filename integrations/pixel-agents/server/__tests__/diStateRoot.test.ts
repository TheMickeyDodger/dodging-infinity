/**
 * A2 isolation: the DI state root and the shared-profile guard.
 *
 * Every case is built in a SANDBOX with a FAKE home and fake profiles (os.homedir is
 * mocked). Nothing here reads, probes or writes the real user profile or anything
 * under the real ~/.pixel-agents.
 */
import * as fs from 'fs';
import * as os from 'os';
import * as path from 'path';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

let fakeHome: string;

vi.mock('os', async () => {
  const actual = await vi.importActual<typeof import('os')>('os');
  return { ...actual, homedir: () => fakeHome };
});

const { getStateRoot, resetStateRootForTests, setStateRoot } = await import('../src/stateRoot.js');
const { DiStateRootConflict, physicalPath, sharedStateRoot, validateDiStateRoot } =
  await import('../src/di/diStateRoot.js');
const { readConfig, writeConfig } = await import('../src/configPersistence.js');
const { readLayoutFromFile, writeLayoutToFile } = await import('../src/layoutPersistence.js');
const { FileStateAdapter } = await import('../src/fileStateAdapter.js');
const { LAYOUT_FILE_DIR, SERVER_JSON_DIR } = await import('../src/constants.js');

let sandbox: string;

beforeEach(() => {
  sandbox = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), 'di-root-')));
  fakeHome = path.join(sandbox, 'home');
  fs.mkdirSync(path.join(fakeHome, '.pixel-agents'), { recursive: true });
  resetStateRootForTests();
});

afterEach(() => {
  resetStateRootForTests();
  fs.rmSync(sandbox, { recursive: true, force: true });
});

function listTree(root: string): string[] {
  const out: string[] = [];
  const walk = (dir: string): void => {
    for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
      const full = path.join(dir, entry.name);
      out.push(path.relative(root, full));
      if (entry.isDirectory()) walk(full);
    }
  };
  if (fs.existsSync(root)) walk(root);
  return out.sort();
}

describe('stateRoot seam', () => {
  it('defaults to <home>/.pixel-agents (existing users unchanged)', () => {
    expect(getStateRoot()).toBe(path.join(fakeHome, '.pixel-agents'));
    expect(LAYOUT_FILE_DIR).toBe(SERVER_JSON_DIR); // one root covers state AND discovery
  });

  it('an injected root moves config, layout and adapter state; nothing lands in the shared profile', () => {
    const diRoot = path.join(sandbox, 'di-state');
    setStateRoot(diRoot);
    const cfg = readConfig();
    cfg.standalone.soundEnabled = false;
    writeConfig(cfg);
    writeLayoutToFile({ version: 1, cols: 1, rows: 1, tiles: [0], furniture: [] });
    const adapter = new FileStateAdapter({ namespace: 'standalone' });
    adapter.saveSeats({ '1': { palette: 1 } });
    expect(listTree(diRoot)).toEqual(['config.json', 'layout.json', 'standalone-state.json']);
    expect(listTree(path.join(fakeHome, '.pixel-agents'))).toEqual([]);
    expect(readLayoutFromFile()).not.toBeNull();
  });

  it('refuses a relative root and refuses re-injection after the root was resolved', () => {
    expect(() => setStateRoot('relative/dir')).toThrow();
    getStateRoot();
    expect(() => setStateRoot(path.join(sandbox, 'late'))).toThrow(/already resolved/);
  });
});

describe('DI state-root guard (physical, case-insensitive, containment both ways, fail closed)', () => {
  it('accepts a separate directory (existing or not yet created)', () => {
    expect(validateDiStateRoot(path.join(sandbox, 'di'))).toBe(path.join(sandbox, 'di'));
    fs.mkdirSync(path.join(sandbox, 'existing'));
    expect(validateDiStateRoot(path.join(sandbox, 'existing'))).toBe(
      path.join(sandbox, 'existing'),
    );
  });

  it('rejects the shared profile itself', () => {
    expect(() => validateDiStateRoot(sharedStateRoot())).toThrow(DiStateRootConflict);
    expect(() => validateDiStateRoot(path.join(fakeHome, '.pixel-agents') + path.sep)).toThrow(
      /shared Pixel Agents profile/,
    );
  });

  it('rejects a root INSIDE the shared profile (existing or not)', () => {
    expect(() => validateDiStateRoot(path.join(fakeHome, '.pixel-agents', 'di'))).toThrow(/inside/);
    expect(() => validateDiStateRoot(path.join(fakeHome, '.pixel-agents', 'a', 'b', 'c'))).toThrow(
      /inside/,
    );
  });

  it('rejects a root that CONTAINS the shared profile (an ancestor)', () => {
    expect(() => validateDiStateRoot(fakeHome)).toThrow(/ancestor/);
    expect(() => validateDiStateRoot(sandbox)).toThrow(/ancestor/);
  });

  it('rejects a SYMLINK alias of the shared profile', () => {
    const alias = path.join(sandbox, 'alias');
    fs.symlinkSync(path.join(fakeHome, '.pixel-agents'), alias);
    expect(() => validateDiStateRoot(alias)).toThrow(/shared Pixel Agents profile/);
  });

  it('rejects a not-yet-existing root under a SYMLINKED PARENT that resolves into the shared profile', () => {
    const parentAlias = path.join(sandbox, 'parent-alias');
    fs.symlinkSync(path.join(fakeHome, '.pixel-agents'), parentAlias);
    const candidate = path.join(parentAlias, 'not', 'created', 'yet');
    expect(physicalPath(candidate)).toBe(
      path.join(fakeHome, '.pixel-agents', 'not', 'created', 'yet'),
    );
    expect(() => validateDiStateRoot(candidate)).toThrow(/inside/);
  });

  it('rejects a differently-CASED spelling of the shared profile (case-insensitive compare)', () => {
    const shouting = path.join(fakeHome.toUpperCase(), '.PIXEL-AGENTS');
    expect(() => validateDiStateRoot(shouting, path.join(fakeHome, '.pixel-agents'))).toThrow(
      DiStateRootConflict,
    );
    expect(() =>
      validateDiStateRoot(
        path.join(fakeHome, '.Pixel-Agents', 'di'),
        path.join(fakeHome, '.pixel-agents'),
      ),
    ).toThrow(DiStateRootConflict);
  });

  it('rejects a relative root', () => {
    expect(() => validateDiStateRoot('di-state')).toThrow(DiStateRootConflict);
  });

  it('a sibling whose name merely starts with the shared name is NOT inside it', () => {
    expect(validateDiStateRoot(path.join(fakeHome, '.pixel-agents-di'))).toBe(
      path.join(fakeHome, '.pixel-agents-di'),
    );
  });
});
