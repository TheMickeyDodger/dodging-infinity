/**
 * feature13 — the OBSERVED STATUS of the office and of each Mission, derived purely from fields the
 * office state has ALREADY projected. Nothing here reads a source, guesses or merges identities.
 *
 * Every result carries:
 *  - a discrete status (running, waiting, blocked, failed, ended, disconnected, stale, unknown),
 *  - a short label and a SHAPE, so status is never carried by colour alone,
 *  - a plain-language sentence,
 *  - the EXACT projected fields that produced it (`sources`), for the inspector.
 *
 * Honesty rules this module keeps (they are the product's, not decoration):
 *  - a terminal's own `activity` (`done`, `busy`, ...) is never task, Mission, verification or
 *    delivery completion;
 *  - `unknown` whenever nothing supported reports it — never guessed, never shown as zero;
 *  - `stale` is not live, and `ended` is not verified success;
 *  - reported complete/success and verified success are different facts, never merged;
 *  - two records that look alike stay two records: nothing here keys on a name or an objective.
 */
import type { DiMember, DiMission, DiOfficeState } from '../../../core/src/messages.js';
import { diZoneLabel, isNormalWaiting, missionHeadline } from './diOfficeFormat.js';

export type DiOpStatus =
  'running' | 'waiting' | 'blocked' | 'failed' | 'ended' | 'disconnected' | 'stale' | 'unknown';

export interface DiOpStatusResult {
  status: DiOpStatus;
  /** Short words for the badge. */
  label: string;
  /** A shape for the badge: status is legible without colour. */
  glyph: string;
  /** One plain-language sentence. */
  sentence: string;
  /** The exact projected fields that produced this status, as `field = value`. */
  sources: string[];
}

/** One distinct shape per status (the badge always carries its words as well). */
export const DI_OP_GLYPH: Record<DiOpStatus, string> = {
  running: '●',
  waiting: '◐',
  blocked: '▲',
  failed: '✕',
  ended: '■',
  disconnected: '◌',
  stale: '◇',
  unknown: '○',
};

const LABEL: Record<DiOpStatus, string> = {
  running: 'Running',
  waiting: 'Waiting',
  blocked: 'Blocked',
  failed: 'Failed',
  ended: 'Ended',
  disconnected: 'Disconnected',
  stale: 'Stale',
  unknown: 'Unknown',
};

/**
 * `mission.lifecycleState` is a NATIVE literal (catalogProjection.ts: "verbatim and case-preserved.
 * NEVER switch on it"): it is only ever DISPLAYED here, beside a status derived from fields with
 * defined semantics. `mission.standing` is a classified value the projection passes on verbatim;
 * only its three declared values are recognised, anything else is said to be unrecognised.
 */
const STANDINGS = ['active', 'inactive', 'terminal'] as const;

/** The socket is NOT connected (`transport_<state>`, state verbatim: the three non-connected states). */
const NOT_CONNECTED_REASONS = [
  'transport_connecting',
  'transport_reconnecting',
  'transport_disconnected',
];
/** Connected again, but no NEW office message yet: a reconnected socket by itself restores nothing. */
const AWAITING_OFFICE_STATE_REASON = 'transport_reconnected_awaiting_office_state';
/** Connected but silent past the last state's valid_until (`client_expired:<why>`). */
const CLIENT_EXPIRED_PREFIX = 'client_expired:';

/** True for a reason the CLIENT itself raised (diOfficeFormat.clientInvalidationReason), known or not. */
function isClientReason(reason: string | null): boolean {
  return (
    reason !== null && (reason.startsWith('transport_') || reason.startsWith(CLIENT_EXPIRED_PREFIX))
  );
}

/**
 * The server-reported NORMAL wait (e.g. `catalog_refresh_throttled`), or null. It is a wait only while
 * the client still vouches for the state: the client's own invalidation reasons take PRECEDENCE, so an
 * older (cached) envelope problem never presents a lost connection as a calm backend wait. Used by
 * `officeStatus` and by every place the panel's face says "waiting".
 */
export function backendNormalWait(state: DiOfficeState): string | null {
  const problem = state.envelope.envelopeProblem;
  return !isClientReason(state.notLiveReason) && isNormalWaiting(problem) ? problem : null;
}

function result(status: DiOpStatus, sentence: string, sources: string[]): DiOpStatusResult {
  return { status, label: LABEL[status], glyph: DI_OP_GLYPH[status], sentence, sources };
}

const field = (name: string, value: string | number | boolean | null): string =>
  `${name} = ${value === null ? 'null' : typeof value === 'string' ? JSON.stringify(value) : String(value)}`;

/**
 * The office as a whole: is what the panel shows current? Live is `running`; a normal operating
 * wait of the backend (e.g. its refresh rate limit) is `waiting`; a socket that is NOT connected
 * is `disconnected`; a socket that is connected again but has not yet delivered a new office state
 * is `waiting` (it restores nothing by itself, and it is NOT disconnected); a connected but silent
 * one past `valid_until` is `stale`; anything else not live — including a `transport_*` reason this
 * module does not recognise — is `stale` (the last state is shown, not vouched for). PRECEDENCE: the
 * client's own reasons (diOfficeFormat.clientInvalidationReason) are evaluated BEFORE the envelope's
 * server-reported normal wait, so a cached throttle never masks a lost connection; only a client that
 * still vouches for the state reads a backend wait as `waiting`.
 */
export function officeStatus(state: DiOfficeState): DiOpStatusResult {
  const reason = state.notLiveReason;
  if (state.live)
    return result('running', 'Live: the office state is current.', [field('state.live', true)]);
  const base = [field('state.live', false), field('state.notLiveReason', reason)];
  // 1. The CLIENT's own invalidation reasons come FIRST. They are distinct from every server-reported
  //    one, and an older (cached) envelope problem can never mask them: server-side freshness cannot
  //    invalidate a disconnected browser, and a throttle from an answer the client can no longer vouch
  //    for must not read as a calm backend wait.
  if (reason !== null && NOT_CONNECTED_REASONS.includes(reason))
    return result(
      'disconnected',
      'Disconnected from the office server: the last state is shown and is not current.',
      base,
    );
  if (reason === AWAITING_OFFICE_STATE_REASON)
    return result(
      'waiting',
      'Connected again, waiting for a new office state: the last state is shown and is not current.',
      base,
    );
  if (reason !== null && reason.startsWith(CLIENT_EXPIRED_PREFIX))
    return result(
      'stale',
      'No new office state arrived before the last one expired (connected but silent): the last state is shown and is not current.',
      base,
    );
  if (reason !== null && reason.startsWith('transport_'))
    // A transport reason this module does not recognise: never "disconnected", never a backend wait.
    return result(
      'stale',
      `Not live (${reason}): the last state is shown and is not current.`,
      base,
    );
  // 2. Only when the client still vouches for the state does a server-reported NORMAL wait read as such.
  const problem = state.envelope.envelopeProblem;
  if (isNormalWaiting(problem))
    return result(
      'waiting',
      `Waiting on the backend (${problem}${
        state.envelope.retryAfterSeconds === null
          ? ''
          : `, retry after ${state.envelope.retryAfterSeconds} s`
      }); a normal state, not an error. Nothing shown is current.`,
      [
        ...base,
        field('envelope.envelopeProblem', problem),
        field('envelope.retryAfterSeconds', state.envelope.retryAfterSeconds),
      ],
    );
  if (reason === null) return result('unknown', 'Not live, and no reason was given.', base);
  return result('stale', `Not live (${reason}): the last state is shown and is not current.`, [
    ...base,
    field('envelope.fresh', state.envelope.fresh),
  ]);
}

/**
 * One Mission's observed status. An office that is not live decides first: nothing about a
 * Mission is current then. Then the record's standing (an unrecognised standing is `unknown`,
 * never a guess); a REPORTED completion without success; a member whose terminal reports
 * `blocked`; and finally whether its team is OBSERVED live. The native record state is displayed
 * verbatim in the sentence and never decides anything.
 */
export function missionStatus(mission: DiMission, state: DiOfficeState): DiOpStatusResult {
  const office = officeStatus(state);
  const shown = `record state ${mission.lifecycleState}`;
  const standing = [field('mission.standing', mission.standing)];
  if (office.status !== 'running') {
    const status: DiOpStatus = office.status === 'disconnected' ? 'disconnected' : 'stale';
    return result(
      status,
      `Last known: ${mission.standing} (${shown}); not current while the office is not live.`,
      [...office.sources, ...standing],
    );
  }
  if (!(STANDINGS as readonly string[]).includes(mission.standing))
    return result(
      'unknown',
      `Unrecognised standing “${mission.standing}” (${shown}): its status is not known.`,
      standing,
    );
  const completion = mission.v2.projected ? mission.v2.missionState.completion : null;
  if (
    completion !== null &&
    completion.state === 'observed' &&
    completion.reportedComplete === true &&
    completion.reportedSuccess === false
  )
    return result(
      'failed',
      'Its Mission State reports completion without success (reported by the source, not verified).',
      [
        ...standing,
        field('v2.missionState.completion.reportedComplete', true),
        field('v2.missionState.completion.reportedSuccess', false),
      ],
    );
  if (mission.standing === 'terminal') {
    const verified = completion?.state === 'observed' ? completion.verifiedSuccess : null;
    return result(
      'ended',
      `The record has ended (${shown}). ${
        verified === true
          ? 'Its Mission State records verified success.'
          : 'Ended is not verified success.'
      }`,
      verified === null
        ? standing
        : [...standing, field('v2.missionState.completion.verifiedSuccess', verified)],
    );
  }
  if (mission.standing === 'inactive') return result('waiting', `Not active (${shown}).`, standing);

  // standing === 'active': only an OBSERVED roster, an ESTABLISHED binding and members that are
  // live AND confirmed as themselves ('present') can make anything about the team current.
  const binding = [
    field('mission.bindingState', mission.bindingState),
    field('mission.bindingReason', mission.bindingReason),
  ];
  if (mission.members.state !== 'observed')
    return result(
      'unknown',
      `The record says ${mission.lifecycleState}; its team is not observed (${mission.members.state}).`,
      [...standing, ...binding, field('mission.members.state', mission.members.state)],
    );
  if (mission.bindingState !== 'bound')
    return result(
      'unknown',
      `The record says ${mission.lifecycleState}; its team is not established (binding ${mission.bindingState}).`,
      [...standing, ...binding],
    );
  const members = mission.members.members;
  const confirmed = members.filter(isConfirmedLive);
  const memberFields = members.map(
    (m) =>
      `${field(`members[${m.memberRef}].live`, m.live)}, ${field(`members[${m.memberRef}].classification`, m.classification)}`,
  );
  if (confirmed.length === 0)
    return result(
      'unknown',
      `The record says ${mission.lifecycleState}; no team member is confirmed live.`,
      [...standing, ...binding, ...memberFields],
    );
  // A terminal's own `blocked` report counts ONLY from a confirmed, live member. Any other member's
  // report is a last-known detail (memberTerminalNotes), never the Mission's current status.
  const blocked = confirmed.filter((m) => m.activity === 'blocked');
  if (blocked.length > 0)
    return result(
      'blocked',
      `${blocked.length} live team member terminal(s) report blocked (the terminal's own report).`,
      [
        ...standing,
        ...binding,
        ...blocked.map(
          (m) =>
            `${field(`members[${m.memberRef}].activity`, m.activity)}, ${field(`members[${m.memberRef}].live`, true)}, ${field(`members[${m.memberRef}].classification`, m.classification)}`,
        ),
      ],
    );
  return result(
    'running',
    `${confirmed.length} of ${members.length} team member(s) observed live.`,
    [...standing, ...binding, ...memberFields],
  );
}

/** Live AND confirmed as itself in the last observation (never replaced, ambiguous, gone). */
function isConfirmedLive(m: DiMember): boolean {
  return m.live && m.classification === 'present';
}

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
