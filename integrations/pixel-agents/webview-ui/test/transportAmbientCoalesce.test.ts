/**
 * S7: the WebSocket transport's queue while disconnected. The automatic DECORATIVE save
 * (saveAmbientState, every 30 s) is coalesced to at most ONE pending, the latest; every other message
 * keeps exactly its existing semantics: queued, in order, flushed on (re)connect.
 */
import assert from 'node:assert/strict';

import { afterEach, beforeEach, test, vi } from 'vitest';

import type { ClientMessage } from '../../core/src/messages.ts';
import { WebSocketTransport } from '../src/transport/webSocketTransport.ts';

class FakeSocket {
  static readonly CONNECTING = 0;
  static readonly OPEN = 1;
  static readonly CLOSED = 3;
  static all: FakeSocket[] = [];
  readyState = FakeSocket.CONNECTING;
  sent: unknown[] = [];
  onopen: (() => void) | null = null;
  onclose: (() => void) | null = null;
  onmessage: ((e: { data: string }) => void) | null = null;
  onerror: (() => void) | null = null;
  readonly url: string;
  constructor(url: string) {
    this.url = url;
    FakeSocket.all.push(this);
  }
  send(data: string): void {
    this.sent.push(JSON.parse(data));
  }
  close(): void {
    this.readyState = FakeSocket.CLOSED;
  }
  open(): void {
    this.readyState = FakeSocket.OPEN;
    this.onopen?.();
  }
  drop(): void {
    this.readyState = FakeSocket.CLOSED;
    this.onclose?.();
  }
}

const g = globalThis as unknown as { WebSocket: unknown };
let saved: unknown;
beforeEach(() => {
  saved = g.WebSocket;
  g.WebSocket = FakeSocket;
  FakeSocket.all = [];
  vi.useFakeTimers();
});
afterEach(() => {
  g.WebSocket = saved;
  vi.useRealTimers();
});

const save = (n: number) => ({ type: 'saveAmbientState', state: { n } }) as unknown as ClientMessage;
const action = (id: string) => ({ type: 'saveLayout', layout: { id } }) as unknown as ClientMessage;
const pending = (t: WebSocketTransport) => (t as unknown as { pendingMessages: ClientMessage[] }).pendingMessages;

test('while CONNECTING: 120 ambient saves leave ONE pending (the latest); actions keep their order; the flush sends exactly that', () => {
  const t = new WebSocketTransport('ws://x');
  t.connect();
  t.send(action('A1'));
  for (let n = 1; n <= 60; n++) t.send(save(n));
  t.send(action('A2'));
  for (let n = 61; n <= 120; n++) t.send(save(n));
  t.send(action('A3'));
  assert.equal(pending(t).length, 4);
  assert.equal(pending(t).filter((m) => m.type === 'saveAmbientState').length, 1);
  FakeSocket.all[0].open();
  assert.deepEqual(FakeSocket.all[0].sent, [action('A1'), save(120), action('A2'), action('A3')]);
  assert.equal(pending(t).length, 0);
});

test('after a DROP (reconnecting, an hour of 30 s saves): still one pending snapshot; on reconnect the latest goes, not a replay', () => {
  const t = new WebSocketTransport('ws://x');
  t.connect();
  FakeSocket.all[0].open();
  FakeSocket.all[0].drop();
  assert.equal(t.state, 'reconnecting');
  const actions = ['B1', 'B2', 'B3'];
  for (let n = 1; n <= 120; n++) {
    t.send(save(n));
    if (n % 40 === 0) t.send(action(actions[n / 40 - 1]));
  }
  assert.equal(pending(t).length, 4, 'one snapshot + three actions');
  vi.advanceTimersByTime(60_000); // the backoff opens a new socket
  const next = FakeSocket.all.at(-1)!;
  assert.notEqual(next, FakeSocket.all[0]);
  next.open();
  assert.deepEqual(next.sent, [save(120), action('B1'), action('B2'), action('B3')]);
});

test('while OPEN nothing is queued or coalesced: every message is sent as it comes', () => {
  const t = new WebSocketTransport('ws://x');
  t.connect();
  FakeSocket.all[0].open();
  t.send(save(1));
  t.send(save(2));
  t.send(action('C1'));
  assert.deepEqual(FakeSocket.all[0].sent, [save(1), save(2), action('C1')]);
  assert.equal(pending(t).length, 0);
});
