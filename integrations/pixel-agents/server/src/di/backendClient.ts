/**
 * Minimal read-only HTTP client for the DI backend's exported loopback API.
 *
 * Exactly three GET routes exist (CONTRACT-v3 "HTTP surface"): /catalog, /diagnostics,
 * /healthz. This client can only issue those three GETs, only to 127.0.0.1, with the
 * bearer token from the discovery file and a Host header naming the same listener.
 * It sends no body, no Origin, no cookie. It never spawns, starts or imports the
 * backend. Every failure is a typed problem code; none carries the token.
 */

import type { IncomingHttpHeaders } from 'http';
import * as http from 'http';

import type { BackendEndpoint } from './backendDiscovery.js';

export type BackendRoute = 'catalog' | 'diagnostics' | 'healthz';

/** Responses larger than this are refused (the 2048-mission bound stays far below it). */
export const MAX_BACKEND_RESPONSE_BYTES = 32 * 1024 * 1024;

export type BackendTransportProblem =
  | 'backend_unreachable'
  | 'backend_timeout'
  | 'backend_response_too_large'
  | 'backend_network_error'
  | 'backend_request_invalid';

export type BackendResult =
  | {
      kind: 'http';
      status: number;
      headers: IncomingHttpHeaders;
      /** Parsed JSON body, or undefined when the body was not JSON (see bodyProblem). */
      body: unknown;
      bodyProblem: 'body_not_json' | null;
    }
  | { kind: 'transport_error'; problem: BackendTransportProblem };

export interface BackendRequestOptions {
  /** /catalog only: 1..30 (v3: zero is a 400). */
  waitSeconds?: number;
  timeoutMs: number;
}

export function routePath(route: BackendRoute, waitSeconds?: number): string | null {
  if (route !== 'catalog') return route === 'diagnostics' ? '/diagnostics' : '/healthz';
  if (waitSeconds === undefined) return '/catalog';
  if (!Number.isInteger(waitSeconds) || waitSeconds < 1 || waitSeconds > 30) return null;
  return `/catalog?wait_seconds=${waitSeconds}`;
}

export function requestBackend(
  endpoint: BackendEndpoint,
  route: BackendRoute,
  options: BackendRequestOptions,
): Promise<BackendResult> {
  const requestPath = routePath(route, options.waitSeconds);
  if (requestPath === null) {
    return Promise.resolve({ kind: 'transport_error', problem: 'backend_request_invalid' });
  }
  return new Promise((resolve) => {
    let settled = false;
    const finish = (result: BackendResult): void => {
      if (settled) return;
      settled = true;
      resolve(result);
    };
    const request = http.request(
      {
        host: '127.0.0.1',
        port: endpoint.port,
        method: 'GET',
        path: requestPath,
        headers: {
          Authorization: `Bearer ${endpoint.token}`,
          Host: `127.0.0.1:${endpoint.port}`,
          Accept: 'application/json',
        },
        timeout: options.timeoutMs,
      },
      (response) => {
        const chunks: Buffer[] = [];
        let total = 0;
        response.on('data', (chunk: Buffer) => {
          total += chunk.length;
          if (total > MAX_BACKEND_RESPONSE_BYTES) {
            request.destroy();
            finish({ kind: 'transport_error', problem: 'backend_response_too_large' });
            return;
          }
          chunks.push(chunk);
        });
        response.on('end', () => {
          const text = Buffer.concat(chunks).toString('utf-8');
          let body: unknown;
          let bodyProblem: 'body_not_json' | null = null;
          try {
            body = JSON.parse(text);
          } catch {
            body = undefined;
            bodyProblem = 'body_not_json';
          }
          finish({
            kind: 'http',
            status: response.statusCode ?? 0,
            headers: response.headers,
            body,
            bodyProblem,
          });
        });
        response.on('error', () =>
          finish({ kind: 'transport_error', problem: 'backend_network_error' }),
        );
      },
    );
    request.on('timeout', () => {
      request.destroy();
      finish({ kind: 'transport_error', problem: 'backend_timeout' });
    });
    request.on('error', (err: NodeJS.ErrnoException) => {
      finish({
        kind: 'transport_error',
        problem: err.code === 'ECONNREFUSED' ? 'backend_unreachable' : 'backend_network_error',
      });
    });
    request.end();
  });
}
