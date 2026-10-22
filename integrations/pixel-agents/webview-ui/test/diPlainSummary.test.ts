/**
 * Product experience (task 20261021-182639-dd2800): the PLAIN FIRST LAYER of the DI panel —
 * Mission titles, short source-qualified refs, the team line, one short phrase per agent row, the
 * runtime agents grouped by what the source establishes, and the attention entry.
 *
 * The truth rules these pin (the brief's §3.1 / §3.1a): process observation is never activity;
 * "Working" only where the agent's own terminal reports busy in a current observation; no current
 * Mission is an assignment record, never idle; a title is a verbatim slice of the objective or an
 * honest placeholder, never invented; a shared reason is said once, a differing row says its own;
 * and a configured Mission request route never depends on the attention count.
 *
 * Data: the office state the real server projects from the labelled packaged fixture
 * `herdr-task-session-mismatch-v2` (FIXTURE, not live); each case changes only what it is about.
 */
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { test } from 'vitest';

import type {
  DiMission,
  DiOfficeState,
  DiReceptionEntry,
  DiRuntimeAgent,
} from '../../core/src/messages.ts';
import {
  reviewNotOfferedConsequence,
  UNAVAILABLE_WORDS,
  unavailableWords,
} from '../src/components/diCodeReviewModel.ts';
import type { DiMissionRequestAccess } from '../src/components/DiMissionRequestEntry.tsx';
import { INITIAL_STATE } from '../src/components/diMissionRequestModel.ts';
import { plainIdentityReason } from '../src/components/diNavigation.ts';
import { navigationOrder } from '../src/components/diOfficeFormat.ts';
import { DiOfficePanel, RuntimeAgentsSection } from '../src/components/DiOfficePanel.tsx';
import {
  agentNow,
  agentPhrase,
  attentionSummary,
  firstLayerName,
  MISSION_TITLE_PLACEHOLDER,
  missionResults,
  missionTitle,
  navigationOrientation,
  plainObserved,
  runtimeAgentMeaning,
  runtimeGroups,
  shortMissionRefs,
  teamSummary,
  viewLimits,
} from '../src/components/diPlainSummary.ts';
import { DI_MISSION_TITLE_MAX_CHARS } from '../src/constants.ts';
import type { OfficeState } from '../src/office/engine/officeState.ts';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const base = (
  JSON.parse(
    fs.readFileSync(
      path.join(root, 'test/fixtures/di-office-state-session-mismatch-v2.FIXTURE.json'),
      'utf-8',
    ),
  ) as { state: DiOfficeState }
).state;
const fresh = (): DiOfficeState => structuredClone(base);
/** The bound, active Mission Core Mission with four live, confirmed, busy members. */
const BOUND_REF = 'mc:mn-1aa604085c36ca55d01b57634ee13f41';
const bound = (s: DiOfficeState): DiMission =>
  s.missions.missions.find((m) => m.missionRef === BOUND_REF)!;

function withObjective(value: string | null): DiMission {
  const m = bound(fresh());
  m.objective =
    value === null
      ? { state: 'not_recorded', value: null, reason: null }
      : { state: 'observed', value, reason: null };
  return m;
}

/** Occurrences of `needle` in rendered markup, read as text (React escapes ' " & in markup). */
const count = (haystack: string, needle: string) =>
  haystack
    .replaceAll('&#x27;', "'")
    .replaceAll('&quot;', '"')
    .replaceAll('&amp;', '&')
    .split(needle).length - 1;

// ── titles ─────────────────────────────────────────────────────────────────────────────────────

test('a title is the objective’s own leading sentence; a long one is cut at a word with an ellipsis', () => {
  const short = missionTitle(withObjective('Speed up the sample test suite. Then document it.'));
  assert.deepEqual(short, { kind: 'sentence', text: 'Speed up the sample test suite' });

  const long =
    'Migrate the sample ingestion service from the nightly batch to the streaming pipeline without losing any late-arriving records along the way. Keep the API stable.';
  const t = missionTitle(withObjective(long));
  assert.equal(t.kind, 'sentence');
  assert.ok(t.text.endsWith('…'), t.text);
  assert.ok(t.text.length <= DI_MISSION_TITLE_MAX_CHARS, `${t.text.length}`);
  const kept = t.text.slice(0, -1);
  assert.ok(long.startsWith(kept), 'a verbatim prefix of the objective');
  assert.match(long[kept.length], /[\s,;:]/, 'cut at a word boundary, never mid-word');
});

test('boilerplate is never a title: headers, AUTHORIZED MISSION and ids are skipped to the meaning', () => {
  assert.equal(
    missionTitle(
      withObjective(
        'OBJECTIVE\nMake the sample panel explain itself in plain English.\n\nCONSTRAINTS\n- none',
      ),
    ).text,
    'Make the sample panel explain itself in plain English',
  );
  assert.equal(
    missionTitle(
      withObjective(
        'AUTHORIZED MISSION 7f3c2a90-1b4e-4c8f-9d2a-0e5b6c7d8e9f: Add a health check to the sample worker.',
      ),
    ).text,
    'Add a health check to the sample worker',
  );
  assert.equal(
    missionTitle(withObjective('Objective: Document the sample import flow')).text,
    'Document the sample import flow',
  );
  // An ordinary sentence that merely STARTS with a label word is left alone.
  assert.equal(
    missionTitle(withObjective('Task chains should show their depth limit.')).text,
    'Task chains should show their depth limit',
  );
});

test('with nothing meaningful left, the title is an honest placeholder — never a made-up summary', () => {
  for (const boiler of [
    'AUTHORIZED MISSION',
    '7f3c2a90-1b4e-4c8f-9d2a-0e5b6c7d8e9f',
    'AUTHORIZED MISSION 20000101-000000-sample0',
    'task 20000101-000000-sample0',
    '## Objective\n\n9f1fedb270ab',
  ]) {
    assert.deepEqual(
      missionTitle(withObjective(boiler)),
      { kind: 'placeholder', text: MISSION_TITLE_PLACEHOLDER },
      boiler,
    );
  }
  assert.equal(MISSION_TITLE_PLACEHOLDER, 'Objective text available — open to read');
});

test('a header-only, empty or missing objective keeps its source-qualified "No goal reported" wording', () => {
  for (const value of ['OBJECTIVE', '   ', null]) {
    const t = missionTitle(withObjective(value));
    assert.equal(t.kind, 'no-goal', String(value));
    assert.match(t.text, /^No goal reported: mc /);
  }
});

// ── short refs ─────────────────────────────────────────────────────────────────────────────────

test('short refs are source-qualified and unique in the view, so lookalike objectives stay apart', () => {
  const s = fresh();
  const refs = shortMissionRefs(s.missions.missions);
  // Every Mission in this fixture has the objective "SYNTHETIC task" or none: the ref tells them apart.
  assert.equal(new Set(refs.values()).size, s.missions.missions.length);
  for (const m of s.missions.missions) {
    const short = refs.get(m.missionRef)!;
    assert.ok(short.endsWith(` · ${m.sourceId}`), short);
    const native = short.slice(0, -` · ${m.sourceId}`.length);
    if (native.endsWith('…')) assert.ok(m.nativeId.startsWith(native.slice(0, -1)), short);
    else assert.equal(native, m.nativeId);
  }
  assert.equal(refs.get('herd:root@0457d2576071'), 'root@0457d2576071 · herd');
});

// ── the team line ──────────────────────────────────────────────────────────────────────────────

test('the team line counts processes observed running; "busy" comes only from busy terminal evidence', () => {
  const s = fresh();
  const m = bound(s);
  assert.deepEqual(teamSummary(m), {
    roles: ['executor', 'lead', 'reviewer', 'supervisor'],
    running: '4 of 4 members observed running',
    activity: '4 terminals busy',
  });
  for (const x of m.members.members) {
    x.animateActive = false;
    x.activity = 'idle';
  }
  assert.equal(teamSummary(m).activity, 'no terminal busy');
  m.members.members[0].activity = 'unknown';
  assert.equal(teamSummary(m).activity, null, 'an unknown activity is not "no terminal busy"');
  // A member that is not live is not counted as running, whatever it last reported.
  m.members.members[1].live = false;
  m.members.members[1].animateActive = false;
  assert.equal(teamSummary(m).running, '3 of 4 members observed running');
  for (const words of Object.values(teamSummary(m)).flat())
    if (typeof words === 'string')
      assert.doesNotMatch(words, /\b(live|active|idle|free|available|working|progress)\b/i);
});

// ── one short phrase per agent ─────────────────────────────────────────────────────────────────

const runtimeAgent = (pick: (a: DiRuntimeAgent) => boolean): DiRuntimeAgent =>
  structuredClone(fresh().runtimeAgents!.agents.find(pick)!);

test('no current Mission never hides observed activity: a verified agent reporting busy reads "Terminal busy"', () => {
  const a = runtimeAgent((x) => x.identityVerified && x.assignmentState === 'unassigned');
  assert.equal(a.activity, 'busy');
  assert.equal(agentPhrase(a).text, 'Terminal busy');
  assert.equal(agentPhrase({ ...a, activity: 'idle' }).text, 'Terminal not busy');
  assert.equal(agentPhrase({ ...a, activity: 'done' }).text, 'Terminal not busy');
  assert.equal(agentPhrase({ ...a, activity: 'blocked' }).text, 'Terminal blocked');
  assert.equal(agentPhrase({ ...a, activity: null }).text, 'Activity not reported');
  assert.equal(agentPhrase({ ...a, activity: 'unknown' }).text, 'Activity unknown');
  // Never a word a reader could take for task progress, completion or availability.
  for (const activity of ['busy', 'idle', 'done', 'blocked', null, 'unknown'])
    assert.doesNotMatch(
      agentPhrase({ ...a, activity }).text,
      /\b(idle|free|available|finished|done|working|progress|complete)\b/i,
    );
  // Not current: no claim of running or busy.
  const stale = agentPhrase({ ...a, live: false, notLiveReason: 'transport_disconnected' });
  assert.doesNotMatch(stale.text, /busy|running/i);
});

test('a runtime agent’s meaning names its assignment record as exactly that — never idle or free', () => {
  const a = runtimeAgent((x) => x.identityVerified && x.assignmentState === 'unassigned');
  assert.equal(
    runtimeAgentMeaning(a),
    'Not a team member: no current Mission is on record for it. That is an assignment record, not its activity.',
  );
  assert.equal(
    runtimeAgentMeaning({ ...a, assignmentState: 'unverified_reference' }),
    'Not a team member: a task is on record for it, but it is not confirmed that it belongs to it.',
  );
  assert.equal(
    runtimeAgentMeaning({ ...a, assignmentState: 'unrecognized:paused' }),
    'Not a team member: its assignment record is in a state this view does not recognise, so whether it has a task is unknown.',
  );
  for (const state of ['unassigned', 'unverified_reference', 'unrecognized:x', ''])
    assert.doesNotMatch(
      runtimeAgentMeaning({ ...a, assignmentState: state }),
      /\b(idle|free|available|done|finished)\b/i,
    );
});

test('an agent not linked to its role is never shown busy, whatever its terminal reports', () => {
  const a = runtimeAgent((x) => !x.identityVerified);
  assert.equal(a.activity, 'busy');
  assert.doesNotMatch(agentPhrase(a).text, /busy|working/i);
});

test('the details lead with one sentence of what the agent is doing — terminal activity, never progress', () => {
  const m = bound(fresh()).members.members[0];
  assert.equal(
    agentNow(m),
    'Its terminal reports busy right now. That is what its terminal is doing, not progress on the task or its completion.',
  );
  assert.equal(
    agentNow({ ...m, animateActive: false, activity: 'idle' }),
    'Its terminal reports “idle” right now: not busy.',
  );
  assert.equal(
    agentNow({ ...m, live: false, animateActive: false, classification: 'replaced' }),
    'Not observed running now: replaced by another session.',
  );
  const a = runtimeAgent((x) => !x.identityVerified);
  assert.equal(
    agentNow(a),
    'An agent with this name was found running, but it can’t be safely linked to its recorded role.',
  );
});

// ── runtime groups ─────────────────────────────────────────────────────────────────────────────

type Group = ReturnType<typeof runtimeGroups>[number];
const groupOf = (s: DiOfficeState, key: string): Group =>
  runtimeGroups(s.runtimeAgents!).find((g) => g.key === key)!;

test('runtime agents are grouped by what the source establishes, never named busy or idle', () => {
  const groups = runtimeGroups(fresh().runtimeAgents!);
  assert.deepEqual(
    groups.map((g) => [g.key, g.count]),
    [
      ['no-mission', 8],
      ['not-linked', 8],
    ],
  );
  for (const g of groups)
    assert.doesNotMatch(g.heading, /working|idle|free|available|busy|task on record/i);
});

test('an unrecognised assignment state is its own group: no task record is asserted, either way', () => {
  const s = fresh();
  const odd = s.runtimeAgents!.agents.find((a) => a.identityVerified)!;
  odd.assignmentState = 'unrecognized:paused';
  const unknown = groupOf(s, 'assignment-unknown');
  assert.equal(unknown.count, 1);
  assert.equal(unknown.rows[0].agent.agentRef, odd.agentRef);
  assert.equal(unknown.heading, 'Assignment not recognised');
  assert.match(unknown.sentence, /whether they have a task is unknown/);
  assert.doesNotMatch(unknown.sentence, /task is on record|no Mission is on record/i);
  // It left both of the other groups.
  for (const key of ['no-mission', 'task-unconfirmed'])
    assert.ok(!(groupOf(s, key)?.rows ?? []).some((r) => r.agent.agentRef === odd.agentRef), key);
});

test('every row carries its herd, so twin names stay apart at the row itself', () => {
  const g = groupOf(fresh(), 'no-mission');
  assert.deepEqual(
    g.rows.map((r) => `${r.agent.logical} · ${r.herd}`),
    [
      'executor1 · done00',
      'lead1 · done00',
      'reviewer1 · done00',
      'supervisor · done00',
      'executor1 · boot00',
      'lead1 · boot00',
      'reviewer1 · boot00',
      'supervisor · boot00',
    ],
  );
});

test('two sources with the same root string are two herds, and the row says which source', () => {
  const s = fresh();
  // A second source carries a root with the SAME root ref string and the same role names.
  const twins = s
    .runtimeAgents!.agents.filter((a) => a.rootAliases[0] === 'done00')
    .map((a) => ({ ...structuredClone(a), sourceId: 'herd-b', agentRef: `herd-b:${a.agentRef}` }));
  s.runtimeAgents!.agents.push(...twins);
  const g = groupOf(s, 'no-mission');
  const lead1 = g.rows.filter((r) => r.agent.logical === 'lead1');
  assert.equal(lead1.length, 3, 'done00 in herd, done00 in herd-b, boot00');
  const labels = lead1.map((r) => r.herd);
  assert.equal(new Set(labels).size, 3, `distinct: ${labels.join(' | ')}`);
  assert.ok(
    labels.includes('done00 · herd') && labels.includes('done00 · herd-b'),
    labels.join(' | '),
  );
  // Rendered: each twin is its own selectable row, qualified by its source.
  const html = renderToStaticMarkup(
    createElement(RuntimeAgentsSection, {
      runtime: s.runtimeAgents,
      selectedKey: null,
      onSelect: () => {},
      freshness: { current: true, reason: null, statement: 'Observed just now.' },
    }),
  );
  // Its four rows, and its one herd note ("done00 · herd-b: last task record COMPLETE.").
  assert.equal(count(html, 'done00 · herd-b'), 5);
  assert.equal(count(html, 'done00 · herd-b: last task record COMPLETE.'), 1);
});

test('shared activity is said once; only a row that differs carries its own phrase', () => {
  const s = fresh();
  let g = groupOf(s, 'no-mission');
  assert.equal(g.activity, 'All 8: terminal busy.');
  assert.ok(g.rows.every((r) => r.phrase === null));
  const quiet = s.runtimeAgents!.agents.find((a) => a.identityVerified)!;
  quiet.activity = 'idle';
  g = groupOf(s, 'no-mission');
  assert.equal(g.activity, '7 of 8: terminal busy.');
  assert.deepEqual(
    g.rows.filter((r) => r.phrase).map((r) => [r.agent.agentRef, r.phrase!.text]),
    [[quiet.agentRef, 'Terminal not busy']],
  );
});

test('a reason is said once, at the narrowest level that shares it: group, then herd, then row', () => {
  const degraded = plainIdentityReason('native_identity_degraded');
  const replaced = plainIdentityReason('session_replaced');
  const exceptions = (g: Group) => g.rows.filter((row) => row.exception !== null);

  // As projected: each herd's four agents share one reason, the two herds differ — once per herd.
  let g = groupOf(fresh(), 'not-linked');
  assert.deepEqual(g.notes, [`act01: ${replaced}.`, `act00: ${degraded}.`]);
  assert.equal(exceptions(g).length, 0);

  // Every row shares one reason: said once for the whole group.
  const s = fresh();
  const unlinked = s.runtimeAgents!.agents.filter((a) => !a.identityVerified);
  for (const a of unlinked) a.identityReason = 'native_identity_degraded:x';
  g = groupOf(s, 'not-linked');
  assert.deepEqual(g.notes, [`Why: ${degraded}.`]);
  assert.equal(exceptions(g).length, 0);

  // One row differs: the group keeps the majority, and only that row carries its own reason.
  unlinked[0].identityReason = 'session_replaced';
  g = groupOf(s, 'not-linked');
  assert.deepEqual(g.notes, [`Why (7 of 8): ${degraded}.`]);
  assert.deepEqual(
    exceptions(g).map((row) => [row.agent.agentRef, row.exception]),
    [[unlinked[0].agentRef, replaced]],
  );

  // No current Mission: the task record each herd shares is said once per herd.
  assert.deepEqual(groupOf(fresh(), 'no-mission').notes, [
    'done00: last task record COMPLETE.',
    'boot00: no task record.',
  ]);
});

test('rendered, a shared explanation appears once — not once per row — and every row stays', () => {
  const html = renderToStaticMarkup(
    createElement(RuntimeAgentsSection, {
      runtime: base.runtimeAgents,
      selectedKey: null,
      onSelect: () => {},
      freshness: { current: true, reason: null, statement: 'Observed just now.' },
    }),
  );
  assert.equal(count(html, plainIdentityReason('native_identity_degraded')), 1);
  assert.equal(count(html, plainIdentityReason('session_replaced')), 1);
  assert.equal(count(html, 'Terminal busy'), 0, 'not one badge per row');
  assert.equal(count(html, 'All 8: terminal busy.'), 1);
  assert.equal(count(html, 'data-testid="di-unverified-row"'), 8);
  assert.equal(count(html, 'data-testid="di-runtime-row"'), 8);
  // The exact proof stays one step away: every agent ref is in the evidence disclosure.
  for (const a of base.runtimeAgents!.agents) assert.ok(html.includes(a.agentRef), a.agentRef);
});

// ── code review, where it is not offered ──────────────────────────────────────────────────────

test('code review not offered: a plain consequence for every reason; the contract mechanics stay exact', () => {
  for (const reason of Object.keys(UNAVAILABLE_WORDS)) {
    const plain = reviewNotOfferedConsequence(reason);
    assert.match(plain, /code review is not offered/i, reason);
    assert.doesNotMatch(plain, /query contract|corroborate|bound for a review/i, reason);
    assert.notEqual(plain, unavailableWords(reason), reason);
  }
  assert.equal(
    reviewNotOfferedConsequence('contract_version_1_office'),
    'This office can’t confirm which code workspace belongs to which agent, so code review is not offered here.',
  );
  assert.equal(reviewNotOfferedConsequence(null), 'Code review is not offered for this agent.');
  // The exact words are unchanged (they stay in the details).
  assert.match(unavailableWords('contract_version_1_office'), /older query contract/);
});

// ── what this view cannot see; where the selection is; a Mission's results ────────────────────

test('the partial view is one plain sentence with its consequence; the exact limits one step away', () => {
  const s = fresh();
  const limits = viewLimits(s)!;
  assert.equal(limits.summary, 'This view may not show everything that needs attention.');
  assert.deepEqual(limits.items, [
    'Not reported by this office: clarification, delivery awaiting a separate decision.',
    'Only partly reported: authorization / reauthorization, failed verification, results ready for review.',
  ]);
  s.coverage.linkedChildRoles = 'not_covered';
  const both = viewLimits(s)!;
  assert.equal(
    both.summary,
    'This view may not show everything that needs attention, or team members working in linked child tasks.',
  );
  assert.equal(
    both.items.at(-1),
    "Teams list each Mission's own task only: members working in its linked child tasks are not shown.",
  );
  for (const c of s.reception!.categories) c.outcome = 'present';
  s.coverage.linkedChildRoles = 'covered';
  assert.equal(viewLimits(s), null);
});

test('the selection says where it is: agent n of N and Mission n of N, in the order the arrows move', () => {
  const s = fresh();
  const drawn = s.missions.missions.flatMap((m) =>
    m.members.members.filter((x) => x.characterId !== null).map((x) => x.characterId!),
  );
  assert.equal(navigationOrientation(s, null), `${navigationOrder(s).length} agents on the map`);
  assert.equal(
    navigationOrientation(s, drawn[0]),
    `Agent 1 of ${navigationOrder(s).length} · Mission 1 of 2`,
  );
  assert.equal(
    navigationOrientation(s, drawn[4]),
    `Agent 5 of ${navigationOrder(s).length} · Mission 2 of 2`,
  );
});

test('a Mission’s results are one plain sentence; the exact per-field facts stay in detail', () => {
  const m = bound(fresh());
  m.herdrTask = undefined;
  m.v2 = { ...m.v2, projected: false };
  assert.equal(
    missionResults(m),
    "This office doesn't report Mission results: whether it is complete, verified, reviewed or delivered is unknown here.",
  );
  // Observed in the fixture: reported complete false — said plainly, never as failure or success.
  assert.equal(missionResults(bound(fresh())), 'Not reported complete.');
  const done = bound(fresh());
  done.v2.missionState.completion.reportedComplete = true;
  // The fixture's reported success is false: complete WITHOUT success (as the status reads Failed).
  assert.equal(missionResults(done), 'Reported complete without success (not verified).');
  done.v2.missionState.completion.reportedSuccess = null;
  assert.equal(missionResults(done), 'Reported complete; success is not verified.');
  done.v2.missionState.completion.verifiedSuccess = true;
  assert.equal(missionResults(done), 'Verified success is recorded.');
  const herd = fresh().missions.missions.find((x) => x.herdrTask)!;
  assert.equal(
    missionResults(herd),
    "Mission results aren't supported for a plain Herdr task record.",
  );
});

// ── identity across row -> dock -> details (Addendum 10) ──────────────────────────────────────

/** The c1 pair: a second source carries the done00 herd again — same root string, same names. */
function withCrossSourceTwins(): { s: DiOfficeState; a: DiRuntimeAgent; b: DiRuntimeAgent } {
  const s = fresh();
  const herd = s.runtimeAgents!.agents.filter((x) => x.rootAliases[0] === 'done00');
  const twins = herd.map((x, i) => ({
    ...structuredClone(x),
    sourceId: 'herd-b',
    agentRef: `herd-b:${x.agentRef}`,
    characterId: 900 + i,
  }));
  s.runtimeAgents!.agents.push(...twins);
  return {
    s,
    a: herd.find((x) => x.logical === 'executor1')!,
    b: twins.find((x) => x.logical === 'executor1')!,
  };
}

/** The Selected dock's text, rendered with `id` selected on the canvas. */
function dockText(s: DiOfficeState, id: number): string {
  const html = renderToStaticMarkup(
    createElement(DiOfficePanel, {
      state: s,
      officeState: {
        ...(officeStub as unknown as Record<string, unknown>),
        characters: new Map([[id, {}]]),
        selectedAgentId: id,
      } as unknown as OfficeState,
      collapsed: false,
      onCollapsedChange: () => {},
    }),
  );
  const dock = html.slice(html.indexOf('data-testid="di-selected-agent"'));
  return dock
    .replace(/<!-- -->/g, '')
    .replace(/<[^>]+>/g, ' ')
    .replaceAll('&#x27;', "'")
    .replace(/\s+/g, ' ');
}

test('cross-source twins keep their source at every first layer: the name the list, dock and details use', () => {
  const { s, a, b } = withCrossSourceTwins();
  assert.equal(firstLayerName(s, { kind: 'runtime', agent: a }), 'executor1 · done00 · herd');
  assert.equal(firstLayerName(s, { kind: 'runtime', agent: b }), 'executor1 · done00 · herd-b');
  // One source in view: no qualifier is added (concise where nothing collides).
  assert.equal(firstLayerName(fresh(), { kind: 'runtime', agent: a }), 'executor1 · done00');
});

test('the Selected dock tells the c1 twins apart and keeps each agent’s own name', () => {
  const { s, a, b } = withCrossSourceTwins();
  const dockA = dockText(s, a.characterId!);
  const dockB = dockText(s, b.characterId!);
  assert.match(dockA, /executor1 · done00 · herd(?!-b)/);
  assert.match(dockB, /executor1 · done00 · herd-b/);
  assert.notEqual(dockA.slice(0, 200), dockB.slice(0, 200));
  // The agent's own name stays on the dock (the existing contract: diAppReconcile).
  for (const [dock, agent] of [
    [dockA, a],
    [dockB, b],
  ] as const)
    assert.ok(dock.includes(agent.agent), `${agent.agentRef}: ${dock.slice(0, 160)}`);
});

test('two members of one Mission with one name are told apart by their chain; unique names stay short', () => {
  const s = fresh();
  const m = bound(s);
  const first = m.members.members[0];
  const child = {
    ...structuredClone(first),
    memberRef: `${first.memberRef}~child`,
    chain: 'child',
    chainTaskId: 'synthetic-child-1',
  };
  m.members.members.push(child);
  assert.equal(
    firstLayerName(s, { kind: 'member', mission: m, member: first }),
    `${first.logical} · ${first.role} · direct task`,
  );
  assert.equal(
    firstLayerName(s, { kind: 'member', mission: m, member: child }),
    `${first.logical} · ${first.role} · linked child task synthetic-child-1`,
  );
  const other = m.members.members.find((x) => x.logical !== first.logical)!;
  assert.equal(
    firstLayerName(s, { kind: 'member', mission: m, member: other }),
    `${other.logical} · ${other.role}`,
  );
});

// ── time ───────────────────────────────────────────────────────────────────────────────────────

test('time reads as a plain age on the office clock; never guessed when it is not reported', () => {
  const current = { current: true, reason: null, statement: 'exact' };
  const lost = { current: false, reason: 'transport_disconnected', statement: 'exact' };
  assert.equal(plainObserved(current, 1000, 1030), 'Observed just now.');
  assert.equal(plainObserved(current, 1000, 1000 + 4 * 60 + 10), 'Observed 4 minutes ago.');
  assert.equal(plainObserved(current, 1000, 1000 + 61), 'Observed 1 minute ago.');
  assert.equal(
    plainObserved(lost, 1000, 1000 + 2 * 3600),
    'Last observed 2 hours ago. Not current: this view is not connected to the office.',
  );
  assert.equal(plainObserved(current, null, 1000), 'Observation time not reported.');
  assert.equal(
    plainObserved(lost, null, 1000),
    'Observation time not reported. Not current: this view is not connected to the office.',
  );
});

// ── attention and the Mission request route ────────────────────────────────────────────────────

test('zero attention entries is said honestly: never "none" while some kinds cannot be read', () => {
  const s = fresh();
  s.reception!.entries = [];
  s.reception!.entriesTotal = 0;
  const zero = attentionSummary(s.reception!, new Set());
  assert.equal(zero.count, 0);
  assert.equal(zero.text, 'Nothing listed as needing attention');
  // What the desk cannot read is said once, by viewLimits — never folded into a "none".
  assert.match(viewLimits(s)!.summary, /may not show everything that needs attention/);
  assert.equal(attentionSummary(fresh().reception!, new Set()).text, '1 needs attention');
});

const officeStub = {
  characters: new Map<number, unknown>(),
  selectedAgentId: null,
  cameraFollowId: null,
  areaMappings: {},
  getLayout: () => ({ areas: [] }),
} as unknown as OfficeState;

function panelHtml(entries: number, mission: DiMissionRequestAccess): string {
  const s = fresh();
  const one = s.reception!.entries[0];
  s.reception!.entries = Array.from({ length: entries }, (_, i): DiReceptionEntry => ({
    ...one,
    key: `${one.key}#${i}`,
    missionRef: `${one.missionRef}#${i}`,
  }));
  s.reception!.entriesTotal = entries;
  return renderToStaticMarkup(
    createElement(DiOfficePanel, {
      state: s,
      officeState: officeStub,
      collapsed: false,
      onCollapsedChange: () => {},
      mission,
    }),
  );
}

/** The order of the orientation strip's parts, by test id, as rendered. */
function stripOrder(html: string): string[] {
  const ids = ['di-attention', 'di-mr-entry', 'di-find', 'di-missions'];
  return ids
    .map((id) => [id, html.indexOf(`data-testid="${id}"`)] as const)
    .filter(([, at]) => at >= 0)
    .sort((a, b) => a[1] - b[1])
    .map(([id]) => id);
}

test('a configured Mission request route is in the same place with zero or twenty attention items', () => {
  const configured: DiMissionRequestAccess = {
    state: {
      ...INITIAL_STATE,
      availability: { kind: 'ok' },
      overview: {
        serverNow: 1,
        capabilities: {
          runAvailable: true,
          progression: null,
          repositoryUrl: null,
          deliveryAuthority: 'none',
          decisionProof: 'operator_attested_not_independently_verified',
          decisionPrincipal: 'operator_attested_relay',
          approvalValidSeconds: 900,
          scopeMeanings: {},
        },
        requests: [],
        requestsTruncated: false,
        conversations: [],
      },
    } as DiMissionRequestAccess['state'],
    open: () => {},
  };
  const none = panelHtml(0, configured);
  const many = panelHtml(20, configured);
  for (const html of [none, many]) {
    assert.ok(html.includes('data-testid="di-mr-open"'), 'the Ask action is offered');
    assert.deepEqual(stripOrder(html), ['di-attention', 'di-mr-entry', 'di-find', 'di-missions']);
  }
  assert.match(none, /Nothing listed as needing attention/);
  assert.match(many, /20 need attention/);
  // Not nested in, or conditional on, the attention entry.
  const attention = (html: string) =>
    html.slice(
      html.indexOf('data-testid="di-attention"'),
      html.indexOf('data-testid="di-mr-entry"'),
    );
  assert.ok(!attention(none).includes('di-mr-open'));
});

test('an unavailable Mission request capability is demoted, never removed: same place, why one step away', () => {
  const unavailable: DiMissionRequestAccess = {
    state: {
      ...INITIAL_STATE,
      availability: {
        kind: 'unavailable',
        problem: 'panel_not_configured',
        reason: 'This office was started without a Mission request panel.',
      },
    },
    open: () => {},
  };
  // Demoted below Find (a quiet one-line notice), in the same place whatever the attention count.
  for (const html of [panelHtml(0, unavailable), panelHtml(20, unavailable)]) {
    assert.deepEqual(stripOrder(html), ['di-attention', 'di-find', 'di-mr-entry', 'di-missions']);
    assert.ok(html.includes('data-testid="di-mr-entry-why"'));
  }
});
