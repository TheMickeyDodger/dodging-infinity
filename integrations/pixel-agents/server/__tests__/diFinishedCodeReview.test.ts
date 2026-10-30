/**
 * I3 — the live code review of a FINISHED (COMPLETED) Mission, office side.
 *
 * The listing facts are the backend's EMITTED ones: `artifact-content-historical-v1.FIXTURE.json` is a COMPLETED Mission built through Mission Core's
 * normal APIs (hash-pinned; FIXTURE evidence only). The backend's overview BODY for the finished Mission is the exported active-assignment overview
 * (`code-review-v1.FIXTURE.json`) with ONLY its two echo fields (mission_ref, task_id) substituted: TEST DATA that exercises the office's re-check of
 * an answer for a Mission-level reference, never evidence that the backend read a finished Mission's real worktree.
 *
 * Proves: the Mission-level offer is decided from typed listing facts only (a Mission Core record, terminal standing, the historical execution
 * ESTABLISHED, a task, a cursor) and never from the lifecycle literal; its listing token is its own (it cannot be a member's); the Mission-level
 * reference is a DISTINCT kind (`diCodeReviewMissionRead`, no member) admitted against the CURRENT listing; the member-bound reference is
 * untouched; the four finished reasons are closed codes the office relays as the backend's; and a Mission-level read answers only the asking
 * connection, with the member field empty and no new answer key.
 */
import * as crypto from 'crypto';
import * as fs from 'fs';
import * as path from 'path';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { AgentStateStore } from '../src/agentStateStore.js';
import type { BackendResult } from '../src/di/backendClient.js';
import type { DiscoveryResult } from '../src/di/backendDiscovery.js';
import { parseEnvelope } from '../src/di/catalogProjection.js';
import type { CodeReviewRef } from '../src/di/codeReview.js';
import {
  admitAgainstView,
  backendQuery,
  CODE_REVIEW_PROBLEMS,
  listingToken,
  missionListingToken,
  missionReviewOfferFor,
  parseCodeReviewRead,
  parseMissionReviewRead,
  REVIEW_UNAVAILABLE_REASONS,
  reviewOfferFor,
} from '../src/di/codeReview.js';
import { DI_HANDLED_MESSAGE_TYPES, handleDiClientMessage } from '../src/di/diClientMessages.js';
import { DiOffice } from '../src/di/diOffice.js';
import { buildDiOfficeStateMessage } from '../src/di/officeStateMessage.js';
import type { MissionPresentation, OfficeView } from '../src/di/officeView.js';
import { buildOfficeView } from '../src/di/officeView.js';
import type { ServeTimeVerdict } from '../src/di/serveTimeVerdict.js';
import { clone } from './helpers/diFixture.js';

type Raw = Record<string, unknown>;
const FRESH: ServeTimeVerdict = { health: { known: true, value: 'fresh' }, problem: null };
const FIXTURES = path.join(__dirname, 'fixtures/di-backend/v2');
const HISTORICAL = path.join(FIXTURES, 'artifact-content-historical-v1.FIXTURE.json');
const HISTORICAL_SHA256 = '789533593eb063068f4407c635df45647b7cadf698c013f95ea4870b0aadecfe';
const ACTIVE = path.join(FIXTURES, 'code-review-v1.FIXTURE.json');
const ACTIVE_SHA256 = 'ad29c7dcf1b8b3d7bc764db9ac9f0717efacbed2c98fb98e2786170b8325ab25';

function pinned(file: string, sha: string): Raw {
  const bytes = fs.readFileSync(file);
  const digest = crypto.createHash('sha256').update(bytes).digest('hex');
  if (digest !== sha) throw new Error(`FIXTURE hash mismatch: ${digest}`);
  return JSON.parse(bytes.toString('utf-8')) as Raw;
}

const catalogOf = (raw: Raw): Raw => raw.catalog_answer as Raw;
const generatedAt = (answer: Raw): number => Number((answer.response as Raw).generated_at);
const canonicalOf = (answer: Raw): Raw =>
  ((answer.response as Raw).missions as Raw[]).find((m) => (m.lifecycle as Raw).model !== 'herdr.task/1')!;

function viewOf(answer: Raw, now = generatedAt(answer)): OfficeView {
  const parsed = parseEnvelope(answer);
  if (!parsed.ok) throw new Error(`envelope refused: ${JSON.stringify(parsed.verdict)}`);
  return buildOfficeView(parsed.envelope, FRESH, now, 2);
}

/** The COMPLETED Mission as the office lists it, with the raw answer it came from. */
function finished() {
  const answer = clone(catalogOf(pinned(HISTORICAL, HISTORICAL_SHA256)));
  const view = viewOf(answer);
  if (view.missions.state !== 'observed') throw new Error('missions not observed');
  const ref = canonicalOf(answer).mission_ref as string;
  const mission = view.missions.value.find((m) => m.missionRef === ref)!;
  return { answer, view, mission };
}

/** The active, bound Mission of the active-review fixture (its member is offered a member-bound review). */
function active() {
  const answer = clone(catalogOf(pinned(ACTIVE, ACTIVE_SHA256)));
  const view = viewOf(answer);
  if (view.missions.state !== 'observed') throw new Error('missions not observed');
  const mission = view.missions.value[0];
  if (mission.members.state !== 'observed') throw new Error('members not observed');
  return { answer, view, mission, member: mission.members.value[0] };
}

/** A view whose one Mission is `mission` (an edited copy; the facts are a typed presentation, never a name). */
const viewWith = (view: OfficeView, mission: MissionPresentation): OfficeView => ({
  ...view,
  missions: { state: 'observed', value: [mission] } as OfficeView['missions'],
});

const withExecution = (mission: MissionPresentation, edit: Partial<NonNullable<MissionPresentation['evidence']>['execution']>): MissionPresentation => ({
  ...mission,
  evidence: { ...mission.evidence!, execution: { ...mission.evidence!.execution, ...edit } },
});

function missionReference(mission: MissionPresentation, extra: Partial<CodeReviewRef> = {}): CodeReviewRef {
  const offer = missionReviewOfferFor(mission);
  if (offer.state !== 'offered') throw new Error(`not offered: ${offer.reason}`);
  return { missionRef: mission.missionRef, memberRef: '', taskId: offer.taskId, listing: offer.listing, level: 'mission', ...extra };
}

/** The message the client sends for a Mission-level reference. */
const messageOf = (ref: CodeReviewRef): Raw => ({
  type: 'diCodeReviewMissionRead',
  missionRef: ref.missionRef,
  taskId: ref.taskId,
  listing: ref.listing,
  ...(ref.snapshot === undefined ? {} : { snapshot: ref.snapshot, fileId: ref.fileId }),
  ...(ref.requestId === undefined ? {} : { requestId: ref.requestId }),
});

const FINISHED_CODES = [
  'finished_execution_not_established',
  'finished_ownership_unverifiable',
  'finished_ownership_contested',
  'finished_workspace_not_corroborated',
];

describe('the Mission-level offer: decided from typed listing facts only', () => {
  it('is OFFERED for the emitted COMPLETED Mission, with its own task and a 64-hex listing token', () => {
    const { mission } = finished();
    const offer = missionReviewOfferFor(mission);
    expect(offer.state).toBe('offered');
    if (offer.state !== 'offered') return;
    expect(offer.reason).toBeNull();
    expect(offer.taskId).toBe(mission.evidence!.execution.taskId);
    expect(offer.taskId).toBe((canonicalOf(finished().answer).execution as Raw).task_id);
    expect(offer.listing).toMatch(/^[0-9a-f]{64}$/);
  });

  it('is unavailable, each for its own reason, and every reason is in the closed list', () => {
    const { mission } = finished();
    const cases: [string, MissionPresentation, string][] = [
      ['a plain Herdr task', { ...mission, sourceKind: 'herdr_task' }, 'not_a_mission_core_record'],
      ['an office without the v2 blocks', { ...mission, v2: null, evidence: null }, 'contract_version_1_office'],
      ['work in progress (standing active)', { ...mission, standing: 'active' }, 'assignment_not_finished'],
      ['a standing the office does not know', { ...mission, standingKnown: false }, 'assignment_not_finished'],
      ['no historical execution listed', withExecution(mission, { basis: 'none' }), 'finished_execution_not_listed'],
      ['the active basis', withExecution(mission, { basis: 'active_binding' }), 'finished_execution_not_listed'],
      ['an identity that is not established', withExecution(mission, { identityState: 'not_established' }), 'finished_execution_not_listed'],
      ['no evidence library at all', { ...mission, evidence: null }, 'finished_execution_not_listed'],
      ['no cursor', { ...mission, evidence: { ...mission.evidence!, cursor: null } }, 'finished_execution_not_listed'],
      // the office is not live: the listing is stale (the evidence library says so), so nothing it lists is offered
      ['a stale listing', { ...mission, evidence: { ...mission.evidence!, currency: 'stale', currencyReason: 'office_not_live' } }, 'finished_execution_not_listed'],
      ['no task', withExecution(mission, { taskId: null }), 'task_not_observed'],
    ];
    for (const [name, edited, reason] of cases) {
      const offer = missionReviewOfferFor(edited);
      expect(offer, name).toMatchObject({ state: 'unavailable', reason, listing: null });
      expect(REVIEW_UNAVAILABLE_REASONS as readonly string[], name).toContain(reason);
    }
  });

  it('never switches on the lifecycle literal: the same facts under any literal give the same offer', () => {
    const { mission } = finished();
    for (const literal of ['COMPLETED', 'CANCELLED', 'whatever-the-record-says']) {
      expect(missionReviewOfferFor({ ...mission, lifecycleState: literal }), literal).toEqual(missionReviewOfferFor(mission));
    }
  });

  it('is independent of the member-bound offer: an active Mission is offered per member and NOT at Mission level', () => {
    const { mission, member } = active();
    expect(reviewOfferFor(mission, member).state).toBe('offered');
    expect(missionReviewOfferFor(mission)).toMatchObject({ state: 'unavailable', reason: 'assignment_not_finished' });
  });
});

describe('the Mission-level listing token', () => {
  it('moves with what the client was shown and with nothing else', () => {
    const { mission } = finished();
    const task = mission.evidence!.execution.taskId!;
    const cursor = mission.evidence!.cursor!;
    const base = missionListingToken(mission, task, cursor);
    expect(base).toMatch(/^[0-9a-f]{64}$/);
    expect(missionListingToken(clone(mission) as MissionPresentation, task, cursor)).toBe(base);
    expect(missionListingToken(mission, task + 'x', cursor)).not.toBe(base);
    expect(missionListingToken(mission, task, cursor.replace(/.$/, (c) => (c === '0' ? '1' : '0')))).not.toBe(base);
    expect(missionListingToken({ ...mission, sourceId: mission.sourceId + 'x' }, task, cursor)).not.toBe(base);
    expect(missionListingToken({ ...mission, nativeId: mission.nativeId + 'x' }, task, cursor)).not.toBe(base);
    expect(missionListingToken({ ...mission, standing: 'active' }, task, cursor)).not.toBe(base);
  });

  it('can never equal a member-bound token for the same Mission facts', () => {
    const { mission, member } = active();
    const task = member.taskId!;
    expect(missionListingToken(mission, task, 'c'.repeat(64))).not.toBe(listingToken(mission, member));
  });
});

describe('the reference: a DISTINCT kind, exactly', () => {
  const ref = () => missionReference(finished().mission);

  it('parses exactly four keys (the overview) or six (one file), plus the one optional requestId', () => {
    const r = ref();
    expect(parseMissionReviewRead(messageOf(r))).toEqual({ missionRef: r.missionRef, memberRef: '', taskId: r.taskId, listing: r.listing, level: 'mission' });
    const file = { ...messageOf(r), snapshot: 'a'.repeat(64), fileId: 'rf-' + 'b'.repeat(32) };
    expect(parseMissionReviewRead(file)).toMatchObject({ snapshot: 'a'.repeat(64), fileId: 'rf-' + 'b'.repeat(32), level: 'mission' });
    expect(parseMissionReviewRead({ ...messageOf(r), requestId: 'w.3' })).toMatchObject({ requestId: 'w.3' });
  });

  it('refuses anything else unread', () => {
    const base = messageOf(ref());
    const bad: [string, Raw][] = [
      ['a member', { ...base, memberRef: 'x' }],
      ['an empty member', { ...base, memberRef: '' }],
      ['a path', { ...base, path: '/' }],
      ['no listing', (({ listing: _l, ...rest }) => rest)(base)],
      ['no task', (({ taskId: _t, ...rest }) => rest)(base)],
      ['a snapshot without a file id', { ...base, snapshot: 'a'.repeat(64) }],
      ['a file id without a snapshot', { ...base, fileId: 'rf-' + 'b'.repeat(32) }],
      ['a request id that is not a token', { ...base, requestId: 'a b' }],
      ['another type', { ...base, type: 'diCodeReviewRead' }],
      ['a Mission ref that is not a ref', { ...base, missionRef: '../x' }],
      ['a listing that is not a digest', { ...base, listing: 'zz' }],
    ];
    for (const [name, message] of bad) expect(parseMissionReviewRead(message), name).toBeNull();
  });

  it('keeps the two kinds apart: neither parser accepts the other\'s message', () => {
    const { mission, member } = active();
    const memberOffer = reviewOfferFor(mission, member);
    if (memberOffer.state !== 'offered') throw new Error('not offered');
    const memberMessage = { type: 'diCodeReviewRead', missionRef: mission.missionRef, memberRef: member.memberRef, taskId: memberOffer.taskId, listing: memberOffer.listing };
    expect(parseCodeReviewRead(memberMessage)).not.toBeNull();
    expect(parseMissionReviewRead(memberMessage)).toBeNull();
    expect(parseCodeReviewRead(messageOf(ref()))).toBeNull();
    // and the member-bound parse yields no `level` key: its result is byte-identical to what it always was
    expect(Object.keys(parseCodeReviewRead(memberMessage)!).sort()).toEqual(['listing', 'memberRef', 'missionRef', 'taskId']);
  });

  it('is asked of the backend as the Mission and the task alone: no member, no level', () => {
    const r = ref();
    expect(backendQuery(r)).toEqual({ missionRef: r.missionRef, taskId: r.taskId });
    expect(backendQuery({ ...r, snapshot: 'a'.repeat(64), fileId: 'rf-' + 'b'.repeat(32) })).toEqual({
      missionRef: r.missionRef,
      taskId: r.taskId,
      snapshot: 'a'.repeat(64),
      fileId: 'rf-' + 'b'.repeat(32),
    });
  });
});

describe('admission against the CURRENT listing', () => {
  it('admits the offered Mission-level reference', () => {
    const { view, mission } = finished();
    expect(admitAgainstView(view, missionReference(mission))).toEqual({ ok: true });
  });

  it('refuses each way the listing no longer supports it, with the office\'s own code', () => {
    const { view, mission } = finished();
    const ref = missionReference(mission);
    expect(admitAgainstView(view, { ...ref, missionRef: ref.missionRef.replace(/.$/, (c) => (c === '0' ? '1' : '0')) })).toMatchObject({ ok: false, problem: 'mission_not_listed' });
    expect(admitAgainstView(view, { ...ref, taskId: ref.taskId + 'x' })).toMatchObject({ ok: false, problem: 'task_not_the_one_listed' });
    expect(admitAgainstView(view, { ...ref, listing: '0'.repeat(64) })).toMatchObject({ ok: false, problem: 'listing_moved' });
    const moved = withExecution(mission, { identityState: 'not_established', basis: 'none' });
    expect(admitAgainstView(viewWith(view, moved), ref)).toMatchObject({ ok: false, problem: 'review_not_offered' });
    const resumed = { ...mission, standing: 'active' };
    expect(admitAgainstView(viewWith(view, resumed), ref)).toMatchObject({ ok: false, problem: 'review_not_offered' });
    const twice = { ...view, missions: { state: 'observed', value: [mission, mission] } } as OfficeView;
    expect(admitAgainstView(twice, ref)).toMatchObject({ ok: false, problem: 'mission_listed_more_than_once' });
    const none = { ...view, missions: { state: 'unavailable', reason: 'x' } } as unknown as OfficeView;
    expect(admitAgainstView(none, ref)).toMatchObject({ ok: false, problem: 'review_not_offered' });
  });

  it('a Mission-level reference is not admitted for an ACTIVE Mission, and a member-bound one is not admitted for a finished Mission', () => {
    const live = active();
    const fake: CodeReviewRef = { missionRef: live.mission.missionRef, memberRef: '', taskId: live.member.taskId!, listing: '0'.repeat(64), level: 'mission' };
    expect(admitAgainstView(live.view, fake)).toMatchObject({ ok: false, problem: 'review_not_offered' });
    const { view, mission } = finished();
    const member: CodeReviewRef = { missionRef: mission.missionRef, memberRef: 'any-member', taskId: mission.evidence!.execution.taskId!, listing: '0'.repeat(64) };
    expect(admitAgainstView(view, member).ok).toBe(false);
  });

  it('the member-bound admission is unchanged for the active Mission', () => {
    const { view, mission, member } = active();
    const offer = reviewOfferFor(mission, member);
    if (offer.state !== 'offered') throw new Error('not offered');
    const ref: CodeReviewRef = { missionRef: mission.missionRef, memberRef: member.memberRef, taskId: offer.taskId, listing: offer.listing };
    expect(admitAgainstView(view, ref)).toEqual({ ok: true });
    expect(admitAgainstView(view, { ...ref, listing: '0'.repeat(64) })).toMatchObject({ ok: false, problem: 'listing_moved' });
  });
});

describe('the office state carries the Mission-level offer on the Mission, and nothing new on the member', () => {
  it('lists it for the finished Mission, and as "not finished" for an active one', () => {
    const done = buildDiOfficeStateMessage(finished().view, () => 1).missions.missions;
    const mission = done.find((m) => m.sourceKind === 'mission_core')!;
    expect(mission.codeReview).toEqual({
      state: 'offered',
      reason: null,
      taskId: finished().mission.evidence!.execution.taskId,
      listing: missionReference(finished().mission).listing,
    });
    const live = buildDiOfficeStateMessage(active().view, () => 1).missions.missions[0];
    expect(live.codeReview).toMatchObject({ state: 'unavailable', reason: 'assignment_not_finished', listing: null });
    // the member's own offer keeps its four keys
    expect(Object.keys(live.members.members[0].codeReview!).sort()).toEqual(['listing', 'reason', 'state', 'taskId']);
  });
});

describe('the client message: handled, and refused unless exactly a reference', () => {
  it('is a handled type; a malformed one is refused before the reader is reached', () => {
    expect(DI_HANDLED_MESSAGE_TYPES as readonly string[]).toContain('diCodeReviewMissionRead');
    const calls: Raw[] = [];
    const ctx = {
      store: new AgentStateStore(),
      cache: {} as never,
      extensionVersion: 'test',
      readCodeReview: (m: Raw) => calls.push(m),
    };
    const send = vi.fn();
    const good = messageOf(missionReference(finished().mission));
    expect(handleDiClientMessage(good, send, ctx as never)).toBe('handled');
    expect(calls).toHaveLength(1);
    for (const bad of [{ ...good, memberRef: 'x' }, { ...good, path: '/' }, { type: 'diCodeReviewMissionRead' }]) {
      expect(handleDiClientMessage(bad as Raw, send, ctx as never)).toMatchObject({ refused: expect.stringContaining('admitted reference') });
    }
    expect(calls).toHaveLength(1);
    expect(handleDiClientMessage(good, send, { ...ctx, readCodeReview: undefined } as never)).toMatchObject({
      refused: expect.stringContaining('no code review reader'),
    });
    expect(send).not.toHaveBeenCalled();
  });
});

describe('the four finished reasons are closed codes', () => {
  it('end the list, in the backend\'s order, with no duplicate', () => {
    expect((CODE_REVIEW_PROBLEMS as readonly string[]).slice(-4)).toEqual(FINISHED_CODES);
    expect(new Set(CODE_REVIEW_PROBLEMS).size).toBe(CODE_REVIEW_PROBLEMS.length);
    expect(CODE_REVIEW_PROBLEMS.length).toBe(34);
  });
});

describe('DiOffice.readCodeReview for a Mission-level reference', () => {
  const ENDPOINT: DiscoveryResult = { ok: true, endpoint: { port: 1, token: 'secret-not-logged' } };
  let clockMs = 0;
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  const http200 = (body: unknown): BackendResult => ({ kind: 'http', status: 200, headers: {}, body, bodyProblem: null });

  async function office(requestReview: (endpoint: unknown, q: unknown) => Promise<BackendResult>, answer: Raw) {
    clockMs = generatedAt(answer) * 1000;
    const o = new DiOffice({
      store: new AgentStateStore(),
      discoveryPath: '/sandbox/backend-discovery.json',
      contractVersion: 2,
      deps: {
        readDiscovery: () => ENDPOINT,
        request: async () => ({ kind: 'http', status: 200, headers: { 'x-di-freshness-health': 'fresh' }, body: clone(answer), bodyProblem: null }),
        nowMs: () => clockMs,
        log: () => undefined,
        requestReview: vi.fn(requestReview),
      },
    });
    await o.poll();
    return o;
  }

  /** The exported active overview with ONLY its two echo fields substituted: test data for the re-check of a Mission-level answer. */
  function overviewFor(ref: CodeReviewRef): Raw {
    const body = clone(pinned(ACTIVE, ACTIVE_SHA256).overview as Raw);
    body.mission_ref = ref.missionRef;
    body.task_id = ref.taskId;
    return body;
  }

  it('asks the backend the Mission and the task alone, and relays the overview with an EMPTY member field and no new key', async () => {
    const { answer, mission } = finished();
    const ref = missionReference(mission, { requestId: 'w.1' });
    const backend = vi.fn(async (_e: unknown, _q: unknown) => http200(overviewFor(ref)));
    const o = await office(backend, answer);
    const sent: Raw[] = [];
    await o.readCodeReview(messageOf(ref), (m) => sent.push(m));
    expect(backend.mock.calls[0][1]).toEqual({ missionRef: ref.missionRef, taskId: ref.taskId });
    expect(sent).toHaveLength(1);
    expect(sent[0]).toMatchObject({ type: 'diCodeReview', state: 'observed', decidedBy: 'backend', memberRef: '', missionRef: ref.missionRef, taskId: ref.taskId, listing: ref.listing, requestId: 'w.1' });
    // the answer has exactly the keys an answer to a member-bound read has (that one named no read, so it has no requestId): the member field is
    // present and empty, and the answer gained no key
    const memberAnswerKeys = Object.keys(await memberAnswer()).sort();
    expect(Object.keys(sent[0]).filter((k) => k !== 'requestId').sort()).toEqual(memberAnswerKeys);
    o.stop();
  });

  /** The answer the office gives a MEMBER-bound read of the active fixture (the key set to compare). */
  async function memberAnswer(): Promise<Raw> {
    const live = active();
    const offer = reviewOfferFor(live.mission, live.member);
    if (offer.state !== 'offered') throw new Error('not offered');
    const message = { type: 'diCodeReviewRead', missionRef: live.mission.missionRef, memberRef: live.member.memberRef, taskId: offer.taskId, listing: offer.listing };
    const o = await office(async () => http200(clone(pinned(ACTIVE, ACTIVE_SHA256).overview as Raw)), live.answer);
    const sent: Raw[] = [];
    await o.readCodeReview(message, (m) => sent.push(m));
    o.stop();
    return sent[0];
  }

  it('refuses a Mission-level read for a Mission that is not offered, without asking the backend', async () => {
    const live = active();
    const message = messageOf({ missionRef: live.mission.missionRef, memberRef: '', taskId: live.member.taskId!, listing: '0'.repeat(64), level: 'mission' });
    const backend = vi.fn(async () => http200({}));
    const o = await office(backend, live.answer);
    const sent: Raw[] = [];
    await o.readCodeReview(message, (m) => sent.push(m));
    expect(sent[0]).toMatchObject({ state: 'refused', decidedBy: 'office', problem: 'review_not_offered', memberRef: '' });
    expect(backend).not.toHaveBeenCalled();
    o.stop();
  });

  it('relays each finished reason as the BACKEND\'s own refusal, with its own code', async () => {
    const { answer, mission } = finished();
    const base = missionReference(mission);
    for (const code of FINISHED_CODES) {
      const body = overviewFor(base);
      Object.assign(body, { state: 'refused', problem: code, detail: 'because', target: null, comparison: null, overview: null, file: null, snapshot: null, file_id: null });
      const o = await office(async () => http200(clone(body)), answer);
      const sent: Raw[] = [];
      await o.readCodeReview(messageOf(base), (m) => sent.push(m));
      expect(sent[0], code).toMatchObject({ state: 'refused', decidedBy: 'backend', problem: code, memberRef: '' });
      o.stop();
    }
  });

  it('does not accept an answer for another Mission or another task', async () => {
    const { answer, mission } = finished();
    const ref = missionReference(mission);
    for (const edit of [{ mission_ref: 'mc-1:mn-' + 'f'.repeat(32) }, { task_id: ref.taskId + 'x' }]) {
      const o = await office(async () => http200({ ...overviewFor(ref), ...edit }), answer);
      const sent: Raw[] = [];
      await o.readCodeReview(messageOf(ref), (m) => sent.push(m));
      expect(sent[0]).toMatchObject({ state: 'refused', decidedBy: 'office', problem: 'backend_answer_invalid' });
      o.stop();
    }
  });
});
