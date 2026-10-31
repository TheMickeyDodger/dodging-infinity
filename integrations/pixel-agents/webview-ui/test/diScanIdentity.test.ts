/**
 * B5, UNRESOLVED IDENTITIES ARE VISIBLE IN THE PRESENTATION, as unresolved, and never as a live verified participant.
 *
 * The trap this closes (the Package A binding gap): for a bound Herdr task the producer MOVES the roles a team already holds out of
 * `runtime_agents` into the Mission's roster `members`, so `runtime_agents.agents` is EMPTY while `represented_by_teams` is 4. Wording fixed in a
 * helper over runtime agents (`IDENTITY_STATE_TEXT`) would never reach this screen. So these tests render the real scan face (the swarm of
 * Mission details and the selected card of the list) over exactly that shape and look at the MARKUP, not at a helper's return value.
 *
 * What they pin, for every state the roster can be in:
 *  - the role is SHOWN (its seat is not dropped), with a label that names its OWN state; unverified, ambiguous and unavailable-type states stay
 *    DISTINCT from each other (replaced / not in the live listing / ambiguous / not counted live / unrecognised), never one generic "unknown";
 *  - it is never given the live look: the avatar is marked unresolved, and an unresolved role shows NO observed activity;
 *  - only a member the office classifies `present` AND counts live is confirmed, and nothing unresolved is worded as confirmed;
 *  - when a role is listed twice, the confirmed listing takes the seat, never a replaced or vanished one.
 */
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';

import type { DiMember, DiOfficeState, DiRuntimeAgent, DiRuntimeAgents } from '../../core/src/messages.ts';
import { identityOfMember, scanRowsFromOffice } from '../src/components/diScanModel.ts';
import { DiScanPanel } from '../src/components/DiScanPanel.tsx';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const historical = JSON.parse(fs.readFileSync(path.join(root, 'test/fixtures/di-office-state-evidence-historical-v2.FIXTURE.json'), 'utf-8')) as {
  states: Record<'live', DiOfficeState>;
};
const HERD = 'herd:20261014-000000-aaaaaa@f69ae73eadfe';
const noop = () => undefined;
const observed = (value: string) => ({ state: 'observed' as const, value, reason: null });

/** A roster member as the office presents it (a labelled synthetic variation: nothing here is a real herd). */
function member(role: string, over: Partial<DiMember> = {}): DiMember {
  return {
    characterId: null,
    coverage: 'direct',
    memberRef: `src:abc#${role}`,
    missionRef: HERD,
    logical: `${role}1`,
    role,
    agent: `he-${role}`,
    provider: 'codex',
    taskId: observed('task-1'),
    paneId: observed(`w1:p-${role}`),
    workspaceId: observed('w1'),
    sessionRecorded: observed(`sess-${role}`),
    sessionLive: observed(`sess-${role}`),
    classification: 'present',
    reason: 'session_confirmed',
    activity: 'busy',
    live: true,
    notLiveReason: null,
    animateActive: true,
    model: observed('gpt-test'),
    cost: observed('0'),
    tokens: observed('0'),
    progress: observed('0'),
    chain: 'direct',
    chainTaskId: null,
    ...over,
  };
}

/** The BOUND-TASK shape: the roles are the Mission's roster; runtime_agents is EMPTY while represented_by_teams is 4. */
function stateWith(members: DiMember[]): DiOfficeState {
  const state = structuredClone(historical.states.live);
  for (const mission of state.missions.missions) {
    if (mission.missionRef === HERD) {
      mission.members = { state: 'observed', reason: null, members };
      // an OPEN Mission, so the list view lists it (the fixture's own Mission is an ended one)
      mission.lifecycleState = 'ACTIVE';
      mission.standing = 'active';
    }
  }
  state.runtimeAgents = {
    ...(state.runtimeAgents as DiRuntimeAgents),
    agents: [],
    agentsTotal: 0,
    counts: [{ name: 'represented_by_teams', value: 4 }],
  };
  return state;
}
const rowOf = (members: DiMember[]) => scanRowsFromOffice(stateWith(members)).find((row) => row.id === HERD)!;
const render = (members: DiMember[], view: 'mission' | 'list') => {
  const row = rowOf(members);
  return renderToStaticMarkup(createElement(DiScanPanel, { rows: [row], initialSelectedId: row.id, initialView: view, onMinimize: noop, missionDelivery: null }));
};
const text = (markup: string) =>
  markup
    .replace(/<[^>]+>/g, ' ')
    .replace(/&#x27;/g, "'")
    .replace(/\s+/g, ' ');
/** Just the swarm: the page has other, unrelated words (an objective can be "Unknown") that a check about identity must not read. */
const swarmOf = (markup: string) => /<ul[^>]*data-testid="di-scan-swarm"[\s\S]*?<\/ul>/.exec(markup)?.[0] ?? '';
const labels = (markup: string) => [...markup.matchAll(/data-testid="di-scan-identity" data-identity="([a-z_]+)"/g)].map((x) => x[1]!);

const STATES: Array<[string, Partial<DiMember>, string, string]> = [
  ['replaced', { classification: 'replaced', reason: 'session_changed', live: false, notLiveReason: 'member_replaced:session_changed' }, 'replaced', 'Replaced by another session'],
  ['disappeared', { classification: 'disappeared', reason: 'not_in_listing', live: false, notLiveReason: 'member_disappeared:x' }, 'disappeared', 'Not in the live listing'],
  ['ambiguous', { classification: 'ambiguous', reason: 'two_candidates', live: false, notLiveReason: 'member_ambiguous:x' }, 'ambiguous', 'Identity ambiguous'],
  ['present but not counted live', { classification: 'present', live: false, notLiveReason: 'chain_binding_unbound:x' }, 'not_counted_live', 'Not counted live'],
  ['an unrecognised classification', { classification: 'something_new', live: false, notLiveReason: 'classification_unrecognized' }, 'unrecognised', 'Identity unrecognised'],
];

describe('the bound-task shape: runtime_agents is empty and the roles are the roster, and unresolved ones are STILL shown', () => {
  it('the fixture state really has the Package A shape (no runtime agents, represented_by_teams 4)', () => {
    const state = stateWith([member('executor')]);
    expect(state.runtimeAgents?.agents).toEqual([]);
    expect(state.runtimeAgents?.counts).toEqual([{ name: 'represented_by_teams', value: 4 }]);
  });

  for (const view of ['mission', 'list'] as const) {
    describe(`in the ${view === 'mission' ? 'Mission details swarm' : 'list card swarm'} (rendered markup)`, () => {
      it.each(STATES)('%s: the role is shown, labelled with its OWN state, the avatar is marked unresolved, and no activity is shown as observed', (_name, over, state, label) => {
        const html = render([member('executor', over)], view);
        expect(html).toContain('data-testid="di-scan-swarm"');
        expect(labels(html)).toEqual([state]);
        expect(text(html)).toContain(label);
        expect(html).toContain('di-scan-avatar-unresolved');
        expect(html).toContain('data-identity="unresolved"');
        // the roster's own reason is shown beside the label, in BOTH views
        const built = member('executor', over);
        expect(text(html)).toContain(`(${state === 'not_counted_live' ? built.notLiveReason : built.reason})`);
        // a role at work is a CONFIRMED claim: an unresolved role carries no observed activity
        expect(text(html)).not.toContain('In progress.');
        // and it is never worded as confirmed, live or verified
        expect(text(html).replace(/not confirmed running/g, '')).not.toMatch(/confirmed running|\bis live\b|verified/i);
      });

      it('a CONFIRMED member (present and counted live) is not flagged, and shows its recorded activity as before', () => {
        const html = render([member('executor')], view);
        expect(labels(html)).toEqual([]);
        expect(html).not.toContain('di-scan-avatar-unresolved');
        if (view === 'mission') expect(text(html)).toContain('In progress.');
      });

      it('all the states side by side stay DISTINCT: each role has its own label, and none is collapsed into another or into a generic unknown', () => {
        const members = [
          member('supervisor', { classification: 'disappeared', live: false, notLiveReason: 'x' }),
          member('lead', { classification: 'ambiguous', live: false, notLiveReason: 'x' }),
          member('executor'),
          member('reviewer', { classification: 'replaced', live: false, notLiveReason: 'x' }),
        ];
        const html = render(members, view);
        expect(labels(html).sort()).toEqual(['ambiguous', 'disappeared', 'replaced']);
        expect(swarmOf(html)).not.toBe('');
        expect(text(swarmOf(html))).not.toMatch(/\bunknown\b/i);
      });
    });
  }

  it('the card swarm carries the sentence behind the label: what the last observation showed, and that nothing is confirmed', () => {
    const html = render([member('executor', STATES[0]![1])], 'mission');
    expect(text(html)).toContain('In the last observation a different live session carried this name, so it is not this member. Nothing is confirmed for this role.');
    const live = render([member('executor', STATES[3]![1])], 'mission');
    expect(text(live)).toContain('The last observation matched this role’s recorded session, but the office does not count it live (chain_binding_unbound:x). It is not confirmed running.');
  });

  it('a role listed TWICE gives its seat to the confirmed listing, never to a replaced or vanished one', () => {
    for (const order of [0, 1]) {
      const stale = member('executor', { classification: 'replaced', live: false, notLiveReason: 'x', characterId: 7, agent: 'he-old' });
      const current = member('executor', { agent: 'he-new' });
      const row = rowOf(order === 0 ? [stale, current] : [current, stale]);
      expect(row.swarm).toHaveLength(1);
      expect(row.swarm[0]!.identity).toEqual({ kind: 'confirmed' });
      expect(row.swarm[0]!.note).toBe('In progress.');
    }
  });
});

describe('the decision itself: only `present` AND counted live is confirmed', () => {
  it('over every classification and both live values', () => {
    for (const classification of ['present', 'replaced', 'disappeared', 'ambiguous', 'something_new']) {
      for (const live of [true, false]) {
        const identity = identityOfMember(member('executor', { classification, live, notLiveReason: live ? null : 'why' }));
        expect(identity.kind === 'confirmed', `${classification}/${live}`).toBe(classification === 'present' && live);
      }
    }
  });

  it('each unresolved state has its own label and its own words (unverified, unavailable and ambiguous kinds are never merged)', () => {
    const seen = new Set<string>();
    for (const [, over] of STATES.map((s) => [s[0], s[1]] as const)) {
      const identity = identityOfMember(member('executor', over));
      if (identity.kind !== 'unresolved') throw new Error('expected unresolved');
      expect(seen.has(identity.state)).toBe(false);
      seen.add(identity.state);
      expect(identity.label.length).toBeGreaterThan(0);
      expect(identity.words).toMatch(/Nothing is confirmed|not confirmed running/);
    }
    expect(seen.size).toBe(STATES.length);
  });
});


// ── RUNTIME IDENTITY ROWS THAT ARE NOT ROSTER MEMBERS (the Reviewer's round-4 finding). The swarm is built from the roster only, so a runtime record
// whose identity is unverified, ambiguous or unavailable DISAPPEARED from the scan face when the task had no observed roster members. The tests render
// the real panel over three shapes and fail if those records are dropped again. They also hold the listing to what the catalog actually says: an
// unresolved runtime record is NOT promoted into the roster, is not drawn, carries no role, task membership, activity or live look, and the three
// states stay distinct.
const ROLE = 'zzdistinctrole'; // a recorded role name no other text contains, so its absence from the page proves the record was not given a role

/** A runtime identity record as the office presents it, rooted at the Mission (a labelled synthetic variation). */
function runtimeRecord(over: Partial<DiRuntimeAgent> = {}): DiRuntimeAgent {
  return {
    agentRef: 'src:abc#runtime-a',
    characterId: null,
    sourceId: 'src',
    rootRef: HERD,
    rootAliases: [],
    logical: 'runtimea1',
    role: ROLE,
    agent: 'he-runtime-a',
    provider: 'codex',
    session: 'sess-runtime-a',
    paneId: 'w1:p-runtime-a',
    workspaceId: 'w1',
    activity: 'busy',
    identityState: 'unverified',
    identityReason: 'session_replaced',
    identityVerified: false,
    assignmentState: 'unassigned',
    assignmentReason: 'role_not_in_the_bound_team_roster',
    taskRef: null,
    taskId: null,
    taskStatus: null,
    live: false,
    notLiveReason: 'runtime_identity_unverified:session_replaced',
    plainText: 'Runtime identity not verified; not drawn',
    ...over,
  };
}
function stateWithRuntime(members: DiMember[], agents: DiRuntimeAgent[], membersState: 'observed' | 'projected_reason' = 'observed'): DiOfficeState {
  const state = stateWith(members);
  for (const mission of state.missions.missions) {
    if (mission.missionRef === HERD) mission.members = { state: membersState, reason: membersState === 'observed' ? null : 'binding_not_applicable:mission_not_active', members };
  }
  state.runtimeAgents = { ...(state.runtimeAgents as DiRuntimeAgents), agents, agentsTotal: agents.length };
  return state;
}
const renderState = (state: DiOfficeState, view: 'mission' | 'list') => {
  const row = scanRowsFromOffice(state).find((r) => r.id === HERD)!;
  return renderToStaticMarkup(createElement(DiScanPanel, { rows: [row], initialSelectedId: row.id, initialView: view, onMinimize: noop, missionDelivery: null }));
};
const listingOf = (html: string) => /<div class="di-scan-unresolved"[\s\S]*?<\/ul><\/div>/.exec(html)?.[0] ?? '';
const runtimeStates = (html: string) => [...html.matchAll(/data-testid="di-scan-unresolved-agent" data-runtime-identity="([a-z]+)"/g)].map((x) => x[1]!);
const THREE = [
  runtimeRecord({ agentRef: 'src:abc#a', logical: 'agent-unverified', agent: 'he-a', session: 'sess-a', paneId: 'w1:pa', identityState: 'unverified' }),
  runtimeRecord({ agentRef: 'src:abc#b', logical: 'agent-ambiguous', agent: 'he-b', session: 'sess-b', paneId: 'w1:pb', identityState: 'ambiguous', identityReason: 'two_candidates' }),
  runtimeRecord({ agentRef: 'src:abc#c', logical: 'agent-unavailable', agent: 'he-c', session: 'sess-c', paneId: 'w1:pc', identityState: 'unavailable', identityReason: 'listing_unreadable' }),
];

describe('SHAPE 1, runtime-only: a task with NO observed roster members and runtime identity rows is not empty, it LISTS them as unresolved', () => {
  for (const membersState of ['projected_reason', 'observed'] as const) {
    for (const view of ['mission', 'list'] as const) {
      it(`${view} view, roster ${membersState === 'observed' ? 'observed and empty' : 'not observed'}: the three records are listed, each in its OWN state, none drawn, none given a role, task, activity or live look`, () => {
        const state = stateWithRuntime([], THREE, membersState);
        const row = scanRowsFromOffice(state).find((r) => r.id === HERD)!;
        // the Reviewer's probe shape: this task's ONE row, an empty swarm, and the records still reachable
        expect(scanRowsFromOffice(state).filter((r) => r.id === HERD)).toHaveLength(1);
        expect(row.swarm).toHaveLength(0);
        expect(row.unresolvedAgents?.map((a) => a.state)).toEqual(['unverified', 'ambiguous', 'unavailable']);
        const html = renderState(state, view);
        const listing = listingOf(html);
        expect(listing, 'the listing must be rendered').not.toBe('');
        expect(runtimeStates(listing)).toEqual(['unverified', 'ambiguous', 'unavailable']); // three DISTINCT states
        expect(text(listing)).toContain('Identity not verified');
        for (const code of ['session_replaced', 'two_candidates', 'listing_unreadable']) expect(text(listing), code).toContain(`(${code})`); // each record's own reason code, in both views
        expect(text(listing)).toContain('Identity ambiguous');
        expect(text(listing)).toContain('Identity could not be checked');
        for (const name of ['agent-unverified', 'agent-ambiguous', 'agent-unavailable']) expect(text(listing)).toContain(name);
        // NOT drawn, NOT promoted into the roster
        expect(listing).not.toContain('di-scan-avatar');
        expect(html).not.toContain('data-testid="di-scan-swarm-member"');
        // no role, no task membership, no activity, no live look
        expect(html).not.toContain(ROLE);
        expect(text(html)).not.toMatch(/\bbusy\b|In progress\./i);
        expect(html).not.toContain('di-scan-avatar-unresolved');
        // and nothing in the label or in the listing's own words says confirmed, verified (other than "not verified"), running or a team member
        // (other than "not a team member"). The office's own plain REASON for each state is shown beside them and is not edited here.
        const own = (row.unresolvedAgents ?? []).map((a) => `${a.label}. ${a.words}`).join(' ').replace(/not verified|not shown as a team member|not shown as working/gi, '');
        expect(own).not.toMatch(/confirmed|\bverified\b|\brunning\b|team member|\blive\b/i);
        if (view === 'mission') {
          expect(text(listing)).toContain('its recorded session was replaced'); // the office's own reason, passed through
          expect(text(listing)).toContain('not established');
          expect(text(html)).toContain('No swarm is recorded for this Mission.'); // the roster is still honestly empty
        }
      });
    }
  }

  it('the bound-task shape: runtime_agents counts say represented_by_teams 4, yet a runtime-only record bound to the Mission is still listed', () => {
    const state = stateWithRuntime([], [THREE[0]!]);
    state.runtimeAgents = { ...(state.runtimeAgents as DiRuntimeAgents), counts: [{ name: 'represented_by_teams', value: 4 }] };
    expect(runtimeStates(listingOf(renderState(state, 'mission')))).toEqual(['unverified']);
  });
});

describe('SHAPE 2, MIXED roster and runtime: neither path hides the other', () => {
  const roster = [member('executor'), member('reviewer', { classification: 'replaced', live: false, notLiveReason: 'x', agent: 'he-reviewer', sessionLive: observed('sess-other') })];
  const runtimeOnly = [
    runtimeRecord({ agentRef: 'src:abc#lead', logical: 'lead-record', agent: 'he-lead-record', session: 'sess-lead', paneId: 'w1:p-lead', identityState: 'unverified' }),
    runtimeRecord({ agentRef: 'src:abc#sup', logical: 'supervisor-record', agent: 'he-sup-record', session: 'sess-sup', paneId: 'w1:p-sup', identityState: 'ambiguous' }),
  ];
  for (const view of ['mission', 'list'] as const) {
    it(`${view} view: the roster roles show in the swarm (one confirmed, one replaced) AND the runtime-only records show in the listing, each exactly once`, () => {
      const state = stateWithRuntime(roster, runtimeOnly);
      const row = scanRowsFromOffice(state).find((r) => r.id === HERD)!;
      expect(row.swarm.map((a) => a.identity?.kind === 'confirmed' ? 'confirmed' : (a.identity as { state: string }).state)).toEqual(['confirmed', 'replaced']);
      expect(row.unresolvedAgents?.map((a) => a.state)).toEqual(['unverified', 'ambiguous']);
      const html = renderState(state, view);
      // the roster path: its labelled members are all there
      expect(labels(html)).toEqual(['replaced']);
      if (view === 'mission') expect(html.match(/data-testid="di-scan-swarm-member"/g)).toHaveLength(2);
      // the runtime path: its records are all there, distinct
      expect(runtimeStates(listingOf(html))).toEqual(['unverified', 'ambiguous']);
      // neither leaks into the other: runtime names are not in the swarm, roster names are not in the listing
      const listing = text(listingOf(html));
      expect(listing).not.toMatch(/Mia|Sam|he-reviewer/);
      expect(swarmOf(html)).not.toContain('lead-record');
      expect(swarmOf(html)).not.toContain('supervisor-record');
      expect(html).not.toContain(ROLE);
    });
  }
});

describe('SHAPE 3, SAME-SESSION records: NOTHING is suppressed. Both records show, each with its own state AND reason, and a name or pane never relates them', () => {
  const NONE = { state: 'not_recorded' as const, value: null, reason: null };
  const roster = (over: Partial<DiMember> = {}) => member('executor', { agent: 'he-exec', logical: 'exec1', sessionLive: observed('sess-1'), paneId: observed('w1:p1'), ...over });

  it('the SAME LIVE SESSION as a roster member that already shows an unresolved state: BOTH are shown (the record is not suppressed), with a cross-reference', () => {
    const ambiguousMember = roster({ classification: 'ambiguous', live: false, notLiveReason: 'x' });
    const dup = runtimeRecord({ agentRef: 'src:abc#dup', logical: 'exec1', agent: 'he-exec', session: 'sess-1', paneId: 'w1:p1', identityState: 'unverified' });
    for (const view of ['mission', 'list'] as const) {
      const state = stateWithRuntime([ambiguousMember], [dup]);
      const row = scanRowsFromOffice(state).find((r) => r.id === HERD)!;
      expect(row.unresolvedAgents?.map((a) => a.state)).toEqual(['unverified']);
      expect(row.unresolvedAgents![0]!.words).toContain('The roster lists this same live session too, with its own state and reason; both records are shown.');
      const html = renderState(state, view);
      expect(labels(html)).toEqual(['ambiguous']); // the roster member's own state
      expect(runtimeStates(listingOf(html))).toEqual(['unverified']); // the runtime record's own, different state
    }
  });

  it('THE ROUND-6 REGRESSION: a runtime record whose unresolved STATE or REASON differs from a same-session roster member\'s is NOT dropped; both records show, each with its OWN state and reason', () => {
    // [roster classification, roster reason, runtime state, runtime reason]
    const cases: Array<[string, string, string, string]> = [
      ['replaced', 'session_changed', 'unavailable', 'listing_unreadable'], // the Reviewer's first probe
      ['replaced', 'session_changed', 'ambiguous', 'two_candidates'], // the Reviewer's second probe
      ['disappeared', 'not_in_listing', 'unverified', 'session_replaced'],
      ['ambiguous', 'two_candidates', 'unavailable', 'listing_unreadable'],
      ['ambiguous', 'two_candidates', 'ambiguous', 'two_candidates'], // even the SAME state and reason: no inference, both shown
    ];
    for (const [classification, rosterReason, runtimeState, runtimeReason] of cases) {
      for (const view of ['mission', 'list'] as const) {
        const m = roster({ classification, reason: rosterReason, live: false, notLiveReason: 'x' });
        const record = runtimeRecord({ agentRef: 'src:abc#r', logical: 'exec1', agent: 'he-exec', session: 'sess-1', identityState: runtimeState, identityReason: runtimeReason });
        const name = `${classification}/${rosterReason} vs ${runtimeState}/${runtimeReason}, ${view}`;
        const row = scanRowsFromOffice(stateWithRuntime([m], [record])).find((r) => r.id === HERD)!;
        // the model keeps BOTH
        expect(row.swarm[0]!.identity, name).toMatchObject({ kind: 'unresolved', state: classification, reason: rosterReason });
        expect(row.unresolvedAgents, name).toHaveLength(1);
        expect(row.unresolvedAgents![0], name).toMatchObject({ state: runtimeState, reasonCode: runtimeReason });
        // and the RENDERED page shows both states and both reasons
        const html = renderState(stateWithRuntime([m], [record]), view);
        expect(labels(html), name).toEqual([classification]);
        expect(html, name).toContain(`data-testid="di-scan-identity-reason">(${rosterReason})`.replace('">(', '">('));
        expect(runtimeStates(listingOf(html)), name).toEqual([runtimeState]);
        expect(text(listingOf(html)), name).toContain(`(${runtimeReason})`);
        expect(text(html), name).toContain(`(${rosterReason})`);
      }
    }
  });

  it('the same session as a CONFIRMED member is a DISAGREEMENT, not a duplicate: the unresolved record is NOT absorbed, both are shown, and its words say they disagree', () => {
    const dup = runtimeRecord({ agentRef: 'src:abc#dup', logical: 'exec1', agent: 'he-exec', session: 'sess-1', paneId: 'w1:p1', identityState: 'unverified' });
    for (const view of ['mission', 'list'] as const) {
      const state = stateWithRuntime([roster()], [dup]);
      const row = scanRowsFromOffice(state).find((r) => r.id === HERD)!;
      expect(row.swarm).toHaveLength(1);
      expect(row.swarm[0]!.identity).toEqual({ kind: 'confirmed' });
      expect(row.unresolvedAgents?.map((a) => a.state)).toEqual(['unverified']);
      expect(row.unresolvedAgents![0]!.words).toContain('The roster lists this same session as a confirmed member; the two records disagree, and both are shown.');
      const html = renderState(state, view);
      expect(runtimeStates(listingOf(html))).toEqual(['unverified']);
    }
  });

  it('A NAME IS NOT EVIDENCE (the over-merge probe): a runtime record with the SAME agent and logical name as a confirmed member, but NO session, is a different, unresolved record and is LISTED', () => {
    // an unavailable identity very often names no session; absorbing it on a name match would show it under the confirmed member's stronger claim
    const nameOnly = runtimeRecord({ agentRef: 'src:abc#name', logical: 'exec1', agent: 'he-exec', session: null, paneId: null, identityState: 'unavailable' });
    for (const view of ['mission', 'list'] as const) {
      const state = stateWithRuntime([roster()], [nameOnly]);
      expect(scanRowsFromOffice(state).find((r) => r.id === HERD)!.unresolvedAgents?.map((a) => a.state)).toEqual(['unavailable']);
      expect(runtimeStates(listingOf(renderState(state, view)))).toEqual(['unavailable']);
    }
    // and with the member itself carrying no session either: still not enough
    const bare = roster({ sessionLive: NONE, paneId: NONE });
    expect(scanRowsFromOffice(stateWithRuntime([bare], [nameOnly])).find((r) => r.id === HERD)!.unresolvedAgents?.map((a) => a.state)).toEqual(['unavailable']);
    // THE NAME-COLLISION REGRESSION: the same bare name against a roster member that is ITSELF unresolved (so the confirmed-member rule is not what
    // keeps the record visible). A record with NO session and NO pane must not be absorbed on the name alone: it fails here if it is.
    for (const classification of ['replaced', 'disappeared', 'ambiguous']) {
      const unresolvedMember = roster({ classification, live: false, notLiveReason: 'x' });
      const listed = scanRowsFromOffice(stateWithRuntime([unresolvedMember], [nameOnly])).find((r) => r.id === HERD)!.unresolvedAgents;
      expect(listed?.map((a) => a.state), `name-only record against a ${classification} member`).toEqual(['unavailable']);
    }
  });

  it('A PANE IS NOT EVIDENCE either: the same pane with no session on the record is listed (a pane outlives the agent in it)', () => {
    const paneOnly = runtimeRecord({ agentRef: 'src:abc#pane', logical: 'someone-else', agent: 'he-other', session: null, paneId: 'w1:p1', identityState: 'ambiguous' });
    const noSessionMember = roster({ sessionLive: NONE });
    expect(scanRowsFromOffice(stateWithRuntime([noSessionMember], [paneOnly])).find((r) => r.id === HERD)!.unresolvedAgents?.map((a) => a.state)).toEqual(['ambiguous']);
    expect(scanRowsFromOffice(stateWithRuntime([roster()], [paneOnly])).find((r) => r.id === HERD)!.unresolvedAgents?.map((a) => a.state)).toEqual(['ambiguous']);
  });

  it('the same NAME on a DIFFERENT session is a different agent (a replaced member and the new session carrying its name): it is listed', () => {
    const replaced = roster({ classification: 'replaced', live: false, notLiveReason: 'x', sessionLive: observed('sess-OLD') });
    const current = runtimeRecord({ agentRef: 'src:abc#new', logical: 'exec1', agent: 'he-exec', session: 'sess-NEW', paneId: 'w1:p9' });
    const state = stateWithRuntime([replaced], [current]);
    expect(scanRowsFromOffice(state).find((r) => r.id === HERD)!.unresolvedAgents?.map((a) => a.state)).toEqual(['unverified']);
    expect(runtimeStates(listingOf(renderState(state, 'mission')))).toEqual(['unverified']);
  });

  it('over every member classification and both live values, a same-session record is ALWAYS listed (never suppressed); a different or absent session adds no cross-reference', () => {
    const record = runtimeRecord({ agentRef: 'src:abc#r', logical: 'exec1', agent: 'he-exec', session: 'sess-1', paneId: 'w1:p1' });
    for (const classification of ['present', 'replaced', 'disappeared', 'ambiguous', 'something_new']) {
      for (const live of [true, false]) {
        const confirmed = classification === 'present' && live;
        const m = roster({ classification, live, notLiveReason: live ? null : 'x' });
        const listed = scanRowsFromOffice(stateWithRuntime([m], [record])).find((r) => r.id === HERD)!.unresolvedAgents!;
        expect(listed.length, `${classification}/${live} same session`).toBe(1);
        expect(listed[0]!.words.includes('the two records disagree'), `${classification}/${live} disagreement note`).toBe(confirmed);
        expect(listed[0]!.words.includes('with its own state and reason; both records are shown'), `${classification}/${live} cross-reference`).toBe(!confirmed);
        // anything other than the same session: listed too, and nothing relates the two
        for (const other of [roster({ classification, live, notLiveReason: live ? null : 'x', sessionLive: observed('sess-2') }), roster({ classification, live, notLiveReason: live ? null : 'x', sessionLive: NONE })]) {
          const l = scanRowsFromOffice(stateWithRuntime([other], [record])).find((r) => r.id === HERD)!.unresolvedAgents!;
          expect(l.length, `${classification}/${live} not the same session`).toBe(1);
          expect(l[0]!.words, `${classification}/${live} not the same session`).not.toMatch(/same live session|same session/);
        }
      }
    }
  });
});

describe('the wording describes how THIS WINDOW represents the record and asserts no negative the catalog never reported', () => {
  const states = ['unverified', 'ambiguous', 'unavailable', 'something_new'].map((identityState, i) => runtimeRecord({ agentRef: `src:abc#w${i}`, identityState }));
  it('all four states: "not established" for role and task membership, "not shown as a team member" and "not shown as working", and never "is not a team member"', () => {
    const listed = scanRowsFromOffice(stateWithRuntime([], states)).find((r) => r.id === HERD)!.unresolvedAgents!;
    expect(listed.map((a) => a.state)).toEqual(['unverified', 'ambiguous', 'unavailable', 'unrecognised']);
    for (const a of listed) {
      expect(a.words, a.state).toContain('its role and task membership are not established');
      expect(a.words, a.state).toContain('It is not shown as a team member and is not shown as working.');
      expect(a.words, a.state).not.toMatch(/\bnot a team member\b|\bis not a member\b|\bnon-member\b/i);
      expect(a.words, a.state).toContain('A runtime record found under this Mission’s workspace.');
    }
    // each state keeps its OWN label and reason text
    expect(listed.map((a) => a.label)).toEqual(['Identity not verified', 'Identity ambiguous', 'Identity could not be checked', 'Identity unrecognised']);
    expect(new Set(listed.map((a) => a.words)).size).toBe(4);
  });
  it('the listing heading says "not shown as team members", never "not team members"', () => {
    const html = renderState(stateWithRuntime([], [states[0]!]), 'mission');
    expect(text(html)).toContain('Unresolved runtime records found under this Mission’s workspace (not shown as team members, not drawn):');
    expect(text(html)).not.toMatch(/\(not team members/);
  });
});

describe('the listing claims no more association or verification than the catalog says', () => {
  it('a record rooted at ANOTHER Mission is not listed here, and an agent\'s own claim of which task it serves does not associate it', () => {
    const elsewhere = runtimeRecord({ agentRef: 'src:abc#else', rootRef: 'someone-else:task@x' });
    const claimsThisTask = runtimeRecord({ agentRef: 'src:abc#claims', rootRef: 'someone-else:task@x', taskRef: HERD, assignmentState: 'assigned' });
    expect(scanRowsFromOffice(stateWithRuntime([], [elsewhere, claimsThisTask])).find((r) => r.id === HERD)!.unresolvedAgents).toEqual([]);
  });
  it('a VERIFIED record is not listed as unresolved, and the contradiction "verified but not verified" is not read as the stronger reading', () => {
    const verified = runtimeRecord({ agentRef: 'src:abc#ok', identityState: 'verified', identityVerified: true });
    const contradiction = runtimeRecord({ agentRef: 'src:abc#odd', identityState: 'verified', identityVerified: false });
    const unknown = runtimeRecord({ agentRef: 'src:abc#new', identityState: 'something_new' });
    const listed = scanRowsFromOffice(stateWithRuntime([], [verified, contradiction, unknown])).find((r) => r.id === HERD)!.unresolvedAgents;
    expect(listed?.map((a) => [a.agentRef, a.state])).toEqual([['src:abc#odd', 'unrecognised'], ['src:abc#new', 'unrecognised']]);
  });
  it('each of the four states has its own label and its own words', () => {
    const states = ['unverified', 'ambiguous', 'unavailable', 'something_new'].map((identityState, i) => runtimeRecord({ agentRef: `src:abc#s${i}`, identityState }));
    const listed = scanRowsFromOffice(stateWithRuntime([], states)).find((r) => r.id === HERD)!.unresolvedAgents!;
    expect(new Set(listed.map((a) => a.label)).size).toBe(4);
    expect(new Set(listed.map((a) => a.words)).size).toBe(4);
    for (const a of listed) expect(a.words).toContain('not established');
  });
});
