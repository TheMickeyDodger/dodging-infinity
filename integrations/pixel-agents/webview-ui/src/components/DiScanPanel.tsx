import './diScanPanel.css';

import { type Ref, useEffect, useMemo, useState } from 'react';

import type { DiScanAvatar, DiScanDiffLine, DiScanPill, DiScanRow, DiScanTab, DiScanView } from './diScanModel.js';
import {
  DI_SCAN_TABS,
  openAttentionCount,
  scanDiff,
  visibleScanRows,
} from './diScanModel.js';

export interface DiScanPanelProps {
  rows: readonly DiScanRow[];
  /** Select this row on first paint (the locked mock opens on its first mission). */
  initialSelectedId?: string | null;
  /** Open on this screen. The product starts on the list. */
  initialView?: DiScanView;
  minimizeRef?: Ref<HTMLButtonElement>;
  onMinimize: () => void;
  /** View only: the parent may move the camera to a drawn member. */
  onSelect?: (id: string) => void;
  /** Opens the Mission request workspace. Absent: the button stays, and explains why it cannot. */
  onNewMission?: (opener: HTMLElement) => void;
  onOpenSettings?: () => void;
  onOpenReception?: (opener: HTMLElement) => void;
  /** Focus that character's real terminal (the host's focusAgent path). */
  onFocusAgent?: (id: number) => void;
  /** Open the recorded code review when this mission offers one. */
  onOpenReview?: (id: string, opener: HTMLElement) => void;
  receptionAttention?: boolean;
}

const CODE_KEYS = new Set(['export', 'function', 'const', 'let', 'while', 'return']);
const CODE_TYPES = new Set(['string', 'DiffChange']);

function assetUrl(palette: number): { url: string; bust: boolean } {
  const base = import.meta.env.BASE_URL ?? '/';
  if (palette >= 0 && palette <= 3) {
    return { url: `${base}assets/characters/busts/bust_${palette}.png`, bust: true };
  }
  const index = ((palette % 6) + 6) % 6;
  return { url: `${base}assets/characters/char_${index}.png`, bust: false };
}

function codeTokens(text: string): { text: string; kind: string }[] {
  if (text.trimStart().startsWith('//')) return [{ text, kind: 'note' }];
  const out: { text: string; kind: string }[] = [];
  // The escaped-char alternative and the ordinary-char class are disjoint, so a long run of
  // backslashes cannot make this scan exponential.
  const re = /([A-Za-z_][A-Za-z0-9_]*)|('(?:\\.|[^'\\])*'|"(?:\\.|[^"\\])*")|(\s+)|(.)/g;
  let match: RegExpExecArray | null;
  while ((match = re.exec(text))) {
    const word = match[1];
    if (word) {
      const call = /^\s*\(/.test(text.slice(re.lastIndex));
      const kind = CODE_KEYS.has(word) ? 'key' : call || CODE_TYPES.has(word) ? 'fn' : 'plain';
      out.push({ text: word, kind });
    } else if (match[2]) {
      out.push({ text: match[2], kind: 'str' });
    } else {
      out.push({ text: match[3] ?? match[4] ?? '', kind: 'plain' });
    }
  }
  return out;
}

function Pills({ pills, fill = false }: { pills: readonly DiScanPill[]; fill?: boolean }) {
  return (
    <span className="di-scan-pills">
      {pills.map((pill) => (
        <span
          key={pill.id}
          className="di-scan-pill"
          data-tone={pill.tone}
          data-fill={fill ? 'solid' : 'chip'}
          data-testid="di-scan-pill"
        >
          {fill ? null : <span className="di-scan-dot" data-tone={pill.tone} aria-hidden="true" />}
          {pill.label}
        </span>
      ))}
    </span>
  );
}

function Avatar({ member, large = false }: { member: DiScanAvatar; large?: boolean }) {
  const art = assetUrl(member.palette);
  return (
    <span
      className={large ? 'di-scan-avatar di-scan-avatar-lg' : 'di-scan-avatar'}
      data-bust={art.bust ? 'true' : 'sheet'}
      style={{ backgroundImage: `url("${art.url}")` }}
      role="img"
      aria-label={member.name}
    />
  );
}

function ChevronIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 16 16" aria-hidden="true">
      <path
        d="M4 3.2 7.6 8 4 12.8M8.4 3.2 12 8 8.4 12.8"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.6"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

function BackIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 16 16" aria-hidden="true">
      <path
        d="M10.5 3.2 5.8 8l4.7 4.8"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.6"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

function DiMark() {
  return (
    <svg viewBox="0 0 45 29" aria-hidden="true" shapeRendering="crispEdges">
      <path fill="currentColor" d="M0 0h21v1h-21zM32 0h12v1h-12zM0 1h21v1h-21zM32 1h12v1h-12zM0 2h23v1h-23zM32 2h12v1h-12zM0 3h23v1h-23zM32 3h12v1h-12zM0 4h24v1h-24zM32 4h12v1h-12zM0 5h26v1h-26zM32 5h12v1h-12zM0 6h26v1h-26zM34 6h8v1h-8zM0 7h8v1h-8zM19 7h7v1h-7zM35 7h7v1h-7zM0 8h7v1h-7zM19 8h7v1h-7zM35 8h7v1h-7zM0 9h7v1h-7zM19 9h7v1h-7zM35 9h7v1h-7zM0 10h8v1h-8zM19 10h7v1h-7zM35 10h7v1h-7zM0 11h8v1h-8zM19 11h7v1h-7zM35 11h7v1h-7zM0 12h8v1h-8zM19 12h7v1h-7zM35 12h7v1h-7zM0 13h8v1h-8zM19 13h7v1h-7zM35 13h7v1h-7zM0 14h8v1h-8zM19 14h7v1h-7zM35 14h7v1h-7zM0 15h7v1h-7zM19 15h7v1h-7zM35 15h7v1h-7zM0 16h7v1h-7zM19 16h7v1h-7zM35 16h7v1h-7zM0 17h7v1h-7zM19 17h7v1h-7zM35 17h7v1h-7zM0 18h7v1h-7zM19 18h7v1h-7zM35 18h7v1h-7zM0 19h7v1h-7zM19 19h7v1h-7zM35 19h7v1h-7zM0 20h7v1h-7zM19 20h7v1h-7zM35 20h7v1h-7zM0 21h8v1h-8zM19 21h7v1h-7zM35 21h7v1h-7zM0 22h26v1h-26zM34 22h8v1h-8zM0 23h25v1h-25zM32 23h13v1h-13zM0 24h23v1h-23zM32 24h13v1h-13zM0 25h23v1h-23zM32 25h13v1h-13zM0 26h22v1h-22zM32 26h13v1h-13zM0 27h21v1h-21zM32 27h13v1h-13zM0 28h21v1h-21zM32 28h12v1h-12z" />
    </svg>
  );
}

function GearIcon() {
  return (
    <svg width="18" height="18" viewBox="0 0 24 24" aria-hidden="true">
      <path
        fill="currentColor"
        fillRule="evenodd"
        d="M9.6 1.8h4.8l.4 2.3c.7.2 1.3.5 1.9.9l2.1-1.1 2.4 2.4-1.1 2.1c.4.6.7 1.2.9 1.9l2.3.4v4.8l-2.3.4c-.2.7-.5 1.3-.9 1.9l1.1 2.1-2.4 2.4-2.1-1.1c-.6.4-1.2.7-1.9.9l-.4 2.3H9.6l-.4-2.3a7 7 0 0 1-1.9-.9l-2.1 1.1-2.4-2.4 1.1-2.1a7 7 0 0 1-.9-1.9l-2.3-.4V9.7l2.3-.4c.2-.7.5-1.3.9-1.9L2.8 5.3 5.2 2.9l2.1 1.1c.6-.4 1.2-.7 1.9-.9l.4-2.3ZM12 8.6a3.4 3.4 0 1 0 .01 0Z"
      />
    </svg>
  );
}

function SlidersIcon() {
  return (
    <svg width="22" height="22" viewBox="0 0 24 24" aria-hidden="true">
      <path
        d="M3.5 6.5h17M3.5 12h17M3.5 17.5h17"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.7"
        strokeLinecap="round"
      />
      <circle cx="16.5" cy="6.5" r="2.15" fill="var(--di-scan-bg)" stroke="currentColor" strokeWidth="1.7" />
      <circle cx="7.2" cy="12" r="2.15" fill="var(--di-scan-bg)" stroke="currentColor" strokeWidth="1.7" />
      <circle cx="12" cy="17.5" r="2.15" fill="var(--di-scan-bg)" stroke="currentColor" strokeWidth="1.7" />
    </svg>
  );
}

function FilterIcon() {
  return (
    <svg width="20" height="20" viewBox="0 0 24 24" aria-hidden="true">
      <path
        d="M3 4.2h18L14.4 12.4V20h-4.8v-7.6L3 4.2Z"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.7"
        strokeLinejoin="round"
      />
    </svg>
  );
}

function SearchIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 16 16" aria-hidden="true">
      <circle cx="7" cy="7" r="4.2" fill="none" stroke="currentColor" strokeWidth="1.5" />
      <path d="M10.2 10.2 13.5 13.5" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
    </svg>
  );
}

function BellIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 16 16" aria-hidden="true">
      <path
        d="M8 1.8a4.2 4.2 0 0 0-4.2 4.2v2.3L2.4 10.6h11.2L12.2 8.3V6A4.2 4.2 0 0 0 8 1.8Z"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.4"
        strokeLinejoin="round"
      />
      <path d="M6.6 12.2a1.4 1.4 0 0 0 2.8 0" fill="none" stroke="currentColor" strokeWidth="1.4" />
    </svg>
  );
}

function TrashIcon() {
  return (
    <svg width="15" height="15" viewBox="0 0 24 24" aria-hidden="true">
      <path
        d="M4 7h16M9.2 7V4.6h5.6V7M6.4 7.4l.9 13h9.4l.9-13"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.8"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
      <path
        d="M10 11v6M14 11v6"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.8"
        strokeLinecap="round"
      />
    </svg>
  );
}

function LinkIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 16 16" aria-hidden="true">
      <path
        d="M6.6 9.4a3.2 3.2 0 0 0 4.5 0l2-2a3.2 3.2 0 0 0-4.5-4.5l-1.1 1.1"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.4"
        strokeLinecap="round"
      />
      <path
        d="M9.4 6.6a3.2 3.2 0 0 0-4.5 0l-2 2a3.2 3.2 0 0 0 4.5 4.5l1.1-1.1"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.4"
        strokeLinecap="round"
      />
    </svg>
  );
}

function CopyIcon() {
  return (
    <svg width="15" height="15" viewBox="0 0 16 16" aria-hidden="true">
      <rect x="5.2" y="5.2" width="8" height="8" rx="1.2" fill="none" stroke="currentColor" strokeWidth="1.4" />
      <path d="M10.6 5.2V3.4A1.2 1.2 0 0 0 9.4 2.2H3.4A1.2 1.2 0 0 0 2.2 3.4v6A1.2 1.2 0 0 0 3.4 10.6H5.2" fill="none" stroke="currentColor" strokeWidth="1.4" />
    </svg>
  );
}

function MoreIcon() {
  return (
    <svg width="15" height="15" viewBox="0 0 16 16" aria-hidden="true">
      <circle cx="8" cy="3.2" r="1.1" fill="currentColor" />
      <circle cx="8" cy="8" r="1.1" fill="currentColor" />
      <circle cx="8" cy="12.8" r="1.1" fill="currentColor" />
    </svg>
  );
}

function TwistIcon() {
  return (
    <svg width="12" height="12" viewBox="0 0 12 12" aria-hidden="true">
      <path
        d="M2.6 4.2 6 7.8 9.4 4.2"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.4"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

function FolderIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 16 16" aria-hidden="true">
      <path d="M2 4.2h4l1.2 1.4H14V13H2Z" fill="none" stroke="currentColor" strokeWidth="1.3" />
    </svg>
  );
}

function FileIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 16 16" aria-hidden="true">
      <path d="M4 2.2h5.2L12.8 6V13.8H4Z" fill="none" stroke="currentColor" strokeWidth="1.3" />
    </svg>
  );
}

function BranchIcon() {
  return (
    <svg width="13" height="13" viewBox="0 0 16 16" aria-hidden="true">
      <circle cx="4" cy="3.2" r="1.5" fill="none" stroke="currentColor" strokeWidth="1.3" />
      <circle cx="4" cy="12.8" r="1.5" fill="none" stroke="currentColor" strokeWidth="1.3" />
      <circle cx="12" cy="8" r="1.5" fill="none" stroke="currentColor" strokeWidth="1.3" />
      <path d="M4 4.7v6.6M4 8h6.4" fill="none" stroke="currentColor" strokeWidth="1.3" />
    </svg>
  );
}

function tabLabel(tab: DiScanTab): string {
  return DI_SCAN_TABS.find((item) => item.id === tab)?.label ?? 'Open';
}

function Crumb({
  row,
  view,
  onList,
  onMission,
}: {
  row: DiScanRow;
  view: DiScanView;
  onList: () => void;
  onMission: () => void;
}) {
  const tail = view === 'diff' ? 'Code Diff' : view === 'herdr' ? 'Herdr' : view === 'comments' ? 'Comments' : '';
  return (
    <nav className="di-scan-crumb" aria-label="Location">
      <button type="button" onClick={onList}>
        World
      </button>
      <span aria-hidden="true">/</span>
      <button type="button" onClick={onList}>
        {tabLabel(row.tab)}
      </button>
      <span aria-hidden="true">/</span>
      {tail === '' ? (
        <span className="di-scan-crumb-current">{row.title}</span>
      ) : (
        <button type="button" onClick={onMission}>
          {row.title}
        </button>
      )}
      {tail !== '' && (
        <>
          <span aria-hidden="true">/</span>
          <span className="di-scan-crumb-current">{tail}</span>
        </>
      )}
    </nav>
  );
}

function MetaLine({ text }: { text: string }) {
  return (
    <p className="di-scan-meta di-scan-meta-line" data-testid="di-scan-meta">
      <LinkIcon />
      <span>{text}</span>
    </p>
  );
}

function Stage({
  view,
  row,
  onList,
  onMission,
  onView,
  onHide,
  minimizeRef,
  onOpenSettings,
  onFocusAgent,
  onOpenReview,
}: {
  view: Exclude<DiScanView, 'list'>;
  row: DiScanRow;
  onList: () => void;
  onMission: () => void;
  onView: (view: Exclude<DiScanView, 'list'>) => void;
  onHide: () => void;
  minimizeRef?: Ref<HTMLButtonElement>;
  onOpenSettings?: () => void;
  onFocusAgent?: (id: number) => void;
  onOpenReview?: (id: string, opener: HTMLElement) => void;
}) {
  const diff = scanDiff(row);
  const [fileQuery, setFileQuery] = useState('');
  const [findOpen, setFindOpen] = useState(false);
  const tree = useMemo(() => {
    if (!diff) return [];
    const q = fileQuery.trim().toLowerCase();
    if (q === '') return diff.tree;
    return diff.tree.filter((entry) => entry.kind === 'dir' || entry.name.toLowerCase().includes(q));
  }, [diff, fileQuery]);

  const copyDiff = () => {
    if (!diff) return;
    const text = diff.lines.map((line) => line.text).join('\n');
    void navigator.clipboard?.writeText(text).catch(() => undefined);
  };

  return (
    <div className="di-scan-stage" data-testid="di-scan-stage" data-view={view}>
      <header className="di-scan-stage-bar">
        <div className="di-scan-header-lead">
          {view === 'mission' ? (
            <button
              type="button"
              ref={minimizeRef}
              className="di-scan-back"
              onClick={onList}
              aria-label="Back to missions"
              data-testid="di-scan-back"
            >
              <BackIcon />
            </button>
          ) : (
            <button
              type="button"
              ref={minimizeRef}
              className="di-scan-chevron"
              onClick={onList}
              title="Back to the mission list"
              aria-label="Back to the mission list"
            >
              <ChevronIcon />
            </button>
          )}
          <span className="di-scan-mark" aria-hidden="true">
            <DiMark />
          </span>
          <Crumb row={row} view={view} onList={onList} onMission={onMission} />
        </div>
        {view === 'diff' && (
          <div className="di-scan-header-tools">
            <button
              type="button"
              className="di-scan-icon-button"
              aria-label="Find a file"
              aria-pressed={findOpen}
              data-testid="di-scan-diff-find"
              onClick={() => setFindOpen((open) => !open)}
            >
              <SearchIcon />
            </button>
            <button type="button" className="di-scan-icon-button" aria-label="Copy the diff" onClick={copyDiff}>
              <CopyIcon />
            </button>
            <button
              type="button"
              className="di-scan-icon-button"
              aria-label="Settings"
              onClick={() => onOpenSettings?.()}
            >
              <GearIcon />
            </button>
            <button type="button" className="di-scan-icon-button" aria-label="More">
              <MoreIcon />
            </button>
          </div>
        )}
      </header>

      {view === 'mission' && (
        <div className="di-scan-mission" data-testid="di-scan-mission">
          <h1 className="di-scan-mission-title">{row.title}</h1>
          <Pills pills={row.pills} fill />
          <section className="di-scan-card" aria-labelledby="di-scan-details-heading">
            <h2 id="di-scan-details-heading">Mission details</h2>
            {row.description !== '' && <p>{row.description}</p>}
            {row.handoff !== '' &&
              row.handoff.split('\n').map((line) => (
                <p key={line} className="di-scan-handoff">
                  {line}
                </p>
              ))}
            <div className="di-scan-card-foot">
              <MetaLine text={row.footer} />
              {row.updated !== '' && <p className="di-scan-updated">{row.updated}</p>}
            </div>
          </section>
          {row.swarm.length > 0 && (
            <>
              <h2 className="di-scan-swarm-heading">Swarm</h2>
              <ul className="di-scan-cards" data-testid="di-scan-swarm">
                {row.swarm.map((member) => (
                  <li key={`${member.role}-${member.name}`} className="di-scan-card-member">
                    <Avatar member={member} large />
                    <span className="di-scan-card-copy">
                      <span className="di-scan-role">{member.role}</span>
                      <span className="di-scan-name">{member.name}</span>
                      {member.note !== '' && <span className="di-scan-note">{member.note}</span>}
                    </span>
                  </li>
                ))}
              </ul>
            </>
          )}
          <div className="di-scan-actions">
            <button
              type="button"
              className="di-scan-action di-scan-action-diff"
              data-testid="di-scan-code-diff"
              onClick={() => onView('diff')}
            >
              CODE DIFF
            </button>
            <button
              type="button"
              className="di-scan-action di-scan-action-herdr"
              data-testid="di-scan-herdr"
              onClick={() => onView('herdr')}
            >
              HERDR
            </button>
            <button
              type="button"
              className="di-scan-action di-scan-action-comments"
              data-testid="di-scan-comments"
              onClick={() => onView('comments')}
            >
              COMMENTS
            </button>
            <button
              type="button"
              className="di-scan-trash di-scan-trash-boxed"
              aria-label={`Clear ${row.title}`}
              data-testid="di-scan-trash"
              onClick={onHide}
            >
              <TrashIcon />
            </button>
          </div>
        </div>
      )}

      {view === 'diff' && (
        <div className="di-scan-ide" data-testid="di-scan-diff">
          <aside className="di-scan-tree" aria-label="Files">
            <div className="di-scan-tree-head">
              <span>FILES</span>
              <MoreIcon />
            </div>
            {findOpen && (
              <label className="di-scan-tree-find">
                <SearchIcon />
                <input
                  type="search"
                  value={fileQuery}
                  placeholder="Find a file"
                  aria-label="Find a file"
                  data-testid="di-scan-diff-search"
                  onChange={(event) => setFileQuery(event.target.value)}
                />
              </label>
            )}
            <ul>
              {(diff ? tree : []).map((entry) => (
                <li key={entry.id}>
                  <span
                    className="di-scan-tree-row"
                    data-selected={entry.selected ? 'true' : undefined}
                    style={{ paddingLeft: `${10 + entry.depth * 16}px` }}
                  >
                    <TwistIcon />
                    {entry.kind === 'dir' ? <FolderIcon /> : <FileIcon />}
                    <span>{entry.name}</span>
                    {entry.mark && <span className="di-scan-mark-badge">{entry.mark}</span>}
                  </span>
                </li>
              ))}
            </ul>
          </aside>
          <div className="di-scan-editor">
            <div className="di-scan-editor-bar">
              <span className="di-scan-editor-path">{diff ? diff.path : 'No diff is recorded for this mission.'}</span>
              <label className="di-scan-unified">
                <span className="di-scan-visually-hidden">Diff layout</span>
                <select aria-label="Diff layout" defaultValue="unified">
                  <option value="unified">Unified</option>
                </select>
              </label>
              <button type="button" className="di-scan-icon-button" aria-label="Close the diff" onClick={onMission}>
                ×
              </button>
            </div>
            <div className="di-scan-editor-body" data-testid="di-scan-editor">
              {diff ? (
                <>
                  <div className="di-scan-gutter-head">
                    <span>Old</span>
                    <span>New</span>
                  </div>
                  {diff.lines.map((line, index) => (
                    <DiffRow key={`${line.kind}-${index}`} line={line} />
                  ))}
                </>
              ) : (
                <div className="di-scan-editor-empty">
                  <p>No diff is recorded for this mission.</p>
                  {row.reviewOffered && onOpenReview && (
                    <button
                      type="button"
                      className="di-scan-action di-scan-action-diff"
                      data-testid="di-scan-open-review"
                      onClick={(event) => onOpenReview(row.id, event.currentTarget)}
                    >
                      Open recorded review
                    </button>
                  )}
                </div>
              )}
            </div>
            <footer className="di-scan-statusbar">
              <span className="di-scan-status-group">
                <BranchIcon />
                {diff?.branch ?? 'main'}
              </span>
              <span>{diff?.filesChanged ?? '0 files changed'}</span>
              <span className="di-scan-status-counts">
                <span className="di-scan-add">+{diff?.added ?? '0'}</span>
                <span className="di-scan-del">−{diff?.removed ?? '0'}</span>
              </span>
              <span className="di-scan-status-group di-scan-status-end">
                <span>UTF-8</span>
                <span>LF</span>
                <span>TypeScript</span>
                <span>Spaces: 2</span>
                <span aria-hidden="true">✓</span>
              </span>
            </footer>
          </div>
        </div>
      )}

      {view === 'herdr' && (
        <div className="di-scan-herdr-view" data-testid="di-scan-herdr-view">
          {row.swarm.length === 0 ? (
            <p className="di-scan-editor-empty">No Herdr members are recorded for this mission.</p>
          ) : (
            <ul className="di-scan-panes">
              {row.swarm.map((member) => (
                <li key={`${member.role}-${member.name}`} className="di-scan-pane" data-testid="di-scan-pane">
                  <header>
                    <Avatar member={member} />
                    <span>
                      <strong>{member.name}</strong>
                      <span className="di-scan-role">{member.role}</span>
                    </span>
                  </header>
                  <pre>
                    {member.lines.length > 0
                      ? member.lines.map((line) => `❯ ${line}`).join('\n')
                      : '❯'}
                  </pre>
                  <button
                    type="button"
                    disabled={member.characterId === null || onFocusAgent === undefined}
                    title={
                      member.characterId === null
                        ? 'No terminal is recorded for this member'
                        : `Focus ${member.name}'s terminal`
                    }
                    onClick={() => {
                      if (member.characterId !== null) onFocusAgent?.(member.characterId);
                    }}
                  >
                    Focus terminal
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}

      {view === 'comments' && (
        <div className="di-scan-comments-view" data-testid="di-scan-comments-view">
          <h1>{row.title}</h1>
          <p>No comments are recorded for this mission.</p>
        </div>
      )}
    </div>
  );
}

function DiffRow({ line }: { line: DiScanDiffLine }) {
  if (line.kind === 'hunk') {
    return (
      <div className="di-scan-diff-line" data-kind="hunk">
        <span />
        <span />
        <span className="di-scan-hunk">{line.text}</span>
      </div>
    );
  }
  const mark = line.kind === 'del' ? '−' : line.kind === 'add' ? '+' : '';
  return (
    <div className="di-scan-diff-line" data-kind={line.kind}>
      <span className="di-scan-ln">{line.oldNo}</span>
      <span className="di-scan-ln">{line.newNo}</span>
      <span className="di-scan-code">
        {mark !== '' && <span className="di-scan-sign">{mark}</span>}
        {codeTokens(line.text).map((token, index) => (
          <span key={index} className={token.kind === 'plain' ? undefined : `di-scan-tok-${token.kind}`}>
            {token.text}
          </span>
        ))}
      </span>
    </div>
  );
}

export function DiScanPanel({
  rows,
  initialSelectedId = null,
  initialView = 'list',
  minimizeRef,
  onMinimize,
  onSelect,
  onNewMission,
  onOpenSettings,
  onOpenReception,
  onFocusAgent,
  onOpenReview,
  receptionAttention = false,
}: DiScanPanelProps) {
  const [tab, setTab] = useState<DiScanTab>('open');
  const [query, setQuery] = useState('');
  const [needsOnly, setNeedsOnly] = useState(false);
  const [hidden, setHidden] = useState<ReadonlySet<string>>(() => new Set());
  const [selectedId, setSelectedId] = useState<string | null>(initialSelectedId);
  const [view, setView] = useState<DiScanView>(initialView);

  useEffect(() => {
    if (selectedId !== null && hidden.has(selectedId)) {
      setSelectedId(null);
      setView('list');
    }
  }, [hidden, selectedId]);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key !== 'Escape' || view === 'list') return;
      setView(view === 'mission' ? 'list' : 'mission');
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [view]);

  const shown = useMemo(
    () => visibleScanRows(rows, tab, query, needsOnly, hidden),
    [rows, tab, query, needsOnly, hidden],
  );
  const attention = openAttentionCount(rows, hidden);
  const selected = rows.find((row) => row.id === selectedId && !hidden.has(row.id)) ?? null;
  const shownSelected = shown.find((row) => row.id === selectedId) ?? null;

  const choose = (id: string) => {
    setSelectedId(id);
    onSelect?.(id);
  };

  const hide = (id: string) => {
    setHidden((prev) => {
      const next = new Set(prev);
      next.add(id);
      return next;
    });
  };

  const openMission = (id: string) => {
    choose(id);
    setView('mission');
  };

  return (
    <div className="di-scan-body" data-testid="di-scan-panel" data-view={view}>
      {view === 'list' && (
        <>
          <header className="di-scan-header">
            <div className="di-scan-header-lead">
              <button
                type="button"
                ref={minimizeRef}
                className="di-scan-mark"
                onClick={onMinimize}
                title="Minimize the office panel"
                aria-label="Minimize the office panel"
                data-testid="di-office-panel-minimize"
              >
                <DiMark />
              </button>
              <button
                type="button"
                className="di-scan-new"
                data-testid="di-scan-new-mission"
                disabled={onNewMission === undefined}
                title={
                  onNewMission === undefined
                    ? 'Mission requests are not available in this office'
                    : 'New mission'
                }
                onClick={(event) => onNewMission?.(event.currentTarget)}
              >
                + NEW MISSION
              </button>
            </div>
            <div className="di-scan-header-tools">
              <button
                type="button"
                className="di-scan-icon-button"
                aria-label="Settings"
                data-testid="di-scan-settings"
                onClick={() => onOpenSettings?.()}
              >
                <SlidersIcon />
              </button>
              <button
                type="button"
                className="di-scan-icon-button"
                aria-label="Filter missions"
                aria-pressed={needsOnly}
                data-testid="di-scan-filter"
                onClick={() => setNeedsOnly((value) => !value)}
              >
                <FilterIcon />
              </button>
            </div>
          </header>

          <div className="di-scan-tabs">
            <div className="di-scan-tablist" role="tablist" aria-label="Missions">
              {DI_SCAN_TABS.map((item) => (
                <button
                  key={item.id}
                  type="button"
                  role="tab"
                  className="di-scan-tab"
                  aria-selected={tab === item.id}
                  data-testid={`di-scan-tab-${item.id}`}
                  onClick={() => setTab(item.id)}
                >
                  {item.label}
                  {item.id === 'open' && attention > 0 && (
                    <span className="di-scan-badge" data-testid="di-scan-attention">
                      {attention}
                    </span>
                  )}
                </button>
              ))}
            </div>
            <label className="di-scan-search">
              <SearchIcon />
              <input
                type="search"
                value={query}
                placeholder="Search missions..."
                aria-label="Search missions"
                data-testid="di-scan-search"
                onChange={(event) => setQuery(event.target.value)}
              />
            </label>
          </div>

          <div className="di-scan-list" data-testid="di-missions">
            {shown.length === 0 ? (
              <p className="di-scan-empty">No missions in this list.</p>
            ) : (
              shown.map((row) => (
                <button
                  key={row.id}
                  type="button"
                  className="di-scan-row"
                  data-testid="di-scan-row"
                  data-mission-ref={row.id}
                  aria-pressed={selectedId === row.id}
                  onClick={() => (selectedId === row.id ? openMission(row.id) : choose(row.id))}
                >
                  <span className="di-scan-row-copy">
                    <span className="di-scan-title">{row.title}</span>
                    <span className="di-scan-context">{row.context}</span>
                  </span>
                  <Pills pills={row.pills} />
                </button>
              ))
            )}
          </div>

          {shownSelected && (
            <section
              className="di-scan-detail"
              data-testid="di-scan-detail"
              aria-label={shownSelected.title}
            >
              <button
                type="button"
                className="di-scan-detail-open"
                data-testid="di-scan-open-mission"
                onClick={() => openMission(shownSelected.id)}
              >
                <span className="di-scan-detail-top">
                  <span className="di-scan-title">{shownSelected.title}</span>
                  <Pills pills={shownSelected.pills} />
                </span>
                {shownSelected.description !== '' && (
                  <p className="di-scan-desc" title={shownSelected.description}>
                    {shownSelected.description}
                  </p>
                )}
                {shownSelected.swarm.length > 0 && (
                  <ul className="di-scan-swarm" data-testid="di-scan-swarm">
                    {shownSelected.swarm.map((member) => (
                      <li key={`${member.role}-${member.name}`} className="di-scan-member">
                        <span className="di-scan-role">{member.role}</span>
                        <Avatar member={member} />
                        <span className="di-scan-name">{member.name}</span>
                      </li>
                    ))}
                  </ul>
                )}
              </button>
              <div className="di-scan-detail-foot">
                <button
                  type="button"
                  className="di-scan-trash"
                  aria-label={`Clear ${shownSelected.title}`}
                  data-testid="di-scan-trash"
                  onClick={() => hide(shownSelected.id)}
                >
                  <TrashIcon />
                </button>
                <MetaLine text={shownSelected.footer} />
              </div>
            </section>
          )}

          <footer className="di-scan-footer">
            <button
              type="button"
              className="di-scan-reception"
              data-testid="di-open-reception"
              data-attention={receptionAttention ? 'true' : undefined}
              onClick={(event) => onOpenReception?.(event.currentTarget)}
            >
              <BellIcon />
              RECEPTION
            </button>
            <button
              type="button"
              className="di-scan-clear"
              data-testid="di-scan-clear"
              onClick={() =>
                setHidden((prev) => {
                  const next = new Set(prev);
                  for (const row of shown) next.add(row.id);
                  return next;
                })
              }
            >
              Clear all
            </button>
          </footer>
        </>
      )}

      {view !== 'list' && selected && (
        <Stage
          view={view}
          row={selected}
          onList={() => setView('list')}
          onMission={() => setView('mission')}
          onView={setView}
          onHide={() => hide(selected.id)}
          minimizeRef={minimizeRef}
          onOpenSettings={onOpenSettings}
          onFocusAgent={onFocusAgent}
          onOpenReview={onOpenReview}
        />
      )}
    </div>
  );
}
