/**
 * Deliverable 1, review finding 6: Mission Core's OWN progress record (`mission_state.report.progress`, mission/observation.py; its vocabulary mission/state.py
 * PROGRESS_STATES) crosses the wire as `DiMissionStateSummary.progress`, so the office can reconcile it. Before this, projectReport (catalogProjection.ts) read many report
 * facts but not `progress`, so terminal progress was invisible: a Mission ABANDONED before dispatch kept its outer AWAITING_DECISION / AUTHORIZED state and read as a
 * nonterminal Not started.
 *
 * Over the hash-pinned contract-v2 emission (`catalog-answer-v2.fc409bbc.FIXTURE.json`, SYNTHETIC ids; fixture evidence only), through the real parser, office view and
 * message builder. The values are the producer's, verbatim; an out-of-vocabulary value stays visible as unrecognized text, never a guess. Without
 * `DI_EXPORT_OFFICE_STATE=1` the last test fails when the committed webview fixture drifts from the projection; with it, the fixture is (re)written.
 */
import * as crypto from 'crypto';
import * as fs from 'fs';
import * as path from 'path';
import { describe, expect, it } from 'vitest';

import type { DiMission, DiMissionProgress } from '../../core/src/messages.js';
import { MISSION_PROGRESS_STATES } from '../src/di/catalogContract.js';
import { parseEnvelope } from '../src/di/catalogProjection.js';
import { buildDiOfficeStateMessage } from '../src/di/officeStateMessage.js';
import { buildOfficeView } from '../src/di/officeView.js';
import type { ServeTimeVerdict } from '../src/di/serveTimeVerdict.js';
import { clone } from './helpers/diFixture.js';

type Raw = Record<string, unknown>;
const ROOT = path.join(__dirname, '..', '..');
const REPO = path.join(ROOT, '..', '..');
const FRESH: ServeTimeVerdict = { health: { known: true, value: 'fresh' }, problem: null };
const V2_REL = 'server/__tests__/fixtures/di-backend/v2/catalog-answer-v2.fc409bbc.FIXTURE.json';
const V2_SHA256 = 'fc409bbc41a6ee3a17253acebe85092a4d118511954fadf2c457ecc11b891556';
const V2_NOW = 1000000; // the fixture's own generated_at
const TARGET = 'webview-ui/test/fixtures/di-office-state-progress-abandoned-v2.FIXTURE.json';
const TARGET_UNAVAILABLE =
  'webview-ui/test/fixtures/di-office-state-progress-unavailable-v2.FIXTURE.json';
const TARGET_STANDING_NULL =
  'webview-ui/test/fixtures/di-office-state-progress-standing-null-v2.FIXTURE.json';
/** Mission Core, RUNNING, progress IN_PROGRESS. */
const RUNNING = 'synthetic_mission_core:mn-00000000000000000000000000000002';
/** Mission Core, AWAITING_DECISION (inactive), no start recorded, progress NOT_STARTED: the record abandonment-before-dispatch is made from. */
const UNSTARTED = 'synthetic_mission_core:mn-00000000000000000000000000000009';
/** A workflow store: it has no Mission State, so it has no Mission Core progress. */
const WORKFLOW = 'synthetic_workflow_store:wf-0005';

function loadV2(): { raw: Raw; bytes: Buffer } {
  const bytes = fs.readFileSync(path.join(ROOT, V2_REL));
  const digest = crypto.createHash('sha256').update(bytes).digest('hex');
  if (digest !== V2_SHA256) throw new Error(`v2 FIXTURE hash mismatch: ${digest}`);
  return { raw: JSON.parse(bytes.toString('utf-8')) as Raw, bytes };
}
const missionRaw = (raw: Raw, ref: string): Raw => {
  const found = ((raw.response as Raw).missions as Raw[]).find((m) => m.mission_ref === ref);
  if (!found) throw new Error(`no ${ref}`);
  return found;
};
const progressRaw = (raw: Raw, ref: string): Raw =>
  ((missionRaw(raw, ref).mission_state as Raw).report as Raw).progress as Raw;

function office(edit?: (raw: Raw) => void) {
  const raw = clone(loadV2().raw);
  edit?.(raw);
  const parsed = parseEnvelope(raw);
  if (!parsed.ok) throw new Error(`did not parse: ${parsed.verdict.detail}`);
  return buildDiOfficeStateMessage(buildOfficeView(parsed.envelope, FRESH, V2_NOW, 2), () => 41);
}
/** Project the edited answer through the real parser, office view and message builder, and export it (or check it for drift). */
function exportState(
  target: string,
  edit: (raw: Raw) => void,
  editWords: string,
  what: string,
): void {
  const { raw: original, bytes } = loadV2();
  const raw = clone(original);
  edit(raw);
  const parsed = parseEnvelope(raw);
  if (!parsed.ok) throw new Error('did not parse');
  const state = buildDiOfficeStateMessage(
    buildOfficeView(parsed.envelope, FRESH, V2_NOW, 2),
    () => 41,
  );
  expect(state.live).toBe(true);
  const doc = {
    fixture: true,
    label:
      `FIXTURE — the contract-v2 office state with ${what}, projected by the real server (parseEnvelope, buildOfficeView, buildDiOfficeStateMessage) ` +
      '— SYNTHETIC STATUS REGRESSION; evidences nothing about any live Mission',
    derived_from: {
      path: V2_REL,
      sha256: crypto.createHash('sha256').update(bytes).digest('hex'),
      edit: editWords,
    },
    generator: 'server/__tests__/diMissionProgressWire.test.ts',
    state,
  };
  const text = JSON.stringify(doc, null, 2) + '\n';
  const at = path.join(ROOT, target);
  if (process.env.DI_EXPORT_OFFICE_STATE === '1') {
    fs.mkdirSync(path.dirname(at), { recursive: true });
    fs.writeFileSync(at, text);
  }
  expect(fs.readFileSync(at, 'utf-8')).toBe(text);
}

const progressOf = (missions: readonly DiMission[], ref: string): DiMissionProgress | undefined => {
  const m = missions.find((x) => x.missionRef === ref);
  return m?.v2.projected ? m.v2.missionState.progress : undefined;
};

describe('Mission Core progress reaches the wire, verbatim', () => {
  it('the vocabulary is Mission Core’s own (mission/state.py PROGRESS_STATES), in its order', () => {
    const source = fs.readFileSync(path.join(REPO, 'mission/state.py'), 'utf-8');
    const consts = new Map(
      [...source.matchAll(/^(PROGRESS_[A-Z_]+) = "([A-Z_]+)"$/gm)].map((m) => [m[1]!, m[2]!]),
    );
    const body = /^PROGRESS_STATES = \(([\s\S]*?)\)\n/m.exec(source)?.[1] ?? '';
    const declared = [...body.matchAll(/\b(PROGRESS_[A-Z_]+)\b/g)].map((m) => consts.get(m[1]!));
    expect(declared).toEqual([...MISSION_PROGRESS_STATES]);
  });

  it('an observed progress is carried with its closure reason', () => {
    const missions = office().missions.missions;
    expect(progressOf(missions, RUNNING)).toEqual({
      state: 'observed',
      reason: null,
      standing: 'verified',
      progress: 'IN_PROGRESS',
      unrecognized: null,
      closureReason: null,
    });
    expect(progressOf(missions, UNSTARTED)?.progress).toBe('NOT_STARTED');
  });

  it('terminal progress is carried whatever the outer state says: ABANDONED on a record that still reads AWAITING_DECISION', () => {
    const missions = office((raw) => {
      progressRaw(raw, UNSTARTED).value = {
        progress: 'ABANDONED',
        closure_reason: 'abandoned_by_caller',
      };
    }).missions.missions;
    const m = missions.find((x) => x.missionRef === UNSTARTED)!;
    expect(m.lifecycleState).toBe('AWAITING_DECISION');
    expect(m.standing).toBe('inactive');
    expect(m.runStarted).toBe(false);
    expect(progressOf(missions, UNSTARTED)).toEqual({
      state: 'observed',
      reason: null,
      standing: 'verified',
      progress: 'ABANDONED',
      unrecognized: null,
      closureReason: 'abandoned_by_caller',
    });
  });

  it('an out-of-vocabulary progress stays visible as unrecognized text, never a guessed state', () => {
    const missions = office((raw) => {
      progressRaw(raw, UNSTARTED).value = { progress: 'PAUSED_FOREVER', closure_reason: null };
    }).missions.missions;
    expect(progressOf(missions, UNSTARTED)).toEqual({
      state: 'observed',
      reason: null,
      standing: 'verified',
      progress: null,
      unrecognized: 'PAUSED_FOREVER',
      closureReason: null,
    });
  });

  it('absent, null and not-applicable progress are each said as themselves, never as a state', () => {
    const missing = office((raw) => {
      delete ((missionRaw(raw, UNSTARTED).mission_state as Raw).report as Raw).progress;
    }).missions.missions;
    expect(progressOf(missing, UNSTARTED)).toMatchObject({
      state: 'unavailable',
      reason: 'progress_not_reported',
      progress: null,
    });
    const nulled = office((raw) => {
      progressRaw(raw, UNSTARTED).value = null;
    }).missions.missions;
    expect(progressOf(nulled, UNSTARTED)).toMatchObject({
      state: 'not_recorded',
      progress: null,
      unrecognized: null,
    });
    // a workflow store has no Mission State at all: its progress is not applicable, with the Mission State's own reason
    const wf = progressOf(office().missions.missions, WORKFLOW);
    expect(wf?.state).toBe('not_applicable');
    expect(wf?.progress).toBeNull();
  });

  it('review round 3, finding 3: a progress fact the SOURCE declares unavailable stays unavailable with its reason; a retained value is never promoted', () => {
    const declared = office((raw) => {
      const fact = progressRaw(raw, UNSTARTED);
      fact.standing = 'unavailable';
      fact.detail = 'mission_state_journal_unreadable';
      // the producer still carries the last value it had: retained, not current
      fact.value = { progress: 'ABANDONED', closure_reason: 'abandoned_by_caller' };
    }).missions.missions;
    expect(progressOf(declared, UNSTARTED)).toEqual({
      state: 'unavailable',
      reason: 'mission_state_journal_unreadable',
      standing: 'unavailable',
      progress: null,
      unrecognized: null,
      closureReason: null,
    });
    // with no detail of its own, the reason says it was the source that declared it unavailable
    const bare = office((raw) => {
      const fact = progressRaw(raw, UNSTARTED);
      fact.standing = 'unavailable';
      fact.detail = null;
    }).missions.missions;
    expect(progressOf(bare, UNSTARTED)).toMatchObject({
      state: 'unavailable',
      reason: 'progress_unavailable',
      standing: 'unavailable',
      progress: null,
    });
  });

  it('a progress fact whose standing is not a current one (unknown, missing) is never a confident observation either', () => {
    for (const standing of ['unknown', null, 'future_standing']) {
      const missions = office((raw) => {
        const fact = progressRaw(raw, UNSTARTED);
        fact.standing = standing;
        fact.value = { progress: 'ABANDONED', closure_reason: 'abandoned_by_caller' };
      }).missions.missions;
      expect(progressOf(missions, UNSTARTED), String(standing)).toMatchObject({
        state: 'unavailable',
        reason: 'progress_standing_not_current',
        standing,
        progress: null,
      });
    }
  });

  it('exports the source-declared-unavailable office state for the webview end-to-end regression', () => {
    exportState(
      TARGET_UNAVAILABLE,
      (raw) => {
        const fact = progressRaw(raw, UNSTARTED);
        fact.standing = 'unavailable';
        fact.detail = 'mission_state_journal_unreadable';
        fact.value = { progress: 'ABANDONED', closure_reason: 'abandoned_by_caller' };
      },
      `only ${UNSTARTED}'s mission_state.report.progress, to standing unavailable (detail mission_state_journal_unreadable) with a RETAINED value {progress: ABANDONED, closure_reason: abandoned_by_caller}`,
      'one Mission Core record whose Mission State progress the source declares UNAVAILABLE (a retained ABANDONED value beside it; its outer state AWAITING_DECISION, no start recorded)',
    );
  });

  it('review round 4, finding 3: exports a progress fact with NO standing (a retained ABANDONED beside it) for the webview end-to-end regression', () => {
    const missing = (raw: Raw) => {
      const fact = progressRaw(raw, UNSTARTED);
      delete fact.standing;
      fact.value = { progress: 'ABANDONED', closure_reason: 'abandoned_by_caller' };
    };
    // the wire, as the real projection produces it: unavailable, not current, no standing, no retained value; DISTINCT from progress_not_reported
    expect(progressOf(office(missing).missions.missions, UNSTARTED)).toEqual({
      state: 'unavailable',
      reason: 'progress_standing_not_current',
      standing: null,
      progress: null,
      unrecognized: null,
      closureReason: null,
    });
    exportState(
      TARGET_STANDING_NULL,
      missing,
      `only ${UNSTARTED}'s mission_state.report.progress: its standing removed, a RETAINED value {progress: ABANDONED, closure_reason: abandoned_by_caller} kept`,
      'one Mission Core record whose Mission State progress fact carries NO standing (a retained ABANDONED beside it; its outer state AWAITING_DECISION, no start recorded)',
    );
  });

  it('exports the abandoned-before-dispatch office state for the webview status regression', () => {
    exportState(
      TARGET,
      (raw) => {
        progressRaw(raw, UNSTARTED).value = {
          progress: 'ABANDONED',
          closure_reason: 'abandoned_by_caller',
        };
        // Deliverable 2: the backend's retention reading of exactly this shape (mission_retention.eligibility: terminal
        // progress, no run intent, no contract activation at its revision; proven on the real stores by
        // tests/test_mission_retention.py ProgressClosureTests and CatalogRetentionTests)
        // a SYNTHETIC store identity (the digest the catalog reports for its Mission Core store), so the owner's delete names its store (target identity)
        (missionRaw(raw, UNSTARTED).native as Raw).store_identity = 'c'.repeat(64);
        missionRaw(raw, UNSTARTED).retention = {
          state: 'observed', problem: null, eligible: true, basis: 'no_start_recorded',
          basis_source: 'run.intent = null', reason: null, phase: 'intact', phase_at: null,
          summary: null, retained_for: 0,
        };
      },
      `only ${UNSTARTED}: mission_state.report.progress.value, to {progress: ABANDONED, closure_reason: abandoned_by_caller} (mission/state.py PROGRESS_ABANDONED, CLOSURE_REASON_CALLER_ABANDONED); its retention reading, eligible on no_start_recorded, and a synthetic native.store_identity of 64 'c' (Deliverable 2)`,
      'one Mission Core record ABANDONED before dispatch (its outer state still AWAITING_DECISION, no start recorded), eligible to archive by the backend\'s retention reading',
    );
  });
});

// Deliverable 2, the capacity seam: the backend's retention and the office decide a terminal BLOCKED workflow from the SAME durable receipts.
// mission_sources/sources.py emits `native.sent_handoff_receipts` (the runtime's dispatch receipts, counted by its own marker; pinned by
// tests/test_mission_retention.py E10), and the office reads it verbatim as `DiMission.sentHandoffReceipts`.
const BLOCKED_NEVER_SENT = 'webview-ui/test/fixtures/di-office-state-workflow-blocked-never-sent-v2.FIXTURE.json';
const BLOCKED_AFTER_SEND = 'webview-ui/test/fixtures/di-office-state-workflow-blocked-after-send-v2.FIXTURE.json';
const blockedWorkflow = (raw: Raw, receipts: number) => {
  const m = missionRaw(raw, WORKFLOW);
  m.lifecycle = { model: 'workflow_authority.workflow_phase/2', standing: 'terminal', state: 'BLOCKED' };
  m.workflow_phase = { state: 'observed', problem: null, phase: 'BLOCKED' };
  // exactly what the catalog's roster projects for a non-active record (mission_sources/roster.py: binding not_applicable `mission_not_active`, no members)
  m.binding = { reason: 'mission_not_active', state: 'not_applicable', task_id: null };
  m.members = [];
  (m.native as Raw).sent_handoff_receipts = receipts;
  // the backend's retention reading of exactly this record (mission_retention/eligibility.py `workflow`)
  m.retention =
    receipts === 0
      ? { state: 'observed', problem: null, eligible: true, basis: 'workflow_not_dispatched',
          basis_source: 'receipts: no dispatch receipt', reason: null, phase: 'intact', phase_at: null,
          summary: null, retained_for: 0 }
      : { state: 'observed', problem: null, eligible: false, basis: null, basis_source: null,
          reason: 'no_positive_basis', phase: 'intact', phase_at: null, summary: null, retained_for: 0 };
};

describe('Deliverable 2: a workflow\'s durable sent-handoff receipts reach the wire, verbatim', () => {
  const of = (edit: (raw: Raw) => void) => office(edit).missions.missions.find((m) => m.missionRef === WORKFLOW)!;
  it('the count is carried as the catalog reports it, 0 included', () => {
    for (const n of [0, 1, 3])
      expect(of((raw) => { (missionRaw(raw, WORKFLOW).native as Raw).sent_handoff_receipts = n; }).sentHandoffReceipts).toBe(n);
  });
  it('absent (an older producer), negative or not an integer: null, never 0', () => {
    expect(of(() => undefined).sentHandoffReceipts).toBeNull();
    for (const bad of [-1, 1.5, '0', true, null])
      expect(of((raw) => { (missionRaw(raw, WORKFLOW).native as Raw).sent_handoff_receipts = bad; }).sentHandoffReceipts).toBeNull();
  });
  it('a Mission Core Mission carries none, whatever its native block holds', () => {
    const missions = office((raw) => { (missionRaw(raw, UNSTARTED).native as Raw).sent_handoff_receipts = 0; }).missions.missions;
    expect(missions.find((m) => m.missionRef === UNSTARTED)!.sentHandoffReceipts).toBeNull();
  });
  it('exports the terminal BLOCKED workflow that never sent a handoff, for the webview capacity regression', () => {
    exportState(
      BLOCKED_NEVER_SENT,
      (raw) => blockedWorkflow(raw, 0),
      `only ${WORKFLOW}: lifecycle to {standing: terminal, state: BLOCKED}, workflow_phase observed BLOCKED, binding not_applicable mission_not_active with no members (the roster's non-active projection), native.sent_handoff_receipts = 0, and its retention reading, eligible on workflow_not_dispatched (Deliverable 2)`,
      'one workflow-store record BLOCKED before any handoff was sent (no durable dispatch receipt), eligible to archive by the backend\'s retention reading',
    );
  });
  it('exports its counterexample, BLOCKED after a handoff was sent', () => {
    exportState(
      BLOCKED_AFTER_SEND,
      (raw) => blockedWorkflow(raw, 1),
      `only ${WORKFLOW}: lifecycle to {standing: terminal, state: BLOCKED}, workflow_phase observed BLOCKED, binding not_applicable mission_not_active with no members (the roster's non-active projection), native.sent_handoff_receipts = 1, and its retention reading, refused on no_positive_basis (Deliverable 2)`,
      'one workflow-store record BLOCKED AFTER a handoff was sent (one durable dispatch receipt): no positive basis on either side',
    );
  });
});
