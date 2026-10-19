/**
 * Item 3 (original 20) — the live code review, office side.
 *
 * A client asks with `diCodeReviewRead`: an ADMITTED REFERENCE — the Mission, the member (the agent
 * it opened the review from), the task it was shown and the LISTING TOKEN the office gave with the
 * offer — never a path, root, ref, pathspec or git option. For ONE file's diff it also names the
 * snapshot the overview carried and the file id the overview listed. The office:
 *
 * 1. parses it EXACTLY (closed key set, the backend's grammar) — anything else is refused unread;
 * 2. admits it against its CURRENT listing: the Mission is listed once, as a Mission Core record
 *    whose standing is active and whose binding is bound; the member is listed once, in the direct
 *    task, live, with the task the client was shown; and the listing token recomputed from the
 *    listing NOW equals the one the client holds (it moves when the assignment, the agent's
 *    session/pane/workspace or the binding moves — deliberately NOT with the Mission's journal,
 *    which advances with every record the agent writes and says nothing about which workspace is
 *    being read);
 * 3. asks the backend's /code_review route (one read at a time, at most one per
 *    CODE_REVIEW_MIN_INTERVAL_MS), which corroborates the active assignment, re-reads the registry,
 *    observes the workspace twice and discards anything that moved;
 * 4. RE-CHECKS the backend's answer before relaying it: closed key sets at every level, the same
 *    reference, a known problem code, every enumeration, every bound, and the arithmetic of the
 *    answer against itself (a hunk's lines against its header, the totals against the files);
 * 5. re-admits against the listing NOW: an answer whose listing moved while it was read is
 *    discarded, never relayed.
 *
 * The answer goes to the asking connection only, as data. Nothing here renders, executes,
 * follows, fetches or writes anything; patch text is untrusted and only ever shown as text.
 */

import * as crypto from 'crypto';

import type { DiCodeReview } from '../../../core/src/messages.js';
import type { CodeReviewQuery } from './backendClient.js';
import { CODE_REVIEW_GRAMMAR, isCodeReviewQuery } from './backendClient.js';
import type { MemberPresentation, MissionPresentation, OfficeView } from './officeView.js';

/** At most one backend review read per this interval (one is in flight at most, always). */
export const CODE_REVIEW_MIN_INTERVAL_MS = 250;
export const CODE_REVIEW_REQUEST_TIMEOUT_MS = 45_000;

// The backend's bounds, RE-DECLARED here and never raised (target_runtime/code_review.py; the
// values are pinned by exact-value assertion in server/__tests__/diCodeReview.test.ts — a
// different mechanism from the Python BoundConstantPinTests, which cannot see TypeScript).
export const MAX_REVIEW_FILES = 500;
export const MAX_REVIEW_PATH_CHARS = 1024;
export const MAX_REVIEW_FILE_LINES = 6000;
export const MAX_REVIEW_FILE_CHARS = 1_048_576;
export const MAX_REVIEW_LINE_CHARS = 1200;
export const MAX_REVIEW_SECTION_CHARS = 200;
export const MAX_REVIEW_BRANCH_CHARS = 255;
export const MAX_REVIEW_WORKTREE_CHARS = 2048;
export const MAX_REVIEW_FILTERS = 16;
const MAX_DETAIL_CHARS = 240;
const MAX_MEMBER_REF_CHARS = 256;
/** A hunk keeps at most the file's lines, plus git's "no newline" marker for each side. */
const MAX_HUNK_ROWS = MAX_REVIEW_FILE_LINES + 2;

/** Why a review is NOT offered for a member: the closed vocabulary the client words in plain text. */
export const REVIEW_UNAVAILABLE_REASONS = [
  'not_a_mission_core_record',
  'contract_version_1_office',
  'assignment_not_active',
  'assignment_not_bound',
  'member_not_in_direct_task',
  'member_not_live',
  'task_not_observed',
] as const;
export type ReviewUnavailableReason = (typeof REVIEW_UNAVAILABLE_REASONS)[number];

export type ReviewOffer =
  | { state: 'offered'; reason: null; taskId: string; listing: string }
  | { state: 'unavailable'; reason: ReviewUnavailableReason; taskId: string | null; listing: null };

/** Refusals decided by the OFFICE (before or after asking the backend). */
export const OFFICE_REVIEW_PROBLEMS = [
  'mission_not_listed',
  'mission_listed_more_than_once',
  'member_not_listed',
  'member_listed_more_than_once',
  'review_not_offered',
  'listing_moved',
  'task_not_the_one_listed',
  'read_in_progress',
  'read_rate_limited',
  'reviews_not_supported_by_this_backend',
  'backend_discovery_unavailable',
  'backend_unavailable',
  'backend_refused_request',
  'backend_answer_invalid',
  'listing_moved_during_read',
] as const;
export type OfficeReviewProblem = (typeof OFFICE_REVIEW_PROBLEMS)[number];

/**
 * The backend's CLOSED refusal vocabulary (target_runtime.code_review.PROBLEMS). Parity is pinned
 * by tests/test_code_review.py, which reads the list between these markers.
 */
// BEGIN CODE_REVIEW_PROBLEMS
export const CODE_REVIEW_PROBLEMS = [
  'review_needs_contract_version_2',
  'owner_shut_down',
  'review_read_in_progress',
  'no_fresh_catalog_answer',
  'mission_not_listed',
  'mission_listed_more_than_once',
  'assignment_not_a_mission_core_record',
  'assignment_not_active',
  'assignment_binding_not_bound',
  'assignment_task_changed',
  'assignment_task_or_workspace_unresolved',
  'registry_unreadable',
  'source_not_registered',
  'source_registration_changed',
  'workspace_not_a_readable_directory',
  'workspace_is_not_a_worktree_root',
  'no_base_commit',
  'git_read_failed',
  'git_read_deadline',
  'inventory_over_bound',
  'too_many_changed_files',
  'path_not_representable',
  'inventory_inconsistent',
  'review_read_budget_exhausted',
  'snapshot_moved',
  'file_not_in_snapshot',
  'workspace_moved_during_read',
  'assignment_changed_during_read',
  'file_diff_over_bound',
  'diff_not_understood',
] as const;
// END CODE_REVIEW_PROBLEMS

export const REVIEW_CHANGES = [
  'added',
  'modified',
  'deleted',
  'renamed',
  'type_changed',
  'untracked',
  'conflicted',
] as const;
export const REVIEW_STAGINGS = ['staged', 'unstaged', 'both', 'untracked', 'conflicted'] as const;
export const REVIEW_FILE_STATES = [
  'text',
  'empty',
  'binary',
  'not_utf8',
  'oversized',
  'withheld',
  'unsupported',
  'no_content_diff',
  'conflicted',
  'submodule',
] as const;
/** The states whose file view carries the file's own hunks (a submodule's is its pointer). */
const SHOWN_STATES = ['text', 'submodule'];
export const REVIEW_REASONS = [
  'private_location',
  'hidden_name_not_listed',
  'credential_like_name',
  'key_material_extension',
  'untracked_not_a_regular_file',
  'untracked_unreadable',
  'untracked_hardlinked',
  'type_change_not_shown',
  'nested_repository',
] as const;
/** The reasons each file state can carry (a state not listed carries none). */
const REASONS_BY_STATE: Record<string, readonly string[] | undefined> = {
  withheld: ['private_location', 'hidden_name_not_listed', 'credential_like_name', 'key_material_extension'],
  unsupported: [
    'untracked_not_a_regular_file',
    'untracked_unreadable',
    'untracked_hardlinked',
    'type_change_not_shown',
    'nested_repository',
  ],
};
export const REVIEW_LINE_KINDS = ['context', 'add', 'del', 'no_newline'] as const;
const COVERS = ['staged', 'unstaged', 'untracked', 'submodule_pointers'];
const DOES_NOT_COVER = [
  'ignored',
  'submodule_contents',
  'binary_content',
  'files_over_bound',
  'private_locations',
];

const SCHEMA = 'di.code_review';
const ANSWER_KEYS = [
  'accounting',
  'comparison',
  'detail',
  'file',
  'file_id',
  'mission_ref',
  'observed_at',
  'overview',
  'problem',
  'schema',
  'snapshot',
  'state',
  'target',
  'task_id',
  'version',
  'view',
];
const ACCOUNTING_KEYS = ['git_bytes', 'git_commands', 'registry_bytes', 'untracked_bytes'];
const TARGET_KEYS = ['attribution', 'branch', 'detached', 'head', 'source_id', 'worktree'];
const COMPARISON_KEYS = [
  'base',
  'candidate',
  'covers',
  'does_not_cover',
  'filters_not_run',
  'mode',
];
const OVERVIEW_KEYS = ['files', 'totals'];
const ENTRY_KEYS = [
  'additions',
  'change',
  'deletions',
  'file_id',
  'old_path',
  'path',
  'reason',
  'staging',
  'state',
];
const FILE_KEYS = [
  ...ENTRY_KEYS,
  'bytes_total',
  'digest_sha256',
  'hunks',
  'lines_retained',
  'lines_total',
  'mode_change',
  'similarity',
  'truncated',
];
const TOTALS_KEYS = ['additions', 'deletions', 'files', 'unshown', 'withheld'];
const HUNK_KEYS = ['lines', 'new_lines', 'new_start', 'old_lines', 'old_start', 'section'];
const LINE_KEYS = ['clipped', 'kind', 'new', 'old', 'text'];

type Json = Record<string, unknown>;
const isObject = (v: unknown): v is Json =>
  typeof v === 'object' && v !== null && !Array.isArray(v);
const exactKeys = (v: Json, keys: string[]): boolean =>
  Object.keys(v).sort().join('\u0000') === [...keys].sort().join('\u0000');
const isInt = (v: unknown): v is number => typeof v === 'number' && Number.isInteger(v);
const isCount = (v: unknown): v is number => isInt(v) && v >= 0 && v <= Number.MAX_SAFE_INTEGER;
const countOrNull = (v: unknown): boolean => v === null || isCount(v);
const isString = (v: unknown, max: number): v is string => typeof v === 'string' && v.length <= max;
const oneOf = (list: readonly string[], v: unknown): boolean =>
  typeof v === 'string' && list.includes(v);
const OID = /^[0-9a-f]{40}$|^[0-9a-f]{64}$/;
const DIGEST = /^[0-9a-f]{64}$/;
const SOURCE_ID = /^[a-z0-9][a-z0-9._-]{0,63}$/;

/** A repository-relative path the listing may name: contained, no NUL or backslash, bounded. */
export function isSafeReviewPath(path: unknown): path is string {
  if (typeof path !== 'string' || path.length === 0 || path.length > MAX_REVIEW_PATH_CHARS) {
    return false;
  }
  if (path.startsWith('/') || path.includes('\u0000') || path.includes('\\')) return false;
  return path.split('/').every((part) => part !== '' && part !== '.' && part !== '..');
}

// ---------------------------------------------------------------------------------------------
// The request

export interface CodeReviewRef {
  missionRef: string;
  memberRef: string;
  taskId: string;
  listing: string;
  /** Present only for one file's diff, and then together with fileId. */
  snapshot?: string;
  fileId?: string;
}

const READ_KEYS = ['type', 'missionRef', 'memberRef', 'taskId', 'listing'];

/** A diCodeReviewRead, EXACTLY: five keys (the overview) or seven (one file); null otherwise. */
export function parseCodeReviewRead(msg: Json): CodeReviewRef | null {
  if (msg.type !== 'diCodeReviewRead') return null;
  const file = 'snapshot' in msg || 'fileId' in msg;
  if (!exactKeys(msg, file ? [...READ_KEYS, 'snapshot', 'fileId'] : READ_KEYS)) return null;
  const query: CodeReviewQuery = {
    missionRef: msg.missionRef as string,
    taskId: msg.taskId as string,
    ...(file ? { snapshot: msg.snapshot as string, fileId: msg.fileId as string } : {}),
  };
  if (!isCodeReviewQuery(query)) return null;
  if (
    typeof msg.memberRef !== 'string' ||
    msg.memberRef.length === 0 ||
    msg.memberRef.length > MAX_MEMBER_REF_CHARS ||
    /[\u0000-\u001f\u007f]/.test(msg.memberRef) ||
    typeof msg.listing !== 'string' ||
    !CODE_REVIEW_GRAMMAR.snapshot.test(msg.listing)
  ) {
    return null;
  }
  return {
    missionRef: query.missionRef,
    memberRef: msg.memberRef,
    taskId: query.taskId,
    listing: msg.listing,
    ...(file ? { snapshot: query.snapshot, fileId: query.fileId } : {}),
  };
}

/** What the backend is asked for a reference (never anything else). */
export function backendQuery(ref: CodeReviewRef): CodeReviewQuery {
  return {
    missionRef: ref.missionRef,
    taskId: ref.taskId,
    ...(ref.snapshot === undefined ? {} : { snapshot: ref.snapshot, fileId: ref.fileId }),
  };
}

// ---------------------------------------------------------------------------------------------
// The offer, and admission against the CURRENT listing

const observedKey = (o: { state: string; value?: unknown }): string =>
  o.state === 'observed' ? `observed:${String(o.value)}` : o.state;

/**
 * The token that identifies WHAT THE CLIENT WAS SHOWN: the assignment (Mission, source, task), the
 * agent (member, name, recorded session, pane, workspace) and the binding. It moves when any of
 * them moves, and is recomputed from the office's listing at admission and again after the read.
 */
export function listingToken(mission: MissionPresentation, member: MemberPresentation): string {
  return crypto
    .createHash('sha256')
    .update(
      JSON.stringify([
        mission.missionRef,
        mission.sourceId,
        mission.nativeId,
        member.taskId,
        member.memberRef,
        member.agent,
        observedKey(member.sessionRecorded),
        observedKey(member.paneId),
        observedKey(member.workspaceId),
        mission.bindingState,
        mission.standing,
      ]),
      'utf8',
    )
    .digest('hex');
}

/**
 * Whether a review is offered for ONE member of ONE Mission, decided from the typed facts the
 * listing carries — never from a name, a path or a label. An ACTIVE ASSIGNMENT is a different
 * question from the evidence library's historical one: a completed Mission is never offered here.
 */
export function reviewOfferFor(
  mission: MissionPresentation,
  member: MemberPresentation,
): ReviewOffer {
  const no = (reason: ReviewUnavailableReason): ReviewOffer => ({
    state: 'unavailable',
    reason,
    taskId: member.taskId,
    listing: null,
  });
  if (mission.sourceKind !== 'mission_core') return no('not_a_mission_core_record');
  if (mission.v2 === null) return no('contract_version_1_office');
  if (!mission.standingKnown || mission.standing !== 'active') return no('assignment_not_active');
  if (mission.bindingState !== 'bound') return no('assignment_not_bound');
  if (member.coverage !== 'direct' || member.chain !== 'direct') {
    return no('member_not_in_direct_task');
  }
  if (!member.presence.live) return no('member_not_live');
  if (member.taskId === null) return no('task_not_observed');
  return {
    state: 'offered',
    reason: null,
    taskId: member.taskId,
    listing: listingToken(mission, member),
  };
}

export type Admission =
  | { ok: true }
  | { ok: false; problem: OfficeReviewProblem; detail: string };

function refuse(problem: OfficeReviewProblem, detail: string): Admission {
  return { ok: false, problem, detail };
}

/** Is this reference offered, for this member, at the listing the client saw — NOW? */
export function admitAgainstView(view: OfficeView, ref: CodeReviewRef): Admission {
  if (view.missions.state !== 'observed') {
    return refuse('review_not_offered', `no Missions are observed (${view.missions.state})`);
  }
  const found = view.missions.value.filter((m) => m.missionRef === ref.missionRef);
  if (found.length === 0) return refuse('mission_not_listed', 'the office does not list it now');
  if (found.length > 1) return refuse('mission_listed_more_than_once', 'listed more than once');
  const mission = found[0];
  if (mission.members.state !== 'observed') {
    return refuse('member_not_listed', `the Mission's members are ${mission.members.state}`);
  }
  const members = mission.members.value.filter((m) => m.memberRef === ref.memberRef);
  if (members.length === 0) return refuse('member_not_listed', 'the agent is not listed now');
  if (members.length > 1) return refuse('member_listed_more_than_once', 'listed more than once');
  const offer = reviewOfferFor(mission, members[0]);
  if (offer.state !== 'offered') {
    return refuse('review_not_offered', `no review is offered for this agent (${offer.reason})`);
  }
  if (offer.taskId !== ref.taskId) {
    return refuse('task_not_the_one_listed', 'the assignment is no longer the task you were shown');
  }
  if (offer.listing !== ref.listing) {
    return refuse('listing_moved', 'the assignment moved since this listing; select it again');
  }
  return { ok: true };
}

// ---------------------------------------------------------------------------------------------
// The backend's answer, re-checked

export interface ReviewTarget {
  sourceId: string;
  worktree: string;
  head: string;
  branch: string | null;
  detached: boolean;
  attribution: 'shared_worktree';
}
export interface ReviewComparison {
  mode: 'head_to_worktree';
  baseKind: 'head_commit';
  baseOid: string;
  candidateKind: 'working_tree';
  covers: string[];
  doesNotCover: string[];
  filtersNotRun: string[];
}
export interface ReviewEntry {
  fileId: string;
  path: string;
  oldPath: string | null;
  change: string;
  staging: string;
  state: string;
  reason: string | null;
  additions: number | null;
  deletions: number | null;
}
export interface ReviewTotals {
  files: number;
  additions: number;
  deletions: number;
  withheld: number;
  unshown: number;
}
export interface ReviewLine {
  kind: string;
  oldNo: number | null;
  newNo: number | null;
  text: string;
  clipped: number;
}
export interface ReviewHunk {
  oldStart: number;
  oldLines: number;
  newStart: number;
  newLines: number;
  section: string | null;
  lines: ReviewLine[];
}
export interface ReviewFile extends ReviewEntry {
  modeChange: { old: string; new: string } | null;
  similarity: number | null;
  hunks: ReviewHunk[];
  truncated: boolean;
  linesTotal: number;
  linesRetained: number;
  bytesTotal: number;
  digestSha256: string | null;
}

export interface ParsedReviewAnswer {
  state: 'observed' | 'refused';
  problem: string | null;
  detail: string | null;
  snapshot: string | null;
  target: ReviewTarget | null;
  comparison: ReviewComparison | null;
  overview: { files: ReviewEntry[]; totals: ReviewTotals } | null;
  file: ReviewFile | null;
  observedAt: number | null;
}

const invalid = (why: string): { ok: false; problem: 'backend_answer_invalid'; detail: string } => ({
  ok: false,
  problem: 'backend_answer_invalid',
  detail: `the backend answer is not exactly the review answer for this reference (${why})`,
});

type Parsed<T> = { ok: true; value: T } | { ok: false; why: string };
const bad = (why: string): { ok: false; why: string } => ({ ok: false, why });

function parseTarget(v: unknown): Parsed<ReviewTarget> {
  if (!isObject(v) || !exactKeys(v, TARGET_KEYS)) return bad('target keys');
  if (
    typeof v.source_id !== 'string' ||
    !SOURCE_ID.test(v.source_id) ||
    !isString(v.worktree, MAX_REVIEW_WORKTREE_CHARS) ||
    v.worktree.length === 0 ||
    typeof v.head !== 'string' ||
    !OID.test(v.head) ||
    typeof v.detached !== 'boolean' ||
    v.attribution !== 'shared_worktree' ||
    (v.branch !== null && (!isString(v.branch, MAX_REVIEW_BRANCH_CHARS) || v.branch === '')) ||
    v.detached !== (v.branch === null)
  ) {
    return bad('target values');
  }
  return {
    ok: true,
    value: {
      sourceId: v.source_id,
      worktree: v.worktree,
      head: v.head,
      branch: v.branch as string | null,
      detached: v.detached,
      attribution: 'shared_worktree',
    },
  };
}

function isStringList(v: unknown, max: number, itemMax: number): v is string[] {
  return (
    Array.isArray(v) && v.length <= max && v.every((x) => typeof x === 'string' && x.length <= itemMax)
  );
}

function parseComparison(v: unknown, head: string): Parsed<ReviewComparison> {
  if (!isObject(v) || !exactKeys(v, COMPARISON_KEYS)) return bad('comparison keys');
  const base = v.base;
  const candidate = v.candidate;
  if (
    v.mode !== 'head_to_worktree' ||
    !isObject(base) ||
    !exactKeys(base, ['kind', 'oid']) ||
    base.kind !== 'head_commit' ||
    base.oid !== head ||
    !isObject(candidate) ||
    !exactKeys(candidate, ['kind']) ||
    candidate.kind !== 'working_tree' ||
    !Array.isArray(v.covers) ||
    v.covers.join() !== COVERS.join() ||
    !Array.isArray(v.does_not_cover) ||
    v.does_not_cover.join() !== DOES_NOT_COVER.join() ||
    !isStringList(v.filters_not_run, MAX_REVIEW_FILTERS, 64)
  ) {
    return bad('comparison values');
  }
  return {
    ok: true,
    value: {
      mode: 'head_to_worktree',
      baseKind: 'head_commit',
      baseOid: head,
      candidateKind: 'working_tree',
      covers: [...COVERS],
      doesNotCover: [...DOES_NOT_COVER],
      filtersNotRun: [...v.filters_not_run],
    },
  };
}

function parseEntry(v: Json, keys: string[]): Parsed<ReviewEntry> {
  if (!exactKeys(v, keys)) return bad('file keys');
  if (
    typeof v.file_id !== 'string' ||
    !CODE_REVIEW_GRAMMAR.fileId.test(v.file_id) ||
    !isSafeReviewPath(v.path) ||
    (v.old_path !== null && !isSafeReviewPath(v.old_path)) ||
    !oneOf(REVIEW_CHANGES, v.change) ||
    !oneOf(REVIEW_STAGINGS, v.staging) ||
    !oneOf(REVIEW_FILE_STATES, v.state) ||
    (v.reason !== null && !oneOf(REVIEW_REASONS, v.reason)) ||
    !countOrNull(v.additions) ||
    !countOrNull(v.deletions) ||
    (v.old_path !== null) !== (v.change === 'renamed')
  ) {
    return bad('file values');
  }
  // A withheld file names no counts and no content; a state carries exactly the reasons it can.
  if (v.state === 'withheld' && (v.additions !== null || v.deletions !== null)) {
    return bad('a withheld file carries counts');
  }
  // A nested repository is ONE new directory git does not enter: no counts, never a tracked change.
  if (v.reason === 'nested_repository' && (v.additions !== null || v.deletions !== null || v.change !== 'untracked')) {
    return bad('a nested repository is one untracked entry with no counts');
  }
  const reasons = REASONS_BY_STATE[v.state as string];
  if (reasons === undefined ? v.reason !== null : !oneOf(reasons, v.reason)) {
    return bad('a file state and its reason do not fit');
  }
  return {
    ok: true,
    value: {
      fileId: v.file_id,
      path: v.path,
      oldPath: v.old_path as string | null,
      change: v.change as string,
      staging: v.staging as string,
      state: v.state as string,
      reason: v.reason as string | null,
      additions: v.additions as number | null,
      deletions: v.deletions as number | null,
    },
  };
}

function parseOverview(v: unknown): Parsed<{ files: ReviewEntry[]; totals: ReviewTotals }> {
  if (!isObject(v) || !exactKeys(v, OVERVIEW_KEYS)) return bad('overview keys');
  if (!Array.isArray(v.files) || v.files.length > MAX_REVIEW_FILES) return bad('file count');
  const files: ReviewEntry[] = [];
  const ids = new Set<string>();
  const paths = new Set<string>();
  for (const raw of v.files) {
    if (!isObject(raw)) return bad('file entry');
    const entry = parseEntry(raw, ENTRY_KEYS);
    if (!entry.ok) return entry;
    if (ids.has(entry.value.fileId) || paths.has(entry.value.path)) return bad('a duplicate file');
    ids.add(entry.value.fileId);
    paths.add(entry.value.path);
    files.push(entry.value);
  }
  const totals = v.totals;
  if (!isObject(totals) || !exactKeys(totals, TOTALS_KEYS)) return bad('totals keys');
  const sum = (pick: (f: ReviewEntry) => number | null): number =>
    files.reduce((n, f) => n + (pick(f) ?? 0), 0);
  const expected: ReviewTotals = {
    files: files.length,
    additions: sum((f) => f.additions),
    deletions: sum((f) => f.deletions),
    withheld: files.filter((f) => f.state === 'withheld').length,
    unshown: files.filter((f) => !SHOWN_STATES.includes(f.state)).length,
  };
  for (const key of TOTALS_KEYS as (keyof ReviewTotals)[]) {
    if (totals[key] !== expected[key]) return bad(`totals.${key} is not the files' sum`);
  }
  return { ok: true, value: { files, totals: expected } };
}

function parseLine(v: unknown): Parsed<ReviewLine> {
  if (!isObject(v) || !exactKeys(v, LINE_KEYS)) return bad('line keys');
  if (
    !oneOf(REVIEW_LINE_KINDS, v.kind) ||
    !isString(v.text, MAX_REVIEW_LINE_CHARS) ||
    !isCount(v.clipped) ||
    !(v.old === null || (isInt(v.old) && v.old >= 0)) ||
    !(v.new === null || (isInt(v.new) && v.new >= 0))
  ) {
    return bad('line values');
  }
  const kind = v.kind as string;
  const shape =
    kind === 'context'
      ? v.old !== null && v.new !== null
      : kind === 'add'
        ? v.old === null && v.new !== null
        : kind === 'del'
          ? v.old !== null && v.new === null
          : v.old === null && v.new === null;
  if (!shape) return bad('line numbers do not fit the line kind');
  return {
    ok: true,
    value: {
      kind,
      oldNo: v.old as number | null,
      newNo: v.new as number | null,
      text: v.text,
      clipped: v.clipped,
    },
  };
}

function parseHunk(v: unknown): Parsed<ReviewHunk> {
  if (!isObject(v) || !exactKeys(v, HUNK_KEYS)) return bad('hunk keys');
  if (
    !isCount(v.old_start) ||
    !isCount(v.old_lines) ||
    !isCount(v.new_start) ||
    !isCount(v.new_lines) ||
    (v.section !== null && !isString(v.section, MAX_REVIEW_SECTION_CHARS)) ||
    !Array.isArray(v.lines) ||
    v.lines.length > MAX_HUNK_ROWS
  ) {
    return bad('hunk values');
  }
  const lines: ReviewLine[] = [];
  for (const raw of v.lines) {
    const line = parseLine(raw);
    if (!line.ok) return line;
    lines.push(line.value);
  }
  return {
    ok: true,
    value: {
      oldStart: v.old_start,
      oldLines: v.old_lines,
      newStart: v.new_start,
      newLines: v.new_lines,
      section: v.section as string | null,
      lines,
    },
  };
}

function parseFile(v: unknown): Parsed<ReviewFile> {
  if (!isObject(v)) return bad('file');
  const entry = parseEntry(v, FILE_KEYS);
  if (!entry.ok) return entry;
  const mode = v.mode_change;
  if (
    mode !== null &&
    !(
      isObject(mode) &&
      exactKeys(mode, ['new', 'old']) &&
      typeof mode.old === 'string' &&
      /^[0-7]{6}$/.test(mode.old) &&
      typeof mode.new === 'string' &&
      /^[0-7]{6}$/.test(mode.new)
    )
  ) {
    return bad('mode change');
  }
  if (
    !(v.similarity === null || (isInt(v.similarity) && v.similarity >= 0 && v.similarity <= 100)) ||
    typeof v.truncated !== 'boolean' ||
    !isCount(v.lines_total) ||
    !isCount(v.lines_retained) ||
    !isCount(v.bytes_total) ||
    !(v.digest_sha256 === null || (typeof v.digest_sha256 === 'string' && DIGEST.test(v.digest_sha256))) ||
    !Array.isArray(v.hunks) ||
    v.hunks.length > MAX_REVIEW_FILE_LINES
  ) {
    return bad('file detail values');
  }
  const hunks: ReviewHunk[] = [];
  for (const raw of v.hunks) {
    const hunk = parseHunk(raw);
    if (!hunk.ok) return hunk;
    hunks.push(hunk.value);
  }
  // The arithmetic of the answer against itself.
  let retained = 0;
  let chars = 0;
  let adds = 0;
  let dels = 0;
  for (const hunk of hunks) {
    let context = 0;
    let a = 0;
    let d = 0;
    for (const line of hunk.lines) {
      chars += line.text.length;
      if (line.kind === 'no_newline') continue;
      retained += 1;
      if (line.kind === 'context') context += 1;
      else if (line.kind === 'add') a += 1;
      else d += 1;
    }
    adds += a;
    dels += d;
    if (!v.truncated && (context + d !== hunk.oldLines || context + a !== hunk.newLines)) {
      return bad('a hunk does not add up to its own header');
    }
  }
  if (
    retained !== v.lines_retained ||
    retained > MAX_REVIEW_FILE_LINES ||
    chars > MAX_REVIEW_FILE_CHARS ||
    v.lines_total < v.lines_retained ||
    v.truncated !== v.lines_total > v.lines_retained
  ) {
    return bad('the retained lines and the totals disagree');
  }
  if (
    !v.truncated &&
    SHOWN_STATES.includes(entry.value.state) &&
    (adds !== entry.value.additions || dels !== entry.value.deletions)
  ) {
    return bad('the lines and the counts disagree');
  }
  if (!SHOWN_STATES.includes(entry.value.state) && hunks.length > 0) {
    return bad('content without a state that shows content');
  }
  return {
    ok: true,
    value: {
      ...entry.value,
      modeChange: mode === null ? null : { old: (mode as Json).old as string, new: (mode as Json).new as string },
      similarity: v.similarity as number | null,
      hunks,
      truncated: v.truncated,
      linesTotal: v.lines_total,
      linesRetained: v.lines_retained,
      bytesTotal: v.bytes_total,
      digestSha256: v.digest_sha256 as string | null,
    },
  };
}

/** The backend's answer, re-checked; anything not exactly right is `backend_answer_invalid`. */
export function parseCodeReviewAnswer(
  body: unknown,
  ref: CodeReviewRef,
):
  | { ok: true; answer: ParsedReviewAnswer }
  | { ok: false; problem: 'backend_answer_invalid'; detail: string } {
  if (!isObject(body) || !exactKeys(body, ANSWER_KEYS)) return invalid('keys');
  if (body.schema !== SCHEMA || body.version !== 1) return invalid('schema');
  const isFile = ref.fileId !== undefined;
  if (
    body.view !== (isFile ? 'file' : 'overview') ||
    body.mission_ref !== ref.missionRef ||
    body.task_id !== ref.taskId ||
    body.file_id !== (ref.fileId ?? null)
  ) {
    return invalid('reference');
  }
  const accounting = body.accounting;
  if (
    !isObject(accounting) ||
    !exactKeys(accounting, ACCOUNTING_KEYS) ||
    !ACCOUNTING_KEYS.every((k) => isCount(accounting[k]))
  ) {
    return invalid('accounting');
  }
  if (!(body.observed_at === null || isInt(body.observed_at))) return invalid('observed_at');
  const detail = body.detail;
  if (detail !== null && !isString(detail, MAX_DETAIL_CHARS)) return invalid('detail');
  const observedAt = body.observed_at as number | null;
  if (body.state === 'refused') {
    if (
      body.target !== null ||
      body.comparison !== null ||
      body.overview !== null ||
      body.file !== null ||
      typeof detail !== 'string' ||
      !oneOf(CODE_REVIEW_PROBLEMS, body.problem) ||
      body.snapshot !== (ref.snapshot ?? null)
    ) {
      return invalid('refusal');
    }
    return {
      ok: true,
      answer: {
        state: 'refused',
        problem: body.problem as string,
        detail,
        snapshot: ref.snapshot ?? null,
        target: null,
        comparison: null,
        overview: null,
        file: null,
        observedAt,
      },
    };
  }
  if (body.state !== 'observed' || body.problem !== null || detail !== null) {
    return invalid('state');
  }
  if (typeof body.snapshot !== 'string' || !CODE_REVIEW_GRAMMAR.snapshot.test(body.snapshot)) {
    return invalid('snapshot');
  }
  if (isFile && body.snapshot !== ref.snapshot) return invalid('snapshot is not the one asked');
  const target = parseTarget(body.target);
  if (!target.ok) return invalid(target.why);
  const comparison = parseComparison(body.comparison, target.value.head);
  if (!comparison.ok) return invalid(comparison.why);
  let overview: ParsedReviewAnswer['overview'] = null;
  let file: ReviewFile | null = null;
  if (isFile) {
    if (body.overview !== null) return invalid('a file answer carries an overview');
    const parsed = parseFile(body.file);
    if (!parsed.ok) return invalid(parsed.why);
    if (parsed.value.fileId !== ref.fileId) return invalid('file is not the one asked');
    file = parsed.value;
  } else {
    if (body.file !== null) return invalid('an overview answer carries a file');
    const parsed = parseOverview(body.overview);
    if (!parsed.ok) return invalid(parsed.why);
    overview = parsed.value;
  }
  return {
    ok: true,
    answer: {
      state: 'observed',
      problem: null,
      detail: null,
      snapshot: body.snapshot,
      target: target.value,
      comparison: comparison.value,
      overview,
      file,
      observedAt,
    },
  };
}

// ---------------------------------------------------------------------------------------------
// What the client receives

function envelope(ref: CodeReviewRef): Pick<
  DiCodeReview,
  'missionRef' | 'memberRef' | 'taskId' | 'listing' | 'snapshot' | 'fileId' | 'view'
> {
  return {
    missionRef: ref.missionRef,
    memberRef: ref.memberRef,
    taskId: ref.taskId,
    listing: ref.listing,
    snapshot: ref.snapshot ?? null,
    fileId: ref.fileId ?? null,
    view: ref.fileId === undefined ? 'overview' : 'file',
  };
}

/**
 * `text` cut to at most `max` UTF-16 code units, never inside a character: a character outside the
 * BMP is two units, and half of one is not text.
 */
function clipUnits(text: string, max: number): string {
  if (text.length <= max) return text;
  const last = text.charCodeAt(max - 1);
  const next = text.charCodeAt(max);
  const splitsAPair = last >= 0xd800 && last <= 0xdbff && next >= 0xdc00 && next <= 0xdfff;
  return text.slice(0, splitsAPair ? max - 1 : max);
}

/** A refusal the office decided: no target, no comparison, no content. */
export function officeRefusal(
  ref: CodeReviewRef,
  problem: OfficeReviewProblem,
  detail: string,
): DiCodeReview {
  return {
    type: 'diCodeReview',
    ...envelope(ref),
    state: 'refused',
    decidedBy: 'office',
    problem,
    detail: clipUnits(detail, MAX_DETAIL_CHARS),
    target: null,
    comparison: null,
    overview: null,
    file: null,
    observedAt: null,
  };
}

/** The backend's re-checked answer, relayed as data (copied, never shared). */
export function backendMessage(ref: CodeReviewRef, answer: ParsedReviewAnswer): DiCodeReview {
  return {
    type: 'diCodeReview',
    ...envelope(ref),
    snapshot: answer.snapshot,
    state: answer.state,
    decidedBy: 'backend',
    problem: answer.problem,
    detail: answer.detail,
    target: answer.target === null ? null : { ...answer.target },
    comparison:
      answer.comparison === null
        ? null
        : {
            ...answer.comparison,
            covers: [...answer.comparison.covers],
            doesNotCover: [...answer.comparison.doesNotCover],
            filtersNotRun: [...answer.comparison.filtersNotRun],
          },
    overview:
      answer.overview === null
        ? null
        : {
            files: answer.overview.files.map((f) => ({ ...f })),
            totals: { ...answer.overview.totals },
          },
    file:
      answer.file === null
        ? null
        : {
            ...answer.file,
            modeChange: answer.file.modeChange === null ? null : { ...answer.file.modeChange },
            hunks: answer.file.hunks.map((h) => ({ ...h, lines: h.lines.map((l) => ({ ...l })) })),
          },
    observedAt: answer.observedAt,
  };
}
