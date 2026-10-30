/**
 * THE SEND RECORDER (the attach proof's app-side evidence): a record of everything this app's process sends through the Node APIs it wraps, across the WHOLE recorded interval, and a classification
 * of every entry inside it.
 *
 * Everything here runs on INJECTED targets: plain objects standing in for child_process, net.Socket.prototype, dgram.Socket.prototype, fs, fs.promises and the global fetch. No test starts a process,
 * opens a socket or writes a file.
 *
 * THE RULES IT STATES: the recorder FILTERS NOTHING (every wrapped call is an entry, a regular file read included); each wrapper records and then calls the captured original with the same receiver and
 * arguments and returns its result; uninstalling restores every original. The classifier labels every entry; the window assessment returns the WHOLE classified record, never a trimmed one, and a window
 * whose marks are missing or out of order supports nothing.
 *
 * WHAT IT DOES NOT ESTABLISH: anything about a path around the wrapped APIs (native code, another isolate, the kernel), or about any other actor. Its record is evidence about calls through these APIs in
 * this process only.
 */
import { describe, expect, it } from 'vitest';

import type { SendRecord, SendRecorderTargets } from '../src/di/sendRecorder.js';
import {
  assessSendWindow,
  classifySend,
  installSendRecorder,
  RECORDED_FILE_APIS,
  RECORDED_FILE_PROMISE_APIS,
  RECORDED_PROCESS_APIS,
} from '../src/di/sendRecorder.js';

type Fn = (this: unknown, ...args: unknown[]) => unknown;
interface Fakes {
  targets: SendRecorderTargets;
  originals: Map<string, Fn>;
  delegated: Array<{ name: string; self: unknown; args: unknown[] }>;
}
function fakes(): Fakes {
  const originals = new Map<string, Fn>();
  const delegated: Fakes['delegated'] = [];
  const make = (name: string): Fn => {
    const fn: Fn = function (this: unknown, ...args: unknown[]) {
      delegated.push({ name, self: this, args });
      return `result of ${name}`;
    };
    originals.set(name, fn);
    return fn;
  };
  const table = (prefix: string, names: readonly string[]) => Object.fromEntries(names.map((n) => [n, make(`${prefix}.${n}`)]));
  return {
    targets: {
      childProcess: table('child_process', RECORDED_PROCESS_APIS),
      socketPrototype: table('socket', ['connect', 'write']),
      datagramPrototype: table('datagram', ['send']),
      fileSystem: table('fs', RECORDED_FILE_APIS),
      fileSystemPromises: table('fs.promises', RECORDED_FILE_PROMISE_APIS),
      global: table('global', ['fetch']),
    },
    originals,
    delegated,
  };
}
function recorder(f: Fakes) {
  const records: SendRecord[] = [];
  let tick = 100;
  const handle = installSendRecorder(f.targets, (r) => records.push(r), () => tick++);
  return { records, handle };
}
const call = (table: Record<string, unknown>, name: string, self: unknown, ...args: unknown[]) => (table[name] as Fn).apply(self, args);

describe('the recorder records every call through the wrapped APIs, then hands it to the original unchanged', () => {
  it.each(RECORDED_PROCESS_APIS.map((n) => [n]))('child_process.%s: one entry naming the program and its arguments, the original called with the same receiver and arguments, its result returned', (name) => {
    const f = fakes();
    const { records } = recorder(f);
    const self = { receiver: true };
    const options = { env: { SECRET_TOKEN: 'do-not-record' } };
    const program = name === 'exec' || name === 'execSync' ? 'herdr agent list' : '/opt/proof/bin/herdr';
    const args = name === 'exec' || name === 'execSync' ? [program, options] : [program, ['agent', 'list'], options];
    const result = call(f.targets.childProcess, name, self, ...args);
    expect(result).toBe(`result of child_process.${name}`);
    expect(f.delegated).toEqual([{ name: `child_process.${name}`, self, args }]);
    expect(records).toHaveLength(1);
    expect(records[0]).toMatchObject({ kind: 'process', api: `child_process.${name}` });
    expect(records[0].detail).toEqual(name === 'exec' || name === 'execSync' ? { command: program } : { file: program, args: ['agent', 'list'] });
  });

  it('no option object is recorded: an environment (which can hold secrets) never reaches the record', () => {
    const f = fakes();
    const { records } = recorder(f);
    call(f.targets.childProcess, 'spawn', null, '/bin/x', ['a'], { env: { SECRET_TOKEN: 'do-not-record' } });
    call(f.targets.childProcess, 'exec', null, 'x a', { env: { SECRET_TOKEN: 'do-not-record' } });
    expect(JSON.stringify(records)).not.toContain('do-not-record');
  });

  it('a socket write names the target its socket connected to, and its byte count', () => {
    const f = fakes();
    const { records } = recorder(f);
    const unix = {};
    const tcp = {};
    const normalised = {};
    const unconnected = { remoteAddress: '10.0.0.2', remotePort: 443 };
    call(f.targets.socketPrototype, 'connect', unix, { path: '/opt/proof/home/.config/herdr/herdr.sock' });
    call(f.targets.socketPrototype, 'connect', tcp, 3100, '127.0.0.1');
    // net.connect and http call Socket.prototype.connect with ONE normalised argument list: [options, callback]
    call(f.targets.socketPrototype, 'connect', normalised, [{ host: 'example.test', port: 80 }, null]);
    expect(call(f.targets.socketPrototype, 'write', unix, 'héllo')).toBe('result of socket.write');
    call(f.targets.socketPrototype, 'write', tcp, new Uint8Array(7));
    call(f.targets.socketPrototype, 'write', normalised, 'x');
    call(f.targets.socketPrototype, 'write', unconnected, 'xy');
    expect(records.map((r) => [r.kind, r.detail])).toEqual([
      ['socket.connect', { target: 'unix:/opt/proof/home/.config/herdr/herdr.sock' }],
      ['socket.connect', { target: 'tcp:127.0.0.1:3100' }],
      ['socket.connect', { target: 'tcp:example.test:80' }],
      ['socket.write', { target: 'unix:/opt/proof/home/.config/herdr/herdr.sock', bytes: 6 }],
      ['socket.write', { target: 'tcp:127.0.0.1:3100', bytes: 7 }],
      ['socket.write', { target: 'tcp:example.test:80', bytes: 1 }],
      ['socket.write', { target: 'tcp:10.0.0.2:443', bytes: 2 }],
    ]);
    expect(f.delegated.map((d) => d.self)).toEqual([unix, tcp, normalised, unix, tcp, normalised, unconnected]);
  });

  it('a datagram send is recorded with its byte count', () => {
    const f = fakes();
    const { records } = recorder(f);
    call(f.targets.datagramPrototype, 'send', {}, new Uint8Array(3), 53, '10.0.0.1');
    expect(records.map((r) => [r.kind, r.detail])).toEqual([['datagram.send', { bytes: 3, to: [53, '10.0.0.1'] }]]);
    expect(f.delegated).toHaveLength(1);
  });

  it('a fetch is recorded with its address and method', () => {
    const f = fakes();
    const { records } = recorder(f);
    call(f.targets.global, 'fetch', undefined, 'http://127.0.0.1:9/x', { method: 'POST' });
    call(f.targets.global, 'fetch', undefined, 'http://127.0.0.1:9/y');
    expect(records.map((r) => [r.kind, r.detail])).toEqual([
      ['fetch', { url: 'http://127.0.0.1:9/x', method: 'POST' }],
      ['fetch', { url: 'http://127.0.0.1:9/y', method: 'GET' }],
    ]);
    expect(f.delegated).toHaveLength(2);
  });

  it.each([
    ...RECORDED_FILE_APIS.map((n) => ['fs', n] as const),
    ...RECORDED_FILE_PROMISE_APIS.map((n) => ['fs.promises', n] as const),
  ])('%s.%s is recorded for EVERY path, a regular file as well as a device: the recorder filters nothing', (where, name) => {
    const f = fakes();
    const { records } = recorder(f);
    const table = where === 'fs' ? f.targets.fileSystem : f.targets.fileSystemPromises;
    call(table, name, null, '/opt/proof/office/state.json', 'r');
    call(table, name, null, '/dev/ttys004', 'w');
    expect(records.map((r) => [r.kind, r.api, (r.detail as { path: string }).path])).toEqual([
      ['file', `${where}.${name}`, '/opt/proof/office/state.json'],
      ['file', `${where}.${name}`, '/dev/ttys004'],
    ]);
    expect(f.delegated).toHaveLength(2);
  });

  it('marks sit in the same stream as the calls, in order, each entry numbered and timed', () => {
    const f = fakes();
    const { records, handle } = recorder(f);
    handle.mark('typing-window-start');
    call(f.targets.childProcess, 'spawn', null, '/bin/x', []);
    handle.mark('typing-window-end');
    expect(records.map((r) => [r.seq, r.at, r.kind])).toEqual([
      [1, 100, 'marker'],
      [2, 101, 'process'],
      [3, 102, 'marker'],
    ]);
    expect(records[0].detail).toEqual({ label: 'typing-window-start' });
  });

  it('uninstalling restores every original exactly, and records nothing after', () => {
    const f = fakes();
    const { records, handle } = recorder(f);
    handle.uninstall();
    const tables = { child_process: f.targets.childProcess, socket: f.targets.socketPrototype, datagram: f.targets.datagramPrototype, fs: f.targets.fileSystem, 'fs.promises': f.targets.fileSystemPromises, global: f.targets.global };
    for (const [prefix, table] of Object.entries(tables)) for (const [name, fn] of Object.entries(table)) expect(fn, `${prefix}.${name}`).toBe(f.originals.get(`${prefix}.${name}`));
    call(f.targets.childProcess, 'spawn', null, '/bin/x', []);
    expect(records).toEqual([]);
  });
});

const EXPECTED = { herdrBin: '/opt/proof/bin/herdr', launcherDir: '/opt/proof/launch' };
const LAUNCHER = '/opt/proof/launch/herdr-attach-a1b2c3d4e5f6a7b8.command';
let seq = 0;
const entry = (kind: SendRecord['kind'], api: string, detail: unknown): SendRecord => ({ seq: ++seq, at: seq, kind, api, detail });

describe('the classifier labels every entry; only exact matches count as the launch and the at-use read', () => {
  it("Herdr's read-only agent list, exactly, is the at-use resolution; any other herdr command is an other process", () => {
    expect(classifySend(entry('process', 'child_process.execFile', { file: '/opt/proof/bin/herdr', args: ['agent', 'list'] }), EXPECTED)).toBe('resolution');
    expect(classifySend(entry('process', 'child_process.execFile', { file: '/opt/proof/bin/herdr', args: ['agent', 'list', '--watch'] }), EXPECTED)).toBe('process');
    expect(classifySend(entry('process', 'child_process.execFile', { file: '/opt/proof/bin/herdr', args: ['pane', 'list'] }), EXPECTED)).toBe('process');
    expect(classifySend(entry('process', 'child_process.execFile', { file: '/elsewhere/herdr', args: ['agent', 'list'] }), EXPECTED)).toBe('process');
    expect(classifySend(entry('process', 'child_process.exec', { command: '/opt/proof/bin/herdr agent list' }), EXPECTED)).toBe('process');
  });

  it('the open of a launcher in the launcher directory, and that launcher written exclusively, are the launch; nothing else is', () => {
    expect(classifySend(entry('process', 'child_process.spawn', { file: '/usr/bin/open', args: ['-a', 'Terminal', LAUNCHER] }), EXPECTED)).toBe('launch');
    expect(classifySend(entry('file', 'fs.promises.writeFile', { path: LAUNCHER, pathType: 'string', flags: 'wx' }), EXPECTED)).toBe('launch');
    expect(classifySend(entry('process', 'child_process.spawn', { file: '/usr/bin/open', args: ['-a', 'Terminal', '/tmp/other.command'] }), EXPECTED)).toBe('process');
    expect(classifySend(entry('process', 'child_process.spawn', { file: '/usr/bin/osascript', args: ['-e', 'tell application "Terminal" to do script "x"'] }), EXPECTED)).toBe('process');
    expect(classifySend(entry('file', 'fs.promises.writeFile', { path: LAUNCHER, pathType: 'string', flags: 'w' }), EXPECTED)).toBe('file');
    expect(classifySend(entry('file', 'fs.promises.writeFile', { path: '/opt/proof/launch-other/x.command', pathType: 'string', flags: 'wx' }), EXPECTED)).toBe('file');
  });

  it('a file under /dev is a device; any other file, a socket, a datagram, a fetch and a marker are labelled as what they are', () => {
    expect(classifySend(entry('file', 'fs.openSync', { path: '/dev/ttys004', pathType: 'string', flags: 'w' }), EXPECTED)).toBe('device-file');
    expect(classifySend(entry('file', 'fs.openSync', { path: '/opt/proof/office/state.json', pathType: 'string', flags: 'r' }), EXPECTED)).toBe('file');
    expect(classifySend(entry('socket.connect', 'socket.connect', { target: 'unix:/x' }), EXPECTED)).toBe('socket');
    expect(classifySend(entry('socket.write', 'socket.write', { target: 'unix:/x', bytes: 1 }), EXPECTED)).toBe('socket');
    expect(classifySend(entry('datagram.send', 'datagram.send', { bytes: 1, to: [] }), EXPECTED)).toBe('datagram');
    expect(classifySend(entry('fetch', 'fetch', { url: 'http://x', method: 'GET' }), EXPECTED)).toBe('fetch');
    expect(classifySend(entry('marker', 'mark', { label: 'x' }), EXPECTED)).toBe('marker');
  });
});

describe('the window assessment keeps the WHOLE record and classifies inside it; it never trims to what supports the claim', () => {
  const start = () => entry('marker', 'mark', { label: 'typing-window-start' });
  const end = () => entry('marker', 'mark', { label: 'typing-window-end' });
  const resolution = () => entry('process', 'child_process.execFile', { file: '/opt/proof/bin/herdr', args: ['agent', 'list'] });
  const launch = () => entry('process', 'child_process.spawn', { file: '/usr/bin/open', args: ['-a', 'Terminal', LAUNCHER] });
  const read = () => entry('file', 'fs.readFile', { path: '/opt/proof/office/state.json', pathType: 'string', flags: 'r' });

  it('every entry, inside the window and outside it, is in the result, in order, with its class', () => {
    const records = [resolution(), launch(), start(), read(), end(), entry('socket.write', 'socket.write', { target: 'tcp:x:1', bytes: 2 })];
    const assessed = assessSendWindow(records, EXPECTED, 'typing-window-start', 'typing-window-end');
    expect(assessed.classified.map((c) => c.seq)).toEqual(records.map((r) => r.seq));
    expect(assessed.classified.map((c) => c.class)).toEqual(['resolution', 'launch', 'marker', 'file', 'marker', 'socket']);
    expect(assessed.classified.map((c) => c.inWindow)).toEqual([false, false, true, true, true, false]);
  });

  it('the launch, the at-use read and plain file activity inside the window do not block it; nothing that could carry input appears, so it is supported', () => {
    const assessed = assessSendWindow([start(), resolution(), launch(), read(), end()], EXPECTED, 'typing-window-start', 'typing-window-end');
    expect(assessed.blocking).toEqual([]);
    expect(assessed.supported).toBe(true);
  });

  it.each([
    ['another process', () => entry('process', 'child_process.spawn', { file: '/usr/bin/osascript', args: ['-e', 'x'] })],
    ['a device file', () => entry('file', 'fs.openSync', { path: '/dev/ttys004', pathType: 'string', flags: 'w' })],
    ['a socket write', () => entry('socket.write', 'socket.write', { target: 'unix:/x', bytes: 1 })],
    ['a datagram', () => entry('datagram.send', 'datagram.send', { bytes: 1, to: [] })],
    ['a fetch', () => entry('fetch', 'fetch', { url: 'http://x', method: 'GET' })],
  ])('%s inside the window blocks it, and is named', (_name, make) => {
    // made in stream order: each entry's number is taken when it is made
    const opened = start();
    const bad = make();
    const closed = end();
    const assessed = assessSendWindow([opened, bad, closed], EXPECTED, 'typing-window-start', 'typing-window-end');
    expect(assessed.supported).toBe(false);
    expect(assessed.blocking.map((b) => b.seq)).toEqual([bad.seq]);
  });

  it('a window with a missing mark, or an end before its start, supports nothing', () => {
    expect(assessSendWindow([start(), read()], EXPECTED, 'typing-window-start', 'typing-window-end').supported).toBe(false);
    expect(assessSendWindow([read(), end()], EXPECTED, 'typing-window-start', 'typing-window-end').supported).toBe(false);
    expect(assessSendWindow([end(), read(), start()], EXPECTED, 'typing-window-start', 'typing-window-end').supported).toBe(false);
    expect(assessSendWindow([start(), start(), end()], EXPECTED, 'typing-window-start', 'typing-window-end').supported).toBe(false);
    expect(assessSendWindow([start(), read()], EXPECTED, 'typing-window-start', 'typing-window-end').reasons).toEqual(['the window has no single start mark before a single end mark']);
  });
});

describe('THE SEAM, wrapper to verdict: a file path the classifier cannot reduce to a plain absolute string path REFUSES the window (round 40)', () => {
  const through = (where: 'fs' | 'fs.promises', name: string, filePath: unknown) => {
    const f = fakes();
    const { records, handle } = recorder(f);
    handle.mark('typing-window-start');
    call(where === 'fs' ? f.targets.fileSystem : f.targets.fileSystemPromises, name, null, filePath, 'w');
    handle.mark('typing-window-end');
    const written = records.find((r) => r.kind === 'file');
    if (written === undefined) throw new Error('the wrapped write is not in the record');
    return { written, assessed: assessSendWindow(records, EXPECTED, 'typing-window-start', 'typing-window-end') };
  };

  it.each([
    ['a URL naming the terminal device', 'fs', 'writeFile', new URL('file:///dev/ttys004'), 'URL'],
    ['a URL naming a regular file (a URL is never normalised, so it is never trusted)', 'fs', 'openSync', new URL('file:///opt/proof/office/state.json'), 'URL'],
    ['a Buffer naming the terminal device', 'fs', 'appendFileSync', Buffer.from('/dev/ttys004'), 'Buffer'],
    ['a Buffer naming a regular file', 'fs.promises', 'writeFile', Buffer.from('/opt/proof/office/state.json'), 'Buffer'],
    ['an open file descriptor', 'fs', 'writeFileSync', 1, 'descriptor'],
    ['a relative path', 'fs', 'openSync', 'dev/ttys004', 'string'],
    ['a path with parent segments', 'fs', 'openSync', '/opt/proof/../../dev/ttys004', 'string'],
    ['a path with an empty segment', 'fs', 'openSync', '//dev/ttys004', 'string'],
  ] as const)('%s, written through the wrapper, is recorded, classified unresolved, and the window is NOT supported, with the reason', (_name, where, name, filePath, given) => {
    const { written, assessed } = through(where, name, filePath);
    expect((written.detail as { pathType: string }).pathType).toBe(given);
    expect(assessed.supported).toBe(false);
    expect(assessed.blocking.map((b) => [b.seq, b.class])).toEqual([[written.seq, 'unresolved-path']]);
    expect(assessed.reasons).toEqual([
      `entry ${written.seq}, ${written.api}: a file path that is not a plain absolute path, so whether it names a device cannot be told (given as ${given})`,
    ]);
  });

  it('CONTROLS through the same seam: a plain absolute regular file supports the window; the plain device path blocks it, as a device', () => {
    const plain = through('fs', 'writeFile', '/opt/proof/office/state.json');
    expect(plain.assessed.supported).toBe(true);
    expect(plain.assessed.reasons).toEqual([]);
    const device = through('fs', 'writeFile', '/dev/ttys004');
    expect(device.assessed.supported).toBe(false);
    expect(device.assessed.blocking.map((b) => b.class)).toEqual(['device-file']);
  });
});
