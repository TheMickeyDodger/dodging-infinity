/**
 * THE HERDR LAUNCH ROUTE: open a REAL macOS Terminal window running plain `herdr` against one Mission's herd, target that herd's workspace, and SEND NO INPUT.
 *
 * The owner rule (owner-decisions.md, 2026-10-26): Herdr is a normal OS-terminal attach; the human types and drives it; the app sends no prompts, keystrokes or commands into herd agent panes or the
 * attached OS terminal. So the only things this route does to a terminal are OPEN one, on an executable launcher file whose attaching command is plain `herdr` (the launcher first checks its own window is
 * large enough: it is no longer ONE command, see herdrLauncherText), and (before that) ask Herdr's socket API to FOCUS the herd's workspace. `herdr workspace focus` is an API call, not terminal input: it is not send_input, send_keys, send_text, agent.prompt or send-keys (diNoTerminalInput.test.ts keeps scanning
 * this file). Terminal runs the launcher file; nothing is typed into the window. AppleScript's `do script` is NOT used: it writes the command into the new window's shell as input.
 *
 * THE SERVER IS NAMED BY ITS SOCKET, UNDER THE OWNER'S CONFINEMENT RULE (Q4, owner-decision-remaining-boundaries-ANSWERS-20261029.md): every Herdr command here, the listing, the focus, the read-back and the
 * launcher alike, runs with XDG_CONFIG_HOME, XDG_STATE_HOME, HERDR_SESSION and HERDR_CLIENT_SOCKET_PATH REMOVED and HOME and HERDR_SOCKET_PATH SET to the target's. Those inherited variables override HOME, and
 * every Herdr pane carries the live server's socket, so without this the route would reach whichever server the office happened to inherit. And it never uses `--session` or `herdr session attach <name>`.
 *
 * AT USE, NEVER CACHED (the Stage 4 design, Revision 5 R5.3): every `launch()` and `check()` reads the Mission's binding afresh and runs Herdr's read-only `agent list` afresh, requires the producer's identity
 * verdict to be `verified`, and requires the recorded session to appear EXACTLY ONCE in the live list. The pane and the WORKSPACE it reports and targets are the ones Herdr lists now; the catalog's recorded
 * pane id is a read handle and is never used to attach, and no workspace id is ever configured or hardcoded: it comes from the live row that matched the verified session (Amendment 1, A7).
 *
 * ORDER (the module's determinacy answer, Amendment 1, A8). ADMISSION first, and every admission step is free of shared-state effects: the target is validated; the binding is read; its identity, session and
 * live row are checked; the launcher name and the workspace id are checked; and the pane area of that workspace is read (Herdr's read-only `api snapshot`) so the window size to require can be derived
 * (Amendment 2, A12; `geometry_unknown` if it cannot). A refusal at any of those writes, focuses and opens NOTHING. Then, effects, in this order:
 *   1. write the launcher (a local file; it touches no shared state, so a failed write can never have moved anyone's view);
 *   2. FOCUS the herd's workspace through the socket API, then READ IT BACK (`workspace get`) and go on only when Herdr's own answer says that workspace is focused;
 *   3. OPEN the terminal on the launcher.
 * FOCUS IS A REQUIRED PRECONDITION OF OPENING (Amendment 1, A10), not a best-effort extra. The window must not appear while the server is still showing some other workspace, because a person who starts
 * typing into a window that shows an unrelated active agent's pane would be typing into the wrong agent. So if the workspace focus is not ESTABLISHED (the call failed, or Herdr's read-back does not say that
 * workspace is focused) the route REFUSES to open: `focus_not_established`, nothing opened, the launcher file named as written (it was written first, and is left in place). That is an OPERATIONAL failure,
 * not an admission refusal, and says so. The reverse case: focus established but the open then fails is `launch_failed`, and says the view HAS moved and nothing opened.
 *
 * THE WINDOW'S SIZE (Amendment 2, A12). Herdr sizes its one shared session to its SMALLEST attached client, which is not ours to change, and a fresh Terminal window is small: attaching one collapsed the
 * whole live herd (measured 162 x 46 down to 54 x ~10). So the launcher requires its real window to be at least the herd's pane area (plus an allowance) before it starts Herdr, asks the terminal to grow
 * that window if it is not, MEASURES the result, and otherwise does not attach. No size is ever set or reported by us: only read from the real window. The full argument, including why this stays
 * consistent with the owner rule that the app types nothing into the window, is on herdrLauncherText. Whether a real Terminal window honours the request is UNOBSERVED here (owed to a human).
 *
 * WHAT THE RESULT CLAIMS, AND WHAT IT NEVER CLAIMS (Amendment 1, A8 wording correction and A11). Separate facts, each only as strong as its receipt:
 *   - `terminal.state` 'opened': the opener (`/usr/bin/open`) exited with status 0, so the terminal application was ASKED to open the launcher. 'unconfirmed': the opener had not finished within its bound.
 *     Neither means the Herdr client attached, drew anything, or accepts keystrokes. Whether a launcher actually ran is a property of the terminal application: Ghostty 1.3.1 was observed to accept
 *     `open -a Ghostty <file>.command` (exit 0) and NOT run it, which is why it is not offered (HERDR_TERMINALS).
 *   - `focus.state` 'focused': `workspace focus` succeeded AND Herdr's own `workspace get` answer then said that workspace is focused. Nothing else is ever reported as focused.
 *   - `matchedPaneId` is the pane of the live row whose session matched the verified identity: IDENTITY EVIDENCE and a read handle. Matching it focuses nothing, and no pane is ever focused here
 *     (`herdr pane focus` moves to a NEIGHBOUR, so a determinate pane focus is not available): the result says `paneFocus: 'not_performed'` rather than leave it to be inferred.
 * The route produces no claim of "attached" and none of "keyboard works": that proof is a person's, made at a real terminal, and nothing in this code observes it.
 *
 * WHAT IT DOES NOT DO OR ESTABLISH:
 * - It imports no process module: the Node effects are built from APIs the composition passes in (`nodeHerdrLaunchEffects`; the composition is herdrProcess.ts), so no test here can start a process.
 * - Launcher files are not removed after use; they stay in the target's launcher directory.
 * - Focusing moves the operator's own view on a SHARED server. It does nothing else to the herd: no create, rename, close, split, move, resize or zoom of any pane, and the server is never stopped or
 *   reloaded. (The only resize anywhere is the launcher asking the terminal to grow ITS OWN window.)
 * - It cannot stop another client, attached later or smaller, from shrinking the shared session again.
 */
import type { Classified, RuntimeIdentityState } from './catalogContract.js';

/** The macOS opener. */
const OPEN_FILE = '/usr/bin/open';
/**
 * The terminals the route may open a launcher in: a CLOSED table, each entry an absolute application path the opener is given (`open -a <path>`), never a bare name another app could answer to. Only a terminal
 * OBSERVED to run an opened `.command` launcher is listed. Terminal.app was observed (2026-10-30, Terminal 2.15): the launcher ran with stdin a TTY and TERM_PROGRAM=Apple_Terminal. Ghostty 1.3.1 was
 * observed NOT to: `open -a Ghostty <file>.command` exited 0, Ghostty logged receiving the file, and the launcher never ran. A terminal that accepts a file without running it would make the result a
 * misreport, so it is absent from this table rather than present and trusted.
 */
export const HERDR_TERMINALS = {
  terminal: { app: '/System/Applications/Utilities/Terminal.app', label: 'Terminal' },
} as const;
export type HerdrTerminalId = keyof typeof HERDR_TERMINALS;
/** Herdr's read-only agent listing (the same read the catalog producer runs: target_runtime/runtime_snapshot.py LISTING_ARGV). */
const LISTING_ARGS = ['agent', 'list'] as const;
/** The listing's bounds. */
export const HERDR_LISTING_TIMEOUT_MS = 10_000;
export const HERDR_LISTING_MAX_BYTES = 4 * 1024 * 1024;
/** Herdr's read-only session snapshot, read ONLY for the pane area of the herd's workspace (A12). Its bounds: it lists every pane of the session, so it is larger than a listing but still bounded. */
const SNAPSHOT_ARGS = ['api', 'snapshot'] as const;
export const HERDR_SNAPSHOT_TIMEOUT_MS = 10_000;
export const HERDR_SNAPSHOT_MAX_BYTES = 8 * 1024 * 1024;
/**
 * An ALLOWANCE, not a measurement. Herdr reports the pane area and where it starts (`area.x`, `area.y`: the sidebar and the top bar sit before it), but nothing about what sits AFTER it (a border, a status
 * line) and nothing about any client's terminal size. A window needs at least the area's far edge, `x + width` by `y + height`; this many more columns and rows are asked for to cover chrome Herdr does not
 * report. Asking for a LARGER real window than strictly needed can never shrink the session (it is sized to its smallest client); asking for less could.
 */
export const WINDOW_MARGIN_COLS = 2;
export const WINDOW_MARGIN_ROWS = 2;
/** Sanity bounds on a window the launcher is told to need: a larger figure is a misread, not a screen. */
export const WINDOW_MAX_COLS = 1000;
export const WINDOW_MAX_ROWS = 500;
/** How long the launcher waits (tries x 0.1 s) for the terminal to carry out its resize request before it says the window is too small. */
export const LAUNCHER_RESIZE_TRIES = 40;
/** The bounds of the two workspace commands (focus and its read-back), which answer with one small record. */
export const HERDR_WORKSPACE_TIMEOUT_MS = 10_000;
export const HERDR_WORKSPACE_MAX_BYTES = 256 * 1024;
/** How long the route waits for the OPENER (never the window) to exit: it hands off and exits at once, so a longer wait means something is wrong. */
export const HERDR_OPEN_TIMEOUT_MS = 10_000;
/** The launcher file: created exclusively, readable and runnable by its owner only. */
const LAUNCHER_OPTIONS = { flag: 'wx', mode: 0o700 } as const;
/** The inherited variables Q4's confinement rule REMOVES from every Herdr command here (each overrides HOME). */
export const HERDR_UNSET_ENV = [
  'XDG_CONFIG_HOME',
  'XDG_STATE_HOME',
  'HERDR_SESSION',
  'HERDR_CLIENT_SOCKET_PATH',
] as const;

/** Where to attach. Every field is validated before any effect. */
export interface HerdrAttachTarget {
  /** The absolute path of the herdr executable. */
  herdrBin: string;
  /** HOME for every Herdr command here. Required: an inherited environment would reach whichever server it names. */
  home: string;
  /** The server's API socket, set as HERDR_SOCKET_PATH for every Herdr command here. It must lie inside `home`. */
  socketPath: string;
  /** The absolute directory the launcher file is written in. */
  launcherDir: string;
  /** Which terminal opens the launcher: a key of HERDR_TERMINALS. */
  terminal: HerdrTerminalId;
}

/** The producer's RUNTIME-IDENTITY verdict for a role (`runtime_agents[].identity`): decided over two source passes, with the role's native working directory corroborated. */
export interface HerdrRuntimeIdentity {
  state: Classified<RuntimeIdentityState>;
  reason: string;
}

/**
 * What the catalog's TEAM ROSTER says about a role (`missions[].members[]`). This is a DIFFERENT verdict vocabulary from the runtime identity above, with a different and weaker assurance: it is the
 * producer's comparison of the session RECORDED for a role with the session LIVE in one observation. It is never called "verified" here, and it never stands in for the runtime verdict silently: the route
 * accepts it only under `teamMemberProblem`'s rule, and says in its result that this is the basis (`identityBasis`).
 *
 * Why a roster is a binding source at all: a bound Herdr task's roles are MOVED OUT of `runtime_agents` into the Mission's members (herdr_task_source.py skips a role already in `members` and counts it as
 * `represented_by_teams`), so for the normal, healthy, bound task `runtime_agents.agents` is EMPTY and the roster is the only place its roles are listed.
 */
export interface HerdrTeamMemberEvidence {
  source: 'team_member';
  /** `members[].classification`, verbatim. */
  classification: string;
  /** `members[].reason`, verbatim. */
  reason: string;
  /** The office's own per-member liveness verdict: a live catalog answer, the Mission active and bound, THIS member's chain bound, and a live classification. */
  presenceLive: boolean;
  /** The roster chain the member is on (`direct`: this task's own roster; `child`: another herd's). */
  chain: string;
  sessionRecorded: string | null;
  sessionLive: string | null;
}

/** What vouched for the role's identity in a result: never "attached", never "keyboard". */
export type HerdrIdentityBasis = 'runtime_identity_verified' | 'team_roster_session_confirmed';

/** What the Mission's catalog says about its role, read AT USE (the producer's own fields: target_runtime/herdr_task_source.py). */
export interface HerdrRoleBinding {
  missionRef: string;
  /** The role's Herdr agent name. */
  agent: string;
  /** The session the producer recorded for the role. */
  session: string | null;
  /** The pane the producer recorded: a READ handle, never used to attach. */
  paneId: string | null;
  /** What vouches for this role: the producer's runtime-identity verdict for this pass, or the team roster's entry for it (`source: 'team_member'`). */
  identity: HerdrRuntimeIdentity | HerdrTeamMemberEvidence;
}

/** A binding that cannot be offered, with the plain reason: refused as `no_binding`, naming that reason instead of a generic sentence. */
export interface HerdrBindingUnavailable {
  unavailable: string;
}

/** How the opener ended: it exited with status 0, or it had not finished within its bound. A failure to start or a non-zero exit rejects instead. */
export type HerdrOpenOutcome = 'exited_ok' | 'unconfirmed';

export interface HerdrLaunchEffects {
  /** A FRESH read of the Mission's binding. */
  readBinding(): Promise<HerdrRoleBinding | HerdrBindingUnavailable | null>;
  /** Run the listing with the inherited environment less `unset`, plus `set`; resolve with its stdout, reject when it did not finish with status 0. */
  runListing(
    file: string,
    args: readonly string[],
    env: { set: Readonly<Record<string, string>>; unset: readonly string[] },
  ): Promise<string>;
  /** Run Herdr's read-only `api snapshot` the same way, for the pane area only; resolve with its stdout, reject when it did not finish with status 0. */
  runSnapshot(
    file: string,
    args: readonly string[],
    env: { set: Readonly<Record<string, string>>; unset: readonly string[] },
  ): Promise<string>;
  /** Run one Herdr workspace command (`workspace focus <id>`, `workspace get <id>`) the same way; resolve with its stdout, reject when it did not finish with status 0. */
  runWorkspaceCommand(
    file: string,
    args: readonly string[],
    env: { set: Readonly<Record<string, string>>; unset: readonly string[] },
  ): Promise<string>;
  /** Create the launcher file with these options; reject on any failure (an existing file included). */
  writeLauncher(filePath: string, text: string, options: { flag: 'wx'; mode: number }): Promise<void>;
  /** Start the opener and wait for ITS exit, never for the window: resolve `exited_ok` on status 0, `unconfirmed` when it has not exited within its bound, reject on any other ending. */
  openInTerminal(file: string, args: readonly string[]): Promise<HerdrOpenOutcome>;
  /** A fresh random name for the launcher file. */
  launcherName(): string;
  /**
   * Whether the terminal application exists at this path. Asked BEFORE any effect: the open comes after the focus, so a terminal that is not installed must be refused while nothing has moved. Absent:
   * not checked here (the opener's own exit status then reports it).
   */
  terminalInstalled?(appPath: string): Promise<boolean>;
}

export type HerdrLaunchRefusal =
  | 'target_invalid'
  | 'terminal_unavailable'
  | 'no_binding'
  | 'identity_not_verified'
  | 'session_unknown'
  | 'listing_unreadable'
  | 'not_live'
  | 'live_more_than_once'
  | 'workspace_unknown'
  | 'geometry_unknown'
  | 'launcher_not_written'
  | 'focus_not_established'
  | 'launch_failed';

/**
 * Whether the herd's workspace was focused. ONLY 'focused' states a focus, and only because Herdr's own read-back said so; the other two say it was NOT established, and a route that cannot establish it
 * opens nothing (A10). Never "attached".
 */
export type HerdrFocusReport =
  | { state: 'focused'; workspaceId: string }
  | { state: 'requested_unconfirmed'; workspaceId: string; detail: string }
  | { state: 'failed'; workspaceId: string; detail: string };

/**
 * The size, in character cells, of the REAL window the launcher will insist on before it starts Herdr in it: the far edge of the herd's pane area plus an allowance. It is what the launcher is told to
 * require, derived from Herdr's own read-only snapshot at the moment of the click. It is a requirement, not an achievement: whether the window reached it is for the launcher to measure, in the window.
 */
export interface HerdrWindowRequirement {
  cols: number;
  rows: number;
}

/** Whether the terminal was asked to open the launcher. Never "attached" and never "typing works". */
export interface HerdrTerminalReport {
  state: 'opened' | 'unconfirmed';
  terminal: HerdrTerminalId;
  label: string;
}

export type HerdrLaunchResult =
  | {
      launched: true;
      missionRef: string;
      agent: string;
      session: string;
      /** What vouched for the role (the runtime verdict, or the team roster's session confirmation). Neither is an attach and neither is a keyboard. */
      identityBasis: HerdrIdentityBasis;
      /** IDENTITY EVIDENCE and a read handle: the pane of the live row whose session matched. Matching it focuses nothing (A11). */
      matchedPaneId: string | null;
      /** No pane focus is ever performed (`herdr pane focus` moves to a neighbour); stated so it is never inferred from `matchedPaneId`. */
      paneFocus: 'not_performed';
      /** The workspace that was targeted AND is reported focused. */
      workspaceId: string;
      /** The window size the launcher was written to REQUIRE before it starts Herdr (a requirement, not an achievement). */
      window: HerdrWindowRequirement;
      launcher: string;
      terminal: HerdrTerminalReport;
      focus: Extract<HerdrFocusReport, { state: 'focused' }>;
    }
  | { launched: false; refusal: HerdrLaunchRefusal; detail: string; focus?: HerdrFocusReport };

/** What `check()` reports: the SAME admission the launch passes, with no effect at all. */
export type HerdrCheckResult =
  | {
      available: true;
      missionRef: string;
      agent: string;
      session: string;
      /** What vouched for the role, as in HerdrLaunchResult. */
      identityBasis: HerdrIdentityBasis;
      /** IDENTITY EVIDENCE and a read handle, as in HerdrLaunchResult. */
      matchedPaneId: string | null;
      /** The workspace a launch WOULD target (the live row's); nothing has been focused. */
      workspaceId: string;
      /** The window size a launch's launcher would require before starting Herdr. */
      window: HerdrWindowRequirement;
      terminal: HerdrTerminalId;
      label: string;
    }
  | { available: false; refusal: HerdrLaunchRefusal; detail: string };

/** An absolute, normalised path of plain characters: no quote, space or shell character can reach the launcher. */
const PLAIN_PATH = /^(\/[A-Za-z0-9._-]+)+$/;
const PLAIN_LAUNCHER_NAME = /^[0-9a-f]{8,64}$/;
/** A Herdr workspace id as it is placed in an argument list: plain characters, never leading with a dash (so it cannot read as an option). */
const PLAIN_WORKSPACE_ID = /^[A-Za-z0-9][A-Za-z0-9._:-]{0,63}$/;

/** The ONE plain-path guard: the route's own targets and the office's flags are both checked with it, so there is no second, looser copy. */
export function plainPath(value: unknown): value is string {
  return (
    typeof value === 'string' &&
    PLAIN_PATH.test(value) &&
    value.split('/').every((segment) => segment !== '.' && segment !== '..')
  );
}

/** Why the target is invalid, or null when it is valid. */
function targetProblem(target: HerdrAttachTarget): string | null {
  if (!plainPath(target.herdrBin)) return 'the herdr path is not a plain absolute path';
  if (!plainPath(target.home)) return 'the HOME for herdr is not a plain absolute path';
  if (!plainPath(target.socketPath))
    return "the Herdr server's socket path is not a plain absolute path";
  if (!target.socketPath.startsWith(`${target.home}/`))
    return "the Herdr server's socket path is not inside the HOME for herdr";
  if (!plainPath(target.launcherDir)) return 'the launcher directory is not a plain absolute path';
  if (typeof target.terminal !== 'string' || !Object.hasOwn(HERDR_TERMINALS, target.terminal))
    return `the terminal is not one this route can use (${Object.keys(HERDR_TERMINALS).join(', ')})`;
  if (!plainPath(HERDR_TERMINALS[target.terminal].app)) return 'the terminal application path is not plain';
  return null;
}

/**
 * WHAT THE ROUTE ACCEPTS FROM A TEAM ROSTER, AND WHY. The route needs a binding to know WHICH session to look for; it then proves for itself, in Herdr's own live list, that exactly one live row carries that
 * session, and it takes the workspace from that row. So the binding's verdict does not have to be the whole proof, but it must be a real claim that this is the Mission's own role, and it must be one the
 * catalog itself backs with fields that AGREE. A roster entry is accepted only if ALL of these hold; anything else fails closed (`identity_not_verified`, naming the real classification and reason):
 *   1. `presenceLive`: the office's own per-member verdict is live. That verdict already requires a live catalog answer, the Mission active and bound, this member's chain bound, and a live classification.
 *   2. `chain` is exactly `direct`: this task's own roster. A `child` member belongs to another herd, whose workspace is not this task's.
 *   3. `classification` is exactly `present`. `replaced` also counts as live elsewhere in the office (a session changed under a role); it means the recorded session is no longer the live one, so it is NOT enough here.
 *   4. `reason` is exactly `session_confirmed`: the producer's own statement of WHY it classified the role present.
 *   5. the recorded session and the live session are both present, non-empty and EQUAL, and they are the session the binding names. The producer's classification is not taken on its word alone: the two
 *      fields it was derived from must still agree in what the office holds now.
 * What this is not: a runtime-identity verification. That verdict (HerdrRuntimeIdentity) is decided over two source passes with the role's working directory corroborated; a roster entry is one comparison
 * of two session ids. The two are different vocabularies with different assurances, so a roster entry is never mapped onto `verified`, and the result says which one vouched (`identityBasis`).
 */
function teamMemberProblem(evidence: HerdrTeamMemberEvidence, session: string | null): string | null {
  const who = `this Mission's agent is ${evidence.classification} in the team roster (${evidence.reason}), not confirmed present`;
  if (!evidence.presenceLive) return `${who}: the office does not count it live`;
  if (evidence.chain !== 'direct') return `this Mission's agent is on the ${evidence.chain} roster chain, not this task's own, so it is not this task's herd`;
  if (evidence.classification !== 'present' || evidence.reason !== 'session_confirmed') return who;
  const { sessionRecorded, sessionLive } = evidence;
  if (sessionRecorded === null || sessionRecorded === '' || sessionLive === null || sessionLive === '') {
    return 'this Mission\'s agent has no recorded and live session in the team roster to compare, so it is not confirmed present';
  }
  if (sessionRecorded !== sessionLive) {
    return "this Mission's agent has a recorded session and a different live session in the team roster, so it is not confirmed present";
  }
  if (session !== sessionRecorded) return "this Mission's agent binding names a session other than the roster's, so it is not confirmed present";
  return null;
}

/**
 * Whether the role's identity is established, and on what basis; or the reason it is not. EXPORTED so the office's choice among a Mission's roles (herdrTarget.ts) uses this same rule and not a copy of it.
 */
export function establishIdentity(binding: HerdrRoleBinding): { basis: HerdrIdentityBasis } | { problem: string } {
  const identity = binding.identity;
  if ('source' in identity) {
    const problem = teamMemberProblem(identity, binding.session);
    return problem === null ? { basis: 'team_roster_session_confirmed' } : { problem };
  }
  const state = identity.state;
  if (!(state.known && state.value === 'verified')) {
    const shown = state.known ? state.value : 'not known to this office';
    return { problem: `this Mission's agent identity is ${shown} (${identity.reason}), not verified` };
  }
  return { basis: 'runtime_identity_verified' };
}

/** Q4's environment for every Herdr command here: the inherited one less HERDR_UNSET_ENV, with HOME and HERDR_SOCKET_PATH set. */
const herdrEnvironment = (target: HerdrAttachTarget) => ({
  set: { HOME: target.home, HERDR_SOCKET_PATH: target.socketPath },
  unset: HERDR_UNSET_ENV,
});

/** The system programs the launcher calls, by absolute path. The defaults are the real ones; only a test that runs the launcher against stand-ins passes others (each is checked as a plain path). */
export interface HerdrLauncherTools {
  stty: string;
  sleep: string;
  env: string;
}
export const SYSTEM_LAUNCHER_TOOLS: HerdrLauncherTools = { stty: '/bin/stty', sleep: '/bin/sleep', env: '/usr/bin/env' };

/**
 * The size the launcher must see in its own window before it starts Herdr, from Herdr's read-only snapshot: the layouts of THIS workspace, the far edge of the widest/tallest pane area (`x + width`,
 * `y + height`) plus the allowance. Anything else is a reason, not a size: a missing workspace, a missing or non-integer area, a figure past the sanity bounds. All layouts of the workspace are
 * considered and the largest extent wins, so a tab with a larger area is never under-asked.
 */
function windowFromSnapshot(stdout: string, workspaceId: string): HerdrWindowRequirement | string {
  let document: unknown;
  try {
    document = JSON.parse(stdout);
  } catch {
    return 'the snapshot is not one JSON document';
  }
  const result = typeof document === 'object' && document !== null ? (document as { result?: unknown }).result : undefined;
  const snapshot = typeof result === 'object' && result !== null ? (result as { snapshot?: unknown }).snapshot : undefined;
  const layouts = typeof snapshot === 'object' && snapshot !== null ? (snapshot as { layouts?: unknown }).layouts : undefined;
  if (!Array.isArray(layouts)) return 'the snapshot carries no layouts list';
  let cols = 0;
  let rows = 0;
  let found = 0;
  const whole = (value: unknown): value is number => typeof value === 'number' && Number.isInteger(value) && value >= 0 && value <= WINDOW_MAX_COLS;
  for (const [index, layout] of layouts.entries()) {
    if (typeof layout !== 'object' || layout === null || Array.isArray(layout)) return `layout ${index} is not an object`;
    const entry = layout as { workspace_id?: unknown; area?: unknown };
    if (entry.workspace_id !== workspaceId) continue;
    const area = entry.area as { x?: unknown; y?: unknown; width?: unknown; height?: unknown } | null | undefined;
    if (typeof area !== 'object' || area === null) return `the layout of workspace ${workspaceId} has no pane area`;
    if (!whole(area.x) || !whole(area.y) || !whole(area.width) || !whole(area.height)) return `the layout of workspace ${workspaceId} has a pane area that is not whole numbers within bounds`;
    if (area.width === 0 || area.height === 0) return `the layout of workspace ${workspaceId} has an empty pane area`;
    found += 1;
    cols = Math.max(cols, area.x + area.width + WINDOW_MARGIN_COLS);
    rows = Math.max(rows, area.y + area.height + WINDOW_MARGIN_ROWS);
  }
  if (found === 0) return `the snapshot has no layout for workspace ${workspaceId}`;
  if (cols > WINDOW_MAX_COLS || rows > WINDOW_MAX_ROWS) return `the pane area asks for a window of ${cols} x ${rows}, past the ${WINDOW_MAX_COLS} x ${WINDOW_MAX_ROWS} this office will ask for`;
  return { cols, rows };
}

/**
 * THE LAUNCHER FILE'S TEXT (Amendment 2, A12). It is NO LONGER "a shebang and one command". Read this before changing it.
 *
 * WHY IT CHANGED. Herdr sizes its ONE shared session to the SMALLEST client attached to it, and that is not ours to change. A freshly opened Terminal window is small, so a plain `exec herdr` in it shrank
 * every pane of the user's live herd, for every role, until the window was closed (measured: 54 x ~10 from 162 x 46). That is the opposite of a usable terminal. So before it starts Herdr the launcher
 * checks that THIS window is at least as large as the herd's pane area needs (`need_cols` x `need_rows`, from Herdr's own snapshot), and starts Herdr only if it is.
 *
 * WHAT IT DOES, in order:
 *   1. MEASURES the window: `stty size`, which READS the size the terminal reports for this window. It never sets one.
 *   2. If the window is already big enough, it goes straight to the last step, with nothing printed.
 *   3. Otherwise it asks the terminal to GROW THIS WINDOW with the standard resize control sequence, `ESC [ 8 ; rows ; cols t`, written to its own standard output. It asks for at least the needed size in
 *      each dimension and never less than the current size in either (it only grows). The terminal either resizes the real window or it does not; a display too small simply cannot hold it.
 *   4. MEASURES AGAIN, up to LAUNCHER_RESIZE_TRIES times a tenth of a second apart. Whatever the window really is now is what counts.
 *   5. If it is big enough: the last step. If it is not (the terminal ignored the request, or the screen cannot hold the window): Herdr is NOT started. The launcher says, in the window, the real size it
 *      measured, the size it needs and why, waits for the person to press Return, measures again, and starts Herdr only when the window really has become big enough. It never attaches a small window.
 *   6. THE ONE COMMAND THAT STAYS: `exec` of PLAIN `herdr` under Q4's environment (no `--session`, no `session attach`), exactly as before.
 *
 * WHAT IT NEVER DOES (and the tests pin each). It never SETS a size: no `stty rows/cols/size N`, no `COLUMNS`/`LINES`, no `TIOCSWINSZ`. A size set that way would be a size the window does not have, told
 * to Herdr as if it did: the session would be laid out for a screen nobody has, and the human would see a misdrawn herd. That would be worse than the defect, and is exactly the false claim this work
 * exists to remove. Every size it acts on is READ from the real window, after the real window was asked to change. It sends nothing to any pane, to Herdr, or to anything that reads input.
 *
 * WHY THIS STAYS CONSISTENT WITH THE OWNER RULE ("the app types nothing into the window"). The rule forbids INPUT: keystrokes, prompts or text delivered as if typed, to a pane's or a terminal's input, where an
 * agent or a shell would read it as the human's. The launcher is a program the human's terminal runs, and everything it writes goes OUT, to its own standard output, which is the terminal's display and
 * control channel, the direction every program (Herdr included) draws in. A resize request is a request to the terminal emulator about its own window, not data any process reads as a command; no agent pane
 * can receive it; the human still types every character that is ever typed. The one thing it can print besides the request is its own refusal message. It is the same class of act as any program
 * printing to its terminal, and it changes no profile, no setting and no other window. The no-input scan (diNoTerminalInput.test.ts) still reads this whole file, and the launcher's text contains none of
 * the verbs it forbids. What it DOES change is the old assumption that the file holds exactly one command; that is said here, in the docs, and in the tests, not left as a detail.
 *
 * WHAT IT DOES NOT GUARANTEE. That the terminal honours the resize request (unobserved in a real window: native sizing is owed to a human). That the size Herdr reports (the pane AREA) plus the allowance
 * is the whole of what Herdr needs: the chrome after the area is not reported. That another client, attached later or smaller, will not shrink the session again: no program of ours can stop that.
 *
 * Every value is an integer within bounds or a validated plain path, and single-quoted as well.
 */
export function herdrLauncherText(
  target: HerdrAttachTarget,
  window: HerdrWindowRequirement,
  tools: HerdrLauncherTools = SYSTEM_LAUNCHER_TOOLS,
): string {
  const unset = HERDR_UNSET_ENV.map((name) => `-u ${name}`).join(' ');
  const attach = `exec ${tools.env} ${unset} HOME='${target.home}' HERDR_SOCKET_PATH='${target.socketPath}' '${target.herdrBin}'`;
  return [
    '#!/bin/sh',
    `need_cols=${window.cols}`,
    `need_rows=${window.rows}`,
    'measure() {',
    `  set -- $(${tools.stty} size 2>/dev/null)`,
    '  known=0; rows=0; cols=0',
    // a size is known only if the terminal said EXACTLY two whole numbers: one token, three tokens, or anything else is "could not be read", never a size
    '  if [ "$#" = 2 ]; then',
    '    case "$1$2" in',
    '      *[!0-9]*) ;;',
    '      *) known=1; rows=$1; cols=$2 ;;',
    '    esac',
    '  fi',
    '}',
    'enough() {',
    '  measure',
    '  [ "$known" = 1 ] && [ "$rows" -ge "$need_rows" ] && [ "$cols" -ge "$need_cols" ]',
    '}',
    'attach() {',
    `  ${attach}`,
    '}',
    'enough && attach',
    'want_cols=$need_cols',
    'want_rows=$need_rows',
    '[ "$cols" -gt "$want_cols" ] && want_cols=$cols',
    '[ "$rows" -gt "$want_rows" ] && want_rows=$rows',
    "printf '\\033[8;%d;%dt' \"$want_rows\" \"$want_cols\"",
    'n=0',
    `while [ "$n" -lt ${LAUNCHER_RESIZE_TRIES} ]; do`,
    `  ${tools.sleep} 0.1`,
    '  enough && attach',
    '  n=$((n + 1))',
    'done',
    'while :; do',
    '  printf \'\\nHerdr was NOT started in this window.\\n\'',
    '  if [ "$known" = 1 ]; then',
    '    printf \'This window is %s columns by %s rows; Herdr needs at least %s by %s here.\\n\' "$cols" "$rows" "$need_cols" "$need_rows"',
    '  else',
    '    printf \'This window\\047s size could not be read; Herdr needs at least %s columns by %s rows here.\\n\' "$need_cols" "$need_rows"',
    '  fi',
    "  printf 'Herdr sizes its shared session to the smallest window attached to it, so attaching a smaller\\n'",
    "  printf 'window would shrink every pane of the herd you are using.\\n'",
    "  printf 'Make this window larger (drag its corner or zoom it), then press Return to try again.\\n'",
    "  printf 'Or close this window.\\n'",
    '  read -r _ || exit 1',
    '  enough && attach',
    'done',
    '',
  ].join('\n');
}

interface LiveRow {
  session: string | null;
  paneId: string | null;
  workspaceId: string | null;
}

const optionalText = (value: unknown): string | null | undefined => (value === undefined || value === null ? null : typeof value === 'string' ? value : undefined);

/** The live rows of ONE listing, or a reason it is unreadable. A row this cannot read makes the WHOLE listing unreadable: a skipped row would make a partial list read as complete. */
function liveRows(stdout: string): LiveRow[] | string {
  let document: unknown;
  try {
    document = JSON.parse(stdout);
  } catch {
    return 'it is not one JSON document';
  }
  const result = typeof document === 'object' && document !== null ? (document as { result?: unknown }).result : undefined;
  const agents = typeof result === 'object' && result !== null ? (result as { agents?: unknown }).agents : undefined;
  if (!Array.isArray(agents)) return 'it carries no agents list';
  const rows: LiveRow[] = [];
  for (const [index, raw] of agents.entries()) {
    if (typeof raw !== 'object' || raw === null || Array.isArray(raw)) return `agent ${index} is not an object`;
    const entry = raw as Record<string, unknown>;
    const sessionObject = entry.agent_session;
    if (sessionObject !== undefined && sessionObject !== null && (typeof sessionObject !== 'object' || Array.isArray(sessionObject)))
      return `agent ${index} has an agent session that is not an object`;
    const session = optionalText(sessionObject === undefined || sessionObject === null ? null : (sessionObject as { value?: unknown }).value);
    const paneId = optionalText(entry.pane_id);
    const workspaceId = optionalText(entry.workspace_id);
    if (session === undefined || paneId === undefined || workspaceId === undefined) return `agent ${index} has a field that is not text`;
    rows.push({ session, paneId, workspaceId });
  }
  return rows;
}

/**
 * What Herdr's own `workspace get <id>` answer says about focus: 'focused' or 'not_focused' for exactly the workspace asked about, or a reason it cannot be read. The record read here is
 * `{"result":{"workspace":{"workspace_id":"w6V","focused":true,...}}}` (observed on Herdr's real output).
 */
function readFocus(stdout: string, workspaceId: string): 'focused' | 'not_focused' | string {
  let document: unknown;
  try {
    document = JSON.parse(stdout);
  } catch {
    return 'the read-back is not one JSON document';
  }
  const result = typeof document === 'object' && document !== null ? (document as { result?: unknown }).result : undefined;
  const workspace = typeof result === 'object' && result !== null ? (result as { workspace?: unknown }).workspace : undefined;
  if (typeof workspace !== 'object' || workspace === null || Array.isArray(workspace)) return 'the read-back carries no workspace record';
  const record = workspace as { workspace_id?: unknown; focused?: unknown };
  if (record.workspace_id !== workspaceId) return 'the read-back names a different workspace';
  if (record.focused === true) return 'focused';
  if (record.focused === false) return 'not_focused';
  return 'the read-back does not say whether the workspace is focused';
}

const refuse = (refusal: HerdrLaunchRefusal, detail: string): { launched: false; refusal: HerdrLaunchRefusal; detail: string } => ({ launched: false, refusal, detail });

type Admitted =
  | { ok: true; binding: HerdrRoleBinding & { session: string }; live: LiveRow; workspaceId: string; basis: HerdrIdentityBasis; window: HerdrWindowRequirement }
  | { ok: false; refusal: HerdrLaunchRefusal; detail: string };

/**
 * The route for one target. `launch()` and `check()` read everything they use at the moment they are called. `check()` runs EXACTLY the admission `launch()` runs and then stops: it writes, focuses and
 * opens nothing, so the office's Herdr control can say whether a launch is available NOW without doing it.
 */
export function createHerdrLauncher(
  target: HerdrAttachTarget,
  effects: HerdrLaunchEffects,
): { launch(): Promise<HerdrLaunchResult>; check(): Promise<HerdrCheckResult> } {
  /** Every admission step. None of them has a shared-state effect: the only processes started are Herdr's read-only agent list. */
  async function admit(): Promise<Admitted> {
    const problem = targetProblem(target);
    if (problem !== null) return { ok: false, refusal: 'target_invalid', detail: `Nothing was opened: ${problem}.` };

    const terminalApp = HERDR_TERMINALS[target.terminal];
    if (effects.terminalInstalled !== undefined) {
      let installed: boolean;
      try {
        installed = await effects.terminalInstalled(terminalApp.app);
      } catch {
        installed = false;
      }
      if (!installed)
        return { ok: false, refusal: 'terminal_unavailable', detail: `Nothing was opened: ${terminalApp.label} is not installed at ${terminalApp.app}.` };
    }

    const read = await effects.readBinding();
    if (read === null) return { ok: false, refusal: 'no_binding', detail: 'Nothing was opened: this Mission has no recorded role to attach to.' };
    if ('unavailable' in read) return { ok: false, refusal: 'no_binding', detail: `Nothing was opened: ${read.unavailable}.` };
    const binding = read;
    const established = establishIdentity(binding);
    if ('problem' in established) return { ok: false, refusal: 'identity_not_verified', detail: `Nothing was opened: ${established.problem}.` };
    if (binding.session === null) return { ok: false, refusal: 'session_unknown', detail: "Nothing was opened: no session is recorded for this Mission's agent." };

    let stdout: string;
    try {
      stdout = await effects.runListing(target.herdrBin, LISTING_ARGS, herdrEnvironment(target));
    } catch (err) {
      return { ok: false, refusal: 'listing_unreadable', detail: `Nothing was opened: Herdr's live agent list could not be read (${(err as Error).message}).` };
    }
    const rows = liveRows(stdout);
    if (typeof rows === 'string') return { ok: false, refusal: 'listing_unreadable', detail: `Nothing was opened: Herdr's live agent list is unreadable (${rows}).` };
    const matches = rows.filter((row) => row.session === binding.session);
    if (matches.length === 0) return { ok: false, refusal: 'not_live', detail: "Nothing was opened: the recorded session is not in Herdr's live agent list now." };
    if (matches.length > 1)
      return {
        ok: false,
        refusal: 'live_more_than_once',
        detail: `Nothing was opened: the recorded session is in Herdr's live agent list ${matches.length} times, so which one to attach cannot be told.`,
      };
    const live = matches[0];
    // A7: the workspace to target is the one Herdr lists for the session that was just verified. A row that names none cannot be targeted, and a terminal that might land on some other workspace is not opened.
    if (live.workspaceId === null || !PLAIN_WORKSPACE_ID.test(live.workspaceId))
      return {
        ok: false,
        refusal: 'workspace_unknown',
        detail: "Nothing was opened: Herdr's live row for this Mission's agent names no usable workspace, so the herd's workspace cannot be targeted.",
      };
    // A12: how large a window Herdr needs so that attaching it does not shrink the herd, from Herdr's own read-only snapshot of THIS workspace's pane area. If it cannot be established the route does
    // not open a window it cannot size: a window of unknown adequacy is how the live herd was collapsed. This is a read, so it is still admission: nothing is written, focused or opened before it.
    let snapshot: string;
    try {
      snapshot = await effects.runSnapshot(target.herdrBin, SNAPSHOT_ARGS, herdrEnvironment(target));
    } catch (err) {
      return {
        ok: false,
        refusal: 'geometry_unknown',
        detail: `Nothing was opened: Herdr's session snapshot could not be read (${(err as Error).message}), so the window size that avoids shrinking the herd cannot be determined.`,
      };
    }
    const window = windowFromSnapshot(snapshot, live.workspaceId);
    if (typeof window === 'string') {
      return {
        ok: false,
        refusal: 'geometry_unknown',
        detail: `Nothing was opened: the window size that avoids shrinking the herd cannot be determined (${window}).`,
      };
    }
    return { ok: true, binding: { ...binding, session: binding.session }, live, workspaceId: live.workspaceId, basis: established.basis, window };
  }

  return {
    async check(): Promise<HerdrCheckResult> {
      const admitted = await admit();
      if (!admitted.ok) return { available: false, refusal: admitted.refusal, detail: admitted.detail };
      const { binding, live, workspaceId, basis, window } = admitted;
      return {
        available: true,
        missionRef: binding.missionRef,
        agent: binding.agent,
        session: binding.session,
        identityBasis: basis,
        matchedPaneId: live.paneId,
        workspaceId,
        window,
        terminal: target.terminal,
        label: HERDR_TERMINALS[target.terminal].label,
      };
    },

    async launch(): Promise<HerdrLaunchResult> {
      const admitted = await admit();
      if (!admitted.ok) return refuse(admitted.refusal, admitted.detail);
      const { binding, live, workspaceId, basis, window } = admitted;
      const terminal = HERDR_TERMINALS[target.terminal];

      const name = effects.launcherName();
      if (!PLAIN_LAUNCHER_NAME.test(name)) return refuse('launcher_not_written', 'Nothing was opened: the launcher name is not plain lowercase hex.');
      const launcher = `${target.launcherDir}/herdr-attach-${name}.command`;

      // EFFECT 1: the launcher file. Local only: a failure here cannot have moved any view.
      try {
        await effects.writeLauncher(launcher, herdrLauncherText(target, window), { ...LAUNCHER_OPTIONS });
      } catch (err) {
        return refuse('launcher_not_written', `Nothing was opened: the launcher could not be written (${(err as Error).message}).`);
      }

      // EFFECT 2: focus the herd's workspace, then read Herdr's own answer back. Only that read-back may say 'focused', and anything less than 'focused' STOPS the route here (A10): a terminal opened onto
      // an unrelated active workspace could take a person's keystrokes into an unrelated agent's pane. This is an operational failure that follows a write, so it names the launcher it left.
      const focus = await focusWorkspace(workspaceId);
      if (focus.state !== 'focused') {
        return {
          launched: false,
          refusal: 'focus_not_established',
          detail: `Nothing was opened: ${focusNotEstablished(focus)} The launcher was written at ${launcher} and was not used.`,
          focus,
        };
      }

      // EFFECT 3: the terminal. The opener's own exit is the only thing waited for.
      let opened: HerdrOpenOutcome;
      try {
        opened = await effects.openInTerminal(OPEN_FILE, ['-a', terminal.app, launcher]);
      } catch (err) {
        return {
          launched: false,
          refusal: 'launch_failed',
          detail: `${terminal.label} was not opened (${(err as Error).message}). Before that, Herdr reported workspace ${workspaceId} focused, so the view HAS moved and nothing opened. The launcher was written at ${launcher}.`,
          focus,
        };
      }
      return {
        launched: true,
        missionRef: binding.missionRef,
        agent: binding.agent,
        session: binding.session,
        identityBasis: basis,
        matchedPaneId: live.paneId,
        paneFocus: 'not_performed',
        workspaceId,
        window,
        launcher,
        terminal: { state: opened === 'exited_ok' ? 'opened' : 'unconfirmed', terminal: target.terminal, label: terminal.label },
        focus,
      };
    },
  };

  /** `workspace focus <id>` through the socket API, then `workspace get <id>`; the report states only what the read-back supports. */
  async function focusWorkspace(workspaceId: string): Promise<HerdrFocusReport> {
    const env = herdrEnvironment(target);
    try {
      await effects.runWorkspaceCommand(target.herdrBin, ['workspace', 'focus', workspaceId], env);
    } catch (err) {
      return { state: 'failed', workspaceId, detail: `Herdr's workspace focus did not succeed (${(err as Error).message}).` };
    }
    let stdout: string;
    try {
      stdout = await effects.runWorkspaceCommand(target.herdrBin, ['workspace', 'get', workspaceId], env);
    } catch (err) {
      return { state: 'requested_unconfirmed', workspaceId, detail: `Herdr accepted the focus request, but the read-back could not be read (${(err as Error).message}).` };
    }
    const seen = readFocus(stdout, workspaceId);
    if (seen === 'focused') return { state: 'focused', workspaceId };
    if (seen === 'not_focused') return { state: 'requested_unconfirmed', workspaceId, detail: 'Herdr accepted the focus request, but its read-back says the workspace is not focused.' };
    return { state: 'requested_unconfirmed', workspaceId, detail: `Herdr accepted the focus request, but ${seen}.` };
  }
}

/** Why the workspace focus was not established, and what is and is not known about the operator's view. */
function focusNotEstablished(focus: Exclude<HerdrFocusReport, { state: 'focused' }>): string {
  const view =
    focus.state === 'failed'
      ? "Workspace focus was not established, and Herdr's call failed, so nothing is known to have moved."
      : 'Workspace focus was not confirmed, and the view may have moved.';
  return `${view} ${focus.detail}`;
}

/** The Node APIs the effects are built from, passed in by the composition (this module imports none of them). */
export interface NodeLaunchApis {
  execFile(
    file: string,
    args: readonly string[],
    options: { env: Record<string, string | undefined>; shell: false; timeout: number; maxBuffer: number; encoding: 'utf8' },
    callback: (error: Error | null, stdout: string, stderr: string) => void,
  ): unknown;
  spawn(
    file: string,
    args: readonly string[],
    options: { shell: false; detached: true; stdio: 'ignore' },
  ): { once(event: 'spawn' | 'error' | 'exit', listener: (...args: unknown[]) => void): unknown; unref(): void };
  writeFile(filePath: string, text: string, options: { flag: 'wx'; mode: number; encoding: 'utf8' }): Promise<void>;
  randomBytes(size: number): { toString(encoding: 'hex'): string };
  env: Readonly<Record<string, string | undefined>>;
  /** Optional: whether a path exists. Present, the effects answer `terminalInstalled` from it; absent, the terminal is not pre-checked. */
  pathExists?(path: string): Promise<boolean>;
}

/**
 * The effects over real Node APIs: no shell anywhere; the Herdr commands bounded in time and size; the opener detached with its output ignored. The office waits for the OPENER'S exit and never for the
 * window, within HERDR_OPEN_TIMEOUT_MS: an opener that cannot start or exits with an error is a failed open, and one that has not finished by then is 'unconfirmed' (neither success nor failure). The wait is
 * a promise that settles at the latest at the timer, and the timer is unref'd, so it cannot hang the office or hold the process open.
 */
export function nodeHerdrLaunchEffects(
  node: NodeLaunchApis,
  readBinding: () => Promise<HerdrRoleBinding | HerdrBindingUnavailable | null>,
): HerdrLaunchEffects {
  const run =
    (timeout: number, maxBuffer: number) =>
    (file: string, args: readonly string[], { set, unset }: { set: Readonly<Record<string, string>>; unset: readonly string[] }): Promise<string> =>
      new Promise((resolve, reject) => {
        const inherited = Object.fromEntries(Object.entries(node.env).filter(([name]) => !unset.includes(name)));
        node.execFile(
          file,
          args,
          { env: { ...inherited, ...set }, shell: false, timeout, maxBuffer, encoding: 'utf8' },
          (error, stdout) => (error === null ? resolve(stdout) : reject(error)),
        );
      });
  return {
    readBinding,
    runListing: run(HERDR_LISTING_TIMEOUT_MS, HERDR_LISTING_MAX_BYTES),
    runSnapshot: run(HERDR_SNAPSHOT_TIMEOUT_MS, HERDR_SNAPSHOT_MAX_BYTES),
    runWorkspaceCommand: run(HERDR_WORKSPACE_TIMEOUT_MS, HERDR_WORKSPACE_MAX_BYTES),
    writeLauncher: (filePath, text, options) => node.writeFile(filePath, text, { ...options, encoding: 'utf8' }),
    openInTerminal: (file, args) =>
      new Promise((resolve, reject) => {
        let settled = false;
        const settle = (finish: () => void): void => {
          if (settled) return;
          settled = true;
          clearTimeout(timer);
          finish();
        };
        const timer = setTimeout(() => settle(() => resolve('unconfirmed')), HERDR_OPEN_TIMEOUT_MS);
        timer.unref();
        let child: ReturnType<NodeLaunchApis['spawn']>;
        try {
          child = node.spawn(file, args, { shell: false, detached: true, stdio: 'ignore' });
        } catch (err) {
          settle(() => reject(err as Error));
          return;
        }
        child.once('error', (err) => settle(() => reject(err as Error)));
        child.once('exit', (code, signal) =>
          settle(() => {
            if (code === 0) resolve('exited_ok');
            else reject(new Error(code === null ? `the opener ended on signal ${String(signal)}` : `the opener exited with status ${String(code)}`));
          }),
        );
        child.unref();
      }),
    launcherName: () => node.randomBytes(12).toString('hex'),
    ...(node.pathExists === undefined ? {} : { terminalInstalled: (appPath: string) => node.pathExists!(appPath) }),
  };
}
