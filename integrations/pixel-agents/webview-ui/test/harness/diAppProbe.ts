/**
 * FIXTURE / TEST ONLY (round 11). Loaded by `di-app.html` BEFORE the real app entry. It changes
 * no behaviour: it records which `OfficeState` instance the real renderer is drawing (the App's
 * own, module-private one) by wrapping `getCharacters`, which the renderer calls every frame, so
 * the reconciliation regression can OBSERVE `selectedAgentId` — what `removeAgent`, a canvas click
 * or the panel did to it — instead of holding a selection of its own.
 */
import { OfficeState } from '../../src/office/engine/officeState.ts';

declare global {
  interface Window {
    __diHarnessOffice?: OfficeState;
  }
}

const original = OfficeState.prototype.getCharacters;
OfficeState.prototype.getCharacters = function (this: OfficeState) {
  window.__diHarnessOffice = this;
  return original.call(this);
};

// ALSO exposed through update(dt), which the game loop calls every frame whether or not anything is painted. The renderer returns before its work (and so never calls
// getCharacters) while the world has no size (OfficeCanvas.tsx: a zero-width or zero-height world is not drawn), so at a fresh narrow page with the office open the
// instance was never exposed and tests waiting for it timed out. Observed there (clone-only diagnostic): getCharacters called 0 times, update advancing about 60 times a
// second, the OfficeState healthy (the character present, its spawn finished). Delegates to the original engine method unchanged.
const originalUpdate = OfficeState.prototype.update;
OfficeState.prototype.update = function (this: OfficeState, ...args: Parameters<OfficeState['update']>) {
  window.__diHarnessOffice = this;
  // every argument, the return value and anything the engine throws pass through untouched: no try/catch, nothing re-wrapped
  return originalUpdate.apply(this, args);
};
