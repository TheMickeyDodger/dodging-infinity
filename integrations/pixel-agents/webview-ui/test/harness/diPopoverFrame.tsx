/**
 * TEST-ONLY SURFACE (B2c round 15): the app's own AnchoredPopover in a clipping frame of a chosen width, its trigger at a chosen place. The cases
 * (`?case=`), frames measured from the window's left edge:
 *  - `neither`: the Reviewer's case. A 220 px frame (200 to 420), a 150 px trigger at 230 (right edge 380): end alignment needs a right edge of at
 *    least 448 and start alignment a left edge of at most 172, so NEITHER fits a 240 px popover;
 *  - `narrow`: a 180 px frame (200 to 380), NARROWER than the 240 px floor, the trigger at 210;
 *  - `roomy`: a 700 px frame (200 to 900), the trigger at 600: the ordinary case, the control.
 * The items are the product's kinds: two enabled, one held with a long reason drawn in it.
 */
import '../../src/index.css';

import { createRoot } from 'react-dom/client';

import { AnchoredPopover, PopoverItem } from '../../src/components/ui/AnchoredPopover.tsx';

const CASES = {
  neither: { frame: 220, offset: 30, trigger: 150 },
  narrow: { frame: 180, offset: 10, trigger: 120 },
  roomy: { frame: 700, offset: 400, trigger: 150 },
} as const;

export function Harness() {
  const key = (new URLSearchParams(window.location.search).get('case') ?? 'roomy') as keyof typeof CASES;
  const c = CASES[key] ?? CASES.roomy;
  return (
    <div className="frame" data-testid="h-frame" data-case={key} style={{ width: c.frame }}>
      <div className="row" style={{ paddingLeft: c.offset }}>
        <span style={{ display: 'inline-block', width: c.trigger }}>
          <AnchoredPopover label="More actions" dialogLabel="More actions" testid="h-pop" triggerClassName="trigger">
            {(dismiss) => (
              <>
                <PopoverItem label="Comments" testid="h-item-1" onSelect={() => dismiss('to-trigger')} />
                <PopoverItem label="Evidence and review" testid="h-item-2" onSelect={() => dismiss('to-trigger')} />
                <PopoverItem
                  label="Herdr"
                  testid="h-item-3"
                  held="Herdr is not offered: this Mission is archived, so it is read-only."
                  onSelect={() => dismiss('to-trigger')}
                />
              </>
            )}
          </AnchoredPopover>
        </span>
      </div>
    </div>
  );
}

createRoot(document.getElementById('root')!).render(<Harness />);
