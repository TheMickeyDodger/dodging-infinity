#!/usr/bin/env node
/**
 * Proves (does not assert) that a forest world preserves its palace and meets the forest brief.
 *
 *   node scripts/verify-forest-world.mjs --palace <verified-live-export.json> --world <world.json>
 *
 * Prints a JSON report and exits non-zero on any failed check. Checks:
 *  - the palace's 64x64 tiles / tileColors / carpetTiles / areaTiles block is reproduced exactly
 *    at the ONE uniform translation (dcol, drow) derived from the data (and must be unique),
 *    EXCEPT the documented doorway cells enumerated below — the check passes only when the
 *    observed mismatch set EQUALS that enumeration (a stray difference anywhere else fails);
 *  - every palace furniture uid is present with the same type and color at col+dcol, row+drow,
 *    and nothing else changed about it; computers (PC_*) and pets are counted by identity;
 *  - P (palace non-VOID cells) and F (non-VOID cells outside the palace block) are MEASURED;
 *  - added furniture: none in a chair category (no seats in the trees), none overlapping the
 *    palace block, none in the strip above the palace's north wall (its wall hangings);
 *  - footprint-aware reachability (findPath semantics: WALL/VOID and non-background footprint
 *    rows block; a chair tile is a valid destination): every seat reachable from the palace
 *    interior in the canonical export is still reachable in the world; the deck reaches every
 *    clearing by trail; and whether the palace interior connects to the deck is REPORTED (the
 *    canonical palace has no exterior opening, so with its block byte-exact it does not).
 */
import crypto from 'node:crypto';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const args = process.argv.slice(2);
const get = (flag) => {
  const i = args.indexOf(flag);
  return i >= 0 ? args[i + 1] : undefined;
};
const palacePath = get('--palace');
const worldPath = get('--world') ?? path.join(ROOT, 'packaging/di/worlds/vast-forest.json');
if (!palacePath) {
  console.error('usage: verify-forest-world.mjs --palace <export.json> [--world <world.json>]');
  process.exit(2);
}
const sha = (file) => crypto.createHash('sha256').update(fs.readFileSync(file)).digest('hex');
const P = JSON.parse(fs.readFileSync(palacePath, 'utf-8'));
const W = JSON.parse(fs.readFileSync(worldPath, 'utf-8'));
const VOID = 255;
const WALL = 0;
const checks = [];
const check = (name, ok, detail) =>
  checks.push({ name, ok: !!ok, ...(detail === undefined ? {} : { detail }) });
const same = (a, b) => JSON.stringify(a ?? null) === JSON.stringify(b ?? null);

// ── catalog from the bundled manifests (footprint, background rows, category) ──
const catalog = new Map();
const furnDir = path.join(ROOT, 'webview-ui/public/assets/furniture');
const visit = (node, inherited) => {
  const props = {
    category: node.category ?? inherited.category,
    backgroundTiles: node.backgroundTiles ?? inherited.backgroundTiles ?? 0,
  };
  if (node.type === 'asset')
    catalog.set(node.id, { w: node.footprintW, h: node.footprintH, ...props });
  for (const m of node.members ?? []) visit(m, props);
};
for (const dir of fs.readdirSync(furnDir))
  visit(JSON.parse(fs.readFileSync(path.join(furnDir, dir, 'manifest.json'), 'utf-8')), {});
const entry = (type) => catalog.get(type) ?? catalog.get(type.replace(/:left$/, ''));

// ── the translation, derived from the furniture and confirmed on the tile block ──
const byUid = new Map(W.furniture.map((f) => [f.uid, f]));
// Derived from every record EXCEPT the documented re-sites (which are checked on their own below).
const RESITED_UIDS = new Set([
  'lookout-399',
  'lookout-400',
  'lookout-401',
  'lookout-402',
  'lookout-403',
]);
const deltas = new Set();
for (const f of P.furniture) {
  const g = byUid.get(f.uid);
  if (g && !RESITED_UIDS.has(f.uid)) deltas.add(`${g.col - f.col},${g.row - f.row}`);
}
check('one uniform translation (all records but the documented re-sites)', deltas.size === 1, [
  ...deltas,
]);
const [dcol, drow] = [...deltas][0].split(',').map(Number);

const pc = P.cols;
/**
 * THE DOCUMENTED EXCEPTIONS — the north gateway's change set, enumerated here independently of
 * the composer. Human design steering turns the palace's north grove (palace cols 21-28, rows
 * 0-8) into a gateway arch; the canonical palace has no exterior opening, so the gateway's clear
 * approach is also the smallest connection the approved acceptance needs (a useful observation
 * deck; usable routes). Tiles: a 4-wide threshold through the row-5 wall, each the palace's OWN
 * doorway threshold (the tile and colour of its existing 2-wide doorway at palace (33,11)).
 * Furniture: the grove's five plants re-sited into the gateway's flanks, identity intact.
 */
const DOOR_SOURCE = [33, 11];
const DOCUMENTED = [23, 24, 25, 26].map((c) => ({
  palace: [c, 5],
  reason: 'gateway threshold through the row-5 wall: interior -> gateway approach -> arch -> deck',
}));
const DOCUMENTED_RESITES = {
  'lookout-399': { to: [17, -4], reason: 'grove plant -> planted flank west of the arch (deck)' },
  'lookout-400': { to: [14, -4], reason: 'grove plant -> planted flank west of the arch (deck)' },
  'lookout-401': { to: [31, -4], reason: 'grove plant -> planted flank east of the arch (deck)' },
  'lookout-402': { to: [21, 2], reason: 'grove plant -> west flank of the gateway approach' },
  'lookout-403': { to: [27, 2], reason: 'grove plant -> east flank of the gateway approach' },
};
/** Where the arch may stand (palace coordinates): the grove columns, from the deck down to row 0. */
const GATEWAY_REGION = { c0: 21, c1: 28, r0: -5, r1: 0 };
const ARRAYS = ['tiles', 'tileColors', 'carpetTiles', 'areaTiles'];
const mismatched = [];
for (let r = 0; r < P.rows; r++)
  for (let c = 0; c < pc; c++) {
    const pi = r * pc + c;
    const wi = (r + drow) * W.cols + (c + dcol);
    const diff = ARRAYS.filter((k) => !same(P[k]?.[pi], W[k]?.[wi]));
    if (diff.length > 0) mismatched.push({ c, r, diff });
  }
const cellKey = ([c, r]) => `${c},${r}`;
const documentedKeys = new Set(DOCUMENTED.map((a) => cellKey(a.palace)));
const observedKeys = new Set(mismatched.map((m) => cellKey([m.c, m.r])));
const srcI = DOOR_SOURCE[1] * pc + DOOR_SOURCE[0];
const exceptions = DOCUMENTED.map((a) => {
  const [c, r] = a.palace;
  const pi = r * pc + c;
  const wi = (r + drow) * W.cols + (c + dcol);
  const asDoor =
    W.tiles[wi] === P.tiles[srcI] &&
    same(W.tileColors?.[wi], P.tileColors?.[srcI]) &&
    same(W.carpetTiles?.[wi], P.carpetTiles?.[pi]) &&
    same(W.areaTiles?.[wi], P.areaTiles?.[pi]);
  return {
    palace: [c, r],
    world: [c + dcol, r + drow],
    old: { tile: P.tiles[pi], tileColor: P.tileColors?.[pi] ?? null },
    new: { tile: W.tiles[wi], tileColor: W.tileColors?.[wi] ?? null },
    copiedFromPalaceDoorway: DOOR_SOURCE,
    matchesPalaceDoorway: asDoor,
    wasWall: P.tiles[pi] === WALL,
    reason: a.reason,
  };
});
const exactSet =
  observedKeys.size === documentedKeys.size &&
  [...observedKeys].every((k) => documentedKeys.has(k));
check(
  `palace block reproduced exactly EXCEPT ${DOCUMENTED.length} documented exceptions (observed set == documented set)`,
  exactSet && exceptions.every((e) => e.matchesPalaceDoorway && e.wasWall),
  {
    documented: [...documentedKeys],
    observed: mismatched.map((m) => ({ palace: [m.c, m.r], arrays: m.diff })),
    exceptions,
  },
);
const wholePalaceByteEquality = {
  claimed: false,
  why: 'The north gateway change set: human design steering for the palace north grove (cols 21-28, rows 0-8), whose clear approach is also the connection the approved integrated-deck and usable-routes acceptance criteria need (the canonical palace has no exterior opening). The 4 tiles and 5 re-sited plants enumerated in the checks differ from the canonical export; nothing else in the 64x64 block or its furniture does.',
  restorePath:
    'Importing the canonical export (backup/verified-live-export.json) returns the original sealed palace exactly.',
};

const furnMismatch = [];
const resiteReport = [];
for (const f of P.furniture) {
  const g = byUid.get(f.uid);
  const moved = DOCUMENTED_RESITES[f.uid];
  const expect = moved
    ? { ...f, col: moved.to[0] + dcol, row: moved.to[1] + drow }
    : { ...f, col: f.col + dcol, row: f.row + drow };
  if (moved)
    resiteReport.push({
      uid: f.uid,
      type: f.type,
      color: f.color ?? null,
      palaceFrom: [f.col, f.row],
      palaceTo: moved.to,
      worldTo: [moved.to[0] + dcol, moved.to[1] + drow],
      reason: moved.reason,
    });
  // same keys, same uid / type / color, position = canonical + (dcol, drow)
  const keys = Object.keys(expect).sort();
  if (!g || !same(Object.keys(g).sort(), keys) || !keys.every((k) => same(g[k], expect[k])))
    furnMismatch.push(f.uid);
}
check(
  `all ${P.furniture.length} palace furniture records: identity intact; translated exactly EXCEPT ${Object.keys(DOCUMENTED_RESITES).length} documented re-sites`,
  furnMismatch.length === 0 && resiteReport.length === Object.keys(DOCUMENTED_RESITES).length,
  {
    palaceRecords: P.furniture.length,
    mismatched: furnMismatch.slice(0, 10),
    resites: resiteReport,
  },
);
const isPc = (t) => t.startsWith('PC_');
const pcs = P.furniture.filter((f) => isPc(f.type));
const pcTypes = {};
for (const f of pcs) pcTypes[f.type] = (pcTypes[f.type] ?? 0) + 1;
const pcsPresent = pcs.filter((f) => byUid.get(f.uid)?.type === f.type).length;
const worldPcs = W.furniture.filter((f) => isPc(f.type)).length;
check(
  'computers preserved by uid (and none added)',
  pcsPresent === pcs.length && worldPcs === pcs.length,
  {
    palace: pcs.length,
    presentByUid: pcsPresent,
    world: worldPcs,
    types: pcTypes,
  },
);
check('pets preserved', same(P.pets, W.pets), { pets: (W.pets ?? []).length });

// ── measured P and F ──
const inBlock = (c, r) => c >= dcol && c < dcol + pc && r >= drow && r < drow + P.rows;
let landP = 0;
let landF = 0;
let edgeVoid = 0;
for (let r = 0; r < W.rows; r++)
  for (let c = 0; c < W.cols; c++) {
    const t = W.tiles[r * W.cols + c];
    if (inBlock(c, r)) {
      if (t !== VOID) landP++;
    } else if (t !== VOID) landF++;
    else edgeVoid++;
  }
check('forest F >= 3 x palace P (measured)', landF >= 3 * landP, {
  P: landP,
  F: landF,
  ratio: +(landF / landP).toFixed(3),
  voidOutsidePalace: edgeVoid,
});

// ── added furniture rules ──
const palaceUids = new Set(P.furniture.map((f) => f.uid));
const added = W.furniture.filter((f) => !palaceUids.has(f.uid));
const chairs = added.filter((f) => entry(f.type)?.category === 'chairs').map((f) => f.uid);
const unknown = added.filter((f) => !entry(f.type)).map((f) => f.type);
const overlap = [];
const strip = [];
const inGatewayRegion = (c, r) =>
  c - dcol >= GATEWAY_REGION.c0 &&
  c - dcol <= GATEWAY_REGION.c1 &&
  r - drow >= GATEWAY_REGION.r0 &&
  r - drow <= GATEWAY_REGION.r1;
const strayGateway = [];
for (const f of added) {
  const e = entry(f.type) ?? { w: 1, h: 1 };
  const isGate = f.type.startsWith('GATEWAY_PILLAR') || f.type === 'GATEWAY_CROWN';
  for (let dr = 0; dr < e.h; dr++)
    for (let dc = 0; dc < e.w; dc++) {
      const c = f.col + dc;
      const r = f.row + dr;
      if (isGate) {
        if (!inGatewayRegion(c, r)) strayGateway.push(f.uid);
      } else if (inBlock(c, r)) overlap.push(f.uid);
      // the wall-hanging strip: the row above the palace's north WALL cells (row -1 items)
      if (r === drow - 1 && c >= dcol && c < dcol + pc && P.tiles[c - dcol] === WALL)
        strip.push(f.uid);
    }
}
check(
  'the arch stands only in the gateway region (palace cols 21-28, rows -5..0)',
  strayGateway.length === 0,
  [...new Set(strayGateway)],
);
check(
  'added furniture: no chair category (no seats outside the palace)',
  chairs.length === 0,
  chairs,
);
check('added furniture: every type is in the bundled catalog', unknown.length === 0, unknown);
check(
  'added furniture: none overlaps the palace block (except the arch, in its region)',
  overlap.length === 0,
  [...new Set(overlap)],
);
check('wall-hanging strip above the palace north WALL cells is clear', strip.length === 0, {
  row: drow - 1,
  palaceWallItemsAtRowMinus1: P.furniture.filter((f) => f.row === -1).length,
  addedItemsInStrip: [...new Set(strip)],
});
const furnitureTotal = W.furniture.length;
check('furniture within DI_LAYOUT_MAX_FURNITURE (4,096)', furnitureTotal <= 4096, {
  total: furnitureTotal,
  palace: P.furniture.length,
  added: added.length,
});

// ── footprint-aware reachability ──
/** BFS over a layout (findPath semantics) from `origin` [col, row]. Returns the reach test,
 *  the seat tiles, the component size and the timing. */
function reachability(L, origin) {
  const blockedSet = new Set();
  const seats = [];
  for (const f of L.furniture) {
    const e = entry(f.type);
    if (!e) continue;
    for (let dr = 0; dr < e.h; dr++)
      for (let dc = 0; dc < e.w; dc++) {
        if (e.category === 'chairs') seats.push({ uid: f.uid, c: f.col + dc, r: f.row + dr });
        if (dr >= e.backgroundTiles) blockedSet.add(`${f.col + dc},${f.row + dr}`);
      }
  }
  const walkable = (c, r) => {
    if (c < 0 || r < 0 || c >= L.cols || r >= L.rows) return false;
    const t = L.tiles[r * L.cols + c];
    return t !== WALL && t !== VOID && !blockedSet.has(`${c},${r}`);
  };
  if (!walkable(origin[0], origin[1])) throw new Error(`BFS origin ${origin} is not walkable`);
  const visited = new Uint8Array(L.cols * L.rows);
  const parent = new Int32Array(L.cols * L.rows).fill(-1);
  const t0 = process.hrtime.bigint();
  const q = [origin];
  visited[origin[1] * L.cols + origin[0]] = 1;
  for (let qi = 0; qi < q.length; qi++) {
    const [c, r] = q[qi];
    for (const [dc, dr] of [
      [1, 0],
      [-1, 0],
      [0, 1],
      [0, -1],
    ]) {
      const nc = c + dc;
      const nr = r + dr;
      if (walkable(nc, nr) && !visited[nr * L.cols + nc]) {
        visited[nr * L.cols + nc] = 1;
        parent[nr * L.cols + nc] = r * L.cols + c;
        q.push([nc, nr]);
      }
    }
  }
  /** The BFS (shortest, footprint-aware) path from the origin to (c, r), or null. */
  const pathTo = (c, r) => {
    if (!visited[r * L.cols + c]) return null;
    const out = [];
    for (let i = r * L.cols + c; i !== -1; i = parent[i])
      out.push([i % L.cols, Math.floor(i / L.cols)]);
    return out.reverse();
  };
  const ms = Number(process.hrtime.bigint() - t0) / 1e6;
  const hit = (c, r) =>
    c >= 0 && r >= 0 && c < L.cols && r < L.rows && visited[r * L.cols + c] === 1;
  // a seat (blocked chair tile) is a valid destination when a neighbour is reached
  const target = (c, r) =>
    hit(c, r) || hit(c + 1, r) || hit(c - 1, r) || hit(c, r + 1) || hit(c, r - 1);
  let walkableCells = 0;
  for (let r = 0; r < L.rows; r++)
    for (let c = 0; c < L.cols; c++) if (walkable(c, r)) walkableCells++;
  return {
    walk: walkable,
    reached: hit,
    target,
    seats,
    component: q.length,
    walkableCells,
    ms,
    pathTo,
  };
}
// The palace's main corridor (palace col 30, row 13) — inside the building.
const INTERIOR = [30, 13];
const base = reachability(P, INTERIOR);
const world = reachability(W, [INTERIOR[0] + dcol, INTERIOR[1] + drow]);
const baseSeats = base.seats.filter((s) => base.target(s.c, s.r));
const lost = baseSeats
  .filter((s) => !world.target(s.c + dcol, s.r + drow))
  .map((s) => `${s.uid}@${s.c},${s.r}`);
const baseUnreachable = base.seats
  .filter((s) => !base.target(s.c, s.r))
  .map((s) => `${s.uid}@${s.c},${s.r}`);
check('every palace seat reachable in the canonical palace is still reachable', lost.length === 0, {
  seatTiles: base.seats.length,
  reachableInCanonical: baseSeats.length,
  lostInWorld: lost,
  unreachableAlreadyInCanonical: baseUnreachable.length,
});
// deck + forest destinations: the nearest walkable cell to each named point
const nearestWalkable = (c0, r0) => {
  for (let d = 0; d < 8; d++)
    for (let dr = -d; dr <= d; dr++)
      for (let dc = -d; dc <= d; dc++) if (walk(c0 + dc, r0 + dr)) return [c0 + dc, r0 + dr];
  return null;
};
const walk = world.walk;
const deckItems = W.furniture.filter((f) => f.type.startsWith('DECK_'));
const deckBox = deckItems.reduce(
  (b, f) => ({
    c0: Math.min(b.c0, f.col),
    r0: Math.min(b.r0, f.row),
    c1: Math.max(b.c1, f.col),
    r1: Math.max(b.r1, f.row),
  }),
  { c0: Infinity, r0: Infinity, c1: -Infinity, r1: -Infinity },
);
const destinations = {
  'deck centre': [
    Math.round((deckBox.c0 + deckBox.c1) / 2),
    Math.round((deckBox.r0 + deckBox.r1) / 2),
  ],
  'deck north-west telescope': [deckBox.c0 + 4, deckBox.r0 + 3],
  'deck stair head': [Math.round((deckBox.c0 + deckBox.c1) / 2), deckBox.r0 - 1],
  'north clearing': [80, 7],
  'pond meadow (shore)': [33, 21],
  'north-east clearing': [143, 14],
  'west clearing': [22, 63],
  'east clearing': [153, 70],
  'south-west meadow': [20, 100],
  'south-east meadow': [156, 101],
};
// Outdoors: from the deck centre (the deck + trails component).
const outdoor = reachability(W, nearestWalkable(...destinations['deck centre']));
const destReport = {};
for (const [name, [c, r]] of Object.entries(destinations)) {
  const w = nearestWalkable(c, r);
  destReport[name] = w
    ? { at: w, reachable: outdoor.reached(w[0], w[1]) }
    : { at: null, reachable: false };
}
check(
  'deck and forest destinations reachable from the deck (trails connect every clearing)',
  Object.values(destReport).every((d) => d.reachable),
  destReport,
);
const deckCentre = nearestWalkable(...destinations['deck centre']);
const lookoutPocket = nearestWalkable(dcol + 24, drow + 1);
const interiorToDeck = {};
for (const [name, [c, r]] of Object.entries(destinations)) {
  const w = nearestWalkable(c, r);
  interiorToDeck[name] = w
    ? { at: w, reachable: world.reached(w[0], w[1]) }
    : { at: null, reachable: false };
}
check(
  'palace INTERIOR (main corridor) -> deck and every clearing (footprint-aware)',
  Object.values(interiorToDeck).every((d) => d.reachable),
  interiorToDeck,
);
// The interior -> deck path TRAVERSES the arch opening (not around it), the crown is walk-under.
const route = world.pathTo(deckCentre[0], deckCentre[1]) ?? [];
const toPalace = ([c, r]) => [c - dcol, r - drow];
const archOpening = route.filter(([c, r]) => {
  const [pc2, pr2] = toPalace([c, r]);
  return pr2 === 0 && pc2 >= 23 && pc2 <= 26;
});
const thresholdCrossed = route.filter(([c, r]) => {
  const [pc2, pr2] = toPalace([c, r]);
  return pr2 === 5 && pc2 >= 23 && pc2 <= 26;
});
const crownCells = [];
for (let pr2 = -5; pr2 <= -2; pr2++)
  for (let pc2 = 23; pc2 <= 26; pc2++) crownCells.push([pc2 + dcol, pr2 + drow]);
const crownBlocked = crownCells.filter(([c, r]) => !world.walk(c, r));
check(
  'the interior -> deck path traverses the gateway threshold AND the arch opening',
  route.length > 0 && archOpening.length > 0 && thresholdCrossed.length > 0,
  {
    pathLength: route.length,
    archOpeningTiles: archOpening.map(toPalace),
    thresholdTiles: thresholdCrossed.map(toPalace),
    pathPalaceCoordsNearGateway: route.map(toPalace).filter(([, r]) => r >= -6 && r <= 12),
  },
);
check(
  'the arch crown never invisibly blocks: every cell under it is walkable',
  crownBlocked.length === 0,
  {
    crownCells: crownCells.length,
    blocked: crownBlocked.map(toPalace),
  },
);
const separation = {
  palaceInteriorReachesDeck: world.reached(deckCentre[0], deckCentre[1]),
  deckReachesLookoutPocket: outdoor.reached(lookoutPocket[0], lookoutPocket[1]),
  canonicalPocketComponentCells: reachability(P, [24, 1]).component,
  canonicalInteriorComponentCells: base.component,
  worldInteriorComponentCells: world.component,
  worldDeckComponentCells: outdoor.component,
  worldWalkableCells: world.walkableCells,
  note: 'The canonical palace has NO exterior opening: its north lookout pocket (palace rows 0-4, cols 21-28) is sealed from the interior by the row-5 wall (cols 19-29) in the canonical export itself. The north gateway 4-tile threshold (palace (23..26,5)) and arch join them, so the interior component grows from the canonical figure to take in the gateway approach, the deck and the trail network.',
};
check(
  'palace interior and deck are ONE walkable component (via the gateway)',
  separation.palaceInteriorReachesDeck && world.component === outdoor.component,
  {
    canonicalInteriorComponentCells: base.component,
    worldInteriorComponentCells: world.component,
    worldDeckComponentCells: outdoor.component,
  },
);
// GATE INTEGRITY: every boolean diagnostic reported outside `checks` must agree with the gate.
// A diagnostic that contradicts a passing gate fails the run — no reported field can say "no"
// while ok says "yes".
const diagnostics = {
  'separation.palaceInteriorReachesDeck': separation.palaceInteriorReachesDeck === true,
  'separation.deckReachesLookoutPocket': separation.deckReachesLookoutPocket === true,
  'interior and deck components are the same size': world.component === outdoor.component,
  're-site list used for the translation equals the documented re-sites':
    [...RESITED_UIDS].sort().join() === Object.keys(DOCUMENTED_RESITES).sort().join(),
  'wholePalaceByteEquality.claimed is false while exceptions exist': !(
    wholePalaceByteEquality.claimed && exceptions.length > 0
  ),
};
check(
  'no reported diagnostic contradicts the gate',
  Object.values(diagnostics).every(Boolean),
  diagnostics,
);

const report = {
  palace: { file: palacePath, sha256: sha(palacePath) },
  world: {
    file: worldPath,
    sha256: sha(worldPath),
    cols: W.cols,
    rows: W.rows,
    bytesPretty: fs.statSync(worldPath).size,
    bytesCompactSaveFrame: Buffer.byteLength(JSON.stringify({ type: 'saveLayout', layout: W })),
  },
  translation: { dcol, drow },
  bfs: {
    worldFromPalaceInterior: {
      componentCells: world.component,
      walkableCells: world.walkableCells,
      ms: +world.ms.toFixed(2),
    },
    worldFromDeck: { componentCells: outdoor.component, ms: +outdoor.ms.toFixed(2) },
    canonicalPalaceFromInterior: {
      componentCells: base.component,
      walkableCells: base.walkableCells,
      ms: +base.ms.toFixed(2),
    },
  },
  separation,
  wholePalaceByteEquality,
  checks,
  ok: checks.every((c) => c.ok),
};
console.log(JSON.stringify(report, null, 2));
process.exit(report.ok ? 0 : 1);
