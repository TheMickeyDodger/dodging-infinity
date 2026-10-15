/**
 * Shape and size limits for the write-bearing DI client messages. DI MODE ONLY: the
 * ordinary Pixel handlers are untouched. A message that is not exactly the editor's shape
 * within these bounds is REFUSED before anything reaches the persistence layer (and its
 * physical write guard). Each validator returns null when valid, else the refusal reason.
 */
import {
  DI_ID_MAX_LENGTH,
  DI_LAYOUT_MAX_AREAS,
  DI_LAYOUT_MAX_DIM,
  DI_LAYOUT_MAX_FURNITURE,
  DI_LAYOUT_MAX_PETS,
  DI_SEATS_MAX_ENTRIES,
  DI_VERSION_MAX_LENGTH,
} from '../constants.js';

type Obj = Record<string, unknown>;

const LAYOUT_KEYS = new Set([
  'version',
  'cols',
  'rows',
  'tiles',
  'furniture',
  'tileColors',
  'layoutRevision',
  'pets',
  'carpetTiles',
  'areas',
  'areaTiles',
]);
const FURNITURE_KEYS = new Set(['uid', 'type', 'col', 'row', 'color']);
const COLOR_KEYS = new Set(['h', 's', 'b', 'c', 'colorize']);
const CARPET_KEYS = new Set(['variant', 'color', 'accentColor', 'order']);
const PET_KEYS = new Set(['id', 'petType']);
const AREA_KEYS = new Set(['label', 'color']);
const SEAT_KEYS = new Set(['palette', 'hueShift', 'seatId']);
/** Colour sliders and hue shifts are small numbers; anything beyond is not editor output. */
const COLOR_COMPONENT_BOUND = 1_000;
/** Furniture may sit partly outside the grid (wall items extend upward); bound it loosely. */
const FURNITURE_POSITION_BOUND = DI_LAYOUT_MAX_DIM * 2;
const TILE_VALUE_MAX = 255;
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
function isColor(value: unknown): boolean {
  if (!isObj(value) || !onlyKeys(value, COLOR_KEYS)) return false;
  for (const k of ['h', 's', 'b', 'c']) {
    const v = value[k];
    if (typeof v !== 'number' || !Number.isFinite(v) || Math.abs(v) > COLOR_COMPONENT_BOUND)
      return false;
  }
  return value.colorize === undefined || typeof value.colorize === 'boolean';
}
function parallelArray(value: unknown, length: number, item: (v: unknown) => boolean): boolean {
  return Array.isArray(value) && value.length === length && value.every(item);
}

/** A `saveLayout` payload: exactly an OfficeLayout v1 within the editor's bounds. */
export function validateDiLayout(layout: unknown): string | null {
  if (!isObj(layout)) return 'layout is not an object';
  if (!onlyKeys(layout, LAYOUT_KEYS)) return 'layout has unknown fields';
  if (layout.version !== 1) return 'layout version is not 1';
  if (!isInt(layout.cols, 1, DI_LAYOUT_MAX_DIM) || !isInt(layout.rows, 1, DI_LAYOUT_MAX_DIM))
    return `layout cols/rows outside 1..${DI_LAYOUT_MAX_DIM}`;
  const cells = layout.cols * layout.rows;
  if (!parallelArray(layout.tiles, cells, (t) => isInt(t, 0, TILE_VALUE_MAX)))
    return 'layout tiles do not match cols x rows';
  const furniture = layout.furniture;
  if (!Array.isArray(furniture) || furniture.length > DI_LAYOUT_MAX_FURNITURE)
    return 'layout furniture missing or too long';
  for (const item of furniture) {
    if (
      !isObj(item) ||
      !onlyKeys(item, FURNITURE_KEYS) ||
      !isBoundedString(item.uid, DI_ID_MAX_LENGTH) ||
      !isBoundedString(item.type, DI_ID_MAX_LENGTH) ||
      !isInt(item.col, -FURNITURE_POSITION_BOUND, FURNITURE_POSITION_BOUND) ||
      !isInt(item.row, -FURNITURE_POSITION_BOUND, FURNITURE_POSITION_BOUND) ||
      (item.color !== undefined && !isColor(item.color))
    )
      return 'layout furniture item malformed';
  }
  if (
    layout.tileColors !== undefined &&
    !parallelArray(layout.tileColors, cells, (c) => c === null || isColor(c))
  )
    return 'layout tileColors malformed';
  if (layout.layoutRevision !== undefined && !isInt(layout.layoutRevision, 0, 1_000_000))
    return 'layout layoutRevision malformed';
  if (layout.pets !== undefined) {
    const pets = layout.pets;
    if (
      !Array.isArray(pets) ||
      pets.length > DI_LAYOUT_MAX_PETS ||
      !pets.every(
        (p) =>
          isObj(p) &&
          onlyKeys(p, PET_KEYS) &&
          isBoundedString(p.id, DI_ID_MAX_LENGTH) &&
          isInt(p.petType, 0, TILE_VALUE_MAX),
      )
    )
      return 'layout pets malformed';
  }
  if (
    layout.carpetTiles !== undefined &&
    !parallelArray(
      layout.carpetTiles,
      cells,
      (c) =>
        c === null ||
        (isObj(c) &&
          onlyKeys(c, CARPET_KEYS) &&
          isInt(c.variant, 0, TILE_VALUE_MAX) &&
          (c.color === undefined || isColor(c.color)) &&
          (c.accentColor === undefined || isColor(c.accentColor)) &&
          (c.order === undefined || isInt(c.order, -1_000_000, 1_000_000))),
    )
  )
    return 'layout carpetTiles malformed';
  const labels = new Set<string>();
  if (layout.areas !== undefined) {
    const areas = layout.areas;
    if (!Array.isArray(areas) || areas.length > DI_LAYOUT_MAX_AREAS)
      return 'layout areas malformed';
    for (const a of areas) {
      if (
        !isObj(a) ||
        !onlyKeys(a, AREA_KEYS) ||
        !isBoundedString(a.label, DI_ID_MAX_LENGTH) ||
        typeof a.color !== 'string' ||
        !/^#[0-9a-fA-F]{6}$/.test(a.color) ||
        labels.has(a.label)
      )
        return 'layout area malformed';
      labels.add(a.label);
    }
  }
  if (
    layout.areaTiles !== undefined &&
    !parallelArray(
      layout.areaTiles,
      cells,
      (t) => t === null || (typeof t === 'string' && labels.has(t)),
    )
  )
    return 'layout areaTiles malformed';
  return null;
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
