/**
 * THE HERDR LAUNCH ROUTE (Stage 4 item D1/D2/L6, the attach proof's prerequisite): the office opens a REAL macOS Terminal window running plain `herdr` against one Mission's herd, TARGETS that herd's workspace,
 * and SENDS NO INPUT.
 *
 * Every effect is INJECTED here: no test reads a real catalog, runs Herdr, writes a launcher or opens Terminal. What each block states is a requirement of the owner rule (the human types in the OS
 * terminal; the app sends no prompts, keystrokes or commands), of the design's binding rule (bind on the recorded session and re-resolve the pane AT USE; a cached pane id is unsafe), and of Amendment 1
 * (the workspace comes from the live row, focus is a REQUIRED precondition of opening, and the result claims no more than its receipts).
 *
 * WHAT THIS DOES NOT ESTABLISH: that the Herdr client attaches inside the window, that a person can type in it, or that the app sends nothing while they do. Those are the runtime proof, a person's, made at
 * a real terminal. The result of the route NEVER says "attached" or "keyboard works", and the tests below pin that it does not.
 *
 * R3 readiness under the owner's Q4 answer: every Herdr command the route runs, the listing, the focus, the read-back and the launcher alike, runs under EXACTLY Q4's environment: XDG_CONFIG_HOME,
 * XDG_STATE_HOME, HERDR_SESSION and HERDR_CLIENT_SOCKET_PATH removed; HOME and HERDR_SOCKET_PATH set to the target's. And the attach is plain `herdr` on that server.
 */
import { afterEach, describe, expect, it, vi } from 'vitest';

import type { RuntimeIdentityState } from '../src/di/catalogContract.js';
import type {
  HerdrAttachTarget,
  HerdrLaunchEffects,
  HerdrOpenOutcome,
  HerdrRoleBinding,
  HerdrTeamMemberEvidence,
  NodeLaunchApis,
} from '../src/di/herdrLaunch.js';
import {
  createHerdrLauncher,
  HERDR_LISTING_MAX_BYTES,
  HERDR_LISTING_TIMEOUT_MS,
  HERDR_OPEN_TIMEOUT_MS,
  HERDR_SNAPSHOT_MAX_BYTES,
  HERDR_SNAPSHOT_TIMEOUT_MS,
  HERDR_TERMINALS,
  HERDR_WORKSPACE_MAX_BYTES,
  HERDR_WORKSPACE_TIMEOUT_MS,
  herdrLauncherText,
  nodeHerdrLaunchEffects,
} from '../src/di/herdrLaunch.js';

const TARGET: HerdrAttachTarget = {
  herdrBin: '/opt/proof/bin/herdr',
  home: '/opt/proof/home',
  socketPath: '/opt/proof/home/.config/herdr/herdr.sock',
  launcherDir: '/opt/proof/launch',
  terminal: 'terminal',
};
/** Q4's environment rule, stated here and never read from the product under test. */
const Q4_SET = {
  HOME: '/opt/proof/home',
  HERDR_SOCKET_PATH: '/opt/proof/home/.config/herdr/herdr.sock',
};
const Q4_UNSET = ['XDG_CONFIG_HOME', 'XDG_STATE_HOME', 'HERDR_SESSION', 'HERDR_CLIENT_SOCKET_PATH'];
const LAUNCHER = '/opt/proof/launch/herdr-attach-a1b2c3d4e5f6a7b8.command';
/** The terminal application path the opener is given: stated here, not read from the table under test. */
const TERMINAL_APP = '/System/Applications/Utilities/Terminal.app';
const VERIFIED = { state: { known: true as const, value: 'verified' as RuntimeIdentityState }, reason: 'present' };

function binding(over: Partial<HerdrRoleBinding> = {}): HerdrRoleBinding {
  return { missionRef: 'herd:task-1@root-1', agent: 'proof-agent', session: 'sess-1', paneId: 'w1:p1', identity: VERIFIED, ...over };
}
function row(session: string | null, pane = 'w2:p7', workspace: string | null = 'w2', name = 'proof-agent'): Record<string, unknown> {
  return { name, agent: 'claude', agent_status: 'idle', agent_session: session === null ? null : { kind: 'id', value: session }, pane_id: pane, workspace_id: workspace };
}
function listing(rows: unknown[]): string {
  return JSON.stringify({ result: { agents: rows } });
}
/** Herdr's own `workspace get` record, in the shape its real output has (observed). */
function workspaceRecord(id: string, focused: boolean | undefined): string {
  return JSON.stringify({
    id: 'cli:workspace:get',
    result: { type: 'workspace_info', workspace: { active_tab_id: `${id}:t1`, agent_status: 'idle', ...(focused === undefined ? {} : { focused }), label: 'herd', number: 1, pane_count: 1, tab_count: 1, workspace_id: id } },
  });
}

/**
 * Herdr's own `api snapshot`, in the shape its real output has (observed): `result.snapshot.layouts[]`, each with a `workspace_id` and the pane `area` (x, y, width, height). The area here is the one
 * measured on the live herd before it was collapsed: it starts at column 26 and row 1 (the sidebar and the top bar sit before it).
 */
function snapshotOf(layouts: unknown[]): string {
  return JSON.stringify({ id: 'cli:api:snapshot', result: { type: 'session_snapshot', snapshot: { protocol: 19, version: '0.8.0', layouts, workspaces: [], tabs: [], panes: [], agents: [] } } });
}
const layoutOf = (workspace: string, area: { x: number; y: number; width: number; height: number }, tab = `${workspace}:t1`) => ({
  workspace_id: workspace,
  tab_id: tab,
  zoomed: false,
  focused_pane_id: `${workspace}:p1`,
  panes: [],
  splits: [],
  area,
});
const AREA = { x: 26, y: 1, width: 162, height: 46 };
/** What the launcher must require for that area: the far edge (x + width, y + height) plus the allowance of 2 and 2. Stated here, not read from the code under test. */
const WINDOW = { cols: 26 + 162 + 2, rows: 1 + 46 + 2 };

interface Calls {
  readBinding: number;
  listing: Array<[string, string[], unknown]>;
  snapshot: Array<[string, string[], unknown]>;
  workspace: Array<[string, string[], unknown]>;
  writes: Array<[string, string, { flag: string; mode: number }]>;
  opens: Array<[string, string[]]>;
  terminalChecks: string[];
  /** Every step in the order it happened. */
  order: string[];
}
function fake(
  opts: {
    binding?: () => HerdrRoleBinding | { unavailable: string } | null;
    stdout?: () => string;
    listingThrows?: boolean;
    writeThrows?: boolean;
    openThrows?: boolean;
    openOutcome?: HerdrOpenOutcome;
    focusThrows?: boolean;
    getThrows?: boolean;
    getStdout?: (workspaceId: string) => string;
    snapshotStdout?: () => string;
    snapshotThrows?: boolean;
    name?: string;
    installed?: boolean;
    withoutTerminalCheck?: boolean;
  } = {},
): { effects: HerdrLaunchEffects; calls: Calls } {
  const calls: Calls = { readBinding: 0, listing: [], snapshot: [], workspace: [], writes: [], opens: [], terminalChecks: [], order: [] };
  const effects: HerdrLaunchEffects = {
    readBinding: async () => {
      calls.readBinding++;
      calls.order.push('binding');
      return (opts.binding ?? (() => binding()))();
    },
    runListing: async (file, args, env) => {
      calls.listing.push([file, [...args], structuredClone(env)]);
      calls.order.push('listing');
      if (opts.listingThrows) throw new Error('the listing exited 1');
      return (opts.stdout ?? (() => listing([row('sess-1')])))();
    },
    runSnapshot: async (file, args, env) => {
      calls.snapshot.push([file, [...args], structuredClone(env)]);
      calls.order.push('snapshot');
      if (opts.snapshotThrows) throw new Error('the snapshot exited 1');
      // by default every workspace the test's own live listing names has the measured area (so a test about another workspace is not tripped by geometry it did not set up)
      if (opts.snapshotStdout) return opts.snapshotStdout();
      let listed: string[] = [];
      try {
        const rows = (JSON.parse((opts.stdout ?? (() => listing([row('sess-1')])))()) as { result: { agents: Array<{ workspace_id?: unknown }> } }).result.agents;
        listed = [...new Set(rows.map((r) => r.workspace_id).filter((w): w is string => typeof w === 'string'))];
      } catch {
        listed = [];
      }
      return snapshotOf(listed.map((w) => layoutOf(w, AREA)));
    },
    runWorkspaceCommand: async (file, args, env) => {
      calls.workspace.push([file, [...args], structuredClone(env)]);
      if (args[1] === 'focus') {
        calls.order.push('focus');
        if (opts.focusThrows) throw new Error('workspace focus exited 1');
        return '{"id":"cli:workspace:focus","result":{"type":"ok"}}';
      }
      calls.order.push('get');
      if (opts.getThrows) throw new Error('workspace get exited 1');
      return (opts.getStdout ?? ((id: string) => workspaceRecord(id, true)))(args[2]);
    },
    writeLauncher: async (filePath, text, options) => {
      calls.writes.push([filePath, text, { ...options }]);
      calls.order.push('write');
      if (opts.writeThrows) throw new Error('EEXIST');
    },
    openInTerminal: async (file, args) => {
      calls.opens.push([file, [...args]]);
      calls.order.push('open');
      if (opts.openThrows) throw new Error('the opener exited with status 1');
      return opts.openOutcome ?? 'exited_ok';
    },
    launcherName: () => opts.name ?? 'a1b2c3d4e5f6a7b8',
    ...(opts.withoutTerminalCheck
      ? {}
      : {
          terminalInstalled: async (appPath: string) => {
            calls.terminalChecks.push(appPath);
            calls.order.push('terminal-check');
            return opts.installed ?? true;
          },
        }),
  };
  return { effects, calls };
}
/** An ADMISSION refusal writes, focuses and opens nothing. */
const nothingDone = (calls: Calls) => {
  expect(calls.writes, 'no launcher is written').toEqual([]);
  expect(calls.workspace, 'no workspace is focused or read back').toEqual([]);
  expect(calls.opens, 'nothing is opened').toEqual([]);
};

describe('the launch opens Terminal on an executable file and types nothing (the owner rule: no app-generated command into the terminal)', () => {
  it('the only process it opens is /usr/bin/open -a <the Terminal application path> <the launcher>, once: no shell, no AppleScript', async () => {
    const { effects, calls } = fake();
    const result = await createHerdrLauncher(TARGET, effects).launch();
    expect(result.launched).toBe(true);
    expect(calls.opens).toEqual([['/usr/bin/open', ['-a', TERMINAL_APP, LAUNCHER]]]);
  });

  // A12 CHANGED THIS PIN, ON PURPOSE. It used to say the launcher is a shebang and ONE command. It is no longer one command: before it starts Herdr it checks that its own real window is large enough, asks
  // the terminal to grow that window if not, measures again, and starts Herdr only when the window really is large enough. The command that attaches is still the exec of PLAIN herdr under Q4's
  // environment (no --session, no session attach). The full text is stated here, independently of the code, so any change to what the launcher does is a decision this test forces.
  it("the launcher's text is exactly this: measure the window, ask it to grow, measure again, and only then exec PLAIN herdr under Q4's environment; and it is the file written", async () => {
    const expected = String.raw`#!/bin/sh
need_cols=190
need_rows=49
measure() {
  set -- $(/bin/stty size 2>/dev/null)
  known=0; rows=0; cols=0
  if [ "$#" = 2 ]; then
    case "$1$2" in
      *[!0-9]*) ;;
      *) known=1; rows=$1; cols=$2 ;;
    esac
  fi
}
enough() {
  measure
  [ "$known" = 1 ] && [ "$rows" -ge "$need_rows" ] && [ "$cols" -ge "$need_cols" ]
}
attach() {
  exec /usr/bin/env -u XDG_CONFIG_HOME -u XDG_STATE_HOME -u HERDR_SESSION -u HERDR_CLIENT_SOCKET_PATH HOME='/opt/proof/home' HERDR_SOCKET_PATH='/opt/proof/home/.config/herdr/herdr.sock' '/opt/proof/bin/herdr'
}
enough && attach
want_cols=$need_cols
want_rows=$need_rows
[ "$cols" -gt "$want_cols" ] && want_cols=$cols
[ "$rows" -gt "$want_rows" ] && want_rows=$rows
printf '\033[8;%d;%dt' "$want_rows" "$want_cols"
n=0
while [ "$n" -lt 40 ]; do
  /bin/sleep 0.1
  enough && attach
  n=$((n + 1))
done
while :; do
  printf '\nHerdr was NOT started in this window.\n'
  if [ "$known" = 1 ]; then
    printf 'This window is %s columns by %s rows; Herdr needs at least %s by %s here.\n' "$cols" "$rows" "$need_cols" "$need_rows"
  else
    printf 'This window\047s size could not be read; Herdr needs at least %s columns by %s rows here.\n' "$need_cols" "$need_rows"
  fi
  printf 'Herdr sizes its shared session to the smallest window attached to it, so attaching a smaller\n'
  printf 'window would shrink every pane of the herd you are using.\n'
  printf 'Make this window larger (drag its corner or zoom it), then press Return to try again.\n'
  printf 'Or close this window.\n'
  read -r _ || exit 1
  enough && attach
done
`;
    expect(herdrLauncherText(TARGET, WINDOW)).toBe(expected);
    const { effects, calls } = fake();
    await createHerdrLauncher(TARGET, effects).launch();
    expect(calls.writes.map(([p, text]) => [p, text])).toEqual([[LAUNCHER, expected]]);
  });

  describe('the launcher sets no size and sends nothing to anything that reads input (A12 forbids both; pinned over the text)', () => {
    const text = herdrLauncherText(TARGET, WINDOW);

    it('it has exactly ONE exec of herdr, and it is plain: no --session, no session attach, no other herdr verb', () => {
      expect(text.match(/exec /g)).toHaveLength(1);
      expect(text).not.toMatch(/session attach|--session/);
      expect(text.match(/'\/opt\/proof\/bin\/herdr'/g)).toHaveLength(1);
      expect(text).not.toMatch(/herdr' +(agent|api|workspace|pane|tab|server|session|update)/);
    });

    it('it READS the window size and never SETS one: no stty assignment, no COLUMNS or LINES, no TIOCSWINSZ, no resize helper', () => {
      expect(text.match(/stty/g)).toHaveLength(1);
      expect(text).toContain('/bin/stty size 2>/dev/null');
      // `stty size 2>/dev/null` is a READ; every form that SETS (rows N, cols N, size N N, a mode change, another device) is refused
      expect(text).not.toMatch(/stty +(rows|cols|columns|-f|-F|sane|raw|cbreak|-echo|echo)\b/);
      expect(text).not.toMatch(/stty +size +\d+ +\d+/);
      expect(text).not.toMatch(/COLUMNS|LINES|TIOCSWINSZ|\bresize\b|tput|\bscreen\b|\btmux\b/);
    });

    it('its only write to the terminal besides its own message is the standard resize request, ESC [ 8 ; rows ; cols t, once', () => {
      expect(text.match(/\\033\[8;/g)).toHaveLength(1);
      expect(text).toContain(String.raw`printf '\033[8;%d;%dt' "$want_rows" "$want_cols"`);
      // the other printf calls print fixed prose and MEASURED numbers; none prints a size it did not measure
      const printfs = text.split('\n').filter((l) => /printf/.test(l));
      expect(printfs.length).toBeGreaterThan(1);
    });

    it('it only ever GROWS the window: the request is never smaller than the window already is, in either dimension', () => {
      expect(text).toContain('[ "$cols" -gt "$want_cols" ] && want_cols=$cols');
      expect(text).toContain('[ "$rows" -gt "$want_rows" ] && want_rows=$rows');
    });

    it('it carries none of the input verbs the no-input rule forbids, and starts Herdr only after measuring the window to be large enough', () => {
      expect(text).not.toMatch(/send-keys|send-input|send-text|send_keys|send_input|send_text|agent prompt|agent\.prompt|pane\.send/);
      const attach = text.indexOf('exec /usr/bin/env');
      expect(attach).toBeGreaterThan(text.indexOf('enough() {'));
      // every call to attach is guarded by `enough &&`
      const calls = [...text.matchAll(/^(.*)\battach$/gm)].map((m) => m[1].trim());
      expect(calls.length).toBeGreaterThan(1);
      for (const guard of calls) expect(guard).toBe('enough &&');
    });

    it('every value in it is an integer or a validated plain path, single-quoted: a window requirement is whole numbers only', () => {
      expect(text).toMatch(/^need_cols=\d+$/m);
      expect(text).toMatch(/^need_rows=\d+$/m);
    });
  });

  it('the launcher is created exclusively (never over an existing file) and owner-only', async () => {
    const { effects, calls } = fake();
    await createHerdrLauncher(TARGET, effects).launch();
    expect(calls.writes.map(([, , options]) => options)).toEqual([{ flag: 'wx', mode: 0o700 }]);
  });

  it("the at-use read is Herdr's read-only agent list, run under Q4's environment: the target's HOME and socket SET, the four overriding variables REMOVED", async () => {
    const { effects, calls } = fake();
    await createHerdrLauncher(TARGET, effects).launch();
    expect(calls.listing).toEqual([['/opt/proof/bin/herdr', ['agent', 'list'], { set: Q4_SET, unset: Q4_UNSET }]]);
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
    expect(await launcher.launch()).toMatchObject({ launched: true, session: 'sess-new', matchedPaneId: 'w3:p2', workspaceId: 'w3' });
    current = binding({ session: 'sess-old' });
    expect(await launcher.launch()).toMatchObject({ launched: true, session: 'sess-old', matchedPaneId: 'w1:p1', workspaceId: 'w1' });
    expect(calls.readBinding).toBe(2);
  });

  it("each launch reads Herdr's live list afresh: a session that moved pane between launches is found where it is now", async () => {
    let now = listing([row('sess-1', 'w1:p1', 'w1')]);
    const { effects, calls } = fake({ stdout: () => now });
    const launcher = createHerdrLauncher(TARGET, effects);
    expect(await launcher.launch()).toMatchObject({ launched: true, matchedPaneId: 'w1:p1', workspaceId: 'w1' });
    now = listing([row('sess-1', 'w4:p9', 'w4')]);
    expect(await launcher.launch()).toMatchObject({ launched: true, matchedPaneId: 'w4:p9', workspaceId: 'w4' });
    expect(calls.listing).toHaveLength(2);
  });

  it("the pane it reports is the one Herdr lists NOW, never the catalog's recorded pane id, and it is reported as matched identity evidence with no pane focus", async () => {
    const { effects } = fake({ binding: () => binding({ paneId: 'w1:p1' }), stdout: () => listing([row('sess-1', 'w2:p7', 'w2')]) });
    expect(await createHerdrLauncher(TARGET, effects).launch()).toEqual({
      launched: true,
      missionRef: 'herd:task-1@root-1',
      agent: 'proof-agent',
      session: 'sess-1',
      identityBasis: 'runtime_identity_verified',
      matchedPaneId: 'w2:p7',
      paneFocus: 'not_performed',
      workspaceId: 'w2',
      window: WINDOW,
      launcher: LAUNCHER,
      terminal: { state: 'opened', terminal: 'terminal', label: 'Terminal' },
      focus: { state: 'focused', workspaceId: 'w2' },
    });
  });
});

/**
 * A12: THE WINDOW SIZE THE LAUNCHER WILL REQUIRE comes from Herdr's own read-only snapshot, in admission, before anything is written, focused or opened. If it cannot be established the route does NOT open
 * a window it cannot size (a window of unknown adequacy is how the live herd was collapsed), and says so.
 */
describe('the window size the launcher requires is derived from Herdr\'s snapshot in ADMISSION, and failing to establish it refuses honestly (A12)', () => {
  it('the requirement is the far edge of the workspace\'s pane area plus the 2 x 2 allowance: x + width + 2 columns by y + height + 2 rows', async () => {
    const { effects, calls } = fake({ snapshotStdout: () => snapshotOf([layoutOf('w2', { x: 26, y: 1, width: 162, height: 46 })]) });
    expect(await createHerdrLauncher(TARGET, effects).check()).toMatchObject({ available: true, window: { cols: 190, rows: 49 } });
    expect(calls.writes.map(([, text]) => text)).toEqual([]);
  });

  it('only THIS workspace\'s layouts count: another workspace\'s larger area is not borrowed', async () => {
    const { effects } = fake({ snapshotStdout: () => snapshotOf([layoutOf('w9', { x: 26, y: 1, width: 400, height: 100 }), layoutOf('w2', AREA)]) });
    expect(await createHerdrLauncher(TARGET, effects).check()).toMatchObject({ window: WINDOW });
  });

  it('with several layouts for the workspace the LARGEST extent wins, in each dimension, so a bigger tab is never under-asked', async () => {
    const { effects } = fake({
      snapshotStdout: () =>
        snapshotOf([layoutOf('w2', { x: 26, y: 1, width: 100, height: 60 }, 'w2:t1'), layoutOf('w2', { x: 26, y: 2, width: 150, height: 30 }, 'w2:t2')]),
    });
    // columns: 26 + 150 + 2 = 178; rows: 1 + 60 + 2 = 63
    expect(await createHerdrLauncher(TARGET, effects).check()).toMatchObject({ window: { cols: 178, rows: 63 } });
  });

  it('the snapshot is read AFTER the identity and the live row (it needs the live workspace), BEFORE anything is written, focused or opened, and the launcher carries the figures', async () => {
    const { effects, calls } = fake({ snapshotStdout: () => snapshotOf([layoutOf('w2', { x: 10, y: 3, width: 80, height: 20 })]) });
    expect(await createHerdrLauncher(TARGET, effects).launch()).toMatchObject({ launched: true, window: { cols: 92, rows: 25 } });
    expect(calls.order.indexOf('listing')).toBeLessThan(calls.order.indexOf('snapshot'));
    expect(calls.order.indexOf('snapshot')).toBeLessThan(calls.order.indexOf('write'));
    expect(calls.writes[0][1]).toContain('need_cols=92\nneed_rows=25\n');
  });

  it("the only snapshot command is Herdr's read-only `api snapshot`, under Q4's environment", async () => {
    const { effects, calls } = fake();
    await createHerdrLauncher(TARGET, effects).launch();
    expect(calls.snapshot).toEqual([['/opt/proof/bin/herdr', ['api', 'snapshot'], { set: Q4_SET, unset: Q4_UNSET }]]);
  });

  it.each([
    ['a snapshot that fails to run', { snapshotThrows: true }, 'could not be read (the snapshot exited 1)'],
    ['a snapshot that is not JSON', { snapshotStdout: () => 'herdr: no server' }, 'not one JSON document'],
    ['a snapshot with no layouts list', { snapshotStdout: () => JSON.stringify({ result: { snapshot: {} } }) }, 'carries no layouts list'],
    ['no layout for the workspace', { snapshotStdout: () => snapshotOf([layoutOf('w9', AREA)]) }, 'no layout for workspace w2'],
    ['a layout with no area', { snapshotStdout: () => snapshotOf([{ workspace_id: 'w2', tab_id: 't' }]) }, 'has no pane area'],
    ['an area of fractions', { snapshotStdout: () => snapshotOf([layoutOf('w2', { x: 26, y: 1, width: 162.5, height: 46 })]) }, 'not whole numbers within bounds'],
    ['an area of negative numbers', { snapshotStdout: () => snapshotOf([layoutOf('w2', { x: -1, y: 1, width: 162, height: 46 })]) }, 'not whole numbers within bounds'],
    ['an area with a string in it', { snapshotStdout: () => snapshotOf([layoutOf('w2', { x: 26, y: 1, width: '162' as unknown as number, height: 46 })]) }, 'not whole numbers within bounds'],
    ['an EMPTY area', { snapshotStdout: () => snapshotOf([layoutOf('w2', { x: 26, y: 1, width: 0, height: 46 })]) }, 'empty pane area'],
    ['an area past the sanity bounds', { snapshotStdout: () => snapshotOf([layoutOf('w2', { x: 26, y: 1, width: 990, height: 46 })]) }, 'past the 1000 x 500'],
    ['a layout that is not an object', { snapshotStdout: () => snapshotOf(['junk']) }, 'layout 0 is not an object'],
  ])('with %s it REFUSES as geometry_unknown, naming why, and writes, focuses and opens nothing', async (_name, over, words) => {
    const { effects, calls } = fake(over);
    const result = await createHerdrLauncher(TARGET, effects).launch();
    expect(result).toMatchObject({ launched: false, refusal: 'geometry_unknown' });
    expect(result.launched === false && result.detail).toContain('Nothing was opened');
    expect(result.launched === false && result.detail).toContain(words);
    nothingDone(calls);
    expect(await createHerdrLauncher(TARGET, fake(over).effects).check()).toMatchObject({ available: false, refusal: 'geometry_unknown' });
  });

  it('an admission refusal BEFORE the live row is found never reads the snapshot (no read it could not use)', async () => {
    const { effects, calls } = fake({ stdout: () => listing([row('sess-other')]) });
    expect(await createHerdrLauncher(TARGET, effects).launch()).toMatchObject({ refusal: 'not_live' });
    expect(calls.snapshot).toEqual([]);
  });
});

/**
 * THE TEAM-REPRESENTED SHAPE (the normal, healthy, bound Herdr task): its roles are in the Mission's team roster, not in runtime_agents. What the route accepts from a roster entry is
 * `teamMemberProblem`'s rule, stated in herdrLaunch.ts: every condition, and anything else fails closed. A roster entry is a DIFFERENT claim from a verified runtime identity and is never reported as one.
 */
describe('a role listed in the team roster (the team-represented shape) is accepted only on the roster rule, and never reported as "verified"', () => {
  /** The roster entry measured on a real contained catalog for a healthy bound task: present, session_confirmed, live session equal to the recorded one, on the direct chain. */
  const roster = (over: Partial<HerdrTeamMemberEvidence> = {}): HerdrTeamMemberEvidence => ({
    source: 'team_member',
    classification: 'present',
    reason: 'session_confirmed',
    presenceLive: true,
    chain: 'direct',
    sessionRecorded: 'sess-1',
    sessionLive: 'sess-1',
    ...over,
  });
  const member = (over: Partial<HerdrTeamMemberEvidence> = {}, session: string | null = 'sess-1') => () => binding({ session, identity: roster(over) });

  it('a confirmed roster entry is accepted: the launch goes ahead, and the result says the basis was the team roster, not a runtime-identity verification', async () => {
    const { effects, calls } = fake({ binding: member() });
    const result = await createHerdrLauncher(TARGET, effects).launch();
    expect(result).toMatchObject({ launched: true, identityBasis: 'team_roster_session_confirmed', session: 'sess-1', workspaceId: 'w2' });
    expect(calls.order).toEqual(['terminal-check', 'binding', 'listing', 'snapshot', 'write', 'focus', 'get', 'open']);
    expect(JSON.stringify(result)).not.toContain('runtime_identity_verified');
  });

  it('check() accepts it too, with the same basis, and does nothing', async () => {
    const { effects, calls } = fake({ binding: member() });
    expect(await createHerdrLauncher(TARGET, effects).check()).toMatchObject({ available: true, identityBasis: 'team_roster_session_confirmed', workspaceId: 'w2' });
    nothingDone(calls);
  });

  it("the independent live-list cross-check STILL applies to a roster role: a session not live, or live twice, is refused exactly as for a runtime role", async () => {
    const notLive = fake({ binding: member(), stdout: () => listing([row('sess-other')]) });
    expect(await createHerdrLauncher(TARGET, notLive.effects).launch()).toMatchObject({ launched: false, refusal: 'not_live' });
    nothingDone(notLive.calls);
    const twice = fake({ binding: member(), stdout: () => listing([row('sess-1', 'w1:p1', 'w1'), row('sess-1', 'w2:p2', 'w2', 'b')]) });
    expect(await createHerdrLauncher(TARGET, twice.effects).launch()).toMatchObject({ launched: false, refusal: 'live_more_than_once' });
    nothingDone(twice.calls);
  });

  it('the workspace is still the LIVE row\'s, not the roster\'s: a roster that recorded another workspace is not followed', async () => {
    const { effects, calls } = fake({ binding: member(), stdout: () => listing([row('sess-1', 'w9:p1', 'w9')]) });
    expect(await createHerdrLauncher(TARGET, effects).launch()).toMatchObject({ launched: true, workspaceId: 'w9' });
    expect(calls.workspace.map(([, args]) => args)).toEqual([
      ['workspace', 'focus', 'w9'],
      ['workspace', 'get', 'w9'],
    ]);
  });

  it.each([
    ['the office does not count the member live', { presenceLive: false }, "in the team roster (session_confirmed), not confirmed present: the office does not count it live"],
    ['it is on a child chain (another herd\'s member)', { chain: 'child' }, 'on the child roster chain, not this task\'s own'],
    ['it is on an unrecognised chain', { chain: 'unrecognized' }, 'on the unrecognized roster chain'],
    ['it is `replaced`: a session changed under the role, which the office counts live elsewhere but is NOT enough here', { classification: 'replaced', reason: 'session_changed' }, 'replaced in the team roster (session_changed), not confirmed present'],
    ['it is `ambiguous`', { classification: 'ambiguous', reason: 'agent_name_duplicated' }, 'ambiguous in the team roster (agent_name_duplicated)'],
    ['it is `disappeared`', { classification: 'disappeared', reason: 'agent_not_live' }, 'disappeared in the team roster (agent_not_live)'],
    ['it is `present` for any reason but session_confirmed', { reason: 'session_unconfirmed' }, 'present in the team roster (session_unconfirmed)'],
    ['it is `present` with reason stable_identity_unconfirmed', { reason: 'stable_identity_unconfirmed' }, 'present in the team roster (stable_identity_unconfirmed)'],
    ['its classification is something the office has never heard of', { classification: 'resurrected' }, 'resurrected in the team roster'],
    ['its live session is missing', { sessionLive: null }, 'no recorded and live session in the team roster to compare'],
    ['its recorded session is missing', { sessionRecorded: null }, 'no recorded and live session in the team roster to compare'],
    ['its live session is empty', { sessionLive: '' }, 'no recorded and live session in the team roster to compare'],
    ['its live session differs from the recorded one', { sessionLive: 'sess-other' }, 'a different live session in the team roster'],
  ])('a roster entry is refused, failing closed, when %s', async (_name, over, words) => {
    const { effects, calls } = fake({ binding: member(over) });
    const result = await createHerdrLauncher(TARGET, effects).launch();
    expect(result).toMatchObject({ launched: false, refusal: 'identity_not_verified' });
    expect(result.launched === false && result.detail).toContain(words);
    expect(calls.listing, 'refused before Herdr\'s list is read').toEqual([]);
    nothingDone(calls);
    expect(await createHerdrLauncher(TARGET, fake({ binding: member(over) }).effects).check()).toMatchObject({ available: false, refusal: 'identity_not_verified' });
  });

  it('a binding whose session is not the roster\'s recorded session is refused (the two must be the same session)', async () => {
    const { effects, calls } = fake({ binding: member({}, 'sess-other') });
    const result = await createHerdrLauncher(TARGET, effects).launch();
    expect(result).toMatchObject({ launched: false, refusal: 'identity_not_verified' });
    expect(result.launched === false && result.detail).toContain('names a session other than the roster');
    nothingDone(calls);
  });

  it('a roster entry with no recorded session in the binding is refused as an unknown session, not accepted on the live one', async () => {
    const { effects, calls } = fake({ binding: member({ sessionRecorded: null }, null) });
    expect(await createHerdrLauncher(TARGET, effects).launch()).toMatchObject({ launched: false, refusal: 'identity_not_verified' });
    nothingDone(calls);
  });

  it('no refusal about a roster entry ever calls it "verified", and none of its words claims a runtime identity', async () => {
    for (const over of [{ presenceLive: false }, { chain: 'child' }, { classification: 'replaced', reason: 'session_changed' }, { reason: 'session_unconfirmed' }, { sessionLive: 'x' }]) {
      const result = await createHerdrLauncher(TARGET, fake({ binding: member(over) }).effects).launch();
      expect(result.launched === false && result.detail).not.toMatch(/\bverified\b|not verified/);
    }
  });

  it('a runtime role is judged exactly as before: the roster rule does not loosen it, and a roster-shaped identity does not rescue an unverified runtime one', async () => {
    const unverified = fake({ binding: () => binding({ identity: { state: { known: true, value: 'unverified' }, reason: 'session_replaced' } }) });
    expect(await createHerdrLauncher(TARGET, unverified.effects).launch()).toMatchObject({ launched: false, refusal: 'identity_not_verified' });
    nothingDone(unverified.calls);
  });
});

describe('an ADMISSION refusal writes nothing, focuses nothing and opens nothing', () => {
  it.each([
    ['unverified', { known: true as const, value: 'unverified' as RuntimeIdentityState }],
    ['ambiguous', { known: true as const, value: 'ambiguous' as RuntimeIdentityState }],
    ['unavailable', { known: true as const, value: 'unavailable' as RuntimeIdentityState }],
    ['a state this office does not know', { known: false as const, raw: 'verified_later' }],
  ])("an identity that is %s is refused before Herdr's list is even read, naming the real state", async (_name, state) => {
    const { effects, calls } = fake({ binding: () => binding({ identity: { state, reason: 'session_replaced' } }) });
    const result = await createHerdrLauncher(TARGET, effects).launch();
    expect(result).toMatchObject({ launched: false, refusal: 'identity_not_verified' });
    expect(result.launched === false && result.detail).toContain('session_replaced');
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

  it('a binding the office says is unavailable is refused as no_binding with THAT reason in the detail, not a generic sentence', async () => {
    const reason = 'this Herdr task is ABORTED (terminal), not active, so no herd is offered for it';
    const { effects, calls } = fake({ binding: () => ({ unavailable: reason }) });
    const result = await createHerdrLauncher(TARGET, effects).launch();
    expect(result).toEqual({ launched: false, refusal: 'no_binding', detail: `Nothing was opened: ${reason}.` });
    expect(calls.listing).toEqual([]);
    nothingDone(calls);
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

  it('a listing that fails to run is refused, with the failure in the detail', async () => {
    const { effects, calls } = fake({ listingThrows: true });
    const result = await createHerdrLauncher(TARGET, effects).launch();
    expect(result).toMatchObject({ launched: false, refusal: 'listing_unreadable' });
    expect(result.launched === false && result.detail).toContain('the listing exited 1');
    nothingDone(calls);
  });

  it.each([
    ['a socket path that would break out of its quotes', { socketPath: "/opt/proof/home/x'; rm -rf ~; echo '" }],
    ['a relative socket path', { socketPath: 'herdr.sock' }],
    ['a socket path with a parent segment', { socketPath: '/opt/proof/home/../herdr.sock' }],
    ['a socket path outside the HOME (the live socket, for one)', { socketPath: '/Users/someone/.config/herdr/herdr.sock' }],
    ['no HOME (an inherited environment would point the route at the live server)', { home: null as unknown as string }],
    ['a relative herdr path', { herdrBin: 'herdr' }],
    ['a herdr path with a space', { herdrBin: '/opt/my proof/herdr' }],
    ['a herdr path with a parent segment', { herdrBin: '/opt/proof/../herdr' }],
    ['a HOME with a quote', { home: "/opt/pro'of" }],
    ['a relative launcher directory', { launcherDir: 'launch' }],
    ['a launcher directory with a trailing slash', { launcherDir: '/opt/proof/launch/' }],
    ['a terminal that is not in the table', { terminal: 'iterm' as never }],
    ['Ghostty, which accepted a launcher file and did not run it when observed, so it is not in the table', { terminal: 'ghostty' as never }],
    ['a terminal named like an object prototype member', { terminal: 'constructor' as never }],
  ])('a target with %s is refused before ANY effect, the binding read included', async (_name, over) => {
    const { effects, calls } = fake();
    expect(await createHerdrLauncher({ ...TARGET, ...over }, effects).launch()).toMatchObject({ launched: false, refusal: 'target_invalid' });
    expect(calls.readBinding).toBe(0);
    expect(calls.listing).toEqual([]);
    expect(calls.terminalChecks).toEqual([]);
    nothingDone(calls);
  });

  it('a launcher name that is not plain lowercase hex is refused, with nothing written', async () => {
    const { effects, calls } = fake({ name: '../../evil' });
    expect(await createHerdrLauncher(TARGET, effects).launch()).toMatchObject({ launched: false, refusal: 'launcher_not_written' });
    nothingDone(calls);
  });
});

describe('the terminal is an installed application from a closed table, checked before anything moves', () => {
  it('the table holds only a terminal observed to run an opened launcher: Terminal, by absolute application path', () => {
    expect(Object.keys(HERDR_TERMINALS)).toEqual(['terminal']);
    expect(HERDR_TERMINALS.terminal.app).toBe(TERMINAL_APP);
  });

  it('a terminal application that is not installed is refused BEFORE the binding is read, the focus is moved or anything is written, naming the application and its path', async () => {
    const { effects, calls } = fake({ installed: false });
    const result = await createHerdrLauncher(TARGET, effects).launch();
    expect(result).toEqual({ launched: false, refusal: 'terminal_unavailable', detail: `Nothing was opened: Terminal is not installed at ${TERMINAL_APP}.` });
    expect(calls.terminalChecks).toEqual([TERMINAL_APP]);
    expect(calls.readBinding).toBe(0);
    nothingDone(calls);
  });

  it('the install check is part of check() too, and it is the same refusal', async () => {
    const { effects } = fake({ installed: false });
    expect(await createHerdrLauncher(TARGET, effects).check()).toMatchObject({ available: false, refusal: 'terminal_unavailable' });
  });
});

describe('the herd workspace is focused through the socket API, after admission and before the open, and only a read-back says "focused"', () => {
  it('the steps happen in one order: admission, then the launcher, then focus, then the read-back, then the open', async () => {
    const { effects, calls } = fake();
    await createHerdrLauncher(TARGET, effects).launch();
    expect(calls.order).toEqual(['terminal-check', 'binding', 'listing', 'snapshot', 'write', 'focus', 'get', 'open']);
  });

  it("the only Herdr commands the route runs are the read-only agent list, `workspace focus <id>` and `workspace get <id>`, each under Q4's environment", async () => {
    const { effects, calls } = fake({ stdout: () => listing([row('sess-1', 'w9:p4', 'w9')]), getStdout: (id) => workspaceRecord(id, true) });
    await createHerdrLauncher(TARGET, effects).launch();
    expect(calls.listing.map(([file, args]) => [file, args])).toEqual([['/opt/proof/bin/herdr', ['agent', 'list']]]);
    expect(calls.workspace).toEqual([
      ['/opt/proof/bin/herdr', ['workspace', 'focus', 'w9'], { set: Q4_SET, unset: Q4_UNSET }],
      ['/opt/proof/bin/herdr', ['workspace', 'get', 'w9'], { set: Q4_SET, unset: Q4_UNSET }],
    ]);
  });

  it('the workspace focused is the LIVE ROW\'s for the verified session, whatever pane the catalog recorded and whatever other workspaces exist', async () => {
    const { effects, calls } = fake({
      binding: () => binding({ paneId: 'w1:p1' }),
      stdout: () => listing([row('sess-other', 'w1:p1', 'w1', 'a-different-herd'), row('sess-1', 'w7:p2', 'w7')]),
    });
    expect(await createHerdrLauncher(TARGET, effects).launch()).toMatchObject({ launched: true, workspaceId: 'w7', focus: { state: 'focused', workspaceId: 'w7' } });
    expect(calls.workspace.map(([, args]) => args)).toEqual([
      ['workspace', 'focus', 'w7'],
      ['workspace', 'get', 'w7'],
    ]);
  });

  it('a success reports the workspace focused only because the read-back said so, and the terminal as opened, as two separate facts', async () => {
    const { effects } = fake();
    const result = await createHerdrLauncher(TARGET, effects).launch();
    expect(result).toMatchObject({ launched: true, focus: { state: 'focused', workspaceId: 'w2' }, terminal: { state: 'opened' }, paneFocus: 'not_performed' });
  });

  it('a result never claims an attach or working keyboard input: no such word appears anywhere in it but the launcher file\'s own name', async () => {
    const { effects } = fake();
    const result = await createHerdrLauncher(TARGET, effects).launch();
    const text = JSON.stringify({ ...result, launcher: undefined });
    expect(text).not.toMatch(/attach|keyboard|typing|typed|input/i);
  });

  it.each([
    ['a workspace the live row does not name', row('sess-1', 'w2:p7', null)],
    ['a workspace id that would read as an option', row('sess-1', 'w2:p7', '--help')],
    ['a workspace id with a space', row('sess-1', 'w2:p7', 'w 2')],
    ['an empty workspace id', row('sess-1', 'w2:p7', '')],
    ['a workspace id with a shell character', row('sess-1', 'w2:p7', 'w2;ls')],
  ])('%s is refused as workspace_unknown before anything is written, focused or opened', async (_name, liveRow) => {
    const { effects, calls } = fake({ stdout: () => listing([liveRow]) });
    expect(await createHerdrLauncher(TARGET, effects).launch()).toMatchObject({ launched: false, refusal: 'workspace_unknown' });
    nothingDone(calls);
  });

  it('A FOCUS THAT FAILS REFUSES TO OPEN: focus_not_established, the terminal is never opened, and the detail names the launcher that WAS written', async () => {
    const { effects, calls } = fake({ focusThrows: true });
    const result = await createHerdrLauncher(TARGET, effects).launch();
    expect(result).toMatchObject({ launched: false, refusal: 'focus_not_established', focus: { state: 'failed', workspaceId: 'w2' } });
    expect(calls.opens, 'nothing is opened').toEqual([]);
    expect(calls.writes.map(([p]) => p), 'the launcher WAS written').toEqual([LAUNCHER]);
    expect(calls.workspace.map(([, args]) => args[1]), 'no read-back follows a failed focus').toEqual(['focus']);
    const detail = result.launched === false ? result.detail : '';
    expect(detail).toContain('Nothing was opened');
    expect(detail).toContain('workspace focus exited 1');
    expect(detail).toContain(LAUNCHER);
    expect(detail).toContain('nothing is known to have moved');
  });

  it.each([
    ['Herdr says the workspace is not focused', (id: string) => workspaceRecord(id, false), 'says the workspace is not focused'],
    ['the read-back names a different workspace', () => workspaceRecord('w99', true), 'a different workspace'],
    ['the read-back does not say', (id: string) => workspaceRecord(id, undefined), 'does not say whether'],
    ['the read-back has no workspace record', () => JSON.stringify({ result: {} }), 'no workspace record'],
    ['the read-back is not JSON', () => 'herdr: error', 'not one JSON document'],
  ])('a focus that cannot be CONFIRMED because %s also refuses to open, and says the view may have moved', async (_name, getStdout, words) => {
    const { effects, calls } = fake({ getStdout });
    const result = await createHerdrLauncher(TARGET, effects).launch();
    expect(result).toMatchObject({ launched: false, refusal: 'focus_not_established', focus: { state: 'requested_unconfirmed', workspaceId: 'w2' } });
    expect(calls.opens, 'nothing is opened').toEqual([]);
    const detail = result.launched === false ? result.detail : '';
    expect(detail).toContain('Nothing was opened');
    expect(detail).toContain('the view may have moved');
    expect(detail).toContain(words);
    expect(detail).toContain(LAUNCHER);
  });

  it('a read-back that fails to run is an unconfirmed focus, and refuses to open', async () => {
    const { effects, calls } = fake({ getThrows: true });
    expect(await createHerdrLauncher(TARGET, effects).launch()).toMatchObject({ launched: false, refusal: 'focus_not_established', focus: { state: 'requested_unconfirmed' } });
    expect(calls.opens).toEqual([]);
  });

  it('A FOCUS ESTABLISHED BUT AN OPEN THAT FAILS is launch_failed, and says the view HAS moved and nothing opened', async () => {
    const { effects, calls } = fake({ openThrows: true });
    const result = await createHerdrLauncher(TARGET, effects).launch();
    expect(result).toMatchObject({ launched: false, refusal: 'launch_failed', focus: { state: 'focused', workspaceId: 'w2' } });
    expect(calls.order.slice(-3)).toEqual(['focus', 'get', 'open']);
    const detail = result.launched === false ? result.detail : '';
    expect(detail).toContain('Terminal was not opened');
    expect(detail).toContain('the opener exited with status 1');
    expect(detail).toContain('the view HAS moved and nothing opened');
    expect(detail).toContain(LAUNCHER);
  });

  it('an opener still running at its bound is "unconfirmed": a launch that is neither a success nor a failure', async () => {
    const { effects } = fake({ openOutcome: 'unconfirmed' });
    expect(await createHerdrLauncher(TARGET, effects).launch()).toMatchObject({ launched: true, terminal: { state: 'unconfirmed' }, focus: { state: 'focused' } });
  });
});

describe('check() is the SAME admission as launch(), with no effect at all', () => {
  it('an available check names the workspace a launch would target and the pane it matched, and writes, focuses and opens nothing', async () => {
    const { effects, calls } = fake();
    expect(await createHerdrLauncher(TARGET, effects).check()).toEqual({
      available: true,
      missionRef: 'herd:task-1@root-1',
      agent: 'proof-agent',
      session: 'sess-1',
      identityBasis: 'runtime_identity_verified',
      matchedPaneId: 'w2:p7',
      workspaceId: 'w2',
      window: WINDOW,
      terminal: 'terminal',
      label: 'Terminal',
    });
    expect(calls.listing).toHaveLength(1);
    expect(calls.snapshot).toHaveLength(1);
    nothingDone(calls);
  });

  it.each([
    ['an unverified identity', { binding: () => binding({ identity: { state: { known: true as const, value: 'unverified' as RuntimeIdentityState }, reason: 'session_replaced' } }) }],
    ['no recorded role', { binding: () => null }],
    ['an unavailable binding', { binding: () => ({ unavailable: 'the office does not list this Mission now' }) }],
    ['a session that is not live', { stdout: () => listing([row('sess-other')]) }],
    ['a session live twice', { stdout: () => listing([row('sess-1', 'w1:p1', 'w1'), row('sess-1', 'w2:p2', 'w2', 'b')]) }],
    ['an unreadable list', { stdout: () => 'no' }],
    ['a failing list', { listingThrows: true }],
    ['a workspace the row does not name', { stdout: () => listing([row('sess-1', 'w2:p7', null)]) }],
    ['an uninstalled terminal', { installed: false }],
  ])('with %s, check() refuses with exactly the refusal and the words launch() gives, and does nothing', async (_name, over) => {
    const forLaunch = fake(over);
    const forCheck = fake(over);
    const launched = await createHerdrLauncher(TARGET, forLaunch.effects).launch();
    const checked = await createHerdrLauncher(TARGET, forCheck.effects).check();
    expect(launched.launched).toBe(false);
    expect(checked.available).toBe(false);
    if (!launched.launched && !checked.available) {
      expect(checked.refusal).toBe(launched.refusal);
      expect(checked.detail).toBe(launched.detail);
    }
    nothingDone(forCheck.calls);
  });

  it('a check refuses an invalid target before reading anything', async () => {
    const { effects, calls } = fake();
    expect(await createHerdrLauncher({ ...TARGET, home: 'relative' }, effects).check()).toMatchObject({ available: false, refusal: 'target_invalid' });
    expect(calls.readBinding).toBe(0);
    expect(calls.listing).toEqual([]);
  });
});

describe('an OPERATIONAL failure is reported as one, and says what it left', () => {
  it('a launcher write that fails is reported as not written, and nothing is focused or opened', async () => {
    const { effects, calls } = fake({ writeThrows: true });
    expect(await createHerdrLauncher(TARGET, effects).launch()).toMatchObject({ launched: false, refusal: 'launcher_not_written' });
    expect(calls.writes, 'the write WAS attempted').toHaveLength(1);
    expect(calls.workspace, 'a failed write never moves the view').toEqual([]);
    expect(calls.opens).toEqual([]);
  });
});

describe('the Node effects: no shell anywhere, bounded Herdr commands, and an opener waited for only to its own exit', () => {
  interface Spawned {
    file: string;
    args: string[];
    options: unknown;
    listeners: Record<string, Array<(...a: unknown[]) => void>>;
    unref: number;
  }
  function node(overrides: Partial<NodeLaunchApis> = {}): { apis: NodeLaunchApis; seen: { execFile: unknown[]; spawned: Spawned[] } } {
    const seen: { execFile: unknown[]; spawned: Spawned[] } = { execFile: [], spawned: [] };
    const apis: NodeLaunchApis = {
      execFile: (file, args, options, callback) => {
        seen.execFile.push([file, [...args], options]);
        callback(null, listing([]), '');
      },
      spawn: (file, args, options) => {
        const spawned: Spawned = { file, args: [...args], options, listeners: {}, unref: 0 };
        seen.spawned.push(spawned);
        return {
          once: (event: string, listener: (...a: unknown[]) => void) => {
            (spawned.listeners[event] ??= []).push(listener);
          },
          unref: () => {
            spawned.unref++;
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
      ...overrides,
    };
    return { apis, seen };
  }
  const emit = (spawned: Spawned, event: string, ...args: unknown[]) => {
    for (const listener of spawned.listeners[event] ?? []) listener(...args);
  };
  const REMAINING_ENV = { PATH: '/usr/bin:/bin', HERDR_PANE_ID: 'w6E:p3', HOME: '/opt/proof/home', HERDR_SOCKET_PATH: '/opt/proof/home/.config/herdr/herdr.sock' };

  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  it("the listing runs with no shell, within a time and size bound, and under EXACTLY Q4's environment: an inherited LIVE socket is replaced and the four overriding variables are removed", async () => {
    const { apis, seen } = node();
    await nodeHerdrLaunchEffects(apis, async () => null).runListing('/opt/proof/bin/herdr', ['agent', 'list'], { set: Q4_SET, unset: Q4_UNSET });
    expect(seen.execFile).toEqual([
      ['/opt/proof/bin/herdr', ['agent', 'list'], { env: REMAINING_ENV, shell: false, timeout: HERDR_LISTING_TIMEOUT_MS, maxBuffer: HERDR_LISTING_MAX_BYTES, encoding: 'utf8' }],
    ]);
    expect(HERDR_LISTING_TIMEOUT_MS).toBeGreaterThan(0);
    expect(HERDR_LISTING_MAX_BYTES).toBeGreaterThan(0);
  });

  it('a workspace command runs the same way: no shell, its own time and size bound, and the same Q4 environment', async () => {
    const { apis, seen } = node();
    await nodeHerdrLaunchEffects(apis, async () => null).runWorkspaceCommand('/opt/proof/bin/herdr', ['workspace', 'focus', 'w2'], { set: Q4_SET, unset: Q4_UNSET });
    expect(seen.execFile).toEqual([
      ['/opt/proof/bin/herdr', ['workspace', 'focus', 'w2'], { env: REMAINING_ENV, shell: false, timeout: HERDR_WORKSPACE_TIMEOUT_MS, maxBuffer: HERDR_WORKSPACE_MAX_BYTES, encoding: 'utf8' }],
    ]);
    expect(HERDR_WORKSPACE_TIMEOUT_MS).toBeGreaterThan(0);
    expect(HERDR_WORKSPACE_MAX_BYTES).toBeGreaterThan(0);
  });

  it('the snapshot read runs the same way: no shell, its own larger size bound and its time bound, and the same Q4 environment', async () => {
    const { apis, seen } = node();
    await nodeHerdrLaunchEffects(apis, async () => null).runSnapshot('/opt/proof/bin/herdr', ['api', 'snapshot'], { set: Q4_SET, unset: Q4_UNSET });
    expect(seen.execFile).toEqual([
      ['/opt/proof/bin/herdr', ['api', 'snapshot'], { env: REMAINING_ENV, shell: false, timeout: HERDR_SNAPSHOT_TIMEOUT_MS, maxBuffer: HERDR_SNAPSHOT_MAX_BYTES, encoding: 'utf8' }],
    ]);
    expect(HERDR_SNAPSHOT_MAX_BYTES).toBeGreaterThan(HERDR_WORKSPACE_MAX_BYTES);
    expect(HERDR_SNAPSHOT_TIMEOUT_MS).toBeGreaterThan(0);
  });

  it('a Herdr command that fails rejects with the failure, so the route can name it', async () => {
    const { apis } = node({ execFile: (_f, _a, _o, callback) => callback(new Error('exit status 1'), '', 'no server') });
    await expect(
      nodeHerdrLaunchEffects(apis, async () => null).runWorkspaceCommand('/opt/proof/bin/herdr', ['workspace', 'get', 'w2'], { set: Q4_SET, unset: Q4_UNSET }),
    ).rejects.toThrow('exit status 1');
  });

  it('the opener is started with no shell, detached, with its output ignored, and exactly the arguments the route chose', async () => {
    const { apis, seen } = node();
    const opening = nodeHerdrLaunchEffects(apis, async () => null).openInTerminal('/usr/bin/open', ['-a', TERMINAL_APP, LAUNCHER]);
    expect(seen.spawned).toHaveLength(1);
    expect(seen.spawned[0]).toMatchObject({ file: '/usr/bin/open', args: ['-a', TERMINAL_APP, LAUNCHER], options: { shell: false, detached: true, stdio: 'ignore' }, unref: 1 });
    emit(seen.spawned[0], 'exit', 0, null);
    await opening;
  });

  it('the office waits for the OPENER to exit, and an exit with status 0 is "exited_ok": it waits for nothing the window does', async () => {
    const { apis, seen } = node();
    let outcome: string | null = null;
    const opening = nodeHerdrLaunchEffects(apis, async () => null).openInTerminal('/usr/bin/open', ['-a', TERMINAL_APP, LAUNCHER]).then((o) => (outcome = o));
    await Promise.resolve();
    expect(outcome, 'a started opener that has not exited is not yet an outcome').toBeNull();
    emit(seen.spawned[0], 'exit', 0, null);
    await opening;
    expect(outcome).toBe('exited_ok');
  });

  it.each([
    ['a non-zero exit status', ['exit', 1, null], 'the opener exited with status 1'],
    ['an exit by signal', ['exit', null, 'SIGKILL'], 'the opener ended on signal SIGKILL'],
    ['a failure to start', ['error', new Error('spawn /usr/bin/open ENOENT')], 'ENOENT'],
  ])('%s is a FAILED open that rejects, so the route reports launch_failed', async (_name, [event, ...args], words) => {
    const { apis, seen } = node();
    const opening = nodeHerdrLaunchEffects(apis, async () => null).openInTerminal('/usr/bin/open', ['-a', TERMINAL_APP, LAUNCHER]);
    emit(seen.spawned[0], event as string, ...args);
    await expect(opening).rejects.toThrow(words as string);
  });

  it('a spawn that throws is a failed open, not an exception that escapes', async () => {
    const { apis } = node({
      spawn: () => {
        throw new Error('EAGAIN');
      },
    });
    await expect(nodeHerdrLaunchEffects(apis, async () => null).openInTerminal('/usr/bin/open', ['-a', TERMINAL_APP, LAUNCHER])).rejects.toThrow('EAGAIN');
  });

  it('an opener that never exits cannot hang the office: the wait ends at its bound as "unconfirmed", the timer is unref\'d, and a later exit changes nothing', async () => {
    vi.useFakeTimers();
    const unrefs: number[] = [];
    const original = globalThis.setTimeout;
    vi.spyOn(globalThis, 'setTimeout').mockImplementation(((fn: () => void, ms?: number) => {
      const timer = original(fn, ms) as unknown as { unref: () => unknown };
      const realUnref = timer.unref.bind(timer);
      timer.unref = () => {
        unrefs.push(ms ?? -1);
        return realUnref();
      };
      return timer;
    }) as unknown as typeof setTimeout);
    const { apis, seen } = node();
    let outcome: string | null = null;
    const opening = nodeHerdrLaunchEffects(apis, async () => null).openInTerminal('/usr/bin/open', ['-a', TERMINAL_APP, LAUNCHER]).then((o) => (outcome = o));
    await vi.advanceTimersByTimeAsync(HERDR_OPEN_TIMEOUT_MS - 1);
    expect(outcome, 'not before its bound').toBeNull();
    await vi.advanceTimersByTimeAsync(1);
    await opening;
    expect(outcome).toBe('unconfirmed');
    expect(unrefs).toEqual([HERDR_OPEN_TIMEOUT_MS]);
    emit(seen.spawned[0], 'exit', 1, null);
    expect(outcome, 'a late exit does not change an outcome already given').toBe('unconfirmed');
  });

  it('an exit before the bound clears the timer, so a finished open leaves nothing waiting', async () => {
    vi.useFakeTimers();
    const { apis, seen } = node();
    const opening = nodeHerdrLaunchEffects(apis, async () => null).openInTerminal('/usr/bin/open', ['-a', TERMINAL_APP, LAUNCHER]);
    expect(vi.getTimerCount()).toBe(1);
    emit(seen.spawned[0], 'exit', 0, null);
    await opening;
    expect(vi.getTimerCount()).toBe(0);
  });

  it('the terminal check is offered only when the composition can answer it', async () => {
    expect(nodeHerdrLaunchEffects(node().apis, async () => null).terminalInstalled).toBeUndefined();
    const withCheck = nodeHerdrLaunchEffects(node({ pathExists: async (p) => p === TERMINAL_APP }).apis, async () => null);
    expect(await withCheck.terminalInstalled?.(TERMINAL_APP)).toBe(true);
    expect(await withCheck.terminalInstalled?.('/nowhere/Terminal.app')).toBe(false);
  });
});
