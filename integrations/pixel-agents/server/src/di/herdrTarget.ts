/**
 * THE HERDR ROUTE'S TARGET: what the office is told about the Herdr server, and what it reads about one Mission's herd. NARROW AND VALIDATED, never discovered.
 *
 * Nothing here searches. The server is named by three EXPLICIT office flags (--herdr-bin, --herdr-home, --herdr-socket); each must be a plain absolute path (herdrLaunch.ts's own guard, not a copy of it),
 * and the socket must lie inside the home. No flag is guessed from PATH, from the environment, from another checkout or from a registry, and none has a default. With none of the three the route is
 * simply not configured, and says which flags it is waiting for; with some but not all, the office refuses to start and names what is missing. The Mission's herd is not configured at all: it is read, at
 * use, from the office's own latest catalog answer, and verified against Herdr's live listing by the route (herdrLaunch.ts).
 *
 * This module imports no process module and starts nothing. The launcher-directory check reads the filesystem only through functions it is handed (`LauncherDirFs`); `realLauncherDirFs` is the real
 * one, and a test that needs real symbolic links passes it too. Containment of every path it is about to create or write is NOT decided here: it is the office's existing guard (diStateRoot.ts), passed in.
 */
import * as nodeFs from 'fs';

import type { RuntimeIdentityState } from './catalogContract.js';
import { classify, RUNTIME_IDENTITY_STATES } from './catalogContract.js';
import type { HerdrBindingUnavailable, HerdrRoleBinding } from './herdrLaunch.js';
import { establishIdentity, plainPath } from './herdrLaunch.js';
import type { MissionPresentation, OfficeView } from './officeView.js';

/** The three office flags, in the order they are documented. */
export const HERDR_FLAGS = ['--herdr-bin', '--herdr-home', '--herdr-socket'] as const;

export interface HerdrConfig {
  /** The absolute path of the herdr executable. */
  bin: string;
  /** HOME for every Herdr command. */
  home: string;
  /** The server's API socket; inside `home`. */
  socketPath: string;
}

/** Why one flag's value is refused, or null. The message names the flag, as every other office flag's does. */
export function herdrFlagProblem(flag: (typeof HERDR_FLAGS)[number], value: string): string | null {
  if (!plainPath(value)) {
    return `${flag} must be a plain absolute path: letters, digits, '.', '_' and '-' in each segment, no '.' or '..' segment, no trailing slash, no spaces`;
  }
  return null;
}

/**
 * The route's configuration from the three flag values (each undefined when the flag was not given): null when NONE was given (not configured); otherwise all three, each valid, the socket inside the
 * home. A problem is one message naming the exact flag(s). It never reads the environment and never fills anything in.
 */
export function resolveHerdrConfig(flags: {
  bin?: string;
  home?: string;
  socket?: string;
}): { config: HerdrConfig | null } | { problem: string } {
  const given = [flags.bin, flags.home, flags.socket];
  if (given.every((value) => value === undefined)) return { config: null };
  const missing = HERDR_FLAGS.filter((_, index) => given[index] === undefined);
  if (missing.length > 0) {
    return { problem: `Herdr targeting needs all three of ${HERDR_FLAGS.join(', ')}; missing ${missing.join(', ')}` };
  }
  const [bin, home, socketPath] = given as [string, string, string];
  const problem =
    herdrFlagProblem('--herdr-bin', bin) ?? herdrFlagProblem('--herdr-home', home) ?? herdrFlagProblem('--herdr-socket', socketPath);
  if (problem !== null) return { problem };
  if (!socketPath.startsWith(`${home}/`)) return { problem: '--herdr-socket must lie inside --herdr-home' };
  return { config: { bin, home, socketPath } };
}

/** The real filesystem for the launcher-directory check (the composition's; a test that wants real symbolic links uses it too, so it runs the production code, not a stand-in). */
export function realLauncherDirFs(): LauncherDirFs {
  return {
    lstat: (p) => {
      try {
        return nodeFs.lstatSync(p);
      } catch {
        return null;
      }
    },
    // not recursive: the state root exists and was validated; a missing PARENT is a problem to report, not to create
    mkdir: (p) => nodeFs.mkdirSync(p, { mode: 0o700 }),
    uid: () => (typeof process.getuid === 'function' ? process.getuid() : -1),
  };
}

/** The filesystem calls the launcher-directory check needs. */
export interface LauncherDirFs {
  lstat(path: string): { isDirectory(): boolean; isSymbolicLink(): boolean; mode: number; uid: number } | null;
  /** Create the directory, owner-only. Called ONLY when preparing for a launch, never when asking whether one is available. */
  mkdir(path: string): void;
  /** The uid this process runs as. */
  uid(): number;
}

/**
 * The office's EXISTING write-containment guard (diStateRoot.ts `makeDiStateWriteGuard`), given a path it is about to write: it resolves the path's PARENT physically at that moment, through its nearest
 * existing ancestor, so a redirected ancestor is followed to where it really points; requires that to lie inside the validated physical state root (case-preserving); and refuses a target that is itself a
 * symbolic link. It THROWS to refuse. It is passed in rather than reimplemented here: a second, weaker copy of this guard is exactly the defect a review found. It is a REQUIRED argument everywhere below
 * so no call site can leave it out.
 */
export type StateContainment = (target: string) => void;

/**
 * Why the launcher directory cannot be used, or null. FIRST, before the directory is looked at or created, its PHYSICAL location must be inside the state root (`contain`): a final-path check alone passes
 * when an ANCESTOR was redirected, because the directory it finds there is a real one, somewhere else. THEN it must be a real directory (never a symbolic link), owned by this process's user and closed to
 * everyone else (no group or other access): a launcher file is a command a terminal will run, so nobody else may be able to put one there or swap one. `create` makes a missing directory (owner-only),
 * and checks containment AGAIN once it exists; without `create`, a missing directory is fine ("it will be created") and is not an error.
 */
export function launcherDirProblem(dir: string, fs: LauncherDirFs, create: boolean, contain: StateContainment): string | null {
  const escaped = (): string | null => {
    try {
      contain(dir);
      return null;
    } catch (err) {
      return `the launcher directory ${dir} is refused by the state-root containment guard (${(err as Error).message})`;
    }
  };
  const before = escaped();
  if (before !== null) return before;
  let stat = fs.lstat(dir);
  if (stat === null) {
    if (!create) return null;
    try {
      fs.mkdir(dir);
    } catch (err) {
      return `the launcher directory ${dir} could not be created (${(err as Error).message})`;
    }
    const after = escaped();
    if (after !== null) return after;
    stat = fs.lstat(dir);
    if (stat === null) return `the launcher directory ${dir} was not there after it was created`;
  }
  if (stat.isSymbolicLink()) return `the launcher directory ${dir} is a symbolic link`;
  if (!stat.isDirectory()) return `the launcher directory ${dir} is not a directory`;
  if (stat.uid !== fs.uid()) return `the launcher directory ${dir} is not owned by this user`;
  if ((stat.mode & 0o077) !== 0) return `the launcher directory ${dir} is open to other users (mode ${(stat.mode & 0o777).toString(8)}); it must be owner-only`;
  return null;
}

/** Longest Mission reference the protocol carries (core/asyncapi.yaml, DiCodeReviewRead.missionRef): the same bound, so one reference grammar is used for every read. */
export const MAX_MISSION_REF_CHARS = 104;

/** Whether a client-supplied Mission reference is worth looking up at all. It is only ever MATCHED against the office's own view, never used as a path or an argument. */
export function isMissionRef(value: unknown): value is string {
  return typeof value === 'string' && value.length > 0 && value.length <= MAX_MISSION_REF_CHARS && !/[\u0000-\u001f\u007f]/.test(value);
}

/**
 * The role binding for ONE Mission, read from the office's LATEST catalog view, or the plain reason there is none (each reason is shown to the person verbatim). Nothing is cached: the caller passes the view
 * it holds NOW. Only a plain Herdr task (`herdr.task/1`) has a herd to attach to here, only an ACTIVE one is offered (a cancelled, aborted or finished task is never selectable), and the answer must be
 * live: an expired catalog answer is not evidence of anything.
 *
 * The role is one of the task's runtime agents (`runtime_agents`, joined on `root_ref` = the Mission's own reference) or one of its team-roster members (`members`), whichever is listed: see the two-places
 * comment below. The role anchors identity evidence only; it is never the thing attached to. What the route then accepts from each kind of role is herdrLaunch.ts's `teamMemberProblem` and `establishIdentity`.
 */
export function herdrBindingFromView(view: OfficeView, missionRef: string): HerdrRoleBinding | HerdrBindingUnavailable {
  if (view.connection.state !== 'answered') {
    return { unavailable: `the office has no catalog answer now (${view.connection.reason})` };
  }
  if (!view.live) {
    return { unavailable: `the office's catalog answer is not live (${view.notLiveReason ?? 'no reason was given'})` };
  }
  if (view.missions.state !== 'observed') {
    return { unavailable: `the office does not list Missions now (${view.missions.state})` };
  }
  const found = view.missions.value.filter((mission) => mission.missionRef === missionRef);
  if (found.length === 0) return { unavailable: 'the office does not list this Mission now' };
  if (found.length > 1) return { unavailable: 'the office lists this Mission more than once, so which herd is meant cannot be told' };
  const mission = found[0];
  if (mission.sourceKind !== 'herdr_task') {
    return { unavailable: `this Mission is a ${mission.sourceKind} Mission, not a Herdr task, so it has no herd of its own to attach to` };
  }
  if (!mission.standingKnown || mission.standing !== 'active') {
    return {
      unavailable: `this Herdr task is ${mission.lifecycleState} (${mission.standingKnown ? mission.standing : 'standing not known to this office'}), not active, so no herd is offered for it`,
    };
  }
  // THE TWO PLACES A TASK'S ROLES ARE LISTED. A role not yet claimed by a team is a runtime agent; a role the Mission's team roster already holds is a MEMBER, and the producer then leaves it OUT of
  // `runtime_agents` (herdr_task_source.py counts it as represented_by_teams). So for the normal, healthy, bound task `runtime_agents.agents` is empty and the roster is where its roles are. Both are
  // read, and a candidate from either is judged by the route's own rule (establishIdentity), not by a second copy of it here.
  const byRef = (a: string, b: string): number => (a < b ? -1 : a > b ? 1 : 0);
  const runtime: HerdrRoleBinding[] = view.runtimeAgents.agents
    .filter((agent) => agent.rootRef === missionRef)
    .sort((a, b) => byRef(a.agentRef, b.agentRef))
    .map((agent) => ({
      missionRef,
      agent: agent.agent,
      session: agent.session,
      paneId: agent.paneId,
      identity: {
        state: classify(RUNTIME_IDENTITY_STATES, agent.identityState) as { known: true; value: RuntimeIdentityState } | { known: false; raw: unknown },
        reason: agent.identityReason,
      },
    }));
  const roster = mission.members.state === 'observed' ? mission.members.value : [];
  const team: HerdrRoleBinding[] = roster
    .filter((member) => member.missionRef === missionRef)
    .sort((a, b) => byRef(a.memberRef, b.memberRef))
    .map((member) => ({
      missionRef,
      agent: member.agent,
      session: member.sessionRecorded.state === 'observed' ? member.sessionRecorded.value : null,
      paneId: member.paneId.state === 'observed' ? member.paneId.value : null,
      identity: {
        source: 'team_member' as const,
        classification: member.classification,
        reason: member.reason,
        presenceLive: member.presence.live,
        chain: member.chain,
        sessionRecorded: member.sessionRecorded.state === 'observed' ? member.sessionRecorded.value : null,
        sessionLive: member.sessionLive.state === 'observed' ? member.sessionLive.value : null,
      },
    }));
  // Runtime agents first: a verified runtime identity is the stronger claim. Then the roster. The first role whose identity IS established wins; if none is, the first role overall is returned anyway, so the
  // route refuses on its real verdict (and names it) instead of a generic "no role". Never a role that was not listed, and never a fabricated verdict.
  const candidates = [...runtime, ...team];
  if (candidates.length > 0) {
    return candidates.find((candidate) => 'basis' in establishIdentity(candidate) && candidate.session !== null) ?? candidates[0];
  }
  return { unavailable: noRoleReason(view, mission) };
}

/**
 * Why a task lists NO role, in what the catalog actually said about each place roles are listed. It never says the catalog "records no role" when it recorded some: a task whose roles the producer counted as
 * represented by a team, with no roster entry to show for them, is said to be exactly that.
 */
function noRoleReason(view: OfficeView, mission: MissionPresentation): string {
  const agents = view.runtimeAgents;
  let runtime: string;
  if (!agents.reported) runtime = 'the catalog does not report runtime agents';
  else if (agents.state !== 'observed') {
    runtime = `the catalog could not read runtime agents (${agents.state ?? 'no state'}${agents.problem === null ? '' : `: ${agents.problem}`})`;
  } else runtime = 'the catalog lists no runtime agent for this task';
  const members = mission.members;
  let team: string;
  if (members.state === 'observed') team = 'its team roster lists no member';
  else if ('reason' in members) team = `its team roster is ${members.state} (${members.reason})`;
  else team = `its team roster is ${members.state}`;
  const represented = agents.counts?.represented_by_teams ?? 0;
  const counted = agents.reported && represented > 0 ? ` The catalog counts ${represented} role(s) as represented by a team, but lists none of them here.` : '';
  return `${runtime}, and ${team}, so there is no role to find this task's herd by.${counted}`;
}
