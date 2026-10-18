/// <reference types="vite/client" />
/**
 * FIXTURE / TEST ONLY (item 2). Mounts the REAL DI office panel (`DiOfficePanel`) over the
 * evidence library ONE real DiOffice projected from the backend's exported answers
 * (`server/__tests__/diOfficeStateExport.test.ts` → di-office-state-evidence-v2.FIXTURE.json),
 * with a content seam that answers each read with the diEvidenceContent message that SAME office
 * relayed for it — as the transport would deliver it. Every read the panel asks for is recorded,
 * so the browser regression can prove what was asked (a reference, never a path).
 */
import '../../src/index.css';

import { useEffect, useMemo, useState } from 'react';
import { createRoot } from 'react-dom/client';

import type { DiEvidenceContent, DiOfficeState } from '../../../core/src/messages.ts';
import type { EvidenceSelection } from '../../src/components/diEvidenceFormat.ts';
import type { DiEvidenceAccess } from '../../src/components/DiEvidenceLibrary.tsx';
import { DiOfficePanel } from '../../src/components/DiOfficePanel.tsx';
import type { OfficeState } from '../../src/office/engine/officeState.ts';
import fixture from '../fixtures/di-office-state-evidence-v2.FIXTURE.json';

const live = fixture.states.live as unknown as DiOfficeState;
const stale = fixture.states.stale as unknown as DiOfficeState;
const replies = fixture.replies as unknown as Record<string, DiEvidenceContent>;

const office = {
  characters: new Map<number, unknown>(),
  selectedAgentId: null as number | null,
  cameraFollowId: null as number | null,
  areaMappings: {} as Record<string, string[]>,
  getLayout: () => ({ areas: [] as Array<{ label: string; color: string }> }),
};

const requests: unknown[] = [];
let setState: (s: DiOfficeState) => void = () => undefined;

export function Harness() {
  const [state, set] = useState<DiOfficeState>(live);
  const [content, setContent] = useState<DiEvidenceContent | null>(null);
  const [pending, setPending] = useState<EvidenceSelection | null>(null);
  useEffect(() => {
    setState = set;
  }, []);
  const access = useMemo<DiEvidenceAccess>(
    () => ({
      content,
      pending,
      request: (selection) => {
        // Recorded exactly as the panel asked (the transport sends these keys and no others).
        requests.push(JSON.parse(JSON.stringify(selection)));
        setPending(selection);
        const reply = Object.values(replies).find(
          (r) => r.artifactId === selection.artifactId && r.cursor === selection.cursor,
        );
        // Answered on a later task, as a socket would.
        setTimeout(() => {
          if (reply) setContent(JSON.parse(JSON.stringify(reply)) as DiEvidenceContent);
        }, 10);
      },
    }),
    [content, pending],
  );
  return (
    <DiOfficePanel
      state={state}
      officeState={office as unknown as OfficeState}
      collapsed={false}
      onCollapsedChange={() => undefined}
      evidence={access}
    />
  );
}

declare global {
  interface Window {
    __diEvidence: {
      requests: unknown[];
      replies: Record<string, DiEvidenceContent>;
      live: DiOfficeState;
      showStale: () => void;
      /** The listing as it would be after the Mission's journal moved. */
      showMoved: () => void;
      /** The same listing with another findings block on its review round. */
      showFindings: (findings: unknown) => void;
    };
  }
}

window.__diEvidence = {
  requests,
  replies,
  live,
  showStale: () => setState(stale),
  showFindings: (findings) => {
    const next = structuredClone(live);
    for (const m of next.missions.missions)
      for (const r of m.evidence?.reviews.items ?? [])
        (r as unknown as { findings: unknown }).findings = findings;
    setState(next);
  },
  showMoved: () => {
    const moved = structuredClone(live);
    for (const m of moved.missions.missions) if (m.evidence) m.evidence.cursor = 'e'.repeat(64);
    setState(moved);
  },
};

createRoot(document.getElementById('root')!).render(<Harness />);
