/**
 * 04 Missions (pass B, S1 to S5): the table, its ownership SCOPE and STATUS filters with counts, its columns, its empty
 * state and its archive note. Rules from CONTROL-MAP "04 Missions", AC-4.4-01/-05/-11/-13/-14/-16/-28, D-5, D-7.
 *
 * Mine/Team is the ownership SCOPE; All, Needs you, Stalled, Running, Done and Archived are STATUS filters (Mine is not
 * a seventh status). Default: scope Mine, status All. Each status filter shows its count; a count this build cannot read
 * says so instead of a number. Real rows carry "Not recorded" where the office record has no such field: no owner, repo
 * or phase is invented. Presentation and navigation only; nothing here changes a Mission.
 */
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';

import { compactAge, DI_SCAN_LOCK, type DiScanRow } from '../src/components/diScanModel.ts';
import { missionsCounts, missionsShown } from '../src/components/diScanPagesModel.ts';
import { DiScanPanel } from '../src/components/DiScanPanel.tsx';
import { productionRow } from './helpers/productionRow.ts';

const noop = () => undefined;
const render = (rows: readonly DiScanRow[]) =>
  renderToStaticMarkup(
    createElement(DiScanPanel, { rows, initialView: 'missions', onMinimize: noop }),
  );
const ids = (rows: readonly DiScanRow[]) => rows.map((r) => r.id);

describe('S1: status filters and their counts (AC-4.4-11), over the labelled specimen', () => {
  it('counts each status filter by hand-checked membership of the specimen', () => {
    // specimen: next-panel (Needs you, Building), auth (Merged), payments (Building, Failed), docs (Merged)
    expect(missionsCounts(DI_SCAN_LOCK)).toEqual({ all: 4, needs: 1, stalled: 0, running: 2, done: 2, archived: 0 });
  });
  it('each filter shows only matching Missions, and All shows every Mission that is not archived', () => {
    expect(ids(missionsShown(DI_SCAN_LOCK, 'all'))).toEqual(['lock-next-panel', 'lock-auth', 'lock-payments', 'lock-docs']);
    expect(ids(missionsShown(DI_SCAN_LOCK, 'needs'))).toEqual(['lock-next-panel']);
    expect(ids(missionsShown(DI_SCAN_LOCK, 'running'))).toEqual(['lock-next-panel', 'lock-payments']);
    expect(ids(missionsShown(DI_SCAN_LOCK, 'done'))).toEqual(['lock-auth', 'lock-docs']);
    expect(ids(missionsShown(DI_SCAN_LOCK, 'stalled'))).toEqual([]);
  });
  it('an archived Mission leaves every filter but Archived (AC-4.4-28)', () => {
    const archived: DiScanRow = { ...DI_SCAN_LOCK[1]!, id: 'old', tab: 'archived', pills: [] };
    const rows = [...DI_SCAN_LOCK, archived];
    expect(ids(missionsShown(rows, 'archived'))).toEqual(['old']);
    for (const f of ['all', 'needs', 'running', 'done', 'stalled'] as const)
      expect(ids(missionsShown(rows, f)), f).not.toContain('old');
    expect(missionsCounts(rows).archived).toBe(1);
  });
  it('a count this build cannot read is null, never a number: stalls are not read for production rows', () => {
    const rows = [productionRow('a', {}), productionRow('b', {}, ['b'])];
    const counts = missionsCounts(rows);
    expect(counts.stalled).toBeNull();
    expect(counts.all).toBe(2);
    expect(counts.needs).toBe(1);
  });
});

describe('PRESENTATION: the Missions table (CONTROL-MAP 04, v3 04-missions.png)', () => {
  const html = render(DI_SCAN_LOCK);
  it('opens on scope Mine and status All; Team is drawn unavailable with its reason, not hidden (AC-4.4-01/-16)', () => {
    expect(html).toMatch(/data-testid="di-missions-scope-mine"[^>]*aria-pressed="true"/);
    const team = /<button[^>]*data-testid="di-missions-scope-team"[^>]*>/.exec(html)?.[0] ?? '';
    expect(team).toContain('disabled=""');
    expect(team).toContain('title="Team Missions are part of Paid and are not available in Free."');
    expect(html).toMatch(/data-testid="di-missions-status-all"[^>]*aria-pressed="true"/);
  });
  it('scope and status are two separate groups: Mine is not a seventh status', () => {
    const scope = /<div[^>]*role="group"[^>]*aria-label="Whose Missions"[^>]*>[\s\S]*?<\/div>/.exec(html)?.[0] ?? '';
    const status = /<div[^>]*role="group"[^>]*aria-label="Status"[^>]*>[\s\S]*?<\/div>/.exec(html)?.[0] ?? '';
    expect(scope).toContain('Mine');
    expect(scope).toContain('Team');
    expect(status).not.toContain('Mine');
    expect([...status.matchAll(/data-testid="di-missions-status-([a-z]+)"/g)].map((m) => m[1])).toEqual([
      'all',
      'needs',
      'stalled',
      'running',
      'done',
      'archived',
    ]);
  });
  it('every status filter shows its count', () => {
    for (const [id, n] of [['all', 4], ['needs', 1], ['stalled', 0], ['running', 2], ['done', 2], ['archived', 0]] as const)
      expect(html, id).toMatch(new RegExp(`data-testid="di-missions-status-${id}"[\\s\\S]*?data-testid="di-missions-count"[^>]*>${n}<`));
  });
  it('the columns are Mission, Owner, Repos, Phase, Status, Last (D-7: models by provider stay on Swarm)', () => {
    const heads = [...html.matchAll(/<th[^>]*>([^<]*)<\/th>/g)].map((m) => m[1]);
    expect(heads).toEqual(['Mission', 'Owner', 'Repos', 'Phase', 'Status', 'Last']);
    expect(html).not.toMatch(/Model /);
  });
  it('a row shows the Mission and its step, and status as a dot AND a word', () => {
    const row = /<tr[^>]*data-mission-ref="lock-next-panel"[\s\S]*?<\/tr>/.exec(html)?.[0] ?? '';
    expect(row).toContain('Next panel overhaul');
    expect(row).toContain('D7 passed · next panel overhaul');
    expect(row).toMatch(/<span class="di-scan-dot" data-tone="needs" aria-hidden="true"><\/span>Needs you/);
  });
  it('each row offers a button that opens its Mission details (S2)', () => {
    expect(html.match(/data-testid="di-missions-open"/g)).toHaveLength(4);
  });
  it('the footer says finished Missions archive after 30 days (S5, AC-4.4-05/-28)', () => {
    expect(html).toContain('Finished Missions archive after 30 days.');
  });
  it('Last is the record\'s own update time on the office clock, said compactly', () => {
    const real = render([productionRow('r', { updatedAt: { state: 'observed', value: '1799999640', reason: null } })]);
    const row = /<tr[^>]*data-mission-ref="r"[\s\S]*?<\/tr>/.exec(real)?.[0] ?? '';
    expect(row).toMatch(/data-testid="di-missions-last"[^>]*>6m</);
  });
  it('production rows say "Not recorded" for owner and repos rather than invent them, and the Phase cell says what the producer declared (this fixture is a v1 office: it provides none), never a step', () => {
    const real = render([productionRow('r', {})]);
    const row = /<tr[^>]*data-mission-ref="r"[\s\S]*?<\/tr>/.exec(real)?.[0] ?? '';
    expect(row.match(/Not recorded/g)?.length ?? 0).toBeGreaterThanOrEqual(2);
    expect(row).toMatch(/data-testid="di-phase-declared"[^>]*data-kind="not_provided"/);
    expect(row).not.toContain('di-phase-bar');
    expect(row).not.toContain('You');
    expect(real).toMatch(/data-testid="di-missions-status-stalled"[\s\S]*?data-testid="di-missions-count"[^>]*>not read</);
  });
});

describe('S4: the empty state is a note and NO New Orchestrator button (D-5, gap 12)', () => {
  // Superseded: AC-4.4-10 asked for "a short empty note and a New Orchestrator button". GAP-RESOLUTIONS 12 and the
  // locked single-homes rule (New Orchestrator lives in the rail only) supersede the button.
  it('with no Missions it says so and offers no New Orchestrator', () => {
    const html = render([]);
    const page = /data-testid="di-missions-page"[\s\S]*$/.exec(html)?.[0] ?? '';
    expect(page).toContain('No Missions yet.');
    expect(page).not.toMatch(/New Orchestrator|New Mission/);
  });
});

describe('S3 Last reads at a glance however old the record is (a synthetic 1970 time read "20741d")', () => {
  it('now, minutes, hours, days, months and years, on one clock', () => {
    const now = 1_800_000_000;
    expect(compactAge(now, now - 30)).toBe('now');
    expect(compactAge(now, now - 6 * 60)).toBe('6m');
    expect(compactAge(now, now - 13 * 3600)).toBe('13h');
    expect(compactAge(now, now - 2 * 86400)).toBe('2d');
    expect(compactAge(now, now - 45 * 86400)).toBe('1mo');
    expect(compactAge(now, now - 400 * 86400)).toBe('1y');
    expect(compactAge(now, 1_000_000)).toBe('57y');
  });
});
