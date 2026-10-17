/**
 * OFFICE LAYOUT BOUNDS — the ONE definition, shared by every path a layout enters through:
 * the DI server's inbound saveLayout, its layout file reads (handshake, file watcher), and the
 * webview's layoutLoaded handler, file import and deserializer. The numbers live here only;
 * server/src/constants.ts and webview-ui/src/constants.ts re-export them, never copy them.
 *
 * STRUCTURAL CAPS: each axis 1..LAYOUT_MAX_DIM (384) AND a TOTAL-CELL cap LAYOUT_MAX_CELLS that is
 * INDEPENDENT of the axis cap — a DIM x DIM grid (147,456 cells) is NOT admitted, so every per-cell
 * byte and allocation is bounded by the cell cap, not by DIM squared. HISTORY: r4 set the cell cap at
 * 81,920 for the connected metropolis (352 x 224 = 78,848 cells, holding the preserved 176 x 112
 * forest); M3 S2 raised it to 110,400 for the living metropolis (352 x 304 = 107,008 cells). Furniture 16,384; pets 64; areas 64; ids/labels 128 chars.
 * RENDER CAPS (one layout's renderer cache entries and per-change build work; below): carpeted
 * cells 4,096; distinct colour variants 1,024 tile / 256 carpet / 1,024 furniture.
 *
 * Two byte ceilings in two UNITS. THE FIGURES IN THIS PARAGRAPH AND THE NEAR-CAP COSTS BELOW ARE
 * r4's MEASUREMENTS AT THE FORMER 81,920-CELL CAP; they have NOT been re-measured at 110,400 cells
 * and are not living-world measurements (re-measurement at the raised cap is owed to M3 S7). r4
 * measured them (scripts/measure-layout-bounds.ts) on a MAXIMAL legitimate layout CONSTRUCTED at the
 * caps simultaneously — 320 x 256 = 81,920 cells (the former cell cap; 384 x 213 within 0.2%), every cell coloured at the editor's widest HSBC spelling
 * and in an area whose label is 128 ASCII characters; LAYOUT_MAX_CARPET_CELLS cells carpeted with
 * colour + accent + order; 16,384 coloured furniture with uid AND type at 128 characters and
 * positions at the bound; 64 pets and 64 areas with ids/labels at 128 characters; EXACTLY AT the
 * three distinct colour-variant caps:
 *   - a COMPACT JSON frame (what the client sends over /ws): 22,457,974 B -> 64 MiB (2.99x);
 *   - a PRETTY saved FILE (writeLayoutToFile uses JSON.stringify(layout, null, 2), and the
 *     office's Export does the same): 30,157,721 B -> 104 MiB (3.62x).
 *   (The shipped 352 x 224 grid at the same density: 21,862,006 B / 29,362,073 B.) The ceilings
 *   were set at ~2x this maximum before the carpeted-cell cap shrank it; they are kept, since a
 *   layout grown up to them is still admitted and its costs are measured below.
 * Those maxima are ASCII. Ids and labels are bounded in CHARACTERS, and a label is stored PER CELL
 * in areaTiles, and colour components are any finite number up to the bound, so a STRUCTURALLY
 * valid layout can be far larger: the structural worst case at the caps (every number at its
 * longest JSON spelling, every label/id of characters JSON escapes to six bytes) measures
 * 104,489,891 B compact / 112,189,638 B pretty. So admission ALSO measures the two byte sizes
 * (checkLayoutBytes, UTF-8, allocation-free counting): a layout is admitted only if its compact
 * save frame and its pretty file both fit — anything admitted can be transported AND saved AND read
 * back, and the structural worst case is REFUSED. The structural bounds still cap allocation before
 * anything is serialized.
 * NEAR-CAP COST (r4, at the FORMER cap; not re-measured at 110,400), measured on this repository's
 * dev Mac (Apple M5 Pro, Node 26.5.0, load average ~5), for the LARGEST layout admission then accepted
 * (81,920 cells at every cap, grown to a 67,108,851 B
 * frame / 74,808,598 B file — 13 B under the frame ceiling): bounded file read + parse + legacy
 * admission 424 ms / 517 MB peak RSS; admission alone 297 ms; migration 2 ms; save frame + pretty
 * file 237 ms / 573 MB peak RSS; refusing the structural worst case 436 ms / 659 MB
 * (.herd evidence measurements/bounds-final). These are SERVER stage costs; the browser-side cost
 * of an admitted layout is measured separately (the render workload, measurements/proofs-*).
 *
 * Validation ORDER, for legacy support: reject oversized BYTES before reading or parsing; then
 * check the raw layout in LEGACY mode (bounds and shapes, tolerating what migration repairs: a
 * missing or wrong-length tileColors); then migrate; then check the result in STRICT mode
 * (exact parallel-array lengths) before it is applied, saved or sent.
 *
 * THE INVARIANT (review round 2, F4): a layout is admissible only if the representation the
 * CONSUMER will actually hold is admissible. Migration can GROW a layout (it generates tileColors
 * and pets when they are missing), so measuring only the input form would admit a layout whose
 * migrated form the webview then refuses — after it had been saved. admitLayout therefore checks
 * the given form AND, whenever migration changes anything, the migrated form (the same
 * layoutMigration.ts function the webview runs). Callers still persist the form they were given.
 */

import type { MigratableLayout } from './layoutMigration.js';
import { migrateLayout } from './layoutMigration.js';

/** Grid bound PER AXIS: cols and rows each 1..LAYOUT_MAX_DIM (the editor's MAX_COLS / MAX_ROWS). */
export const LAYOUT_MAX_DIM = 384;
/**
 * TOTAL-CELL bound, INDEPENDENT of LAYOUT_MAX_DIM (not DIM x DIM): cols x rows must also fit
 * here, so each axis stays flexible while the largest admitted grid — and with it every per-cell
 * byte and every per-cell allocation — stays near the shape actually shipped (the living
 * metropolis, 352 x 304 = 107,008 cells; r4 shipped 352 x 224 = 78,848 under the former 81,920).
 * 368 x 300 and 345 x 320 sit exactly at it with both axes inside 384. It is also the length
 * tolerance for a non-strict (legacy) parallel array: nothing longer than the largest admissible
 * grid is ever accepted.
 */
export const LAYOUT_MAX_CELLS = 110_400;
export const LAYOUT_MAX_FURNITURE = 16_384;
export const LAYOUT_MAX_PETS = 64;
export const LAYOUT_MAX_AREAS = 64;
/** Max length of a uid, type id, area label or pet id. */
export const LAYOUT_ID_MAX_LENGTH = 128;
/**
 * DISTINCT COLOUR VARIANTS — bounded because each one is a cache entry the renderer CREATES on
 * first draw and KEEPS (colorize.ts: a colorized sprite, plus a canvas per zoom in spriteCache):
 * the uncapped render-workload probe (scripts/measure-layout-bounds.ts `render <out> uncapped`:
 * a distinct colour on every one of LAYOUT_MAX_CELLS cells and on every item) made the page stop
 * responding — after the office and whole-world views, a click on Map could not complete within
 * 30 s (bundle index-BGgilKHw.js, 2026-10-16). With every kind AT these caps the page stays
 * responsive, and once the caches are warm the variant count no longer changes the per-frame cost
 * (ablation E, measurements/ablation-dpr2-*). Counted per kind: tile (floor value, colour) pairs,
 * carpet (variant, colour, accent) triples, furniture (type, colour) pairs — countRenderVariants
 * below reports them with exactly these keys. The connected world (metropolis.json) uses
 * 209 / 10 / 73 and the preserved forest 65 / 0 / 73, so both are admitted. These are NEW limits:
 * the imported forest-era limits had no variant caps, and a layout with more distinct variants
 * than these is refused now. They bound ONE layout; what the renderer keeps across successive
 * loads and imports is bounded by its cache lifecycle (webview office/renderCaches.ts).
 */
export const LAYOUT_MAX_TILE_COLOUR_VARIANTS = 1_024;
export const LAYOUT_MAX_CARPET_COLOUR_VARIANTS = 256;
export const LAYOUT_MAX_FURNITURE_COLOUR_VARIANTS = 1_024;
/**
 * CARPETED CELLS — carpets are drawn per tile CORNER (a junction resolves up to four neighbouring
 * carpets). MEASURED in the DPR 2 whole-world view of the capped render workload, BEFORE the
 * pre-composed carpet layer: every cell carpeted 72 frames / 10 s, no carpets 345, and 4,096
 * heterogeneous carpets in view 179 (measurements/ablation-dpr2-*). The webview now composes the
 * carpets once into one layer (renderer.ts), so coverage no longer costs per frame — at this cap,
 * all in view, 355 frames / 10 s (measurements/proofs-index-BmWYA8kI) — and the cap bounds what
 * remains: the one-time plan and layer build after a carpet change (at the cap, drawing every
 * junction directly took 8-20 ms per full view in the equivalence receipt).
 * COMPATIBILITY, stated narrowly: the verified forest input carpets no cell and the connected
 * world 940, so both are admitted. This is a NEW limit — the imported forest-era limits (192 per
 * axis, no carpet cap) admitted layouts with more carpeted cells, and those are refused now.
 */
export const LAYOUT_MAX_CARPET_CELLS = 4_096;
const MIB = 1_048_576;
/** Compact JSON frame ceiling (the DI /ws inbound payload bound). */
export const LAYOUT_MAX_FRAME_BYTES = 64 * MIB;
/** Pretty-printed layout FILE ceiling (saved layout.json, exported / imported files). */
export const LAYOUT_MAX_FILE_BYTES = 104 * MIB;
/** Furniture may sit partly outside the grid (wall items extend upward); bound it loosely. */
export const LAYOUT_POSITION_BOUND = LAYOUT_MAX_DIM * 2;
/** Colour sliders and hue shifts are small numbers; anything beyond is not editor output. */
export const LAYOUT_COLOR_COMPONENT_BOUND = 1_000;
const TILE_VALUE_MAX = 255;
const LAYOUT_REVISION_MAX = 1_000_000;
const CARPET_ORDER_BOUND = 1_000_000;

const LAYOUT_KEYS = new Set([
  'version',
  'cols',
  'rows',
  'tiles',
  'furniture',
  'tileColors',
  'layoutRevision',
  'pets',
  'carpetTiles',
  'areas',
  'areaTiles',
  'home',
]);
/** The explicit HOME region (worldViews.officeRegion): a tile rectangle inside the grid. */
const HOME_KEYS = new Set(['c0', 'r0', 'c1', 'r1']);
const FURNITURE_KEYS = new Set(['uid', 'type', 'col', 'row', 'color']);
const COLOR_KEYS = new Set(['h', 's', 'b', 'c', 'colorize']);
const CARPET_KEYS = new Set(['variant', 'color', 'accentColor', 'order']);
const PET_KEYS = new Set(['id', 'petType']);
const AREA_KEYS = new Set(['label', 'color']);
const AREA_COLOR = /^#[0-9a-fA-F]{6}$/;

type Obj = Record<string, unknown>;

function isObj(value: unknown): value is Obj {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}
function onlyKeys(value: Obj, allowed: Set<string>): boolean {
  return Object.keys(value).every((k) => allowed.has(k));
}
function isInt(value: unknown, min: number, max: number): value is number {
  return typeof value === 'number' && Number.isInteger(value) && value >= min && value <= max;
}
export function isBoundedId(value: unknown, maxLength = LAYOUT_ID_MAX_LENGTH): value is string {
  return typeof value === 'string' && value.length > 0 && value.length <= maxLength;
}
export function isLayoutColor(value: unknown): boolean {
  if (!isObj(value) || !onlyKeys(value, COLOR_KEYS)) return false;
  for (const k of ['h', 's', 'b', 'c']) {
    const v = value[k];
    if (typeof v !== 'number' || !Number.isFinite(v) || Math.abs(v) > LAYOUT_COLOR_COMPONENT_BOUND)
      return false;
  }
  return value.colorize === undefined || typeof value.colorize === 'boolean';
}
function parallelArray(
  value: unknown,
  length: number,
  exact: boolean,
  item: (v: unknown) => boolean,
): boolean {
  if (!Array.isArray(value)) return false;
  if (exact ? value.length !== length : value.length > LAYOUT_MAX_CELLS) return false;
  return value.every(item);
}

export interface LayoutCheckOptions {
  /**
   * LEGACY (pre-migration) mode: a raw layout from disk, the wire or a file, before
   * migrateLayout. Tolerates only what migration repairs — tileColors absent or of the wrong
   * length (it is regenerated) — while every bound, key set and value shape still applies.
   * Default: STRICT (exact parallel-array lengths), for a migrated layout.
   */
  legacy?: boolean;
}

/**
 * Is THIS representation of `layout` an OfficeLayout v1 within the bounds? Returns null when valid,
 * else the reason. Structure first (O(cells), no allocation — safe before anything is built from
 * the input), then, only for a structurally bounded layout, its UTF-8 byte sizes against both
 * ceilings. Does NOT consider migration: use checkLayout / admitLayout for admission.
 */
export function checkLayoutForm(layout: unknown, options: LayoutCheckOptions = {}): string | null {
  const strict = !options.legacy;
  if (!isObj(layout)) return 'layout is not an object';
  if (!onlyKeys(layout, LAYOUT_KEYS)) return 'layout has unknown fields';
  if (layout.version !== 1) return 'layout version is not 1';
  if (!isInt(layout.cols, 1, LAYOUT_MAX_DIM) || !isInt(layout.rows, 1, LAYOUT_MAX_DIM))
    return `layout cols/rows outside 1..${LAYOUT_MAX_DIM}`;
  const cells = layout.cols * layout.rows;
  if (cells > LAYOUT_MAX_CELLS)
    return `layout cols x rows is ${cells} cells, over ${LAYOUT_MAX_CELLS}`;
  if (!parallelArray(layout.tiles, cells, true, (t) => isInt(t, 0, TILE_VALUE_MAX)))
    return 'layout tiles do not match cols x rows';
  const furniture = layout.furniture;
  if (!Array.isArray(furniture) || furniture.length > LAYOUT_MAX_FURNITURE)
    return 'layout furniture missing or too long';
  for (const item of furniture) {
    if (
      !isObj(item) ||
      !onlyKeys(item, FURNITURE_KEYS) ||
      !isBoundedId(item.uid) ||
      !isBoundedId(item.type) ||
      !isInt(item.col, -LAYOUT_POSITION_BOUND, LAYOUT_POSITION_BOUND) ||
      !isInt(item.row, -LAYOUT_POSITION_BOUND, LAYOUT_POSITION_BOUND) ||
      (item.color !== undefined && !isLayoutColor(item.color))
    )
      return 'layout furniture item malformed';
  }
  if (
    layout.tileColors !== undefined &&
    !parallelArray(layout.tileColors, cells, strict, (c) => c === null || isLayoutColor(c))
  )
    return 'layout tileColors malformed';
  if (layout.layoutRevision !== undefined && !isInt(layout.layoutRevision, 0, LAYOUT_REVISION_MAX))
    return 'layout layoutRevision malformed';
  if (layout.pets !== undefined) {
    const pets = layout.pets;
    if (
      !Array.isArray(pets) ||
      pets.length > LAYOUT_MAX_PETS ||
      !pets.every(
        (p) =>
          isObj(p) &&
          onlyKeys(p, PET_KEYS) &&
          isBoundedId(p.id) &&
          isInt(p.petType, 0, TILE_VALUE_MAX),
      )
    )
      return 'layout pets malformed';
  }
  if (
    layout.carpetTiles !== undefined &&
    !parallelArray(
      layout.carpetTiles,
      cells,
      true,
      (c) =>
        c === null ||
        (isObj(c) &&
          onlyKeys(c, CARPET_KEYS) &&
          isInt(c.variant, 0, TILE_VALUE_MAX) &&
          (c.color === undefined || isLayoutColor(c.color)) &&
          (c.accentColor === undefined || isLayoutColor(c.accentColor)) &&
          (c.order === undefined || isInt(c.order, -CARPET_ORDER_BOUND, CARPET_ORDER_BOUND))),
    )
  )
    return 'layout carpetTiles malformed';
  const labels = new Set<string>();
  if (layout.areas !== undefined) {
    const areas = layout.areas;
    if (!Array.isArray(areas) || areas.length > LAYOUT_MAX_AREAS) return 'layout areas malformed';
    for (const a of areas) {
      if (
        !isObj(a) ||
        !onlyKeys(a, AREA_KEYS) ||
        !isBoundedId(a.label) ||
        typeof a.color !== 'string' ||
        !AREA_COLOR.test(a.color) ||
        labels.has(a.label)
      )
        return 'layout area malformed';
      labels.add(a.label);
    }
  }
  if (
    layout.areaTiles !== undefined &&
    !parallelArray(
      layout.areaTiles,
      cells,
      true,
      (t) => t === null || (typeof t === 'string' && labels.has(t)),
    )
  )
    return 'layout areaTiles malformed';
  if (layout.home !== undefined) {
    const h = layout.home;
    if (
      !isObj(h) ||
      !onlyKeys(h, HOME_KEYS) ||
      !isInt(h.c0, 0, layout.cols - 1) ||
      !isInt(h.r0, 0, layout.rows - 1) ||
      !isInt(h.c1, h.c0 + 1, layout.cols) ||
      !isInt(h.r1, h.r0 + 1, layout.rows)
    )
      return 'layout home malformed';
  }
  const colourVariants = checkColourVariants(layout);
  if (colourVariants !== null) return colourVariants;
  // Structure is within bounds (so serializing it is bounded too): now the bytes it would take.
  return checkLayoutBytes(layout);
}

const colourKey = (c: Obj): string =>
  `${c.h as number},${c.s as number},${c.b as number},${c.c as number},${c.colorize === true ? 1 : 0}`;

/** The variant KEYS — the one definition of what counts as a distinct variant, used by the check
 *  below and by countRenderVariants (so a reported count can never use different semantics). */
const tileVariantKey = (tile: number, c: Obj): string => `${tile}|${colourKey(c)}`;
const carpetVariantKey = (c: Obj): string =>
  `${c.variant as number}|${c.color ? colourKey(c.color as Obj) : '-'}|${c.accentColor ? colourKey(c.accentColor as Obj) : '-'}`;
const furnitureVariantKey = (f: Obj): string => `${f.type as string}|${colourKey(f.color as Obj)}`;

/**
 * The render-cache figures of a layout, counted with EXACTLY the admission check's keys (for
 * reports and evidence; the check itself stops at the first cap exceeded). Assumes a layout that
 * passed checkLayoutForm.
 */
export function countRenderVariants(layout: Record<string, unknown>): {
  tileColourVariants: number;
  carpetedCells: number;
  carpetColourVariants: number;
  furnitureColourVariants: number;
} {
  const tiles = layout.tiles as number[];
  const tileColors = (layout.tileColors as Array<Obj | null> | undefined) ?? [];
  const carpets = (layout.carpetTiles as Array<Obj | null> | undefined) ?? [];
  const tile = new Set<string>();
  tileColors.forEach((c, i) => c && tile.add(tileVariantKey(tiles[i], c)));
  const carpet = new Set<string>();
  let carpetedCells = 0;
  for (const c of carpets)
    if (c) {
      carpetedCells++;
      carpet.add(carpetVariantKey(c));
    }
  const furniture = new Set<string>();
  for (const f of layout.furniture as Obj[]) if (f.color) furniture.add(furnitureVariantKey(f));
  return {
    tileColourVariants: tile.size,
    carpetedCells,
    carpetColourVariants: carpet.size,
    furnitureColourVariants: furniture.size,
  };
}

/** The render-cache half of the structure check: carpeted cells (LAYOUT_MAX_CARPET_CELLS) and
 *  distinct colour variants (LAYOUT_MAX_*_COLOUR_VARIANTS).
 *  Runs only on a layout already structurally bounded, so its work is O(cells + furniture). */
function checkColourVariants(layout: Obj): string | null {
  const tiles = layout.tiles as number[];
  const tileColors = layout.tileColors as Array<Obj | null> | undefined;
  if (tileColors) {
    const seen = new Set<string>();
    for (let i = 0; i < tileColors.length; i++) {
      const c = tileColors[i];
      if (!c) continue;
      seen.add(tileVariantKey(tiles[i], c));
      if (seen.size > LAYOUT_MAX_TILE_COLOUR_VARIANTS)
        return `layout has over ${LAYOUT_MAX_TILE_COLOUR_VARIANTS} distinct tile colour variants`;
    }
  }
  const carpets = layout.carpetTiles as Array<Obj | null> | undefined;
  if (carpets) {
    const seen = new Set<string>();
    let carpeted = 0;
    for (const c of carpets) {
      if (!c) continue;
      if (++carpeted > LAYOUT_MAX_CARPET_CELLS)
        return `layout has over ${LAYOUT_MAX_CARPET_CELLS} carpeted cells`;
      seen.add(carpetVariantKey(c));
      if (seen.size > LAYOUT_MAX_CARPET_COLOUR_VARIANTS)
        return `layout has over ${LAYOUT_MAX_CARPET_COLOUR_VARIANTS} distinct carpet colour variants`;
    }
  }
  const seen = new Set<string>();
  for (const f of layout.furniture as Obj[]) {
    if (!f.color) continue;
    seen.add(furnitureVariantKey(f));
    if (seen.size > LAYOUT_MAX_FURNITURE_COLOUR_VARIANTS)
      return `layout has over ${LAYOUT_MAX_FURNITURE_COLOUR_VARIANTS} distinct furniture colour variants`;
  }
  return null;
}

/**
 * ADMISSION: the given form passes checkLayoutForm, and so does the form the consumer will hold —
 * the result of migrateLayout — whenever migration changes anything (an unchanged layout was
 * already fully checked: unchanged means its tileColors already had the exact length). Returns
 * the MIGRATED layout (what the webview holds) or the reason it is refused.
 */
export function admitLayout(
  layout: unknown,
  options: LayoutCheckOptions = {},
): { layout: Obj } | { refused: string } {
  const own = checkLayoutForm(layout, options);
  if (own !== null) return { refused: own };
  const migrated = migrateLayout(layout as MigratableLayout);
  if (!migrated.changed) return { layout: migrated.layout as unknown as Obj };
  const after = checkLayoutForm(migrated.layout);
  return after === null
    ? { layout: migrated.layout as unknown as Obj }
    : { refused: `after migration: ${after}` };
}

/** Admission as a yes/no: null when admissible (see admitLayout), else the reason. */
export function checkLayout(layout: unknown, options: LayoutCheckOptions = {}): string | null {
  const result = admitLayout(layout, options);
  return 'refused' in result ? result.refused : null;
}

/** UTF-8 byte length of a string, counted without allocating (lone surrogates count as 3, as
 *  TextEncoder would encode them as U+FFFD). */
export function utf8Length(text: string): number {
  let bytes = 0;
  for (let i = 0; i < text.length; i++) {
    const code = text.charCodeAt(i);
    if (code < 0x80) bytes += 1;
    else if (code < 0x800) bytes += 2;
    else if (code >= 0xd800 && code <= 0xdbff && i + 1 < text.length) {
      const next = text.charCodeAt(i + 1);
      if (next >= 0xdc00 && next <= 0xdfff) {
        bytes += 4;
        i++;
      } else bytes += 3;
    } else bytes += 3;
  }
  return bytes;
}

/** The two byte sizes that matter for a layout: its compact saveLayout FRAME (what /ws carries)
 *  and its pretty-printed FILE (what writeLayoutToFile and Export write). */
export function layoutByteSizes(layout: unknown): { frame: number; file: number } {
  return {
    frame: utf8Length(JSON.stringify({ type: 'saveLayout', layout })),
    file: utf8Length(JSON.stringify(layout, null, 2)),
  };
}

/** The byte half of admission: null when both sizes fit their ceilings, else the reason. */
export function checkLayoutBytes(layout: unknown): string | null {
  const { frame, file } = layoutByteSizes(layout);
  if (frame > LAYOUT_MAX_FRAME_BYTES)
    return `layout save frame is ${frame} bytes (UTF-8), over ${LAYOUT_MAX_FRAME_BYTES}`;
  if (file > LAYOUT_MAX_FILE_BYTES)
    return `layout file would be ${file} bytes (UTF-8), over ${LAYOUT_MAX_FILE_BYTES}`;
  return null;
}

/**
 * The INPUT-BYTE gate for layout TEXT, run BEFORE any JSON.parse: whitespace and pretty-printing
 * in the input are never measured by admission (which measures the NORMALIZED serialization), so a
 * tiny layout behind megabytes of whitespace would otherwise be fully parsed and then admitted.
 * O(1) when the text is over by code units alone (UTF-8 bytes are never fewer than UTF-16 code
 * units); otherwise counted exactly, allocation-free.
 */
export function checkLayoutTextBytes(text: string): string | null {
  if (text.length > LAYOUT_MAX_FILE_BYTES)
    return `layout text is over ${LAYOUT_MAX_FILE_BYTES} bytes (UTF-8)`;
  const bytes = utf8Length(text);
  return bytes > LAYOUT_MAX_FILE_BYTES
    ? `layout text is ${bytes} bytes (UTF-8), over ${LAYOUT_MAX_FILE_BYTES}`
    : null;
}

/** Parse a layout FILE's text — refused by its byte size BEFORE parsing (checkLayoutTextBytes;
 *  callers that can stat the file refuse earlier still, before reading it) — then check it in
 *  LEGACY mode. Returns the raw layout, or the reason it was refused. */
export function parseLayoutText(text: string): { layout: Obj } | { refused: string } {
  const tooLarge = checkLayoutTextBytes(text);
  if (tooLarge !== null) return { refused: tooLarge };
  let parsed: unknown;
  try {
    parsed = JSON.parse(text);
  } catch {
    return { refused: 'layout is not valid JSON' };
  }
  const reason = checkLayout(parsed, { legacy: true });
  return reason === null ? { layout: parsed as Obj } : { refused: reason };
}
