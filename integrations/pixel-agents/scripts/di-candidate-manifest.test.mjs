import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import test, { after } from 'node:test';
import { fileURLToPath } from 'node:url';

import {
  artifactDigest,
  buildManifest,
  EXCLUDED_NAMES,
  MANIFEST_SCHEMA,
  sourceDigest,
  verifyManifest,
} from './di-candidate-manifest.mjs';

// Every temporary directory this file makes is recorded, and removed when the file's tests end. Only a
// directory this file created, by its exact path, directly under the temp root, with one of this file's three
// prefixes and mkdtemp's six characters, is ever removed; anything else is left where it is.
const created = [];
function ownedTemp(prefix) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), prefix));
  created.push(dir);
  return dir;
}
after(() => {
  const own = /^di-(manifest|portable|fixture)-[A-Za-z0-9]{6}$/;
  for (const dir of created) {
    const info = fs.lstatSync(dir, { throwIfNoEntry: false });
    if (
      info !== undefined &&
      info.isDirectory() &&
      path.dirname(dir) === os.tmpdir() &&
      own.test(path.basename(dir))
    ) {
      fs.rmSync(dir, { recursive: true, force: true });
    }
  }
});

function tree(files) {
  const root = ownedTemp('di-manifest-');
  for (const [name, content] of Object.entries(files)) {
    const target = path.join(root, name);
    fs.mkdirSync(path.dirname(target), { recursive: true });
    fs.writeFileSync(target, content);
  }
  return root;
}

const BASE = {
  'a.py': 'print(1)\n',
  'pkg/b.ts': 'export const b = 1;\n',
  'integrations/pixel-agents/server/src/x.ts': 'export {};\n',
};

test('the source digest names the bytes: the same tree gives the same digest, a changed byte another', () => {
  const root = tree(BASE);
  const first = sourceDigest(root);
  assert.equal(first.digest, sourceDigest(root).digest);
  assert.equal(first.files, 3);
  fs.writeFileSync(path.join(root, 'pkg/b.ts'), 'export const b = 2;\n');
  assert.notEqual(sourceDigest(root).digest, first.digest);
});

test('a renamed file, an added file and a removed file each change the digest', () => {
  const base = sourceDigest(tree(BASE)).digest;
  const renamed = tree({
    'a.py': BASE['a.py'],
    'pkg/c.ts': BASE['pkg/b.ts'],
    'integrations/pixel-agents/server/src/x.ts': 'export {};\n',
  });
  const added = tree({ ...BASE, 'extra.txt': 'x' });
  const removed = tree({ 'a.py': BASE['a.py'], 'pkg/b.ts': BASE['pkg/b.ts'] });
  for (const root of [renamed, added, removed]) assert.notEqual(sourceDigest(root).digest, base);
});

// ── the exclusion is by NAME, whatever the entry is ─────────────────────────────────────────────

/** Make `where` exist (relative to root) as a file, a directory, or a symlink to `target`. */
function plant(root, where, kind, target) {
  const abs = path.join(root, where);
  fs.mkdirSync(path.dirname(abs), { recursive: true });
  if (kind === 'file') fs.writeFileSync(abs, `a local pointer: ${target}\n`);
  else if (kind === 'directory') {
    fs.mkdirSync(abs, { recursive: true });
    fs.writeFileSync(path.join(abs, 'noise.bin'), `changes every build ${target}`);
  } else fs.symlinkSync(target, abs);
}

test('an excluded NAME is excluded whether it is a file, a directory or a symlink, at any depth', () => {
  const clean = sourceDigest(tree(BASE));
  for (const name of EXCLUDED_NAMES) {
    for (const kind of ['file', 'directory', 'symlink']) {
      for (const where of [name, path.join('integrations/pixel-agents', name)]) {
        const root = tree(BASE);
        const elsewhere = tree({ 'secret.txt': 'private to this machine' });
        plant(root, where, kind, kind === 'symlink' ? elsewhere : '/Users/someone/local/path');
        const found = sourceDigest(root);
        assert.equal(
          found.digest,
          clean.digest,
          `${name} as a ${kind} at ${where} moved the digest`,
        );
        assert.equal(found.files, clean.files, `${name} as a ${kind} at ${where} was counted`);
      }
    }
  }
});

test('a worktree’s .git is a FILE of absolute local paths, and it is not source', () => {
  const clean = sourceDigest(tree(BASE));
  const one = tree(BASE);
  const other = tree(BASE);
  fs.writeFileSync(path.join(one, '.git'), 'gitdir: /Users/alice/tools/main/.git/worktrees/mn-1\n');
  fs.writeFileSync(
    path.join(other, '.git'),
    'gitdir: /home/bob/checkouts/other/.git/worktrees/mn-2\n',
  );
  assert.equal(sourceDigest(one).digest, clean.digest);
  assert.equal(
    sourceDigest(other).digest,
    clean.digest,
    'the digest depends on where the checkout lives',
  );
});

test('a dependency directory that is a symlink is excluded, never hashed as "symlink:" plus a local target', () => {
  const clean = sourceDigest(tree(BASE));
  const first = tree(BASE);
  const second = tree(BASE);
  fs.symlinkSync(tree({ 'm.js': 'one' }), path.join(first, 'node_modules'));
  fs.symlinkSync(tree({ 'm.js': 'two' }), path.join(second, 'node_modules'));
  assert.equal(sourceDigest(first).digest, clean.digest);
  assert.equal(sourceDigest(second).digest, clean.digest);
});

test('the excluded names are the dependency, build, cache, evidence and VCS names, and nothing that is source', () => {
  for (const name of ['.git', '.herd', 'node_modules', 'dist', '__pycache__', '.DS_Store']) {
    assert.ok(EXCLUDED_NAMES.includes(name), name);
  }
  for (const name of [
    'src',
    'scripts',
    'docs',
    'tests',
    'test',
    'package.json',
    'README.md',
    'build',
  ]) {
    assert.ok(!EXCLUDED_NAMES.includes(name), `${name} is source`);
  }
});

test('a PORTABLE copy of a worktree, holding only the source, has the same digest', () => {
  // The worktree: the source, plus everything a local checkout adds around it.
  const worktree = tree(BASE);
  fs.writeFileSync(
    path.join(worktree, '.git'),
    'gitdir: /Users/alice/tools/main/.git/worktrees/mn-1\n',
  );
  plant(worktree, '.herd', 'directory', 'evidence');
  plant(worktree, 'integrations/pixel-agents/dist', 'directory', 'build');
  plant(worktree, 'integrations/pixel-agents/node_modules', 'symlink', tree({ 'x.js': 'local' }));
  plant(worktree, 'tests/__pycache__', 'directory', 'bytecode');
  fs.writeFileSync(path.join(worktree, '.DS_Store'), 'finder');
  // The copy: a clean checkout elsewhere with ONLY the source files, no pointer, no local directories.
  const copy = tree(BASE);
  const here = sourceDigest(worktree);
  const there = sourceDigest(copy);
  assert.equal(here.files, there.files);
  assert.equal(here.digest, there.digest);
});

// ── the real checkout: the check Root ran by hand, encoded ──────────────────────────────────────

const REPO_ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../../..');

function git(args, options = {}) {
  return spawnSync('git', ['-C', REPO_ROOT, ...args], { maxBuffer: 1 << 28, ...options });
}

function realCheckoutSkip() {
  const inside = git(['rev-parse', '--is-inside-work-tree']);
  if (inside.status !== 0 || inside.stdout.toString().trim() !== 'true')
    return 'not a git work tree';
  const attributes = git([
    'grep',
    '-lE',
    'export-(ignore|subst)',
    '--',
    '*.gitattributes',
    '.gitattributes',
  ]);
  if (attributes.status === 0)
    return 'export attributes make `git archive` differ from the work tree';
  return false;
}

// The independent copy of a checkout, rebuilt from its base commit: a clean archive of it (nothing of the work
// tree's own: no `.git` pointer, no dependency directory, no build output, no evidence), then EVERY path in which
// the work tree differs from that commit taken from the work tree: modified, added, deleted or renamed (both
// names), staged or not, and untracked (not ignored); a path absent from the work tree is deleted in the copy.
//
// The paths are enumerated against the COMMIT, never against the index. `git ls-files -m -o` is index-relative
// and is wrong for exactly the states a merge in progress leaves: a staged ADDITION is tracked (so not `-o`) and
// unchanged since it was staged (so not `-m`), so it is never copied; a staged MODIFICATION whose work tree
// equals the index is not `-m` either, so the copy silently keeps the commit's OLD bytes (the count looks right
// and the digest is wrong); a staged DELETION is gone from the index, so nothing removes it from the copy. The
// commit-relative list is `git diff-index` (plumbing: it compares the commit's tree with the work tree and does
// not refresh or write the index, which porcelain `git diff` does) plus the untracked files.
const commitRelative = (run) => {
  const differing = run(['diff-index', '--name-only', '-z', '--no-renames', 'HEAD']);
  assert.equal(differing.status, 0, String(differing.stderr));
  const untracked = run(['ls-files', '-z', '-o', '--exclude-standard']);
  assert.equal(untracked.status, 0, String(untracked.stderr));
  return [differing, untracked].flatMap((r) => r.stdout.toString().split('\0')).filter(Boolean);
};
// The index-relative enumeration this file used to rely on. It is kept ONLY as the control that proves the
// fixtures below are ones it cannot rebuild; no check of a checkout uses it.
const indexRelative = (run) => {
  const changed = run(['ls-files', '-z', '-m', '-o', '--exclude-standard']);
  assert.equal(changed.status, 0, String(changed.stderr));
  return changed.stdout.toString().split('\0').filter(Boolean);
};

function cleanCopy(root, run, enumerate = commitRelative) {
  const copy = ownedTemp('di-portable-');
  const archive = run(['archive', '--format=tar', 'HEAD']);
  assert.equal(archive.status, 0, String(archive.stderr));
  const extract = spawnSync('tar', ['-x', '-C', copy], {
    input: archive.stdout,
    maxBuffer: 1 << 28,
  });
  assert.equal(extract.status, 0, String(extract.stderr));
  assert.ok(!fs.existsSync(path.join(copy, '.git')), 'the archive carries a .git entry');
  for (const rel of new Set(enumerate(run))) {
    const from = path.join(root, rel);
    const to = path.join(copy, rel);
    let stat = null;
    try {
      stat = fs.lstatSync(from);
    } catch {
      fs.rmSync(to, { force: true });
      continue;
    }
    fs.mkdirSync(path.dirname(to), { recursive: true });
    fs.rmSync(to, { force: true, recursive: true });
    if (stat.isSymbolicLink()) fs.symlinkSync(fs.readlinkSync(from), to);
    else fs.copyFileSync(from, to);
  }
  return copy;
}

test(
  'THE REAL CHECKOUT: HEAD archived, the changed paths copied onto it, has the work tree’s digest',
  { timeout: 600_000, skip: realCheckoutSkip() },
  () => {
    const copy = cleanCopy(REPO_ROOT, git);
    const there = sourceDigest(copy);
    const here = sourceDigest(REPO_ROOT);
    assert.equal(
      here.files,
      there.files,
      'the work tree holds source-set files the clean copy does not (or the reverse)',
    );
    assert.equal(
      here.digest,
      there.digest,
      'the source digest depends on where the checkout lives',
    );
  },
);

// ── the same reconstruction, proved on an OWNED fixture repository in every staged state ───────────
//
// Each fixture is a tiny repository this file creates and removes: its own index, its own staged states. The real
// repository's index is never read here, staged or unstaged. Git is run with no user or system configuration (no
// hooks, no signing) and with every variable a hook or wrapper exports to redirect git removed. Git is REQUIRED: a
// machine without it fails these tests loudly, they are never skipped.

const GIT_REDIRECTS =
  /^GIT_(DIR|WORK_TREE|INDEX_FILE|COMMON_DIR|PREFIX|OBJECT_DIRECTORY|ALTERNATE_OBJECT_DIRECTORIES|NAMESPACE|CEILING_DIRECTORIES)$/;

function fixtureRepo() {
  const root = ownedTemp('di-fixture-');
  const env = { ...process.env, GIT_CONFIG_NOSYSTEM: '1', GIT_CONFIG_GLOBAL: os.devNull };
  for (const name of Object.keys(env)) if (GIT_REDIRECTS.test(name)) delete env[name];
  const run = (args, options = {}) =>
    spawnSync(
      'git',
      [
        '-C',
        root,
        '-c',
        `core.hooksPath=${os.devNull}`,
        '-c',
        'commit.gpgsign=false',
        '-c',
        'user.name=fixture',
        '-c',
        'user.email=fixture@example.invalid',
        ...args,
      ],
      { maxBuffer: 1 << 28, env, ...options },
    );
  const must = (...args) => {
    const result = run(args);
    assert.equal(result.status, 0, `git ${args.join(' ')}: ${String(result.stderr)}`);
  };
  const write = (rel, content) => {
    const file = path.join(root, rel);
    fs.mkdirSync(path.dirname(file), { recursive: true });
    fs.writeFileSync(file, content);
  };
  const read = (rel, base = root) => {
    try {
      return fs.readFileSync(path.join(base, rel), 'utf8');
    } catch {
      return null;
    }
  };
  must('init', '-q', '.');
  for (const [rel, content] of Object.entries({
    'keep.txt': 'never touched\n',
    'modified-staged.txt': 'base\n',
    'modified-staged-then-edited.txt': 'base\n',
    'modified-unstaged.txt': 'base\n',
    'nested/dir/modified-staged.txt': 'base\n',
    'deleted-staged.txt': 'base\n',
    'deleted-unstaged.txt': 'base\n',
    'renamed-from.txt': 'a file that is renamed\n',
  }))
    write(rel, content);
  must('add', '--all');
  must('commit', '-q', '-m', 'the base revision');
  return { root, run, must, write, read };
}

// each state a merge in progress can leave, as a function on a fixture, with what the clean copy must hold
const STATES = {
  'a staged ADDITION (absent from the base revision), and one edited again after it was staged': {
    apply: (fx) => {
      fx.write('added-staged.txt', 'new\n');
      fx.must('add', 'added-staged.txt');
      fx.write('added-then-edited.txt', 'staged text\n');
      fx.must('add', 'added-then-edited.txt');
      fx.write('added-then-edited.txt', 'edited after it was staged\n');
    },
    expect: {
      'added-staged.txt': 'new\n',
      'added-then-edited.txt': 'edited after it was staged\n',
    },
  },
  'a staged MODIFICATION whose work tree equals the index, one edited again, and a nested one': {
    apply: (fx) => {
      fx.write('modified-staged.txt', 'staged bytes\n');
      fx.must('add', 'modified-staged.txt');
      fx.write('modified-staged-then-edited.txt', 'staged text\n');
      fx.must('add', 'modified-staged-then-edited.txt');
      fx.write('modified-staged-then-edited.txt', 'edited after it was staged\n');
      fx.write('nested/dir/modified-staged.txt', 'nested staged bytes\n');
      fx.must('add', 'nested/dir/modified-staged.txt');
    },
    expect: {
      'modified-staged.txt': 'staged bytes\n',
      'modified-staged-then-edited.txt': 'edited after it was staged\n',
      'nested/dir/modified-staged.txt': 'nested staged bytes\n',
    },
  },
  'a staged DELETION, and a deletion that was never staged': {
    apply: (fx) => {
      fx.must('rm', '-q', 'deleted-staged.txt');
      fs.rmSync(path.join(fx.root, 'deleted-unstaged.txt'));
    },
    expect: { 'deleted-staged.txt': null, 'deleted-unstaged.txt': null },
  },
  'a staged RENAME: the old name must go and the new name must come': {
    apply: (fx) => fx.must('mv', 'renamed-from.txt', 'renamed-to.txt'),
    expect: { 'renamed-from.txt': null, 'renamed-to.txt': 'a file that is renamed\n' },
  },
  'an unstaged modification and an untracked file': {
    apply: (fx) => {
      fx.write('modified-unstaged.txt', 'unstaged bytes\n');
      fx.write('untracked.txt', 'untracked\n');
    },
    expect: { 'modified-unstaged.txt': 'unstaged bytes\n', 'untracked.txt': 'untracked\n' },
  },
};

function judge(fx, states, enumerate) {
  for (const state of states) state.apply(fx);
  const copy = cleanCopy(fx.root, fx.run, enumerate);
  return { copy, here: sourceDigest(fx.root), there: sourceDigest(copy) };
}

for (const [name, state] of Object.entries(STATES)) {
  test(`the clean copy rebuilds the work tree exactly, by digest AND file count AND every path's bytes: ${name}`, () => {
    const fx = fixtureRepo();
    const { copy, here, there } = judge(fx, [state]);
    assert.equal(
      here.files,
      there.files,
      'the work tree holds source-set files the clean copy does not (or the reverse)',
    );
    assert.equal(here.digest, there.digest, 'the clean copy has other bytes than the work tree');
    for (const [rel, bytes] of Object.entries(state.expect))
      assert.equal(fx.read(rel, copy), bytes, `${rel} in the clean copy (null = absent)`);
    assert.equal(fx.read('keep.txt', copy), 'never touched\n');
  });
}

test('every staged state at once, as a merge in progress leaves them, rebuilds exactly', () => {
  const fx = fixtureRepo();
  const states = Object.values(STATES);
  const { copy, here, there } = judge(fx, states);
  assert.equal(here.files, there.files);
  assert.equal(here.digest, there.digest);
  for (const state of states)
    for (const [rel, bytes] of Object.entries(state.expect))
      assert.equal(fx.read(rel, copy), bytes, `${rel} in the clean copy (null = absent)`);
});

test('CONTROL: the index-relative enumeration cannot rebuild a staged addition, modification or deletion, so these fixtures have teeth', () => {
  for (const name of [
    'a staged ADDITION (absent from the base revision), and one edited again after it was staged',
    'a staged MODIFICATION whose work tree equals the index, one edited again, and a nested one',
    'a staged DELETION, and a deletion that was never staged',
    'a staged RENAME: the old name must go and the new name must come',
  ]) {
    const { here, there } = judge(fixtureRepo(), [STATES[name]], indexRelative);
    assert.notEqual(
      there.digest,
      here.digest,
      `the index-relative enumeration would have rebuilt this state, so the fixture proves nothing: ${name}`,
    );
  }
  // the state it was designed for it does handle, so the control is not simply broken
  const { here, there } = judge(
    fixtureRepo(),
    [STATES['an unstaged modification and an untracked file']],
    indexRelative,
  );
  assert.equal(there.digest, here.digest);
});

// ── the manifest ────────────────────────────────────────────────────────────────────────────────

test('the manifest names the source and every artifact, states the rule it applied, and carries no absolute path', () => {
  const root = tree(BASE);
  const artifact = tree({ 'index.html': '<html></html>', 'assets/app.js': 'x' });
  const manifest = buildManifest({
    root,
    artifacts: [artifact],
    now: () => '2026-01-01T00:00:00.000Z',
  });
  assert.equal(manifest.schema, MANIFEST_SCHEMA);
  assert.equal(manifest.source.digest, sourceDigest(root).digest);
  assert.equal(manifest.artifacts.length, 1);
  assert.equal(manifest.artifacts[0].digest, artifactDigest(artifact).digest);
  const text = JSON.stringify(manifest);
  assert.ok(!text.includes(root), 'the manifest leaks the checkout path');
  assert.ok(!text.includes(artifact), 'the manifest leaks the artifact path');
  assert.deepEqual(manifest.source.excluded, [...EXCLUDED_NAMES]);
  assert.match(manifest.statement, /not a signature/i);
  // The described method is the implemented one: by NAME, for every entry type, and nothing else.
  assert.match(manifest.source.method, /by NAME/);
  assert.match(manifest.source.method, /whatever it is/);
  assert.match(manifest.source.method, /not by entry type/);
  assert.match(
    manifest.source.method,
    /a file such as a worktree.s `\.git` pointer, a directory, or a symlink/,
  );
  assert.match(manifest.source.method, /\.git/);
  assert.doesNotMatch(manifest.source.method, /directories listed/);
  assert.match(manifest.artifactMethod, /excludes nothing/);
});

test('verify accepts the source the manifest was written for and the artifact it recorded', () => {
  const root = tree(BASE);
  const artifact = tree({ 'index.html': '<html></html>' });
  const manifest = buildManifest({ root, artifacts: [artifact] });
  const result = verifyManifest({ manifest, root, artifacts: [artifact] });
  assert.equal(result.ok, true, JSON.stringify(result.problems));
  assert.deepEqual(result.problems, []);
});

test('verify refuses a package built from anything other than the frozen source', () => {
  const frozen = tree(BASE);
  const artifact = tree({ 'index.html': '<html></html>' });
  const manifest = buildManifest({ root: frozen, artifacts: [artifact] });
  fs.writeFileSync(path.join(frozen, 'pkg/b.ts'), 'export const b = 99;\n');
  const result = verifyManifest({ manifest, root: frozen, artifacts: [artifact] });
  assert.equal(result.ok, false);
  assert.match(result.problems.join('\n'), /source digest differs/);
});

test('verify refuses an artifact that is not the one recorded, or that is missing', () => {
  const root = tree(BASE);
  const artifact = tree({ 'index.html': '<html></html>' });
  const manifest = buildManifest({ root, artifacts: [artifact] });
  fs.writeFileSync(path.join(artifact, 'index.html'), '<html>tampered</html>');
  const changed = verifyManifest({ manifest, root, artifacts: [artifact] });
  assert.equal(changed.ok, false);
  assert.match(changed.problems.join('\n'), /artifact 1 digest differs/);
  const missing = verifyManifest({ manifest, root, artifacts: [] });
  assert.equal(missing.ok, false);
  assert.match(missing.problems.join('\n'), /1 artifact\(s\) recorded, 0 given/);
});

test('a directory ARTIFACT is hashed whole: a package’s dist/ and node_modules/ are part of it, not excluded', () => {
  const artifact = tree({
    'package.json': '{}',
    'dist/di-cli.js': 'bundle',
    'node_modules/fastify/index.js': 'dependency',
  });
  const before = artifactDigest(artifact);
  assert.equal(before.files, 3);
  for (const rel of ['dist/di-cli.js', 'node_modules/fastify/index.js']) {
    const original = fs.readFileSync(path.join(artifact, rel));
    fs.writeFileSync(path.join(artifact, rel), 'changed');
    assert.notEqual(
      artifactDigest(artifact).digest,
      before.digest,
      `${rel} is not covered by the artifact digest`,
    );
    fs.writeFileSync(path.join(artifact, rel), original);
  }
  assert.equal(artifactDigest(artifact).digest, before.digest);
});

test('a single file is an artifact too (a package tarball)', () => {
  const root = tree(BASE);
  const file = path.join(tree({}), 'pixel-agents-di.tgz');
  fs.writeFileSync(file, 'tarball bytes');
  const manifest = buildManifest({ root, artifacts: [file] });
  assert.equal(manifest.artifacts[0].kind, 'file');
  assert.equal(verifyManifest({ manifest, root, artifacts: [file] }).ok, true);
  fs.writeFileSync(file, 'other bytes');
  assert.equal(verifyManifest({ manifest, root, artifacts: [file] }).ok, false);
});

test('verify rejects a manifest that is not exactly the documented shape, an older schema included', () => {
  const root = tree(BASE);
  const manifest = buildManifest({ root, artifacts: [] });
  for (const bad of [
    null,
    [],
    {},
    { ...manifest, schema: 99 },
    { ...manifest, schema: MANIFEST_SCHEMA - 1 },
    { ...manifest, source: { digest: 'nope' } },
  ]) {
    const result = verifyManifest({ manifest: bad, root, artifacts: [] });
    assert.equal(result.ok, false);
    assert.match(result.problems.join('\n'), /manifest/);
  }
});
