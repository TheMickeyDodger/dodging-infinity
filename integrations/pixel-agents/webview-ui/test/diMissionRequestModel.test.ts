/**
 * Item 4 — the Mission request panel's model: parsers, the reducer and the words, without a
 * browser. The rules pinned here are the ones a renderer must not be able to get wrong:
 * a display is FROZEN and a moved binding INVALIDATES it (never re-points it); a decision binds to
 * what was SHOWN; a write of unknown outcome is shown as unknown and never replayed; nothing stays
 * on screen once the client cannot vouch for it; and no wording calls a withdrawal a denial.
 */
import { describe, expect, it } from 'vitest';

import type { DiMissionRequestReply } from '../../core/src/messages.ts';
import {
  durationWords,
  proofRulesAbsentWords,
  proofRuleSections,
  readProofRules,
} from '../src/components/diMissionRequestContract.ts';
import {
  actionPending,
  agoWords,
  bindingOf,
  buildMessage,
  codePointCount,
  composerProblem,
  countdownWords,
  countsOf,
  decisionBlockedWords,
  decisionFields,
  EMPTY_CONVERSATION,
  entryWords,
  INITIAL_STATE,
  isDecisionWord,
  isPending,
  journeyIndex,
  layoutFor,
  linesOf,
  MR_FIXTURE_WORDS,
  MR_MAX_TEXT_CHARS,
  MR_NAME,
  MR_PANE_PX,
  MR_PANE_WIDE_PX,
  MR_PROGRESS_LABELS,
  MR_TAGLINE,
  MR_WORLD_KEEP_PX,
  type MrEvent,
  type MrState,
  needsYou,
  offeredActions,
  offerMatchesDisplay,
  offerState,
  omittedWords,
  parseConversation,
  parseOffer,
  parseOverview,
  parseProgression,
  parseRequest,
  parseResult,
  problemWords,
  progressionWords,
  reduce,
  remainingSeconds,
  scopeLine,
  serverNowSeconds,
  shortDigest,
  turnStateWords,
  unavailableWords,
  withdrawFields,
} from '../src/components/diMissionRequestModel.ts';
import {
  MR_START_CAUSE_MAX_CHARS,
  MR_START_CAUSE_MAX_ROLES,
  parseStartCause,
  START_CAUSE_CLOSED_NOTE,
  START_CAUSE_NOTE,
  startCauseWords,
} from '../src/components/diMissionRequestStartCause.ts';

type Loose = Record<string, any>; // eslint-disable-line @typescript-eslint/no-explicit-any

const REF = `lr-${'a'.repeat(32)}`;
const REF2 = `lr-${'b'.repeat(32)}`;
const MISSION = `mn-${'c'.repeat(32)}`;
const DIGEST = 'd'.repeat(64);
const DIGEST2 = 'e'.repeat(64);

/** A canonical proof contract: every part present, under the closed key sets Mission Core enforces. */
const contract = (over: Record<string, unknown> = {}): Record<string, unknown> => ({
  requirements: [
    {
      key: 'run_result',
      description: 'The result is bound to the run.',
      evidence_kinds: ['verification_record'],
      required_artifact_keys: ['result_file'],
      max_evidence_age_seconds: 3600,
    },
  ],
  required_artifacts: [
    { key: 'result_file', role: 'result', expected_content_digest_sha256: 'a'.repeat(64) },
  ],
  required_dependencies: [
    { key: 'base', kind: 'prerequisite', target: { form: 'exact_resource', resource_key: 'repo_main' } },
  ],
  required_resource_readiness: [{ resource_key: 'repo_main', max_age_seconds: 600 }],
  degradation_policy: { permitted_blocker_keys: ['flaky_network'] },
  continuation_budget: { max_attempts: 3, max_checkpoints: 5 },
  ...over,
});

const proposal = (over: Record<string, unknown> = {}): Record<string, unknown> => ({
  objective: 'Make the probe stable',
  target_context: 'readiness',
  repository_url: 'https://github.com/Example/Repo',
  requested_scope: 'the probe only',
  requested_action_scope: ['engineering_change', 'repository_read'],
  requested_delivery_target: null,
  proof_contract: contract(),
  ...over,
});

const lifecycle = (over: Record<string, unknown> = {}): Record<string, unknown> => ({
  stage: 'of_record',
  label: 'Waiting for your decision',
  detail: 'Nothing has been approved.',
  terminal: false,
  actions: ['decide', 'withdraw'],
  notice: null,
  approval_expires_at: null,
  provenance: null,
  residual_risk: null,
  ...over,
});

const wireRequest = (over: Record<string, unknown> = {}): Record<string, unknown> => ({
  ok: true,
  request_ref: REF,
  mission_id: MISSION,
  revision: 1,
  proposal: proposal(),
  proposal_digest_sha256: DIGEST,
  created_at: 100,
  lifecycle: lifecycle(),
  lineage: { supersedes: null, superseded_by: null, turn_id: null },
  cancellation: null,
  attestation: null,
  run: null,
  ...over,
});

const OFFER_DIGEST = 'f'.repeat(64);
const wireOffer = (over: Record<string, unknown> = {}): Record<string, unknown> => ({
  ok: true,
  request_ref: REF,
  mission_id: MISSION,
  revision: 1,
  proposal_digest_sha256: DIGEST,
  offer_digest_sha256: OFFER_DIGEST,
  expires_at: 1900,
  issued_at: 1000,
  approved_action_scope: ['engineering_change', 'repository_read'],
  approved_delivery_targets: [],
  repository_url: 'https://github.com/Example/Repo',
  approval_valid_seconds: 900,
  server_now: 1000,
  ...over,
});

const reply = (
  op: DiMissionRequestReply['op'],
  seq: number,
  state: DiMissionRequestReply['state'],
  answer: Record<string, unknown> | null,
  problem: string | null = null,
  reason: string | null = null,
): DiMissionRequestReply => ({
  type: 'diMissionRequestReply',
  seq,
  op,
  state,
  problem,
  reason,
  answer,
});

const apply = (state: MrState, ...events: MrEvent[]): MrState => events.reduce(reduce, state);
/** The same state with the frozen offer read for the proposal on display. */
const withOffer = (state: MrState, over: Record<string, unknown> = {}): MrState =>
  apply(
    state,
    { type: 'sent', seq: 2, op: 'offer', requestRef: REF, turnId: null },
    { type: 'reply', nowMs: 1100, reply: reply('offer', 2, 'ok', wireOffer(over)) },
  );

/** A state in which REF has been read once and is selected. */
const stateWithView = (wire: Record<string, unknown> = wireRequest()): MrState =>
  apply(
    INITIAL_STATE,
    { type: 'select', requestRef: REF },
    { type: 'sent', seq: 1, op: 'request', requestRef: REF, turnId: null },
    { type: 'reply', nowMs: 1000, reply: reply('request', 1, 'ok', wire) },
  );

describe("parsers: the adapter's answers are untrusted", () => {
  it('parses a request view into the shape the panel uses', () => {
    const r = parseRequest(
      wireRequest({
        attestation: {
          state: 'APPLIED',
          attested_at: 5,
          relayed_reply: 'approve',
          provenance_label: {
            principal_kind: 'operator_attested_relay',
            proof: 'operator_attested_not_independently_verified',
          },
          residual_risk: 'could fabricate it',
          binding: { expires_at: 900 },
        },
      }),
    )!;
    expect(r).toMatchObject({ requestRef: REF, missionId: MISSION, revision: 1, digest: DIGEST });
    expect(r.proposal).toMatchObject({
      objective: 'Make the probe stable',
      actionScope: ['engineering_change', 'repository_read'],
      deliveryTarget: null,
    });
    expect(r.proposal?.proof.unrepresented).toBeNull();
    expect(r.proposal?.proof.contract?.requirements).toEqual([
      {
        key: 'run_result',
        description: 'The result is bound to the run.',
        evidenceKinds: ['verification_record'],
        artifactKeys: ['result_file'],
        maxAgeSeconds: 3600,
      },
    ]);
    expect(r.attestation).toMatchObject({
      state: 'APPLIED',
      expiresAt: 900,
      provenanceProof: 'operator_attested_not_independently_verified',
    });
  });

  it('refuses a view missing its identity, instead of showing half of one', () => {
    for (const key of [
      'request_ref',
      'mission_id',
      'proposal_digest_sha256',
      'revision',
      'lifecycle',
    ]) {
      const wire = wireRequest();
      delete wire[key];
      expect(parseRequest(wire), key).toBeNull();
    }
    expect(parseRequest('x')).toBeNull();
    expect(parseRequest(null)).toBeNull();
    expect(parseRequest([])).toBeNull();
    expect(parseRequest(wireRequest({ revision: 1.5 }))).toBeNull();
    expect(parseRequest(wireRequest({ lifecycle: lifecycle({ terminal: 'no' }) }))).toBeNull();
  });

  it('drops an action this panel does not know how to offer, rather than guess', () => {
    const r = parseRequest(
      wireRequest({
        lifecycle: lifecycle({ actions: ['decide', 'rm -rf', 'shell', 7, 'withdraw'] }),
      }),
    )!;
    expect(r.lifecycle.actions).toEqual(['decide', 'withdraw']);
  });

  it('a request with no readable proposal still parses (and shows no proposal)', () => {
    expect(parseRequest(wireRequest({ proposal: { objective: 7 } }))?.proposal).toBeNull();
  });

  it('parses the overview, the conversation, the offer and the result', () => {
    const overview = parseOverview({
      server_now: 1000,
      requests_truncated: false,
      capabilities: {
        run_available: true,
        repository_url: 'https://github.com/Example/Repo',
        delivery_authority: 'none',
        decision: {
          transport: 'operator_attested_relay',
          proof: 'operator_attested_not_independently_verified',
          reply: 'approve',
          approval_valid_seconds: 900,
        },
        scope_meanings: {
          engineering_change: 'change files in an isolated copy of the repository',
        },
      },
      requests: [
        {
          request_ref: REF,
          mission_id: MISSION,
          revision: 1,
          created_at: 5,
          objective: 'Make it stable',
          proposal_digest_sha256: DIGEST,
          lifecycle: {
            stage: 'of_record',
            label: 'Waiting for your decision',
            terminal: false,
            actions: ['decide'],
            notice: null,
          },
        },
        { bad: true },
      ],
      conversations: [
        {
          conversation_id: `cv-${'f'.repeat(32)}`,
          turns: 2,
          last_activity: 9,
          first_words: 'hello',
        },
      ],
    })!;
    expect(overview.capabilities).toMatchObject({
      runAvailable: true,
      approvalValidSeconds: 900,
      deliveryAuthority: 'none',
    });
    expect(overview.requests).toHaveLength(1);
    expect(overview.conversations[0]).toMatchObject({ turns: 2, firstWords: 'hello' });

    const convo = parseConversation({
      conversation_id: `cv-${'f'.repeat(32)}`,
      turns_total: 1,
      turns: [
        {
          turn_id: `ct-${'1'.repeat(32)}`,
          conversation_id: `cv-${'f'.repeat(32)}`,
          sequence: 1,
          state: 'COMPLETED',
          in_flight: false,
          user_text: 'hi',
          assistant_text: 'hello',
          provider_text: 'untrusted',
          outcome: { status: 'completed', error_code: null, detail: null },
          draft: { proposal: proposal(), proposal_digest_sha256: DIGEST, runnable_problem: null },
          draft_problem: null,
          submission: {
            state: 'UNCERTAIN',
            request_ref: null,
            supersedes: null,
            problem: null,
            possible_matches: [REF],
          },
        },
        'junk',
      ],
    })!;
    expect(convo.turns).toHaveLength(1);
    expect(convo.turns[0].draft?.digest).toBe(DIGEST);
    expect(convo.turns[0].submission).toMatchObject({ state: 'UNCERTAIN', possibleMatches: [REF] });

    expect(parseOffer(wireOffer({ server_now: 4 }))).toMatchObject({
      revision: 1,
      serverNow: 4,
      expiresAt: 1900,
    });
    expect(
      parseResult({
        recoverable: true,
        result_text: 'r',
        review_text: 'v',
        result_truncated: false,
        review_truncated: true,
        delivered: false,
      }),
    ).toMatchObject({ resultText: 'r', reviewTruncated: true, delivered: false });
    expect(parseResult({ recoverable: 'yes' })).toBeNull();
    expect(parseOverview({})).toBeNull();
  });
});

describe("the workspace's tabs", () => {
  it('opens on the tab asked for, and remembers it', () => {
    expect(apply(INITIAL_STATE, { type: 'open' }).tab).toBe('ask');
    expect(apply(INITIAL_STATE, { type: 'open', tab: 'requests' })).toMatchObject({
      open: true,
      tab: 'requests',
    });
    expect(
      apply(INITIAL_STATE, { type: 'open', tab: 'requests' }, { type: 'close' }, { type: 'open' })
        .tab,
    ).toBe('requests');
    expect(apply(INITIAL_STATE, { type: 'tab', tab: 'requests' }).tab).toBe('requests');
  });
  it('a submitted proposal is shown where it can be read; starting a revision returns to the conversation', () => {
    const submitted = apply(
      INITIAL_STATE,
      { type: 'sent', seq: 1, op: 'submit', requestRef: null, turnId: `ct-${'1'.repeat(32)}` },
      {
        type: 'reply',
        nowMs: 1,
        reply: reply('submit', 1, 'ok', {
          created: true,
          submission: { state: 'SUBMITTED' },
          request: wireRequest(),
        }),
      },
    );
    expect(submitted.tab).toBe('requests');
    expect(
      apply({ ...INITIAL_STATE, tab: 'requests' }, { type: 'revise', requestRef: REF }).tab,
    ).toBe('ask');
    expect(
      apply({ ...INITIAL_STATE, tab: 'requests' }, { type: 'revise', requestRef: null }).tab,
    ).toBe('requests');
  });
});

describe('the decision word', () => {
  it("mirrors the adapter's rule: the whole reply, case and surrounding space ignored", () => {
    for (const ok of ['approve', 'Approve', ' APPROVED ', 'approved\n'])
      expect(isDecisionWord(ok), ok).toBe(true);
    for (const no of [
      '',
      'yes',
      'approve it',
      'I approve',
      'disapprove',
      'approve.',
      'app rove',
      'approve approve',
    ])
      expect(isDecisionWord(no), no).toBe(false);
  });
});

describe('a display is frozen; a moved binding invalidates it', () => {
  it('the same proposal moves with its record (the stage changes, the proposal does not)', () => {
    let s = stateWithView();
    s = apply(
      s,
      { type: 'sent', seq: 2, op: 'request', requestRef: REF, turnId: null },
      {
        type: 'reply',
        nowMs: 2000,
        reply: reply(
          'request',
          2,
          'ok',
          wireRequest({
            lifecycle: lifecycle({ stage: 'approved', label: 'Approved', actions: ['dispatch'] }),
          }),
        ),
      },
    );
    expect(s.views[REF].stale).toBe(false);
    expect(s.views[REF].displayed.lifecycle.stage).toBe('approved');
    expect(s.views[REF].readAtMs).toBe(2000);
  });

  it('a different revision FREEZES the display, marks it stale and clears what was typed', () => {
    let s = stateWithView();
    s = apply(s, { type: 'typed', text: 'approve' });
    s = apply(
      s,
      { type: 'sent', seq: 2, op: 'request', requestRef: REF, turnId: null },
      {
        type: 'reply',
        nowMs: 2000,
        reply: reply(
          'request',
          2,
          'ok',
          wireRequest({
            revision: 2,
            proposal_digest_sha256: DIGEST2,
            proposal: proposal({ objective: 'Something else entirely' }),
          }),
        ),
      },
    );
    const view = s.views[REF];
    expect(view.stale).toBe(true);
    expect(view.displayed.revision).toBe(1);
    expect(view.displayed.proposal?.objective).toBe('Make the probe stable');
    expect(view.latest.revision).toBe(2);
    expect(s.typed).toBe('');
  });

  it('a digest or Mission change invalidates it just the same', () => {
    for (const change of [
      { proposal_digest_sha256: DIGEST2 },
      { mission_id: `mn-${'9'.repeat(32)}` },
    ]) {
      const s = apply(
        stateWithView(),
        { type: 'sent', seq: 2, op: 'request', requestRef: REF, turnId: null },
        { type: 'reply', nowMs: 2000, reply: reply('request', 2, 'ok', wireRequest(change)) },
      );
      expect(s.views[REF].stale, JSON.stringify(change)).toBe(true);
    }
  });

  it('stays stale on later reads, even if the record moves BACK: the person never read it again', () => {
    let s = stateWithView();
    s = apply(
      s,
      { type: 'sent', seq: 2, op: 'request', requestRef: REF, turnId: null },
      {
        type: 'reply',
        nowMs: 2000,
        reply: reply(
          'request',
          2,
          'ok',
          wireRequest({ revision: 2, proposal_digest_sha256: DIGEST2 }),
        ),
      },
    );
    s = apply(
      s,
      { type: 'sent', seq: 3, op: 'request', requestRef: REF, turnId: null },
      { type: 'reply', nowMs: 3000, reply: reply('request', 3, 'ok', wireRequest()) },
    );
    expect(s.views[REF].stale).toBe(true);
  });

  it('withholds every action while stale, and only an explicit acknowledgement reads the current proposal', () => {
    let s = stateWithView();
    expect(offeredActions(s, REF)).toEqual(['decide', 'withdraw']);
    s = apply(
      s,
      { type: 'sent', seq: 2, op: 'request', requestRef: REF, turnId: null },
      {
        type: 'reply',
        nowMs: 2000,
        reply: reply(
          'request',
          2,
          'ok',
          wireRequest({ revision: 2, proposal_digest_sha256: DIGEST2 }),
        ),
      },
    );
    expect(offeredActions(s, REF)).toEqual([]);
    s = apply(s, { type: 'acknowledge', requestRef: REF });
    expect(s.views[REF].stale).toBe(false);
    expect(s.views[REF].displayed.revision).toBe(2);
    expect(bindingOf(s.views[REF].displayed)).toMatchObject({ revision: 2, digest: DIGEST2 });
    expect(offeredActions(s, REF)).toEqual(['decide', 'withdraw']);
  });

  it('a decision and a withdrawal bind to what was SHOWN, never to the live record', () => {
    let s = withOffer(stateWithView());
    s = apply(
      s,
      { type: 'sent', seq: 3, op: 'request', requestRef: REF, turnId: null },
      {
        type: 'reply',
        nowMs: 2000,
        reply: reply(
          'request',
          3,
          'ok',
          wireRequest({ revision: 2, proposal_digest_sha256: DIGEST2 }),
        ),
      },
    );
    const view = s.views[REF];
    expect(decisionFields(view, 'approve')).toEqual({
      requestRef: REF,
      missionId: MISSION,
      revision: 1,
      digest: DIGEST,
      reply: 'approve',
      offerDigest: OFFER_DIGEST,
      expiresAt: 1900,
      scope: ['engineering_change', 'repository_read'],
      deliveryTargets: [],
      repositoryUrl: 'https://github.com/Example/Repo',
    });
    expect(withdrawFields(view)).toEqual({
      requestRef: REF,
      missionId: MISSION,
      revision: 1,
      digest: DIGEST,
    });
  });

  it('with no offer on display there is nothing to decide on: no fields at all', () => {
    expect(decisionFields(stateWithView().views[REF], 'approve')).toBeNull();
  });

  it('a refused decision on a stale display invalidates it and drops the typed word', () => {
    let s = stateWithView();
    s = apply(
      s,
      { type: 'typed', text: 'approve' },
      { type: 'sent', seq: 2, op: 'decide', requestRef: REF, turnId: null },
      {
        type: 'reply',
        nowMs: 2000,
        reply: reply(
          'decide',
          2,
          'refused',
          { details: { display_invalid: true } },
          'mission_panel_display_stale',
          'x',
        ),
      },
    );
    expect(s.views[REF].stale).toBe(true);
    expect(s.typed).toBe('');
    expect(s.notices[0]).toMatchObject({ kind: 'refusal' });
    expect(s.notices[0].text).toMatch(/no longer the offer of record/);
    expect(s.notices[0].text).toMatch(/Nothing was approved/);
  });

  it('selecting another request drops the typed word: a decision does not follow the person', () => {
    const s = apply(
      stateWithView(),
      { type: 'typed', text: 'approve' },
      { type: 'select', requestRef: REF2 },
    );
    expect(s.typed).toBe('');
    expect(
      apply(stateWithView(), { type: 'typed', text: 'app' }, { type: 'select', requestRef: REF })
        .typed,
    ).toBe('app');
  });
});

describe('the displayed offer: frozen, shared, compared, and refreshed only on request', () => {
  it('parses the offer whole, and refuses one without its digest or expiry', () => {
    const offer = parseOffer(wireOffer())!;
    expect(offer).toMatchObject({
      offerDigest: OFFER_DIGEST,
      expiresAt: 1900,
      issuedAt: 1000,
      scope: ['engineering_change', 'repository_read'],
      deliveryTargets: [],
      repositoryUrl: 'https://github.com/Example/Repo',
    });
    for (const key of ['offer_digest_sha256', 'expires_at']) {
      const wire = wireOffer();
      delete wire[key];
      expect(parseOffer(wire), key).toBeNull();
    }
  });

  it('is accepted only for exactly the proposal on display', () => {
    const s = withOffer(stateWithView());
    expect(s.views[REF].offer?.offerDigest).toBe(OFFER_DIGEST);
    expect(offerMatchesDisplay(s.views[REF].displayed, s.views[REF].offer!)).toBe(true);
    for (const change of [
      { revision: 2 },
      { proposal_digest_sha256: DIGEST2 },
      { mission_id: `mn-${'9'.repeat(32)}` },
    ]) {
      const moved = withOffer(stateWithView(), change);
      expect(moved.views[REF].stale, JSON.stringify(change)).toBe(true);
      expect(moved.views[REF].offer, JSON.stringify(change)).toBeNull();
      expect(decisionFields(moved.views[REF], 'approve')).toBeNull();
    }
  });

  it('reads as missing, live or expired by the clock the window can read', () => {
    const view = withOffer(stateWithView()).views[REF];
    expect(offerState(stateWithView().views[REF], 1500)).toBe('missing');
    expect(offerState(view, 1500)).toBe('live');
    expect(offerState(view, 1899)).toBe('live');
    expect(offerState(view, 1900)).toBe('expired');
    expect(offerState(view, 5000)).toBe('expired');
    expect(offerState(view, null)).toBe('live');
  });

  it('an EXPIRED refusal drops only the offer: the display stays valid and the typed word is cleared', () => {
    let s = withOffer(stateWithView());
    s = apply(
      s,
      { type: 'typed', text: 'approve' },
      { type: 'sent', seq: 5, op: 'decide', requestRef: REF, turnId: null },
      {
        type: 'reply',
        nowMs: 3000,
        reply: reply(
          'decide',
          5,
          'refused',
          { details: { display_invalid: false } },
          'mission_panel_offer_expired',
          'x',
        ),
      },
    );
    const view = s.views[REF];
    expect(view.stale).toBe(false);
    expect(view.offer).toBeNull();
    expect(view.offerEpoch).toBe(1);
    expect(view.displayed.digest).toBe(DIGEST);
    expect(s.typed).toBe('');
    expect(s.notices[0].text).toMatch(/approval window you were shown has passed/);
    expect(s.notices[0].text).toMatch(/Nothing about the proposal changed/);
  });

  it('a STALE refusal invalidates the display; an expired one does not (the two are never conflated)', () => {
    const base = withOffer(stateWithView());
    const stale = apply(
      base,
      { type: 'sent', seq: 5, op: 'decide', requestRef: REF, turnId: null },
      {
        type: 'reply',
        nowMs: 3000,
        reply: reply('decide', 5, 'refused', {}, 'mission_panel_display_stale', 'x'),
      },
    );
    const expired = apply(
      base,
      { type: 'sent', seq: 5, op: 'decide', requestRef: REF, turnId: null },
      {
        type: 'reply',
        nowMs: 3000,
        reply: reply('decide', 5, 'refused', {}, 'mission_panel_offer_expired', 'x'),
      },
    );
    expect(stale.views[REF].stale).toBe(true);
    expect(expired.views[REF].stale).toBe(false);
    expect(offeredActions(stale, REF)).toEqual([]);
    expect(offeredActions(expired, REF)).toEqual(['decide', 'withdraw']);
  });

  it('refreshing the offer is explicit: it drops the old one, clears the word and counts an epoch', () => {
    let s = apply(
      withOffer(stateWithView()),
      { type: 'typed', text: 'approve' },
      { type: 'dropOffer', requestRef: REF },
    );
    expect(s.views[REF].offer).toBeNull();
    expect(s.views[REF].offerEpoch).toBe(1);
    expect(s.typed).toBe('');
    s = withOffer(s, { offer_digest_sha256: '1'.repeat(64), expires_at: 2800 });
    expect(s.views[REF].offer).toMatchObject({ offerDigest: '1'.repeat(64), expiresAt: 2800 });
    expect(apply(s, { type: 'dropOffer', requestRef: REF2 })).toEqual(s);
  });

  it('acknowledging a stale display drops its offer and counts an epoch, so a new one is read', () => {
    let s = withOffer(stateWithView());
    s = apply(
      s,
      { type: 'sent', seq: 3, op: 'request', requestRef: REF, turnId: null },
      {
        type: 'reply',
        nowMs: 2000,
        reply: reply(
          'request',
          3,
          'ok',
          wireRequest({ revision: 2, proposal_digest_sha256: DIGEST2 }),
        ),
      },
    );
    s = apply(s, { type: 'acknowledge', requestRef: REF });
    expect(s.views[REF].offer).toBeNull();
    expect(s.views[REF].offerEpoch).toBe(1);
    expect(s.views[REF].displayed.revision).toBe(2);
  });
});

describe('the fixture label', () => {
  it('states that nothing in a fixture window is a real request or a real approval', () => {
    expect(MR_FIXTURE_WORDS).toMatch(/^FIXTURE \/ VISUAL TEST/);
    expect(MR_FIXTURE_WORDS).toMatch(/synthetic/);
    expect(MR_FIXTURE_WORDS).toMatch(/Nothing here is a real request awaiting a real decision/);
    expect(MR_FIXTURE_WORDS).toMatch(/no approval given here is real/);
  });
});

describe('the name: what is being requested', () => {
  it('names the Codex Orchestrator and says in one sentence what asking it does', () => {
    expect(MR_NAME).toBe('Codex Orchestrator');
    expect(MR_TAGLINE).toMatch(/Ask Codex for engineering work in ordinary words/);
    expect(MR_TAGLINE).toMatch(/you decide/);
    expect(MR_TAGLINE).toMatch(/isolated copy/);
  });
});

describe('progression: what the screen says about whether a started Mission moves on its own', () => {
  const running = parseProgression({
    state: 'running',
    detail: 'the progression runtime is passing',
    passes: 4,
    interval_seconds: 5,
    last_pass_age_seconds: 2,
  })!;
  const as = (state: string) =>
    progressionWords({ ...running, state: state as never, detail: `because ${state}` }, true)!;

  it('parses the documented fields and reads an unknown state as unreadable, never as running', () => {
    expect(running).toMatchObject({ state: 'running', passes: 4, lastPassAgeSeconds: 2 });
    expect(parseProgression({ state: 'definitely-fine', detail: 'x' })!.state).toBe('unreadable');
    expect(parseProgression({ state: 7 })!.state).toBe('unreadable');
    expect(parseProgression(null)).toBeNull();
    expect(parseProgression('running')).toBeNull();
  });

  it('the always-visible line is short: the state and what Start does, with no counters or diagnostics', () => {
    const view = progressionWords(running, true)!;
    expect(view.tone).toBe('ok');
    expect(view.label).toBe('ON');
    expect(view.text).toBe(
      'Automatic progress is ON. Start begins the work; after that it advances by itself.',
    );
    expect(view.text.length).toBeLessThan(110);
    for (const noise of [/pass/i, /\d+ s/, /runtime status/i, /interval/i])
      expect(view.text).not.toMatch(noise);
  });

  it('every state has its OWN label on the line itself, and only ON says it advances by itself', () => {
    const lines = new Map<string, string>([
      ['ON', progressionWords(running, true)!.text],
      ['OFF', as('stopped').text],
      ['STALLED', as('stale').text],
      ['NOT FOR THESE REQUESTS', as('mismatch').text],
      ['UNKNOWN', as('unreadable').text],
      ['FAILED', as('failing').text],
      ['UNAVAILABLE', progressionWords(running, true, false)!.text],
    ]);
    expect([...lines.keys()].sort()).toEqual([...MR_PROGRESS_LABELS].sort());
    for (const [label, text] of lines) {
      // The label is on the visible line, as a word, and no other state's label is.
      expect(text, label).toContain(label);
      for (const other of MR_PROGRESS_LABELS) {
        if (other === label) continue;
        // 'ON' is a substring of other labels' words; compare whole words.
        const word = new RegExp(`(^|[^A-Z])${other}([^A-Z]|$)`);
        expect(word.test(text), `${label} line must not carry ${other}`).toBe(false);
      }
      if (label !== 'ON') expect(text, label).not.toMatch(/advances by itself/);
    }
  });

  it('unknown, unavailable and failed stay exactly distinguishable and never read as running', () => {
    const unknown = as('unreadable');
    const unavailable = progressionWords(running, true, false)!;
    const failed = as('failing');
    expect(new Set([unknown.label, unavailable.label, failed.label]).size).toBe(3);
    for (const view of [unknown, unavailable, failed, as('stopped'), as('stale'), as('mismatch')]) {
      expect(view.tone).toBe('warn');
      expect(view.label).not.toBe('ON');
      expect(view.text).not.toMatch(/Automatic progress is ON/);
    }
    expect(failed.text).toBe(
      'Automatic progress has FAILED. The runtime is running but its last pass failed; nothing is being advanced.',
    );
    expect(unknown.text).toMatch(/^Automatic progress is UNKNOWN\./);
    expect(unavailable.text).toMatch(/^Automatic progress is UNAVAILABLE\./);
  });

  it('what the line does not carry is in the disclosure, never the other way round', () => {
    const view = progressionWords(running, true)!;
    const details = view.details.join(' ');
    expect(details).toMatch(/Runtime status: the progression runtime is passing\./);
    expect(details).toMatch(/Passes so far: 4\./);
    expect(details).toMatch(/Last pass: 2 s ago\./);
    expect(details).toMatch(/about every 5 s/);
    expect(details).toMatch(/never starts a Mission/);
    expect(details).toMatch(/never records a failure as a success/);
    expect(details).toMatch(/never starts the work a second time/);
    // And the failure's own words are on the line, not only in the disclosure.
    expect(as('failing').text).toMatch(/last pass failed/);
    expect(as('stale').text).toMatch(/stopped reporting/);
    expect(as('stopped').text).toMatch(/nothing advances the work unless you do it by hand/);
  });

  it('says nothing where the panel cannot start work, and never claims to know when it cannot tell', () => {
    expect(progressionWords(null, false)).toBeNull();
    expect(progressionWords(running, false)).toBeNull();
    const unknown = progressionWords(null, true)!;
    expect(unknown.label).toBe('UNKNOWN');
    expect(unknown.tone).toBe('warn');
  });

  it('an unreachable panel is UNAVAILABLE even if the last overview said running', () => {
    expect(progressionWords(running, true, false)!.label).toBe('UNAVAILABLE');
    expect(progressionWords(null, false, false)!.label).toBe('UNAVAILABLE');
  });

  it('reaches the overview, and an overview without it reads as null, not as running', () => {
    const base = {
      ok: true,
      server_now: 100,
      requests: [],
      conversations: [],
      capabilities: {
        run_available: true,
        repository_url: null,
        delivery_authority: 'none',
        decision: {
          transport: 'operator_attested_relay',
          proof: 'operator_attested_not_independently_verified',
          reply: 'approve',
          approval_valid_seconds: 900,
        },
        scope_meanings: {},
      },
    };
    expect(parseOverview(base)!.capabilities.progression).toBeNull();
    const withIt = parseOverview({
      ...base,
      capabilities: {
        ...base.capabilities,
        progression: { state: 'running', detail: 'd', passes: 1 },
      },
    })!;
    expect(withIt.capabilities.progression!.state).toBe('running');
  });
});

describe('asking is held until the answer; an unknown outcome is shown as unknown and never replayed', () => {
  it('holds an action for a request while it is waiting for its answer', () => {
    let s = stateWithView();
    expect(actionPending(s, 'dispatch', REF)).toBe(false);
    s = apply(s, { type: 'sent', seq: 5, op: 'run', requestRef: REF, turnId: null });
    expect(actionPending(s, 'dispatch', REF)).toBe(true);
    expect(actionPending(s, 'verify', REF)).toBe(true); // one run at a time per request
    expect(actionPending(s, 'dispatch', REF2)).toBe(false);
    expect(isPending(s, 'run')).toBe(true);
    s = apply(s, { type: 'reply', nowMs: 1, reply: reply('run', 5, 'ok', wireRequest()) });
    expect(actionPending(s, 'dispatch', REF)).toBe(false);
  });

  it('a timeout or reset AFTER a write says the outcome is unknown, with no success shown', () => {
    for (const problem of ['panel_timeout', 'panel_network_error']) {
      const s = apply(
        stateWithView(),
        { type: 'sent', seq: 5, op: 'decide', requestRef: REF, turnId: null },
        {
          type: 'reply',
          nowMs: 1,
          reply: reply(
            'decide',
            5,
            'unavailable',
            null,
            problem,
            'The request panel did not answer in time. Whether it applied this request is not known; read the record again before acting.',
          ),
        },
      );
      expect(s.notices[0].kind).toBe('unknown');
      expect(s.notices[0].text).toMatch(/not known/);
      expect(s.availability.kind).toBe('ok'); // the panel is not declared down
      expect(s.pending).toEqual([]);
      expect(s.views[REF].displayed.lifecycle.stage).toBe('of_record'); // nothing was shown as done
    }
  });

  it('"not listening" means nothing was sent, and marks the panel unavailable', () => {
    const s = apply(
      stateWithView(),
      { type: 'sent', seq: 5, op: 'decide', requestRef: REF, turnId: null },
      {
        type: 'reply',
        nowMs: 1,
        reply: reply(
          'decide',
          5,
          'unavailable',
          null,
          'panel_unreachable',
          'The request panel is not listening. Nothing was sent.',
        ),
      },
    );
    expect(s.availability).toMatchObject({ kind: 'unavailable', problem: 'panel_unreachable' });
    expect(s.notices[0].kind).toBe('unavailable');
  });

  it('an unavailable refresh drops the overview rather than keep showing it as current', () => {
    let s = apply(
      INITIAL_STATE,
      { type: 'sent', seq: 1, op: 'refresh', requestRef: null, turnId: null },
      {
        type: 'reply',
        nowMs: 1,
        reply: reply('refresh', 1, 'ok', {
          server_now: 5,
          capabilities: { decision: {}, run_available: false },
          requests: [],
        }),
      },
    );
    expect(s.overview).not.toBeNull();
    s = apply(
      s,
      { type: 'sent', seq: 2, op: 'refresh', requestRef: null, turnId: null },
      {
        type: 'reply',
        nowMs: 2,
        reply: reply(
          'refresh',
          2,
          'unavailable',
          null,
          'panel_discovery_unavailable',
          'The request panel is not running (discovery_absent).',
        ),
      },
    );
    expect(s.overview).toBeNull();
    expect(s.availability).toMatchObject({
      kind: 'unavailable',
      problem: 'panel_discovery_unavailable',
    });
  });

  it('keeps the composer when a turn is refused and clears it only when the turn was accepted', () => {
    let s = apply(
      INITIAL_STATE,
      { type: 'composer', text: 'my request' },
      { type: 'sent', seq: 1, op: 'turn', requestRef: null, turnId: null },
      {
        type: 'reply',
        nowMs: 1,
        reply: reply('turn', 1, 'refused', null, 'mission_panel_conversation_busy', 'busy'),
      },
    );
    expect(s.composer).toBe('my request');
    s = apply(
      s,
      { type: 'sent', seq: 2, op: 'turn', requestRef: null, turnId: null },
      {
        type: 'reply',
        nowMs: 2,
        reply: reply('turn', 2, 'ok', {
          turn: {
            turn_id: `ct-${'1'.repeat(32)}`,
            conversation_id: `cv-${'f'.repeat(32)}`,
            sequence: 1,
            state: 'RESERVED',
            in_flight: true,
            user_text: 'my request',
            assistant_text: null,
            outcome: null,
            draft: null,
            draft_problem: null,
            submission: null,
          },
        }),
      },
    );
    expect(s.composer).toBe('');
    expect(s.conversation).toMatchObject({ id: `cv-${'f'.repeat(32)}` });
    expect(s.conversation.turns[0].state).toBe('RESERVED');
  });

  it('a conversation read for a conversation that is no longer open is ignored', () => {
    const s = apply(
      INITIAL_STATE,
      { type: 'openConversation', conversationId: `cv-${'1'.repeat(32)}` },
      { type: 'sent', seq: 1, op: 'conversation', requestRef: null, turnId: null },
      {
        type: 'reply',
        nowMs: 1,
        reply: reply('conversation', 1, 'ok', {
          conversation_id: `cv-${'2'.repeat(32)}`,
          turns: [],
          turns_total: 0,
        }),
      },
    );
    expect(s.conversation.id).toBe(`cv-${'1'.repeat(32)}`);
  });

  it('a submitted proposal selects the new request and ends the revision', () => {
    let s = apply(INITIAL_STATE, { type: 'revise', requestRef: REF });
    s = apply(
      s,
      { type: 'sent', seq: 1, op: 'submit', requestRef: null, turnId: `ct-${'1'.repeat(32)}` },
      {
        type: 'reply',
        nowMs: 1,
        reply: reply('submit', 1, 'ok', {
          created: true,
          submission: { state: 'SUBMITTED' },
          request: wireRequest({ request_ref: REF2 }),
        }),
      },
    );
    expect(s.selectedRef).toBe(REF2);
    expect(s.revising).toBeNull();
  });

  it('an uncertain submission is announced as uncertain and never as a proposal', () => {
    const s = apply(
      INITIAL_STATE,
      { type: 'sent', seq: 1, op: 'submit', requestRef: null, turnId: `ct-${'1'.repeat(32)}` },
      {
        type: 'reply',
        nowMs: 1,
        reply: reply('submit', 1, 'ok', {
          created: true,
          submission: { state: 'UNCERTAIN' },
          request: null,
        }),
      },
    );
    expect(s.selectedRef).toBeNull();
    expect(s.notices[0]).toMatchObject({ kind: 'unknown' });
    expect(s.notices[0].text).toMatch(/not known/);
    expect(s.notices[0].text).toMatch(/not submitted again/);
  });
});

describe('nothing stays on screen once the client cannot vouch for it', () => {
  it("drops the list, the views, what was typed and every pending ask; keeps the person's own words", () => {
    let s = apply(
      stateWithView(),
      { type: 'composer', text: 'half written' },
      { type: 'typed', text: 'approve' },
    );
    s = apply(s, { type: 'sent', seq: 9, op: 'run', requestRef: REF, turnId: null });
    s = apply(s, { type: 'unvouched', reason: 'disconnected' });
    expect(s.views).toEqual({});
    expect(s.overview).toBeNull();
    expect(s.typed).toBe('');
    expect(s.pending).toEqual([]);
    expect(s.composer).toBe('half written');
    expect(offeredActions(s, REF)).toEqual([]);
    expect(entryWords(s)).toMatch(/cannot vouch/);
    s = apply(s, { type: 'unvouched', reason: null });
    expect(s.unvouched).toBeNull();
  });
});

describe('words', () => {
  it('no wording calls a withdrawal or a refusal a denial', () => {
    const everything = [
      ...[
        'local_request_reply_not_affirmative',
        'mission_panel_display_stale',
        'mission_panel_offer_expired',
        'local_request_cancelled',
        'mission_panel_withdrawal_key_unavailable',
        'mission_panel_action_not_offered',
        'mission_panel_conversation_busy',
        'mission_panel_provider_busy',
        'office_busy',
        'request_invalid',
        'something_else',
      ].map((p) => problemWords(p, null)),
      ...[
        'panel_not_configured',
        'panel_discovery_unavailable',
        'panel_unreachable',
        'panel_credentials_rejected',
        'other',
      ].map((p) => unavailableWords(p, 'reason')),
      countdownWords(0),
      countdownWords(75),
      countdownWords(9),
    ].join('\n');
    expect(everything).not.toMatch(/\b(deny|denied|denial|rejected)\b/i);
  });

  it('a stale refusal and a not-the-word refusal tell the person exactly what happened', () => {
    expect(problemWords('local_request_reply_not_affirmative', 'x')).toContain('"approve"');
    expect(problemWords('local_request_reply_not_affirmative', 'x')).toContain(
      'Nothing was recorded',
    );
    expect(problemWords('mission_panel_display_stale', null)).toContain('Nothing was approved');
    expect(problemWords('weird', 'The adapter said this.')).toBe('The adapter said this.');
  });

  it('says the panel is not set up, and how, without inventing a status', () => {
    expect(unavailableWords('panel_not_configured', '')).toMatch(/--mission-panel-discovery/);
    expect(unavailableWords('panel_discovery_unavailable', 'x (discovery_absent)')).toMatch(
      /not running/,
    );
  });

  it('the countdown is plain and never negative', () => {
    expect(countdownWords(75)).toBe(
      'The approval is valid for 1 min 15 s more. Start the run before then, or it expires.',
    );
    expect(countdownWords(9)).toContain('9 s');
    expect(countdownWords(0)).toBe('The approval window has passed.');
    expect(countdownWords(-30)).toBe('The approval window has passed.');
  });

  it('estimates the server clock from the overview and measures what remains', () => {
    const overview = parseOverview({
      server_now: 1000,
      capabilities: { decision: {}, run_available: false },
      requests: [],
    })!;
    expect(serverNowSeconds(overview, 5000, 12000)).toBe(1007);
    expect(serverNowSeconds(overview, 5000, 4000)).toBe(1000); // a clock step back never rewinds
    expect(serverNowSeconds(null, null, 1)).toBeNull();
    expect(remainingSeconds(1900, 1007)).toBe(893);
    expect(remainingSeconds(null, 1007)).toBeNull();
    expect(agoWords(0, 2000)).toBe('just now');
    expect(agoWords(0, 30000)).toBe('30 s ago');
    expect(agoWords(0, 180000)).toBe('3 min ago');
  });

  it('counts what needs the person, and says so on the entry', () => {
    const overview = parseOverview({
      server_now: 1,
      capabilities: { decision: {}, run_available: false },
      requests: [
        {
          request_ref: REF,
          mission_id: MISSION,
          revision: 1,
          proposal_digest_sha256: DIGEST,
          lifecycle: { stage: 'of_record', label: 'a', terminal: false, actions: ['decide'] },
        },
        {
          request_ref: REF2,
          mission_id: MISSION,
          revision: 1,
          proposal_digest_sha256: DIGEST,
          lifecycle: { stage: 'running', label: 'b', terminal: false, actions: [] },
        },
      ],
    })!;
    expect(countsOf(overview)).toEqual({ needsYou: 1, running: 1, total: 2 });
    expect(needsYou(['decide'])).toBe(true);
    expect(needsYou(['dispatch'])).toBe(false);
    const s: MrState = { ...INITIAL_STATE, overview, availability: { kind: 'ok' } };
    expect(entryWords(s)).toBe('1 needs you · 1 in progress · 2 in all');
    expect(
      entryWords({
        ...INITIAL_STATE,
        availability: {
          kind: 'unavailable',
          problem: 'p',
          reason: 'Mission requests are not set up.',
        },
      }),
    ).toBe('Mission requests are not set up.');
    expect(entryWords(INITIAL_STATE)).toBe('Reading requests…');
    expect(entryWords({ ...INITIAL_STATE, overview: { ...overview, requests: [] } })).toMatch(
      /No requests yet/,
    );
  });

  it('places every lifecycle stage the adapter can name on the journey, or on none', () => {
    const on: Record<string, number> = {
      of_record: 0,
      approval_uncertain: 0,
      approved: 1,
      approved_elsewhere: 1,
      dispatch_uncertain: 2,
      dispatched: 2,
      running: 3,
      pending_proof: 3,
      verified: 4,
    };
    for (const [stage, index] of Object.entries(on)) expect(journeyIndex(stage), stage).toBe(index);
    for (const stage of [
      'withdrawn',
      'revised',
      'expired',
      'blocked',
      'run_cancelled',
      'closed_elsewhere',
      'unreadable',
      'withdrawal_incomplete',
    ]) {
      expect(journeyIndex(stage), stage).toBeNull();
    }
  });

  it('describes a turn that has not produced a reply, and says an uncertain one was not retried', () => {
    const turn = (state: string) =>
      parseConversation({
        conversation_id: `cv-${'f'.repeat(32)}`,
        turns: [
          {
            turn_id: `ct-${'1'.repeat(32)}`,
            conversation_id: `cv-${'f'.repeat(32)}`,
            sequence: 1,
            state,
            user_text: 'x',
          },
        ],
      })!.turns[0];
    expect(turnStateWords(turn('COMPLETED'))).toBeNull();
    expect(turnStateWords(turn('RESERVED'))).toMatch(/cannot approve or start anything/);
    expect(turnStateWords(turn('UNCERTAIN'))).toMatch(/not known/);
    expect(turnStateWords(turn('UNCERTAIN'))).toMatch(/not sent again/);
    expect(turnStateWords(turn('FAILED'))).toMatch(/Nothing was approved or started/);
  });
});

describe('where the workspace sits', () => {
  const PANEL = 420;
  it('takes its width beside the office panel and leaves the world visible where the window allows', () => {
    expect(layoutFor(1600, false, false, PANEL)).toEqual({
      covering: false,
      widthPx: MR_PANE_PX,
      rightPx: PANEL,
    });
    expect(layoutFor(1800, false, true, PANEL)).toEqual({
      covering: false,
      widthPx: MR_PANE_WIDE_PX,
      rightPx: PANEL,
    });
    // "Wide" never takes the room the world is promised: at 1600 it narrows to keep 240 px of it.
    expect(layoutFor(1600, false, true, PANEL)).toEqual({
      covering: false,
      widthPx: 1600 - PANEL - MR_WORLD_KEEP_PX,
      rightPx: PANEL,
    });
    const squeezed = layoutFor(1100, false, false, PANEL);
    expect(squeezed.covering).toBe(false);
    expect(1100 - PANEL - squeezed.widthPx).toBeGreaterThanOrEqual(MR_WORLD_KEEP_PX);
  });
  it('docks to the edge when the office panel is hidden', () => {
    expect(layoutFor(1100, true, false, PANEL)).toEqual({
      covering: false,
      widthPx: MR_PANE_PX,
      rightPx: 0,
    });
  });
  it('covers the window, and must say so, when there is no room beside the panel', () => {
    for (const viewport of [320, 700, 900, 1000]) {
      expect(layoutFor(viewport, false, false, PANEL), String(viewport)).toEqual({
        covering: true,
        widthPx: viewport,
        rightPx: 0,
      });
    }
  });
});

describe('the composer and small helpers', () => {
  it('counts characters as the adapter does (code points), not UTF-16 units', () => {
    expect(codePointCount('😀😀')).toBe(2);
    expect(composerProblem('')).toMatch(/Write what you want/);
    expect(composerProblem('   ')).toMatch(/Write what you want/);
    expect(composerProblem('x'.repeat(MR_MAX_TEXT_CHARS))).toBeNull();
    expect(composerProblem('x'.repeat(MR_MAX_TEXT_CHARS + 1))).toMatch(/longer than/);
    expect(composerProblem('😀'.repeat(MR_MAX_TEXT_CHARS))).toBeNull();
  });

  it('builds closed messages and reading lines', () => {
    expect(buildMessage('refresh', 3)).toEqual({ type: 'diMissionRequest', seq: 3, op: 'refresh' });
    expect(buildMessage('run', 4, { requestRef: REF, command: 'dispatch' })).toEqual({
      type: 'diMissionRequest',
      seq: 4,
      op: 'run',
      requestRef: REF,
      command: 'dispatch',
    });
    expect(linesOf('a\nb\r\nc\n')).toEqual(['a', 'b', 'c']);
    expect(linesOf('')).toEqual(['']);
    expect(shortDigest(DIGEST)).toBe('dddddddddddd…');
    expect(scopeLine('engineering_change', { engineering_change: 'change files' })).toBe(
      'change files (engineering_change)',
    );
    expect(scopeLine('unknown_scope', {})).toBe('unknown_scope');
  });
});


// ---------------------------------------------------------------------------------------------
// Round 27, F1 — the proof rules a person approves are SHOWN in the offer that binds them, or the
// proposal is refused. Showing less than the contract and accepting anyway must not exist.
// ---------------------------------------------------------------------------------------------

/** The proposal wire object (as the panel sends it) with the contract replaced or edited. */
const withContract = (c: Record<string, unknown> | null): Record<string, unknown> =>
  proposal({ proof_contract: c });

const withoutContract = (): Record<string, unknown> => {
  const wire = proposal();
  delete wire.proof_contract;
  return wire;
};
const rulesOf = (c: Record<string, unknown> | null) => readProofRules(withContract(c));
const proseOf = (c: Record<string, unknown>): string => {
  const rules = rulesOf(c);
  expect(rules.unrepresented, JSON.stringify(c)).toBeNull();
  return JSON.stringify(proofRuleSections(rules.contract!));
};

describe('the proof rules: read whole under a closed shape, or the proposal is not represented', () => {
  it('reads every part of the contract', () => {
    const rules = rulesOf(
      contract({
        required_dependencies: [
          { key: 'a', kind: 'prerequisite', target: { form: 'exact_resource', resource_key: 'repo_main' } },
          {
            key: 'b',
            kind: 'prerequisite',
            target: {
              form: 'exact_mission',
              mission_id: MISSION,
              revision: 2,
              proposal_digest_sha256: DIGEST,
            },
          },
          {
            key: 'c',
            kind: 'eligibility',
            target: { form: 'eligibility', condition: 'merged', proposal_digest_sha256: DIGEST2 },
          },
        ],
      }),
    );
    expect(rules.unrepresented).toBeNull();
    expect(rules.contract).toMatchObject({
      requirements: [{ key: 'run_result', maxAgeSeconds: 3600, artifactKeys: ['result_file'] }],
      artifacts: [{ key: 'result_file', role: 'result', digest: 'a'.repeat(64) }],
      readiness: [{ resourceKey: 'repo_main', maxAgeSeconds: 600 }],
      permittedBlockers: ['flaky_network'],
      maxAttempts: 3,
      maxCheckpoints: 5,
    });
    expect(rules.contract?.dependencies.map((d) => d.target.form)).toEqual([
      'exact_resource',
      'exact_mission',
      'eligibility',
    ]);
  });

  it('an absent or null contract is a stated absence, not an unrepresented one', () => {
    for (const wire of [proposal({ proof_contract: null }), withoutContract()]) {
      const rules = readProofRules(wire);
      expect(rules).toEqual({ unrepresented: null, contract: null });
      expect(proofRulesAbsentWords(rules)).toContain('carries no proof rules');
    }
  });

  it('any field this window does not know, at ANY level, makes the proposal unrepresented', () => {
    const at = (edit: (c: Loose) => void): Record<string, unknown> => {
      const c = JSON.parse(JSON.stringify(contract())) as Loose;
      edit(c);
      return c;
    };
    const cases: Array<[string, Record<string, unknown>]> = [
      ['contract', at((c) => (c.extra = 1))],
      ['requirement', at((c) => (c.requirements[0].extra = 1))],
      ['artifact', at((c) => (c.required_artifacts[0].extra = 1))],
      ['dependency', at((c) => (c.required_dependencies[0].extra = 1))],
      ['target', at((c) => (c.required_dependencies[0].target.extra = 1))],
      ['readiness', at((c) => (c.required_resource_readiness[0].extra = 1))],
      ['degradation policy', at((c) => (c.degradation_policy.extra = 1))],
      ['continuation budget', at((c) => (c.continuation_budget.extra = 1))],
    ];
    for (const [where, c] of cases) {
      const rules = rulesOf(c);
      expect(rules.unrepresented, where).toContain('does not know');
      expect(rules.contract, where).toBeNull();
    }
    // ... and an unknown field on the PROPOSAL itself.
    expect(readProofRules(proposal({ extra_rule: 'x' })).unrepresented).toContain('extra_rule');
  });

  it('a missing part, a wrong kind of value, an unknown kind of target, or an over-long list is not represented', () => {
    const at = (edit: (c: Loose) => void): Record<string, unknown> => {
      const c = JSON.parse(JSON.stringify(contract())) as Loose;
      edit(c);
      return c;
    };
    const bad: Array<[string, Record<string, unknown>]> = [
      ['missing budget', at((c) => delete c.continuation_budget)],
      ['missing readiness', at((c) => delete c.required_resource_readiness)],
      ['age is text', at((c) => (c.requirements[0].max_evidence_age_seconds = '3600'))],
      ['age is fractional', at((c) => (c.requirements[0].max_evidence_age_seconds = 1.5))],
      ['age is negative', at((c) => (c.requirements[0].max_evidence_age_seconds = -1))],
      ['attempts is text', at((c) => (c.continuation_budget.max_attempts = '3'))],
      ['key is a number', at((c) => (c.requirements[0].key = 7))],
      ['blockers not a list', at((c) => (c.degradation_policy.permitted_blocker_keys = 'x'))],
      ['unknown target form', at((c) => (c.required_dependencies[0].target.form = 'anything'))],
      ['target is text', at((c) => (c.required_dependencies[0].target = 'repo_main'))],
      ['requirements not a list', at((c) => (c.requirements = {}))],
      [
        'too many requirements',
        at((c) => (c.requirements = Array.from({ length: 33 }, (_, i) => ({ ...c.requirements[0], key: `k${i}` })))),
      ],
      ['contract is text', 'x' as unknown as Record<string, unknown>],
      ['contract is a list', [] as unknown as Record<string, unknown>],
    ];
    for (const [why, c] of bad) {
      const rules = rulesOf(c);
      expect(rules.unrepresented, why).not.toBeNull();
      expect(rules.contract, why).toBeNull();
    }
  });

  it('never throws, whatever the panel sent', () => {
    for (const wire of [null, undefined, 7, 'x', [], {}, { proof_contract: {} }, { proof_contract: [] }]) {
      expect(() => readProofRules(wire)).not.toThrow();
    }
  });
});

describe('the proof rules, in words: every rule appears, and a change to any one changes the words', () => {
  it('says each rule with its exact numbers, keys and digests', () => {
    const text = proofRuleSections(rulesOf(contract()).contract!)
      .flatMap((section) => [section.title, ...section.lines])
      .join('\n');
    for (const needed of [
      'run_result',
      'The result is bound to the run.',
      'verification_record',
      'result_file',
      '1 hour (3600 s)',
      'a'.repeat(64),
      'base',
      'repo_main',
      '10 minutes (600 s)',
      'flaky_network',
      'At most 3 attempts and 5 checkpoints',
    ]) {
      expect(text, needed).toContain(needed);
    }
  });

  it('every field a person is asked to rely on moves the words when it changes', () => {
    const base = proseOf(contract());
    const mutate = (edit: (c: Loose) => void): string => {
      const c = JSON.parse(JSON.stringify(contract())) as Loose;
      edit(c);
      return proseOf(c);
    };
    const edits: Record<string, (c: Loose) => void> = {
      'requirement key': (c) => (c.requirements[0].key = 'other'),
      'requirement description': (c) => (c.requirements[0].description = 'A weaker sentence.'),
      'evidence kind': (c) => (c.requirements[0].evidence_kinds = ['artifact_digest']),
      'evidence kinds removed': (c) => (c.requirements[0].evidence_kinds = []),
      'artifact reference': (c) => (c.requirements[0].required_artifact_keys = []),
      'evidence age': (c) => (c.requirements[0].max_evidence_age_seconds = 86400),
      'second requirement': (c) =>
        c.requirements.push({ ...c.requirements[0], key: 'second', required_artifact_keys: [] }),
      'artifact key': (c) => (c.required_artifacts[0].key = 'renamed'),
      'artifact role': (c) => (c.required_artifacts[0].role = 'log'),
      'artifact digest': (c) => (c.required_artifacts[0].expected_content_digest_sha256 = 'b'.repeat(64)),
      'artifact removed': (c) => {
        c.required_artifacts = [];
        c.requirements[0].required_artifact_keys = [];
      },
      'dependency key': (c) => (c.required_dependencies[0].key = 'renamed'),
      'dependency kind': (c) => (c.required_dependencies[0].kind = 'other_kind'),
      'dependency resource': (c) => (c.required_dependencies[0].target.resource_key = 'repo_other'),
      'dependency target form': (c) =>
        (c.required_dependencies[0].target = { form: 'eligibility', condition: 'merged', proposal_digest_sha256: DIGEST }),
      'dependency removed': (c) => (c.required_dependencies = []),
      'readiness resource': (c) => (c.required_resource_readiness[0].resource_key = 'repo_other'),
      'readiness age': (c) => (c.required_resource_readiness[0].max_age_seconds = 60),
      'readiness removed': (c) => (c.required_resource_readiness = []),
      'blocker': (c) => (c.degradation_policy.permitted_blocker_keys = ['other_blocker']),
      'blocker added': (c) => c.degradation_policy.permitted_blocker_keys.push('another'),
      'blockers removed': (c) => (c.degradation_policy.permitted_blocker_keys = []),
      'attempts': (c) => (c.continuation_budget.max_attempts = 4),
      'checkpoints': (c) => (c.continuation_budget.max_checkpoints = 6),
    };
    for (const [what, edit] of Object.entries(edits)) {
      expect(mutate(edit), what).not.toBe(base);
    }
  });

  it('the exact-Mission and eligibility targets show their identity, digest included', () => {
    const exact = proseOf(
      contract({
        required_dependencies: [
          {
            key: 'b',
            kind: 'prerequisite',
            target: { form: 'exact_mission', mission_id: MISSION, revision: 2, proposal_digest_sha256: DIGEST },
          },
        ],
      }),
    );
    for (const needed of [MISSION, 'revision 2', DIGEST]) expect(exact).toContain(needed);
    const eligible = proseOf(
      contract({
        required_dependencies: [
          { key: 'c', kind: 'x', target: { form: 'eligibility', condition: 'merged', proposal_digest_sha256: DIGEST2 } },
        ],
      }),
    );
    for (const needed of ['merged', DIGEST2]) expect(eligible).toContain(needed);
  });

  it('says what an empty list means, rather than saying nothing', () => {
    const text = proseOf(
      contract({
        required_artifacts: [],
        required_dependencies: [],
        required_resource_readiness: [],
        degradation_policy: { permitted_blocker_keys: [] },
        requirements: [
          { key: 'r', description: 'd', evidence_kinds: [], required_artifact_keys: [], max_evidence_age_seconds: 0 },
        ],
      }),
    );
    for (const needed of [
      'No artifact is required.',
      'No prerequisite is required.',
      'No resource readiness is required.',
      'Any open blocker prevents completion.',
      'evidence of any recorded kind',
      'needing no artifact',
    ]) {
      expect(text, needed).toContain(needed);
    }
  });

  it('durations are read in the unit a person uses, with the exact seconds beside them', () => {
    expect(durationWords(0)).toBe('0 seconds');
    expect(durationWords(1)).toBe('1 second');
    expect(durationWords(45)).toBe('45 seconds');
    expect(durationWords(60)).toBe('1 minute (60 s)');
    expect(durationWords(600)).toBe('10 minutes (600 s)');
    expect(durationWords(3600)).toBe('1 hour (3600 s)');
    expect(durationWords(5400)).toBe('90 minutes (5400 s)');
    expect(durationWords(172800)).toBe('2 days (172800 s)');
  });
});

describe('F1: a proposal is approved only on rules the window shows', () => {
  /** A state in which REF is read with the given wire proposal and its offer is on display. */
  const stateWith = (wireProposal: Record<string, unknown>): MrState =>
    withOffer(stateWithView(wireRequest({ proposal: wireProposal })));

  it('approval is possible when the rules are shown in full', () => {
    const s = stateWith(proposal());
    expect(decisionBlockedWords(s.views[REF])).toBeNull();
    expect(decisionFields(s.views[REF], 'approve')).not.toBeNull();
  });

  it('approval is possible when the proposal states no rules at all (and says so)', () => {
    const s = stateWith(proposal({ proof_contract: null }));
    expect(decisionBlockedWords(s.views[REF])).toBeNull();
    expect(decisionFields(s.views[REF], 'approve')).not.toBeNull();
  });

  it('approval is REFUSED when any part of the contract cannot be represented, even with a matching offer', () => {
    const c = contract() as Loose;
    c.requirements[0].new_rule_family = { anything: true };
    const s = stateWith(proposal({ proof_contract: c }));
    expect(s.views[REF].offer, 'the offer itself is on display and matches').not.toBeNull();
    expect(offerMatchesDisplay(s.views[REF].displayed, s.views[REF].offer!)).toBe(true);
    expect(decisionBlockedWords(s.views[REF])).toContain('cannot show everything this proposal binds');
    expect(decisionBlockedWords(s.views[REF])).toContain('new_rule_family');
    expect(decisionFields(s.views[REF], 'approve')).toBeNull();
    // Withdrawing is not an approval and stays possible.
    expect(withdrawFields(s.views[REF]).requestRef).toBe(REF);
  });

  it('approval is refused when the proposal itself is not on display', () => {
    const s = withOffer(stateWithView(wireRequest({ proposal: null })));
    expect(decisionBlockedWords(s.views[REF])).toContain('not shown');
    expect(decisionFields(s.views[REF], 'approve')).toBeNull();
  });

  it('a revision that differs ONLY in the proof rules freezes the old display and moves the binding', () => {
    // Everything but the contract is identical: same objective, scope, context, repository.
    const newer = contract({ continuation_budget: { max_attempts: 64, max_checkpoints: 256 } });
    const before = stateWith(proposal());
    expect(before.views[REF].displayed.proposal).toMatchObject({ objective: 'Make the probe stable' });
    const s = apply(
      before,
      { type: 'sent', seq: 3, op: 'request', requestRef: REF, turnId: null },
      {
        type: 'reply',
        nowMs: 1200,
        reply: reply(
          'request',
          3,
          'ok',
          wireRequest({ revision: 2, proposal_digest_sha256: DIGEST2, proposal: proposal({ proof_contract: newer }) }),
        ),
      },
    );
    const view = s.views[REF];
    // The old display is refused as stale: it still shows the OLD rules, never the new, unseen ones.
    expect(view.stale).toBe(true);
    expect(view.displayed.proposal?.proof.contract?.maxAttempts).toBe(3);
    expect(view.latest.proposal?.proof.contract?.maxAttempts).toBe(64);
    // Nothing but the rules differs between what is shown and what is now of record.
    const { proof: shown, ...shownRest } = view.displayed.proposal!;
    const { proof: latest, ...latestRest } = view.latest.proposal!;
    expect(latestRest).toEqual(shownRest);
    expect(JSON.stringify(proofRuleSections(latest.contract!))).not.toBe(
      JSON.stringify(proofRuleSections(shown.contract!)),
    );
    // A decision from the frozen display is bound to the OLD digest (the panel refuses it as stale).
    expect(decisionFields(view, 'approve')?.digest).toBe(DIGEST);
    expect(decisionFields(view, 'approve')?.revision).toBe(1);

    // Acknowledging shows the NEW rules; the old offer is dropped; the next offer binds the NEW digest.
    const after = withOffer(
      apply(s, { type: 'acknowledge', requestRef: REF }),
      { revision: 2, proposal_digest_sha256: DIGEST2 },
    );
    expect(after.views[REF].stale).toBe(false);
    expect(after.views[REF].displayed.proposal?.proof.contract?.maxAttempts).toBe(64);
    const fields = decisionFields(after.views[REF], 'approve');
    expect(fields).toMatchObject({ revision: 2, digest: DIGEST2 });
  });

  it('SYNTHETIC version skew: different rules under an UNCHANGED binding also invalidate the display', () => {
    // A panel that this window disagrees with: the binding (revision, digest) did not move but the rules
    // did. The person must not be moved onto them silently.
    const before = stateWith(proposal());
    const s = apply(
      before,
      { type: 'sent', seq: 3, op: 'request', requestRef: REF, turnId: null },
      {
        type: 'reply',
        nowMs: 1200,
        reply: reply(
          'request',
          3,
          'ok',
          wireRequest({
            proposal: proposal({
              proof_contract: contract({ degradation_policy: { permitted_blocker_keys: ['everything'] } }),
            }),
          }),
        ),
      },
    );
    expect(s.views[REF].stale).toBe(true);
    expect(s.views[REF].displayed.proposal?.proof.contract?.permittedBlockers).toEqual(['flaky_network']);
  });

  it('a read that carries NO body under the same binding is not a change: the body the person was shown stays', () => {
    // The panel stops sending a body for a revision it no longer holds (approved, after being revised outside
    // the panel). The unchanged digest still names what was shown; the display must neither go stale nor lose it.
    const before = stateWith(proposal());
    const s = apply(
      before,
      { type: 'sent', seq: 3, op: 'request', requestRef: REF, turnId: null },
      {
        type: 'reply',
        nowMs: 1200,
        reply: reply(
          'request',
          3,
          'ok',
          wireRequest({
            proposal: null,
            lifecycle: lifecycle({ stage: 'approved', label: 'Approved', actions: ['dispatch'] }),
          }),
        ),
      },
    );
    const view = s.views[REF];
    expect(view.stale).toBe(false);
    expect(view.displayed.lifecycle.stage).toBe('approved');
    expect(view.displayed.proposal?.proof.contract?.maxAttempts).toBe(3);
    expect(view.latest.proposal).toBeNull();
  });

  it('a read whose rules became unrepresented invalidates the display too, and approval stays refused after acknowledging', () => {
    const before = stateWith(proposal());
    const odd = contract() as Loose;
    odd.continuation_budget.surprise = true;
    let s = apply(
      before,
      { type: 'sent', seq: 3, op: 'request', requestRef: REF, turnId: null },
      {
        type: 'reply',
        nowMs: 1200,
        reply: reply('request', 3, 'ok', wireRequest({ proposal: proposal({ proof_contract: odd }) })),
      },
    );
    expect(s.views[REF].stale).toBe(true);
    s = withOffer(apply(s, { type: 'acknowledge', requestRef: REF }));
    expect(decisionFields(s.views[REF], 'approve')).toBeNull();
    expect(decisionBlockedWords(s.views[REF])).toContain('surprise');
  });
});

// ---------------------------------------------------------------------------------------------
// Round 27, F2 — the panel says what it left out of a conversation, and the window says it too.
// ---------------------------------------------------------------------------------------------

describe('F2: a conversation the panel cut is never shown as if it were whole', () => {
  const turn = (sequence: number): Record<string, unknown> => ({
    turn_id: `tn-${String(sequence).padStart(32, '0')}`,
    conversation_id: `cv-${'1'.repeat(32)}`,
    sequence,
    state: 'COMPLETED',
    user_text: `message ${sequence}`,
    assistant_text: `answer ${sequence}`,
    draft: null,
    outcome: null,
    created_at: 100 + sequence,
    linked_request_ref: null,
  });
  const wireConversation = (over: Record<string, unknown> = {}): Record<string, unknown> => ({
    ok: true,
    conversation_id: `cv-${'1'.repeat(32)}`,
    turns_total: 50,
    turns_shown: 3,
    turns_omitted: 47,
    turns_truncated: true,
    turns_omitted_by_turn_limit: 0,
    turns_omitted_by_byte_limit: 47,
    view_limits: { turns: 50, bytes: 3 * 1024 * 1024 },
    turns: [turn(48), turn(49), turn(50)],
    ...over,
  });

  it('parses how many turns the record holds, how many were left out, and the panel\'s own limits', () => {
    const parsed = parseConversation(wireConversation())!;
    expect(parsed.total).toBe(50);
    expect(parsed.omitted).toBe(47);
    expect(parsed.limits).toEqual({ turns: 50, bytes: 3 * 1024 * 1024 });
    expect(parsed.split).toEqual({ byTurns: 0, byBytes: 47 });
    expect(parsed.turns.map((t) => t.sequence)).toEqual([48, 49, 50]);
  });

  it('never trusts a silence: turns missing from what was shown count as omitted even if the panel did not say', () => {
    const parsed = parseConversation(
      wireConversation({ turns_omitted: undefined, turns_truncated: false, view_limits: undefined }),
    )!;
    expect(parsed.omitted).toBe(47);
    expect(parsed.limits).toBeNull();
  });

  it('says it, in one sentence: how many are shown of how many, that the rest are kept, and the bound', () => {
    const words = omittedWords({ ...EMPTY_CONVERSATION, id: 'x', ...parseConversation(wireConversation())!, })!;
    expect(words).toContain('Showing the most recent 3 of 50 turns');
    expect(words).toContain('47 earlier turns are kept in the record and not shown here');
    // The byte budget decided, and the sentence says so with the real count.
    expect(words).toContain('the turns shown already reach the about 3 MB of text this window receives at once');
    expect(words).not.toContain('whichever');
    // The turn bound alone.
    const byTurns = omittedWords({
      ...EMPTY_CONVERSATION,
      id: 'x',
      ...parseConversation(
        wireConversation({ turns_total: 60, turns_omitted: 57, turns_omitted_by_turn_limit: 57, turns_omitted_by_byte_limit: 0 }),
      )!,
    })!;
    expect(byTurns).toContain('this window receives at most 50 turns at a time');
    // Both, each counted.
    const both = omittedWords({
      ...EMPTY_CONVERSATION,
      id: 'x',
      ...parseConversation(
        wireConversation({ turns_total: 55, turns_omitted: 52, turns_omitted_by_turn_limit: 5, turns_omitted_by_byte_limit: 47 }),
      )!,
    })!;
    expect(both).toContain('5 because this window receives at most 50 turns, and 47 more because the turns shown already reach');
    // A panel that gives no split, or one that does not add up, is not given a reason it did not state.
    for (const over of [
      { turns_omitted_by_turn_limit: undefined, turns_omitted_by_byte_limit: undefined },
      { turns_omitted_by_turn_limit: 1, turns_omitted_by_byte_limit: 1 },
    ]) {
      const unknownWhy = omittedWords({
        ...EMPTY_CONVERSATION,
        id: 'x',
        ...parseConversation(wireConversation(over))!,
      })!;
      expect(unknownWhy).toContain('whichever is reached first');
    }
    expect(
      omittedWords({
        ...EMPTY_CONVERSATION,
        id: 'x',
        total: 4,
        omitted: 1,
        turns: parseConversation(wireConversation({ turns: [turn(2), turn(3), turn(4)] }))!.turns,
      }),
    ).toContain('1 earlier turn is kept in the record');
  });

  it('says nothing when nothing was left out', () => {
    const parsed = parseConversation(
      wireConversation({ turns_total: 3, turns_omitted: 0, turns_truncated: false }),
    )!;
    expect(parsed.omitted).toBe(0);
    expect(omittedWords({ ...EMPTY_CONVERSATION, id: 'x', ...parsed })).toBeNull();
  });

  it('reaches the state through a read, and a new turn keeps the omission it was shown with', () => {
    let s = apply(
      INITIAL_STATE,
      { type: 'openConversation', conversationId: `cv-${'1'.repeat(32)}` },
      { type: 'sent', seq: 1, op: 'conversation', requestRef: null, turnId: null },
      { type: 'reply', nowMs: 1000, reply: reply('conversation', 1, 'ok', wireConversation()) },
    );
    expect(s.conversation).toMatchObject({ total: 50, omitted: 47 });
    expect(omittedWords(s.conversation)).toContain('Showing the most recent 3 of 50 turns');
    s = apply(s, {
      type: 'reply',
      nowMs: 1100,
      reply: reply('turn', 2, 'ok', { turn: turn(51) }),
    });
    expect(s.conversation.omitted).toBe(47);
    expect(s.conversation.total).toBe(51);
  });

  it('words the panel\'s oversize refusal plainly', () => {
    expect(problemWords('mission_panel_response_too_large', 'ignored')).toContain('refused instead of being cut');
  });
});


// ---------------------------------------------------------------------------------------------
// Startup diagnostic delta — why an in-doubt Start is in doubt (parsed under a closed shape, said honestly).
// ---------------------------------------------------------------------------------------------

const boundCause = (over: Record<string, unknown> = {}): Record<string, unknown> => ({
  state: 'BOUND',
  kind: 'BINDINGS_NOT_ESTABLISHED',
  stage: 'establish_role_bindings',
  failure_class: 'BindingsNotEstablished',
  roles: [
    { logical: 'lead1', session: 'ABSENT', reason: 'the recorded binding carries no session' },
    { logical: 'supervisor', session: 'UNKNOWN', reason: 'Herdr returned no readable record (status missing)' },
  ],
  roles_total: 2,
  roles_truncated: false,
  reference: 'startup-binding-failure-20261020T123532Z-6f2a3820.json',
  ...over,
});

describe('start cause: read under a closed shape or not shown', () => {
  it('reads a BOUND cause whole', () => {
    const cause = parseStartCause(boundCause())!;
    expect(cause).toMatchObject({
      state: 'BOUND',
      kind: 'BINDINGS_NOT_ESTABLISHED',
      stage: 'establish_role_bindings',
      failureClass: 'BindingsNotEstablished',
      rolesTotal: 2,
      rolesTruncated: false,
      reference: 'startup-binding-failure-20261020T123532Z-6f2a3820.json',
    });
    expect(cause.roles).toEqual([
      { logical: 'lead1', session: 'ABSENT', reason: 'the recorded binding carries no session' },
      { logical: 'supervisor', session: 'UNKNOWN', reason: 'Herdr returned no readable record (status missing)' },
    ]);
  });

  it('reads the two fallbacks, which carry only a count', () => {
    expect(parseStartCause({ state: 'NONE', rejected: 3 })).toMatchObject({ state: 'NONE', rejected: 3, roles: [] });
    expect(parseStartCause({ state: 'AMBIGUOUS', candidates: 2 })).toMatchObject({
      state: 'AMBIGUOUS',
      candidates: 2,
    });
  });

  it('shows nothing, rather than part of a cause, for anything it cannot account for', () => {
    const bad: Array<[string, unknown]> = [
      ['not an object', 'BOUND'],
      ['a list', []],
      ['an unknown state', { state: 'PROBABLY' }],
      ['NONE without a count', { state: 'NONE' }],
      ['NONE with a negative count', { state: 'NONE', rejected: -1 }],
      ['AMBIGUOUS with a text count', { state: 'AMBIGUOUS', candidates: '2' }],
      ['an unknown kind', boundCause({ kind: 'SOMETHING_ELSE' })],
      ['no reference', boundCause({ reference: undefined })],
      ['no stage', boundCause({ stage: undefined })],
      ['a role with a made-up session', boundCause({ roles: [{ logical: 'a', session: 'MAYBE', reason: 'r' }] })],
      ['a role that is not an object', boundCause({ roles: ['lead1'] })],
      ['roles that are not a list', boundCause({ roles: 'lead1' })],
      ['a total of the wrong kind', boundCause({ roles_total: '2' })],
      ['truncated of the wrong kind', boundCause({ roles_truncated: 'no' })],
    ];
    for (const [why, value] of bad) expect(parseStartCause(value), why).toBeNull();
  });

  it('caps what it will draw: roles, and every text field', () => {
    const many = Array.from({ length: 40 }, (_, i) => ({
      logical: `role${i}`,
      session: 'ABSENT',
      reason: 'R'.repeat(5000),
    }));
    const cause = parseStartCause(boundCause({ roles: many, roles_total: 40, stage: 'S'.repeat(5000) }))!;
    expect(cause.roles).toHaveLength(MR_START_CAUSE_MAX_ROLES);
    expect(cause.rolesTruncated).toBe(true);
    expect(cause.rolesTotal).toBe(40);
    for (const role of cause.roles) expect(role.reason.length).toBeLessThanOrEqual(MR_START_CAUSE_MAX_CHARS);
    expect(cause.stage!.length).toBeLessThanOrEqual(MR_START_CAUSE_MAX_CHARS);
  });
});

describe('start cause: the words', () => {
  it('a BOUND binding failure names the stage, every affected role, the file and the no-retry note', () => {
    const words = startCauseWords(parseStartCause(boundCause())!);
    const text = [words.heading, ...words.lines, words.note].join('\n');
    expect(words.heading).toBe('Why this Start is in doubt');
    expect(text).toContain("binding the team's roles to their live sessions");
    expect(text).toContain('2 roles could not be bound to a live session within the settle window');
    expect(text).toContain('lead1: the recorded binding carries no session (Herdr reported no session id for it)');
    expect(text).toContain('supervisor: Herdr returned no readable record (status missing) (no readable record was returned for it)');
    expect(text).toContain('Diagnostic file: startup-binding-failure-20261020T123532Z-6f2a3820.json');
    expect(text).toContain('this window cannot open it');
    expect(words.note).toBe(START_CAUSE_NOTE);
    expect(words.note).toContain('a retry is not implied to be safe');
    expect(text).not.toMatch(/safe to retry|you can retry|try again/i);
  });

  it('a CLOSED run is described as a Start that never confirmed, with none of the in-doubt stage\u2019s words', () => {
    const bound = startCauseWords(parseStartCause(boundCause())!, true);
    expect(bound.heading).toBe('Why this Start never confirmed');
    expect(bound.note).toBe(START_CAUSE_CLOSED_NOTE);
    expect(bound.note).toContain('closed and cannot be started again');
    const text = [bound.heading, ...bound.lines, bound.note].join('\n');
    expect(text).not.toMatch(/in doubt|Reconcile|retry/i);
    for (const state of [{ state: 'NONE', rejected: 0 }, { state: 'AMBIGUOUS', candidates: 2 }]) {
      const words = startCauseWords(parseStartCause(state)!, true);
      expect(words.heading).toBe('Why this Start never confirmed: not known');
      expect(words.note).toBe(START_CAUSE_CLOSED_NOTE);
    }
    // ... and the same cause on a run still in doubt keeps the in-doubt words
    expect(startCauseWords(parseStartCause(boundCause())!, false).heading).toBe('Why this Start is in doubt');
  });

  it('says so when roles were cut from the list', () => {
    const cause = parseStartCause(boundCause({ roles_total: 30, roles_truncated: true }))!;
    expect(startCauseWords(cause).lines.join('\n')).toContain('Showing 2 of 30 roles.');
  });

  it('an unknown kind of failure names only its class and says its message is not recorded', () => {
    const cause = parseStartCause(
      boundCause({ kind: 'UNKNOWN', failure_class: 'RuntimeError', roles: [], roles_total: 0 }),
    )!;
    const text = startCauseWords(cause).lines.join('\n');
    expect(text).toContain('a kind this diagnostic cannot describe (RuntimeError)');
    expect(text).toContain('its message is deliberately not recorded');
    expect(text).not.toContain('could not be bound');
  });

  it('NONE and AMBIGUOUS say the cause is NOT known, never a guess', () => {
    const none = startCauseWords(parseStartCause({ state: 'NONE', rejected: 0 })!);
    expect(none.heading).toBe('Why this Start is in doubt: not known');
    expect(none.lines.join(' ')).toBe('No retained startup diagnostic is bound to this attempt.');
    const rejected = startCauseWords(parseStartCause({ state: 'NONE', rejected: 1 })!);
    expect(rejected.lines.join(' ')).toContain('(1 retained file examined did not match this attempt)');
    const ambiguous = startCauseWords(parseStartCause({ state: 'AMBIGUOUS', candidates: 2 })!);
    expect(ambiguous.lines.join(' ')).toContain('2 retained startup diagnostics claim this attempt');
    expect(ambiguous.lines.join(' ')).toContain('so none of them is used');
    expect(ambiguous.heading).toContain('not known');
  });
});

describe('start cause: carried on the request and the live display', () => {
  it('parseRequest carries it, and is null without it or with something unaccountable', () => {
    expect(parseRequest(wireRequest({ start_cause: boundCause() }))?.startCause?.state).toBe('BOUND');
    expect(parseRequest(wireRequest())?.startCause).toBeNull();
    expect(parseRequest(wireRequest({ start_cause: { state: 'PROBABLY' } }))?.startCause).toBeNull();
    expect(parseRequest(wireRequest({ start_cause: null }))?.startCause).toBeNull();
  });

  it('a later read of the same proposal brings the cause onto the live display without invalidating it', () => {
    const before = stateWithView();
    expect(before.views[REF].displayed.startCause).toBeNull();
    const s = apply(
      before,
      { type: 'sent', seq: 2, op: 'request', requestRef: REF, turnId: null },
      {
        type: 'reply',
        nowMs: 2000,
        reply: reply(
          'request',
          2,
          'ok',
          wireRequest({
            start_cause: boundCause(),
            lifecycle: lifecycle({ stage: 'dispatch_uncertain', label: 'Start outcome unknown', actions: ['reconcile'] }),
          }),
        ),
      },
    );
    expect(s.views[REF].stale).toBe(false);
    expect(s.views[REF].displayed.startCause?.state).toBe('BOUND');
    expect(s.views[REF].displayed.lifecycle.stage).toBe('dispatch_uncertain');
  });
});
