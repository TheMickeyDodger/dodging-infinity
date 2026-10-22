/**
 * How the DI e2e names ONE record now that the first layer shows SHORT Mission refs (the complete
 * source-qualified ref is carried in the element's `title`). The same adaptation as the webview
 * diInspector tests, with BOTH conditions kept:
 *  - the dock's Mission line carries the COMPLETE ref as its title, compared EXACTLY; and
 *  - its visible short identity is the one that record's OWN card shows (the card found by its
 *    complete ref, exactly), while no other given Mission's short identity is on the dock.
 * A member row is found by its first-layer name at the START of its text ("logical · role …").
 */
import { expect, type Page } from '@playwright/test';

/** The short ref the product shows for this Mission, read from that Mission's own card. */
export const visibleMissionRef = (page: Page, missionRef: string): Promise<string> =>
  page
    .locator(`[data-mission-ref="${missionRef}"]`)
    .getByTestId('di-mission-ref')
    .locator('.di-op-ref')
    .innerText();

/** The Selected dock names exactly this Mission: complete ref (title) and short ref (face). */
export async function expectDockNamesMission(
  page: Page,
  missionRef: string,
  others: readonly string[],
  timeout?: number,
): Promise<void> {
  const dock = page.getByTestId('di-selected-agent');
  const line = dock.getByTestId('di-selected-member-mission');
  await expect(line).toHaveAttribute('title', missionRef, timeout ? { timeout } : undefined);
  await expect(line).toContainText(await visibleMissionRef(page, missionRef));
  for (const other of others.filter((x) => x !== missionRef))
    await expect(dock).not.toContainText(await visibleMissionRef(page, other));
}

/** The same two conditions plus the member's name, as an in-page predicate for timed waits. */
export function dockNamesPredicate(
  missionRef: string,
  visibleRef: string,
  logical: string,
): string {
  return `() => { const d = document.querySelector('[data-testid="di-selected-member"]'); const t = d?.innerText ?? ''; return d?.querySelector('[data-testid="di-selected-member-mission"]')?.getAttribute('title') === ${JSON.stringify(missionRef)} && t.includes(${JSON.stringify(visibleRef)}) && t.includes(${JSON.stringify(logical)}); }`;
}

/** A member row by its first-layer name: its text STARTS with "logical · ". */
export const memberRowName = (logical: string): RegExp =>
  new RegExp(`^${logical.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')} · `);
