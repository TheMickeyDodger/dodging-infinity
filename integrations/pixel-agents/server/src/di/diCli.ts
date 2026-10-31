#!/usr/bin/env node

/**
 * DI office entry point — a SEPARATE composition, not the standalone CLI with a flag.
 *
 *   node dist/di-cli.js --state-dir <abs dir> --backend-discovery <abs file> [--port <n>]
 *   node dist/di-cli.js --state-dir <abs dir> --fixture-source <abs fixture script> [--port <n>]
 *
 * What it composes: the agent store, the bundled assets, the layout, and the DI
 * office's own authenticated loopback HTTP/WS surface (diHttpServer.ts), plus a
 * read-only consumer of the DI backend's catalog API (diOffice.ts), and the two relays a
 * person points at a process they started: the Mission request panel (missionRequests.ts,
 * --mission-panel-discovery) and the office delivery adapter (deliveryRequests.ts, with
 * deliveryClient.ts as its transport, --delivery-adapter-discovery). Each answers "not
 * configured" without its flag. (This list omitted the request panel before the delivery
 * flag was added; naming it corrects that older omission.)
 *
 * It also composes ONE terminal route, the Herdr control (herdrRequests.ts over the launch route in
 * herdrLaunch.ts), opt-in: it exists only with --herdr-bin, --herdr-home and --herdr-socket, and answers
 * "not configured", naming them, without. On a person's click it writes one launcher file, focuses the herd's
 * workspace through Herdr's socket API and asks macOS to open Terminal on the launcher. It sends NO input to any
 * pane or terminal. herdrProcess.ts is the entry's ONLY process-starting module (pinned by name in
 * diClientMessages.test.ts).
 *
 * What it never composes — by construction, not by a disabled branch: no hook
 * provider (so no consent ask and no installer), no AgentRuntime (so no session
 * scanners, transcript watching or ~/.claude reads), no PixelAgentsServer (so no
 * server.json / servers/ discovery publishing), no terminal INPUT path (the Herdr route above opens a
 * window and types nothing), no model invocation, and no external asset directories. It never starts,
 * supervises or owns the backend: it only READS the backend's discovery file, and an absent file is an
 * honest "unavailable" state.
 *
 * Every file it persists (settings, layout, seats) lands under --state-dir, injected
 * as the state root before anything resolves a path.
 */

import * as crypto from 'crypto';
import * as fs from 'fs';
import * as path from 'path';

import { AgentStateStore } from '../agentStateStore.js';
import { buildAssetCache } from '../assetReload.js';
import { getConfigFilePath } from '../configPersistence.js';
import {
  DI_CATALOG_WAIT_SECONDS,
  DI_CONSUMER_POLL_INTERVAL_SECONDS,
  DI_SHUTDOWN_DRAIN_TIMEOUT_MS,
  MAX_PORT,
  MIN_PORT,
} from '../constants.js';
import { FileStateAdapter } from '../fileStateAdapter.js';
import { setStateRoot, setStateWriteGuard } from '../stateRoot.js';
import { loadAmbientSprites } from './ambientSprites.js';
import {
  BrowserAccessExists,
  publishBrowserAccess,
  unpublishBrowserAccess,
} from './browserAccess.js';
import type { QueryContractVersion } from './catalogContract.js';
import { DEFAULT_QUERY_CONTRACT_VERSION } from './catalogContract.js';
import { requestDelivery } from './deliveryClient.js';
import { DiDeliveryRequests } from './deliveryRequests.js';
import { handleDiClientMessage, initDiDefaults } from './diClientMessages.js';
import { createDiHttpServer, DI_BIND_HOST } from './diHttpServer.js';
import { DiOffice } from './diOffice.js';
import { makeDiStateWriteGuard, validateDiStateRoot } from './diStateRoot.js';
import type { FixtureSource } from './fixtureSource.js';
import { createFixtureSource, readFixtureScript } from './fixtureSource.js';
import { realLaunchApis } from './herdrProcess.js';
import { DiHerdrRequests } from './herdrRequests.js';
import type { HerdrConfig } from './herdrTarget.js';
import { HERDR_FLAGS, realLauncherDirFs, resolveHerdrConfig } from './herdrTarget.js';
import { DiMissionRequests } from './missionRequests.js';

export interface DiCliArgs {
  stateDir: string;
  /** The canonical backend's discovery file. Absent exactly when `fixtureSource` is set. */
  backendDiscovery?: string;
  /**
   * FIXTURE SOURCE: an in-process fixture script REPLACES the backend. With it, canonical
   * discovery is disabled by composition: no discovery file is read and no backend request is
   * made (fixtureSource.ts). Mutually exclusive with `backendDiscovery`; implies fixturePreview.
   */
  fixtureSource?: string;
  /** The ONE catalog contract version this process reads (launch-time; default 1). */
  contractVersion: QueryContractVersion;
  /** Unset -> OS-assigned port. */
  port?: number;
  /** EXPLICIT: the backend is a synthetic FIXTURE stand-in; label everything as such. */
  fixturePreview: boolean;
  /**
   * item 4: the Mission request panel adapter's discovery file (dimission_panel.py), READ ONLY.
   * Absent: the panel is honestly "not configured". Independent of the catalog backend, so it
   * combines with a fixture source (the world is a fixture; the panel is whatever this names).
   */
  missionPanelDiscovery?: string;
  /**
   * b2b: the office delivery adapter's discovery file (didelivery_office.py), READ ONLY. Absent: every
   * delivery request is answered "not configured". Independent of the catalog backend, like the panel's.
   */
  deliveryAdapterDiscovery?: string;
  /**
   * The Herdr terminal route's target: --herdr-bin, --herdr-home and --herdr-socket, all three or none (null: the route answers "not configured"). Each is a plain
   * absolute path and the socket lies inside the home; nothing is defaulted, found on PATH or read from the environment.
   */
  herdr: HerdrConfig | null;
}

/** The directory (inside the state root) the Herdr route writes its launcher files in. */
export const HERDR_LAUNCHER_DIR_NAME = 'herdr-launchers';

export class DiCliArgsError extends Error {}

const USAGE = `Usage: pixel-agents-di --state-dir <absolute dir> --backend-discovery <absolute file> [--port <n>]
       pixel-agents-di --state-dir <absolute dir> --fixture-source <absolute file> [--port <n>]

  --state-dir <dir>              DI office state root (settings, layout, seats). Required,
                                 absolute, and physically separate from ~/.pixel-agents.
  --backend-discovery <file>     The DI backend's discovery file, READ ONLY. Absolute. Required
                                 unless --fixture-source is given; never both.
  --fixture-source <file>        VISUAL TESTING ONLY: serve a labelled FIXTURE script in-process
                                 INSTEAD of the backend. Canonical discovery is then DISABLED:
                                 no discovery file is read and no backend connection is made.
                                 Implies --fixture-preview. Absolute.
  --mission-panel-discovery <file>
                                 The Mission request panel adapter's discovery file
                                 (dimission_panel.py), READ ONLY. Absolute. Optional: without it
                                 the office shows the request panel as not configured. The
                                 token it holds stays in this server; a browser never sees it.
  --delivery-adapter-discovery <file>
                                 The office delivery adapter's discovery file
                                 (didelivery_office.py), READ ONLY. Absolute. Optional: without it
                                 every delivery request is answered as not configured. The token it
                                 holds stays in this server; a browser never sees it.
  --herdr-bin <file>             The herdr executable. Absolute and plain. With --herdr-home and
  --herdr-home <dir>             --herdr-socket (all three or none) it turns the office's Herdr
  --herdr-socket <file>          control on: a click focuses the Mission's herd workspace in the
                                 Herdr server at that socket, then opens a Terminal window running
                                 plain herdr. HOME for every Herdr command is --herdr-home, and the
                                 socket must lie inside it. Each is a plain absolute path (letters,
                                 digits, '.', '_', '-'). Nothing is searched for or defaulted;
                                 without them the control stays off and says which flags it needs.
                                 The office types nothing into any pane or terminal.
  --port, -p <n>                 Loopback port (default: OS-assigned)
  --contract-version <1|2>       The catalog contract version this process reads (default 1).
                                 ONE version per process: a response of any other version is
                                 shown as INCOMPATIBLE, never negotiated per request.
  --fixture-preview              VISUAL TESTING ONLY: the backend is a synthetic FIXTURE
                                 stand-in. Every view carries a persistent "FIXTURE / VISUAL
                                 TEST" label and LIVE reads "LIVE - FIXTURE DATA". Off by default.

Consumer policy (pinned, not configurable): every catalog request sends
wait_seconds=${DI_CATALOG_WAIT_SECONDS}; the next request starts ${DI_CONSUMER_POLL_INTERVAL_SECONDS} s after the previous RESPONSE
completes, or after the backend's declared min_poll_interval_seconds if that is larger.

The office binds ${DI_BIND_HOST} only. It never starts the backend.

Restart after a KILLED office (e.g. SIGKILL): its owner-only browser access file
<state-dir>/run/browser-access.json is left behind, and the next launch from the same state
dir REFUSES. Refusal is keyed on that file's PRESENCE — it is not proof that a live office
holds it, and the office never guesses (no pid or owner inference). If no DI office is running
from that state dir, delete that one file yourself and start again; layout and settings are
kept. Then open the NEW URL from the new access file: a browser tab from the killed launch
cannot resume (its token was minted for that launch only).`;

export function parseDiArgs(argv: string[]): DiCliArgs | 'help' {
  let stateDir: string | undefined;
  let backendDiscovery: string | undefined;
  let fixtureSource: string | undefined;
  let port: number | undefined;
  let contractVersion: QueryContractVersion = DEFAULT_QUERY_CONTRACT_VERSION;
  let fixturePreview = false;
  let missionPanelDiscovery: string | undefined;
  let deliveryAdapterDiscovery: string | undefined;
  const herdrFlags: { bin?: string; home?: string; socket?: string } = {};
  for (let i = 0; i < argv.length; i++) {
    const flag = argv[i];
    const value = argv[i + 1];
    if (flag === '--help') return 'help';
    if (flag === '--fixture-preview') {
      fixturePreview = true;
      continue;
    }
    if (
      flag === '--state-dir' ||
      flag === '--backend-discovery' ||
      flag === '--fixture-source' ||
      flag === '--port' ||
      flag === '-p'
    ) {
      if (value === undefined) throw new DiCliArgsError(`Missing value for ${flag}`);
      i++;
      if (flag === '--state-dir') stateDir = value;
      else if (flag === '--backend-discovery') backendDiscovery = value;
      else if (flag === '--fixture-source') fixtureSource = value;
      else {
        const parsed = Number(value);
        if (!Number.isInteger(parsed) || parsed < MIN_PORT || parsed > MAX_PORT) {
          throw new DiCliArgsError(`Invalid --port "${value}"`);
        }
        port = parsed;
      }
    } else if (flag === '--mission-panel-discovery') {
      if (value === undefined) throw new DiCliArgsError(`Missing value for ${flag}`);
      i++;
      if (!path.isAbsolute(value)) {
        throw new DiCliArgsError('--mission-panel-discovery must be an absolute path');
      }
      missionPanelDiscovery = path.normalize(value);
    } else if (flag === '--delivery-adapter-discovery') {
      if (value === undefined) throw new DiCliArgsError(`Missing value for ${flag}`);
      i++;
      if (!path.isAbsolute(value)) {
        throw new DiCliArgsError('--delivery-adapter-discovery must be an absolute path');
      }
      deliveryAdapterDiscovery = path.normalize(value);
    } else if (flag === '--herdr-bin' || flag === '--herdr-home' || flag === '--herdr-socket') {
      if (value === undefined) throw new DiCliArgsError(`Missing value for ${flag}`);
      i++;
      // Stored as given: a value that is not already plain and normalised is REFUSED below, never repaired.
      if (flag === '--herdr-bin') herdrFlags.bin = value;
      else if (flag === '--herdr-home') herdrFlags.home = value;
      else herdrFlags.socket = value;
    } else if (flag === '--contract-version') {
      if (value === undefined) throw new DiCliArgsError(`Missing value for ${flag}`);
      i++;
      if (value !== '1' && value !== '2') {
        throw new DiCliArgsError(`Invalid --contract-version "${value}" (supported: 1, 2)`);
      }
      contractVersion = value === '2' ? 2 : 1;
    } else if (flag === '--catalog-wait-seconds') {
      throw new DiCliArgsError(
        `The catalog wait is pinned consumer policy (wait_seconds=${DI_CATALOG_WAIT_SECONDS}); --catalog-wait-seconds is not accepted`,
      );
    } else if (flag === '--host') {
      throw new DiCliArgsError(`The DI office binds ${DI_BIND_HOST} only; --host is not accepted`);
    } else {
      throw new DiCliArgsError(`Unknown argument "${flag}"`);
    }
  }
  if (!stateDir || !path.isAbsolute(stateDir)) {
    throw new DiCliArgsError('--state-dir is required and must be an absolute path');
  }
  const resolvedHerdr = resolveHerdrConfig(herdrFlags);
  if ('problem' in resolvedHerdr) throw new DiCliArgsError(resolvedHerdr.problem);
  const herdr = resolvedHerdr.config;
  if (fixtureSource !== undefined) {
    if (backendDiscovery !== undefined) {
      throw new DiCliArgsError(
        '--fixture-source replaces the backend: it cannot be combined with --backend-discovery',
      );
    }
    if (!path.isAbsolute(fixtureSource)) {
      throw new DiCliArgsError('--fixture-source must be an absolute path');
    }
    return {
      stateDir: path.normalize(stateDir),
      fixtureSource: path.normalize(fixtureSource),
      port,
      contractVersion,
      fixturePreview: true,
      missionPanelDiscovery,
      deliveryAdapterDiscovery,
      herdr,
    };
  }
  if (!backendDiscovery || !path.isAbsolute(backendDiscovery)) {
    throw new DiCliArgsError('--backend-discovery is required and must be an absolute path');
  }
  return {
    stateDir: path.normalize(stateDir),
    backendDiscovery: path.normalize(backendDiscovery),
    port,
    contractVersion,
    fixturePreview,
    missionPanelDiscovery,
    deliveryAdapterDiscovery,
    herdr,
  };
}

/**
 * b2b: the office delivery adapter's relay, with its HTTP client (deliveryClient.ts) as the transport. The relay has no
 * default transport of its own; this is where the office injects it. Without a discovery file, every delivery request is
 * answered "not configured".
 */
export function deliveryRelay(
  discoveryPath: string | null,
  log?: (line: string) => void,
): DiDeliveryRequests {
  return new DiDeliveryRequests(discoveryPath, { request: requestDelivery, log });
}

async function main(): Promise<void> {
  let args: DiCliArgs | 'help';
  try {
    args = parseDiArgs(process.argv.slice(2));
  } catch (err) {
    console.error(
      `[Pixel Agents DI] ${err instanceof Error ? err.message : String(err)}\n\n${USAGE}`,
    );
    process.exit(2);
  }
  if (args === 'help') {
    console.log(USAGE);
    process.exit(0);
  }
  // The standalone debug log appends to an arbitrary path outside any root.
  if (process.env['PIXEL_AGENTS_DEBUG_LOG']) {
    console.error(
      '[Pixel Agents DI] PIXEL_AGENTS_DEBUG_LOG is not supported in DI mode; unset it.',
    );
    process.exit(2);
  }

  // A fixture script is validated BEFORE anything else starts: a bad script refuses to launch.
  let fixture: FixtureSource | null = null;
  if (args.fixtureSource !== undefined) {
    try {
      fixture = createFixtureSource(readFixtureScript(args.fixtureSource));
    } catch (err) {
      console.error(
        `[Pixel Agents DI] refusing to start: fixture source: ${err instanceof Error ? err.message : String(err)}`,
      );
      process.exit(2);
    }
  }

  // FIRST: the state root, before anything resolves a persisted path. Validated on the
  // PHYSICAL path against the shared profile; a conflict refuses to start (fail closed).
  let stateRoot: string;
  try {
    stateRoot = validateDiStateRoot(args.stateDir);
  } catch (err) {
    console.error(
      `[Pixel Agents DI] refusing to start: ${err instanceof Error ? err.message : String(err)}`,
    );
    process.exit(2);
  }
  setStateRoot(stateRoot);
  // Every write beneath the root is contained AT WRITE TIME (a validated root does not
  // contain a symlinked descendant): config, layout, state and the browser access file.
  setStateWriteGuard(makeDiStateWriteGuard(stateRoot));
  fs.mkdirSync(stateRoot, { recursive: true, mode: 0o700 });

  const distRoot = __dirname;
  const staticDir = path.join(distRoot, 'webview');
  // Bundled assets only: DI mode loads no external asset directory.
  const cache = await buildAssetCache(distRoot, []);
  const ambientSprites = loadAmbientSprites(distRoot);
  console.log(`[Pixel Agents] Ambient character sheets: ${ambientSprites.length}`);

  const store = new AgentStateStore();
  const adapter = new FileStateAdapter({ namespace: 'standalone' });
  store.setAdapter(adapter);
  initDiDefaults(adapter, fs.existsSync(getConfigFilePath()));

  const token = crypto.randomUUID();
  const extensionVersion = process.env.PIXEL_AGENTS_VERSION ?? '';
  const office = new DiOffice({
    store,
    discoveryPath: fixture
      ? `fixture-source:${args.fixtureSource ?? ''}`
      : (args.backendDiscovery ?? ''),
    contractVersion: args.contractVersion,
    fixturePreview: args.fixturePreview,
    // The fixture REPLACES both backend seams; without one, the real reader and client are used.
    deps: fixture ? fixture.deps : undefined,
  });
  // item 4: the request panel. Its token stays in THIS process; the browser only ever sends the
  // closed diMissionRequest messages, which are relayed here and answered to the asker alone.
  const missionRequests = new DiMissionRequests(args.missionPanelDiscovery ?? null);
  console.log(
    args.missionPanelDiscovery
      ? `[Pixel Agents DI] mission request panel discovery (read-only): ${args.missionPanelDiscovery}`
      : '[Pixel Agents DI] mission request panel: not configured (--mission-panel-discovery)',
  );
  // b2b: the office delivery adapter. Its token stays in THIS process; the browser only ever sends the closed
  // diDeliveryRequest messages, which are relayed here, one at a time, and answered to the asker alone.
  const deliveryRequests = deliveryRelay(args.deliveryAdapterDiscovery ?? null);
  console.log(
    args.deliveryAdapterDiscovery
      ? `[Pixel Agents DI] office delivery adapter discovery (read-only): ${args.deliveryAdapterDiscovery}`
      : '[Pixel Agents DI] office delivery adapter: not configured (--delivery-adapter-discovery)',
  );
  // The Herdr terminal route. Opt-in: with no --herdr-* flags the relay answers every request "not configured" and names them. It is told the office's
  // LATEST catalog view at each request (never a copy), and given the real Node APIs by herdrProcess.ts, the entry's one process-starting module.
  const herdrLauncherDir = path.join(stateRoot, HERDR_LAUNCHER_DIR_NAME);
  const herdrRequests = new DiHerdrRequests({
    config: args.herdr,
    launcherDir: herdrLauncherDir,
    getView: () => office.getView(),
    apis: realLaunchApis(),
    dirFs: realLauncherDirFs(),
    // The SAME guard the office's other state writes go through, over the SAME validated physical root: not a copy, and not a weaker one.
    contain: makeDiStateWriteGuard(stateRoot),
    nowMs: () => Date.now(),
    log: (line) => console.log(line),
  });
  console.log(
    args.herdr
      ? `[Pixel Agents DI] Herdr terminal route: on (herdr ${args.herdr.bin}, HOME ${args.herdr.home}, socket ${args.herdr.socketPath}; launchers in ${herdrLauncherDir})`
      : `[Pixel Agents DI] Herdr terminal route: not configured (${HERDR_FLAGS.join(', ')})`,
  );
  if (args.fixturePreview)
    console.log(
      '[Pixel Agents DI] FIXTURE PREVIEW: backend is a synthetic fixture stand-in; views are labelled',
    );
  if (fixture)
    console.log(
      `[Pixel Agents DI] canonical backend discovery: DISABLED by --fixture-source (fixture "${fixture.script.fixtureId}": no discovery file is read, no backend connection is made)`,
    );

  const { app, port, forceCloseAll } = await createDiHttpServer({
    port: args.port,
    token,
    store,
    staticDir,
    // The office state is the FIRST message on every authenticated connection (fresh or
    // reconnected): the client knows it is a DI office before any agentCreated can arrive.
    onConnected: (send) => office.sendOfficeState(send),
    onClientMessage: (msg, send) => {
      const disposition = handleDiClientMessage(msg, send, {
        store,
        cache,
        extensionVersion,
        ambientSprites,
        readEvidence: (m, reply) => void office.readEvidence(m, reply),
        readCodeReview: (m, reply) => void office.readCodeReview(m, reply),
        missionRequest: (m, reply) => void missionRequests.handle(m, reply),
        deliveryRequest: (m, reply) => void deliveryRequests.handle(m, reply),
        herdrRequest: (m, reply) => void herdrRequests.handle(m, reply),
      });
      if (typeof disposition === 'object') {
        // The type and the reason only — never the payload.
        console.warn(`[Pixel Agents DI] refused ${String(msg.type)}: ${disposition.refused}`);
      }
      if (msg.type === 'webviewReady') office.sendOfficeState(send);
    },
  });

  // Publish the browser access file BEFORE the office starts polling. EXCLUSIVE: a second
  // launch at the same root refuses to start rather than clobbering the first's file.
  let published: ReturnType<typeof publishBrowserAccess>;
  try {
    published = publishBrowserAccess(stateRoot, {
      url: `http://${DI_BIND_HOST}:${port}/?token=${token}`,
      port,
      token,
    });
  } catch (err) {
    console.error(
      `[Pixel Agents DI] refusing to start: ${err instanceof Error ? err.message : String(err)}`,
    );
    await app.close();
    process.exit(err instanceof BrowserAccessExists ? 2 : 1);
  }
  const accessFile = published.path;

  office.start();

  console.log(`[Pixel Agents DI] state root: ${stateRoot}`);
  if (fixture) console.log(`[Pixel Agents DI] fixture source (read once): ${args.fixtureSource}`);
  else console.log(`[Pixel Agents DI] backend discovery (read-only): ${args.backendDiscovery}`);
  console.log(
    `[Pixel Agents DI] catalog contract version: ${args.contractVersion} (one per process; any other served version is INCOMPATIBLE)`,
  );
  console.log(
    `[Pixel Agents DI] consumer policy: wait_seconds=${DI_CATALOG_WAIT_SECONDS}, poll ${DI_CONSUMER_POLL_INTERVAL_SECONDS} s after each response (or the backend's declared minimum if larger)`,
  );
  // The per-launch browser token is NEVER printed (stdout reaches scrollback, captured
  // logs, evidence and screenshots). The openable URL goes to one owner-only file in the
  // isolated state root; stdout carries safe metadata only: host, port, the URL WITHOUT
  // the token, and where the openable URL is.
  // Last resort on any other exit path: still OWNER-CHECKED, so it never deletes a file
  // another launch published.
  process.on('exit', () => {
    try {
      unpublishBrowserAccess(published);
    } catch {
      // exiting anyway; nothing else to do
    }
  });
  console.log(
    `\n  Pixel Agents DI office listening on http://${DI_BIND_HOST}:${port}/ (loopback only)` +
      `\n  Open the URL stored in ${accessFile}` +
      `\n  (owner-only file; it holds this launch's access token and is removed on shutdown)\n`,
  );

  // Shutdown: stop accepting and DRAIN in-flight handlers FIRST (against a live office),
  // BOUNDED by DI_SHUTDOWN_DRAIN_TIMEOUT_MS; THEN stop the office; THEN unpublish
  // (owner-checked) — the access file outlives the last request — THEN exit. Cleanup runs
  // on BOTH paths, so a forced exit cannot leave a stale file that bricks the next launch.
  // Idempotent: a repeated signal is a no-op.
  let shuttingDown = false;
  const shutdown = (): void => {
    if (shuttingDown) return;
    shuttingDown = true;
    void (async () => {
      let code = 0;
      const outcome = await Promise.race([
        app.close().then(
          () => 'drained' as const,
          (err: unknown) => {
            console.error('[Pixel Agents DI] shutdown: server close failed:', err);
            return 'failed' as const;
          },
        ),
        new Promise<'timeout'>((resolve) =>
          setTimeout(() => resolve('timeout'), DI_SHUTDOWN_DRAIN_TIMEOUT_MS).unref(),
        ),
      ]);
      if (outcome === 'drained') {
        console.log('[Pixel Agents DI] shutdown: server closed, in-flight requests drained');
      } else if (outcome === 'timeout') {
        forceCloseAll();
        code = 1; // visible to whatever supervises the process: the drain did NOT complete
        console.log(
          `[Pixel Agents DI] shutdown: FORCE-TERMINATED after ${DI_SHUTDOWN_DRAIN_TIMEOUT_MS / 1000} s — NOT drained; in-flight work may have been cut`,
        );
      } else {
        code = 1;
      }
      office.stop();
      console.log('[Pixel Agents DI] shutdown: office stopped');
      try {
        const outcome = unpublishBrowserAccess(published);
        console.log(
          outcome === 'not-ours'
            ? `[Pixel Agents DI] shutdown: ${accessFile} is no longer this launch's file; left in place`
            : `[Pixel Agents DI] shutdown: browser access file ${outcome}`,
        );
      } catch (err) {
        code = 1;
        console.error('[Pixel Agents DI] shutdown: could not remove the access file:', err);
      }
      process.exit(code);
    })();
  };
  process.on('SIGINT', shutdown);
  process.on('SIGTERM', shutdown);
}

if (require.main === module) {
  main().catch((err: unknown) => {
    console.error('[Pixel Agents DI] failed to start:', err instanceof Error ? err.message : err);
    process.exit(1);
  });
}
