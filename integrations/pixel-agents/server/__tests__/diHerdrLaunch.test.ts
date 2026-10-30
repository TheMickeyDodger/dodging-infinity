/**
 * THE HERDR LAUNCH ROUTE (Stage 4 item D1/D2/L6, the attach proof's prerequisite): the office opens a REAL macOS Terminal window attached to one Mission's herd, and SENDS NO INPUT.
 *
 * Every effect is INJECTED here: no test reads a real catalog, runs Herdr, writes a launcher or opens Terminal. What each block states is a requirement of the owner rule (the human types in the OS
 * terminal; the app sends no prompts, keystrokes or commands) and of the design's binding rule (bind on the recorded session and re-resolve the pane AT USE; a cached pane id is unsafe).
 *
 * WHAT THIS DOES NOT ESTABLISH: that the attach lands anywhere determinate, that a person can type in it, or that the app sends nothing while they do. Those are the runtime proof, owed after this. The
 * Herdr server is named by the target's socket path (the disposable one, by construction); how the app finds the server, workspace and agent of a real Mission is NOT solved here and stays owed.
 *
 * R3 readiness under the owner's Q4 answer: every Herdr command the route runs, the listing and the launcher alike, runs under EXACTLY Q4's environment: XDG_CONFIG_HOME, XDG_STATE_HOME,
 * HERDR_SESSION and HERDR_CLIENT_SOCKET_PATH removed; HOME and HERDR_SOCKET_PATH set to the target's. An inherited HERDR_SOCKET_PATH (every Herdr pane carries the live one) would otherwise
 * point the route at the LIVE server. And the attach is plain `herdr` on that server: `herdr session attach <name>` names a separate named-session server Q4's start never creates.
 */
import { describe, expect, it } from 'vitest';

import type { RuntimeIdentityState } from '../src/di/catalogContract.js';
import type { HerdrAttachTarget, HerdrLaunchEffects, HerdrRoleBinding, NodeLaunchApis } from '../src/di/herdrLaunch.js';
import {
  createHerdrLauncher,
  HERDR_LISTING_MAX_BYTES,
  HERDR_LISTING_TIMEOUT_MS,
  herdrLauncherText,
  nodeHerdrLaunchEffects,
} from '../src/di/herdrLaunch.js';

const TARGET: HerdrAttachTarget = {
  herdrBin: '/opt/proof/bin/herdr',
  home: '/opt/proof/home',
  socketPath: '/opt/proof/home/.config/herdr/herdr.sock',
  launcherDir: '/opt/proof/launch',
};
/** Q4's environment rule, stated here and never read from the product under test. */
const Q4_SET = {
  HOME: '/opt/proof/home',
  HERDR_SOCKET_PATH: '/opt/proof/home/.config/herdr/herdr.sock',
};
const Q4_UNSET = ['XDG_CONFIG_HOME', 'XDG_STATE_HOME', 'HERDR_SESSION', 'HERDR_CLIENT_SOCKET_PATH'];
const LAUNCHER = '/opt/proof/launch/herdr-attach-a1b2c3d4e5f6a7b8.command';
const VERIFIED = { state: { known: true as const, value: 'verified' as RuntimeIdentityState }, reason: 'present' };

function binding(over: Partial<HerdrRoleBinding> = {}): HerdrRoleBinding {
  return { missionRef: 'herd:task-1@root-1', agent: 'proof-agent', session: 'sess-1', paneId: 'w1:p1', identity: VERIFIED, ...over };
}
function row(session: string | null, pane = 'w2:p7', workspace = 'w2', name = 'proof-agent'): Record<string, unknown> {
  return { name, agent: 'claude', agent_status: 'idle', agent_session: session === null ? null : { kind: 'id', value: session }, pane_id: pane, workspace_id: workspace };
}
function listing(rows: unknown[]): string {
  return JSON.stringify({ result: { agents: rows } });
}

interface Calls {
  readBinding: number;
  listing: Array<[string, string[], unknown]>;
  writes: Array<[string, string, { flag: string; mode: number }]>;
  opens: Array<[string, string[]]>;
}
function fake(
  opts: {
    binding?: () => HerdrRoleBinding | null;
    stdout?: () => string;
    listingThrows?: boolean;
    writeThrows?: boolean;
    openThrows?: boolean;
    name?: string;
  } = {},
): { effects: HerdrLaunchEffects; calls: Calls } {
  const calls: Calls = { readBinding: 0, listing: [], writes: [], opens: [] };
  const effects: HerdrLaunchEffects = {
    readBinding: async () => {
      calls.readBinding++;
      return (opts.binding ?? (() => binding()))();
    },
    runListing: async (file, args, env) => {
      calls.listing.push([file, [...args], structuredClone(env)]);
      if (opts.listingThrows) throw new Error('the listing exited 1');
      return (opts.stdout ?? (() => listing([row('sess-1')])))();
    },
    writeLauncher: async (filePath, text, options) => {
      calls.writes.push([filePath, text, { ...options }]);
      if (opts.writeThrows) throw new Error('EEXIST');
    },
    openInTerminal: async (file, args) => {
      calls.opens.push([file, [...args]]);
      if (opts.openThrows) throw new Error('open exited 1');
    },
    launcherName: () => opts.name ?? 'a1b2c3d4e5f6a7b8',
  };
  return { effects, calls };
}
const nothingDone = (calls: Calls) => {
  expect(calls.writes, 'no launcher is written').toEqual([]);
  expect(calls.opens, 'nothing is opened').toEqual([]);
};

describe('the launch opens Terminal on an executable file and types nothing (the owner rule: no app-generated command into the terminal)', () => {
  it('the only process it opens is /usr/bin/open -a Terminal <the launcher>, once: no shell, no AppleScript', async () => {
    const { effects, calls } = fake();
    const result = await createHerdrLauncher(TARGET, effects).launch();
    expect(result.launched).toBe(true);
    expect(calls.opens).toEqual([['/usr/bin/open', ['-a', 'Terminal', LAUNCHER]]]);
  });

  it("the launcher holds exactly one command, the exec of PLAIN herdr under Q4's environment (no --session, no session attach), and it is the file written", async () => {
    expect(herdrLauncherText(TARGET)).toBe(
      "#!/bin/sh\nexec /usr/bin/env -u XDG_CONFIG_HOME -u XDG_STATE_HOME -u HERDR_SESSION -u HERDR_CLIENT_SOCKET_PATH HOME='/opt/proof/home' HERDR_SOCKET_PATH='/opt/proof/home/.config/herdr/herdr.sock' '/opt/proof/bin/herdr'\n",
    );
    expect(herdrLauncherText(TARGET)).not.toMatch(/session attach|--session/);
    const { effects, calls } = fake();
    await createHerdrLauncher(TARGET, effects).launch();
    expect(calls.writes.map(([p, text]) => [p, text])).toEqual([[LAUNCHER, herdrLauncherText(TARGET)]]);
  });

  it('the launcher is created exclusively (never over an existing file) and owner-only', async () => {
    const { effects, calls } = fake();
    await createHerdrLauncher(TARGET, effects).launch();
    expect(calls.writes.map(([, , options]) => options)).toEqual([{ flag: 'wx', mode: 0o700 }]);
  });

  it("the at-use read is Herdr's read-only agent list, run under Q4's environment: the target's HOME and socket SET, the four overriding variables REMOVED", async () => {
    const { effects, calls } = fake();
    await createHerdrLauncher(TARGET, effects).launch();
    expect(calls.listing).toEqual([
      ['/opt/proof/bin/herdr', ['agent', 'list'], { set: Q4_SET, unset: Q4_UNSET }],
    ]);
  });
});

describe('the binding is read AT USE, never cached', () => {
  it("each launch reads the Mission's binding afresh: a binding that changes after the launcher is made is the one used", async () => {
    let current = binding({ session: 'sess-old' });
    const { effects, calls } = fake({
      binding: () => current,
      stdout: () => listing([row('sess-old', 'w1:p1', 'w1'), row('sess-new', 'w3:p2', 'w3', 'proof-agent-2')]),
    });
    const launcher = createHerdrLauncher(TARGET, effects);
    current = binding({ session: 'sess-new', agent: 'proof-agent-2' });
    expect(await launcher.launch()).toMatchObject({ launched: true, session: 'sess-new', paneId: 'w3:p2' });
    current = binding({ session: 'sess-old' });
    expect(await launcher.launch()).toMatchObject({ launched: true, session: 'sess-old', paneId: 'w1:p1' });
    expect(calls.readBinding).toBe(2);
  });

  it("each launch reads Herdr's live list afresh: a session that moved pane between launches is found where it is now", async () => {
    let now = listing([row('sess-1', 'w1:p1', 'w1')]);
    const { effects, calls } = fake({ stdout: () => now });
    const launcher = createHerdrLauncher(TARGET, effects);
    expect(await launcher.launch()).toMatchObject({ launched: true, paneId: 'w1:p1', workspaceId: 'w1' });
    now = listing([row('sess-1', 'w4:p9', 'w4')]);
    expect(await launcher.launch()).toMatchObject({ launched: true, paneId: 'w4:p9', workspaceId: 'w4' });
    expect(calls.listing).toHaveLength(2);
  });

  it("the pane it reports is the one Herdr lists NOW, never the catalog's recorded pane id", async () => {
    const { effects } = fake({ binding: () => binding({ paneId: 'w1:p1' }), stdout: () => listing([row('sess-1', 'w2:p7', 'w2')]) });
    expect(await createHerdrLauncher(TARGET, effects).launch()).toEqual({
      launched: true,
      missionRef: 'herd:task-1@root-1',
      agent: 'proof-agent',
      session: 'sess-1',
      paneId: 'w2:p7',
      workspaceId: 'w2',
      launcher: LAUNCHER,
    });
  });
});

describe('an ADMISSION refusal writes nothing and opens nothing', () => {
  it.each([
    ['unverified', { known: true as const, value: 'unverified' as RuntimeIdentityState }],
    ['ambiguous', { known: true as const, value: 'ambiguous' as RuntimeIdentityState }],
    ['unavailable', { known: true as const, value: 'unavailable' as RuntimeIdentityState }],
    ['a state this office does not know', { known: false as const, raw: 'verified_later' }],
  ])("an identity that is %s is refused before Herdr's list is even read", async (_name, state) => {
    const { effects, calls } = fake({ binding: () => binding({ identity: { state, reason: 'session_replaced' } }) });
    const result = await createHerdrLauncher(TARGET, effects).launch();
    expect(result).toMatchObject({ launched: false, refusal: 'identity_not_verified' });
    expect(calls.listing).toEqual([]);
    nothingDone(calls);
  });

  it('a Mission with no recorded role, or no recorded session, is refused before anything runs', async () => {
    const none = fake({ binding: () => null });
    expect(await createHerdrLauncher(TARGET, none.effects).launch()).toMatchObject({ launched: false, refusal: 'no_binding' });
    expect(none.calls.listing).toEqual([]);
    nothingDone(none.calls);
    const noSession = fake({ binding: () => binding({ session: null }) });
    expect(await createHerdrLauncher(TARGET, noSession.effects).launch()).toMatchObject({ launched: false, refusal: 'session_unknown' });
    expect(noSession.calls.listing).toEqual([]);
    nothingDone(noSession.calls);
  });

  it("a recorded session that is not in Herdr's live list now is refused", async () => {
    const { effects, calls } = fake({ stdout: () => listing([row('sess-other')]) });
    expect(await createHerdrLauncher(TARGET, effects).launch()).toMatchObject({ launched: false, refusal: 'not_live' });
    nothingDone(calls);
  });

  it('a session listed more than once is refused: which one to attach cannot be told', async () => {
    const { effects, calls } = fake({ stdout: () => listing([row('sess-1', 'w1:p1', 'w1'), row('sess-1', 'w2:p2', 'w2', 'proof-agent-b')]) });
    expect(await createHerdrLauncher(TARGET, effects).launch()).toMatchObject({ launched: false, refusal: 'live_more_than_once' });
    nothingDone(calls);
  });

  it.each([
    ['not JSON', () => 'herdr: no server running'],
    ['no agents list', () => JSON.stringify({ result: {} })],
    ['an agent session that is not an object', () => listing([{ ...row('sess-1'), agent_session: 'sess-1' }])],
    ['ONE unreadable row beside a matching one (a skipped row would make a partial list read as complete)', () => listing([row('sess-1'), 'junk'])],
  ])('a live list with %s is unreadable as a whole, and refused', async (_name, stdout) => {
    const { effects, calls } = fake({ stdout });
    expect(await createHerdrLauncher(TARGET, effects).launch()).toMatchObject({ launched: false, refusal: 'listing_unreadable' });
    nothingDone(calls);
  });

  it('a listing that fails to run is refused', async () => {
    const { effects, calls } = fake({ listingThrows: true });
    expect(await createHerdrLauncher(TARGET, effects).launch()).toMatchObject({ launched: false, refusal: 'listing_unreadable' });
    nothingDone(calls);
  });

  it.each([
    [
      'a socket path that would break out of its quotes',
      { socketPath: "/opt/proof/home/x'; rm -rf ~; echo '" },
    ],
    ['a relative socket path', { socketPath: 'herdr.sock' }],
    ['a socket path with a parent segment', { socketPath: '/opt/proof/home/../herdr.sock' }],
    [
      'a socket path outside the HOME (the live socket, for one)',
      { socketPath: '/Users/someone/.config/herdr/herdr.sock' },
    ],
    [
      'no HOME (an inherited environment would point the route at the live server)',
      { home: null as unknown as string },
    ],
    ['a relative herdr path', { herdrBin: 'herdr' }],
    ['a herdr path with a space', { herdrBin: '/opt/my proof/herdr' }],
    ['a herdr path with a parent segment', { herdrBin: '/opt/proof/../herdr' }],
    ['a HOME with a quote', { home: "/opt/pro'of" }],
    ['a relative launcher directory', { launcherDir: 'launch' }],
    ['a launcher directory with a trailing slash', { launcherDir: '/opt/proof/launch/' }],
  ])('a target with %s is refused before ANY effect, the binding read included', async (_name, over) => {
    const { effects, calls } = fake();
    expect(await createHerdrLauncher({ ...TARGET, ...over }, effects).launch()).toMatchObject({ launched: false, refusal: 'target_invalid' });
    expect(calls.readBinding).toBe(0);
    expect(calls.listing).toEqual([]);
    nothingDone(calls);
  });

  it('a launcher name that is not plain lowercase hex is refused, with nothing written', async () => {
    const { effects, calls } = fake({ name: '../../evil' });
    expect(await createHerdrLauncher(TARGET, effects).launch()).toMatchObject({ launched: false, refusal: 'launcher_not_written' });
    nothingDone(calls);
  });

});

describe('an OPERATIONAL failure is reported as one, and says what it left', () => {
  it('a launcher write that fails is reported as not written, and nothing is opened', async () => {
    const { effects, calls } = fake({ writeThrows: true });
    expect(await createHerdrLauncher(TARGET, effects).launch()).toMatchObject({ launched: false, refusal: 'launcher_not_written' });
    expect(calls.writes, 'the write WAS attempted').toHaveLength(1);
    expect(calls.opens).toEqual([]);
  });

  it('an open that fails is reported as a failed launch, not a launch, and names the launcher file it left behind', async () => {
    const { effects, calls } = fake({ openThrows: true });
    const result = await createHerdrLauncher(TARGET, effects).launch();
    expect(result).toMatchObject({ launched: false, refusal: 'launch_failed' });
    expect(calls.writes.map(([p]) => p), 'the launcher WAS written').toEqual([LAUNCHER]);
    expect(result.launched === false && result.detail).toContain(LAUNCHER);
  });
});

describe('the Node effects: no shell anywhere, a bounded listing, a detached open', () => {
  function node(): { apis: NodeLaunchApis; seen: Record<string, unknown[]> } {
    const seen: Record<string, unknown[]> = { execFile: [], spawn: [], unref: [] };
    const apis: NodeLaunchApis = {
      execFile: (file, args, options, callback) => {
        seen.execFile.push([file, [...args], options]);
        callback(null, listing([]), '');
      },
      spawn: (file, args, options) => {
        seen.spawn.push([file, [...args], options]);
        return {
          once: (event: string, listener: (...a: unknown[]) => void) => {
            if (event === 'spawn') queueMicrotask(() => listener());
          },
          unref: () => {
            seen.unref.push(true);
          },
        };
      },
      writeFile: async () => undefined,
      randomBytes: () => ({ toString: () => 'a1b2c3d4e5f6a7b8c9d0e1f2' }),
      // What a process started inside a LIVE Herdr pane inherits: the live server's socket, and each variable Q4 names.
      env: {
        HOME: '/the/real/home',
        PATH: '/usr/bin:/bin',
        HERDR_SOCKET_PATH: '/the/real/home/.config/herdr/herdr.sock',
        XDG_CONFIG_HOME: '/the/real/home/.xdg',
        XDG_STATE_HOME: '/the/real/home/.state',
        HERDR_SESSION: 'live',
        HERDR_CLIENT_SOCKET_PATH: '/the/real/home/.config/herdr/herdr-client.sock',
        HERDR_PANE_ID: 'w6E:p3',
      },
    };
    return { apis, seen };
  }

  it("the listing runs with no shell, within a time and size bound, and under EXACTLY Q4's environment: an inherited LIVE socket is replaced and the four overriding variables are removed", async () => {
    const { apis, seen } = node();
    await nodeHerdrLaunchEffects(apis, async () => null).runListing(
      '/opt/proof/bin/herdr',
      ['agent', 'list'],
      { set: Q4_SET, unset: Q4_UNSET },
    );
    expect(seen.execFile).toEqual([
      [
        '/opt/proof/bin/herdr',
        ['agent', 'list'],
        {
          env: {
            PATH: '/usr/bin:/bin',
            HERDR_PANE_ID: 'w6E:p3',
            HOME: '/opt/proof/home',
            HERDR_SOCKET_PATH: '/opt/proof/home/.config/herdr/herdr.sock',
          },
          shell: false,
          timeout: HERDR_LISTING_TIMEOUT_MS,
          maxBuffer: HERDR_LISTING_MAX_BYTES,
          encoding: 'utf8',
        },
      ],
    ]);
    expect(HERDR_LISTING_TIMEOUT_MS).toBeGreaterThan(0);
    expect(HERDR_LISTING_MAX_BYTES).toBeGreaterThan(0);
  });

  it('the open runs with no shell, detached, with its output ignored, and the office does not wait on it', async () => {
    const { apis, seen } = node();
    await nodeHerdrLaunchEffects(apis, async () => null).openInTerminal('/usr/bin/open', ['-a', 'Terminal', LAUNCHER]);
    expect(seen.spawn).toEqual([['/usr/bin/open', ['-a', 'Terminal', LAUNCHER], { shell: false, detached: true, stdio: 'ignore' }]]);
    expect(seen.unref).toEqual([true]);
  });
});
