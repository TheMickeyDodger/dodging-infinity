/**
 * feature13 — the SELF-ISOLATING sandbox the owned DI specs share (the same pattern officeUsability.spec.ts
 * and panelViewports.spec.ts use inline): a mkdtemp sandbox, a fake HOME, an ephemeral 127.0.0.1 FIXTURE
 * backend stand-in (bearer and Host enforced), a sandbox --state-dir, and the PRODUCTION BUILT office
 * (dist/di-cli.js) opened only through the access file the CLI names (the token is never printed).
 * Nothing live is touched. SYNTHETIC FIXTURE content only.
 */
import { type ChildProcess, spawn } from 'node:child_process';
import crypto from 'node:crypto';
import fs from 'node:fs';
import http from 'node:http';
import type { AddressInfo } from 'node:net';
import os from 'node:os';
import path from 'node:path';

import { expect, type Page } from '@playwright/test';

const REPO_ROOT = path.join(__dirname, '../..');
const DI_BUNDLE = path.join(REPO_ROOT, 'dist', 'di-cli.js');
const BACKEND_TOKEN = 'fixture-backend-token-e2e-0123456789';

export const delay = (ms: number): Promise<void> => new Promise((r) => setTimeout(r, ms));

export interface SandboxOffice {
  url: string;
  token: string;
  stop(): Promise<void>;
}

/** Start the built office over a labelled FIXTURE backend that answers with `envelopeAt(now)`. */
export async function startSandboxOffice(opts: {
  prefix: string;
  envelopeAt: (nowSeconds: number) => unknown;
  contractVersion?: 1 | 2;
  /** `--fixture-preview`: the PRODUCT labels itself ("LIVE - FIXTURE DATA", the FIXTURE / VISUAL TEST banner). */
  fixturePreview?: boolean;
}): Promise<SandboxOffice> {
  const sandbox = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), `${opts.prefix}-`)));
  const fakeHome = path.join(sandbox, 'home');
  fs.mkdirSync(fakeHome);
  let backendPort = 0;
  const backend = http.createServer((req, res) => {
    const authorized =
      req.headers.authorization === `Bearer ${BACKEND_TOKEN}` &&
      req.headers.host === `127.0.0.1:${backendPort}`;
    if (!authorized) {
      res.writeHead(401);
      res.end();
      return;
    }
    res.writeHead(200, {
      'content-type': 'application/json; charset=utf-8',
      'x-di-freshness-health': 'fresh',
    });
    res.end(JSON.stringify(opts.envelopeAt(Math.floor(Date.now() / 1000))));
  });
  await new Promise<void>((r) => backend.listen(0, '127.0.0.1', () => r()));
  backendPort = (backend.address() as AddressInfo).port;
  const runtimeDir = path.join(sandbox, 'runtime');
  fs.mkdirSync(runtimeDir, { mode: 0o700 });
  fs.chmodSync(runtimeDir, 0o700);
  const discovery = path.join(runtimeDir, 'backend-discovery.json');
  fs.writeFileSync(
    discovery,
    JSON.stringify({
      url: `http://127.0.0.1:${backendPort}`,
      port: backendPort,
      token: BACKEND_TOKEN,
    }),
    { mode: 0o600 },
  );
  let output = '';
  const child: ChildProcess = spawn(
    process.execPath,
    [
      DI_BUNDLE,
      '--state-dir',
      path.join(sandbox, 'di-state'),
      '--backend-discovery',
      discovery,
      ...(opts.contractVersion === 2 ? ['--contract-version', '2'] : []),
      ...(opts.fixturePreview ? ['--fixture-preview'] : []),
    ],
    {
      env: { ...process.env, HOME: fakeHome, USERPROFILE: fakeHome, PIXEL_AGENTS_DEBUG_LOG: '' },
      stdio: ['ignore', 'pipe', 'pipe'],
    },
  );
  child.stdout?.on('data', (c: Buffer) => (output += c.toString()));
  child.stderr?.on('data', (c: Buffer) => (output += c.toString()));
  const stop = async () => {
    child.kill('SIGTERM');
    await delay(300);
    await new Promise<void>((r) => backend.close(() => r()));
    fs.rmSync(sandbox, { recursive: true, force: true });
  };
  try {
    let accessFile = '';
    await expect
      .poll(
        () =>
          (accessFile = /Open the URL stored in (\S+)/.exec(output)?.[1] ?? '') &&
          fs.existsSync(accessFile),
        { timeout: 20_000 },
      )
      .toBe(true);
    const access = JSON.parse(fs.readFileSync(accessFile, 'utf-8')) as {
      url: string;
      token: string;
    };
    return { url: access.url, token: access.token, stop };
  } catch (err) {
    await stop();
    throw err;
  }
}

/** Open the office, never echoing the tokened URL in a failure. */
export async function openOffice(page: Page, office: SandboxOffice): Promise<void> {
  await page.addInitScript(() => {
    (window as unknown as { __PIXEL_AGENTS_E2E?: boolean }).__PIXEL_AGENTS_E2E = true;
  });
  try {
    await page.goto(office.url);
  } catch (err) {
    throw new Error(`navigation failed: ${String(err).split(office.token).join('<REDACTED>')}`);
  }
  await expect(page.getByTestId('di-office-panel')).toBeVisible({ timeout: 30_000 });
}

// ── the real canvas click (the same math officeUsability.spec.ts uses) ──────────────────────────
const TILE_SIZE = 16;
const SITTING_OFFSET = 6;
const HIT_HEIGHT = 24;

export interface HooksWindow {
  __pixelAgentsTestHooks?: {
    getCharacters?: () => Array<{
      id: number;
      x: number;
      y: number;
      state: string;
      matrixEffect: string | null;
      isGreeter?: boolean;
    }>;
    getView?: () => { zoom: number; panX: number; panY: number; cols: number; rows: number };
    getDiStored?: () => unknown;
  };
}

/** Characters seated and typing (the office drew every live member). */
export const typingCount = (page: Page): Promise<number> =>
  page.evaluate(
    () =>
      ((window as unknown as HooksWindow).__pixelAgentsTestHooks?.getCharacters?.() ?? []).filter(
        (c) => !c.isGreeter && c.state === 'type' && !c.matrixEffect,
      ).length,
  );

/** Characters drawn (every live member, spawn effect finished), whatever they are doing. */
export const drawnCount = (page: Page): Promise<number> =>
  page.evaluate(
    () =>
      ((window as unknown as HooksWindow).__pixelAgentsTestHooks?.getCharacters?.() ?? []).filter(
        (c) => !c.isGreeter && !c.matrixEffect,
      ).length,
  );

/** The office state the APPLICATION HANDLER stored last. */
export const storedState = <T>(page: Page): Promise<T> =>
  page.evaluate(
    () => (window as unknown as HooksWindow).__pixelAgentsTestHooks?.getDiStored?.() ?? null,
  ) as Promise<T>;

/** A point inside a character's canvas hit box, and whether the canvas is what is on top there. */
export async function characterPoint(
  page: Page,
  id: number,
): Promise<{
  x: number;
  y: number;
  onCanvas: boolean;
  topElement: string;
  /** The elements under the point, topmost first, read in the SAME evaluate (the same synchronous turn) as `topElement`: what covered the point when `onCanvas` was decided. Observation only. */
  stack: Array<{
    tag: string;
    id: string | null;
    testid: string | null;
    label: string;
    rect: number[];
  }>;
}> {
  return await page.evaluate(
    ({ id, tile, sit, hit }) => {
      const hooks = (window as unknown as HooksWindow).__pixelAgentsTestHooks;
      const ch = (hooks?.getCharacters?.() ?? []).find((c) => c.id === id);
      if (!ch) throw new Error(`character ${id} not found`);
      const view = hooks?.getView?.();
      if (!view) throw new Error('getView test hook missing');
      const canvas = Array.from(document.querySelectorAll('canvas')).sort(
        (a, b) => b.clientWidth * b.clientHeight - a.clientWidth * a.clientHeight,
      )[0];
      const rect = canvas.getBoundingClientRect();
      const dpr = window.devicePixelRatio || 1;
      const offX =
        Math.floor((Math.round(rect.width * dpr) - view.cols * tile * view.zoom) / 2) +
        Math.round(view.panX);
      const offY =
        Math.floor((Math.round(rect.height * dpr) - view.rows * tile * view.zoom) / 2) +
        Math.round(view.panY);
      const worldY = ch.y + (ch.state === 'type' ? sit : 0) - hit / 2;
      const x = rect.left + (offX + ch.x * view.zoom) / dpr;
      const y = rect.top + (offY + worldY * view.zoom) / dpr;
      const top = document.elementFromPoint(x, y);
      // the stack under the point, read in this same synchronous turn as `top`: the identity and rectangle of whatever covers the point when `onCanvas` is decided
      const stack = document
        .elementsFromPoint(x, y)
        .slice(0, 6)
        .map((el) => {
          const box = el.getBoundingClientRect();
          return {
            tag: el.tagName,
            id: el.id || null,
            testid: el.getAttribute('data-testid'),
            label: el.getAttribute('aria-label') ?? (el.textContent ?? '').trim().slice(0, 40),
            rect: [
              Math.round(box.left),
              Math.round(box.top),
              Math.round(box.right),
              Math.round(box.bottom),
            ],
          };
        });
      return {
        x,
        y,
        onCanvas: top === canvas,
        topElement: top ? `${top.tagName}${top.id ? `#${top.id}` : ''}` : 'none',
        stack,
      };
    },
    { id, tile: TILE_SIZE, sit: SITTING_OFFSET, hit: HIT_HEIGHT },
  );
}

/**
 * The BUILD these measurements or frames come from, bound to them (the same logic panelViewports.spec.ts
 * uses): sha256 and size of dist/di-cli.js and every dist/webview file and asset, and sha256 of the DI panel
 * sources; it FAILS if any built file is older than the newest source it was built from, so a capture can
 * never silently describe an older build than the sources it claims.
 */
export function buildIdentity(): Record<string, unknown> {
  const sha = (file: string) =>
    crypto.createHash('sha256').update(fs.readFileSync(file)).digest('hex');
  const rel = (file: string) => path.relative(REPO_ROOT, file);
  const walk = (dir: string): string[] =>
    fs.existsSync(dir)
      ? fs
          .readdirSync(dir, { withFileTypes: true })
          .flatMap((e) =>
            e.isDirectory() ? walk(path.join(dir, e.name)) : [path.join(dir, e.name)],
          )
      : [];
  const webview = path.join(REPO_ROOT, 'dist', 'webview');
  const built = [
    DI_BUNDLE,
    ...fs
      .readdirSync(webview)
      .map((f) => path.join(webview, f))
      .filter((f) => fs.statSync(f).isFile()),
    ...walk(path.join(webview, 'assets')).filter((f) => /\.(js|css)$/.test(f)),
  ];
  const sources = ['webview-ui/src', 'server/src', 'core/src'].flatMap((d) =>
    walk(path.join(REPO_ROOT, d)),
  );
  const oldestBuilt = Math.min(...built.map((f) => fs.statSync(f).mtimeMs));
  const newestSource = sources.reduce(
    (a, f) =>
      fs.statSync(f).mtimeMs > a.mtimeMs ? { file: f, mtimeMs: fs.statSync(f).mtimeMs } : a,
    { file: '', mtimeMs: 0 },
  );
  if (oldestBuilt < newestSource.mtimeMs)
    throw new Error(
      `STALE BUILD: ${rel(newestSource.file)} is newer than the built bundle; run the production build first`,
    );
  const panelSources = walk(path.join(REPO_ROOT, 'webview-ui', 'src', 'components'))
    .filter((f) => /\/(Di|di)[A-Za-z]*\.(tsx?|css)$/.test(f))
    .concat(
      ['App.tsx', 'constants.ts', 'index.css'].map((f) =>
        path.join(REPO_ROOT, 'webview-ui', 'src', f),
      ),
    )
    .filter((f) => fs.existsSync(f))
    .sort();
  return {
    builtAt: new Date(oldestBuilt).toISOString(),
    newestSource: {
      file: rel(newestSource.file),
      at: new Date(newestSource.mtimeMs).toISOString(),
    },
    built: built.map((f) => ({ file: rel(f), bytes: fs.statSync(f).size, sha256: sha(f) })),
    panelSources: panelSources.map((f) => ({ file: rel(f), sha256: sha(f) })),
  };
}
