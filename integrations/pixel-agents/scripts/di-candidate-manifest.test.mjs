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
// directory this file created, by its exact path, directly under the temp root, with one of this file's two
// prefixes and mkdtemp's six characters, is ever removed; anything else is left where it is.
const created = [];
function ownedTemp(prefix) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), prefix));
  created.push(dir);
  return dir;
}
after(() => {
  const own = /^di-(manifest|portable)-[A-Za-z0-9]{6}$/;
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

test(
  'THE REAL CHECKOUT: HEAD archived, the changed paths copied onto it, has the work tree’s digest',
  { timeout: 600_000, skip: realCheckoutSkip() },
  () => {
    // The independent copy: a clean archive of the base commit, and nothing of the work tree's own
    // (no `.git` pointer, no dependency directory, no build output, no evidence).
    const copy = ownedTemp('di-portable-');
    const archive = git(['archive', '--format=tar', 'HEAD']);
    assert.equal(archive.status, 0, String(archive.stderr));
    const extract = spawnSync('tar', ['-x', '-C', copy], {
      input: archive.stdout,
      maxBuffer: 1 << 28,
    });
    assert.equal(extract.status, 0, String(extract.stderr));
    assert.ok(!fs.existsSync(path.join(copy, '.git')), 'the archive carries a .git entry');
    // Then ONLY the paths this work tree changed (modified or untracked, not ignored) are copied over;
    // a path deleted in the work tree is deleted in the copy.
    const changed = git(['ls-files', '-z', '-m', '-o', '--exclude-standard']);
    assert.equal(changed.status, 0, String(changed.stderr));
    for (const rel of changed.stdout.toString().split('\0').filter(Boolean)) {
      const from = path.join(REPO_ROOT, rel);
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
