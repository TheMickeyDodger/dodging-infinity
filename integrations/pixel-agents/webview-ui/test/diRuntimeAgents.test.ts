/**
 * item 07 in the webview: RUNTIME AGENTS (a verified runtime identity that is not a team member)
 * and plain Herdr tasks. Identity and membership stay distinct everywhere: canvas (characters,
 * zone, label, animation), list, inspector. Plain outcome text first; raw provenance behind a
 * labelled disclosure; no control of any kind. The data is projected server-side.
 */
import assert from 'node:assert/strict';

import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { test } from 'vitest';

import { DI_RUNTIME_ZONE_LABEL, DI_RUNTIME_ZONE_REF } from '../../core/src/constants.ts';
import type {
  DiHerdrTask,
  DiOfficeState,
  DiRuntimeAgent,
  DiRuntimeAgents,
} from '../../core/src/messages.ts';
import {
  diAnimationPlan,
  diCharacterLabel,
  diCharacterTitle,
  diRuntimeIdentityKey,
  formatRuntimeAgentsStatement,
  navigationOrder,
  reconcileDiCharacters,
  runtimeAgentForCharacter,
  withClientInvalidation,
} from '../src/components/diOfficeFormat.ts';
import {
  HerdrTaskNote,
  RuntimeAgentsSection,
  SelectedRuntimeAgent,
} from '../src/components/DiOfficePanel.tsx';

const agent = (over: Partial<DiRuntimeAgent> = {}): DiRuntimeAgent => ({
  agentRef: 'herd:0123456789ab#lead1',
  characterId: 41,
  sourceId: 'herd',
  rootRef: 'herd:root@0123456789ab',
  rootAliases: ['boot00', 'boot00-b'],
  logical: 'lead1',
  role: 'lead',
  agent: 'boot00-lead1',
  provider: 'claude',
  session: 'sess-boot00-lead1',
  paneId: 'w1:p3',
  workspaceId: 'w1',
  activity: 'busy',
  identityState: 'verified',
  identityReason: 'native_identity_present',
  identityVerified: true,
  assignmentState: 'unassigned',
  assignmentReason: 'no_task_record',
  taskRef: null,
  taskId: null,
  taskStatus: null,
  live: true,
  notLiveReason: null,
  plainText: 'Runtime identity verified · unassigned DI runtime (no task record)',
  ...over,
});

const runtime = (
  agents: DiRuntimeAgent[],
  over: Partial<DiRuntimeAgents> = {},
): DiRuntimeAgents => ({
  reported: true,
  state: 'observed',
  problem: null,
  plainStatement: `${agents.filter((a) => a.identityVerified).length} runtime agent(s) with a verified identity, not team members; 0 listed without a verified identity.`,
  caveat: 'two_pass_agreement_not_atomic_aba_not_detected',
  observedAt: 1800000000,
  firstPassAt: 1800000000,
  secondPassAt: 1800000000,
  counts: [
    { name: 'verified', value: agents.filter((a) => a.identityVerified).length },
    { name: 'roots_over_capacity', value: 2 },
  ],
  agents,
  agentsTotal: agents.length,
  agentsCap: agents.length + 24,
  agentsOverBound: 0,
  capture: { discovered: 18, captured: 16, unavailable: 0, overCapacity: 2, listingAnchor: false },
  locators: {
    registeredAliases: 98,
    physicalRoots: 73,
    sharedRootGroups: 4,
    rootsWithCurrentTask: 53,
    rootsWithoutCurrentTask: 10,
    aliasesWithoutHerdState: 12,
    excludedTotal: 19,
    excludedByReason: [{ name: 'registered_root_absent', value: 19 }],
  },
  ...over,
});

const state = (agents: DiRuntimeAgent[], member: Record<string, unknown> | null = null) =>
  ({
    live: true,
    notLiveReason: null,
    envelope: { fresh: true, reason: null },
    runtime: { fresh: true, reason: null },
    times: { validUntil: 1800000060 },
    missions: {
      state: 'observed',
      reason: null,
      missions:
        member === null
          ? []
          : [
              {
                missionRef: 'herd:task@aaaa',
                sourceId: 'herd',
                live: true,
                notLiveReason: null,
                members: { state: 'observed', reason: null, members: [member] },
              },
            ],
    },
    runtimeAgents: runtime(agents),
  }) as unknown as DiOfficeState;

const teamMember = {
  characterId: 7,
  coverage: 'direct',
  memberRef: 'herd:task@aaaa#lead1',
  taskId: { state: 'observed', value: 'task-1', reason: null },
  live: true,
  animateActive: true,
  activity: 'busy',
  logical: 'lead1',
  provider: 'claude',
};

test('a verified runtime agent is a character in its OWN zone, keyed by its own identity', () => {
  const plan = reconcileDiCharacters(new Map(), state([agent()], teamMember), []);
  assert.deepEqual(plan.add, [
    { id: 7, folderName: 'herd:task@aaaa' },
    { id: 41, folderName: DI_RUNTIME_ZONE_REF },
  ]);
  assert.equal(plan.next.get(41), diRuntimeIdentityKey(agent()));
  assert.notEqual(plan.next.get(41), plan.next.get(7));
  assert.equal(DI_RUNTIME_ZONE_LABEL, 'Runtime agents (not team members)');
});

test('an unverified runtime agent is never drawn (no characterId reaches the canvas)', () => {
  const unverified = agent({
    characterId: null,
    identityVerified: false,
    identityState: 'unverified',
    identityReason: 'session_replaced',
    live: false,
  });
  const plan = reconcileDiCharacters(new Map(), state([unverified]), []);
  assert.deepEqual(plan.add, []);
});

test('a runtime agent NEVER animates as working, even when its own activity is busy', () => {
  const s = state([agent({ activity: 'busy' })], teamMember);
  const plan = reconcileDiCharacters(new Map(), s, []);
  const animate = new Map(diAnimationPlan(s, [7, 41], plan.next));
  assert.equal(animate.get(7), true); // the team member, busy and live
  assert.equal(animate.get(41), false); // the runtime agent: never
});

test('label and title say what is verified, never an activity or a task', () => {
  const s = state([
    agent(),
    agent({
      characterId: 42,
      agentRef: 'herd:x#executor1',
      logical: 'executor1',
      assignmentState: 'unverified_reference',
      assignmentReason: 'root_not_captured_over_capacity',
      taskId: 't-1',
      taskStatus: 'ACTIVE',
    }),
  ]);
  assert.equal(diCharacterLabel(s, 41), 'runtime · unassigned (no current task)');
  assert.equal(diCharacterLabel(s, 42), 'runtime · task membership not verified');
  assert.doesNotMatch(diCharacterLabel(s, 41), /busy|working|Idle/);
  assert.equal(diCharacterTitle(s, 41), 'lead1 · claude');
  assert.equal(runtimeAgentForCharacter(s, 41)?.agentRef, 'herd:0123456789ab#lead1');
  assert.equal(runtimeAgentForCharacter(s, 7), null);
});

test('client invalidation takes runtime liveness away and keeps every identity verbatim', () => {
  const out = withClientInvalidation(state([agent()]), 'transport_reconnecting');
  const [a] = out.runtimeAgents!.agents;
  assert.equal(a.live, false);
  assert.equal(a.notLiveReason, 'transport_reconnecting');
  assert.equal(a.characterId, 41);
  assert.equal(a.identityState, 'verified');
  assert.equal(diCharacterLabel(out, 41), 'not live · runtime · unassigned (no current task)');
});

test('navigation reaches runtime agents after every Mission roster', () => {
  assert.deepEqual(navigationOrder(state([agent()], teamMember)), [7, 41]);
});

test('the statement says when a list is capped, and when the field is absent says so', () => {
  const capped = runtime([agent()], { agentsTotal: 30 });
  assert.match(formatRuntimeAgentsStatement(capped), /showing 1 of 30; 29 omitted/);
  assert.equal(
    formatRuntimeAgentsStatement(undefined),
    'Runtime agents: not provided by this office.',
  );
});

const markup = (element: Parameters<typeof renderToStaticMarkup>[0]) =>
  renderToStaticMarkup(element);

test('the runtime section: plain outcome first, raw evidence behind a labelled disclosure, no control', () => {
  const html = markup(
    createElement(RuntimeAgentsSection, {
      runtime: runtime([agent()]),
      selectedKey: null,
      onSelect: () => undefined,
    }),
  );
  const details = html.indexOf('<details');
  assert.ok(details > 0);
  // The plain outcome precedes the disclosure; the reasons and counts live inside it.
  assert.ok(html.indexOf('Runtime identity verified · unassigned DI runtime') < details);
  assert.ok(html.indexOf('Runtime agents (not team members)') < details);
  assert.ok(html.indexOf('registered_root_absent') > details);
  assert.ok(html.indexOf('over capacity, not observed') > details);
  assert.ok(html.indexOf('not atomic') > details);
  // C1 (item 04): its rows are real buttons that only SELECT (view only); no other control.
  assert.doesNotMatch(html, /<input|<select|href=/);
  const buttons = html.match(/<button[^>]*>/g) ?? [];
  assert.ok(buttons.length > 0);
  for (const b of buttons) {
    assert.match(b, /type="button"/);
    assert.match(b, /data-testid="di-(runtime|unverified)-row"/);
  }
});

test('the selected runtime agent: identity, then NOT a team member; refs only behind the disclosure', () => {
  const html = markup(
    createElement(SelectedRuntimeAgent, {
      agent: agent({
        assignmentState: 'unverified_reference',
        assignmentReason: 'root_not_captured_over_capacity',
        taskId: 't-1',
        taskStatus: 'ACTIVE',
        taskRef: 'herd:t-1@ffff',
        plainText:
          'Runtime identity verified; task membership not verified (root not captured / capacity)',
      }),
    }),
  );
  const details = html.indexOf('<details');
  assert.ok(html.indexOf('task membership not verified (root not captured / capacity)') < details);
  assert.ok(html.indexOf('Not a team member') < details);
  assert.ok(html.indexOf('Recorded task reference (not verified; not membership): t-1') < details);
  assert.ok(html.indexOf('herd:0123456789ab#lead1') > details);
  assert.ok(html.indexOf('root_not_captured_over_capacity') > details);
  assert.doesNotMatch(html, /<button|<input|<select/);
});

test('an unverified row says so in plain words; its reason code appears only in the disclosure', () => {
  const unverified = agent({
    agentRef: 'herd:aaaa#executor1',
    characterId: null,
    identityVerified: false,
    identityState: 'unverified',
    identityReason: 'native_identity_degraded:identity_stable_identity_mismatch',
    live: false,
    notLiveReason: 'runtime_identity_unverified:native_identity_degraded',
    plainText: 'Runtime identity not verified; not drawn',
  });
  const html = markup(
    createElement(RuntimeAgentsSection, {
      runtime: runtime([unverified]),
      selectedKey: null,
      onSelect: () => undefined,
    }),
  );
  const details = html.indexOf('<details');
  const plain = html.slice(0, details);
  assert.match(plain, /Runtime identity not verified; not drawn/);
  assert.match(plain, /NOT LIVE/);
  assert.doesNotMatch(plain, /native_identity|runtime_identity_|stable_identity/);
  assert.ok(html.indexOf('native_identity_degraded:identity_stable_identity_mismatch') > details);
});

test('an absent runtime block is said, never rendered as an empty list', () => {
  const html = markup(
    createElement(RuntimeAgentsSection, {
      runtime: undefined,
      selectedKey: null,
      onSelect: () => undefined,
    }),
  );
  assert.match(html, /Runtime agents: not provided by this office\./);
});

test('a plain Herdr task says so first; Mission Core facts unsupported; provenance disclosed', () => {
  const herdrTask: DiHerdrTask = {
    rootAliases: ['w5n'],
    membership: 'derived_from_current_task_record',
    schema: 'di.catalog.herdr_task',
    schemaVersion: 1,
    taskRecordVersion: 1,
    startedAt: 1800000000,
  };
  const html = markup(createElement(HerdrTaskNote, { herdrTask }));
  const details = html.indexOf('<details');
  assert.ok(
    html.indexOf('Herdr task — a plain Herdr task record, not a Mission Core Mission') < details,
  );
  assert.ok(html.indexOf('not supported for it') < details);
  assert.ok(html.indexOf('derived_from_current_task_record') > details);
  assert.doesNotMatch(html, /mn-/);
});
