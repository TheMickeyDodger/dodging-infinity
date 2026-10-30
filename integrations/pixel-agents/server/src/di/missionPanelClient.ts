/**
 * HTTP client for the Mission request panel's local adapter (`dimission_panel.py`).
 *
 * It is the ONLY way the DI server reaches the adapter, and the adapter is the only thing it
 * reaches. It issues exactly the adapter's fifteen routes, only to 127.0.0.1, with the per-launch
 * bearer token read from the adapter's discovery file and a Host header naming the same
 * listener. It sends no Origin and no cookie. A path that is not one of the fifteen, or a body
 * larger than the adapter accepts, is refused HERE before a socket exists. It never spawns,
 * starts or imports the adapter. Every failure is a typed problem code; none carries the token.
 *
 * Why a write-capable client exists beside `backendClient.ts` (whose contract is "only GETs"):
 * the catalog adapter is passive and read-only, so submit, decide and run cannot ride on it.
 * This client is separate so that contract stays exactly what it says.
 *
 * A failure AFTER the request left (a timeout, a reset) leaves its effect UNKNOWN: the caller
 * must say so and re-read the record, and must never replay a write on its own.
 */

import * as http from 'http';

import type { BackendEndpoint } from './backendDiscovery.js';

/** The adapter's own `MAX_BODY_BYTES`; a request body larger than this is never sent. */
export const MAX_PANEL_REQUEST_BODY_BYTES = 65536;
/**
 * The most bytes of one answer this client will read. It EQUALS the panel's own
 * `mission_panel.wire.MAX_RESPONSE_BYTES` (a Python test reads this line and fails if they differ), and the
 * panel measures what it builds in these serialized bytes, never in characters: an answer that would exceed
 * it is cut by whole turns with the cut stated, or refused in words, and is never sent for this client to
 * reject mid-stream. Keep the `4 * 1024 * 1024` shape; that test parses it.
 */
export const MAX_PANEL_RESPONSE_BYTES = 4 * 1024 * 1024;
/** Preparing a Mission's isolated workspace is the slowest thing the adapter does. */
export const PANEL_REQUEST_TIMEOUT_MS = 60_000;

export interface PanelCall {
  method: 'GET' | 'POST';
  path: string;
  body?: Record<string, unknown>;
}

const REF = 'lr-[0-9a-f]{32}';
/** The adapter's routes, and nothing else, by method. */
const ROUTES: ReadonlyArray<{ method: 'GET' | 'POST'; path: RegExp }> = [
  { method: 'GET', path: /^\/healthz$/ },
  { method: 'GET', path: /^\/overview$/ },
  { method: 'GET', path: /^\/conversations\/cv-[0-9a-f]{32}$/ },
  { method: 'GET', path: new RegExp(`^/requests/${REF}$`) },
  { method: 'GET', path: new RegExp(`^/requests/${REF}/result$`) },
  { method: 'POST', path: /^\/conversations\/turn$/ },
  { method: 'POST', path: /^\/proposals\/submit$/ },
  // Issuing an offer records bookkeeping (the frozen offer), so it is not a GET.
  { method: 'POST', path: new RegExp(`^/requests/${REF}/offer$`) },
  { method: 'POST', path: new RegExp(`^/requests/${REF}/decide$`) },
  { method: 'POST', path: new RegExp(`^/requests/${REF}/check-approval$`) },
  // (c): the person's decline, rejection or change request, on exactly the offer shown.
  { method: 'POST', path: new RegExp(`^/requests/${REF}/decline$`) },
  { method: 'POST', path: new RegExp(`^/requests/${REF}/reject$`) },
  { method: 'POST', path: new RegExp(`^/requests/${REF}/request-changes$`) },
  { method: 'POST', path: new RegExp(`^/requests/${REF}/withdraw$`) },
  { method: 'POST', path: new RegExp(`^/requests/${REF}/run$`) },
];

export function isPanelRoute(call: PanelCall): boolean {
  return ROUTES.some((route) => route.method === call.method && route.path.test(call.path));
}

export type PanelTransportProblem =
  /**
   * Nothing was sent: nothing answered at the address in the discovery file. That does not establish whether the adapter runs (it
   * may be elsewhere, or the address stale); only that this address took no connection.
   */
  | 'panel_unreachable'
  /** The request may have been applied; its outcome is UNKNOWN. */
  | 'panel_timeout'
  | 'panel_network_error'
  | 'panel_response_too_large'
  /** Refused locally: not one of the routes, or a body over the bound. Nothing was sent. */
  | 'panel_request_invalid';

export type PanelResult =
  | { kind: 'http'; status: number; body: unknown; bodyProblem: 'body_not_json' | null }
  | { kind: 'transport_error'; problem: PanelTransportProblem };

export function requestPanel(
  endpoint: BackendEndpoint,
  call: PanelCall,
  timeoutMs: number = PANEL_REQUEST_TIMEOUT_MS,
): Promise<PanelResult> {
  const payload = call.body === undefined ? null : Buffer.from(JSON.stringify(call.body), 'utf-8');
  if (
    !isPanelRoute(call) ||
    (call.method === 'POST') !== (payload !== null) ||
    (payload !== null && payload.length > MAX_PANEL_REQUEST_BODY_BYTES)
  ) {
    return Promise.resolve({ kind: 'transport_error', problem: 'panel_request_invalid' });
  }
  return new Promise((resolve) => {
    let settled = false;
    const finish = (result: PanelResult): void => {
      if (settled) return;
      settled = true;
      resolve(result);
    };
    const headers: Record<string, string | number> = {
      Authorization: `Bearer ${endpoint.token}`,
      Host: `127.0.0.1:${endpoint.port}`,
      Accept: 'application/json',
    };
    if (payload !== null) {
      headers['Content-Type'] = 'application/json';
      headers['Content-Length'] = payload.length;
    }
    const request = http.request(
      {
        host: '127.0.0.1',
        port: endpoint.port,
        method: call.method,
        path: call.path,
        headers,
        timeout: timeoutMs,
      },
      (response) => {
        const chunks: Buffer[] = [];
        let total = 0;
        response.on('data', (chunk: Buffer) => {
          total += chunk.length;
          if (total > MAX_PANEL_RESPONSE_BYTES) {
            request.destroy();
            finish({ kind: 'transport_error', problem: 'panel_response_too_large' });
            return;
          }
          chunks.push(chunk);
        });
        response.on('end', () => {
          let body: unknown;
          let bodyProblem: 'body_not_json' | null = null;
          try {
            body = JSON.parse(Buffer.concat(chunks).toString('utf-8'));
          } catch {
            body = undefined;
            bodyProblem = 'body_not_json';
          }
          finish({ kind: 'http', status: response.statusCode ?? 0, body, bodyProblem });
        });
        response.on('error', () =>
          finish({ kind: 'transport_error', problem: 'panel_network_error' }),
        );
      },
    );
    request.on('timeout', () => {
      request.destroy();
      finish({ kind: 'transport_error', problem: 'panel_timeout' });
    });
    request.on('error', (err: NodeJS.ErrnoException) => {
      finish({
        kind: 'transport_error',
        problem: err.code === 'ECONNREFUSED' ? 'panel_unreachable' : 'panel_network_error',
      });
    });
    request.end(payload ?? undefined);
  });
}
