/**
 * REAL clicks across the visible world, for the rule that nothing in the world opens Reception, neither the inbox dialog
 * nor the scan face's full Reception page (D-10: Reception is left-rail navigation only). FIXTURE / TEST ONLY.
 *
 * Every point of a grid inside `area` where the world canvas itself is on top (never a control floating over it) is clicked
 * with the real mouse. A watcher records whether the inbox EVER appeared, so a click that opened it and a later click that
 * closed it (its backdrop closes on a click) cannot go unseen. After each click the selection and the camera follow are
 * cleared, so the world does not pan away under the grid. The grid is fine enough to land on the reception desk and its
 * receptionist; that it DOES reach them is proved by the mutation check recorded with the tests that use it (the old
 * desk opener restored makes the rule fail), not by this comment.
 *
 * Pass C (AC-4.4-26): on the scan face a click on a Mission's agent now OPENS that Mission's details, which cover the
 * world. Without care every later click would land on that screen, not the world, and the probe would stay green while it
 * had stopped probing. So each point is checked to be the WORLD at the moment it is clicked (`worldClicks`), and a click
 * that opened a Mission is counted (`missionsOpened`) and undone through the screen's own Back before the next click.
 * Callers assert worldClicks === clicks.
 *
 * WHAT A CLICK DID is decided from the app's own state, never from a DOM sample at an arbitrary instant (`opensMission`): a click on
 * a Mission's agent selects it at once and the Mission's stage mounts a moment LATER, so reading the DOM right after the click can
 * miss a stage that is about to appear (measured: 1 click in 39; the stage then covered the next point and the sweep reported a
 * point that was not the world). With `opensMission` given, a click that selected a Mission agent WAITS for that stage, which MUST
 * appear (a Mission that does not open is a failure, never a pass), then goes Back; any other click opens nothing. Without it the
 * old immediate read stays, so callers that do not pass it behave exactly as before.
 */
import type { Page } from 'playwright-core';

export interface WorldArea {
  left: number;
  top: number;
  right: number;
  bottom: number;
}

export async function clickAcrossWorld(
  page: Page,
  area: WorldArea,
  step: number,
  options?: { opensMission?: (agentId: number | null) => boolean },
): Promise<{
  clicks: number;
  worldClicks: number;
  missionsOpened: number;
  firstMissionAt: number | null;
  inboxOpened: boolean;
}> {
  const points = await page.evaluate(
    ([a, s]) => {
      const out: Array<[number, number]> = [];
      for (let y = a.top + s / 2; y < a.bottom; y += s)
        for (let x = a.left + s / 2; x < a.right; x += s)
          if (document.elementFromPoint(x, y)?.tagName === 'CANVAS') out.push([x, y]);
      return out;
    },
    [area, step] as const,
  );
  await page.evaluate(() => {
    const w = window as unknown as { __diInboxSeen?: boolean };
    // either form of Reception: the inbox dialog, or the scan face's full page (pass B)
    const seen = () =>
      document.querySelector(
        '[data-testid="di-reception-inbox"], [data-testid="di-reception-page"]',
      ) !== null;
    w.__diInboxSeen = seen();
    new MutationObserver(() => {
      if (seen()) w.__diInboxSeen = true;
    }).observe(document.body, { childList: true, subtree: true });
  });
  let worldClicks = 0;
  let missionsOpened = 0;
  /** 1-based index of the first click that opened a Mission: the old probe left the world there. */
  let firstMissionAt: number | null = null;
  let index = 0;
  for (const [x, y] of points) {
    index++;
    // the point must be the world NOW: a label drawn for the agent just cleared goes on the next frame, so wait for that
    // (bounded); a point still covered after that is counted as NOT probing the world, and the caller's assert fails
    const onWorld = await page
      .waitForFunction(
        ([xx, yy]) => document.elementFromPoint(xx, yy)?.tagName === 'CANVAS',
        [x, y] as const,
        {
          timeout: 1_000,
        },
      )
      .then(
        () => true,
        () => false,
      );
    if (onWorld) worldClicks++;
    await page.mouse.click(x, y);
    // what the click did: decided from the app's state when the caller says which agents own a Mission (see the header comment)
    let expectsMission = false;
    if (options?.opensMission !== undefined) {
      const hit = await page.evaluate(
        () =>
          (window as unknown as { __diHarnessOffice?: { selectedAgentId: number | null } }).__diHarnessOffice
            ?.selectedAgentId ?? null,
      );
      expectsMission = options.opensMission(hit);
      if (expectsMission)
        await page.waitForSelector('[data-testid="di-scan-stage"]', { timeout: 5_000 }).catch(() => {
          throw new Error(`the click at (${x}, ${y}) selected Mission agent ${hit} but its Mission did not open within 5 s`);
        });
    }
    // a Mission opened from the world (scan face): count it and go back Home the way a person would
    if (expectsMission || (await page.locator('[data-testid="di-scan-stage"]').count()) > 0) {
      missionsOpened++;
      firstMissionAt ??= index;
      await page.click('[data-testid="di-scan-back"]');
      await page.waitForFunction(
        () => document.querySelector('[data-testid="di-scan-stage"]') === null,
      );
    }
    await page.evaluate(() => {
      const office = (
        window as unknown as {
          __diHarnessOffice?: { selectedAgentId: number | null; cameraFollowId: number | null };
        }
      ).__diHarnessOffice;
      if (office) {
        office.selectedAgentId = null;
        office.cameraFollowId = null;
      }
    });
  }
  const inboxOpened = await page.evaluate(
    () => (window as unknown as { __diInboxSeen?: boolean }).__diInboxSeen === true,
  );
  return { clicks: points.length, worldClicks, missionsOpened, firstMissionAt, inboxOpened };
}
