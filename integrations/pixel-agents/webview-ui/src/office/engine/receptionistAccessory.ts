/**
 * THE RECEPTIONIST'S ACCESSORY (DOM-free data, no new art pipeline): a headset (a band over the head and an earcup on each side) and a name badge,
 * drawn from code over the character's own sprite, in fixed colours defined in constants.ts. Drawn ONLY on the receptionist (`ch.isReceptionist`),
 * which is never a DI agent, so no agent can ever carry it; with its fixed palette and the permanent "Reception" label it is how the receptionist reads
 * apart from the working roster. Sprite-pixel coordinates are in the 16 x 32 character cell facing down (the receptionist never turns), measured against
 * the standing frame of the character sheets: the head is rows 3-13, the chest rows 18-23.
 */
import {
  DI_RECEPTIONIST_BADGE_COLOR,
  DI_RECEPTIONIST_BADGE_EDGE_COLOR,
  DI_RECEPTIONIST_HEADSET_COLOR,
} from '../../constants.js';
import { Direction } from '../types.js';

export interface AccessoryRect {
  x: number;
  y: number;
  w: number;
  h: number;
  color: string;
}

/** The accessory, in sprite pixels of the 16 x 32 cell (facing down). */
export const RECEPTIONIST_ACCESSORY: readonly AccessoryRect[] = [
  { x: 3, y: 4, w: 10, h: 1, color: DI_RECEPTIONIST_HEADSET_COLOR }, // the band over the head
  { x: 0, y: 8, w: 2, h: 4, color: DI_RECEPTIONIST_HEADSET_COLOR }, // left earcup
  { x: 14, y: 8, w: 2, h: 4, color: DI_RECEPTIONIST_HEADSET_COLOR }, // right earcup
  { x: 10, y: 19, w: 3, h: 2, color: DI_RECEPTIONIST_BADGE_COLOR }, // the name badge
  { x: 10, y: 21, w: 3, h: 1, color: DI_RECEPTIONIST_BADGE_EDGE_COLOR }, // its lower edge
];

/** Whether this character is drawn with the accessory: the receptionist, facing down. Never an agent, a resident or the greeter. */
export function hasReceptionistAccessory(ch: { isReceptionist?: boolean; dir: number }): boolean {
  return ch.isReceptionist === true && ch.dir === Direction.DOWN;
}

/** Draw the accessory over a character whose sprite's top-left is at (drawX, drawY) device pixels at `zoom` device pixels per sprite pixel. */
export function drawReceptionistAccessory(
  c: CanvasRenderingContext2D,
  drawX: number,
  drawY: number,
  zoom: number,
): void {
  for (const r of RECEPTIONIST_ACCESSORY) {
    c.fillStyle = r.color;
    c.fillRect(drawX + r.x * zoom, drawY + r.y * zoom, r.w * zoom, r.h * zoom);
  }
}
