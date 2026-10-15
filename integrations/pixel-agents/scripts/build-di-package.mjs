#!/usr/bin/env node
/**
 * Assemble the STANDALONE Pixel Agents DI office package from an existing build (dist/).
 * A separate artifact: the root package.json `files` / `bin` are NOT touched — shipping the
 * DI entry in the ordinary npm package would change normal Pixel.
 *
 *   node scripts/build-di-package.mjs --out <absolute dir>
 *
 * Contents (an ALLOWLIST — anything else is excluded and the exclusion is PROVEN in the
 * inventory):
 *   dist/di-cli.js                      the DI entry (no source map)
 *   dist/webview/index.html, assets/index-*.{js,css}, fonts/FSPixelSansUnicode-Regular.ttf
 *   dist/assets/**                      runtime assets (characters, floors, walls, carpets,
 *                                       furniture, pets, default layout)
 *   node_modules/**                     the EXACT runtime closure of the four externals
 *                                       (fastify, @fastify/{websocket,static,cors}) and their
 *                                       transitive dependencies, copied from the installed tree
 *   LICENSE, THIRD-PARTY-NOTICES.txt, CREDITS.txt, README.txt, package.json,
 *   dist/webview/fonts/OFL.txt          notices (font bytes are copied unchanged)
 *   worlds/expanded-office.json, pixel  the opt-in world preset and the open-in-browser
 *                                       launcher, from packaging/di/ (byte-exact; the
 *                                       office itself never loads or runs either)
 * Excluded by ruling (W3): Screenshot.jpg, icon.png and the README composites.
 * Writes <out>/pixel-agents-di-office-<version>/, PACKAGE-INVENTORY.txt inside it, and
 * <out>/pixel-agents-di-office-<version>.tgz; prints the tarball's sha256.
 */
import { spawnSync } from 'node:child_process';
import crypto from 'node:crypto';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const EXTERNALS = ['fastify', '@fastify/websocket', '@fastify/static', '@fastify/cors'];
const FONT_REL = 'dist/webview/fonts/FSPixelSansUnicode-Regular.ttf';
/** sha256 the font must keep (W3 ruling 4: byte-for-byte, never re-encoded). */
const FONT_SHA256_PREFIX = '3b4e53ff';
const W3_EXCLUDED = ['Screenshot.jpg', 'icon.png', 'banner.png', 'office.png', 'characters.png'];
const FONT_SOURCE_URL =
  'https://fontstruct.com/fontstructions/show/2606508/fs-pixel-sans-unicode-regular';
const FONT_LICENSE_URL =
  'https://fontstruct.com/fontstructions/license/2606508/fs-pixel-sans-unicode-regular';

/** The shared index.html's dangling Vite default icon (no vite.svg exists in any build). */
const VITE_ICON_LINK = '<link rel="icon" type="image/svg+xml" href="/vite.svg" />';

/**
 * The DI favicon: the AUTHORED inline SVG of webview-ui/src/constants.ts DI_FAVICON_DATA_URI,
 * read from that single source (its quoted string literals, concatenated) — never a copy here.
 */
function diFaviconDataUri() {
  const text = fs.readFileSync(path.join(ROOT, 'webview-ui/src/constants.ts'), 'utf-8');
  const block = /export const DI_FAVICON_DATA_URI =[\s\S]*?encodeURIComponent\(([\s\S]*?)\);/.exec(
    text,
  );
  if (!block) fail('DI_FAVICON_DATA_URI not found in webview-ui/src/constants.ts');
  const svg = [...block[1].matchAll(/'([^']*)'|"([^"]*)"/g)].map((m) => m[1] ?? m[2]).join('');
  if (!svg.startsWith('<svg') || !svg.endsWith('</svg>')) fail('DI favicon is not a whole SVG');
  return 'data:image/svg+xml,' + encodeURIComponent(svg);
}

function fail(message) {
  console.error(`[build-di-package] ${message}`);
  process.exit(1);
}

function args() {
  const i = process.argv.indexOf('--out');
  const out = i >= 0 ? process.argv[i + 1] : undefined;
  if (!out || !path.isAbsolute(out)) fail('--out <absolute dir> is required');
  // --draft: an UNSEALED evidence build (README markers allowed, named -DRAFT-UNSEALED); never
  // the frozen package.
  return { out, draft: process.argv.includes('--draft') };
}

const sha256 = (file) => crypto.createHash('sha256').update(fs.readFileSync(file)).digest('hex');
const readJson = (file) => JSON.parse(fs.readFileSync(file, 'utf-8'));

function copyFile(src, dest) {
  fs.mkdirSync(path.dirname(dest), { recursive: true });
  fs.copyFileSync(src, dest);
}

/** Copy a directory tree, optionally skipping a top-level child name (e.g. node_modules). */
function copyTree(src, dest, skipTopLevel = new Set()) {
  for (const entry of fs.readdirSync(src, { withFileTypes: true })) {
    if (skipTopLevel.has(entry.name)) continue;
    const from = path.join(src, entry.name);
    const to = path.join(dest, entry.name);
    if (entry.isDirectory()) copyTree(from, to);
    else if (entry.isFile()) copyFile(from, to);
    // symlinks and specials are not followed or copied
  }
}

function listFiles(dir, base = dir) {
  const out = [];
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) out.push(...listFiles(full, base));
    else if (entry.isFile()) out.push(path.relative(base, full));
  }
  return out.sort();
}

/** Node's own lookup: <dir>/node_modules/<name>, then each ancestor, up to the repo root. */
function resolvePackageDir(name, fromDir) {
  let dir = fromDir;
  for (;;) {
    const candidate = path.join(dir, 'node_modules', name);
    if (fs.existsSync(path.join(candidate, 'package.json'))) return candidate;
    if (dir === ROOT || path.dirname(dir) === dir) return null;
    dir = path.dirname(dir);
  }
}

/** The exact installed runtime closure of `names` (dependencies + installed optionals). */
function runtimeClosure(names) {
  const found = new Map(); // package dir -> { name, version, license }
  const queue = names.map((name) => ({ name, from: ROOT, optional: false }));
  while (queue.length > 0) {
    const { name, from, optional } = queue.shift();
    const dir = resolvePackageDir(name, from);
    if (!dir) {
      if (optional) continue;
      fail(
        `runtime dependency ${name} (required from ${path.relative(ROOT, from) || '.'}) is not installed`,
      );
    }
    if (found.has(dir)) continue;
    const pkg = readJson(path.join(dir, 'package.json'));
    found.set(dir, { name, version: pkg.version, license: pkg.license ?? null });
    for (const dep of Object.keys(pkg.dependencies ?? {}))
      queue.push({ name: dep, from: dir, optional: false });
    for (const dep of Object.keys(pkg.optionalDependencies ?? {}))
      queue.push({ name: dep, from: dir, optional: true });
  }
  return found;
}

/** The font's own name-table records (nameID 0 copyright, 1 family, 13 license, ...). */
function fontNames(file) {
  const b = fs.readFileSync(file);
  const numTables = b.readUInt16BE(4);
  let nameOffset = -1;
  for (let i = 0; i < numTables; i++) {
    const rec = 12 + i * 16;
    if (b.toString('latin1', rec, rec + 4) === 'name') nameOffset = b.readUInt32BE(rec + 8);
  }
  if (nameOffset < 0) fail('font has no name table');
  const count = b.readUInt16BE(nameOffset + 2);
  const strings = b.readUInt16BE(nameOffset + 4);
  const names = {};
  for (let i = 0; i < count; i++) {
    const r = nameOffset + 6 + i * 12;
    const platform = b.readUInt16BE(r);
    const nameId = b.readUInt16BE(r + 6);
    const length = b.readUInt16BE(r + 8);
    const offset = b.readUInt16BE(r + 10);
    const raw = b.subarray(nameOffset + strings + offset, nameOffset + strings + offset + length);
    let text;
    if (platform === 3 || platform === 0) {
      text = '';
      for (let j = 0; j + 1 < raw.length; j += 2) text += String.fromCharCode(raw.readUInt16BE(j));
    } else {
      text = raw.toString('latin1');
    }
    if (!(nameId in names) || platform === 3) names[nameId] = text;
  }
  return names;
}

/** npm packages esbuild bundled INTO dist/di-cli.js (from its source map, else its markers). */
function bundledIntoDiCli() {
  const map = path.join(ROOT, 'dist/di-cli.js.map');
  const names = new Set();
  const take = (p) => {
    const m = /node_modules\/((?:@[^/]+\/)?[^/]+)\//.exec(p);
    if (m) names.add(m[1]);
  };
  if (fs.existsSync(map)) for (const source of readJson(map).sources ?? []) take(source);
  else
    for (const line of fs.readFileSync(path.join(ROOT, 'dist/di-cli.js'), 'utf-8').split('\n'))
      if (line.startsWith('// node_modules/')) take(line.slice(3));
  for (const external of EXTERNALS) names.delete(external);
  return [...names].sort();
}

function licenseText(dir) {
  for (const candidate of ['LICENSE', 'LICENSE.md', 'LICENSE.txt', 'license', 'LICENCE']) {
    const file = path.join(dir, candidate);
    if (fs.existsSync(file)) return fs.readFileSync(file, 'utf-8').trim();
  }
  return null;
}

function main() {
  const { out, draft } = args();
  const rootPkg = readJson(path.join(ROOT, 'package.json'));
  const name = `pixel-agents-di-office-${rootPkg.version}${draft ? '-DRAFT-UNSEALED' : ''}`;
  const pkgDir = path.join(out, name);
  if (fs.existsSync(pkgDir)) fail(`${pkgDir} already exists (never overwritten)`);
  fs.mkdirSync(pkgDir, { recursive: true });

  // ── the DI entry and the production webview / assets (allowlist) ──
  const entry = path.join(ROOT, 'dist/di-cli.js');
  if (!fs.existsSync(entry)) fail('dist/di-cli.js missing: build first (node esbuild.js)');
  copyFile(entry, path.join(pkgDir, 'dist/di-cli.js'));
  const webview = path.join(ROOT, 'dist/webview');
  const webviewFiles = listFiles(webview);
  const allowedWebview = webviewFiles.filter(
    (f) =>
      f === 'index.html' ||
      /^assets\/index-[A-Za-z0-9_-]+\.(js|css)$/.test(f) ||
      f === 'fonts/FSPixelSansUnicode-Regular.ttf',
  );
  const excludedWebview = webviewFiles.filter((f) => !allowedWebview.includes(f));
  for (const f of allowedWebview)
    copyFile(path.join(webview, f), path.join(pkgDir, 'dist/webview', f));
  // DI-ONLY favicon: the PACKAGE's index.html gets the authored inline SVG in place of the
  // dangling /vite.svg link, so the initial document fetches nothing (no 404, and with an icon
  // declared no /favicon.ico fallback). The build's own index.html (normal Pixel) is untouched.
  const indexPath = path.join(pkgDir, 'dist/webview/index.html');
  const indexHtml = fs.readFileSync(indexPath, 'utf-8');
  if (indexHtml.split(VITE_ICON_LINK).length !== 2)
    fail('index.html: the /vite.svg icon link is not present exactly once');
  fs.writeFileSync(
    indexPath,
    indexHtml.replace(
      VITE_ICON_LINK,
      `<link rel="icon" type="image/svg+xml" href="${diFaviconDataUri()}" />`,
    ),
  );
  copyTree(path.join(ROOT, 'dist/assets'), path.join(pkgDir, 'dist/assets'));

  const fontHash = sha256(path.join(pkgDir, FONT_REL));
  if (!fontHash.startsWith(FONT_SHA256_PREFIX))
    fail(`font bytes changed (${fontHash}); W3 requires the binary unchanged`);

  // ── the exact runtime closure of the externals ──
  const closure = runtimeClosure(EXTERNALS);
  const rootModules = path.join(ROOT, 'node_modules');
  for (const dir of closure.keys()) {
    const rel = path.relative(rootModules, dir);
    if (rel.startsWith('..')) fail(`closure package outside node_modules: ${dir}`);
    copyTree(dir, path.join(pkgDir, 'node_modules', rel), new Set(['node_modules']));
  }

  // ── notices ──
  copyFile(path.join(ROOT, 'LICENSE'), path.join(pkgDir, 'LICENSE'));
  copyFile(path.join(ROOT, 'packaging/di/CREDITS.txt'), path.join(pkgDir, 'CREDITS.txt'));
  // The Operator doc ships INSIDE the sealed bytes: refuse to seal while any item that must be
  // copied verbatim from the backend's FINAL contract is still a marker.
  const readme = fs.readFileSync(path.join(ROOT, 'packaging/di/README.txt'), 'utf-8');
  const pending = readme.match(/\[\[FROM-FINAL-CONTRACT:[^\]]*\]\]/g);
  if (pending && !draft)
    fail(`README.txt still has ${pending.length} unfilled FROM-FINAL-CONTRACT item(s)`);
  copyFile(path.join(ROOT, 'packaging/di/README.txt'), path.join(pkgDir, 'README.txt'));

  // ── opt-in extras: the world preset (imported by the user, never auto-loaded) and the
  // `pixel` launcher (opens an already running office; must stay executable) ──
  copyFile(
    path.join(ROOT, 'packaging/di/worlds/expanded-office.json'),
    path.join(pkgDir, 'worlds/expanded-office.json'),
  );
  copyFile(path.join(ROOT, 'packaging/di/pixel'), path.join(pkgDir, 'pixel'));
  fs.chmodSync(path.join(pkgDir, 'pixel'), 0o755);
  const names = fontNames(path.join(ROOT, FONT_REL));
  fs.writeFileSync(
    path.join(pkgDir, 'dist/webview/fonts/OFL.txt'),
    [
      'FS Pixel Sans Unicode Regular (dist/webview/fonts/FSPixelSansUnicode-Regular.ttf)',
      `Font source: ${FONT_SOURCE_URL}`,
      `Licence page: ${FONT_LICENSE_URL}`,
      '',
      'This font is licensed under the SIL Open Font License, Version 1.1 — NOT under the',
      "MIT licence that covers Pixel Agents' code. The binary is shipped byte-for-byte unchanged.",
      '',
      'Copyright notice EMBEDDED in the font binary (name table, nameID 0), verbatim:',
      '----',
      names[0] ?? '(none present)',
      '----',
      `Embedded licence description (nameID 13): ${names[13] ?? '(none present)'}`,
      '',
      'CURRENT SOURCE notice (font source page): Copyright NZWStudios2024, 2025–2026.',
      'The two notices record different facts and are both preserved.',
      '',
      '======================================================================',
      fs.readFileSync(path.join(ROOT, 'packaging/di/OFL-1.1.txt'), 'utf-8'),
    ].join('\n'),
  );

  // THIRD-PARTY notices: MIT code bundled INTO the shipped files (not the closure packages,
  // which ship whole with their own licence files).
  const bundled = new Map();
  for (const n of bundledIntoDiCli()) bundled.set(n, 'dist/di-cli.js');
  const webviewDeps = Object.keys(
    readJson(path.join(ROOT, 'webview-ui/package.json')).dependencies ?? {},
  );
  for (const dir of runtimeClosure(webviewDeps).keys()) {
    const pkg = readJson(path.join(dir, 'package.json'));
    bundled.set(pkg.name, 'dist/webview/assets/index-*.js');
  }
  const notices = ['THIRD-PARTY NOTICES — code bundled into the shipped files of this package', ''];
  for (const [pkgName, where] of [...bundled].sort()) {
    const dir = resolvePackageDir(pkgName, ROOT);
    if (!dir) fail(`bundled package ${pkgName} is not installed (cannot record its notice)`);
    const pkg = readJson(path.join(dir, 'package.json'));
    const text = licenseText(dir);
    if (!text) fail(`bundled package ${pkgName} has no licence file`);
    notices.push(
      '======================================================================',
      `${pkg.name}@${pkg.version} — ${pkg.license ?? 'licence field absent'} — bundled into ${where}`,
      '----------------------------------------------------------------------',
      text,
      '',
    );
  }
  fs.writeFileSync(path.join(pkgDir, 'THIRD-PARTY-NOTICES.txt'), notices.join('\n'));

  // package.json: documents the closure; nothing is installed from it.
  const deps = {};
  for (const external of EXTERNALS) {
    const dir = resolvePackageDir(external, ROOT);
    deps[external] = readJson(path.join(dir, 'package.json')).version;
  }
  fs.writeFileSync(
    path.join(pkgDir, 'package.json'),
    `${JSON.stringify(
      {
        name: 'pixel-agents-di-office',
        version: rootPkg.version,
        private: true,
        description: 'Pixel Agents DI office — standalone, read-only, loopback-only package',
        license:
          'MIT AND OFL-1.1 (see LICENSE, dist/webview/fonts/OFL.txt, THIRD-PARTY-NOTICES.txt)',
        engines: { node: '>=20' },
        main: 'dist/di-cli.js',
        dependencies: deps,
      },
      null,
      2,
    )}\n`,
  );

  // ── inventory: every file + the closure + the proven exclusions ──
  const files = listFiles(pkgDir);
  // OUR source maps are never shipped. The runtime closure is copied byte-exact as installed,
  // so a dependency's own .map files travel with it; they are counted in the inventory.
  const thirdPartyMaps = [];
  for (const f of files) {
    const base = path.basename(f);
    if (W3_EXCLUDED.includes(base)) fail(`excluded file shipped: ${f}`);
    if (!f.endsWith('.map')) continue;
    if (!f.startsWith('node_modules/')) fail(`source map shipped: ${f}`);
    thirdPartyMaps.push(f);
  }
  const inventory = [
    `# ${name} — package inventory (every shipped file, sha256)`,
    ...(draft
      ? [
          `# DRAFT — UNSEALED: README.txt still carries ${pending?.length ?? 0} FROM-FINAL-CONTRACT marker(s); NOT the frozen package`,
        ]
      : []),
    `# build: dist/di-cli.js ${sha256(entry)}`,
    `# font ${FONT_REL} ${fontHash} (unchanged)`,
    '# dist/webview/index.html: DI-only icon link — the dangling /vite.svg replaced by the authored inline SVG (DI_FAVICON_DATA_URI); the build file differs by that one line',
    `# EXCLUDED from dist/webview (W3 ruling; not runtime): ${excludedWebview.join(', ') || 'none'}`,
    `# source maps: none of ours; ${thirdPartyMaps.length} inside the closure's packages (shipped verbatim as installed)`,
    '# runtime closure (externals + transitive, copied from the installed tree):',
    ...[...closure.entries()]
      .sort((a, b) => a[0].localeCompare(b[0]))
      .map(
        ([dir, p]) =>
          `#   ${path.relative(rootModules, dir)} ${p.name}@${p.version} ${p.license ?? 'licence field absent'}`,
      ),
    ...files.map((f) => `${sha256(path.join(pkgDir, f))}  ${f}`),
  ];
  fs.writeFileSync(path.join(pkgDir, 'PACKAGE-INVENTORY.txt'), `${inventory.join('\n')}\n`);

  // ── tarball ──
  const tarball = path.join(out, `${name}.tgz`);
  const tar = spawnSync('tar', ['-czf', tarball, '-C', out, name], { stdio: 'inherit' });
  if (tar.status !== 0) fail('tar failed');
  console.log(`[build-di-package] ${tarball}`);
  // The archive's own digest lives OUTSIDE it (a file inside cannot state it truthfully).
  const digest = sha256(tarball);
  fs.writeFileSync(`${tarball}.sha256`, `${digest}  ${path.basename(tarball)}\n`);
  console.log(`[build-di-package] sha256 ${digest} (sidecar ${path.basename(tarball)}.sha256)`);
  console.log(`[build-di-package] files ${files.length + 1}, closure packages ${closure.size}`);
}

main();
