/**
 * Visual lock: the scan panel rendered with the labelled SAMPLE specimen over an office still. Fixture / screenshot only.
 *
 * `di-scan-view`: the screen to open on. `di-scan-cast=v3`: the v3 sample cast (the five Missions of references 03 and 04)
 * with the sample Reception that points at them; the default is the pixel-lock cast (DI_SCAN_LOCK), which has no
 * Reception desk. `di-scan-case=digest`: the live-digest regression row; `di-scan-case=limit`: 15 sample Orchestrators
 * running (AC-4.3-49). Every page says the data is a sample.
 */
import '../../src/index.css';

import { useState } from 'react';
import { createRoot } from 'react-dom/client';

import world from '../../../../../assets/pixel-world/team-discovery-default-office.png';
import type { DiMission, DiOfficeState } from '../../../core/src/messages.ts';
import {
  DI_SCAN_LOCK,
  DI_SCAN_V3_CAST,
  type DiScanRow,
  type DiScanView,
  known,
  scanRowsFromOffice,
} from '../../src/components/diScanModel.ts';
import {
  DI_NEW_MISSION_SPECIMEN,
  DI_ORCHESTRATOR_LIMIT,
  DI_RECEPTION_SPECIMEN,
} from '../../src/components/diScanPagesModel.ts';
import { DiScanPanel } from '../../src/components/DiScanPanel.tsx';
import { DI_PANEL_WIDTH_PX } from '../../src/constants.ts';

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

const VIEWS: readonly DiScanView[] = [
  'list',
  'mission',
  'diff',
  'comments',
  'missions',
  'reception',
  'new',
  'proposal',
];

function requestedView(): DiScanView {
  const value = new URLSearchParams(window.location.search).get('di-scan-view');
  return VIEWS.find((view) => view === value) ?? 'list';
}

/**
 * SAMPLE Missions whose facts are NOT KNOWN (the unknown case, B-H1 round 5): the office has no Reception and is not live,
 * so whether each needs you or is running is unknown, and the panel must say so per filter.
 */
function unknownRows(): DiScanRow[] {
  const mission = (ref: string, title: string) =>
    ({
      missionRef: ref,
      sourceId: 'di_herdr_tasks',
      nativeId: ref,
      sourceKind: 'herdr_task',
      lifecycleModel: 'herdr.task/1',
      lifecycleState: 'ACTIVE',
      standing: 'active',
      bindingState: 'bound',
      bindingReason: 'canonical_chain_complete',
      producerDeclared: 'herdctl',
      live: false,
      notLiveReason: null,
      objective: { state: 'observed', value: title, reason: null },
      updatedAt: { state: 'not_recorded', value: null, reason: null },
      identitySource: { state: 'not_recorded', value: null, reason: null },
      paused: { state: 'not_applicable', value: null, reason: null },
      cancelRequested: { state: 'not_applicable', value: null, reason: null },
      ambiguity: { state: 'not_applicable', value: null, reason: null },
      members: { state: 'observed', reason: null, members: [] },
      v2: { projected: false },
    }) as unknown as DiMission;
  const office = {
    live: false,
    notLiveReason: 'client_expired: no new office state',
    envelope: { envelopeProblem: null, retryAfterSeconds: null, fresh: false },
    missions: {
      state: 'observed',
      reason: null,
      missions: [mission('unknown-1', 'Sample Mission one'), mission('unknown-2', 'Sample Mission two')],
    },
    times: { evaluatedAt: 1_800_000_000 },
  } as unknown as DiOfficeState;
  return scanRowsFromOffice(office);
}

/** 15 SAMPLE Missions read as running (the limit case). */
function limitRows(): DiScanRow[] {
  return Array.from({ length: DI_ORCHESTRATOR_LIMIT }, (_, i) => ({
    ...DI_SCAN_LOCK[0]!,
    id: `limit-${i + 1}`,
    title: `Sample Orchestrator ${i + 1}`,
    needsYou: known(false),
    pills: [{ id: 'building', label: 'Building', tone: 'building' as const }],
    state: { ...DI_SCAN_LOCK[0]!.state, running: known(true), awaitingApproval: false },
  }));
}

export function Lock() {
  const [gone, setGone] = useState(false);
  if (gone) return null;
  const params = new URLSearchParams(window.location.search);
  const digest = params.get('di-scan-case') === 'digest';
  const limit = params.get('di-scan-case') === 'limit';
  const unknown = params.get('di-scan-case') === 'unknown';
  const v3 = params.get('di-scan-cast') === 'v3';
  const rows = digest
    ? digestRows()
    : limit
      ? limitRows()
      : unknown
        ? unknownRows()
        : v3
          ? DI_SCAN_V3_CAST
          : DI_SCAN_LOCK;
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
      {/* pass C (C-V1): Home's panel is the PRODUCT's dock width (DI_PANEL_WIDTH_PX), never a proportion of the window: a
          32.8% aside agreed with the product only at 1280 (295 px at 900, 548 px at 1672) */}
      <aside
        className="di-scan"
        style={{ width: view === 'list' ? `${DI_PANEL_WIDTH_PX}px` : '100%' }}
        data-testid="di-office-panel"
      >
        <DiScanPanel
          rows={rows}
          initialSelectedId={rows[0]?.id ?? null}
          initialView={view}
          onMinimize={() => setGone(true)}
          specimen={!digest}
          reception={v3 ? DI_RECEPTION_SPECIMEN : null}
          newMission={DI_NEW_MISSION_SPECIMEN}
        />
      </aside>
    </div>
  );
}

const root = document.getElementById('root');
if (root) createRoot(root).render(<Lock />);
