/**
 * The DI office's ClientMessage handler — an ALLOWLIST, not the standalone handler
 * with branches switched off.
 *
 * DI mode is passive: it never installs hooks, never asks for consent, never
 * launches, focuses or closes a terminal, never scans sessions and never reads an
 * arbitrary directory. Those capabilities are not disabled here; they are ABSENT.
 * This module imports no hook provider, no AgentRuntime, no scanner and no
 * consent code, so no message — whatever a client sends, behind whatever button —
 * can reach one. The messages it does act on only render the office or persist
 * office-local preferences (layout, seats, display toggles) under the DI state
 * root (see stateRoot.ts).
 */

import type { AmbientSpriteSheet } from '../../../core/src/messages.js';
import { resendAgentActivity } from '../agentActivityResend.js';
import type { AgentSeatMeta } from '../agentSeats.js';
import { applyAgentSeats } from '../agentSeats.js';
import type { AgentStateStore } from '../agentStateStore.js';
import type { AssetCache } from '../clientMessageHandler.js';
import { PALETTE_COUNT } from '../constants.js';
import { readLayoutFromFile, writeLayoutToFile } from '../layoutPersistence.js';
import { readAmbientState, writeAmbientState } from './ambientStatePersistence.js';
import { validateDiLayout, validateDiSeats, validateDiVersion } from './diInputLimits.js';
import { parseEvidenceRead } from './evidenceContent.js';

export type DiSend = (message: Record<string, unknown>) => void;

export interface DiMessageContext {
  store: AgentStateStore;
  cache: AssetCache;
  extensionVersion: string;
  /** The bounded ambient character sheets (ambientSprites.ts), loaded once at startup. */
  ambientSprites?: AmbientSpriteSheet[];
  /**
   * item 2: the office's evidence content read (DiOffice.readEvidence). It answers the asking
   * connection only, by an admitted reference; absent, the message is refused.
   */
  readEvidence?: (msg: Record<string, unknown>, send: DiSend) => void;
}

/** What the handler did with one message (returned for tests and diagnostics). */
export type DiDisposition = 'handled' | 'inert' | 'unknown' | { refused: string };

/** The only ClientMessage types the DI office acts on. */
export const DI_HANDLED_MESSAGE_TYPES = [
  'webviewReady',
  'saveLayout',
  'saveAgentSeats',
  'setSoundEnabled',
  'setLastSeenVersion',
  'setAlwaysShowLabels',
  'setGhostHeadlessAgents',
  'setShowAreas',
  'saveAmbientState',
  'diEvidenceRead',
] as const;

/**
 * ClientMessage types the protocol defines that DI mode deliberately does NOT act
 * on: hook install/consent, launch/terminal, session scanning, arbitrary-directory
 * asset loading, area mappings keyed by Claude folders, diagnostics of
 * Claude-session internals. Dropped without a reply and without any side effect.
 */
export const DI_INERT_MESSAGE_TYPES = [
  'launchAgent',
  'focusAgent',
  'closeAgent',
  'setHooksEnabled',
  'hooksConsentResponse',
  'setHooksInfoShown',
  'setWatchAllSessions',
  'openSessionsFolder',
  'addExternalAssetDirectory',
  'removeExternalAssetDirectory',
  'saveAreaMappings',
  'exportLayout',
  'importLayout',
  'requestDiagnostics',
] as const;

// Setting keys (same keys as the standalone surface, persisted under the DI root).
const KEY_SOUND_ENABLED = 'pixel-agents.soundEnabled';
const KEY_LAST_SEEN_VERSION = 'pixel-agents.lastSeenVersion';
const KEY_ALWAYS_SHOW_LABELS = 'pixel-agents.alwaysShowLabels';
const KEY_GHOST_HEADLESS_AGENTS = 'pixel-agents.ghostHeadlessAgents';
const KEY_SHOW_AREAS = 'pixel-agents.showAreas';

const BOOLEAN_SETTINGS: Readonly<Record<string, string>> = {
  setSoundEnabled: KEY_SOUND_ENABLED,
  setAlwaysShowLabels: KEY_ALWAYS_SHOW_LABELS,
  setGhostHeadlessAgents: KEY_GHOST_HEADLESS_AGENTS,
  setShowAreas: KEY_SHOW_AREAS,
};

const INERT: ReadonlySet<string> = new Set(DI_INERT_MESSAGE_TYPES);

/**
 * A FRESH DI office (no config yet under its state root) shows its Mission zones (Areas)
 * by default. Recorded ONCE, at creation; from then on the user's Show Areas choice persists
 * as on every surface. (getSetting never falls back to a default once the config exists.)
 */
export function initDiDefaults(
  adapter: { setSetting<T>(key: string, value: T): void },
  configExists: boolean,
): void {
  if (!configExists) adapter.setSetting(KEY_SHOW_AREAS, true);
}

export function handleDiClientMessage(
  msg: Record<string, unknown>,
  send: DiSend,
  ctx: DiMessageContext,
): DiDisposition {
  const { store, cache } = ctx;
  const adapter = store.getAdapter();
  const type = msg.type;

  switch (type) {
    case 'webviewReady':
      sendDiHandshake(send, ctx);
      return 'handled';

    // Write-bearing messages: REFUSED unless exactly the editor's shape within bounds
    // (diInputLimits.ts); nothing malformed or oversized reaches persistence.
    case 'saveLayout': {
      const reason = validateDiLayout(msg.layout);
      if (reason !== null) {
        // Nothing is acknowledged on this protocol, so a refused save must not leave the client
        // showing a layout that was never persisted: re-send the persisted one (bound-checked).
        send({ type: 'layoutLoaded', layout: readLayoutFromFile() ?? cache.defaultLayout ?? null });
        return { refused: reason };
      }
      const notWritten = writeLayoutToFile(msg.layout as Record<string, unknown>);
      if (notWritten !== null) {
        send({ type: 'layoutLoaded', layout: readLayoutFromFile() ?? cache.defaultLayout ?? null });
        return { refused: notWritten };
      }
      return 'handled';
    }

    // The decorative ambient snapshot (M3 S4): checked against its contract before it is written.
    case 'saveAmbientState': {
      const notWritten = writeAmbientState(msg.state);
      return notWritten === null ? 'handled' : { refused: notWritten };
    }

    case 'saveAgentSeats': {
      const reason = validateDiSeats(msg.seats);
      if (reason !== null) return { refused: reason };
      applyAgentSeats(
        store,
        msg.seats as Record<string, AgentSeatMeta>,
        cache.characters?.characters.length ?? PALETTE_COUNT,
      );
      return 'handled';
    }

    case 'setSoundEnabled':
    case 'setAlwaysShowLabels':
    case 'setGhostHeadlessAgents':
    case 'setShowAreas':
      if (typeof msg.enabled !== 'boolean') return { refused: 'enabled is not a boolean' };
      adapter?.setSetting(BOOLEAN_SETTINGS[type], msg.enabled);
      return 'handled';

    case 'setLastSeenVersion': {
      const reason = validateDiVersion(msg.version);
      if (reason !== null) return { refused: reason };
      adapter?.setSetting(KEY_LAST_SEEN_VERSION, msg.version as string);
      return 'handled';
    }

    // item 2 (evidence library): a READ, by an admitted reference only — never a path. Nothing
    // is written; the office answers this connection with exactly one diEvidenceContent.
    case 'diEvidenceRead': {
      if (parseEvidenceRead(msg) === null) {
        return { refused: 'diEvidenceRead is not exactly an admitted reference' };
      }
      if (!ctx.readEvidence) return { refused: 'no evidence reader in this office' };
      ctx.readEvidence(msg, send);
      return 'handled';
    }

    default:
      return typeof type === 'string' && INERT.has(type) ? 'inert' : 'unknown';
  }
}

/**
 * The DI `webviewReady` handshake: assets, settings, agents, layout — and nothing
 * Claude-shaped. No providerCapabilities tool lists, no hooksStatus, no
 * hooksConsentRequest (there is no provider to ask about), no session restore.
 */
export function sendDiHandshake(send: DiSend, ctx: DiMessageContext): void {
  const { store, cache } = ctx;
  const adapter = store.getAdapter();

  send({ type: 'providerCapabilities', readingTools: [], subagentToolNames: [] });

  if (cache.characters) {
    send({ type: 'characterSpritesLoaded', characters: cache.characters.characters });
  }
  if (cache.pets) {
    send({
      type: 'petSpritesLoaded',
      pets: cache.pets.pets,
      petNames: cache.pets.manifests.map((m) => m.name),
    });
  }
  if (cache.floorTiles) send({ type: 'floorTilesLoaded', sprites: cache.floorTiles });
  if (cache.wallTiles) send({ type: 'wallTilesLoaded', sets: cache.wallTiles });
  if (cache.carpetTiles) send({ type: 'carpetTilesLoaded', sets: cache.carpetTiles });
  if (cache.furniture) {
    send({
      type: 'furnitureAssetsLoaded',
      catalog: cache.furniture.catalog,
      sprites: Object.fromEntries(cache.furniture.sprites),
    });
  }

  send({
    type: 'settingsLoaded',
    soundEnabled: adapter?.getSetting(KEY_SOUND_ENABLED, true) ?? true,
    lastSeenVersion: adapter?.getSetting(KEY_LAST_SEEN_VERSION, '') ?? '',
    extensionVersion: ctx.extensionVersion,
    watchAllSessions: false,
    alwaysShowLabels: adapter?.getSetting(KEY_ALWAYS_SHOW_LABELS, false) ?? false,
    ghostHeadlessAgents: adapter?.getSetting(KEY_GHOST_HEADLESS_AGENTS, false) ?? false,
    // No hook provider exists in DI mode: report hooks off, and the hooks info
    // as already shown so the webview never offers a hooks explainer.
    hooksEnabled: false,
    hooksInfoShown: true,
    externalAssetDirectories: [],
    showAreas: adapter?.getSetting(KEY_SHOW_AREAS, false) ?? false,
  });

  send({ type: 'areaMappingsLoaded', mappings: {} });

  // Existing agents BEFORE layout: the webview buffers existingAgents and only
  // materializes them on the next layoutLoaded (same ordering as standalone).
  const agentIds: number[] = [];
  const folderNames: Record<number, string> = {};
  const persistedSeats = adapter?.loadSeats() ?? {};
  const agentMeta: Record<number, { palette?: number; hueShift?: number; seatId?: string }> = {};
  for (const [id, agent] of store) {
    agentIds.push(id);
    if (agent.folderName) folderNames[id] = agent.folderName;
    agentMeta[id] = {
      palette: agent.palette,
      hueShift: agent.hueShift,
      seatId: persistedSeats[String(id)]?.seatId,
    };
  }
  send({ type: 'existingAgents', agents: agentIds, agentMeta, folderNames, externalAgents: {} });

  // the decorative ambient snapshot BEFORE the layout: the webview holds it until it builds the
  // residents for that layout, then applies it once if the world fingerprint matches
  if (ctx.ambientSprites && ctx.ambientSprites.length > 0) send({ type: 'ambientSpritesLoaded', sheets: ctx.ambientSprites });
  send({ type: 'ambientStateLoaded', state: readAmbientState() });

  send({ type: 'layoutLoaded', layout: readLayoutFromFile() ?? cache.defaultLayout ?? null });

  resendAgentActivity(send, store);
}
