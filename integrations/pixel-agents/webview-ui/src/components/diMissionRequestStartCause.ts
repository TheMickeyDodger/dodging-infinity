/**
 * Item 4, startup diagnostic delta — why an in-doubt Start is in doubt, as the window shows it.
 *
 * When a child's startup fails after its roles were contracted, Herdr retains a bounded, private diagnostic in the
 * child workspace. The panel's bridge projects it, read-only, onto the exact attempt it belongs to (by the run
 * intent's own workspace and a runtime that began no earlier than the intent; never "the newest file"), and the
 * panel puts the bounded result on the request view as `start_cause`. This window never reads a file: it parses what
 * the panel sent under a CLOSED shape and says it in sentences, or shows nothing it cannot account for.
 *
 * Three states, said honestly:
 *  - BOUND: this attempt left a diagnostic. Its stage, the failure's type and each affected role's gap are shown;
 *  - NONE: no retained diagnostic binds to this attempt. The cause is NOT known, and the window says so;
 *  - AMBIGUOUS: more than one claims the attempt (or there are too many to examine). None is used.
 *
 * Nothing here implies a retry is safe. The Start stays what it was: durably recorded, in doubt, never respawned.
 */

type Json = Record<string, unknown>;

const isObject = (v: unknown): v is Json =>
  typeof v === 'object' && v !== null && !Array.isArray(v);
const str = (v: unknown): string | null => (typeof v === 'string' ? v : null);
const count = (v: unknown): number | null =>
  typeof v === 'number' && Number.isInteger(v) && v >= 0 ? v : null;

/** What this window will draw, however much the panel sent. */
export const MR_START_CAUSE_MAX_ROLES = 16;
export const MR_START_CAUSE_MAX_CHARS = 300;

export type MrSessionState = 'ABSENT' | 'PRESENT' | 'UNKNOWN';
const SESSION_STATES: readonly MrSessionState[] = ['ABSENT', 'PRESENT', 'UNKNOWN'];

export interface MrStartCauseRole {
  logical: string;
  reason: string;
  session: MrSessionState;
}

export interface MrStartCause {
  state: 'BOUND' | 'NONE' | 'AMBIGUOUS';
  kind: 'BINDINGS_NOT_ESTABLISHED' | 'UNKNOWN' | null;
  stage: string | null;
  failureClass: string | null;
  roles: MrStartCauseRole[];
  rolesTotal: number;
  rolesTruncated: boolean;
  reference: string | null;
  /** NONE: how many retained files were examined and did not match this attempt. */
  rejected: number;
  /** AMBIGUOUS: how many claimed it. */
  candidates: number;
}

const cap = (s: string): string => s.slice(0, MR_START_CAUSE_MAX_CHARS);

/**
 * Read the panel's `start_cause` under the closed shape. Anything that is not exactly one of the three states, or
 * a BOUND cause with any field of the wrong kind, is `null`: not shown, rather than shown in part.
 */
export function parseStartCause(v: unknown): MrStartCause | null {
  if (!isObject(v)) return null;
  const empty: Omit<MrStartCause, 'state'> = {
    kind: null,
    stage: null,
    failureClass: null,
    roles: [],
    rolesTotal: 0,
    rolesTruncated: false,
    reference: null,
    rejected: 0,
    candidates: 0,
  };
  if (v.state === 'NONE') {
    const rejected = count(v.rejected);
    return rejected === null ? null : { ...empty, roles: [], state: 'NONE', rejected };
  }
  if (v.state === 'AMBIGUOUS') {
    const candidates = count(v.candidates);
    return candidates === null ? null : { ...empty, roles: [], state: 'AMBIGUOUS', candidates };
  }
  if (v.state !== 'BOUND') return null;
  const kind = v.kind === 'BINDINGS_NOT_ESTABLISHED' || v.kind === 'UNKNOWN' ? v.kind : null;
  const stage = str(v.stage);
  const reference = str(v.reference);
  const total = count(v.roles_total);
  if (kind === null || stage === null || reference === null || total === null) return null;
  if (!Array.isArray(v.roles) || typeof v.roles_truncated !== 'boolean') return null;
  const roles: MrStartCauseRole[] = [];
  for (const item of v.roles.slice(0, MR_START_CAUSE_MAX_ROLES)) {
    if (!isObject(item)) return null;
    const logical = str(item.logical);
    const reason = str(item.reason);
    const session = SESSION_STATES.find((s) => s === item.session);
    if (logical === null || reason === null || session === undefined) return null;
    roles.push({ logical: cap(logical), reason: cap(reason), session });
  }
  return {
    ...empty,
    state: 'BOUND',
    kind,
    stage: cap(stage),
    failureClass: str(v.failure_class) === null ? null : cap(str(v.failure_class) as string),
    roles,
    rolesTotal: total,
    rolesTruncated: v.roles_truncated || v.roles.length > roles.length || total > roles.length,
    reference: cap(reference),
  };
}

const plural = (n: number, one: string, many: string): string => (n === 1 ? one : many);

export function stageWords(stage: string): string {
  switch (stage) {
    case 'establish_role_bindings':
      return "binding the team's roles to their live sessions";
    case 'heartbeat_controller':
      return 'starting the heartbeat controller, after the roles were bound';
    default:
      return `the stage “${stage}”`;
  }
}

export function sessionWords(session: MrSessionState): string {
  switch (session) {
    case 'ABSENT':
      return 'Herdr reported no session id for it';
    case 'PRESENT':
      return 'a session id was reported, but the binding was incomplete otherwise';
    default:
      return 'no readable record was returned for it';
  }
}

/** While the Start is still in doubt: reconcile is the only thing offered, and it only looks for evidence. */
export const START_CAUSE_NOTE =
  'Nothing was respawned, and a retry is not implied to be safe. Reconcile only looks for evidence.';
/**
 * Once the run has stopped durably (it is closed), the Start is no longer “in doubt” and reconcile is no longer
 * offered, so neither word is borrowed from the stage the run has left.
 */
export const START_CAUSE_CLOSED_NOTE =
  'Nothing was respawned. This run is closed and cannot be started again.';

export interface MrStartCauseWords {
  heading: string;
  lines: string[];
  note: string;
}

/**
 * The cause in sentences. Every number and name in it came from the panel's bounded answer. `closed` is whether the
 * run has stopped durably: a closed run is described as a Start that never confirmed, not as one still in doubt.
 */
export function startCauseWords(cause: MrStartCause, closed = false): MrStartCauseWords {
  const subject = closed ? 'Why this Start never confirmed' : 'Why this Start is in doubt';
  const note = closed ? START_CAUSE_CLOSED_NOTE : START_CAUSE_NOTE;
  if (cause.state === 'NONE') {
    const examined =
      cause.rejected > 0
        ? ` (${cause.rejected} retained ${plural(cause.rejected, 'file', 'files')} examined did not match this attempt)`
        : '';
    return {
      heading: `${subject}: not known`,
      lines: [`No retained startup diagnostic is bound to this attempt${examined}.`],
      note,
    };
  }
  if (cause.state === 'AMBIGUOUS') {
    return {
      heading: `${subject}: not known`,
      lines: [
        `${cause.candidates} retained startup ${plural(cause.candidates, 'diagnostic claims', 'diagnostics claim')} this attempt (or there are too many to examine), so none of them is used.`,
      ],
      note,
    };
  }
  const lines: string[] = [`Startup stopped while ${stageWords(cause.stage ?? '')}.`];
  if (cause.kind === 'BINDINGS_NOT_ESTABLISHED') {
    lines.push(
      `The team's roles were started, but ${cause.rolesTotal} ${plural(cause.rolesTotal, 'role', 'roles')} could not be bound to a live session within the settle window.`,
    );
    for (const role of cause.roles) {
      lines.push(`${role.logical}: ${role.reason} (${sessionWords(role.session)}).`);
    }
    if (cause.rolesTruncated) {
      lines.push(`Showing ${cause.roles.length} of ${cause.rolesTotal} roles.`);
    }
  } else {
    lines.push(
      `The failure was of a kind this diagnostic cannot describe (${cause.failureClass ?? 'unnamed'}); its message is deliberately not recorded.`,
    );
  }
  lines.push(
    `Diagnostic file: ${cause.reference ?? 'none'}. It is kept privately in the child workspace (.herd/state/diagnostics); this window cannot open it.`,
  );
  return { heading: subject, lines, note };
}
