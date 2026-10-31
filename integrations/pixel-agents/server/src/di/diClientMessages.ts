/**
 * The DI office's ClientMessage handler — an ALLOWLIST, not the standalone handler
 * with branches switched off.
 *
 * DI mode is passive: it never installs hooks, never asks for consent, never
 * launches, focuses or closes a terminal, never scans sessions and never reads an
 * arbitrary directory. Those capabilities are not disabled here; they are ABSENT.
 * This module imports no hook provider, no AgentRuntime, no scanner and no
 * consent code, so no message — whatever a client sends, behind whatever button —
 * can reach one. The messages it does act on only render the office, persist
 * office-local preferences (layout, seats, display toggles) under the DI state
 * root (see stateRoot.ts), or READ by an admitted reference (evidence, code review).
 *
 * TWO deliberate, bounded exceptions, each relayed by the server (which alone holds its token) to
 * a loopback adapter a person starts; this module never decides anything. (A THIRD, the Herdr
 * terminal route, is not a relay to an adapter; it is described after these two.)
 *
 * - item 4, Mission requests: `diMissionRequest`. It is not passive: it can submit a proposal,
 *   relay a person's typed decision (or, by owner decision (c), their decline, rejection or change
 *   request) and ask for a run to start. It is still not a shell, a PTY, a
 *   filesystem read or a command route. It is one of fourteen closed operations, each with its own
 *   exact keys and reference-shaped ids, relayed to the request panel's adapter. That adapter, not
 *   this module, binds a decision to the displayed Mission, revision and digest and records it as
 *   an operator attestation that is NOT independently verified.
 * - b2b, the delivery bridge: `diDeliveryRequest`. Two closed operations: `status`, a read, and
 *   `advanceOnce`, which asks the office delivery adapter for AT MOST ONE step, carrying the exact
 *   binding of that step as the adapter displayed it. pr_delivery's expected-step guard, not this
 *   module, admits or refuses it before any effect. No op advances further, revokes, mints or retries.
 * - The Herdr terminal route: `diHerdrRequest`. Two closed operations: `status`, which runs the route's
 *   admission and does nothing, and `launch`, which (only after that admission) writes one launcher file,
 *   focuses the herd's workspace through Herdr's socket API and asks macOS to open a Terminal window on the
 *   launcher. It sends no keystroke, prompt or text to any pane or terminal (diNoTerminalInput.test.ts
 *   scans for that over this code). It exists only when the office was started with the Herdr flags; the
 *   one process-starting module in the entry's closure is herdrProcess.ts (pinned by name in the tests).
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
import { readCityPresentation, writeCityPresentation } from './cityPresentationPersistence.js';
import { parseCodeReviewRead, parseMissionReviewRead, unadmittedReadReply } from './codeReview.js';
import { invalidRequestReply, notConfiguredReply, parseDeliveryRequest } from './deliveryRequests.js';
import { validateDiLayout, validateDiSeats, validateDiVersion } from './diInputLimits.js';
import { readDiSeatPreferences, writeDiSeatPreferences } from './diSeatPreferencesPersistence.js';
import { parseEvidenceRead } from './evidenceContent.js';
import {
  invalidRequestReply as invalidHerdrReply,
  notConfiguredReply as herdrNotConfiguredReply,
  parseHerdrRequest,
} from './herdrRequests.js';
import {
  invalidMissionRequestReply,
  panelNotConfiguredReply,
  parseMissionRequest,
} from './missionRequests.js';

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
  /**
   * item 3: the office's live code review read (DiOffice.readCodeReview). It answers the asking
   * connection only, by an admitted reference; absent, the message is refused.
   */
  readCodeReview?: (msg: Record<string, unknown>, send: DiSend) => void;
  /**
   * item 4: the Mission request panel (DiMissionRequests.handle). It answers the asking
   * connection only; absent, the message is refused.
   */
  missionRequest?: (msg: Record<string, unknown>, send: DiSend) => void;
  /**
   * b2b: the office delivery adapter (DiDeliveryRequests.handle). It answers the asking
   * connection only, with one adapter request at most; absent, the message is refused.
   */
  deliveryRequest?: (msg: Record<string, unknown>, send: DiSend) => void;
  /**
   * The Herdr terminal route (DiHerdrRequests.handle). It answers the asking connection only, after the route's own admission checks; absent, the message is answered "not configured".
   */
  herdrRequest?: (msg: Record<string, unknown>, send: DiSend) => void;
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
  'saveCityPresentation',
  'saveDiSeatPreferences',
  'diEvidenceRead',
  'diCodeReviewRead',
  'diCodeReviewMissionRead',
  'diMissionRequest',
  'diDeliveryRequest',
  'diHerdrRequest',
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
 * The number-keyed seats a client saved during THIS server process, per store (the process-lifetime object). The DI handshake offers a character's seat
 * only from here: a seat file written by an earlier process names characters by numbers that process assigned, and numbers restart at 1, so an old
 * number-keyed entry could attach a seat to a different agent. Such entries are ignored, never misapplied; the identity-keyed preferences
 * (seat-preferences.json) carry the seating across restarts instead.
 */
const seatsSavedThisProcess = new WeakMap<
  AgentStateStore,
  Record<string, { palette?: number; hueShift?: number; seatId?: string }>
>();

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

    // The viewer's city presentation preferences (living city 6-9): checked against the shared
    // contract before they are written. Local decoration; no Mission, agent or canonical meaning.
    case 'saveCityPresentation': {
      const notWritten = writeCityPresentation(msg.state);
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
      // these number-keyed seats were saved by THIS process, so each number still names the same identity (the identity map never reassigns a number)
      seatsSavedThisProcess.set(store, { ...(msg.seats as Record<string, AgentSeatMeta>) });
      return 'handled';
    }

    // The office's seat preferences, keyed by canonical identity (never by the process-local number): checked against the shared contract before
    // anything is written. Local seating preference; no Mission, agent or canonical meaning.
    case 'saveDiSeatPreferences': {
      const notWritten = writeDiSeatPreferences(msg.state);
      return notWritten === null ? 'handled' : { refused: notWritten };
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

    // item 3 (live code review): a READ, by an admitted reference only — never a path, root, ref
    // or git option. Nothing is written; the office answers this connection with exactly one
    // diCodeReview.
    // THE CONTRACT: one answer on every path, a refusal included. A read refused unread is answered when it can be named (its shape is exactly a
    // read's: codeReview.ts unadmittedReadReply); one that cannot be named gets nothing, as an uncorrelatable delivery request gets nothing.
    case 'diCodeReviewRead': {
      if (parseCodeReviewRead(msg) === null) {
        const reply = unadmittedReadReply(msg);
        if (reply !== null) send(reply as unknown as Record<string, unknown>);
        return { refused: 'diCodeReviewRead is not exactly an admitted reference' };
      }
      if (!ctx.readCodeReview) return { refused: 'no code review reader in this office' };
      ctx.readCodeReview(msg, send);
      return 'handled';
    }

    // The same read for a FINISHED Mission, by a Mission-level reference (no member): the Mission, its task and the listing token the office gave
    // with the Mission's own offer. Never a path, root, ref or git option. Answered with exactly one diCodeReview.
    case 'diCodeReviewMissionRead': {
      if (parseMissionReviewRead(msg) === null) {
        const reply = unadmittedReadReply(msg);
        if (reply !== null) send(reply as unknown as Record<string, unknown>);
        return { refused: 'diCodeReviewMissionRead is not exactly an admitted reference' };
      }
      if (!ctx.readCodeReview) return { refused: 'no code review reader in this office' };
      ctx.readCodeReview(msg, send);
      return 'handled';
    }

    // item 4 (Mission requests): one of fourteen closed operations, relayed to the request panel's
    // loopback adapter. The shape is checked HERE, before anything is sent, and again by the
    // handler that relays it; a decision is the person's typed word (or their chosen non-approval
    // outcome and its reason), never anything generated.
    // EVERY diMissionRequest inside the correlation domain (a valid type, seq and op) gets EXACTLY ONE correlated reply: the relay
    // sends it, or, when this office refuses the message itself, this case sends it, built by the relay's own reply functions. A
    // message outside the domain gets none, and no seq or op is invented for it.
    case 'diMissionRequest': {
      const parsed = parseMissionRequest(msg);
      if (!parsed.ok) {
        const refusal = invalidMissionRequestReply(parsed);
        if (refusal !== null) send(refusal as unknown as Record<string, unknown>);
        return { refused: `diMissionRequest ${parsed.reason}` };
      }
      if (!ctx.missionRequest) {
        send(panelNotConfiguredReply(parsed) as unknown as Record<string, unknown>);
        return { refused: 'no request panel in this office' };
      }
      ctx.missionRequest(msg, send);
      return 'handled';
    }

    // b2b (the delivery bridge): one of two closed operations, relayed to the office delivery adapter's
    // loopback interface. The shape is checked HERE, before anything is sent, and again by the relay;
    // advanceOnce asks for at most one step, which pr_delivery's expected-step guard admits or refuses.
    // THE CORRELATION DOMAIN (round 48; owed item 1): every diDeliveryRequest whose type, seq and op are valid gets EXACTLY
    // ONE correlated reply: the relay sends it, or, when this office refuses the message itself, this case sends it, built by
    // the relay's own reply functions. A message without a valid seq or op cannot be correlated: no reply, nothing invented.
    case 'diDeliveryRequest': {
      const parsed = parseDeliveryRequest(msg);
      if (!parsed.ok) {
        const refusal = invalidRequestReply(parsed);
        if (refusal !== null) send(refusal as unknown as Record<string, unknown>);
        return { refused: `diDeliveryRequest ${parsed.reason}` };
      }
      if (!ctx.deliveryRequest) {
        send(notConfiguredReply(parsed) as unknown as Record<string, unknown>);
        return { refused: 'no delivery adapter in this office' };
      }
      ctx.deliveryRequest(msg, send);
      return 'handled';
    }

    // The Herdr terminal route: one of two closed operations (`status`, a read with no effect; `launch`), relayed to the route. The shape is checked HERE and again by the relay. Every diHerdrRequest whose
    // type, seq and op are valid gets EXACTLY ONE correlated reply (the relay sends it, or this case does when the office refuses the message itself); one without a valid seq or op gets none.
    case 'diHerdrRequest': {
      const parsed = parseHerdrRequest(msg);
      if (!parsed.ok) {
        const refusal = invalidHerdrReply(parsed);
        if (refusal !== null) send(refusal as unknown as Record<string, unknown>);
        return { refused: `diHerdrRequest ${parsed.reason}` };
      }
      if (!ctx.herdrRequest) {
        send(herdrNotConfiguredReply(parsed) as unknown as Record<string, unknown>);
        return { refused: 'no Herdr route in this office' };
      }
      ctx.herdrRequest(msg, send);
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
  const persistedSeats = seatsSavedThisProcess.get(store) ?? {};
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
  // the identity-keyed seat preferences, before the layout: the webview holds them until it places characters
  send({ type: 'diSeatPreferencesLoaded', state: readDiSeatPreferences() });

  // the decorative ambient snapshot BEFORE the layout: the webview holds it until it builds the
  // residents for that layout, then applies it once if the world fingerprint matches
  if (ctx.ambientSprites && ctx.ambientSprites.length > 0) send({ type: 'ambientSpritesLoaded', sheets: ctx.ambientSprites });
  send({ type: 'ambientStateLoaded', state: readAmbientState() });
  send({ type: 'cityPresentationLoaded', state: readCityPresentation() });

  send({ type: 'layoutLoaded', layout: readLayoutFromFile() ?? cache.defaultLayout ?? null });

  resendAgentActivity(send, store);
}
