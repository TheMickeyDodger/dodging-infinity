/**
 * The Reception's "Earlier" list (R12, the panel-requests half): request-panel requests with a RECORDED approval attestation or
 * withdrawal inside the 24 hours that end at the PANEL'S OWN clock. It reads the request panel's overview and nothing else.
 *
 * Every sentence here is a claim about its contents, so each dimension on which the list could claim more than its data is
 * answered from what the overview actually holds:
 *  - WHAT     only request-panel records. Delivery decisions, questions and information items are not read, and the list says so.
 *  - WHOSE    a recorded time says that a record was written and when. It does not say who acted, so no sentence names an actor.
 *  - MEANING  `attested_at` is stamped when the attestation record is first written, BEFORE Mission Core applies it, so the card
 *             says "an approval attestation was recorded", never that the approval took effect, and the list never says "handled".
 *  - HOW MANY the count is what this window read from a list the panel cuts off at its limit: "at least" when it was cut off, and a
 *             request this window could not read is not counted. No total is acquired or implied.
 *  - HOW RECENT the window ends at `server_now`, the panel's clock when it was last read; this window's clock and time zone are
 *             never consulted (the function takes no clock).
 *  - HOW COMPLETE not a record of the day's activity. When the panel is not read at all, the list is null and the cause is stated.
 */
import type { MrOverview, MrSummary } from './diMissionRequestModel.js';
import type { PanelSource } from './diReceptionItems.js';
import { PANEL_SHORT_LABEL } from './diReceptionItems.js';
import type { DiReceptionCard, DiScanEarlierRead } from './diScanPagesModel.js';
import { DI_REPOS_NOT_RECORDED } from './diScanPagesModel.js';

/** The window: the 24 hours ending at the panel's clock when it was last read. */
export const EARLIER_WINDOW_S = 24 * 60 * 60;

/** Elapsed seconds in words ("under a minute", "12 min", "3 h 5 min"), measured on the panel's clock. */
function elapsedWords(seconds: number): string {
  if (seconds < 60) return 'under a minute';
  if (seconds < 3600) return `${Math.floor(seconds / 60)} min`;
  return `${Math.floor(seconds / 3600)} h ${Math.floor((seconds % 3600) / 60)} min`;
}

/** A recorded time inside the window, or null. A time after the panel's own clock is not "earlier", so it is outside. */
function inWindow(time: number | null, now: number): number | null {
  return time !== null && time >= now - EARLIER_WINDOW_S && time <= now ? time : null;
}

interface Recorded {
  request: MrSummary;
  approval: number | null;
  withdrawal: number | null;
  latest: number;
}

function recordedIn(overview: MrOverview): Recorded[] {
  const out: Recorded[] = [];
  for (const request of overview.requests) {
    const approval = inWindow(request.attestedAt, overview.serverNow);
    const withdrawal = inWindow(request.cancelledAt, overview.serverNow);
    if (approval === null && withdrawal === null) continue;
    out.push({ request, approval, withdrawal, latest: Math.max(approval ?? 0, withdrawal ?? 0) });
  }
  // Newest record first; the request ref only breaks a tie so the order is stable.
  return out.sort((a, b) => b.latest - a.latest || a.request.requestRef.localeCompare(b.request.requestRef));
}

function cardOf(r: Recorded, now: number): DiReceptionCard {
  const need =
    r.approval !== null && r.withdrawal !== null
      ? 'An approval attestation and a withdrawal were recorded.'
      : r.approval !== null
        ? 'An approval attestation was recorded.'
        : 'A withdrawal was recorded.';
  const when: string[] = [];
  if (r.approval !== null)
    when.push(`approval recorded ${elapsedWords(now - r.approval)} before the panel was read`);
  if (r.withdrawal !== null)
    when.push(`withdrawal recorded ${elapsedWords(now - r.withdrawal)} before the panel was read`);
  const stage = r.request.label.trim() === '' ? [] : [`now: ${r.request.label}`];
  return {
    key: `panel-earlier:${r.request.requestRef}`,
    kind: 'info',
    missionRef: null,
    title: r.request.objective.trim() === '' ? 'Mission request' : r.request.objective,
    tag: { label: 'Recorded', tone: 'idle' },
    need,
    meta: [DI_REPOS_NOT_RECORDED, `from the ${PANEL_SHORT_LABEL.toLowerCase()}`, ...when, ...stage].join(' · '),
    note: '',
    suggestions: [],
    request: null,
  };
}

function headingOf(count: number, floor: boolean): string {
  const lead = `${PANEL_SHORT_LABEL}, last 24 hours:`;
  const what = 'an approval or withdrawal recorded';
  if (count === 0)
    return `${lead} none of the ${floor ? 'newest ' : ''}requests listed has ${what}`;
  return `${lead} ${floor ? 'at least ' : ''}${count} ${count === 1 ? 'request has' : 'requests have'} ${what}`;
}

/** What the request panel's overview gives the "Earlier" list: the list, or null with the exact cause. */
export function earlierFromPanel(source: PanelSource): DiScanEarlierRead {
  const unread = (cause: string): DiScanEarlierRead => ({
    earlier: null,
    unread: `Earlier panel activity is not shown: ${cause}. Nothing is listed or counted in its place.`,
  });
  switch (source.kind) {
    case 'not_provided':
      return unread('this window has no request panel');
    case 'not_available':
      return unread(`the request panel is not available (${source.reason})`);
    case 'not_vouched':
      return unread(`the request panel could not be vouched for (${source.reason})`);
    case 'not_read':
      return unread('the request panel has not been read yet');
    case 'read': {
      const { overview } = source;
      const recorded = recordedIn(overview);
      const floor = overview.requestsTruncated;
      return {
        unread: '',
        earlier: {
          heading: headingOf(recorded.length, floor),
          count: recorded.length,
          notes: [
            "Only request panel records are read here. Delivery decisions, questions and information items are not listed in this list, and it is not a complete record of the day's activity.",
            'A recorded time marks when a record was written, not who acted and not that the action took effect.',
            "The 24 hours end at the request panel's own clock when it was last read, not at this window's clock; the list is as of that read.",
            ...(floor
              ? ["The panel's list was cut off at its limit, so older requests are not read and the count is at least what is listed."]
              : []),
            'Only requests the panel listed are counted; a request this window could not read is not counted.',
          ],
          items: recorded.map((r) => cardOf(r, overview.serverNow)),
        },
      };
    }
  }
}
