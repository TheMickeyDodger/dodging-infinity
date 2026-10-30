/**
 * The operational Reception: "Hide in this view" must never change the NUMBER of things that need the person.
 *
 * The defect (pre-existing at the baseline, recorded in the inventory against AC-4.7-06 and AC-4.7-16): the inbox dialog's per-item Hide removed the item from `visible`, and the attention count was computed from
 * `visible`, so hiding one entry lowered the number the dialog headline, the restore chip and the sidebar badge all read ("Nothing listed as needing attention" with one entry still needing the person). The docs
 * state that hiding "resolves nothing, approves nothing and dismisses nothing anywhere": a view preference cannot change how many things need attention.
 *
 * The fix keeps the capability (the control, the "N hidden in this view" notice, "Show N hidden") and makes the count independent of it: `attention` counts EVERY item that needs the person, hidden here or not, and
 * the notice says the hidden entries are still counted, so "N need attention" and "M hidden in this view" are true together.
 *
 * NOT about the accepted R12 panel-history increment ("Earlier": diReceptionEarlier*.test.ts) or Clear info on the scan face (diScanReceptionClearInfo.test.ts); those stay as they are.
 */
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';

import type { DiOfficeState, DiReception, DiReceptionSourceEntry } from '../../core/src/messages.ts';
import type { MrOverview, MrSummary } from '../src/components/diMissionRequestModel.ts';
import { DiReceptionInbox } from '../src/components/DiReceptionInbox.tsx';
import { attentionLine, buildInbox, type PanelSource } from '../src/components/diReceptionItems.ts';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const fixture = JSON.parse(
  fs.readFileSync(path.join(root, 'test/fixtures/di-office-state-session-mismatch-v2.FIXTURE.json'), 'utf-8'),
) as { state: DiOfficeState };
const state = fixture.state;
const NONE: PanelSource = { kind: 'not_provided' };
const NOTHING: ReadonlySet<string> = new Set();

/** A delivery proposal that WAITS on the person (a synthetic entry, labelled so; the same shape the model reads from `sourceEntries`). */
const delivery = (n: number): DiReceptionSourceEntry => ({
  key: `grok:wait-${n}`,
  signature: `s${n}`,
  category: 'delivery_decision',
  sourceId: 'grok_delivery_presentations',
  proposalDigest: String(n).repeat(64),
  state: 'awaiting_decision',
  needsYou: true,
  presentedAt: 1_800_000_000 + n,
  expiresAt: 1_800_003_600 + n,
  mode: 'pr',
  repositoryUrl: null,
  workflowId: null,
  matchingDeliveries: 1,
  linkState: 'unlinked',
  missionRef: null,
  authorizationCheck: 'checked',
  current: true,
  staleReasons: [],
  route: 'grok',
});
const withDelivery = (r: DiReception, count: number): DiReception => ({
  ...r,
  sourceEntries: Array.from({ length: count }, (_, i) => delivery(i + 1)),
});

/** A request-panel request waiting on the person's decision (a synthetic panel overview). */
const panelWithDecision = (): PanelSource => {
  const request: MrSummary = {
    requestRef: `lr-${'1'.repeat(32)}`,
    missionId: `mn-${'2'.repeat(32)}`,
    revision: 1,
    createdAt: 1_800_000_000,
    objective: 'Make the probe stable',
    digest: 'd'.repeat(64),
    stage: 'of_record',
    label: 'Waiting for your decision',
    terminal: false,
    actions: ['decide'],
    notice: null,
    attestedAt: null,
    cancelledAt: null,
  };
  const overview: MrOverview = {
    serverNow: 1_800_000_100,
    capabilities: {} as MrOverview['capabilities'],
    requests: [request],
    requestsTruncated: false,
    conversations: [],
  };
  return { kind: 'read', overview };
};

const reception = state.reception!;
const missions = state.missions.missions;

describe('the attention count does not depend on what is hidden in this view', () => {
  const cases: Array<[string, DiReception, PanelSource]> = [
    ['Mission entries (the fixture\'s own)', reception, NONE],
    ['delivery proposals that wait on the person', withDelivery(reception, 2), NONE],
    ['a request-panel decision', reception, panelWithDecision()],
    ['all three kinds together', withDelivery(reception, 2), panelWithDecision()],
  ];
  for (const [name, r, panel] of cases) {
    it(`${name}: hiding any one item, or every item, leaves the count exactly where it was`, () => {
      const base = buildInbox(r, panel, missions, NOTHING);
      expect(base.all.length, 'the case has items to hide').toBeGreaterThan(0);
      expect(base.attention).toBe(base.all.length + Math.max(0, r.entriesTotal - r.entries.length));
      for (const item of base.all) {
        const one = buildInbox(r, panel, missions, new Set([item.hideKey]));
        expect(one.attention, `hiding ${item.kind} ${item.key}`).toBe(base.attention);
        expect(one.hiddenCount).toBe(1);
        expect(one.visible).toHaveLength(base.all.length - 1);
      }
      const every = buildInbox(r, panel, missions, new Set(base.all.map((i) => i.hideKey)));
      expect(every.attention).toBe(base.attention);
      expect(every.visible).toEqual([]);
      expect(every.hiddenCount).toBe(base.all.length);
    });
  }

  it('the words for the number come from the count, never from the list: hidden entries do not turn "1 needs attention" into "Nothing listed"', () => {
    const base = buildInbox(reception, NONE, missions, NOTHING);
    const hidden = buildInbox(reception, NONE, missions, new Set(base.all.map((i) => i.hideKey)));
    expect(attentionLine(hidden.attention)).toBe(attentionLine(base.attention));
    expect(attentionLine(hidden.attention)).not.toBe('Nothing listed as needing attention');
  });
});

describe('the dialog says both numbers, and they are true together', () => {
  const renderDialog = (r: DiReception, hidden: ReadonlySet<string>): string =>
    renderToStaticMarkup(
      createElement(DiReceptionInbox, {
        state: { reception: r, coverage: state.coverage, fixtureMode: false, missions: state.missions },
        hidden,
        onHide: () => undefined,
        onShowHidden: () => undefined,
        onJump: () => undefined,
        onClose: () => undefined,
        returnFocusTo: { current: null },
      }),
    );
  const headline = (html: string): string => /data-testid="di-reception-inbox-summary"[^>]*>([^<]*)</.exec(html)?.[1] ?? '';

  it('with one entry hidden, the headline still counts it and the notice says it is hidden here and still counted', () => {
    const base = buildInbox(reception, NONE, missions, NOTHING);
    const html = renderDialog(reception, new Set(base.all.map((i) => i.hideKey)));
    expect(headline(html)).toBe(attentionLine(base.attention));
    expect(headline(html)).not.toMatch(/Nothing listed/);
    expect(html).toMatch(new RegExp(`${base.all.length} hidden in this view only, and still counted in the number above`));
    expect(html).toContain('Every entry is hidden in this view.');
  });

  it('with nothing hidden there is no notice and the headline is the same', () => {
    const base = buildInbox(reception, NONE, missions, NOTHING);
    const html = renderDialog(reception, NOTHING);
    expect(headline(html)).toBe(attentionLine(base.attention));
    expect(html).not.toContain('di-reception-hidden');
  });
});
