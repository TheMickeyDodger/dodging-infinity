/**
 * Product experience — the PLAIN FIRST LAYER of the DI office panel: a Mission's title and short
 * source-qualified ref, its team line, one short phrase per agent row, the runtime agents grouped
 * by what the source establishes, and the attention entry's words.
 *
 * Pure presentation over fields the office state ALREADY projects. Nothing is read, derived across
 * records or invented, and every exact value stays where it was (the inspector and the labelled
 * disclosures). The truth rules are the product's, not decoration:
 *  - a process observed running is not work: "Working" only where the agent's own terminal reports
 *    busy in a current observation (for a member: the server's `animateActive`, the working halo's
 *    rule); otherwise the row says what IS known, briefly, and an absent metric is simply not shown;
 *  - "no current Mission" is an assignment record, never idle, free or done — and it never hides
 *    the activity the agent's terminal does report;
 *  - a title is a verbatim slice of the objective, or an honest placeholder; never a summary;
 *  - a reason every row shares is said ONCE for the group; only a row that differs says its own.
 */
import type {
  DiMember,
  DiMission,
  DiOfficeState,
  DiReception,
  DiRuntimeAgent,
  DiRuntimeAgents,
} from '../../../core/src/messages.js';
import {
  DI_MISSION_TITLE_MAX_CHARS,
  DI_SHORT_REF_FULL_MAX_CHARS,
  DI_SHORT_REF_MIN_CHARS,
} from '../constants.js';
import type { DiFreshness, DiRecord } from './diNavigation.js';
import { plainBindingReason, plainIdentityReason, plainNotLive } from './diNavigation.js';
import { navigationOrder } from './diOfficeFormat.js';
import { missionObjective, shortMemberIdentity } from './diOperationalStatus.js';

// ── titles ─────────────────────────────────────────────────────────────────────────────────────

/** Said instead of a title when the objective's opening is only boilerplate (ids, headers). */
export const MISSION_TITLE_PLACEHOLDER = 'Objective text available: open to read';
/** The headline of a Mission whose source reported no goal; the source and state are secondary detail. */
export const MISSION_TITLE_NO_GOAL = 'No goal reported';

export interface DiMissionTitle {
  /** sentence: a verbatim slice of the objective; placeholder: nothing meaningful opens it; no-goal: the source gave none. */
  kind: 'sentence' | 'placeholder' | 'no-goal';
  text: string;
}

/** An identifier, not words: a UUID, a dated task id, a prefixed or bare hex id (optionally @root). */
const ID = String.raw`(?:[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}|\d{8}-\d{6}(?:-[0-9a-z]+)?|[a-z]{1,4}-[0-9a-f]{6,}|[0-9a-f]{8,})(?:@[0-9a-z]+)?`;
const SEPARATORS = String.raw`[\s:#|·—–-]*`;
/** A label word that only introduces what follows (never an ordinary sentence's first word). */
const LEADING_LABEL = new RegExp(
  String.raw`^(?:authori[sz]ed\s+mission|objective|goal|task|mission)(?=\s*(?:[:—–-]|$)|\s+${ID}(?![\w-]))${SEPARATORS}`,
  'i',
);
const LEADING_ID = new RegExp(String.raw`^${ID}(?![\w-])${SEPARATORS}`, 'i');
/** A whole line that is a heading: ALL CAPS ("OBJECTIVE", "AUTHORIZED MISSION") or a short "Label:". */
const HEADING_LINE = /^(?:[\p{Lu}\d &/_-]+|[\p{L} ]{1,40}:)$/u;
/** The handoff's parenthetical identity: revision and proposal digest. Never a title. */
const DIGEST_CLAUSE = /\(\s*revision\s+\d+\s*,\s*proposal digest\s+[0-9a-f]+\s*\)\.?/i;
/** The fixed sentence that follows the handoff prefix. It names no goal. */
const HANDOFF_LEAD = /^This is the approved Mission content only\b[\s\S]*?(?:\.\s*|$)/i;
const LONG_HEX = /[0-9a-f]{16,}/i;
const HANDOFF_LEAD_TEXT = 'this is the approved mission content only';
/** A cut-off start of the handoff's fixed sentence ("This is …", "This is the appr…"): never a title. */
function truncatedHandoffLead(text: string): boolean {
  const m = /^(.*?)\s*(?:…|\.\.\.)\s*$/su.exec(text.trim());
  if (!m) return false;
  const head = m[1].replace(/\s+/g, ' ').trim().toLowerCase();
  return head.length >= 4 && HANDOFF_LEAD_TEXT.startsWith(head);
}
const OBJECTIVE_SECTION =
  /(?:^|\n)\s*OBJECTIVE\s*\n+([\s\S]*?)(?=\n\s*(?:TARGET CONTEXT|REQUESTED SCOPE|APPROVED ACTION SCOPE|APPROVED DELIVERY TARGETS|DELIVERY)\b|$)/i;

function stripBoilerplate(line: string): string {
  let rest = line.replace(/^#+\s*/, '').trim();
  for (;;) {
    const next = rest.replace(LEADING_LABEL, '').replace(LEADING_ID, '').trim();
    if (next === rest) return rest;
    rest = next;
  }
}

/** Drop the authorization prefix, the digest clause, and the handoff's fixed sentence. */
function stripAuthority(line: string): string {
  const rest = stripBoilerplate(line).replace(DIGEST_CLAUSE, ' ').replace(HANDOFF_LEAD, '').replace(/\s{2,}/g, ' ').trim();
  return truncatedHandoffLead(rest) ? '' : rest;
}

function usableTitle(sentence: string): boolean {
  if (!meaningful(sentence)) return false;
  if (LONG_HEX.test(sentence)) return false;
  if (/proposal digest/i.test(sentence)) return false;
  if (/^this is the approved mission content only/i.test(sentence)) return false;
  if (truncatedHandoffLead(sentence)) return false;
  return true;
}

/** The first sentence: up to a stop followed by a capital, a digit, a quote or the end. */
function firstSentence(text: string): string {
  const end = /[.!?](?=\s+[\p{Lu}\d"“(]|\s*$)/u.exec(text);
  const sentence = end ? text.slice(0, end.index) : text;
  return sentence.replace(/[\s.!?:;,]+$/, '');
}

/** Words carry meaning; ids and single labels do not. */
const meaningful = (text: string) => (text.match(/\p{L}{2,}/gu) ?? []).length >= 2;

/** Cut at a word boundary, with an explicit ellipsis, within the pinned bound. */
function bounded(sentence: string): string {
  if (sentence.length <= DI_MISSION_TITLE_MAX_CHARS) return sentence;
  const room = sentence.slice(0, DI_MISSION_TITLE_MAX_CHARS - 1);
  const space = room.lastIndexOf(' ');
  const kept = space > DI_MISSION_TITLE_MAX_CHARS / 2 ? room.slice(0, space) : room;
  return `${kept.replace(/[\s,;:—–-]+$/, '')}…`;
}

/**
 * A Mission's human-meaningful title. The projection carries no separate title field, so the only
 * source-backed meaning is the objective's own text: its first meaningful sentence, after any
 * opening boilerplate (a heading line, "AUTHORIZED MISSION", an id, the revision and proposal-digest
 * clause, the handoff's fixed sentence). A long hex string is never a title. When nothing meaningful
 * opens it, an honest placeholder; when the source gave no goal, its existing source-qualified words.
 * The complete objective is never altered and stays in the inspector.
 */
export function missionTitle(mission: DiMission): DiMissionTitle {
  const objective = missionObjective(mission);
  // Plain-English naming (owner ruling, 2026-10-28): the HEADLINE carries no source id and no state code. It says what the state says, that this source REPORTED no goal (not that none exists);
  // the source-qualified note with its state stays as secondary detail (missionObjective's text, shown beside the title).
  if (objective.kind === 'no-goal') return { kind: 'no-goal', text: MISSION_TITLE_NO_GOAL };
  for (const raw of objective.text.split(/\r?\n/)) {
    const line = raw.trim();
    if (line === '' || HEADING_LINE.test(line.replace(/^#+\s*/, ''))) continue;
    const rest = stripAuthority(line);
    if (rest === '') continue;
    const sentence = firstSentence(rest);
    if (!usableTitle(sentence)) continue;
    return { kind: 'sentence', text: bounded(sentence) };
  }
  return { kind: 'placeholder', text: MISSION_TITLE_PLACEHOLDER };
}

/**
 * The goal in plain words for a card: the OBJECTIVE section when the handoff carries one, otherwise
 * the text with the authorization prefix, proposal digest and long hex runs removed. Empty when
 * nothing but that boilerplate was recorded.
 */
export function plainMissionDescription(text: string): string {
  const section = OBJECTIVE_SECTION.exec(text);
  let body = (section?.[1] ?? text).replace(DIGEST_CLAUSE, ' ');
  body = body.replace(/AUTHORIZED MISSION\b[^\n]*/gi, ' ');
  body = body.replace(HANDOFF_LEAD, ' ');
  body = body.replace(/\b[0-9a-f]{16,}\b/gi, ' ');
  body = body
    .replace(/[ \t]{2,}/g, ' ')
    .replace(/[ \t]*\n[ \t]*/g, '\n')
    .replace(/\n{3,}/g, '\n\n')
    .trim();
  if (!usableTitle(body.replace(/\n/g, ' '))) return '';
  // Full text, no character cap: Mission details scrolls inside its card, and the Home detail card clamps with CSS (-webkit-line-clamp).
  return body;
}

// ── short refs ─────────────────────────────────────────────────────────────────────────────────

/**
 * Each Mission's SHORT source-qualified reference ("<native id> · <source>"): the native id whole
 * when it is short, else the shortest prefix no other Mission of the same source shares (never
 * shorter than the office zone label) and an ellipsis. Unique in the view; the complete ref stays
 * in the inspector and in the element's title.
 */
export function shortMissionRefs(missions: readonly DiMission[]): Map<string, string> {
  const out = new Map<string, string>();
  for (const m of missions) {
    const native = m.nativeId || m.missionRef;
    let label = native;
    if (native.length > DI_SHORT_REF_FULL_MAX_CHARS) {
      const others = missions
        .filter((o) => o.sourceId === m.sourceId && o.missionRef !== m.missionRef)
        .map((o) => o.nativeId || o.missionRef);
      let length = DI_SHORT_REF_MIN_CHARS;
      while (length < native.length && others.some((o) => o.startsWith(native.slice(0, length))))
        length += 1;
      label = length < native.length ? `${native.slice(0, length)}…` : native;
    }
    out.set(m.missionRef, `${label} · ${m.sourceId}`);
  }
  // Belt and braces: a label two records would share falls back to the complete ref.
  const seen = new Map<string, number>();
  for (const v of out.values()) seen.set(v, (seen.get(v) ?? 0) + 1);
  for (const [ref, v] of out) if ((seen.get(v) ?? 0) > 1) out.set(ref, ref);
  return out;
}

// ── the team line ──────────────────────────────────────────────────────────────────────────────

export interface DiTeamSummary {
  /** The distinct roles, in roster order. */
  roles: string[];
  /** Process observation, worded for what it counts. */
  running: string;
  /** Terminal activity, only where it is reported; null when it is not (nothing is shown then). */
  activity: string | null;
}

/** Terminal activities that are known and are not busy (the source's own words). */
const KNOWN_NOT_BUSY = ['idle', 'done', 'blocked'];

const observedRunning = (m: DiMember) => m.live && m.classification === 'present';

export function teamSummary(mission: DiMission): DiTeamSummary {
  const members = mission.members.members;
  const roles: string[] = [];
  for (const m of members) if (!roles.includes(m.role)) roles.push(m.role);
  const running = members.filter(observedRunning);
  // The server's `animateActive` (the working halo's rule): live, activity known, exactly `busy`.
  const busy = running.filter((m) => m.animateActive).length;
  const activity =
    busy > 0
      ? `${busy} ${busy === 1 ? 'terminal' : 'terminals'} busy`
      : running.length > 0 && running.every((m) => KNOWN_NOT_BUSY.includes(m.activity))
        ? 'no terminal busy'
        : null;
  return {
    roles,
    running: `${running.length} of ${members.length} members observed running`,
    activity,
  };
}

// ── one short phrase per agent ─────────────────────────────────────────────────────────────────

export type DiAgentTone =
  'busy' | 'blocked' | 'quiet' | 'unreported' | 'absent' | 'stale' | 'unlinked';

export interface DiAgentPhrase {
  tone: DiAgentTone;
  /** A shape, so the phrase never rests on colour alone. */
  glyph: string;
  text: string;
}

export const DI_AGENT_GLYPH: Record<DiAgentTone, string> = {
  busy: '●',
  blocked: '▲',
  quiet: '◐',
  unreported: '○',
  absent: '◌',
  stale: '◇',
  unlinked: '△',
};

const phrase = (tone: DiAgentTone, text: string): DiAgentPhrase => ({
  tone,
  glyph: DI_AGENT_GLYPH[tone],
  text,
});

const isRuntime = (a: DiRuntimeAgent | DiMember): a is DiRuntimeAgent => 'identityVerified' in a;

/** A not-live reason that is the VIEW's or the office's (not current), not the agent's own. */
const notCurrent = (reason: string | null) =>
  reason !== null &&
  (reason.startsWith('transport_') ||
    reason.startsWith('client_expired') ||
    reason.startsWith('expired_locally') ||
    reason === 'office_not_live');

/**
 * One short, true phrase for an agent. While it is observed running now, what its OWN TERMINAL
 * reports — worded as the terminal's state ("Terminal busy"), so it cannot be read as task
 * progress, completion or availability; otherwise why it is not shown as running. The exact facts
 * are in the dock and the inspector.
 */
export function agentPhrase(agent: DiRuntimeAgent | DiMember): DiAgentPhrase {
  if (isRuntime(agent) && !agent.identityVerified)
    return phrase('unlinked', 'Not linked to its role');
  const running = isRuntime(agent) ? agent.live : observedRunning(agent);
  if (running) {
    const busy = isRuntime(agent) ? agent.activity === 'busy' : agent.animateActive;
    if (busy) return phrase('busy', 'Terminal busy');
    switch (agent.activity) {
      case 'blocked':
        return phrase('blocked', 'Terminal blocked');
      case 'idle':
      case 'done':
        return phrase('quiet', 'Terminal not busy');
      case null:
        return phrase('unreported', 'Activity not reported');
      default:
        return phrase('unreported', 'Activity unknown');
    }
  }
  if (notCurrent(agent.notLiveReason)) return phrase('stale', 'Not current');
  if (!isRuntime(agent) && agent.classification !== 'present')
    return phrase('absent', `Not observed running · ${shortMemberIdentity(agent.classification)}`);
  return phrase('absent', 'Not observed running');
}

/**
 * The details' first sentence about an agent: what it is doing, as its own terminal reports it
 * (and, when busy, that this is terminal activity — not task progress or completion), or why it is
 * not shown running. The exact identity, codes and times follow behind a labelled disclosure.
 */
export function agentNow(agent: DiRuntimeAgent | DiMember): string {
  if (isRuntime(agent) && !agent.identityVerified)
    return 'An agent with this name was found running, but it can’t be safely linked to its recorded role.';
  const said = agentPhrase(agent);
  switch (said.tone) {
    case 'busy':
      return 'Its terminal reports busy right now. That is what its terminal is doing, not progress on the task or its completion.';
    case 'blocked':
      return 'Its terminal reports blocked right now.';
    case 'quiet':
      return `Its terminal reports “${agent.activity}” right now: not busy.`;
    case 'unreported':
      return agent.activity === null
        ? 'It is observed running; its terminal activity is not reported.'
        : `It is observed running; its terminal activity is unknown (“${agent.activity}”).`;
    case 'stale':
      return `Not current: ${plainNotLive(agent.notLiveReason)}.`;
    default:
      return !isRuntime(agent) && agent.classification !== 'present'
        ? `Not observed running now: ${shortMemberIdentity(agent.classification)}.`
        : 'Not observed running now.';
  }
}

/**
 * What a verified runtime agent's assignment record says, in one plain sentence: it is not a team
 * member, and "no current Mission" is a record of assignment — never of activity. A state this
 * view does not recognise asserts nothing about a task.
 */
export function runtimeAgentMeaning(agent: Pick<DiRuntimeAgent, 'assignmentState'>): string {
  switch (agent.assignmentState) {
    case 'unassigned':
      return 'Not a team member: no current Mission is on record for it. That is an assignment record, not its activity.';
    case 'unverified_reference':
      return 'Not a team member: a task is on record for it, but it is not confirmed that it belongs to it.';
    default:
      return 'Not a team member: its assignment record is in a state this view does not recognise, so whether it has a task is unknown.';
  }
}

// ── runtime agents, grouped by what the source establishes ─────────────────────────────────────

export interface DiRuntimeRow {
  agent: DiRuntimeAgent;
  /** Its herd — the source-qualified root it runs under — always on the row: twins stay apart. */
  herd: string;
  /** Its activity, only where it differs from what the group says once. */
  phrase: DiAgentPhrase | null;
  /** Its own reason or record, only where it differs from what the group and herd lines say. */
  exception: string | null;
  /** The agent's own name, only where two rows of one herd share a role name. */
  disambiguator: string | null;
}

export type DiRuntimeGroupKey =
  'no-mission' | 'task-unconfirmed' | 'assignment-unknown' | 'not-linked';

export interface DiRuntimeGroup {
  key: DiRuntimeGroupKey;
  heading: string;
  /** What every row in the group shares, said once. */
  sentence: string;
  /** The activity all (or most) rows share, said once ("All 8: terminal busy."), else null. */
  activity: string | null;
  /** Reasons or records said once: for the group ("Why: …"), else per herd ("act01: …"). */
  notes: string[];
  count: number;
  /** Ordered herd by herd. */
  rows: DiRuntimeRow[];
}

const GROUP_WORDS: Record<DiRuntimeGroupKey, { heading: string; sentence: string }> = {
  'no-mission': {
    heading: 'No current Mission',
    sentence:
      'Identity confirmed, but no Mission is on record for them right now. That is an assignment record only, not what they are doing.',
  },
  'task-unconfirmed': {
    heading: 'Task not confirmed',
    sentence:
      'Identity confirmed and a task is on record for them, but it is not confirmed that they belong to it.',
  },
  'assignment-unknown': {
    heading: 'Assignment not recognised',
    sentence:
      'Identity confirmed, but their assignment record is in a state this view does not recognise, so whether they have a task is unknown. The exact state is in the evidence.',
  },
  'not-linked': {
    heading: 'Can’t be linked to a role',
    sentence:
      'An agent with each name was found running, but it can’t be safely linked to its recorded role, so it is not drawn and is not counted as a team member.',
  },
};

/** The server's words for assignment reasons it names (officeView.ts), else the binding words. */
const ASSIGNMENT_REASON_TEXT: Record<string, string> = {
  root_not_captured_over_capacity: 'its root was not captured (capacity)',
  role_not_in_the_bound_team_roster: "it is not in its task's team roster",
  task_status_unrecognized: "the task record's status is not recognized",
};
const assignmentReason = (reason: string) =>
  ASSIGNMENT_REASON_TEXT[reason] ?? plainBindingReason(reason);

const taskRecordNote = (status: string | null) =>
  status === null ? 'no task record' : `last task record ${status}`;

/**
 * The three assignment cases the server keeps (officeView.ts), never folded: `unassigned`,
 * `unverified_reference`, and anything else — an unrecognised (`unrecognized:<value>`), empty or
 * future state, for which nothing about a task is asserted.
 */
function groupOf(a: DiRuntimeAgent): DiRuntimeGroupKey {
  if (!a.identityVerified) return 'not-linked';
  if (a.assignmentState === 'unassigned') return 'no-mission';
  if (a.assignmentState === 'unverified_reference') return 'task-unconfirmed';
  return 'assignment-unknown';
}

/** A herd's identity: the SOURCE and its root (canonical identity includes the source). */
const herdKey = (a: DiRuntimeAgent) => JSON.stringify([a.sourceId, a.rootRef]);

/**
 * Each herd's label for the first layer: its first registry alias (or root ref), shortened to the
 * shortest prefix no other herd shares; qualified by its source whenever the list holds more than
 * one source, or two herds would otherwise read alike. The complete root ref stays in the evidence.
 */
function herdLabels(agents: readonly DiRuntimeAgent[]): Map<string, string> {
  const herds = new Map<string, DiRuntimeAgent>();
  for (const a of agents) if (!herds.has(herdKey(a))) herds.set(herdKey(a), a);
  const base = new Map<string, string>();
  const names = [...herds].map(([k, a]) => [k, a.rootAliases[0] ?? a.rootRef] as const);
  for (const [key, name] of names) {
    let label = name;
    if (name.length > DI_SHORT_REF_FULL_MAX_CHARS) {
      const others = names.filter(([k]) => k !== key).map(([, n]) => n);
      let length = DI_SHORT_REF_MIN_CHARS;
      while (length < name.length && others.some((o) => o.startsWith(name.slice(0, length))))
        length += 1;
      label = length < name.length ? `${name.slice(0, length)}…` : name;
    }
    base.set(key, label);
  }
  const sources = new Set(agents.map((a) => a.sourceId));
  const seen = new Map<string, number>();
  for (const label of base.values()) seen.set(label, (seen.get(label) ?? 0) + 1);
  const out = new Map<string, string>();
  for (const [key, label] of base) {
    const a = herds.get(key)!;
    const qualified =
      sources.size > 1 || (seen.get(label) ?? 0) > 1 ? `${label} · ${a.sourceId}` : label;
    out.set(key, qualified);
  }
  // Still alike (same alias, same source, different roots): the complete root ref decides.
  const again = new Map<string, number>();
  for (const label of out.values()) again.set(label, (again.get(label) ?? 0) + 1);
  for (const [key, label] of out)
    if ((again.get(label) ?? 0) > 1) out.set(key, `${label} (${herds.get(key)!.rootRef})`);
  return out;
}

/** A runtime agent's herd label as the LIST shows it (source-qualified when the list is). */
export function runtimeHerdLabel(
  runtime: DiRuntimeAgents | undefined,
  agent: DiRuntimeAgent,
): string {
  return (
    herdLabels(runtime?.agents ?? [agent]).get(herdKey(agent)) ??
    agent.rootAliases[0] ??
    agent.rootRef
  );
}

/**
 * An agent's FIRST-LAYER name, the same on every surface that shows it (list row, Selected dock,
 * details heading), qualified only where the view needs it: a runtime agent by its herd (source-
 * qualified exactly as the list is) and, for twins inside one herd, its agent name; a team member
 * by its role and, when another member of the same Mission shares its name, its chain. Two records
 * that would otherwise read alike in the current view never share this name.
 */
export function firstLayerName(
  state: Pick<DiOfficeState, 'runtimeAgents'>,
  record:
    | { kind: 'runtime'; agent: DiRuntimeAgent }
    | { kind: 'member'; mission: DiMission; member: DiMember },
): string {
  if (record.kind === 'runtime') {
    const { agent } = record;
    const herd = runtimeHerdLabel(state.runtimeAgents, agent);
    const twins = (state.runtimeAgents?.agents ?? []).filter(
      (a) => herdKey(a) === herdKey(agent) && a.logical === agent.logical,
    ).length;
    return `${agent.logical} · ${herd}${twins > 1 ? ` · ${agent.agent}` : ''}`;
  }
  const { mission, member } = record;
  const twins = mission.members.members.filter((m) => m.logical === member.logical).length;
  const chain =
    member.chain === 'child'
      ? `linked child task ${member.chainTaskId ?? '(task not recorded)'}`
      : member.chain === 'direct'
        ? 'direct task'
        : member.chain;
  return `${member.logical} · ${member.role}${twins > 1 ? ` · ${chain}` : ''}`;
}

/**
 * The inspector's heading: who or what this is, by its first-layer name. A Mission is headed by its title, the plain-English name
 * the naming rule derives from its own goal text. Where the source reported NO goal, the facts support no name, so the heading says
 * exactly that ("No goal reported") and names no id; the inspector draws the short source-qualified ref on its own line beneath it.
 */
export function inspectorHeading(
  state: Pick<DiOfficeState, 'runtimeAgents'>,
  record: DiRecord | null,
  mission: DiMission | null,
): string {
  if (record?.target.kind === 'member')
    return firstLayerName(state, {
      kind: 'member',
      mission: record.target.mission,
      member: record.target.member,
    });
  if (record?.target.kind === 'runtime' || record?.target.kind === 'unverified')
    return firstLayerName(state, { kind: 'runtime', agent: record.target.agent });
  return mission ? missionTitle(mission).text : 'This record is no longer in the office state';
}

/** What most rows share, said once: "All N: x." / "n of N: x." (a clear majority), else null. */
function majority<T>(
  items: readonly T[],
  keyOf: (t: T) => string,
): { key: string; n: number } | null {
  const counts = new Map<string, number>();
  for (const t of items) counts.set(keyOf(t), (counts.get(keyOf(t)) ?? 0) + 1);
  const [key, n] = [...counts].sort((x, y) => y[1] - x[1])[0] ?? ['', 0];
  return n === items.length || (n >= 2 && n * 2 > items.length) ? { key, n } : null;
}

const sharedLine = (n: number, total: number) =>
  n === total ? (total === 1 ? '' : `All ${total}: `) : `${n} of ${total}: `;

/** The runtime agents in at most four groups, herd by herd; what rows share is said once. */
export function runtimeGroups(runtime: DiRuntimeAgents): DiRuntimeGroup[] {
  const labels = herdLabels(runtime.agents);
  const order: DiRuntimeGroupKey[] = [
    'no-mission',
    'task-unconfirmed',
    'assignment-unknown',
    'not-linked',
  ];
  return order.flatMap((key): DiRuntimeGroup[] => {
    const agents = runtime.agents.filter((a) => groupOf(a) === key);
    if (agents.length === 0) return [];
    // Herd by herd, in the order herds first appear.
    const herds = [...new Set(agents.map(herdKey))];
    const sorted = herds.flatMap((h) => agents.filter((a) => herdKey(a) === h));

    // Activity (verified groups): the phrase most rows share, once; rows that differ keep theirs.
    const phrases = new Map(sorted.map((a) => [a.agentRef, agentPhrase(a)]));
    const sharesActivity = key !== 'not-linked';
    const act = sharesActivity ? majority(sorted, (a) => phrases.get(a.agentRef)!.text) : null;
    const activity =
      act === null
        ? null
        : `${sharedLine(act.n, sorted.length)}${act.key.charAt(0).toLowerCase()}${act.key.slice(1)}.`;

    // Reasons or records: once for the group, else once per herd, else on the row.
    const reasonOf: ((a: DiRuntimeAgent) => string) | null =
      key === 'not-linked'
        ? (a) => plainIdentityReason(a.identityReason)
        : key === 'task-unconfirmed'
          ? (a) => assignmentReason(a.assignmentReason)
          : key === 'no-mission'
            ? (a) => taskRecordNote(a.taskStatus)
            : null;
    const notes: string[] = [];
    const exceptions = new Map<string, string>();
    if (reasonOf !== null) {
      const all = new Set(sorted.map(reasonOf));
      const top = key === 'no-mission' ? null : majority(sorted, reasonOf);
      if (all.size === 1)
        notes.push(
          key !== 'no-mission'
            ? `Why: ${reasonOf(sorted[0])}.`
            : sorted[0].taskStatus === null
              ? 'No task record for any of them.'
              : `Last task record for each: ${sorted[0].taskStatus}.`,
        );
      else if (top !== null) {
        notes.push(`Why (${top.n} of ${sorted.length}): ${top.key}.`);
        for (const a of sorted)
          if (reasonOf(a) !== top.key) exceptions.set(a.agentRef, reasonOf(a));
      } else
        for (const h of herds) {
          const inHerd = sorted.filter((a) => herdKey(a) === h);
          const reasons = new Set(inHerd.map(reasonOf));
          if (reasons.size === 1) notes.push(`${labels.get(h)}: ${reasonOf(inHerd[0])}.`);
          else for (const a of inHerd) exceptions.set(a.agentRef, reasonOf(a));
        }
    }

    const rows = sorted.map((agent): DiRuntimeRow => {
      const inHerd = sorted.filter((a) => herdKey(a) === herdKey(agent));
      const twins = inHerd.filter((a) => a.logical === agent.logical).length > 1;
      const own = phrases.get(agent.agentRef)!;
      return {
        agent,
        herd: labels.get(herdKey(agent))!,
        phrase: sharesActivity && (act === null || own.text !== act.key) ? own : null,
        exception: exceptions.get(agent.agentRef) ?? null,
        disambiguator: twins ? agent.agent : null,
      };
    });
    return [{ key, ...GROUP_WORDS[key], activity, notes, count: sorted.length, rows }];
  });
}

// ── time ───────────────────────────────────────────────────────────────────────────────────────

function age(seconds: number): string {
  const unit = (n: number, word: string) => `${n} ${word}${n === 1 ? '' : 's'} ago`;
  if (seconds < 60) return 'just now';
  if (seconds < 3600) return unit(Math.floor(seconds / 60), 'minute');
  if (seconds < 86400) return unit(Math.floor(seconds / 3600), 'hour');
  return unit(Math.floor(seconds / 86400), 'day');
}

/**
 * When something was observed, as a plain age on the OFFICE's clock (`times.evaluatedAt`), and —
 * when it is not current — why, in words. The exact ISO time and epoch stay one step away (the
 * element's title, the evidence disclosure and the inspector). Never guessed when not reported.
 */
export function plainObserved(
  freshness: DiFreshness,
  observedAt: number | null,
  nowSeconds: number,
): string {
  const when =
    observedAt === null
      ? 'Observation time not reported.'
      : `${freshness.current ? 'Observed' : 'Last observed'} ${age(Math.max(0, nowSeconds - observedAt))}.`;
  return freshness.current ? when : `${when} Not current: ${plainNotLive(freshness.reason)}.`;
}

// ── attention ──────────────────────────────────────────────────────────────────────────────────

export interface DiAttentionSummary {
  /** Every entry listed as needing attention (hidden in some view or not), plus any the cap left out. */
  count: number;
  text: string;
}

/**
 * The ONE attention entry's words. Zero is said as what it is — nothing LISTED — never "none" or
 * "all clear": what the desk cannot read is said once, by viewLimits. It takes no hidden set: hiding
 * an entry in a view resolves and dismisses nothing, so it cannot change how many need attention.
 */
export function attentionSummary(reception: DiReception): DiAttentionSummary {
  const notListed = Math.max(0, reception.entriesTotal - reception.entries.length);
  const count = reception.entries.length + notListed;
  return {
    count,
    text:
      count === 0
        ? 'Nothing listed as needing attention'
        : `${count} ${count === 1 ? 'needs' : 'need'} attention`,
  };
}

// ── what this view cannot see ──────────────────────────────────────────────────────────────────

export interface DiViewLimits {
  /** One plain sentence: what may be missing from this view. */
  summary: string;
  /** The exact limits, one per line (the categories' own labels; the coverage in words). */
  items: string[];
}

const lowerFirst = (text: string) => `${text.charAt(0).toLowerCase()}${text.slice(1)}`;

/**
 * The view's material limits as ONE plain sentence with its consequence — what may be missing —
 * and the exact limits one step away: attention kinds this office does not report, cannot read
 * now or reports only in part, and team members of linked child tasks when they are not covered.
 * Null when nothing is limited. The categories' exact outcomes and gaps stay in the desk.
 */
export function viewLimits(
  state: Pick<DiOfficeState, 'reception' | 'coverage'>,
): DiViewLimits | null {
  const items: string[] = [];
  const cats = state.reception?.categories ?? [];
  const names = (outcome: string) =>
    cats.filter((c) => c.outcome === outcome).map((c) => lowerFirst(c.label));
  const unsupported = names('unsupported');
  const unavailable = names('unavailable');
  const partial = names('partial');
  if (unsupported.length > 0) items.push(`Not reported by this office: ${unsupported.join(', ')}.`);
  if (unavailable.length > 0) items.push(`Could not be read just now: ${unavailable.join(', ')}.`);
  if (partial.length > 0) items.push(`Only partly reported: ${partial.join(', ')}.`);
  const attention = items.length > 0;
  const children = state.coverage.linkedChildRoles === 'not_covered';
  if (children)
    items.push(
      "Teams list each Mission's own task only: members working in its linked child tasks are not shown.",
    );
  if (items.length === 0) return null;
  const missing = [
    ...(attention ? ['everything that needs attention'] : []),
    ...(children ? ['team members working in linked child tasks'] : []),
  ];
  return {
    summary: `This view may not show ${missing.join(', or ')}.`,
    items,
  };
}

// ── where the selection is ─────────────────────────────────────────────────────────────────────

/**
 * Where the selected character is in the order the dock's arrows move through: "Agent 3 of 16 ·
 * Mission 1 of 2" (its Mission only when it is a team member), or how many agents are on the map.
 */
export function navigationOrientation(state: DiOfficeState, current: number | null): string {
  const order = navigationOrder(state);
  const at = current === null ? -1 : order.indexOf(current);
  if (at < 0) return `${order.length} ${order.length === 1 ? 'agent' : 'agents'} on the map`;
  const missions = state.missions.missions.filter((m) =>
    m.members.members.some((x) => x.characterId !== null),
  );
  const mi = missions.findIndex((m) => m.members.members.some((x) => x.characterId === current));
  return `Agent ${at + 1} of ${order.length}${mi < 0 ? '' : ` · Mission ${mi + 1} of ${missions.length}`}`;
}

// ── a Mission's results ────────────────────────────────────────────────────────────────────────

/**
 * A Mission's results in one plain sentence: reported and verified kept apart, an unreported
 * result said once (not once per field). The exact per-field facts stay in the inspector's details.
 */
export function missionResults(mission: DiMission): string {
  if (mission.herdrTask) return "Mission results aren't supported for a plain Herdr task record.";
  if (!mission.v2.projected)
    return "This office doesn't report Mission results: whether it is complete, verified, reviewed or delivered is unknown here.";
  const c = mission.v2.missionState.completion;
  if (c.state !== 'observed')
    return 'Mission results were not observed (the reason is in the details).';
  if (c.verifiedSuccess === true) return 'Verified success is recorded.';
  if (c.reportedComplete === true && c.reportedSuccess === false)
    return 'Reported complete without success (not verified).';
  if (c.reportedComplete === true) return 'Reported complete; success is not verified.';
  if (c.reportedComplete === false) return 'Not reported complete.';
  return 'Whether it is complete is not recorded.';
}
