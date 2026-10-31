/**
 * THE HERDR ROUTE'S TARGET (A1): narrow, validated, never discovered.
 *
 * Three things are stated here. (1) The office is told the Herdr server by three explicit flags, each a plain absolute path under the SAME guard the route itself uses, the socket inside the home; with none
 * the route is "not configured", with some it refuses to start, and nothing is defaulted, found on PATH or read from the environment. (2) The launcher directory is checked before a runnable file is put in
 * it. (3) A Mission's binding is read from the office's latest catalog view, and a cancelled, finished, unlisted, non-Herdr or unverified task is never selectable.
 *
 * WHAT THIS DOES NOT ESTABLISH: that the catalog's view matches Herdr's live server (the route checks that, against Herdr's own list, at use), or anything about a real Herdr.
 */
import * as nodeFs from 'fs';
import * as os from 'os';
import * as path from 'path';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';

import { makeDiStateWriteGuard } from '../src/di/diStateRoot.js';
import type { HerdrRoleBinding } from '../src/di/herdrLaunch.js';
import { establishIdentity } from '../src/di/herdrLaunch.js';
import type { LauncherDirFs, StateContainment } from '../src/di/herdrTarget.js';
import {
  HERDR_FLAGS,
  herdrBindingFromView,
  herdrFlagProblem,
  isMissionRef,
  launcherDirProblem,
  MAX_MISSION_REF_CHARS,
  realLauncherDirFs,
  resolveHerdrConfig,
} from '../src/di/herdrTarget.js';
import type { OfficeView } from '../src/di/officeView.js';

const BIN = '/Users/someone/.local/bin/herdr';
const HOME = '/Users/someone';
const SOCKET = '/Users/someone/.config/herdr/herdr.sock';

describe('the three flags (A1): explicit, plain, absolute, the socket inside the home, and nothing defaulted', () => {
  it('names exactly the three flags', () => {
    expect([...HERDR_FLAGS]).toEqual(['--herdr-bin', '--herdr-home', '--herdr-socket']);
  });

  it('with none of them the route is not configured (null), not defaulted from anywhere', () => {
    expect(resolveHerdrConfig({})).toEqual({ config: null });
  });

  it('with all three, valid, the configuration is exactly what was given', () => {
    expect(resolveHerdrConfig({ bin: BIN, home: HOME, socket: SOCKET })).toEqual({ config: { bin: BIN, home: HOME, socketPath: SOCKET } });
  });

  it.each([
    ['only the executable', { bin: BIN }, ['--herdr-home', '--herdr-socket']],
    ['only the home', { home: HOME }, ['--herdr-bin', '--herdr-socket']],
    ['only the socket', { socket: SOCKET }, ['--herdr-bin', '--herdr-home']],
    ['the executable and the home', { bin: BIN, home: HOME }, ['--herdr-socket']],
  ])('%s is a startup problem that names exactly what is missing', (_name, flags, missing) => {
    const result = resolveHerdrConfig(flags);
    expect(result).toHaveProperty('problem');
    const problem = (result as { problem: string }).problem;
    for (const flag of missing) expect(problem).toContain(flag);
    for (const flag of HERDR_FLAGS.filter((f) => !missing.includes(f))) expect(problem.split('missing')[1] ?? '').not.toContain(flag);
  });

  it.each([
    ['a relative executable', { bin: 'herdr', home: HOME, socket: SOCKET }, '--herdr-bin'],
    ['an executable with a space', { bin: '/Users/some one/herdr', home: HOME, socket: SOCKET }, '--herdr-bin'],
    ['an executable with a parent segment', { bin: '/Users/someone/../herdr', home: HOME, socket: SOCKET }, '--herdr-bin'],
    ['an executable with a quote', { bin: "/Users/someone/h'erdr", home: HOME, socket: SOCKET }, '--herdr-bin'],
    ['a relative home', { bin: BIN, home: 'someone', socket: SOCKET }, '--herdr-home'],
    ['a home with a trailing slash', { bin: BIN, home: '/Users/someone/', socket: SOCKET }, '--herdr-home'],
    ['a relative socket', { bin: BIN, home: HOME, socket: 'herdr.sock' }, '--herdr-socket'],
    ['a socket with a shell character', { bin: BIN, home: HOME, socket: '/Users/someone/.config/herdr/h;sock' }, '--herdr-socket'],
    ['an empty value', { bin: '', home: HOME, socket: SOCKET }, '--herdr-bin'],
  ])('%s is refused, and the message names the exact flag', (_name, flags, flag) => {
    const result = resolveHerdrConfig(flags);
    expect(result).toHaveProperty('problem');
    expect((result as { problem: string }).problem).toContain(flag);
  });

  it('a socket outside the home is refused (the route\'s own rule is enforced here too, never bypassed), naming both flags', () => {
    const result = resolveHerdrConfig({ bin: BIN, home: HOME, socket: '/Users/other/.config/herdr/herdr.sock' });
    expect(result).toEqual({ problem: '--herdr-socket must lie inside --herdr-home' });
  });

  it('a socket that merely starts with the home\'s characters is NOT inside it', () => {
    expect(resolveHerdrConfig({ bin: BIN, home: HOME, socket: '/Users/someone2/herdr.sock' })).toHaveProperty('problem');
  });

  it('there is a single plain-path rule: a value is refused for the same reason the route would refuse it', () => {
    expect(herdrFlagProblem('--herdr-bin', BIN)).toBeNull();
    expect(herdrFlagProblem('--herdr-bin', '/a b')).toContain('plain absolute path');
  });
});

/**
 * The tests of the directory's OWN checks (symbolic link, kind, owner, mode, creation) hold containment out of the picture on purpose: containment is the office's existing guard and is proved against
 * REAL symbolic links in the describe that follows, not against a stand-in. This one accepts everything and is used ONLY with the in-memory filesystem below.
 */
const NO_CONTAINMENT: StateContainment = () => undefined;

describe('the launcher directory: a runnable file is put only where nobody else can', () => {
  const OWNER = 501;
  const DIR = '/state/herdr-launchers';
  function fs(over: { stat?: ReturnType<LauncherDirFs['lstat']> | 'created'; mkdirThrows?: boolean } = {}): { fs: LauncherDirFs; made: string[] } {
    const made: string[] = [];
    // `stat: null` and `stat: 'created'` both start with NO directory (the caller reads absence); only an omitted `stat` means an ordinary owner-only one.
    let current: ReturnType<LauncherDirFs['lstat']> =
      over.stat === undefined ? { isDirectory: () => true, isSymbolicLink: () => false, mode: 0o40700, uid: OWNER } : over.stat === 'created' ? null : over.stat;
    return {
      made,
      fs: {
        lstat: () => current,
        mkdir: (p) => {
          if (over.mkdirThrows) throw new Error('EACCES');
          made.push(p);
          current = { isDirectory: () => true, isSymbolicLink: () => false, mode: 0o40700, uid: OWNER };
        },
        uid: () => OWNER,
      },
    };
  }

  it('an owner-only directory of ours is fine', () => {
    expect(launcherDirProblem(DIR, fs().fs, false, NO_CONTAINMENT)).toBeNull();
    expect(launcherDirProblem(DIR, fs().fs, true, NO_CONTAINMENT)).toBeNull();
  });

  it('a missing directory is fine when only asking, and nothing is created', () => {
    const { fs: stand, made } = fs({ stat: null });
    expect(launcherDirProblem(DIR, stand, false, NO_CONTAINMENT)).toBeNull();
    expect(made).toEqual([]);
  });

  it('a missing directory is created owner-only when preparing a launch, and then checked', () => {
    const { fs: stand, made } = fs({ stat: 'created' });
    expect(launcherDirProblem(DIR, stand, true, NO_CONTAINMENT)).toBeNull();
    expect(made).toEqual([DIR]);
  });

  it('a directory that cannot be created is a problem that says why', () => {
    expect(launcherDirProblem(DIR, fs({ stat: null, mkdirThrows: true }).fs, true, NO_CONTAINMENT)).toContain('EACCES');
  });

  it.each([
    ['a symbolic link', { isDirectory: () => true, isSymbolicLink: () => true, mode: 0o40700, uid: OWNER }, 'symbolic link'],
    ['not a directory', { isDirectory: () => false, isSymbolicLink: () => false, mode: 0o100700, uid: OWNER }, 'not a directory'],
    ['owned by another user', { isDirectory: () => true, isSymbolicLink: () => false, mode: 0o40700, uid: 0 }, 'not owned by this user'],
    ['readable by the group', { isDirectory: () => true, isSymbolicLink: () => false, mode: 0o40750, uid: OWNER }, 'open to other users'],
    ['writable by others', { isDirectory: () => true, isSymbolicLink: () => false, mode: 0o40702, uid: OWNER }, 'open to other users'],
  ])('a directory that is %s is refused, with the reason', (_name, stat, words) => {
    expect(launcherDirProblem(DIR, fs({ stat }).fs, false, NO_CONTAINMENT)).toContain(words);
  });
});

/**
 * PHYSICAL CONTAINMENT, against REAL symbolic links (review finding 2). An in-memory check of the final path passes when an ANCESTOR is redirected, because the directory found there is a real one, somewhere
 * else; the office's existing guard (`makeDiStateWriteGuard`) resolves the path's parent physically and refuses it. These tests run the production `launcherDirProblem` with the production
 * `realLauncherDirFs()` and the production guard over a temporary directory tree, and look at the disk afterwards: what matters is that NOTHING was created outside the state root.
 *
 * WHAT THIS DOES NOT ESTABLISH: anything about a real office's state root, or filesystems other than the one the temporary directory is on.
 */
describe('the launcher directory is contained PHYSICALLY: a redirected directory or ancestor is refused before anything is created, on a real filesystem', () => {
  let base: string;
  let root: string;
  let outside: string;

  beforeEach(() => {
    base = nodeFs.realpathSync(nodeFs.mkdtempSync(path.join(os.tmpdir(), 'herdrcontain-')));
    root = path.join(base, 'state');
    outside = path.join(base, 'outside');
    nodeFs.mkdirSync(root, { mode: 0o700 });
    nodeFs.mkdirSync(outside, { mode: 0o700 });
  });

  afterEach(() => {
    // only the directory this describe made, by its exact shape, directly under the temp root
    if (path.dirname(base) === nodeFs.realpathSync(os.tmpdir()) && /^herdrcontain-[A-Za-z0-9]{6}$/.test(path.basename(base))) nodeFs.rmSync(base, { recursive: true, force: true });
  });

  const entries = (dir: string): string[] => nodeFs.readdirSync(dir).sort();

  it('the ordinary case: a launcher directory directly under the root is created owner-only and accepted', () => {
    const dir = path.join(root, 'herdr-launchers');
    expect(launcherDirProblem(dir, realLauncherDirFs(), true, makeDiStateWriteGuard(root))).toBeNull();
    expect(nodeFs.lstatSync(dir).isDirectory()).toBe(true);
    expect(nodeFs.lstatSync(dir).mode & 0o777).toBe(0o700);
  });

  it('asking (not preparing a launch) never creates the directory', () => {
    const dir = path.join(root, 'herdr-launchers');
    expect(launcherDirProblem(dir, realLauncherDirFs(), false, makeDiStateWriteGuard(root))).toBeNull();
    expect(entries(root)).toEqual([]);
  });

  it('the launcher directory ITSELF a symbolic link to outside the root is refused, and nothing outside is touched', () => {
    const dir = path.join(root, 'herdr-launchers');
    nodeFs.symlinkSync(outside, dir);
    for (const create of [false, true]) {
      const problem = launcherDirProblem(dir, realLauncherDirFs(), create, makeDiStateWriteGuard(root));
      expect(problem, `create=${create}`).toContain('refused by the state-root containment guard');
      expect(problem).toContain('symbolic link');
    }
    expect(entries(outside)).toEqual([]);
  });

  it('the launcher directory a symbolic link to a place INSIDE the root is refused too: the guard refuses every link target', () => {
    const real = path.join(root, 'elsewhere');
    nodeFs.mkdirSync(real, { mode: 0o700 });
    const dir = path.join(root, 'herdr-launchers');
    nodeFs.symlinkSync(real, dir);
    expect(launcherDirProblem(dir, realLauncherDirFs(), true, makeDiStateWriteGuard(root))).toContain('refused by the state-root containment guard');
    expect(entries(real)).toEqual([]);
  });

  it('A REDIRECTED ANCESTOR (the reviewer\'s case): the state root is replaced by a link to outside AFTER startup, and the directory is refused, not created in the outside place', () => {
    // the guard is built at startup from the validated physical root, as the composition does
    const guard = makeDiStateWriteGuard(root);
    // later, the root itself is moved aside and a link to an attacker-chosen directory stands where it was
    const moved = path.join(base, 'state-moved');
    nodeFs.renameSync(root, moved);
    nodeFs.symlinkSync(outside, root);
    const dir = path.join(root, 'herdr-launchers');
    for (const create of [false, true]) {
      const problem = launcherDirProblem(dir, realLauncherDirFs(), create, guard);
      expect(problem, `create=${create}`).toContain('refused by the state-root containment guard');
      expect(problem).toContain('outside the DI state root');
    }
    expect(entries(outside), 'nothing was created in the place the link points at').toEqual([]);
  });

  it('a redirected ancestor BELOW the root is refused as well: a link at an intermediate directory to outside', () => {
    const middle = path.join(root, 'middle');
    nodeFs.symlinkSync(outside, middle);
    const dir = path.join(middle, 'herdr-launchers');
    expect(launcherDirProblem(dir, realLauncherDirFs(), true, makeDiStateWriteGuard(root))).toContain('outside the DI state root');
    expect(entries(outside)).toEqual([]);
  });

  it('documents the WEAKNESS this replaced: a check of the final path alone ACCEPTS the redirected ancestor that the existing guard refuses', () => {
    const guard = makeDiStateWriteGuard(root);
    nodeFs.renameSync(root, path.join(base, 'state-moved'));
    nodeFs.symlinkSync(outside, root);
    const dir = path.join(root, 'herdr-launchers');
    // the old shape: look at the final path in memory only. It creates the directory in the OUTSIDE place and finds a perfectly ordinary one there.
    expect(launcherDirProblem(dir, realLauncherDirFs(), true, NO_CONTAINMENT)).toBeNull();
    expect(entries(outside), 'with no containment the directory IS created outside').toEqual(['herdr-launchers']);
    // the same input under the existing guard
    nodeFs.rmSync(path.join(outside, 'herdr-launchers'), { recursive: true });
    expect(launcherDirProblem(dir, realLauncherDirFs(), true, guard)).toContain('outside the DI state root');
    expect(entries(outside)).toEqual([]);
  });

  it('containment is checked AGAIN after the directory is created: a guard that starts refusing once it exists is honoured', () => {
    const dir = path.join(root, 'herdr-launchers');
    let calls = 0;
    const second: StateContainment = () => {
      calls += 1;
      if (calls === 2) throw new Error('moved while being created');
    };
    const problem = launcherDirProblem(dir, realLauncherDirFs(), true, second);
    expect(calls).toBe(2);
    expect(problem).toContain('moved while being created');
  });

  it('containment is checked FIRST: a refusing guard means the filesystem is not asked anything, so nothing is created', () => {
    const touched: string[] = [];
    const watching: LauncherDirFs = {
      lstat: (p) => {
        touched.push(`lstat ${p}`);
        return null;
      },
      mkdir: (p) => touched.push(`mkdir ${p}`),
      uid: () => 0,
    };
    const refuse: StateContainment = () => {
      throw new Error('outside');
    };
    expect(launcherDirProblem(path.join(root, 'herdr-launchers'), watching, true, refuse)).toContain('outside');
    expect(touched).toEqual([]);
  });
});

describe('a Mission reference is only ever matched against the office\'s own view', () => {
  it.each([
    ['a Herdr task reference', 'di_herdr_tasks:task-1@abc123', true],
    ['an empty string', '', false],
    ['a string over the protocol bound', 'x'.repeat(MAX_MISSION_REF_CHARS + 1), false],
    ['a string at the bound', 'x'.repeat(MAX_MISSION_REF_CHARS), true],
    ['a reference with a control character', 'a\u0000b', false],
    ['a reference with a newline', 'a\nb', false],
    ['a number', 7, false],
    ['null', null, false],
  ])('%s: %s', (_name, value, expected) => {
    expect(isMissionRef(value)).toBe(expected);
  });
});

describe('a Mission\'s binding comes from the office\'s latest view, and a task that is not active, listed, Herdr or live is never selectable', () => {
  const REF = 'di_herdr_tasks:task-1@abc123';
  interface AgentOver {
    agentRef?: string;
    rootRef?: string;
    agent?: string;
    session?: string | null;
    paneId?: string | null;
    identityState?: string;
    identityVerified?: boolean;
    identityReason?: string;
  }
  function agent(over: AgentOver = {}) {
    return {
      agentRef: 'di_herdr_tasks:abc123#executor1',
      rootRef: REF,
      agent: 'he-exec1',
      session: 'sess-exec1',
      paneId: 'w6V:p3',
      identityState: 'verified',
      identityVerified: true,
      identityReason: 'present',
      ...over,
    };
  }
  /** Only the fields herdrBindingFromView reads: the test says so by building the view from just those. */
  function view(
    over: {
      connection?: unknown;
      live?: boolean;
      notLiveReason?: string | null;
      missions?: unknown;
      reported?: boolean;
      agents?: unknown[];
      /** `runtime_agents.state` and `.problem` (default: observed, none). */
      runtimeState?: string | null;
      runtimeProblem?: string | null;
      /** `runtime_agents.counts` (default: none reported). */
      counts?: Record<string, number> | null;
      /** The Mission's team roster as the office presents it (default: observed, empty). */
      members?: unknown;
      mission?: Partial<{ missionRef: string; sourceKind: string; standing: string; standingKnown: boolean; lifecycleState: string }>;
    } = {},
  ): OfficeView {
    const mission = {
      missionRef: REF,
      sourceKind: 'herdr_task',
      standing: 'active',
      standingKnown: true,
      lifecycleState: 'ACTIVE',
      members: over.members ?? { state: 'observed', value: [] },
      ...over.mission,
    };
    return {
      connection: over.connection ?? { state: 'answered' },
      live: over.live ?? true,
      notLiveReason: over.notLiveReason ?? null,
      missions: over.missions ?? { state: 'observed', value: [mission] },
      runtimeAgents: {
        reported: over.reported ?? true,
        state: over.runtimeState === undefined ? 'observed' : over.runtimeState,
        problem: over.runtimeProblem ?? null,
        counts: over.counts ?? null,
        agents: over.agents ?? [agent()],
      },
    } as unknown as OfficeView;
  }

  /** A roster entry as the office presents it (officeView.ts MemberPresentation), for a healthy session-confirmed role: the shape measured on a real contained catalog. */
  function member(over: Record<string, unknown> = {}) {
    return {
      coverage: 'direct',
      memberRef: 'di_herdr_tasks:abc123#executor1',
      missionRef: REF,
      agent: 'he-exec1',
      logical: 'executor1',
      role: 'executor',
      chain: 'direct',
      classification: 'present',
      reason: 'session_confirmed',
      presence: { live: true, reason: null },
      paneId: { state: 'observed', value: 'w6V:p3' },
      workspaceId: { state: 'observed', value: 'w6V' },
      sessionRecorded: { state: 'observed', value: 'sess-exec1' },
      sessionLive: { state: 'observed', value: 'sess-exec1' },
      ...over,
    };
  }
  const roster = (...members: unknown[]) => ({ state: 'observed', value: members });

  it('an active Herdr task with a verified role yields that role\'s recorded agent, session and pane, and its identity verdict', () => {
    expect(herdrBindingFromView(view(), REF)).toEqual({
      missionRef: REF,
      agent: 'he-exec1',
      session: 'sess-exec1',
      paneId: 'w6V:p3',
      identity: { state: { known: true, value: 'verified' }, reason: 'present' },
    });
  });

  it('with several roles it takes the first VERIFIED one in agent-reference order, not the first listed', () => {
    const result = herdrBindingFromView(
      view({
        agents: [
          agent({ agentRef: 'z', agent: 'zz', session: 'sz' }),
          agent({ agentRef: 'b', agent: 'unverified-b', session: 'sb', identityState: 'unverified', identityVerified: false, identityReason: 'session_replaced' }),
          agent({ agentRef: 'c', agent: 'cc', session: 'sc' }),
        ],
      }),
      REF,
    );
    expect(result).toMatchObject({ agent: 'cc', session: 'sc' });
  });

  it('when NO role is verified the first is returned anyway, so the route refuses on its real identity state instead of a generic one', () => {
    const result = herdrBindingFromView(
      view({ agents: [agent({ identityState: 'ambiguous', identityVerified: false, identityReason: 'multiple_roots' })] }),
      REF,
    );
    expect(result).toMatchObject({ identity: { state: { known: true, value: 'ambiguous' }, reason: 'multiple_roots' } });
  });

  it('an identity state this office does not know is carried as not known, never as verified', () => {
    const result = herdrBindingFromView(view({ agents: [agent({ identityState: 'verified_later', identityVerified: false })] }), REF);
    expect(result).toMatchObject({ identity: { state: { known: false, raw: 'verified_later' } } });
  });

  it('only roles whose root is THIS Mission count: another task\'s verified role is not borrowed', () => {
    const result = herdrBindingFromView(view({ agents: [agent({ rootRef: 'di_herdr_tasks:other@zzz' })] }), REF);
    expect(result).toEqual({
      unavailable: "the catalog lists no runtime agent for this task, and its team roster lists no member, so there is no role to find this task's herd by.",
    });
  });

  it.each([
    ['ABORTED, a terminal task (a cancelled one is never selectable)', { standing: 'terminal', lifecycleState: 'ABORTED' }, 'ABORTED (terminal), not active'],
    ['COMPLETED, a finished task', { standing: 'terminal', lifecycleState: 'COMPLETED' }, 'COMPLETED (terminal), not active'],
    ['in ERROR, an inactive task', { standing: 'inactive', lifecycleState: 'ERROR' }, 'ERROR (inactive), not active'],
    ['in a state this office cannot place', { standing: 'unknown_future', lifecycleState: 'PAUSED', standingKnown: false }, 'standing not known to this office'],
  ])('a task that is %s is not selectable, and the reason names its state', (_name, mission, words) => {
    const result = herdrBindingFromView(view({ mission }), REF);
    expect(result).toHaveProperty('unavailable');
    expect((result as { unavailable: string }).unavailable).toContain(words);
  });

  it.each([
    ['the office has no answer', { connection: { state: 'unavailable', reason: 'discovery:file_missing' } }, 'no catalog answer now (discovery:file_missing)'],
    ['the answer is not live', { live: false, notLiveReason: 'expired' }, 'not live (expired)'],
    ['the answer is not live and gives no reason', { live: false }, 'not live (no reason was given)'],
    ['Missions are not observed', { missions: { state: 'unavailable', reason: 'x' } }, 'does not list Missions now (unavailable)'],
    ['the Mission is not listed', { missions: { state: 'observed', value: [] } }, 'does not list this Mission now'],
    ['the Mission is listed twice', { missions: { state: 'observed', value: [{ missionRef: REF }, { missionRef: REF }] } }, 'more than once'],
    ['it is not a Herdr task', { mission: { sourceKind: 'mission_core' } }, 'mission_core Mission, not a Herdr task'],
    // an older producer that predates the field: "not reported" is a block with NO agents (RUNTIME_AGENTS_NOT_REPORTED), so that is what is supplied
    ['runtime agents are not reported', { reported: false, runtimeState: null, agents: [] }, 'does not report runtime agents, and its team roster lists no member'],
    ['it has no roles anywhere', { agents: [] }, 'lists no runtime agent for this task, and its team roster lists no member'],
  ])('when %s the binding is unavailable and says exactly that', (_name, over, words) => {
    const result = herdrBindingFromView(view(over), REF);
    expect(result).toHaveProperty('unavailable');
    expect((result as { unavailable: string }).unavailable).toContain(words);
  });

  // THE TEAM-REPRESENTED SHAPE, measured on a real contained catalog for a healthy bound Herdr task (lead-readiness-binding-gap.md): runtime_agents observed with roles_recorded=4, roles_live=4,
  // represented_by_teams=4, verified=0 and agents=[] (EMPTY), while the Mission's four members are all classification present, reason session_confirmed, session.live equal to session.recorded. The producer
  // moves a role the team already holds OUT of runtime_agents, so for the normal bound task the roster is the only place its roles are listed.
  describe('the team-represented shape: runtime_agents is EMPTY and the roles are the Mission\'s team roster', () => {
    const BOUND = {
      runtimeState: 'observed',
      counts: { roles_recorded: 4, roles_live: 4, represented_by_teams: 4, verified: 0, unverified: 0, ambiguous: 0 },
      agents: [] as unknown[],
    };
    const four = [
      member({ memberRef: 'di_herdr_tasks:abc123#executor1', agent: 'he-exec1', sessionRecorded: { state: 'observed', value: 's-exec1' }, sessionLive: { state: 'observed', value: 's-exec1' }, paneId: { state: 'observed', value: 'w6V:p3' } }),
      member({ memberRef: 'di_herdr_tasks:abc123#lead1', agent: 'he-lead1', sessionRecorded: { state: 'observed', value: 's-lead1' }, sessionLive: { state: 'observed', value: 's-lead1' }, paneId: { state: 'observed', value: 'w6V:p2' } }),
      member({ memberRef: 'di_herdr_tasks:abc123#reviewer1', agent: 'he-rev1', sessionRecorded: { state: 'observed', value: 's-rev1' }, sessionLive: { state: 'observed', value: 's-rev1' }, paneId: { state: 'observed', value: 'w6V:p4' } }),
      member({ memberRef: 'di_herdr_tasks:abc123#supervisor', agent: 'he-sup', sessionRecorded: { state: 'observed', value: 's-sup' }, sessionLive: { state: 'observed', value: 's-sup' }, paneId: { state: 'observed', value: 'w6V:p1' } }),
    ];

    it('a healthy bound task whose runtime_agents is empty yields a binding from its roster: the first member in member-reference order, carrying the roster entry as its evidence', () => {
      const result = herdrBindingFromView(view({ ...BOUND, members: roster(...four) }), REF);
      expect(result).toEqual({
        missionRef: REF,
        agent: 'he-exec1',
        session: 's-exec1',
        paneId: 'w6V:p3',
        identity: {
          source: 'team_member',
          classification: 'present',
          reason: 'session_confirmed',
          presenceLive: true,
          chain: 'direct',
          sessionRecorded: 's-exec1',
          sessionLive: 's-exec1',
        },
      });
    });

    it('the binding it yields is one the route ACCEPTS, on the roster basis, and not on the verified one', () => {
      const result = herdrBindingFromView(view({ ...BOUND, members: roster(...four) }), REF) as HerdrRoleBinding;
      expect(establishIdentity(result)).toEqual({ basis: 'team_roster_session_confirmed' });
      expect('state' in result.identity, 'a roster entry carries no runtime-identity verdict to be mistaken for one').toBe(false);
    });

    it('the roster entry is NEVER turned into a runtime verdict: no `verified` state is fabricated for it', () => {
      const result = herdrBindingFromView(view({ ...BOUND, members: roster(...four) }), REF) as HerdrRoleBinding;
      expect(JSON.stringify(result.identity)).not.toContain('verified');
    });

    it('a confirmed DIRECT member is chosen over a child-chain one, whichever sorts first', () => {
      const child = member({ memberRef: 'a-child', agent: 'child-agent', chain: 'child', coverage: 'child', sessionRecorded: { state: 'observed', value: 's-child' }, sessionLive: { state: 'observed', value: 's-child' } });
      const result = herdrBindingFromView(view({ ...BOUND, members: roster(child, four[0]) }), REF);
      expect(result).toMatchObject({ agent: 'he-exec1', session: 's-exec1' });
    });

    it('a member the roster says is not present is not chosen while a confirmed one exists', () => {
      const replaced = member({ memberRef: 'a-replaced', agent: 'replaced-agent', classification: 'replaced', reason: 'session_changed', sessionLive: { state: 'observed', value: 'other' } });
      expect(herdrBindingFromView(view({ ...BOUND, members: roster(replaced, four[1]) }), REF)).toMatchObject({ agent: 'he-lead1' });
    });

    it('when NO member is confirmed the first is returned anyway, so the route refuses on the roster\'s real classification and reason, not a generic "no role"', () => {
      const replaced = member({ classification: 'replaced', reason: 'session_changed', sessionLive: { state: 'observed', value: 'other' } });
      const result = herdrBindingFromView(view({ ...BOUND, members: roster(replaced) }), REF) as HerdrRoleBinding;
      expect(result.identity).toMatchObject({ source: 'team_member', classification: 'replaced', reason: 'session_changed' });
      expect(establishIdentity(result)).toHaveProperty('problem');
    });

    it('a member whose recorded session is not observed has no session to bind, and is not accepted', () => {
      const result = herdrBindingFromView(view({ ...BOUND, members: roster(member({ sessionRecorded: { state: 'not_recorded' } })) }), REF) as HerdrRoleBinding;
      expect(result.session).toBeNull();
      expect(establishIdentity(result)).toHaveProperty('problem');
    });

    it('a member whose liveness the office denies is carried with presenceLive false, and is not accepted', () => {
      const result = herdrBindingFromView(view({ ...BOUND, members: roster(member({ presence: { live: false, reason: 'chain_binding_unbound:x' } })) }), REF) as HerdrRoleBinding;
      expect(result.identity).toMatchObject({ presenceLive: false });
      expect(establishIdentity(result)).toHaveProperty('problem');
    });

    it('only members of THIS Mission count: another Mission\'s roster entry is not borrowed', () => {
      const result = herdrBindingFromView(view({ ...BOUND, members: roster(member({ missionRef: 'di_herdr_tasks:other@zzz' })) }), REF);
      expect(result).toHaveProperty('unavailable');
    });

    it('a verified runtime agent is preferred over a roster member (the stronger claim comes first)', () => {
      const result = herdrBindingFromView(view({ agents: [agent({ agent: 'runtime-agent', session: 's-runtime' })], members: roster(...four) }), REF);
      expect(result).toMatchObject({ agent: 'runtime-agent', identity: { state: { known: true, value: 'verified' } } });
    });

    it('a confirmed roster member is preferred over an UNVERIFIED runtime agent', () => {
      const unverified = agent({ agent: 'runtime-agent', identityState: 'unverified', identityVerified: false, identityReason: 'session_replaced' });
      expect(herdrBindingFromView(view({ agents: [unverified], members: roster(four[0]) }), REF)).toMatchObject({ agent: 'he-exec1', identity: { source: 'team_member' } });
    });

    it('with an unverified runtime agent and no usable member, the runtime agent is returned so its real state is named', () => {
      const unverified = agent({ identityState: 'unverified', identityVerified: false, identityReason: 'session_replaced' });
      expect(herdrBindingFromView(view({ agents: [unverified] }), REF)).toMatchObject({ identity: { state: { known: true, value: 'unverified' } } });
    });
  });

  // TRUTHFULNESS: the old copy said "the catalog records no live role for this task" while the catalog had recorded four. A task is said to have no role only when NEITHER place lists one, and the
  // sentence says what each place actually reported.
  describe('when no role is listed, the reason says what each place actually reported, and never claims the catalog "records no live role"', () => {
    const reason = (over: Parameters<typeof view>[0]) => (herdrBindingFromView(view(over), REF) as { unavailable: string }).unavailable;

    it('runtime agents the catalog could NOT read are said as that, with the catalog\'s own problem code', () => {
      const words = reason({ runtimeState: 'unavailable', runtimeProblem: 'role_records_read_in_one_source_pass_only', agents: [] });
      expect(words).toContain('could not read runtime agents (unavailable: role_records_read_in_one_source_pass_only)');
    });

    it('a roster that is not a list of members is said as what it is, with its reason', () => {
      const words = reason({ agents: [], members: { state: 'projected_reason', reason: 'binding_unbound:no_match' } });
      expect(words).toContain('its team roster is projected_reason (binding_unbound:no_match)');
    });

    it('roles the producer counted as represented by a team, with no roster entry to show for them, are said to be exactly that', () => {
      const words = reason({ agents: [], counts: { represented_by_teams: 4, roles_live: 4 } });
      expect(words).toContain('The catalog counts 4 role(s) as represented by a team, but lists none of them here.');
    });

    it('none of those sentences claims the catalog records no live role', () => {
      for (const over of [
        { agents: [] },
        { reported: false, runtimeState: null, agents: [] },
        { runtimeState: 'unavailable', runtimeProblem: 'x', agents: [] },
        { agents: [], members: { state: 'unavailable', reason: 'y' } },
      ]) {
        expect(reason(over)).not.toMatch(/records no live role/);
      }
    });
  });
});
