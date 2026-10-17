/**
 * LARGE-WORLD CAMERA VIEWS AND LIVING AREA (DOM-free, unit-tested).
 *
 * A layout within the former 64 x 64 ceiling is a LEGACY-SIZE world: nothing here applies and it
 * keeps whole-world fit framing exactly. A LARGER world cannot be fitted whole at a usable zoom
 * (integer zoom clamps at ZOOM_MIN, so a ~20k-cell world would shrink the office to a speck), so
 * it gets NAMED VIEWS derived from the layout's own CONTENT — no extra layout field, so the saved
 * layout and its export stay exactly the editor's shape:
 *   office — the bounding box of the WALL tiles (the building), at a usable zoom;
 *   deck   — the bounding box of DECK_* furniture, framed to look out over what lies north;
 *   woods  — the woodland around the palace at the smallest PIXEL-PERFECT zoom (ZOOM_MIN, centred
 *            on the world). Integer zoom cannot fit a large world, so this is a framed region of
 *            it, not an overview: the WHOLE world is the Map (fitWorldMap), drawn separately and
 *            scaled down, so integer device-pixels-per-sprite-pixel stays intact everywhere else.
 * The same regions bound where characters and pets LIVE in a large world (wander targets and
 * spawn points): the forest is scenery, so the live team stays in view instead of drifting off
 * into thousands of walkable woodland tiles. Pathfinding itself is unchanged.
 */
import {
  LEGACY_WORLD_MAX_DIM,
  TILE_SIZE,
  WORLD_DECK_TYPE_PREFIX,
  WORLD_DECK_VIEW_SOUTH_EDGE_FRACTION,
  WORLD_VIEW_MIN_CSS_PX_PER_SPRITE_PX,
  ZOOM_MAX,
  ZOOM_MIN,
} from '../../constants.js';
import type { OfficeLayout } from '../types.js';
import { TileType } from '../types.js';
import { diFitZoom } from './diWorld.js';

/** A tile rectangle: [c0, c1) x [r0, r1). */
export interface TileRect {
  c0: number;
  r0: number;
  c1: number;
  r1: number;
}

export type WorldViewKind = 'office' | 'deck' | 'woods';
export const WORLD_VIEW_KINDS: readonly WorldViewKind[] = ['office', 'deck', 'woods'];

/** Footprint of a furniture type in tiles (and whether it is a SEAT — a chair), or null when the
 *  type is unknown. */
export type FootprintOf = (type: string) => { w: number; h: number; seat?: boolean } | null;

/** How far (tiles) a DECK_* item may lie outside the home office and still be ITS deck. */
const DECK_ANCHOR_MARGIN = 24;

export interface Framing {
  zoom: number;
  panX: number;
  panY: number;
}

export function isLargeWorld(cols: number, rows: number): boolean {
  return cols > LEGACY_WORLD_MAX_DIM || rows > LEGACY_WORLD_MAX_DIM;
}

/**
 * THE HOME OFFICE. A layout that stores an explicit `home` region (the metropolis world stores the
 * palace's own 64 x 64 block) is anchored to it, exactly: no content anywhere else — walls, chairs,
 * deck-style furniture in a decorated satellite office — can move or stretch it.
 * Without `home` (every legacy layout, the forest world): derived from content. WALL tiles are
 * grouped into 4-connected components and the home is the union of the components whose bounding
 * box contains a SEAT (a chair tile); with no seat information every wall counts — the original
 * rule, which is identical for a world with one building.
 */
export function officeRegion(
  layout: Pick<OfficeLayout, 'cols' | 'rows' | 'tiles'> &
    Partial<Pick<OfficeLayout, 'furniture' | 'home'>>,
  footprintOf?: FootprintOf,
): TileRect | null {
  const { cols, rows, tiles } = layout;
  if (layout.home) {
    const h = layout.home;
    const c0 = Math.max(0, h.c0);
    const r0 = Math.max(0, h.r0);
    const c1 = Math.min(cols, h.c1);
    const r1 = Math.min(rows, h.r1);
    if (c1 > c0 && r1 > r0) return { c0, r0, c1, r1 };
  }
  const seats: Array<{ col: number; row: number }> = [];
  if (footprintOf && layout.furniture)
    for (const f of layout.furniture) {
      const fp = footprintOf(f.type);
      if (fp?.seat) seats.push({ col: f.col, row: f.row });
    }
  const comp = new Int32Array(cols * rows).fill(-1);
  const boxes: TileRect[] = [];
  const queue: number[] = [];
  for (let start = 0; start < cols * rows; start++) {
    if (tiles[start] !== TileType.WALL || comp[start] >= 0) continue;
    const id = boxes.length;
    const box = { c0: Infinity, r0: Infinity, c1: -Infinity, r1: -Infinity };
    comp[start] = id;
    queue.length = 0;
    queue.push(start);
    for (let q = 0; q < queue.length; q++) {
      const p = queue[q];
      const c = p % cols;
      const r = (p - c) / cols;
      box.c0 = Math.min(box.c0, c);
      box.r0 = Math.min(box.r0, r);
      box.c1 = Math.max(box.c1, c + 1);
      box.r1 = Math.max(box.r1, r + 1);
      const next = [
        c > 0 ? p - 1 : -1,
        c < cols - 1 ? p + 1 : -1,
        r > 0 ? p - cols : -1,
        r < rows - 1 ? p + cols : -1,
      ];
      for (const n of next)
        if (n >= 0 && tiles[n] === TileType.WALL && comp[n] < 0) {
          comp[n] = id;
          queue.push(n);
        }
    }
    boxes.push(box);
  }
  if (boxes.length === 0) return null;
  const holding = boxes.filter((b) =>
    seats.some((s) => s.col >= b.c0 && s.col < b.c1 && s.row >= b.r0 && s.row < b.r1),
  );
  const chosen = holding.length > 0 ? holding : boxes;
  return {
    c0: Math.min(...chosen.map((b) => b.c0)),
    r0: Math.min(...chosen.map((b) => b.r0)),
    c1: Math.max(...chosen.map((b) => b.c1)),
    r1: Math.max(...chosen.map((b) => b.r1)),
  };
}

/**
 * The observation deck: the bounding box of the DECK_* furniture footprints (null if none). With
 * an `anchor` (the home office), only DECK_* items within DECK_ANCHOR_MARGIN tiles of it count, so
 * a deck-style bench or lantern reused in a far district never joins the palace's deck.
 */
export function deckRegion(
  layout: Pick<OfficeLayout, 'cols' | 'rows' | 'furniture'>,
  footprintOf: FootprintOf,
  anchor?: TileRect | null,
): TileRect | null {
  let c0 = Infinity;
  let r0 = Infinity;
  let c1 = -Infinity;
  let r1 = -Infinity;
  for (const f of layout.furniture) {
    if (!f.type.startsWith(WORLD_DECK_TYPE_PREFIX)) continue;
    const fp = footprintOf(f.type) ?? { w: 1, h: 1 };
    if (
      anchor &&
      (f.col + fp.w < anchor.c0 - DECK_ANCHOR_MARGIN ||
        f.col > anchor.c1 + DECK_ANCHOR_MARGIN ||
        f.row + fp.h < anchor.r0 - DECK_ANCHOR_MARGIN ||
        f.row > anchor.r1 + DECK_ANCHOR_MARGIN)
    )
      continue;
    c0 = Math.min(c0, f.col);
    r0 = Math.min(r0, f.row);
    c1 = Math.max(c1, f.col + fp.w);
    r1 = Math.max(r1, f.row + fp.h);
  }
  if (c1 < 0) return null;
  return {
    c0: Math.max(0, c0),
    r0: Math.max(0, r0),
    c1: Math.min(layout.cols, c1),
    r1: Math.min(layout.rows, r1),
  };
}

/**
 * Where characters and pets live: in a large world, the office and the deck. Null means "the
 * whole world" (a legacy-size layout, or a large one with no building to anchor to).
 */
export function livingRegions(
  layout: Pick<OfficeLayout, 'cols' | 'rows' | 'tiles' | 'furniture'> &
    Partial<Pick<OfficeLayout, 'home'>>,
  footprintOf: FootprintOf,
): TileRect[] | null {
  if (!isLargeWorld(layout.cols, layout.rows)) return null;
  const office = officeRegion(layout, footprintOf);
  if (!office) return null;
  const deck = deckRegion(layout, footprintOf, office);
  return deck ? [office, deck] : [office];
}

export function inRegions(col: number, row: number, regions: readonly TileRect[]): boolean {
  return regions.some((g) => col >= g.c0 && col < g.c1 && row >= g.r0 && row < g.r1);
}

/**
 * The tiles connected (4-neighbour, through `tiles` themselves) to an ANCHOR — a seat, which is
 * a blocked chair tile, so the walk starts from the anchors' walkable neighbours. Where agents
 * work defines where they can go: a walkable pocket sealed off from every seat is excluded.
 */
export function connectedToAnchors<T extends { col: number; row: number }>(
  tiles: T[],
  anchors: ReadonlyArray<{ col: number; row: number }>,
): T[] {
  const byKey = new Map(tiles.map((t) => [`${t.col},${t.row}`, t]));
  const seen = new Set<string>();
  const queue: string[] = [];
  const visit = (col: number, row: number) => {
    const key = `${col},${row}`;
    if (byKey.has(key) && !seen.has(key)) {
      seen.add(key);
      queue.push(key);
    }
  };
  for (const a of anchors) {
    visit(a.col, a.row);
    visit(a.col + 1, a.row);
    visit(a.col - 1, a.row);
    visit(a.col, a.row + 1);
    visit(a.col, a.row - 1);
  }
  for (let i = 0; i < queue.length; i++) {
    const t = byKey.get(queue[i])!;
    visit(t.col + 1, t.row);
    visit(t.col - 1, t.row);
    visit(t.col, t.row + 1);
    visit(t.col, t.row - 1);
  }
  return tiles.filter((t) => seen.has(`${t.col},${t.row}`));
}

/**
 * Where characters and pets LIVE (wander targets, spawn points): in a large world, the walkable
 * tiles of the office and deck regions that a seat can actually reach. A legacy-size layout —
 * or one with nothing to anchor to — keeps every walkable tile, exactly as before.
 */
export function livingTiles<T extends { col: number; row: number }>(
  walkable: T[],
  regions: readonly TileRect[] | null,
  seats: ReadonlyArray<{ col: number; row: number }>,
): T[] {
  if (!regions) return walkable;
  const reachable = seats.length > 0 ? connectedToAnchors(walkable, seats) : walkable;
  const living = confineTiles(reachable, regions);
  return living.length > 0 ? living : confineTiles(walkable, regions);
}

/** Keep only the tiles inside the regions (null regions = keep all). Never empties the list. */
export function confineTiles<T extends { col: number; row: number }>(
  tiles: T[],
  regions: readonly TileRect[] | null,
): T[] {
  if (!regions) return tiles;
  const kept = tiles.filter((t) => inRegions(t.col, t.row, regions));
  return kept.length > 0 ? kept : tiles;
}

/** Pan bounds so the map never leaves an edge gap where the world is wider than the canvas. */
export function clampPanToWorld(
  panX: number,
  panY: number,
  zoom: number,
  cols: number,
  rows: number,
  backingW: number,
  backingH: number,
): { x: number; y: number } {
  const maxX = Math.max(0, (cols * TILE_SIZE * zoom - backingW) / 2);
  const maxY = Math.max(0, (rows * TILE_SIZE * zoom - backingH) / 2);
  return {
    x: Math.max(-maxX, Math.min(maxX, panX)),
    y: Math.max(-maxY, Math.min(maxY, panY)),
  };
}

/** The usable zoom for a region: its fit, but never below WORLD_VIEW_MIN_CSS_PX_PER_SPRITE_PX. */
export function usableZoom(
  region: TileRect,
  backingW: number,
  backingH: number,
  dpr: number,
): number {
  const fit = diFitZoom(backingW, backingH, region.c1 - region.c0, region.r1 - region.r0);
  const floor = Math.round(Math.max(1, dpr) * WORLD_VIEW_MIN_CSS_PX_PER_SPRITE_PX);
  return Math.max(ZOOM_MIN, Math.min(ZOOM_MAX, Math.max(fit, floor)));
}

function panToCenter(
  centerXPx: number,
  centerYPx: number,
  zoom: number,
  cols: number,
  rows: number,
  backingW: number,
  backingH: number,
): Framing {
  const mapW = cols * TILE_SIZE * zoom;
  const mapH = rows * TILE_SIZE * zoom;
  const pan = clampPanToWorld(
    mapW / 2 - centerXPx * zoom,
    mapH / 2 - centerYPx * zoom,
    zoom,
    cols,
    rows,
    backingW,
    backingH,
  );
  return { zoom, panX: pan.x, panY: pan.y };
}

/**
 * Zoom + pan (device pixels, the renderer's pan convention) for a named view of a LARGE world,
 * or null when the view does not exist in this layout (e.g. no deck) or the world is legacy-size.
 */
export function frameWorldView(
  kind: WorldViewKind,
  layout: Pick<OfficeLayout, 'cols' | 'rows' | 'tiles' | 'furniture'> &
    Partial<Pick<OfficeLayout, 'home'>>,
  footprintOf: FootprintOf,
  backingW: number,
  backingH: number,
  dpr: number,
): Framing | null {
  const { cols, rows } = layout;
  if (!isLargeWorld(cols, rows) || backingW <= 0 || backingH <= 0) return null;
  const office = officeRegion(layout, footprintOf);
  if (kind === 'woods') {
    return panToCenter(
      (cols * TILE_SIZE) / 2,
      (rows * TILE_SIZE) / 2,
      diFitZoom(backingW, backingH, cols, rows),
      cols,
      rows,
      backingW,
      backingH,
    );
  }
  if (kind === 'office') {
    if (!office) return null;
    const zoom = usableZoom(office, backingW, backingH, dpr);
    return panToCenter(
      ((office.c0 + office.c1) / 2) * TILE_SIZE,
      ((office.r0 + office.r1) / 2) * TILE_SIZE,
      zoom,
      cols,
      rows,
      backingW,
      backingH,
    );
  }
  const deck = deckRegion(layout, footprintOf, office);
  if (!deck) return null;
  // The same scale as the office view, the deck's south edge low in the frame so the view
  // looks out over whatever lies north of it.
  const zoom = usableZoom(office ?? deck, backingW, backingH, dpr);
  const visibleH = backingH / zoom;
  const centerY = deck.r1 * TILE_SIZE - (WORLD_DECK_VIEW_SOUTH_EDGE_FRACTION - 0.5) * visibleH;
  return panToCenter(
    ((deck.c0 + deck.c1) / 2) * TILE_SIZE,
    centerY,
    zoom,
    cols,
    rows,
    backingW,
    backingH,
  );
}

/** A CSS rectangle inside a container. */
export interface CssRect {
  x: number;
  y: number;
  w: number;
  h: number;
  /** CSS pixels per world sprite pixel (below 1 for a large world: the Map scales down). */
  scale: number;
}

/**
 * THE MAP: the whole world (worldW x worldH sprite pixels), scaled to fit inside a container
 * (availW x availH CSS pixels, less `margin` on every side), aspect preserved and centred. Never
 * scaled UP beyond 1 CSS pixel per sprite pixel. All four world edges are inside the container by
 * construction — the overview the integer-zoom renderer cannot give a large world.
 */
export function fitWorldMap(
  worldW: number,
  worldH: number,
  availW: number,
  availH: number,
  margin: number,
): CssRect {
  const innerW = Math.max(1, availW - 2 * margin);
  const innerH = Math.max(1, availH - 2 * margin);
  const scale = Math.min(1, innerW / worldW, innerH / worldH);
  const w = Math.floor(worldW * scale);
  const h = Math.floor(worldH * scale);
  return { x: Math.floor((availW - w) / 2), y: Math.floor((availH - h) / 2), w, h, scale };
}

/** The world sprite-pixel point under a CSS point on the Map (clamped to the world). */
export function mapPointToWorld(
  rect: CssRect,
  cssX: number,
  cssY: number,
  worldW: number,
  worldH: number,
): { x: number; y: number } {
  return {
    x: Math.max(0, Math.min(worldW, (cssX - rect.x) / rect.scale)),
    y: Math.max(0, Math.min(worldH, (cssY - rect.y) / rect.scale)),
  };
}

/** Pan (renderer convention) that centres the camera on a world sprite-pixel point, clamped. */
export function panToWorldPoint(
  worldX: number,
  worldY: number,
  zoom: number,
  cols: number,
  rows: number,
  backingW: number,
  backingH: number,
): { x: number; y: number } {
  const mapW = cols * TILE_SIZE * zoom;
  const mapH = rows * TILE_SIZE * zoom;
  return clampPanToWorld(
    mapW / 2 - worldX * zoom,
    mapH / 2 - worldY * zoom,
    zoom,
    cols,
    rows,
    backingW,
    backingH,
  );
}

/** The world sprite-pixel point at the CENTRE of the viewport (inverse of the renderer's
 *  mapOffset): what the camera is looking at. */
export function viewportCentreWorldPoint(
  panX: number,
  panY: number,
  zoom: number,
  cols: number,
  rows: number,
  backingW: number,
  backingH: number,
): { x: number; y: number } {
  const offsetX = Math.floor((backingW - cols * TILE_SIZE * zoom) / 2) + Math.round(panX);
  const offsetY = Math.floor((backingH - rows * TILE_SIZE * zoom) / 2) + Math.round(panY);
  return { x: (backingW / 2 - offsetX) / zoom, y: (backingH / 2 - offsetY) / zoom };
}

/**
 * A USER zoom step in a LARGE world: the pan at `toZoom` that keeps the world point under the
 * ANCHOR (a canvas point in device pixels — the viewport centre for the zoom buttons, the cursor
 * for Ctrl+wheel) under that same canvas point, then clamped to the world at the NEW zoom.
 * Pan is device pixels from the centred map, and the centre's world point is C/2 - pan/zoom: a
 * zoom change that kept pan would slide the view toward the world's middle (zooming in on the far
 * coast landed in the city — defect S-N1). The clamp can legitimately move the anchor when the
 * world edge would otherwise leave a gap (that is the clamp's job; never a gap instead).
 */
export function panForZoomAroundPoint(
  panX: number,
  panY: number,
  fromZoom: number,
  toZoom: number,
  cols: number,
  rows: number,
  backingW: number,
  backingH: number,
  anchorX = backingW / 2,
  anchorY = backingH / 2,
): { x: number; y: number } {
  const offsetX = Math.floor((backingW - cols * TILE_SIZE * fromZoom) / 2) + Math.round(panX);
  const offsetY = Math.floor((backingH - rows * TILE_SIZE * fromZoom) / 2) + Math.round(panY);
  const worldX = (anchorX - offsetX) / fromZoom;
  const worldY = (anchorY - offsetY) / fromZoom;
  const nextOffsetX = anchorX - worldX * toZoom;
  const nextOffsetY = anchorY - worldY * toZoom;
  return clampPanToWorld(
    nextOffsetX - Math.floor((backingW - cols * TILE_SIZE * toZoom) / 2),
    nextOffsetY - Math.floor((backingH - rows * TILE_SIZE * toZoom) / 2),
    toZoom,
    cols,
    rows,
    backingW,
    backingH,
  );
}

/** A named district of a large world, from the layout's own `areas` / `areaTiles` (DATA, not a
 *  fixed list): its label, cell bounding box, and the cell OF the district nearest its centroid
 *  (the camera target — a centroid can fall outside an irregular district). */
export interface District {
  label: string;
  box: TileRect;
  target: { col: number; row: number };
  cells: number;
}

export function districtsOf(
  layout: Pick<OfficeLayout, 'cols' | 'rows'> & Partial<Pick<OfficeLayout, 'areas' | 'areaTiles'>>,
): District[] {
  const { cols, rows, areas, areaTiles } = layout;
  if (!areas || !areaTiles || areas.length === 0) return [];
  const acc = new Map<string, { n: number; sc: number; sr: number; box: TileRect }>();
  for (let r = 0; r < rows; r++)
    for (let c = 0; c < cols; c++) {
      const label = areaTiles[r * cols + c];
      if (!label) continue;
      const a = acc.get(label) ?? {
        n: 0,
        sc: 0,
        sr: 0,
        box: { c0: c, r0: r, c1: c + 1, r1: r + 1 },
      };
      a.n++;
      a.sc += c;
      a.sr += r;
      a.box = {
        c0: Math.min(a.box.c0, c),
        r0: Math.min(a.box.r0, r),
        c1: Math.max(a.box.c1, c + 1),
        r1: Math.max(a.box.r1, r + 1),
      };
      acc.set(label, a);
    }
  const out: District[] = [];
  for (const { label } of areas) {
    const a = acc.get(label);
    if (!a) continue; // a defined area with no cells is not a place
    const cc = a.sc / a.n;
    const cr = a.sr / a.n;
    let best = { col: a.box.c0, row: a.box.r0 };
    let bestD = Infinity;
    for (let r = a.box.r0; r < a.box.r1; r++)
      for (let c = a.box.c0; c < a.box.c1; c++) {
        if (areaTiles[r * cols + c] !== label) continue;
        const d = (c - cc) ** 2 + (r - cr) ** 2;
        if (d < bestD) {
          bestD = d;
          best = { col: c, row: r };
        }
      }
    out.push({ label, box: a.box, target: best, cells: a.n });
  }
  return out;
}

export interface DistrictFraming extends Framing {
  /** True when the district's target cell is at the viewport centre. False only when no zoom up
   *  to ZOOM_MAX can centre it (the world edge clamps the camera) — the view then still shows
   *  the district, and the UI says so instead of being silently off-target. */
  centred: boolean;
}

/**
 * Frame a named district: the SMALLEST zoom at or above the district's usable zoom at which its
 * target cell can sit at the viewport centre once the pan is clamped to the world (an edge
 * district needs a closer zoom — at the widest zoom the world edge would hold the camera inside
 * the neighbouring district). Pan centres the target (renderer convention, device pixels).
 */
export function frameDistrict(
  layout: Pick<OfficeLayout, 'cols' | 'rows'> & Partial<Pick<OfficeLayout, 'areas' | 'areaTiles'>>,
  district: District,
  backingW: number,
  backingH: number,
  dpr: number,
): DistrictFraming | null {
  const { cols, rows } = layout;
  if (backingW <= 0 || backingH <= 0) return null;
  const base = usableZoom(district.box, backingW, backingH, dpr);
  const wx = (district.target.col + 0.5) * TILE_SIZE;
  const wy = (district.target.row + 0.5) * TILE_SIZE;
  for (let zoom = base; zoom <= ZOOM_MAX; zoom++) {
    const pan = panToWorldPoint(wx, wy, zoom, cols, rows, backingW, backingH);
    const centre = viewportCentreWorldPoint(pan.x, pan.y, zoom, cols, rows, backingW, backingH);
    const col = Math.floor(centre.x / TILE_SIZE);
    const row = Math.floor(centre.y / TILE_SIZE);
    if (layout.areaTiles?.[row * cols + col] === district.label)
      return { zoom, panX: pan.x, panY: pan.y, centred: true };
  }
  const pan = panToWorldPoint(wx, wy, base, cols, rows, backingW, backingH);
  return { zoom: base, panX: pan.x, panY: pan.y, centred: false };
}
