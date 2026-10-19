/**
 * Item 3 (original 20) — the live code review modal.
 *
 * The side panel is only the ENTRY. Reading happens here: a large dialog (full screen on request)
 * with the assignment, the repository and the exact comparison at the top, a searchable tree of the
 * changed files with their status and line counts, and the selected file's diff — unified or side
 * by side — as line-numbered, syntax-coloured text the reader can size, collapse and navigate by
 * keyboard. It is VIEW ONLY: nothing here edits, stages, applies, reverts, commits, dispatches,
 * approves or opens a shell; the only thing it ever sends is a read, by an admitted reference.
 *
 * Truthfulness is the point of the layout: a shared worktree is the TEAM's changes (never an
 * agent's); when the workspace moves, the old content is labelled stale (or cleared when the
 * connection or the assignment cannot be vouched for); truncated and clipped text says exactly how
 * much is not shown; binary, empty, oversized, withheld and unsupported files each say what they
 * are. Patch text is UNTRUSTED: every line is a React text node — never markup, never a link,
 * never fetched or run — and a character that could reorder or hide text is shown as a visible
 * placeholder, not performed.
 */
import './diCodeReview.css';

import type { KeyboardEvent as ReactKeyboardEvent, ReactNode, RefObject, UIEvent as ReactUIEvent } from 'react';
import { memo, useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';
import { createPortal } from 'react-dom';

import type {
  DiCodeReview,
  DiCodeReviewEntry,
  DiCodeReviewFile,
  DiCodeReviewHunk,
} from '../../../core/src/messages.js';
import type {
  ReviewSession,
  SplitRow,
  TextSegment,
  Token,
  UnifiedRow,
  VisibleRow,
} from './diCodeReviewModel.js';
import {
  ageWords,
  ATTRIBUTION_WORDS,
  buildTree,
  changeGlyph,
  clampTextSize,
  clearedWords,
  clippedWords,
  clockWords,
  comparisonSentences,
  DEFAULT_TEXT_SIZE_INDEX,
  emptyListingWords,
  entryForPath,
  expandTabs,
  filterFiles,
  gutterDigits,
  isAging,
  languageFor,
  languageName,
  longestLine,
  NARROW_VIEWPORT_PX,
  neighbourPath,
  overviewFiles,
  paneLabel,
  paneMinWidthCh,
  problemWords,
  rowPx,
  shortOid,
  showsDiff,
  SPLIT_MIN_VIEWPORT_PX,
  splitColumns,
  splitPaneHeads,
  splitRows,
  splitUnavailableWords,
  stagingWords,
  stateWords,
  TEXT_SIZES,
  tokenizeLine,
  totalsWords,
  truncationWords,
  unifiedRows,
  visibleRows,
  visibleSegments,
  windowRows,
} from './diCodeReviewModel.js';
import { nextEntryIndex } from './diReceptionFormat.js';

// ---------------------------------------------------------------------------------------------
// Per-viewer conveniences (never state that matters: wrapped, and the view works without them)

const PREF_SIZE = 'di.review.textSize';
const PREF_LAYOUT = 'di.review.layout';

function readPref(key: string): string | null {
  try {
    return window.localStorage.getItem(key);
  } catch {
    return null;
  }
}
function writePref(key: string, value: string): void {
  try {
    window.localStorage.setItem(key, value);
  } catch {
    /* private window or blocked storage: the preference is simply not remembered */
  }
}

type Layout = 'unified' | 'split';

// ---------------------------------------------------------------------------------------------
// Small pieces

function Glyph({ change }: { change: string }) {
  const g = changeGlyph(change);
  return (
    <span className="di-rv-glyph" data-tone={g.tone} title={g.label} aria-label={g.label} role="img">
      {g.letter}
    </span>
  );
}

function Stat({ additions, deletions }: { additions: number | null; deletions: number | null }) {
  if (additions === null && deletions === null) return null;
  return (
    <span className="di-rv-stat" aria-label={`${additions ?? 0} added, ${deletions ?? 0} removed`}>
      <span className="add">+{additions ?? 0}</span>
      <span className="del">&minus;{deletions ?? 0}</span>
    </span>
  );
}

function Notice({
  tone,
  title,
  children,
  code,
  actions,
  testid = 'di-review-notice',
  state,
}: {
  tone: 'info' | 'warn' | 'blocked' | 'ok';
  title: string;
  children?: ReactNode;
  code?: string | null;
  actions?: ReactNode;
  testid?: string;
  state?: string;
}) {
  return (
    <div
      className="di-rv-notice"
      data-tone={tone}
      data-testid={testid}
      data-state={state}
      role={tone === 'blocked' || tone === 'warn' ? 'alert' : 'status'}
    >
      <h3>{title}</h3>
      {children}
      {code ? <p className="code">code: {code}</p> : null}
      {actions ? <div className="di-rv-notice-actions">{actions}</div> : null}
    </div>
  );
}

/** Visible form of a run of text: control characters become placeholders, nothing is dropped. */
function Visible({ text }: { text: string }) {
  const segments: TextSegment[] = visibleSegments(text);
  return (
    <>
      {segments.map((s, i) =>
        s.control ? (
          <span key={i} className="di-rv-ctl" title={s.title ?? undefined} data-testid="di-review-control">
            {s.text}
          </span>
        ) : (
          <span key={i}>{s.text}</span>
        ),
      )}
    </>
  );
}

/** One line of code: tabs expanded, tokens coloured, controls visible, a clipped tail counted. */
const Code = memo(function Code({
  text,
  clipped,
  language,
  inBlock,
}: {
  text: string;
  clipped: number;
  language: string | null;
  inBlock: boolean;
}) {
  const tokens: Token[] = useMemo(
    () => tokenizeLine(expandTabs(text), language, inBlock).tokens,
    [text, language, inBlock],
  );
  return (
    <>
      {tokens.map((t, i) =>
        t.kind === 'plain' ? (
          <Visible key={i} text={t.text} />
        ) : (
          <span key={i} className={`tok-${t.kind}`}>
            <Visible text={t.text} />
          </span>
        ),
      )}
      {clipped > 0 ? (
        <span className="di-rv-clip" data-testid="di-review-clipped">
          &hellip; {clippedWords(clipped)}
        </span>
      ) : null}
    </>
  );
});

// ---------------------------------------------------------------------------------------------
// The tree

function TreeRow({
  row,
  selectedPath,
  tabbable,
  setSize,
  position,
  onToggle,
  onChoose,
  refAt,
  onKeyDown,
}: {
  row: VisibleRow;
  selectedPath: string | null;
  tabbable: boolean;
  setSize: number;
  position: number;
  onToggle: (path: string) => void;
  onChoose: (path: string) => void;
  refAt: (el: HTMLButtonElement | null) => void;
  onKeyDown: (event: ReactKeyboardEvent<HTMLButtonElement>) => void;
}) {
  const { node } = row;
  const style = { ['--rv-depth' as string]: row.depth } as React.CSSProperties;
  if (node.kind === 'folder') {
    return (
      <li role="none">
        <button
          type="button"
          role="treeitem"
          className="di-rv-row"
          style={style}
          ref={refAt}
          tabIndex={tabbable ? 0 : -1}
          aria-level={row.depth + 1}
          aria-setsize={setSize}
          aria-posinset={position}
          aria-expanded={row.expanded === true}
          data-testid="di-review-folder"
          data-path={node.path}
          onClick={() => onToggle(node.path)}
          onKeyDown={onKeyDown}
          title={`${node.files} ${node.files === 1 ? 'file' : 'files'} in ${node.path}`}
        >
          <span className="di-rv-twisty" aria-hidden="true">
            {row.expanded ? '▾' : '▸'}
          </span>
          <span className="di-rv-name di-rv-folder-name">{node.name}</span>
          {node.additions + node.deletions > 0 ? (
            <Stat additions={node.additions} deletions={node.deletions} />
          ) : null}
        </button>
      </li>
    );
  }
  const entry = node.entry;
  const words = stateWords(entry.state, entry.reason, entry.change);
  return (
    <li role="none">
      <button
        type="button"
        role="treeitem"
        className="di-rv-row"
        style={style}
        ref={refAt}
        tabIndex={tabbable ? 0 : -1}
        aria-level={row.depth + 1}
        aria-setsize={setSize}
        aria-posinset={position}
        aria-selected={selectedPath === entry.path}
        data-testid="di-review-row"
        data-path={entry.path}
        data-state={entry.state}
        onClick={() => onChoose(entry.path)}
        onKeyDown={onKeyDown}
        title={`${entry.path} — ${changeGlyph(entry.change).label}, ${stagingWords(entry.staging)}${
          words.badge ? `, ${words.badge}` : ''
        }`}
      >
        <span className="di-rv-twisty" aria-hidden="true" />
        <Glyph change={entry.change} />
        <span className="di-rv-name">{node.name}</span>
        {words.badge ? (
          <span className="di-rv-chip" data-tone={words.tone}>
            {words.badge}
          </span>
        ) : null}
        <Stat additions={entry.additions} deletions={entry.deletions} />
      </button>
    </li>
  );
}

function FileTree({
  files,
  query,
  collapsed,
  selectedPath,
  onToggle,
  onChoose,
}: {
  files: DiCodeReviewEntry[];
  query: string;
  collapsed: ReadonlySet<string>;
  selectedPath: string | null;
  onToggle: (path: string) => void;
  onChoose: (path: string) => void;
}) {
  const shown = useMemo(() => filterFiles(files, query), [files, query]);
  const rows = useMemo(
    () => visibleRows(buildTree(shown), collapsed, query.trim() !== ''),
    [shown, collapsed, query],
  );
  const refs = useRef<Array<HTMLButtonElement | null>>([]);
  const focusedIndex = useRef(0);
  const selectedIndex = rows.findIndex((r) => r.node.kind === 'file' && r.node.path === selectedPath);
  const tabIndexRow = selectedIndex >= 0 ? selectedIndex : 0;

  const onKeyDown = (index: number) => (event: ReactKeyboardEvent<HTMLButtonElement>) => {
    const row = rows[index];
    if (!row) return;
    focusedIndex.current = index;
    if (event.key === 'ArrowRight' && row.node.kind === 'folder') {
      event.preventDefault();
      if (row.expanded) refs.current[index + 1]?.focus();
      else onToggle(row.node.path);
      return;
    }
    if (event.key === 'ArrowLeft') {
      event.preventDefault();
      if (row.node.kind === 'folder' && row.expanded) {
        onToggle(row.node.path);
        return;
      }
      // to the parent folder
      for (let i = index - 1; i >= 0; i -= 1) {
        if (rows[i].depth < row.depth) {
          refs.current[i]?.focus();
          return;
        }
      }
      return;
    }
    const next = nextEntryIndex(index, event.key, rows.length);
    if (next !== null) {
      event.preventDefault();
      refs.current[next]?.focus();
    }
  };

  if (files.length === 0) return null;
  if (shown.length === 0) {
    return (
      <p className="di-rv-tree-count" style={{ padding: '10px 12px' }} data-testid="di-review-no-match">
        No changed file matches &ldquo;{query}&rdquo;.
      </p>
    );
  }
  return (
    <ul className="di-rv-tree" role="tree" aria-label="Changed files" data-testid="di-review-tree">
      {rows.map((row, index) => (
        <TreeRow
          key={`${row.node.kind}:${row.node.path}`}
          row={row}
          selectedPath={selectedPath}
          tabbable={index === tabIndexRow}
          setSize={rows.length}
          position={index + 1}
          onToggle={onToggle}
          onChoose={onChoose}
          refAt={(el) => {
            refs.current[index] = el;
          }}
          onKeyDown={onKeyDown(index)}
        />
      ))}
    </ul>
  );
}

// ---------------------------------------------------------------------------------------------
// The diff

interface DiffModel {
  file: DiCodeReviewFile;
  language: string | null;
  /** For each line object: does it start inside a block comment (best effort, per hunk). */
  inBlock: WeakMap<object, boolean>;
  digits: number;
  longest: number;
}

function diffModel(file: DiCodeReviewFile): DiffModel {
  const language = languageFor(file.path);
  const inBlock = new WeakMap<object, boolean>();
  for (const hunk of file.hunks) {
    let state = false;
    for (const line of hunk.lines) {
      inBlock.set(line, state);
      if (line.kind === 'del') continue; // the removed side's comment state is its own; not tracked
      state = tokenizeLine(expandTabs(line.text), language, state).inBlock;
    }
  }
  return {
    file,
    language,
    inBlock,
    digits: gutterDigits(file.hunks),
    longest: longestLine(file.hunks),
  };
}

function HunkHeader({
  hunk,
  collapsed,
  onToggle,
  index,
}: {
  hunk: DiCodeReviewHunk;
  collapsed: boolean;
  onToggle: (index: number) => void;
  index: number;
}) {
  const adds = hunk.lines.filter((l) => l.kind === 'add').length;
  const dels = hunk.lines.filter((l) => l.kind === 'del').length;
  return (
    <div className="di-rv-hunk">
      <button
        type="button"
        className="di-rv-hunk-button"
        aria-expanded={!collapsed}
        data-testid="di-review-hunk"
        data-hunk={index}
        onClick={() => onToggle(index)}
        title={collapsed ? 'Expand this section' : 'Collapse this section'}
      >
        <span aria-hidden="true">{collapsed ? '▸' : '▾'}</span>
        <span>
          @@ &minus;{hunk.oldStart},{hunk.oldLines} +{hunk.newStart},{hunk.newLines} @@
        </span>
        {hunk.section ? (
          <span className="di-rv-hunk-section">
            <Visible text={hunk.section} />
          </span>
        ) : null}
        <span className="di-rv-hunk-counts">
          <span style={{ color: 'var(--rv-add-mark)' }}>+{adds}</span>{' '}
          <span style={{ color: 'var(--rv-del-mark)' }}>&minus;{dels}</span>
        </span>
      </button>
    </div>
  );
}

function UnifiedLine({ row, model }: { row: Extract<UnifiedRow, { kind: 'line' }>; model: DiffModel }) {
  const { line } = row;
  if (line.kind === 'no_newline') {
    return (
      <div className="di-rv-note-row" data-testid="di-review-line" data-kind="note">
        &#92;{line.text}
      </div>
    );
  }
  const mark = line.kind === 'add' ? '+' : line.kind === 'del' ? '−' : ' ';
  return (
    <div className="di-rv-line" data-kind={line.kind} data-testid="di-review-line">
      <span className="di-rv-gutter" aria-hidden="true">
        {line.oldNo ?? ''}
      </span>
      <span className="di-rv-gutter" aria-hidden="true">
        {line.newNo ?? ''}
      </span>
      <span className="di-rv-mark" aria-label={line.kind === 'add' ? 'added' : line.kind === 'del' ? 'removed' : 'unchanged'}>
        {mark}
      </span>
      <span className="di-rv-text">
        <Code
          text={line.text}
          clipped={line.clipped}
          language={model.language}
          inBlock={model.inBlock.get(line) ?? false}
        />
      </span>
    </div>
  );
}

/** A hunk's stripe in the side that holds no button: the same bar, so the section reads across both. */
function HunkEcho() {
  return <div className="di-rv-hunk di-rv-hunk-echo" aria-hidden="true" />;
}

function SplitHalf({
  side,
  which,
  model,
}: {
  side: Extract<SplitRow, { kind: 'pair' }>['left'];
  which: 'old' | 'new';
  model: DiffModel;
}) {
  if (side === null) {
    return (
      <div className="di-rv-half" data-kind="none" data-side={which} data-testid="di-review-line" aria-hidden="true">
        <span className="di-rv-gutter" />
        <span className="di-rv-mark" />
        <span className="di-rv-text" />
      </div>
    );
  }
  return (
    <div className="di-rv-half" data-kind={side.kind} data-side={which} data-testid="di-review-line">
      <span className="di-rv-gutter" aria-hidden="true">
        {side.no}
      </span>
      <span className="di-rv-mark" aria-label={side.kind === 'add' ? 'added' : side.kind === 'del' ? 'removed' : 'unchanged'}>
        {side.kind === 'add' ? '+' : side.kind === 'del' ? '−' : ' '}
      </span>
      <span className="di-rv-text">
        <Code text={side.text} clipped={side.clipped} language={model.language} inBlock={false} />
      </span>
    </div>
  );
}

interface DiffBodyProps {
  model: DiffModel;
  collapsedHunks: ReadonlySet<number>;
  onToggleHunk: (index: number) => void;
  textSizeIndex: number;
  filePath: string;
  registerJump?: (jump: ((direction: 1 | -1) => void) | null) => void;
  /** The commit the comparison's left side is, and when the right side (the working tree) was observed. */
  baseOid: string;
  observedAt: number | null;
}

/**
 * Which rows of a diff are drawn for the scroll position. A unified view has ONE scroller; the
 * side-by-side view has two, and the OLD side (`mirror`) is held at the NEW side's vertical position
 * so the two read across — while each keeps its OWN horizontal position, so a long line is
 * navigated on the side it is on. A different file starts at the top and the left.
 */
function useWindowedRows(rowCount: number, filePath: string, textSizeIndex: number) {
  const px = rowPx(textSizeIndex);
  const scroller = useRef<HTMLDivElement>(null);
  const mirror = useRef<HTMLDivElement>(null);
  const [scrollTop, setScrollTop] = useState(0);
  const [viewport, setViewport] = useState(600);
  const frame = useRef<number | null>(null);

  useLayoutEffect(() => {
    const el = scroller.current;
    const twin = mirror.current;
    if (!el) return;
    const measure = () => {
      setViewport(el.clientHeight);
      // a sticky section bar is as wide as what is VISIBLE, so its counts are never off-screen
      for (const pane of [el, twin]) pane?.style.setProperty('--rv-view-w', `${pane.clientWidth}px`);
    };
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(el);
    if (twin) observer.observe(twin);
    return () => observer.disconnect();
  }, []);
  useEffect(() => {
    for (const pane of [scroller.current, mirror.current]) {
      if (pane) {
        pane.scrollTop = 0;
        pane.scrollLeft = 0;
      }
    }
    setScrollTop(0);
  }, [filePath]);
  useEffect(
    () => () => {
      if (frame.current !== null) cancelAnimationFrame(frame.current);
    },
    [],
  );

  /** Scroll to the next (or previous) section header: long diffs stay navigable. */
  const jump = useCallback(
    (direction: 1 | -1, rows: ReadonlyArray<{ kind: string }>) => {
      const el = scroller.current;
      if (!el) return;
      const top = Math.round(el.scrollTop / px);
      const at = rows.reduce<number[]>((found, row, i) => (row.kind === 'hunk' ? [...found, i] : found), []);
      const target = direction === 1 ? at.find((i) => i > top) : [...at].reverse().find((i) => i < top);
      if (target !== undefined) el.scrollTop = target * px;
    },
    [px],
  );

  const onScroll = (event: ReactUIEvent<HTMLDivElement>) => {
    const from = event.currentTarget;
    const twin = from === mirror.current ? scroller.current : mirror.current;
    if (twin && Math.abs(twin.scrollTop - from.scrollTop) >= 1) twin.scrollTop = from.scrollTop;
    if (frame.current !== null) return;
    frame.current = requestAnimationFrame(() => {
      frame.current = null;
      if (scroller.current) setScrollTop(scroller.current.scrollTop);
    });
  };

  return { px, scroller, mirror, onScroll, jump, win: windowRows(rowCount, px, scrollTop, viewport) };
}

/** Whether a pane's content is wider than the pane (so its long lines need the sideways scroll). */
function useHorizontalOverflow(pane: RefObject<HTMLDivElement | null>, content: unknown): boolean {
  const [overflow, setOverflow] = useState(false);
  useLayoutEffect(() => {
    const el = pane.current;
    if (!el) return;
    const check = () => setOverflow(el.scrollWidth > el.clientWidth + 1);
    check();
    const observer = new ResizeObserver(check);
    observer.observe(el);
    return () => observer.disconnect();
  }, [pane, content]);
  return overflow;
}

function codeStyle(textSizeIndex: number, px: number, digits: number, minWidthCh: number): React.CSSProperties {
  return {
    ['--rv-code-size' as string]: `${TEXT_SIZES[Math.min(Math.max(0, textSizeIndex), TEXT_SIZES.length - 1)]}px`,
    ['--rv-row' as string]: `${px}px`,
    ['--rv-digits' as string]: digits,
    ['--rv-minw' as string]: minWidthCh,
  } as React.CSSProperties;
}

function UnifiedBody({
  model,
  collapsedHunks,
  onToggleHunk,
  textSizeIndex,
  filePath,
  registerJump,
}: DiffBodyProps) {
  const rows: UnifiedRow[] = useMemo(() => unifiedRows(model.file.hunks, collapsedHunks), [model, collapsedHunks]);
  const { px, scroller, onScroll, jump, win } = useWindowedRows(rows.length, filePath, textSizeIndex);
  useEffect(() => {
    registerJump?.((direction) => jump(direction, rows));
    return () => registerJump?.(null);
  }, [jump, rows, registerJump]);

  return (
    <div
      className="di-rv-code-scroll"
      ref={scroller}
      onScroll={onScroll}
      style={codeStyle(textSizeIndex, px, model.digits, model.longest + 2 * model.digits + 12)}
      tabIndex={0}
      role="region"
      aria-label={`Changes in ${filePath}, unified`}
      data-testid="di-review-code"
      data-layout="unified"
      data-rows={rows.length}
    >
      <div
        className="di-rv-code-window"
        style={{ height: win.totalPx, minWidth: `max(100%, calc(var(--rv-minw) * 1ch))` }}
      >
        <div style={{ position: 'absolute', top: win.offsetPx, left: 0, right: 0 }}>
          {rows.slice(win.first, win.last).map((row, i) => {
            const absolute = win.first + i;
            if (row.kind === 'hunk') {
              return (
                <HunkHeader
                  key={`h${row.hunkIndex}`}
                  hunk={row.hunk}
                  collapsed={row.collapsed}
                  onToggle={onToggleHunk}
                  index={row.hunkIndex}
                />
              );
            }
            return <UnifiedLine key={`l${absolute}`} row={row} model={model} />;
          })}
        </div>
      </div>
    </div>
  );
}

/**
 * Side by side: TWO panes, each its own scroll region with its own horizontal position (so a long
 * line is read on the side it is on and neither side is ever pushed off the screen), held at one
 * vertical position (so the old line stays opposite its replacement). Each says what it is.
 */
function SplitBody({
  model,
  collapsedHunks,
  onToggleHunk,
  textSizeIndex,
  filePath,
  registerJump,
  baseOid,
  observedAt,
}: DiffBodyProps) {
  const rows: SplitRow[] = useMemo(() => splitRows(model.file.hunks, collapsedHunks), [model, collapsedHunks]);
  const { px, scroller, mirror, onScroll, jump, win } = useWindowedRows(rows.length, filePath, textSizeIndex);
  useEffect(() => {
    registerJump?.((direction) => jump(direction, rows));
    return () => registerJump?.(null);
  }, [jump, rows, registerJump]);
  const columns = useMemo(() => splitColumns(rows), [rows]);
  const heads = splitPaneHeads(baseOid, observedAt);
  const content = `${rows.length}:${textSizeIndex}`;
  const overflow = {
    old: useHorizontalOverflow(mirror, content),
    new: useHorizontalOverflow(scroller, content),
  };

  const drawn = rows.slice(win.first, win.last);
  const pane = (side: 'old' | 'new') => (
    <div
      className="di-rv-code-scroll"
      ref={side === 'old' ? mirror : scroller}
      onScroll={onScroll}
      style={codeStyle(textSizeIndex, px, model.digits, paneMinWidthCh(columns[side], model.digits))}
      tabIndex={0}
      role="region"
      aria-label={paneLabel(side, filePath)}
      data-testid={side === 'new' ? 'di-review-code' : 'di-review-code-old'}
      data-layout="split"
      data-side={side}
      data-rows={rows.length}
    >
      <div
        className="di-rv-code-window"
        style={{ height: win.totalPx, minWidth: `max(100%, calc(var(--rv-minw) * 1ch))` }}
      >
        <div style={{ position: 'absolute', top: win.offsetPx, left: 0, right: 0 }}>
          {drawn.map((row, i) => {
            const absolute = win.first + i;
            if (row.kind === 'hunk') {
              return side === 'old' ? (
                <HunkHeader
                  key={`h${row.hunkIndex}`}
                  hunk={row.hunk}
                  collapsed={row.collapsed}
                  onToggle={onToggleHunk}
                  index={row.hunkIndex}
                />
              ) : (
                <HunkEcho key={`h${row.hunkIndex}`} />
              );
            }
            if (row.kind === 'note') {
              return (
                <div
                  key={`n${absolute}`}
                  className="di-rv-note-row"
                  data-testid="di-review-line"
                  data-kind="note"
                  data-side={side}
                  aria-hidden={side === 'new' ? 'true' : undefined}
                >
                  {row.text}
                </div>
              );
            }
            return (
              <SplitHalf
                key={`p${absolute}`}
                side={side === 'old' ? row.left : row.right}
                which={side}
                model={model}
              />
            );
          })}
        </div>
      </div>
    </div>
  );

  return (
    <div className="di-rv-split-view" data-testid="di-review-split">
      <div className="di-rv-pane-heads">
        {(['old', 'new'] as const).map((side) => (
          <div
            key={side}
            className="di-rv-pane-head"
            data-side={side}
            data-testid={`di-review-pane-head-${side}`}
            data-overflow={overflow[side] ? 'true' : undefined}
          >
            <span className="di-rv-pane-title">{heads[side].title}</span>
            <span className="di-rv-pane-detail">{heads[side].detail}</span>
            {overflow[side] ? (
              <span className="di-rv-pane-hint" data-testid={`di-review-pane-hint-${side}`}>
                Long lines: scroll this side sideways
              </span>
            ) : null}
          </div>
        ))}
      </div>
      <div className="di-rv-panes">
        {pane('old')}
        {pane('new')}
      </div>
    </div>
  );
}

function DiffBody(props: DiffBodyProps & { layout: Layout }) {
  return props.layout === 'split' ? <SplitBody {...props} /> : <UnifiedBody {...props} />;
}

// ---------------------------------------------------------------------------------------------
// The file pane

function FilePane({
  session,
  entry,
  layout,
  splitAllowed,
  narrow,
  onLayout,
  textSizeIndex,
  onTextSize,
  stale,
  onRefresh,
}: {
  session: ReviewSession;
  entry: DiCodeReviewEntry | null;
  layout: Layout;
  splitAllowed: boolean;
  narrow: boolean;
  onLayout: (l: Layout) => void;
  textSizeIndex: number;
  onTextSize: (i: number) => void;
  stale: boolean;
  onRefresh: () => void;
}) {
  const answer: DiCodeReview | null = session.file?.answer ?? null;
  // Only the file that is SELECTED is ever shown: an answer for another is treated as not yet read.
  const file = answer?.file && answer.file.path === session.selectedPath ? answer.file : null;
  const [collapsed, setCollapsed] = useState<ReadonlySet<number>>(() => new Set());
  const identity = file === null ? null : `${file.fileId}:${answer?.snapshot}`;
  useEffect(() => setCollapsed(new Set()), [identity]);
  const toggleHunk = useCallback((index: number) => {
    setCollapsed((prev) => {
      const next = new Set(prev);
      if (next.has(index)) next.delete(index);
      else next.add(index);
      return next;
    });
  }, []);
  const model = useMemo(() => (file === null ? null : diffModel(file)), [file]);
  const jumper = useRef<((direction: 1 | -1) => void) | null>(null);

  if (session.selectedPath === null) {
    return (
      <div className="di-rv-diff-pane" data-stale={stale ? 'true' : undefined}>
        <Notice tone="info" title="Select a file to read its changes" testid="di-review-nothing-selected">
          <p>
            Choose a changed file in the list. Use the arrow keys to move through it, Enter to open a
            file, and <kbd>[</kbd> / <kbd>]</kbd> to go to the previous or next file.
          </p>
        </Notice>
      </div>
    );
  }
  if (entry === null) {
    return (
      <div className="di-rv-diff-pane">
        <Notice tone="warn" title="That file is not in the current list of changes" testid="di-review-gone">
          <p>
            <code>{session.selectedPath}</code> is not listed in the overview you are looking at. It may
            have been committed, reverted or renamed since it was selected.
          </p>
        </Notice>
      </div>
    );
  }
  const words = stateWords(entry.state, entry.reason, entry.change);
  const glyph = changeGlyph(entry.change);
  const pending = session.pending?.kind === 'file' && session.pending.path === entry.path;
  const refusal = session.refusal;
  const refusalWords = refusal ? problemWords(refusal.problem, refusal.detail) : null;
  const truncated = file === null ? null : truncationWords(file);

  return (
    <div className="di-rv-diff-pane" data-stale={stale ? 'true' : undefined}>
      <div className="di-rv-diff-head">
        <h2 className="di-rv-path" data-testid="di-review-path">
          {entry.path}
          {entry.oldPath ? <small>renamed from {entry.oldPath}</small> : null}
        </h2>
        <div className="di-rv-toolbar">
          <span className="di-rv-chip" data-tone="info">
            {glyph.label}
          </span>
          <span className="di-rv-chip">{stagingWords(entry.staging)}</span>
          <Stat additions={file?.additions ?? entry.additions} deletions={file?.deletions ?? entry.deletions} />
          {file ? (
            <span className="di-rv-chip" title="Highlighting is a best-effort reading aid; the text is exactly as stored">
              {languageName(model?.language ?? null)}
            </span>
          ) : null}
        </div>
        {file && showsDiff(file.state) ? (
          <div className="di-rv-toolbar" role="toolbar" aria-label="Diff view options">
            {narrow ? null : (
            <div className="di-rv-seg" role="group" aria-label="Layout">
              <button
                type="button"
                aria-pressed={layout === 'unified'}
                onClick={() => onLayout('unified')}
                data-testid="di-review-layout-unified"
              >
                Unified
              </button>
              <button
                type="button"
                aria-pressed={layout === 'split'}
                disabled={!splitAllowed}
                title={splitAllowed ? 'Old and new side by side' : 'Too narrow for two columns; widen the window or use full screen'}
                onClick={() => onLayout('split')}
                data-testid="di-review-layout-split"
              >
                Side by side
              </button>
            </div>
            )}
            <div className="di-rv-seg" role="group" aria-label="Text size">
              <button
                type="button"
                aria-label="Smaller text"
                disabled={textSizeIndex <= 0}
                onClick={() => onTextSize(textSizeIndex - 1)}
                data-testid="di-review-size-down"
              >
                A&minus;
              </button>
              <span className="di-rv-size" data-testid="di-review-size">
                {TEXT_SIZES[textSizeIndex]}px
              </span>
              <button
                type="button"
                aria-label="Larger text"
                disabled={textSizeIndex >= TEXT_SIZES.length - 1}
                onClick={() => onTextSize(textSizeIndex + 1)}
                data-testid="di-review-size-up"
              >
                A+
              </button>
            </div>
            {file.hunks.length > 1 ? (
              <div className="di-rv-seg" role="group" aria-label="Sections">
                <button
                  type="button"
                  onClick={() => jumper.current?.(-1)}
                  data-testid="di-review-prev-section"
                  title="Scroll to the previous section"
                >
                  Previous
                </button>
                <button
                  type="button"
                  onClick={() => jumper.current?.(1)}
                  data-testid="di-review-next-section"
                  title="Scroll to the next section"
                >
                  Next
                </button>
                <button
                  type="button"
                  onClick={() => setCollapsed(new Set(file.hunks.map((_h, i) => i)))}
                  data-testid="di-review-collapse-all"
                >
                  Collapse all
                </button>
                <button type="button" onClick={() => setCollapsed(new Set())} data-testid="di-review-expand-all">
                  Expand all
                </button>
              </div>
            ) : null}
          </div>
        ) : null}
        {file && showsDiff(file.state) && !splitAllowed ? (
          // Why there is no second column, in text a reader can SEE and read (the disabled toggle's tooltip is
          // for the pointer only; a keyboard or touch reader would otherwise be told nothing).
          <p className="di-rv-layout-note" data-testid="di-review-split-unavailable">
            {splitUnavailableWords(narrow)}
          </p>
        ) : null}
      </div>
      {truncated ? (
        <div className="di-rv-banner" data-tone="info" data-testid="di-review-truncated" role="status">
          <span>{truncated}</span>
        </div>
      ) : null}
      {refusalWords && !pending ? (
        <Notice
          tone={refusalWords.retry ? 'warn' : 'blocked'}
          title={refusalWords.title}
          code={refusal?.problem ?? null}
          state="refused"
          actions={
            refusalWords.retry ? (
              <button type="button" onClick={onRefresh} data-testid="di-review-retry">
                Refresh
              </button>
            ) : null
          }
        >
          <p>{refusalWords.body}</p>
        </Notice>
      ) : null}
      {pending && file === null ? (
        <Notice tone="info" title="Reading this file…" testid="di-review-loading-file">
          <p>git is asked a read-only question about this one file. Nothing is changed.</p>
          <div className="di-rv-progress" aria-hidden="true" />
        </Notice>
      ) : null}
      {entry.state !== 'text' && entry.state !== 'no_content_diff' ? (
        <Notice tone={words.tone === 'ok' ? 'info' : words.tone} title={words.title} state={entry.state}>
          <p>{words.body}</p>
        </Notice>
      ) : null}
      {file && file.state !== 'text' && entry.state !== file.state ? (
        <Notice tone={stateWords(file.state, file.reason, file.change).tone === 'ok' ? 'info' : stateWords(file.state, file.reason, file.change).tone} title={stateWords(file.state, file.reason, file.change).title} state={file.state}>
          <p>{stateWords(file.state, file.reason, file.change).body}</p>
        </Notice>
      ) : null}
      {file && file.state === 'no_content_diff' ? (
        <Notice tone="info" title={stateWords('no_content_diff', null).title} state="no_content_diff">
          <p>
            {stateWords('no_content_diff', null).body}
            {file.modeChange ? ` Permissions changed from ${file.modeChange.old} to ${file.modeChange.new}.` : ''}
          </p>
        </Notice>
      ) : null}
      {file && showsDiff(file.state) && model ? (
        <DiffBody
          model={model}
          layout={layout}
          collapsedHunks={collapsed}
          onToggleHunk={toggleHunk}
          textSizeIndex={textSizeIndex}
          filePath={file.path}
          registerJump={(jump) => {
            jumper.current = jump;
          }}
          baseOid={answer?.comparison?.baseOid ?? ''}
          observedAt={answer?.observedAt ?? null}
        />
      ) : null}
      {file && showsDiff(file.state) ? (
        <div className="di-rv-banner" data-tone="info" style={{ borderTop: '2px solid var(--rv-border-soft)', borderBottom: 0 }}>
          <span className="di-rv-tree-count" data-testid="di-review-file-facts">
            {file.hunks.length} {file.hunks.length === 1 ? 'section' : 'sections'} &middot;{' '}
            {file.linesTotal.toLocaleString('en-US')} lines &middot; {file.bytesTotal.toLocaleString('en-US')} bytes of diff
            {file.digestSha256 ? <> &middot; sha256 {file.digestSha256.slice(0, 12)}&hellip;</> : null}
            {file.similarity !== null ? <> &middot; {file.similarity}% similar to the old name</> : null}
            {file.modeChange ? <> &middot; permissions {file.modeChange.old} &rarr; {file.modeChange.new}</> : null}
          </span>
        </div>
      ) : null}
    </div>
  );
}

// ---------------------------------------------------------------------------------------------
// The dialog

export interface DiCodeReviewProps {
  session: ReviewSession;
  onClose: () => void;
  onRefresh: () => void;
  onSelect: (path: string | null) => void;
  /** Where focus returns when the dialog closes (the entry button that opened it). */
  returnFocusTo: { current: HTMLElement | null };
}

/**
 * The dialog's TAB ORDER: what Tab actually visits, in order. A roving-tabindex row (tabindex -1)
 * is focusable but not a tab stop, so it is not in this list; the trap wraps at the real ends.
 */
function tabStops(root: HTMLElement): HTMLElement[] {
  const selector =
    'button:not([disabled]), input:not([disabled]), summary, [href], select:not([disabled]), textarea:not([disabled]), [tabindex]';
  return [...root.querySelectorAll<HTMLElement>(selector)].filter(
    (el) =>
      el.tabIndex >= 0 &&
      el.getAttribute('aria-hidden') !== 'true' &&
      el.getClientRects().length > 0,
  );
}

/** What the dialog's key handler needs of an event: both React's and the browser's own have it. */
type KeyLike = Pick<KeyboardEvent, 'key' | 'shiftKey' | 'target' | 'preventDefault' | 'stopPropagation'>;

function Dialog({ session, onClose, onRefresh, onSelect, returnFocusTo }: DiCodeReviewProps) {
  const target = session.target!;
  const dialogRef = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(1200);
  const [fullscreen, setFullscreen] = useState(false);
  const [detailsOpen, setDetailsOpen] = useState(false);
  const [query, setQuery] = useState('');
  const [collapsedFolders, setCollapsedFolders] = useState<ReadonlySet<string>>(() => new Set());
  const [narrowPane, setNarrowPane] = useState<'files' | 'diff'>('files');
  const [nowMs, setNowMs] = useState(() => Date.now());
  const [textSizeIndex, setTextSizeIndex] = useState(() => {
    const saved = Number(readPref(PREF_SIZE));
    return Number.isInteger(saved) && readPref(PREF_SIZE) !== null
      ? clampTextSize(saved)
      : DEFAULT_TEXT_SIZE_INDEX;
  });
  const [layout, setLayout] = useState<Layout>(() => (readPref(PREF_LAYOUT) === 'split' ? 'split' : 'unified'));

  const narrow = width < NARROW_VIEWPORT_PX;
  const splitAllowed = width >= SPLIT_MIN_VIEWPORT_PX;
  const effectiveLayout: Layout = layout === 'split' && splitAllowed ? 'split' : 'unified';

  // -- the dialog owns focus while open, and gives it back ------------------------------------
  useLayoutEffect(() => {
    const root = document.getElementById('root');
    // The entry that opened the review set this before the dialog existed; it is read once.
    const back = returnFocusTo.current;
    root?.setAttribute('inert', '');
    dialogRef.current?.focus();
    return () => {
      root?.removeAttribute('inert');
      if (back && back.isConnected) back.focus();
    };
  }, [returnFocusTo]);

  useLayoutEffect(() => {
    const el = dialogRef.current;
    if (!el) return;
    setWidth(el.clientWidth);
    const observer = new ResizeObserver(() => setWidth(el.clientWidth));
    observer.observe(el);
    return () => observer.disconnect();
  }, []);

  useEffect(() => {
    const timer = setInterval(() => setNowMs(Date.now()), 5000);
    return () => clearInterval(timer);
  }, []);

  const files = overviewFiles(session);
  const answer = session.overview?.answer ?? null;
  const observedAt = answer?.observedAt ?? null;
  const entry = entryForPath(session, session.selectedPath);
  const rows = useMemo(
    () => visibleRows(buildTree(filterFiles(files, query)), collapsedFolders, query.trim() !== ''),
    [files, query, collapsedFolders],
  );

  const choose = useCallback(
    (path: string) => {
      onSelect(path);
      setNarrowPane('diff');
    },
    [onSelect],
  );
  const toggleFolder = useCallback((path: string) => {
    setCollapsedFolders((prev) => {
      const next = new Set(prev);
      if (next.has(path)) next.delete(path);
      else next.add(path);
      return next;
    });
  }, []);
  const changeSize = (index: number) => {
    const clamped = clampTextSize(index);
    setTextSizeIndex(clamped);
    writePref(PREF_SIZE, String(clamped));
  };
  const changeLayout = (next: Layout) => {
    setLayout(next);
    writePref(PREF_LAYOUT, next);
  };

  // -- keys: trap Tab, Escape closes, [ and ] move between files; none reaches the office ------
  const handleKey = (event: KeyLike) => {
    event.stopPropagation();
    const root = dialogRef.current;
    if (!root) return;
    if (event.key === 'Escape') {
      event.preventDefault();
      const field = event.target instanceof HTMLInputElement ? event.target : null;
      if (field && field.value !== '') {
        setQuery('');
        return;
      }
      onClose();
      return;
    }
    if (event.key === 'Tab') {
      const items = tabStops(root);
      if (items.length === 0) {
        event.preventDefault();
        return;
      }
      const first = items[0];
      const last = items[items.length - 1];
      const active = document.activeElement;
      if (event.shiftKey && (active === first || active === root)) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && active === last) {
        event.preventDefault();
        first.focus();
      } else if (!root.contains(active)) {
        event.preventDefault();
        first.focus();
      }
      return;
    }
    const inField = event.target instanceof HTMLInputElement;
    if (!inField && (event.key === '[' || event.key === ']') && files.length > 0) {
      const next = neighbourPath(rows, session.selectedPath, event.key === ']' ? 1 : -1);
      if (next !== null && next !== session.selectedPath) {
        event.preventDefault();
        choose(next);
      }
    }
  };
  const onKeyDown = (event: ReactKeyboardEvent<HTMLDivElement>) => handleKey(event);

  // -- focus and keys stay inside the dialog across asynchronous updates -------------------------
  // A control can stop being focusable while it holds focus: Refresh disables itself for as long as a read is in flight, a size
  // control does at its limit, a re-render can remove the focused node. The browser then drops focus to the page body, and the next
  // key is dispatched to the body: outside this dialog, so onKeyDown never runs and the key reaches the office. Looking at where
  // focus is after a key cannot see that gap. These two guards do not depend on a key having arrived inside the dialog:
  //  - focus that falls out is brought back to the dialog as soon as the browser has settled it;
  //  - a key dispatched outside the dialog anyway (in the instant before focus is back) is taken in the capture phase, before
  //    anything behind can see it, and handled here exactly as if it had arrived inside.
  const handleKeyRef = useRef(handleKey);
  useEffect(() => {
    handleKeyRef.current = handleKey;
  });
  useEffect(() => {
    const root = dialogRef.current;
    if (!root) return;
    const retake = () => {
      if (!root.contains(document.activeElement)) root.focus({ preventScroll: true });
    };
    const onFocusOut = (event: FocusEvent) => {
      if (event.relatedTarget === null) queueMicrotask(retake);
    };
    const onWindowKey = (event: KeyboardEvent) => {
      if (event.target instanceof Node && root.contains(event.target)) return; // inside: onKeyDown has it
      retake();
      handleKeyRef.current(event);
    };
    root.addEventListener('focusout', onFocusOut);
    window.addEventListener('keydown', onWindowKey, true);
    return () => {
      root.removeEventListener('focusout', onFocusOut);
      window.removeEventListener('keydown', onWindowKey, true);
    };
  }, []);

  // -- what the overview says ------------------------------------------------------------------
  const cleared = session.cleared;
  const clearedText = cleared ? clearedWords(cleared) : null;
  const refusal = session.refusal;
  const overviewRefusal =
    refusal && refusal.view === 'overview' ? problemWords(refusal.problem, refusal.detail) : null;
  const refreshing = session.pending?.kind === 'overview' && session.overview !== null;
  const loading = session.pending?.kind === 'overview' && session.overview === null;
  const aging = answer !== null && !session.stale && isAging(nowMs, observedAt);
  const info = answer?.target ?? null;
  const comparison = answer?.comparison ?? null;
  const totals = answer?.overview?.totals ?? null;
  const stale = session.stale || cleared !== null;

  const tree = (
    <div className="di-rv-tree-pane" data-stale={session.stale ? 'true' : undefined}>
      <div className="di-rv-tree-tools">
        <input
          type="search"
          aria-label="Filter changed files"
          placeholder="Filter files by path"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          data-testid="di-review-search"
          autoComplete="off"
          spellCheck={false}
        />
        <span className="di-rv-tree-count" data-testid="di-review-count" aria-live="polite">
          {query.trim() === ''
            ? `${files.length} changed ${files.length === 1 ? 'file' : 'files'}`
            : `${filterFiles(files, query).length} of ${files.length} match`}
        </span>
      </div>
      <FileTree
        files={files}
        query={query}
        collapsed={collapsedFolders}
        selectedPath={session.selectedPath}
        onToggle={toggleFolder}
        onChoose={choose}
      />
    </div>
  );

  let content: ReactNode;
  if (clearedText) {
    content = (
      <Notice tone="blocked" title={clearedText.title} testid="di-review-cleared" state={cleared ?? undefined}>
        <p>{clearedText.body}</p>
        <div className="di-rv-notice-actions">
          <button type="button" onClick={onClose} data-testid="di-review-cleared-close">
            Close
          </button>
        </div>
      </Notice>
    );
  } else if (loading) {
    content = (
      <Notice tone="info" title="Reading the workspace…" testid="di-review-loading" state="loading">
        <p>
          git is asked read-only questions about the changed files. Nothing in the workspace is changed,
          staged or run.
        </p>
        <div className="di-rv-progress" aria-hidden="true" />
      </Notice>
    );
  } else if (answer === null) {
    const words = refusal ? problemWords(refusal.problem, refusal.detail) : null;
    content = (
      <Notice
        tone={words?.retry ? 'warn' : 'blocked'}
        title={words?.title ?? 'The review could not be read'}
        code={refusal?.problem ?? null}
        state="refused"
        actions={
          <button type="button" onClick={onRefresh} data-testid="di-review-retry">
            Try again
          </button>
        }
      >
        <p>{words?.body ?? 'No answer was received.'}</p>
      </Notice>
    );
  } else if (files.length === 0) {
    const empty = emptyListingWords(answer.comparison?.baseOid ?? '', clockWords(observedAt));
    content = (
      <Notice tone={empty.tone} title={empty.title} testid="di-review-empty" state="empty">
        <p>{empty.body}</p>
      </Notice>
    );
  } else {
    content = (
      <div className="di-rv-body" data-narrow={narrow ? 'true' : undefined} data-testid="di-review-body">
        {!narrow || narrowPane === 'files' ? tree : null}
        {!narrow || narrowPane === 'diff' ? (
          <FilePane
            session={session}
            entry={entry}
            layout={effectiveLayout}
            splitAllowed={splitAllowed}
            narrow={narrow}
            onLayout={changeLayout}
            textSizeIndex={textSizeIndex}
            onTextSize={changeSize}
            stale={session.stale}
            onRefresh={onRefresh}
          />
        ) : null}
      </div>
    );
  }

  const title = `Code review — ${target.agentName}`;
  const compare = comparison && info
    ? `${info.detached ? 'detached HEAD' : `branch ${info.branch}`} · compared with commit ${shortOid(comparison.baseOid)} · staged, unstaged and new files`
    : null;
  const dialog = (
    <div
      className="di-rv-backdrop"
      data-testid="di-review-backdrop"
      data-fullscreen={fullscreen ? 'true' : 'false'}
    >
      <div
        ref={dialogRef}
        className="di-rv"
        role="dialog"
        aria-modal="true"
        aria-labelledby="di-rv-title"
        aria-describedby="di-rv-compare"
        tabIndex={-1}
        data-fullscreen={fullscreen ? 'true' : 'false'}
        data-narrow={narrow ? 'true' : undefined}
        data-testid="di-review-dialog"
        onKeyDown={onKeyDown}
        onKeyUp={(e) => e.stopPropagation()}
        onKeyPress={(e) => e.stopPropagation()}
      >
        <div className="di-rv-top">
          <h1 className="di-rv-title" id="di-rv-title">
            {title}
            <small className="di-rv-sub">
              Read only &middot; a shared worktree, shown as the team&rsquo;s changes
            </small>
          </h1>
          <div className="di-rv-actions">
            <button
              type="button"
              onClick={onRefresh}
              disabled={session.pending !== null || cleared !== null}
              data-testid="di-review-refresh"
              title="Read the workspace again"
            >
              {refreshing ? 'Refreshing…' : 'Refresh'}
            </button>
            {narrow ? null : (
              <button
                type="button"
                aria-pressed={fullscreen}
                onClick={() => setFullscreen((f) => !f)}
                data-testid="di-review-fullscreen"
              >
                {fullscreen ? 'Exit full screen' : 'Full screen'}
              </button>
            )}
            <button type="button" onClick={onClose} data-testid="di-review-close" aria-label="Close code review">
              Close
            </button>
          </div>
        </div>

        {answer && info && comparison ? (
          <section className="di-rv-overview" aria-label="Assignment, repository and comparison" data-testid="di-review-overview">
            <div className="di-rv-overview-head">
              <dl className="di-rv-facts">
                <div className="di-rv-fact">
                  <dt>Worktree</dt>
                  <dd data-testid="di-review-worktree">{info.worktree}</dd>
                </div>
                <div className="di-rv-fact">
                  <dt>{info.detached ? 'HEAD' : 'Branch'}</dt>
                  <dd data-testid="di-review-branch">{info.detached ? 'detached' : info.branch}</dd>
                </div>
                <div className="di-rv-fact">
                  <dt>Base commit</dt>
                  <dd>
                    <code data-testid="di-review-base">{shortOid(comparison.baseOid)}</code>
                  </dd>
                </div>
                <div className="di-rv-fact">
                  <dt>Observed</dt>
                  <dd data-testid="di-review-observed">
                    {clockWords(observedAt)} ({ageWords(nowMs, observedAt)})
                  </dd>
                </div>
                {totals ? (
                  <div className="di-rv-fact">
                    <dt>Changes</dt>
                    <dd data-testid="di-review-totals">{totalsWords(totals)}</dd>
                  </div>
                ) : null}
              </dl>
              <button
                type="button"
                className="di-rv-toggle"
                aria-expanded={detailsOpen}
                aria-controls="di-rv-details"
                onClick={() => setDetailsOpen((o) => !o)}
                data-testid="di-review-details"
              >
                {detailsOpen ? 'Hide details' : 'What is compared?'}
              </button>
            </div>
            <p id="di-rv-compare" className="di-rv-attribution" data-testid="di-review-compare-line">
              {narrow ? (
                <>
                  Compared with commit {shortOid(comparison.baseOid)} (staged, unstaged and new files).
                  A shared worktree: the team&rsquo;s changes, not any one agent&rsquo;s.
                </>
              ) : (
                <>
                  {compare} &middot; {ATTRIBUTION_WORDS}
                </>
              )}
            </p>
            {detailsOpen ? (
              <div className="di-rv-overview-body" id="di-rv-details" data-testid="di-review-details-body">
                {comparisonSentences(info, comparison).map((sentence) => (
                  <p key={sentence}>{sentence}</p>
                ))}
                <p>
                  Opened from the agent <strong>{target.agentName}</strong>; the assignment is task{' '}
                  <code>{target.taskId}</code>.
                </p>
              </div>
            ) : null}
          </section>
        ) : (
          <p id="di-rv-compare" className="di-rv-visually-hidden">
            {target.agentName}
          </p>
        )}

        {session.stale && !cleared ? (
          <div className="di-rv-banner" data-tone="blocked" data-testid="di-review-stale" role="alert">
            <span>
              <strong>Out of date.</strong> The workspace changed after the overview you are looking at
              (observed {clockWords(observedAt)}). What is shown is the old state, not the current one.
            </span>
            <button type="button" onClick={onRefresh} disabled={session.pending !== null}>
              Refresh
            </button>
          </div>
        ) : null}
        {aging ? (
          <div className="di-rv-banner" data-tone="info" data-testid="di-review-aging" role="status">
            <span>
              Observed {ageWords(nowMs, observedAt)}. The workspace may have changed since; refresh to
              read it again.
            </span>
            <button type="button" onClick={onRefresh} disabled={session.pending !== null}>
              Refresh
            </button>
          </div>
        ) : null}
        {overviewRefusal && answer !== null && !cleared ? (
          <div className="di-rv-banner" data-tone="blocked" data-testid="di-review-refusal" role="alert">
            <span>
              <strong>{overviewRefusal.title}.</strong> {overviewRefusal.body} (code: {refusal?.problem})
              {session.overview ? ' The overview below is from the earlier read.' : ''}
            </span>
          </div>
        ) : null}

        {narrow && answer !== null && files.length > 0 && !cleared ? (
          <div className="di-rv-tabs" role="group" aria-label="Show the file list or the diff">
            <button
              type="button"
              aria-pressed={narrowPane === 'files'}
              onClick={() => setNarrowPane('files')}
              data-testid="di-review-tab-files"
            >
              Files ({files.length})
            </button>
            <button
              type="button"
              aria-pressed={narrowPane === 'diff'}
              onClick={() => setNarrowPane('diff')}
              data-testid="di-review-tab-diff"
            >
              Diff
            </button>
          </div>
        ) : null}

        {content}

        <p className="di-rv-visually-hidden" role="status" aria-live="polite" data-testid="di-review-live">
          {loading
            ? 'Reading the workspace'
            : cleared
              ? (clearedText?.title ?? '')
              : entry && session.file?.answer.file
                ? `Showing ${entry.path}`
                : answer
                  ? `${files.length} changed files`
                  : ''}
        </p>
        {narrow || cleared !== null || files.length === 0 ? null : (
          <div className="di-rv-hints" data-testid="di-review-hints">
            <span>
              <kbd>&uarr;</kbd> <kbd>&darr;</kbd> move &middot; <kbd>Enter</kbd> open &middot; <kbd>[</kbd>{' '}
              <kbd>]</kbd> previous / next file &middot; <kbd>Esc</kbd> close
              {stale ? ' \u00b7 content is not current' : ''}
            </span>
          </div>
        )}
      </div>
    </div>
  );
  return createPortal(dialog, document.body);
}

/** The modal. Renders nothing while no review is open. */
export function DiCodeReview(props: DiCodeReviewProps) {
  if (props.session.target === null) return null;
  return <Dialog {...props} />;
}
