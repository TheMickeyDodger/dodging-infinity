#!/usr/bin/env node
/**
 * The verifiable link from FROZEN SOURCE BYTES to the PACKAGED ARTIFACT.
 *
 *   node scripts/di-candidate-manifest.mjs digest
 *       Print the source digest of the checkout: the name of the exact candidate bytes.
 *
 *   node scripts/di-candidate-manifest.mjs write --out FILE [--artifact PATH]...
 *       Record the source digest and the digest of every artifact (a package tarball, or a built
 *       directory such as dist/webview). Run it on the frozen tree, right after building from it.
 *
 *   node scripts/di-candidate-manifest.mjs verify --manifest FILE [--artifact PATH]...
 *       Recompute the digests and compare. Exit 0 only if the checkout is still exactly the frozen
 *       source AND every artifact given is exactly the one recorded, in the order recorded.
 *
 * What it proves, and what it does not. The SOURCE DIGEST is sha256 over every file of the checkout,
 * in sorted path order, as path + NUL + bytes + NUL (a symlink is recorded by its target and never
 * followed), EXCEPT that every entry whose NAME is in EXCLUDED_NAMES is skipped wherever it appears and
 * WHATEVER IT IS: a file, a directory or a symlink. The names are the local, non-source things around a
 * checkout: version-control metadata (`.git`, which in a git WORKTREE is a FILE holding an absolute path
 * to the parent checkout, and in a clone is a directory), dependencies (`node_modules`, which may be a
 * directory or a symlink to a shared one), build output, caches and the local evidence store, and
 * `.DS_Store`. Excluding by name, not by entry type, is what makes the digest PORTABLE: the same source
 * hashes the same wherever it lives, and a package whose manifest verifies against a checkout was built
 * while the checkout held exactly those bytes. The ARTIFACT digest is the same construction over the
 * artifact but EXCLUDES NOTHING (a package directory's own `dist/` and `node_modules/` are part of it).
 * This is a NAME for bytes and a tamper check, not a signature: anyone who can write both the manifest
 * and the tree can make them agree, and the manifest does not say HOW the artifact was built. A package
 * built from any other source has a different source digest and fails `verify`. The manifest carries no
 * absolute path.
 *
 * Nothing here reads outside the tree it is given, runs a build, writes anywhere but `--out`, or uses
 * the network or Git.
 */
import { createHash } from 'node:crypto';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

// 2: the exclusion became BY NAME for every entry type (a worktree's `.git` is a file), so a digest written
// under schema 1 is a different, non-portable thing and is refused rather than compared.
export const MANIFEST_SCHEMA = 2;
/**
 * Names (at any depth, for a file, a directory or a symlink alike) that are not source. ONE list, applied
 * by name only: a second list per entry type is how the worktree `.git` file was once hashed as source.
 * Named in the manifest so the claim is explicit.
 */
export const EXCLUDED_NAMES = Object.freeze([
  '.git',
  '.herd',
  'node_modules',
  'dist',
  'out',
  '__pycache__',
  '.pytest_cache',
  'allure-results',
  'allure-report',
  'coverage',
  '.DS_Store',
]);
const HEX64 = /^[0-9a-f]{64}$/;

/** The files and symlinks under `root`, sorted, skipping every entry whose NAME is in `exclude`. */
function entries(root, exclude, relative = '') {
  const found = [];
  const here = path.join(root, relative);
  for (const name of fs.readdirSync(here).sort()) {
    // By NAME, before the entry's type is even looked at: a file, a directory and a symlink of an
    // excluded name are all skipped (and a skipped symlink is never read, so its target is irrelevant).
    if (exclude.includes(name)) continue;
    const rel = relative === '' ? name : path.join(relative, name);
    const stat = fs.lstatSync(path.join(root, rel));
    if (stat.isDirectory()) found.push(...entries(root, exclude, rel));
    else if (stat.isSymbolicLink() || stat.isFile()) found.push(rel);
    // sockets, devices and the like are not source and are never read
  }
  return found;
}

function digestOf(root, relativePaths) {
  const hash = createHash('sha256');
  for (const rel of relativePaths) {
    const abs = path.join(root, rel);
    hash.update(`${rel.split(path.sep).join('/')}\0`);
    const stat = fs.lstatSync(abs);
    if (stat.isSymbolicLink()) hash.update(`symlink:${fs.readlinkSync(abs)}`);
    else hash.update(fs.readFileSync(abs));
    hash.update('\0');
  }
  return hash.digest('hex');
}

/** The identity of the checkout's source bytes. */
export function sourceDigest(root) {
  const files = entries(root, EXCLUDED_NAMES);
  return { digest: digestOf(root, files), files: files.length };
}

/** The identity of an artifact: a file's bytes, or a directory tree's (same construction as the source). */
export function artifactDigest(target) {
  const stat = fs.lstatSync(target);
  if (stat.isDirectory()) {
    const files = entries(target, []);
    return { kind: 'directory', digest: digestOf(target, files), files: files.length };
  }
  const hash = createHash('sha256');
  hash.update(fs.readFileSync(target));
  return { kind: 'file', digest: hash.digest('hex'), files: 1 };
}

export function buildManifest({ root, artifacts = [], now = () => new Date().toISOString() }) {
  const source = sourceDigest(root);
  return {
    schema: MANIFEST_SCHEMA,
    statement:
      'This names the exact source bytes a candidate was built from and the exact artifacts produced. It is a name for bytes and a tamper check, not a signature: it does not say how the artifact was built.',
    writtenAt: now(),
    source: {
      digest: source.digest,
      files: source.files,
      method:
        'sha256 over every file in sorted path order as path, NUL, bytes (a symlink: "symlink:" and its target, never followed), NUL; every entry whose NAME is in `excluded` is skipped wherever it appears and whatever it is (by NAME, not by entry type: a file such as a worktree\'s `.git` pointer, a directory, or a symlink such as a linked node_modules); nothing else is skipped',
      excluded: [...EXCLUDED_NAMES],
    },
    artifactMethod:
      'a file: sha256 of its bytes. A directory: the same construction as the source over every entry; it excludes nothing',
    artifacts: artifacts.map((target) => {
      const found = artifactDigest(target);
      return {
        name: path.basename(target),
        kind: found.kind,
        digest: found.digest,
        files: found.files,
      };
    }),
  };
}

function shapeProblems(manifest) {
  if (manifest === null || typeof manifest !== 'object' || Array.isArray(manifest)) {
    return ['manifest is not an object'];
  }
  const problems = [];
  if (manifest.schema !== MANIFEST_SCHEMA)
    problems.push(`manifest schema is not ${MANIFEST_SCHEMA}`);
  if (!manifest.source || !HEX64.test(manifest.source.digest ?? '')) {
    problems.push('manifest source digest is missing or not 64 hex characters');
  }
  if (!Array.isArray(manifest.artifacts)) problems.push('manifest artifacts is not a list');
  else {
    for (const [i, a] of manifest.artifacts.entries()) {
      if (!a || !HEX64.test(a.digest ?? ''))
        problems.push(`manifest artifact ${i + 1} digest is not 64 hex characters`);
    }
  }
  return problems;
}

/** Compare a manifest with the checkout and the artifacts given. `ok` only if nothing differs. */
export function verifyManifest({ manifest, root, artifacts = [] }) {
  const shape = shapeProblems(manifest);
  if (shape.length > 0) return { ok: false, problems: shape };
  const problems = [];
  const now = sourceDigest(root);
  if (now.digest !== manifest.source.digest) {
    problems.push(
      `source digest differs: the checkout is ${now.digest.slice(0, 16)}… and the manifest was written for ${manifest.source.digest.slice(0, 16)}…`,
    );
  }
  if (manifest.artifacts.length !== artifacts.length) {
    problems.push(`${manifest.artifacts.length} artifact(s) recorded, ${artifacts.length} given`);
  } else {
    artifacts.forEach((target, i) => {
      let found;
      try {
        found = artifactDigest(target);
      } catch {
        problems.push(`artifact ${i + 1} cannot be read`);
        return;
      }
      if (found.digest !== manifest.artifacts[i].digest) {
        problems.push(
          `artifact ${i + 1} digest differs: it is ${found.digest.slice(0, 16)}… and the manifest recorded ${manifest.artifacts[i].digest.slice(0, 16)}…`,
        );
      }
    });
  }
  return { ok: problems.length === 0, problems };
}

function parseArgs(argv) {
  const out = { command: argv[0], artifacts: [], out: null, manifest: null, root: null };
  for (let i = 1; i < argv.length; i += 2) {
    const flag = argv[i];
    const value = argv[i + 1];
    if (value === undefined) throw new Error(`${flag} needs a value`);
    if (flag === '--artifact') out.artifacts.push(path.resolve(value));
    else if (flag === '--out') out.out = path.resolve(value);
    else if (flag === '--manifest') out.manifest = path.resolve(value);
    else if (flag === '--root') out.root = path.resolve(value);
    else throw new Error(`unknown option ${flag}`);
  }
  return out;
}

function main(argv) {
  const here = path.dirname(fileURLToPath(import.meta.url));
  let args;
  try {
    args = parseArgs(argv);
  } catch (error) {
    console.error(`di-candidate-manifest: ${error.message}`);
    return 2;
  }
  // The checkout is the repository root: three levels above this file's directory by default.
  const root = args.root ?? path.resolve(here, '../../..');
  if (args.command === 'digest') {
    const found = sourceDigest(root);
    console.log(`${found.digest}  (${found.files} files)`);
    return 0;
  }
  if (args.command === 'write') {
    if (args.out === null) {
      console.error('di-candidate-manifest: write needs --out FILE');
      return 2;
    }
    const manifest = buildManifest({ root, artifacts: args.artifacts });
    fs.writeFileSync(args.out, `${JSON.stringify(manifest, null, 2)}\n`, { flag: 'wx' });
    console.log(`wrote ${path.basename(args.out)}: source ${manifest.source.digest}`);
    return 0;
  }
  if (args.command === 'verify') {
    if (args.manifest === null) {
      console.error('di-candidate-manifest: verify needs --manifest FILE');
      return 2;
    }
    let manifest;
    try {
      manifest = JSON.parse(fs.readFileSync(args.manifest, 'utf-8'));
    } catch {
      console.error('di-candidate-manifest: the manifest cannot be read as JSON');
      return 1;
    }
    const result = verifyManifest({ manifest, root, artifacts: args.artifacts });
    if (result.ok) {
      console.log('OK: the checkout is the frozen source and every artifact is the one recorded');
      return 0;
    }
    for (const problem of result.problems) console.error(`MISMATCH: ${problem}`);
    return 1;
  }
  console.error(
    'usage: di-candidate-manifest.mjs digest | write --out FILE [--artifact PATH]... | verify --manifest FILE [--artifact PATH]...',
  );
  return 2;
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  process.exitCode = main(process.argv.slice(2));
}
