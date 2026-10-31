/**
 * THE HERDR CONTROL'S SERVER SIDE (A1, A2, A5): the reply a button gets, from the real relay over the real route and the real Node effects, with stand-in Node APIs underneath (so no process starts and
 * no Herdr, file or terminal is touched).
 *
 * What is shown: a request is EXACTLY its four keys and gets exactly one correlated reply; an office without the Herdr flags answers "not configured" naming them; `status` runs the route's admission and
 * does NOTHING (no launcher, no focus, no window); `launch` reaches the route and its real outcome, refusal words included, is the reply (A5); one request at a time, one window per click; and nothing a log
 * line carries can name a path, session, pane or workspace.
 *
 * WHAT THIS DOES NOT ESTABLISH: anything about a real Herdr server, a real Terminal window, or a person typing. Those are the Supervisor's native proof.
 */
import { describe, expect, it } from 'vitest';

import type { DiHerdrReply } from '../../core/src/messages.js';
import type { NodeLaunchApis } from '../src/di/herdrLaunch.js';
import {
  DiHerdrRequests,
  invalidRequestReply,
  MIN_LAUNCH_INTERVAL_MS,
  notConfiguredReply,
  parseHerdrRequest,
} from '../src/di/herdrRequests.js';
import type { HerdrConfig, LauncherDirFs } from '../src/di/herdrTarget.js';
import { HERDR_FLAGS } from '../src/di/herdrTarget.js';
import type { OfficeView } from '../src/di/officeView.js';

const REF = 'di_herdr_tasks:task-1@abc123';
const CONFIG: HerdrConfig = { bin: '/opt/proof/bin/herdr', home: '/opt/proof/home', socketPath: '/opt/proof/home/.config/herdr/herdr.sock' };
const LAUNCHER_DIR = '/opt/proof/state/herdr-launchers';
const OWNER = 501;
const OK_DIR = { isDirectory: () => true, isSymbolicLink: () => false, mode: 0o40700, uid: OWNER };

const request = (over: Record<string, unknown> = {}) => ({ type: 'diHerdrRequest', seq: 4, op: 'status', missionRef: REF, ...over });

function agent(over: Record<string, unknown> = {}) {
  return {
    agentRef: 'src:abc123#executor1',
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
function view(over: { mission?: Record<string, unknown>; agents?: unknown[]; members?: unknown[]; live?: boolean } = {}): OfficeView {
  const mission = {
    missionRef: REF,
    sourceKind: 'herdr_task',
    standing: 'active',
    standingKnown: true,
    lifecycleState: 'ACTIVE',
    members: { state: 'observed', value: over.members ?? [] },
    ...over.mission,
  };
  return {
    connection: { state: 'answered' },
    live: over.live ?? true,
    notLiveReason: null,
    missions: { state: 'observed', value: [mission] },
    runtimeAgents: { reported: true, state: 'observed', problem: null, counts: null, agents: over.agents ?? [agent()] },
  } as unknown as OfficeView;
}
/** A roster entry for a healthy session-confirmed role, as the office presents it: the shape measured on a real contained catalog. */
function rosterMember(over: Record<string, unknown> = {}) {
  return {
    coverage: 'direct',
    memberRef: 'src:abc123#executor1',
    missionRef: REF,
    agent: 'he-exec1',
    chain: 'direct',
    classification: 'present',
    reason: 'session_confirmed',
    presence: { live: true, reason: null },
    paneId: { state: 'observed', value: 'w6V:p3' },
    sessionRecorded: { state: 'observed', value: 'sess-exec1' },
    sessionLive: { state: 'observed', value: 'sess-exec1' },
    ...over,
  };
}

interface Harness {
  relay: DiHerdrRequests;
  replies: DiHerdrReply[];
  logs: string[];
  /** Every Herdr command and opener the stand-in APIs were asked to run, in order. */
  commands: string[][];
  writes: string[];
  mkdirs: string[];
  /** Every path the containment guard was asked about, in order (the guard is the office's, passed in; here a recorder that can be told to refuse). */
  contained: string[];
  clock: { now: number };
  ask: (msg: Record<string, unknown>) => Promise<DiHerdrReply[]>;
}
function harness(
  opts: {
    config?: HerdrConfig | null;
    view?: () => OfficeView;
    listing?: string;
    focusFails?: boolean;
    getFocused?: boolean;
    openStatus?: number;
    dirStat?: ReturnType<LauncherDirFs['lstat']>;
    getViewThrows?: boolean;
    installed?: boolean;
    snapshot?: string;
    /** Refuse containment for every path (the guard throws), or only for the path ending in this. */
    refuseContain?: true | string;
  } = {},
): Harness {
  const commands: string[][] = [];
  const writes: string[] = [];
  const mkdirs: string[] = [];
  const contained: string[] = [];
  const replies: DiHerdrReply[] = [];
  const logs: string[] = [];
  const clock = { now: 1_000_000 };
  const listing = opts.listing ?? JSON.stringify({ result: { agents: [{ name: 'he-exec1', agent_session: { kind: 'id', value: 'sess-exec1' }, pane_id: 'w6V:p3', workspace_id: 'w6V' }] } });
  // the area measured on the live herd: the window the launcher will require is 26 + 162 + 2 by 1 + 46 + 2
  const snapshot =
    opts.snapshot ??
    JSON.stringify({ result: { snapshot: { layouts: [{ workspace_id: 'w6V', tab_id: 'w6V:t1', area: { x: 26, y: 1, width: 162, height: 46 } }] } } });
  const apis: NodeLaunchApis = {
    execFile: (file, args, _options, callback) => {
      commands.push([file, ...args]);
      if (args[0] === 'agent') return callback(null, listing, '');
      if (args[0] === 'api') return callback(null, snapshot, '');
      if (args[1] === 'focus') return opts.focusFails ? callback(new Error('exit status 1'), '', '') : callback(null, '{}', '');
      callback(null, JSON.stringify({ result: { workspace: { workspace_id: args[2], focused: opts.getFocused ?? true } } }), '');
      return undefined;
    },
    spawn: (file, args) => {
      commands.push([file, ...args]);
      return {
        once: (event: string, listener: (...a: unknown[]) => void) => {
          if (event === 'exit') queueMicrotask(() => listener(opts.openStatus ?? 0, null));
        },
        unref: () => undefined,
      };
    },
    writeFile: async (filePath) => {
      writes.push(filePath);
    },
    randomBytes: () => ({ toString: () => 'a1b2c3d4e5f6a7b8c9d0e1f2' }),
    env: {},
    pathExists: async () => opts.installed ?? true,
  };
  let dir = opts.dirStat === undefined ? OK_DIR : opts.dirStat;
  const relay = new DiHerdrRequests({
    config: opts.config === undefined ? CONFIG : opts.config,
    launcherDir: LAUNCHER_DIR,
    getView: () => {
      if (opts.getViewThrows) throw new Error('the view is gone');
      return (opts.view ?? (() => view()))();
    },
    apis,
    dirFs: {
      lstat: () => dir,
      mkdir: (p) => {
        mkdirs.push(p);
        dir = OK_DIR;
      },
      uid: () => OWNER,
    },
    contain: (target) => {
      contained.push(target);
      if (opts.refuseContain === true || (typeof opts.refuseContain === 'string' && target.endsWith(opts.refuseContain))) {
        throw new Error(`DI state write refused: ${target} resolves outside the DI state root`);
      }
    },
    nowMs: () => clock.now,
    log: (line) => logs.push(line),
  });
  return {
    relay,
    replies,
    logs,
    commands,
    writes,
    mkdirs,
    contained,
    clock,
    ask: async (msg) => {
      const out: DiHerdrReply[] = [];
      await relay.handle(msg, (m) => out.push(m as unknown as DiHerdrReply));
      replies.push(...out);
      return out;
    },
  };
}

describe('a request is EXACTLY type, seq, op and missionRef, and every correlatable one gets one reply', () => {
  it('parses the two operations', () => {
    expect(parseHerdrRequest(request())).toEqual({ ok: true, seq: 4, op: 'status', missionRef: REF });
    expect(parseHerdrRequest(request({ op: 'launch' }))).toEqual({ ok: true, seq: 4, op: 'launch', missionRef: REF });
  });

  it.each([
    ['an extra key', request({ extra: 1 })],
    ['a pane id smuggled in', request({ paneId: 'w6V:p1' })],
    ['a workspace id smuggled in', request({ workspaceId: 'w6V' })],
    ['a missing missionRef', (() => { const { missionRef: _m, ...rest } = request(); return rest; })()],
    ['a non-string missionRef', request({ missionRef: 5 })],
    ['an empty missionRef', request({ missionRef: '' })],
    ['a missionRef with a control character', request({ missionRef: 'a\u0000b' })],
    ['a missionRef over the bound', request({ missionRef: 'x'.repeat(105) })],
  ])('%s is refused', (_name, msg) => {
    const parsed = parseHerdrRequest(msg as Record<string, unknown>);
    expect(parsed.ok).toBe(false);
    expect(invalidRequestReply(parsed as Extract<typeof parsed, { ok: false }>)).toMatchObject({ type: 'diHerdrReply', seq: 4, op: 'status', state: 'refused', problem: 'request_invalid', answer: null });
  });

  it.each([
    ['an op that is not one of the two (no op that types, prompts or sends keys exists)', request({ op: 'sendKeys' })],
    ['a negative seq', request({ seq: -1 })],
    ['a fractional seq', request({ seq: 1.5 })],
    ['a seq over the bound', request({ seq: 2147483648 })],
    ['a wrong type', request({ type: 'diDeliveryRequest' })],
  ])('%s is outside the correlation domain: refused with NO reply and no seq or op invented', (_name, msg) => {
    const parsed = parseHerdrRequest(msg);
    expect(parsed.ok).toBe(false);
    expect(invalidRequestReply(parsed as Extract<typeof parsed, { ok: false }>)).toBeNull();
  });

  it('through the relay: an invalid correlatable request gets one refusal, an uncorrelatable one gets nothing', async () => {
    const h = harness();
    expect(await h.ask(request({ extra: 1 }))).toHaveLength(1);
    expect(await h.ask(request({ op: 'nope' }))).toEqual([]);
    expect(h.commands).toEqual([]);
  });
});

describe('without the Herdr flags the control is genuinely unavailable, and says exactly what it is waiting for (A2)', () => {
  it.each(['status', 'launch'])('%s answers "unavailable" naming all three flags, and attempts nothing', async (op) => {
    const h = harness({ config: null });
    const [out, ...more] = await h.ask(request({ op }));
    expect(more).toEqual([]);
    expect(out).toMatchObject({ type: 'diHerdrReply', seq: 4, op, state: 'unavailable', problem: 'herdr_not_configured', answer: null });
    for (const flag of HERDR_FLAGS) expect(out.reason).toContain(flag);
    expect(h.commands).toEqual([]);
    expect(h.writes).toEqual([]);
    expect(h.mkdirs).toEqual([]);
  });

  it('the not-configured reply is built the same way the dispatcher builds it', () => {
    expect(notConfiguredReply({ ok: true, seq: 9, op: 'launch', missionRef: REF })).toMatchObject({ seq: 9, op: 'launch', state: 'unavailable', problem: 'herdr_not_configured' });
  });
});

describe('`status` runs the route\'s admission and does NOTHING (A2)', () => {
  it('an available status names the workspace a launch would target, the pane it matched and the window size a launch would require, and starts only the two read-only reads', async () => {
    const h = harness();
    const [out] = await h.ask(request());
    expect(out).toEqual({
      type: 'diHerdrReply',
      seq: 4,
      op: 'status',
      state: 'ok',
      problem: null,
      reason: null,
      answer: {
        agent: 'he-exec1',
        identityBasis: 'runtime_identity_verified',
        workspaceId: 'w6V',
        windowMinCols: 190,
        windowMinRows: 49,
        workspaceFocus: 'not_performed',
        matchedPaneId: 'w6V:p3',
        paneFocus: 'not_performed',
        terminal: 'terminal',
        terminalLabel: 'Terminal',
        terminalState: null,
      },
    });
    // admission reads Herdr's read-only agent list and its read-only snapshot (the window size), and nothing else
    expect(h.commands).toEqual([
      ['/opt/proof/bin/herdr', 'agent', 'list'],
      ['/opt/proof/bin/herdr', 'api', 'snapshot'],
    ]);
    expect(h.writes, 'no launcher').toEqual([]);
    expect(h.mkdirs, 'no directory').toEqual([]);
  });

  it('a status never creates the launcher directory, even when it does not exist yet', async () => {
    const h = harness({ dirStat: null });
    const [out] = await h.ask(request());
    expect(out.state).toBe('ok');
    expect(h.mkdirs).toEqual([]);
  });

  it('reads the view at the moment of each request: a task that stops being active is unavailable on the next status', async () => {
    let current = view();
    const h = harness({ view: () => current });
    expect((await h.ask(request()))[0].state).toBe('ok');
    current = view({ mission: { standing: 'terminal', lifecycleState: 'ABORTED' } });
    const [out] = await h.ask(request());
    expect(out).toMatchObject({ state: 'refused', problem: 'no_binding' });
    expect(out.reason).toContain('ABORTED (terminal), not active');
  });
});

// THE SHAPE THIS GATE IS PROVEN AGAINST: a healthy bound Herdr task whose runtime_agents is EMPTY (the producer moves roles a team already holds out of it) and whose roles are the Mission's team roster.
describe('the team-represented shape, through the whole relay and route: runtime_agents is empty and the roster holds the roles', () => {
  const bound = (members: unknown[] = [rosterMember()]) => () => view({ agents: [], members });

  it('status is available on the roster basis: it names that basis, the workspace from Herdr\'s live row, and starts only the two read-only reads', async () => {
    const h = harness({ view: bound() });
    const [out] = await h.ask(request());
    expect(out).toMatchObject({ state: 'ok', answer: { agent: 'he-exec1', identityBasis: 'team_roster_session_confirmed', workspaceId: 'w6V', matchedPaneId: 'w6V:p3', windowMinCols: 190, windowMinRows: 49, workspaceFocus: 'not_performed' } });
    expect(h.commands).toEqual([
      ['/opt/proof/bin/herdr', 'agent', 'list'],
      ['/opt/proof/bin/herdr', 'api', 'snapshot'],
    ]);
    expect(h.writes).toEqual([]);
  });

  it('launch proceeds in the same order as for a runtime role, and reports the roster basis', async () => {
    const h = harness({ view: bound() });
    const [out] = await h.ask(request({ op: 'launch' }));
    expect(out).toMatchObject({ state: 'ok', answer: { identityBasis: 'team_roster_session_confirmed', workspaceFocus: 'focused', terminalState: 'opened' } });
    expect(h.commands.map((c) => c.slice(0, 3).join(' '))).toEqual([
      '/opt/proof/bin/herdr agent list',
      '/opt/proof/bin/herdr api snapshot',
      '/opt/proof/bin/herdr workspace focus',
      '/opt/proof/bin/herdr workspace get',
      '/usr/bin/open -a /System/Applications/Utilities/Terminal.app',
    ]);
  });

  it('the independent live-list check still applies: a roster role whose session Herdr does not list is refused with nothing done', async () => {
    const h = harness({ view: bound(), listing: JSON.stringify({ result: { agents: [] } }) });
    const [out] = await h.ask(request({ op: 'launch' }));
    expect(out).toMatchObject({ state: 'refused', problem: 'not_live', answer: null });
    expect(h.writes).toEqual([]);
    expect(h.commands.filter((c) => c[1] === 'workspace' || c[0] === '/usr/bin/open')).toEqual([]);
  });

  it.each([
    ['replaced', { classification: 'replaced', reason: 'session_changed', sessionLive: { state: 'observed', value: 'other' } }, 'replaced in the team roster (session_changed), not confirmed present'],
    ['not session_confirmed', { reason: 'session_unconfirmed' }, 'present in the team roster (session_unconfirmed), not confirmed present'],
    ['a live session that differs', { sessionLive: { state: 'observed', value: 'other' } }, 'a different live session in the team roster'],
    ['not live in the office', { presence: { live: false, reason: 'chain_binding_unbound:x' } }, 'the office does not count it live'],
    ['a child chain', { chain: 'child', coverage: 'child' }, 'on the child roster chain'],
  ])('a roster role that is %s is refused as identity_not_verified with the roster\'s own words, and nothing is written, focused or opened', async (_name, over, words) => {
    const h = harness({ view: bound([rosterMember(over)]) });
    const [out] = await h.ask(request({ op: 'launch' }));
    expect(out).toMatchObject({ state: 'refused', problem: 'identity_not_verified', answer: null });
    expect(out.reason).toContain(words);
    expect(out.reason).not.toMatch(/\bverified\b/);
    expect(h.writes).toEqual([]);
    expect(h.commands).toEqual([]);
  });

  it('a healthy-looking task with NO role listed anywhere is refused with what each place reported, never "records no live role"', async () => {
    const h = harness({ view: bound([]) });
    const [out] = await h.ask(request());
    expect(out).toMatchObject({ state: 'refused', problem: 'no_binding' });
    expect(out.reason).toContain('lists no runtime agent for this task, and its team roster lists no member');
    expect(out.reason).not.toContain('records no live role');
  });
});

describe('`launch` reaches the route, and its real outcome is the reply', () => {
  it('a launch writes the launcher, focuses and reads back the workspace, then asks the opener for the Terminal application, in that order', async () => {
    const h = harness();
    const [out] = await h.ask(request({ op: 'launch' }));
    expect(h.commands).toEqual([
      ['/opt/proof/bin/herdr', 'agent', 'list'],
      ['/opt/proof/bin/herdr', 'api', 'snapshot'],
      ['/opt/proof/bin/herdr', 'workspace', 'focus', 'w6V'],
      ['/opt/proof/bin/herdr', 'workspace', 'get', 'w6V'],
      ['/usr/bin/open', '-a', '/System/Applications/Utilities/Terminal.app', `${LAUNCHER_DIR}/herdr-attach-a1b2c3d4e5f6a7b8c9d0e1f2.command`],
    ]);
    expect(h.writes).toEqual([`${LAUNCHER_DIR}/herdr-attach-a1b2c3d4e5f6a7b8c9d0e1f2.command`]);
    expect(out).toMatchObject({
      state: 'ok',
      answer: {
        workspaceId: 'w6V',
        windowMinCols: 190,
        windowMinRows: 49,
        workspaceFocus: 'focused',
        matchedPaneId: 'w6V:p3',
        paneFocus: 'not_performed',
        terminalState: 'opened',
        terminalLabel: 'Terminal',
      },
    });
  });

  it('the reply states a focus and an opened terminal as separate facts, and claims no attach and no keyboard anywhere', async () => {
    const h = harness();
    const [out] = await h.ask(request({ op: 'launch' }));
    expect(JSON.stringify(out)).not.toMatch(/attach|keyboard|typing|typed/i);
  });

  it('the launcher directory is created owner-only (when missing) only AFTER admission passed', async () => {
    const refused = harness({ dirStat: null, view: () => view({ live: false }) });
    await refused.ask(request({ op: 'launch' }));
    expect(refused.mkdirs, 'an admission refusal creates nothing').toEqual([]);
    const launched = harness({ dirStat: null });
    expect((await launched.ask(request({ op: 'launch' })))[0].state).toBe('ok');
    expect(launched.mkdirs).toEqual([LAUNCHER_DIR]);
  });

  it.each([
    ['a task that is not active', { view: () => view({ mission: { standing: 'terminal', lifecycleState: 'ABORTED' } }) }, 'no_binding', 'ABORTED (terminal), not active'],
    ['an unverified role', { view: () => view({ agents: [agent({ identityState: 'unverified', identityVerified: false, identityReason: 'session_replaced' })] }) }, 'identity_not_verified', 'session_replaced'],
    ['a session that is not live', { listing: JSON.stringify({ result: { agents: [] } }) }, 'not_live', 'not in Herdr\'s live agent list'],
    ['an unreadable list', { listing: 'not json' }, 'listing_unreadable', 'not one JSON document'],
    ['a live row with no workspace', { listing: JSON.stringify({ result: { agents: [{ agent_session: { value: 'sess-exec1' }, pane_id: 'p', workspace_id: null }] } }) }, 'workspace_unknown', 'names no usable workspace'],
    ['a terminal that is not installed', { installed: false }, 'terminal_unavailable', 'is not installed at /System/Applications/Utilities/Terminal.app'],
  ])('with %s the reply is the route\'s refusal with its real detail, and nothing was written, focused or opened', async (_name, opts, problem, words) => {
    const h = harness(opts);
    const [out] = await h.ask(request({ op: 'launch' }));
    expect(out).toMatchObject({ state: 'refused', problem, answer: null });
    expect(out.reason).toContain(words);
    expect(out.reason).toContain('Nothing was opened');
    expect(h.writes).toEqual([]);
    expect(h.commands.filter((c) => c[1] === 'workspace' || c[0] === '/usr/bin/open')).toEqual([]);
  });

  it('a focus that fails: focus_not_established, the launcher named as written, the terminal NEVER opened', async () => {
    const h = harness({ focusFails: true });
    const [out] = await h.ask(request({ op: 'launch' }));
    expect(out).toMatchObject({ state: 'refused', problem: 'focus_not_established', answer: null });
    expect(out.reason).toContain('Nothing was opened');
    expect(out.reason).toContain('exit status 1');
    expect(out.reason).toContain('herdr-attach-a1b2c3d4e5f6a7b8c9d0e1f2.command');
    expect(h.commands.some((c) => c[0] === '/usr/bin/open')).toBe(false);
  });

  it('a focus Herdr\'s read-back does not confirm: focus_not_established, "the view may have moved", the terminal NEVER opened', async () => {
    const h = harness({ getFocused: false });
    const [out] = await h.ask(request({ op: 'launch' }));
    expect(out).toMatchObject({ state: 'refused', problem: 'focus_not_established' });
    expect(out.reason).toContain('the view may have moved');
    expect(h.commands.some((c) => c[0] === '/usr/bin/open')).toBe(false);
  });

  it('an opener that fails after a focus: launch_failed, and the reply says the view HAS moved and nothing opened', async () => {
    const h = harness({ openStatus: 1 });
    const [out] = await h.ask(request({ op: 'launch' }));
    expect(out).toMatchObject({ state: 'refused', problem: 'launch_failed' });
    expect(out.reason).toContain('the opener exited with status 1');
    expect(out.reason).toContain('the view HAS moved and nothing opened');
  });

  it('a launcher directory that is open to other users is refused before any route step, with nothing created', async () => {
    const h = harness({ dirStat: { isDirectory: () => true, isSymbolicLink: () => false, mode: 0o40777, uid: OWNER } });
    const [out] = await h.ask(request({ op: 'launch' }));
    expect(out).toMatchObject({ state: 'refused', problem: 'launcher_directory_unusable' });
    expect(out.reason).toContain('open to other users');
    expect(h.commands).toEqual([]);
    expect(h.writes).toEqual([]);
  });

  it('an unexpected error inside the route is answered as a refusal that says what is not known, never thrown', async () => {
    const h = harness({ getViewThrows: true });
    const [out] = await h.ask(request({ op: 'launch' }));
    expect(out).toMatchObject({ state: 'refused', problem: 'herdr_route_failed', answer: null });
    expect(out.reason).toContain('the view is gone');
    expect(out.reason).toContain('not known');
    const status = await h.ask(request({ seq: 5 }));
    expect(status[0].reason).toContain('Nothing was changed');
  });
});

describe('the window size (A12): a launch whose size cannot be established is refused as geometry_unknown, with nothing done, and the reply says so', () => {
  it.each([
    ['a snapshot that is not JSON', 'herdr: no server', 'not one JSON document'],
    ['a snapshot with no layout for the workspace', JSON.stringify({ result: { snapshot: { layouts: [] } } }), 'no layout for workspace w6V'],
    ['a snapshot whose area is unusable', JSON.stringify({ result: { snapshot: { layouts: [{ workspace_id: 'w6V', area: { x: 26, y: 1, width: 'wide', height: 46 } }] } } }), 'not whole numbers within bounds'],
  ])('with %s a launch is refused, and so is a status', async (_name, snapshot, words) => {
    const h = harness({ snapshot });
    const [out] = await h.ask(request({ op: 'launch' }));
    expect(out).toMatchObject({ state: 'refused', problem: 'geometry_unknown', answer: null });
    expect(out.reason).toContain(words);
    expect(out.reason).toContain('Nothing was opened');
    expect(h.writes).toEqual([]);
    expect(h.commands.filter((c) => c[1] === 'workspace' || c[0] === '/usr/bin/open')).toEqual([]);
    h.clock.now += MIN_LAUNCH_INTERVAL_MS;
    expect((await h.ask(request({ seq: 5 })))[0]).toMatchObject({ state: 'refused', problem: 'geometry_unknown' });
  });

  it('an answer carries the window size and no claim that the window reached it: a launch reports what was ASKED of the window, not what a terminal did', async () => {
    const h = harness();
    const [out] = await h.ask(request({ op: 'launch' }));
    expect(out.answer).toMatchObject({ windowMinCols: 190, windowMinRows: 49, terminalState: 'opened' });
    expect(JSON.stringify(out)).not.toMatch(/resized|attached|keyboard/i);
  });
});

/**
 * PHYSICAL CONTAINMENT in the relay (review finding 2): the office's existing guard is asked about the launcher DIRECTORY before it is looked at or made, and about the launcher FILE immediately before it is
 * written. The guard here is a recorder that can refuse; its real behaviour against real symbolic links is proved in diHerdrTarget.test.ts. WHAT THIS ESTABLISHES: the relay asks, in that order, and a refusal
 * stops the launch before anything is created or written.
 */
describe('the relay asks the office\'s containment guard about the directory and the file, and a refusal stops the launch before anything is created or written', () => {
  it('a launch asks about the directory (before creating it), then about the directory again (after), then about the exact file it is about to write', async () => {
    const h = harness({ dirStat: null });
    expect((await h.ask(request({ op: 'launch' })))[0].state).toBe('ok');
    expect(h.contained.at(-1)).toBe(`${LAUNCHER_DIR}/herdr-attach-a1b2c3d4e5f6a7b8c9d0e1f2.command`);
    expect(h.contained.filter((p) => p === LAUNCHER_DIR).length).toBeGreaterThanOrEqual(2);
    expect(h.writes).toEqual([`${LAUNCHER_DIR}/herdr-attach-a1b2c3d4e5f6a7b8c9d0e1f2.command`]);
  });

  it('a status asks about the directory (so a redirected one is reported) and never about a file: it writes nothing', async () => {
    const h = harness();
    await h.ask(request());
    expect(h.contained).toEqual([LAUNCHER_DIR]);
  });

  it('a directory the guard refuses is launcher_directory_unusable and nothing was created, listed with Herdr, written, focused or opened', async () => {
    const h = harness({ dirStat: null, refuseContain: true });
    const [out] = await h.ask(request({ op: 'launch' }));
    expect(out).toMatchObject({ state: 'refused', problem: 'launcher_directory_unusable', answer: null });
    expect(out.reason).toContain('refused by the state-root containment guard');
    expect(out.reason).toContain('outside the DI state root');
    expect(h.mkdirs).toEqual([]);
    expect(h.writes).toEqual([]);
    expect(h.commands).toEqual([]);
  });

  it('a status on a redirected directory says so too, and starts nothing', async () => {
    const h = harness({ refuseContain: true });
    const [out] = await h.ask(request());
    expect(out).toMatchObject({ state: 'refused', problem: 'launcher_directory_unusable' });
    expect(h.commands).toEqual([]);
  });

  it('a LAUNCHER FILE the guard refuses (a symbolic link planted at its name, say) is never written, and the view is never moved and no window opened', async () => {
    const h = harness({ refuseContain: '.command' });
    const [out] = await h.ask(request({ op: 'launch' }));
    expect(out.state).toBe('refused');
    expect(out.reason).toContain('outside the DI state root');
    expect(h.writes, 'the refused file is not written').toEqual([]);
    expect(h.commands.filter((c) => c[1] === 'workspace' || c[0] === '/usr/bin/open')).toEqual([]);
  });
});

describe('one request at a time, and one window per click', () => {
  it('a second request while one is running is refused as busy, with nothing done for it', async () => {
    const h = harness();
    const first = h.relay.handle(request({ op: 'launch' }), () => undefined);
    const out: DiHerdrReply[] = [];
    await h.relay.handle(request({ seq: 5, op: 'status' }), (m) => out.push(m as unknown as DiHerdrReply));
    await first;
    expect(out).toHaveLength(1);
    expect(out[0]).toMatchObject({ seq: 5, state: 'refused', problem: 'office_busy' });
    expect(h.commands.filter((c) => c[0] === '/usr/bin/open')).toHaveLength(1);
  });

  it('a second launch inside the interval is refused, not queued, and a status in that time is still answered', async () => {
    const h = harness();
    expect((await h.ask(request({ op: 'launch' })))[0].state).toBe('ok');
    h.clock.now += MIN_LAUNCH_INTERVAL_MS - 1;
    const [again] = await h.ask(request({ seq: 6, op: 'launch' }));
    expect(again).toMatchObject({ state: 'refused', problem: 'launch_rate_limited' });
    expect(h.commands.filter((c) => c[0] === '/usr/bin/open')).toHaveLength(1);
    expect((await h.ask(request({ seq: 7 })))[0].state).toBe('ok');
    h.clock.now += 1;
    expect((await h.ask(request({ seq: 8, op: 'launch' })))[0].state).toBe('ok');
    expect(h.commands.filter((c) => c[0] === '/usr/bin/open')).toHaveLength(2);
  });

  it('an admission refusal does not count as a launch: the person can click again at once', async () => {
    let current = view({ live: false });
    const h = harness({ view: () => current });
    expect((await h.ask(request({ op: 'launch' })))[0].state).toBe('refused');
    current = view();
    expect((await h.ask(request({ seq: 6, op: 'launch' })))[0].state).toBe('ok');
  });
});

describe('the log carries codes only: never a path, a session, a pane, a workspace or a Mission', () => {
  it('after a successful launch, a refusal and a not-configured office', async () => {
    const h = harness();
    await h.ask(request({ op: 'launch' }));
    h.clock.now += MIN_LAUNCH_INTERVAL_MS;
    await h.ask(request({ seq: 6, op: 'launch', missionRef: 'x' }));
    await h.ask(request({ seq: 7, extra: 1 }));
    const text = h.logs.join('\n');
    for (const secret of ['/opt/proof', 'sess-exec1', 'w6V', REF, 'he-exec1', 'herdr-attach', 'abc123']) expect(text).not.toContain(secret);
    expect(h.logs.length).toBeGreaterThan(0);
    for (const line of h.logs) expect(line).toMatch(/^\[Pixel Agents DI\] herdr request/);
  });
});
