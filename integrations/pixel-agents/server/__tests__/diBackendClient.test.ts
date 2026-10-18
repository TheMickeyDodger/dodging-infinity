/**
 * The read-only backend HTTP client, against an in-process FIXTURE HTTP server (a stand-in
 * for the backend adapter — NOT the backend, NOT live evidence). Nothing here spawns or
 * starts a backend.
 */
import * as fs from 'fs';
import * as http from 'http';
import type { AddressInfo } from 'net';
import * as path from 'path';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';

import { MAX_ARTIFACT_RESPONSE_BYTES, requestBackend, routePath } from '../src/di/backendClient.js';
import { parseArtifactContentAnswer } from '../src/di/evidenceContent.js';

const TOKEN = 'fixture-token-abc';

let server: http.Server;
let port = 0;
let seen: http.IncomingMessage[] = [];
let respond: (req: http.IncomingMessage, res: http.ServerResponse) => void;

beforeEach(async () => {
  seen = [];
  respond = (_req, res) => {
    res.writeHead(200, { 'content-type': 'application/json', 'x-di-freshness-health': 'fresh' });
    res.end('{"ok":true}');
  };
  server = http.createServer((req, res) => {
    seen.push(req);
    respond(req, res);
  });
  await new Promise<void>((r) => server.listen(0, '127.0.0.1', () => r()));
  port = (server.address() as AddressInfo).port;
});

afterEach(async () => {
  await new Promise<void>((r) => server.close(() => r()));
});

describe('routePath', () => {
  it('only the three read routes; wait_seconds must be 1..30 (zero is refused)', () => {
    expect(routePath('catalog', 5)).toBe('/catalog?wait_seconds=5');
    expect(routePath('catalog')).toBe('/catalog');
    expect(routePath('diagnostics')).toBe('/diagnostics');
    expect(routePath('healthz')).toBe('/healthz');
    expect(routePath('catalog', 0)).toBeNull();
    expect(routePath('catalog', 31)).toBeNull();
    expect(routePath('catalog', 1.5)).toBeNull();
  });
});

describe('requestBackend (FIXTURE server)', () => {
  it('sends GET with the bearer token and a Host naming the same loopback listener, no Origin, no cookie', async () => {
    const result = await requestBackend({ port, token: TOKEN }, 'catalog', {
      waitSeconds: 3,
      timeoutMs: 5000,
    });
    expect(result).toMatchObject({
      kind: 'http',
      status: 200,
      body: { ok: true },
      bodyProblem: null,
    });
    const req = seen[0];
    expect(req.method).toBe('GET');
    expect(req.url).toBe('/catalog?wait_seconds=3');
    expect(req.headers.authorization).toBe(`Bearer ${TOKEN}`);
    expect(req.headers.host).toBe(`127.0.0.1:${port}`);
    expect(req.headers.origin).toBeUndefined();
    expect(req.headers.cookie).toBeUndefined();
  });

  it('carries non-2xx statuses through for the caller to render as unavailable', async () => {
    respond = (_req, res) => {
      res.writeHead(401);
      res.end('unauthorized');
    };
    const result = await requestBackend({ port, token: TOKEN }, 'catalog', {
      waitSeconds: 1,
      timeoutMs: 5000,
    });
    expect(result).toMatchObject({ kind: 'http', status: 401, bodyProblem: 'body_not_json' });
  });

  it('a non-JSON 200 body is flagged, never treated as data', async () => {
    respond = (_req, res) => {
      res.writeHead(200);
      res.end('<html>');
    };
    const result = await requestBackend({ port, token: TOKEN }, 'catalog', {
      waitSeconds: 1,
      timeoutMs: 5000,
    });
    expect(result).toMatchObject({
      kind: 'http',
      status: 200,
      body: undefined,
      bodyProblem: 'body_not_json',
    });
  });

  it('an unreachable port is backend_unreachable (nothing is started)', async () => {
    const closed = port;
    await new Promise<void>((r) => server.close(() => r()));
    server = http.createServer();
    await new Promise<void>((r) => server.listen(0, '127.0.0.1', () => r()));
    const result = await requestBackend({ port: closed, token: TOKEN }, 'healthz', {
      timeoutMs: 2000,
    });
    expect(result).toEqual({ kind: 'transport_error', problem: 'backend_unreachable' });
  });

  it('a stalled backend times out with backend_timeout', async () => {
    respond = () => {
      /* never answer */
    };
    const result = await requestBackend({ port, token: TOKEN }, 'catalog', {
      waitSeconds: 1,
      timeoutMs: 200,
    });
    expect(result).toEqual({ kind: 'transport_error', problem: 'backend_timeout' });
  });

  it('an invalid wait is refused before any request is made', async () => {
    const result = await requestBackend({ port, token: TOKEN }, 'catalog', {
      waitSeconds: 0,
      timeoutMs: 1000,
    });
    expect(result).toEqual({ kind: 'transport_error', problem: 'backend_request_invalid' });
    expect(seen).toHaveLength(0);
  });
});

describe('item 2: the /artifact route over real HTTP (FIXTURE server serving exported bytes)', () => {
  const exported = JSON.parse(
    fs.readFileSync(
      path.join(__dirname, 'fixtures/di-backend/v2/artifact-content-v1.FIXTURE.json'),
      'utf-8',
    ),
  ) as { content_answers: Record<string, Record<string, unknown>> };
  const answer = exported.content_answers.admitted_text;
  const ref = {
    missionRef: String(answer.mission_ref),
    artifactId: String(answer.artifact_id),
    cursor: String(answer.cursor),
  };

  it('sends exactly the admitted reference as the fixed query, bearer-authenticated', async () => {
    const body = JSON.stringify(answer);
    respond = (_req, res) => {
      res.writeHead(200, { 'content-type': 'application/json' });
      res.end(body);
    };
    const result = await requestBackend({ port, token: TOKEN }, 'artifact', {
      artifact: ref,
      timeoutMs: 5000,
    });
    expect(result).toMatchObject({ kind: 'http', status: 200, bodyProblem: null });
    const req = seen[0];
    expect(req.method).toBe('GET');
    expect(req.url).toBe(
      `/artifact?mission_ref=${encodeURIComponent(ref.missionRef)}&artifact_id=${ref.artifactId}&cursor=${ref.cursor}`,
    );
    expect(req.headers.authorization).toBe(`Bearer ${TOKEN}`);
    expect(req.headers.origin).toBeUndefined();
    if (result.kind !== 'http') throw new Error('transport');
    const parsed = parseArtifactContentAnswer(result.body, ref);
    expect(parsed.ok).toBe(true);
  });

  it('a malformed reference is never sent; an over-cap answer is refused unread past it', async () => {
    const bad = await requestBackend({ port, token: TOKEN }, 'artifact', {
      artifact: { ...ref, artifactId: '../../etc/passwd' },
      timeoutMs: 5000,
    });
    expect(bad).toEqual({ kind: 'transport_error', problem: 'backend_request_invalid' });
    expect(seen).toHaveLength(0);
    respond = (_req, res) => {
      res.writeHead(200, { 'content-type': 'application/json' });
      res.end('"' + 'x'.repeat(MAX_ARTIFACT_RESPONSE_BYTES + 16) + '"');
    };
    const big = await requestBackend({ port, token: TOKEN }, 'artifact', {
      artifact: ref,
      timeoutMs: 5000,
    });
    expect(big).toEqual({ kind: 'transport_error', problem: 'backend_response_too_large' });
  });
});
