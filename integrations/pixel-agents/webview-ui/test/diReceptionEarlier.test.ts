/**
 * R12 (the panel-requests half): the Reception's "Earlier" list, derived from the request panel's OVERVIEW and from nothing else.
 *
 * Every label and sentence is a claim about its contents, so this file enumerates the axes on which the list could claim more than its data and pins each:
 *   WHAT      the items are request-panel requests with a RECORDED approval attestation or withdrawal, not handled items in general (no delivery decisions, questions or information items);
 *   WHOSE     the times say that a record was written and when, not who acted: no second person, no "you approved";
 *   MEANING   `attested_at` is stamped when the attestation record is first written, before Mission Core applies it: it does NOT say the approval took effect, and nothing here says "handled";
 *   HOW MANY  the count is what this window read from a list the panel cuts off at its limit: "at least" when it is cut off, and a request this window could not read is not counted. No total is invented;
 *   HOW RECENT the 24 hours end at the PANEL'S OWN clock when it was last read (`server_now`), never this window's clock or time zone;
 *   HOW COMPLETE it is not a complete record of the day's activity, and when the panel is not read at all the page says exactly why and claims nothing.
 */
import { afterEach, describe, expect, it, vi } from 'vitest';

import type { MrOverview, MrSummary } from '../src/components/diMissionRequestModel.ts';
import { EARLIER_WINDOW_S, earlierFromPanel } from '../src/components/diReceptionEarlier.ts';
import type { PanelSource } from '../src/components/diReceptionItems.ts';

const NOW = 1_800_100_000;
const DAY = 86_400;

function request(n: number, over: Partial<MrSummary> = {}): MrSummary {
  return {
    requestRef: `lr-${String(n).padStart(32, '0')}`,
    missionId: `mn-${String(n).padStart(32, '0')}`,
    revision: 1,
    createdAt: NOW - 50_000,
    objective: `Objective ${n}`,
    digest: 'd'.repeat(64),
    stage: 'approved',
    label: `Stage label ${n}`,
    terminal: false,
    actions: [],
    notice: null,
    attestedAt: null,
    cancelledAt: null,
    ...over,
  };
}
const overviewOf = (requests: MrSummary[], truncated = false): MrOverview => ({
  serverNow: NOW,
  capabilities: {} as MrOverview['capabilities'],
  requests,
  requestsTruncated: truncated,
  conversations: [],
});
const read = (requests: MrSummary[], truncated = false): PanelSource => ({ kind: 'read', overview: overviewOf(requests, truncated) });
const earlierOf = (requests: MrSummary[], truncated = false) => earlierFromPanel(read(requests, truncated)).earlier!;
const allText = (e: NonNullable<ReturnType<typeof earlierFromPanel>['earlier']>): string =>
  [e.heading, ...e.notes, ...e.items.flatMap((c) => [c.title, c.need, c.meta, c.note, c.tag.label])].join('\n');

afterEach(() => vi.useRealTimers());

describe('WHAT and WHICH: only requests with a recorded time inside the window, newest record first', () => {
  it('lists a request with a recorded approval or withdrawal inside the window, and nothing else', () => {
    const e = earlierOf([
      request(1, { attestedAt: NOW - 600 }),
      request(2), // nothing recorded
      request(3, { cancelledAt: NOW - 60 }),
      request(4, { attestedAt: NOW - DAY - 1 }), // just outside
      request(5, { attestedAt: NOW + 1 }), // after the panel's own clock: not "earlier"
    ]);
    expect(e.items.map((c) => c.title)).toEqual(['Objective 3', 'Objective 1']);
    expect(e.count).toBe(2);
  });
  it('the window is the 24 hours ending at the panel clock: the first instant is inside, one second before is outside', () => {
    expect(EARLIER_WINDOW_S).toBe(DAY);
    expect(earlierOf([request(1, { attestedAt: NOW - DAY })]).count).toBe(1);
    expect(earlierOf([request(1, { attestedAt: NOW - DAY - 1 })]).count).toBe(0);
    expect(earlierOf([request(1, { attestedAt: NOW })]).count).toBe(1);
  });
  it('ordered by the LATEST recorded time of each request, newest first; a request with both times is one card', () => {
    const e = earlierOf([
      request(1, { attestedAt: NOW - 3000 }),
      request(2, { attestedAt: NOW - 5000, cancelledAt: NOW - 100 }),
      request(3, { attestedAt: NOW - 1000 }),
    ]);
    expect(e.items.map((c) => c.title)).toEqual(['Objective 2', 'Objective 3', 'Objective 1']);
    expect(e.count).toBe(3);
    expect(e.items[0]!.need).toBe('An approval attestation and a withdrawal were recorded.');
  });
  it('a card says what was recorded in the passive, never who did it, and never that it was handled', () => {
    const e = earlierOf([request(1, { attestedAt: NOW - 600 }), request(2, { cancelledAt: NOW - 700 })]);
    const [approval, withdrawal] = e.items;
    expect(approval!.need).toBe('An approval attestation was recorded.');
    expect(withdrawal!.need).toBe('A withdrawal was recorded.');
  });
});

describe('the cards are read-only records of the panel, and say where the time comes from', () => {
  const e = earlierOf([request(1, { attestedAt: NOW - 750 })]);
  const [card] = e.items;
  it('information-kind, not tied to a Mission or a request workspace route (no control can be drawn on it)', () => {
    expect(card!.kind).toBe('info');
    expect(card!.missionRef).toBeNull();
    expect(card!.request).toBeNull();
    expect(card!.suggestions).toEqual([]);
    expect(card!.note).toBe('');
  });
  it('the time is measured to the PANEL clock, in words, and names the request panel and the stage it now shows', () => {
    expect(card!.meta).toContain('from the request panel');
    expect(card!.meta).toContain('recorded 12 min before the panel was read');
    expect(card!.meta).toContain('now: Stage label 1');
  });
  it('the tag says Recorded, never Handled', () => {
    expect(card!.tag.label).toBe('Recorded');
  });
});

describe('WHOSE, MEANING: no attribution, no completion, no handled claim, anywhere in the list', () => {
  const texts = [
    allText(earlierOf([request(1, { attestedAt: NOW - 600 }), request(2, { cancelledAt: NOW - 60 })])),
    allText(earlierOf([request(1, { attestedAt: NOW - 600 })], true)),
    allText(earlierOf([request(1)])),
    allText(earlierOf([])),
  ];
  it('no second person and no named actor', () => {
    for (const t of texts) {
      expect(t).not.toMatch(/\byou(r|rs|rself)?\b/i);
      expect(t).not.toMatch(/\b(operator|user|human|person|approver|owner)\b\s+(approved|attested|cancelled|canceled|withdrew|decided)/i);
      expect(t).not.toMatch(/\b(approved|cancelled|canceled|withdrawn|decided) by\b/i);
    }
  });
  it('never calls anything handled, done, settled, resolved or completed, and never "Earlier today"', () => {
    for (const t of texts) {
      expect(t).not.toMatch(/\b(handled|done|settled|resolved|completed|finished)\b/i);
      expect(t).not.toMatch(/earlier today/i);
    }
  });
  it('says what a recorded time is and is not, once, in the notes', () => {
    const notes = earlierOf([request(1, { attestedAt: NOW - 600 })]).notes.join(' ');
    expect(notes).toContain('marks when a record was written');
    expect(notes).toContain('not who acted');
    expect(notes).toContain('not that the action took effect');
  });
});

describe('HOW MANY, HOW COMPLETE: the bound is stated and no total is invented', () => {
  it('when the panel cut its list off, the heading says AT LEAST and a note says the older requests were not read', () => {
    const e = earlierOf([request(1, { attestedAt: NOW - 600 }), request(2, { attestedAt: NOW - 700 })], true);
    expect(e.heading).toMatch(/^Request panel, last 24 hours: at least 2 requests have an approval or withdrawal recorded$/);
    expect(e.notes.join(' ')).toContain('cut off at its limit');
    expect(e.notes.join(' ')).toContain('older requests are not read');
  });
  it('when the list was not cut off, the count is not a floor, and is still only of the requests listed', () => {
    const e = earlierOf([request(1, { attestedAt: NOW - 600 })]);
    expect(e.heading).toBe('Request panel, last 24 hours: 1 request has an approval or withdrawal recorded');
    expect(e.notes.join(' ')).not.toContain('cut off');
    expect(e.notes.join(' ')).toContain('Only requests the panel listed are counted');
  });
  it('a request this window could not read is not counted, and the note says so in both cases', () => {
    for (const truncated of [false, true])
      expect(earlierOf([request(1, { attestedAt: NOW - 600 })], truncated).notes.join(' ')).toContain('a request this window could not read is not counted');
  });
  it('no number other than the count appears as a total ("of N", "20", "total")', () => {
    const t = allText(earlierOf([request(1, { attestedAt: NOW - 600 })], true));
    expect(t).not.toMatch(/\bof \d+\b/);
    expect(t).not.toMatch(/\btotal\b/i);
    expect(t).not.toMatch(/\b20\b/);
  });
  it('it is not a complete record of activity, and says what else is not here', () => {
    const notes = earlierOf([request(1, { attestedAt: NOW - 600 })]).notes.join(' ');
    expect(notes).toContain('Only request panel records are read here');
    expect(notes).toContain('Delivery decisions, questions and information items are not listed');
  });
  it('with nothing recorded in the window it says so ABOUT THE LISTED REQUESTS, not about the day, and offers no cards', () => {
    const e = earlierOf([request(1), request(2, { attestedAt: NOW - DAY - 5 })]);
    expect(e.count).toBe(0);
    expect(e.items).toEqual([]);
    expect(e.heading).toBe('Request panel, last 24 hours: none of the requests listed has an approval or withdrawal recorded');
    const cut = earlierOf([request(1)], true);
    expect(cut.heading).toBe('Request panel, last 24 hours: none of the newest requests listed has an approval or withdrawal recorded');
  });
});

describe('HOW RECENT: the panel clock, not this window\'s', () => {
  it('names the clock the window ends at and that the data is as of the last read', () => {
    const notes = earlierOf([request(1, { attestedAt: NOW - 600 })]).notes.join(' ');
    expect(notes).toContain("The 24 hours end at the request panel's own clock when it was last read, not at this window's clock");
    expect(notes).toContain('as of that read');
  });
  it('the result does not depend on this window\'s clock or time zone', () => {
    const requests = [request(1, { attestedAt: NOW - 600 }), request(2, { cancelledAt: NOW - 90_000 })];
    vi.useFakeTimers();
    vi.setSystemTime(new Date('2001-01-01T00:00:00Z'));
    const a = JSON.stringify(earlierFromPanel(read(requests)));
    vi.setSystemTime(new Date('2090-12-31T23:59:59Z'));
    const b = JSON.stringify(earlierFromPanel(read(requests)));
    expect(a).toBe(b);
  });
});

describe('when the panel is NOT read: null, the exact cause, and no claim', () => {
  const cases: Array<[string, PanelSource, string]> = [
    ['not_provided', { kind: 'not_provided' }, 'this window has no request panel'],
    ['not_available', { kind: 'not_available', reason: 'the adapter is off' }, 'the request panel is not available (the adapter is off)'],
    ['not_vouched', { kind: 'not_vouched', reason: 'the connection is not vouched for' }, 'the request panel could not be vouched for (the connection is not vouched for)'],
    ['not_read', { kind: 'not_read' }, 'the request panel has not been read yet'],
  ];
  for (const [name, source, cause] of cases)
    it(`${name}: earlier is null and the page is told the cause`, () => {
      const r = earlierFromPanel(source);
      expect(r.earlier).toBeNull();
      expect(r.unread).toBe(`Earlier panel activity is not shown: ${cause}. Nothing is listed or counted in its place.`);
      expect(r.unread).not.toMatch(/\b(handled|you|your|today)\b/i);
    });
});
