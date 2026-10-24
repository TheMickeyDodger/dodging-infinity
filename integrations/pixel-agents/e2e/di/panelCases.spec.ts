/**
 * feature13 — the three HARD CASES in the PRODUCTION BUILD, through real user paths, against OWNED
 * labelled FIXTURE variants (operationalVariants.ts; SYNTHETIC, not live evidence, no contract extended):
 *
 *  - DUPLICATE NAMES: three Missions whose team shares the name `executor1`, and two Missions with an
 *    IDENTICAL objective, stay distinct, source-qualified records. A real canvas click on each selects
 *    THAT record in the panel, the world and the inspector, never a lookalike; Find lists them apart.
 *  - BLOCKED / FAILED: the card carries the status WORD and SHAPE; the inspector cites the exact source
 *    fields; a replaced (non-present, not-live) member reporting `blocked` does NOT promote Blocked; a
 *    Mission State reporting completion without success reads Failed, as REPORTED and not verified.
 *  - HEADER-ONLY OBJECTIVE: the card says plainly, source-qualified, that the source reported no goal
 *    (never the heading as a goal); status and team still render; the inspector heading stays bounded and
 *    qualified.
 *
 * Self-isolating (officeSandbox.ts). Run from integrations/pixel-agents against a fresh production build.
 */
import { expect, type Locator, type Page, test } from '@playwright/test';

import { expectDockNamesMission, memberRowName } from './dockIdentity';
import {
  characterPoint,
  drawnCount,
  openOffice,
  startSandboxOffice,
  storedState,
} from './officeSandbox';
import { buildCasesV1, buildCasesV2 } from './operationalVariants';

interface WireMember {
  memberRef: string;
  logical: string;
  chain: string;
  characterId: number | null;
}
interface WireState {
  missions: { missions: Array<{ missionRef: string; members: { members: WireMember[] } }> };
}

const card = (page: Page, ref: string): Locator => page.locator(`[data-mission-ref="${ref}"]`);
const chip = (page: Page, ref: string) =>
  card(page, ref)
    .getByTestId('di-op-status')
    .first()
    .evaluate((el) => {
      const glyph = el.querySelector('.di-op-status-glyph')?.textContent ?? '';
      return {
        status: el.getAttribute('data-status'),
        word: (el.textContent ?? '').replace(glyph, '').trim(),
        glyph,
      };
    });

/** Open a Mission's inspector as a person does, read it, and close it with Escape. */
async function inMissionInspector<T>(
  page: Page,
  ref: string,
  read: (d: Locator) => Promise<T>,
): Promise<T> {
  await card(page, ref).getByTestId('di-open-inspector-mission').click();
  const dialog = page.getByRole('dialog');
  await expect(dialog).toBeVisible();
  try {
    return await read(dialog);
  } finally {
    await page.keyboard.press('Escape');
    await expect(dialog).toHaveCount(0);
  }
}

/**
 * OBSERVATION ONLY (never an assertion, never a substitute for one): a SECOND SAMPLE of the page at a character's click point. The first check below fails with only the covering element's
 * TAG ("top BUTTON"). The body's `finally` awaits `office.stop()`, so the error context Playwright saves MAY reflect a torn-down page; reading the source does not fix when that snapshot is
 * captured, so it is not used as the state at the check.
 *
 * ORDER, stated because two samples are not one instant: sample 1 is the real `characterPoint` call (its `onCanvas` is what the assertion tests); sample 2 is this function, a SEPARATE
 * `page.evaluate` made afterwards and before the assertion. Between them the page can change (an animation frame, a character moving, the transport). So this reports what was under the point
 * at sample 2, NOT at the instant of sample 1. Both are logged and neither overwrites the other; a disagreement between them is information (the page changed between the samples), and the
 * first sample stands as what the assertion saw. It records the stack under the point (identity, test id, label, rectangle), the canvas and panel rectangles, the camera, the character,
 * the update notice and the reception button, the time since the page began, and whether the connection indicator shows.
 */
async function observeCoverage(page: Page, id: number, at: { x: number; y: number }) {
  return page.evaluate(
    ({ id, x, y }) => {
      const rect = (el: Element | null) => {
        if (!el) return null;
        const r = el.getBoundingClientRect();
        return [Math.round(r.left), Math.round(r.top), Math.round(r.right), Math.round(r.bottom)];
      };
      const hooks = (
        window as unknown as {
          __pixelAgentsTestHooks?: {
            getCharacters?: () => Array<{ id: number; x: number; y: number; state: string }>;
            getView?: () => unknown;
          };
        }
      ).__pixelAgentsTestHooks;
      const canvas = Array.from(document.querySelectorAll('canvas')).sort(
        (a, b) => b.clientWidth * b.clientHeight - a.clientWidth * a.clientHeight,
      )[0];
      const ch = hooks?.getCharacters?.().find((c) => c.id === id);
      return {
        msSincePageBegan: Math.round(performance.now()),
        viewport: [innerWidth, innerHeight],
        point: [Math.round(x * 10) / 10, Math.round(y * 10) / 10],
        canvasRect: rect(canvas ?? null),
        panelRect: rect(document.querySelector('[data-testid="di-office-panel"]')),
        insideCanvasRect: canvas
          ? x >= canvas.getBoundingClientRect().left &&
            x < canvas.getBoundingClientRect().right &&
            y >= canvas.getBoundingClientRect().top &&
            y < canvas.getBoundingClientRect().bottom
          : null,
        character: ch ? { x: Math.round(ch.x), y: Math.round(ch.y), state: ch.state } : null,
        view: hooks?.getView?.() ?? null,
        updateNotice: rect(document.querySelector('.di-version-notice')),
        receptionButton: rect(document.querySelector('[data-testid="di-reception-target"]')),
        connectionIndicatorShown: /Reconnecting/.test(document.body.innerText),
        stack: document
          .elementsFromPoint(x, y)
          .slice(0, 6)
          .map((el) => ({
            tag: el.tagName,
            id: el.id || null,
            testid: el.getAttribute('data-testid'),
            label: el.getAttribute('aria-label') ?? (el.textContent ?? '').trim().slice(0, 40),
            cls: String(el.getAttribute('class') ?? '')
              .split(' ')
              .slice(0, 3)
              .join(' '),
            rect: rect(el),
          })),
      };
    },
    { id, x: at.x, y: at.y },
  );
}

test('built app, contract v1: duplicate names, blocked and a header-only objective (FIXTURE, not live)', async ({
  page,
}) => {
  const cases = buildCasesV1();
  const office = await startSandboxOffice({ prefix: 'di-cases-v1', envelopeAt: cases.envelopeAt });
  try {
    await page.setViewportSize({ width: 1440, height: 900 });
    await openOffice(page, office);
    // Every live member is drawn (a blocked terminal is not animated as typing, so count drawn, not typing).
    await expect.poll(() => drawnCount(page), { timeout: 30_000 }).toBe(cases.liveMemberCount);

    // ── HEADER-ONLY OBJECTIVE ───────────────────────────────────────────────────────────────────
    const header = card(page, cases.refs.header);
    const objective = header.getByTestId('di-mission-objective');
    await expect(objective).toHaveAttribute('data-goal', 'none');
    await expect(objective).toHaveText(
      'No goal reported: synthetic-core exported only the section header “OBJECTIVE”, not a goal.',
    );
    // Status and team still carry the row; its source identity is intact and it stays inspectable.
    expect(await chip(page, cases.refs.header)).toEqual({
      status: 'running',
      word: 'Running',
      glyph: '●',
    });
    await expect(header.getByTestId('di-mission-team')).toContainText(
      '1 of 1 members observed running',
    );
    await inMissionInspector(page, cases.refs.header, async (dialog) => {
      const title = page.locator('#di-inspector-title');
      await expect(title).toHaveText(/^No goal reported: mn-[0-9a-f]{8,}/);
      await dialog.getByTestId('di-inspector-provenance').locator('summary').click();
      await expect(dialog.getByTestId('di-mission-qualifier')).toHaveText(cases.refs.header);
      // The heading is BOUNDED (at most two lines) however long the export is.
      const lines = await title.evaluate(
        (el) => el.getBoundingClientRect().height / parseFloat(getComputedStyle(el).lineHeight),
      );
      expect(lines).toBeLessThanOrEqual(2.05);
      await expect(dialog).toContainText(/objective as exported\s*“OBJECTIVE”/);
      await expect(dialog.getByTestId('di-inspector-team')).toBeVisible();
    });

    // ── DUPLICATE NAMES ─────────────────────────────────────────────────────────────────────────
    const stored = await storedState<WireState>(page);
    const executors = cases.executor1Refs.map((ref) => {
      const m = stored.missions.missions.find((x) => x.missionRef === ref)!;
      return { missionRef: ref, ...m.members.members.find((x) => x.logical === 'executor1')! };
    });
    expect(new Set(executors.map((e) => e.memberRef)).size).toBe(3);
    expect(new Set(executors.map((e) => e.characterId)).size).toBe(3);
    // Find lists the three records apart, never merged.
    await page.getByTestId('di-find-input').fill('executor1');
    await expect(page.getByTestId('di-find-outcome')).toContainText(
      /3 records match “executor1”.*different records/,
    );
    const results = page.getByTestId('di-find-result');
    await expect(results).toHaveCount(3);
    const resultTexts = await results.allInnerTexts();
    expect(new Set(resultTexts).size).toBe(3);
    // Two Missions with an IDENTICAL objective stay two cards, apart by ref, zone and inspector.
    const a = card(page, cases.refs.dupA);
    const b = card(page, cases.refs.dupB);
    expect(await a.getByTestId('di-mission-objective').innerText()).toBe(
      await b.getByTestId('di-mission-objective').innerText(),
    );
    expect(await a.getByTestId('di-mission-zone').innerText()).not.toBe(
      await b.getByTestId('di-mission-zone').innerText(),
    );
    // A REAL click on each executor1 selects THAT record in the world, the dock and the inspector.
    await page.getByTestId('di-find-input').fill('');
    for (const e of executors) {
      const point = await characterPoint(page, e.characterId!);
      // observation only. SAMPLE 1 is the real characterPoint call: its `stack` was read in the SAME evaluate as the `onCanvas` that is asserted, so it is what covered the point when the check was
      // decided. SAMPLE 2 (observeCoverage, see its comment) is a LATER evaluate that adds context. Both are logged for every executor, passing or not, with the ordering named, and neither overwrites
      // the other; the assertion's own message carries sample 1's stack.
      const observed = await observeCoverage(page, e.characterId!, point);
      console.log(
        `[executor click point] ${e.memberRef}: SAMPLE 1 (characterPoint, one evaluate with its stack; asserted) ${JSON.stringify({ x: point.x, y: point.y, onCanvas: point.onCanvas, top: point.topElement, stack: point.stack })}; SAMPLE 2 (observeCoverage, a LATER evaluate) ${JSON.stringify(observed)}; samples agree on the top element: ${observed.stack[0]?.tag === point.topElement.replace(/#.*/, '')}`,
      );
      expect(
        point.onCanvas,
        `${e.memberRef} clickable (top ${point.topElement}; stack at the asserted instant ${JSON.stringify(point.stack.slice(0, 3))})`,
      ).toBe(true);
      await page.mouse.click(point.x, point.y);
      const dock = page.getByTestId('di-selected-agent');
      await expectDockNamesMission(
        page,
        e.missionRef,
        executors.map((x) => x.missionRef),
        5_000,
      );
      // The world: the label appears on THAT character, on neither lookalike.
      await expect(
        page.locator(`[data-testid="agent-overlay"][data-agent-id="${e.characterId}"]`),
      ).toBeVisible();
      for (const other of executors.filter((x) => x !== e))
        await expect(
          page.locator(`[data-testid="agent-overlay"][data-agent-id="${other.characterId}"]`),
        ).toHaveCount(0);
      // The inspector, from the dock: the exact complete member ref, never a lookalike's.
      await dock.getByTestId('di-open-inspector-selection').click();
      const member = page.getByTestId('di-inspector-member');
      await expect(member).toContainText(e.memberRef);
      for (const other of executors.filter((x) => x !== e))
        await expect(member).not.toContainText(other.memberRef);
      await page.keyboard.press('Escape');
      await expect(page.getByRole('dialog')).toHaveCount(0);
    }
    // ...and by KEYBOARD through Find: the THIRD result selects the third record.
    await page.getByTestId('di-find-input').fill('executor1');
    await page.getByTestId('di-find-input').press('ArrowDown');
    await page.keyboard.press('ArrowDown');
    await page.keyboard.press('ArrowDown');
    await page.keyboard.press('Enter');
    await expectDockNamesMission(
      page,
      executors[2].missionRef,
      executors.map((x) => x.missionRef),
      5_000,
    );
    await expect(
      page.locator(`[data-testid="agent-overlay"][data-agent-id="${executors[2].characterId}"]`),
    ).toBeVisible();

    // ── BLOCKED ─────────────────────────────────────────────────────────────────────────────────
    // A live, confirmed member reports blocked: the card reads Blocked (word and shape) and says why.
    expect(await chip(page, cases.refs.blocked)).toEqual({
      status: 'blocked',
      word: 'Blocked',
      glyph: '▲',
    });
    await expect(
      card(page, cases.refs.blocked).getByTestId('di-op-status-sentence').first(),
    ).toHaveText("1 live team member terminal(s) report blocked (the terminal's own report).");
    await inMissionInspector(page, cases.refs.blocked, async (dialog) => {
      await dialog.getByTestId('di-inspector-provenance').locator('summary').click();
      const derived = dialog
        .getByTestId('di-inspector-provenance')
        .locator('.di-op-row')
        .filter({ hasText: 'status derived from' });
      const lead = `${cases.refs.blocked}#lead1`;
      await expect(derived).toContainText(`members[${lead}].activity = "blocked"`);
      await expect(derived).toContainText(`members[${lead}].live = true`);
      await expect(derived).toContainText(`members[${lead}].classification = "present"`);
    });
    // A REPLACED (not live, not this member) reviewer2 reports blocked: it must NOT promote Blocked;
    // the observation is kept as a last-known detail, labelled as the terminal's own report.
    expect(await chip(page, cases.refs.replacedBlocked)).toEqual({
      status: 'running',
      word: 'Running',
      glyph: '●',
    });
    await expect(
      card(page, cases.refs.replacedBlocked).getByTestId('di-mission-team'),
    ).toContainText('1 of 2 members observed running');
    await expect(
      card(page, cases.refs.replacedBlocked).getByTestId('di-op-terminal-note'),
    ).toHaveText(
      "reviewer2: its terminal last reported blocked (the terminal's own report; last known, not current; not live).",
    );
    await inMissionInspector(page, cases.refs.replacedBlocked, async (dialog) => {
      await dialog.getByTestId('di-inspector-provenance').locator('summary').click();
      const derived = dialog
        .getByTestId('di-inspector-provenance')
        .locator('.di-op-row')
        .filter({ hasText: 'status derived from' });
      await expect(derived).not.toContainText('activity = "blocked"');
    });
  } finally {
    await office.stop();
  }
});

test('built app, contract v2: a reported failure and duplicate names inside one team (FIXTURE, not live)', async ({
  page,
}) => {
  const cases = buildCasesV2();
  const office = await startSandboxOffice({
    prefix: 'di-cases-v2',
    envelopeAt: cases.envelopeAt,
    contractVersion: 2,
  });
  try {
    await page.setViewportSize({ width: 1440, height: 900 });
    await openOffice(page, office);
    await expect(card(page, cases.refs.failed)).toBeVisible({ timeout: 30_000 });

    // ── FAILED: the source REPORTS completion without success (a contract-v2 Mission State field) ──
    expect(await chip(page, cases.refs.failed)).toEqual({
      status: 'failed',
      word: 'Failed',
      glyph: '✕',
    });
    await expect(
      card(page, cases.refs.failed).getByTestId('di-op-status-sentence').first(),
    ).toContainText('reported by the source, not verified');
    await inMissionInspector(page, cases.refs.failed, async (dialog) => {
      await dialog.getByTestId('di-inspector-provenance').locator('summary').click();
      const derived = dialog
        .getByTestId('di-inspector-provenance')
        .locator('.di-op-row')
        .filter({ hasText: 'status derived from' });
      await expect(derived).toContainText('v2.missionState.completion.reportedComplete = true');
      await expect(derived).toContainText('v2.missionState.completion.reportedSuccess = false');
      // The Mission State's own words stay separate from the verified outcome.
      await expect(dialog.getByTestId('di-inspector-checkpoints')).toBeVisible();
    });

    // ── DUPLICATE NAMES inside ONE team: a direct and a child executor1 (and lead1) ──────────────
    const stored = await storedState<WireState>(page);
    const team = stored.missions.missions.find((m) => m.missionRef === cases.refs.duplicates)!
      .members.members;
    const executors = team.filter((m) => m.logical === 'executor1');
    expect(executors).toHaveLength(2);
    expect(new Set(executors.map((e) => e.memberRef)).size).toBe(2);
    expect(new Set(executors.map((e) => e.chain)).size).toBe(2);
    const missionCard = card(page, cases.refs.duplicates);
    await missionCard.getByTestId('di-mission-roster').locator('summary').click();
    const rows = missionCard
      .getByTestId('di-member-row')
      .filter({ hasText: memberRowName('executor1') });
    await expect(rows).toHaveCount(2);
    expect(new Set(await rows.allInnerTexts()).size).toBe(2); // told apart on the face (direct / child)
    for (let i = 0; i < 2; i++) {
      await rows.nth(i).click();
      await page
        .getByTestId('di-selected-agent')
        .getByTestId('di-open-inspector-selection')
        .click();
      // The full record is under "Everything recorded": opened as a person does, then read.
      await page.getByTestId('di-inspector-record').locator('summary').click();
      const member = page.getByTestId('di-inspector-member');
      // Whichever row was chosen, the inspector names exactly ONE of the two complete refs.
      const text = (await member.innerText()).replace(/\s+/g, ' ');
      const named = executors.filter((e) => text.includes(e.memberRef));
      expect(named).toHaveLength(1);
      await page.keyboard.press('Escape');
      await expect(page.getByRole('dialog')).toHaveCount(0);
    }
  } finally {
    await office.stop();
  }
});
