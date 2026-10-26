/**
 * Visual lock: the scan panel rendered with the 2026-10-25 specimen over an office still.
 * Fixture / screenshot only.
 */
import '../../src/index.css';

import { useState } from 'react';
import { createRoot } from 'react-dom/client';

import world from '../../../../../assets/pixel-world/team-discovery-default-office.png';
import { DI_SCAN_LOCK, type DiScanView } from '../../src/components/diScanModel.ts';
import { DiScanPanel } from '../../src/components/DiScanPanel.tsx';

function requestedView(): DiScanView {
  const value = new URLSearchParams(window.location.search).get('di-scan-view');
  if (value === 'mission' || value === 'diff' || value === 'herdr' || value === 'comments') return value;
  return 'list';
}

export function Lock() {
  const [gone, setGone] = useState(false);
  if (gone) return null;
  return (
    <div className="di-scan-lock-stage" style={{ backgroundImage: `url("${world}")` }}>
      <aside className="di-scan" style={{ width: '32.8%' }} data-testid="di-office-panel">
        <DiScanPanel
          rows={DI_SCAN_LOCK}
          initialSelectedId={DI_SCAN_LOCK[0]?.id ?? null}
          initialView={requestedView()}
          onMinimize={() => setGone(true)}
        />
      </aside>
    </div>
  );
}

const root = document.getElementById('root');
if (root) createRoot(root).render(<Lock />);
