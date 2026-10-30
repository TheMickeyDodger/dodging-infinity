/**
 * Stage 4, increment 1 (CONTROL-MAP C1 to C4, the parts that READ): the scan Code Diff on the working tree the EXISTING review route
 * read for the SELECTED Mission.
 *
 * This is a pure mapper from the review session (`diCodeReviewModel.ts`, the one session the operational modal also uses) to the
 * data the owner-approved composition draws (`DiScanDiff`). It adds NO route and NO lifecycle: a read is still the closed
 * `diCodeReviewRead` reference (Mission, member, task, listing; a snapshot and a listed file id for one file), the office still
 * admits it and re-checks the answer, and nothing here can name a path (the grammar has no field for one).
 *
 * The rules this file keeps (each has a test):
 *  - THE SPECIMEN COMES FIRST and is untouched: the owner-approved sample is returned before anything else is considered.
 *  - A SESSION IS THIS ROW'S ONLY IF it was opened for this Mission AND the reference the office offers NOW. One session serves
 *    every Mission the reader visits, so a session that holds another Mission's diff is never shown under this Mission's name:
 *    the row says it is reading.
 *  - UNKNOWN IS NOT ABSENT: what the route did not determine says so (the route classifies the DIFF TEXT as strict UTF-8; that is a
 *    property of the text it produced, not a determination of the file's encoding, and it says nothing of line endings or
 *    indentation), a count that is only a floor says it is one, "no changes found" is said only when a listing was READ and was
 *    empty, and every refusal, cleared session or unavailable offer is its own stated state, never an empty diff.
 *  - Every file state the route distinguishes (binary, not UTF-8, oversized, withheld, unsupported, conflicted, submodule, empty,
 *    no content difference) and every kind of change (added, modified, deleted, renamed, type changed, untracked, conflicted) stays
 *    distinct, in the mark letter AND in plain words.
 */
import type { DiCodeReview, DiCodeReviewEntry } from '../../../core/src/messages.js';
import type { ReviewSession } from './diCodeReviewModel.js';
import {
  ATTRIBUTION_WORDS,
  canOpen,
  changeGlyph,
  clearedWords,
  clippedWords,
  clockWords,
  emptyListingWords,
  languageFor,
  languageName,
  problemWords,
  shortOid,
  showsDiff,
  stateWords,
  truncationWords,
  visibleSegments,
} from './diCodeReviewModel.js';
import type {
  DiScanDiff,
  DiScanDiffFile,
  DiScanDiffLine,
  DiScanDiffSource,
  DiScanRow,
  DiScanTreeEntry,
} from './diScanModel.js';
import { DI_DIFF_READING, scanDiff } from './diScanModel.js';
import type { DiScanCountDoubt } from './diScanPagesModel.js';
import { countBadge } from './diScanPagesModel.js';

/**
 * The status bar's words for what the route does NOT determine, on every real file. ONE VOCABULARY: every fact the review cannot state is
 * "unknown" (the longer "not determined" did not fit the approved status bar at 900 px; the measurement is in the Stage 4 report). The visible
 * phrase and the full statement an assistive technology reads are kept apart so the visible one can be briefer without becoming a claim: the
 * full one keeps "to this review", because it states a limit of the REVIEW'S reading, never a property of the file.
 */
export const DI_DIFF_STATUS_WORDS = {
  encoding: { visible: 'Encoding unknown', full: 'Encoding: unknown to this review' },
  lineEnding: {
    visible: 'Line ending unknown',
    full: 'Line ending: unknown to this review',
  },
  indent: {
    visible: 'Indentation unknown',
    full: 'Indentation: unknown to this review',
  },
  /**
   * THE COLLECTIVE FORM the bar shows: one phrase for the three facts above. It is honest ONLY BECAUSE the review route carries NONE of
   * encoding, line ending or indentation: there is no file for which one is known and another is not, so a single "unknown"
   * over the three hides no known fact and asserts nothing false. IF THE ROUTE EVER CARRIES ANY ONE OF THEM, this phrase becomes a lie
   * for that file and MUST BE SPLIT AGAIN (show the determined fact, and the rest as "unknown" on their own). The three
   * facts stay ENUMERATED in the accessible text, so nothing is lost to a screen reader or the hover title.
   */
  format: { visible: 'Format unknown' },
} as const;

/** The collective phrase and its accessible enumeration of the three facts it stands for. */
export function formatWords(): { visible: string; accessible: string } {
  return {
    visible: DI_DIFF_STATUS_WORDS.format.visible,
    accessible: [
      DI_DIFF_STATUS_WORDS.encoding.full,
      DI_DIFF_STATUS_WORDS.lineEnding.full,
      DI_DIFF_STATUS_WORDS.indent.full,
    ].join('. '),
  };
}

/** The route's `doesNotCover` ids in words (an id this table does not know is shown as itself, never dropped). */
export const DI_DIFF_NOT_COVERED_WORDS: Readonly<Record<string, string>> = {
  ignored: 'ignored files',
  submodule_contents: 'what is inside a submodule',
  binary_content: 'the content of binary or non-UTF-8 files',
  files_over_bound: 'files over a size bound',
  private_locations: 'private locations (listed by name only)',
};

/** Why a count is only a floor (the same pattern the Open badge uses). */
const COUNTS_ARE_A_FLOOR: DiScanCountDoubt = {
  text: 'Some listed files carry no line counts (binary, withheld, too large or not shown), so these totals are a floor.',
  codes: ['counts_not_carried'],
};

/** The language word by extension; an unknown extension is "unknown", never "plain text". */
export function languageWords(path: string): string {
  const key = languageFor(path);
  if (key !== null) return languageName(key);
  return /\.txt$/i.test(path) ? 'plain text' : 'language unknown';
}

function compareNames(a: string, b: string): number {
  const x = a.toLowerCase();
  const y = b.toLowerCase();
  return x < y ? -1 : x > y ? 1 : a < b ? -1 : a > b ? 1 : 0;
}

/** The tree the composition draws: one row per directory level (as the specimen does), directories before files at each level. */
export function treeOf(files: readonly DiCodeReviewEntry[]): {
  tree: DiScanTreeEntry[];
  fileOrder: DiCodeReviewEntry[];
} {
  interface Dir {
    dirs: Map<string, Dir>;
    files: DiCodeReviewEntry[];
  }
  const root: Dir = { dirs: new Map(), files: [] };
  for (const entry of files) {
    const parts = entry.path.split('/');
    let at = root;
    for (const part of parts.slice(0, -1)) {
      let next = at.dirs.get(part);
      if (next === undefined) {
        next = { dirs: new Map(), files: [] };
        at.dirs.set(part, next);
      }
      at = next;
    }
    at.files.push(entry);
  }
  const tree: DiScanTreeEntry[] = [];
  const fileOrder: DiCodeReviewEntry[] = [];
  const walk = (dir: Dir, prefix: string, depth: number): void => {
    for (const name of [...dir.dirs.keys()].sort(compareNames)) {
      tree.push({ id: `dir:${prefix}${name}`, name, depth, kind: 'dir' });
      walk(dir.dirs.get(name)!, `${prefix}${name}/`, depth + 1);
    }
    for (const entry of [...dir.files].sort((a, b) => compareNames(a.path, b.path))) {
      const name = entry.path.split('/').pop() ?? entry.path;
      const glyph = changeGlyph(entry.change);
      const state = stateWords(entry.state, entry.reason, entry.change).badge;
      tree.push({
        id: entry.fileId,
        name,
        depth,
        kind: 'file',
        mark: glyph.letter,
        markLabel: glyph.label,
        rowLabel: `${name}, ${glyph.label}${state === '' ? '' : `, ${state}`}`,
        path: entry.path,
      });
      fileOrder.push(entry);
    }
  };
  walk(root, '', 0);
  return { tree, fileOrder };
}

const show = (text: string): string =>
  visibleSegments(text)
    .map((segment) => segment.text)
    .join('');

/** One file's lines from its real hunks. Patch text is untrusted: controls and bidirectional overrides are shown, never performed. */
function linesOf(file: NonNullable<DiCodeReview['file']>): DiScanDiffLine[] {
  const lines: DiScanDiffLine[] = [];
  for (const hunk of file.hunks) {
    lines.push({
      kind: 'hunk',
      oldNo: '',
      newNo: '',
      text: `@@ -${hunk.oldStart},${hunk.oldLines} +${hunk.newStart},${hunk.newLines} @@${hunk.section === null ? '' : ` ${show(hunk.section)}`}`,
    });
    for (const line of hunk.lines) {
      if (line.kind === 'no_newline') {
        lines.push({ kind: 'ctx', oldNo: '', newNo: '', text: '\\ No newline at end of file' });
        continue;
      }
      lines.push({
        kind: line.kind === 'add' ? 'add' : line.kind === 'del' ? 'del' : 'ctx',
        oldNo: line.oldNo === null ? '' : String(line.oldNo),
        newNo: line.newNo === null ? '' : String(line.newNo),
        text: `${show(line.text)}${line.clipped > 0 ? ` … (${clippedWords(line.clipped)})` : ''}`,
      });
    }
  }
  return lines;
}

/** The selected file's record: its real lines, or the plain words for why there are none. */
function fileRecord(entry: DiCodeReviewEntry, session: ReviewSession): DiScanDiffFile {
  const path = entry.oldPath === null ? entry.path : `${entry.oldPath} → ${entry.path}`;
  const language = languageWords(entry.path);
  if (!canOpen(entry)) {
    // never read: the OVERVIEW's state is the only observation there is
    const words = stateWords(entry.state, entry.reason, entry.change);
    return {
      path,
      language,
      lines: [],
      notice: { state: entry.state, title: words.title, body: words.body },
    };
  }
  const answer = session.file?.answer;
  const file =
    answer !== undefined &&
    answer.state === 'observed' &&
    answer.file !== null &&
    answer.file.fileId === entry.fileId
      ? answer.file
      : null;
  if (file === null) {
    const refusal = session.refusal;
    if (refusal !== null && refusal.view === 'file' && refusal.fileId === entry.fileId) {
      const said = problemWords(refusal.problem, refusal.detail);
      return {
        path,
        language,
        lines: [],
        notice: { state: 'refused', title: said.title, body: said.body },
      };
    }
    return {
      path,
      language,
      lines: [],
      notice: {
        state: 'reading',
        title: 'Reading this file',
        body: 'Its changes have been asked for and have not arrived yet.',
      },
    };
  }
  // THE FILE READ IS THE LATER OBSERVATION: the overview's state for this file was a guess from the inventory, the read observed the file itself, so
  // when the two disagree (the overview said text, the read found it is not UTF-8) the read's state is the one stated, with its own reason.
  const observed = stateWords(file.state, file.reason, file.change);
  const lines = linesOf(file);
  if (lines.length === 0) {
    // The real route reports a read with no line to draw as its own state (`no_content_diff`: a mode-only change or a pure rename, listed that way in the
    // overview too), whose words explain why. A read that observed a state that normally HAS lines (`text`, `submodule`) and returned none is a state the
    // route cannot emit: a DEFENSIVE path, and its bare state word ("Text") would explain nothing, so it says what was observed instead.
    const words = showsDiff(file.state)
      ? {
          title: 'No lines to show',
          body: 'The route read this file and returned no changed lines for it, so there is nothing to draw. It gave no reason.',
        }
      : observed;
    return {
      path,
      language,
      lines: [],
      notice: { state: file.state, title: words.title, body: words.body },
    };
  }
  // a submodule's lines are its pointer: the state's own words say what that is
  const foot = [truncationWords(file), file.state === 'submodule' ? observed.body : null]
    .filter((x) => x !== null)
    .join(' ');
  return { path, language, lines, ...(foot === '' ? {} : { foot }) };
}

const joinWords = (items: readonly string[]): string =>
  items.length <= 1
    ? (items[0] ?? '')
    : `${items.slice(0, -1).join(', ')} and ${items[items.length - 1]}`;

/** The ONE note: whose changes these are, and what the listing does not cover, FROM THE ANSWER'S OWN LIST. */
export function noteOf(comparison: NonNullable<DiCodeReview['comparison']>): string {
  const notCovered = comparison.doesNotCover.map((id) => DI_DIFF_NOT_COVERED_WORDS[id] ?? id);
  const filters =
    comparison.filtersNotRun.length === 0
      ? ''
      : ` This repository configures content filters (${comparison.filtersNotRun.join(', ')}); they were not run, so a file one manages is compared as stored and can look changed.`;
  return `${ATTRIBUTION_WORDS} Not covered: ${joinWords(notCovered)}.${filters}`;
}

/** The composition's data for a listing the route READ (an observed overview), with the selected file's record. */
function readSource(session: ReviewSession): DiScanDiffSource {
  const answer = session.overview!.answer;
  const listing = answer.overview;
  const target = answer.target;
  const comparison = answer.comparison;
  if (listing === null || target === null || comparison === null) {
    return {
      kind: 'refused',
      reason: 'The answer was incomplete, so nothing is shown.',
      title: 'The answer was incomplete',
      detail:
        'The office relayed an overview without its listing, its target or its comparison. Nothing is shown rather than a guess.',
    };
  }
  const { tree, fileOrder } = treeOf(listing.files);
  const selected =
    session.selectedPath === null
      ? null
      : (listing.files.find((f) => f.path === session.selectedPath) ?? null);
  const firstOpenable = fileOrder.find((entry) => canOpen(entry)) ?? null;
  const clock = clockWords(answer.observedAt);

  // Counts are a FLOOR only when a listed file carries none (the backend sums a missing count as zero). An OBSERVED EMPTY listing has
  // no such file, so its totals are exactly zero and KNOWN: its limits (a withheld file is never opened) belong to the empty-listing
  // notice, not to the counts. Certain is never shown as uncertain either.
  const known = listing.files.every((f) => f.additions !== null && f.deletions !== null);
  const count = (n: number): string =>
    known ? String(n) : (countBadge(n, COUNTS_ARE_A_FLOOR) ?? '?');
  const added = count(listing.totals.additions);
  const removed = count(listing.totals.deletions);
  // TWO FACTS, TWO FIELDS. The CHECKOUT is `target.branch` / `target.detached` / `target.head` (the revision the checkout is sitting at);
  // the COMPARISON BASE is `comparison.baseOid` (the revision the diff is computed against). The office refuses an answer in which
  // `baseOid` differs from `target.head` (server/src/di/codeReview.ts, parseComparison), so on every answer it admits they are equal; this
  // mapper does not rely on that. The bar's revision is the one chip labelled "base" and it comes from `comparison.baseOid`. The checkout's
  // own revision is never drawn as the base's, and the base is never drawn as the checkout's: a detached HEAD's identity is carried, from
  // `target.head`, in the branch's title and accessible description only.
  // A detached HEAD shows "Detached" (the shortest true word) and the word "HEAD" completes its accessible name; the base chip is present
  // in every read state, so "Detached" is never alone: an answer without its comparison draws no bar at all (above).
  const detached = target.branch === null && target.detached;
  const branch = target.branch !== null ? target.branch : detached ? 'Detached' : 'Branch unknown';
  const branchTitle =
    target.branch !== null
      ? target.branch
      : detached
        ? `Detached HEAD at ${target.head}`
        : 'Branch unknown';
  const files = listing.totals.files;
  const filesChanged = `${files === 0 ? 'no changes found' : `${files} ${files === 1 ? 'file' : 'files'} changed`}${session.stale ? ' when read' : ''}`;

  const diff: DiScanDiff = {
    tree,
    branch,
    filesChanged,
    added,
    removed,
    encoding: DI_DIFF_STATUS_WORDS.encoding.visible,
    lineEnding: DI_DIFF_STATUS_WORDS.lineEnding.visible,
    indent: DI_DIFF_STATUS_WORDS.indent.visible,
    initial: selected === null ? '' : selected.fileId,
    files: selected === null ? {} : { [selected.fileId]: fileRecord(selected, session) },
    base: { short: shortOid(comparison.baseOid), full: comparison.baseOid },
    format: formatWords(),
    branchTitle,
    ...(detached ? { branchHidden: ' HEAD' } : {}),
    ...(target.branch === null ? { branchFixed: true as const } : {}),
    note: noteOf(comparison),
    tag: session.stale
      ? `Working tree, changed since it was read at ${clock}`
      : `Working tree, read ${clock}`,
    ...(known ? {} : { countsNote: COUNTS_ARE_A_FLOOR.text }),
  };
  if (files === 0) {
    const empty = emptyListingWords(comparison.baseOid, clock);
    return {
      kind: 'read',
      diff: { ...diff, files: {}, initial: '' },
      selectedPath: null,
      firstOpenablePath: null,
      // a finding, not a verdict: the empty listing's own words say what "no changes found" does and does not prove
      emptyNotice: { title: empty.title, body: empty.body },
    };
  }
  return {
    kind: 'read',
    diff,
    selectedPath: selected === null ? null : selected.path,
    firstOpenablePath: firstOpenable === null ? null : firstOpenable.path,
  };
}

/**
 * What the scan Code Diff shows for `row`, given the one review `session` (null where no reader exists). The specimen, an
 * unavailable offer and an office that said nothing are settled by `scanDiff`. An offered review (a bound workspace) is never
 * the layout-preview stub: without a matching session it is being read, and a session is shown only when it is THIS Mission's.
 */
export function scanDiffFor(row: DiScanRow, session: ReviewSession | null): DiScanDiffSource {
  const base = scanDiff(row);
  if (base.kind !== 'reading') return base;
  const offer = row.reviewOffer;
  if (offer.kind !== 'offered' || session === null) return base;
  const target = session.target;
  // THE GUARD (Mission switch): one session serves every Mission the reader visits, so it is this row's only if it was opened for THIS
  // Mission and for the reference the office offers now. Anything else is another Mission's (or a stale listing's) and is never shown.
  if (
    target === null ||
    target.missionRef !== row.id ||
    target.memberRef !== offer.target.memberRef ||
    target.level !== offer.target.level ||
    target.taskId !== offer.target.taskId ||
    target.listing !== offer.target.listing
  ) {
    return { kind: 'reading', reason: DI_DIFF_READING };
  }
  if (session.cleared !== null) {
    const said = clearedWords(session.cleared);
    return { kind: 'unavailable', reason: said.title, title: said.title, detail: said.body };
  }
  if (session.overview !== null) return readSource(session);
  if (session.refusal !== null) {
    const said = problemWords(session.refusal.problem, session.refusal.detail);
    return { kind: 'refused', reason: said.title, title: said.title, detail: said.body };
  }
  return { kind: 'reading', reason: DI_DIFF_READING };
}
