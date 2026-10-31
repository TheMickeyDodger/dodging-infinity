/**
 * THE ATTACH PROOF'S COMPOSITION (owner item (d), R2): the separate, human-started proof entry that composes the approved launch route (herdrLaunch.ts) under the approved send recorder
 * (sendRecorder.ts) for ONE disposable target, and keeps the whole record of what THIS PROCESS sent across the person's typing window.
 *
 * Every effect is INJECTED here: stand-in module tables take the recorder, a scripted person answers, a counter is the clock. No test starts a process, runs Herdr, writes a launcher, opens
 * Terminal, reads a real file or listens on a socket. What each block states is a requirement of the owner rule (the human types in the OS terminal; the app sends nothing) and of the
 * Supervisor's R2 bounds (the recorder around the launch; the whole interval kept; launch and resolution told apart from input; nothing claimed about another actor).
 *
 * WHAT THIS DOES NOT ESTABLISH: that the target exists (R3, waiting on where Herdr's API socket goes), that an attach lands, that a person typed (R4, Travis), or that the real process sent
 * nothing. Those are the runtime proof. The session name is known by construction; production Mission-to-session discovery stays owed.
 */
import { describe, expect, it } from 'vitest';

import * as proofModule from '../src/di/attachProof.js';
import type { HerdrRoleBinding } from '../src/di/herdrLaunch.js';
import type { SendRecord, SendRecorderTargets } from '../src/di/sendRecorder.js';
import {
  RECORDED_FILE_APIS,
  RECORDED_FILE_PROMISE_APIS,
  RECORDED_PROCESS_APIS,
} from '../src/di/sendRecorder.js';

type Any = Record<string, any>;
type Fn = (...args: any[]) => any;

const proof = proofModule as unknown as Any;
/** A named export R2 adds: its absence FAILS (an assertion), never errors. */
const need = (name: string): Fn => {
  expect(typeof proof[name], `${name} is exported`).toBe('function');
  return proof[name] as Fn;
};

const ROOT = '/Users/travissellers/Documents/gb-tmp/herdr-attach-proof-20261028';
const EVIDENCE =
  '/Users/travissellers/tools/di-mission-worktrees/mn-491c0b35b901d74ca373a8f2f9c076fe/.herd/state/evidence/20261026-141936-4b6847/attach-proof';
const TARGET = {
  herdrBin: '/opt/proof/bin/herdr',
  home: '/opt/proof/root',
  socketPath: '/opt/proof/root/.config/herdr/herdr.sock',
  launcherDir: '/opt/proof/root/launchers',
  terminal: 'terminal',
};
const LAUNCHER_NAME = 'a1b2c3d4e5f6a7b8c9d0e1f2';
const LAUNCHER = `${TARGET.launcherDir}/herdr-attach-${LAUNCHER_NAME}.command`;
const BINDING: HerdrRoleBinding = {
  missionRef: 'disposable:attach-proof',
  agent: 'proof-agent',
  session: 'sess-proof-1',
  paneId: 'w9:p1',
  identity: {
    state: { known: true, value: 'verified' },
    reason: 'the disposable target was created with this session',
  },
};
const LISTING = JSON.stringify({
  result: {
    agents: [
      {
        name: 'proof-agent',
        agent_session: { kind: 'id', value: 'sess-proof-1' },
        pane_id: 'w9:p2',
        workspace_id: 'w9',
      },
    ],
  },
});

/** Herdr's read-only snapshot for the same workspace: the pane area the launcher's required window size is derived from (A12). */
const SNAPSHOT = JSON.stringify({ result: { snapshot: { layouts: [{ workspace_id: 'w9', tab_id: 'w9:t1', area: { x: 26, y: 1, width: 162, height: 46 } }] } } });

/** Stand-in module tables for the recorder, with each original kept so a test can see it restored. */
function standIns(): {
  tables: SendRecorderTargets;
  originals: Array<[Record<string, unknown>, string, unknown]>;
  stdout: object;
} {
  const inert = (name: string) => () => {
    throw new Error(`stand-in ${name} was not expected to be called`);
  };
  const childProcess: Record<string, unknown> = {};
  for (const name of RECORDED_PROCESS_APIS) childProcess[name] = inert(name);
  // The launch route runs four Herdr commands: the read-only agent list, the read-only `api snapshot` (the window size, A12), `workspace focus <id>` and the `workspace get <id>` read-back that confirms it.
  // Each gets the answer Herdr gives.
  childProcess.execFile = (
    _file: string,
    args: string[],
    _options: unknown,
    callback: (e: Error | null, out: string, err: string) => void,
  ) => {
    const out =
      args[0] === 'workspace' && args[1] === 'get'
        ? JSON.stringify({ result: { workspace: { workspace_id: args[2], focused: true } } })
        : args[0] === 'workspace'
          ? '{}'
          : args[0] === 'api'
            ? SNAPSHOT
            : LISTING;
    queueMicrotask(() => callback(null, out, ''));
    return {};
  };
  // The opener exits with status 0 once started: the route waits for the opener's own exit, never for a window.
  childProcess.spawn = () => {
    const child = {
      once(event: string, listener: (...a: unknown[]) => void) {
        if (event === 'exit') queueMicrotask(() => listener(0, null));
        return child;
      },
      unref() {},
    };
    return child;
  };
  const fileSystem: Record<string, unknown> = {};
  for (const name of RECORDED_FILE_APIS) fileSystem[name] = inert(name);
  const fileSystemPromises: Record<string, unknown> = {};
  for (const name of RECORDED_FILE_PROMISE_APIS) fileSystemPromises[name] = inert(name);
  fileSystemPromises.writeFile = async () => {};
  const socketPrototype: Record<string, unknown> = { connect: inert('connect'), write: () => true };
  const datagramPrototype: Record<string, unknown> = { send: inert('send') };
  const global: Record<string, unknown> = { fetch: inert('fetch') };
  const tables: SendRecorderTargets = {
    childProcess,
    socketPrototype,
    datagramPrototype,
    fileSystem,
    fileSystemPromises,
    global,
  };
  const originals: Array<[Record<string, unknown>, string, unknown]> = [];
  for (const table of Object.values(tables))
    for (const [name, value] of Object.entries(table)) originals.push([table, name, value]);
  return { tables, originals, stdout: {} };
}

interface Run {
  outcome: Any;
  evidence: Any[];
  said: string[];
  asked: number;
  tables: SendRecorderTargets;
  originals: Array<[Record<string, unknown>, string, unknown]>;
}

/** The composition's dependencies over stand-ins, with the person's `lines` given. */
function depsFor(
  lines: { next(): Promise<string>; ignored(): number },
  opts: { binding?: HerdrRoleBinding | null } = {},
): {
  deps: Any;
  evidence: Any[];
  said: string[];
  tables: SendRecorderTargets;
  originals: Array<[Record<string, unknown>, string, unknown]>;
} {
  const { tables, originals, stdout } = standIns();
  const said: string[] = [];
  const evidence: Any[] = [];
  let tick = 0;
  const deps = {
    target: TARGET,
    recorderTargets: tables,
    randomBytes: () => ({ toString: () => LAUNCHER_NAME }),
    env: { PATH: '/usr/bin:/bin' },
    readBinding: async () => (opts.binding === undefined ? BINDING : opts.binding),
    lines,
    say: (text: string) => {
      said.push(text);
      (tables.socketPrototype.write as Fn).call(stdout, text);
    },
    clock: () => ++tick,
    writeEvidence: async (text: string) => {
      evidence.push(JSON.parse(text));
    },
  };
  return { deps, evidence, said, tables, originals };
}

/** One run of the composition. `duringTyping` runs between the person's two answers, i.e. while they type. */
async function run(
  opts: {
    binding?: HerdrRoleBinding | null;
    duringTyping?: (tables: SendRecorderTargets) => void;
  } = {},
): Promise<Run> {
  const runAttachProof = need('runAttachProof');
  let asked = 0;
  let tablesRef: SendRecorderTargets | null = null;
  const lines = {
    next: async () => {
      asked += 1;
      if (asked === 2 && tablesRef !== null) opts.duringTyping?.(tablesRef);
      return '';
    },
    ignored: () => 0,
  };
  const { deps, evidence, said, tables, originals } = depsFor(lines, opts);
  tablesRef = tables;
  const outcome = await runAttachProof(deps);
  return { outcome, evidence, said, asked, tables, originals };
}

/** Let queued microtasks and one timer turn run: the stand-ins settle within it. */
const settle = () => new Promise<void>((resolve) => setTimeout(resolve, 0));

const labelOf = (r: SendRecord): unknown => (r.detail as { label?: unknown } | null)?.label;
const seqOfMark = (records: SendRecord[], label: string): number => {
  const marks = records.filter((r) => r.kind === 'marker' && labelOf(r) === label);
  expect(marks.length, `exactly one "${label}" mark`).toBe(1);
  return marks[0].seq;
};

describe('the one target and where the proof writes (the boundary, stated as constants)', () => {
  it('the target is the owner-permitted root, HOME for Herdr is that root, and its launchers stay under it', () => {
    expect(typeof proof.ATTACH_PROOF, 'ATTACH_PROOF is exported').toBe('object');
    const c = proof.ATTACH_PROOF;
    expect(c.root).toBe(ROOT);
    expect(c.target.home).toBe(ROOT);
    expect(c.target.launcherDir).toBe(`${ROOT}/launchers`);
    // Q4: the disposable server's API socket, at the path the owner's answer established for this HOME.
    expect(c.target.socketPath).toBe(`${ROOT}/.config/herdr/herdr.sock`);
    expect(c.target.herdrBin).toBe('/Users/travissellers/.local/bin/herdr');
  });

  it('the binding it reads and the evidence it writes are in this repository, never under the target', () => {
    expect(typeof proof.ATTACH_PROOF, 'ATTACH_PROOF is exported').toBe('object');
    const c = proof.ATTACH_PROOF;
    expect(c.bindingFile).toBe(`${EVIDENCE}/binding.json`);
    expect(c.evidenceDir).toBe(EVIDENCE);
  });
});

describe('the preflight: nothing is installed or launched unless the target and the binding are there', () => {
  type Kind = 'dir' | 'file' | 'link' | null;
  const stat = (kinds: Record<string, Kind>) => (p: string) => {
    const kind = kinds[p] ?? null;
    return kind === null
      ? null
      : {
          isDirectory: () => kind === 'dir',
          isFile: () => kind === 'file',
          isSymbolicLink: () => kind === 'link',
        };
  };
  const PATHS = {
    root: '/r',
    launcherDir: '/r/launchers',
    bindingFile: '/e/binding.json',
    evidenceDir: '/e',
  };
  const ALL: Record<string, Kind> = {
    '/r': 'dir',
    '/r/launchers': 'dir',
    '/e/binding.json': 'file',
    '/e': 'dir',
  };

  it('with every path in place, there is no problem', () => {
    expect(need('attachProofPreflight')(PATHS, stat(ALL))).toBeNull();
  });

  it('each missing path, or one that is a symbolic link, is a problem named by its path', () => {
    const preflight = need('attachProofPreflight');
    const paths = Object.keys(ALL);
    expect(paths).toEqual(['/r', '/r/launchers', '/e/binding.json', '/e']);
    for (const p of paths) {
      for (const kind of [null, 'link'] as Kind[]) {
        const problem = preflight(PATHS, stat({ ...ALL, [p]: kind }));
        expect(typeof problem, `${p} as ${String(kind)}`).toBe('string');
        expect(problem, `${p} as ${String(kind)}`).toContain(p);
      }
    }
  });

  it('a launcher directory outside the root is a problem', () => {
    const problem = need('attachProofPreflight')(
      { ...PATHS, launcherDir: '/elsewhere' },
      stat({ ...ALL, '/elsewhere': 'dir' }),
    );
    expect(typeof problem).toBe('string');
  });
});

describe("the binding file: the disposable target's ONE role, read at use", () => {
  const good = { disposable: true, ...BINDING };

  it('a disposable binding reads as the role binding, identity included', () => {
    expect(need('parseProofBinding')(JSON.stringify(good))).toEqual(BINDING);
  });

  it('anything not marked disposable, malformed or incomplete reads as no binding', () => {
    const parse = need('parseProofBinding');
    const bad = [
      'not json',
      JSON.stringify({ ...good, disposable: false }),
      JSON.stringify(BINDING),
      JSON.stringify({ ...good, session: 5 }),
      JSON.stringify({ ...good, missionRef: undefined }),
      JSON.stringify({ ...good, identity: { state: { known: true, value: 'verified' } } }),
      JSON.stringify([good]),
    ];
    expect(bad.length).toBe(7);
    for (const text of bad) expect(parse(text), text).toBeNull();
  });
});

describe('the launch goes through the recorder', () => {
  it("the launch's Node functions are looked up when CALLED, so a recorder installed after they were built still sees them", async () => {
    const launchApisFrom = need('launchApisFrom');
    const { installSendRecorder } = await import('../src/di/sendRecorder.js');
    const { tables } = standIns();
    const apis = launchApisFrom(tables, () => ({ toString: () => LAUNCHER_NAME }), {});
    const records: SendRecord[] = [];
    const recorder = installSendRecorder(
      tables,
      (r) => records.push(r),
      () => 1,
    );
    await new Promise<void>((resolve) =>
      apis.execFile(
        TARGET.herdrBin,
        ['agent', 'list'],
        { env: {}, shell: false, timeout: 1, maxBuffer: 1, encoding: 'utf8' },
        () => resolve(),
      ),
    );
    await apis.writeFile(LAUNCHER, '#!/bin/sh\n', { flag: 'wx', mode: 0o700, encoding: 'utf8' });
    apis.spawn('/usr/bin/open', ['-a', 'Terminal', LAUNCHER], {
      shell: false,
      detached: true,
      stdio: 'ignore',
    });
    recorder.uninstall();
    expect(records.map((r) => r.api)).toEqual([
      'child_process.execFile',
      'fs.promises.writeFile',
      'child_process.spawn',
    ]);
  });

  it('the recorder is installed first; the at-use read and the launch come after its first mark and before the typing window', async () => {
    const { evidence } = await run();
    expect(evidence.length).toBe(1);
    const records = evidence[0].records as SendRecord[];
    expect(records[0].kind).toBe('marker');
    expect(labelOf(records[0])).toBe('proof-start');
    const start = seqOfMark(records, 'typing-start');
    const classes = evidence[0].assessment.classified.map((c: Any) => [c.seq, c.class]);
    const before = classes
      .filter(([seq]: [number]) => seq < start)
      .map(([, cls]: [number, string]) => cls);
    expect(before.filter((cls: string) => cls === 'resolution').length).toBe(1);
    // The recorder's exact matches are unchanged: the launcher file write is still the one 'launch' entry. The route's NEWER steps (the read-only snapshot, workspace focus, its read-back, and the opener now
    // given the Terminal application by absolute path rather than by name) are not among those exact matches, so they are recorded as 'process', conservatively, and all of them before the typing window.
    expect(before.filter((cls: string) => cls === 'launch').length).toBe(1);
    expect(before.filter((cls: string) => cls === 'process').length).toBe(4);
    expect(classes.filter(([seq]: [number]) => seq > start).map(([, cls]: [number, string]) => cls)).not.toContain('process');
  });
});

describe('the typing window', () => {
  it('the person is asked twice, once to start and once to end; nothing is said while they type', async () => {
    const { evidence, asked, said } = await run();
    expect(asked).toBe(2);
    expect(said.length).toBeGreaterThan(0);
    const records = evidence[0].records as SendRecord[];
    const start = seqOfMark(records, 'typing-start');
    const end = seqOfMark(records, 'typing-end');
    expect(start).toBeLessThan(end);
    const inside = records.filter((r) => r.seq > start && r.seq < end);
    expect(inside).toEqual([]);
  });

  it('with nothing sent while they type, the window supports the no-input statement, and the WHOLE record is kept', async () => {
    const { evidence, outcome } = await run();
    const e = evidence[0];
    expect(e.assessment.supported).toBe(true);
    expect(e.assessment.reasons).toEqual([]);
    expect(e.assessment.classified.length).toBe(e.records.length);
    expect(e.assessment.classified.map((c: Any) => c.seq)).toEqual(
      e.records.map((r: SendRecord) => r.seq),
    );
    expect(e.assessment.window).toEqual({
      startSeq: seqOfMark(e.records, 'typing-start'),
      endSeq: seqOfMark(e.records, 'typing-end'),
    });
    expect(outcome.assessment.supported).toBe(true);
  });

  it('a send inside the window is kept and named, never filtered: the window then supports nothing', async () => {
    const { evidence } = await run({
      duringTyping: (tables) => (tables.socketPrototype.write as Fn).call({}, 'x'),
    });
    const e = evidence[0];
    expect(e.assessment.supported).toBe(false);
    expect(e.assessment.reasons.length).toBe(1);
    expect(e.assessment.reasons[0]).toContain('socket');
    expect(
      e.records.some(
        (r: SendRecord) =>
          r.kind === 'socket.write' && r.seq > seqOfMark(e.records, 'typing-start'),
      ),
    ).toBe(true);
  });

  it('the evidence states the launch, the target and the limits', async () => {
    const { evidence } = await run();
    const e = evidence[0];
    expect(e.launch.launched).toBe(true);
    expect(e.launch.session).toBe('sess-proof-1');
    // the pane of the live row that matched the session: identity evidence, which focuses nothing (the field was `paneId` before it was named for what it is)
    expect(e.launch.matchedPaneId).toBe('w9:p2');
    expect(e.launch.paneFocus).toBe('not_performed');
    expect(e.launch.workspaceId).toBe('w9');
    expect(e.launch.focus).toEqual({ state: 'focused', workspaceId: 'w9' });
    expect(e.launch.launcher).toBe(LAUNCHER);
    expect(e.target).toEqual(TARGET);
    expect(Array.isArray(e.limits)).toBe(true);
    expect(e.limits.join(' ')).toContain('other actor');
  });
});

describe('round 65: each mark is set by a line received AFTER its wait began', () => {
  it('a wait is filled only by a line received after it began; earlier lines are ignored and counted; two lines in one chunk fill one wait', async () => {
    const humanLines = need('humanLines');
    let push: (chunk: string) => void = () => {};
    const lines = humanLines((listener: (chunk: string) => void) => {
      push = listener;
    });
    push('early\n');
    let first: string | null = null;
    const firstWait = lines.next().then((line: string) => (first = line));
    await settle();
    expect(first, 'a line received before the wait began does not fill it').toBeNull();
    push('b\nc\n');
    await firstWait;
    expect(first).toBe('b');
    expect(lines.ignored()).toBe(2);
    let second: string | null = null;
    const secondWait = lines.next().then((line: string) => (second = line));
    await settle();
    expect(
      second,
      'the line left over from the earlier chunk does not fill the next wait',
    ).toBeNull();
    push('d\n');
    await secondWait;
    expect(second).toBe('d');
    expect(lines.ignored()).toBe(2);
  });

  it('two Enters pressed before the first wait set no mark: the start waits for a fresh line, the end for one received after the start mark', async () => {
    const humanLines = need('humanLines');
    const runAttachProof = need('runAttachProof');
    let push: (chunk: string) => void = () => {};
    const lines = humanLines((listener: (chunk: string) => void) => {
      push = listener;
    });
    push('\n\n');
    const { deps, evidence } = depsFor(lines);
    let settled = false;
    const running = runAttachProof(deps).then((outcome: Any) => {
      settled = true;
      return outcome;
    });
    await settle();
    await settle();
    expect(settled, 'the run still waits for the person').toBe(false);
    push('\n');
    await settle();
    expect(settled, 'the end still waits for a line received after the start mark').toBe(false);
    push('\n');
    await running;
    const records = evidence[0].records as SendRecord[];
    expect(seqOfMark(records, 'typing-start')).toBeLessThan(seqOfMark(records, 'typing-end'));
    expect(evidence[0].linesIgnored).toBe(2);
    expect(evidence[0].assessment.supported).toBe(true);
  });
});

describe('afterwards', () => {
  it('every wrapped function is restored before the evidence is written', async () => {
    const { tables, originals } = await run();
    expect(originals.length).toBe(
      Object.values(tables).reduce((n, table) => n + Object.keys(table).length, 0),
    );
    for (const [table, name, value] of originals) expect(table[name], name).toBe(value);
  });

  it('a refused launch asks the person for nothing, opens no window, and records the refusal', async () => {
    const { evidence, asked, outcome } = await run({ binding: null });
    expect(asked).toBe(0);
    expect(outcome.launch.launched).toBe(false);
    expect(outcome.launch.refusal).toBe('no_binding');
    expect(evidence.length).toBe(1);
    expect(evidence[0].launch.refusal).toBe('no_binding');
    expect(evidence[0].assessment).toBeNull();
    const records = evidence[0].records as SendRecord[];
    expect(records.some((r) => r.kind === 'marker' && labelOf(r) === 'typing-start')).toBe(false);
    expect(records.some((r) => r.kind === 'process')).toBe(false);
  });
});
