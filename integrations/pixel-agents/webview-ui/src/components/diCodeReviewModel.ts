/**
 * Item 3 (original 20) — the live code review, webview model.
 *
 * Everything the modal DECIDES lives here as pure functions so it is testable without a DOM: the
 * session (what is open, what was asked, what is current), the file tree and its search, the plain
 * words for every state and refusal, the side-by-side pairing of a hunk, a best-effort syntax
 * tokenizer, the visible form of control characters, the row windowing a large diff needs, and the
 * contrast arithmetic the stylesheet is checked against.
 *
 * Nothing here reads, writes or fetches anything. Patch text is UNTRUSTED: it passes through this
 * module only as strings that the component renders as React text nodes — never as markup.
 */

import type {
  DiCodeReview,
  DiCodeReviewComparison,
  DiCodeReviewEntry,
  DiCodeReviewFile,
  DiCodeReviewHunk,
  DiCodeReviewLine,
  DiCodeReviewOffer,
  DiCodeReviewTarget,
  DiMember,
  DiOfficeState,
} from '../../../core/src/messages.js';

// ---------------------------------------------------------------------------------------------
// Bounds (webview side). The backend's bounds are pinned in server/__tests__/diCodeReview.test.ts;
// these are this view's own, and asserted by exact value in test/diCodeReviewModel.test.ts.

/** Text sizes the reader can choose, in CSS pixels. */
export const TEXT_SIZES = [11, 12, 13, 14, 16, 18, 20, 24] as const;
export const DEFAULT_TEXT_SIZE_INDEX = 3;
/** A diff row is this many times the text size tall (constant: the window needs no measuring). */
export const ROW_HEIGHT_RATIO = 1.55;
/** Rows rendered beyond the visible window on each side. */
export const WINDOW_OVERSCAN_ROWS = 24;
/** An overview older than this is called out as possibly out of date. */
export const REVIEW_AGING_AFTER_SECONDS = 60;
/** Below this viewport width the file tree and the diff are one view at a time. */
export const NARROW_VIEWPORT_PX = 820;
/** Below this width side-by-side is not offered (two columns would be unreadable). */
export const SPLIT_MIN_VIEWPORT_PX = 1040;
/** Width of a tab stop, in characters. */
export const TAB_WIDTH = 4;
/** A single control-character placeholder is at most this long (it replaces ONE character). */
export const MAX_PLACEHOLDER_CHARS = 10;

// ---------------------------------------------------------------------------------------------
// The session

/** What the client was shown and may ask about (from the member's offer in the office state). */
export interface ReviewTargetRef {
  missionRef: string;
  memberRef: string;
  taskId: string;
  listing: string;
  /** For display only: the agent the review was opened from. */
  agentName: string;
}

export type ReviewRequest =
  | { kind: 'overview' }
  | { kind: 'file'; fileId: string; snapshot: string; path: string };

export interface ReviewSession {
  /** What is open: null when the modal is closed. */
  target: ReviewTargetRef | null;
  /** The request in flight (one at a time), or null. */
  pending: ReviewRequest | null;
  /** The latest overview that was OBSERVED, with the local time it arrived. */
  overview: { answer: DiCodeReview; receivedAtMs: number } | null;
  /** The file the reader selected (by path, so it survives a new snapshot). */
  selectedPath: string | null;
  /** The latest file view for the selected file, against the snapshot it belongs to. */
  file: { answer: DiCodeReview; receivedAtMs: number } | null;
  /** The latest refusal, shown beside whatever is still current. */
  refusal: DiCodeReview | null;
  /** Why content was cleared (shown where the content was): never a stale diff labelled current. */
  cleared: ClearedReason | null;
  /** The workspace is known to have moved past the overview that is on screen. */
  stale: boolean;
  /** A new overview was asked for (refresh); the old content stays, labelled, until it arrives. */
  refresh: boolean;
}

export type ClearedReason =
  | 'connection_lost'
  | 'reconnected_awaiting_state'
  | 'office_expired'
  | 'office_not_live'
  | 'assignment_changed'
  | 'agent_not_live'
  | 'agent_gone';

export const CLOSED_SESSION: ReviewSession = {
  target: null,
  pending: null,
  overview: null,
  selectedPath: null,
  file: null,
  refusal: null,
  cleared: null,
  stale: false,
  refresh: false,
};

export type ReviewEvent =
  | { type: 'open'; target: ReviewTargetRef }
  | { type: 'close' }
  | { type: 'asked'; request: ReviewRequest }
  | { type: 'answer'; answer: DiCodeReview; nowMs: number }
  | { type: 'select'; path: string | null }
  | { type: 'clear'; reason: ClearedReason }
  | { type: 'refresh' }
  | { type: 'retry' }
  | { type: 'moved' };

const sameTarget = (a: ReviewTargetRef, answer: DiCodeReview): boolean =>
  a.missionRef === answer.missionRef &&
  a.memberRef === answer.memberRef &&
  a.taskId === answer.taskId &&
  a.listing === answer.listing;

/** Does this answer belong to the request now in flight (never to an older or another one)? */
export function answersPending(session: ReviewSession, answer: DiCodeReview): boolean {
  const { target, pending } = session;
  if (target === null || pending === null || !sameTarget(target, answer)) return false;
  if (pending.kind === 'overview') return answer.view === 'overview' && answer.fileId === null;
  return (
    answer.view === 'file' &&
    answer.fileId === pending.fileId &&
    answer.snapshot === pending.snapshot
  );
}

/**
 * The one reducer of the review session. Rules that matter:
 * - an answer is applied only if it answers the request in flight for THIS target;
 * - an observed overview REPLACES the earlier one and the file view with it (a file view belongs to
 *   the snapshot it was read in);
 * - a refusal never erases what is still current, except that `snapshot_moved` and
 *   `workspace_moved_during_read` mark the overview stale (it stays visible, labelled);
 * - when the connection, the office state or the assignment cannot be vouched for, ALL content is
 *   cleared at once and the reason is kept: a stale diff is never labelled current.
 */
export function reduceSession(session: ReviewSession, event: ReviewEvent): ReviewSession {
  switch (event.type) {
    case 'open':
      return { ...CLOSED_SESSION, target: event.target };
    case 'close':
      return CLOSED_SESSION;
    case 'asked':
      return session.target === null
        ? session
        : {
            ...session,
            pending: event.request,
            refresh: event.request.kind === 'overview' ? false : session.refresh,
          };
    case 'retry':
      return session.target === null || session.cleared !== null || session.refusal === null
        ? session
        : { ...session, refusal: null };
    case 'refresh':
      return session.target === null || session.cleared !== null
        ? session
        : { ...session, refresh: true, refusal: null };
    case 'select':
      return session.target === null
        ? session
        : { ...session, selectedPath: event.path, file: null, refusal: null };
    case 'moved':
      return session.target === null ? session : { ...session, stale: true };
    case 'clear':
      return session.target === null
        ? session
        : {
            ...session,
            pending: null,
            overview: null,
            file: null,
            refusal: null,
            stale: false,
            refresh: false,
            cleared: event.reason,
          };
    case 'answer': {
      if (!answersPending(session, event.answer)) return session;
      const answer = event.answer;
      const settled: ReviewSession = { ...session, pending: null, cleared: null };
      if (answer.state === 'refused') {
        const moved =
          answer.problem === 'snapshot_moved' || answer.problem === 'workspace_moved_during_read';
        return { ...settled, refusal: answer, stale: moved ? true : settled.stale };
      }
      if (
        answer.view === 'file' &&
        session.pending?.kind === 'file' &&
        session.pending.path !== session.selectedPath
      ) {
        // The reader moved to another file while this one was read: its answer is not shown under
        // the file now selected. The next request asks for the one that is.
        return settled;
      }
      if (answer.view === 'overview') {
        return {
          ...settled,
          overview: { answer, receivedAtMs: event.nowMs },
          file: null,
          refusal: null,
          stale: false,
          refresh: false,
        };
      }
      return { ...settled, file: { answer, receivedAtMs: event.nowMs }, refusal: null };
    }
  }
}

/** Refusals that only mean "not this instant": the session asks again by itself. */
const RETRYABLE_AT_ONCE = ['read_rate_limited', 'read_in_progress', 'review_read_in_progress'];

export function isRetryable(refusal: DiCodeReview | null): boolean {
  return refusal !== null && refusal.problem !== null && RETRYABLE_AT_ONCE.includes(refusal.problem);
}

/** The files of the current overview (empty when none). */
export function overviewFiles(session: ReviewSession): DiCodeReviewEntry[] {
  return session.overview?.answer.overview?.files ?? [];
}

/** The listed entry for a path in the current overview, if any. */
export function entryForPath(session: ReviewSession, path: string | null): DiCodeReviewEntry | null {
  if (path === null) return null;
  return overviewFiles(session).find((f) => f.path === path) ?? null;
}

/**
 * The next request the session should make, or null: the overview when there is none and nothing
 * is pending; else the selected file when its view is missing. Pure: the component asks, then
 * sends exactly what this returns.
 */
export function nextRequest(session: ReviewSession): ReviewRequest | null {
  if (session.target === null || session.pending !== null || session.cleared !== null) return null;
  if (session.refresh) return { kind: 'overview' };
  if (session.overview === null) return session.refusal === null ? { kind: 'overview' } : null;
  const entry = entryForPath(session, session.selectedPath);
  if (entry === null || session.file !== null || session.refusal !== null) return null;
  if (!canOpen(entry)) return null;
  return {
    kind: 'file',
    fileId: entry.fileId,
    snapshot: session.overview.answer.snapshot!,
    path: entry.path,
  };
}

/**
 * Is a review still offered for the target, by the office state NOW? The modal clears itself the
 * moment this is false — the assignment ended, the agent left, or the listing token moved.
 */
export function offerStillHolds(
  target: ReviewTargetRef,
  offer: DiCodeReviewOffer | null | undefined,
): boolean {
  return (
    offer !== null &&
    offer !== undefined &&
    offer.state === 'offered' &&
    offer.taskId === target.taskId &&
    offer.listing === target.listing
  );
}

// ---------------------------------------------------------------------------------------------
// The tree

export interface TreeFolder {
  kind: 'folder';
  /** The full path of the folder (single-child chains are joined: `a/b/c`). */
  path: string;
  name: string;
  children: TreeNode[];
  additions: number;
  deletions: number;
  files: number;
}
export interface TreeLeaf {
  kind: 'file';
  path: string;
  name: string;
  entry: DiCodeReviewEntry;
}
export type TreeNode = TreeFolder | TreeLeaf;

/** The folder/file tree of a listing, sorted folders-first then by name, single-child chains joined. */
export function buildTree(files: readonly DiCodeReviewEntry[]): TreeNode[] {
  interface Raw {
    name: string;
    path: string;
    folders: Map<string, Raw>;
    leaves: TreeLeaf[];
  }
  const root: Raw = { name: '', path: '', folders: new Map(), leaves: [] };
  for (const entry of files) {
    const parts = entry.path.split('/');
    let at = root;
    for (let i = 0; i < parts.length - 1; i += 1) {
      const path = parts.slice(0, i + 1).join('/');
      let next = at.folders.get(parts[i]);
      if (next === undefined) {
        next = { name: parts[i], path, folders: new Map(), leaves: [] };
        at.folders.set(parts[i], next);
      }
      at = next;
    }
    at.leaves.push({ kind: 'file', path: entry.path, name: parts[parts.length - 1], entry });
  }
  const collect = (raw: Raw): TreeNode[] => {
    const folders = [...raw.folders.values()]
      .sort((a, b) => compareNames(a.name, b.name))
      .map((f): TreeFolder => {
        let node = f;
        let name = f.name;
        // A folder holding only one folder and no file is shown as one line: a/b/c.
        while (node.leaves.length === 0 && node.folders.size === 1) {
          node = [...node.folders.values()][0];
          name = `${name}/${node.name}`;
        }
        const children = collect(node);
        const leaves = flatLeaves(children);
        return {
          kind: 'folder',
          path: node.path,
          name,
          children,
          additions: leaves.reduce((n, l) => n + (l.entry.additions ?? 0), 0),
          deletions: leaves.reduce((n, l) => n + (l.entry.deletions ?? 0), 0),
          files: leaves.length,
        };
      });
    const leaves = [...raw.leaves].sort((a, b) => compareNames(a.name, b.name));
    return [...folders, ...leaves];
  };
  return collect(root);
}

const compareNames = (a: string, b: string): number =>
  a.toLowerCase() < b.toLowerCase() ? -1 : a.toLowerCase() > b.toLowerCase() ? 1 : a < b ? -1 : a > b ? 1 : 0;

function flatLeaves(nodes: readonly TreeNode[]): TreeLeaf[] {
  return nodes.flatMap((n) => (n.kind === 'file' ? [n] : flatLeaves(n.children)));
}

/** Files whose path (or former path) contains every word of the query, case-insensitively. */
export function filterFiles(
  files: readonly DiCodeReviewEntry[],
  query: string,
): DiCodeReviewEntry[] {
  const words = query.toLowerCase().split(/\s+/).filter((w) => w !== '');
  if (words.length === 0) return [...files];
  return files.filter((f) => {
    const haystack = `${f.path} ${f.oldPath ?? ''}`.toLowerCase();
    return words.every((w) => haystack.includes(w));
  });
}

export interface VisibleRow {
  node: TreeNode;
  depth: number;
  expanded: boolean | null;
}

/**
 * The rows the tree shows, in order. A search opens every folder that holds a match (the reader
 * should not have to hunt); otherwise a folder is open unless the reader collapsed it.
 */
export function visibleRows(
  tree: readonly TreeNode[],
  collapsed: ReadonlySet<string>,
  searching: boolean,
): VisibleRow[] {
  const rows: VisibleRow[] = [];
  const walk = (nodes: readonly TreeNode[], depth: number): void => {
    for (const node of nodes) {
      if (node.kind === 'file') {
        rows.push({ node, depth, expanded: null });
        continue;
      }
      const open = searching || !collapsed.has(node.path);
      rows.push({ node, depth, expanded: open });
      if (open) walk(node.children, depth + 1);
    }
  };
  walk(tree, 0);
  return rows;
}

/** Previous/next file path in tree order (wrapping nowhere), for the `[` and `]` keys. */
export function neighbourPath(
  rows: readonly VisibleRow[],
  current: string | null,
  step: 1 | -1,
): string | null {
  const files = rows.filter((r) => r.node.kind === 'file').map((r) => r.node.path);
  if (files.length === 0) return null;
  const at = current === null ? -1 : files.indexOf(current);
  if (at === -1) return step === 1 ? files[0] : files[files.length - 1];
  const next = at + step;
  return next < 0 || next >= files.length ? files[at] : files[next];
}

// ---------------------------------------------------------------------------------------------
// Plain words

export interface StatusGlyph {
  letter: string;
  label: string;
  tone: 'added' | 'removed' | 'changed' | 'renamed' | 'new' | 'conflict' | 'neutral';
}

export function changeGlyph(change: string): StatusGlyph {
  switch (change) {
    case 'added':
      return { letter: 'A', label: 'added', tone: 'added' };
    case 'deleted':
      return { letter: 'D', label: 'deleted', tone: 'removed' };
    case 'modified':
      return { letter: 'M', label: 'modified', tone: 'changed' };
    case 'renamed':
      return { letter: 'R', label: 'renamed', tone: 'renamed' };
    case 'untracked':
      return { letter: 'U', label: 'new file, not yet tracked by git', tone: 'new' };
    case 'type_changed':
      return { letter: 'T', label: 'changed kind (for example file to link)', tone: 'changed' };
    case 'conflicted':
      return { letter: '!', label: 'merge conflict', tone: 'conflict' };
    default:
      return { letter: '?', label: change, tone: 'neutral' };
  }
}

export function stagingWords(staging: string): string {
  switch (staging) {
    case 'staged':
      return 'staged';
    case 'unstaged':
      return 'not staged';
    case 'both':
      return 'staged, then changed again';
    case 'untracked':
      return 'new, not tracked';
    case 'conflicted':
      return 'in conflict';
    default:
      return staging;
  }
}

export interface StateWords {
  badge: string;
  title: string;
  body: string;
  tone: 'ok' | 'info' | 'warn' | 'blocked';
}

const REASON_WORDS: Record<string, string> = {
  private_location:
    'It is in a private location (Herdr’s own state, git’s own directory, agent state or credential storage). Its name is listed; its content is never read or shown.',
  hidden_name_not_listed:
    'It is under a hidden name this view does not list. Its name is shown; its content is never read or shown.',
  credential_like_name:
    'Its name looks like credentials or secrets. Its name is shown; its content is never read or shown.',
  key_material_extension:
    'Its extension marks key material. Its name is shown; its content is never read or shown.',
  untracked_not_a_regular_file:
    'It is new and is not a plain file (a link, a device or a pipe), so it is not followed or read.',
  untracked_unreadable: 'It is new and could not be read, so there is nothing to show.',
  untracked_hardlinked:
    'It is new and has more than one hard link, so it is not read (it may be the same file as one elsewhere).',
  type_change_not_shown:
    'It changed kind (for example from a file to a link). git describes that as a removal and an addition of the same path; this view does not show it.',
  nested_repository:
    'It is a separate git repository inside this workspace that this repository does not track. Its files are not part of these changes and are not read.',
};

/**
 * The one limit of how a withheld TRACKED file's change is known. The file is never opened, so that it changed
 * is read from the metadata the repository recorded for it and compared with the file's stat, at the second the
 * repository records: a same-size edit within that second is not seen. (It under-reports; it never reveals.)
 */
export const WITHHELD_CHANGE_WORDS =
  'That it changed is judged from metadata the repository recorded for it (its mode, its size and the second it was recorded), never from its content, so an edit of the same size within that second may not be seen.';

/**
 * The plain words for a file's state, with its reason when it has one. `change` is the kind of change the listing
 * gave the file: a withheld file that git tracks says how its change is known (see WITHHELD_CHANGE_WORDS).
 */
export function stateWords(state: string, reason: string | null, change: string | null = null): StateWords {
  switch (state) {
    case 'text':
      return { badge: '', title: 'Text', body: '', tone: 'ok' };
    case 'empty':
      return {
        badge: 'empty',
        title: 'Empty file',
        body: 'The file has no content, so there are no lines to show.',
        tone: 'info',
      };
    case 'binary':
      return {
        badge: 'binary',
        title: 'Binary file',
        body: 'git treats this file as binary. It changed, but its bytes are not shown as text and no line counts exist.',
        tone: 'info',
      };
    case 'not_utf8':
      return {
        badge: 'not UTF-8',
        title: 'Not valid UTF-8 text',
        body: 'This file is not valid UTF-8, so its characters cannot be shown faithfully. It is not guessed at or shown with replacement characters.',
        tone: 'warn',
      };
    case 'oversized':
      return {
        badge: 'too large',
        title: 'Too large to read here',
        body: 'This new file is larger than the 64 KiB this view reads for a file git does not yet track. It is listed, but not shown.',
        tone: 'warn',
      };
    case 'withheld':
      return {
        badge: 'withheld',
        title: 'Withheld',
        body:
          ((reason !== null && REASON_WORDS[reason]) || 'Its content is never read or shown.') +
          (change !== null && change !== 'untracked' ? ` ${WITHHELD_CHANGE_WORDS}` : ''),
        tone: 'blocked',
      };
    case 'unsupported':
      return {
        badge: 'not shown',
        title: 'Not shown',
        body: (reason !== null && REASON_WORDS[reason]) || 'This view does not show this kind of change.',
        tone: 'warn',
      };
    case 'no_content_diff':
      return {
        badge: 'no text change',
        title: 'No change to the text',
        body: 'Git reports this file as changed, but no line differs. For example only its permissions changed.',
        tone: 'info',
      };
    case 'submodule':
      return {
        badge: 'submodule',
        title: 'Submodule',
        body: 'This is a submodule: the repository records the commit it points to, and that recorded commit changed. Only the pointer is compared (the old and the new commit, below). What is inside the submodule is not read, so work there that is not committed does not appear.',
        tone: 'info',
      };
    case 'conflicted':
      return {
        badge: 'conflict',
        title: 'Merge conflict',
        body: 'This file is in an unresolved merge conflict. It is listed so it is not missed; resolve it in the worktree.',
        tone: 'blocked',
      };
    default:
      return { badge: state, title: state, body: '', tone: 'info' };
  }
}

export interface ProblemWords {
  title: string;
  body: string;
  /** Whether asking again may help (a moved workspace) or the cause must change first. */
  retry: boolean;
}

const PROBLEM_WORDS: Record<string, ProblemWords> = {
  // refused by the office
  mission_not_listed: {
    title: 'The assignment is no longer listed',
    body: 'The office no longer lists this Mission, so there is nothing current to read.',
    retry: false,
  },
  mission_listed_more_than_once: {
    title: 'The assignment is listed more than once',
    body: 'The office lists this Mission twice, so which workspace is meant cannot be told. Nothing was read.',
    retry: false,
  },
  member_not_listed: {
    title: 'The agent is no longer listed',
    body: 'The agent this review was opened from is no longer in the Mission’s roster.',
    retry: false,
  },
  member_listed_more_than_once: {
    title: 'The agent is listed more than once',
    body: 'The roster lists this agent twice, so the review cannot tell which one is meant. Nothing was read.',
    retry: false,
  },
  review_not_offered: {
    title: 'A review is not offered for this agent now',
    body: 'The assignment is no longer active work in a bound workspace, or the agent is not live.',
    retry: false,
  },
  listing_moved: {
    title: 'The assignment changed',
    body: 'The assignment or the agent changed since you opened this review. Select the agent again.',
    retry: false,
  },
  task_not_the_one_listed: {
    title: 'The task changed',
    body: 'This agent now works on another task. A review of the old one is not shown as the new one.',
    retry: false,
  },
  listing_moved_during_read: {
    title: 'The assignment changed while reading',
    body: 'The assignment or the agent changed while the workspace was being read, so the answer was discarded.',
    retry: true,
  },
  read_in_progress: {
    title: 'Another read is in progress',
    body: 'One read at a time. Try again in a moment.',
    retry: true,
  },
  read_rate_limited: {
    title: 'Reading too quickly',
    body: 'Reads are limited to a few per second. Try again in a moment.',
    retry: true,
  },
  reviews_not_supported_by_this_backend: {
    title: 'This office cannot read workspaces',
    body: 'This office is showing a preview or a substitute backend that has no code review route.',
    retry: false,
  },
  backend_discovery_unavailable: {
    title: 'The backend cannot be found',
    body: 'The office could not find the catalog backend to ask. Nothing was read.',
    retry: true,
  },
  backend_unavailable: {
    title: 'The backend did not answer',
    body: 'The catalog backend could not be reached or did not answer in time.',
    retry: true,
  },
  backend_refused_request: {
    title: 'The backend refused the request',
    body: 'The catalog backend answered with an error instead of a review.',
    retry: true,
  },
  backend_answer_invalid: {
    title: 'The answer could not be trusted',
    body: 'The backend’s answer was not exactly the shape and arithmetic a review has, so none of it is shown.',
    retry: true,
  },
  // refused by the backend
  review_needs_contract_version_2: {
    title: 'The backend does not support reviews',
    body: 'This backend was started with the older query contract, which corroborates no assignment workspace.',
    retry: false,
  },
  owner_shut_down: {
    title: 'The backend is shutting down',
    body: 'It takes no new work.',
    retry: false,
  },
  review_read_in_progress: {
    title: 'Another read is in progress',
    body: 'The backend reads one workspace at a time. Try again in a moment.',
    retry: true,
  },
  no_fresh_catalog_answer: {
    title: 'The backend has no current picture of the assignment',
    body: 'Its last catalog answer is missing or too old to show the assignment is active. Nothing was read.',
    retry: true,
  },
  assignment_not_a_mission_core_record: {
    title: 'Not a supported assignment',
    body: 'Only a Mission Core assignment names a workspace this view can read.',
    retry: false,
  },
  assignment_not_active: {
    title: 'The assignment is not active',
    body: 'A live review is offered only for work in progress. A completed or paused assignment is not read here.',
    retry: false,
  },
  assignment_binding_not_bound: {
    title: 'The workspace is not confirmed',
    body: 'The backend cannot show that this assignment is running in one confirmed workspace now.',
    retry: false,
  },
  assignment_task_changed: {
    title: 'The task changed',
    body: 'The Mission now works on another task than the one you opened. Open the review again.',
    retry: false,
  },
  assignment_task_or_workspace_unresolved: {
    title: 'The workspace is not resolved',
    body: 'The assignment’s task and workspace are not recorded consistently, so no workspace is read.',
    retry: false,
  },
  registry_unreadable: {
    title: 'The source registry could not be read',
    body: 'The backend could not check that the assignment’s source is still registered.',
    retry: true,
  },
  source_not_registered: {
    title: 'The assignment’s source is no longer registered',
    body: 'It was removed after the last catalog answer, so nothing is read.',
    retry: false,
  },
  source_registration_changed: {
    title: 'The assignment’s source changed',
    body: 'Its registration is not what the catalog listed, so nothing is read.',
    retry: true,
  },
  workspace_not_a_readable_directory: {
    title: 'The workspace is not a readable folder',
    body: 'The folder the assignment names is not an absolute, resolved, existing directory now.',
    retry: true,
  },
  workspace_is_not_a_worktree_root: {
    title: 'The workspace is not the root of a git worktree',
    body: 'git names a different root for the folder the assignment names, so what would be compared is unclear.',
    retry: false,
  },
  no_base_commit: {
    title: 'Nothing to compare with yet',
    body: 'The workspace has no commit (an unborn branch), so there is no base to compare against.',
    retry: false,
  },
  git_read_failed: {
    title: 'git could not read the workspace',
    body: 'git refused or failed. For example the folder is not a repository, or another user owns it.',
    retry: true,
  },
  git_read_deadline: {
    title: 'git took too long',
    body: 'A read passed its time limit and was stopped. The workspace may be very large or busy.',
    retry: true,
  },
  inventory_over_bound: {
    title: 'Too much changed to list',
    body: 'The list of changes is larger than this view reads. It is refused, never cut short.',
    retry: false,
  },
  too_many_changed_files: {
    title: 'Too many changed files',
    body: 'More files are changed than this view lists at once. It is refused rather than show part of the list as if it were all.',
    retry: false,
  },
  path_not_representable: {
    title: 'A changed path cannot be shown',
    body: 'A changed path is not valid text, so the listing cannot name it. It is refused, not skipped.',
    retry: false,
  },
  inventory_inconsistent: {
    title: 'The listing did not agree with itself',
    body: 'git’s lists of changes disagreed, so none of it is trusted.',
    retry: true,
  },
  review_read_budget_exhausted: {
    title: 'Read budget used up',
    body: 'The new files already read for this view used its limit. Nothing more is read.',
    retry: false,
  },
  snapshot_moved: {
    title: 'The workspace changed since this overview',
    body: 'The files have changed since the overview you are looking at, so this file is not read against it. Refresh to see the current changes.',
    retry: true,
  },
  file_not_in_snapshot: {
    title: 'That file is not in this overview',
    body: 'The overview does not list the file asked for.',
    retry: true,
  },
  workspace_moved_during_read: {
    title: 'The workspace changed while reading',
    body: 'The workspace kept changing while it was read, so the answer was discarded rather than shown as if it were one moment.',
    retry: true,
  },
  assignment_changed_during_read: {
    title: 'The assignment changed while reading',
    body: 'The assignment, its workspace or its source changed while the workspace was read, so the answer was discarded.',
    retry: true,
  },
  file_diff_over_bound: {
    title: 'This file’s diff is too large',
    body: 'It is larger than the most this view reads for one file. It is refused rather than cut short without saying so.',
    retry: false,
  },
  diff_not_understood: {
    title: 'The diff could not be understood',
    body: 'git printed something this view does not recognise, so nothing is guessed.',
    retry: false,
  },
};

export function problemWords(problem: string | null, detail: string | null): ProblemWords {
  const known = problem === null ? undefined : PROBLEM_WORDS[problem];
  if (known !== undefined) return known;
  return {
    title: 'The review could not be read',
    body: detail ?? 'No reason was given.',
    retry: true,
  };
}

/** Every problem word the model can say (for the parity test against the closed vocabularies). */
export const PROBLEM_WORD_KEYS: readonly string[] = Object.keys(PROBLEM_WORDS);

export const UNAVAILABLE_WORDS: Record<string, string> = {
  not_a_mission_core_record:
    'This is a plain Herdr task, not a Mission Core assignment, so no workspace is bound for a review.',
  contract_version_1_office:
    'This office was started with the older query contract, which does not corroborate assignment workspaces.',
  assignment_not_active: 'The assignment is not active work now.',
  assignment_not_bound:
    'The assignment is not confirmed running in one workspace, so which folder to read is not established.',
  member_not_in_direct_task:
    'This agent belongs to a linked child task, not the Mission’s own task, so the Mission’s workspace is not its workspace.',
  member_not_live: 'This agent is not confirmed running now.',
  task_not_observed: 'This agent has no observed task, so no assignment to review.',
};

export function unavailableWords(reason: string | null): string {
  return (reason !== null && UNAVAILABLE_WORDS[reason]) || 'A review is not offered for this agent.';
}

/**
 * The PLAIN CONSEQUENCE of each reason a review is not offered, for the first layer: what the
 * person cannot do and why, in ordinary words, never the contract mechanics (those stay exact in
 * UNAVAILABLE_WORDS, shown in the details). It never implies a review is available.
 */
const NOT_OFFERED_CONSEQUENCE: Record<string, string> = {
  not_a_mission_core_record:
    'This is a plain Herdr task with no code workspace bound to it, so code review is not offered.',
  contract_version_1_office:
    'This office can’t confirm which code workspace belongs to which agent, so code review is not offered here.',
  assignment_not_active: 'Its assignment is not active work now, so code review is not offered.',
  assignment_not_bound:
    'Which code workspace its assignment uses is not confirmed, so code review is not offered.',
  member_not_in_direct_task:
    'This agent works in a linked child task, not in the Mission’s own workspace, so code review is not offered here.',
  member_not_live: 'This agent is not confirmed running now, so code review is not offered.',
  task_not_observed: 'No task is observed for this agent, so code review is not offered.',
};

/** True when a member's review entry states that its review is not offered (no button): the rule
 *  DiCodeReviewEntry applies. */
export function statesReviewNotOffered(member: DiMember): boolean {
  const offer = member.codeReview;
  return (
    offer !== undefined &&
    (offer.state !== 'offered' || offer.listing === null || offer.taskId === null)
  );
}

export function reviewNotOfferedConsequence(reason: string | null): string {
  return (
    (reason !== null && NOT_OFFERED_CONSEQUENCE[reason]) ||
    'Code review is not offered for this agent.'
  );
}

export function clearedWords(reason: ClearedReason): { title: string; body: string } {
  switch (reason) {
    case 'connection_lost':
      return {
        title: 'Connection lost: the review was cleared',
        body: 'The link to the office is not confirmed, so nothing here can be called current. Reopen the review when the connection returns.',
      };
    case 'reconnected_awaiting_state':
      return {
        title: 'Reconnected, but no fresh office state yet: the review was cleared',
        body: 'A reconnected link restores nothing by itself. Reopen the review once the office has sent a new state.',
      };
    case 'office_expired':
      return {
        title: 'The office state expired: the review was cleared',
        body: 'The office has said nothing new past the time its last state was valid, so what the review stood on can no longer be vouched for.',
      };
    case 'office_not_live':
      return {
        title: 'The office is not live: the review was cleared',
        body: 'The office reports that it is not live (its backend or its runtime observation is not current), so the listing this review stood on is not vouched for.',
      };
    case 'assignment_changed':
      return {
        title: 'The assignment changed: the review was cleared',
        body: 'The assignment or the agent is no longer what you opened this review for (it is no longer active work in a bound workspace, or its task or session changed). Select the agent again.',
      };
    case 'agent_not_live':
      return {
        title: 'The agent is not confirmed running: the review was cleared',
        body: 'The agent this review was opened from is no longer confirmed running, so the workspace it works in is no longer vouched for.',
      };
    case 'agent_gone':
      return {
        title: 'The agent is gone: the review was cleared',
        body: 'The agent this review was opened from is no longer listed.',
      };
  }
}

/** The offer the office state carries for a target: undefined when the member is not listed. */
export function offerFor(
  state: DiOfficeState | null,
  target: ReviewTargetRef,
): DiCodeReviewOffer | null | undefined {
  if (state === null || state.missions.state !== 'observed') return null;
  const mission = state.missions.missions.find((m) => m.missionRef === target.missionRef);
  const member = mission?.members.members.find((m) => m.memberRef === target.memberRef);
  return member === undefined ? undefined : (member.codeReview ?? null);
}

/**
 * Why an open review can no longer stand on what the client knows, or null while it can. Decided
 * in order of what is least trusted: the link (the client's own invalidation reason), then the
 * office state as a whole, then this agent's offer. Each cause has its own words.
 */
export function clearedReasonFor(
  clientReason: string | null,
  state: DiOfficeState | null,
  target: ReviewTargetRef,
): ClearedReason | null {
  if (clientReason !== null) {
    if (clientReason.startsWith('client_expired')) return 'office_expired';
    if (clientReason === 'transport_reconnected_awaiting_office_state') {
      return 'reconnected_awaiting_state';
    }
    return 'connection_lost';
  }
  if (state === null || !state.live) return 'office_not_live';
  const offer = offerFor(state, target);
  if (offer === undefined) return 'agent_gone';
  if (offerStillHolds(target, offer)) return null;
  if (offer !== null && offer.state === 'unavailable' && offer.reason === 'member_not_live') {
    return 'agent_not_live';
  }
  return 'assignment_changed';
}

/** The comparison, said in sentences (never a label alone). */
export function comparisonSentences(
  target: DiCodeReviewTarget,
  comparison: DiCodeReviewComparison,
): string[] {
  const base = comparison.baseOid.slice(0, 10);
  const where = target.detached
    ? `a detached HEAD at commit ${base}`
    : `branch ${target.branch} at commit ${base}`;
  const lines = [
    `The working tree of ${target.worktree} (${where}) is compared with that commit.`,
    `Covered: staged changes, changes not yet staged, new files git does not track yet, and the commit a submodule points to. A file changed both ways shows as one net difference.`,
    `Not covered: ignored files, what is inside a submodule, the content of binary or non-UTF-8 files, files over a bound, and private locations (those are listed by name only).`,
    `A private file is never opened. Whether a tracked one changed is judged from metadata the repository recorded for it (its mode, its size and the second it was recorded), never from its content, so an edit of the same size within that second may not be seen.`,
    `A rename is shown when git pairs a removed and an added file; otherwise it shows as a removal and an addition.`,
  ];
  if (comparison.filtersNotRun.length > 0) {
    lines.push(
      `This repository configures content filters (${comparison.filtersNotRun.join(', ')}). They were not run, so a file one manages is compared as stored and can look changed.`,
    );
  }
  return lines;
}

export const ATTRIBUTION_WORDS =
  'A shared worktree shows the whole team’s changes in it. Nothing here says who made a change.';

export function totalsWords(
  totals: { files: number; additions: number; deletions: number; withheld: number; unshown: number },
): string {
  const files = `${totals.files} ${totals.files === 1 ? 'file' : 'files'}`;
  // nothing listed is a finding, not a verdict (see emptyListingWords): the header does not say "0 files changed"
  const parts = [totals.files === 0 ? 'no changes found' : `${files} changed`, `+${totals.additions}`, `−${totals.deletions}`];
  if (totals.withheld > 0) parts.push(`${totals.withheld} withheld`);
  if (totals.unshown - totals.withheld > 0) {
    parts.push(`${totals.unshown - totals.withheld} not shown as text`);
  }
  return parts.join(' · ');
}

/** `observedAt` (epoch seconds from the backend) against the local clock, in plain words. */
export function ageWords(nowMs: number, observedAtSeconds: number | null): string {
  if (observedAtSeconds === null) return 'time not recorded';
  const seconds = Math.max(0, Math.round(nowMs / 1000 - observedAtSeconds));
  if (seconds < 5) return 'just now';
  if (seconds < 60) return `${seconds} seconds ago`;
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `${minutes} ${minutes === 1 ? 'minute' : 'minutes'} ago`;
  const hours = Math.round(minutes / 60);
  return `${hours} ${hours === 1 ? 'hour' : 'hours'} ago`;
}

export function isAging(nowMs: number, observedAtSeconds: number | null): boolean {
  return (
    observedAtSeconds !== null && nowMs / 1000 - observedAtSeconds >= REVIEW_AGING_AFTER_SECONDS
  );
}

export function clockWords(observedAtSeconds: number | null): string {
  if (observedAtSeconds === null) return 'not recorded';
  const d = new Date(observedAtSeconds * 1000);
  const two = (n: number): string => String(n).padStart(2, '0');
  return `${two(d.getHours())}:${two(d.getMinutes())}:${two(d.getSeconds())}`;
}

// ---------------------------------------------------------------------------------------------
// Rows: unified and side by side

/** One row of the unified view: a hunk header, or a line. */
export type UnifiedRow =
  | { kind: 'hunk'; hunkIndex: number; hunk: DiCodeReviewHunk; collapsed: boolean }
  | { kind: 'line'; hunkIndex: number; line: DiCodeReviewLine };

export function unifiedRows(
  hunks: readonly DiCodeReviewHunk[],
  collapsed: ReadonlySet<number>,
): UnifiedRow[] {
  const rows: UnifiedRow[] = [];
  hunks.forEach((hunk, hunkIndex) => {
    const isCollapsed = collapsed.has(hunkIndex);
    rows.push({ kind: 'hunk', hunkIndex, hunk, collapsed: isCollapsed });
    if (!isCollapsed) for (const line of hunk.lines) rows.push({ kind: 'line', hunkIndex, line });
  });
  return rows;
}

export interface Side {
  no: number;
  text: string;
  clipped: number;
  kind: 'context' | 'add' | 'del';
}
/** One row of the side-by-side view. A pure add has no left side, a pure delete no right side. */
export type SplitRow =
  | { kind: 'hunk'; hunkIndex: number; hunk: DiCodeReviewHunk; collapsed: boolean }
  | { kind: 'pair'; hunkIndex: number; left: Side | null; right: Side | null }
  | { kind: 'note'; hunkIndex: number; text: string };

/**
 * Side-by-side rows: context lines on both sides; each run of deletions is paired in order with
 * the run of additions that follows it (so a changed line sits opposite its replacement), and the
 * longer run's remainder stands alone against a blank. A "no newline" marker is a note row.
 */
export function splitRows(
  hunks: readonly DiCodeReviewHunk[],
  collapsed: ReadonlySet<number>,
): SplitRow[] {
  const rows: SplitRow[] = [];
  hunks.forEach((hunk, hunkIndex) => {
    const isCollapsed = collapsed.has(hunkIndex);
    rows.push({ kind: 'hunk', hunkIndex, hunk, collapsed: isCollapsed });
    if (isCollapsed) return;
    let dels: DiCodeReviewLine[] = [];
    let adds: DiCodeReviewLine[] = [];
    const flush = (): void => {
      const count = Math.max(dels.length, adds.length);
      for (let i = 0; i < count; i += 1) {
        const d = dels[i];
        const a = adds[i];
        rows.push({
          kind: 'pair',
          hunkIndex,
          left:
            d === undefined
              ? null
              : { no: d.oldNo!, text: d.text, clipped: d.clipped, kind: 'del' },
          right:
            a === undefined
              ? null
              : { no: a.newNo!, text: a.text, clipped: a.clipped, kind: 'add' },
        });
      }
      dels = [];
      adds = [];
    };
    for (const line of hunk.lines) {
      if (line.kind === 'del') dels.push(line);
      else if (line.kind === 'add') adds.push(line);
      else {
        flush();
        if (line.kind === 'context') {
          rows.push({
            kind: 'pair',
            hunkIndex,
            left: { no: line.oldNo!, text: line.text, clipped: line.clipped, kind: 'context' },
            right: { no: line.newNo!, text: line.text, clipped: line.clipped, kind: 'context' },
          });
        } else {
          rows.push({ kind: 'note', hunkIndex, text: `\\${line.text}` });
        }
      }
    }
    flush();
  });
  return rows;
}

/** The longest line (in characters) among the retained lines — sizes the horizontal scroll area. */
export function longestLine(hunks: readonly DiCodeReviewHunk[]): number {
  let longest = 0;
  for (const hunk of hunks) for (const l of hunk.lines) longest = Math.max(longest, l.text.length);
  return longest;
}

/** Columns the "N more characters not shown" tag after a clipped line takes (an upper bound). */
export const CLIP_TAG_COLUMNS = 40;
/** Columns a row's gutter, mark and padding take besides the digits and the text. */
export const PANE_CHROME_COLUMNS = 10;

/**
 * The columns each SIDE of a side-by-side view needs (tabs expanded; a clipped line also its tag):
 * the horizontal scroll area of a pane is sized from ITS OWN longest line, not from both sides'.
 */
export function splitColumns(rows: readonly SplitRow[]): { old: number; new: number } {
  const need = (side: { text: string; clipped: number } | null): number =>
    side === null ? 0 : expandTabs(side.text).length + (side.clipped > 0 ? CLIP_TAG_COLUMNS : 0);
  let oldSide = 0;
  let newSide = 0;
  for (const row of rows) {
    if (row.kind !== 'pair') continue;
    oldSide = Math.max(oldSide, need(row.left));
    newSide = Math.max(newSide, need(row.right));
  }
  return { old: oldSide, new: newSide };
}

/** The minimum width, in characters, of one pane's scrollable content. */
export function paneMinWidthCh(columns: number, digits: number): number {
  return columns + digits + PANE_CHROME_COLUMNS;
}

export interface PaneHead {
  title: string;
  detail: string;
}

/**
 * What each pane of the side-by-side view is, said where the eye lands: the left is the BASE (the
 * commit HEAD names), the right the WORKING TREE as observed. Never "old/new" alone.
 */
export function splitPaneHeads(
  baseOid: string,
  observedAtSeconds: number | null,
): { old: PaneHead; new: PaneHead } {
  return {
    old: { title: 'Base (old)', detail: `commit ${baseOid.slice(0, 10)}` },
    new: {
      title: 'Working tree (new)',
      detail: observedAtSeconds === null ? 'as observed' : `as observed ${clockWords(observedAtSeconds)}`,
    },
  };
}

/**
 * Why a window shows one column: said in visible text beside the diff's controls, never only in a tooltip.
 * Narrow (phone) windows have no layout choice at all and no full-screen button, so they are not told to use it.
 */
export function splitUnavailableWords(narrow: boolean): string {
  return narrow
    ? 'One column at this width: side by side needs a wider window.'
    : `Side by side needs a dialog at least ${SPLIT_MIN_VIEWPORT_PX.toLocaleString('en-US')} px wide: widen the window or use full screen.`;
}

/** The accessible name of one pane. */
export function paneLabel(side: 'old' | 'new', filePath: string): string {
  return side === 'old'
    ? `Base side (old) of ${filePath}, side by side`
    : `Working tree side (new) of ${filePath}, side by side`;
}

/** The widest line number among the retained lines (the gutter's width, in digits). */
export function gutterDigits(hunks: readonly DiCodeReviewHunk[]): number {
  let widest = 1;
  for (const hunk of hunks) {
    widest = Math.max(widest, String(hunk.oldStart + hunk.oldLines).length);
    widest = Math.max(widest, String(hunk.newStart + hunk.newLines).length);
  }
  return widest;
}

export interface RowWindow {
  first: number;
  last: number;
  offsetPx: number;
  totalPx: number;
}

/** Which rows [first, last) of `total` to render for a scroll position, and where they start. */
export function windowRows(
  total: number,
  rowPx: number,
  scrollTop: number,
  viewportPx: number,
  overscan = WINDOW_OVERSCAN_ROWS,
): RowWindow {
  const totalPx = total * rowPx;
  if (total === 0 || rowPx <= 0) return { first: 0, last: 0, offsetPx: 0, totalPx: 0 };
  const top = Math.min(Math.max(0, scrollTop), Math.max(0, totalPx - 1));
  const first = Math.max(0, Math.floor(top / rowPx) - overscan);
  const visible = Math.ceil(Math.max(0, viewportPx) / rowPx);
  const last = Math.min(total, Math.floor(top / rowPx) + visible + overscan);
  return { first, last, offsetPx: first * rowPx, totalPx };
}

export function rowPx(textSizeIndex: number): number {
  const size = TEXT_SIZES[Math.min(Math.max(0, textSizeIndex), TEXT_SIZES.length - 1)];
  return Math.round(size * ROW_HEIGHT_RATIO);
}

export function clampTextSize(index: number): number {
  return Math.min(Math.max(0, Math.trunc(index)), TEXT_SIZES.length - 1);
}

// ---------------------------------------------------------------------------------------------
// Visible control characters

export interface TextSegment {
  text: string;
  /** True for a placeholder standing in for a character that would otherwise be invisible. */
  control: boolean;
  /** Plain words for the placeholder (a tooltip), when it is one. */
  title: string | null;
}

const BIDI_NAMES: Record<number, string> = {
  0x202a: 'left-to-right embedding',
  0x202b: 'right-to-left embedding',
  0x202c: 'pop directional formatting',
  0x202d: 'left-to-right override',
  0x202e: 'right-to-left override',
  0x2066: 'left-to-right isolate',
  0x2067: 'right-to-left isolate',
  0x2068: 'first strong isolate',
  0x2069: 'pop directional isolate',
  0x200b: 'zero width space',
  0x200c: 'zero width non-joiner',
  0x200d: 'zero width joiner',
  0x200e: 'left-to-right mark',
  0x200f: 'right-to-left mark',
  0x2060: 'word joiner',
  0xfeff: 'byte order mark',
  0x00ad: 'soft hyphen',
  0x2028: 'line separator',
  0x2029: 'paragraph separator',
};

function isInvisible(code: number): boolean {
  return (
    (code < 0x20 && code !== 0x09) ||
    code === 0x7f ||
    (code >= 0x80 && code < 0xa0) ||
    code in BIDI_NAMES
  );
}

const hex = (code: number): string => code.toString(16).toUpperCase().padStart(4, '0');

/**
 * The text as segments in which every character that could change what a reader SEES or hide
 * something (controls, bidirectional overrides and isolates, zero-width characters, separators) is
 * a visible placeholder `⟦U+202E⟧`, and a tab is spaces to the next tab stop. Nothing is removed:
 * the placeholder replaces one character with its code point, so a reordering trick in source code
 * is shown, never performed.
 */
export function visibleSegments(text: string): TextSegment[] {
  const segments: TextSegment[] = [];
  let plain = '';
  let column = 0;
  const flush = (): void => {
    if (plain !== '') segments.push({ text: plain, control: false, title: null });
    plain = '';
  };
  for (const ch of text) {
    const code = ch.codePointAt(0)!;
    if (code === 0x09) {
      const spaces = TAB_WIDTH - (column % TAB_WIDTH);
      plain += ' '.repeat(spaces);
      column += spaces;
    } else if (isInvisible(code)) {
      flush();
      const name = BIDI_NAMES[code] ?? (code < 0x20 ? 'control character' : 'control character');
      segments.push({ text: `⟦U+${hex(code)}⟧`, control: true, title: `${name} (U+${hex(code)})` });
      column += 1;
    } else {
      plain += ch;
      column += 1;
    }
  }
  flush();
  return segments;
}

/** Tabs as spaces to the next tab stop; every code point (a control too) is one column. */
export function expandTabs(text: string): string {
  if (!text.includes('\t')) return text;
  let out = '';
  let column = 0;
  for (const ch of text) {
    if (ch === '\t') {
      const spaces = TAB_WIDTH - (column % TAB_WIDTH);
      out += ' '.repeat(spaces);
      column += spaces;
    } else {
      out += ch;
      column += 1;
    }
  }
  return out;
}

/** True when the text holds any character `visibleSegments` would make visible. */
export function hasHiddenCharacters(text: string): boolean {
  for (const ch of text) if (isInvisible(ch.codePointAt(0)!)) return true;
  return false;
}

// ---------------------------------------------------------------------------------------------
// Syntax: a best-effort tokenizer. It is a reading aid; the text itself is never altered.

export type TokenKind =
  | 'plain'
  | 'keyword'
  | 'string'
  | 'comment'
  | 'number'
  | 'type'
  | 'function'
  | 'punct'
  | 'property'
  | 'tag'
  | 'attr';

export interface Token {
  text: string;
  kind: TokenKind;
}

interface Language {
  name: string;
  keywords: ReadonlySet<string>;
  types?: ReadonlySet<string>;
  lineComment?: string[];
  blockComment?: [string, string];
  strings: string[];
  template?: boolean;
  jsonLike?: boolean;
  markup?: boolean;
  cssLike?: boolean;
  caseInsensitive?: boolean;
}

const set = (words: string): ReadonlySet<string> => new Set(words.split(/\s+/).filter(Boolean));

const JS_KEYWORDS = set(
  'break case catch class const continue debugger default delete do else enum export extends false finally for function if import in instanceof let new null of return super switch this throw true try typeof undefined var void while with yield async await static get set as from type interface implements private protected public readonly declare namespace abstract keyof satisfies',
);
const JS_TYPES = set(
  'string number boolean any unknown never object symbol bigint void Array Record Promise Map Set Date Error RegExp Partial Required Readonly Pick Omit',
);
const PY_KEYWORDS = set(
  'and as assert async await break class continue def del elif else except False finally for from global if import in is lambda None nonlocal not or pass raise return True try while with yield match case self cls',
);
const SH_KEYWORDS = set(
  'if then else elif fi for while do done case esac function in return export local readonly set unset source alias echo exit cd test',
);
const RUST_KEYWORDS = set(
  'as break const continue crate else enum extern false fn for if impl in let loop match mod move mut pub ref return self Self static struct super trait true type unsafe use where while async await dyn',
);
const GO_KEYWORDS = set(
  'break case chan const continue default defer else fallthrough for func go goto if import interface map package range return select struct switch type var nil true false',
);
const C_KEYWORDS = set(
  'auto break case char const continue default do double else enum extern float for goto if inline int long register restrict return short signed sizeof static struct switch typedef union unsigned void volatile while class public private protected virtual namespace template typename using new delete this true false nullptr bool include define ifdef endif',
);
const JAVA_KEYWORDS = set(
  'abstract assert boolean break byte case catch char class const continue default do double else enum extends final finally float for goto if implements import instanceof int interface long native new package private protected public return short static strictfp super switch synchronized this throw throws transient try void volatile while true false null var record',
);
const SQL_KEYWORDS = set(
  'select from where insert into values update set delete create table alter drop index join inner left right outer on group by order having limit offset and or not null is in as distinct union all primary key foreign references default unique exists case when then else end begin commit rollback',
);
const YAML_KEYWORDS = set('true false null yes no on off');
const CSS_KEYWORDS = set('important inherit initial unset none auto');

const LANGUAGES: Record<string, Language> = {
  ts: {
    name: 'TypeScript',
    keywords: JS_KEYWORDS,
    types: JS_TYPES,
    lineComment: ['//'],
    blockComment: ['/*', '*/'],
    strings: ['"', "'", '`'],
    template: true,
  },
  py: {
    name: 'Python',
    keywords: PY_KEYWORDS,
    lineComment: ['#'],
    strings: ['"', "'"],
  },
  sh: { name: 'Shell', keywords: SH_KEYWORDS, lineComment: ['#'], strings: ['"', "'", '`'] },
  rs: {
    name: 'Rust',
    keywords: RUST_KEYWORDS,
    lineComment: ['//'],
    blockComment: ['/*', '*/'],
    strings: ['"'],
  },
  go: {
    name: 'Go',
    keywords: GO_KEYWORDS,
    lineComment: ['//'],
    blockComment: ['/*', '*/'],
    strings: ['"', '`', "'"],
  },
  c: {
    name: 'C/C++',
    keywords: C_KEYWORDS,
    lineComment: ['//'],
    blockComment: ['/*', '*/'],
    strings: ['"', "'"],
  },
  java: {
    name: 'Java',
    keywords: JAVA_KEYWORDS,
    lineComment: ['//'],
    blockComment: ['/*', '*/'],
    strings: ['"', "'"],
  },
  sql: {
    name: 'SQL',
    keywords: SQL_KEYWORDS,
    lineComment: ['--'],
    blockComment: ['/*', '*/'],
    strings: ["'", '"'],
    caseInsensitive: true,
  },
  json: { name: 'JSON', keywords: set('true false null'), strings: ['"'], jsonLike: true },
  yaml: { name: 'YAML', keywords: YAML_KEYWORDS, lineComment: ['#'], strings: ['"', "'"], jsonLike: true },
  css: {
    name: 'CSS',
    keywords: CSS_KEYWORDS,
    blockComment: ['/*', '*/'],
    strings: ['"', "'"],
    cssLike: true,
  },
  html: { name: 'HTML', keywords: new Set(), strings: ['"', "'"], markup: true },
  md: { name: 'Markdown', keywords: new Set(), strings: [] },
  toml: { name: 'TOML', keywords: set('true false'), lineComment: ['#'], strings: ['"', "'"], jsonLike: true },
};

const EXTENSIONS: Record<string, string> = {
  ts: 'ts', tsx: 'ts', js: 'ts', jsx: 'ts', mjs: 'ts', cjs: 'ts', mts: 'ts', cts: 'ts',
  py: 'py', pyi: 'py',
  sh: 'sh', bash: 'sh', zsh: 'sh',
  rs: 'rs', go: 'go',
  c: 'c', h: 'c', cc: 'c', cpp: 'c', hpp: 'c', cs: 'c',
  java: 'java', kt: 'java', swift: 'java',
  sql: 'sql',
  json: 'json', jsonc: 'json', json5: 'json',
  yml: 'yaml', yaml: 'yaml',
  css: 'css', scss: 'css', less: 'css',
  html: 'html', htm: 'html', xml: 'html', svg: 'html', vue: 'html',
  md: 'md', markdown: 'md', txt: '',
  toml: 'toml',
};

/** The language key for a path (by its extension or well-known file name), or null. */
export function languageFor(path: string): string | null {
  const name = path.split('/').pop() ?? path;
  const lowered = name.toLowerCase();
  if (lowered === 'dockerfile' || lowered === 'makefile') return 'sh';
  const dot = lowered.lastIndexOf('.');
  if (dot === -1) return null;
  const key = EXTENSIONS[lowered.slice(dot + 1)];
  return key === undefined || key === '' ? null : key;
}

export function languageName(key: string | null): string {
  return key === null ? 'plain text' : (LANGUAGES[key]?.name ?? 'plain text');
}

const isIdentStart = (ch: string): boolean => /[A-Za-z_$]/.test(ch);
const isIdent = (ch: string): boolean => /[A-Za-z0-9_$]/.test(ch);
const isDigit = (ch: string): boolean => /[0-9]/.test(ch);

/**
 * Tokenize ONE line. `inBlock` says the line starts inside a block comment (carried by the caller
 * from the previous line of the same hunk; a hunk starts outside one, which is the honest limit
 * of highlighting a diff). Returns the tokens and whether the line ends inside a block comment.
 */
export function tokenizeLine(
  text: string,
  languageKey: string | null,
  inBlock = false,
): { tokens: Token[]; inBlock: boolean } {
  const lang = languageKey === null ? undefined : LANGUAGES[languageKey];
  if (lang === undefined || text === '') return { tokens: [{ text, kind: 'plain' }], inBlock: false };
  const tokens: Token[] = [];
  let plain = '';
  const flush = (): void => {
    if (plain !== '') tokens.push({ text: plain, kind: 'plain' });
    plain = '';
  };
  const push = (t: string, kind: TokenKind): void => {
    flush();
    tokens.push({ text: t, kind });
  };
  let i = 0;
  let block = inBlock;
  const startsWith = (s: string): boolean => text.startsWith(s, i);
  if (lang.name === 'Markdown') {
    const heading = /^(#{1,6}\s)(.*)$/.exec(text);
    if (heading) return { tokens: [{ text: heading[1], kind: 'keyword' }, { text: heading[2], kind: 'function' }], inBlock: false };
    const fence = /^(\s*(```|~~~).*)$/.exec(text);
    if (fence) return { tokens: [{ text, kind: 'comment' }], inBlock: false };
    const list = /^(\s*(?:[-*+]|\d+\.)\s)(.*)$/.exec(text);
    if (list) return { tokens: [{ text: list[1], kind: 'punct' }, { text: list[2], kind: 'plain' }], inBlock: false };
    return { tokens: [{ text, kind: 'plain' }], inBlock: false };
  }
  while (i < text.length) {
    if (block && lang.blockComment) {
      const end = text.indexOf(lang.blockComment[1], i);
      if (end === -1) {
        push(text.slice(i), 'comment');
        i = text.length;
      } else {
        push(text.slice(i, end + lang.blockComment[1].length), 'comment');
        i = end + lang.blockComment[1].length;
        block = false;
      }
      continue;
    }
    const ch = text[i];
    if (lang.blockComment && startsWith(lang.blockComment[0])) {
      block = true;
      const end = text.indexOf(lang.blockComment[1], i + lang.blockComment[0].length);
      if (end === -1) {
        push(text.slice(i), 'comment');
        i = text.length;
      } else {
        push(text.slice(i, end + lang.blockComment[1].length), 'comment');
        i = end + lang.blockComment[1].length;
        block = false;
      }
      continue;
    }
    if (lang.lineComment?.some((c) => startsWith(c))) {
      // A "#" in the middle of a YAML/TOML/shell word is not a comment; only after whitespace.
      const marker = lang.lineComment.find((c) => startsWith(c))!;
      const before = text[i - 1];
      if (marker !== '#' || i === 0 || before === ' ' || before === '\t') {
        push(text.slice(i), 'comment');
        i = text.length;
        continue;
      }
    }
    if (lang.markup && ch === '<') {
      const close = text.indexOf('>', i);
      const end = close === -1 ? text.length : close + 1;
      const body = text.slice(i, end);
      const open = /^(<\/?)([A-Za-z][\w:-]*)(.*?)(\/?>?)$/s.exec(body);
      if (open) {
        push(open[1], 'punct');
        push(open[2], 'tag');
        let rest = open[3];
        while (rest !== '') {
          const attr = /^(\s+)([\w:@.-]+)(=?)(".*?"|'.*?'|[^\s"']*)?/.exec(rest);
          if (!attr) {
            push(rest, 'plain');
            break;
          }
          push(attr[1], 'plain');
          push(attr[2], 'attr');
          if (attr[3]) push(attr[3], 'punct');
          if (attr[4]) push(attr[4], 'string');
          rest = rest.slice(attr[0].length);
        }
        if (open[4]) push(open[4], 'punct');
      } else {
        push(body, 'plain');
      }
      i = end;
      continue;
    }
    if (lang.strings.includes(ch)) {
      let j = i + 1;
      while (j < text.length) {
        if (text[j] === '\\') {
          j += 2;
          continue;
        }
        if (text[j] === ch) break;
        j += 1;
      }
      const end = Math.min(text.length, j + 1);
      const literal = text.slice(i, end);
      // JSON-like: a string followed by a colon is a key.
      const isKey = lang.jsonLike && /^\s*:/.test(text.slice(end));
      push(literal, isKey ? 'property' : 'string');
      i = end;
      continue;
    }
    if (isDigit(ch) || (ch === '.' && isDigit(text[i + 1] ?? ''))) {
      let j = i + 1;
      while (j < text.length && /[0-9a-fA-FxXoObB_.eE+-]/.test(text[j])) {
        if ((text[j] === '+' || text[j] === '-') && !/[eE]/.test(text[j - 1])) break;
        j += 1;
      }
      push(text.slice(i, j), 'number');
      i = j;
      continue;
    }
    if (lang.cssLike && (ch === '.' || ch === '#' || ch === '-' || ch === '@') && isIdentStart(text[i + 1] ?? '')) {
      let j = i + 1;
      while (j < text.length && /[\w-]/.test(text[j])) j += 1;
      const word = text.slice(i, j);
      push(word, ch === '@' ? 'keyword' : word.startsWith('--') ? 'property' : ch === '-' ? 'plain' : 'type');
      i = j;
      continue;
    }
    if (isIdentStart(ch)) {
      let j = i + 1;
      while (j < text.length && (isIdent(text[j]) || (lang.cssLike && text[j] === '-'))) j += 1;
      const word = text.slice(i, j);
      const lookup = lang.caseInsensitive ? word.toLowerCase() : word;
      const next = text.slice(j).match(/^\s*(\(|:)/);
      if (lang.keywords.has(lookup)) push(word, 'keyword');
      else if (lang.types?.has(word) || (/^[A-Z][A-Za-z0-9]+$/.test(word) && !lang.cssLike)) push(word, 'type');
      else if (lang.cssLike && next?.[1] === ':') push(word, 'property');
      else if (next?.[1] === '(') push(word, 'function');
      else plain += word;
      i = j;
      continue;
    }
    if (/[{}()[\];,.<>=+\-*/%&|^!~?:@]/.test(ch)) {
      push(ch, 'punct');
      i += 1;
      continue;
    }
    plain += ch;
    i += 1;
  }
  flush();
  return { tokens: tokens.length === 0 ? [{ text, kind: 'plain' }] : tokens, inBlock: block };
}

// ---------------------------------------------------------------------------------------------
// Contrast

function channel(v: number): number {
  const s = v / 255;
  return s <= 0.03928 ? s / 12.92 : Math.pow((s + 0.055) / 1.055, 2.4);
}

/** WCAG relative luminance of `#rrggbb`. */
export function luminance(hexColor: string): number {
  const m = /^#([0-9a-f]{6})$/i.exec(hexColor);
  if (!m) throw new Error(`not a #rrggbb colour: ${hexColor}`);
  const n = parseInt(m[1], 16);
  return 0.2126 * channel((n >> 16) & 255) + 0.7152 * channel((n >> 8) & 255) + 0.0722 * channel(n & 255);
}

/** WCAG contrast ratio of two `#rrggbb` colours (1..21). */
export function contrastRatio(a: string, b: string): number {
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x);
  return (hi + 0.05) / (lo + 0.05);
}

/** Blend `top` at `alpha` over `bottom` (both `#rrggbb`): the colour a translucent tint shows as. */
export function blend(top: string, alpha: number, bottom: string): string {
  const t = parseInt(top.slice(1), 16);
  const b = parseInt(bottom.slice(1), 16);
  const mix = (shift: number): number =>
    Math.round(((t >> shift) & 255) * alpha + ((b >> shift) & 255) * (1 - alpha));
  return `#${[16, 8, 0].map((s) => mix(s).toString(16).padStart(2, '0')).join('')}`;
}

// ---------------------------------------------------------------------------------------------
// Which files and states can be opened, and what the file pane says

export function canOpen(entry: DiCodeReviewEntry): boolean {
  return showsDiff(entry.state) || entry.state === 'no_content_diff';
}

/** The states whose file view carries the file's own lines (a submodule's are its pointer). */
export function showsDiff(state: string): boolean {
  return state === 'text' || state === 'submodule';
}

/** The truncation sentence for a file view, with the exact totals; null when nothing was left out. */
export function truncationWords(file: DiCodeReviewFile): string | null {
  if (!file.truncated) return null;
  return `Showing the first ${file.linesRetained.toLocaleString('en-US')} of ${file.linesTotal.toLocaleString('en-US')} changed and surrounding lines. The rest is not shown. The counts, size and digest above are for the whole diff.`;
}

export function clippedWords(count: number): string {
  return `${count.toLocaleString('en-US')} more ${count === 1 ? 'character' : 'characters'} not shown`;
}

export function shortOid(oid: string): string {
  return oid.slice(0, 10);
}

/**
 * What an EMPTY listing says. It is a finding, not a verdict: the list is git's own for every file whose content git reads,
 * but a withheld file is never opened, so its change is judged from recorded metadata and a same-size edit within the recorded
 * second may not be seen. An empty list therefore does not prove the working tree matches the commit.
 */
export function emptyListingWords(baseOid: string, observed: string): { title: string; body: string; tone: 'info' } {
  return {
    title: 'No changes found',
    body:
      `No staged, unstaged or new files differ from commit ${shortOid(baseOid)}, as far as this view can tell. ` +
      `That is not proof the working tree matches it: a private file is never opened, so whether one changed is judged from ` +
      `metadata the repository recorded for it (its mode, its size and the second it was recorded), and an edit of the same size ` +
      `within that second may not be seen. This is the workspace as observed at ${observed}.`,
    tone: 'info',
  };
}
