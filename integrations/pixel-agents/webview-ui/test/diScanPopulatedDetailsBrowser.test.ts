/**
 * A8: a POPULATED Mission's details lead short, and give back every word on demand, by keyboard (Free UI streamline, 2026-11-04, approved Mission mn-92b246d…, Lead1 addendum 4).
 *
 * The Mission is the labelled populated SAMPLE (test/diPopulatedSample.ts on the scan specimen, drawn with its own "Sample data (fixture)" label) in the scan lock harness
 * (test/harness/di-scan-lock.html?di-scan-case=populated): a long description with a line break, a multi-paragraph handoff, a Swarm with an UNRESOLVED identity, a model "not
 * recorded" and a "last seen, not live", and a runtime record not linked to a role. An empty swarm is not proof here; this one is full.
 *
 * Proved in a real browser, at 1440 x 900 and at the pixel-lock size 1672 x 941:
 *  - by default: the description is drawn as ONE line (its whole text in the document) in a card sized by what it shows (A9: no reserved empty space); the handoff, the
 *    roster and the unlinked record's list are folded; the Swarm says in ONE line how many roles are listed and how many identities are NOT resolved (A8.3, A9.2);
 *  - each fold is a control reached by Tab and operated by Enter, named, its state in aria-expanded (or a details element's `open`);
 *  - opened, the exact content comes back character for character: the description, each handoff paragraph, each member's model, provider, note and identity words, and the record.
 *
 * Captures: with DI_STREAMLINE_SHOTS=<absolute dir>, the views are written there with populated-manifest.json. Browser: the installed Chrome, else Playwright's Chromium; with
 * neither, every test is skipped with an explicit message.
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
import { afterAll, beforeAll, test } from 'vitest';

import { POPULATED_DESCRIPTION, POPULATED_HANDOFF, populatedRow, STRAY_AGENT, UNRESOLVED_IDENTITY, UNRESOLVED_ROLE } from './diPopulatedSample.ts';

const STEPS_MS = 120_000;
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const SHOTS = process.env['DI_STREAMLINE_SHOTS'] ?? null;
if (SHOTS !== null && !path.isAbsolute(SHOTS)) throw new Error('DI_STREAMLINE_SHOTS must be an absolute directory');
const ROW = populatedRow();

let server: ViteDevServer | null = null;
let browser: Browser | null = null;
let origin = '';
let unavailable: string | null = null;
const shots: Array<Record<string, unknown>> = [];

beforeAll(async () => {
  server = await createServer({ configFile: path.resolve(root, 'vite.config.ts'), server: { port: 0, strictPort: false, hmr: false }, logLevel: 'silent' });
  await server.listen();
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
  if (SHOTS !== null) fs.mkdirSync(SHOTS, { recursive: true });
}, STEPS_MS);

afterAll(async () => {
  await browser?.close();
  await server?.close();
  if (SHOTS !== null && shots.length > 0)
    fs.writeFileSync(
      path.join(SHOTS, 'populated-manifest.json'),
      `${JSON.stringify(
        {
          fixture: true,
          label:
            'LABELLED SAMPLE: the populated sample Mission (webview-ui/test/diPopulatedSample.ts) on the scan specimen, in the scan lock harness (webview-ui/test/harness/di-scan-lock.html?di-scan-case=populated), mounted from source by the Vite dev server. Not a real Mission, not a live backend.',
          shots,
        },
        null,
        2,
      )}\n`,
    );
}, STEPS_MS);

const sel = (id: string) => `[data-testid="${id}"]`;
const lines = (page: Page, selector: string) =>
  page.locator(selector).first().evaluate((el) => {
    const range = document.createRange();
    range.selectNodeContents(el);
    const tops = new Set<number>();
    // only the line boxes DRAWN inside the element's own box: a line a clamp hides is still laid out (it has a box) but is not drawn
    const box = el.getBoundingClientRect();
    for (const r of range.getClientRects())
      if (r.width > 0 && r.height > 0 && r.top >= box.top - 1 && r.bottom <= box.bottom + 1) tops.add(Math.round(r.top));
    return tops.size;
  });
async function tabTo(page: Page, testid: string, max = 120): Promise<void> {
  for (let i = 0; i < max; i++) {
    if ((await page.evaluate(() => document.activeElement?.getAttribute('data-testid'))) === testid) return;
    await page.keyboard.press('Tab');
  }
  throw new Error(`${testid} was not reached by Tab`);
}
async function snap(page: Page, name: string, note: string, into?: string): Promise<void> {
  if (SHOTS === null) return;
  if (into) await page.locator(into).first().scrollIntoViewIfNeeded();
  await page.waitForTimeout(150);
  const file = `${name}.png`;
  await page.screenshot({ path: path.join(SHOTS, file) });
  shots.push({ file, note, viewport: page.viewportSize(), sha256: crypto.createHash('sha256').update(fs.readFileSync(path.join(SHOTS, file))).digest('hex') });
}

for (const viewport of [
  { width: 1440, height: 900 },
  { width: 1672, height: 941 },
] as const)
  test(
    `A8 at ${viewport.width} x ${viewport.height}: a POPULATED Mission's details lead short; each fold is a keyboard control; every word comes back exactly`,
    async (ctx) => {
      if (unavailable) {
        console.warn(`SKIPPED (populated details): ${unavailable}`);
        ctx.skip();
        return;
      }
      const page = await browser!.newPage({ viewport });
      await page.emulateMedia({ reducedMotion: 'reduce' });
      await page.goto(`${origin}/test/harness/di-scan-lock.html?di-scan-case=populated&di-scan-view=mission`, { waitUntil: 'load' });
      await page.waitForSelector(sel('di-scan-details-text'));
      await page.evaluate(async () => {
        await document.fonts.ready;
      });
      assert.equal(await page.locator(sel('di-scan-mission-title')).innerText(), ROW.title, 'the sample is labelled as a sample in its own title');
      assert.match(ROW.title, /\(fixture\)/);

      // ── DEFAULT: short ────────────────────────────────────────────────────────────────────────────────────────────────────────────────────
      assert.equal(await page.locator(sel('di-scan-details-text')).textContent(), POPULATED_DESCRIPTION, 'the whole description is in the document');
      assert.equal(await lines(page, sel('di-scan-details-text')), 1, 'the description is drawn as ONE line');
      const handoff = page.locator(sel('di-scan-details-handoff'));
      assert.equal(await handoff.count(), POPULATED_HANDOFF.split('\n').length);
      for (let i = 0; i < (await handoff.count()); i++) assert.equal(await handoff.nth(i).isVisible(), false, `handoff paragraph ${i + 1} is folded`);
      assert.equal(await page.locator(sel('di-scan-details-toggle')).getAttribute('aria-expanded'), 'false');
      // A9: folded, the card is sized by what it shows: no reserved empty space between its toggle and its foot
      const gap = await page.evaluate(() => {
        const toggle = document.querySelector('[data-testid="di-scan-details-toggle"]')!.getBoundingClientRect();
        const foot = document.querySelector('.di-scan-card-foot')!.getBoundingClientRect();
        const card = document.querySelector('[data-testid="di-scan-details"]')!.getBoundingClientRect();
        return { between: foot.top - toggle.bottom, card: card.height };
      });
      assert.ok(gap.between >= 0 && gap.between <= 24, `no reserved space inside the folded card: ${JSON.stringify(gap)}`);
      // and nothing of it spills out: its foot is inside its box, and the Swarm starts below it
      const fit = await page.evaluate(() => {
        const card = document.querySelector('[data-testid="di-scan-details"]')!.getBoundingClientRect();
        const foot = document.querySelector('.di-scan-card-foot')!.getBoundingClientRect();
        const swarm = document.querySelector('.di-scan-swarm-heading')!.getBoundingClientRect();
        return { cardBottom: card.bottom, footBottom: foot.bottom, swarmTop: swarm.top };
      });
      assert.ok(fit.footBottom <= fit.cardBottom + 1, `the folded card holds its foot: ${JSON.stringify(fit)}`);
      assert.ok(fit.swarmTop >= fit.cardBottom, `the Swarm starts below the folded card: ${JSON.stringify(fit)}`);
      assert.ok(gap.card < 348, `the folded card is smaller than the full-text frame (348 px): ${JSON.stringify(gap)}`);
      // A9: the Swarm leads with ONE summary line that says how many identities are NOT resolved; the roster is folded
      const status = page.locator(sel('di-scan-swarm-status'));
      assert.equal((await status.innerText()).trim(), `${ROW.swarm.length} roles listed; 1 identity is not resolved.`);
      assert.equal(await lines(page, sel('di-scan-swarm-status')), 1, 'the Swarm: one line');
      const cards = page.locator(sel('di-scan-swarm-member'));
      assert.equal(await cards.count(), ROW.swarm.length, 'every member is in the document');
      for (let i = 0; i < ROW.swarm.length; i++) assert.equal(await cards.nth(i).isVisible(), false, `${ROW.swarm[i]!.role}: folded`);
      assert.equal(await page.locator(sel('di-scan-swarm-toggle')).getAttribute('aria-expanded'), 'false');
      // the record not linked to a role: one line, its list folded
      assert.equal(await lines(page, sel('di-scan-unresolved-status')), 1, 'the unlinked record: one line');
      assert.equal(await page.locator(sel('di-scan-unresolved-agent')).isVisible(), false, 'its list is folded');
      await snap(page, `populated-${viewport.width}-01-default`, 'A populated sample Mission, by default: the description on one line; the roster by role, name and identity mark; the details folded.');

      // ── ON DEMAND, BY KEYBOARD: exact ───────────────────────────────────────────────────────────────────────────────────────────────────
      await page.locator(sel('di-scan-back')).focus();
      await tabTo(page, 'di-scan-details-toggle');
      await page.keyboard.press('Enter');
      assert.equal(await page.locator(sel('di-scan-details-toggle')).getAttribute('aria-expanded'), 'true');
      assert.ok((await lines(page, sel('di-scan-details-text'))) > 1, 'the whole description is drawn');
      assert.equal(await page.locator(sel('di-scan-details-text')).innerText(), POPULATED_DESCRIPTION, 'the description, character for character');
      const paragraphs = POPULATED_HANDOFF.split('\n');
      for (let i = 0; i < paragraphs.length; i++) {
        assert.ok(await handoff.nth(i).isVisible(), `handoff paragraph ${i + 1} is drawn`);
        assert.equal(await handoff.nth(i).innerText(), paragraphs[i], `handoff paragraph ${i + 1}, exactly`);
      }
      await snap(page, `populated-${viewport.width}-02-description`, 'The whole description and the handoff, opened by keyboard.', sel('di-scan-details'));

      await tabTo(page, 'di-scan-swarm-toggle');
      await page.keyboard.press('Enter');
      assert.equal(await page.locator(sel('di-scan-swarm-toggle')).getAttribute('aria-expanded'), 'true');
      const unresolved = cards.filter({ hasText: UNRESOLVED_ROLE });
      assert.ok(await unresolved.locator(sel('di-scan-identity')).isVisible(), 'the unresolved member is marked unresolved in the roster');
      assert.equal((await unresolved.locator(sel('di-scan-identity')).innerText()).trim(), UNRESOLVED_IDENTITY.kind === 'unresolved' ? UNRESOLVED_IDENTITY.label : '');
      for (let i = 0; i < ROW.swarm.length; i++) {
        const member = ROW.swarm[i]!;
        const card = cards.nth(i);
        assert.ok(await card.isVisible(), `${member.role}: drawn`);
        assert.ok((await card.innerText()).includes(member.role), `${member.role}: its role`);
        assert.ok((await card.innerText()).includes(member.name), `${member.role}: its name`);
        assert.equal((await card.locator(sel('di-scan-model')).innerText()).trim(), member.model.text, `${member.role}: its model fact, exactly`);
        assert.equal((await card.locator(sel('di-scan-provider')).innerText()).trim(), member.provider.text, `${member.role}: its provider fact, exactly`);
        if (member.note !== '') assert.ok((await card.innerText()).includes(member.note), `${member.role}: its note`);
      }
      const shown = await page.locator(sel('di-scan-swarm')).innerText();
      for (const words of ['Model gpt-5.5 (observed)', 'Provider codex (configured)', '(last seen, not live)', 'Model not recorded'])
        assert.ok(shown.includes(words), `the roster shows "${words}"`);
      assert.equal(
        (await unresolved.locator(sel('di-scan-identity-words')).innerText()).trim(),
        UNRESOLVED_IDENTITY.kind === 'unresolved' ? UNRESOLVED_IDENTITY.words : '',
        'the unresolved identity\'s words, exactly',
      );
      await snap(page, `populated-${viewport.width}-03-roster`, 'Each role\'s model, provider and notes, opened by keyboard; the unresolved identity\'s words.', sel('di-scan-swarm'));

      await tabTo(page, 'di-scan-unresolved-more-summary');
      await page.keyboard.press('Enter');
      const agent = page.locator(sel('di-scan-unresolved-agent'));
      assert.ok(await agent.isVisible());
      assert.equal((await agent.locator('.di-scan-name').innerText()).trim(), STRAY_AGENT.name);
      assert.equal((await agent.locator(sel('di-scan-unresolved-label')).innerText()).trim(), STRAY_AGENT.label);
      assert.equal((await agent.locator(sel('di-scan-unresolved-reason')).innerText()).trim(), `(${STRAY_AGENT.reasonCode})`);
      assert.equal((await agent.locator(sel('di-scan-unresolved-words')).innerText()).trim(), `${STRAY_AGENT.words} ${STRAY_AGENT.reason}.`);

      // and each closes again by keyboard
      await page.locator(sel('di-scan-swarm-toggle')).focus();
      await page.keyboard.press('Enter');
      assert.equal(await page.locator(sel('di-scan-swarm-toggle')).getAttribute('aria-expanded'), 'false');
      assert.equal(await cards.first().isVisible(), false);
      await page.close();
    },
    STEPS_MS,
  );
