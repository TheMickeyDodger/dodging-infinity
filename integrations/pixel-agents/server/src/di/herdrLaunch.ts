/**
 * THE HERDR LAUNCH ROUTE: open a REAL macOS Terminal window attached to one Mission's herd, and SEND NO INPUT.
 *
 * The owner rule (owner-decisions.md, 2026-10-26): Herdr is a normal OS-terminal attach; the human types and drives it; the app sends no prompts, keystrokes or commands into herd agent panes or the
 * attached OS terminal. So the only thing this route does to a terminal is OPEN one, on an executable launcher file whose one command is plain `herdr`, attaching the server the target names.
 * Terminal runs that file; nothing is typed into the window. AppleScript's `do script` is NOT used: it writes the command into the new window's shell as input, which is exactly what the rule forbids.
 *
 * THE SERVER IS NAMED BY ITS SOCKET, UNDER THE OWNER'S CONFINEMENT RULE (Q4, owner-decision-remaining-boundaries-ANSWERS-20261029.md): every Herdr command here, the listing and the launcher alike,
 * runs with XDG_CONFIG_HOME, XDG_STATE_HOME, HERDR_SESSION and HERDR_CLIENT_SOCKET_PATH REMOVED and HOME and HERDR_SOCKET_PATH SET to the target's. Those inherited variables override HOME, and
 * every Herdr pane carries the live server's socket, so without this the route would reach the LIVE server. And it never uses `--session` or `herdr session attach <name>`: a named session is a
 * separate server of its own, which the target's start never creates.
 *
 * AT USE, NEVER CACHED (the Stage 4 design, Revision 5 R5.3): every `launch()` reads the Mission's binding afresh and runs Herdr's read-only `agent list` afresh, requires the producer's identity
 * verdict to be `verified`, and requires the recorded session to appear EXACTLY ONCE in the live list. The pane it reports is the one Herdr lists now; the catalog's recorded pane id is a read handle and
 * is never used to attach.
 *
 * ORDER: the target is validated before ANY effect; the binding is checked before Herdr's list is read; nothing is written or opened unless every admission check passed. An ADMISSION
 * refusal (target, binding, identity, session, live list, launcher name) writes nothing and opens nothing. An OPERATIONAL failure is not that: a failed launcher write opens nothing but may leave
 * a partly written file, and a failed open leaves the written launcher in the launcher directory and names it.
 *
 * WHAT IT DOES NOT DO OR ESTABLISH:
 * - It is not reachable from any user control: the Herdr controls stay drawn disabled until the runtime proof is accepted. Nothing in the office composes it.
 * - The Herdr SERVER comes from the target's socket path. On the disposable proof target it is known because the proof creates it. How the app finds the server, workspace and agent of a REAL
 *   Mission is NOT solved, and stays owed with the design's determinacy questions (where the attach lands; whether a `workspace focus` is needed and in which order). This route attaches only; it
 *   focuses nothing.
 * - It imports no process module: the Node effects are built from APIs the composition passes in (`nodeHerdrLaunchEffects`), so no test here can start a process.
 * - Launcher files are not removed after use; they stay in the target's launcher directory.
 */
import type { Classified, RuntimeIdentityState } from './catalogContract.js';

/** The macOS opener, and the application it is asked to open the launcher with. */
const OPEN_FILE = '/usr/bin/open';
const OPEN_APP_ARGS = ['-a', 'Terminal'] as const;
/** Herdr's read-only agent listing (the same read the catalog producer runs: target_runtime/runtime_snapshot.py LISTING_ARGV). */
const LISTING_ARGS = ['agent', 'list'] as const;
/** The listing's bounds. */
export const HERDR_LISTING_TIMEOUT_MS = 10_000;
export const HERDR_LISTING_MAX_BYTES = 4 * 1024 * 1024;
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
  /** HOME for every Herdr command here. Required: an inherited environment would reach the live server. */
  home: string;
  /** The server's API socket, set as HERDR_SOCKET_PATH for every Herdr command here. It must lie inside `home`. */
  socketPath: string;
  /** The absolute directory the launcher file is written in. */
  launcherDir: string;
}

/** What the Mission's catalog says about its role, read AT USE (the producer's own fields: target_runtime/herdr_task_source.py). */
export interface HerdrRoleBinding {
  missionRef: string;
  /** The role's Herdr agent name. */
  agent: string;
  /** The session the producer recorded for the role. */
  session: string | null;
  /** The pane the producer recorded: a READ handle, never used to attach. */
  paneId: string | null;
  /** The producer's identity verdict for this pass. */
  identity: { state: Classified<RuntimeIdentityState>; reason: string };
}

export interface HerdrLaunchEffects {
  /** A FRESH read of the Mission's binding. */
  readBinding(): Promise<HerdrRoleBinding | null>;
  /** Run the listing with the inherited environment less `unset`, plus `set`; resolve with its stdout, reject when it did not finish with status 0. */
  runListing(
    file: string,
    args: readonly string[],
    env: { set: Readonly<Record<string, string>>; unset: readonly string[] },
  ): Promise<string>;
  /** Create the launcher file with these options; reject on any failure (an existing file included). */
  writeLauncher(filePath: string, text: string, options: { flag: 'wx'; mode: number }): Promise<void>;
  /** Start the opener; resolve once it has started, reject when it could not be. */
  openInTerminal(file: string, args: readonly string[]): Promise<void>;
  /** A fresh random name for the launcher file. */
  launcherName(): string;
}

export type HerdrLaunchRefusal =
  | 'target_invalid'
  | 'no_binding'
  | 'identity_not_verified'
  | 'session_unknown'
  | 'listing_unreadable'
  | 'not_live'
  | 'live_more_than_once'
  | 'launcher_not_written'
  | 'launch_failed';

export type HerdrLaunchResult =
  | { launched: true; missionRef: string; agent: string; session: string; paneId: string | null; workspaceId: string | null; launcher: string }
  | { launched: false; refusal: HerdrLaunchRefusal; detail: string };

/** An absolute, normalised path of plain characters: no quote, space or shell character can reach the launcher. */
const PLAIN_PATH = /^(\/[A-Za-z0-9._-]+)+$/;
const PLAIN_LAUNCHER_NAME = /^[0-9a-f]{8,64}$/;

function plainPath(value: unknown): value is string {
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
  return null;
}

/** Q4's environment for every Herdr command here: the inherited one less HERDR_UNSET_ENV, with HOME and HERDR_SOCKET_PATH set. */
const herdrEnvironment = (target: HerdrAttachTarget) => ({
  set: { HOME: target.home, HERDR_SOCKET_PATH: target.socketPath },
  unset: HERDR_UNSET_ENV,
});

/**
 * The launcher file's text: a shebang and ONE command, the exec of PLAIN `herdr` under Q4's environment (no `--session`, no `session attach`). Every value was validated as plain, and is
 * single-quoted as well.
 */
export function herdrLauncherText(target: HerdrAttachTarget): string {
  const unset = HERDR_UNSET_ENV.map((name) => `-u ${name}`).join(' ');
  return `#!/bin/sh\nexec /usr/bin/env ${unset} HOME='${target.home}' HERDR_SOCKET_PATH='${target.socketPath}' '${target.herdrBin}'\n`;
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

const refuse = (refusal: HerdrLaunchRefusal, detail: string): HerdrLaunchResult => ({ launched: false, refusal, detail });

/** The launch route for one target. `launch()` reads everything it uses at the moment it is called. */
export function createHerdrLauncher(target: HerdrAttachTarget, effects: HerdrLaunchEffects): { launch(): Promise<HerdrLaunchResult> } {
  return {
    async launch(): Promise<HerdrLaunchResult> {
      const problem = targetProblem(target);
      if (problem !== null) return refuse('target_invalid', `Nothing was opened: ${problem}.`);

      const binding = await effects.readBinding();
      if (binding === null) return refuse('no_binding', 'Nothing was opened: this Mission has no recorded role to attach to.');
      const state = binding.identity.state;
      if (!(state.known && state.value === 'verified')) {
        const shown = state.known ? state.value : 'not known to this office';
        return refuse('identity_not_verified', `Nothing was opened: this Mission's agent identity is ${shown} (${binding.identity.reason}), not verified.`);
      }
      if (binding.session === null) return refuse('session_unknown', "Nothing was opened: no session is recorded for this Mission's agent.");

      let stdout: string;
      try {
        stdout = await effects.runListing(target.herdrBin, LISTING_ARGS, herdrEnvironment(target));
      } catch (err) {
        return refuse('listing_unreadable', `Nothing was opened: Herdr's live agent list could not be read (${(err as Error).message}).`);
      }
      const rows = liveRows(stdout);
      if (typeof rows === 'string') return refuse('listing_unreadable', `Nothing was opened: Herdr's live agent list is unreadable (${rows}).`);
      const matches = rows.filter((row) => row.session === binding.session);
      if (matches.length === 0) return refuse('not_live', "Nothing was opened: the recorded session is not in Herdr's live agent list now.");
      if (matches.length > 1)
        return refuse('live_more_than_once', `Nothing was opened: the recorded session is in Herdr's live agent list ${matches.length} times, so which one to attach cannot be told.`);
      const live = matches[0];

      const name = effects.launcherName();
      if (!PLAIN_LAUNCHER_NAME.test(name)) return refuse('launcher_not_written', 'Nothing was opened: the launcher name is not plain lowercase hex.');
      const launcher = `${target.launcherDir}/herdr-attach-${name}.command`;
      try {
        await effects.writeLauncher(launcher, herdrLauncherText(target), { ...LAUNCHER_OPTIONS });
      } catch (err) {
        return refuse('launcher_not_written', `Nothing was opened: the launcher could not be written (${(err as Error).message}).`);
      }
      try {
        await effects.openInTerminal(OPEN_FILE, [...OPEN_APP_ARGS, launcher]);
      } catch (err) {
        return refuse('launch_failed', `Terminal was not opened (${(err as Error).message}). The launcher was written at ${launcher}.`);
      }
      return { launched: true, missionRef: binding.missionRef, agent: binding.agent, session: binding.session, paneId: live.paneId, workspaceId: live.workspaceId, launcher };
    },
  };
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
  ): { once(event: 'spawn' | 'error', listener: (...args: unknown[]) => void): unknown; unref(): void };
  writeFile(filePath: string, text: string, options: { flag: 'wx'; mode: number; encoding: 'utf8' }): Promise<void>;
  randomBytes(size: number): { toString(encoding: 'hex'): string };
  env: Readonly<Record<string, string | undefined>>;
}

/** The effects over real Node APIs: no shell anywhere; the listing bounded in time and size; the opener detached, its output ignored, never waited on. */
export function nodeHerdrLaunchEffects(node: NodeLaunchApis, readBinding: () => Promise<HerdrRoleBinding | null>): HerdrLaunchEffects {
  return {
    readBinding,
    runListing: (file, args, { set, unset }) =>
      new Promise((resolve, reject) => {
        const inherited = Object.fromEntries(
          Object.entries(node.env).filter(([name]) => !unset.includes(name)),
        );
        node.execFile(
          file,
          args,
          {
            env: { ...inherited, ...set },
            shell: false,
            timeout: HERDR_LISTING_TIMEOUT_MS,
            maxBuffer: HERDR_LISTING_MAX_BYTES,
            encoding: 'utf8',
          },
          (error, stdout) => (error === null ? resolve(stdout) : reject(error)),
        );
      }),
    writeLauncher: (filePath, text, options) => node.writeFile(filePath, text, { ...options, encoding: 'utf8' }),
    openInTerminal: (file, args) =>
      new Promise((resolve, reject) => {
        const child = node.spawn(file, args, { shell: false, detached: true, stdio: 'ignore' });
        child.once('spawn', () => resolve());
        child.once('error', (err) => reject(err as Error));
        child.unref();
      }),
    launcherName: () => node.randomBytes(12).toString('hex'),
  };
}
