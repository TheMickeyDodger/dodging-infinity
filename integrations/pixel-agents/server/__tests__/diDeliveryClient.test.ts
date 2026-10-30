/**
 * The office delivery adapter's HTTP client (server/src/di/deliveryClient.ts), with NO SOCKET.
 *
 * Every connection is an IN-MEMORY DUPLEX, handed to node's REAL http client through the client's injected `connect`. Node writes
 * real HTTP/1.1 bytes into it; the test reads and parses them, writes raw answer bytes back, and node's real parser reads those.
 * No OS socket, port, listener or loopback exists.
 *
 * WHAT THIS ESTABLISHES: the client's behaviour against SCRIPTED ANSWERS over node's real HTTP machinery. WHAT IT DOES NOT: its
 * behaviour against the real adapter process, which would need a real socket (excluded).
 *
 * The adapter's rules asserted below are read from office_delivery/http_adapter.py (`admit`). Two of them, the body bound and the
 * delivery-id pattern, are read from that source by the parity pins; no Python runs.
 */
import * as fs from 'fs';
import * as http from 'http';
import * as path from 'path';
import { Duplex } from 'stream';
import { describe, expect, it } from 'vitest';

import * as clientModule from '../src/di/deliveryClient.js';
import {
  DELIVERY_ID_PATTERN,
  DELIVERY_STATUS_TIMEOUT_MS,
  MAX_DELIVERY_REQUEST_BODY_BYTES,
  MAX_DELIVERY_RESPONSE_BYTES,
  requestDelivery,
} from '../src/di/deliveryClient.js';
import type { DeliveryCall, DeliveryResult } from '../src/di/deliveryRequests.js';

const ADAPTER_SOURCE = path.resolve(__dirname, '../../../../office_delivery/http_adapter.py');
const PORT = 47123;
const TOKEN = `office-delivery-token-${'f'.repeat(40)}`;
const ENDPOINT = { port: PORT, token: TOKEN };
const ID = `prd-${'a'.repeat(24)}`;
const STATUS: DeliveryCall = { method: 'GET', path: `/deliveries/${ID}/status`, body: null };
const CONFIRMATION = {
  delivery_id: ID,
  revision: 3,
  candidate_identity_digest_sha256: 'c'.repeat(64),
  step: 'COMMIT',
};
const ADVANCE: DeliveryCall = {
  method: 'POST',
  path: `/deliveries/${ID}/advance-once`,
  body: CONFIRMATION,
};
const INVALID: DeliveryResult = { kind: 'transport_error', problem: 'delivery_request_invalid' };

/** One in-memory connection: node's http client holds this end; the test reads what it wrote and pushes the answer. */
class MemorySocket extends Duplex {
  private readonly chunks: Buffer[] = [];
  onWrite: (() => void) | null = null;

  _write(chunk: Buffer, _encoding: BufferEncoding, done: (error?: Error | null) => void): void {
    this.chunks.push(Buffer.from(chunk));
    done();
    this.onWrite?.();
  }

  _read(): void {}

  setNoDelay(): this {
    return this;
  }

  setKeepAlive(): this {
    return this;
  }

  setTimeout(): this {
    return this;
  }

  ref(): this {
    return this;
  }

  unref(): this {
    return this;
  }

  /** Everything node wrote, one character per byte. */
  get sent(): string {
    return Buffer.concat(this.chunks).toString('latin1');
  }

  answer(raw: string): void {
    this.push(Buffer.from(raw, 'utf-8'));
  }

  close(): void {
    this.push(null);
  }
}

interface Wiring {
  ports: number[];
  sockets: MemorySocket[];
  connect: (port: number) => Duplex;
}

function wiring(make: () => MemorySocket = () => new MemorySocket()): Wiring {
  const w: Wiring = {
    ports: [],
    sockets: [],
    connect: (port) => {
      w.ports.push(port);
      const socket = make();
      w.sockets.push(socket);
      return socket;
    },
  };
  return w;
}

interface Received {
  line: string;
  headers: Array<[string, string]>;
  body: string;
}

/** The request node wrote, once its head and the body its Content-Length declares have arrived. */
function received(socket: MemorySocket): Promise<Received> {
  return new Promise((resolve) => {
    const check = (): void => {
      const raw = socket.sent;
      const end = raw.indexOf('\r\n\r\n');
      if (end < 0) return;
      const [line, ...rest] = raw.slice(0, end).split('\r\n');
      const headers = rest.map((h): [string, string] => {
        const i = h.indexOf(':');
        return [h.slice(0, i), h.slice(i + 1).trim()];
      });
      const declared = headers.find(([name]) => name.toLowerCase() === 'content-length');
      const body = raw.slice(end + 4);
      if (body.length < Number(declared?.[1] ?? 0)) return;
      socket.onWrite = null;
      resolve({ line, headers, body: Buffer.from(body, 'latin1').toString('utf-8') });
    };
    socket.onWrite = check;
    check();
  });
}

const values = (headers: Array<[string, string]>, name: string): string[] =>
  headers.filter(([n]) => n.toLowerCase() === name).map(([, v]) => v);

const answer = (status: number, body: string): string =>
  `HTTP/1.1 ${status} X\r\nContent-Type: application/json\r\nContent-Length: ${Buffer.byteLength(body, 'utf-8')}\r\n\r\n${body}`;

const wait = (ms: number): Promise<void> => new Promise((resolve) => setTimeout(resolve, ms));

/** A connection that fails the way the operating system would report it, with words that must never reach a result. */
const failing = (code: string) => (): MemorySocket => {
  const socket = new MemorySocket();
  setImmediate(() =>
    socket.destroy(Object.assign(new Error(`${code} 127.0.0.1:${PORT} /Users/x/secret ${TOKEN}`), { code })),
  );
  return socket;
};

describe('the harness: a real node HTTP exchange over an in-memory duplex, with no socket', () => {
  it("node's own http client writes a real request into the duplex and reads the raw answer back", async () => {
    const socket = new MemorySocket();
    const got = new Promise<{ status: number; body: string }>((resolve, reject) => {
      const request = http.request(
        { method: 'GET', path: '/x', headers: { Host: 'h' }, setHost: false, createConnection: () => socket },
        (response) => {
          let body = '';
          response.setEncoding('utf-8');
          response.on('data', (chunk: string) => (body += chunk));
          response.on('end', () => resolve({ status: response.statusCode ?? 0, body }));
        },
      );
      request.on('error', reject);
      request.end();
    });
    const request = await received(socket);
    expect(request.line).toBe('GET /x HTTP/1.1');
    socket.answer(answer(200, '{"ok":true}'));
    expect(await got).toEqual({ status: 200, body: '{"ok":true}' });
  });
});

describe('parity with the adapter, read from its source (no Python runs)', () => {
  const source = fs.readFileSync(ADAPTER_SOURCE, 'utf-8');

  it("the request-body bound is the adapter's own MAX_BODY_BYTES", () => {
    const bound = /^MAX_BODY_BYTES = (\d+)$/m.exec(source);
    expect(bound).not.toBeNull();
    expect(Number(bound![1])).toBe(MAX_DELIVERY_REQUEST_BODY_BYTES);
  });

  it("the delivery-id pattern, the two routes and the loopback host are the adapter's", () => {
    const route = /^_DELIVERY_ROUTE = re\.compile\(r"\^\/deliveries\/\(([^)]+)\)\/\(([^)]+)\)\$"\)$/m.exec(source);
    expect(route).not.toBeNull();
    expect(route![1]).toBe(DELIVERY_ID_PATTERN);
    expect(route![2]).toBe('status|advance-once');
    expect(source).toMatch(/^LOOPBACK_HOST = "127\.0\.0\.1"$/m);
  });
});

describe("the wire: what node actually sends meets the adapter's admission rules", () => {
  it('a status read: GET, the Host the adapter requires, exactly one bearer, no body, no Origin, Cookie or Transfer-Encoding', async () => {
    const w = wiring();
    const pending = requestDelivery(ENDPOINT, STATUS, { connect: w.connect });
    expect(w.ports).toEqual([PORT]);
    const request = await received(w.sockets[0]);
    expect(request.line).toBe(`GET /deliveries/${ID}/status HTTP/1.1`);
    expect(values(request.headers, 'host')).toEqual([`127.0.0.1:${PORT}`]);
    expect(values(request.headers, 'authorization')).toEqual([`Bearer ${TOKEN}`]);
    expect(values(request.headers, 'accept')).toEqual(['application/json']);
    for (const name of ['origin', 'cookie', 'transfer-encoding', 'content-length', 'content-type'])
      expect(values(request.headers, name), name).toEqual([]);
    expect(request.body).toBe('');
    w.sockets[0].answer(answer(200, '{"ok":true,"status":{}}'));
    expect(await pending).toEqual({ kind: 'http', status: 200, body: { ok: true, status: {} } });
  });

  it('an advance: POST, Content-Type exactly application/json, an exact Content-Length, and exactly the confirmation as its body', async () => {
    const w = wiring();
    const pending = requestDelivery(ENDPOINT, ADVANCE, { connect: w.connect });
    expect(w.ports).toEqual([PORT]);
    const request = await received(w.sockets[0]);
    expect(request.line).toBe(`POST /deliveries/${ID}/advance-once HTTP/1.1`);
    expect(values(request.headers, 'host')).toEqual([`127.0.0.1:${PORT}`]);
    expect(values(request.headers, 'authorization')).toEqual([`Bearer ${TOKEN}`]);
    expect(values(request.headers, 'content-type')).toEqual(['application/json']);
    expect(values(request.headers, 'content-length')).toEqual([String(Buffer.byteLength(request.body, 'utf-8'))]);
    for (const name of ['origin', 'cookie', 'transfer-encoding'])
      expect(values(request.headers, name), name).toEqual([]);
    expect(JSON.parse(request.body)).toEqual(CONFIRMATION);
    w.sockets[0].answer(answer(200, '{"ok":true,"result":{"advanced":true}}'));
    expect(await pending).toEqual({ kind: 'http', status: 200, body: { ok: true, result: { advanced: true } } });
  });

  it('the token goes on the wire ONCE, in the Authorization header, and nowhere else', async () => {
    for (const call of [STATUS, ADVANCE]) {
      const w = wiring();
      const pending = requestDelivery(ENDPOINT, call, { connect: w.connect });
      expect(w.ports, call.path).toEqual([PORT]);
      await received(w.sockets[0]);
      const sent = w.sockets[0].sent;
      expect(sent.split(TOKEN).length - 1, call.path).toBe(1);
      const carrying = sent.split('\r\n').filter((line) => line.includes(TOKEN));
      expect(carrying, call.path).toEqual([`Authorization: Bearer ${TOKEN}`]);
      w.sockets[0].answer(answer(200, '{"ok":true}'));
      await pending;
    }
  });

  it('each request opens its own connection to the endpoint port; none is reused', async () => {
    const w = wiring();
    for (const [i, call] of [STATUS, ADVANCE].entries()) {
      const pending = requestDelivery(ENDPOINT, call, { connect: w.connect });
      expect(w.ports, call.path).toEqual(Array(i + 1).fill(PORT));
      const socket = w.sockets[i];
      await received(socket);
      socket.answer(answer(200, '{"ok":true}'));
      await pending;
    }
    expect(w.ports).toEqual([PORT, PORT]);
    expect(w.sockets[0]).not.toBe(w.sockets[1]);
  });
});

describe('refused HERE, with no connection made', () => {
  it("anything that is not exactly one of the relay's two routes, with its own method and body", async () => {
    const bad: DeliveryCall[] = [
      { method: 'GET', path: `/deliveries/${ID}/advance-once`, body: null },
      { method: 'POST', path: `/deliveries/${ID}/status`, body: {} },
      { method: 'GET', path: `/deliveries/${ID}/status`, body: {} },
      { method: 'POST', path: `/deliveries/${ID}/advance-once`, body: null },
      { method: 'GET', path: '/healthz', body: null },
      { method: 'GET', path: `/deliveries/${ID}/status/`, body: null },
      { method: 'GET', path: `/deliveries/${ID}/status?x=1`, body: null },
      { method: 'GET', path: `/deliveries/PRD-${'a'.repeat(24)}/status`, body: null },
      { method: 'GET', path: `/deliveries/prd-${'a'.repeat(23)}/status`, body: null },
      { method: 'GET', path: `/deliveries/${ID}/../status`, body: null },
      { method: 'GET', path: `//deliveries/${ID}/status`, body: null },
      { method: 'POST', path: `/deliveries/${ID}/advance`, body: {} },
      { method: 'PUT' as 'POST', path: `/deliveries/${ID}/advance-once`, body: {} },
    ];
    const w = wiring();
    for (const call of bad) {
      // No connection is checked BEFORE awaiting: a call let through connects at once, and would otherwise wait for an answer.
      const pending = requestDelivery(ENDPOINT, call, { connect: w.connect });
      expect(w.ports, `${call.method} ${call.path}`).toEqual([]);
      expect(await pending, `${call.method} ${call.path}`).toEqual(INVALID);
    }
  });

  it("a body over the adapter's bound, counted in BYTES, is refused; one exactly at the bound is sent", async () => {
    const atBound = { pad: 'x'.repeat(MAX_DELIVERY_REQUEST_BODY_BYTES - 10) };
    expect(Buffer.byteLength(JSON.stringify(atBound), 'utf-8')).toBe(MAX_DELIVERY_REQUEST_BODY_BYTES);
    // Fewer characters than the bound, more bytes: 2044 two-byte characters.
    const overBound = { pad: 'é'.repeat((MAX_DELIVERY_REQUEST_BODY_BYTES - 10) / 2 + 1) };
    expect(JSON.stringify(overBound).length).toBeLessThan(MAX_DELIVERY_REQUEST_BODY_BYTES);
    expect(Buffer.byteLength(JSON.stringify(overBound), 'utf-8')).toBeGreaterThan(MAX_DELIVERY_REQUEST_BODY_BYTES);
    const w = wiring();
    const sent = requestDelivery(ENDPOINT, { ...ADVANCE, body: atBound }, { connect: w.connect });
    expect(w.ports).toEqual([PORT]);
    await received(w.sockets[0]);
    w.sockets[0].answer(answer(200, '{"ok":true}'));
    expect(await sent).toMatchObject({ kind: 'http', status: 200 });
    const refused = requestDelivery(ENDPOINT, { ...ADVANCE, body: overBound }, { connect: w.connect });
    expect(w.ports).toEqual([PORT]);
    expect(await refused).toEqual(INVALID);
  });

  it('an endpoint whose port is not a port, or whose token is empty or not plain printable ASCII', async () => {
    const w = wiring();
    for (const port of [0, -1, 65536, 1.5, Number.NaN]) {
      const pending = requestDelivery({ port, token: TOKEN }, STATUS, { connect: w.connect });
      expect(w.ports, String(port)).toEqual([]);
      expect(await pending, String(port)).toEqual(INVALID);
    }
    for (const token of ['', 'a b', 'a\tb', 'a\nb', 'a\rb', 'a\u0000b', 'a\u007fb', 'tök', 7 as unknown as string]) {
      const pending = requestDelivery({ port: PORT, token }, STATUS, { connect: w.connect });
      expect(w.ports, JSON.stringify(token)).toEqual([]);
      expect(await pending, JSON.stringify(token)).toEqual(INVALID);
    }
  });

  it('a connection that cannot even be made is "nothing sent", with no word of the failure in the result', async () => {
    const result = await requestDelivery(ENDPOINT, ADVANCE, {
      connect: () => {
        throw new Error(`cannot /Users/x/secret ${TOKEN}`);
      },
    });
    expect(result).toEqual(INVALID);
  });
});

describe('a body that cannot be serialized: a typed refusal, never a synchronous throw', () => {
  const cyclic: Record<string, unknown> = { a: 1 };
  cyclic.self = cyclic;
  const throwingGetter = Object.defineProperty({}, 'x', {
    enumerable: true,
    get() {
      throw new Error(`getter /Users/x/secret ${TOKEN}`);
    },
  }) as Record<string, unknown>;
  const unserializable: Array<[string, Record<string, unknown>]> = [
    ['cyclic', cyclic],
    ['BigInt', { n: BigInt(1) }],
    ['toJSON returning undefined', { toJSON: () => undefined }],
    ['a throwing getter', throwingGetter],
  ];

  it('each is refused HERE: no synchronous throw, a RESOLVED delivery_request_invalid, and no connection made', async () => {
    for (const [what, body] of unserializable) {
      const w = wiring();
      let pending: Promise<DeliveryResult> | undefined;
      expect(() => {
        pending = requestDelivery(ENDPOINT, { ...ADVANCE, body }, { connect: w.connect });
      }, what).not.toThrow();
      expect(w.ports, what).toEqual([]);
      expect(await pending, what).toEqual(INVALID);
    }
  });

  it('the planner names its three refusals apart: not a route, over the bound, not serializable', () => {
    const plan = (clientModule as Record<string, unknown>).planDeliveryCall as
      | ((call: DeliveryCall) => unknown)
      | undefined;
    expect(typeof plan).toBe('function');
    for (const [what, body] of unserializable) {
      expect(plan!({ ...ADVANCE, body }), what).toEqual({ kind: 'refuse', why: 'not_serializable' });
    }
    const overBound = { pad: 'x'.repeat(MAX_DELIVERY_REQUEST_BODY_BYTES) };
    expect(plan!({ ...ADVANCE, body: overBound })).toEqual({ kind: 'refuse', why: 'over_bound' });
    expect(plan!({ method: 'GET', path: '/healthz', body: null })).toEqual({ kind: 'refuse', why: 'not_a_route' });
    expect(plan!({ ...STATUS, body: {} })).toEqual({ kind: 'refuse', why: 'not_a_route' });
    expect(plan!(STATUS)).toEqual({ kind: 'send', advance: false, payload: null });
    expect(plan!(ADVANCE)).toEqual({
      kind: 'send',
      advance: true,
      payload: Buffer.from(JSON.stringify(CONFIRMATION), 'utf-8'),
    });
  });
});

describe('answers and failures: an answer as it came, every failure as a code', () => {
  it('an HTTP answer is returned with its status and its parsed JSON body, whatever the status', async () => {
    for (const [status, body] of [
      [200, { ok: true, status: { step: 'COMMIT' } }],
      [404, { ok: false, problem: 'not_found' }],
      [401, { ok: false, problem: 'unauthorized' }],
    ] as const) {
      const w = wiring();
      const pending = requestDelivery(ENDPOINT, STATUS, { connect: w.connect });
      expect(w.ports, String(status)).toEqual([PORT]);
      await received(w.sockets[0]);
      w.sockets[0].answer(answer(status, JSON.stringify(body)));
      expect(await pending, String(status)).toEqual({ kind: 'http', status, body });
    }
  });

  it('a body that is not JSON comes back with no body, for the relay to read as unreadable', async () => {
    const w = wiring();
    const pending = requestDelivery(ENDPOINT, ADVANCE, { connect: w.connect });
    expect(w.ports).toEqual([PORT]);
    await received(w.sockets[0]);
    w.sockets[0].answer(answer(200, '<html>'));
    const result = await pending;
    expect(result).toMatchObject({ kind: 'http', status: 200 });
    expect(Object.hasOwn(result, 'body') && (result as { body: unknown }).body === undefined).toBe(true);
  });

  it("an answer is read up to the office's own bound and no further: over it, the connection is destroyed", async () => {
    const atBound = `"${'x'.repeat(MAX_DELIVERY_RESPONSE_BYTES - 2)}"`;
    const w = wiring();
    const first = requestDelivery(ENDPOINT, STATUS, { connect: w.connect });
    expect(w.ports).toEqual([PORT]);
    await received(w.sockets[0]);
    w.sockets[0].answer(answer(200, atBound));
    expect(await first).toEqual({ kind: 'http', status: 200, body: 'x'.repeat(MAX_DELIVERY_RESPONSE_BYTES - 2) });
    const second = requestDelivery(ENDPOINT, STATUS, { connect: w.connect });
    await received(w.sockets[1]);
    w.sockets[1].answer(answer(200, `${atBound} `));
    expect(await second).toEqual({ kind: 'transport_error', problem: 'delivery_response_too_large' });
    expect(w.sockets[1].destroyed).toBe(true);
  });

  it('nothing answering is "unreachable"; any other failure, or an answer cut short, is a network error', async () => {
    const refused = wiring(failing('ECONNREFUSED'));
    expect(await requestDelivery(ENDPOINT, ADVANCE, { connect: refused.connect })).toEqual({
      kind: 'transport_error',
      problem: 'delivery_unreachable',
    });
    const reset = wiring(failing('ECONNRESET'));
    expect(await requestDelivery(ENDPOINT, ADVANCE, { connect: reset.connect })).toEqual({
      kind: 'transport_error',
      problem: 'delivery_network_error',
    });
    const short = wiring();
    const cut = requestDelivery(ENDPOINT, ADVANCE, { connect: short.connect });
    expect(short.ports).toEqual([PORT]);
    await received(short.sockets[0]);
    short.sockets[0].answer('HTTP/1.1 200 X\r\nContent-Type: application/json\r\nContent-Length: 50\r\n\r\n{"ok":tr');
    short.sockets[0].close();
    expect(await cut).toEqual({ kind: 'transport_error', problem: 'delivery_network_error' });
    const silent = wiring();
    const closed = requestDelivery(ENDPOINT, ADVANCE, { connect: silent.connect });
    await received(silent.sockets[0]);
    silent.sockets[0].close();
    expect(await closed).toEqual({ kind: 'transport_error', problem: 'delivery_network_error' });
  });

  it('no result this client composes carries the token, a path or an error message: only its code', async () => {
    const badToken = wiring();
    const invalid = requestDelivery({ port: PORT, token: `${TOKEN} x` }, STATUS, { connect: badToken.connect });
    expect(badToken.ports).toEqual([]);
    const results = [
      await requestDelivery(ENDPOINT, ADVANCE, { connect: wiring(failing('ECONNREFUSED')).connect }),
      await requestDelivery(ENDPOINT, ADVANCE, { connect: wiring(failing('ECONNRESET')).connect }),
      await Promise.race([
        requestDelivery(ENDPOINT, STATUS, { connect: wiring().connect, statusTimeoutMs: 20 }),
        wait(1000).then((): DeliveryResult => ({ kind: 'http', status: 0, body: 'still waiting' })),
      ]),
      await invalid,
    ];
    expect(results.map((r) => (r.kind === 'transport_error' ? r.problem : r.kind))).toEqual([
      'delivery_unreachable',
      'delivery_network_error',
      'delivery_timeout',
      'delivery_request_invalid',
    ]);
    for (const r of results) expect(Object.keys(r).sort()).toEqual(['kind', 'problem']);
    const everything = JSON.stringify(results);
    expect(everything).not.toContain(TOKEN);
    expect(everything).not.toContain('/Users/');
  });
});

describe('deadlines: a status read is bounded; an advance has none, so no client timer is a route that frees the one-call slot', () => {
  it('the default status deadline is 30 s', () => {
    expect(DELIVERY_STATUS_TIMEOUT_MS).toBe(30_000);
  });

  it('a status read with no answer by its deadline is a timeout, and its connection is destroyed', async () => {
    const w = wiring();
    const pending = requestDelivery(ENDPOINT, STATUS, { connect: w.connect, statusTimeoutMs: 40 });
    expect(w.ports).toEqual([PORT]);
    await received(w.sockets[0]);
    // Raced against a bounded wait, so a read with no deadline fails HERE, on the assertion, and not as a test timeout.
    expect(await Promise.race([pending, wait(1000).then(() => 'still waiting')])).toEqual({
      kind: 'transport_error',
      problem: 'delivery_timeout',
    });
    expect(w.sockets[0].destroyed).toBe(true);
  });

  it("an advance has NO deadline: it is still waiting long past the status deadline, and settles only on the adapter's answer", async () => {
    const w = wiring();
    let settled = false;
    const pending = requestDelivery(ENDPOINT, ADVANCE, { connect: w.connect, statusTimeoutMs: 20 }).then((r) => {
      settled = true;
      return r;
    });
    expect(w.ports).toEqual([PORT]);
    await received(w.sockets[0]);
    await wait(200);
    expect(settled).toBe(false);
    expect(w.sockets[0].destroyed).toBe(false);
    w.sockets[0].answer(answer(200, '{"ok":true,"result":{"advanced":true}}'));
    expect(await pending).toEqual({ kind: 'http', status: 200, body: { ok: true, result: { advanced: true } } });
  });

  it('an advance whose connection breaks settles at once, as a network error', async () => {
    const w = wiring();
    const pending = requestDelivery(ENDPOINT, ADVANCE, { connect: w.connect, statusTimeoutMs: 20 });
    expect(w.ports).toEqual([PORT]);
    await received(w.sockets[0]);
    w.sockets[0].close();
    expect(await pending).toEqual({ kind: 'transport_error', problem: 'delivery_network_error' });
  });
});
