/**
 * Minimal read-only HTTP client for the DI backend's exported loopback API.
 *
 * The CONTRACT-v3 "HTTP surface" has three GET routes: /catalog, /diagnostics, /healthz. Item 2
 * (evidence library) adds ONE more read route, /artifact, which takes exactly an admitted
 * reference (mission ref, artifact id, listed journal digest), never a path. This client can only
 * issue those four GETs, only to 127.0.0.1, with the bearer token from the discovery file and a
 * Host header naming the same listener.
 * It sends no body, no Origin, no cookie. It never spawns, starts or imports the
 * backend. Every failure is a typed problem code; none carries the token.
 */

import type { IncomingHttpHeaders } from 'http';
import * as http from 'http';

import type { BackendEndpoint } from './backendDiscovery.js';

export type BackendRoute = 'catalog' | 'diagnostics' | 'healthz' | 'artifact';

/** Responses larger than this are refused (the 2048-mission bound stays far below it). */
export const MAX_BACKEND_RESPONSE_BYTES = 32 * 1024 * 1024;
/**
 * /artifact answers carry at most 64 KiB of text, which JSON escapes to at most three times its
 * bytes, plus a small envelope: an answer larger than this is refused unread past it.
 */
export const MAX_ARTIFACT_RESPONSE_BYTES = 512 * 1024;

/** The ONE thing /artifact may be asked: an admitted reference, by the backend's grammar. */
export interface ArtifactReference {
  missionRef: string;
  artifactId: string;
  cursor: string;
}
export const ARTIFACT_REFERENCE_GRAMMAR = {
  missionRef: /^[a-z0-9][a-z0-9._-]{0,63}:mn-[0-9a-f]{32}$/,
  artifactId: /^mf-[0-9a-f]{32}$/,
  cursor: /^[0-9a-f]{64}$/,
} as const;

export function isArtifactReference(value: Partial<ArtifactReference>): boolean {
  return (
    typeof value.missionRef === 'string' &&
    ARTIFACT_REFERENCE_GRAMMAR.missionRef.test(value.missionRef) &&
    typeof value.artifactId === 'string' &&
    ARTIFACT_REFERENCE_GRAMMAR.artifactId.test(value.artifactId) &&
    typeof value.cursor === 'string' &&
    ARTIFACT_REFERENCE_GRAMMAR.cursor.test(value.cursor)
  );
}

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
  /** /artifact only: the admitted reference. */
  artifact?: ArtifactReference;
  timeoutMs: number;
}

export function routePath(
  route: BackendRoute,
  waitSeconds?: number,
  artifact?: ArtifactReference,
): string | null {
  if (route === 'artifact') {
    if (artifact === undefined || !isArtifactReference(artifact)) return null;
    return (
      `/artifact?mission_ref=${encodeURIComponent(artifact.missionRef)}` +
      `&artifact_id=${artifact.artifactId}&cursor=${artifact.cursor}`
    );
  }
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
  const requestPath = routePath(route, options.waitSeconds, options.artifact);
  const limit = route === 'artifact' ? MAX_ARTIFACT_RESPONSE_BYTES : MAX_BACKEND_RESPONSE_BYTES;
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
          if (total > limit) {
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
