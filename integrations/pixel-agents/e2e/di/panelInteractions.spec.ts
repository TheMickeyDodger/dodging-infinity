/**
 * feature13 — LOCAL INTERACTION MEASUREMENTS of the surfaces this feature touched, in the PRODUCTION
 * BUILD, against OWNED labelled FIXTURES (SYNTHETIC, not live). An evidence INSTRUMENT: it sets no budget
 * and asserts nothing about speed; it only fails if an interaction does not happen at all.
 *
 * WHAT IS TIMED, in the page, from the user's input EVENT (pointerdown / keydown, capture phase, its own
 * `timeStamp`) to (a) the moment the DOM satisfies the expectation ("toDom", sub-millisecond) and (b) the
 * SECOND requestAnimationFrame CALLBACK after it ("toSecondRaf"). A requestAnimationFrame callback is
 * scheduled before the browser paints; it is NOT evidence that anything was painted or presented on a
 * display, and these are EVENT-TO-DOM and CALLBACK timings only: never FPS, never "presented", never
 * "frame time". "toSecondRaf" is quantised to the callback interval (about 8.3 ms on the machine used):
 *   open the inspector from a Mission card · close it (Escape) · select a team member's roster row ·
 *   open the inspector from the Selected dock · close it · locate-from-inspector ("Show in the office") to
 *   the world being visible again with that agent labelled · arrow-key list navigation (Find results) ·
 *   a real canvas click on a character (the world's label, and the dock catching up).
 * It also records requestAnimationFrame CALLBACK INTERVALS in three conditions (panel open, inspector open,
 * after interactions) — callback intervals, NOT a frame rate.
 *
 * LOADS (both existing, none invented): ORDINARY = the realistic fixture, 11 Missions (contract v1);
 * CROWDED = the pinned herdr-task v2 answer, 64 Missions / 16 runtime agents (8 not verified), the largest
 * load server/__tests__/diHerdrTaskConsumer.test.ts exercises ('herdr-task-crowded-v2').
 *
 * REPEATS: SAMPLES per metric per session, SESSIONS fresh page loads per load; every session is kept, so
 * the spread across sessions is visible next to the pooled min / median / p95 / max.
 *
 * LIMITS (also written into the output): LOCAL measurements on this machine, NOT GitHub CI; interaction and
 * callback timings are NOT FPS; the fixture backend's request gaps are its POLL CADENCE, not panel
 * performance, and are not measured here; headless Chromium under test automation.
 *
 * Output: $DI_MEASURE_DIR/panelInteractions-measurements.json (default test-results/di-office/).
 * Run: DI_MEASURE_DIR=<abs dir> npx playwright test --config e2e/di/playwright.di.config.ts e2e/di/panelInteractions.spec.ts
 */
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';

import { expect, type Page, test } from '@playwright/test';

import { dockNamesPredicate, memberRowName, visibleMissionRef } from './dockIdentity';
import {
  buildIdentity,
  characterPoint,
  delay,
  openOffice,
  startSandboxOffice,
  storedState,
} from './officeSandbox';
import { buildCrowdedV2 } from './operationalVariants';
import { buildRealisticFixture } from './realisticFixture';

const SAMPLES = 15;
/** World clicks need a fresh load each (see runWorldClicks), so fewer. */
const WORLD_SAMPLES = 8;
const SESSIONS = 3;
const RAF_WINDOW_MS = 3000;
const SETTLE_MS = 150;

interface WireMember {
  memberRef: string;
  logical: string;
  characterId: number | null;
}
interface WireState {
  missions: { missions: Array<{ missionRef: string; members: { members: WireMember[] } }> };
}
type Sample = { toDom: number; toSecondRaf: number };
type Series = Record<string, Sample[]>;

/** Installs the capture-phase input recorder once per page. */
async function installRecorder(page: Page): Promise<void> {
  await page.evaluate(() => {
    const w = window as unknown as { __armed: boolean; __t0: number | null };
    w.__armed = false;
    w.__t0 = null;
    const capture = (e: Event) => {
      if (w.__armed && w.__t0 === null) w.__t0 = e.timeStamp;
    };
    for (const type of ['pointerdown', 'keydown']) window.addEventListener(type, capture, true);
  });
}

/**
 * Arm, perform the action, and read when each predicate (a JS function source evaluated in the page)
 * first holds: at DOM update (MutationObserver + a per-callback check) and at the second
 * requestAnimationFrame callback after it (a callback time, not a presentation time).
 */
async function timed(
  page: Page,
  act: () => Promise<void>,
  predicates: Record<string, string>,
): Promise<Record<string, Sample>> {
  await page.evaluate((preds) => {
    const w = window as unknown as Record<string, unknown> & {
      __armed: boolean;
      __t0: number | null;
    };
    w.__result = {};
    w.__t0 = null;
    const checks = Object.entries(preds).map(
      ([name, src]) => [name, new Function(`return (${src})()`) as () => boolean] as const,
    );
    const done = new Set<string>();
    const results = w.__result as Record<string, { toDom: number; toSecondRaf: number }>;
    const evaluate = () => {
      if (w.__t0 === null) return;
      for (const [name, check] of checks) {
        if (done.has(name) || !check()) continue;
        done.add(name);
        const toDom = performance.now() - (w.__t0 as number);
        requestAnimationFrame(() =>
          requestAnimationFrame(() => {
            results[name] = { toDom, toSecondRaf: performance.now() - (w.__t0 as number) };
          }),
        );
      }
    };
    const observer = new MutationObserver(evaluate);
    observer.observe(document.documentElement, {
      subtree: true,
      childList: true,
      attributes: true,
      characterData: true,
    });
    const frame = () => {
      evaluate();
      if (Object.keys(results).length < checks.length) requestAnimationFrame(frame);
      else observer.disconnect();
    };
    requestAnimationFrame(frame);
    w.__armed = true;
  }, predicates);
  await act();
  try {
    await page.waitForFunction(
      (n) => Object.keys((window as unknown as { __result: object }).__result).length >= n,
      Object.keys(predicates).length,
      { timeout: 10_000 },
    );
  } catch (err) {
    // The 10 s is this INSTRUMENT's own wait, never a product budget. A predicate that never holds is either
    // an instrument bug (wrong condition) or a product non-response: name which keys were seen, so the
    // difference can be read from the failure instead of guessed.
    const seen = await page
      .evaluate(() => Object.keys((window as unknown as { __result?: object }).__result ?? {}))
      .catch(() => ['<page unreadable>']);
    throw new Error(
      `instrument wait timed out: predicates expected [${Object.keys(predicates).join(', ')}], observed [${seen.join(', ')}]\n${String(err)}`,
    );
  }
  const result = await page.evaluate(() => {
    const w = window as unknown as { __result: Record<string, Sample>; __armed: boolean };
    w.__armed = false;
    return w.__result;
  });
  await delay(SETTLE_MS);
  return result;
}

const record = (series: Series, name: string, s: Sample) => (series[name] ??= []).push(s);

async function rafIntervals(page: Page, ms: number): Promise<number[]> {
  return await page.evaluate(
    (window_ms) =>
      new Promise<number[]>((resolve) => {
        const intervals: number[] = [];
        let last = performance.now();
        const start = last;
        const tick = (t: number) => {
          intervals.push(t - last);
          last = t;
          if (t - start < window_ms) requestAnimationFrame(tick);
          else resolve(intervals.slice(1));
        };
        requestAnimationFrame(tick);
      }),
    ms,
  );
}

function stats(values: number[]) {
  const v = [...values].sort((a, b) => a - b);
  const q = (p: number) => v[Math.min(v.length - 1, Math.ceil((p / 100) * v.length) - 1)];
  return {
    n: v.length,
    min: +v[0].toFixed(2),
    p50: +q(50).toFixed(2),
    p95: +q(95).toFixed(2),
    max: +v[v.length - 1].toFixed(2),
    mean: +(v.reduce((a, b) => a + b, 0) / v.length).toFixed(2),
  };
}

const DIALOG_OPEN = `() => document.querySelector('[role="dialog"][aria-modal="true"]') !== null`;
const DIALOG_GONE = `() => document.querySelector('[role="dialog"]') === null`;

/** One session: SAMPLES of every interaction on a freshly loaded page. */
async function runSession(
  page: Page,
  officeUrl: string,
  load: { name: string; findQuery: string },
): Promise<{
  series: Series;
  raf: Record<string, number[]>;
}> {
  await page.goto(officeUrl);
  await expect(page.getByTestId('di-office-panel')).toBeVisible({ timeout: 30_000 });
  await expect
    .poll(() => page.evaluate(() => document.querySelectorAll('[data-mission-ref]').length), {
      timeout: 30_000,
    })
    .toBeGreaterThan(0);
  await delay(1500); // let the world settle (characters seated)
  await installRecorder(page);
  const series: Series = {};
  const raf: Record<string, number[]> = {};

  // Two Missions with a drawn member each: their rosters are the selection rows.
  const state = await storedState<WireState>(page);
  const withTeams = state.missions.missions.filter((m) =>
    m.members.members.some((x) => x.characterId !== null),
  );
  expect(withTeams.length).toBeGreaterThanOrEqual(1);
  const targets = withTeams.slice(0, 2).map((m) => ({
    ref: m.missionRef,
    member: m.members.members.find((x) => x.characterId !== null)!,
  }));
  const cardOf = (ref: string) => page.locator(`[data-mission-ref="${ref}"]`);
  for (const t of targets) {
    const roster = cardOf(t.ref).getByTestId('di-mission-roster');
    if (!(await roster.evaluate((el) => (el as HTMLDetailsElement).open)))
      await roster.locator('summary').click();
  }
  const visible = new Map<string, string>();
  for (const t of targets) visible.set(t.ref, await visibleMissionRef(page, t.ref));
  const rowOf = (t: (typeof targets)[number]) =>
    cardOf(t.ref)
      .getByTestId('di-member-row')
      .filter({ hasText: memberRowName(t.member.logical) })
      .first();
  const dockNames = (t: (typeof targets)[number]) =>
    dockNamesPredicate(t.ref, visible.get(t.ref)!, t.member.logical);

  // 1+2. open the inspector from a card, close it.
  const firstCard = page
    .locator('[data-mission-ref]')
    .first()
    .getByTestId('di-open-inspector-mission');
  for (let i = 0; i < SAMPLES; i++) {
    record(
      series,
      'openFromCard',
      (await timed(page, () => firstCard.click(), { r: DIALOG_OPEN })).r,
    );
    record(
      series,
      'closeFromCard',
      (await timed(page, () => page.keyboard.press('Escape'), { r: DIALOG_GONE })).r,
    );
  }
  // 3. select a team member's roster row (alternating, so every click changes the selection).
  for (let i = 0; i < SAMPLES; i++) {
    const t = targets[i % targets.length];
    record(series, 'selectRow', (await timed(page, () => rowOf(t).click(), { r: dockNames(t) })).r);
  }
  // 4+5. open the inspector from the Selected dock, close it.
  const dockOpen = page.getByTestId('di-selected-agent').getByTestId('di-open-inspector-selection');
  for (let i = 0; i < SAMPLES; i++) {
    record(
      series,
      'openFromDock',
      (await timed(page, () => dockOpen.click(), { r: DIALOG_OPEN })).r,
    );
    if (i === 0) raf['inspectorOpen'] = await rafIntervals(page, RAF_WINDOW_MS);
    record(
      series,
      'closeFromDock',
      (await timed(page, () => page.keyboard.press('Escape'), { r: DIALOG_GONE })).r,
    );
  }
  // 6. locate-from-inspector to the world being visible again with THAT agent labelled.
  for (let i = 0; i < SAMPLES; i++) {
    const t = targets[i % targets.length];
    await rowOf(t).click();
    await page.waitForFunction(new Function(`return (${dockNames(t)})()`) as () => boolean);
    await dockOpen.click();
    // The selection's own locate is in the details' first section (its full record, under
    // "Everything recorded", carries none); the locator is strict, so it must be exactly one.
    await page.waitForSelector(
      '[data-testid="di-inspector-selected"] [data-testid="di-show-in-office"]',
    );
    const locate = page.locator(
      '[data-testid="di-inspector-selected"] [data-testid="di-show-in-office"]',
    );
    const id = t.member.characterId;
    record(
      series,
      'locateToWorld',
      (
        await timed(page, () => locate.click(), {
          r: `() => document.querySelector('[role="dialog"]') === null && document.querySelector('[data-testid="agent-overlay"][data-agent-id="${id}"]') !== null`,
        })
      ).r,
    );
  }
  raf['panelOpen'] = await rafIntervals(page, RAF_WINDOW_MS);
  // 7. arrow-key list navigation in Find (the list the panel scrolls and focuses).
  await page.getByTestId('di-find-input').fill(load.findQuery);
  await page.getByTestId('di-find-input').press('ArrowDown');
  const rows = await page.getByTestId('di-find-result').count();
  expect(rows).toBeGreaterThan(2);
  for (let i = 0; i < SAMPLES; i++) {
    const key = i % 2 === 0 ? 'ArrowDown' : 'ArrowUp';
    await page.evaluate(
      () => ((window as unknown as { __prev: Element | null }).__prev = document.activeElement),
    );
    record(
      series,
      'arrowKey',
      (
        await timed(page, () => page.keyboard.press(key), {
          r: `() => document.activeElement !== window.__prev && document.activeElement?.matches('[data-testid="di-find-result"]')`,
        })
      ).r,
    );
  }
  await page.getByTestId('di-find-input').fill('');
  raf['afterInteractions'] = await rafIntervals(page, RAF_WINDOW_MS);
  return { series, raf };
}

/**
 * World selection by a REAL canvas click. Each sample is a fresh page load (the camera follows a selected
 * agent, so a second click would land on a panned view — officeUsability.spec.ts reloads for the same
 * reason): it is therefore the FIRST interaction after load, stated as such. Measures the world (the
 * agent's label appears) and the dock catching up (it follows the office selection on a 250 ms poll).
 */
async function runWorldClicks(page: Page, officeUrl: string, count: number): Promise<Series> {
  const series: Series = {};
  for (let i = 0; i < count; i++) {
    await page.goto(officeUrl);
    await expect(page.getByTestId('di-office-panel')).toBeVisible({ timeout: 30_000 });
    await expect
      .poll(() => page.evaluate(() => document.querySelectorAll('[data-mission-ref]').length), {
        timeout: 30_000,
      })
      .toBeGreaterThan(0);
    await delay(1500);
    await installRecorder(page);
    const state = await storedState<WireState>(page);
    const owner = new Map<number, { ref: string; logical: string }>();
    for (const m of state.missions.missions)
      for (const x of m.members.members)
        if (x.characterId !== null)
          owner.set(x.characterId, { ref: m.missionRef, logical: x.logical });
    const typing = await page.evaluate(() =>
      (
        (
          window as unknown as {
            __pixelAgentsTestHooks?: {
              getCharacters?: () => Array<{
                id: number;
                state: string;
                matrixEffect: string | null;
                isGreeter?: boolean;
              }>;
            };
          }
        ).__pixelAgentsTestHooks?.getCharacters?.() ?? []
      )
        .filter((c) => !c.isGreeter && c.state === 'type' && !c.matrixEffect)
        .map((c) => c.id),
    );
    const candidates: number[] = [];
    for (const id of typing)
      if (owner.has(id) && (await characterPoint(page, id)).onCanvas) candidates.push(id);
    expect(candidates.length).toBeGreaterThanOrEqual(1);
    const id = candidates[i % candidates.length];
    const who = owner.get(id)!;
    const point = await characterPoint(page, id);
    const whoVisible = await visibleMissionRef(page, who.ref);
    const r = await timed(page, () => page.mouse.click(point.x, point.y), {
      world: `() => document.querySelector('[data-testid="agent-overlay"][data-agent-id="${id}"]') !== null`,
      dock: dockNamesPredicate(who.ref, whoVisible, who.logical),
    });
    record(series, 'worldClickToLabel', r.world);
    record(series, 'worldClickToDock', r.dock);
  }
  return series;
}

const LOADS = [
  {
    name: 'ordinary',
    label: 'ORDINARY: the realistic fixture, 11 Missions (contract v1)',
    findQuery: 'executor',
    crowded: false,
  },
  {
    name: 'crowded',
    label:
      'CROWDED: the pinned herdr-task v2 answer, 64 Missions / 16 runtime agents (8 not verified)',
    findQuery: 'e',
    crowded: true,
  },
] as const;

test('feature13 local interaction measurements (FIXTURE; local machine, not CI; not FPS)', async ({
  browser,
}) => {
  test.setTimeout(600_000);
  const identity = buildIdentity();
  const output: Record<string, unknown> = {
    note: 'LOCAL measurements on this machine (not GitHub CI). Interaction and callback timings are NOT FPS. The fixture backend request gaps are its poll cadence, not panel performance, and are not reported here.',
    method:
      'In-page: from the input event timeStamp (pointerdown / keydown, capture phase) to the DOM satisfying the expectation (toDom) and to the second requestAnimationFrame callback after it (toSecondRaf, quantised to the callback interval). These are event-to-DOM and callback timings only: not FPS, not frame time, and not evidence of presentation on a display.',
    samplesPerMetricPerSession: SAMPLES,
    sessionsPerLoad: SESSIONS,
    machine: {
      node: process.version,
      platform: `${os.platform()} ${os.arch()} ${os.release()}`,
      cpu: os.cpus()[0]?.model,
      cpus: os.cpus().length,
      memoryGB: Math.round(os.totalmem() / 2 ** 30),
      chromium: browser.version(),
    },
    build: identity,
    loads: {} as Record<string, unknown>,
  };
  for (const load of LOADS) {
    const fixture = load.crowded ? buildCrowdedV2() : buildRealisticFixture();
    const office = await startSandboxOffice({
      prefix: `di-perf-${load.name}`,
      envelopeAt: (now) => fixture.envelopeAt(now),
      contractVersion: load.crowded ? 2 : 1,
    });
    try {
      const sessions: Array<{ series: Series; raf: Record<string, number[]> }> = [];
      for (let s = 0; s < SESSIONS; s++) {
        const context = await browser.newContext({
          viewport: { width: 1440, height: 900 },
          deviceScaleFactor: 2,
        });
        const page = await context.newPage();
        await page.addInitScript(() => {
          (window as unknown as { __PIXEL_AGENTS_E2E?: boolean }).__PIXEL_AGENTS_E2E = true;
        });
        try {
          await openOffice(page, office);
          const session = await runSession(page, office.url, load);
          const world = await runWorldClicks(page, office.url, WORLD_SAMPLES);
          Object.assign(session.series, world);
          sessions.push(session);
        } finally {
          await context.close();
        }
      }
      const metrics = Object.keys(sessions[0].series);
      const pooled: Record<string, unknown> = {};
      const perSession: Record<string, unknown> = {};
      for (const m of metrics) {
        for (const field of ['toDom', 'toSecondRaf'] as const) {
          const all = sessions.flatMap((s) => s.series[m].map((x) => x[field]));
          pooled[`${m}.${field}`] = stats(all);
          perSession[`${m}.${field}`] = sessions.map(
            (s) => stats(s.series[m].map((x) => x[field])).p50,
          );
        }
      }
      const rafPooled: Record<string, unknown> = {};
      for (const c of Object.keys(sessions[0].raf))
        rafPooled[c] = stats(sessions.flatMap((s) => s.raf[c]));
      (output.loads as Record<string, unknown>)[load.name] = {
        load: load.label,
        missions: load.crowded ? (fixture as ReturnType<typeof buildCrowdedV2>).missionCount : 11,
        pooledMs: pooled,
        perSessionMedianMs: perSession,
        rafCallbackIntervalsMs: rafPooled,
      };
    } finally {
      await office.stop();
    }
  }
  const dir = process.env['DI_MEASURE_DIR'] ?? path.join(__dirname, '../../test-results/di-office');
  fs.mkdirSync(dir, { recursive: true });
  const file = path.join(dir, 'panelInteractions-measurements.json');
  fs.writeFileSync(file, `${JSON.stringify(output, null, 2)}\n`);
  console.log(`[feature13 measurements] ${file}`);
  for (const [name, load] of Object.entries(
    output.loads as Record<
      string,
      { pooledMs: Record<string, { n: number; p50: number; p95: number; max: number }> }
    >,
  ))
    for (const [k, v] of Object.entries(load.pooledMs))
      if (k.endsWith('.toSecondRaf'))
        console.log(`[${name}] ${k} n=${v.n} p50=${v.p50} p95=${v.p95} max=${v.max}`);
});
