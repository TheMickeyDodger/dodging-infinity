/**
 * Visual lock: the scan panel rendered with the 2026-10-25 specimen over an office still.
 * Fixture / screenshot only.
 */
import '../../src/index.css';

import { useState } from 'react';
import { createRoot } from 'react-dom/client';

import world from '../../../../../assets/pixel-world/team-discovery-default-office.png';
import type { DiMission, DiOfficeState } from '../../../core/src/messages.ts';
import { DI_SCAN_LOCK, type DiScanRow, type DiScanView, scanRowsFromOffice } from '../../src/components/diScanModel.ts';
import { DiScanPanel } from '../../src/components/DiScanPanel.tsx';

const DIGEST = '3033dab9ca1127b3a8dc907fcbbfbeb522aa461d3035277ff41413fa58820bcb';

function seat(role: string, logical: string, id: string): Record<string, unknown> {
  return {
    characterId: 1,
    coverage: 'direct',
    memberRef: id,
    missionRef: 'live-digest',
    logical,
    role,
    agent: `w-${logical}`,
    provider: 'claude',
    classification: 'present',
    activity: role === 'lead' ? 'busy' : 'waiting',
    live: true,
    chain: 'direct',
    chainTaskId: null,
  };
}

/** The live failure Travis photographed: handoff prefix as the goal, and the same roles listed twice. */
function digestRows(): DiScanRow[] {
  const mission = {
    missionRef: 'live-digest',
    sourceId: 'di_herdr_tasks',
    nativeId: '20261024-120000-abcdef',
    sourceKind: 'herdr_task',
    lifecycleModel: 'herdr.task/1',
    lifecycleState: 'ACTIVE',
    standing: 'active',
    bindingState: 'bound',
    bindingReason: 'canonical_chain_complete',
    producerDeclared: 'herdctl',
    live: true,
    notLiveReason: null,
    objective: {
      state: 'observed',
      value: `AUTHORIZED MISSION mn-ba1e47939b4f6b443cd34174b9b1732 (revision 1, proposal digest ${DIGEST}). This is the approved Mission content only, NOT an engineering plan.\n\nOBJECTIVE\nRedesign the core panel system.`,
      reason: null,
    },
    updatedAt: { state: 'not_recorded', value: null, reason: null },
    identitySource: { state: 'not_recorded', value: null, reason: null },
    paused: { state: 'not_applicable', value: null, reason: null },
    cancelRequested: { state: 'not_applicable', value: null, reason: null },
    ambiguity: { state: 'not_applicable', value: null, reason: null },
    members: {
      state: 'observed',
      reason: null,
      members: [
        seat('supervisor', 'supervisor', 's1'),
        seat('supervisor', 'supervisor', 's2'),
        seat('lead', 'lead1', 'l1'),
        seat('lead', 'lead1', 'l2'),
        seat('executor', 'mia', 'e1'),
        seat('reviewer', 'sam', 'r1'),
      ],
    },
    v2: { projected: false },
  } as unknown as DiMission;
  const office = {
    live: true,
    notLiveReason: null,
    envelope: { envelopeProblem: null, retryAfterSeconds: null, fresh: true },
    missions: { state: 'observed', reason: null, missions: [mission] },
    reception: { entries: [] },
  } as unknown as DiOfficeState;
  return scanRowsFromOffice(office);
}

function requestedView(): DiScanView {
  const value = new URLSearchParams(window.location.search).get('di-scan-view');
  if (value === 'mission' || value === 'diff' || value === 'herdr' || value === 'comments') return value;
  return 'list';
}

export function Lock() {
  const [gone, setGone] = useState(false);
  if (gone) return null;
  const digest = new URLSearchParams(window.location.search).get('di-scan-case') === 'digest';
  const rows = digest ? digestRows() : DI_SCAN_LOCK;
  const view = requestedView();
  return (
    <div className="di-scan-lock-stage" style={{ backgroundImage: `url("${world}")` }}>
      {digest && view !== 'list' && (
        <div
          data-testid="agent-overlay"
          style={{
            position: 'fixed',
            zIndex: 42,
            left: '42%',
            top: 168,
            padding: '6px 10px',
            background: 'var(--di-scan-chip)',
            color: 'var(--di-scan-text)',
            font: '12px var(--di-font-ui)',
          }}
        >
          executor: claude | idle
        </div>
      )}
      <aside className="di-scan" style={{ width: view === 'list' ? '32.8%' : '100%' }} data-testid="di-office-panel">
        <DiScanPanel
          rows={rows}
          initialSelectedId={rows[0]?.id ?? null}
          initialView={view}
          onMinimize={() => setGone(true)}
        />
      </aside>
    </div>
  );
}

const root = document.getElementById('root');
if (root) createRoot(root).render(<Lock />);
