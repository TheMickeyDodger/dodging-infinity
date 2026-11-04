/**
 * Deliverable 1: a Mission whose run NEVER STARTED reads as never started on EVERY surface, in the one contract's words (diMissionStatus.ts), and never as Started, Running,
 * Building or Team working. The Missions are the office state the REAL server projects from a labelled packaged fixture (FIXTURE, not live): its Herdr roots with no current
 * task carry the catalog's own `runStarted: false`.
 *
 * Surfaces: the Missions list rows and pills, the Missions table (Status column, Running filter, Mine scope, Repos), Mission details, the rail, Reception, the office panel's
 * Mission cards, and the request panel's journey and entry counts. Each says the same word for the same status.
 */
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';

import type { DiOfficeState } from '../../core/src/messages.ts';
import {
  entryWords,
  INITIAL_STATE,
  type MrState,
  parseOverview,
} from '../src/components/diMissionRequestModel.ts';
import { Journey } from '../src/components/DiMissionRequestParts.tsx';
import {
  DI_COMPLETION_NOT_AFFIRMED,
  DI_MISSION_STATUS,
  DI_MISSION_STATUS_IDS,
  requestStageStatus,
} from '../src/components/diMissionStatus.ts';
import { DiOfficePanel } from '../src/components/DiOfficePanel.tsx';
import { buildInbox } from '../src/components/diReceptionItems.ts';
import { missionStateSamples } from '../src/components/diSampleStates.ts';
import {
  DI_HARNESS_REPO,
  DI_SCAN_LOCK,
  DI_SCAN_V3_CAST,
  type DiScanRow,
  scanRowsFromOffice,
} from '../src/components/diScanModel.ts';
import {
  continueAskReason,
  DI_ORCHESTRATOR_LIMIT,
  DI_TEAM_UNAVAILABLE,
  missionsShown,
  orchestratorUse,
  receptionFromInbox,
} from '../src/components/diScanPagesModel.ts';
import { DiScanPanel } from '../src/components/DiScanPanel.tsx';
import type { OfficeState } from '../src/office/engine/officeState.ts';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
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
/** The catalog's own never-started Missions in the fixture: Herdr roots with no current task (`runStarted: false`). */
const NEVER = base.missions.missions.filter((m) => m.runStarted === false).map((m) => m.missionRef);
/** The bound Mission Core Mission with four live, confirmed members (`runStarted: true`). */
const RUNNING_REF = 'mc:mn-1aa604085c36ca55d01b57634ee13f41';
const NOT_STARTED = DI_MISSION_STATUS.not_started.label;
/** Words that claim a run started, or that a team is working: never said of a Mission that never started. */
const CLAIMS_STARTED = /\bStarted\b|Building|Team working|>Running<|Start requested/;
const noop = () => undefined;

const rows = scanRowsFromOffice(base);
const row = (ref: string): DiScanRow => rows.find((r) => r.id === ref)!;
const tr = (html: string, ref: string): string =>
  new RegExp(
    `<tr[^>]*data-mission-ref="${ref.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}"[\\s\\S]*?</tr>`,
  ).exec(html)?.[0] ?? '';

describe('the fixture holds what these tests rely on', () => {
  it('the real projection carries never-started Missions and a running one', () => {
    expect(NEVER.length).toBeGreaterThan(0);
    expect(base.missions.missions.find((m) => m.missionRef === RUNNING_REF)?.runStarted).toBe(true);
  });
});

describe('the Missions list rows and their pills', () => {
  it('every never-started Mission wears exactly the Not started pill, is open, and is KNOWN not to be running', () => {
    for (const ref of NEVER) {
      const r = row(ref);
      expect(r.status?.status, ref).toBe('not_started');
      expect(
        r.pills.filter((p) => p.id !== 'needs').map((p) => p.label),
        ref,
      ).toEqual([NOT_STARTED]);
      expect(r.tab, ref).toBe('open');
      expect(r.state.running, ref).toEqual({ known: true, value: false });
    }
  });
  it('the running Mission wears the contract’s Running, the same word the badge and the filter say', () => {
    expect(row(RUNNING_REF).pills.map((p) => p.label)).toContain(DI_MISSION_STATUS.running.label);
  });
});

describe('the Missions table', () => {
  const html = renderToStaticMarkup(
    createElement(DiScanPanel, { rows, initialView: 'missions', onMinimize: noop }),
  );
  it('a never-started row’s Status column says Not started, never a started word', () => {
    for (const ref of NEVER) {
      const cells = tr(html, ref);
      expect(cells, ref).toContain(NOT_STARTED);
      expect(cells, ref).not.toMatch(CLAIMS_STARTED);
    }
  });
  it('the Running filter never shows a never-started Mission; it does show the running one', () => {
    const running = missionsShown(rows, 'running').map((r) => r.id);
    for (const ref of NEVER) expect(running).not.toContain(ref);
    expect(running).toContain(RUNNING_REF);
  });
  it('open Missions appear on Mine, the default scope (Decision 29); Team stays drawn and unavailable with its reason', () => {
    expect(html).toMatch(/data-testid="di-missions-scope-mine"[^>]*aria-pressed="true"/);
    for (const ref of NEVER) expect(tr(html, ref), ref).not.toBe('');
    const team = /<button[^>]*data-testid="di-missions-scope-team"[^>]*>/.exec(html)?.[0] ?? '';
    expect(team).toContain('disabled=""');
    expect(team).toContain(`title="${DI_TEAM_UNAVAILABLE}"`);
  });
  it('a row says its harness repo and that its target count is not reported (AC-4.4-13), never a zero; the specimen keeps its sample count', () => {
    const real = tr(html, NEVER[0]!);
    expect(real).toContain(`${DI_HARNESS_REPO} + targets not reported`);
    const r = row(NEVER[0]!);
    expect(r.table?.repos?.targets.known).toBe(false);
    const specimen = renderToStaticMarkup(
      createElement(DiScanPanel, { rows: DI_SCAN_LOCK, initialView: 'missions', onMinimize: noop }),
    );
    expect(tr(specimen, 'lock-next-panel')).toContain(`${DI_HARNESS_REPO} + 1`);
  });
});

describe('Mission details and the rail', () => {
  it('Mission details of a never-started Mission say Not started and no started word', () => {
    const ref = NEVER[0]!;
    const html = renderToStaticMarkup(
      createElement(DiScanPanel, {
        rows,
        initialSelectedId: ref,
        initialView: 'mission',
        onMinimize: noop,
      }),
    );
    const pills = [
      ...html.matchAll(/data-testid="di-scan-pill"[^>]*>[\s\S]*?<\/span>([^<]*)<\/span>/g),
    ].map((m) => m[1]);
    expect(pills).toContain(NOT_STARTED);
    expect(html).not.toMatch(/Team working|\bStarted\b|Building/);
  });
  it('the rail lists a never-started Mission with the same word', () => {
    const html = renderToStaticMarkup(
      createElement(DiScanPanel, { rows, initialView: 'list', onMinimize: noop }),
    );
    const words = [...html.matchAll(/data-testid="di-rail-orchestrator-status">([^<]*)</g)].map(
      (m) => m[1],
    );
    expect(words).toContain(NOT_STARTED);
    expect(words).not.toContain('Building');
  });
});

describe('Reception', () => {
  it('an item about a never-started Mission carries that Mission’s status, the same pill its row wears', () => {
    const s = fresh();
    const ref = NEVER[0]!;
    s.reception!.entries = [{ ...s.reception!.entries[0]!, missionRef: ref }];
    const officeRows = scanRowsFromOffice(s);
    const reception = receptionFromInbox(
      buildInbox(s.reception!, { kind: 'not_provided' }, s.missions.missions, new Set()),
      s.reception!,
      officeRows,
    );
    const card = [...reception.cards, ...reception.information].find((c) => c.missionRef === ref);
    expect(card?.missionStatus?.label).toBe(NOT_STARTED);
    expect(card?.missionStatus).toEqual(officeRows.find((r) => r.id === ref)?.pills.at(-1));
    const html = renderToStaticMarkup(
      createElement(DiScanPanel, {
        rows: officeRows,
        reception,
        initialView: 'reception',
        onMinimize: noop,
      }),
    );
    expect(html).toMatch(
      new RegExp(
        `data-testid="di-reception-mission-status"[^>]*>[\\s\\S]*?</span>${NOT_STARTED}</span>`,
      ),
    );
  });
});

describe('the office panel', () => {
  const officeStub = {
    characters: new Map<number, unknown>(),
    selectedAgentId: null,
    cameraFollowId: null,
    areaMappings: {},
    getLayout: () => ({ areas: [] }),
  } as unknown as OfficeState;
  it('a never-started Mission’s card wears the Not started badge (its shape and word), never Running', () => {
    const html = renderToStaticMarkup(
      createElement(DiOfficePanel, {
        state: fresh(),
        officeState: officeStub,
        collapsed: false,
        onCollapsedChange: noop,
      }),
    );
    const cards = [...html.matchAll(/<article[^>]*data-mission-ref="([^"]+)"[\s\S]*?<\/article>/g)];
    const never = cards.filter((m) => NEVER.includes(m[1]!));
    expect(never.length).toBeGreaterThan(0);
    for (const [card, ref] of never) {
      expect(card, ref).toMatch(/data-status="not_started"/);
      expect(card, ref).toContain(`${DI_MISSION_STATUS.not_started.glyph}</span>${NOT_STARTED}`);
      expect(card, ref).not.toMatch(/data-status="running"/);
    }
  });
});

describe('the request panel', () => {
  it('a dispatched request is at Start requested, never “Started” or “Team working”', () => {
    const html = renderToStaticMarkup(createElement(Journey, { stage: 'dispatched' }));
    expect(html).toContain('Now: Start requested');
    expect(html).toContain('Next: Task observed');
    expect(html).not.toMatch(/\bStarted\b|Team working/);
  });
  it('a request whose start was only requested is never counted as running in the entry’s counts', () => {
    const overview = parseOverview({
      server_now: 1,
      capabilities: { decision: {}, run_available: false },
      requests: [
        {
          request_ref: `lr-${'1'.repeat(32)}`,
          mission_id: `mn-${'2'.repeat(32)}`,
          revision: 1,
          proposal_digest_sha256: 'a'.repeat(64),
          lifecycle: {
            stage: 'dispatched',
            label: 'Start requested',
            terminal: false,
            actions: [],
          },
        },
      ],
    })!;
    const s: MrState = { ...INITIAL_STATE, overview, availability: { kind: 'ok' } };
    const words = entryWords(s);
    expect(words).toContain('1 start requested');
    expect(words).not.toMatch(/running|in progress/);
  });
});

describe('C3 on every surface: an active, bound Herdr task whose team is not confirmed (the real projection) never reads as working', () => {
  // server/__tests__/diHerdrUnconfirmedTeamExport.test.ts: one real DiOffice over the packaged fixture, with the bound task's members as the roster emits them for an
  // unconfirmed team. Its `runStarted: true` is the producer's (for herdr_task: a task record exists).
  const projected = (
    JSON.parse(
      fs.readFileSync(
        path.join(root, 'test/fixtures/di-office-state-herdr-unconfirmed-team-v2.FIXTURE.json'),
        'utf-8',
      ),
    ) as { state: DiOfficeState }
  ).state;
  const REF = 'herd:20261017-150121-aaaaaa@5a4574665441';
  const TASK_OBSERVED = DI_MISSION_STATUS.task_observed.label;
  const projectedRows = scanRowsFromOffice(projected);
  it('the list row wears Task observed, is not running, and the Running filter does not show it', () => {
    const r = projectedRows.find((x) => x.id === REF)!;
    expect(r.pills.filter((p) => p.id !== 'needs').map((p) => p.label)).toEqual([TASK_OBSERVED]);
    expect(r.pills.map((p) => p.tone)).not.toContain('building');
    expect(r.state.running).toEqual({ known: true, value: false });
    expect(missionsShown(projectedRows, 'running').map((x) => x.id)).not.toContain(REF);
  });
  it('the Missions table and the office panel card say Task observed, never Running or Building', () => {
    const table = renderToStaticMarkup(
      createElement(DiScanPanel, {
        rows: projectedRows,
        initialView: 'missions',
        onMinimize: noop,
      }),
    );
    expect(tr(table, REF)).toContain(TASK_OBSERVED);
    expect(tr(table, REF)).not.toMatch(CLAIMS_STARTED);
    const officeStub = {
      characters: new Map<number, unknown>(),
      selectedAgentId: null,
      cameraFollowId: null,
      areaMappings: {},
      getLayout: () => ({ areas: [] }),
    } as unknown as OfficeState;
    const panel = renderToStaticMarkup(
      createElement(DiOfficePanel, {
        state: structuredClone(projected),
        officeState: officeStub,
        collapsed: false,
        onCollapsedChange: noop,
      }),
    );
    const card =
      new RegExp(
        `<article[^>]*data-mission-ref="${REF.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}"[\\s\\S]*?</article>`,
      ).exec(panel)?.[0] ?? '';
    expect(card).not.toBe('');
    expect(card).toMatch(/data-status="task_observed"/);
    expect(card).not.toMatch(/data-status="running"|Building|Team working/);
  });
});

const readState = (name: string): DiOfficeState =>
  (
    JSON.parse(fs.readFileSync(path.join(root, `test/fixtures/${name}`), 'utf-8')) as {
      state: DiOfficeState;
    }
  ).state;
const stubOffice = {
  characters: new Map<number, unknown>(),
  selectedAgentId: null,
  cameraFollowId: null,
  areaMappings: {},
  getLayout: () => ({ areas: [] }),
} as unknown as OfficeState;
/**
 * The word in a table row's Status cell (the fifth cell), read apart from its glyph. Deliverable 3: a Mission status's mark is its canonical glyph, so
 * wherever the cell names a status of the contract, its glyph must be drawn and must be THAT status's own (diMissionStatus.ts).
 */
const statusCell = (row: string, contract = true): string | undefined => {
  const cell = [...row.matchAll(/<td[^>]*>([\s\S]*?)<\/td>/g)][4]?.[1];
  if (cell === undefined) return undefined;
  const glyph = /data-testid="di-status-glyph"[^>]*>([^<]*)</.exec(cell)?.[1];
  const word = cell.replace(/<span[^>]*data-testid="di-status-glyph"[^>]*>[^<]*<\/span>/, '').replace(/<[^>]+>/g, '');
  const id = DI_MISSION_STATUS_IDS.find((x) => DI_MISSION_STATUS[x].label === word);
  if (contract && id !== undefined) expect(glyph, `${word} wears its own glyph`).toBe(DI_MISSION_STATUS[id].glyph);
  return word;
};
const escapeRe = (text: string) => text.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
const articleFor = (html: string, ref: string) =>
  new RegExp(`<article[^>]*data-mission-ref="${escapeRe(ref)}"[\\s\\S]*?</article>`).exec(
    html,
  )?.[0] ?? '';
/** Words that claim work started or is under way. */
const WORKING = /\bStarted\b|Building|Team working|>Running<|Start requested|Task observed/;

describe('review finding 2: a request waiting on proof reads as waiting, never as an ongoing task observation (rendered)', () => {
  it('the rendered journey: the task observation is DONE and the step now is Waiting, its proof-waiting status; nothing claims Verified', () => {
    const html = renderToStaticMarkup(createElement(Journey, { stage: 'pending_proof' }));
    expect(html).toContain('Done: Task observed');
    expect(html).toContain(`Now: ${DI_MISSION_STATUS.waiting.label}`);
    expect(html).not.toContain('Now: Task observed');
    expect(html).not.toMatch(/Verified/);
    // the same word the request's status chip says
    expect(requestStageStatus('pending_proof', null).label).toBe(DI_MISSION_STATUS.waiting.label);
  });
  it('the other run stages keep their own truthful steps', () => {
    const running = renderToStaticMarkup(createElement(Journey, { stage: 'running' }));
    expect(running).toContain('Now: Task observed');
    expect(running).toContain('Next: Verified');
    const verified = renderToStaticMarkup(createElement(Journey, { stage: 'verified' }));
    expect(verified).toContain('Done: Task observed');
    expect(verified).toContain('Now: Verified');
  });
});

describe('review finding 3: Herdr tasks whose workers NEVER STARTED, on their REAL projection path (unverified, not corroborated)', () => {
  // The real server projection (diOfficeStateExport) of the packaged fixture, unedited. A Herdr task is bound only when a recorded role is held by a live agent on its
  // recorded session (mission_sources/roster.py `_corroborated`); with none, the roster withholds the team (`unverified` / `task_record_not_corroborated_by_a_live_session`).
  // These are the ones with NO runtime agent at their root at all: no worker exists to be live.
  const state = readState('di-office-state-session-mismatch-v2.FIXTURE.json');
  const agentRoots = new Set((state.runtimeAgents?.agents ?? []).map((a) => a.rootRef));
  const NEVER_STARTED = state.missions.missions.filter(
    (m) =>
      m.sourceKind === 'herdr_task' &&
      m.standing === 'active' &&
      m.bindingState === 'unverified' &&
      m.bindingReason === 'task_record_not_corroborated_by_a_live_session' &&
      !agentRoots.has(m.missionRef),
  );
  const refs = NEVER_STARTED.map((m) => m.missionRef);
  const rows = scanRowsFromOffice(state);
  it('the projection holds them, as the producer emits them: active task records, roster withheld, no worker anywhere, and runStarted true (a task record exists)', () => {
    expect(NEVER_STARTED.length).toBeGreaterThanOrEqual(10);
    for (const m of NEVER_STARTED) {
      expect(m.runStarted, m.missionRef).toBe(true);
      expect(m.members.state, m.missionRef).toBe('unavailable');
      expect(m.members.members, m.missionRef).toEqual([]);
    }
  });
  it('every one reads Unavailable (its team cannot be read) with the producer’s reason, never a working word, and is not counted as running', () => {
    for (const r of rows.filter((x) => refs.includes(x.id))) {
      expect(r.status?.status, r.id).toBe('unavailable');
      expect(r.status?.why?.codes, r.id).toContain(
        'binding_unverified:task_record_not_corroborated_by_a_live_session',
      );
      expect(r.pills.map((p) => p.label).join(' '), r.id).not.toMatch(WORKING);
      expect(r.state.running.known, r.id).toBe(false);
    }
    const running = missionsShown(rows, 'running').map((x) => x.id);
    for (const ref of refs) expect(running).not.toContain(ref);
  });
  it('the Missions table, Mission details, the rail and the office panel card say no working word for any of them', () => {
    const table = renderToStaticMarkup(
      createElement(DiScanPanel, { rows, initialView: 'missions', onMinimize: noop }),
    );
    // the rail of THESE rows only (the office's other Missions are really running, and say so)
    const rail = renderToStaticMarkup(
      createElement(DiScanPanel, {
        rows: rows.filter((x) => refs.includes(x.id)),
        initialView: 'list',
        onMinimize: noop,
      }),
    );
    const panel = renderToStaticMarkup(
      createElement(DiOfficePanel, {
        state: structuredClone(state),
        officeState: stubOffice,
        collapsed: false,
        onCollapsedChange: noop,
      }),
    );
    for (const ref of refs) {
      expect(tr(table, ref), ref).not.toMatch(WORKING);
      const card = articleFor(panel, ref);
      if (card !== '') {
        expect(card, ref).toMatch(/data-status="unavailable"/);
        expect(card, ref).not.toMatch(/data-status="(running|task_observed|start_requested)"/);
      }
    }
    const railWords = [...rail.matchAll(/data-testid="di-rail-orchestrator-status">([^<]*)</g)].map(
      (m) => m[1],
    );
    expect(railWords.length).toBe(refs.length);
    for (const word of railWords) expect(word).toBe(DI_MISSION_STATUS.unavailable.label);
    const details = renderToStaticMarkup(
      createElement(DiScanPanel, {
        rows,
        initialSelectedId: refs[0]!,
        initialView: 'mission',
        onMinimize: noop,
      }),
    );
    expect(details).not.toMatch(/Team working|\bStarted\b|Building/);
    expect(details).toContain(DI_MISSION_STATUS.unavailable.label);
  });
  it('Reception: an item about one of them carries the same Unavailable pill its row wears', () => {
    const s = structuredClone(state);
    s.reception!.entries = [{ ...s.reception!.entries[0]!, missionRef: refs[0]! }];
    const officeRows = scanRowsFromOffice(s);
    const reception = receptionFromInbox(
      buildInbox(s.reception!, { kind: 'not_provided' }, s.missions.missions, new Set()),
      s.reception!,
      officeRows,
    );
    const card = [...reception.cards, ...reception.information].find(
      (c) => c.missionRef === refs[0],
    );
    expect(card?.missionStatus?.label).toBe(DI_MISSION_STATUS.unavailable.label);
  });
  it('capacity: an unknown is never spare capacity: each one counts as NOT KNOWN in the Orchestrator guard', () => {
    const only = rows.filter((x) => refs.includes(x.id));
    expect(orchestratorUse(only)).toEqual({ holding: 0, notKnown: only.length });
  });
});

describe('review finding 4: the Orchestrator capacity guard is a separate input from the display count', () => {
  const projected = readState('di-office-state-herdr-unconfirmed-team-v2.FIXTURE.json');
  const TASK = 'herd:20261017-150121-aaaaaa@5a4574665441';
  /** The office with `n` copies of the bound, active, unconfirmed Herdr task (the real projection's record), each its own Mission. */
  const withCopies = (
    n: number,
    change: (m: DiOfficeState['missions']['missions'][number]) => void = () => undefined,
  ): DiOfficeState => {
    const s = structuredClone(projected);
    const task = s.missions.missions.find((m) => m.missionRef === TASK)!;
    s.missions.missions = Array.from({ length: n }, (_, i) => {
      const copy = structuredClone(task);
      copy.missionRef = `${TASK}#copy-${i}`;
      change(copy);
      return copy;
    });
    s.reception!.entries = [];
    return s;
  };
  it('fifteen bound active tasks with no confirmed team: the DISPLAY says none is running, and the GUARD refuses a sixteenth', () => {
    const rows = scanRowsFromOffice(withCopies(DI_ORCHESTRATOR_LIMIT));
    expect(rows.every((r) => r.status?.status === 'task_observed')).toBe(true);
    // the display stays honest: none of them is Running
    expect(missionsShown(rows, 'running')).toEqual([]);
    // the guard keeps its strength: every one holds an Orchestrator
    const use = orchestratorUse(rows);
    expect(use).toEqual({ holding: DI_ORCHESTRATOR_LIMIT, notKnown: 0 });
    expect(continueAskReason('Fix the probe', use, true)).toBe(
      `${DI_ORCHESTRATOR_LIMIT} of ${DI_ORCHESTRATOR_LIMIT} Orchestrators are in use. Finish or stop one to start another.`,
    );
    // and the New Mission page refuses on screen
    const page = renderToStaticMarkup(
      createElement(DiScanPanel, { rows, initialView: 'new', onMinimize: noop }),
    );
    expect(page).toMatch(/data-testid="di-new-limit"/);
  });
  it('an unknown never reads as spare capacity: fourteen held and one not known refuses; fifteen not known refuses; fourteen held alone does not', () => {
    const fourteenAndUnknown = scanRowsFromOffice(
      withCopies(DI_ORCHESTRATOR_LIMIT, (m) => {
        if (m.missionRef.endsWith('#copy-0'))
          m.members = { state: 'not_recorded', reason: null, members: [] };
      }),
    );
    expect(orchestratorUse(fourteenAndUnknown)).toEqual({
      holding: DI_ORCHESTRATOR_LIMIT - 1,
      notKnown: 1,
    });
    expect(continueAskReason('Fix the probe', orchestratorUse(fourteenAndUnknown), true)).toMatch(
      /in use or cannot be checked \(14 in use, 1 cannot be checked\)/,
    );
    const stale = withCopies(DI_ORCHESTRATOR_LIMIT);
    stale.live = false;
    stale.notLiveReason = 'client_expired:valid_until_passed';
    expect(orchestratorUse(scanRowsFromOffice(stale))).toEqual({
      holding: 0,
      notKnown: DI_ORCHESTRATOR_LIMIT,
    });
    expect(
      continueAskReason('Fix the probe', orchestratorUse(scanRowsFromOffice(stale)), true),
    ).not.toBeNull();
    const fourteen = orchestratorUse(scanRowsFromOffice(withCopies(DI_ORCHESTRATOR_LIMIT - 1)));
    expect(continueAskReason('Fix the probe', fourteen, true)).toBeNull();
  });
  it('a record its OWN PROGRESS closed is still counted by its execution: live workers hold, an office not live is not known (round 3, finding 1)', () => {
    const withProgress = (live: boolean) =>
      withCopies(DI_ORCHESTRATOR_LIMIT, (m) => {
        if (m.v2.projected)
          m.v2.missionState.progress = {
            state: 'observed',
            reason: null,
            standing: 'verified',
            progress: 'ABANDONED',
            unrecognized: null,
            closureReason: 'abandoned_by_caller',
          };
        if (live)
          for (const x of m.members.members) {
            x.live = true;
            x.classification = 'present';
          }
      });
    const liveRows = scanRowsFromOffice(withProgress(true));
    // CHANGED IN Deliverable 2: "closed" is no filing now. Archived needs the backend's retention reading (this fixture carries none), so a record whose own
    // progress closed while its team is observed working stays with the active Missions; its execution still counts (below).
    expect(liveRows.every((r) => r.tab === 'open')).toBe(true);
    expect(liveRows.every((r) => r.status?.status === 'running')).toBe(true);
    expect(orchestratorUse(liveRows)).toEqual({ holding: DI_ORCHESTRATOR_LIMIT, notKnown: 0 });
    expect(continueAskReason('Fix the probe', orchestratorUse(liveRows), true)).not.toBeNull();
    const stale = withProgress(true);
    stale.live = false;
    stale.notLiveReason = 'client_expired:valid_until_passed';
    expect(orchestratorUse(scanRowsFromOffice(stale))).toEqual({
      holding: 0,
      notKnown: DI_ORCHESTRATOR_LIMIT,
    });
  });
  it('(corrected in round 4) a Herdr task the ENGINE records as stopped holds none, fresh or stale: a positive basis named by its source field, not its terminal standing', () => {
    for (const live of [true, false]) {
      const closed = withCopies(DI_ORCHESTRATOR_LIMIT, (m) => {
        m.standing = 'terminal';
        m.lifecycleState = 'COMPLETE';
      });
      if (!live) {
        closed.live = false;
        closed.notLiveReason = 'client_expired:valid_until_passed';
      }
      const rows = scanRowsFromOffice(closed);
      for (const r of rows)
        expect(r.status?.capacity, String(live)).toEqual({ bears: 'none', basis: 'engine_task_stopped', source: 'mission.lifecycleState = "COMPLETE"' });
      expect(orchestratorUse(rows)).toEqual({ holding: 0, notKnown: 0 });
    }
  });
  it('round 4, finding 2: fifteen Mission Core records terminal BLOCKED by a failed verification (verify_target_not_stopped) make the guard REFUSE, fresh AND stale', () => {
    // the target can still run; the roster withholds their members (it stops nothing), and neither a fresh answer nor an empty roster is a basis
    const core = readState('di-office-state-session-mismatch-v2.FIXTURE.json');
    const source = core.missions.missions.find((m) => m.missionRef === 'mc:mn-1aa604085c36ca55d01b57634ee13f41')!;
    const blocked = (live: boolean): DiOfficeState => {
      const s = structuredClone(core);
      s.missions.missions = Array.from({ length: DI_ORCHESTRATOR_LIMIT }, (_, i) => {
        const m = structuredClone(source);
        m.missionRef = `${source.missionRef}#blocked-${i}`;
        m.standing = 'terminal';
        m.lifecycleState = 'BLOCKED';
        m.bindingState = 'not_applicable';
        m.bindingReason = 'mission_not_active';
        m.members = { state: 'projected_reason', reason: 'binding_not_applicable:mission_not_active', members: [] };
        return m;
      });
      s.reception!.entries = [];
      if (!live) {
        s.live = false;
        s.notLiveReason = 'client_expired:valid_until_passed';
      }
      return s;
    };
    for (const live of [true, false]) {
      const rows = scanRowsFromOffice(blocked(live));
      // they read as the record says (Stopped, or Stale when not live); the guard still counts every one as not known
      // CHANGED IN Deliverable 2: with no backend retention reading (and no positive basis), they stay with the active Missions, never Archived
      expect(rows.every((r) => r.tab === 'open'), String(live)).toBe(true);
      expect(orchestratorUse(rows), String(live)).toEqual({ holding: 0, notKnown: DI_ORCHESTRATOR_LIMIT });
      expect(continueAskReason('Fix the probe', orchestratorUse(rows), true), String(live)).toMatch(/cannot be checked \(0 in use, 15 cannot be checked\)/);
      const page = renderToStaticMarkup(createElement(DiScanPanel, { rows, initialView: 'new', onMinimize: noop }));
      expect(page, String(live)).toMatch(/data-testid="di-new-limit"/);
    }
  });
});

describe('review finding 5: current product samples use the canonical status words', () => {
  it('the labelled specimen and the v3 sample cast say Running, never Building, in the contract’s words', () => {
    for (const row of [...DI_SCAN_LOCK, ...DI_SCAN_V3_CAST]) {
      for (const pill of row.pills) expect(pill.label, row.id).not.toBe('Building');
      if (row.pills.some((p) => p.tone === 'building'))
        expect(
          row.pills.map((p) => p.label),
          row.id,
        ).toContain(DI_MISSION_STATUS.running.label);
    }
  });
  it('the AC5 sample states say Running, with the contract’s own shape', () => {
    const running = missionStateSamples(true).find((s) => s.tone === 'running');
    expect(running?.state).toBe(DI_MISSION_STATUS.running.label);
    expect(running?.glyph).toBe(DI_MISSION_STATUS.running.glyph);
    expect(missionStateSamples(true).map((s) => s.state)).not.toContain('Building');
  });
});

describe('review finding 6 on every surface: a Mission ABANDONED before dispatch (the real projection) is closed, never Not started', () => {
  const state = readState('di-office-state-progress-abandoned-v2.FIXTURE.json');
  const REF = 'synthetic_mission_core:mn-00000000000000000000000000000009';
  const rows = scanRowsFromOffice(state);
  const row = rows.find((r) => r.id === REF)!;
  // CHANGED IN Deliverable 2: the backend's retention reading (exported with this projection) proves it finished with nothing started, so it files Archived at
  // once and leaves every other filter (AC-4.4-28), Done included. Its status is still Stopped, and it still holds no Orchestrator.
  it('its row is Stopped and filed under Archived; Archived shows it, Done and Running do not, and it holds no Orchestrator', () => {
    expect(row.pills.filter((p) => p.id !== 'needs').map((p) => p.label)).toEqual([
      DI_MISSION_STATUS.stopped.label,
    ]);
    expect(row.tab).toBe('archived');
    expect(missionsShown(rows, 'archived').map((r) => r.id)).toContain(REF);
    expect(missionsShown(rows, 'done').map((r) => r.id)).not.toContain(REF);
    expect(missionsShown(rows, 'running').map((r) => r.id)).not.toContain(REF);
    expect(orchestratorUse([row])).toEqual({ holding: 0, notKnown: 0 });
  });
  it('the Missions table’s Status column says Stopped (its attention is shown apart), and the office panel card says Stopped: one Mission, one status', () => {
    // CHANGED IN Deliverable 2: an archived Mission is listed under the Archived filter only, so the table is read there
    const table = renderToStaticMarkup(
      createElement(DiScanPanel, { rows, initialView: 'missions', initialMissionsStatus: 'archived', onMinimize: noop }),
    );
    expect(statusCell(tr(table, REF))).toBe(DI_MISSION_STATUS.stopped.label);
    expect(tr(table, REF)).not.toContain(NOT_STARTED);
    // Reception lists it: the attention is still said, beside the title, never in place of the status
    expect(tr(table, REF)).toMatch(/data-testid="di-missions-attention"[\s\S]*?Needs you/);
    const panel = renderToStaticMarkup(
      createElement(DiOfficePanel, {
        state: structuredClone(state),
        officeState: stubOffice,
        collapsed: false,
        onCollapsedChange: noop,
      }),
    );
    const card = articleFor(panel, REF);
    expect(card).not.toBe('');
    expect(card).toMatch(/data-status="stopped"/);
  });
});

describe('round 3, finding 2: the Missions table and the rail say the canonical lifecycle status, never attention in its place', () => {
  it('for EVERY Mission of the real projection, the table’s Status cell, the rail and the office card say the same status word', () => {
    const state = readState('di-office-state-session-mismatch-v2.FIXTURE.json');
    const officeRows = scanRowsFromOffice(state);
    // CHANGED IN Deliverable 2: an archived Mission is listed under the Archived filter only, so each row is read in the table of its own filing
    const tableFor = (filter: 'all' | 'archived') =>
      renderToStaticMarkup(
        createElement(DiScanPanel, { rows: officeRows, initialView: 'missions', initialMissionsStatus: filter, onMinimize: noop }),
      );
    const tables = { all: tableFor('all'), archived: tableFor('archived') };
    expect(officeRows.some((r) => r.tab === 'archived')).toBe(true);
    expect(officeRows.some((r) => r.tab !== 'archived')).toBe(true);
    const panel = renderToStaticMarkup(
      createElement(DiOfficePanel, {
        state: structuredClone(state),
        officeState: stubOffice,
        collapsed: false,
        onCollapsedChange: noop,
      }),
    );
    let compared = 0;
    for (const r of officeRows) {
      const word = r.status!.label;
      const table = r.tab === 'archived' ? tables.archived : tables.all;
      expect(statusCell(tr(table, r.id)), r.id).toBe(word);
      const card = articleFor(panel, r.id);
      if (card !== '') {
        expect(
          /data-testid="di-op-status"[^>]*>[\s\S]*?<\/span>([^<]*)<\/span>/.exec(card)?.[1],
          r.id,
        ).toBe(word);
        compared += 1;
      }
    }
    expect(compared).toBeGreaterThan(0);
  });
  it('a Mission that needs the person still shows its status in the Status column, and its attention beside its title', () => {
    const state = readState('di-office-state-session-mismatch-v2.FIXTURE.json');
    const running = 'mc:mn-1aa604085c36ca55d01b57634ee13f41';
    state.reception!.entries = [{ ...state.reception!.entries[0]!, missionRef: running }];
    const officeRows = scanRowsFromOffice(state);
    expect(officeRows.find((r) => r.id === running)?.pills[0]?.label).toBe('Needs you');
    const table = renderToStaticMarkup(
      createElement(DiScanPanel, { rows: officeRows, initialView: 'missions', onMinimize: noop }),
    );
    expect(statusCell(tr(table, running))).toBe(DI_MISSION_STATUS.running.label);
    expect(tr(table, running)).toMatch(/data-testid="di-missions-attention"[\s\S]*?Needs you/);
    const rail = renderToStaticMarkup(
      createElement(DiScanPanel, {
        rows: officeRows.filter((r) => r.id === running),
        initialView: 'list',
        onMinimize: noop,
      }),
    );
    expect(
      [...rail.matchAll(/data-testid="di-rail-orchestrator-status">([^<]*)</g)].map((m) => m[1]),
    ).toEqual([DI_MISSION_STATUS.running.label]);
  });
  it('the labelled specimen’s Status column says its status too (Running, not Needs you)', () => {
    const table = renderToStaticMarkup(
      createElement(DiScanPanel, { rows: DI_SCAN_LOCK, initialView: 'missions', onMinimize: noop }),
    );
    // the labelled specimen draws its own locked SAMPLE pills (DI_SCAN_LOCK), not a contract status result: they carry no status glyph and keep the dot
    expect(statusCell(tr(table, 'lock-next-panel'), false)).toBe(DI_MISSION_STATUS.running.label);
    expect(tr(table, 'lock-next-panel')).not.toContain('data-testid="di-status-glyph"');
  });
});

describe('round 3, finding 3 END TO END (parser, view, message builder, rendered): progress the source declares unavailable never renders Stopped', () => {
  const state = readState('di-office-state-progress-unavailable-v2.FIXTURE.json');
  const REF = 'synthetic_mission_core:mn-00000000000000000000000000000009';
  const rows = scanRowsFromOffice(state);
  it('the row, the table and the office card say Unavailable; it is not filed closed', () => {
    const row = rows.find((r) => r.id === REF)!;
    expect(row.status?.status).toBe('unavailable');
    expect(row.tab).toBe('open');
    const table = renderToStaticMarkup(
      createElement(DiScanPanel, { rows, initialView: 'missions', onMinimize: noop }),
    );
    expect(statusCell(tr(table, REF))).toBe(DI_MISSION_STATUS.unavailable.label);
    expect(tr(table, REF)).not.toContain(DI_MISSION_STATUS.stopped.label);
    const panel = renderToStaticMarkup(
      createElement(DiOfficePanel, {
        state: structuredClone(state),
        officeState: stubOffice,
        collapsed: false,
        onCollapsedChange: noop,
      }),
    );
    const card = articleFor(panel, REF);
    expect(card).toMatch(/data-status="unavailable"/);
    expect(card).not.toMatch(/data-status="stopped"/);
  });
});

describe('round 4, finding 1 on the surfaces: a reported completion awaiting its review never renders Failed beside a live team', () => {
  it('the table row and the office card say Running, never Failed', () => {
    const state = readState('di-office-state-session-mismatch-v2.FIXTURE.json');
    const REF = 'mc:mn-1aa604085c36ca55d01b57634ee13f41';
    const m = state.missions.missions.find((x) => x.missionRef === REF)!;
    if (m.v2.projected)
      m.v2.missionState.completion = {
        ...m.v2.missionState.completion,
        state: 'observed',
        reportedComplete: true,
        reportedSuccess: false,
        verifiedSuccess: false,
        holds: ['no_canonical_closure', 'review_unknown'],
      };
    const rows = scanRowsFromOffice(state);
    const table = renderToStaticMarkup(createElement(DiScanPanel, { rows, initialView: 'missions', onMinimize: noop }));
    expect(statusCell(tr(table, REF))).toBe(DI_MISSION_STATUS.running.label);
    expect(tr(table, REF)).not.toContain(DI_MISSION_STATUS.failed.label);
    const panel = renderToStaticMarkup(
      createElement(DiOfficePanel, { state: structuredClone(state), officeState: stubOffice, collapsed: false, onCollapsedChange: noop }),
    );
    const card = articleFor(panel, REF);
    expect(card).toMatch(/data-status="running"/);
    expect(card).not.toMatch(/data-status="failed"/);
    // round 5, finding 2: the qualification is RENDERED on the Running card, not only held in the model
    expect(card).toMatch(/data-testid="di-op-status-qualification"[^>]*>[^<]*its reported success is not affirmed: that is not a failure/);
  });
  it('round 5, finding 3: a Task observed card whose completion has NO review hold (fresh APPROVE, stale task report) renders no invented review cause', () => {
    const state = readState('di-office-state-session-mismatch-v2.FIXTURE.json');
    const REF = 'mc:mn-1aa604085c36ca55d01b57634ee13f41';
    const m = state.missions.missions.find((x) => x.missionRef === REF)!;
    for (const member of m.members.members) member.live = false;
    if (m.v2.projected)
      m.v2.missionState.completion = {
        ...m.v2.missionState.completion,
        state: 'observed',
        reportedComplete: true,
        reportedSuccess: false,
        verifiedSuccess: false,
        holds: ['no_canonical_closure'],
      };
    const panel = renderToStaticMarkup(
      createElement(DiOfficePanel, { state: structuredClone(state), officeState: stubOffice, collapsed: false, onCollapsedChange: noop }),
    );
    const card = articleFor(panel, REF);
    expect(card).toMatch(/data-status="task_observed"/);
    expect(card).toMatch(/data-testid="di-op-status-qualification"[^>]*>[^<]*its reported success is not affirmed/);
    // what the card says ABOUT the status names no review (the team's "reviewer" role elsewhere on the card is a member, not a cause)
    const said = [...card.matchAll(/data-testid="di-op-status-(?:sentence|qualification)"[^>]*>([^<]*)</g)].map((x) => x[1]!);
    expect(said.length).toBeGreaterThanOrEqual(2);
    for (const text of said) expect(text).not.toMatch(/review|because|\byet\b/i);
  });
});

describe('round 4, finding 4: the rail says the canonical status AND which Mission needs the person', () => {
  it('a Mission Reception lists shows its status and its own Needs you in the rail, both rendered', () => {
    const state = readState('di-office-state-session-mismatch-v2.FIXTURE.json');
    const REF = 'mc:mn-1aa604085c36ca55d01b57634ee13f41';
    state.reception!.entries = [{ ...state.reception!.entries[0]!, missionRef: REF }];
    const rows = scanRowsFromOffice(state);
    const rail = renderToStaticMarkup(createElement(DiScanPanel, { rows, initialView: 'list', onMinimize: noop }));
    const items = [...rail.matchAll(/<button[^>]*data-testid="di-rail-orchestrator"[^>]*>([\s\S]*?)<\/button>/g)].map((x) => x[1]!);
    const title = rows.find((r) => r.id === REF)!.title;
    const mine = items.filter((i) => i.includes('data-testid="di-rail-orchestrator-attention"'));
    // exactly the Mission Reception lists carries the attention signal, beside its canonical status
    expect(mine).toHaveLength(1);
    expect(mine[0]).toContain(title);
    expect(mine[0]).toMatch(/data-testid="di-rail-orchestrator-status">Running</);
    expect(mine[0]).toMatch(/data-testid="di-rail-orchestrator-attention"[^>]*>[\s\S]*?Needs you/);
    // the others say their status and no attention
    for (const other of items.filter((i) => !i.includes('di-rail-orchestrator-attention')))
      expect(other).toMatch(/data-testid="di-rail-orchestrator-status">[^<]+</);
  });
});

describe('round 5, finding 1 on the guard: workflows BLOCKED after a post-effect exception (no engine recorded) keep the guard closed', () => {
  it('fifteen such workflow-store rows make the guard REFUSE, fresh AND stale; runStarted = false frees none of them', () => {
    const core = readState('di-office-state-session-mismatch-v2.FIXTURE.json');
    const source = core.missions.missions.find((m) => m.missionRef === 'mc:mn-1aa604085c36ca55d01b57634ee13f41')!;
    const blocked = (live: boolean): DiOfficeState => {
      const s = structuredClone(core);
      s.missions.missions = Array.from({ length: DI_ORCHESTRATOR_LIMIT }, (_, i) => {
        const m = structuredClone(source);
        m.missionRef = `synthetic_workflow_store:wf-post-effect-${i}`;
        m.sourceKind = 'workflow_store';
        m.lifecycleModel = 'workflow_authority.workflow_phase/2';
        m.lifecycleState = 'BLOCKED';
        m.standing = 'terminal';
        m.runStarted = false;
        m.bindingState = 'not_applicable';
        m.bindingReason = 'mission_not_active';
        m.members = { state: 'projected_reason', reason: 'binding_not_applicable:mission_not_active', members: [] };
        if (m.v2.projected) m.v2.workflowPhase = { state: 'observed', phase: 'BLOCKED', unrecognized: null, problem: null };
        return m;
      });
      s.reception!.entries = [];
      if (!live) {
        s.live = false;
        s.notLiveReason = 'client_expired:valid_until_passed';
      }
      return s;
    };
    for (const live of [true, false]) {
      const rows = scanRowsFromOffice(blocked(live));
      expect(rows.map((r) => r.status?.capacity.bears), String(live)).toEqual(Array(DI_ORCHESTRATOR_LIMIT).fill('not_known'));
      expect(orchestratorUse(rows), String(live)).toEqual({ holding: 0, notKnown: DI_ORCHESTRATOR_LIMIT });
      expect(continueAskReason('Fix the probe', orchestratorUse(rows), true), String(live)).toMatch(/cannot be checked/);
    }
  });
});

describe('round 5, item 4 on the surfaces: a freshly bound team, every member confirmed live but idle or done, claims no current work anywhere', () => {
  it('the table row, the rail and the office card say Waiting, never Running, and claim no work; the guard still counts it as held', () => {
    const state = readState('di-office-state-session-mismatch-v2.FIXTURE.json');
    const REF = 'mc:mn-1aa604085c36ca55d01b57634ee13f41';
    const m = state.missions.missions.find((x) => x.missionRef === REF)!;
    m.members.members.forEach((member, i) => (member.activity = i % 2 === 0 ? 'idle' : 'done'));
    state.reception!.entries = [];
    const rows = scanRowsFromOffice(state);
    const row = rows.find((r) => r.id === REF)!;
    expect(row.status?.status).toBe('waiting');
    expect(row.status?.capacity.bears).toBe('holds');
    const CLAIMS_WORK = /\bworking\b|in progress|under way|>Running</i;
    const table = renderToStaticMarkup(createElement(DiScanPanel, { rows, initialView: 'missions', onMinimize: noop }));
    expect(statusCell(tr(table, REF))).toBe(DI_MISSION_STATUS.waiting.label);
    expect(tr(table, REF)).not.toMatch(CLAIMS_WORK);
    const rail = renderToStaticMarkup(createElement(DiScanPanel, { rows, initialView: 'list', onMinimize: noop }));
    const item = [...rail.matchAll(/<button[^>]*data-testid="di-rail-orchestrator"[^>]*>([\s\S]*?)<\/button>/g)]
      .map((x) => x[1]!)
      .find((i) => i.includes(row.title))!;
    expect(item).toMatch(/data-testid="di-rail-orchestrator-status">Waiting</);
    expect(item).not.toMatch(CLAIMS_WORK);
    const panel = renderToStaticMarkup(
      createElement(DiOfficePanel, { state: structuredClone(state), officeState: stubOffice, collapsed: false, onCollapsedChange: noop }),
    );
    const card = articleFor(panel, REF);
    expect(card).toMatch(/data-status="waiting"/);
    expect(card).toMatch(/data-testid="di-op-status-sentence"[^>]*>[^<]*none reports work/);
    expect(card).not.toMatch(CLAIMS_WORK);
  });
});

describe('round 6 on the surfaces: a verified closure whose reported success is not affirmed renders Verified, with no qualification denying it', () => {
  const COMBOS: ReadonlyArray<[string, string[]]> = [
    ['review unavailable', ['review_unavailable']],
    ['review unknown', ['review_unknown']],
    ['fresh APPROVE, inapplicable task report', []],
  ];
  it.each(COMBOS)('%s: the office card says Verified and nothing that denies it', (_name, holds) => {
    const state = readState('di-office-state-session-mismatch-v2.FIXTURE.json');
    const REF = 'mc:mn-1aa604085c36ca55d01b57634ee13f41';
    const m = state.missions.missions.find((x) => x.missionRef === REF)!;
    m.standing = 'terminal';
    m.lifecycleState = 'COMPLETED';
    if (m.v2.projected)
      m.v2.missionState.completion = {
        ...m.v2.missionState.completion,
        state: 'observed',
        reportedComplete: true,
        reportedSuccess: false,
        verifiedSuccess: true,
        holds,
      };
    const panel = renderToStaticMarkup(
      createElement(DiOfficePanel, { state: structuredClone(state), officeState: stubOffice, collapsed: false, onCollapsedChange: noop }),
    );
    const card = articleFor(panel, REF);
    expect(card).toMatch(/data-status="verified"/);
    expect(card).not.toMatch(/data-testid="di-op-status-qualification"/);
    expect(card).not.toMatch(/not affirmed/);
    const said = [...card.matchAll(/data-testid="di-op-status-sentence"[^>]*>([^<]*)</g)].map((x) => x[1]!);
    expect(said.length).toBeGreaterThanOrEqual(1);
    for (const text of said) expect(text).not.toMatch(/not verified/);
  });
  it.each(COMBOS)('%s, verifiedSuccess NULL (not known): the card qualifies reported success only, and never says "not verified" of it', (_name, holds) => {
    const state = readState('di-office-state-session-mismatch-v2.FIXTURE.json');
    const REF = 'mc:mn-1aa604085c36ca55d01b57634ee13f41';
    const m = state.missions.missions.find((x) => x.missionRef === REF)!;
    if (m.v2.projected)
      m.v2.missionState.completion = {
        ...m.v2.missionState.completion,
        state: 'observed',
        reportedComplete: true,
        reportedSuccess: false,
        verifiedSuccess: null,
        holds,
      };
    const panel = renderToStaticMarkup(
      createElement(DiOfficePanel, { state: structuredClone(state), officeState: stubOffice, collapsed: false, onCollapsedChange: noop }),
    );
    const card = articleFor(panel, REF);
    expect(card).toMatch(/data-status="running"/);
    const qualification = /data-testid="di-op-status-qualification"[^>]*>([^<]*)</.exec(card)?.[1] ?? '';
    expect(qualification).toBe(DI_COMPLETION_NOT_AFFIRMED);
    expect(qualification).not.toMatch(/verif/);
  });
});
