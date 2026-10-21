/**
 * FEATURE13 BEFORE/AFTER VISUAL EVIDENCE of the DI office panel — an evidence INSTRUMENT, not a
 * test of the panel's design. SYNTHETIC FIXTURE content (buildRealisticFixture(): eleven
 * realistic-length Missions), NOT live evidence and NOT live acceptance.
 *
 * Drives the PRODUCTION BUILD (dist/di-cli.js + dist/webview) against the labelled FIXTURE backend
 * stand-in, self-isolated exactly like officeUsability.spec.ts: mkdtemp sandbox, fake HOME,
 * ephemeral 127.0.0.1 backend (bearer + Host enforced), sandbox --state-dir. Nothing live is
 * touched. Every frame carries the on-frame FIXTURE label.
 *
 * The SAME file runs unchanged before and after the rewrite (DI_SHOT_SUFFIX=-BASELINE, then e.g.
 * -AFTER), so frames and numbers stay strictly comparable. It grows into the full matrix by adding
 * entries to VIEWPORTS and STATES.
 *
 * TWO NAMED MODES (DI_PANEL_MODE), never an implicit "element absent, so skip":
 *  - candidate (the DEFAULT): the feature is REQUIRED. A missing Selected dock, a missing "Open
 *    inspector" doorway or an inspector that does not open FAILS the run, and the layout invariants
 *    that decide feature13 are asserted unconditionally on every state at every viewport: the panel
 *    never scrolls itself (overflowPx 0), the Selected dock is inside the viewport, an open inspector
 *    fits it with Close reachable, no stray dialog, every Mission card carries its "Open inspector",
 *    and the first Mission's objective, status and team are in view (>= 3 cards at 1440x900).
 *  - baseline (DI_PANEL_MODE=baseline, set explicitly): the BEFORE build, which has no inspector and
 *    is expected to fail those invariants. The same checks are RECORDED in the measurements JSON
 *    (per-invariant pass/fail), the absent states are named "expected absent in baseline", and nothing
 *    is asserted. Forgetting the flag on the baseline build fails the run; it never goes quietly green.
 *
 * Seams it relies on: [data-testid="di-office-panel"] (the docked panel), and the e2e test hooks
 * (getCharacters, getView, getDiStored) for the real canvas click.
 *
 * Run from integrations/pixel-agents:
 *   DI_SHOT_DIR=<abs dir> DI_SHOT_SUFFIX=-BASELINE \
 *     npx playwright test --config e2e/di/playwright.di.config.ts e2e/di/panelViewports.spec.ts
 */
import { type ChildProcess, spawn } from 'node:child_process';
import crypto from 'node:crypto';
import fs from 'node:fs';
import http from 'node:http';
import type { AddressInfo } from 'node:net';
import os from 'node:os';
import path from 'node:path';

import { expect, type Page, test } from '@playwright/test';

import { layoutInvariants, type PanelMeasure } from './panelInvariants';
import { buildRealisticFixture } from './realisticFixture';

const REPO_ROOT = path.join(__dirname, '../..');
const DI_BUNDLE = path.join(REPO_ROOT, 'dist', 'di-cli.js');
const BACKEND_TOKEN = 'fixture-backend-token-e2e-0123456789';
// Mirrors of webview constants (TILE_SIZE, CHARACTER_SITTING_OFFSET_PX, CHARACTER_HIT_HEIGHT), as
// in officeUsability.spec.ts. Zoom, pan and grid size are READ from the renderer (getView).
const TILE_SIZE = 16;
const SITTING_OFFSET = 6;
const HIT_HEIGHT = 24;
/** Let the panel's selection poll and the camera settle before a frame is taken. */
const SETTLE_MS = 600;

/** Viewports captured: the desktop opening, a smaller desktop, and two constrained windows. */
const VIEWPORTS: ReadonlyArray<{ width: number; height: number }> = [
  { width: 1440, height: 900 },
  { width: 1280, height: 800 },
  { width: 1024, height: 768 },
  { width: 1100, height: 680 },
];

/** The selected team member: the first FIXTURE Mission's executor1 (as officeUsability.spec.ts). */
const MEMBER_LOGICAL = 'executor1';
/** 'candidate' (default) requires and asserts the feature; 'baseline' only records (see the header). */
const MODE: 'candidate' | 'baseline' =
  process.env['DI_PANEL_MODE'] === 'baseline' ? 'baseline' : 'candidate';

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
    getDiStored?: () => unknown;
  };
  __PIXEL_AGENTS_E2E?: boolean;
}
interface DiStateWire {
  missions: {
    missions: Array<{
      missionRef: string;
      members: { members: Array<{ memberRef: string; characterId: number | null }> };
    }>;
  };
}
interface Office {
  url: string;
  token: string;
  liveMemberCount: number;
  firstMissionRef: string;
}

function delay(ms: number): Promise<void> {
  return new Promise((r) => setTimeout(r, ms));
}

function shotPath(name: string): string {
  const dir = process.env['DI_SHOT_DIR'];
  if (!dir) throw new Error('DI_SHOT_DIR must be set');
  return path.join(dir, `${name}-FIXTURE${process.env['DI_SHOT_SUFFIX'] ?? ''}.png`);
}

/** Self-labels every frame as FIXTURE evidence (same test-only DOM as officeUsability.spec.ts). */
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
      // Above any dialog too: every frame carries the label, inspector open or not.
      zIndex: '2147483000',
      background: '#000',
      color: '#ff0',
      font: '11px monospace',
      padding: '1px 6px',
      pointerEvents: 'none',
    });
    document.body.appendChild(label);
  });
}

async function typingCount(page: Page): Promise<number> {
  return await page.evaluate(
    () =>
      ((window as unknown as HooksWindow).__pixelAgentsTestHooks?.getCharacters?.() ?? []).filter(
        (c) => !c.isGreeter && c.state === 'type' && !c.matrixEffect,
      ).length,
  );
}

/** Screen point inside a character's canvas hit box (officeUsability.spec.ts characterPoint). */
async function characterPoint(
  page: Page,
  id: number,
): Promise<{ x: number; y: number; onCanvas: boolean; topElement: string }> {
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

/** Occurrences of `text` in the page's DOM text, excluding the canvas agent overlays. */
async function occurrences(page: Page, text: string): Promise<number> {
  return await page.evaluate((t) => {
    const count = (s: string | null) => (s ?? '').split(t).length - 1;
    const overlays = Array.from(document.querySelectorAll('[data-testid="agent-overlay"]'));
    return (
      count(document.body.textContent) - overlays.reduce((n, o) => n + count(o.textContent), 0)
    );
  }, text);
}

/**
 * LOCAL MEASUREMENTS of the docked panel (this machine, not CI). Page-wide where a fact may move
 * out of the panel; null where an element is absent.
 *  - panel: its scrollHeight against its visible clientHeight;
 *  - firstObjective: the first visible text in the panel reading "FIXTURE objective <n>" (every
 *    fixture Mission objective starts so), its top edge in panel-content px, and the panel scroll
 *    needed before that edge enters the visible area.
 */
async function measurePanel(page: Page): Promise<unknown> {
  return await page.evaluate(() => {
    const panel = document.querySelector<HTMLElement>('[data-testid="di-office-panel"]');
    if (!panel) return { panel: null };
    const rect = panel.getBoundingClientRect();
    const contentTop = (el: Element) =>
      el.getBoundingClientRect().top - rect.top - panel.clientTop + panel.scrollTop;
    const walker = document.createTreeWalker(panel, NodeFilter.SHOW_TEXT);
    let first: { text: string; contentTop: number } | null = null;
    for (let node = walker.nextNode(); node && !first; node = walker.nextNode()) {
      const host = node.parentElement;
      if (!host || host.closest('select, option')) continue;
      const m = /FIXTURE objective \d+/.exec(node.textContent ?? '');
      if (!m || host.getClientRects().length === 0) continue;
      first = { text: m[0], contentTop: Math.round(contentTop(host)) };
    }
    // The region the Mission cards scroll in: the nearest scrolling ancestor of the list.
    const list = panel.querySelector('[data-testid="di-missions"]');
    let region: HTMLElement = panel;
    for (let el = list?.parentElement ?? null; el && el !== panel; el = el.parentElement) {
      const overflow = getComputedStyle(el).overflowY;
      if (overflow === 'auto' || overflow === 'scroll') {
        region = el;
        break;
      }
    }
    if (list instanceof HTMLElement) {
      const own = getComputedStyle(list).overflowY;
      if (own === 'auto' || own === 'scroll') region = list;
    }
    const box = region.getBoundingClientRect();
    const visibleTop = Math.max(box.top, 0);
    const visibleBottom = Math.min(box.bottom, window.innerHeight);
    // A card is READABLE when its top and its team line (or, where none, the card) are in view.
    const cards = Array.from(panel.querySelectorAll('[data-mission-ref]'));
    const readable = cards.filter((card) => {
      const top = card.getBoundingClientRect().top;
      const team = card.querySelector('[data-testid="di-mission-team"]') ?? card;
      return top >= visibleTop - 0.5 && team.getBoundingClientRect().bottom <= visibleBottom + 0.5;
    }).length;
    const dialog = document.querySelector<HTMLElement>('[role="dialog"][aria-modal="true"]');
    const inView = (el: Element) => {
      const r = el.getBoundingClientRect();
      return (
        r.top >= -0.5 &&
        r.left >= -0.5 &&
        r.bottom <= window.innerHeight + 0.5 &&
        r.right <= window.innerWidth + 0.5
      );
    };
    // Inside the region the cards scroll in AND the window (null: no such element in this build).
    const inRegion = (el: Element | null) => {
      if (!el) return null;
      const r = el.getBoundingClientRect();
      return r.top >= visibleTop - 0.5 && r.bottom <= visibleBottom + 0.5 && r.height > 0;
    };
    const firstCard = cards[0] ?? null;
    const selected = panel.querySelector('[data-testid="di-selected-agent"]');
    const sel = selected?.getBoundingClientRect() ?? null;
    // The Selected dock's own scroll area and its actions, read from the layout with NO scroll, click,
    // hover or focus first: a click auto-scrolls and a bounding box cannot see an element hidden inside
    // its parent's own scroll area, so each action is compared with the dock's VISIBLE client rect.
    const dockFacts = (() => {
      if (!(selected instanceof HTMLElement)) return null;
      const box = selected.getBoundingClientRect();
      const visible = {
        top: box.top + selected.clientTop,
        left: box.left + selected.clientLeft,
        bottom: box.top + selected.clientTop + selected.clientHeight,
        right: box.left + selected.clientLeft + selected.clientWidth,
      };
      const style = getComputedStyle(selected);
      const mask = style.maskImage || style.getPropertyValue('-webkit-mask-image');
      const fadePx = Number(/calc\(100% - ([\d.]+)px\)/.exec(mask)?.[1] ?? 0);
      const round = (n: number) => Math.round(n * 10) / 10;
      const action = (testId: string) => {
        const el = selected.querySelector<HTMLElement>(`[data-testid="${testId}"]`);
        if (!el) return null;
        const r = el.getBoundingClientRect();
        const hit = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2);
        return {
          rect: {
            top: round(r.top),
            left: round(r.left),
            bottom: round(r.bottom),
            right: round(r.right),
            width: round(r.width),
            height: round(r.height),
          },
          insideViewport:
            r.top >= 0 &&
            r.left >= 0 &&
            r.bottom <= window.innerHeight &&
            r.right <= window.innerWidth &&
            r.height > 0,
          insideDockVisible:
            r.top >= visible.top - 0.5 &&
            r.left >= visible.left - 0.5 &&
            r.bottom <= visible.bottom + 0.5 &&
            r.right <= visible.right + 0.5,
          clearOfFade: r.bottom <= visible.bottom - fadePx + 0.5,
          hitIsAction: hit !== null && (hit === el || el.contains(hit)),
          hit: hit
            ? `${hit.tagName.toLowerCase()}${hit.getAttribute('data-testid') ? `[${hit.getAttribute('data-testid')}]` : ''}`
            : null,
        };
      };
      return {
        scrollTop: selected.scrollTop,
        scrollHeight: selected.scrollHeight,
        clientHeight: selected.clientHeight,
        visible: {
          top: round(visible.top),
          left: round(visible.left),
          bottom: round(visible.bottom),
          right: round(visible.right),
        },
        fadePx,
        reviewState: selected.querySelector('[data-testid="di-open-code-review"]')
          ? ('offered' as const)
          : selected.querySelector('[data-testid="di-dock-code-review-unavailable"]')
            ? ('not-offered' as const)
            : ('absent' as const),
        actions: {
          'Open inspector': action('di-open-inspector-selection'),
          'Show in the office': action('di-show-in-office'),
          'Show this Mission in the list': action('di-show-mission'),
          'Open code review': action('di-open-code-review'),
        },
      };
    })();
    return {
      panel: {
        scrollTop: panel.scrollTop,
        scrollHeight: panel.scrollHeight,
        clientHeight: panel.clientHeight,
        overflowPx: panel.scrollHeight - panel.clientHeight,
        viewportHeight: window.innerHeight,
      },
      firstObjective: first && {
        ...first,
        scrollNeededToReachTop: Math.max(0, first.contentTop - panel.clientHeight),
        visibleWithoutScrolling: first.contentTop < panel.clientHeight,
      },
      missionCards: {
        total: cards.length,
        readableWithoutScrolling: readable,
        withOpenInspector: cards.filter(
          (c) => c.querySelector('[data-testid="di-open-inspector-mission"]') !== null,
        ).length,
      },
      selectedSection: sel && {
        top: Math.round(sel.top),
        bottom: Math.round(sel.bottom),
        withinViewport: sel.top >= 0 && sel.bottom <= window.innerHeight + 0.5,
      },
      dock: dockFacts,
      inspectorOpen: dialog !== null,
      inspector: dialog && {
        withinViewport: inView(dialog),
        closeVisible: (() => {
          const close = Array.from(dialog.querySelectorAll('button')).find(
            (b) => b.textContent?.trim() === 'Close',
          );
          return close !== undefined && inView(close);
        })(),
      },
      firstCard: firstCard && {
        objective: inRegion(firstCard.querySelector('[data-testid="di-mission-objective"]')),
        status: inRegion(firstCard.querySelector('[data-testid="di-op-status"]')),
        team: inRegion(firstCard.querySelector('[data-testid="di-mission-team"]')),
      },
    };
  });
}

/**
 * A REAL click on the first Mission's drawn executor1. The selection is held when the page's text
 * names that member or its Mission (complete refs) more often than before the click: true of any
 * view that shows what is selected, wherever it puts the identity.
 */
async function selectMemberOnCanvas(page: Page, office: Office): Promise<void> {
  const state = (await page.evaluate(
    () => (window as unknown as HooksWindow).__pixelAgentsTestHooks?.getDiStored?.() ?? null,
  )) as DiStateWire | null;
  const member = state?.missions.missions
    .find((m) => m.missionRef === office.firstMissionRef)
    ?.members.members.find((m) => m.memberRef.endsWith(`#${MEMBER_LOGICAL}`));
  if (!member || member.characterId === null)
    throw new Error(`precondition: ${MEMBER_LOGICAL} of the first Mission is not drawn`);
  const point = await characterPoint(page, member.characterId);
  if (!point.onCanvas)
    throw new Error(`precondition: ${member.memberRef} not clickable (top ${point.topElement})`);
  const named = async () =>
    (await occurrences(page, member.memberRef)) + (await occurrences(page, office.firstMissionRef));
  const before = await named();
  await page.mouse.click(point.x, point.y);
  await expect
    .poll(named, {
      timeout: 5_000,
      message: `the click selected ${member.memberRef}`,
    })
    .toBeGreaterThan(before);
}

/**
 * Opens the inspector through a control a person uses, when this build has one; returns false (and
 * captures nothing) where the build offers no such entry — the BEFORE build has no inspector.
 */
async function openInspectorVia(page: Page, testId: string): Promise<boolean> {
  const entry = page.getByTestId(testId).first();
  if ((await entry.count()) === 0) {
    // Named, not implicit: only the baseline build is EXPECTED to lack the doorway.
    if (MODE === 'baseline') return false;
    throw new Error(`candidate mode: the inspector doorway [data-testid="${testId}"] is missing`);
  }
  await entry.click();
  await expect(page.getByRole('dialog')).toBeVisible({ timeout: 5_000 });
  return true;
}

/**
 * States captured, each from a fresh load. `enter` returns false where the build has no route to
 * the state (recorded as "not present in this build"; no frame is taken). The full matrix adds
 * Find, a not-verified runtime record and the collapsed panel.
 */
const STATES: ReadonlyArray<{
  id: string;
  enter: (page: Page, office: Office) => Promise<boolean | void>;
}> = [
  { id: 'a-loaded-nothing-selected', enter: async () => {} },
  { id: 'b-team-member-selected', enter: selectMemberOnCanvas },
  {
    id: 'c-inspector-from-mission-card',
    enter: async (page) => openInspectorVia(page, 'di-open-inspector-mission'),
  },
  {
    id: 'd-inspector-from-selected-dock',
    enter: async (page, office) => {
      await selectMemberOnCanvas(page, office);
      return openInspectorVia(page, 'di-open-inspector-selection');
    },
  },
];

let office: Office | null = null;
let teardown: (() => Promise<void>) | null = null;

test.describe.configure({ mode: 'serial' });

/**
 * The BUILD these frames come from, bound to them: sha256 and size of dist/di-cli.js and of every
 * dist/webview top-level file and asset, and sha256 of the DI panel sources. A capture can never
 * silently show an older build than the sources it claims: if any built file is older than the
 * newest source file (webview-ui/src, server/src, core/src), the run fails before any frame.
 */
function buildIdentity(): Record<string, unknown> {
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
let build: Record<string, unknown> | null = null;

test.beforeAll(async () => {
  build = buildIdentity();
  const fixture = buildRealisticFixture();
  const sandbox = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), 'di-panel-')));
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
    res.end(JSON.stringify(fixture.envelopeAt(Math.floor(Date.now() / 1000))));
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
    [DI_BUNDLE, '--state-dir', path.join(sandbox, 'di-state'), '--backend-discovery', discovery],
    {
      env: { ...process.env, HOME: fakeHome, USERPROFILE: fakeHome, PIXEL_AGENTS_DEBUG_LOG: '' },
      stdio: ['ignore', 'pipe', 'pipe'],
    },
  );
  child.stdout?.on('data', (c: Buffer) => (output += c.toString()));
  child.stderr?.on('data', (c: Buffer) => (output += c.toString()));
  teardown = async () => {
    child.kill('SIGTERM');
    await delay(300);
    await new Promise<void>((r) => backend.close(() => r()));
    fs.rmSync(sandbox, { recursive: true, force: true });
  };

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
  const access = JSON.parse(fs.readFileSync(accessFile, 'utf-8')) as { url: string; token: string };
  office = {
    url: access.url,
    token: access.token,
    liveMemberCount: [...fixture.members.values()].filter((m) => m.classification === 'present')
      .length,
    firstMissionRef: fixture.missionRefs[0],
  };
});

test.afterAll(async () => {
  await teardown?.();
});

for (const viewport of VIEWPORTS) {
  const vp = `${viewport.width}x${viewport.height}`;
  test(`feature13 panel evidence at ${vp} (FIXTURE, not live acceptance)`, async ({ page }) => {
    const o = office!;
    await page.setViewportSize(viewport);
    await page.addInitScript(() => {
      (window as unknown as HooksWindow).__PIXEL_AGENTS_E2E = true;
    });
    const measurements: Record<string, unknown> = {};
    for (const state of STATES) {
      // Redact at capture time: a failed navigation's call log would echo the tokened URL.
      try {
        await page.goto(o.url);
      } catch (err) {
        throw new Error(`navigation failed: ${String(err).split(o.token).join('<REDACTED>')}`);
      }
      await expect(page.getByTestId('di-office-panel')).toBeVisible({ timeout: 30_000 });
      await expect
        .poll(() => typingCount(page), { timeout: 30_000, message: 'every live member seated' })
        .toBe(o.liveMemberCount);
      if ((await state.enter(page, o)) === false) {
        // Reachable ONLY in baseline mode (candidate mode throws where a doorway is missing).
        measurements[state.id] = { expectedAbsentInBaseline: true };
        continue;
      }
      await delay(SETTLE_MS);
      // Measured FIRST: before the caption is injected and before any screenshot, so nothing this driver
      // does can scroll, focus or hover the dock before its geometry is read.
      const m = (await measurePanel(page)) as PanelMeasure;
      await labelFixture(page);
      await page.screenshot({ path: shotPath(`${vp}-${state.id}`), fullPage: true });
      const invariants = layoutInvariants(m, vp, state.id);
      measurements[state.id] = { ...m, invariants };
      // Candidate: asserted (soft: every frame is still captured, then the run fails). Baseline:
      // recorded above, never asserted.
      if (MODE === 'candidate')
        for (const check of invariants) expect.soft(check.ok, check.name).toBe(true);
    }
    const record = {
      note: 'LOCAL MEASUREMENTS on this machine (not CI); synthetic FIXTURE content',
      mode: MODE,
      viewport,
      deviceScaleFactor: await page.evaluate(() => window.devicePixelRatio),
      build,
      measurements,
    };
    console.log(`[feature13 panel ${vp}] ${JSON.stringify(record)}`);
    fs.writeFileSync(
      shotPath(`${vp}-measurements`).replace(/\.png$/, '.json'),
      `${JSON.stringify(record, null, 2)}\n`,
    );
  });
}
