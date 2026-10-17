/**
 * THE WORKING HALO (M3 S5) — ONE source of truth for which real agents glow, read by BOTH rendering
 * paths (the office canvas and the full-world Map), so the two can never disagree.
 *
 * A character is eligible ONLY when ALL of these hold, every one read from the server's projection
 * (officeStateMessage.ts), never re-derived here:
 * - the office is live (both freshness levels hold);
 * - the projection carries a FINITE `validUntil` and has not expired on THIS clock (also finite): past
 *   it with no newer payload, nothing glows; an absent or unreadable expiry is treated as expired.
 *   This also covers hidden-tab resume, because eligibility is evaluated at draw time;
 * - its Mission is live;
 * - the member is live and canonically identified (the server gives a `characterId` only to a live
 *   member);
 * - `animateActive` holds: live, with its own reported activity exactly `busy`, the fresh observed
 *   terminal-working classification.
 *
 * NEVER eligible:
 * - idle, unknown, missing, stale, disconnected or replaced members;
 * - anything enabled by animation alone (the character's own animation state is never read);
 * - ambient residents, which are not characters at all.
 *
 * The halo means "observed working now". It never means progress, completion, verification or model
 * identity.
 */
import type { DiOfficeState } from '../../../core/src/messages.js';

/**
 * FAIL CLOSED: absence of evidence is never evidence of freshness. Every condition must be POSITIVELY
 * present: strict `true` flags, an `observed` list, a FINITE `validUntil`, a FINITE client clock and
 * an integer character id. An absent, null, NaN or infinite expiry, a bad clock read or any malformed
 * field makes NOTHING eligible.
 */
export function haloEligibleCharacterIds(di: DiOfficeState | null, nowSec: number): Set<number> {
  const out = new Set<number>();
  if (!di || di.live !== true) return out;
  const validUntil: unknown = di.times?.validUntil;
  if (typeof validUntil !== 'number' || !Number.isFinite(validUntil) || !Number.isFinite(nowSec) || nowSec > validUntil) return out;
  if (di.missions?.state !== 'observed' || !Array.isArray(di.missions.missions)) return out;
  for (const mission of di.missions.missions) {
    if (mission?.live !== true || mission.members?.state !== 'observed' || !Array.isArray(mission.members.members)) continue;
    for (const m of mission.members.members)
      if (m?.live === true && m.animateActive === true && Number.isInteger(m.characterId)) out.add(m.characterId as number);
  }
  return out;
}

/** The halo's drawn strength: a gentle pulse, or STEADY under reduced motion (never off when eligible). */
export function haloIntensity(timeSec: number, reducedMotion: boolean): number {
  return reducedMotion ? 0.6 : 0.45 + 0.2 * Math.sin(timeSec * Math.PI);
}
