import type { AgentStateStore } from './agentStateStore.js';
import { HUE_SHIFT_MAX_DEG } from './constants.js';

/** Seat metadata a client persists per agent id (palette, hue shift, chosen seat). */
export type AgentSeatMeta = { palette?: number; hueShift?: number; seatId?: string };

/**
 * Apply a client's `saveAgentSeats` payload: sync palette/hueShift back onto the live
 * AgentState (so existingAgents stays consistent across reconnects) and persist the seats
 * through the store's adapter. Values outside their range are ignored rather than stored —
 * a remote client or hand-edited payload must not corrupt them into a rendering glitch.
 * `paletteCount` is dynamic: external asset directories can add palettes beyond the bundled 6.
 */
export function applyAgentSeats(
  store: AgentStateStore,
  seats: Record<string, AgentSeatMeta>,
  paletteCount: number,
): void {
  for (const [idStr, meta] of Object.entries(seats)) {
    const agent = store.get(Number(idStr));
    if (!agent) continue;
    if (
      meta.palette !== undefined &&
      Number.isInteger(meta.palette) &&
      meta.palette >= 0 &&
      meta.palette < paletteCount
    ) {
      agent.palette = meta.palette;
    }
    if (
      meta.hueShift !== undefined &&
      Number.isInteger(meta.hueShift) &&
      meta.hueShift >= 0 &&
      meta.hueShift <= HUE_SHIFT_MAX_DEG
    ) {
      agent.hueShift = meta.hueShift;
    }
  }
  store.getAdapter()?.saveSeats(seats);
}
