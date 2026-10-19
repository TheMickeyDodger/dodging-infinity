import { useCallback, useEffect, useMemo, useRef, useState } from 'react';

import { AMBIENT_SAVE_MAX_BYTES } from '../../core/src/ambientSave.js';
import { LAYOUT_MAX_FILE_BYTES } from '../../core/src/layoutLimits.js';
import { toMajorMinor } from './changelogData.js';
import { AmbientLegend } from './components/AmbientLegend.js';
import { BottomToolbar } from './components/BottomToolbar.js';
import { ChangelogModal } from './components/ChangelogModal.js';
import { ConnectionIndicator } from './components/ConnectionIndicator.js';
import { DebugView } from './components/DebugView.js';
import { DiCodeReview } from './components/DiCodeReview.js';
import type { DiCodeReviewAccess } from './components/DiCodeReviewEntry.js';
import type { EvidenceSelection } from './components/diEvidenceFormat.js';
import type { DiEvidenceAccess } from './components/DiEvidenceLibrary.js';
import {
  DI_FIXTURE_BANNER,
  diCharacterLabel,
  diCharacterTitle,
} from './components/diOfficeFormat.js';
import { DiOfficePanel } from './components/DiOfficePanel.js';
import { EditActionBar } from './components/EditActionBar.js';
import { IntroBubble } from './components/IntroBubble.js';
import { MigrationNotice } from './components/MigrationNotice.js';
import type { PasteImportResult } from './components/PasteImportModal.js';
import { PasteImportModal } from './components/PasteImportModal.js';
import { SettingsModal } from './components/SettingsModal.js';
import { Tooltip } from './components/Tooltip.js';
import { Modal } from './components/ui/Modal.js';
import { VersionIndicator } from './components/VersionIndicator.js';
import { WorldMap } from './components/WorldMap.js';
import { WorldViewControls } from './components/WorldViewControls.js';
import { ZoomControls } from './components/ZoomControls.js';
import {
  AMBIENT_SAVE_INTERVAL_MS,
  DI_DOCUMENT_TITLE,
  DI_FAVICON_DATA_URI,
  DI_PANEL_WIDTH_PX,
  DI_WORLD_REFLOW_MS,
  WORLD_KEY_PAN_FRACTION,
  ZOOM_MAX,
  ZOOM_MIN,
} from './constants.js';
import { useDiCodeReview } from './hooks/useDiCodeReview.js';
import { useEditorActions } from './hooks/useEditorActions.js';
import { useEditorKeyboard } from './hooks/useEditorKeyboard.js';
import { useExtensionMessages } from './hooks/useExtensionMessages.js';
import { useIntroTour } from './hooks/useIntroTour.js';
import { prepareAmbientText } from './office/ambient/ambientIntake.js';
import {
  snapshotAgents,
  snapshotAmbient,
  snapshotTrainsRaw,
} from './office/ambient/ambientProbe.js';
import { OfficeCanvas } from './office/components/OfficeCanvas.js';
import { ToolOverlay } from './office/components/ToolOverlay.js';
import { EditorState } from './office/editor/editorState.js';
import { EditorToolbar } from './office/editor/EditorToolbar.js';
import { OfficeState } from './office/engine/officeState.js';
import { diFitZoom } from './office/layout/diWorld.js';
import { exportAmbientToFile, exportLayoutToFile } from './office/layout/exportLayout.js';
import { getCatalogEntry, isRotatable } from './office/layout/furnitureCatalog.js';
import { applyImport, importFileTooLarge } from './office/layout/layoutIntake.js';
import type { FootprintOf, WorldViewKind } from './office/layout/worldViews.js';
import {
  clampPanToWorld,
  districtsOf,
  frameDistrict,
  frameWorldView,
  isLargeWorld,
  panForZoomAroundPoint,
  panToWorldPoint,
  WORLD_VIEW_KINDS,
} from './office/layout/worldViews.js';
import { subscribeReducedMotion } from './office/reducedMotion.js';
import { resetRenderCaches } from './office/renderCaches.js';
import { getPetCount } from './office/sprites/petSpriteData.js';
import type { OfficeLayout } from './office/types.js';
import { EditTool } from './office/types.js';
import { isBrowserRuntime, isE2E } from './runtime.js';
import { installTestHooks } from './testHooks.js';
import { transport } from './transport/index.js';

// Game state lives outside React — updated imperatively by message handlers
const officeStateRef = { current: null as OfficeState | null };
const editorState = new EditorState();

// Test-only observability hooks (message/sound logs, addAgent wrapper, selectAgent).
// Installed only under the e2e harness so they never patch prototypes or grow
// unbounded logs in a real user's session.
if (isE2E) installTestHooks(officeStateRef);

const footprintOf: FootprintOf = (type) => {
  const entry = getCatalogEntry(type);
  return entry
    ? { w: entry.footprintW, h: entry.footprintH, seat: entry.category === 'chairs' }
    : null;
};

function getOfficeState(): OfficeState {
  if (!officeStateRef.current) {
    officeStateRef.current = new OfficeState();
  }
  return officeStateRef.current;
}

function App() {
  // Browser runtime (dev or static dist): dispatch mock messages after the
  // useExtensionMessages listener has been registered.
  useEffect(() => {
    // browserMock is for Vite dev mode only (UI prototyping without a server).
    // In standalone server mode, the server sends all state over WebSocket.
    // In VS Code mode, the extension sends all state via postMessage.
    if (isBrowserRuntime && import.meta.env.DEV) {
      void import('./browserMock.js').then(({ dispatchMockMessages }) => dispatchMockMessages());
    }
  }, []);

  const editor = useEditorActions(getOfficeState, editorState);

  const isEditDirty = useCallback(
    () => editor.isEditMode && editor.isDirty,
    [editor.isEditMode, editor.isDirty],
  );

  const {
    diOfficeState,
    diEvidenceContent,
    diCodeReviewAnswer,
    diClientReason,
    agents,
    selectedAgent,
    agentTools,
    agentStatuses,
    subagentTools,
    subagentCharacters,
    layoutReady,
    layoutWasReset,
    loadedAssets,
    workspaceFolders,
    agentFolderNames,
    externalAssetDirectories,
    lastSeenVersion,
    extensionVersion,
    watchAllSessions,
    setWatchAllSessions,
    alwaysShowLabels,
    ghostHeadlessAgents,
    setGhostHeadlessAgents,
    hooksEnabled,
    hooksInstalled,
    hooksStatusSeq,
    hooksInfoShown,
    consentRequest,
    dismissConsentRequest,
    areaMappings,
    setAreaMappings,
    showAreas,
    setShowAreas,
    setDiViewport,
    reflowDiWorld,
    markDiWorldCustom,
    diWorldTick,
    diAnnounced,
  } = useExtensionMessages(getOfficeState, editor.setLastSavedLayout, isEditDirty);

  // Show migration notice once layout reset is detected
  const [migrationNoticeDismissed, setMigrationNoticeDismissed] = useState(false);
  const showMigrationNotice = layoutWasReset && !migrationNoticeDismissed;

  const [isChangelogOpen, setIsChangelogOpen] = useState(false);
  const [isSettingsOpen, setIsSettingsOpen] = useState(false);
  const [diPanelCollapsed, setDiPanelCollapsed] = useState(false);
  const [isHooksInfoOpen, setIsHooksInfoOpen] = useState(false);
  const [hooksTooltipDismissed, setHooksTooltipDismissed] = useState(false);
  const [isDebugMode, setIsDebugMode] = useState(false);
  const [, setReducedMotionEpoch] = useState(0);
  const [alwaysShowOverlay, setAlwaysShowOverlay] = useState(false);

  const currentMajorMinor = toMajorMinor(extensionVersion);

  const handleWhatsNewDismiss = useCallback(() => {
    transport.send({ type: 'setLastSeenVersion', version: currentMajorMinor });
  }, [currentMajorMinor]);

  const handleOpenChangelog = useCallback(() => {
    setIsChangelogOpen(true);
    transport.send({ type: 'setLastSeenVersion', version: currentMajorMinor });
  }, [currentMajorMinor]);

  // Sync alwaysShowOverlay from persisted settings
  useEffect(() => {
    setAlwaysShowOverlay(alwaysShowLabels);
  }, [alwaysShowLabels]);

  const handleToggleDebugMode = useCallback(() => setIsDebugMode((prev) => !prev), []);
  const handleToggleAlwaysShowOverlay = useCallback(() => {
    setAlwaysShowOverlay((prev) => {
      const newVal = !prev;
      transport.send({ type: 'setAlwaysShowLabels', enabled: newVal });
      return newVal;
    });
  }, []);

  // Toggle "Display headless as ghosts". setGhostHeadlessAgents also updates the
  // renderer's module copy, so the office redraws on the next frame.
  const handleToggleGhostHeadlessAgents = useCallback(() => {
    const next = !ghostHeadlessAgents;
    setGhostHeadlessAgents(next);
    transport.send({ type: 'setGhostHeadlessAgents', enabled: next });
  }, [ghostHeadlessAgents, setGhostHeadlessAgents]);

  const handleSelectAgent = useCallback((id: number) => {
    transport.send({ type: 'focusAgent', id });
  }, []);

  // The Intro's wire-facing state machine — which asks survive being mooted,
  // when a hooksStatus is this tour's install verdict — lives in useIntroTour
  // (pure reducer in introTourState.ts); the App only wires it to the bubble.
  const {
    intro,
    installFailed,
    installPending,
    onChoice: handleConsentChoice,
    onClose: handleIntroClose,
  } = useIntroTour({ consentRequest, hooksInstalled, hooksStatusSeq, dismissConsentRequest });

  // The Settings surface renders one provider today; its checkbox binds to
  // the Claude row of the per-provider install-state map.
  const claudeHooksInstalled = hooksInstalled['claude'] === true;

  // Mutate folder→Area mappings locally + send to server. Updates OfficeState in
  // the same tick so a follow-up agentCreated picks up the new mapping.
  const handleAreaMappingChange = useCallback(
    (folderName: string, areaLabel: string, action: 'add' | 'remove') => {
      const current = areaMappings[folderName] ?? [];
      let nextLabels: string[];
      if (action === 'add') {
        if (current.includes(areaLabel)) return;
        nextLabels = [...current, areaLabel];
      } else {
        nextLabels = current.filter((l) => l !== areaLabel);
      }
      const next = { ...areaMappings };
      if (nextLabels.length === 0) {
        delete next[folderName];
      } else {
        next[folderName] = nextLabels;
      }
      setAreaMappings(next);
      getOfficeState().setAreaMappings(next);
      transport.send({ type: 'saveAreaMappings', mappings: next });
    },
    [areaMappings, setAreaMappings],
  );

  // Toggle global Show Areas — persisted via setShowAreas message; runs server-
  // side through configPersistence.
  const onToggleShowAreas = useCallback(() => {
    const next = !showAreas;
    setShowAreas(next);
    transport.send({ type: 'setShowAreas', enabled: next });
  }, [showAreas, setShowAreas]);

  // When AREA_PAINT is active in the editor, force the overlay on even if the
  // user has toggled Show Areas off globally — they need to see what they're
  // editing. The selected area's overlay is alpha-bumped via activeAreaLabel.
  // DI office: identifiable in a browser tab (title + an original code-native favicon).
  // DI mode only — the ordinary surfaces keep their page title and icon.
  const isDiMode = diOfficeState !== null;

  // item 2: the evidence library's content seam. A read names an ADMITTED reference only (never
  // a path); the office answers this connection with one diEvidenceContent.
  const [diEvidencePending, setDiEvidencePending] = useState<EvidenceSelection | null>(null);
  const requestDiEvidence = useCallback((selection: EvidenceSelection) => {
    setDiEvidencePending(selection);
    transport.send({ type: 'diEvidenceRead', ...selection });
  }, []);
  const diEvidence = useMemo<DiEvidenceAccess>(
    () => ({ content: diEvidenceContent, pending: diEvidencePending, request: requestDiEvidence }),
    [diEvidenceContent, diEvidencePending, requestDiEvidence],
  );
  // item 3: the live code review. The side panel is only the entry; the reading is in the modal.
  // The session sends only reads by an admitted reference and clears itself the moment the
  // connection, the office state, the assignment or the agent cannot be vouched for.
  const review = useDiCodeReview(diOfficeState, diClientReason, diCodeReviewAnswer);
  const reviewOpener = useRef<HTMLElement | null>(null);
  const openReview = review.open;
  const reviewAccess = useMemo<DiCodeReviewAccess>(
    () => ({
      open: (target, opener) => {
        reviewOpener.current = opener;
        openReview(target);
      },
    }),
    [openReview],
  );
  // Round 15 F2: a read pending when the client stops vouching for the office state is
  // forgotten with it (its answer, if any, is dropped by the message handler).
  useEffect(() => {
    if (diClientReason !== null) setDiEvidencePending(null);
  }, [diClientReason]);
  useEffect(() => {
    if (!isDiMode) return;
    document.title = DI_DOCUMENT_TITLE;
    let icon = document.querySelector<HTMLLinkElement>("link[rel~='icon']");
    if (!icon) {
      icon = document.createElement('link');
      icon.rel = 'icon';
      document.head.appendChild(icon);
    }
    icon.type = 'image/svg+xml';
    icon.href = DI_FAVICON_DATA_URI;
  }, [isDiMode]);

  // DI office: REDUCED MOTION follows the OS preference, live. DI mode only — normal Pixel never
  // sets the flag, so its behaviour is unchanged (a deliberate scope decision, not an oversight).
  useEffect(() => {
    if (!isDiMode) return;
    const unsubscribe = subscribeReducedMotion((reduce) => {
      getOfficeState().reducedMotion = reduce;
      setReducedMotionEpoch((n) => n + 1); // re-render the React surfaces that read the flag
    });
    return () => {
      unsubscribe();
      getOfficeState().reducedMotion = false;
    };
  }, [isDiMode]);

  // DI office: AMBIENT RESIDENTS — a decorative local population that keeps the world inhabited
  // while the office is open, even with zero real agents. DI mode only; normal Pixel never sets it.
  useEffect(() => {
    if (!isDiMode) return;
    getOfficeState().setAmbientEnabled(true);
    return () => getOfficeState().setAmbientEnabled(false);
  }, [isDiMode]);

  // DI office: PERSISTENCE of the decorative ambient snapshot (M3 S4) — saved every
  // AMBIENT_SAVE_INTERVAL_MS while visible and whenever the view is hidden or closed; and a BOUNDED
  // catch-up when the view becomes visible again (at most AMBIENT_CATCHUP_MAX_SEC simulated, minus
  // anything already advanced while hidden). The game loop's hidden guard stops frames advancing while
  // hidden; nothing runs while closed or asleep. Local presentation state only.
  useEffect(() => {
    if (!isDiMode) return;
    const save = () => {
      const state = getOfficeState().serializeAmbient(Date.now());
      if (state)
        transport.send({
          type: 'saveAmbientState',
          state: state as unknown as Record<string, unknown>,
        });
    };
    let hiddenAt: number | null = null;
    let clockAtHide = 0;
    const onVisibility = () => {
      const os = getOfficeState();
      if (document.visibilityState === 'hidden') {
        hiddenAt = performance.now();
        clockAtHide = os.ambientClock;
        save();
      } else if (hiddenAt !== null) {
        // the hidden interval is covered ONCE: catch-up subtracts anything that did advance meanwhile
        os.catchUpAmbient((performance.now() - hiddenAt) / 1000, os.ambientClock - clockAtHide);
        hiddenAt = null;
      }
    };
    const timer = setInterval(() => {
      if (document.visibilityState === 'visible') save();
    }, AMBIENT_SAVE_INTERVAL_MS);
    document.addEventListener('visibilitychange', onVisibility);
    window.addEventListener('pagehide', save);
    return () => {
      clearInterval(timer);
      document.removeEventListener('visibilitychange', onVisibility);
      window.removeEventListener('pagehide', save);
    };
  }, [isDiMode]);

  // FIXTURE PREVIEW ONLY: a read-only probe of resident positions, so evidence can track named
  // ambient identities over time. Copies only; nothing here can change the simulation. Never
  // installed for a live (non-fixture) office.
  const isFixturePreview = diOfficeState?.fixtureMode === true;
  useEffect(() => {
    if (!isFixturePreview) return;
    window.__pixelAgentsAmbientProbe = {
      snapshot: () => snapshotAmbient(getOfficeState()),
      agents: () => snapshotAgents(getOfficeState()),
      transit: () => getOfficeState().transit?.snapshot() ?? null,
      halo: () => [...getOfficeState().haloCharacterIds(Date.now() / 1000)],
      restore: () => getOfficeState().lastAmbientRestore,
      trainsRaw: () => snapshotTrainsRaw(getOfficeState().transit),
    };
    return () => {
      delete window.__pixelAgentsAmbientProbe;
    };
  }, [isFixturePreview]);

  // DI office: RESPONSIVE FRAMING. The world's SHAPE follows the main area (the default
  // layout only — see diWorld.ts) and an integer fit-zoom in DEVICE pixels does the scaling.
  // The fit seeds and reflows; once the user zooms by hand it never overrides them.
  const userZoomedRef = useRef(false);
  // LARGE WORLDS: a named view (office / deck / forest) frames the camera until the user pans or
  // zooms by hand; choosing a view again hands framing back. Re-applied on resize and reload.
  const userNavigatedRef = useRef(false);
  const [worldView, setWorldView] = useState<WorldViewKind>('office');
  const [worldViewTick, setWorldViewTick] = useState(0);
  const [worldViewHeld, setWorldViewHeld] = useState(true);
  const { handleZoomChange, zoom: currentZoom } = editor;
  /**
   * THE user zoom path (zoom buttons, Ctrl+wheel). In a LARGE world the pan is reprojected so the
   * world point under the anchor stays put — the anchor is a canvas point in device pixels: the
   * cursor for the wheel, the viewport centre otherwise (defect S-N1). A legacy-size world keeps
   * its pan exactly as before. Framings and Map jumps do not come through here: they write the pan
   * FOR their target zoom and set the zoom with it, so nothing is reprojected twice.
   */
  const handleUserZoom = useCallback(
    (zoom: number, anchor?: { x: number; y: number }) => {
      userZoomedRef.current = true;
      userNavigatedRef.current = true;
      setWorldViewHeld(false);
      const layout = getOfficeState().getLayout();
      const el = containerRef.current;
      const next = Math.max(ZOOM_MIN, Math.min(ZOOM_MAX, zoom));
      if (el && next !== currentZoom && isLargeWorld(layout.cols, layout.rows)) {
        const r = el.getBoundingClientRect();
        const dpr = window.devicePixelRatio || 1;
        const w = Math.round(r.width * dpr);
        const h = Math.round(r.height * dpr);
        const p = editor.panRef.current;
        getOfficeState().cameraFollowId = null;
        editor.panRef.current = panForZoomAroundPoint(
          p.x,
          p.y,
          currentZoom,
          next,
          layout.cols,
          layout.rows,
          w,
          h,
          anchor?.x ?? w / 2,
          anchor?.y ?? h / 2,
        );
      }
      handleZoomChange(next);
    },
    [handleZoomChange, currentZoom, editor.panRef],
  );
  const handleUserPan = useCallback(() => {
    if (userNavigatedRef.current) return;
    userNavigatedRef.current = true;
    setWorldViewHeld(false);
  }, []);
  // The whole-world Map overlay (presentation only).
  const [mapOpen, setMapOpen] = useState(false);
  const handleSelectWorldView = useCallback((view: WorldViewKind) => {
    userNavigatedRef.current = false;
    setWorldViewHeld(true);
    setWorldView(view);
    setWorldViewTick((n) => n + 1);
    setMapOpen(false);
  }, []);
  const toggleMap = useCallback(() => setMapOpen((open) => !open), []);
  // The Map and the layout editor never share the screen: entering edit mode closes the Map.
  useEffect(() => {
    if (editor.isEditMode) setMapOpen(false);
  }, [editor.isEditMode]);
  const closeMap = useCallback(() => setMapOpen(false), []);
  const getCameraCanvas = useCallback(() => {
    const el = containerRef.current;
    if (!el) return null;
    const r = el.getBoundingClientRect();
    const dpr = window.devicePixelRatio || 1;
    return { w: Math.round(r.width * dpr), h: Math.round(r.height * dpr) };
  }, []);
  // A Map click: centre the camera there at the office view's usable zoom (a manual move).
  const jumpToWorldPoint = useCallback(
    (worldX: number, worldY: number) => {
      const size = getCameraCanvas();
      if (!size) return;
      const layout = getOfficeState().getLayout();
      const dpr = window.devicePixelRatio || 1;
      const zoom =
        frameWorldView('office', layout, footprintOf, size.w, size.h, dpr)?.zoom ?? currentZoom;
      getOfficeState().cameraFollowId = null;
      editor.panRef.current = panToWorldPoint(
        worldX,
        worldY,
        zoom,
        layout.cols,
        layout.rows,
        size.w,
        size.h,
      );
      userNavigatedRef.current = true;
      setWorldViewHeld(false);
      if (zoom !== currentZoom) handleZoomChange(zoom);
      setMapOpen(false);
    },
    [getCameraCanvas, currentZoom, handleZoomChange, editor.panRef],
  );
  const liveLayout = getOfficeState().getLayout();
  const layoutKey = `${liveLayout.cols}x${liveLayout.rows}`;
  const largeWorld = isLargeWorld(liveLayout.cols, liveLayout.rows);
  // DISTRICTS come from the layout's own areas (data): recomputed only when the layout changes
  const districts = useMemo(
    () => (largeWorld ? districtsOf(liveLayout) : []),
    [liveLayout, largeWorld],
  );
  const [districtStatus, setDistrictStatus] = useState<string | null>(null);
  const goToDistrict = useCallback(
    (label: string) => {
      const d = districts.find((x) => x.label === label);
      const size = getCameraCanvas();
      if (!d || !size) return;
      const layout = getOfficeState().getLayout();
      const framing = frameDistrict(layout, d, size.w, size.h, window.devicePixelRatio || 1);
      if (!framing) return;
      // a framing: the pan is written FOR its zoom, then the zoom (never reprojected twice)
      getOfficeState().cameraFollowId = null;
      editor.panRef.current = { x: framing.panX, y: framing.panY };
      userNavigatedRef.current = true;
      setWorldViewHeld(false);
      if (framing.zoom !== currentZoom) handleZoomChange(framing.zoom);
      setMapOpen(false);
      // the jump is INSTANT CAMERA TRAVEL, never a ride: say so, distinctly from the subway
      setDistrictStatus(
        framing.centred
          ? `Camera jump to ${label} (not a ride)`
          : `Camera jump to ${label} (not a ride; at the world's edge, not centred)`,
      );
    },
    [districts, getCameraCanvas, currentZoom, handleZoomChange, editor.panRef],
  );
  const [diViewportTick, setDiViewportTick] = useState(0);
  useEffect(() => {
    const el = containerRef.current;
    if (!isDiMode || !el) return;
    let timer: ReturnType<typeof setTimeout> | null = null;
    const measure = () => {
      const rect = el.getBoundingClientRect();
      const dpr = window.devicePixelRatio || 1;
      setDiViewport(Math.round(rect.width * dpr), Math.round(rect.height * dpr));
      if (timer) clearTimeout(timer);
      timer = setTimeout(() => {
        reflowDiWorld();
        setDiViewportTick((n) => n + 1);
      }, DI_WORLD_REFLOW_MS);
    };
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(el);
    return () => {
      observer.disconnect();
      if (timer) clearTimeout(timer);
    };
    // layoutReady: the container mounts only once the layout is ready (before that the page
    // shows the unready notice), so the observer must (re)attach then — otherwise the main
    // area is never measured and the world's shape never follows it.
  }, [isDiMode, layoutReady, setDiViewport, reflowDiWorld]);
  useEffect(() => {
    const el = containerRef.current;
    if (!isDiMode || !layoutReady || !el || userZoomedRef.current) return;
    const rect = el.getBoundingClientRect();
    const dpr = window.devicePixelRatio || 1;
    const layout = getOfficeState().getLayout();
    if (isLargeWorld(layout.cols, layout.rows)) return; // framed by a named view instead
    const fit = diFitZoom(
      Math.round(rect.width * dpr),
      Math.round(rect.height * dpr),
      layout.cols,
      layout.rows,
    );
    if (fit !== currentZoom) handleZoomChange(fit);
  }, [
    isDiMode,
    layoutReady,
    diWorldTick,
    diViewportTick,
    currentZoom,
    handleZoomChange,
    layoutKey,
  ]);
  // A different world (import, reload into another layout) starts from its default framing; an
  // editor resize of the same world does not move the camera under the editing hand.
  const prevLayoutKeyRef = useRef(layoutKey);
  useEffect(() => {
    if (prevLayoutKeyRef.current === layoutKey) return;
    prevLayoutKeyRef.current = layoutKey;
    if (editor.isEditMode) return;
    editor.panRef.current = { x: 0, y: 0 };
    userNavigatedRef.current = false;
    userZoomedRef.current = false;
    setWorldViewHeld(true);
    setWorldView('office');
  }, [layoutKey, editor.isEditMode, editor.panRef]);
  // LARGE WORLD framing: the held named view, at a usable zoom, re-applied on resize / reload.
  useEffect(() => {
    const el = containerRef.current;
    if (!isDiMode || !layoutReady || !el || userNavigatedRef.current) return;
    const layout = getOfficeState().getLayout();
    if (!isLargeWorld(layout.cols, layout.rows)) return;
    const rect = el.getBoundingClientRect();
    const dpr = window.devicePixelRatio || 1;
    const backingW = Math.round(rect.width * dpr);
    const backingH = Math.round(rect.height * dpr);
    const framing =
      frameWorldView(worldView, layout, footprintOf, backingW, backingH, dpr) ??
      frameWorldView('office', layout, footprintOf, backingW, backingH, dpr);
    if (!framing) return;
    getOfficeState().cameraFollowId = null;
    editor.panRef.current = { x: framing.panX, y: framing.panY };
    if (framing.zoom !== currentZoom) handleZoomChange(framing.zoom);
  }, [
    isDiMode,
    layoutReady,
    diWorldTick,
    diViewportTick,
    worldView,
    worldViewTick,
    currentZoom,
    handleZoomChange,
    editor.panRef,
    layoutKey,
  ]);
  // Arrow keys pan a large world (presentation only). Never in the layout editor, never while
  // a form control has focus, never with a modifier (the editor's own shortcuts stay theirs).
  useEffect(() => {
    if (!isDiMode || !largeWorld || editor.isEditMode) return;
    const onKey = (e: KeyboardEvent) => {
      const dir: Record<string, [number, number]> = {
        ArrowLeft: [1, 0],
        ArrowRight: [-1, 0],
        ArrowUp: [0, 1],
        ArrowDown: [0, -1],
      };
      const step = dir[e.key];
      if (!step || e.ctrlKey || e.metaKey || e.altKey || e.shiftKey) return;
      const target = e.target as HTMLElement | null;
      if (target && (target.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(target.tagName)))
        return;
      const el = containerRef.current;
      if (!el) return;
      e.preventDefault();
      const rect = el.getBoundingClientRect();
      const dpr = window.devicePixelRatio || 1;
      const backingW = Math.round(rect.width * dpr);
      const backingH = Math.round(rect.height * dpr);
      const layout = getOfficeState().getLayout();
      const pan = editor.panRef.current;
      getOfficeState().cameraFollowId = null;
      editor.panRef.current = clampPanToWorld(
        pan.x + step[0] * backingW * WORLD_KEY_PAN_FRACTION,
        pan.y + step[1] * backingH * WORLD_KEY_PAN_FRACTION,
        currentZoom,
        layout.cols,
        layout.rows,
        backingW,
        backingH,
      );
      handleUserPan();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [isDiMode, largeWorld, editor.isEditMode, editor.panRef, currentZoom, handleUserPan]);
  // A layout the user edits is theirs: the DI world never reshapes it again.
  useEffect(() => {
    if (isDiMode && editor.isDirty) markDiWorldCustom();
  }, [isDiMode, editor.isDirty, markDiWorldCustom]);

  const isEditingAreas = editor.isEditMode && editorState.activeTool === EditTool.AREA_PAINT;
  const effectiveShowAreas = isEditingAreas || showAreas;
  const activeAreaLabel = isEditingAreas ? editor.selectedAreaLabel : null;

  // e2e: register the component-scoped editor-action drivers + the effective
  // show-areas gate on the test-hooks namespace (module-load installTestHooks
  // can't reach these React callbacks). Bypasses only canvas pixel→tile
  // geometry — the handlers still own undo/dirty/rebuild. Guarded on isE2E.
  useEffect(() => {
    if (!isE2E || typeof window === 'undefined') return;
    const hooks = (window.__pixelAgentsTestHooks ??= {});
    hooks.editorTileAction = (col, row) => editor.handleEditorTileAction(col, row);
    hooks.getView = () => {
      const layout = getOfficeState().getLayout();
      return {
        zoom: currentZoom,
        panX: editor.panRef.current.x,
        panY: editor.panRef.current.y,
        cols: layout.cols,
        rows: layout.rows,
      };
    };
    hooks.editorEraseAction = (col, row) => editor.handleEditorEraseAction(col, row);
    hooks.getShowAreas = () => effectiveShowAreas;
    // the Map's own point jump (legible office zoom), for captures centred on a world point
    hooks.jumpToWorldPoint = (x, y) => jumpToWorldPoint(x, y);
  }, [
    jumpToWorldPoint,
    editor.handleEditorTileAction,
    editor.handleEditorEraseAction,
    effectiveShowAreas,
    currentZoom,
    editor.panRef,
  ]);

  const containerRef = useRef<HTMLDivElement>(null);

  const [editorTickForKeyboard, setEditorTickForKeyboard] = useState(0);
  useEditorKeyboard(
    editor.isEditMode,
    editorState,
    editor.handleDeleteSelected,
    editor.handleRotateSelected,
    editor.handleToggleState,
    editor.handleUndo,
    editor.handleRedo,
    useCallback(() => setEditorTickForKeyboard((n) => n + 1), []),
    editor.handleToggleEditMode,
  );

  const handleCloseAgent = useCallback((id: number) => {
    transport.send({ type: 'closeAgent', id });
  }, []);

  const handleClick = useCallback((agentId: number) => {
    // If clicked agent is a sub-agent, focus the parent's terminal instead
    const os = getOfficeState();
    const meta = os.subagentMeta.get(agentId);
    const focusId = meta ? meta.parentAgentId : agentId;
    transport.send({ type: 'focusAgent', id: focusId });
  }, []);

  const officeState = getOfficeState();

  // Merged set of folders the Areas dropdown can map: real workspace folders plus
  // every distinct folder an agent has run in this session (deduped by name; name
  // is the areaMappings key / seat-bias identity, path is only the React list key).
  const areaFolders = useMemo(() => {
    const byName = new Map<string, { name: string; path: string }>();
    for (const f of workspaceFolders) byName.set(f.name, f);
    for (const name of agentFolderNames) {
      if (!byName.has(name)) byName.set(name, { name, path: name });
    }
    return [...byName.values()];
  }, [workspaceFolders, agentFolderNames]);

  // Areas authoring is available when the layout already defines areas, or when
  // there is at least one mappable folder. Decouples the Areas UI from VS Code
  // multi-root workspaces (fixes single-root VS Code AND standalone, where
  // workspaceFolders is always empty).
  const areasAvailable = (officeState.getLayout().areas?.length ?? 0) > 0 || areaFolders.length > 0;

  const handleExportLayout = useCallback(() => {
    exportLayoutToFile(getOfficeState().getLayout());
  }, []);

  // DI office: the ambient snapshot (residents with their pending legs, trains with their progress
  // and onboard riders) is its OWN single-download action, never a second download behind another.
  const handleExportAmbient = useCallback(() => {
    const snapshot = getOfficeState().serializeAmbient(Date.now());
    if (snapshot) exportAmbientToFile(snapshot);
    else window.alert('No ambient snapshot to export: the residents are not running in this view.');
  }, []);

  /** The ambient import, from TEXT (the paste import's route): bounded before parse
   *  (ambientIntake.ts), then the one validator and apply path; when applied, a save is SENT like any
   *  save (queued while disconnected; no write acknowledgment); refused whole with the reason
   *  otherwise. Never touches the layout. */
  const importAmbientText = useCallback((text: string): PasteImportResult => {
    const intake = prepareAmbientText(text);
    if ('refused' in intake) return { ok: false, reason: intake.refused };
    const os = getOfficeState();
    const result = os.importAmbientSnapshot(intake.snapshot);
    if (result.outcome !== 'applied') return { ok: false, reason: result.reason ?? result.outcome };
    const state = os.serializeAmbient(Date.now());
    if (state)
      transport.send({
        type: 'saveAmbientState',
        state: state as unknown as Record<string, unknown>,
      });
    return {
      ok: true,
      message: `${result.residentsRestored} residents restored (${result.aboardRestored} aboard a train, ${result.waitingRestored} waiting), ${result.trainsRestored} trains; save sent (queued if disconnected), not confirmed written`,
    };
  }, []);

  /** The layout import, from TEXT (one route for pasted text and a file's text): the existing bounded
   *  intake and atomic apply (layoutIntake.ts applyImport). A refused import changes nothing. */
  const importLayoutText = useCallback(
    (text: string): PasteImportResult => {
      let refused: string | null = null;
      let applied: OfficeLayout | null = null;
      applyImport(text, {
        rebuild: (layout) => {
          // a layout REPLACED: no palette of the previous one is kept (renderCaches.ts)
          resetRenderCaches();
          getOfficeState().rebuildFromLayout(layout);
          applied = layout;
        },
        setLastSaved: (layout) => editor.setLastSavedLayout(layout),
        send: (layout) =>
          transport.send({
            type: 'saveLayout',
            layout: layout as unknown as Record<string, unknown>,
          }),
        markClean: () => editor.markClean(),
        refuse: (reason) => {
          refused = reason;
        },
      });
      const done = applied as OfficeLayout | null;
      // applied LOCALLY; the save is sent (queued while disconnected) and no write is acknowledged
      return done
        ? {
            ok: true,
            message: `layout ${done.cols} x ${done.rows} applied; save sent (queued if disconnected), not confirmed written`,
          }
        : { ok: false, reason: refused ?? 'refused' };
    },
    [editor],
  );

  /** DI office (browser): Import Layout / Import Ambient Snapshot open the ONE paste surface. */
  const [pasteKind, setPasteKind] = useState<'layout' | 'ambient' | null>(null);
  const handlePasteImport = useCallback(
    (kind: 'layout' | 'ambient', text: string): PasteImportResult => {
      if (kind === 'ambient') return importAmbientText(text);
      if (
        isEditDirty() &&
        !window.confirm('Replace the current layout? Unsaved edits will be lost.')
      )
        return { ok: false, reason: 'cancelled: unsaved edits kept' };
      return importLayoutText(text);
    },
    [importAmbientText, importLayoutText, isEditDirty],
  );

  const handleImportLayout = useCallback(
    (file: File) => {
      // Browser-native import (standalone): bounded intake (layoutIntake.ts), then apply
      // directly, bypassing the layoutLoaded message whose dirty guard would skip it.
      if (
        isEditDirty() &&
        !window.confirm('Replace the current layout? Unsaved edits will be lost.')
      ) {
        return;
      }
      // Byte size BEFORE the file is read.
      if (importFileTooLarge(file.size)) {
        window.alert('Layout file is too large.');
        return;
      }
      const reader = new FileReader();
      reader.onload = () => {
        // A refused import changes nothing: not rendered, not sent, not marked clean.
        const result = importLayoutText(String(reader.result));
        if (!result.ok) window.alert(`Invalid layout file (${result.reason}).`);
      };
      reader.readAsText(file);
    },
    [isEditDirty, importLayoutText],
  );

  // Force dependency on editorTickForKeyboard to propagate keyboard-triggered re-renders
  void editorTickForKeyboard;

  // Show "Press R to rotate" hint when a rotatable item is selected or being placed
  const showRotateHint =
    editor.isEditMode &&
    (() => {
      if (editorState.selectedFurnitureUid) {
        const item = officeState
          .getLayout()
          .furniture.find((f) => f.uid === editorState.selectedFurnitureUid);
        if (item && isRotatable(item.type)) return true;
      }
      if (
        editorState.activeTool === EditTool.FURNITURE_PLACE &&
        isRotatable(editorState.selectedFurnitureType)
      ) {
        return true;
      }
      return false;
    })();

  if (!layoutReady) {
    // DI mode: observable office data can arrive before the canvas has its layout; say so
    // rather than look empty (the office requests its bootstrap if the layout was lost).
    return (
      <div className="w-full h-full flex items-center justify-center ">
        {diOfficeState ? (
          <span data-testid="di-canvas-unready">
            DI office data received (
            {diOfficeState.live ? 'LIVE' : `NOT LIVE: ${diOfficeState.notLiveReason ?? ''}`}) —
            office canvas NOT READY, awaiting layout
          </span>
        ) : (
          'Loading...'
        )}
      </div>
    );
  }

  return (
    <div
      ref={containerRef}
      // overflow CLIP, not hidden: a hidden box is still programmatically scrollable (focus,
      // scrollIntoView), and scrolling it lifted the office canvas off the bottom of the view
      className="h-full relative overflow-clip"
      // DI mode: the office yields the docked panel's width, so it re-centres beside it
      // and no character can sit under the panel.
      style={{
        width: diOfficeState && !diPanelCollapsed ? `calc(100% - ${DI_PANEL_WIDTH_PX}px)` : '100%',
      }}
    >
      <OfficeCanvas
        officeState={officeState}
        onClick={handleClick}
        isEditMode={editor.isEditMode}
        editorState={editorState}
        onEditorTileAction={editor.handleEditorTileAction}
        onEditorEraseAction={editor.handleEditorEraseAction}
        onEditorSelectionChange={editor.handleEditorSelectionChange}
        onDeleteSelected={editor.handleDeleteSelected}
        onRotateSelected={editor.handleRotateSelected}
        onDragMove={editor.handleDragMove}
        editorTick={editor.editorTick}
        zoom={editor.zoom}
        onZoomChange={handleUserZoom}
        panRef={editor.panRef}
        showAreas={effectiveShowAreas}
        activeAreaLabel={activeAreaLabel}
        centerWhenWorldFits={isDiMode}
        onUserPan={handleUserPan}
        renderPaused={isDiMode && largeWorld && mapOpen && !editor.isEditMode}
      />

      {!isDebugMode ? (
        <>
          <ZoomControls zoom={editor.zoom} onZoomChange={handleUserZoom} />
          {isDiMode && largeWorld && (
            <WorldViewControls
              views={WORLD_VIEW_KINDS.filter(
                (v) =>
                  v !== 'deck' || frameWorldView('deck', liveLayout, footprintOf, 1, 1, 1) !== null,
              )}
              active={worldViewHeld && !mapOpen ? worldView : null}
              onSelect={handleSelectWorldView}
              mapOpen={mapOpen}
              mapAvailable={!editor.isEditMode}
              onToggleMap={toggleMap}
              districts={districts.map((d) => d.label)}
              onSelectDistrict={goToDistrict}
              districtStatus={districtStatus}
            />
          )}
          {isDiMode && largeWorld && mapOpen && !editor.isEditMode && (
            <WorldMap
              officeState={officeState}
              zoom={editor.zoom}
              panRef={editor.panRef}
              getCameraCanvas={getCameraCanvas}
              onJump={jumpToWorldPoint}
              onClose={closeMap}
            />
          )}

          {/* Vignette overlay */}
          <div
            className="absolute inset-0 pointer-events-none"
            style={{ background: 'var(--vignette)' }}
          />

          {editor.isEditMode && editor.isDirty && (
            <EditActionBar editor={editor} editorState={editorState} />
          )}

          {showRotateHint && (
            <div
              className="absolute left-1/2 -translate-x-1/2 z-11 bg-accent-bright text-white text-sm py-3 px-8 rounded-none border-2 border-accent shadow-pixel pointer-events-none whitespace-nowrap"
              style={{ top: editor.isDirty ? 64 : 8 }}
            >
              Rotate (R)
            </div>
          )}

          {editor.isEditMode &&
            (() => {
              const selUid = editorState.selectedFurnitureUid;
              const selColor = selUid
                ? (officeState.getLayout().furniture.find((f) => f.uid === selUid)?.color ?? null)
                : null;
              return (
                <EditorToolbar
                  activeTool={editorState.activeTool}
                  selectedTileType={editorState.selectedTileType}
                  selectedFurnitureType={editorState.selectedFurnitureType}
                  selectedFurnitureUid={selUid}
                  selectedFurnitureColor={selColor}
                  floorColor={editorState.floorColor}
                  wallColor={editorState.wallColor}
                  selectedWallSet={editorState.selectedWallSet}
                  onToolChange={editor.handleToolChange}
                  onTileTypeChange={editor.handleTileTypeChange}
                  onFloorColorChange={editor.handleFloorColorChange}
                  onWallColorChange={editor.handleWallColorChange}
                  onWallSetChange={editor.handleWallSetChange}
                  onSelectedFurnitureColorChange={editor.handleSelectedFurnitureColorChange}
                  pickedFurnitureColor={editorState.pickedFurnitureColor}
                  onPickedFurnitureColorChange={editor.handlePickedFurnitureColorChange}
                  onFurnitureTypeChange={editor.handleFurnitureTypeChange}
                  loadedAssets={loadedAssets}
                  activePetTypes={officeState.getActivePetTypes()}
                  petCount={getPetCount()}
                  onPetToggle={editor.handlePetToggle}
                  carpetVariant={editor.carpetVariant}
                  carpetColor={editor.carpetColor}
                  carpetAccentColor={editor.carpetAccentColor}
                  onCarpetVariantChange={editor.handleCarpetVariantChange}
                  onCarpetColorChange={editor.handleCarpetColorChange}
                  onCarpetAccentColorChange={editor.handleCarpetAccentColorChange}
                  areas={officeState.getLayout().areas ?? []}
                  selectedAreaLabel={editor.selectedAreaLabel}
                  workspaceFolders={areaFolders}
                  areasAvailable={areasAvailable}
                  areaMappings={areaMappings}
                  onSelectArea={editor.handleSelectArea}
                  onAddArea={editor.handleAddArea}
                  onRemoveArea={editor.handleRemoveArea}
                  onRenameArea={editor.handleRenameArea}
                  onAreaColorChange={editor.handleAreaColorChange}
                  onAreaMappingChange={handleAreaMappingChange}
                />
              );
            })()}

          <ToolOverlay
            officeState={officeState}
            agents={agents}
            agentTools={agentTools}
            subagentTools={subagentTools}
            subagentCharacters={subagentCharacters}
            containerRef={containerRef}
            zoom={editor.zoom}
            panRef={editor.panRef}
            onCloseAgent={handleCloseAgent}
            alwaysShowOverlay={alwaysShowOverlay}
            // DI mode: every character gets a DI label (unconfirmed when no office state lists
            // it) and no Pixel controls; undefined = ordinary Pixel.
            diLabelFor={
              diOfficeState
                ? (id) =>
                    `${diOfficeState.fixtureMode ? 'FIXTURE · ' : ''}${diCharacterLabel(diOfficeState, id)}`
                : undefined
            }
            diTitleFor={diOfficeState ? (id) => diCharacterTitle(diOfficeState, id) : undefined}
          />
          {diOfficeState?.fixtureMode && (
            // EXPLICIT fixture preview only: fixed, above every overlay (Map included), never
            // inside the panel, so it cannot scroll away or be hidden with it.
            <div
              className="fixed top-4 left-1/2 -translate-x-1/2 z-50 pixel-panel px-12 py-4 text-sm text-warning pointer-events-none select-none"
              role="status"
              data-testid="di-fixture-banner"
            >
              {DI_FIXTURE_BANNER}
            </div>
          )}
          {diOfficeState && <AmbientLegend officeState={officeState} />}
          {diOfficeState && (
            <DiOfficePanel
              state={diOfficeState}
              officeState={officeState}
              collapsed={diPanelCollapsed}
              onCollapsedChange={setDiPanelCollapsed}
              announced={diAnnounced}
              evidence={diEvidence}
              review={reviewAccess}
            />
          )}
          <DiCodeReview
            session={review.session}
            onClose={review.close}
            onRefresh={review.refresh}
            onSelect={review.select}
            returnFocusTo={reviewOpener}
          />
        </>
      ) : (
        <DebugView
          agents={agents}
          selectedAgent={selectedAgent}
          agentTools={agentTools}
          agentStatuses={agentStatuses}
          subagentTools={subagentTools}
          officeState={officeState}
          onSelectAgent={handleSelectAgent}
        />
      )}

      {/* Hooks first-run tooltip. Gated on hooksInstalled (the hooksStatus
          message), NOT the hooksEnabled preference: hooksEnabled defaults true
          while first-run consent is still pending, and announcing "Instant
          Detection Active" before anything is installed would be a lie. */}
      {hooksEnabled && claudeHooksInstalled && !hooksInfoShown && !hooksTooltipDismissed && (
        <Tooltip
          title="Instant Detection Active"
          position="top-right"
          onDismiss={() => {
            setHooksTooltipDismissed(true);
            transport.send({ type: 'setHooksInfoShown' });
          }}
        >
          <span className="text-sm text-text leading-none">
            Your agents now respond in real-time.{' '}
            <span
              className="text-accent cursor-pointer underline"
              onClick={() => {
                setIsHooksInfoOpen(true);
                setHooksTooltipDismissed(true);
                transport.send({ type: 'setHooksInfoShown' });
              }}
            >
              View more
            </span>
          </span>
        </Tooltip>
      )}

      {/* Hooks info modal */}
      <Modal
        isOpen={isHooksInfoOpen}
        onClose={() => setIsHooksInfoOpen(false)}
        title="Instant Detection is ON"
        zIndex={52}
      >
        <div className="text-base text-text px-10" style={{ lineHeight: 1.4 }}>
          <p className="mb-8">Your Pixel Agents office now reacts in real-time:</p>
          <ul className="mb-8 pl-18 list-disc m-0">
            <li className="text-sm mb-2">Permission prompts appear instantly</li>
            <li className="text-sm mb-2">Turn completions detected the moment they happen</li>
            <li className="text-sm mb-2">Sound notifications play immediately</li>
          </ul>
          <p className="mb-12 text-text-muted">
            This works through Claude Code Hooks, small event listeners that notify Pixel Agents
            whenever something happens in your Claude sessions.
          </p>
          <div className="text-center">
            <button
              onClick={() => setIsHooksInfoOpen(false)}
              className="py-4 px-20 text-lg bg-accent text-white border-2 border-accent rounded-none cursor-pointer shadow-pixel"
            >
              Got it
            </button>
          </div>
          <p className="mt-8 text-xs text-text-muted text-center">
            To disable, go to Settings {'>'} Instant Detection
          </p>
        </div>
      </Modal>

      <BottomToolbar
        isEditMode={editor.isEditMode}
        onOpenClaude={editor.handleOpenClaude}
        onToggleEditMode={editor.handleToggleEditMode}
        isSettingsOpen={isSettingsOpen}
        onToggleSettings={() => setIsSettingsOpen((v) => !v)}
        workspaceFolders={workspaceFolders}
      />

      <VersionIndicator
        currentVersion={extensionVersion}
        lastSeenVersion={lastSeenVersion}
        onDismiss={handleWhatsNewDismiss}
        onOpenChangelog={handleOpenChangelog}
      />

      <ConnectionIndicator reducedMotion={officeState.reducedMotion} />

      <ChangelogModal
        isOpen={isChangelogOpen}
        onClose={() => setIsChangelogOpen(false)}
        currentVersion={extensionVersion}
      />

      <SettingsModal
        isOpen={isSettingsOpen}
        onClose={() => setIsSettingsOpen(false)}
        isDebugMode={isDebugMode}
        onToggleDebugMode={handleToggleDebugMode}
        alwaysShowOverlay={alwaysShowOverlay}
        onToggleAlwaysShowOverlay={handleToggleAlwaysShowOverlay}
        ghostHeadlessAgents={ghostHeadlessAgents}
        onToggleGhostHeadlessAgents={handleToggleGhostHeadlessAgents}
        externalAssetDirectories={externalAssetDirectories}
        watchAllSessions={watchAllSessions}
        onToggleWatchAllSessions={() => {
          const newVal = !watchAllSessions;
          setWatchAllSessions(newVal);
          transport.send({ type: 'setWatchAllSessions', enabled: newVal });
        }}
        hooksInstalled={claudeHooksInstalled}
        onToggleHooksEnabled={() => {
          // Toggle the DISPLAYED state (actual install), not the preference: when the two disagree — preference on,
          // nothing installed while consent is pending — toggling the preference would turn hooks OFF for a user
          // asking for ON. No optimistic local update either; both backends answer with the truthful hooksStatus this
          // checkbox renders, so it lands correct instead of flickering when an install fails. The providerId is
          // ECHOED from that row (never originated here), so nothing sends until the row has arrived.
          const [rowProviderId] =
            Object.entries(hooksInstalled).find(([id]) => id === 'claude') ?? [];
          if (rowProviderId !== undefined) {
            transport.send({
              type: 'setHooksEnabled',
              providerId: rowProviderId,
              enabled: !claudeHooksInstalled,
            });
          }
        }}
        showAreas={showAreas}
        onToggleShowAreas={onToggleShowAreas}
        showAreasAvailable={areasAvailable}
        onExportLayout={handleExportLayout}
        onImportLayout={handleImportLayout}
        onExportAmbient={handleExportAmbient}
        onPasteImport={setPasteKind}
        diMode={diOfficeState !== null}
      />

      <PasteImportModal
        kind={pasteKind}
        maxBytes={pasteKind === 'ambient' ? AMBIENT_SAVE_MAX_BYTES : LAYOUT_MAX_FILE_BYTES}
        onImport={handlePasteImport}
        onClose={() => setPasteKind(null)}
      />

      {showMigrationNotice && (
        <MigrationNotice onDismiss={() => setMigrationNoticeDismissed(true)} />
      )}

      {intro && (
        <IntroBubble
          officeState={officeState}
          headline={intro.headline}
          disclosure={intro.disclosure}
          containerRef={containerRef}
          zoom={editor.zoom}
          panRef={editor.panRef}
          installFailed={installFailed}
          installPending={installPending}
          onChoice={handleConsentChoice}
          onClose={handleIntroClose}
          escapeSuppressed={
            isSettingsOpen ||
            isChangelogOpen ||
            isHooksInfoOpen ||
            showMigrationNotice ||
            editor.isEditMode
          }
        />
      )}
    </div>
  );
}

export default App;
