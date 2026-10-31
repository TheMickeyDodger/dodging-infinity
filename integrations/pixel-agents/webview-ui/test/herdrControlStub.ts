/**
 * A Herdr control for tests: the control's STATE is set directly (the model's own shapes), so a rendered panel can be shown in each state the server can leave a Mission in, without a transport. `launch` and
 * `requestStatus` record what the panel asked for, so a test can show what a click asks for and what it does not.
 */
import type { DiHerdrControl, HerdrCheck, HerdrLaunch, HerdrState } from '../src/components/diHerdrModel.ts';

/** The "available" check, typed as that variant so a test can spread it and change one field. */
export const AVAILABLE: Extract<HerdrCheck, { kind: 'available' }> = {
  kind: 'available',
  agent: 'he-exec1',
  identityBasis: 'runtime_identity_verified',
  workspaceId: 'w6V',
  windowMinCols: 190,
  windowMinRows: 49,
  matchedPaneId: 'w6V:p3',
  terminalLabel: 'Terminal',
};

export interface StubOptions {
  check?: HerdrCheck;
  launch?: HerdrLaunch;
  unvouched?: string | null;
  pending?: HerdrState['pending'];
}

/** A control whose state for `missionRef` is as given (the default: the server said a launch is available). */
export function herdrControl(
  missionRef: string,
  options: StubOptions = {},
): DiHerdrControl & { asked: { launch: string[]; status: string[] } } {
  const asked = { launch: [] as string[], status: [] as string[] };
  return {
    state: {
      pending: options.pending ?? null,
      missions: new Map([[missionRef, { check: options.check ?? AVAILABLE, launch: options.launch ?? { kind: 'idle' } }]]),
      unvouched: options.unvouched ?? null,
    },
    requestStatus: (ref) => void asked.status.push(ref),
    launch: (ref) => void asked.launch.push(ref),
    asked,
  };
}
