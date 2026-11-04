/**
 * Deliverable 1: ONE closed, source-qualified Mission status contract (diMissionStatus.ts).
 *
 * Pinned here, over the office state the REAL server projects from a labelled packaged fixture (FIXTURE, not live), each case changing only the fields it is about:
 *  - the vocabulary is closed: every status has its id, label, shape, plain meaning and terminal flag, and no two share a label or a shape;
 *  - every declared mapping is DATA over the producer's own closed vocabulary, pinned against the producer's source (Python) so a new literal cannot slip past it;
 *  - the source of truth and its precedence: the office's liveness, then standing, then reported completion, then terminal literals, then the team, the declared phase and the
 *    run-start fact; sources that disagree read Unknown with both cited, never the stronger reading;
 *  - NEVER STARTED is its own state: a Mission whose run never started never reads Running, Start requested or Verified;
 *  - not read, not known, stale, unrecognised and unavailable stay apart, each with its own reason, and none becomes a confident status.
 */
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

import { describe, test } from 'vitest';

import type { DiMission, DiOfficeState } from '../../core/src/messages.ts';
import { JOURNEY, MR_PROGRESSION_STATES } from '../src/components/diMissionRequestModel.ts';
import {
  capacityReleaseBasis,
  DI_BOUND_TASK_STATUS,
  DI_CAPACITY_RELEASE,
  DI_COMPLETION_NOT_AFFIRMED,
  DI_MEMBER_ACTIVITY_WORK,
  DI_MISSION_PROGRESS_STATUS,
  DI_MISSION_STATUS,
  DI_MISSION_STATUS_IDS,
  DI_PROGRESSION_PROBE_STATUS,
  DI_REQUEST_STAGE_STATUS,
  DI_RUN_STARTED_MEANING,
  DI_STANDING_STATUS,
  DI_STATUS_CAPACITY,
  DI_TERMINAL_LITERAL_STATUS,
  DI_WORKFLOW_PHASE_STATUS,
  type DiMissionStatusId,
  missionStatus,
  officeStatus,
  recordClosed,
  requestStageStatus,
  runningFact,
  runStartedOf,
} from '../src/components/diMissionStatus.ts';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const repo = path.join(root, '..', '..', '..');
const base = (
  JSON.parse(
    fs.readFileSync(
      path.join(root, 'test/fixtures/di-office-state-session-mismatch-v2.FIXTURE.json'),
      'utf-8',
    ),
  ) as {
    state: DiOfficeState;
  }
).state;
const fresh = (): DiOfficeState => structuredClone(base);
/** The bound, active Mission Core Mission with four live, confirmed members (the catalog records its run start). */
const BOUND_REF = 'mc:mn-1aa604085c36ca55d01b57634ee13f41';
/** A Herdr root with no current task (IDLE, inactive): the catalog records no run start. */
const IDLE_REF = 'herd:root@0457d2576071';
/** The one bound, active Herdr task. */
const HERDR_BOUND_REF = 'herd:20261017-150121-aaaaaa@5a4574665441';
const find = (s: DiOfficeState, ref: string): DiMission =>
  s.missions.missions.find((m) => m.missionRef === ref)!;
const status = (s: DiOfficeState, m: DiMission) => missionStatus(m, s);
const nobodyLive = (m: DiMission) => {
  for (const x of m.members.members) x.live = false;
};

// ── The producer's own vocabularies, read from its source (the declared mappings must cover them exactly) ──────────────

const py = (rel: string): string => fs.readFileSync(path.join(repo, rel), 'utf-8');
/** The quoted strings of one `NAME = ( ... )` tuple. */
function tupleStrings(source: string, name: string): string[] {
  const body = new RegExp(`^${name} = \\(([\\s\\S]*?)\\)\\n`, 'm').exec(source)?.[1];
  assert.ok(body !== undefined, `tuple ${name} not found`);
  return [...body.replace(/#.*$/gm, '').matchAll(/"([^"]+)"/g)].map((m) => m[1]!);
}
/** `NAME = "value"` constants of one module. */
function constants(source: string): Map<string, string> {
  return new Map([...source.matchAll(/^([A-Z][A-Z_]*) = "([^"]*)"$/gm)].map((m) => [m[1]!, m[2]!]));
}
/** The constant NAMES of one `NAME = ( ... )` tuple, resolved through `consts`. */
function tupleNames(source: string, name: string, consts: Map<string, string>): string[] {
  const body = new RegExp(`^${name} = \\(([\\s\\S]*?)\\)\\n`, 'm').exec(source)?.[1];
  assert.ok(body !== undefined, `tuple ${name} not found`);
  return [...body.matchAll(/\b([A-Z][A-Z_]+)\b/g)].map((m) => {
    const value = consts.get(m[1]!);
    assert.ok(value !== undefined, `${m[1]} not resolved`);
    return value;
  });
}

const PANEL_STAGES = tupleStrings(py('mission_panel/lifecycle.py'), 'STAGES');
const probeSource = py('target_runtime/mission_progress_status.py');
const PROBE_STATES = tupleNames(probeSource, 'PROBE_STATES', constants(probeSource));
const workflowSource = py('workflow_authority/record.py');
const workflowConsts = constants(workflowSource);
const WORKFLOW_PHASES = tupleNames(workflowSource, 'PHASES', workflowConsts);
const WORKFLOW_TERMINAL = tupleNames(workflowSource, 'TERMINAL_PHASES', workflowConsts);
const HERDR_TERMINAL = [
  ...tupleStrings(py('herdr/vintage.py'), 'TERMINAL_TASK_STATUSES'),
  'ABORTED',
];
const missionRecord = constants(py('mission/record.py'));
const MISSION_CORE_TERMINAL = [
  ...py('mission_sources/sources.py').matchAll(
    /mission_record\.(STATE_[A-Z_]+): contract\.STANDING_TERMINAL/g,
  ),
].map((m) => missionRecord.get(m[1]!)!);
const sourcesContract = constants(py('mission_sources/contract.py'));
const MODEL_OF: Record<string, string> = {
  [sourcesContract.get('LIFECYCLE_MODEL_MISSION_CORE')!]: 'mission_core',
  [sourcesContract.get('LIFECYCLE_MODEL_WORKFLOW_STORE')!]: 'workflow_store',
  [constants(py('target_runtime/herdr_task_source.py')).get('LIFECYCLE_MODEL')!]: 'herdr_task',
};
const PRODUCER_TERMINAL: Record<string, readonly string[]> = {
  mission_core: MISSION_CORE_TERMINAL,
  workflow_store: WORKFLOW_TERMINAL,
  herdr_task: HERDR_TERMINAL,
};

describe('the vocabulary is closed, and each status has its own words and shape', () => {
  test('every id has a label, a shape, a plain meaning and a terminal flag; the list and the table agree', () => {
    assert.deepEqual([...DI_MISSION_STATUS_IDS].sort(), Object.keys(DI_MISSION_STATUS).sort());
    for (const id of DI_MISSION_STATUS_IDS) {
      const entry = DI_MISSION_STATUS[id];
      assert.ok(entry.label.trim() !== '', id);
      assert.ok(entry.glyph.trim() !== '', id);
      assert.ok(entry.meaning.trim().endsWith('.'), id);
      assert.equal(typeof entry.terminal, 'boolean', id);
    }
  });
  test('no two statuses share a label or a shape (never colour alone, never one word for two meanings)', () => {
    const entries = DI_MISSION_STATUS_IDS.map((id) => DI_MISSION_STATUS[id]);
    assert.equal(new Set(entries.map((e) => e.label)).size, entries.length);
    assert.equal(new Set(entries.map((e) => e.glyph)).size, entries.length);
  });
  test('not started, not known, stale, unrecognised and unavailable are separate members; only confident ones are current', () => {
    for (const id of [
      'not_started',
      'unknown',
      'stale',
      'unrecognised',
      'unavailable',
      'disconnected',
    ] as const)
      assert.ok(DI_MISSION_STATUS_IDS.includes(id), id);
    const uncertain = DI_MISSION_STATUS_IDS.filter(
      (id) => DI_MISSION_STATUS[id].certainty !== 'current',
    );
    assert.deepEqual(uncertain.sort(), [
      'disconnected',
      'stale',
      'unavailable',
      'unknown',
      'unrecognised',
    ]);
    // no uncertain status is terminal, and none is running
    for (const id of uncertain) assert.equal(DI_MISSION_STATUS[id].terminal, false, id);
  });
  test('the office status speaks the same vocabulary: the same words and shapes for the same meaning', () => {
    const s = fresh();
    const live = officeStatus(s);
    assert.equal(live.label, DI_MISSION_STATUS.running.label);
    assert.equal(live.glyph, DI_MISSION_STATUS.running.glyph);
    s.live = false;
    s.notLiveReason = 'transport_disconnected';
    const lost = officeStatus(s);
    assert.equal(lost.status, 'disconnected');
    assert.equal(lost.glyph, DI_MISSION_STATUS.disconnected.glyph);
  });
});

describe('every declared mapping is data over the producer’s own closed vocabulary', () => {
  test('standing: exactly the three declared standings', () => {
    assert.deepEqual(Object.keys(DI_STANDING_STATUS).sort(), ['active', 'inactive', 'terminal']);
  });
  test('terminal literals: each is a TERMINAL literal of its own model (exact, case-preserved), and maps to a terminal status', () => {
    const entries = Object.entries(DI_TERMINAL_LITERAL_STATUS);
    assert.ok(entries.length > 0);
    for (const [model, literals] of entries) {
      const kind = MODEL_OF[model];
      assert.ok(kind !== undefined, `unknown lifecycle model ${model}`);
      for (const [literal, id] of Object.entries(literals)) {
        assert.ok(
          PRODUCER_TERMINAL[kind]!.includes(literal),
          `${model} ${literal} is not a terminal literal of the producer`,
        );
        assert.equal(DI_MISSION_STATUS[id].terminal, true, `${model} ${literal}`);
      }
      // exhaustive: every terminal literal the producer declares is declared here too (a new one cannot slip past)
      assert.deepEqual(Object.keys(literals).sort(), [...PRODUCER_TERMINAL[kind]!].sort(), model);
    }
  });
  test('workflow phase: exactly the producer’s ten phases, each to one status', () => {
    assert.deepEqual(Object.keys(DI_WORKFLOW_PHASE_STATUS).sort(), [...WORKFLOW_PHASES].sort());
    // the phases before DISPATCHED, in the producer's own order, are before any run
    const dispatched = WORKFLOW_PHASES.indexOf('DISPATCHED');
    for (const phase of WORKFLOW_PHASES.slice(0, dispatched))
      assert.equal(
        DI_WORKFLOW_PHASE_STATUS[phase as keyof typeof DI_WORKFLOW_PHASE_STATUS],
        'not_started',
        phase,
      );
    for (const phase of WORKFLOW_TERMINAL)
      assert.equal(
        DI_MISSION_STATUS[DI_WORKFLOW_PHASE_STATUS[phase as keyof typeof DI_WORKFLOW_PHASE_STATUS]]
          .terminal,
        true,
        phase,
      );
  });
  test('request panel stage: exactly the panel’s own stages, each to one status; a start that was only requested never reads Running', () => {
    assert.deepEqual(Object.keys(DI_REQUEST_STAGE_STATUS).sort(), [...PANEL_STAGES].sort());
    assert.equal(DI_REQUEST_STAGE_STATUS.dispatched, 'start_requested');
    assert.equal(DI_REQUEST_STAGE_STATUS.dispatch_uncertain, 'start_requested');
    for (const stage of [
      'of_record',
      'approved',
      'approved_elsewhere',
      'approval_uncertain',
      'expired',
    ] as const)
      assert.equal(DI_REQUEST_STAGE_STATUS[stage], 'not_started', stage);
    // C4: the panel's `running` stage is Mission Core's STATE_RUNNING, TASK-observation evidence (mission_bridge._observe_run: the bound target task the receipt names, even
    // after it stopped), never evidence that a worker started: it is Task observed, and NO panel stage is Running (the panel observes no worker at all)
    assert.equal(DI_REQUEST_STAGE_STATUS.running, 'task_observed');
    assert.ok(!(Object.values(DI_REQUEST_STAGE_STATUS) as string[]).includes('running'));
    assert.equal(DI_REQUEST_STAGE_STATUS.verified, 'verified');
    assert.equal(DI_REQUEST_STAGE_STATUS.unreadable, 'unavailable');
  });
  test('progression probe: exactly the probe’s six states (Python and the window agree); it never names a Mission status', () => {
    assert.deepEqual(Object.keys(DI_PROGRESSION_PROBE_STATUS).sort(), [...PROBE_STATES].sort());
    assert.deepEqual([...MR_PROGRESSION_STATES].sort(), [...PROBE_STATES].sort());
    for (const entry of Object.values(DI_PROGRESSION_PROBE_STATUS))
      assert.deepEqual(Object.keys(entry).sort(), ['advancing', 'words']);
    assert.equal(DI_PROGRESSION_PROBE_STATUS.running.advancing, true);
    for (const state of PROBE_STATES.filter((x) => x !== 'running'))
      assert.equal(
        DI_PROGRESSION_PROBE_STATUS[state as keyof typeof DI_PROGRESSION_PROBE_STATUS].advancing,
        false,
        state,
      );
  });
  test('run_started’s meaning is declared per source; for herdr_task it is pinned to the producer: TRUE FOR EVERY EXISTING TASK RECORD, never "a run started"', () => {
    // target_runtime/herdr_task_source.py `_view`: the no-task branch is the only false; every task record sets started_run = True, and receipt_recorded is the same value
    const view =
      /^def _view\(source, aliases, root, task\):\n([\s\S]*?)\n\n\n/m.exec(
        py('target_runtime/herdr_task_source.py'),
      )?.[1] ?? '';
    assert.match(
      view,
      /if task is NO_CURRENT_TASK:[\s\S]*?started_run = True, False\n\s+else:[\s\S]*?\n\s+started_run = True\n/,
    );
    assert.match(view, /"run_started": started_run,\s*\n\s*"receipt_recorded": started_run,/);
    assert.equal(DI_RUN_STARTED_MEANING.herdr_task.true, 'a task record exists');
    assert.deepEqual(Object.keys(DI_RUN_STARTED_MEANING).sort(), [
      'herdr_task',
      'mission_core',
      'workflow_store',
    ]);
    for (const meaning of Object.values(DI_RUN_STARTED_MEANING))
      assert.doesNotMatch(
        `${meaning.true} ${meaning.false}`,
        /worker|team|work(ing|ed)\b|running|started/i,
      );
  });
  test('the bound-task entry names its source fields and never Running: runStarted is not one of its inputs', () => {
    assert.deepEqual(DI_BOUND_TASK_STATUS, {
      standing: 'active',
      bindingState: 'bound',
      confirmedLive: 0,
      status: 'task_observed',
    });
    assert.ok(!('runStarted' in DI_BOUND_TASK_STATUS));
  });
});

describe('NEVER STARTED is its own honest state', () => {
  test('the run-start fact is the catalog’s, verbatim: known true, known false, or NOT READ with its reason', () => {
    const s = fresh();
    assert.deepEqual(runStartedOf(find(s, BOUND_REF)), { known: true, value: true });
    assert.deepEqual(runStartedOf(find(s, IDLE_REF)), { known: true, value: false });
    const m = find(s, BOUND_REF);
    delete m.runStarted;
    const unread = runStartedOf(m);
    assert.equal(unread.known, false);
    assert.equal(!unread.known && unread.why.kind, 'not_read');
  });
  test('an inactive record whose run never started reads Not started, citing the fact', () => {
    const s = fresh();
    const r = status(s, find(s, IDLE_REF));
    assert.equal(r.status, 'not_started');
    assert.equal(r.label, 'Not started');
    assert.equal(r.why, null);
    assert.ok(r.sources.includes('mission.runStarted = false'));
  });
  test('an ACTIVE record whose run never started reads Not started, never Running, even with a bound binding and an active task record', () => {
    for (const ref of [BOUND_REF, HERDR_BOUND_REF]) {
      const s = fresh();
      const m = find(s, ref);
      m.runStarted = false;
      nobodyLive(m);
      const r = status(s, m);
      assert.equal(r.status, 'not_started', ref);
      assert.ok(r.sources.includes('mission.runStarted = false'), ref);
    }
  });
  test('a record that says no run started while team members are confirmed live: the sources disagree, so Unknown with both cited', () => {
    const s = fresh();
    const m = find(s, BOUND_REF);
    m.runStarted = false;
    const r = status(s, m);
    assert.equal(r.status, 'unknown');
    assert.equal(r.why?.kind, 'not_known');
    assert.ok(r.sources.includes('mission.runStarted = false'));
    assert.ok(r.sources.some((x) => x.includes('.live = true')));
    assert.match(r.sentence, /disagree/);
  });
  test('a terminal record that never started keeps its terminal status and says it never started', () => {
    const s = fresh();
    const m = find(s, IDLE_REF);
    m.standing = 'terminal';
    m.lifecycleState = 'COMPLETE';
    const r = status(s, m);
    assert.equal(r.status, 'ended');
    assert.match(r.sentence, /never started/);
  });
});

describe('the source of truth and its precedence', () => {
  test('the office decides first: not live is Stale or Disconnected (why: stale), whatever the record says', () => {
    const s = fresh();
    s.live = false;
    s.notLiveReason = 'client_expired:valid_until_passed';
    const stale = status(s, find(s, IDLE_REF));
    assert.equal(stale.status, 'stale');
    assert.equal(stale.why?.kind, 'stale');
    s.notLiveReason = 'transport_disconnected';
    const lost = status(s, find(s, BOUND_REF));
    assert.equal(lost.status, 'disconnected');
    assert.equal(lost.why?.kind, 'stale');
  });
  test('an unrecognised standing is Unrecognised (its own reason, the raw value cited), never Unknown and never a guess', () => {
    const s = fresh();
    const m = find(s, BOUND_REF);
    for (const raw of ['future_standing', 'Active', '']) {
      m.standing = raw;
      const r = status(s, m);
      assert.equal(r.status, 'unrecognised', JSON.stringify(raw));
      assert.equal(r.why?.kind, 'unrecognised');
      assert.ok(r.sentence.includes(`“${raw}”`));
    }
  });
  test('a roster the source could not provide is Unavailable with the source’s reason; a roster otherwise not observed is Unknown', () => {
    const s = fresh();
    const m = find(s, BOUND_REF);
    m.members.state = 'unavailable';
    m.members.reason = 'snapshot_unavailable';
    const r = status(s, m);
    assert.equal(r.status, 'unavailable');
    assert.equal(r.why?.kind, 'unavailable');
    assert.ok(r.why?.codes?.includes('snapshot_unavailable'));
    // the reason CODE is provenance (cited, and kept in the why's codes), never words on the face: a long code is one unbreakable token
    assert.ok(r.sources.includes('mission.members.reason = "snapshot_unavailable"'));
    assert.ok(!r.sentence.includes('snapshot_unavailable'));
    assert.ok(!r.why?.text.includes('snapshot_unavailable'));
    m.members.state = 'not_recorded';
    assert.equal(status(s, m).status, 'unknown');
  });
  test('terminal literals refine a TERMINAL standing only, by exact model and literal; anything else stays Ended', () => {
    const s = fresh();
    const herdr = find(s, IDLE_REF);
    herdr.standing = 'terminal';
    herdr.runStarted = true;
    const read = (literal: string) => {
      herdr.lifecycleState = literal;
      return status(s, herdr);
    };
    assert.equal(read('FAILED').status, 'failed');
    assert.equal(read('ABORTED').status, 'stopped');
    assert.equal(read('ABANDONED').status, 'stopped');
    assert.equal(read('COMPLETE').status, 'ended');
    // exact and case-preserved: no substring, no case folding
    assert.equal(read('failed').status, 'ended');
    assert.equal(read('FAILED_TWICE').status, 'ended');
    // the literal is cited as what decided it
    const failed = read('FAILED');
    assert.ok(failed.sources.includes('mission.lifecycleModel = "herdr.task/1"'));
    assert.ok(failed.sources.includes('mission.lifecycleState = "FAILED"'));
    // the same literal under ANOTHER model is not this model's literal
    herdr.lifecycleModel = 'some.other/1';
    assert.equal(read('FAILED').status, 'ended');
  });
  test('a terminal literal never decides a NON-terminal standing (the native state stays display-only there)', () => {
    const s = fresh();
    const m = find(s, BOUND_REF);
    for (const literal of ['FAILED', 'BLOCKED', 'CANCELLED', 'COMPLETED', 'x']) {
      m.lifecycleState = literal;
      const r = status(s, m);
      assert.equal(r.status, 'running', literal);
      assert.ok(!r.sources.some((x) => x.startsWith('mission.lifecycleState')), literal);
    }
  });
  test('verified success is Verified; reported success alone is Ended (reported is not verified)', () => {
    const s = fresh();
    const m = find(s, BOUND_REF);
    m.standing = 'terminal';
    m.lifecycleState = 'COMPLETED';
    assert.equal(m.v2.projected, true);
    const c = m.v2.projected ? m.v2.missionState.completion : null;
    c!.state = 'observed';
    c!.reportedComplete = true;
    c!.reportedSuccess = true;
    c!.verifiedSuccess = null;
    assert.equal(status(s, m).status, 'ended');
    c!.verifiedSuccess = true;
    const v = status(s, m);
    assert.equal(v.status, 'verified');
    assert.ok(v.sources.includes('v2.missionState.completion.verifiedSuccess = true'));
  });
  test('workflow phase: each declared phase reads its declared status, and a phase that contradicts the run-start fact reads Unknown', () => {
    const s = fresh();
    const m = find(s, BOUND_REF);
    nobodyLive(m);
    m.sourceKind = 'workflow_store';
    assert.equal(m.v2.projected, true);
    const set = (phase: string, runStarted: boolean) => {
      if (m.v2.projected)
        m.v2.workflowPhase = { state: 'observed', phase, unrecognized: null, problem: null };
      m.runStarted = runStarted;
      return status(s, m);
    };
    assert.equal(set('PLANNED', false).status, 'not_started');
    assert.equal(set('VALIDATED', false).status, 'not_started');
    assert.equal(set('DISPATCHED', false).status, 'start_requested');
    assert.equal(set('DISPATCHED', true).status, 'start_requested');
    assert.equal(set('NEEDS_REAUTHORIZATION', true).status, 'waiting');
    // a phase before dispatch with a recorded run start, or verified with none: the sources disagree
    const odd = set('PREPARED', true);
    assert.equal(odd.status, 'unknown');
    assert.match(odd.sentence, /disagree/);
    assert.equal(set('VERIFIED', false).status, 'unknown');
    // the phase is cited
    assert.ok(set('DISPATCHED', false).sources.includes('v2.workflowPhase.phase = "DISPATCHED"'));
  });
  test('a DISPATCHED workflow whose team is confirmed live is Running (the team is the stronger, observed evidence)', () => {
    const s = fresh();
    const m = find(s, BOUND_REF);
    m.sourceKind = 'workflow_store';
    if (m.v2.projected)
      m.v2.workflowPhase = {
        state: 'observed',
        phase: 'DISPATCHED',
        unrecognized: null,
        problem: null,
      };
    m.runStarted = true;
    assert.equal(status(s, m).status, 'running');
  });
  test('a bound, active record with no confirmed live member is Task observed, never Running, whatever runStarted says (Herdr task and Mission Core alike)', () => {
    for (const ref of [HERDR_BOUND_REF, BOUND_REF])
      for (const runStarted of [true, undefined]) {
        const s = fresh();
        const m = find(s, ref);
        nobodyLive(m);
        if (runStarted === undefined) delete m.runStarted;
        else m.runStarted = runStarted;
        const r = status(s, m);
        const at = `${ref} runStarted=${String(runStarted)}`;
        assert.equal(r.status, 'task_observed', at);
        assert.equal(r.why, null, at);
        assert.match(
          r.sentence,
          /no team member is confirmed live: nothing says any worker started/,
          at,
        );
        assert.ok(r.sources.includes('mission.bindingState = "bound"'), at);
        assert.ok(r.sources.includes(`mission.sourceKind = "${m.sourceKind}"`), at);
        assert.ok(
          r.sources.some((x) => x.includes('.live = false')),
          at,
        );
      }
  });
  test('running is claimed from confirmed live members and is cited field by field', () => {
    const s = fresh();
    const r = status(s, find(s, BOUND_REF));
    assert.equal(r.status, 'running');
    assert.ok(r.sources.includes('mission.standing = "active"'));
    assert.ok(r.sources.includes('mission.runStarted = true'));
    assert.ok(r.sources.some((x) => x.includes('.live = true') && x.includes('"present"')));
  });
});

describe('runningFact: what the Running filter and the orchestrator limit read', () => {
  test('a current status is a KNOWN yes or no; an uncertain one is not known with ITS reason (never "not running")', () => {
    const s = fresh();
    assert.deepEqual(runningFact(status(s, find(s, BOUND_REF))), { known: true, value: true });
    assert.deepEqual(runningFact(status(s, find(s, IDLE_REF))), { known: true, value: false });
    s.live = false;
    s.notLiveReason = 'client_expired:valid_until_passed';
    const stale = runningFact(status(s, find(s, BOUND_REF)));
    assert.equal(stale.known, false);
    assert.equal(!stale.known && stale.why.kind, 'stale');
  });
});

describe('the request panel’s stages read through the same contract', () => {
  test('each stage reads its declared status, cited as the panel’s own stage', () => {
    const r = requestStageStatus('dispatched', null);
    assert.equal(r.status, 'start_requested');
    assert.equal(r.label, 'Start requested');
    assert.ok(r.sources.includes('request.lifecycle.stage = "dispatched"'));
  });
  test('a stage the panel does not declare is Unrecognised with the raw stage, never a guess', () => {
    const r = requestStageStatus('teleported', null);
    assert.equal(r.status, 'unrecognised');
    assert.equal(r.why?.kind, 'unrecognised');
    assert.match(r.sentence, /teleported/);
  });
  test('the progression probe never changes a Mission’s status; it only qualifies one that is running or was asked to start', () => {
    for (const probe of MR_PROGRESSION_STATES) {
      const progression = {
        state: probe,
        detail: '',
        passes: null,
        intervalSeconds: null,
        lastPassAgeSeconds: null,
      };
      for (const stage of PANEL_STAGES)
        assert.equal(
          requestStageStatus(stage, progression).status,
          DI_REQUEST_STAGE_STATUS[stage as keyof typeof DI_REQUEST_STAGE_STATUS],
          `${stage}/${probe}`,
        );
    }
    const stale = requestStageStatus('running', {
      state: 'stale',
      detail: '',
      passes: null,
      intervalSeconds: null,
      lastPassAgeSeconds: null,
    });
    assert.ok(stale.sources.includes('progression.state = "stale"'));
    // C2: the probe says only how current the record shown is likely to be
    assert.match(stale.sentence, /Nothing advances or re-reads its record automatically right now/);
    assert.equal(stale.status, 'task_observed');
    // a HEALTHY runtime is never evidence that the request's Mission started, or that anyone worked
    const healthy = {
      state: 'running' as const,
      detail: '',
      passes: null,
      intervalSeconds: null,
      lastPassAgeSeconds: null,
    };
    for (const stage of PANEL_STAGES.filter(
      (x) => DI_REQUEST_STAGE_STATUS[x as keyof typeof DI_REQUEST_STAGE_STATUS] === 'not_started',
    )) {
      const r = requestStageStatus(stage, healthy);
      assert.equal(r.status, 'not_started', stage);
      // exactly the not-started meaning: the probe adds nothing to it, and is not cited
      assert.equal(r.sentence, DI_MISSION_STATUS.not_started.meaning, stage);
      assert.ok(!r.sources.some((x) => x.startsWith('progression.')), stage);
    }
    assert.match(
      requestStageStatus('running', healthy).sentence,
      /not evidence that any worker started/,
    );
    const notQualified = requestStageStatus('of_record', {
      state: 'stale',
      detail: '',
      passes: null,
      intervalSeconds: null,
      lastPassAgeSeconds: null,
    });
    assert.ok(!notQualified.sources.some((x) => x.startsWith('progression.')));
  });
  test('the journey’s run steps wear the contract’s words and say only what was observed: no “Started”, no “Running”, no “Team working”', () => {
    const labels = JOURNEY.map((step) => step.label);
    assert.ok(!labels.includes('Started'));
    assert.ok(!labels.includes('Team working'));
    assert.ok(!labels.includes(DI_MISSION_STATUS.running.label));
    const run: DiMissionStatusId[] = ['start_requested', 'task_observed', 'verified'];
    for (const id of run) assert.ok(labels.includes(DI_MISSION_STATUS[id].label), id);
  });
});

describe('C3: an ACTIVE, BOUND Herdr task whose team is not confirmed (the real projection of a producer-shaped record)', () => {
  // Exported by server/__tests__/diHerdrUnconfirmedTeamExport.test.ts through one real DiOffice. Its `runStarted: true` is the producer's own value: for herdr_task it is
  // true for EVERY existing task record (herdr_task_source.py `_view`), so it says a task record exists, never that a worker started. Nothing below sets a wire field.
  const projected = (
    JSON.parse(
      fs.readFileSync(
        path.join(root, 'test/fixtures/di-office-state-herdr-unconfirmed-team-v2.FIXTURE.json'),
        'utf-8',
      ),
    ) as {
      state: DiOfficeState;
    }
  ).state;
  const task = projected.missions.missions.find((m) => m.missionRef === HERDR_BOUND_REF)!;
  test('the projection is the case: active, bound, runStarted true (a task record exists), and no member live AND confirmed', () => {
    assert.equal(task.standing, 'active');
    assert.equal(task.bindingState, 'bound');
    assert.equal(task.runStarted, true);
    assert.equal(task.members.state, 'observed');
    assert.deepEqual(
      task.members.members.filter((m) => m.live && m.classification === 'present'),
      [],
    );
  });
  test('it never reads Running, Start requested, Started, Building or Team working: its bound task is observed, its workers are not', () => {
    const r = missionStatus(task, projected);
    assert.notEqual(r.status, 'running');
    assert.notEqual(r.status, 'start_requested');
    assert.equal(r.status, 'task_observed');
    assert.doesNotMatch(
      `${r.label} ${r.sentence}`,
      /\bRunning\b|\bStarted\b|Building|Team working|is working|are working/,
    );
    // honest about what is and is not known, and it cites what it used
    assert.match(r.sentence, /no team member is confirmed live/i);
    assert.match(r.sentence, /a task record exists/);
    for (const cited of [
      'mission.standing = "active"',
      'mission.bindingState = "bound"',
      'mission.sourceKind = "herdr_task"',
      'mission.runStarted = true',
    ])
      assert.ok(r.sources.includes(cited), cited);
    assert.ok(r.sources.some((x) => x.includes('.classification = "ambiguous"')));
    // and it is not counted as running anywhere (the Running filter, the orchestrator limit)
    assert.deepEqual(runningFact(r), { known: true, value: false });
  });
  test('runStarted is never an input that makes anything Running: the same record with runStarted not read reads the same', () => {
    const s = structuredClone(projected);
    const m = s.missions.missions.find((x) => x.missionRef === HERDR_BOUND_REF)!;
    delete m.runStarted;
    assert.equal(missionStatus(m, s).status, 'task_observed');
  });
});

describe('review finding 1: an unrecognised workflow phase is Unrecognised, with its reason and the phase’s provenance', () => {
  const workflow = (runStarted: boolean | undefined, unrecognized: string | null) => {
    const s = fresh();
    const m = find(s, BOUND_REF);
    nobodyLive(m);
    m.sourceKind = 'workflow_store';
    if (m.v2.projected)
      m.v2.workflowPhase = { state: 'observed', phase: null, unrecognized, problem: null };
    if (runStarted === undefined) delete m.runStarted;
    else m.runStarted = runStarted;
    return status(s, m);
  };
  test('never a confident Not started (or any confident status), whatever the run-start fact says', () => {
    for (const runStarted of [false, true, undefined]) {
      const r = workflow(runStarted, 'TELEPORTED');
      const at = `runStarted=${String(runStarted)}`;
      assert.equal(r.status, 'unrecognised', at);
      assert.equal(r.why?.kind, 'unrecognised', at);
      assert.match(r.why?.text ?? '', /workflow phase/, at);
      // the phase's provenance is kept: what the producer declared, verbatim, and how it was observed
      assert.ok(r.sources.includes('v2.workflowPhase.state = "observed"'), at);
      assert.ok(r.sources.includes('v2.workflowPhase.unrecognized = "TELEPORTED"'), at);
      assert.ok(r.sources.includes('mission.sourceKind = "workflow_store"'), at);
      assert.match(r.sentence, /TELEPORTED/, at);
    }
  });
  test('a declared value that was not text is still Unrecognised, cited as null', () => {
    const r = workflow(false, null);
    assert.equal(r.status, 'unrecognised');
    assert.ok(r.sources.includes('v2.workflowPhase.unrecognized = null'));
  });
  test('a team confirmed live is still the stronger, observed evidence: Running', () => {
    const s = fresh();
    const m = find(s, BOUND_REF);
    m.sourceKind = 'workflow_store';
    if (m.v2.projected)
      m.v2.workflowPhase = {
        state: 'observed',
        phase: null,
        unrecognized: 'TELEPORTED',
        problem: null,
      };
    assert.equal(status(s, m).status, 'running');
  });
});

describe('Mission Core’s own PROGRESS describes the RECORD, never execution (round-1 finding 6, corrected in round 3)', () => {
  // mission/state.py: "a descriptive progress lifecycle, not execution" (mission/state_service.py abandon() changes neither the run nor its workers). So terminal
  // progress FILES the record closed, decides the status WORD only where no start was ever recorded, and never says execution stopped or that capacity is free.
  const PROGRESS_SOURCE = py('mission/state.py');
  const progressConsts = new Map(
    [...PROGRESS_SOURCE.matchAll(/^(PROGRESS_[A-Z_]+) = "([A-Z_]+)"$/gm)].map((m) => [
      m[1]!,
      m[2]!,
    ]),
  );
  const PROGRESS_STATES = tupleNames(PROGRESS_SOURCE, 'PROGRESS_STATES', progressConsts);
  const TERMINAL_PROGRESS = tupleNames(PROGRESS_SOURCE, 'TERMINAL_PROGRESS_STATES', progressConsts);
  const fixture = (name: string): DiOfficeState =>
    (
      JSON.parse(fs.readFileSync(path.join(root, `test/fixtures/${name}`), 'utf-8')) as {
        state: DiOfficeState;
      }
    ).state;
  const abandoned = fixture('di-office-state-progress-abandoned-v2.FIXTURE.json');
  const unavailableProgress = fixture('di-office-state-progress-unavailable-v2.FIXTURE.json');
  /** Mission Core, AWAITING_DECISION, no start recorded: the record a never-started Mission is. */
  const UNSTARTED_REF = 'synthetic_mission_core:mn-00000000000000000000000000000009';
  const withProgress = (
    s: DiOfficeState,
    ref: string,
    progress: string | null,
    extra: Record<string, unknown> = {},
  ) => {
    const m = find(s, ref);
    if (m.v2.projected)
      m.v2.missionState.progress = {
        state: 'observed',
        reason: null,
        standing: 'verified',
        progress,
        unrecognized: null,
        closureReason: null,
        ...extra,
      };
    return m;
  };
  test('the mapping is data over Mission Core’s own vocabulary: exactly PROGRESS_STATES, and a status only for TERMINAL_PROGRESS_STATES', () => {
    assert.deepEqual(Object.keys(DI_MISSION_PROGRESS_STATUS).sort(), [...PROGRESS_STATES].sort());
    for (const p of PROGRESS_STATES) {
      const id = DI_MISSION_PROGRESS_STATUS[p as keyof typeof DI_MISSION_PROGRESS_STATUS];
      if (TERMINAL_PROGRESS.includes(p))
        assert.equal(id !== null && DI_MISSION_STATUS[id].terminal, true, p);
      else assert.equal(id, null, p);
    }
  });
  test('the real projection of a Mission ABANDONED before dispatch: no start was ever recorded, so the record’s progress decides: Stopped, closed, no capacity', () => {
    const m = find(abandoned, UNSTARTED_REF);
    assert.equal(m.lifecycleState, 'AWAITING_DECISION');
    assert.equal(m.runStarted, false);
    assert.equal(m.v2.projected && m.v2.missionState.progress?.progress, 'ABANDONED');
    const r = missionStatus(m, abandoned);
    assert.equal(r.status, 'stopped');
    assert.equal(r.capacity.bears, 'none');
    assert.equal(recordClosed(m), true);
    assert.ok(r.sources.includes('v2.missionState.progress.progress = "ABANDONED"'));
    assert.ok(r.sources.includes('v2.missionState.progress.closureReason = "abandoned_by_caller"'));
    assert.ok(r.sources.includes('mission.runStarted = false'));
  });
  test('on a record with no start recorded, the terminal progress word: COMPLETED Ended (Verified only with verified success), CLOSED_UNSUCCESSFUL and ABANDONED Stopped', () => {
    const read = (progress: string, verified: boolean | null = null) => {
      const s = structuredClone(abandoned);
      const m = withProgress(s, UNSTARTED_REF, progress);
      if (m.v2.projected && verified !== null) {
        m.v2.missionState.completion.state = 'observed';
        m.v2.missionState.completion.verifiedSuccess = verified;
      }
      return missionStatus(m, s);
    };
    assert.equal(read('COMPLETED').status, 'ended');
    assert.equal(read('COMPLETED', true).status, 'verified');
    assert.equal(read('CLOSED_UNSUCCESSFUL').status, 'stopped');
    assert.equal(read('ABANDONED').status, 'stopped');
    for (const p of ['COMPLETED', 'CLOSED_UNSUCCESSFUL', 'ABANDONED'])
      assert.equal(read(p).capacity.bears, 'none', p);
  });
  test('terminal progress beside CONFIRMED-LIVE workers: the team is observed working, so Running and capacity HELD; the record is filed closed; never Stopped', () => {
    for (const p of TERMINAL_PROGRESS) {
      const s = fresh();
      const m = withProgress(s, BOUND_REF, p, { closureReason: 'abandoned_by_caller' });
      const r = status(s, m);
      assert.equal(r.status, 'running', p);
      assert.notEqual(r.status, 'stopped', p);
      assert.equal(r.capacity.bears, 'holds', p);
      assert.equal(recordClosed(m), true, p);
      // the progress is cited, and said as a description of the record, not of execution
      assert.ok(r.sources.includes(`v2.missionState.progress.progress = "${p}"`), p);
      assert.match(r.sentence, /describes the record, not execution/, p);
      assert.ok(
        r.sources.some((x) => x.includes('.live = true')),
        p,
      );
    }
  });
  test('terminal progress beside UNCERTAIN run liveness: an honest status, and capacity counted as held or not known, never free', () => {
    // a bound task observed, its team not confirmed: Task observed, holds
    let s = fresh();
    let m = withProgress(s, BOUND_REF, 'ABANDONED');
    nobodyLive(m);
    let r = status(s, m);
    assert.equal(r.status, 'task_observed');
    assert.equal(r.capacity.bears, 'holds');
    // a roster that is not observed: Unknown, not known
    s = fresh();
    m = withProgress(s, BOUND_REF, 'ABANDONED');
    m.members = { state: 'not_recorded', reason: null, members: [] };
    r = status(s, m);
    assert.equal(r.status, 'unknown');
    assert.equal(r.capacity.bears, 'not_known');
    // the office is not live: Stale, not known, although the record's progress files it closed
    s = fresh();
    m = withProgress(s, BOUND_REF, 'ABANDONED');
    s.live = false;
    s.notLiveReason = 'client_expired:valid_until_passed';
    r = status(s, m);
    assert.equal(r.status, 'stale');
    assert.equal(r.capacity.bears, 'not_known');
    assert.equal(recordClosed(m), true);
    // (corrected in round 4) a terminal STANDING alone frees nothing either: with no positive basis its capacity stays not known
    if (m.v2.projected) delete m.v2.missionState.progress;
    m.standing = 'terminal';
    assert.equal(status(s, m).capacity.bears, 'not_known');
  });
  test('non-terminal progress decides nothing: IN_PROGRESS is not worker evidence; NOT_STARTED and BLOCKED leave the outer reading', () => {
    for (const p of ['NOT_STARTED', 'IN_PROGRESS', 'BLOCKED']) {
      const s = fresh();
      withProgress(s, BOUND_REF, p);
      assert.equal(status(s, find(s, BOUND_REF)).status, 'running', p);
      assert.equal(recordClosed(find(s, BOUND_REF)), false, p);
      const quiet = fresh();
      const m = withProgress(quiet, BOUND_REF, p);
      nobodyLive(m);
      assert.equal(status(quiet, m).status, 'task_observed', p);
    }
    // a terminal outer record decides; a COMPLETED Mission may still show IN_PROGRESS (local_request/surface.py RUN_PROGRESS_NOTE)
    const s = fresh();
    const m = withProgress(s, BOUND_REF, 'IN_PROGRESS');
    m.standing = 'terminal';
    m.lifecycleState = 'COMPLETED';
    assert.equal(status(s, m).status, 'ended');
  });
  test('END TO END: progress the SOURCE declares unavailable (a retained ABANDONED beside it) stays Unavailable with its reason, is not filed closed, never Stopped', () => {
    const m = find(unavailableProgress, UNSTARTED_REF);
    assert.equal(m.v2.projected && m.v2.missionState.progress?.state, 'unavailable');
    assert.equal(m.v2.projected && m.v2.missionState.progress?.standing, 'unavailable');
    assert.equal(m.v2.projected && m.v2.missionState.progress?.progress, null);
    const r = missionStatus(m, unavailableProgress);
    assert.notEqual(r.status, 'stopped');
    assert.equal(r.status, 'unavailable');
    assert.equal(r.why?.kind, 'unavailable');
    assert.ok(r.why?.codes?.includes('mission_state_journal_unreadable'));
    assert.ok(r.sources.includes('v2.missionState.progress.standing = "unavailable"'));
    assert.equal(recordClosed(m), false);
    // no start was ever recorded: nothing holds an Orchestrator
    assert.equal(r.capacity.bears, 'none');
  });
  test('progress it cannot read: on a record with no start recorded it is Unrecognised or Unavailable with its reason; beside an observed team the team still decides', () => {
    // unrecognised, on a never-started record
    let s = structuredClone(abandoned);
    withProgress(s, UNSTARTED_REF, null, { unrecognized: 'PAUSED_FOREVER' });
    const u = missionStatus(find(s, UNSTARTED_REF), s);
    assert.equal(u.status, 'unrecognised');
    assert.equal(u.why?.kind, 'unrecognised');
    assert.ok(u.sources.includes('v2.missionState.progress.unrecognized = "PAUSED_FOREVER"'));
    // an unexpected shape, and a standing that is not current: Unavailable with the reason
    for (const [reason, standing] of [
      ['progress_unexpected', 'verified'],
      ['progress_standing_not_current', 'unknown'],
    ] as const) {
      s = structuredClone(abandoned);
      const m = find(s, UNSTARTED_REF);
      if (m.v2.projected)
        m.v2.missionState.progress = {
          state: 'unavailable',
          reason,
          standing,
          progress: null,
          unrecognized: null,
          closureReason: null,
        };
      const x = missionStatus(m, s);
      assert.equal(x.status, 'unavailable', reason);
      assert.ok(x.why?.codes?.includes(reason), reason);
    }
    // beside an observed team: Running, and the unreadable progress is cited, never hidden
    s = fresh();
    withProgress(s, BOUND_REF, null, { unrecognized: 'PAUSED_FOREVER' });
    const live = status(s, find(s, BOUND_REF));
    assert.equal(live.status, 'running');
    assert.ok(live.sources.includes('v2.missionState.progress.unrecognized = "PAUSED_FOREVER"'));
    // a server that predates the field, or no progress fact at all: nothing changes
    s = fresh();
    const old = find(s, BOUND_REF);
    if (old.v2.projected) delete old.v2.missionState.progress;
    assert.equal(status(s, old).status, 'running');
    s = structuredClone(abandoned);
    const notReported = find(s, UNSTARTED_REF);
    if (notReported.v2.projected)
      notReported.v2.missionState.progress = {
        state: 'unavailable',
        reason: 'progress_not_reported',
        standing: null,
        progress: null,
        unrecognized: null,
        closureReason: null,
      };
    assert.equal(missionStatus(notReported, s).status, 'not_started');
  });
  test('every status carries its capacity: the contract’s declared table, never derived from filing', () => {
    const s = fresh();
    for (const m of s.missions.missions) {
      const r = missionStatus(m, s);
      assert.ok(['holds', 'none', 'not_known'].includes(r.capacity.bears), m.missionRef);
    }
    assert.equal(status(s, find(s, BOUND_REF)).capacity.bears, DI_STATUS_CAPACITY.running);
  });
});

describe('round 4, finding 1: a reported completion WITHOUT affirmed success is not a failure (pending, missing or stale review)', () => {
  // mission/observation.py: reported_success = reported_complete AND an applicable APPROVE review; "a stale one, or one collected at a cursor the document has left,
  // affirms nothing". So false also means the review is pending, missing, stale or inapplicable: an ABSENCE of affirmation, never a failure report.
  const obs = py('mission/observation.py');
  const hold = (name: string) => constants(obs).get(name)!;
  const REVIEW_HOLDS = ['HOLD_REVIEW_UNKNOWN', 'HOLD_REVIEW_UNAVAILABLE', 'HOLD_REVIEW_NOT_APPROVED', 'HOLD_REVIEW_INAPPLICABLE'].map(hold);
  const reportedNotAffirmed = (m: DiMission, holds: string[]) => {
    if (!m.v2.projected) throw new Error('a v2 Mission');
    m.v2.missionState.completion = {
      ...m.v2.missionState.completion,
      state: 'observed',
      reportedComplete: true,
      reportedSuccess: false,
      verifiedSuccess: false,
      holds,
    };
  };
  test('the producer’s own rule is pinned: reported_success needs an applicable APPROVE; the task’s FAILED report is a separate signal', () => {
    assert.match(obs, /"reported_success": \(reported_complete and review_approved\s+and applicable\(task, now, collection\)\)/);
    assert.match(obs, /task_contradicts = \(task\["standing"\] == STANDING_REPORTED and task\["value"\] in \(/);
    assert.equal(REVIEW_HOLDS.every((h) => typeof h === 'string' && h.startsWith('review_')), true);
  });
  test('an ACTIVE Mission with CONFIRMED-LIVE workers and a pending, missing, stale or inapplicable review is Running, capacity HELD, never Failed', () => {
    for (const reviewHold of REVIEW_HOLDS) {
      const s = fresh();
      const m = find(s, BOUND_REF);
      reportedNotAffirmed(m, ['no_canonical_closure', reviewHold]);
      const r = status(s, m);
      assert.equal(r.status, 'running', reviewHold);
      assert.notEqual(r.status, 'failed', reviewHold);
      assert.equal(r.capacity.bears, 'holds', reviewHold);
      // the reported completion is not hidden: cited, and said as what it is
      assert.ok(r.sources.includes('v2.missionState.completion.reportedComplete = true'), reviewHold);
      assert.ok(r.sources.includes('v2.missionState.completion.reportedSuccess = false'), reviewHold);
      // (CHANGED IN round 5, findings 2 and 3) said as its own qualification, rendered beside the status, never folded into the sentence a Running face hides
      assert.equal(r.qualification, DI_COMPLETION_NOT_AFFIRMED, reviewHold);
      assert.doesNotMatch(r.sentence, /affirm/, reviewHold);
    }
  });
  test('the same with no confirmed member is Task observed (holds); nothing in the completion makes anything Failed', () => {
    const s = fresh();
    const m = find(s, BOUND_REF);
    nobodyLive(m);
    reportedNotAffirmed(m, ['review_unknown']);
    const r = status(s, m);
    assert.equal(r.status, 'task_observed');
    assert.equal(r.capacity.bears, 'holds');
  });
});

describe('round 4, finding 2: capacity NONE needs a source-grounded POSITIVE basis, fresh or stale; withheld members and a fresh answer are not one', () => {
  // A terminal BLOCKED via failed verification can still run: mission/service.py makes a failed verification terminal BLOCKED, including verify_target_not_stopped
  // (mission/record.py VERIFY_CONJUNCTS; tests/test_mission_bridge.py covers it). The roster then WITHHOLDS its members (it does not stop them).
  const record = py('mission/record.py');
  const service = py('mission/service.py');
  const broker = py('target_runtime/broker.py');
  const blockedByVerification = (s: DiOfficeState) => {
    const m = find(s, BOUND_REF);
    // the shape the roster gives a non-active record: membership withheld, nothing said about workers
    m.standing = 'terminal';
    m.lifecycleState = 'BLOCKED';
    m.bindingState = 'not_applicable';
    m.bindingReason = 'mission_not_active';
    m.members = { state: 'projected_reason', reason: 'binding_not_applicable:mission_not_active', members: [] };
    return m;
  };
  const stale = (s: DiOfficeState) => {
    s.live = false;
    s.notLiveReason = 'client_expired:valid_until_passed';
    return s;
  };
  test('the producer facts are pinned: verification can end BLOCKED on verify_target_not_stopped; COMPLETED only when every conjunct held, target_stopped included', () => {
    assert.match(record, /\("target_stopped", "verify_target_not_stopped"\)/);
    assert.match(service, /record\.STATE_COMPLETED if verified else record\.STATE_BLOCKED/);
    assert.match(py('tests/test_mission_bridge.py'), /"verify_target_not_stopped", \{"raw": raw_observation\(\)\}/);
    // herd's OWN stopped set, the one DI's verification reads for target_stopped
    const stopped = /_TARGET_TERMINAL_STATUSES = \(([^)]*)\)/.exec(broker)?.[1] ?? '';
    assert.deepEqual([...stopped.matchAll(/"([A-Z]+)"/g)].map((m) => m[1]).sort(), [...DI_CAPACITY_RELEASE.herdrStoppedStatuses].sort());
  });
  test('terminal BLOCKED via failed verification, FRESH collection: Stopped is the record’s word, but its capacity is NOT KNOWN, never none', () => {
    const s = fresh();
    const r = status(s, blockedByVerification(s));
    assert.equal(r.status, 'stopped');
    assert.equal(r.capacity.bears, 'not_known');
    assert.equal(r.capacity.bears === 'not_known' && r.capacity.why.kind, 'not_known');
    assert.match(r.capacity.bears === 'not_known' ? r.capacity.why.text : '', /establishes that its execution ended/);
  });
  test('terminal BLOCKED via failed verification, STALE office: capacity NOT KNOWN (stale), never none', () => {
    const s = stale(fresh());
    const r = status(s, blockedByVerification(s));
    assert.equal(r.status, 'stale');
    assert.equal(r.capacity.bears, 'not_known');
    assert.equal(r.capacity.bears === 'not_known' && r.capacity.why.kind, 'stale');
  });
  test('(corrected round-3 test) a terminal STANDING alone is no basis: a Mission Core record terminal with no positive basis holds capacity NOT KNOWN, fresh and stale', () => {
    for (const make of [fresh, () => stale(fresh())]) {
      const s = make();
      const m = find(s, BOUND_REF);
      m.standing = 'terminal';
      assert.notEqual(status(s, m).capacity.bears, 'none');
    }
  });
  test('the three declared bases, each naming its source field: an engine-stopped Herdr task, a verified Mission Core COMPLETED, and no start ever recorded', () => {
    // herd's own stopped status (fresh AND stale: a stopped task record never resumes; new work is a new record)
    for (const make of [fresh, () => stale(fresh())])
      for (const literal of DI_CAPACITY_RELEASE.herdrStoppedStatuses) {
        const s = make();
        const m = find(s, IDLE_REF);
        m.lifecycleState = literal;
        m.standing = literal === 'ERROR' ? 'inactive' : 'terminal';
        m.runStarted = true;
        const c = status(s, m).capacity;
        assert.equal(c.bears, 'none', literal);
        assert.equal(c.bears === 'none' && c.basis, 'engine_task_stopped', literal);
        assert.equal(c.bears === 'none' && c.source, `mission.lifecycleState = "${literal}"`, literal);
      }
    // the vintage terminal literals herd itself does not write are NO basis
    for (const literal of ['COMPLETED', 'ABANDONED', 'FAILED']) {
      const s = fresh();
      const m = find(s, IDLE_REF);
      m.lifecycleState = literal;
      m.standing = 'terminal';
      m.runStarted = true;
      assert.equal(status(s, m).capacity.bears, 'not_known', literal);
    }
    // Mission Core COMPLETED: verification held every conjunct, target_stopped included (fresh AND stale)
    // CHANGED IN Deliverable 2, the capacity seam (lead-accepted principle): this case borrowed the bound fixture's LIVE, confirmed team, which a finished record
    // never carries (the catalog withholds a non-active record's members). Its release is pinned on the production shape (no members); the borrowed live team
    // now holds while live and is not known while stale, never a release (test/diCapacityBasisCases.test.ts pins every basis).
    for (const make of [fresh, () => stale(fresh())]) {
      const s = make();
      const m = find(s, BOUND_REF);
      m.standing = 'terminal';
      m.lifecycleState = 'COMPLETED';
      m.bindingState = 'not_applicable';
      m.members = { state: 'projected_reason', reason: 'binding_not_applicable:mission_not_active', members: [] };
      const c = status(s, m).capacity;
      assert.equal(c.bears === 'none' && c.basis, 'verified_target_stopped');
    }
    for (const make of [fresh, () => stale(fresh())]) {
      const s = make();
      const m = find(s, BOUND_REF);
      m.standing = 'terminal';
      m.lifecycleState = 'COMPLETED';
      assert.equal(status(s, m).capacity.bears, s.live ? 'holds' : 'not_known', String(s.live));
    }
    // no start ever recorded: a basis in a LIVE office; stale, only for a terminal record (it can never record a start)
    let s = fresh();
    assert.equal(status(s, find(s, IDLE_REF)).capacity.bears === 'none' && (status(s, find(s, IDLE_REF)).capacity as { basis: string }).basis, 'no_start_recorded');
    s = stale(fresh());
    assert.equal(status(s, find(s, IDLE_REF)).capacity.bears, 'not_known');
    s = stale(fresh());
    const rejected = find(s, BOUND_REF);
    rejected.standing = 'terminal';
    rejected.lifecycleState = 'REJECTED';
    rejected.runStarted = false;
    // CHANGED IN Deliverable 2, the capacity seam: as above, a finished record carries no members (the roster withholds them), and on that shape it releases;
    // with the bound fixture's borrowed live team, a stale office's last good observation had workers live, so it is not known, never a release.
    assert.equal(status(s, rejected).capacity.bears, 'not_known');
    rejected.bindingState = 'not_applicable';
    rejected.members = { state: 'projected_reason', reason: 'binding_not_applicable:mission_not_active', members: [] };
    assert.equal(status(s, rejected).capacity.bears, 'none');
  });
  test('a workflow the broker VERIFIED or COMPLETED holds none (verification runs only once the target is observed stopped); BLOCKED, fresh or stale, is no basis', () => {
    // the producer facts: DISPATCHED is the only way into VERIFIED, and VERIFIED the only way into COMPLETED (workflow_authority/record.py); the broker's ONE
    // VERIFIED write sits after `if not observation["target_complete"]: ... return` (target_runtime/broker.py `_verify`), and target_complete is herd's stopped set
    const transitions = py('workflow_authority/record.py');
    assert.match(transitions, /PHASE_DISPATCHED: frozenset\(\s*\(PHASE_VERIFIED, PHASE_NEEDS_REAUTHORIZATION, PHASE_BLOCKED\)\s*\)/);
    assert.match(transitions, /PHASE_VERIFIED: frozenset\(\(PHASE_COMPLETED, PHASE_BLOCKED\)\)/);
    assert.equal([...broker.matchAll(/entry, record_module\.PHASE_VERIFIED/g)].length, 1);
    const verify = broker.slice(broker.indexOf('    def _verify(self, workflows, entry):'), broker.indexOf('entry, record_module.PHASE_VERIFIED'));
    assert.match(verify, /if not observation\["target_complete"\]:/);
    assert.match(broker, /target_complete = \(\s*supported and status in _TARGET_TERMINAL_STATUSES/);
    const workflow = (s: DiOfficeState, phase: string, runStarted: boolean) => {
      const m = find(s, BOUND_REF);
      nobodyLive(m);
      m.sourceKind = 'workflow_store';
      m.lifecycleModel = 'workflow_authority.workflow_phase/2';
      m.lifecycleState = phase;
      m.standing = phase === 'VERIFIED' ? 'active' : 'terminal';
      m.runStarted = runStarted;
      if (m.v2.projected) m.v2.workflowPhase = { state: 'observed', phase, unrecognized: null, problem: null };
      return status(s, m);
    };
    for (const make of [fresh, () => stale(fresh())]) {
      for (const phase of ['VERIFIED', 'COMPLETED']) {
        const c = workflow(make(), phase, true).capacity;
        assert.equal(c.bears, 'none', phase);
        assert.equal(c.bears === 'none' && c.basis, 'workflow_target_stopped', phase);
        assert.equal(c.bears === 'none' && c.source, `v2.workflowPhase.phase = "${phase}"`, phase);
      }
      assert.equal(workflow(make(), 'BLOCKED', true).capacity.bears, 'not_known');
    }
    // where the sources DISAGREE (verified, yet no run start recorded) the status is Unknown and nothing frees capacity
    const odd = workflow(fresh(), 'VERIFIED', false);
    assert.equal(odd.status, 'unknown');
    assert.equal(odd.capacity.bears, 'not_known');
  });
  test('an unrecognised standing gives no basis, whatever its literal says', () => {
    const s = fresh();
    const m = find(s, IDLE_REF);
    m.lifecycleState = 'COMPLETE';
    m.standing = 'future_standing';
    assert.equal(status(s, m).capacity.bears, 'not_known');
  });
});

describe('round 4, finding 3: a progress fact with NO standing stays uncertain, distinct from a producer that predates the field (END TO END)', () => {
  const fixture = (name: string): DiOfficeState =>
    (JSON.parse(fs.readFileSync(path.join(root, `test/fixtures/${name}`), 'utf-8')) as { state: DiOfficeState }).state;
  const REF = 'synthetic_mission_core:mn-00000000000000000000000000000009';
  test('the real projection (null standing, retained ABANDONED) reads Unavailable, citing progress_standing_not_current; never Not started, never Stopped', () => {
    const s = fixture('di-office-state-progress-standing-null-v2.FIXTURE.json');
    const m = find(s, REF);
    assert.equal(m.v2.projected && m.v2.missionState.progress?.reason, 'progress_standing_not_current');
    assert.equal(m.v2.projected && m.v2.missionState.progress?.standing, null);
    const r = missionStatus(m, s);
    assert.equal(r.status, 'unavailable');
    assert.notEqual(r.status, 'not_started');
    assert.ok(r.why?.codes?.includes('progress_standing_not_current'));
    assert.ok(r.sources.includes('v2.missionState.progress.reason = "progress_standing_not_current"'));
    assert.ok(r.sources.includes('v2.missionState.progress.standing = null'));
  });
  test('a producer that predates the field (progress_not_reported) changes nothing: still Not started, citing no progress', () => {
    const s = fixture('di-office-state-progress-standing-null-v2.FIXTURE.json');
    const m = find(s, REF);
    if (m.v2.projected)
      m.v2.missionState.progress = { state: 'unavailable', reason: 'progress_not_reported', standing: null, progress: null, unrecognized: null, closureReason: null };
    const r = missionStatus(m, s);
    assert.equal(r.status, 'not_started');
    assert.ok(!r.sources.some((x) => x.startsWith('v2.missionState.progress')));
  });
});

describe('round 5, finding 1: "no start recorded" is a basis ONLY where the source records the start BEFORE the effect', () => {
  const sources = py('mission_sources/sources.py');
  const broker = py('target_runtime/broker.py');
  const bridge = py('target_runtime/mission_bridge.py');
  const tasks = py('herdr/tasks.py');
  const stale = (s: DiOfficeState) => {
    s.live = false;
    s.notLiveReason = 'client_expired:valid_until_passed';
    return s;
  };
  const before = (text: string, ...marks: string[]) => {
    const at = marks.map((m) => text.indexOf(m));
    assert.ok(at.every((i) => i >= 0), JSON.stringify(marks));
    assert.deepEqual([...at].sort((a, b) => a - b), at, JSON.stringify(marks));
  };
  test('the producer facts are pinned: a workflow’s run_started is its engine identity, recorded AFTER the spawn; DISPATCHED is saved BEFORE it', () => {
    assert.match(sources, /"run_started": engine is not None,/);
    before(
      broker,
      'record_module.apply_transition(\n                entry, record_module.PHASE_DISPATCHED',
      '# Durable BEFORE the external spawn.',
      'spawn_result = self._spawn(self.control_realpath, request)',
      'entry["target_engine"] = (',
    );
    // a post-effect exception: BLOCKED, and the engine identity is never recorded
    assert.match(broker, /except Exception as exc:\s+if entry\["phase"\] not in record_module\.TERMINAL_PHASES:\s+record_module\.apply_transition\(\s+entry, record_module\.PHASE_BLOCKED/);
  });
  test('the producer facts are pinned: Mission Core’s intent and herd’s task record are saved BEFORE the effect', () => {
    assert.match(sources, /"run_started": intent is not None,/);
    before(bridge, 'self._missions.record_run_intent,', '# The intent is durable.', 'spawned = self._spawn(self._control_repo, derived.spawn_request())');
    const start = tasks.slice(tasks.lastIndexOf('"status": "ACTIVE"'));
    before(start, '"status": "ACTIVE"', 'save_task(', 'result = prompt(');
  });
  const workflow = (s: DiOfficeState, phase: string, standing: string, runStarted: boolean) => {
    const m = find(s, BOUND_REF);
    nobodyLive(m);
    m.sourceKind = 'workflow_store';
    m.lifecycleModel = 'workflow_authority.workflow_phase/2';
    m.lifecycleState = phase;
    m.standing = standing;
    m.runStarted = runStarted;
    if (m.v2.projected) m.v2.workflowPhase = { state: 'observed', phase, unrecognized: null, problem: null };
    return m;
  };
  test('a workflow BLOCKED after a post-effect exception (no engine recorded, so runStarted = false) is NEVER released, fresh AND stale', () => {
    for (const make of [fresh, () => stale(fresh())]) {
      const s = make();
      const r = status(s, workflow(s, 'BLOCKED', 'terminal', false));
      assert.equal(r.capacity.bears, 'not_known', String(s.live));
      assert.notEqual(r.capacity.bears, 'none', String(s.live));
    }
  });
  test('BOTH directions: the same runStarted = false holds a workflow (engine recorded after the spawn) and releases Mission Core (intent recorded before it)', () => {
    for (const make of [fresh, () => stale(fresh())]) {
      // workflow store: an absent spawn result after a post-effect exception, never released
      const w = make();
      assert.equal(status(w, workflow(w, 'BLOCKED', 'terminal', false)).capacity.bears, 'not_known', `workflow, live=${w.live}`);
      // Mission Core: no run intent recorded, so no spawn was ever made; a terminal record (it can never record one later), fresh AND stale
      const c = make();
      const core = find(c, BOUND_REF);
      nobodyLive(core);
      core.sourceKind = 'mission_core';
      core.standing = 'terminal';
      core.lifecycleState = 'REJECTED';
      core.runStarted = false;
      const released = status(c, core).capacity;
      assert.equal(released.bears, 'none', `mission core, live=${c.live}`);
      assert.equal(released.bears === 'none' && released.basis, 'no_start_recorded');
      assert.equal(released.bears === 'none' && released.source, 'mission.runStarted = false');
    }
    // and an ACTIVE Mission Core record with no intent, in a live office, releases too (nothing was spawned)
    const live = fresh();
    const pending = find(live, BOUND_REF);
    nobodyLive(pending);
    pending.sourceKind = 'mission_core';
    pending.standing = 'inactive';
    pending.lifecycleState = 'AWAITING_DECISION';
    pending.runStarted = false;
    assert.equal(status(live, pending).capacity.bears, 'none');
  });
  test('a workflow before DISPATCHED holds none in a live office: its phase, saved before any spawn, is the basis, never runStarted', () => {
    for (const phase of DI_CAPACITY_RELEASE.workflowUndispatchedPhases) {
      const s = fresh();
      const c = status(s, workflow(s, phase, 'active', false)).capacity;
      assert.equal(c.bears, 'none', phase);
      // CHANGED IN Deliverable 2 (the capacity seam): this phase reading is now named `workflow_before_dispatch`; `workflow_not_dispatched` is the
      // receipt fact the backend shares. Same rule, same source field, same refusal when not live.
      assert.equal(c.bears === 'none' && c.basis, 'workflow_before_dispatch', phase);
      assert.equal(c.bears === 'none' && c.source, `v2.workflowPhase.phase = "${phase}"`, phase);
      // not live: it may have dispatched since, so not known
      const old = stale(fresh());
      assert.equal(status(old, workflow(old, phase, 'active', false)).capacity.bears, 'not_known', phase);
    }
    // the undispatched phases are exactly the ones the contract reads as Not started
    assert.deepEqual(
      [...DI_CAPACITY_RELEASE.workflowUndispatchedPhases].sort(),
      Object.entries(DI_WORKFLOW_PHASE_STATUS).filter(([, v]) => v === 'not_started').map(([k]) => k).sort(),
    );
    // NEEDS_REAUTHORIZATION is reachable from DISPATCHED too, so it is no basis
    const s = fresh();
    assert.equal(status(s, workflow(s, 'NEEDS_REAUTHORIZATION', 'active', false)).capacity.bears, 'not_known');
  });
  test('the rule is declared per source, and a workflow’s runStarted is declared NOT evidence', () => {
    assert.deepEqual(Object.keys(DI_CAPACITY_RELEASE.noStartRecordedBySource).sort(), ['herdr_task', 'mission_core']);
    assert.ok(!('workflow_store' in DI_CAPACITY_RELEASE.noStartRecordedBySource));
    // the exported reading D2 reuses gives the same answer as the status
    const s = fresh();
    assert.equal(capacityReleaseBasis(workflow(s, 'BLOCKED', 'terminal', false), true), null);
  });
});

describe('round 5, finding 3: the completion qualification states only the lack of affirmation, never an invented cause', () => {
  const obs = py('mission/observation.py');
  test('the producer fact is pinned: reported_success also needs an APPLICABLE task report, and no hold records an inapplicable one', () => {
    assert.match(obs, /"reported_success": \(reported_complete and review_approved\s+and applicable\(task, now, collection\)\)/);
    const appended = [...obs.matchAll(/holds\.append\((HOLD_[A-Z_]+)\)/g)].map((m) => m[1]);
    assert.deepEqual(appended.filter((h) => h!.startsWith('HOLD_TASK_')), ['HOLD_TASK_CONTRADICTS']);
  });
  test('fresh APPROVE + a stale task report (reported_success false, NO review hold): the qualification names no review, and no cause at all', () => {
    const s = fresh();
    const m = find(s, BOUND_REF);
    nobodyLive(m);
    if (!m.v2.projected) throw new Error('a v2 Mission');
    m.v2.missionState.completion = {
      ...m.v2.missionState.completion,
      state: 'observed',
      reportedComplete: true,
      reportedSuccess: false,
      verifiedSuccess: false,
      holds: ['no_canonical_closure'],
    };
    const r = status(s, m);
    assert.equal(r.status, 'task_observed');
    assert.equal(r.qualification, DI_COMPLETION_NOT_AFFIRMED);
    assert.doesNotMatch(`${r.sentence} ${r.qualification}`, /review|because|yet\b/i);
    // the evidence is cited, not interpreted
    assert.ok(r.sources.includes('v2.missionState.completion.reportedSuccess = false'));
  });
  test('the wording itself claims nothing about why, and nothing about verification: it says reported complete, reported success not affirmed, not a failure', () => {
    assert.match(DI_COMPLETION_NOT_AFFIRMED, /reports the task complete/);
    // (CHANGED IN round 6) reported_success = false supports exactly one claim, that reported success is NOT AFFIRMED: no "not verified" (verification is a
    // separate fact, said by the status and the results line), and no negative verdict of any kind
    assert.match(DI_COMPLETION_NOT_AFFIRMED, /its reported success is not affirmed/);
    assert.match(DI_COMPLETION_NOT_AFFIRMED, /not a failure/);
    assert.doesNotMatch(DI_COMPLETION_NOT_AFFIRMED, /verif|unsuccessful|not successful|\bfailed\b|incomplete|not complete/i);
    assert.doesNotMatch(DI_COMPLETION_NOT_AFFIRMED, /review|because|pending|missing|stale/i);
  });
});

describe('round 5, item 4: Running needs a live member that REPORTS WORK; a live team whose members are all idle or done is not Running', () => {
  const contract = py('mission_sources/contract.py');
  const activity = (s: DiOfficeState, ...acts: string[]) => {
    const m = find(s, BOUND_REF);
    m.members.members.forEach((member, i) => (member.activity = acts[i % acts.length]!));
    return m;
  };
  const CLAIMS_WORK = /\bworking\b|\bis at work\b|in progress|under way|\bRunning\b/i;
  test('the producer facts are pinned: the runtime’s five literals map to five activities (busy, idle, blocked, done, unknown); liveness is the snapshot’s freshness', () => {
    assert.match(contract, /RUNTIME_STATUS_ACTIVITY = \{\s+"working": ACTIVITY_BUSY,\s+"idle": ACTIVITY_IDLE,\s+"blocked": ACTIVITY_BLOCKED,\s+"done": ACTIVITY_DONE,\s+"unknown": ACTIVITY_UNKNOWN,\s+\}/);
    assert.match(contract, /def is_live\(self\):\s+return self\.health == HEALTH_FRESH/);
    const literals = [...contract.matchAll(/^ACTIVITY_[A-Z_]+ = "([a-z_]+)"/gm)].map((m) => m[1]).sort();
    assert.deepEqual(Object.keys(DI_MEMBER_ACTIVITY_WORK).sort(), literals);
  });
  test('freshly bound, every member confirmed live but idle or done, no work evidence: Waiting, capacity HELD, and nothing it says claims current work', () => {
    for (const acts of [['idle'], ['done'], ['idle', 'done']]) {
      const s = fresh();
      const r = status(s, activity(s, ...acts));
      assert.equal(r.status, 'waiting', acts.join());
      assert.notEqual(r.status, 'running', acts.join());
      assert.equal(r.capacity.bears, 'holds', acts.join());
      assert.match(r.sentence, /observed live/, acts.join());
      assert.match(r.sentence, /none reports work/, acts.join());
      assert.doesNotMatch(`${r.label} ${r.sentence} ${r.qualification ?? ''}`, CLAIMS_WORK, acts.join());
      assert.ok(r.sources.some((x) => /\.activity = "(idle|done)"/.test(x)), acts.join());
    }
  });
  test('one member that reports work (busy) is Running; its sentence counts who reports work', () => {
    const s = fresh();
    const r = status(s, activity(s, 'busy', 'idle', 'done', 'idle'));
    assert.equal(r.status, 'running');
    assert.match(r.sentence, /1 reports work \(busy\)/);
  });
  test('no member reports work and some report no known activity: Unknown, with its reason, capacity HELD; never Running, never Waiting', () => {
    for (const other of ['unknown', 'unknown_future', 'teleporting']) {
      const s = fresh();
      const r = status(s, activity(s, 'idle', other));
      assert.equal(r.status, 'unknown', other);
      assert.equal(r.why?.kind, 'not_known', other);
      assert.equal(r.capacity.bears, 'holds', other);
    }
  });
  test('a live member reporting blocked still reads Blocked, ahead of everything else', () => {
    const s = fresh();
    assert.equal(status(s, activity(s, 'idle', 'blocked')).status, 'blocked');
  });
  test('the Running meaning says what it requires; the Waiting meaning covers a live team with no member at work', () => {
    assert.match(DI_MISSION_STATUS.running.meaning, /at least one reports work/);
    assert.match(DI_MISSION_STATUS.waiting.meaning, /no member reports work/);
  });
});

describe('round 6: the qualification never denies an INDEPENDENTLY verified result (verified success and reported success are separate facts)', () => {
  const obs = py('mission/observation.py');
  /** The three producer-reachable combinations: reported_success false, verified_success true. */
  const COMBOS: ReadonlyArray<{ name: string; holds: string[] }> = [
    { name: 'verified closure, review unavailable', holds: ['review_unavailable'] },
    { name: 'verified closure, review unknown', holds: ['review_unknown'] },
    { name: 'verified closure, fresh APPROVE, inapplicable task report', holds: [] },
  ];
  const verifiedNotReported = (m: DiMission, holds: string[]) => {
    if (!m.v2.projected) throw new Error('a v2 Mission');
    m.v2.missionState.completion = {
      ...m.v2.missionState.completion,
      state: 'observed',
      reportedComplete: true,
      reportedSuccess: false,
      verifiedSuccess: true,
      holds,
    };
  };
  test('the producer facts are pinned: verified_success is computed apart from reported_success, and review unavailable/unknown never block it', () => {
    assert.match(obs, /"verified_success": verified and not blocking,/);
    assert.match(obs, /"reported_success": \(reported_complete and review_approved\s+and applicable\(task, now, collection\)\)/);
    assert.match(obs, /INFORMATION_HOLDS = frozenset\(\(HOLD_REVIEW_UNAVAILABLE, HOLD_REVIEW_UNKNOWN\)\)/);
    assert.match(obs, /blocking = \[h for h in holds if h not in INFORMATION_HOLDS\]/);
  });
  test('each combination, a terminal verified closure: Verified, NO qualification, and nothing it says denies the verification', () => {
    for (const { name, holds } of COMBOS) {
      const s = fresh();
      const m = find(s, BOUND_REF);
      nobodyLive(m);
      m.standing = 'terminal';
      m.lifecycleState = 'COMPLETED';
      verifiedNotReported(m, holds);
      const r = status(s, m);
      assert.equal(r.status, 'verified', name);
      assert.equal(r.qualification, null, name);
      assert.doesNotMatch(`${r.sentence} ${r.qualification ?? ''}`, /not verified|not affirmed/, name);
      assert.ok(r.sources.includes('v2.missionState.completion.verifiedSuccess = true'), name);
    }
  });
  test('each combination beside a LIVE team (Running): still no qualification, because verified success is recorded', () => {
    for (const { name, holds } of COMBOS) {
      const s = fresh();
      const m = find(s, BOUND_REF);
      verifiedNotReported(m, holds);
      const r = status(s, m);
      assert.equal(r.status, 'running', name);
      assert.equal(r.qualification, null, name);
    }
  });
  test('verifiedSuccess is THREE-valued: with null (not known), each combination keeps only "reported success is not affirmed", and nothing says "not verified"', () => {
    for (const { name, holds } of COMBOS) {
      for (const make of [
        (m: DiMission) => {
          m.standing = 'terminal';
          m.lifecycleState = 'COMPLETED';
        },
        () => undefined,
      ]) {
        const s = fresh();
        const m = find(s, BOUND_REF);
        make(m);
        verifiedNotReported(m, holds);
        if (m.v2.projected) m.v2.missionState.completion.verifiedSuccess = null;
        const r = status(s, m);
        assert.notEqual(r.status, 'verified', name);
        assert.equal(r.qualification, DI_COMPLETION_NOT_AFFIRMED, name);
        assert.doesNotMatch(r.qualification ?? '', /verif/, name);
        assert.ok(!r.sources.includes('v2.missionState.completion.verifiedSuccess = true'), name);
      }
    }
  });
  test('control: the same reported shortfall WITHOUT verified success keeps its qualification (false, and not recorded)', () => {
    for (const verifiedSuccess of [false, null]) {
      const s = fresh();
      const m = find(s, BOUND_REF);
      verifiedNotReported(m, ['review_unknown']);
      if (m.v2.projected) m.v2.missionState.completion.verifiedSuccess = verifiedSuccess;
      assert.equal(status(s, m).qualification, DI_COMPLETION_NOT_AFFIRMED, String(verifiedSuccess));
    }
  });
});

describe('Deliverable 2: the cancel_quiescent basis (a Mission Core cancel whose quiescence was affirmatively recorded)', () => {
  const stale = (s: DiOfficeState) => {
    s.live = false;
    s.notLiveReason = 'client_expired:valid_until_passed';
    return s;
  };
  const cancelled = (s: DiOfficeState, quiescence: string | null) => {
    const m = find(s, BOUND_REF);
    nobodyLive(m);
    m.standing = 'terminal';
    m.lifecycleState = 'CANCELLED';
    m.cancelQuiescence = quiescence;
    return m;
  };
  test('each approved value releases the Orchestrator, fresh AND stale, naming mission.cancelQuiescence', () => {
    for (const make of [fresh, () => stale(fresh())])
      for (const value of DI_CAPACITY_RELEASE.cancelQuiescent) {
        const s = make();
        const c = status(s, cancelled(s, value)).capacity;
        assert.equal(c.bears, 'none', value);
        assert.equal(c.bears === 'none' && c.basis, 'cancel_quiescent', value);
        assert.equal(c.bears === 'none' && c.source, `mission.cancelQuiescence = "${value}"`, value);
      }
  });
  test('unproven, not_applicable, absent, or the value on a record that is not CANCELLED: no basis, capacity not known', () => {
    for (const value of ['unproven', 'not_applicable', 'owned_group_already_gone', null]) {
      const s = fresh();
      assert.equal(status(s, cancelled(s, value)).capacity.bears, 'not_known', String(value));
    }
    const s = fresh();
    const blocked = cancelled(s, 'task_observed_stopped');
    blocked.lifecycleState = 'BLOCKED';
    assert.equal(status(s, blocked).capacity.bears, 'not_known');
  });
  test('the approved set is exactly three values', () => {
    assert.deepEqual([...DI_CAPACITY_RELEASE.cancelQuiescent].sort(), ['nothing_started', 'owned_group_reaped', 'task_observed_stopped']);
  });
});
