/**
 * PRESENTATION of the DI navigation rail (pass A, A2): what the rail draws, in what order, which entries are live and
 * which are truthfully unavailable, and that New Orchestrator and Reception each have ONE home (CONTROL-MAP "Locked
 * rules" and "Navigation", 01-home.jpg, deviations D-3, D-8, D-9).
 *
 * A passing presentation test is not acceptance of a control. The rail adds no action route: it navigates (Home, the
 * Missions table, the Reception page, the New Mission page, a Mission's details) and opens the existing Settings dialog.
 *
 * Pass B (lead's rulings, 2026-10-26): Missions opens the Missions table (CONTROL-MAP L5, S1 to S5), replacing pass A's
 * "drawn but unavailable"; + New Orchestrator opens the drawn New Mission page (G1, lead Q1), replacing "opens the existing
 * request workspace"; Reception opens the full-page Reception, whose source also carries the badge count (lead Q2).
 */
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';

import { DI_SCAN_LOCK } from '../src/components/diScanModel.ts';
import type { DiScanReception } from '../src/components/diScanPagesModel.ts';
import { DiScanPanel, type DiScanPanelProps } from '../src/components/DiScanPanel.tsx';
import { DI_RAIL_WIDTH_PX } from '../src/constants.ts';

const noop = () => undefined;
/** A reception source whose count is `n` (the page and the badge both read it). */
const receptionOf = (n: number): DiScanReception => ({
  source: 'office',
  attention: n,
  // pass C shape migration: a whole count (no doubt), as this literal always meant
  attentionDoubt: null,
  cards: [],
  information: [],
  notListed: n,
  earlier: null,
  empty: [],
  checked: [],
});
const render = (props: Partial<DiScanPanelProps> = {}) =>
  renderToStaticMarkup(
    createElement(DiScanPanel, {
      rows: DI_SCAN_LOCK,
      onMinimize: noop,
      onOpenRequestWorkspace: noop,
      onOpenSettings: noop,
      reception: receptionOf(2),
      ...props,
    }),
  );

/** The rail's own markup. */
function rail(html: string): string {
  const m = /<nav[^>]*data-testid="di-rail"[\s\S]*?<\/nav>/.exec(html);
  if (!m) throw new Error('no rail rendered');
  return m[0];
}
/** The opening tag of the element carrying `testid` (the first one). */
function tag(html: string, testid: string): string {
  const m = new RegExp(`<[a-z]+[^>]*data-testid="${testid}"[^>]*>`).exec(html);
  if (!m) throw new Error(`no ${testid}`);
  return m[0];
}
/** Visible text of the element carrying `testid` (tags removed). */
function text(html: string, testid: string): string {
  const m = new RegExp(`<([a-z]+)[^>]*data-testid="${testid}"[^>]*>([\\s\\S]*?)</\\1>`).exec(html);
  if (!m) throw new Error(`no ${testid}`);
  return m[2]
    .replace(/<span class="di-scan-visually-hidden"[^>]*>[\s\S]*?<\/span>/g, '')
    .replace(/<[^>]+>/g, '')
    .trim();
}
const testids = (html: string) =>
  [...html.matchAll(/data-testid="(di-[a-z-]+)"/g)].map((m) => m[1]);

describe('PRESENTATION: the rail draws the Home navigation in order (01-home.jpg, CONTROL-MAP Navigation)', () => {
  it('DI World, + New Orchestrator, Reception, the Orchestrators, then Workspace (Missions, Herdr, Connections), and Settings last', () => {
    const order = testids(rail(render())).filter(
      (id) => id.startsWith('di-rail-') || id === 'di-open-reception',
    );
    const entries = order.filter(
      (id) =>
        id !== 'di-rail-reception-count' &&
        id !== 'di-rail-orchestrator-status' &&
        id !== 'di-rail-orchestrators',
    );
    expect(entries).toEqual([
      'di-rail-home',
      'di-rail-new-orchestrator',
      'di-open-reception',
      'di-rail-orchestrator',
      'di-rail-orchestrator',
      'di-rail-missions',
      'di-rail-herdr',
      'di-rail-connections',
      'di-rail-settings',
    ]);
    const r = rail(render());
    expect(text(r, 'di-rail-new-orchestrator')).toBe('+ New Orchestrator');
    expect(r).toMatch(/>Orchestrators</);
    expect(r).toMatch(/>Workspace</);
  });
  it('is as wide as the width the world yields for it, so the two cannot overlap', () => {
    expect(tag(render(), 'di-rail')).toContain(`width:${DI_RAIL_WIDTH_PX}px`);
  });
  it('labels the herd entry "Herdr", never "Herd" (D-3)', () => {
    expect(text(rail(render()), 'di-rail-herdr')).toBe('Herdr');
    expect(rail(render())).not.toMatch(/>Herd</);
  });
  it('has no Team entry and no Connections count, which no source could supply (D-9)', () => {
    const r = rail(render());
    expect(r).not.toMatch(/\bTeam\b/);
    expect(text(r, 'di-rail-connections')).toBe('Connections');
  });
  it('lists as Orchestrators the Missions still in progress, each with a status dot AND its word (AC-UX-10)', () => {
    const r = rail(render());
    const items = [
      ...r.matchAll(/data-testid="di-rail-orchestrator"[^>]*>([\s\S]*?)<\/button>/g),
    ].map((m) => m[1]);
    expect(
      items.map((i) => /class="di-rail-orchestrator-title"[^>]*>([^<]*)</.exec(i)?.[1]),
    ).toEqual(['Next panel overhaul', 'Payment retry engine']);
    for (const item of items) {
      expect(item).toMatch(/class="di-scan-dot" data-tone="[a-z]+" aria-hidden="true"/);
      expect(item).toMatch(/data-testid="di-rail-orchestrator-status">(Needs you|Building)</);
    }
  });
  it('says plainly when no Orchestrator is running', () => {
    const r = rail(render({ rows: [] }));
    expect(r).not.toContain('data-testid="di-rail-orchestrator"');
    expect(r).toContain('No Orchestrators are running.');
  });
});

describe('PRESENTATION: the Reception badge carries the count it is given, and only when something waits (AC-4.7-15/-16)', () => {
  it('shows the count beside Reception, and says it in words to a screen reader', () => {
    const r = rail(render({ reception: receptionOf(3) }));
    expect(text(r, 'di-rail-reception-count')).toBe('3');
    expect(tag(r, 'di-open-reception')).toContain('aria-label="Reception, 3 need attention"');
  });
  it('no badge at zero, and none where the office has no reception desk', () => {
    expect(rail(render({ reception: receptionOf(0) }))).not.toContain('di-rail-reception-count');
    expect(rail(render({ reception: null }))).not.toContain('di-rail-reception-count');
  });
});

describe('PRESENTATION: single homes (CONTROL-MAP locked rules, D-8)', () => {
  it('+ New Orchestrator is drawn only in the rail; the missions panel header has no New button', () => {
    const html = render();
    expect(html.match(/New Orchestrator/g)).toHaveLength(1);
    expect(rail(html)).toContain('New Orchestrator');
    expect(html).not.toMatch(/NEW MISSION|New mission/);
    expect(html).not.toContain('data-testid="di-scan-new-mission"');
  });
  it('Reception is reached only from the rail; the missions panel footer has no Reception button', () => {
    const html = render();
    expect(html.match(/data-testid="di-open-reception"/g)).toHaveLength(1);
    expect(rail(html)).toContain('data-testid="di-open-reception"');
    expect(html).not.toContain('>RECEPTION<');
  });
});

describe('PRESENTATION: entries behave truthfully (A2)', () => {
  // Superseded in pass B: Missions was "drawn but unavailable" until the Missions table existed (CONTROL-MAP L5, S1 to
  // S5). It is now live navigation; Herdr and Connections keep the unavailable rule unchanged.
  it('Missions opens the Missions table: live, never disabled, and marked when the table is shown', () => {
    const t = tag(rail(render()), 'di-rail-missions');
    expect(t).not.toContain('disabled');
    expect(t).not.toContain('aria-current');
    expect(tag(rail(render({ initialView: 'missions' })), 'di-rail-missions')).toContain(
      'aria-current="page"',
    );
  });
  it('Herdr and Connections are drawn but unavailable, each with its plain reason', () => {
    const r = rail(render());
    const reasons: Record<string, string> = {
      'di-rail-herdr':
        'Herdr opens in your own terminal. That is not connected in this layout preview.',
      'di-rail-connections': 'Connections are not part of this build.',
    };
    for (const [id, reason] of Object.entries(reasons)) {
      const t = tag(r, id);
      expect(t, id).toContain('disabled=""');
      expect(t, id).toContain(`title="${reason}"`);
      expect(r, id).toContain(`>${reason}</span>`);
    }
  });
  // Superseded in pass B: New Orchestrator opened the existing request workspace directly. It now opens the drawn New
  // Mission page (gap 1, CONTROL-MAP G1, lead Q1), which is always drawable, so the entry is never disabled; the page
  // carries the one labelled link to the existing workspace, and says why when the office does not offer it.
  it('New Orchestrator opens the New Mission page: live with or without the request workspace, Cmd N, marked when shown', () => {
    for (const props of [{}, { onOpenRequestWorkspace: undefined }]) {
      const live = tag(rail(render(props)), 'di-rail-new-orchestrator');
      expect(live).not.toContain('disabled');
      expect(live).toContain('aria-keyshortcuts="Meta+N"');
      expect(live).toContain('title="Start a new Mission (Cmd N)"');
    }
    expect(tag(rail(render({ initialView: 'new' })), 'di-rail-new-orchestrator')).toContain(
      'aria-current="page"',
    );
  });
  it('Reception opens the Reception page when the office has a desk, marked when shown, and otherwise says why', () => {
    expect(tag(rail(render()), 'di-open-reception')).not.toContain('disabled');
    expect(tag(rail(render({ initialView: 'reception' })), 'di-open-reception')).toContain(
      'aria-current="page"',
    );
    const absent = tag(rail(render({ reception: null })), 'di-open-reception');
    expect(absent).toContain('disabled=""');
    expect(absent).toContain('title="Reception is not available in this office"');
  });
  it('Settings opens the existing settings dialog when it is offered, and otherwise says why', () => {
    expect(tag(rail(render()), 'di-rail-settings')).not.toContain('disabled');
    const absent = tag(rail(render({ onOpenSettings: undefined })), 'di-rail-settings');
    expect(absent).toContain('disabled=""');
    expect(absent).toContain('title="Settings are not available in this office"');
  });
  it('keeps Minimize on the rail, including when a stage or rail page covers the Home header', () => {
    // Free UI draws Mission details, Code Diff, and the rail pages from the rail's right edge to the
    // window's right edge, which covers the Home logo that used to be the only Minimize control.
    const views = ['list', 'mission', 'diff', 'comments', 'missions', 'reception', 'new'] as const;
    for (const initialView of views) {
      const html = render({ initialView, initialSelectedId: DI_SCAN_LOCK[0]!.id });
      const marks = html.match(/data-testid="di-office-panel-minimize"/g) ?? [];
      expect(marks, initialView).toHaveLength(1);
      const r = rail(html);
      expect(r, initialView).toContain('data-testid="di-office-panel-minimize"');
      expect(text(r, 'di-office-panel-minimize').replace(/\s+/g, ''), initialView).toBe('▸Minimize');
      expect(tag(r, 'di-office-panel-minimize'), initialView).toContain(
        'title="Minimize the office panel; Restore brings it back as you left it"',
      );
    }
  });
  it('marks where the person is: DI World on Home, the Orchestrator on its Mission details', () => {
    expect(tag(rail(render()), 'di-rail-home')).toContain('aria-current="page"');
    const onMission = rail(
      render({ initialSelectedId: DI_SCAN_LOCK[0]!.id, initialView: 'mission' }),
    );
    expect(tag(onMission, 'di-rail-home')).not.toContain('aria-current');
    expect(tag(onMission, 'di-rail-orchestrator')).toContain('aria-current="page"');
  });
});
