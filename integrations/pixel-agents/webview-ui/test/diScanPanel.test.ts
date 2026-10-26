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
  assert.equal(open?.needsYou, true);
  assert.deepEqual(
    open?.pills.map((pill) => pill.label),
    ['Needs you', 'Building'],
  );
  assert.equal(open?.swarm[0]?.role, 'Lead');
  assert.equal(open?.swarm[0]?.name, 'lead1');
  assert.equal(open?.swarm[0]?.note, 'In progress.');
  assert.equal(open?.reviewOffered, false);
  assert.equal(open?.context, 'ACTIVE · herd');
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

test('the scan chrome renders the locked header, tabs, detail and footer', () => {
  const html = renderToStaticMarkup(
    createElement(DiScanPanel, {
      rows: DI_SCAN_LOCK,
      initialSelectedId: DI_SCAN_LOCK[0]?.id,
      onMinimize: () => undefined,
    }),
  );
  for (const text of [
    '+ NEW MISSION',
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
    'RECEPTION',
    'Clear all',
  ]) {
    assert.ok(html.includes(text), `missing ${text}`);
  }
  assert.equal(html.includes('Clear finished'), false);
  assert.ok(html.includes('data-testid="di-scan-attention"'));
  assert.ok(html.includes('>1<'));
});

test('mission details, the code diff, Herdr panes and comments open from the locked row', () => {
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
    'CODE DIFF',
    'HERDR',
    'COMMENTS',
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

  const herdr = renderToStaticMarkup(
    createElement(DiScanPanel, {
      rows: DI_SCAN_LOCK,
      initialSelectedId: DI_SCAN_LOCK[0]?.id,
      initialView: 'herdr',
      onMinimize: () => undefined,
    }),
  );
  assert.equal(herdr.includes('data-testid="di-scan-pane"'), true);
  assert.equal(herdr.includes('Focus terminal'), true);
  assert.equal((herdr.match(/data-testid="di-scan-pane"/g) ?? []).length, 4);

  const comments = renderToStaticMarkup(
    createElement(DiScanPanel, {
      rows: DI_SCAN_LOCK,
      initialSelectedId: DI_SCAN_LOCK[0]?.id,
      initialView: 'comments',
      onMinimize: () => undefined,
    }),
  );
  assert.ok(comments.includes('No comments are recorded for this mission.'));
  assert.equal(comments.includes('di-scan-tree'), false);
});

test('the product face is the scan panel unless a harness asks for the operational list', () => {
  assert.equal(officePanelPresentation({}), 'scan');
  assert.equal(officePanelPresentation({ e2e: true }), 'operational');
  assert.equal(officePanelPresentation({ face: 'operational' }), 'operational');
  assert.equal(officePanelPresentation({ face: 'scan', e2e: true }), 'scan');
});
