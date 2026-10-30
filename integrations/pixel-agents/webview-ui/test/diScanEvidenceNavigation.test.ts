/**
 * R10, the part that is established: a Mission's GENERIC listed evidence, the EXISTING evidence library, opened as a nested Mission screen named "Evidence and review", the existing heading of the
 * office section that holds the library (DiOfficePanel's inspector). Never "View log" or "View checks". It is entered from Mission details and returns there; it is NOT a fourth sibling beside Code
 * Diff and Comments, because a fourth wraps the Code Diff header to two rows at the locked widths (diScanHeaderRegion's one-row requirement).
 *
 * Why not log or checks: read from the producers, not the fixtures (inventory row R10). The artifact role and the evidence kind are closed sets with no log or check member (mission/record.py), the
 * office contract has no log or check-run field, and pull-request checks are read nowhere (pr_delivery reads only the base commit's runs and keeps one verdict word). So the log and check obligation stays
 * owed (a backend or contract addition) and is NOT discharged here.
 *
 * What this pins:
 *  - a real row says whether the office LISTS an evidence library for its Mission (the record's own `evidence` block), and nothing else;
 *  - "Evidence and review" on Mission details is enabled only where one is listed and the library can be drawn here; otherwise it is drawn disabled with its reason as visible text;
 *  - the locked three siblings on Code Diff and Comments are unchanged;
 *  - the library is drawn on its own screen only, for that Mission;
 *  - no control is named "View log" or "View checks".
 */
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';

import type { DiOfficeState } from '../../core/src/messages.ts';
import {
  DI_EVIDENCE_LABEL,
  DI_EVIDENCE_NOT_AVAILABLE,
  DI_EVIDENCE_NOT_LISTED,
  DI_SCAN_MISSION_VIEWS,
  type DiScanRow,
  type DiScanView,
  scanRowsFromOffice,
} from '../src/components/diScanModel.ts';
import { DiScanPanel } from '../src/components/DiScanPanel.tsx';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
/** The evidence library of artifact-content-v1 as one real DiOffice projects it (its `live` state). */
const state = (
  JSON.parse(fs.readFileSync(path.join(root, 'test/fixtures/di-office-state-evidence-v2.FIXTURE.json'), 'utf-8')) as { states: { live: DiOfficeState } }
).states.live;
/** SYNTHETIC: the same office with the evidence block removed from every Mission record, for the "none listed" case. */
const bare = structuredClone(state);
for (const m of bare.missions.missions) delete m.evidence;
const rows = [...scanRowsFromOffice(state), ...scanRowsFromOffice(bare).map((r) => ({ ...r, id: `bare:${r.id}` }))];
const listed = state.missions.missions.filter((m) => m.evidence !== undefined).map((m) => m.missionRef);
const unlisted = [
  ...state.missions.missions.filter((m) => m.evidence === undefined).map((m) => m.missionRef),
  ...bare.missions.missions.map((m) => `bare:${m.missionRef}`),
];

/** The library stand-in: the panel draws whatever the office hands it for THAT Mission. */
const probe = (ref: string) => createElement('div', { 'data-testid': 'probe-library', 'data-ref': ref });
const html = (row: DiScanRow, view: DiScanView, renderEvidence?: (ref: string) => unknown) =>
  renderToStaticMarkup(
    createElement(DiScanPanel, {
      rows: [row],
      initialSelectedId: row.id,
      initialView: view,
      onMinimize: () => undefined,
      renderEvidence: renderEvidence as never,
    }),
  );
const byTestid = (markup: string, id: string) => new RegExp(`<button[^>]*data-testid="${id}"[^>]*>([^<]*)</button>`).exec(markup);
const text = (markup: string) => markup.replace(/<[^>]+>/g, ' ').replace(/&#x27;/g, "'").replace(/\s+/g, ' ');

describe('a real row says whether the office lists an evidence library for its Mission', () => {
  it('the fixture has Missions of both kinds (a non-empty guard on each selection)', () => {
    expect(listed.length).toBeGreaterThan(0);
    expect(unlisted.length).toBeGreaterThan(0);
  });

  it('listed exactly where the record carries its evidence block', () => {
    for (const row of rows) expect(row.evidenceListed, row.id).toBe(listed.includes(row.id));
  });

  it('the evidence screen is one of a Mission\'s nested screens', () => {
    expect(DI_SCAN_MISSION_VIEWS).toContain('evidence');
  });
});

describe('the entrances', () => {
  const withLibrary = rows.find((r) => listed.includes(r.id))!;
  const without = rows.find((r) => unlisted.includes(r.id))!;

  it('where one is listed and the library can be drawn: "Evidence and review" on Mission details, enabled', () => {
    const details = byTestid(html(withLibrary, 'mission', probe), 'di-scan-evidence');
    expect(details?.[1]).toBe(DI_EVIDENCE_LABEL);
    expect(details?.[0]).not.toContain('disabled');
  });

  it('the locked three siblings stay three on Code Diff and Comments (a fourth wraps the Code Diff header at the locked widths)', () => {
    for (const view of ['diff', 'comments'] as const) {
      const markup = html(withLibrary, view, probe);
      expect(markup, view).not.toContain('di-scan-sibling-evidence');
      expect(markup.match(/data-testid="di-scan-sibling-[a-z]+"/g), view).toEqual([
        'data-testid="di-scan-sibling-diff"',
        'data-testid="di-scan-sibling-herdr"',
        'data-testid="di-scan-sibling-comments"',
      ]);
    }
  });

  it('where none is listed: drawn disabled, saying so as visible text on Mission details', () => {
    const markup = html(without, 'mission', probe);
    expect(byTestid(markup, 'di-scan-evidence')?.[0]).toContain('disabled');
    expect(text(markup)).toContain(DI_EVIDENCE_NOT_LISTED);
  });

  it('where the library cannot be drawn in this office: drawn disabled, saying so as visible text', () => {
    const markup = html(withLibrary, 'mission');
    expect(byTestid(markup, 'di-scan-evidence')?.[0]).toContain('disabled');
    expect(text(markup)).toContain(DI_EVIDENCE_NOT_AVAILABLE);
  });

  it('no control is named "View log" or "View checks"', () => {
    for (const row of [withLibrary, without])
      for (const view of ['mission', 'diff', 'evidence'] as const)
        expect(html(row, view, probe)).not.toMatch(/<button[^>]*>\s*View (log|checks)\s*<\/button>/);
  });
});

describe('the evidence screen', () => {
  const withLibrary = rows.find((r) => listed.includes(r.id))!;

  it('draws the library the office hands over for THAT Mission, on its own screen only', () => {
    expect(html(withLibrary, 'evidence', probe)).toContain(`data-ref="${withLibrary.id}"`);
    expect(html(withLibrary, 'mission', probe)).not.toContain('probe-library');
    expect(html(withLibrary, 'diff', probe)).not.toContain('probe-library');
  });

  it('it says where the person is (the location ends with "Evidence and review"), and Back returns to Mission details', () => {
    const markup = html(withLibrary, 'evidence', probe);
    const crumb = /<nav[^>]*data-testid="di-scan-crumb"[^>]*>([\s\S]*?)<\/nav>/.exec(markup)?.[1] ?? '';
    expect(text(crumb).trim().endsWith(DI_EVIDENCE_LABEL)).toBe(true);
    expect(/<button[^>]*data-testid="di-scan-back"[^>]*>/.exec(markup)?.[0]).toContain('aria-label="Back to mission details"');
  });
});
