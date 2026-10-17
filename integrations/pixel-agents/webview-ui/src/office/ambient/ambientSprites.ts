/**
 * The AMBIENT CHARACTER SHEETS (M3 S6) on the webview side: the job sheets and the Easter-egg
 * families delivered by `ambientSpritesLoaded` (server/src/di/ambientSprites.ts), and the ONE rule for
 * which frame a resident shows. A resident whose job has a sheet is drawn from it; any other resident
 * keeps the procedural palette sprite. Decorative only: never an agent's sprite.
 *
 * Frames: walking cycles the sheet's `walk` columns; standing (dwelling, waiting, backing off) cycles
 * idle, idle, gesture — so on a 4x4 MIN sheet with a single idle column the resident still visibly
 * moves between its idle and gesture poses. Facing comes from the sheet's own named rows.
 */
import type { AmbientSpriteSheet } from '../../../../core/src/messages.js';
import type { SpriteData } from '../types.js';

const sheets = new Map<string, AmbientSpriteSheet>();
const FACING = ['DOWN', 'LEFT', 'RIGHT', 'UP'] as const;

export function setAmbientSheets(list: readonly AmbientSpriteSheet[]): void {
  sheets.clear();
  for (const s of list) sheets.set(s.id, s);
}

/** The sheet for a job (sheet ids are job ids), or null when the job has none. */
export function ambientSheetFor(job: string): AmbientSpriteSheet | null {
  return sheets.get(job) ?? null;
}

export function ambientSheetIds(): string[] {
  return [...sheets.keys()].sort();
}

/** The standing cycle: idle, idle, gesture (columns). */
export function standingCycle(sheet: AmbientSpriteSheet): number[] {
  return [...sheet.idle, ...sheet.idle, ...sheet.gesture];
}

/** The frame a resident shows: facing by the sheet's named row, column by state and frame counter. */
export function ambientFrame(sheet: AmbientSpriteSheet, dir: number, walking: boolean, frame: number): SpriteData {
  const row = Math.max(0, sheet.rows.indexOf(FACING[dir] ?? 'DOWN'));
  const cycle = walking ? sheet.walk : standingCycle(sheet);
  return sheet.frames[row][cycle[((frame % cycle.length) + cycle.length) % cycle.length]] as SpriteData;
}
