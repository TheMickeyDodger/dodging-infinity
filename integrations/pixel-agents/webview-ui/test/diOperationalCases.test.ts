/**
 * feature13: the cases the realistic fixture lacks, rendered by the REAL panel in a real browser and
 * driven by real user actions: a source whose exported objective is only the section header, two
 * Missions whose teams share every member name, and the status classes that matter when reading the
 * office (blocked, failed, waiting, unknown, ended, stale, disconnected).
 *
 * The data is the labelled packaged office state `herdr-task-session-mismatch-v2` (FIXTURE, not live),
 * changed ONLY in the fields each case is about, through the harness's `update` (a later projection,
 * as the office sends). Every objective stays a labelled fixture string; every source id is synthetic.
 *
 * Browser: the installed Chrome (playwright-core, channel `chrome`), else Playwright's own
 * Chromium. These are REQUIRED regressions: with neither browser launchable every test FAILS, naming
 * both launch errors. They never skip (a missing browser must not turn the regression into a pass).
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

import type { DiOfficeState } from '../../core/src/messages.ts';
import { shortMissionRefs } from '../src/components/diPlainSummary.ts';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');

let server: ViteDevServer | null = null;
let browser: Browser | null = null;
let page: Page | null = null;
let url = '';
let unavailable: string | null = null;

beforeAll(async () => {
  server = await createServer({
    configFile: path.resolve(root, 'vite.config.ts'),
    server: { port: 0, strictPort: false },
    logLevel: 'silent',
  });
  await server.listen();
  const addr = server.httpServer?.address();
  const port = typeof addr === 'object' && addr !== null ? addr.port : 0;
  url = `http://localhost:${port}/test/harness/di-panel.html`;
  const firstLine = (e: unknown) => String(e).split('\n')[0];
  try {
    browser = await chromium.launch({ channel: 'chrome', headless: true });
  } catch (chromeError) {
    try {
      browser = await chromium.launch({ headless: true });
    } catch (chromiumError) {
      unavailable = `neither browser could be launched. Installed Chrome (channel "chrome"): ${firstLine(chromeError)} | Playwright's Chromium: ${firstLine(chromiumError)}`;
    }
  }
}, 120_000);

afterAll(async () => {
  await browser?.close();
  await server?.close();
});

beforeEach(async () => {
  // A REQUIRED regression: no browser is a FAILURE, never a skip.
  if (unavailable) throw new Error(`required browser regression cannot run: ${unavailable}`);
  await page?.close();
  page = await browser!.newPage({ viewport: { width: 1440, height: 900 } });
  await page.goto(url, { waitUntil: 'domcontentloaded' });
  await page.waitForSelector('[data-testid="di-office-panel"]', { timeout: 30_000 });
});

const p = () => page!;
const T = (id: string) => `[data-testid="${id}"]`;

type Harness = {
  update: (f: (s: DiOfficeState) => DiOfficeState) => void;
  initial: DiOfficeState;
  office: { selectedAgentId: number | null };
};

/** The two Missions of the fixture whose teams share every member name (lead1, executor1, ...). */
const [A_REF, B_REF] = (
  JSON.parse(
    fs.readFileSync(
      path.join(root, 'test/fixtures/di-office-state-session-mismatch-v2.FIXTURE.json'),
      'utf-8',
    ),
  ) as { state: DiOfficeState }
).state.missions.missions
  .filter((m) => m.members.members.length > 0)
  .map((m) => m.missionRef);

/** Each Mission's short source-qualified ref, as the panel computes it over the same list. */
const shortRefs = shortMissionRefs(
  (
    JSON.parse(
      fs.readFileSync(
        path.join(root, 'test/fixtures/di-office-state-session-mismatch-v2.FIXTURE.json'),
        'utf-8',
      ),
    ) as { state: DiOfficeState }
  ).state.missions.missions,
);

/**
 * A patch to ONE Mission's record, applied in the page as a later projection (the office's own
 * update path), merged recursively: objects merge, arrays merge by index, and `{ $set: v }` assigns v.
 */
type Patch = { [key: string]: Patch | unknown } | { $set: unknown };
async function mutateMission(ref: string, patch: Patch): Promise<void> {
  await p().evaluate(
    ([target, patchJson]) => {
      const merge = (into: Record<string, unknown>, from: Record<string, unknown>) => {
        for (const [key, value] of Object.entries(from)) {
          if (value !== null && typeof value === 'object' && '$set' in (value as object)) {
            into[key] = (value as { $set: unknown }).$set;
          } else if (value !== null && typeof value === 'object' && !Array.isArray(value)) {
            merge((into[key] ??= {}) as Record<string, unknown>, value as Record<string, unknown>);
          } else into[key] = value;
        }
      };
      (window as unknown as { __diHarness: Harness }).__diHarness.update((s) => {
        const mission = s.missions.missions.find((m) => m.missionRef === target)!;
        merge(mission as unknown as Record<string, unknown>, JSON.parse(patchJson));
        return s;
      });
    },
    [ref, JSON.stringify(patch)] as const,
  );
  await p().evaluate(
    () => new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r))),
  );
}
const card = (ref: string) => p().locator(`[data-mission-ref="${ref}"]`);
const chip = async (ref: string) =>
  card(ref)
    .locator(T('di-op-status'))
    .first()
    .evaluate((el) => {
      const glyph = el.querySelector('.di-op-status-glyph')?.textContent ?? '';
      return {
        status: el.getAttribute('data-status'),
        text: (el.textContent ?? '').replace(glyph, '').trim(),
        glyph,
      };
    });

test('a header-only objective is said plainly and source-qualified; status and team still render and the row stays selectable', async () => {
  assert.ok(B_REF);
  await mutateMission(B_REF, {
    objective: { state: 'observed', value: 'OBJECTIVE', reason: null },
  });
  const c = card(B_REF);
  const objective = c.locator(T('di-mission-objective'));
  assert.equal(await objective.getAttribute('data-goal'), 'none');
  assert.match(
    (await objective.innerText()).trim(),
    /^No goal reported: mc exported only the section header “OBJECTIVE”, not a goal\.$/,
  );
  // Nothing was substituted for the goal: no description, title, ref or role text stands in for it.
  assert.doesNotMatch(
    await objective.innerText(),
    /lead|executor|reviewer|supervisor|FIXTURE|SYNTHETIC/,
  );
  // Status and team carry the row.
  assert.deepEqual(await chip(B_REF), { status: 'running', text: 'Running', glyph: '●' });
  assert.match(
    await c.locator(T('di-mission-team')).innerText(),
    /^4 of 4 members observed running · 4 terminals busy$/,
  );
  // The picker says the same, honestly, and keeps the source identity.
  const option = await p().$eval(
    `${T('di-mission-select')} option[value="${B_REF}"]`,
    (el) => (el as HTMLOptionElement).text,
  );
  assert.match(option, /^No goal reported — /);
  // Still selectable and inspectable: the inspector shows the export verbatim and the COMPLETE ref.
  await c.locator(T('di-open-inspector-mission')).click();
  await p().waitForSelector('[role="dialog"]');
  assert.equal(
    await p().$eval(T('di-inspector-short-ref'), (el) => el.getAttribute('title')),
    B_REF,
  );
  await p().click(`${T('di-inspector-provenance')} > summary`);
  assert.equal(await p().$eval(T('di-mission-qualifier'), (el) => el.textContent), B_REF);
  const dialog = await p().$eval('[role="dialog"]', (el) => (el as HTMLElement).innerText);
  assert.match(dialog, /objective as exported\s+“OBJECTIVE”/);
  assert.match(dialog, /No goal reported/);
});

test('two Missions whose teams share every member name stay two records: selection never swaps them', async () => {
  const [aMembers, bMembers] = await p().evaluate(
    ([a, b]) => {
      const s = (window as unknown as { __diHarness: Harness }).__diHarness.initial;
      const idOf = (ref: string) =>
        Object.fromEntries(
          s.missions.missions
            .find((m) => m.missionRef === ref)!
            .members.members.map((m) => [m.logical, m.characterId]),
        );
      return [idOf(a), idOf(b)];
    },
    [A_REF, B_REF] as const,
  );
  assert.deepEqual(
    Object.keys(aMembers).sort(),
    Object.keys(bMembers).sort(),
    'the names really are shared',
  );
  assert.notEqual(aMembers['executor1'], bMembers['executor1']);

  // Find lists the two executor1 records separately, never merged.
  await p().fill(T('di-find-input'), 'executor1');
  assert.match(
    await p().$eval(T('di-find-outcome'), (el) => el.textContent ?? ''),
    /different records/,
  );

  for (const [ref, ids, other] of [
    [B_REF, bMembers, A_REF],
    [A_REF, aMembers, B_REF],
  ] as const) {
    const c = card(ref);
    const roster = c.locator(T('di-mission-roster'));
    if (!(await roster.evaluate((el) => (el as HTMLDetailsElement).open)))
      await roster.locator('summary').click();
    await c.locator(T('di-member-row')).filter({ hasText: 'executor1 · executor' }).first().click();
    await p().waitForFunction(
      (id) =>
        (window as unknown as { __diHarness: Harness }).__diHarness.office.selectedAgentId === id,
      ids['executor1'],
      { timeout: 5_000 },
    );
    const dock = await p().$eval(T('di-selected-agent'), (el) => (el as HTMLElement).innerText);
    const named = await p().$eval(T('di-selected-member-mission'), (el) =>
      el.getAttribute('title'),
    );
    assert.equal(named, ref, `the dock names ${ref}`);
    assert.notEqual(named, other, `...and not the lookalike ${other}`);
    assert.ok(dock.includes(shortRefs.get(ref)!), 'its short source-qualified ref is shown');
    assert.ok(!dock.includes(shortRefs.get(other)!), '...and not the lookalike\x27s');
    assert.ok(dock.includes('executor1'));
  }
});

test('blocked reads Blocked only from a live, confirmed member; a not-live blocked report is a last-known detail', async () => {
  await mutateMission(B_REF, { members: { members: { 0: { activity: 'blocked' } } } });
  assert.deepEqual(await chip(B_REF), { status: 'blocked', text: 'Blocked', glyph: '▲' });
  assert.match(await card(B_REF).innerText(), /report blocked \(the terminal's own report\)/);
  await mutateMission(B_REF, { members: { members: { 0: { live: false } } } });
  assert.notEqual((await chip(B_REF)).status, 'blocked');
  assert.match(
    await card(B_REF).locator(T('di-op-terminal-note')).first().innerText(),
    /last known, not current/,
  );
});

test('failed is the source’s REPORT, not a verified fact; ended is not verified success; waiting and unknown are never zero', async () => {
  await mutateMission(B_REF, {
    v2: {
      missionState: {
        completion: { state: 'observed', reportedComplete: true, reportedSuccess: false },
      },
    },
  });
  assert.deepEqual(await chip(B_REF), { status: 'failed', text: 'Failed', glyph: '✕' });
  assert.match(
    await card(B_REF).locator(T('di-op-status-sentence')).first().innerText(),
    /reported by the source, not verified/,
  );
  await mutateMission(B_REF, {
    v2: { missionState: { completion: { state: 'not_recorded' } } },
    standing: 'terminal',
  });
  assert.deepEqual(await chip(B_REF), { status: 'ended', text: 'Ended', glyph: '■' });
  assert.match(await card(B_REF).innerText(), /Ended is not verified success/);
  await mutateMission(B_REF, { standing: 'inactive' });
  assert.deepEqual(await chip(B_REF), { status: 'waiting', text: 'Waiting', glyph: '◐' });
  await mutateMission(B_REF, {
    standing: 'active',
    bindingState: 'unverified',
    members: { state: 'unavailable', members: { $set: [] } },
  });
  assert.deepEqual(await chip(B_REF), { status: 'unknown', text: 'Unknown', glyph: '○' });
  // Unknown is not zero: the team line never claims "Team 0".
  assert.doesNotMatch(await card(B_REF).locator(T('di-mission-team')).innerText(), /Team 0/);
});

test('every status has a word AND its own shape (never colour alone), including stale and disconnected', async () => {
  const seen = new Map<string, string>();
  const read = async () => {
    const all = await p().$$eval(`${T('di-op-status')}`, (els) =>
      els.map((el) => ({
        status: el.getAttribute('data-status')!,
        word: (el as HTMLElement).innerText.trim(),
        glyph: el.querySelector('.di-op-status-glyph')?.textContent ?? '',
        hidden: el.querySelector('.di-op-status-glyph')?.getAttribute('aria-hidden'),
      })),
    );
    for (const s of all) {
      assert.ok(s.word.length > 0 && s.glyph.length > 0, `${s.status} has a word and a shape`);
      assert.equal(
        s.hidden,
        'true',
        'the shape is decorative; the word carries it for assistive tech',
      );
      seen.set(s.status, s.glyph);
    }
  };
  await read();
  const set = async (reason: string) =>
    p().evaluate(
      (r) =>
        (window as unknown as { __diHarness: Harness }).__diHarness.update((s) => {
          s.live = false;
          s.notLiveReason = r;
          return s;
        }),
      reason,
    );
  await set('client_expired:no_office_state_before_valid_until');
  await p().waitForSelector(`${T('di-op-status')}[data-status="stale"]`);
  await read();
  await set('transport_disconnected');
  await p().waitForSelector(`${T('di-op-status')}[data-status="disconnected"]`);
  await read();
  for (const status of ['running', 'stale', 'disconnected'])
    assert.ok(seen.has(status), `${status} was rendered`);
  assert.equal(new Set(seen.values()).size, seen.size, 'each status has its own shape');
});
