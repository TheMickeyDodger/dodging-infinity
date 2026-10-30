/**
 * Item 3 (original 20) — the live code review, office side, against the backend's EMITTED
 * answers: `code-review-v1.FIXTURE.json` is exported by tests/test_code_review_owner.py
 * (ExportForConsumerTests) from a REAL catalog owner reading a REAL git workspace of a REAL bound
 * Mission, NORMALIZED with the D1 conventions. Hash-pinned. FIXTURE evidence only — never live
 * acceptance.
 *
 * Proves: a review is offered only for a live member of the direct task of an ACTIVE, BOUND Mission
 * Core assignment; a read names an admitted reference (never a path, root, ref or git option); the
 * listing token moves with the assignment and the agent but not with the journal; every backend
 * answer is re-checked field by field and its arithmetic against itself before it is relayed; an
 * answer whose listing moved while it was read is discarded; and what the client is sent is data.
 *
 * The TypeScript bounds below are pinned by EXACT-VALUE assertion in this file. That is a
 * DIFFERENT mechanism from the Python BoundConstantPinTests (which derives only Python and cannot
 * see these); the Python side of the same bounds is pinned there.
 */
import * as crypto from 'crypto';
import * as fs from 'fs';
import * as path from 'path';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { AgentStateStore } from '../src/agentStateStore.js';
import type { BackendResult } from '../src/di/backendClient.js';
import {
  CODE_REVIEW_GRAMMAR,
  isCodeReviewQuery,
  MAX_CODE_REVIEW_RESPONSE_BYTES,
  routePath,
} from '../src/di/backendClient.js';
import type { DiscoveryResult } from '../src/di/backendDiscovery.js';
import { parseEnvelope } from '../src/di/catalogProjection.js';
import type { CodeReviewRef } from '../src/di/codeReview.js';
import * as codeReview from '../src/di/codeReview.js';
import {
  admitAgainstView,
  backendMessage,
  CODE_REVIEW_MIN_INTERVAL_MS,
  CODE_REVIEW_PROBLEMS,
  CODE_REVIEW_REQUEST_TIMEOUT_MS,
  herdrWorkspaceReviewOffer,
  isSafeReviewPath,
  listingToken,
  OFFICE_REVIEW_PROBLEMS,
  parseCodeReviewAnswer,
  parseCodeReviewRead,
  REVIEW_UNAVAILABLE_REASONS,
  reviewOfferFor,
} from '../src/di/codeReview.js';
import { DI_HANDLED_MESSAGE_TYPES, handleDiClientMessage } from '../src/di/diClientMessages.js';
import { DiOffice } from '../src/di/diOffice.js';
import { buildDiOfficeStateMessage } from '../src/di/officeStateMessage.js';
import type { MemberPresentation, MissionPresentation, OfficeView } from '../src/di/officeView.js';
import { buildOfficeView } from '../src/di/officeView.js';
import type { ServeTimeVerdict } from '../src/di/serveTimeVerdict.js';
import { clone } from './helpers/diFixture.js';

const FIXTURE = path.join(__dirname, 'fixtures/di-backend/v2/code-review-v1.FIXTURE.json');
const FIXTURE_SHA256 = 'ad29c7dcf1b8b3d7bc764db9ac9f0717efacbed2c98fb98e2786170b8325ab25';
const FRESH: ServeTimeVerdict = { health: { known: true, value: 'fresh' }, problem: null };
const REAL_ROOT =
  /(?:\/private)?\/var\/folders\/[^/<"\s]+\/[^/<"\s]+\/T\/|\/private\/tmp\/|\/Users\/|\/home\/[a-z]/;

type Raw = Record<string, unknown>;

function exported(): Raw {
  const bytes = fs.readFileSync(FIXTURE);
  const digest = crypto.createHash('sha256').update(bytes).digest('hex');
  if (digest !== FIXTURE_SHA256) throw new Error(`FIXTURE hash mismatch: ${digest}`);
  return JSON.parse(bytes.toString('utf-8')) as Raw;
}

const catalogAnswer = (raw: Raw): Raw => raw.catalog_answer as Raw;
const generatedAt = (answer: Raw): number => Number((answer.response as Raw).generated_at);
const overviewAnswer = (raw: Raw): Raw => raw.overview as Raw;
const fileAnswer = (raw: Raw, p: string): Raw => (raw.files as Raw)[p] as Raw;
const refusalAnswer = (raw: Raw, name: string): Raw => (raw.refusals as Raw)[name] as Raw;
const missionRaw = (answer: Raw): Raw => ((answer.response as Raw).missions as Raw[])[0];

function viewOf(answer: Raw, now = generatedAt(answer)): OfficeView {
  const parsed = parseEnvelope(answer);
  if (!parsed.ok) throw new Error(`envelope refused: ${JSON.stringify(parsed.verdict)}`);
  return buildOfficeView(parsed.envelope, FRESH, now, 2);
}

function theMission(view: OfficeView): MissionPresentation {
  if (view.missions.state !== 'observed') throw new Error(`missions ${view.missions.state}`);
  return view.missions.value[0];
}

function theMember(view: OfficeView): MemberPresentation {
  const mission = theMission(view);
  if (mission.members.state !== 'observed') throw new Error('members not observed');
  return mission.members.value[0];
}

/** The overview request for the fixture's one agent, exactly as the client would send it. */
function overviewRequest(view = viewOf(catalogAnswer(exported()))): CodeReviewRef {
  const offer = reviewOfferFor(theMission(view), theMember(view));
  if (offer.state !== 'offered') throw new Error(`not offered: ${offer.reason}`);
  return {
    missionRef: theMission(view).missionRef,
    memberRef: theMember(view).memberRef,
    taskId: offer.taskId,
    listing: offer.listing,
  };
}

function fileRequest(raw: Raw, p: string, view?: OfficeView): CodeReviewRef {
  const answer = fileAnswer(raw, p);
  return {
    ...overviewRequest(view),
    snapshot: String(answer.snapshot),
    fileId: String(answer.file_id),
  };
}

/** The message the client sends for a reference. */
const messageOf = (ref: CodeReviewRef): Raw => ({ type: 'diCodeReviewRead', ...ref });

describe('the exported fixture is portable and leaks nothing', () => {
  it('carries no real machine root and none of the private content', () => {
    const text = fs.readFileSync(FIXTURE, 'utf-8');
    expect(REAL_ROOT.test(text)).toBe(false);
    expect(text).not.toContain('S3CR3T-KEY-MATERIAL-NEVER-SHOWN');
    expect(text).not.toContain('hunter2-NEVER-SHOWN');
    const raw = exported();
    const record = raw.normalization as Raw;
    expect(String(record.transformation)).toContain('/var/folders/<a>/<b>/T/<dir>');
    expect((overviewAnswer(raw).target as Raw).worktree).toBe('di-fixture-root/workspace');
  });
});

describe('the TypeScript bounds (exact values — a different mechanism from the Python gate)', () => {
  it('are the backend bounds, never raised', () => {
    expect({
      MAX_REVIEW_FILES: codeReview.MAX_REVIEW_FILES,
      MAX_REVIEW_PATH_CHARS: codeReview.MAX_REVIEW_PATH_CHARS,
      MAX_REVIEW_FILE_LINES: codeReview.MAX_REVIEW_FILE_LINES,
      MAX_REVIEW_FILE_CHARS: codeReview.MAX_REVIEW_FILE_CHARS,
      MAX_REVIEW_LINE_CHARS: codeReview.MAX_REVIEW_LINE_CHARS,
      MAX_REVIEW_SECTION_CHARS: codeReview.MAX_REVIEW_SECTION_CHARS,
      MAX_REVIEW_BRANCH_CHARS: codeReview.MAX_REVIEW_BRANCH_CHARS,
      MAX_REVIEW_WORKTREE_CHARS: codeReview.MAX_REVIEW_WORKTREE_CHARS,
      MAX_REVIEW_FILTERS: codeReview.MAX_REVIEW_FILTERS,
      CODE_REVIEW_MIN_INTERVAL_MS,
      CODE_REVIEW_REQUEST_TIMEOUT_MS,
      MAX_CODE_REVIEW_RESPONSE_BYTES,
    }).toEqual({
      MAX_REVIEW_FILES: 500,
      MAX_REVIEW_PATH_CHARS: 1024,
      MAX_REVIEW_FILE_LINES: 6000,
      MAX_REVIEW_FILE_CHARS: 1_048_576,
      MAX_REVIEW_LINE_CHARS: 1200,
      MAX_REVIEW_SECTION_CHARS: 200,
      MAX_REVIEW_BRANCH_CHARS: 255,
      MAX_REVIEW_WORKTREE_CHARS: 2048,
      MAX_REVIEW_FILTERS: 16,
      CODE_REVIEW_MIN_INTERVAL_MS: 250,
      CODE_REVIEW_REQUEST_TIMEOUT_MS: 45_000,
      MAX_CODE_REVIEW_RESPONSE_BYTES: 8 * 1024 * 1024,
    });
  });

  it('the response cap covers the worst retained file and the file count', () => {
    // 1 MiB of line text, escaped at most six-fold, plus the structure of 6,000 lines
    expect(MAX_CODE_REVIEW_RESPONSE_BYTES).toBeGreaterThan(6 * codeReview.MAX_REVIEW_FILE_CHARS);
    // 500 files at a generous 1 KiB each
    expect(MAX_CODE_REVIEW_RESPONSE_BYTES).toBeGreaterThan(500 * 1024);
  });

  it('the office never half-writes a character: its own refusal text is cut at a character, not inside a surrogate pair', () => {
    // 'a' then 200 emoji: unit 239 is the first half of a pair, so a cut at 240 units would leave half of one
    const ref: CodeReviewRef = { missionRef: 'm', memberRef: 'x', taskId: 't', listing: 'l' };
    const refusal = codeReview.officeRefusal(
      ref,
      'review_not_offered',
      'a' + '\u{1F600}'.repeat(200),
    );
    const detail = refusal.detail as string;
    expect(detail.length).toBeLessThanOrEqual(240);
    expect(detail.startsWith('a')).toBe(true);
    expect(
      /[\uD800-\uDBFF](?![\uDC00-\uDFFF])|(?<![\uD800-\uDBFF])[\uDC00-\uDFFF]/.test(detail),
    ).toBe(false);
    expect(detail).toBe('a' + '\u{1F600}'.repeat(119)); // 239 units: the 120th emoji did not fit whole
  });

  it('the closed problem vocabularies have no duplicates and do not overlap', () => {
    for (const list of [CODE_REVIEW_PROBLEMS, OFFICE_REVIEW_PROBLEMS, REVIEW_UNAVAILABLE_REASONS]) {
      expect(new Set(list).size).toBe(list.length);
    }
    const overlap = (CODE_REVIEW_PROBLEMS as readonly string[]).filter((p) =>
      (OFFICE_REVIEW_PROBLEMS as readonly string[]).includes(p),
    );
    // the same words may be said by both sides; a reader maps each to the same plain text
    expect(overlap.sort()).toEqual(['mission_listed_more_than_once', 'mission_not_listed']);
  });
});

describe('the request: EXACTLY an admitted reference', () => {
  const overview = (): Raw => messageOf(overviewRequest());
  const file = (): Raw => messageOf(fileRequest(exported(), 'src/app.py'));

  it('accepts the overview (five keys) and one file (seven keys)', () => {
    expect(parseCodeReviewRead(overview())).toEqual(overviewRequest());
    expect(parseCodeReviewRead(file())).toEqual(fileRequest(exported(), 'src/app.py'));
  });

  // ROUND 16: a read may NAME ITSELF with one optional, opaque `requestId`; the office echoes it. The shapes stay closed: the same five or seven keys plus
  // this ONE optional key, a bounded token. It carries no authority and the backend is never given it (see the DiOffice tests below).
  it('accepts the same two shapes WITH one optional opaque requestId, and carries it on the reference', () => {
    expect(parseCodeReviewRead({ ...overview(), requestId: 'r1.7' })).toEqual({
      ...overviewRequest(),
      requestId: 'r1.7',
    });
    expect(parseCodeReviewRead({ ...file(), requestId: 'abc-DEF_0.9' })).toEqual({
      ...fileRequest(exported(), 'src/app.py'),
      requestId: 'abc-DEF_0.9',
    });
    expect(parseCodeReviewRead({ ...overview(), requestId: 'x'.repeat(64) })?.requestId).toBe(
      'x'.repeat(64),
    );
  });

  it('a request WITHOUT a requestId is still served exactly as before (the legacy shape; the recorded fixtures carry none)', () => {
    expect(parseCodeReviewRead(overview())).toEqual(overviewRequest());
    expect('requestId' in (parseCodeReviewRead(overview()) as object)).toBe(false);
  });

  it('refuses a requestId that is not a bounded opaque token, and still refuses every other extra key', () => {
    const cases: Array<[string, Raw]> = [
      ['a number', { ...overview(), requestId: 7 }],
      ['an empty id', { ...overview(), requestId: '' }],
      ['an over-long id', { ...overview(), requestId: 'x'.repeat(65) }],
      ['an id with a space', { ...overview(), requestId: 'a b' }],
      ['an id with a slash', { ...overview(), requestId: 'a/b' }],
      ['an id with a control character', { ...overview(), requestId: 'a\u0000b' }],
      ['an id that is an object', { ...overview(), requestId: {} }],
      ['a second extra key beside a valid id', { ...overview(), requestId: 'r1', extra: 1 }],
      ['a path beside a valid id', { ...overview(), requestId: 'r1', path: '/etc/passwd' }],
    ];
    for (const [label, msg] of cases) expect(parseCodeReviewRead(msg), label).toBeNull();
  });

  it('refuses everything else, unread', () => {
    const cases: Array<[string, Raw]> = [
      ['an extra key', { ...overview(), extra: 1 }],
      ['a path', { ...overview(), path: '/etc/passwd' }],
      ['a root', { ...overview(), root: '/' }],
      ['a ref', { ...overview(), ref: 'HEAD~1' }],
      ['a git option', { ...overview(), option: '--output=/tmp/x' }],
      ['a pathspec', { ...overview(), pathspec: ':(glob)**' }],
      ['a snapshot without a file id', { ...overview(), snapshot: 'a'.repeat(64) }],
      ['a file id without a snapshot', { ...overview(), fileId: 'rf-' + 'a'.repeat(32) }],
      ['the wrong type', { ...overview(), type: 'diEvidenceRead' }],
      [
        'a missing key',
        (({ listing: _l, ...rest }) => rest)(overview() as Raw & { listing: string }),
      ],
      ['a non-string Mission', { ...overview(), missionRef: 7 }],
      ['a Mission ref that is a path', { ...overview(), missionRef: '../../etc' }],
      ['a malformed task id', { ...overview(), taskId: '2026-10-18' }],
      ['a malformed listing token', { ...overview(), listing: 'xyz' }],
      ['an empty member ref', { ...overview(), memberRef: '' }],
      ['a member ref with a control character', { ...overview(), memberRef: 'a\u0000b' }],
      ['an over-long member ref', { ...overview(), memberRef: 'm'.repeat(257) }],
      ['a malformed snapshot', { ...file(), snapshot: 'nope' }],
      ['a malformed file id', { ...file(), fileId: 'rf-short' }],
      ['an array', [] as unknown as Raw],
    ];
    for (const [label, msg] of cases) {
      expect(parseCodeReviewRead(msg), label).toBeNull();
    }
  });

  it('the backend is asked the closed query and nothing else, whatever id the read carried (the id is for the office and the asking client only)', () => {
    const named = { ...fileRequest(exported(), 'src/app.py'), requestId: 'r1.7' };
    expect(codeReview.backendQuery(named)).toEqual(
      codeReview.backendQuery(fileRequest(exported(), 'src/app.py')),
    );
    expect(
      Object.keys(codeReview.backendQuery({ ...overviewRequest(), requestId: 'r1.7' })).sort(),
    ).toEqual(['missionRef', 'taskId']);
  });

  it('the backend is asked the closed query and nothing else', () => {
    const ref = fileRequest(exported(), 'src/app.py');
    expect(codeReview.backendQuery(ref)).toEqual({
      missionRef: ref.missionRef,
      taskId: ref.taskId,
      snapshot: ref.snapshot,
      fileId: ref.fileId,
    });
    expect(Object.keys(codeReview.backendQuery(overviewRequest())).sort()).toEqual([
      'missionRef',
      'taskId',
    ]);
  });

  it('the route path carries only the closed query, in the backend grammar', () => {
    const ref = fileRequest(exported(), 'src/app.py');
    const q = codeReview.backendQuery(ref);
    expect(routePath('code_review', undefined, undefined, q)).toBe(
      `/code_review?mission_ref=${encodeURIComponent(ref.missionRef)}&task_id=${ref.taskId}` +
        `&snapshot=${ref.snapshot}&file_id=${ref.fileId}`,
    );
    expect(
      routePath('code_review', undefined, undefined, {
        ...q,
        snapshot: undefined,
        fileId: undefined,
      }),
    ).toBe(`/code_review?mission_ref=${encodeURIComponent(ref.missionRef)}&task_id=${ref.taskId}`);
    expect(routePath('code_review')).toBeNull();
    for (const bad of [
      { missionRef: '../x', taskId: ref.taskId },
      { missionRef: ref.missionRef, taskId: 'x' },
      { missionRef: ref.missionRef, taskId: ref.taskId, snapshot: ref.snapshot },
      { missionRef: ref.missionRef, taskId: ref.taskId, fileId: ref.fileId },
      { ...q, fileId: 'rf-zz' },
    ]) {
      expect(isCodeReviewQuery(bad)).toBe(false);
      expect(routePath('code_review', undefined, undefined, bad)).toBeNull();
    }
    expect(CODE_REVIEW_GRAMMAR.taskId.test('20261014-000000-aaaaaa')).toBe(true);
    const herdrRef = 'di_herdr_tasks:20261014-000000-aaaaaa@' + 'ab'.repeat(6);
    expect(isCodeReviewQuery({ missionRef: herdrRef, taskId: '20261014-000000-aaaaaa' })).toBe(
      true,
    );
    expect(
      routePath('code_review', undefined, undefined, {
        missionRef: herdrRef,
        taskId: '20261014-000000-aaaaaa',
      }),
    ).toContain('mission_ref=');
  });
});

describe('the offer: an active, bound Mission Core assignment or Herdr task, for a live direct member', () => {
  const view = (edit?: (answer: Raw) => void, now?: number): OfficeView => {
    const answer = clone(catalogAnswer(exported()));
    edit?.(answer);
    return viewOf(answer, now);
  };
  const offer = (v: OfficeView) => reviewOfferFor(theMission(v), theMember(v));

  it('is offered for the fixture agent, with a listing token', () => {
    const v = view();
    const o = offer(v);
    expect(o.state).toBe('offered');
    if (o.state !== 'offered') return;
    expect(o.taskId).toBe('20261014-000000-aaaaaa');
    expect(o.listing).toMatch(/^[0-9a-f]{64}$/);
    expect(o.listing).toBe(listingToken(theMission(v), theMember(v)));
  });

  it('is not offered, and says why, for each way an assignment can fail to be active work', () => {
    const cases: Array<[string, (a: Raw) => void, string]> = [
      [
        'inactive standing',
        (a) => {
          (missionRaw(a).lifecycle as Raw).standing = 'inactive';
        },
        'assignment_not_active',
      ],
      [
        'terminal standing (a completed Mission)',
        (a) => {
          (missionRaw(a).lifecycle as Raw).standing = 'terminal';
        },
        'assignment_not_active',
      ],
      [
        'binding not bound',
        (a) => {
          (missionRaw(a).binding as Raw).state = 'unbound';
        },
        'assignment_not_bound',
      ],
      [
        'the agent has disappeared from the roster',
        (a) => {
          ((missionRaw(a).members as Raw[])[0].classification as unknown) = 'disappeared';
        },
        'member_not_live',
      ],
      [
        'the agent was replaced (another session holds its place)',
        (a) => {
          ((missionRaw(a).members as Raw[])[0].classification as unknown) = 'replaced';
        },
        'member_not_live',
      ],
    ];
    for (const [label, edit, reason] of cases) {
      let o: ReturnType<typeof offer>;
      try {
        o = offer(view(edit));
      } catch (error) {
        throw new Error(`${label}: ${String(error)}`);
      }
      expect(o.state, label).toBe('unavailable');
      expect(o.reason, label).toBe(reason);
      expect(o.listing, label).toBeNull();
    }
  });

  it('is not offered to a member outside the direct task, a member with no task, or in a v1 office', () => {
    const v = view();
    const mission = theMission(v);
    const member = theMember(v);
    expect(reviewOfferFor(mission, { ...member, chain: 'child' })).toMatchObject({
      state: 'unavailable',
      reason: 'member_not_in_direct_task',
    });
    expect(reviewOfferFor(mission, { ...member, coverage: 'unrecognized' })).toMatchObject({
      reason: 'member_not_in_direct_task',
    });
    expect(reviewOfferFor(mission, { ...member, taskId: null })).toMatchObject({
      reason: 'task_not_observed',
    });
    expect(reviewOfferFor({ ...mission, v2: null }, member)).toMatchObject({
      reason: 'contract_version_1_office',
    });
    // an active, bound Herdr task is offered from the same listing facts as Mission Core
    const herdr = reviewOfferFor({ ...mission, sourceKind: 'herdr_task' }, member);
    expect(herdr).toMatchObject({ state: 'offered', reason: null, taskId: member.taskId });
    if (herdr.state === 'offered') expect(herdr.listing).toMatch(/^[0-9a-f]{64}$/);
    // unbound: the workspace is not confirmed, which is its own reason
    expect(
      reviewOfferFor({ ...mission, sourceKind: 'herdr_task', bindingState: 'unverified' }, member),
    ).toMatchObject({ state: 'unavailable', reason: 'assignment_not_bound', listing: null });
    // a bound active Herdr workspace is offered even when the member is not live
    const quiet = herdrWorkspaceReviewOffer({ ...mission, sourceKind: 'herdr_task' });
    expect(quiet.state).toBe('offered');
    if (quiet.state === 'offered') expect(quiet.listing).toMatch(/^[0-9a-f]{64}$/);
    expect(
      herdrWorkspaceReviewOffer({ ...mission, sourceKind: 'herdr_task', bindingState: 'unverified' }),
    ).toMatchObject({ state: 'unavailable', reason: 'assignment_not_bound' });
    // a workflow record and an unrecognized kind are not reviewed
    for (const sourceKind of ['workflow_store', 'unrecognized'] as const) {
      expect(reviewOfferFor({ ...mission, sourceKind }, member), sourceKind).toMatchObject({
        state: 'unavailable',
        reason: 'not_a_mission_core_record',
        listing: null,
      });
    }
    expect(reviewOfferFor({ ...mission, standingKnown: false }, member)).toMatchObject({
      reason: 'assignment_not_active',
    });
  });

  it('the listing token moves with the assignment and the agent, not with the journal', () => {
    const v = view();
    const mission = theMission(v);
    const member = theMember(v);
    const token = listingToken(mission, member);
    const moved: Array<[string, string]> = [
      ['task', listingToken(mission, { ...member, taskId: '20261014-000000-bbbbbb' })],
      ['member', listingToken(mission, { ...member, memberRef: member.memberRef + 'x' })],
      ['agent', listingToken(mission, { ...member, agent: 'someone-else' })],
      [
        'session',
        listingToken(mission, {
          ...member,
          sessionRecorded: { state: 'observed', value: 'other' },
        }),
      ],
      ['pane', listingToken(mission, { ...member, paneId: { state: 'observed', value: 'w9:p9' } })],
      [
        'workspace',
        listingToken(mission, { ...member, workspaceId: { state: 'observed', value: 'w9' } }),
      ],
      ['binding', listingToken({ ...mission, bindingState: 'ambiguous' }, member)],
      ['standing', listingToken({ ...mission, standing: 'inactive' }, member)],
      ['source', listingToken({ ...mission, sourceId: 'other' }, member)],
      ['native id', listingToken({ ...mission, nativeId: 'mn-other' }, member)],
    ];
    for (const [what, other] of moved) expect(other, what).not.toBe(token);
    // The Mission's journal advances with every record the agent writes; the token must not.
    const journal = view((a) => {
      const report = (missionRaw(a).mission_state as Raw).report as Raw;
      (report.provenance as Raw).journal_digest_sha256 = 'f'.repeat(64);
    });
    expect(listingToken(theMission(journal), theMember(journal))).toBe(token);
  });
});

describe('admission against the CURRENT listing', () => {
  const ref = (): CodeReviewRef => overviewRequest();
  const v = (): OfficeView => viewOf(catalogAnswer(exported()));

  it('admits the reference the client was shown', () => {
    expect(admitAgainstView(v(), ref())).toEqual({ ok: true });
  });

  it('refuses each way the listing can have moved, with its own code', () => {
    const r = ref();
    const refuse = (view: OfficeView, ref_: CodeReviewRef, problem: string) => {
      const admitted = admitAgainstView(view, ref_);
      expect(admitted.ok).toBe(false);
      if (!admitted.ok) expect(admitted.problem).toBe(problem);
    };
    refuse(v(), { ...r, missionRef: 'mc1:mn-' + 'e'.repeat(32) }, 'mission_not_listed');
    refuse(v(), { ...r, memberRef: r.memberRef + '#other' }, 'member_not_listed');
    refuse(v(), { ...r, taskId: '20261014-000000-bbbbbb' }, 'task_not_the_one_listed');
    refuse(v(), { ...r, listing: 'e'.repeat(64) }, 'listing_moved');
    // an assignment that stopped being active or bound
    const inactive = clone(catalogAnswer(exported()));
    ((missionRaw(inactive).lifecycle as Raw).standing as unknown) = 'inactive';
    refuse(viewOf(inactive), r, 'review_not_offered');
    const unbound = clone(catalogAnswer(exported()));
    (missionRaw(unbound).binding as Raw).state = 'ambiguous';
    refuse(viewOf(unbound), r, 'review_not_offered');
    // the office is no longer live
    refuse(
      viewOf(catalogAnswer(exported()), generatedAt(catalogAnswer(exported())) + 100_000),
      r,
      'review_not_offered',
    );
    // the office lists no Missions at all
    const missions = viewOf(catalogAnswer(exported()));
    refuse(
      { ...missions, missions: { state: 'unavailable', reason: 'x' } } as OfficeView,
      r,
      'review_not_offered',
    );
  });

  it('refuses a Mission listed twice and a member listed twice', () => {
    const r = ref();
    const twice = clone(catalogAnswer(exported()));
    const missions = (twice.response as Raw).missions as Raw[];
    missions.push(clone(missions[0]));
    const dup = admitAgainstView(viewOf(twice), r);
    expect(dup.ok).toBe(false);
    if (!dup.ok) expect(dup.problem).toBe('mission_listed_more_than_once');
    const members = clone(catalogAnswer(exported()));
    const roster = missionRaw(members).members as Raw[];
    roster.push(clone(roster[0]));
    const again = admitAgainstView(viewOf(members), r);
    expect(again.ok).toBe(false);
  });
});

describe('the backend answer, re-checked against the real emissions', () => {
  const overviewRef = (): CodeReviewRef => overviewRequest();

  it('accepts the real overview, field for field', () => {
    const raw = exported();
    const parsed = parseCodeReviewAnswer(clone(overviewAnswer(raw)), overviewRef());
    expect(parsed.ok).toBe(true);
    if (!parsed.ok) return;
    const a = parsed.answer;
    expect(a.state).toBe('observed');
    expect(a.overview?.files).toHaveLength(23);
    expect(a.overview?.totals).toEqual({
      files: 23,
      additions: 7025,
      deletions: 110,
      withheld: 6,
      unshown: 11,
    });
    expect(a.target).toMatchObject({
      branch: 'main',
      detached: false,
      worktree: 'di-fixture-root/workspace',
      attribution: 'shared_worktree',
    });
    expect(a.comparison).toMatchObject({
      mode: 'head_to_worktree',
      baseKind: 'head_commit',
      candidateKind: 'working_tree',
      covers: ['staged', 'unstaged', 'untracked', 'submodule_pointers'],
      doesNotCover: [
        'ignored',
        'submodule_contents',
        'binary_content',
        'files_over_bound',
        'private_locations',
      ],
      filtersNotRun: [],
    });
    const byPath = new Map(a.overview!.files.map((f) => [f.path, f]));
    // a submodule is listed by its recorded commit (shown, so not "unshown"); a nested repository
    // is one entry that says why it is not shown
    expect(byPath.get('vendor/lib')).toMatchObject({
      state: 'submodule',
      reason: null,
      additions: 1,
      deletions: 1,
    });
    expect(byPath.get('vendor/clone')).toMatchObject({
      state: 'unsupported',
      reason: 'nested_repository',
    });
    expect(byPath.get('src/moved.py')).toMatchObject({
      change: 'renamed',
      oldPath: 'src/mover.py',
    });
    expect(byPath.get('secrets/server.pem')).toMatchObject({
      state: 'withheld',
      reason: 'credential_like_name',
      additions: null,
      deletions: null,
    });
    expect(byPath.get('generated/bundle.min.js')?.state).toBe('oversized');
    expect(byPath.get('legacy/latin1.txt')?.state).toBe('not_utf8');
    expect(byPath.get('bin/blob.bin')?.state).toBe('binary');
  });

  it('accepts every real file answer, including the truncated one', () => {
    const raw = exported();
    for (const p of Object.keys(raw.files as Raw)) {
      const parsed = parseCodeReviewAnswer(clone(fileAnswer(raw, p)), fileRequest(raw, p));
      expect(parsed.ok, p).toBe(true);
      if (parsed.ok) expect(parsed.answer.file?.path, p).toBe(p);
    }
    const big = parseCodeReviewAnswer(
      clone(fileAnswer(raw, 'data/large.txt')),
      fileRequest(raw, 'data/large.txt'),
    );
    expect(big.ok).toBe(true);
    if (!big.ok) return;
    expect(big.answer.file).toMatchObject({
      truncated: true,
      linesTotal: 7100,
      linesRetained: 6000,
      additions: 7000,
      deletions: 100,
    });
  });

  it('accepts every real refusal, with the code the backend gave', () => {
    const raw = exported();
    const refs: Record<string, CodeReviewRef> = {
      assignment_task_changed: { ...overviewRef(), taskId: '20261014-000000-bbbbbb' },
      file_not_in_snapshot: {
        ...overviewRef(),
        snapshot: String(overviewAnswer(raw).snapshot),
        fileId: 'rf-' + '0'.repeat(32),
      },
      snapshot_moved: {
        ...overviewRef(),
        snapshot: String(overviewAnswer(raw).snapshot),
        fileId: String(refusalAnswer(raw, 'snapshot_moved').file_id),
      },
    };
    for (const [name, ref] of Object.entries(refs)) {
      const parsed = parseCodeReviewAnswer(clone(refusalAnswer(raw, name)), ref);
      expect(parsed.ok, name).toBe(true);
      if (parsed.ok) {
        expect(parsed.answer).toMatchObject({ state: 'refused', problem: name });
        expect(parsed.answer.target).toBeNull();
      }
    }
  });

  it('every refusal code the backend can emit is one the office knows', () => {
    const raw = exported();
    for (const name of Object.keys(raw.refusals as Raw)) {
      expect(CODE_REVIEW_PROBLEMS).toContain(name);
    }
  });
});

describe('an answer that is not EXACTLY right is never relayed', () => {
  const raw = (): Raw => exported();
  const overview = (): Raw => clone(overviewAnswer(raw()));
  const file = (p = 'src/app.py'): Raw => clone(fileAnswer(raw(), p));
  const invalid = (body: unknown, ref: CodeReviewRef, label: string): void => {
    const parsed = parseCodeReviewAnswer(body, ref);
    expect(parsed.ok, label).toBe(false);
    if (!parsed.ok) expect(parsed.problem, label).toBe('backend_answer_invalid');
  };
  const files = (a: Raw): Raw[] => (a.overview as Raw).files as Raw[];
  const entry = (a: Raw, p: string): Raw => files(a).find((f) => f.path === p)!;

  it('the envelope', () => {
    const ref = overviewRequest();
    invalid(null, ref, 'null');
    invalid([], ref, 'array');
    invalid({ ...overview(), extra: 1 }, ref, 'an extra key');
    invalid(
      (({ accounting: _a, ...rest }) => rest)(overview() as Raw & { accounting: unknown }),
      ref,
      'a missing key',
    );
    invalid({ ...overview(), schema: 'di.evidence.artifact_content' }, ref, 'schema');
    invalid({ ...overview(), version: 2 }, ref, 'version');
    invalid({ ...overview(), view: 'file' }, ref, 'the wrong view');
    invalid({ ...overview(), mission_ref: 'mc1:mn-' + 'd'.repeat(32) }, ref, 'another Mission');
    invalid({ ...overview(), task_id: '20261014-000000-bbbbbb' }, ref, 'another task');
    invalid({ ...overview(), file_id: 'rf-' + 'a'.repeat(32) }, ref, 'a file id in an overview');
    invalid({ ...overview(), observed_at: 1.5 }, ref, 'observed_at');
    invalid({ ...overview(), state: 'maybe' }, ref, 'state');
    invalid({ ...overview(), problem: 'made_up' }, ref, 'an observed answer with a problem');
    invalid({ ...overview(), detail: 'x' }, ref, 'an observed answer with a detail');
    invalid(
      { ...overview(), accounting: { ...(overview().accounting as Raw), git_bytes: -1 } },
      ref,
      'accounting',
    );
    invalid({ ...overview(), snapshot: 'nope' }, ref, 'snapshot');
  });

  it('a refusal', () => {
    const r = parseCodeReviewAnswer(clone(refusalAnswer(raw(), 'assignment_task_changed')), {
      ...overviewRequest(),
      taskId: '20261014-000000-bbbbbb',
    });
    expect(r.ok).toBe(true);
    const ref = { ...overviewRequest(), taskId: '20261014-000000-bbbbbb' };
    const base = refusalAnswer(raw(), 'assignment_task_changed');
    invalid({ ...clone(base), problem: 'made_up_code' }, ref, 'an unknown problem');
    invalid({ ...clone(base), problem: null }, ref, 'no problem');
    invalid({ ...clone(base), detail: null }, ref, 'no detail');
    invalid({ ...clone(base), detail: 'x'.repeat(241) }, ref, 'an over-long detail');
    invalid({ ...clone(base), target: overviewAnswer(raw()).target }, ref, 'a target in a refusal');
    invalid(
      { ...clone(base), overview: overviewAnswer(raw()).overview },
      ref,
      'content in a refusal',
    );
    invalid({ ...clone(base), snapshot: 'a'.repeat(64) }, ref, 'a snapshot nobody asked for');
  });

  it('the target and the comparison', () => {
    const ref = overviewRequest();
    const withTarget = (change: Raw): Raw => {
      const a = overview();
      a.target = { ...(a.target as Raw), ...change };
      return a;
    };
    invalid(withTarget({ attribution: 'agent_authored' }), ref, 'an attribution claim');
    invalid(withTarget({ extra: 1 }), ref, 'target key');
    invalid(withTarget({ head: 'xyz' }), ref, 'head');
    invalid(withTarget({ detached: true }), ref, 'detached with a branch');
    invalid(withTarget({ branch: null }), ref, 'attached without a branch');
    invalid(withTarget({ branch: '' }), ref, 'empty branch');
    invalid(withTarget({ branch: 'b'.repeat(256) }), ref, 'long branch');
    invalid(withTarget({ worktree: '' }), ref, 'empty worktree');
    invalid(withTarget({ source_id: 'Bad Id' }), ref, 'source id');
    const withComparison = (change: Raw): Raw => {
      const a = overview();
      a.comparison = { ...(a.comparison as Raw), ...change };
      return a;
    };
    invalid(withComparison({ mode: 'index_to_head' }), ref, 'mode');
    invalid(
      withComparison({ base: { kind: 'head_commit', oid: 'a'.repeat(40) } }),
      ref,
      'a base that is not the target head',
    );
    invalid(withComparison({ covers: ['staged'] }), ref, 'a narrower claim of coverage');
    invalid(withComparison({ does_not_cover: [] }), ref, 'a silenced gap');
    invalid(withComparison({ filters_not_run: Array(17).fill('x') }), ref, 'filters bound');
    invalid(withComparison({ candidate: { kind: 'index' } }), ref, 'candidate');
  });

  it('the overview and its arithmetic', () => {
    const ref = overviewRequest();
    const mutate = (edit: (a: Raw) => void): Raw => {
      const a = overview();
      edit(a);
      return a;
    };
    invalid(
      mutate((a) => (((a.overview as Raw).totals as Raw).files = 20)),
      ref,
      'totals.files',
    );
    invalid(
      mutate((a) => (((a.overview as Raw).totals as Raw).additions = 1)),
      ref,
      'totals.additions',
    );
    invalid(
      mutate((a) => (((a.overview as Raw).totals as Raw).withheld = 0)),
      ref,
      'totals.withheld',
    );
    invalid(
      mutate((a) => (((a.overview as Raw).totals as Raw).unshown = 0)),
      ref,
      'totals.unshown',
    );
    invalid(
      mutate((a) => files(a).push(clone(files(a)[0]))),
      ref,
      'a duplicate file',
    );
    invalid(
      mutate((a) => (files(a)[1].file_id = files(a)[0].file_id)),
      ref,
      'a duplicate id',
    );
    invalid(
      mutate((a) => (files(a)[0].path = files(a)[1].path)),
      ref,
      'a duplicate path',
    );
    for (const bad of [
      '/etc/passwd',
      '../x',
      'a//b',
      'a/./b',
      'a\\b',
      'a\u0000b',
      '',
      'p'.repeat(1025),
    ]) {
      invalid(
        mutate((a) => (files(a)[0].path = bad)),
        ref,
        `path ${JSON.stringify(bad.slice(0, 12))}`,
      );
    }
    invalid(
      mutate((a) => (files(a)[0].change = 'obliterated')),
      ref,
      'change',
    );
    invalid(
      mutate((a) => (files(a)[0].staging = 'maybe')),
      ref,
      'staging',
    );
    invalid(
      mutate((a) => (files(a)[0].state = 'fine')),
      ref,
      'state',
    );
    invalid(
      mutate((a) => (files(a)[0].file_id = 'rf-xyz')),
      ref,
      'file id',
    );
    invalid(
      mutate((a) => (entry(a, 'secrets/server.pem').additions = 3)),
      ref,
      'counts on a withheld file',
    );
    invalid(
      mutate((a) => (entry(a, 'secrets/server.pem').reason = null)),
      ref,
      'a withheld file without a reason',
    );
    invalid(
      mutate((a) => (entry(a, 'src/app.py').reason = 'private_location')),
      ref,
      'a text file with a reason',
    );
    // a submodule is shown (it carries its pointer's lines), so it is not "unshown" and carries no reason;
    // a nested repository's reason belongs to the state that explains it
    invalid(
      mutate((a) => (entry(a, 'vendor/lib').reason = 'nested_repository')),
      ref,
      'a submodule with a reason',
    );
    invalid(
      mutate((a) => (entry(a, 'src/app.py').reason = 'nested_repository')),
      ref,
      'nested_repository on a text file',
    );
    invalid(
      mutate((a) => (entry(a, 'vendor/clone').reason = null)),
      ref,
      'a nested repository without its reason',
    );
    invalid(
      mutate((a) => (entry(a, 'vendor/clone').additions = 2)),
      ref,
      'counts on a nested repository',
    );
    invalid(
      mutate((a) => (entry(a, 'vendor/clone').change = 'modified')),
      ref,
      'a nested repository that is not new',
    );
    invalid(
      mutate((a) => (((a.overview as Raw).totals as Raw).unshown = 12)),
      ref,
      'a submodule counted as not shown',
    );
    invalid(
      mutate((a) => (entry(a, 'src/app.py').old_path = 'src/old.py')),
      ref,
      'old_path without a rename',
    );
    invalid(
      mutate((a) => (entry(a, 'src/moved.py').old_path = null)),
      ref,
      'a rename without old_path',
    );
    invalid(
      mutate((a) => (entry(a, 'src/app.py').additions = -1)),
      ref,
      'negative count',
    );
    invalid(
      mutate((a) => (entry(a, 'src/app.py').deletions = 1.5)),
      ref,
      'fractional count',
    );
    invalid(
      mutate(
        (a) =>
          ((a.overview as Raw).files = Array.from({ length: 501 }, (_x, i) => ({
            ...clone(files(a)[0]),
            path: `p${i}`,
            file_id: 'rf-' + String(i).padStart(32, '0'),
          }))),
      ),
      ref,
      'more files than the bound',
    );
  });

  it('one file, its hunks and their arithmetic', () => {
    const raw_ = raw();
    const ref = fileRequest(raw_, 'src/util.py');
    const mutate = (edit: (block: Raw) => void, p = 'src/util.py'): Raw => {
      const a = file(p);
      edit(a.file as Raw);
      return a;
    };
    const hunk = (block: Raw): Raw => (block.hunks as Raw[])[0];
    const lines = (block: Raw): Raw[] => hunk(block).lines as Raw[];
    expect(parseCodeReviewAnswer(file('src/util.py'), ref).ok).toBe(true);
    invalid({ ...file('src/util.py'), view: 'overview' }, ref, 'view');
    invalid(
      { ...file('src/util.py'), overview: overviewAnswer(raw_).overview },
      ref,
      'an overview in a file answer',
    );
    invalid(
      { ...file('src/util.py'), snapshot: 'b'.repeat(64) },
      ref,
      'a snapshot nobody asked for',
    );
    invalid(file('src/app.py'), ref, 'another file than the one asked');
    invalid({ ...file('src/util.py'), file: null }, ref, 'no file');
    invalid(
      mutate((b) => (hunk(b).old_lines = 9)),
      ref,
      'a hunk longer than its header',
    );
    invalid(
      mutate((b) => (hunk(b).new_lines = 1)),
      ref,
      'a hunk shorter than its header',
    );
    invalid(
      mutate((b) => lines(b).pop()),
      ref,
      'a missing line',
    );
    invalid(
      mutate((b) => (lines(b)[0].kind = 'weird')),
      ref,
      'line kind',
    );
    invalid(
      mutate((b) => (lines(b)[0].old = null)),
      ref,
      'a context line without an old number',
    );
    invalid(
      mutate((b) => (lines(b)[1].new = 4)),
      ref,
      'a deleted line with a new number',
    );
    invalid(
      mutate((b) => (lines(b)[0].text = 'x'.repeat(1201))),
      ref,
      'an over-long line',
    );
    invalid(
      mutate((b) => (lines(b)[0].clipped = -1)),
      ref,
      'clipped',
    );
    invalid(
      mutate((b) => (b.additions = 5)),
      ref,
      'counts that disagree with the lines',
    );
    invalid(
      mutate((b) => (b.lines_retained = 4)),
      ref,
      'retained lines that disagree',
    );
    invalid(
      mutate((b) => (b.lines_total = 2)),
      ref,
      'a total below what was retained',
    );
    invalid(
      mutate((b) => (b.truncated = true)),
      ref,
      'truncated with nothing left out',
    );
    invalid(
      mutate((b) => (b.digest_sha256 = 'abc')),
      ref,
      'digest',
    );
    invalid(
      mutate((b) => (b.mode_change = { old: 'x', new: 'y' })),
      ref,
      'mode change',
    );
    invalid(
      mutate((b) => (b.similarity = 101)),
      ref,
      'similarity',
    );
    invalid(
      mutate((b) => (hunk(b).section = 's'.repeat(201))),
      ref,
      'section',
    );
    invalid(
      mutate((b) => (b.extra = 1)),
      ref,
      'an extra file key',
    );
    invalid(
      mutate((b) => (hunk(b).extra = 1)),
      ref,
      'an extra hunk key',
    );
    invalid(
      mutate((b) => (lines(b)[0].extra = 1)),
      ref,
      'an extra line key',
    );
    invalid(
      mutate((b) => ((b.hunks as Raw[]).length = 0)),
      ref,
      'a text file whose hunks are gone',
    );
    // a file whose state carries no content must carry none
    const binaryRef = fileRequest(raw_, 'bin/blob.bin');
    expect(parseCodeReviewAnswer(file('bin/blob.bin'), binaryRef).ok).toBe(true);
    invalid(
      mutate(
        (b) => (b.hunks = clone((fileAnswer(raw_, 'src/util.py').file as Raw).hunks)),
        'bin/blob.bin',
      ),
      binaryRef,
      'content on a binary file',
    );
    // a submodule's file view is its pointer: lines that add up to its counts
    const pointerRef = fileRequest(raw_, 'vendor/lib');
    expect(parseCodeReviewAnswer(file('vendor/lib'), pointerRef).ok).toBe(true);
    invalid(
      mutate((b) => (b.additions = 3), 'vendor/lib'),
      pointerRef,
      'a pointer whose counts disagree with its lines',
    );
    invalid(
      mutate((b) => ((b.hunks as Raw[]).length = 0), 'vendor/lib'),
      pointerRef,
      'a pointer whose lines are gone',
    );
    // a nested repository has nothing to show
    const nestedRef = fileRequest(raw_, 'vendor/clone');
    expect(parseCodeReviewAnswer(file('vendor/clone'), nestedRef).ok).toBe(true);
    invalid(
      mutate(
        (b) => (b.hunks = clone((fileAnswer(raw_, 'vendor/lib').file as Raw).hunks)),
        'vendor/clone',
      ),
      nestedRef,
      'content on a nested repository',
    );
  });

  it('isSafeReviewPath is the backend rule', () => {
    for (const ok of ['a', 'a/b.txt', '.github/workflows/ci.yml', 'x y/z']) {
      expect(isSafeReviewPath(ok), ok).toBe(true);
    }
    for (const bad of ['', '/a', 'a/', 'a//b', '.', '..', 'a/../b', 'a\\b', 'a\u0000', 7, null]) {
      expect(isSafeReviewPath(bad as unknown), String(bad)).toBe(false);
    }
  });
});

/** The keys of a diCodeReview answer, exactly (the schema's required list): what a read that carried no id is answered with. With an id there is one more, `requestId`. */
const ANSWER_KEYS = [
  'comparison',
  'decidedBy',
  'detail',
  'file',
  'fileId',
  'listing',
  'memberRef',
  'missionRef',
  'observedAt',
  'overview',
  'problem',
  'snapshot',
  'state',
  'target',
  'taskId',
  'type',
  'view',
].sort();

describe('DiOffice.readCodeReview — one bounded read, relayed only to the asking client', () => {
  const ENDPOINT: DiscoveryResult = { ok: true, endpoint: { port: 1, token: 'secret-not-logged' } };
  let clockMs = 0;
  const feed: { answer: Raw } = { answer: {} };
  const logs: string[] = [];

  beforeEach(() => {
    vi.useFakeTimers();
    logs.length = 0;
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  /** What the discovery file says NOW (a test may take it away after the office has polled). */
  const discoveryNow: { value: DiscoveryResult } = { value: ENDPOINT };

  async function office(
    requestReview?: (endpoint: unknown, q: unknown) => Promise<BackendResult>,
    answer = catalogAnswer(exported()),
  ) {
    clockMs = generatedAt(answer) * 1000;
    feed.answer = answer;
    discoveryNow.value = ENDPOINT;
    const o = new DiOffice({
      store: new AgentStateStore(),
      discoveryPath: '/sandbox/backend-discovery.json',
      contractVersion: 2,
      deps: {
        readDiscovery: () => discoveryNow.value,
        request: async () => ({
          kind: 'http',
          status: 200,
          headers: { 'x-di-freshness-health': 'fresh' },
          body: clone(feed.answer),
          bodyProblem: null,
        }),
        nowMs: () => clockMs,
        log: (line) => logs.push(line),
        ...(requestReview ? { requestReview: vi.fn(requestReview) } : {}),
      },
    });
    await o.poll();
    return o;
  }

  const http200 = (body: unknown): BackendResult => ({
    kind: 'http',
    status: 200,
    headers: {},
    body,
    bodyProblem: null,
  });

  // ROUND 16: the answer names the read it answers, on EVERY path, so a client can tell which of two reads of the same reference an answer is for. The
  // office refuses a second read IMMEDIATELY (that reply arrives first), so a refusal without the id would leave a hole at the message that starts the sequence.
  it('the answer ECHOES the requestId, the backend is never given it, and the id never reaches the log', async () => {
    const raw = exported();
    const backend = vi.fn(async (_e: unknown, _q: unknown) => http200(clone(overviewAnswer(raw))));
    const o = await office(backend);
    const sent: Raw[] = [];
    await o.readCodeReview(messageOf({ ...overviewRequest(), requestId: 'r1.7' }), (m) =>
      sent.push(m),
    );
    expect(sent).toHaveLength(1);
    expect(sent[0]).toMatchObject({ state: 'observed', requestId: 'r1.7' });
    expect(backend.mock.calls[0][1]).toEqual({
      missionRef: overviewRequest().missionRef,
      taskId: overviewRequest().taskId,
    }); // no id
    expect(JSON.stringify(backend.mock.calls)).not.toContain('r1.7');
    expect(logs.join('\n')).not.toContain('r1.7');
    o.stop();
  });

  it('an id the BACKEND supplies is never relayed: its answer is invalid, and the id on the refusal is the one the CLIENT sent', async () => {
    const raw = exported();
    const o = await office(async () =>
      http200({ ...clone(overviewAnswer(raw)), requestId: 'invented-by-the-backend' }),
    );
    const sent: Raw[] = [];
    await o.readCodeReview(messageOf({ ...overviewRequest(), requestId: 'r1' }), (m) =>
      sent.push(m),
    );
    expect(sent).toHaveLength(1);
    expect(sent[0]).toMatchObject({
      state: 'refused',
      problem: 'backend_answer_invalid',
      requestId: 'r1',
    });
    expect(JSON.stringify(sent)).not.toContain('invented-by-the-backend');
    const bare = await office(async () =>
      http200({ ...clone(overviewAnswer(raw)), requestId: 'invented-by-the-backend' }),
    );
    const sentBare: Raw[] = [];
    await bare.readCodeReview(messageOf(overviewRequest()), (m) => sentBare.push(m));
    expect(sentBare[0].state).toBe('refused');
    expect('requestId' in sentBare[0]).toBe(false);
    o.stop();
    bare.stop();
  });

  it('a read WITHOUT an id is answered WITHOUT one (no key is added): the legacy path is unchanged', async () => {
    const raw = exported();
    const o = await office(async () => http200(clone(overviewAnswer(raw))));
    const sent: Raw[] = [];
    await o.readCodeReview(messageOf(overviewRequest()), (m) => sent.push(m));
    expect(sent[0].state).toBe('observed');
    expect('requestId' in sent[0]).toBe(false);
    expect(Object.keys(sent[0]).sort()).toEqual(ANSWER_KEYS); // the key is ABSENT, not present with an undefined value
    o.stop();
  });

  // The field is optional in the answer too: a read that carried no id is answered by a message with exactly the seventeen keys it always had. The
  // key is ABSENT (an in-process consumer doing an exact key-set check, as our own closed-shape tests do, would see a stray key if it were merely
  // undefined, and only serialisation would hide it). One test per path.
  describe('a read WITHOUT an id is answered with NO requestId key on every path (absent, not present-and-undefined)', () => {
    it('read_in_progress', async () => {
      const raw = exported();
      let release: (r: BackendResult) => void = () => undefined;
      const o = await office(() => new Promise<BackendResult>((resolve) => (release = resolve)));
      const sent: Raw[] = [];
      const first = o.readCodeReview(messageOf(overviewRequest()), (m) => sent.push(m));
      await o.readCodeReview(messageOf(overviewRequest()), (m) => sent.push(m));
      expect(sent[0]).toMatchObject({ problem: 'read_in_progress' });
      expect(Object.keys(sent[0]).sort()).toEqual(ANSWER_KEYS);
      release(http200(clone(overviewAnswer(raw))));
      await first;
      o.stop();
    });

    it('read_rate_limited', async () => {
      const raw = exported();
      const o = await office(async () => http200(clone(overviewAnswer(raw))));
      const sent: Raw[] = [];
      await o.readCodeReview(messageOf(overviewRequest()), (m) => sent.push(m));
      await o.readCodeReview(messageOf(overviewRequest()), (m) => sent.push(m));
      expect(sent[1]).toMatchObject({ problem: 'read_rate_limited' });
      expect(Object.keys(sent[1]).sort()).toEqual(ANSWER_KEYS);
      o.stop();
    });

    it('an admission refusal', async () => {
      const o = await office(async () => http200({}));
      const sent: Raw[] = [];
      await o.readCodeReview(
        messageOf({ ...overviewRequest(), missionRef: 'mc-1:mn-' + 'f'.repeat(32) }),
        (m) => sent.push(m),
      );
      expect(sent[0].state).toBe('refused');
      expect(Object.keys(sent[0]).sort()).toEqual(ANSWER_KEYS);
      o.stop();
    });

    it('a backend failure', async () => {
      const o = await office(
        async () => ({ kind: 'transport_error', problem: 'backend_unreachable' }) as BackendResult,
      );
      const sent: Raw[] = [];
      await o.readCodeReview(messageOf(overviewRequest()), (m) => sent.push(m));
      expect(sent[0]).toMatchObject({ problem: 'backend_unavailable' });
      expect(Object.keys(sent[0]).sort()).toEqual(ANSWER_KEYS);
      o.stop();
    });
  });

  describe('EVERY refusal path echoes the id (one test per path, so a path that stops echoing is named by its own test)', () => {
    it("read_in_progress: the second read is refused AT ONCE and names WHICH read was refused; the first read's answer names the first", async () => {
      const raw = exported();
      let release: (r: BackendResult) => void = () => undefined;
      const backend = vi.fn(() => new Promise<BackendResult>((resolve) => (release = resolve)));
      const o = await office(backend);
      const sent: Raw[] = [];
      const first = o.readCodeReview(messageOf({ ...overviewRequest(), requestId: 'r1' }), (m) =>
        sent.push(m),
      );
      await o.readCodeReview(messageOf({ ...overviewRequest(), requestId: 'r2' }), (m) =>
        sent.push(m),
      ); // refused AT ONCE, and it arrives first
      expect(sent).toHaveLength(1);
      expect(sent[0]).toMatchObject({ problem: 'read_in_progress', requestId: 'r2' });
      release(http200(clone(overviewAnswer(raw))));
      await first;
      expect(sent[1]).toMatchObject({ state: 'observed', requestId: 'r1' }); // the old read's answer names the OLD read
      o.stop();
    });

    it('read_rate_limited: a read straight after another is refused and names itself', async () => {
      const raw = exported();
      const o = await office(async () => http200(clone(overviewAnswer(raw))));
      const sent: Raw[] = [];
      await o.readCodeReview(messageOf({ ...overviewRequest(), requestId: 'r1' }), (m) =>
        sent.push(m),
      );
      await o.readCodeReview(messageOf({ ...overviewRequest(), requestId: 'r3' }), (m) =>
        sent.push(m),
      ); // the interval has not passed
      expect(sent[1]).toMatchObject({ problem: 'read_rate_limited', requestId: 'r3' });
      o.stop();
    });

    it('an admission refusal (a Mission the office does not list) names the read it refused', async () => {
      const o = await office(async () => http200({}));
      const sent: Raw[] = [];
      await o.readCodeReview(
        messageOf({
          ...overviewRequest(),
          missionRef: 'mc-1:mn-' + 'f'.repeat(32),
          requestId: 'r4',
        }),
        (m) => sent.push(m),
      );
      expect(sent[0]).toMatchObject({ state: 'refused', requestId: 'r4' });
      expect(sent[0].problem).not.toBe('read_in_progress');
      o.stop();
    });

    it('a backend failure names the read it failed', async () => {
      const failing = await office(
        async () => ({ kind: 'transport_error', problem: 'backend_unreachable' }) as BackendResult,
      );
      const sent: Raw[] = [];
      await failing.readCodeReview(messageOf({ ...overviewRequest(), requestId: 'r5' }), (m) =>
        sent.push(m),
      );
      expect(sent[0]).toMatchObject({ problem: 'backend_unavailable', requestId: 'r5' });
      failing.stop();
    });
  });

  it('the overview reaches only the requesting socket, as data, asking the closed query', async () => {
    const raw = exported();
    const backend = vi.fn(async (_e: unknown, _q: unknown) => http200(clone(overviewAnswer(raw))));
    const o = await office(backend);
    const sent: Raw[] = [];
    await o.readCodeReview(messageOf(overviewRequest()), (m) => sent.push(m));
    expect(backend).toHaveBeenCalledTimes(1);
    expect(backend.mock.calls[0][1]).toEqual({
      missionRef: overviewRequest().missionRef,
      taskId: overviewRequest().taskId,
    });
    expect(sent).toHaveLength(1);
    expect(sent[0]).toMatchObject({
      type: 'diCodeReview',
      view: 'overview',
      state: 'observed',
      decidedBy: 'backend',
      problem: null,
      missionRef: overviewRequest().missionRef,
      memberRef: overviewRequest().memberRef,
      listing: overviewRequest().listing,
    });
    expect(((sent[0].overview as Raw).files as unknown[]).length).toBe(23);
    expect((sent[0].target as Raw).attribution).toBe('shared_worktree');
    o.stop();
  });

  it('one file, with its snapshot and file id, and the hunks as inert data', async () => {
    const raw = exported();
    const ref = fileRequest(raw, 'web/long_line.js');
    const backend = vi.fn(async (_e: unknown, _q: unknown) =>
      http200(clone(fileAnswer(raw, 'web/long_line.js'))),
    );
    const o = await office(backend);
    const sent: Raw[] = [];
    await o.readCodeReview(messageOf(ref), (m) => sent.push(m));
    expect(backend.mock.calls[0][1]).toEqual({
      missionRef: ref.missionRef,
      taskId: ref.taskId,
      snapshot: ref.snapshot,
      fileId: ref.fileId,
    });
    expect(sent[0]).toMatchObject({ view: 'file', state: 'observed', fileId: ref.fileId });
    const lines = (((sent[0].file as Raw).hunks as Raw[])[0].lines as Raw[]).filter(
      (l) => l.kind === 'add',
    );
    // "const payload = '" (17) + 5,000 digits + "';" (2) = 5,019 characters, shown to 1,200
    expect(String(lines[0].text)).toHaveLength(1200);
    expect(lines[0].clipped).toBe(5019 - 1200);
    o.stop();
  });

  it('a backend refusal is relayed with its own code and decided_by backend', async () => {
    const raw = exported();
    const ref = { ...overviewRequest() };
    const o = await office(async () =>
      http200({ ...clone(refusalAnswer(raw, 'assignment_task_changed')), task_id: ref.taskId }),
    );
    const sent: Raw[] = [];
    await o.readCodeReview(messageOf(ref), (m) => sent.push(m));
    expect(sent[0]).toMatchObject({
      state: 'refused',
      decidedBy: 'backend',
      problem: 'assignment_task_changed',
      target: null,
      overview: null,
      file: null,
    });
    o.stop();
  });

  it('refuses at the office, WITHOUT asking the backend, what the listing does not admit', async () => {
    const backend = vi.fn(async () => http200({}));
    const o = await office(backend);
    const base = overviewRequest();
    const cases: Array<[string, CodeReviewRef, string]> = [
      [
        'an unlisted Mission',
        { ...base, missionRef: 'mc1:mn-' + 'e'.repeat(32) },
        'mission_not_listed',
      ],
      ['an unlisted member', { ...base, memberRef: base.memberRef + 'x' }, 'member_not_listed'],
      ['another task', { ...base, taskId: '20261014-000000-bbbbbb' }, 'task_not_the_one_listed'],
      ['a moved listing', { ...base, listing: 'c'.repeat(64) }, 'listing_moved'],
    ];
    for (const [label, ref, problem] of cases) {
      clockMs += 10_000;
      const sent: Raw[] = [];
      await o.readCodeReview(messageOf(ref), (m) => sent.push(m));
      expect(sent, label).toHaveLength(1);
      expect(sent[0], label).toMatchObject({ state: 'refused', decidedBy: 'office', problem });
    }
    expect(backend).not.toHaveBeenCalled();
    o.stop();
  });

  it('refuses a malformed message silently (the dispatcher already refused it)', async () => {
    const backend = vi.fn(async () => http200({}));
    const o = await office(backend);
    const sent: Raw[] = [];
    await o.readCodeReview({ type: 'diCodeReviewRead', path: '/etc/passwd' }, (m) => sent.push(m));
    expect(sent).toEqual([]);
    expect(backend).not.toHaveBeenCalled();
    o.stop();
  });

  it('one read at a time, and at a bounded rate', async () => {
    const raw = exported();
    let release: (r: BackendResult) => void = () => undefined;
    const backend = vi.fn(
      () =>
        new Promise<BackendResult>((resolve) => {
          release = resolve;
        }),
    );
    const o = await office(backend);
    const sent: Raw[] = [];
    const ref = overviewRequest();
    const first = o.readCodeReview(messageOf(ref), (m) => sent.push(m));
    await o.readCodeReview(messageOf(ref), (m) => sent.push(m));
    expect(sent.map((m) => m.problem)).toEqual(['read_in_progress']);
    release(http200(clone(overviewAnswer(raw))));
    await first;
    expect(sent).toHaveLength(2);
    // immediately after: rate limited
    await o.readCodeReview(messageOf(ref), (m) => sent.push(m));
    expect(sent[2]).toMatchObject({ problem: 'read_rate_limited' });
    // and again after the interval
    clockMs += CODE_REVIEW_MIN_INTERVAL_MS;
    const again = o.readCodeReview(messageOf(ref), (m) => sent.push(m));
    release(http200(clone(overviewAnswer(raw))));
    await again;
    expect(sent[3]).toMatchObject({ state: 'observed' });
    expect(backend).toHaveBeenCalledTimes(2);
    o.stop();
  });

  it('a transport failure, a non-200 and a body that is not JSON are explicit refusals', async () => {
    const ref = overviewRequest();
    const outcomes: Array<[BackendResult, string]> = [
      [{ kind: 'transport_error', problem: 'backend_unreachable' }, 'backend_unavailable'],
      [
        { kind: 'http', status: 503, headers: {}, body: undefined, bodyProblem: null },
        'backend_refused_request',
      ],
      [
        { kind: 'http', status: 200, headers: {}, body: undefined, bodyProblem: 'body_not_json' },
        'backend_answer_invalid',
      ],
      [http200({ not: 'an answer' }), 'backend_answer_invalid'],
    ];
    for (const [result, problem] of outcomes) {
      const o = await office(async () => result);
      const sent: Raw[] = [];
      await o.readCodeReview(messageOf(ref), (m) => sent.push(m));
      expect(sent[0], problem).toMatchObject({ state: 'refused', problem });
      o.stop();
    }
    const throwing = await office(async () => {
      throw new Error('boom');
    });
    const sent: Raw[] = [];
    await throwing.readCodeReview(messageOf(ref), (m) => sent.push(m));
    expect(sent[0]).toMatchObject({ state: 'refused', problem: 'backend_unavailable' });
    throwing.stop();
  });

  it('an answer for another reference is never relayed', async () => {
    const raw = exported();
    const o = await office(async () => http200(clone(fileAnswer(raw, 'src/app.py'))));
    const sent: Raw[] = [];
    await o.readCodeReview(messageOf(overviewRequest()), (m) => sent.push(m));
    expect(sent[0]).toMatchObject({ state: 'refused', problem: 'backend_answer_invalid' });
    o.stop();
  });

  it('a listing that moves while the backend reads discards the answer', async () => {
    const raw = exported();
    let release: (r: BackendResult) => void = () => undefined;
    const o = await office(
      () =>
        new Promise<BackendResult>((resolve) => {
          release = resolve;
        }),
    );
    const sent: Raw[] = [];
    const pending = o.readCodeReview(messageOf(overviewRequest()), (m) => sent.push(m));
    // the assignment is replaced under the read: the next poll lists another task for the agent
    const moved = clone(catalogAnswer(raw));
    const mission = missionRaw(moved);
    ((mission.execution as Raw).task_id as unknown) = '20261014-000000-bbbbbb';
    ((mission.members as Raw[])[0].task_id as unknown) = '20261014-000000-bbbbbb';
    feed.answer = moved;
    clockMs += 10_000;
    await o.poll();
    release(http200(clone(overviewAnswer(raw))));
    await pending;
    expect(sent).toHaveLength(1);
    expect(sent[0]).toMatchObject({
      state: 'refused',
      decidedBy: 'office',
      problem: 'listing_moved_during_read',
      overview: null,
    });
    o.stop();
  });

  it('with no discovery file, and with a substituted backend, it says so and asks nothing', async () => {
    const backend = vi.fn(async () => http200({}));
    const none = await office(backend);
    discoveryNow.value = { ok: false, problem: 'discovery_absent' };
    const sent: Raw[] = [];
    await none.readCodeReview(messageOf(overviewRequest()), (m) => sent.push(m));
    expect(sent[0]).toMatchObject({ problem: 'backend_discovery_unavailable' });
    expect(backend).not.toHaveBeenCalled();
    none.stop();
    const substituted = await office(undefined);
    const sent2: Raw[] = [];
    await substituted.readCodeReview(messageOf(overviewRequest()), (m) => sent2.push(m));
    expect(sent2[0]).toMatchObject({ problem: 'reviews_not_supported_by_this_backend' });
    substituted.stop();
  });

  it('the log line carries a code and nothing of the answer', async () => {
    const raw = exported();
    const o = await office(async () => http200(clone(overviewAnswer(raw))));
    await o.readCodeReview(messageOf(overviewRequest()), () => undefined);
    const text = logs.join('\n');
    expect(text).toContain('code review read: backend observed');
    for (const secret of [
      'secret-not-logged',
      'src/app.py',
      'main',
      'di-fixture-root',
      overviewRequest().listing,
    ]) {
      expect(text).not.toContain(secret);
    }
    o.stop();
  });
});

describe('the dispatcher, the projected state and the relayed message', () => {
  it('diCodeReviewRead is a handled type, read-only, reaching the reader only when exact', () => {
    expect(DI_HANDLED_MESSAGE_TYPES).toContain('diCodeReviewRead');
    const calls: Raw[] = [];
    const ctx = {
      store: new AgentStateStore(),
      cache: {} as never,
      extensionVersion: 'test',
      readCodeReview: (m: Raw) => calls.push(m),
    };
    const send = vi.fn();
    expect(handleDiClientMessage(messageOf(overviewRequest()), send, ctx as never)).toBe('handled');
    expect(calls).toHaveLength(1);
    for (const bad of [
      { ...messageOf(overviewRequest()), path: '/' },
      { type: 'diCodeReviewRead' },
      { ...messageOf(overviewRequest()), snapshot: 'a'.repeat(64) },
    ]) {
      const result = handleDiClientMessage(bad as Raw, send, ctx as never);
      expect(result).toMatchObject({ refused: expect.stringContaining('admitted reference') });
    }
    expect(calls).toHaveLength(1);
    const noReader = handleDiClientMessage(messageOf(overviewRequest()), send, {
      ...ctx,
      readCodeReview: undefined,
    } as never);
    expect(noReader).toMatchObject({ refused: expect.stringContaining('no code review reader') });
    expect(send).not.toHaveBeenCalled();
  });

  // THE CONTRACT (core/asyncapi.yaml, DiCodeReviewRead.requestId): the read's id is "echoed in the one diCodeReview it sends for this read, on every
  // path, a refusal included". Found by the reception-v2 preview: the office OFFERED a review whose task id ("SYNTHETIC-task-direct") the read
  // grammar refuses, refused the page's read unread, and sent nothing, so the page waited on "Reading the files." with no end. A read the parser
  // refuses whose SHAPE is exactly a read's is answered with ONE refusal echoing what was sent (what the page matches an answer by); any other
  // shape cannot be named and gets nothing, as an uncorrelatable delivery request gets nothing.
  it("a read refused UNREAD gets ONE correlated office refusal (reference_not_admitted) when it is exactly a read's shape, and nothing otherwise", () => {
    const readCodeReview = vi.fn();
    const ctx = {
      store: new AgentStateStore(),
      cache: {} as never,
      extensionVersion: 'test',
      readCodeReview,
    };
    const MISSION = 'synthetic_mission_core:mn-00000000000000000000000000000002';
    const read: Raw = {
      type: 'diCodeReviewRead',
      missionRef: MISSION,
      memberRef: `${MISSION}#SYNTHETIC-task-direct#executor1`,
      taskId: 'SYNTHETIC-task-direct',
      listing: 'a'.repeat(64),
      requestId: 'w.1',
    };
    const refused = {
      state: 'refused',
      decidedBy: 'office',
      problem: 'reference_not_admitted',
      detail: 'the read is not exactly an admitted reference',
    };
    const empty = { target: null, comparison: null, overview: null, file: null, observedAt: null };
    const answerTo = (msg: Raw): unknown[] => {
      const send = vi.fn();
      const result = handleDiClientMessage(msg, send, ctx as never);
      expect(result).toMatchObject({
        refused: expect.stringContaining('is not exactly an admitted reference'),
      });
      return send.mock.calls.map((c) => c[0]);
    };

    // the overview read the reception-v2 page sends: ONE answer, the reference and the request id exactly as sent
    expect(answerTo(read)).toEqual([
      {
        type: 'diCodeReview',
        missionRef: MISSION,
        memberRef: read.memberRef,
        taskId: 'SYNTHETIC-task-direct',
        listing: read.listing,
        snapshot: null,
        fileId: null,
        view: 'overview',
        requestId: 'w.1',
        ...refused,
        ...empty,
      },
    ]);
    // one file's read: the view, the snapshot and the file id are echoed too
    const fileRead = {
      ...read,
      snapshot: 'b'.repeat(64),
      fileId: `rf-${'c'.repeat(32)}`,
      requestId: 'w.2',
    };
    expect(answerTo(fileRead)).toEqual([
      {
        type: 'diCodeReview',
        missionRef: MISSION,
        memberRef: read.memberRef,
        taskId: 'SYNTHETIC-task-direct',
        listing: read.listing,
        snapshot: fileRead.snapshot,
        fileId: fileRead.fileId,
        view: 'file',
        requestId: 'w.2',
        ...refused,
        ...empty,
      },
    ]);
    // a read that named itself with no id is answered with no id (absence is not a wildcard on the page, so it is not invented here)
    const { requestId: _unnamed, ...anonymous } = read;
    const unnamedAnswer = answerTo(anonymous);
    expect(unnamedAnswer).toHaveLength(1);
    expect('requestId' in (unnamedAnswer[0] as Raw)).toBe(false);
    // a FINISHED Mission's read (no member): answered with the empty member the contract gives a Mission-level answer
    const missionRead = {
      type: 'diCodeReviewMissionRead',
      missionRef: MISSION,
      taskId: 'SYNTHETIC-task-direct',
      listing: read.listing,
      requestId: 'w.3',
    };
    expect(answerTo(missionRead)).toEqual([
      {
        type: 'diCodeReview',
        missionRef: MISSION,
        memberRef: '',
        taskId: 'SYNTHETIC-task-direct',
        listing: read.listing,
        snapshot: null,
        fileId: null,
        view: 'overview',
        requestId: 'w.3',
        ...refused,
        ...empty,
      },
    ]);
    // LENGTH IS NOT SHAPE: the catalog projection does not bound an offer's strings, so an over-long task id is still answered
    expect(answerTo({ ...read, taskId: 'x'.repeat(300), requestId: 'w.4' })).toHaveLength(1);

    // NOT a read's shape: nothing can be named, so nothing is sent
    for (const [why, msg] of [
      ['a request id that is not the bounded token', { ...read, requestId: 'not a token!' }],
      ['a reference value that is not a string', { ...read, taskId: 7 }],
      ['a missing key', (({ listing: _l, ...rest }) => rest)(read)],
      ['an extra key', { ...read, path: '/' }],
      ['a snapshot without its file id', { ...read, snapshot: 'b'.repeat(64) }],
      ['a Mission-level read carrying a member', { ...missionRead, memberRef: read.memberRef }],
    ] as const) {
      expect(answerTo(msg as Raw), why).toEqual([]);
    }
    // and the reader is never asked for a read refused unread
    expect(readCodeReview).not.toHaveBeenCalled();
  });

  it('the office state projects the offer on each member', () => {
    const view = viewOf(catalogAnswer(exported()));
    const message = buildDiOfficeStateMessage(view, () => 1);
    const mission = message.missions.missions[0];
    expect(mission.members.members).toHaveLength(1);
    expect(mission.members.members[0].codeReview).toEqual({
      state: 'offered',
      reason: null,
      taskId: '20261014-000000-aaaaaa',
      listing: overviewRequest(view).listing,
    });
    const inactive = clone(catalogAnswer(exported()));
    ((missionRaw(inactive).lifecycle as Raw).standing as unknown) = 'inactive';
    const off = buildDiOfficeStateMessage(viewOf(inactive), () => 1);
    expect(off.missions.missions[0].members.members[0].codeReview).toEqual({
      state: 'unavailable',
      reason: 'assignment_not_active',
      taskId: '20261014-000000-aaaaaa',
      listing: null,
    });
  });

  it('relays a copy of the answer: mutating the message never touches the parsed answer', () => {
    const raw = exported();
    const ref = fileRequest(raw, 'src/app.py');
    const parsed = parseCodeReviewAnswer(clone(fileAnswer(raw, 'src/app.py')), ref);
    if (!parsed.ok) throw new Error('refused');
    const message = backendMessage(ref, parsed.answer);
    (message.file!.hunks[0].lines[0] as { text: string }).text = 'changed';
    expect(parsed.answer.file!.hunks[0].lines[0].text).not.toBe('changed');
    expect(message).toMatchObject({ type: 'diCodeReview', view: 'file', decidedBy: 'backend' });
  });
});
