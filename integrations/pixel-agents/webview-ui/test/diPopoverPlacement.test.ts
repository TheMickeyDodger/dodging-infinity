/**
 * B2c: the anchored popover's PLACEMENT rule (`placePopover`, pure: numbers in, placement out), every branch. The rendered runs (cut 4) only ever met
 * the flipped placement (above, start) because the trigger sat near its frame's bottom; this pins the default (below, end) and each flip directly.
 * The rule (diPopover.ts): below and end-aligned unless that leaves the frame; above when it does not fit below AND there is more room above; start
 * when end would cross the frame's left edge and start would not cross its right one. The room on the chosen side becomes `maxHeight` (the popover
 * scrolls inside it); `maxWidth` is the room toward the chosen alignment.
 */
import { describe, expect, it } from 'vitest';

import { placePopover, POPOVER_GAP_PX as GAP, POPOVER_MARGIN_PX as MARGIN } from '../src/components/diPopover.ts';

const frame = { top: 50, left: 200, right: 1200, bottom: 900 };
const trigger = (top: number, left: number, width = 120, height = 36) => ({ top, bottom: top + height, left, right: left + width });
const size = { width: 240, height: 171 };

describe('placePopover: below and end-aligned by default; each flip only when the frame demands it', () => {
  it('DEFAULT: room below and to the left, so below, end-aligned, its room below as max-height', () => {
    const t = trigger(300, 600);
    expect(placePopover(t, size, frame)).toEqual({
      side: 'below',
      align: 'end',
      maxHeight: frame.bottom - MARGIN - (t.bottom + GAP),
      maxWidth: t.right - (frame.left + MARGIN),
    });
  });

  it('ABOVE: too little room below and more above (the measured cut-4 case: a trigger near its frame bottom)', () => {
    const t = trigger(809, 600);
    const placed = placePopover(t, size, frame);
    expect(placed.side).toBe('above');
    expect(placed.maxHeight).toBe(t.top - GAP - (frame.top + MARGIN));
  });

  it('STAYS BELOW, scrolling inside: too little room both ways but more below than above', () => {
    const tight = { top: 50, left: 200, right: 1200, bottom: 290 }; // room below 162 < 171, room above 18
    const t = trigger(80, 600);
    const placed = placePopover(t, size, tight);
    expect(placed.side).toBe('below');
    expect(placed.maxHeight).toBe(tight.bottom - MARGIN - (t.bottom + GAP));
    expect(placed.maxHeight).toBeLessThan(size.height);
  });

  it('START: end alignment would cross the frame left edge and start alignment fits', () => {
    const t = trigger(300, 220); // its right edge (340) less the popover (240) is left of the frame edge plus the margin (208)
    const placed = placePopover(t, size, frame);
    expect(placed.align).toBe('start');
    expect(placed.maxWidth).toBe(frame.right - MARGIN - t.left);
  });

  it('END at the margin exactly: end alignment that just meets the left margin stays end', () => {
    const t = trigger(300, frame.left + MARGIN + size.width - 120); // right edge - width === left + margin
    expect(placePopover(t, size, frame).align).toBe('end');
    const oneLess = trigger(300, frame.left + MARGIN + size.width - 121);
    expect(placePopover(oneLess, size, frame).align).toBe('start');
  });

  it('NEITHER fits sideways: end alignment is kept and its room is the max-width (the popover is narrowed, never moved off the trigger)', () => {
    const narrow = { top: 50, left: 200, right: 420, bottom: 900 };
    const t = trigger(300, 230, 150); // end needs right >= 448, start needs left <= 172: neither
    const placed = placePopover(t, size, narrow);
    expect(placed.align).toBe('end');
    expect(placed.maxWidth).toBe(t.right - (narrow.left + MARGIN));
    expect(placed.maxWidth).toBeLessThan(size.width);
  });

  it('never a negative room, even where both rooms compute below zero', () => {
    const tiny = { top: 0, left: 0, right: 10, bottom: 10 };
    const t = trigger(0, -200); // below: 10 - 8 - 40 < 0; above: 0 - 4 - 8 < 0; end room: -80 - 8 < 0
    const placed = placePopover(t, size, tiny);
    expect(placed.maxHeight).toBeGreaterThanOrEqual(0);
    expect(placed.maxWidth).toBeGreaterThanOrEqual(0);
  });
});
