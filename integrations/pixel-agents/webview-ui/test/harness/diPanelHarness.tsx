/// <reference types="vite/client" />
/**
 * FIXTURE / TEST ONLY. Mounts the REAL DI office panel (`DiOfficePanel`) over the labelled office
 * state that `server/__tests__/diOfficeStateExport.test.ts` projects from a packaged fixture, with
 * a minimal stand-in for the office canvas state (the panel reads only the fields below). The
 * browser regression (`test/diPanelKeyboard.test.ts`) drives it with real key presses.
 */
import '../../src/index.css';

import { useEffect, useState } from 'react';
import { createRoot } from 'react-dom/client';

import type { DiOfficeState } from '../../../core/src/messages.ts';
import type { DiCodeReviewAccess } from '../../src/components/DiCodeReviewEntry.tsx';
import type { DiMissionRequestAccess } from '../../src/components/DiMissionRequestEntry.tsx';
import { INITIAL_STATE } from '../../src/components/diMissionRequestModel.ts';
import { DiOfficePanel } from '../../src/components/DiOfficePanel.tsx';
import type { OfficeState } from '../../src/office/engine/officeState.ts';
import fixture from '../fixtures/di-office-state-session-mismatch-v2.FIXTURE.json';

const initial: DiOfficeState = {
  ...(fixture.state as unknown as DiOfficeState),
  fixtureMode: true,
};

function drawnIds(state: DiOfficeState): number[] {
  const ids: number[] = [];
  for (const m of state.missions.missions)
    for (const member of m.members.members)
      if (member.characterId !== null) ids.push(member.characterId);
  for (const a of state.runtimeAgents?.agents ?? [])
    if (a.characterId !== null) ids.push(a.characterId);
  return ids;
}

/** The fields of the office canvas state the panel reads; everything else is absent. */
const office = {
  characters: new Map<number, unknown>(drawnIds(initial).map((id) => [id, {}])),
  selectedAgentId: null as number | null,
  cameraFollowId: null as number | null,
  areaMappings: {} as Record<string, string[]>,
  getLayout: () => ({ areas: [] as Array<{ label: string; color: string }> }),
};

/**
 * FIXTURE stand-ins for the App's two entry seams, recording what a person's action reached: the live
 * code review (offered per member by the office state) and the Mission request workspace (configured:
 * availability "ok"). They open nothing themselves; the App's own tests cover the dialogs they open.
 */
const calls = {
  review: [] as Array<{
    missionRef: string;
    memberRef: string;
    taskId: string;
    listing: string;
    openerTestId: string | null;
  }>,
  mission: [] as Array<{ tab: string; openerTestId: string | null }>,
};
const review: DiCodeReviewAccess = {
  open: (target, opener) =>
    calls.review.push({
      missionRef: target.missionRef,
      memberRef: target.memberRef,
      taskId: target.taskId,
      listing: target.listing,
      openerTestId: opener.getAttribute('data-testid'),
    }),
};
const missionAccess: DiMissionRequestAccess = {
  state: { ...INITIAL_STATE, availability: { kind: 'ok' } },
  open: (tab, opener) =>
    calls.mission.push({ tab, openerTestId: opener.getAttribute('data-testid') }),
};

const listeners = new Set<(next: DiOfficeState) => void>();

export function Harness() {
  const [state, setState] = useState(initial);
  useEffect(() => {
    listeners.add(setState);
    return () => {
      listeners.delete(setState);
    };
  }, []);
  return (
    <DiOfficePanel
      state={state}
      officeState={office as unknown as OfficeState}
      collapsed={false}
      onCollapsedChange={() => undefined}
      review={review}
      mission={missionAccess}
    />
  );
}

declare global {
  interface Window {
    __diHarness: {
      office: typeof office;
      calls: typeof calls;
      initial: DiOfficeState;
      /** Publish a new office state (a later projection), as the transport would. */
      update: (edit: (state: DiOfficeState) => DiOfficeState) => void;
      current: () => DiOfficeState;
    };
  }
}

let latest = initial;
window.__diHarness = {
  office,
  calls,
  initial,
  update: (edit) => {
    latest = edit(structuredClone(latest));
    office.characters = new Map(drawnIds(latest).map((id) => [id, {}]));
    for (const listener of listeners) listener(latest);
  },
  current: () => latest,
};

createRoot(document.getElementById('root')!).render(<Harness />);
