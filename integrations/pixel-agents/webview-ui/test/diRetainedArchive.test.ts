/**
 * Deliverable 2, the retained-only archive, RENDERED (the Missions page, Archived), on the office states the REAL server projects from the REAL catalog's archive
 * readings (di-office-state-archive-{observed,unavailable}-v2, exported by server/__tests__/diRetentionArchiveWire.test.ts from readings
 * tests/test_mission_retention.py CR4 and AC5 record). What is asserted is what a person reads: the status chip in the SAME closed vocabulary the Mission had while
 * listed, its recorded final state as provenance only, its verdict / CI / comments each with its honest state, every control disabled with why, and counts that
 * never double-count and never read as a confident number over an archive that could not be read.
 */
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';

import type { DiMission, DiOfficeState, DiRetainedSource } from '../../core/src/messages.ts';
import { DI_MISSION_STATUS, missionStatus } from '../src/components/diMissionStatus.ts';
import { scanRowsFromOffice } from '../src/components/diScanModel.ts';
import { missionsShown, RETENTION_FACT_WORDS } from '../src/components/diScanPagesModel.ts';
import { DiScanPanel } from '../src/components/DiScanPanel.tsx';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const officeState = (name: string): DiOfficeState =>
  (JSON.parse(fs.readFileSync(path.join(root, 'test/fixtures', name), 'utf-8')) as { state: DiOfficeState }).state;
const observed = officeState('di-office-state-archive-observed-v2.FIXTURE.json');
const unavailable = officeState('di-office-state-archive-unavailable-v2.FIXTURE.json');
const noop = () => undefined;

const page = (state: DiOfficeState, retained: readonly DiRetainedSource[] | null = state.missions.retained ?? null) =>
  renderToStaticMarkup(
    createElement(DiScanPanel, {
      rows: scanRowsFromOffice(state),
      initialView: 'missions',
      initialMissionsStatus: 'archived',
      onMinimize: noop,
      retained,
    }),
  );
const archivedChip = (html: string) =>
  /data-testid="di-missions-status-archived"[\s\S]*?data-testid="di-missions-count"[^>]*>([^<]*)</.exec(html)?.[1];
const item = (html: string, ref: string) => {
  const at = html.indexOf(`data-testid="di-missions-retained-item" data-mission-ref="${ref}"`);
  expect(at, ref).toBeGreaterThan(-1);
  return html.slice(at, html.indexOf('</li>', at));
};
const listedArchived = (state: DiOfficeState) => missionsShown(scanRowsFromOffice(state), 'archived').length;

/** What a LISTED terminal workflow with this phase shows as its status (the chip it had before it left its store). */
function listedLabel(phase: 'COMPLETED' | 'BLOCKED'): string {
  const state = structuredClone(observed);
  const m = structuredClone(state.missions.missions[0]!) as DiMission;
  m.sourceKind = 'workflow_store';
  m.lifecycleModel = 'workflow_authority.workflow_phase/2';
  m.standing = 'terminal';
  m.lifecycleState = phase;
  if (m.v2.projected) m.v2.workflowPhase = { state: 'observed', phase, unrecognized: null, problem: null };
  return missionStatus(m, state).label;
}

describe('the retained-only archive, rendered: one vocabulary, honest facts, read-only, exact counts', () => {
  const html = page(observed);
  const REF_DONE = 'synthetic_workflow_store:wf-done';
  const REF_NEVER = 'synthetic_workflow_store:wf-never';

  it('each Mission gone from its store shows the SAME status chip it had while listed; its literal is provenance, never the status', () => {
    const done = item(html, REF_DONE);
    const never = item(html, REF_NEVER);
    expect(done).toContain(`data-status="ended"`);
    expect(done).toContain(`${DI_MISSION_STATUS.ended.glyph}</span> ${listedLabel('COMPLETED')}`);
    expect(never).toContain(`data-status="stopped"`);
    expect(never).toContain(`${DI_MISSION_STATUS.stopped.glyph}</span> ${listedLabel('BLOCKED')}`);
    expect(done).toMatch(/data-testid="di-missions-retained-provenance">Recorded final state COMPLETED \(workflows\.json workflows\[wf-done\]\.phase/);
    expect(done).not.toMatch(/final status COMPLETED/);
  });
  // CHANGED IN Deliverable 2, round 10: `not_recorded_locally` is now CONFIRMED absence only (its source was read and holds none: here a configured, readable
  // pr_delivery.json with no delivery for wf-done), and its words say exactly that; not read, unreadable and not known are their own states (diRetentionHonesty).
  // The protection kept: each fact says its honest state, never blank, and a confirmed absence is still said as one.
  it('its verdict, CI results and comments each say their honest state: recorded detail, or a CONFIRMED absence said as one, never blank', () => {
    const none = RETENTION_FACT_WORDS.not_recorded_locally;
    const done = item(html, REF_DONE);
    expect(done).toMatch(/data-fact="verdict" data-state="recorded"><dt>Verdict<\/dt><dd>reviewer HERD_DECISION: APPROVE/);
    expect(done).toMatch(/data-fact="comments" data-state="recorded"><dt>Comments<\/dt><dd>[^<]*no blocker remains/);
    expect(done).toMatch(new RegExp(`data-fact="ci" data-state="not_recorded_locally"><dt>CI results</dt><dd>${none} <span`));
    const never = item(html, REF_NEVER);
    for (const fact of ['verdict', 'ci', 'comments'])
      expect(never).toMatch(new RegExp(`data-fact="${fact}" data-state="not_recorded_locally"><dt>[^<]+</dt><dd>${none} <span`));
    // and none of them is said as a source that could not be read, or was not consulted
    expect(done + never).not.toMatch(/Could not be read|Not read:|Not known:/);
  });
  it('its phase is said apart from intact, and every control is drawn disabled with why; its delete reason is that it has left its store', () => {
    const done = item(html, REF_DONE);
    expect(done).toContain('data-phase="reclaimed"');
    expect(done).toMatch(/Reclaimed 30 days after it finished; only its summary is kept/);
    const actions = [...done.matchAll(/<button[^>]*data-testid="di-missions-retained-action"[^>]*>/g)].map((m) => m[0]);
    expect(actions).toHaveLength(4);
    for (const action of actions) expect(action).toMatch(/disabled=""/);
    expect(done).toMatch(/Delete local data: Its record is no longer in this office’s store, so it cannot be deleted from here/);
    expect(done).toMatch(/Start: Its record is no longer in this office’s store, so nothing can start, run, deliver or open it from here/);
    expect(done).toContain('<details>');
  });
  it('the Archived chip counts the listed archived Missions AND those gone from their store, exactly, with no plus', () => {
    expect(archivedChip(html)).toBe(String(listedArchived(observed) + 2));
  });
});

describe('archive health, rendered: an archive that could not be read is said, never a confident zero', () => {
  it('an emptied store whose retention store is malformed: its reason shown, and the chip is not a bare number', () => {
    const html = page(unavailable);
    expect(html).toMatch(/data-testid="di-missions-archive-unavailable" data-source="synthetic_workflow_store">The archive of synthetic_workflow_store could not be read \(mission_retention_unreadable\)/);
    expect(html).toMatch(/not known here: not none/);
    const chip = archivedChip(html)!;
    expect(chip).not.toMatch(/^\d+$/);
    expect(chip === 'unavailable' || /^\d+\+$/.test(chip)).toBe(true);
    expect(html).not.toMatch(/No Missions match this filter/);
  });
  it('not reported at all: said, and never counted as none', () => {
    const html = page(observed, null);
    expect(html).toMatch(/data-testid="di-missions-archive-not-reported"/);
    const chip = archivedChip(html)!;
    expect(chip).not.toMatch(/^\d+$/);
  });
});

describe('the displayed counts: disjoint populations, never added twice', () => {
  const source = observed.missions.retained![0]!;
  const withCounts = (patch: Partial<DiRetainedSource>): DiRetainedSource[] => [{ ...structuredClone(source), ...patch }];

  it('an ending already represented by a retained record is counted ONCE (in total, never again as awaiting)', () => {
    const one = withCounts({ total: 1, awaiting: 0, missions: [structuredClone(source.missions[0]!)] });
    const html = page(observed, one);
    expect(archivedChip(html)).toBe(String(listedArchived(observed) + 1));
    expect([...html.matchAll(/data-testid="di-missions-retained-item"/g)]).toHaveLength(1);
    expect(html).not.toMatch(/di-missions-archive-awaiting/);
  });
  it('an absent ending with no retained record yet is counted, and shown as awaiting its summary', () => {
    const html = page(observed, withCounts({ total: 2, awaiting: 1 }));
    expect(archivedChip(html)).toBe(String(listedArchived(observed) + 3));
    expect(html).toMatch(/data-testid="di-missions-archive-awaiting">1 Mission was removed from its store and awaits its summary; capture is retried\./);
  });
  it('a retained item that is ALSO still listed is shown and counted with the listed rows only', () => {
    const state = structuredClone(observed);
    const listedRef = state.missions.missions[0]!.missionRef;
    const twin = { ...structuredClone(source.missions[0]!), missionRef: listedRef };
    const html = page(state, withCounts({ total: 3, awaiting: 0, missions: [twin, ...structuredClone(source.missions)] }));
    expect(html).not.toContain(`data-testid="di-missions-retained-item" data-mission-ref="${listedRef}"`);
    expect(archivedChip(html)).toBe(String(listedArchived(state) + 2));
  });
  it('counted beyond what a source lists: said, never dropped', () => {
    const html = page(observed, withCounts({ total: 5, awaiting: 0 }));
    expect(archivedChip(html)).toBe(String(listedArchived(observed) + 5));
    expect(html).toMatch(/data-testid="di-missions-archive-unlisted">3 more are kept but not listed here/);
  });
});

describe('the producer\u2019s own final-state shapes (generated by _mission_core_summary) read their canonical status, never Unrecognised', () => {
  type Backend = { final_state: string; final_state_source: string } & Record<'verdict' | 'ci' | 'comments', { state: string; detail: string | null; source: string }>;
  const backend = (
    JSON.parse(fs.readFileSync(path.join(root, 'test/fixtures/di-retained-summaries.FIXTURE.json'), 'utf-8')) as { summaries: Record<string, Backend> }
  ).summaries;
  // the backend's own keys, renamed exactly as the server's projection renames them (server/src/di/catalogProjection.ts projectRetentionSummary); no value is touched
  const summaries = Object.fromEntries(
    Object.entries(backend).map(([name, b]) => [
      name,
      { finalState: b.final_state, finalStateSource: b.final_state_source, verdict: b.verdict, ci: b.ci, comments: b.comments },
    ]),
  ) as Record<string, NonNullable<DiRetainedSource['missions'][number]['summary']>>;
  const reading = (summary: DiRetainedSource['missions'][number]['summary'], phase = 'reclaimed', nativeId = 'mn-' + '7'.repeat(32)): DiRetainedSource[] => [
    {
      sourceId: 'mc',
      state: 'observed',
      problem: null,
      total: 1,
      awaiting: 0,
      missions: [{ missionRef: `mc:${nativeId}`, sourceId: 'mc', nativeId, family: 'mission_core', phase, phaseAt: 9, summary }],
    },
  ];
  const chipOf = (html: string) => /data-testid="di-missions-retained-status" data-status="([a-z_]+)"/.exec(html)?.[1];

  for (const [name, expected] of [
    ['bare_completed', 'ended'],
    ['bare_rejected', 'ended'],
    ['bare_cancelled', 'stopped'],
    ['withdrawn', 'ended'],
    ['progress_completed', 'ended'],
    ['progress_closed_unsuccessful', 'stopped'],
    ['progress_abandoned', 'stopped'],
  ] as const) {
    it(`${name} (“${summaries[name]!.finalState}”) reads ${expected}, its text kept as provenance only`, () => {
      const html = page(observed, reading(summaries[name]!));
      expect(chipOf(html)).toBe(expected);
      expect(html).not.toMatch(/di-missions-retained-status-reason/);
      expect(html).toContain(`Recorded final state ${summaries[name]!.finalState} (`);
    });
  }
  it('COMPLETED Mission State progress is Ended, NEVER Verified: reported completion is not verified success', () => {
    const html = page(observed, reading(summaries.progress_completed!));
    expect(chipOf(html)).not.toBe('verified');
    expect(html).not.toContain(DI_MISSION_STATUS.verified.label);
  });
  it('a withdrawn proposal reads closed (Ended), never as an open decision', () => {
    const html = page(observed, reading(summaries.withdrawn!));
    expect(chipOf(html)).toBe('ended');
    const status = /data-testid="di-missions-retained-status"[^>]*>([\s\S]*?)<\/span> <span class="di-missions-title"/.exec(item(html, `mc:mn-${'7'.repeat(32)}`))?.[1] ?? '';
    expect(status).toContain(DI_MISSION_STATUS.ended.label);
    expect(status).not.toMatch(/Not started|Awaiting|Waiting/);
  });
  it('progress closure reads the SAME chip as the real listed record of that shape (the abandoned-before-dispatch projection)', () => {
    const abandoned = officeState('di-office-state-progress-abandoned-v2.FIXTURE.json');
    const listed = scanRowsFromOffice(abandoned).find((r) => r.id === 'synthetic_mission_core:mn-00000000000000000000000000000009')!;
    const html = page(observed, reading(summaries.progress_abandoned!));
    expect(html).toContain(`</span> ${listed.status!.label}</span>`);
  });
  it('a genuinely unknown final state is Unrecognised, WITH its reason', () => {
    const html = page(observed, reading({ ...summaries.bare_completed!, finalState: 'SOMETHING_ELSE (not a producer shape)' }));
    expect(chipOf(html)).toBe('unrecognised');
    expect(html).toMatch(/data-testid="di-missions-retained-status-reason">This office does not know the recorded final state “SOMETHING_ELSE \(not a producer shape\)” for a mission_core Mission, so its status is not guessed\./);
  });
  it('deleting: the disabled delete says started and retrying, never already deleted; deleted: already deleted', () => {
    const deleting = page(observed, reading(null, 'deleting'));
    expect(deleting).toMatch(/Delete local data: Its owner’s deletion has started and is not finished: its summary is already removed, and the next retention pass retries the remaining cleanup\./);
    expect(deleting).not.toMatch(/were already deleted/);
    expect(deleting).toMatch(/the next retention pass retries the rest/);
    const deleted = page(observed, reading(null, 'deleted'));
    expect(deleted).toMatch(/Delete local data: Its local data and its summary were already deleted at its owner’s request\./);
  });
});
