/**
 * R12 (the panel-requests half), on the PAGE: what the Reception draws for its "Earlier" section from the request panel's overview.
 *
 * The derivation itself (window, ordering, wording, the six axes) is pinned in diReceptionEarlier.test.ts. This file pins what reaches the screen: the list names what it is in its heading, its limits are VISIBLE (not behind a
 * click), Show is offered only when there is something to show, a panel that is not read leaves no heading and no count and says exactly why, and the sample (fixture) page keeps its own labelled wording. Server-rendered markup
 * (no browser): clicking Show over the office-derived cards is NOT driven here, so that click is a named gap; the specimen's Show click is driven in diScanPassB.test.ts.
 */
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';

import type { DiOfficeState } from '../../core/src/messages.ts';
import type { MrOverview, MrSummary } from '../src/components/diMissionRequestModel.ts';
import { earlierFromPanel } from '../src/components/diReceptionEarlier.ts';
import { buildInbox, type PanelSource } from '../src/components/diReceptionItems.ts';
import { scanRowsFromOffice } from '../src/components/diScanModel.ts';
import {
  DI_EARLIER_NOT_READ,
  DI_RECEPTION_SPECIMEN,
  type DiScanReception,
  receptionFromInbox,
} from '../src/components/diScanPagesModel.ts';
import { DiScanPanel } from '../src/components/DiScanPanel.tsx';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const fixture = JSON.parse(
  fs.readFileSync(path.join(root, 'test/fixtures/di-office-state-evidence-historical-v2.FIXTURE.json'), 'utf-8'),
) as { states: Record<string, DiOfficeState> };
const state = fixture.states.live!;
const NOW = 1_800_100_000;

const request = (n: number, over: Partial<MrSummary> = {}): MrSummary => ({
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
});
const read = (requests: MrSummary[], truncated = false): PanelSource => ({
  kind: 'read',
  overview: { serverNow: NOW, capabilities: {} as MrOverview['capabilities'], requests, requestsTruncated: truncated, conversations: [] },
});
const pageFor = (source: PanelSource): DiScanReception => {
  const model = buildInbox(state.reception, { kind: 'not_provided' }, state.missions.missions, new Set());
  return receptionFromInbox(model, state.reception, scanRowsFromOffice(state), false, earlierFromPanel(source));
};
const render = (reception: DiScanReception) =>
  renderToStaticMarkup(
    createElement(DiScanPanel, { rows: scanRowsFromOffice(state), initialView: 'reception', onMinimize: () => undefined, reception, specimen: reception.source === 'fixture' }),
  );
const earlierSection = (html: string) => /<section[^>]*data-testid="di-reception-earlier"[\s\S]*?<\/section>/.exec(html)?.[0] ?? '';
const text = (markup: string) => markup.replace(/<[^>]+>/g, ' ').replace(/&#x27;/g, "'").replace(/\s+/g, ' ').trim();
const tags = (html: string, id: string) => [...html.matchAll(new RegExp(`<[a-z]+[^>]*data-testid="${id}"[^>]*>`, 'g'))].map((m) => m[0]);

describe('the office page with the panel read', () => {
  const html = render(pageFor(read([request(1, { attestedAt: NOW - 600 }), request(2, { cancelledAt: NOW - 90 }), request(3)])));
  const section = earlierSection(html);
  it('the heading names the list: panel requests with a recorded approval or withdrawal, in the last 24 hours, and the count', () => {
    expect(text(tags(section, 'di-reception-earlier-heading').length === 1 ? section : '')).toContain(
      'Request panel, last 24 hours: 2 requests have an approval or withdrawal recorded',
    );
    expect(section).not.toContain('Earlier today');
  });
  it('every limit on the list is VISIBLE on the page, not behind a click', () => {
    const notes = text(section);
    expect(tags(section, 'di-reception-earlier-note')).toHaveLength(4);
    expect(notes).toContain('Only request panel records are read here');
    expect(notes).toContain('not who acted and not that the action took effect');
    expect(notes).toContain("the request panel's own clock when it was last read");
    expect(notes).toContain('a request this window could not read is not counted');
  });
  it('Show is offered, and the cards stay hidden until it is used', () => {
    expect(tags(section, 'di-reception-earlier-show')).toHaveLength(1);
    expect(section).not.toContain('data-testid="di-reception-earlier-list"');
  });
  it('nothing in the section is attributed to the viewer or called handled', () => {
    expect(text(section)).not.toMatch(/\b(you|your|handled)\b/i);
  });
});

describe('cut off at the panel\'s limit', () => {
  it('says AT LEAST in the heading and shows the cut-off note on the page', () => {
    const section = earlierSection(render(pageFor(read([request(1, { attestedAt: NOW - 600 })], true))));
    expect(text(section)).toContain('at least 1 request has an approval or withdrawal recorded');
    expect(tags(section, 'di-reception-earlier-note')).toHaveLength(5);
    expect(text(section)).toContain('older requests are not read');
  });
});

describe('nothing recorded in the window', () => {
  const section = earlierSection(render(pageFor(read([request(1), request(2, { attestedAt: NOW - 90_000 })]))));
  it('the heading speaks about the LISTED requests only, and there is no Show for an empty list', () => {
    expect(text(section)).toContain('none of the requests listed has an approval or withdrawal recorded');
    expect(tags(section, 'di-reception-earlier-show')).toHaveLength(0);
  });
});

describe('the panel is NOT read', () => {
  const cases: Array<[string, PanelSource]> = [
    ['not_provided', { kind: 'not_provided' }],
    ['not_available', { kind: 'not_available', reason: 'the adapter is off' }],
    ['not_vouched', { kind: 'not_vouched', reason: 'the connection is not vouched for' }],
    ['not_read', { kind: 'not_read' }],
  ];
  for (const [name, source] of cases)
    it(`${name}: no heading, no count, no Show; the exact cause is on the page`, () => {
      const reception = pageFor(source);
      expect(reception.earlier).toBeNull();
      const section = earlierSection(render(reception));
      expect(tags(section, 'di-reception-earlier-heading')).toHaveLength(0);
      expect(tags(section, 'di-reception-earlier-show')).toHaveLength(0);
      expect(text(section)).toBe(earlierFromPanel(source).unread);
      expect(text(section)).not.toContain(DI_EARLIER_NOT_READ);
    });
  it('without being handed the panel at all, the page keeps the layout-preview wording and counts nothing', () => {
    const model = buildInbox(state.reception, { kind: 'not_provided' }, state.missions.missions, new Set());
    const bare = receptionFromInbox(model, state.reception, scanRowsFromOffice(state));
    expect(bare.earlier).toBeNull();
    expect(bare.earlierUnread).toBeUndefined();
    expect(text(earlierSection(render(bare)))).toBe(DI_EARLIER_NOT_READ);
  });
});

describe('the sample (fixture) page keeps its own labelled wording', () => {
  it('Earlier today: 3 handled, with Show, on the specimen only', () => {
    const section = earlierSection(render(DI_RECEPTION_SPECIMEN));
    expect(text(section)).toContain('Earlier today: 3 handled');
    expect(tags(section, 'di-reception-earlier-show')).toHaveLength(1);
  });
});
