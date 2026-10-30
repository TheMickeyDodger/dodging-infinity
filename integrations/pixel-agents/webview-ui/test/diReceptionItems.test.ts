/**
 * The reception inbox's unified ITEMS (P2-A): delivery proposals, request-panel entries, and the
 * Mission entries they may duplicate — as pure functions over hand-built SYNTHETIC inputs (labelled
 * below; no live record is read). Each claim is tested against its near neighbour.
 */
import { describe, expect, it } from 'vitest';

import type {
  DiMission,
  DiReception,
  DiReceptionEntry,
  DiReceptionSourceEntry,
} from '../../core/src/messages.ts';
import type { MrOverview, MrSummary } from '../src/components/diMissionRequestModel.ts';
import { parseOverview } from '../src/components/diMissionRequestModel.ts';
import { hideKey, invalidateReception, utcStamp } from '../src/components/diReceptionFormat.ts';
import type { PanelSource } from '../src/components/diReceptionItems.ts';
import {
  attentionLine,
  buildInbox,
  chipsOf,
  EMPTY_FILTER,
  emptyStateLines,
  filterItems,
  missionForRequest,
  panelSourceOf,
  pendingDecisionRevision,
  plainDelivery,
  plainStaleWords,
  reconcileDecisions,
  undeterminedDeliveries,
} from '../src/components/diReceptionItems.ts';
import { plainBlocker, plainReasons } from '../src/components/diReceptionPlain.ts';

const CV = `cv-${'a'.repeat(32)}`;
const CT = `ct-${'b'.repeat(32)}`;
const LR1 = `lr-${'1'.repeat(32)}`;
const LR2 = `lr-${'2'.repeat(32)}`;
const MN = 'mn-00000000000000000000000000000009';
const DIGEST = 'd'.repeat(64);
const T0 = 1_800_000_000;

// SYNTHETIC: a Mission entry shaped like the server's, with only what the model reads set meaningfully.
function missionEntry(over: Partial<DiReceptionEntry> = {}, revision: string | null = '2'): DiReceptionEntry {
  return {
    key: JSON.stringify(['authorization', 'synthetic_core', MN]),
    signature: 's1',
    category: 'authorization',
    missionRef: `synthetic_core:${MN}`,
    sourceId: 'synthetic_core',
    nativeId: MN,
    sourceKind: 'mission_core',
    producerDeclared: 'synthetic',
    objective: { state: 'observed', value: 'Make the probe stable', reason: null },
    lifecycleModel: 'mission_core.mission_state/1',
    lifecycleState: 'running',
    standing: 'active',
    bindingState: 'bound',
    bindingReason: 'x',
    live: true,
    notLiveReason: null,
    occurrences: 1,
    identityConflict: false,
    causes: [
      {
        cause: 'awaiting_decision',
        label: 'Mission Core: this Mission awaits a human decision',
        details: [
          { label: 'awaiting decision', value: 'true' },
          { label: 'revision', value: revision },
        ],
        staleReasons: [],
      },
    ],
    alsoUnknown: [],
    current: true,
    route: 'Outside this office: a decision is made through the existing Operator decision route.',
    routeIdentifiers: [],
    ...over,
  };
}

function receptionOf(
  entries: DiReceptionEntry[],
  sourceEntries?: DiReceptionSourceEntry[],
  extra: Partial<DiReception> = {},
): DiReception {
  const ids = ['blockers', 'clarification', 'authorization', 'failed_verification', 'ready_for_review', 'delivery_decision'] as const;
  return {
    contractVersion: 2,
    categories: ids.map((category) => ({
      category,
      label: category,
      statement: '',
      backing: '',
      outcome: 'present',
      reason: null,
      counts: { present: 0, knownEmpty: 0, unsupported: 0, unavailable: 0 },
      gaps: [],
      entriesTotal: 0,
    })),
    entries,
    entriesTotal: entries.length,
    entriesCap: 24,
    notices: [],
    minPollIntervalSeconds: 5,
    refreshStatement: '',
    dismissalStatement: '',
    ...(sourceEntries === undefined
      ? {}
      : {
          sourceEntries,
          sourceEntriesTotal: sourceEntries.length,
          sourceEntriesCap: 16,
          sourceCoverage: [
            {
              sourceId: 'grok_delivery_presentations',
              label: 'x',
              outcome: 'present',
              reason: null,
              statement: 'Covers proposals presented through the Grok Bot route only.',
              total: sourceEntries.length,
            },
          ],
        }),
    ...extra,
  };
}

function delivery(over: Partial<DiReceptionSourceEntry> = {}): DiReceptionSourceEntry {
  return {
    key: JSON.stringify(['delivery_decision', 'grok_delivery_presentations', DIGEST]),
    signature: 'sig',
    category: 'delivery_decision',
    sourceId: 'grok_delivery_presentations',
    proposalDigest: DIGEST,
    state: 'awaiting_no_recorded_authorization',
    needsYou: true,
    presentedAt: T0 - 600,
    expiresAt: T0 + 3000,
    mode: 'pull_request',
    repositoryUrl: 'https://github.com/octo/synthetic-repo',
    workflowId: null,
    matchingDeliveries: 0,
    linkState: 'unlinked_no_mission_binding',
    missionRef: null,
    authorizationCheck: 'not_comparable',
    current: true,
    staleReasons: [],
    route: 'Outside this office: ...',
    ...over,
  };
}

function summary(over: Partial<MrSummary> = {}): MrSummary {
  return {
    requestRef: LR1,
    missionId: MN,
    revision: 2,
    createdAt: 5,
    objective: 'Make the probe stable',
    digest: DIGEST,
    stage: 'of_record',
    label: 'Waiting for your decision',
    terminal: false,
    actions: ['decide'],
    notice: null,
    attestedAt: null,
    cancelledAt: null,
    ...over,
  };
}

function overviewOf(over: Partial<MrOverview> = {}): MrOverview {
  return {
    serverNow: 1000,
    capabilities: {} as MrOverview['capabilities'],
    requests: [],
    requestsTruncated: false,
    conversations: [],
    ...over,
  };
}

const read = (overview: MrOverview): PanelSource => ({ kind: 'read', overview });
const core = (nativeId: string, sourceId = 'synthetic_core', sourceKind = 'mission_core'): DiMission =>
  ({ missionRef: `${sourceId}:${nativeId}`, sourceId, nativeId, sourceKind }) as unknown as DiMission;
const none = new Set<string>();

const conversation = (over: Record<string, unknown> = {}) => ({
  conversationId: CV,
  turns: 2,
  lastActivity: 9,
  firstWords: 'make it stable',
  awaitingAnswer: true,
  questionsReporting: 'read' as const,
  questionsProblem: null,
  awaitingTurnId: CT,
  awaitingQuestions: ['Which branch?'],
  latestTurnState: 'COMPLETED',
  ...over,
});

// First-layer words that must not lead a card (policy / implementation language belongs in a disclosure).
const TECHNICAL = /attest|untrusted|decides nothing|authorizes nothing|read-only|operator|provenance|coverage|not independently|gate/i;

describe('delivery proposals: the first layer says only the supported fact', () => {
  it('awaiting: “presented … no matching authorization is recorded”, never “has not been decided”', () => {
    const p = plainDelivery(delivery());
    expect(p.what).toBe(
      'A delivery proposal was presented (a pull request for https://github.com/octo/synthetic-repo) and no matching authorization is recorded.',
    );
    expect(p.when).toBe(
      `presented ${utcStamp(T0 - 600)}; no recorded authorization; approval window until ${utcStamp(T0 + 3000)}`,
    );
    for (const text of [p.what, p.when, p.why ?? '', p.next, p.link]) {
      expect(text).not.toMatch(/not been decided|undecided|declin|rejected|pending approval/i);
      expect(text).not.toMatch(TECHNICAL);
    }
    expect(p.next).toMatch(/decide in the Grok Bot conversation where it was presented/);
    expect(p.next).toMatch(/Nothing here can approve or deliver it/);
  });

  it('never claims an approval is usable and keeps the limits behind the card', () => {
    for (const state of [
      'awaiting_no_recorded_authorization',
      'authorized',
      'expired_without_authorization',
      'unknown',
    ] as const) {
      const p = plainDelivery(delivery({ state, matchingDeliveries: state === 'unknown' ? null : 0 }));
      for (const text of [p.what, p.when, p.why ?? '', p.next, p.link])
        expect(text).not.toMatch(/can still be approved|is available|ready to approve|declin|rejected/i);
      expect(p.about).toMatch(/cannot see a decision or decline made elsewhere/);
      expect(p.about).toMatch(/command line/);
      expect(p.about).toMatch(/not a promise that an approval can still be made/);
    }
  });

  it('each state says its own fact', () => {
    expect(plainDelivery(delivery({ state: 'authorized', matchingDeliveries: 1 })).what).toMatch(
      /has a matching authorization on record/,
    );
    expect(plainDelivery(delivery({ state: 'expired_without_authorization' })).what).toMatch(
      /no matching authorization recorded and its approval window has closed/,
    );
    expect(plainDelivery(delivery({ state: 'unknown', matchingDeliveries: null })).what).toMatch(
      /whether a matching authorization is recorded was not determined/,
    );
  });

  it('every link state is said plainly, with the workflow when it matters', () => {
    const say = (linkState: string, workflowId: string | null = null) =>
      plainDelivery(delivery({ linkState, workflowId })).link;
    expect(say('linked')).toBe('Tied to the Mission shown on this card.');
    expect(say('unlinked_no_mission_binding')).toMatch(/names none/);
    // the first layer says it in words and names no workflow id (the exact workflow is in the card's record); no workflow named is said as such
    expect(say('unlinked_no_registered_mission', 'wf-1')).toMatch(/the workflow it names has no registered Mission/);
    expect(say('unlinked_no_registered_mission', 'wf-1')).not.toMatch(/wf-1/);
    expect(say('unlinked_no_registered_mission')).toMatch(/it names no workflow/);
    expect(say('unlinked_ambiguous', 'wf-1')).toMatch(/more than one registered Mission/);
    expect(say('unlinked_ambiguous', 'wf-1')).not.toMatch(/wf-1/);
    expect(say('unlinked_registry_unavailable')).toMatch(/list of Missions could not be read/);
    expect(say('unlinked_sources_incomplete')).toMatch(/some Mission sources could not be read/);
  });
});

describe('delivery proposals whose authorization status was not determined', () => {
  const unknown = delivery({ state: 'unknown', needsYou: false, matchingDeliveries: null });
  it('never claim waiting, authorized or expired, and say the store was not checked', () => {
    const p = plainDelivery(unknown);
    expect(p.what).toMatch(/whether a matching authorization is recorded was not determined\.$/);
    expect(p.when).toMatch(/the delivery store was not checked$/);
    expect(`${p.what} ${p.when}`).not.toMatch(/no delivery store is registered/i);
    expect(`${p.what} ${p.when}`).not.toMatch(/waiting|awaiting|authorized|expired|no recorded authorization/i);
  });

  it('are counted as an uncertainty, not as something that needs the person', () => {
    const reception = receptionOf([], [unknown, delivery({ key: 'k2', proposalDigest: 'f'.repeat(64), state: 'unknown', needsYou: false, matchingDeliveries: null })]);
    expect(undeterminedDeliveries(reception)).toBe(2);
    expect(undeterminedDeliveries(undefined)).toBe(0);
    const model = buildInbox(reception, panelSourceOf(null), [], none);
    expect(model.attention).toBe(0);
    expect(model.all).toEqual([]);
    expect(model.information).toHaveLength(2);
    expect(model.sources[0].summary).toBe('2 presented · 0 waiting for you · 2 not determined');
    // a successful-looking empty list is never offered while proposals are undetermined
    expect(emptyStateLines(model, reception, true)).toHaveLength(2);
  });
});

describe('delivery wording respects current / expired-since-read / stale', () => {
  const T2 = utcStamp(T0 + 3000);
  const cached = delivery(); // ONE cached record; only its currency changes below
  const passed = {
    ...cached,
    current: false,
    staleReasons: ['approval_window_ended_since_catalog_read'],
  };
  const stale = {
    ...cached,
    current: false,
    staleReasons: ['envelope_not_fresh:expired_locally:response_expired'],
  };
  const text = (e: DiReceptionSourceEntry) => {
    const p = plainDelivery(e);
    return [p.what, p.when, p.why ?? ''].join(' | ');
  };

  it('current: no matching authorization is recorded, and the window closes at a stated time', () => {
    const p = plainDelivery(cached);
    expect(p.what).toMatch(/and no matching authorization is recorded\.$/);
    expect(p.when).toBe(`presented ${utcStamp(T0 - 600)}; no recorded authorization; approval window until ${T2}`);
    expect(p.why).toBe(`No authorization is recorded for it yet; its approval window closes ${T2}.`);
  });

  it('after the local expiry, the SAME cached record says what was recorded when last read, that the window has passed, and that its status needs checking', () => {
    expect(passed.state).toBe(cached.state);
    expect(passed.proposalDigest).toBe(cached.proposalDigest);
    expect(passed.expiresAt).toBe(cached.expiresAt);
    const p = plainDelivery(passed);
    expect(p.what).toMatch(/When last read, no matching authorization was recorded\.$/);
    expect(p.when).toBe(
      `presented ${utcStamp(T0 - 600)}; when last read, no recorded authorization; its approval window has now passed (${T2}); its current status needs checking`,
    );
    expect(p.why).toBe('Its approval window has passed since this was read, so its current status needs checking.');
    // it does NOT turn the clock into a new outcome
    expect(text(passed)).not.toMatch(/expired without|ended without|was not authori|never authori|no authorization was granted|declin|reject/i);
    expect(text(passed)).not.toMatch(/closes|has not closed|until \d/);
  });

  it('another stale cause uses the same last-recorded framing, with the cause in plain words', () => {
    const p = plainDelivery(stale);
    expect(p.what).toMatch(/When last read, no matching authorization was recorded\.$/);
    expect(p.when).toBe(
      `presented ${utcStamp(T0 - 600)}; when last read, no recorded authorization; approval window until ${T2} (as read)`,
    );
    expect(p.why).toBe('This is out of date (the catalog answer is out of date), so its current status needs checking.');
    expect(plainStaleWords(['client:transport_disconnected'])).toBe('this window can no longer vouch for the office');
    expect(plainStaleWords(['something_new'])).toBe('its data is out of date');
    expect(text(stale)).not.toMatch(/envelope_not_fresh|expired_locally|response_expired/);
  });

  it('no wording anywhere says the window “has not closed”', () => {
    for (const e of [cached, passed, stale])
      for (const state of ['awaiting_no_recorded_authorization', 'authorized', 'expired_without_authorization', 'unknown'] as const)
        expect(text({ ...e, state, matchingDeliveries: state === 'unknown' ? null : 0 })).not.toMatch(/has not closed/i);
  });

  it('a record that is not the awaiting state is marked as last read when it is out of date', () => {
    expect(plainDelivery({ ...stale, state: 'authorized', matchingDeliveries: 1 }).when).toMatch(/as last read; out of date/);
    expect(plainDelivery({ ...stale, state: 'expired_without_authorization' }).what).toMatch(/, as last read\.$/);
  });
});

describe('request-panel cards: the first layer leads with the fact and the next step', () => {
  const cards = () => {
    const model = buildInbox(
      receptionOf([]),
      read(
        overviewOf({
          conversations: [conversation()],
          requests: [
            summary({ requestRef: LR1, actions: ['decide'] }),
            summary({ requestRef: LR2, missionId: 'mn-2', actions: ['check_approval', 'verify'] }),
          ],
        }),
      ),
      [],
      none,
    );
    return model.all.filter((i) => i.kind === 'panel').map((i) => i.entry as import('../src/components/diReceptionItems.ts').PanelEntry);
  };

  it('a conversation says what is true: the assistant asked and is waiting for the reply', () => {
    const c = cards().find((e) => e.conversationId === CV)!;
    expect(c.what).toBe(
      'The assistant asked a question in this conversation and is waiting for your reply.',
    );
    expect(c.why).toBe('The newest reply in this conversation asked for your answer, and nothing has followed it.');
    expect(`${c.what} ${c.why} ${c.next}`).not.toMatch(/cannot become a proposal|until its questions are answered|must be answered/i);
    expect(`${c.what} ${c.why} ${c.next}`).not.toMatch(TECHNICAL);
    // the authority explanation is said once, behind the card
    expect(c.about).toMatch(/untrusted content that decides and authorizes nothing/);
    expect(c.about).toMatch(/not a gate the request panel enforces/);
  });

  it('decision, approval-check and verify cards keep technical wording out of the first layer', () => {
    const all = cards().filter((e) => e.requestRef !== null);
    expect(all.map((e) => e.category).sort()).toEqual(['authorization', 'authorization', 'ready_for_review']);
    for (const e of all) {
      expect(`${e.what} ${e.why} ${e.next}`).not.toMatch(TECHNICAL);
      expect(e.about.length).toBeGreaterThan(40);
    }
    expect(all.find((e) => e.decision)!.about).toMatch(/operator attestation \(not independently verified\)/);
    expect(all.find((e) => e.next.includes('verify'))!.about).toMatch(/report, not a verification/);
  });

  it('a conversation not awaiting an answer is not listed, and neither is a request with nothing to do', () => {
    const model = buildInbox(
      receptionOf([]),
      read(
        overviewOf({
          conversations: [conversation({ awaitingAnswer: false, awaitingQuestions: [], awaitingTurnId: null })],
          requests: [summary({ actions: ['withdraw', 'observe'] })],
        }),
      ),
      [],
      none,
    );
    expect(model.all).toEqual([]);
  });
});

describe('one decision observed twice counts once — only when the evidence proves it', () => {
  const entry = missionEntry();
  const withPanel = (requests: MrSummary[], entries = [entry], missions = [core(MN)]) =>
    buildInbox(receptionOf(entries), read(overviewOf({ requests })), missions, none);

  it('same Mission, same revision, one mission_core source → ONE item with both facts, counted once', () => {
    const model = withPanel([summary()]);
    expect(model.all).toHaveLength(1);
    expect(model.attention).toBe(1);
    const item = model.all[0];
    expect(item.kind).toBe('mission');
    if (item.kind !== 'mission') return;
    expect(item.panel).toMatchObject({ requestRef: LR1, stage: 'of_record', digest: DIGEST, revision: 2 });
    expect(pendingDecisionRevision(item.entry)).toBe(2);
    // the panel's own route is what the merged card offers (the component renders it from `panel`)
    expect(item.panel!.next).toMatch(/Open the request panel/);
  });

  it('a MERGED card is hidden by its OWN hideKey (Mission content + panel signature) and returns when EITHER side changes', () => {
    const build = (e: DiReceptionEntry, requests: MrSummary[], hidden: ReadonlySet<string>) =>
      buildInbox(receptionOf([e]), read(overviewOf({ requests })), [core(MN)], hidden);
    const item = withPanel([summary()]).all[0];
    if (item.kind !== 'mission' || item.panel === null) throw new Error('expected a merged item');
    // the merged card's key is the Mission key PLUS the panel's signature, so the plain Mission key alone hides nothing of it
    const plain = hideKey(item.entry);
    expect(item.hideKey).toBe(`${plain}\u0000${item.panel.signature}`);
    expect(build(entry, [summary()], new Set([plain])).visible).toHaveLength(1);
    // the item's own key hides it from the LIST and counts it hidden, but leaves the attention count where it was: hiding is a view preference that resolves and dismisses nothing,
    // so it cannot change how many things need the person (the count and its words are pinned across every kind of item in diReceptionHideCount.test.ts)
    const hidden = build(entry, [summary()], new Set([item.hideKey]));
    expect(hidden.visible).toEqual([]);
    expect(hidden.hiddenCount).toBe(1);
    expect(hidden.attention).toBe(build(entry, [summary()], new Set()).attention);
    expect(hidden.attention).toBe(1);
    // a new PANEL signature (the request's digest) resurfaces it, and so does new MISSION content (its signature)
    expect(build(entry, [summary({ digest: 'c'.repeat(64) })], new Set([item.hideKey])).visible).toHaveLength(1);
    expect(build({ ...entry, signature: 's2' }, [summary()], new Set([item.hideKey])).visible).toHaveLength(1);
  });

  it('a different revision stays two items, and says why they were not joined', () => {
    const model = withPanel([summary({ revision: 3 })]);
    expect(model.all.map((i) => i.kind).sort()).toEqual(['mission', 'panel']);
    expect(model.attention).toBe(2);
    const panel = model.all.find((i) => i.kind === 'panel')!;
    if (panel.kind !== 'panel') return;
    expect(panel.entry.unresolved).toMatch(/revision 2.*revision 3.*not shown to be the same decision/);
  });

  it('a Mission found in several sources is ambiguous: two items, explicitly unresolved', () => {
    const model = withPanel([summary()], [entry], [core(MN), core(MN, 'other_core')]);
    expect(model.all).toHaveLength(2);
    const panel = model.all.find((i) => i.kind === 'panel')!;
    if (panel.kind !== 'panel') return;
    expect(panel.entry.unresolved).toMatch(/not unique among the sources read/);
  });

  it('two pending-decision entries with that id (two sources) are never merged', () => {
    const other = missionEntry({ sourceId: 'other_core', missionRef: `other_core:${MN}`, key: 'k2' });
    const model = withPanel([summary()], [entry, other], [core(MN), core(MN, 'other_core')]);
    expect(model.all.filter((i) => i.kind === 'mission' && i.panel !== null)).toEqual([]);
    expect(model.all).toHaveLength(3);
  });

  it('a lone pending entry from source A and a lone Mission from source B that share a native id and revision are NOT merged', () => {
    // The ids and the revision agree, but they are two different records: nothing proves one decision.
    const model = withPanel([summary()], [entry], [core(MN, 'other_core')]);
    expect(model.all.map((i) => i.kind).sort()).toEqual(['mission', 'panel']);
    const mission = model.all.find((i) => i.kind === 'mission')!;
    if (mission.kind === 'mission') expect(mission.panel).toBeNull();
    const panel = model.all.find((i) => i.kind === 'panel')!;
    if (panel.kind !== 'panel') return;
    expect(panel.entry.unresolved).toMatch(
      /synthetic_core:mn-0+9.*other_core:mn-0+9.*share an id but not a record/,
    );
    expect(model.attention).toBe(2); // counted as two, not silently as one
  });

  it('the same source but a different full ref is not the same record either', () => {
    const odd = missionEntry({ missionRef: `synthetic_core:${MN}-copy` });
    const model = withPanel([summary()], [odd], [core(MN)]);
    expect(model.all).toHaveLength(2);
    const panel = model.all.find((i) => i.kind === 'panel')!;
    if (panel.kind !== 'panel') return;
    expect(panel.entry.unresolved).toMatch(/share an id but not a record/);
  });

  it('a request already tied to another Mission ref is not merged into this entry', () => {
    // Defence in depth: the linkage the request carries must also agree with the pending entry.
    const e = missionEntry();
    const panelEntries = [
      {
        key: 'k', signature: 's', category: 'authorization' as const, identity: LR1, conversationId: null,
        requestRef: LR1, title: 't', what: 'w', why: 'y', next: 'n', about: 'a', questions: [],
        missionRef: 'someone_else:mn-other', linkNote: '', decision: true, missionId: MN, revision: 2,
        stage: 'of_record', digest: DIGEST, unresolved: null,
      },
    ];
    const linkage = reconcileDecisions([e], panelEntries, [core(MN)]);
    expect(linkage.merged.size).toBe(0);
    expect(linkage.consumed.size).toBe(0);
    expect(linkage.unresolved.get(panelEntries[0])).toMatch(/tied to someone_else:mn-other/);
  });

  it('a title that matches without source proof is not a match', () => {
    // same words, different Mission id: nothing names the request, so nothing joins
    const model = withPanel([summary({ missionId: 'mn-different' })]);
    expect(model.all).toHaveLength(2);
    const panel = model.all.find((i) => i.kind === 'panel')!;
    if (panel.kind !== 'panel') return;
    expect(panel.entry.unresolved).toBeNull();
  });

  it('a native id that matches a non-mission_core source is not proof', () => {
    const wf = missionEntry({ sourceKind: 'workflow_store', sourceId: 'wf_store', missionRef: `wf_store:${MN}` });
    const model = withPanel([summary()], [wf], [core(MN, 'wf_store', 'workflow_store')]);
    expect(model.all.filter((i) => i.kind === 'mission' && i.panel !== null)).toEqual([]);
    expect(model.all).toHaveLength(2);
  });

  it('a pending decision whose revision is not stated is not tied to the request', () => {
    const model = withPanel([summary()], [missionEntry({}, null)]);
    expect(model.all).toHaveLength(2);
    const panel = model.all.find((i) => i.kind === 'panel')!;
    if (panel.kind !== 'panel') return;
    expect(panel.entry.unresolved).toMatch(/without a revision/);
  });

  it('two requests waiting on one Mission are not joined to its decision', () => {
    const model = withPanel([summary(), summary({ requestRef: LR2, revision: 2 })]);
    expect(model.all.filter((i) => i.kind === 'mission' && i.panel !== null)).toEqual([]);
    expect(model.all).toHaveLength(3);
  });

  it('a delivery proposal for the same Mission stays its own item', () => {
    const model = buildInbox(
      receptionOf([entry], [delivery({ workflowId: MN, linkState: 'linked', missionRef: `synthetic_core:${MN}` })]),
      read(overviewOf({ requests: [summary()] })),
      [core(MN)],
      none,
    );
    expect(model.all.map((i) => i.kind).sort()).toEqual(['delivery', 'mission']);
    const mission = model.all.find((i) => i.kind === 'mission')!;
    if (mission.kind === 'mission') expect(mission.panel).not.toBeNull();
    expect(model.attention).toBe(2);
  });

  it('a verify/result item for the same Mission is not the decision and stays its own item', () => {
    const model = withPanel([summary({ actions: ['verify'] })]);
    expect(model.all.map((i) => i.kind).sort()).toEqual(['mission', 'panel']);
    expect(model.all.find((i) => i.kind === 'panel')!.category).toBe('ready_for_review');
    expect(model.attention).toBe(2);
  });

  it('an approval check for the same Mission is not the decision either', () => {
    const model = withPanel([summary({ actions: ['check_approval'] })]);
    expect(model.all).toHaveLength(2);
  });
});

describe('the model: counts, hiding, ordering', () => {
  it('counts Mission entries not listed, delivery entries waiting on you and panel entries; not information', () => {
    const reception = receptionOf(
      [missionEntry()],
      [delivery(), delivery({ proposalDigest: 'e'.repeat(64), key: 'k-auth', state: 'authorized', needsYou: false })],
      { entriesTotal: 4 },
    );
    const model = buildInbox(reception, read(overviewOf({ conversations: [conversation()] })), [], none);
    expect(model.attention).toBe(1 + 1 + 1 + 3); // mission + delivery + conversation + not listed
    expect(model.information).toHaveLength(1);
    expect(attentionLine(model.attention)).toBe('6 need attention');
    expect(attentionLine(1)).toBe('1 needs attention');
    expect(attentionLine(0)).toBe('Nothing listed as needing attention');
  });

  it('hiding is local, keyed on content, and returns when the content changes', () => {
    const base = buildInbox(receptionOf([], [delivery()]), panelSourceOf(null), [], none);
    const key = base.all[0].hideKey;
    const hidden = buildInbox(receptionOf([], [delivery()]), panelSourceOf(null), [], new Set([key]));
    expect(hidden.visible).toEqual([]);
    expect(hidden.hiddenCount).toBe(1);
    const changed = buildInbox(receptionOf([], [delivery({ signature: 'changed' })]), panelSourceOf(null), [], new Set([key]));
    expect(changed.visible).toHaveLength(1);
  });

  it('lists categories in the desk order, Mission entries before panel before delivery', () => {
    const model = buildInbox(
      receptionOf([missionEntry({ category: 'ready_for_review', key: 'r' })], [delivery()]),
      read(overviewOf({ conversations: [conversation()] })),
      [],
      none,
    );
    expect(model.all.map((i) => `${i.category}:${i.kind}`)).toEqual([
      'clarification:panel',
      'ready_for_review:mission',
      'delivery_decision:delivery',
    ]);
  });
});

describe('one coverage line per source — never a universal “none”', () => {
  it('the request panel, in every state it can be in', () => {
    const line = (p: PanelSource) => buildInbox(receptionOf([]), p, [], none).sources.find((s) => s.sourceId === 'request_panel')!;
    expect(line({ kind: 'not_provided' })).toMatchObject({ outcome: 'unsupported', summary: 'not set up in this office' });
    expect(line({ kind: 'not_available', reason: 'No panel.' })).toMatchObject({ outcome: 'unsupported' });
    expect(line({ kind: 'not_vouched', reason: 'x' })).toMatchObject({ outcome: 'unavailable' });
    expect(line({ kind: 'not_read' })).toMatchObject({ outcome: 'unavailable', summary: 'not read yet' });
    expect(line(read(overviewOf()))).toMatchObject({ outcome: 'known_empty' });
    expect(line(read(overviewOf({ conversations: [conversation()] })))).toMatchObject({ outcome: 'present' });
    const partial = line(read(overviewOf({ conversations: [conversation({ awaitingAnswer: false, awaitingQuestions: [], awaitingTurnId: null, questionsReporting: 'not_reported' })] })));
    expect(partial.outcome).toBe('partial');
    expect(partial.summary).toMatch(/1 older conversation not reported/);
    expect(partial.statement).toMatch(/not reported/);
    expect(partial.statement).toMatch(/10 most recent conversations/);
  });

  it('delivery proposals: the server’s own coverage, or “not reported by this office” when it sends none', () => {
    const withCoverage = buildInbox(receptionOf([], [delivery()]), panelSourceOf(null), [], none);
    expect(withCoverage.sources[0]).toMatchObject({ sourceId: 'grok_delivery_presentations', outcome: 'present' });
    expect(withCoverage.sources[0].summary).toBe('1 presented · 1 waiting for you');
    const legacy = buildInbox(receptionOf([]), panelSourceOf(null), [], none);
    expect(legacy.sources[0]).toMatchObject({ outcome: 'unsupported', summary: 'not reported by this office' });
    expect(legacy.sources[0].statement).toMatch(/not “none”/);
  });
});

describe('the ordinary empty state is scoped in BOTH branches', () => {
  it('says what is listed, never that nothing needs the person, and adds the unchecked line only when it applies', () => {
    const checked = buildInbox(receptionOf([], [], { notices: [] }), read(overviewOf()), [], none);
    const checkedLines = emptyStateLines(checked, receptionOf([], []), false);
    expect(checkedLines).toEqual(['Nothing is listed as needing you right now.']);
    const unchecked = buildInbox(receptionOf([]), panelSourceOf(null), [], none);
    const lines = emptyStateLines(unchecked, receptionOf([]), false);
    expect(lines[0]).toBe('Nothing is listed as needing you right now.');
    expect(lines).toHaveLength(2);
    expect(lines[1]).toMatch(/not a guarantee/);
    // a notice or an unknown line alone also adds it
    expect(emptyStateLines(checked, receptionOf([], []), true)).toHaveLength(2);
    expect(
      emptyStateLines(checked, receptionOf([], [], { notices: [{ reason: 'r', statement: 's' }] }), false),
    ).toHaveLength(2);
    // the unscoped phrase, and the old one, fail if they return — in either branch
    for (const text of [...checkedLines, ...lines])
      expect(text).not.toMatch(/^Nothing needs you|all caught up|No items were reported|nothing is waiting/i);
  });
});

describe('search and filter across the sources', () => {
  const model = () =>
    buildInbox(
      receptionOf([missionEntry()], [delivery()]),
      read(overviewOf({ conversations: [conversation()], requests: [summary({ requestRef: LR2, missionId: 'mn-x', actions: ['verify'], objective: 'Ship the probe' })] })),
      [],
      none,
    );
  const label = (c: string) => c;

  it('every whitespace-separated word must match, across kinds', () => {
    const items = model().visible;
    expect(filterItems(items, { query: 'probe', category: null }, label).length).toBe(2);
    expect(filterItems(items, { query: 'probe ship', category: null }, label)).toHaveLength(1);
    expect(filterItems(items, { query: 'which branch', category: null }, label)).toHaveLength(1);
    expect(filterItems(items, { query: DIGEST.slice(0, 12), category: null }, label)).toHaveLength(1);
    expect(filterItems(items, { query: 'nothing matches this', category: null }, label)).toHaveLength(0);
    expect(filterItems(items, EMPTY_FILTER, label)).toHaveLength(items.length);
  });

  it('a category chip narrows to that category and counts per category', () => {
    const items = model().visible;
    expect(filterItems(items, { query: '', category: 'delivery_decision' }, label)).toHaveLength(1);
    const chips = chipsOf(receptionOf([]), items);
    expect(chips.find((c) => c.category === 'clarification')?.count).toBe(1);
    expect(chips.find((c) => c.category === 'blockers')).toBeUndefined();
  });
});

describe('linking a request to a Mission', () => {
  it('only a unique mission_core native id links', () => {
    expect(missionForRequest(MN, [core(MN)])).toBe(`synthetic_core:${MN}`);
    expect(missionForRequest(MN, [core(MN), core(MN, 'other')])).toBeNull();
    expect(missionForRequest(MN, [core(MN, 'wf', 'workflow_store')])).toBeNull();
    expect(missionForRequest(MN, [])).toBeNull();
  });
});

describe('client invalidation reaches the source-keyed parts', () => {
  it('makes delivery entries not current and a known-empty source unavailable', () => {
    const r = invalidateReception(
      receptionOf([], [delivery()], {
        sourceCoverage: [
          { sourceId: 'grok_delivery_presentations', label: 'x', outcome: 'known_empty', reason: null, statement: '', total: 0 },
        ],
      }),
      'disconnected',
    );
    expect(r.sourceEntries![0].current).toBe(false);
    expect(r.sourceEntries![0].staleReasons).toContain('client:disconnected');
    expect(r.sourceCoverage![0].outcome).toBe('unavailable');
  });
});

describe('no raw blocker JSON on the first layer', () => {
  it('leads with the plain facts and keeps the blocker id behind the details', () => {
    const json = JSON.stringify({ blocker_id: 'bk-123', key: 'waiting on the signing key', severity: 'hard', opened_at: T0 });
    const text = plainBlocker(json);
    expect(text).toBe(`Reported blocker: waiting on the signing key (hard), opened ${utcStamp(T0)}`);
    expect(text).not.toMatch(/[{}"]/);
    expect(text).not.toContain('bk-123');
    expect(plainBlocker('{"key":"only a key"}')).toBe('Reported blocker: only a key');
  });

  it('shows source-written plain text verbatim and says plainly when a record is unreadable', () => {
    expect(plainBlocker('waiting on the signing key')).toBe('Reported blocker: waiting on the signing key');
    for (const bad of ['{"blocker_id":"bk-1","key":', '{"blocker_id":"bk-1"}', '[1,2]', '{"key":""}']) {
      const text = plainBlocker(bad);
      expect(text).toBe('A blocker is reported; its exact record is in the details below.');
      expect(text).not.toMatch(/[{}"]/);
    }
  });

  it('plainReasons never prints the record on a card', () => {
    const e = missionEntry({
      category: 'blockers',
      causes: [
        {
          cause: 'mission_core_blockers',
          label: 'Mission Core: active blockers on this Mission',
          details: [
            { label: 'hard blocker active', value: 'true' },
            { label: 'blocker', value: JSON.stringify({ blocker_id: 'bk-9', key: 'k', severity: 'soft', opened_at: T0 }) },
          ],
          staleReasons: [],
        },
      ],
    });
    const reasons = plainReasons(e);
    expect(reasons.join(' ')).not.toMatch(/[{}]|blocker_id|bk-9/);
    expect(reasons.some((r) => r.startsWith('Reported blocker: k (soft), opened '))).toBe(true);
  });
});

describe('the request panel as a source, from this window’s own state', () => {
  it('reads the state it holds and never fetches', () => {
    const base = { unvouched: null, availability: { kind: 'ok' }, overview: null } as unknown as Parameters<typeof panelSourceOf>[0];
    expect(panelSourceOf(null)).toEqual({ kind: 'not_provided' });
    expect(panelSourceOf({ ...base!, unvouched: 'disconnected' })).toMatchObject({ kind: 'not_vouched' });
    expect(panelSourceOf({ ...base!, availability: { kind: 'unavailable', problem: 'p', reason: 'r' } })).toMatchObject({ kind: 'not_available' });
    expect(panelSourceOf(base)).toEqual({ kind: 'not_read' });
    const overview = parseOverview({ server_now: 1, requests: [], conversations: [], capabilities: { decision: {}, scope_meanings: {} } });
    expect(panelSourceOf({ ...base!, overview })).toMatchObject({ kind: 'read' });
  });
});
