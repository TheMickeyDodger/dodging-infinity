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
