/**
 * Does a seat face electronics (a PC or monitor)? DOM-free and pure: the ONE definition shared by the engine's PC-biased seat pick
 * and by the three-office detection, which calls a seat a WORKSTATION only when it faces a desk AND passes this test — the same
 * forward-and-flanking scan that switches a PC on when an agent sits down, so what the person sees (a lit monitor) and what the
 * seating decides (a workstation) cannot disagree.
 */
import { AUTO_ON_FACING_DEPTH } from '../../constants.js';
import type { OfficeLayout } from '../types.js';
import { Direction } from '../types.js';
import { getCatalogEntry } from './furnitureCatalog.js';

/** The unit step one tile in front of a seat. Any direction that is not RIGHT, LEFT or DOWN is UP. */
export function seatFacingOffset(direction: Direction): { dCol: number; dRow: number } {
  if (direction === Direction.RIGHT) return { dCol: 1, dRow: 0 };
  if (direction === Direction.LEFT) return { dCol: -1, dRow: 0 };
  if (direction === Direction.DOWN) return { dCol: 0, dRow: 1 };
  return { dCol: 0, dRow: -1 };
}

/** Every `col,row` a piece of electronics-category furniture covers. */
export function electronicsTileSet(furniture: OfficeLayout['furniture']): Set<string> {
  const out = new Set<string>();
  for (const item of furniture) {
    const entry = getCatalogEntry(item.type);
    if (!entry || entry.category !== 'electronics') continue;
    for (let dr = 0; dr < entry.footprintH; dr++) {
      for (let dc = 0; dc < entry.footprintW; dc++) {
        out.add(`${item.col + dc},${item.row + dr}`);
      }
    }
  }
  return out;
}

/**
 * True when an electronics tile lies up to AUTO_ON_FACING_DEPTH tiles straight ahead of the seat, or one tile to either side of any of
 * those tiles (desks are wide). Mirrors the forward-and-flanking scan used by furniture auto-state.
 */
export function seatFacesElectronics(
  seat: { seatCol: number; seatRow: number; facingDir: Direction },
  electronicsTiles: ReadonlySet<string>,
): boolean {
  const { dCol, dRow } = seatFacingOffset(seat.facingDir);
  for (let d = 1; d <= AUTO_ON_FACING_DEPTH; d++) {
    const tileCol = seat.seatCol + dCol * d;
    const tileRow = seat.seatRow + dRow * d;
    if (electronicsTiles.has(`${tileCol},${tileRow}`)) return true;
    if (dCol !== 0) {
      if (
        electronicsTiles.has(`${tileCol},${tileRow - 1}`) ||
        electronicsTiles.has(`${tileCol},${tileRow + 1}`)
      ) {
        return true;
      }
    } else if (
      electronicsTiles.has(`${tileCol - 1},${tileRow}`) ||
      electronicsTiles.has(`${tileCol + 1},${tileRow}`)
    ) {
      return true;
    }
  }
  return false;
}
