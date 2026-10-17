/**
 * Shape and size limits for the write-bearing DI client messages. DI MODE ONLY: the
 * ordinary Pixel handlers are untouched. A message that is not exactly the editor's shape
 * within these bounds is REFUSED before anything reaches the persistence layer (and its
 * physical write guard). Each validator returns null when valid, else the refusal reason.
 */
import { checkLayout, LAYOUT_COLOR_COMPONENT_BOUND } from '../../../core/src/layoutLimits.js';
import { DI_ID_MAX_LENGTH, DI_SEATS_MAX_ENTRIES, DI_VERSION_MAX_LENGTH } from '../constants.js';

type Obj = Record<string, unknown>;

const SEAT_KEYS = new Set(['palette', 'hueShift', 'seatId']);
/** Hue shifts share the layout colour-component bound (one definition, in core). */
const COLOR_COMPONENT_BOUND = LAYOUT_COLOR_COMPONENT_BOUND;
const SEAT_PALETTE_MAX = 64;

function isObj(value: unknown): value is Obj {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}
function onlyKeys(value: Obj, allowed: Set<string>): boolean {
  return Object.keys(value).every((k) => allowed.has(k));
}
function isInt(value: unknown, min: number, max: number): value is number {
  return typeof value === 'number' && Number.isInteger(value) && value >= min && value <= max;
}
function isBoundedString(value: unknown, maxLength: number): value is string {
  return typeof value === 'string' && value.length > 0 && value.length <= maxLength;
}

/** A `saveLayout` payload: exactly an OfficeLayout v1 within the editor's bounds — the ONE shared
 *  check (core/src/layoutLimits.ts) in STRICT mode: the editor only ever sends migrated layouts. */
export function validateDiLayout(layout: unknown): string | null {
  return checkLayout(layout);
}

/** A `saveAgentSeats` payload: characterId -> { palette?, hueShift?, seatId? }. */
export function validateDiSeats(seats: unknown): string | null {
  if (!isObj(seats)) return 'seats is not an object';
  const entries = Object.entries(seats);
  if (entries.length > DI_SEATS_MAX_ENTRIES) return 'too many seats';
  for (const [id, meta] of entries) {
    if (!/^-?\d{1,10}$/.test(id)) return 'seat key is not a character id';
    if (!isObj(meta) || !onlyKeys(meta, SEAT_KEYS)) return 'seat entry malformed';
    if (meta.palette !== undefined && !isInt(meta.palette, 0, SEAT_PALETTE_MAX))
      return 'seat palette malformed';
    if (
      meta.hueShift !== undefined &&
      (typeof meta.hueShift !== 'number' ||
        !Number.isFinite(meta.hueShift) ||
        Math.abs(meta.hueShift) > COLOR_COMPONENT_BOUND)
    )
      return 'seat hueShift malformed';
    // seatId is absent, NULL (an unseated character — what getPersistableSeats actually
    // emits; core/schemas AgentSeat.seatId is string | null) or a bounded string.
    // NOTE: the generated wire type (messages.ts AgentSeatMeta.seatId?: string) omits null;
    // this matches what is SENT. The protocol divergence is recorded, not changed here.
    if (
      meta.seatId !== undefined &&
      meta.seatId !== null &&
      !isBoundedString(meta.seatId, DI_ID_MAX_LENGTH)
    )
      return 'seat seatId malformed';
  }
  return null;
}

/** A `setLastSeenVersion` value. */
export function validateDiVersion(version: unknown): string | null {
  return isBoundedString(version, DI_VERSION_MAX_LENGTH) ? null : 'version malformed';
}
