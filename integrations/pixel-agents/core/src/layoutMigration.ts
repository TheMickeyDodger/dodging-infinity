/**
 * LAYOUT MIGRATION — the ONE definition. The webview runs it on every layout it loads (so this is
 * the form the CONSUMER holds), and admission (layoutLimits.ts) runs the very same function to
 * check that form — so the checker and the consumer can never disagree about what migration
 * produces. Pure: returns a new layout and whether anything changed; never mutates its input.
 *
 * Every step, and how it can change the serialized size (the complete list):
 *   1. legacy furniture type strings -> catalog ids (LEGACY_FURNITURE_TYPES): a rename grows an
 *      item by at most 13 bytes ('chair' -> 'WOODEN_CHAIR_FRONT'); 'cooler'/'lamp' are REMOVED.
 *   2. OLD_VOID 8 -> VOID 255, only when the layout has no layoutRevision: +2 bytes per such tile.
 *   3. pets absent -> []: a few bytes.
 *   4. tileColors absent or of the wrong length -> regenerated, one entry per tile: the largest
 *      growth (a colour object per floor tile), or a shrink if the old array was longer.
 * Admission measures the migrated result itself, so no bound on this growth has to be assumed.
 */

export type MigrationColor = { h: number; s: number; b: number; c: number; colorize?: boolean };

/** The fields migration reads and may rewrite. */
export interface MigratableLayout {
  tiles: number[];
  furniture: Array<{ type: string }>;
  tileColors?: Array<MigrationColor | null>;
  pets?: unknown[];
  layoutRevision?: number;
}

/** Old hardcoded furniture type strings -> manifest ids (null = no equivalent: removed). */
export const LEGACY_FURNITURE_TYPES: Readonly<Record<string, string | null>> = {
  desk: 'DESK_FRONT',
  chair: 'WOODEN_CHAIR_FRONT',
  bookshelf: 'BOOKSHELF',
  plant: 'PLANT',
  cooler: null,
  whiteboard: 'WHITEBOARD',
  pc: 'PC_FRONT_OFF',
  lamp: null,
};
/** The old VOID value; FLOOR_8 reuses 8, so only layouts without a layoutRevision migrate it. */
export const OLD_VOID_TILE = 8;
const WALL_TILE = 0;
const VOID_TILE = 255;
/** Generated colours for a layout saved before per-tile colours existed. */
export const DEFAULT_LEFT_ROOM_COLOR: MigrationColor = { h: 35, s: 30, b: 15, c: 0 }; // warm beige
export const DEFAULT_RIGHT_ROOM_COLOR: MigrationColor = { h: 25, s: 45, b: 5, c: 10 }; // warm brown

function generatedTileColor(tile: number): MigrationColor | null {
  switch (tile) {
    case WALL_TILE:
      return null;
    case 1: // was TILE_FLOOR -> FLOOR_1 beige
      return DEFAULT_LEFT_ROOM_COLOR;
    case 2: // was WOOD_FLOOR -> FLOOR_2 brown
      return DEFAULT_RIGHT_ROOM_COLOR;
    case 3: // was CARPET -> FLOOR_3 purple
      return { h: 280, s: 40, b: -5, c: 0 };
    case 4: // was DOORWAY -> FLOOR_4 tan
      return { h: 35, s: 25, b: 10, c: 0 };
    default:
      // Floor tile types without colours: neutral grey
      return tile > 0 && tile !== VOID_TILE ? { h: 0, s: 0, b: 0, c: 0 } : null;
  }
}

export function migrateLayout<T extends MigratableLayout>(
  input: T,
): { layout: T; changed: boolean } {
  let changed = false;
  let layout = input;

  // 1. furniture types
  const furniture: Array<{ type: string }> = [];
  for (const item of layout.furniture) {
    const renamed = LEGACY_FURNITURE_TYPES[item.type];
    if (renamed === undefined) furniture.push(item);
    else {
      changed = true;
      if (renamed !== null) furniture.push({ ...item, type: renamed });
    }
  }
  layout = { ...layout, furniture } as T;

  // 2. old VOID value — legacy layouts only (FLOOR_8 reuses the value)
  if (!layout.layoutRevision && layout.tiles.includes(OLD_VOID_TILE)) {
    changed = true;
    layout = {
      ...layout,
      tiles: layout.tiles.map((t) => (t === OLD_VOID_TILE ? VOID_TILE : t)),
    } as T;
  }

  // 3. pets default
  if (!layout.pets) {
    changed = true;
    layout = { ...layout, pets: [] } as T;
  }

  // 4. per-tile colours
  if (!(layout.tileColors && layout.tileColors.length === layout.tiles.length)) {
    changed = true;
    layout = { ...layout, tileColors: layout.tiles.map(generatedTileColor) } as T;
  }

  return { layout, changed };
}
