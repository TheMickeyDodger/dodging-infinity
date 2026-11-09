/**
 * B3, 1A: PRIMARY-ACTION HIERARCHY on the New Mission page (NOT a canonical test while it is in the evidence tree; PREPARED, NOT RUN). Records with the prefix B3HI.
 *
 * THE CLAIM, per state and per viewport, each in its own test, PROVEN BY COMPUTED STYLE and never by class name: in the ACTIVE AND EXPOSED view (a control that is not drawn, or whose
 * centre is covered, is not the state's primary) the CONTENT holds exactly ONE available primary, and it is the one the state's work needs next:
 *   blank           Send is held (nothing typed): NO available primary, and Send is not DRAWN as one;
 *   typed           Send;
 *   drafted, empty  "Open it in the request workspace to submit" (Send is held);
 *   drafted, typed  "Open it in the request workspace to submit", and Send is a SECONDARY that is still there, still labelled and still reached by Tab.
 * A primary is a button whose computed background IS the page's accent (read from the product through a probe, never typed), that is drawn, exposed by elementFromPoint at five points, and not held.
 * The RAIL's + New Orchestrator is RECORDED in every state (region 'rail') and NOT asserted: whether the rail counts against the content is decision D2 in SPEC.md, to be settled from these records.
 *
 * NEGATIVE CONTROL (on the CURRENT bytes, before the candidate), predicted from the source: `drafted-typed` COUNT fails AT its count claim (two available primaries: the open control and Send);
 * `drafted-typed` RANK fails AT its rank claim (Send keeps the accent); the `blank` and `drafted-empty` HELD tests fail AT their held-look claim (a held Send keeps the accent fill), each with its STATE
 * precondition lines passing first. The `blank`, `typed` and `drafted-empty` COUNT tests and the `drafted-typed` reachability line PASS on current bytes, as guards. Any other failure is a finding.
 * HONEST BOUNDS: Chrome, not Brave; synthetic conversation; the Reception cards and the Mission details' Diff action are NOT asserted here (SPEC D4, INVENTORY section 3).
 */
import assert from 'node:assert/strict';
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
import { availableCapacityOffice } from './diAvailableCapacity.ts';
import {
  relayReply,
  syntheticConversation,
  syntheticConversationSummary,
  syntheticDraftedTurn,
  syntheticOverview,
  syntheticTurn,
} from './diNewMissionSynthetic.ts';

const STEPS_MS = 120_000;
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const office = availableCapacityOffice(
  (JSON.parse(fs.readFileSync(path.join(root, 'test/fixtures/di-office-state-session-mismatch-v2.FIXTURE.json'), 'utf-8')) as { state: DiOfficeState }).state,
);
const REVIEW = JSON.parse(fs.readFileSync(path.join(root, 'test/fixtures/di-office-state-code-review-v2.FIXTURE.json'), 'utf-8')) as {
  states: Record<'live', DiOfficeState>;
  replies: { overview: DiCodeReview; files: Record<string, DiCodeReview> };
};
const VERSION = (JSON.parse(fs.readFileSync(path.join(root, '..', 'package.json'), 'utf-8')) as { version: string }).version;
const SHOTS = process.env.DI_B3HI_SHOTS ?? null;
/** The panel's own cap on first words (mission_panel/service.py:70). */
const FIRST_WORDS_CHARS = 120;
const LONG: Array<{ id: string; ask: string }> = [16, 28, 40, 52, 64, 76, 88, 96, 104, 112, 118, 120].map((n, i) => {
  const base = `SYNTHETIC ask ${String(i + 1).padStart(2, '0')}: make the readiness probe stable under load and report what changed in the subsystem`;
  const cut = base.padEnd(FIRST_WORDS_CHARS, ' and its tests').slice(0, n);
  return { id: `cv-${'0123456789ef'[i].repeat(32)}`, ask: cut.endsWith(' ') ? `${cut.slice(0, -1)}s` : cut };
});

/** Each conversation's turns as the synthetic panel holds them: a read returns them all, a turn sent joins them. */
let turnsOf: Record<string, Array<ReturnType<typeof syntheticTurn>>> = {};
/** Every request the page sent, in order (to show that a deselection reads nothing again and sends nothing). */
let sent: Array<{ op: string; conversationId: string | null }> = [];
/** B2e correction: the conversations whose reads the synthetic panel REFUSES (the states test's own refusal reply). */
let refuse = new Set<string>();
/** The conversation a fresh send (from the blank page) starts. */
const FRESH = `cv-${'d'.repeat(32)}`;
let server: ViteDevServer | null = null;
let wss: WebSocketServer | null = null;
let browser: Browser | null = null;
let page: Page | null = null;
let url = '';
let unavailable: string | null = null;
let socket: ServerSocket | null = null;
let resolveConnected: (ws: ServerSocket) => void = () => undefined;

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
          return;
        }
        if (msg.type === 'diCodeReviewRead') {
          // the fake office's reply, as diCodeReview.test.ts relays it: the overview for a read with no fileId, else the file's reply, echoing the requestId
          const stored = msg.fileId === undefined ? REVIEW.replies.overview : Object.values(REVIEW.replies.files).find((r) => r.fileId === msg.fileId);
          if (stored !== undefined) ws.send(JSON.stringify(typeof msg.requestId === 'string' ? { ...stored, requestId: msg.requestId } : stored));
          return;
        }
        if (msg.type !== 'diMissionRequest') return;
        sent.push({ op: String(msg.op), conversationId: msg.conversationId === undefined || msg.conversationId === null ? null : String(msg.conversationId) });
        const send = (m: Record<string, unknown>) => ws.send(JSON.stringify(m));
        if (msg.op === 'refresh') send(relayReply(msg.seq, 'refresh', syntheticOverview(LONG.map((c) => syntheticConversationSummary(c.id, c.ask)))));
        if (msg.op === 'conversation') {
          const id = String(msg.conversationId);
          if (refuse.has(id)) {
            send({ type: 'diMissionRequestReply', seq: msg.seq, op: 'conversation', state: 'refused', problem: 'mission_panel_conversation_unreadable', reason: 'SYNTHETIC: that conversation could not be read.', answer: null });
            return;
          }
          if (turnsOf[id] === undefined) turnsOf[id] = [syntheticDraftedTurn(LONG.find((c) => c.id === id)?.ask ?? 'SYNTHETIC')];
          send(relayReply(msg.seq, 'conversation', syntheticConversation(turnsOf[id], id)));
        }
        if (msg.op === 'turn') {
          const into = msg.conversationId === undefined || msg.conversationId === null ? FRESH : String(msg.conversationId);
          const turn = syntheticTurn(String(msg.text), { conversation_id: into, turn_id: `ct-${'7'.repeat(32)}`, sequence: (turnsOf[into]?.length ?? 0) + 1 });
          turnsOf[into] = [...(turnsOf[into] ?? []), turn];
          send(relayReply(msg.seq, 'turn', { ok: true, turn }));
        }
      }),
    );
  });
  const addr = server.httpServer?.address();
  url = `http://localhost:${typeof addr === 'object' && addr !== null ? addr.port : 0}/test/harness/di-app-scan.html`;
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
    console.warn(`SKIPPED (B3 hierarchy): ${unavailable}`);
    ctx.skip();
    return;
  }
  await page?.close();
  page = null;
  turnsOf = {};
  sent = [];
  refuse = new Set();
}, STEPS_MS);

const p = () => page!;
const sel = (id: string) => `[data-testid="${id}"]`;
const chipFor = (id: string) => p().locator(`[data-testid="di-new-recent-ask"][data-conversation="${id}"]`);
const record = (name: string, r: unknown) => console.log(`B3HI ${name}: ${JSON.stringify(r)}`);

async function shot(name: string): Promise<void> {
  if (SHOTS === null) return;
  const file = path.join(SHOTS, name);
  assert.ok(!fs.existsSync(file), `refusing to overwrite ${file}`);
  await p().screenshot({ path: file });
}

async function boot(viewport: { width: number; height: number }): Promise<void> {
  const connected = new Promise<ServerSocket>((resolve) => (resolveConnected = resolve));
  page = await browser!.newPage({ viewport });
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await page.goto(url, { waitUntil: 'domcontentloaded' });
  await connected;
  socket!.send(JSON.stringify({ type: 'providerCapabilities', readingTools: ['Read', 'Grep', 'Glob', 'WebFetch', 'WebSearch'], subagentToolNames: ['Task', 'Agent'] }));
  socket!.send(JSON.stringify({ type: 'settingsLoaded', soundEnabled: false, lastSeenVersion: VERSION, extensionVersion: VERSION }));
  socket!.send(JSON.stringify(office));
  await p().waitForSelector(sel('di-rail-new-orchestrator'));
  await p().click(sel('di-rail-new-orchestrator'));
  await p().waitForSelector(sel('di-new-mission'));
  for (const c of LONG) await chipFor(c.id).waitFor({ timeout: 20_000 });
}

// ── B3 1A: one primary per state ────────────────────────────────────────────────────────────────────────────────────────────────────────────────────
const ASK = 'SYNTHETIC an ask typed on the New Mission page';
const SIZES = [
  { width: 1280, height: 800, role: 'a robustness size' },
  { width: 1440, height: 900, role: 'a shipping size' },
  { width: 1600, height: 1000, role: 'a shipping size' },
] as const;
const STATES = ['blank', 'typed', 'drafted-empty', 'drafted-typed'] as const;
type State = (typeof STATES)[number];
/** The pinned identity of the ONE available content primary in each state (the design claim, decision D1). */
const PRIMARY_OF: Record<State, string[]> = { blank: [], typed: ['di-new-send'], 'drafted-empty': ['di-new-draft-open'], 'drafted-typed': ['di-new-draft-open'] };

interface Fill {
  id: string;
  region: 'rail' | 'content';
  held: boolean;
  hits: number;
  of: number;
  opacity: number;
  box: number[];
  /** The treatment as DRAWN (computed), so a card's action can be compared with the screen-primary reference without a class name. */
  treatment: { bg: string; height: number; fontSize: string; fontWeight: string };
}
/** Every drawn button whose computed background IS the page's accent, with where it is and whether it is exposed and held. */
async function census(): Promise<{ accent: string; fills: Fill[] }> {
  return p().evaluate(() => {
    const colourOf = (host: Element): string => {
      const probe = document.createElement('i');
      probe.style.cssText = 'position:fixed;left:-99px;top:0;width:1px;height:1px;background:var(--di-scan-accent)';
      host.append(probe);
      const c = getComputedStyle(probe).backgroundColor;
      probe.remove();
      return c;
    };
    const probeT = document.createElement('i');
    probeT.style.cssText = 'position:fixed;left:-99px;top:0;width:1px;height:1px;background:transparent';
    document.documentElement.append(probeT);
    const transparent = getComputedStyle(probeT).backgroundColor;
    probeT.remove();
    let accent = colourOf(document.documentElement);
    if (accent === transparent) accent = colourOf(document.querySelector('.di-scan') ?? document.body);
    const fills: Fill[] = [];
    for (const el of Array.from(document.querySelectorAll<HTMLElement>('button, a[href], [role="button"]'))) {
      const cs = getComputedStyle(el);
      if (cs.backgroundColor !== accent || el.getBoundingClientRect().width === 0 || el.getBoundingClientRect().height === 0) continue;
      // INSTRUMENT (compose-3: drafted-empty read hits 0 of 5 for an available Open control drawn BELOW the fold at 1280x800 and 1440x900, because a pixel that is not in the window hits nothing): bring the control into view before sampling, and sample the middle and the middles of the edges (the corners are rounded)
      el.scrollIntoView({ block: 'nearest', inline: 'nearest' });
      const r = el.getBoundingClientRect();
      const pts = [
        [0.5, 0.5],
        [0.25, 0.5],
        [0.75, 0.5],
        [0.5, 0.25],
        [0.5, 0.75],
      ];
      let hits = 0;
      for (const [fx, fy] of pts) {
        const hit = document.elementFromPoint(r.left + r.width * fx, r.top + r.height * fy);
        if (hit !== null && (hit === el || el.contains(hit))) hits += 1;
      }
      fills.push({
        id: el.getAttribute('data-testid') ?? `(no testid) ${(el.textContent ?? '').trim().slice(0, 40)}`,
        region: el.closest('[data-testid="di-rail"]') !== null ? 'rail' : 'content',
        held: el.matches(':disabled') || el.getAttribute('aria-disabled') === 'true',
        hits,
        of: pts.length,
        opacity: Number(cs.opacity),
        box: [r.left, r.top, r.right, r.bottom].map((n) => Math.round(n * 10) / 10),
        treatment: { bg: cs.backgroundColor, height: Math.round(r.height * 10) / 10, fontSize: cs.fontSize, fontWeight: cs.fontWeight },
      });
    }
    return { accent, fills };
  });
}
/** Send's own look, whatever its state: the claim about a HELD control is about how it is DRAWN. */
async function sendLook(): Promise<{ bg: string; border: string; borderStyle: string; held: string | null; label: string; reason: string | null; accent: string; transparent: string }> {
  return p().evaluate(() => {
    const el = document.querySelector<HTMLElement>('[data-testid="di-new-send"]')!;
    const cs = getComputedStyle(el);
    const probe = document.createElement('i');
    probe.style.cssText = 'position:fixed;left:-99px;top:0;width:1px;height:1px;background:var(--di-scan-accent)';
    document.documentElement.append(probe);
    let accent = getComputedStyle(probe).backgroundColor;
    probe.remove();
    const probeT = document.createElement('i');
    probeT.style.cssText = 'position:fixed;left:-99px;top:0;width:1px;height:1px;background:transparent';
    document.documentElement.append(probeT);
    const transparent = getComputedStyle(probeT).backgroundColor;
    probeT.remove();
    if (accent === transparent) accent = getComputedStyle(document.querySelector('.di-scan') ?? document.body).getPropertyValue('--di-scan-accent');
    return {
      bg: cs.backgroundColor,
      border: cs.borderTopColor,
      borderStyle: cs.borderTopStyle,
      held: el.getAttribute('aria-disabled'),
      label: (el.textContent ?? '').trim(),
      reason: document.querySelector('[data-testid="di-new-send-reason"]')?.textContent?.trim() ?? null,
      accent,
      transparent,
    };
  });
}
async function activeTestId(): Promise<string> {
  return p().evaluate(() => (document.activeElement as HTMLElement | null)?.getAttribute('data-testid') ?? document.activeElement?.tagName ?? 'none');
}

/** Reach a state and ASSERT it (loudly, before any claim). */
async function reach(vp: { width: number; height: number }, st: State): Promise<void> {
  await boot(vp);
  if (st === 'drafted-empty' || st === 'drafted-typed') {
    await chipFor(LONG[0].id).click();
    await p().waitForSelector(sel('di-new-draft-open'), { timeout: 20_000 });
  }
  if (st === 'typed' || st === 'drafted-typed') await p().locator(sel('di-new-ask')).fill(ASK);
  // STATE preconditions
  const draftOpen = await p().locator(sel('di-new-draft-open')).count();
  assert.equal(draftOpen, st === 'drafted-empty' || st === 'drafted-typed' ? 1 : 0, `STATE ${st}: the open control is ${draftOpen === 1 ? 'drawn' : 'absent'}`);
  const look = await sendLook();
  const wantHeld = st === 'blank' || st === 'drafted-empty';
  assert.equal(look.held === 'true', wantHeld, `STATE ${st}: Send ${look.held === 'true' ? 'is held' : 'is available'} (${look.reason ?? 'no reason'})`);
  assert.ok(look.accent !== look.transparent && look.accent !== '', 'STATE: the accent was not resolved');
}

for (const vp of SIZES) {
  for (const st of STATES) {
    const where = `${vp.width}x${vp.height} (${vp.role})`;
    test(`B3 HIERARCHY, ${st}, at ${where}, COUNT: the content holds exactly its one available primary`, async () => {
      await reach(vp, st);
      const c = await census();
      const available = c.fills.filter((f) => !f.held && f.hits >= 3);
      record(`COUNT ${st} ${vp.width}x${vp.height}`, { accent: c.accent, fills: c.fills });
      await shot(`b3hi-${st}-${vp.width}x${vp.height}.png`);
      const content = available.filter((f) => f.region === 'content').map((f) => f.id);
      assert.deepEqual(content, PRIMARY_OF[st], `CONTRACT (COUNT): ${st} at ${where}: the available content primaries are ${JSON.stringify(content)}, the state's one is ${JSON.stringify(PRIMARY_OF[st])}`);
    });
    if (st === 'blank' || st === 'drafted-empty') {
      test(`B3 HIERARCHY, ${st}, at ${where}, HELD: a held Send is not drawn as an available primary`, async () => {
        await reach(vp, st);
        const look = await sendLook();
        record(`HELD ${st} ${vp.width}x${vp.height}`, look);
        assert.notEqual(look.reason, null, `STATE ${st}: a held Send says why`);
        assert.notEqual(look.bg, look.accent, `CONTRACT (HELD): ${st} at ${where}: a held Send is drawn with the accent fill (${look.bg})`);
        assert.equal(look.borderStyle, 'dashed', `CONTRACT (HELD): ${st} at ${where}: a held Send keeps a solid border (${look.borderStyle})`);
        await p().locator(sel('di-new-send')).focus();
        assert.equal(await activeTestId(), 'di-new-send', 'a held Send keeps its focus (aria-disabled, never disabled)');
      });
    }
    if (st === 'drafted-typed') {
      test(`B3 HIERARCHY, ${st}, at ${where}, RANK: the demoted Send is still there, labelled and reached by Tab`, async () => {
        await reach(vp, st);
        const look = await sendLook();
        record(`RANK ${st} ${vp.width}x${vp.height}`, look);
        assert.equal(look.label, 'Send', 'the demoted control keeps its label');
        assert.notEqual(look.bg, look.accent, `CONTRACT (RANK): ${st} at ${where}: Send is drawn as a primary beside the open control`);
        await p().locator(sel('di-new-ask')).focus();
        let presses = 0;
        while ((await activeTestId()) !== 'di-new-send' && presses < 80) {   // EXPECTATION (compose-3: the cap of 12 stopped on the 12th Recent-ask chip in between; the claim is that Send is reached, and the press count is RECORDED)
          await p().keyboard.press('Tab');
          presses += 1;
        }
        record(`ROUTE ${st} ${vp.width}x${vp.height}`, { presses });
        assert.equal(await activeTestId(), 'di-new-send', `CONTRACT (REACH): Send is reached within 80 Tab presses of the ask box (${presses} presses; the Recent asks between them are part of the route)`);
      });
    }
  }
  test(`B3 HIERARCHY, reception page, at ${vp.width}x${vp.height} (${vp.role}), CENSUS: every accent-filled control, recorded against the rail's reference (D2, D4)`, async () => {
    await boot(vp);
    assert.equal(await p().locator(sel('di-open-reception')).isEnabled(), true, 'STATE: the rail offers Reception in this office');
    await p().click(sel('di-open-reception'));
    await p().waitForSelector(sel('di-reception-page'));
    const c = await census();
    const reference = c.fills.find((f) => f.id === 'di-rail-new-orchestrator') ?? null;
    record(`RECEPTION ${vp.width}x${vp.height}`, {
      accent: c.accent,
      cards: await p().locator(sel('di-reception-card')).count(),
      reference: reference?.treatment ?? null,
      fills: c.fills.map((f) => ({ ...f, sameTreatmentAsReference: reference !== null && f.treatment.bg === reference.treatment.bg && f.treatment.height === reference.treatment.height && f.treatment.fontSize === reference.treatment.fontSize })),
    });
    assert.ok(reference !== null, 'STATE: the rail entry is drawn with the accent, so there is a reference to compare with');
  });
}

/**
 * B3 HEADINGS (executor1, 2026-11-08). The proposal's heading takes its LEVEL from the caller and its LOOK from a class, so the level the page's outline needs changes nothing that is drawn.
 * MEASURED on the bytes before the repair: New Mission h2 "This conversation" -> h4 "The proposal, exactly as drafted" and the request workspace's Ask tab h2 "Codex Orchestrator" -> h4 (h3 missing in both); the Requests tab
 * (h3 request title -> h4 -> h5 rules) and the offer (h4 -> h5) were right and must stay so. The expected numbers below are the BEFORE table's, not chosen: 13px/600 for the proposal heading, 12px/600 with an 8px/2px margin for the
 * rules' sub-headings, and the text colour (--di-text, read from the product through a probe and never typed). Problems are COLLECTED across every check and asserted once, so a red names every defect and not the first.
 */
type HeadingRow = {
  level: number;
  tag: string;
  text: string;
  isProposalHeading: boolean;
  isRuleHeading: boolean;
  inOffer: boolean;
  fontSize: string;
  fontWeight: string;
  color: string;
  marginTop: string;
  marginBottom: string;
  lineHeight: string;
  textColor: string;
};
async function headingRows(page: Page, scope: string, proposalTestId: string): Promise<HeadingRow[]> {
  return page.evaluate(
    ({ sc, pid }) => {
      const root = document.querySelector<HTMLElement>(sc);
      if (root === null) return [];
      const probe = document.createElement('span');
      probe.style.color = 'var(--di-text)';
      root.appendChild(probe);
      const textColor = getComputedStyle(probe).color;
      probe.remove();
      return [...root.querySelectorAll<HTMLElement>('h1, h2, h3, h4, h5, h6, [role="heading"]')]
        .filter((e) => e.getClientRects().length > 0)
        .map((e) => {
          const cs = getComputedStyle(e);
          const proposal = e.closest(`[data-testid="${pid}"]`);
          return {
            level: Number(e.getAttribute('aria-level') ?? e.tagName.slice(1)),
            tag: e.tagName.toLowerCase(),
            text: (e.textContent ?? '').trim().slice(0, 40),
            isProposalHeading: proposal !== null && e.parentElement === proposal,
            isRuleHeading: proposal !== null && e.closest('.di-mr-rules') !== null,
            inOffer: e.closest('[data-testid="di-mr-offer"]') !== null && e.closest('.di-mr-rules') !== null,
            fontSize: cs.fontSize,
            fontWeight: cs.fontWeight,
            color: cs.color,
            marginTop: cs.marginTop,
            marginBottom: cs.marginBottom,
            lineHeight: cs.lineHeight,
            textColor,
          };
        });
    },
    { sc: scope, pid: proposalTestId },
  );
}
const colourWord = (r: HeadingRow) => (r.color === r.textColor ? '--di-text' : r.color);
function headingProblems(
  where: string,
  rows: HeadingRow[],
  want: { proposalLevel: number; rulesLevel: number | null; proposalLineHeight: string; rulesLineHeight: string },
): string[] {
  const problems: string[] = [];
  rows.forEach((r, i) => {
    if (i > 0 && r.level > rows[i - 1]!.level + 1)
      problems.push(`${where}: the outline skips a level: ${rows[i - 1]!.tag} "${rows[i - 1]!.text}" -> ${r.tag} "${r.text}"`);
  });
  const proposal = rows.filter((r) => r.isProposalHeading);
  if (proposal.length !== 1) problems.push(`${where}: expected ONE proposal heading, found ${proposal.length}`);
  for (const r of proposal) {
    if (r.level !== want.proposalLevel) problems.push(`${where}: the proposal heading is h${r.level}; the outline needs h${want.proposalLevel}`);
    const look = `${r.fontSize} ${r.fontWeight} ${colourWord(r)} m${r.marginTop}/${r.marginBottom} lh${r.lineHeight}`;
    const expected = `13px 600 --di-text m0px/0px lh${want.proposalLineHeight}`;
    if (look !== expected) problems.push(`${where}: the proposal heading is drawn "${look}"; it was drawn "${expected}"`);
  }
  const rules = rows.filter((r) => r.isRuleHeading);
  if (want.rulesLevel !== null) {
    if (rules.length === 0) problems.push(`${where}: no rule sub-heading is drawn, so their level and look are not checked`);
    for (const r of rules) {
      if (r.level !== want.rulesLevel) problems.push(`${where}: the rule sub-heading "${r.text}" is h${r.level}; the outline needs h${want.rulesLevel}`);
      const look = `${r.fontSize} ${r.fontWeight} ${colourWord(r)} m${r.marginTop}/${r.marginBottom} lh${r.lineHeight}`;
      const expected = `12px 600 --di-text m8px/2px lh${want.rulesLineHeight}`;
      if (look !== expected) problems.push(`${where}: the rule sub-heading "${r.text}" is drawn "${look}"; it was drawn "${expected}"`);
    }
  }
  return problems;
}

test('B3 headings: New Mission with a drafted proposal never skips a heading level: the proposal is one level under "This conversation", drawn as it was', async () => {
  await reach({ width: 1440, height: 900 }, 'drafted-empty');
  const rows = await headingRows(p(), sel('di-new-mission'), 'di-new-draft-proposal');
  const problems = headingProblems('New Mission, drafted', rows, { proposalLevel: 3, rulesLevel: null, proposalLineHeight: '18.2px', rulesLineHeight: '' });
  const conversation = rows.find((r) => r.text === 'This conversation');
  if (conversation === undefined) problems.push('New Mission, drafted: the "This conversation" heading is not drawn, so the proposal is not placed under it');
  else if (conversation.level !== 2) problems.push(`New Mission, drafted: "This conversation" is h${conversation.level}, expected h2`);
  assert.deepEqual(problems, [], problems.join('\n'));
});
