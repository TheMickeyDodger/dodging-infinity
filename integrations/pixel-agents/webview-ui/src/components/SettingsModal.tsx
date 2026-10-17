import { useRef, useState } from 'react';

import { JSON_FILE_ACCEPT } from '../constants.js';
import { isSoundEnabled, setSoundEnabled } from '../notificationSound.js';
import { isBrowserRuntime } from '../runtime.js';
import { transport } from '../transport/index.js';
import { Button } from './ui/Button.js';
import { Checkbox } from './ui/Checkbox.js';
import { MenuItem } from './ui/MenuItem.js';
import { Modal } from './ui/Modal.js';

interface SettingsModalProps {
  isOpen: boolean;
  onClose: () => void;
  isDebugMode: boolean;
  onToggleDebugMode: () => void;
  alwaysShowOverlay: boolean;
  onToggleAlwaysShowOverlay: () => void;
  /** Whether headless agents (adopted, no terminal to focus) render translucent. */
  ghostHeadlessAgents: boolean;
  onToggleGhostHeadlessAgents: () => void;
  externalAssetDirectories: string[];
  watchAllSessions: boolean;
  onToggleWatchAllSessions: () => void;
  /** ACTUAL install state (the hooksStatus message), not the hooksEnabled
   *  preference. The preference defaults to true while first-run consent is
   *  still pending, so binding the checkbox to it renders "on" over an empty
   *  ~/.claude/settings.json. */
  hooksInstalled: boolean;
  onToggleHooksEnabled: () => void;
  /** Whether the areas overlay is rendered outside of the Areas edit tool. */
  showAreas: boolean;
  onToggleShowAreas: () => void;
  /** Hide the Show Areas checkbox entirely when areas are unavailable. */
  showAreasAvailable: boolean;
  /** Browser-native layout export (standalone only; VS Code uses the host save dialog). */
  onExportLayout: () => void;
  /** Browser-native layout import from a chosen file (standalone only). */
  onImportLayout: (file: File) => void;
  /** DI office (browser): export the ambient snapshot (residents and trains) as ONE file. */
  onExportAmbient?: () => void;
  /** DI office (browser): Import Layout and Import Ambient Snapshot open the ONE paste surface
   *  (PasteImportModal) instead of a file chooser. */
  onPasteImport?: (kind: 'layout' | 'ambient') => void;
  /** DI office: hide the integrations DI does not act on (their messages are dropped) and
   *  say so, instead of offering toggles that would do nothing. Visibility and wording only. */
  diMode?: boolean;
}

export function SettingsModal({
  isOpen,
  onClose,
  isDebugMode,
  onToggleDebugMode,
  alwaysShowOverlay,
  onToggleAlwaysShowOverlay,
  ghostHeadlessAgents,
  onToggleGhostHeadlessAgents,
  externalAssetDirectories,
  watchAllSessions,
  onToggleWatchAllSessions,
  hooksInstalled,
  onToggleHooksEnabled,
  showAreas,
  onToggleShowAreas,
  showAreasAvailable,
  onExportLayout,
  onImportLayout,
  onExportAmbient,
  onPasteImport,
  diMode = false,
}: SettingsModalProps) {
  const [soundLocal, setSoundLocal] = useState(isSoundEnabled);
  const fileInputRef = useRef<HTMLInputElement>(null);
  // DI office in a browser: imports are by PASTED JSON. The native file chooser could not open a
  // valid file in the Operator's browser (cause not diagnosed), so no chooser is offered here.
  const pasteImports = diMode && isBrowserRuntime && !!onPasteImport;
  const ambientActions = pasteImports && !!onExportAmbient;
  const [assetDirDraft, setAssetDirDraft] = useState('');

  return (
    <Modal isOpen={isOpen} onClose={onClose} title="Settings">
      {/* Open Sessions Folder opens an OS file manager — impossible in the browser. */}
      {!isBrowserRuntime && (
        <MenuItem
          onClick={() => {
            transport.send({ type: 'openSessionsFolder' });
            onClose();
          }}
        >
          Open Sessions Folder
        </MenuItem>
      )}
      <MenuItem
        onClick={() => {
          if (isBrowserRuntime) {
            onExportLayout();
          } else {
            transport.send({ type: 'exportLayout' });
          }
          onClose();
        }}
      >
        Export Layout
      </MenuItem>
      <MenuItem
        onClick={() => {
          if (pasteImports) {
            onPasteImport?.('layout');
            onClose();
          } else if (isBrowserRuntime) {
            // Open the native file picker; the import is applied in onChange below.
            fileInputRef.current?.click();
          } else {
            transport.send({ type: 'importLayout' });
            onClose();
          }
        }}
      >
        {pasteImports ? 'Import Layout (paste JSON)' : 'Import Layout'}
      </MenuItem>
      {isBrowserRuntime && !pasteImports && (
        <input
          ref={fileInputRef}
          type="file"
          accept={JSON_FILE_ACCEPT}
          className="hidden"
          onChange={(e) => {
            const file = e.target.files?.[0];
            // Reset the value so re-selecting the same file fires change again.
            e.target.value = '';
            if (file) {
              onImportLayout(file);
              onClose();
            }
          }}
        />
      )}
      {ambientActions && (
        <>
          {/* One action, one file: the decorative residents (with their pending legs) and the
              trains (progress, operating state, onboard riders). Never bundled behind another
              download. */}
          <MenuItem
            onClick={() => {
              onExportAmbient?.();
              onClose();
            }}
          >
            Export Ambient Snapshot
          </MenuItem>
          <MenuItem
            onClick={() => {
              onPasteImport?.('ambient');
              onClose();
            }}
          >
            Import Ambient Snapshot (paste JSON)
          </MenuItem>
        </>
      )}
      {diMode && (
        <div className="py-4 px-10 text-xs text-text-muted" data-testid="di-settings-note">
          DI office: a read-only view. These settings are display preferences only; no agent action
          is routed through this office. Not available here: Watch All Sessions, Instant Detection
          (Hooks) and external asset directories. Those are Claude Code integrations, and this
          office never installs hooks, scans sessions or loads assets from other folders. Missions
          and agents come only from the DI backend.
        </div>
      )}
      {/* Browser has no native directory picker, so accept a typed absolute path. */}
      {diMode ? null : isBrowserRuntime ? (
        <div className="flex items-center gap-4 py-4 px-10">
          <input
            type="text"
            value={assetDirDraft}
            placeholder="Absolute asset directory path"
            onChange={(e) => setAssetDirDraft(e.target.value)}
            className="flex-1 min-w-0 text-xs py-2 px-4 bg-bg border-2 border-border rounded-none text-text"
          />
          <Button
            variant="default"
            size="sm"
            onClick={() => {
              const path = assetDirDraft.trim();
              if (!path) return;
              transport.send({ type: 'addExternalAssetDirectory', path });
              setAssetDirDraft('');
            }}
            className="shrink-0"
          >
            Add
          </Button>
        </div>
      ) : (
        <MenuItem
          onClick={() => {
            transport.send({ type: 'addExternalAssetDirectory' });
            onClose();
          }}
        >
          Add Asset Directory
        </MenuItem>
      )}
      {(diMode ? [] : externalAssetDirectories).map((dir) => (
        <div key={dir} className="flex items-center justify-between py-4 px-10 gap-8">
          <span
            className="text-xs text-text-muted overflow-hidden text-ellipsis whitespace-nowrap"
            title={dir}
          >
            {dir.split(/[/\\]/).pop() ?? dir}
          </span>
          <Button
            variant="ghost"
            size="sm"
            onClick={() => transport.send({ type: 'removeExternalAssetDirectory', path: dir })}
            className="shrink-0"
          >
            x
          </Button>
        </div>
      ))}
      <Checkbox
        label="Sound Notifications"
        checked={soundLocal}
        onChange={() => {
          const newVal = !isSoundEnabled();
          setSoundEnabled(newVal);
          setSoundLocal(newVal);
          transport.send({ type: 'setSoundEnabled', enabled: newVal });
        }}
      />
      {!diMode && (
        <Checkbox
          label="Watch All Sessions"
          checked={watchAllSessions}
          onChange={onToggleWatchAllSessions}
        />
      )}
      {!diMode && (
        <Checkbox
          label="Instant Detection (Hooks)"
          checked={hooksInstalled}
          onChange={onToggleHooksEnabled}
        />
      )}
      <Checkbox
        label="Always Show Labels"
        checked={alwaysShowOverlay}
        onChange={onToggleAlwaysShowOverlay}
      />
      {/* Headless agents are the office's only terminal-less citizens in VS Code.
          Standalone has no terminals at all, so nothing there would ever ghost. */}
      {!isBrowserRuntime && (
        <Checkbox
          label="Display Headless as Ghosts"
          checked={ghostHeadlessAgents}
          onChange={onToggleGhostHeadlessAgents}
        />
      )}
      {showAreasAvailable && (
        <Checkbox label="Show Areas" checked={showAreas} onChange={onToggleShowAreas} />
      )}
      <Checkbox label="Debug View" checked={isDebugMode} onChange={onToggleDebugMode} />
    </Modal>
  );
}
