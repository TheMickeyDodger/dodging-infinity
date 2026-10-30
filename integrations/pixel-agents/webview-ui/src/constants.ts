import { LAYOUT_MAX_CELLS, LAYOUT_MAX_DIM } from '../../core/src/layoutLimits.js';
import type { ColorValue } from './components/ui/types.js';

// ── Grid & Layout ────────────────────────────────────────────
export const TILE_SIZE = 16;
export const DEFAULT_COLS = 20;
export const DEFAULT_ROWS = 11;
/**
 * WORLD CEILING: the editor's grid bound (expandLayout refuses past it). DEFINED ONCE, in
 * core/src/layoutLimits.ts (LAYOUT_MAX_DIM), which the DI server's DI_LAYOUT_MAX_DIM and every
 * layout entry path also use — so the editor can never build what the server refuses. Each axis
 * up to 384 AND at most MAX_CELLS (110,400) cells in all — an independent cap, not 384 x 384: room
 * for the 352 x 304 living metropolis (107,008 cells) with editor headroom. The renderer
 * culls to the viewport, so per-frame tile work is bounded by the window.
 */
export const MAX_COLS = LAYOUT_MAX_DIM;
export const MAX_ROWS = LAYOUT_MAX_DIM;
export const MAX_CELLS = LAYOUT_MAX_CELLS;
/**
 * WATER floors are never walkable (no false walkability on ponds, rivers, shallows or sea): tile
 * values FLOOR_14 (still water — the forest pond), FLOOR_22 (deep sea) and FLOOR_23 (river, lake
 * shallows). Bridges, piers and jetties are plank floors (FLOOR_13 deck, FLOOR_24 boardwalk) laid
 * over the crossing, so a crossing exists exactly where a bridge does.
 */
// TileType.FLOOR_14 (still water), FLOOR_22 (sea), FLOOR_23 (river / lake) — numbers here because
// office/types.ts imports this module (types.ts names every tile value, append-only)
export const WATER_FLOOR_TILES: ReadonlySet<number> = new Set([14, 22, 23]);
/** The former 64 x 64 ceiling. A layout within it is a LEGACY-SIZE world and keeps the original
 *  whole-world fit framing exactly; a larger one is a LARGE world (named camera views). */
export const LEGACY_WORLD_MAX_DIM = 64;

// ── Character Animation ─────────────────────────────────────
export const WALK_SPEED_PX_PER_SEC = 48;
export const WALK_FRAME_DURATION_SEC = 0.15;
export const TYPE_FRAME_DURATION_SEC = 0.3;
export const WANDER_PAUSE_MIN_SEC = 2.0;
export const WANDER_PAUSE_MAX_SEC = 20.0;
export const WANDER_MOVES_BEFORE_REST_MIN = 3;
export const WANDER_MOVES_BEFORE_REST_MAX = 6;
export const SEAT_REST_MIN_SEC = 120.0;
export const SEAT_REST_MAX_SEC = 240.0;

// ── Matrix Effect ────────────────────────────────────────────
export const MATRIX_EFFECT_DURATION_SEC = 0.3;
export const MATRIX_TRAIL_LENGTH = 6;
export const MATRIX_SPRITE_COLS = 16;
export const MATRIX_SPRITE_ROWS = 24;
export const MATRIX_FLICKER_FPS = 30;
export const MATRIX_FLICKER_VISIBILITY_THRESHOLD = 180;
export const MATRIX_COLUMN_STAGGER_RANGE = 0.3;
export const MATRIX_HEAD_COLOR = '#ccffcc';
export const matrixGreenBright = (a: number): string => `rgba(0, 255, 65, ${a})`;
export const matrixGreenMid = (a: number): string => `rgba(0, 170, 40, ${a})`;
export const matrixGreenDim = (a: number): string => `rgba(0, 85, 20, ${a})`;
export const MATRIX_TRAIL_OVERLAY_ALPHA = 0.6;
export const MATRIX_TRAIL_EMPTY_ALPHA = 0.5;
export const MATRIX_TRAIL_MID_THRESHOLD = 0.33;
export const MATRIX_TRAIL_DIM_THRESHOLD = 0.66;

// ── Rendering ────────────────────────────────────────────────
export const CHARACTER_SITTING_OFFSET_PX = 6;
export const CHARACTER_Z_SORT_OFFSET = 0.5;
export const OUTLINE_Z_SORT_OFFSET = 0.001;
export const SELECTED_OUTLINE_ALPHA = 1.0;
export const HOVERED_OUTLINE_ALPHA = 0.5;
/** The working halo (M3 S5), drawn as the human asked ("they should just glow"): a soft AURA hugging an
 *  eligible real agent's own silhouette, behind the body. It reaches HALO_AURA_RADIUS_PX sprite pixels
 *  out from the silhouette, fading with distance, and never below the sprite's lowest opaque row: no
 *  ring, no floor ellipse, nothing under the feet. Eligibility is office/halo.ts alone. */
export const HALO_AURA_RGB = '#FFD666';
export const HALO_AURA_RADIUS_PX = 3;
/** Aura alpha (0-255) at 1, 2 and 3 sprite pixels from the silhouette; the draw multiplies by the halo
 *  intensity (haloIntensity: steady under reduced motion). */
export const HALO_AURA_ALPHAS: readonly number[] = [0xff, 0xb0, 0x60];
/** The aura-mask cache's HARD entry cap (least-recently-used eviction; getAuraSprite). The character
 *  sprite cache it reads from is a strong map over palette:hue keys, so the masks need their own bound.
 *  256 covers 48 working agents' typing and reading frames without thrashing. */
export const HALO_AURA_CACHE_MAX_ENTRIES = 256;
/** Headless agents (adopted, no terminal to focus) render slightly translucent. */
export const HEADLESS_CHARACTER_ALPHA = 0.5;
export const GHOST_PREVIEW_SPRITE_ALPHA = 0.5;
export const GHOST_PREVIEW_TINT_ALPHA = 0.25;
export const SELECTION_DASH_PATTERN: [number, number] = [4, 3];
export const BUTTON_MIN_RADIUS = 6;
export const BUTTON_RADIUS_ZOOM_FACTOR = 3;
export const BUTTON_ICON_SIZE_FACTOR = 0.45;
export const BUTTON_LINE_WIDTH_MIN = 1.5;
export const BUTTON_LINE_WIDTH_ZOOM_FACTOR = 0.5;
export const BUBBLE_FADE_DURATION_SEC = 0.5;
export const BUBBLE_SITTING_OFFSET_PX = 10;
export const BUBBLE_VERTICAL_OFFSET_PX = 24;
export const FALLBACK_FLOOR_COLOR = '#808080';
/**
 * The PRE-COMPOSED CARPET LAYER (renderer.ts) is one offscreen canvas holding every carpet at BASE
 * resolution (one pixel per sprite pixel), whatever the zoom, so it spans at most
 * (cols + 1) x (rows + 1) x 16 x 16 pixels. This guard (28 Mi px = 29,360,128 px, 117,440,512 B
 * RGBA; r4 had 24 Mi px) is above that for every admitted grid — the worst, 382 x 289, needs
 * 28,433,920 px; the living metropolis 352 x 304 needs 27,562,240 px — so an admitted layout never
 * falls back to drawing junctions directly (a unit test asserts it over every width). These are
 * allocation ARITHMETIC, not measured process memory. M3 S2 raised the guard with the cell cap.
 */
export const CARPET_LAYER_MAX_PIXELS = 28 * 1024 * 1024;
/**
 * RENDER-CACHE BOUND across edits (office/renderCaches.ts): when the colorize and carpet caches
 * together hold more entries than this after a layout rebuild, both are cleared at that safe
 * point and the current layout's sprites are recomputed lazily. MEASURED need of one layout
 * (lifecycle evidence, measurements/proofs-*): the shipped connected world 424 entries, the
 * capped render workload 3,072 — neither reaches 4,096 on its own; edits that keep bringing new
 * colours can, and then the caches clear and refill with what is in use. Each entry
 * pins small canvases (one per zoom drawn), so the bound is kept near that need: at 16,384 an
 * edits-only run grew Chrome RSS by about 1 GB before the first clear (receipt retained). A layout
 * whose WALLS use many colour variants can need more (up to 16 auto-tile sprites per wall colour);
 * it then recomputes its sprites once per rebuild — memory stays bounded, edits are slower.
 */
export const RENDER_CACHE_MAX_ENTRIES = 4_096;
/**
 * PREVIEW caches (office/colorize.ts, office/sprites/carpetTiles.ts): sprites colorized for EDITOR
 * previews — brush swatches (floor, wall, carpet), catalogue thumbnails and the placement ghost —
 * are kept apart from the layout's own art and bounded AT INSERTION, because a brush-colour change
 * creates them without any layout rebuild. When full, a preview cache is cleared and refilled with
 * what is on screen. 1,024 holds the furniture catalogue's thumbnails for one brush colour (~330 at
 * the shipped catalogue) a few times over; carpet swatches need far fewer.
 */
export const PREVIEW_CACHE_MAX_ENTRIES = 1_024;
export const CARPET_PREVIEW_CACHE_MAX_ENTRIES = 256;

// ── Rendering - Overlay Colors (canvas, not CSS) ─────────────
export const SEAT_OWN_COLOR = 'rgba(0, 127, 212, 0.35)';
export const SEAT_AVAILABLE_COLOR = 'rgba(0, 200, 80, 0.35)';
export const SEAT_BUSY_COLOR = 'rgba(220, 50, 50, 0.35)';
export const GRID_LINE_COLOR = 'rgba(255,255,255,0.12)';
export const VOID_TILE_OUTLINE_COLOR = 'rgba(255,255,255,0.08)';
export const VOID_TILE_DASH_PATTERN: [number, number] = [2, 2];
export const GHOST_BORDER_HOVER_FILL = 'rgba(60, 130, 220, 0.25)';
export const GHOST_BORDER_HOVER_STROKE = 'rgba(60, 130, 220, 0.5)';
export const GHOST_BORDER_STROKE = 'rgba(255, 255, 255, 0.06)';
export const GHOST_VALID_TINT = '#00ff00';
export const GHOST_INVALID_TINT = '#ff0000';
export const SELECTION_HIGHLIGHT_COLOR = '#007fd4';
export const DELETE_BUTTON_BG = 'rgba(200, 50, 50, 0.85)';
export const ROTATE_BUTTON_BG = 'rgba(50, 120, 200, 0.85)';
export const BUTTON_ICON_COLOR = '#fff';
export const CANVAS_FALLBACK_TILE_COLOR = '#444';
export const CANVAS_ERROR_TILE_COLOR = '#FF00FF';
export const WALL_COLOR = '#3A3A5C';

// ── Camera ───────────────────────────────────────────────────
export const CAMERA_FOLLOW_LERP = 0.1;
export const CAMERA_FOLLOW_SNAP_THRESHOLD = 0.5;

// ── Zoom ─────────────────────────────────────────────────────
export const ZOOM_MIN = 1;
export const ZOOM_MAX = 10;
export const ZOOM_DEFAULT_DPR_FACTOR = 2;
export const ZOOM_LEVEL_FADE_DELAY_MS = 1500;
export const ZOOM_LEVEL_HIDE_DELAY_MS = 2000;
export const ZOOM_LEVEL_FADE_DURATION_SEC = 0.5;
export const ZOOM_SCROLL_THRESHOLD = 50;
export const PAN_MARGIN_FRACTION = 0.25;

// ── Large-world views (worlds beyond LEGACY_WORLD_MAX_DIM; see office/layout/worldViews.ts) ──
/** Named views never go below 1 CSS pixel per sprite pixel (zoom = round(DPR) on Retina): the
 *  office stays legible instead of shrinking to fit a world it cannot fit. */
export const WORLD_VIEW_MIN_CSS_PX_PER_SPRITE_PX = 1;
/** Furniture types whose footprints define the observation deck view. */
export const WORLD_DECK_TYPE_PREFIX = 'DECK_';
/** Deck view: where the deck's south edge sits, as a fraction of the canvas height from the top,
 *  so the frame looks out over what lies north of the deck. */
export const WORLD_DECK_VIEW_SOUTH_EDGE_FRACTION = 0.85;
/** Arrow-key pan step, as a fraction of the canvas (presentation only; not in edit mode). */
export const WORLD_KEY_PAN_FRACTION = 0.2;
/** The whole-world Map overlay: margin inside the office area, redraw cadence (characters move;
 *  slower under reduced motion), and the colour of the current-camera rectangle drawn on it. */
export const WORLD_MAP_MARGIN_PX = 24;
/** District labels drawn on the whole-world Map (readable at whole-world scale). */
export const WORLD_MAP_LABEL_FONT_CSS_PX = 13;
export const WORLD_MAP_LABEL_COLOR = '#fff4d6';
export const WORLD_MAP_LABEL_SHADOW_COLOR = '#10121a';
export const WORLD_MAP_HOME_LABEL = 'Palace (home)';
/** The camera-view controls (Districts list included) start this far from the top of the world. They SHARE the left strip's height with the bottom-left column
 *  (WorldLeftRail): they are given what the column leaves, scroll inside it, and their stops scroll into view when focused. */
export const WORLD_VIEW_CONTROLS_TOP_PX = 104;
/** The left strip (WorldLeftRail) runs from this far below the top of the world to this far above its bottom. */
export const WORLD_RAIL_TOP_PX = 8;
export const WORLD_RAIL_BOTTOM_PX = 10;
/** One camera-view button (36 px): the least the rail's first row is ever given (the controls' own offset plus this), so at least one stop is always shown. Where that plus the
 *  bottom column is taller than the window, the strip itself scrolls (anchored so the column stays in view) and a stop scrolls into view when focused: the controls are NEVER removed
 *  for want of height (navigation is not lost at 400% zoom), and nothing is left as a stop that cannot be reached and seen. */
export const WORLD_VIEW_CONTROL_MIN_PX = 36;
export const WORLD_MAP_REFRESH_MS = 500;
export const WORLD_MAP_REFRESH_REDUCED_MS = 2000;
/** The Map's living layer markers (C3): resident dot and train bar sizes, canvas pixels. */
export const WORLD_MAP_RESIDENT_DOT_PX = 3;
export const WORLD_MAP_TRAIN_BAR_PX = 4;
export const TRANSIT_MAP_TRAIN_COLOR = '#2f8f8a';
/** The Map's decorative-traffic markers: one box per visible vehicle, its footprint, never smaller than this many canvas pixels each way. */
export const WORLD_MAP_VEHICLE_MIN_PX = 2;
/** One neutral colour for every vehicle (no kind colouring, so an emergency-looking vehicle reads as an ordinary one). */
export const WORLD_MAP_VEHICLE_COLOR = '#e6d9ae';
export const WORLD_MAP_CAMERA_COLOR = '#ffe28a';
export const WORLD_MAP_CAMERA_LINE_CSS_PX = 2;

// ── Editor ───────────────────────────────────────────────────
export const UNDO_STACK_MAX_SIZE = 50;
export const LAYOUT_SAVE_DEBOUNCE_MS = 500;

// ── Layout Import/Export (browser-native, standalone) ────────
/** Suggested filename when exporting the office layout from the standalone browser. */
export const LAYOUT_EXPORT_FILENAME = 'pixel-agents-layout.json';
/** DI office: the decorative ambient snapshot export (M3 S4) is named by what it carries:
 *  `pixel-agents-ambient-<N>-aboard.json`, N = residents aboard a train at export. */
export const AMBIENT_EXPORT_FILENAME_STEM = 'pixel-agents-ambient';
/** File-picker filter for JSON imports (the non-DI file route): the extension AND the MIME type.
 *  It was TRIED for the DI office's native macOS picker (Brave), where Open stayed disabled for a valid
 *  owned .json file, and it did NOT fix it (Open stayed disabled under All Files too). The picker's
 *  cause was NOT diagnosed. The DI office in a browser imports by PASTED JSON instead (an alternative
 *  supported route, not a repair); this value is kept only because it is harmless. */
export const JSON_FILE_ACCEPT = '.json,application/json';
/** MIME type for the exported layout Blob. */
export const LAYOUT_EXPORT_MIME = 'application/json';
export const DEFAULT_FLOOR_COLOR: ColorValue = { h: 35, s: 30, b: 15, c: 0 };
export const DEFAULT_WALL_COLOR: ColorValue = { h: 240, s: 25, b: 0, c: 0 };
export const DEFAULT_NEUTRAL_COLOR: ColorValue = { h: 0, s: 0, b: 0, c: 0 };

// ── Carpets ──────────────────────────────────────────────────
/** Main (lowest-luminance) color applied to carpets when no per-tile override is set. */
export const CARPET_DEFAULT_COLOR: ColorValue = { h: 0, s: 71, b: -32, c: 0, colorize: true };
/** Accent (highest-luminance) color applied to carpets when no per-tile override is set. */
export const CARPET_DEFAULT_ACCENT_COLOR: ColorValue = {
  h: 34,
  s: 64,
  b: 21,
  c: 0,
  colorize: true,
};
/** Keyboard key that switches from CARPET_PAINT to CARPET_PICK while editing. */
export const KEY_CARPET_PICK = 'p';

// ── Areas (named, colored workspace-folder zones) ────────────
/** Color palette assigned to new Areas in rotation (cycles when more areas exist). */
export const AREA_DEFAULT_COLORS: readonly string[] = [
  '#ff6b6b',
  '#feca57',
  '#48dbfb',
  '#1dd1a1',
  '#5f27cd',
  '#ff9ff3',
  '#54a0ff',
  '#ffa502',
] as const;
/** Translucent overlay alpha for area tile fills. */
export const AREA_OVERLAY_ALPHA = 0.25;
/** Alpha multiplier applied to the actively-selected area's overlay. */
export const AREA_ACTIVE_ALPHA_MULTIPLIER = 1.6;
/** Base font size (pixel-pre-zoom) for area centroid labels. */
export const AREA_LABEL_FONT_SIZE_PX = 14;
/** Minimum on-screen label size to keep labels legible at low zoom. */
export const AREA_LABEL_MIN_FONT_SIZE_PX = 12;
/** Alpha of the area label text. */
export const AREA_LABEL_ALPHA = 1.0;
/** Fallback label color when an area has no color set (shouldn't happen in practice). */
export const AREA_LABEL_FALLBACK_COLOR = '#ffffff';
/** Drop-shadow color behind area labels for legibility on light backgrounds. */
export const AREA_LABEL_SHADOW_COLOR = '#000000';
/** Drop-shadow alpha behind area labels. */
export const AREA_LABEL_SHADOW_ALPHA = 0.6;

// ── VisualColorPicker (HSV wheel + brightness for carpets) ───
export const VISUAL_COLOR_PICKER_SV_SIZE_PX = 180;
export const VISUAL_COLOR_PICKER_HUE_WIDTH_PX = 20;
export const VISUAL_COLOR_PICKER_MARKER_RADIUS_PX = 6;
/**
 * The hue bar gradient is intrinsic to the color-picking interaction, not a
 * theme color — it must span the full hue circle. Centralized here so the
 * component body stays free of inline color literals. (The saturation/brightness
 * square is painted to a canvas from the carpet HSL model, not a CSS gradient.)
 */
export const VISUAL_COLOR_PICKER_HUE_GRADIENT =
  'linear-gradient(to bottom, ' +
  '#ff0000 0%, #ffff00 16.7%, #00ff00 33.3%, ' +
  '#00ffff 50%, #0000ff 66.7%, #ff00ff 83.3%, #ff0000 100%)';
export const VISUAL_COLOR_PICKER_MARKER_BORDER = '2px solid #fff';
export const VISUAL_COLOR_PICKER_MARKER_SHADOW = '0 0 0 1px rgba(0,0,0,0.6)';
/** Width of the collapsed swatch + hex trigger row (compact mode). */
export const VISUAL_COLOR_PICKER_COMPACT_WIDTH_PX = 160;
/** Swatch square size shown in the collapsed trigger. */
export const VISUAL_COLOR_PICKER_SWATCH_PX = 22;
/** Gap (px) between the collapsed trigger and the expanded popup panel. */
export const VISUAL_COLOR_PICKER_POPUP_GAP_PX = 6;

// ── Notification Sound (done: ascending chime) ─────────────
export const NOTIFICATION_NOTE_1_HZ = 659.25; // E5
export const NOTIFICATION_NOTE_2_HZ = 1318.51; // E6 (octave up)
export const NOTIFICATION_NOTE_1_START_SEC = 0;
export const NOTIFICATION_NOTE_2_START_SEC = 0.1;
export const NOTIFICATION_NOTE_DURATION_SEC = 0.18;
export const NOTIFICATION_VOLUME = 0.14;

// ── Permission Sound (attention: descending double tap) ────
export const PERMISSION_NOTE_1_HZ = 880; // A5
export const PERMISSION_NOTE_2_HZ = 659.25; // E5 (down a fourth)
export const PERMISSION_NOTE_1_START_SEC = 0;
export const PERMISSION_NOTE_2_START_SEC = 0.12;
export const PERMISSION_NOTE_DURATION_SEC = 0.15;
export const PERMISSION_VOLUME = 0.12;

// ── Furniture Animation ─────────────────────────────────────
export const FURNITURE_ANIM_INTERVAL_SEC = 0.2;

// ── Version Notice ──────────────────────────────────────────
export const WHATS_NEW_AUTO_CLOSE_MS = 20000;
export const WHATS_NEW_FADE_MS = 1000;

// ── Game Logic ───────────────────────────────────────────────
export const MAX_DELTA_TIME_SEC = 0.1;
/** The game loop's tick-rate bound (frameStep.ts): visible time earns one tick (simulation AND render)
 *  per this interval, held at most SIM_TICK_BURST, so in any T seconds there are at most
 *  T / 0.015 + 2 ticks (about 66.7 per second sustained) on ANY display. A 60 Hz display still ticks
 *  every frame; a faster one is held to that rate. Per-tick budgets (path searches, updates) are
 *  therefore per-second bounds. */
export const SIM_MIN_TICK_INTERVAL_SEC = 0.015;
/** The most tick credit the loop may hold (see SIM_MIN_TICK_INTERVAL_SEC). */
export const SIM_TICK_BURST = 2;
export const WAITING_BUBBLE_DURATION_SEC = 2.0;
export const DISMISS_BUBBLE_FAST_FADE_SEC = 0.3;
export const INACTIVE_SEAT_TIMER_MIN_SEC = 3.0;
export const INACTIVE_SEAT_TIMER_RANGE_SEC = 2.0;
/** Default/fallback palette count (bundled characters). Actual count comes from getLoadedCharacterCount(). */
export const PALETTE_COUNT = 6;
export const AUTO_ON_FACING_DEPTH = 3;
export const AUTO_ON_SIDE_DEPTH = 2;
export const CHARACTER_HIT_HALF_WIDTH = 8;
export const CHARACTER_HIT_HEIGHT = 24;
export const TOOL_OVERLAY_VERTICAL_OFFSET = 32;

// ── Greeter + Intro bubble ──────────────────────────────────
/** Reserved character id for the Intro's greeter. Far outside both real agent
 *  ids (positive) and sub-agent ids (small negatives from -1 down). */
export const GREETER_ID = -1_000_000_000;
/** Stacking order for the Intro's bubble. Deliberately BELOW the modal stack
 *  (ui/Modal defaults to 50, ChangelogModal 51, the migration notice z-100) and
 *  ABOVE the covering workspace (44) and the open mission stage (43): the Intro
 *  is diegetic furniture over the office, not a modal, so a modal opened on top
 *  of it must cover it rather than slide underneath. */
export const INTRO_BUBBLE_Z_INDEX = 46;
/** The greeter stands this many tiles in from the office's bottom-left corner
 *  (target tile (margin, rows-1-margin); nearest walkable tile if blocked). */
export const GREETER_TILE_MARGIN = 3;
/** World px above the greeter's anchor (feet) where the bubble's bottom sits.
 *  Kept well above the head target (INTRO_TAIL_TARGET_RISE_WORLD) so the
 *  tail squares have a visible run between bubble and head. */
export const INTRO_BUBBLE_ANCHOR_RISE_WORLD = 44;
/** World px right of the greeter's center where the bubble's left edge starts —
 *  just clear of the sprite so the tail points down-left at the head. */
export const INTRO_BUBBLE_OFFSET_X_WORLD = 10;
/** Bubble width cap (CSS px) and the margin kept from the container edges.
 *  Wide on purpose: the disclosure reads as three short paragraphs instead of
 *  a tall column (still clamped to the container on narrow panels). */
export const INTRO_BUBBLE_MAX_WIDTH_PX = 560;
export const INTRO_BUBBLE_EDGE_MARGIN_PX = 4;
/** Where the Intro's Claude Code step sends people who don't have it yet. */
export const CLAUDE_CODE_URL = 'https://claude.com/claude-code';
export const CLAUDE_CODE_INSTALL_COMMAND = 'npm install -g @anthropic-ai/claude-code';
/** Speech-tail squares: placed at fraction `t` along the segment from the
 *  bubble's nearest edge point to the greeter's head, shrinking toward the
 *  speaker. Recomputed every frame so the tail stays connected no matter where
 *  edge-clamping or panning puts the bubble relative to the character. */
export const INTRO_TAIL_STEPS = [
  { t: 0.25, size: 12 },
  { t: 0.55, size: 9 },
  { t: 0.82, size: 6 },
] as const;
/** World px above the greeter's anchor (feet) the tail points at — the head. */
export const INTRO_TAIL_TARGET_RISE_WORLD = 26;
/** Camera-offset caps while centering character + bubble. The ideal composition
 *  assumes the bubble fits beside/above the character; when it can't (narrow or
 *  short viewports clamp the bubble to the screen), uncapped offsets shove the
 *  greeter to the viewport edge. Horizontal offset is capped to this fraction
 *  of the viewport; vertical offset always keeps this many world px of the
 *  character visible above the bottom edge. */
export const INTRO_CAMERA_MAX_X_OFFSET_VIEWPORT_FRACTION = 0.25;
export const INTRO_CAMERA_MIN_CHAR_VISIBLE_WORLD = 48;
/** Extra downward camera shift (CSS px) so the character+bubble composition
 *  sits a bit above the vertical center instead of dead-centered. */
export const INTRO_CAMERA_DOWN_SHIFT_PX = 50;

// ── Context Fuel Gauge ──────────────────────────────────────
/** Window assumed before the runtime reports one (it always does for agents
 *  that have taken a turn; this only covers characters created ahead of it). */
export const DEFAULT_MAX_CONTEXT_TOKENS = 200_000;
export const CONTEXT_WARN_THRESHOLD = 0.6;
export const CONTEXT_DANGER_THRESHOLD = 0.8;
export const CONTEXT_CRITICAL_THRESHOLD = 0.95;
export const CONTEXT_GAUGE_WIDTH_PX = 40;
export const CONTEXT_GAUGE_HEIGHT_PX = 4;
export const CONTEXT_GAUGE_COLOR_OK = '#44cc44';
export const CONTEXT_GAUGE_COLOR_WARN = '#ffcc00';
export const CONTEXT_GAUGE_COLOR_DANGER = '#ff8800';
export const CONTEXT_GAUGE_COLOR_CRITICAL = '#ff2222';
export const CONTEXT_GAUGE_BG = '#222';

// ── Agent Teams ─────────────────────────────────────────────
export const TEAM_LEAD_COLOR = '#ffd700';
export const TEAM_ROLE_COLOR = '#66aaff';

// ── Pets ────────────────────────────────────────────────────────
/** Walking speed in world pixels per second (matches character walk speed visually but slower). */
export const PET_WALK_SPEED_PX_PER_SEC = 32;
/** Time per WALK animation cycle step (4 cycle steps × 0.15s = 0.6s per loop). */
export const PET_WALK_FRAME_DURATION_SEC = 0.15;
/** Time per IDLE animation cycle step (4 cycle steps × 0.3s = 1.2s per loop). */
export const PET_IDLE_FRAME_DURATION_SEC = 0.3;
/** Walk cycle: 4-step lookup into the 3-frame walkDown/walkUp/walkRight arrays. */
export const PET_WALK_SEQUENCE = [0, 1, 0, 2] as const;
/** Idle cycle: 4-step lookup into the 3-frame idleDown/idleUp arrays. */
export const PET_IDLE_SEQUENCE = [0, 1, 2, 1] as const;
/** Minimum seconds the pet stays in IDLE before making a new decision. */
export const PET_WANDER_PAUSE_MIN_SEC = 3.0;
/** Maximum seconds the pet stays in IDLE before making a new decision. */
export const PET_WANDER_PAUSE_MAX_SEC = 15.0;
/** Seconds between FOLLOW path re-computations. */
export const PET_FOLLOW_RECALC_INTERVAL_SEC = 1.0;
/** Probability that a pet enters FOLLOW (instead of WALK) when wanderTimer expires. */
export const PET_FOLLOW_CHANCE = 0.3;
/** Maximum Manhattan distance (tiles) at which a character can become a follow target. */
export const PET_FOLLOW_RADIUS_TILES = 3;
/** Minimum seconds a FOLLOW episode lasts before timing out. */
export const PET_FOLLOW_DURATION_MIN_SEC = 5.0;
/** Maximum seconds a FOLLOW episode lasts before timing out. */
export const PET_FOLLOW_DURATION_MAX_SEC = 15.0;
/** Hit-box half-width (world px) for pet click detection. */
export const PET_HIT_HALF_WIDTH = 8;
/** Hit-box height (world px) measured upward from the bottom-center anchor. */
export const PET_HIT_HEIGHT = 16;
/** Zoom factor used to draw pet thumbnails in the EditorToolbar Pets tab. */
export const PET_THUMB_ZOOM = 2;
/** Scale margin so the pet thumbnail fills the ItemSelect cell without touching the edges. */
export const PET_THUMB_SCALE_MARGIN = 0.85;
/** Fallback background fill for sprite-less thumbnail (used while pet sprites are loading). */
export const EMPTY_SPRITE_THUMBNAIL_BG = '#333';
/** Maximum string length for a PlacedPet.id (defends against pathologically-long layout entries). */
export const MAX_PET_ID_LENGTH = 128;

// ── DI reception (physical desk + decorative receptionist) ───
/** The shipped worlds' existing reception desk: the lobby desk every DI preset carries under this
 *  uid (palace-relative (23, 9) in expanded-office, vast-forest, metropolis and living-metropolis). */
export const DI_RECEPTION_DESK_UID = 'expansion-148';
/** The furniture type the reception desk must still be: a recycled uid on another type is no desk. */
export const DI_RECEPTION_DESK_TYPE = 'DESK_FRONT';
/** Reserved id of the decorative receptionist. Outside real agent ids (positive), sub-agent ids
 *  (small negatives) and the greeter's, so no agent, glow or seat loop can ever address it. */
export const DI_RECEPTIONIST_ID = -1_000_000_001;
/** The receptionist's character palette (0-5): the receptionist job's own palette. */
export const DI_RECEPTIONIST_PALETTE = 3;
/** The shipped worlds' reception bench: the one chair that faces the reception desk. It is the receptionist's
 *  workstation, so it is never a seat a real agent can take (alongside any seat whose facing tile is on the desk). */
export const DI_RECEPTION_BENCH_UID = 'expansion-150';
/** The receptionist's fixed, documented accessory (no new art: drawn from code at render time). It is drawn only on the
 *  receptionist (`ch.isReceptionist`), never on a DI agent: a headset band over the hair and a name badge on the chest. */
export const DI_RECEPTIONIST_HEADSET_COLOR = '#2b2b3a';
export const DI_RECEPTIONIST_BADGE_COLOR = '#f2c94c';
export const DI_RECEPTIONIST_BADGE_EDGE_COLOR = '#7a5a12';

// ── DI three-office seating (P2-C) ──────────────────────────
/**
 * Office districts of a large world with an explicit home: the labels `ambientJobs.DISTRICT_JOBS` gives its office
 * rows (a drift test pins that every pattern here matches such a row). `short` is the quick-view button label.
 */
export const DI_OFFICE_DISTRICT_PATTERNS: ReadonlyArray<{ match: RegExp; short: string }> = [
  { match: /atrium/i, short: 'Atrium' },
  { match: /observatory/i, short: 'Observatory' },
];
/**
 * A district is a working office only if it holds at least this many DESK-FACING, reachable seats (a chair adjacent to and facing a desk): its
 * workstations and its table seats together. Despite the name it does NOT count tier-1 workstations (desk AND electronics) alone, so which districts
 * are offices is the same as before the workstation tiers existed.
 */
export const DI_OFFICE_MIN_WORKSTATIONS = 12;
/** At most this many offices (the home plus detected districts) are ever selected, whatever the layout holds. */
export const DI_OFFICE_MAX_OFFICES = 8;
/** The label of the home office in allocation and ambient places (the home rect is the Palace). */
export const DI_HOME_OFFICE_LABEL = 'Palace';
/** At most this many residents of an office may STAND (dwell) inside its work rooms at a sample; the re-measurement is judged against it. */
export const DI_OFFICE_WORK_ROOM_RESIDENT_TARGET = 2;
/**
 * C5, detected OFFICE pools only: of an office's ROAMING residents (taken in roster order) only every Nth makes the one cross-world visit to another
 * place; the others keep to the office's common rooms. Every roaming resident used to make that visit, and that one long walk dominates a loop
 * (measured: 66-88% of a roamer's time is spent outside its own office rooms, from one far stop in five), so the offices emptied. N = 4 is the smallest
 * N at which every detected office's time-averaged PHYSICAL presence in its non-work rooms is at least S4's physical presence in the same office
 * (work + non-work rooms), with 0 and with 24 agents (121 samples over 1200 simulated s; 24 agents against the mean of 12 wander seeds). Measured, new
 * 0/24 agents against S4's 2.71 / 5.15 (Palace), 3.72 / 3.83 (Atrium), 3.66 / 3.66 (Observatory): N=1 Palace 2.98 (everyone visits: short), N=2 4.48
 * and N=3 5.11 (short), N=4 Palace 5.35 / Atrium 5.15 / Observatory 4.45 (met), N=5 Palace 5.04 (short again: which roamer visits changes its walk
 * length), N=6 5.24. `ambientOfficePresence.test.ts` pins the rule and re-measures the table. Every other place keeps its routines exactly (the remote
 * S4 golden is byte-equal).
 */
export const AMBIENT_OFFICE_VISIT_EVERY = 4;

// ── DI office panel ─────────────────────────────────────────
/**
 * Width of the docked DI office sidebar; the office canvas narrows by this while it is open. 428 (pass C, C-V1): MEASURED from
 * the locked Home (reconciled-free-ui-v3/01-home.jpg, 1280 wide) to the panel's own EDGE RULE at x 852 (1280 - 852 = 428).
 * NOT to where the dark fill begins at x 847: those 5 px are the fill's lead-in, and measuring them would read 433. Was 420.
 */
export const DI_PANEL_WIDTH_PX = 428;
/**
 * Width of the DI navigation rail at the window's LEFT edge (the scan face only: 01-home.jpg draws it 200 px wide in a 1280 px
 * frame). The world yields this width while the office is open, so the rail covers nothing of it, and the Mission stage starts at
 * its edge. At a 900 px window the scan face's world is 900 - 428 - 200 = 272, which keeps its controls offered against the scan
 * face's own threshold (DI_WORLD_SCAN_CONTROLS_MIN_PX, 240), not the pixel toolbar's 276.
 */
export const DI_RAIL_WIDTH_PX = 200;
/** How often the DI panel re-reads the canvas selection (a local UI read only). */
export const DI_SELECTION_POLL_MS = 250;
/**
 * Width of the edge strip that holds the restore control of every minimized surface (AC3), from the approved
 * prototype's collapsed-panel strip (44px). The world yields this width while the strip shows, so nothing is
 * covered by it.
 */
export const DI_MINIMIZED_STRIP_WIDTH_PX = 44;

/**
 * The smallest world width at which the world's controls are offered. It is the toolbar's right edge as MEASURED in the rendered App (the
 * toolbar occupies 10 to 276 px of the world, so a narrower world would clip it). It is also exactly the world that exists at 320 after the
 * office is minimized (320 minus the 44 px strip, which the office's own restore control occupies), the narrowest supported case: a larger value
 * would make the controls unreachable there by construction (diPanelLayout.test.ts guards that structurally). That the controls really are drawn
 * whole and reachable by the real keyboard at this width is proved by the rendered tests in diWorldYield.test.ts, not by this comment. Below it
 * the controls are removed from the tab order, the pointer and the screen together (WorldControlsGate).
 */
export const DI_WORLD_CONTROLS_MIN_PX = 276;
/**
 * The SCAN face's own threshold for offering the world's controls (pass C, decision (B)): its world chrome is converged to Home
 * tokens and Inter (item 5) and is NARROWER than the pixel toolbar that sets DI_WORLD_CONTROLS_MIN_PX (276), so gating it by
 * 276 would gate new chrome by old chrome's width. MEASURED as rendered on the production build (fixture viewer, reception-v2,
 * 1440 x 900, the disclosure open; logs/measure-scan-world-chrome-1440-20261027T005429Z.json): its widest pieces end 230.7 px
 * into the world (the ambient legend; it wraps at the column cap as its counts grow) and 230 px (the FIXTURE label, capped
 * at 220 wide); the Layout / City / Settings bar ends at 219.8 and the view buttons at 110.5. 240 = 230.7 plus about 9 px of
 * headroom. Applied ONLY where the rail is (diDockFor railPx > 0, the scan face with the office open); the operational face,
 * which keeps the pixel toolbar, keeps 276. Below it the controls are REMOVED whole, never clipped (WorldControlsGate), and
 * that they are whole and non-overlapping at 900 is proved rendered in diScanFace.test.ts, not by this comment.
 */
export const DI_WORLD_SCAN_CONTROLS_MIN_PX = 240;

/** The bottom-left column's distance from the world's left edge (WorldStack). */
export const DI_WORLD_COLUMN_LEFT_PX = 10;
/**
 * The widest the bottom-left column (toolbar, fixture label, ambient legend, City popover) may be. It ends where the world's controls end
 * (DI_WORLD_CONTROLS_MIN_PX, the toolbar's right edge), so the toolbar, which is as wide as this, sets it and nothing in the column can reach further:
 * the legend's text depends on live counts and WRAPS rather than growing past it. The request workspace keeps exactly this much world beside it
 * (MR_WORLD_KEEP_PX), so the column is never painted over the workspace's content (a composer label cut, Send's edge covered: measured at 1600 wide in
 * wider reading before this, in the Ask tab with a draft scrolled to the end).
 */
export const DI_WORLD_COLUMN_MAX_PX = DI_WORLD_CONTROLS_MIN_PX - DI_WORLD_COLUMN_LEFT_PX;

/**
 * Below this world width the corner furniture on the RIGHT cannot keep its usual bottom-right places without meeting the bottom-left column (WorldStack: the
 * toolbar runs to 276 px, the fixture label, legend and popover are up to 220 to 320 px wide, and the update notice and version label sit 28 px in from the right
 * edge): the version label (34 px wide) would sit on the toolbar's right end below 338 px, and the update notice (about 171 px wide) would meet the label and the
 * legend below about 475 px. In a world this narrow both move into the TOP-RIGHT band (index.css), which nothing else in the world uses there (the docked request
 * workspace needs a world of at least 636 px beside the office: MR_MIN_BESIDE_PX plus MR_WORLD_KEEP_PX, so it never coexists with a world this narrow); wider worlds are unchanged. Measured, and held by the rendered checks
 * in diWorldYield.test.ts and diFixtureLabel.test.ts.
 */
export const DI_WORLD_ROOMY_PX = 480;

/** How often the client re-checks its own invalidation (transport, valid_until) for the DI office. */
export const DI_CLIENT_CHECK_MS = 1000;
/** Longest Mission title (characters, ellipsis included); the full objective stays in the inspector. */
export const DI_MISSION_TITLE_MAX_CHARS = 90;
/** A Mission's native id is shown whole up to this length, else as its shortest unique prefix. */
export const DI_SHORT_REF_FULL_MAX_CHARS = 24;
/** The shortest prefix a shortened native id keeps (the office zone label's length). */
export const DI_SHORT_REF_MIN_CHARS = 11;
/** Browser tab title of the DI office (DI mode only; the ordinary surfaces keep theirs). */
export const DI_DOCUMENT_TITLE = 'Pixel Agents · DI office';
/** DI office favicon: an ORIGINAL code-native 16x16 pixel mark ("DI" on the panel colour),
 *  authored in-repo under the project's MIT licence. No external or bundled image file. */
/** The OS preference the DI office follows for REDUCED MOTION (live; it can flip mid-session). */
export const DI_REDUCED_MOTION_QUERY = '(prefers-reduced-motion: reduce)';

export const DI_FAVICON_DATA_URI =
  'data:image/svg+xml,' +
  encodeURIComponent(
    "<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 16 16' shape-rendering='crispEdges'>" +
      "<rect width='16' height='16' fill='#1e1e2e'/>" +
      "<g fill='#a6e3a1'>" +
      "<rect x='2' y='3' width='1' height='10'/><rect x='3' y='3' width='2' height='1'/>" +
      "<rect x='3' y='12' width='2' height='1'/><rect x='5' y='4' width='1' height='8'/>" +
      "<rect x='8' y='3' width='5' height='1'/><rect x='10' y='4' width='1' height='8'/>" +
      "<rect x='8' y='12' width='5' height='1'/>" +
      '</g></svg>',
  );

// ── DI responsive office world ──────────────────────────────
/**
 * Structural fingerprint (layoutFingerprint) of the bundled default layout. ONLY a layout
 * with this fingerprint is "the default" and may be reshaped to the viewport; any custom
 * layout keeps its exact cols, rows and furniture. Pinned by test against the asset file.
 */
export const DI_DEFAULT_LAYOUT_FINGERPRINT = 'fnv1a32:709c2051';
/** Rows above the first workstation band: the hall's top wall and a walkway. */
export const DI_HALL_HEAD_ROWS = 2;
/** Rows per workstation band: desk (2), benches (1), aisle (1). */
export const DI_BAND_ROWS = 4;
/** First pod column and the column pitch between pods within a band. */
export const DI_POD_FIRST_COL = 2;
export const DI_POD_PITCH = 4;
/** Mission zones are HALF-BANDS ("slots"): small Missions share a band instead of each forcing a
 *  whole new one (at DPR 1 an extra band can cost a whole integer zoom step). */
export const DI_SLOTS_PER_BAND = 2;
/** Seats per pod: two benches under each desk. */
export const DI_POD_SEATS = 2;
/** Existing catalog furniture a pod is built from (the default room's own workstation kit). */
export const DI_POD_DESK = 'DESK_FRONT';
export const DI_POD_PC = 'PC_FRONT_OFF';
export const DI_POD_SEAT = 'CUSHIONED_BENCH';
/** Mission zone tints (Areas overlay), assigned by zone order. */
export const DI_ZONE_COLORS = [
  '#4e8cff',
  '#ff9f43',
  '#2ecc71',
  '#e056fd',
  '#f9ca24',
  '#22a6b3',
  '#eb4d4b',
  '#a29bfe',
];
/** Debounce for re-fitting the DI world after the main area resizes. */
export const DI_WORLD_REFLOW_MS = 150;
/**
 * Row cap for the DI DEFAULT world's workstation bands — deliberately the former 64-row
 * ceiling, NOT MAX_ROWS: raising the world ceiling for large custom worlds must not multiply
 * the default office's band capacity.
 */
export const DI_WORLD_MAX_ROWS = LEGACY_WORLD_MAX_DIM;

// ── Ambient residents (DI mode; decorative only) ─────────────────────────────
// Ambient residents are a LOCAL, DECORATIVE simulation that runs only while the viewer is open.
// They are never agents: they are not in OfficeState.characters, never take a seat, never
// receive real-agent status styling, and carry no Mission, activity or authority meaning.
/** Fixed world seed: the same world always yields the same roster, posts and routines. */
export const AMBIENT_WORLD_SEED = 0x5eedd1a9;
/** Ordinary population target (enforced: the roster never exceeds it unless raised below the cap). */
export const AMBIENT_POPULATION_ORDINARY = 140;
/** District share of the population grows with walkable area to this power (sub-linear). */
export const AMBIENT_DISTRICT_WEIGHT_EXPONENT = 0.7;
/** Hard population cap: no roster, however requested, holds more residents than this. */
export const AMBIENT_POPULATION_CAP = 200;
/** Every resident is animated, so the animated-sprite cap equals the hard population cap. */
export const AMBIENT_ANIMATED_CAP = AMBIENT_POPULATION_CAP;
/** Bounded A*: nodes a single path search may expand before it gives up. */
export const AMBIENT_PATH_MAX_EXPANSIONS = 6_000;
/** Longest path (tiles) a resident will walk in one leg; longer results are refused. */
export const AMBIENT_PATH_MAX_LENGTH = 220;
/** Path searches allowed per simulation tick across ALL residents; the rest wait their turn. */
export const AMBIENT_PATH_SEARCHES_PER_TICK = 4;
/** Pedestrian graph: one hub per HUB_SPACING x HUB_SPACING block with walkable floor. */
export const AMBIENT_HUB_SPACING = 12;
/** Legs up to this Manhattan distance try bounded A* first; longer ones route on the graph. */
export const AMBIENT_LOCAL_ROUTE_RADIUS = 48;
/** Longest graph route (tiles) a resident will walk in one leg. */
export const AMBIENT_ROUTE_MAX_LENGTH = 1_400;
/** Minimum residents in the original palace and on the deck (C1: inhabited at zero agents). */
export const AMBIENT_PALACE_MIN = 10;
export const AMBIENT_DECK_MIN = 4;
/** Congestion: a resident blocked by another resident waits, then passes through (no gridlock). */
export const AMBIENT_CONGESTION_PASS_SEC = 2.5;
/** A resident blocked by a REAL AGENT never passes through; it re-plans after this long. */
export const AMBIENT_AGENT_BLOCK_REPLAN_SEC = 0.8;
/**
 * The receptionist's stand tile is a FIXED obstacle: a leg that would cross it is detoured around it (a bounded local search with the tile avoided),
 * or fails so the resident moves on to its next stop. The journey PLAN (routines, stops, rides) is never changed by this. A detour that needs more
 * than this many expansions or this many steps (or a leg that crosses the tile more than this many times) is a failed leg, not a long walk.
 */
export const AMBIENT_POST_DETOUR_MAX_EXPANSIONS = 600;
export const AMBIENT_POST_DETOUR_MAX_LENGTH = 40;
export const AMBIENT_POST_DETOUR_MAX_CROSSINGS = 4;
/** Yield search radius (tiles) when a resident must clear an agent's tile or assigned seat. */
export const AMBIENT_YIELD_RADIUS = 3;
/**
 * WORKING-OFFICE VISIBLE OCCUPANCY. A pool that holds a seat (the Palace, a Mission's room, the legacy
 * single Office) shows at most min(AMBIENT_WORKING_OFFICE_ON_DUTY, max(1, ceil(T /
 * AMBIENT_WORKING_OFFICE_TILES_PER_RESIDENT))) residents ON DUTY at once, where T is the pool's
 * RESIDENT-ELIGIBLE tiles exactly as `addPool` builds them (`pool.tiles.length`): walkable, on the main
 * pedestrian network, NOT a seat tile or a seat tile's walkable neighbour, and belonging to that place. T is
 * not the room's or the place's area. A small floor therefore shows fewer than a large office. The rest are
 * OFF DUTY: derived (never saved), frozen exactly where and as they are, not drawn, not hoverable, not an
 * obstacle, and every id, job, routine, ride and the saved-state fingerprint stay as they were. A pool
 * already at or under its bound is untouched. This value is the CAP (what a large office shows); it was
 * chosen from the BUILT expanded-office pictures. There is no floor: thinning the Palace is permitted; what
 * must be preserved is every identity, job, journey, transit state and supported save.
 */
export const AMBIENT_WORKING_OFFICE_ON_DUTY = 16;
/**
 * Resident-eligible pool tiles (`pool.tiles.length`, see AMBIENT_WORKING_OFFICE_ON_DUTY) per resident on
 * duty: a pool of T such tiles shows at most ceil(T / this) residents, never fewer than one and never more
 * than AMBIENT_WORKING_OFFICE_ON_DUTY. Chosen from the BUILT default-office comparison at normal zoom, 8 on
 * duty (14) against 4 (34): at 8 about five residents still stood in a knot above the working table; at 4
 * every seated agent was plainly visible with a few spaced residents keeping the room alive. In the shipped
 * worlds: the default office's Mission pool has T = 105, so 4 are on duty; expanded-office's Office pool has
 * T = 718, so ceil(718 / 34) = 22, capped to 16; the Palace has T = 2183, so ceil(2183 / 34) = 65, capped to
 * 16, which is above its 11 residents, so all 11 stay on duty and it is unchanged. (The tests pin these.)
 */
export const AMBIENT_WORKING_OFFICE_TILES_PER_RESIDENT = 34;
/**
 * Seconds between hand-overs in one working office: one resident steps off duty at its next dwell
 * boundary and the next in the roster steps on. Chosen from the built moving frames only; there was no
 * separate study of the cadence.
 */
export const AMBIENT_DUTY_ROTATION_SEC = 6;
/** Cached leg paths per resident (one per routine leg); the cache is dropped with the roster. */
export const AMBIENT_LEG_CACHE_PER_RESIDENT = 6;
/** Routine stops per resident (its post plus patrol stops). */
// ── Transit (M3 S3; decorative subway) ─────────────────
/** Train cruise speed and acceleration/braking, in tiles. */
export const TRANSIT_CRUISE_TILES_PER_SEC = 5;
export const TRANSIT_ACCEL_TILES_PER_SEC2 = 1.6;
/** Seconds a train stands at a platform; seconds a terminus crossover takes. */
export const TRANSIT_DWELL_SEC = 7;
export const TRANSIT_CROSSOVER_SEC = 2.5;
/** Passenger-car animation frame interval (frames A and B) while moving. */
export const TRANSIT_FRAME_SEC = 0.22;
/** Bounded transit event log (arrivals, departures, boardings, alightings, crossovers). */
export const TRANSIT_EVENT_LOG_MAX = 200;
/** Residents who ride the subway (a bounded subset of the open-ground places near the line). Every one of them
 *  can be aboard at once and stay within the saved-snapshot bound on riders aboard (AMBIENT_SAVE_MAX_ABOARD). */
export const AMBIENT_RIDERS_MAX = 24;
/** A resident rides when its post is within this Manhattan distance (tiles) of a station platform (or it lives at a
 *  station, or on the Grand or Market Boulevard), and its place is open ground. */
export const AMBIENT_RIDER_REACH_TILES = 96;
/** The share of riders per journey kind, out of the sum of these: a commute (to a station at least two along the line
 *  when there is one), a hop (the next station) and an excursion (three stations and back). */
export const AMBIENT_RIDER_KIND_WEIGHTS = { commute: 5, hop: 3, excursion: 2 } as const;
/** Dwell profiles: a POSTED job holds its post this many times longer than an ordinary stop, a ROAMING job lingers
 *  this fraction as long at every stop (so some residents are fixtures of a place and others keep moving). */
export const AMBIENT_POST_DWELL_FACTOR = 2.5;
export const AMBIENT_ROAMING_DWELL_FACTOR = 0.6;
/** Hidden-view resume catch-up: at most this many seconds are simulated, in fixed steps. */
export const AMBIENT_CATCHUP_MAX_SEC = 20;
export const AMBIENT_CATCHUP_STEP_SEC = 0.1;
/** How often the DI office saves the decorative ambient snapshot while visible (also on hide). */
export const AMBIENT_SAVE_INTERVAL_MS = 30_000;
/** A rider gives up waiting and walks after this long. */
export const AMBIENT_RIDE_WAIT_MAX_SEC = 150;
export const AMBIENT_ROUTINE_STOPS = 4;
/**
 * HARD bound on one routine's length (post, patrol stops, a sibling stop, a cross-world stop and
 * room-coverage stops). Room coverage never exceeds it; rooms it would leave uncovered are COUNTED
 * (AmbientWorld.coverage) rather than silently dropped.
 */
export const AMBIENT_ROUTINE_MAX_STOPS = 10;
/** Patrol stops are drawn within this Manhattan radius of the post. */
export const AMBIENT_PATROL_RADIUS_TILES = 28;
/** Dwell at a stop, seconds (seeded per resident). */
export const AMBIENT_DWELL_MIN_SEC = 2.5;
export const AMBIENT_DWELL_MAX_SEC = 9.0;
/** Walk speed and walk-cycle frame duration. */
export const AMBIENT_WALK_SPEED_PX_PER_SEC = 36;
export const AMBIENT_WALK_FRAME_DURATION_SEC = 0.16;
/** A resident whose next path search failed retries after this long. */
export const AMBIENT_PATH_RETRY_SEC = 4.0;
/** Job badge: a small coloured mark above the head (world pixels). */
export const AMBIENT_BADGE_W = 4;
export const AMBIENT_BADGE_H = 2;
export const AMBIENT_BADGE_OFFSET_Y = 27;
/** Badge colours by job id (canvas fill). */
export const AMBIENT_JOB_COLORS: Readonly<Record<string, string>> = {
  bellhop: '#d4a017',
  police: '#2f5bd3',
  firefighter: '#e03a2f',
  'store-clerk': '#2fa84f',
  barista: '#8b5a2b',
  courier: '#f08c2a',
  'street-sweeper': '#9aa0a6',
  gardener: '#5fbf3f',
  farmhand: '#c9b458',
  'dock-worker': '#3a7ca5',
  angler: '#4fb3bf',
  aquarist: '#29c4d6',
  ranger: '#4b7f3a',
  jogger: '#e85fa8',
  tourist: '#b98be0',
  receptionist: '#c06090',
  custodian: '#7a6a5a',
  'office-visitor': '#60a0e0',
  verger: '#806040',
  'cathedral-visitor': '#b090d0',
  shopper: '#e07040',
  'egg-pocket-creature': '#f2d33c',
  'egg-shinobi': '#f07a24',
  'egg-martial-artist': '#e8a020',
  'egg-cul-de-sac-kid': '#5aa0e0',
  'egg-trickster-hare': '#b8b8c8',
};
/** Standing residents drawn from a sheet step through idle, idle, gesture at this cadence. */
export const AMBIENT_IDLE_FRAME_SEC = 0.7;
/** Hover label for a resident (canvas). */
export const AMBIENT_LABEL_BG = 'rgba(20, 20, 32, 0.88)';
export const AMBIENT_LABEL_FG = '#e8e8f0';
export const AMBIENT_LABEL_FONT_PX = 9;

// ── Living city: lighting and weather (decorative only; living city 6–9) ──────
// Every value below is a presentation constant of the DECORATIVE city. None of it reaches a Mission, an
// agent, an observation or anything canonical, and none of it comes from a location or a weather service.

/** The darkness laid over the OUTDOORS at each lighting stage (r, g, b 0-255; a = opacity at that stage).
 *  Day is the city exactly as it always looked: no layer is drawn at all. */
export const CITY_LIGHT_STAGE_DAY = { r: 0, g: 0, b: 0, a: 0 } as const;
export const CITY_LIGHT_STAGE_DUSK = { r: 74, g: 38, b: 66, a: 0.3 } as const;
export const CITY_LIGHT_STAGE_NIGHT = { r: 8, g: 14, b: 46, a: 0.58 } as const;
/** The darkness level of each STILL stage (0 = day, the middle = dusk, 1 = night). */
export const CITY_LIGHT_LEVEL_DUSK = 0.5;
export const CITY_LIGHT_LEVEL_NIGHT = 1;
/** Below this level no layer is drawn (it could not be seen). */
export const CITY_LIGHT_VISIBLE_EPSILON = 0.01;
/** Seconds for a change of stage to settle (instant under reduced motion). */
export const CITY_LIGHT_FADE_SEC = 0.8;
/** The part of the darkness removed over INDOOR tiles: offices keep their lights on. */
export const CITY_LIGHT_INDOOR_LIT = 0.85;
/** Light pools punched out of the darkness: real agents (and sub-agents) and street lamps. */
export const CITY_LIGHT_AGENT_RADIUS_TILES = 3;
export const CITY_LIGHT_LAMP_RADIUS_TILES = 4;
export const CITY_LIGHT_AGENT_STRENGTH = 0.95;
export const CITY_LIGHT_LAMP_STRENGTH = 0.9;
/** Hard cap on lights drawn in one frame (agents first, then the lamps in view). */
export const CITY_LIGHT_MAX_LIGHTS = 96;
/** A real agent's light sits this many sprite pixels above its position (torso, not feet). */
export const CITY_LIGHT_AGENT_LIFT_PX = 4;
/** Lights further than this many tiles outside the view are not drawn at all. */
export const CITY_LIGHT_VIEW_MARGIN_TILES = 5;
/** Resolution of the shared soft-circle sprite every light is drawn from. */
export const CITY_LIGHT_SPRITE_PX = 64;
/** The warm glow added around a lamp, and its strength at full night. */
export const CITY_LIGHT_LAMP_GLOW = { r: 255, g: 196, b: 110 } as const;
export const CITY_LIGHT_LAMP_GLOW_ALPHA = 0.22;
/** Furniture that gives light, and where its light sits relative to its footprint (in tiles). */
export const CITY_LIGHT_SOURCE_TYPES: readonly string[] = [
  'DETAIL_LAMP_ORNATE',
  'DETAIL_LAMP_MODERN',
  'DECK_LANTERN',
];
/** Local-clock schedule, in hours: night until DAWN_START, dawn ramps to day by DAY_START, day until
 *  DUSK_START, dusk ramps to night by NIGHT_START. The clock mode is a function of the clock, nothing more. */
export const CITY_CLOCK_DAWN_START_H = 5;
export const CITY_CLOCK_DAY_START_H = 7;
export const CITY_CLOCK_DUSK_START_H = 17;
export const CITY_CLOCK_NIGHT_START_H = 21;

/** Weather particles. Screen-space, seeded, a fixed number per kind: never a forecast. */
export const CITY_WEATHER_SEED = 0x2c17;
export const CITY_RAIN_PARTICLES = 520;
/** How often the City panel re-reads the lighting status words (Clock mode follows the clock), milliseconds. */
export const CITY_STATUS_REFRESH_MS = 1_000;
export const CITY_SNOW_PARTICLES = 300;
/** Fall speed in screen heights per second, and sideways drift in screen widths per second. */
export const CITY_RAIN_SPEED = 1.1;
export const CITY_RAIN_DRIFT = 0.12;
export const CITY_SNOW_SPEED = 0.07;
export const CITY_SNOW_SWAY = 0.015;
/** Rain streak length and snowflake size, in sprite pixels (multiplied by the zoom when drawn). */
export const CITY_RAIN_LENGTH_PX = 6;
export const CITY_SNOW_SIZE_PX = 1;
export const CITY_RAIN_COLOR = 'rgba(184, 214, 255, 0.72)';
export const CITY_SNOW_COLOR = 'rgba(245, 248, 255, 0.9)';
/** The faint cool veil laid over the OUTDOORS while it rains or snows, so even a still frame (reduced motion)
 *  reads as weather. Indoors is punched out of it like the night darkness; it draws nothing for clear skies. */
export const CITY_OVERCAST_RAIN = { r: 62, g: 78, b: 104, a: 0.2 } as const;
export const CITY_OVERCAST_SNOW = { r: 120, g: 134, b: 160, a: 0.14 } as const;
/** A canvas fillStyle from channels (the colour literal stays in this file, like the matrix greens above). */
export const cityRgba = (r: number, g: number, b: number, a: number): string =>
  `rgba(${r}, ${g}, ${b}, ${a})`;

// ── Living city: seasons and regional weather (decorative only; item 18, milestone 1) ──────────────────────
// Every value below is a presentation constant of the DECORATIVE city. None of it reaches a Mission, an agent, an
// observation or anything canonical, and none of it is a forecast, a location or a calendar.

/** Automatic seasons: visible running time per season, in milliseconds (30 minutes; two hours for the year). */
export const CITY_SEASON_INTERVAL_MS = 1_800_000;
/** The longest game-loop step, in seconds, the season and weather clocks will take. A longer gap (a stalled frame, a tab that
 *  was hidden) is clipped to this: it is never turned into catch-up. */
export const CITY_CLOCK_MAX_STEP_SEC = 0.25;
/** The calm cross-fade between two seasons, in milliseconds (reduced motion swaps at once). */
export const CITY_SEASON_FADE_MS = 3_000;
/** Cap on each season recolour cache (recoloured sprites, and the per-colour memos): cleared whole when full, rebuilt on use. */
export const CITY_SEASON_CACHE_MAX_ENTRIES = 2_048;

/** Weather regions: the fixed number of region slots, and how a layout WITHOUT district labels is divided: a world at least
 *  CITY_REGION_BAND_MIN_COLS wide is split into CITY_REGION_FALLBACK_BANDS equal bands west to east; a narrower one is one region. */
export const CITY_REGION_SLOTS = 5;
export const CITY_REGION_FALLBACK_BANDS = 3;
export const CITY_REGION_BAND_MIN_COLS = 96;
/** The width, in tiles, of the soft edge between two regions: a tile within this many tiles of a foreign region blends its weather veil and
 *  its particle strength with that region's, by distance. World-anchored and deterministic (a function of the region map alone). */
export const CITY_REGION_EDGE_TILES = 4;

/** Automatic regional weather: each region re-draws its weather after a seeded interval drawn uniformly from this closed range
 *  (milliseconds of visible running time: two to five minutes), and fades between two kinds over CITY_WEATHER_FADE_MS. */
export const CITY_WEATHER_INTERVAL_MIN_MS = 120_000;
export const CITY_WEATHER_INTERVAL_MAX_MS = 300_000;
export const CITY_WEATHER_FADE_MS = 4_000;
/** The most CONSECUTIVE non-clear automatic draws one region may make. The draw after that many is clear whatever the roll, so continuous
 *  weather in a region lasts at most CITY_WEATHER_MAX_WET_DRAWS intervals: 2 x 5 minutes = 10 minutes, a third of one season. */
export const CITY_WEATHER_MAX_WET_DRAWS = 2;
/** The default seed of the automatic schedule (a draw is a pure function of seed, region slot and that slot's step count). */
export const CITY_WEATHER_SCHEDULE_SEED = 0x51a7;

/** Heavy snow: its own, denser field (two depth layers, larger flakes, faster and wider-swaying fall) and a stronger cool veil. */
export const CITY_HEAVY_SNOW_PARTICLES = 520;
/**
 * THE PARTICLE POOL. The live particles of ALL three fields together (rain, light snow, heavy snow) never exceed this, which is the
 * load budget's own bound (CITY_RAIN_PARTICLES): each kind has a capacity no larger than it, and when several kinds are shown at once they
 * share the pool in proportion to the regions showing them. A share moves at most CITY_WEATHER_BALANCE_RATE particles a second, and a
 * field only grows into the room the others have left, so the total is within the cap at every moment, a transition included.
 * THE REMAINDER RULE: each share is floor(cap x regions showing the kind / regions showing any kind), limited to the kind's own capacity,
 * and nothing is redistributed. So with n kinds shown and no capacity binding, cap - (n - 1) <= total <= cap: 520 for one kind, 260 + 260
 * for two equal kinds, 173 x 3 = 519 for three equal kinds. (cityRegionWeather.ts `entitlements` implements it; the tests pin it by hand.)
 */
export const CITY_WEATHER_PARTICLE_TOTAL = 520;
export const CITY_WEATHER_BALANCE_RATE = 200;
/** A light-snow flake is laid on a small cool shadow, one SCREEN pixel wider on every side, so it reads on white ground and on dark forest alike
 *  (heavy snow has larger flakes and a denser veil). The shadow is laid only where all of it is over open ground. */
export const CITY_SNOW_HALO_COLOR = 'rgba(70, 92, 138, 0.5)';
export const CITY_SNOW_HALO_PX = 1;
/** A light-snow flake is drawn this many times its sprite-pixel size at full depth, times the zoom and its depth (it was 1.4 before the shadow was added, which left a flake
 *  a single white pixel in a ring at ordinary zoom); heavy snow keeps its own, larger flakes. */
export const CITY_SNOW_FLAKE_SCALE = 3;
export const CITY_HEAVY_SNOW_SPEED = 0.16;
export const CITY_HEAVY_SNOW_SWAY = 0.03;
export const CITY_HEAVY_SNOW_SIZE_PX = 2;
/** Depth at or above which a heavy-snow flake is on the NEAR layer and is drawn half as large again. */
export const CITY_HEAVY_SNOW_NEAR_DEPTH = 0.75;
export const CITY_OVERCAST_HEAVY_SNOW = { r: 150, g: 164, b: 190, a: 0.26 } as const;

/** Saving the city (contract v2): the poll that decides whether a save is due, the least gap between two saves that were NOT a viewer's own
 *  choice, and the bounded checkpoint rate (time left moves every frame, so a running clock is saved at most this often). A choice saves at
 *  once; a transition is saved at the next poll after the gap; a hidden view is saved once. NEVER per frame. */
export const CITY_SAVE_POLL_MS = 1_000;
export const CITY_SAVE_MIN_GAP_MS = 2_000;
export const CITY_SAVE_CHECKPOINT_MS = 60_000;
/** A region's automatic draw index wraps to 0 at this value (the saved record bounds it at one less; see core/src/cityPresentation.ts). */
export const CITY_WEATHER_STEP_LIMIT = 1_000_000;

/** TEST SEAM ONLY (testHooks.ts `advanceCityClock`, never a control and never a default): the most seconds of visible running time one
 *  accelerated call may move the city's season and weather clocks by (four hours). Captures that use it say "accelerated test clock". */
export const CITY_TEST_CLOCK_MAX_SECONDS = 14_400;

/** Season recolours (first pass; code-native, applied to outdoor ground and outdoor foliage only). A tone moves a pixel's hue
 *  toward `hueTo` by `hueBlend`, multiplies its saturation by `satMul` (never above `satMax`), and its lightness by `lightMul`
 *  then adds `lightAdd`. Foliage pixels are split into a GREEN band (hue 60-175) and a WARM band (the rest, trunks excluded). */
export interface CitySeasonTone {
  hueTo: number;
  hueBlend: number;
  satMul: number;
  satMax: number;
  lightMul: number;
  lightAdd: number;
}
export const CITY_SEASON_GREEN_HUE_MIN = 60;
export const CITY_SEASON_GREEN_HUE_MAX = 175;
/** A pixel at or below this saturation is a grey (rock, shadow, trunk shade) and is left alone by the foliage tones. */
export const CITY_SEASON_GREY_SATURATION = 0.1;
/**
 * Winter snow covers the ground, and a WARM district wash (the Areas overlay: the Autumn Wildwood's orange, the Metropolis's tan) lying over it would
 * turn it rose-beige and leave a warm/cool seam beside the cool Highlands. In winter that wash, over meadow and forest-floor tiles only, is drawn in
 * this cool neutral instead (see winterAreaWash). Cool, green and grey washes, paving, roads and every other season are left exactly as they are.
 */
export const CITY_WINTER_AREA_WASH_COLOR = '#B8C4D2';
/** A dark, brown-ish pixel (lightness below this, hue 10-50) is a trunk or soil and is left alone by the foliage tones. */
export const CITY_SEASON_TRUNK_LIGHTNESS = 0.32;
const SAME = { hueTo: 0, hueBlend: 0, satMul: 1, satMax: 1, lightMul: 1, lightAdd: 0 } as const;
export const CITY_SEASON_TONES = {
  // meadow, forest floor. WINTER keeps hueBlend at 1 (the frost hue exactly): a PARTIAL blend of a warm hue toward blue runs the short way
  // round the wheel, through magenta, and paints brown ground mauve (the first pass did). The same rule holds for every winter tone below:
  // hueBlend is 0 (keep the source hue, only desaturate and frost it) or 1 (impose the hue), never in between.
  ground: {
    spring: {
      green: {
        hueTo: 108,
        hueBlend: 0.5,
        satMul: 1.12,
        satMax: 0.8,
        lightMul: 1.1,
        lightAdd: 0.07,
      },
      warm: SAME,
    },
    // later autumn on warm ground (leaf litter: darker, redder brown; an olive clearing turns tan)
    autumn: {
      green: { hueTo: 38, hueBlend: 0.8, satMul: 1.05, satMax: 0.62, lightMul: 1, lightAdd: 0.03 },
      warm: { hueTo: 32, hueBlend: 0.45, satMul: 1.12, satMax: 0.6, lightMul: 0.9, lightAdd: 0 },
    },
    winter: {
      green: { hueTo: 212, hueBlend: 1, satMul: 0.3, satMax: 0.12, lightMul: 0.42, lightAdd: 0.66 },
      warm: { hueTo: 212, hueBlend: 1, satMul: 0.3, satMax: 0.12, lightMul: 0.42, lightAdd: 0.66 },
    },
  },
  // dirt trail, ploughed soil, sand. In winter a trail is beaten, frosted earth: it keeps its warm hue, greyed, darker than the snow beside it.
  soil: {
    spring: {
      green: SAME,
      warm: { hueTo: 28, hueBlend: 0.1, satMul: 1.05, satMax: 0.6, lightMul: 0.96, lightAdd: 0 },
    },
    autumn: {
      green: SAME,
      warm: { hueTo: 24, hueBlend: 0.2, satMul: 1.1, satMax: 0.6, lightMul: 0.97, lightAdd: 0 },
    },
    winter: {
      green: { hueTo: 30, hueBlend: 0, satMul: 0.3, satMax: 0.16, lightMul: 0.55, lightAdd: 0.36 },
      warm: { hueTo: 30, hueBlend: 0, satMul: 0.3, satMax: 0.16, lightMul: 0.55, lightAdd: 0.36 },
    },
  },
  // pavement, cobbles, planks: a light autumn warmth, and only a light frost in winter (the hue is kept: brown planks stay brown, not lilac)
  hard: {
    spring: { green: SAME, warm: SAME },
    autumn: {
      green: { hueTo: 30, hueBlend: 0.12, satMul: 1.1, satMax: 0.5, lightMul: 0.98, lightAdd: 0 },
      warm: { hueTo: 30, hueBlend: 0.12, satMul: 1.1, satMax: 0.5, lightMul: 0.98, lightAdd: 0 },
    },
    winter: {
      green: { hueTo: 30, hueBlend: 0, satMul: 0.5, satMax: 0.3, lightMul: 1, lightAdd: 0.12 },
      warm: { hueTo: 30, hueBlend: 0, satMul: 0.5, satMax: 0.3, lightMul: 1, lightAdd: 0.12 },
    },
  },
  // deciduous trees, bushes, ferns, flowers, reeds, apple trees
  canopy: {
    spring: {
      green: {
        hueTo: 112,
        hueBlend: 0.3,
        satMul: 1.12,
        satMax: 0.85,
        lightMul: 1.06,
        lightAdd: 0.05,
      },
      warm: {
        hueTo: 104,
        hueBlend: 0.85,
        satMul: 0.85,
        satMax: 0.7,
        lightMul: 1.1,
        lightAdd: 0.08,
      },
    },
    // already-autumn foliage (the Autumn Wildwood's authored palette) turns later-autumn: browner and duller
    autumn: {
      green: {
        hueTo: 30,
        hueBlend: 0.85,
        satMul: 1.1,
        satMax: 0.78,
        lightMul: 1.05,
        lightAdd: 0.02,
      },
      warm: { hueTo: 28, hueBlend: 0.45, satMul: 0.62, satMax: 0.7, lightMul: 0.86, lightAdd: 0 },
    },
    // bare, frosted trees: a green canopy goes dusty blue-green (darker than the snow so it reads), warm foliage goes taupe, never lilac
    winter: {
      green: {
        hueTo: 204,
        hueBlend: 0.7,
        satMul: 0.3,
        satMax: 0.22,
        lightMul: 0.72,
        lightAdd: 0.2,
      },
      warm: { hueTo: 30, hueBlend: 0, satMul: 0.32, satMax: 0.28, lightMul: 0.92, lightAdd: 0.05 },
    },
  },
  // evergreens: a fresher green in spring, dulled in autumn, snow-frosted in winter
  conifer: {
    spring: {
      green: {
        hueTo: 112,
        hueBlend: 0.25,
        satMul: 1.12,
        satMax: 0.8,
        lightMul: 1.1,
        lightAdd: 0.05,
      },
      warm: SAME,
    },
    autumn: {
      green: { hueTo: 80, hueBlend: 0.2, satMul: 0.92, satMax: 0.7, lightMul: 0.94, lightAdd: 0 },
      warm: SAME,
    },
    winter: {
      green: {
        hueTo: 192,
        hueBlend: 0.4,
        satMul: 0.45,
        satMax: 0.3,
        lightMul: 0.75,
        lightAdd: 0.2,
      },
      warm: SAME,
    },
  },
  // crop rows and standing crops (the field floor and the wheat, corn and pumpkins on it): fresh shoots, harvest gold, pale olive stubble
  crop: {
    spring: {
      green: {
        hueTo: 100,
        hueBlend: 0.45,
        satMul: 1.1,
        satMax: 0.75,
        lightMul: 1.06,
        lightAdd: 0.04,
      },
      warm: {
        hueTo: 100,
        hueBlend: 0.45,
        satMul: 1.1,
        satMax: 0.75,
        lightMul: 1.06,
        lightAdd: 0.04,
      },
    },
    autumn: {
      green: { hueTo: 40, hueBlend: 0.3, satMul: 1.1, satMax: 0.75, lightMul: 1, lightAdd: 0 },
      warm: { hueTo: 40, hueBlend: 0.3, satMul: 1.1, satMax: 0.75, lightMul: 1, lightAdd: 0 },
    },
    winter: {
      green: { hueTo: 66, hueBlend: 1, satMul: 0.5, satMax: 0.26, lightMul: 0.5, lightAdd: 0.42 },
      warm: { hueTo: 66, hueBlend: 1, satMul: 0.5, satMax: 0.26, lightMul: 0.5, lightAdd: 0.42 },
    },
  },
} as const;

// ── Living city: traffic (decorative only; living city 6–9, row 16) ───────────
// The moving traffic is the world's OWN lane-aligned vehicle furniture brought to life. Every value below is a
// presentation constant of the DECORATIVE city: none of it reaches a Mission, an agent, an observation or
// anything canonical, no vehicle has any authority (an ambulance is an ordinary car that happens to look like
// one), and real agents and pedestrians always have priority.

export const TRAFFIC_SEED = 0x7a11c;
/** Hard cap on vehicle slots ever created (the shipped world animates 70). */
export const TRAFFIC_MAX_VEHICLES = 96;
/** The vehicles allowed at the traffic LOW level (the rest stay parked, drawn as the furniture they are). */
export const TRAFFIC_CAP_LOW = 24;
/** Hard bounds on the derived road graph: free lane segments, and intersection boxes. */
export const TRAFFIC_MAX_LANES = 256;
export const TRAFFIC_MAX_BOXES = 64;
/** A free lane segment shorter than this is never driven on or turned into. */
export const TRAFFIC_LANE_MIN_TILES = 6;
/** Tiles a vehicle holds along its lane: its own sprite's length when travelling east or west (the art runs from
 *  2 tiles, a hatchback, to 4, the fire engine). Nothing outside this range is animated. */
export const TRAFFIC_BODY_MIN_TILES = 2;
export const TRAFFIC_BODY_MAX_TILES = 4;
/** A vehicle's rolling route never holds more than this many tiles, and is extended when fewer than the lookahead remain. */
export const TRAFFIC_ROUTE_MAX_TILES = 96;
export const TRAFFIC_ROUTE_LOOKAHEAD_TILES = 8;
/** Tiles claimed ahead of the head, so a vehicle can brake in time. */
export const TRAFFIC_CLAIM_AHEAD_TILES = 2;
/** Cruise speed range (tiles per second, per vehicle, seeded), acceleration and braking (tiles per second squared). */
export const TRAFFIC_SPEED_MIN = 2.8;
export const TRAFFIC_SPEED_MAX = 4.4;
export const TRAFFIC_ACCEL = 5;
export const TRAFFIC_BRAKE = 9;
/** Every approach to an intersection or a crossing stops this long (plus up to the jitter) before it may enter. */
export const TRAFFIC_STOP_DWELL_SEC = 0.7;
export const TRAFFIC_STOP_JITTER_SEC = 0.6;
/** At the end of a free lane segment a vehicle waits this long, fades, and re-enters at the segment's start. */
export const TRAFFIC_END_DWELL_SEC = 1;
export const TRAFFIC_FADE_SEC = 0.5;
export const TRAFFIC_RESPAWN_SEC = 1.5;
/** A vehicle waiting this long without being able to move is removed and re-enters (a gridlock valve). */
export const TRAFFIC_STUCK_SEC = 30;
/** Relative weights of straight on, turning right and turning left at an intersection. */
export const TRAFFIC_TURN_WEIGHT_STRAIGHT = 6;
export const TRAFFIC_TURN_WEIGHT_RIGHT = 3;
export const TRAFFIC_TURN_WEIGHT_LEFT = 1;
/** Pedestrian priority: a vehicle never claims a tile a pedestrian stands on or is about to reach (their next
 *  path tiles), and never one adjacent to a real agent. */
export const TRAFFIC_PEDESTRIAN_LOOKAHEAD_TILES = 3;
export const TRAFFIC_AGENT_HALO_TILES = 1;
/** Vehicles start at staggered times within this many seconds, so they do not move in lockstep. */
export const TRAFFIC_START_STAGGER_SEC = 2;
/** The words every vehicle carries when hovered: it is decoration, whatever it looks like. */
export const TRAFFIC_LABEL = 'Decorative traffic';
