/**
 * Transport helpers shared by the standalone HTTP server (httpServer.ts) and the DI
 * office's server (di/diHttpServer.ts). Deliberately dependency-light: importing this
 * module pulls in no ClientMessage handler, no hook provider and no runtime, so the
 * DI office's module graph stays free of them by construction.
 */

import * as crypto from 'crypto';

import type { AgentStateStore } from './agentStateStore.js';
import type { AgentState } from './types.js';

/**
 * Forward store events to one socket as ServerMessages; returns the unsubscribe.
 * Shared by the standalone /ws route and the DI office's authenticated /ws route,
 * so both surfaces emit the identical agentCreated/agentClosed/broadcast shapes.
 */
export function subscribeStoreToSocket(
  store: AgentStateStore,
  send: (message: Record<string, unknown>) => void,
): () => void {
  const onAgentAdded = (id: number, agent: AgentState) => {
    send({
      type: 'agentCreated',
      id,
      folderName: agent.folderName,
      isExternal: agent.isExternal || undefined,
      isTeammate: agent.leadAgentId !== undefined || undefined,
      teammateName: agent.agentName,
      parentAgentId: agent.leadAgentId,
      teamName: agent.teamName,
      hooksOnly: agent.hooksOnly || undefined,
      palette: agent.palette,
      hueShift: agent.hueShift,
    });
  };
  const onAgentRemoved = (id: number) => {
    send({ type: 'agentClosed', id });
  };
  const onBroadcast = (message: Record<string, unknown>) => {
    send(message);
  };
  store.on('agentAdded', onAgentAdded);
  store.on('agentRemoved', onAgentRemoved);
  store.on('broadcast', onBroadcast);
  return () => {
    store.off('agentAdded', onAgentAdded);
    store.off('agentRemoved', onAgentRemoved);
    store.off('broadcast', onBroadcast);
  };
}

/** Constant-time string compare, length-guarded (timingSafeEqual throws on a
 *  length mismatch). One implementation for every token comparison. */
export function timingSafeStringEqual(actual: string, expected: string): boolean {
  const actualBuf = Buffer.from(actual);
  const expectedBuf = Buffer.from(expected);
  return actualBuf.length === expectedBuf.length && crypto.timingSafeEqual(actualBuf, expectedBuf);
}

export function safeSend(
  socket: { send: (data: string) => void; readyState: number },
  message: Record<string, unknown>,
): void {
  // WebSocket.OPEN = 1
  if (socket.readyState === 1) {
    socket.send(JSON.stringify(message));
  }
}
