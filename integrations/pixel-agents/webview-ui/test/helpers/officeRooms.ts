/**
 * The rooms of each detected office on the living metropolis, from the layout's own floor and the detected desk-facing seats: a WORK room holds a
 * desk-facing seat (a workstation or a table seat — exactly the input the residents' plan reads), every other room of the office is a COMMON room
 * (lobby, corridor, courtyard). Places are classified by their EXACT detected
 * names, never by substring. Shared by the interior tests so every measurement uses one definition of "inside the office" and "in a work room".
 */
import { WATER_FLOOR_TILES } from '../../src/constants.ts';
import { roomsOf } from '../../src/office/ambient/ambientWorld.ts';
import { OfficeState } from '../../src/office/engine/officeState.ts';
import { getCatalogEntry } from '../../src/office/layout/furnitureCatalog.ts';
import { inRegions, livingRegions } from '../../src/office/layout/worldViews.ts';
import type { OfficeLayout } from '../../src/office/types.ts';
import { TileType } from '../../src/office/types.ts';

export const OFFICE_LABELS = ['Palace', 'Botanical Atrium', 'Deco Observatory'] as const;

export interface OfficeRoomTiles {
  /** tiles of the rooms holding a desk-facing seat (workstation or table seat) */
  work: Set<number>;
  /** tiles of every other room of the office */
  common: Set<number>;
}

const footprintOf = (type: string) => {
  const e = getCatalogEntry(type);
  return e ? { w: e.footprintW, h: e.footprintH, seat: e.category === 'chairs' } : null;
};

export function officeRooms(layout: OfficeLayout): {
  rooms: Map<string, OfficeRoomTiles>;
  /** the detected tier-1 workstation seat ids of each office, in detection order (Palace, Botanical Atrium, Deco Observatory) */
  workstations: string[][];
} {
  const probe = new OfficeState(layout);
  probe.setReceptionistEnabled(true);
  const det = probe.officeDetection!;
  const tileMap: number[][] = [];
  for (let r = 0; r < layout.rows; r++) tileMap.push(layout.tiles.slice(r * layout.cols, (r + 1) * layout.cols));
  const regions = livingRegions(layout, footprintOf)!;
  const placeOf = (c: number, r: number): string | null =>
    inRegions(c, r, [regions[0]]) ? 'Palace' : (layout.areaTiles?.[r * layout.cols + c] ?? null);
  const rooms = new Map<string, OfficeRoomTiles>();
  for (const office of det.offices) {
    const floor: number[] = [];
    for (let r = 0; r < layout.rows; r++)
      for (let c = 0; c < layout.cols; c++) {
        const t = tileMap[r][c];
        if (placeOf(c, r) === office.label && t !== TileType.WALL && t !== TileType.VOID && !WATER_FLOOR_TILES.has(t)) floor.push(r * layout.cols + c);
      }
    const seatTiles = new Set(
      [...office.workstationIds, ...office.tableIds].map((id) => {
        const s = layout.furniture.find((f) => f.uid === id.split(':')[0])!;
        const entry = getCatalogEntry(s.type)!;
        const k = id.includes(':') ? Number(id.split(':')[1]) : 0;
        return (s.row + Math.floor(k / entry.footprintW)) * layout.cols + s.col + (k % entry.footprintW);
      }),
    );
    const work = new Set<number>();
    const common = new Set<number>();
    for (const room of roomsOf(floor, layout.cols, layout.rows, tileMap as never)) {
      const isWork = room.some((t) => seatTiles.has(t));
      for (const t of room) (isWork ? work : common).add(t);
    }
    rooms.set(office.label, { work, common });
  }
  return { rooms, workstations: det.offices.map((o) => [...o.workstationIds]) };
}
