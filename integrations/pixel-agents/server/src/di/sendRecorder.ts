/**
 * THE SEND RECORDER: the attach proof's APP-SIDE evidence. It records everything THIS PROCESS sends through the Node APIs it wraps, from install to uninstall, and classifies every entry.
 *
 * WHAT IT WRAPS (each named list below is the whole set): the child_process functions that start a program; net.Socket.prototype `connect` and `write` (TCP, Unix sockets and every HTTP or WebSocket
 * client built on them); dgram.Socket.prototype `send`; the fs and fs.promises entry points that open or write a file; and the global `fetch`.
 *
 * THE RULES:
 * - It FILTERS NOTHING. Every wrapped call is an entry, a regular file read included. Classification happens afterwards, over the whole record.
 * - Each wrapper records, then calls the CAPTURED ORIGINAL with the same receiver and arguments and returns its result. Uninstalling restores every original exactly; a wrapper still referenced
 *   somewhere after that records nothing.
 * - No option object is recorded, so an environment (which can hold secrets) never reaches the record: a program is recorded as its file and argument list, a file as its path and flags.
 * - The typing window is two MARKS in the same stream. `assessSendWindow` returns the WHOLE classified record, never a trimmed one; a window whose marks are missing, repeated or out of order supports
 *   nothing.
 *
 * ITS REACH, stated: it sees calls through the APIs it wraps, in this process, after it is installed. It does not see a native path around them, another isolate (a worker), a call bound before it was
 * installed, writes through a file descriptor (fs.write on an fd it did not see opened), or anything kernel-wide. It says nothing about any other actor.
 */

export const RECORDED_PROCESS_APIS = ['spawn', 'spawnSync', 'exec', 'execSync', 'execFile', 'execFileSync', 'fork'] as const;
export const RECORDED_FILE_APIS = ['open', 'openSync', 'writeFile', 'writeFileSync', 'appendFile', 'appendFileSync', 'createWriteStream'] as const;
export const RECORDED_FILE_PROMISE_APIS = ['open', 'writeFile', 'appendFile'] as const;

export type SendKind = 'process' | 'socket.connect' | 'socket.write' | 'datagram.send' | 'fetch' | 'file' | 'marker';
export interface SendRecord {
  /** 1, 2, 3, ... in the order the entries happened. */
  seq: number;
  /** The clock's value when it happened. */
  at: number;
  kind: SendKind;
  /** The wrapped API, as `<module>.<name>`. */
  api: string;
  detail: unknown;
}
/** The objects whose functions are wrapped: in the proof, the real modules and prototypes; in tests, stand-ins. */
export interface SendRecorderTargets {
  childProcess: Record<string, unknown>;
  socketPrototype: Record<string, unknown>;
  datagramPrototype: Record<string, unknown>;
  fileSystem: Record<string, unknown>;
  fileSystemPromises: Record<string, unknown>;
  global: Record<string, unknown>;
}

type Wrapped = (this: unknown, ...args: unknown[]) => unknown;

const text = (value: unknown): string => {
  if (typeof value === 'string') return value;
  if (value instanceof URL) return value.href;
  if (typeof value === 'number') return `fd:${value}`;
  return String(value);
};

/** What a file argument WAS before `text` made it a string: the classifier trusts only a plain `string`. */
const pathType = (value: unknown): string => {
  if (typeof value === 'string') return 'string';
  if (value instanceof URL) return 'URL';
  if (Buffer.isBuffer(value)) return 'Buffer';
  if (typeof value === 'number') return 'descriptor';
  return typeof value;
};

const byteCount = (chunk: unknown, encoding: unknown): number | null => {
  if (typeof chunk === 'string') return Buffer.byteLength(chunk, typeof encoding === 'string' ? (encoding as BufferEncoding) : 'utf8');
  if (ArrayBuffer.isView(chunk)) return chunk.byteLength;
  return null;
};

function processDetail(name: string, args: unknown[]): unknown {
  if (name === 'exec' || name === 'execSync') return { command: text(args[0]) };
  return { file: text(args[0]), args: Array.isArray(args[1]) ? args[1].map(text) : [] };
}

function connectTarget(args: unknown[]): string {
  // net.connect and http pass ONE normalised list, [options, callback]
  const first = Array.isArray(args[0]) ? args[0][0] : args[0];
  if (typeof first === 'string') return `unix:${first}`;
  if (typeof first === 'number') return `tcp:${typeof args[1] === 'string' ? args[1] : 'localhost'}:${first}`;
  if (typeof first === 'object' && first !== null) {
    const options = first as { path?: unknown; port?: unknown; host?: unknown };
    if (typeof options.path === 'string') return `unix:${options.path}`;
    if (options.port !== undefined) return `tcp:${typeof options.host === 'string' ? options.host : 'localhost'}:${String(options.port)}`;
  }
  return 'unknown';
}

function fileFlags(name: string, args: unknown[]): unknown {
  if (name === 'open' || name === 'openSync') return typeof args[1] === 'string' || typeof args[1] === 'number' ? args[1] : 'r';
  if (name === 'createWriteStream') {
    const options = args[1];
    return typeof options === 'object' && options !== null && 'flags' in options ? (options as { flags: unknown }).flags : 'w';
  }
  const options = args[2];
  const fallback = name.startsWith('append') ? 'a' : 'w';
  if (typeof options === 'object' && options !== null && 'flag' in options) return (options as { flag: unknown }).flag;
  return fallback;
}

function fetchDetail(args: unknown[]): unknown {
  const [input, init] = args;
  const url = typeof input === 'string' ? input : input instanceof URL ? input.href : typeof input === 'object' && input !== null && 'url' in input ? text((input as { url: unknown }).url) : text(input);
  const fromInit = typeof init === 'object' && init !== null && 'method' in init ? (init as { method: unknown }).method : undefined;
  const fromInput = typeof input === 'object' && input !== null && 'method' in input ? (input as { method: unknown }).method : undefined;
  return { url, method: typeof fromInit === 'string' ? fromInit : typeof fromInput === 'string' ? fromInput : 'GET' };
}

/** Install the recorder on `targets`. Every entry goes to `sink`, numbered, with `clock()`'s value. A sink that throws makes the wrapped call throw: lost evidence is never silent. */
export function installSendRecorder(targets: SendRecorderTargets, sink: (record: SendRecord) => void, clock: () => number): { mark(label: string): void; uninstall(): void } {
  let seq = 0;
  let active = true;
  const restores: Array<() => void> = [];
  const connected = new WeakMap<object, string>();
  const emit = (kind: SendKind, api: string, detail: unknown) => {
    if (!active) return;
    seq += 1;
    sink({ seq, at: clock(), kind, api, detail });
  };
  const wrap = (table: Record<string, unknown>, label: string, name: string, before: (self: unknown, args: unknown[]) => void) => {
    const original = table[name];
    if (typeof original !== 'function') throw new Error(`the send recorder cannot wrap ${label}.${name}: it is not a function`);
    const wrapped: Wrapped = function (this: unknown, ...args: unknown[]) {
      before(this, args);
      return (original as Wrapped).apply(this, args);
    };
    table[name] = wrapped;
    restores.push(() => {
      table[name] = original;
    });
  };

  for (const name of RECORDED_PROCESS_APIS)
    wrap(targets.childProcess, 'child_process', name, (_self, args) => emit('process', `child_process.${name}`, processDetail(name, args)));
  wrap(targets.socketPrototype, 'socket', 'connect', (self, args) => {
    const target = connectTarget(args);
    if (typeof self === 'object' && self !== null) connected.set(self, target);
    emit('socket.connect', 'socket.connect', { target });
  });
  wrap(targets.socketPrototype, 'socket', 'write', (self, args) => {
    let target = typeof self === 'object' && self !== null ? connected.get(self) : undefined;
    if (target === undefined) {
      const remote = (self ?? {}) as { remoteAddress?: unknown; remotePort?: unknown };
      target = typeof remote.remoteAddress === 'string' ? `tcp:${remote.remoteAddress}:${String(remote.remotePort)}` : 'unknown';
    }
    emit('socket.write', 'socket.write', { target, bytes: byteCount(args[0], args[1]) });
  });
  wrap(targets.datagramPrototype, 'datagram', 'send', (_self, args) =>
    emit('datagram.send', 'datagram.send', { bytes: byteCount(args[0], undefined), to: args.slice(1).filter((a) => typeof a === 'number' || typeof a === 'string') }),
  );
  for (const name of RECORDED_FILE_APIS)
    wrap(targets.fileSystem, 'fs', name, (_self, args) => emit('file', `fs.${name}`, { path: text(args[0]), pathType: pathType(args[0]), flags: fileFlags(name, args) }));
  for (const name of RECORDED_FILE_PROMISE_APIS)
    wrap(targets.fileSystemPromises, 'fs.promises', name, (_self, args) =>
      emit('file', `fs.promises.${name}`, { path: text(args[0]), pathType: pathType(args[0]), flags: fileFlags(name, args) }),
    );
  wrap(targets.global, 'global', 'fetch', (_self, args) => emit('fetch', 'fetch', fetchDetail(args)));

  return {
    mark: (label: string) => emit('marker', 'mark', { label }),
    uninstall: () => {
      active = false;
      for (const restore of restores.reverse()) restore();
    },
  };
}

/**
 * What an entry IS. Only exact matches are the launch and the at-use read; everything else is labelled by its kind.
 * `unresolved-path`: a file entry whose path the classifier cannot reduce to a PLAIN ABSOLUTE STRING PATH (given as a URL, a Buffer or a descriptor, or a string that is relative or has an
 * empty, `.` or `..` segment). It is REFUSED, never normalised: whether it names a device cannot be told safely, and a wrong normalisation would certify a falsehood.
 */
export type SendClass = 'marker' | 'resolution' | 'launch' | 'process' | 'device-file' | 'unresolved-path' | 'file' | 'socket' | 'datagram' | 'fetch';
/** The classes that could carry input to a process or a terminal through the wrapped APIs, or whose nature cannot be told. One of them inside the window means the window does not support a no-input claim. */
export const BLOCKING_SEND_CLASSES: readonly SendClass[] = ['process', 'device-file', 'unresolved-path', 'socket', 'datagram', 'fetch'];
const BLOCKING_REASONS: Readonly<Record<string, string>> = {
  process: 'a program other than the at-use read and the launch was started',
  'device-file': 'a device file was opened or written',
  'unresolved-path': 'a file path that is not a plain absolute path, so whether it names a device cannot be told',
  socket: 'a socket was connected or written',
  datagram: 'a datagram was sent',
  fetch: 'a network request was made',
};
export interface SendExpectation {
  /** The herdr executable the launch route was given. */
  herdrBin: string;
  /** The launcher directory the launch route was given. */
  launcherDir: string;
}
export type ClassifiedSend = SendRecord & { class: SendClass; inWindow: boolean };
export interface SendWindowAssessment {
  /** EVERY entry, in order, classified: nothing is dropped. */
  classified: ClassifiedSend[];
  window: { startSeq: number; endSeq: number } | null;
  /** The entries inside the window whose class is blocking. */
  blocking: ClassifiedSend[];
  /** Why the window is not supported: one plain reason per blocking entry, or the reason the window itself is unusable. Empty exactly when it is supported. */
  reasons: string[];
  supported: boolean;
}

/** A path the classifier understands: a string, absolute, with no empty, `.` or `..` segment and no NUL. Anything else is refused, not normalised. */
const plainAbsolutePath = (value: unknown): value is string =>
  typeof value === 'string' && value.startsWith('/') && !value.includes('\0') && value.slice(1).split('/').every((segment) => segment !== '' && segment !== '.' && segment !== '..');

const LAUNCHER_NAME = /^herdr-attach-[0-9a-f]{8,64}\.command$/;
const isLauncher = (filePath: unknown, expected: SendExpectation): boolean => {
  if (typeof filePath !== 'string' || !filePath.startsWith(`${expected.launcherDir}/`)) return false;
  return LAUNCHER_NAME.test(filePath.slice(expected.launcherDir.length + 1));
};
const sameList = (a: unknown, b: readonly string[]): boolean => Array.isArray(a) && a.length === b.length && a.every((v, i) => v === b[i]);

export function classifySend(record: SendRecord, expected: SendExpectation): SendClass {
  const detail = (record.detail ?? {}) as { file?: unknown; args?: unknown; path?: unknown; pathType?: unknown; flags?: unknown };
  switch (record.kind) {
    case 'marker':
      return 'marker';
    case 'process': {
      if (detail.file === expected.herdrBin && sameList(detail.args, ['agent', 'list'])) return 'resolution';
      if (detail.file === '/usr/bin/open' && Array.isArray(detail.args) && detail.args.length === 3 && detail.args[0] === '-a' && detail.args[1] === 'Terminal' && isLauncher(detail.args[2], expected))
        return 'launch';
      return 'process';
    }
    case 'file': {
      if (detail.pathType !== 'string' || !plainAbsolutePath(detail.path)) return 'unresolved-path';
      const filePath = detail.path;
      if (filePath === '/dev' || filePath.startsWith('/dev/')) return 'device-file';
      if (detail.flags === 'wx' && isLauncher(filePath, expected)) return 'launch';
      return 'file';
    }
    case 'socket.connect':
    case 'socket.write':
      return 'socket';
    case 'datagram.send':
      return 'datagram';
    case 'fetch':
      return 'fetch';
  }
}

/** The whole record, classified, and the window between the ONE start mark and the ONE end mark (both included). */
export function assessSendWindow(records: readonly SendRecord[], expected: SendExpectation, startLabel: string, endLabel: string): SendWindowAssessment {
  const marked = (label: string) => records.filter((r) => r.kind === 'marker' && (r.detail as { label?: unknown } | null)?.label === label);
  const starts = marked(startLabel);
  const ends = marked(endLabel);
  const window = starts.length === 1 && ends.length === 1 && starts[0].seq < ends[0].seq ? { startSeq: starts[0].seq, endSeq: ends[0].seq } : null;
  const classified = records.map((r) => ({ ...r, class: classifySend(r, expected), inWindow: window !== null && r.seq >= window.startSeq && r.seq <= window.endSeq }));
  const blocking = classified.filter((c) => c.inWindow && BLOCKING_SEND_CLASSES.includes(c.class));
  const reasons = [
    ...(window === null ? ['the window has no single start mark before a single end mark'] : []),
    ...blocking.map((b) => {
      const given = b.class === 'unresolved-path' ? ` (given as ${String((b.detail as { pathType?: unknown } | null)?.pathType)})` : '';
      return `entry ${b.seq}, ${b.api}: ${BLOCKING_REASONS[b.class]}${given}`;
    }),
  ];
  return { classified, window, blocking, reasons, supported: window !== null && blocking.length === 0 };
}
