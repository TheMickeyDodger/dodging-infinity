/**
 * The locked scan panel: tab buckets from office state, and the 2026-10-25 mock rendered as chrome.
 * Real rows never invent a pull request. The lock specimen is the visual source of truth.
 */
import assert from 'node:assert/strict';

import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { test } from 'vitest';

import type { DiMission, DiOfficeState } from '../../core/src/messages.ts';
import {
  DI_SCAN_LOCK,
  officePanelPresentation,
  openAttentionCount,
  scanRowsFromOffice,
  visibleScanRows,
} from '../src/components/diScanModel.ts';
import { DiScanPanel } from '../src/components/DiScanPanel.tsx';

function mission(partial: Record<string, unknown>): DiMission {
  return {
    missionRef: 'm',
    sourceId: 'herd',
    nativeId: 'native',
    sourceKind: 'herdr_task',
    lifecycleModel: 'herdr.task/1',
    lifecycleState: 'ACTIVE',
    standing: 'active',
    bindingState: 'bound',
    bindingReason: 'canonical_chain_complete',
    producerDeclared: 'herdctl',
    live: true,
    notLiveReason: null,
    objective: { state: 'observed', value: 'Ship the panel', reason: null },
    updatedAt: { state: 'not_recorded', value: null, reason: null },
    identitySource: { state: 'not_recorded', value: null, reason: null },
    paused: { state: 'not_applicable', value: null, reason: null },
    cancelRequested: { state: 'not_applicable', value: null, reason: null },
    ambiguity: { state: 'not_applicable', value: null, reason: null },
    members: { state: 'observed', reason: null, members: [] },
    v2: { projected: false },
    ...partial,
  } as unknown as DiMission;
}

function office(missions: DiMission[], attention: string[] = []): DiOfficeState {
  return {
    live: true,
    notLiveReason: null,
    envelope: { envelopeProblem: null, retryAfterSeconds: null, fresh: true },
    missions: { state: 'observed', reason: null, missions },
    reception: { entries: attention.map((missionRef) => ({ missionRef })) },
  } as unknown as DiOfficeState;
}

const member = {
  characterId: 1,
  coverage: 'direct',
  memberRef: 'mem',
  missionRef: 'live-1',
  logical: 'lead1',
  role: 'lead',
  agent: 'w-lead1',
  provider: 'claude',
  classification: 'present',
  activity: 'busy',
  live: true,
  chain: 'direct',
  chainTaskId: null,
};

test('office rows bucket by standing and never invent a pull request', () => {
  const live = mission({
    missionRef: 'live-1',
    nativeId: 'live-1',
    lifecycleState: 'ACTIVE',
    standing: 'active',
    members: { state: 'observed', reason: null, members: [member] },
  });
  const aborted = mission({
    missionRef: 'dead-1',
    nativeId: 'dead-1',
    lifecycleState: 'ABORTED',
    standing: 'terminal',
    bindingState: 'not_applicable',
  });
  const rows = scanRowsFromOffice(office([live, aborted], ['live-1']));
  const open = rows.find((row) => row.id === 'live-1');
  const closed = rows.find((row) => row.id === 'dead-1');
  assert.equal(open?.tab, 'open');
  assert.deepEqual(open?.needsYou, { known: true, value: true });
  assert.deepEqual(
    open?.pills.map((pill) => pill.label),
    ['Needs you', 'Building'],
  );
  assert.equal(open?.swarm[0]?.role, 'Lead');
  assert.equal(open?.swarm[0]?.name, 'Leo');
  assert.equal(open?.swarm[0]?.note, 'In progress.');
  assert.equal(open?.reviewOffered, false);
  assert.equal(open?.context, 'Active · Herdr');
  assert.equal(closed?.tab, 'closed');
  assert.equal(open?.footer.includes('PR #'), false);
  assert.equal(closed?.footer.includes('PR #'), false);
});

test('the locked specimen matches the mock list on Open and the attention badge', () => {
  assert.equal(openAttentionCount(DI_SCAN_LOCK, new Set()), 1);
  const open = visibleScanRows(DI_SCAN_LOCK, 'open', '', false, new Set());
  assert.deepEqual(
    open.map((row) => row.title),
    [
      'Next panel overhaul',
      'Auth service migration',
      'Payment retry engine',
      'Docs refresh & restructure',
    ],
  );
  assert.equal(open[0]?.footer, `PR #${'4821'} · CI passed on D7 · 2h ago`);
  const merged = visibleScanRows(DI_SCAN_LOCK, 'merged', '', false, new Set());
  assert.deepEqual(
    merged.map((row) => row.title),
    ['Auth service migration', 'Docs refresh & restructure'],
  );
  const found = visibleScanRows(DI_SCAN_LOCK, 'open', 'payments', false, new Set());
  assert.deepEqual(
    found.map((row) => row.title),
    ['Payment retry engine'],
  );
});

// Superseded in part (pass A): '+ NEW MISSION' in the header and 'RECEPTION' in the footer are gone. CONTROL-MAP locked rules
// ("Single homes: + New Orchestrator left rail only; Reception left-rail only") and deviation D-8 move both to the rail.
// Superseded in part (pass A, V-6): the footer's 'Clear all' hid every shown Mission at once, a bulk hide that the locked
// rule "trash = Mission delete only (icon bottom-right; no Clear finished text)" forbids. The footer is gone; the only
// delete affordance is the per-Mission trash, whose gating diScanMissionDetails.test.ts and diScanMissionModel.test.ts hold.
test('the scan chrome renders the locked header, tabs and detail; New and Reception live only in the rail; no bulk Clear all', () => {
  const html = renderToStaticMarkup(
    createElement(DiScanPanel, {
      rows: DI_SCAN_LOCK,
      initialSelectedId: DI_SCAN_LOCK[0]?.id,
      onMinimize: () => undefined,
    }),
  );
  for (const text of [
    'Open',
    'Merged',
    'Closed',
    'Archived',
    'Search missions...',
    'Next panel overhaul',
    'Needs you',
    'Building',
    'Ken',
    'Leo',
    'Mia',
    'Sam',
    'Supervisor',
    `PR #${'4821'} · CI passed on D7 · 2h ago`,
  ]) {
    assert.ok(html.includes(text), `missing ${text}`);
  }
  assert.equal(html.includes('Clear finished'), false);
  assert.equal(html.includes('+ NEW MISSION'), false);
  assert.equal(html.includes('>RECEPTION<'), false);
  assert.equal(html.includes('Clear all'), false);
  assert.equal(html.includes('data-testid="di-scan-clear"'), false);
  assert.equal(html.includes('class="di-scan-footer"'), false);
  assert.ok(html.includes('data-testid="di-scan-attention"'));
  assert.ok(html.includes('>1<'));
});

// Superseded in part (pass A): the nested Herdr pane grid ('herdr' view, 'Ask Codex to do anything') is removed by GAP-RESOLUTIONS
// 9, 11 and 13 (deviation D-4: Herdr opens the machine's own terminal, never an in-app screen), and the uppercase pixel labels
// CODE DIFF / HERDR / COMMENTS become plain title case in Home type (deviation D-2; RECONCILE.md "no pixel display titles").
test('mission details, the code diff and comments open from the locked row; Herdr opens no in-app screen', () => {
  const mission = renderToStaticMarkup(
    createElement(DiScanPanel, {
      rows: DI_SCAN_LOCK,
      initialSelectedId: DI_SCAN_LOCK[0]?.id,
      initialView: 'mission',
      onMinimize: () => undefined,
    }),
  );
  for (const text of [
    'Mission details',
    'Supervisor has the context. Reviewer queue is clear.',
    'No blockers.',
    'Next: Lead handoff to Executor.',
    'Updated just now',
    'All clear. No blockers.',
    'Drafting structure.',
    '>Code Diff<',
    '>Herdr<',
    '>Comments<',
  ]) {
    assert.ok(mission.includes(text), `mission missing ${text}`);
  }
  assert.ok(mission.includes('data-view="mission"'));

  const diff = renderToStaticMarkup(
    createElement(DiScanPanel, {
      rows: DI_SCAN_LOCK,
      initialSelectedId: DI_SCAN_LOCK[0]?.id,
      initialView: 'diff',
      onMinimize: () => undefined,
    }),
  );
  for (const text of [
    'code-diff.ts',
    'Two-pointer diff implementation',
    'Simple diff implementation',
    'Unified',
    '8 files changed',
    'Spaces: 2',
    'src/world/open/next-panel-overhaul/code-diff.ts',
  ]) {
    assert.ok(diff.includes(text), `diff missing ${text}`);
  }
  assert.equal(diff.includes('FILES'), true);

  assert.equal(mission.includes('data-testid="di-scan-pane"'), false);
  assert.equal(mission.includes('Ask Codex to do anything'), false);
  assert.equal(mission.includes('Focus terminal'), false);
  assert.match(mission, /<button[^>]*data-testid="di-scan-herdr"[^>]*disabled=""/);

  const comments = renderToStaticMarkup(
    createElement(DiScanPanel, {
      rows: DI_SCAN_LOCK,
      initialSelectedId: DI_SCAN_LOCK[0]?.id,
      initialView: 'comments',
      onMinimize: () => undefined,
    }),
  );
  // Superseded in pass B (CONTROL-MAP X2/X3, gap 10, D-6): the locked row's Comments said "No comments are recorded".
  // The specimen now draws a labelled SAMPLE thread and, where a composer would be, the Paid/unavailable label; a real
  // Free Mission has no thread (diScanCodeDiffComments.test.ts).
  assert.ok(comments.includes('Sample thread (fixture)'));
  assert.ok(
    comments.includes('Comments are part of Paid team sharing. They are not available in Free.'),
  );
  assert.equal(/<textarea|>Send</.test(comments), false);
  assert.equal(comments.includes('aria-label="Back to mission details"'), true);
  assert.equal(comments.includes('di-scan-tree'), false);

  assert.equal(diff.includes('aria-label="Back to mission details"'), true);
});

// Superseded in part (pass A): the rule is unchanged (one seat per role, no digest as a title), but it is now checked on the
// Swarm cards of Mission details, because the in-app Herdr panes it used to count are removed (GAP-RESOLUTIONS 9, 11, 13).
test('duplicate seats collapse to one Swarm card per role, and a digest never becomes the title', () => {
  const digest = '3033dab9ca1127b3a8dc907fcbbfbeb522aa461d3035277ff41413fa58820bcb';
  const seat = (
    role: string,
    logical: string,
    id: string,
  ): Record<string, unknown> => ({
    ...member,
    memberRef: id,
    logical,
    role,
    agent: `w-${logical}`,
  });
  const live = mission({
    missionRef: 'live-digest',
    nativeId: '20261024-120000-abcdef',
    sourceId: 'di_herdr_tasks',
    lifecycleState: 'ACTIVE',
    objective: {
      state: 'observed',
      value: `AUTHORIZED MISSION mn-ba1e47939b4f6b443cd34174b9b1732 (revision 1, proposal digest ${digest}). This is the approved Mission content only, NOT an engineering plan.\n\nOBJECTIVE\nRedesign the core panel system.`,
      reason: null,
    },
    members: {
      state: 'observed',
      reason: null,
      members: [
        seat('supervisor', 'supervisor', 's1'),
        seat('supervisor', 'supervisor', 's2'),
        seat('supervisor', 'supervisor', 's3'),
        seat('lead', 'lead1', 'l1'),
        seat('executor', 'executor', 'e1'),
        seat('reviewer', 'sam', 'r1'),
      ],
    },
  });
  const row = scanRowsFromOffice(office([live]))[0];
  assert.ok(row);
  assert.equal(row.title, 'Redesign the core panel system');
  assert.equal(row.title.includes(digest.slice(0, 12)), false);
  assert.equal(row.description.includes(digest.slice(0, 12)), false);
  assert.equal(row.description.includes('Redesign the core panel system'), true);
  assert.equal(row.context, 'Active · Herdr');
  assert.deepEqual(
    row.swarm.map((avatar) => avatar.role),
    ['Supervisor', 'Lead', 'Executor', 'Reviewer'],
  );
  assert.deepEqual(
    row.swarm.map((avatar) => avatar.name),
    ['Ken', 'Leo', 'Mia', 'Sam'],
  );
  assert.equal(row.footer.includes(digest), false);
  assert.equal(row.footer.includes('di_herdr_tasks'), false);

  const html = renderToStaticMarkup(
    createElement(DiScanPanel, {
      rows: [row],
      initialSelectedId: row.id,
      initialView: 'mission',
      onMinimize: () => undefined,
    }),
  );
  assert.equal((html.match(/data-testid="di-scan-swarm-member"/g) ?? []).length, 4);
  assert.equal(html.includes(digest), false);
  assert.equal(html.includes('Focus terminal'), false);
  assert.equal(html.includes('Ask Codex to do anything'), false);
});

test('the product face is the scan panel unless a harness asks for the operational list', () => {
  assert.equal(officePanelPresentation({}), 'scan');
  assert.equal(officePanelPresentation({ e2e: true }), 'operational');
  assert.equal(officePanelPresentation({ face: 'operational' }), 'operational');
  assert.equal(officePanelPresentation({ face: 'scan', e2e: true }), 'scan');
});

test('pass C (B-E1 on Home): the labelled sample specimen says so on Home; real office rows never carry the sample tag', () => {
  const home = (specimen: boolean) =>
    renderToStaticMarkup(
      createElement(DiScanPanel, {
        rows: DI_SCAN_LOCK,
        initialSelectedId: DI_SCAN_LOCK[0]!.id,
        initialView: 'list',
        onMinimize: () => undefined,
        specimen,
      }),
    );
  assert.match(home(true), /data-testid="di-home-sample-tag"[^>]*>Sample Missions \(fixture\)</);
  assert.equal(home(false).includes('di-home-sample-tag'), false);
});
