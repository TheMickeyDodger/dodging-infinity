import { useCallback, useEffect, useMemo, useRef, useState } from 'react';

import type { DiOfficeState, HooksConsentRequest } from '../../../core/src/messages.js';
import {
  clientInvalidationReason,
  DI_READY_TRACKER_AT_MOUNT,
  diAnimationPlan,
  type DiConnection,
  diZoneLabel,
  nextDiConnection,
  readyTrackerAfterTransport,
  reconcileDiCharacters,
  shouldRequestDiBootstrap,
  watchTransportState,
  withClientInvalidation,
} from '../components/diOfficeFormat.js';
import {
  DI_CLIENT_CHECK_MS,
  DI_DEFAULT_LAYOUT_FINGERPRINT,
  DI_SLOTS_PER_BAND,
} from '../constants.js';
import { playDoneSound, playPermissionSound, setSoundEnabled } from '../notificationSound.js';
import type { ExistingAgentMeta, PendingAgent } from '../office/engine/existingAgents.js';
import { reconcileExistingAgents } from '../office/engine/existingAgents.js';
import type { OfficeState } from '../office/engine/officeState.js';
import { setGhostHeadlessAgents as setRendererGhostHeadlessAgents } from '../office/engine/renderer.js';
import { setFloorSprites } from '../office/floorTiles.js';
import {
  aspectBands,
  assignDiZones,
  bandsForSlots,
  buildDiWorld,
  cropToContent,
  type DiZoneAssignment,
  layoutFingerprint,
  maxBands,
  slotSeats,
} from '../office/layout/diWorld.js';
import { buildDynamicCatalog } from '../office/layout/furnitureCatalog.js';
import { layoutToSeats, migrateLayoutColors } from '../office/layout/layoutSerializer.js';
import { setCarpetSprites } from '../office/sprites/carpetTiles.js';
import { setPetTemplates } from '../office/sprites/petSpriteData.js';
import { setCharacterTemplates } from '../office/sprites/spriteData.js';
import {
  extractToolName,
  isSubagentToolName,
  setProviderCapabilities,
} from '../office/toolUtils.js';
import type { OfficeLayout, ToolActivity } from '../office/types.js';
import { setWallSprites } from '../office/wallTiles.js';
import { isBrowserRuntime, isE2E } from '../runtime.js';
import { transport } from '../transport/index.js';

/**
 * A Headless agent is one the office adopted from outside (`claude -p`, a session
 * picked up by Watch All Sessions) and therefore has no terminal to focus. Its
 * character renders translucent so it reads as untouchable at a glance.
 *
 * Standalone is exempt: that adapter has no terminals at all, so every agent
 * would qualify and the cue would distinguish nothing.
 */
const isHeadlessAgent = (isExternal: boolean | undefined): boolean =>
  isExternal === true && !isBrowserRuntime;

export interface SubagentCharacter {
  id: number;
  parentAgentId: number;
  parentToolId: string;
  label: string;
}

interface FurnitureAsset {
  id: string;
  name: string;
  label: string;
  category: string;
  file: string;
  width: number;
  height: number;
  footprintW: number;
  footprintH: number;
  isDesk: boolean;
  canPlaceOnWalls: boolean;
  groupId?: string;
  canPlaceOnSurfaces?: boolean;
  backgroundTiles?: number;
  orientation?: string;
  state?: string;
  mirrorSide?: boolean;
  rotationScheme?: string;
  animationGroup?: string;
  frame?: number;
}

export interface WorkspaceFolder {
  name: string;
  path: string;
}

interface ExtensionMessageState {
  /** DI office only: the server-projected office state (null on every other surface). */
  diOfficeState: DiOfficeState | null;
  agents: number[];
  selectedAgent: number | null;
  agentTools: Record<number, ToolActivity[]>;
  agentStatuses: Record<number, string>;
  subagentTools: Record<number, Record<string, ToolActivity[]>>;
  subagentCharacters: SubagentCharacter[];
  layoutReady: boolean;
  layoutWasReset: boolean;
  loadedAssets?: { catalog: FurnitureAsset[]; sprites: Record<string, string[][]> };
  workspaceFolders: WorkspaceFolder[];
  /** Distinct folderNames seen across agents this session — source for the Areas folder dropdown. */
  agentFolderNames: string[];
  externalAssetDirectories: string[];
  lastSeenVersion: string;
  extensionVersion: string;
  watchAllSessions: boolean;
  setWatchAllSessions: (v: boolean) => void;
  alwaysShowLabels: boolean;
  ghostHeadlessAgents: boolean;
  setGhostHeadlessAgents: (v: boolean) => void;
  hooksEnabled: boolean;
  setHooksEnabled: (v: boolean) => void;
  /** Actual install state per provider (hooksStatus messages) — absent/false
   *  while first-run consent is pending, unlike hooksEnabled which defaults
   *  true. Keyed by providerId; today's Settings checkbox reads 'claude'. */
  hooksInstalled: Record<string, boolean>;
  /** Bumped per provider on every hooksStatus message. `hooksInstalled` alone cannot say "the server answered": a
   *  failed install re-reports the `false` already held, so no effect runs. The Intro needs the ARRIVAL to tell a
   *  pending install from a failed one, per provider — A's status is never a verdict on B's install. */
  hooksStatusSeq: Record<string, number>;
  hooksInfoShown: boolean;
  /** First-run consent ask (hooksConsentRequest). Non-null while the server waits on an answer; carries the provider
   *  and the server's exact disclosure copy, so the consent step renders the terms being approved with no client
   *  duplicate to drift. Cleared on answer/dismissal, and by a matching provider's hooksStatus installed=true. */
  consentRequest: HooksConsentRequest | null;
  dismissConsentRequest: (providerId: string | null) => void;
  // Areas
  areaMappings: Record<string, string[]>;
  setAreaMappings: (m: Record<string, string[]>) => void;
  showAreas: boolean;
  setShowAreas: (v: boolean) => void;
  // DI responsive world
  /** The main area's size in DEVICE pixels (what the world's shape is fitted to). */
  setDiViewport: (width: number, height: number) => void;
  /** Re-fit the DI default world to the current main area (no-op for a custom layout). */
  reflowDiWorld: () => void;
  /** The user is authoring the layout: never reshape it again this session. */
  markDiWorldCustom: () => void;
  /** Bumped whenever the DI world (and so the grid size) changes — re-fit the zoom on it. */
  diWorldTick: number;
  /** DI: characters the server ANNOUNCED (agentCreated) that no office snapshot has confirmed
   *  yet — never drawn, reported in the inspector. Cleared by every snapshot. */
  diAnnounced: Array<{ id: number; missionRef: string | null }>;
}

function saveAgentSeats(os: OfficeState): void {
  transport.send({ type: 'saveAgentSeats', seats: os.getPersistableSeats() });
}

export function useExtensionMessages(
  getOfficeState: () => OfficeState,
  onLayoutLoaded?: (layout: OfficeLayout) => void,
  isEditDirty?: () => boolean,
): ExtensionMessageState {
  const [agents, setAgents] = useState<number[]>([]);
  /** DI only: the last office state as RECEIVED from the server. */
  const [diServerState, setDiServerState] = useState<DiOfficeState | null>(null);
  /** DI only: transport state + whether a NEW office message arrived since it connected. */
  const [diConnection, setDiConnection] = useState<DiConnection>({
    transport: transport.state,
    freshSinceConnect: false,
  });
  const [diClientReason, setDiClientReason] = useState<string | null>(null);
  /** DI only: characterId -> canonical identity of every DI character drawn (FINDING 2). */
  const diDrawnRef = useRef(new Map<number, string>());
  /** DI only: set by the FIRST office message (the DI server sends one first on every connection). */
  const diModeRef = useRef(false);
  /** DI only: has the CURRENT connection carried a webviewReady? (mount-time send covers the first). */
  const diReadyRef = useRef(DI_READY_TRACKER_AT_MOUNT);
  /** DI only: the responsive world, active ONLY while the layout is the bundled default. */
  const diWorldRef = useRef<{
    room: OfficeLayout;
    roomSeats: number;
    assignment: DiZoneAssignment;
    key: string;
  } | null>(null);
  /** DI only: the user authored the layout this session — it is never reshaped again. */
  const diWorldCustomRef = useRef(false);
  /** DI only: the main area in device pixels (set by the App's resize observer). */
  const diViewportRef = useRef<{ width: number; height: number } | null>(null);
  const applyDiWorldRef = useRef<((os: OfficeState, state: DiOfficeState) => void) | null>(null);
  const [diWorldTick, setDiWorldTick] = useState(0);
  /** DI only: server-announced, snapshot-unconfirmed characters (inspector only, never drawn). */
  const [diAnnounced, setDiAnnounced] = useState<Array<{ id: number; missionRef: string | null }>>(
    [],
  );
  /** DI only: per character id, the server's persisted look and seat (existingAgents) or the
   *  look of a fresh join (agentCreated) — DATA only, applied when a snapshot draws the member,
   *  so confirmed members keep their palette, hue and seat across reloads and reconnects. */
  const diMetaRef = useRef(
    new Map<number, { palette?: number; hueShift?: number; seatId?: string; restored: boolean }>(),
  );
  /** DI only: the last office state as RECEIVED, for the layout-ready flush (a ref: handlers read it). */
  const diStoredRef = useRef<DiOfficeState | null>(null);
  /** DI only: the PRESENTED office state (after client invalidation) — the sole animation authority. */
  const diPresentedRef = useRef<DiOfficeState | null>(null);
  const [selectedAgent, setSelectedAgent] = useState<number | null>(null);
  const [agentTools, setAgentTools] = useState<Record<number, ToolActivity[]>>({});
  const [agentStatuses, setAgentStatuses] = useState<Record<number, string>>({});
  const [subagentTools, setSubagentTools] = useState<
    Record<number, Record<string, ToolActivity[]>>
  >({});
  const [subagentCharacters, setSubagentCharacters] = useState<SubagentCharacter[]>([]);
  const [layoutReady, setLayoutReady] = useState(false);
  const [layoutWasReset, setLayoutWasReset] = useState(false);
  const [loadedAssets, setLoadedAssets] = useState<
    { catalog: FurnitureAsset[]; sprites: Record<string, string[][]> } | undefined
  >();
  const [workspaceFolders, setWorkspaceFolders] = useState<WorkspaceFolder[]>([]);
  const [agentFolderNames, setAgentFolderNames] = useState<string[]>([]);
  const [externalAssetDirectories, setExternalAssetDirectories] = useState<string[]>([]);
  const [lastSeenVersion, setLastSeenVersion] = useState('');
  const [extensionVersion, setExtensionVersion] = useState('');
  const [watchAllSessions, setWatchAllSessions] = useState(false);
  const [alwaysShowLabels, setAlwaysShowLabels] = useState(false);
  const [ghostHeadlessAgents, setGhostHeadlessAgentsState] = useState(false);
  const [hooksEnabled, setHooksEnabled] = useState(true);
  const [hooksInstalled, setHooksInstalled] = useState<Record<string, boolean>>({});
  const [hooksStatusSeq, setHooksStatusSeq] = useState<Record<string, number>>({});
  const [hooksInfoShown, setHooksInfoShown] = useState(true);
  // FIFO of pending consent asks, at most one per provider (a re-ask replaces that provider's entry in place). The
  // HEAD is what the Intro renders; answering, dismissing, or mooting it advances to the next provider's ask rather
  // than dropping it — the server sends one request per provider on the same handshake.
  const [consentQueue, setConsentQueue] = useState<HooksConsentRequest[]>([]);
  const consentRequest = consentQueue[0] ?? null;
  const [areaMappings, setAreaMappings] = useState<Record<string, string[]>>({});
  const [showAreas, setShowAreas] = useState(false);

  // The renderer keeps its own module-level copy (read every rAF frame), so both
  // sources of truth move together — the persisted value on settingsLoaded and
  // the user's click in Settings.
  const applyGhostHeadlessAgents = useCallback((enabled: boolean) => {
    setGhostHeadlessAgentsState(enabled);
    setRendererGhostHeadlessAgents(enabled);
  }, []);

  // Track whether initial layout has been loaded (ref to avoid re-render)
  const layoutReadyRef = useRef(false);

  // Live background spawn tools per agent (runInBackground agentToolStart, or a
  // lazily-created watched sub). Their sub-characters outlive the parent's turn:
  // agentToolsClear must NOT remove them — despawning and re-creating moved the
  // character to a new tile every turn. Cleared by subagentClear/agentClosed.
  const backgroundParentToolIdsRef = useRef<Record<number, Set<string>>>({});

  useEffect(() => {
    // Buffer agents from existingAgents until layout is loaded
    let pendingAgents: PendingAgent[] = [];

    // Accumulate distinct folderNames seen across agents (never removed during the
    // session): the source for the Areas folder-mapping dropdown, so a folder stays
    // editable even after its agents close.
    const noteFolderName = (name?: string) => {
      if (!name) return;
      setAgentFolderNames((prev) => (prev.includes(name) ? prev : [...prev, name]));
    };

    /** Every DI character on the canvas (all of them participate; sub-agents never occur in DI). */
    const diCanvasIds = (os: OfficeState): number[] =>
      [...os.characters.values()].filter((ch) => !ch.isSubagent).map((ch) => ch.id);
    if (isE2E && typeof window !== 'undefined') {
      if (!window.__pixelAgentsTestHooks) window.__pixelAgentsTestHooks = {};
      window.__pixelAgentsTestHooks.getDiRecorded = () => [...diDrawnRef.current.entries()];
      window.__pixelAgentsTestHooks.getDiStored = () => diStoredRef.current;
    }
    /**
     * Reconcile EVERY canvas character (confirmed or not) with an office state on the canonical
     * identity tuple — a reconnect may have missed agentCreated/agentClosed, and a character drawn
     * by agentCreated or by the legacy existingAgents flush may never have been confirmed. Needs
     * the layout (seats). Records the identities the animation authority then decides against.
     */
    /**
     * The DI default world for `state` at the current main-area aspect: sticky Mission zones,
     * enough bands to seat every drawn character (capacity) and to suit the aspect (shape).
     * Returns null when no world is active (a custom layout) or nothing changed.
     */
    const computeDiWorld = (
      state: DiOfficeState,
      force: boolean,
    ): { layout: OfficeLayout; mappings: Record<string, string[]> } | null => {
      const world = diWorldRef.current;
      if (!world || diWorldCustomRef.current) return null;
      const missions = state.missions.missions.map((m) => ({
        missionRef: m.missionRef,
        count: m.members.members.filter((x) => x.characterId !== null).length,
      }));
      const assignment = assignDiZones(
        world.assignment,
        missions,
        world.roomSeats,
        slotSeats(world.room.cols),
        maxBands(world.room.rows) * DI_SLOTS_PER_BAND,
      );
      const viewport = diViewportRef.current;
      const aspect = viewport && viewport.height > 0 ? viewport.width / viewport.height : 0;
      const bands = Math.max(
        aspectBands(world.room.cols, world.room.rows, aspect),
        bandsForSlots(assignment.slots.length),
      );
      const key = JSON.stringify([bands, assignment]);
      world.assignment = assignment;
      if (!force && key === world.key) return null;
      world.key = key;
      const labelOf = (ref: string): string => {
        const mission = state.missions.missions.find((m) => m.missionRef === ref);
        return mission ? diZoneLabel(mission) : ref;
      };
      return buildDiWorld(world.room, bands, assignment, labelOf);
    };
    /** Apply the world to the office (mappings FIRST, so reseating lands in Mission zones). */
    const applyDiWorld = (os: OfficeState, state: DiOfficeState): void => {
      if (isEditDirty?.()) return; // never reshape under an in-progress edit
      const next = computeDiWorld(state, false);
      if (!next) return;
      os.setAreaMappings(next.mappings);
      setAreaMappings(next.mappings);
      os.rebuildFromLayout(next.layout);
      onLayoutLoaded?.(next.layout);
      setDiWorldTick((n) => n + 1);
    };
    applyDiWorldRef.current = applyDiWorld;
    const reconcileDiCanvas = (os: OfficeState, state: DiOfficeState): void => {
      // Capacity and Mission zones first: the world grows before characters need its seats.
      applyDiWorld(os, state);
      const plan = reconcileDiCharacters(diDrawnRef.current, state, diCanvasIds(os));
      for (const id of plan.remove) {
        setAgents((prev) => prev.filter((a) => a !== id));
        setSelectedAgent((prev) => (prev === id ? null : prev));
        os.removeAgent(id);
      }
      for (const { id, folderName } of plan.add) {
        // Restored members keep their persisted look and seat and appear without the spawn
        // effect; a fresh join (agentCreated) keeps its server look and spawns.
        const meta = diMetaRef.current.get(id);
        os.addAgent(id, meta?.palette, meta?.hueShift, meta?.seatId, meta?.restored, folderName);
        if (os.characters.get(id)?.matrixEffect === 'despawn') {
          // The previous character for this number is still despawning (addAgent does
          // not draw over it): leave it unrecorded so the NEXT message draws the new
          // identity — never reuse the old character for it.
          plan.next.delete(id);
          continue;
        }
        setAgents((prev) => (prev.includes(id) ? prev : [...prev, id]));
        noteFolderName(folderName);
      }
      diDrawnRef.current = plan.next;
    };
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const handler = (msg: any) => {
      handleMessage(msg);
      // DI mode: whatever this message did (agentCreated, existingAgents, a layout flush, a
      // status), animation is re-derived from the PRESENTED office state for EVERY canvas
      // character — an unconfirmed incremental creation never animates.
      if (diModeRef.current) {
        const os = getOfficeState();
        for (const [id, animate] of diAnimationPlan(
          diPresentedRef.current,
          diCanvasIds(os),
          diDrawnRef.current,
        ))
          os.setAgentActive(id, animate);
      }
    };
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const handleMessage = (msg: any) => {
      const os = getOfficeState();
      // CI / e2e diagnostic: record every received transport message on the
      // window-side log. The fixture reads window.__pixelAgentsTestHooks.
      // messageLog and attaches as JSON so CI failures can see the exact
      // sequence of messages the webview actually processed. Gated on the e2e
      // harness flag so this unbounded log never grows in a real session.
      if (isE2E && typeof window !== 'undefined') {
        if (!window.__pixelAgentsTestHooks) window.__pixelAgentsTestHooks = {};
        if (!window.__pixelAgentsTestHooks.messageLog) {
          window.__pixelAgentsTestHooks.messageLog = [];
        }
        window.__pixelAgentsTestHooks.messageLog.push({
          at: Date.now(),
          type: msg.type,
          id: msg.id,
          toolName: msg.toolName,
          status: msg.status,
          toolId: msg.toolId,
          parentToolId: msg.parentToolId,
        });
      }

      if (msg.type === 'providerCapabilities') {
        setProviderCapabilities({
          readingTools: msg.readingTools,
          subagentToolNames: msg.subagentToolNames,
        });
        return;
      }

      if (msg.type === 'layoutLoaded') {
        // Skip external layout updates while editor has unsaved changes
        if (layoutReadyRef.current && isEditDirty?.()) {
          console.log('[Webview] Skipping external layout update — editor has unsaved changes');
          return;
        }
        const rawLayout = msg.layout as OfficeLayout | null;
        let layout = rawLayout && rawLayout.version === 1 ? migrateLayoutColors(rawLayout) : null;
        // DI: ONLY the bundled default becomes the responsive world; a custom layout is never
        // reshaped (exact cols, rows and furniture — it gets fit-zoom only).
        if (diModeRef.current && layout) {
          if (
            !diWorldCustomRef.current &&
            layoutFingerprint(layout) === DI_DEFAULT_LAYOUT_FINGERPRINT
          ) {
            const room = cropToContent(layout);
            diWorldRef.current = {
              room,
              roomSeats: layoutToSeats(room.furniture).size,
              assignment: diWorldRef.current?.assignment ?? { room: null, slots: [] },
              key: '',
            };
            const stored = diStoredRef.current;
            const world = stored ? computeDiWorld(stored, true) : null;
            if (world) {
              os.setAreaMappings(world.mappings);
              setAreaMappings(world.mappings);
              layout = world.layout;
              setDiWorldTick((n) => n + 1);
            }
          } else {
            diWorldRef.current = null;
          }
        }
        if (layout) {
          os.rebuildFromLayout(layout);
          onLayoutLoaded?.(layout);
        } else {
          // Default layout — snapshot whatever OfficeState built
          onLayoutLoaded?.(os.getLayout());
        }
        // Add buffered agents now that layout (and seats) are correct
        for (const p of pendingAgents) {
          os.addAgent(p.id, p.palette, p.hueShift, p.seatId, true, p.folderName);
          if (p.isHeadless) os.setHeadless(p.id, true);
        }
        pendingAgents = [];
        layoutReadyRef.current = true;
        setLayoutReady(true);
        // DI: the CANONICAL layout-ready flush. The legacy flush above draws the server's store
        // with no recorded identity; reconcile against the office state ALREADY RECEIVED now, on
        // layout arrival — never wait for the next office message (the animation authority then
        // runs on the recorded identities, after this message).
        if (diModeRef.current && diStoredRef.current) reconcileDiCanvas(os, diStoredRef.current);
        if (msg.wasReset) {
          setLayoutWasReset(true);
        }
        if (os.characters.size > 0) {
          saveAgentSeats(os);
        }
      } else if (msg.type === 'agentCreated' && diModeRef.current) {
        // DI: the canvas holds ONLY snapshot-confirmed members. An announcement is recorded for
        // the inspector (and its look kept for when a snapshot confirms it) — never drawn, never
        // selected. (diModeRef is set by the office snapshot that opens every DI connection.)
        const id = msg.id as number;
        diMetaRef.current.set(id, {
          palette: msg.palette as number | undefined,
          hueShift: msg.hueShift as number | undefined,
          restored: false,
        });
        const missionRef = (msg.folderName as string | undefined) ?? null;
        setDiAnnounced((prev) =>
          prev.some((a) => a.id === id) ? prev : [...prev, { id, missionRef }],
        );
      } else if (msg.type === 'agentCreated') {
        const id = msg.id as number;
        const folderName = msg.folderName as string | undefined;
        const isTeammate = msg.isTeammate as boolean | undefined;
        const teammateName = msg.teammateName as string | undefined;
        const teammateParentId = msg.parentAgentId as number | undefined;
        const teamName = msg.teamName as string | undefined;
        setAgents((prev) => (prev.includes(id) ? prev : [...prev, id]));
        // Don't auto-select teammates (keep focus on lead)
        if (!isTeammate) {
          setSelectedAgent(id);
        }
        if (isTeammate && teammateParentId !== undefined) {
          // Teammate: inherit parent's palette and workspace folderName (teammate runs
          // in the same workspace as the lead). Name shown via agentName (teamRoleLabel).
          // Seat them at the free seat closest to the lead so the team clusters.
          const parentCh = os.characters.get(teammateParentId);
          const palette = parentCh ? parentCh.palette : undefined;
          const hueShift = parentCh ? parentCh.hueShift : undefined;
          os.addAgent(
            id,
            palette,
            hueShift,
            undefined,
            undefined,
            parentCh?.folderName,
            teammateParentId,
          );
          noteFolderName(parentCh?.folderName);
          // Set team metadata on the character
          const ch = os.characters.get(id);
          if (ch) {
            ch.leadAgentId = teammateParentId;
            ch.teamName = teamName ?? parentCh?.teamName;
            ch.agentName = teammateName;
          }
        } else {
          const palette = msg.palette as number | undefined;
          const hueShift = msg.hueShift as number | undefined;
          os.addAgent(id, palette, hueShift, undefined, undefined, folderName);
          noteFolderName(folderName);
          if (isHeadlessAgent(msg.isExternal as boolean | undefined)) {
            os.setHeadless(id, true);
          }
        }
        saveAgentSeats(os);
      } else if (msg.type === 'agentClosed') {
        const id = msg.id as number;
        // DI: snapshot reconciliation is the SOLE membership authority in BOTH directions. A bare
        // agentClosed asserts no absence: it only settles bookkeeping (the announcement, the
        // retained look/seat, tools, sub-agents). A member leaves the canvas when the next office
        // state omits it (the reconciler's !w arm). The Claude path keeps its direct removal.
        const di = diModeRef.current;
        if (di) {
          setDiAnnounced((prev) => prev.filter((a) => a.id !== id));
          diMetaRef.current.delete(id);
        } else {
          setAgents((prev) => prev.filter((a) => a !== id));
          setSelectedAgent((prev) => (prev === id ? null : prev));
        }
        setAgentTools((prev) => {
          if (!(id in prev)) return prev;
          const next = { ...prev };
          delete next[id];
          return next;
        });
        setAgentStatuses((prev) => {
          if (!(id in prev)) return prev;
          const next = { ...prev };
          delete next[id];
          return next;
        });
        setSubagentTools((prev) => {
          if (!(id in prev)) return prev;
          const next = { ...prev };
          delete next[id];
          return next;
        });
        // Remove all sub-agent characters belonging to this agent
        delete backgroundParentToolIdsRef.current[id];
        os.removeAllSubagents(id);
        setSubagentCharacters((prev) => prev.filter((s) => s.parentAgentId !== id));
        if (!di) os.removeAgent(id);
      } else if (msg.type === 'existingAgents' && diModeRef.current) {
        // DI: never drawn from here (only snapshots draw); keep the server-assigned look.
        const meta = (msg.agentMeta || {}) as Record<number, ExistingAgentMeta>;
        for (const id of (msg.agents as number[]) ?? []) {
          diMetaRef.current.set(id, {
            palette: meta[id]?.palette,
            hueShift: meta[id]?.hueShift,
            seatId: meta[id]?.seatId,
            restored: true,
          });
        }
      } else if (msg.type === 'existingAgents') {
        const incoming = msg.agents as number[];
        const meta = (msg.agentMeta || {}) as Record<number, ExistingAgentMeta>;
        const folderNames = (msg.folderNames || {}) as Record<number, string>;
        const externalAgents = (msg.externalAgents || {}) as Record<number, boolean>;
        const headlessAgents: Record<number, boolean> = {};
        for (const id of incoming) {
          noteFolderName(folderNames[id]);
          if (isHeadlessAgent(externalAgents[id])) headlessAgents[id] = true;
        }
        // Order-independent restore: add agents now if the layout (and its seats)
        // is already built, otherwise buffer them for the next layoutLoaded.
        // Depending on layoutLoaded always arriving last stranded restored agents
        // on surfaces that send layout first (issue #334).
        if (
          reconcileExistingAgents(
            os,
            incoming,
            meta,
            folderNames,
            layoutReadyRef.current,
            pendingAgents,
            headlessAgents,
          )
        ) {
          saveAgentSeats(os);
        }
        setAgents((prev) => {
          const ids = new Set(prev);
          const merged = [...prev];
          for (const id of incoming) {
            if (!ids.has(id)) {
              merged.push(id);
            }
          }
          return merged.sort((a, b) => a - b);
        });
      } else if (msg.type === 'agentToolStart') {
        const id = msg.id as number;
        const toolId = msg.toolId as string;
        const status = msg.status as string;
        const permissionActive = msg.permissionActive as boolean | undefined;
        setAgentTools((prev) => {
          const list = prev[id] || [];
          if (list.some((t) => t.toolId === toolId)) return prev;
          return {
            ...prev,
            [id]: [
              ...list,
              { toolId, status, done: false, permissionWait: permissionActive || false },
            ],
          };
        });
        const toolName = (msg.toolName as string | undefined) ?? extractToolName(status);
        os.setAgentTool(id, toolName);
        os.setAgentActive(id, true);
        // Don't clear the permission bubble if the hook already confirmed permission is needed
        if (!permissionActive) {
          os.clearPermissionBubble(id);
        }
        // Create sub-agent character for Task/Agent tool subtasks.
        // agentToolStart for Task/Agent is always emitted via JSONL (with the stable
        // toolu_* id), never from the hook path — handlePreToolUse skips these tools.
        // runInBackground routing:
        //   - parent HAS teamName: teammate path (onTeammateDetected) creates the
        //     teammate; we skip here so we don't spawn a ghost sub-agent alongside.
        //   - parent has NO teamName: no teammate path exists, so we must still
        //     create the Subtask sub-character or the background task is invisible.
        const runInBackground = msg.runInBackground as boolean | undefined;
        if (runInBackground) {
          const set = (backgroundParentToolIdsRef.current[id] ??= new Set());
          set.add(toolId);
        }
        // Named spawns are Teammates-to-be: a teammate character will represent
        // them, so never create the Subtask ghost the teammate would replace.
        const isTeammateSpawn = msg.isTeammateSpawn as boolean | undefined;
        const parentChar = os.characters.get(id);
        const parentHasTeam = !!parentChar?.teamName;
        if (
          isSubagentToolName(toolName) &&
          !isTeammateSpawn &&
          (!runInBackground || !parentHasTeam)
        ) {
          const label = status.startsWith('Subtask:') ? status.slice('Subtask:'.length).trim() : '';
          const subId = os.addSubagent(id, toolId);
          setSubagentCharacters((prev) => {
            if (prev.some((s) => s.id === subId)) return prev;
            return [...prev, { id: subId, parentAgentId: id, parentToolId: toolId, label }];
          });
        }
      } else if (msg.type === 'agentToolDone') {
        const id = msg.id as number;
        const toolId = msg.toolId as string;
        setAgentTools((prev) => {
          const list = prev[id];
          if (!list) return prev;
          return {
            ...prev,
            [id]: list.map((t) => (t.toolId === toolId ? { ...t, done: true } : t)),
          };
        });
      } else if (msg.type === 'agentToolsClear') {
        const id = msg.id as number;
        const bgSet = backgroundParentToolIdsRef.current[id];
        setAgentTools((prev) => {
          if (!(id in prev)) return prev;
          const next = { ...prev };
          delete next[id];
          return next;
        });
        // Keep sub-tool rows of live background spawns: their sub-characters
        // survive the parent's turn end and stay animated by their own activity.
        setSubagentTools((prev) => {
          const agentSubs = prev[id];
          if (!agentSubs) return prev;
          const kept: Record<string, ToolActivity[]> = {};
          for (const [parentToolId, rows] of Object.entries(agentSubs)) {
            if (bgSet?.has(parentToolId)) kept[parentToolId] = rows;
          }
          const next = { ...prev };
          if (Object.keys(kept).length === 0) {
            delete next[id];
          } else {
            next[id] = kept;
          }
          return next;
        });
        // Remove this agent's sub-agent characters, EXCEPT:
        // - team leads with inline teammates: their sub-agents represent real
        //   teammates, removed only by SubagentStop/subagentClear;
        // - live background spawns: removing + re-creating them on every turn
        //   end teleported the character (subagentClear removes them for real).
        const clearCh = os.characters.get(id);
        const hasInlineTeammates =
          clearCh?.teamName && clearCh?.isTeamLead && !clearCh?.teamUsesTmux;
        if (!hasInlineTeammates) {
          const doomed: string[] = [];
          for (const meta of os.subagentMeta.values()) {
            if (meta.parentAgentId === id && !bgSet?.has(meta.parentToolId)) {
              doomed.push(meta.parentToolId);
            }
          }
          for (const parentToolId of doomed) {
            os.removeSubagent(id, parentToolId);
          }
          setSubagentCharacters((prev) =>
            prev.filter((s) => s.parentAgentId !== id || bgSet?.has(s.parentToolId)),
          );
        }
        os.setAgentTool(id, null);
        os.clearPermissionBubble(id);
      } else if (msg.type === 'agentSelected') {
        const id = msg.id as number;
        setSelectedAgent(id);
      } else if (msg.type === 'agentStatus') {
        const id = msg.id as number;
        const status = msg.status as string;
        setAgentStatuses((prev) => {
          if (status === 'active') {
            if (!(id in prev)) return prev;
            const next = { ...prev };
            delete next[id];
            return next;
          }
          return { ...prev, [id]: status };
        });
        os.setAgentActive(id, status === 'active');
        if (status === 'waiting') {
          os.showWaitingBubble(id, msg.awaitingInput === true);
          playDoneSound();
        }
      } else if (msg.type === 'agentToolPermission') {
        const id = msg.id as number;
        setAgentTools((prev) => {
          const list = prev[id];
          if (!list) return prev;
          return {
            ...prev,
            [id]: list.map((t) => (t.done ? t : { ...t, permissionWait: true })),
          };
        });
        os.showPermissionBubble(id);
        playPermissionSound();
      } else if (msg.type === 'subagentToolPermission') {
        const id = msg.id as number;
        const parentToolId = msg.parentToolId as string;
        // Show permission bubble on the sub-agent character
        const subId = os.getSubagentId(id, parentToolId);
        if (subId !== null) {
          os.showPermissionBubble(subId);
        }
      } else if (msg.type === 'agentToolPermissionClear') {
        const id = msg.id as number;
        setAgentTools((prev) => {
          const list = prev[id];
          if (!list) return prev;
          const hasPermission = list.some((t) => t.permissionWait);
          if (!hasPermission) return prev;
          return {
            ...prev,
            [id]: list.map((t) => (t.permissionWait ? { ...t, permissionWait: false } : t)),
          };
        });
        os.clearPermissionBubble(id);
        // Also clear permission bubbles on all sub-agent characters of this parent
        for (const [subId, meta] of os.subagentMeta) {
          if (meta.parentAgentId === id) {
            os.clearPermissionBubble(subId);
          }
        }
      } else if (msg.type === 'subagentToolStart') {
        const id = msg.id as number;
        const parentToolId = msg.parentToolId as string;
        const toolId = msg.toolId as string;
        const status = msg.status as string;
        setSubagentTools((prev) => {
          const agentSubs = prev[id] || {};
          const list = agentSubs[parentToolId] || [];
          if (list.some((t) => t.toolId === toolId)) return prev;
          return {
            ...prev,
            [id]: { ...agentSubs, [parentToolId]: [...list, { toolId, status, done: false }] },
          };
        });
        // Update sub-agent character's tool and active state. The sub-agent is
        // usually created by an earlier agentToolStart from JSONL using the same
        // (real) parentToolId. When it's missing — a teamed lead's background
        // spawn (the creation gate above suppressed the Subtask) or a reloaded
        // panel that lost it — create it lazily; addSubagent is idempotent.
        let subId = os.getSubagentId(id, parentToolId);
        if (subId === null) {
          subId = os.addSubagent(id, parentToolId);
          const newSubId = subId;
          setSubagentCharacters((prev) => {
            if (prev.some((s) => s.id === newSubId)) return prev;
            return [...prev, { id: newSubId, parentAgentId: id, parentToolId, label: '' }];
          });
          // Only watched background spawns are created lazily -- mark the
          // parent tool as background so agentToolsClear preserves the sub.
          const set = (backgroundParentToolIdsRef.current[id] ??= new Set());
          set.add(parentToolId);
        }
        const subToolName = extractToolName(status);
        os.setAgentTool(subId, subToolName);
        os.setAgentActive(subId, true);
      } else if (msg.type === 'subagentToolDone') {
        const id = msg.id as number;
        const parentToolId = msg.parentToolId as string;
        const toolId = msg.toolId as string;
        setSubagentTools((prev) => {
          const agentSubs = prev[id];
          if (!agentSubs) return prev;
          const list = agentSubs[parentToolId];
          if (!list) return prev;
          return {
            ...prev,
            [id]: {
              ...agentSubs,
              [parentToolId]: list.map((t) => (t.toolId === toolId ? { ...t, done: true } : t)),
            },
          };
        });
      } else if (msg.type === 'subagentClear') {
        const id = msg.id as number;
        const parentToolId = msg.parentToolId as string;
        backgroundParentToolIdsRef.current[id]?.delete(parentToolId);
        setSubagentTools((prev) => {
          const agentSubs = prev[id];
          if (!agentSubs || !(parentToolId in agentSubs)) return prev;
          const next = { ...agentSubs };
          delete next[parentToolId];
          if (Object.keys(next).length === 0) {
            const outer = { ...prev };
            delete outer[id];
            return outer;
          }
          return { ...prev, [id]: next };
        });
        // Remove sub-agent character
        os.removeSubagent(id, parentToolId);
        setSubagentCharacters((prev) =>
          prev.filter((s) => !(s.parentAgentId === id && s.parentToolId === parentToolId)),
        );
      } else if (msg.type === 'characterSpritesLoaded') {
        const characters = msg.characters as Array<{
          down: string[][][];
          up: string[][][];
          right: string[][][];
        }>;
        console.log(`[Webview] Received ${characters.length} pre-colored character sprites`);
        setCharacterTemplates(characters);
      } else if (msg.type === 'petSpritesLoaded') {
        const pets = msg.pets;
        if (!Array.isArray(pets)) {
          return;
        }
        const petNames = Array.isArray(msg.petNames) ? (msg.petNames as string[]) : undefined;
        console.log(`[Webview] Received ${pets.length} pet sprites`);
        setPetTemplates(
          pets as Array<{
            walkDown: string[][][];
            idleDown: string[][][];
            walkUp: string[][][];
            idleUp: string[][][];
            walkRight: string[][][];
          }>,
          petNames,
        );
      } else if (msg.type === 'floorTilesLoaded') {
        const sprites = msg.sprites as string[][][];
        console.log(`[Webview] Received ${sprites.length} floor tile patterns`);
        setFloorSprites(sprites);
      } else if (msg.type === 'wallTilesLoaded') {
        const sets = msg.sets as string[][][][];
        console.log(`[Webview] Received ${sets.length} wall tile set(s)`);
        setWallSprites(sets);
      } else if (msg.type === 'carpetTilesLoaded') {
        const sets = msg.sets as string[][][][];
        console.log(`[Webview] Received ${sets.length} carpet variant(s)`);
        setCarpetSprites(sets);
      } else if (msg.type === 'areaMappingsLoaded') {
        const mappings = (msg.mappings ?? {}) as Record<string, string[]>;
        setAreaMappings(mappings);
        os.setAreaMappings(mappings);
      } else if (msg.type === 'workspaceFolders') {
        const folders = msg.folders as WorkspaceFolder[];
        setWorkspaceFolders(folders);
      } else if (msg.type === 'settingsLoaded') {
        const soundOn = msg.soundEnabled as boolean;
        setSoundEnabled(soundOn);
        if (typeof msg.watchAllSessions === 'boolean') {
          setWatchAllSessions(msg.watchAllSessions as boolean);
        }
        if (typeof msg.alwaysShowLabels === 'boolean') {
          setAlwaysShowLabels(msg.alwaysShowLabels as boolean);
        }
        if (typeof msg.ghostHeadlessAgents === 'boolean') {
          applyGhostHeadlessAgents(msg.ghostHeadlessAgents as boolean);
        }
        if (typeof msg.hooksEnabled === 'boolean') {
          setHooksEnabled(msg.hooksEnabled as boolean);
        }
        if (typeof msg.hooksInfoShown === 'boolean') {
          setHooksInfoShown(msg.hooksInfoShown as boolean);
        }
        if (typeof msg.showAreas === 'boolean') {
          setShowAreas(msg.showAreas as boolean);
        }
        if (Array.isArray(msg.externalAssetDirectories)) {
          setExternalAssetDirectories(msg.externalAssetDirectories as string[]);
        }
        if (typeof msg.lastSeenVersion === 'string') {
          setLastSeenVersion(msg.lastSeenVersion as string);
        }
        if (typeof msg.extensionVersion === 'string') {
          setExtensionVersion(msg.extensionVersion as string);
        }
      } else if (msg.type === 'hooksStatus') {
        if (typeof msg.installed === 'boolean' && typeof msg.providerId === 'string') {
          const providerId = msg.providerId as string;
          const installed = msg.installed as boolean;
          setHooksInstalled((m) => ({ ...m, [providerId]: installed }));
          setHooksStatusSeq((m) => ({ ...m, [providerId]: (m[providerId] ?? 0) + 1 }));
          if (installed) {
            // Moot once THIS provider's hooks are installed — the Settings toggle or another tab granted consent
            // while the dialog was open. Drop it from the queue (head or queued) rather than let a stale approval
            // re-install; another provider's status is not about this ask.
            setConsentQueue((q) => q.filter((r) => r.providerId !== providerId));
          }
        }
      } else if (msg.type === 'hooksConsentRequest') {
        if (
          typeof msg.providerId === 'string' &&
          typeof msg.headline === 'string' &&
          typeof msg.disclosure === 'string'
        ) {
          const request: HooksConsentRequest = {
            type: 'hooksConsentRequest',
            providerId: msg.providerId as string,
            headline: msg.headline as string,
            disclosure: msg.disclosure as string,
          };
          setConsentQueue((q) => {
            const i = q.findIndex((r) => r.providerId === request.providerId);
            if (i === -1) return [...q, request];
            const next = q.slice();
            next[i] = request; // a re-ask carries the freshest copy
            return next;
          });
        }
      } else if (msg.type === 'externalAssetDirectoriesUpdated') {
        if (Array.isArray(msg.dirs)) {
          setExternalAssetDirectories(msg.dirs as string[]);
        }
      } else if (msg.type === 'furnitureAssetsLoaded') {
        try {
          const catalog = msg.catalog as FurnitureAsset[];
          const sprites = msg.sprites as Record<string, string[][]>;
          console.log(`📦 Webview: Loaded ${catalog.length} furniture assets`);
          // Build dynamic catalog immediately so getCatalogEntry() works when layoutLoaded arrives next
          buildDynamicCatalog({ catalog, sprites });
          setLoadedAssets({ catalog, sprites });
        } catch (err) {
          console.error(`❌ Webview: Error processing furnitureAssetsLoaded:`, err);
        }
      } else if (msg.type === 'agentTeamInfo') {
        const id = msg.id as number;
        os.setTeamInfo(
          id,
          msg.teamName as string | undefined,
          msg.agentName as string | undefined,
          msg.isTeamLead as boolean | undefined,
          msg.leadAgentId as number | undefined,
          msg.teamUsesTmux as boolean | undefined,
        );
      } else if (msg.type === 'agentContextUsage') {
        const id = msg.id as number;
        os.setAgentContext(id, msg.contextTokens as number, msg.maxContextTokens as number);
      } else if (msg.type === 'diOfficeState') {
        // DI office: fully projected by the server. Store it; the presentation (and
        // animateActive) is applied below, after the client's own invalidation.
        const state = msg as unknown as DiOfficeState;
        diModeRef.current = true;
        // Bootstrap recovery: the layout is missing and THIS connection has not carried a
        // webviewReady (it was lost on an earlier connection and is never replayed) — request it.
        if (shouldRequestDiBootstrap(layoutReadyRef.current, diReadyRef.current.readySent)) {
          diReadyRef.current = { ...diReadyRef.current, readySent: true };
          transport.send({ type: 'webviewReady' });
        }
        // Reconcile with this NEW message; before the layout is ready it is stored and the
        // layout-ready flush reconciles against it on layout arrival.
        diStoredRef.current = state;
        // Every snapshot settles the announcements: those it lists are drawn by the canonical
        // reconcile, those it omits are refuted by it.
        setDiAnnounced((prev) => (prev.length === 0 ? prev : []));
        if (layoutReadyRef.current) reconcileDiCanvas(os, state);
        setDiServerState(state);
        setDiConnection((prev) => nextDiConnection(prev, { type: 'officeMessage' }));
      }
    };
    const unsubscribe = transport.onMessage(handler);
    transport.send({ type: 'webviewReady' });
    return unsubscribe;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [getOfficeState]);

  // Idle sub-agent characters between their turns: when every tracked tool row
  // for a sub is done, stop its typing animation (a later subagentToolStart
  // reactivates it). Watched background sub-agents get a synthesized done for
  // each live tool at their turn end, so they sit idle instead of typing forever.
  useEffect(() => {
    const os = getOfficeState();
    for (const sub of subagentCharacters) {
      const rows = subagentTools[sub.parentAgentId]?.[sub.parentToolId];
      if (rows && rows.length > 0 && rows.every((t) => t.done)) {
        os.setAgentTool(sub.id, null);
        os.setAgentActive(sub.id, false);
      }
    }
  }, [subagentTools, subagentCharacters, getOfficeState]);

  // ── DI office only: the client's OWN invalidation (server freshness cannot reach a
  // client that has stopped receiving). Inert on every other surface: nothing below runs
  // its effect until a diOfficeState has been received, and it touches no Claude-path
  // agent, tool or status.
  // watchTransportState re-reads the CURRENT state, then subscribes (adjacent, synchronous):
  // the socket can open between the first render and this effect, and a MISSED 'connected'
  // transition left the office falsely NOT LIVE (transport_connecting) in the real recapture.
  // nextDiConnection keeps the rule: leaving 'connected' drops freshness; a bare reconnect
  // restores nothing; only a NEW office message does.
  useEffect(
    () =>
      watchTransportState(transport, (state) => {
        diReadyRef.current = readyTrackerAfterTransport(diReadyRef.current, state);
        setDiConnection((prev) => nextDiConnection(prev, { type: 'transport', state }));
      }),
    [],
  );

  useEffect(() => {
    if (!diServerState) return;
    const evaluate = () =>
      setDiClientReason(
        clientInvalidationReason({
          transport: diConnection.transport,
          freshSinceConnect: diConnection.freshSinceConnect,
          nowSeconds: Math.floor(Date.now() / 1000),
          validUntil: diServerState.times.validUntil,
        }),
      );
    evaluate();
    const timer = setInterval(evaluate, DI_CLIENT_CHECK_MS);
    return () => clearInterval(timer);
  }, [diServerState, diConnection]);

  const diOfficeState = useMemo(
    () =>
      diServerState && diClientReason
        ? withClientInvalidation(diServerState, diClientReason)
        : diServerState,
    [diServerState, diClientReason],
  );

  // The PRESENTED state (false everywhere whenever the client cannot vouch) drives animation
  // for EVERY canvas character, including any not listed in it. Never removes or relabels.
  useEffect(() => {
    diPresentedRef.current = diOfficeState;
    if (!diOfficeState) return;
    const os = getOfficeState();
    const ids = [...os.characters.values()].filter((ch) => !ch.isSubagent).map((ch) => ch.id);
    for (const [id, animate] of diAnimationPlan(diOfficeState, ids, diDrawnRef.current))
      os.setAgentActive(id, animate);
  }, [diOfficeState, getOfficeState]);

  const setDiViewport = useCallback((width: number, height: number) => {
    diViewportRef.current = { width, height };
  }, []);
  const reflowDiWorld = useCallback(() => {
    const stored = diStoredRef.current;
    if (stored && layoutReadyRef.current) applyDiWorldRef.current?.(getOfficeState(), stored);
  }, [getOfficeState]);
  const markDiWorldCustom = useCallback(() => {
    diWorldCustomRef.current = true;
    diWorldRef.current = null;
  }, []);

  return {
    setDiViewport,
    reflowDiWorld,
    markDiWorldCustom,
    diWorldTick,
    diAnnounced,
    diOfficeState,
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
    setGhostHeadlessAgents: applyGhostHeadlessAgents,
    hooksEnabled,
    hooksInstalled,
    hooksStatusSeq,
    setHooksEnabled,
    hooksInfoShown,
    consentRequest,
    // Called when a tour ends (answer + Let's Go, the X, Escape) with the providerId that tour was ABOUT, removing
    // that entry so the next provider's ask becomes the head. Keyed by id, not a blind shift: an answered ask's own
    // installed:true moot may already have removed the head, and a shift would then drop the NEXT provider's ask
    // unanswered. An aborted ask returns on the next connect; the queue never re-adds it here.
    dismissConsentRequest: useCallback(
      (providerId: string | null) =>
        setConsentQueue((q) =>
          providerId === null ? q.slice(1) : q.filter((r) => r.providerId !== providerId),
        ),
      [],
    ),
    areaMappings,
    setAreaMappings,
    showAreas,
    setShowAreas,
  };
}
