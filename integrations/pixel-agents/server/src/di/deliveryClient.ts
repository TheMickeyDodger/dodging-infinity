/**
 * (b2b) The HTTP client for the office delivery adapter's loopback interface (didelivery_office.py, office_delivery/http_adapter.py).
 *
 * It is the transport the delivery relay (deliveryRequests.ts) is built with. This module does not wire itself in: the office entry
 * does (diCli.ts, `deliveryRelay`, under --delivery-adapter-discovery).
 * It issues exactly the relay's two routes, only to 127.0.0.1, and meets the adapter's admission rules as its `admit` states them:
 * - a Host naming the same listener, EXACTLY ONE `Authorization: Bearer <token>`, and no Origin, Cookie or Transfer-Encoding;
 * - a POST carries Content-Type exactly `application/json` (the adapter refuses a charset parameter) and an exact Content-Length;
 * - a GET carries no body and no Content-Length.
 * A call that is not one of the two routes, a body over the adapter's bound or one that cannot be serialized, or an endpoint whose
 * port or token cannot be sent, is refused HERE, before any connection exists.
 *
 * THE TOKEN: this client AUTHENTICATES WITH IT and NEVER REFLECTS IT OUTWARD. It goes into the Authorization header and nowhere
 * else. Every failure is a typed code with no message, and this module logs nothing. An http result carries the adapter's own
 * answer unchanged; the relay withholds a reply that contains the token (deliveryRequests.ts, `withheld`).
 *
 * THE DEADLINES:
 * - A status read has a total deadline (DELIVERY_STATUS_TIMEOUT_MS). On expiry the connection is destroyed and the result is
 *   `delivery_timeout`, which the relay answers `unavailable`: a read changes nothing.
 * - An advance-once has NO deadline here. It settles only when the adapter answers or the connection breaks. pr_delivery runs
 *   every step with no deadline, so a client deadline would be a number chosen against no worst case: it would add a window, and
 *   resolve nothing.
 * - Having none removes ONE route by which the relay's one-call slot is freed while a step may still run. It does not make that
 *   slot the only check. A broken connection still frees the slot, and a settlement says nothing about whether the step stopped.
 *
 * Each request opens its own connection (no agent, no keep-alive reuse), through `connect`. It is injectable, so the tests can hand
 * node's real HTTP client an in-memory duplex instead of a socket.
 */
import * as http from 'http';
import * as net from 'net';
import type { Duplex } from 'stream';

import type { BackendEndpoint } from './backendDiscovery.js';
import type { DeliveryCall, DeliveryResult, DeliveryTransportProblem } from './deliveryRequests.js';

/** The adapter's own MAX_BODY_BYTES (office_delivery/http_adapter.py): a request body larger than this, in bytes, is never sent. */
export const MAX_DELIVERY_REQUEST_BODY_BYTES = 4096;
/** THE OFFICE'S OWN bound on one answer, in bytes: the adapter states none. */
export const MAX_DELIVERY_RESPONSE_BYTES = 1024 * 1024;
/** The total deadline of a status READ. An advance has none (see above). */
export const DELIVERY_STATUS_TIMEOUT_MS = 30_000;
/** The adapter's delivery-id pattern (its _DELIVERY_ROUTE). */
export const DELIVERY_ID_PATTERN = 'prd-[0-9a-f]{24}';

const LOOPBACK_HOST = '127.0.0.1';
const STATUS_ROUTE = new RegExp(`^/deliveries/${DELIVERY_ID_PATTERN}/status$`);
const ADVANCE_ROUTE = new RegExp(`^/deliveries/${DELIVERY_ID_PATTERN}/advance-once$`);
/** A token this client will put in a header: visible ASCII only, within the discovery file's own length bound. */
const SENDABLE_TOKEN = /^[!-~]{1,512}$/;

/** Where one request's connection comes from. */
export type DeliveryConnect = (port: number) => Duplex;

export interface DeliveryRequestOptions {
  /** By default, its own TCP connection to 127.0.0.1:<port>. */
  connect?: DeliveryConnect;
  /** The status read's total deadline; an advance has none. */
  statusTimeoutMs?: number;
}

const tcp: DeliveryConnect = (port) => net.createConnection({ host: LOOPBACK_HOST, port });

function failure(problem: DeliveryTransportProblem): DeliveryResult {
  return { kind: 'transport_error', problem };
}

function isObject(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

/** Why this client will not send a call: three distinct conditions, each of which means "this office refused to send it; nothing was sent". */
export type DeliveryRefusal = 'not_a_route' | 'over_bound' | 'not_serializable';

export type DeliveryPlan =
  | { kind: 'send'; advance: boolean; payload: Buffer | null }
  | { kind: 'refuse'; why: DeliveryRefusal };

/**
 * What a call may send, decided before any connection: exactly one of the relay's two routes with its own method and body, and an
 * advance body that serializes to JSON within the adapter's bound in BYTES. Each refusal is named apart, so no one value stands for
 * two conditions. Serialization is the step that can throw on a body (a cycle, a BigInt, a getter that throws); it is caught here, and
 * a body that throws or serializes to nothing (a toJSON that returns undefined) is `not_serializable`.
 */
export function planDeliveryCall(call: DeliveryCall): DeliveryPlan {
  if (call.method === 'GET' && STATUS_ROUTE.test(call.path) && call.body === null) {
    return { kind: 'send', advance: false, payload: null };
  }
  if (call.method !== 'POST' || !ADVANCE_ROUTE.test(call.path) || !isObject(call.body)) {
    return { kind: 'refuse', why: 'not_a_route' };
  }
  let serialized: unknown;
  try {
    serialized = JSON.stringify(call.body);
  } catch {
    return { kind: 'refuse', why: 'not_serializable' };
  }
  if (typeof serialized !== 'string') return { kind: 'refuse', why: 'not_serializable' };
  const payload = Buffer.from(serialized, 'utf-8');
  return payload.length <= MAX_DELIVERY_REQUEST_BODY_BYTES
    ? { kind: 'send', advance: true, payload }
    : { kind: 'refuse', why: 'over_bound' };
}

function endpointSendable(endpoint: BackendEndpoint): boolean {
  const { port, token } = endpoint;
  return (
    Number.isInteger(port) &&
    port >= 1 &&
    port <= 65535 &&
    typeof token === 'string' &&
    SENDABLE_TOKEN.test(token)
  );
}

/**
 * ONE request to the office delivery adapter. It settles exactly once. Handled failures RESOLVE as typed DeliveryResult values; they
 * do not reject the Promise. Every refusal the planner names resolves the same `delivery_request_invalid`, and none of them throws.
 * That statement covers inputs of the declared types; it is no claim about arbitrary values (a null endpoint, for one).
 */
export function requestDelivery(
  endpoint: BackendEndpoint,
  call: DeliveryCall,
  options: DeliveryRequestOptions = {},
): Promise<DeliveryResult> {
  const plan = planDeliveryCall(call);
  if (plan.kind === 'refuse' || !endpointSendable(endpoint)) {
    return Promise.resolve(failure('delivery_request_invalid'));
  }
  const connect = options.connect ?? tcp;
  const statusTimeoutMs = options.statusTimeoutMs ?? DELIVERY_STATUS_TIMEOUT_MS;
  return new Promise((resolve) => {
    let settled = false;
    let deadline: ReturnType<typeof setTimeout> | null = null;
    // True only when the connection itself could not be made: then nothing was sent.
    let notSent = false;
    const finish = (result: DeliveryResult): void => {
      if (settled) return;
      settled = true;
      if (deadline !== null) clearTimeout(deadline);
      resolve(result);
    };
    const headers: Record<string, string | number> = {
      Host: `${LOOPBACK_HOST}:${endpoint.port}`,
      Authorization: `Bearer ${endpoint.token}`,
      Accept: 'application/json',
    };
    if (plan.payload !== null) {
      headers['Content-Type'] = 'application/json';
      headers['Content-Length'] = plan.payload.length;
    }
    let request: http.ClientRequest;
    try {
      request = http.request(
        {
          method: call.method,
          path: call.path,
          headers,
          setHost: false,
          createConnection: () => {
            try {
              return connect(endpoint.port);
            } catch (error) {
              notSent = true;
              throw error;
            }
          },
        },
        (response) => {
          const chunks: Buffer[] = [];
          let total = 0;
          response.on('data', (chunk: Buffer) => {
            total += chunk.length;
            if (total > MAX_DELIVERY_RESPONSE_BYTES) {
              finish(failure('delivery_response_too_large'));
              request.destroy();
              return;
            }
            chunks.push(chunk);
          });
          response.on('end', () => {
            let body: unknown;
            try {
              body = JSON.parse(Buffer.concat(chunks).toString('utf-8'));
            } catch {
              body = undefined;
            }
            finish({ kind: 'http', status: response.statusCode ?? 0, body });
          });
          response.on('error', () => finish(failure('delivery_network_error')));
          // An answer cut short closes without ending.
          response.on('close', () => {
            if (!response.complete) finish(failure('delivery_network_error'));
          });
        },
      );
    } catch {
      // Node refused to build the request (for example an invalid header character): nothing was sent.
      finish(failure('delivery_request_invalid'));
      return;
    }
    request.on('error', (error: NodeJS.ErrnoException) => {
      if (notSent) finish(failure('delivery_request_invalid'));
      else finish(failure(error.code === 'ECONNREFUSED' ? 'delivery_unreachable' : 'delivery_network_error'));
    });
    if (!plan.advance) {
      deadline = setTimeout(() => {
        finish(failure('delivery_timeout'));
        request.destroy();
      }, statusTimeoutMs);
    }
    request.end(plan.payload ?? undefined);
  });
}
