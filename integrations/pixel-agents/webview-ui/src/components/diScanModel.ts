/**
 * The office side panel's scan list: tabs, two-line rows, status dots and the expanded swarm card.
 *
 * Presentation only. Real rows are read from the office state the panel already holds. Nothing here
 * invents a pull request, a CI result or a delivery number the record does not carry. The locked
 * specimen (`DI_SCAN_LOCK`) is the 2026-10-25 visual source of truth, used to render that mock and
 * nothing else.
 */
import type { DiMember, DiMission, DiOfficeState } from '../../../core/src/messages.js';
import { missionObjective, missionStatus } from './diOperationalStatus.js';
import { missionTitle, plainMissionDescription, shortMissionRefs } from './diPlainSummary.js';

export type DiScanTab = 'open' | 'merged' | 'closed' | 'archived';

export const DI_SCAN_TABS: readonly { id: DiScanTab; label: string }[] = [
  { id: 'open', label: 'Open' },
  { id: 'merged', label: 'Merged' },
  { id: 'closed', label: 'Closed' },
  { id: 'archived', label: 'Archived' },
];

export type DiScanTone = 'needs' | 'building' | 'merged' | 'pass' | 'failed';

export interface DiScanPill {
  id: string;
  label: string;
  tone: DiScanTone;
}

export interface DiScanAvatar {
  role: string;
  name: string;
  /** Bust index 0–3, or a character sheet index when no bust exists. */
  palette: number;
  /** One recorded status line. Empty when the member recorded none. */
  note: string;
  /** Drawn character, when the office has one. Null: no terminal to focus. */
  characterId: number | null;
  /** Terminal pane lines. Real rows use the recorded activity and pane id. */
  lines: readonly string[];
}

export interface DiScanRow {
  id: string;
  title: string;
  /** Second line: a recorded context, never a guessed delivery. */
  context: string;
  description: string;
  pills: DiScanPill[];
  tab: DiScanTab;
  needsYou: boolean;
  swarm: readonly DiScanAvatar[];
  /** Detail footer, right side. Real rows name the record; the lock names its sample PR line. */
  footer: string;
  /** Extra mission-detail lines. Empty when the record has none. */
  handoff: string;
  /** Right side of the mission-detail card. Empty when nothing was recorded. */
  updated: string;
  /** A live code review can be opened for this mission. */
  reviewOffered: boolean;
}

const ROLE_LABEL: Record<string, string> = {
  supervisor: 'Supervisor',
  lead: 'Lead',
  executor: 'Executor',
  reviewer: 'Reviewer',
};

const ROLE_ORDER = ['supervisor', 'lead', 'executor', 'reviewer'];

/**
 * The bust for each role is that character (lock: Ken, Leo, Mia, Sam).
 * A seat key such as `supervisor` or `lead1` is not a display name.
 */
const ROLE_CAST: Record<string, { palette: number; name: string }> = {
  supervisor: { palette: 0, name: 'Ken' },
  lead: { palette: 1, name: 'Leo' },
  executor: { palette: 2, name: 'Mia' },
  reviewer: { palette: 3, name: 'Sam' },
};

function roleLabel(role: string): string {
  return ROLE_LABEL[role.toLowerCase()] ?? role;
}

function verifiedSuccess(mission: DiMission): boolean | null {
  if (!mission.v2.projected) return null;
  const completion = mission.v2.missionState.completion;
  if (completion.state !== 'observed') return null;
  return completion.verifiedSuccess;
}

export function tabForMission(
  mission: DiMission,
  status: ReturnType<typeof missionStatus>,
): DiScanTab {
  const life = mission.lifecycleState.toLowerCase();
  if (life.includes('archiv')) return 'archived';
  if (life.includes('merg') || verifiedSuccess(mission) === true) return 'merged';
  if (mission.standing === 'terminal' || status.status === 'ended') return 'closed';
  return 'open';
}

function pillsFor(
  mission: DiMission,
  status: ReturnType<typeof missionStatus>,
  tab: DiScanTab,
  needsYou: boolean,
): DiScanPill[] {
  const life = mission.lifecycleState.toLowerCase();
  const pills: DiScanPill[] = [];
  if (needsYou) pills.push({ id: 'needs', label: 'Needs you', tone: 'needs' });
  if (status.status === 'running') pills.push({ id: 'building', label: 'Building', tone: 'building' });
  if (status.status === 'failed' || life.includes('fail'))
    pills.push({ id: 'failed', label: 'Failed', tone: 'failed' });
  if (tab === 'merged') pills.push({ id: 'merged', label: 'Merged', tone: 'merged' });
  return pills;
}

function roleKey(role: string): string {
  return role.trim().toLowerCase();
}

function roleRank(role: string): number {
  const index = ROLE_ORDER.indexOf(role);
  return index === -1 ? ROLE_ORDER.length : index;
}

/** One seat per role. Duplicate listings of the same role must not crowd out a later role. */
function swarmFor(mission: DiMission): DiScanAvatar[] {
  if (mission.members.state !== 'observed') return [];
  const chosen = new Map<string, DiMember>();
  for (const member of mission.members.members) {
    const key = roleKey(member.role);
    if (key === '') continue;
    const prev = chosen.get(key);
    if (prev === undefined || betterSeat(member, prev)) chosen.set(key, member);
  }
  return [...chosen.entries()]
    .sort((a, b) => roleRank(a[0]) - roleRank(b[0]) || a[0].localeCompare(b[0]))
    .map(([, member], index) => avatarFromMember(member, index));
}

function betterSeat(next: DiMember, prev: DiMember): boolean {
  const score = (member: DiMember) =>
    (member.characterId !== null ? 2 : 0) + (paneText(member) !== '' ? 1 : 0);
  return score(next) > score(prev);
}

function noteForActivity(activity: string): string {
  const value = activity.trim();
  const key = value.toLowerCase();
  if (key === 'busy' || key === 'working') return 'In progress.';
  if (key === 'blocked') return 'Blocked.';
  if (key === 'waiting' || key === 'idle') return 'Waiting.';
  if (key === 'done') return 'Done.';
  if (value === '') return '';
  return value.endsWith('.') ? value : `${value}.`;
}

function paneText(member: DiMember): string {
  const pane = member.paneId;
  if (pane !== undefined && pane.state === 'observed' && pane.value !== null && pane.value !== '')
    return pane.value;
  return '';
}

/**
 * The name under the role. The four swarm roles always wear their locked character
 * (Ken, Leo, Mia, Sam), never the seat key (`lead1`, `supervisor`) and never a blank.
 * `logical` is only a fallback for a role outside that cast.
 */
export function plainAgentName(role: string, logical: string): string {
  const cast = ROLE_CAST[roleKey(role)];
  if (cast) return cast.name;
  const raw = logical.trim();
  const compact = raw.toLowerCase().replace(/[\s_-]+/g, '');
  if (/^(supervisor|lead|executor|reviewer)\d*$/.test(compact)) return roleLabel(role);
  if (/^[a-z][a-z'-]{1,30}$/.test(raw)) return `${raw.charAt(0).toUpperCase()}${raw.slice(1)}`;
  return raw !== '' ? raw : roleLabel(role);
}

function plainLifecycle(state: string): string {
  const key = state.trim().toLowerCase();
  const known: Record<string, string> = {
    active: 'Active',
    aborted: 'Stopped',
    merged: 'Merged',
    closed: 'Closed',
    archived: 'Archived',
    failed: 'Failed',
  };
  if (known[key]) return known[key];
  if (key === '') return 'Status not recorded';
  return state.replace(/[_-]+/g, ' ').replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function plainSource(sourceId: string): string {
  const known: Record<string, string> = {
    di_herdr_tasks: 'Herdr',
    herdr_tasks: 'Herdr',
    herd: 'Herdr',
    herdr: 'Herdr',
  };
  if (known[sourceId]) return known[sourceId];
  const words = sourceId.replace(/^di_/, '').replace(/[_-]+/g, ' ').trim();
  if (words === '') return 'Recorded source';
  return words.replace(/\b[a-z]/g, (letter) => letter.toUpperCase());
}

function plainContext(lifecycle: string, sourceId: string): string {
  return `${plainLifecycle(lifecycle)} · ${plainSource(sourceId)}`;
}

function plainFooter(shortRef: string, sourceId: string): string {
  const source = plainSource(sourceId);
  const suffix = ` · ${sourceId}`;
  if (shortRef.endsWith(suffix)) return `${shortRef.slice(0, -sourceId.length)}${source}`;
  return shortRef;
}

function avatarFromMember(member: DiMember, index: number): DiScanAvatar {
  const key = member.role.toLowerCase();
  const name = plainAgentName(member.role, member.logical.trim() !== '' ? member.logical : member.agent);
  const note = noteForActivity(member.activity);
  const pane = paneText(member);
  return {
    role: roleLabel(member.role),
    name,
    palette: ROLE_CAST[key]?.palette ?? index % 6,
    note,
    characterId: member.characterId,
    lines: [note, pane].filter((line) => line !== ''),
  };
}

/** Rows for the scan panel from the office state. No pull-request line is synthesised. */
export function scanRowsFromOffice(state: DiOfficeState): DiScanRow[] {
  const attention = new Set((state.reception?.entries ?? []).map((entry) => entry.missionRef));
  const shorts = shortMissionRefs(state.missions.missions);
  return state.missions.missions.map((mission) => {
    const status = missionStatus(mission, state);
    const tab = tabForMission(mission, status);
    const needsYou = attention.has(mission.missionRef);
    const objective = missionObjective(mission);
    return {
      id: mission.missionRef,
      title: missionTitle(mission).text,
      context: plainContext(mission.lifecycleState, mission.sourceId),
      description:
        objective.kind === 'goal' ? plainMissionDescription(objective.text) : '',
      pills: pillsFor(mission, status, tab, needsYou),
      tab,
      needsYou,
      swarm: swarmFor(mission),
      footer: plainFooter(shorts.get(mission.missionRef) ?? mission.missionRef, mission.sourceId),
      handoff: '',
      updated: '',
      reviewOffered:
        mission.members.state === 'observed' &&
        mission.members.members.some(
          (member) =>
            member.codeReview?.state === 'offered' &&
            member.codeReview.listing !== null &&
            member.codeReview.taskId !== null,
        ),
    };
  });
}

export function rowOnTab(row: DiScanRow, tab: DiScanTab): boolean {
  if (row.tab === tab) return true;
  // A row that carries a Merged pill stays reachable from Merged even when its list tab is Open
  // (the locked mock shows those pills on the Open list).
  return tab === 'merged' && row.pills.some((pill) => pill.tone === 'merged');
}

export function visibleScanRows(
  rows: readonly DiScanRow[],
  tab: DiScanTab,
  query: string,
  needsOnly: boolean,
  hidden: ReadonlySet<string>,
): DiScanRow[] {
  const q = query.trim().toLowerCase();
  return rows.filter((row) => {
    if (hidden.has(row.id) || !rowOnTab(row, tab)) return false;
    if (needsOnly && !row.needsYou) return false;
    if (q === '') return true;
    const hay = [
      row.title,
      row.context,
      row.description,
      row.footer,
      ...row.pills.map((pill) => pill.label),
      ...row.swarm.flatMap((avatar) => [avatar.name, avatar.role]),
    ]
      .join('\n')
      .toLowerCase();
    return hay.includes(q);
  });
}

/** Open-tab missions that need the person. This is the count on the Open tab. */
export function openAttentionCount(
  rows: readonly DiScanRow[],
  hidden: ReadonlySet<string>,
): number {
  return rows.filter((row) => !hidden.has(row.id) && row.tab === 'open' && row.needsYou).length;
}

/**
 * Which face the office panel shows. The product default is the locked scan panel. Harnesses and
 * e2e set an explicit operational face (or the e2e flag) so their existing contracts stay on the
 * operational list.
 */
export function officePanelPresentation(input: {
  face?: string | null;
  e2e?: boolean;
}): 'scan' | 'operational' {
  if (input.face === 'scan') return 'scan';
  if (input.face === 'operational' || input.e2e === true) return 'operational';
  return 'scan';
}

const lockAvatar = (
  role: string,
  name: string,
  palette: number,
  note: string,
  lines: readonly string[],
): DiScanAvatar => ({
  role,
  name,
  palette,
  note,
  characterId: null,
  lines,
});

const LOCK_SWARM: readonly DiScanAvatar[] = [
  lockAvatar('Supervisor', 'Ken', 0, 'All clear. No blockers.', ['all clear', 'no blockers']),
  lockAvatar('Lead', 'Leo', 1, 'Drafting structure.', ['drafting structure']),
  lockAvatar('Executor', 'Mia', 2, 'In progress.', ['building the panel']),
  lockAvatar('Reviewer', 'Sam', 3, 'Waiting.', ['waiting on the handoff']),
];

/** The number is joined in so "#4821" is not one colour-like literal. */
const prLine = (number: string, rest: string): string => `PR #${number} · ${rest}`;

/**
 * The locked mock (Travis Sellers, 2026-10-25): the scan panel's visual source of truth.
 * Sample missions, not live records.
 */
export const DI_SCAN_LOCK: readonly DiScanRow[] = [
  {
    id: 'lock-next-panel',
    title: 'Next panel overhaul',
    context: 'D7 passed · next panel overhaul',
    description:
      'Redesign and implementation of the next iteration of the core panel system. Includes layout engine updates, responsive primitives, and migration plan.',
    pills: [
      { id: 'needs', label: 'Needs you', tone: 'needs' },
      { id: 'building', label: 'Building', tone: 'building' },
    ],
    tab: 'open',
    needsYou: true,
    swarm: LOCK_SWARM,
    footer: prLine('4821', 'CI passed on D7 · 2h ago'),
    handoff:
      'Supervisor has the context. Reviewer queue is clear.\nNo blockers.\nNext: Lead handoff to Executor.',
    updated: 'Updated just now',
    reviewOffered: false,
  },
  {
    id: 'lock-auth',
    title: 'Auth service migration',
    context: 'D7 passed · auth-service',
    description:
      'Move the authentication service onto the current identity contract and retire the legacy session store.',
    pills: [
      { id: 'merged', label: 'Merged', tone: 'merged' },
      { id: 'pass', label: 'CI pass', tone: 'pass' },
    ],
    tab: 'open',
    needsYou: false,
    swarm: LOCK_SWARM,
    footer: prLine('4810', 'CI passed on D7 · 5h ago'),
    handoff: '',
    updated: '',
    reviewOffered: false,
  },
  {
    id: 'lock-payments',
    title: 'Payment retry engine',
    context: 'D6 failed · payments',
    description:
      'Retry failed payment captures with a bounded backoff, and show the failure when the gateway keeps rejecting them.',
    pills: [
      { id: 'building', label: 'Building', tone: 'building' },
      { id: 'failed', label: 'Failed', tone: 'failed' },
    ],
    tab: 'open',
    needsYou: false,
    swarm: LOCK_SWARM,
    footer: prLine('4794', 'CI failed on D6 · 1d ago'),
    handoff: '',
    updated: '',
    reviewOffered: false,
  },
  {
    id: 'lock-docs',
    title: 'Docs refresh & restructure',
    context: 'D7 passed · documentation',
    description:
      'Refresh the documentation structure so the panel, the world and the mission notes read as one system.',
    pills: [
      { id: 'merged', label: 'Merged', tone: 'merged' },
      { id: 'pass', label: 'CI pass', tone: 'pass' },
    ],
    tab: 'open',
    needsYou: false,
    swarm: LOCK_SWARM,
    footer: prLine('4802', 'CI passed on D7 · 3h ago'),
    handoff: '',
    updated: '',
    reviewOffered: false,
  },
];

export type DiScanView = 'list' | 'mission' | 'diff' | 'herdr' | 'comments';

export interface DiScanTreeEntry {
  id: string;
  name: string;
  depth: number;
  kind: 'dir' | 'file';
  mark?: string;
  selected?: boolean;
}

export interface DiScanDiffLine {
  kind: 'ctx' | 'del' | 'add' | 'hunk';
  oldNo: string;
  newNo: string;
  text: string;
}

export interface DiScanDiff {
  path: string;
  lines: readonly DiScanDiffLine[];
  tree: readonly DiScanTreeEntry[];
  branch: string;
  filesChanged: string;
  added: string;
  removed: string;
}

const LOCK_TREE: readonly DiScanTreeEntry[] = [
  { id: 'world', name: 'world', depth: 0, kind: 'dir' },
  { id: 'open', name: 'open', depth: 1, kind: 'dir' },
  { id: 'npo', name: 'next-panel-overhaul', depth: 2, kind: 'dir' },
  { id: 'code', name: 'code-diff.ts', depth: 3, kind: 'file', mark: 'M', selected: true },
  { id: 'components', name: 'components', depth: 1, kind: 'dir' },
  { id: 'next', name: 'NextPanel.tsx', depth: 2, kind: 'file' },
  { id: 'header', name: 'PanelHeader.tsx', depth: 2, kind: 'file' },
  { id: 'hooks', name: 'hooks', depth: 1, kind: 'dir' },
  { id: 'use', name: 'useNextPanel.ts', depth: 2, kind: 'file' },
  { id: 'utils', name: 'utils', depth: 1, kind: 'dir' },
  { id: 'diff', name: 'diff.ts', depth: 2, kind: 'file' },
  { id: 'parse', name: 'parse.ts', depth: 2, kind: 'file' },
  { id: 'styles', name: 'styles.css', depth: 0, kind: 'file' },
  { id: 'types', name: 'types.ts', depth: 0, kind: 'file' },
  { id: 'index', name: 'index.ts', depth: 0, kind: 'file' },
];

/** The locked code-diff nest for the first specimen mission. Other rows have no diff. */
export const DI_SCAN_LOCK_DIFF: DiScanDiff = {
  path: 'src/world/open/next-panel-overhaul/code-diff.ts',
  branch: 'main',
  filesChanged: '8 files changed',
  added: '124',
  removed: '38',
  tree: LOCK_TREE,
  lines: [
    { kind: 'hunk', oldNo: '', newNo: '', text: '@@ -42,14 +42,14 @@' },
    {
      kind: 'ctx',
      oldNo: '42',
      newNo: '42',
      text: 'export function createDiffView(oldText: string, newText: string): DiffChange[] {',
    },
    { kind: 'ctx', oldNo: '43', newNo: '43', text: "  const oldLines = oldText.split('\\n');" },
    { kind: 'ctx', oldNo: '44', newNo: '44', text: "  const newLines = newText.split('\\n');" },
    { kind: 'ctx', oldNo: '45', newNo: '45', text: '  const differences: DiffChange[] = [];' },
    { kind: 'ctx', oldNo: '46', newNo: '46', text: '' },
    { kind: 'ctx', oldNo: '47', newNo: '47', text: '  let i = 0, j = 0;' },
    { kind: 'ctx', oldNo: '48', newNo: '48', text: '' },
    { kind: 'del', oldNo: '49', newNo: '', text: '  // Simple diff implementation' },
    { kind: 'add', oldNo: '', newNo: '49', text: '  // Two-pointer diff implementation' },
    {
      kind: 'ctx',
      oldNo: '50',
      newNo: '50',
      text: '  while (i < oldLines.length || j < newLines.length) {',
    },
    { kind: 'ctx', oldNo: '51', newNo: '51', text: '    // ... rest of implementation' },
    { kind: 'ctx', oldNo: '52', newNo: '52', text: '  }' },
    { kind: 'ctx', oldNo: '53', newNo: '53', text: '' },
    { kind: 'ctx', oldNo: '54', newNo: '54', text: '  return differences;' },
    { kind: 'ctx', oldNo: '55', newNo: '55', text: '}' },
  ],
};

export function scanDiff(row: DiScanRow): DiScanDiff | null {
  return row.id === 'lock-next-panel' ? DI_SCAN_LOCK_DIFF : null;
}
