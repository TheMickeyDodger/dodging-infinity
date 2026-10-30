/**
 * The merge language REMOVED (owner ruling, 2026-10-28, owner-decisions.md:93): "Hold to merge" is gone, and nothing replaces it as a control. The pinned no-merge boundary stays: pr_delivery has no
 * merge step, and its steps end at opening a pull request (pr_delivery/authorization.py STEPS).
 *
 * Why removal and not a renamed or disabled control: a disabled merge control says "not now" where the truth is "not ever, by design", and a "Hold" control would name an operation this product
 * does not have (no hold, no pause, and no per-step authorization: the delivery tool's one human authorization covers all its steps and `advance` runs them in one call, pr_delivery/machine.py).
 *
 * What this pins:
 *  - Delivery lists four steps, Commit, Push, Open pull request, Accept CI results; there is no merge step and no "hold";
 *  - the steps are followed by one STATEMENT of what delivery produces, "Delivery ends with an open pull request.", as text, never a control, saying nothing about merging or holding;
 *  - no control in the Delivery section names merge or hold;
 *  - the specimen's copy no longer says "Ready to merge", "unlock Merge" or "Merge happens";
 *  - the status words for an OBSERVED state ("Merged", a tab) are not part of this removal and stay.
 */
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';

import {
  deliverySteps,
  DI_DELIVERY_END,
  DI_SCAN_LOCK,
  DI_SCAN_TABS,
  type DiDeliveryStage,
  type DiScanRow,
} from '../src/components/diScanModel.ts';
import { DI_RECEPTION_SPECIMEN } from '../src/components/diScanPagesModel.ts';
import { DiScanPanel } from '../src/components/DiScanPanel.tsx';

const STAGES: readonly DiDeliveryStage[] = ['working', 'approved', 'committed', 'pushed', 'pr_open', 'ci_failed', 'ci_passed', 'ci_accepted', 'merged'];
const delivery = (row: DiScanRow) => {
  const html = renderToStaticMarkup(createElement(DiScanPanel, { rows: [row], initialSelectedId: row.id, initialView: 'mission', onMinimize: () => undefined }));
  return /<section[^>]*data-testid="di-scan-delivery"[\s\S]*?<\/section>/.exec(html)?.[0] ?? '';
};
const text = (markup: string) => markup.replace(/<[^>]+>/g, ' ').replace(/&#x27;/g, "'").replace(/\s+/g, ' ');

describe('no merge step', () => {
  it('Delivery lists four steps; there is no merge step and no hold, at any stage', () => {
    for (const stage of STAGES) {
      const steps = deliverySteps({ source: 'fixture', stage });
      expect(steps.map((s) => s.label), stage).toEqual(['Commit', 'Push', 'Open pull request', 'Accept CI results']);
      expect(steps.some((s) => /merge|hold/i.test(`${s.id} ${s.label} ${s.reason}`)), stage).toBe(false);
    }
  });
});

describe('what delivery produces, stated, never a control', () => {
  it('the statement says what delivery produces and nothing about merging or holding', () => {
    expect(DI_DELIVERY_END).toBe('Delivery ends with an open pull request.');
    expect(DI_DELIVERY_END).not.toMatch(/merge|hold|separately authori[sz]ed/i);
  });

  it('every row draws it after the steps, as text; no control in Delivery names merge or hold', () => {
    const real: DiScanRow = { ...DI_SCAN_LOCK[0]!, id: 'real', delivery: { source: 'not_read', stage: null } };
    for (const row of [...DI_SCAN_LOCK, real]) {
      const d = delivery(row);
      expect(d, row.id).not.toBe('');
      expect(d, row.id).toMatch(new RegExp(`<p[^>]*data-testid="di-scan-delivery-end"[^>]*>${DI_DELIVERY_END.replace('.', '\\.')}</p>`));
      for (const button of d.match(/<button[^>]*>[\s\S]*?<\/button>/g) ?? []) expect(button, row.id).not.toMatch(/merge|hold/i);
      // any inflection ("merge", "merges", "merged", "merging"), and "hold" as a word
      expect(text(d), row.id).not.toMatch(/merg|\bhold/i);
    }
  });
});

describe('the specimen no longer implies that DI merges', () => {
  it('no "Ready to merge", "unlock Merge" or "Merge happens" in its rows or its Reception', () => {
    const copy = JSON.stringify([DI_SCAN_LOCK, DI_RECEPTION_SPECIMEN]);
    expect(copy).not.toMatch(/Ready to merge|unlock Merge|Merge happens/i);
  });

  it('the words for an OBSERVED state stay: the "Merged" tab', () => {
    expect(DI_SCAN_TABS.map((t) => t.label)).toContain('Merged');
  });
});
