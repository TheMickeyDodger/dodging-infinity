/**
 * feature13 — presentation helpers for the office panel: a member's terminal notes, a Mission's objective and label, a member's identity in a few words. Derived purely from
 * fields the office state has ALREADY projected; nothing here reads a source, guesses or merges identities.
 *
 * A Mission's STATUS is not decided here: it is the one contract in diMissionStatus.ts (its vocabulary, its declared mappings and its source of truth), which every surface
 * reads. The honesty rules this module keeps: a terminal's own `activity` is never task, Mission, verification or delivery completion; a header is never a goal; two records
 * that look alike stay two records.
 */
import type { DiMission } from '../../../core/src/messages.js';
import { isConfirmedLive } from './diMissionStatus.js';
import { diZoneLabel, missionHeadline } from './diOfficeFormat.js';

/**
 * A `blocked` terminal report from a member that does NOT drive the status (not live, or not
 * confirmed as itself): kept as a qualified detail — the terminal's own report, last-known.
 */
export function memberTerminalNotes(mission: DiMission): string[] {
  return mission.members.members
    .filter((m) => m.activity === 'blocked' && !isConfirmedLive(m))
    .map(
      (m) =>
        `${m.logical}: its terminal last reported blocked (the terminal's own report; last known, not current; ${
          m.live ? `identity ${m.classification}` : 'not live'
        }).`,
    );
}

/**
 * The objective as the overview may present it. A source can export a SECTION HEADER where the
 * goal belongs (a bare "OBJECTIVE", seen from a native task export): that carries no goal, so it
 * is said plainly and source-qualified. Nothing is ever substituted for a goal — no description,
 * title, ref or role. A pure check over the already-projected field; no source is read.
 */
export type DiOpObjective =
  { kind: 'goal'; text: string } | { kind: 'no-goal'; text: string; raw: string | null };

/** A header word alone ("OBJECTIVE", "## Objective:", ...) is a header, not a goal. */
const HEADER_ONLY = /^#*\s*objective\s*:?\s*$/i;

export function missionObjective(mission: DiMission): DiOpObjective {
  const o = mission.objective;
  if (o.state === 'observed' && o.value !== null) {
    const trimmed = o.value.trim();
    if (trimmed === '')
      return {
        kind: 'no-goal',
        text: `No goal reported: ${mission.sourceId} exported an empty objective.`,
        raw: o.value,
      };
    if (HEADER_ONLY.test(trimmed))
      return {
        kind: 'no-goal',
        text: `No goal reported: ${mission.sourceId} exported only the section header “${trimmed}”, not a goal.`,
        raw: o.value,
      };
    return { kind: 'goal', text: o.value };
  }
  return {
    kind: 'no-goal',
    text: `No goal reported: ${mission.sourceId} gives no objective (${o.state}${
      o.reason ? `: ${o.reason}` : ''
    }).`,
    raw: null,
  };
}

/**
 * A member's identity in a few words for a compact row (as of the LAST observation; the full
 * sentence is `plainMemberIdentity`, the raw classification and reason sit behind a disclosure).
 */
export function shortMemberIdentity(classification: string): string {
  switch (classification) {
    case 'present':
      return 'identity confirmed';
    case 'replaced':
      return 'replaced by another session';
    case 'disappeared':
      return 'not in the live listing';
    case 'ambiguous':
      return 'identity ambiguous';
    default:
      return 'identity unrecognised';
  }
}

/** The team in a few words: the counts (as formatMemberCount) and the distinct roles, in order. */
export function teamRoles(mission: DiMission): string[] {
  const roles: string[] = [];
  for (const m of mission.members.members) if (!roles.includes(m.role)) roles.push(m.role);
  return roles;
}

/**
 * A Mission's one-line VERBATIM label: missionHeadline() — the existing bounded rule (objective cut at
 * its declared bound, then the short id) — except where the source gave no goal, which is said as
 * exactly that. A header is never presented as a goal. No primary name uses it: headings and the
 * Mission picker are named by missionTitle (diPlainSummary.ts), their ids kept secondary.
 */
export function missionLabel(mission: DiMission): string {
  return missionObjective(mission).kind === 'goal'
    ? missionHeadline(mission)
    : `No goal reported: ${diZoneLabel(mission)}`;
}
