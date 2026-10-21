import {
  LAYOUT_ID_MAX_LENGTH,
  LAYOUT_MAX_AREAS,
  LAYOUT_MAX_CELLS,
  LAYOUT_MAX_DIM,
  LAYOUT_MAX_FRAME_BYTES,
  LAYOUT_MAX_FURNITURE,
  LAYOUT_MAX_PETS,
} from '../../core/src/layoutLimits.js';

// ── JSONL File Watching ─────────────────────────────────────
export const JSONL_POLL_INTERVAL_MS = 1000;
export const FILE_WATCHER_POLL_INTERVAL_MS = 500;
export const PROJECT_SCAN_INTERVAL_MS = 1000;

// ── Heuristic Agent Status Detection ────────────────────────
// These timers are the fallback when CLI hooks are not active
// (hookDelivered = false). When hooks are working, these are
// suppressed and the server receives instant events instead.
/** Delay before sending agentToolDone (prevents UI flicker on rapid tool transitions) */
export const TOOL_DONE_DELAY_MS = 300;
/** Heuristic: time after a non-exempt tool starts before showing permission bubble.
 *  Not used for teammates -- false positives on slow tools (WebFetch/WebSearch).
 *  Teammates rely on the lead's routed Notification(permission_prompt) hook. */
export const PERMISSION_TIMER_DELAY_MS = 7000;
/** Heuristic: silence duration before marking a text-only turn as complete */
export const TEXT_IDLE_DELAY_MS = 5000;
/** Heuristic: idle threshold for per-agent /clear detection (content check prevents stealing) */
export const CLEAR_IDLE_THRESHOLD_MS = 2000;

// ── External Session Detection ──────────────────────────────
export const EXTERNAL_SCAN_INTERVAL_MS = 3000;
/** Only adopt JSONL files modified within this window */
export const EXTERNAL_ACTIVE_THRESHOLD_MS = 120_000; // 2 minutes
/** Remove external agents after this much inactivity */
// export const EXTERNAL_STALE_TIMEOUT_MS = 300_000; // 5 minutes - deprecated
export const EXTERNAL_STALE_CHECK_INTERVAL_MS = 30_000;
/** Cooldown after user closes an agent via X. Must be > EXTERNAL_ACTIVE_THRESHOLD_MS
 *  so the file's mtime becomes stale before the dismissal expires. */
export const DISMISSED_COOLDOWN_MS = 180_000; // 3 minutes

// ── Context Window Usage ────────────────────────────────────
/** Window size assumed until a transcript proves otherwise. Transcripts never
 *  state the model's context limit, so this is the floor, not the truth. */
export const DEFAULT_MAX_CONTEXT_TOKENS = 200_000;
/** Known window sizes, ascending. The smallest tier that fits the largest
 *  context observed so far wins; beyond the last tier we round up to a whole
 *  multiple of it, so an unknown future window still reads under 100%. */
export const CONTEXT_WINDOW_TIERS = [200_000, 1_000_000] as const;
/** How much of a transcript's tail to read when seeding an agent's context on
 *  adoption or restore. Comfortably more than one turn's worth of records. */
export const CONTEXT_SEED_TAIL_BYTES = 256 * 1024;

// ── Global Session Scanning ─────────────────────────────────
/** Only adopt global JSONL files larger than this (filters out empty/init-only sessions) */
export const GLOBAL_SCAN_ACTIVE_MIN_SIZE = 3_072; // 3KB
/** Only adopt global JSONL files modified within this window */
export const GLOBAL_SCAN_ACTIVE_MAX_AGE_MS = 600_000; // 10 minutes

// ── Display Truncation + Pixel Agents Server paths ──────────
// Centralized in core/src/constants.ts; re-exported here for back-compat.
export {
  BASH_COMMAND_DISPLAY_MAX_LENGTH,
  HOOK_API_PREFIX,
  HOOK_SCRIPTS_DIR,
  SERVER_JSON_DIR,
  SERVER_JSON_NAME,
  TASK_DESCRIPTION_DISPLAY_MAX_LENGTH,
} from '../../core/src/constants.js';

// ── Multi-Server Discovery ──────────────────────────────────
/** Subdirectory (under SERVER_JSON_DIR) holding one registry entry per live
 *  server, so a hook event can fan out to every running instance instead of
 *  only the single legacy server.json pointer. See server/src/server.ts. */
export const SERVERS_DIR = 'servers';
/** Valid explicit TCP port range. Port 0 remains an internal-only signal for
 *  OS-assigned ephemeral binding and is never accepted from persisted records
 *  or the CLI's --port option. */
export const MIN_PORT = 1;
export const MAX_PORT = 65_535;
/** Format version stamped on every registry entry (both the per-server records
 *  and the legacy server.json). Bump on breaking field changes; additive
 *  fields (servesSpa, protocol itself) don't require a bump -- readers already
 *  tolerate unknown/missing fields (see ServerConfig.debugLog precedent). */
export const SERVER_REGISTRY_PROTOCOL_VERSION = 1;

// ── WebSocket close codes (application range 4000-4999) ────
/** Embedded mode: Bearer token missing or wrong. */
export const WS_CLOSE_UNAUTHORIZED = 4001;
/** Standalone mode: the handshake's Origin is not this server's own origin.
 *  WebSocket connects bypass CORS, so this is the only thing standing between
 *  a drive-by web page and the privileged client-message channel. */
export const WS_CLOSE_FORBIDDEN_ORIGIN = 4003;

export const HOOK_EVENT_BUFFER_MS = 5_000;
/** Grace period after SessionEnd(reason=clear/resume) before triggering onSessionEnd.
 *  /clear and /resume fire SessionEnd then SessionStart within ms. This timeout is a
 *  safety net: if SessionStart never arrives (e.g. the CLI crashes mid-transition),
 *  the agent is cleaned up instead of staying as a zombie with pendingClear forever. */
export const SESSION_END_GRACE_MS = 2000;
export const MAX_HOOK_BODY_SIZE = 65_536; // 64KB

// ── Layout/Config Persistence ──────────────────────────────
export const LAYOUT_FILE_DIR = '.pixel-agents';
export const LAYOUT_FILE_NAME = 'layout.json';
/** DI office only: the decorative ambient snapshot (core/src/ambientSave.ts), under the DI state root. */
export const AMBIENT_STATE_FILE_NAME = 'ambient-state.json';
/** The viewer's city presentation preferences (lighting, weather, traffic) under the DI state root. */
export const CITY_PRESENTATION_FILE_NAME = 'city-presentation.json';
export const LAYOUT_FILE_POLL_INTERVAL_MS = 2000;
export const LAYOUT_REVISION_KEY = 'layoutRevision';
export const CONFIG_FILE_NAME = 'config.json';

// ── Avatar Customization ────────────────────────────────────
/** Number of pre-colored bundled character palettes (char_0.png–char_5.png).
 *  Mirrors `PALETTE_COUNT` in webview-ui/src/constants.ts; kept separate
 *  because the server has no DOM/sprite access and cannot import the webview
 *  constant. The two values must stay in sync. */
export const PALETTE_COUNT = 6;
/** Inclusive upper bound for a valid agent hue shift, in degrees. Used by
 *  clientMessageHandler to guard saveAgentSeats payloads from a remote or
 *  hand-edited source corrupting the stored values with out-of-range values. */
export const HUE_SHIFT_MAX_DEG = 360;

// ── DI office consumer policy (numeric selection by Supervisor + Lead within the Operator's bounds, 2026-10-14) ─────
/**
 * `wait_seconds` on every DI catalog request. Supported by the backend side's quiet curve:
 * 30 runs across FOUR wait settings (wait1 n=10, wait2 n=10, wait5 n=5, wait10 n=5); all 30
 * cold+refresh steps, across all four settings, returned 4 members. For wait1 SPECIFICALLY:
 * n=10 cold, empirical max 114 ms; n=10 refresh, empirical max 130 ms. (Global max across all
 * settings: 116 ms cold, 135 ms refresh.) Empirical maxima and run counts only — no
 * percentile. Scope of THAT curve: its runs were spaced >= 5.3 s, so it is not proof of the
 * deployed 6 s interval and does not exercise a true 5 s cadence; and it had no competing gate,
 * so it did not exercise contention. Contention, starvation and recovery ARE separately
 * evidenced by the backend side (8 sequences), not yet independently verified by the parent
 * lead — which is not the same as absent.
 */
export const DI_CATALOG_WAIT_SECONDS = 1;
/**
 * Consumer poll FLOOR: the next catalog request starts this long after the previous
 * RESPONSE completes — or after the backend's declared `limits.min_poll_interval_seconds`
 * if that is larger (theirs is a backend constraint, ours a consumer policy). A POLICY
 * CHOICE pinned by test and by the launch instructions, not a measured result.
 */
export const DI_CONSUMER_POLL_INTERVAL_SECONDS = 6;
/**
 * Bound on the DI office's shutdown DRAIN (Lead decision, 2026-10-15). A client that never
 * finishes its request keeps Fastify's close() pending indefinitely (forceCloseConnections
 * 'idle' never destroys an ACTIVE request's socket; a WebSocket peer that ignores the close
 * frame keeps its socket alive). This is a temporary loopback runtime with one browser and
 * ~130 ms of measured request work, so 5 s is ~40x headroom while still exiting promptly.
 * One definition point; not configurable.
 */
export const DI_SHUTDOWN_DRAIN_TIMEOUT_MS = 5_000;

/**
 * Mission reception desk (item 06): the most attention entries one diOfficeState carries and the
 * panel renders. The true total is always carried beside it, so a truncated list says so.
 */
export const DI_RECEPTION_MAX_ENTRIES = 24;

/**
 * Runtime agents (item 07): the most NOT-verified runtime entries one diOfficeState carries. Every
 * VERIFIED entry is always carried (each is a drawn character, already bounded by the producer and
 * the live listing); `agentsTotal` is the real count, so a capped list says so.
 */
export const DI_RUNTIME_AGENTS_MAX_UNVERIFIED = 24;

/**
 * FIXTURE SOURCE bounds (--fixture-source, visual testing only). A fixture script is read ONCE at
 * launch, size-checked before parsing, and served in-process: it replaces the backend discovery
 * read and the backend HTTP request, so a fixture office makes no backend connection at all.
 */
export const DI_FIXTURE_SOURCE_MAX_BYTES = 4 * 1024 * 1024;
export const DI_FIXTURE_MAX_FRAMES = 64;
export const DI_FIXTURE_MAX_FRAME_REPEAT = 1_000;
/** A fixture's `label` must begin with this word, so a fixture can never read as real data. */
export const DI_FIXTURE_LABEL_PREFIX = 'FIXTURE';

/**
 * DI INBOUND BOUNDS. The /ws channel carries the only write-bearing DI messages (layout,
 * seats, preferences), so it gets an explicit payload ceiling instead of the library default
 * (ws: 100 MiB). A frame above it closes the socket (1009) BEFORE any parsing (the ws layer
 * enforces maxPayload while receiving; no message event, no JSON.parse), so the ceiling bounds
 * what a connection can make the office allocate, not what it will parse.
 * The layout numbers are DEFINED ONCE in core/src/layoutLimits.ts and only re-exported here, so
 * the frame ceiling cannot drift from the grid bound again: LAYOUT_MAX_FRAME_BYTES is ~2x the
 * measured compact frame of a MAXIMAL legitimate layout at EVERY structural cap at once
 * (LAYOUT_MAX_CELLS cells, LAYOUT_MAX_FURNITURE items, ids/labels at the character cap:
 * 34,770,547 B, ~424 B/cell) and the "maximal layout fits the cap" test enforces it. The real
 * 176x112 forest world is a 1,099,668-byte frame — 51 KB over the former 1 MiB ceiling, which
 * would have dropped it.
 */
export const DI_WS_MAX_PAYLOAD_BYTES = LAYOUT_MAX_FRAME_BYTES;
/** Layout grid bound PER AXIS — the editor's own MAX_COLS/MAX_ROWS (both re-export LAYOUT_MAX_DIM). */
export const DI_LAYOUT_MAX_DIM = LAYOUT_MAX_DIM;
/** Total cells per layout — an independent cap (not DI_LAYOUT_MAX_DIM squared). */
export const DI_LAYOUT_MAX_CELLS = LAYOUT_MAX_CELLS;
/** Furniture records per layout (16,384): the connected metropolis world spends its forest's
 *  2,945 records (palace 406 included) plus its new districts', inside this bound. */
export const DI_LAYOUT_MAX_FURNITURE = LAYOUT_MAX_FURNITURE;
export const DI_LAYOUT_MAX_PETS = LAYOUT_MAX_PETS;
export const DI_LAYOUT_MAX_AREAS = LAYOUT_MAX_AREAS;
/** Max length of a uid, type id, area label, pet id or seat id. */
export const DI_ID_MAX_LENGTH = LAYOUT_ID_MAX_LENGTH;
export const DI_SEATS_MAX_ENTRIES = 1_024;
export const DI_VERSION_MAX_LENGTH = 64;

// ── DI office: ambient character sheets (M3 S6) ─────────────────
/** Folder under dist/assets holding one sub-folder per ambient sheet (manifest.json + <id>.png). */
export const AMBIENT_SPRITE_DIR = 'ambient';
/** Bounds the loader enforces before anything is sent: sheet count, file sizes, frame geometry. */
export const AMBIENT_SPRITE_SHEETS_MAX = 16;
export const AMBIENT_SPRITE_PNG_MAX_BYTES = 262_144;
export const AMBIENT_SPRITE_MANIFEST_MAX_BYTES = 16_384;
export const AMBIENT_SPRITE_FRAME_MAX_PX = 48;
export const AMBIENT_SPRITE_COLUMNS_MAX = 8;
