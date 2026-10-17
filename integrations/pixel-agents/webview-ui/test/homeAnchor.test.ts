/**
 * AMENDMENT 14 REGRESSION — the HOME anchor, with REAL CHAIRS.
 *
 * A large world furnishes offices far from the palace. Those satellite offices hold REAL
 * chair-category furniture (a seatless satellite proves nothing: the seat-based fallback already
 * ignores it), and deck-style DECK_* furniture is reused in far districts. With an explicit
 * `home`, none of that may move the Office view, join the deck, become a seat, or become a place
 * a character or pet lives:
 *   - officeRegion == home; frameWorldView('office') centres on it at its usable zoom;
 *   - deckRegion(..., home) is the deck beside the palace only (DECK_ANCHOR_MARGIN);
 *   - livingRegions == [home, deck];
 *   - a real OfficeState: every seat, every living tile, every assigned seat and — over thousands
 *     of simulated update steps — every character and pet tile stays inside home ∪ deck.
 * The same world WITHOUT `home` documents the legacy seat-based fallback: with real chairs in the
 * satellites it STRETCHES to span them (that stretch is what `home` exists to prevent).
 *
 * Furniture types, footprints and categories are the REAL bundled manifests
 * (webview-ui/public/assets/furniture), flattened the way the server's asset loader does.
 */
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';

import { beforeAll, test } from 'vitest';

import type {
  FurnitureAsset,
  FurnitureManifest,
  InheritedProps,
  ManifestGroup,
} from '../../core/src/assets/manifestUtils.ts';
import { flattenManifest } from '../../core/src/assets/manifestUtils.ts';
import { TILE_SIZE } from '../src/constants.ts';
import { expandLayout } from '../src/office/editor/editorActions.ts';
import { OfficeState } from '../src/office/engine/officeState.ts';
import { buildDynamicCatalog, getCatalogEntry } from '../src/office/layout/furnitureCatalog.ts';
import type { FootprintOf, TileRect } from '../src/office/layout/worldViews.ts';
import {
  deckRegion,
  frameWorldView,
  inRegions,
  isLargeWorld,
  livingRegions,
  officeRegion,
  usableZoom,
  viewportCentreWorldPoint,
} from '../src/office/layout/worldViews.ts';
import { mapOffset, visibleTiles } from '../src/office/projection.ts';
import { setPetTemplates } from '../src/office/sprites/petSpriteData.ts';
import type {
  OfficeLayout,
  PlacedFurniture,
  TileType as TileTypeVal,
} from '../src/office/types.ts';
import { TileType } from '../src/office/types.ts';

const ROOT = path.join(__dirname, '..', '..');
const FURNITURE_DIR = path.join(ROOT, 'webview-ui/public/assets/furniture');
/** Mirrors worldViews.ts DECK_ANCHOR_MARGIN (not exported): how far a DECK_* item may lie
 *  outside the home and still be ITS deck. */
const DECK_ANCHOR_MARGIN = 24;

// ── The real bundled furniture catalog ──────────────────────────────────────────────────────────

/** Every bundled manifest flattened exactly as server/src/assetLoader.ts does, registered with
 *  the webview catalog. Sprites are blank stand-ins of the declared size: nothing here draws, and
 *  every footprint, category, orientation and background row is the manifest's own. */
function loadBundledCatalog(): FurnitureAsset[] {
  const catalog: FurnitureAsset[] = [];
  for (const dir of fs.readdirSync(FURNITURE_DIR)) {
    const file = path.join(FURNITURE_DIR, dir, 'manifest.json');
    if (!fs.existsSync(file)) continue;
    const m = JSON.parse(fs.readFileSync(file, 'utf-8')) as FurnitureManifest;
    if (m.type === 'asset') {
      catalog.push({
        id: m.id,
        name: m.name,
        label: m.name,
        category: m.category,
        file: m.file ?? `${m.id}.png`,
        width: m.width!,
        height: m.height!,
        footprintW: m.footprintW!,
        footprintH: m.footprintH!,
        isDesk: m.category === 'desks',
        canPlaceOnWalls: m.canPlaceOnWalls,
        canPlaceOnSurfaces: m.canPlaceOnSurfaces,
        backgroundTiles: m.backgroundTiles,
        groupId: m.id,
      });
      continue;
    }
    const inherited: InheritedProps = {
      groupId: m.id,
      name: m.name,
      category: m.category,
      canPlaceOnWalls: m.canPlaceOnWalls,
      canPlaceOnSurfaces: m.canPlaceOnSurfaces,
      backgroundTiles: m.backgroundTiles,
      ...(m.rotationScheme ? { rotationScheme: m.rotationScheme } : {}),
    };
    const root: ManifestGroup = {
      type: 'group',
      groupType: m.groupType as ManifestGroup['groupType'],
      rotationScheme: m.rotationScheme,
      members: m.members!,
    };
    catalog.push(...flattenManifest(root, inherited));
  }
  const sprites = Object.fromEntries(
    catalog.map((a) => [
      a.id,
      Array.from({ length: a.height }, () => new Array<string>(a.width).fill('')),
    ]),
  );
  assert.ok(buildDynamicCatalog({ catalog, sprites }), 'the bundled catalog builds');
  return catalog;
}

/** The footprint the PRODUCT uses (officeState.ts / App.tsx): the catalog entry, seat = chair. */
const footprintOf: FootprintOf = (type) => {
  const e = getCatalogEntry(type);
  return e ? { w: e.footprintW, h: e.footprintH, seat: e.category === 'chairs' } : null;
};

// ── The synthetic large world ───────────────────────────────────────────────────────────────────

const COLS = 200;
const ROWS = 140;
/** The palace: a walled 40 x 40 block — the explicit `home` is exactly its bounding box. */
const HOME: TileRect = { c0: 40, r0: 50, c1: 80, r1: 90 };
/** The palace's deck, just north of it: planks on a VOID moat (open on its south side to the
 *  palace's north doorway only), bounded by its DECK_* fittings. */
const DECK: TileRect = { c0: 50, r0: 41, c1: 70, r1: 50 };
/** Two distant, decorated, walled satellite offices — each with REAL chairs. */
const EAST: TileRect = { c0: 150, r0: 15, c1: 185, r1: 45 };
const SOUTH: TileRect = { c0: 100, r0: 105, c1: 135, r1: 135 };
const SATELLITES = [EAST, SOUTH];

type Item = [type: string, col: number, row: number];
const PALACE_ITEMS: Item[] = [
  ['DESK_FRONT', 44, 54],
  ['CUSHIONED_CHAIR_BACK', 45, 56],
  ['DESK_FRONT', 50, 54],
  ['CUSHIONED_CHAIR_BACK', 51, 56],
  ['DESK_FRONT', 66, 54],
  ['CUSHIONED_CHAIR_BACK', 67, 56],
  ['DESK_FRONT', 72, 54],
  ['CUSHIONED_CHAIR_BACK', 73, 56],
  ['WOODEN_CHAIR_FRONT', 46, 67],
  ['SMALL_TABLE_FRONT', 46, 70],
  ['WOODEN_CHAIR_SIDE', 70, 70],
  ['SOFA_FRONT', 58, 80],
  ['COFFEE_TABLE', 58, 82],
];
/** One seat per non-background footprint tile of each palace chair: 4 + 1 + 1 + 2. */
const HOME_SEAT_COUNT = 8;
const DECK_ITEMS: Item[] = [
  ['DECK_RAILING', 50, 41],
  ['DECK_RAILING', 69, 41],
  ['DECK_LANTERN', 50, 48],
  ['DECK_LANTERN', 69, 48],
  ['DECK_BENCH', 54, 44],
  ['DECK_TELESCOPE', 64, 43],
  ['DECK_SIGNPOST', 56, 46],
];
const EAST_ITEMS: Item[] = [
  ['TABLE_FRONT', 155, 18],
  ['WOODEN_CHAIR_FRONT', 160, 20],
  ['CUSHIONED_CHAIR_FRONT', 165, 25],
  ['SOFA_FRONT', 170, 30],
  ['COFFEE_TABLE', 170, 32],
  ['DESK_FRONT', 152, 35],
  ['CUSHIONED_CHAIR_BACK', 153, 37],
  ['DECK_BENCH', 175, 38], // deck-style decor reused in a satellite office
];
const SOUTH_ITEMS: Item[] = [
  ['CUSHIONED_CHAIR_FRONT', 105, 110],
  ['WOODEN_CHAIR_FRONT', 110, 108],
  ['SMALL_TABLE_FRONT', 110, 113],
  ['SOFA_FRONT', 115, 120],
  ['COFFEE_TABLE', 115, 122],
  ['DESK_FRONT', 122, 112],
  ['CUSHIONED_CHAIR_BACK', 123, 114],
  ['DECK_LANTERN', 130, 125], // deck-style decor reused in a satellite office
];
/** DECK_* reused in other districts, all more than DECK_ANCHOR_MARGIN tiles from the home. */
const FAR_DECK_ITEMS: Item[] = [
  ['DECK_BENCH', 120, 60],
  ['DECK_LANTERN', 140, 80],
  ['DECK_BENCH', 10, 130],
  ['DECK_SIGNPOST', 190, 5],
];

const place = (items: Item[], prefix: string): PlacedFurniture[] =>
  items.map(([type, col, row], i) => ({ uid: `${prefix}-${i}-${type}`, type, col, row }));

interface WorldOptions {
  home?: boolean;
  satelliteChairs?: boolean;
}

function buildWorld({ home = true, satelliteChairs = true }: WorldOptions = {}): OfficeLayout {
  const tiles = new Array<TileTypeVal>(COLS * ROWS).fill(TileType.FLOOR_10);
  const set = (c: number, r: number, t: TileTypeVal) => {
    tiles[r * COLS + c] = t;
  };
  /** A walled building: WALL ring, floored interior, doorways cut into the ring. */
  const building = (b: TileRect, doors: Array<[number, number]>) => {
    for (let r = b.r0; r < b.r1; r++)
      for (let c = b.c0; c < b.c1; c++) {
        const edge = r === b.r0 || r === b.r1 - 1 || c === b.c0 || c === b.c1 - 1;
        set(c, r, edge ? TileType.WALL : TileType.FLOOR_1);
      }
    for (const [c, r] of doors) set(c, r, TileType.FLOOR_1);
  };
  // north doorway onto the deck, south gate onto open ground (so the outside IS reachable)
  building(HOME, [
    [59, HOME.r0],
    [60, HOME.r0],
    [60, HOME.r1 - 1],
  ]);
  building(EAST, [[167, EAST.r1 - 1]]);
  building(SOUTH, [[117, SOUTH.r0]]);
  for (let r = DECK.r0 - 1; r < DECK.r1; r++)
    for (let c = DECK.c0 - 1; c <= DECK.c1; c++) {
      const moat = r === DECK.r0 - 1 || c === DECK.c0 - 1 || c === DECK.c1;
      set(c, r, moat ? TileType.VOID : TileType.FLOOR_13);
    }
  const isChair = (type: string) => getCatalogEntry(type)?.category === 'chairs';
  const satellite = (items: Item[]) =>
    satelliteChairs ? items : items.filter(([type]) => !isChair(type));
  return {
    version: 1,
    cols: COLS,
    rows: ROWS,
    tiles,
    furniture: [
      ...place(PALACE_ITEMS, 'palace'),
      ...place(DECK_ITEMS, 'deck'),
      ...place(satellite(EAST_ITEMS), 'east'),
      ...place(satellite(SOUTH_ITEMS), 'south'),
      ...place(FAR_DECK_ITEMS, 'far'),
    ],
    ...(home ? { home: { ...HOME } } : {}),
  };
}

const inRect = (col: number, row: number, b: TileRect) =>
  col >= b.c0 && col < b.c1 && row >= b.r0 && row < b.r1;
const LIVING: TileRect[] = [HOME, DECK];
const living = (col: number, row: number) => inRegions(col, row, LIVING);

/** Deterministic Math.random (mulberry32) for the duration of `fn`. */
function withSeededRandom<T>(seed: number, fn: () => T): T {
  const real = Math.random;
  let a = seed >>> 0;
  Math.random = () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
  try {
    return fn();
  } finally {
    Math.random = real;
  }
}

beforeAll(() => {
  loadBundledCatalog();
  // one pet sprite set (3 blank frames per direction) so addPet admits petType 0
  const frames = () => [[['']], [['']], [['']]];
  setPetTemplates([
    {
      walkDown: frames(),
      idleDown: frames(),
      walkUp: frames(),
      idleUp: frames(),
      walkRight: frames(),
    },
  ]);
});

// ── The fixture is what it claims to be ─────────────────────────────────────────────────────────

test('fixture: every type is a REAL bundled asset; satellites hold real chairs; the world is large', () => {
  const layout = buildWorld();
  assert.ok(isLargeWorld(layout.cols, layout.rows));
  for (const f of layout.furniture) assert.ok(getCatalogEntry(f.type), `${f.type} is bundled`);
  for (const type of ['WOODEN_CHAIR_FRONT', 'CUSHIONED_CHAIR_FRONT', 'SOFA_FRONT'])
    assert.equal(footprintOf(type)?.seat, true, `${type} is chair-category (a real seat)`);
  for (const type of ['DECK_BENCH', 'DECK_LANTERN', 'TABLE_FRONT', 'COFFEE_TABLE'])
    assert.equal(footprintOf(type)?.seat, false, `${type} is not a seat`);
  for (const sat of SATELLITES) {
    const chairs = layout.furniture.filter(
      (f) => footprintOf(f.type)?.seat && inRect(f.col, f.row, sat),
    );
    assert.ok(chairs.length >= 3, `satellite ${JSON.stringify(sat)} holds real chairs`);
  }
  for (const [type, col, row] of FAR_DECK_ITEMS) {
    const fp = footprintOf(type)!;
    const gap = Math.max(
      HOME.c0 - (col + fp.w),
      col - HOME.c1,
      HOME.r0 - (row + fp.h),
      row - HOME.r1,
    );
    assert.ok(gap > DECK_ANCHOR_MARGIN, `${type}@${col},${row} is ${gap} tiles from home`);
  }
});

// ── With the explicit home: the pure functions ──────────────────────────────────────────────────

test('HOME: officeRegion is exactly home — satellite walls and chairs never stretch it', () => {
  const layout = buildWorld();
  assert.deepEqual(officeRegion(layout, footprintOf), HOME);
  assert.deepEqual(officeRegion(layout), HOME); // with or without seat information
});

test('HOME: deckRegion(anchor = home) is the palace deck only; far DECK_* reuse is excluded', () => {
  const layout = buildWorld();
  assert.deepEqual(deckRegion(layout, footprintOf, HOME), DECK);
  // unanchored, every DECK_* counts: the far reuse would stretch the deck across the world
  const unanchored = deckRegion(layout, footprintOf)!;
  assert.ok(unanchored.c1 - unanchored.c0 > 100, JSON.stringify(unanchored));
});

test('HOME: livingRegions == [home, deck]', () => {
  assert.deepEqual(livingRegions(buildWorld(), footprintOf), [HOME, DECK]);
});

test('HOME: the Office view centres on home at its usable zoom; the view never spans a satellite', () => {
  const layout = buildWorld();
  for (const [w, h, dpr] of [
    [2184, 1720, 2],
    [1152, 945, 1],
    [2304, 1890, 2],
  ]) {
    const ctx = `${w}x${h} @${dpr}x`;
    const f = frameWorldView('office', layout, footprintOf, w, h, dpr)!;
    assert.ok(f, ctx);
    assert.equal(f.zoom, usableZoom(HOME, w, h, dpr), `${ctx}: usable zoom of home`);
    const centre = viewportCentreWorldPoint(f.panX, f.panY, f.zoom, COLS, ROWS, w, h);
    assert.ok(
      Math.abs(centre.x - ((HOME.c0 + HOME.c1) / 2) * TILE_SIZE) <= 1 &&
        Math.abs(centre.y - ((HOME.r0 + HOME.r1) / 2) * TILE_SIZE) <= 1,
      `${ctx}: centred on home, got ${JSON.stringify(centre)}`,
    );
    const { offsetX, offsetY } = mapOffset(w, h, COLS, ROWS, f.zoom, f.panX, f.panY);
    const view = visibleTiles(w, h, offsetX, offsetY, f.zoom, COLS, ROWS, 0);
    const overlaps = (b: TileRect) =>
      view.c0 < b.c1 && b.c0 < view.c1 && view.r0 < b.r1 && b.r0 < view.r1;
    assert.ok(overlaps(HOME) && overlaps(DECK), `${ctx}: palace and deck in view`);
    for (const sat of SATELLITES)
      assert.ok(!overlaps(sat), `${ctx}: view ${JSON.stringify(view)} spans a satellite`);
  }
});

// ── With the explicit home: a REAL OfficeState ──────────────────────────────────────────────────

test('HOME: OfficeState seats are the palace chairs only; living tiles stay in home ∪ deck', () => {
  const os = new OfficeState(buildWorld());
  assert.equal(os.seats.size, HOME_SEAT_COUNT);
  for (const [uid, s] of os.seats) {
    assert.ok(living(s.seatCol, s.seatRow), `seat ${uid} at ${s.seatCol},${s.seatRow}`);
    assert.ok(uid.startsWith('palace-'), `seat ${uid} is a palace chair`);
  }
  assert.ok(os.walkableTiles.length > 0);
  for (const t of os.walkableTiles)
    assert.ok(living(t.col, t.row), `living tile ${t.col},${t.row}`);
  assert.ok(
    os.walkableTiles.some((t) => inRect(t.col, t.row, DECK)),
    'the deck is part of the living area (reachable through the north doorway)',
  );
  // the open ground beyond the south gate IS reachable from a seat, yet is not lived on
  assert.ok(!os.walkableTiles.some((t) => t.col === 60 && t.row === HOME.r1));
});

test('HOME: assigned seats are in the home; seatless overflow spawns in home ∪ deck', () => {
  withSeededRandom(14, () => {
    const os = new OfficeState(buildWorld());
    for (let id = 1; id <= HOME_SEAT_COUNT; id++) {
      os.addAgent(id);
      const ch = os.characters.get(id)!;
      assert.ok(ch.seatId, `agent ${id} seated`);
      const seat = os.seats.get(ch.seatId)!;
      assert.ok(inRect(seat.seatCol, seat.seatRow, HOME), `agent ${id} seat ${ch.seatId}`);
    }
    // the home is full: satellite chairs are NOT seats, so the overflow gets none
    for (let id = HOME_SEAT_COUNT + 1; id <= HOME_SEAT_COUNT + 3; id++) {
      os.addAgent(id);
      const ch = os.characters.get(id)!;
      assert.equal(ch.seatId, null, `overflow agent ${id} is not seated in a satellite`);
      assert.ok(
        living(ch.tileCol, ch.tileRow),
        `overflow agent ${id} at ${ch.tileCol},${ch.tileRow}`,
      );
    }
  });
});

test('HOME: thousands of simulated steps — no character or pet ever leaves home ∪ deck', () => {
  withSeededRandom(20261016, () => {
    const os = new OfficeState({ ...buildWorld(), pets: [] });
    const agents = HOME_SEAT_COUNT + 2; // two more than the home seats: two roam seatless
    for (let id = 1; id <= agents; id++) os.addAgent(id);
    for (let id = 3; id <= agents; id++) os.setAgentActive(id, false); // idle agents wander
    os.addPet({ id: 'pet-a', petType: 0 });
    os.addPet({ id: 'pet-b', petType: 0 });
    os.addPet({ id: 'pet-c', petType: 0 });
    assert.equal(os.pets.length, 3);

    const STEPS = 6_000;
    const DT = 0.1; // the game loop's delta cap: 600 simulated seconds
    const violations: string[] = [];
    const visited = new Set<string>();
    let deckVisits = 0;
    for (let step = 0; step < STEPS; step++) {
      // agents come and go from work, so seat-bound walks happen throughout
      if (step % 400 === 0) {
        const id = 1 + ((step / 400) % agents);
        const ch = os.characters.get(id)!;
        os.setAgentActive(id, !ch.isActive);
      }
      os.update(DT);
      for (const ch of os.characters.values()) {
        visited.add(`${ch.tileCol},${ch.tileRow}`);
        if (inRect(ch.tileCol, ch.tileRow, DECK)) deckVisits++;
        if (!living(ch.tileCol, ch.tileRow))
          violations.push(`step ${step}: agent ${ch.id} at ${ch.tileCol},${ch.tileRow}`);
      }
      for (const pet of os.pets) {
        visited.add(`${pet.tileCol},${pet.tileRow}`);
        if (inRect(pet.tileCol, pet.tileRow, DECK)) deckVisits++;
        if (!living(pet.tileCol, pet.tileRow))
          violations.push(`step ${step}: ${pet.id} at ${pet.tileCol},${pet.tileRow}`);
      }
    }
    assert.deepEqual(violations.slice(0, 10), []);
    // not vacuous: they really moved around, the deck included
    assert.ok(visited.size > 150, `only ${visited.size} distinct tiles visited`);
    assert.ok(deckVisits > 0, 'someone walked out onto the deck');
    for (const ch of os.characters.values())
      if (ch.seatId)
        assert.ok(inRect(os.seats.get(ch.seatId)!.seatCol, os.seats.get(ch.seatId)!.seatRow, HOME));
  });
});

// ── DECK_ANCHOR_MARGIN, at its edge ─────────────────────────────────────────────────────────────

test('DECK_ANCHOR_MARGIN: a DECK_* item margin-1 tiles out joins the deck; margin+5 is excluded', () => {
  const base = buildWorld();
  const fp = footprintOf('DECK_RAILING')!; // 1 x 1
  const at = (col: number, row: number) =>
    deckRegion(
      { ...base, furniture: [...base.furniture, { uid: 'probe', type: 'DECK_RAILING', col, row }] },
      footprintOf,
      HOME,
    )!;
  const midC = (HOME.c0 + HOME.c1) / 2;
  const midR = (HOME.r0 + HOME.r1) / 2;
  const near = DECK_ANCHOR_MARGIN - 1;
  const far = DECK_ANCHOR_MARGIN + 5;
  // east: gap = col - c1 · west: gap = c0 - (col + w) · north: gap = r0 - (row + h) · south: gap = row - r1
  const probes: Record<string, (gap: number) => [number, number]> = {
    east: (gap) => [HOME.c1 + gap, midR],
    west: (gap) => [HOME.c0 - fp.w - gap, midR],
    north: (gap) => [midC, HOME.r0 - fp.h - gap],
    south: (gap) => [midC, HOME.r1 + gap],
  };
  for (const [side, probe] of Object.entries(probes)) {
    const [nc, nr] = probe(near);
    const joined = at(nc, nr);
    assert.ok(
      inRect(nc, nr, joined),
      `${side}: margin-1 (${nc},${nr}) joins ${JSON.stringify(joined)}`,
    );
    assert.notDeepEqual(joined, DECK, `${side}: the deck grew to include it`);
    const [fc, fr] = probe(far);
    assert.deepEqual(at(fc, fr), DECK, `${side}: margin+5 (${fc},${fr}) is excluded`);
  }
  // exactly AT the margin still counts (the exclusion is strictly beyond it)
  assert.ok(inRect(HOME.c1 + DECK_ANCHOR_MARGIN, midR, at(HOME.c1 + DECK_ANCHOR_MARGIN, midR)));
  assert.deepEqual(at(HOME.c1 + DECK_ANCHOR_MARGIN + 1, midR), DECK);
});

// ── Without home: the legacy seat-based fallback (documented, expected to STRETCH) ──────────────

test('NO HOME (legacy fallback): real satellite chairs make the office STRETCH over every walled building that holds a seat', () => {
  const layout = buildWorld({ home: false });
  // Wall components whose bbox holds a chair: the palace (its ring is split into two arcs by the
  // north doorway and the south gate — both hold palace chairs) AND both satellites.
  const stretched: TileRect = {
    c0: Math.min(HOME.c0, EAST.c0, SOUTH.c0),
    r0: Math.min(HOME.r0, EAST.r0, SOUTH.r0),
    c1: Math.max(HOME.c1, EAST.c1, SOUTH.c1),
    r1: Math.max(HOME.r1, EAST.r1, SOUTH.r1),
  };
  assert.deepEqual(stretched, { c0: 40, r0: 15, c1: 185, r1: 135 });
  assert.deepEqual(officeRegion(layout, footprintOf), stretched);
  // its deck anchors to that stretched office, so DECK_* reuse within 24 tiles of it joins: all of
  // it except the bench at (10,130), whose east edge (12) is more than 24 tiles west of col 40
  const deck = deckRegion(layout, footprintOf, stretched);
  assert.deepEqual(deck, { c0: 50, r0: 5, c1: 191, r1: 127 });
  assert.deepEqual(livingRegions(layout, footprintOf), [stretched, deck]);
  // the Office view is framed on the stretched region: far from the palace
  const f = frameWorldView('office', layout, footprintOf, 2184, 1720, 2)!;
  const c = viewportCentreWorldPoint(f.panX, f.panY, f.zoom, COLS, ROWS, 2184, 1720);
  const homeX = ((HOME.c0 + HOME.c1) / 2) * TILE_SIZE;
  assert.ok(
    Math.abs(c.x - homeX) / TILE_SIZE > 30,
    `centred ${(c.x - homeX) / TILE_SIZE} tiles off home`,
  );
  // and satellite chairs become SEATS — the leak `home` exists to prevent
  const os = new OfficeState(layout);
  const leaked = [...os.seats.values()].filter((s) =>
    SATELLITES.some((sat) => inRect(s.seatCol, s.seatRow, sat)),
  );
  assert.ok(leaked.length > 0, 'legacy fallback seats agents in the satellites');
  assert.ok(os.seats.size > HOME_SEAT_COUNT);
});

test('NO HOME, SEATLESS satellites: the fallback alone already picks the palace (why seatless is not the regression)', () => {
  const layout = buildWorld({ home: false, satelliteChairs: false });
  assert.deepEqual(officeRegion(layout, footprintOf), HOME);
  assert.deepEqual(livingRegions(layout, footprintOf), [HOME, DECK]);
  assert.equal(new OfficeState(layout).seats.size, HOME_SEAT_COUNT);
});

// ── The editor keeps home on the grid ───────────────────────────────────────────────────────────

test('expandLayout shifts home when the grid grows left/up, and leaves it when it grows right/down', () => {
  const layout = buildWorld();
  const left = expandLayout(layout, 'left')!;
  assert.deepEqual(left.layout.home, { ...HOME, c0: HOME.c0 + 1, c1: HOME.c1 + 1 });
  const up = expandLayout(layout, 'up')!;
  assert.deepEqual(up.layout.home, { ...HOME, r0: HOME.r0 + 1, r1: HOME.r1 + 1 });
  assert.deepEqual(expandLayout(layout, 'right')!.layout.home, HOME);
  assert.deepEqual(expandLayout(layout, 'down')!.layout.home, HOME);
  assert.deepEqual(officeRegion(left.layout, footprintOf), left.layout.home);
});
