/**
 * DI office USABILITY in the PRODUCTION BUILD (dist/di-cli.js + dist/webview), against a
 * labelled FIXTURE backend stand-in serving eleven realistic-length synthetic Missions.
 * FIXTURE evidence for the layout judgment only — NOT live acceptance: the Operator's
 * visual gate still needs the reviewed backend's real data.
 *
 * Writes full-page screenshots (DOM panel + canvas + selection in one frame) to
 * $DI_SHOT_DIR. Opens the office only through the URL the CLI prints.
 */
import { type ChildProcess, spawn } from 'node:child_process';
import fs from 'node:fs';
import http from 'node:http';
import type { AddressInfo } from 'node:net';
import os from 'node:os';
import path from 'node:path';

import { expect, type Locator, type Page, test, type WebSocketRoute } from '@playwright/test';

import { expectDockNamesMission, memberRowName } from './dockIdentity';
import { buildRealisticFixture } from './realisticFixture';

const REPO_ROOT = path.join(__dirname, '../..');
const DI_BUNDLE = path.join(REPO_ROOT, 'dist', 'di-cli.js');
const BACKEND_TOKEN = 'fixture-backend-token-e2e-0123456789';
// Mirrors of webview constants (TILE_SIZE, CHARACTER_SITTING_OFFSET_PX, CHARACTER_HIT_HEIGHT).
// If they drift the real click misses and this spec FAILS. Zoom, pan and grid size are READ
// from the renderer (getView), never mirrored.
const TILE_SIZE = 16;
const SITTING_OFFSET = 6;
const HIT_HEIGHT = 24;
/** FIXTURE VARIATION for the silent-server expiry check (canonical max age is 60 s). */
const SHORT_VALIDITY_SECONDS = 8;

interface CharacterSnapshot {
  id: number;
  x: number;
  y: number;
  state: string;
  matrixEffect: string | null;
  isGreeter?: boolean;
}
interface HooksWindow {
  __pixelAgentsTestHooks?: {
    getCharacters?: () => CharacterSnapshot[];
    getView?: () => { zoom: number; panX: number; panY: number; cols: number; rows: number };
    getSeats?: () => Array<{ uid: string; col: number; row: number; areaLabel: string | null }>;
    getShowAreas?: () => boolean;
    /** The last office state the APPLICATION HANDLER received (e2e-only accessor). */
    getDiStored?: () => unknown;
    /** Every message the APPLICATION HANDLER received (e2e-only). */
    messageLog?: Array<{ type: string; id?: number }>;
  };
  __PIXEL_AGENTS_E2E?: boolean;
}
interface DiMemberWire {
  memberRef: string;
  characterId: number | null;
  live: boolean;
  taskId: { value: string | null };
}
interface DiStateWire {
  times: { generatedAt: number | null; validUntil: number | null };
  missions: {
    missions: Array<{ missionRef: string; nativeId: string; members: { members: DiMemberWire[] } }>;
  };
}

/** The renderer's view + canvas, for the FRAMING check (read, never re-derived). */
async function framingOf(page: Page) {
  return await page.evaluate(() => {
    const view = (window as unknown as HooksWindow).__pixelAgentsTestHooks!.getView!();
    const canvas = Array.from(document.querySelectorAll('canvas')).sort(
      (a, b) => b.clientWidth * b.clientHeight - a.clientWidth * a.clientHeight,
    )[0];
    const rect = canvas.getBoundingClientRect();
    return {
      view,
      cssW: rect.width,
      cssH: rect.height,
      backingW: canvas.width,
      backingH: canvas.height,
      dpr: window.devicePixelRatio || 1,
    };
  });
}

/** Integer device-pixel fit (the formula under test): floor(min(backing / grid)), 1..10. */
function expectedFit(f: Awaited<ReturnType<typeof framingOf>>): number {
  const fit = Math.floor(
    Math.min(f.backingW / (f.view.cols * TILE_SIZE), f.backingH / (f.view.rows * TILE_SIZE)),
  );
  return Math.max(1, Math.min(10, fit));
}

const shotDir = process.env['DI_SHOT_DIR'];

function delay(ms: number): Promise<void> {
  return new Promise((r) => setTimeout(r, ms));
}

async function shot(page: Page, name: string): Promise<void> {
  if (!shotDir) throw new Error('DI_SHOT_DIR must be set');
  // DI_SHOT_SUFFIX: a later run never reuses an earlier run's artifact names.
  const suffix = process.env['DI_SHOT_SUFFIX'] ?? '';
  await page.screenshot({
    path: path.join(shotDir, name.replace(/\.png$/, `${suffix}.png`)),
    fullPage: true,
  });
}

/** Self-labels every screenshot as FIXTURE evidence (test-only DOM, pointer-events none). */
async function labelFixture(page: Page): Promise<void> {
  await page.evaluate(() => {
    if (document.getElementById('fixture-evidence-label')) return;
    const label = document.createElement('div');
    label.id = 'fixture-evidence-label';
    label.textContent = 'FIXTURE BACKEND: synthetic content, NOT live evidence';
    Object.assign(label.style, {
      position: 'fixed',
      left: '330px',
      bottom: '0px',
      zIndex: '60',
      background: '#000',
      color: '#ff0',
      font: '11px monospace',
      padding: '1px 6px',
      pointerEvents: 'none',
    });
    document.body.appendChild(label);
  });
}

/** The office state the APPLICATION HANDLER stored last (not a socket spy). */
async function latestDiState(page: Page): Promise<DiStateWire> {
  return (await page.evaluate(
    () => (window as unknown as HooksWindow).__pixelAgentsTestHooks?.getDiStored?.() ?? null,
  )) as unknown as DiStateWire;
}

/** Messages of this type the APPLICATION HANDLER received since the page loaded. */
async function handled(page: Page, type: string): Promise<Array<{ type: string; id?: number }>> {
  return await page.evaluate(
    (t) =>
      ((window as unknown as HooksWindow).__pixelAgentsTestHooks?.messageLog ?? []).filter(
        (m) => m.type === t,
      ),
    type,
  );
}

async function seatedCharacters(page: Page): Promise<CharacterSnapshot[]> {
  return await page.evaluate(() =>
    ((window as unknown as HooksWindow).__pixelAgentsTestHooks?.getCharacters?.() ?? []).filter(
      (c) => !c.isGreeter,
    ),
  );
}

/** Screen point inside a character's canvas hit box, and what element is on top there. */
async function characterPoint(
  page: Page,
  id: number,
): Promise<{ x: number; y: number; onCanvas: boolean; topElement: string }> {
  return await page.evaluate(
    ({ id, tile, sit, hit }) => {
      const hooks = (window as unknown as HooksWindow).__pixelAgentsTestHooks;
      const ch = (hooks?.getCharacters?.() ?? []).find((c) => c.id === id);
      if (!ch) throw new Error(`character ${id} not found`);
      // The renderer's ACTUAL view (integer zoom, pan, grid) — never re-derived here.
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
      return {
        x,
        y,
        onCanvas: top === canvas,
        topElement: top ? `${top.tagName}${top.id ? `#${top.id}` : ''}` : 'none',
      };
    },
    { id, tile: TILE_SIZE, sit: SITTING_OFFSET, hit: HIT_HEIGHT },
  );
}

async function expectInViewport(page: Page, target: string | Locator): Promise<void> {
  const testId = typeof target === 'string' ? target : 'element';
  const box = await (typeof target === 'string' ? page.getByTestId(target) : target).boundingBox();
  const viewport = page.viewportSize()!;
  expect(box, `${testId} rendered`).not.toBeNull();
  expect(box!.y, `${testId} top`).toBeGreaterThanOrEqual(0);
  expect(box!.y + box!.height, `${testId} bottom within the viewport`).toBeLessThanOrEqual(
    viewport.height,
  );
  expect(box!.x + box!.width, `${testId} right within the viewport`).toBeLessThanOrEqual(
    viewport.width,
  );
}

/**
 * feature13: the full-size INSPECTOR, reached as a person does. A Mission's card carries an explicit
 * "Show details"; the Selected dock carries one for the selection. `read` runs inside the open
 * dialog; the dialog is then closed with Escape (focus returns to the opener).
 */
async function inInspector<T>(
  page: Page,
  opener: Locator,
  read: (dialog: Locator) => Promise<T>,
): Promise<T> {
  await opener.click();
  const dialog = page.getByRole('dialog');
  await expect(dialog).toBeVisible({ timeout: 5_000 });
  try {
    return await read(dialog);
  } finally {
    await page.keyboard.press('Escape');
    await expect(dialog).toHaveCount(0);
  }
}
const missionInspector = <T>(page: Page, missionRef: string, read: (d: Locator) => Promise<T>) =>
  inInspector(
    page,
    page.locator(`[data-mission-ref="${missionRef}"]`).getByTestId('di-open-inspector-mission'),
    read,
  );
const selectionInspector = <T>(page: Page, read: (d: Locator) => Promise<T>) =>
  inInspector(
    page,
    page.getByTestId('di-selected-agent').getByTestId('di-open-inspector-selection'),
    read,
  );

/** The "model confirmed running" value of a record, inside an open inspector. */
const modelConfirmed = (scope: Locator) =>
  scope
    .getByText('model confirmed running', { exact: true })
    .locator('xpath=following-sibling::span[1]');

/** One member's raw provenance row in an open Mission inspector (identified by its complete ref). */
const provenanceRow = (dialog: Locator, memberRef: string) =>
  dialog
    .getByTestId('di-inspector-provenance')
    .locator('.di-op-row')
    .filter({ hasText: memberRef })
    // the "status derived from" row cites every member by ref too: it is not the member's own row
    .filter({ hasNotText: 'status derived from' });

/**
 * A complete source-qualified ref is shown WHOLE: the element's text is exactly the ref (no
 * ellipsis) and every one of its line boxes lies inside its dock or dialog.
 */
async function refShownWhole(ref: Locator, expected: string): Promise<void> {
  const result = await ref.evaluate((el) => {
    const box = el
      .closest('[data-testid="di-selected-agent"], [role="dialog"]')!
      .getBoundingClientRect();
    const rects = Array.from(el.getClientRects());
    return {
      text: el.textContent,
      ellipsis: getComputedStyle(el).textOverflow === 'ellipsis',
      inside:
        rects.length > 0 &&
        rects.every((r) => r.left >= box.left - 0.5 && r.right <= box.right + 0.5),
    };
  });
  expect(result).toEqual({ text: expected, ellipsis: false, inside: true });
}

/** A Mission card's roster rows are inside a closed "Team members" disclosure: open it, as a person does. */
async function rosterRow(page: Page, missionRef: string, logical: string): Promise<Locator> {
  const card = page.locator(`[data-mission-ref="${missionRef}"]`);
  const roster = card.getByTestId('di-mission-roster');
  if (!(await roster.evaluate((el) => (el as HTMLDetailsElement).open)))
    await roster.locator('summary').click();
  return card
    .getByTestId('di-member-row')
    .filter({ hasText: memberRowName(logical) })
    .first();
}

test('DI office production build: usable with eleven realistic-length FIXTURE Missions', async ({
  page,
}) => {
  const fixture = buildRealisticFixture();
  const sandbox = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), 'di-ux-')));
  const fakeHome = path.join(sandbox, 'home');
  fs.mkdirSync(fakeHome);

  // ── FIXTURE backend stand-in: bearer + Host enforced, switchable fresh / throttled ──
  let mode: 'fresh' | 'throttled' | 'shortValidity' = 'fresh';
  let roster: 'base' | 'changed' = 'base';
  let capture: 'available' | 'starved' = 'available';
  let backendPort = 0;
  const requestLog: Array<{ atMs: number; url: string }> = [];
  const backend = http.createServer((req, res) => {
    const authorized =
      req.headers.authorization === `Bearer ${BACKEND_TOKEN}` &&
      req.headers.host === `127.0.0.1:${backendPort}`;
    if (!authorized) {
      res.writeHead(401);
      res.end();
      return;
    }
    requestLog.push({ atMs: Date.now(), url: req.url ?? '' });
    const now = Math.floor(Date.now() / 1000);
    const throttled = mode === 'throttled';
    // Throttled header values are an ASSUMED fixture variation; the panel keys on the body.
    res.writeHead(200, {
      'content-type': 'application/json; charset=utf-8',
      'x-di-freshness-health': throttled ? 'stale' : 'fresh',
      ...(throttled ? { 'x-di-freshness-problem': 'catalog_refresh_throttled' } : {}),
    });
    res.end(
      JSON.stringify(
        throttled
          ? fixture.throttledEnvelopeAt(now, 3)
          : fixture.envelopeAt(
              now,
              mode === 'shortValidity' ? SHORT_VALIDITY_SECONDS : undefined,
              roster,
              capture,
            ),
      ),
    );
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

  // ── the PRODUCTION-BUILT office ──
  let output = '';
  const child: ChildProcess = spawn(
    process.execPath,
    [DI_BUNDLE, '--state-dir', path.join(sandbox, 'di-state'), '--backend-discovery', discovery],
    {
      env: { ...process.env, HOME: fakeHome, USERPROFILE: fakeHome, PIXEL_AGENTS_DEBUG_LOG: '' },
      stdio: ['ignore', 'pipe', 'pipe'],
    },
  );
  child.stdout?.on('data', (c: Buffer) => (output += c.toString()));
  child.stderr?.on('data', (c: Buffer) => (output += c.toString()));

  try {
    // The openable URL is handed over through the owner-only access file, never stdout.
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
    expect(output, 'the office token never reaches captured stdout').not.toContain(access.token);
    const policy = /consumer policy: wait_seconds=(\d+), poll (\d+) s after each response/.exec(
      output,
    );
    expect(policy, 'the launch output states the pinned consumer policy').not.toBeNull();
    const [policyWait, policyPollSeconds] = [Number(policy![1]), Number(policy![2])];

    // The e2e flag ONLY (enables the application's own test hooks). No WebSocket spy anywhere:
    // received messages are observed at the APPLICATION HANDLER (messageLog, getDiStored).
    // The layout check runs at 1440x900 (DPR 2 from the config; DPR 1 in its own context below).
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.addInitScript(() => {
      (window as unknown as HooksWindow).__PIXEL_AGENTS_E2E = true;
    });
    // The page's /ws runs through a test proxy: transparent until a step cuts the socket,
    // refuses reconnects, or withholds server -> page messages (a silent server).
    let refuseConnections = false;
    let forwardToPage = true;
    /** PARTIAL DELIVERY: forward ONE message of this type, then cut the connection at once. */
    let cutAfterType: string | null = null;
    const sockets: Array<{ page: WebSocketRoute; server: WebSocketRoute }> = [];
    /** The agents of every existingAgents frame the proxy FORWARDED (delivery-side record). */
    const forwardedExistingAgents: number[][] = [];
    /** A frame's type and existingAgents payload, whatever its representation. */
    const frameInfo = (message: string | Buffer): { type: string | null; agents?: number[] } => {
      try {
        const text = typeof message === 'string' ? message : Buffer.from(message).toString('utf-8');
        const parsed = JSON.parse(text) as { type?: unknown; agents?: unknown };
        const type = typeof parsed.type === 'string' ? parsed.type : null;
        return type === 'existingAgents' && Array.isArray(parsed.agents)
          ? { type, agents: parsed.agents as number[] }
          : { type };
      } catch {
        return { type: null };
      }
    };
    await page.routeWebSocket(/\/ws\?token=/, (ws) => {
      if (refuseConnections) {
        void ws.close({ code: 1001, reason: 'FIXTURE: server unreachable' });
        return;
      }
      const server = ws.connectToServer();
      ws.onMessage((message) => server.send(message));
      server.onMessage((message) => {
        if (!forwardToPage) return;
        const { type, agents } = frameInfo(message);
        if (agents) forwardedExistingAgents.push(agents);
        ws.send(message);
        if (cutAfterType !== null) {
          if (type === cutAfterType) {
            cutAfterType = null;
            forwardToPage = false; // nothing after it reaches the page (its office message is lost)
            refuseConnections = true;
            void ws.close({ code: 1001, reason: `FIXTURE: lost right after ${String(type)}` });
            void server.close();
          }
        }
      });
      sockets.push({ page: ws, server });
    });
    const diMessageCount = async (): Promise<number> =>
      (await handled(page, 'diOfficeState')).length;
    const typingCount = async (): Promise<number> =>
      (await seatedCharacters(page)).filter((c) => c.state === 'type' && !c.matrixEffect).length;

    // Redact at capture time: a failed navigation's call log would echo the tokened URL.
    try {
      await page.goto(access.url);
    } catch (err) {
      throw new Error(
        `navigation to the DI office failed: ${String(err).split(access.token).join('<REDACTED>')}`,
      );
    }

    // (a) agents appear AUTOMATICALLY: no click, no console, no devtools.
    await expect(page.getByTestId('di-office-panel')).toBeVisible({ timeout: 30_000 });
    const liveMemberCount = [...fixture.members.values()].filter(
      (m) => m.classification === 'present',
    ).length;
    await expect
      .poll(
        async () =>
          (await seatedCharacters(page)).filter((c) => c.state === 'type' && !c.matrixEffect)
            .length,
        { timeout: 30_000, message: 'every live member seated and typing' },
      )
      .toBe(liveMemberCount);
    await labelFixture(page);
    await delay(500);
    await shot(page, '01-office-autoload-11-missions-FIXTURE.png');

    // FIRST CONNECTION, recorded order: the office state precedes everything, layoutLoaded included;
    // existingAgents precedes layoutLoaded (buffered) and is FLUSHED when the layout is ready — the
    // drawn set is then exactly the canonically present set.
    const firstOrder = (await page.evaluate(() =>
      ((window as unknown as HooksWindow).__pixelAgentsTestHooks?.messageLog ?? []).map(
        (m) => m.type,
      ),
    )) as string[];
    expect(firstOrder[0], 'first message on the first connection').toBe('diOfficeState');
    const existingAt = firstOrder.indexOf('existingAgents');
    expect(existingAt, 'existingAgents delivered').toBeGreaterThan(0);
    expect(existingAt, 'existingAgents before layoutLoaded').toBeLessThan(
      firstOrder.indexOf('layoutLoaded'),
    );
    const flushedCount = forwardedExistingAgents[0]?.length ?? 0;
    expect(
      flushedCount,
      'existingAgents carries agents for the layout-ready flush',
    ).toBeGreaterThan(0);
    const firstPresent = (await latestDiState(page)).missions.missions
      .flatMap((m) => m.members.members)
      .filter((x) => x.characterId !== null)
      .map((x) => x.characterId!)
      .sort((a, b) => a - b);
    expect((await seatedCharacters(page)).map((c) => c.id).sort((a, b) => a - b)).toEqual(
      firstPresent,
    );

    // (f) the office is still the office: the canvas yields the docked panel, so nothing overlaps.
    const panelBox = (await page.getByTestId('di-office-panel').boundingBox())!;
    const zoomBox = (await page.getByTitle('Zoom in (Ctrl+Scroll)').boundingBox())!;
    expect(zoomBox.x + zoomBox.width, 'zoom controls clear of the panel').toBeLessThan(panelBox.x);
    const canvasRight = await page.evaluate(() => {
      const c = Array.from(document.querySelectorAll('canvas')).sort(
        (a, b) => b.clientWidth * b.clientHeight - a.clientWidth * a.clientHeight,
      )[0];
      return c.getBoundingClientRect().right;
    });
    expect(canvasRight, 'office canvas ends where the panel begins').toBeLessThanOrEqual(
      panelBox.x + 1,
    );

    // FRAMING at 1440x900, measured on the RENDERED result: an INTEGER fit-zoom in DEVICE
    // pixels, and the world occupying the main area beside the readable panel.
    const frame2 = await framingOf(page);
    expect(frame2.dpr).toBe(2);
    expect(frame2.view.zoom, 'DPR 2: integer device-pixel fit').toBe(expectedFit(frame2));
    const world2 = {
      w: (frame2.view.cols * TILE_SIZE * frame2.view.zoom) / frame2.dpr,
      h: (frame2.view.rows * TILE_SIZE * frame2.view.zoom) / frame2.dpr,
    };
    console.log(
      `[framing DPR 2] main ${frame2.cssW}x${frame2.cssH} CSS, world ${frame2.view.cols}x${frame2.view.rows} tiles, zoom ${frame2.view.zoom}, world ${world2.w}x${world2.h} CSS (${Math.round((100 * world2.w) / frame2.cssW)}% x ${Math.round((100 * world2.h) / frame2.cssH)}%)`,
    );
    expect(world2.w).toBeLessThanOrEqual(frame2.cssW + 0.5);
    expect(world2.h).toBeLessThanOrEqual(frame2.cssH + 0.5);
    expect(
      Math.max(world2.w / frame2.cssW, world2.h / frame2.cssH),
      'fills one axis',
    ).toBeGreaterThan(0.85);
    expect(
      Math.min(world2.w / frame2.cssW, world2.h / frame2.cssH),
      'and most of the other',
    ).toBeGreaterThan(0.7);
    await labelFixture(page);
    await shot(page, '01b-framing-1440x900-dpr2-FIXTURE.png');

    // Concurrent Missions DISTINGUISHABLE: every character sits in its OWN Mission's labelled
    // zone (existing Areas + areaMappings), shown by default; different Missions, different zones.
    expect(
      await page.evaluate(() =>
        (window as unknown as HooksWindow).__pixelAgentsTestHooks!.getShowAreas!(),
      ),
    ).toBe(true);
    const zoneOf = await page.evaluate(() => {
      const hooks = (window as unknown as HooksWindow).__pixelAgentsTestHooks!;
      const seats = hooks.getSeats!();
      return Object.fromEntries(
        hooks.getCharacters!()
          .filter((c) => !c.isGreeter)
          .map((c) => {
            const col = Math.floor(c.x / 16);
            const row = Math.floor(c.y / 16);
            return [c.id, seats.find((x) => x.col === col && x.row === row)?.areaLabel ?? null];
          }),
      ) as Record<string, string | null>;
    });
    const zoneState = await latestDiState(page);
    const missionZones = new Map<string, string>();
    for (const mission of zoneState.missions.missions) {
      for (const member of mission.members.members) {
        if (member.characterId === null) continue;
        const zone = zoneOf[String(member.characterId)];
        expect(zone, `${member.memberRef} sits in its Mission's zone`).toBe(
          mission.nativeId.slice(0, 11),
        );
        missionZones.set(mission.missionRef, zone!);
      }
    }
    expect(missionZones.size, 'concurrent Missions with live members').toBeGreaterThan(1);
    expect(new Set(missionZones.values()).size, 'one distinct zone per Mission').toBe(
      missionZones.size,
    );
    // READABLE inspector: each Mission's card names the SAME office zone its characters sit in.
    for (const [missionRef, zone] of missionZones) {
      const card = page.locator(`[data-mission-ref="${missionRef}"]`);
      await expect(card.getByTestId('di-mission-zone')).toHaveText(`office zone ${zone}`);
    }

    // The DI office is identifiable in a browser tab: its own title and favicon.
    expect(await page.title()).toBe('Pixel Agents · DI office');
    expect(
      await page.evaluate(
        () => document.querySelector<HTMLLinkElement>("link[rel~='icon']")?.href ?? '',
      ),
    ).toMatch(/^data:image\/svg\+xml,/);

    // HONEST settings: the integrations DI drops are not offered; a read-only note says why.
    await page.getByTitle('Settings').click();
    await expect(page.getByTestId('di-settings-note')).toContainText('read-only view');
    await expect(page.getByText('Watch All Sessions', { exact: true })).toHaveCount(0);
    await expect(page.getByText('Instant Detection (Hooks)', { exact: true })).toHaveCount(0);
    await expect(page.getByPlaceholder('Absolute asset directory path')).toHaveCount(0);
    await expect(page.getByText('Add Asset Directory', { exact: true })).toHaveCount(0);
    await shot(page, '01c-settings-honest-di-FIXTURE.png');
    // Close with the MODAL'S OWN close control: its backdrop covers the toolbar button that
    // opened it (the run-2 timeout).
    // (Scoped to the modal header: other dismissible elements also carry an "x" button.)
    await page
      .locator('span', { hasText: /^Settings$/ })
      .locator('xpath=following-sibling::button[1]')
      .click();
    await expect(page.getByTestId('di-settings-note')).toHaveCount(0);

    // feature13 (section 0): the Missions LEAD. The first Mission's objective, observed status and team
    // are readily visible without scrolling, with the coverage caveat and the Selected dock inside the
    // viewport. (The envelope/runtime freshness rows moved out of the first screen by design: they are
    // asserted below where they now live, reachable with one scroll of the Missions region.)
    const firstCard = page.locator(`[data-mission-ref="${fixture.missionRefs[0]}"]`);
    await expectInViewport(page, firstCard.getByTestId('di-mission-objective'));
    await expectInViewport(page, firstCard.getByTestId('di-op-status'));
    await expectInViewport(page, firstCard.getByTestId('di-mission-team'));
    for (const id of ['di-view-limits', 'di-selected-agent']) await expectInViewport(page, id);
    // The partial view in plain words on the face (product experience); the exact coverage one step
    // away, under "About this list".
    await expect(page.getByTestId('di-view-limits')).toContainText(
      'team members working in linked child tasks',
    );
    await page.getByTestId('di-missions-about').locator('summary').click();
    await expect(page.getByTestId('di-coverage')).toContainText(
      'Linked child-task roles: NOT COVERED',
    );
    // Freshness is stated and reachable: in the Office data section, one scroll away, still in the
    // viewport once there; the face carries the office's LIVE state.
    await expect(page.getByTestId('di-office-panel')).toContainText('DI Mission office: LIVE');
    const freshness = page.getByTestId('di-freshness-summary');
    await freshness.scrollIntoViewIfNeeded();
    await expectInViewport(page, 'di-freshness-summary');
    await expect(freshness).toContainText('fresh');
    await expect(freshness).toContainText('served refreshed');
    const missionsList = page.getByTestId('di-missions');
    await expect(missionsList).toContainText('Missions: 11');
    // Every one of the eleven Missions is its OWN card, identified by its complete source-qualified
    // ref (exact, never a substring), and that ref is shown whole in its inspector.
    for (const ref of fixture.missionRefs) {
      await expect(page.locator(`[data-mission-ref="${ref}"]`)).toHaveCount(1);
      await missionInspector(page, ref, async (dialog) => {
        await dialog.getByTestId('di-inspector-provenance').locator('summary').click();
        await refShownWhole(dialog.getByTestId('di-mission-qualifier'), ref);
      });
    }
    // Each scroll region of the panel CONTAINS its positioned parts (the cards' screen-reader-only
    // zone names are position:absolute): none escapes to stretch the panel's own scroll area, so
    // the panel itself never scrolls past the dock into blank space.
    const containment = await page.getByTestId('di-office-panel').evaluate((panel) => {
      const regions = [...panel.querySelectorAll<HTMLElement>('*')].filter((el) =>
        ['auto', 'scroll'].includes(getComputedStyle(el).overflowY),
      );
      const positioned = regions.flatMap((region) =>
        [...region.querySelectorAll<HTMLElement>('*')]
          .filter((el) => ['absolute', 'fixed'].includes(getComputedStyle(el).position))
          .map((el) => ({ region, el })),
      );
      return {
        zoneNames: panel.querySelectorAll('[data-testid="di-mission-zone"]').length,
        escaped: positioned
          .filter(({ region, el }) => el.offsetParent === null || !region.contains(el.offsetParent))
          .map(({ el }) => `${el.dataset['testid'] ?? el.tagName} -> ${el.offsetParent?.tagName}`),
        panelOverflowPx: panel.scrollHeight - panel.clientHeight,
      };
    });
    expect(containment.zoneNames, 'the zone names are there to check').toBeGreaterThan(0);
    expect(containment.escaped, 'positioned parts escape their scroll region').toEqual([]);
    expect(containment.panelOverflowPx, 'the panel itself does not overflow').toBe(0);
    await page.getByTestId('di-missions').evaluate((el) => el.scrollIntoView({ block: 'start' }));
    // The Missions live in ONE bounded scroll region (the panel's main region), inside the viewport.
    const region = page.getByTestId('di-op-main');
    await expectInViewport(page, 'di-op-main');
    const bounded = await region.evaluate((el) => ({
      scrolls: el.scrollHeight > el.clientHeight,
      overflowY: getComputedStyle(el).overflowY,
    }));
    expect(bounded.overflowY).toBe('auto');
    expect(bounded.scrolls, 'eleven Missions live in their own bounded scroll region').toBe(true);

    // Every live character is reachable by the pointer (not under any overlay).
    const state = await latestDiState(page);
    const live = state.missions.missions.flatMap((m) =>
      m.members.members
        .filter((x) => x.characterId !== null)
        .map((x) => ({ ...x, missionRef: m.missionRef })),
    );
    expect(live).toHaveLength(liveMemberCount);
    for (const member of live) {
      const point = await characterPoint(page, member.characterId!);
      expect(
        point.onCanvas,
        `${member.memberRef} clickable (top element ${point.topElement})`,
      ).toBe(true);
    }

    // (b)(c) a REAL click on an actual agent reveals its canonical task-qualified identity.
    const target = live.find((m) => m.memberRef.endsWith('#executor1'))!;
    const point = await characterPoint(page, target.characterId!);
    await page.mouse.click(point.x, point.y);
    const selected = page.getByTestId('di-selected-agent');
    // The dock is concise: it names the member (logical and role) and its Mission by the COMPLETE
    // source-qualified ref (shown whole, never clipped), with identity and state in words.
    const logical = target.memberRef.split('#')[1];
    await expect(selected.getByTestId('di-selected-member')).toContainText(logical, {
      timeout: 5_000,
    });
    // Its Mission by title and short ref on the face, bound to the COMPLETE ref exactly (its title);
    // its state as one plain phrase. The complete ref is shown whole in the inspector (below).
    await expectDockNamesMission(page, target.missionRef, fixture.missionRefs);
    await expect(selected.getByTestId('di-agent-phrase')).toHaveText(/Terminal busy/);
    await expectInViewport(page, 'di-selected-agent');
    // The canvas label agrees with the office state (never a tool-derived "Idle"), and the
    // inert "Close agent" control is not offered in DI mode.
    const overlay = page.locator(
      `[data-testid="agent-overlay"][data-agent-id="${target.characterId}"]`,
    );
    await expect(overlay).toContainText('busy');
    await expect(overlay).not.toContainText('Idle');
    await expect(page.getByTitle('Close agent')).toHaveCount(0);
    await delay(400);
    await shot(page, '02-agent-selected-by-click-FIXTURE.png');
    // The raw identity fields live in the inspector (one "Show details" away): asserted there,
    // exactly. Provider comes from the PROJECTION; the model is "unknown", rendered as such.
    await selectionInspector(page, async (dialog) => {
      const member = dialog.getByTestId('di-inspector-member');
      await expect(member).toContainText(target.memberRef);
      await expect(member).toContainText(target.missionRef);
      await expect(member).toContainText(target.taskId.value!);
      await expect(member).toContainText('present (session_confirmed)');
      await expect(member).toContainText('pod-executor1 / claude');
      await expect(modelConfirmed(member)).toHaveText('unknown (no supported source reports it)');
    });

    // FRAMING at DPR 1 (same 1440x900, its own context): the fit picks HALF the DPR-2 zoom's
    // device pixels for the same world, and characters stay clickable.
    {
      const ctx1 = await page
        .context()
        .browser()!
        .newContext({
          viewport: { width: 1440, height: 900 },
          deviceScaleFactor: 1,
        });
      const p1 = await ctx1.newPage();
      await p1.addInitScript(() => {
        (window as unknown as HooksWindow).__PIXEL_AGENTS_E2E = true;
      });
      try {
        await p1.goto(access.url);
      } catch (err) {
        throw new Error(
          `DPR 1 navigation failed: ${String(err).split(access.token).join('<REDACTED>')}`,
        );
      }
      await expect(p1.getByTestId('di-office-panel')).toBeVisible({ timeout: 30_000 });
      await expect
        .poll(
          async () =>
            (await seatedCharacters(p1)).filter((c) => c.state === 'type' && !c.matrixEffect)
              .length,
          { timeout: 30_000 },
        )
        .toBe(liveMemberCount);
      const frame1 = await framingOf(p1);
      expect(frame1.dpr).toBe(1);
      expect(frame1.view.zoom, 'DPR 1: integer device-pixel fit').toBe(expectedFit(frame1));
      const world1 = {
        w: frame1.view.cols * TILE_SIZE * frame1.view.zoom,
        h: frame1.view.rows * TILE_SIZE * frame1.view.zoom,
      };
      console.log(
        `[framing DPR 1] main ${frame1.cssW}x${frame1.cssH} CSS, world ${frame1.view.cols}x${frame1.view.rows} tiles, zoom ${frame1.view.zoom}, world ${world1.w}x${world1.h} CSS (${Math.round((100 * world1.w) / frame1.cssW)}% x ${Math.round((100 * world1.h) / frame1.cssH)}%)`,
      );
      expect(world1.w).toBeLessThanOrEqual(frame1.cssW + 0.5);
      expect(world1.h).toBeLessThanOrEqual(frame1.cssH + 0.5);
      expect(Math.max(world1.w / frame1.cssW, world1.h / frame1.cssH)).toBeGreaterThan(0.75);
      const p1Target = live.find((m) => m.memberRef.endsWith('#executor1'))!;
      const p1Point = await characterPoint(p1, p1Target.characterId!);
      expect(p1Point.onCanvas, `DPR 1: ${p1Target.memberRef} clickable`).toBe(true);
      await shot(p1, '02b0-dpr1-before-click-FIXTURE.png');
      await p1.mouse.click(p1Point.x, p1Point.y);
      // OBSERVATION on a miss: the point, the target as drawn, and the view at click time.
      const p1Observed = JSON.stringify({
        point: p1Point,
        target: (await seatedCharacters(p1)).find((c) => c.id === p1Target.characterId),
        view: await p1.evaluate(() =>
          (window as unknown as HooksWindow).__pixelAgentsTestHooks!.getView!(),
        ),
      });
      const p1Selected = p1.getByTestId('di-selected-agent');
      await expect(
        p1Selected.getByTestId('di-selected-member'),
        `DPR 1 click selects ${p1Target.memberRef}; observed ${p1Observed}`,
      ).toContainText(p1Target.memberRef.split('#')[1], { timeout: 5_000 });
      await expectDockNamesMission(p1, p1Target.missionRef, fixture.missionRefs);
      // ...and the canvas selected THAT character: its label appears on it.
      await expect(
        p1.locator(`[data-testid="agent-overlay"][data-agent-id="${p1Target.characterId}"]`),
      ).toContainText('busy');
      await labelFixture(p1);
      await shot(p1, '02c-framing-1440x900-dpr1-FIXTURE.png');
      await ctx1.close();
    }

    // A SECOND real click, on the other provider (the FIXTURE mirrors the real direct four:
    // lead1/executor1 claude, reviewer1/supervisor codex). Reload first: the first click's
    // camera-follow panned the view, and the click math assumes the unpanned camera.
    await page.reload();
    await expect(page.getByTestId('di-office-panel')).toBeVisible({ timeout: 30_000 });
    await expect.poll(typingCount, { timeout: 30_000 }).toBe(liveMemberCount);
    await labelFixture(page);
    const codexTarget = live.find((m) => m.memberRef.endsWith('#supervisor'))!;
    const codexPoint = await characterPoint(page, codexTarget.characterId!);
    expect(codexPoint.onCanvas, `supervisor clickable (top ${codexPoint.topElement})`).toBe(true);
    await page.mouse.click(codexPoint.x, codexPoint.y);
    await expect(selected.getByTestId('di-selected-member')).toContainText('supervisor', {
      timeout: 5_000,
    });
    await expectDockNamesMission(page, codexTarget.missionRef, fixture.missionRefs);
    await selectionInspector(page, async (dialog) => {
      const member = dialog.getByTestId('di-inspector-member');
      await expect(member).toContainText(codexTarget.memberRef);
      await expect(member).toContainText('pod-supervisor / codex');
      await expect(modelConfirmed(member)).toHaveText('unknown (no supported source reports it)');
    });
    await delay(400);
    await shot(page, '02b-codex-agent-selected-by-click-FIXTURE.png');

    // replaced/session_changed: VISIBLE, verbatim, both sessions — never drawn, never counted live.
    const replacedRef = [...fixture.members.entries()].find(
      ([, m]) => m.classification === 'replaced',
    )![0];
    const [replacedMissionRef, replacedLogical] = replacedRef.split('#');
    // The face (its card): the team line counts it NOT live, and its roster row says in words that
    // it is a replaced member and not live.
    await expect(
      page.locator(`[data-mission-ref="${replacedMissionRef}"]`).getByTestId('di-mission-team'),
    ).toContainText('1 of 2 members observed running');
    const replacedRow = await rosterRow(page, replacedMissionRef, replacedLogical);
    await replacedRow.scrollIntoViewIfNeeded();
    await expect(replacedRow).toContainText('replaced by another session');
    await expect(replacedRow).toContainText('Not observed running');
    expect((await seatedCharacters(page)).length).toBe(liveMemberCount);
    await shot(page, '03-replaced-visible-unconfirmed-FIXTURE.png');
    // The raw fields, verbatim, in the Mission's inspector (provenance): both sessions.
    await missionInspector(page, replacedMissionRef, async (dialog) => {
      await dialog.getByTestId('di-inspector-provenance').locator('summary').click();
      const row = provenanceRow(dialog, replacedRef);
      await expect(row).toContainText('replaced (session_changed)');
      await expect(row).toContainText('NOT LIVE: member_replaced:session_changed');
      await expect(row).toContainText(`sess-${replacedLogical}-1`);
      await expect(row).toContainText(`sess-${replacedLogical}-2`);
      await expect(dialog.locator(`[data-member-ref="${replacedRef}"]`)).toContainText(
        'replaced by another session',
      );
    });

    // (e) freshness detail: the distinct times, one interaction away.
    await page.getByText('Freshness detail and times').click();
    for (const label of [
      'requested_at',
      'generated_at',
      'valid_until',
      'snapshot observed_at',
      'evaluated (office clock)',
    ])
      await expect(page.getByTestId('di-freshness-detail')).toContainText(label);
    await shot(page, '04-freshness-detail-FIXTURE.png');
    await page.getByText('Freshness detail and times').click();

    // (f) the layout editor is intact.
    await page.getByTitle('Edit office layout').click();
    await expect(page.getByTitle('Paint floor tiles')).toBeVisible();
    await shot(page, '05-layout-editor-intact-FIXTURE.png');
    await page.getByTitle('Edit office layout').click();
    await expect(page.getByTitle('Paint floor tiles')).toBeHidden();

    // catalog_refresh_throttled: a NORMAL waiting state with its reason, never an error tone.
    mode = 'throttled';
    const header = page
      .getByTestId('di-office-panel')
      .getByText('waiting: catalog_refresh_throttled');
    await expect(header.first()).toBeVisible({ timeout: 20_000 });
    // The elements that EXPRESS the waiting state — the header and the Office data envelope row —
    // and the Missions state stay in the neutral tone: neither error (danger) nor caution (warning).
    const officePanel = page.getByTestId('di-office-panel');
    const tonesOf = (loc: Locator) => loc.evaluateAll((els) => els.map((el) => el.className));
    const waitingTones = await tonesOf(officePanel.getByText('waiting: catalog_refresh_throttled'));
    expect(waitingTones.length).toBeGreaterThanOrEqual(2);
    for (const tone of waitingTones) expect(tone).not.toMatch(/danger|warning/);
    const missionsStateTones = await tonesOf(
      page.getByTestId('di-missions').locator('.di-op-section-title > span'),
    );
    expect(missionsStateTones.length).toBeGreaterThan(0);
    for (const tone of missionsStateTones) expect(tone).not.toMatch(/danger|warning/);
    // EVERY element that carries the reason — the reception desk's separate staleness notice
    // included — never takes the ERROR tone. (That notice uses the product's CAUTION tone by design
    // since the desk was added (it is now the reception inbox, DiReceptionInbox.tsx; the earlier inline
    // DiReceptionDesk.tsx was removed), which postdates this spec's single revision; the
    // old blanket "no warning" assertion on all reason-bearing elements was therefore already
    // unsatisfiable at HEAD, independent of feature13.)
    const allTones = await tonesOf(
      officePanel.getByText(
        /waiting: catalog_refresh_throttled|no_response:catalog_refresh_throttled/,
      ),
    );
    expect(allTones.length).toBeGreaterThanOrEqual(waitingTones.length + missionsStateTones.length);
    for (const tone of allTones) expect(tone).not.toMatch(/danger/);
    await expect
      .poll(async () => (await seatedCharacters(page)).length, { timeout: 5_000 })
      .toBe(0);
    await delay(400);
    await shot(page, '06-throttled-normal-waiting-state-FIXTURE.png');

    // Back to fresh: the same characters return; then the panel collapses to one button.
    mode = 'fresh';
    await expect
      .poll(
        async () =>
          (await seatedCharacters(page)).filter((c) => c.state === 'type' && !c.matrixEffect)
            .length,
        { timeout: 40_000 },
      )
      .toBe(liveMemberCount);
    // ── HONEST UNKNOWN 1: server loss. Server freshness cannot reach a disconnected
    // browser, so the client must stop vouching on its own — and infer nothing terminal.
    const panel = page.getByTestId('di-office-panel');
    await expect(panel).toContainText('DI Mission office: LIVE');
    const idsBefore = (await seatedCharacters(page)).map((c) => c.id).sort((a, b) => a - b);
    refuseConnections = true;
    forwardToPage = false;
    for (const socket of sockets.splice(0)) {
      await socket.page.close({ code: 1001, reason: 'FIXTURE: server lost' });
      await socket.server.close();
    }
    await expect(panel).toContainText('DI Mission office: NOT LIVE: transport_reconnecting', {
      timeout: 5_000,
    });
    await expect.poll(typingCount, { timeout: 10_000, message: 'nothing keeps typing' }).toBe(0);
    expect((await seatedCharacters(page)).map((c) => c.id).sort((a, b) => a - b)).toEqual(
      idsBefore,
    ); // unknown, not gone: no character removed
    // The face (the card's roster, its disclosure opened as a person does): the row keeps its identity
    // in words and says NOT LIVE — never relabelled as gone.
    const execTarget = live.find((m) => m.memberRef.endsWith('#executor1'))!;
    const executorRow = await rosterRow(page, execTarget.missionRef, 'executor1');
    await expect(executorRow).toContainText('Not current');
    // The raw backend values stay VERBATIM — never relabelled, never disappeared/ended — for EVERY
    // previously live member, asserted in each Mission's inspector (provenance).
    const liveByMission = new Map<string, typeof live>();
    for (const member of live)
      liveByMission.set(member.missionRef, [
        ...(liveByMission.get(member.missionRef) ?? []),
        member,
      ]);
    for (const [missionRef, members] of liveByMission)
      await missionInspector(page, missionRef, async (dialog) => {
        await dialog.getByTestId('di-inspector-provenance').locator('summary').click();
        for (const member of members) {
          const row = provenanceRow(dialog, member.memberRef);
          await expect(row).toContainText('present (session_confirmed)');
          await expect(row).toContainText('NOT LIVE: transport_reconnecting');
        }
      });
    await expect(panel).toContainText('DI Mission office: NOT LIVE: transport_reconnecting');
    await shot(page, '07-server-lost-not-live-FIXTURE.png');

    // ── FINDING 2 setup: while the page hears nothing, the roster CHANGES on the backend —
    // a removal, an addition and a replacement. The server re-projects and broadcasts;
    // every agentCreated / agentClosed / diOfficeState is withheld from the page.
    const { removedRef, addedRef, replacedRef: rosterReplacedRef } = fixture.changedRoster;
    const idOf = (state: DiStateWire, ref: string): number | null =>
      state.missions.missions.flatMap((m) => m.members.members).find((x) => x.memberRef === ref)
        ?.characterId ?? null;
    const lastBeforeLoss = await latestDiState(page);
    const removedId = idOf(lastBeforeLoss, removedRef)!;
    const replacedId = idOf(lastBeforeLoss, rosterReplacedRef)!;
    expect(removedId).not.toBeNull();
    expect(replacedId).not.toBeNull();
    const servedBeforeChange = requestLog.length;
    roster = 'changed';
    await expect
      .poll(() => requestLog.length, {
        timeout: 15_000,
        message: 'backend served the changed roster',
      })
      .toBeGreaterThan(servedBeforeChange);
    await delay(500); // the server applies and broadcasts right after the response

    // ── HONEST UNKNOWN 2: a bare reconnect restores NOTHING. ──
    const messagesBeforeReconnect = await diMessageCount();
    refuseConnections = false; // the next retry reaches the server; its messages are withheld
    await expect(panel).toContainText(
      'DI Mission office: NOT LIVE: transport_reconnected_awaiting_office_state',
      { timeout: 20_000 },
    );
    await expect(page.getByText(/Reconnecting…|Disconnected/)).toBeHidden(); // transport connected
    expect(await typingCount()).toBe(0);
    await shot(page, '08-bare-reconnect-still-not-live-FIXTURE.png');
    forwardToPage = true; // live returns ONLY with a new office message
    await expect(panel).toContainText('DI Mission office: LIVE', { timeout: 15_000 });
    expect(await diMessageCount()).toBeGreaterThan(messagesBeforeReconnect);

    // FINDING 2: the reconnect RECONCILES membership on the canonical identity — the drawn
    // characters become exactly the new live set (the missed agentClosed/agentCreated are
    // made good from the office message itself).
    const changedState = await latestDiState(page);
    const liveIdsAfter = changedState.missions.missions
      .flatMap((m) => m.members.members)
      .filter((x) => x.characterId !== null)
      .map((x) => x.characterId!)
      .sort((a, b) => a - b);
    expect(liveIdsAfter).toHaveLength(liveMemberCount - 1); // -lead1 (replaced) -executor3 +reviewer3
    expect(idOf(changedState, removedRef)).toBeNull(); // gone from the roster entirely
    expect(idOf(changedState, rosterReplacedRef)).toBeNull(); // still listed, not live
    const addedId = idOf(changedState, addedRef)!;
    expect(addedId).not.toBeNull();
    await expect
      .poll(async () => (await seatedCharacters(page)).map((c) => c.id).sort((a, b) => a - b), {
        timeout: 15_000,
        message: 'drawn characters == the new live set',
      })
      .toEqual(liveIdsAfter);
    const drawnNow = (await seatedCharacters(page)).map((c) => c.id);
    expect(drawnNow).not.toContain(removedId); // removal reconciled
    expect(drawnNow).not.toContain(replacedId); // replaced: not drawn, not live
    expect(drawnNow).toContain(addedId); // addition reconciled
    // lead1 is REPLACED in the reconciled roster: visible, in words on its card and, verbatim with
    // both sessions, in its Mission's inspector.
    const leadMissionRef = rosterReplacedRef.split('#')[0];
    const leadRow = await rosterRow(page, leadMissionRef, 'lead1');
    await leadRow.scrollIntoViewIfNeeded();
    await expect(leadRow).toContainText('replaced by another session');
    await missionInspector(page, leadMissionRef, async (dialog) => {
      const row = provenanceRow(dialog, rosterReplacedRef);
      await expect(row).toContainText('replaced (session_changed)'); // visible, verbatim
      await expect(row).toContainText('sess-lead1-1');
      await expect(row).toContainText('sess-lead1-2');
    });
    await expect.poll(typingCount, { timeout: 30_000 }).toBe(liveMemberCount - 1);
    await shot(page, '08b-reconnect-reconciled-FIXTURE.png');

    // Back to the base roster (connected): lead1 returns under its SAME character number —
    // the server's identity numbers are stable and never reused for another identity.
    roster = 'base';
    await expect
      .poll(async () => (await seatedCharacters(page)).map((c) => c.id).includes(replacedId), {
        timeout: 20_000,
      })
      .toBe(true);
    await expect.poll(typingCount, { timeout: 30_000 }).toBe(liveMemberCount);
    expect((await seatedCharacters(page)).map((c) => c.id)).not.toContain(addedId);

    // ── HONEST UNKNOWN 3: connected but silent past valid_until is not a healthy office. ──
    mode = 'shortValidity';
    await expect
      .poll(
        async () => {
          const s = await latestDiState(page);
          return (s.times.validUntil ?? 0) - (s.times.generatedAt ?? 0);
        },
        { timeout: 15_000 },
      )
      .toBe(SHORT_VALIDITY_SECONDS);
    forwardToPage = false; // the socket stays open; the page simply hears nothing more
    await expect(panel).toContainText(
      'DI Mission office: NOT LIVE: client_expired:no_office_state_before_valid_until',
      { timeout: (SHORT_VALIDITY_SECONDS + 5) * 1000 },
    );
    await expect(page.getByText(/Reconnecting…|Disconnected/)).toBeHidden();
    await expect.poll(typingCount, { timeout: 10_000 }).toBe(0);
    expect((await seatedCharacters(page)).length).toBe(liveMemberCount);
    await shot(page, '09-connected-but-silent-expired-FIXTURE.png');
    mode = 'fresh';
    forwardToPage = true;
    await expect(panel).toContainText('DI Mission office: LIVE', { timeout: 15_000 });
    await expect.poll(typingCount, { timeout: 30_000 }).toBe(liveMemberCount);

    // ── ROUND-2 BLOCKER 1: PARTIAL DELIVERY. The connection is cut BETWEEN agentCreated(B) and
    // B's office message; B is then removed server-side while the page is disconnected.
    const createdCount = async (): Promise<number> => (await handled(page, 'agentCreated')).length;
    const createdBefore = await createdCount();
    // The members the roster change will CLOSE (lead1 replaced, executor3 left) and the bare
    // agentClosed count before the cut — the REMOVAL side of the same invariant.
    const beforeCut = await latestDiState(page);
    const idOfRef = (ref: string) =>
      beforeCut.missions.missions.flatMap((m) => m.members.members).find((x) => x.memberRef === ref)
        ?.characterId ?? null;
    const closingIds = [fixture.changedRoster.replacedRef, fixture.changedRoster.removedRef].map(
      (ref) => idOfRef(ref)!,
    );
    expect(closingIds.every((id) => id !== null)).toBe(true);
    const closedBefore = (await handled(page, 'agentClosed')).length;
    cutAfterType = 'agentCreated';
    roster = 'changed'; // the server creates reviewer3 (B): agentCreated(B) precedes its office message
    await expect
      .poll(() => cutAfterType, { timeout: 20_000, message: 'cut right after agentCreated(B)' })
      .toBeNull();
    // agentCreated(B) as the APPLICATION HANDLER received it.
    await expect.poll(createdCount, { timeout: 5_000 }).toBe(createdBefore + 1);
    const createdList = await handled(page, 'agentCreated');
    const bId = createdList[createdList.length - 1].id!;
    await expect(panel).toContainText('DI Mission office: NOT LIVE: transport_', {
      timeout: 5_000,
    });
    // SNAPSHOT-ONLY canvas: B was ANNOUNCED (agentCreated) but its office message was lost, so
    // B is NOT DRAWN. The inspector reports it, as exactly that. Already-confirmed members stay
    // drawn and NOT LIVE (silence is never absence). The members the server CLOSED before the
    // cut (lead1 replaced, executor3 left) are not retained.
    await expect(page.getByTestId('di-unconfirmed')).toContainText(`character #${bId}`, {
      timeout: 5_000,
    });
    expect((await seatedCharacters(page)).map((c) => c.id)).not.toContain(bId);
    await expect.poll(typingCount, { timeout: 10_000 }).toBe(0);
    // REMOVAL side: the two bare agentClosed (their trailing office state lost with the cut)
    // reached the handler, yet assert NO absence: both still-confirmed members stay drawn. They
    // leave on the next canonical state that omits them (below).
    expect((await handled(page, 'agentClosed')).length).toBeGreaterThanOrEqual(closedBefore + 2);
    const drawnInCut = (await seatedCharacters(page)).map((c) => c.id);
    for (const id of closingIds)
      expect(drawnInCut, `still-confirmed ${id} stays drawn`).toContain(id);
    expect(drawnInCut.length, 'every confirmed member stays drawn').toBe(liveMemberCount);
    await expect(page.getByTitle('Close agent')).toHaveCount(0);
    await shot(page, '09b-partial-delivery-B-announced-not-drawn-FIXTURE.png');
    // While disconnected, the capture STARVES: the server clears every character (B included);
    // the page misses B's removal.
    const servedBeforeStarve = requestLog.length;
    capture = 'starved';
    await expect
      .poll(() => requestLog.length, { timeout: 15_000 })
      .toBeGreaterThan(servedBeforeStarve);
    await delay(500);
    // Reconnect: the FIRST message on the new connection is the office state (connect-first).
    refuseConnections = false;
    forwardToPage = true;
    await expect(panel).toContainText(/DI Mission office: NOT LIVE: runtime_/, { timeout: 20_000 });
    await expect
      .poll(async () => (await seatedCharacters(page)).length, {
        timeout: 15_000,
        message: 'B is NOT left drawn: every character reconciled away',
      })
      .toBe(0);
    await expect(page.getByTestId('di-missions')).toContainText('unavailable'); // never "none"
    await expect(page.getByTestId('di-unconfirmed')).toHaveCount(0); // the snapshot settled it
    await shot(page, '09c-reconnect-unavailable-capture-B-not-left-drawn-FIXTURE.png');
    // Then removal and replacement with full delivery: capture back on the CHANGED roster.
    capture = 'available';
    await expect.poll(typingCount, { timeout: 30_000 }).toBe(liveMemberCount - 1);
    const recovered = await latestDiState(page);
    const recoveredLive = recovered.missions.missions
      .flatMap((m) => m.members.members)
      .filter((x) => x.characterId !== null)
      .map((x) => x.characterId!)
      .sort((a, b) => a - b);
    await expect
      .poll(async () => (await seatedCharacters(page)).map((c) => c.id).sort((a, b) => a - b), {
        timeout: 10_000,
        message: 'drawn characters == the live set by identity (removal + replacement reconciled)',
      })
      .toEqual(recoveredLive);
    // B is drawn NOW — when, and only when, an office snapshot confirms it — and SELECTABLE.
    expect(recoveredLive).toContain(bId);
    const bPoint = await characterPoint(page, bId);
    expect(bPoint.onCanvas, `B clickable (top ${bPoint.topElement})`).toBe(true);
    await page.mouse.click(bPoint.x, bPoint.y);
    const [addedMissionRef, addedLogical] = fixture.changedRoster.addedRef.split('#');
    const bDock = page.getByTestId('di-selected-agent');
    await expect(bDock.getByTestId('di-selected-member')).toContainText(addedLogical, {
      timeout: 5_000,
    });
    await expectDockNamesMission(page, addedMissionRef, []);
    await shot(page, '09d-B-confirmed-drawn-selectable-FIXTURE.png');
    // The complete member ref (source-qualified) is in the inspector.
    await selectionInspector(page, async (dialog) => {
      await expect(dialog.getByTestId('di-inspector-member')).toContainText(
        fixture.changedRoster.addedRef,
      );
    });
    roster = 'base';
    await expect.poll(typingCount, { timeout: 30_000 }).toBe(liveMemberCount);

    await page.getByText('◂ hide').click();
    await expect(page.getByTestId('di-office-panel-collapsed')).toBeVisible();
    // The canvas resizes on its ResizeObserver, a frame or so after the container widens.
    await expect
      .poll(
        () =>
          page.evaluate(() =>
            Math.max(
              ...Array.from(document.querySelectorAll('canvas')).map(
                (c) => c.getBoundingClientRect().width,
              ),
            ),
          ),
        { timeout: 5_000, message: 'collapsed: the office takes the full width' },
      )
      .toBe(page.viewportSize()!.width);
    await delay(400);
    await shot(page, '10-panel-collapsed-full-office-FIXTURE.png');

    // Bounded actual-cadence observation (the built office against the stand-in, real time):
    // every request carries the pinned wait; consecutive arrivals are >= the pinned poll floor.
    for (const r of requestLog) expect(r.url).toBe(`/catalog?wait_seconds=${policyWait}`);
    const gaps = requestLog.slice(1).map((r, i) => r.atMs - requestLog[i].atMs);
    console.log(`[DI ux] backend request gaps (ms, n=${gaps.length}): ${gaps.join(', ')}`);
    expect(gaps.length).toBeGreaterThanOrEqual(2);
    for (const gap of gaps) expect(gap).toBeGreaterThanOrEqual(policyPollSeconds * 1000);
    expect(output).not.toContain(BACKEND_TOKEN);
    expect(output).not.toContain(access.token);
  } finally {
    child.kill('SIGTERM');
    await delay(300);
    await new Promise<void>((r) => backend.close(() => r()));
    fs.rmSync(sandbox, { recursive: true, force: true });
  }
});
