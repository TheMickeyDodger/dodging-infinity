/**
 * THE LAUNCHER, RUN (Amendment 2, A12): what the script the route writes actually DOES, branch by branch, under /bin/sh.
 *
 * The launcher decides, in the window it runs in, whether to start Herdr: it MEASURES the window (`stty size`, a read), asks the terminal to grow it if it is too small, MEASURES AGAIN, and starts plain
 * `herdr` only if the window really is large enough. Here `stty` and `sleep` are replaced by stand-in programs (the generator accepts their paths so a test can), and `herdr` by a program that records the
 * environment it was started with. No terminal is opened and no real window is measured: the sizes are whatever the stand-in `stty` is told to say.
 *
 * WHAT THIS SHOWS: every branch of the script's logic is as documented; the only thing it ever writes to the terminal is the standard resize request and its own refusal message; it never calls `stty` with
 * anything but `size`; it grows a dimension only when it must and never shrinks one; and it never starts Herdr in a window it measured too small or could not measure.
 *
 * WHAT THIS DOES NOT ESTABLISH: that Terminal.app carries out the resize request (UNOBSERVED: native sizing is owed to a human), or what a real `stty size` says in a real window. The stand-ins say what the test
 * tells them, so a test passing here is a statement about the script, not about any terminal.
 */
import { spawnSync } from 'child_process';
import * as fs from 'fs';
import * as os from 'os';
import * as path from 'path';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';

import type { HerdrAttachTarget } from '../src/di/herdrLaunch.js';
import { herdrLauncherText, LAUNCHER_RESIZE_TRIES } from '../src/di/herdrLaunch.js';

let dir: string;
let target: HerdrAttachTarget;
const WINDOW = { cols: 190, rows: 49 };
const ESC = String.fromCharCode(27);

beforeEach(() => {
  dir = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), 'herdrlauncher-')));
  const exe = (name: string, body: string): string => {
    const file = path.join(dir, name);
    fs.writeFileSync(file, `#!/bin/sh\n${body}\n`, { mode: 0o700 });
    return file;
  };
  // a stand-in `stty`: records its arguments, then says the next size from stty.sizes (one "rows cols" per line, the last repeating), or fails on the line FAIL
  exe(
    'stty',
    [
      'echo "$@" >> "$STUB_DIR/stty.args"',
      'n=$(cat "$STUB_DIR/stty.count" 2>/dev/null || echo 0); n=$((n + 1)); echo $n > "$STUB_DIR/stty.count"',
      'line=$(sed -n "${n}p" "$STUB_DIR/stty.sizes"); [ -z "$line" ] && line=$(tail -n 1 "$STUB_DIR/stty.sizes")',
      '[ "$line" = FAIL ] && exit 1',
      'echo "$line"',
    ].join('\n'),
  );
  exe('sleep', 'echo "$@" >> "$STUB_DIR/sleep.args"');
  // a stand-in `herdr`: records that it ran, with what arguments and the environment that matters
  exe(
    'herdr',
    [
      'echo "started" > "$STUB_DIR/herdr.ran"',
      'echo "argc=$#" >> "$STUB_DIR/herdr.ran"',
      'echo "HOME=$HOME" >> "$STUB_DIR/herdr.ran"',
      'echo "HERDR_SOCKET_PATH=$HERDR_SOCKET_PATH" >> "$STUB_DIR/herdr.ran"',
      'echo "XDG_CONFIG_HOME=${XDG_CONFIG_HOME-UNSET}" >> "$STUB_DIR/herdr.ran"',
      'echo "XDG_STATE_HOME=${XDG_STATE_HOME-UNSET}" >> "$STUB_DIR/herdr.ran"',
      'echo "HERDR_SESSION=${HERDR_SESSION-UNSET}" >> "$STUB_DIR/herdr.ran"',
      'echo "HERDR_CLIENT_SOCKET_PATH=${HERDR_CLIENT_SOCKET_PATH-UNSET}" >> "$STUB_DIR/herdr.ran"',
    ].join('\n'),
  );
  target = {
    herdrBin: path.join(dir, 'herdr'),
    home: path.join(dir, 'home'),
    socketPath: path.join(dir, 'home', 'herdr.sock'),
    launcherDir: dir,
    terminal: 'terminal',
  };
});

afterEach(() => {
  // only the directory this file made, by its exact path, directly under the temp root
  if (path.dirname(dir) === fs.realpathSync(os.tmpdir()) && /^herdrlauncher-[A-Za-z0-9]{6}$/.test(path.basename(dir))) fs.rmSync(dir, { recursive: true, force: true });
});

interface Ran {
  status: number | null;
  stdout: string;
  started: string[] | null;
  sttyCalls: number;
  sttyArgs: string[];
  sleepArgs: string[];
}

/** Run the generated launcher under /bin/sh. `sizes` is what the stand-in stty says on its 1st, 2nd, ... call; `stdin` is what the person types. */
function run(sizes: string[], stdin = ''): Ran {
  // each run starts from clean stand-in state (a test may run the launcher more than once in the same directory)
  for (const name of ['stty.count', 'stty.args', 'sleep.args', 'herdr.ran']) fs.rmSync(path.join(dir, name), { force: true });
  fs.writeFileSync(path.join(dir, 'stty.sizes'), `${sizes.join('\n')}\n`);
  const script = path.join(dir, 'launcher.command');
  fs.writeFileSync(script, herdrLauncherText(target, WINDOW, { stty: path.join(dir, 'stty'), sleep: path.join(dir, 'sleep'), env: '/usr/bin/env' }), { mode: 0o700 });
  const result = spawnSync('/bin/sh', [script], {
    input: stdin,
    encoding: 'utf8',
    timeout: 30_000,
    // what a process started inside a LIVE Herdr pane inherits: each variable the confinement rule removes
    env: { PATH: '/usr/bin:/bin', STUB_DIR: dir, XDG_CONFIG_HOME: '/live/xdg', XDG_STATE_HOME: '/live/state', HERDR_SESSION: 'live', HERDR_CLIENT_SOCKET_PATH: '/live/client.sock', HERDR_SOCKET_PATH: '/live/herdr.sock', HOME: '/live/home' },
  });
  const read = (name: string): string | null => (fs.existsSync(path.join(dir, name)) ? fs.readFileSync(path.join(dir, name), 'utf8') : null);
  const lines = (name: string): string[] => (read(name) ?? '').split('\n').filter((l) => l !== '');
  const ran = read('herdr.ran');
  return {
    status: result.status,
    stdout: result.stdout,
    started: ran === null ? null : ran.split('\n').filter((l) => l !== ''),
    sttyCalls: Number(read('stty.count') ?? 0),
    sttyArgs: lines('stty.args'),
    sleepArgs: lines('sleep.args'),
  };
}

describe('a window that is already large enough: Herdr starts, and nothing is printed or asked', () => {
  it('starts plain herdr under the confined environment, prints nothing, and does not ask the terminal to resize', () => {
    const r = run(['60 200']);
    expect(r.status).toBe(0);
    expect(r.stdout).toBe('');
    expect(r.started).toEqual([
      'started',
      'argc=0',
      `HOME=${path.join(dir, 'home')}`,
      `HERDR_SOCKET_PATH=${path.join(dir, 'home', 'herdr.sock')}`,
      'XDG_CONFIG_HOME=UNSET',
      'XDG_STATE_HOME=UNSET',
      'HERDR_SESSION=UNSET',
      'HERDR_CLIENT_SOCKET_PATH=UNSET',
    ]);
    expect(r.sttyCalls).toBe(1);
    expect(r.sleepArgs).toEqual([]);
  });

  it('exactly the required size is enough, in each dimension separately: one cell short in either is not', () => {
    expect(run(['49 190']).started).not.toBeNull();
    const shortCols = run(['49 189']);
    expect(shortCols.stdout).toContain(`${ESC}[8;49;190t`);
    const shortRows = run(['48 190']);
    expect(shortRows.stdout).toContain(`${ESC}[8;49;190t`);
  });
});

describe('a window that is too small is asked to GROW, measured again, and Herdr starts only when it really did', () => {
  it('asks once for the needed size, then starts Herdr when the next measurement is large enough; the request is the only thing printed', () => {
    const r = run(['24 80', '50 200']);
    expect(r.status).toBe(0);
    expect(r.stdout).toBe(`${ESC}[8;49;190t`);
    expect(r.started).not.toBeNull();
    expect(r.sttyCalls).toBe(2);
    expect(r.sleepArgs).toEqual(['0.1']);
  });

  it('keeps measuring (a tenth of a second apart) while the terminal catches up, and starts Herdr the moment the window is large enough', () => {
    const r = run(['24 80', '24 80', '30 120', '49 189', '49 190']);
    expect(r.started).not.toBeNull();
    expect(r.sttyCalls).toBe(5);
    expect(r.sleepArgs).toEqual(['0.1', '0.1', '0.1', '0.1']);
    expect(r.stdout).toBe(`${ESC}[8;49;190t`);
  });

  it('only ever GROWS a window: a dimension that is already larger than needed is asked for at its CURRENT size, never reduced', () => {
    expect(run(['20 300', '60 300']).stdout).toBe(`${ESC}[8;49;300t`);
    expect(run(['100 100', '100 200']).stdout).toBe(`${ESC}[8;100;190t`);
  });

  it('the size it acts on is READ from the window, never set: stty is only ever called with `size`', () => {
    const r = run(['24 80', '24 80', '50 200']);
    expect(r.sttyArgs.length).toBe(r.sttyCalls);
    for (const args of r.sttyArgs) expect(args).toBe('size');
  });
});

describe('a window that never becomes large enough: Herdr is NOT started, and the window says exactly what is true', () => {
  it('after its tries, prints the real measured size, the size needed and why, does NOT start Herdr, and exits when no one answers', () => {
    const r = run(['24 80']);
    expect(r.started).toBeNull();
    expect(r.status).toBe(1);
    expect(r.sttyCalls).toBe(1 + LAUNCHER_RESIZE_TRIES);
    expect(r.sleepArgs).toHaveLength(LAUNCHER_RESIZE_TRIES);
    // the ONE resize request, then the message; nothing else was written
    expect(r.stdout.startsWith(`${ESC}[8;49;190t`)).toBe(true);
    expect(r.stdout.match(/\u001b\[8;/g)).toHaveLength(1);
    expect(r.stdout).toContain('Herdr was NOT started in this window.');
    expect(r.stdout).toContain('This window is 80 columns by 24 rows; Herdr needs at least 190 by 49 here.');
    expect(r.stdout).toContain('Herdr sizes its shared session to the smallest window attached to it');
    expect(r.stdout).toContain('Make this window larger (drag its corner or zoom it), then press Return to try again.');
  });

  it('never reports a size it did not measure: the figures in the message are the ones the last measurement gave', () => {
    const r = run(['30 90', '31 91']);
    expect(r.stdout).toContain('This window is 91 columns by 31 rows; Herdr needs at least 190 by 49 here.');
    expect(r.stdout).not.toContain('80 columns');
    expect(r.stdout).not.toContain('190 columns by');
  });

  it('a size that cannot be READ is said to be unreadable, not guessed, and Herdr is not started', () => {
    const r = run(['FAIL']);
    expect(r.started).toBeNull();
    expect(r.status).toBe(1);
    expect(r.stdout).toContain("This window's size could not be read; Herdr needs at least 190 columns by 49 rows here.");
    expect(r.stdout).not.toMatch(/This window is \d+ columns/);
  });

  // Found by RUNNING the launcher: a single token like `12` used to pass the digit check and print "This window is  columns by 12 rows" (an empty column count reported as a measurement). A size is
  // known only when the terminal said EXACTLY two whole numbers.
  it.each([
    ['words', 'garbage here'],
    ['letters', 'x y'],
    ['a single number', '12'],
    ['a number and a word', '12 three'],
    ['three numbers', '12 190 7'],
    ['a negative number', '-1 190'],
    ['a fraction', '1.5 190'],
    ['empty', ''],
  ])('a measurement of %s is treated as unreadable, never as a size, and never printed as one', (_name, line) => {
    const r = run([line === '' ? 'FAIL' : line]);
    expect(r.started).toBeNull();
    expect(r.stdout).toContain("This window's size could not be read;");
    expect(r.stdout).not.toMatch(/This window is/);
  });

  it('when the person makes the window larger and presses Return, it measures again and starts Herdr, and not before', () => {
    const tooSmall = Array.from({ length: 1 + LAUNCHER_RESIZE_TRIES }, () => '24 80');
    const r = run([...tooSmall, '60 200'], '\n');
    expect(r.started).not.toBeNull();
    expect(r.status).toBe(0);
    expect(r.stdout.match(/Herdr was NOT started in this window\./g)).toHaveLength(1);
    expect(r.sttyCalls).toBe(1 + LAUNCHER_RESIZE_TRIES + 1);
  });

  it('when the person presses Return but the window is STILL too small, it says so again with the new measurement and waits again', () => {
    const tooSmall = Array.from({ length: 1 + LAUNCHER_RESIZE_TRIES }, () => '24 80');
    const r = run([...tooSmall, '40 150'], '\n');
    expect(r.started).toBeNull();
    expect(r.stdout.match(/Herdr was NOT started in this window\./g)).toHaveLength(2);
    expect(r.stdout).toContain('This window is 150 columns by 40 rows');
    expect(r.status).toBe(1);
  });

  it('input that is not Return does not start Herdr either: only a measurement that is large enough does', () => {
    const tooSmall = Array.from({ length: 1 + LAUNCHER_RESIZE_TRIES }, () => '24 80');
    const r = run(tooSmall, 'start herdr anyway\n');
    expect(r.started).toBeNull();
  });
});

describe('what the launcher is NOT: no fake geometry anywhere in what it does or what Herdr is started with', () => {
  it('Herdr is started with no size handed to it: no argument, and neither COLUMNS nor LINES is set for it', () => {
    const r = run(['60 200']);
    expect(r.started).toContain('argc=0');
    const text = herdrLauncherText(target, WINDOW);
    expect(text).not.toMatch(/COLUMNS|LINES/);
  });

  it('the launcher never starts Herdr in a window it measured too small, whatever the sequence of measurements', () => {
    const sequences = [['24 80'], ['FAIL'], ['48 189'], ['10 500'], ['500 10'], ['0 0']];
    for (const sizes of sequences) expect(run(sizes).started, sizes.join('|')).toBeNull();
  });
});
