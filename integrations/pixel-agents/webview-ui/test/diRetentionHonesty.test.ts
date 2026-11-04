/**
 * Deliverable 2, round 10, the CLASS of defect: a third state (not read, unreadable, interrupted, not reported) never folds into a confident one. Rendered on the
 * real projection of an archived Mission Core Mission (di-office-state-progress-abandoned-v2) and on the retained-only archive, with retained facts AS THE REAL
 * PRODUCERS MAKE THEM (di-retained-fact-states.FIXTURE.json, exported by tests/test_mission_retention.py FS4: the CI producer's three claims, and a version-1 summary
 * as the store upgrades it). Each case is one the earlier tests could not reach: the Mission's own details with an interrupted phase, and an EMPTY listed store.
 */
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';

import type { DiOfficeState, DiRetainedSource, DiRetentionFact, DiRetentionSummary } from '../../core/src/messages.ts';
import { scanRowsFromOffice } from '../src/components/diScanModel.ts';
import { LISTED_RETENTION_PHASE, RETENTION_FACT_WORDS } from '../src/components/diScanPagesModel.ts';
import { DiScanPanel } from '../src/components/DiScanPanel.tsx';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const read = (name: string) => JSON.parse(fs.readFileSync(path.join(root, 'test/fixtures', name), 'utf-8')) as Record<string, unknown>;
const office = (read('di-office-state-progress-abandoned-v2.FIXTURE.json') as { state: DiOfficeState }).state;
const produced = read('di-retained-fact-states.FIXTURE.json') as {
  facts: Record<'ci_not_read' | 'ci_unreadable' | 'ci_confirmed_absent', DiRetentionFact>;
  upgraded_v1_summary: { final_state: string; final_state_source: string } & Record<'verdict' | 'ci' | 'comments', DiRetentionFact>;
};
const REF = 'synthetic_mission_core:mn-00000000000000000000000000000009';
const noop = () => undefined;
const CONFIDENT_ABSENCE = /keeps no such record|this office keeps no/;

/** A summary in wire shape (the backend's keys renamed exactly as the server's projection renames them; no value touched). */
const summaryWith = (ci: DiRetentionFact, extra: Partial<DiRetentionSummary> = {}): DiRetentionSummary => ({
  finalState: 'COMPLETED',
  finalStateSource: 'missions.json missions[mn-…].state',
  verdict: produced.facts.ci_confirmed_absent,
  ci,
  comments: produced.facts.ci_confirmed_absent,
  ...extra,
});
const upgraded: DiRetentionSummary = {
  finalState: produced.upgraded_v1_summary.final_state,
  finalStateSource: produced.upgraded_v1_summary.final_state_source,
  verdict: produced.upgraded_v1_summary.verdict,
  ci: produced.upgraded_v1_summary.ci,
  comments: produced.upgraded_v1_summary.comments,
};

/** The archived Mission's OWN details (the surface the person acts from), with its retention phase and summary set. */
function details(phase: 'reclaiming' | 'reclaimed' | 'deleting' | 'deleted', summary: DiRetentionSummary | null): string {
  const state = structuredClone(office);
  const mission = state.missions.missions.find((m) => m.missionRef === REF)!;
  mission.retention = { ...mission.retention!, phase, phaseAt: 100, summary };
  return renderToStaticMarkup(
    createElement(DiScanPanel, { rows: scanRowsFromOffice(state), initialView: 'mission', initialSelectedId: REF, onMinimize: noop, onDeleteLocalData: noop }),
  );
}
const retention = (html: string) => /data-testid="di-scan-retention"[\s\S]*?(?=<p class="di-scan-stage-note" id="di-scan-trash-note")/.exec(html)?.[0] ?? '';

describe('a retained fact says which claim it makes: not read, unreadable and confirmed absence never collapse', () => {
  for (const [name, fact, words] of [
    ['not configured', produced.facts.ci_not_read, RETENTION_FACT_WORDS.not_read],
    ['unreadable pr_delivery.json', produced.facts.ci_unreadable, RETENTION_FACT_WORDS.unreadable],
    ['confirmed absence', produced.facts.ci_confirmed_absent, RETENTION_FACT_WORDS.not_recorded_locally],
  ] as const) {
    it(`${name}: on the Mission's own details, the CI fact reads "${words.split(':')[0]}"`, () => {
      const html = retention(details('reclaimed', summaryWith(fact)));
      expect(html).toMatch(new RegExp(`data-fact="ci" data-state="${fact.state}">CI results: ${words.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}`));
      if (fact.state !== 'not_recorded_locally') expect(html).not.toMatch(/CI results: None recorded/);
      expect(html).not.toMatch(CONFIDENT_ABSENCE);
    });
  }
  it('unreadable is never said beside a claim of absence: the reviewer’s reproduction', () => {
    const html = retention(details('reclaimed', summaryWith(produced.facts.ci_unreadable)));
    expect(html).toMatch(/could not be read/);
    expect(html).toMatch(/CI results: Could not be read: a record may exist/);
    expect(html).not.toMatch(CONFIDENT_ABSENCE);
  });
  it('a summary kept by version 1 renders honestly: its unestablished absences read Not known, never None recorded', () => {
    const html = retention(details('reclaimed', upgraded));
    expect(html).toMatch(/data-fact="verdict" data-state="not_known">Verdict: Not known/);
    expect(html).toMatch(/data-fact="ci" data-state="unreadable">CI results: Could not be read/);
    expect(html).not.toMatch(/None recorded/);
  });
  it('the retained-only archive reads the same claims, from the same table', () => {
    const reading: DiRetainedSource[] = [
      {
        sourceId: 'mc', state: 'observed', problem: null, total: 1, awaiting: 0,
        missions: [{ missionRef: 'mc:mn-' + '4'.repeat(32), sourceId: 'mc', nativeId: 'mn-' + '4'.repeat(32), family: 'mission_core', phase: 'reclaimed', phaseAt: 9, summary: upgraded }],
      },
    ];
    const html = renderToStaticMarkup(
      createElement(DiScanPanel, { rows: scanRowsFromOffice(office), initialView: 'missions', initialMissionsStatus: 'archived', onMinimize: noop, retained: reading }),
    );
    expect(html).toMatch(/data-fact="verdict" data-state="not_known"><dt>Verdict<\/dt><dd>Not known/);
    expect(html).toMatch(/data-fact="ci" data-state="unreadable"><dt>CI results<\/dt><dd>Could not be read/);
    expect(html).not.toMatch(CONFIDENT_ABSENCE);
  });
});

describe('an interrupted reclamation or deletion is said as interrupted, on the details AND where the person acts, never as done', () => {
  it('reclaiming: started, not finished, retried; never "reclaimed" or "no longer kept"', () => {
    const html = details('reclaiming', summaryWith(produced.facts.ci_confirmed_absent));
    const note = retention(html);
    expect(note).toContain('data-phase="reclaiming" data-finished="false"');
    expect(note).toContain(LISTED_RETENTION_PHASE.reclaiming.words);
    expect(note).not.toMatch(/Reclaimed 30 days|are no longer kept here/);
  });
  it('deleting: started, not finished, retried; never "was deleted", and the delete control says the same', () => {
    const html = details('deleting', null);
    const note = retention(html);
    expect(note).toContain('data-phase="deleting" data-finished="false"');
    expect(note).toContain(LISTED_RETENTION_PHASE.deleting.words);
    expect(note).not.toMatch(/was deleted|were deleted|Deleted by its owner/);
    expect(/data-testid="di-scan-trash-reason"[^>]*>([^<]*)</.exec(html)?.[1]).toMatch(/has started and is not finished/);
  });
  it('the finished phases say finished', () => {
    expect(retention(details('reclaimed', summaryWith(produced.facts.ci_confirmed_absent)))).toContain('data-finished="true"');
    expect(retention(details('deleted', null))).toContain('data-finished="true"');
  });
  it('in the retained-only archive too: reclaiming is said as started and retried', () => {
    const html = renderToStaticMarkup(
      createElement(DiScanPanel, {
        rows: scanRowsFromOffice(office), initialView: 'missions', initialMissionsStatus: 'archived', onMinimize: noop,
        retained: [{ sourceId: 'mc', state: 'observed', problem: null, total: 1, awaiting: 0, missions: [{ missionRef: 'mc:mn-' + '5'.repeat(32), sourceId: 'mc', nativeId: 'mn-' + '5'.repeat(32), family: 'mission_core', phase: 'reclaiming', phaseAt: 9, summary: summaryWith(produced.facts.ci_confirmed_absent) }] }],
      }),
    );
    expect(html).toMatch(/data-testid="di-missions-retained-phase">Its reclamation started and is not finished: its summary is kept, and the next retention pass retries the rest/);
  });
});

describe('an EMPTY listed store says what the archive allows: "No Missions yet" only when it was read in full and holds none', () => {
  const emptyPage = (retained: readonly DiRetainedSource[] | null, status: 'all' | 'archived' = 'all') =>
    renderToStaticMarkup(createElement(DiScanPanel, { rows: [], initialView: 'missions', initialMissionsStatus: status, onMinimize: noop, retained }));
  const empty = (html: string) => /data-testid="di-missions-empty" data-kind="([a-z_]+)">([^<]*)</.exec(html);
  const chip = (html: string) => /data-testid="di-missions-status-archived"[\s\S]*?data-testid="di-missions-count"[^>]*>([^<]*)</.exec(html)?.[1];
  const observedEmpty: DiRetainedSource[] = [{ sourceId: 'mc', state: 'observed', problem: null, total: 0, awaiting: 0, missions: [] }];

  for (const status of ['all', 'archived'] as const) {
    it(`${status}: the archive NOT reported: the message agrees with the "not reported" chip, never "No Missions yet"`, () => {
      const html = emptyPage(null, status);
      expect(empty(html)?.[1]).toBe('archive_not_reported');
      expect(empty(html)?.[2]).toMatch(/whether any finished Missions are kept is not known/);
      expect(html).not.toContain('No Missions yet');
      expect(chip(html)).toBe('not reported');
    });
    it(`${status}: the archive could NOT be read: unavailable, with its reason`, () => {
      const html = emptyPage([{ sourceId: 'mc', state: 'unavailable', problem: 'mission_retention_unreadable', total: null, awaiting: null, missions: [] }], status);
      expect(empty(html)?.[1]).toBe('archive_unavailable');
      expect(empty(html)?.[2]).toMatch(/the archive of mc \(mission_retention_unreadable\) could not be read/);
      expect(html).not.toContain('No Missions yet');
      expect(chip(html)).toBe('unavailable');
    });
    it(`${status}: the archive PRESENT and EMPTY: the genuine confident answer, "No Missions yet", and a 0`, () => {
      const html = emptyPage(observedEmpty, status);
      expect(empty(html)?.[1]).toBe('no_missions');
      expect(empty(html)?.[2]).toContain('No Missions yet.');
      expect(chip(html)).toBe('0');
    });
  }
  it('the archive HOLDING Missions with nothing listed: never "No Missions yet"', () => {
    const holding: DiRetainedSource[] = [{ ...observedEmpty[0]!, total: 1, missions: [{ missionRef: 'mc:mn-' + '6'.repeat(32), sourceId: 'mc', nativeId: 'mn-' + '6'.repeat(32), family: 'mission_core', phase: 'reclaimed', phaseAt: 9, summary: upgraded }] }];
    const all = emptyPage(holding, 'all');
    expect(empty(all)?.[1]).toBe('archive_only');
    expect(all).not.toContain('No Missions yet');
    expect(chip(all)).toBe('1');
  });
});
