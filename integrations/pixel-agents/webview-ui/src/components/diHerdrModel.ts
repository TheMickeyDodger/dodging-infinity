/**
 * The Herdr control's MODEL: what the office's Herdr buttons are allowed to do and to say, decided here and rendered elsewhere (DiScanPanel.tsx), sent by useDiHerdr.ts. Pure: no transport, no DOM, no clock.
 *
 * WHAT THE CONTROL IS. A click on Herdr asks the office SERVER (diHerdrRequest) to open a macOS Terminal window running Herdr on this Mission's herd. The server first asks Herdr to focus the herd's
 * workspace and opens the window only if Herdr's own read-back says it is focused. This model never builds any other request: two operations, `status` (is a launch available NOW; the server runs the
 * route's admission and does nothing) and `launch`. It builds `launch` only for a Mission whose last `status` said available, and only when nothing else is waiting. It sends no key, prompt or text.
 *
 * WHAT THE CONTROL SAYS. Every note is derived from what the server last answered for THIS Mission, never from a constant standing in for an outcome: the button is enabled exactly when the server said a
 * launch is available, and a refusal is shown with the server's own words. It says only what a receipt supports:
 *   - 'opened': the macOS opener exited 0, so the terminal application was ASKED to open the launcher; 'unconfirmed': it had not finished within its limit.
 *   - the workspace is 'focused' only because Herdr's own read-back said so.
 *   - the pane is "matched as identity evidence": matching it focuses nothing, and no pane is ever focused.
 * It NEVER says the terminal is attached, that Herdr is showing the herd, or that the keyboard works: that is a person's observation at the window (and the Supervisor's native gate), and nothing here
 * observes it. The tests pin that the words never appear.
 *
 * It holds nothing durable and polls nothing: a status is read when the Mission is opened or when the person asks to check again.
 */
import type { DiHerdrIdentityBasis, DiHerdrOp, DiHerdrReply, DiHerdrRequest } from '../../../core/src/messages.js';

export type HerdrCheck =
  | { kind: 'unknown' }
  | { kind: 'checking' }
  | {
      kind: 'available';
      agent: string;
      identityBasis: DiHerdrIdentityBasis;
      workspaceId: string;
      /** The size the launcher will REQUIRE of its real window before starting Herdr (a requirement, not an achievement). */
      windowMinCols: number;
      windowMinRows: number;
      matchedPaneId: string | null;
      terminalLabel: string;
    }
  | { kind: 'unavailable'; problem: string | null; reason: string };

export type HerdrLaunch =
  | { kind: 'idle' }
  | { kind: 'launching' }
  | {
      kind: 'opened';
      workspaceId: string;
      windowMinCols: number;
      windowMinRows: number;
      matchedPaneId: string | null;
      terminalLabel: string;
      terminalState: 'opened' | 'unconfirmed';
    }
  | { kind: 'refused'; problem: string | null; reason: string };

export interface HerdrMissionState {
  check: HerdrCheck;
  launch: HerdrLaunch;
}

export interface HerdrState {
  /** The one request in flight (the server answers one at a time, and so does this model). */
  pending: { seq: number; op: DiHerdrOp; missionRef: string } | null;
  /** By Mission reference; a Map, so no reference can name a prototype member. */
  missions: ReadonlyMap<string, HerdrMissionState>;
  /** Why the client cannot vouch for the office (disconnected, expired); null when it can. */
  unvouched: string | null;
}

export const INITIAL_HERDR_STATE: HerdrState = { pending: null, missions: new Map(), unvouched: null };

/**
 * The Herdr control the panels read (useDiHerdr.ts makes one over the transport; tests make one over a state they set). Defined here, in the pure model, so nothing that only needs its SHAPE has to import the
 * hook and, through it, the transport.
 */
export interface DiHerdrControl {
  state: HerdrState;
  /** Ask the office whether a launch is available for this Mission (a read: nothing is opened or moved). */
  requestStatus: (missionRef: string) => void;
  /** Ask the office to open the terminal on this Mission's herd. Sent only after a status said it is available. */
  launch: (missionRef: string) => void;
}

const UNKNOWN: HerdrMissionState = { check: { kind: 'unknown' }, launch: { kind: 'idle' } };

export function missionState(state: HerdrState, missionRef: string): HerdrMissionState {
  return state.missions.get(missionRef) ?? UNKNOWN;
}

/** A sequence number no two requests of this window share. */
let nextHerdrSeq = 1;
export function takeHerdrSeq(): number {
  const seq = nextHerdrSeq;
  nextHerdrSeq += 1;
  return seq;
}

export type HerdrEvent =
  | { type: 'sent'; seq: number; op: DiHerdrOp; missionRef: string }
  | { type: 'reply'; reply: DiHerdrReply }
  | { type: 'unvouched'; reason: string | null };

/**
 * The refusals that mean "a launch is not available for this Mission" (the route's admission, the office's configuration). After one of these on a LAUNCH the control must stop offering the click, so the
 * check is set to unavailable with the same words. The others are operational outcomes of one attempt (a focus that could not be established, an opener that failed, a click too soon) and leave a launch
 * on offer.
 */
const ADMISSION_PROBLEMS: ReadonlySet<string> = new Set([
  'herdr_not_configured',
  'launcher_directory_unusable',
  'target_invalid',
  'terminal_unavailable',
  'no_binding',
  'identity_not_verified',
  'session_unknown',
  'listing_unreadable',
  'not_live',
  'live_more_than_once',
  'workspace_unknown',
  'geometry_unknown',
]);

const MISSING_REASON = 'The office refused without giving a reason.';
const NO_ANSWER_REASON = 'The office answered without saying what it established, so nothing is known.';

function withMission(state: HerdrState, missionRef: string, change: (m: HerdrMissionState) => HerdrMissionState): ReadonlyMap<string, HerdrMissionState> {
  const next = new Map(state.missions);
  next.set(missionRef, change(missionState(state, missionRef)));
  return next;
}

export function reduce(state: HerdrState, event: HerdrEvent): HerdrState {
  switch (event.type) {
    case 'sent':
      return {
        ...state,
        pending: { seq: event.seq, op: event.op, missionRef: event.missionRef },
        missions: withMission(state, event.missionRef, (m) =>
          event.op === 'status' ? { ...m, check: { kind: 'checking' } } : { ...m, launch: { kind: 'launching' } },
        ),
      };
    case 'unvouched':
      // The client stops vouching: nothing stays on screen as current. Coming back leaves every Mission to be read again.
      return event.reason === null ? { ...state, unvouched: null } : { pending: null, missions: new Map(), unvouched: event.reason };
    case 'reply': {
      const { reply } = event;
      const pending = state.pending;
      // A reply is matched by its seq and op only. One that matches nothing in flight is not ours (stale, or another session's) and changes nothing.
      if (pending === null || pending.seq !== reply.seq || pending.op !== reply.op) return state;
      const next = (change: (m: HerdrMissionState) => HerdrMissionState): HerdrState => ({
        ...state,
        pending: null,
        missions: withMission(state, pending.missionRef, change),
      });
      if (reply.op === 'status') {
        if (reply.state === 'ok') {
          const a = reply.answer;
          return next((m) => ({
            ...m,
            check:
              a === null
                ? { kind: 'unavailable', problem: null, reason: NO_ANSWER_REASON }
                : {
                    kind: 'available',
                    agent: a.agent,
                    identityBasis: a.identityBasis,
                    workspaceId: a.workspaceId,
                    windowMinCols: a.windowMinCols,
                    windowMinRows: a.windowMinRows,
                    matchedPaneId: a.matchedPaneId,
                    terminalLabel: a.terminalLabel,
                  },
          }));
        }
        return next((m) => ({ ...m, check: { kind: 'unavailable', problem: reply.problem, reason: reply.reason ?? MISSING_REASON } }));
      }
      if (reply.state === 'ok') {
        const a = reply.answer;
        if (a === null || a.workspaceFocus !== 'focused' || a.terminalState === null)
          return next((m) => ({ ...m, launch: { kind: 'refused', problem: null, reason: NO_ANSWER_REASON } }));
        return next((m) => ({
          ...m,
          launch: {
            kind: 'opened',
            workspaceId: a.workspaceId,
            windowMinCols: a.windowMinCols,
            windowMinRows: a.windowMinRows,
            matchedPaneId: a.matchedPaneId,
            terminalLabel: a.terminalLabel,
            terminalState: a.terminalState as 'opened' | 'unconfirmed',
          },
        }));
      }
      const reason = reply.reason ?? MISSING_REASON;
      return next((m) => ({
        launch: { kind: 'refused', problem: reply.problem, reason },
        check: reply.problem !== null && ADMISSION_PROBLEMS.has(reply.problem) ? { kind: 'unavailable', problem: reply.problem, reason } : m.check,
      }));
    }
  }
}

/**
 * Whether a status for THIS Mission is already in flight. Asking again would only repeat it, and worse: when the first answers, the repeat would be sent as well and would turn the answer just received back into
 * "checking". A screen that asks twice in a row (React StrictMode runs every mount effect twice in development) must therefore be answered once, not held and replayed.
 */
export function statusInFlight(state: HerdrState, missionRef: string): boolean {
  return state.pending !== null && state.pending.op === 'status' && state.pending.missionRef === missionRef;
}

/** A status request for this Mission, or null when none may be sent now (one is in flight, or the office is not vouched for). */
export function statusMessage(state: HerdrState, missionRef: string, seq: number): DiHerdrRequest | null {
  if (state.unvouched !== null || state.pending !== null) return null;
  return { type: 'diHerdrRequest', seq, op: 'status', missionRef };
}

/** A launch request, only for a Mission whose last status said a launch is available, with nothing in flight. */
export function launchMessage(state: HerdrState, missionRef: string, seq: number): DiHerdrRequest | null {
  if (state.unvouched !== null || state.pending !== null) return null;
  const m = missionState(state, missionRef);
  if (m.check.kind !== 'available' || m.launch.kind === 'launching') return null;
  return { type: 'diHerdrRequest', seq, op: 'launch', missionRef };
}

/** Said where no Herdr control is wired to the view at all (a layout preview, a harness): the honest state of a button with nothing behind it. */
export const HERDR_NOT_CONNECTED = 'The Herdr control is not connected to this view, so nothing can be opened from here.';
/** Said by the rail's Herdr when no Mission is open: the control acts on one Mission's herd. */
export const HERDR_NO_MISSION = "Open a Mission to use Herdr: it opens a terminal on that Mission's herd.";

export interface HerdrControlView {
  /** The button is clickable exactly when this is true. */
  enabled: boolean;
  /** A request is in flight for this Mission. */
  busy: boolean;
  /** The words under the button: derived from the server's last answer for this Mission. */
  note: string;
  tone: 'idle' | 'working' | 'ok' | 'refused';
  /** Whether "Check again" is offered (the control is unavailable, so the person can ask the server again). */
  canRecheck: boolean;
  /** A launch is in flight or has an outcome (opened or refused) to show: the places too narrow to show a note all the time show it then. */
  outcome: boolean;
}

const paneClause = (matched: string | null): string =>
  matched === null ? '' : ` Pane ${matched} is matched as identity evidence only; no pane is focused.`;

/**
 * Why the window is asked to grow, and what happens if it does not. Herdr sizes its ONE shared session to its smallest client (not ours to change), so a small new window would shrink every pane of the
 * herd. The launcher therefore starts Herdr only in a window it has MEASURED to be at least this large; if the window cannot be made that large, Herdr is not started and the window says so. Said as a
 * requirement, never as an achievement: nothing here has seen the window.
 */
const windowClause = (cols: number, rows: number): string =>
  ` Herdr sizes its shared session to its smallest client, so the window is asked to grow to at least ${cols} x ${rows} cells, and Herdr is started in it only if the window really is that large; if not, the window says so and Herdr is not started.`;

/**
 * What vouched for the role the office anchored on, said as what it is. The two bases are different claims of different strength: a runtime identity the catalog VERIFIED, or the catalog's team roster
 * listing the role as present with its recorded and live sessions equal (one comparison, not a verification). Both are cross-checked against Herdr's own live list, which names the session once.
 */
const BASIS_WORDS: Readonly<Record<DiHerdrIdentityBasis, string>> = {
  runtime_identity_verified: " The catalog's runtime identity for the role is verified, and Herdr's live list names its session once.",
  team_roster_session_confirmed:
    " The role was found in the catalog's team roster with its recorded and live sessions equal (a roster confirmation, not a runtime-identity verification), and Herdr's live list names its session once.",
};

/** What the Herdr button for one Mission (or none: `missionRef` null) is, and says, in this state. `state` null: no control is wired. */
export function herdrControlView(state: HerdrState | null, missionRef: string | null): HerdrControlView {
  if (state === null) return { enabled: false, busy: false, note: HERDR_NOT_CONNECTED, tone: 'refused', canRecheck: false, outcome: false };
  if (missionRef === null) return { enabled: false, busy: false, note: HERDR_NO_MISSION, tone: 'idle', canRecheck: false, outcome: false };
  if (state.unvouched !== null) {
    return { enabled: false, busy: false, note: `Herdr is unavailable: the office is not being heard from (${state.unvouched}).`, tone: 'refused', canRecheck: false, outcome: false };
  }
  const { check, launch } = missionState(state, missionRef);
  const outcome = launch.kind !== 'idle';
  if (launch.kind === 'launching') {
    return { enabled: false, busy: true, note: 'Asking Herdr to focus this Mission\'s workspace, then asking the terminal to open…', tone: 'working', canRecheck: false, outcome };
  }
  if (check.kind === 'unknown') {
    return { enabled: false, busy: false, note: 'Whether Herdr can open for this Mission has not been checked yet.', tone: 'idle', canRecheck: true, outcome };
  }
  if (check.kind === 'checking') {
    return { enabled: false, busy: true, note: 'Checking whether Herdr can open for this Mission…', tone: 'working', canRecheck: false, outcome };
  }
  if (check.kind === 'unavailable') {
    return { enabled: false, busy: false, note: check.reason, tone: 'refused', canRecheck: true, outcome };
  }
  // available
  if (launch.kind === 'opened') {
    const note =
      launch.terminalState === 'opened'
        ? `Herdr reported workspace ${launch.workspaceId} focused, then ${launch.terminalLabel} was asked to open the Herdr launcher. Look at the ${launch.terminalLabel} window to see what it shows; nothing was typed into any pane or terminal.${paneClause(launch.matchedPaneId)}${windowClause(launch.windowMinCols, launch.windowMinRows)}`
        : `Herdr reported workspace ${launch.workspaceId} focused. ${launch.terminalLabel} was asked to open the Herdr launcher, but the opener had not finished within its time limit, so the window may not have opened; nothing was typed into any pane or terminal.${paneClause(launch.matchedPaneId)}${windowClause(launch.windowMinCols, launch.windowMinRows)}`;
    return { enabled: true, busy: false, note, tone: 'ok', canRecheck: false, outcome };
  }
  if (launch.kind === 'refused') {
    return { enabled: true, busy: false, note: launch.reason, tone: 'refused', canRecheck: false, outcome };
  }
  return {
    enabled: true,
    busy: false,
    note: `Opens ${check.terminalLabel} on this Mission's herd. It first asks Herdr to focus workspace ${check.workspaceId}, and types nothing into any pane or terminal.${paneClause(check.matchedPaneId)}${BASIS_WORDS[check.identityBasis]}${windowClause(check.windowMinCols, check.windowMinRows)}`,
    tone: 'idle',
    canRecheck: false,
    outcome,
  };
}
