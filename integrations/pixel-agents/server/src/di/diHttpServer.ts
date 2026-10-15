/**
 * The DI office's browser-facing HTTP + WebSocket surface.
 *
 * A SEPARATE surface from the backend's catalog adapter, with its own protection.
 * It deliberately differs from the standalone surface (httpServer.ts) where that
 * surface would leak observation data or credentials:
 *
 * - Loopback only. Binds 127.0.0.1; no host option exists.
 * - Every request: `Host` must name this listener (127.0.0.1:<port> or
 *   localhost:<port>), and an `Origin`, when present, must be same-origin with it.
 *   A DNS-rebound name or a foreign page is refused with 403 before routing.
 * - OBSERVATION REQUIRES THE TOKEN. The only data path is `/ws`, and a socket
 *   without the per-launch token is closed before ANY store listener is attached
 *   or any message is read — it receives no Mission or agent data at all. (The
 *   standalone surface lets an untokened socket watch the office; DI does not.)
 * - The ONE enumerated unauthenticated exception: GET of a file that exists under
 *   the compiled SPA directory (index.html, JS/CSS bundles, fonts, sprite/asset
 *   JSON). Those bytes are the same for every launch and carry no Mission or agent
 *   data. Unknown paths are 404 (no history fallback); there is no hook route and
 *   no health route.
 * - No CORS: the SPA is same-origin, so no Access-Control-* header is ever sent.
 * - No request logging (Fastify logger off), so a `?token=` URL is never logged.
 */

import fastifyStatic from '@fastify/static';
import fastifyWebsocket from '@fastify/websocket';
import type { FastifyInstance } from 'fastify';
import Fastify from 'fastify';
import type { Socket } from 'net';

import type { AgentStateStore } from '../agentStateStore.js';
import { DI_WS_MAX_PAYLOAD_BYTES, WS_CLOSE_UNAUTHORIZED } from '../constants.js';
import { safeSend, subscribeStoreToSocket, timingSafeStringEqual } from '../socketPipe.js';

/** The only address the DI office ever binds. */
export const DI_BIND_HOST = '127.0.0.1';

/** Bodies are never accepted on this surface; keep the parser bound tiny. */
const DI_BODY_LIMIT_BYTES = 1024;

export interface DiHttpServerOptions {
  /** 0 or omitted = OS-assigned. */
  port?: number;
  /** Per-launch secret. Required for every WebSocket (the only data path). */
  token: string;
  store: AgentStateStore;
  /** Compiled SPA directory (dist/webview). */
  staticDir: string;
  /**
   * Invoked once per AUTHENTICATED socket, BEFORE it is subscribed to store broadcasts, so the
   * first message on every DI connection (fresh or reconnected) can be the office state: the
   * client then knows it is a DI office before any character can be created on that socket.
   */
  onConnected?: (send: (message: Record<string, unknown>) => void) => void;
  /** Invoked for every parsed message from an AUTHENTICATED socket. */
  onClientMessage: (
    msg: Record<string, unknown>,
    send: (message: Record<string, unknown>) => void,
  ) => void;
}

export interface DiHttpServerHandle {
  app: FastifyInstance;
  port: number;
  /** Destroy every accepted socket, upgraded WebSockets included. Used ONLY when the
   *  bounded shutdown drain expires (force termination); never on the normal path. */
  forceCloseAll: () => void;
}

/** Is `host` (the request's Host header) exactly this loopback listener? */
export function isAllowedDiHost(host: string | undefined, port: number): boolean {
  if (!host || port <= 0) return false;
  const normalized = host.toLowerCase();
  return normalized === `127.0.0.1:${port}` || normalized === `localhost:${port}`;
}

/** An absent Origin passes (non-browser client); a present one must be same-origin http. */
export function isSameOriginOrAbsent(
  origin: string | undefined,
  host: string | undefined,
): boolean {
  if (origin === undefined) return true;
  if (!host) return false;
  try {
    const parsed = new URL(origin);
    return parsed.protocol === 'http:' && parsed.host.toLowerCase() === host.toLowerCase();
  } catch {
    return false;
  }
}

/** The token rides the `?token=` query (the SPA forwards it) or an Authorization Bearer header. */
export function diRequestTokenValid(
  url: string | undefined,
  authorization: string | undefined,
  expected: string,
): boolean {
  if (!expected) return false;
  let fromQuery = '';
  try {
    fromQuery = new URL(url ?? '', 'http://localhost').searchParams.get('token') ?? '';
  } catch {
    fromQuery = '';
  }
  if (fromQuery && timingSafeStringEqual(fromQuery, expected)) return true;
  return timingSafeStringEqual(authorization ?? '', `Bearer ${expected}`);
}

export async function createDiHttpServer(
  options: DiHttpServerOptions,
): Promise<DiHttpServerHandle> {
  if (!options.token) throw new Error('DI office requires a per-launch token');

  const app = Fastify({
    logger: false,
    bodyLimit: DI_BODY_LIMIT_BYTES,
    // Stated, not inherited: on close() destroy only IDLE keep-alive connections; an ACTIVE
    // request is allowed to finish (the drain). The drain itself is bounded by the caller.
    forceCloseConnections: 'idle',
  });
  let boundPort = 0;
  // Every accepted socket, so a bounded shutdown can force-terminate what will not drain
  // (Node's HTTP connection tracking does not cover upgraded WebSocket sockets).
  const sockets = new Set<Socket>();
  app.server.on('connection', (socket: Socket) => {
    sockets.add(socket);
    socket.on('close', () => sockets.delete(socket));
  });

  // Gate 1 — every request, before routing (WebSocket upgrades included).
  app.addHook('onRequest', async (request, reply) => {
    const host = request.headers.host;
    if (!isAllowedDiHost(host, boundPort) || !isSameOriginOrAbsent(request.headers.origin, host)) {
      await reply.code(403).type('text/plain; charset=utf-8').send('forbidden');
    }
  });

  // No referrer (the page URL carries the token), no sniffing, no caching.
  app.addHook('onSend', async (_request, reply, payload) => {
    reply.header('Referrer-Policy', 'no-referrer');
    reply.header('X-Content-Type-Options', 'nosniff');
    reply.header('Cache-Control', 'no-store');
    return payload;
  });

  // Explicit inbound bound on the channel that carries the write-bearing messages (the
  // library default is 100 MiB): an oversized frame closes the socket (1009) unparsed.
  await app.register(fastifyWebsocket, { options: { maxPayload: DI_WS_MAX_PAYLOAD_BYTES } });
  await app.register(fastifyStatic, { root: options.staticDir, prefix: '/' });
  app.setNotFoundHandler((_request, reply) => {
    void reply.code(404).type('text/plain; charset=utf-8').send('not found');
  });

  // Gate 2 — the data path. Authenticate BEFORE attaching any listener.
  app.get('/ws', { websocket: true }, (socket, request) => {
    if (!diRequestTokenValid(request.url, request.headers.authorization, options.token)) {
      socket.close(WS_CLOSE_UNAUTHORIZED, 'unauthorized');
      return;
    }
    const send = (message: Record<string, unknown>): void => safeSend(socket, message);
    options.onConnected?.(send); // first message on this connection, before any broadcast
    const unsubscribe = subscribeStoreToSocket(options.store, send);
    socket.on('message', (data: Buffer | string) => {
      let msg: unknown;
      try {
        msg = JSON.parse(data.toString());
      } catch {
        return; // malformed JSON: ignored
      }
      if (msg && typeof msg === 'object' && !Array.isArray(msg)) {
        options.onClientMessage(msg as Record<string, unknown>, send);
      }
    });
    socket.on('close', unsubscribe);
  });

  await app.listen({ host: DI_BIND_HOST, port: options.port ?? 0 });
  const address = app.server.address();
  boundPort = typeof address === 'object' && address ? address.port : 0;
  return {
    app,
    port: boundPort,
    forceCloseAll: () => {
      for (const socket of sockets) socket.destroy();
    },
  };
}
