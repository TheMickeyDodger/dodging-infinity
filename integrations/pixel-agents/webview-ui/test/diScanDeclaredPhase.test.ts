/**
 * S3: the Missions table's Phase column shows the producer's DECLARED workflow phase, as the producer's OWN word, and draws NO step marks.
 *
 * The table's design draws four steps (Proposal, Build, Review, Delivery); the producer declares ten phases; no document maps one to the other, so any mapping would be a fabricated fact. Real rows therefore
 * show the declared phase as its own word (the producer's identifier, capitalised and spaced, nothing more), and the four-step bar stays the labelled specimen's alone. (The mismatch is a named design question.)
 *
 * Absences are different facts and read differently, at the two levels the source has them:
 *   inside the v2 block   `not_applicable` ("this kind of record has no phase": Mission Core is one) and `not_reported` ("the producer reported no phase": unknown, never "none"), and `observed` (the phase), which is
 *                         valid only for a workflow_authority record; an absent v2 field is NOT an observed phase;
 *   the block itself      a v1 office has no v2 block (`projected: false`, with no phase field), and the surface says "not provided by this office" from that absence, with no phase value of any kind.
 * Nothing is derived from the display-only lifecycle state, and no owner or repository is invented (the record holds neither: they stay "Not recorded").
 */
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';

import type { DiMission, DiOfficeState } from '../../core/src/messages.ts';
import {
  DI_PHASE_NOT_APPLICABLE,
  DI_PHASE_NOT_PROVIDED,
  DI_PHASE_NOT_REPORTED,
  type DiScanRow,
  phaseWord,
  scanRowsFromOffice,
} from '../src/components/diScanModel.ts';
import { DiScanPanel } from '../src/components/DiScanPanel.tsx';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const historical = JSON.parse(
  fs.readFileSync(path.join(root, 'test/fixtures/di-office-state-evidence-historical-v2.FIXTURE.json'), 'utf-8'),
) as { states: Record<'live', DiOfficeState> };

type Declared = NonNullable<DiMission['v2']['workflowPhase']>;
/** The real projection of the emitted COMPLETED Mission, with its one Mission's v2 block edited (a labelled synthetic variation: the wire shape itself is pinned by the server's diWorkflowPhaseWire test). */
function rowsWith(edit: (m: DiMission) => void): DiScanRow[] {
  const state = structuredClone(historical.states.live);
  for (const m of state.missions.missions) edit(m);
  return scanRowsFromOffice(state);
}
const declare = (w: Declared) => (m: DiMission) => {
  m.v2 = { ...m.v2, projected: true, workflowPhase: w };
};
const declaredOf = (rows: DiScanRow[]) => rows.map((r) => r.table?.declared);
const html = (rows: DiScanRow[]) =>
  renderToStaticMarkup(createElement(DiScanPanel, { rows, initialView: 'missions', onMinimize: () => undefined }));
const cells = (markup: string) => [...markup.matchAll(/<span[^>]*data-testid="di-phase-declared"[^>]*>[\s\S]*?<\/span><\/span>|<span[^>]*data-testid="di-phase-declared"[^>]*>[^<]*<span[^>]*>[^<]*<\/span>/g)].map((m) => m[0]);

describe('the declared phase reaches the row, state by state', () => {
  it('observed with a recognised phase: the producer\'s own word', () => {
    const rows = rowsWith(declare({ state: 'observed', phase: 'DISPATCHED', unrecognized: null, problem: null }));
    for (const d of declaredOf(rows)) expect(d).toEqual({ kind: 'word', word: 'Dispatched' });
  });

  it('the ten phases become their own words and nothing else: the identifier, spaced and capitalised', () => {
    const words = ['PLANNED', 'AUTHORIZED', 'WORKSPACE_READY', 'PREPARED', 'VALIDATED', 'DISPATCHED', 'VERIFIED', 'COMPLETED', 'BLOCKED', 'NEEDS_REAUTHORIZATION'].map(phaseWord);
    expect(words).toEqual(['Planned', 'Authorized', 'Workspace ready', 'Prepared', 'Validated', 'Dispatched', 'Verified', 'Completed', 'Blocked', 'Needs reauthorization']);
  });

  it('observed with a phase outside the vocabulary: shown as unrecognised, with its text when there is any, never as a known phase', () => {
    expect(declaredOf(rowsWith(declare({ state: 'observed', phase: null, unrecognized: 'SOMEDAY', problem: null })))[0]).toEqual({ kind: 'unrecognized', text: 'SOMEDAY' });
    expect(declaredOf(rowsWith(declare({ state: 'observed', phase: null, unrecognized: null, problem: null })))[0]).toEqual({ kind: 'unrecognized', text: null });
  });

  it('not_applicable (Mission Core): a real answer of its own, with the producer\'s reason, not an empty cell', () => {
    const d = declaredOf(rowsWith(declare({ state: 'not_applicable', phase: null, unrecognized: null, problem: 'lifecycle_model_has_no_workflow_phase' })))[0];
    expect(d).toEqual({ kind: 'not_applicable', problem: 'lifecycle_model_has_no_workflow_phase' });
  });

  it('not_reported: unknown, a different answer from not_applicable and from "not provided"', () => {
    expect(declaredOf(rowsWith(declare({ state: 'not_reported', phase: null, unrecognized: null, problem: null })))[0]).toEqual({ kind: 'not_reported' });
  });

  it('a v1 office (no v2 block: projected false, the field absent) is "not provided", read from the block\'s absence', () => {
    const rows = rowsWith((m) => {
      m.v2 = { projected: false, taskChains: [], childrenRecord: m.v2.childrenRecord, missionState: m.v2.missionState, canonicalReview: m.v2.canonicalReview };
    });
    for (const d of declaredOf(rows)) expect(d).toEqual({ kind: 'not_provided' });
  });

  it('a projected block whose phase field is missing (a server that predates the field) is "not provided", never a guessed state', () => {
    const rows = rowsWith((m) => {
      const v2 = { ...m.v2 };
      delete v2.workflowPhase;
      m.v2 = v2;
    });
    for (const d of declaredOf(rows)) expect(d).toEqual({ kind: 'not_provided' });
  });
});

describe('nothing is derived or invented', () => {
  it('a lifecycle that literally says DISPATCHED, with no phase reported, is NOT a Dispatched phase', () => {
    const rows = rowsWith((m) => {
      m.lifecycleState = 'DISPATCHED';
      declare({ state: 'not_reported', phase: null, unrecognized: null, problem: null })(m);
    });
    for (const d of declaredOf(rows)) expect(d?.kind).toBe('not_reported');
  });

  it('a declared phase that disagrees with the lifecycle literal is shown as declared', () => {
    const rows = rowsWith((m) => {
      m.lifecycleState = 'DISPATCHED';
      declare({ state: 'observed', phase: 'PLANNED', unrecognized: null, problem: null })(m);
    });
    for (const d of declaredOf(rows)) expect(d).toEqual({ kind: 'word', word: 'Planned' });
  });

  it('no step marks are drawn from real data: the four-step phase stays null on production rows', () => {
    const rows = rowsWith(declare({ state: 'observed', phase: 'DISPATCHED', unrecognized: null, problem: null }));
    for (const r of rows) expect(r.table?.phase).toBeNull();
    const markup = html(rows);
    expect(markup).not.toContain('di-phase-bar');
    expect(markup).not.toContain('di-phase-seg');
  });

  it('no owner or repository is invented: both stay "not recorded" next to a declared phase', () => {
    const rows = rowsWith(declare({ state: 'observed', phase: 'DISPATCHED', unrecognized: null, problem: null }));
    for (const r of rows) {
      expect(r.table?.owner).toBeNull();
      expect(r.table?.repos).toBeNull();
    }
  });
});

describe('the page: the three absences read differently', () => {
  const page = (w: Declared | null) =>
    html(
      rowsWith(
        w === null
          ? (m) => {
              m.v2 = { projected: false, taskChains: [], childrenRecord: m.v2.childrenRecord, missionState: m.v2.missionState, canonicalReview: m.v2.canonicalReview };
            }
          : declare(w),
      ),
    );
  const text = (markup: string) => markup.replace(/<[^>]+>/g, ' ').replace(/\s+/g, ' ');
  const cell = (markup: string) => /<span[^>]*data-testid="di-phase-declared"[^>]*data-kind="([a-z_]+)"[^>]*>([\s\S]*?)<\/span>(?:<span[^>]*>[^<]*<\/span>)?<\/span>/.exec(markup);

  it('a word is drawn as the word, with no explanation text beside it', () => {
    const markup = page({ state: 'observed', phase: 'WORKSPACE_READY', unrecognized: null, problem: null });
    expect(cell(markup)?.[1]).toBe('word');
    expect(text(markup)).toContain('Workspace ready');
  });

  it('not applicable, not reported and not provided are three different visible words, each with its own explanation for a person who cannot hover', () => {
    const notApplicable = page({ state: 'not_applicable', phase: null, unrecognized: null, problem: 'lifecycle_model_has_no_workflow_phase' });
    const notReported = page({ state: 'not_reported', phase: null, unrecognized: null, problem: null });
    const notProvided = page(null);
    expect(cell(notApplicable)?.[1]).toBe('not_applicable');
    expect(cell(notReported)?.[1]).toBe('not_reported');
    expect(cell(notProvided)?.[1]).toBe('not_provided');
    expect(text(notApplicable)).toContain(DI_PHASE_NOT_APPLICABLE);
    expect(text(notReported)).toContain(DI_PHASE_NOT_REPORTED);
    expect(text(notProvided)).toContain(DI_PHASE_NOT_PROVIDED);
    expect(new Set([DI_PHASE_NOT_APPLICABLE, DI_PHASE_NOT_REPORTED, DI_PHASE_NOT_PROVIDED]).size).toBe(3);
    // each says WHY in words, so the three stay different things to a person
    expect(text(notApplicable)).toContain('This kind of record has no workflow phase.');
    expect(text(notReported)).toContain('The producer did not report a workflow phase for this record.');
    expect(text(notProvided)).toContain('This office does not provide a workflow phase for its Missions.');
  });

  it('an unrecognised phase says so and carries its text as inert text', () => {
    const markup = page({ state: 'observed', phase: null, unrecognized: '<b>SOMEDAY</b>', problem: null });
    expect(cell(markup)?.[1]).toBe('unrecognized');
    expect(markup).not.toContain('<b>SOMEDAY</b>');
    expect(text(markup)).toContain('Unrecognized');
  });

  it('cells() finds the declared-phase cells the page draws (the helper is not vacuous)', () => {
    expect(cells(page({ state: 'not_reported', phase: null, unrecognized: null, problem: null })).length).toBeGreaterThan(0);
  });
});
