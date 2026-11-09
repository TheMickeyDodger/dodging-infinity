/**
 * B2d, MEASUREMENT ONLY (the Lead: "tell me what the populated Code Diff actually does at 1280, 1440 and 1672, with the tree populated, before
 * proposing any change"). Chrome, headless; not Brave evidence. Nothing here changes the product.
 *
 * PART A, the POPULATED Code Diff in the ACTUAL composition: the real App on the scan face (test/harness/di-app-scan.html), fed the REAL review answers
 * the server code produced over a fixture git workspace (test/fixtures/di-office-state-code-review-v2.FIXTURE.json; 23 files, long and nested paths).
 * Two files are selected and read: `src/syntax_sample.ts` (indented code, longest line 84 characters; the realistic case) and `web/long_line.js`
 * (one 1200-character line; the pathological case and the overflow instrument's positive control). Widths: 1280 (one pane), 1440 and 1672 (the Lead's),
 * plus 1480 and 1500, either side of the WINDOW-keyed `@media (min-width: 1500px)` Code Diff body rule (window-keyed inventory #4).
 *
 * PART B, inventory #3 (MEASURE FIRST, the Lead's ruling): Mission details' CONTENT under the WINDOW-keyed `@media (max-width: 1499px)`, docked at 1480 and
 * 1500, on the labelled POPULATED sample (test/harness/di-scan-lock.html?di-scan-case=populated): does either width produce a wrong result?
 *
 * Method rule 5: the CHANNEL (the code cell's content box), the CONTENT (each line's own width) and the LINES that do not fit are each MEASURED, never one
 * inferred from another; "characters that fit" is COMPUTED (channel / measured advance) and labelled so. Method rule 6: each instrument reads where the
 * thing is drawn (the code cell, the tree's name span, the stage's descendants) and is shown able to detect what it reports (positive controls).
 * ASSERTED: only that each named state was reached, and the positive controls. Everything else is RECORDED ("B2D ...") and compared by the reader.
 * PROBES (measurement-only, removed straight after reading, labelled PROBE in the record): the tree hidden, to read the channel a collapse WOULD give (no
 * collapse exists yet); the tree narrowed, the names' positive control; a wide element in the stage, the overflow positive control.
 */
import assert from 'node:assert/strict';
import crypto from 'node:crypto';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

import type { Browser, Page } from 'playwright-core';
import { chromium } from 'playwright-core';
import type { ViteDevServer } from 'vite';
import { createServer } from 'vite';
import { afterAll, beforeAll, beforeEach, test } from 'vitest';
import type { WebSocket as ServerSocket } from 'ws';
import { WebSocketServer } from 'ws';

import type { DiCodeReview, DiOfficeState } from '../../core/src/messages.ts';

const STEPS_MS = 180_000;
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const doc = JSON.parse(fs.readFileSync(path.join(root, 'test/fixtures/di-office-state-code-review-v2.FIXTURE.json'), 'utf-8')) as {
  states: Record<'live', DiOfficeState>;
  replies: { overview: DiCodeReview; files: Record<string, DiCodeReview> };
};
const VERSION = (JSON.parse(fs.readFileSync(path.join(root, '..', 'package.json'), 'utf-8')) as { version: string }).version;
const SHOTS = process.env.DI_B2D_SHOTS ?? null;
const VIEWPORTS = [
  { width: 1280, height: 800 },
  { width: 1440, height: 900 },
  { width: 1480, height: 900 },
  { width: 1500, height: 941 },
  { width: 1672, height: 941 },
];

let server: ViteDevServer | null = null;
let wss: WebSocketServer | null = null;
let browser: Browser | null = null;
let page: Page | null = null;
let origin = '';
let unavailable: string | null = null;
let socket: ServerSocket | null = null;
let resolveConnected: (ws: ServerSocket) => void = () => undefined;

function replyFor(request: Record<string, unknown>): DiCodeReview | null {
  const stored =
    request.fileId === undefined ? doc.replies.overview : Object.values(doc.replies.files).find((r) => r.fileId === request.fileId);
  return stored === undefined ? null : structuredClone(stored);
}

beforeAll(async () => {
  server = await createServer({ configFile: path.resolve(root, 'vite.config.ts'), server: { port: 0, strictPort: false, hmr: false, ws: false }, logLevel: 'silent' });
  await server.listen();
  wss = new WebSocketServer({ noServer: true });
  server.httpServer!.on('upgrade', (req, sock, head) => {
    if (!req.url?.startsWith('/ws')) {
      sock.destroy();
      return;
    }
    wss!.handleUpgrade(req, sock, head, (ws) =>
      ws.on('message', (data) => {
        const msg = JSON.parse(String(data)) as Record<string, unknown>;
        if (msg.type === 'webviewReady') {
          socket = ws;
          resolveConnected(ws);
        } else if (msg.type === 'diCodeReviewRead' || msg.type === 'diCodeReviewMissionRead') {
          const found = replyFor(msg);
          if (found === null) return;
          ws.send(JSON.stringify(typeof msg.requestId === 'string' ? { ...found, requestId: msg.requestId } : found));
        }
      }),
    );
  });
  const addr = server.httpServer?.address();
  origin = `http://localhost:${typeof addr === 'object' && addr !== null ? addr.port : 0}`;
  try {
    browser = await chromium.launch({ channel: 'chrome', headless: true });
  } catch {
    try {
      browser = await chromium.launch({ headless: true });
    } catch (e) {
      unavailable = `no Chrome or Playwright Chromium to drive: ${String(e).split('\n')[0]}`;
    }
  }
}, STEPS_MS);

afterAll(async () => {
  await page?.close();
  await browser?.close();
  wss?.close();
  await server?.close();
}, STEPS_MS);

beforeEach(async (ctx) => {
  if (unavailable) {
    console.warn(`SKIPPED (B2d measure): ${unavailable}`);
    ctx.skip();
    return;
  }
  await page?.close();
  page = null;
}, STEPS_MS);

const p = () => page!;
const record = (name: string, r: unknown) => console.log(`B2D ${name}: ${JSON.stringify(r)}`);

/** Method rule 4: never overwrite a measurement artifact. */
async function shot(name: string): Promise<void> {
  if (SHOTS === null) return;
  fs.mkdirSync(SHOTS, { recursive: true });
  const file = path.join(SHOTS, name);
  assert.equal(fs.existsSync(file), false, `refusing to overwrite ${file}`);
  await p().screenshot({ path: file });
  console.log(`B2D SHOT ${name} sha256 ${crypto.createHash('sha256').update(fs.readFileSync(file)).digest('hex')}`);
}

async function boot(viewport: { width: number; height: number }): Promise<void> {
  const connected = new Promise<ServerSocket>((resolve) => (resolveConnected = resolve));
  page = await browser!.newPage({ viewport });
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await page.goto(`${origin}/test/harness/di-app-scan.html`, { waitUntil: 'domcontentloaded' });
  await connected;
  socket!.send(JSON.stringify({ type: 'providerCapabilities', readingTools: ['Read', 'Grep', 'Glob', 'WebFetch', 'WebSearch'], subagentToolNames: ['Task', 'Agent'] }));
  socket!.send(JSON.stringify({ type: 'settingsLoaded', soundEnabled: false, lastSeenVersion: VERSION, extensionVersion: VERSION }));
  socket!.send(JSON.stringify(doc.states.live));
  await p().waitForSelector('[data-testid="di-rail-orchestrator"]');
}

async function openCodeDiff(): Promise<void> {
  await p().click('[data-testid="di-rail-orchestrator"] >> nth=0');
  await p().waitForSelector('[data-testid="di-scan-stage"][data-view="mission"]');
  await p().click('[data-testid="di-scan-code-diff"]');
  await p().waitForSelector('[data-testid="di-scan-stage"][data-view="diff"]');
  await p().waitForSelector('[data-testid="di-scan-diff"][data-source="read"]', { timeout: 15_000 });
  await p().waitForSelector('[data-testid="di-scan-tree-file"]');
}

async function selectFile(name: string): Promise<void> {
  await p().locator('[data-testid="di-scan-tree-file"]', { hasText: name }).first().click();
  await p().waitForFunction((n) => (document.querySelector('[data-testid="di-scan-editor-path"]')?.textContent ?? '').includes(n), name, { timeout: 15_000 });
  await p().waitForSelector('.di-scan-diff-line .di-scan-code', { timeout: 15_000 });
  await p().waitForTimeout(200);
}

/** The composition and the CODE CHANNEL, read where they are drawn. */
const composition = () =>
  p().evaluate(() => {
    const r = (sel: string) => {
      const el = document.querySelector<HTMLElement>(sel);
      if (el === null) return null;
      const b = el.getBoundingClientRect();
      return { left: Math.round(b.left), width: Math.round(b.width) };
    };
    // CORRECTED (measure 2): `.di-scan-home` is `display: contents` (no box of its own), so measure 1 read the LIST as width 0 and not visible while
    // the screenshot drew it beside the stage. The drawn list is `[data-testid="di-missions"]`; whether it is covered stays read on the home's attribute.
    const home = document.querySelector<HTMLElement>('[data-testid="di-scan-home"]');
    const listEl = document.querySelector<HTMLElement>('[data-testid="di-missions"]');
    const code = document.querySelector<HTMLElement>('.di-scan-diff-line .di-scan-code');
    const cs = code ? getComputedStyle(code) : null;
    const channel = code && cs ? code.clientWidth - parseFloat(cs.paddingLeft) - parseFloat(cs.paddingRight) : null;
    // the measured ADVANCE of one character in the code cell's own font (a measurement-only probe, removed after reading)
    let advance: number | null = null;
    if (code) {
      const probe = document.createElement('span');
      probe.style.cssText = 'position:absolute;visibility:hidden;white-space:pre';
      probe.textContent = 'M'.repeat(200);
      code.append(probe);
      advance = probe.getBoundingClientRect().width / 200;
      probe.remove();
    }
    const body = document.querySelector<HTMLElement>('[data-testid="di-scan-editor"]');
    // ADDED (measure 2): is the DECLARED first face actually drawn? `document.fonts.check` answers true for a face it does not know, so it cannot see
    // absence; the same string is measured in "JetBrains Mono", serif and in serif alone: equal widths mean the face is NOT present (measurement-only probe)
    const width = (family: string) => {
      const s = document.createElement('span');
      s.style.cssText = `position:absolute;visibility:hidden;white-space:pre;font-size:12px;font-family:${family}`;
      s.textContent = 'MMMMMMMMMMiiiiiiiiii0123456789';
      document.body.append(s);
      const w = s.getBoundingClientRect().width;
      s.remove();
      return w;
    };
    const jetbrainsDrawn = Math.abs(width('"JetBrains Mono", serif') - width('serif')) > 0.5;
    return {
      codeFaceJetBrainsMonoDrawn: jetbrainsDrawn,
      panes: document.querySelector('[data-testid="di-scan-panel"]')?.getAttribute('data-panes') ?? null,
      list: listEl ? { ...r('[data-testid="di-missions"]'), covered: home?.getAttribute('data-covered') === 'true', visible: listEl.checkVisibility({ checkVisibilityCSS: true }) } : null,
      stage: r('[data-testid="di-scan-stage"]'),
      tree: r('.di-scan-tree'),
      editor: r('.di-scan-editor'),
      codeFont: cs ? { family: cs.fontFamily.split(',')[0], size: cs.fontSize } : null,
      channelPx: channel === null ? null : Math.round(channel),
      advancePx: advance === null ? null : Number(advance.toFixed(3)),
      charsThatFitCOMPUTED: channel !== null && advance ? Math.floor(channel / advance) : null,
      editorScrolls: body ? { scrollWidth: body.scrollWidth, clientWidth: body.clientWidth, horizontal: body.scrollWidth > body.clientWidth + 1 } : null,
    };
  });

/** The selected file's lines: each line's own content width against the channel (measured per line, at the code cell). */
const lines = () =>
  p().evaluate(() => {
    const cells = [...document.querySelectorAll<HTMLElement>('.di-scan-diff-line .di-scan-code')];
    const over = cells.filter((c) => c.scrollWidth > c.clientWidth + 1);
    const widest = cells.reduce((m, c) => Math.max(m, (c.textContent ?? '').length), 0);
    return { lines: cells.length, overflowing: over.length, longestChars: widest, firstOverflowing: over.slice(0, 3).map((c) => (c.textContent ?? '').length) };
  });

/** The tree as drawn: rows, depth, and names that SPILL past the tree or are CUT (each read on the name span). */
const tree = () =>
  p().evaluate(() => {
    const t = document.querySelector<HTMLElement>('.di-scan-tree')!;
    const tb = t.getBoundingClientRect();
    const rows = [...t.querySelectorAll<HTMLElement>('[data-testid="di-scan-tree-file"], [data-testid="di-scan-tree-dir"]')];
    const facts = rows.map((row) => {
      const name = [...row.querySelectorAll<HTMLElement>(':scope > span')].find((s) => !s.className && (s.textContent ?? '').length > 0) ?? null;
      const nb = name?.getBoundingClientRect();
      return {
        name: name?.textContent ?? '',
        indentPx: parseFloat(getComputedStyle(row).paddingLeft),
        spills: nb !== undefined && nb.right > tb.right - 1,
        cut: name !== null && name.scrollWidth > name.clientWidth + 1,
        rowHeight: Math.round(row.getBoundingClientRect().height),
      };
    });
    const deepest = facts.reduce((m, f) => (f.indentPx > m.indentPx ? f : m), facts[0]);
    return {
      width: Math.round(tb.width),
      rows: facts.length,
      files: t.querySelectorAll('[data-testid="di-scan-tree-file"]').length,
      spilling: facts.filter((f) => f.spills).map((f) => f.name),
      cut: facts.filter((f) => f.cut).map((f) => f.name),
      tallRows: facts.filter((f) => f.rowHeight > 44).map((f) => `${f.name} (${f.rowHeight}px)`),
      deepest: deepest ? { name: deepest.name, indentPx: deepest.indentPx, spills: deepest.spills, cut: deepest.cut } : null,
    };
  });

for (const vp of VIEWPORTS)
  test(`PART A, the populated Code Diff at ${vp.width}x${vp.height}: composition, channel, lines that do not fit, the tree`, async () => {
    await boot(vp);
    await openCodeDiff();
    const panes = await p().getAttribute('[data-testid="di-scan-panel"]', 'data-panes');
    assert.equal(panes, vp.width >= 1440 ? 'two' : 'one', 'STATE: the panes the window names');
    // the realistic case
    await selectFile('syntax_sample.ts');
    const comp = await composition();
    const realistic = await lines();
    const treeOpen = await tree();
    await shot(`b2d-codediff-${vp.width}x${vp.height}-syntax_sample-tree-open.png`);
    // PROBE: the channel a collapse WOULD give (the tree hidden, measurement only, restored after reading)
    await p().evaluate(() => document.querySelector<HTMLElement>('.di-scan-tree')!.style.setProperty('display', 'none'));
    await p().waitForTimeout(100);
    const probeCollapsed = { comp: await composition(), lines: await lines() };
    await shot(`b2d-codediff-${vp.width}x${vp.height}-syntax_sample-PROBE-tree-hidden.png`);
    await p().evaluate(() => document.querySelector<HTMLElement>('.di-scan-tree')!.style.removeProperty('display'));
    await p().waitForTimeout(100);
    // POSITIVE CONTROL (names): the tree narrowed to 120 px must show a CUT or SPILLING name, or the name instrument is blind
    await p().evaluate(() => document.querySelector<HTMLElement>('.di-scan-tree')!.style.setProperty('width', '120px'));
    const narrowed = await tree();
    await p().evaluate(() => document.querySelector<HTMLElement>('.di-scan-tree')!.style.removeProperty('width'));
    // the pathological case, and the overflow instrument's POSITIVE CONTROL
    await selectFile('long_line.js');
    const pathological = await lines();
    const pathologicalScroll = (await composition()).editorScrolls;
    record(`${vp.width}x${vp.height}`, { composition: comp, syntax_sample: realistic, tree: treeOpen, PROBE_treeHidden: probeCollapsed, long_line: { ...pathological, editorScrolls: pathologicalScroll }, namesControl: { spilling: narrowed.spilling.length, cut: narrowed.cut.length } });
    assert.ok(comp.advancePx !== null && comp.advancePx > 0 && comp.channelPx !== null && comp.channelPx > 0, 'INSTRUMENT: the channel and the advance were read');
    assert.ok(pathological.overflowing >= 1, 'POSITIVE CONTROL: the 1200-character line reads as overflowing its channel');
    assert.ok(narrowed.spilling.length + narrowed.cut.length >= 1, 'POSITIVE CONTROL: a 120 px tree shows a name that spills or is cut');
  }, STEPS_MS);

/** Mission details' content under the window-keyed rule: each element's box and the properties the 1499 rule sets; and what leaves the stage. */
const missionContent = () =>
  p().evaluate(() => {
    const stage = document.querySelector<HTMLElement>('[data-testid="di-scan-stage"]')!;
    const sb = stage.getBoundingClientRect();
    const scs = getComputedStyle(stage);
    const right = sb.right - parseFloat(scs.paddingRight);
    const SEL = ['.di-scan-mission-title', '.di-scan-card', '.di-scan-swarm-heading', '.di-scan-cards', '.di-scan-card-member', '.di-scan-avatar-lg'];
    const props = (el: HTMLElement) => {
      const c = getComputedStyle(el);
      return { margin: c.margin, padding: c.padding, minHeight: c.minHeight, gap: c.gap, width: c.width, fontSize: c.fontSize };
    };
    const elements = Object.fromEntries(
      SEL.map((s) => {
        const all = [...stage.querySelectorAll<HTMLElement>(s)];
        const first = all[0];
        if (first === undefined) return [s, { count: 0 }];
        const b = first.getBoundingClientRect();
        return [s, { count: all.length, box: { width: Math.round(b.width), height: Math.round(b.height) }, ...props(first) }];
      }),
    );
    const title = stage.querySelector<HTMLElement>('.di-scan-mission-title');
    const lh = title ? parseFloat(getComputedStyle(title).lineHeight) : NaN;
    const leaving = [...stage.querySelectorAll<HTMLElement>('*')]
      .filter((el) => el.checkVisibility({ checkVisibilityCSS: true }) && el.getBoundingClientRect().width > 0 && el.getBoundingClientRect().right > right + 0.5)
      .slice(0, 8)
      .map((el) => `${el.tagName.toLowerCase()}${el.getAttribute('data-testid') ? `[${el.getAttribute('data-testid')}]` : ''}.${[...el.classList].slice(0, 2).join('.')} +${Math.round(el.getBoundingClientRect().right - right)}px`);
    const cut = [...stage.querySelectorAll<HTMLElement>('*')]
      .filter((el) => getComputedStyle(el).overflowX !== 'visible' && el.scrollWidth > el.clientWidth + 1 && el.checkVisibility({ checkVisibilityCSS: true }))
      .slice(0, 8)
      .map((el) => `${el.tagName.toLowerCase()}.${[...el.classList].slice(0, 2).join('.')}`);
    return {
      panes: document.querySelector('[data-testid="di-scan-panel"]')?.getAttribute('data-panes') ?? null,
      stage: { width: Math.round(sb.width), contentRight: Math.round(right) },
      titleRows: title && Number.isFinite(lh) && lh > 0 ? Math.round(title.getBoundingClientRect().height / lh) : null,
      elements,
      leavingTheStage: leaving,
      cutWithScroll: cut,
      stageScrollsSideways: stage.scrollWidth > stage.clientWidth + 1,
    };
  });

for (const vp of [
  { width: 1480, height: 900 },
  { width: 1500, height: 941 },
])
  test(`PART B, inventory #3: Mission details' content docked at ${vp.width}x${vp.height}, on the labelled populated sample`, async () => {
    page = await browser!.newPage({ viewport: vp });
    await page.emulateMedia({ reducedMotion: 'reduce' });
    await page.goto(`${origin}/test/harness/di-scan-lock.html?di-scan-case=populated&di-scan-view=mission`, { waitUntil: 'load' });
    await page.waitForSelector('[data-testid="di-scan-stage"][data-view="mission"] .di-scan-mission-title');
    const m = await missionContent();
    await shot(`b2d-mission-${vp.width}x${vp.height}-populated.png`);
    // ADDED (measure 2): the roster is COLLAPSED by default (data-expanded false), so measure 1's member cards and avatars were 0 x 0, NOT DRAWN; open it
    await p().click('[data-testid="di-scan-swarm-toggle"]');
    await p().waitForSelector('.di-scan-swarm-box[data-expanded="true"]');
    await p().waitForTimeout(200);
    const open = await missionContent();
    await shot(`b2d-mission-${vp.width}x${vp.height}-populated-roster-open.png`);
    // POSITIVE CONTROL (overflow): a measurement-only element 200 px wider than the stage must read as leaving it
    await p().evaluate(() => {
      const probe = document.createElement('div');
      probe.id = 'b2d-probe';
      probe.style.cssText = 'width:calc(100% + 200px);height:4px';
      document.querySelector('[data-testid="di-scan-stage"] .di-scan-mission-title')!.after(probe);
    });
    const control = (await missionContent()).leavingTheStage.length;
    await p().evaluate(() => document.getElementById('b2d-probe')?.remove());
    record(`mission ${vp.width}x${vp.height}`, { collapsed: m, rosterOpen: open, overflowControlSaw: control });
    assert.equal(m.panes, 'two', 'STATE: docked (two panes)');
    const member = open.elements['.di-scan-card-member'] as { box?: { width: number } } | undefined;
    assert.ok(member?.box !== undefined && member.box.width > 0, 'STATE: with the roster open, the member cards are DRAWN');
    assert.ok(control >= 1, 'POSITIVE CONTROL: an element wider than the stage reads as leaving it');
  }, STEPS_MS);

/**
 * PART C, measure 3 (the Lead's grant; NOT evidence for inventory #3, which stays LATENT: a defect present at both 1480 and 1500 is not caused by the rule
 * that differs between them). EVERY member avatar (rule 6c: the field is a list over ALL avatars, by index and name, never the first one read as the set):
 * its declared and drawn box, its aspect, its flex-shrink, and the text beside it. CAUSE PROBE (measurement only, removed after reading): `flex-shrink: 0`
 * on the avatars, to see whether the squeeze is flex shrink, and what holding the avatar's width would cost the text (does the card then overflow?).
 */
const everyAvatar = () =>
  p().evaluate(() =>
    [...document.querySelectorAll<HTMLElement>('[data-testid="di-scan-swarm"] .di-scan-card-member')].map((card, index) => {
      const avatar = card.querySelector<HTMLElement>('.di-scan-avatar-lg');
      const copy = card.querySelector<HTMLElement>('.di-scan-card-copy');
      const cb = card.getBoundingClientRect();
      const ccs = getComputedStyle(card);
      const contentWidth = cb.width - parseFloat(ccs.paddingLeft) - parseFloat(ccs.paddingRight) - parseFloat(ccs.borderLeftWidth) - parseFloat(ccs.borderRightWidth);
      const ab = avatar?.getBoundingClientRect();
      const acs = avatar ? getComputedStyle(avatar) : null;
      const copyBox = copy?.getBoundingClientRect();
      const name = card.querySelector('.di-scan-name')?.textContent ?? '';
      // the copy's MIN-CONTENT width (measurement-only probe, restored)
      let copyMinContent: number | null = null;
      if (copy) {
        const was = copy.style.cssText;
        copy.style.setProperty('width', 'min-content', 'important');
        copy.style.setProperty('flex', 'none', 'important');
        copyMinContent = Math.round(copy.getBoundingClientRect().width);
        copy.style.cssText = was;
      }
      return {
        index,
        name,
        cardContentWidth: Math.round(contentWidth),
        // RENAMED (rule 6c): getComputedStyle().width is the USED width on a laid-out element, not the declared value (measure 3 named it 'declared')
        avatarUsed: acs ? { width: acs.width, height: acs.height } : null,
        avatarDrawn: ab ? { width: Number(ab.width.toFixed(1)), height: Number(ab.height.toFixed(1)), aspect: Number((ab.width / ab.height).toFixed(3)) } : null,
        avatarFlexShrink: acs?.flexShrink ?? null,
        copyDrawnWidth: copyBox ? Math.round(copyBox.width) : null,
        copyMinContent,
        cardOverflows: card.scrollWidth > card.clientWidth + 1,
      };
    }),
  );

for (const vp of [
  { width: 1440, height: 900 },
  { width: 1480, height: 900 },
  { width: 1500, height: 941 },
  { width: 1672, height: 941 },
])
  test(`PART C, measure 3: EVERY member avatar at ${vp.width}x${vp.height}, roster open, with its cause`, async () => {
    page = await browser!.newPage({ viewport: vp });
    await page.emulateMedia({ reducedMotion: 'reduce' });
    await page.goto(`${origin}/test/harness/di-scan-lock.html?di-scan-case=populated&di-scan-view=mission`, { waitUntil: 'load' });
    await page.waitForSelector('[data-testid="di-scan-swarm-toggle"]');
    await p().click('[data-testid="di-scan-swarm-toggle"]');
    await p().waitForSelector('.di-scan-swarm-box[data-expanded="true"]');
    await p().waitForTimeout(200);
    const avatars = await everyAvatar();
    await shot(`b2d-m3-avatars-${vp.width}x${vp.height}.png`);
    // CAUSE PROBE: hold each avatar's width (flex-shrink: 0), read again, restore
    await p().evaluate(() => {
      const s = document.createElement('style');
      s.id = 'b2d-m3-probe';
      s.textContent = '[data-testid="di-scan-swarm"] .di-scan-avatar-lg { flex-shrink: 0 !important; }';
      document.head.append(s);
    });
    await p().waitForTimeout(100);
    const held = await everyAvatar();
    await shot(`b2d-m3-avatars-${vp.width}x${vp.height}-PROBE-flex-shrink-0.png`);
    await p().evaluate(() => document.getElementById('b2d-m3-probe')?.remove());
    record(`m3 avatars ${vp.width}x${vp.height}`, { everyAvatar: avatars, PROBE_flexShrink0: held });
    assert.equal(await p().getAttribute('[data-testid="di-scan-panel"]', 'data-panes'), 'two', 'STATE: docked (two panes)');
    assert.ok(avatars.length >= 2 && avatars.every((a) => a.avatarDrawn !== null && a.avatarDrawn.width > 0), 'STATE: every avatar is drawn (the roster open)');
  }, STEPS_MS);
