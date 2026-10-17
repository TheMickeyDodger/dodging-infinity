#!/usr/bin/env node
/**
 * FIXTURE VIEWER — run the DI office against a labelled FIXTURE, in an ISOLATED profile.
 * VISUAL TESTING ONLY. It never reads a backend discovery file and never contacts a backend.
 *
 *   node fixture-viewer.mjs --profile <absolute dir> [--fixture <id|absolute file>]
 *                           [--port <n>] [--world <living-metropolis|metropolis|vast-forest|expanded-office|none>]
 *
 *   --profile   An absolute directory for this viewer ONLY. Created fresh (0700) when absent and
 *               marked with FIXTURE-PROFILE.json; an existing directory is used only if it carries
 *               that marker, so this can never adopt another office's profile.
 *   --fixture   zero-agent (default), crowded, stale, malformed, replacement, reconnect, or an
 *               absolute path to a fixture script.
 *   --port      Loopback port (default 63420). 62892 is refused: it is the live office's port.
 *   --world     The world seeded into a FRESH profile's layout (default living-metropolis); "none" keeps
 *               the bundled default. An existing profile keeps its own layout.
 *
 * What it runs: `node dist/di-cli.js --state-dir <profile>/di-state --fixture-source <fixture>
 * --port <n>`. --fixture-source is the setting that disables canonical discovery: the office is
 * composed with an in-process fixture instead of the discovery reader and the backend client.
 *
 * Stop: Ctrl-C, or `kill -TERM $(cat <profile>/fixture-viewer.pid)`. The office drains and removes
 * its access file. The profile (layout, settings) is kept; delete the directory to discard it.
 */
import { spawn } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const LIVE_OFFICE_PORT = 62892;
const DEFAULT_PORT = 63420;
const MARKER = 'FIXTURE-PROFILE.json';
const MARKER_KIND = 'pixel-agents-di-fixture-viewer-profile';
const FIXTURE_IDS = ['zero-agent', 'crowded', 'stale', 'malformed', 'replacement', 'reconnect'];
const WORLDS = ['living-metropolis', 'metropolis', 'vast-forest', 'expanded-office'];

function fail(message, code = 2) {
  console.error(`[fixture-viewer] ${message}`);
  process.exit(code);
}

/** The office root: the package root (dist/ beside this file) or a checkout (packaging/di/). */
function officeRoot() {
  for (const candidate of [HERE, path.resolve(HERE, '../..')]) {
    if (fs.existsSync(path.join(candidate, 'dist/di-cli.js'))) return candidate;
  }
  fail('dist/di-cli.js not found next to this script or two levels up (build first)', 1);
}

/** Where fixtures and worlds live: the package root, or packaging/di/ in a checkout. */
function dataDir(root) {
  return fs.existsSync(path.join(root, 'fixtures')) ? root : path.join(root, 'packaging/di');
}

function parseArgs(argv) {
  const out = { fixture: 'zero-agent', port: DEFAULT_PORT, world: 'living-metropolis' };
  for (let i = 0; i < argv.length; i++) {
    const flag = argv[i];
    const value = argv[i + 1];
    if (flag === '--help') {
      console.log(fs.readFileSync(fileURLToPath(import.meta.url), 'utf-8').split('*/')[0]);
      process.exit(0);
    }
    if (!['--profile', '--fixture', '--port', '--world'].includes(flag))
      fail(`unknown argument "${flag}"`);
    if (value === undefined) fail(`missing value for ${flag}`);
    i++;
    if (flag === '--profile') out.profile = value;
    else if (flag === '--fixture') out.fixture = value;
    else if (flag === '--world') out.world = value;
    else out.port = Number(value);
  }
  if (!out.profile || !path.isAbsolute(out.profile)) fail('--profile <absolute dir> is required');
  if (!Number.isInteger(out.port) || out.port < 1024 || out.port > 65535)
    fail('--port must be an integer in 1024..65535');
  if (out.port === LIVE_OFFICE_PORT)
    fail(`port ${LIVE_OFFICE_PORT} belongs to the live office and is refused`);
  if (out.world !== 'none' && !WORLDS.includes(out.world))
    fail(`--world must be one of ${WORLDS.join(', ')} or none`);
  return out;
}

const args = parseArgs(process.argv.slice(2));
const root = officeRoot();
const data = dataDir(root);
const fixtureFile = path.isAbsolute(args.fixture)
  ? args.fixture
  : FIXTURE_IDS.includes(args.fixture)
    ? path.join(data, 'fixtures', `${args.fixture}.FIXTURE.json`)
    : fail(`--fixture must be one of ${FIXTURE_IDS.join(', ')} or an absolute file`);
if (!fs.existsSync(fixtureFile)) fail(`fixture not found: ${fixtureFile}`);

const profile = path.normalize(args.profile);
const stateDir = path.join(profile, 'di-state');
const markerPath = path.join(profile, MARKER);
if (fs.existsSync(profile)) {
  let marker = null;
  try {
    marker = JSON.parse(fs.readFileSync(markerPath, 'utf-8'));
  } catch {
    marker = null;
  }
  if (!marker || marker.kind !== MARKER_KIND)
    fail(`${profile} exists but is not a fixture-viewer profile (no ${MARKER}); refusing to use it`);
  if (fs.existsSync(path.join(stateDir, 'run/browser-access.json')))
    fail(`an office may still be running from ${stateDir} (its access file exists); stop it first`);
} else {
  fs.mkdirSync(stateDir, { recursive: true, mode: 0o700 });
  fs.chmodSync(profile, 0o700);
  if (args.world !== 'none') {
    // The supported import format: the world file IS an office layout.
    fs.copyFileSync(path.join(data, 'worlds', `${args.world}.json`), path.join(stateDir, 'layout.json'));
  }
  fs.writeFileSync(
    markerPath,
    `${JSON.stringify(
      {
        kind: MARKER_KIND,
        created_at: new Date().toISOString(),
        world: args.world,
        note: 'Isolated FIXTURE viewer profile. Synthetic data only; canonical discovery disabled (--fixture-source).',
      },
      null,
      2,
    )}\n`,
    { mode: 0o600 },
  );
}

const pidFile = path.join(profile, 'fixture-viewer.pid');
fs.writeFileSync(pidFile, `${process.pid}\n`, { mode: 0o600 });
const env = { ...process.env };
delete env.PIXEL_AGENTS_DEBUG_LOG;
const childArgs = [
  path.join(root, 'dist/di-cli.js'),
  '--state-dir',
  stateDir,
  '--fixture-source',
  fixtureFile,
  '--port',
  String(args.port),
];
console.log('[fixture-viewer] FIXTURE / VISUAL TEST ONLY — synthetic data, no backend contact');
console.log(`[fixture-viewer] profile:   ${profile}`);
console.log(`[fixture-viewer] state dir: ${stateDir}`);
console.log(`[fixture-viewer] fixture:   ${fixtureFile}`);
console.log(`[fixture-viewer] office:    ${path.join(root, 'dist/di-cli.js')}`);
console.log(`[fixture-viewer] preview:   http://127.0.0.1:${args.port}/  (open with: pixel --state-dir ${stateDir})`);
console.log(`[fixture-viewer] stop:      kill -TERM ${process.pid}   (pid file ${pidFile})`);
const child = spawn(process.execPath, childArgs, { stdio: 'inherit', env });
const forward = (signal) => {
  if (child.exitCode === null) child.kill(signal);
};
process.on('SIGINT', () => forward('SIGINT'));
process.on('SIGTERM', () => forward('SIGTERM'));
child.on('exit', (code, signal) => {
  try {
    fs.rmSync(pidFile, { force: true });
  } catch {
    // leaving a stale pid file is harmless: it is re-written on the next start
  }
  process.exit(code ?? (signal ? 1 : 0));
});
